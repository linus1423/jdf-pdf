"""Menü „Werkzeuge“: Scannen, Bereinigen, Rastern, Text, Serienbrief, externe Programme, Softproof."""

from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ..core import cleanup, external, scan, textedit, vdp
from ..core.impose import Layout
from ..core.pagerange import parse_pages
from ..core.template import step_dict
from .dialogs_tools import (
    CleanupDialog,
    EditorsDialog,
    ReplaceTextDialog,
    ScanDialog,
    SoftproofDialog,
    TextBlockDialog,
    VdpDialog,
)


class ToolsActions:
    """Mixin für ``MainWindow``."""

    editors_path: Path | None = None
    vdp_setup: vdp.VdpSetup | None = None

    def _build_tools_menu(self, action) -> list:
        tr = self.tr_
        self.act_scan = action("scan", self.scan_pages)
        self.act_cleanup = action("cleanup_pages", self.cleanup_pages)
        self.act_rasterize = action("rasterize_pages", self.rasterize_pages)
        self.act_text_block = action("add_text_block", self.add_text_block)
        self.act_replace_text = action("replace_text", self.replace_text, "Ctrl+H")
        self.act_remove_text = action("remove_text_edits", self.remove_text_edits)
        self.act_vdp = action("vdp", self.variable_data)
        self.act_editors = action("external_editors_menu", self.edit_external_editors)
        self.act_softproof = action("softproof", self.export_softproof)
        menu = tr.bind(self.menuBar().addMenu(""), "tools_menu", "setTitle")
        menu.addAction(self.act_scan)
        menu.addActions([self.act_cleanup, self.act_rasterize])
        menu.addSeparator()
        menu.addActions([self.act_text_block, self.act_replace_text, self.act_remove_text])
        menu.addSeparator()
        menu.addAction(self.act_vdp)
        menu.addSeparator()
        self.external_menu = tr.bind(menu.addMenu(""), "edit_externally", "setTitle")
        menu.addAction(self.act_editors)
        menu.addSeparator()
        menu.addAction(self.act_softproof)
        self._edit_sessions: list[external.EditSession] = []
        self._edit_timer = QTimer(self, interval=1000, timeout=self._poll_edits)
        self._rebuild_external_menu()
        return [self.act_cleanup, self.act_rasterize, self.act_text_block, self.act_replace_text,
                self.act_remove_text, self.act_vdp, self.act_softproof, self.external_menu.menuAction()]

    # --- Scannen und Bereinigen ------------------------------------------------

    def scan_pages(self) -> None:
        dialog = ScanDialog(self.tr_, self)
        dialog.refresh()
        if not dialog.exec():
            return
        folder = Path(tempfile.mkdtemp(prefix="jdfpdf-scan-"))
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            files = scan.scan(dialog.device(), dialog.options(), folder)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, self.tr_("error"), str(exc))
            return
        QApplication.restoreOverrideCursor()
        start = self.doc.page_count if self.doc is not None else 0
        self.insert_files(files, self._insert_position() if self.doc is not None else None)
        if dialog.cleanup.isChecked() and self.doc is not None:
            self._select_pages(list(range(start, start + len(files))))
            self.cleanup_pages()

    def _run_cleanup(self, rasterize_only: bool) -> None:
        if self.doc is None:
            return
        pages = self._scope("selection")
        index = max(self.pages.currentRow(), 0)
        dialog = CleanupDialog(self.tr_, self.doc, index, rasterize_only, self)
        if not dialog.exec():
            return
        opts = dialog.options()
        result = {}
        if rasterize_only:
            ok = self.modify(lambda: cleanup.rasterize_pages(self.doc, pages, opts.dpi, opts.mode),
                             step_dict("rasterize", pages=self._pages_spec(pages), dpi=opts.dpi, mode=opts.mode))
        else:
            def run() -> None:
                result["angles"] = cleanup.cleanup_pages(self.doc, pages, opts)

            ok = self.modify(run, step_dict("cleanup", pages=self._pages_spec(pages), options=opts))
        if ok:
            turned = sum(1 for a in result.get("angles", {}).values() if a)
            self.statusBar().showMessage(self.tr_("cleanup_done", len(pages), turned), 10000)

    def cleanup_pages(self) -> None:
        self._run_cleanup(False)

    def rasterize_pages(self) -> None:
        self._run_cleanup(True)

    # --- Text --------------------------------------------------------------------

    def add_text_block(self) -> None:
        if self.doc is None:
            return
        dialog = TextBlockDialog(self.tr_, self)
        if dialog.exec() and dialog.text.toPlainText().strip():
            pages = self._scope("selection")
            block = dialog.block()
            self.modify(lambda: textedit.add_text_block(self.doc, pages, block),
                        step_dict("text_block", pages=self._pages_spec(pages), block=block))

    def replace_text(self) -> None:
        if self.doc is None:
            return
        dialog = ReplaceTextDialog(self.tr_, self.doc, self)
        if not dialog.exec() or not dialog.find.text():
            return
        pages = self._scope("selection") if dialog.selection_only.isChecked() else None
        find, repl = dialog.find.text(), dialog.replace.text()
        rewrite = dialog.method.currentData() == "rewrite"
        style = dialog.style()
        case, word = dialog.match_case.isChecked(), dialog.whole_word.isChecked()
        result = {}

        def run() -> None:
            if rewrite:
                result["report"] = textedit.rewrite_text(self.doc, find, repl, pages)
            else:
                result["count"] = textedit.replace_text(self.doc, find, repl, pages, style, case, word)

        step = step_dict("replace_text", find=find, replace=repl, rewrite=rewrite, style=style, match_case=case,
                         whole_word=word, **({"pages": self._pages_spec(pages)} if pages is not None else {}))
        if not self.modify(run, step):
            return
        if rewrite:
            report = result["report"]
            message = self.tr_("rewrite_done", report.replaced, len(report.skipped))
            if report.skipped:
                QMessageBox.information(self, self.tr_("replace_text"), message + "\n\n" +
                                        "\n".join(report.skipped[:20]))
            self.statusBar().showMessage(message, 10000)
        else:
            self.statusBar().showMessage(self.tr_("replace_done", result["count"]), 10000)

    def remove_text_edits(self) -> None:
        if self.doc is None:
            return
        pages = self._scope("selection")
        self.modify(lambda: textedit.remove_text_edits(self.doc, pages),
                    step_dict("remove_text_edits", pages=self._pages_spec(pages)))

    # --- Serienbrief -------------------------------------------------------------

    def variable_data(self) -> None:
        if self.doc is None:
            return
        dialog = VdpDialog(self.tr_, self.doc, self.vdp_setup, self)
        if not dialog.exec():
            return
        setup = dialog.setup()
        self.vdp_setup = setup
        if dialog.data is None:
            dialog.load_data()
        data = dialog.data
        if data is None or not data.rows:
            QMessageBox.warning(self, self.tr_("vdp"), self.tr_("no_records"))
            return
        records = parse_pages(setup.records, len(data.rows)) if setup.records else None
        result = {}

        def run() -> None:
            result["n"] = vdp.merge_in_place(self.doc, data, setup.fields, records, setup.sections)

        if self.modify(run, {"op": "vdp", **asdict(setup)}):
            self.statusBar().showMessage(self.tr_("vdp_done", result["n"], self.doc.page_count), 10000)

    # --- Externe Programme -------------------------------------------------------

    def external_editors(self) -> list[external.ExternalEditor]:
        try:
            return external.load_editors(self.editors_path)
        except Exception:
            return []

    def _rebuild_external_menu(self) -> None:
        self.external_menu.clear()
        editors = self.external_editors()
        for editor in editors:
            self.external_menu.addAction(editor.name, lambda e=editor: self.edit_externally(e))
        if not editors:
            empty = self.external_menu.addAction(self.tr_("no_external_editors"))
            empty.setEnabled(False)

    def edit_external_editors(self) -> None:
        dialog = EditorsDialog(self.tr_, self.external_editors(), self)
        if dialog.exec():
            if self._guard(lambda: external.save_editors(dialog.editors(), self.editors_path)):
                self._rebuild_external_menu()

    def edit_externally(self, editor: external.ExternalEditor) -> None:
        if self.doc is None:
            return
        index = max(self.pages.currentRow(), 0)
        name = self.doc.path.stem if self.doc.path else "seite"
        try:
            session = external.EditSession(editor, self.doc, index, name)
            session.start()
        except Exception as exc:
            QMessageBox.critical(self, self.tr_("error"), str(exc))
            return
        self._edit_sessions.append(session)
        self._edit_timer.start()
        self.statusBar().showMessage(self.tr_("editing_externally", index + 1, editor.name), 10000)

    def _poll_edits(self) -> None:
        for session in list(self._edit_sessions):
            if session.poll() and self.doc is not None and session.index < self.doc.page_count:
                try:
                    edited = session.read()
                except Exception:  # Datei wird evtl. noch geschrieben
                    continue
                if self.modify(lambda s=session, e=edited: external.apply_edit(self.doc, s.index, e)):
                    self.statusBar().showMessage(self.tr_("page_updated", session.index + 1), 8000)

    def close_edit_sessions(self) -> None:
        """Beim Laden eines anderen Dokuments: Überwachung beenden, Zwischendateien löschen."""
        for session in self._edit_sessions:
            session.close()
        self._edit_sessions = []
        self._edit_timer.stop()

    # --- Softproof ---------------------------------------------------------------

    def export_softproof(self) -> None:
        if self.doc is None:
            return
        imposition = self.imposition()
        dialog = SoftproofDialog(self.tr_, imposition.layout != Layout.NONE, self)
        if not dialog.exec():
            return
        start = str(self.doc.path.with_name(self.doc.path.stem + "_proof.pdf")) if self.doc.path else ""
        path, _ = QFileDialog.getSaveFileName(self, self.tr_("softproof"), start, self.tr_("pdf_filter"))
        if not path:
            return
        from ..core.softproof import write_proof

        opts = dialog.options(self.tr_.language)
        if self._guard(lambda: write_proof(self.doc, self.ticket(), path, opts, imposition)):
            self.statusBar().showMessage(self.tr_("saved", Path(path).name), 8000)
