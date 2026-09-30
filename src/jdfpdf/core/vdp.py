"""Variabler Datendruck: Felder für Text, Bilder, Barcodes und QR-Codes aus CSV oder XLSX.

Die Vorlage (alle Seiten des Dokuments) wird je Datensatz wiederholt; die Felder
kommen als eigene Ebene (``vdp``) darüber. Platzhalter ``{Spalte}`` werden durch
die Werte des Datensatzes ersetzt, ``{#}`` durch die laufende Nummer.

XLSX wird ohne Zusatzbibliothek gelesen (ZIP + XML); Formeln liefern den zuletzt
gespeicherten Wert.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import etree
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, eanbc, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics

from .layers import add_layer, cmyk, draw_overlay, visual_matrix
from .layers import font as layer_font
from .pdfdoc import PdfDocument, Section
from .textedit import _lines, register_font

MM = 72 / 25.4
LAYER = "vdp"
SUFFIX = ".jdfvdp"


# --- Datenquellen -----------------------------------------------------------------------


@dataclass
class DataSource:
    headers: list[str]
    rows: list[dict[str, str]]
    path: Path | None = None

    def __len__(self) -> int:
        return len(self.rows)


def read_csv(path: str | Path) -> DataSource:
    raw = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        class dialect(csv.excel):
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [r for r in reader if any(cell.strip() for cell in r)]
    if not rows:
        return DataSource([], [], Path(path))
    headers = _unique_headers(rows[0])
    return DataSource(headers, [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in rows[1:]], Path(path))


def _unique_headers(cells: list[str]) -> list[str]:
    headers, seen = [], {}
    for number, cell in enumerate(cells, start=1):
        name = cell.strip() or f"Spalte{number}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
       "rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    value = 0
    for ch in letters:
        value = value * 26 + ord(ch) - 64
    return value - 1


def _number(text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return text
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def xlsx_sheets(path: str | Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        book = etree.fromstring(z.read("xl/workbook.xml"))
    return [s.get("name") for s in book.iterfind("m:sheets/m:sheet", _NS)]


def read_xlsx(path: str | Path, sheet: str | int = 0) -> DataSource:
    with zipfile.ZipFile(path) as z:
        book = etree.fromstring(z.read("xl/workbook.xml"))
        sheets = book.findall("m:sheets/m:sheet", _NS)
        if not sheets:
            raise ValueError("Arbeitsmappe enthält keine Tabellenblätter")
        chosen = sheets[sheet] if isinstance(sheet, int) else next(s for s in sheets if s.get("name") == sheet)
        rid = chosen.get(f"{{{_NS['r']}}}id")
        rels = etree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        target = next(r.get("Target") for r in rels.iterfind("rel:Relationship", _NS) if r.get("Id") == rid)
        target = target.lstrip("/")
        sheet_path = target if target.startswith("xl/") else "xl/" + target
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            sst = etree.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")) for si in sst.iterfind("m:si", _NS)]
        data = etree.fromstring(z.read(sheet_path))
    grid: list[list[str]] = []
    for row in data.iterfind("m:sheetData/m:row", _NS):
        cells: dict[int, str] = {}
        for position, cell in enumerate(row.iterfind("m:c", _NS)):
            ref = cell.get("r")
            column = _col_index(ref) if ref else position
            kind = cell.get("t", "n")
            value_el = cell.find("m:v", _NS)
            value = value_el.text if value_el is not None and value_el.text is not None else ""
            if kind == "s" and value:
                value = shared[int(value)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{{{_NS['m']}}}t"))
            elif kind == "b":
                value = "WAHR" if value == "1" else "FALSCH"
            elif kind == "n" and value:
                value = _number(value)
            cells[column] = value
        if cells and any(v.strip() for v in cells.values()):
            width = max(cells) + 1
            grid.append([cells.get(i, "") for i in range(width)])
    if not grid:
        return DataSource([], [], Path(path))
    headers = _unique_headers(grid[0])
    rows = [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in grid[1:]]
    return DataSource(headers, rows, Path(path))


def read_data(path: str | Path, sheet: str | int = 0) -> DataSource:
    suffix = Path(path).suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        return read_xlsx(path, sheet)
    if suffix in (".csv", ".txt", ".tsv"):
        return read_csv(path)
    raise ValueError(f"Datenquelle nicht unterstützt: {Path(path).name} (CSV oder XLSX)")


# --- Felder -----------------------------------------------------------------------------


@dataclass
class Field:
    kind: str = "text"  # text, image, code128, ean13, qr
    value: str = ""  # mit Platzhaltern, z. B. "{Vorname} {Name}" oder "{Bild}"
    page: int = 1  # Seite der Vorlage (1-basiert), 0 = alle Seiten
    x_mm: float = 20.0  # von links, sichtbare Seite
    y_mm: float = 20.0  # von oben bis Oberkante
    width_mm: float = 0.0  # Text: Umbruchbreite (0 = keiner); Bild/Code: Breite
    height_mm: float = 0.0  # Bild/Code: Höhe (0 = aus Seitenverhältnis bzw. Standard)
    font: str = "Vera Sans"
    size: float = 11.0
    color_cmyk: tuple[float, float, float, float] = (0, 0, 0, 100)
    align: str = "left"


class _Values(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(template: str, record: dict[str, str], number: int) -> str:
    values = _Values(record)
    values["#"] = str(number)
    try:
        return template.format_map(values)
    except (ValueError, IndexError):
        return template


def _draw_field(c, f: Field, value: str, vh: float, base_dir: Path | None) -> None:
    x, top = f.x_mm * MM, vh - f.y_mm * MM
    if f.kind == "text":
        font = register_font(f.font)
        width = f.width_mm * MM
        lines = _lines(value, font, f.size, width)
        box_w = width or max((pdfmetrics.stringWidth(line, font, f.size) for line in lines), default=0)
        c.setFillColor(cmyk(*f.color_cmyk))
        c.setFont(font, f.size)
        for number, line in enumerate(lines):
            y = top - f.size * 0.8 - number * f.size * 1.2
            if f.align == "center":
                c.drawCentredString(x + box_w / 2, y, line)
            elif f.align == "right":
                c.drawRightString(x + box_w, y, line)
            else:
                c.drawString(x, y, line)
        return
    if not value:
        return
    if f.kind == "image":
        path = Path(value)
        if not path.is_absolute() and base_dir is not None:
            path = base_dir / path
        reader = ImageReader(str(path))
        iw, ih = reader.getSize()
        width = (f.width_mm or 40) * MM
        height = f.height_mm * MM or width * ih / iw
        c.drawImage(reader, x, top - height, width, height, mask="auto", preserveAspectRatio=bool(f.height_mm))
        return
    c.setFillColor(cmyk(*f.color_cmyk))
    c.setStrokeColor(cmyk(*f.color_cmyk))
    if f.kind == "qr":
        size = (f.width_mm or 20) * MM
        widget = qr.QrCodeWidget(value, barFillColor=cmyk(*f.color_cmyk))
        x0, y0, x1, y1 = widget.getBounds()
        drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, c, x, top - size)
        return
    height = (f.height_mm or 12) * MM
    if f.kind == "ean13":
        digits = re.sub(r"\D", "", value)
        widget = eanbc.Ean13BarcodeWidget(digits[:12], barFillColor=cmyk(*f.color_cmyk), fontName=layer_font(),
                                          textColor=cmyk(*f.color_cmyk))
        x0, y0, x1, y1 = widget.getBounds()
        width = (f.width_mm or 37.3) * MM
        drawing = Drawing(width, height, transform=[width / (x1 - x0), 0, 0, height / (y1 - y0), 0, 0])
        drawing.add(widget)
        renderPDF.draw(drawing, c, x, top - height)
        return
    if f.kind == "code128":
        bar = code128.Code128(value, barHeight=height, barWidth=0.8, humanReadable=False)
        if f.width_mm:
            c.saveState()
            c.translate(x, top - height)
            c.scale(f.width_mm * MM / bar.width, 1)
            bar.drawOn(c, 0, 0)
            c.restoreState()
        else:
            bar.drawOn(c, x, top - height)
        return
    raise ValueError(f"Unbekannte Feldart {f.kind!r}")


def apply_fields(doc: PdfDocument, fields: list[Field], record: dict[str, str], number: int,
                 first_page: int = 0, template_pages: int | None = None, base_dir: Path | None = None) -> None:
    """Felder eines Datensatzes auf die Seiten ``first_page`` … der Vorlage setzen."""
    template_pages = template_pages or doc.page_count
    for offset in range(template_pages):
        index = first_page + offset
        mine = [f for f in fields if f.page in (0, offset + 1)]
        if not mine:
            continue
        matrix, vw, vh = visual_matrix(doc, index)

        def draw(c, mine=mine) -> None:
            for f in mine:
                c.saveState()
                _draw_field(c, f, fill(f.value, record, number), vh, base_dir)
                c.restoreState()

        add_layer(doc, index, LAYER, draw_overlay(vw, vh, draw), matrix)


def merge(template: PdfDocument, data: DataSource, fields: list[Field], records: list[int] | None = None,
          sections: bool = False, section_title: str = "{#}") -> PdfDocument:
    """Ein PDF mit allen (bzw. den gewählten) Datensätzen; optional je Datensatz ein Abschnitt."""
    records = list(range(len(data.rows))) if records is None else records
    if not records:
        raise ValueError("Keine Datensätze ausgewählt")
    source = template.to_bytes()
    per = template.page_count
    out = PdfDocument.from_bytes(source)
    out.set_sections([])
    for _ in records[1:]:
        out.insert_pages_from(PdfDocument.from_bytes(source))
    base_dir = data.path.parent if data.path else None
    marks = []
    for position, row in enumerate(records):
        record = data.rows[row]
        apply_fields(out, fields, record, row + 1, position * per, per, base_dir)
        if sections:
            marks.append(Section(fill(section_title, record, row + 1), position * per))
    if sections:
        out.set_sections(marks)
    return out


def merge_in_place(doc: PdfDocument, data: DataSource, fields: list[Field], records: list[int] | None = None,
                   sections: bool = False) -> int:
    """Wie ``merge``, ersetzt aber den Inhalt von ``doc``; liefert die Zahl der Datensätze."""
    merged = merge(doc, data, fields, records, sections)
    count = doc.page_count
    doc.insert_pages_from(merged, count)
    doc.delete_pages(list(range(count)))
    doc.set_sections(merged.sections())
    return merged.page_count // max(count, 1)


def preview(template: PdfDocument, data: DataSource, fields: list[Field], row: int) -> PdfDocument:
    return merge(template, data, fields, [row])


# --- Einrichtung speichern --------------------------------------------------------------


@dataclass
class VdpSetup:
    data: str = ""
    sheet: str | int = 0
    fields: list[Field] = field(default_factory=list)
    records: str = ""  # Datensätze, z. B. "1-100"; leer = alle
    sections: bool = False

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict) -> "VdpSetup":
        fields = [Field(**{k: (tuple(v) if k == "color_cmyk" else v) for k, v in f.items()
                           if k in Field.__dataclass_fields__}) for f in data.get("fields", [])]
        return cls(data.get("data", ""), data.get("sheet", 0), fields, data.get("records", ""),
                   bool(data.get("sections", False)))

    @classmethod
    def load(cls, path: str | Path) -> "VdpSetup":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
