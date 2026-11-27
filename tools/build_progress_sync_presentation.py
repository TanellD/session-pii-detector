"""Build the five-slide progress sync using the previous checkpoint design."""

from pathlib import Path
import json
import re
import subprocess
import sys

import presentation_renderer as kit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/progress-sync-2026-11-11"
sys.path.insert(0, str(ROOT / "outputs/presentation-deps"))
from pypdf import PdfReader, PdfWriter
from pptx import Presentation

Page = kit.Page
BLUE, INK, GREY, WHITE = kit.BLUE, kit.INK, kit.GREY, kit.WHITE
PALE, GREEN = "E8EDFF", "E4F1EB"
REPO = kit.REPO
REFS = {
    "metrics": "ba2652d6f7e7fb9f7eea001eaf14ab48d906069b",
    "data": "0929e1e",
    "api": "55ba78f874cc622c9000460c042b30e50d66ca3e",
}


def source(kind, path):
    return f"{REPO}/blob/{REFS[kind]}/{path}"


METRICS = source("metrics", "docs/kb/metrics.md")
LATENCY = source("metrics", "docs/kb/latency-budget.md")
DATA = source("data", "docs/project/data-research-summary.md")
API = source("api", "docs/api/session-api.md")


def chrome(page, number, eyebrow, title):
    page.text(80, 44, 1230, eyebrow.upper(), 21, GREY, True)
    page.text(80, 98, 1430, title, 49, INK, True)
    page.box(80, 187, 90, 5, BLUE)
    page.box(80, 832, 1440, 2, kit.BORDER)
    page.text(80, 851, 1260, "SESSION PII  /  СИНК ПО ПРОГРЕССУ  /  11.11.2026", 18, GREY)
    page.text(1460, 846, 60, f"{number:02d}", 25, BLUE, True)


def link(page, label, url, x=80, y=789, width=1400):
    page.text(x, y, width, label, 19, BLUE, False, url)


def fraction(page, x, y, width, numerator, denominator, size=36, color=BLUE):
    page.text(x, y, width, numerator, size, color, True)
    page.box(x, y + size * 1.35, width, 2, color)
    page.text(x, y + size * 1.55, width, denominator, size, color, True)


