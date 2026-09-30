"""JMF: Aufträge an einen JDF-Controller senden und die Warteschlange abfragen.

``submit`` schickt ein MIME-Paket (multipart/related) aus JMF ``SubmitQueueEntry``,
JDF und PDF per HTTP-POST; JMF und JDF verweisen per ``cid:`` auf die Teile.
``queue_status`` fragt ``QueueStatus`` ab. Nur Standardbibliothek (urllib).
"""

from __future__ import annotations

import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import make_msgid

from lxml import etree

from .jdf import JDF_NS, MIME_TYPE, JobTicket, build_jdf

JMF_MIME = "application/vnd.cip4-jmf+xml"
SENDER = "jdfpdf"
JDF_CID = "jdf@jdfpdf"
PDF_CID = "doc@jdfpdf"


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jmf(*children: etree._Element) -> bytes:
    root = etree.Element(f"{{{JDF_NS}}}JMF", nsmap={None: JDF_NS}, SenderID=SENDER, TimeStamp=_stamp(),
                         Version="1.4")
    for child in children:
        root.append(child)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _el(tag: str, **attrs: str) -> etree._Element:
    return etree.Element(f"{{{JDF_NS}}}{tag}", **attrs)


def submit_jmf(jdf_url: str = f"cid:{JDF_CID}", return_url: str | None = None) -> bytes:
    command = _el("Command", ID=f"C{uuid.uuid4().hex[:8]}", Type="SubmitQueueEntry")
    params = _el("QueueSubmissionParams", URL=jdf_url)
    if return_url:
        params.set("ReturnJMF", return_url)
    command.append(params)
    return _jmf(command)


def queue_status_jmf() -> bytes:
    query = _el("Query", ID=f"Q{uuid.uuid4().hex[:8]}", Type="QueueStatus")
    query.append(_el("QueueFilter", QueueEntryDetails="Brief"))
    return _jmf(query)


def mime_package(ticket: JobTicket, pdf: bytes, return_url: str | None = None) -> tuple[bytes, str]:
    """MIME-Paket (Body, Content-Type) aus JMF, JDF und PDF."""
    jdf = build_jdf(replace(ticket, pdf_url=f"cid:{PDF_CID}"))
    boundary = "jdfpdf-" + uuid.uuid4().hex
    parts = [
        (JMF_MIME, make_msgid("jmf", "jdfpdf").strip("<>"), submit_jmf(return_url=return_url)),
        (MIME_TYPE, JDF_CID, jdf),
        ("application/pdf", PDF_CID, pdf),
    ]
    body = b""
    for content_type, cid, data in parts:
        body += (f"--{boundary}\r\nContent-Type: {content_type}\r\nContent-ID: <{cid}>\r\n"
                 f"Content-Transfer-Encoding: binary\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f'multipart/related; boundary="{boundary}"; type="{JMF_MIME}"'


@dataclass
class QueueEntry:
    queue_entry_id: str
    status: str
    job_id: str = ""
    job_part_id: str = ""
    priority: str = ""


@dataclass
class JmfResponse:
    return_code: int
    queue_status: str = ""
    entries: list[QueueEntry] = field(default_factory=list)
    comment: str = ""
    raw: bytes = b""

    @property
    def ok(self) -> bool:
        return self.return_code == 0


def parse_response(data: bytes) -> JmfResponse:
    root = etree.fromstring(data)
    ns = {"j": JDF_NS}
    response = root.find("j:Response", ns)
    if response is None:
        response = root.find("j:Acknowledge", ns)
    if response is None:
        raise ValueError("Keine JMF-Antwort erhalten")
    code = int(response.get("ReturnCode", "0"))
    queue = response.find("j:Queue", ns)
    entries = []
    if queue is not None:
        entries = [QueueEntry(e.get("QueueEntryID", ""), e.get("Status", ""), e.get("JobID", ""),
                              e.get("JobPartID", ""), e.get("Priority", ""))
                   for e in queue.findall("j:QueueEntry", ns)]
    single = response.find("j:QueueEntry", ns)
    if single is not None:
        entries.insert(0, QueueEntry(single.get("QueueEntryID", ""), single.get("Status", ""),
                                     single.get("JobID", ""), single.get("JobPartID", "")))
    comment = " ".join(c.text or "" for c in response.iter(f"{{{JDF_NS}}}Comment")).strip()
    notification = response.find("j:Notification", ns)
    if not comment and notification is not None:
        comment = " ".join(c.text or "" for c in notification.iter(f"{{{JDF_NS}}}Comment")).strip()
    return JmfResponse(code, queue.get("Status", "") if queue is not None else "", entries, comment, data)


def _post(url: str, body: bytes, content_type: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            return reply.read()
    except urllib.error.HTTPError as exc:
        raise ConnectionError(f"HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc.reason)) from exc


def submit(url: str, ticket: JobTicket, pdf: bytes, timeout: float = 30.0) -> JmfResponse:
    """Auftrag senden; Controller-URL z. B. ``http://prismasync:8010/jmf``."""
    body, content_type = mime_package(ticket, pdf)
    return parse_response(_post(url, body, content_type, timeout))


def queue_status(url: str, timeout: float = 10.0) -> JmfResponse:
    return parse_response(_post(url, queue_status_jmf(), JMF_MIME, timeout))
