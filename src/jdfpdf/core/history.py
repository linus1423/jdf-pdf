"""Rückgängig/Wiederholen über Schnappschüsse des Dokuments."""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class History(Generic[T]):
    """Speichert Zustände (z. B. PDF-Bytes plus Einstellungen); begrenzt auf ``limit`` Schritte."""

    def __init__(self, limit: int = 50) -> None:
        self.limit = limit
        self._undo: list[T] = []
        self._redo: list[T] = []

    def push(self, state: T) -> None:
        """Zustand *vor* einer Änderung merken."""
        self._undo.append(state)
        del self._undo[: -self.limit]
        self._redo.clear()

    def undo(self, current: T) -> T | None:
        if not self._undo:
            return None
        self._redo.append(current)
        return self._undo.pop()

    def redo(self, current: T) -> T | None:
        if not self._redo:
            return None
        self._undo.append(current)
        return self._redo.pop()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
