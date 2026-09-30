"""Seitenbereiche als Text: ``"1-3, 7, 10-"`` ↔ 0-basierte Indizes."""

from __future__ import annotations


def parse_pages(text: str, page_count: int) -> list[int]:
    """Bereichsangabe (1-basiert, ``-`` offen am Ende) in sortierte 0-basierte Indizes."""
    result: set[int] = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        first, dash, last = part.partition("-")
        try:
            a = int(first) if first.strip() else 1
            b = (int(last) if last.strip() else page_count) if dash else a
        except ValueError as exc:
            raise ValueError(f"Ungültiger Seitenbereich: {part!r}") from exc
        if a > b:
            a, b = b, a
        result.update(i - 1 for i in range(max(a, 1), min(b, page_count) + 1))
    return sorted(result)


def format_pages(indices: list[int]) -> str:
    """0-basierte Indizes kompakt als 1-basierte Bereiche, z. B. ``"1-3, 7"``."""
    runs: list[list[int]] = []
    for i in sorted(set(indices)):
        if runs and runs[-1][1] == i - 1:
            runs[-1][1] = i
        else:
            runs.append([i, i])
    return ", ".join(f"{a + 1}" if a == b else f"{a + 1}-{b + 1}" for a, b in runs)
