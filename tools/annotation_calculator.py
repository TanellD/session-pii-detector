"""Локальный калькулятор оснований разметки: python tools/annotation_calculator.py."""

import argparse
from collections import defaultdict
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from estimate_occupation_candidates import load_reference, normalized


ROOT = Path(__file__).resolve().parents[1]
RED = {"passport", "snils", "individual_inn", "bank_card"}
TYPES = RED | {"phone", "email", "fio", "first_name", "last_name", "patronymic", "sex", "age", "occupation", "organization", "geography", "address", "relationship"}


def normalize_state(facts):
    """История не меняется; позднее значение заменяет тот же смысловой атрибут."""
    active = {}
    for fact in facts:
        if fact["type"] == "fio" and fact.get("complete", True) and len(fact.get("value", "").split()) == 3:
            # Разбираем в рабочем состоянии: поздняя поправка одной части должна
            # обновлять ФИО, не переписывая исходное упоминание в истории.
            active = {key: value for key, value in active.items()
                      if key[0] not in {"fio", "last_name", "first_name", "patronymic"}}
            for part, value in zip(("last_name", "first_name", "patronymic"), fact["value"].split()):
                active[(part, "default")] = {**fact, "type": part, "value": value}
            continue
        key = (fact["type"], fact.get("role", "default"))
        active[key] = fact
    parts = [next((f for (kind, _), f in active.items() if kind == part), None)
             for part in ("last_name", "first_name", "patronymic")]
    if any(kind == "fio" for kind, _ in active) or (all(parts) and all(p.get("complete", True) and p.get("value", "").strip() for p in parts)):
        if not any(kind == "fio" for kind, _ in active):
            active[("fio", "default")] = {"type": "fio", "value": " ".join(p["value"] for p in parts),
                                            "person_id": parts[0]["person_id"], "uncertain": any(p.get("uncertain") for p in parts)}
        active = {key: value for key, value in active.items() if key[0] not in {"last_name", "first_name", "patronymic"}}
    return list(active.values())


