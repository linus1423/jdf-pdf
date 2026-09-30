"""Scan-Bereinigung und Rastern: Entflecken, Geraderichten, Ausrichten, Rand und Bereiche radieren.

Seiten werden mit PDFium in sichtbarer Lage gerastert, als Bild bearbeitet und als
neue Bildseite gleicher Größe zurückgeschrieben. Nur Pillow, kein NumPy.
"""

from __future__ import annotations

import io
import zlib
from dataclasses import dataclass, field

import pikepdf
from PIL import Image, ImageFilter
from pikepdf import Name

from .pdfdoc import PdfDocument

MM = 72 / 25.4


@dataclass
class CleanupOptions:
    dpi: int = 300
    mode: str = "keep"  # keep (Farbe wie gerendert), gray, lineart
    threshold: int = 128  # für lineart
    despeckle: int = 0  # Medianfilter-Größe: 0 aus, 3 oder 5
    deskew: bool = False
    max_angle: float = 5.0
    align: str = "none"  # none, center, top_left
    margin_mm: float = 10.0  # Abstand für top_left
    border_mm: float = 0.0  # Rand ringsum weiß radieren (schwarze Scannerkanten)
    erase: list[tuple[float, float, float, float]] = field(default_factory=list)  # mm, oben links, sichtbare Seite
    jpeg_quality: int = 90


# --- Bildoperationen --------------------------------------------------------------------


def despeckle(img: Image.Image, size: int = 3) -> Image.Image:
    """Medianfilter; wirkt je Kanal, also auch auf farbige Flecken."""
    if size < 3:
        return img
    size = size if size % 2 else size + 1
    if img.mode == "1":
        return img.convert("L").filter(ImageFilter.MedianFilter(size)).point(lambda v: 255 if v >= 128 else 0, "1")
    return img.filter(ImageFilter.MedianFilter(size))


def _ink(img: Image.Image, threshold: int = 160) -> Image.Image:
    """Maske der dunklen Pixel (255 = Farbe/Schwarz)."""
    gray = img.convert("L")
    return gray.point(lambda v: 255 if v < threshold else 0, "L")


def _profile_score(ink: Image.Image, angle: float) -> float:
    rotated = ink.rotate(angle, resample=Image.Resampling.NEAREST, expand=False, fillcolor=0)
    rows = rotated.resize((1, rotated.height), Image.Resampling.BOX).tobytes()
    mean = sum(rows) / len(rows)
    return sum((r - mean) ** 2 for r in rows)


def detect_skew(img: Image.Image, max_angle: float = 5.0) -> float:
    """Korrekturwinkel in Grad (gegen den Uhrzeigersinn) per Projektionsprofil der Zeilen."""
    small = img.copy()
    small.thumbnail((1000, 1000))
    ink = _ink(small)
    if ink.getbbox() is None:
        return 0.0

    def search(center: float, span: float, step: float) -> float:
        best, best_score = center, -1.0
        steps = int(round(span / step))
        for i in range(-steps, steps + 1):
            angle = center + i * step
            score = _profile_score(ink, angle)
            if score > best_score:
                best, best_score = angle, score
        return best

    coarse = search(0.0, max_angle, 0.5)
    fine = search(coarse, 0.5, 0.05)
    return round(fine, 2)


def _white(img: Image.Image):
    return {"1": 1, "L": 255, "RGB": (255, 255, 255), "CMYK": (0, 0, 0, 0)}.get(img.mode, 255)


def deskew(img: Image.Image, max_angle: float = 5.0) -> tuple[Image.Image, float]:
    angle = detect_skew(img, max_angle)
    if abs(angle) < 0.05:
        return img, 0.0
    source = img.convert("L") if img.mode == "1" else img
    rotated = source.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=_white(source))
    return (rotated.point(lambda v: 255 if v >= 128 else 0, "1") if img.mode == "1" else rotated), angle


def content_bbox(img: Image.Image) -> tuple[int, int, int, int] | None:
    ink = _ink(img).filter(ImageFilter.MedianFilter(3))  # Einzelpixel ignorieren
    return ink.getbbox()


def align(img: Image.Image, mode: str = "center", margin_px: int = 0) -> Image.Image:
    """Inhalt (Begrenzungsrahmen der dunklen Pixel) mittig bzw. oben links auf die Seite setzen."""
    if mode == "none":
        return img
    bbox = content_bbox(img)
    if bbox is None:
        return img
    content = img.crop(bbox)
    cw, ch = content.size
    if mode == "center":
        x, y = (img.width - cw) // 2, (img.height - ch) // 2
    elif mode == "top_left":
        x, y = margin_px, margin_px
    else:
        raise ValueError(f"Unbekannte Ausrichtung {mode!r}")
    page = Image.new(img.mode, img.size, _white(img))
    page.paste(content, (x, y))
    return page


def erase(img: Image.Image, rects_px: list[tuple[int, int, int, int]]) -> Image.Image:
    img = img.copy()
    white = Image.new(img.mode, img.size, _white(img))
    for x0, y0, x1, y1 in rects_px:
        box = (max(0, min(x0, x1)), max(0, min(y0, y1)), min(img.width, max(x0, x1)), min(img.height, max(y0, y1)))
        if box[2] > box[0] and box[3] > box[1]:
            img.paste(white.crop(box), box)
    return img


