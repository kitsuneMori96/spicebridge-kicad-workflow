#!/usr/bin/env python3
"""题 7-6 换路：位置 1 → 位置 2，一阶 RL，全部走真实 ngspice。

    python3 circuits/t7_6/solve.py

  1) 换路前稳态（位置 1：10V—1Ω—L—4Ω，电感短路）→ i_L(0−)、u_L(0−)
  2) 0+ 电阻网络（替代定理，电感换成 i_L(0−) 的电流源）→ u_L(0+)
  3) 从电感端口看进去的 Req（换路后 4+4=8 Ω）→ τ = L/Req，给出解析式
  4) 真 SPDT 瞬态（**先合后断**）：位置 2 在 t=0 合上、位置 1 延后 50 ns 断开，
     0− 状态由直流初始解自己给出；沿后读数与 2)、3) 交叉验证
  5) i(t) 与 u_L(t) 整段曲线对解析式

**必须先合后断**：位置 1 一断，电感电流 2 A 就只剩 roff=1e12 可走，L·di/dt 打出
1e12 V 尖峰、数值解静默塌掉（7-3(b) 实测 i_L 从 1.2 A 塌到 3e-5 A）。代价是 50 ns 的
重叠窗口里位置 1、2 同时接着，v(c) 被拉到 6.4 V，u_L 在这个窗口里是 −1.6 V 的过渡值，
**不是**答案的 −16 V——所以 0+ 要等窗口结束（t ≥ 2e-7）再读，而教科书里的"瞬间换向"
对应的是窗口结束那一刻。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import circ  # noqa: E402

HERE = Path(__file__).resolve().parent
SW_OPEN, SW_CLOSED = 1e9, 1e-6
US, R1, R4, L_VAL = 10.0, 1.0, 4.0, 1.0

base = (HERE / "netlist.cir").read_text(encoding="utf-8")
pre = circ.set_value(circ.set_value(base, "Rsw1", SW_CLOSED), "Rsw2", SW_OPEN)
post = circ.set_value(circ.set_value(base, "Rsw1", SW_OPEN), "Rsw2", SW_CLOSED)


def swap_line(nl: str, device: str, new: str) -> str:
    """整行替换元件名为 `device` 的那一行（首字段全等）。
    末尾补回换行：splitlines 吞掉了它，直接拼接会把下一个元件粘到 `.end` 上。"""
    return "\n".join(new if ln.split()[:1] == [device] else ln for ln in nl.splitlines()) + "\n"


# ---- 1) 换路前稳态
m = circ.dcop(pre, ["v(c)", "v(b)", "i(l1)"])
i0 = m["i(l1)"].real
ul0 = m["v(c)"].real - m["v(b)"].real
print(f"换路前稳态 : i_L(0−) = {i0:.6f} A   u_L(0−) = {ul0:.6f} V")
print(f"             解析: i = 10/(1+4) = 2 A，电感短路 → u_L = 0")

# ---- 2) 0+ 电阻网络：电感换成 i_L(0−) 的电流源
m = circ.dcop(swap_line(post, "L1", "IL c b %.10f" % i0), ["v(c)", "v(b)", "i(v10)"])
va, vb = m["v(c)"].real, m["v(b)"].real
ul = va - vb
print(f"换路后 0+  : u_L(0+) = v(c) − v(b) = {va:.6f} − {vb:.6f} = {ul:.6f} V"
      f"（i_L = {i0:.6f} A 不变）")
print(f"             解析: 2 A 经 4Ω 右支路 → v(b) = 8 V；回程经 4Ω 竖支路 → v(c) = −8 V")

# ---- 3) 端口 Req → τ（端口元件必须是电阻）
th = circ.thévenin(swap_line(post, "L1", "Rport c b 1"), "Rport", ["c", "b"])
tau = L_VAL / th["req"]
print(f"端口等效   : Req = {th['req']:.6f} Ω（解析 4+4=8，10V 支路随 S 断开）"
      f"  → τ = L/Req = {tau:.4f} s")
print(f"解析式     : i(t) = {i0:.4f}·e^(−t/{tau:.4f}s) A   u_L(t) = "
      f"{ul:.4f}·e^(−t/{tau:.4f}s) V")

# ---- 4) 真 SPDT：先合（位置 2，t=0）后断（位置 1，t=5e-8）
sw = swap_line(post, "Rsw2", circ.switch_lines(post, "c", "n2", close=True, pw=10, per=20))
sw = swap_line(sw, "Rsw1", circ.switch_lines(sw, "n1", "c", close=False,
                                             t_action=5e-8, pw=10, per=20))
d = circ.tran(sw, ["i(l1)", "v(c)", "v(b)"], 1e-9, 1e-5, tmax=1e-9)
t, il = d["i(l1)"][0], np.real(d["i(l1)"][1])
ulv = np.real(d["v(c)"][1]) - np.real(d["v(b)"][1])
k = int(np.argmax(t >= 2e-7))                       # 重叠窗口结束（5e-8）之后再读
print(f"瞬态实测 0+ : i = {il[k]:.6f} A   u_L = {ulv[k]:.6f} V（t = {t[k]:.3e} s，重叠窗口已过）")
print(f"             重叠窗口内 u_L 读数是 {ulv[np.argmin(np.abs(t - 2e-8))]:.4f} V"
      f"——位置 1、2 同时接着的过渡值，不是答案")

# ---- 5) 整段曲线 vs 解析式
d2 = circ.tran(sw, ["i(l1)", "v(c)", "v(b)"], tau / 200, 12 * tau)
t2, il2 = d2["i(l1)"][0], np.real(d2["i(l1)"][1])
ul2 = np.real(d2["v(c)"][1]) - np.real(d2["v(b)"][1])
sel = t2 > 2e-7                                     # 重叠窗口不归解析式管
err_i = float(np.max(np.abs(il2[sel] - i0 * np.exp(-t2[sel] / tau))) / i0)
err_ul = float(np.max(np.abs(ul2[sel] - ul * np.exp(-t2[sel] / tau))) / abs(ul))
r = circ.three_element(sw, "i(l1)", 12 * tau, tau / 200,
                       expect={"t0": i0, "tinf": 0.0, "tau": tau},
                       tol={"t0": 1e-3, "tinf": 1e-3, "tau": 2e-2})
print(f"曲线对账   : i(t) 与解析式最大偏差 {err_i:.2e}，u_L(t) 最大偏差 {err_ul:.2e}（0~12τ）")
print(f"三要素反解 : i(0+) = {r['t0']:.6f} A  tinf = {r['tinf']:.2e}  τ = {r['tau']:.6f} s")

bad = list(th["fail"]) + list(r["fail"])
def check(name, err, tol):
    if err > tol:
        bad.append((name, err))
    print(f"  [{'OK ' if err <= tol else 'BAD'}] {name:<26} 误差 {err:.2e}（限 {tol:g}）")

check("u_L(0+) 替代法 vs 瞬态", abs(ulv[k] - ul) / abs(ul), 1e-3)
check("i(0+) 瞬态", abs(il[k] - i0) / i0, 1e-3)
check("i(t) 实测 vs 解析式", err_i, 2e-3)
check("u_L(t) 实测 vs 解析式", err_ul, 2e-3)
print("\nRESULT PASS: t7_6" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
