"""Regressions found in the completion audit: report drift and API contradictions."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.pii_smoke.evaluate import aggregate, latency_summary
from benchmarks.pii_smoke.report import load_verified, render
from tools.validate_api_examples import ROOT, validate_pair


class ReportChecks(unittest.TestCase):
    def fixture(self):
        row = {
            "id": "occupation",
            "text": "инженер",
            "expected": [{"type": "OCCUPATION", "start": 0, "end": 7}],
            "latency_ms": [1.0, 2.0, 3.0],
        }
        row["predicted"] = copy.deepcopy(row["expected"])
        summary = {
            "metadata": {
                "started_at_utc": "2026-11-02T00:00:00+00:00",
                "dataset_version": "test-only",
                "commit": "test-only",
                "worktree_dirty": False,
                "dataset_sha256": "test-only",
                "config_sha256": "test-only",
                "python": "3.13",
                "platform": "test-only",
                "processor": "test-only",
                "logical_cpus": 1,
                "ram_gib": 1,
                "available_ram_gib_at_start": 1,
                "device": "cpu",
                "packages": {},
                "config": {"repeats": 3},
            },
            "case_count": 1,
            "modes": {
                "regex": {
                    "exact": aggregate([row]),
                    "overlap": aggregate([row], True),
                    "latency_ms": latency_summary([row]),
                }
            },
        }
        return summary, [row]

    def write_fixture(self, directory, summary, rows):
        (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (directory / "regex.json").write_text(json.dumps(rows), encoding="utf-8")

    def test_regex_only_and_perfect_predictions_have_no_stale_errors(self):
        summary, rows = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            self.write_fixture(Path(tmp), summary, rows)
            verified, by_mode = load_verified(Path(tmp))
            output = render(verified, by_mode)
        self.assertIn("test-only", output)
        self.assertIn(
            "Расхождений по точному типу и границам в этом прогоне нет", output
        )
        self.assertNotIn("не извлечены", output)
        self.assertNotIn("## По типам: combined", output)

    def test_stale_timing_is_rejected(self):
        summary, rows = self.fixture()
        rows[0]["latency_ms"][0] = 100.0
        with tempfile.TemporaryDirectory() as tmp:
            self.write_fixture(Path(tmp), summary, rows)
            with self.assertRaises(ValueError):
                load_verified(Path(tmp))

    def test_stale_quality_is_rejected(self):
        summary, rows = self.fixture()
        rows[0]["predicted"] = []
        with tempfile.TemporaryDirectory() as tmp:
            self.write_fixture(Path(tmp), summary, rows)
            with self.assertRaises(ValueError):
                load_verified(Path(tmp))


class ContractChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pairs = {
            pair["name"]: pair
            for pair in json.loads(
                (ROOT / "docs/api/session-api.examples.json").read_text(
                    encoding="utf-8"
                )
            )["pairs"]
        }

    def test_all_document_pairs(self):
        for pair in self.pairs.values():
            with self.subTest(pair=pair["name"]):
                validate_pair(pair)

    def test_four_audit_contradictions_are_rejected(self):
        for issue in (
            "incomplete_no_alert",
            "history_no_alert",
            "missing_person",
            "false_signals",
        ):
            with self.subTest(issue=issue):
                pair = copy.deepcopy(self.pairs["single_unicode"])
                result = pair["response"]["results"][0]
                if issue == "incomplete_no_alert":
                    result["session_verdict"].update(
                        coverage_complete=False, reason=None
                    )
                elif issue == "history_no_alert":
                    result["session_verdict"]["first_violation_message_id"] = 1
                elif issue == "missing_person":
                    result["entities"][0]["person_id"] = "missing"
                else:
                    result["message_verdict"]["has_signals"] = False
                with self.assertRaises(ValueError):
                    validate_pair(pair)

    def test_correction_cannot_clear_historical_alert(self):
        pair = copy.deepcopy(self.pairs["correction_keeps_historical_alert"])
        pair["response"]["results"][0]["session_verdict"].update(
            decision="no_alert", first_violation_message_id=None
        )
        with self.assertRaises(ValueError):
            validate_pair(pair)


if __name__ == "__main__":
    unittest.main()
