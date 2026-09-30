"""Farbe: Farbseiten erkennen, Seiten in Graustufen umwandeln, Farb-/Schwarzweiß-Split und Merge.

- ``detect_color_pages`` rendert jede Seite klein (PDFium) und prüft, ob Pixel
  eine nennenswerte Buntheit haben.
- ``convert_to_gray`` schreibt die Farboperatoren der Inhaltsströme (auch in
  Formularen und Ebenen) nach DeviceGray um und ersetzt Bilder durch Graustufenbilder.
  Geteilte Ressourcen werden kopiert, nicht gewählte Seiten bleiben farbig.
  Verläufe werden neu abgetastet; Gitterverläufe und Inline-Bilder bleiben
  unverändert und werden gemeldet.
- ``split_by_color``/``merge_split`` teilen einen Auftrag in einen Farb- und einen
  Schwarzweißteil und setzen ihn wieder zusammen; die Reihenfolge steht im Merge-Plan.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from pikepdf import Name
from PIL import ImageChops

from .colorspace import family, to_gray
from .jdf import ColorModel, JobTicket, MediaRange
from .pdfdoc import PdfDocument
from .pdfimage import load_image, write_image
from .remap import Remapper

COLOR = "color"
BW = "bw"


# --- Erkennung ----------------------------------------------------------------


def _is_colored(image, tolerance: int, min_fraction: float) -> bool:
    r, g, b = image.convert("RGB").split()
    chroma = ImageChops.lighter(
        ImageChops.lighter(ImageChops.difference(r, g), ImageChops.difference(g, b)), ImageChops.difference(r, b)
    )
    count = sum(chroma.histogram()[tolerance:])
    return count >= max(3, min_fraction * image.width * image.height)


def detect_color_pages(doc: PdfDocument, dpi: float = 50, tolerance: int = 24,
                       min_fraction: float = 0.0001) -> list[int]:
    """Indizes der Seiten, auf denen sichtbar Farbe vorkommt."""
    rendered = pdfium.PdfDocument(doc.to_bytes())
    try:
        result = []
        for index in range(len(rendered)):
            page = rendered[index]
            image = page.render(scale=dpi / 72).to_pil()
            page.close()
            if _is_colored(image, tolerance, min_fraction):
                result.append(index)
        return result
    finally:
        rendered.close()


# --- Graustufen -----------------------------------------------------------------


@dataclass
class GrayReport:
    pages: int = 0
    images: int = 0
    skipped: Counter = field(default_factory=Counter)  # "shading", "pattern", "inline_image", "image"


def _gray_image(pdf: pikepdf.Pdf, skipped: Counter):
    def convert(obj):
        space = obj.get("/ColorSpace")
        if space is None or family(space) in ("DeviceGray", "CalGray") or (
                family(space) == "ICCBased" and int(space[1].N) == 1):
            return obj
        image = load_image(obj)
        if image is None:
            skipped["image"] += 1
            return None
        return write_image(pdf, image.convert("L"), obj, Name.DeviceGray)

    return convert


def convert_to_gray(doc: PdfDocument, indices: list[int]) -> GrayReport:
    """Seiten ``indices`` nach Graustufen umwandeln."""
    skipped: Counter = Counter()
    remapper = Remapper(doc.pdf, to_gray, _gray_image(doc.pdf, skipped))
    for index in sorted(set(indices)):
        remapper.page(doc.pdf.pages[index])
    return GrayReport(remapper.pages, remapper.images, remapper.skipped + skipped)


# --- Split und Merge -------------------------------------------------------------


@dataclass
class SplitPlan:
    """Reihenfolge der Seiten im Original: (Teil, Seitenindex im Teil)."""

    order: list[tuple[str, int]]

    def to_json(self) -> str:
        return json.dumps({"order": [{"part": p, "page": i + 1} for p, i in self.order]}, indent=1)

    @classmethod
    def from_json(cls, text: str) -> "SplitPlan":
        return cls([(item["part"], item["page"] - 1) for item in json.loads(text)["order"]])

    def summary(self) -> str:
        """Kurzform für das JDF, z. B. ``color:1-2 bw:1-4 color:3``."""
        runs: list[list] = []
        for part, index in self.order:
            if runs and runs[-1][0] == part and runs[-1][2] == index - 1:
                runs[-1][2] = index
            else:
                runs.append([part, index, index])
        return " ".join(f"{p}:{a + 1}" + (f"-{b + 1}" if b > a else "") for p, a, b in runs)


def color_units(color_pages: list[int], page_count: int, duplex: bool) -> set[int]:
    """Farbseiten; bei Duplex jeweils ganze Bögen (Vorder- und Rückseite)."""
    pages = set(color_pages)
    if duplex:
        pages |= {i ^ 1 for i in pages if (i ^ 1) < page_count}
    return pages


def _subset(doc: PdfDocument, keep: list[int]) -> PdfDocument:
    part = PdfDocument.from_bytes(doc.to_bytes())
    part.delete_pages([i for i in range(doc.page_count) if i not in set(keep)])
    return part


def split_by_color(doc: PdfDocument, color_pages: list[int], duplex: bool = False,
                   gray_bw: bool = True) -> tuple[PdfDocument | None, PdfDocument | None, SplitPlan]:
    """Dokument in Farb- und Schwarzweißteil aufteilen; ``gray_bw`` wandelt den s/w-Teil in Graustufen."""
    color = color_units(color_pages, doc.page_count, duplex)
    color_idx = [i for i in range(doc.page_count) if i in color]
    bw_idx = [i for i in range(doc.page_count) if i not in color]
    order, counters = [], {COLOR: 0, BW: 0}
    for i in range(doc.page_count):
        part = COLOR if i in color else BW
        order.append((part, counters[part]))
        counters[part] += 1
    color_doc = _subset(doc, color_idx) if color_idx else None
    bw_doc = _subset(doc, bw_idx) if bw_idx else None
    if bw_doc is not None and gray_bw:
        convert_to_gray(bw_doc, list(range(bw_doc.page_count)))
    return color_doc, bw_doc, SplitPlan(order)


def merge_split(color_doc: PdfDocument | None, bw_doc: PdfDocument | None, plan: SplitPlan) -> PdfDocument:
    """Farb- und Schwarzweißteil in der ursprünglichen Reihenfolge zusammenführen."""
    base = color_doc or bw_doc
    if base is None:
        raise ValueError("Keine Teile zum Zusammenführen")
    result = PdfDocument.from_bytes(base.to_bytes())
    parts = {COLOR: color_doc, BW: bw_doc}
    result.delete_pages(list(range(result.page_count)))
    for part, index in plan.order:
        source = parts[part]
        if source is None:
            raise ValueError(f"Teil {part} fehlt")
        result.pdf.pages.append(source.pdf.pages[index])
    return result


def _remap_ranges(ranges: list[MediaRange], mapping: dict[int, int]) -> list[MediaRange]:
    """Medienbereiche auf die Seiten eines Teils übertragen."""
    result = []
    for r in ranges:
        pages = sorted(mapping[i] for i in range(r.first, r.last + 1) if i in mapping)
        start = None
        for j, page in enumerate(pages):
            if start is None:
                start = prev = page
            elif page != prev + 1:
                result.append(MediaRange(start, prev, r.media))
                start = page
            prev = page
            if j == len(pages) - 1:
                result.append(MediaRange(start, prev, r.media))
    return result


SPLIT_SUFFIX = {COLOR: "_color", BW: "_bw"}
MERGE_SUFFIX = "_merge.json"


def write_split(doc: PdfDocument, ticket: JobTicket, pdf_path: Path, options, color_pages: list[int],
                duplex: bool = False, gray_bw: bool = True) -> list:
    """Farb- und s/w-Auftrag mit je eigenem JDF schreiben, dazu den Merge-Plan als JSON."""
    from .prepress import write_output

    pdf_path = Path(pdf_path)
    color_doc, bw_doc, plan = split_by_color(doc, color_pages, duplex, gray_bw)
    results = []
    for part, part_doc, model in ((COLOR, color_doc, ColorModel.CMYK), (BW, bw_doc, ColorModel.GRAY)):
        if part_doc is None:
            continue
        mapping = {orig: idx for orig, (p, idx) in enumerate(plan.order) if p == part}
        comment = "; ".join(filter(None, [ticket.comment, f"Merge {plan.summary()}"]))
        part_ticket = replace(ticket, color=model, job_name=f"{ticket.job_name}{SPLIT_SUFFIX[part]}",
                              media_ranges=_remap_ranges(ticket.media_ranges, mapping), comment=comment)
        target = pdf_path.with_name(pdf_path.stem + SPLIT_SUFFIX[part] + pdf_path.suffix)
        results.append(write_output(part_doc, part_ticket, target, options))
    merge = pdf_path.with_name(pdf_path.stem + MERGE_SUFFIX)
    merge.write_text(plan.to_json(), encoding="utf-8")
    if results:
        results[-1].extra_files.append(merge)
    return results
