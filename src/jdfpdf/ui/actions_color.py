"""Menü „Farbe“ des Hauptfensters: Farbseiten, Graustufen, Split, Bildkorrektur, Sonderfarben."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..core import color, imagefix, spot
from ..core.jdf import Sides
from ..core.pagerange import format_pages, parse_pages
from ..core.template import step_dict
from .dialogs_color import ImageAdjustDialog, SplitDialog, SpotColorsDialog, SpotLibraryDialog


class ColorActions:
    """Mixin für ``MainWindow``; nutzt dessen ``doc``, ``modify``, ``pages``, ``tr_`` usw."""

    color_pages: list[int] | None = None
    spot_library_path: Path | None = None

    def _build_color_menu(self, action) -> list:
        tr = self.tr_
        self.act_detect_color = action("detect_color", self.detect_color)
        self.act_to_gray = action("to_gray", self.to_gray)
        self.act_split = action("color_split", self.color_split)
        self.act_imagefix = action("image_adjust", self.image_adjust)
        self.act_spots = action("spot_colors", self.spot_colors)
        self.act_spot_library = action("spot_library", self.spot_library)
        menu = tr.bind(self.menuBar().addMenu(""), "color_menu", "setTitle")
        menu.addActions([self.act_detect_color, self.act_to_gray, self.act_split])
        menu.addSeparator()
        menu.addAction(self.act_imagefix)
        menu.addSeparator()
        menu.addActions([self.act_spots, self.act_spot_library])
        return [self.act_detect_color, self.act_to_gray, self.act_split, self.act_imagefix, self.act_spots]

    def _spot_library(self) -> spot.SpotLibrary:
        return spot.SpotLibrary.load(self.spot_library_path)

    def detect_color(self) -> None:
        if self.doc is None:
            return
        pages = color.detect_color_pages(self.doc)
        self.color_pages = pages
        self.pages.clearSelection()
        for index in pages:
            self.pages.item(index).setSelected(True)
        self.statusBar().showMessage(
            self.tr_("color_pages_found", n=len(pages), total=self.doc.page_count,
                     pages=format_pages(pages) or "–"), 10000)

    def to_gray(self) -> None:
        if self.doc is None:
            return
        pages = self._scope("selection")
        result = {}

        def run() -> None:
            result["r"] = color.convert_to_gray(self.doc, pages)

        if self.modify(run, step_dict("gray", pages=self._pages_spec(pages))):
            report = result["r"]
            message = self.tr_("gray_done", pages=report.pages, images=report.images)
            if report.skipped:
                message += " " + self.tr_("gray_skipped", ", ".join(
                    f"{self.tr_('skip_' + k)}: {v}" for k, v in sorted(report.skipped.items())))
            self.statusBar().showMessage(message, 12000)
            self.color_pages = None

    def color_split(self) -> None:
        if self.doc is None:
            return
        if self.color_pages is None:
            self.color_pages = color.detect_color_pages(self.doc)
        duplex = self.job.sides.value() != Sides.SIMPLEX
        dialog = SplitDialog(self.tr_, self.color_pages, duplex, self)
        if not dialog.exec():
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("color_split"), str(self.doc.path or ""),
                                              self.tr_("pdf_filter"))
        if not path:
            return
        result = {}

        def run() -> None:
            pages = parse_pages(dialog.pages.text(), self.doc.page_count)
            result["r"] = color.write_split(self.doc, self.ticket(), Path(path), self.output.options(), pages,
                                            dialog.duplex.isChecked(), dialog.gray.isChecked())

        if self._guard(run):
            files = [p.name for r in result["r"] for p in [r.pdf, *r.extra_files]]
            self._show_warnings([w for r in result["r"] for w in r.warnings])
            self.statusBar().showMessage(self.tr_("written", ", ".join(files)), 10000)

    def image_adjust(self) -> None:
        if self.doc is None:
            return
        index = max(self.pages.currentRow(), 0)
        dialog = ImageAdjustDialog(self.tr_, self.doc, index, self)
        if dialog.exec():
            adj, paths = dialog.adjustment(), dialog.paths()
            self.modify(lambda: imagefix.adjust_images(self.doc, index, adj, paths),
                        step_dict("image_adjust", adjust=adj, pages=str(index + 1), images=paths))

    def spot_colors(self) -> None:
        if self.doc is None:
            return
        library = self._spot_library()
        dialog = SpotColorsDialog(self.tr_, self.doc, library, self)
        if dialog.exec():
            self.modify(lambda: dialog.apply(self.doc), dialog.steps())
        if dialog.library_changed:
            self._guard(lambda: library.save(self.spot_library_path))

    def spot_library(self) -> None:
        library = self._spot_library()
        dialog = SpotLibraryDialog(self.tr_, library, self)
        if dialog.exec():
            self._guard(lambda: library.save(self.spot_library_path))
            if self.doc is not None and spot.spot_colors(self.doc):
                answer = QMessageBox.question(self, self.tr_("spot_library"), self.tr_("spot_apply_library"))
                if answer == QMessageBox.StandardButton.Yes:
                    self.modify(lambda: library.apply(self.doc),
                                step_dict("spot_library", path=str(self.spot_library_path or "") or None))
