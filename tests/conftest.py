import pikepdf
import pytest


def make_pdf(path, pages=3, pdfx=None):
    pdf = pikepdf.new()
    for i in range(pages):
        pdf.add_blank_page(page_size=(595 + i, 842))
    if pdfx:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["pdfxid:GTS_PDFXVersion"] = pdfx
    pdf.save(path)
    return path


@pytest.fixture
def sample_pdf(tmp_path):
    return make_pdf(tmp_path / "sample.pdf")
