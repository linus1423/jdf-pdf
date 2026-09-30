"""Hauptfenster: Seitenübersicht, Seitenbearbeitung und Auftragsdaten."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QTabWidget,
)

from ..core.jdf import JobTicket
from ..core.media import MediaCatalog
from ..core.pdfdoc import PdfDocument
from ..core.prepress import process_file, write_output
from ..core.render import render_pages
from .dialogs import MediaCatalogDialog
from .i18n import LANGUAGES, Translator
from .panels import FinishingPanel, JobPanel, MediaPanel, OutputPanel

PAGE_ROLE = Qt.ItemDataRole.UserRole

# Anzeige der JDF-Medienfarben in der Seitenübersicht
MEDIA_COLORS = {
    "white": "#ffffff", "yellow": "#fff4a3", "blue": "#cfe3ff", "green": "#d4f5d0", "pink": "#ffd6e7",
    "red": "#ffc9c2", "orange": "#ffe0b8", "gray": "#e3e3e3", "grey": "#e3e3e3", "ivory": "#fffbe8",
}


class PageList(QListWidget):
    """Seitenminiaturen; Reihenfolge per Drag & Drop änderbar."""

    def __init__(self, on_reordered) -> None:
        super().__init__()
        self._on_reordered = on_reordered
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(160, 160))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Snap)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSpacing(8)
        # Grauer Hintergrund, damit weiße Seiten sichtbar sind.
        self.setStyleSheet("QListWidget { background: #d9d9d9; }")

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        self._on_reordered([self.item(i).data(PAGE_ROLE) for i in range(self.count())])

    def selected_pages(self) -> list[int]:
        return sorted(self.row(item) for item in self.selectedItems())


class _TabLabel:
    """Beschriftung eines Tabs, damit der Translator sie wie ein Label setzen kann."""

    def __init__(self, tabs: QTabWidget, index: int) -> None:
        self.tabs, self.index = tabs, index

    def setText(self, text: str) -> None:
        self.tabs.setTabText(self.index, text)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, catalog_path: Path | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("jdfpdf", "jdfpdf")
        self.tr_ = Translator(str(self.settings.value("language", "de")))
        self.catalog_path = catalog_path
        self.catalog = MediaCatalog.load(catalog_path)
        self.doc: PdfDocument | None = None

        self.pages = PageList(self._reorder)
        self.setCentralWidget(self.pages)
        self._build_panels()
        self._build_actions()
        self.tr_.bind(self, "app_title", "setWindowTitle")
        self.resize(1280, 820)
        self._refresh()

    # --- Aufbau -------------------------------------------------------------

    def _build_panels(self) -> None:
        tr = self.tr_
        self.job = JobPanel(tr)
        self.media = MediaPanel(tr, self.catalog)
        self.media.selection_provider = self.pages.selected_pages
        self.media.changed.connect(self._refresh_media_colors)
        self.media.catalog_requested.connect(self.edit_catalog)
        self.finishing = FinishingPanel(tr)
        self.output = OutputPanel(tr)
        self.output.write_requested.connect(self.write_output)
        self.output.output_intent_requested.connect(self.set_output_intent)

        self.tabs = QTabWidget()
        for panel, key in [(self.job, "tab_job"), (self.media, "tab_media"),
                           (self.finishing, "tab_finishing"), (self.output, "tab_output")]:
            tr.bind(_TabLabel(self.tabs, self.tabs.addTab(panel, "")), key)

        self.dock = QDockWidget()
        self.dock.setObjectName("ticket")
        self.dock.setWidget(self.tabs)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        tr.bind(self.dock, "ticket", "setWindowTitle")
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

    def _build_actions(self) -> None:
        tr = self.tr_

        def action(key: str, slot, shortcut=None) -> QAction:
            act = QAction(self, triggered=slot)
            if shortcut is not None:
                act.setShortcut(shortcut)
            return tr.bind(act, key)

        self.act_open = action("open", self.open_pdf, QKeySequence.StandardKey.Open)
        self.act_save = action("save_as", self.save_as, QKeySequence.StandardKey.SaveAs)
        self.act_insert = action("insert_pdf", self.insert_pdf)
        self.act_batch = action("batch", self.batch)
        self.act_quit = action("quit", self.close, QKeySequence.StandardKey.Quit)
        self.act_rot_l = action("rotate_left", lambda: self._rotate(-90), "Ctrl+L")
        self.act_rot_r = action("rotate_right", lambda: self._rotate(90), "Ctrl+R")
        self.act_delete = action("delete_pages", self._delete, QKeySequence.StandardKey.Delete)
        self.act_catalog = action("edit_catalog", self.edit_catalog)

        menu_file = tr.bind(self.menuBar().addMenu(""), "file", "setTitle")
        menu_file.addActions([self.act_open, self.act_save, self.act_insert])
        menu_file.addSeparator()
        menu_file.addActions([self.act_batch, self.act_catalog])
        menu_file.addSeparator()
        menu_file.addAction(self.act_quit)

        menu_pages = tr.bind(self.menuBar().addMenu(""), "pages", "setTitle")
        menu_pages.addActions([self.act_rot_l, self.act_rot_r, self.act_delete])

        menu_lang = tr.bind(self.menuBar().addMenu(""), "language", "setTitle")
        for code, label in LANGUAGES.items():
            menu_lang.addAction(label, lambda c=code: self._set_language(c))

        toolbar = self.addToolBar("main")
        toolbar.setObjectName("main")
        toolbar.addActions([self.act_open, self.act_save])
        toolbar.addSeparator()
        toolbar.addActions([self.act_rot_l, self.act_rot_r, self.act_delete])

    def _set_language(self, code: str) -> None:
        self.settings.setValue("language", code)
        self.tr_.set_language(code)
        self._refresh_info()

    # --- Anzeige ------------------------------------------------------------

    def _refresh(self) -> None:
        self.pages.clear()
        if self.doc is not None:
            for index, image in enumerate(render_pages(self.doc.to_bytes(), max_size=160)):
                item = QListWidgetItem(QIcon(QPixmap.fromImage(ImageQt(image))), str(index + 1))
                item.setData(PAGE_ROLE, index)
                self.pages.addItem(item)
            self.media.page_count = self.doc.page_count
        has_doc = self.doc is not None
        for act in (self.act_save, self.act_insert, self.act_rot_l, self.act_rot_r, self.act_delete):
            act.setEnabled(has_doc)
        self.output.write_btn.setEnabled(has_doc)
        self._refresh_media_colors()
        self._refresh_info()

    def _refresh_media_colors(self) -> None:
        for index in range(self.pages.count()):
            item = self.pages.item(index)
            media = self.media.media_for_page(index)
            color = MEDIA_COLORS.get((media.color or "").lower()) if media else None
            item.setBackground(QColor(color) if color else QColor(0, 0, 0, 0))
            item.setToolTip(media.label() if media else "")

    def _refresh_info(self) -> None:
        t = self.tr_
        out = self.output
        out.attachments.clear()
        if self.doc is None:
            out.info.setText(t("no_document"))
            out.intent.clear()
            return
        pdfx = self.doc.pdfx_version()
        out.info.setText(t("doc_info", pages=self.doc.page_count, pdfx=pdfx or t("no_pdfx")))
        intent = self.doc.output_intent()
        out.intent.setText(t("output_intent", intent["identifier"] if intent else t("none")))
        for att in self.doc.attachments():
            out.attachments.addItem(f"{att.name} ({att.size} B, {att.relationship or '–'})")

    def _show_warnings(self, warnings: list[str]) -> None:
        texts = []
        for warning in warnings:
            key, _, arg = warning.partition(":")
            texts.append(self.tr_(key, arg))
        self.output.warning.setText("\n".join(texts))

    # --- Aktionen -----------------------------------------------------------

    def open_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("open"), "", self.tr_("pdf_filter"))
        if path:
            self._guard(lambda: self.load(Path(path)))

    def load(self, path: Path) -> None:
        self.doc = PdfDocument.open(path)
        self.job.name.setText(path.stem)
        self.media.clear_ranges()
        self.output.warning.clear()
        self._refresh()

    def insert_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("insert_pdf"), "", self.tr_("pdf_filter"))
        if path and self.doc:
            self._guard(lambda: self.doc.insert_pages_from(PdfDocument.open(path)))
            self._refresh()

    def save_as(self) -> None:
        if self.doc is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("save_as"), str(self.doc.path or ""),
                                              self.tr_("pdf_filter"))
        if path and self._guard(lambda: self.doc.save(path)):
            self.statusBar().showMessage(self.tr_("saved", path), 5000)

    def _rotate(self, degrees: int) -> None:
        if self.doc:
            for index in self.pages.selected_pages():
                self.doc.rotate_page(index, degrees)
            self._refresh()

    def _delete(self) -> None:
        selected = self.pages.selected_pages()
        if self.doc and selected and len(selected) < self.doc.page_count:
            self.doc.delete_pages(selected)
            self._refresh()

    def _reorder(self, order: list[int]) -> None:
        if self.doc:
            self.doc.reorder(order)
            self._refresh()

    def edit_catalog(self) -> None:
        dialog = MediaCatalogDialog(self.catalog, self.tr_, self)
        if dialog.exec():
            self._guard(lambda: self.catalog.save(self.catalog_path))
            self.media.reload_catalog()
            self._refresh_media_colors()

    def set_output_intent(self) -> None:
        if self.doc is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("set_output_intent"), "", self.tr_("icc_filter"))
        if not path:
            return
        identifier, ok = QInputDialog.getText(self, self.tr_("set_output_intent"), self.tr_("identifier"))
        if ok and identifier:
            data = Path(path).read_bytes()
            # Farbraum steht im ICC-Header ab Byte 16
            components = {b"CMYK": 4, b"RGB ": 3, b"GRAY": 1}.get(data[16:20], 4)
            self._guard(lambda: self.doc.set_output_intent(data, identifier, components))
            self._refresh_info()

    def ticket(self) -> JobTicket:
        job = self.job
        return JobTicket(
            job_name=job.name.text().strip(),
            pdf_url="",
            copies=job.copies.value(),
            sides=job.sides.value(),
            color=job.color.value(),
            media=self.media.default_media(),
            media_ranges=self.media.media_ranges(),
            finishing=self.finishing.finishing(),
            customer=job.customer.text().strip() or None,
            comment=job.comment.toPlainText().strip() or None,
        )

    def write_output(self) -> None:
        if self.doc is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("write_output"), str(self.doc.path or ""),
                                              self.tr_("pdf_filter"))
        if not path:
            return
        result = {}

        def run() -> None:
            result["r"] = write_output(self.doc, self.ticket(), Path(path), self.output.options())

        if self._guard(run):
            res = result["r"]
            self._show_warnings(res.warnings)
            files = ", ".join(p.name for p in [res.pdf, *res.extra_files])
            self.statusBar().showMessage(self.tr_("written", files), 8000)
            self._refresh_info()

    def batch(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr_("batch"), "", self.tr_("pdf_filter"))
        if not paths:
            return
        out = QFileDialog.getExistingDirectory(self, self.tr_("choose_output"))
        if not out:
            return
        ok, failed = 0, []
        template = self.ticket()
        template.job_name = ""
        template.media_ranges = []  # Seitenbereiche gelten nur für das offene Dokument
        for p in paths:
            try:
                process_file(Path(p), Path(out), template, self.output.options())
                ok += 1
            except Exception as exc:
                failed.append(f"{Path(p).name}: {exc}")
        message = self.tr_("batch_done", ok=ok, failed=len(failed))
        if failed:
            message += "\n\n" + "\n".join(failed)
        QMessageBox.information(self, self.tr_("batch"), message)

    def _guard(self, func) -> bool:
        try:
            func()
            return True
        except Exception as exc:
            QMessageBox.critical(self, self.tr_("error"), str(exc))
            return False


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("jdfpdf")
    window = MainWindow()
    if len(sys.argv) > 1:
        window.load(Path(sys.argv[1]))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
