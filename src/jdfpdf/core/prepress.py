"""Ausgabe: JDF erzeugen und mit dem PDF ausliefern, einzeln oder im Stapel.

Drei Ausgabewege, frei kombinierbar:

- **eingebettet**: JDF als Anhang/Associated File im PDF
- **separat**: ``<name>.jdf`` neben dem PDF (Hotfolder mit getrenntem Ticket)
- **JDF-Ticketing**: eine Datei aus JDF-Ticket und direkt angehängten PDF-Daten,
  das Ticket verweist per ``cid:`` auf das Dokument. So erwartet es Canon
  PRISMAsync laut Doku („JDF ticketing“).

Optional kommen CIP3-PPF-Dateien je Bogen (Offset-Farbzonen) und eine CSV mit den
Zonenwerten dazu; die PPF lässt sich auch einbetten.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .impose import Imposition, Layout, impose
from .jdf import MIME_TYPE, JobTicket, Sides, build_jdf
from .media import Media
from .pdfdoc import PdfDocument

if TYPE_CHECKING:
    from .ppf import PressProfile

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
    ppf: bool = False  # CIP3-PPF je Bogen neben das PDF schreiben
    ppf_embed: bool = False  # PPF zusätzlich ins PDF einbetten
    ppf_profile: "PressProfile | None" = None  # None: erstes Standardprofil
    preflight: bool = False  # Preflight-Bericht ``<name>_preflight.html`` schreiben
    language: str = "de"  # Sprache für Berichte

    @property
    def any(self) -> bool:
        return self.embed or self.sidecar or self.ticketing or self.ppf


@dataclass
class OutputResult:
    pdf: Path
    extra_files: list[Path] = field(default_factory=list)
    embedded: bool = False
    pdfx_version: str | None = None
    warnings: list[str] = field(default_factory=list)
    preflight: object | None = None  # PreflightReport, falls geprüft


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


def write_output(doc: PdfDocument, ticket: JobTicket, pdf_path: Path, options: OutputOptions,
                 imposition: Imposition | None = None) -> OutputResult:
    """PDF samt JDF gemäß ``options`` schreiben; mit ``imposition`` wird vorher ausgeschossen."""
    if not options.any:
        raise ValueError("Keine Ausgabe gewählt")
    pdf_path = Path(pdf_path)
    warnings = []
    if imposition is not None and imposition.layout != Layout.NONE:
        doc = impose(doc, imposition)
        if ticket.media_ranges:
            # Seitenbereiche beziehen sich auf die Einzelseiten, nicht auf die Bögen
            warnings.append("media_ranges_dropped:")
            ticket = replace(ticket, media_ranges=[])
        if ticket.media is not None and ticket.media.name == "default":
            ticket = replace(ticket, media=None)
    ticket = complete_ticket(doc, replace(ticket, pdf_url=pdf_path.name))
    result = OutputResult(pdf=pdf_path, pdfx_version=doc.pdfx_version(), warnings=warnings)

    if options.preflight:
        from .preflight import Severity, preflight, write_report

        report = preflight(doc, file=pdf_path.name)
        result.preflight = report
        result.extra_files.append(write_report(report, pdf_path.with_name(pdf_path.stem + "_preflight.html"),
                                               options.language))
        if not report.ok:
            result.warnings.append(f"preflight_errors:{report.count(Severity.ERROR)}")

    blocking = blocks_embedding(doc)
    ppf_files: list[tuple[str, bytes]] = []
    if options.ppf or options.ppf_embed:
        from . import ppf

        profile = options.ppf_profile or ppf.default_profiles()[0]
        sheets = ppf.analyse(doc, profile, duplex=ticket.sides != Sides.SIMPLEX)
        names = ppf.ppf_names(pdf_path.stem, len(sheets))
        ppf_files = [(name, ppf.write_ppf(sheet, ticket.job_name or pdf_path.stem, profile=profile))
                     for name, sheet in zip(names, sheets)]
        skipped = sum((side.skipped for sh in sheets for side in (sh.front, sh.back) if side), start=Counter())
        if skipped:
            result.warnings.append("ppf_skipped:" + ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())))
        if options.ppf:
            zones = pdf_path.with_name(pdf_path.stem + "_zones.csv")
            zones.write_text(ppf.zones_csv(sheets), encoding="utf-8")
            result.extra_files.append(zones)
        if options.ppf_embed:
            if blocking and options.pdfx_policy == PdfxPolicy.KEEP:
                result.warnings.append(f"pdfx_skipped:{blocking}")
            else:
                for name, data in ppf_files:
                    doc.attach(name, data, ppf.PPF_MIME, description="CIP3 PPF")
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

    if options.ppf:
        for name, data in ppf_files:
            target = pdf_path.with_name(name)
            target.write_bytes(data)
            result.extra_files.append(target)
    if options.sidecar:
        sidecar = pdf_path.with_suffix(".jdf")
        sidecar.write_bytes(build_jdf(ticket))
        result.extra_files.append(sidecar)
    if options.ticketing:
        combined = pdf_path.with_name(pdf_path.stem + TICKETING_SUFFIX)
        combined.write_bytes(ticketing_bytes(doc, ticket))
        result.extra_files.append(combined)
    return result


def process_file(src: Path, dst_dir: Path, ticket: JobTicket, options: OutputOptions | None = None,
                 imposition: Imposition | None = None) -> OutputResult:
    """Ein PDF für den Stapel verarbeiten; Auftragsname ist der Dateiname, falls leer."""
    doc = PdfDocument.open(src)
    dst_dir.mkdir(parents=True, exist_ok=True)
    ticket = replace(ticket, job_name=ticket.job_name or Path(src).stem)
    return write_output(doc, ticket, dst_dir / Path(src).name, options or OutputOptions(), imposition)
