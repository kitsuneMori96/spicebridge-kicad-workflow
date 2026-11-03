#!/usr/bin/env python3
"""题 7-4 换路瞬间：直流替代 + 状态方程 + 真开关瞬态，三条互不相关的路对账。

    python3 circuits/t7_4/solve.py

题目要 0+ 时刻五个量：u_C(0+)、i_L(0+)，以及 du_C/dt、di_L/dt、di_R/dt。
后三个是导数，教材路子是先解 0+ 的电阻网络（电容换成电压源、电感换成电流源），
再回代 i_C = C·du_C/dt、u_L = L·di_L/dt。这里每一步都由仿真给数：

  1) 换路前稳态（S 闭合，上支路 6Ω 与中支路 6Ω 并联）→ u_C(0−)、i_L(0−)
  2) 换路后 0+ 的电阻网络（S 打开 + 替代定理）→ i_C(0+)、u_L(0+)、i_R(0+)
     du_C/dt = i_C/C   di_L/dt = u_L/L   di_R/dt = −(du_C/dt)/6   （因为 i_R = (12−u_C)/6）
  3) 换路后稳态 → u_C(∞)、i_L(∞)，用来定状态方程的特解
  4) 换路后的状态方程（解析矩阵指数）与 5) 的实测整段曲线逐点对比
  5) 真 `sw` 开关瞬态：沿后用两个窗口做差分斜率、Richardson 外推回 t=0+，
     独立复核 2) 的三个导数

**这是二阶电路**：L 和 C 分处两个节点，既不能合并成一只等效储能元件，也就没有
"τ = Req·C"。极点 −17 ± j8.43（衰减常数 59 ms、振荡周期 0.75 s），所以第 4) 步用
状态方程而不是三要素式——别把 7-3 那套三要素模板照搬过来。

换路方向：题面"开关未动作前电路已达稳态，t=0 时开关 S 打开"，S 在**上支路**（串着
那只 6Ω），打开后总电阻从 6∥6=3 Ω 变成 6 Ω。
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import circ  # noqa: E402

HERE = Path(__file__).resolve().parent
SW_OPEN, SW_CLOSED = 1e9, 1e-6
US, R_MID, R3, L_VAL, C_VAL = 12.0, 6.0, 3.0, 0.1, 1.0 / 24.0

base = (HERE / "netlist.cir").read_text(encoding="utf-8")
pre = circ.set_value(base, "Rsw", SW_CLOSED)      # 换路前 S 闭合
post = circ.set_value(base, "Rsw", SW_OPEN)       # t=0 打开


def swap_line(nl: str, device: str, new: str) -> str:
    """整行替换元件名为 `device` 的那一行（按首字段全等，避免 "R3" 命中 "R6m" 之类）。
    末尾补回换行：splitlines 吞掉了它，直接拼接会把下一个元件粘到 `.end` 上。"""
    return "\n".join(new if ln.split()[:1] == [device] else ln for ln in nl.splitlines()) + "\n"


def expm_at(A: np.ndarray, t: np.ndarray) -> np.ndarray:
    """2×2 状态矩阵的矩阵指数 e^{At}，逐时刻返回 (N,2,2)——用特征分解，不依赖 scipy。"""
    lam, V = np.linalg.eig(A)
    e = np.exp(t[:, None] * lam[None, :])[:, None, :]      # (N,1,2)，第 2 轴对上 V 的列
    return (V[None] * e) @ np.linalg.inv(V)


# ---- 1) 换路前稳态：电容开路、电感短路，上中两支路 6Ω 并联 = 3 Ω
m = circ.dcop(pre, ["v(a)", "v(b)", "i(l1)", "i(vram)"])
u0, iL0 = m["v(a)"].real, m["i(l1)"].real
ir0 = m["i(vram)"].real
print(f"换路前稳态 : u_C(0−) = {u0:.6f} V   i_L(0−) = {iL0:.6f} A   i_R(0−) = {ir0:.6f} A")
print(f"             解析: v(a) = 12×3/(3+3) = {12 * 3 / 6:.4f} V → i_L = 6/3 = 2 A，i_R = 6/6 = 1 A")

# ---- 2) 0+ 电阻网络：S 打开，电容→u_C(0−) 电压源、电感→i_L(0−) 电流源
sub = swap_line(post, "C1", "Vcap a 0 %.10f" % u0)
sub = swap_line(sub, "L1", "Il a b %.10f" % iL0)     # I n+ n− val：电流从 n+ 经源流向 n−
m = circ.dcop(sub, ["v(a)", "v(b)", "i(vcap)", "i(vram)"])
va, vb = m["v(a)"].real, m["v(b)"].real
ic, ir = m["i(vcap)"].real, m["i(vram)"].real
ul = va - vb
d_uc, d_il = ic / C_VAL, ul / L_VAL
d_ir = -d_uc / R_MID
print(f"换路后 0+  : u_C = {va:.6f} V（= 0−，换路定律）  i_L = {iL0:.6f} A（= 0−）")
print(f"             i_C(0+) = {ic:.6f} A   u_L(0+) = {ul:.6f} V   i_R(0+) = {ir:.6f} A")
print(f"             KCL 核对: i_R − i_C − i_L = {ir - ic - iL0:.2e}（应为 0，上支路已断）")
print(f"导数       : du_C/dt = i_C/C = {d_uc:.6f} V/s")
print(f"             di_L/dt = u_L/L = {d_il:.6f} A/s   （v(a)=6 V、v(b)=3×2=6 V ⇒ u_L=0）")
print(f"             di_R/dt = −(du_C/dt)/6 = {d_ir:.6f} A/s")

# ---- 3) 换路后稳态
m = circ.dcop(post, ["v(a)", "v(b)", "i(l1)", "i(vram)"])
uinf, iLinf, irinf = m["v(a)"].real, m["i(l1)"].real, m["i(vram)"].real
print(f"换路后稳态 : u_C(∞) = {uinf:.6f} V  i_L(∞) = {iLinf:.6f} A  i_R(∞) = {irinf:.6f} A")
print(f"             解析: 6 Ω 串 (短路+3 Ω) → v(∞) = 12×3/9 = {12 * 3 / 9:.4f} V，i_L(∞) = 4/3 A")

# ---- 4) 状态方程（换路后，S 打开）：x = [u_C, i_L]
A = np.array([[-1.0 / (R_MID * C_VAL), -1.0 / C_VAL],
              [1.0 / L_VAL, -R3 / L_VAL]])
b = np.array([US / (R_MID * C_VAL), 0.0])
xinf = -np.linalg.solve(A, b)
lam = np.linalg.eigvals(A)
print(f"状态矩阵   : A = {A.tolist()}  极点 {lam[0]:.4f} / {lam[1]:.4f}"
      f"  → 衰减常数 {abs(1 / lam.real[0]) * 1e3:.2f} ms、振荡周期 {2 * np.pi / abs(lam.imag[0]):.4f} s")
print(f"             特解 x(∞) = [{xinf[0]:.6f}, {xinf[1]:.6f}]（与 3) 的仿真稳态对账）")

# ---- 5) 真 `sw` 开关瞬态：控制极 +5V→−5V，直流初始解落在换路前的网络上
switched = swap_line(post, "Rsw", circ.switch_lines(post, "w1", "a", close=False))
d = circ.tran(switched, ["v(a)", "i(l1)", "i(vram)"], 1e-6, 2e-3, tmax=1e-6)
t, vc, il, irv = (d["v(a)"][0], np.real(d["v(a)"][1]),
                  np.real(d["i(l1)"][1]), np.real(d["i(vram)"][1]))
print(f"瞬态起点   : u_C(0−) = {vc[0]:.6f} V  i_L(0−) = {il[0]:.6f} A（开关自己解出，与 1) 对账）")


def slope(y: np.ndarray, T: float) -> float:
    """沿后 [0, T] 窗口的最小二乘斜率；窗口内曲线近似线性，斜率≈ 0+ 导数。"""
    sel = (t > 0) & (t <= T)
    return float(np.polyfit(t[sel], y[sel], 1)[0])


# s(T) = s(0+) + ½·s'(0+)·T ⇒ 用 T 与 2T 两个窗口外推回 0+
rich = lambda y, T: 2 * slope(y, T) - slope(y, 2 * T)        # noqa: E731
m_uc, m_il, m_ir = rich(vc, 2e-5), rich(il, 2e-5), rich(irv, 2e-5)
print(f"瞬态斜率   : du_C/dt = {m_uc:.4f}  di_L/dt = {m_il:.4f}  di_R/dt = {m_ir:.4f}"
      f"   （窗口 20/40 µs Richardson 外推）")

# 整段曲线 vs 矩阵指数解：0.4 s（≈7 个衰减常数）上逐点比
d2 = circ.tran(switched, ["v(a)", "i(l1)"], 1e-4, 0.4)
t2, vc2, il2 = d2["v(a)"][0], np.real(d2["v(a)"][1]), np.real(d2["i(l1)"][1])
x0 = np.array([u0, iL0])
X = xinf + expm_at(A, t2) @ (x0 - xinf)          # (N,2)：第 0 列 u_C、第 1 列 i_L
err_uc = float(np.max(np.abs(vc2 - X[:, 0])) / abs(u0))
err_il = float(np.max(np.abs(il2 - X[:, 1])) / abs(iL0))
print(f"曲线对账   : u_C 偏差 {err_uc:.2e}  i_L 偏差 {err_il:.2e}（0~0.4 s 全程，相对 0− 幅值）")

bad = []
def check(name, err, tol):
    if err > tol:
        bad.append((name, err))
    print(f"  [{'OK ' if err <= tol else 'BAD'}] {name:<24} 误差 {err:.2e}（限 {tol:g}）")

check("u_C(0−) 直流 vs 开关瞬态", abs(vc[0] - u0) / abs(u0), 1e-3)
check("i_L(0−) 直流 vs 开关瞬态", abs(il[0] - iL0) / abs(iL0), 1e-3)
check("稳态 x(∞) 直流 vs 解析", max(abs(uinf - xinf[0]) / abs(xinf[0]),
                                    abs(iLinf - xinf[1]) / abs(xinf[1])), 1e-3)
check("du_C/dt 替代法 vs 瞬态斜率", abs(m_uc - d_uc) / abs(d_uc), 2e-2)
check("di_L/dt 替代法 vs 瞬态斜率", abs(m_il - d_il), 0.05)          # 解析值就是 0，只能用绝对容差
check("di_R/dt 替代法 vs 瞬态斜率", abs(m_ir - d_ir) / abs(d_ir), 2e-2)
check("实测曲线 vs 状态方程解", max(err_uc, err_il), 5e-3)
print("\nRESULT PASS: t7_4" if not bad else f"\nRESULT FAIL: {bad}")
sys.exit(0 if not bad else 1)
