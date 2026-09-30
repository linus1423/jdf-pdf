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
