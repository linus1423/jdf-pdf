"""Preflight: Druckdaten prüfen und einen Bericht (HTML/PDF) erzeugen.

Geprüft werden Schriften, effektive Bildauflösung, Farbräume, Beschnitt, Seitenformate,
Haarlinien, Transparenzen, PDF/X-Grundregeln, Sonderfarben, Verschlüsselung und
Formularfelder. Die Inhaltsströme werden dazu mit Transformationsmatrix durchlaufen,
damit Bildauflösung und Linienstärken so bewertet werden, wie sie gedruckt werden.
"""

from __future__ import annotations

import html
import io
import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

import pikepdf
from pikepdf import Name

from .colorspace import components, family, name_str
from .pdfdoc import PdfDocument

MM = 72 / 25.4


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class PreflightProfile:
    min_ppi: float = 150.0  # Farb-/Graustufenbilder
    min_ppi_bitmap: float = 600.0  # 1-Bit-Bilder
    min_line_pt: float = 0.25  # dünner gilt als Haarlinie
    bleed_mm: float = 3.0
    cmyk_output: bool = True  # RGB meldet dann eine Warnung
    check_fonts: bool = True
    check_images: bool = True
    check_colors: bool = True
    check_bleed: bool = True
    check_sizes: bool = True
    check_lines: bool = True
    check_transparency: bool = True
    check_pdfx: bool = True
    check_spots: bool = True


@dataclass
class Finding:
    rule: str
    severity: Severity
    key: str  # Textschlüssel in MESSAGES
    args: dict = field(default_factory=dict)
    pages: list[int] = field(default_factory=list)  # 0-basiert

    def text(self, language: str = "de") -> str:
        template = MESSAGES.get(self.key, {}).get(language) or MESSAGES.get(self.key, {}).get("de", self.key)
        args = dict(self.args)
        if isinstance(args.get("what"), (list, tuple)):
            args["what"] = ", ".join(WHAT.get(w, {}).get(language, w) for w in args["what"])
        try:
            return template.format(**args)
        except (KeyError, IndexError):
            return template


@dataclass
class PreflightReport:
    file: str
    page_count: int
    pdfx: str | None
    findings: list[Finding]
    created: datetime = field(default_factory=datetime.now)

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def ok(self) -> bool:
        return self.count(Severity.ERROR) == 0


MESSAGES: dict[str, dict[str, str]] = {
    "font_not_embedded": {"de": "Schrift nicht eingebettet: {font}", "en": "Font not embedded: {font}"},
    "image_low_ppi": {"de": "Bildauflösung zu gering: {ppi} ppi (mindestens {min})",
                      "en": "Image resolution too low: {ppi} ppi (minimum {min})"},
    "rgb_used": {"de": "RGB-Farben im Druckauftrag ({what})", "en": "RGB colors in print job ({what})"},
    "rgb_in_pdfx1a": {"de": "RGB-Farben sind in {pdfx} nicht erlaubt ({what})",
                      "en": "RGB colors are not allowed in {pdfx} ({what})"},
    "no_trimbox": {"de": "Kein Endformat (TrimBox) festgelegt", "en": "No trim box defined"},
    "bleed_missing": {"de": "Beschnitt zu klein: {bleed} mm (mindestens {min} mm)",
                      "en": "Bleed too small: {bleed} mm (minimum {min} mm)"},
    "mixed_sizes": {"de": "Abweichendes Seitenformat {size} (erste Seite {first})",
                    "en": "Different page size {size} (first page {first})"},
    "hairline": {"de": "Haarlinie: {width} pt (mindestens {min} pt)", "en": "Hairline: {width} pt (minimum {min} pt)"},
    "zero_line": {"de": "Linie mit Stärke 0 (dünnstmögliche Linie)", "en": "Line with width 0 (thinnest possible line)"},
    "transparency": {"de": "Transparenz verwendet ({what})", "en": "Transparency used ({what})"},
    "transparency_pdfx": {"de": "Transparenz ist in {pdfx} nicht erlaubt ({what})",
                          "en": "Transparency is not allowed in {pdfx} ({what})"},
    "pdfx_no_intent": {"de": "{pdfx}: Output Intent fehlt", "en": "{pdfx}: output intent missing"},
    "pdfx_no_trim": {"de": "{pdfx}: Seite ohne TrimBox oder ArtBox", "en": "{pdfx}: page without trim or art box"},
    "pdfx_ok": {"de": "Als {pdfx} gekennzeichnet", "en": "Marked as {pdfx}"},
    "not_pdfx": {"de": "Nicht als PDF/X gekennzeichnet", "en": "Not marked as PDF/X"},
    "spot_used": {"de": "Sonderfarbe {name} (Ersatz {alternate})", "en": "Spot color {name} (alternate {alternate})"},
    "spot_simulated": {"de": "Sonderfarben werden am Digitaldrucker über ihren CMYK-Ersatz gedruckt",
                       "en": "Spot colors are printed via their CMYK alternate on digital presses"},
    "encrypted": {"de": "Das PDF ist verschlüsselt", "en": "The PDF is encrypted"},
    "form_fields": {"de": "Formularfelder oder Kommentare vorhanden (werden ggf. nicht gedruckt)",
                    "en": "Form fields or annotations present (may not print)"},
    "check_failed": {"de": "Prüfung nicht möglich: {error}", "en": "Check not possible: {error}"},
}

