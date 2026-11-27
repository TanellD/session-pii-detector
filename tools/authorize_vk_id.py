"""Локальный VK ID Authorization Code + PKCE для публичного Web-приложения."""

import argparse
import base64
from datetime import datetime, timezone, timedelta
import hashlib
from html import escape
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import time
import threading
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener

from collect_vk_name_pairs import NoRedirect

REDIRECT = "http://localhost/callback"


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


def env_settings(path):
    result = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                result[key.strip()] = value.strip().strip("\"'")
    return result


def save_env(path, updates):
    if path.resolve().parent != Path.cwd().resolve() or path.name != ".env":
        raise ValueError("Environment file must stay in project root")
    for key, value in updates.items():
        if not re.fullmatch(r"VK_[A-Z0-9_]+", key) or any(c in str(value) for c in ('\r', '\n', '\x00', '"', "'", '#', '\\')):
            raise ValueError("Unsafe environment value")
    temporary = path.with_name(".env.vk-auth.tmp")
    for target in (path, temporary):
        if subprocess.run(["git", "check-ignore", "--quiet", str(target)], capture_output=True).returncode:
            raise ValueError("Environment file is not ignored by Git")
    previous = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    remaining, used = [], set()
    for line in previous.splitlines():
        key = line.partition("=")[0].strip()
        if key in updates:
            if key not in used:
                remaining.append(f"{key}={updates[key]}")
                used.add(key)
        else:
            remaining.append(line)
    remaining.extend(f"{key}={value}" for key, value in updates.items() if key not in used)
    temporary.write_text("\n".join(remaining) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Flow:
    def __init__(self):
        self.lock = threading.RLock()
        self.form_nonce = secrets.token_urlsafe(32)
        self.pending = None
        self.status = "waiting_for_application"
        self.last_event = "ready"
        self.authorization_url = None

    def start(self, client_id):
        with self.lock:
            return self._start(client_id)

    def _start(self, client_id):
        if not re.fullmatch(r"[1-9][0-9]{0,19}", client_id):
            raise ValueError("Invalid client ID")
        self.pending = {"client_id": client_id, "verifier": secrets.token_urlsafe(48),
                        "state": secrets.token_urlsafe(32), "created": time.monotonic()}
        self.status = "awaiting_vk_callback"
        self.authorization_url = "https://id.vk.ru/authorize?" + urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
            "scope": "vkid.personal_info", "state": self.pending["state"],
            "code_challenge": challenge(self.pending["verifier"]), "code_challenge_method": "S256"})
        self.last_event = "authorization_link_ready"
        return self.authorization_url

    def accept(self, query):
        with self.lock:
            return self._accept(query)

    def _accept(self, query):
        fields = parse_qs(query, keep_blank_values=True, max_num_fields=20)
        if any(len(values) != 1 for values in fields.values()):
            raise ValueError("Repeated callback fields")
        if "payload" in fields:
            if any(key in fields for key in ("code", "state", "device_id")):
                raise ValueError("Ambiguous callback")
            payload = json.loads(fields["payload"][0])
        else:
            payload = {key: values[0] for key, values in fields.items()}
        if not isinstance(payload, dict) or not self.pending:
            raise ValueError("No pending authorization")
        state = payload.get("state")
        if not isinstance(state, str) or not secrets.compare_digest(state, self.pending["state"]):
            raise ValueError("State mismatch")
        if time.monotonic() - self.pending["created"] > 600:
            self.pending = None
            raise ValueError("Authorization expired")
        if payload.get("error") or not all(isinstance(payload.get(k), str) and payload[k] for k in ("code", "device_id")):
            self.pending = None
            self.status = "authorization_not_completed"
            raise ValueError("Incomplete callback")
        parameters = {"client_id": self.pending["client_id"], "grant_type": "authorization_code",
                      "code_verifier": self.pending["verifier"], "code": payload["code"],
                      "device_id": payload["device_id"], "redirect_uri": REDIRECT, "state": state}
        self.pending = None  # одноразовый callback, в том числе при ошибке обмена
        self.authorization_url = None
        return parameters


