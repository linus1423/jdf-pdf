import io
import os
import stat
import sys
import time
import zipfile

import pikepdf
import pytest
from PIL import Image, ImageDraw
from reportlab.pdfgen.canvas import Canvas

from conftest import make_pdf
from jdfpdf.core import cleanup, external, importers, office, scan, softproof, tabs, textedit, vdp
from jdfpdf.core.hotfolder import ACCEPTED
from jdfpdf.core.impose import Imposition, Layout
from jdfpdf.core.jdf import Finishing, JobTicket, MediaRange, Punch, Sides, Staple
from jdfpdf.core.layers import layer_kinds
from jdfpdf.core.media import Media
from jdfpdf.core.pdfdoc import PdfDocument, Section
from jdfpdf.core.prepress import OutputOptions, write_output
from jdfpdf.core.template import Template, run_template

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="Shell-Skripte als Ersatzprogramme")


def text_pdf(lines=("Hallo Welt Hallo",), size=(400, 300), font="Helvetica") -> PdfDocument:
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=size)
    c.setFont(font, 20)
    for number, line in enumerate(lines):
        c.drawString(50, 200 - number * 40, line)
    c.showPage()
    c.save()
    return PdfDocument.from_bytes(buf.getvalue())


def page_text(doc: PdfDocument, index: int = 0) -> str:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(doc.to_bytes())
    return pdf[index].get_textpage().get_text_range()


def script(folder, name, body):
    path = folder / name
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


# --- Office -----------------------------------------------------------------------------

FAKE_SOFFICE = """
import sys, pathlib
from reportlab.pdfgen.canvas import Canvas
args = sys.argv[1:]
assert "--headless" in args and args[args.index("--convert-to") + 1] == "pdf"
assert any(a.startswith("-env:UserInstallation=file:") for a in args)
out = pathlib.Path(args[args.index("--outdir") + 1])
src = pathlib.Path(args[-1])
c = Canvas(str(out / (src.stem + ".pdf")))
c.drawString(72, 700, src.read_text())
c.showPage()
c.drawString(72, 700, "Seite 2")
c.showPage()
c.save()
"""


@posix_only
def test_office_conversion_with_libreoffice(tmp_path, monkeypatch):
    fake = script(tmp_path, "soffice", FAKE_SOFFICE)
    monkeypatch.setenv("JDFPDF_SOFFICE", str(fake))
    source = tmp_path / "brief.docx"
    source.write_text("Brieftext")
    assert office.is_office(source) and office.available_converter() == "libreoffice"
    doc = importers.load_document(source)
    assert doc.page_count == 2 and doc.path.name == "brief.pdf"
    assert "Brieftext" in page_text(doc)

    # im Hotfolder und in Vorlagen
    assert ".xlsx" in ACCEPTED and ".pptx" in ACCEPTED
    result = run_template(source, tmp_path / "out", Template(steps=[{"op": "rotate", "pages": "1"}]))
    assert result.pdf.name == "brief.pdf" and PdfDocument.open(result.pdf).page_rotation(0) == 90


def test_office_converter_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("JDFPDF_SOFFICE", str(tmp_path / "gibt-es-nicht"))
    monkeypatch.setattr(office, "ms_office_available", lambda: False)
    source = tmp_path / "tabelle.xlsx"
    source.write_bytes(b"x")
    with pytest.raises(office.ConverterMissing, match="LibreOffice"):
        importers.load_document(source)
    with pytest.raises(ValueError):
        importers.load_document(tmp_path / "x.xyz")


def test_ms_office_script_quotes_paths(tmp_path):
    ps = office.ms_office_script(tmp_path / "O'Brien.xlsx", tmp_path / "out.pdf")
    assert "Excel.Application" in ps and "O''Brien" in ps
    assert "PowerPoint" in office.ms_office_script(tmp_path / "a.pptx", tmp_path / "a.pdf")


@pytest.mark.skipif(not os.environ.get("JDFPDF_TEST_LIBREOFFICE"), reason="nur mit installiertem LibreOffice Writer")
def test_office_real_libreoffice(tmp_path):
    source = tmp_path / "notiz.txt"
    source.write_text("Echte Umwandlung", encoding="utf-8")
    doc = PdfDocument.from_bytes(office.convert_libreoffice(source, timeout=240))
    assert doc.page_count >= 1 and "Echte Umwandlung" in page_text(doc)


