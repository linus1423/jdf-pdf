"""Projektdatei (.jdfproj): bearbeitetes PDF plus alle Auftragseinstellungen.

Aufbau: ZIP mit ``document.pdf`` und ``project.json``.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .impose import BackFlip, Imposition, Layout
from .jdf import ColorModel, Finishing, Fold, JobTicket, MediaRange, Punch, Sides, Staple
from .media import Media
from .pdfdoc import PdfDocument
from .prepress import OutputOptions, PdfxPolicy

FORMAT_VERSION = 1
SUFFIX = ".jdfproj"


@dataclass
class Project:
    document: PdfDocument
    ticket: JobTicket
    output: OutputOptions = field(default_factory=OutputOptions)
    imposition: Imposition = field(default_factory=Imposition)
    extra: dict[str, Any] = field(default_factory=dict)  # Einstellungen späterer Module

    def save(self, path: str | Path) -> None:
        data = {
            "version": FORMAT_VERSION,
            "ticket": _to_jsonable(self.ticket),
            "output": _to_jsonable(self.output),
            "imposition": _to_jsonable(self.imposition),
            "extra": self.extra,
        }
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("project.json", json.dumps(data, indent=2, ensure_ascii=False))
            zf.writestr("document.pdf", self.document.to_bytes())

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        with zipfile.ZipFile(path) as zf:
            data = json.loads(zf.read("project.json"))
            pdf = zf.read("document.pdf")
        if data.get("version", 0) > FORMAT_VERSION:
            raise ValueError("Projektdatei stammt aus einer neueren Programmversion")
        return cls(
            document=PdfDocument.from_bytes(pdf),
            ticket=ticket_from_dict(data["ticket"]),
            output=_output_from_dict(data.get("output", {})),
            imposition=imposition_from_dict(data.get("imposition", {})),
            extra=data.get("extra", {}),
        )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def _media(data: dict | None) -> Media | None:
    if data is None:
        return None
    known = {f.name for f in fields(Media)}
    return Media(**{k: v for k, v in data.items() if k in known})


def ticket_from_dict(data: dict) -> JobTicket:
    fin = data.get("finishing", {})
    return JobTicket(
        job_name=data.get("job_name", ""),
        pdf_url=data.get("pdf_url", ""),
        copies=data.get("copies", 1),
        sides=Sides[data.get("sides", "SIMPLEX")],
        color=ColorModel[data.get("color", "CMYK")],
        media=_media(data.get("media")),
        media_ranges=[MediaRange(r["first"], r["last"], _media(r["media"])) for r in data.get("media_ranges", [])],
        finishing=Finishing(
            staple=Staple[fin.get("staple", "NONE")],
            punch=Punch[fin.get("punch", "NONE")],
            fold=Fold[fin.get("fold", "NONE")],
            trim=fin.get("trim", False),
        ),
        page_count=data.get("page_count"),
        job_id=data.get("job_id") or JobTicket("", "").job_id,
        customer=data.get("customer"),
        comment=data.get("comment"),
    )


def imposition_from_dict(data: dict) -> Imposition:
    known = {f.name for f in fields(Imposition)}
    values = {k: v for k, v in data.items() if k in known}
    if "layout" in values:
        values["layout"] = Layout[values["layout"]]
    if "back_flip" in values:
        values["back_flip"] = BackFlip[values["back_flip"]]
    return Imposition(**values)


def _output_from_dict(data: dict) -> OutputOptions:
    return OutputOptions(
        embed=data.get("embed", True),
        sidecar=data.get("sidecar", True),
        ticketing=data.get("ticketing", False),
        pdfx_policy=PdfxPolicy[data.get("pdfx_policy", "KEEP")],
        ppf=data.get("ppf", False),
        ppf_embed=data.get("ppf_embed", False),
        preflight=data.get("preflight", False),
        finishing_jdf=data.get("finishing_jdf", False),
        softproof=data.get("softproof", False),
        barcode_text=data.get("barcode_text", "{job}-{page}"),
        language=data.get("language", "de"),
        ppf_profile=_profile(data.get("ppf_profile")),
    )


def _profile(data: dict | None):
    if not data:
        return None
    from .ppf import PressProfile

    known = {f.name for f in fields(PressProfile)}
    return PressProfile(**{k: v for k, v in data.items() if k in known})


__all__ = ["Project", "ticket_from_dict", "SUFFIX"]
