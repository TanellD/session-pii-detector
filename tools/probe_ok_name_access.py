"""Проверка доступа ОК: одна страница участников и до десяти профилей, без вывода ПДн."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from collect_vk_name_pairs import NoRedirect
from research_name_pairs import ROOT, write_private

KEYS = ("OK_APPLICATION_KEY", "OK_APPLICATION_SECRET_KEY", "OK_ACCESS_TOKEN", "OK_GROUP_ID")


def settings():
    values = {key: os.environ.get(key, "") for key in KEYS}
    path = Path(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in values and not values[key.strip()]:
                values[key.strip()] = value.strip().strip("\"'")
    return values


def signed_parameters(method, parameters, config):
    if method not in ("group.getMembers", "users.getInfo"):
        raise ValueError("Unsupported method")
    values = {**parameters, "method": method, "application_key": config["OK_APPLICATION_KEY"],
              "format": "json", "__online": "false"}
    if any(key in parameters for key in ("access_token", "sig", "session_key")):
        raise ValueError("Authentication parameters cannot be overridden")
    secret = hashlib.md5((config["OK_ACCESS_TOKEN"] + config["OK_APPLICATION_SECRET_KEY"]).encode()).hexdigest()
    text = "".join(f"{key}={values[key]}" for key in sorted(values)) + secret
    return {**values, "sig": hashlib.md5(text.encode()).hexdigest(), "access_token": config["OK_ACCESS_TOKEN"]}


class OkFailure(Exception):
    def __init__(self, code, wait_millis=None):
        self.code = code if isinstance(code, int) else None
        self.wait_millis = wait_millis if isinstance(wait_millis, int) and wait_millis >= 0 else None
        super().__init__("OK API request failed")


def api(method, parameters, config):
    body = urlencode(signed_parameters(method, parameters, config)).encode()
    request = Request("https://api.ok.ru/fb.do", data=body,
                      headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "SessionPiiResearch/0.1"})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Response too large")
    payload = json.loads(raw)
    if isinstance(payload, dict) and "error_code" in payload:
        raise OkFailure(payload["error_code"], payload.get("wait_millis"))
    return payload


def probe(config, group):
    missing = [key for key in KEYS[:3] if not config.get(key)]
    if not group:
        missing.append("OK_GROUP_ID or --group")
    if missing:
        return {"status": "configuration_required", "missing": missing, "network_used": False}
    if not re.fullmatch(r"[0-9]{1,32}", group):
        raise ValueError("A numeric group ID is required")
    members = api("group.getMembers", {"uid": group, "count": "10", "statuses": ""}, config)
    if not isinstance(members, dict) or not isinstance(members.get("members"), list):
        raise ValueError("Unexpected member-page structure")
    if members.get("membersHidden"):
        return {"status": "members_hidden", "network_used": True}
    identifiers = []
    for row in members["members"]:
        if not isinstance(row, dict):
            raise ValueError("Unexpected member structure")
        identifier = str(row.get("userId", ""))
        if re.fullmatch(r"[0-9]{1,32}", identifier) and identifier not in identifiers:
            identifiers.append(identifier)
    if not identifiers:
        return {"status": "no_member_ids", "network_used": True}
    if len(identifiers) > 10:
        raise ValueError("Probe member bound exceeded")
    time.sleep(1)
    users = api("users.getInfo", {"uids": ",".join(identifiers),
                                 "fields": "uid,first_name,last_name,gender,private"}, config)
    if not isinstance(users, list) or not all(isinstance(row, dict) for row in users):
        raise ValueError("Unexpected user-response structure")
    allowed, seen, quality = set(identifiers), set(), Counter()
    for row in users:
        identifier = str(row.get("uid", ""))
        if identifier not in allowed or identifier in seen:
            quality["unexpected_or_duplicate_uid"] += 1
            continue
        seen.add(identifier)
        if row.get("private"):
            quality["private"] += 1
        elif isinstance(row.get("first_name"), str) and isinstance(row.get("last_name"), str) and row["first_name"].strip() and row["last_name"].strip():
            quality["public_written_name_and_surname"] += 1
        else:
            quality["missing_name_parts"] += 1
    return {"status": "probe_completed", "network_used": True, "requested_profiles": len(identifiers),
            "returned_profiles": len(users), "quality": dict(quality), "has_more": members.get("has_more"),
            "anchor_present": bool(members.get("anchor")), "patronymic_field_confirmed": False,
            "million_record_access_confirmed": False, "personal_rows_saved": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", help="Numeric OK group ID; otherwise OK_GROUP_ID in .env")
    args = parser.parse_args()
    config = settings()
    try:
        result = probe(config, args.group or config["OK_GROUP_ID"])
    except Exception as error:
        result = {"status": "stopped", "error_type": type(error).__name__,
                  "error_code": getattr(error, "code", None), "wait_millis": getattr(error, "wait_millis", None)}
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    write_private(ROOT / "ok-access-probe.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "probe_completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
