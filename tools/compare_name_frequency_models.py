"""Пересчёт опубликованных агрегатов; реальные ФИО и индивидуальные записи не используются.

По умолчанию воспроизводит арифметику таблиц исследований, не их исходный эксперимент.
--joint-aggregates принимает только обезличенные категории F1/I1/O1 и положительные
целые счётчики; описательные зависимости внутри этой таблицы не доказывают переносимость.
"""

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
from urllib.request import Request, urlopen


PAPER = "https://arxiv.org/html/1811.05361v1"
# Таблица 2, модели II/III/IV; в тексте диапазон 21–100,
# в заголовке таблицы опечатка 20–100. Десятичные числа опубликованы округлёнными.
PUBLISHED = [
    ("1", 1.000, 0.940, 0.897),
    ("2-5", 1.611, 1.842, 1.608),
    ("6-20", 3.061, 4.633, 3.165),
    ("21-100", 5.949, 14.573, 6.639),
    (">100", 12.627, 56.297, 16.925),
]


class PaperTables(HTMLParser):
    """Извлекает только табличные клетки; исходная страница не сохраняется."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def verify_published_source():
    with urlopen(Request(PAPER, headers={"User-Agent": "SessionPiiResearch/0.1"}), timeout=30) as response:
        raw = response.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise ValueError("Страница превышает лимит")
    parser = PaperTables()
    parser.feed(raw.decode("utf-8"))
    verified = {}
    for model, column in (("II", 1), ("III", 2), ("IV", 3)):
        expected = [r[column] for r in PUBLISHED]
        candidates = []
        for row in parser.rows:
            if len(row) >= 7 and row[0] == model:
                try:
                    candidates.append([float(value) for value in row[-5:]])
                except ValueError:
                    pass
        if expected not in candidates:
            raise ValueError("Опубликованная таблица не совпала; проверьте формат и версию")
        verified[model] = expected
    return {"source": PAPER, "source_html_sha256": hashlib.sha256(raw).hexdigest(),
            "verified_models": verified, "raw_html_saved": False}


def binary_association(total, count_a, count_b, joint):
    """Ковариация/phi относятся к индикаторам конкретных событий, не кодам имён."""
    if total <= 0 or min(count_a, count_b, joint) < 0 or max(count_a, count_b) > total:
        raise ValueError("Некорректные агрегаты")
    if not max(0, count_a + count_b - total) <= joint <= min(count_a, count_b):
        raise ValueError("Несогласованная совместная частота")
    pa, pb, pab = count_a / total, count_b / total, joint / total
    independent = pa * pb
    covariance = pab - independent
    denominator = math.sqrt(pa * (1 - pa) * pb * (1 - pb))
    return {"independence_expected_count": total * independent, "observed_count": joint,
            "lift": pab / independent if independent else None,
            "relative_prediction_error": (total * independent - joint) / joint if joint else None,
            "binary_indicator_covariance": covariance,
            "binary_indicator_phi": covariance / denominator if denominator else None}


def published_comparison():
    rows = []
    for band, joint, independent, conditional in PUBLISHED:
        rows.append({"true_count_band": band, "joint_mle_rmse": joint,
                     "independent_rmse": independent, "pairwise_conditional_rmse": conditional,
                     "independent_over_joint_rmse_ratio": independent / joint,
                     "independent_over_conditional_rmse_ratio": independent / conditional,
                     "conditional_rmse_reduction_percent": 100 * (1 - conditional / independent)})
    # Tucker (2001), печатная стр. 70, PDF стр. 2: только опубликованные числовые
    # агрегаты примера; его имена/фамилия здесь намеренно не воспроизводятся.
    # N включает строки с неизвестным именем как отдельной категорией наблюдения.
    pair = binary_association(88700000, 1321612, 56636, 807)
    pair.update(source="https://ans-names.pitt.edu/ans/article/download/1619/1618/3240",
                source_printed_page=70, population_total_rounded=True,
                scope="Одна опубликованная пара США, телефонный справочник 1997; не российское ФИО. Неизвестные имена входят в общий знаменатель.")
    return {"mode": "recalculation_of_published_summary_tables_not_raw_data_replication",
            "full_name_study": {"source": PAPER, "table": 2, "pdf_page": 10,
                                "normalization": "В исследовании объединены половые варианты фамилий и отчеств; не точные формы нашего калькулятора.",
                                "evaluation": "Экстраполяция из половины людей в полный исходный реестр; не независимый внешний тест населения России.",
                                "rmse_unit": "people_per_distinct_name_within_source", "rows": rows},
            "published_pair_example": pair,
            "warning": "Отношение RMSE — не поправочный множитель частоты ФИО. По этим таблицам нельзя восстановить lift каждого ФИО, ковариацию или общую взаимную информацию российских троек."}


def audit_summary(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for name, item in report["collections"].items():
        details = item.get("normalization_audit", {})
        mass = details.get("source_count_mass_in_normalized_collision_groups")
        rows.append({"collection": name, "source_sum_count": item["sum_counts"],
                     "singleton_rows_percent": 100 * item["singleton_rows"] / item["rows"],
                     "normalized_collision_groups": item["normalized_duplicate_groups"],
                     "normalization_audit": details,
                     "source_count_mass_in_collisions_percent": 100 * mass / item["sum_counts"] if mass is not None else None})
    return {"revision": report["revision"], "audit_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "rows": rows}


def entropy(counter, total):
    return -sum((count / total) * math.log2(count / total) for count in counter.values() if count)


def joint_diagnostics(rows):
    """Все клетки одного согласованного агрегата; итог не содержит ключей категорий."""
    if not isinstance(rows, list) or not rows:
        raise ValueError("Нужен непустой список агрегатов")
    counts = Counter()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"surname", "name", "patronymic", "count"}:
            raise ValueError("Ожидаются только surname/name/patronymic/count")
        keys = tuple(row[key] for key in ("surname", "name", "patronymic"))
        for value, prefix in zip(keys, ("F", "I", "O")):
            if not isinstance(value, str) or not re.fullmatch(prefix + r"[1-9][0-9]*", value):
                raise ValueError("Разрешены только обезличенные категории F1/I1/O1; реальные имена не принимаются")
        count = row["count"]
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            raise ValueError("Счётчик должен быть положительным целым")
        if keys in counts:
            raise ValueError("Повтор агрегированной клетки: сначала подтвердите отсутствие пересечений")
        counts[keys] = count
    total = sum(counts.values())
    marginals = [Counter() for _ in range(3)]
    pairs = {(0, 1): Counter(), (0, 2): Counter(), (1, 2): Counter()}
    for key, count in counts.items():
        for i in range(3):
            marginals[i][key[i]] += count
        for ij, counter in pairs.items():
            counter[(key[ij[0]], key[ij[1]])] += count
    h = [entropy(counter, total) for counter in marginals]
    tc = sum(h) - entropy(counts, total)
    pair_mi = {f"{i}-{j}": h[i] + h[j] - entropy(counter, total) for (i, j), counter in pairs.items()}
    weighted_logs = 0.0
    squared_errors_observed = 0.0
    products_squared = [sum((c / total) ** 2 for c in marginal.values()) for marginal in marginals]
    # Полная L2-ошибка включает также не наблюдавшиеся клетки декартова произведения.
    sum_p_squared = sum((count / total) ** 2 for count in counts.values())
    sum_pq = 0.0
    lift_min, lift_max = math.inf, 0.0
    for key, count in counts.items():
        p = count / total
        q = math.prod(marginals[i][key[i]] / total for i in range(3))
        lift = p / q
        weighted_logs += p * math.log2(lift)
        squared_errors_observed += (count - total * q) ** 2
        sum_pq += p * q
        lift_min, lift_max = min(lift_min, lift), max(lift_max, lift)
    return {"population_in_aggregate": total, "observed_distinct_triples": len(counts),
            "marginal_category_counts": [len(c) for c in marginals],
            "total_correlation_bits": max(0.0, tc),
            "weighted_log2_lift_bits": weighted_logs,
            "pair_mutual_information_bits": {k: max(0.0, v) for k, v in pair_mi.items()},
            "observed_cell_lift_min": lift_min, "observed_cell_lift_max": lift_max,
            "observed_cells_in_sample_count_rmse": math.sqrt(squared_errors_observed / len(counts)),
            "all_cells_probability_l2_squared": max(0.0, sum_p_squared - 2 * sum_pq + math.prod(products_squared)),
            "scope": "Описательный расчёт внутри переданного агрегата; не проверка на отложенной выборке. Оценки зависимости смещены на разреженных таблицах; нулевая клетка не доказывает отсутствие людей."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--joint-aggregates", type=Path)
    mode.add_argument("--synthetic-demo", action="store_true", help="Показать независимость пар при зависимости тройки на синтетике")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-source", action="store_true", help="Сверить числа с таблицей статьи через сеть, не сохраняя страницу")
    parser.add_argument("--audit", type=Path, help="Добавить доли из обезличенного аудита словарей")
    args = parser.parse_args()
    if args.synthetic_demo:
        independent = [{"surname": f"F{a+1}", "name": f"I{b+1}", "patronymic": f"O{c+1}", "count": 10}
                       for a in range(2) for b in range(2) for c in range(2)]
        xor = [row for row in independent if (int(row["surname"][1:]) - 1) ^ (int(row["name"][1:]) - 1) == int(row["patronymic"][1:]) - 1]
        result = {"status": "synthetic_not_population_measurement", "independent": joint_diagnostics(independent),
                  "pairwise_independent_but_triple_dependent": joint_diagnostics(xor)}
    elif args.joint_aggregates:
        raw = args.joint_aggregates.read_bytes()
        result = joint_diagnostics(json.loads(raw))
        result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    else:
        result = published_comparison()
    if args.verify_source:
        result["published_source_verification"] = verify_published_source()
    if args.audit:
        result["dictionary_audit_summary"] = audit_summary(args.audit)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        root = Path("data/reference").resolve()
        target = args.output.resolve()
        if not target.is_relative_to(root):
            parser.error("Производные статистики сохраняются только в data/reference вне git")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
