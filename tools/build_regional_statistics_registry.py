"""Собрать реестр региональных ссылок Росстата без сохранения персональных сведений."""

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from openpyxl import load_workbook


DIRECTORY_URL = "https://www.rosstat.gov.ru/territorial"


class DirectoryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href = None
        self.label = []
        self.regions = {}

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href")
            self.label = []

    def handle_data(self, data):
        if self.href:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or not self.href:
            return
        host = urlparse(self.href).hostname or ""
        label = " ".join(" ".join(self.label).split())
        if re.fullmatch(r"\d+\.rosstat\.gov\.ru", host) and ":" in label:
            region = label.split(":", 1)[0]
            self.regions[region] = {"region": region, "site_url": "https://" + host,
                                    "directory_url": DIRECTORY_URL}
        self.href = None


def normalized(value):
    value = value.lower().replace("ё", "е").removesuffix(" - городское население")
    return re.sub(r"[^а-яa-z0-9]", "", value)


def federal_rows(path):
    if not path.exists():
        return {}
    workbook = load_workbook(path, read_only=True, data_only=True)
    result = {}
    try:
        for sheet in workbook:
            for index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                if row[0] and isinstance(row[1], (int, float)):
                    result.setdefault(normalized(str(row[0])), []).append(
                        {"sheet": sheet.title, "row": index, "label": str(row[0])})
    finally:
        workbook.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory-input", type=Path, default=Path("data/external/geography/territorial-regions-2026-11-15.json"))
    parser.add_argument("--refresh-directory", action="store_true")
    parser.add_argument("--catalogs", type=Path, default=Path("configs/rosstat-regional-catalogs.json"))
    parser.add_argument("--population", type=Path, default=Path("data/external/geography/rosstat-vpn2020-table5.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("docs/research/notes/rosstat-all-regions-2026-11-15.md"))
    args = parser.parse_args()
    if args.refresh_directory:
        with urlopen(Request(DIRECTORY_URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=20) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            parser.error("Превышен лимит размера каталога")
        directory_parser = DirectoryParser()
        directory_parser.feed(raw.decode("utf-8"))
        if not directory_parser.regions:
            parser.error("Региональные ссылки не найдены; структура каталога могла измениться")
        snapshot = {"directory_sha256": hashlib.sha256(raw).hexdigest(),
                    "regions": list(directory_parser.regions.values())}
        args.directory_input.parent.mkdir(parents=True, exist_ok=True)
        args.directory_input.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.directory_input.exists():
        parser.error("Нет снимка каталога; используйте --refresh-directory")
    snapshot = json.loads(args.directory_input.read_text(encoding="utf-8"))
    catalogs = {item["provider_url"]: item for item in json.loads(args.catalogs.read_text(encoding="utf-8"))["catalogs"]}
    regions = snapshot["regions"]
    providers = Counter(item["site_url"] for item in regions)
    populations = federal_rows(args.population)
    kinds = {"census_catalog": "Перепись/итоги", "population_catalog": "Население",
             "municipal_catalog": "Муниципальная статистика", "census_pdf": "Переписной PDF",
             "population_archive": "Архив населения"}
    lines = ["# Реестр источников населения по регионам из каталога Росстата", "",
             "- Дата составления: 2026-11-15.",
             f"- Основание списка и принадлежности сайтов: [официальный каталог]({DIRECTORY_URL}).",
             "- Территориальные названия воспроизведены из каталога источника; его охват не подменяет охват выбранного статистического среза.",
             "- Назначение: поиск статистических оснований для правил разметки первого алерта.", "",
             "## Как читать реестр", "",
             "Официальность сайта подтверждена каталогом. Тематические ссылки найдены в индексируемых официальных публикациях; работоспособность экспорта и содержимое всех региональных файлов не подтверждены. «Не найден» означает пробел этой проверки, а не отсутствие данных вообще.", "",
             "При общем управлении необходимо выбрать внутри каталога именно нужный регион. Не переносить таблицу одного региона на остальные регионы этого управления.", "",
             "Федеральная строка проверяется по точному нормализованному названию в локальном XLSX. Отсутствие совпадения не означает нулевое население. Совпадение не доказывает полноту малых населённых пунктов, наличие возрастного разреза или согласованность знаменателя со всеми территориями каталога.", "",
             "## Региональные маршруты", "",
             "| Регион из каталога | Официальный сайт | Найденная тематическая публикация | Федеральная строка ВПН | Особенность |",
             "|---|---|---|---|---|"]
    found = matched = 0
    for item in regions:
        site = item["site_url"]
        catalog = catalogs.get(site)
        publication = f"[{kinds[catalog['kind']]}]({catalog['publication_url']}) — найдено в индексе" if catalog else "Не найден; начать с официального сайта и БД ПМО"
        found += bool(catalog)
        matches = populations.get(normalized(item["region"]), [])
        matched += bool(matches)
        status = "; ".join(f"{r['sheet']}, строка {r['row']}" for r in matches) if matches else "Нет точного совпадения / файл отсутствует"
        note = "Общее управление: выбрать регион" if providers[site] > 1 else "Региональный орган"
        lines.append(f"| {item['region']} | [{urlparse(site).hostname}]({site}) | {publication} | {status} | {note} |")
    lines += ["", "## Покрытие проверки", "",
              f"Региональных записей: {len(regions)}; сайтов управлений: {len(providers)}; записей с тематической ссылкой: {found}; с точным совпадением в федеральном XLSX: {matched}.", "",
              "Все счётчики и координаты воспроизводятся командой:", "", "```powershell",
              "python -X utf8 tools/build_regional_statistics_registry.py", "```", "",
              "Для повторного получения перечня сайтов:", "", "```powershell",
              "python -X utf8 tools/build_regional_statistics_registry.py --refresh-directory", "```", "",
              "Тематические ссылки и статусы: `configs/rosstat-regional-catalogs.json`. Очищенный снимок перечня сохраняется в `data/external/geography/` вне git. Исходная страница не сохраняется: из неё извлекаются только названия территорий и официальные URL, без персональных контактных сведений.", "",
              "[Федеральный файл и команда его загрузки](russia-geography-population-2026-11-15.md). Для разметки использовать фиксированный статистический срез и сохранять координаты ячеек, фильтры и хеш; не читать живые обновляемые таблицы заново при каждой аннотации.", "",
              "## Какие атрибуты проверять в региональном каталоге", "",
              "Численность конкретного населённого пункта; пол; возрастные группы; готовые совместные разрезы территории, возраста и пола. Перечень этих направлений не означает, что все они опубликованы для каждой деревни. Муниципалитет, сельское поселение и деревня — разные единицы.", "",
              "Для ФИО, жильцов конкретной квартиры, телефона и email появление ссылки на региональный Росстат не закрывает пробел статистики. [Справочник доступов и ограничений для разметки](statistics-sources-for-annotation-2026-11-15.md)."]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"regions": len(regions), "providers": len(providers), "with_publication": found,
                      "with_federal_row": matched, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