# --- Scannen ----------------------------------------------------------------------------


def test_sane_parsing_and_command(tmp_path):
    found = scan.parse_sane_list("pixma:04A9\tCanon PIXMA MG5200\nairscan:e0\tHP LaserJet\n\n")
    assert [s.id for s in found] == ["pixma:04A9", "airscan:e0"] and found[0].name == "Canon PIXMA MG5200"
    cmd = scan.sane_command("pixma:04A9", scan.ScanOptions(200, "gray", "ADF Duplex", True), tmp_path)
    assert "--mode=Gray" in cmd and "--source=ADF Duplex" in cmd and "--resolution=200" in cmd
    assert any(c.startswith("--batch=") for c in cmd)
    ps = scan.wia_script("dev'1", scan.ScanOptions(300, "color", "ADF", True), tmp_path)
    assert "dev''1" in ps and "while ($true)" in ps


@posix_only
def test_scan_with_fake_scanimage(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script(bin_dir, "scanimage", """
import sys, io
from PIL import Image
args = sys.argv[1:]
if "-f" in args:
    print("test:0\\tTestscanner")
    sys.exit(0)
batch = [a for a in args if a.startswith("--batch=")]
img = Image.new("L", (100, 140), 255)
if batch:
    for n in (1, 2):
        img.save(batch[0].split("=", 1)[1] % n, format="TIFF", dpi=(100, 100))
    print("Document feeder out of documents", file=sys.stderr)
    sys.exit(7)
buf = io.BytesIO()
img.save(buf, format="TIFF", dpi=(100, 100))
sys.stdout.buffer.write(buf.getvalue())
""")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert scan.backend() == "sane"
    assert scan.list_scanners()[0].id == "test:0"
    single = scan.scan("test:0", scan.ScanOptions(100), tmp_path / "a")
    assert len(single) == 1 and Image.open(single[0]).size == (100, 140)
    batch = scan.scan("test:0", scan.ScanOptions(100, batch=True, source="ADF"), tmp_path / "b")
    assert [p.name for p in batch] == ["scan0001.tif", "scan0002.tif"]


# --- Bereinigen -------------------------------------------------------------------------


def lines_image(angle=0.0, speck=False) -> Image.Image:
    img = Image.new("L", (1000, 1300), 255)
    draw = ImageDraw.Draw(img)
    for y in range(200, 1100, 36):
        draw.rectangle((150, y, 850, y + 12), fill=0)
    if speck:
        for x, y in ((60, 60), (940, 1240), (500, 150)):
            draw.point((x, y), fill=0)
    return img.rotate(angle, fillcolor=255) if angle else img


def test_deskew_despeckle_align_erase():
    assert cleanup.detect_skew(lines_image(1.5)) == pytest.approx(-1.5, abs=0.15)
    assert cleanup.detect_skew(lines_image()) == pytest.approx(0, abs=0.1)
    straight, angle = cleanup.deskew(lines_image(-2.0))
    assert angle == pytest.approx(2.0, abs=0.15)

    speckled = lines_image(speck=True)
    assert speckled.getpixel((60, 60)) == 0
    assert cleanup.despeckle(speckled, 3).getpixel((60, 60)) == 255
    colored = Image.new("RGB", (50, 50), "white")
    colored.putpixel((25, 25), (255, 0, 0))
    assert cleanup.despeckle(colored, 3).getpixel((25, 25)) == (255, 255, 255)

    img = Image.new("L", (400, 400), 255)
    ImageDraw.Draw(img).rectangle((10, 10, 60, 40), fill=0)
    centered = cleanup.align(img, "center")
    x0, y0, x1, y1 = cleanup.content_bbox(centered)
    assert abs((x0 + x1) / 2 - 200) <= 2 and abs((y0 + y1) / 2 - 200) <= 2
    assert cleanup.content_bbox(cleanup.align(img, "top_left", 50))[:2] == (50, 50)
    assert cleanup.content_bbox(cleanup.erase(img, [(0, 0, 100, 100)])) is None
    assert cleanup.content_bbox(cleanup.erase_border(img, 70)) is None

    opts = cleanup.CleanupOptions(dpi=100, mode="lineart", despeckle=3, deskew=True, erase=[(0, 0, 5, 5)])
    result, angle = cleanup.clean_image(lines_image(1.0, speck=True).convert("RGB"), opts)
    assert result.mode == "1" and angle == pytest.approx(-1.0, abs=0.15)


def test_cleanup_and_rasterize_pages(tmp_path):
    doc = text_pdf(size=(400, 300))
    doc.set_box(0, "TrimBox", (10, 10, 390, 290))
    doc.rotate_page(0, 0)
    two = text_pdf(size=(400, 300))
    two.rotate_page(0, 90)
    doc.insert_pages_from(two)
    angles = cleanup.cleanup_pages(doc, [0, 1], cleanup.CleanupOptions(dpi=72, mode="gray", deskew=True))
    assert set(angles) == {0, 1}
    assert doc.box(0, "MediaBox") == pytest.approx((0, 0, 400, 300), abs=0.1)
    assert doc.box(0, "TrimBox") == pytest.approx((10, 10, 390, 290))
    assert doc.page_rotation(1) == 0 and doc.box(1, "MediaBox") == pytest.approx((0, 0, 300, 400), abs=0.1)
    image = doc.pdf.pages[0].Resources.XObject.Im0
    assert image.ColorSpace == pikepdf.Name.DeviceGray and image.Filter == pikepdf.Name.DCTDecode

    cleanup.rasterize_pages(doc, [0], dpi=72, mode="lineart")
    assert doc.pdf.pages[0].Resources.XObject.Im0.BitsPerComponent == 1
    assert "Hallo" not in page_text(doc)  # kein Text mehr, nur Bild


# --- Text -------------------------------------------------------------------------------


def test_text_block_and_replace():
    doc = text_pdf()
    textedit.add_text_block(doc, [0], textedit.TextBlock("Erste Zeile ist lang genug\nZweite", width_mm=30,
                                                         background_cmyk=(0, 0, 0, 0)))
    assert "text" in layer_kinds(doc, 0)
    text = page_text(doc)
    assert "Zweite" in text and "Erste Zeile" in text

    hits = textedit.find_text(doc, "welt")
    assert len(hits) == 1 and hits[0].size == pytest.approx(20) and hits[0].origin[1] == pytest.approx(200)
    assert textedit.find_text(doc, "welt", match_case=True) == []
    assert textedit.replace_text(doc, "Welt", "Erde") == 1
    assert "Erde" in page_text(doc)
    assert textedit.remove_text_edits(doc, [0]) == 2
    assert "Erde" not in page_text(doc)


def test_rewrite_simple_font():
    doc = text_pdf(["Hallo Welt", "Welt Welt"])
    report = textedit.rewrite_text(doc, "Welt", "Größe")
    assert report.replaced == 3 and not report.skipped
    assert "Hallo Größe" in page_text(doc) and "Welt" not in page_text(doc)
    # nicht in WinAnsi darstellbar
    report = textedit.rewrite_text(doc, "Hallo", "Привет")
    assert report.replaced == 0 and "nicht in der Schrift" in report.skipped[0]


def test_rewrite_refuses_missing_subset_glyphs():
    from jdfpdf.core.layers import font

    doc = text_pdf(["abc Welt"], font=font())  # eingebettete TrueType-Teilschrift (reportlab)
    report = textedit.rewrite_text(doc, "Welt", "Xyz")
    assert report.replaced == 0 and "Xyz" in report.skipped[0]
    report = textedit.rewrite_text(doc, "Welt", "Wab cle")  # nur Glyphen aus der Teilschrift
    assert report.replaced == 1 and "abc Wab cle" in page_text(doc)


# --- Serienbrief ------------------------------------------------------------------------


def write_xlsx(path, rows):
    shared = sorted({v for r in rows for v in r if isinstance(v, str)})
    cells = []
    for r, row in enumerate(rows, start=1):
        items = []
        for c, value in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(value, str):
                items.append(f'<c r="{ref}" t="s"><v>{shared.index(value)}</v></c>')
            elif value is not None:
                items.append(f'<c r="{ref}"><v>{value}</v></c>')
        cells.append(f'<row r="{r}">{"".join(items)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="Daten" sheetId="1" '
                                      f'r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}>' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData>{"".join(cells)}</sheetData></worksheet>')


def test_vdp_sources(tmp_path):
    csv_path = tmp_path / "daten.csv"
    csv_path.write_bytes("Name;Nr\nMüller;1\nSchmidt;2\n".encode("cp1252"))
    data = vdp.read_data(csv_path)
    assert data.headers == ["Name", "Nr"] and data.rows[0]["Name"] == "Müller" and len(data) == 2
    comma = tmp_path / "komma.csv"
    comma.write_text("a,b\n1,2\n", encoding="utf-8")
    assert vdp.read_csv(comma).rows == [{"a": "1", "b": "2"}]

    xlsx = tmp_path / "daten.xlsx"
    write_xlsx(xlsx, [["Name", "Menge", None, "Name"], ["Meier", 3, None, "x"], ["Huber", 2.5, None, None]])
    assert vdp.xlsx_sheets(xlsx) == ["Daten"]
    data = vdp.read_data(xlsx)
    assert data.headers == ["Name", "Menge", "Spalte3", "Name_2"]
    assert data.rows[0] == {"Name": "Meier", "Menge": "3", "Spalte3": "", "Name_2": "x"}
    assert data.rows[1]["Menge"] == "2.5"
    with pytest.raises(ValueError):
        vdp.read_data(tmp_path / "x.doc")


def test_vdp_merge_and_template(tmp_path):
    Image.new("RGB", (40, 20), "red").save(tmp_path / "logo.png")
    data_path = tmp_path / "d.csv"
    data_path.write_text("Name;Code;Bild\nMüller;4006381333931;logo.png\nSchmidt;12345;\nKrause;7;\n",
                         encoding="utf-8")
    template = PdfDocument.open(make_pdf(tmp_path / "t.pdf", pages=2))
    fields = [vdp.Field("text", "Hallo {Name} ({#})"), vdp.Field("qr", "{Code}", page=2),
              vdp.Field("ean13", "{Code}", y_mm=60), vdp.Field("code128", "{Code}", y_mm=90, width_mm=50),
              vdp.Field("image", "{Bild}", y_mm=120, width_mm=30), vdp.Field("text", "{Unbekannt}", page=2)]
    data = vdp.read_data(data_path)
    out = vdp.merge(template, data, fields, [0, 2], sections=True, section_title="{Name}")
    assert out.page_count == 4 and [s.title for s in out.sections()] == ["Müller", "Krause"]
    assert "Hallo Müller (1)" in page_text(out, 0) and "Hallo Krause (3)" in page_text(out, 2)
    assert "{Unbekannt}" in page_text(out, 1)
    assert template.page_count == 2

    setup = vdp.VdpSetup(str(data_path), 0, fields, "1-2", True)
    setup.save(tmp_path / "s.jdfvdp")
    loaded = vdp.VdpSetup.load(tmp_path / "s.jdfvdp")
    assert loaded.fields[0] == fields[0]
    tpl = Template(steps=[{"op": "vdp", "data": "d.csv", "fields": [vars(f) for f in fields], "records": "1-2",
                           "sections": True}])
    result = run_template(tmp_path / "t.pdf", tmp_path / "out", tpl, base_dir=tmp_path)
    merged = PdfDocument.open(result.pdf)
    assert merged.page_count == 4 and [s.title for s in merged.sections()] == ["1", "2"]


# --- Externe Programme ------------------------------------------------------------------


def test_external_editor_roundtrip(tmp_path):
    editors = [external.ExternalEditor("Ink", "/usr/bin/inkscape", "--pdf-poppler {file}")]
    external.save_editors(editors, tmp_path / "editors.json")
    assert external.load_editors(tmp_path / "editors.json") == editors
    with pytest.raises(ValueError):
        external.save_editors(editors * 11, tmp_path / "x.json")
    assert external.ExternalEditor("A", "prog", "").argv(tmp_path / "f.pdf") == ["prog", str(tmp_path / "f.pdf")]

    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf", pages=3))
    session = external.EditSession(external.ExternalEditor("X", sys.executable, "-c pass"), doc, 1, "a")
    assert session.path.exists() and PdfDocument.open(session.path).page_size(0)[0] == pytest.approx(596)
    session.start()
    assert not session.poll()
    # Programm speichert eine geänderte Fassung mit zwei Seiten
    edited = PdfDocument.open(make_pdf(tmp_path / "e.pdf", pages=2))
    edited.rotate_page(0, 90)
    time.sleep(0.01)
    edited.save(session.path)
    assert not session.poll()  # erst stabil über zwei Abfragen
    assert session.poll()
    assert not session.poll()
    assert external.apply_edit(doc, session.index, session.read()) == 2
    assert doc.page_count == 4 and doc.page_rotation(1) == 90
    session.close()
    assert not session.folder.exists()
    with pytest.raises(FileNotFoundError):
        external.EditSession(external.ExternalEditor("Y", str(tmp_path / "fehlt")), doc, 0).start()


# --- Softproof --------------------------------------------------------------------------


def test_softproof(tmp_path):
    pdf = pikepdf.new()
    for _ in range(4):
        pdf.add_blank_page(page_size=(595, 842))
    buf = io.BytesIO()
    pdf.save(buf)
    doc = PdfDocument.from_bytes(buf.getvalue())
    doc.set_box(1, "TrimBox", (10, 10, 585, 832))
    doc.set_sections([Section("A", 0), Section("B", 2)])
    tabs.insert_tabs_for_sections(doc)
    assert tabs.TAB_KEY in doc.pdf.pages[0].obj
    ticket = JobTicket("Proof", "", sides=Sides.DUPLEX_LONG_EDGE, media=Media("Weiß", color="White"),
                       media_ranges=[MediaRange(2, 2, Media("Gelb", color="Yellow"))],
                       finishing=Finishing(Staple.LEFT_TWO, Punch.TWO_LEFT), customer="Kunde")
    proof = softproof.build_proof(doc, ticket, softproof.ProofOptions(watermark="PROOF"))
    assert proof.page_count == doc.page_count + 1
    assert "Proof" in page_text(proof, 0) and "Kunde" in page_text(proof, 0)
    pad = softproof.PAD
    # Registerblatt vorne: Dokumentseite 2 ist die beschnittene Seite
    assert proof.page_size(3) == pytest.approx((575 + 2 * pad, 822 + 2 * pad), abs=0.1)

    import pypdfium2 as pdfium

    rendered = pdfium.PdfDocument(proof.to_bytes())
    yellow = rendered[3].render(scale=0.25).to_pil().convert("RGB")
    r, g, b = yellow.getpixel((yellow.width // 2, yellow.height // 3))
    assert r > 240 and g > 220 and b < 200  # Medienfarbe Gelb
    tab = rendered[1].render(scale=0.25).to_pil().convert("RGB")
    corner = tab.getpixel((tab.width - int(pad * 0.25) - 3, tab.height - int(pad * 0.25) - 3))
    assert corner != (255, 255, 255)  # unter dem Tab: Arbeitsfläche, nicht Papier

    spreads = softproof.build_proof(doc, ticket, softproof.ProofOptions(spreads=True, info_page=False))
    assert spreads.page_count == 1 + (doc.page_count - 1 + 1) // 2
    sheets = softproof.build_proof(doc, ticket, softproof.ProofOptions(sheets=True, info_page=False),
                                   Imposition(layout=Layout.BOOKLET, auto_sheet=True))
    assert sheets.page_count >= 1

    result = write_output(PdfDocument.from_bytes(doc.to_bytes()), ticket, tmp_path / "job.pdf",
                          OutputOptions(embed=False, sidecar=False, softproof=True))
    assert (tmp_path / "job_proof.pdf") in result.extra_files


def test_template_text_and_cleanup_steps(tmp_path):
    source = tmp_path / "t.pdf"
    text_pdf(["Hallo Welt"]).save(source)
    tpl = Template(steps=[
        {"op": "replace_text", "find": "Welt", "replace": "Erde", "rewrite": True},
        {"op": "replace_text", "find": "Fehlt", "replace": "x"},
        {"op": "text_block", "pages": "1", "block": {"text": "Neu", "color_cmyk": [0, 100, 0, 0]}},
        {"op": "rasterize", "pages": "1", "dpi": 50, "mode": "gray"},
        {"op": "cleanup", "pages": "1", "options": {"dpi": 50, "deskew": True, "erase": [[0, 0, 5, 5]]}},
    ])
    result = run_template(source, tmp_path / "out", tpl)
    assert any(w.startswith("text_not_found:Fehlt") for w in result.warnings)
    out = PdfDocument.open(result.pdf)
    assert "Im0" in str(list(out.pdf.pages[0].Resources.XObject.keys()))
