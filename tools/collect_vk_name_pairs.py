"""Проверка доступа VK API и ограниченный пилот ФИ с локальным кешем."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

from research_name_pairs import ROOT, write_private

CACHE = ROOT / "vk-name-pairs-pilot.json"
WORD = re.compile(r"[А-ЯЁа-яё]+(?:-[А-ЯЁа-яё]+)*")


def settings(token_key="VK_ACCESS_TOKEN"):
    if token_key not in ("VK_ACCESS_TOKEN", "VK_SERVICE_TOKEN"):
        raise ValueError("Unsupported token type")
    values = {key: os.environ.get(key, "") for key in (token_key, "VK_API_VERSION")}
    path = Path(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in values and not values[key.strip()]:
                values[key.strip()] = value.strip().strip("\"'")
    return values[token_key], values["VK_API_VERSION"] or "5.199"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class ApiFailure(Exception):
    def __init__(self, code, token_expired=False):
        self.code = code if isinstance(code, int) else None
        self.reason = "access_token_expired" if token_expired else None
        super().__init__("VK API request failed")


def api(method, parameters, token, version):
    if method not in ("database.getCountries", "users.search", "utils.getServerTime", "groups.getMembers"):
        raise ValueError("Unsupported method")
    body = urlencode({**parameters, "access_token": token, "v": version}).encode()
    request = Request("https://api.vk.com/method/" + method, data=body,
                      headers={"User-Agent": "SessionPiiResearch/0.1",
                               "Content-Type": "application/x-www-form-urlencoded"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Response too large")
    payload = json.loads(raw)
    if "error" in payload:
        # Ответ ошибки может содержать request_params и access_token: не экспортировать.
        message = payload["error"].get("error_msg", "")
        expired = isinstance(message, str) and "access_token has expired" in message.casefold()
        raise ApiFailure(payload["error"].get("error_code"), token_expired=expired)
    return payload["response"]


def normalize_items(items):
    records, counts, seen = [], Counter(), set()
    for item in items:
        identifier = item.get("id")
        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
            counts["invalid_id"] += 1
            continue
        if identifier in seen:
            counts["duplicate_accounts"] += 1
            continue
        seen.add(identifier)
        if item.get("deactivated") or item.get("is_closed"):
            counts["deactivated_or_closed"] += 1
            continue
        first, last = item.get("first_name", "").strip(), item.get("last_name", "").strip()
        if not WORD.fullmatch(first) or not WORD.fullmatch(last):
            counts["unsupported_written_name"] += 1
            continue
        records.append({"account_id": identifier, "name": first.casefold(), "surname": last.casefold(),
                        "sex": item.get("sex") if item.get("sex") in (1, 2) else None})
    return records, dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Один ограниченный пилотный поиск")
    parser.add_argument("--probe-service", action="store_true", help="Проверить сервисный токен отдельно от пользовательского")
    args = parser.parse_args()
    if args.probe_service:
        if args.download:
            parser.error("--probe-service and --download are separate operations")
        token, version = settings("VK_SERVICE_TOKEN")
        if not token:
            raise SystemExit("Добавьте VK_SERVICE_TOKEN в локальный .env.")
        report = {"checked_at": datetime.now(timezone.utc).isoformat(), "token_type": "service", "methods": {}}
        for method, parameters in (("utils.getServerTime", {}), ("users.search", {"count": 1, "offset": 0})):
            try:
                payload = api(method, parameters, token, version)
                report["methods"][method] = {"status": "accessible"}
                if method == "users.search":
                    report["methods"][method]["returned_accounts"] = len(payload.get("items", []))
            except Exception as error:
                report["methods"][method] = {"status": "request_failed", "error_type": type(error).__name__,
                                              "error_code": getattr(error, "code", None)}
        write_private(ROOT / "vk-service-token-access.json", report)
        print(json.dumps(report))
        return
    token, version = settings()
    if not args.download:
        print(json.dumps({"token_configured": bool(token), "api_version": version,
                          "cache_exists": CACHE.exists(), "network_used": False}))
        return
    if CACHE.exists():
        cached = json.loads(CACHE.read_text(encoding="utf-8"))
        print(json.dumps({"status": "using_existing_cache", "accounts": len(cached["records"])}))
        return
    if not token:
        raise SystemExit("Добавьте пользовательский VK_ACCESS_TOKEN в локальный .env; токен не присылайте в чат.")
    try:
        country_id = 1
        parameters = {"country": country_id, "count": 100, "offset": 0, "sort": 1, "fields": "sex"}
        payload = api("users.search", parameters, token, version)
        records, counts = normalize_items(payload["items"])
        write_private(CACHE, {"source": "vk_users_search_pilot", "checked_at": datetime.now(timezone.utc).isoformat(),
                              "api_version": version, "parameters": parameters, "counts": counts,
                              "records": records, "scope": "Accounts in one sorted search page; not people or population frequencies"})
        print(json.dumps({"status": "cached", "accounts": len(records), "counts": counts}))
    except Exception as error:
        failure = {"status": "request_failed", "error_type": type(error).__name__,
                   "error_code": getattr(error, "code", None)}
        write_private(ROOT / "vk-last-attempt.json", failure)
        print(json.dumps(failure))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
