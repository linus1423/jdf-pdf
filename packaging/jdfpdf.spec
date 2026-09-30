# PyInstaller-Bauplan: ein Ordner mit Oberfläche (jdfpdf) und Kommandozeile (jdfpdf-cli).
# Aufruf aus dem Projektordner:  pyinstaller packaging/jdfpdf.spec --noconfirm
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs, collect_submodules

root = Path(SPECPATH).parent
datas, binaries, hidden = [], [], []
for package in ("pypdfium2", "pypdfium2_raw"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hidden += h
datas += collect_data_files("reportlab")  # Schriften (Vera), Barcode-Daten
binaries += collect_dynamic_libs("pikepdf")
# reportlab lädt alle Barcode-Module dynamisch beim Import
hidden += collect_submodules("reportlab.graphics.barcode") + collect_submodules("reportlab.pdfbase") + ["PIL.ImageQt"]
excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia"]


def analysis(script):
    return Analysis([str(root / "packaging" / script)], pathex=[str(root / "src")], binaries=binaries,
                    datas=datas, hiddenimports=hidden, excludes=excludes, noarchive=False)


gui = analysis("jdfpdf_gui.py")
cli = analysis("jdfpdf_cli.py")
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="jdfpdf", console=False,
              upx=False)
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="jdfpdf-cli", console=True,
              upx=False)
COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name="jdfpdf", upx=False)
