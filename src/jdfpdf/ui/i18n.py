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
    # Paket 1: Medien, Finishing, Ausgabe
    "tab_job": {"de": "Auftrag", "en": "Job"},
    "tab_media": {"de": "Medien", "en": "Media"},
    "tab_finishing": {"de": "Weiterverarbeitung", "en": "Finishing"},
    "tab_output": {"de": "Ausgabe", "en": "Output"},
    "media_default": {"de": "Standardmedium", "en": "Default media"},
    "media_from_document": {"de": "Format aus Dokument", "en": "Size from document"},
    "media_ranges": {"de": "Medien für Seitenbereiche", "en": "Media for page ranges"},
    "from_page": {"de": "Von Seite", "en": "From page"},
    "to_page": {"de": "Bis Seite", "en": "To page"},
    "media": {"de": "Medium", "en": "Media"},
    "add": {"de": "Hinzufügen", "en": "Add"},
    "remove": {"de": "Entfernen", "en": "Remove"},
    "assign_selection": {"de": "Auswahl zuweisen", "en": "Assign to selection"},
    "edit_catalog": {"de": "Medienkatalog …", "en": "Media catalog …"},
    "catalog_title": {"de": "Medienkatalog", "en": "Media catalog"},
    "import_jmf": {"de": "Aus JMF-Datei importieren …", "en": "Import from JMF file …"},
    "imported": {"de": "{0} Medien importiert", "en": "{0} media imported"},
    "col_name": {"de": "Name", "en": "Name"},
    "col_width": {"de": "Breite (mm)", "en": "Width (mm)"},
    "col_height": {"de": "Höhe (mm)", "en": "Height (mm)"},
    "col_weight": {"de": "g/m²", "en": "gsm"},
    "col_type": {"de": "Typ", "en": "Type"},
    "col_color": {"de": "Farbe", "en": "Color"},
    "col_coating": {"de": "Beschichtung", "en": "Coating"},
    "col_thickness": {"de": "Dicke (µm)", "en": "Thickness (µm)"},
    "col_punched": {"de": "Gelocht", "en": "Punched"},
    "col_tabs": {"de": "Taben", "en": "Tabs"},
    "staple": {"de": "Heften", "en": "Staple"},
    "punch": {"de": "Lochen", "en": "Punch"},
    "fold": {"de": "Falzen", "en": "Fold"},
    "trim": {"de": "Beschneiden", "en": "Trim"},
    "staple_none": {"de": "Keine", "en": "None"},
    "staple_top_left": {"de": "Oben links", "en": "Top left"},
    "staple_top_right": {"de": "Oben rechts", "en": "Top right"},
    "staple_left_two": {"de": "Links, 2 Klammern", "en": "Left, 2 staples"},
    "staple_top_two": {"de": "Oben, 2 Klammern", "en": "Top, 2 staples"},
    "staple_saddle": {"de": "Rückstich", "en": "Saddle stitch"},
    "punch_none": {"de": "Keine", "en": "None"},
    "punch_two_left": {"de": "2 Löcher links", "en": "2 holes left"},
    "punch_four_left": {"de": "4 Löcher links", "en": "4 holes left"},
    "punch_two_top": {"de": "2 Löcher oben", "en": "2 holes top"},
    "fold_none": {"de": "Kein", "en": "None"},
    "fold_half": {"de": "Einbruchfalz", "en": "Half fold"},
    "fold_z": {"de": "Zickzackfalz", "en": "Z fold"},
    "out_embed": {"de": "JDF ins PDF einbetten", "en": "Embed JDF in PDF"},
    "out_sidecar": {"de": "JDF als eigene Datei", "en": "JDF as separate file"},
    "out_ticketing": {"de": "JDF+PDF in einer Datei (PRISMAsync)", "en": "JDF+PDF in one file (PRISMAsync)"},
    "out_pdfx_anyway": {
        "de": "Auch in PDF/X-1a/3/4 einbetten (bricht ggf. die Konformität)",
        "en": "Embed into PDF/X-1a/3/4 too (may break conformance)",
    },
    "output_intent": {"de": "Output Intent: {0}", "en": "Output intent: {0}"},
    "none": {"de": "keiner", "en": "none"},
    "set_output_intent": {"de": "Output Intent setzen …", "en": "Set output intent …"},
    "icc_filter": {"de": "ICC-Profile (*.icc *.icm)", "en": "ICC profiles (*.icc *.icm)"},
    "identifier": {"de": "Kennung (z. B. FOGRA39)", "en": "Identifier (e.g. FOGRA39)"},
    "write_output": {"de": "Ausgeben …", "en": "Write output …"},
    "pdfx_skipped": {
        "de": "{0}: JDF nicht eingebettet, damit das PDF konform bleibt. Separate Datei bzw. JDF-Ticketing nutzen.",
        "en": "{0}: JDF not embedded to keep the PDF conformant. Use the separate file or JDF ticketing.",
    },
    "written": {"de": "Geschrieben: {0}", "en": "Written: {0}"},
    # Paket 2: Komposition, Abschnitte, Geometrie, Arbeitsbereich
    "edit": {"de": "&Bearbeiten", "en": "&Edit"},
    "view": {"de": "&Ansicht", "en": "&View"},
    "undo": {"de": "Rückgängig", "en": "Undo"},
    "redo": {"de": "Wiederholen", "en": "Redo"},
    "open_project": {"de": "Projekt öffnen …", "en": "Open project …"},
    "save_project": {"de": "Projekt speichern …", "en": "Save project …"},
    "project_filter": {"de": "JDF-PDF-Projekte (*.jdfproj)", "en": "JDF-PDF projects (*.jdfproj)"},
    "duplicate_pages": {"de": "Seiten duplizieren", "en": "Duplicate pages"},
    "insert_blank": {"de": "Leerseite einfügen", "en": "Insert blank page"},
    "replace_page": {"de": "Seite ersetzen …", "en": "Replace page …"},
    "insert_images": {"de": "Bilder als Seiten einfügen …", "en": "Insert images as pages …"},
    "image_filter": {"de": "Bilder (*.jpg *.jpeg *.png *.tif *.tiff)", "en": "Images (*.jpg *.jpeg *.png *.tif *.tiff)"},
    "scale_pages": {"de": "Format / Skalieren …", "en": "Page size / scale …"},
    "scale_title": {"de": "Seitenformat ändern", "en": "Change page size"},
    "preset": {"de": "Vorlage", "en": "Preset"},
    "scale_mode": {"de": "Modus", "en": "Mode"},
    "scale_fit": {"de": "Einpassen", "en": "Fit"},
    "scale_fill": {"de": "Füllen", "en": "Fill"},
    "scale_actual": {"de": "100 %, nur Format", "en": "100 %, size only"},
    "scale_stretch": {"de": "Verzerren", "en": "Stretch"},
    "scope": {"de": "Seiten", "en": "Pages"},
    "scope_selection": {"de": "Ausgewählte Seiten", "en": "Selected pages"},
    "scope_all": {"de": "Alle Seiten", "en": "All pages"},
    "shift_content": {"de": "Inhalt verschieben …", "en": "Shift content …"},
    "shift_title": {"de": "Inhalt verschieben", "en": "Shift content"},
    "shift_x": {"de": "Horizontal", "en": "Horizontal"},
    "shift_y": {"de": "Vertikal", "en": "Vertical"},
    "shift_mirror": {"de": "Gerade Seiten spiegeln (Bundzugabe)", "en": "Mirror even pages (gutter)"},
    "edit_boxes": {"de": "Seitenboxen …", "en": "Page boxes …"},
    "boxes_title": {"de": "Seitenboxen", "en": "Page boxes"},
    "box_left": {"de": "Links", "en": "Left"},
    "box_bottom": {"de": "Unten", "en": "Bottom"},
    "box_right": {"de": "Rechts", "en": "Right"},
    "box_top": {"de": "Oben", "en": "Top"},
    "add_bleed": {"de": "Beschnitt hinzufügen …", "en": "Add bleed …"},
    "bleed_title": {"de": "Beschnitt hinzufügen", "en": "Add bleed"},
    "bleed": {"de": "Beschnitt", "en": "Bleed"},
    "sections": {"de": "Abschnitte", "en": "Sections"},
    "new_section": {"de": "Abschnitt ab Auswahl", "en": "Section at selection"},
    "rename": {"de": "Umbenennen", "en": "Rename"},
    "from_bookmarks": {"de": "Aus Lesezeichen", "en": "From bookmarks"},
    "section_name": {"de": "Abschnittsname", "en": "Section name"},
    "bookmark_depth": {"de": "Lesezeichen-Tiefe", "en": "Bookmark depth"},
    "zoom_in": {"de": "Vergrößern", "en": "Zoom in"},
    "zoom_out": {"de": "Verkleinern", "en": "Zoom out"},
    "zoom_fit": {"de": "Ganze Seite", "en": "Fit page"},
    "measure": {"de": "Messen", "en": "Measure"},
    "show_boxes": {"de": "Seitenboxen anzeigen", "en": "Show page boxes"},
    "clear_guides": {"de": "Hilfslinien entfernen", "en": "Clear guides"},
    "guides_hint": {
        "de": "Doppelklick: senkrechte Hilfslinie, Umschalt+Doppelklick: waagerechte",
        "en": "Double-click: vertical guide, Shift+double-click: horizontal",
    },
    "position": {"de": "x {0:.1f} mm, y {1:.1f} mm", "en": "x {0:.1f} mm, y {1:.1f} mm"},
    "measured": {"de": "Δx {0:.1f} mm, Δy {1:.1f} mm, Länge {2:.1f} mm", "en": "Δx {0:.1f} mm, Δy {1:.1f} mm, length {2:.1f} mm"},
    "unsupported_file": {"de": "Nicht unterstütztes Format: {0}", "en": "Unsupported format: {0}"},
}


class Translator:
    """Übersetzt Schlüssel; gebundene Widgets werden beim Sprachwechsel neu beschriftet."""

    def __init__(self, language: str = "de") -> None:
        self.language = language if language in LANGUAGES else "de"
        self._bindings: list[tuple[object, str, str]] = []

    def bind(self, widget: object, key: str, setter: str = "setText") -> object:
        self._bindings.append((widget, setter, key))
        getattr(widget, setter)(self(key))
        return widget

    def set_language(self, language: str) -> None:
        self.language = language if language in LANGUAGES else "de"
        for widget, setter, key in self._bindings:
            try:
                getattr(widget, setter)(self(key))
            except RuntimeError:  # Widget wurde bereits gelöscht
                pass

    def __call__(self, key: str, *args: object, **kwargs: object) -> str:
        entry = _TEXTS.get(key)
        text = entry.get(self.language, entry["de"]) if entry else key
        return text.format(*args, **kwargs) if (args or kwargs) else text
