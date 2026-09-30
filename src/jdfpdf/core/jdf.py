"""Erzeugung von JDF-Jobtickets (CIP4 JDF 1.4) für Digitaldruck.

Das Ticket ist ein Combined-Knoten mit DigitalPrinting, wie ihn Canon
PRISMAsync über Hotfolder annimmt. Welche Felder PRISMAsync tatsächlich
auswertet, muss noch an einer echten Maschine geprüft werden.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from lxml import etree

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


@dataclass
class JobTicket:
    job_name: str
    pdf_url: str
    copies: int = 1
    sides: Sides = Sides.SIMPLEX
    color: ColorModel = ColorModel.CMYK
    media_width_pt: float | None = None  # None: aus der ersten Seite übernehmen
    media_height_pt: float | None = None
    media_weight_gsm: float | None = None
    media_type: str = "Paper"
    media_description: str | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    customer: str | None = None
    comment: str | None = None


def build_jdf(ticket: JobTicket) -> bytes:
    """JDF-XML als UTF-8-Bytes erzeugen."""
    if ticket.copies < 1:
        raise ValueError("Auflage muss mindestens 1 sein")

    nsmap = {None: JDF_NS}
    root = etree.Element(
        f"{{{JDF_NS}}}JDF",
        nsmap=nsmap,
        ID="n_root",
        JobID=ticket.job_id,
        JobPartID="p1",
        Type="Combined",
        Types="LayoutPreparation Imposition Interpreting Rendering DigitalPrinting",
        Status="Waiting",
        Version=JDF_VERSION,
        DescriptiveName=ticket.job_name,
    )

    audit = _sub(root, "AuditPool")
    _sub(
        audit,
        "Created",
        AgentName="jdfpdf",
        TimeStamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    if ticket.customer:
        info = _sub(root, "CustomerInfo", CustomerJobName=ticket.job_name)
        _sub(info, "Contact", ContactTypes="Customer").append(
            _el("Company", OrganizationName=ticket.customer)
        )

    if ticket.comment:
        comment = _sub(root, "Comment", Name="Instruction")
        comment.text = ticket.comment

    pool = _sub(root, "ResourcePool")

    runlist = _sub(pool, "RunList", ID="r_runlist", Class="Parameter", Status="Available")
    layout = _sub(runlist, "LayoutElement")
    _sub(layout, "FileSpec", URL=ticket.pdf_url, MimeType="application/pdf")

    media_attrs = {
        "ID": "r_media",
        "Class": "Consumable",
        "Status": "Available",
        "MediaType": ticket.media_type,
    }
    if ticket.media_width_pt and ticket.media_height_pt:
        media_attrs["Dimension"] = f"{ticket.media_width_pt:.2f} {ticket.media_height_pt:.2f}"
    if ticket.media_weight_gsm:
        media_attrs["Weight"] = f"{ticket.media_weight_gsm:g}"
    if ticket.media_description:
        media_attrs["DescriptiveName"] = ticket.media_description
    _sub(pool, "Media", **media_attrs)

    _sub(
        pool,
        "LayoutPreparationParams",
        ID="r_lpp",
        Class="Parameter",
        Status="Available",
        Sides=ticket.sides.value,
    )
    _sub(
        pool,
        "ColorantControl",
        ID="r_cc",
        Class="Parameter",
        Status="Available",
        ProcessColorModel=ticket.color.value,
    )
    _sub(pool, "DigitalPrintingParams", ID="r_dpp", Class="Parameter", Status="Available")
    _sub(
        pool,
        "Component",
        ID="r_output",
        Class="Quantity",
        Status="Unavailable",
        ComponentType="FinalProduct",
    )

    links = _sub(root, "ResourceLinkPool")
    _sub(links, "RunListLink", rRef="r_runlist", Usage="Input")
    _sub(links, "MediaLink", rRef="r_media", Usage="Input")
    _sub(links, "LayoutPreparationParamsLink", rRef="r_lpp", Usage="Input")
    _sub(links, "ColorantControlLink", rRef="r_cc", Usage="Input")
    _sub(links, "DigitalPrintingParamsLink", rRef="r_dpp", Usage="Input")
    _sub(links, "ComponentLink", rRef="r_output", Usage="Output", Amount=str(ticket.copies))

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _el(tag: str, **attrs: str) -> etree._Element:
    return etree.Element(f"{{{JDF_NS}}}{tag}", **attrs)


def _sub(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    return etree.SubElement(parent, f"{{{JDF_NS}}}{tag}", **attrs)
