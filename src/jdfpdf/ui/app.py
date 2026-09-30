"""Hauptfenster: Seitenübersicht, Seitenbearbeitung und JDF-Auftragsdaten."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.jdf import ColorModel, JobTicket, Sides
from ..core.pdfdoc import PdfDocument
from ..core.prepress import JDF_ATTACHMENT_NAME, embed_ticket, process_file
from ..core.render import render_pages
from .i18n import LANGUAGES, Translator

PAGE_ROLE = Qt.ItemDataRole.UserRole


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


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None) -> None:
        super().__init__()
        self.settings = settings or QSettings("jdfpdf", "jdfpdf")
        self.tr_ = Translator(str(self.settings.value("language", "de")))
        self.doc: PdfDocument | None = None

        self.pages = PageList(self._reorder)
        self.setCentralWidget(self.pages)
        self._build_ticket_panel()
        self._build_actions()
        self.resize(1200, 800)
        self.retranslate()
        self._refresh()

    # --- Aufbau -------------------------------------------------------------

    def _build_actions(self) -> None:
        self.act_open = QAction(self, shortcut=QKeySequence.StandardKey.Open, triggered=self.open_pdf)
        self.act_save = QAction(self, shortcut=QKeySequence.StandardKey.SaveAs, triggered=self.save_as)
        self.act_insert = QAction(self, triggered=self.insert_pdf)
        self.act_batch = QAction(self, triggered=self.batch)
        self.act_quit = QAction(self, shortcut=QKeySequence.StandardKey.Quit, triggered=self.close)
        self.act_rot_l = QAction(self, shortcut="Ctrl+L", triggered=lambda: self._rotate(-90))
        self.act_rot_r = QAction(self, shortcut="Ctrl+R", triggered=lambda: self._rotate(90))
        self.act_delete = QAction(self, shortcut=QKeySequence.StandardKey.Delete, triggered=self._delete)

        self.menu_file = self.menuBar().addMenu("")
        self.menu_file.addActions([self.act_open, self.act_save, self.act_insert])
        self.menu_file.addSeparator()
        self.menu_file.addActions([self.act_batch])
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_quit)

        self.menu_pages = self.menuBar().addMenu("")
        self.menu_pages.addActions([self.act_rot_l, self.act_rot_r, self.act_delete])

        self.menu_lang = self.menuBar().addMenu("")
        for code, label in LANGUAGES.items():
            self.menu_lang.addAction(label, lambda c=code: self._set_language(c))

        toolbar = self.addToolBar("main")
        toolbar.setObjectName("main")
        toolbar.addActions([self.act_open, self.act_save])
        toolbar.addSeparator()
        toolbar.addActions([self.act_rot_l, self.act_rot_r, self.act_delete])

    def _build_ticket_panel(self) -> None:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.form = QFormLayout()
        self.f_name = QLineEdit()
        self.f_copies = QSpinBox(minimum=1, maximum=1_000_000)
        self.f_sides = QComboBox()
        self.f_color = QComboBox()
        self.f_weight = QDoubleSpinBox(minimum=0, maximum=1000, decimals=0, specialValueText="–")
        self.f_customer = QLineEdit()
        self.f_comment = QPlainTextEdit()
        self.f_comment.setMaximumHeight(80)
        self.form_labels = {}
        for key, widget in [
            ("job_name", self.f_name),
            ("copies", self.f_copies),
            ("sides", self.f_sides),
            ("color", self.f_color),
            ("weight", self.f_weight),
            ("customer", self.f_customer),
            ("comment", self.f_comment),
        ]:
            label = QLabel()
            self.form_labels[key] = label
            self.form.addRow(label, widget)
        layout.addLayout(self.form)

        self.f_sidecar = QCheckBox(checked=True)
        layout.addWidget(self.f_sidecar)
        self.btn_embed = QPushButton(clicked=self.embed_and_save)
        layout.addWidget(self.btn_embed)

        self.lbl_info = QLabel(wordWrap=True)
        self.lbl_warning = QLabel(wordWrap=True)
        self.lbl_warning.setStyleSheet("color: #b35c00;")
        self.lbl_attachments = QLabel()
        self.list_attachments = QListWidget()
        for widget in (self.lbl_info, self.lbl_warning, self.lbl_attachments, self.list_attachments):
            layout.addWidget(widget)
        layout.addStretch()

        self.dock = QDockWidget()
        self.dock.setObjectName("ticket")
        self.dock.setWidget(panel)
        self.dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)

    def retranslate(self) -> None:
        t = self.tr_
        self.setWindowTitle(t("app_title"))
        for action, key in [
            (self.act_open, "open"),
            (self.act_save, "save_as"),
            (self.act_insert, "insert_pdf"),
            (self.act_batch, "batch"),
            (self.act_quit, "quit"),
            (self.act_rot_l, "rotate_left"),
            (self.act_rot_r, "rotate_right"),
            (self.act_delete, "delete_pages"),
        ]:
            action.setText(t(key))
        self.menu_file.setTitle(t("file"))
        self.menu_pages.setTitle(t("pages"))
        self.menu_lang.setTitle(t("language"))
        self.dock.setWindowTitle(t("ticket"))
        for key, label in self.form_labels.items():
            label.setText(t(key))
        self._fill_combo(self.f_sides, [(s, s.name.lower()) for s in Sides])
        self._fill_combo(self.f_color, [(c, c.name.lower()) for c in ColorModel])
        self.f_sidecar.setText(t("sidecar"))
        self.btn_embed.setText(t("embed_save"))
        self.lbl_attachments.setText(t("attachments"))
        self._refresh_info()

    def _fill_combo(self, combo: QComboBox, items) -> None:
        current = combo.currentIndex()
        combo.clear()
        for value, key in items:
            combo.addItem(self.tr_(key), value)
        combo.setCurrentIndex(max(current, 0))

    def _set_language(self, code: str) -> None:
        self.tr_ = Translator(code)
        self.settings.setValue("language", code)
        self.retranslate()

    # --- Anzeige ------------------------------------------------------------

    def _refresh(self) -> None:
        self.pages.clear()
        if self.doc is not None:
            for index, image in enumerate(render_pages(self.doc.to_bytes(), max_size=160)):
                item = QListWidgetItem(QIcon(QPixmap.fromImage(ImageQt(image))), str(index + 1))
                item.setData(PAGE_ROLE, index)
                self.pages.addItem(item)
        has_doc = self.doc is not None
        for action in (self.act_save, self.act_insert, self.act_rot_l, self.act_rot_r, self.act_delete):
            action.setEnabled(has_doc)
        self.btn_embed.setEnabled(has_doc)
        self._refresh_info()

    def _refresh_info(self) -> None:
        t = self.tr_
        self.list_attachments.clear()
        self.lbl_warning.clear()
        if self.doc is None:
            self.lbl_info.setText(t("no_document"))
            return
        pdfx = self.doc.pdfx_version()
        self.lbl_info.setText(t("doc_info", pages=self.doc.page_count, pdfx=pdfx or t("no_pdfx")))
        for att in self.doc.attachments():
            self.list_attachments.addItem(f"{att.name} ({att.size} B, {att.relationship or '–'})")

    def _show_warnings(self, warnings: list[str]) -> None:
        texts = []
        for warning in warnings:
            key, _, arg = warning.partition(":")
            texts.append(self.tr_(key, arg))
        self.lbl_warning.setText("\n".join(texts))

    # --- Aktionen -----------------------------------------------------------

    def open_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("open"), "", self.tr_("pdf_filter"))
        if path:
            self._guard(lambda: self._load(Path(path)))

    def _load(self, path: Path) -> None:
        self.doc = PdfDocument.open(path)
        self.f_name.setText(path.stem)
        self._refresh()

    def insert_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("insert_pdf"), "", self.tr_("pdf_filter"))
        if path and self.doc:
            self._guard(lambda: self.doc.insert_pages_from(PdfDocument.open(path)))
            self._refresh()

    def save_as(self) -> Path | None:
        if self.doc is None:
            return None
        start = str(self.doc.path or "")
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("save_as"), start, self.tr_("pdf_filter"))
        if not path:
            return None
        self._guard(lambda: self.doc.save(path))
        self.statusBar().showMessage(self.tr_("saved", path), 5000)
        return Path(path)

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

    def _ticket(self) -> JobTicket:
        return JobTicket(
            job_name=self.f_name.text().strip(),
            pdf_url="",
            copies=self.f_copies.value(),
            sides=self.f_sides.currentData(),
            color=self.f_color.currentData(),
            media_weight_gsm=self.f_weight.value() or None,
            customer=self.f_customer.text().strip() or None,
            comment=self.f_comment.toPlainText().strip() or None,
        )

    def embed_and_save(self) -> None:
        if self.doc is None:
            return
        start = str(self.doc.path or "")
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("embed_save"), start, self.tr_("pdf_filter"))
        if not path:
            return
        path = Path(path)

        def run() -> None:
            ticket = replace(self._ticket(), pdf_url=path.name)
            self._show_warnings(embed_ticket(self.doc, ticket))
            self.doc.save(path)
            if self.f_sidecar.isChecked():
                path.with_suffix(".jdf").write_bytes(self.doc.attachment_data(JDF_ATTACHMENT_NAME))
            self.statusBar().showMessage(self.tr_("saved", path), 5000)

        self._guard(run)
        warnings = self.lbl_warning.text()
        self._refresh_info()
        self.lbl_warning.setText(warnings)

    def batch(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr_("batch"), "", self.tr_("pdf_filter"))
        if not paths:
            return
        out = QFileDialog.getExistingDirectory(self, self.tr_("choose_output"))
        if not out:
            return
        ok, failed = 0, []
        template = self._ticket()
        for p in paths:
            try:
                process_file(Path(p), Path(out), replace(template, job_name=""), self.f_sidecar.isChecked())
                ok += 1
            except Exception as exc:
                failed.append(f"{Path(p).name}: {exc}")
        message = self.tr_("batch_done", ok=ok, failed=len(failed))
        if failed:
            message += "\n\n" + "\n".join(failed)
        QMessageBox.information(self, self.tr_("batch"), message)

    def _guard(self, func) -> None:
        try:
            func()
        except Exception as exc:
            QMessageBox.critical(self, self.tr_("error"), str(exc))


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("jdfpdf")
    window = MainWindow()
    if len(sys.argv) > 1:
        window._load(Path(sys.argv[1]))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