class Calculator:
    def __init__(self, policy_only=False):
        self.policy_only = policy_only
        self.policy = json.loads((ROOT / "configs/annotation-estimator-policy.json").read_text(encoding="utf-8"))
        self.occupations = None if policy_only else load_reference()
        self.geo = {"rows": []} if policy_only else json.loads((ROOT / "data/reference/annotation-geography-vpn2020.json").read_text(encoding="utf-8"))
        self.geo_by_id = {row["id"]: row for row in self.geo["rows"]}
        self.housing = {"rows": []} if policy_only else json.loads((ROOT / "data/reference/housing-occupancy-vpn2020.json").read_text(encoding="utf-8"))
        defaults = self.policy.get("housing_defaults", {})
        for kind, unit in defaults.get("units", {}).items():
            if not any(r["region"] == defaults["region"] and r["settlement"] == "all" and r["housing_type"] == kind
                       for r in self.housing["rows"]):
                self.housing["rows"].append({"region": defaults["region"], "settlement": "all", "housing_type": kind,
                    "mean_residents_per_occupied_unit": unit["mean"], "source_url": defaults["source_url"],
                    "reference_date": defaults["reference_date"], "source_sha256": defaults["source_sha256"]})

    def search_geography(self, query):
        query = normalized(query)
        if len(query) < 2:
            return []
        return [row for row in self.geo["rows"] if query in normalized(row["label"]) or query in normalized(row["region"])][:30]

    def within_threshold(self, count):
        if count is None or count <= 0:
            return False
        threshold = self.policy["candidate_threshold"]
        return count < threshold if self.policy.get("candidate_comparison") == "lt" else count <= threshold

    def person(self, facts):
        active = normalize_state(facts)
        by_type = defaultdict(list)
        for fact in active:
            by_type[fact["type"]].append(fact)
        reasons = []
        unresolved = []
        estimates = []
        if by_type["age"]:
            unresolved.append("Возраст выделен; проверенный совместный возрастной справочник ещё не подключён")
        rule_codes = []
        address_estimate_count = None
        direct = any(f.get("complete") for kind in RED for f in by_type[kind])
        contact_pair = all(any(f.get("complete") and f.get("usage") == "personal" for f in by_type[kind])
                           for kind in ("phone", "email"))
        if direct:
            reasons.append("Полный красный маркер")
            rule_codes.append("red_rule")
        if contact_pair:
            reasons.append("Полный личный телефон и email одного человека")
            rule_codes.append("phone_email_rule")
        sex_facts = by_type["sex"]
        sex = sex_facts[-1]["value"] if sex_facts else "all"
        if sex not in {"male", "female", "all"}:
            raise ValueError("Для пола используйте male, female либо all")
        occupation = by_type["occupation"]
        organization = by_type["organization"]
        occupation_result = None
        if len(occupation) > 1 or len(organization) > 1:
            unresolved.append("Несколько профессиональных ролей/работодателей: совместная оценка не определена")
        elif occupation:
            fact = occupation[0]
            occupation_result = self.occupations.estimate(fact.get("value"), fact.get("okz"), sex) if self.occupations else {
                "status": "reference_unavailable_policy_only", "candidate_count": None}
            if occupation_result["candidate_count"] is None:
                unresolved.append("Должность не сопоставлена однозначно с ОКЗ")
            else:
                count = occupation_result["candidate_count"]
                method = "Численность профессиональной группы с учётом пола; Росстат, 2023"
                if organization:
                    size = self.policy["employer_size"]["value"]
                    count = 1 + (size - 1) * occupation_result["joint_share_among_all_employed"]
                    method = "Приближение состава работодателя: 1 + (s − 1) × q; занятость 2023, численность ФНС 2025"
                estimates.append({"count": count, "method": method, "source": occupation_result})
        elif organization:
            estimates.append({"count": self.policy["employer_size"]["value"], "method": "Выбранное взвешенное среднее работодателя; ФНС, 2025"})
            if sex != "all":
                unresolved.append("Совместное правило работодателя и пола без профессии ещё не выбрано")
        geo_facts = by_type["geography"]
        if len(geo_facts) > 1:
            unresolved.append("Несколько смысловых территорий: выберите территорию проживания, не перемножая места")
        elif geo_facts:
            geo_fact = geo_facts[0]
            geo = self.geo_by_id.get(geo_fact.get("geography_id"))
            if geo_facts[0].get("role", "default") not in {"default", "residence", "проживание"}:
                unresolved.append("Эта территория не подтверждена как проживание; счётчик жителей не применён")
            elif (geo is None and geo_fact.get("specific_settlement_under_5000_confirmed") is True
                  and isinstance(geo_fact.get("population_bound_source"), str)
                  and geo_fact["population_bound_source"].strip() and geo_fact.get("value", "").strip()):
                estimates.append({"count": self.policy["small_settlement_fallback"]["value"],
                                  "method": "Принятое приближение: 5 000 для конкретного населённого пункта с подтверждённой численностью не более 5 000; пол дополнительно не уменьшает оценку",
                                  "source": {"population_bound_source": geo_fact["population_bound_source"],
                                             "settlement": geo_fact["value"]},
                                  "approximation": True})
            elif geo is None or geo[sex] is None:
                unresolved.append("Нужно выбрать опубликованную территорию; отсутствующая строка не означает ноль")
            else:
                estimates.append({"count": geo[sex], "method": "Прямой счётчик территории и пола; ВПН-2020", "source": geo})
        elif not occupation and not organization and sex_facts and not by_type["address"]:
            country = next((row for row in self.geo["rows"] if row["kind"] == "country"), None)
            if country is None:
                unresolved.append("Статистика пола не подключена")
            else:
                estimates.append({"count": country[sex], "method": "Численность пола; ВПН-2020"})
        if by_type["address"]:
            if len(by_type["address"]) == 1:
                address = by_type["address"][0]
                housing_type = address.get("housing_type")
                whole_building = housing_type == "apartment_building"
                building_conflicts_with_apartment = whole_building and address.get("resolved_level") == "apartment"
                unit_type = "apartment" if whole_building else housing_type
                apartment_count = address.get("house_flat_count")
                valid_apartment_count = isinstance(apartment_count, int) and not isinstance(apartment_count, bool) and apartment_count > 0
                complete = address.get("completeness") == "full"
                residence = address.get("role", "default") in {"default", "residence", "проживание"}
                # Тип жилья подтверждает разметчик, а не слово «дом» из адресного справочника.
                address_region = address.get("reference_region", "Российская Федерация")
                if len(geo_facts) == 1 and geo_facts[0].get("role", "default") in {"default", "residence", "проживание"}:
                    selected_geo = self.geo_by_id.get(geo_facts[0].get("geography_id"))
                    if selected_geo:
                        address_region = selected_geo["region"]
                candidates = [r for r in self.housing["rows"] if r["housing_type"] == unit_type
                              and r["region"] == address_region and r["settlement"] == "all"]
                if not candidates and address_region != "Российская Федерация":
                    candidates = [r for r in self.housing["rows"] if r["housing_type"] == unit_type
                                  and r["region"] == "Российская Федерация" and r["settlement"] == "all"]
                if building_conflicts_with_apartment:
                    unresolved.append("Адрес указывает квартиру: выберите подтверждённый тип квартиры, а не дом целиком")
                elif whole_building and not valid_apartment_count:
                    unresolved.append("Для всего многоквартирного дома нужно положительное целое число квартир; отсутствующее значение не означает ноль")
                elif complete and residence and len(candidates) == 1:
                    multiplier = apartment_count if whole_building else 1
                    method = "Приближение: среднее жителей занятого помещения подтверждённого типа; ВПН-2020, " + candidates[0]["region"]
                    assumption = None
                    if whole_building:
                        method = "Приближение всего дома: число квартир × среднее жителей занятой квартиры; ВПН-2020, " + candidates[0]["region"]
                        assumption = "Все квартиры предполагаются занятыми; реальная заселённость не измерена"
                    estimates.append({"count": multiplier * candidates[0]["mean_residents_per_occupied_unit"],
                                      "method": method, "source": candidates[0],
                                      "house_flat_count": apartment_count if whole_building else None,
                                      "occupancy_assumption": assumption})
                    address_estimate_count = estimates[-1]["count"]
                else:
                    unresolved.append("Для адреса не подтверждены полнота, проживание или тип жилой единицы; многоквартирный дом не равен квартире")
            else:
                unresolved.append("Несколько адресов: совместная оценка не определена")
        if len(estimates) > 1:
            unresolved.append("Совместная статистика территории и профессии/работодателя отсутствует; доли не перемножены")
            candidate_count = None
        else:
            candidate_count = estimates[0]["count"] if estimates else None
        for kind in ("fio", "first_name", "last_name", "patronymic"):
            if by_type[kind]:
                unresolved.append("Частоты частей ФИО разрешены только для исследования, не для основной разметки")
                break
        if any(not f.get("complete") for kind in RED for f in by_type[kind]):
            unresolved.append("Неполный документ не является красным маркером")
        incomplete_contacts = any(not f.get("complete") or f.get("usage") != "personal"
                                  for kind in ("phone", "email") for f in by_type[kind])
        if incomplete_contacts:
            unresolved.append("Неполный, общий или неподтверждённый личный контакт: отдельное правило не установлено")
        # Уже достаточное сочетание не перестаёт срабатывать после добавления другого атрибута.
        # При этом отдельные счётчики не выдаются за совместное число кандидатов.
        numerical = any(self.within_threshold(item["count"]) for item in estimates)
        name_policy = self.policy.get("partial_name_plus_attribute", {})
        complete_part_types = {kind for kind in ("first_name", "last_name", "patronymic")
                               if any(f.get("complete", True) and f.get("value", "").strip() for f in by_type[kind])}
        has_name_pair = (len(complete_part_types) >= name_policy.get("minimum_distinct_complete_name_parts", 2)
                         or any(f.get("complete", True) and f.get("value", "").strip() for f in by_type["fio"]))
        active_name_marker = None
        if any(f.get("complete", True) and f.get("value", "").strip() for f in by_type["fio"]):
            active_name_marker = {"type": "fio", "zone": "orange"}
        else:
            for marker, components in name_policy.get("combinations", {}).items():
                if set(components) <= complete_part_types:
                    active_name_marker = {"type": marker, "zone": name_policy["zone"]}
                    break
        allowed = set(name_policy.get("additional_attributes", []))
        qualifying = []
        for kind in ("phone", "email"):
            if "complete_personal_" + kind in allowed and any(
                    f.get("complete") and f.get("usage") == "personal" and f.get("value", "").strip()
                    for f in by_type[kind]):
                qualifying.append(kind)
        if "exact_employer" in allowed and any(
                f.get("exact") is True and f.get("value", "").strip()
                and f.get("role", "default") in {"default", "employer", "work", "работа", "работодатель"}
                for f in organization):
            qualifying.append("organization")
        if ("full_residential_address_with_estimate_within_threshold" in allowed
                and address_estimate_count is not None
                and self.within_threshold(address_estimate_count)):
            qualifying.append("address")
        if "full_residential_address" in allowed and any(
                f.get("completeness") == "full" and f.get("value", "").strip()
                and f.get("role", "default") in {"default", "residence", "проживание"}
                for f in by_type["address"]):
            qualifying.append("address")
        name_attribute_alert = bool(name_policy.get("enabled") and has_name_pair and qualifying)
        if name_attribute_alert:
            rule_codes.append("partial_name_plus_attribute_policy")
            reasons.append("Не менее двух полных частей имени и дополнительный личный атрибут: " + ", ".join(qualifying))
            if not numerical:
                candidate_count = None  # Отдельное правило не создаёт численную оценку сочетания.
        if candidate_count is not None and candidate_count <= 0:
            unresolved.append("Нулевая оценка требует проверки, а не алерта")
        address_only = bool(by_type["address"]) and set(f["type"] for f in active) <= {"address", "geography", "relationship"}
        if numerical and not address_only:
            rule_codes.append("candidate_count_rule")
            reasons.append("Положительная точечная оценка " + ("меньше" if self.policy.get("candidate_comparison") == "lt" else "не более") + " выбранного порога")
        if direct or contact_pair or name_attribute_alert:
            alert = True
        elif address_only:
            alert = False
            reasons.append("Адрес без других личных признаков не вызывает алерт")
        elif numerical:
            alert = True
        elif unresolved:
            alert = None
        else:
            alert = False
        return {"candidate_count": candidate_count, "alert": alert, "reasons": reasons,
                "rule_codes": rule_codes,
                "active_name_marker": active_name_marker,
                "alert_reason": "red_rule" if direct else "combination_rule" if contact_pair or name_attribute_alert else "candidate_count_rule" if alert and numerical else None,
                "status": "policy_only" if name_attribute_alert and not numerical else "insufficient_data" if unresolved else "estimated" if estimates else "policy_only",
                "unresolved": list(dict.fromkeys(unresolved)), "estimates": estimates,
                "occupation": occupation_result, "active_attribute_types": sorted(set(f["type"] for f in active)),
                "uncertain_attributes_counted": sum(bool(f.get("uncertain")) for f in active)}

    def session(self, facts):
        if not isinstance(facts, list) or len(facts) > 200:
            raise ValueError("Нужен список максимум из 200 атрибутов")
        people = defaultdict(list)
        first_alert = None
        first_alert_reason = None
        unassigned = 0
        attribute_history = []
        for index, fact in enumerate(facts, 1):
            if not isinstance(fact, dict) or fact.get("type") not in TYPES or not isinstance(fact.get("value", ""), str):
                raise ValueError("Некорректный атрибут")
            if len(fact.get("value", "")) > 2000:
                raise ValueError("Слишком длинное значение")
            for key in ("complete", "uncertain", "exact"):
                if key in fact and not isinstance(fact[key], bool):
                    raise ValueError("Флаги должны быть логическими значениями")
            person_id = fact.get("person_id")
            if person_id is not None and (not isinstance(person_id, str) or not person_id.strip()):
                raise ValueError("Нужен непустой идентификатор человека либо null")
            if person_id is None:
                unassigned += 1
                triggered = fact["type"] in RED and fact.get("complete")
                reason = "red_rule" if triggered else None
                pending = False
            else:
                people[person_id].append(fact)
                assessment = self.person(people[person_id])
                triggered = assessment["alert"] is True
                reason = assessment["alert_reason"]
                pending = assessment["alert"] is None
            attribute_history.append({"attribute_index": index, "alert": bool(triggered),
                                      "annotation_uncertain": pending})
            if triggered and first_alert is None:
                first_alert = index
                first_alert_reason = reason
        results = {person_id: self.person(rows) for person_id, rows in people.items()}
        session_alert = first_alert is not None
        needs_clarification = any(step["annotation_uncertain"] for step in attribute_history)
        return {"people": results, "first_alert_attribute": first_alert, "session_alert": session_alert,
                "first_alert_reason": first_alert_reason,
                "unassigned_attributes": unassigned, "history_unchanged": True,
                "annotation_uncertain": needs_clarification,
                "clarification_reason": "insufficient_statistics" if needs_clarification else None,
                "attribute_history": attribute_history,
                "notice": "Результат относится к последовательности ручного ввода атрибутов; индекс сообщения диалога не вычисляется."}


