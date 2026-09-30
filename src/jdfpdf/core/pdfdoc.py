"""PDF-Dokumentmodell: Seiten bearbeiten und Dateien als Anhang einbetten.

Arbeitet auf pikepdf (qpdf), damit Seiteninhalte nie neu geschrieben werden und
Farbräume, Output Intents und Schriften unangetastet bleiben.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Name


BOXES = ("MediaBox", "CropBox", "BleedBox", "TrimBox", "ArtBox")


@dataclass(frozen=True)
class Section:
    title: str
    page: int  # 0-basierte Startseite


@dataclass(frozen=True)
class Attachment:
    name: str
    mime_type: str | None
    size: int
    description: str | None
    relationship: str | None


class PdfDocument:
    """Ein geöffnetes PDF, das bearbeitet und gespeichert werden kann."""

    def __init__(self, pdf: pikepdf.Pdf, path: Path | None = None) -> None:
        self._pdf = pdf
        self.path = path

    @classmethod
    def open(cls, path: str | Path) -> "PdfDocument":
        path = Path(path)
        # Vollständig in den Speicher laden, damit die Quelldatei überschrieben werden darf.
        return cls(pikepdf.Pdf.open(io.BytesIO(path.read_bytes())), path)

    @classmethod
    def from_bytes(cls, data: bytes) -> "PdfDocument":
        return cls(pikepdf.Pdf.open(io.BytesIO(data)))

    @property
    def pdf(self) -> pikepdf.Pdf:
        """Das zugrunde liegende pikepdf-Dokument (für Module in ``core``)."""
        return self._pdf

    # --- Seiten -------------------------------------------------------------

    @property
    def page_count(self) -> int:
        return len(self._pdf.pages)

    def page_size(self, index: int) -> tuple[float, float]:
        """Größe der TrimBox (sonst MediaBox) in Punkt, unter Berücksichtigung der Drehung."""
        page = self._pdf.pages[index]
        box = page.obj.get("/TrimBox", page.mediabox)
        width = float(box[2]) - float(box[0])
        height = float(box[3]) - float(box[1])
        if self.page_rotation(index) % 180:
            width, height = height, width
        return width, height

    def page_rotation(self, index: int) -> int:
        return int(self._pdf.pages[index].obj.get("/Rotate", 0)) % 360

    def rotate_page(self, index: int, degrees: int) -> None:
        if degrees % 90:
            raise ValueError("Drehung nur in 90°-Schritten möglich")
        self._pdf.pages[index].rotate(degrees, relative=True)

    def delete_pages(self, indices: list[int]) -> None:
        for index in sorted(set(indices), reverse=True):
            del self._pdf.pages[index]

    def move_page(self, src: int, dst: int) -> None:
        """Seite von Position ``src`` an Position ``dst`` verschieben."""
        if src == dst:
            return
        page = self._pdf.pages[src]
        copy = pikepdf.Page(page.obj)
        del self._pdf.pages[src]
        self._pdf.pages.insert(dst, copy)

    def reorder(self, order: list[int]) -> None:
        """Seiten in die angegebene Reihenfolge bringen (Liste alter Indizes)."""
        if sorted(order) != list(range(self.page_count)):
            raise ValueError("Reihenfolge muss jede Seite genau einmal enthalten")
        pages = [self._pdf.pages[i].obj for i in order]
        for _ in range(self.page_count):
            del self._pdf.pages[0]
        for obj in pages:
            self._pdf.pages.append(pikepdf.Page(obj))

    def insert_pages_from(
        self, other: "PdfDocument", at: int | None = None, section: str | None = None
    ) -> None:
        """Alle Seiten von ``other`` einfügen; optional als neuen Abschnitt kennzeichnen."""
        at = self.page_count if at is None else at
        sections = self.sections()
        shift = other.page_count
        for offset, page in enumerate(other._pdf.pages):
            self._pdf.pages.insert(at + offset, page)
        # Abschnitte hinter der Einfügestelle verschieben sich; Lesezeichen zeigen auf
        # Seitenobjekte und bleiben daher gültig, nur neu anlegen müssen wir den Abschnitt.
        if section:
            updated = [s for s in sections if s.page < at] + [Section(section, at)]
            updated += [Section(s.title, s.page + shift) for s in sections if s.page >= at]
            self.set_sections(updated)

    def duplicate_pages(self, indices: list[int]) -> list[int]:
        """Kopien der Seiten hinter der letzten gewählten Seite einfügen; liefert ihre Indizes."""
        indices = sorted(set(indices))
        if not indices:
            return []
        at = indices[-1] + 1
        for offset, index in enumerate(indices):
            # flache Kopie: Inhalt und Ressourcen werden geteilt, das Seiten-Dictionary nicht
            page = pikepdf.Page(self._pdf.make_indirect(pikepdf.Dictionary(self._pdf.pages[index].obj)))
            self._pdf.pages.insert(at + offset, page)
        return list(range(at, at + len(indices)))

    def insert_blank(self, at: int, width: float | None = None, height: float | None = None) -> None:
        """Leere Seite einfügen; Format der Nachbarseite, falls nicht angegeben."""
        if width is None or height is None:
            ref = min(max(at - 1, 0), self.page_count - 1)
            box = self._pdf.pages[ref].mediabox if self.page_count else [0, 0, 595.276, 841.89]
            width, height = float(box[2]) - float(box[0]), float(box[3]) - float(box[1])
        page = pikepdf.Dictionary(
            Type=Name.Page,
            MediaBox=pikepdf.Array([0, 0, width, height]),
            Resources=pikepdf.Dictionary(),
            Contents=self._pdf.make_stream(b""),
        )
        self._pdf.pages.insert(at, pikepdf.Page(self._pdf.make_indirect(page)))

    def replace_page(self, index: int, other: "PdfDocument", other_index: int = 0) -> None:
        """Seite ``index`` durch eine Seite aus ``other`` ersetzen."""
        self._pdf.pages.insert(index, other._pdf.pages[other_index])
        del self._pdf.pages[index + 1]

    def insert_images(self, paths: list[str | Path], at: int | None = None) -> int:
        """Bilder (JPEG, PNG, auch mehrseitiges TIFF) als Seiten einfügen; liefert die Seitenzahl."""
        from .images import images_to_pdf

        other = PdfDocument.from_bytes(images_to_pdf(paths))
        self.insert_pages_from(other, at)
        return other.page_count

    # --- Abschnitte (oberste Lesezeichenebene) --------------------------------

    def sections(self) -> list[Section]:
        """Abschnitte = Lesezeichen der obersten Ebene, sortiert nach Startseite."""
        result = []
        page_index = {p.obj.objgen: i for i, p in enumerate(self._pdf.pages)}
        with self._pdf.open_outline() as outline:
            for item in outline.root:
                index = self._outline_page(item, page_index)
                if index is not None:
                    result.append(Section(item.title, index))
        return sorted(result, key=lambda s: s.page)

    def set_sections(self, sections: list[Section]) -> None:
        """Oberste Lesezeichenebene durch die Abschnitte ersetzen; Unterlesezeichen bleiben erhalten."""
        page_index = {p.obj.objgen: i for i, p in enumerate(self._pdf.pages)}
        with self._pdf.open_outline() as outline:
            children = {}
            for item in outline.root:
                index = self._outline_page(item, page_index)
                children[(item.title, index)] = list(item.children)
            outline.root.clear()
            for section in sorted(sections, key=lambda s: s.page):
                if not 0 <= section.page < self.page_count:
                    continue
                item = pikepdf.OutlineItem(section.title, section.page)
                item.children.extend(children.get((section.title, section.page), []))
                outline.root.append(item)

    def sections_from_bookmarks(self, level: int = 1) -> list[Section]:
        """Lesezeichen bis zur Tiefe ``level`` flach als Abschnitte übernehmen."""
        found: list[Section] = []
        page_index = {p.obj.objgen: i for i, p in enumerate(self._pdf.pages)}

        def walk(items, depth: int) -> None:
            for item in items:
                index = self._outline_page(item, page_index)
                if index is not None:
                    found.append(Section(item.title, index))
                if depth < level:
                    walk(item.children, depth + 1)

        with self._pdf.open_outline() as outline:
            walk(outline.root, 1)
        unique = {s.page: s for s in reversed(found)}  # je Seite der erste Eintrag
        result = sorted(unique.values(), key=lambda s: s.page)
        self.set_sections(result)
        return result

    def section_of_page(self, index: int) -> Section | None:
        current = None
        for section in self.sections():
            if section.page <= index:
                current = section
        return current

    def _outline_page(self, item: pikepdf.OutlineItem, page_index: dict) -> int | None:
        dest = item.destination
        if dest is None and item.action is not None and item.action.get("/S") == Name.GoTo:
            dest = item.action.get("/D")
        if isinstance(dest, int):
            return dest if 0 <= dest < self.page_count else None
        if isinstance(dest, (pikepdf.Array, list)) and len(dest) > 0:
            target = dest[0]
            if isinstance(target, int):
                return target
            if isinstance(target, pikepdf.Object) and target.is_indirect:
                return page_index.get(target.objgen)
        if isinstance(dest, (str, pikepdf.String)):
            names = self._pdf.Root.get("/Names", {}).get("/Dests")
            if names is not None:
                resolved = pikepdf.NameTree(names).get(str(dest))
                if resolved is not None:
                    if isinstance(resolved, pikepdf.Dictionary):
                        resolved = resolved.get("/D")
                    if resolved is not None and len(resolved) and resolved[0].is_indirect:
                        return page_index.get(resolved[0].objgen)
        return None

    # --- Seitenboxen ----------------------------------------------------------

    def box(self, index: int, name: str) -> tuple[float, float, float, float]:
        """Box ``name`` einer Seite; fehlende Boxen fallen nach PDF-Regeln auf Crop-/MediaBox zurück."""
        if name not in BOXES:
            raise ValueError(f"Unbekannte Box {name}")
        page = self._pdf.pages[index].obj
        value = page.get("/" + name)
        if value is None and name != "MediaBox":
            fallback = "MediaBox" if name == "CropBox" else "CropBox"
            return self.box(index, fallback)
        if value is None:  # MediaBox kann geerbt sein
            value = self._pdf.pages[index].mediabox
        x0, y0, x1, y1 = (float(v) for v in value)
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def has_box(self, index: int, name: str) -> bool:
        return ("/" + name) in self._pdf.pages[index].obj

    def set_box(self, index: int, name: str, rect: tuple[float, float, float, float] | None) -> None:
        if name not in BOXES:
            raise ValueError(f"Unbekannte Box {name}")
        page = self._pdf.pages[index].obj
        if rect is None:
            if name == "MediaBox":
                raise ValueError("MediaBox kann nicht entfernt werden")
            if "/" + name in page:
                del page["/" + name]
            return
        x0, y0, x1, y1 = rect
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Box muss eine positive Größe haben")
        page["/" + name] = pikepdf.Array([x0, y0, x1, y1])

    def add_bleed(self, index: int, bleed_pt: float) -> None:
        """Beschnitt ergänzen: TrimBox = bisheriges Format, Media-/BleedBox um ``bleed_pt`` vergrößert.

        Der Seiteninhalt reicht danach nur dann in den Beschnitt, wenn er schon über
        das Format hinaus angelegt war; sonst bleibt der Rand weiß.
        """
        trim = self.box(index, "TrimBox") if self.has_box(index, "TrimBox") else self.box(index, "CropBox")
        x0, y0, x1, y1 = trim
        bleed = (x0 - bleed_pt, y0 - bleed_pt, x1 + bleed_pt, y1 + bleed_pt)
        media = self.box(index, "MediaBox")
        self.set_box(index, "TrimBox", trim)
        self.set_box(index, "BleedBox", bleed)
        self.set_box(
            index,
            "MediaBox",
            (min(media[0], bleed[0]), min(media[1], bleed[1]), max(media[2], bleed[2]), max(media[3], bleed[3])),
        )
        if self.has_box(index, "CropBox"):
            self.set_box(index, "CropBox", None)

    # --- PDF/X --------------------------------------------------------------

    def pdfx_version(self) -> str | None:
        """PDF/X-Kennung aus XMP bzw. Info-Dictionary, z. B. ``PDF/X-4``."""
        try:
            with self._pdf.open_metadata() as meta:
                value = meta.get("pdfxid:GTS_PDFXVersion") or meta.get("pdfx:GTS_PDFXVersion")
                if value:
                    return str(value)
        except Exception:  # defektes XMP ist kein Grund zum Abbruch
            pass
        info = self._pdf.docinfo.get("/GTS_PDFXVersion")
        return str(info) if info is not None else None

    def has_pdfx_output_intent(self) -> bool:
        intents = self._pdf.Root.get("/OutputIntents", [])
        return any(intent.get("/S") == Name.GTS_PDFX for intent in intents)

    def output_intent(self) -> dict[str, str] | None:
        """Erster PDF/X-Output-Intent: Kennung, Info und ob ein ICC-Profil eingebettet ist."""
        for intent in self._pdf.Root.get("/OutputIntents", []):
            if intent.get("/S") == Name.GTS_PDFX:
                return {
                    "identifier": str(intent.get("/OutputConditionIdentifier", "")),
                    "info": str(intent.get("/Info", "")),
                    "profile": "yes" if "/DestOutputProfile" in intent else "no",
                }
        return None

    def set_output_intent(self, icc_profile: bytes, identifier: str, components: int = 4, info: str = "") -> None:
        """PDF/X-Output-Intent mit eingebettetem ICC-Profil setzen (ersetzt vorhandene)."""
        if components not in (1, 3, 4):
            raise ValueError("ICC-Profil muss 1, 3 oder 4 Komponenten haben")
        profile = self._pdf.make_stream(icc_profile)
        profile.N = components
        intent = pikepdf.Dictionary(
            Type=Name.OutputIntent,
            S=Name.GTS_PDFX,
            OutputConditionIdentifier=identifier,
            Info=info or identifier,
            DestOutputProfile=profile,
        )
        others = [i for i in self._pdf.Root.get("/OutputIntents", []) if i.get("/S") != Name.GTS_PDFX]
        self._pdf.Root.OutputIntents = pikepdf.Array([*others, intent])

    # --- Anhänge ------------------------------------------------------------

    def attach(
        self,
        name: str,
        data: bytes,
        mime_type: str,
        description: str | None = None,
        relationship: str = "Supplement",
    ) -> None:
        """Datei einbetten und zusätzlich als Associated File (PDF 2.0 / PDF/X-6) verknüpfen.

        Eine vorhandene Datei gleichen Namens wird ersetzt.
        """
        self.detach(name)
        spec = pikepdf.AttachedFileSpec(
            self._pdf, data, description=description or "", filename=name, mime_type=mime_type
        )
        self._pdf.attachments[name] = spec
        # Indirekt ablegen, damit Name-Tree und /AF auf dasselbe Objekt zeigen.
        # Neuere pikepdf-Versionen tragen /AF selbst ein, ältere nicht; daher neu aufbauen.
        spec_obj = self._find_filespec(name)
        if not spec_obj.is_indirect:
            spec_obj = self._pdf.make_indirect(spec_obj)
            pikepdf.NameTree(self._pdf.Root.Names.EmbeddedFiles)[name] = spec_obj
        spec_obj.AFRelationship = Name("/" + relationship)
        self._set_af([*self._af_without(name), spec_obj])

    def detach(self, name: str) -> None:
        if name in self._pdf.attachments:
            del self._pdf.attachments[name]
        self._set_af(self._af_without(name))

    def _af_without(self, name: str) -> list[pikepdf.Object]:
        af = self._pdf.Root.get("/AF", [])
        return [item for item in af if str(item.get("/UF", item.get("/F", ""))) != name]

    def _set_af(self, items: list[pikepdf.Object]) -> None:
        if items:
            self._pdf.Root.AF = pikepdf.Array(items)
        elif "/AF" in self._pdf.Root:
            del self._pdf.Root["/AF"]

    def attachments(self) -> list[Attachment]:
        result = []
        for name, spec in self._pdf.attachments.items():
            spec_obj = self._find_filespec(name)
            rel = spec_obj.get("/AFRelationship") if spec_obj is not None else None
            data = spec.get_file().read_bytes()
            result.append(
                Attachment(
                    name=name,
                    mime_type=spec.get_file().mime_type or None,
                    size=len(data),
                    description=spec.description or None,
                    relationship=str(rel)[1:] if rel is not None else None,
                )
            )
        return result

    def attachment_data(self, name: str) -> bytes:
        return self._pdf.attachments[name].get_file().read_bytes()

    def _find_filespec(self, name: str) -> pikepdf.Object | None:
        tree = self._pdf.Root.get("/Names", {}).get("/EmbeddedFiles")
        if tree is None:
            return None
        return pikepdf.NameTree(tree).get(name)

    # --- Speichern ----------------------------------------------------------

    def to_bytes(self) -> bytes:
        buf = io.BytesIO()
        self._pdf.save(buf)
        return buf.getvalue()

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_bytes(self.to_bytes())
        self.path = path
