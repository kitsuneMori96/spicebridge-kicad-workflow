#!/usr/bin/env python3
"""Build a KiCad 8 schematic (.kicad_sch) from a SPICE netlist.

SPICEBridge's own exporter lays every part out in three fixed columns and
routes nets pin-to-pin, which shorts unrelated nets on anything bigger than
an RC chain.  This module keeps the netlist/simulation side in SPICEBridge but
emits the schematic itself:

  * components are placed on a 1.27 mm grid in a readable signal-flow order
  * every pin position comes from the embedded lib symbol, so wires land
    exactly on pins
  * 2-pin nets get a direct L route, wider nets get a dedicated rail, and
    ground pins get a GND power symbol
  * the result is verified by letting kicad-cli export a netlist and comparing
    the connectivity with the source netlist (see verify_kicad.py)

Usage:
  python3 kicad_build.py netlist.cir out.kicad_sch [--layout layout.json]
"""
from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from spicebridge.kicad_export import (
    _GND_LIB_SYMBOL,
    _LIB_SYMBOL_TEMPLATES,
    _ensure_subckt_symbol,
)
from spicebridge.schematic import parse_netlist

GRID = 1.27


def snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 3)


def uid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------
# Extra lib symbols (op-amp, VCC) in the same style as the SPICE ones
# --------------------------------------------------------------------------

_OPAMP_LIB = """
    (symbol "SPICE:OPAMP" (pin_names (offset 0.762)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "U" (at -2.54 6.35 0) (effects (font (size 1.27 1.27))))
      (property "Value" "OPAMP" (at -2.54 -6.35 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "~" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "OPAMP_0_1"
        (polyline (pts (xy -5.08 5.08) (xy 5.08 0) (xy -5.08 -5.08) (xy -5.08 5.08))
          (stroke (width 0.254) (type default)) (fill (type background))
        )
        (polyline (pts (xy -4.064 2.54) (xy -2.286 2.54))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy -3.175 3.429) (xy -3.175 1.651))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
        (polyline (pts (xy -4.064 -2.54) (xy -2.286 -2.54))
          (stroke (width 0.254) (type default)) (fill (type none))
        )
      )
      (symbol "OPAMP_1_1"
        (pin input line (at -7.62 2.54 0) (length 2.54) (name "+" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin input line (at -7.62 -2.54 0) (length 2.54) (name "-" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))
        (pin output line (at 7.62 0 180) (length 2.54) (name "~" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))
        (pin power_in line (at -2.54 7.62 270) (length 2.54) (name "V+" (effects (font (size 1.27 1.27)))) (number "4" (effects (font (size 1.27 1.27)))))
        (pin power_in line (at -2.54 -7.62 90) (length 2.54) (name "V-" (effects (font (size 1.27 1.27)))) (number "5" (effects (font (size 1.27 1.27)))))
      )
    )"""

_VCC_LIB = """
    (symbol "power:VCC" (power) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "#PWR" (at 0 -3.81 0) (effects (font (size 1.27 1.27)) hide))
      (property "Value" "VCC" (at 0 3.81 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
      (symbol "VCC_0_1"
        (polyline (pts (xy -1.27 1.27) (xy 0 2.54) (xy 1.27 1.27))
          (stroke (width 0) (type default)) (fill (type none))
        )
        (polyline (pts (xy 0 0) (xy 0 2.54))
          (stroke (width 0) (type default)) (fill (type none))
        )
      )
      (symbol "VCC_1_1"
        (pin power_in line (at 0 0 90) (length 0) hide (name "VCC" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
      )
    )"""

LIB = dict(_LIB_SYMBOL_TEMPLATES)
LIB["SPICE:OPAMP"] = _OPAMP_LIB
LIB["power:GND"] = _GND_LIB_SYMBOL
LIB["power:VCC"] = _VCC_LIB


