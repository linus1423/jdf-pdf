"""Ausschießen: Seiten auf Druckbögen anordnen.

Layouts: n-up (fortlaufend), Step & Repeat (Nutzen gleich), Schneiden & Stapeln,
Broschüre (Rückstich) mit Creep-Kompensation, Multi-Broschüre/Signaturen,
Klebebindung mit Umschlag und tonerfreier Klebezone.

Die Quellseiten werden als Form-XObjects (BBox = BleedBox) platziert; ihr Inhalt
wird nicht verändert. Maßgeblich ist die TrimBox (Endformat) jeder Seite.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from enum import Enum

import pikepdf
from pikepdf import Name

from .pdfdoc import PdfDocument

MM = 72 / 25.4
Matrix = tuple[float, float, float, float, float, float]


class Layout(str, Enum):
    NONE = "none"
    NUP = "nup"  # fortlaufend n Seiten pro Bogenseite
    REPEAT = "repeat"  # Step & Repeat: jede Seite n-mal (Nutzen gleich)
    CUT_STACK = "cut_stack"  # Schneiden & Stapeln
    BOOKLET = "booklet"  # Rückstichbroschüre, 2 Seiten nebeneinander
    MULTI_BOOKLET = "multi_booklet"  # mehrere Broschüren/Signaturen hintereinander
    PERFECT_BOUND = "perfect_bound"  # Klebebindung: Inhalt 1-up + Umschlag mit Rücken


class BackFlip(str, Enum):
    LONG_EDGE = "long_edge"  # Umschlagen um die lange Kante
    SHORT_EDGE = "short_edge"  # um die kurze Kante


@dataclass
class Imposition:
    layout: Layout = Layout.NONE
    sheet_width_mm: float = 320.0  # SRA3
    sheet_height_mm: float = 450.0
    auto_sheet: bool = False  # Bogen = kleinstes Format, das die Nutzen fasst (Broschüre: 2 × Seite)
    cols: int = 2
    rows: int = 2
    gap_mm: float = 0.0  # Abstand zwischen den Nutzen
    bleed_mm: float = 0.0  # sichtbarer Beschnitt je Nutzen (begrenzt durch Abstand)
    rotate: bool = False  # Nutzen um 90° drehen
    duplex: bool = True
    back_flip: BackFlip = BackFlip.LONG_EDGE
    crop_marks: bool = False
    # Broschüre
    creep_mm: float | None = None  # Gesamt-Creep; None: aus Papierdicke berechnet
    paper_thickness_mm: float = 0.1
    sheets_per_booklet: int = 4  # Multi-Broschüre/Signatur: Bögen je Heft
    # Klebebindung
    spine_mm: float = 0.0
    glue_zone_mm: float = 0.0  # tonerfreie Klebezone am Bund des Inhalts
    cover_front: int | None = None  # Seitenindex für Umschlag vorne (None: keine Umschlagseite)
    cover_back: int | None = None

    @property
    def per_side(self) -> int:
        return self.cols * self.rows


@dataclass
class Placement:
    page: int | None  # None = leer
    x: float  # untere linke Ecke der Zelle (Endformat) auf dem Bogen
    y: float
    rotation: int = 0  # zusätzliche Drehung 0/90/180/270
    shift_x: float = 0.0  # Creep/Bundverschiebung
    clip: tuple[float, float, float, float] | None = None  # Clip-Rechteck auf dem Bogen


@dataclass
class SheetSide:
    width: float
    height: float
    placements: list[Placement] = field(default_factory=list)
    cut_lines: list[tuple[float, float, float, float]] = field(default_factory=list)  # Zellen für Marken


# --- Matrizen ---------------------------------------------------------------


def _mul(m1: Matrix, m2: Matrix) -> Matrix:
    """Erst m1, dann m2 anwenden (PDF-Konvention p' = p · m1 · m2)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2, c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2, e1 * b2 + f1 * d2 + f2,
    )


def _to_visual(trim: tuple[float, float, float, float], rotation: int) -> Matrix:
    """Seitenkoordinaten → sichtbare Lage mit Ursprung unten links des Endformats."""
    x0, y0, x1, y1 = trim
    return {
        0: (1, 0, 0, 1, -x0, -y0),
        90: (0, -1, 1, 0, -y0, x1),
        180: (-1, 0, 0, -1, x1, y1),
        270: (0, 1, -1, 0, y1, -x0),
    }[rotation % 360]


def _place(w: float, h: float, rotation: int, x: float, y: float) -> Matrix:
    """Sichtbare Seite (w×h) um ``rotation`` gedreht in die Zelle bei (x, y) legen."""
    return {
        0: (1, 0, 0, 1, x, y),
        90: (0, 1, -1, 0, x + h, y),
        180: (-1, 0, 0, -1, x + w, y + h),
        270: (0, -1, 1, 0, x, y + w),
    }[rotation % 360]


