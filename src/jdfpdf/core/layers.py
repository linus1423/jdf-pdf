"""Zusätzliche Inhaltsebenen auf Seiten (Stempel, Register, Marken …), die sich wieder entfernen lassen.

Jede Ebene ist ein Form-XObject, das über einen eigenen Inhaltsstrom gezeichnet
wird. Der Strom trägt im Stream-Dictionary ``/JdfpdfLayer /<art>``; so lassen
sich alle Elemente einer Art wieder entfernen, ohne den Originalinhalt anzufassen.
"""

from __future__ import annotations

import io
import itertools
import os
from functools import lru_cache
from typing import Callable

import pikepdf
from pikepdf import Name
from reportlab.lib.colors import CMYKColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from .pdfdoc import PdfDocument

LAYER_KEY = Name("/JdfpdfLayer")
_counter = itertools.count(1)


@lru_cache(maxsize=None)
def font(bold: bool = False) -> str:
    """Eingebettete Schrift (Bitstream Vera aus reportlab, freie Lizenz) registrieren."""
    import reportlab

    name = "JdfVeraBd" if bold else "JdfVera"
    path = os.path.join(os.path.dirname(reportlab.__file__), "fonts", "VeraBd.ttf" if bold else "Vera.ttf")
    pdfmetrics.registerFont(TTFont(name, path))
    return name


def cmyk(c: float, m: float, y: float, k: float, alpha: float = 1.0) -> CMYKColor:
    """CMYK in Prozent (0–100)."""
    return CMYKColor(c / 100, m / 100, y / 100, k / 100, alpha=alpha)


def draw_overlay(width: float, height: float, draw: Callable[[Canvas], None]) -> pikepdf.Pdf:
    """Einseitiges PDF der Größe ``width``×``height`` mit reportlab zeichnen."""
    buf = io.BytesIO()
    canvas = Canvas(buf, pagesize=(width, height), pageCompression=1)
    canvas.setPageCompression(1)
    draw(canvas)
    canvas.showPage()
    canvas.save()
    return pikepdf.open(io.BytesIO(buf.getvalue()))


def add_layer(doc: PdfDocument, index: int, kind: str, overlay: pikepdf.Pdf,
              matrix: tuple[float, ...] = (1, 0, 0, 1, 0, 0), underlay: bool = False) -> None:
    """Erste Seite von ``overlay`` als Ebene ``kind`` auf Seite ``index`` legen.

    ``matrix`` bildet Overlay-Koordinaten auf Seitenkoordinaten (ungedreht) ab.
    """
    page = doc.pdf.pages[index]
    xobj = doc.pdf.copy_foreign(overlay.pages[0].as_form_xobject())
    name = Name(f"/JdfL{next(_counter)}")
    # Ressourcen können mit anderen Seiten geteilt sein (z. B. duplizierte Seiten): kopieren
    resources = page.obj.get("/Resources")
    page.obj.Resources = resources = pikepdf.Dictionary(resources) if resources is not None else pikepdf.Dictionary()
    xobjects = resources.get("/XObject")
    resources.XObject = pikepdf.Dictionary(xobjects) if xobjects is not None else pikepdf.Dictionary()
    resources.XObject[name] = xobj
    cm = " ".join(f"{v:.4f}" for v in matrix)
    stream = doc.pdf.make_stream(f"q {cm} cm {name} Do Q\n".encode())
    stream[LAYER_KEY] = Name("/" + kind)
    if underlay:
        page.contents_add(stream, prepend=True)
    else:
        # Originalinhalt in q/Q einschließen, damit sein Grafikzustand die Ebene nicht verschiebt
        _isolate_original(doc, page)
        page.contents_add(stream, prepend=False)


def _isolate_original(doc: PdfDocument, page: pikepdf.Page) -> None:
    contents = page.obj.get("/Contents")
    streams = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
    if streams and streams[0].get(LAYER_KEY) == Name("/_open"):
        return
    opener = doc.pdf.make_stream(b"q\n")
    opener[LAYER_KEY] = Name("/_open")
    closer = doc.pdf.make_stream(b"\nQ\n")
    closer[LAYER_KEY] = Name("/_close")
    page.obj.Contents = pikepdf.Array([opener, *streams, closer])


def visual_matrix(doc: PdfDocument, index: int) -> tuple[tuple[float, ...], float, float]:
    """Matrix, die ein Overlay in *sichtbarer* Seitenlage (CropBox, inkl. /Rotate) platziert.

    Liefert (Matrix, sichtbare Breite, sichtbare Höhe). Das Overlay wird so groß wie
    die sichtbare Seite gezeichnet; Ursprung unten links der sichtbaren Seite.
    """
    x0, y0, x1, y1 = doc.box(index, "CropBox")
    w, h = x1 - x0, y1 - y0
    rotation = doc.page_rotation(index)
    if rotation == 0:
        return (1, 0, 0, 1, x0, y0), w, h
    if rotation == 90:  # Seite wird im Uhrzeigersinn gedreht angezeigt
        return (0, 1, -1, 0, x1, y0), h, w
    if rotation == 180:
        return (-1, 0, 0, -1, x1, y1), w, h
    return (0, -1, 1, 0, x0, y1), h, w


def remove_layers(doc: PdfDocument, index: int, kind: str | None = None) -> int:
    """Ebenen einer Art (oder alle) von einer Seite entfernen; liefert die Anzahl."""
    page = doc.pdf.pages[index]
    contents = page.obj.get("/Contents")
    if not isinstance(contents, pikepdf.Array):
        return 0
    keep, removed = [], 0
    for stream in contents:
        tag = stream.get(LAYER_KEY)
        if tag is not None and tag not in (Name("/_open"), Name("/_close")) and (kind is None or tag == Name("/" + kind)):
            removed += 1
            continue
        keep.append(stream)
    if not any(s.get(LAYER_KEY) not in (None, Name("/_open"), Name("/_close")) for s in keep):
        keep = [s for s in keep if s.get(LAYER_KEY) not in (Name("/_open"), Name("/_close"))]
    page.obj.Contents = pikepdf.Array(keep)
    return removed


def layer_kinds(doc: PdfDocument, index: int) -> list[str]:
    contents = doc.pdf.pages[index].obj.get("/Contents")
    if not isinstance(contents, pikepdf.Array):
        return []
    kinds = []
    for stream in contents:
        tag = stream.get(LAYER_KEY)
        if tag is not None and str(tag) not in ("/_open", "/_close") and str(tag)[1:] not in kinds:
            kinds.append(str(tag)[1:])
    return kinds
