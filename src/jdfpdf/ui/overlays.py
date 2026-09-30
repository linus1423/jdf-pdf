"""WYSIWYG-Einblendungen der Weiterverarbeitung (Heftklammern, Lochung, Falzlinien)."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem

from ..core.jdf import Finishing, Fold, Punch, Staple

MM = 72 / 25.4
COLOR = QColor(200, 30, 30, 200)


def _pen(dashed: bool = False) -> QPen:
    pen = QPen(COLOR, 0)
    pen.setCosmetic(True)
    if dashed:
        pen.setDashPattern([6, 4])
    return pen


def finishing_items(view, trim: tuple[float, float, float, float], fin: Finishing, index: int) -> list[QGraphicsItem]:
    """Grafikelemente in Seitenkoordinaten der Ansicht (``view.to_scene``)."""
    x0, y0, x1, y1 = trim
    items: list[QGraphicsItem] = []
    # bei Duplex liegen Heftung/Lochung auf geraden Seiten gespiegelt; die Ansicht zeigt die Vorderseite
    back = index % 2 == 1

    def staple(cx: float, cy: float, horizontal: bool, angle: float = 0) -> None:
        length = 12 * MM
        p = view.to_scene(cx, cy)
        if horizontal:
            rect = QRectF(p.x() - length / 2, p.y() - 1.2, length, 2.4)
        else:
            rect = QRectF(p.x() - 1.2, p.y() - length / 2, 2.4, length)
        item = QGraphicsRectItem(rect)
        item.setBrush(QBrush(COLOR))
        item.setPen(_pen())
        if angle:
            item.setTransformOriginPoint(p)
            item.setRotation(angle)
        items.append(item)

    def hole(cx: float, cy: float) -> None:
        r = 2.75 * MM
        p = view.to_scene(cx, cy)
        item = QGraphicsEllipseItem(p.x() - r, p.y() - r, 2 * r, 2 * r)
        item.setPen(_pen())
        items.append(item)

    def line(ax: float, ay: float, bx: float, by: float) -> None:
        a, b = view.to_scene(ax, ay), view.to_scene(bx, by)
        item = QGraphicsLineItem(a.x(), a.y(), b.x(), b.y())
        item.setPen(_pen(dashed=True))
        items.append(item)

    m = 8 * MM
    left_edge = x1 if back else x0
    sign = -1 if back else 1
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
            hole(left_edge + sign * hole_margin, cy + off)
    elif fin.punch == Punch.TWO_TOP:
        cx = (x0 + x1) / 2
        for off in (-40 * MM, 40 * MM):
            hole(cx + off, y1 - hole_margin)

    if fin.fold == Fold.HALF:
        mid = (x0 + x1) / 2
        line(mid, y0, mid, y1)
    elif fin.fold == Fold.Z:
        for f in (1 / 3, 2 / 3):
            x = x0 + (x1 - x0) * f
            line(x, y0, x, y1)
    return items