# --- Seitenmaße -------------------------------------------------------------


def visual_trim(doc: PdfDocument, index: int) -> tuple[float, float]:
    x0, y0, x1, y1 = doc.box(index, "TrimBox")
    w, h = x1 - x0, y1 - y0
    return (h, w) if doc.page_rotation(index) % 180 else (w, h)


# --- Layouts ----------------------------------------------------------------


def _grid(imp: Imposition, cell_w: float, cell_h: float, sheet_w: float, sheet_h: float):
    gap = imp.gap_mm * MM
    used_w = imp.cols * cell_w + (imp.cols - 1) * gap
    used_h = imp.rows * cell_h + (imp.rows - 1) * gap
    if used_w > sheet_w + 0.01 or used_h > sheet_h + 0.01:
        raise ValueError(
            f"{imp.cols}×{imp.rows} Nutzen ({used_w / MM:.0f}×{used_h / MM:.0f} mm) passen nicht "
            f"auf den Bogen ({sheet_w / MM:.0f}×{sheet_h / MM:.0f} mm)"
        )
    ox, oy = (sheet_w - used_w) / 2, (sheet_h - used_h) / 2
    cells = []
    for r in range(imp.rows):
        for c in range(imp.cols):
            # Leserichtung: oben links beginnend
            cells.append((ox + c * (cell_w + gap), oy + (imp.rows - 1 - r) * (cell_h + gap)))
    return cells


def _mirror_cells(cells, imp: Imposition, sheet_w: float, sheet_h: float, cell_w: float, cell_h: float):
    """Zellpositionen der Rückseite, damit Vorder- und Rückseite nach dem Wenden deckungsgleich sind."""
    portrait = sheet_h >= sheet_w
    vertical_axis = (imp.back_flip == BackFlip.LONG_EDGE) == portrait
    if vertical_axis:  # wie eine Buchseite wenden
        return [(sheet_w - x - cell_w, y) for x, y in cells], 0
    # über die waagerechte Achse wenden: Rückseite steht Kopf
    return [(x, sheet_h - y - cell_h) for x, y in cells], 180


def _sheet_size(imp: Imposition, cell_w: float, cell_h: float) -> tuple[float, float]:
    if imp.auto_sheet:
        gap = imp.gap_mm * MM
        margin = 2 * (imp.bleed_mm * MM + (15 * MM if imp.crop_marks else 0))
        return imp.cols * cell_w + (imp.cols - 1) * gap + margin, imp.rows * cell_h + (imp.rows - 1) * gap + margin
    return imp.sheet_width_mm * MM, imp.sheet_height_mm * MM


def _clip(x: float, y: float, w: float, h: float, bleed: float) -> tuple[float, float, float, float]:
    return (x - bleed, y - bleed, x + w + bleed, y + h + bleed)


def plan(doc: PdfDocument, imp: Imposition) -> list[SheetSide]:
    """Bogenplan berechnen (ohne PDF zu erzeugen)."""
    if doc.page_count == 0:
        raise ValueError("Dokument ist leer")
    if imp.layout == Layout.NONE:
        return []
    if imp.layout in (Layout.BOOKLET, Layout.MULTI_BOOKLET):
        return _plan_booklets(doc, imp)
    if imp.layout == Layout.PERFECT_BOUND:
        return _plan_perfect_bound(doc, imp)
    return _plan_grid(doc, imp)


