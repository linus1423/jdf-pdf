import io

import pikepdf
import pytest
from PIL import Image
from reportlab.lib.colors import CMYKColorSep
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from jdfpdf.core import color, imagefix, spot
from jdfpdf.core.colorspace import to_gray
from jdfpdf.core.jdf import ColorModel, JobTicket, MediaRange
from jdfpdf.core.media import Media
from jdfpdf.core.pagerange import format_pages, parse_pages
from jdfpdf.core.pdfdoc import PdfDocument
from jdfpdf.core.pdffunc import evaluate
from jdfpdf.core.pdfimage import load_image
from jdfpdf.core.prepress import OutputOptions


def colored_pdf() -> bytes:
    """Seite 1: rotes Rechteck, Sonderfarbe, blaues Bild; Seite 2: grau; Seite 3: gleiches Bild."""
    buf = io.BytesIO()
    c = Canvas(buf, pagesize=(200, 200))
    blue = ImageReader(Image.new("RGB", (16, 16), (0, 0, 255)))
    c.setFillColorRGB(1, 0, 0)
    c.rect(10, 10, 80, 80, fill=1)
    c.setFillColor(CMYKColorSep(0, 1, 1, 0, spotName="PANTONE 185 C"))
    c.rect(100, 10, 80, 80, fill=1)
    c.drawImage(blue, 10, 100, 50, 50)
    c.showPage()
    c.setFillColorRGB(0.5, 0.5, 0.5)
    c.rect(10, 10, 80, 80, fill=1)
    c.showPage()
    c.drawImage(blue, 10, 100, 50, 50)
    c.showPage()
    c.save()
    return buf.getvalue()


def images(doc, index):
    return [load_image(obj) for _, obj in imagefix._iter_images(doc.pdf.pages[index].obj.get("/Resources"))]


def test_page_ranges():
    assert parse_pages("1-3, 7; 9-", 10) == [0, 1, 2, 6, 8, 9]
    assert parse_pages("", 5) == []
    assert format_pages([0, 1, 2, 6, 8, 9]) == "1-3, 7, 9-10"
    with pytest.raises(ValueError):
        parse_pages("a-b", 3)


def test_pdf_functions():
    pdf = pikepdf.new()
    type2 = pikepdf.Dictionary(FunctionType=2, Domain=[0, 1], C0=[0, 0, 0, 0], C1=[0, 1, 0.5, 0], N=1)
    assert evaluate(type2, [0.5]) == [0, 0.5, 0.25, 0]
    type4 = pdf.make_stream(b"{dup 0.84 mul exch 0 exch dup 0.5 gt {pop 1} {2 mul} ifelse 0}")
    type4.FunctionType = 4
    type4.Domain = [0, 1]
    type4.Range = [0, 1] * 4
    assert evaluate(type4, [1.0]) == pytest.approx([0.84, 0, 1, 0])
    assert evaluate(type4, [0.25]) == pytest.approx([0.21, 0, 0.5, 0])
    stitched = pikepdf.Dictionary(FunctionType=3, Domain=[0, 1], Bounds=[0.5], Encode=[0, 1, 0, 1],
                                  Functions=[type2, type2])
    assert evaluate(stitched, [0.75]) == pytest.approx([0, 0.5, 0.25, 0])


def test_to_gray_values():
    assert to_gray(pikepdf.Name.DeviceRGB, [1, 1, 1]) == pytest.approx(1)
    assert to_gray(pikepdf.Name.DeviceCMYK, [0, 0, 0, 1]) == 0
    assert to_gray(pikepdf.Name.Pattern, [0]) is None


def test_detect_and_convert_to_gray():
    doc = PdfDocument.from_bytes(colored_pdf())
    assert color.detect_color_pages(doc) == [0, 2]
    report = color.convert_to_gray(doc, [0])
    assert report.pages == 1 and report.images == 1
    doc = PdfDocument.from_bytes(doc.to_bytes())
    assert color.detect_color_pages(doc) == [2]  # geteiltes Bild auf Seite 3 bleibt farbig
    assert images(doc, 0)[0].mode == "L"
    assert images(doc, 2)[0].mode == "RGB"


