"""Dialoge für Werkzeuge: Scannen, Bereinigen, Text, Serienbrief (VDP), externe Programme, Softproof."""

from __future__ import annotations

from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import cleanup, external, scan, textedit, vdp
from ..core.pdfdoc import PdfDocument
from .dialogs import _Form
from .dialogs_automation import PathEdit, _ok_cancel
from .i18n import Translator


def _spin(value: float, maximum: float = 2000, decimals: int = 1, minimum: float = 0) -> QDoubleSpinBox:
    box = QDoubleSpinBox(decimals=decimals, minimum=minimum, maximum=maximum)
    box.setValue(value)
    return box


def _combo(tr: Translator, prefix: str, values: list[str], current: str = "") -> QComboBox:
    combo = QComboBox()
    for value in values:
        combo.addItem(tr(f"{prefix}_{value}"), value)
    if current in values:
        combo.setCurrentIndex(values.index(current))
    return combo


def _pixmap(image, max_size: int = 360) -> QPixmap:
    image = image.copy()
    image.thumbnail((max_size, max_size))
    return QPixmap.fromImage(ImageQt(image.convert("RGB")))


class _Cmyk(QHBoxLayout):
    def __init__(self, values: tuple[float, float, float, float]) -> None:
        super().__init__()
        self.boxes = []
        for label, value in zip("CMYK", values):
            box = _spin(value, 100, 0)
            box.setPrefix(f"{label} ")
            self.addWidget(box)
            self.boxes.append(box)

    def value(self) -> tuple[float, float, float, float]:
        return tuple(b.value() for b in self.boxes)


def _font_combo(current: str = "Vera Sans") -> QComboBox:
    combo = QComboBox(editable=True)
    combo.addItems(sorted(textedit.available_fonts()))
    combo.setCurrentText(current)
    combo.setToolTip("TTF")
    return combo


# --- Scannen und Bereinigen -------------------------------------------------------------


class ScanDialog(_Form):
    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(tr, "scan_title", parent)
        self.scanner = QComboBox()
        refresh = QPushButton(tr("refresh"), clicked=self.refresh)
        row = QHBoxLayout()
        row.addWidget(self.scanner, 1)
        row.addWidget(refresh)
        self.row("scanner", row)
        self.dpi = self.row("dpi", QSpinBox(minimum=75, maximum=1200, value=300, singleStep=50))
        self.mode = self.row("scan_mode", _combo(tr, "mode", ["color", "gray", "lineart"]))
        self.source = self.row("scan_source", QComboBox(editable=True))
        self.source.addItems(["", "Flatbed", "ADF", "ADF Duplex"])
        self.batch = self.row("scan_batch", QCheckBox())
        self.cleanup = self.row("scan_then_cleanup", QCheckBox())

    def refresh(self) -> None:
        self.scanner.clear()
        try:
            found = scan.list_scanners()
        except Exception as exc:
            QMessageBox.warning(self, self.tr_("error"), str(exc))
            return
        if not found:
            self.scanner.addItem(self.tr_("no_scanner"), "")
        for scanner in found:
            self.scanner.addItem(scanner.name, scanner.id)

    def options(self) -> scan.ScanOptions:
        return scan.ScanOptions(self.dpi.value(), self.mode.currentData(), self.source.currentText().strip(),
                                self.batch.isChecked())

    def device(self) -> str:
        return self.scanner.currentData() or ""


