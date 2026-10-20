#!/usr/bin/env python3
"""Structural validator for KiCad 8 .kicad_sch files.

Checks that a schematic will actually be usable when opened in KiCad:
  * top-level s-expression is well formed and has the right version
  * every placed symbol references an embedded lib_symbols entry
  * every placed pin has a uuid (KiCad regenerates these otherwise)
  * pin endpoints (after rotation/mirror) are attached to a wire end,
    a junction, a label, or another pin
  * dangling wires are reported

Usage: python3 validate_kicad.py FILE.kicad_sch [--strict]
Exit code 1 when errors are found.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict

import sexpdata

Quoted = sexpdata.Quoted


def q(value):
    return Sexpr(value.value()) if isinstance(value, sexpdata.Quoted) else value


class Sexpr(list):
    """list subclass that also records the head symbol as .head."""

    @property
    def head(self):
        return self[0] if self else None


def normalize(node):
    """sexpdata yields Symbol for bare atoms; turn every atom into a plain str."""
    if isinstance(node, list):
        return [normalize(c) for c in node]
    if isinstance(node, sexpdata.Quoted):
        return node.value()
    if isinstance(node, sexpdata.Symbol):
        return str(node)
    return node


def parse(path):
    with open(path, encoding="utf-8") as fh:
        return normalize(sexpdata.loads(fh.read()))


def head(node):
    if isinstance(node, list) and node and not isinstance(node[0], list):
        return node[0]
    return None


def children(node, name):
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def child(node, name):
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def val(node, name, idx=1, default=None):
    c = child(node, name)
    if c is None or len(c) <= idx:
        return default
    return c[idx]


def num(node, name, idx=1, default=0.0):
    v = val(node, name, idx)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def rotate(x, y, deg):
    """KiCad schematic Y axis points down; symbol rotation is CCW in that space."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return x * c + y * s, -x * s + y * c


def pin_positions(lib_symbol):
    """Absolute pin offsets (relative to symbol origin) in schematic coordinates."""
    pins = []
    # pins live inside nested unit symbols, e.g. (symbol "R_0_1" ... (pin ...))
    stack = [lib_symbol]
    while stack:
        node = stack.pop()
        for c in node[1:]:
            if not isinstance(c, list) or not c:
                continue
            if c[0] == "symbol":
                stack.append(c)
            elif c[0] == "pin":
                at = child(c, "at")
                if at is None:
                    continue
                px, py = float(at[1]), float(at[2])
                ang = float(at[3]) if len(at) > 3 else 0.0
                number = val(c, "number", 1, "?")
                name = val(c, "name", 1, "")
                pins.append((str(number), str(name), px, py, ang))
    return pins


def main() -> int:
    path = sys.argv[1]
    strict = "--strict" in sys.argv
    root = parse(path)

    errors: list[str] = []
    warnings: list[str] = []

    if head(root) != "kicad_sch":
        errors.append(f"root node is {head(root)!r}, expected 'kicad_sch'")
        print("\n".join(errors))
        return 1

    version = int(val(root, "version", 1, 0))
    if version < 20230121:
        errors.append(f"version {version} is older than KiCad 7/8 format")

    libs = {}
    lib_block = child(root, "lib_symbols")
    if lib_block is None:
        errors.append("no (lib_symbols ...) block: symbols will be unresolved")
    else:
        for sym in children(lib_block, "symbol"):
            libs[sym[1]] = sym

    # wire endpoints
    wire_ends: dict[tuple[float, float], int] = defaultdict(int)
    wires = []
    for w in children(root, "wire"):
        pts = child(w, "pts")
        xy = children(pts, "xy") if pts else []
        if len(xy) != 2:
            errors.append("wire without exactly two points")
            continue
        a = (round(float(xy[0][1]), 3), round(float(xy[0][2]), 3))
        b = (round(float(xy[1][1]), 3), round(float(xy[1][2]), 3))
        wires.append((a, b))
        wire_ends[a] += 1
        wire_ends[b] += 1

    anchors = set(wire_ends)
    for name in ("junction", "no_connect", "label", "global_label", "hierarchical_label"):
        for node in children(root, name):
            at = child(node, "at")
            if at:
                anchors.add((round(float(at[1]), 3), round(float(at[2]), 3)))

    pins_at: dict[tuple[float, float], list[str]] = defaultdict(list)
    placed = children(root, "symbol")
    if not placed:
        errors.append("no placed symbols")

    for sym in placed:
        lib_id = val(sym, "lib_id", 1)
        ref = "?"
        for prop in children(sym, "property"):
            if prop[1] == "Reference":
                ref = prop[2]
        if lib_id not in libs:
            errors.append(f"{ref}: lib_id {lib_id!r} missing from lib_symbols")
            continue

        at = child(sym, "at")
        ox, oy = float(at[1]), float(at[2])
        rot = float(at[3]) if len(at) > 3 else 0.0
        mirror = val(sym, "mirror", 1, None)

        if child(sym, "uuid") is None:
            errors.append(f"{ref}: missing symbol uuid")
        pin_uuid_count = len(children(sym, "pin"))
        lib_pins = pin_positions(libs[lib_id])
        if pin_uuid_count < len(lib_pins):
            warnings.append(
                f"{ref}: {len(lib_pins)} lib pins but {pin_uuid_count} (pin ...) entries"
            )

        for number, name, px, py, _ang in lib_pins:
            if mirror == "x":
                py = -py
            elif mirror == "y":
                px = -px
            rx, ry = rotate(px, py, rot)
            pos = (round(ox + rx, 3), round(oy + ry, 3))
            pins_at[pos].append(f"{ref}.{number}")

    dangling = []
    for pos, names in pins_at.items():
        if pos in anchors or len(names) > 1:
            continue
        dangling.append(f"{','.join(names)} at {pos}")
    for d in dangling:
        (errors if strict else warnings).append(f"unconnected pin: {d}")

    loose = [a for a in wire_ends if wire_ends[a] == 1 and a not in pins_at]
    for a in loose[:10]:
        warnings.append(f"dangling wire end at {a}")

    refs = []
    for sym in placed:
        for prop in children(sym, "property"):
            if prop[1] == "Reference":
                refs.append(prop[2])

    print(f"file      : {path}")
    print(f"version   : {version}  (KiCad 8 = 20231120)")
    print(f"symbols   : {len(placed)} placed, {len(libs)} lib definitions")
    print(f"refs      : {', '.join(refs)}")
    print(f"wires     : {len(wires)}  junctions: {len(children(root, 'junction'))}")
    print(f"pins      : {sum(len(v) for v in pins_at.values())}")
    if warnings:
        print("\nWARNINGS")
        for w in warnings:
            print(f"  - {w}")
    if errors:
        print("\nERRORS")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nOK: structurally valid KiCad 8 schematic")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
