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
        if not Path("data/reference/housing-occupancy-vpn2020.json").exists():
            self.skipTest("Справочники не построены")
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", 8765))
            except OSError:
                self.skipTest("Порт занят: чужой процесс не останавливается")
        process = subprocess.Popen([sys.executable, "-X", "utf8", "tools/annotation_calculator.py"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            ready = False
            for _ in range(50):
                try:
                    with urlopen("http://127.0.0.1:8765/", timeout=1) as response:
                        self.assertIn("Калькулятор".encode(), response.read())
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
            request = Request("http://127.0.0.1:8765/api/estimate", data=payload,
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
        finally:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
