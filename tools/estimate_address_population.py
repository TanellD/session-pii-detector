"""Оценка жителей выбранного адреса по структурированному ответу и жилищным средним."""
import sqlite3
from contextlib import closing
from pathlib import Path


def positive_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def lookup_house(house_id, path):
    path = Path(path)
    if not house_id or not path.exists():
        return {"status": "index_unavailable_or_no_house_id"}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            rows = connection.execute("SELECT house_type, flats, state, region FROM houses WHERE house_id=? LIMIT 2",
                                      (house_id.casefold(),)).fetchall()
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    except sqlite3.Error:
        return {"status": "index_unavailable"}
    if len(rows) != 1:
        return {"status": "ambiguous" if rows else "not_found"}
    kind, flats, state, region = rows[0]
    return {"status": "matched", "house_type": kind, "flats": flats, "state": state,
            "region": region, "source": metadata}


def estimate_resolved(calculator, address, building=None):
    result = {"status": "unknown", "population_estimate": None, "address": address,
              "assumptions": [], "address_alone_alert": False}
    if address.get("completeness") != "full":
        return {**result, "reason": "house_not_identified"}
    if address.get("country_iso_code") not in {None, "RU"}:
        return {**result, "reason": "outside_reference_country"}
    if address.get("has_room"):
        return {**result, "reason": "room_population_not_supported"}
    building = building or {"status": "not_found"}
    if building.get("status") == "ambiguous":
        return {**result, "reason": "ambiguous_building_registry"}
    if building.get("status") == "matched" and building.get("state") != "Эксплуатация":
        return {**result, "reason": "building_not_confirmed_in_operation"}
    provider_flats = positive_int(address.get("house_flat_count"))
    registry_flats = positive_int(building.get("flats"))
    if not address.get("has_flat") and provider_flats and registry_flats and provider_flats != registry_flats:
        return {**result, "reason": "apartment_counts_disagree", "building": building}
    flats = registry_flats or provider_flats
    kind = None
    classification = None
    if address.get("has_flat"):
        if address.get("flat_type") not in {"кв", "квартира"}:
            return {**result, "reason": "unit_type_not_confirmed_as_apartment"}
        kind, classification = "apartment", "explicit_apartment_in_selected_address"
    elif building.get("house_type") in {"Индивидуальный жилой дом", "Индивидуальный"}:
        kind, classification = "individual_house", "explicit_individual_type_in_registry"
    elif building.get("house_type") == "Многоквартирный":
        kind, classification = "apartment_building", "apartment_building_in_registry"
    elif provider_flats and provider_flats >= 2:
        kind, classification = "apartment_building", "multiple_apartments_reported_by_provider"
    if kind is None:
        return {**result, "reason": "housing_type_unknown", "building": building}
    if kind == "apartment_building" and flats is None:
        return {**result, "reason": "apartment_count_unknown", "building": building}
    fact = {"type": "address", "value": address["value"], "complete": True,
            "completeness": "full", "housing_type": kind, "role": "residence",
            "resolved_level": address.get("resolved_level"),
            "reference_region": building.get("region") or address.get("region") or "Российская Федерация"}
    if kind == "apartment_building":
        fact["house_flat_count"] = flats
    assessment = calculator.person([{**fact, "person_id": "address-preview"}])
    estimates = assessment["estimates"]
    if not estimates:
        return {**result, "reason": "housing_reference_unavailable", "building": building}
    estimate = estimates[0]
    assumptions = ["Используется средняя заселённость занятого жилья; проживание и занятость не проверены."]
    if kind == "apartment_building":
        assumptions.append("Все квартиры предполагаются занятыми.")
    return {**result, "status": "estimated", "population_estimate": estimate["count"],
            "housing_type": kind, "classification": classification, "apartment_count": flats,
            "address_fact": fact, "building": building, "estimate_source": estimate,
            "reference_date": calculator.policy["housing_defaults"]["reference_date"],
            "reference_url": calculator.policy["housing_defaults"]["source_url"],
            "assumptions": assumptions, "reason": None}


def estimate_from_query(calculator, query, suggest, selected=False, expected_house_id=None):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 300 or type(selected) is not bool:
        raise ValueError("Нужна строка адреса от 2 до 300 символов и логический флаг выбора")
    def unknown(status, reason, **extra):
        return {"status": status, "reason": reason, "population_estimate": None, **extra}
    initial = suggest(query.strip(), 1 if selected else 5)
    if initial.get("status") != "selection_required":
        return unknown(initial.get("status", "provider_error"), "address_provider_unavailable")
    options = initial.get("suggestions", [])
    if not options:
        return unknown("unknown", "address_not_found")
    if not selected:
        normalized = " ".join(query.split()).casefold()
        exact = [a for a in options if normalized in {" ".join(a[k].split()).casefold()
                 for k in ("value", "unrestricted_value") if a.get(k)}]
        if len(options) == 1:
            choice = options[0]
        elif len(exact) == 1:
            choice = exact[0]
        else:
            return unknown("selection_required", "choose_address", suggestions=options)
        if choice.get("completeness") != "full":
            return unknown("unknown", "house_not_identified", suggestions=options)
        house_id = choice.get("house_fias_id") or (choice.get("gar_id") if choice.get("resolved_level") == "house" else None)
        return estimate_from_query(calculator, choice.get("unrestricted_value", choice["value"]), suggest,
                                   selected=True, expected_house_id=house_id)
    if len(options) != 1:
        return unknown("selection_required", "choose_address", suggestions=options)
    address = options[0]
    house_id = address.get("house_fias_id") or (address.get("gar_id") if address.get("resolved_level") == "house" else None)
    if expected_house_id and expected_house_id != house_id:
        return unknown("unknown", "resolved_house_changed")
    # Импорт здесь позволяет тестировать чистую классификацию без зависимости от сервера.
    from annotation_calculator import ROOT
    index = ROOT / calculator.policy["automatic_address_estimation"]["house_index_path"]
    building = lookup_house(house_id, index)
    return estimate_resolved(calculator, address, building)
