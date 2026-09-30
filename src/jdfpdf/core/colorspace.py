"""Farbräume auflösen und Farbwerte näherungsweise nach Grau bzw. CMYK umrechnen."""

from __future__ import annotations

import pikepdf
from pikepdf import Name

from .pdffunc import UnsupportedFunction, evaluate

# Anteil der Prozessfarben an der Deckung, um Grau aus CMYK/DeviceN zu schätzen
PROCESS_WEIGHTS = {"Cyan": 0.3, "Magenta": 0.59, "Yellow": 0.11, "Black": 1.0}
_ABBREVIATIONS = {"G": "DeviceGray", "RGB": "DeviceRGB", "CMYK": "DeviceCMYK", "I": "Indexed"}


def name_str(obj) -> str:
    return str(obj)[1:] if isinstance(obj, Name) else str(obj)


def family(space) -> str:
    """Farbraumfamilie, z. B. ``DeviceRGB``, ``ICCBased``, ``Separation``."""
    if isinstance(space, Name):
        name = name_str(space)
    elif isinstance(space, pikepdf.Array) and len(space):
        name = name_str(space[0])
    else:
        return ""
    return _ABBREVIATIONS.get(name, name)


def resolve(resources, name) -> object | None:
    """Farbraum zum Namen im Inhaltsstrom (Gerätefarbraum oder Eintrag in /ColorSpace)."""
    if family(name) in ("DeviceGray", "DeviceRGB", "DeviceCMYK", "Pattern"):
        return name
    spaces = resources.get("/ColorSpace") if resources is not None else None
    if spaces is not None and name in spaces:
        return spaces[name]
    return None


def components(space) -> int:
    fam = family(space)
    if fam in ("DeviceGray", "CalGray", "Separation", "Indexed"):
        return 1
    if fam in ("DeviceRGB", "CalRGB", "Lab"):
        return 3
    if fam == "DeviceCMYK":
        return 4
    if fam == "ICCBased":
        return int(space[1].get("/N", 3))
    if fam == "DeviceN":
        return len(space[1])
    return 0


def rgb_gray(r: float, g: float, b: float) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def cmyk_gray(c: float, m: float, y: float, k: float) -> float:
    return max(0.0, 1.0 - min(1.0, 0.3 * c + 0.59 * m + 0.11 * y + k))


def lab_to_rgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    """CIE-Lab (D50) nach sRGB, grob; genügt für Vorschau und Grauwert."""
    fy = (L + 16) / 116
    fx, fz = fy + a / 500, fy - b / 200

    def f_inv(t: float) -> float:
        return t**3 if t > 6 / 29 else 3 * (6 / 29) ** 2 * (t - 4 / 29)

    x, y, z = 0.9642 * f_inv(fx), f_inv(fy), 0.8249 * f_inv(fz)
    r = 3.1339 * x - 1.6169 * y - 0.4906 * z
    g = -0.9788 * x + 1.9161 * y + 0.0335 * z
    bl = 0.0719 * x - 0.2290 * y + 1.4052 * z

    def gamma(v: float) -> float:
        v = min(max(v, 0.0), 1.0)
        return 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055

    return gamma(r), gamma(g), gamma(bl)


def rgb_to_cmyk(r: float, g: float, b: float) -> tuple[float, float, float, float]:
    k = 1 - max(r, g, b)
    if k >= 1:
        return 0.0, 0.0, 0.0, 1.0
    return (1 - r - k) / (1 - k), (1 - g - k) / (1 - k), (1 - b - k) / (1 - k), k


def to_cmyk(space, values: list[float]) -> tuple[float, float, float, float] | None:
    """Farbwert in CMYK (0–1) umrechnen; ``None``, wenn der Farbraum das nicht zulässt."""
    fam = family(space)
    if fam == "ICCBased":
        n = components(space)
        fam = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(n, "")
    if fam in ("DeviceGray", "CalGray"):
        return 0.0, 0.0, 0.0, 1.0 - values[0]
    if fam in ("DeviceRGB", "CalRGB"):
        return rgb_to_cmyk(*values[:3])
    if fam == "DeviceCMYK":
        return tuple(values[:4])
    if fam == "Lab":
        return rgb_to_cmyk(*lab_to_rgb(*values[:3]))
    if fam == "Separation":
        try:
            return to_cmyk(space[2], evaluate(space[3], values[:1]))
        except (UnsupportedFunction, KeyError, IndexError, ValueError, ZeroDivisionError):
            return None
    if fam == "DeviceN":
        try:
            return to_cmyk(space[2], evaluate(space[3], values))
        except (UnsupportedFunction, KeyError, IndexError, ValueError, ZeroDivisionError):
            return None
    return None


