"""Beliebige unterstützte Datei (PDF, Bild, Office) als ``PdfDocument`` laden."""

from __future__ import annotations

from pathlib import Path

from . import office
from .pdfdoc import PdfDocument

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
SUPPORTED = {".pdf"} | IMAGE_SUFFIXES | office.OFFICE_SUFFIXES


def is_supported(path: str | Path) -> bool:
    return Path(path).suffix.lower() in SUPPORTED


def load_document(path: str | Path) -> PdfDocument:
    """PDF öffnen, Bild bzw. Office-Datei umwandeln. ``doc.path`` zeigt auf ``<name>.pdf``."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return PdfDocument.open(path)
    if suffix in IMAGE_SUFFIXES:
        from .images import images_to_pdf

        doc = PdfDocument.from_bytes(images_to_pdf([path]))
    elif suffix in office.OFFICE_SUFFIXES:
        doc = PdfDocument.from_bytes(office.convert(path))
    else:
        raise ValueError(f"Dateityp nicht unterstützt: {path.name}")
    doc.path = path.with_suffix(".pdf")
    return doc
