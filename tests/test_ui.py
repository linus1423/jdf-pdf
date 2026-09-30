import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")

from jdfpdf.core.jdf import Staple  # noqa: E402
from jdfpdf.core.pdfdoc import PdfDocument  # noqa: E402
from jdfpdf.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(app, tmp_path):
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    w = MainWindow(settings, catalog_path=tmp_path / "media.json")
    yield w
    w.close()


def test_main_window_loads_and_edits(win, sample_pdf):
    win.load(sample_pdf)
    assert win.pages.count() == 3
    win.pages.item(0).setSelected(True)
    win._rotate(90)
    assert win.doc.page_rotation(0) == 90
    win._reorder([2, 1, 0])
    assert round(win.doc.page_size(0)[0]) == 597
    win._set_language("en")
    assert win.output.write_btn.text() == "Write output …"
    assert win.tabs.tabText(1) == "Media"
    assert win.finishing.staple.itemText(1) == "Top left"


def test_ticket_from_panels(win, sample_pdf, tmp_path):
    win.load(sample_pdf)
    win.job.copies.setValue(7)
    win.finishing.staple.set_value(Staple.SADDLE)
    win.media.default.setCurrentIndex(win.media.default.findData("A4 80 g"))
    win.pages.item(1).setSelected(True)
    win.pages.item(2).setSelected(True)
    win.media.range_media.setCurrentIndex(win.media.range_media.findData("A4 gelb 80 g"))
    win.media.assign_to_selection()
    ticket = win.ticket()
    assert ticket.copies == 7
    assert ticket.finishing.staple == Staple.SADDLE
    assert [(r.first, r.last, r.media.name) for r in ticket.media_ranges] == [(1, 2, "A4 gelb 80 g")]
    assert win.pages.item(1).background().color().name() == "#fff4a3"

    win.output.ticketing.setChecked(True)
    from jdfpdf.core.prepress import write_output
    res = write_output(win.doc, ticket, tmp_path / "out.pdf", win.output.options())
    assert len(res.extra_files) == 2
    assert b"RunIndex=\"1 2\"" in PdfDocument.open(tmp_path / "out.pdf").attachment_data("job.jdf")


def test_undo_redo_and_composition(win, sample_pdf, tmp_path):
    win.load(sample_pdf)
    win.pages.item(0).setSelected(True)
    win.duplicate_pages()
    assert win.doc.page_count == 4
    win.insert_blank()
    assert win.doc.page_count == 5
    win.undo()
    win.undo()
    assert win.doc.page_count == 3
    win.redo()
    assert win.doc.page_count == 4
    from conftest import make_pdf
    extra = make_pdf(tmp_path / "Anhang.pdf", pages=2)
    win.insert_files([extra], at=None)
    assert win.doc.page_count == 6
    assert [s.title for s in win.doc.sections()] == ["Anhang"]
    assert win.sections.tree.topLevelItemCount() == 1


def test_failed_modify_restores(win, sample_pdf, monkeypatch):
    win.load(sample_pdf)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *a, **k: None)

    def broken():
        win.doc.delete_pages([0])
        raise RuntimeError("kaputt")

    assert not win.modify(broken)
    assert win.doc.page_count == 3
    assert not win.history.can_undo


def test_project_save_load(win, sample_pdf, tmp_path):
    win.load(sample_pdf)
    win.job.copies.setValue(9)
    win.finishing.staple.set_value(Staple.TOP_LEFT)
    win.output.ticketing.setChecked(True)
    win.project().save(tmp_path / "p.jdfproj")
    win.job.copies.setValue(1)
    win.output.ticketing.setChecked(False)
    win.load(tmp_path / "p.jdfproj")
    assert win.job.copies.value() == 9
    assert win.finishing.staple.value() == Staple.TOP_LEFT
    assert win.output.ticketing.isChecked()
    assert win.doc.page_count == 3


def test_page_view_shows_boxes(win, sample_pdf):
    win.load(sample_pdf)
    win.modify(lambda: win.doc.add_bleed(0, 10))
    win._show_page(0)
    assert win.page_view._page_item is not None
    assert any(item.toolTip() == "BleedBox" for item in win.page_view._box_items)
    guide = win.page_view.add_guide(True, 50)
    assert guide in win.page_view.guides
    win.page_view.clear_guides()
    assert not win.page_view.guides


def test_element_dialogs(win, sample_pdf, monkeypatch):
    from jdfpdf.ui import dialogs_elements as de
    win.load(sample_pdf)
    monkeypatch.setattr(de.ElementDialog, "exec", lambda self: True)
    monkeypatch.setattr(de.MarksDialog, "exec", lambda self: True)
    win.pages.item(0).setSelected(True)
    win.add_element()
    from jdfpdf.core.layers import layer_kinds
    assert layer_kinds(win.doc, 0) == ["element"]
    assert layer_kinds(win.doc, 1) == []
    win.printer_marks()
    assert "marks" in layer_kinds(win.doc, 0)
    win.undo()
    assert "marks" not in layer_kinds(win.doc, 0)
    # Dialog-Voreinstellungen
    d = de.ElementDialog(win.tr_)
    d.preset.setCurrentIndex(d.preset.findData("preset_watermark"))
    assert d.element().rotation == 45 and d.element().text == "ENTWURF"


def test_tab_sheets_assign_media(win, sample_pdf, monkeypatch):
    from jdfpdf.core.pdfdoc import Section
    from jdfpdf.ui import dialogs_elements as de
    win.load(sample_pdf)
    win.modify(lambda: win.doc.set_sections([Section("A", 0), Section("B", 2)]))
    monkeypatch.setattr(de.TabSheetDialog, "exec", lambda self: True)
    win.insert_tab_sheets()
    assert win.doc.page_count == 5
    ranges = win.media.media_ranges()
    assert [(r.first, r.media.name) for r in ranges] == [(0, "A4 Register 5er"), (3, "A4 Register 5er")]
