from jdfpdf.cli import main
from jdfpdf.core.pdfdoc import PdfDocument
from jdfpdf.core.prepress import JDF_ATTACHMENT_NAME

from conftest import make_pdf


def test_batch_cli(tmp_path):
    a = make_pdf(tmp_path / "a.pdf")
    b = make_pdf(tmp_path / "b.pdf", pdfx="PDF/X-4")
    out = tmp_path / "out"
    assert main([str(a), str(b), "-o", str(out), "--copies", "5", "--sides", "duplex_long_edge"]) == 0
    for name in ("a", "b"):
        doc = PdfDocument.open(out / f"{name}.pdf")
        jdf = doc.attachment_data(JDF_ATTACHMENT_NAME)
        assert b'Amount="5"' in jdf
        assert f'URL="{name}.pdf"'.encode() in jdf
        assert b'Dimension="595.00 842.00"' in jdf
        assert (out / f"{name}.jdf").read_bytes() == jdf
    assert PdfDocument.open(out / "b.pdf").pdfx_version() == "PDF/X-4"
