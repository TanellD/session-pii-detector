"""Проверить предоставленный сборник Росстата и извлечь таблицу занятий по полу."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from pypdf import PdfReader


SOURCE_SHA256 = "dbf9fcfdccbbf23afd4b91d8969745de7a4cd95884ca9a40c3f3d4d24b1bea51"


def parse_table(text, pdf_page, preceding_rows):
    rows = []
    pending = []
    for line in text.splitlines():
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) == 9 and all(re.fullmatch(r"[\d ,]+", part) for part in parts[1:]):
            label = " ".join(pending + [parts[0]])
            pending = []
            counts = [int(value.replace(" ", "")) * 1000 for value in parts[1:4]]
            percentages = [float(value.replace(",", ".")) for value in parts[4:]]
            rows.append({"id": f"table_2_40_row_{preceding_rows + len(rows) + 1:02d}",
                         "label": label, "all": counts[0], "male": counts[1], "female": counts[2],
                         "published_percent_occupation_among_employed": dict(zip(("all", "male", "female"), percentages[:3])),
                         "published_percent_sex_within_occupation": dict(zip(("male", "female"), percentages[3:])),
                         "pdf_page": pdf_page, "printed_page": pdf_page - 1})
        elif line.strip() and re.search(r"[а-яА-Я]", line) and len(parts) == 1:
            # Только строки названия в левой колонке; заголовки и нижние колонтитулы исключены.
            indent = len(line) - len(line.lstrip())
            if indent <= 6 and not re.search(r"\d|ЗАНЯТОСТЬ|РАБОЧАЯ СИЛА", line):
                pending.append(line.strip())
    return rows


def extract(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("Этот разбор проверен только для предоставленного PDF; другой файл требует проверки структуры")
    reader = PdfReader(path)
    rows = []
    for page in (76, 77):
        rows.extend(parse_table(reader.pages[page - 1].extract_text(extraction_mode="layout"), page, len(rows)))
    if not rows or rows[0]["label"] != "Занятые – всего":
        raise ValueError("Не распознан итог таблицы")
    total = rows[0]
    for row in rows:
        if abs(row["male"] + row["female"] - row["all"]) > 1000:
            raise ValueError("Расхождение полов превышает допустимое для опубликованного округления")
        row["share_among_employed"] = {sex: row[sex] / total[sex] for sex in ("all", "male", "female")}
    return {"publication_year": 2024, "reference_year": 2023, "sha256": digest,
            "source_file": str(path), "source_table": "2.40", "pdf_pages": len(reader.pages),
            "source_unit": "thousand_persons_rounded_to_integer", "output_unit": "persons_rounded_to_thousand",
            "coverage": "Россия, в среднем за год; без ДНР, ЛНР, Запорожской и Херсонской областей; обследование рабочей силы",
            "rows": rows,
            "limitations": ["Строки включают итоги и вложенные группы: их нельзя суммировать вместе.",
                            "Суммы округлённых полов могут отличаться от округлённого итога.",
                            "Коды ОКЗ для текстовых должностей ещё не сопоставлены; ID строк не являются кодами ОКЗ.",
                            "Проверенный исторический срез; статус последней доступной статистики не подтверждён."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/reference/rosstat-occupations-2023.json"))
    parser.add_argument("--table", choices=("2.39", "2.40"))
    args = parser.parse_args()
    if args.input is None:
        matches = list(Path("docs/project/drafts").glob("*(2024).pdf"))
        if len(matches) != 1:
            parser.error("Укажите --input: требуется единственный проверяемый PDF")
        args.input = matches[0]
    result = extract(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False, indent=2))
    print(json.dumps({"extracted_rows_including_totals": len(result["rows"]), "total": result["rows"][0],
                      "reference": str(args.output)}, ensure_ascii=False, indent=2))
    if args.table:
        reader = PdfReader(args.input)
        pages = (75,) if args.table == "2.39" else (76, 77)
        for page in pages:
            print(f"PDF page {page}")
            print(reader.pages[page - 1].extract_text(extraction_mode="layout"))


if __name__ == "__main__":
    main()
