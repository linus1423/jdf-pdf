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

    def insert_pages_from(self, other: "PdfDocument", at: int | None = None) -> None:
        at = self.page_count if at is None else at
        for offset, page in enumerate(other._pdf.pages):
            self._pdf.pages.insert(at + offset, page)

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