def build_pages():
    pages = []
    notes = (
        "Начнём с частей сервиса, чтобы дальше было понятно, где применяются метрики. "
        "На входе новое сообщение и идентификатор сессии. Regex и NER извлекают упоминания "
        "с типами и границами. Нормализация устраняет конфликты и повторы. Линковка определяет, "
        "какому человеку принадлежат атрибуты, и явно сохраняет неизвестную принадлежность. "
        "Затем обновляется состояние по людям, а политика формирует скор и первый алерт. "
        "Нормализация и линковка согласуются между собой: окончательный повтор разрешается "
        "с учётом владельца. Это рабочая схема для MVP. "
        "Извлечение уже проверялось на CPU; сессионное ядро ещё предстоит собрать. "
        "Историю повторно через NER не пропускаем. Явно общий факт может принадлежать разным людям; "
        "родство само по себе не доказывает общие атрибуты."
    )
    p = Page("Из каких частей собираем сервис", notes)
    chrome(p, 1, "Рабочая схема MVP · реализация сессионного ядра впереди", "Из каких частей собираем сервис")
    p.text(80, 215, 1400, "Новое сообщение + session_id → обновлённое состояние → первый алерт", 29, GREY)
    stages = [
        ("Извлечение", "Regex + NER\nТип и границы\nупоминания"),
        ("Нормализация", "Единый формат\nКонфликты типов\nПовторные факты"),
        ("Линковка", "Принадлежность\nлюдям и явная\nнеопределённость"),
        ("Состояние", "Уникальные факты\nпо каждому\nчеловеку"),
        ("Политика", "Скор и правила\nВердикт сессии\nПервый алерт"),
    ]
    for i, (title, body) in enumerate(stages):
        x = 80 + i * 294
        p.box(x, 302, 264, 245, BLUE if i == 4 else WHITE)
        p.text(x + 20, 326, 230, title, 27, WHITE if i == 4 else INK, True)
        p.text(x + 20, 390, 225, body, 25, WHITE if i == 4 else GREY)
        if i < 4:
            p.text(x + 271, 396, 23, "›", 34, BLUE, True)
    p.box(80, 589, 1440, 131, PALE)
    p.text(105, 611, 1380, "Общий принцип: факты разных людей не смешиваем", 30, INK, True)
    p.text(105, 662, 1380, "Нормализация и линковка согласуются; повторный факт не увеличивает накопление.", 26, GREY)
    pages.append(p)

    notes = (
        "Теперь распределим измерения по компонентам. Для извлечения считаем точное совпадение "
        "границ и типа: TP — найденный эталонный спан, FP — лишний, FN — пропущенный. "
        "Используем Precision, Recall и F2 с большим весом полноты. Для линковки сначала подаём "
        "эталонные атрибуты: это отделяет ошибки владельца от ошибок NER. Pairwise проверяет, "
        "есть ли у пары атрибутов общий известный владелец. Лишнее объединение даёт FP, "
        "разделение — FN; считаем Precision, Recall и F1 по парам. "
        "B-cubed остаётся альтернативной диагностикой на однозначном срезе: "
        "P_m=|C(m)∩G(m)|/|C(m)|, R_m=|C(m)∩G(m)|/|G(m)|; затем средние по элементам "
        "и F1=2PR/(P+R). Общие владельцы требуют адаптации. "
        "Весь сервис измеряем по задержке от принятия полного сообщения обработчиком до готового "
        "ответа после обновления состояния. Сеть клиента, очередь до обработчика и внешняя LLM "
        "не входят в эту границу. Публикуем квантили, ошибки, нагрузку и рост памяти. "
        "Численного бюджета пока нет."
    )
    p = Page("Как проверяем каждый компонент", notes)
    chrome(p, 2, "Диагностика компонентов · качество извлечения и принадлежности", "Как проверяем каждый компонент")
    rows = [
        ("Извлечение", "Точные границы + тип", "P = TP / (TP + FP)  ·  R = TP / (TP + FN)", "F2 = 5TP / (5TP + 4FN + FP)"),
        ("Линковка", "Пары на эталонных атрибутах", "P = TP / (TP + FP)  ·  R = TP / (TP + FN)", "F1 = 2TP / (2TP + FP + FN)"),
    ]
    for i, (name, meaning, formula, bottom) in enumerate(rows):
        y = 235 + i * 220
        p.box(80, y, 1440, 192, WHITE)
        p.text(104, y + 25, 355, name, 33, INK, True)
        p.text(104, y + 87, 350, meaning, 25, GREY)
        p.text(505, y + 25, 470, "P = TP / (TP + FP)", 31, BLUE, True)
        p.text(1000, y + 25, 490, "R = TP / (TP + FN)", 31, BLUE, True)
        p.text(505, y + 100, 980, bottom, 31, INK)
    p.box(80, 704, 1440, 67, PALE)
    p.text(103, 721, 1390, "Весь сервис: latency = t_end − t_start  ·  p50 / p95 / p99  ·  рост памяти", 28, INK, True)
    link(p, "TP — верно найденное; FP — лишнее; FN — пропуск · Для извлечения считаем спаны, для линковки — пары", METRICS)
    pages.append(p)

    notes = (
        "Основная итоговая метрика — F2-timely. t_gold — первое сообщение, на котором по эталонной "
        "политике требуется алерт; t_pred — сообщение первого алерта системы. "
        "Точное попадание — TP. Ранний алерт — FP. Опоздание или отсутствие "
        "алерта — FN. На отрицательной сессии любой алерт — FP. "
        "Для F2-session используем ту же формулу, но другие счётчики: любой алерт в положительной "
        "сессии даёт TP независимо от позиции. Это диагностика наличия распознавания. "
        "Offset считаем только там, где есть и эталонный, и предсказанный момент, то есть на TP-session. "
        "Отрицательный offset — рано, положительный — поздно. FP0 — доля отрицательных сессий "
        "с ложным алертом. Все основные счётчики суммируем по корпусу, затем вычисляем метрики. "
        "Важное ограничение: Recall-timely не включает ранние алерты в знаменатель и не является "
        "долей точных попаданий среди всех положительных сессий. Неуверенный эталон исключается "
        "с отдельным отчётом; это не разрешение исключать неудобные предсказания системы. "
        "Пустые знаменатели — N/A. Численных результатов baseline ещё нет."
    )
    p = Page("Главное — первый алерт вовремя", notes)
    chrome(p, 3, "Итоговая оценка сервиса · своевременность первого алерта", "Главное — первый алерт вовремя")
    p.box(80, 225, 560, 329, BLUE)
    p.text(108, 249, 500, "F2-timely", 39, WHITE, True)
    fraction(p, 110, 318, 490, "5TP", "5TP + 4FN + FP", 35, WHITE)
    p.text(110, 465, 490, "Micro: сначала суммируем\nисходы по корпусу", 25, WHITE)
    p.text(691, 227, 820, "t_gold — эталонный момент; t_pred — первый алерт", 25, GREY)
    outcomes = [("Точно: t_pred = t_gold", "TP", GREEN), ("Рано: t_pred < t_gold", "FP", PALE), ("Поздно или алерта нет", "FN", WHITE), ("Алерт в отрицательной сессии", "FP", WHITE)]
    for i, (label, value, color) in enumerate(outcomes):
        y = 284 + i * 67
        p.box(685, y, 835, 57, color)
        p.text(703, y + 10, 600, label, 25, INK)
        p.text(1320, y + 10, 185, value, 25, BLUE, True)
    for x, name, formula, detail in [
        (80, "F2-session", "5TP / (5TP + 4FN + FP)", "Любой алерт в положительной\nсессии — TP, независимо от позиции"),
        (570, "Offset", "t_pred − t_gold", "На TP-session: минус — рано,\nплюс — поздно; пропуски отдельно"),
        (1060, "FP0", "FP_negative / N_negative", "Доля отрицательных сессий\nс ложным алертом"),
    ]:
        p.box(x, 590, 460, 174, WHITE)
        p.text(x + 20, 606, 425, name, 29, INK, True)
        p.text(x + 20, 653, 425, formula, 25, BLUE, True)
        p.text(x + 20, 699, 425, detail, 21, GREY)
    link(p, "Recall-timely не равен доле точных попаданий среди всех положительных сессий · численных целей пока нет", METRICS)
    pages.append(p)

    notes = (
        "По данным подготовлены обзор русских источников, сценарии, план синтетики, "
        "few-shot-промпты и предложения к разметке. Для первичного аудита выделены Scanpatch, "
        "русская конфигурация Meddies и собственные русские синтетические сессии CONFIDE. "
        "Redmadrobot оставлен кандидатом: нужно отделить полностью синтетическую часть от "
        "производных реальных логов. "
        "Готового открытого русского корпуса с нужным моментом в исследованном пуле не найдено; "
        "это ограниченный поиск, а не доказательство отсутствия таких данных. "
        "До пилота нужны правила достаточности, схема и каталог сценариев. "
        "На этом синке обсуждаем подготовленные материалы и оставшиеся вопросы по данным."
    )
    p = Page("Данные: подготовлен путь к пилоту", notes)
    chrome(p, 4, "Прогресс по данным · источники, сценарии и разметка", "Данные: подготовлен путь к пилоту")
    for x, title, body, color in [
        (80, "Подготовлено", "Обзор русских источников\nСценарии для обсуждения\nПлан синтетики и промпты\nПредложения к разметке", WHITE),
        (570, "Кандидаты на аудит", "Scanpatch · русская часть\nMeddies · russian\nCONFIDE · RU-синтетика\nRedmadrobot · проверить источник", WHITE),
        (1060, "До первого пилота", "Принять политику и схему\nПроверить допуск источников\nСогласовать сценарии\nОценить объём и стоимость", PALE),
    ]:
        p.box(x, 230, 460, 306, color)
        p.text(x + 24, 255, 410, title, 31, INK, True)
        p.text(x + 24, 321, 410, body, 25, GREY)
    p.text(80, 605, 1430, "Готового русского корпуса с нужным моментом в изученном пуле не найдено.", 30, GREY)
    pages.append(p)

    notes = (
        "Сейчас завершаем уже начатые задачи. Данил занимается координацией, определением "
        "ПДн и классами. Андрей — детекторами и контрактом сервиса. Олеся — постановкой, "
        "метриками и методикой задержки. Владимир — данными, сценариями и разметкой. "
        "Ближайшее направление работы состоит из трёх частей: документ о том, как определяем "
        "момент появления ПДн в сессии; линковщик, связывающий факты с людьми; адаптация NER "
        "к нашим классам, меткам и границам. На следующем синке добавятся новые задачи. "
        "Пока завершаем то, что уже было в работе. Новые направления этим слайдом "
        "не распределяем по исполнителям."
    )
    p = Page("Текущие задачи и следующий шаг", notes)
    chrome(p, 5, "Команда и ближайшие направления работы", "Текущие задачи и следующий шаг")
    p.text(80, 222, 700, "Текущее распределение", 31, INK, True)
    p.text(850, 222, 670, "Следующий шаг", 31, INK, True)
    duties = [
        ("Данил", "Координация, определение ПДн и классы"),
        ("Андрей", "Детекторы и контракт сервиса"),
        ("Олеся", "Постановка, метрики и методика задержки"),
        ("Владимир", "Данные, сценарии и разметка"),
    ]
    for i, (name, duty) in enumerate(duties):
        y = 282 + i * 99
        p.box(80, y, 725, 85, WHITE)
        p.text(102, y + 12, 175, name, 27, INK, True)
        p.text(290, y + 12, 490, duty, 25, GREY)
    for i, (title, body) in enumerate([
        ("Момент ПДн в сессии", "Документ: основания первого алерта и спорные случаи"),
        ("Линковщик", "Принадлежность фактов людям и повторы"),
        ("Адаптация NER", "Наши классы, машинные метки и границы"),
    ]):
        y = 282 + i * 132
        p.box(850, y, 670, 117, PALE)
        p.text(873, y + 13, 620, title, 29, BLUE, True)
        p.text(873, y + 57, 620, body, 24, GREY)
    p.box(80, 718, 1440, 79, GREEN)
    p.text(103, 740, 1390, "Пока завершаем начатое. На следующем синке добавятся новые задачи.", 29, INK, True)
    pages.append(p)
    return pages


