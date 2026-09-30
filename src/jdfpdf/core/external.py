"""Seiten in externen Programmen bearbeiten (Illustrator, Inkscape, Acrobat …).

Die Seite wird als Einzel-PDF in einen temporären Ordner geschrieben und mit dem
konfigurierten Programm geöffnet. ``EditSession.poll`` meldet, sobald die Datei
gespeichert wurde und sich über zwei Abfragen nicht mehr ändert; danach kann die
Seite im Dokument ersetzt werden. Bis zu zehn Programme lassen sich einrichten.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import pikepdf

from .media import config_dir
from .pdfdoc import PdfDocument

MAX_EDITORS = 10


@dataclass
class ExternalEditor:
    name: str
    command: str  # Programmpfad
    args: str = "{file}"  # Argumente; {file} wird durch die Seiten-PDF ersetzt

    def argv(self, file: Path) -> list[str]:
        parts = shlex.split(self.args, posix=sys.platform != "win32") or ["{file}"]
        if not any("{file}" in p for p in parts):
            parts.append("{file}")
        return [self.command, *[p.replace("{file}", str(file)).strip('"') for p in parts]]


def default_editors_path() -> Path:
    return config_dir() / "editors.json"


def load_editors(path: Path | None = None) -> list[ExternalEditor]:
    path = Path(path or default_editors_path())
    if not path.exists():
        return []
    return [ExternalEditor(**{k: v for k, v in e.items() if k in ExternalEditor.__dataclass_fields__})
            for e in json.loads(path.read_text(encoding="utf-8"))][:MAX_EDITORS]


def save_editors(editors: list[ExternalEditor], path: Path | None = None) -> None:
    if len(editors) > MAX_EDITORS:
        raise ValueError(f"Höchstens {MAX_EDITORS} externe Programme")
    path = Path(path or default_editors_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(e) for e in editors], indent=1, ensure_ascii=False), encoding="utf-8")


def export_page(doc: PdfDocument, index: int, folder: Path, name: str = "") -> Path:
    single = pikepdf.new()
    single.pages.append(doc.pdf.pages[index])
    path = Path(folder) / f"{name or 'seite'}_{index + 1}.pdf"
    single.save(path)
    return path


def _signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:  # Programme speichern oft über eine Zwischendatei und benennen dann um
        return None
    return stat.st_size, stat.st_mtime_ns


class EditSession:
    """Eine Seite, die gerade extern bearbeitet wird."""

    def __init__(self, editor: ExternalEditor, doc: PdfDocument, index: int, name: str = "") -> None:
        self.editor = editor
        self.index = index
        self.folder = Path(tempfile.mkdtemp(prefix="jdfpdf-edit-"))
        self.path = export_page(doc, index, self.folder, name)
        self._last = _signature(self.path)
        self._pending: tuple[int, int] | None = None
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        argv = self.editor.argv(self.path)
        if not (Path(argv[0]).exists() or shutil.which(argv[0])):
            raise FileNotFoundError(f"Programm nicht gefunden: {argv[0]}")
        self.process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def poll(self) -> bool:
        """True, wenn eine neue, fertig geschriebene Fassung vorliegt."""
        current = _signature(self.path)
        if current is None or current == self._last:
            self._pending = None
            return False
        if current != self._pending:  # noch nicht stabil
            self._pending = current
            return False
        self._last, self._pending = current, None
        return True

    def read(self) -> PdfDocument:
        return PdfDocument.from_bytes(self.path.read_bytes())

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self) -> None:
        shutil.rmtree(self.folder, ignore_errors=True)


def apply_edit(doc: PdfDocument, index: int, edited: PdfDocument) -> int:
    """Seite ``index`` durch die bearbeitete(n) Seite(n) ersetzen; liefert die Anzahl neuer Seiten."""
    if edited.page_count == 0:
        raise ValueError("Die bearbeitete Datei enthält keine Seiten")
    doc.replace_page(index, edited, 0)
    for offset in range(1, edited.page_count):
        doc.pdf.pages.insert(index + offset, edited.pdf.pages[offset])
    return edited.page_count


def suggested_editors() -> list[ExternalEditor]:
    """Bekannte Programme, die auf diesem System gefunden werden."""
    found = []
    for name, commands in (("Inkscape", ["inkscape"]), ("Scribus", ["scribus"]), ("GIMP", ["gimp"]),
                           ("LibreOffice Draw", ["lodraw", "libreoffice"]), ("Okular", ["okular"]),
                           ("Xournal++", ["xournalpp"])):
        for command in commands:
            path = shutil.which(command)
            if path:
                args = "--draw {file}" if command == "libreoffice" else "{file}"
                found.append(ExternalEditor(name, path, args))
                break
    if sys.platform == "win32":  # pragma: no cover
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for name, rel in (("Acrobat", r"Adobe\Acrobat DC\Acrobat\Acrobat.exe"),
                              ("Inkscape", r"Inkscape\bin\inkscape.exe")):
                exe = Path(base) / rel
                if exe.exists() and name not in {e.name for e in found}:
                    found.append(ExternalEditor(name, str(exe)))
    return found[:MAX_EDITORS]
