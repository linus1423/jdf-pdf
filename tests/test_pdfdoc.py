from jdfpdf.core.pdfdoc import PdfDocument


def widths(doc):
    return [round(doc.page_size(i)[0]) for i in range(doc.page_count)]


def test_page_operations(sample_pdf):
    doc = PdfDocument.open(sample_pdf)
    assert widths(doc) == [595, 596, 597]
    doc.move_page(0, 2)
    assert widths(doc) == [596, 597, 595]
    doc.reorder([2, 0, 1])
    assert widths(doc) == [595, 596, 597]
    doc.rotate_page(1, 90)
    assert doc.page_rotation(1) == 90
    assert doc.page_size(1) == (842, 596)
    doc.delete_pages([0, 2])
    assert doc.page_count == 1


def test_attachment_roundtrip(sample_pdf, tmp_path):
    doc = PdfDocument.open(sample_pdf)
    doc.attach("job.jdf", b"<JDF/>", "application/vnd.cip4-jdf+xml")
    doc.attach("job.jdf", b"<JDF v='2'/>", "application/vnd.cip4-jdf+xml")  # ersetzt
    out = tmp_path / "out.pdf"
    doc.save(out)

    reopened = PdfDocument.open(out)
    [att] = reopened.attachments()
    assert att.name == "job.jdf"
    assert att.relationship == "Supplement"
    assert reopened.attachment_data("job.jdf") == b"<JDF v='2'/>"
    assert len(reopened._pdf.Root.AF) == 1

    reopened.detach("job.jdf")
    assert reopened.attachments() == []
    assert "/AF" not in reopened._pdf.Root
