"""Office-Dateien nach PDF wandeln: LibreOffice (headless) oder unter Windows MS Office.

LibreOffice (MPL) wird als externes Programm aufgerufen, nicht eingebunden. Jeder
Aufruf nutzt ein eigenes, temporäres Benutzerprofil, damit eine laufende
LibreOffice-Instanz die Konvertierung nicht blockiert. Der Pfad zu ``soffice``
lässt sich mit der Umgebungsvariablen ``JDFPDF_SOFFICE`` festlegen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORD = {".doc", ".docx", ".docm", ".dot", ".dotx", ".odt", ".ott", ".rtf", ".txt", ".wpd"}
EXCEL = {".xls", ".xlsx", ".xlsm", ".ods", ".ots"}
POWERPOINT = {".ppt", ".pptx", ".pptm", ".pps", ".ppsx", ".odp", ".otp"}
OTHER = {".odg", ".vsd", ".vsdx", ".pub"}
OFFICE_SUFFIXES = WORD | EXCEL | POWERPOINT | OTHER


class ConverterMissing(RuntimeError):
    """Kein Konverter gefunden (LibreOffice bzw. MS Office)."""

    def __init__(self) -> None:
        super().__init__("Kein Office-Konverter gefunden. Bitte LibreOffice installieren "
                         "(https://www.libreoffice.org) oder den Pfad in JDFPDF_SOFFICE angeben.")


def is_office(path: str | Path) -> bool:
    return Path(path).suffix.lower() in OFFICE_SUFFIXES


def find_soffice() -> str | None:
    env = os.environ.get("JDFPDF_SOFFICE")
    if env:
        return env if Path(env).exists() or shutil.which(env) else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    candidates = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    else:
        candidates += [Path("/opt/libreoffice/program/soffice"), Path("/usr/lib/libreoffice/program/soffice")]
    return next((str(p) for p in candidates if p.exists()), None)


def ms_office_available() -> bool:  # pragma: no cover - nur unter Windows
    if sys.platform != "win32":
        return False
    import winreg

    for prog_id in ("Word.Application", "Excel.Application", "PowerPoint.Application"):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog_id))
            return True
        except OSError:
            continue
    return False


def available_converter() -> str | None:
    """``"libreoffice"``, ``"msoffice"`` oder ``None``."""
    if find_soffice():
        return "libreoffice"
    if ms_office_available():
        return "msoffice"
    return None


def convert_libreoffice(path: Path, soffice: str | None = None, timeout: float = 180) -> bytes:
    soffice = soffice or find_soffice()
    if not soffice:
        raise ConverterMissing()
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix="jdfpdf-office-") as tmp:
        profile = Path(tmp) / "profile"
        out = Path(tmp) / "out"
        out.mkdir()
        cmd = [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
               "--nolockcheck", "--convert-to", "pdf", "--outdir", str(out), str(path)]
        try:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"LibreOffice hat nicht innerhalb von {timeout:.0f} s geantwortet") from exc
        result = out / (path.stem + ".pdf")
        if not result.exists():
            detail = (done.stderr or done.stdout).strip()
            raise RuntimeError(f"LibreOffice konnte {path.name} nicht umwandeln" + (f": {detail}" if detail else ""))
        return result.read_bytes()


_PS_SCRIPTS = {
    "word": "$app = New-Object -ComObject Word.Application; $app.Visible = $false; "
            "try {{ $d = $app.Documents.Open('{src}', $false, $true); $d.ExportAsFixedFormat('{dst}', 17); "
            "$d.Close(0) }} finally {{ $app.Quit() }}",
    "excel": "$app = New-Object -ComObject Excel.Application; $app.Visible = $false; $app.DisplayAlerts = $false; "
             "try {{ $d = $app.Workbooks.Open('{src}', 0, $true); $d.ExportAsFixedFormat(0, '{dst}'); "
             "$d.Close($false) }} finally {{ $app.Quit() }}",
    "powerpoint": "$app = New-Object -ComObject PowerPoint.Application; "
                  "try {{ $d = $app.Presentations.Open('{src}', $true, $false, $false); $d.SaveAs('{dst}', 32); "
                  "$d.Close() }} finally {{ $app.Quit() }}",
}


def _ms_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in EXCEL:
        return "excel"
    if suffix in POWERPOINT:
        return "powerpoint"
    return "word"


def ms_office_script(path: Path, target: Path) -> str:
    """PowerShell-Befehl für die Umwandlung per COM (Pfade mit ' werden verdoppelt)."""
    quote = lambda p: str(p).replace("'", "''")  # noqa: E731
    return _PS_SCRIPTS[_ms_kind(path)].format(src=quote(path), dst=quote(target))


def convert_msoffice(path: Path, timeout: float = 300) -> bytes:  # pragma: no cover - nur unter Windows
    path = Path(path).resolve()
    with tempfile.TemporaryDirectory(prefix="jdfpdf-office-") as tmp:
        target = Path(tmp) / (path.stem + ".pdf")
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                               ms_office_script(path, target)], capture_output=True, text=True, timeout=timeout)
        if not target.exists():
            raise RuntimeError(f"MS Office konnte {path.name} nicht umwandeln: {done.stderr.strip()}")
        return target.read_bytes()


def convert(path: str | Path, prefer: str | None = None) -> bytes:
    """Office-Datei nach PDF; ``prefer`` = ``"libreoffice"`` oder ``"msoffice"``."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    order = ["msoffice", "libreoffice"] if prefer == "msoffice" else ["libreoffice", "msoffice"]
    for name in order:
        if name == "libreoffice" and find_soffice():
            return convert_libreoffice(path)
        if name == "msoffice" and ms_office_available():  # pragma: no cover
            return convert_msoffice(path)
    raise ConverterMissing()
