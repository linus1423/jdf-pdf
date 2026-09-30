"""Zusammenführung: JDF erzeugen und in ein PDF einbetten, einzeln oder im Stapel."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .jdf import MIME_TYPE, JobTicket, build_jdf
from .pdfdoc import PdfDocument

JDF_ATTACHMENT_NAME = "job.jdf"

# PDF/X-Versionen vor PDF/X-6 kennen keine Associated Files. Ob eingebettete
# Dateien die Konformität dort brechen, ist noch gegen die Normen zu prüfen.
_PDFX_WITHOUT_AF = ("PDF/X-1", "PDF/X-3", "PDF/X-4", "PDF/X-5")


@dataclass
class EmbedResult:
    output: Path
    pdfx_version: str | None
    warnings: list[str]


def conformance_warnings(doc: PdfDocument) -> list[str]:
    version = doc.pdfx_version()
    if version and version.startswith(_PDFX_WITHOUT_AF):
        return [f"pdfx_embed:{version}"]
    return []


def embed_ticket(doc: PdfDocument, ticket: JobTicket) -> list[str]:
    """JDF zum Ticket erzeugen und als Anhang in ``doc`` einbetten."""
    if not ticket.media_width_pt or not ticket.media_height_pt:
        width, height = doc.page_size(0)
        ticket = replace(ticket, media_width_pt=width, media_height_pt=height)
    doc.attach(
        JDF_ATTACHMENT_NAME,
        build_jdf(ticket),
        MIME_TYPE,
        description="JDF job ticket",
    )
    return conformance_warnings(doc)


def process_file(src: Path, dst_dir: Path, ticket: JobTicket, write_sidecar: bool = True) -> EmbedResult:
    """Ein PDF verarbeiten: JDF einbetten, PDF speichern, optional JDF daneben ablegen.

    Die separate JDF-Datei ist für Hotfolder gedacht (z. B. PRISMAsync), die das
    Ticket neben dem PDF erwarten.
    """
    doc = PdfDocument.open(src)
    dst_dir.mkdir(parents=True, exist_ok=True)
    output = dst_dir / src.name
    ticket = replace(ticket, job_name=ticket.job_name or src.stem, pdf_url=output.name)
    warnings = embed_ticket(doc, ticket)
    doc.save(output)
    if write_sidecar:
        output.with_suffix(".jdf").write_bytes(doc.attachment_data(JDF_ATTACHMENT_NAME))
    return EmbedResult(output=output, pdfx_version=doc.pdfx_version(), warnings=warnings)
