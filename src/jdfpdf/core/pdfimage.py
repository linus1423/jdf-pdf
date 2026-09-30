"""Hilfen für Streams und Bild-XObjects: lesen, neu schreiben, Ressourcen kopieren."""

from __future__ import annotations

import io
import zlib

import pikepdf
from pikepdf import Name
from PIL import Image

from .colorspace import components, family

_STREAM_KEYS = {"/Length", "/Filter", "/DecodeParms", "/DL"}
_IMAGE_KEYS_KEPT = ("/SMask", "/Interpolate", "/Intent", "/Metadata", "/OC", "/Alternates")


def stream_like(pdf: pikepdf.Pdf, template: pikepdf.Stream, data: bytes) -> pikepdf.Stream:
    """Neuer (unkomprimiert übergebener, Flate-komprimierter) Stream mit den Einträgen von ``template``."""
    stream = pdf.make_stream(zlib.compress(data))
    for key, value in template.items():
        if key not in _STREAM_KEYS:
            stream[key] = value
    stream.Filter = Name.FlateDecode
    return stream


def copy_dict(obj) -> pikepdf.Dictionary:
    return pikepdf.Dictionary(obj) if obj is not None else pikepdf.Dictionary()


def cow_xobjects(page_or_form) -> pikepdf.Dictionary:
    """Ressourcen und XObject-Verzeichnis kopieren (andere Seiten teilen sie evtl.); liefert das Verzeichnis."""
    resources = copy_dict(page_or_form.get("/Resources"))
    page_or_form.Resources = resources
    resources.XObject = copy_dict(resources.get("/XObject"))
    return resources.XObject


def load_image(obj: pikepdf.Stream) -> Image.Image | None:
    """Bild-XObject als PIL-Bild (L, RGB, CMYK); ``None``, wenn es sich nicht sicher bearbeiten lässt."""
    if obj.get("/ImageMask", False):
        return None
    space = obj.get("/ColorSpace")
    fam = family(space) if space is not None else ""
    if fam in ("Separation", "DeviceN", "Lab", "Pattern", ""):
        return None
    if int(obj.get("/BitsPerComponent", 8)) > 8:
        return None
    if "/Decode" in obj and fam != "Indexed":
        default = [0, 1] * components(space)
        if [float(v) for v in obj.Decode] != default:
            return None
    try:
        image = pikepdf.PdfImage(obj).as_pil_image()
    except Exception:
        return None
    if image.mode == "P" or fam == "Indexed":
        image = image.convert("RGB")
    elif image.mode == "1":
        image = image.convert("L")
    if image.mode not in ("L", "RGB", "CMYK"):
        return None
    return image


def is_jpeg(obj: pikepdf.Stream) -> bool:
    filters = obj.get("/Filter")
    if filters is None:
        return False
    names = [filters] if isinstance(filters, Name) else list(filters)
    return Name.DCTDecode in names


def write_image(pdf: pikepdf.Pdf, image: Image.Image, template: pikepdf.Stream,
                colorspace=None) -> pikepdf.Stream:
    """Neues Bild-XObject aus ``image``; übernimmt Maske, Interpolation usw. von ``template``.

    Graustufen- und RGB-Bilder, die vorher JPEG waren, werden wieder als JPEG
    gespeichert, alles andere verlustfrei (Flate), auch CMYK, um die Adobe-Invertierung
    von CMYK-JPEGs zu vermeiden.
    """
    if colorspace is None:
        colorspace = {"L": Name.DeviceGray, "RGB": Name.DeviceRGB, "CMYK": Name.DeviceCMYK}[image.mode]
    if is_jpeg(template) and image.mode in ("L", "RGB"):
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=92)
        stream = pdf.make_stream(buf.getvalue())
        stream.Filter = Name.DCTDecode
    else:
        stream = pdf.make_stream(zlib.compress(image.tobytes()))
        stream.Filter = Name.FlateDecode
    stream.Type = Name.XObject
    stream.Subtype = Name.Image
    stream.Width = image.width
    stream.Height = image.height
    stream.BitsPerComponent = 8
    stream.ColorSpace = colorspace
    for key in _IMAGE_KEYS_KEPT:
        if key in template:
            stream[key] = template[key]
    # Maske per Stanzbild übernehmen, Farbschlüssel-Masken passen nach der Änderung nicht mehr
    mask = template.get("/Mask")
    if isinstance(mask, pikepdf.Stream):
        stream.Mask = mask
    return stream
