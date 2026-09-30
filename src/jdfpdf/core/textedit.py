"""Text bearbeiten: Textblöcke setzen, vorhandenen Text abdecken und ersetzen, einfach umschreiben.

- ``add_text_block``: mehrzeiliger Text mit Umbruch als eigene Ebene (``text``).
- ``replace_text``: Fundstellen (Textsuche von PDFium) weiß abdecken und den neuen
  Text an der Grundlinie des alten setzen. Funktioniert mit jeder Schrift, da der
  Originalinhalt unverändert bleibt.
- ``rewrite_text``: echtes Umschreiben in den Inhaltsströmen. Nur bei einfachen
  Schriften (Type1/TrueType mit Standard- oder WinAnsi-Kodierung) und nur, wenn alle
  neuen Zeichen in der Schrift vorhanden sind (bei Teilmengen: schon auf der Seite
  mit dieser Schrift verwendet).

Eingesetzte Schriften werden eingebettet (PDF/X); Standard ist Bitstream Vera.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pikepdf
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .layers import add_layer, cmyk, draw_overlay, remove_layers, visual_matrix
from .pdfdoc import PdfDocument

MM = 72 / 25.4
LAYER = "text"


# --- Schriften --------------------------------------------------------------------------


def _vera() -> dict[str, str]:
    import reportlab

    base = Path(reportlab.__file__).parent / "fonts"
    return {"Vera Sans": str(base / "Vera.ttf"), "Vera Sans Bold": str(base / "VeraBd.ttf"),
            "Vera Sans Italic": str(base / "VeraIt.ttf"), "Vera Sans Bold Italic": str(base / "VeraBI.ttf")}


def _font_dirs() -> list[Path]:
    if sys.platform == "win32":
        dirs = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
        return dirs
    if sys.platform == "darwin":
        return [Path("/Library/Fonts"), Path.home() / "Library" / "Fonts", Path("/System/Library/Fonts")]
    return [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
            Path.home() / ".local" / "share" / "fonts"]


@lru_cache(maxsize=1)
def available_fonts() -> dict[str, str]:
    """Anzeigename → TTF-Datei: Vera plus TrueType-Schriften des Systems."""
    fonts = dict(_vera())
    for folder in _font_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.ttf"))[:2000]:
            fonts.setdefault(path.stem.replace("_", " ").replace("-", " "), str(path))
    return fonts


@lru_cache(maxsize=None)
def register_font(name_or_path: str) -> str:
    """Schrift (Anzeigename oder Pfad) bei reportlab registrieren; liefert den internen Namen."""
    path = available_fonts().get(name_or_path, name_or_path)
    if not Path(path).exists():
        raise FileNotFoundError(f"Schrift nicht gefunden: {name_or_path}")
    internal = "JdfT" + str(abs(hash(path)))
    pdfmetrics.registerFont(TTFont(internal, path))
    return internal


# --- Textblöcke -------------------------------------------------------------------------


@dataclass
class TextBlock:
    text: str
    x_mm: float = 20.0  # von links, sichtbare Seite
    y_mm: float = 20.0  # von oben bis Oberkante der ersten Zeile
    width_mm: float = 0.0  # 0: kein Umbruch
    font: str = "Vera Sans"
    size: float = 11.0
    leading: float = 1.2  # Zeilenabstand als Vielfaches der Schriftgröße
    color_cmyk: tuple[float, float, float, float] = (0, 0, 0, 100)
    align: str = "left"  # left, center, right
    background_cmyk: tuple[float, float, float, float] | None = None  # z. B. (0, 0, 0, 0) zum Abdecken
    padding_mm: float = 1.0
    rotation: float = 0.0


def _lines(text: str, font: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if width > 0:
            lines.extend(simpleSplit(paragraph, font, size, width) or [""])
        else:
            lines.append(paragraph)
    return lines


def add_text_block(doc: PdfDocument, indices: list[int], block: TextBlock) -> None:
    font = register_font(block.font)
    for index in indices:
        matrix, vw, vh = visual_matrix(doc, index)
        width = block.width_mm * MM
        lines = _lines(block.text, font, block.size, width)
        box_w = width or max((pdfmetrics.stringWidth(line, font, block.size) for line in lines), default=0)
        step = block.size * block.leading
        x, top = block.x_mm * MM, vh - block.y_mm * MM

        def draw(c) -> None:
            c.translate(x, top)
            c.rotate(-block.rotation)
            if block.background_cmyk is not None:
                pad = block.padding_mm * MM
                height = step * len(lines)
                c.setFillColor(cmyk(*block.background_cmyk))
                c.rect(-pad, -height - pad, box_w + 2 * pad, height + 2 * pad, stroke=0, fill=1)
            c.setFillColor(cmyk(*block.color_cmyk))
            c.setFont(font, block.size)
            for number, line in enumerate(lines):
                baseline = -block.size * 0.8 - number * step
                if block.align == "center":
                    c.drawCentredString(box_w / 2, baseline, line)
                elif block.align == "right":
                    c.drawRightString(box_w, baseline, line)
                else:
                    c.drawString(0, baseline, line)

        add_layer(doc, index, LAYER, draw_overlay(vw, vh, draw), matrix)


def remove_text_edits(doc: PdfDocument, indices: list[int]) -> int:
    return sum(remove_layers(doc, i, LAYER) for i in indices)


# --- Suchen -----------------------------------------------------------------------------


@dataclass
class TextHit:
    page: int
    text: str
    rects: list[tuple[float, float, float, float]]  # Seitenkoordinaten (ungedreht)
    origin: tuple[float, float]  # Grundlinie des ersten Zeichens
    size: float


def find_text(doc: PdfDocument, query: str, pages: list[int] | None = None, match_case: bool = False,
              whole_word: bool = False) -> list[TextHit]:
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    if not query:
        return []
    hits = []
    pdf = pdfium.PdfDocument(doc.to_bytes())
    try:
        for index in pages if pages is not None else range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            searcher = textpage.search(query, match_case=match_case, match_whole_word=whole_word)
            while (found := searcher.get_next()) is not None:
                start, count = found
                rects = [tuple(textpage.get_rect(i)) for i in range(textpage.count_rects(start, count))]
                x, y = ctypes.c_double(), ctypes.c_double()
                raw.FPDFText_GetCharOrigin(textpage.raw, start, x, y)
                size = raw.FPDFText_GetFontSize(textpage.raw, start) or (rects[0][3] - rects[0][1])
                hits.append(TextHit(index, textpage.get_text_range(start, count), rects, (x.value, y.value),
                                    float(size)))
            searcher.close()
            textpage.close()
            page.close()
    finally:
        pdf.close()
    return hits


# --- Abdecken und ersetzen --------------------------------------------------------------


@dataclass
class ReplaceStyle:
    font: str = "Vera Sans"
    size: float | None = None  # None: Größe des gefundenen Texts
    color_cmyk: tuple[float, float, float, float] = (0, 0, 0, 100)
    cover_cmyk: tuple[float, float, float, float] = (0, 0, 0, 0)
    fit: bool = True  # Schrift verkleinern, wenn der neue Text breiter als der alte ist


def replace_text(doc: PdfDocument, query: str, replacement: str, pages: list[int] | None = None,
                 style: ReplaceStyle | None = None, match_case: bool = False, whole_word: bool = False) -> int:
    """Fundstellen abdecken und überschreiben; liefert die Anzahl."""
    style = style or ReplaceStyle()
    font = register_font(style.font)
    hits = find_text(doc, query, pages, match_case, whole_word)
    by_page: dict[int, list[TextHit]] = {}
    for hit in hits:
        by_page.setdefault(hit.page, []).append(hit)
    for index, page_hits in by_page.items():
        mx0, my0, mx1, my1 = doc.box(index, "MediaBox")

        def draw(c, page_hits=page_hits) -> None:
            for hit in page_hits:
                c.setFillColor(cmyk(*style.cover_cmyk))
                for x0, y0, x1, y1 in hit.rects:
                    c.rect(x0 - mx0 - 0.5, y0 - my0 - 0.5, x1 - x0 + 1, y1 - y0 + 1, stroke=0, fill=1)
                if not replacement:
                    continue
                size = style.size or hit.size
                available = hit.rects[0][2] - hit.rects[0][0]
                width = pdfmetrics.stringWidth(replacement, font, size)
                if style.fit and width > available > 0:
                    size *= available / width
                c.setFillColor(cmyk(*style.color_cmyk))
                c.setFont(font, size)
                c.drawString(hit.origin[0] - mx0, hit.origin[1] - my0, replacement)

        add_layer(doc, index, LAYER, draw_overlay(mx1 - mx0, my1 - my0, draw), (1, 0, 0, 1, mx0, my0))
    return len(hits)


# --- Echtes Umschreiben -----------------------------------------------------------------


@dataclass
class RewriteReport:
    replaced: int = 0
    skipped: list[str] = field(default_factory=list)  # Gründe, je Fundstelle


_SIMPLE = {"/Type1", "/TrueType", "/MMType1"}
_ENCODINGS = {None, "/WinAnsiEncoding", "/StandardEncoding"}
_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")


def _parse_to_unicode(data: bytes) -> dict[int, str]:
    """Einbyte-Zuordnungen (bfchar/bfrange) einer ToUnicode-CMap."""
    mapping: dict[int, str] = {}

    def text(hexstr: bytes) -> str:
        raw = bytes.fromhex(hexstr.decode())
        return raw.decode("utf-16-be", errors="replace")

    for block in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        values = _HEX.findall(block)
        for code, uni in zip(values[::2], values[1::2]):
            if len(code) <= 2:
                mapping[int(code, 16)] = text(uni)
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for line in block.splitlines():
            values = _HEX.findall(line)
            if len(values) >= 3 and len(values[0]) <= 2 and b"[" not in line:
                start, end, uni = int(values[0], 16), int(values[1], 16), int(values[2], 16)
                for offset in range(end - start + 1):
                    mapping[start + offset] = chr(uni + offset)
    return mapping


@dataclass
class _Codec:
    ok: bool
    reason: str = ""
    decode_map: dict[int, str] | None = None  # None: WinAnsi
    first: int = 0
    last: int = 255
    subset: bool = False  # eingebettete Teilschrift: nur schon verwendete Zeichen sind sicher vorhanden

    def decode(self, data: bytes) -> str:
        if self.decode_map is None:
            return data.decode("cp1252", errors="replace")
        return "".join(self.decode_map.get(b, "\ufffd") for b in data)

    def encode(self, text: str) -> bytes | None:
        if self.decode_map is None:
            try:
                data = text.encode("cp1252")
            except UnicodeEncodeError:
                return None
        else:
            reverse = {v: k for k, v in self.decode_map.items()}
            if any(ch not in reverse for ch in text):
                return None
            data = bytes(reverse[ch] for ch in text)
        return data if all(self.first <= b <= self.last for b in data) else None


def _codec(font: pikepdf.Object | None) -> _Codec:
    """Wie Zeichen der Schrift kodiert sind; nur einfache Schriften lassen sich umschreiben.

    Mit ToUnicode-Tabelle werden nur Zeichen zugelassen, die dort vorkommen (bei
    Teilschriften also genau die eingebetteten Glyphen). Ohne Tabelle gilt WinAnsi,
    außer bei symbolischen Schriften ohne Kodierung.
    """
    if font is None:
        return _Codec(False, "Schrift nicht gefunden")
    subtype = str(font.get("/Subtype"))
    if subtype not in _SIMPLE:
        return _Codec(False, f"Schrifttyp {subtype[1:]}")
    encoding = font.get("/Encoding")
    if isinstance(encoding, pikepdf.Dictionary):
        if "/Differences" in encoding and "/ToUnicode" not in font:
            return _Codec(False, "eigene Kodierung ohne ToUnicode")
        encoding = encoding.get("/BaseEncoding")
    first, last = int(font.get("/FirstChar", 0)), int(font.get("/LastChar", 255))
    descriptor = font.get("/FontDescriptor", {})
    embedded = any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
    base = str(font.get("/BaseFont", ""))[1:]
    subset = embedded and len(base) > 7 and base[6] == "+"
    if "/ToUnicode" in font:
        mapping = _parse_to_unicode(font.ToUnicode.read_bytes())
        if mapping:
            return _Codec(True, "", mapping, first, last, subset)
    enc = str(encoding) if encoding is not None else None
    if enc not in _ENCODINGS and not (isinstance(font.get("/Encoding"), pikepdf.Dictionary)):
        return _Codec(False, f"Kodierung {encoding}")
    symbolic = int(descriptor.get("/Flags", 32)) & 4
    if enc is None and symbolic and embedded:
        return _Codec(False, "symbolische Schrift ohne Kodierung")
    return _Codec(True, "", None, first, last, subset)


def _strings(operands, operator: str) -> list:
    if operator in ("Tj", "'"):
        return [operands[0]]
    if operator == '"':
        return [operands[2]]
    if operator == "TJ":
        return [item for item in operands[0] if isinstance(item, pikepdf.String)]
    return []


def rewrite_text(doc: PdfDocument, query: str, replacement: str, pages: list[int] | None = None) -> RewriteReport:
    """Text in den Inhaltsströmen der Seiten ersetzen (nur innerhalb eines Textoperanden)."""
    report = RewriteReport()
    for index in pages if pages is not None else range(doc.page_count):
        page = doc.pdf.pages[index]
        fonts = page.obj.get("/Resources", {}).get("/Font", {})
        codecs: dict[str, _Codec] = {}
        contents = page.obj.get("/Contents")
        streams = list(contents) if isinstance(contents, pikepdf.Array) else ([contents] if contents is not None else [])
        # vorhandene Zeichen je Schrift (für Teilschriften)
        used: dict[str, set[str]] = {}
        current = None
        for stream in streams:
            if stream.get("/JdfpdfLayer") is not None:
                continue
            for operands, op in pikepdf.parse_content_stream(stream):
                if str(op) == "Tf":
                    current = str(operands[0])
                for item in _strings(operands, str(op)):
                    if current not in codecs:
                        codecs[current] = _codec(fonts.get(current) if current else None)
                    used.setdefault(current, set()).update(codecs[current].decode(bytes(item)))
        changed = False
        current = None
        new_streams = []
        for stream in streams:
            if stream.get("/JdfpdfLayer") is not None:  # eigene Ebenen nicht anfassen
                new_streams.append(stream)
                continue
            out = []
            stream_changed = False
            for operands, op in pikepdf.parse_content_stream(stream):
                name = str(op)
                if name == "Tf":
                    current = str(operands[0])
                strings = _strings(operands, name)
                if strings:
                    if current not in codecs:
                        codecs[current] = _codec(fonts.get(current) if current else None)
                    codec = codecs[current]
                    decoded = [codec.decode(bytes(s)) for s in strings]
                    if any(query in d for d in decoded):
                        new_operands, reason = _rewrite_operands(operands, name, query, replacement, codec,
                                                                 used.get(current, set()))
                        if new_operands is None:
                            report.skipped.append(f"Seite {index + 1}: {reason}")
                        else:
                            report.replaced += sum(d.count(query) for d in decoded)
                            operands = new_operands
                            stream_changed = True
                out.append((operands, op))
            if stream_changed:
                changed = True
                new_streams.append(doc.pdf.make_stream(pikepdf.unparse_content_stream(out)))
            else:
                new_streams.append(stream)
        if changed:
            # neue Ströme statt Änderung an Ort und Stelle: geteilte Inhalte anderer Seiten bleiben unberührt
            page.obj.Contents = pikepdf.Array(new_streams) if len(new_streams) > 1 else new_streams[0]
    return report


def _rewrite_operands(operands, op: str, query: str, replacement: str, codec: _Codec, used: set[str]):
    if not codec.ok:
        return None, codec.reason
    missing = {ch for ch in replacement if codec.encode(ch) is None}
    if codec.subset:
        missing |= set(replacement) - used
    if missing:
        return None, f"Zeichen nicht in der Schrift vorhanden: {''.join(sorted(missing))}"

    def fix(s: pikepdf.String) -> pikepdf.String:
        text = codec.decode(bytes(s))
        if query not in text:
            return s
        encoded = codec.encode(text.replace(query, replacement))
        return pikepdf.String(encoded) if encoded is not None else s

    operands = list(operands)
    if op in ("Tj", "'"):
        operands[0] = fix(operands[0])
    elif op == '"':
        operands[2] = fix(operands[2])
    else:
        operands[0] = pikepdf.Array([fix(i) if isinstance(i, pikepdf.String) else i for i in operands[0]])
    return operands, ""


__all__ = ["TextBlock", "TextHit", "ReplaceStyle", "RewriteReport", "add_text_block", "available_fonts",
           "find_text", "register_font", "remove_text_edits", "replace_text", "rewrite_text"]
