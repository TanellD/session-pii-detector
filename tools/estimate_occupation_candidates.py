"""Оценка по профессиональной группе и полу; неизвестные названия не получают нулевую частоту."""

import argparse
import json
from pathlib import Path
import re


def normalized(value):
    return " ".join(value.casefold().replace("ё", "е").split())


class OccupationReference:
    def __init__(self, reference, mapping):
        if reference["sha256"] != mapping["source_sha256"]:
            raise ValueError("Справочник не совпадает с версией сопоставления")
        self.reference = reference
        self.mapping = mapping
        self.by_code = {mapping["row_to_okz"][row["id"].rsplit("_", 1)[1]]: row
                        for row in reference["rows"][1:]}
        if len(self.by_code) != len(reference["rows"]) - 1:
            raise ValueError("Отсутствует однозначное сопоставление строк")

    def estimate(self, occupation=None, code=None, sex="all"):
        if sex not in {"all", "male", "female"}:
            raise ValueError("Неизвестное значение пола")
        result = {"status": "unmapped", "candidate_count": None,
                  "reference_year": self.reference["reference_year"], "source_table": "2.40",
                  "source_sha256": self.reference["sha256"], "sex": sex, "age_used": False,
                  "assumptions": ["Более узкая должность представлена частотой всей опубликованной группы.",
                                  "Исторические среднегодовые выборочные оценки, округлённые до тысяч человек."]}
        if code is not None:
            if not isinstance(code, str):
                raise ValueError("Код ОКЗ должен быть строкой")
            code = code.strip()
            if not re.fullmatch(r"[0-9]{1,4}", code):
                raise ValueError("Нужен код ОКЗ без контрольного числа")
            codes = [code] if code in self.by_code else []
            result["mapping_method"] = "explicit_published_okz_group"
        else:
            codes = self.mapping["aliases"].get(normalized(occupation or ""), [])
            result["mapping_method"] = "exact_reviewable_alias"
        result["possible_codes"] = codes
        if not codes:
            result["reason"] = "Нужен выбор группы ОКЗ; неизвестная должность не считается редкой"
            return result
        if len(codes) > 1:
            result["status"] = "ambiguous"
            result["clarification"] = self.mapping.get("ambiguity_questions", {}).get(
                normalized(occupation or ""), "Уточните обязанности и квалификацию, затем выберите группу ОКЗ.")
            result["options"] = [{"code": c, "label": self.by_code[c]["label"]} for c in codes]
            return result
        row = self.by_code[codes[0]]
        total = self.reference["rows"][0]
        result.update(status="estimated_group", okz_group=codes[0], group_label=row["label"],
                      candidate_count=row[sex], share_among_employed=row[sex] / total[sex],
                      joint_share_among_all_employed=row[sex] / total["all"],
                      source_pdf_page=row["pdf_page"], source_row_id=row["id"])
        return result


def load_reference():
    return OccupationReference(
        json.loads(Path("data/reference/rosstat-occupations-2023.json").read_text(encoding="utf-8")),
        json.loads(Path("configs/occupation-okz-mapping.json").read_text(encoding="utf-8")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--occupation")
    group.add_argument("--list-mapping", action="store_true", help="Показать словарь соответствий и его охват без загрузки статистики")
    group.add_argument("--okz", help="Код опубликованной группы, например 25; подробный классификатор не загружен")
    parser.add_argument("--sex", choices=("all", "male", "female"), default="all")
    args = parser.parse_args()
    if args.list_mapping:
        mapping = json.loads(Path("configs/occupation-okz-mapping.json").read_text(encoding="utf-8"))
        aliases = mapping["aliases"]
        print(json.dumps({"status": mapping["aliases_status"], "scope": mapping["mapping_scope"],
                          "alias_count": len(aliases),
                          "unambiguous_alias_count": sum(len(codes) == 1 for codes in aliases.values()),
                          "ambiguous_alias_count": sum(len(codes) > 1 for codes in aliases.values()),
                          "sources": mapping["mapping_sources"], "aliases": aliases}, ensure_ascii=False, indent=2))
        return
    result = load_reference().estimate(args.occupation, args.okz, args.sex)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
