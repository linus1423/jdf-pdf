"""Dialoge für Vorlagen, Hotfolder, Druckerprofile und das Senden an Drucker."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import printing
from ..core.hotfolder import Hotfolder
from ..core.template import Template, parse_param
from .i18n import Translator


def _ok_cancel(dialog: QDialog) -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class PathEdit(QHBoxLayout):
    def __init__(self, tr: Translator, directory: bool = False, filter_key: str = "", value: str = "") -> None:
        super().__init__()
        self.edit = QLineEdit(value)
        self._tr, self._dir, self._filter = tr, directory, filter_key
        self.addWidget(self.edit, 1)
        self.addWidget(QPushButton("…", clicked=self._browse))

    def _browse(self) -> None:
        if self._dir:
            path = QFileDialog.getExistingDirectory(None, "", self.edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(None, "", self.edit.text(), self._tr(self._filter))
        if path:
            self.edit.setText(path)

    def text(self) -> str:
        return self.edit.text().strip()


class StepsDialog(QDialog):
    """Aufgezeichnete Schritte ansehen, einzelne entfernen, als Vorlage speichern."""

    def __init__(self, tr: Translator, steps: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("recorded_steps"))
        self.resize(640, 400)
        self.steps = list(steps)
        self.list = QListWidget()
        self._fill()
        remove = QPushButton(tr("remove"), clicked=self._remove)
        clear = QPushButton(tr("clear_steps"), clicked=self._clear)
        row = QHBoxLayout()
        row.addWidget(remove)
        row.addWidget(clear)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        layout.addLayout(row)
        layout.addWidget(_ok_cancel(self))

    def _fill(self) -> None:
        self.list.clear()
        for step in self.steps:
            details = {k: v for k, v in step.items() if k != "op"}
            self.list.addItem(f"{step['op']}  {json.dumps(details, ensure_ascii=False)[:160]}")

    def _remove(self) -> None:
        for row in sorted({self.list.row(i) for i in self.list.selectedItems()}, reverse=True):
            del self.steps[row]
        self._fill()

    def _clear(self) -> None:
        self.steps = []
        self._fill()


class TemplateParamsDialog(QDialog):
    """Parameter einer Vorlage vor dem Anwenden anpassen (eine Zeile je Parameter)."""

    def __init__(self, tr: Translator, template: Template, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("template_params", template.name or ""))
        self.edits: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for name, value in template.params.items():
            edit = QLineEdit(json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value)
            form.addRow(name, edit)
            self.edits[name] = edit
        layout = QVBoxLayout(self)
        if not self.edits:
            layout.addWidget(QLabel(tr("no_params")))
        layout.addLayout(form)
        layout.addWidget(_ok_cancel(self))

    def values(self) -> dict:
        return dict(parse_param(f"{name}={edit.text()}") for name, edit in self.edits.items())


class HotfolderDialog(QDialog):
    """Hotfolder im Programm laufen lassen (Abfrage per Timer)."""

    def __init__(self, tr: Translator, printers: list[printing.PrinterProfile], catalog, parent=None) -> None:
        super().__init__(parent)
        self.tr_ = tr
        self.catalog = catalog
        self.setWindowTitle(tr("hotfolder_title"))
        self.resize(720, 460)
        self.template = PathEdit(tr, filter_key="template_filter")
        self.inbox = PathEdit(tr, directory=True)
        self.outbox = PathEdit(tr, directory=True)
        self.printer = QComboBox()
        self.printer.addItem("–", None)
        for profile in printers:
            self.printer.addItem(profile.name, profile)
        form = QFormLayout()
        form.addRow(tr("template"), self.template)
        form.addRow(tr("hotfolder_in"), self.inbox)
        form.addRow(tr("hotfolder_out"), self.outbox)
        form.addRow(tr("send_to_printer"), self.printer)
        self.start_btn = QPushButton(tr("start"), clicked=self.toggle)
        self.log = QPlainTextEdit(readOnly=True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.start_btn)
        layout.addWidget(self.log)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        layout.addWidget(box)
        self.folder: Hotfolder | None = None
        self.timer = QTimer(self, interval=2000, timeout=self.poll)

    def toggle(self) -> None:
        if self.folder is not None:
            self.timer.stop()
            self.folder = None
            self.start_btn.setText(self.tr_("start"))
            self.log.appendPlainText(self.tr_("hotfolder_stopped"))
            return
        try:
            template = Template.load(self.template.text())
            profile = self.printer.currentData()

            def after(path, result) -> None:
                if profile is not None:
                    from ..core.pdfdoc import PdfDocument

                    printing.send(profile, PdfDocument.open(result.pdf), result.ticket, name=path.stem)

            self.folder = Hotfolder(Path(self.inbox.text()), Path(self.outbox.text()), template,
                                    catalog=self.catalog, after=after)
        except Exception as exc:
            self.log.appendPlainText(f"{self.tr_('error')}: {exc}")
            return
        self.start_btn.setText(self.tr_("stop"))
        self.log.appendPlainText(self.tr_("hotfolder_started", self.inbox.text()))
        self.timer.start()

    def poll(self) -> None:
        if self.folder is None:
            return
        for event in self.folder.poll():
            self.log.appendPlainText(event.line())

    def reject(self) -> None:
        self.timer.stop()
        super().reject()


_PRINTER_COLUMNS = ["col_name", "printer_kind", "printer_target", "printer_payload", "printer_raster"]


class PrinterProfilesDialog(QDialog):
    def __init__(self, tr: Translator, profiles: list[printing.PrinterProfile], system_printers: list[str],
                 parent=None) -> None:
        super().__init__(parent)
        self.tr_ = tr
        self.setWindowTitle(tr("printer_profiles"))
        self.resize(820, 340)
        self.system_printers = system_printers
        self.table = QTableWidget(0, len(_PRINTER_COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in _PRINTER_COLUMNS])
        self.table.horizontalHeader().setStretchLastSection(True)
        for profile in profiles:
            self._append(profile)
        add = QPushButton(tr("add"), clicked=lambda: self._append(printing.PrinterProfile(tr("new_printer"))))
        remove = QPushButton(tr("remove"), clicked=self._remove)
        hint = QLabel(tr("printer_target_hint"))
        hint.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(hint)
        layout.addLayout(row)
        layout.addWidget(_ok_cancel(self))

    def _combo(self, enum, value, prefix: str) -> QComboBox:
        combo = QComboBox()
        for member in enum:
            combo.addItem(self.tr_(f"{prefix}_{member.value}"), member)
        combo.setCurrentIndex(list(enum).index(value))
        return combo

    def _append(self, profile: printing.PrinterProfile) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(profile.name))
        self.table.setCellWidget(row, 1, self._combo(printing.PrinterKind, profile.kind, "kind"))
        target = QComboBox(editable=True)
        target.addItems(self.system_printers)
        target.setCurrentText(profile.target)
        self.table.setCellWidget(row, 2, target)
        self.table.setCellWidget(row, 3, self._combo(printing.Payload, profile.payload, "payload"))
        raster = QTableWidgetItem()
        raster.setFlags(raster.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        raster.setCheckState(Qt.CheckState.Checked if profile.raster else Qt.CheckState.Unchecked)
        self.table.setItem(row, 4, raster)

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def profiles(self) -> list[printing.PrinterProfile]:
        result = []
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text().strip() if self.table.item(row, 0) else ""
            if not name:
                continue
            result.append(printing.PrinterProfile(
                name=name, kind=self.table.cellWidget(row, 1).currentData(),
                target=self.table.cellWidget(row, 2).currentText().strip(),
                payload=self.table.cellWidget(row, 3).currentData(),
                raster=self.table.item(row, 4).checkState() == Qt.CheckState.Checked,
            ))
        return result


class SendDialog(QDialog):
    def __init__(self, tr: Translator, profiles: list[printing.PrinterProfile], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("send_to_printer"))
        self.printer = QComboBox()
        for profile in profiles:
            self.printer.addItem(f"{profile.name} ({tr('kind_' + profile.kind.value)})", profile)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow(tr("printer"), self.printer)
        layout.addLayout(form)
        layout.addWidget(_ok_cancel(self))

    def profile(self) -> printing.PrinterProfile | None:
        return self.printer.currentData()

