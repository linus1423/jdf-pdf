"""Dialoge für Seitenelemente, Register, Rückentitel und Druckmarken."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QWidget,
)

from ..core import elements as el
from ..core.marks import BarcodeType, MarkOptions
from ..core.tabs import BleedTabStyle, TabSheetStyle
from .dialogs import _Form, _mm_spin, _scope_combo
from .i18n import Translator
from .widgets import EnumCombo


class CmykEdit(QWidget):
    def __init__(self, value=(0, 0, 0, 100)) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.spins = []
        for letter, v in zip("CMYK", value):
            spin = QSpinBox(minimum=0, maximum=100, suffix=" %", prefix=f"{letter} ")
            spin.setValue(int(v))
            row.addWidget(spin)
            self.spins.append(spin)

    def value(self) -> tuple[float, float, float, float]:
        return tuple(float(s.value()) for s in self.spins)

    def set_value(self, value) -> None:
        for spin, v in zip(self.spins, value):
            spin.setValue(int(v))


class FileEdit(QWidget):
    def __init__(self, tr: Translator, filter_key: str) -> None:
        super().__init__()
        self.edit = QLineEdit()
        button = QPushButton("…", clicked=self._browse)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.edit, 1)
        row.addWidget(button)
        self._tr, self._filter = tr, filter_key

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "", "", self._tr(self._filter))
        if path:
            self.edit.setText(path)

    def path(self) -> str | None:
        text = self.edit.text().strip()
        return text if text and Path(text).exists() else None


PRESETS = ["preset_footer", "preset_header", "preset_page_numbers", "preset_watermark", "preset_stamp",
           "preset_text", "preset_image"]


class ElementDialog(_Form):
    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(tr, "element_title", parent)
        self.preset = self.row("preset", QComboBox())
        for key in PRESETS:
            self.preset.addItem(tr(key), key)
        self.text = self.row("element_text", QLineEdit("{page} / {pages}"))
        self.text.setToolTip(tr("placeholders_hint"))
        self.image = self.row("element_image", FileEdit(tr, "image_filter"))
        self.anchor = self.row("anchor", EnumCombo(el.Anchor, tr, "anchor"))
        self.dx = self.row("offset_x", _mm_spin(0))
        self.dy = self.row("offset_y", _mm_spin(10))
        self.size = self.row("font_size", QDoubleSpinBox(minimum=2, maximum=300, value=10))
        self.bold = self.row("bold", QCheckBox())
        self.color = self.row("color", CmykEdit())
        self.opacity = self.row("opacity", QSpinBox(minimum=5, maximum=100, value=100, suffix=" %"))
        self.rotation = self.row("rotation", QDoubleSpinBox(minimum=-180, maximum=180, suffix=" °"))
        self.image_width = self.row("image_width", _mm_spin(30, 1, 1000))
        self.mirror = self.row("mirror_even", QCheckBox())
        self.scope = self.row("scope", _scope_combo(tr))
        self.preset.currentIndexChanged.connect(self._apply_preset)
        self._apply_preset()

    def _apply_preset(self) -> None:
        key = self.preset.currentData()
        factory = {
            "preset_footer": lambda: el.footer("{file} · {page}/{pages}"),
            "preset_header": lambda: el.header("{section}"),
            "preset_page_numbers": lambda: el.page_numbers(),
            "preset_watermark": lambda: el.watermark(self.tr_("watermark_default")),
            "preset_stamp": lambda: el.stamp(self.tr_("stamp_default")),
            "preset_text": lambda: el.Element(text="", anchor=el.Anchor.CENTER, offset_y_mm=0),
            "preset_image": lambda: el.image_element("", el.Anchor.TOP_LEFT, 30, offset_x_mm=10),
        }[key]
        self.set_element(factory())
        self.image.setVisible(key == "preset_image")
        self.text.setVisible(key != "preset_image")

    def set_element(self, e: el.Element) -> None:
        self.text.setText(e.text)
        self.anchor.set_value(e.anchor)
        self.dx.setValue(e.offset_x_mm)
        self.dy.setValue(e.offset_y_mm)
        self.size.setValue(e.font_size)
        self.bold.setChecked(e.bold)
        self.color.set_value(e.color_cmyk)
        self.opacity.setValue(int(e.opacity * 100))
        self.rotation.setValue(e.rotation)
        self.image_width.setValue(e.image_width_mm)
        self.mirror.setChecked(e.mirror_even)

    def element(self) -> el.Element:
        image = self.image.path() if self.preset.currentData() == "preset_image" else None
        return el.Element(
            text=self.text.text(), image=image, anchor=self.anchor.value(), offset_x_mm=self.dx.value(),
            offset_y_mm=self.dy.value(), font_size=self.size.value(), bold=self.bold.isChecked(),
            color_cmyk=self.color.value(), opacity=self.opacity.value() / 100, rotation=self.rotation.value(),
            image_width_mm=self.image_width.value(), mirror_even=self.mirror.isChecked(),
        )


class TabSheetDialog(_Form):
    def __init__(self, tr: Translator, media_names: list[str], parent=None) -> None:
        super().__init__(tr, "tabsheet_title", parent)
        self.count = self.row("tabs_per_set", QSpinBox(minimum=1, maximum=50, value=5))
        self.extension = self.row("tab_extension", _mm_spin(12.7, 1, 50))
        self.size = self.row("font_size", QDoubleSpinBox(minimum=4, maximum=30, value=9))
        self.double = self.row("double_sided", QCheckBox())
        self.titles = self.row("titles_file", FileEdit(tr, "text_filter"))
        self.titles.setToolTip(tr("titles_hint"))
        self.image = self.row("element_image", FileEdit(tr, "image_filter"))
        self.media = self.row("tabsheet_media", QComboBox())
        self.media.addItem("–", None)
        for name in media_names:
            self.media.addItem(name, name)
        tab_index = next((i for i, n in enumerate(media_names) if "regist" in n.lower() or "tab" in n.lower()), -1)
        self.media.setCurrentIndex(tab_index + 1)

    def style(self) -> TabSheetStyle:
        return TabSheetStyle(tab_count=self.count.value(), extension_mm=self.extension.value(),
                             font_size=self.size.value(), double_sided=self.double.isChecked(),
                             image=self.image.path())


class BleedTabDialog(_Form):
    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(tr, "bleedtab_title", parent)
        self.count = self.row("tabs_per_set", QSpinBox(minimum=1, maximum=50, value=6))
        self.width = self.row("tab_width", _mm_spin(8, 1, 50))
        self.bleed = self.row("bleed", _mm_spin(3, 0, 20))
        self.rounded = self.row("rounded", QCheckBox())
        self.show_text = self.row("show_text", QCheckBox(checked=True))
        self.duplex = self.row("duplex_mirror", QCheckBox(checked=True))
        self.mono = self.row("single_color", QCheckBox())
        self.color = self.row("color", CmykEdit((100, 0, 0, 0)))

    def style(self) -> BleedTabStyle:
        style = BleedTabStyle(tab_count=self.count.value(), width_mm=self.width.value(),
                              bleed_mm=self.bleed.value(), rounded=self.rounded.isChecked(),
                              show_text=self.show_text.isChecked(), duplex=self.duplex.isChecked())
        if self.mono.isChecked():
            style.colors = [self.color.value()]
        return style


class SpineDialog(_Form):
    def __init__(self, tr: Translator, spine_mm: float, parent=None) -> None:
        super().__init__(tr, "spine_title", parent)
        self.text = self.row("element_text", QLineEdit())
        self.width = self.row("spine_width", _mm_spin(spine_mm, 0.5, 200))
        self.size = self.row("font_size", QDoubleSpinBox(minimum=0, maximum=100, value=0, specialValueText="auto"))
        self.direction = self.row("reading_direction", QComboBox())
        self.direction.addItem(tr("top_to_bottom"), True)
        self.direction.addItem(tr("bottom_to_top"), False)
        self.color = self.row("color", CmykEdit())
        self.background = self.row("spine_background", QCheckBox())
        self.bg_color = self.row("background_color", CmykEdit((0, 0, 0, 0)))


class MarksDialog(_Form):
    def __init__(self, tr: Translator, parent=None) -> None:
        super().__init__(tr, "marks_title", parent)
        o = MarkOptions()
        self.crop = self.row("mark_crop", QCheckBox(checked=o.crop))
        self.bleed = self.row("mark_bleed", QCheckBox(checked=o.bleed))
        self.registration = self.row("mark_registration", QCheckBox(checked=o.registration))
        self.color_bar = self.row("mark_color_bar", QCheckBox(checked=o.color_bar))
        self.info = self.row("mark_info", QCheckBox(checked=o.info))
        self.folds = self.row("mark_folds", QLineEdit())
        self.folds.setPlaceholderText("105; 210")
        self.margin = self.row("mark_margin", _mm_spin(o.margin_mm, 3, 50))
        self.offset = self.row("mark_offset", _mm_spin(o.offset_mm, 0, 20))
        self.length = self.row("mark_length", _mm_spin(o.length_mm, 1, 20))
        self.barcode = self.row("barcode", EnumCombo(BarcodeType, tr, "barcode"))
        self.barcode_text = self.row("barcode_text", QLineEdit(o.barcode_text))
        self.scope = self.row("scope", _scope_combo(tr))

    def options(self) -> MarkOptions:
        folds = []
        for part in self.folds.text().replace(",", ".").split(";"):
            try:
                folds.append(float(part))
            except ValueError:
                pass
        return MarkOptions(
            crop=self.crop.isChecked(), bleed=self.bleed.isChecked(), registration=self.registration.isChecked(),
            color_bar=self.color_bar.isChecked(), info=self.info.isChecked(), fold_x_mm=folds,
            margin_mm=self.margin.value(), offset_mm=self.offset.value(), length_mm=self.length.value(),
            barcode=self.barcode.value(), barcode_text=self.barcode_text.text(),
        )