def erase_border(img: Image.Image, border_px: int) -> Image.Image:
    if border_px <= 0:
        return img
    w, h = img.size
    b = border_px
    return erase(img, [(0, 0, w, b), (0, h - b, w, h), (0, 0, b, h), (w - b, 0, w, h)])


def to_mode(img: Image.Image, mode: str, threshold: int = 128) -> Image.Image:
    if mode == "gray":
        return img.convert("L")
    if mode == "lineart":
        return img.convert("L").point(lambda v: 255 if v >= threshold else 0, "1")
    return img if img.mode in ("L", "RGB", "1") else img.convert("RGB")


def clean_image(img: Image.Image, opts: CleanupOptions) -> tuple[Image.Image, float]:
    """Alle Schritte in fester Reihenfolge; liefert Bild und Drehwinkel."""
    px = opts.dpi / 25.4
    img = to_mode(img, opts.mode, opts.threshold)
    img = despeckle(img, opts.despeckle)
    angle = 0.0
    if opts.deskew:
        img, angle = deskew(img, opts.max_angle)
    img = erase_border(img, int(round(opts.border_mm * px)))
    if opts.erase:
        img = erase(img, [tuple(int(round(v * px)) for v in rect) for rect in opts.erase])
    img = align(img, opts.align, int(round(opts.margin_mm * px)))
    return img, angle


# --- Seiten -----------------------------------------------------------------------------


def render_visible(doc: PdfDocument, index: int, dpi: int) -> Image.Image:
    """Seite in sichtbarer Lage (CropBox, /Rotate) auf Weiß rendern."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(doc.to_bytes())
    try:
        page = pdf[index]
        image = page.render(scale=dpi / 72, may_draw_forms=True).to_pil().convert("RGB")
        page.close()
        return image
    finally:
        pdf.close()


def _image_xobject(pdf: pikepdf.Pdf, img: Image.Image, quality: int) -> pikepdf.Stream:
    if img.mode == "1":
        data = zlib.compress(img.tobytes())
        stream = pdf.make_stream(data)
        stream.Filter = Name.FlateDecode
        stream.BitsPerComponent = 1
        stream.ColorSpace = Name.DeviceGray
    else:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, subsampling=0)
        stream = pdf.make_stream(buf.getvalue())
        stream.Filter = Name.DCTDecode
        stream.BitsPerComponent = 8
        stream.ColorSpace = Name.DeviceGray if img.mode == "L" else Name.DeviceRGB
    stream.Type = Name.XObject
    stream.Subtype = Name.Image
    stream.Width, stream.Height = img.size
    return stream


def image_page_pdf(img: Image.Image, width: float, height: float, quality: int = 90) -> PdfDocument:
    """Einseitiges PDF der Größe ``width``×``height`` pt, vollflächig mit ``img``."""
    pdf = pikepdf.new()
    xobj = _image_xobject(pdf, img, quality)
    page = pikepdf.Dictionary(
        Type=Name.Page, MediaBox=pikepdf.Array([0, 0, width, height]),
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj)),
        Contents=pdf.make_stream(f"q {width:.4f} 0 0 {height:.4f} 0 0 cm /Im0 Do Q\n".encode()),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    return PdfDocument(pdf)


def _visible_size(doc: PdfDocument, index: int) -> tuple[float, float]:
    x0, y0, x1, y1 = doc.box(index, "CropBox")
    w, h = x1 - x0, y1 - y0
    return (h, w) if doc.page_rotation(index) in (90, 270) else (w, h)


def replace_with_image(doc: PdfDocument, index: int, img: Image.Image, quality: int = 90) -> None:
    """Seite durch Bildseite gleicher sichtbarer Größe ersetzen; Trim-/BleedBox bleiben bei 0° erhalten."""
    width, height = _visible_size(doc, index)
    boxes = {}
    if doc.page_rotation(index) == 0:
        cx0, cy0, _, _ = doc.box(index, "CropBox")
        for name in ("TrimBox", "BleedBox"):
            if doc.has_box(index, name):
                x0, y0, x1, y1 = doc.box(index, name)
                rect = (max(0, x0 - cx0), max(0, y0 - cy0), min(width, x1 - cx0), min(height, y1 - cy0))
                if rect[2] > rect[0] and rect[3] > rect[1]:
                    boxes[name] = rect
    doc.replace_page(index, image_page_pdf(img, width, height, quality))
    for name, rect in boxes.items():
        doc.set_box(index, name, rect)


def rasterize_pages(doc: PdfDocument, indices: list[int], dpi: int = 300, mode: str = "keep",
                    quality: int = 90) -> None:
    """Seiten (auch elektronische PDFs) in Bilder umwandeln."""
    for index in indices:
        img = to_mode(render_visible(doc, index, dpi), mode)
        replace_with_image(doc, index, img, quality)


def cleanup_pages(doc: PdfDocument, indices: list[int], opts: CleanupOptions) -> dict[int, float]:
    """Seiten rastern und bereinigen; liefert die Drehwinkel je Seite."""
    angles = {}
    for index in indices:
        img, angle = clean_image(render_visible(doc, index, opts.dpi), opts)
        replace_with_image(doc, index, img, opts.jpeg_quality)
        angles[index] = angle
    return angles
