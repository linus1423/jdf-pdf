"""Dialoge für Farbe: Farb-/s/w-Split, Bildkorrektur, Sonderfarben, Farbbibliothek."""

from __future__ import annotations

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core import imagefix, spot
from ..core.pagerange import format_pages
from ..core.pdfdoc import PdfDocument
from ..core.pdfimage import load_image
from .dialogs import _Form
from .i18n import Translator


def _buttons(dialog: QDialog) -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class SplitDialog(_Form):
    def __init__(self, tr: Translator, color_pages: list[int], duplex: bool, parent=None) -> None:
        super().__init__(tr, "split_title", parent)
        self.pages = self.row("color_pages", QLineEdit(format_pages(color_pages)))
        self.pages.setToolTip(tr("page_range_hint"))
        self.duplex = self.row("split_duplex", QCheckBox(checked=duplex))
        self.gray = self.row("split_gray", QCheckBox(checked=True))


class _Slider(QHBoxLayout):
    def __init__(self, minimum: int, maximum: int, value: int, on_change) -> None:
        super().__init__()
        self.slider = QSlider(Qt.Orientation.Horizontal, minimum=minimum, maximum=maximum, value=value)
        self.label = QLabel()
        self.label.setMinimumWidth(48)
        self.slider.valueChanged.connect(lambda v: (self.label.setText(f"{v:+d} %"), on_change()))
        self.label.setText(f"{value:+d} %")
        self.addWidget(self.slider, 1)
        self.addWidget(self.label)

    def value(self) -> int:
        return self.slider.value()


class ImageAdjustDialog(QDialog):
    """Bilder der aktuellen Seite wählen und Helligkeit/Kontrast/Sättigung einstellen (mit Vorschau)."""

    def __init__(self, tr: Translator, doc: PdfDocument, index: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("imagefix_title"))
        self.doc, self.index = doc, index
        self.list = QListWidget()
        for info in imagefix.page_images(doc, index):
            item = QListWidgetItem(f"{info.path} · {info.width}×{info.height} · {info.colorspace}")
            item.setData(Qt.ItemDataRole.UserRole, info.path)
            if info.editable:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip(tr("image_not_editable"))
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._preview)
        self.before, self.after = QLabel(), QLabel()
        for label in (self.before, self.after):
            label.setFixedSize(220, 220)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brightness = _Slider(-100, 100, 0, self._preview)
        self.contrast = _Slider(-100, 100, 0, self._preview)
        self.saturation = _Slider(-100, 100, 0, self._preview)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list)
        previews = QHBoxLayout()
        previews.addWidget(self.before)
        previews.addWidget(self.after)
        layout.addLayout(previews)
        for key, slider in (("brightness", self.brightness), ("contrast", self.contrast),
                            ("saturation", self.saturation)):
            layout.addWidget(QLabel(tr(key)))
            layout.addLayout(slider)
        if self.list.count() == 0:
            layout.addWidget(QLabel(tr("no_images")))
        layout.addWidget(_buttons(self))
        self._images = {path: obj for path, obj in imagefix._iter_images(doc.pdf.pages[index].obj.get("/Resources"))}
        if self.list.count():
            self.list.setCurrentRow(0)

    def adjustment(self) -> imagefix.Adjustment:
        return imagefix.Adjustment(1 + self.brightness.value() / 100, 1 + self.contrast.value() / 100,
                                   1 + self.saturation.value() / 100)

    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]

    def _preview(self, *_):
        item = self.list.currentItem()
        obj = self._images.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        image = load_image(obj) if obj is not None else None
        if image is None:
            self.before.clear()
            self.after.clear()
            return
        image.thumbnail((220, 220))
        for label, img in ((self.before, image), (self.after, imagefix.adjust(image, self.adjustment()))):
            label.setPixmap(QPixmap.fromImage(ImageQt(img.convert("RGB"))))


_CMYK = ("C", "M", "Y", "K")


def _cmyk_cells(table: QTableWidget, row: int, start: int, cmyk) -> None:
    for j in range(4):
        table.setItem(row, start + j, QTableWidgetItem("" if cmyk is None else f"{cmyk[j]:g}"))


def _read_cmyk(table: QTableWidget, row: int, start: int):
    try:
        return tuple(float(table.item(row, start + j).text().replace(",", ".")) for j in range(4))
    except (AttributeError, ValueError):
        return None


