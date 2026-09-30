import pikepdf

from conftest import make_pdf
from jdfpdf.cli import main
from jdfpdf.core.jdf import JobTicket
from jdfpdf.core.pdfdoc import PdfDocument
from jdfpdf.core.prepress import (
    JDF_ATTACHMENT_NAME, TICKETING_CID, TICKETING_SUFFIX, OutputOptions, PdfxPolicy, write_output,
)


def test_batch_cli(tmp_path):
    a = make_pdf(tmp_path / "a.pdf")
    out = tmp_path / "out"
    assert main([str(a), "-o", str(out), "--copies", "5", "--sides", "duplex_long_edge",
                 "--staple", "top_left", "--ticketing"]) == 0
    doc = PdfDocument.open(out / "a.pdf")
    jdf = doc.attachment_data(JDF_ATTACHMENT_NAME)
    assert b'Amount="5"' in jdf
    assert b'URL="a.pdf"' in jdf
    assert b'Dimension="595.00 842.00"' in jdf
    assert b"StitchingParams" in jdf
    assert (out / "a.jdf").read_bytes().count(b"<JDF") == 1
    assert (out / f"a{TICKETING_SUFFIX}").exists()


def test_ticketing_file_layout(tmp_path):
    src = make_pdf(tmp_path / "src.pdf", pages=2)
    res = write_output(PdfDocument.open(src), JobTicket("j", ""), tmp_path / "o.pdf",
                       OutputOptions(embed=False, sidecar=False, ticketing=True))
    [combined] = res.extra_files
    data = combined.read_bytes()
    assert data.startswith(b"<?xml")
    split = data.index(b"%PDF-")
    assert TICKETING_CID.encode() in data[:split]
    assert data[:split].rstrip().endswith(b"</JDF>")
    assert len(pikepdf.open(__import__("io").BytesIO(data[split:])).pages) == 2
    # PDF selbst bleibt ohne Anhang
    assert PdfDocument.open(tmp_path / "o.pdf").attachments() == []


def test_pdfx_keeps_conformance_by_default(tmp_path):
    src = make_pdf(tmp_path / "x4.pdf", pdfx="PDF/X-4")
    res = write_output(PdfDocument.open(src), JobTicket("j", ""), tmp_path / "o.pdf", OutputOptions())
    assert not res.embedded
    assert res.warnings == ["pdfx_skipped:PDF/X-4"]
    assert PdfDocument.open(tmp_path / "o.pdf").attachments() == []
    assert (tmp_path / "o.jdf").exists()

    res = write_output(PdfDocument.open(src), JobTicket("j", ""), tmp_path / "p.pdf",
                       OutputOptions(pdfx_policy=PdfxPolicy.EMBED_ANYWAY))
    assert res.embedded and res.warnings == ["pdfx_embed:PDF/X-4"]


def test_no_embed_removes_old_attachment(tmp_path):
    src = make_pdf(tmp_path / "s.pdf")
    doc = PdfDocument.open(src)
    write_output(doc, JobTicket("j", ""), tmp_path / "a.pdf", OutputOptions(sidecar=False))
    doc = PdfDocument.open(tmp_path / "a.pdf")
    assert len(doc.attachments()) == 1
    write_output(doc, JobTicket("j", ""), tmp_path / "b.pdf", OutputOptions(embed=False))
    assert PdfDocument.open(tmp_path / "b.pdf").attachments() == []


def test_output_intent(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "s.pdf"))
    assert doc.output_intent() is None
    doc.set_output_intent(b"fakeicc", "FOGRA39", 4)
    doc.save(tmp_path / "oi.pdf")
    oi = PdfDocument.open(tmp_path / "oi.pdf").output_intent()
    assert oi == {"identifier": "FOGRA39", "info": "FOGRA39", "profile": "yes"}
