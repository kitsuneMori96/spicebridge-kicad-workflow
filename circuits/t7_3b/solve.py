#!/usr/bin/env python3
"""题 7-3(b) 换路瞬间：S 从位置 1 打到位置 2，求 0+ 时刻电压电流。

    python3 circuits/t7_3b/solve.py

和 (a) 同一套路，只是记忆元件换成电感，所以"替代"用等值电流源：
  1) 换路前稳态（S 在 1）→ i_L(0−)；电感在直流下就是短路
  2) 换路后 0+：电感换成 i_L(0−) 的电流源 → 解纯电阻网络 → u_L(0+)
  3) 换路后稳态 → i_L(∞) = 0（右侧回路里已无独立源）
  4) 从电感端口看进去的 Req → τ = L/Req
  5) 瞬态实测（`ic=` + `uic`）反解 τ，与 4) 交叉验证
  6) 真 `sw` 开关版复核：同一张图插两只开关当 SPDT，靠控制极电压换路、初值不回填，
     看 i_L 是否真的能穿过换路沿保持不变（换路定律由实测成立，不是我们假定它成立）

注意网表里 3A 源写的是 `I3 0 n1 3`：ngspice 的独立电流源正方向是从 n+ 经过源流向
n−，图中箭头向上（向 n1 注入），所以 n+ 要写电源侧的 0，写反了整个响应差一个负号。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import circ  # noqa: E402

HERE = Path(__file__).resolve().parent
L_VAL = 10.0


def swap_line(nl: str, device: str, new: str) -> str:
    """整行替换元件名为 `device` 的那一行（按首字段全等，避免 "L1" 命中 "L10"）。
    末尾补回换行：splitlines 会吞掉它，直接拼接就会把下一个元件粘到 `.end` 上。"""
    return "\n".join(new if ln.split()[:1] == [device] else ln for ln in nl.splitlines()) + "\n"


pre = (HERE / "pre.cir").read_text(encoding="utf-8")
post = (HERE / "post.cir").read_text(encoding="utf-8")

# 1) 换路前稳态
m = circ.dcop(pre, ["v(n1)", "v(n2)", "i(l1)"])
i0 = m["i(l1)"].real
print(f"换路前稳态 : i_L(0−) = {i0:.6f} A   v(n1) = {m['v(n1)'].real:.6f} V"
      f"   v(n2) = {m['v(n2)'].real:.6f} V（电感短路）")
print(f"             解析: 3A 在 20Ω∥(30Ω+短路) 上分流 → i_L = 3×20/50 = 1.2 A, v(n1) = 36 V")

# 2) 换路后 0+：电感换成 i_L(0−) 的电流源
sub = swap_line(post, "L1", "Iind n2 0 %.10f" % i0)
m = circ.dcop(sub, ["v(n2)", "v(piv)", "v(n1)", "i(vsw)"])
ul0, vpiv, vn1 = m["v(n2)"].real, m["v(piv)"].real, m["v(n1)"].real
print(f"换路后 0+  : u_L(0+) = {ul0:.6f} V   v(位置2) = {vpiv:.6f} V   v(n1) = {vn1:.6f} V")
print(f"             解析: −i_L·(30+15) = {-i0 * 45:.4f} V；左侧 3A 全灌 20Ω → v(n1) = 60 V")

# 3) 换路后稳态
closed = swap_line(post, "L1", "L1 n2 0 %.10g ic=%.10f" % (L_VAL, i0))
m = circ.dcop(closed, ["v(n2)", "v(n1)"])
print(f"换路后稳态 : i_L(∞) = 0（回路无源）  v(n1) = {m['v(n1)'].real:.6f} V 保持 60 V")

# 4) 从电感端口看进去的 Req → τ（端口元件必须是电阻）
th = circ.thévenin(swap_line(post, "L1", "Rport n2 0 1"), "Rport", ["n2", "0"])
tau = L_VAL / th["req"]
print(f"端口等效   : Req = {th['req']:.6f} Ω（解析 30+15 = 45）  → τ = L/Req = {tau:.6f} s")
print(f"解析式     : i_L(t) = {i0:.4f}·e^(−t/{tau:.4f}s) A   u_L(t) = {-i0 * 45:.4f}·e^(−t/{tau:.4f}s) V")

# 5) 瞬态实测反解 τ
r = circ.three_element(closed, "i(l1)", 8 * tau, tau / 200,
                       expect={"t0": i0, "tinf": 0.0, "tau": tau},
                       tol={"t0": 1e-3, "tinf": 1e-2, "tau": 2e-2}, uic=True)
d = circ.tran(closed, ["v(n2)", "i(l1)"], tau / 200, 8 * tau, uic=True)
ul_meas = float(np.real(d["v(n2)"][1][0]))
print(f"瞬态实测   : i_L(0+) = {r['t0']:.6f} A  tinf = {r['tinf']:.2e}  τ = {r['tau']:.6f} s"
      f"  曲线与三要素式偏差 {r['exp_model_err']:.2e}")
print(f"             u_L 首样 = {ul_meas:.6f} V（替代法算出 {ul0:.6f} V）")

bad = th["fail"] + r["fail"]
if abs(ul_meas - ul0) / abs(ul0) > 0.01:
    bad.append(("u_L(0+) 瞬态 vs 替代法", abs(ul_meas - ul0) / abs(ul0)))

# 6) 换路定律的独立复核：把 SPDT 做成两只真 `sw` 开关，同一张图靠控制极电压换路，
#    初值一律不手工回填。i_L(0−) 必须由仿真自己从直流初始解里长出来。
#    开关顺序是**先合后断**（位置 2 在 t=0 闭合，位置 1 延后 5e-8 s 才断开）：
#    电感电流必须有通路，先断后合会让 1.2 A 撞向 roff=1e12，打出 1e12 V 尖峰把数值解冲垮
#    （实测 i_L 一路塌到 3e-5 A）。重叠的 50 ns 里 u_L 是 −20.6 V 的过渡值，不读它。
nl = swap_line(pre, "R30", "R30 piv n2 30") + "R15 k 0 15\n"
nl += circ.switch_lines(nl, "piv", "k")                                   # 位置 2，t=0 闭合
nl += circ.switch_lines(nl, "piv", "n1", close=False, t_action=5e-8)      # 位置 1，随后断开
d = circ.tran(nl, ["i(l1)", "v(n2)"], 1e-7, 2e-5, tmax=1e-9)
ti = d["i(l1)"][0]
il_edge = float(np.real(d["i(l1)"][1][np.searchsorted(ti, 5e-9)]))       # 沿上
il_end = float(np.real(d["i(l1)"][1][-1]))                               # 沿后 20 µs
ul_edge = float(np.real(d["v(n2)"][1][-1]))
il_ana = i0 * float(np.exp(-ti[-1] / tau))
print(f"真开关复核  : i_L 沿上 = {il_edge:.6f} A（沿前直流 {i0:.6f}）→ 连续性成立")
print(f"             u_L 沿后 = {ul_edge:.6f} V（替代法 {ul0:.6f}）  "
      f"i_L(20µs) = {il_end:.6f} A（三要素式 {il_ana:.6f}）")
for name, got, ref in (("i_L(0−) 仿真自解", il_edge, i0),
                       ("u_L(0+) 真开关 vs 替代法", ul_edge, ul0),
                       ("i_L(20µs) vs 三要素式", il_end, il_ana)):
    err = abs(got - ref) / abs(ref)
    if err > 1e-3:
        bad.append((name, err))
print("\nRESULT PASS: t7_3b" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
