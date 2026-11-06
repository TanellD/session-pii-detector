"""Build the checkpoint deck, linked PDF, preview and speaker notes."""

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "outputs/presentation-deps"))
from PIL import Image, ImageDraw, ImageFont
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUT = ROOT / "outputs/checkpoint-2026-11-05"
W, H = 1600, 900
NAVY, INK, PAPER = "202938", "252C39", "F6F7FB"
BLUE, MINT, WHITE, GREY, BORDER = "0019FF", "B9DED4", "FFFFFF", "667183", "E1E5EF"
REPO = "https://github.com/TanellD/session-pii-detector"
TEAM = REPO + "/blob/main/docs/project/team.md"
FONTS = {False: Path("C:/Windows/Fonts/segoeui.ttf"), True: Path("C:/Windows/Fonts/segoeuib.ttf")}


@dataclass
class Element:
    kind: str
    x: float
    y: float
    w: float
    h: float
    color: str
    text: str = ""
    size: int = 30
    bold: bool = False
    link: str = ""
    points: tuple = ()


class Page:
    def __init__(self, title, notes, dark=False):
        self.title, self.notes, self.dark = title, notes, dark
        self.elements = []

    def box(self, x, y, w, h, color):
        self.elements.append(Element("roundbox" if w > 180 and h > 70 else "box", x, y, w, h, color))

    def ellipse(self, x, y, w, h, color):
        self.elements.append(Element("ellipse", x, y, w, h, color))

    def polygon(self, points, color):
        self.elements.append(Element("polygon", 0, 0, 0, 0, color, points=tuple(points)))

    def person(self, cx, top, size, color):
        self.ellipse(cx - size * .22, top, size * .44, size * .44, color)
        self.ellipse(cx - size * .39, top + size * .52, size * .78, size * .7, color)
        self.box(cx - size * .39, top + size * .85, size * .78, size * .5, color)

    def text(self, x, y, w, text, size=30, color=INK, bold=False, link=""):
        text = text.replace(" ↗", "")
        font = ImageFont.truetype(str(FONTS[bold]), size)
        lines = []
        for paragraph in text.split("\n"):
            words = paragraph.split()
            line = ""
            for word in words:
                candidate = (line + " " + word).strip()
                if font.getlength(candidate) > w and line:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
        wrapped = "\n".join(lines)
        height = len(lines) * size * 1.28 + 4
        if x < 0 or y < 0 or x + w > W or y + height > H:
            raise ValueError(f"Text exceeds slide bounds: {text[:70]}")
        self.elements.append(Element("text", x, y, w, height, color, wrapped, size, bold, link))
        return height


def chrome(page, number, eyebrow, title):
    page.text(80, 44, 1200, eyebrow.upper(), 21, GREY, True)
    page.text(80, 98, 1430, title, 51, INK, True)
    page.box(80, 187, 90, 5, BLUE)
    page.box(80, 832, 1440, 2, BORDER)
    page.text(80, 851, 1260, "SESSION PII  /  ЧЕКПОИНТ ИССЛЕДОВАНИЯ  /  05.11.2026", 18, GREY)
    page.text(1460, 846, 60, f"{number:02d}", 25, BLUE, True)


def circle_polygon(cx, cy, radius):
    return [(cx + radius * math.cos(i * math.tau / 120),
             cy + radius * math.sin(i * math.tau / 120)) for i in range(120)]


def intersection(subject, clip):
    """Clip convex vector paths; all sets remain editable in PowerPoint."""
    result = subject
    for edge_index, b in enumerate(clip):
        a = clip[edge_index - 1]
        def signed(p):
            return (b[0]-a[0])*(p[1]-a[1]) - (b[1]-a[1])*(p[0]-a[0])
        incoming, result = result, []
        if not incoming:
            break
        previous = incoming[-1]
        previous_side = signed(previous)
        for point in incoming:
            side = signed(point)
            if (side >= 0) != (previous_side >= 0):
                t = previous_side / (previous_side - side)
                result.append((previous[0] + t*(point[0]-previous[0]),
                               previous[1] + t*(point[1]-previous[1])))
            if side >= 0:
                result.append(point)
            previous, previous_side = point, side
    return result