WHAT = {
    "vector": {"de": "Vektorgrafik", "en": "vector graphics"},
    "image": {"de": "Bilder", "en": "images"},
    "shading": {"de": "Verläufe", "en": "shadings"},
    "opacity": {"de": "Deckkraft", "en": "opacity"},
    "soft_mask": {"de": "weiche Maske", "en": "soft mask"},
    "blend_mode": {"de": "Füllmethode", "en": "blend mode"},
    "group": {"de": "Transparenzgruppe", "en": "transparency group"},
    "image_mask": {"de": "Bild mit Alphakanal", "en": "image with alpha"},
}

RULE_TITLES = {
    "fonts": {"de": "Schriften", "en": "Fonts"},
    "images": {"de": "Bildauflösung", "en": "Image resolution"},
    "colors": {"de": "Farbräume", "en": "Color spaces"},
    "bleed": {"de": "Beschnitt", "en": "Bleed"},
    "sizes": {"de": "Seitenformate", "en": "Page sizes"},
    "lines": {"de": "Haarlinien", "en": "Hairlines"},
    "transparency": {"de": "Transparenz", "en": "Transparency"},
    "pdfx": {"de": "PDF/X", "en": "PDF/X"},
    "spots": {"de": "Sonderfarben", "en": "Spot colors"},
    "document": {"de": "Dokument", "en": "Document"},
}


# --- Inhalt durchlaufen -----------------------------------------------------------


def _mul(a, b):
    return (a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5])


def _scale(m) -> float:
    """Mittlere lineare Skalierung einer Matrix (für Linienstärken)."""
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


@dataclass
class _Usage:
    fonts: dict = field(default_factory=dict)  # Name -> eingebettet
    min_ppi: dict = field(default_factory=dict)  # (bitmap: bool) -> kleinste ppi
    rgb: set = field(default_factory=set)  # Fundstellen: "vector", "image", "shading"
    thinnest: float | None = None
    zero_line: bool = False
    transparency: set = field(default_factory=set)
    spots: dict = field(default_factory=dict)  # Name -> Ersatzfarbraum


def _font_embedded(font) -> bool:
    subtype = font.get("/Subtype")
    if subtype == Name.Type3:
        return True
    if subtype == Name.Type0:
        descendants = font.get("/DescendantFonts")
        if not descendants:
            return False
        font = descendants[0]
    descriptor = font.get("/FontDescriptor")
    if descriptor is None:
        return False
    return any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))


def _is_rgb(space) -> bool:
    fam = family(space)
    if fam in ("DeviceRGB", "CalRGB"):
        return True
    if fam == "ICCBased":
        return components(space) == 3
    if fam == "Indexed":
        return _is_rgb(space[1])
    return False


