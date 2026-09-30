import io
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from lxml import etree

from conftest import make_pdf
from jdfpdf.cli import main as cli_main
from jdfpdf.core import jmf, printing
from jdfpdf.core.finishing_jdf import build_finishing_jdf
from jdfpdf.core.hotfolder import Hotfolder
from jdfpdf.core.impose import Imposition, Layout
from jdfpdf.core.jdf import JDF_NS, Finishing, Fold, JobTicket, MediaRange, Staple
from jdfpdf.core.media import Media, MediaCatalog
from jdfpdf.core.pdfdoc import PdfDocument, Section
from jdfpdf.core.prepress import OutputOptions, write_output
from jdfpdf.core.template import Template, apply_template, parse_param, resolve_pages, run_template, substitute

NS = {"j": JDF_NS}


def sample_template() -> Template:
    return Template(
        name="Test",
        params={"stamp": "ENTWURF", "copies": 2},
        steps=[
            {"op": "rotate", "degrees": 90, "pages": "odd"},
            {"op": "element", "element": {"text": "${stamp} {page}", "anchor": "TOP_RIGHT"}, "pages": "all"},
            {"op": "insert_blank", "at": "end"},
            {"op": "marks", "options": {"crop": True, "barcode": "CODE128"}, "pages": "all"},
        ],
        ticket=JobTicket("${file}-job", "", copies="${copies}", finishing=Finishing(staple=Staple.TOP_LEFT)),
        output=OutputOptions(sidecar=True, embed=False),
    )


def test_placeholders_and_pages():
    assert substitute({"a": "${x}", "b": "v${x}w", "c": ["${y}"]}, {"x": 3, "y": True}) == \
        {"a": 3, "b": "v3w", "c": [True]}
    assert parse_param("copies=5") == ("copies", 5)
    assert parse_param("name=Hallo Welt") == ("name", "Hallo Welt")
    with pytest.raises(ValueError):
        parse_param("kaputt")
    assert resolve_pages("even", 5) == [1, 3]
    assert resolve_pages("last", 5) == [4]
    assert resolve_pages("2-3", 5) == [1, 2]


def test_template_roundtrip_and_apply(tmp_path):
    template = sample_template()
    path = tmp_path / "t.jdftpl"
    template.save(path)
    loaded = Template.load(path)
    assert loaded.steps == template.steps and loaded.params == template.params
    doc = PdfDocument.open(make_pdf(tmp_path / "in.pdf", pages=3))
    ticket, output, imposition, ctx = apply_template(doc, loaded, tmp_path / "in.pdf", {"stamp": "FREIGABE"})
    assert doc.page_count == 4
    assert doc.page_rotation(0) == 90 and doc.page_rotation(1) == 0
    assert ticket.job_name == "in-job" and ticket.copies == 2
    assert ticket.finishing.staple == Staple.TOP_LEFT
    assert not output.embed


def test_template_errors_name_step(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "in.pdf"))
    with pytest.raises(ValueError, match="Schritt 1"):
        apply_template(doc, Template(steps=[{"op": "zaubern"}]))
    with pytest.raises(ValueError, match="Schritt 1 \\(delete\\)"):
        apply_template(doc, Template(steps=[{"op": "delete", "pages": "all"}]))


def test_tab_sheet_step_assigns_media(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "in.pdf", pages=4))
    doc.set_sections([Section("A", 0), Section("B", 2)])
    catalog = MediaCatalog([Media("Register"), Media("Gelb", color="Yellow")])
    ticket = JobTicket("t", "", media_ranges=[MediaRange(3, 3, catalog.get("Gelb"))])
    template = Template(steps=[{"op": "tab_sheets", "media": "Register"}], ticket=ticket)
    result_ticket, *_ = apply_template(doc, template, catalog=catalog)
    ranges = sorted((r.first, r.last, r.media.name) for r in result_ticket.media_ranges)
    assert ranges == [(0, 0, "Register"), (3, 3, "Register"), (5, 5, "Gelb")]