def draw_identification(page):
    circles = [(1030, 365, 205), (1280, 365, 205), (1155, 550, 205)]
    paths = [circle_polygon(*circle) for circle in circles]
    for path, color in zip(paths, ("DCE5FF", "D8EDE8", "F3E6CB")):
        page.polygon(path, color)
    for i, j in ((0, 1), (0, 2), (1, 2)):
        page.polygon(intersection(paths[i], paths[j]), "BDCEE8")
    accent = "526EE2"
    page.polygon(intersection(intersection(paths[0], paths[1]), paths[2]), accent)
    for row, y in enumerate(range(217, 660, 66)):
        for col, x in enumerate(range(877, 1460, 60)):
            x += 12 if row % 2 else 0
            memberships = sum((x-cx)**2 + (y+16-cy)**2 < (r-17)**2 for cx,cy,r in circles)
            if memberships in (1, 2) and (x-1155)**2 + (y+16-431)**2 > 100**2:
                page.person(x, y, 21, "9AAFC5" if memberships == 1 else "819ABE")
    page.ellipse(1090, 365, 130, 130, WHITE)
    page.person(1155, 385, 66, accent)
    page.text(920, 153, 180, "Город", 26, GREY, True)
    page.text(1290, 153, 200, "Работа", 26, GREY, True)
    page.text(1097, 689, 210, "Возраст", 26, GREY, True)
    page.text(1051, 511, 235, "Один человек", 27, INK, True)
    page.text(950, 785, 560, "Пересечение накопленных сигналов", 23, GREY)