class _Scanner:
    def __init__(self, usage: _Usage) -> None:
        self.u = usage
        self.depth = 0

    def space_used(self, space, where: str) -> None:
        if space is None:
            return
        fam = family(space)
        if _is_rgb(space):
            self.u.rgb.add(where)
        if fam == "Separation":
            name = name_str(space[1])
            if name not in ("All", "None"):
                self.u.spots.setdefault(name, family(space[2]))
        elif fam == "DeviceN":
            for n in space[1]:
                name = name_str(n)
                if name not in ("Cyan", "Magenta", "Yellow", "Black", "None", "All"):
                    self.u.spots.setdefault(name, family(space[2]))
        elif fam == "Indexed":
            self.space_used(space[1], where)

    def resources_space(self, resources, name):
        if family(name) in ("DeviceGray", "DeviceRGB", "DeviceCMYK", "Pattern"):
            return name
        spaces = resources.get("/ColorSpace") if resources is not None else None
        return spaces.get(name) if spaces is not None else None

    def ext_gstate(self, gs) -> None:
        if gs is None:
            return
        if float(gs.get("/ca", 1)) < 1 or float(gs.get("/CA", 1)) < 1:
            self.u.transparency.add("opacity")
        smask = gs.get("/SMask")
        if smask is not None and smask != Name("/None"):
            self.u.transparency.add("soft_mask")
        bm = gs.get("/BM")
        if bm is not None:
            modes = [bm] if isinstance(bm, Name) else list(bm)
            if any(m not in (Name.Normal, Name("/Compatible")) for m in modes):
                self.u.transparency.add("blend_mode")

    def image(self, obj, ctm) -> None:
        space = obj.get("/ColorSpace")
        self.space_used(space, "image")
        if "/SMask" in obj:
            self.u.transparency.add("image_mask")
        width_in = math.hypot(ctm[0], ctm[1]) / 72
        height_in = math.hypot(ctm[2], ctm[3]) / 72
        if width_in <= 0 or height_in <= 0:
            return
        ppi = min(int(obj.Width) / width_in, int(obj.Height) / height_in)
        bitmap = bool(obj.get("/ImageMask", False)) or int(obj.get("/BitsPerComponent", 8)) == 1
        current = self.u.min_ppi.get(bitmap)
        if current is None or ppi < current:
            self.u.min_ppi[bitmap] = ppi

    def stream(self, stream, resources, ctm) -> None:
        if self.depth > 12:
            return
        self.depth += 1
        stack = []
        line_width = 1.0
        try:
            instructions = pikepdf.parse_content_stream(stream)
        except pikepdf.PdfError:
            self.depth -= 1
            return
        for item in instructions:
            if isinstance(item, pikepdf.ContentStreamInlineImage):
                continue
            ops, op = item.operands, str(item.operator)
            if op == "q":
                stack.append((ctm, line_width))
            elif op == "Q" and stack:
                ctm, line_width = stack.pop()
            elif op == "cm" and len(ops) == 6:
                ctm = _mul(tuple(float(v) for v in ops), ctm)
            elif op == "w" and ops:
                line_width = float(ops[0])
            elif op in ("S", "s", "B", "b", "B*", "b*"):
                if line_width == 0:
                    self.u.zero_line = True
                else:
                    width = line_width * _scale(ctm)
                    if self.u.thinnest is None or width < self.u.thinnest:
                        self.u.thinnest = width
            elif op in ("rg", "RG"):
                self.u.rgb.add("vector")
            elif op in ("cs", "CS") and ops:
                self.space_used(self.resources_space(resources, ops[0]), "vector")
            elif op == "gs" and ops and resources is not None and "/ExtGState" in resources:
                self.ext_gstate(resources.ExtGState.get(ops[0]))
            elif op == "sh" and ops and resources is not None and "/Shading" in resources:
                shading = resources.Shading.get(ops[0])
                if shading is not None:
                    self.space_used(shading.get("/ColorSpace"), "shading")
            elif op == "Tf" and ops and resources is not None and "/Font" in resources:
                font = resources.Font.get(ops[0])
                if font is not None:
                    name = str(font.get("/BaseFont", ops[0]))[1:]
                    self.u.fonts[name] = self.u.fonts.get(name, True) and _font_embedded(font)
            elif op == "Do" and ops and resources is not None and "/XObject" in resources:
                obj = resources.XObject.get(ops[0])
                if obj is None:
                    continue
                if obj.get("/Subtype") == Name.Image:
                    self.image(obj, ctm)
                elif obj.get("/Subtype") == Name.Form:
                    matrix = tuple(float(v) for v in obj.get("/Matrix", [1, 0, 0, 1, 0, 0]))
                    group = obj.get("/Group")
                    if group is not None and group.get("/S") == Name.Transparency:
                        self.u.transparency.add("group")
                    self.stream(obj, obj.get("/Resources", resources), _mul(matrix, ctm))
        self.depth -= 1


