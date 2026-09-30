import pypdfium2 as pdfium
import pytest
from PIL import Image

from conftest import make_pdf
from jdfpdf.core.elements import (
    Anchor, Context, Element, apply_element, footer, image_element, page_numbers, remove_elements, stamp, watermark,
)
from jdfpdf.core.layers import layer_kinds
from jdfpdf.core.marks import BarcodeType, MarkOptions, add_marks, remove_marks
from jdfpdf.core.media import MM, Media
from jdfpdf.core.pdfdoc import PdfDocument, Section
from jdfpdf.core.render import render_page
from jdfpdf.core.spine import add_spine_text, spine_width_mm
from jdfpdf.core.tabs import BleedTabStyle, TabSheetStyle, apply_bleed_tabs, insert_tabs_for_sections, titles_from_text


def texts(doc):
    pdf = pdfium.PdfDocument(doc.to_bytes())
    return [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]


def text_boxes(doc, index):
    """(Text, x, y) aller Zeichenketten einer Seite, y von unten."""
    pdf = pdfium.PdfDocument(doc.to_bytes())
    tp = pdf[index].get_textpage()
    return [tp.get_text_range(), tp.get_charbox(0) if tp.count_chars() else None]


@pytest.fixture
def doc(tmp_path):
    return PdfDocument.open(make_pdf(tmp_path / "a.pdf", pages=3))


def test_footer_with_placeholders(doc):
    doc.set_sections([Section("Kapitel", 0)])
    apply_element(doc, [0, 1, 2], footer("{job} {section} {page}/{pages}"), Context(job="J1"))
    assert [t.strip() for t in texts(doc)] == ["J1 Kapitel 1/3", "J1 Kapitel 2/3", "J1 Kapitel 3/3"]
    _, box = text_boxes(doc, 0)
    assert box[1] < 50  # unten
    assert layer_kinds(doc, 0) == ["element"]
    assert remove_elements(doc, [0, 1, 2]) == 3
    assert [t.strip() for t in texts(doc)] == ["", "", ""]


def test_page_numbers_mirror(doc):
    apply_element(doc, [0, 1], page_numbers("{page}"))
    _, right = text_boxes(doc, 0)
    _, left = text_boxes(doc, 1)
    assert right[0] > 400 and left[0] < 200


def test_rotated_page_header_is_visually_on_top(doc):
    doc.rotate_page(0, 90)
    apply_element(doc, [0], Element(text="OBEN", anchor=Anchor.TOP_CENTER))
    img = render_page(doc.to_bytes(), 0, scale=0.5).convert("L")  # quer: 421 x 298 px
    w, h = img.size
    assert w > h
    top = img.crop((0, 0, w, h // 4)).getextrema()[0]
    bottom = img.crop((0, 3 * h // 4, w, h)).getextrema()[0]
    assert top < 128 and bottom > 200


def test_watermark_stamp_and_image(doc, tmp_path):
    apply_element(doc, [0], watermark("ENTWURF"))
    apply_element(doc, [0], stamp("FREIGABE"))
    Image.new("RGB", (40, 20), "blue").save(tmp_path / "logo.png")
    apply_element(doc, [0], image_element(tmp_path / "logo.png", Anchor.TOP_LEFT, 20))
    assert "ENTWURF" in texts(doc)[0] and "FREIGABE" in texts(doc)[0]
    img = render_page(doc.to_bytes(), 0, scale=1).convert("RGB")
    blues = [p for _, p in img.crop((0, 0, 120, 60)).getcolors(100000) if p[2] > 150 and p[0] < 80]
    assert blues
    # Schrift ist eingebettet (PDF/X-tauglich)
    raw = doc.to_bytes()
    assert b"/FontFile2" in raw


def test_tab_sheets(doc):
    doc.set_sections([Section("Einleitung", 0), Section("Hauptteil", 1), Section("Anhang", 2)])
    inserted = insert_tabs_for_sections(doc, TabSheetStyle(tab_count=5, double_sided=True))
    assert inserted == [0, 1, 3, 4, 6, 7]
    assert doc.page_count == 9
    assert "Einleitung" in texts(doc)[0] and "Hauptteil" in texts(doc)[3]
    width = doc.page_size(0)[0]
    assert width == pytest.approx(595 + 12.7 * MM, abs=0.1)
    # Abschnitte zeigen weiter auf die Inhaltsseiten
    assert [s.page for s in doc.sections()] == [2, 5, 8]


def test_titles_from_text():
    assert titles_from_text("A\n\nB\\nzweite Zeile\n") == ["A", "B\nzweite Zeile"]


def test_bleed_tabs(doc):
    for i in range(3):
        doc.add_bleed(i, 3 * MM)
    doc.set_sections([Section("A", 0), Section("B", 1)])
    apply_bleed_tabs(doc, BleedTabStyle(tab_count=4, colors=[(100, 0, 0, 0), (0, 100, 0, 0)]))
    # Seite 1 (rechts, Slot 0 oben) cyan, Seite 2 (gerade, links, Slot 1) magenta
    img = render_page(doc.to_bytes(), 0, scale=0.5).convert("RGB")
    w, h = img.size
    assert img.getpixel((w - 3, 20))[0] < 100  # cyan: wenig Rot
    img2 = render_page(doc.to_bytes(), 1, scale=0.5).convert("RGB")
    pix = img2.getpixel((3, int(h * 0.25) + 20))
    assert pix[1] < 100  # magenta: wenig Grün


def test_spine(tmp_path):
    assert spine_width_mm(200, Media("x", thickness_um=100)) == pytest.approx(10)
    cover = PdfDocument.open(make_pdf(tmp_path / "c.pdf", pages=1))
    cover.pdf.pages[0].mediabox = [0, 0, 2 * 420 + 28, 595]
    add_spine_text(cover, 0, "Mein Buch", 10)
    assert "Mein Buch" in texts(cover)[0]
    with pytest.raises(ValueError):
        add_spine_text(cover, 0, "x", 5000)


def test_marks_and_barcode(doc):
    add_marks(doc, [0], MarkOptions(color_bar=True, barcode=BarcodeType.QR, fold_x_mm=[105]), {"job": "J9"})
    m = doc.box(0, "MediaBox")
    assert m[0] == pytest.approx(-12 * MM) and doc.box(0, "TrimBox") == (0, 0, 595, 842)
    raw = doc.to_bytes()
    assert b"/All" in raw  # Registerfarbe
    add_marks(doc, [1], MarkOptions(barcode=BarcodeType.CODE128))
    assert remove_marks(doc, [0, 1]) == 2
