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
