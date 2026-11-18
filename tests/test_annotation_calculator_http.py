"""Проверка локального HTTP-интерфейса на синтетической сессии."""

import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CalculatorHttpTests(unittest.TestCase):
    def test_local_page_and_estimate(self):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            except OSError:
                self.skipTest("Локальные сокеты недоступны")
        base = f"http://127.0.0.1:{port}"
        process = subprocess.Popen([sys.executable, "-X", "utf8", "tools/annotation_calculator.py", "--policy-only", "--port", str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            ready = False
            for _ in range(50):
                try:
                    with urlopen(base + "/", timeout=1) as response:
                        page = response.read()
                        self.assertIn("Калькулятор".encode(), page)
                        self.assertNotIn(b'value="fio"', page)
                        self.assertNotIn(b'value="relationship"', page)
                        self.assertNotIn(b'id="uncertain"', page)
                        self.assertNotIn(b'????', page)
                    ready = True
                    break
                except (URLError, TimeoutError):
                    if process.poll() is not None:
                        break
                    time.sleep(0.1)
            self.assertTrue(ready, "Сервис не запустился")
            facts = [{"type": kind, "value": "[СИНТЕТИЧЕСКИЙ-КОНТАКТ]", "person_id": "p1",
                      "complete": True, "usage": "personal"} for kind in ("phone", "email")]
            payload = json.dumps({"facts": facts}).encode()
            request = Request(base + "/api/estimate", data=payload,
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
                self.assertTrue(result["session_alert"])
                self.assertEqual(result["first_alert_attribute"], 2)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            request.add_header("Origin", "https://synthetic.example")
            with self.assertRaises(HTTPError) as error:
                urlopen(request, timeout=2)
            self.assertEqual(error.exception.code, 403)
            with urlopen(base + "/api/context", timeout=2) as response:
                self.assertEqual(json.load(response)["reference_mode"], "policy_only")
            for contact in facts:
                contact["person_id"] = None
            request = Request(base + "/api/estimate", data=json.dumps({"facts": facts}).encode(),
                              headers={"Content-Type": "application/json"})
            with urlopen(request, timeout=2) as response:
                result = json.load(response)
                self.assertFalse(result["session_alert"])
                self.assertFalse(result["annotation_uncertain"])
            cases = json.loads(Path("tests/fixtures/annotation-mvp-cases.json").read_text(encoding="utf-8"))["cases"]
            doc = next(c["annotation"] for c in cases if c["id"] == "io_phone")
            def post_annotation(value):
                return urlopen(Request(base + "/api/annotation", data=json.dumps(value).encode(),
                                      headers={"Content-Type": "application/json"}), timeout=2)
            with post_annotation(doc) as response:
                result = json.load(response)
                self.assertEqual(result["first_alert"]["message_id"], "m3")
                self.assertEqual(result["first_alert"]["reason"], "combination_rule")
            doc["entities"][0]["type"] = "fio"
            with self.assertRaises(HTTPError) as error:
                post_annotation(doc)
            self.assertEqual(error.exception.code, 400)
        finally:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