def test_run_template_and_cli(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", pages=2)
    template = sample_template()
    template.save(tmp_path / "t.jdftpl")
    result = run_template(src, tmp_path / "out", template, {"copies": 7})
    jdf = etree.fromstring(result.pdf.with_suffix(".jdf").read_bytes())
    assert jdf.find(".//j:ComponentLink", NS).get("Amount") == "7"
    assert cli_main(["run", str(tmp_path / "t.jdftpl"), str(src), "-o", str(tmp_path / "cli"), "-p", "copies=3"]) == 0
    jdf = etree.fromstring((tmp_path / "cli" / "a.jdf").read_bytes())
    assert jdf.find(".//j:ComponentLink", NS).get("Amount") == "3"


def test_hotfolder(tmp_path):
    inbox, outbox = tmp_path / "in", tmp_path / "out"
    sent = []
    folder = Hotfolder(inbox, outbox, Template(output=OutputOptions(embed=True, sidecar=False)),
                       after=lambda path, result: sent.append((path.name, result.ticket.job_name)))
    make_pdf(inbox / "job1.pdf")
    (inbox / "broken.pdf").write_bytes(b"kein pdf")
    assert folder.poll() == []  # erst beim zweiten Durchlauf gilt die Datei als vollständig
    events = {e.file: e for e in folder.poll()}
    assert events["job1.pdf"].ok and not events["broken.pdf"].ok
    assert (outbox / "job1.pdf").exists()
    assert (inbox / "done" / "job1.pdf").exists() and (inbox / "error" / "broken.pdf").exists()
    assert "FEHLER broken.pdf" in (inbox / "hotfolder.log").read_text(encoding="utf-8")
    assert sent == [("job1.pdf", "job1")]


class _Controller(BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Controller.received.append((self.headers["Content-Type"], body))
        if b"SubmitQueueEntry" in body:
            reply = (f'<JMF xmlns="{JDF_NS}"><Response Type="SubmitQueueEntry" ReturnCode="0">'
                     '<QueueEntry QueueEntryID="qe42" Status="Waiting"/></Response></JMF>')
        else:
            reply = (f'<JMF xmlns="{JDF_NS}"><Response Type="QueueStatus" ReturnCode="0"><Queue Status="Running">'
                     '<QueueEntry QueueEntryID="qe42" Status="Running" JobID="j1"/></Queue></Response></JMF>')
        self.send_response(200)
        self.send_header("Content-Type", jmf.JMF_MIME)
        self.end_headers()
        self.wfile.write(reply.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def controller():
    server = HTTPServer(("127.0.0.1", 0), _Controller)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Controller.received = []
    yield f"http://127.0.0.1:{server.server_address[1]}/jmf"
    server.shutdown()


def test_jmf_submit_and_queue(controller, tmp_path):
    ticket = JobTicket("Auftrag", "", copies=3)
    response = jmf.submit(controller, ticket, b"%PDF-1.7 test")
    assert response.ok and response.entries[0].queue_entry_id == "qe42"
    content_type, body = _Controller.received[0]
    assert content_type.startswith("multipart/related") and jmf.JMF_MIME in content_type
    assert b"SubmitQueueEntry" in body and b'URL="cid:doc@jdfpdf"' in body and b"%PDF-1.7 test" in body
    status = jmf.queue_status(controller)
    assert status.queue_status == "Running" and status.entries[0].job_id == "j1"
    assert cli_main(["queue", controller]) == 0


def test_jmf_connection_error():
    with pytest.raises(ConnectionError):
        jmf.queue_status("http://127.0.0.1:9/jmf", timeout=1)


def test_printer_profiles_and_hotfolder_send(tmp_path, controller):
    folder = tmp_path / "hot"
    folder.mkdir()
    profiles = [printing.PrinterProfile("Hot", printing.PrinterKind.HOTFOLDER, str(folder), printing.Payload.PDF_JDF),
                printing.PrinterProfile("Ticketing", printing.PrinterKind.HOTFOLDER, str(folder),
                                        printing.Payload.TICKETING),
                printing.PrinterProfile("Controller", printing.PrinterKind.JMF, controller)]
    printing.save_printers(profiles, tmp_path / "printers.json")
    loaded = printing.load_printers(tmp_path / "printers.json")
    assert [p.kind for p in loaded] == [printing.PrinterKind.HOTFOLDER, printing.PrinterKind.HOTFOLDER,
                                        printing.PrinterKind.JMF]
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf"))

    def content(d):  # qpdf erneuert sonst den zweiten Teil der /ID zeitabhängig
        buf = io.BytesIO()
        d.pdf.save(buf, deterministic_id=True)
        return buf.getvalue()

    before = content(doc)
    result = printing.send(loaded[0], doc, JobTicket("Mein Auftrag", ""))
    assert sorted(p.name for p in folder.iterdir()) == ["Mein_Auftrag.jdf", "Mein_Auftrag.pdf"]
    printing.send(loaded[1], doc, JobTicket("Mein Auftrag", ""))
    assert (folder / "Mein_Auftrag_prismasync.jdf").read_bytes().count(b"%PDF") == 1
    assert content(doc) == before  # Dokument unverändert
    assert printing.send(loaded[2], doc, JobTicket("x", "")).queue_entry == "qe42"
    assert result.files


@pytest.mark.skipif(sys.platform == "win32", reason="lp nur unter Unix")
def test_system_printer_via_lp(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "lp.log"
    for name, script in (("lp", f'echo "$@" >> {log}\necho "request id is Q-1"'), ("lpstat", "echo 'Q1 accepting'")):
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n{script}\n")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    assert printing.list_system_printers() == ["Q1"]
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf"))
    profile = printing.PrinterProfile("Canon", printing.PrinterKind.SYSTEM, "Q1", options={"media": "A4"})
    assert "Q-1" in printing.send(profile, doc, JobTicket("job", "", copies=4)).message
    raw = printing.PrinterProfile("Raw", printing.PrinterKind.RAW, "Q1", printing.Payload.TICKETING)
    printing.send(raw, doc, JobTicket("job", ""))
    lines = log.read_text().splitlines()
    assert "-d Q1 -n 4" in lines[0] and "media=A4" in lines[0]
    assert "-o raw" in lines[1] and lines[1].endswith("_prismasync.jdf")


def test_finishing_jdf(tmp_path):
    doc = PdfDocument.open(make_pdf(tmp_path / "a.pdf", pages=8))
    ticket = JobTicket("Heft", "", copies=10, finishing=Finishing(staple=Staple.SADDLE, fold=Fold.HALF))
    imp = Imposition(layout=Layout.NUP, cols=2, rows=1, auto_sheet=True, duplex=False)
    result = write_output(doc, ticket, tmp_path / "o.pdf", OutputOptions(finishing_jdf=True, sidecar=False), imp)
    path = tmp_path / "o_finishing.jdf"
    assert path in result.extra_files
    root = etree.fromstring(path.read_bytes())
    assert root.get("Types") == "Cutting Folding Stitching"
    sheets = root.findall(".//j:Component[@ComponentType='Sheet']/j:Component", NS)
    assert [s.get("ProductID") for s in sheets] == ["Heft-1", "Heft-2", "Heft-3", "Heft-4"]
    assert len(root.findall(".//j:CutBlock", NS)) == 2
    with pytest.raises(ValueError):
        build_finishing_jdf(JobTicket("x", ""), ["x-1"])


def test_cli_legacy_still_works(tmp_path):
    src = make_pdf(tmp_path / "a.pdf")
    assert cli_main([str(src), "-o", str(tmp_path / "o"), "--finishing-jdf", "--staple", "top_left"]) == 0
    assert (tmp_path / "o" / "a_finishing.jdf").exists()
