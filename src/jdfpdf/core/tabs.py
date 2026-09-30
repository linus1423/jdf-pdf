"""Register: vorgeschnittene Registerblätter und randabfallende Registermarken.

- ``insert_tab_sheets`` fügt vor jedem Abschnitt ein Registerblatt ein (breiter um
  den Tab-Überstand) und beschriftet den Tab an der passenden Position im Satz.
- ``apply_bleed_tabs`` druckt auf allen Seiten eines Abschnitts eine farbige Marke
  am Rand, die in den Beschnitt läuft (Daumenregister).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from reportlab.lib.utils import ImageReader

from .layers import add_layer, cmyk, draw_overlay, font, remove_layers
from .pdfdoc import PdfDocument, Section

MM = 72 / 25.4
LAYER_SHEET = "tabsheet"
LAYER_BLEED = "bleedtab"

DEFAULT_TAB_COLORS = [
    (100, 0, 0, 0), (0, 100, 0, 0), (0, 0, 100, 0), (60, 0, 100, 0),
    (0, 60, 100, 0), (60, 80, 0, 0), (0, 0, 0, 60),
]


@dataclass
class TabSheetStyle:
    tab_count: int = 5  # Taben pro Satz
    extension_mm: float = 12.7  # Überstand des Tabs über das Blattformat
    font_size: float = 9.0
    bold: bool = True
    double_sided: bool = False  # Tab auch auf der Rückseite beschriften
    text_cmyk: tuple[float, float, float, float] = (0, 0, 0, 100)
    image: str | None = None  # optionales Bild auf dem Tab (über dem Text)


@dataclass
class BleedTabStyle:
    tab_count: int = 6  # Positionen, bevor es oben wieder beginnt
    width_mm: float = 8.0  # sichtbare Breite innerhalb des Formats
    bleed_mm: float = 3.0
    height_mm: float | None = None  # None: Formathöhe / tab_count
    rounded: bool = False
    colors: list[tuple[float, float, float, float]] = field(default_factory=lambda: list(DEFAULT_TAB_COLORS))
    show_text: bool = True
    font_size: float = 7.0
    text_cmyk: tuple[float, float, float, float] = (0, 0, 0, 0)  # weiß auf Farbe
    duplex: bool = True  # gerade Seiten: Marke am linken Rand


def titles_from_text(text: str) -> list[str]:
    """Eine Zeile je Tab; ``\\n`` innerhalb der Zeile (als Zeichenfolge ``\\n``) bricht um (max. 3 Zeilen)."""
    return [line.strip().replace("\\n", "\n") for line in text.splitlines() if line.strip()]


def _lines(title: str) -> list[str]:
    return title.split("\n")[:3]


def insert_tab_sheets(
    doc: PdfDocument, tabs: list[tuple[str, int]], style: TabSheetStyle | None = None
) -> list[int]:
    """Registerblätter vor den angegebenen Seiten einfügen.

    ``tabs`` ist eine Liste (Tabtext, Seitenindex, vor dem das Blatt kommt). Liefert
    die Indizes aller eingefügten Blätter (bei beidseitig inkl. Rückseiten) im neuen
    Dokument, z. B. um ihnen das Registermedium zuzuweisen.
    """
    style = style or TabSheetStyle()
    if style.tab_count < 1:
        raise ValueError("Mindestens ein Tab pro Satz")
    ext = style.extension_mm * MM
    inserted: list[int] = []
    # von hinten einfügen, damit die Zielindizes gültig bleiben
    ordered = sorted(enumerate(tabs), key=lambda item: item[1][1], reverse=True)
    for number, (title, before) in ordered:
        ref = min(before, doc.page_count - 1)
        x0, y0, x1, y1 = doc.box(ref, "TrimBox")
        width, height = x1 - x0 + ext, y1 - y0
        slot = number % style.tab_count
        band = height / style.tab_count
        top = height - slot * band
        pages = [False, True] if style.double_sided else [False]
        for offset, back in enumerate(pages):
            doc.insert_blank(before + offset, width, height)

            def draw(c, back=back) -> None:
                # Vorderseite: Tab rechts; Rückseite: gespiegelt links
                tab_x0 = 0 if back else width - ext
                c.setFillColor(cmyk(*style.text_cmyk))
                cx = tab_x0 + ext / 2
                cy = top - band / 2
                c.saveState()
                c.translate(cx, cy)
                c.rotate(90 if not back else -90)
                if style.image:
                    reader = ImageReader(style.image)
                    iw, ih = reader.getSize()
                    img_h = ext * 0.6
                    img_w = img_h * iw / ih
                    c.drawImage(reader, -img_w / 2, ext * 0.05, img_w, img_h, mask="auto")
                c.setFont(font(style.bold), style.font_size)
                lines = _lines(title)
                leading = style.font_size * 1.15
                start = (len(lines) - 1) * leading / 2 - style.font_size * 0.35
                for i, line in enumerate(lines):
                    c.drawCentredString(0, start - i * leading, line)
                c.restoreState()

            add_layer(doc, before + offset, LAYER_SHEET, draw_overlay(width, height, draw))
        inserted.append(before)
    # Indizes im Enddokument: jedes frühere Einfügen verschiebt spätere Blätter
    per_tab = 2 if style.double_sided else 1
    result = []
    for rank, before in enumerate(sorted(inserted)):
        start = before + rank * per_tab
        result.extend(range(start, start + per_tab))
    return result


def insert_tabs_for_sections(doc: PdfDocument, style: TabSheetStyle | None = None,
                             titles: list[str] | None = None) -> list[int]:
    """Vor jedem Abschnitt ein Registerblatt; Tabtext = Abschnittsname oder ``titles`` in Reihenfolge."""
    sections = doc.sections()
    if not sections:
        raise ValueError("Keine Abschnitte vorhanden")
    tabs = [((titles[i] if titles and i < len(titles) else s.title), s.page) for i, s in enumerate(sections)]
    return insert_tab_sheets(doc, tabs, style)


def apply_bleed_tabs(doc: PdfDocument, style: BleedTabStyle | None = None,
                     sections: list[Section] | None = None) -> None:
    """Randabfallende Registermarken je Abschnitt auf alle Seiten des Abschnitts drucken."""
    style = style or BleedTabStyle()
    sections = sections if sections is not None else doc.sections()
    if not sections:
        raise ValueError("Keine Abschnitte vorhanden")
    for number, section in enumerate(sections):
        last = sections[number + 1].page - 1 if number + 1 < len(sections) else doc.page_count - 1
        color = style.colors[number % len(style.colors)] if style.colors else (0, 0, 0, 100)
        slot = number % style.tab_count
        for index in range(section.page, last + 1):
            _draw_bleed_tab(doc, index, section.title, slot, color, style)


def _draw_bleed_tab(doc: PdfDocument, index: int, title: str, slot: int, color, style: BleedTabStyle) -> None:
    mx0, my0, mx1, my1 = doc.box(index, "MediaBox")
    tx0, ty0, tx1, ty1 = doc.box(index, "TrimBox")
    width, height = mx1 - mx0, my1 - my0
    band = (style.height_mm * MM) if style.height_mm else (ty1 - ty0) / style.tab_count
    band_top = ty1 - slot * band
    left_side = style.duplex and index % 2 == 1
    w, bleed = style.width_mm * MM, style.bleed_mm * MM

    def draw(c) -> None:
        # Overlay deckt die MediaBox ab; Koordinaten relativ zu deren Ursprung
        ox, oy = -mx0, -my0
        if left_side:
            rx0, rx1 = tx0 - bleed, tx0 + w
        else:
            rx0, rx1 = tx1 - w, tx1 + bleed
        c.setFillColor(cmyk(*color))
        ry0 = band_top - band
        if style.rounded:
            c.roundRect(rx0 + ox, ry0 + oy, rx1 - rx0, band, radius=min(w, band) / 3, stroke=0, fill=1)
        else:
            c.rect(rx0 + ox, ry0 + oy, rx1 - rx0, band, stroke=0, fill=1)
        if style.show_text and title:
            c.setFillColor(cmyk(*style.text_cmyk))
            c.setFont(font(True), style.font_size)
            cx = (tx0 + w / 2 if left_side else tx1 - w / 2) + ox
            c.saveState()
            c.translate(cx, ry0 + band / 2 + oy)
            c.rotate(90 if left_side else -90)
            c.drawCentredString(0, -style.font_size * 0.35, title.replace("\n", " "))
            c.restoreState()

    add_layer(doc, index, LAYER_BLEED, draw_overlay(width, height, draw), (1, 0, 0, 1, mx0, my0))


def remove_tabs(doc: PdfDocument) -> None:
    """Randabfallende Marken entfernen und eingefügte Registerblätter löschen."""
    from .layers import layer_kinds

    sheets = [i for i in range(doc.page_count) if LAYER_SHEET in layer_kinds(doc, i)]
    for index in range(doc.page_count):
        remove_layers(doc, index, LAYER_BLEED)
    doc.delete_pages(sheets)