def address_suggestions(query, count=5):
    """Сеть только по явной кнопке поиска. Никаких журналов/сохранения запросов."""
    key = None
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "DADATA_API_KEY":
                key = value.strip().strip("\"'")
    if not key:
        return {"status": "configuration_required", "suggestions": [],
                "message": "Добавьте DADATA_API_KEY в локальный .env. Ключ не отправляется в браузер или журнал."}
    url = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"
    if not isinstance(query, str) or not 2 <= len(query) <= 300 or count not in {1, 5}:
        raise ValueError("Нужна строка адреса от 2 до 300 символов")
    payload = json.dumps({"query": query, "count": count}).encode("utf-8")
    try:
        request = Request(url, data=payload, headers={"Authorization": "Token " + key,
                          "Content-Type": "application/json", "Accept": "application/json"})
        with urlopen(request, timeout=15) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return {"status": "provider_error", "suggestions": []}
        data = json.loads(raw)
        selected = []
        for item in data.get("suggestions", []):
            fields = item.get("data") or {}
            selected.append({"value": item["value"], "unrestricted_value": item.get("unrestricted_value", item["value"]),
                             "gar_id": fields.get("fias_id"), "house_fias_id": fields.get("house_fias_id"),
                             "country_iso_code": fields.get("country_iso_code"),
                             "flat_type": fields.get("flat_type"), "has_flat": bool(fields.get("flat")),
                             "has_room": bool(fields.get("room")) or fields.get("flat_type") in {"ком", "комн", "комната"},
                             "completeness": "full" if fields.get("house") else "partial",
                             "resolved_level": "apartment" if fields.get("flat") else "house" if fields.get("house") else "territory",
                             "region": fields.get("region_with_type"), "settlement": fields.get("settlement_with_type") or fields.get("city_with_type"),
                             "house_flat_count": int(str(fields["house_flat_count"]))
                             if str(fields.get("house_flat_count", "")).isdigit() and int(str(fields["house_flat_count"])) > 0 else None})
        return {"status": "selection_required", "suggestions": selected,
                "message": "Выберите подходящую подсказку. Поиск не сообщает число жителей дома."}
    except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
        return {"status": "provider_error", "suggestions": [], "message": "Не удалось получить подсказки; запрос и ключ не сохранены."}


