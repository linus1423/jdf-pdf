"""Farben aller Seiteninhalte auf Grauwerte abbilden.

Grundlage für die Graustufenumwandlung (``color``) und für die Auszüge der
PPF-Vorschau (``ppf``): Eine Abbildung ``value_fn(farbraum, werte) -> grau``
(0 = schwarz, 1 = weiß) wird auf Farboperatoren, Verläufe, Muster, Formulare
und Bilder angewendet. Geteilte Ressourcen werden kopiert, Originale bleiben für
andere Seiten erhalten. Masken (ExtGState/SMask) werden bewusst nicht verändert.
"""

from __future__ import annotations

from collections import Counter
from typing import Callable

import pikepdf
from pikepdf import Name, Operator

from .colorspace import family, initial_values, resolve
from .pdffunc import UnsupportedFunction, evaluate
from .pdfimage import stream_like

ValueFn = Callable[[object, list], "float | None"]
ImageFn = Callable[[pikepdf.Stream], "pikepdf.Stream | None"]


class _State:
    def __init__(self) -> None:
        self.stack: list[tuple] = []
        self.fill = None  # ursprünglicher Farbraum, falls umgesetzt; None = unverändert
        self.stroke = None

    def push(self) -> None:
        self.stack.append((self.fill, self.stroke))

    def pop(self) -> None:
        if self.stack:
            self.fill, self.stroke = self.stack.pop()

    def set(self, fill: bool, space) -> None:
        if fill:
            self.fill = space
        else:
            self.stroke = space


def _num(value: float) -> float:
    return round(min(max(value, 0.0), 1.0), 4)


