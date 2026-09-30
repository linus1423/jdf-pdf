"""Erzeugung von JDF-Jobtickets (CIP4 JDF 1.4) für Digitaldruck.

Das Ticket ist ein Combined-Knoten mit DigitalPrinting, wie ihn Canon
PRISMAsync annimmt (siehe Canon-Doku „Basic JDF ticket“). Medien pro
Seitenbereich werden über ``DigitalPrintingParams`` partitioniert nach
``RunIndex`` abgebildet. Welche Felder PRISMAsync tatsächlich auswertet,
muss noch an einer echten Maschine geprüft werden.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from lxml import etree

from .media import Media

JDF_NS = "http://www.CIP4.org/JDFSchema_1_1"
JDF_VERSION = "1.4"
MIME_TYPE = "application/vnd.cip4-jdf+xml"


class Sides(str, Enum):
    SIMPLEX = "OneSidedFront"
    DUPLEX_LONG_EDGE = "TwoSidedFlipY"
    DUPLEX_SHORT_EDGE = "TwoSidedFlipX"


class ColorModel(str, Enum):
    CMYK = "DeviceCMYK"
    GRAY = "DeviceGray"


class Staple(str, Enum):
    NONE = "none"
    TOP_LEFT = "top_left"
    TOP_RIGHT = "top_right"
    LEFT_TWO = "left_two"
    TOP_TWO = "top_two"
    SADDLE = "saddle"


class Punch(str, Enum):
    NONE = "none"
    TWO_LEFT = "two_left"
    FOUR_LEFT = "four_left"
    TWO_TOP = "two_top"


class Fold(str, Enum):
    NONE = "none"
    HALF = "half"  # Einbruchfalz, CIP4-Faltkatalog F4-1
    Z = "z"  # Zickzackfalz, F6-1 (Katalognummer gegen CIP4 prüfen)


@dataclass
class Finishing:
    staple: Staple = Staple.NONE
    punch: Punch = Punch.NONE
    fold: Fold = Fold.NONE
    trim: bool = False

    @property
    def any(self) -> bool:
        return (
            self.staple != Staple.NONE or self.punch != Punch.NONE or self.fold != Fold.NONE or self.trim
        )


@dataclass
class MediaRange:
    """Medium für die Seiten ``first`` bis ``last`` (0-basiert, einschließlich)."""

    first: int
    last: int
    media: Media


@dataclass
class JobTicket:
    job_name: str
    pdf_url: str
    copies: int = 1
    sides: Sides = Sides.SIMPLEX
    color: ColorModel = ColorModel.CMYK
    media: Media | None = None  # None: Format aus der ersten Seite
    media_ranges: list[MediaRange] = field(default_factory=list)
    finishing: Finishing = field(default_factory=Finishing)
    page_count: int | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    customer: str | None = None
    comment: str | None = None


_STAPLE = {
    Staple.TOP_LEFT: dict(StitchType="Corner", NumberOfStitches="1", ReferenceEdge="Top", Angle="45"),
    Staple.TOP_RIGHT: dict(StitchType="Corner", NumberOfStitches="1", ReferenceEdge="Top", Angle="135"),
    Staple.LEFT_TWO: dict(StitchType="Side", NumberOfStitches="2", ReferenceEdge="Left"),
    Staple.TOP_TWO: dict(StitchType="Side", NumberOfStitches="2", ReferenceEdge="Top"),
    Staple.SADDLE: dict(StitchType="Saddle", NumberOfStitches="2"),
}
_PUNCH = {
    Punch.TWO_LEFT: dict(HoleType="R2m-DIN", HoleReferenceEdge="Left"),
    Punch.FOUR_LEFT: dict(HoleType="R4m-DIN-A4", HoleReferenceEdge="Left"),
    Punch.TWO_TOP: dict(HoleType="R2m-DIN", HoleReferenceEdge="Top"),
}
_FOLD = {Fold.HALF: "F4-1", Fold.Z: "F6-1"}


def build_jdf(ticket: JobTicket) -> bytes:
    """JDF-XML als UTF-8-Bytes erzeugen."""
    if ticket.copies < 1:
        raise ValueError("Auflage muss mindestens 1 sein")
    for rng in ticket.media_ranges:
        if rng.first < 0 or rng.last < rng.first:
            raise ValueError(f"Ungültiger Seitenbereich {rng.first + 1}–{rng.last + 1}")
        if ticket.page_count is not None and rng.last >= ticket.page_count:
            raise ValueError(f"Seitenbereich {rng.first + 1}–{rng.last + 1} liegt hinter der letzten Seite")

    fin = ticket.finishing
    types = ["LayoutPreparation", "Imposition", "Interpreting", "Rendering", "DigitalPrinting"]
    if fin.staple != Staple.NONE:
        types.append("Stitching")
    if fin.punch != Punch.NONE:
        types.append("HoleMaking")
    if fin.fold != Fold.NONE:
        types.append("Folding")
    if fin.trim:
        types.append("Trimming")

    root = etree.Element(
        f"{{{JDF_NS}}}JDF",
        nsmap={None: JDF_NS},
        ID="n_root",
        JobID=ticket.job_id,
        JobPartID="p1",
        Type="Combined",
        Types=" ".join(types),
        Status="Waiting",
        Version=JDF_VERSION,
        DescriptiveName=ticket.job_name,
    )

    audit = _sub(root, "AuditPool")
    _sub(audit, "Created", AgentName="jdfpdf", TimeStamp=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    if ticket.customer:
        info = _sub(root, "CustomerInfo", CustomerJobName=ticket.job_name)
        _sub(info, "Contact", ContactTypes="Customer").append(_el("Company", OrganizationName=ticket.customer))

    if ticket.comment:
        _sub(root, "Comment", Name="Instruction").text = ticket.comment

    pool = _sub(root, "ResourcePool")
    links = _sub(root, "ResourceLinkPool")

    def resource(tag: str, rid: str, usage: str = "Input", cls: str = "Parameter", **attrs) -> etree._Element:
        node = _sub(pool, tag, ID=rid, Class=cls, Status="Available", **attrs)
        _sub(links, f"{tag}Link", rRef=rid, Usage=usage)
        return node

    runlist = resource("RunList", "r_runlist")
    if ticket.page_count:
        runlist.set("NPage", str(ticket.page_count))
    _sub(_sub(runlist, "LayoutElement"), "FileSpec", URL=ticket.pdf_url, MimeType="application/pdf")

    default_media = ticket.media or Media("default", width_pt=0, height_pt=0)
    media_ids = {id(default_media): "r_media"}
    _media_node(pool, "r_media", default_media)
    _sub(links, "MediaLink", rRef="r_media", Usage="Input")
    for index, rng in enumerate(ticket.media_ranges):
        if id(rng.media) not in media_ids:
            rid = f"r_media{index + 1}"
            media_ids[id(rng.media)] = rid
            _media_node(pool, rid, rng.media)
            _sub(links, "MediaLink", rRef=rid, Usage="Input")

    resource("LayoutPreparationParams", "r_lpp", Sides=ticket.sides.value)
    resource("ColorantControl", "r_cc", ProcessColorModel=ticket.color.value)

    dpp = resource("DigitalPrintingParams", "r_dpp")
    if ticket.media_ranges:
        dpp.set("PartIDKeys", "RunIndex")
        for rng in ticket.media_ranges:
            part = _sub(dpp, "DigitalPrintingParams", RunIndex=f"{rng.first} {rng.last}")
            _sub(part, "MediaRef", rRef=media_ids[id(rng.media)])

    if fin.staple != Staple.NONE:
        resource("StitchingParams", "r_stitch", **_STAPLE[fin.staple])
    if fin.punch != Punch.NONE:
        resource("HoleMakingParams", "r_holes", **_PUNCH[fin.punch])
    if fin.fold != Fold.NONE:
        resource("FoldingParams", "r_fold", FoldCatalog=_FOLD[fin.fold])
    if fin.trim:
        resource("TrimmingParams", "r_trim")

    _sub(pool, "Component", ID="r_output", Class="Quantity", Status="Unavailable", ComponentType="FinalProduct")
    _sub(links, "ComponentLink", rRef="r_output", Usage="Output", Amount=str(ticket.copies))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _media_node(pool: etree._Element, rid: str, media: Media) -> None:
    attrs = {"ID": rid, "Class": "Consumable", "Status": "Available", "MediaType": media.media_type}
    if media.width_pt and media.height_pt:
        attrs["Dimension"] = f"{media.width_pt:.2f} {media.height_pt:.2f}"
    if media.weight_gsm:
        attrs["Weight"] = f"{media.weight_gsm:g}"
    if media.thickness_um:
        attrs["Thickness"] = f"{media.thickness_um:g}"
    if media.color:
        attrs["MediaColorName"] = media.color
    if media.coating:
        attrs["FrontCoatings"] = media.coating
    if media.pre_punched:
        attrs["HoleType"] = "R4m-DIN-A4"
    if media.tab_count:
        attrs["MediaSetCount"] = str(media.tab_count)
    if media.name != "default":
        attrs["DescriptiveName"] = media.name
    _sub(pool, "Media", **attrs)


def _el(tag: str, **attrs: str) -> etree._Element:
    return etree.Element(f"{{{JDF_NS}}}{tag}", **attrs)


def _sub(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{JDF_NS}}}{tag}", **attrs)