def lib_pins(lib_id: str) -> dict[str, tuple[float, float]]:
    pins: dict[str, tuple[float, float]] = {}
    for m in re.finditer(
        r'\(pin \w+ \w+ \(at (-?[\d.]+) (-?[\d.]+) \d+\) \(length [\d.]+\)'
        r' \(name "[^"]*"[^)]*\)+ \(number "([^"]+)"',
        LIB[lib_id],
    ):
        pins[m.group(3)] = (float(m.group(1)), float(m.group(2)))
    return pins


# SPICE node order -> KiCad pin number
def pin_numbers(comp_type: str, n: int) -> list[str]:
    if comp_type == "Q":
        return ["B", "C", "E"][:n]
    if comp_type == "M":
        return ["G", "D", "S"][:n]
    if comp_type == "D":
        return ["A", "K"][:n]
    return [str(i + 1) for i in range(n)]


def lib_for(comp_type: str, value: str, n: int) -> str:
    if comp_type == "Q":
        return "Device:Q_PNP_BCE" if "pnp" in value.lower() else "Device:Q_NPN_BCE"
    if comp_type == "M":
        return "Device:Q_PMOS_GDS" if "pmos" in value.lower() else "Device:Q_NMOS_GDS"
    if comp_type == "X":
        # numbered box sized to the node count: SPICE subcircuit pin order is
        # model-defined, so a numbered box cannot mislabel pins
        lib_id = _ensure_subckt_symbol(n)
        LIB[lib_id] = _LIB_SYMBOL_TEMPLATES[lib_id]
        return lib_id
    return {
        "R": "Device:R",
        "C": "Device:C",
        "L": "Device:L",
        "D": "Device:D",
        "V": "Simulation_SPICE:VDC",
        "I": "Simulation_SPICE:IDC",
    }.get(comp_type, "Device:R")


@dataclass
class Part:
    ref: str
    comp_type: str
    value: str
    nodes: list[str]
    lib_id: str
    x: float
    y: float
    rot: float = 0.0
    pins: dict[str, tuple[float, float]] = field(default_factory=dict)

    def pin(self, number: str) -> tuple[float, float]:
        return self.pin_pos(number)

    def pin_for(self, node: str) -> tuple[float, float]:
        """Pin position for the terminal attached to `node`."""
        return self.pin_pos(pin_numbers(self.comp_type, len(self.nodes))[self.nodes.index(node)])

    def pin_pos(self, number: str) -> tuple[float, float]:
        dx, dy = self.pins[number]
        # library Y points up, schematic Y points down, then rotate clockwise
        # by the stored angle (KiCad's angle is CW once inside the sheet)
        import math

        t = math.radians(self.rot)
        c, s = math.cos(t), math.sin(t)
        x0, y0 = dx, -dy
        return (snap(self.x + x0 * c + y0 * s), snap(self.y - x0 * s + y0 * c))


