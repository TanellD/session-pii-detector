"""Кешируемая выборка участников VK через groups.getMembers, только локально вне Git."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import re
import time
from urllib.error import URLError

from collect_vk_name_pairs import api, settings, normalize_items, ApiFailure
from research_name_pairs import ROOT, write_private, entropy

LANGUAGE = "ru"
PARSER_VERSION = 2


def failure_summary(error):
    """Export safe scalar diagnostics, never arbitrary exception payloads."""
    code = getattr(error, "code", None)
    reason = None
    if isinstance(error, ApiFailure):
        reason = error.reason
    elif isinstance(error, TimeoutError) or (isinstance(error, URLError) and isinstance(error.reason, TimeoutError)):
        reason = "network_timeout"
    elif isinstance(error, URLError):
        reason = "network_error"
    return {"status": "stopped", "error_type": type(error).__name__,
            "error_code": code if type(code) is int else None,
            "reason": reason, "cached_pages_preserved": True}


class MemberClient:
    """Sequential requests with one retry budget shared by the entire run."""

    def __init__(self, token, version, delay_seconds=1, max_retries=4):
        if not math.isfinite(delay_seconds) or not 1 <= delay_seconds <= 3600:
            raise ValueError("delay_seconds must be finite and between 1 and 3600")
        if not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between 0 and 10")
        self.token, self.version = token, version
        self.delay_seconds, self.max_retries = delay_seconds, max_retries
        self.retries_used = 0

    def request(self, parameters):
        attempt = 0
        wait_seconds = self.delay_seconds
        while True:
            time.sleep(wait_seconds)
            try:
                return api("groups.getMembers", parameters, self.token, self.version)
            except ApiFailure as error:
                if error.code not in (6, 9) or self.retries_used >= self.max_retries:
                    raise
                base = 60 if error.code == 9 else 1
                wait_seconds = max(self.delay_seconds, min(900, base * 2 ** attempt))
                attempt += 1
                self.retries_used += 1
                print(json.dumps({"stage": "api_backoff", "error_code": error.code,
                                  "wait_seconds": wait_seconds, "retries_used": self.retries_used,
                                  "retries_remaining": self.max_retries - self.retries_used}), flush=True)


def current_page(page):
    return page.get("language") == LANGUAGE and page.get("parser_version") == PARSER_VERSION


def archive_legacy(path):
    target = path.parent / "legacy-unlocalized" / path.name
    if path.exists() and not target.exists():
        write_private(target, json.loads(path.read_text(encoding="utf-8")))


def page_offsets(total, limit, page_size=1000):
    if total <= 0 or limit <= 0:
        return []
    pages = math.ceil(min(total, limit) / page_size)
    if pages == 1:
        return [0]
    if total <= limit:
        return list(range(0, total, page_size))
    # Детерминированные страницы по диапазону списка, не случайные аккаунты.
    return sorted({int(i * max(0, total - page_size) / (pages - 1)) for i in range(pages)})


def supported_page(payload):
    items = payload["items"]
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("Expected member objects with name fields")
    records, quality = normalize_items(items)
    countries = {item.get("id"): item.get("country", {}) for item in items}
    for record in records:
        country = countries.get(record["account_id"], {})
        record["country_id"] = country.get("id") if isinstance(country, dict) else None
    source_items = []
    for item in items:
        if item.get("deactivated") or item.get("is_closed"):
            continue
        source = {key: item[key] for key in ("id", "first_name", "last_name", "sex") if key in item}
        country = item.get("country")
        if isinstance(country, dict) and isinstance(country.get("id"), int):
            source["country"] = {"id": country["id"]}
        source_items.append(source)
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "total_at_fetch": payload["count"],
            "language": LANGUAGE, "parser_version": PARSER_VERSION,
            "returned_items": len(items), "records": records, "quality": quality, "source_items": source_items}


def pair_summary(records):
    rows = [(r["surname"], r["name"]) for r in records]
    if not rows:
        return {"status": "no_supported_records"}
    n = len(rows)
    marginal = [Counter(row[i] for row in rows) for i in range(2)]
    pairs = Counter(rows)
    actual = sum(entropy(c, n) for c in marginal) - entropy(pairs, n)
    rng, null = random.Random(42), []
    first, second = zip(*sorted(rows))
    for _ in range(199):
        shuffled = list(second)
        rng.shuffle(shuffled)
        null.append(sum(entropy(c, n) for c in marginal) - entropy(Counter(zip(first, shuffled)), n))
    return {"accounts": n, "distinct_written_FI": len(pairs), "repeated_FI_cells": sum(c > 1 for c in pairs.values()),
            "mutual_information_bits": actual, "permutation_mean_bits": sum(null) / len(null),
            "permutation_upper_tail_p": (1 + sum(v >= actual - 1e-10 for v in null)) / (1 + len(null)),
            "permutations": len(null), "seed": 42,
            "country_id_counts": dict(Counter(str(r.get("country_id")) for r in records)),
            "sex_counts": dict(Counter(str(r.get("sex")) for r in records)),
            "scope": "Accounts in deterministic community-member pages; names self-reported; no patronymics or population extrapolation"}


def collect(group, limit, token_type, download, delay_seconds=1, max_retries=4):
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", group):
        raise ValueError("Use a community screen name or numeric ID")
    directory = ROOT / "vk-groups" / group
    output_directory = directory
    plan_path = directory / "plan.json"
    key = "VK_SERVICE_TOKEN" if token_type == "service" else "VK_ACCESS_TOKEN"
    token, version = settings(key)
    client = MemberClient(token, version, delay_seconds, max_retries)
    if download and not token:
        raise ValueError("Requested token is not configured")
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if plan["token_type"] != token_type or plan["api_version"] != version:
            raise ValueError("Use the existing plan parameters for resuming this cache")
        if plan["limit"] != limit:
            if not download:
                output_directory = directory / "snapshots" / f"limit-{limit}"
                plan = json.loads((output_directory / "plan.json").read_text(encoding="utf-8"))
                if plan["limit"] != limit:
                    raise ValueError("Snapshot limit mismatch")
            elif limit < plan["limit"]:
                raise ValueError("Only downloading a larger plan can change the cache limit")
            else:
                snapshot = directory / "snapshots" / f"limit-{plan['limit']}"
                for filename in ("plan.json", "results.json", "adequacy.json"):
                    source, target = directory / filename, snapshot / filename
                    if source.exists() and not target.exists():
                        write_private(target, json.loads(source.read_text(encoding="utf-8")))
                plan = dict(plan, limit=limit, offsets=page_offsets(plan["total_at_start"], limit),
                            expanded_at=datetime.now(timezone.utc).isoformat())
                write_private(plan_path, plan)
        if download and (plan.get("language") != LANGUAGE or plan.get("parser_version") != PARSER_VERSION):
            archive_legacy(plan_path)
            plan.update(language=LANGUAGE, parser_version=PARSER_VERSION)
            write_private(plan_path, plan)
    else:
        if not download:
            raise ValueError("No local cache; run with --download first")
        first = supported_page(client.request({"group_id": group, "count": min(1000, limit),
                                               "offset": 0, "sort": "id_asc", "fields": "sex,country", "lang": LANGUAGE}))
        write_private(directory / "page-0.json", first)
        plan = {"group": group, "limit": limit, "token_type": token_type, "api_version": version,
                "created_at": datetime.now(timezone.utc).isoformat(), "total_at_start": first["total_at_fetch"],
                "offsets": page_offsets(first["total_at_fetch"], limit), "sort": "id_asc", "fields": "sex,country",
                "language": LANGUAGE, "parser_version": PARSER_VERSION}
        write_private(plan_path, plan)
    for offset in plan["offsets"]:
        path = directory / f"page-{offset}.json"
        if path.exists() and current_page(json.loads(path.read_text(encoding="utf-8"))):
            continue
        if not download:
            continue
        remaining = limit - plan["offsets"].index(offset) * 1000
        page = supported_page(client.request({"group_id": group, "count": min(1000, remaining),
                                             "offset": offset, "sort": "id_asc", "fields": "sex,country", "lang": LANGUAGE}))
        archive_legacy(path)
        write_private(path, page)
        print(json.dumps({"stage": "cached_page", "offset": offset, "returned_items": page["returned_items"],
                          "supported_accounts": len(page["records"]), "quality": page["quality"]}), flush=True)
    records, seen, quality, hashes, totals = [], set(), Counter(), [], []
    available = 0
    legacy = 0
    returned_items = 0
    for offset in plan["offsets"]:
        path = directory / f"page-{offset}.json"
        if not path.exists():
            continue
        raw = path.read_bytes()
        page = json.loads(raw)
        if not current_page(page):
            legacy += 1
            continue
        available += 1
        returned_items += page.get("returned_items", 0)
        hashes.append(hashlib.sha256(raw).hexdigest())
        totals.append(page["total_at_fetch"])
        quality.update(page["quality"])
        for row in page["records"]:
            if row["account_id"] in seen:
                quality["duplicate_accounts_across_pages"] += 1
                continue
            seen.add(row["account_id"])
            records.append(row)
    report = {"source": "vk_groups_getMembers", "group": group,
              "checked_at": datetime.now(timezone.utc).isoformat(), "total_at_start": plan["total_at_start"],
              "planned_pages": len(plan["offsets"]), "cached_pages": available,
              "returned_items": returned_items,
              "legacy_pages_ignored": legacy, "language": LANGUAGE, "parser_version": PARSER_VERSION,
              "request_policy": {"delay_seconds": delay_seconds, "max_retries_per_run": max_retries,
                                 "retries_used": client.retries_used},
              "complete_plan": available == len(plan["offsets"]), "member_count_changed_during_collection": len(set(totals)) > 1,
              "page_sha256": hashes, "quality": dict(quality), "pair_statistics": pair_summary(records)}
    write_private(output_directory / "results.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="vk")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--token-type", choices=("service", "user"), default="service")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--delay-seconds", type=float, default=1, help="Pause before each request, at least one second")
    parser.add_argument("--max-retries", type=int, default=4, help="Total retry budget for errors 6 and 9 across the run; 0 disables retries")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000000:
        parser.error("limit must be between 1 and 1000000")
    try:
        report = collect(args.group, args.limit, args.token_type, args.download,
                         args.delay_seconds, args.max_retries)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    except Exception as error:
        failure = failure_summary(error)
        write_private(ROOT / "vk-groups-last-attempt.json", failure)
        print(json.dumps(failure))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
