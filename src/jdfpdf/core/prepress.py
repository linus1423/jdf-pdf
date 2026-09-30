"""Ausgabe: JDF erzeugen und mit dem PDF ausliefern, einzeln oder im Stapel.

Drei Ausgabewege, frei kombinierbar:

- **eingebettet**: JDF als Anhang/Associated File im PDF
- **separat**: ``<name>.jdf`` neben dem PDF (Hotfolder mit getrenntem Ticket)
- **JDF-Ticketing**: eine Datei aus JDF-Ticket und direkt angehängten PDF-Daten,
  das Ticket verweist per ``cid:`` auf das Dokument. So erwartet es Canon
  PRISMAsync laut Doku („JDF ticketing“).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from .jdf import MIME_TYPE, JobTicket, build_jdf
from .media import Media
from .pdfdoc import PdfDocument

JDF_ATTACHMENT_NAME = "job.jdf"
TICKETING_CID = "cid:doc@jdfpdf"
TICKETING_SUFFIX = "_prismasync.jdf"

# PDF/X-Versionen vor PDF/X-6 kennen keine Associated Files.
_PDFX_WITHOUT_AF = ("PDF/X-1", "PDF/X-3", "PDF/X-4", "PDF/X-5")


class PdfxPolicy(str, Enum):
    KEEP = "keep"  # bei PDF/X < 6 nicht einbetten, damit das PDF konform bleibt
    EMBED_ANYWAY = "embed_anyway"  # trotzdem einbetten, mit Warnung


@dataclass
class OutputOptions:
    embed: bool = True
    sidecar: bool = True
    ticketing: bool = False
    pdfx_policy: PdfxPolicy = PdfxPolicy.KEEP

    @property
    def any(self) -> bool:
        return self.embed or self.sidecar or self.ticketing


@dataclass
class OutputResult:
    pdf: Path
    extra_files: list[Path] = field(default_factory=list)
    embedded: bool = False
    pdfx_version: str | None = None
    warnings: list[str] = field(default_factory=list)


def blocks_embedding(doc: PdfDocument) -> str | None:
    """PDF/X-Version, falls sie eingebettete Dateien nicht vorsieht."""
    version = doc.pdfx_version()
    if version and version.startswith(_PDFX_WITHOUT_AF):
        return version
    return None


def complete_ticket(doc: PdfDocument, ticket: JobTicket) -> JobTicket:
    """Seitenzahl und Format aus dem Dokument ergänzen."""
    media = ticket.media
    if media is None:
        width, height = doc.page_size(0)
        media = Media("default", width_pt=width, height_pt=height)
    return replace(ticket, media=media, page_count=doc.page_count)


def ticketing_bytes(doc: PdfDocument, ticket: JobTicket) -> bytes:
    """JDF-Ticket und PDF-Daten zu einer Datei verketten."""
    jdf = build_jdf(replace(complete_ticket(doc, ticket), pdf_url=TICKETING_CID))
    return jdf + doc.to_bytes()


def write_output(doc: PdfDocument, ticket: JobTicket, pdf_path: Path, options: OutputOptions) -> OutputResult:
    """PDF samt JDF gemäß ``options`` schreiben."""
    if not options.any:
        raise ValueError("Keine Ausgabe gewählt")
    pdf_path = Path(pdf_path)
    ticket = complete_ticket(doc, replace(ticket, pdf_url=pdf_path.name))
    result = OutputResult(pdf=pdf_path, pdfx_version=doc.pdfx_version())

    blocking = blocks_embedding(doc)
    if options.embed:
        if blocking and options.pdfx_policy == PdfxPolicy.KEEP:
            result.warnings.append(f"pdfx_skipped:{blocking}")
            doc.detach(JDF_ATTACHMENT_NAME)
        else:
            if blocking:
                result.warnings.append(f"pdfx_embed:{blocking}")
            doc.attach(JDF_ATTACHMENT_NAME, build_jdf(ticket), MIME_TYPE, description="JDF job ticket")
            result.embedded = True
    else:
        doc.detach(JDF_ATTACHMENT_NAME)

    doc.save(pdf_path)

    if options.sidecar:
        sidecar = pdf_path.with_suffix(".jdf")
        sidecar.write_bytes(build_jdf(ticket))
        result.extra_files.append(sidecar)
    if options.ticketing:
        combined = pdf_path.with_name(pdf_path.stem + TICKETING_SUFFIX)
        combined.write_bytes(ticketing_bytes(doc, ticket))
        result.extra_files.append(combined)
    return result


def process_file(src: Path, dst_dir: Path, ticket: JobTicket, options: OutputOptions | None = None) -> OutputResult:
    """Ein PDF für den Stapel verarbeiten; Auftragsname ist der Dateiname, falls leer."""
    doc = PdfDocument.open(src)
    dst_dir.mkdir(parents=True, exist_ok=True)
    ticket = replace(ticket, job_name=ticket.job_name or Path(src).stem)
    return write_output(doc, ticket, dst_dir / Path(src).name, options or OutputOptions())
