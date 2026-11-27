"""Синтетические проверки PKCE, callback и сохранения .env без реальных секретов."""

import json
from pathlib import Path
import sys
import tempfile
import socket
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, parse_qs, urlsplit
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from authorize_vk_id import Flow, Handler, challenge, save_env, exchange, token_updates


class VkIdAuthorizationTests(unittest.TestCase):
    def test_idle_browser_connection_does_not_block_status_or_form_submission(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.flow, server.client_id = Flow(), ""
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        idle = socket.create_connection(server.server_address, timeout=2)
        try:
            base = "http://127.0.0.1:" + str(server.server_port)
            with urlopen(Request(base + "/status", headers={"Host": "localhost"}), timeout=2) as response:
                self.assertIn(b"waiting_for_application", response.read())
            # Проверяем POST и заголовок перехода без обращения к VK.
            request = ("POST /start HTTP/1.0\r\nHost: localhost\r\nOrigin: http://localhost\r\n"
                       "Content-Type: application/x-www-form-urlencoded\r\n")
            data = urlencode({"client_id": "12345", "nonce": server.flow.form_nonce}).encode()
            with socket.create_connection(server.server_address, timeout=2) as connection:
                connection.sendall(request.encode() + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
                raw = connection.recv(8192)
            self.assertIn(b"303", raw.split(b"\r\n", 1)[0])
            self.assertIn(b"Location: /continue", raw)
            with urlopen(Request(base + "/continue", headers={"Host": "localhost"}), timeout=2) as response:
                page = response.read().decode()
            self.assertIn("https://id.vk.ru/authorize?", page)
            self.assertIn("Продолжить в VK ID", page)
            self.assertNotIn(server.flow.pending["verifier"], page)
            # Старый nonce и неожиданное Origin должны показывать текст вместо пустой ошибки.
            for extra, nonce, expected_status in (
                    ("", "stale", b"400"),
                    ("Origin: https://example.invalid\r\n", server.flow.form_nonce, b"403"),
                    ("Origin: null\r\nSec-Fetch-Site: cross-site\r\n", server.flow.form_nonce, b"403"),
                    ("Origin: null\r\n", "stale", b"400")):
                data = urlencode({"client_id": "12345", "nonce": nonce}).encode()
                headers = "POST /start HTTP/1.0\r\nHost: localhost\r\n" + extra
                with socket.create_connection(server.server_address, timeout=2) as connection:
                    connection.sendall(headers.encode() + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
                    connection.settimeout(2)
                    raw = b""
                    while chunk := connection.recv(8192):
                        raw += chunk
                self.assertIn(expected_status, raw.split(b"\r\n", 1)[0])
                self.assertIn("<h1>".encode(), raw.split(b"\r\n\r\n", 1)[1])
            # no-referrer может скрыть Origin законного POST. Nonce всё равно обязателен.
            original_nonce = server.flow.form_nonce
            data = urlencode({"client_id": "12345", "nonce": original_nonce}).encode()
            headers = ("POST /start HTTP/1.0\r\nHost: localhost\r\nOrigin: null\r\n"
                       "Sec-Fetch-Site: same-origin\r\n")
            with socket.create_connection(server.server_address, timeout=2) as connection:
                connection.sendall(headers.encode() + f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
                raw = b""
                while chunk := connection.recv(8192):
                    raw += chunk
            self.assertIn(b"303", raw.split(b"\r\n", 1)[0])
            self.assertNotEqual(original_nonce, server.flow.form_nonce)
        finally:
            idle.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_pkce_matches_rfc7636_example(self):
        self.assertEqual(challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_authorization_url_does_not_reveal_verifier(self):
        flow = Flow()
        url = flow.start("12345")
        params = parse_qs(urlsplit(url).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["scope"], ["vkid.personal_info"])
        self.assertNotIn(flow.pending["verifier"], url)

    def callback(self, flow):
        return {"code": "synthetic-code", "device_id": "synthetic-device", "state": flow.pending["state"]}

    def test_payload_and_plain_callbacks_work_and_are_one_use(self):
        for wrapped in (True, False):
            flow = Flow()
            flow.start("12345")
            data = self.callback(flow)
            query = urlencode({"payload": json.dumps(data)}) if wrapped else urlencode(data)
            accepted = flow.accept(query)
            self.assertEqual(accepted["device_id"], "synthetic-device")
            self.assertEqual(accepted["redirect_uri"], "http://localhost/callback")
            with self.assertRaises(ValueError):
                flow.accept(query)

    def test_reject_bad_state_ambiguous_duplicate_and_expired_callbacks(self):
        flow = Flow()
        flow.start("12345")
        valid = self.callback(flow)
        with self.assertRaises(ValueError):
            flow.accept(urlencode({**valid, "state": "wrong"}))
        with self.assertRaises(ValueError):
            flow.accept(urlencode(valid) + "&code=other")
        with self.assertRaises(ValueError):
            flow.accept(urlencode({"payload": json.dumps(valid), "state": valid["state"]}))
        flow.pending["created"] -= 601
        with self.assertRaises(ValueError):
            flow.accept(urlencode(valid))

    def test_env_update_preserves_other_secrets_and_clears_stale_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".env"
            path.write_text("# comment\nOTHER_KEY=synthetic-other\nVK_ACCESS_TOKEN=old\nVK_ACCESS_TOKEN=duplicate\nVK_REFRESH_TOKEN=old-refresh\n")
            with patch("authorize_vk_id.Path.cwd", return_value=root), patch("authorize_vk_id.subprocess.run") as ignored:
                ignored.return_value.returncode = 0
                save_env(path, {"VK_ACCESS_TOKEN": "synthetic-new", "VK_REFRESH_TOKEN": ""})
            text = path.read_text()
            self.assertIn("OTHER_KEY=synthetic-other", text)
            self.assertEqual(text.count("VK_ACCESS_TOKEN="), 1)
            self.assertIn("VK_REFRESH_TOKEN=\n", text)
            self.assertFalse((root / ".env.vk-auth.tmp").exists())

    def test_env_write_rejects_tracked_paths_and_newline_injection(self):
        path = Path.cwd() / ".env"
        with patch("authorize_vk_id.subprocess.run") as tracked:
            tracked.return_value.returncode = 1
            with self.assertRaises(ValueError):
                save_env(path, {"VK_ACCESS_TOKEN": "synthetic-token"})
        with self.assertRaises(ValueError):
            save_env(path, {"VK_ACCESS_TOKEN": "synthetic\nOTHER=x"})

    def test_exchange_uses_post_and_rejects_response_state_mismatch(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit): return b'{"access_token":"synthetic-token","state":"other"}'
        with patch("authorize_vk_id.build_opener") as opener:
            opener.return_value.open.return_value = Response()
            with self.assertRaises(ValueError):
                exchange({"state": "expected", "code_verifier": "synthetic-verifier"})
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://id.vk.ru/oauth2/auth")
        self.assertIn(b"code_verifier=synthetic-verifier", request.data)

    def test_expired_token_metadata_does_not_preserve_stale_refresh(self):
        updates = token_updates({"client_id": "12345", "device_id": "synthetic-device"},
                                {"access_token": "synthetic-token", "expires_in": 0})
        self.assertEqual(updates["VK_REFRESH_TOKEN"], "")
        self.assertEqual(updates["VK_TOKEN_EXPIRES_AT"], "")


if __name__ == "__main__":
    unittest.main()
