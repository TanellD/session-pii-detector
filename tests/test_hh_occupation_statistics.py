"""Синтетические ответы API: пересечения ролей, пропуски, частичный сбор и безопасный лог."""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from collect_hh_occupation_statistics import ApiFailure, Client, SampleAccumulator, cluster_statistics, collect, print_report


class HhStatisticsTests(unittest.TestCase):
    def test_clusters_remain_marginal_not_normalized(self):
        result = cluster_statistics({"found": 10, "clusters": [{"id": "professional_role", "items": [
            {"value": "a", "name": "Роль A", "count": 8},
            {"value": "b", "name": "Роль B", "count": 7}]}]})
        self.assertEqual(result["sum_role_counts"], 15)
        self.assertAlmostEqual(sum(r["share_of_found"] for r in result["roles"]), 1.5)
        self.assertIsNone(result["other"])

    def test_missing_cluster_is_not_zero_other(self):
        result = cluster_statistics({"found": 10, "clusters": None})
        self.assertFalse(result["professional_role_cluster_present"])
        self.assertIsNone(result["other"])

    def test_fractional_roles_duplicates_and_other(self):
        accumulator = SampleAccumulator()
        accumulator.add([
            {"id": "synthetic-a", "professional_roles": [{"id": "a"}, {"id": "b"}, {"id": "b"}]},
            {"id": "synthetic-b", "professional_roles": []},
            {"id": "synthetic-a", "professional_roles": [{"id": "a"}]},
            {"professional_roles": [{"id": "a"}]}])
        result = accumulator.report()
        self.assertEqual(result["unique_vacancies"], 2)
        self.assertEqual(result["duplicate_ids_skipped"], 1)
        self.assertEqual(result["records_without_id_skipped"], 1)
        self.assertEqual(result["multi_role_vacancies"], 1)
        weights = {r["id"]: r["weight"] for r in result["roles"]}
        self.assertEqual(weights, {"a": 0.5, "b": 0.5, "other": 1})
        self.assertEqual(sum(r["share"] for r in result["roles"]), 1)

    def test_partial_sample_retains_aggregates_and_reports_failure(self):
        class FakeClient:
            calls = 0

            def get(self, path, params):
                self.calls += 1
                if self.calls == 1:
                    return {"found": 500, "clusters": []}
                if self.calls == 2:
                    return {"items": [{"id": "synthetic-a", "professional_roles": []}]}
                raise ApiFailure("access_denied_or_captcha", 403)

        report = collect(FakeClient(), sample_pages=2, pause=0)
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["sample"]["truncated"])
        self.assertEqual(report["sample"]["unique_vacancies"], 1)
        self.assertEqual(report["error"]["stage"], "sample")

    def test_http_denial_does_not_leak_response_or_token(self):
        error = HTTPError("https://api.hh.ru/vacancies", 403, "", {},
                          StringIO("synthetic-secret-response"))
        with patch("collect_hh_occupation_statistics.urlopen", side_effect=error) as opener:
            report = collect(Client({"HH_API_TOKEN": "synthetic-secret-token"}))
        self.assertEqual(opener.call_count, 1)
        output = StringIO()
        with redirect_stdout(output):
            print_report(report)
        self.assertIn("access_denied_or_captcha", output.getvalue())
        self.assertNotIn("synthetic-secret", output.getvalue())

    def test_invalid_counter_is_not_accepted(self):
        with self.assertRaises(ApiFailure):
            cluster_statistics({"found": 2, "clusters": [{"id": "professional_role", "items": [
                {"value": "a", "count": 3}]}]})


if __name__ == "__main__":
    unittest.main()