# --- Prüfung ---------------------------------------------------------------------


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _merge(findings: list[Finding], new: Finding) -> None:
    """Gleiche Befunde über Seiten zusammenfassen."""
    for f in findings:
        if f.rule == new.rule and f.key == new.key and f.args == new.args and f.severity == new.severity:
            f.pages.extend(p for p in new.pages if p not in f.pages)
            return
    findings.append(new)


def preflight(doc: PdfDocument, profile: PreflightProfile | None = None, file: str = "") -> PreflightReport:
    p = profile or PreflightProfile()
    pdfx = doc.pdfx_version()
    strict_color = bool(pdfx and pdfx.startswith("PDF/X-1"))
    no_transparency = bool(pdfx and pdfx.startswith(("PDF/X-1", "PDF/X-3")))
    findings: list[Finding] = []
    add = lambda f: _merge(findings, f)  # noqa: E731

    if doc.pdf.is_encrypted:
        add(Finding("document", Severity.ERROR, "encrypted"))

    first_size = None
    spots: dict[str, str] = {}
    for index, page in enumerate(doc.pdf.pages):
        usage = _Usage()
        try:
            _Scanner(usage).stream(page, page.obj.get("/Resources"), (1, 0, 0, 1, 0, 0))
        except Exception as exc:  # beschädigte Inhalte nicht zum Abbruch führen lassen
            add(Finding("document", Severity.WARNING, "check_failed", {"error": str(exc)}, [index]))
        group = page.obj.get("/Group")
        if group is not None and group.get("/S") == Name.Transparency:
            usage.transparency.add("group")
        if page.obj.get("/Annots"):
            add(Finding("document", Severity.WARNING, "form_fields", pages=[index]))

        if p.check_fonts:
            for font, embedded in sorted(usage.fonts.items()):
                if not embedded:
                    add(Finding("fonts", Severity.ERROR, "font_not_embedded", {"font": font}, [index]))
        if p.check_images:
            for bitmap, ppi in usage.min_ppi.items():
                minimum = p.min_ppi_bitmap if bitmap else p.min_ppi
                if ppi < minimum:
                    severity = Severity.ERROR if ppi < minimum / 2 else Severity.WARNING
                    add(Finding("images", severity, "image_low_ppi",
                                {"ppi": int(ppi), "min": int(minimum)}, [index]))
        if p.check_colors and usage.rgb:
            what = tuple(sorted(usage.rgb))
            if strict_color:
                add(Finding("colors", Severity.ERROR, "rgb_in_pdfx1a", {"pdfx": pdfx, "what": what}, [index]))
            elif p.cmyk_output:
                add(Finding("colors", Severity.WARNING, "rgb_used", {"what": what}, [index]))
        if p.check_lines:
            if usage.zero_line:
                add(Finding("lines", Severity.WARNING, "zero_line", pages=[index]))
            if usage.thinnest is not None and usage.thinnest < p.min_line_pt:
                add(Finding("lines", Severity.WARNING, "hairline",
                            {"width": _fmt(usage.thinnest, 2), "min": _fmt(p.min_line_pt, 2)}, [index]))
        if p.check_transparency and usage.transparency:
            what = tuple(sorted(usage.transparency))
            if no_transparency:
                add(Finding("transparency", Severity.ERROR, "transparency_pdfx", {"pdfx": pdfx, "what": what}, [index]))
            else:
                add(Finding("transparency", Severity.INFO, "transparency", {"what": what}, [index]))
        spots.update(usage.spots)

        has_trim = doc.has_box(index, "TrimBox")
        if p.check_bleed:
            if not has_trim:
                add(Finding("bleed", Severity.WARNING, "no_trimbox", pages=[index]))
            else:
                t = doc.box(index, "TrimBox")
                b = doc.box(index, "BleedBox")
                bleed = min(t[0] - b[0], t[1] - b[1], b[2] - t[2], b[3] - t[3]) / MM
                if bleed + 0.05 < p.bleed_mm:
                    add(Finding("bleed", Severity.WARNING, "bleed_missing",
                                {"bleed": _fmt(max(bleed, 0)), "min": _fmt(p.bleed_mm)}, [index]))
        if p.check_sizes:
            w, h = doc.page_size(index)
            size = f"{_fmt(w / MM, 0)} × {_fmt(h / MM, 0)} mm"
            if first_size is None:
                first_size = size
            elif size != first_size:
                add(Finding("sizes", Severity.WARNING, "mixed_sizes", {"size": size, "first": first_size}, [index]))
        if p.check_pdfx and pdfx and not (has_trim or doc.has_box(index, "ArtBox")):
            add(Finding("pdfx", Severity.ERROR, "pdfx_no_trim", {"pdfx": pdfx}, [index]))

    if p.check_pdfx:
        if pdfx:
            if not doc.has_pdfx_output_intent():
                add(Finding("pdfx", Severity.ERROR, "pdfx_no_intent", {"pdfx": pdfx}))
            else:
                add(Finding("pdfx", Severity.INFO, "pdfx_ok", {"pdfx": pdfx}))
        else:
            add(Finding("pdfx", Severity.INFO, "not_pdfx"))
    if p.check_spots and spots:
        for name, alternate in sorted(spots.items()):
            add(Finding("spots", Severity.INFO, "spot_used", {"name": name, "alternate": alternate}))
        add(Finding("spots", Severity.WARNING, "spot_simulated"))

    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    findings.sort(key=lambda f: (order[f.severity], f.rule))
    return PreflightReport(file or (doc.path.name if doc.path else ""), doc.page_count, pdfx, findings)


