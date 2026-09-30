"""Medien (Papiere) und Medienkatalog.

Der Katalog wird lokal als JSON gespeichert und kann aus einer JMF-Antwort
eines JDF-fähigen Controllers (PRISMAsync, Fiery) importiert werden.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from lxml import etree

MM = 72 / 25.4


@dataclass
class Media:
    name: str
    width_pt: float = 210 * MM
    height_pt: float = 297 * MM
    weight_gsm: float | None = None
    media_type: str = "Paper"  # JDF MediaType: Paper, Transparency, …
    color: str | None = None  # JDF NamedColor, z. B. White, Yellow, Blue
    coating: str | None = None  # JDF FrontCoatings: None, Coated, Glossy, Matte …
    thickness_um: float | None = None  # für Rückenbreite und Creep
    pre_punched: bool = False
    tab_count: int = 0  # >0: Registerpapier mit so vielen Taben pro Satz

    @property
    def thickness_mm(self) -> float:
        """Papierdicke in mm; geschätzt aus der Grammatur (Volumen 1,0), falls unbekannt."""
        if self.thickness_um:
            return self.thickness_um / 1000
        return (self.weight_gsm or 80) / 1000

    def label(self) -> str:
        parts = [self.name, f"{self.width_pt / MM:.0f}×{self.height_pt / MM:.0f} mm"]
        if self.weight_gsm:
            parts.append(f"{self.weight_gsm:g} g/m²")
        return ", ".join(parts)


@dataclass
class MediaCatalog:
    media: list[Media] = field(default_factory=list)

    def get(self, name: str) -> Media | None:
        return next((m for m in self.media if m.name == name), None)

    def add(self, media: Media) -> None:
        """Hinzufügen oder gleichnamigen Eintrag ersetzen."""
        self.media = [m for m in self.media if m.name != media.name] + [media]

    def remove(self, name: str) -> None:
        self.media = [m for m in self.media if m.name != name]

    # --- Speichern ----------------------------------------------------------

    def to_json(self) -> str:
        return json.dumps([asdict(m) for m in self.media], indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "MediaCatalog":
        known = {f.name for f in fields(Media)}
        return cls([Media(**{k: v for k, v in item.items() if k in known}) for item in json.loads(text)])

    def save(self, path: Path | None = None) -> None:
        path = path or default_catalog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> "MediaCatalog":
        path = path or default_catalog_path()
        if not path.exists():
            return cls(default_media())
        return cls.from_json(path.read_text(encoding="utf-8"))

    # --- Import -------------------------------------------------------------

    def import_jmf(self, xml: bytes) -> int:
        """Medien aus einer JMF-Antwort (Resource-Query ``Media``) übernehmen."""
        imported = parse_jmf_media(xml)
        for media in imported:
            self.add(media)
        return len(imported)


def parse_jmf_media(xml: bytes) -> list[Media]:
    root = etree.fromstring(xml)
    result = []
    for node in root.iter("{*}Media"):
        name = node.get("DescriptiveName") or node.get("MediaName") or node.get("ID")
        if not name:
            continue
        width, height = 210 * MM, 297 * MM
        if node.get("Dimension"):
            try:
                width, height = (float(v) for v in node.get("Dimension").split()[:2])
            except ValueError:
                pass
        result.append(
            Media(
                name=name,
                width_pt=width,
                height_pt=height,
                weight_gsm=_float(node.get("Weight")),
                media_type=node.get("MediaType", "Paper"),
                color=node.get("MediaColorName") or node.get("Color"),
                coating=node.get("FrontCoatings"),
                thickness_um=_float(node.get("Thickness")),
                pre_punched=node.get("HoleType") not in (None, "None"),
                tab_count=int(_float(node.get("MediaSetCount")) or 0) if node.get("MediaType") == "Tab" else 0,
            )
        )
    return result


def default_media() -> list[Media]:
    return [
        Media("A4 80 g", weight_gsm=80),
        Media("A4 160 g", weight_gsm=160),
        Media("A4 300 g Umschlag", weight_gsm=300, coating="Coated"),
        Media("A3 120 g", 297 * MM, 420 * MM, weight_gsm=120),
        Media("SRA3 170 g", 320 * MM, 450 * MM, weight_gsm=170),
        Media("A4 Register 5er", weight_gsm=160, media_type="Tab", tab_count=5),
        Media("A4 gelb 80 g", weight_gsm=80, color="Yellow"),
        Media("A4 vorgelocht 80 g", weight_gsm=80, pre_punched=True),
    ]


# Anzeigefarben für JDF-NamedColor (Seitenliste, Softproof)
MEDIA_RGB = {
    "white": "#ffffff", "yellow": "#fff4a3", "blue": "#cfe3ff", "green": "#d4f5d0", "pink": "#ffd6e7",
    "red": "#ffc9c2", "orange": "#ffe0b8", "gray": "#e3e3e3", "grey": "#e3e3e3", "ivory": "#fffbe8",
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "jdfpdf"


def default_catalog_path() -> Path:
    return config_dir() / "media.json"


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None