class Schematic:
    def __init__(self, title: str = ""):
        self.title = title
        self.parts: dict[str, Part] = {}
        self.wires: list[list[tuple[float, float]]] = []
        self.junctions: list[tuple[float, float]] = []
        self.labels: list[tuple[str, float, float, float]] = []
        self.power: list[tuple[str, float, float, float]] = []  # lib, ref, x, y
        self._pwr = 0

    # -- construction ------------------------------------------------------
    def add(
        self,
        ref: str,
        comp_type: str,
        value: str,
        nodes: list[str],
        x: float,
        y: float,
        rot: float = 0.0,
    ) -> Part:
        lib_id = lib_for(comp_type, value, len(nodes))
        part = Part(ref, comp_type, value, nodes, lib_id, snap(x), snap(y), rot,
                    lib_pins(lib_id))
        self.parts[ref] = part
        return part

    def wire(self, *pts: tuple[float, float]) -> None:
        pts = [(snap(x), snap(y)) for x, y in pts]
        for a, b in zip(pts, pts[1:]):
            if a != b:
                self.wires.append([a, b])

    def connect(self, a, b, mode: str = "auto") -> None:
        """Route between two points (or (x, y) tuples) with at most one bend."""
        (x1, y1), (x2, y2) = a, b
        if x1 == x2 or y1 == y2:
            self.wire((x1, y1), (x2, y2))
            return
        horizontal = mode == "h" or (mode == "auto" and abs(x2 - x1) >= abs(y2 - y1))
        corner = (x2, y1) if horizontal else (x1, y2)
        self.wire((x1, y1), corner, (x2, y2))

    def junction(self, x: float, y: float) -> None:
        p = (snap(x), snap(y))
        if p not in self.junctions:
            self.junctions.append(p)

    def label(self, name: str, x: float, y: float, angle: float = 0) -> None:
        self.labels.append((name, snap(x), snap(y), angle))

    def power_port(self, lib: str, x: float, y: float) -> str:
        self._pwr += 1
        ref = f"#PWR{self._pwr:02d}"
        self.power.append((lib, ref, snap(x), snap(y)))
        return ref

    # -- output ------------------------------------------------------------
    def _split_wires(self) -> list[tuple[tuple[float, float], tuple[float, float]]]:
        """Split every wire at any point where another wire or a pin lands.

        KiCad only joins a wire that *ends* on another wire; a wire passing
        straight through the point is not connected to it, so a T junction
        must be a real corner in the file.
        """
        pts: set[tuple[float, float]] = set()
        for a, b in self.wires:
            pts.add(a)
            pts.add(b)
        for part in self.parts.values():
            for n in part.pins:
                pts.add(part.pin_pos(n))

        out: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for a, b in self.wires:
            (x1, y1), (x2, y2) = a, b
            inner = [
                p for p in pts
                if min(x1, x2) - 0.001 <= p[0] <= max(x1, x2) + 0.001
                and min(y1, y2) - 0.001 <= p[1] <= max(y1, y2) + 0.001
                and (abs(p[0] - x1) < 0.001 and abs(p[1] - y1) < 0.001 or
                     abs(p[0] - x2) < 0.001 and abs(p[1] - y2) < 0.001 or
                     abs((p[0] - x1) * (y2 - y1) - (p[1] - y1) * (x2 - x1)) < 0.01)
            ]
            inner.sort(key=lambda p: (p[0] - x1) ** 2 + (p[1] - y1) ** 2)
            chain = [a] + [p for p in inner if p != a and p != b] + [b]
            for u, v in zip(chain, chain[1:]):
                if u != v:
                    out.append((u, v))
        return out

    def emit(self) -> str:
        used = {p.lib_id for p in self.parts.values()} | {p[0] for p in self.power}
        root = uid()   # every instance path must point at this sheet
        out = [
            "(kicad_sch (version 20231120) (generator eeschema)",
            f'  (uuid "{root}")',
            '  (paper "A4")',
            "  (lib_symbols",
        ]
        for lib_id in sorted(used):
            out.append(LIB[lib_id].strip("\n"))
        out.append("  )")
        out.append("")

        for part in self.parts.values():
            out.append(self._emit_part(part, root))
        for lib, ref, x, y in self.power:
            out.append(self._emit_power(lib, ref, x, y, root))

        for (a, b) in self._split_wires():
            out.append(
                f'  (wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]}))\n'
                f'    (stroke (width 0) (type default))\n    (uuid "{uid()}")\n  )'
            )
        for x, y in self.junctions:
            out.append(
                f'  (junction (at {x} {y}) (diameter 0) (color 0 0 0 0)\n'
                f'    (uuid "{uid()}")\n  )'
            )
        for name, x, y, ang in self.labels:
            out.append(
                f'  (label "{name}" (at {x} {y} {ang}) (fields_autoplaced yes)\n'
                f'    (effects (font (size 1.27 1.27)) (justify left bottom))\n'
                f'    (uuid "{uid()}")\n  )'
            )
        out.append('  (sheet_instances\n    (path "/" (page "1"))\n  )')
        out.append(")")
        return "\n".join(out) + "\n"

    def _emit_part(self, p: Part, su: str) -> str:
        nums = pin_numbers(p.comp_type, len(p.nodes))
        # KiCad rotates field text with the symbol, so a horizontal part
        # (rot 90/270) needs a 90 deg field angle to keep its text level.
        ang = 90 if p.rot in (90, 270) else 0
        if p.rot in (90, 270):          # horizontal part: text above / below
            rx, ry = p.x, p.y - 3.81
            vx, vy = p.x, p.y + 3.81
        elif p.comp_type in ("V", "I"):  # source: text clear of the circle
            rx, ry = p.x - 6.35, p.y - 1.27
            vx, vy = p.x - 6.35, p.y + 1.27
        else:                            # vertical part: text on the right
            rx, ry = p.x + 3.81, p.y - 1.27
            vx, vy = p.x + 3.81, p.y + 1.27
        just = "right" if p.comp_type in ("V", "I") else "left"
        props = [
            f'    (property "Reference" "{p.ref}" (at {rx} {ry} {ang})'
            ' (effects (font (size 1.27 1.27)) (justify ' + just + ')))',
            f'    (property "Value" "{p.value}" (at {vx} {vy} {ang})'
            ' (effects (font (size 1.27 1.27)) (justify ' + just + ')))',
            f'    (property "Footprint" "" (at {p.x} {p.y} 0)'
            ' (effects (font (size 1.27 1.27)) hide))',
            f'    (property "Datasheet" "~" (at {p.x} {p.y} 0)'
            ' (effects (font (size 1.27 1.27)) hide))',
        ]
        lines = [
            f'  (symbol (lib_id "{p.lib_id}") (at {p.x} {p.y} {p.rot})',
            f'    (uuid "{uid()}")',
            *props,
            '    (instances\n      (project ""\n'
            f'        (path "/{su}"\n          (reference "{p.ref}") (unit 1)\n'
            '        )\n      )\n    )',
        ]
        for n in nums:
            lines.append(f'    (pin "{n}" (uuid "{uid()}"))')
        lines.append("  )")
        return "\n".join(lines)

    def _emit_power(self, lib: str, ref: str, x: float, y: float, su: str) -> str:
        off = 3.81 if lib == "power:GND" else -3.81
        return (
            f'  (symbol (lib_id "{lib}") (at {x} {y} 0)\n'
            f'    (uuid "{uid()}")\n'
            f'    (property "Reference" "{ref}" (at {x} {y + off} 0)'
            ' (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Value" "{lib.split(":")[1]}" (at {x} {y + 2 * off} 0)'
            ' (effects (font (size 1.27 1.27))))\n'
            f'    (property "Footprint" "" (at {x} {y} 0)'
            ' (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Datasheet" "" (at {x} {y} 0)'
            ' (effects (font (size 1.27 1.27)) hide))\n'
            '    (instances\n      (project ""\n'
            f'        (path "/{su}"\n          (reference "{ref}") (unit 1)\n'
            '        )\n      )\n    )\n'
            f'    (pin "1" (uuid "{uid()}"))\n  )'
        )


