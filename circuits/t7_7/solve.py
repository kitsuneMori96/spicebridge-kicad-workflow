#!/usr/bin/env python3
"""题 7-7：S 由断开到闭合，求 t≥0 的电流 i（由 m 流向 k，向下）。

    python3 circuits/t7_7/solve.py

拓扑（按放大后的原图逐段核对）：
  外围是一个串联回路：100Ω(p—地) — 0.1H(p—m) — 20µF(m—r) — 100Ω(r—地)；
  m 向下一条线（标着 i）到节点 k；k 到地有**两条并联支路**：
  一条是开关 S，另一条是 150Ω + 60V（60V 的 + 极朝上、接 150Ω）。

上一版把 S 当成串在 150Ω 支路里，于是认为 S 断开时中支路悬空、电路零状态——读错了。
实际 S 断开时 150Ω+60V 仍接在 m 与地之间，换路前是**非零稳态**：
  v(m) = 60·100/(100+150) = 24 V，i_L = −0.24 A，u_C = 24 V。
S 闭合后 k 被短接到地，点 m 也被短接到地，60V 源只在 k—150Ω—60V—S 的局部回路里
空转 0.4 A，不再影响 m；左右两条支路各自独立放电：
  τ_C = 100Ω·20µF = 2 ms（极点 −500 s⁻¹），τ_L = 0.1H/100Ω = 1 ms（极点 −1000 s⁻¹）。
所以 i 是两支路电流之和，二阶但可解耦，闭式：
  i(t) = 0.24(e^{−500t} − e^{−1000t}) A，t ≥ 0；峰值 60 mA 出现在 t = ln2/500 = 1.386 ms。

验证路线（互相独立）：
  1) 仿真解出换路前稳态（S 断开）→ 0− 值
  2) 替代定理的纯电阻 0+ 网络（L→i_L(0−) 电流源、C→u_C(0−) 电压源）
  3) 仿真解出换路后稳态
  4) 解析闭式（两条一阶支路叠加）
  5) 真 `sw` 开关瞬态，整段曲线与 4) 对账；本文末还跑一遍旧误读拓扑作反例
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


# ---- 1) 换路前稳态：S 断开，但 150Ω+60V 仍接在 m 与地之间 → 非零
m = circ.dcop(pre, ["v(m)", "v(r)", "v(p)", "i(vaml)", "i(vam)"])
vm_pre, vr_pre = m["v(m)"].real, m["v(r)"].real
u0, iL0, i_pre = vm_pre - vr_pre, m["i(vaml)"].real, m["i(vam)"].real
print(f"换路前稳态 : v(m) = {vm_pre:.6f} V   u_C(0−) = {u0:.6f} V   "
      f"i_L(0−) = {iL0:.6f} A（p→m）   i(0−) = {i_pre:.6f} A（m→k）")
print(f"             解析: 60V 经 150Ω 与左 100Ω 分压 → v(m) = 60·100/250 = 24 V；"
      f"i(0−) = (24−60)/150 = −0.24 A；右支路无通路，v(r) = 0")
print(f"             注意: 这不是零状态——S 断开时中支路照样把 60V 源接在 m 上")

# ---- 2) 0+ 电阻网络：L→i_L(0−) 电流源，C→u_C(0−) 电压源
sub = swap_line(post, "L1", f"IL pl m {iL0:.12f}")
sub = swap_line(sub, "C1", f"Vcap m r {u0:.12f}")
m = circ.dcop(sub, ["v(m)", "v(r)", "i(vam)", "i(vcap)", "i(vaml)"])
vm0, vr0, i0, icap, il_sub = (m["v(m)"].real, m["v(r)"].real, m["i(vam)"].real,
                              m["i(vcap)"].real, m["i(vaml)"].real)
kcl = i0 - il_sub + icap
print(f"换路后 0+  : v(m) = {vm0:.6f} V   v(r) = {vr0:.6f} V   "
      f"i(0+) = {i0 * 1e3:.6f} mA   i_C(0+) = {icap * 1e3:.6f} mA")
print(f"             KCL 核对: i − i_L + i_C = {kcl:.2e}（应为 0；i_L(0+)=i_L(0−)）")
print(f"             解析: 电容电压不能跳变 → v(r) 从 0 跳到 −24 V；电感电流不能跳变 → "
      f"i_L = −0.24 A；i(0+) = i_L − i_C = 0")

# ---- 3) 换路后稳态：S 把 m 短接到地，两路储能元件都放到零
m = circ.dcop(post, ["v(m)", "v(r)", "i(vaml)", "i(vam)"])
uinf, iinf, ilinf = (m["v(m)"].real - m["v(r)"].real, m["i(vam)"].real,
                     m["i(vaml)"].real)
print(f"换路后稳态 : v(m) = {m['v(m)'].real:.3e} V   u_C(∞) = {uinf:.3e} V   "
      f"i_L(∞) = {ilinf:.3e} A   i(∞) = {iinf:.3e} A")
print("             解析: S 闭合后 60V 只在局部回路空转，m 接地 → 全部趋于零")

# ---- 4) 解析闭式：闭合后两条一阶支路解耦
lam_c, lam_l = -1.0 / (RR * CB), -RL / LL          # −500、−1000 s⁻¹（过阻尼）
uc_an = lambda t: u0 * np.exp(lam_c * t)
il_an = lambda t: iL0 * np.exp(lam_l * t)
i_an = lambda t: il_an(t) + uc_an(t) / RR          # KCL@m: i = i_L − i_C, i_C = −u_C/R
c0, c1 = u0 / RR, iL0                              # i = c0 e^{λc t} + c1 e^{λl t}
tmin_c = float(np.log(-c1 * lam_l / (c0 * lam_c)) / (lam_c - lam_l))
imin_c = float(i_an(tmin_c))
print(f"解析闭式   : i(t) = {c0:.6f}·e^({lam_c:g}t) + ({c1:.6f})·e^({lam_l:g}t) A"
      f" = 0.24(e^(−500t) − e^(−1000t)) A")
print(f"             τ_C = 100Ω·20µF = 2 ms（极点 {lam_c:g}）  "
      f"τ_L = 0.1H/100Ω = 1 ms（极点 {lam_l:g}）→ 过阻尼")
print(f"             峰值 i = {imin_c * 1e3:.4f} mA @ t = {tmin_c * 1e3:.4f} ms（i'(t)=0 闭式解）")

# ---- 5) 真开关瞬态：S 在 t=0 由断开变闭合（控制极 −5V→+5V）
sw = swap_line(post, "Rsw", circ.switch_lines(post, "k", "0", close=True, edge=1e-8))
d = circ.tran(sw, ["i(vam)", "v(m)", "v(r)", "i(vaml)"], 2e-5, 0.02, tmax=1e-6)
t, ii = d["i(vam)"][0], np.real(d["i(vam)"][1])
uc_s = np.real(d["v(m)"][1]) - np.real(d["v(r)"][1])
il_s = np.real(d["i(vaml)"][1])
sel = t > 1e-4                                    # 开关沿附近不归解析式管
err = float(np.max(np.abs(ii[sel] - i_an(t[sel]))) / 0.24)
err_uc = float(np.max(np.abs(uc_s[sel] - uc_an(t[sel]))) / 24)
err_il = float(np.max(np.abs(il_s[sel] - il_an(t[sel]))) / 0.24)
kmax = int(np.argmax(ii[sel]))
tpk_s, ipk_s = t[sel][kmax], ii[sel][kmax]


def tau_from(t1: float, t2: float, y1: float, y2: float) -> float:
    return -(t2 - t1) / np.log(y2 / y1)


tau_c = tau_from(1e-3, 2e-3, np.interp(1e-3, t, uc_s), np.interp(2e-3, t, uc_s))
tau_l = tau_from(0.5e-3, 1.5e-3, np.interp(0.5e-3, t, -il_s), np.interp(1.5e-3, t, -il_s))
print(f"瞬态实测   : i 全程最大偏差 {err:.2e}（相对 0.24 A）；u_C、i_L 偏差 "
      f"{err_uc:.2e} / {err_il:.2e}")
print(f"峰值核对   : 仿真 max i = {ipk_s * 1e3:.4f} mA @ t = {tpk_s * 1e3:.4f} ms"
      f"（闭式 {imin_c * 1e3:.4f} mA @ {tmin_c * 1e3:.4f} ms）")
print(f"时间常数   : 由实测曲线反解 τ_C = {tau_c * 1e3:.4f} ms（解析 2 ms）、"
      f"τ_L = {tau_l * 1e3:.4f} ms（解析 1 ms）")
print(f"起点/终点  : 首个采样点 t = {t[sel][0] * 1e3:.4f} ms 处 i = {ii[sel][0] * 1e3:.4f} mA"
      f"（解析 {i_an(t[sel][0]) * 1e3:.4f} mA；真正的 0+ 是 0，见 2) 的替代法）"
      f"   i(∞) = {iinf * 1e3:.4f} mA")

# ---- 反例（规则 4）：旧误读把 S 串进 150Ω 支路，确认本判据真能把它踢掉
OLD = """* 反例：S 串在 150Ω+60V 支路里（旧误读）
R100a p 0 100
VamL p pl 0
L1 pl m 0.1
C1 m r 20u
R100b r 0 100
Rsw m d 1e9
Vam d dm 0
R150 dm src 150
V60 src 0 60
.end
"""
old_pre = circ.set_value(OLD, "Rsw", SW_CLOSED)
old_sw = swap_line(old_pre, "Rsw",
                   circ.switch_lines(old_pre, "m", "d", close=True, edge=1e-8))
d_old = circ.tran(old_sw, ["i(vam)"], 2e-5, 0.02, tmax=1e-6)
t_old, old_i = d_old["i(vam)"][0], np.real(d_old["i(vam)"][1])
o_sel = t_old > 1e-4                              # 各自的时间网格，不能套用 sel
old_err = float(np.max(np.abs(old_i[o_sel] - i_an(t_old[o_sel]))))
print(f"反例核对   : 旧（误读）拓扑的 i(t) 与新解最大差 {old_err:.4f} A"
      f"（旧解 i(0+)=i(∞)=−0.24 A，新解为 0 → 0），本判据能区分")

bad = []


def check(name, err, tol):
    if err > tol:
        bad.append((name, err))
    print(f"  [{'OK ' if err <= tol else 'BAD'}] {name:<28} 误差 {err:.2e}（限 {tol:g}）")


check("u_C(0−) 仿真 vs 解析 24 V", abs(u0 - 24) / 24, 1e-3)
check("i_L(0−) 仿真 vs 解析 −0.24 A", abs(iL0 + 0.24) / 0.24, 1e-3)
check("i(0−) 仿真 vs 解析 −0.24 A", abs(i_pre + 0.24) / 0.24, 1e-3)
check("i(0+) 替代法 vs 解析 0", abs(i0) / 0.24, 1e-3)
check("v(r)(0+) 替代法 vs 解析 −24 V", abs(vr0 + 24) / 24, 1e-3)
check("0+ KCL 残差", abs(kcl), 1e-6)
check("稳态 i(∞) ≈ 0", abs(iinf), 1e-4)
check("稳态 u_C(∞) ≈ 0", abs(uinf), 1e-4)
check("i(t) 实测 vs 解析式", err, 2e-3)
check("u_C(t) 实测 vs 解析式", err_uc, 2e-3)
check("i_L(t) 实测 vs 解析式", err_il, 2e-3)
check("峰值幅值 实测 vs 闭式", abs(ipk_s - imin_c) / 0.24, 5e-3)
check("峰值时刻 实测 vs 闭式", abs(tpk_s - tmin_c), 3e-5)      # 采样网格 20 µs
check("τ_C 实测 vs 解析 2 ms", abs(tau_c - RR * CB) / (RR * CB), 2e-2)
check("τ_L 实测 vs 解析 1 ms", abs(tau_l - LL / RL) / (LL / RL), 2e-2)
if old_err < 0.1:
    bad.append(("反例未区分", old_err))
print(f"\nRESULT PASS: t7_7" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