class CleanupDialog(QDialog):
    """Einstellungen mit Vorher/Nachher-Vorschau der aktuellen Seite."""

    def __init__(self, tr: Translator, doc: PdfDocument, index: int, rasterize_only: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        self.tr_, self.doc, self.index = tr, doc, index
        self.setWindowTitle(tr("rasterize_title" if rasterize_only else "cleanup_title"))
        form = _Form(tr, "cleanup_title")  # nur für das Formularlayout
        self.dpi = form.row("dpi", QSpinBox(minimum=72, maximum=1200, value=300, singleStep=50))
        self.mode = form.row("color_mode", _combo(tr, "mode", ["keep", "gray", "lineart"]))
        self.threshold = form.row("threshold", QSpinBox(minimum=1, maximum=254, value=128))
        self.despeckle = form.row("despeckle", QComboBox())
        for size in (0, 3, 5):
            self.despeckle.addItem(tr("off") if size == 0 else f"{size} px", size)
        self.deskew = form.row("deskew", QCheckBox(checked=True))
        self.max_angle = form.row("max_angle", _spin(5, 20))
        self.align = form.row("align", _combo(tr, "align", ["none", "center", "top_left"]))
        self.margin = form.row("margin_mm", _spin(10, 100))
        self.border = form.row("border_mm", _spin(0, 50))
        self.erase = form.row("erase_areas", QPlainTextEdit())
        self.erase.setPlaceholderText(tr("erase_hint"))
        self.erase.setMaximumHeight(70)
        form.buttons.hide()
        for widget in (self.threshold, self.despeckle, self.deskew, self.max_angle, self.align, self.margin,
                       self.border, self.erase):
            label = form.form.labelForField(widget)
            if rasterize_only:
                widget.hide()
                label.hide()
        self.before, self.after = QLabel(), QLabel()
        preview = QPushButton(tr("preview"), clicked=self.update_preview)
        images = QHBoxLayout()
        images.addWidget(self.before)
        images.addWidget(self.after)
        layout = QVBoxLayout(self)
        layout.addWidget(form)
        layout.addWidget(preview)
        layout.addLayout(images)
        self.info = QLabel()
        layout.addWidget(self.info)
        layout.addWidget(_ok_cancel(self))

    def options(self) -> cleanup.CleanupOptions:
        rects = []
        for line in self.erase.toPlainText().splitlines():
            parts = line.replace(",", " ").replace(";", " ").split()
            if len(parts) == 4:
                rects.append(tuple(float(p) for p in parts))
        return cleanup.CleanupOptions(
            dpi=self.dpi.value(), mode=self.mode.currentData(), threshold=self.threshold.value(),
            despeckle=self.despeckle.currentData(), deskew=self.deskew.isChecked(),
            max_angle=self.max_angle.value(), align=self.align.currentData(), margin_mm=self.margin.value(),
            border_mm=self.border.value(), erase=rects)

    def update_preview(self) -> None:
        opts = self.options()
        opts.dpi = min(opts.dpi, 100)  # Vorschau schneller
        source = cleanup.render_visible(self.doc, self.index, opts.dpi)
        result, angle = cleanup.clean_image(source, opts)
        self.before.setPixmap(_pixmap(source))
        self.after.setPixmap(_pixmap(result))
        self.info.setText(self.tr_("deskew_angle", f"{angle:+.2f}"))


# --- Text -------------------------------------------------------------------------------


class TextBlockDialog(_Form):
    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(tr, "text_block_title", parent)
        self.text = self.row("text", QPlainTextEdit())
        self.font = self.row("font", _font_combo())
        self.size = self.row("font_size", _spin(11, 500))
        self.x = self.row("x_mm", _spin(20))
        self.y = self.row("y_mm_top", _spin(20))
        self.width = self.row("wrap_width_mm", _spin(0))
        self.align = self.row("text_align", _combo(tr, "talign", ["left", "center", "right"]))
        self.color = _Cmyk((0, 0, 0, 100))
        self.row("color_cmyk", self.color)
        self.cover = self.row("cover_white", QCheckBox())
        self.rotation = self.row("rotation", _spin(0, 360, 0, -360))

    def block(self) -> textedit.TextBlock:
        return textedit.TextBlock(
            text=self.text.toPlainText(), x_mm=self.x.value(), y_mm=self.y.value(), width_mm=self.width.value(),
            font=self.font.currentText(), size=self.size.value(), color_cmyk=self.color.value(),
            align=self.align.currentData(), background_cmyk=(0, 0, 0, 0) if self.cover.isChecked() else None,
            rotation=self.rotation.value())


class ReplaceTextDialog(_Form):
    def __init__(self, tr: Translator, doc: PdfDocument, parent=None) -> None:
        super().__init__(tr, "replace_text_title", parent)
        self.doc = doc
        self.find = self.row("find_text", QLineEdit())
        self.replace = self.row("replace_with", QLineEdit())
        self.match_case = self.row("match_case", QCheckBox())
        self.whole_word = self.row("whole_word", QCheckBox())
        self.method = self.row("replace_method", _combo(tr, "method", ["cover", "rewrite"]))
        self.font = self.row("font", _font_combo())
        self.size = self.row("font_size_auto", _spin(0, 500))
        self.color = _Cmyk((0, 0, 0, 100))
        self.row("color_cmyk", self.color)
        self.selection_only = self.row("selected_pages_only", QCheckBox())
        self.hits = QLabel()
        count = QPushButton(tr("count_hits"), clicked=self.count)
        self.form.addRow(count, self.hits)
        self.method.currentIndexChanged.connect(self._method_changed)

    def _method_changed(self) -> None:
        cover = self.method.currentData() == "cover"
        for widget in (self.font, self.size, *self.color.boxes):
            widget.setEnabled(cover)

    def count(self) -> None:
        hits = textedit.find_text(self.doc, self.find.text(), None, self.match_case.isChecked(),
                                  self.whole_word.isChecked())
        self.hits.setText(self.tr_("hits_found", len(hits), len({h.page for h in hits})))

    def style(self) -> textedit.ReplaceStyle:
        return textedit.ReplaceStyle(font=self.font.currentText(), size=self.size.value() or None,
                                     color_cmyk=self.color.value())


# --- Serienbrief ------------------------------------------------------------------------


_FIELD_COLUMNS = ["field_kind", "field_value", "field_page", "x_mm", "y_mm_top", "width_mm", "height_mm",
                  "font_size"]
_KINDS = ["text", "image", "code128", "ean13", "qr"]


class VdpDialog(QDialog):
    def __init__(self, tr: Translator, doc: PdfDocument, setup: vdp.VdpSetup | None = None, parent=None) -> None:
        super().__init__(parent)
        self.tr_, self.doc = tr, doc
        self.data: vdp.DataSource | None = None
        self.setWindowTitle(tr("vdp_title"))
        self.resize(1100, 680)
        self.source = PathEdit(tr, filter_key="data_filter")
        load = QPushButton(tr("load"), clicked=self.load_data)
        self.sheet = QComboBox()
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("data_source")))
        top.addLayout(self.source, 1)
        top.addWidget(self.sheet)
        top.addWidget(load)

        self.columns = QListWidget()
        self.columns.setToolTip(tr("columns_hint"))
        self.columns.itemDoubleClicked.connect(self._insert_column)
        self.table = QTableWidget(0, len(_FIELD_COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in _FIELD_COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)
        add = QPushButton(tr("add"), clicked=lambda: self._append(vdp.Field(value="{" + self._first_column() + "}")))
        remove = QPushButton(tr("remove"), clicked=self._remove)
        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()
        fields_box = QVBoxLayout()
        fields_box.addWidget(self.table)
        fields_box.addLayout(buttons)

        self.record = QSpinBox(minimum=1, maximum=1)
        self.record.valueChanged.connect(self.update_preview)
        self.preview = QLabel()
        self.preview.setMinimumSize(360, 360)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_btn = QPushButton(tr("preview"), clicked=self.update_preview)
        right = QVBoxLayout()
        nav = QHBoxLayout()
        nav.addWidget(QLabel(tr("record")))
        nav.addWidget(self.record)
        nav.addWidget(preview_btn)
        right.addLayout(nav)
        right.addWidget(self.preview, 1)

        middle = QHBoxLayout()
        middle.addWidget(self.columns, 1)
        middle.addLayout(fields_box, 4)
        middle.addLayout(right, 3)

        self.records = QLineEdit()
        self.records.setPlaceholderText(tr("all"))
        self.sections = QCheckBox(tr("vdp_sections"))
        save = QPushButton(tr("save_setup"), clicked=self.save_setup)
        open_ = QPushButton(tr("load_setup"), clicked=self.load_setup)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(tr("records")))
        bottom.addWidget(self.records)
        bottom.addWidget(self.sections)
        bottom.addStretch()
        bottom.addWidget(open_)
        bottom.addWidget(save)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(middle, 1)
        layout.addLayout(bottom)
        layout.addWidget(_ok_cancel(self))
        if setup is not None:
            self.set_setup(setup)

    # Daten
    def load_data(self) -> None:
        path = self.source.text()
        if not path:
            return
        try:
            if Path(path).suffix.lower() in (".xlsx", ".xlsm") and self.sheet.count() == 0:
                self.sheet.addItems(vdp.xlsx_sheets(path))
            sheet = self.sheet.currentText() if self.sheet.count() else 0
            self.data = vdp.read_data(path, sheet or 0)
        except Exception as exc:
            QMessageBox.critical(self, self.tr_("error"), str(exc))
            return
        self.columns.clear()
        self.columns.addItems(self.data.headers)
        self.record.setMaximum(max(len(self.data), 1))
        if self.table.rowCount() == 0 and self.data.headers:
            self._append(vdp.Field(value="{" + self.data.headers[0] + "}"))
        self.update_preview()

    def _first_column(self) -> str:
        return self.data.headers[0] if self.data and self.data.headers else "Spalte1"

    def _insert_column(self, item) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        cell = self.table.item(row, 1)
        cell.setText(cell.text() + "{" + item.text() + "}")

    # Felder
    def _append(self, f: vdp.Field) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        kind = _combo(self.tr_, "kind", _KINDS, f.kind)
        self.table.setCellWidget(row, 0, kind)
        self.table.setItem(row, 1, QTableWidgetItem(f.value))
        for column, value in enumerate([f.page, f.x_mm, f.y_mm, f.width_mm, f.height_mm, f.size], start=2):
            self.table.setItem(row, column, QTableWidgetItem(f"{value:g}"))
        self.table.setCurrentCell(row, 1)

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def fields(self) -> list[vdp.Field]:
        result = []
        for row in range(self.table.rowCount()):
            def num(column: int, default: float = 0.0) -> float:
                item = self.table.item(row, column)
                try:
                    return float(item.text().replace(",", ".")) if item and item.text().strip() else default
                except ValueError:
                    return default

            value = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            result.append(vdp.Field(kind=self.table.cellWidget(row, 0).currentData(), value=value,
                                    page=int(num(2, 1)), x_mm=num(3), y_mm=num(4), width_mm=num(5),
                                    height_mm=num(6), size=num(7, 11) or 11))
        return result

    def update_preview(self) -> None:
        if self.data is None or not self.data.rows:
            return
        from ..core.render import render_page

        try:
            doc = vdp.preview(self.doc, self.data, self.fields(), self.record.value() - 1)
            image = render_page(doc.to_bytes(), 0, scale=0.8)
        except Exception as exc:
            self.preview.setText(str(exc))
            return
        self.preview.setPixmap(_pixmap(image, 420))

    # Einrichtung
    def setup(self) -> vdp.VdpSetup:
        return vdp.VdpSetup(self.source.text(), self.sheet.currentText() if self.sheet.count() else 0,
                            self.fields(), self.records.text().strip(), self.sections.isChecked())

    def set_setup(self, setup: vdp.VdpSetup) -> None:
        self.source.edit.setText(setup.data)
        self.table.setRowCount(0)
        for f in setup.fields:
            self._append(f)
        self.records.setText(setup.records)
        self.sections.setChecked(setup.sections)
        if setup.data and Path(setup.data).exists():
            self.load_data()

    def save_setup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("save_setup"), "", self.tr_("vdp_filter"))
        if path:
            self.setup().save(path if path.endswith(vdp.SUFFIX) else path + vdp.SUFFIX)

    def load_setup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("load_setup"), "", self.tr_("vdp_filter"))
        if path:
            try:
                self.set_setup(vdp.VdpSetup.load(path))
            except Exception as exc:
                QMessageBox.critical(self, self.tr_("error"), str(exc))


