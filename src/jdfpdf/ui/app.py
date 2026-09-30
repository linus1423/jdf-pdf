"""Hauptfenster: Seitenübersicht, Seitenansicht, Abschnitte und Auftragsdaten."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import QItemSelectionModel, QSettings, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDockWidget,
    QFileDialog,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
)

from ..core import elements, geometry, marks, spine, tabs
from ..core.history import History
from ..core.jdf import JobTicket
from ..core.media import MM, MediaCatalog
from ..core.pdfdoc import BOXES, PdfDocument, Section
from ..core.prepress import process_file, write_output
from ..core.project import SUFFIX, Project
from ..core.render import render_page_media, render_pages
from .dialogs import BleedDialog, BoxesDialog, MediaCatalogDialog, ScaleDialog, ShiftDialog
from .dialogs_elements import BleedTabDialog, ElementDialog, MarksDialog, SpineDialog, TabSheetDialog
from .i18n import LANGUAGES, Translator
from .pageview import PageView
from .panels import FinishingPanel, JobPanel, MediaPanel, OutputPanel, SectionsPanel

PAGE_ROLE = Qt.ItemDataRole.UserRole
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Anzeige der JDF-Medienfarben in der Seitenübersicht
MEDIA_COLORS = {
    "white": "#ffffff", "yellow": "#fff4a3", "blue": "#cfe3ff", "green": "#d4f5d0", "pink": "#ffd6e7",
    "red": "#ffc9c2", "orange": "#ffe0b8", "gray": "#e3e3e3", "grey": "#e3e3e3", "ivory": "#fffbe8",
}


class PageList(QListWidget):
    """Seitenminiaturen; Reihenfolge per Drag & Drop änderbar, Dateien können hineingezogen werden."""

    def __init__(self, on_reordered, on_files_dropped) -> None:
        super().__init__()
        self._on_reordered = on_reordered
        self._on_files_dropped = on_files_dropped
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(140, 140))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Snap)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setSpacing(8)
        # Grauer Hintergrund, damit weiße Seiten sichtbar sind.
        self.setStyleSheet("QListWidget { background: #d9d9d9; }")

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            target = self.indexAt(event.position().toPoint()).row()
            paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
            event.acceptProposedAction()
            self._on_files_dropped(paths, None if target < 0 else target)
            return
        super().dropEvent(event)
        self._on_reordered([self.item(i).data(PAGE_ROLE) for i in range(self.count())])

    def selected_pages(self) -> list[int]:
        return sorted(self.row(item) for item in self.selectedItems())

    def select_range(self, first: int, last: int) -> None:
        self.clearSelection()
        for index in range(first, last + 1):
            self.item(index).setSelected(True)
        self.setCurrentRow(first)
        self.scrollToItem(self.item(first))


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
        self.history: History[bytes] = History()

        self.pages = PageList(self._reorder, self.insert_files)
        self.pages.currentRowChanged.connect(self._show_page)
        self.page_view = PageView()
        self.page_view.hovered.connect(lambda x, y: self.pos_label.setText(self.tr_("position", x, y)))
        self.page_view.measured.connect(lambda dx, dy, l: self.statusBar().showMessage(self.tr_("measured", dx, dy, l)))
        splitter = QSplitter()
        splitter.addWidget(self.pages)
        splitter.addWidget(self.page_view)
        splitter.setSizes([520, 480])
        self.setCentralWidget(splitter)
        self.pos_label = QLabel()
        self.statusBar().addPermanentWidget(self.pos_label)

        self._build_panels()
        self._build_actions()
        self.tr_.bind(self, "app_title", "setWindowTitle")
        self.resize(1400, 860)
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

        self.sections = SectionsPanel(tr)
        self.sections.section_clicked.connect(self.pages.select_range)
        self.sections.new_requested.connect(self.new_section)
        self.sections.rename_requested.connect(self.rename_section)
        self.sections.delete_requested.connect(self.delete_section)
        self.sections.bookmarks_requested.connect(self.sections_from_bookmarks)
        self.sections_dock = QDockWidget()
        self.sections_dock.setObjectName("sections")
        self.sections_dock.setWidget(self.sections)
        tr.bind(self.sections_dock, "sections", "setWindowTitle")
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.sections_dock)
        self.resizeDocks([self.sections_dock, self.dock], [220, 420], Qt.Orientation.Horizontal)

    def _build_actions(self) -> None:
        tr = self.tr_

        def action(key: str, slot, shortcut=None, checkable: bool = False) -> QAction:
            act = QAction(self, triggered=slot, checkable=checkable)
            if shortcut is not None:
                act.setShortcut(shortcut)
            return tr.bind(act, key)

        self.act_open = action("open", self.open_pdf, QKeySequence.StandardKey.Open)
        self.act_save = action("save_as", self.save_as, QKeySequence.StandardKey.SaveAs)
        self.act_open_project = action("open_project", self.open_project)
        self.act_save_project = action("save_project", self.save_project, QKeySequence.StandardKey.Save)
        self.act_insert = action("insert_pdf", self.insert_pdf)
        self.act_images = action("insert_images", self.insert_images)
        self.act_batch = action("batch", self.batch)
        self.act_catalog = action("edit_catalog", self.edit_catalog)
        self.act_quit = action("quit", self.close, QKeySequence.StandardKey.Quit)

        self.act_undo = action("undo", self.undo, QKeySequence.StandardKey.Undo)
        self.act_redo = action("redo", self.redo, QKeySequence.StandardKey.Redo)

        self.act_rot_l = action("rotate_left", lambda: self._rotate(-90), "Ctrl+L")
        self.act_rot_r = action("rotate_right", lambda: self._rotate(90), "Ctrl+R")
        self.act_delete = action("delete_pages", self._delete, QKeySequence.StandardKey.Delete)
        self.act_duplicate = action("duplicate_pages", self.duplicate_pages, "Ctrl+D")
        self.act_blank = action("insert_blank", self.insert_blank)
        self.act_replace = action("replace_page", self.replace_page)
        self.act_scale = action("scale_pages", self.scale_pages)
        self.act_shift = action("shift_content", self.shift_content)
        self.act_boxes = action("edit_boxes", self.edit_boxes)
        self.act_bleed = action("add_bleed", self.add_bleed)

        self.act_element = action("add_element", self.add_element, "Ctrl+T")
        self.act_remove_elements = action("remove_elements", self.remove_elements)
        self.act_tab_sheets = action("insert_tab_sheets", self.insert_tab_sheets)
        self.act_bleed_tabs = action("bleed_tabs", self.bleed_tabs)
        self.act_remove_tabs = action("remove_tabs", self.remove_tabs)
        self.act_spine = action("spine_text", self.spine_text)
        self.act_marks = action("printer_marks", self.printer_marks)
        self.act_remove_marks = action("remove_marks", self.remove_marks)

        self.act_zoom_in = action("zoom_in", lambda: self.page_view.zoom(1.25), QKeySequence.StandardKey.ZoomIn)
        self.act_zoom_out = action("zoom_out", lambda: self.page_view.zoom(0.8), QKeySequence.StandardKey.ZoomOut)
        self.act_zoom_fit = action("zoom_fit", self.page_view.fit, "Ctrl+0")
        self.act_measure = action("measure", self.page_view.set_measure_mode, "M", checkable=True)
        self.act_show_boxes = action("show_boxes", self._toggle_boxes, checkable=True)
        self.act_show_boxes.setChecked(True)
        self.act_clear_guides = action("clear_guides", self.page_view.clear_guides)

        menu_file = tr.bind(self.menuBar().addMenu(""), "file", "setTitle")
        menu_file.addActions([self.act_open, self.act_insert, self.act_images, self.act_save])
        menu_file.addSeparator()
        menu_file.addActions([self.act_open_project, self.act_save_project])
        menu_file.addSeparator()
        menu_file.addActions([self.act_batch, self.act_catalog])
        menu_file.addSeparator()
        menu_file.addAction(self.act_quit)

        menu_edit = tr.bind(self.menuBar().addMenu(""), "edit", "setTitle")
        menu_edit.addActions([self.act_undo, self.act_redo])

        menu_pages = tr.bind(self.menuBar().addMenu(""), "pages", "setTitle")
        menu_pages.addActions([self.act_rot_l, self.act_rot_r, self.act_delete, self.act_duplicate,
                               self.act_blank, self.act_replace])
        menu_pages.addSeparator()
        menu_pages.addActions([self.act_scale, self.act_shift, self.act_boxes, self.act_bleed])

        menu_elements = tr.bind(self.menuBar().addMenu(""), "elements", "setTitle")
        menu_elements.addActions([self.act_element, self.act_remove_elements])
        menu_elements.addSeparator()
        menu_elements.addActions([self.act_tab_sheets, self.act_bleed_tabs, self.act_remove_tabs])
        menu_elements.addSeparator()
        menu_elements.addActions([self.act_spine, self.act_marks, self.act_remove_marks])

        menu_view = tr.bind(self.menuBar().addMenu(""), "view", "setTitle")
        menu_view.addActions([self.act_zoom_in, self.act_zoom_out, self.act_zoom_fit])
        menu_view.addSeparator()
        menu_view.addActions([self.act_measure, self.act_show_boxes, self.act_clear_guides])
        menu_view.addSeparator()
        menu_view.addActions([self.dock.toggleViewAction(), self.sections_dock.toggleViewAction()])

        menu_lang = tr.bind(self.menuBar().addMenu(""), "language", "setTitle")
        for code, label in LANGUAGES.items():
            menu_lang.addAction(label, lambda c=code: self._set_language(c))

        toolbar = self.addToolBar("main")
        toolbar.setObjectName("main")
        toolbar.addActions([self.act_open, self.act_save_project])
        toolbar.addSeparator()
        toolbar.addActions([self.act_undo, self.act_redo])
        toolbar.addSeparator()
        toolbar.addActions([self.act_rot_l, self.act_rot_r, self.act_delete, self.act_duplicate])
        toolbar.addSeparator()
        toolbar.addActions([self.act_zoom_fit, self.act_measure])

        self._doc_actions = [
            self.act_save, self.act_save_project, self.act_insert, self.act_images, self.act_rot_l,
            self.act_rot_r, self.act_delete, self.act_duplicate, self.act_blank, self.act_replace,
            self.act_scale, self.act_shift, self.act_boxes, self.act_bleed, self.act_element,
            self.act_remove_elements, self.act_tab_sheets, self.act_bleed_tabs, self.act_remove_tabs,
            self.act_spine, self.act_marks, self.act_remove_marks,
        ]

    def _set_language(self, code: str) -> None:
        self.settings.setValue("language", code)
        self.tr_.set_language(code)
        self._refresh_info()

    # --- Bearbeiten mit Rückgängig ------------------------------------------

    def modify(self, func) -> bool:
        """Änderung am Dokument ausführen; bei Erfolg rückgängig machbar, bei Fehler zurückgesetzt."""
        if self.doc is None:
            return False
        snapshot = self.doc.to_bytes()
        try:
            func()
        except Exception as exc:
            self._restore(snapshot)
            QMessageBox.critical(self, self.tr_("error"), str(exc))
            return False
        self.history.push(snapshot)
        self._refresh()
        return True

    def _restore(self, data: bytes) -> None:
        path = self.doc.path if self.doc else None
        self.doc = PdfDocument.from_bytes(data)
        self.doc.path = path

    def undo(self) -> None:
        if self.doc is None:
            return
        state = self.history.undo(self.doc.to_bytes())
        if state is not None:
            self._restore(state)
            self._refresh()

    def redo(self) -> None:
        if self.doc is None:
            return
        state = self.history.redo(self.doc.to_bytes())
        if state is not None:
            self._restore(state)
            self._refresh()

    # --- Anzeige ------------------------------------------------------------

    def _refresh(self) -> None:
        current = max(self.pages.currentRow(), 0)
        self.pages.blockSignals(True)
        self.pages.clear()
        if self.doc is not None:
            for index, image in enumerate(render_pages(self.doc.to_bytes(), max_size=140)):
                item = QListWidgetItem(QIcon(QPixmap.fromImage(ImageQt(image))), str(index + 1))
                item.setData(PAGE_ROLE, index)
                self.pages.addItem(item)
            self.media.page_count = self.doc.page_count
            self.sections.set_sections(self.doc.sections(), self.doc.page_count)
        else:
            self.sections.set_sections([], 0)
        self.pages.blockSignals(False)
        has_doc = self.doc is not None
        for act in self._doc_actions:
            act.setEnabled(has_doc)
        self.act_undo.setEnabled(self.history.can_undo)
        self.act_redo.setEnabled(self.history.can_redo)
        self.output.write_btn.setEnabled(has_doc)
        self._refresh_media_colors()
        self._refresh_info()
        if has_doc and self.doc.page_count:
            self.pages.setCurrentRow(min(current, self.doc.page_count - 1),
                                     QItemSelectionModel.SelectionFlag.NoUpdate)
            self._show_page(self.pages.currentRow())

    def _show_page(self, index: int) -> None:
        if self.doc is None or not 0 <= index < self.doc.page_count:
            return
        first_show = self.page_view._page_item is None
        image = render_page_media(self.doc.to_bytes(), index, scale=2.0)
        boxes = {}
        if self.doc.page_rotation(index) == 0:  # Box-Anzeige nur für ungedrehte Seiten
            boxes = {name: self.doc.box(index, name) for name in BOXES if self.doc.has_box(index, name)}
            boxes.setdefault("TrimBox", self.doc.box(index, "TrimBox"))
        media_box = self.doc.box(index, "MediaBox")
        if self.doc.page_rotation(index) % 180:
            x0, y0, x1, y1 = media_box
            media_box = (0, 0, y1 - y0, x1 - x0)
        self.page_view.set_page(QPixmap.fromImage(ImageQt(image)), media_box, boxes)
        if first_show:
            self.page_view.fit()

    def _toggle_boxes(self, checked: bool) -> None:
        self.page_view.show_boxes = checked
        self._show_page(self.pages.currentRow())

    def _refresh_media_colors(self) -> None:
        for index in range(self.pages.count()):
            item = self.pages.item(index)
            media = self.media.media_for_page(index)
            color = MEDIA_COLORS.get((media.color or "").lower()) if media else None
            item.setBackground(QColor(color) if color else QColor(0, 0, 0, 0))
            tip = media.label() if media else ""
            if self.doc is not None:
                section = self.doc.section_of_page(index)
                if section:
                    tip = f"{section.title}\n{tip}".strip()
            item.setToolTip(tip)

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

    def _scope(self, scope: str) -> list[int]:
        if scope == "all" or not self.pages.selected_pages():
            return list(range(self.doc.page_count))
        return self.pages.selected_pages()

    # --- Dateien ------------------------------------------------------------

    def open_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("open"), "", self.tr_("pdf_filter"))
        if path:
            self._guard(lambda: self.load(Path(path)))

    def load(self, path: Path) -> None:
        path = Path(path)
        if path.suffix.lower() == SUFFIX:
            self.load_project(path)
            return
        if path.suffix.lower() in IMAGE_SUFFIXES:
            from ..core.images import images_to_pdf

            self.doc = PdfDocument.from_bytes(images_to_pdf([path]))
            self.doc.path = path.with_suffix(".pdf")
        else:
            self.doc = PdfDocument.open(path)
        self.history.clear()
        self.job.name.setText(path.stem)
        self.media.clear_ranges()
        self.output.warning.clear()
        self._refresh()

    def insert_files(self, paths: list[Path], at: int | None = None) -> None:
        """PDFs und Bilder einfügen (Drag & Drop); jede PDF wird ein eigener Abschnitt."""
        if self.doc is None:
            if not paths:
                return
            self._guard(lambda: self.load(paths[0]))
            paths = paths[1:]
            if not paths:
                return

        def run() -> None:
            position = self.doc.page_count if at is None else at
            for path in paths:
                suffix = path.suffix.lower()
                if suffix == ".pdf":
                    other = PdfDocument.open(path)
                    self.doc.insert_pages_from(other, position, section=path.stem)
                    position += other.page_count
                elif suffix in IMAGE_SUFFIXES:
                    position += self.doc.insert_images([path], position)
                else:
                    raise ValueError(self.tr_("unsupported_file", path.name))

        self.modify(run)

    def insert_pdf(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr_("insert_pdf"), "", self.tr_("pdf_filter"))
        if paths:
            self.insert_files([Path(p) for p in paths], self._insert_position())

    def insert_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr_("insert_images"), "", self.tr_("image_filter"))
        if paths:
            self.insert_files([Path(p) for p in paths], self._insert_position())

    def _insert_position(self) -> int | None:
        selected = self.pages.selected_pages()
        return selected[-1] + 1 if selected else None

    def save_as(self) -> None:
        if self.doc is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("save_as"), str(self.doc.path or ""),
                                              self.tr_("pdf_filter"))
        if path and self._guard(lambda: self.doc.save(path)):
            self.statusBar().showMessage(self.tr_("saved", path), 5000)

    def project(self) -> Project:
        return Project(self.doc, self.ticket(), self.output.options())

    def save_project(self) -> None:
        if self.doc is None:
            return
        start = str((self.doc.path or Path("projekt")).with_suffix(SUFFIX))
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("save_project"), start, self.tr_("project_filter"))
        if path and self._guard(lambda: self.project().save(path)):
            self.statusBar().showMessage(self.tr_("saved", path), 5000)

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("open_project"), "", self.tr_("project_filter"))
        if path:
            self._guard(lambda: self.load_project(Path(path)))

    def load_project(self, path: Path) -> None:
        project = Project.load(path)
        self.doc = project.document
        self.doc.path = path.with_suffix(".pdf")
        self.history.clear()
        self.apply_ticket(project.ticket)
        out = self.output
        out.embed.setChecked(project.output.embed)
        out.sidecar.setChecked(project.output.sidecar)
        out.ticketing.setChecked(project.output.ticketing)
        out.pdfx_anyway.setChecked(project.output.pdfx_policy.name == "EMBED_ANYWAY")
        self._refresh()

    def apply_ticket(self, ticket: JobTicket) -> None:
        job = self.job
        job.name.setText(ticket.job_name)
        job.copies.setValue(ticket.copies)
        job.sides.set_value(ticket.sides)
        job.color.set_value(ticket.color)
        job.customer.setText(ticket.customer or "")
        job.comment.setPlainText(ticket.comment or "")
        for media in [ticket.media, *(r.media for r in ticket.media_ranges)]:
            if media is not None and media.name != "default" and self.catalog.get(media.name) is None:
                self.catalog.add(media)
        self.media.reload_catalog()
        name = ticket.media.name if ticket.media and ticket.media.name != "default" else None
        self.media.default.setCurrentIndex(max(self.media.default.findData(name), 0))
        self.media.clear_ranges()
        for rng in ticket.media_ranges:
            self.media.add_range(rng.first, rng.last, rng.media.name)
        fin = self.finishing
        fin.staple.set_value(ticket.finishing.staple)
        fin.punch.set_value(ticket.finishing.punch)
        fin.fold.set_value(ticket.finishing.fold)
        fin.trim.setChecked(ticket.finishing.trim)

    # --- Seiten -------------------------------------------------------------

    def _rotate(self, degrees: int) -> None:
        selected = self.pages.selected_pages()
        if selected:
            self.modify(lambda: [self.doc.rotate_page(i, degrees) for i in selected])

    def _delete(self) -> None:
        selected = self.pages.selected_pages()
        if self.doc and selected and len(selected) < self.doc.page_count:
            self.modify(lambda: self.doc.delete_pages(selected))

    def _reorder(self, order: list[int]) -> None:
        self.modify(lambda: self.doc.reorder(order))

    def duplicate_pages(self) -> None:
        selected = self.pages.selected_pages()
        if selected:
            self.modify(lambda: self.doc.duplicate_pages(selected))

    def insert_blank(self) -> None:
        at = self._insert_position()
        self.modify(lambda: self.doc.insert_blank(self.doc.page_count if at is None else at))

    def replace_page(self) -> None:
        selected = self.pages.selected_pages()
        if not selected:
            return
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("replace_page"), "", self.tr_("pdf_filter"))
        if not path:
            return

        def run() -> None:
            other = PdfDocument.open(path)
            for offset, index in enumerate(selected):
                self.doc.replace_page(index, other, min(offset, other.page_count - 1))

        self.modify(run)

    def scale_pages(self) -> None:
        if self.doc is None:
            return
        index = max(self.pages.currentRow(), 0)
        width, height = self.doc.page_size(index)
        dialog = ScaleDialog(self.tr_, width / MM, height / MM, self)
        if dialog.exec():
            pages = self._scope(dialog.scope.currentData())
            w, h, mode = dialog.width.value() * MM, dialog.height.value() * MM, dialog.mode.value()
            self.modify(lambda: [geometry.scale_page(self.doc, i, w, h, mode) for i in pages])

    def shift_content(self) -> None:
        dialog = ShiftDialog(self.tr_, self)
        if dialog.exec():
            pages = self._scope(dialog.scope.currentData())
            dx, dy = dialog.dx.value() * MM, dialog.dy.value() * MM
            mirror = dialog.mirror.isChecked()

            def run() -> None:
                for i in pages:
                    sign = -1 if (mirror and i % 2 == 1) else 1
                    geometry.shift_content(self.doc, i, sign * dx, dy)

            self.modify(run)

    def edit_boxes(self) -> None:
        if self.doc is None:
            return
        index = max(self.pages.currentRow(), 0)
        current = {name: self.doc.box(index, name) if self.doc.has_box(index, name) else None for name in BOXES}
        current["MediaBox"] = self.doc.box(index, "MediaBox")
        dialog = BoxesDialog(self.tr_, current, self)
        if dialog.exec():
            pages = self._scope(dialog.scope.currentData())
            boxes = dialog.boxes()
            self.modify(lambda: [self.doc.set_box(i, name, rect) for i in pages for name, rect in boxes.items()])

    def add_bleed(self) -> None:
        dialog = BleedDialog(self.tr_, self)
        if dialog.exec():
            pages = self._scope(dialog.scope.currentData())
            bleed = dialog.bleed.value() * MM
            self.modify(lambda: [self.doc.add_bleed(i, bleed) for i in pages])

    # --- Abschnitte ---------------------------------------------------------

    def new_section(self) -> None:
        selected = self.pages.selected_pages()
        if self.doc is None or not selected:
            return
        name, ok = QInputDialog.getText(self, self.tr_("new_section"), self.tr_("section_name"))
        if ok and name:
            sections = [s for s in self.doc.sections() if s.page != selected[0]] + [Section(name, selected[0])]
            self.modify(lambda: self.doc.set_sections(sections))

    def rename_section(self, row: int) -> None:
        sections = self.doc.sections()
        name, ok = QInputDialog.getText(self, self.tr_("rename"), self.tr_("section_name"),
                                        text=sections[row].title)
        if ok and name:
            sections[row] = Section(name, sections[row].page)
            self.modify(lambda: self.doc.set_sections(sections))

    def delete_section(self, row: int) -> None:
        sections = self.doc.sections()
        del sections[row]
        self.modify(lambda: self.doc.set_sections(sections))

    def sections_from_bookmarks(self) -> None:
        if self.doc is None:
            return
        depth, ok = QInputDialog.getInt(self, self.tr_("from_bookmarks"), self.tr_("bookmark_depth"), 1, 1, 9)
        if ok:
            self.modify(lambda: self.doc.sections_from_bookmarks(depth))

    # --- Elemente, Register, Rückentitel, Marken ----------------------------

    def _context(self) -> elements.Context:
        file = self.doc.path.name if self.doc and self.doc.path else ""
        return elements.Context(job=self.job.name.text().strip(), file=file)

    def add_element(self) -> None:
        dialog = ElementDialog(self.tr_, self)
        if dialog.exec():
            pages = self._scope(dialog.scope.currentData())
            element = dialog.element()
            self.modify(lambda: elements.apply_element(self.doc, pages, element, self._context()))

    def remove_elements(self) -> None:
        pages = self._scope("selection")
        self.modify(lambda: elements.remove_elements(self.doc, pages))

    def _require_sections(self) -> bool:
        if self.doc is not None and self.doc.sections():
            return True
        QMessageBox.information(self, self.tr_("sections"), self.tr_("no_sections"))
        return False

    def insert_tab_sheets(self) -> None:
        if not self._require_sections():
            return
        dialog = TabSheetDialog(self.tr_, [m.name for m in self.catalog.media], self)
        if not dialog.exec():
            return
        titles_path = dialog.titles.path()
        titles = tabs.titles_from_text(Path(titles_path).read_text(encoding="utf-8")) if titles_path else None
        result = {}

        def run() -> None:
            result["pages"] = tabs.insert_tabs_for_sections(self.doc, dialog.style(), titles)

        if self.modify(run) and dialog.media.currentData():
            # Medienbereiche hinter den Registerblättern verschieben sich; Tabs neu zuweisen
            for index in result["pages"]:
                self.media.add_range(index, index, dialog.media.currentData())

    def bleed_tabs(self) -> None:
        if not self._require_sections():
            return
        dialog = BleedTabDialog(self.tr_, self)
        if dialog.exec():
            style = dialog.style()
            self.modify(lambda: tabs.apply_bleed_tabs(self.doc, style))

    def remove_tabs(self) -> None:
        self.modify(lambda: tabs.remove_tabs(self.doc))

    def spine_text(self) -> None:
        if self.doc is None:
            return
        media = self.media.default_media()
        from ..core.media import Media

        width = spine.spine_width_mm(self.doc.page_count, media or Media("default"),
                                     self.job.sides.value().name != "SIMPLEX")
        dialog = SpineDialog(self.tr_, max(width, 1.0), self)
        if dialog.exec():
            index = max(self.pages.currentRow(), 0)
            kwargs = dict(font_size=dialog.size.value() or None, top_to_bottom=dialog.direction.currentData(),
                          color_cmyk=dialog.color.value(),
                          background_cmyk=dialog.bg_color.value() if dialog.background.isChecked() else None)
            text, width_mm = dialog.text.text(), dialog.width.value()
            self.modify(lambda: spine.add_spine_text(self.doc, index, text, width_mm, **kwargs))

    def printer_marks(self) -> None:
        dialog = MarksDialog(self.tr_, self)
        if dialog.exec():
            pages = self._scope(dialog.scope.currentData())
            opts = dialog.options()
            fin = self.finishing.finishing()
            ctx = {"job": self.job.name.text().strip(), "file": self._context().file,
                   "finishing": "-".join(v.value for v in (fin.staple, fin.punch, fin.fold) if v.value != "none")}
            self.modify(lambda: marks.add_marks(self.doc, pages, opts, ctx))

    def remove_marks(self) -> None:
        pages = self._scope("all")
        self.modify(lambda: marks.remove_marks(self.doc, pages))

    # --- Auftrag und Ausgabe ------------------------------------------------

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
            self.modify(lambda: self.doc.set_output_intent(data, identifier, components))

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
