"""Ограниченный пилот по публичному списку выпускников: только агрегаты, без записи ФИО.

python -X utf8 tools/pilot_public_fullname_statistics.py --download
Без --download показывает ранее сохранённую обезличенную сводку.
"""

from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import random
import re
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from compare_name_frequency_models import joint_diagnostics


SOURCE = "https://sa.cs.msu.ru/node/466"
AGENT = "SessionPiiResearch/0.1"
OUTPUT = Path("data/reference/public-fullname-pilot-2026-11-16.json")


class BodyText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag not in {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}:
                self.depth += 1
            if tag in {"br", "p", "li", "tr", "div"}:
                self.parts.append("\n")
        elif "field-name-body" in dict(attrs).get("class", "").split():
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth and tag not in {"br", "img", "hr", "input", "meta", "link", "source", "wbr"}:
            self.depth -= 1
            if tag in {"p", "li", "tr", "div"}:
                self.parts.append("\n")

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


def parse_candidates(html):
    parser = BodyText()
    parser.feed(html)
    word = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
    # Узкий проверяемый критерий; не универсальный NER. Не угадываем отсутствующее отчество.
    full = re.compile(rf"^(?:\d+[.)]?\s+)?({word})\s+({word})\s+({word})(?:\s*[,;.]?\s*)$")
    candidates = []
    rejected = 0
    for raw in "".join(parser.parts).splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        match = full.fullmatch(line)
        if match and match[3].casefold().endswith(("ович", "евич", "овна", "евна", "ична", "ич")):
            candidates.append(tuple(part.casefold() for part in match.groups()))
        else:
            rejected += 1
    return candidates, rejected


def anonymous_diagnostics(triples):
    mappings = [{value: f"{prefix}{i+1}" for i, value in enumerate(sorted({r[j] for r in triples}))}
                for j, prefix in enumerate(("F", "I", "O"))]
    counts = Counter(triples)
    rows = [{"surname": mappings[0][key[0]], "name": mappings[1][key[1]],
             "patronymic": mappings[2][key[2]], "count": count} for key, count in counts.items()]
    return joint_diagnostics(rows)


def summarize(triples, rejected=0, permutations=100, seed=42):
    if not triples:
        return {"status": "no_supported_fullname_rows", "rejected_nonempty_lines": rejected}
    # Порядок строк документа не должен менять перестановочный фон при том же seed.
    triples = sorted(triples)
    observed = anonymous_diagnostics(triples)
    generator = random.Random(seed)
    null = []
    for _ in range(permutations):
        columns = [list(values) for values in zip(*triples)]
        for values in columns:
            generator.shuffle(values)
        null.append(anonymous_diagnostics(list(zip(*columns))))
    names = ["total_correlation_bits", "surname_name_mi", "surname_patronymic_mi", "name_patronymic_mi"]

    def values(result):
        return [result["total_correlation_bits"], *[result["pair_mutual_information_bits"][pair]
                                                   for pair in ("0-1", "0-2", "1-2")]]

    permutation_results = {}
    for i, name in enumerate(names):
        actual = values(observed)[i]
        samples = sorted(values(result)[i] for result in null)
        permutation_results[name] = {"observed": actual, "null_mean": sum(samples) / len(samples),
                                     "null_min": samples[0], "null_max": samples[-1],
                                     "permutation_upper_tail_p": (1 + sum(v >= actual - 1e-10 for v in samples)) / (1 + len(samples))}
    return {"status": "pilot_mentions_not_population_counts", "recognized_fullname_rows": len(triples),
            "distinct_written_fullnames": len(set(triples)), "rejected_nonempty_lines": rejected,
            "descriptive": observed, "permutations": permutations, "random_seed": seed,
            "permutation_baseline": permutation_results,
            "limitations": ["Кандидаты распознаны узким правилом записи; персональная валидация не проводилась.",
                            "Считаются строки списка, не независимо подтверждённые уникальные люди.",
                            "Точное совпадение ФИО не используется как доказательство тождества людей.",
                            "Выпускники одной кафедры не представляют население России; место учёбы не равно проживанию.",
                            "Отбор строк с поддержанным отчеством исключает другие формы имён.",
                            "На редких таблицах эмпирические lift и TC сильно зависят от конечного объёма; перестановочный фон обязателен.",
                            "Пилот проверяет процесс и не задаёт коэффициенты основной разметки."]}


def fetch(url, limit):
    with urlopen(Request(url, headers={"User-Agent": AGENT}), timeout=20) as response:
        if response.geturl().split("/")[2] != "sa.cs.msu.ru":
            raise ValueError("Неожиданное перенаправление")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Размер источника превышает лимит")
    return data


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if not args.download:
        print(OUTPUT.read_text(encoding="utf-8"))
        return
    try:
        robots = fetch("https://sa.cs.msu.ru/robots.txt", 200000).decode("utf-8")
        policy = RobotFileParser()
        policy.parse(robots.splitlines())
        if not policy.can_fetch(AGENT, SOURCE):
            raise ValueError("Источник запрещён robots.txt для этого агента")
        raw = fetch(SOURCE, 1000000)
        triples, rejected = parse_candidates(raw.decode("utf-8"))
        result = summarize(triples, rejected)
        result.update(source_url=SOURCE, source_sha256=hashlib.sha256(raw).hexdigest(),
                      source_scope="ВМК МГУ, кафедра системного анализа, выпуск магистратуры 2024",
                      checked_at=datetime.now(timezone.utc).isoformat(),
                      raw_document_saved=False, personal_rows_saved=False,
                      robots_checked=True)
    except (URLError, ValueError, UnicodeError):
        # Не печатаем страницу, запрос с секретами или строки кандидатов при ошибке.
        raise SystemExit("Пилот не выполнен: проверьте доступ и структуру источника; исходные записи не сохранены.")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
