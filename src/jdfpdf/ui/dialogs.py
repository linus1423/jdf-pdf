"""Dialoge."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.media import MM, Media, MediaCatalog
from .i18n import Translator

_COLUMNS = [
    "col_name", "col_width", "col_height", "col_weight", "col_type",
    "col_color", "col_coating", "col_thickness", "col_punched", "col_tabs",
]


class MediaCatalogDialog(QDialog):
    """Medienkatalog als Tabelle bearbeiten, JMF-Antworten importieren."""

    def __init__(self, catalog: MediaCatalog, tr: Translator, parent=None) -> None:
        super().__init__(parent)
        self.tr_ = tr
        self.catalog = catalog
        self.setWindowTitle(tr("catalog_title"))
        self.resize(900, 420)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in _COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)

        add = QPushButton(tr("add"), clicked=lambda: self._append(Media(f"Medium {self.table.rowCount() + 1}")))
        remove = QPushButton(tr("remove"), clicked=self._remove_selected)
        imp = QPushButton(tr("import_jmf"), clicked=self._import_jmf)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        for widget in (add, remove, imp):
            row.addWidget(widget)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(buttons)

        for media in catalog.media:
            self._append(media)

    def _append(self, media: Media) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            media.name, f"{media.width_pt / MM:.1f}", f"{media.height_pt / MM:.1f}",
            _fmt(media.weight_gsm), media.media_type, media.color or "", media.coating or "",
            _fmt(media.thickness_um), "", str(media.tab_count or ""),
        ]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if _COLUMNS[col] == "col_punched":
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if media.pre_punched else Qt.CheckState.Unchecked)
            self.table.setItem(row, col, item)

    def _remove_selected(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def _import_jmf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("import_jmf"), "", "JMF/XML (*.jmf *.xml)")
        if not path:
            return
        try:
            imported = MediaCatalog()
            count = imported.import_jmf(Path(path).read_bytes())
        except Exception as exc:
            QMessageBox.critical(self, self.tr_("error"), str(exc))
            return
        existing = {m.name for m in self.media()}
        for media in imported.media:
            if media.name in existing:
                self._remove_named(media.name)
            self._append(media)
        QMessageBox.information(self, self.tr_("catalog_title"), self.tr_("imported", count))

    def _remove_named(self, name: str) -> None:
        for row in range(self.table.rowCount() - 1, -1, -1):
            if self.table.item(row, 0).text() == name:
                self.table.removeRow(row)

    def media(self) -> list[Media]:
        result = []
        for row in range(self.table.rowCount()):
            cell = lambda c: (self.table.item(row, c).text().strip() if self.table.item(row, c) else "")
            name = cell(0)
            if not name:
                continue
            result.append(
                Media(
                    name=name,
                    width_pt=(_num(cell(1)) or 210) * MM,
                    height_pt=(_num(cell(2)) or 297) * MM,
                    weight_gsm=_num(cell(3)),
                    media_type=cell(4) or "Paper",
                    color=cell(5) or None,
                    coating=cell(6) or None,
                    thickness_um=_num(cell(7)),
                    pre_punched=self.table.item(row, 8).checkState() == Qt.CheckState.Checked,
                    tab_count=int(_num(cell(9)) or 0),
                )
            )
        return result

    def accept(self) -> None:
        self.catalog.media = self.media()
        super().accept()


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def _num(text: str) -> float | None:
    try:
        return float(text.replace(",", ".")) if text else None
    except ValueError:
        return None


PAGE_PRESETS = {
    "A5": (148, 210), "A4": (210, 297), "A3": (297, 420), "SRA3": (320, 450),
    "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8),
}


def _mm_spin(value: float = 0, minimum: float = -2000, maximum: float = 2000) -> "QDoubleSpinBox":
    from PySide6.QtWidgets import QDoubleSpinBox

    spin = QDoubleSpinBox(minimum=minimum, maximum=maximum, decimals=1, suffix=" mm")
    spin.setValue(value)
    return spin


class _Form(QDialog):
    """Basis für kleine Formulardialoge mit OK/Abbrechen."""

    def __init__(self, tr: Translator, title_key: str, parent=None) -> None:
        from PySide6.QtWidgets import QFormLayout

        super().__init__(parent)
        self.tr_ = tr
        self.setWindowTitle(tr(title_key))
        self.form = QFormLayout()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addWidget(self.buttons)

    def row(self, key: str, widget):
        self.form.addRow(self.tr_(key), widget)
        return widget


class ScaleDialog(_Form):
    """Seitenformat ändern: skalieren (anpassen/füllen/verzerren) oder nur Format (100 %)."""

    def __init__(self, tr: Translator, width_mm: float, height_mm: float, parent=None) -> None:
        from PySide6.QtWidgets import QComboBox

        from ..core.geometry import ScaleMode
        from .widgets import EnumCombo

        super().__init__(tr, "scale_title", parent)
        self.preset = self.row("preset", QComboBox())
        self.preset.addItem("–", None)
        for name, size in PAGE_PRESETS.items():
            self.preset.addItem(name, size)
        self.width = self.row("col_width", _mm_spin(width_mm, 1))
        self.height = self.row("col_height", _mm_spin(height_mm, 1))
        self.mode = self.row("scale_mode", EnumCombo(ScaleMode, tr, "scale"))
        self.scope = self.row("scope", _scope_combo(tr))
        self.preset.currentIndexChanged.connect(self._apply_preset)

    def _apply_preset(self) -> None:
        size = self.preset.currentData()
        if size:
            self.width.setValue(size[0])
            self.height.setValue(size[1])


class ShiftDialog(_Form):
    def __init__(self, tr: Translator, parent=None) -> None:
        from PySide6.QtWidgets import QCheckBox

        super().__init__(tr, "shift_title", parent)
        self.dx = self.row("shift_x", _mm_spin())
        self.dy = self.row("shift_y", _mm_spin())
        self.mirror = self.row("shift_mirror", QCheckBox())
        self.scope = self.row("scope", _scope_combo(tr))


class BleedDialog(_Form):
    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(tr, "bleed_title", parent)
        self.bleed = self.row("bleed", _mm_spin(3, 0, 50))
        self.scope = self.row("scope", _scope_combo(tr))


class BoxesDialog(_Form):
    """Seitenboxen numerisch bearbeiten (Werte in mm, Ursprung unten links)."""

    def __init__(self, tr: Translator, boxes: dict[str, tuple | None], parent=None) -> None:
        from PySide6.QtWidgets import QCheckBox, QGridLayout, QLabel, QWidget

        from ..core.pdfdoc import BOXES

        super().__init__(tr, "boxes_title", parent)
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        for col, key in enumerate(["", "box_left", "box_bottom", "box_right", "box_top"]):
            if key:
                grid.addWidget(QLabel(tr(key)), 0, col)
        self.fields: dict[str, tuple] = {}
        for row, name in enumerate(BOXES, start=1):
            check = QCheckBox(name)
            rect = boxes.get(name)
            check.setChecked(rect is not None)
            check.setEnabled(name != "MediaBox")
            spins = [_mm_spin((rect[i] / MM) if rect else 0) for i in range(4)]
            grid.addWidget(check, row, 0)
            for col, spin in enumerate(spins, start=1):
                grid.addWidget(spin, row, col)
            self.fields[name] = (check, spins)
        self.form.addRow(grid_widget)
        self.scope = self.row("scope", _scope_combo(tr))

    def boxes(self) -> dict[str, tuple | None]:
        return {
            name: tuple(s.value() * MM for s in spins) if check.isChecked() else None
            for name, (check, spins) in self.fields.items()
        }


def _scope_combo(tr: Translator):
    from PySide6.QtWidgets import QComboBox

    combo = QComboBox()
    combo.addItem(tr("scope_selection"), "selection")
    combo.addItem(tr("scope_all"), "all")
    return combo
