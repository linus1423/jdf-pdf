"""Vorlagen: Bearbeitungsschritte, Auftrag und Ausgabe als JSON speichern und auf Dateien anwenden.

Eine Vorlage besteht aus

- ``steps``: Liste von Schritten ``{"op": "<name>", ...}`` (siehe ``OPS``),
- ``ticket``/``output``/``imposition``: wie in der Projektdatei,
- ``params``: Standardwerte für Platzhalter.

Werte der Form ``"${name}"`` werden beim Ausführen durch Parameter ersetzt (mit Typ),
``"${name}"`` innerhalb eines Textes als Text. Immer verfügbar: ``file`` (Dateiname
ohne Endung) und ``date``. Seitenangaben: ``"all"``, ``"odd"``, ``"even"``, ``"first"``,
``"last"`` oder Bereiche wie ``"1-3, 7"``.
"""

from __future__ import annotations

import json
import re
import typing
from dataclasses import dataclass, field, fields, is_dataclass, replace
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from . import color, elements, geometry, imagefix, impose, marks, spine, spot, tabs
from .impose import Imposition
from .jdf import JobTicket, MediaRange
from .media import MM, MediaCatalog
from .pagerange import parse_pages
from .pdfdoc import PdfDocument, Section
from .prepress import OutputOptions, OutputResult, write_output
from .project import _output_from_dict, _to_jsonable, imposition_from_dict, ticket_from_dict

