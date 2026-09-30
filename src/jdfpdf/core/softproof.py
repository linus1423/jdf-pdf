"""Softproof: Vorschau-PDF des Endprodukts für den Kunden.

Jede Seite wird auf das Endformat beschnitten auf einer grauen Arbeitsfläche
gezeigt, auf der Medienfarbe (Multiplizieren simuliert farbiges Papier), mit
Registerblättern in Blattform, Heftklammern, Lochung und Falzlinien. Optional als
Doppelseiten (gebundenes Produkt) oder als ausgeschossene Bögen, mit Deckblatt
(Auftragsdaten) und Wasserzeichen. Der Proof ist nicht farbverbindlich und kein
Druck-PDF.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pikepdf
from pikepdf import Name

from . import elements
from .finishing_shapes import HOLE_RADIUS, STAPLE_LENGTH, finishing_shapes
from .impose import Imposition, Layout, _mul, _to_visual, impose
from .jdf import Fold, JobTicket, Punch, Sides, Staple
from .layers import draw_overlay, font
from .media import MEDIA_RGB, Media
from .pdfdoc import PdfDocument
from .tabs import TAB_KEY

MM = 72 / 25.4
PASTEBOARD = (0.82, 0.82, 0.84)
PAD = 10 * MM


@dataclass
class ProofOptions:
    media_color: bool = True
    finishing: bool = True
    tabs: bool = True  # Registerblätter in Blattform
    trim: bool = True  # auf Endformat (TrimBox) beschneiden, sonst CropBox
    spreads: bool = False  # Doppelseiten wie im gebundenen Produkt
    sheets: bool = False  # ausgeschossene Bögen statt Endprodukt
    info_page: bool = True
    watermark: str = ""  # z. B. "PROOF"
    language: str = "de"


def media_for_page(ticket: JobTicket, index: int) -> Media | None:
    for rng in reversed(ticket.media_ranges):
        if rng.first <= index <= rng.last:
            return rng.media
    return ticket.media


def _rgb(media: Media | None) -> tuple[float, float, float]:
    value = MEDIA_RGB.get((media.color or "").lower(), "#ffffff") if media else "#ffffff"
    return tuple(int(value[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _fmt(values) -> str:
    return " ".join(f"{v:.4f}" for v in values)


@dataclass
class _Cell:
    page: int
    box: tuple[float, float, float, float]
    width: float
    height: float
    shape: list[tuple[float, float, float, float]]  # Rechtecke der Blattform (sichtbare Koordinaten)


def _cell(doc: PdfDocument, index: int, opts: ProofOptions) -> _Cell:
    box = doc.box(index, "TrimBox" if opts.trim else "CropBox")
    rotation = doc.page_rotation(index)
    w, h = box[2] - box[0], box[3] - box[1]
    vw, vh = (h, w) if rotation % 180 else (w, h)
    shape = [(0, 0, vw, vh)]
    tab = doc.pdf.pages[index].obj.get(TAB_KEY)
    if opts.tabs and tab is not None and rotation == 0:
        tx0, ty0, tx1, ty1 = (float(v) - o for v, o in zip(tab, (box[0], box[1], box[0], box[1])))
        body = (tx1, 0, vw, vh) if tx0 <= 0.5 else (0, 0, tx0, vh)
        shape = [body, (tx0, ty0, tx1, ty1)]
    return _Cell(index, box, vw, vh, shape)


def _path(rects, dx: float, dy: float) -> str:
    return " ".join(f"{x0 + dx:.3f} {y0 + dy:.3f} {x1 - x0:.3f} {y1 - y0:.3f} re" for x0, y0, x1, y1 in rects)


def _finishing_ops(cell: _Cell, ticket: JobTicket, dx: float, dy: float, back: bool) -> list[str]:
    ops = []
    trim = (dx, dy, dx + cell.width, dy + cell.height)
    for shape in finishing_shapes(trim, ticket.finishing, 1 if back else 0):
        if shape.kind == "staple":
            half, thick = STAPLE_LENGTH / 2, 0.8
            angle = -math.radians(shape.angle)  # Bildschirm (y nach unten) → PDF
            if not shape.horizontal:
                angle += math.pi / 2
            c, s = math.cos(angle), math.sin(angle)
            ops.append(f"q 0.35 0.35 0.38 rg {_fmt((c, s, -s, c, shape.x, shape.y))} cm "
                       f"{-half:.3f} {-thick:.3f} {2 * half:.3f} {2 * thick:.3f} re f Q")
        elif shape.kind == "hole":
            r, k = HOLE_RADIUS, HOLE_RADIUS * 0.5523
            x, y = shape.x, shape.y
            ops.append(f"q {_fmt(PASTEBOARD)} rg 0.6 0.6 0.6 RG 0.3 w {x + r:.3f} {y:.3f} m "
                       f"{x + r:.3f} {y + k:.3f} {x + k:.3f} {y + r:.3f} {x:.3f} {y + r:.3f} c "
                       f"{x - k:.3f} {y + r:.3f} {x - r:.3f} {y + k:.3f} {x - r:.3f} {y:.3f} c "
                       f"{x - r:.3f} {y - k:.3f} {x - k:.3f} {y - r:.3f} {x:.3f} {y - r:.3f} c "
                       f"{x + k:.3f} {y - r:.3f} {x + r:.3f} {y - k:.3f} {x + r:.3f} {y:.3f} c b Q")
        else:
            ops.append(f"q 0.55 0.55 0.55 RG 0.4 w [4 3] 0 d {shape.x:.3f} {shape.y:.3f} m "
                       f"{shape.x2:.3f} {shape.y2:.3f} l S Q")
    return ops


def _groups(count: int, spreads: bool) -> list[list[int | None]]:
    if not spreads:
        return [[i] for i in range(count)]
    groups: list[list[int | None]] = [[None, 0]] if count else []
    for start in range(1, count, 2):
        groups.append([start, start + 1 if start + 1 < count else None])
    return groups


def _build_pages(doc: PdfDocument, ticket: JobTicket, opts: ProofOptions, sheet_media: Media | None = None) -> pikepdf.Pdf:
    out = pikepdf.new()
    cache: dict[int, pikepdf.Object] = {}
    gs = out.make_indirect(pikepdf.Dictionary(Type=Name.ExtGState, BM=Name.Multiply))
    duplex = ticket.sides != Sides.SIMPLEX
    cells = [_cell(doc, i, opts) for i in range(doc.page_count)]
    for group in _groups(doc.page_count, opts.spreads):
        present = [cells[i] for i in group if i is not None]
        slot_w = max(c.width for c in present)
        height = max(c.height for c in present)
        page_w = 2 * PAD + slot_w * len(group)
        page_h = 2 * PAD + height
        content = [f"q {_fmt(PASTEBOARD)} rg 0 0 {page_w:.3f} {page_h:.3f} re f Q"]
        resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(), ExtGState=pikepdf.Dictionary(GSm=gs))
        for slot, index in enumerate(group):
            if index is None:
                continue
            cell = cells[index]
            # linke Seite einer Doppelseite an den Bund (rechts) schieben
            dx = PAD + slot * slot_w + (slot_w - cell.width if len(group) > 1 and slot == 0 else 0)
            dy = PAD + (height - cell.height)
            if index not in cache:
                xobj = doc.pdf.pages[index].as_form_xobject(False)
                cache[index] = out.copy_foreign(xobj)
            name = Name(f"/P{index}")
            resources.XObject[name] = cache[index]
            media = sheet_media if sheet_media is not None else media_for_page(ticket, index)
            color = _rgb(media) if opts.media_color else (1.0, 1.0, 1.0)
            matrix = _mul(_to_visual(cell.box, doc.page_rotation(index)), (1, 0, 0, 1, dx, dy))
            clip = _path(cell.shape, dx, dy)
            content.append(f"q {clip} W n {_fmt(color)} rg {clip} f "
                           f"q /GSm gs {_fmt(matrix)} cm {name} Do Q Q")
            content.append(f"q 0.6 0.6 0.6 RG 0.3 w {clip} S Q")
            if opts.finishing and sheet_media is None:
                back = duplex and index % 2 == 1
                content.extend(_finishing_ops(cell, ticket, dx, dy, back))
        page = pikepdf.Dictionary(Type=Name.Page, MediaBox=pikepdf.Array([0, 0, page_w, page_h]),
                                  Resources=resources, Contents=out.make_stream("\n".join(content).encode()))
        out.pages.append(pikepdf.Page(out.make_indirect(page)))
    return out


_LABELS = {
    "de": {"title": "Softproof", "note": "Bildschirmvorschau, nicht farbverbindlich.", "job": "Auftrag",
           "customer": "Kunde", "copies": "Auflage", "pages": "Seiten", "sides": "Druckseiten",
           "media": "Medien", "finishing": "Weiterverarbeitung", "date": "Datum", "comment": "Bemerkung",
           "none": "keine", "simplex": "einseitig", "duplex": "beidseitig", "pages_of": "Seiten {0}–{1}",
           "staple": "Heftung", "punch": "Lochung", "fold": "Falz", "trim": "Beschnitt"},
    "en": {"title": "Soft proof", "note": "Screen preview, not colour accurate.", "job": "Job",
           "customer": "Customer", "copies": "Copies", "pages": "Pages", "sides": "Sides",
           "media": "Media", "finishing": "Finishing", "date": "Date", "comment": "Comment",
           "none": "none", "simplex": "simplex", "duplex": "duplex", "pages_of": "pages {0}–{1}",
           "staple": "Stapling", "punch": "Punching", "fold": "Folding", "trim": "Trimming"},
}


def _info_page(doc: PdfDocument, ticket: JobTicket, opts: ProofOptions) -> pikepdf.Pdf:
    t = _LABELS.get(opts.language, _LABELS["de"])
    fin = ticket.finishing
    finishing = [f"{t['staple']}: {fin.staple.value}" if fin.staple != Staple.NONE else "",
                 f"{t['punch']}: {fin.punch.value}" if fin.punch != Punch.NONE else "",
                 f"{t['fold']}: {fin.fold.value}" if fin.fold != Fold.NONE else "",
                 t["trim"] if fin.trim else ""]
    media = [f"{ticket.media.label()}" if ticket.media else ""]
    media += [f"{t['pages_of'].format(r.first + 1, r.last + 1)}: {r.media.label()}" for r in ticket.media_ranges]
    rows = [(t["job"], ticket.job_name), (t["customer"], ticket.customer or ""), (t["copies"], str(ticket.copies)),
            (t["pages"], str(doc.page_count)),
            (t["sides"], t["simplex"] if ticket.sides == Sides.SIMPLEX else t["duplex"]),
            (t["media"], "; ".join(m for m in media if m) or "–"),
            (t["finishing"], ", ".join(f for f in finishing if f) or t["none"]),
            (t["comment"], ticket.comment or ""), (t["date"], date.today().strftime("%d.%m.%Y"))]

    def draw(c) -> None:
        width, height = 210 * MM, 297 * MM
        c.setFont(font(True), 22)
        c.drawString(25 * MM, height - 35 * MM, t["title"])
        c.setFont(font(), 10)
        c.drawString(25 * MM, height - 43 * MM, t["note"])
        y = height - 60 * MM
        for label, value in rows:
            if not value:
                continue
            c.setFont(font(True), 10)
            c.drawString(25 * MM, y, label)
            c.setFont(font(), 10)
            c.drawString(70 * MM, y, value[:90])
            y -= 7 * MM
        c.setStrokeGray(0.7)
        c.line(25 * MM, 20 * MM, width - 25 * MM, 20 * MM)

    return draw_overlay(210 * MM, 297 * MM, draw)


def build_proof(doc: PdfDocument, ticket: JobTicket, opts: ProofOptions | None = None,
                imposition: Imposition | None = None) -> PdfDocument:
    """Softproof erzeugen; ``doc`` bleibt unverändert."""
    opts = opts or ProofOptions()
    source = doc
    sheet_media = None
    if opts.sheets and imposition is not None and imposition.layout != Layout.NONE:
        source = impose(PdfDocument.from_bytes(doc.to_bytes()), imposition)
        sheet_media = ticket.media or Media("")
        opts = ProofOptions(**{**opts.__dict__, "trim": False, "spreads": False})
    pdf = _build_pages(source, ticket, opts, sheet_media)
    if opts.info_page:
        info = _info_page(doc, ticket, opts)
        pdf.pages.insert(0, info.pages[0])
    buf = io.BytesIO()
    pdf.save(buf)
    result = PdfDocument.from_bytes(buf.getvalue())
    if opts.watermark:
        first = 1 if opts.info_page else 0
        mark = elements.watermark(opts.watermark, color_cmyk=(0, 80, 80, 0), opacity=0.18, font_size=60)
        elements.apply_element(result, list(range(first, result.page_count)), mark)
    return result


def write_proof(doc: PdfDocument, ticket: JobTicket, path: str | Path, opts: ProofOptions | None = None,
                imposition: Imposition | None = None) -> Path:
    path = Path(path)
    build_proof(doc, ticket, opts, imposition).save(path)
    return path
