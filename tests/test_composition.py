import pikepdf
import pytest
from PIL import Image

from conftest import make_pdf
from jdfpdf.core.geometry import ScaleMode, scale_page, shift_content, shift_for_binding
from jdfpdf.core.history import History
from jdfpdf.core.jdf import Finishing, JobTicket, MediaRange, Staple
from jdfpdf.core.media import MM, Media
from jdfpdf.core.pdfdoc import PdfDocument, Section
from jdfpdf.core.project import Project
from jdfpdf.core.render import render_page


def widths(doc):
    return [round(doc.page_size(i)[0]) for i in range(doc.page_count)]


def test_duplicate_blank_replace(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf"))
    assert doc.duplicate_pages([0, 1]) == [2, 3]
    assert widths(doc) == [595, 596, 595, 596, 597]
    doc.rotate_page(2, 90)  # Kopie ist unabhängig vom Original
    assert doc.page_rotation(0) == 0
    doc.insert_blank(0)
    assert doc.page_count == 6 and widths(doc)[0] == 595
    other = PdfDocument.open(make_pdf(tmp_path / "b.pdf", pages=1))
    other.pdf.pages[0].mediabox = [0, 0, 300, 300]
    doc.replace_page(1, other)
    assert doc.page_count == 6 and widths(doc)[1] == 300
    doc.save(tmp_path / "out.pdf")
    assert len(pikepdf.open(tmp_path / "out.pdf").pages) == 6


def test_insert_images(tmp_path):
    Image.new("RGB", (300, 150), "red").save(tmp_path / "a.png", dpi=(150, 150))
    frames = [Image.new("L", (100, 100), v) for v in (0, 128)]
    frames[0].save(tmp_path / "b.tif", save_all=True, append_images=frames[1:], dpi=(100, 100))
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf", pages=1))
    assert doc.insert_images([tmp_path / "a.png", tmp_path / "b.tif"], at=0) == 3
    assert doc.page_count == 4
    w, h = doc.page_size(0)
    assert round(w) == 144 and round(h) == 72  # 300 px bei 150 dpi = 2 Zoll


def test_sections(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf", pages=4))
    doc.set_sections([Section("Teil B", 2), Section("Teil A", 0)])
    assert doc.sections() == [Section("Teil A", 0), Section("Teil B", 2)]
    doc.move_page(3, 0)  # Lesezeichen hängen an Seitenobjekten
    assert doc.sections() == [Section("Teil A", 1), Section("Teil B", 3)]
    doc.insert_pages_from(PdfDocument.open(make_pdf(tmp_path / "b.pdf", pages=2)), at=1, section="Neu")
    assert [(s.title, s.page) for s in doc.sections()] == [("Neu", 1), ("Teil A", 3), ("Teil B", 5)]
    assert doc.section_of_page(4).title == "Teil A"
    doc.save(tmp_path / "s.pdf")
    assert len(PdfDocument.open(tmp_path / "s.pdf").sections()) == 3


def test_sections_from_nested_bookmarks(tmp_path):
    pdf = pikepdf.open(make_pdf(tmp_path / "a.pdf", pages=4))
    with pdf.open_outline() as o:
        top = pikepdf.OutlineItem("Kapitel", 0)
        top.children.append(pikepdf.OutlineItem("Unterkapitel", 2))
        o.root.append(top)
    pdf.save(tmp_path / "b.pdf")
    doc = PdfDocument.open(tmp_path / "b.pdf")
    assert [s.page for s in doc.sections()] == [0]
    doc.sections_from_bookmarks(level=2)
    assert [(s.title, s.page) for s in doc.sections()] == [("Kapitel", 0), ("Unterkapitel", 2)]


def test_boxes_and_bleed(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf", pages=1))
    assert doc.box(0, "TrimBox") == (0, 0, 595, 842)  # Rückfall auf MediaBox
    doc.add_bleed(0, 3 * MM)
    b = 3 * MM
    assert doc.box(0, "TrimBox") == (0, 0, 595, 842)
    assert doc.box(0, "BleedBox") == pytest.approx((-b, -b, 595 + b, 842 + b))
    assert doc.box(0, "MediaBox") == doc.box(0, "BleedBox")
    doc.set_box(0, "ArtBox", (10, 10, 100, 100))
    assert doc.has_box(0, "ArtBox")
    doc.set_box(0, "ArtBox", None)
    assert not doc.has_box(0, "ArtBox")


def _red_page(tmp_path, w=200, h=100):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(w, h))
    pdf.pages[0].Contents = pdf.make_stream(f"1 0 0 rg 0 0 {w} {h} re f".encode())
    pdf.save(tmp_path / "red.pdf")
    return PdfDocument.open(tmp_path / "red.pdf")


def test_scale_fit_and_fill(tmp_path):
    doc = _red_page(tmp_path)
    scale_page(doc, 0, 400, 400, ScaleMode.FIT)
    assert doc.box(0, "MediaBox") == (0, 0, 400, 400)
    img = render_page(doc.to_bytes(), 0, scale=0.25).convert("RGB")  # 100x100 px
    assert img.getpixel((50, 5)) == (255, 255, 255)  # Rand oben weiß
    assert img.getpixel((50, 50))[1] < 50  # Mitte rot
    doc = _red_page(tmp_path)
    scale_page(doc, 0, 400, 400, ScaleMode.FILL)
    img = render_page(doc.to_bytes(), 0, scale=0.25).convert("RGB")
    assert img.getpixel((50, 2))[1] < 50  # gefüllt


def test_shift_content(tmp_path):
    doc = _red_page(tmp_path)
    shift_content(doc, 0, 100, 0)
    img = render_page(doc.to_bytes(), 0, scale=0.5).convert("RGB")  # 100x50 px
    assert img.getpixel((10, 25)) == (255, 255, 255)
    assert img.getpixel((80, 25))[1] < 50
    doc = PdfDocument.open(make_pdf(tmp_path / "x.pdf", pages=2))
    shift_for_binding(doc, [0, 1], 10)
    assert b"10.0000 0.0000 cm" in doc.pdf.pages[0].Contents[0].read_bytes()
    assert b"-10.0000 0.0000 cm" in doc.pdf.pages[1].Contents[0].read_bytes()


def test_history():
    h = History(limit=2)
    h.push("a"); h.push("b"); h.push("c")
    assert h.undo("d") == "c" and h.undo("c") == "b" and h.undo("b") is None
    assert h.redo("b") == "c"
    h.push("x")
    assert not h.can_redo


def test_project_roundtrip(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf"))
    ticket = JobTicket("Job", "", copies=3, media=Media("A", weight_gsm=90),
                       media_ranges=[MediaRange(1, 2, Media("B", color="Yellow"))],
                       finishing=Finishing(staple=Staple.SADDLE))
    Project(doc, ticket, extra={"k": 1}).save(tmp_path / "p.jdfproj")
    loaded = Project.load(tmp_path / "p.jdfproj")
    assert loaded.document.page_count == 3
    assert loaded.ticket.copies == 3 and loaded.ticket.finishing.staple == Staple.SADDLE
    assert loaded.ticket.media_ranges[0].media.color == "Yellow"
    assert loaded.ticket.job_id == ticket.job_id
    assert loaded.extra == {"k": 1}
