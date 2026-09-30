import io

import pikepdf
import pytest
from reportlab.lib.colors import CMYKColor
from reportlab.pdfgen.canvas import Canvas

from jdfpdf.cli import main as cli_main
from jdfpdf.core import ppf
from jdfpdf.core.color import convert_to_gray, detect_color_pages
from jdfpdf.core.jdf import JobTicket, Sides
from jdfpdf.core.pdfdoc import PdfDocument
from jdfpdf.core.preflight import PreflightProfile, Severity, preflight, report_html, report_pdf
from jdfpdf.core.prepress import OutputOptions, write_output
from test_color import colored_pdf

MM = 72 / 25.4


def cmyk_sheet() -> bytes:
    """100 × 100 mm: linke Hälfte 100 % Cyan, rechts 50 % Schwarz, eine Haarlinie."""
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(100 * MM, 100 * MM))
    c.setFillColor(CMYKColor(1, 0, 0, 0))
    c.rect(0, 0, 50 * MM, 100 * MM, fill=1, stroke=0)
    c.setFillColor(CMYKColor(0, 0, 0, 0.5))
    c.rect(50 * MM, 0, 50 * MM, 100 * MM, fill=1, stroke=0)
    c.setLineWidth(0.1)
    c.line(0, 0, 10, 10)
    c.showPage()
    c.save()
    return buf.getvalue()


def shaded_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    fn = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[1, 0, 0], C1=[0, 0, 1], N=1)
    shading = pikepdf.Dictionary(ShadingType=2, ColorSpace=pikepdf.Name.DeviceRGB, Coords=[0, 0, 100, 0],
                                 Function=fn, Extend=[True, True])
    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(Shading=pikepdf.Dictionary(Sh0=pdf.make_indirect(shading)))
    page.Contents = pdf.make_stream(b"/Sh0 sh")
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_shading_converted_to_gray():
    doc = PdfDocument.from_bytes(shaded_pdf())
    assert detect_color_pages(doc) == [0]
    report = convert_to_gray(doc, [0])
    assert not report.skipped
    assert detect_color_pages(doc) == []


def test_ink_zones_and_separations():
    doc = PdfDocument.from_bytes(cmyk_sheet())
    profile = ppf.PressProfile("test", zone_count=4, zone_width_mm=25)
    side = ppf.analyse_side(doc, 0, profile)
    by_name = {s.name: s for s in side.separations}
    assert list(by_name) == ["Cyan", "Magenta", "Yellow", "Black"]
    assert by_name["Cyan"].coverage == pytest.approx(50, abs=2)
    assert by_name["Cyan"].zones[:2] == [pytest.approx(100, abs=3)] * 2
    assert by_name["Cyan"].zones[2:] == [pytest.approx(0, abs=3)] * 2
    assert by_name["Black"].zones[3] == pytest.approx(50, abs=3)
    assert by_name["Magenta"].coverage == 0
    # breitere Maschine: Bogen mittig, äußere Zonen leer
    wide = ppf.zone_coverage(by_name["Cyan"].plate, (100, 100), ppf.PressProfile("w", 6, 25))
    assert wide[0] == 0 and wide[1] == pytest.approx(100, abs=3) and wide[5] == 0


def test_spot_separation_and_ppf_file():
    doc = PdfDocument.from_bytes(colored_pdf())
    side = ppf.analyse_side(doc, 0, ppf.PressProfile("t", 6, 30))
    names = [s.name for s in side.separations]
    assert names == ["Cyan", "Magenta", "Yellow", "Black", "PANTONE 185 C"]
    spot = side.separations[-1]
    assert spot.coverage > 10
    data = ppf.write_ppf(ppf.Sheet(1, side), "Auftrag (1)")
    text = data.decode("latin-1")
    assert text.startswith("%!PS-Adobe-3.0\n%%CIP3-File Version 3.0")
    assert text.count("CIP3BeginSeparation") == 5
    assert "/CIP3AdmJobName (Auftrag \\(1\\)) def" in text
    assert text.rstrip().endswith("%%CIP3EndOfFile")


def test_output_with_ppf_and_preflight(tmp_path):
    doc = PdfDocument.from_bytes(colored_pdf())
    ticket = JobTicket("job", "", sides=Sides.DUPLEX_LONG_EDGE)
    options = OutputOptions(ppf=True, ppf_embed=True, preflight=True, ppf_profile=ppf.PressProfile("t", 6, 30))
    result = write_output(doc, ticket, tmp_path / "a.pdf", options)
    names = sorted(p.name for p in result.extra_files)
    assert names == ["a.jdf", "a_001.ppf", "a_002.ppf", "a_preflight.html", "a_zones.csv"]
    assert "CIP3BeginBack" in (tmp_path / "a_001.ppf").read_text(encoding="latin-1")
    attached = {a.name for a in PdfDocument.open(tmp_path / "a.pdf").attachments()}
    assert {"a_001.ppf", "a_002.ppf", "job.jdf"} <= attached
    assert any(w.startswith("preflight_errors:") for w in result.warnings)
    csv = (tmp_path / "a_zones.csv").read_text(encoding="utf-8")
    assert csv.splitlines()[0].startswith("Bogen;Seite;Farbe")


def test_preflight_findings():
    doc = PdfDocument.from_bytes(cmyk_sheet())
    report = preflight(doc)
    keys = {f.key for f in report.findings}
    assert "hairline" in keys and "no_trimbox" in keys and "rgb_used" not in keys
    doc.set_box(0, "TrimBox", (3 * MM, 3 * MM, 97 * MM, 97 * MM))
    doc.set_box(0, "BleedBox", (1 * MM, 1 * MM, 99 * MM, 99 * MM))
    report = preflight(doc, PreflightProfile(check_lines=False))
    bleed = [f for f in report.findings if f.key == "bleed_missing"]
    assert bleed and bleed[0].args["bleed"] == "2"
    assert "hairline" not in {f.key for f in report.findings}

    colored = preflight(PdfDocument.from_bytes(colored_pdf()))
    by_key = {f.key: f for f in colored.findings}
    assert by_key["font_not_embedded"].severity == Severity.ERROR
    assert by_key["image_low_ppi"].pages == [0, 2]
    assert any("Vektorgrafik" in f.text("de") for f in colored.findings if f.key == "rgb_used")
    assert by_key["spot_used"].args["name"] == "PANTONE 185 C"
    assert "Preflight report" in report_html(colored, "en")
    assert report_pdf(colored).startswith(b"%PDF")


def test_preflight_pdfx_rules(tmp_path):
    from conftest import make_pdf
    doc = PdfDocument.open(make_pdf(tmp_path / "x.pdf", pages=1, pdfx="PDF/X-1a:2001"))
    keys = {f.key for f in preflight(doc).findings}
    assert {"pdfx_no_intent", "pdfx_no_trim"} <= keys


def test_cli_preflight_and_ppf(tmp_path):
    src = tmp_path / "in.pdf"
    src.write_bytes(cmyk_sheet())
    assert cli_main([str(src), "-o", str(tmp_path / "out"), "--preflight", "--ppf", "--no-sidecar",
                     "--press", ppf.default_profiles()[1].name]) == 0
    assert (tmp_path / "out" / "in.ppf").exists()
    assert (tmp_path / "out" / "in_preflight.html").exists()