class SpotColorsDialog(QDialog):
    """Sonderfarben des Dokuments: umbenennen, CMYK-Ersatz ändern, zusammenführen, Bibliothek."""

    def __init__(self, tr: Translator, doc: PdfDocument, library: spot.SpotLibrary, parent=None) -> None:
        super().__init__(parent)
        self.tr_ = tr
        self.library = library
        self.setWindowTitle(tr("spot_title"))
        self.resize(720, 380)
        self.colors = spot.spot_colors(doc)
        self.table = QTableWidget(len(self.colors), 7)
        self.table.setHorizontalHeaderLabels([tr("col_name"), *_CMYK, tr("alternate"), tr("uses")])
        for row, color in enumerate(self.colors):
            self.table.setItem(row, 0, QTableWidgetItem(color.name))
            _cmyk_cells(self.table, row, 1, color.cmyk)
            for col, text in ((5, color.alternate + (" / DeviceN" if color.in_devicen else "")), (6, str(color.uses))):
                cell = QTableWidgetItem(text)
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, cell)
        self.merge_target = QComboBox()
        self.merge_target.addItems([c.name for c in self.colors])
        merge = QPushButton(tr("spot_merge"), clicked=self._merge)
        from_lib = QPushButton(tr("spot_from_library"), clicked=self._from_library)
        to_lib = QPushButton(tr("spot_to_library"), clicked=self._to_library)
        row = QHBoxLayout()
        row.addWidget(merge)
        row.addWidget(self.merge_target)
        row.addStretch()
        row.addWidget(from_lib)
        row.addWidget(to_lib)
        layout = QVBoxLayout(self)
        if not self.colors:
            layout.addWidget(QLabel(tr("no_spots")))
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(_buttons(self))
        self.merges: list[tuple[str, str]] = []
        self.library_changed = False

    def _merge(self) -> None:
        target = self.merge_target.currentText()
        rows = sorted({i.row() for i in self.table.selectedItems()})
        for row in rows:
            source = self.colors[row].name
            if source != target:
                self.merges.append((source, target))
                self.table.item(row, 0).setText(target)
                _cmyk_cells(self.table, row, 1, next((c.cmyk for c in self.colors if c.name == target), None))

    def _from_library(self) -> None:
        for row in range(self.table.rowCount()):
            entry = self.library.get(self.table.item(row, 0).text())
            if entry is not None and entry.cmyk is not None:
                _cmyk_cells(self.table, row, 1, entry.cmyk)

    def _to_library(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedItems()}) or range(self.table.rowCount())
        for row in rows:
            cmyk = _read_cmyk(self.table, row, 1)
            if cmyk is not None:
                self.library.add(spot.LibraryColor(self.table.item(row, 0).text(), cmyk))
                self.library_changed = True
        QMessageBox.information(self, self.tr_("spot_title"), self.tr_("spot_saved_library"))

    def apply(self, doc: PdfDocument) -> None:
        """Änderungen der Tabelle auf das Dokument anwenden."""
        merged = {source for source, _ in self.merges}
        for source, target in self.merges:
            spot.merge_spots(doc, source, target)
        for row, color in enumerate(self.colors):
            if color.name in merged:
                continue
            name = self.table.item(row, 0).text().strip()
            if name and name != color.name:
                spot.rename_spot(doc, color.name, name)
            cmyk = _read_cmyk(self.table, row, 1)
            if cmyk is not None and cmyk != color.cmyk and not color.in_devicen:
                spot.set_spot_alternate(doc, name or color.name, cmyk=cmyk)


class SpotLibraryDialog(QDialog):
    """Eigene Farbbibliothek (Name, CMYK, Lab) bearbeiten."""

    def __init__(self, tr: Translator, library: spot.SpotLibrary, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.setWindowTitle(tr("spot_library_title"))
        self.resize(640, 380)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([tr("col_name"), *_CMYK, "L*", "a*", "b*"])
        for color in library.colors:
            self._append(color)
        add = QPushButton(tr("add"), clicked=lambda: self._append(spot.LibraryColor(tr("new_color"), (0, 0, 0, 100))))
        remove = QPushButton(tr("remove"), clicked=self._remove)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch()
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(row)
        layout.addWidget(_buttons(self))

    def _append(self, color: spot.LibraryColor) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(color.name))
        _cmyk_cells(self.table, row, 1, color.cmyk)
        for j in range(3):
            self.table.setItem(row, 5 + j, QTableWidgetItem("" if color.lab is None else f"{color.lab[j]:g}"))

    def _remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedItems()}, reverse=True):
            self.table.removeRow(row)

    def accept(self) -> None:
        colors = []
        for row in range(self.table.rowCount()):
            name = (self.table.item(row, 0).text() if self.table.item(row, 0) else "").strip()
            if not name:
                continue
            try:
                lab = tuple(float(self.table.item(row, 5 + j).text().replace(",", ".")) for j in range(3))
            except (AttributeError, ValueError):
                lab = None
            colors.append(spot.LibraryColor(name, _read_cmyk(self.table, row, 1), lab))
        self.library.colors = sorted(colors, key=lambda c: c.name.lower())
        super().accept()
