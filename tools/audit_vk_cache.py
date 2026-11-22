"""Офлайн-аудит всех страниц VK: текущий план, прежние страницы, целостность и дубли ID."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from research_name_pairs import ROOT
from collect_vk_group_members import current_page


def audit(directory):
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    offsets = set(plan["offsets"])
    counts, identifiers, planned_identifiers = Counter(), set(), set()
    invalid, latest = [], None
    for path in sorted(directory.glob("page-*.json")):
        try:
            offset = int(path.stem[5:])
            page = json.loads(path.read_text(encoding="utf-8"))
            if not current_page(page):
                counts["legacy_pages_ignored"] += 1
                continue
            ids = {row["account_id"] for row in page["records"]}
            category = "current_plan" if offset in offsets else "outside_current_plan"
            counts[category + "_pages"] += 1
            counts[category + "_returned_records"] += page["returned_items"]
            counts[category + "_supported_rows"] += len(page["records"])
            identifiers.update(ids)
            if offset in offsets:
                planned_identifiers.update(ids)
            modified = path.stat().st_mtime
            latest = modified if latest is None else max(latest, modified)
        except (ValueError, KeyError, TypeError) as error:
            invalid.append({"file": path.name, "error_type": type(error).__name__})
    return {"network_used": False, "requested_limit": plan["limit"], "planned_pages": len(offsets),
            "counts": dict(counts), "all_cached_unique_supported_accounts": len(identifiers),
            "current_plan_unique_supported_accounts": len(planned_identifiers),
            "extra_unique_supported_accounts": len(identifiers - planned_identifiers),
            "invalid_files": invalid, "temporary_files": [str(p.relative_to(directory)) for p in directory.rglob("*.tmp")],
            "latest_page_modified_utc": datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest is not None else None,
            "scope": "Current-format pages only; all-cache union mixes deterministic plans; accounts are not verified people"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", default="vk")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", args.group):
        parser.error("Use a community screen name or numeric ID")
    print(json.dumps(audit(ROOT / "vk-groups" / args.group), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