def main():
    pages = build_pages()
    assert len(pages) == 5
    OUT.mkdir(parents=True, exist_ok=True)
    kit.OUT = OUT
    kit.render(pages)
    old_pdf = OUT / "session-pii-checkpoint.pdf"
    pdf_path = OUT / "session-pii-progress-sync.pdf"
    reader = PdfReader(old_pdf)
    writer = PdfWriter()
    writer.append(reader)
    writer.add_metadata({"/Title": "Session PII — синк по прогрессу 11.11.2026", "/Author": "Команда проекта"})
    with pdf_path.open("wb") as stream:
        writer.write(stream)
    pptx_path = OUT / "session-pii-progress-sync.pptx"
    (OUT / "session-pii-checkpoint.pptx").replace(pptx_path)
    old_pdf.unlink()
    speech = ["# Синк по прогрессу — 11.11.2026", "", "Текст также встроен в заметки PowerPoint.", ""]
    for i, p in enumerate(pages, 1):
        speech.extend([f"## Слайд {i}. {p.title}", "", p.notes, ""])
    (OUT / "speech.md").write_text("\n".join(speech), encoding="utf-8")
    manifest = {}
    for key, ref in REFS.items():
        manifest[key] = subprocess.check_output(["git", "rev-parse", ref], cwd=ROOT, text=True).strip()
    (OUT / "sources.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    loaded = Presentation(pptx_path)
    assert len(loaded.slides) == len(PdfReader(pdf_path).pages) == 5
    for slide in loaded.slides:
        assert slide.notes_slide.notes_text_frame.text.strip()
    for page in pages:
        for e in page.elements:
            if e.kind == "text":
                for line in e.text.splitlines():
                    font = kit.ImageFont.truetype(str(kit.FONTS[e.bold]), e.size)
                    assert font.getlength(line) <= e.w + 1, line
    print(f"Validated {len(pages)} slides in PPTX/PDF, speaker notes and text widths.")
    words = sum(len(re.findall(r"\S+", p.notes)) for p in pages)
    print(f"Speech: {words} words.")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