def build_pages(telegram):
    notes = [
        "Мы делаем модуль сессионной детекции персональных данных для диалога пользователя с LLM. Проблема в том, что сведения могут раскрываться постепенно: сначала человек называет имя, затем место работы и должность, а позже добавляет другие признаки. Проверка каждого сообщения отдельно не показывает всей накопленной картины. При этом нельзя просто сложить все найденные слова: в одном разговоре могут обсуждаться пользователь, его мама и коллега. Поэтому нам важно знать, кому принадлежит каждый факт. Цель проекта — определить первое сообщение, после которого накопленных сведений о человеке достаточно для срабатывания согласованной политики. Сейчас мы на этапе исследования и сужения первой версии; готовую сессионную систему пока не демонстрируем.",
        "В команде четыре участника. Данил координирует проект и работает над постановкой, классами ПДн и правилами срабатывания. Андрей исследует аналоги и детекторы, провёл проверку regex и GLiNER и подготовил черновик API. Олеся работает над метриками, определением успешного результата и методикой измерения задержки. Направление Вовы — данные, сценарии и разметка диалогов. Это фокусы работы на текущем этапе, а не постоянное разделение ролей. На слайде есть ссылки на профили участников, страницу команды и репозиторий, где можно посмотреть материалы и обсуждения.",
        "За время исследования мы подготовили три группы результатов. Первая — постановка: собраны правовые источники и предложены классы и комбинации признаков. Ментор в целом поддержал компактный список и подчеркнул приоритет полноты. Вторая — обзор аналогов. Нашли близкую работу о накоплении сведений по человеку и изучили PII-TRACE, который оценивает контекстное обнаружение повторных упоминаний. Поэтому сам принцип накопления не объявляем новым. Третья — практическая проверка извлечения: regex и GLiNER запускаются на CPU, результаты и команды воспроизведения записаны. Обнаружены ошибки типов и границ, в том числе конфликт распознавания номера как разных сущностей. Также разобрали leaderboard ментора: он полезен для классов и ошибок детекторов, но не даёт частот реального трафика. API и архитектура пока остаются предложениями.",
        "Для MVP предлагаем сознательно ограничиться русским обычным чатом и анализировать только сообщения пользователя. Извлечение выполняем на новом сообщении, а историю повторно через NER не пропускаем. Дальше связываем признаки с людьми, нормализуем их и исключаем дубли. Состояние строим по каждому человеку отдельно. Начинаем с простого счётчика уникальных признаков и фиксированных правил для сильных оснований. Явно общий факт, например один адрес проживания двух людей, можно связать с обоими. Но родство само по себе не даёт права переносить фамилию или адрес. Исправления, сложный вымысел, специальные исключения для публичных лиц и полноценные агентные сценарии откладываем. Это границы предлагаемой первой версии, а не утверждение, что такие случаи безопасны. Правило одиночного телефона ещё нужно свести с новым ответом ментора.",
        "К MVP первого декабря хотим пройти три шага. Сначала зафиксировать политику срабатывания, схему разметки и небольшой синтетический набор. Затем собрать сессионное ядро: сначала проверить его на эталонных фактах, а потом подключить реальное извлечение. Наконец, сравнить проверку отдельных сообщений, простое накопление и накопление по людям на одинаковых данных и правилах. Основной ориентир — своевременный первый алерт; F2-timely пока предложение протокола, его полный подсчёт согласуем до финального эксперимента. Отдельно измерим задержку и память своего сервиса. Результатом должен стать работающий API, воспроизводимый отчёт и понятные ограничения. Большое сравнение моделей и перенос на агентов добавим после готовности этого ядра. Материалы доступны в репозитории; вопросы по проекту можно направлять лиду.",
    ]
    notes[1] = notes[1].replace("Вовы", "Владимира")
    notes[0] = "Мы делаем модуль сессионной детекции персональных данных для диалога пользователя с LLM. Общая задача — находить ПДн в сообщениях и отслеживать, как накопленный контекст меняет оценку сессии. Здесь возможны разные ситуации: сведения относятся к человеку, который уже определён в доступном контексте, либо постепенно появляется возможность его определить. Наш исследовательский фокус — второй случай: разные сообщения по отдельности не показывают всей картины, а вместе раскрывают человека. Пересечение групп на иллюстрации показывает эту идею, но не является измеренным порогом. При этом важно различать пользователя и третьих лиц, чтобы не складывать чужие факты в один профиль. Мы учитываем только доступный модулю контекст и не обещаем узнать неизвестную ему связь аккаунта с личностью. Сейчас мы на этапе исследования и сужения первой версии; готовую сессионную систему пока не демонстрируем."
    pages = []
    p = Page("Задача", notes[0])
    p.text(80, 57, 1300, "SESSION PII  /  ИССЛЕДОВАТЕЛЬСКИЙ ПРОЕКТ", 22, BLUE, True)
    p.box(80, 130, 90, 5, BLUE)
    p.text(80, 154, 680, "Персональные данные\nв контексте сессии", 56, INK, True)
    p.text(80, 373, 650, "Находим персональные данные в сообщениях и отслеживаем, как накопленный контекст меняет оценку сессии.", 34, GREY)
    p.box(80, 604, 650, 146, "E8EDFF")
    p.text(106, 626, 590, "Наш фокус\nСведения из разных сообщений вместе позволяют определить человека.", 26, INK)
    draw_identification(p)
    p.text(80, 803, 700, "Репозиторий проекта ↗", 25, BLUE, True, REPO)
    p.text(1400, 843, 120, "01 / 05", 20, GREY)
    pages.append(p)

    p = Page("Команда", notes[1])
    chrome(p, 2, "Текущие направления · задачи будут дополняться", "Команда проекта")
    people = [
        (80, 222, "Данил", "TanellD", "Лид · постановка и классы ПДн"),
        (820, 222, "Андрей", "Kopylov-Andrey", "Аналоги · детекторы · API"),
        (80, 468, "Владимир", "vladimirtimoshenko", "Данные · сценарии · разметка"),
        (820, 468, "Олеся", "olesyasiz22-a11y", "Метрики · оценка · задержка"),
    ]
    for x, y, name, login, role in people:
        p.box(x, y, 700, 210, WHITE)
        p.box(x, y, 7, 210, BLUE)
        p.text(x + 30, y + 25, 640, name, 39, INK, True)
        p.text(x + 30, y + 89, 640, role, 28, GREY)
        p.text(x + 30, y + 153, 640, "github.com/" + login + " ↗", 23, BLUE, link="https://github.com/" + login)
    p.text(80, 740, 620, "Страница команды ↗", 26, BLUE, True, TEAM)
    if telegram:
        p.text(820, 740, 700, "Telegram лида ↗", 26, BLUE, True, telegram)
    pages.append(p)

    p = Page("Что сделано", notes[2])
    chrome(p, 3, "Результаты исследования", "Есть основа для сессионного MVP")
    cards = [
        (80, "ПОСТАНОВКА", "Что считаем сигналом", "Правовые источники\nКлассы и комбинации\nОбратная связь ментора", "PR #16 ↗", REPO + "/pull/16"),
        (573, "АНАЛОГИ", "Что уже существует", "Lee et al. · накопление\nPII-TRACE · контекст\nLeaderboard · извлечение", "Источники и обзор ↗", REPO + "/pull/20"),
        (1066, "ПРАКТИКА", "Что проверили", "Regex + GLiNER на CPU\nОшибки типов и границ\nВоспроизводимый запуск", "Эксперимент ↗", REPO + "/blob/55ba78f874cc622c9000460c042b30e50d66ca3e/docs/experiments/2026-11-02-gliner-cpu.md"),
    ]
    for x, label, title, body, link_text, link in cards:
        p.box(x, 229, 454, 409, WHITE)
        p.text(x + 26, 257, 400, label, 20, BLUE, True)
        p.text(x + 26, 307, 400, title, 32, INK, True)
        p.text(x + 26, 392, 400, body, 27, GREY)
        p.text(x + 26, 581, 400, link_text, 22, BLUE, True, link)
    p.box(80, 681, 1440, 102, "E8EDFF")
    p.text(108, 702, 1375, "Сессионный сервис — следующий этап. Правила, метрики и API пока содержат предложения к согласованию.", 27, INK)
    pages.append(p)

    p = Page("Контур MVP", notes[3])
    chrome(p, 4, "Предлагаемая первая версия", "Небольшое ядро, которое можно измерить")
    steps = [("Сообщение", "Русский текст user"), ("Извлечение", "Regex + GLiNER"), ("Люди и факты", "Линковка · дубли"), ("Накопление", "Счётчик и правила"), ("Вердикт", "Первый алерт")]
    for i, (title, body) in enumerate(steps):
        x = 80 + i * 292
        p.box(x, 237, 270, 150, BLUE if i == 4 else WHITE)
        p.text(x + 19, 261, 232, title, 28, WHITE if i == 4 else INK, True)
        p.text(x + 19, 321, 232, body, 22, "F4F7F4" if i == 4 else GREY)
        if i < 4:
            p.text(x + 271, 284, 22, "›", 30, BLUE, True)
    p.text(80, 455, 670, "В ядре", 34, INK, True)
    p.text(80, 517, 670, "• Пользователь и явные третьи лица\n• Уникальные факты по каждому человеку\n• Общие факты — только при явной связи\n• Историю через NER не перезапускаем", 28, INK)
    p.text(860, 455, 660, "После ядра", 34, GREY, True)
    p.text(860, 517, 660, "Агенты и tool calls\nИсправления и сложный вымысел\nГрафовые выводы и обученные веса\nСпециальная обработка публичных лиц", 28, GREY)
    pages.append(p)

    p = Page("План", notes[4])
    chrome(p, 5, "Следующий чекпоинт · 01.12.2026", "Что хотим показать к MVP")
    stages = [
        (80, "A", "Политика и данные", "Таблица алертов\nРазметка момента\nСинтетические диалоги"),
        (573, "B", "Сессионное ядро", "Линковка и накопление\nПроверка на эталонных фактах\nПодключение детектора"),
        (1066, "C", "Сравнение и API", "Сообщение против сессии\nСвоевременность алерта\nЗадержка и память"),
    ]
    for x, mark, title, body in stages:
        p.box(x, 223, 454, 302, WHITE)
        p.text(x + 25, 245, 380, mark, 26, BLUE, True)
        p.text(x + 25, 299, 404, title, 31, INK, True)
        p.text(x + 25, 367, 404, body, 25, GREY)
    p.text(80, 565, 1420, "Итог: работающий сервис + воспроизводимая проверка пользы сессионного анализа", 35, INK, True)
    pages.append(p)
    return pages