# --- Berichte ----------------------------------------------------------------------

_LABELS = {
    "title": {"de": "Preflight-Bericht", "en": "Preflight report"},
    "file": {"de": "Datei", "en": "File"},
    "pages": {"de": "Seiten", "en": "Pages"},
    "date": {"de": "Geprüft am", "en": "Checked on"},
    "result": {"de": "Ergebnis", "en": "Result"},
    "passed": {"de": "Keine Fehler", "en": "No errors"},
    "failed": {"de": "{n} Fehler, {w} Warnungen", "en": "{n} errors, {w} warnings"},
    "check": {"de": "Prüfung", "en": "Check"},
    "finding": {"de": "Befund", "en": "Finding"},
    "on_pages": {"de": "Seiten", "en": "Pages"},
    "none": {"de": "Keine Befunde.", "en": "No findings."},
    Severity.ERROR.value: {"de": "Fehler", "en": "Error"},
    Severity.WARNING.value: {"de": "Warnung", "en": "Warning"},
    Severity.INFO.value: {"de": "Hinweis", "en": "Info"},
}


def _label(key: str, language: str, **kw) -> str:
    return _LABELS[key].get(language, _LABELS[key]["de"]).format(**kw)


def _pages_text(pages: list[int]) -> str:
    from .pagerange import format_pages

    return format_pages(pages) if pages else "–"


def _result_text(report: PreflightReport, language: str) -> str:
    if report.ok and report.count(Severity.WARNING) == 0:
        return _label("passed", language)
    return _label("failed", language, n=report.count(Severity.ERROR), w=report.count(Severity.WARNING))


