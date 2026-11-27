"""Shared editable slide elements and PPTX/PDF/PNG rendering."""

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
