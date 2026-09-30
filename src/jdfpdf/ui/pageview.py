"""Große Seitenansicht mit Zoom, Seitenboxen, Hilfslinien und Messwerkzeug."""

from __future__ import annotations

import math

from PySide6.QtCore import QLineF, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

MM = 72 / 25.4

BOX_COLORS = {
    "MediaBox": "#808080",
    "CropBox": "#000000",
    "BleedBox": "#d02090",
    "TrimBox": "#1a7f37",
    "ArtBox": "#0969da",
}


class Guide(QGraphicsLineItem):
    """Verschiebbare Hilfslinie (horizontal oder vertikal)."""

    def __init__(self, vertical: bool, pos: float, extent: float) -> None:
        super().__init__()
        self.vertical = vertical
        if vertical:
            self.setLine(0, -extent, 0, 2 * extent)
            self.setPos(pos, 0)
        else:
            self.setLine(-extent, 0, 2 * extent, 0)
            self.setPos(0, pos)
        pen = QPen(QColor("#00a0e0"), 0)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.SplitHCursor if vertical else Qt.CursorShape.SplitVCursor)
        self.setZValue(10)

    def itemChange(self, change, value):
        # nur senkrecht zur Linie verschieben
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(value.x(), 0) if self.vertical else QPointF(0, value.y())
        return super().itemChange(change, value)


class PageView(QGraphicsView):
    """Szenenkoordinaten = PDF-Punkte, Ursprung oben links der MediaBox."""

    measured = Signal(float, float, float)  # dx, dy, Länge in mm
    hovered = Signal(float, float)  # Position in mm (PDF-Koordinaten, Ursprung unten links)

    def __init__(self) -> None:
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#9a9a9a"))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setMouseTracking(True)
        self.measure_mode = False
        self.show_boxes = True
        self._page_item: QGraphicsPixmapItem | None = None
        self._box_items: list[QGraphicsItem] = []
        self._overlay_items: list[QGraphicsItem] = []
        self.guides: list[Guide] = []
        self._measure_start: QPointF | None = None
        self._measure_line: QGraphicsLineItem | None = None
        self._measure_text: QGraphicsSimpleTextItem | None = None
        self._height = 0.0
        self._origin = (0.0, 0.0)

    def set_page(self, pixmap: QPixmap, media_box: tuple[float, float, float, float],
                 boxes: dict[str, tuple[float, float, float, float]]) -> None:
        """Seite anzeigen; ``pixmap`` deckt die MediaBox ab."""
        scene = self.scene()
        for item in [self._page_item, *self._box_items, *self._overlay_items]:
            if item is not None:
                scene.removeItem(item)
        self._box_items, self._overlay_items = [], []
        x0, y0, x1, y1 = media_box
        width, height = x1 - x0, y1 - y0
        self._height, self._origin = height, (x0, y0)
        self._page_item = scene.addPixmap(pixmap)
        self._page_item.setScale(width / max(pixmap.width(), 1))
        self._page_item.setZValue(0)
        if self.show_boxes:
            for name, (bx0, by0, bx1, by1) in boxes.items():
                if name == "MediaBox":
                    continue
                rect = QGraphicsRectItem(bx0 - x0, height - (by1 - y0), bx1 - bx0, by1 - by0)
                pen = QPen(QColor(BOX_COLORS.get(name, "#ff0000")), 0, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                rect.setPen(pen)
                rect.setZValue(5)
                rect.setToolTip(name)
                scene.addItem(rect)
                self._box_items.append(rect)
        margin = max(width, height) * 0.05
        scene.setSceneRect(-margin, -margin, width + 2 * margin, height + 2 * margin)

    def add_overlay(self, item: QGraphicsItem) -> None:
        """Zusätzliche Zeichnung (z. B. Heftklammern) in Seitenkoordinaten."""
        item.setZValue(6)
        self.scene().addItem(item)
        self._overlay_items.append(item)

    def to_scene(self, x_pt: float, y_pt: float) -> QPointF:
        """PDF-Koordinate (Ursprung unten links) in Szenenkoordinate umrechnen."""
        return QPointF(x_pt - self._origin[0], self._height - (y_pt - self._origin[1]))

    def fit(self) -> None:
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor: float) -> None:
        self.scale(factor, factor)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
        else:
            super().wheelEvent(event)

    # --- Hilfslinien --------------------------------------------------------

    def add_guide(self, vertical: bool, pos_pt: float) -> Guide:
        extent = max(self.scene().sceneRect().width(), self.scene().sceneRect().height())
        guide = Guide(vertical, pos_pt, extent)
        self.scene().addItem(guide)
        self.guides.append(guide)
        return guide

    def clear_guides(self) -> None:
        for guide in self.guides:
            self.scene().removeItem(guide)
        self.guides.clear()

    def mouseDoubleClickEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        vertical = not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self.add_guide(vertical, pos.x() if vertical else pos.y())

    # --- Messen ---------------------------------------------------------------

    def set_measure_mode(self, enabled: bool) -> None:
        self.measure_mode = enabled
        self.setDragMode(QGraphicsView.DragMode.NoDrag if enabled else QGraphicsView.DragMode.ScrollHandDrag)
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event) -> None:
        if self.measure_mode and event.button() == Qt.MouseButton.LeftButton:
            self._measure_start = self.mapToScene(event.position().toPoint())
            for item in (self._measure_line, self._measure_text):
                if item is not None:
                    self.scene().removeItem(item)
            pen = QPen(QColor("#e00000"), 0)
            pen.setCosmetic(True)
            self._measure_line = self.scene().addLine(QLineF(self._measure_start, self._measure_start), pen)
            self._measure_line.setZValue(20)
            self._measure_text = self.scene().addSimpleText("")
            self._measure_text.setBrush(QColor("#e00000"))
            self._measure_text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            self._measure_text.setZValue(20)
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        self.hovered.emit((pos.x()) / MM, (self._height - pos.y()) / MM)
        if self.measure_mode and self._measure_start is not None and self._measure_line is not None:
            self._measure_line.setLine(QLineF(self._measure_start, pos))
            dx = (pos.x() - self._measure_start.x()) / MM
            dy = (self._measure_start.y() - pos.y()) / MM
            length = math.hypot(dx, dy)
            self._measure_text.setText(f"{length:.1f} mm")
            self._measure_text.setPos(pos)
            self.measured.emit(dx, dy, length)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.measure_mode:
            self._measure_start = None
            return
        super().mouseReleaseEvent(event)
