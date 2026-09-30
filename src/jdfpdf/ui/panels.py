"""Seitenleisten-Panels: Auftrag, Medien, Weiterverarbeitung, Ausgabe."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.jdf import ColorModel, Finishing, Fold, MediaRange, Punch, Sides, Staple
from ..core.media import Media, MediaCatalog
from ..core.ppf import PressProfile, load_profiles
from ..core.prepress import OutputOptions, PdfxPolicy
from .i18n import Translator
from .widgets import EnumCombo


def _form(tr: Translator, rows: list[tuple[str, QWidget]]) -> QFormLayout:
    form = QFormLayout()
    for key, widget in rows:
        form.addRow(tr.bind(QLabel(), key), widget)
    return form


class JobPanel(QWidget):
    def __init__(self, tr: Translator) -> None:
        super().__init__()
        self.name = QLineEdit()
        self.copies = QSpinBox(minimum=1, maximum=1_000_000)
        self.sides = EnumCombo(Sides, tr)
        self.color = EnumCombo(ColorModel, tr)
        self.customer = QLineEdit()
        self.comment = QPlainTextEdit()
        self.comment.setMaximumHeight(90)
        layout = QVBoxLayout(self)
        layout.addLayout(
            _form(
                tr,
                [
                    ("job_name", self.name),
                    ("copies", self.copies),
                    ("sides", self.sides),
                    ("color", self.color),
                    ("customer", self.customer),
                    ("comment", self.comment),
                ],
            )
        )
        layout.addStretch()


class MediaPanel(QWidget):
    """Standardmedium und Medien je Seitenbereich."""

    changed = Signal()
    catalog_requested = Signal()

    def __init__(self, tr: Translator, catalog: MediaCatalog) -> None:
        super().__init__()
        self.tr_ = tr
        self.catalog = catalog
        self.page_count = 0
        self.default = QComboBox()
        self.default.currentIndexChanged.connect(self.changed)

        self.ranges = QTableWidget(0, 3)
        self._set_headers()
        tr.bind(self, "", setter="_retranslate")
        self.ranges.horizontalHeader().setStretchLastSection(True)
        self.ranges.itemChanged.connect(lambda *_: self.changed.emit())

        self.range_media = QComboBox()
        add = tr.bind(QPushButton(clicked=self.assign_to_selection), "assign_selection")
        remove = tr.bind(QPushButton(clicked=self._remove_selected), "remove")
        catalog_btn = tr.bind(QPushButton(clicked=self.catalog_requested), "edit_catalog")

        layout = QVBoxLayout(self)
        layout.addLayout(_form(tr, [("media_default", self.default)]))
        layout.addWidget(tr.bind(QLabel(), "media_ranges"))
        layout.addWidget(self.ranges)
        row = QHBoxLayout()
        row.addWidget(self.range_media, 1)
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        layout.addWidget(catalog_btn)
        self.selection_provider = lambda: []
        self.reload_catalog()

    def _retranslate(self, _text: str) -> None:
        self._set_headers()
        self.default.setItemText(0, self.tr_("media_from_document"))

    def _set_headers(self) -> None:
        self.ranges.setHorizontalHeaderLabels([self.tr_("from_page"), self.tr_("to_page"), self.tr_("media")])

    def reload_catalog(self) -> None:
        current = self.default.currentData()
        self.default.blockSignals(True)
        self.default.clear()
        self.default.addItem(self.tr_("media_from_document"), None)
        self.range_media.clear()
        for media in self.catalog.media:
            self.default.addItem(media.label(), media.name)
            self.range_media.addItem(media.label(), media.name)
        index = self.default.findData(current)
        self.default.setCurrentIndex(max(index, 0))
        self.default.blockSignals(False)

    def default_media(self) -> Media | None:
        name = self.default.currentData()
        return self.catalog.get(name) if name else None

    def add_range(self, first: int, last: int, media_name: str) -> None:
        self.ranges.blockSignals(True)
        row = self.ranges.rowCount()
        self.ranges.insertRow(row)
        self.ranges.setItem(row, 0, QTableWidgetItem(str(first + 1)))
        self.ranges.setItem(row, 1, QTableWidgetItem(str(last + 1)))
        self.ranges.setItem(row, 2, QTableWidgetItem(media_name))
        self.ranges.blockSignals(False)
        self.changed.emit()

    def assign_to_selection(self) -> None:
        pages = self.selection_provider()
        name = self.range_media.currentData()
        if not pages or not name:
            return
        # zusammenhängende Bereiche bilden
        start = prev = pages[0]
        for page in pages[1:] + [None]:
            if page is not None and page == prev + 1:
                prev = page
                continue
            self.add_range(start, prev, name)
            if page is not None:
                start = prev = page

    def _remove_selected(self) -> None:
        for row in sorted({i.row() for i in self.ranges.selectedIndexes()}, reverse=True):
            self.ranges.removeRow(row)
        self.changed.emit()

    def clear_ranges(self) -> None:
        self.ranges.setRowCount(0)
        self.changed.emit()

    def media_ranges(self) -> list[MediaRange]:
        result = []
        for row in range(self.ranges.rowCount()):
            try:
                first = int(self.ranges.item(row, 0).text()) - 1
                last = int(self.ranges.item(row, 1).text()) - 1
            except (AttributeError, ValueError):
                continue
            media = self.catalog.get(self.ranges.item(row, 2).text())
            if media is not None and 0 <= first <= last:
                result.append(MediaRange(first, last, media))
        return result

    def media_for_page(self, index: int) -> Media | None:
        for rng in reversed(self.media_ranges()):
            if rng.first <= index <= rng.last:
                return rng.media
        return self.default_media()


class FinishingPanel(QWidget):
    changed = Signal()

    def __init__(self, tr: Translator) -> None:
        super().__init__()
        self.staple = EnumCombo(Staple, tr, "staple")
        self.punch = EnumCombo(Punch, tr, "punch")
        self.fold = EnumCombo(Fold, tr, "fold")
        self.trim = tr.bind(QCheckBox(), "trim")
        for widget in (self.staple, self.punch, self.fold):
            widget.currentIndexChanged.connect(self.changed)
        self.trim.toggled.connect(self.changed)
        layout = QVBoxLayout(self)
        layout.addLayout(_form(tr, [("staple", self.staple), ("punch", self.punch), ("fold", self.fold)]))
        layout.addWidget(self.trim)
        layout.addStretch()

    def finishing(self) -> Finishing:
        return Finishing(self.staple.value(), self.punch.value(), self.fold.value(), self.trim.isChecked())


class OutputPanel(QWidget):
    write_requested = Signal()
    output_intent_requested = Signal()
    zones_requested = Signal()

    def __init__(self, tr: Translator, profiles: list[PressProfile] | None = None) -> None:
        super().__init__()
        self.tr_ = tr
        self.embed = tr.bind(QCheckBox(checked=True), "out_embed")
        self.sidecar = tr.bind(QCheckBox(checked=True), "out_sidecar")
        self.ticketing = tr.bind(QCheckBox(), "out_ticketing")
        self.pdfx_anyway = tr.bind(QCheckBox(), "out_pdfx_anyway")
        self.preflight = tr.bind(QCheckBox(), "out_preflight")
        self.ppf = tr.bind(QCheckBox(), "out_ppf")
        self.ppf_embed = tr.bind(QCheckBox(), "out_ppf_embed")
        self.profile = QComboBox()
        self.set_profiles(profiles if profiles is not None else load_profiles())
        zones_btn = tr.bind(QPushButton(clicked=self.zones_requested), "ink_zones")
        profile_row = QHBoxLayout()
        profile_row.addWidget(tr.bind(QLabel(), "press_profile"))
        profile_row.addWidget(self.profile, 1)
        profile_row.addWidget(zones_btn)
        self.info = QLabel(wordWrap=True)
        self.warning = QLabel(wordWrap=True)
        self.warning.setStyleSheet("color: #b35c00;")
        self.intent = QLabel(wordWrap=True)
        intent_btn = tr.bind(QPushButton(clicked=self.output_intent_requested), "set_output_intent")
        self.write_btn = tr.bind(QPushButton(clicked=self.write_requested), "write_output")
        self.attachments_label = tr.bind(QLabel(), "attachments")
        self.attachments = QListWidget()

        layout = QVBoxLayout(self)
        for widget in (self.embed, self.sidecar, self.ticketing, self.pdfx_anyway, self.preflight, self.ppf,
                       self.ppf_embed):
            layout.addWidget(widget)
        layout.addLayout(profile_row)
        for widget in (self.write_btn, self.info, self.warning, self.intent, intent_btn, self.attachments_label,
                       self.attachments):
            layout.addWidget(widget)
        layout.addStretch()

    def set_profiles(self, profiles: list[PressProfile]) -> None:
        self.profile.clear()
        for profile in profiles:
            self.profile.addItem(profile.name, profile)

    def press_profile(self) -> PressProfile | None:
        return self.profile.currentData()

    def options(self) -> OutputOptions:
        return OutputOptions(
            embed=self.embed.isChecked(),
            sidecar=self.sidecar.isChecked(),
            ticketing=self.ticketing.isChecked(),
            pdfx_policy=PdfxPolicy.EMBED_ANYWAY if self.pdfx_anyway.isChecked() else PdfxPolicy.KEEP,
            ppf=self.ppf.isChecked(),
            ppf_embed=self.ppf_embed.isChecked(),
            ppf_profile=self.press_profile(),
            preflight=self.preflight.isChecked(),
            language=self.tr_.language,
        )

    def set_options(self, options: OutputOptions) -> None:
        self.embed.setChecked(options.embed)
        self.sidecar.setChecked(options.sidecar)
        self.ticketing.setChecked(options.ticketing)
        self.pdfx_anyway.setChecked(options.pdfx_policy == PdfxPolicy.EMBED_ANYWAY)
        self.ppf.setChecked(options.ppf)
        self.preflight.setChecked(options.preflight)
        self.ppf_embed.setChecked(options.ppf_embed)
        if options.ppf_profile is not None:
            index = self.profile.findText(options.ppf_profile.name)
            if index < 0:
                self.profile.addItem(options.ppf_profile.name, options.ppf_profile)
                index = self.profile.count() - 1
            self.profile.setCurrentIndex(index)


class SectionsPanel(QWidget):
    """Strukturansicht: Abschnitte des Dokuments (oberste Lesezeichenebene)."""

    section_clicked = Signal(int, int)  # erste, letzte Seite
    new_requested = Signal()
    rename_requested = Signal(int)
    delete_requested = Signal(int)
    bookmarks_requested = Signal()

    def __init__(self, tr: Translator) -> None:
        from PySide6.QtWidgets import QTreeWidget

        super().__init__()
        self.tr_ = tr
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.itemDoubleClicked.connect(lambda item, _col: self.rename_requested.emit(self._row(item)))
        new = tr.bind(QPushButton(clicked=self.new_requested), "new_section")
        rename = tr.bind(QPushButton(clicked=lambda: self._emit_current(self.rename_requested)), "rename")
        delete = tr.bind(QPushButton(clicked=lambda: self._emit_current(self.delete_requested)), "remove")
        bookmarks = tr.bind(QPushButton(clicked=self.bookmarks_requested), "from_bookmarks")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.tree)
        row = QHBoxLayout()
        for button in (new, rename, delete):
            row.addWidget(button)
        layout.addLayout(row)
        layout.addWidget(bookmarks)
        self._ranges: list[tuple[int, int]] = []

    def set_sections(self, sections, page_count: int) -> None:
        from PySide6.QtWidgets import QTreeWidgetItem

        self.tree.clear()
        self._ranges = []
        for i, section in enumerate(sections):
            last = (sections[i + 1].page - 1) if i + 1 < len(sections) else page_count - 1
            self._ranges.append((section.page, last))
            span = f"{section.page + 1}" if last == section.page else f"{section.page + 1}–{last + 1}"
            self.tree.addTopLevelItem(QTreeWidgetItem([f"{section.title}  ({span})"]))

    def _row(self, item) -> int:
        return self.tree.indexOfTopLevelItem(item)

    def _clicked(self, item, _col) -> None:
        first, last = self._ranges[self._row(item)]
        self.section_clicked.emit(first, last)

    def _emit_current(self, signal) -> None:
        item = self.tree.currentItem()
        if item is not None:
            signal.emit(self._row(item))


class LayoutPanel(QWidget):
    """Ausschießen: Layout, Bogen, Nutzen, Broschüre, Klebebindung, Bogenansicht."""

    changed = Signal()

    def __init__(self, tr: Translator) -> None:
        from PySide6.QtWidgets import QDoubleSpinBox

        from ..core.impose import BackFlip, Layout
        from .dialogs import PAGE_PRESETS

        super().__init__()
        self.tr_ = tr
        self.layout_combo = EnumCombo(Layout, tr, "layout")
        self.sheet_preset = QComboBox()
        self.sheet_preset.addItem("–", None)
        for name, size in {**PAGE_PRESETS, "SRA4": (225, 320), "SRA3+": (330, 488)}.items():
            self.sheet_preset.addItem(name, size)
        self.sheet_w = QDoubleSpinBox(minimum=10, maximum=2000, value=320, suffix=" mm", decimals=1)
        self.sheet_h = QDoubleSpinBox(minimum=10, maximum=2000, value=450, suffix=" mm", decimals=1)
        self.auto_sheet = tr.bind(QCheckBox(), "auto_sheet")
        self.cols = QSpinBox(minimum=1, maximum=50, value=2)
        self.rows = QSpinBox(minimum=1, maximum=50, value=2)
        self.gap = QDoubleSpinBox(minimum=0, maximum=100, suffix=" mm", decimals=1)
        self.bleed = QDoubleSpinBox(minimum=0, maximum=20, suffix=" mm", decimals=1)
        self.rotate = tr.bind(QCheckBox(), "rotate_cells")
        self.duplex = tr.bind(QCheckBox(checked=True), "duplex")
        self.back_flip = EnumCombo(BackFlip, tr, "flip")
        self.crop_marks = tr.bind(QCheckBox(), "mark_crop")
        self.creep_auto = tr.bind(QCheckBox(checked=True), "creep_auto")
        self.creep = QDoubleSpinBox(minimum=0, maximum=20, suffix=" mm", decimals=2)
        self.thickness = QDoubleSpinBox(minimum=0.01, maximum=2, value=0.1, suffix=" mm", decimals=3)
        self.sheets_per_booklet = QSpinBox(minimum=1, maximum=50, value=4)
        self.spine = QDoubleSpinBox(minimum=0, maximum=200, suffix=" mm", decimals=1)
        self.glue = QDoubleSpinBox(minimum=0, maximum=30, suffix=" mm", decimals=1)
        self.cover = tr.bind(QCheckBox(), "cover_first_last")
        self.sheet_view = tr.bind(QPushButton(checkable=True), "sheet_view")

        layout = QVBoxLayout(self)
        layout.addLayout(_form(tr, [
            ("layout", self.layout_combo), ("preset", self.sheet_preset), ("sheet_width", self.sheet_w),
            ("sheet_height", self.sheet_h),
        ]))
        layout.addWidget(self.auto_sheet)
        layout.addLayout(_form(tr, [
            ("cols", self.cols), ("rows", self.rows), ("gap", self.gap), ("bleed", self.bleed),
        ]))
        for widget in (self.rotate, self.duplex):
            layout.addWidget(widget)
        layout.addLayout(_form(tr, [("back_flip", self.back_flip)]))
        layout.addWidget(self.crop_marks)
        layout.addWidget(self.creep_auto)
        layout.addLayout(_form(tr, [
            ("creep", self.creep), ("paper_thickness", self.thickness),
            ("sheets_per_booklet", self.sheets_per_booklet), ("spine_width", self.spine),
            ("glue_zone", self.glue),
        ]))
        layout.addWidget(self.cover)
        layout.addWidget(self.sheet_view)
        layout.addStretch()

        self.sheet_preset.currentIndexChanged.connect(self._apply_preset)
        for widget in (self.layout_combo, self.back_flip):
            widget.currentIndexChanged.connect(self.changed)
        for widget in (self.sheet_w, self.sheet_h, self.gap, self.bleed, self.creep, self.thickness,
                       self.spine, self.glue):
            widget.valueChanged.connect(self.changed)
        for widget in (self.cols, self.rows, self.sheets_per_booklet):
            widget.valueChanged.connect(self.changed)
        for widget in (self.auto_sheet, self.rotate, self.duplex, self.crop_marks, self.creep_auto, self.cover):
            widget.toggled.connect(self.changed)

    def _apply_preset(self) -> None:
        size = self.sheet_preset.currentData()
        if size:
            self.sheet_w.setValue(size[0])
            self.sheet_h.setValue(size[1])

    def imposition(self, page_count: int = 0):
        from ..core.impose import Imposition

        return Imposition(
            layout=self.layout_combo.value(), sheet_width_mm=self.sheet_w.value(),
            sheet_height_mm=self.sheet_h.value(), auto_sheet=self.auto_sheet.isChecked(),
            cols=self.cols.value(), rows=self.rows.value(), gap_mm=self.gap.value(), bleed_mm=self.bleed.value(),
            rotate=self.rotate.isChecked(), duplex=self.duplex.isChecked(), back_flip=self.back_flip.value(),
            crop_marks=self.crop_marks.isChecked(),
            creep_mm=None if self.creep_auto.isChecked() else self.creep.value(),
            paper_thickness_mm=self.thickness.value(), sheets_per_booklet=self.sheets_per_booklet.value(),
            spine_mm=self.spine.value(), glue_zone_mm=self.glue.value(),
            cover_front=0 if self.cover.isChecked() and page_count else None,
            cover_back=page_count - 1 if self.cover.isChecked() and page_count > 1 else None,
        )

    def set_imposition(self, imp) -> None:
        widgets = [self.layout_combo, self.sheet_w, self.sheet_h, self.auto_sheet, self.cols, self.rows, self.gap,
                   self.bleed, self.rotate, self.duplex, self.back_flip, self.crop_marks, self.creep_auto,
                   self.creep, self.thickness, self.sheets_per_booklet, self.spine, self.glue, self.cover]
        for w in widgets:
            w.blockSignals(True)
        self.layout_combo.set_value(imp.layout)
        self.sheet_w.setValue(imp.sheet_width_mm)
        self.sheet_h.setValue(imp.sheet_height_mm)
        self.auto_sheet.setChecked(imp.auto_sheet)
        self.cols.setValue(imp.cols)
        self.rows.setValue(imp.rows)
        self.gap.setValue(imp.gap_mm)
        self.bleed.setValue(imp.bleed_mm)
        self.rotate.setChecked(imp.rotate)
        self.duplex.setChecked(imp.duplex)
        self.back_flip.set_value(imp.back_flip)
        self.crop_marks.setChecked(imp.crop_marks)
        self.creep_auto.setChecked(imp.creep_mm is None)
        self.creep.setValue(imp.creep_mm or 0)
        self.thickness.setValue(imp.paper_thickness_mm)
        self.sheets_per_booklet.setValue(imp.sheets_per_booklet)
        self.spine.setValue(imp.spine_mm)
        self.glue.setValue(imp.glue_zone_mm)
        self.cover.setChecked(imp.cover_front is not None)
        for w in widgets:
            w.blockSignals(False)
        self.changed.emit()
