"""Einfache Übersetzungstabelle für die Oberfläche (Deutsch/Englisch)."""

from __future__ import annotations

LANGUAGES = {"de": "Deutsch", "en": "English"}

_TEXTS: dict[str, dict[str, str]] = {
    "app_title": {"de": "JDF-PDF Druckvorbereitung", "en": "JDF-PDF Prepress"},
    "file": {"de": "&Datei", "en": "&File"},
    "pages": {"de": "&Seiten", "en": "&Pages"},
    "language": {"de": "S&prache", "en": "&Language"},
    "open": {"de": "Öffnen …", "en": "Open …"},
    "save_as": {"de": "Speichern unter …", "en": "Save as …"},
    "insert_pdf": {"de": "PDF-Seiten anhängen …", "en": "Append PDF pages …"},
    "batch": {"de": "Stapelverarbeitung …", "en": "Batch processing …"},
    "quit": {"de": "Beenden", "en": "Quit"},
    "rotate_left": {"de": "Links drehen", "en": "Rotate left"},
    "rotate_right": {"de": "Rechts drehen", "en": "Rotate right"},
    "delete_pages": {"de": "Seiten löschen", "en": "Delete pages"},
    "ticket": {"de": "Auftrag (JDF)", "en": "Job (JDF)"},
    "job_name": {"de": "Auftragsname", "en": "Job name"},
    "copies": {"de": "Auflage", "en": "Copies"},
    "sides": {"de": "Druckseiten", "en": "Sides"},
    "simplex": {"de": "Einseitig", "en": "Simplex"},
    "duplex_long_edge": {"de": "Beidseitig, lange Kante", "en": "Duplex, long edge"},
    "duplex_short_edge": {"de": "Beidseitig, kurze Kante", "en": "Duplex, short edge"},
    "color": {"de": "Farbe", "en": "Color"},
    "cmyk": {"de": "Farbe (CMYK)", "en": "Color (CMYK)"},
    "gray": {"de": "Schwarzweiß", "en": "Grayscale"},
    "weight": {"de": "Grammatur (g/m²)", "en": "Paper weight (gsm)"},
    "customer": {"de": "Kunde", "en": "Customer"},
    "comment": {"de": "Bemerkung", "en": "Comment"},
    "sidecar": {"de": "JDF zusätzlich als eigene Datei speichern", "en": "Also save JDF as separate file"},
    "embed_save": {"de": "JDF einbetten und speichern …", "en": "Embed JDF and save …"},
    "attachments": {"de": "Eingebettete Dateien", "en": "Embedded files"},
    "no_document": {"de": "Kein PDF geöffnet", "en": "No PDF open"},
    "doc_info": {"de": "{pages} Seiten · {pdfx}", "en": "{pages} pages · {pdfx}"},
    "no_pdfx": {"de": "kein PDF/X", "en": "not PDF/X"},
    "pdfx_embed": {
        "de": "Achtung: {0} erlaubt eingebettete Dateien möglicherweise nicht. Erst PDF/X-6 sieht Associated Files vor.",
        "en": "Warning: {0} may not permit embedded files. Only PDF/X-6 defines associated files.",
    },
    "saved": {"de": "Gespeichert: {0}", "en": "Saved: {0}"},
    "error": {"de": "Fehler", "en": "Error"},
    "pdf_filter": {"de": "PDF-Dateien (*.pdf)", "en": "PDF files (*.pdf)"},
    "choose_output": {"de": "Zielordner wählen", "en": "Choose output folder"},
    "batch_done": {"de": "{ok} verarbeitet, {failed} fehlgeschlagen", "en": "{ok} processed, {failed} failed"},
    "restart_hint": {"de": "Sprache geändert.", "en": "Language changed."},
}


class Translator:
    def __init__(self, language: str = "de") -> None:
        self.language = language if language in LANGUAGES else "de"

    def __call__(self, key: str, *args: object, **kwargs: object) -> str:
        entry = _TEXTS.get(key)
        text = entry.get(self.language, entry["de"]) if entry else key
        return text.format(*args, **kwargs) if (args or kwargs) else text
