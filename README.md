# jdf-pdf

Desktop-Tool für Linux und Windows zur Druckvorbereitung von PDFs: Seiten bearbeiten,
JDF-Jobtickets und CIP3-PPF (mit Farbzonen) erzeugen und in das PDF einbetten, an
Canon imagePRESS / PRISMAsync ausgeben.

## Funktionen

- **Dateien:** PDF, Bilder und Office-Dateien (Word, Excel, PowerPoint, OpenDocument; Umwandlung
  über LibreOffice headless, unter Windows alternativ MS Office) öffnen und einfügen; Scannen über
  SANE bzw. WIA.
- **Seiten:** drehen, löschen, umsortieren, duplizieren, leere Seiten, ersetzen, skalieren,
  verschieben, Seitenboxen, Beschnitt, Abschnitte aus Lesezeichen.
- **Elemente:** Kopf-/Fußzeilen, Seitenzahlen, Stempel, Wasserzeichen, Bilder, Textblöcke, Text
  suchen und ersetzen (abdecken oder bei einfachen Schriften echt umschreiben), Registerblätter,
  Daumenregister, Rückentext, Druckmarken mit Barcode.
- **Scan-Bereinigung:** Entflecken, Geraderichten, Ausrichten, Rand und Bereiche radieren, Seiten rastern.
- **Farbe:** Farbseiten erkennen, Graustufen, Farb-/s/w-Split mit Merge-Plan, Bildkorrektur,
  Sonderfarben und Farbbibliothek.
- **Prüfen:** Preflight mit HTML/PDF-Bericht, Farbzonen je Maschinenprofil.
- **Ausschießen:** n-up, Step & Repeat, Schneiden & Stapeln, Broschüre, Multi-Broschüre, Klebebindung.
- **Variabler Datendruck:** Text, Bilder, Code 128, EAN-13 und QR-Codes aus CSV oder XLSX.
- **Externe Programme:** bis zu zehn Programme; die Seite wird nach dem Speichern automatisch übernommen.
- **Ausgabe:** JDF eingebettet oder als Datei, JDF-Ticketing für PRISMAsync, PPF, Weiterverarbeitungs-JDF,
  Softproof-PDF für den Kunden. PDF/X bleibt konform (bei PDF/X-1a/3/4/5 wird standardmäßig nicht eingebettet).
- **Automatisierung:** Vorlagen aus aufgezeichneten Schritten, Hotfolder, Druckerprofile (CUPS,
  Windows-Spooler, Hotfolder, JMF), JMF-Warteschlange.
- Oberfläche auf Deutsch und Englisch.

## Starten

Fertige Pakete für Windows und Linux gibt es unter *Releases* (Ordner entpacken, `jdfpdf` bzw.
`jdfpdf.exe` starten). Aus dem Quelltext:

```bash
pip install -e .
jdfpdf                 # GUI
jdfpdf-cli *.pdf -o ausgabe --copies 100 --sides duplex_long_edge --media "A4 160 g" --staple left_two --ticketing
jdfpdf-cli run vorlage.jdftpl *.docx -o ausgabe
```

Unter Linux braucht Qt ggf. Systembibliotheken (`libegl1 libgl1 libxkbcommon0`). Für Office-Dateien
LibreOffice installieren (oder den Pfad zu `soffice` in `JDFPDF_SOFFICE` angeben), zum Scannen unter
Linux `sane-utils`.

## Pakete bauen

```bash
pip install . pyinstaller
pyinstaller packaging/jdfpdf.spec --noconfirm   # Ergebnis: dist/jdfpdf/
```

Ein Tag `v*` baut die Pakete per GitHub Actions und hängt sie an ein Release.

## Tests

```bash
pip install -e .[dev]
QT_QPA_PLATFORM=offscreen pytest
JDFPDF_TEST_LIBREOFFICE=1 QT_QPA_PLATFORM=offscreen pytest   # zusätzlich mit echtem LibreOffice
```

## Lizenzen der Abhängigkeiten

Bewusst ohne AGPL: PySide6 (LGPL-3.0), pikepdf/qpdf (MPL-2.0/Apache-2.0),
pypdfium2/PDFium (Apache-2.0/BSD-3), lxml (BSD), reportlab (BSD), Pillow (MIT-CMU).
Kein PyMuPDF, kein Ghostscript. LibreOffice (MPL-2.0) wird nur als externes Programm aufgerufen.