def serve(policy_only=False, port=8765):
    calculator = Calculator(policy_only=policy_only)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, status=200, content_type="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.reply((ROOT / "tools/annotation_calculator.html").read_bytes(), content_type="text/html; charset=utf-8")
            elif parsed.path == "/api/geography":
                query = parse_qs(parsed.query).get("q", [""])[0]
                self.reply({"rows": calculator.search_geography(query), "source": "ВПН-2020"})
            elif parsed.path == "/api/context":
                self.reply({"schema_version": calculator.policy["annotation_schema"],
                            "candidate_threshold": calculator.policy["candidate_threshold"],
                            "reference_mode": "policy_only" if calculator.policy_only else "local_references",
                            "prepared_means_connected": False,
                            "employer_size": calculator.policy["employer_size"]["value"],
                            "housing_defaults": calculator.policy.get("housing_defaults", {})})
            else:
                self.reply({"error": "not_found"}, 404)

        def do_POST(self):
            # Только запросы локального интерфейса с JSON; чужой сайт не может молча передать форму.
            if self.headers.get("Origin") not in {None, f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
                self.reply({"error": "origin_not_allowed"}, 403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 256 * 1024 or not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ValueError("Нужен JSON допустимого размера")
                data = json.loads(self.rfile.read(length))
                if self.path == "/api/estimate":
                    self.reply(calculator.session(data["facts"]))
                elif self.path in {"/api/annotation", "/api/annotation/enrich"}:
                    from annotation_contract import assess
                    from annotation_address_enrichment import build_enrichment
                    document = data.get("annotation", data)
                    enrichment = data.get("address_enrichment")
                    if self.path == "/api/annotation/enrich":
                        enrichment = build_enrichment(document, calculator)
                    result = assess(document, calculator, address_enrichment=enrichment)
                    if enrichment is not None:
                        result["address_enrichment"] = enrichment
                    self.reply(result)
                elif self.path == "/api/address/estimate":
                    from estimate_address_population import estimate_from_query
                    self.reply(estimate_from_query(calculator, data["query"],
                        lambda value, count: address_suggestions(value, count), selected=data.get("selected", False),
                        expected_house_id=data.get("expected_house_id")))
                elif self.path == "/api/address":
                    query = data["query"]
                    if not isinstance(query, str) or not 2 <= len(query) <= 500:
                        raise ValueError("Некорректный запрос адреса")
                    self.reply(address_suggestions(query))
                else:
                    self.reply({"error": "not_found"}, 404)
            except (ValueError, KeyError, TypeError):
                self.reply({"error": "invalid_input", "message": "Проверьте структуру атрибутов и выбранные значения."}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Калькулятор: http://127.0.0.1:{server.server_port} — Ctrl+C для остановки. Данные сессии не сохраняются.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-only", action="store_true", help="Работать без локальных файлов справочников")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Порт должен быть от 0 до 65535")
    serve(policy_only=args.policy_only, port=args.port)
