"""Hotfolder: eingehende Dateien mit einer Vorlage verarbeiten.

Ohne zusätzliche Abhängigkeiten per Abfrage (Polling): Eine Datei gilt als
vollständig, wenn Größe und Änderungszeit über zwei Durchläufe gleich bleiben.
Danach wird sie verarbeitet und nach ``done`` bzw. ``error`` verschoben; ein
Protokoll steht in ``hotfolder.log`` im Eingangsordner.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .media import MediaCatalog
from .template import Template, run_template

ACCEPTED = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


@dataclass
class HotfolderEvent:
    file: str
    ok: bool
    message: str
    outputs: list[str] = field(default_factory=list)
    time: datetime = field(default_factory=datetime.now)

    def line(self) -> str:
        status = "OK" if self.ok else "FEHLER"
        extra = f" -> {', '.join(self.outputs)}" if self.outputs else ""
        return f"{self.time:%Y-%m-%d %H:%M:%S} {status} {self.file}: {self.message}{extra}"


class Hotfolder:
    def __init__(self, inbox: Path, outbox: Path, template: Template, done: Path | None = None,
                 error: Path | None = None, overrides: dict[str, Any] | None = None,
                 catalog: MediaCatalog | None = None, after: Callable[[Path, object], None] | None = None) -> None:
        self.inbox = Path(inbox)
        self.outbox = Path(outbox)
        self.done = Path(done) if done else self.inbox / "done"
        self.error = Path(error) if error else self.inbox / "error"
        self.template = template
        self.overrides = overrides or {}
        self.catalog = catalog
        self.after = after  # z. B. Senden an einen Drucker
        self._seen: dict[Path, tuple[int, float]] = {}
        for folder in (self.inbox, self.outbox, self.done, self.error):
            folder.mkdir(parents=True, exist_ok=True)

    def _ready(self) -> list[Path]:
        ready = []
        current = {}
        for path in sorted(self.inbox.iterdir()):
            if not path.is_file() or path.suffix.lower() not in ACCEPTED or path.name.startswith("."):
                continue
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime)
            current[path] = signature
            if self._seen.get(path) == signature and stat.st_size > 0:
                ready.append(path)
        self._seen = {p: s for p, s in current.items() if p not in ready}
        return ready

    def _move(self, path: Path, folder: Path) -> None:
        target = folder / path.name
        if target.exists():
            target = folder / f"{path.stem}_{datetime.now():%Y%m%d%H%M%S}{path.suffix}"
        shutil.move(str(path), target)

    def _log(self, event: HotfolderEvent) -> None:
        with open(self.inbox / "hotfolder.log", "a", encoding="utf-8") as log:
            log.write(event.line() + "\n")

    def poll(self) -> list[HotfolderEvent]:
        """Einen Durchlauf ausführen; liefert die Ereignisse der verarbeiteten Dateien."""
        events = []
        for path in self._ready():
            try:
                result = run_template(path, self.outbox, self.template, self.overrides, self.catalog,
                                      base_dir=self.inbox)
                if self.after is not None:
                    self.after(path, result)
                outputs = [result.pdf.name, *[p.name for p in result.extra_files]]
                event = HotfolderEvent(path.name, True, "; ".join(result.warnings) or "verarbeitet", outputs)
                self._move(path, self.done)
            except Exception as exc:
                event = HotfolderEvent(path.name, False, str(exc))
                self._move(path, self.error)
            self._log(event)
            events.append(event)
        return events

    def run(self, interval: float = 2.0, stop: Callable[[], bool] = lambda: False,
            on_event: Callable[[HotfolderEvent], None] | None = None) -> None:
        """Endlos abfragen, bis ``stop()`` wahr ist (Strg+C beendet ebenfalls)."""
        while not stop():
            for event in self.poll():
                if on_event:
                    on_event(event)
            time.sleep(interval)
