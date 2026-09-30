"""Sonderfarben (Separation/DeviceN): auflisten, umbenennen, Ersatzwerte ändern, zusammenführen.

Farbräume können als indirekte Objekte oder direkt in Ressourcen, Bildern und
Verläufen stehen; deshalb wird das ganze Dokument durchlaufen. Die Farbbibliothek
(``SpotLibrary``) speichert eigene Farben mit CMYK- und/oder Lab-Werten.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pikepdf
from pikepdf import Name

from .colorspace import family, name_str, spot_cmyk
from .media import config_dir

RESERVED = {"All", "None", "Cyan", "Magenta", "Yellow", "Black"}


@dataclass
class SpotColor:
    name: str
    alternate: str  # Familie des Ersatzfarbraums
    cmyk: tuple[float, float, float, float] | None  # Ersatzwert bei 100 %, in Prozent
    uses: int  # Anzahl der Farbraumdefinitionen
    in_devicen: bool = False


def _walk(obj, visit, seen: set) -> None:
    if isinstance(obj, pikepdf.Stream):
        items = obj.stream_dict.items()
    elif isinstance(obj, pikepdf.Dictionary):
        items = obj.items()
    elif isinstance(obj, pikepdf.Array):
        if len(obj) and family(obj) in ("Separation", "DeviceN"):
            visit(obj)
        items = enumerate(obj)
    else:
        return
    for _, child in items:
        if isinstance(child, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
            if child.is_indirect:
                continue  # indirekte Objekte besucht die äußere Schleife
            _walk(child, visit, seen)


def _spaces(doc) -> list[pikepdf.Array]:
    found: list[pikepdf.Array] = []
    seen: set = set()
    for obj in doc.pdf.objects:
        _walk(obj, found.append, seen)
    return found


def spot_colors(doc) -> list[SpotColor]:
    """Alle im Dokument definierten Sonderfarben (ohne All/None und Prozessfarben)."""
    colors: dict[str, SpotColor] = {}
    for space in _spaces(doc):
        if family(space) == "Separation":
            name = name_str(space[1])
            if name in RESERVED:
                continue
            entry = colors.get(name)
            if entry is None:
                cmyk = spot_cmyk(space)
                colors[name] = SpotColor(name, family(space[2]),
                                         tuple(round(v * 100, 1) for v in cmyk) if cmyk else None, 1)
            else:
                entry.uses += 1
        else:
            for n in space[1]:
                name = name_str(n)
                if name in RESERVED:
                    continue
                entry = colors.setdefault(name, SpotColor(name, family(space[2]), None, 0, True))
                entry.uses += 1
                entry.in_devicen = True
    return sorted(colors.values(), key=lambda c: c.name.lower())


def rename_spot(doc, old: str, new: str) -> int:
    """Sonderfarbe umbenennen; liefert die Anzahl geänderter Farbräume."""
    if not new or new in RESERVED:
        raise ValueError(f"Ungültiger Name: {new!r}")
    changed = 0
    for space in _spaces(doc):
        if family(space) == "Separation":
            if name_str(space[1]) == old:
                space[1] = Name("/" + new)
                changed += 1
        else:
            names = [name_str(n) for n in space[1]]
            if old in names and new not in names:
                space[1] = pikepdf.Array([Name("/" + (new if n == old else n)) for n in names])
                changed += 1
    return changed


def _tint_function(pdf: pikepdf.Pdf, c1: list[float], c0: list[float]) -> pikepdf.Object:
    return pdf.make_indirect(pikepdf.Dictionary(
        FunctionType=2, Domain=pikepdf.Array([0, 1]), C0=pikepdf.Array(c0), C1=pikepdf.Array(c1), N=1,
    ))


def _lab_space() -> pikepdf.Array:
    return pikepdf.Array([Name.Lab, pikepdf.Dictionary(
        WhitePoint=pikepdf.Array([0.9642, 1.0, 0.8249]), Range=pikepdf.Array([-128, 127, -128, 127]),
    )])


def set_spot_alternate(doc, name: str, cmyk: tuple[float, float, float, float] | None = None,
                       lab: tuple[float, float, float] | None = None) -> int:
    """Ersatzfarbe einer Separation setzen (CMYK in Prozent oder Lab); DeviceN bleibt unverändert."""
    if cmyk is None and lab is None:
        raise ValueError("CMYK oder Lab angeben")
    changed = 0
    for space in _spaces(doc):
        if family(space) != "Separation" or name_str(space[1]) != name:
            continue
        if lab is not None:
            space[2] = _lab_space()
            space[3] = _tint_function(doc.pdf, list(lab), [100.0, 0.0, 0.0])
        else:
            space[2] = Name.DeviceCMYK
            space[3] = _tint_function(doc.pdf, [v / 100 for v in cmyk], [0.0, 0.0, 0.0, 0.0])
        changed += 1
    return changed


def merge_spots(doc, source: str, target: str) -> int:
    """``source`` in ``target`` aufgehen lassen: umbenennen und Ersatzwert von ``target`` übernehmen."""
    colors = {c.name: c for c in spot_colors(doc)}
    changed = rename_spot(doc, source, target)
    target_color = colors.get(target)
    if target_color is not None and target_color.cmyk is not None:
        set_spot_alternate(doc, target, cmyk=target_color.cmyk)
    return changed


# --- Farbbibliothek -----------------------------------------------------------------


@dataclass
class LibraryColor:
    name: str
    cmyk: tuple[float, float, float, float] | None = None  # Prozent
    lab: tuple[float, float, float] | None = None


def default_library() -> list[LibraryColor]:
    """Gängige Produktionsfarben (Stanz-/Schneidkonturen, Lack, Weiß)."""
    return [
        LibraryColor("CutContour", (0, 100, 0, 0)),
        LibraryColor("Thru-cut", (100, 0, 0, 0)),
        LibraryColor("Stanzkontur", (0, 100, 0, 0)),
        LibraryColor("Varnish", (20, 0, 0, 0)),
        LibraryColor("White", (0, 0, 0, 10)),
    ]


class SpotLibrary:
    def __init__(self, colors: list[LibraryColor] | None = None) -> None:
        self.colors = list(colors) if colors is not None else default_library()

    def get(self, name: str) -> LibraryColor | None:
        return next((c for c in self.colors if c.name == name), None)

    def add(self, color: LibraryColor) -> None:
        self.remove(color.name)
        self.colors.append(color)
        self.colors.sort(key=lambda c: c.name.lower())

    def remove(self, name: str) -> None:
        self.colors = [c for c in self.colors if c.name != name]

    def to_json(self) -> str:
        return json.dumps([asdict(c) for c in self.colors], indent=1, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "SpotLibrary":
        items = json.loads(text)
        return cls([LibraryColor(i["name"], tuple(i["cmyk"]) if i.get("cmyk") else None,
                                 tuple(i["lab"]) if i.get("lab") else None) for i in items])

    def save(self, path: Path | None = None) -> None:
        path = Path(path or default_library_path())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> "SpotLibrary":
        path = Path(path or default_library_path())
        if path.exists():
            return cls.from_json(path.read_text(encoding="utf-8"))
        return cls()

    def import_from(self, doc) -> int:
        """Sonderfarben eines Dokuments mit ihrem CMYK-Ersatzwert übernehmen."""
        count = 0
        for color in spot_colors(doc):
            if color.cmyk is not None:
                self.add(LibraryColor(color.name, color.cmyk))
                count += 1
        return count

    def apply(self, doc) -> int:
        """Ersatzwerte aus der Bibliothek auf gleichnamige Sonderfarben im Dokument anwenden."""
        count = 0
        for color in spot_colors(doc):
            entry = self.get(color.name)
            if entry is not None:
                count += set_spot_alternate(doc, color.name, cmyk=entry.cmyk, lab=entry.lab)
        return count


def default_library_path() -> Path:
    return config_dir() / "spots.json"
