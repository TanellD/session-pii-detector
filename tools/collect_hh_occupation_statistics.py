"""Получить агрегаты профессиональных ролей hh; стандартная библиотека, без сохранения вакансий."""

import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://api.hh.ru"


def credentials():
    values = {}
    path = Path(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key in {"HH_API_TOKEN", "HH_USER_AGENT"}:
                values[key] = value.strip().strip("\"'")
    for key in ("HH_API_TOKEN", "HH_USER_AGENT"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


class ApiFailure(Exception):
    def __init__(self, category, http_status=None):
        super().__init__(category)
        self.category = category
        self.http_status = http_status

    def public(self):
        return {"category": self.category, "http_status": self.http_status}


class Client:
    def __init__(self, auth, timeout=20, retries=2):
        self.headers = {"User-Agent": auth.get("HH_USER_AGENT", "SessionPiiResearch/0.1"),
                        "HH-User-Agent": auth.get("HH_USER_AGENT", "SessionPiiResearch/0.1"),
                        "Accept": "application/json"}
        if auth.get("HH_API_TOKEN"):
            self.headers["Authorization"] = "Bearer " + auth["HH_API_TOKEN"]
        self.timeout = timeout
        self.retries = retries

    def get(self, path, params):
        url = API + path + "?" + urlencode(params)
        for attempt in range(self.retries + 1):
            try:
                with urlopen(Request(url, headers=self.headers), timeout=self.timeout) as response:
                    raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ApiFailure("response_too_large")
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise ApiFailure("non_json_response") from None
                if not isinstance(data, dict) or "errors" in data:
                    raise ApiFailure("unexpected_api_response")
                return data
            except HTTPError as error:
                status = error.code
                retry_after = error.headers.get("Retry-After", "")
                error.close()
                if status in {429, 500, 502, 503, 504} and attempt < self.retries:
                    delay = min(float(retry_after), 8) if retry_after.isdigit() else 2 ** attempt
                    time.sleep(delay)
                    continue
                category = {400: "bad_request_check_user_agent_and_parameters",
                            401: "authorization_required_or_expired",
                            403: "access_denied_or_captcha",
                            429: "rate_limited"}.get(status, "http_error")
                # Тело ошибки, URL капчи и заголовки авторизации не выводятся.
                raise ApiFailure(category, status) from None
            except (URLError, TimeoutError, socket.timeout):
                if attempt < self.retries:
                    time.sleep(2 ** attempt)
                    continue
                raise ApiFailure("network_or_tls_error") from None


def nonnegative_integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApiFailure("invalid_counter_in_response")
    return value


def safe_label(value):
    return " ".join(str(value).split())[:180]


def cluster_statistics(data):
    found = nonnegative_integer(data.get("found"))
    groups = data.get("clusters") or []
    if not isinstance(groups, list):
        raise ApiFailure("invalid_clusters_in_response")
    group = next((c for c in groups if isinstance(c, dict) and c.get("id") == "professional_role"), None)
    rows = []
    seen = set()
    if group is not None:
        for item in group.get("items", []):
            role_id = str(item.get("value", item.get("id", "")))
            if not role_id or role_id in seen:
                raise ApiFailure("invalid_or_duplicate_role_in_clusters")
            seen.add(role_id)
            count = nonnegative_integer(item.get("count"))
            if count > found:
                raise ApiFailure("role_count_exceeds_total")
            rows.append({"id": safe_label(role_id), "name": safe_label(item.get("name", role_id)),
                         "count": count, "share_of_found": count / found if found else None})
    return {"found": found, "professional_role_cluster_present": group is not None,
            "roles": sorted(rows, key=lambda row: (-row["count"], row["id"])),
            "sum_role_counts": sum(row["count"] for row in rows),
            "other": None,
            "interpretation": "Маргинальные доли среди найденных вакансий. Роли могут пересекаться; other из остатка не вычисляется."}


class SampleAccumulator:
    def __init__(self):
        self.seen = set()
        self.weights = Counter()
        self.names = {"other": "Нет профессиональной роли"}
        self.duplicates = 0
        self.multi_role = 0
        self.missing_ids = 0

    def add(self, items):
        for item in items:
            vacancy_id = item.get("id")
            if vacancy_id is None:
                self.missing_ids += 1
                continue
            vacancy_id = str(vacancy_id)
            if vacancy_id in self.seen:
                self.duplicates += 1
                continue
            self.seen.add(vacancy_id)
            roles = {str(r["id"]): safe_label(r.get("name", r["id"]))
                     for r in (item.get("professional_roles") or [])
                     if isinstance(r, dict) and r.get("id") is not None}
            if not roles:
                self.weights["other"] += Fraction(1)
            else:
                self.multi_role += len(roles) > 1
                for role_id, name in roles.items():
                    self.weights[role_id] += Fraction(1, len(roles))
                    self.names[role_id] = name

    def report(self):
        total = len(self.seen)
        return {"unique_vacancies": total, "duplicate_ids_skipped": self.duplicates,
                "records_without_id_skipped": self.missing_ids, "multi_role_vacancies": self.multi_role,
                "roles": [{"id": role_id, "name": self.names[role_id], "weight": float(weight),
                           "share": float(weight / total) if total else None}
                          for role_id, weight in sorted(self.weights.items(), key=lambda pair: (-pair[1], pair[0]))],
                "interpretation": "Одна вакансия имеет суммарный вес 1; вес делится между её ролями. other означает отсутствие роли в API, не всех работников вне hh."}


def collect(client, area="113", sample_pages=0, pause=0.5):
    base = {"area": area, "host": "hh.ru", "locale": "RU", "order_by": "publication_time"}
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "area": area,
              "source": API + "/vacancies", "sampling": "active_vacancies_at_request_time",
              "status": "failed", "clusters": None, "sample": None,
              "limitations": ["Вакансии не равны работникам или населению.",
                              "Счётчики динамические; запросы не образуют атомарного снимка.",
                              "Разные ID могут описывать одно рабочее место; такие дубли не разрешаются."]}
    accumulator = SampleAccumulator()
    completed_pages = 0
    stage = "clusters"
    try:
        data = client.get("/vacancies", dict(base, per_page=0, clusters="true"))
        report["clusters"] = cluster_statistics(data)
        report["status"] = "ok" if report["clusters"]["professional_role_cluster_present"] else "role_cluster_missing"
        if sample_pages:
            stage = "sample"
            found = report["clusters"]["found"]
            target_pages = min(sample_pages, math.ceil(found / 100))
            for page in range(target_pages):
                time.sleep(pause)
                data = client.get("/vacancies", dict(base, per_page=100, page=page))
                if not isinstance(data.get("items"), list):
                    raise ApiFailure("invalid_items_in_response")
                accumulator.add(data["items"])
                completed_pages += 1
                if not data["items"]:
                    break
            report["sample"] = accumulator.report()
            report["sample"].update(pages_completed=completed_pages,
                                     truncated=accumulator.report()["unique_vacancies"] < found,
                                     sampling="latest_publications_not_random_sample")
    except ApiFailure as error:
        report["status"] = "failed" if stage == "clusters" else "partial"
        report["error"] = dict(error.public(), stage=stage)
        if stage == "sample":
            report["sample"] = dict(accumulator.report(), pages_completed=completed_pages,
                                     truncated=True, sampling="latest_publications_not_random_sample")
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    return report


def print_report(report):
    print("HH_REPORT_BEGIN")
    # По одной строке на роль: весь блок удобно скопировать из консоли.
    header = {k: v for k, v in report.items() if k not in {"clusters", "sample"}}
    print(json.dumps(header, ensure_ascii=False))
    for section in ("clusters", "sample"):
        if report[section] is not None:
            print(json.dumps({"section": section, **{k: v for k, v in report[section].items() if k != "roles"}}, ensure_ascii=False))
            for row in report[section]["roles"]:
                print(json.dumps({"section": section, "role": row}, ensure_ascii=False))
    print("HH_REPORT_END")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", default="113", help="Регион hh; по умолчанию Россия")
    parser.add_argument("--sample-pages", type=int, default=0, help="Дополнительно разобрать страницы вакансий; 0–20")
    parser.add_argument("--pause", type=float, default=0.5)
    parser.add_argument("--output", type=Path, default=Path("data/reference/hh-occupation-statistics.json"))
    args = parser.parse_args()
    if not 0 <= args.sample_pages <= 20 or not math.isfinite(args.pause) or args.pause < 0:
        parser.error("Нужны 0–20 страниц и конечная неотрицательная пауза")
    try:
        report = collect(Client(credentials()), area=args.area, sample_pages=args.sample_pages, pause=args.pause)
    except OSError:
        report = {"status": "failed", "error": {"category": "credentials_file_unreadable"},
                  "clusters": None, "sample": None}
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        report["output_error"] = "cannot_write_aggregate_file"
    print_report(report)
    if report.get("error", {}).get("http_status") in {401, 403}:
        print("Доступ hh не подтверждён: возможны ограничения API, авторизации или капча. "
              "API для соискателей прекращён; токен соискателя не является решением. "
              "Пришлите блок HH_REPORT. Для дальнейшего сбора нужен подтверждённый доступ "
              "приложения к этому методу либо другой источник агрегатов.")
    return 0 if report["status"] == "ok" and not report.get("output_error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
