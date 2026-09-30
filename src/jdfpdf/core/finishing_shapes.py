"""Lage von Heftklammern, Lochung und Falzlinien auf einer Seite (für Vorschau und Softproof)."""

from __future__ import annotations

from dataclasses import dataclass

from .jdf import Finishing, Fold, Punch, Staple

MM = 72 / 25.4
STAPLE_LENGTH = 12 * MM
HOLE_RADIUS = 2.75 * MM


@dataclass
class Shape:
    kind: str  # staple, hole, fold
    x: float  # Mittelpunkt bzw. Startpunkt (Seitenkoordinaten, y nach oben)
    y: float
    horizontal: bool = True  # staple
    angle: float = 0.0  # staple: Drehung in Grad (Bildschirm, im Uhrzeigersinn)
    x2: float = 0.0  # fold: Endpunkt
    y2: float = 0.0


def finishing_shapes(trim: tuple[float, float, float, float], fin: Finishing, index: int) -> list[Shape]:
    """Formen im Endformat ``trim``; bei Rückseiten (ungerader Index) gespiegelt."""
    x0, y0, x1, y1 = trim
    shapes: list[Shape] = []
    back = index % 2 == 1
    m = 8 * MM
    left_edge = x1 if back else x0
    sign = -1 if back else 1

    def staple(cx: float, cy: float, horizontal: bool, angle: float = 0) -> None:
        shapes.append(Shape("staple", cx, cy, horizontal, angle))

    if fin.staple == Staple.TOP_LEFT:
        staple((x1 - m) if back else (x0 + m), y1 - m, True, 45 if back else -45)
    elif fin.staple == Staple.TOP_RIGHT:
        staple((x0 + m) if back else (x1 - m), y1 - m, True, -45 if back else 45)
    elif fin.staple == Staple.LEFT_TWO:
        for f in (0.25, 0.75):
            staple(left_edge + sign * m, y0 + (y1 - y0) * f, False)
    elif fin.staple == Staple.TOP_TWO:
        for f in (0.25, 0.75):
            staple(x0 + (x1 - x0) * f, y1 - m, True)
    elif fin.staple == Staple.SADDLE:
        for f in (0.25, 0.75):
            staple(left_edge, y0 + (y1 - y0) * f, False)

    hole_margin = 12 * MM
    if fin.punch in (Punch.TWO_LEFT, Punch.FOUR_LEFT):
        cy = (y0 + y1) / 2
        offsets = (-40 * MM, 40 * MM) if fin.punch == Punch.TWO_LEFT else (-120 * MM, -40 * MM, 40 * MM, 120 * MM)
        for off in offsets:
            shapes.append(Shape("hole", left_edge + sign * hole_margin, cy + off))
    elif fin.punch == Punch.TWO_TOP:
        cx = (x0 + x1) / 2
        for off in (-40 * MM, 40 * MM):
            shapes.append(Shape("hole", cx + off, y1 - hole_margin))

    if fin.fold == Fold.HALF:
        mid = (x0 + x1) / 2
        shapes.append(Shape("fold", mid, y0, x2=mid, y2=y1))
    elif fin.fold == Fold.Z:
        for f in (1 / 3, 2 / 3):
            x = x0 + (x1 - x0) * f
            shapes.append(Shape("fold", x, y0, x2=x, y2=y1))
    return shapes
