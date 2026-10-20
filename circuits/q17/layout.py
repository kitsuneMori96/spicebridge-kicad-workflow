#!/usr/bin/env python3
"""教材第 17 题：分压/偏置共射放大器（发射极直接接地）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kicad_build import Schematic, layout_main

sch = Schematic("CE amp (exam 17)")
RAIL_Y, BASE_Y = 35.56, 66.04

Q1 = sch.add("Q1", "Q", "Q2N2222", ["c", "b", "0"], 127.00, 66.04, 0)

R1 = sch.add("R1", "R", "565k", ["vcc", "b"], 101.60, 48.26, 0)   # 基极偏置
R2 = sch.add("R2", "R", "3k", ["vcc", "c"], 129.54, 48.26, 0)     # 集电极电阻 Rc
R3 = sch.add("R3", "R", "3k", ["out", "0"], 158.75, 71.12, 0)     # 负载 RL

C1 = sch.add("C1", "C", "10u", ["in", "b"], 88.90, 66.04, 90)
C2 = sch.add("C2", "C", "10u", ["c", "out"], 146.05, 63.50, 90)

V1 = sch.add("V1", "V", "SIN 30m", ["in", "0"], 78.74, 71.12, 0)

# 电源轨
sch.wire((96.52, RAIL_Y), (129.54, RAIL_Y))
sch.power_port("power:VCC", 96.52, RAIL_Y)
sch.junction(101.60, RAIL_Y)

# 集电极支路
sch.wire((129.54, RAIL_Y), R2.pin("1"))
sch.wire(R2.pin("2"), Q1.pin("C"))
sch.wire(Q1.pin("C"), C2.pin_for("c"))

# 基极支路
sch.wire((101.60, RAIL_Y), R1.pin("1"))
sch.wire(R1.pin("2"), (101.60, BASE_Y))
sch.wire((91.44, BASE_Y), (124.46, BASE_Y))
sch.junction(101.60, BASE_Y)

# 发射极直接接地
sch.wire(Q1.pin("E"), (129.54, 71.12))
sch.power_port("power:GND", 129.54, 71.12)

# 输出支路
sch.wire(C2.pin_for("out"), (158.75, 63.50))
sch.wire((158.75, 63.50), R3.pin("1"))
sch.wire(R3.pin("2"), (158.75, 74.93))
sch.power_port("power:GND", 158.75, 74.93)

# 输入支路
sch.wire(C1.pin_for("in"), (78.74, BASE_Y))
sch.wire((78.74, BASE_Y), V1.pin("1"))
sch.wire(V1.pin("2"), (78.74, 74.93))
sch.power_port("power:GND", 78.74, 74.93)

sch.label("in", 82.55, BASE_Y)
sch.label("b", 116.84, BASE_Y)
sch.label("c", 135.89, 63.50)
sch.label("out", 152.40, 63.50)

if __name__ == "__main__":
    raise SystemExit(layout_main(sch))
