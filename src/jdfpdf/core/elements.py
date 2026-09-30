"""Seitenelemente: Kopf-/Fußzeilen, Stempel, Wasserzeichen, Logos/Bilder, Seitenzahlen.

Elemente werden als eigene Ebene (``core.layers``) gezeichnet und lassen sich
mit ``remove_elements`` wieder entfernen. Positionen gelten für die sichtbare
Seite (inkl. Drehung), bezogen auf die TrimBox bzw. CropBox.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

from reportlab.lib.utils import ImageReader

from .layers import add_layer, cmyk, draw_overlay, font, remove_layers, visual_matrix
from .pdfdoc import PdfDocument

MM = 72 / 25.4
LAYER = "element"


class Anchor(str, Enum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


@dataclass
class Element:
    """Text oder Bild an einer Ankerposition.

    Text darf Platzhalter enthalten: ``{page}``, ``{pages}``, ``{date}``, ``{file}``,
    ``{section}``, ``{job}``.
    """

    text: str = ""
    image: str | None = None  # Pfad zu einem Bild; dann wird statt Text das Bild gesetzt
    anchor: Anchor = Anchor.BOTTOM_CENTER
    offset_x_mm: float = 0.0
    offset_y_mm: float = 10.0  # Abstand vom Rand (bei Mitte: Verschiebung)
    font_size: float = 10.0
    bold: bool = False
    color_cmyk: tuple[float, float, float, float] = (0, 0, 0, 100)
    opacity: float = 1.0
    rotation: float = 0.0
    image_width_mm: float = 30.0
    mirror_even: bool = False  # bei geraden Seiten links/rechts tauschen (Duplex)


def header(text: str, **kw) -> Element:
    return Element(text=text, anchor=Anchor.TOP_CENTER, **kw)


def footer(text: str, **kw) -> Element:
    return Element(text=text, anchor=Anchor.BOTTOM_CENTER, **kw)


def page_numbers(fmt: str = "{page} / {pages}", anchor: Anchor = Anchor.BOTTOM_RIGHT, **kw) -> Element:
    return Element(text=fmt, anchor=anchor, mirror_even=True, **kw)


def watermark(text: str, **kw) -> Element:
    params = dict(anchor=Anchor.CENTER, offset_y_mm=0, font_size=72, bold=True, opacity=0.15,
                  rotation=45, color_cmyk=(0, 0, 0, 60))
    params.update(kw)
    return Element(text=text, **params)


def stamp(text: str, **kw) -> Element:
    params = dict(anchor=Anchor.TOP_RIGHT, offset_x_mm=15, offset_y_mm=15, font_size=18, bold=True,
                  color_cmyk=(0, 100, 100, 0), rotation=-15)
    params.update(kw)
    return Element(text=text, **params)


@dataclass
class Context:
    job: str = ""
    file: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def _expand(text: str, doc: PdfDocument, index: int, ctx: Context) -> str:
    section = doc.section_of_page(index)
    values = {
        "page": str(index + 1),
        "pages": str(doc.page_count),
        "date": date.today().strftime("%d.%m.%Y"),
        "file": ctx.file,
        "job": ctx.job,
        "section": section.title if section else "",
        **ctx.extra,
    }
    try:
        return text.format(**values)
    except (KeyError, IndexError, ValueError):
        return text


def _trim_in_visual(doc: PdfDocument, index: int, vw: float, vh: float) -> tuple[float, float, float, float]:
    """TrimBox in Koordinaten der sichtbaren Seite (Ursprung unten links der CropBox)."""
    cx0, cy0, cx1, cy1 = doc.box(index, "CropBox")
    tx0, ty0, tx1, ty1 = doc.box(index, "TrimBox")
    left, bottom, right, top = tx0 - cx0, ty0 - cy0, cx1 - tx1, cy1 - ty1
    rotation = doc.page_rotation(index)
    # Ränder mitdrehen
    for _ in range(rotation // 90):
        left, bottom, right, top = bottom, right, top, left
    return left, bottom, vw - right, vh - top


_ANCHOR_PARTS = {
    Anchor.TOP_LEFT: ("top", "left"), Anchor.TOP_CENTER: ("top", "center"), Anchor.TOP_RIGHT: ("top", "right"),
    Anchor.CENTER_LEFT: ("center", "left"), Anchor.CENTER: ("center", "center"),
    Anchor.CENTER_RIGHT: ("center", "right"), Anchor.BOTTOM_LEFT: ("bottom", "left"),
    Anchor.BOTTOM_CENTER: ("bottom", "center"), Anchor.BOTTOM_RIGHT: ("bottom", "right"),
}


def _position(anchor: Anchor, box, dx: float, dy: float, mirror: bool) -> tuple[float, float, str]:
    """Ankerpunkt in ``box``; ``dx``/``dy`` sind Abstände vom Rand (bzw. Verschiebung bei Mitte)."""
    x0, y0, x1, y1 = box
    vertical, horizontal = _ANCHOR_PARTS[anchor]
    if mirror:
        horizontal = {"left": "right", "right": "left"}.get(horizontal, horizontal)
    if horizontal == "left":
        x = x0 + dx
    elif horizontal == "right":
        x = x1 - dx
    else:
        x = (x0 + x1) / 2 + (-dx if mirror else dx)
    if vertical == "top":
        y = y1 - dy
    elif vertical == "bottom":
        y = y0 + dy
    else:
        y = (y0 + y1) / 2 + dy
    return x, y, horizontal


def apply_element(doc: PdfDocument, indices: list[int], element: Element, ctx: Context | None = None) -> None:
    ctx = ctx or Context()
    for index in indices:
        matrix, vw, vh = visual_matrix(doc, index)
        box = _trim_in_visual(doc, index, vw, vh)
        mirror = element.mirror_even and index % 2 == 1
        x, y, align = _position(element.anchor, box, element.offset_x_mm * MM, element.offset_y_mm * MM, mirror)
        text = _expand(element.text, doc, index, ctx)

        def draw(c) -> None:
            color = cmyk(*element.color_cmyk, alpha=element.opacity)
            c.setFillColor(color)
            c.setStrokeColor(color)
            c.translate(x, y)
            c.rotate(element.rotation)
            if element.image:
                reader = ImageReader(element.image)
                iw, ih = reader.getSize()
                width = element.image_width_mm * MM
                height = width * ih / iw
                ox = {"left": 0, "right": -width, "center": -width / 2}[align]
                if element.opacity < 1:
                    c.setFillAlpha(element.opacity)
                c.drawImage(reader, ox, -height / 2, width, height, mask="auto")
            else:
                name = font(element.bold)
                c.setFont(name, element.font_size)
                # vertikal auf Schriftmitte ausrichten
                baseline = -element.font_size * 0.35
                {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[align](
                    0, baseline, text
                )

        add_layer(doc, index, LAYER, draw_overlay(vw, vh, draw), matrix)


def remove_elements(doc: PdfDocument, indices: list[int]) -> int:
    return sum(remove_layers(doc, i, LAYER) for i in indices)


def image_element(path: str | Path, anchor: Anchor = Anchor.TOP_LEFT, width_mm: float = 30, **kw) -> Element:
    return Element(image=str(path), anchor=anchor, image_width_mm=width_mm, **kw)
