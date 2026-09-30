"""Menü „Automatisierung“: Vorlagen, Hotfolder, Druckerprofile, Senden und JMF-Warteschlange."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from ..core import jmf, printing
from ..core.template import SUFFIX, Template, apply_template, run_template
from .dialogs_automation import HotfolderDialog, PrinterProfilesDialog, SendDialog, StepsDialog, TemplateParamsDialog


class AutomationActions:
    """Mixin für ``MainWindow``."""

    printers_path: Path | None = None

    def _build_automation_menu(self, action) -> list:
        tr = self.tr_
        self.act_save_template = action("save_template", self.save_template)
        self.act_apply_template = action("apply_template", self.apply_template_to_doc)
        self.act_run_template = action("run_template", self.run_template_on_files)
        self.act_steps = action("recorded_steps", self.edit_steps)
        self.act_hotfolder = action("hotfolder", self.open_hotfolder)
        self.act_send = action("send_to_printer_menu", self.send_to_printer, "Ctrl+P")
        self.act_printers = action("printer_profiles_menu", self.edit_printers)
        self.act_queue = action("jmf_queue", self.show_queue)
        menu = tr.bind(self.menuBar().addMenu(""), "automation_menu", "setTitle")
        menu.addActions([self.act_save_template, self.act_apply_template, self.act_run_template, self.act_steps])
        menu.addSeparator()
        menu.addAction(self.act_hotfolder)
        menu.addSeparator()
        menu.addActions([self.act_send, self.act_printers, self.act_queue])
        return [self.act_apply_template, self.act_send]

    def printers(self) -> list[printing.PrinterProfile]:
        return printing.load_printers(self.printers_path)

    # --- Vorlagen ------------------------------------------------------------

    def current_template(self, name: str = "") -> Template:
        return Template(name=name, steps=list(self.steps), ticket=self.ticket(), output=self.output.options(),
                        imposition=self.imposition())

    def save_template(self, path: str | None = None) -> Path | None:
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, self.tr_("save_template"), "", self.tr_("template_filter"))
        if not path:
            return None
        path = Path(path)
        if path.suffix != SUFFIX:
            path = path.with_suffix(SUFFIX)
        template = self.current_template(path.stem)
        # der Auftragsname kommt beim Anwenden aus dem Dateinamen
        template.ticket.job_name = "${file}" if not template.ticket.job_name else template.ticket.job_name
        template.ticket.media_ranges = []
        self._guard(lambda: template.save(path))
        self.statusBar().showMessage(self.tr_("saved", path.name), 6000)
        return path

    def _ask_template(self) -> tuple[Template, Path, dict] | None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("apply_template"), "", self.tr_("template_filter"))
        if not path:
            return None
        template = Template.load(path)
        overrides = {}
        if template.params:
            dialog = TemplateParamsDialog(self.tr_, template, self)
            if not dialog.exec():
                return None
            overrides = dialog.values()
        return template, Path(path), overrides

    def apply_template_to_doc(self, template: Template | None = None, overrides: dict | None = None,
                              base_dir: Path | None = None) -> bool:
        if self.doc is None:
            return False
        if template is None:
            chosen = self._ask_template()
            if chosen is None:
                return False
            template, path, overrides = chosen
            base_dir = path.parent
        result = {}

        def run() -> None:
            result["r"] = apply_template(self.doc, template, self.doc.path, overrides, self.catalog, base_dir)

        if not self.modify(run, list(template.steps)):
            return False
        ticket, output, imposition, _ctx = result["r"]
        if template.ticket is not None:
            self.apply_ticket(ticket)
        if template.output is not None:
            self.output.set_options(output)
        if imposition is not None:
            self.layout_panel.set_imposition(imposition)
        return True

    def run_template_on_files(self) -> None:
        chosen = self._ask_template()
        if chosen is None:
            return
        template, path, overrides = chosen
        files, _ = QFileDialog.getOpenFileNames(self, self.tr_("run_template"), "", self.tr_("input_filter"))
        if not files:
            return
        out = QFileDialog.getExistingDirectory(self, self.tr_("choose_output"))
        if not out:
            return
        ok, failed = 0, []
        for file in files:
            try:
                run_template(Path(file), Path(out), template, overrides, self.catalog, path.parent)
                ok += 1
            except Exception as exc:
                failed.append(f"{Path(file).name}: {exc}")
        message = self.tr_("batch_done", ok=ok, failed=len(failed))
        if failed:
            message += "\n\n" + "\n".join(failed)
        QMessageBox.information(self, self.tr_("run_template"), message)

    def edit_steps(self) -> None:
        dialog = StepsDialog(self.tr_, self.steps, self)
        if dialog.exec():
            self.steps = dialog.steps

    # --- Hotfolder und Drucker -------------------------------------------------

    def open_hotfolder(self):
        dialog = HotfolderDialog(self.tr_, self.printers(), self.catalog, self)
        dialog.show()
        self._hotfolder_dialog = dialog
        return dialog

    def edit_printers(self) -> None:
        dialog = PrinterProfilesDialog(self.tr_, self.printers(), printing.list_system_printers(), self)
        if dialog.exec():
            profiles = dialog.profiles()
            self._guard(lambda: printing.save_printers(profiles, self.printers_path))

    def send_to_printer(self, profile: printing.PrinterProfile | None = None):
        if self.doc is None:
            return None
        if profile is None:
            profiles = self.printers()
            if not profiles:
                QMessageBox.information(self, self.tr_("send_to_printer"), self.tr_("no_printers"))
                self.edit_printers()
                return None
            dialog = SendDialog(self.tr_, profiles, self)
            if not dialog.exec():
                return None
            profile = dialog.profile()
        name = self.job.name.text().strip() or (self.doc.path.stem if self.doc.path else "job")
        ticket = self.ticket()
        ticket.job_name = name
        result = {}

        def run() -> None:
            result["r"] = printing.send(profile, self.doc, ticket, self.output.options(), self.imposition(), name)

        if self._guard(run):
            res = result["r"]
            text = self.tr_("sent", profile=res.profile, message=res.message)
            if res.queue_entry:
                text += f" ({res.queue_entry})"
            self.statusBar().showMessage(text, 10000)
            return res
        return None

    def show_queue(self) -> None:
        profiles = [p for p in self.printers() if p.kind == printing.PrinterKind.JMF]
        if not profiles:
            QMessageBox.information(self, self.tr_("jmf_queue"), self.tr_("no_jmf_printers"))
            return
        names = [p.name for p in profiles]
        name, ok = QInputDialog.getItem(self, self.tr_("jmf_queue"), self.tr_("printer"), names, 0, False)
        if not ok:
            return
        profile = profiles[names.index(name)]
        result = {}
        if not self._guard(lambda: result.update(r=jmf.queue_status(profile.target))):
            return
        response = result["r"]
        lines = [f"{self.tr_('queue_state')}: {response.queue_status or '–'}"]
        lines += [f"{e.queue_entry_id}  {e.status}  {e.job_id}" for e in response.entries] or [self.tr_("queue_empty")]
        QMessageBox.information(self, self.tr_("jmf_queue"), "\n".join(lines))