class Remapper:
    """Wendet ``value_fn``/``image_fn`` auf Seiten an.

    ``image_fn`` liefert ein neues Bild-XObject oder ``None`` (unverändert lassen,
    wird als übersprungen gezählt). ``reset_default`` setzt am Seitenanfang die
    Standardfarbe (DeviceGray schwarz) über ``value_fn`` – nötig für Auszüge, bei
    denen Schwarz nicht schwarz bleibt.
    """

    def __init__(self, pdf: pikepdf.Pdf, value_fn: ValueFn, image_fn: ImageFn, reset_default: bool = False) -> None:
        self.pdf = pdf
        self.value_fn = value_fn
        self.image_fn = image_fn
        self.reset_default = reset_default
        self.memo: dict = {}
        self.skipped: Counter = Counter()
        self.images = 0
        self.pages = 0

    def _gray(self, space, values) -> float | None:
        try:
            return self.value_fn(space, values)
        except (IndexError, ValueError, TypeError, KeyError, ZeroDivisionError):
            return None

    # --- Inhaltsströme -----------------------------------------------------------

    def ops(self, stream, resources, state: _State) -> list:
        out = []
        for item in pikepdf.parse_content_stream(stream):
            if isinstance(item, pikepdf.ContentStreamInlineImage):
                self.skipped["inline_image"] += 1
                out.append(item)
                continue
            operands, op = list(item.operands), str(item.operator)
            if op == "q":
                state.push()
            elif op == "Q":
                state.pop()
            elif op in ("g", "G", "rg", "RG", "k", "K"):
                space = {"g": Name.DeviceGray, "r": Name.DeviceRGB, "k": Name.DeviceCMYK}[op[0].lower()]
                gray = self._numbers(space, operands)
                state.set(op.islower(), None)
                if gray is not None:
                    out.append(([_num(gray)], Operator("g" if op.islower() else "G")))
                    continue
            elif op in ("cs", "CS") and operands:
                space = resolve(resources, operands[0])
                fill = op == "cs"
                if space is not None and family(space) != "Pattern":
                    start = self._gray(space, initial_values(space))
                    if start is not None:
                        state.set(fill, space)
                        out.append(([Name.DeviceGray], Operator(op)))
                        out.append(([_num(start)], Operator("sc" if fill else "SC")))
                        continue
                state.set(fill, None)
                if space is not None and isinstance(space, pikepdf.Array) and len(space) > 1:
                    self.skipped["pattern"] += 1  # ungefärbtes Muster mit Basisfarbraum
            elif op in ("sc", "scn", "SC", "SCN"):
                space = state.fill if op.islower() else state.stroke
                if space is not None:
                    gray = self._numbers(space, operands)
                    if gray is not None:
                        out.append(([_num(gray)], Operator("sc" if op.islower() else "SC")))
                        continue
            out.append(item)
        return out

    def _numbers(self, space, operands) -> float | None:
        try:
            values = [float(v) for v in operands]
        except (TypeError, ValueError):
            return None
        return self._gray(space, values) if values else None

    def new_content(self, stream, resources, state: _State, prefix: bytes = b"") -> pikepdf.Stream:
        data = pikepdf.unparse_content_stream(self.ops(stream, resources, state))
        return stream_like(self.pdf, stream, prefix + data)

    # --- Ressourcen ----------------------------------------------------------------

    def resources(self, resources) -> pikepdf.Dictionary:
        new = pikepdf.Dictionary(resources) if resources is not None else pikepdf.Dictionary()
        for key, convert in (("/XObject", self.xobject), ("/Shading", self.shading), ("/Pattern", self.pattern)):
            entries = new.get(key)
            if entries is not None:
                converted = pikepdf.Dictionary()
                for name, obj in entries.items():
                    converted[name] = self._memo(obj, lambda o=obj: convert(o, resources))
                new[key] = converted
        return new

    def _memo(self, obj, make):
        key = obj.objgen if obj.is_indirect else None
        if key is not None and key in self.memo:
            return self.memo[key]
        result = make()
        if key is not None:
            self.memo[key] = result
        return result

    def xobject(self, obj, parent_resources):
        subtype = obj.get("/Subtype")
        if subtype == Name.Image:
            if obj.get("/ImageMask", False):
                return obj
            new = self.image_fn(obj)
            if new is None:
                return obj
            if new is not obj:
                self.images += 1
                return self.pdf.make_indirect(new)
            return obj
        if subtype == Name.Form:
            return self.form(obj, parent_resources)
        return obj

    def form(self, obj, parent_resources):
        own = obj.get("/Resources")
        resources = own if own is not None else parent_resources
        new = self.pdf.make_indirect(self.new_content(obj, resources, _State()))
        if own is not None:
            new.Resources = self.resources(own)
        _gray_group(new)
        return new

    def pattern(self, obj, parent_resources):
        kind = int(obj.get("/PatternType", 0))
        if kind == 2 and "/Shading" in obj:
            new = pikepdf.Dictionary(obj)
            new.Shading = self._memo(obj.Shading, lambda: self.shading(obj.Shading, parent_resources))
            return self.pdf.make_indirect(new)
        if kind == 1 and int(obj.get("/PaintType", 1)) == 1:
            return self.form(obj, parent_resources)
        return obj

    def shading(self, obj, parent_resources=None):
        kind = int(obj.get("/ShadingType", 0))
        space = obj.get("/ColorSpace")
        if kind not in (1, 2, 3) or "/Function" not in obj or space is None:
            self.skipped["shading"] += 1
            return obj
        function = obj.Function
        try:
            if kind == 1:
                domain = [float(v) for v in obj.get("/Domain", [0, 1, 0, 1])]
                size = [33, 33]
                samples = []
                for j in range(size[1]):
                    y = domain[2] + (domain[3] - domain[2]) * j / (size[1] - 1)
                    for i in range(size[0]):
                        x = domain[0] + (domain[1] - domain[0]) * i / (size[0] - 1)
                        samples.append(self._gray(space, evaluate(function, [x, y])))
            else:
                domain = [float(v) for v in obj.get("/Domain", [0, 1])]
                size = [256]
                samples = [self._gray(space, evaluate(function, [domain[0] + (domain[1] - domain[0]) * i / 255]))
                           for i in range(256)]
        except (UnsupportedFunction, IndexError, KeyError, ValueError, ZeroDivisionError, TypeError):
            self.skipped["shading"] += 1
            return obj
        if any(s is None for s in samples):
            self.skipped["shading"] += 1
            return obj
        sampled = self.pdf.make_stream(bytes(int(round(_num(s) * 255)) for s in samples))
        sampled.FunctionType = 0
        sampled.Domain = pikepdf.Array(domain)
        sampled.Range = pikepdf.Array([0, 1])
        sampled.Size = pikepdf.Array(size)
        sampled.BitsPerSample = 8
        new = pikepdf.Dictionary({k: v for k, v in obj.items() if k not in ("/Function", "/ColorSpace", "/Background")})
        new.ColorSpace = Name.DeviceGray
        new.Function = sampled
        if "/Background" in obj:
            background = self._numbers(space, list(obj.Background))
            if background is not None:
                new.Background = pikepdf.Array([_num(background)])
        return self.pdf.make_indirect(new)

    # --- Seiten --------------------------------------------------------------------

    def page(self, page: pikepdf.Page) -> None:
        obj = page.obj
        resources = obj.get("/Resources")
        contents = obj.get("/Contents")
        state = _State()
        prefix = b""
        if self.reset_default:
            black = self._gray(Name.DeviceGray, [0.0])
            prefix = f"{_num(black)} g {_num(black)} G\n".encode()
        if isinstance(contents, pikepdf.Array):
            streams = [self.new_content(s, resources, state, prefix if i == 0 else b"")
                       for i, s in enumerate(contents)]
            obj.Contents = pikepdf.Array(streams)
        elif contents is not None:
            obj.Contents = self.new_content(contents, resources, state, prefix)
        obj.Resources = self.resources(resources)
        _gray_group(obj)
        self.pages += 1


def _gray_group(obj) -> None:
    group = obj.get("/Group")
    if group is not None and "/CS" in group:
        group = pikepdf.Dictionary(group)
        group.CS = Name.DeviceGray
        obj.Group = group