def to_gray(space, values: list[float]) -> float | None:
    """Farbwert nach Grau (0 = schwarz, 1 = weiß); ``None`` bei Mustern und ``/None``."""
    fam = family(space)
    if fam == "ICCBased":
        fam = {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(components(space), "")
    if fam in ("DeviceGray", "CalGray"):
        return values[0]
    if fam in ("DeviceRGB", "CalRGB"):
        return rgb_gray(*values[:3])
    if fam == "DeviceCMYK":
        return cmyk_gray(*values[:4])
    if fam == "Lab":
        return min(max(values[0] / 100, 0.0), 1.0)
    if fam == "Indexed":
        return _indexed_gray(space, int(values[0]))
    if fam == "Separation":
        name = name_str(space[1])
        if name == "None":
            return None
        if name == "All":
            return 1 - values[0]
        return max(0.0, 1 - values[0] * _darkness(space))
    if fam == "DeviceN":
        cmyk = to_cmyk(space, values)
        if cmyk is not None:
            return cmyk_gray(*cmyk)
        total = sum(v * PROCESS_WEIGHTS.get(name_str(n), 1.0) for v, n in zip(values, space[1]))
        return max(0.0, 1 - min(1.0, total))
    return None


def initial_values(space) -> list[float]:
    """Anfangsfarbe nach ``cs`` (PDF 1.7, 8.6.8): Sonderfarben volle Deckung, sonst schwarz."""
    fam = family(space)
    if fam in ("Separation", "DeviceN"):
        return [1.0] * components(space)
    if fam in ("DeviceCMYK",):
        return [0.0, 0.0, 0.0, 1.0]
    if fam == "ICCBased" and components(space) == 4:
        return [0.0, 0.0, 0.0, 1.0]
    return [0.0] * max(components(space), 1)


def spot_cmyk(space) -> tuple[float, float, float, float] | None:
    """CMYK-Ersatzwert einer Separation bei 100 % Tonwert."""
    return to_cmyk(space, [1.0])


def _darkness(space) -> float:
    cmyk = spot_cmyk(space)
    return 1.0 - cmyk_gray(*cmyk) if cmyk is not None else 1.0


def lookup_bytes(space) -> bytes:
    table = space[3]
    if isinstance(table, pikepdf.Stream):
        return table.read_bytes()
    return bytes(table)


def _indexed_gray(space, index: int) -> float | None:
    base = space[1]
    n = components(base)
    data = lookup_bytes(space)
    raw = data[index * n:(index + 1) * n]
    if len(raw) < n:
        return None
    if family(base) == "Lab":
        return raw[0] / 255
    return to_gray(base, [v / 255 for v in raw])


PROCESS = ("Cyan", "Magenta", "Yellow", "Black")


def ink_amount(space, values: list[float], separation: str) -> float | None:
    """Farbauftrag (0–1) der Farbe ``space``/``values`` im Auszug ``separation``.

    Prozessfarben ergeben sich aus der CMYK-Umrechnung; eine Sonderfarbe erscheint
    nur in ihrem eigenen Auszug (und ``All`` in jedem). ``None`` bei Mustern.
    """
    fam = family(space)
    if fam == "Separation":
        name = name_str(space[1])
        if name == "All":
            return values[0]
        if name == "None":
            return 0.0
        return values[0] if name == separation else 0.0
    if fam == "DeviceN":
        names = [name_str(n) for n in space[1]]
        return values[names.index(separation)] if separation in names else 0.0
    if fam == "Indexed":
        base = space[1]
        n = components(base)
        raw = lookup_bytes(space)[int(values[0]) * n:(int(values[0]) + 1) * n]
        if len(raw) < n:
            return None
        scaled = [v / 255 for v in raw]
        if family(base) == "Lab":
            scaled = [scaled[0] * 100, raw[1] - 128, raw[2] - 128] if n == 3 else scaled
        return ink_amount(base, scaled, separation)
    if fam == "Pattern":
        return None
    if separation not in PROCESS:
        return 0.0
    cmyk = to_cmyk(space, values)
    if cmyk is None:
        return None
    return min(max(cmyk[PROCESS.index(separation)], 0.0), 1.0)
