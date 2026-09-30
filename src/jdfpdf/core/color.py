"""Farbe: Farbseiten erkennen, Seiten in Graustufen umwandeln, Farb-/Schwarzweiß-Split und Merge.

- ``detect_color_pages`` rendert jede Seite klein (PDFium) und prüft, ob Pixel
  eine nennenswerte Buntheit haben.
- ``convert_to_gray`` schreibt die Farboperatoren der Inhaltsströme (auch in
  Formularen und Ebenen) nach DeviceGray um und ersetzt Bilder durch Graustufenbilder.
  Geteilte Ressourcen werden kopiert, nicht gewählte Seiten bleiben farbig.
  Verläufe (``sh``/Muster) und Inline-Bilder bleiben unverändert und werden gemeldet.
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
from pikepdf import Name, Operator
from PIL import ImageChops

from .colorspace import family, initial_values, resolve, to_gray
from .jdf import ColorModel, JobTicket, MediaRange
from .pdfdoc import PdfDocument
from .pdfimage import load_image, stream_like, write_image

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


class _State:
    def __init__(self) -> None:
        self.stack: list[tuple] = []
        self.fill = None  # ursprünglicher Farbraum, falls umgesetzt; None = unverändert
        self.stroke = None

    def push(self) -> None:
        self.stack.append((self.fill, self.stroke))

    def pop(self) -> None:
        if self.stack:
            self.fill, self.stroke = self.stack.pop()


def _num(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 4)


class _GrayConverter:
    def __init__(self, pdf: pikepdf.Pdf, report: GrayReport) -> None:
        self.pdf = pdf
        self.report = report
        self.memo: dict = {}

    # Inhaltsströme
    def ops(self, stream, resources, state: _State) -> list:
        out = []
        for item in pikepdf.parse_content_stream(stream):
            if isinstance(item, pikepdf.ContentStreamInlineImage):
                self.report.skipped["inline_image"] += 1
                out.append(item)
                continue
            operands, op = list(item.operands), str(item.operator)
            if op == "q":
                state.push()
            elif op == "Q":
                state.pop()
            elif op in ("rg", "RG") and len(operands) == 3:
                gray = to_gray(Name.DeviceRGB, [float(v) for v in operands])
                out.append(([_num(gray)], Operator("g" if op == "rg" else "G")))
                self._set(state, op.islower(), None)
                continue
            elif op in ("k", "K") and len(operands) == 4:
                gray = to_gray(Name.DeviceCMYK, [float(v) for v in operands])
                out.append(([_num(gray)], Operator("g" if op == "k" else "G")))
                self._set(state, op.islower(), None)
                continue
            elif op in ("cs", "CS") and operands:
                space = resolve(resources, operands[0])
                start = initial_values(space) if space is not None else []
                if space is not None and family(space) not in ("DeviceGray", "Pattern") \
                        and to_gray(space, start) is not None:
                    self._set(state, op == "cs", space)
                    out.append(([Name.DeviceGray], Operator(op)))
                    if family(space) in ("Separation", "DeviceN"):
                        out.append(([_num(to_gray(space, start))], Operator("sc" if op == "cs" else "SC")))
                    continue
                self._set(state, op == "cs", None)
                if space is not None and family(space) == "Pattern":
                    self.report.skipped["pattern"] += 1
            elif op in ("sc", "scn", "SC", "SCN"):
                space = state.fill if op.islower() else state.stroke
                if space is not None and all(isinstance(v, (int, float, pikepdf.Object)) for v in operands):
                    try:
                        values = [float(v) for v in operands]
                    except (TypeError, ValueError):
                        values = None
                    gray = to_gray(space, values) if values else None
                    if gray is not None:
                        out.append(([_num(gray)], Operator("sc" if op.islower() else "SC")))
                        continue
            elif op in ("g", "G"):
                self._set(state, op == "g", None)
            elif op == "sh":
                self.report.skipped["shading"] += 1
            out.append(item)
        return out

    @staticmethod
    def _set(state: _State, fill: bool, space) -> None:
        if fill:
            state.fill = space
        else:
            state.stroke = space

    def new_content(self, stream, resources, state: _State) -> pikepdf.Stream:
        data = pikepdf.unparse_content_stream(self.ops(stream, resources, state))
        return stream_like(self.pdf, stream, data)

    # Ressourcen
    def resources(self, resources) -> pikepdf.Dictionary:
        new = pikepdf.Dictionary(resources) if resources is not None else pikepdf.Dictionary()
        xobjects = new.get("/XObject")
        if xobjects is not None:
            converted = pikepdf.Dictionary()
            for name, obj in xobjects.items():
                converted[name] = self.xobject(obj, resources)
            new.XObject = converted
        return new

    def xobject(self, obj, parent_resources):
        key = obj.objgen if obj.is_indirect else None
        if key is not None and key in self.memo:
            return self.memo[key]
        subtype = obj.get("/Subtype")
        if subtype == Name.Image:
            result = self.image(obj)
        elif subtype == Name.Form:
            result = self.form(obj, parent_resources)
        else:
            result = obj
        if key is not None:
            self.memo[key] = result
        return result

    def image(self, obj):
        space = obj.get("/ColorSpace")
        if obj.get("/ImageMask", False) or space is None:
            return obj
        if family(space) in ("DeviceGray", "CalGray") or (family(space) == "ICCBased" and int(space[1].N) == 1):
            return obj
        image = load_image(obj)
        if image is None:
            self.report.skipped["image"] += 1
            return obj
        self.report.images += 1
        return self.pdf.make_indirect(write_image(self.pdf, image.convert("L"), obj, Name.DeviceGray))

    def form(self, obj, parent_resources):
        own = obj.get("/Resources")
        resources = own if own is not None else parent_resources
        new = self.new_content(obj, resources, _State())
        new = self.pdf.make_indirect(new)
        if own is not None:
            new.Resources = self.resources(own)
        _gray_group(new)
        return new

    def page(self, page: pikepdf.Page) -> None:
        obj = page.obj
        resources = obj.get("/Resources")
        contents = obj.get("/Contents")
        state = _State()
        if isinstance(contents, pikepdf.Array):
            obj.Contents = pikepdf.Array([self.new_content(s, resources, state) for s in contents])
        elif contents is not None:
            obj.Contents = self.new_content(contents, resources, state)
        obj.Resources = self.resources(resources)
        _gray_group(obj)
        self.report.pages += 1


def _gray_group(obj) -> None:
    group = obj.get("/Group")
    if group is not None and "/CS" in group:
        group = pikepdf.Dictionary(group)
        group.CS = Name.DeviceGray
        obj.Group = group


def convert_to_gray(doc: PdfDocument, indices: list[int]) -> GrayReport:
    """Seiten ``indices`` nach Graustufen umwandeln."""
    report = GrayReport()
    converter = _GrayConverter(doc.pdf, report)
    for index in sorted(set(indices)):
        converter.page(doc.pdf.pages[index])
    return report


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
