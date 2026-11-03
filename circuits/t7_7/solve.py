#!/usr/bin/env python3
"""题 7-7 开关闭合：二阶电路（L、C 分处两条支路，合不成一只等效储能元件）。

    python3 circuits/t7_7/solve.py

三条规定都接在节点 m 与地之间（S 断开时中支路悬空）：
  左：100 Ω + 0.1 H      中：S + 150 Ω + 60 V      右：20 µF + 100 Ω
t<0 时唯一闭合的回路（左—m—右—地）里没有电源 → 全零状态；
t=0 合上 S，60 V 源经 150 Ω 接到 m，把左边 RL 与右边 RC 一起驱动起来。

  1) 换路前稳态 → 验证零状态（i_L(0−)=0、u_C(0−)=0）
  2) 0+ 电阻网络（替代定理：L→0 A 源、C→0 V 源）→ i(0+)、v(m)(0+)
  3) 换路后稳态 → i(∞)、v(m)(∞)
  4) 状态方程（L、C 两个状态）解析求解 → i(t) 的显式表达式
  5) 真 `sw` 开关瞬态与 4) 整段曲线对账，并核对 i(t) 的非单调性（先下探再回来）

**没有 τ**：极点是两个实根 −343.2 / −1456.8 s⁻¹，属过阻尼二阶电路，
三要素式不成立——用状态转移矩阵，别硬套一阶模板。

一个反直觉的结果：i(0+) = i(∞) = −0.24 A，但 i(t) 在两者之间还会再往负方向探到
约 −0.261 A 才回来（因为 i_L 与 u_C 都从 0 出发，前几毫秒里电容分走的电流在变）。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import circ  # noqa: E402

HERE = Path(__file__).resolve().parent
SW_OPEN, SW_CLOSED = 1e9, 1e-6
E, RL, LL, RR, CB, RM = 60.0, 100.0, 0.1, 100.0, 20e-6, 150.0

base = (HERE / "netlist.cir").read_text(encoding="utf-8")
pre = circ.set_value(base, "Rsw", SW_OPEN)       # 换路前 S 断开
post = circ.set_value(base, "Rsw", SW_CLOSED)     # t=0 闭合


def swap_line(nl: str, device: str, new: str) -> str:
    """整行替换元件名为 `device` 的那一行（首字段全等）。
    末尾补回换行：splitlines 吞掉了它，直接拼接会把下一个元件粘到 `.end` 上。"""
    return "\n".join(new if ln.split()[:1] == [device] else ln for ln in nl.splitlines()) + "\n"


def expm_at(A: np.ndarray, t: np.ndarray) -> np.ndarray:
    """2×2 状态矩阵的 e^{At}，逐时刻返回 (N,2,2)（特征分解，不依赖 scipy）。"""
    lam, V = np.linalg.eig(A)
    e = np.exp(t[:, None] * lam[None, :])[:, None, :]
    return (V[None] * e) @ np.linalg.inv(V)


# ---- 1) 换路前：零状态
m = circ.dcop(pre, ["v(m)", "v(r)", "i(l1)", "i(vam)"])
u0, iL0 = m["v(m)"].real - m["v(r)"].real, m["i(l1)"].real
print(f"换路前稳态 : i_L(0−) = {iL0:.3e} A   u_C(0−) = {u0:.3e} V   "
      f"i(0−) = {m['i(vam)'].real:.3e} A")
print("             解析: S 断开 → 左-m-右 这条回路里没有电源，稳态全零（与 t<0 的零状态一致）")

# ---- 2) 0+ 电阻网络：L→0 A 电流源，C→0 V 电压源
sub = swap_line(post, "L1", "IL p m 0")
sub = swap_line(sub, "C1", "Vcap m r 0")
m = circ.dcop(sub, ["v(m)", "i(vam)", "i(vcap)"])
vm0, i0, icap = m["v(m)"].real, m["i(vam)"].real, m["i(vcap)"].real
print(f"换路后 0+  : v(m) = {vm0:.6f} V   i(0+) = {i0 * 1e3:.6f} mA   "
      f"i_C(0+) = {icap * 1e3:.6f} mA")
print(f"             KCL 核对: i_L − i − i_C = {0.0 - i0 - icap:.2e}（i_L(0+)=0，应为 0）")
print(f"             解析: 0.1H 电流为 0 → 左支路全断；电容短路把 m 与 r 并在一起，"
      f"(v−60)/150 + v/100 = 0 → v = 24 V，i = −0.24 A")

# ---- 3) 换路后稳态
m = circ.dcop(post, ["v(m)", "v(r)", "i(l1)", "i(vam)"])
uinf, iinf = m["v(m)"].real - m["v(r)"].real, m["i(vam)"].real
print(f"换路后稳态 : v(m) = {m['v(m)'].real:.6f} V  u_C(∞) = {uinf:.6f} V  "
      f"i_L(∞) = {m['i(l1)'].real:.6f} A  i(∞) = {iinf * 1e3:.6f} mA")
print("             解析: 电容开路、电感短路 → 60V 灌进 150+100 Ω → 0.24 A，v(m) = 24 V")

# ---- 4) 状态方程：x = [u_C, i_L]
#   KCL@m: i_L = (v_m − E)/150 + C·du_C/dt；KCL@r: C·du_C/dt = v_r/100；u_C = v_m − v_r
#   → v_m = 0.6u_C + 60i_L + 24，v_r = −0.4u_C + 60i_L + 24
#   → du_C/dt = (−0.4u_C + 60i_L + 24)/(100C)，di_L/dt = −(v_m + 100i_L)/L
A = np.array([[-0.4 / (RR * CB), 60.0 / (RR * CB)],
              [-0.6 / LL, -(60.0 + RL) / LL]])
b = np.array([24.0 / (RR * CB), -24.0 / LL])
xinf = -np.linalg.solve(A, b)
lam = np.linalg.eigvals(A)
print(f"状态矩阵   : A = {np.round(A, 3).tolist()}  b = {np.round(b, 1).tolist()}"
      f"  极点 {lam[0]:.4f} / {lam[1]:.4f} s⁻¹（过阻尼）")
print(f"             特解 x(∞) = [u_C, i_L] = [{xinf[0]:.6f}, {xinf[1]:.6f}]"
      f"（与 3) 的仿真稳态对账）")
x0 = np.array([u0, iL0])                          # 0− 零状态，由 1) 独立解出
di0 = 0.004 * (A[0] @ x0 + b[0]) + 0.4 * (A[1] @ x0 + b[1])
c = np.linalg.solve([[1.0, 1.0], [lam[0], lam[1]]], [i0 - iinf, di0])
print(f"解析式     : i(t) = {iinf:.6f} + ({c[0]:.6f})·e^({lam[0]:.3f}t)"
      f" + ({c[1]:.6f})·e^({lam[1]:.3f}t) A，t ≥ 0")
_, V = np.linalg.eig(A)
a = np.real(np.linalg.solve(V, x0 - xinf))
Vr = np.real(V)
for name, row, inf in (("u_C(t)", Vr[0] * a, xinf[0]), ("i_L(t)", Vr[1] * a, xinf[1])):
    print(f"             {name} = {inf:.6f} + ({row[0]:.6f})·e^({lam[0]:.3f}t)"
          f" + ({row[1]:.6f})·e^({lam[1]:.3f}t)")

# ---- 5) 真开关瞬态
sw = swap_line(post, "Rsw", circ.switch_lines(post, "m", "d", close=True, pw=10, per=20))
d = circ.tran(sw, ["i(vam)", "v(m)", "v(r)", "i(l1)"], 1e-5, 6 / abs(lam[0]))
t, ii = d["i(vam)"][0], np.real(d["i(vam)"][1])
uc_s, il_s = np.real(d["v(m)"][1]) - np.real(d["v(r)"][1]), np.real(d["i(l1)"][1])
X = xinf + expm_at(A, t) @ (x0 - xinf)
i_an = 0.004 * X[:, 0] + 0.4 * X[:, 1] - 0.24
sel = t > 1e-6                                    # 开关沿附近不归解析式管
err = float(np.max(np.abs(ii[sel] - i_an[sel])) / 0.24)
err_uc = float(np.max(np.abs(uc_s[sel] - X[sel, 0])) / 24)
err_il = float(np.max(np.abs(il_s[sel] - X[sel, 1])) / 0.24)
tmin_c = float(np.log(-c[0] * lam[0] / (c[1] * lam[1])) / (lam[1] - lam[0]))   # i'(t)=0 的闭式解
imin_c = iinf + c[0] * np.exp(lam[0] * tmin_c) + c[1] * np.exp(lam[1] * tmin_c)
imin_s, tmin_s = float(np.interp(tmin_c, t, ii)), tmin_c       # 仿真曲线在闭式时刻插值
kgrid = int(np.argmin(ii[sel]))
print(f"瞬态实测   : i 全程最大偏差 {err:.2e}（相对 0.24 A），曲线与状态方程解对账")
print(f"非单调核对 : 闭式最低 i = {imin_c * 1e3:.4f} mA @ t = {tmin_c * 1e3:.4f} ms"
      f"；仿真同点插值 {imin_s * 1e3:.4f} mA（采样网格 argmin 落在 {t[sel][kgrid] * 1e3:.4f} ms）")
print(f"起点/终点  : i(0+) = {ii[sel][0] * 1e3:.4f} mA   i(∞) = {iinf * 1e3:.4f} mA"
      f"（二者相等，但中途下探）")

bad = []
def check(name, err, tol):
    if err > tol:
        bad.append((name, err))
    print(f"  [{'OK ' if err <= tol else 'BAD'}] {name:<26} 误差 {err:.2e}（限 {tol:g}）")

check("i(0+) 替代法 vs 解析", abs(i0 - (0.004 * 0 + 0.4 * 0 - 0.24)) / 0.24, 1e-3)
check("稳态 v(m) 仿真 vs 解析", abs(m["v(m)"].real - (0.6 * xinf[0] + 60 * xinf[1] + 24)) / 24, 1e-3)
check("i(t) 实测 vs 状态方程解", err, 2e-3)
check("u_C(t) 实测 vs 状态方程解", err_uc, 2e-3)
check("i_L(t) 实测 vs 状态方程解", err_il, 2e-3)
check("最低点幅值 实测 vs 闭式", abs(imin_s - imin_c) / 0.24, 5e-3)
check("最低点时刻 实测 vs 闭式", abs(t[sel][kgrid] - tmin_c), 2e-5)     # 采样网格 10 µs
print("\nRESULT PASS: t7_7" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
