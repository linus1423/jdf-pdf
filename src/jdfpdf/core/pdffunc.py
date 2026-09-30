"""PDF-Funktionen auswerten (Typ 0, 2, 3, 4), z. B. Tint-Transformationen von Sonderfarben."""

from __future__ import annotations

import math
import re

import pikepdf


class UnsupportedFunction(ValueError):
    pass


def _floats(obj, default=None) -> list[float]:
    if obj is None:
        return list(default or [])
    return [float(v) for v in obj]


def _clip(values: list[float], bounds: list[float]) -> list[float]:
    if not bounds:
        return values
    return [min(max(v, bounds[2 * i]), bounds[2 * i + 1]) for i, v in enumerate(values[: len(bounds) // 2])]


def evaluate(fn, inputs: list[float]) -> list[float]:
    """Funktion ``fn`` (Dictionary/Stream oder Array von Funktionen) für ``inputs`` auswerten."""
    if isinstance(fn, pikepdf.Array):
        return [evaluate(f, inputs)[0] for f in fn]
    kind = int(fn.get("/FunctionType", -1))
    inputs = _clip([float(v) for v in inputs], _floats(fn.get("/Domain")))
    if kind == 2:
        out = _type2(fn, inputs[0])
    elif kind == 3:
        out = _type3(fn, inputs[0])
    elif kind == 4:
        out = _type4(fn, inputs)
    elif kind == 0:
        out = _type0(fn, inputs)
    else:
        raise UnsupportedFunction(f"FunctionType {kind}")
    return _clip(out, _floats(fn.get("/Range")))


def _type2(fn, x: float) -> list[float]:
    c0 = _floats(fn.get("/C0"), [0.0])
    c1 = _floats(fn.get("/C1"), [1.0])
    n = float(fn.get("/N", 1))
    xn = x**n if x > 0 or n == int(n) else 0.0
    return [a + xn * (b - a) for a, b in zip(c0, c1)]


def _type3(fn, x: float) -> list[float]:
    functions = list(fn.Functions)
    bounds = _floats(fn.get("/Bounds"))
    encode = _floats(fn.get("/Encode"))
    d0, d1 = _floats(fn.get("/Domain"), [0, 1])[:2]
    k = 0
    while k < len(bounds) and x >= bounds[k]:
        k += 1
    lo = d0 if k == 0 else bounds[k - 1]
    hi = d1 if k == len(bounds) else bounds[k]
    e0, e1 = encode[2 * k], encode[2 * k + 1]
    t = e0 if hi == lo else e0 + (x - lo) * (e1 - e0) / (hi - lo)
    return evaluate(functions[k], [t])


def _type0(fn, inputs: list[float]) -> list[float]:
    """Abgetastete Funktion; nächster Stützwert (für Farbvorschau genügt das)."""
    size = [int(v) for v in fn.Size]
    bps = int(fn.BitsPerSample)
    domain = _floats(fn.get("/Domain"))
    rng = _floats(fn.get("/Range"))
    n_out = len(rng) // 2
    encode = _floats(fn.get("/Encode")) or [v for s in size for v in (0, s - 1)]
    decode = _floats(fn.get("/Decode")) or rng
    data = fn.read_bytes()
    offset = 0
    stride = 1
    for i, x in enumerate(inputs):
        d0, d1 = domain[2 * i], domain[2 * i + 1]
        e = encode[2 * i] + (x - d0) * (encode[2 * i + 1] - encode[2 * i]) / ((d1 - d0) or 1)
        idx = min(max(int(round(e)), 0), size[i] - 1)
        offset += idx * stride
        stride *= size[i]
    maxval = (1 << bps) - 1
    out = []
    for j in range(n_out):
        bit = (offset * n_out + j) * bps
        if bps >= 8:
            nbytes = bps // 8
            start = bit // 8
            raw = int.from_bytes(data[start:start + nbytes], "big")
        else:
            byte = data[bit // 8]
            raw = (byte >> (8 - bps - bit % 8)) & maxval
        out.append(decode[2 * j] + raw * (decode[2 * j + 1] - decode[2 * j]) / maxval)
    return out


_TOKEN = re.compile(rb"[{}]|[^\s{}]+")


def _parse_ps(data: bytes):
    tokens = _TOKEN.findall(data)
    pos = 0

    def block():
        nonlocal pos
        items = []
        while pos < len(tokens):
            tok = tokens[pos]
            pos += 1
            if tok == b"{":
                items.append(block())
            elif tok == b"}":
                return items
            else:
                try:
                    items.append(float(tok) if b"." in tok or b"e" in tok.lower() else int(tok))
                except ValueError:
                    items.append(tok.decode("ascii"))
        return items

    program = block()
    # äußere Klammer
    return program[0] if len(program) == 1 and isinstance(program[0], list) else program


def _type4(fn, inputs: list[float]) -> list[float]:
    stack: list = list(inputs)
    _run(_parse_ps(fn.read_bytes()), stack)
    n_out = len(_floats(fn.get("/Range"))) // 2
    return [float(v) for v in stack[-n_out:]] if n_out else [float(v) for v in stack]


def _run(program: list, s: list) -> None:
    i = 0
    while i < len(program):
        op = program[i]
        i += 1
        if isinstance(op, (int, float)):
            s.append(op)
        elif isinstance(op, list):
            # Prozedur für if/ifelse
            if i < len(program) and program[i] == "if":
                i += 1
                if s.pop():
                    _run(op, s)
            elif i + 1 < len(program) and isinstance(program[i], list) and program[i + 1] == "ifelse":
                other = program[i]
                i += 2
                _run(op if s.pop() else other, s)
            else:
                raise UnsupportedFunction("Prozedur ohne if/ifelse")
        else:
            _op(op, s)


def _op(op: str, s: list) -> None:
    b = a = None
    if op in _BINARY:
        b, a = s.pop(), s.pop()
        s.append(_BINARY[op](a, b))
    elif op in _UNARY:
        s.append(_UNARY[op](s.pop()))
    elif op == "dup":
        s.append(s[-1])
    elif op == "pop":
        s.pop()
    elif op == "exch":
        s[-1], s[-2] = s[-2], s[-1]
    elif op == "index":
        n = int(s.pop())
        s.append(s[-1 - n])
    elif op == "copy":
        n = int(s.pop())
        s.extend(s[-n:] if n else [])
    elif op == "roll":
        j, n = int(s.pop()), int(s.pop())
        if n:
            part = s[-n:]
            j %= n
            s[-n:] = part[-j:] + part[:-j] if j else part
    elif op == "true":
        s.append(True)
    elif op == "false":
        s.append(False)
    else:
        raise UnsupportedFunction(f"PostScript-Operator {op}")


def _idiv(a, b):
    return int(a / b)


_BINARY = {
    "add": lambda a, b: a + b, "sub": lambda a, b: a - b, "mul": lambda a, b: a * b,
    "div": lambda a, b: a / b, "idiv": _idiv, "mod": lambda a, b: int(math.fmod(a, b)),
    "exp": lambda a, b: a**b, "atan": lambda a, b: math.degrees(math.atan2(a, b)) % 360,
    "eq": lambda a, b: a == b, "ne": lambda a, b: a != b, "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b, "lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
    "and": lambda a, b: a and b if isinstance(a, bool) else int(a) & int(b),
    "or": lambda a, b: a or b if isinstance(a, bool) else int(a) | int(b),
    "xor": lambda a, b: a != b if isinstance(a, bool) else int(a) ^ int(b),
    "bitshift": lambda a, b: int(a) << int(b) if b >= 0 else int(a) >> -int(b),
}
_UNARY = {
    "neg": lambda a: -a, "abs": abs, "ceiling": math.ceil, "floor": math.floor,
    "round": lambda a: math.floor(a + 0.5), "truncate": math.trunc, "sqrt": math.sqrt,
    "sin": lambda a: math.sin(math.radians(a)), "cos": lambda a: math.cos(math.radians(a)),
    "ln": math.log, "log": math.log10, "cvi": int, "cvr": float,
    "not": lambda a: (not a) if isinstance(a, bool) else ~int(a),
}
