"""JDF für die Near-Line-Weiterverarbeitung („Prepare to Finish“).

Ein eigenes Ticket für Schneiden, Falzen, Heften, Lochen und Beschneiden, das zum
gedruckten Stapel passt: Jeder Bogen ist als Teil der Eingangskomponente mit der
``ProductID`` aufgeführt, die als Barcode auf dem Bogen steht (``marks``), und die
Schneideblöcke kommen aus dem Ausschießplan.
"""

from __future__ import annotations

from datetime import datetime, timezone

from lxml import etree

from .impose import SheetSide
from .jdf import _FOLD, _PUNCH, _STAPLE, JDF_NS, JDF_VERSION, Fold, JobTicket, Punch, Staple
from .marks import _fmt
from .pdfdoc import PdfDocument

SUFFIX = "_finishing.jdf"


def _sub(parent, tag: str, **attrs) -> etree._Element:
    return etree.SubElement(parent, f"{{{JDF_NS}}}{tag}", **{k: str(v) for k, v in attrs.items()})


def sheet_ids(doc: PdfDocument, ticket: JobTicket, barcode_text: str = "{job}-{page}", duplex: bool = False,
              file: str = "") -> list[str]:
    """Barcode-Inhalte der Bögen (Vorderseiten), wie sie ``marks`` druckt."""
    step = 2 if duplex else 1
    ctx = {"job": ticket.job_name, "file": file}
    return [_fmt(barcode_text, index, doc, ctx) for index in range(0, doc.page_count, step)]


def build_finishing_jdf(ticket: JobTicket, sheets: list[str], layout: list[SheetSide] | None = None) -> bytes:
    fin = ticket.finishing
    cut_blocks = layout[0].cut_lines if layout else []
    types = []
    if len(cut_blocks) > 1:
        types.append("Cutting")
    if fin.fold != Fold.NONE:
        types.append("Folding")
    if fin.staple != Staple.NONE:
        types.append("Stitching")
    if fin.punch != Punch.NONE:
        types.append("HoleMaking")
    if fin.trim:
        types.append("Trimming")
    if not types:
        raise ValueError("Keine Weiterverarbeitung festgelegt")

    root = etree.Element(f"{{{JDF_NS}}}JDF", nsmap={None: JDF_NS}, ID="n_finish", JobID=ticket.job_id,
                         JobPartID="finish", Type="Combined", Types=" ".join(types), Status="Waiting",
                         Version=JDF_VERSION, DescriptiveName=f"{ticket.job_name} – Weiterverarbeitung")
    _sub(_sub(root, "AuditPool"), "Created", AgentName="jdfpdf",
         TimeStamp=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    pool = _sub(root, "ResourcePool")
    links = _sub(root, "ResourceLinkPool")

    def resource(tag: str, rid: str, usage: str = "Input", cls: str = "Parameter", **attrs):
        node = _sub(pool, tag, ID=rid, Class=cls, Status="Available", **attrs)
        _sub(links, f"{tag}Link", rRef=rid, Usage=usage)
        return node

    component = resource("Component", "r_sheets", cls="Quantity", ComponentType="Sheet", PartIDKeys="SheetName",
                         DescriptiveName="Gedruckte Bögen")
    for number, product_id in enumerate(sheets, start=1):
        _sub(component, "Component", SheetName=f"S{number}", ProductID=product_id)

    if "Cutting" in types:
        params = resource("CuttingParams", "r_cut")
        for number, (x0, y0, x1, y1) in enumerate(cut_blocks, start=1):
            _sub(params, "CutBlock", BlockName=f"B{number}", BlockType="CutBlock",
                 BlockSize=f"{x1 - x0:.2f} {y1 - y0:.2f}", BlockTrf=f"1 0 0 1 {x0:.2f} {y0:.2f}")
    if fin.fold != Fold.NONE:
        resource("FoldingParams", "r_fold", FoldCatalog=_FOLD[fin.fold])
    if fin.staple != Staple.NONE:
        resource("StitchingParams", "r_stitch", **_STAPLE[fin.staple])
    if fin.punch != Punch.NONE:
        resource("HoleMakingParams", "r_holes", **_PUNCH[fin.punch])
    if fin.trim:
        resource("TrimmingParams", "r_trim")

    _sub(pool, "Component", ID="r_output", Class="Quantity", Status="Unavailable", ComponentType="FinalProduct")
    _sub(links, "ComponentLink", rRef="r_output", Usage="Output", Amount=ticket.copies)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