# --- Externe Programme ------------------------------------------------------------------


class EditorsDialog(QDialog):
    def __init__(self, tr: Translator, editors: list[external.ExternalEditor], parent=None) -> None:
        super().__init__(parent)
        self.tr_ = tr
        self.setWindowTitle(tr("external_editors"))
        self.resize(760, 360)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("col_name"), tr("program"), tr("arguments")])
        self.table.horizontalHeader().setStretchLastSection(True)
        for editor in editors:
            self._append(editor)
        add = QPushButton(tr("add"), clicked=self._add)
        remove = QPushButton(tr("remove"), clicked=self._remove)
        suggest = QPushButton(tr("find_programs"), clicked=self._suggest)
        hint = QLabel(tr("editors_hint", external.MAX_EDITORS))
        hint.setWordWrap(True)
        row = QHBoxLayout()
        for button in (add, remove, suggest):
            row.addWidget(button)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(hint)
        layout.addLayout(row)
        layout.addWidget(_ok_cancel(self))

    def _append(self, editor: external.ExternalEditor) -> None:
        if self.table.rowCount() >= external.MAX_EDITORS:
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate((editor.name, editor.command, editor.args)):
            self.table.setItem(row, column, QTableWidgetItem(value))

    def _add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("program"))
        if path:
            self._append(external.ExternalEditor(Path(path).stem, path))

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def _suggest(self) -> None:
        known = {e.command for e in self.editors()}
        for editor in external.suggested_editors():
            if editor.command not in known:
                self._append(editor)

    def editors(self) -> list[external.ExternalEditor]:
        result = []
        for row in range(self.table.rowCount()):
            values = [self.table.item(row, c).text().strip() if self.table.item(row, c) else "" for c in range(3)]
            if values[0] and values[1]:
                result.append(external.ExternalEditor(values[0], values[1], values[2] or "{file}"))
        return result


