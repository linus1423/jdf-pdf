import pytest
from lxml import etree

from jdfpdf.core.jdf import JDF_NS, Finishing, Fold, JobTicket, MediaRange, Punch, Sides, Staple, build_jdf
from jdfpdf.core.media import Media

NS = {"j": JDF_NS}


def parse(ticket):
    return etree.fromstring(build_jdf(ticket))


def assert_links_resolve(root):
    ids = {r.get("ID") for r in root.find("j:ResourcePool", NS)}
    assert {l.get("rRef") for l in root.find("j:ResourceLinkPool", NS)} <= ids
    for ref in root.iter(f"{{{JDF_NS}}}MediaRef"):
        assert ref.get("rRef") in ids


def test_build_jdf_basic():
    media = Media("Flyer 170", 595.28, 841.89, weight_gsm=170)
    root = parse(JobTicket(job_name="Flyer", pdf_url="flyer.pdf", copies=250,
                           sides=Sides.DUPLEX_LONG_EDGE, media=media))
    assert root.get("Type") == "Combined"
    assert root.find(".//j:FileSpec", NS).get("URL") == "flyer.pdf"
    assert root.find(".//j:ComponentLink", NS).get("Amount") == "250"
    assert root.find(".//j:LayoutPreparationParams", NS).get("Sides") == "TwoSidedFlipY"
    node = root.find(".//j:Media", NS)
    assert node.get("Weight") == "170"
    assert node.get("Dimension") == "595.28 841.89"
    assert node.get("DescriptiveName") == "Flyer 170"
    assert "Stitching" not in root.get("Types")
    assert_links_resolve(root)


def test_media_ranges_partition_digital_printing_params():
    cover = Media("Umschlag", weight_gsm=300)
    yellow = Media("Gelb", color="Yellow")
    ticket = JobTicket("x", "x.pdf", page_count=10, media=Media("Normal"),
                       media_ranges=[MediaRange(0, 0, cover), MediaRange(9, 9, cover), MediaRange(3, 5, yellow)])
    root = parse(ticket)
    assert len(root.findall("j:ResourcePool/j:Media", NS)) == 3
    dpp = root.find("j:ResourcePool/j:DigitalPrintingParams", NS)
    assert dpp.get("PartIDKeys") == "RunIndex"
    parts = {p.get("RunIndex"): p.find("j:MediaRef", NS).get("rRef") for p in dpp}
    assert parts["0 0"] == parts["9 9"] != parts["3 5"]
    assert root.find("j:ResourcePool/j:RunList", NS).get("NPage") == "10"
    assert_links_resolve(root)


def test_media_range_validation():
    with pytest.raises(ValueError):
        build_jdf(JobTicket("x", "x.pdf", page_count=3, media_ranges=[MediaRange(2, 4, Media("a"))]))


def test_finishing():
    fin = Finishing(staple=Staple.LEFT_TWO, punch=Punch.FOUR_LEFT, fold=Fold.HALF, trim=True)
    root = parse(JobTicket("x", "x.pdf", finishing=fin))
    types = root.get("Types").split()
    assert {"Stitching", "HoleMaking", "Folding", "Trimming"} <= set(types)
    st = root.find(".//j:StitchingParams", NS)
    assert (st.get("StitchType"), st.get("NumberOfStitches"), st.get("ReferenceEdge")) == ("Side", "2", "Left")
    assert root.find(".//j:HoleMakingParams", NS).get("HoleType") == "R4m-DIN-A4"
    assert root.find(".//j:FoldingParams", NS).get("FoldCatalog") == "F4-1"
    assert_links_resolve(root)
