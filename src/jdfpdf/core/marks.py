"""Druckmarken und Bogen-Barcodes.

Marken werden außerhalb der TrimBox gezeichnet. Reicht der Rand nicht, wird die
MediaBox um ``margin_mm`` um die TrimBox vergrößert. Passermarken und
Schnittmarken stehen in der Registerfarbe (Separation „All“).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.colors import CMYKColorSep

from .layers import add_layer, cmyk, draw_overlay, font, remove_layers
from .pdfdoc import PdfDocument

MM = 72 / 25.4
LAYER = "marks"


class BarcodeType(str, Enum):
    NONE = "none"
    CODE128 = "code128"
    QR = "qr"


@dataclass
class MarkOptions:
    crop: bool = True
    bleed: bool = False
    registration: bool = True
    color_bar: bool = False
    info: bool = True
    fold_x_mm: list[float] = field(default_factory=list)  # senkrechte Falzlinien, ab linker Formatkante
    fold_y_mm: list[float] = field(default_factory=list)  # waagerechte Falzlinien, ab unterer Formatkante
    margin_mm: float = 12.0  # Platz für Marken um die TrimBox
    offset_mm: float = 3.0  # Abstand der Schnittmarken vom Format
    length_mm: float = 5.0
    line_width_pt: float = 0.25
    barcode: BarcodeType = BarcodeType.NONE
    barcode_text: str = "{job}-{page}"  # Platzhalter: {job} {page} {pages} {file} {finishing}
    info_text: str = "{file}  ·  {page}/{pages}  ·  {date}"


REGISTRATION = CMYKColorSep(1, 1, 1, 1, spotName="All")


def _fmt(template: str, index: int, doc: PdfDocument, ctx: dict[str, str]) -> str:
    values = {"page": index + 1, "pages": doc.page_count, "date": date.today().strftime("%d.%m.%Y"),
              "job": "", "file": "", "finishing": "", **ctx}
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def ensure_margin(doc: PdfDocument, index: int, margin: float) -> None:
    """TrimBox explizit setzen und MediaBox so erweitern, dass ``margin`` rundum Platz ist."""
    trim = doc.box(index, "TrimBox")
    doc.set_box(index, "TrimBox", trim)
    mx0, my0, mx1, my1 = doc.box(index, "MediaBox")
    tx0, ty0, tx1, ty1 = trim
    media = (min(mx0, tx0 - margin), min(my0, ty0 - margin), max(mx1, tx1 + margin), max(my1, ty1 + margin))
    doc.set_box(index, "MediaBox", media)
    if doc.has_box(index, "CropBox"):
        doc.set_box(index, "CropBox", None)


def add_marks(doc: PdfDocument, indices: list[int], opts: MarkOptions | None = None,
              ctx: dict[str, str] | None = None) -> None:
    opts = opts or MarkOptions()
    ctx = ctx or {}
    for index in indices:
        ensure_margin(doc, index, opts.margin_mm * MM)
        _draw_marks(doc, index, opts, ctx)


def _draw_marks(doc: PdfDocument, index: int, opts: MarkOptions, ctx: dict[str, str]) -> None:
    mx0, my0, mx1, my1 = doc.box(index, "MediaBox")
    tx0, ty0, tx1, ty1 = (v for v in doc.box(index, "TrimBox"))
    bleed = doc.box(index, "BleedBox") if doc.has_box(index, "BleedBox") else None
    off, length = opts.offset_mm * MM, opts.length_mm * MM
    # Koordinaten relativ zur MediaBox
    tx0, tx1, ty0, ty1 = tx0 - mx0, tx1 - mx0, ty0 - my0, ty1 - my0
    width, height = mx1 - mx0, my1 - my0

    def draw(c) -> None:
        c.setLineWidth(opts.line_width_pt)
        c.setStrokeColor(REGISTRATION)
        c.setFillColor(REGISTRATION)
        if opts.crop:
            for x, dx in ((tx0, -1), (tx1, 1)):
                for y, dy in ((ty0, -1), (ty1, 1)):
                    c.line(x + dx * off, y, x + dx * (off + length), y)
                    c.line(x, y + dy * off, x, y + dy * (off + length))
        if opts.bleed and bleed is not None:
            bx0, by0, bx1, by1 = bleed[0] - mx0, bleed[1] - my0, bleed[2] - mx0, bleed[3] - my0
            c.setDash(2, 2)
            for x, dx in ((bx0, -1), (bx1, 1)):
                for y, dy in ((by0, -1), (by1, 1)):
                    c.line(x + dx * off, y, x + dx * (off + length / 2), y)
                    c.line(x, y + dy * off, x, y + dy * (off + length / 2))
            c.setDash()
        if opts.registration:
            r = min(length, opts.margin_mm * MM - off) / 2
            for cx, cy in (((tx0 + tx1) / 2, ty1 + off + r), ((tx0 + tx1) / 2, ty0 - off - r),
                           (tx0 - off - r, (ty0 + ty1) / 2), (tx1 + off + r, (ty0 + ty1) / 2)):
                c.circle(cx, cy, r * 0.6, stroke=1, fill=0)
                c.line(cx - r, cy, cx + r, cy)
                c.line(cx, cy - r, cx, cy + r)
        if opts.fold_x_mm or opts.fold_y_mm:
            c.setDash(3, 2)
            for fx in opts.fold_x_mm:
                x = tx0 + fx * MM
                c.line(x, ty1 + off, x, ty1 + off + length)
                c.line(x, ty0 - off, x, ty0 - off - length)
            for fy in opts.fold_y_mm:
                y = ty0 + fy * MM
                c.line(tx0 - off, y, tx0 - off - length, y)
                c.line(tx1 + off, y, tx1 + off + length, y)
            c.setDash()
        if opts.color_bar:
            size = min(4 * MM, opts.margin_mm * MM - off - 1)
            x = tx0 + 10 * MM
            y = ty0 - off - size
            patches = [(100, 0, 0, 0), (0, 100, 0, 0), (0, 0, 100, 0), (0, 0, 0, 100),
                       (50, 0, 0, 0), (0, 50, 0, 0), (0, 0, 50, 0), (0, 0, 0, 50),
                       (100, 100, 0, 0), (0, 100, 100, 0), (100, 0, 100, 0)]
            for patch in patches:
                c.setFillColor(cmyk(*patch))
                c.rect(x, y, size, size, stroke=0, fill=1)
                x += size
        if opts.info:
            c.setFillColor(REGISTRATION)
            c.setFont(font(), 6)
            c.drawString(tx0 + off + length + 2 * MM, ty1 + off + 1, _fmt(opts.info_text, index, doc, ctx))
        if opts.barcode != BarcodeType.NONE:
            value = _fmt(opts.barcode_text, index, doc, ctx)
            space = opts.margin_mm * MM - off
            if opts.barcode == BarcodeType.CODE128:
                bar = code128.Code128(value, barHeight=space * 0.6, barWidth=0.6, humanReadable=False)
                bar.drawOn(c, tx1 - bar.width - 10 * MM, ty0 - off - space * 0.7)
            else:
                size = space * 0.9
                widget = qr.QrCodeWidget(value)
                x0b, y0b, x1b, y1b = widget.getBounds()
                drawing = Drawing(size, size, transform=[size / (x1b - x0b), 0, 0, size / (y1b - y0b), 0, 0])
                drawing.add(widget)
                renderPDF.draw(drawing, c, tx1 - size - 10 * MM, ty0 - off - size)

    add_layer(doc, index, LAYER, draw_overlay(width, height, draw), (1, 0, 0, 1, mx0, my0))


def remove_marks(doc: PdfDocument, indices: list[int]) -> int:
    return sum(remove_layers(doc, i, LAYER) for i in indices)
