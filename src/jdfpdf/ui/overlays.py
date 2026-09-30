"""WYSIWYG-Einblendungen der Weiterverarbeitung (Heftklammern, Lochung, Falzlinien)."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsEllipseItem, QGraphicsItem, QGraphicsLineItem, QGraphicsRectItem

from ..core.finishing_shapes import HOLE_RADIUS, STAPLE_LENGTH, finishing_shapes
from ..core.jdf import Finishing

COLOR = QColor(200, 30, 30, 200)


def _pen(dashed: bool = False) -> QPen:
    pen = QPen(COLOR, 0)
    pen.setCosmetic(True)
    if dashed:
        pen.setDashPattern([6, 4])
    return pen


def finishing_items(view, trim: tuple[float, float, float, float], fin: Finishing, index: int) -> list[QGraphicsItem]:
    """Grafikelemente in Seitenkoordinaten der Ansicht (``view.to_scene``)."""
    items: list[QGraphicsItem] = []
    for shape in finishing_shapes(trim, fin, index):
        p = view.to_scene(shape.x, shape.y)
        if shape.kind == "staple":
            length = STAPLE_LENGTH
            if shape.horizontal:
                rect = QRectF(p.x() - length / 2, p.y() - 1.2, length, 2.4)
            else:
                rect = QRectF(p.x() - 1.2, p.y() - length / 2, 2.4, length)
            item = QGraphicsRectItem(rect)
            item.setBrush(QBrush(COLOR))
            item.setPen(_pen())
            if shape.angle:
                item.setTransformOriginPoint(p)
                item.setRotation(shape.angle)
        elif shape.kind == "hole":
            r = HOLE_RADIUS
            item = QGraphicsEllipseItem(p.x() - r, p.y() - r, 2 * r, 2 * r)
            item.setPen(_pen())
        else:
            b = view.to_scene(shape.x2, shape.y2)
            item = QGraphicsLineItem(p.x(), p.y(), b.x(), b.y())
            item.setPen(_pen(dashed=True))
        items.append(item)
    return items
