"""Синтетические проверки страниц VK; реальные профили и сеть не используются."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from collect_vk_group_members import page_offsets, supported_page, pair_summary, collect, MemberClient, failure_summary
from collect_vk_name_pairs import ApiFailure


class VkGroupMemberTests(unittest.TestCase):
    def test_timeout_diagnostic_is_json_serializable_without_raw_reason(self):
        import json
        from urllib.error import URLError
        for error in (URLError(TimeoutError("synthetic-sensitive-detail")),
                      URLError("synthetic-sensitive-detail"), TimeoutError("synthetic-sensitive-detail"), ApiFailure(9)):
            with self.subTest(error_type=type(error).__name__):
                result = failure_summary(error)
                serialized = json.dumps(result)
                self.assertNotIn("synthetic-sensitive-detail", serialized)
                self.assertTrue(result["cached_pages_preserved"])
        self.assertEqual(failure_summary(URLError(TimeoutError()))["reason"], "network_timeout")

    def test_rate_error_backoff_retries_same_page(self):
        parameters = {"group_id": "synthetic_group", "offset": 1000}
        with patch("collect_vk_group_members.api", side_effect=[ApiFailure(6), ApiFailure(6), {"count": 1}]) as request, \
             patch("collect_vk_group_members.time.sleep") as sleep, \
             patch("builtins.print"):
            client = MemberClient("synthetic-token", "5.199", max_retries=2)
            self.assertEqual(client.request(parameters), {"count": 1})
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 1, 2])
        self.assertTrue(all(c.args[1] == parameters for c in request.call_args_list))

    def test_flood_backoff_stops_at_run_budget_even_after_success(self):
        with patch("collect_vk_group_members.api", side_effect=[ApiFailure(9), {}, ApiFailure(9), ApiFailure(9)]) as request, \
             patch("collect_vk_group_members.time.sleep") as sleep, \
             patch("builtins.print"):
            client = MemberClient("synthetic-token", "5.199", delay_seconds=3, max_retries=2)
            self.assertEqual(client.request({"offset": 0}), {})
            with self.assertRaises(ApiFailure):
                client.request({"offset": 1000})
        self.assertEqual(request.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [3, 60, 3, 60])
        self.assertEqual(client.retries_used, 2)

    def test_other_errors_are_not_retried(self):
        for code in (5, 14, 15, 28, 29):
            with self.subTest(code=code), \
                 patch("collect_vk_group_members.api", side_effect=ApiFailure(code)) as request, \
                 patch("collect_vk_group_members.time.sleep"):
                with self.assertRaises(ApiFailure):
                    MemberClient("synthetic-token", "5.199").request({})
                self.assertEqual(request.call_count, 1)

    def test_retry_settings_reject_unbounded_values(self):
        for delay in (0, -1, float('nan'), float('inf'), 3601):
            with self.assertRaises(ValueError):
                MemberClient("synthetic-token", "5.199", delay_seconds=delay)
        for retries in (-1, 11):
            with self.assertRaises(ValueError):
                MemberClient("synthetic-token", "5.199", max_retries=retries)

    def test_offsets_cover_range_and_obey_small_group_boundaries(self):
        self.assertEqual(page_offsets(0, 10000), [])
        self.assertEqual(page_offsets(200, 10000), [0])
        self.assertEqual(page_offsets(2500, 10000), [0, 1000, 2000])
        offsets = page_offsets(1000000, 10000)
        self.assertEqual(len(offsets), 10)
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets[-1], 999000)
        million = page_offsets(14148520, 1000000)
        self.assertEqual(len(million), 1000)
        self.assertEqual(million[0], 0)
        self.assertEqual(million[-1], 14147520)
        self.assertTrue(all(b - a >= 1000 for a, b in zip(million, million[1:])))

    def test_page_keeps_only_allowed_fields(self):
        page = supported_page({"count": 10, "items": [
            {"id": 1, "first_name": "Тестимир", "last_name": "Вымышлов", "sex": 2,
             "country": {"id": 1, "title": "synthetic-country"}, "contacts": "synthetic-not-for-cache"},
            {"id": 2, "first_name": "Тестимир", "last_name": "Вымышлов", "is_closed": True}]})
        self.assertEqual(page["returned_items"], 2)
        self.assertEqual(len(page["records"]), 1)
        self.assertEqual(set(page["records"][0]), {"account_id", "surname", "name", "sex", "country_id"})
        self.assertEqual(page["language"], "ru")
        self.assertEqual(len(page["source_items"]), 1)
        self.assertNotIn("contacts", page["source_items"][0])

    def test_expansion_preserves_baseline_and_reuses_grid(self):
        import json
        import tempfile
        total = 14148520
        self.assertTrue(set(page_offsets(total, 10000)).issubset(page_offsets(total, 100000)))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "vk-groups" / "synthetic_group"
            directory.mkdir(parents=True)
            baseline = {"limit": 10000, "token_type": "service", "api_version": "5.199",
                        "total_at_start": total, "offsets": page_offsets(total, 10000),
                        "language": "ru", "parser_version": 2}
            (directory / "plan.json").write_text(json.dumps(baseline), encoding="utf-8")
            def save(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            page = {"total_at_fetch": total, "language": "ru", "parser_version": 2,
                    "records": [], "quality": {}}
            for offset in page_offsets(total, 100000):
                save(directory / f"page-{offset}.json", page)
            with patch("collect_vk_group_members.ROOT", root), \
                 patch("collect_vk_group_members.settings", return_value=("synthetic-token", "5.199")), \
                 patch("collect_vk_group_members.write_private", side_effect=save), \
                 patch("collect_vk_group_members.api") as request:
                result = collect("synthetic_group", 100000, "service", True)
                old_result = collect("synthetic_group", 10000, "service", False)
            request.assert_not_called()
            self.assertEqual(result["cached_pages"], 100)
            self.assertEqual(old_result["cached_pages"], 10)
            preserved = json.loads((directory / "snapshots/limit-10000/plan.json").read_text())
            self.assertEqual(preserved, baseline)
            self.assertEqual(json.loads((directory / "plan.json").read_text())["limit"], 100000)

    def test_id_only_response_is_not_misinterpreted_as_full_profiles(self):
        with self.assertRaises(ValueError):
            supported_page({"count": 2, "items": [1, 2]})

    def test_offline_analysis_uses_cached_pages_without_api(self):
        page = {"total_at_fetch": 1, "records": [{"account_id": 1, "surname": "synthetic-f", "name": "synthetic-i"}], "quality": {}, "language": "ru", "parser_version": 2}
        import json
        plan = {"limit": 10000, "token_type": "service", "api_version": "5.199", "total_at_start": 1, "offsets": [0]}
        def read(path, *args, **kwargs):
            return json.dumps(plan if path.name == "plan.json" else page)
        with patch("collect_vk_group_members.settings", return_value=("", "5.199")), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", read), \
             patch.object(Path, "read_bytes", lambda path: read(path).encode()), \
             patch("collect_vk_group_members.write_private"), patch("collect_vk_group_members.api") as request:
            result = collect("synthetic_group", 10000, "service", False)
        request.assert_not_called()
        self.assertEqual(result["pair_statistics"]["accounts"], 1)
        self.assertTrue(result["complete_plan"])

    def test_old_zero_page_is_replaced_and_new_requests_require_russian(self):
        import json
        plan = {"limit": 10000, "token_type": "service", "api_version": "5.199", "total_at_start": 1, "offsets": [0]}
        old = {"total_at_fetch": 1, "records": [], "quality": {"unsupported_written_name": 1}}
        response = {"count": 1, "items": [{"id": 1, "first_name": "Тестимир", "last_name": "Вымышлов"}]}
        def read(path, *args, **kwargs):
            return json.dumps(plan if path.name == "plan.json" else old)
        writes = {}
        def save(path, value):
            writes[str(path)] = value
        def read_bytes(path):
            return json.dumps(writes.get(str(path), old)).encode()
        with patch("collect_vk_group_members.settings", return_value=("synthetic-token", "5.199")), \
             patch.object(Path, "exists", return_value=True), patch.object(Path, "read_text", read), \
             patch.object(Path, "read_bytes", read_bytes), patch("collect_vk_group_members.time.sleep"), \
             patch("collect_vk_group_members.write_private", side_effect=save), \
             patch("collect_vk_group_members.api", return_value=response) as request:
            result = collect("synthetic_group", 10000, "service", True)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1]["lang"], "ru")
        self.assertEqual(result["pair_statistics"]["accounts"], 1)

    def test_independent_pairs_match_permutation_background(self):
        rows = [{"surname": f, "name": i} for f in ("f1", "f2") for i in ("i1", "i2")]
        result = pair_summary(rows)
        self.assertAlmostEqual(result["mutual_information_bits"], 0)
        self.assertNotIn("surname", str(result))


if __name__ == "__main__":
    unittest.main()
