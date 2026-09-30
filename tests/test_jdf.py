from lxml import etree

from jdfpdf.core.jdf import JDF_NS, JobTicket, Sides, build_jdf

NS = {"j": JDF_NS}


def test_build_jdf():
    xml = build_jdf(
        JobTicket(job_name="Flyer", pdf_url="flyer.pdf", copies=250, sides=Sides.DUPLEX_LONG_EDGE,
                  media_width_pt=595.28, media_height_pt=841.89, media_weight_gsm=170)
    )
    root = etree.fromstring(xml)
    assert root.get("Type") == "Combined"
    assert root.find(".//j:FileSpec", NS).get("URL") == "flyer.pdf"
    assert root.find(".//j:ComponentLink", NS).get("Amount") == "250"
    assert root.find(".//j:LayoutPreparationParams", NS).get("Sides") == "TwoSidedFlipY"
    media = root.find(".//j:Media", NS)
    assert media.get("Weight") == "170"
    assert media.get("Dimension") == "595.28 841.89"
    # jede ResourceLink zeigt auf eine vorhandene Ressource
    ids = {r.get("ID") for r in root.find("j:ResourcePool", NS)}
    assert {l.get("rRef") for l in root.find("j:ResourceLinkPool", NS)} <= ids
