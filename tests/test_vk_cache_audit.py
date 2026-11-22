"""Проверки объединения планов без реальных имён или сетевых запросов."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from audit_vk_cache import audit


class VkCacheAuditTests(unittest.TestCase):
    def test_outside_pages_do_not_disappear_and_overlapping_ids_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "plan.json").write_text(json.dumps({"limit": 1000, "offsets": [0]}))
            for offset, identifiers in ((0, [1, 2]), (1000, [2, 3])):
                page = {"language": "ru", "parser_version": 2, "returned_items": 2,
                        "records": [{"account_id": uid} for uid in identifiers]}
                (directory / f"page-{offset}.json").write_text(json.dumps(page))
            (directory / "page-2000.json").write_text("{")
            (directory / "page-3000.json.tmp").write_text("")
            result = audit(directory)
        self.assertEqual(result["counts"]["outside_current_plan_pages"], 1)
        self.assertEqual(result["all_cached_unique_supported_accounts"], 3)
        self.assertEqual(result["extra_unique_supported_accounts"], 1)
        self.assertEqual(len(result["invalid_files"]), 1)
        self.assertEqual(len(result["temporary_files"]), 1)
        self.assertFalse(result["network_used"])


if __name__ == "__main__":
    unittest.main()
