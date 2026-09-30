from jdfpdf.core.media import MM, Media, MediaCatalog, parse_jmf_media

JMF = b"""<?xml version="1.0"?>
<JMF xmlns="http://www.CIP4.org/JDFSchema_1_1" Version="1.4">
 <Response Type="Resource" ReturnCode="0">
  <ResourceInfo>
   <ResourceSet Name="Media">
    <Resource><Media DescriptiveName="Canon Oce Top Colour 120" Dimension="595.28 841.89" Weight="120" MediaType="Paper" FrontCoatings="None"/></Resource>
    <Resource><Media DescriptiveName="Register A4" Dimension="637.8 841.89" Weight="160" MediaType="Tab" MediaSetCount="5"/></Resource>
    <Resource><Media ID="noname-but-id" HoleType="R4m-DIN-A4"/></Resource>
   </ResourceSet>
  </ResourceInfo>
 </Response>
</JMF>"""


def test_parse_jmf_media():
    media = parse_jmf_media(JMF)
    assert [m.name for m in media] == ["Canon Oce Top Colour 120", "Register A4", "noname-but-id"]
    assert media[0].weight_gsm == 120
    assert media[1].tab_count == 5
    assert media[2].pre_punched


def test_catalog_roundtrip(tmp_path):
    cat = MediaCatalog([Media("A", weight_gsm=90)])
    cat.add(Media("A", weight_gsm=100))  # ersetzt
    assert cat.import_jmf(JMF) == 3
    path = tmp_path / "media.json"
    cat.save(path)
    loaded = MediaCatalog.load(path)
    assert loaded.get("A").weight_gsm == 100
    assert len(loaded.media) == 4


def test_defaults_when_missing(tmp_path):
    cat = MediaCatalog.load(tmp_path / "none.json")
    assert cat.get("A4 80 g").width_pt == 210 * MM


def test_thickness_estimate():
    assert Media("x", weight_gsm=100).thickness_mm == 0.1
    assert Media("x", thickness_um=120).thickness_mm == 0.12
