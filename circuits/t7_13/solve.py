#!/usr/bin/env python3
"""题 7-13：u_C(0−)=0，t=0 时 S 闭合，求 t≥0 的 u_C(t) 与 i_C(t)。

    python3 circuits/t7_13/solve.py

  1) 换路前稳态（S 断开）：源网络与电容无关，v(a)=18 V、i=1 A；电容孤立未充电
  2) 换路后 0+（替代定理）：C 换成 0V 源 → u_C 不跳变，i_C(0+)=90 A
  3) 换路后稳态：C 开路 → u_C(∞)=18 V、i(∞)=1 A
  4) 从电容端口看进去的 Req（受控源保留）→ τ=0.02 s，给解析式
  5) 真 `sw` 开关瞬态（先断后合，容性电路允许）：与解析式整段对账
  6) 反例：把 8i 误读成 CCVS，稳态 16V、τ=0.1s —— 确认判据真能踢掉这个误读

解析答案（与幻灯片一致）：
    u_C(t) = 18(1 − e^(−t/0.02)) V = 18(1 − e^(−50t)) V
    i_C(t) = C·du_C/dt = 90 e^(−50t) A
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import circ  # noqa: E402

HERE = Path(__file__).resolve().parent
SW_OPEN, SW_CLOSED = 1e9, 1e-6
C_VAL, R_EQ, U_INF, I0_C = 0.1, 0.2, 18.0, 90.0
TAU = R_EQ * C_VAL                      # 0.02 s（理想开关）
RON = 1e-3                              # 本机 sw 模型 ron（SW_MODEL），与 C 串联
TAU_SW = (R_EQ + RON) * C_VAL           # 0.0201 s（仿真里真正的时间常数）
DELAY = 1e-7                            # 断→合的间隔（容性：先断后合，电压不会跳变）

base = (HERE / "netlist.cir").read_text(encoding="utf-8")
pre = circ.set_value(circ.set_value(base, "Rsw1", SW_CLOSED), "Rsw2", SW_OPEN)
post = circ.set_value(circ.set_value(base, "Rsw1", SW_OPEN), "Rsw2", SW_CLOSED)


def swap_line(nl: str, device: str, new: str) -> str:
    """整行替换元件名为 `device` 的那一行（首字段全等）。
    末尾补回换行：splitlines 吞掉了它，直接拼接会把下一个元件粘到 `.end` 上。"""
    return "\n".join(new if ln.split()[:1] == [device] else ln for ln in nl.splitlines()) + "\n"


# ---- 1) 换路前稳态：S 断开，电容上板接地、u_C=0；源网络自己的直流解
m = circ.dcop(pre, ["v(a)", "v(cp)", "v(cp2)", "i(vctrl)", "i(vic)"])
va_pre, u0, i_pre, ic_pre = (m["v(a)"].real, m["v(cp2)"].real,
                             m["i(vctrl)"].real, m["i(vic)"].real)
print(f"换路前稳态 : v(a) = {va_pre:.6f} V   u_C(0−) = {u0:.6e} V   "
      f"i(0−) = {i_pre:.6f} A（向上）   i_C(0−) = {ic_pre:.2e} A")
print(f"             解析: i=(20−v)/2、v=8i → 10v=180 → v=18 V，i=1 A；"
      f"CCCS 送 8 A 入 a，右 2Ω 取 9 A → KCL 平衡")

# ---- 2) 换路后 0+：C 换成 0V 源（u_C 不跳变），解纯电阻网络
sub = swap_line(post, "C1", "Vcap cp2 0 0")
m = circ.dcop(sub, ["v(a)", "v(cp)", "i(vic)", "i(vctrl)"])
va0, uc0, ic0, i0p = (m["v(a)"].real, m["v(cp)"].real, m["i(vic)"].real,
                      m["i(vctrl)"].real)
kcl0 = ic0 - (i0p + 8 * i0p - va0 / 2)
print(f"换路后 0+ : u_C(0+) = {uc0:.2e} V   v(a) = {va0:.6f} V   "
      f"i(0+) = {i0p:.6f} A   i_C(0+) = {ic0:.6f} A")
print(f"             KCL 核对 i_C − (i + 8i − v(a)/2) = {kcl0:.2e}（应为 0）")
print(f"             解析: 电容钳住 a=0 → i=(20−0)/2=10 A，CCCS=80 A，"
      f"全部灌进电容 → i_C=90 A")

# ---- 3) 换路后稳态：C 开路
m = circ.dcop(post, ["v(cp)", "v(cp2)", "i(vctrl)", "i(vic)"])
uinf, iinf, icinf = (m["v(cp2)"].real, m["i(vctrl)"].real, m["i(vic)"].real)
print(f"换路后稳态 : u_C(∞) = {uinf:.6f} V   i(∞) = {iinf:.6f} A   i_C(∞) = {icinf:.2e} A")
print(f"             解析: 电容充满后不再取电流，网络退回换路前的 18 V / 1 A")

# ---- 4) 电容端口 Req（独立源置零、受控源保留）→ τ
req_nl = swap_line(post, "C1", "Rport a 0 1")
th = circ.thévenin(req_nl, "Rport", ["a", "0"],
                   expect={"voc": U_INF, "isc": I0_C, "req": R_EQ})
tau = th["req"] * C_VAL
print(f"端口等效   : Voc = {th['voc']:.6f} V   Isc = {th['isc']:.6f} A   "
      f"Req = {th['req']:.6f} Ω（解析 0.2）")
print(f"             Voc/Isc 与加压求流两条路一致度 {th['req_consistency']:.1e}"
      f"  → τ = Req·C = {tau:.6f} s（极点 {1 / tau:g} s⁻¹）")
print(f"解析式     : u_C(t) = {U_INF:g}(1 − e^(−{1 / tau:g}t)) V   "
      f"i_C(t) = {I0_C:g}·e^(−{1 / tau:g}t) A")
print(f"开关串联电阻: 仿真里 sw 的 ron={RON:g}Ω 与 C 串联 → 实测应为 "
      f"τ_sw=(Req+ron)C={TAU_SW:.6f} s（比理论 0.02 s 大 {RON / R_EQ:.1%}，"
      f"对账用 τ_sw，答案仍写 0.02 s）")

I0_SW = C_VAL * U_INF / TAU_SW         # 89.55 A：τ 被 ron 拉大后 0+ 斜率也跟着变小
uc_an = lambda t: U_INF * (1 - np.exp(-np.maximum(t, 0) / TAU_SW))
ic_an = lambda t: I0_SW * np.exp(-np.maximum(t, 0) / TAU_SW)

# ---- 5) 真开关瞬态：S 在 t=0 断开位置 1，t=DELAY 合向位置 2（容性可先断后合）
sw = swap_line(post, "Rsw1", circ.switch_lines(post, "cp", "0", close=False))
sw = swap_line(sw, "Rsw2", circ.switch_lines(sw, "cp", "a", close=True,
                                             t_action=DELAY))
vecs = ["v(cp)", "i(vic)", "v(a)", "i(vctrl)"]

# 5a) 细看换路沿：tmax=2ns 抓住 0+ 的 90 A
d0 = circ.tran(sw, vecs, 2e-9, 3e-6, tmax=2e-9)
t0s, ic_s = d0["i(vic)"][0], np.real(d0["i(vic)"][1])
k0 = int(np.argmax(t0s >= DELAY + 1e-8))
ic_pred0 = ic_an(t0s[k0] - DELAY)
print(f"瞬态沿实测 : t = {t0s[k0]:.3e} s  i_C = {ic_s[k0]:.4f} A"
      f"（该时刻解析 {ic_pred0:.4f} A）   u_C = {np.real(d0['v(cp)'][1][k0]):.2e} V   "
      f"i = {np.real(d0['i(vctrl)'][1][k0]):.4f} A")

# 5b) 整段曲线（到 12τ）与解析式；tmax 压到 2µs，换路后的陡沿才不会带进 0.03V 偏差
d = circ.tran(sw, vecs, TAU_SW / 400, DELAY + 12 * TAU_SW, tmax=2e-6)
t, uc_s = d["v(cp)"][0], np.real(d["v(cp)"][1])
ic_s = np.real(d["i(vic)"][1])
i_s = np.real(d["i(vctrl)"][1])
sel_u = t > DELAY + 3e-7
err_u = float(np.max(np.abs(uc_s[sel_u] - uc_an(t[sel_u] - DELAY))) / U_INF)
sel_i = t > DELAY + 5e-4
err_i = float(np.max(np.abs(ic_s[sel_i] - ic_an(t[sel_i] - DELAY))) / I0_C)
sel_n = t > DELAY + 5e-4
# i(t) 不用三要素：由 u_C 与 KCL 反推，看点是否落在解析轨上
i_an = lambda tq: (20.0 - uc_an(tq) - RON * ic_an(tq)) / 2.0   # v(a)=u_C+ron·i_C
err_in = float(np.max(np.abs(i_s[sel_n] - i_an(t[sel_n] - DELAY))) / 10.0)
r = circ.three_element(sw, "v(cp)", DELAY + 12 * TAU_SW, TAU_SW / 400, tmax=2e-6,
                       expect={"t0": 0.0, "tinf": U_INF, "tau": TAU_SW},
                       tol={"tinf": 2e-3, "tau": 1e-2})
print(f"曲线对账   : u_C 最大偏差 {err_u:.2e}（相对 18V）；i_C 最大偏差 "
      f"{err_i:.2e}（相对 90A）；i 最大偏差 {err_in:.2e}（相对 10A）")
print(f"三要素反解 : u_C: t0={r['t0']:.2e} V  tinf={r['tinf']:.6f} V  τ={r['tau']:.6f} s"
      f"  曲线拟合残差 {r['exp_model_err']:.2e}")
print(f"KCL 抽查   : t=1τ 时 u_C={U_INF * (1 - np.exp(-1)):.4f} V，"
      f"i=1+9e⁻¹={1 + 9 * np.exp(-1):.4f} A，i_C=90e⁻¹={90 * np.exp(-1):.4f} A；"
      f"9i = {9 * (1 + 9 * np.exp(-1)):.4f}，u_C/2 + i_C = "
      f"{U_INF * (1 - np.exp(-1)) / 2 + 90 * np.exp(-1):.4f}")

# ---- 6) 反例：把 8i 误读成 CCVS（菱形看成功控电压源）→ 16V、τ=0.1s
wrong = swap_line(post, "Fcccs", "Hccvs a 0 Vctrl 8")
mw = circ.dcop(wrong, ["v(a)"])
vw = mw["v(a)"].real
tau_wrong = 1.0 * C_VAL                 # 压源输出阻抗 0 → 2Ω∥2Ω
tq = np.linspace(0.0, 0.1, 2000)
gap = float(np.max(np.abs(uc_an(tq) - vw * (1 - np.exp(-tq / tau_wrong)))))
print(f"反例核对   : 误读成 CCVS → u_C(∞)={vw:.4f} V、τ={tau_wrong:g} s，"
      f"与真解曲线最大差 {gap:.3f} V；本判据（相对容差 2e-3 ≈ 0.036 V）能区分")

bad = list(th["fail"]) + list(r["fail"])


def check(name, err, tol):
    if err > tol:
        bad.append((name, err))
    print(f"  [{'OK ' if err <= tol else 'BAD'}] {name:<30} 误差 {err:.2e}（限 {tol:g}）")


check("u_C(0−) 仿真 vs 给定 0", abs(u0) / 1.0, 1e-6)
check("i(0−) 仿真 vs 解析 1 A", abs(i_pre - 1.0) / 1.0, 1e-3)
check("v(a)(0−) 仿真 vs 解析 18 V", abs(va_pre - 18.0) / 18.0, 1e-3)
check("u_C(0+) 不跳变", abs(uc0) / 18.0, 1e-3)
check("i_C(0+) 替代法 vs 解析 90 A", abs(ic0 - I0_C) / I0_C, 1e-3)
check("i(0+) 替代法 vs 解析 10 A", abs(i0p - 10.0) / 10.0, 1e-3)
check("0+ KCL 残差", abs(kcl0), 1e-6)
check("u_C(∞) 仿真 vs 解析 18 V", abs(uinf - 18.0) / 18.0, 1e-3)
check("i(∞) 仿真 vs 解析 1 A", abs(iinf - 1.0) / 1.0, 1e-3)
check("瞬态沿 i_C vs 解析值", abs(abs(ic_s[k0]) - abs(ic_pred0)) / I0_C, 5e-2)
check("u_C(t) 实测 vs 解析式", err_u, 2e-3)
check("i_C(t) 实测 vs 解析式", err_i, 5e-3)
check("i(t) 实测 vs 解析式", err_in, 3e-3)
check("τ 实测 vs (Req+ron)C", abs(r["tau"] - TAU_SW) / TAU_SW, 1e-2)
check("τ 实测 vs 理论 0.02 s", abs(r["tau"] - tau) / tau, 1.5e-2)
if gap < 1.0:                      # 反例必须与真解明显不同，判据才有意义
    bad.append(("反例未区分", gap))
print("\nRESULT PASS: t7_13" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
