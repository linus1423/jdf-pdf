# jdf-pdf

Desktop-Tool für Linux und Windows zur Druckvorbereitung von PDFs: Seiten bearbeiten,
JDF-Jobtickets erzeugen und in das PDF einbetten. PPF (CIP3) mit Farbzonen folgt.

## Stand

- PDF öffnen, Seitenvorschau, Seiten drehen, löschen, per Drag & Drop umsortieren, PDFs anhängen
- JDF 1.4 (Combined/DigitalPrinting) aus Auftragsdaten erzeugen: Auflage, Simplex/Duplex, Farbe, Papier
- Ausgabewege frei kombinierbar: JDF eingebettet (Anhang + Associated File), JDF als eigene `.jdf`,
  JDF-Ticketing für Canon PRISMAsync (JDF + PDF in einer Datei, `cid:`-Verweis)
- Medienkatalog (lokal, Import aus JMF-Antworten von PRISMAsync/Fiery), Medien je Seitenbereich
- Weiterverarbeitung im JDF: Heften, Lochen, Falzen, Beschneiden
- PDF/X: bei PDF/X-1a/3/4/5 wird standardmäßig nicht eingebettet, damit das PDF konform bleibt; Output Intent anzeigen und setzen
- Stapelverarbeitung in der GUI und per Kommandozeile
- Oberfläche auf Deutsch und Englisch

## Starten

```bash
pip install -e .
jdfpdf                 # GUI
jdfpdf-cli *.pdf -o ausgabe --copies 100 --sides duplex_long_edge --media "A4 160 g" --staple left_two --ticketing
```

Unter Linux braucht Qt ggf. Systembibliotheken (`libegl1 libgl1 libxkbcommon0`).

## Tests

```bash
pip install -e .[dev]
QT_QPA_PLATFORM=offscreen pytest
```

## Lizenzen der Abhängigkeiten

Bewusst ohne AGPL: PySide6 (LGPL-3.0), pikepdf/qpdf (MPL-2.0/Apache-2.0),
pypdfium2/PDFium (Apache-2.0/BSD-3), lxml (BSD). Kein PyMuPDF, kein Ghostscript.
