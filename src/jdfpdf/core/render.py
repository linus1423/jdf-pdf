"""Seitenvorschau über PDFium (pypdfium2, Apache/BSD-lizenziert)."""

from __future__ import annotations

import pypdfium2 as pdfium
from PIL import Image


def render_pages(pdf_bytes: bytes, max_size: int = 200) -> list[Image.Image]:
    """Alle Seiten als Vorschaubilder rendern; längste Kante ``max_size`` Pixel."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        images = []
        for page in doc:
            width, height = page.get_size()
            scale = max_size / max(width, height)
            images.append(page.render(scale=scale, may_draw_forms=False).to_pil())
            page.close()
        return images
    finally:
        doc.close()


def render_page(pdf_bytes: bytes, index: int, scale: float = 1.5) -> Image.Image:
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[index]
        return page.render(scale=scale).to_pil()
    finally:
        doc.close()
