#!/usr/bin/env python3
"""Hand-laid KiCad schematic: single-stage common-emitter amplifier."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root
from kicad_build import Schematic

sch = Schematic("CE amplifier")
RAIL_Y = 35.56
BASE_Y = 66.04
GND_Y = 88.90

Q1 = sch.add("Q1", "Q", "Q2N2222", ["c", "b", "e"], 127.00, 66.04, 0)

R1 = sch.add("R1", "R", "1.5k", ["vcc", "c"], 129.54, 48.26, 0)
R2 = sch.add("R2", "R", "100k", ["vcc", "b"], 101.60, 48.26, 0)
R3 = sch.add("R3", "R", "18k", ["b", "0"], 109.22, 76.20, 0)
R4 = sch.add("R4", "R", "220", ["e", "0"], 129.54, 76.20, 0)

C1 = sch.add("C1", "C", "10u", ["in", "b"], 88.90, 66.04, 90)
C2 = sch.add("C2", "C", "10u", ["c", "out"], 146.05, 63.50, 90)

V1 = sch.add("V1", "V", "AC 1", ["in", "0"], 78.74, 71.12, 0)
R5 = sch.add("R5", "R", "10k", ["out", "0"], 158.75, 71.12, 0)

# --- supply rail ----------------------------------------------------------
sch.wire((96.52, RAIL_Y), (129.54, RAIL_Y))
sch.power_port("power:VCC", 96.52, RAIL_Y)
sch.junction(101.60, RAIL_Y)

# --- collector ------------------------------------------------------------
sch.wire((129.54, RAIL_Y), R1.pin("1"))
sch.wire(R1.pin("2"), Q1.pin("C"))
sch.wire(Q1.pin("C"), C2.pin_for("c"))

# --- base bias ------------------------------------------------------------
sch.wire((101.60, RAIL_Y), R2.pin("1"))
sch.wire(R2.pin("2"), (101.60, BASE_Y))
sch.wire((91.44, BASE_Y), (124.46, BASE_Y))      # base rail
sch.wire((109.22, BASE_Y), R3.pin("1"))
sch.junction(101.60, BASE_Y)
sch.junction(109.22, BASE_Y)

sch.wire(R3.pin("2"), (109.22, GND_Y))
sch.power_port("power:GND", 109.22, GND_Y)

# --- emitter --------------------------------------------------------------
sch.wire(Q1.pin("E"), R4.pin("1"))
sch.wire(R4.pin("2"), (129.54, GND_Y))
sch.power_port("power:GND", 129.54, GND_Y)

# --- output ---------------------------------------------------------------
sch.wire(C2.pin_for("out"), (158.75, 63.50))
sch.wire((158.75, 63.50), R5.pin("1"))
sch.wire(R5.pin("2"), (158.75, 74.93))
sch.power_port("power:GND", 158.75, 74.93)

# --- input ----------------------------------------------------------------
sch.wire(C1.pin_for("in"), (78.74, BASE_Y))
sch.wire((78.74, BASE_Y), V1.pin("1"))
sch.wire(V1.pin("2"), (78.74, 74.93))
sch.power_port("power:GND", 78.74, 74.93)

# --- net labels -----------------------------------------------------------
sch.label("in", 82.55, BASE_Y)
sch.label("b", 116.84, BASE_Y)
sch.label("c", 133.35, 63.50)
sch.label("out", 152.40, 63.50)

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/bjt_hand.kicad_sch"
    open(out, "w").write(sch.emit())
    print(f"wrote {out}: {len(sch.parts)} parts, {len(sch.wires)} wires")
