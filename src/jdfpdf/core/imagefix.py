"""Bildkorrektur: Helligkeit, Kontrast und Sättigung eingebetteter Bilder ändern.

Bilder werden seitenweise ersetzt: Ressourcen (und Formulare, in denen Bilder
stecken) werden für die Seite kopiert, damit andere Seiten mit demselben Bild
unverändert bleiben. Der Farbraum (auch ICC) bleibt erhalten, indizierte Bilder
werden zu RGB. CMYK wird im Farbauftrag gerechnet: heller heißt weniger Farbe.
"""

from __future__ import annotations

from dataclasses import dataclass

import pikepdf
from pikepdf import Name
from PIL import Image, ImageChops, ImageEnhance

from .colorspace import family
from .pdfdoc import PdfDocument
from .pdfimage import copy_dict, load_image, write_image


@dataclass
class ImageInfo:
    path: str  # Ressourcenpfad, z. B. "Im1" oder "Fm0/Im1"
    width: int
    height: int
    colorspace: str
    bits: int
    editable: bool


@dataclass
class Adjustment:
    brightness: float = 1.0  # Faktoren, 1.0 = unverändert
    contrast: float = 1.0
    saturation: float = 1.0

    @property
    def identity(self) -> bool:
        return self.brightness == self.contrast == self.saturation == 1.0


def _iter_images(resources, prefix: str = "", depth: int = 0):
    xobjects = resources.get("/XObject") if resources is not None else None
    if xobjects is None or depth > 8:
        return
    for name, obj in xobjects.items():
        path = prefix + str(name)[1:]
        subtype = obj.get("/Subtype")
        if subtype == Name.Image:
            yield path, obj
        elif subtype == Name.Form:
            yield from _iter_images(obj.get("/Resources"), path + "/", depth + 1)


def page_images(doc: PdfDocument, index: int) -> list[ImageInfo]:
    result = []
    for path, obj in _iter_images(doc.pdf.pages[index].obj.get("/Resources")):
        space = obj.get("/ColorSpace")
        name = "ImageMask" if obj.get("/ImageMask", False) else (family(space) if space is not None else "?")
        if name == "ICCBased":
            name = f"ICC ({int(space[1].N)})"
        result.append(ImageInfo(path, int(obj.Width), int(obj.Height), name,
                                int(obj.get("/BitsPerComponent", 1)), load_image(obj) is not None))
    return result


def _channel_ops(adj: Adjustment):
    def level(v: float) -> int:
        v = v * adj.brightness
        v = 128 + (v - 128) * adj.contrast
        return int(min(max(round(v), 0), 255))

    return [level(v) for v in range(256)]


def adjust(image: Image.Image, adj: Adjustment) -> Image.Image:
    """Korrektur auf ein PIL-Bild (L, RGB, CMYK) anwenden."""
    table = _channel_ops(adj)
    if image.mode == "CMYK":
        # im Helligkeitsraum rechnen: invertieren, anpassen, zurück
        c, m, y, k = (ImageChops.invert(ch).point(table) for ch in image.split())
        if adj.saturation != 1.0:
            third = [v // 3 for v in range(256)]
            mean = ImageChops.add(ImageChops.add(c.point(third), m.point(third)), y.point(third))
            c, m, y = (Image.blend(mean, ch, adj.saturation) for ch in (c, m, y))
        return Image.merge("CMYK", [ImageChops.invert(ch) for ch in (c, m, y, k)])
    result = image.point(table * len(image.getbands()))
    if image.mode == "RGB" and adj.saturation != 1.0:
        result = ImageEnhance.Color(result).enhance(adj.saturation)
    return result


def _keep_colorspace(obj, image: Image.Image):
    space = obj.get("/ColorSpace")
    if space is None or family(space) == "Indexed":
        return None
    if family(space) == "ICCBased" and int(space[1].N) != len(image.getbands()):
        return None
    return space


def adjust_images(doc: PdfDocument, index: int, adj: Adjustment, paths: list[str] | None = None) -> int:
    """Bilder der Seite ``index`` korrigieren (alle oder nur ``paths``); liefert die Anzahl."""
    if adj.identity:
        return 0
    pdf = doc.pdf
    page = doc.pdf.pages[index].obj
    count = 0

    def process(owner, prefix: str, depth: int) -> bool:
        nonlocal count
        resources = owner.get("/Resources")
        xobjects = resources.get("/XObject") if resources is not None else None
        if xobjects is None or depth > 8:
            return False
        replaced: dict = {}
        for name, obj in xobjects.items():
            path = prefix + str(name)[1:]
            subtype = obj.get("/Subtype")
            if subtype == Name.Image and (paths is None or path in paths):
                image = load_image(obj)
                if image is None:
                    continue
                new = write_image(pdf, adjust(image, adj), obj, _keep_colorspace(obj, image))
                replaced[name] = pdf.make_indirect(new)
                count += 1
            elif subtype == Name.Form:
                copy = pdf.make_indirect(_copy_stream(pdf, obj))
                if process(copy, path + "/", depth + 1):
                    replaced[name] = copy
        if not replaced:
            return False
        new_resources = copy_dict(resources)
        new_xobjects = copy_dict(xobjects)
        for name, obj in replaced.items():
            new_xobjects[name] = obj
        new_resources.XObject = new_xobjects
        owner.Resources = new_resources
        return True

    process(page, "", 0)
    return count


def _copy_stream(pdf: pikepdf.Pdf, stream: pikepdf.Stream) -> pikepdf.Stream:
    copy = pdf.make_stream(b"")
    copy.write(stream.read_raw_bytes(), filter=stream.get("/Filter"), decode_parms=stream.get("/DecodeParms"))
    for key, value in stream.items():
        if key not in ("/Length", "/Filter", "/DecodeParms"):
            copy[key] = value
    return copy
