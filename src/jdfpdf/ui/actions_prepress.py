"""Menü „Prüfen“: Preflight, Farbzonen (PPF) und Maschinenprofile."""

from __future__ import annotations

import json
from dataclasses import asdict, fields
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel

from ..core import ppf
from ..core.preflight import PreflightProfile, preflight
from .dialogs_prepress import InkZonesDialog, PreflightDialog, PreflightSettingsDialog, PressProfilesDialog


class PrepressActions:
    """Mixin für ``MainWindow``."""

    press_profiles_path: Path | None = None

    def _build_prepress_menu(self, action) -> list:
        tr = self.tr_
        self.act_preflight = action("run_preflight", self.run_preflight, "F7")
        self.act_preflight_settings = action("preflight_settings", self.preflight_settings)
        self.act_ink_zones = action("ink_zones_menu", self.show_ink_zones)
        self.act_press_profiles = action("press_profiles", self.edit_press_profiles)
        menu = tr.bind(self.menuBar().addMenu(""), "check_menu", "setTitle")
        menu.addActions([self.act_preflight, self.act_preflight_settings])
        menu.addSeparator()
        menu.addActions([self.act_ink_zones, self.act_press_profiles])
        self.output.zones_requested.connect(self.show_ink_zones)
        return [self.act_preflight, self.act_ink_zones]

    def preflight_profile(self) -> PreflightProfile:
        try:
            data = json.loads(str(self.settings.value("preflight_profile", "{}")))
        except ValueError:
            data = {}
        known = {f.name for f in fields(PreflightProfile)}
        return PreflightProfile(**{k: v for k, v in data.items() if k in known})

    def preflight_settings(self) -> None:
        dialog = PreflightSettingsDialog(self.tr_, self.preflight_profile(), self)
        if dialog.exec():
            self.settings.setValue("preflight_profile", json.dumps(asdict(dialog.profile())))

    def run_preflight(self):
        if self.doc is None:
            return None
        report = preflight(self.doc, self.preflight_profile())
        dialog = PreflightDialog(self.tr_, report, self)
        dialog.pages_selected.connect(self._select_pages)
        dialog.show()
        self._preflight_dialog = dialog  # Referenz halten (nicht modal)
        return dialog

    def _select_pages(self, pages: list[int]) -> None:
        if self.sheet_mode or not pages:
            return
        self.pages.setCurrentRow(pages[0], QItemSelectionModel.SelectionFlag.NoUpdate)
        self.pages.clearSelection()
        for index in pages:
            if index < self.pages.count():
                self.pages.item(index).setSelected(True)

    def show_ink_zones(self):
        doc = self.view_doc or self.doc
        if doc is None:
            return None
        profile = self.output.press_profile() or ppf.default_profiles()[0]
        index = min(max(self.pages.currentRow(), 0), doc.page_count - 1)
        result = {}

        def run() -> None:
            result["side"] = ppf.analyse_side(doc, index, profile)

        if not self._guard(run):
            return None
        dialog = InkZonesDialog(self.tr_, result["side"], profile, self)
        dialog.show()
        self._zones_dialog = dialog
        return dialog

    def edit_press_profiles(self) -> None:
        dialog = PressProfilesDialog(self.tr_, ppf.load_profiles(self.press_profiles_path), self)
        if dialog.exec():
            profiles = dialog.profiles()
            if self._guard(lambda: ppf.save_profiles(profiles, self.press_profiles_path)):
                self.output.set_profiles(profiles)