# --- Softproof --------------------------------------------------------------------------


class SoftproofDialog(_Form):
    def __init__(self, tr: Translator, has_imposition: bool, parent=None) -> None:
        super().__init__(tr, "softproof_title", parent)
        self.media_color = self.row("proof_media_color", QCheckBox(checked=True))
        self.finishing = self.row("proof_finishing", QCheckBox(checked=True))
        self.tabs = self.row("proof_tabs", QCheckBox(checked=True))
        self.trim = self.row("proof_trim", QCheckBox(checked=True))
        self.spreads = self.row("proof_spreads", QCheckBox())
        self.sheets = self.row("proof_sheets", QCheckBox(enabled=has_imposition))
        self.info_page = self.row("proof_info_page", QCheckBox(checked=True))
        self.watermark = self.row("proof_watermark", QLineEdit())

    def options(self, language: str):
        from ..core.softproof import ProofOptions

        return ProofOptions(self.media_color.isChecked(), self.finishing.isChecked(), self.tabs.isChecked(),
                            self.trim.isChecked(), self.spreads.isChecked(), self.sheets.isChecked(),
                            self.info_page.isChecked(), self.watermark.text().strip(), language)


__all__ = ["CleanupDialog", "EditorsDialog", "ReplaceTextDialog", "ScanDialog", "SoftproofDialog",
           "TextBlockDialog", "VdpDialog"]
