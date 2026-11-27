import contextlib
import copy
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_compatible_annotation_pilot import endpoint, make_body, response_document, source_sessions, main, NoRedirect, materialize_quotes
from audit_annotation_consistency import document
from build_llm_annotation_pilot import CASES


class CompatiblePilotTests(unittest.TestCase):
    def setUp(self):
        self.doc = document(CASES[0])
        self.source = {"synthetic_only": True, "sessions": [{
            "session_id": self.doc["session_id"], "messages": self.doc["messages"]}]}
        (ROOT / "data/tmp").mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ROOT / "data/tmp")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.input = self.path / "input.json"
        self.input.write_text(json.dumps(self.source), encoding="utf-8")

    def args(self, output="run"):
        return ["--model", "Gemma-4-31B-IT", "--input", str(self.input),
                "--output", str(self.path / output), "--limit", "1"]

    def response(self, doc=None):
        return {"id": "synthetic-response", "model": "Gemma-4-31B-IT", "usage": {"total_tokens": 1},
                "choices": [{"finish_reason": "stop", "message": {
                    "content": json.dumps(self.doc if doc is None else doc)}}]}

    def test_chat_protocol_and_explicit_formats(self):
        for mode in ("json_schema", "json_object", "prompt"):
            body = make_body("Qwen3.8-27B", "instructions", self.source["sessions"][0], mode, 6000, 0)
            self.assertEqual(body["messages"][0]["role"], "system")
            self.assertEqual(json.loads(body["messages"][1]["content"]), self.source["sessions"][0])
            self.assertNotIn("input", body)
            self.assertNotIn("store", body)
            self.assertNotIn("instructions", body)
            self.assertEqual("response_format" in body, mode != "prompt")

    def test_dry_run_never_calls_api_or_creates_output(self):
        with patch("run_compatible_annotation_pilot.send", side_effect=AssertionError("Network")), \
                patch("run_compatible_annotation_pilot.local_key", return_value=""), \
                contextlib.redirect_stdout(StringIO()) as out:
            self.assertEqual(main(self.args() + ["--dry-run"]), 0)
        self.assertEqual(json.loads(out.getvalue())["api_requests"], 0)
        self.assertFalse((self.path / "run").exists())

    def test_missing_key_blocks_before_request(self):
        with patch("run_compatible_annotation_pilot.local_key", return_value=""), \
                patch("run_compatible_annotation_pilot.send", side_effect=AssertionError("Network")), \
                contextlib.redirect_stdout(StringIO()):
            self.assertEqual(main(self.args()), 2)
        self.assertFalse((self.path / "run").exists())

    def test_raw_and_validated_results_saved_without_key(self):
        with patch("run_compatible_annotation_pilot.local_key", return_value="synthetic-secret"), \
                patch("run_compatible_annotation_pilot.send", return_value=self.response()) as send, \
                contextlib.redirect_stdout(StringIO()) as out:
            self.assertEqual(main(self.args()), 0)
        send.assert_called_once()
        self.assertNotIn("synthetic-secret", out.getvalue())
        for path in (self.path / "run").glob("*.json"):
            self.assertNotIn("synthetic-secret", path.read_text(encoding="utf-8"))
        manifest = json.loads((self.path / "run/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["records"][0]["status"], "valid")
        self.assertEqual(manifest["api_requests"], 1)
        self.assertIsNone(manifest["quality_metrics"])
        self.assertTrue((self.path / "run/01-assessment.json").exists())

    def test_rate_error_is_not_retried(self):
        with patch("run_compatible_annotation_pilot.local_key", return_value="synthetic-secret"), \
                patch("run_compatible_annotation_pilot.send", side_effect=HTTPError("https://example.invalid", 429,
                      "synthetic-secret", None, None)) as send, contextlib.redirect_stdout(StringIO()) as out:
            self.assertEqual(main(self.args()), 1)
        send.assert_called_once()
        self.assertNotIn("synthetic-secret", out.getvalue())
        manifest = json.loads((self.path / "run/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["records"][0]["http_status"], 429)

    def test_changed_input_is_rejected_and_raw_response_retained(self):
        bad = copy.deepcopy(self.doc)
        bad["messages"][0]["text"] += " changed"
        with patch("run_compatible_annotation_pilot.local_key", return_value="synthetic-secret"), \
                patch("run_compatible_annotation_pilot.send", return_value=self.response(bad)), \
                contextlib.redirect_stdout(StringIO()):
            self.assertEqual(main(self.args()), 1)
        self.assertTrue((self.path / "run/01-response.json").exists())
        self.assertFalse((self.path / "run/01-annotation.json").exists())

    def test_explicit_continue_skips_timeout_without_retrying_session(self):
        second = copy.deepcopy(self.source['sessions'][0])
        second['session_id'] = 'next-session'
        self.source['sessions'].append(second)
        self.input.write_text(json.dumps(self.source), encoding='utf-8')
        second_doc = copy.deepcopy(self.doc)
        second_doc['session_id'] = 'next-session'
        with patch('run_compatible_annotation_pilot.local_key', return_value='synthetic-secret'), \
                patch('run_compatible_annotation_pilot.send', side_effect=[TimeoutError(), self.response(second_doc)]) as send, \
                contextlib.redirect_stdout(StringIO()):
            self.assertEqual(main(self.args() + ['--limit', '2', '--delay', '0', '--continue-on-failure']), 1)
        self.assertEqual(send.call_count, 2)
        sent_ids = [json.loads(call.args[2]['messages'][1]['content'])['session_id'] for call in send.call_args_list]
        self.assertEqual(sent_ids, [self.doc['session_id'], 'next-session'])
        self.assertTrue((self.path / 'run/02-annotation.json').exists())

    def test_explicit_continue_still_stops_on_http_rate_limit(self):
        with patch('run_compatible_annotation_pilot.local_key', return_value='synthetic-secret'), \
                patch('run_compatible_annotation_pilot.send', side_effect=HTTPError('https://example.invalid', 429, '', None, None)) as send, \
                contextlib.redirect_stdout(StringIO()):
            self.assertEqual(main(self.args() + ['--continue-on-failure']), 1)
        send.assert_called_once()

    def test_incomplete_refused_or_markdown_response_not_repaired(self):
        for reason, message in (("length", {"content": "{}"}), ("stop", {"refusal": "no", "content": "{}"}),
                                ("stop", {"content": "```json\n{}\n```"})):
            with self.assertRaises(ValueError):
                response_document({"choices": [{"finish_reason": reason, "message": message}]})

    def test_only_synthetic_source_without_gold_is_accepted(self):
        self.source["sessions"][0]["entities"] = []
        self.input.write_text(json.dumps(self.source), encoding="utf-8")
        with self.assertRaises(ValueError):
            source_sessions(self.input)
        self.source["synthetic_only"] = False
        self.input.write_text(json.dumps(self.source), encoding="utf-8")
        with self.assertRaises(ValueError):
            source_sessions(self.input)

    def test_endpoint_rejects_secrets_http_remote_and_redirects(self):
        self.assertEqual(endpoint("http://127.0.0.1:8000/v1/"), "http://127.0.0.1:8000/v1/chat/completions")
        for value in ("http://remote.invalid/v1", "https://secret@remote.invalid/v1", "https://remote.invalid/v1?key=secret"):
            with self.assertRaises(ValueError):
                endpoint(value)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, None, None, "https://other.invalid"))

    def test_exact_quote_positions_use_unicode_and_explicit_occurrence(self):
        source = {"session_id": "synthetic", "messages": [{"id": "m1", "text": "🙂 Тестимир и Тестимир"}]}
        raw = {**source, "schema_version": "annotation-quotes-v1", "case_flags": {
            "is_example": False, "annotation_uncertain": False, "clarification_reason": None},
            "entities": [{"id": "e1", "message_id": "m1", "type": "first_name", "person_id": "p1",
                          "mention": "Тестимир", "occurrence": 2}]}
        result = materialize_quotes(raw, source)
        entity = result["entities"][0]
        self.assertEqual(entity["start"], source["messages"][0]["text"].rindex("Тестимир"))
        self.assertNotIn("mention", entity)
        self.assertEqual(result["schema_version"], "annotation-mvp-v2")
        for mention, occurrence in (("ТЕСТИМИР", 1), ("Тестимир", 3), (" Тестимир", 1), ("Тестимир", True)):
            bad = copy.deepcopy(raw)
            bad["entities"][0].update(mention=mention, occurrence=occurrence)
            with self.assertRaises(ValueError):
                materialize_quotes(bad, source)


if __name__ == "__main__":
    unittest.main()