# --------------------------------------------------------------------------
# Placement
# --------------------------------------------------------------------------

def auto_layout(parts) -> None:
    """Sources on the left, then series parts across, shunt parts below."""
    sources = [p for p in parts if p.comp_type in ("V", "I")]
    shunt = [p for p in parts if p.comp_type not in ("V", "I")
             and any(n in ("0", "gnd", "GND") for n in p.nodes)]
    series = [p for p in parts if p not in sources and p not in shunt]

    for i, p in enumerate(sources):
        p.x, p.y, p.rot = 50.8, 50.8 + i * 25.4, 0
    x = 76.2
    for p in series:
        if len(p.nodes) > 2:
            p.x, p.y, p.rot = x, 50.8, 0
            x += 38.1
        else:
            p.x, p.y, p.rot = x, 50.8, 270
            x += 25.4
    for i, p in enumerate(shunt):
        p.x, p.y, p.rot = 76.2 + i * 25.4, 76.2, 0


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

def _foreign_pins(sch: Schematic, net_pins, net_nodes) -> list[tuple[float, float]]:
    out = []
    for p in sch.parts.values():
        nums = pin_numbers(p.comp_type, len(p.nodes))
        for n, node in zip(nums, p.nodes):
            if node not in net_nodes:
                out.append(p.pin_pos(n))
    return out