def _plan_grid(doc: PdfDocument, imp: Imposition) -> list[SheetSide]:
    w, h = visual_trim(doc, 0)
    rotation = 90 if imp.rotate else 0
    cell_w, cell_h = (h, w) if imp.rotate else (w, h)
    sheet_w, sheet_h = _sheet_size(imp, cell_w, cell_h)
    cells = _grid(imp, cell_w, cell_h, sheet_w, sheet_h)
    n = imp.per_side
    pages = doc.page_count
    per_sheet = 2 * n if imp.duplex else n
    bleed = min(imp.bleed_mm * MM, imp.gap_mm * MM / 2) if imp.gap_mm else 0.0

    sides_pages: list[list[int | None]] = []
    if imp.layout == Layout.NUP:
        count = math.ceil(pages / per_sheet)
        for s in range(count):
            base = s * per_sheet
            if imp.duplex:
                # Vorderseite: Seiten 1, 3, 5 …; Rückseite: 2, 4, 6 … jeweils hinter ihrer Vorderseite
                for side in range(2):
                    sides_pages.append([base + 2 * i + side if base + 2 * i + side < pages else None
                                        for i in range(n)])
            else:
                sides_pages.append([base + i if base + i < pages else None for i in range(n)])
    elif imp.layout == Layout.REPEAT:
        step = 2 if imp.duplex else 1
        for p in range(0, pages, step):
            sides_pages.append([p] * n)
            if imp.duplex:
                sides_pages.append([p + 1 if p + 1 < pages else None] * n)
    elif imp.layout == Layout.CUT_STACK:
        faces = 2 if imp.duplex else 1
        sheets = math.ceil(pages / (n * faces))
        for s in range(sheets):
            for side in range(faces):
                row = []
                for pos in range(n):
                    # Stapel pos enthält fortlaufend die Seiten pos*sheets*faces …
                    p = (pos * sheets + s) * faces + side
                    row.append(p if p < pages else None)
                sides_pages.append(row)

    result = []
    for number, page_list in enumerate(sides_pages):
        back = imp.duplex and number % 2 == 1
        side = SheetSide(sheet_w, sheet_h)
        side_cells, extra = (_mirror_cells(cells, imp, sheet_w, sheet_h, cell_w, cell_h) if back else (cells, 0))
        for (x, y), page in zip(side_cells, page_list):
            side.placements.append(Placement(page, x, y, (rotation + extra) % 360,
                                             clip=_clip(x, y, cell_w, cell_h, bleed)))
            side.cut_lines.append((x, y, x + cell_w, y + cell_h))
        result.append(side)
    return result


