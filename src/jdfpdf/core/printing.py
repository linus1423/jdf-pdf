"""Ausgabe an Drucker: Systemdrucker (CUPS/Windows), Rohdaten, Hotfolder oder JMF.

Druckerprofile legen Ziel und Inhalt fest:

- ``SYSTEM``: PDF über den Druckdienst. Linux/macOS: ``lp``; Windows: PDF als
  Rohdaten an den Spooler (Drucker mit PDF-Direktdruck, z. B. imagePRESS) oder,
  mit ``raster``, gerendert über Qt.
- ``RAW``: Nutzdaten unverändert an die Warteschlange (``lp -o raw`` bzw.
  Windows-Spooler ``RAW``), etwa die JDF-Ticketing-Datei für PRISMAsync.
- ``HOTFOLDER``: Dateien in einen Ordner schreiben (erst temporär, dann umbenennen).
- ``JMF``: per ``SubmitQueueEntry`` an einen JDF-Controller.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

from .jdf import JobTicket
from .media import config_dir
from .pdfdoc import PdfDocument
from .prepress import OutputOptions, OutputResult, write_output


class PrinterKind(str, Enum):
    SYSTEM = "system"
    RAW = "raw"
    HOTFOLDER = "hotfolder"
    JMF = "jmf"


class Payload(str, Enum):
    PDF = "pdf"  # PDF (mit eingebettetem JDF, falls die Ausgabeoptionen das vorsehen)
    PDF_JDF = "pdf_jdf"  # PDF plus separate JDF-Datei (nur Hotfolder)
    TICKETING = "ticketing"  # JDF + PDF in einer Datei (PRISMAsync)


@dataclass
class PrinterProfile:
    name: str
    kind: PrinterKind = PrinterKind.HOTFOLDER
    target: str = ""  # Druckername, Ordner oder JMF-URL
    payload: Payload = Payload.PDF
    raster: bool = False  # Windows/SYSTEM: über Qt gerendert drucken
    raster_dpi: int = 300
    options: dict = field(default_factory=dict)  # zusätzliche lp-Optionen, z. B. {"media": "A4"}

    def to_dict(self) -> dict:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["payload"] = self.payload.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PrinterProfile":
        data = dict(data)
        data["kind"] = PrinterKind(data.get("kind", "hotfolder"))
        data["payload"] = Payload(data.get("payload", "pdf"))
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})


def default_printers_path() -> Path:
    return config_dir() / "printers.json"


def load_printers(path: Path | None = None) -> list[PrinterProfile]:
    path = Path(path or default_printers_path())
    if path.exists():
        return [PrinterProfile.from_dict(p) for p in json.loads(path.read_text(encoding="utf-8"))]
    return []


def save_printers(printers: list[PrinterProfile], path: Path | None = None) -> None:
    path = Path(path or default_printers_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([p.to_dict() for p in printers], indent=1, ensure_ascii=False), encoding="utf-8")


# --- Systemdrucker ---------------------------------------------------------------------


def list_system_printers() -> list[str]:
    if sys.platform == "win32":
        return _win_printers()
    lpstat = shutil.which("lpstat")
    if not lpstat:
        return []
    try:
        out = subprocess.run([lpstat, "-e"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in out.splitlines() if line.strip()]


def _lp(printer: str, path: Path, copies: int = 1, raw: bool = False, options: dict | None = None,
        title: str = "") -> str:
    lp = shutil.which("lp")
    if not lp:
        raise RuntimeError("Befehl 'lp' nicht gefunden (CUPS installiert?)")
    cmd = [lp, "-d", printer, "-n", str(max(copies, 1))]
    if title:
        cmd += ["-t", title]
    if raw:
        cmd += ["-o", "raw"]
    for key, value in (options or {}).items():
        cmd += ["-o", f"{key}={value}" if value not in (None, "", True) else str(key)]
    cmd.append(str(path))
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip() or f"lp beendet mit {done.returncode}")
    return done.stdout.strip()


def _win_printers() -> list[str]:  # pragma: no cover - nur unter Windows
    import ctypes
    from ctypes import wintypes

    winspool = ctypes.WinDLL("winspool.drv")
    flags = 0x2 | 0x4  # PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS
    needed, returned = wintypes.DWORD(), wintypes.DWORD()
    winspool.EnumPrintersW(flags, None, 4, None, 0, ctypes.byref(needed), ctypes.byref(returned))
    buf = ctypes.create_string_buffer(needed.value)
    if not winspool.EnumPrintersW(flags, None, 4, buf, needed, ctypes.byref(needed), ctypes.byref(returned)):
        return []

    class PRINTER_INFO_4(ctypes.Structure):
        _fields_ = [("pPrinterName", wintypes.LPWSTR), ("pServerName", wintypes.LPWSTR),
                    ("Attributes", wintypes.DWORD)]

    info = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4))
    return [info[i].pPrinterName for i in range(returned.value)]


def _win_raw(printer: str, data: bytes, title: str) -> str:  # pragma: no cover - nur unter Windows
    import ctypes
    from ctypes import wintypes

    winspool = ctypes.WinDLL("winspool.drv")

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR), ("pOutputFile", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR)]

    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer, ctypes.byref(handle), None):
        raise RuntimeError(f"Drucker {printer!r} nicht gefunden")
    try:
        job = winspool.StartDocPrinterW(handle, 1, ctypes.byref(DOC_INFO_1(title or "jdfpdf", None, "RAW")))
        if not job:
            raise RuntimeError("StartDocPrinter fehlgeschlagen")
        try:
            winspool.StartPagePrinter(handle)
            written = wintypes.DWORD()
            if not winspool.WritePrinter(handle, data, len(data), ctypes.byref(written)):
                raise RuntimeError("WritePrinter fehlgeschlagen")
            winspool.EndPagePrinter(handle)
        finally:
            winspool.EndDocPrinter(handle)
        return f"Job {job}"
    finally:
        winspool.ClosePrinter(handle)


def print_rendered(printer: str, pdf: bytes, copies: int = 1, dpi: int = 300, title: str = "") -> str:
    """PDF mit PDFium rendern und über Qt drucken (für Drucker ohne PDF-Direktdruck)."""
    import pypdfium2 as pdfium
    from PIL.ImageQt import ImageQt
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QGuiApplication, QPageSize, QPainter
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

    if QGuiApplication.instance() is None:  # Qt braucht eine Anwendung
        print_rendered._app = QGuiApplication([])
    info = next((p for p in QPrinterInfo.availablePrinters() if p.printerName() == printer), None)
    if info is None:
        raise RuntimeError(f"Drucker {printer!r} nicht gefunden")
    qprinter = QPrinter(info, QPrinter.PrinterMode.HighResolution)
    qprinter.setDocName(title or "jdfpdf")
    qprinter.setCopyCount(max(copies, 1))
    qprinter.setFullPage(True)
    doc = pdfium.PdfDocument(pdf)
    painter = QPainter()
    try:
        for index in range(len(doc)):
            page = doc[index]
            w, h = page.get_size()
            qprinter.setPageSize(QPageSize(QRectF(0, 0, w, h).size(), QPageSize.Unit.Point))
            if index == 0:
                if not painter.begin(qprinter):
                    raise RuntimeError("Druck konnte nicht gestartet werden")
            else:
                qprinter.newPage()
            image = page.render(scale=dpi / 72).to_pil()
            target = painter.viewport()
            painter.drawImage(QRectF(target), ImageQt(image))
            page.close()
    finally:
        if painter.isActive():
            painter.end()
        doc.close()
    return f"{len(doc)} Seiten"


# --- Senden --------------------------------------------------------------------------


@dataclass
class SendResult:
    profile: str
    message: str
    files: list[Path] = field(default_factory=list)
    queue_entry: str = ""


def _atomic_write(path: Path, data: bytes) -> Path:
    tmp = path.with_name("." + path.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return path


def send(profile: PrinterProfile, doc: PdfDocument, ticket: JobTicket, options: OutputOptions | None = None,
         imposition=None, name: str | None = None) -> SendResult:
    """Auftrag gemäß Profil ausgeben. ``doc`` wird dabei nicht verändert."""
    options = options or OutputOptions()
    name = name or ticket.job_name or "job"
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    with tempfile.TemporaryDirectory() as tmp:
        work = PdfDocument.from_bytes(doc.to_bytes())
        opts = OutputOptions(embed=options.embed, sidecar=True, ticketing=True, pdfx_policy=options.pdfx_policy)
        result: OutputResult = write_output(work, ticket, Path(tmp) / f"{safe}.pdf", opts, imposition)
        pdf_path = result.pdf
        jdf_path = pdf_path.with_suffix(".jdf")
        ticketing_path = next(p for p in result.extra_files if p.name.endswith("_prismasync.jdf"))
        payload = {Payload.PDF: pdf_path, Payload.TICKETING: ticketing_path}.get(profile.payload, pdf_path)
        copies = ticket.copies

        if profile.kind == PrinterKind.HOTFOLDER:
            folder = Path(profile.target)
            if not folder.is_dir():
                raise RuntimeError(f"Hotfolder {folder} nicht gefunden")
            files = [_atomic_write(folder / pdf_path.name, pdf_path.read_bytes())] \
                if profile.payload != Payload.TICKETING else []
            if profile.payload == Payload.PDF_JDF:
                files.append(_atomic_write(folder / jdf_path.name, jdf_path.read_bytes()))
            if profile.payload == Payload.TICKETING:
                files.append(_atomic_write(folder / ticketing_path.name, ticketing_path.read_bytes()))
            return SendResult(profile.name, ", ".join(f.name for f in files), files)

        if profile.kind == PrinterKind.JMF:
            from . import jmf

            response = jmf.submit(profile.target, ticket, pdf_path.read_bytes())
            if not response.ok:
                raise RuntimeError(f"JMF ReturnCode {response.return_code}: {response.comment}")
            entry = response.entries[0].queue_entry_id if response.entries else ""
            return SendResult(profile.name, response.comment or "OK", queue_entry=entry)

        if profile.kind == PrinterKind.RAW:
            data = payload.read_bytes()
            if sys.platform == "win32":  # pragma: no cover
                message = _win_raw(profile.target, data, name)
            else:
                message = _lp(profile.target, payload, 1, raw=True, options=profile.options, title=name)
            return SendResult(profile.name, message)

        # Systemdrucker: normales PDF, Auflage über den Druckdienst
        if profile.raster:
            message = print_rendered(profile.target, pdf_path.read_bytes(), copies, profile.raster_dpi, name)
        elif sys.platform == "win32":  # pragma: no cover
            message = _win_raw(profile.target, pdf_path.read_bytes(), name)
        else:
            message = _lp(profile.target, pdf_path, copies, options=profile.options, title=name)
        return SendResult(profile.name, message)

