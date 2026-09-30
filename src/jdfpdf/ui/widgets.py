"""Kleine wiederverwendbare Widgets."""

from __future__ import annotations

from enum import Enum

from PySide6.QtWidgets import QComboBox

from .i18n import Translator


class EnumCombo(QComboBox):
    """Auswahlliste für ein Enum; Beschriftung über ``<prefix>_<name>`` bzw. ``<name>``."""

    def __init__(self, enum: type[Enum], tr: Translator, prefix: str = "") -> None:
        super().__init__()
        self._enum = enum
        self._tr = tr
        self._prefix = prefix
        # Enum-Mitglieder nicht als itemData ablegen: Qt wandelt str-Enums in str um.
        self._members = list(enum)
        for _ in self._members:
            self.addItem("")
        self.retranslate()
        tr.bind(self, "", setter="_retranslate_ignore")

    def _retranslate_ignore(self, _text: str) -> None:
        self.retranslate()

    def retranslate(self) -> None:
        for index, member in enumerate(self._members):
            key = f"{self._prefix}_{member.name.lower()}" if self._prefix else member.name.lower()
            self.setItemText(index, self._tr(key))

    def value(self) -> Enum:
        return self._members[max(self.currentIndex(), 0)]

    def set_value(self, member: Enum) -> None:
        self.setCurrentIndex(self._members.index(member))
