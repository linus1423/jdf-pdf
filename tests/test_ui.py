import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
QtCore = pytest.importorskip("PySide6.QtCore")

from jdfpdf.ui.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_main_window_loads_and_edits(app, sample_pdf, tmp_path):
    settings = QtCore.QSettings(str(tmp_path / "settings.ini"), QtCore.QSettings.Format.IniFormat)
    win = MainWindow(settings)
    win._load(sample_pdf)
    assert win.pages.count() == 3
    win.pages.item(0).setSelected(True)
    win._rotate(90)
    assert win.doc.page_rotation(0) == 90
    win._reorder([2, 1, 0])
    assert round(win.doc.page_size(0)[0]) == 597
    win._set_language("en")
    assert win.btn_embed.text() == "Embed JDF and save …"
    win.close()