def _vertical_hits_pin(x, y0, y1, pins) -> bool:
    lo, hi = min(y0, y1), max(y0, y1)
    return any(abs(px - x) < 0.01 and lo + 0.01 < py < hi - 0.01 for px, py in pins)


def _horizontal_hits_pin(y, x0, x1, pins) -> bool:
    lo, hi = min(x0, x1), max(x0, x1)
    return any(abs(py - y) < 0.01 and lo + 0.01 < px < hi - 0.01 for px, py in pins)


def route(sch: Schematic) -> None:
    nets: dict[str, list[tuple[Part, str]]] = {}
    for p in sch.parts.values():
        nums = pin_numbers(p.comp_type, len(p.nodes))
        for n, node in zip(nums, p.nodes):
            if node.lower() in ("0", "gnd"):
                sch.power_port("power:GND", *p.pin_pos(n))
            else:
                nets.setdefault(node, []).append((p, n))

    # a dedicated rail per net keeps multi-drop nets short and unambiguous
    rail_y = 100.0
    for name in sorted(nets, key=lambda k: len(nets[k])):
        entries = nets[name]
        if len(entries) < 2:
            continue
        pts = [p.pin_pos(n) for p, n in entries]
        foreign = _foreign_pins(sch, pts, {name})
        sch.label(name, *pts[0])

        if len(pts) == 2:
            (x1, y1), (x2, y2) = pts
            if x1 == x2 or y1 == y2:
                sch.wire((x1, y1), (x2, y2))
                continue
            candidates = [
                [(x1, y1), (x2, y1), (x2, y2)],          # horizontal first
                [(x1, y1), (x1, y2), (x2, y2)],          # vertical first
            ]
            step = 2.54 if y2 > y1 else -2.54
            for k in (1, 2, 3):
                ym = y1 + step * k
                candidates.append([(x1, y1), (x1, ym), (x2, ym), (x2, y2)])
            for path in candidates:
                if all(
                    not _vertical_hits_pin(a[0], a[1], b[1], foreign)
                    and not _horizontal_hits_pin(a[1], a[0], b[0], foreign)
                    for a, b in zip(path, path[1:])
                ):
                    sch.wire(*path)
                    break
            else:
                sch.wire(*candidates[0])
            continue

        # rail: horizontal trunk below the parts, one 2.54 mm stub per pin
        y = rail_y
        while any(abs(y - py) < 0.01 for _, py in pts) or \
                _horizontal_hits_pin(y, min(p[0] for p in pts), max(p[0] for p in pts),
                                     foreign):
            y += 2.54
        rail_y = y + 5.08
        stub_xs = []
        for (px, py) in pts:
            sx = px
            if _vertical_hits_pin(sx, py, y, foreign):
                sx = px + 2.54
                sch.wire((px, py), (sx, py))
            sch.wire((sx, py), (sx, y))
            sch.junction(sx, y)
            stub_xs.append(sx)
        sch.wire((min(stub_xs), y), (max(stub_xs), y))


def build(netlist: str, title: str = "") -> Schematic:
    comps = parse_netlist(netlist)
    sch = Schematic(title)
    parts = [sch.add(c.ref, c.comp_type, c.value, list(c.nodes), 0, 0) for c in comps]
    auto_layout(parts)
    route(sch)
    return sch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("netlist")
    ap.add_argument("out")
    ap.add_argument("--title", default="")
    args = ap.parse_args()
    sch = build(Path(args.netlist).read_text(), args.title or Path(args.netlist).stem)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sch.emit())
    print(f"wrote {out} ({len(sch.parts)} parts, {len(sch.wires)} wires)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
