"""Scannen über SANE (``scanimage``, Linux/macOS) bzw. WIA (Windows, per PowerShell).

Es werden nur externe Werkzeuge aufgerufen, keine Treiber eingebunden. Ergebnis
sind TIFF- bzw. PNG-Dateien, die wie Bilder eingefügt werden.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class ScannerUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Keine Scan-Schnittstelle gefunden (Linux: Paket 'sane-utils' mit scanimage; "
                         "Windows: WIA).")


@dataclass
class Scanner:
    id: str
    name: str


@dataclass
class ScanOptions:
    dpi: int = 300
    mode: str = "color"  # color, gray, lineart
    source: str = ""  # leer: Standard (meist Flachbett); z. B. "ADF", "ADF Duplex"
    batch: bool = False  # mehrere Seiten aus dem Einzug


_SANE_MODES = {"color": "Color", "gray": "Gray", "lineart": "Lineart"}
_WIA_INTENT = {"color": 1, "gray": 2, "lineart": 4}


def backend() -> str | None:
    if sys.platform == "win32":
        return "wia"
    return "sane" if shutil.which("scanimage") else None


def parse_sane_list(output: str) -> list[Scanner]:
    """Ausgabe von ``scanimage -f '%d\\t%v %m%n'``."""
    scanners = []
    for line in output.splitlines():
        if "\t" in line:
            device, name = line.split("\t", 1)
            scanners.append(Scanner(device.strip(), name.strip() or device.strip()))
    return scanners


def list_scanners(timeout: float = 30) -> list[Scanner]:
    kind = backend()
    if kind == "sane":
        done = subprocess.run(["scanimage", "-f", "%d\t%v %m%n"], capture_output=True, text=True, timeout=timeout)
        return parse_sane_list(done.stdout)
    if kind == "wia":  # pragma: no cover - nur unter Windows
        script = ("$m = New-Object -ComObject WIA.DeviceManager; foreach ($d in $m.DeviceInfos) "
                  "{ if ($d.Type -eq 1) { $d.DeviceID + \"`t\" + $d.Properties.Item('Name').Value } }")
        done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                              capture_output=True, text=True, timeout=timeout)
        return parse_sane_list(done.stdout)
    return []


def sane_command(device: str, opts: ScanOptions, out_dir: Path) -> list[str]:
    cmd = ["scanimage", "--format=tiff", f"--resolution={opts.dpi}", f"--mode={_SANE_MODES[opts.mode]}"]
    if device:
        cmd += ["-d", device]
    if opts.source:
        cmd += [f"--source={opts.source}"]
    if opts.batch:
        cmd += [f"--batch={out_dir / 'scan%04d.tif'}", "--batch-start=1"]
    return cmd


def scan(device: str, opts: ScanOptions, out_dir: Path, timeout: float = 600) -> list[Path]:
    """Scannen; liefert die erzeugten Bilddateien in Seitenreihenfolge."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = backend()
    if kind == "sane":
        cmd = sane_command(device, opts, out_dir)
        if opts.batch:
            done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            files = sorted(out_dir.glob("scan*.tif"))
            # scanimage meldet am Ende des Einzugs einen Fehler ("Document feeder out of documents")
            if not files:
                raise RuntimeError(done.stderr.strip() or "Keine Seiten gescannt")
            return files
        target = out_dir / "scan0001.tif"
        with open(target, "wb") as fh:
            done = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, text=False, timeout=timeout)
        if done.returncode != 0 or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            raise RuntimeError(done.stderr.decode(errors="replace").strip() or "Scan fehlgeschlagen")
        return [target]
    if kind == "wia":  # pragma: no cover - nur unter Windows
        return _scan_wia(device, opts, out_dir, timeout)
    raise ScannerUnavailable()


def wia_script(device: str, opts: ScanOptions, out_dir: Path) -> str:
    """PowerShell: über WIA scannen; im Einzugsmodus so lange, bis keine Seite mehr kommt."""
    dev = device.replace("'", "''")
    folder = str(out_dir).replace("'", "''")
    loop = "while ($true)" if opts.batch else "foreach ($once in 1)"
    return (
        "$m = New-Object -ComObject WIA.DeviceManager; "
        f"$info = $m.DeviceInfos | Where-Object {{ $_.DeviceID -eq '{dev}' -or '{dev}' -eq '' }} | Select-Object -First 1; "
        "if (-not $info) { throw 'Scanner nicht gefunden' }; $d = $info.Connect(); "
        + ("$d.Properties.Item('Document Handling Select').Value = 1; " if opts.source or opts.batch else "")
        + "$item = $d.Items.Item(1); "
        f"$item.Properties.Item('6146').Value = {_WIA_INTENT[opts.mode]}; "
        f"$item.Properties.Item('6147').Value = {opts.dpi}; $item.Properties.Item('6148').Value = {opts.dpi}; "
        f"$n = 0; {loop} {{ try {{ $img = $item.Transfer('{{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}}') }} "
        "catch { if ($n -gt 0) { break } else { throw } }; $n++; "
        f"$img.SaveFile((Join-Path '{folder}' ('scan{{0:D4}}.png' -f $n))) }}"
    )


def _scan_wia(device: str, opts: ScanOptions, out_dir: Path, timeout: float) -> list[Path]:  # pragma: no cover
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                           wia_script(device, opts, out_dir)], capture_output=True, text=True, timeout=timeout)
    files = sorted(out_dir.glob("scan*.png"))
    if not files:
        raise RuntimeError(done.stderr.strip() or "Scan fehlgeschlagen")
    return files
