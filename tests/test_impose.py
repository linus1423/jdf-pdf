import pikepdf
import pypdfium2 as pdfium
import pytest

from jdfpdf.core.impose import (
    BackFlip, Imposition, Layout, booklet_order, impose, plan, repeat_pages,
)
from jdfpdf.core.media import MM
from jdfpdf.core.pdfdoc import PdfDocument


def numbered(tmp_path, pages, w=210 * MM, h=297 * MM):
    pdf = pikepdf.new()
    font = pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    for i in range(pages):
        pdf.add_blank_page(page_size=(w, h))
        pg = pdf.pages[-1]
        pg.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
        pg.Contents = pdf.make_stream(f"BT /F1 40 Tf 20 20 Td (P{i + 1}) Tj ET".encode())
    path = tmp_path / f"n{pages}.pdf"
    pdf.save(path)
    return PdfDocument.open(path)


def words(doc, index):
    pdf = pdfium.PdfDocument(doc.to_bytes())
    tp = pdf[index].get_textpage()
    result = []
    for rect in range(tp.count_rects()):
        l, b, r, t = tp.get_rect(rect)
        result.append((tp.get_text_bounded(l, b, r, t).strip(), round(l), round(b)))
    return sorted(result, key=lambda w: (-round(w[2] / 20), w[1]))


def test_booklet_order():
    assert booklet_order(8) == [(7, 0, 1, 6), (5, 2, 3, 4)]
    assert booklet_order(6)[0] == (None, 0, 1, None)


def test_booklet(tmp_path):
    doc = numbered(tmp_path, 8, 148 * MM, 210 * MM)
    out = impose(doc, Imposition(layout=Layout.BOOKLET, auto_sheet=True, creep_mm=0))
    assert out.page_count == 4
    assert out.box(0, "MediaBox")[2] == pytest.approx(296 * MM)
    front = words(out, 0)
    assert [w[0] for w in front] == ["P8", "P1"]
    assert front[1][1] > front[0][1]  # P1 rechts
    assert [w[0] for w in words(out, 1)] == ["P2", "P7"]
    assert doc.page_count == 8  # Quelle unverändert


def test_booklet_creep(tmp_path):
    doc = numbered(tmp_path, 8, 148 * MM, 210 * MM)
    no_creep = impose(doc, Imposition(layout=Layout.BOOKLET, auto_sheet=True, creep_mm=0))
    creep = impose(doc, Imposition(layout=Layout.BOOKLET, auto_sheet=True, creep_mm=4))
    # innerer Bogen (Seite 3 des Ergebnisses): linke Seite P6 Richtung Bund verschoben
    a = dict((w[0], w[1]) for w in words(no_creep, 2))
    b = dict((w[0], w[1]) for w in words(creep, 2))
    assert b["P6"] - a["P6"] == pytest.approx(2 * MM, abs=1.5)
    assert a["P3"] - b["P3"] == pytest.approx(2 * MM, abs=1.5)


def test_nup_duplex_backside_alignment(tmp_path):
    doc = numbered(tmp_path, 8, 100 * MM, 100 * MM)
    imp = Imposition(layout=Layout.NUP, cols=2, rows=2, sheet_width_mm=200, sheet_height_mm=200)
    sides = plan(doc, imp)
    assert len(sides) == 2
    front = {p.page: (p.x, p.y) for p in sides[0].placements}
    back = {p.page: (p.x, p.y) for p in sides[1].placements}
    assert sorted(front) == [0, 2, 4, 6] and sorted(back) == [1, 3, 5, 7]
    # quadratisch = hochformatig behandelt, lange Kante → Buchwendung: x gespiegelt
    assert back[1] == pytest.approx((200 * MM - front[0][0] - 100 * MM, front[0][1]))


def test_nup_too_big(tmp_path):
    doc = numbered(tmp_path, 2)
    with pytest.raises(ValueError):
        impose(doc, Imposition(layout=Layout.NUP, cols=3, rows=3, sheet_width_mm=320, sheet_height_mm=450))