SUFFIX = ".jdftpl"
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class Template:
    name: str = ""
    steps: list[dict] = field(default_factory=list)
    ticket: JobTicket | None = None
    output: OutputOptions | None = None
    imposition: Imposition | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        data = {
            "name": self.name, "params": self.params, "steps": self.steps,
            "ticket": _to_jsonable(self.ticket) if self.ticket else None,
            "output": _to_jsonable(self.output) if self.output else None,
            "imposition": _to_jsonable(self.imposition) if self.imposition else None,
        }
        return json.dumps(data, indent=1, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Template":
        data = json.loads(text)
        return cls(
            name=data.get("name", ""),
            steps=list(data.get("steps", [])),
            ticket=ticket_from_dict(data["ticket"]) if data.get("ticket") else None,
            output=_output_from_dict(data["output"]) if data.get("output") else None,
            imposition=imposition_from_dict(data["imposition"]) if data.get("imposition") else None,
            params=dict(data.get("params", {})),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Template":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


# --- Platzhalter ---------------------------------------------------------------------


def substitute(value: Any, params: dict[str, Any]) -> Any:
    """Platzhalter in ``value`` (auch verschachtelt) ersetzen."""
    if isinstance(value, str):
        whole = _PLACEHOLDER.fullmatch(value)
        if whole and whole.group(1) in params:
            return params[whole.group(1)]
        return _PLACEHOLDER.sub(lambda m: str(params.get(m.group(1), m.group(0))), value)
    if isinstance(value, list):
        return [substitute(v, params) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v, params) for k, v in value.items()}
    return value


def parse_param(text: str) -> tuple[str, Any]:
    """``name=wert`` aus der Kommandozeile; Zahlen, true/false und JSON werden umgewandelt."""
    name, sep, raw = text.partition("=")
    if not sep or not name:
        raise ValueError(f"Parameter muss name=wert sein: {text!r}")
    try:
        return name.strip(), json.loads(raw)
    except ValueError:
        return name.strip(), raw


def resolve_pages(spec, page_count: int) -> list[int]:
    if spec is None or spec == "all":
        return list(range(page_count))
    if isinstance(spec, list):
        return [int(i) for i in spec if 0 <= int(i) < page_count]
    spec = str(spec).strip().lower()
    if spec == "odd":
        return list(range(0, page_count, 2))
    if spec == "even":
        return list(range(1, page_count, 2))
    if spec == "first":
        return [0] if page_count else []
    if spec == "last":
        return [page_count - 1] if page_count else []
    return parse_pages(spec, page_count)


def build(cls, data: dict | None):
    """Dataclass aus einem Dict; Enums per Name oder Wert, unbekannte Schlüssel ignoriert."""
    data = data or {}
    hints = typing.get_type_hints(cls)
    values = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        hint = hints.get(f.name)
        enum = next((t for t in (typing.get_args(hint) or (hint,)) if isinstance(t, type) and issubclass(t, Enum)),
                    None)
        if enum is not None and isinstance(value, str):
            value = enum[value] if value in enum.__members__ else enum(value)
        elif isinstance(value, list) and "tuple" in str(hint):
            value = tuple(value)
        values[f.name] = value
    return cls(**values)


# --- Schritte ------------------------------------------------------------------------


@dataclass
class RunContext:
    ticket: JobTicket
    params: dict[str, Any]
    catalog: MediaCatalog | None = None
    base_dir: Path = Path(".")
    log: list[str] = field(default_factory=list)

    def path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.base_dir / path


def _pages(doc: PdfDocument, step: dict) -> list[int]:
    return resolve_pages(step.get("pages"), doc.page_count)


def _op_rotate(doc, step, ctx):
    for i in _pages(doc, step):
        doc.rotate_page(i, int(step.get("degrees", 90)))


def _op_delete(doc, step, ctx):
    pages = _pages(doc, step)
    if len(pages) >= doc.page_count:
        raise ValueError("Es können nicht alle Seiten gelöscht werden")
    doc.delete_pages(pages)


def _op_duplicate(doc, step, ctx):
    doc.duplicate_pages(_pages(doc, step))


def _op_insert_blank(doc, step, ctx):
    at = step.get("at")
    at = doc.page_count if at in (None, "end") else int(at)
    width, height = step.get("width_mm"), step.get("height_mm")
    for _ in range(int(step.get("count", 1))):
        doc.insert_blank(at, width * MM if width else None, height * MM if height else None)


def _op_insert_pdf(doc, step, ctx):
    at = step.get("at")
    doc.insert_pages_from(PdfDocument.open(ctx.path(step["path"])), None if at in (None, "end") else int(at),
                          step.get("section"))


def _op_insert_images(doc, step, ctx):
    at = step.get("at")
    doc.insert_images([ctx.path(p) for p in step["paths"]], None if at in (None, "end") else int(at))


def _op_scale(doc, step, ctx):
    mode = geometry.ScaleMode[step.get("mode", "FIT").upper()]
    for i in _pages(doc, step):
        geometry.scale_page(doc, i, step["width_mm"] * MM, step["height_mm"] * MM, mode)


def _op_shift(doc, step, ctx):
    dx, dy = step.get("dx_mm", 0) * MM, step.get("dy_mm", 0) * MM
    for i in _pages(doc, step):
        sign = -1 if step.get("mirror") and i % 2 == 1 else 1
        geometry.shift_content(doc, i, sign * dx, dy)


def _op_add_bleed(doc, step, ctx):
    for i in _pages(doc, step):
        doc.add_bleed(i, step.get("mm", 3) * MM)


def _op_set_box(doc, step, ctx):
    rect = step.get("rect_mm")
    for i in _pages(doc, step):
        doc.set_box(i, step["name"], tuple(v * MM for v in rect) if rect else None)


def _op_sections_from_bookmarks(doc, step, ctx):
    doc.set_sections(doc.sections_from_bookmarks(int(step.get("level", 1))))


def _op_sections(doc, step, ctx):
    doc.set_sections([Section(s["title"], int(s["page"]) - 1) for s in step["sections"]])


def _elements_ctx(ctx) -> elements.Context:
    return elements.Context(job=ctx.ticket.job_name, file=str(ctx.params.get("file", "")))


def _op_element(doc, step, ctx):
    element = build(elements.Element, step.get("element", {}))
    if element.image:
        element = replace(element, image=str(ctx.path(element.image)))
    elements.apply_element(doc, _pages(doc, step), element, _elements_ctx(ctx))


def _op_remove_elements(doc, step, ctx):
    elements.remove_elements(doc, _pages(doc, step))


def _op_marks(doc, step, ctx):
    opts = build(marks.MarkOptions, step.get("options", {}))
    fin = ctx.ticket.finishing
    info = {"job": ctx.ticket.job_name, "file": str(ctx.params.get("file", "")),
            "finishing": "-".join(v.value for v in (fin.staple, fin.punch, fin.fold) if v.value != "none")}
    marks.add_marks(doc, _pages(doc, step), opts, info)


def _op_remove_marks(doc, step, ctx):
    marks.remove_marks(doc, _pages(doc, step))


def _op_tab_sheets(doc, step, ctx):
    titles = step.get("titles")
    if isinstance(titles, str):
        titles = tabs.titles_from_text(ctx.path(titles).read_text(encoding="utf-8"))
    inserted = tabs.insert_tabs_for_sections(doc, build(tabs.TabSheetStyle, step.get("style", {})), titles)
    media_name = step.get("media")
    if media_name:
        media = ctx.catalog.get(media_name) if ctx.catalog else None
        if media is None:
            raise ValueError(f"Medium {media_name!r} nicht im Katalog")
        # vorhandene Bereiche hinter den eingefügten Blättern verschieben sich
        new_index, orig = {}, 0
        for final in range(doc.page_count):
            if final not in inserted:
                new_index[orig] = final
                orig += 1
        ranges = [MediaRange(new_index[r.first], new_index[r.last], r.media) for r in ctx.ticket.media_ranges
                  if r.first in new_index and r.last in new_index]
        ranges += [MediaRange(i, i, media) for i in inserted]
        ctx.ticket.media_ranges = ranges


def _op_bleed_tabs(doc, step, ctx):
    tabs.apply_bleed_tabs(doc, build(tabs.BleedTabStyle, step.get("style", {})))


def _op_remove_tabs(doc, step, ctx):
    tabs.remove_tabs(doc)


def _op_spine(doc, step, ctx):
    index = resolve_pages(step.get("page", "first"), doc.page_count)[0]
    kwargs = {k: step[k] for k in ("font_size", "top_to_bottom", "color_cmyk", "background_cmyk") if k in step}
    for key in ("color_cmyk", "background_cmyk"):
        if isinstance(kwargs.get(key), list):
            kwargs[key] = tuple(kwargs[key])
    width = step.get("width_mm")
    if width is None:
        media = ctx.ticket.media
        from .media import Media

        width = spine.spine_width_mm(doc.page_count, media or Media("default"), ctx.ticket.sides.name != "SIMPLEX")
    spine.add_spine_text(doc, index, step.get("text", ""), width, **kwargs)


def _op_gray(doc, step, ctx):
    report = color.convert_to_gray(doc, _pages(doc, step))
    if report.skipped:
        ctx.log.append("gray_skipped:" + ", ".join(f"{k} {v}" for k, v in report.skipped.items()))


def _op_gray_bw_pages(doc, step, ctx):
    """Nur Seiten ohne sichtbare Farbe in echte Graustufen wandeln (spart Farbklicks)."""
    colored = set(color.detect_color_pages(doc))
    color.convert_to_gray(doc, [i for i in range(doc.page_count) if i not in colored])


def _op_image_adjust(doc, step, ctx):
    adj = build(imagefix.Adjustment, step.get("adjust", step))
    for i in _pages(doc, step):
        imagefix.adjust_images(doc, i, adj, step.get("images"))


def _op_spot_rename(doc, step, ctx):
    spot.rename_spot(doc, step["old"], step["new"])


def _op_spot_alternate(doc, step, ctx):
    cmyk, lab = step.get("cmyk"), step.get("lab")
    spot.set_spot_alternate(doc, step["name"], cmyk=tuple(cmyk) if cmyk else None, lab=tuple(lab) if lab else None)


def _op_spot_merge(doc, step, ctx):
    spot.merge_spots(doc, step["source"], step["target"])


def _op_spot_library(doc, step, ctx):
    library = spot.SpotLibrary.load(ctx.path(step["path"]) if step.get("path") else None)
    library.apply(doc)


def _op_repeat(doc, step, ctx):
    impose.repeat_pages(doc, int(step.get("times", 2)))


def _op_output_intent(doc, step, ctx):
    data = ctx.path(step["icc"]).read_bytes()
    components = {b"CMYK": 4, b"RGB ": 3, b"GRAY": 1}.get(data[16:20], 4)
    doc.set_output_intent(data, step.get("identifier", Path(step["icc"]).stem), components, step.get("info", ""))


OPS: dict[str, Callable[[PdfDocument, dict, RunContext], None]] = {
    "rotate": _op_rotate, "delete": _op_delete, "duplicate": _op_duplicate, "insert_blank": _op_insert_blank,
    "insert_pdf": _op_insert_pdf, "insert_images": _op_insert_images, "scale": _op_scale, "shift": _op_shift,
    "add_bleed": _op_add_bleed, "set_box": _op_set_box, "sections": _op_sections,
    "sections_from_bookmarks": _op_sections_from_bookmarks, "element": _op_element,
    "remove_elements": _op_remove_elements, "marks": _op_marks, "remove_marks": _op_remove_marks,
    "tab_sheets": _op_tab_sheets, "bleed_tabs": _op_bleed_tabs, "remove_tabs": _op_remove_tabs,
    "spine": _op_spine, "gray": _op_gray, "gray_bw_pages": _op_gray_bw_pages, "image_adjust": _op_image_adjust,
    "spot_rename": _op_spot_rename, "spot_alternate": _op_spot_alternate, "spot_merge": _op_spot_merge,
    "spot_library": _op_spot_library, "repeat": _op_repeat, "output_intent": _op_output_intent,
}


def step_dict(op: str, **values) -> dict:
    """Schritt für die Aufzeichnung; Dataclasses und Enums werden JSON-fähig gemacht."""
    return {"op": op, **{k: _to_jsonable(v) if (is_dataclass(v) or isinstance(v, Enum)) else v
                         for k, v in values.items()}}


# --- Ausführen ----------------------------------------------------------------------


def run_params(template: Template, source: Path | None, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    params = {"file": source.stem if source else "", "date": date.today().isoformat()}
    params.update(template.params)
    params.update(overrides or {})
    return params


def apply_steps(doc: PdfDocument, steps: list[dict], ctx: RunContext) -> None:
    for number, raw in enumerate(steps, start=1):
        step = substitute(raw, ctx.params)
        op = OPS.get(step.get("op"))
        if op is None:
            raise ValueError(f"Schritt {number}: unbekannte Operation {step.get('op')!r}")
        try:
            op(doc, step, ctx)
        except Exception as exc:
            raise ValueError(f"Schritt {number} ({step['op']}): {exc}") from exc


def apply_template(doc: PdfDocument, template: Template, source: Path | None = None,
                   overrides: dict[str, Any] | None = None, catalog: MediaCatalog | None = None,
                   base_dir: Path | None = None) -> tuple[JobTicket, OutputOptions, Imposition | None, RunContext]:
    """Schritte auf ``doc`` anwenden; liefert Auftrag, Ausgabe und Ausschießen mit ersetzten Parametern."""
    params = run_params(template, source, overrides)
    ticket = template.ticket or JobTicket(job_name="", pdf_url="")
    ticket = ticket_from_dict(substitute(_to_jsonable(ticket), params))
    if not ticket.job_name:
        ticket.job_name = params["file"]
    # Aufrufparameter mit dem Namen eines Auftragsfelds überschreiben dieses direkt
    for key in ("copies", "customer", "comment", "job_name"):
        if key in (overrides or {}):
            value = overrides[key]
            setattr(ticket, key, int(value) if key == "copies" else str(value))
    ctx = RunContext(ticket, params, catalog, base_dir or Path("."))
    apply_steps(doc, template.steps, ctx)
    output = template.output or OutputOptions()
    return ctx.ticket, output, template.imposition, ctx


def run_template(source: Path, dst_dir: Path, template: Template, overrides: dict[str, Any] | None = None,
                 catalog: MediaCatalog | None = None, base_dir: Path | None = None) -> OutputResult:
    """Datei (PDF oder Bild) mit der Vorlage verarbeiten und ausgeben."""
    source = Path(source)
    if source.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        from .images import images_to_pdf

        doc = PdfDocument.from_bytes(images_to_pdf([source]))
    else:
        doc = PdfDocument.open(source)
    ticket, output, imposition, ctx = apply_template(doc, template, source, overrides, catalog, base_dir)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    result = write_output(doc, ticket, dst_dir / (source.stem + ".pdf"), output, imposition)
    result.warnings.extend(ctx.log)
    return result