def render(pages):
    OUT.mkdir(parents=True, exist_ok=True)
    presentation = Presentation()
    presentation.slide_width, presentation.slide_height = Inches(13.333333), Inches(7.5)
    pdfmetrics.registerFont(TTFont("SlideRegular", str(FONTS[False])))
    pdfmetrics.registerFont(TTFont("SlideBold", str(FONTS[True])))
    pdf_target = OUT / "session-pii-checkpoint.pdf"
    pdf_temporary = OUT / "session-pii-checkpoint-building.pdf"
    pdf = canvas.Canvas(str(pdf_temporary), pagesize=(960, 540))
    pdf.setTitle("Session PII — исследование и план MVP")
    pdf.setAuthor("Команда проекта")
    previews = []
    for idx, page in enumerate(pages, 1):
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor.from_string(NAVY if page.dark else PAPER)
        slide.notes_slide.notes_text_frame.text = page.notes
        image = Image.new("RGB", (W, H), "#" + (NAVY if page.dark else PAPER))
        drawing = ImageDraw.Draw(image)
        pdf.setFillColor("#" + (NAVY if page.dark else PAPER))
        pdf.rect(0, 0, 960, 540, fill=1, stroke=0)
        for e in page.elements:
            x, y, width, height = [Inches(v / 120) for v in (e.x, e.y, e.w, e.h)]
            if e.kind in ("box", "roundbox", "ellipse", "polygon"):
                if e.kind == "polygon":
                    coordinates = [(int(Inches(px / 120)), int(Inches(py / 120))) for px,py in e.points]
                    builder = slide.shapes.build_freeform(*coordinates[0])
                    builder.add_line_segments(coordinates[1:], close=True)
                    shape = builder.convert_to_shape()
                else:
                    kind = MSO_SHAPE.OVAL if e.kind == "ellipse" else (MSO_SHAPE.ROUNDED_RECTANGLE if e.kind == "roundbox" else MSO_SHAPE.RECTANGLE)
                    shape = slide.shapes.add_shape(kind, x, y, width, height)
                    if e.kind == "roundbox":
                        shape.adjustments[0] = 0.075
                shape.fill.solid()
                shape.fill.fore_color.rgb = RGBColor.from_string(e.color)
                shape.line.fill.background()
                if e.kind == "roundbox" and e.color == WHITE:
                    shape.line.fill.solid()
                    shape.line.color.rgb = RGBColor.from_string(BORDER)
                    shape.line.width = Pt(.65)
                pdf.setFillColor("#" + e.color)
                if e.kind == "polygon":
                    drawing.polygon(e.points, fill="#" + e.color)
                    path = pdf.beginPath()
                    path.moveTo(e.points[0][0]*.6, (H-e.points[0][1])*.6)
                    for px,py in e.points[1:]:
                        path.lineTo(px*.6, (H-py)*.6)
                    path.close()
                    pdf.drawPath(path, fill=1, stroke=0)
                elif e.kind == "ellipse":
                    drawing.ellipse((e.x, e.y, e.x+e.w, e.y+e.h), fill="#" + e.color)
                    pdf.ellipse(e.x*.6, (H-e.y-e.h)*.6, (e.x+e.w)*.6, (H-e.y)*.6, fill=1, stroke=0)
                elif e.kind == "roundbox":
                    radius = min(e.w,e.h)*.075
                    drawing.rounded_rectangle((e.x,e.y,e.x+e.w,e.y+e.h), radius=radius, fill="#"+e.color,
                                              outline="#"+BORDER if e.color==WHITE else None, width=1)
                    pdf.setStrokeColor("#"+BORDER)
                    pdf.setLineWidth(.65)
                    pdf.roundRect(e.x*.6,(H-e.y-e.h)*.6,e.w*.6,e.h*.6,radius*.6,fill=1,stroke=int(e.color==WHITE))
                else:
                    drawing.rectangle((e.x, e.y, e.x + e.w, e.y + e.h), fill="#" + e.color)
                    pdf.rect(e.x * .6, (H - e.y - e.h) * .6, e.w * .6, e.h * .6, fill=1, stroke=0)
            else:
                shape = slide.shapes.add_textbox(x, y, width, height)
                tf = shape.text_frame
                tf.word_wrap = False
                tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
                for line_index, line in enumerate(e.text.split("\n")):
                    paragraph = tf.paragraphs[0] if line_index == 0 else tf.add_paragraph()
                    paragraph.space_before = paragraph.space_after = Pt(0)
                    paragraph.line_spacing = Pt(e.size * 1.28 * .6)
                    run = paragraph.add_run()
                    run.text = line
                    run.font.name, run.font.size = "Segoe UI", Pt(e.size * .6)
                    run.font.bold = e.bold
                    run.font.color.rgb = RGBColor.from_string(e.color)
                    if e.link:
                        run.hyperlink.address = e.link
                    font = ImageFont.truetype(str(FONTS[e.bold]), e.size)
                    drawing.text((e.x, e.y + line_index * e.size * 1.28), line, fill="#" + e.color, font=font)
                    pdf.setFillColor("#" + e.color)
                    pdf.setFont("SlideBold" if e.bold else "SlideRegular", e.size * .6)
                    pdf.drawString(e.x * .6, (H - e.y - e.size - line_index * e.size * 1.28) * .6, line)
                if e.link:
                    pdf.linkURL(e.link, (e.x * .6, (H-e.y-e.h)*.6, (e.x+e.w)*.6, (H-e.y)*.6), relative=0, thickness=0)
        image.save(OUT / f"slide-{idx}.png")
        previews.append(image.resize((800, 450)))
        pdf.showPage()
    pdf.save()
    pdf_candidates = [pdf_target, OUT / "session-pii-checkpoint-updated.pdf"]
    pdf_candidates += [OUT / f"session-pii-checkpoint-revision-{i}.pdf" for i in range(2, 25)]
    for candidate in pdf_candidates:
        try:
            pdf_temporary.replace(candidate)
            pdf_target = candidate
            break
        except PermissionError:
            continue
    else:
        raise PermissionError("All presentation PDF destinations are open; generated PDF remains in the building file")
    print(f"PDF: {pdf_target}")
    presentation.save(OUT / "session-pii-checkpoint.pptx")
    contact_sheet = Image.new("RGB", (1600, 1350), "#DCE4ED")
    for i, preview in enumerate(previews):
        contact_sheet.paste(preview, ((i % 2) * 800, (i // 2) * 450))
    contact_sheet.save(OUT / "preview.png")


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--telegram", default="")
    options = args.parse_args()
    telegram = options.telegram.strip()
    if telegram.startswith("@"):
        telegram = "https://t.me/" + telegram[1:]
    if telegram and not re.fullmatch(r"https://t\.me/[A-Za-z0-9_]+/?", telegram):
        raise ValueError("Expected a Telegram @username or https://t.me/username")
    pages = build_pages(telegram)
    render(pages)
    speech = ["# Выступление: исследование и план MVP", "", "Ориентир — около пяти минут при спокойном темпе. Текст также встроен в заметки PowerPoint.", ""]
    for index, page in enumerate(pages, 1):
        speech += [f"## Слайд {index}. {page.title}", "", page.notes, ""]
    (OUT / "speech.md").write_text("\n".join(speech), encoding="utf-8")
    words = sum(len(re.findall(r"\S+", page.notes)) for page in pages)
    print(f"Created {len(pages)} slides; speech: {words} words.")
    print(f"Output: {OUT}")
    print("Telegram link: " + ("included" if telegram else "awaiting user input"))


if __name__ == "__main__":
    main()
