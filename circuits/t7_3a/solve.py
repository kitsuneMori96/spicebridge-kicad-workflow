#!/usr/bin/env python3
"""题 7-3(a) 换路瞬间：三次直流求解 + 一次瞬态实测，全部走真实 ngspice。

    python3 circuits/t7_3a/solve.py

方法就是教材那几步，只是每一步都由仿真给数：
  1) 换路前稳态（S 闭合）→ 读出 u_C(0−)
  2) 换路后 0+：把电容换成 u_C(0−) 的电压源（替代定理）→ 解纯电阻网络，
     该电压源的电流就是 i_C(0+)，各节点电压就是各量的 0+ 值
  3) 换路后稳态（S 已打开）→ 读出 u_C(∞)
  4) 从电容端口看进去的 Req（独立源置零 + 1A 测试源）→ τ = Req·C
  5) 瞬态实测曲线反解 τ，与 4) 交叉验证

**换路方向**：题图里 S 换路前是闭合的，t=0 打开。别照开关符号的画法想当然——方向读反，
u_C(0−) 从 15 V 变 10 V、i_C(0+) 从 −1/6 A 变 +4/23 A、τ 从 30 µs 变 28.75 µs，全错。

第 5) 步怎么换路？用真开关：`circ.switch_lines()` 生成 `S` 器件 + `.model swmod sw` +
方波控制极，整段替换掉 `Rsw` 那一行。控制极 t<0 给 +5V（导通）、t=0 跳到 −5V（阈值 vt=1，
低于即关断），所以直流初始解就是"开关短路"的网络，u_C 的 0− 值由仿真自己算出来，
不需要 `ic=` + `uic` 手工回填——这一步与前面 1) 的独立直流解互为对账。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import circ  # noqa: E402

HERE = Path(__file__).resolve().parent
SW_OPEN, SW_CLOSED, C_VAL = 1e9, 1e-6, 1e-6

base = (HERE / "netlist.cir").read_text(encoding="utf-8")
pre = circ.set_value(base, "Rsw", SW_CLOSED)     # 换路前 S 闭合
post = circ.set_value(base, "Rsw", SW_OPEN)      # t=0 打开


def swap_line(nl: str, device: str, new: str) -> str:
    """整行替换元件名为 `device` 的那一行。必须按首字段全等匹配——
    用 startswith 的话 "V1" 会连 "V10" 一起换掉，把 10V 源悄悄覆盖成重复元件。
    末尾补回换行：splitlines 吞掉了它，直接拼接会把下一个元件粘到 `.end` 上。"""
    return "\n".join(new if ln.split()[:1] == [device] else ln for ln in nl.splitlines()) + "\n"


# 1) 换路前稳态
m = circ.dcop(pre, ["v(a)", "v(p)"])
u0 = m["v(a)"].real - m["v(p)"].real
print(f"换路前稳态 : u_C(0−) = {u0:.6f} V   （S 闭合、i_C=0 → i = (30−10)/(15+5) = 1 A"
      f" → v(a) = 30−15 = 15 V，25Ω 无压降）")

# 2) 换路后 0+：电容换成 u_C(0−) 的电压源
m = circ.dcop(swap_line(post, "C1", "Vcap a p %.10f" % u0),
              ["v(a)", "v(p)", "i(v1)", "i(v10)", "i(vcap)"])
va, vp = m["v(a)"].real, m["v(p)"].real
i15, i5, ic = -m["i(v1)"].real, m["i(v10)"].real, m["i(vcap)"].real
print(f"换路后 0+  : u_C = {va - vp:.6f} V（必须等于 0−）  v(a) = {va:.6f} V  v(p) = {vp:.6f} V")
print(f"             i_15 = {i15:.6f} A   i_5 = {i5:.6f} A   i_C(0+) = {ic:.6f} A")
print(f"             KCL 核对: i_15 − i_5 − i_C = {i15 - i5 - ic:.2e}（应为 0）")

# 3) 换路后稳态
m = circ.dcop(post, ["v(a)", "v(p)"])
uinf = m["v(a)"].real - m["v(p)"].real
print(f"换路后稳态 : u_C(∞) = {uinf:.6f} V")

# 4) 从电容端口看进去的等效电阻 → τ（端口元件必须是电阻，thévenin 靠改它的阻值）
th = circ.thévenin(swap_line(post, "C1", "Rport a p 1"), "Rport", ["a", "p"])
tau = th["req"] * C_VAL
print(f"端口等效   : Voc = {th['voc']:.6f} V  Req = {th['req']:.6f} Ω（解析 5+25=30，"
      f"15 Ω 支路随开关断开）  → τ = Req·C = {tau * 1e6:.4f} µs")
print(f"解析式     : u_C(t) = {uinf:.4f} + ({u0 - uinf:.4f})·e^(−t/{tau * 1e6:.4f}µs) V")

# 5) 瞬态实测：真 `sw` 开关在 t=0 打开，控制极 +5V→−5V，换路前状态由仿真自己解出
switched = swap_line(post, "Rsw", circ.switch_lines(post, "w1", "a", close=False))
r = circ.three_element(switched, "v(a)-v(p)", 8 * tau, tau / 50,
                       expect={"t0": u0, "tinf": uinf, "tau": tau},
                       tol={"t0": 1e-3, "tinf": 1e-3, "tau": 2e-2}, tmax=1e-9)
# 跳变沿只有 1 ns，τ 有 30 µs，读 0+ 电流得把内部步长压到 ns 量级才追得到沿
ti, xi = circ.tran(switched, ["i(vsen)"], 1e-9, tau / 20, tmax=1e-9)["i(vsen)"]
i0_meas = float(np.real(xi[np.argmax(np.abs(xi))]))     # 沿后幅值单调衰减，峰值即 0+
print(f"瞬态实测   : t0 = {r['t0']:.6f}（换路前直流 {u0:.6f}）  tinf = {r['tinf']:.6f}"
      f"  τ = {r['tau'] * 1e6:.4f} µs  曲线与三要素式偏差 {r['exp_model_err']:.2e}")
print(f"             i_C 首末对比: i_C(0+) = {i0_meas:.6f} A（替代法算出 {ic:.6f} A）"
      f"  i_C(τ/20) = {float(np.real(xi[-1])):.6f} A → 幅值按指数衰减 ✓")

bad = th["fail"] + r["fail"]
if abs(i0_meas - ic) / abs(ic) > 0.01:        # 分母取绝对值：i_C 现在是负的
    bad.append(("i_C(0+) 瞬态 vs 替代法", abs(i0_meas - ic) / abs(ic)))
print("\nRESULT PASS: t7_3a" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
