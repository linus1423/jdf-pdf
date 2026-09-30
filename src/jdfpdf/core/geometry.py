"""Seitengeometrie: skalieren, Format ändern, Inhalt verschieben.

Der bestehende Seiteninhalt wird dabei nicht neu geschrieben, sondern in eine
Transformation eingeschlossen (``q … cm … Q``) bzw. als Form-XObject platziert.
"""

from __future__ import annotations

from enum import Enum

import pikepdf

from .pdfdoc import PdfDocument


class ScaleMode(str, Enum):
    FIT = "fit"  # vollständig sichtbar, ggf. Ränder
    FILL = "fill"  # Format ausfüllen, ggf. beschnitten
    ACTUAL = "actual"  # 100 %, zentriert
    STRETCH = "stretch"  # verzerrt auf das Format


def _wrap(doc: PdfDocument, index: int, matrix: tuple[float, ...]) -> None:
    """Seiteninhalt in ``q <matrix> cm … Q`` einschließen."""
    page = doc.pdf.pages[index]
    a, b, c, d, e, f = matrix
    page.contents_add(doc.pdf.make_stream(f"q {a:.6f} {b:.6f} {c:.6f} {d:.6f} {e:.4f} {f:.4f} cm\n".encode()),
                      prepend=True)
    page.contents_add(doc.pdf.make_stream(b"\nQ\n"), prepend=False)


def _reset_boxes(doc: PdfDocument, index: int, width: float, height: float) -> None:
    page = doc.pdf.pages[index].obj
    page.MediaBox = pikepdf.Array([0, 0, width, height])
    for name in ("/CropBox", "/BleedBox", "/ArtBox"):
        if name in page:
            del page[name]
    page.TrimBox = pikepdf.Array([0, 0, width, height])


def scale_page(doc: PdfDocument, index: int, width: float, height: float, mode: ScaleMode = ScaleMode.FIT) -> None:
    """Sichtbaren Bereich (CropBox) der Seite auf ``width``×``height`` pt bringen."""
    x0, y0, x1, y1 = doc.box(index, "CropBox")
    src_w, src_h = x1 - x0, y1 - y0
    # gedrehte Seiten: Drehung beibehalten, Zielformat entsprechend tauschen
    if doc.page_rotation(index) % 180:
        width, height = height, width
    sx, sy = width / src_w, height / src_h
    if mode == ScaleMode.FIT:
        sx = sy = min(sx, sy)
    elif mode == ScaleMode.FILL:
        sx = sy = max(sx, sy)
    elif mode == ScaleMode.ACTUAL:
        sx = sy = 1.0
    dx = (width - src_w * sx) / 2 - x0 * sx
    dy = (height - src_h * sy) / 2 - y0 * sy
    _wrap(doc, index, (sx, 0, 0, sy, dx, dy))
    _reset_boxes(doc, index, width, height)


def resize_canvas(doc: PdfDocument, index: int, width: float, height: float) -> None:
    """Format ändern ohne Skalierung; Inhalt bleibt zentriert."""
    scale_page(doc, index, width, height, ScaleMode.ACTUAL)


def shift_content(doc: PdfDocument, index: int, dx: float, dy: float) -> None:
    """Seiteninhalt um ``dx``/``dy`` pt verschieben (z. B. Bundzugabe)."""
    rotation = doc.page_rotation(index)
    # Verschiebung in Leserichtung angeben, auch bei gedrehten Seiten
    dx, dy = {0: (dx, dy), 90: (-dy, dx), 180: (-dx, -dy), 270: (dy, -dx)}[rotation]
    _wrap(doc, index, (1, 0, 0, 1, dx, dy))


def shift_for_binding(doc: PdfDocument, indices: list[int], gutter: float, edge: str = "left") -> None:
    """Bundzugabe: ungerade Seiten (1, 3, …) weg vom Bund, gerade gespiegelt (Duplex)."""
    for index in indices:
        odd = index % 2 == 0  # Seite 1 hat Index 0
        if edge == "left":
            shift_content(doc, index, gutter if odd else -gutter, 0)
        else:  # oben gebunden
            shift_content(doc, index, 0, -gutter if odd else gutter)