def exchange(parameters):
    request = Request("https://id.vk.ru/oauth2/auth", data=urlencode(parameters).encode(), headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "SessionPiiResearch/0.1"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(262145)
    if len(raw) > 262144:
        raise ValueError("Oversized token response")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("error") or not isinstance(payload.get("access_token"), str) or not payload["access_token"]:
        raise ValueError("Token exchange failed")
    if "state" in payload and payload["state"] != parameters["state"]:
        raise ValueError("Token response state mismatch")
    return payload


def token_updates(parameters, payload):
    expires = payload.get("expires_in")
    if not isinstance(expires, int) or isinstance(expires, bool) or expires < 0:
        raise ValueError("Invalid token expiry")
    return {"VK_CLIENT_ID": parameters["client_id"], "VK_REDIRECT_URI": REDIRECT,
            "VK_ACCESS_TOKEN": payload["access_token"], "VK_REFRESH_TOKEN": payload.get("refresh_token", ""),
            "VK_DEVICE_ID": parameters["device_id"], "VK_TOKEN_SCOPE": payload.get("scope", ""),
            "VK_TOKEN_EXPIRES_AT": (datetime.now(timezone.utc) + timedelta(seconds=expires)).isoformat() if expires else "",
            "VK_TOKEN_OBTAINED_AT": datetime.now(timezone.utc).isoformat()}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(10)
        super().setup()

    def log_message(self, *args):
        pass  # URL callback содержит code, state, device_id

    def host_ok(self):
        return self.headers.get("Host") in ("localhost", "localhost:80", "127.0.0.1", "127.0.0.1:80")

    def reply(self, status, body="", location=None):
        if status >= 400 and not body:
            body = "<h1>Запрос не выполнен</h1><p>Откройте свежую форму и повторите вход.</p><a href='/'>Открыть форму</a>"
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "same-origin" if urlsplit(self.path).path == "/" else "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self' https://id.vk.ru; frame-ancestors 'none'; base-uri 'none'")
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        encoded = ("<!doctype html><html lang='ru'><meta charset='utf-8'><title>VK ID</title><body>" + body + "</body></html>").encode()
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if not self.host_ok():
            return self.reply(403, "Недопустимый адрес.")
        path = urlsplit(self.path)
        if path.path == "/":
            client_id = self.server.client_id
            return self.reply(200, "<h1>Вход через VK ID</h1><p>Публичное Web-приложение: домен localhost, redirect URL http://localhost/callback.</p>"
                              "<p>Пароль вводится только на сайте VK. После входа токен сохранится в локальный .env.</p>"
                              "<form method='post' action='/start'><label>ID приложения: "
                              f"<input name='client_id' required inputmode='numeric' pattern='[0-9]+' value='{escape(client_id, quote=True)}'></label>"
                              f"<input type='hidden' name='nonce' value='{self.server.flow.form_nonce}'>"
                              "<button type='submit'>Войти через VK ID</button></form>")
        if path.path == "/status":
            return self.reply(200, escape(self.server.flow.status) + " " + escape(self.server.flow.last_event))
        if path.path == "/start":
            return self.reply(200, "<h1>Откройте форму входа</h1><p>Обновление адреса /start не отправляет ID приложения повторно.</p><a href='/'>Вернуться к вводу ID</a>")
        if path.path == "/continue":
            with self.server.flow.lock:
                flow = self.server.flow
                if not flow.pending or not flow.authorization_url or time.monotonic() - flow.pending["created"] > 600:
                    return self.reply(400, "<h1>Попытка входа устарела</h1><a href='/'>Начать заново</a>")
                url = escape(flow.authorization_url, quote=True)
                client_id = escape(flow.pending["client_id"])
            return self.reply(200, f"<h1>Авторизация подготовлена</h1><p>ID приложения: {client_id}.</p>"
                              f"<p><a href='{url}' rel='noreferrer'>Продолжить в VK ID</a></p>"
                              "<p>Вход выполняется на сайте VK. После подтверждения вы вернётесь сюда.</p>"
                              "<a href='/'>Изменить ID приложения</a>")
        if path.path == "/done":
            return self.reply(200, "<h1>Токен сохранён в .env</h1><p>Окно можно закрыть. Доступ к users.search проверяется отдельно.</p>")
        if path.path != "/callback":
            return self.reply(404)
        try:
            parameters = self.server.flow.accept(path.query)
            payload = exchange(parameters)
            save_env(self.server.env_path, token_updates(parameters, payload))
            self.server.flow.status = "token_saved"
            print("VK ID: токен сохранён в .env; секреты не выводятся.", flush=True)
            return self.reply(303, location="/done")
        except Exception as error:
            # Не экспортировать ответ провайдера, URL и str(error).
            print("VK ID: авторизация не завершена; тип ошибки: " + type(error).__name__, flush=True)
            return self.reply(400, "<p>Авторизация не завершена. Проверьте настройки приложения и повторите вход.</p><a href='/'>Начать заново</a>")

    def do_POST(self):
        if not self.host_ok() or self.path != "/start":
            self.server.flow.last_event = "start_rejected_address"
            return self.reply(403, "<h1>Неверный адрес формы</h1><a href='http://localhost/'>Открыть локальную форму</a>")
        # В частности Firefox может послать Origin: null для POST при no-referrer.
        # Null не подтверждает источник: обязательный nonce ниже остаётся защитой формы.
        if self.headers.get("Sec-Fetch-Site") == "cross-site" or self.headers.get("Origin") not in (None, "null", "http://localhost", "http://localhost:80", "http://127.0.0.1", "http://127.0.0.1:80"):
            self.server.flow.last_event = "start_rejected_origin"
            return self.reply(403, "<h1>Форма открыта из другого источника</h1><p>Откройте http://localhost/ напрямую в браузере.</p><a href='http://localhost/'>Открыть форму</a>")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4096:
                raise ValueError("Invalid form size")
            fields = parse_qs(self.rfile.read(length).decode(), max_num_fields=4)
            with self.server.flow.lock:
                if any(len(v) != 1 for v in fields.values()) or not secrets.compare_digest(fields.get("nonce", [""])[0], self.server.flow.form_nonce):
                    self.server.flow.last_event = "start_rejected_stale_form"
                    return self.reply(400, "<h1>Форма устарела</h1><p>Обновите страницу после перезапуска помощника.</p><a href='/'>Открыть свежую форму</a>")
                self.server.flow.start(fields.get("client_id", [""])[0].strip())
                self.server.flow.form_nonce = secrets.token_urlsafe(32)
            return self.reply(303, "<p>ID принят.</p><a href='/continue'>Продолжить авторизацию</a>", location="/continue")
        except Exception:
            self.server.flow.last_event = "start_rejected_invalid_form"
            return self.reply(400, "Некорректный ID приложения. <a href='/'>Вернуться</a>")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Проверка конфигурации без сети и запуска сервера")
    args = parser.parse_args()
    path = Path(".env")
    config = env_settings(path)
    client_id = os.environ.get("VK_CLIENT_ID") or config.get("VK_CLIENT_ID", "")
    if args.check:
        print(json.dumps({"client_id_configured": bool(client_id), "redirect_uri": REDIRECT,
                          "token_configured": bool(config.get("VK_ACCESS_TOKEN")), "network_used": False}))
        return
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 80), Handler)
        server.flow, server.env_path, server.client_id = Flow(), path, client_id
        print("VK ID: откройте http://localhost/ . Сервер доступен только на этом компьютере.", flush=True)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    except OSError:
        raise SystemExit("Не удалось открыть порт 80. Проверьте, свободен ли он и разрешён ли локальный сервер.")
    finally:
        if "server" in locals():
            server.server_close()


if __name__ == "__main__":
    main()
