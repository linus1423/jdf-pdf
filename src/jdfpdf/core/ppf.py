"""CIP3-PPF: Auszugsvorschau je Druckfarbe und Farbzonenberechnung für Offsetmaschinen.

Ablauf je Bogenseite (= Seite des ausgeschossenen PDFs):

1. Farben der Seite auf den Farbauftrag eines Auszugs abbilden (``remap``):
   Prozessfarben über CMYK, Sonderfarben nur im eigenen Auszug.
2. Mit PDFium in Graustufen rendern (kein Ghostscript), 0 = 100 % Farbe.
3. Flächendeckung je Farbzone für ein Maschinenprofil berechnen.
4. PPF-Datei (CIP3 PPF 3.0) mit einer Vorschau je Auszug schreiben.

Bekannte Vereinfachungen: Überdrucken wird nicht simuliert (Aussparen), Gitterverläufe
und Inline-Bilder gehen unverändert in jeden Auszug ein (werden gemeldet).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from pikepdf import Name
from PIL import Image, ImageChops, ImageMath, ImageStat

from .colorspace import PROCESS, family, ink_amount, name_str
from .media import config_dir
from .pdfdoc import PdfDocument
from .pdfimage import load_image, write_image
from .remap import Remapper

MM = 72 / 25.4
DEFAULT_DPI = 50.8  # übliche CIP3-Vorschauauflösung
PPF_MIME = "application/vnd.cip3-ppf"


# --- Maschinenprofile ---------------------------------------------------------------


@dataclass
class PressProfile:
    name: str
    zone_count: int = 23
    zone_width_mm: float = 32.5
    centered: bool = True  # Bogen mittig zur Farbzonenreihe, sonst an Zone 1 angelegt
    offset_mm: float = 0.0  # zusätzliche Verschiebung des Bogens quer zur Laufrichtung
    zones_along_height: bool = False  # Zonen über die Bogenhöhe statt über die Breite

    @property
    def width_mm(self) -> float:
        return self.zone_count * self.zone_width_mm


def default_profiles() -> list[PressProfile]:
    return [
        PressProfile("32,5 mm × 16 Zonen (B3)", 16, 32.5),
        PressProfile("32,5 mm × 23 Zonen (B2)", 23, 32.5),
        PressProfile("32,5 mm × 32 Zonen (B1)", 32, 32.5),
        PressProfile("30 mm × 26 Zonen", 26, 30.0),
    ]


def default_profiles_path() -> Path:
    return config_dir() / "presses.json"


def load_profiles(path: Path | None = None) -> list[PressProfile]:
    path = Path(path or default_profiles_path())
    if path.exists():
        return [PressProfile(**item) for item in json.loads(path.read_text(encoding="utf-8"))]
    return default_profiles()


def save_profiles(profiles: list[PressProfile], path: Path | None = None) -> None:
    path = Path(path or default_profiles_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(p) for p in profiles], indent=1, ensure_ascii=False), encoding="utf-8")


# --- Auszüge -----------------------------------------------------------------------


def page_separations(doc: PdfDocument, index: int) -> list[str]:
    """CMYK plus die auf der Seite verwendeten Sonderfarben (in Reihenfolge des Auftretens)."""
    names: list[str] = []
    seen: set = set()

    def walk(obj) -> None:
        if isinstance(obj, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)) and obj.is_indirect:
            if obj.objgen in seen:
                return
            seen.add(obj.objgen)
        if isinstance(obj, pikepdf.Array):
            if len(obj) > 1 and family(obj) == "Separation":
                names.append(name_str(obj[1]))
            elif len(obj) > 1 and family(obj) == "DeviceN":
                names.extend(name_str(n) for n in obj[1])
            for child in obj:
                walk(child)
        elif isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
            items = obj.stream_dict.items() if isinstance(obj, pikepdf.Stream) else obj.items()
            for key, child in items:
                if key not in ("/Parent", "/Font", "/Metadata", "/Annots"):
                    walk(child)

    walk(doc.pdf.pages[index].obj.get("/Resources"))
    spots = [n for n in dict.fromkeys(names) if n not in PROCESS and n not in ("All", "None")]
    return [*PROCESS, *spots]


def _rgb_to_cmyk(image: Image.Image) -> Image.Image:
    """RGB nach CMYK mit vollem Schwarzaufbau, wie ``colorspace.rgb_to_cmyk``."""
    r, g, b = image.split()
    mx = ImageChops.lighter(ImageChops.lighter(r, g), b)
    k = ImageChops.invert(mx)
    divisor = ImageChops.lighter(mx, Image.new("L", image.size, 1))
    channels = [ImageMath.lambda_eval(lambda a: (a["m"] - a["c"]) * 255 / a["d"], m=mx, c=c, d=divisor).convert("L")
                for c in (r, g, b)]
    return Image.merge("CMYK", [*channels, k])


def _separation_plate(image: Image.Image, separation: str) -> Image.Image | None:
    """Graustufenbild des Auszugs (weiß = keine Farbe) oder ``None``, wenn leer."""
    if separation not in PROCESS:
        return None
    if image.mode == "L":
        return image if separation == "Black" else None
    cmyk = image if image.mode == "CMYK" else _rgb_to_cmyk(image.convert("RGB"))
    return ImageChops.invert(cmyk.split()[PROCESS.index(separation)])


def _spot_image(obj, separation: str) -> Image.Image | None | bool:
    """Einkanalige Sonderfarbenbilder; ``False``, wenn nicht lesbar."""
    space = obj.ColorSpace
    if int(obj.get("/BitsPerComponent", 8)) != 8:
        return False
    try:
        data = obj.read_bytes()
    except Exception:
        return False
    w, h = int(obj.Width), int(obj.Height)
    if len(data) < w * h:
        return False
    if name_str(space[1]) not in (separation, "All"):
        return None
    return ImageChops.invert(Image.frombytes("L", (w, h), data[: w * h]))


def _image_fn(pdf: pikepdf.Pdf, separation: str, skipped: Counter):
    def blank(obj):
        return write_image(pdf, Image.new("L", (1, 1), 255), obj, Name.DeviceGray)

    def convert(obj):
        space = obj.get("/ColorSpace")
        fam = family(space) if space is not None else ""
        if fam == "Separation":
            plate = _spot_image(obj, separation)
        elif fam == "DeviceN":
            skipped["image"] += 1
            return None
        else:
            image = load_image(obj)
            if image is None:
                skipped["image"] += 1
                return None
            image.thumbnail((1200, 1200))  # für die Vorschau genügt eine geringe Auflösung
            plate = _separation_plate(image, separation)
        if plate is False:
            skipped["image"] += 1
            return None
        return blank(obj) if plate is None else write_image(pdf, plate, obj, Name.DeviceGray)

    return convert


def _single_page(doc: PdfDocument, index: int) -> bytes:
    single = pikepdf.new()
    single.pages.append(doc.pdf.pages[index])
    return PdfDocument(single).to_bytes()


def render_separation(page_pdf: bytes, separation: str, dpi: float = DEFAULT_DPI,
                      skipped: Counter | None = None) -> Image.Image:
    """Auszug einer einseitigen PDF über die ganze MediaBox; L-Bild, 0 = 100 % Farbe."""
    doc = PdfDocument.from_bytes(page_pdf)
    extra: Counter = Counter()
    remapper = Remapper(doc.pdf, lambda space, values: _plate_gray(space, values, separation),
                        _image_fn(doc.pdf, separation, extra), reset_default=True)
    remapper.page(doc.pdf.pages[0])
    if skipped is not None:
        skipped.update(remapper.skipped + extra)
    rendered = pdfium.PdfDocument(doc.to_bytes())
    try:
        page = rendered[0]
        page.set_cropbox(*page.get_mediabox())
        image = page.render(scale=dpi / 72, grayscale=True, may_draw_forms=False).to_pil()
        page.close()
    finally:
        rendered.close()
    return image.convert("L")


def _plate_gray(space, values, separation: str) -> float | None:
    amount = ink_amount(space, values, separation)
    return None if amount is None else 1.0 - amount


# --- Farbzonen ---------------------------------------------------------------------


def zone_coverage(plate: Image.Image, sheet_mm: tuple[float, float], profile: PressProfile) -> list[float]:
    """Flächendeckung (0–100 %) je Farbzone; Zonen außerhalb des Bogens haben 0 %."""
    ink = ImageChops.invert(plate)
    if profile.zones_along_height:
        ink = ink.transpose(Image.Transpose.ROTATE_90)
        sheet_width = sheet_mm[1]
    else:
        sheet_width = sheet_mm[0]
    px_per_mm = ink.width / sheet_width
    start_mm = (profile.width_mm - sheet_width) / 2 if profile.centered else 0.0
    start_mm += profile.offset_mm
    result = []
    for zone in range(profile.zone_count):
        x0 = (zone * profile.zone_width_mm - start_mm) * px_per_mm
        x1 = x0 + profile.zone_width_mm * px_per_mm
        left, right = max(int(round(x0)), 0), min(int(round(x1)), ink.width)
        if right <= left:
            result.append(0.0)
            continue
        mean = ImageStat.Stat(ink.crop((left, 0, right, ink.height))).mean[0]
        result.append(round(mean / 255 * 100, 1))
    return result


@dataclass
class SeparationResult:
    name: str
    plate: Image.Image  # 0 = 100 % Farbe
    coverage: float  # Flächendeckung des ganzen Bogens in %
    zones: list[float]


@dataclass
class SideResult:
    page: int  # Seitenindex im (ausgeschossenen) Dokument
    width_pt: float
    height_pt: float
    separations: list[SeparationResult]
    skipped: Counter = field(default_factory=Counter)


def analyse_side(doc: PdfDocument, index: int, profile: PressProfile, dpi: float = DEFAULT_DPI,
                 separations: list[str] | None = None) -> SideResult:
    x0, y0, x1, y1 = doc.box(index, "MediaBox")
    width, height = x1 - x0, y1 - y0
    if doc.page_rotation(index) % 180:
        width, height = height, width
    page_pdf = _single_page(doc, index)
    skipped: Counter = Counter()
    results = []
    for name in separations or page_separations(doc, index):
        plate = render_separation(page_pdf, name, dpi, skipped)
        coverage = round((255 - ImageStat.Stat(plate).mean[0]) / 255 * 100, 1)
        zones = zone_coverage(plate, (width / MM, height / MM), profile)
        results.append(SeparationResult(name, plate, coverage, zones))
    # Sonderfarben ohne Deckung weglassen, Prozessfarben bleiben immer drin
    results = [r for r in results if r.name in PROCESS or r.coverage > 0]
    return SideResult(index, width, height, results, skipped)


@dataclass
class Sheet:
    number: int
    front: SideResult
    back: SideResult | None = None


def analyse(doc: PdfDocument, profile: PressProfile, duplex: bool = False, dpi: float = DEFAULT_DPI) -> list[Sheet]:
    """Alle Bögen analysieren; bei ``duplex`` bilden je zwei Seiten Vorder- und Rückseite."""
    sheets = []
    step = 2 if duplex else 1
    for number, index in enumerate(range(0, doc.page_count, step), start=1):
        front = analyse_side(doc, index, profile, dpi)
        back = analyse_side(doc, index + 1, profile, dpi) if duplex and index + 1 < doc.page_count else None
        sheets.append(Sheet(number, front, back))
    return sheets


# --- PPF schreiben ---------------------------------------------------------------------


def _ps_string(text: str) -> str:
    safe = text.encode("latin-1", "replace").decode("latin-1")
    return "(" + safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _side_block(side: SideResult, tag: str, dpi: float) -> list[str]:
    lines = [f"CIP3Begin{tag}",
             "/CIP3AdmSeparationNames [" + " ".join(_ps_string(s.name) for s in side.separations) + "] def",
             "CIP3BeginPreviewImage"]
    for sep in side.separations:
        w, h = sep.plate.size
        lines += [
            "CIP3BeginSeparation",
            f"/CIP3PreviewImageWidth {w} def",
            f"/CIP3PreviewImageHeight {h} def",
            "/CIP3PreviewImageBitsPerComp 8 def",
            "/CIP3PreviewImageComponents 1 def",
            f"/CIP3PreviewImageMatrix [{w} 0 0 {-h} 0 {h}] def",
            f"/CIP3PreviewImageResolution [{dpi:g} {dpi:g}] def",
            "/CIP3PreviewImageEncoding /ASCIIHexDecode def",
            "/CIP3PreviewImageCompression /None def",
            "% Farbzonen (%): " + " ".join(f"{z:g}" for z in sep.zones),
            "CIP3PreviewImage",
        ]
        data = sep.plate.tobytes().hex()
        lines += [data[i:i + 128] for i in range(0, len(data), 128)]
        lines[-1] += ">"
        lines.append("CIP3EndSeparation")
    lines += ["CIP3EndPreviewImage", f"CIP3End{tag}"]
    return lines


def write_ppf(sheet: Sheet, job_name: str, dpi: float = DEFAULT_DPI, profile: PressProfile | None = None) -> bytes:
    """Eine PPF-Datei (CIP3 PPF 3.0) für einen Bogen."""
    front = sheet.front
    lines = [
        "%!PS-Adobe-3.0",
        "%%CIP3-File Version 3.0",
        "CIP3BeginSheet",
        f"/CIP3AdmJobName {_ps_string(job_name)} def",
        "/CIP3AdmMake (jdfpdf) def",
        "/CIP3AdmModel (jdfpdf) def",
        "/CIP3AdmSoftware (jdfpdf) def",
        f"/CIP3AdmCreationTime {_ps_string(time.strftime('%a %b %d %H:%M:%S %Y'))} def",
        f"/CIP3AdmSheetName {_ps_string(str(sheet.number))} def",
        f"/CIP3AdmPSExtent [{front.width_pt:.2f} {front.height_pt:.2f}] def",
        "/CIP3TransferFilmCurveData [0.0 0.0 1.0 1.0] def",
        "/CIP3TransferPlateCurveData [0.0 0.0 1.0 1.0] def",
    ]
    if profile is not None:
        lines.append(f"% Maschinenprofil: {profile.name}, {profile.zone_count} Zonen x {profile.zone_width_mm:g} mm")
    lines += _side_block(front, "Front", dpi)
    if sheet.back is not None:
        lines += _side_block(sheet.back, "Back", dpi)
    lines += ["CIP3EndSheet", "%%CIP3EndOfFile", ""]
    return "\n".join(lines).encode("latin-1")


def zones_csv(sheets: list[Sheet]) -> str:
    """Farbzonen aller Bögen als CSV (Semikolon, Dezimalkomma)."""
    width = max((len(s.zones) for sh in sheets for side in (sh.front, sh.back) if side
                 for s in side.separations), default=0)
    rows = [";".join(["Bogen", "Seite", "Farbe", "Deckung %", *[f"Zone {i + 1}" for i in range(width)]])]
    for sheet in sheets:
        for label, side in (("Vorne", sheet.front), ("Hinten", sheet.back)):
            if side is None:
                continue
            for sep in side.separations:
                values = [f"{sep.coverage:g}", *[f"{z:g}" for z in sep.zones]]
                rows.append(";".join([str(sheet.number), label, sep.name, *[v.replace(".", ",") for v in values]]))
    return "\n".join(rows) + "\n"


def ppf_names(stem: str, count: int) -> list[str]:
    return [f"{stem}.ppf"] if count == 1 else [f"{stem}_{i:03d}.ppf" for i in range(1, count + 1)]
