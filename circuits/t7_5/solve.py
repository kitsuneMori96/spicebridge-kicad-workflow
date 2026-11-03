#!/usr/bin/env python3
"""题 7-5 换路：位置 1 → 位置 2，一阶 RC，全部走真实 ngspice。

    python3 circuits/t7_5/solve.py

  1) 换路前稳态（S 在位置 1：25 kΩ 串 100 kΩ 分压）→ u_C(0−)
  2) 0+ 电阻网络（替代定理，电容换成 u_C(0−) 的电压源）→ i(0+)、各支路分流
  3) 从电容端口看进去的 Req（两只 100 kΩ 并联）→ τ，解析式给出 u_C(t)、i(t)
  4) 真 SPDT（两只 `sw` 开关，先断后合）瞬态：控制极在 t=0 断开触点 1、10 ns 后
     合上触点 2，0− 状态由直流初始解自己给出（`.op` 与 1) 的直流解对账）
  5) 瞬态曲线反解三要素，并与 3) 的解析式、2) 的 i(0+) 交叉验证

三点容易读错的地方：
  * t<0 时**左侧 100 kΩ 与电容并联**，25 kΩ 与它串联分压 → u_C(0−) = 5×100/(25+100) = 4 V；
    不是 5×25/(25+100)。画图时 100 kΩ 竖在电容左边、上端接着动触点，别看成与源并联。
  * t>0 时 5 V 与 25 kΩ 整条支路悬空（触点 1 断开），只剩两只 100 kΩ 与电容，Req = 50 kΩ。
  * 这是**容性**换路，可以先断后合；7-3(b) 的感性回路必须反过来，见 AGENTS.md 硬规则 3。
    另外本题 τ = 0.5 s、要跑到 8τ = 4 s，控制方波的脉宽必须抬到 4 s 以上（`pw=10`），
    否则 PULSE 默认 1 s 就翻回去，后半段测的其实是第二次换路。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import circ  # noqa: E402

HERE = Path(__file__).resolve().parent
SW_OPEN, SW_CLOSED = 1e9, 1e-6
US, R25, R100, C_VAL = 5.0, 25e3, 100e3, 10e-6

base = (HERE / "netlist.cir").read_text(encoding="utf-8")


def at_position1(nl: str) -> str:
    return circ.set_value(circ.set_value(nl, "Rsw1", SW_CLOSED), "Rsw2", SW_OPEN)


def at_position2(nl: str) -> str:
    return circ.set_value(circ.set_value(nl, "Rsw1", SW_OPEN), "Rsw2", SW_CLOSED)


def swap_line(nl: str, device: str, new: str) -> str:
    """整行替换元件名为 `device` 的那一行（首字段全等匹配）。
    末尾补回换行：splitlines 吞掉了它，直接拼接会把下一个元件粘到 `.end` 上。"""
    return "\n".join(new if ln.split()[:1] == [device] else ln for ln in nl.splitlines()) + "\n"


pre, post = at_position1(base), at_position2(base)

# ---- 1) 换路前稳态
m = circ.dcop(pre, ["v(c)", "i(vram)", "i(v5)"])
u0, i_pre = m["v(c)"].real, m["i(vram)"].real
print(f"换路前稳态 : u_C(0−) = {u0:.6f} V   i(0−) = {i_pre:.6e} A（触点 2 断开，右边 100kΩ 无电流）")
print(f"             解析: 5×100/(25+100) = {US * R100 / (R25 + R100):.4f} V，i_C(0−) = 0")

# ---- 2) 0+ 电阻网络：电容换成 u_C(0−) 的电压源
m = circ.dcop(swap_line(post, "C1", "Vcap c 0 %.10f" % u0),
              ["v(c)", "i(vcap)", "i(vram)", "i(v5)"])
vc0, ic0, i0 = m["v(c)"].real, m["i(vcap)"].real, m["i(vram)"].real
print(f"换路后 0+  : u_C = {vc0:.6f} V（= 0−）  i(0+) = {i0 * 1e6:.6f} µA"
      f"   i_C(0+) = {ic0 * 1e6:.6f} µA")
print(f"             解析: 两只 100 kΩ 均分 → i = 4/100k = 40 µA，i_C = −(40+40) = −80 µA")

# ---- 3) Req / τ / 解析式（端口元件必须是电阻；这里用电阻占位而不是真开关——
#        thévenin 的“独立源置零”会把开关的控制源一起置零，开关状态当场翻车）
th = circ.thévenin(swap_line(post, "C1", "Rport c 0 1"), "Rport", ["c", "0"])
tau = th["req"] * C_VAL
print(f"端口等效   : Voc = {th['voc']:.3e} V  Req = {th['req'] / 1e3:.4f} kΩ"
      f"（解析 100k∥100k = 50k）  → τ = Req·C = {tau:.4f} s")
print(f"解析式     : u_C(t) = {u0:.4f}·e^(−t/{tau:.4f}s) V   i(t) = "
      f"{u0 / R100 * 1e6:.4f}·e^(−t/{tau:.4f}s) µA")

# ---- 4) 真 SPDT：先断（触点 1）后合（触点 2，延迟 10 ns）
sw = swap_line(post, "Rsw1", circ.switch_lines(post, "n1", "c", close=False, pw=10, per=20))
sw = swap_line(sw, "Rsw2", circ.switch_lines(sw, "c", "n2", close=True,
                                             t_action=1e-8, pw=10, per=20))
u_op = circ.op(sw, ["v(c)"])["v(c)"].real
print(f"开关自身的 0− : v(c) = {u_op:.6f} V（.op 取 PULSE 初值，与 1) 的直流解对账）")

d = circ.tran(sw, ["i(vram)", "v(c)"], 1e-7, 1e-4, tmax=1e-7)
ti, xi = d["i(vram)"][0], np.real(d["i(vram)"][1])
k = int(np.argmax(ti >= 1.5e-8))                 # 触点 2 在 1e-8 s 合上，取沿后第一点
i0_meas = float(xi[k])
print(f"瞬态实测 0+ : i(0+) = {i0_meas * 1e6:.4f} µA（替代法 {i0 * 1e6:.4f} µA，"
      f"t = {ti[k]:.3e} s）")

# ---- 5) 长瞬态反解三要素 + 整段曲线对账
r = circ.three_element(sw, "v(c)", 12 * tau, tau / 50,
                       expect={"t0": u0, "tinf": 0.0, "tau": tau},
                       tol={"t0": 1e-3, "tinf": 1e-3, "tau": 2e-2})
d2 = circ.tran(sw, ["v(c)", "i(vram)"], tau / 100, 12 * tau)
t2, uc2, i2 = d2["v(c)"][0], np.real(d2["v(c)"][1]), np.real(d2["i(vram)"][1])
post_edge = t2 > 1e-6          # 第 0 行是换路前：此时触点 2 还断着，i = 0，不归解析式管
err_uc = float(np.max(np.abs(uc2[post_edge] - u0 * np.exp(-t2[post_edge] / tau))) / u0)
err_i = float(np.max(np.abs(i2[post_edge] - u0 / R100 * np.exp(-t2[post_edge] / tau))) / (u0 / R100))
print(f"瞬态实测   : t0 = {r['t0']:.6f}  tinf = {r['tinf']:.2e}  τ = {r['tau']:.6f} s"
      f"  曲线与三要素式偏差 {r['exp_model_err']:.2e}")
print(f"             u_C(t) 与解析式最大偏差 {err_uc:.2e}，i(t) 与解析式最大偏差 {err_i:.2e}"
      f"（换路后 0~12τ 全程）")

bad = list(th["fail"]) + list(r["fail"])
def check(name, err, tol):
    if err > tol:
        bad.append((name, err))
    print(f"  [{'OK ' if err <= tol else 'BAD'}] {name:<26} 误差 {err:.2e}（限 {tol:g}）")

check("换路前 u_C(0−) 直流 vs .op", abs(u_op - u0) / abs(u0), 1e-3)
check("i(0+) 替代法 vs 瞬态", abs(i0_meas - i0) / abs(i0), 1e-2)
check("u_C(t) 实测 vs 解析式", err_uc, 2e-3)
check("i(t) 实测 vs 解析式", err_i, 2e-3)
print("\nRESULT PASS: t7_5" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