def test_gray_keeps_layers_removable():
    from jdfpdf.core import elements
    doc = PdfDocument.from_bytes(colored_pdf())
    elements.apply_element(doc, [0], elements.stamp("TEST"))
    color.convert_to_gray(doc, [0])
    assert color.detect_color_pages(doc) == [2]
    assert elements.remove_elements(doc, [0]) == 1


def test_split_and_merge(tmp_path):
    doc = PdfDocument.from_bytes(colored_pdf())
    color_doc, bw_doc, plan = color.split_by_color(doc, [0, 2])
    assert color_doc.page_count == 2 and bw_doc.page_count == 1
    assert plan.summary() == "color:1 bw:1 color:2"
    assert color.SplitPlan.from_json(plan.to_json()).order == plan.order
    merged = color.merge_split(color_doc, bw_doc, plan)
    assert merged.page_count == 3
    assert color.detect_color_pages(merged) == [0, 2]

    _, _, duplex_plan = color.split_by_color(doc, [0], duplex=True)
    assert duplex_plan.summary() == "color:1-2 bw:1"

    ticket = JobTicket("auftrag", "", media_ranges=[MediaRange(1, 2, Media("gelb"))])
    results = color.write_split(doc, ticket, tmp_path / "a.pdf", OutputOptions(), [0])
    names = sorted(p.name for r in results for p in [r.pdf, *r.extra_files])
    assert names == ["a_bw.jdf", "a_bw.pdf", "a_color.jdf", "a_color.pdf", "a_merge.json"]
    bw_jdf = (tmp_path / "a_bw.jdf").read_text()
    assert ColorModel.GRAY.value in bw_jdf and "Merge color:1 bw:1-2" in bw_jdf
    assert "gelb" in bw_jdf  # Medienbereich auf den s/w-Teil übertragen


def test_spot_colors():
    doc = PdfDocument.from_bytes(colored_pdf())
    colors = spot.spot_colors(doc)
    assert [(c.name, c.cmyk) for c in colors] == [("PANTONE 185 C", (0, 100, 100, 0))]
    assert spot.rename_spot(doc, "PANTONE 185 C", "Rot") == 1
    assert spot.set_spot_alternate(doc, "Rot", cmyk=(0, 90, 80, 0)) == 1
    doc = PdfDocument.from_bytes(doc.to_bytes())
    assert [(c.name, c.cmyk) for c in spot.spot_colors(doc)] == [("Rot", (0, 90, 80, 0))]
    spot.set_spot_alternate(doc, "Rot", lab=(50, 60, 40))
    assert spot.spot_colors(doc)[0].alternate == "Lab"
    with pytest.raises(ValueError):
        spot.rename_spot(doc, "Rot", "All")


def test_spot_library(tmp_path):
    lib = spot.SpotLibrary()
    assert lib.get("CutContour") is not None
    doc = PdfDocument.from_bytes(colored_pdf())
    assert lib.import_from(doc) == 1
    lib.add(spot.LibraryColor("PANTONE 185 C", (0, 95, 90, 0)))
    lib.save(tmp_path / "spots.json")
    lib = spot.SpotLibrary.load(tmp_path / "spots.json")
    assert lib.apply(doc) == 1
    assert spot.spot_colors(doc)[0].cmyk == (0, 95, 90, 0)


def test_image_adjust():
    doc = PdfDocument.from_bytes(colored_pdf())
    infos = imagefix.page_images(doc, 0)
    assert len(infos) == 1 and infos[0].editable
    assert imagefix.adjust_images(doc, 0, imagefix.Adjustment(saturation=0)) == 1
    doc = PdfDocument.from_bytes(doc.to_bytes())
    r, g, b = images(doc, 0)[0].getpixel((1, 1))
    assert r == g == b  # entsättigt
    assert images(doc, 2)[0].getpixel((1, 1)) == (0, 0, 255)  # andere Seite unverändert
    cmyk = Image.new("CMYK", (2, 2), (100, 0, 0, 0))
    assert imagefix.adjust(cmyk, imagefix.Adjustment(brightness=1.2)).getpixel((0, 0))[0] < 100
