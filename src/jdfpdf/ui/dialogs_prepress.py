"""Dialoge für Preflight, Farbzonen (PPF) und Maschinenprofile."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import ppf
from ..core.pagerange import format_pages
from ..core.preflight import RULE_TITLES, PreflightProfile, PreflightReport, Severity, write_report
from .dialogs import _Form
from .i18n import Translator

_TONES = {Severity.ERROR: "#c62828", Severity.WARNING: "#b35c00", Severity.INFO: "#455a64"}


def _close_box(dialog: QDialog) -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    box.rejected.connect(dialog.reject)
    return box


class PreflightDialog(QDialog):
    """Befunde anzeigen; Klick auf eine Zeile wählt die Seiten aus; Bericht speichern."""

    pages_selected = Signal(list)

    def __init__(self, tr: Translator, report: PreflightReport, parent=None) -> None:
        super().__init__(parent)
        self.tr_, self.report = tr, report
        self.setWindowTitle(tr("preflight_title"))
        self.resize(820, 440)
        lang = tr.language
        summary = QLabel()
        errors, warnings = report.count(Severity.ERROR), report.count(Severity.WARNING)
        summary.setText(tr("preflight_summary", errors=errors, warnings=warnings, pages=report.page_count))
        summary.setStyleSheet(f"font-weight: bold; color: {'#2e7d32' if report.ok else '#c62828'};")
        self.table = QTableWidget(len(report.findings), 4)
        self.table.setHorizontalHeaderLabels([tr("severity"), tr("check"), tr("finding"), tr("pages_label")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row, f in enumerate(report.findings):
            cells = [tr(f"severity_{f.severity.value}"), RULE_TITLES[f.rule].get(lang, f.rule), f.text(lang),
                     format_pages(f.pages) if f.pages else "–"]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col == 0:
                    item.setForeground(QColor(_TONES[f.severity]))
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.cellClicked.connect(lambda row, _c: self.pages_selected.emit(report.findings[row].pages))
        save = QPushButton(tr("save_report"), clicked=self.save_report)
        row = QHBoxLayout()
        row.addWidget(save)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(summary)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(_close_box(self))

    def save_report(self, path: str | None = None) -> Path | None:
        if not path:
            stem = Path(self.report.file or "report").stem
            path, _ = QFileDialog.getSaveFileName(self, self.tr_("save_report"), f"{stem}_preflight.pdf",
                                                  self.tr_("report_filter"))
        if not path:
            return None
        return write_report(self.report, Path(path), self.tr_.language)


class PreflightSettingsDialog(_Form):
    def __init__(self, tr: Translator, profile: PreflightProfile, parent=None) -> None:
        super().__init__(tr, "preflight_settings", parent)
        self.min_ppi = self.row("min_ppi", QDoubleSpinBox(minimum=1, maximum=2400, value=profile.min_ppi, suffix=" ppi"))
        self.min_ppi_bitmap = self.row("min_ppi_bitmap", QDoubleSpinBox(
            minimum=1, maximum=4800, value=profile.min_ppi_bitmap, suffix=" ppi"))
        self.min_line = self.row("min_line", QDoubleSpinBox(
            minimum=0, maximum=5, decimals=2, singleStep=0.05, value=profile.min_line_pt, suffix=" pt"))
        self.bleed = self.row("bleed", QDoubleSpinBox(minimum=0, maximum=20, value=profile.bleed_mm, suffix=" mm"))
        self.cmyk = self.row("preflight_cmyk", QCheckBox(checked=profile.cmyk_output))
        self.checks = {}
        for name in ("fonts", "images", "colors", "bleed", "sizes", "lines", "transparency", "pdfx", "spots"):
            box = QCheckBox(checked=getattr(profile, "check_" + name))
            self.form.addRow(RULE_TITLES[name].get(tr.language, name), box)
            self.checks[name] = box

    def profile(self) -> PreflightProfile:
        return PreflightProfile(
            min_ppi=self.min_ppi.value(), min_ppi_bitmap=self.min_ppi_bitmap.value(),
            min_line_pt=self.min_line.value(), bleed_mm=self.bleed.value(), cmyk_output=self.cmyk.isChecked(),
            **{"check_" + k: box.isChecked() for k, box in self.checks.items()},
        )


class InkZonesDialog(QDialog):
    """Auszüge und Farbzonen einer Bogenseite."""

    def __init__(self, tr: Translator, side: ppf.SideResult, profile: ppf.PressProfile, parent=None) -> None:
        super().__init__(parent)
        self.tr_, self.side = tr, side
        self.setWindowTitle(tr("ink_zones_title", profile.name))
        self.resize(900, 520)
        previews = QHBoxLayout()
        for sep in side.separations:
            column = QVBoxLayout()
            label = QLabel()
            thumb = sep.plate.copy()
            thumb.thumbnail((150, 150))
            label.setPixmap(QPixmap.fromImage(ImageQt(thumb.convert("RGB"))))
            column.addWidget(label)
            column.addWidget(QLabel(f"{sep.name}: {sep.coverage:g} %"))
            previews.addLayout(column)
        previews.addStretch()
        zones = profile.zone_count
        self.table = QTableWidget(len(side.separations), zones)
        self.table.setHorizontalHeaderLabels([str(i + 1) for i in range(zones)])
        self.table.setVerticalHeaderLabels([s.name for s in side.separations])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, sep in enumerate(side.separations):
            for col, value in enumerate(sep.zones):
                item = QTableWidgetItem(f"{value:g}")
                shade = int(255 - min(value, 100) * 1.6)
                item.setBackground(QColor(shade, shade, 255))
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        layout = QVBoxLayout(self)
        layout.addLayout(previews)
        layout.addWidget(QLabel(tr("ink_zones_hint")))
        layout.addWidget(self.table)
        if side.skipped:
            layout.addWidget(QLabel(tr("ppf_skipped", ", ".join(f"{k} {v}" for k, v in side.skipped.items()))))
        layout.addWidget(_close_box(self))


_PROFILE_COLUMNS = ["col_name", "zone_count", "zone_width", "centered", "offset", "zones_along_height"]


class PressProfilesDialog(QDialog):
    def __init__(self, tr: Translator, profiles: list[ppf.PressProfile], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("press_profiles"))
        self.resize(760, 320)
        self.table = QTableWidget(0, len(_PROFILE_COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in _PROFILE_COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)
        for profile in profiles:
            self._append(profile)
        add = QPushButton(tr("add"), clicked=lambda: self._append(ppf.PressProfile(tr("new_profile"))))
        remove = QPushButton(tr("remove"), clicked=self._remove)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(box)

    def _append(self, profile: ppf.PressProfile) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = asdict(profile)
        for col, key in enumerate(("name", "zone_count", "zone_width_mm", "centered", "offset_mm",
                                   "zones_along_height")):
            value = values[key]
            item = QTableWidgetItem()
            if isinstance(value, bool):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
            else:
                item.setText(f"{value:g}" if isinstance(value, float) else str(value))
            self.table.setItem(row, col, item)

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(row)

    def profiles(self) -> list[ppf.PressProfile]:
        result = []
        for row in range(self.table.rowCount()):
            cell = lambda c: self.table.item(row, c)  # noqa: E731
            try:
                result.append(ppf.PressProfile(
                    name=cell(0).text().strip() or f"#{row + 1}",
                    zone_count=max(int(cell(1).text()), 1),
                    zone_width_mm=float(cell(2).text().replace(",", ".")),
                    centered=cell(3).checkState() == Qt.CheckState.Checked,
                    offset_mm=float(cell(4).text().replace(",", ".") or 0),
                    zones_along_height=cell(5).checkState() == Qt.CheckState.Checked,
                ))
            except (AttributeError, ValueError):
                continue
        return result