def test_step_and_repeat_and_rotation(tmp_path):
    doc = numbered(tmp_path, 2, 85 * MM, 55 * MM)  # Visitenkarten
    imp = Imposition(layout=Layout.REPEAT, cols=2, rows=5, rotate=False, sheet_width_mm=210, sheet_height_mm=297,
                     gap_mm=4, bleed_mm=2, crop_marks=True)
    out = impose(doc, imp)
    assert out.page_count == 2
    assert [w[0] for w in words(out, 0)].count("P1") == 10
    assert [w[0] for w in words(out, 1)].count("P2") == 10
    rotated = impose(doc, Imposition(layout=Layout.REPEAT, cols=5, rows=2, rotate=True, sheet_width_mm=297,
                                     sheet_height_mm=210, duplex=False))
    assert rotated.page_count == 2


def test_cut_and_stack(tmp_path):
    doc = numbered(tmp_path, 8, 100 * MM, 100 * MM)
    sides = plan(doc, Imposition(layout=Layout.CUT_STACK, cols=2, rows=1, sheet_width_mm=200,
                                 sheet_height_mm=100, duplex=False))
    # 4 Bögen, Stapel links 1–4, rechts 5–8
    assert [[p.page for p in s.placements] for s in sides] == [[0, 4], [1, 5], [2, 6], [3, 7]]


def test_multi_booklet(tmp_path):
    doc = numbered(tmp_path, 16, 148 * MM, 210 * MM)
    out = impose(doc, Imposition(layout=Layout.MULTI_BOOKLET, auto_sheet=True, sheets_per_booklet=2, creep_mm=0))
    assert out.page_count == 8
    assert [w[0] for w in words(out, 0)] == ["P8", "P1"]
    assert [w[0] for w in words(out, 4)] == ["P16", "P9"]


def test_perfect_bound_cover_and_glue(tmp_path):
    doc = numbered(tmp_path, 6)
    out = impose(doc, Imposition(layout=Layout.PERFECT_BOUND, spine_mm=10, cover_front=0, cover_back=5,
                                 glue_zone_mm=5))
    assert out.page_count == 5
    assert out.box(0, "MediaBox")[2] == pytest.approx(430 * MM)
    cover = words(out, 0)
    assert {w[0] for w in cover} == {"P1", "P6"}
    assert b"re f" in out.pdf.pages[1].Contents.read_bytes()


def test_back_flip_short_edge(tmp_path):
    doc = numbered(tmp_path, 4, 100 * MM, 100 * MM)
    sides = plan(doc, Imposition(layout=Layout.NUP, cols=1, rows=2, sheet_width_mm=100, sheet_height_mm=200,
                                 back_flip=BackFlip.SHORT_EDGE))
    assert sides[1].placements[0].rotation == 180


def test_repeat_pages(tmp_path):
    doc = numbered(tmp_path, 2)
    repeat_pages(doc, 3)
    assert doc.page_count == 6


def test_write_output_with_imposition(tmp_path):
    from jdfpdf.core.jdf import JobTicket, MediaRange
    from jdfpdf.core.media import Media
    from jdfpdf.core.prepress import OutputOptions, write_output
    from jdfpdf.core.project import Project

    doc = numbered(tmp_path, 8, 148 * MM, 210 * MM)
    imp = Imposition(layout=Layout.BOOKLET, auto_sheet=True)
    res = write_output(doc, JobTicket("b", "", media_ranges=[MediaRange(0, 0, Media("x"))]), tmp_path / "o.pdf",
                       OutputOptions(), imp)
    assert res.warnings == ["media_ranges_dropped:"]
    out = PdfDocument.open(tmp_path / "o.pdf")
    assert out.page_count == 4
    assert b'Dimension="839.06 595.28"' in out.attachment_data("job.jdf")
    Project(doc, JobTicket("b", ""), imposition=imp).save(tmp_path / "p.jdfproj")
    assert Project.load(tmp_path / "p.jdfproj").imposition.layout == Layout.BOOKLET