def booklet_order(page_count: int) -> list[tuple[int | None, int | None, int | None, int | None]]:
    """Je Bogen (Vorder links, Vorder rechts, Rück links, Rück rechts), aufgefüllt auf Vielfaches von 4."""
    n = math.ceil(page_count / 4) * 4
    sheets = []
    for k in range(n // 4):
        quad = (n - 1 - 2 * k, 2 * k, 2 * k + 1, n - 2 - 2 * k)
        sheets.append(tuple(p if p < page_count else None for p in quad))
    return sheets


def _plan_booklets(doc: PdfDocument, imp: Imposition) -> list[SheetSide]:
    w, h = visual_trim(doc, 0)
    sheet_w, sheet_h = (2 * w, h) if imp.auto_sheet else (imp.sheet_width_mm * MM, imp.sheet_height_mm * MM)
    if 2 * w > sheet_w + 0.01 or h > sheet_h + 0.01:
        raise ValueError(f"Broschüre {2 * w / MM:.0f}×{h / MM:.0f} mm passt nicht auf den Bogen")
    ox, oy = (sheet_w - 2 * w) / 2, (sheet_h - h) / 2
    pages = list(range(doc.page_count))
    if imp.layout == Layout.MULTI_BOOKLET:
        chunk = max(1, imp.sheets_per_booklet) * 4
        groups = [pages[i:i + chunk] for i in range(0, len(pages), chunk)]
    else:
        groups = [pages]
    result = []
    for group in groups:
        order = booklet_order(len(group))
        count = len(order)
        total_creep = imp.creep_mm * MM if imp.creep_mm is not None else (count - 1) * imp.paper_thickness_mm * MM
        for k, (fl, fr, bl, br) in enumerate(order):
            # Creep: innere Bögen zum Bund hin verschieben (äußerster Bogen 0)
            shift = total_creep * k / (count - 1) / 2 if count > 1 else 0.0
            for left, right in ((fl, fr), (bl, br)):
                side = SheetSide(sheet_w, sheet_h)
                for page, x, sx in ((left, ox, shift), (right, ox + w, -shift)):
                    real = group[page] if page is not None else None
                    bleed = imp.bleed_mm * MM
                    side.placements.append(Placement(real, x, oy, 0, shift_x=sx,
                                                     clip=(x, oy - bleed, x + w, oy + h + bleed)))
                side.cut_lines.append((ox, oy, ox + 2 * w, oy + h))
                result.append(side)
    return result


def _plan_perfect_bound(doc: PdfDocument, imp: Imposition) -> list[SheetSide]:
    """Umschlag (Rückseite | Rücken | Vorderseite) als erster Bogen, danach Inhalt 1-up."""
    w, h = visual_trim(doc, 0)
    spine = imp.spine_mm * MM
    result = []
    content = list(range(doc.page_count))
    if imp.cover_front is not None:
        cover = SheetSide(2 * w + spine, h)
        back = imp.cover_back
        cover.placements.append(Placement(back, 0, 0))
        cover.placements.append(Placement(imp.cover_front, w + spine, 0))
        cover.cut_lines.append((0, 0, 2 * w + spine, h))
        result.append(cover)
        content = [p for p in content if p not in (imp.cover_front, imp.cover_back)]
    for page in content:
        side = SheetSide(w, h)
        side.placements.append(Placement(page, 0, 0))
        result.append(side)
    return result


# --- PDF erzeugen -----------------------------------------------------------


def impose(doc: PdfDocument, imp: Imposition) -> PdfDocument:
    """Ausgeschossenes Dokument erzeugen; ``doc`` bleibt unverändert."""
    sides = plan(doc, imp)
    if not sides:
        return doc
    out = pikepdf.new()
    cache: dict[int, pikepdf.Object] = {}
    glue = imp.glue_zone_mm * MM if imp.layout == Layout.PERFECT_BOUND else 0.0
    for number, side in enumerate(sides):
        resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary())
        content: list[str] = []
        for i, pl in enumerate(side.placements):
            if pl.page is None:
                continue
            if pl.page not in cache:
                xobj = doc.pdf.pages[pl.page].as_form_xobject(False)
                if doc.has_box(pl.page, "BleedBox"):
                    xobj.BBox = pikepdf.Array(list(doc.box(pl.page, "BleedBox")))
                cache[pl.page] = out.copy_foreign(xobj)
            name = f"/P{i}"
            resources.XObject[Name(name)] = cache[pl.page]
            trim = doc.box(pl.page, "TrimBox")
            rot = doc.page_rotation(pl.page)
            vw, vh = visual_trim(doc, pl.page)
            m = _mul(_to_visual(trim, rot), _place(vw, vh, pl.rotation, pl.x + pl.shift_x, pl.y))
            clip = ""
            if pl.clip:
                cx0, cy0, cx1, cy1 = pl.clip
                clip = f"{cx0:.3f} {cy0:.3f} {cx1 - cx0:.3f} {cy1 - cy0:.3f} re W n "
            content.append(f"q {clip}{' '.join(f'{v:.5f}' for v in m)} cm {name} Do Q")
            if glue and side.width < 1.5 * vw:  # Inhaltsseite: Klebezone am Bund weiß
                bund_left = (number - (1 if imp.cover_front is not None else 0)) % 2 == 0
                gx = pl.x if bund_left else pl.x + vw - glue
                content.append(f"q 0 0 0 0 k {gx:.3f} {pl.y:.3f} {glue:.3f} {vh:.3f} re f Q")
        if imp.crop_marks:
            content.append(_crop_marks(side))
        page = pikepdf.Dictionary(
            Type=Name.Page,
            MediaBox=pikepdf.Array([0, 0, side.width, side.height]),
            Resources=resources,
            Contents=out.make_stream("\n".join(content).encode()),
        )
        if len(side.cut_lines) == 1:
            page.TrimBox = pikepdf.Array(list(side.cut_lines[0]))
        out.pages.append(pikepdf.Page(out.make_indirect(page)))
    buf = io.BytesIO()
    out.save(buf)
    result = PdfDocument.from_bytes(buf.getvalue())
    result.path = doc.path
    # Output Intent übernehmen (PDF/X)
    intents = doc.pdf.Root.get("/OutputIntents")
    if intents is not None:
        result.pdf.Root.OutputIntents = result.pdf.copy_foreign(intents)
    return result


def _crop_marks(side: SheetSide, offset: float = 3 * MM, length: float = 5 * MM) -> str:
    lines = ["q 0.25 w 1 1 1 1 K"]
    xs = sorted({v for c in side.cut_lines for v in (c[0], c[2])})
    ys = sorted({v for c in side.cut_lines for v in (c[1], c[3])})
    y_min, y_max = min(ys), max(ys)
    x_min, x_max = min(xs), max(xs)
    for x in xs:
        lines.append(f"{x:.3f} {y_min - offset:.3f} m {x:.3f} {y_min - offset - length:.3f} l S")
        lines.append(f"{x:.3f} {y_max + offset:.3f} m {x:.3f} {y_max + offset + length:.3f} l S")
    for y in ys:
        lines.append(f"{x_min - offset:.3f} {y:.3f} m {x_min - offset - length:.3f} {y:.3f} l S")
        lines.append(f"{x_max + offset:.3f} {y:.3f} m {x_max + offset + length:.3f} {y:.3f} l S")
    lines.append("Q")
    return "\n".join(lines)


def repeat_pages(doc: PdfDocument, times: int) -> None:
    """Jede Seite ``times``-mal hintereinander (z. B. Notizblöcke: 50 Blatt je Block)."""
    if times < 1:
        raise ValueError("Anzahl muss mindestens 1 sein")
    for index in range(doc.page_count - 1, -1, -1):
        for _ in range(times - 1):
            doc.duplicate_pages([index])
