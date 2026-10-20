#!/usr/bin/env python3
"""教材第 18 题：带发射极电阻(含旁路电容)的共射放大器。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from kicad_build import Schematic, layout_main

sch = Schematic("CE amp with emitter resistor (exam 18)")
RAIL_Y, BASE_Y = 35.56, 66.04

Q1 = sch.add("Q1", "Q", "Q2N2222", ["c", "b", "e"], 127.00, 66.04, 0)

R2 = sch.add("R2", "R", "510k", ["vcc", "b"], 101.60, 48.26, 0)   # Rb
R3 = sch.add("R3", "R", "3k", ["vcc", "c"], 129.54, 48.26, 0)     # Rc
R4 = sch.add("R4", "R", "1k", ["e", "0"], 129.54, 82.55, 0)       # Re
C3 = sch.add("C3", "C", "100u", ["e", "0"], 118.11, 82.55, 0)     # Ce 旁路
R5 = sch.add("R5", "R", "3k", ["out", "0"], 158.75, 71.12, 0)     # RL

C1 = sch.add("C1", "C", "10u", ["in", "b"], 88.90, 66.04, 90)
C2 = sch.add("C2", "C", "10u", ["c", "out"], 146.05, 63.50, 90)

R1 = sch.add("R1", "R", "1k", ["s", "in"], 76.20, 66.04, 90)      # 信号源内阻 Rs
V1 = sch.add("V1", "V", "SIN 30m", ["s", "0"], 63.50, 71.12, 0)

# 电源轨
sch.wire((96.52, RAIL_Y), (129.54, RAIL_Y))
sch.power_port("power:VCC", 96.52, RAIL_Y)
sch.junction(101.60, RAIL_Y)

# 集电极支路
sch.wire((129.54, RAIL_Y), R3.pin("1"))
sch.wire(R3.pin("2"), Q1.pin("C"))
sch.wire(Q1.pin("C"), C2.pin_for("c"))

# 基极偏置
sch.wire((101.60, RAIL_Y), R2.pin("1"))
sch.wire(R2.pin("2"), (101.60, BASE_Y))

# 基极线：V1 -> Rs -> C1 -> 基极，按实际引脚坐标分段
sch.wire(V1.pin("1"), (63.50, BASE_Y))
sch.wire((63.50, BASE_Y), R1.pin("1"))
sch.wire(R1.pin("2"), C1.pin("1"))
sch.wire(C1.pin("2"), Q1.pin("B"))
sch.junction(101.60, BASE_Y)

# 发射极：Q -> Re -> 地，Ce 与 Re 并联
sch.wire(Q1.pin("E"), R4.pin("1"))
sch.wire((129.54, 76.20), (118.11, 76.20))
sch.junction(129.54, 76.20)
sch.wire((118.11, 76.20), C3.pin("1"))
sch.wire(R4.pin("2"), (129.54, 88.90))
sch.power_port("power:GND", 129.54, 88.90)
sch.wire(C3.pin("2"), (118.11, 88.90))
sch.power_port("power:GND", 118.11, 88.90)

# 输出支路
sch.wire(C2.pin_for("out"), (158.75, 63.50))
sch.wire((158.75, 63.50), R5.pin("1"))
sch.wire(R5.pin("2"), (158.75, 74.93))
sch.power_port("power:GND", 158.75, 74.93)

# 信号源
sch.wire(V1.pin("2"), (63.50, 78.74))
sch.power_port("power:GND", 63.50, 78.74)

sch.label("s", 63.50, 67.31)
sch.label("in", 82.55, 66.04)
sch.label("b", 116.84, 66.04)
sch.label("c", 135.89, 63.50)
sch.label("out", 152.40, 63.50)
sch.label("e", 124.46, 76.20)

if __name__ == "__main__":
    raise SystemExit(layout_main(sch))