def report_html(report: PreflightReport, language: str = "de") -> str:
    colors = {Severity.ERROR: "#c62828", Severity.WARNING: "#b35c00", Severity.INFO: "#455a64"}
    rows = "".join(
        f"<tr><td style='color:{colors[f.severity]};font-weight:bold'>{_label(f.severity.value, language)}</td>"
        f"<td>{html.escape(RULE_TITLES[f.rule].get(language, f.rule))}</td><td>{html.escape(f.text(language))}</td>"
        f"<td>{_pages_text(f.pages)}</td></tr>"
        for f in report.findings
    ) or f"<tr><td colspan='4'>{_label('none', language)}</td></tr>"
    status = "#2e7d32" if report.ok else "#c62828"
    return f"""<!DOCTYPE html>
<html lang="{language}"><head><meta charset="utf-8"><title>{_label('title', language)}</title>
<style>body{{font-family:sans-serif;margin:2em;color:#222}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#f3f3f3}}</style>
</head><body>
<h1>{_label('title', language)}</h1>
<p>{_label('file', language)}: <b>{html.escape(report.file)}</b><br>
{_label('pages', language)}: {report.page_count} · PDF/X: {html.escape(report.pdfx or '–')}<br>
{_label('date', language)}: {report.created.strftime('%d.%m.%Y %H:%M')}</p>
<p style="font-size:1.2em;color:{status}"><b>{_label('result', language)}: {_result_text(report, language)}</b></p>
<table><tr><th></th><th>{_label('check', language)}</th><th>{_label('finding', language)}</th>
<th>{_label('on_pages', language)}</th></tr>{rows}</table>
</body></html>
"""


def report_pdf(report: PreflightReport, language: str = "de") -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from .layers import font

    from reportlab.pdfbase.pdfmetrics import registerFontFamily

    regular, bold = font(False), font(True)
    registerFontFamily(regular, normal=regular, bold=bold, italic=regular, boldItalic=bold)
    body = ParagraphStyle("body", fontName=regular, fontSize=9, leading=12)
    head = ParagraphStyle("head", fontName=bold, fontSize=16, leading=20, spaceAfter=8)
    buf = io.BytesIO()
    story = [
        Paragraph(_label("title", language), head),
        Paragraph(f"{_label('file', language)}: <b>{html.escape(report.file)}</b>", body),
        Paragraph(f"{_label('pages', language)}: {report.page_count} · PDF/X: {html.escape(report.pdfx or '–')}", body),
        Paragraph(f"{_label('date', language)}: {report.created.strftime('%d.%m.%Y %H:%M')}", body),
        Spacer(0, 6),
        Paragraph(f"<b>{_label('result', language)}: {_result_text(report, language)}</b>", body),
        Spacer(0, 8),
    ]
    data = [["", _label("check", language), _label("finding", language), _label("on_pages", language)]]
    for f in report.findings:
        data.append([_label(f.severity.value, language), RULE_TITLES[f.rule].get(language, f.rule),
                     Paragraph(html.escape(f.text(language)), body), _pages_text(f.pages)])
    if len(data) == 1:
        data.append(["", "", _label("none", language), ""])
    table = Table(data, colWidths=[55, 85, 280, 60], repeatRows=1)
    style = [("FONT", (0, 0), (-1, -1), regular, 9), ("FONT", (0, 0), (-1, 0), bold, 9),
             ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f3f3")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd"))]
    tone = {Severity.ERROR: "#c62828", Severity.WARNING: "#b35c00", Severity.INFO: "#455a64"}
    for row, f in enumerate(report.findings, start=1):
        style.append(("TEXTCOLOR", (0, row), (0, row), colors.HexColor(tone[f.severity])))
    table.setStyle(TableStyle(style))
    story.append(table)
    SimpleDocTemplate(buf, pagesize=A4, title=_label("title", language)).build(story)
    return buf.getvalue()


def write_report(report: PreflightReport, path: Path, language: str = "de") -> Path:
    """Bericht als ``.html`` oder ``.pdf`` (nach Dateiendung) speichern."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        path.write_bytes(report_pdf(report, language))
    else:
        path.write_text(report_html(report, language), encoding="utf-8")
    return path
