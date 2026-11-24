#!/usr/bin/env python3
"""实验 5.15 一阶 RC 电路响应：τ 测量、微分电路、积分电路，含实验误差模型。

    python3 circuits/lab5_15/lab.py

和仓库其余用例同一条铁律：报告里的每个数和每根曲线都来自真实 ngspice 运行，
解析式只当期望值对账，对不上就 FAIL。

误差模型（都可以关掉做对照）：
  * 函数发生器内阻 Rs = 50 Ω（±10%），与被测 R 串联；
  * 示波器 10× 探头 Rp = 10 MΩ ∥ Cp = 15 pF（Cp 在 10~20 pF 间均匀）；
  * R ±5%、C ±5%（6800 pF）；0.01 µF / 0.1 µF 陶瓷电容按 ±10% 另算；
  * 信号源幅度 ±2%、频率 ±0.5%；
  * 读数模型：8 bit 垂直量化（0.5 V/div）、光标垂直 ±0.1 div、水平 0.1 div；
    τ 按 0.632 判据读，时间轴按 0.1 div 量化。

产物：out/lab5_15/*.png + results.json。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import circ  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
import schemdraw  # noqa: E402
import schemdraw.elements as elm  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = ROOT / "out" / "lab5_15"
OUT.mkdir(parents=True, exist_ok=True)

U0, F0 = 3.0, 1e3
T0 = 1 / F0
TR = 50e-9
R_NOM, C_NOM = 10e3, 6800e-12
RS_NOM, RP_NOM, CP_NOM = 50.0, 10e6, 15e-12
TOL_R, TOL_C = 0.05, 0.05
SEED = 20261124
MC_N = int(os.environ.get("LAB_MC_N", "200"))
COL = dict(src="#7f8c8d", out="#c0392b", alt="#2471a3", ok="#1e8449", warn="#b9770e",
           band="#f4d03f", grid="#d5d8dc")

plt.rcParams.update({
    "font.family": "WenQuanYi Zen Hei",
    "axes.unicode_minus": False,
    "figure.dpi": 150,
    "savefig.dpi": 200,
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.labelsize": 10,
    "legend.fontsize": 8.5,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.color": COL["grid"],
})


def _add_cjk_font():
    fp = Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
    if fp.exists():
        font_manager.fontManager.addfont(str(fp))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(fp)).get_name()


_add_cjk_font()


# ---------------------------------------------------------------- 网表与仿真
def netlist(kind, R, C, *, U=U0, f=F0, Rs=RS_NOM, Rp=RP_NOM, Cp=CP_NOM, tr=TR):
    period = 1 / f
    src = f"PULSE(0 {U:.8g} 0 {tr:.8g} {tr:.8g} {period / 2:.9g} {period:.9g})"
    if kind == "lowpass":
        body = [f"V1 src 0 {src}", f"RS src in {Rs:.8g}",
                f"R1 in out {R:.8g}", f"C1 out 0 {C:.8g}"]
    elif kind == "highpass":
        body = [f"V1 src 0 {src}", f"RS src in {Rs:.8g}",
                f"C1 in out {C:.8g}", f"R1 out 0 {R:.8g}"]
    else:
        raise ValueError(kind)
    body += [f"RP out 0 {Rp:.8g}", f"CP out 0 {Cp:.8g}"]
    return "\n".join(["* 实验5.15 方波激励 + 误差模型"] + body + [".end"])


def simulate(kind, R, C, *, U=U0, f=F0, Rs=RS_NOM, Rp=RP_NOM, Cp=CP_NOM,
             periods=4, dt=None, tmax=None, tr=TR):
    tau = R * C
    tstop = periods / f
    if dt is None:
        dt = min(0.5e-6, tau / 100)
    if tmax is None:
        tmax = min(dt, 1e-7)
    d = circ.tran(netlist(kind, R, C, U=U, f=f, Rs=Rs, Rp=Rp, Cp=Cp, tr=tr),
                  ["v(src)", "v(out)"], dt, tstop, tmax=tmax)
    t = d["v(out)"][0].real
    return t, d["v(src)"][1].real, d["v(out)"][1].real, tau


def tau_eff(R, C, Rs=RS_NOM, Rp=RP_NOM, Cp=CP_NOM):
    return (R + Rs) * Rp / (R + Rs + Rp) * (C + Cp)


def rising_edges(t, u, level):
    s = np.where(u >= level, 1, -1)
    idx = np.where((s[:-1] < 0) & (s[1:] >= 0))[0]
    out = []
    for i in idx:
        du = u[i + 1] - u[i]
        w = (level - u[i]) / du if du else 0.0
        out.append(t[i] + w * (t[i + 1] - t[i]))
    return np.array(out)


def last_edge(t, us, U, T):
    ed = rising_edges(t, us, U / 2)
    ed = ed[ed < t[-1] - 0.55 * T]
    if not len(ed):
        raise SystemExit("找不到可用的上升沿")
    return float(ed[-1])


def _cross_time(t, u, te, thr):
    j = np.where((t > te) & (u >= thr))[0][0]
    return float(t[j - 1] + (thr - u[j - 1]) / (u[j] - u[j - 1]) * (t[j] - t[j - 1]))


def _cross_time_down(t, u, te, thr):
    j = np.where((t > te) & (u <= thr))[0][0]
    return float(t[j - 1] + (thr - u[j - 1]) / (u[j] - u[j - 1]) * (t[j] - t[j - 1]))


def tau_cross(t, uo, U, T, te):
    """理想读数：起始电平取换路前的实际值，终值取源的真实幅值。"""
    h = T / 2
    m0 = (t > te - 0.03 * h) & (t < te - 0.005 * h)
    u0 = float(np.median(uo[m0]))
    thr = u0 + (1 - np.exp(-1)) * (U - u0)
    return _cross_time(t, uo, te, thr) - te, u0


def timebase_for(tau):
    for tb in (1e-6, 2e-6, 5e-6, 10e-6, 20e-6, 50e-6, 100e-6,
               200e-6, 500e-6, 1e-3, 2e-3):
        if 2.0 <= tau / tb <= 6.0:
            return tb
    return tau / 4


def tau_scope(t, uo, U, T, te, tb, *, rng=None, read_div=0.1, vs=0.5):
    """示波器读数：0.632 判据 + 8 bit 垂直量化 + 光标/网格读数误差。"""
    h = T / 2
    lsb = vs / 32
    q = lambda x: float(np.round(x / lsb) * lsb)  # noqa: E731
    m0 = (t > te - 0.03 * h) & (t < te - 0.005 * h)
    m1 = (t > te + 0.85 * h) & (t < te + 0.995 * h)
    u0, ui = q(np.median(uo[m0])), q(np.median(uo[m1]))
    if rng is not None:
        u0 += rng.uniform(-read_div, read_div) * vs
        ui += rng.uniform(-read_div, read_div) * vs
    thr = u0 + (1 - np.exp(-1)) * (ui - u0)
    tau = _cross_time(t, uo, te, thr) - te
    step = read_div * tb
    return float(np.round(tau / step) * step)


# ---------------------------------------------------------------- Monte Carlo
def stats(x):
    x = np.asarray(x, dtype=float)
    return dict(mean=float(x.mean()), std=float(x.std(ddof=1)), min=float(x.min()),
                max=float(x.max()), p2_5=float(np.percentile(x, 2.5)),
                p97_5=float(np.percentile(x, 97.5)),
                rel_std_pct=float(x.std(ddof=1) / x.mean() * 100))


def mc(n, *, tol, read, seed):
    rng = np.random.default_rng(seed)
    taus, taus_read, params, max_rel = [], [], [], 0.0
    for _ in range(n):
        R, C, Rs, Cp, Ua, fa = R_NOM, C_NOM, RS_NOM, CP_NOM, U0, F0
        if tol:
            R *= 1 + rng.uniform(-TOL_R, TOL_R)
            C *= 1 + rng.uniform(-TOL_C, TOL_C)
            Rs = RS_NOM * (1 + rng.uniform(-0.10, 0.10))
            Cp = rng.uniform(10e-12, 20e-12)
            Ua = U0 * (1 + rng.uniform(-0.02, 0.02))
            fa = F0 * (1 + rng.uniform(-0.005, 0.005))
        T = 1 / fa
        t, us, uo, _ = simulate("lowpass", R, C, U=Ua, f=fa, Rs=Rs, Cp=Cp,
                                periods=4, dt=0.5e-6, tmax=0.25e-6)
        te = last_edge(t, us, Ua, T)
        tau_i, _ = tau_cross(t, uo, Ua, T, te)
        ref = tau_eff(R, C, Rs=Rs, Cp=Cp)
        max_rel = max(max_rel, abs(tau_i - ref) / ref)
        taus.append(tau_i)
        if read:
            taus_read.append(tau_scope(t, uo, Ua, T, te, timebase_for(ref), rng=rng))
        params.append(dict(R=R, C=C, Rs=Rs, Cp=Cp, U=Ua, f=fa, tau_eff=ref))
    return dict(tau=stats(taus), tau_read=stats(taus_read) if taus_read else None,
                max_cross_vs_analytic=max_rel, params=params, samples=taus_read)


# ---------------------------------------------------------------- 各工况
def run_case(kind, R, C, periods, dt, tmax):
    t, us, uo, _ = simulate(kind, R, C, periods=periods, dt=dt, tmax=tmax)
    T = T0
    te = last_edge(t, us, U0, T)
    return t, us, uo, te, T


def measure_tau_case(R, C, periods, dt, tmax, rng=None):
    t, us, uo, _ = simulate("lowpass", R, C, periods=periods, dt=dt, tmax=tmax)
    T = T0
    ed = rising_edges(t, us, U0 / 2)
    ed = ed[ed < t[-1] - 0.55 * T]
    te = float(ed[1] if len(ed) > 1 else ed[-1])
    ref = tau_eff(R, C)
    tau_i, u0 = tau_cross(t, uo, U0, T, te)
    tau_rd = tau_scope(t, uo, U0, T, te, timebase_for(ref), rng=rng)
    tf = te - T / 2
    u_hi = float(np.median(uo[(t > tf - 0.03 * T) & (t < tf - 0.005 * T)]))
    u_lo = float(np.median(uo[(t > tf + 0.40 * T) & (t < tf + 0.49 * T)]))
    thr_f = u_lo + np.exp(-1) * (u_hi - u_lo)
    tau_f = _cross_time_down(t, uo, tf, thr_f) - tf
    return dict(t=t, us=us, uo=uo, te=te, tau_eff=ref, tau_cross=tau_i,
                tau_scope=tau_rd, tau_fall=tau_f, u0=u0, u_hi=u_hi, u_lo=u_lo)


def small_change(Rs=(9.1e3, R_NOM, 11e3)):
    """手册①的“少量改变电阻值”：固定 C，观察 τ 与波形的时间轴移动。"""
    rows = []
    for R in Rs:
        d = measure_tau_case(R, C_NOM, 4, 0.5e-6, 1e-7)
        rows.append(dict(R=R, tau=d["tau_eff"], tau_cross=d["tau_cross"],
                         tau_scope=d["tau_scope"],
                         dev_pct=(d["tau_cross"] / d["tau_eff"] - 1) * 100,
                         t=d["t"], us=d["us"], uo=d["uo"], te=d["te"]))
    return rows


def diff_metrics(R, C=0.01e-6):
    tau = tau_eff(R, C)
    periods = int(max(2, min(20, np.ceil(8 * tau / T0) + 2)))
    dt = min(2e-6, max(tau / 100, 5e-8))
    tmax = max(20e-9, min(dt / 2, tau / 50))
    t, us, uo, _ = simulate("highpass", R, C, periods=periods, dt=dt, tmax=tmax)
    T = T0
    te = last_edge(t, us, U0, T)
    h = T / 2
    win = (t >= te - 0.02 * T) & (t <= te + 0.53 * T)
    peak = float(np.max(uo[win]))
    neg = float(np.min(uo[win]))
    at_quarter = float(np.interp(te + T / 4, t, uo))
    m = (t > te) & (t < te + h) & (uo <= 0.1 * peak)
    width = float(t[np.argmax(m)] - te) if m.any() else float("nan")
    u0 = float(np.median(uo[(t > te - 0.02 * T) & (t < te)]))
    peak_ideal = (U0 + u0) * R / (R + RS_NOM)
    return dict(R=R, C=C, tau=tau, ratio=tau / h, peak=peak, neg=neg,
                peak_ideal=peak_ideal, peak_vs_ideal_pct=(peak / peak_ideal - 1) * 100,
                u0=u0, residual_pct=at_quarter / peak * 100, width_to_10pct=width,
                t=t, us=us, uo=uo, te=te)


def int_metrics(R, C=0.1e-6):
    tau = tau_eff(R, C)
    periods = int(max(20, np.ceil(8 * tau / T0)))
    dt = min(5e-6, max(tau / 100, 5e-8))
    t, us, uo, _ = simulate("lowpass", R, C, periods=periods, dt=dt,
                            tmax=min(dt / 2, 1e-7))
    T = T0
    ed = rising_edges(t, us, U0 / 2)
    ed = ed[ed < t[-1] - 2.05 * T]
    te = float(ed[-1])
    h = T / 2
    m = (t >= te) & (t <= te + h)
    seg_t, seg_u = t[m], uo[m]
    umin, umax = float(seg_u[0]), float(seg_u[-1])
    du = umax - umin
    chord = umin + (umax - umin) * (seg_t - seg_t[0]) / (seg_t[-1] - seg_t[0])
    curv = float(np.max(np.abs(seg_u - chord)) / du * 100)
    du_ideal = U0 * T / (4 * tau)
    return dict(R=R, C=C, tau=tau, ratio=tau / h, du=du, du_ideal=du_ideal,
                du_vs_ideal_pct=(du / du_ideal - 1) * 100, curvature_pct=curv,
                end_of_half_pct=umax / U0 * 100,
                t=t, us=us, uo=uo, te=te)


# ---------------------------------------------------------------- 图
def _schematic(kind, path):
    with schemdraw.Drawing(show=False) as d:
        d.config(unit=2.2, fontsize=12)
        V = d.add(elm.SourceSquare().right().label("$u_s(t)$", loc="bottom"))
        if kind == "lowpass":
            R = d.add(elm.Resistor().right().label("$R$"))
            d.add(elm.Dot())
            d.add(elm.Line().right().length(0.7).label("$u_C$", loc="top"))
            C = d.add(elm.Capacitor().down().label("$C$"))
        else:
            C = d.add(elm.Capacitor().right().label("$C$"))
            d.add(elm.Dot())
            d.add(elm.Line().right().length(0.7).label("$u_R$", loc="top"))
            R = d.add(elm.Resistor().down().label("$R$"))
        d.add(elm.Ground())
        d.add(elm.Line().at(C.end).left().tox(V.start[0]))
        d.add(elm.Line().up().toy(V.start[1]))
    d.save(str(path), transparent=False, dpi=220)


def fig_setup():
    p1, p2 = OUT / "_sch_lp.png", OUT / "_sch_hp.png"
    _schematic("lowpass", p1)
    _schematic("highpass", p2)
    fig, axs = plt.subplots(1, 2, figsize=(9.6, 3.3))
    for ax, p, title in ((axs[0], p1, "(a) 低通：R 串联、C 并联"),
                         (axs[1], p2, "(b) 高通：C 串联、R 并联")):
        ax.imshow(plt.imread(p))
        ax.axis("off")
        ax.set_title(title, pad=6)
    fig.text(0.5, -0.01,
             "(a) τ≪T/2：RC 充放电；τ≫T/2：积分电路        "
             "(b) τ≪T/2：微分电路",
             ha="center", fontsize=9.5)
    fig.tight_layout()
    fig.savefig(OUT / "fig1_setup.png", bbox_inches="tight")
    plt.close(fig)
    for p in (p1, p2):
        p.unlink(missing_ok=True)


def fig_tau(d):
    t, us, uo, te = d["t"], d["us"], d["uo"], d["te"]
    tf = te - T0 / 2
    thr_hi = 0.632 * U0
    thr_lo = d["u_lo"] + np.exp(-1) * (d["u_hi"] - d["u_lo"])
    fig = plt.figure(figsize=(10.8, 6.4))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.15, 1.0], height_ratios=[1, 1],
                          hspace=0.32, wspace=0.22)
    axs = fig.add_subplot(gs[0, 0])
    axc = fig.add_subplot(gs[1, 0], sharex=axs)
    axz = fig.add_subplot(gs[:, 1])

    m = t <= 2 * T0
    axs.plot(t[m] * 1e3, us[m], color=COL["alt"], lw=1.7, drawstyle="steps-post")
    axs.set_ylim(-0.15, 3.25)
    axs.set_ylabel("$u_s$ / V")
    axs.set_title("(a) 激励 $u_s$：0/3 V 方波，$f$ = 1 kHz")
    axs.tick_params(labelbottom=False)
    axs.text(1.03, 2.62, "$U_m$ = 3 V", color=COL["alt"], fontsize=9)

    axc.plot(t[m] * 1e3, uo[m], color=COL["out"], lw=2.0)
    axc.axhline(thr_hi, color=COL["alt"], lw=0.9, ls=":", label="0.632 阈值")
    axc.axhline(thr_lo, color=COL["ok"], lw=0.9, ls=":", label="0.368 阈值")
    axc.axvline((te + d["tau_cross"]) * 1e3, color=COL["alt"], lw=1.0, ls="--")
    axc.axvline((tf + d["tau_fall"]) * 1e3, color=COL["ok"], lw=1.0, ls="--")
    axc.axvspan(te * 1e3, (te + 5 * d["tau_eff"]) * 1e3, color=COL["band"], alpha=0.18)
    axc.text(te * 1e3 + 0.03, 2.72, "5τ", color="0.35", fontsize=9)
    axc.text(0.04, 2.52,
             f"0.632: {d['tau_cross'] * 1e6:.1f} µs\n0.368: {d['tau_fall'] * 1e6:.1f} µs",
             fontsize=8.5, va="top",
             bbox=dict(boxstyle="round", fc="white", ec="0.8", alpha=0.9))
    axc.set_xlim(0, 2)
    axc.set_ylim(-0.15, 3.25)
    axc.set_xlabel("t / ms")
    axc.set_ylabel("$u_C$ / V")
    axc.set_title("(b) 响应 $u_C$：充电（零状态）+ 放电（零输入）")
    axc.legend(loc="center right", fontsize=8)

    win = (t >= te - 0.02 * T0) & (t <= te + 6 * d["tau_eff"])
    axz.plot((t[win] - te) * 1e6, uo[win], color=COL["out"], lw=2.0, label="$u_C$ 上升段")
    axz.axhline(thr_hi, color=COL["alt"], lw=0.9, ls=":")
    axz.axvline(d["tau_eff"] * 1e6, color=COL["ok"], lw=1.0, ls="--")
    axz.plot([d["tau_eff"] * 1e6], [thr_hi], "o", color=COL["alt"], ms=5)
    axz.annotate(f"τ = {d['tau_cross'] * 1e6:.2f} µs\n(0.632 判据)",
                 xy=(d["tau_eff"] * 1e6, thr_hi),
                 xytext=(d["tau_eff"] * 1e6 * 1.35, 1.15),
                 arrowprops=dict(arrowstyle="->", color="0.3"), fontsize=8.5)
    axz.axvspan(0, 5 * d["tau_eff"] * 1e6, color=COL["band"], alpha=0.18)
    axz.text(5 * d["tau_eff"] * 1e6 * 0.97, 0.25, "5τ", ha="right", color="0.35", fontsize=9)
    axz.set_xlabel("t - t_edge / µs")
    axz.set_ylabel("$u_C$ / V")
    axz.set_title("(c) 上升沿放大：0.632 判据")
    axz.set_xlim(-5, max(6 * d["tau_eff"] * 1e6, 150))
    axz.set_ylim(-0.1, 3.2)
    axz.legend(loc="lower right")
    fig.savefig(OUT / "fig2_tau.png", bbox_inches="tight")
    plt.close(fig)


def fig_small_R(rows):
    fig, ax = plt.subplots(figsize=(8.8, 3.8))
    for r, col in zip(rows, (COL["src"], COL["out"], COL["alt"])):
        m = (r["t"] >= r["te"] - 0.02 * T0) & (r["t"] <= r["te"] + 6 * r["tau"])
        ax.plot((r["t"][m] - r["te"]) * 1e6, r["uo"][m], color=col, lw=2,
                label=f"R={r['R'] / 1e3:.1f} kΩ，τ={r['tau'] * 1e6:.1f} µs")
        ax.axvline(r["tau"] * 1e6, color=col, lw=1, ls="--")
        ax.plot([r["tau"] * 1e6], [0.632 * U0], "o", color=col, ms=5)
    ax.axhline(0.632 * U0, color="0.4", lw=0.8, ls=":")
    ax.text(4, 0.632 * U0 + 0.08, "0.632$U_s$ = 1.896 V", color="0.35", fontsize=8.5)
    ax.set_xlim(-5, 400)
    ax.set_ylim(-0.1, 3.2)
    ax.set_xlabel("t - t_edge / µs")
    ax.set_ylabel("$u_C$ / V")
    ax.set_title("少量改变 R：波形形状不变，过阈时间随 τ 成比例移动")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(OUT / "fig2b_small_R.png", bbox_inches="tight")
    plt.close(fig)


def fig_c_sweep(rows):
    fig, axs = plt.subplots(3, 1, figsize=(8.8, 7.2), sharex=False)
    for ax, r in zip(axs, rows):
        t, us, uo = r["t"], r["us"], r["uo"]
        t0 = r["te"] - T0
        m = (t >= t0) & (t <= t0 + 2 * T0)
        tt = (t[m] - t0) * 1e3
        ax.plot(tt, us[m], color=COL["src"], lw=1.0, ls="--", label="$u_s$")
        ax.plot(tt, uo[m], color=COL["out"], lw=1.8, label="$u_C$")
        ax.axhline(U0, color="0.6", lw=0.6, ls=":")
        ax.set_ylabel("V")
        ax.set_xlim(0, 2)
        ax.set_ylim(-0.15, 3.3)
        ax.set_title(f"$C$ = {r['C'] * 1e9:.4g} nF，τ = {r['tau'] * 1e6:.0f} µs，"
                     f"τ/(T/2) = {r['ratio']:.2f}，半周期末 $u_C$ = {r['end_voltage']:.2f} V")
        ax.legend(loc="upper right", ncol=2)
    axs[-1].set_xlabel("t / ms（截取稳态两个周期）")
    fig.tight_layout()
    fig.savefig(OUT / "fig3_c_sweep.png", bbox_inches="tight")
    plt.close(fig)


def fig_mc(mc_all, budget):
    fig, axs = plt.subplots(1, 2, figsize=(10.4, 3.8),
                            gridspec_kw=dict(width_ratios=[1.25, 1]))
    ax = axs[0]
    x = np.array(mc_all["samples_us"])
    ax.hist(x, bins=22, color=COL["alt"], alpha=0.65, edgecolor="white")
    for val, lab, col, ls in ((mc_all["tau_theory"] * 1e6, "理论 RC", "0.25", ":"),
                              (mc_all["tau_nominal"] * 1e6, "含寄生标称", COL["ok"], "--"),
                              (mc_all["mean_us"], "MC 均值", COL["out"], "-")):
        ax.axvline(val, color=col, lw=1.2, ls=ls, label=f"{lab} {val:.1f} µs")
    ax.set_xlabel("τ 读数 / µs")
    ax.set_ylabel("频数")
    ax.set_title(f"(a) 容差 + 读数仿真（N = {mc_all['n']}）")
    ax.legend(loc="upper right")
    ax.text(0.03, 0.95,
            f"σ = {mc_all['std_us']:.2f} µs（{mc_all['rel_std_pct']:.1f}%）\n"
            f"95% 区间 {mc_all['p2_5_us']:.1f}–{mc_all['p97_5_us']:.1f} µs",
            transform=ax.transAxes, va="top", fontsize=8.5,
            bbox=dict(boxstyle="round", fc="white", ec="0.7", alpha=0.9))

    ax = axs[1]
    names = [b["name"] for b in budget]
    vals = [b["sigma_pct"] for b in budget]
    cols = [COL["alt"]] * (len(budget) - 2) + [COL["warn"], COL["out"]]
    y = np.arange(len(names))[::-1]
    ax.barh(y, vals, color=cols, height=0.6)
    for yy, vv in zip(y, vals):
        ax.text(vv + 0.08, yy, f"{vv:.2f}%", va="center", fontsize=8.5)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.set_xlabel("对 τ 的标准不确定度贡献（%）")
    ax.set_title("(b) 误差预算（RSS 合成）")
    ax.set_xlim(0, max(vals) * 1.25)
    fig.tight_layout()
    fig.savefig(OUT / "fig4_mc.png", bbox_inches="tight")
    plt.close(fig)


def fig_diff(rows):
    fig, axs = plt.subplots(4, 1, figsize=(9.2, 8.8), sharex=True,
                            gridspec_kw=dict(height_ratios=[0.7, 1, 1, 1]))
    r0 = rows[0]
    m0 = (r0["t"] >= r0["te"] - 0.1 * T0) & (r0["t"] <= r0["te"] + 0.95 * T0)
    axs[0].plot((r0["t"][m0] - r0["te"]) * 1e3, r0["us"][m0],
                color=COL["alt"], lw=1.5, drawstyle="steps-post")
    axs[0].set_ylim(-0.4, 3.4)
    axs[0].set_ylabel("$u_s$ / V")
    axs[0].set_title("(a) 激励 $u_s$：0/3 V 方波（三组响应共用）")
    for ax, r, tag in zip(axs[1:], rows, "bcd"):
        t, uo = r["t"], r["uo"]
        m = (t >= r["te"] - 0.1 * T0) & (t <= r["te"] + 0.95 * T0)
        ax.plot((t[m] - r["te"]) * 1e3, uo[m], color=COL["out"], lw=1.6, label="$u_R$")
        ax.axhline(0, color="0.4", lw=0.6)
        ax.set_title(f"({tag}) $R$ = {r['R'] / 1e3:g} kΩ，τ = {r['tau'] * 1e6:.0f} µs，"
                     f"τ/(T/2) = {r['ratio']:.2f}，平顶中点残余 = {r['residual_pct']:.1f}%")
        ax.set_ylabel("$u_R$ / V")
        ax.set_ylim(-3.5, 3.5)
        ax.legend(loc="upper right")
    axs[-1].set_xlabel("t - t_edge / ms")
    axs[-1].set_xlim(-0.05, 0.98)
    fig.tight_layout()
    fig.savefig(OUT / "fig5_diff.png", bbox_inches="tight")
    plt.close(fig)


def fig_int(rows):
    fig, axs = plt.subplots(2, 1, figsize=(9.2, 6.4), sharex=False)
    for ax, r in zip(axs, rows):
        t, us, uo = r["t"], r["us"], r["uo"]
        m = (t >= r["te"] - 0.05 * T0) & (t <= r["te"] + 1.95 * T0)
        tt = (t[m] - r["te"]) * 1e3
        ax.plot(tt, us[m], color=COL["src"], lw=0.9, ls="--", label="$u_s$")
        ax.plot(tt, uo[m], color=COL["out"], lw=1.8, label="$u_C$")
        mh = (t >= r["te"]) & (t <= r["te"] + T0 / 2)
        seg_t, seg_u = (t[mh] - r["te"]) * 1e3, uo[mh]
        ax.plot(seg_t, seg_u[0] + (seg_u[-1] - seg_u[0]) * seg_t / seg_t[-1],
                color=COL["ok"], lw=0.9, ls=":", label="弦线（线性参考）")
        ax.set_title(f"$R$ = {r['R'] / 1e3:g} kΩ，τ = {r['tau'] * 1e3:.1f} ms，"
                     f"τ/(T/2) = {r['ratio']:.1f}，Δ$u_C$ = {r['du']:.3f} V，"
                     f"曲率误差 = {r['curvature_pct']:.2f}%")
        ax.set_ylabel("V")
        ax.legend(loc="upper right", ncol=3)
    axs[-1].set_xlabel("t - t_edge / ms")
    fig.tight_layout()
    fig.savefig(OUT / "fig6_int.png", bbox_inches="tight")
    plt.close(fig)


def fig_criteria(diff_rows, int_rows):
    fig, axs = plt.subplots(1, 2, figsize=(10.4, 3.6))
    ax = axs[0]
    x = np.array([r["ratio"] for r in diff_rows])
    y = np.array([r["residual_pct"] for r in diff_rows])
    ax.semilogx(x, y, "o-", color=COL["out"], lw=1.5, ms=4)
    ax.axvline(0.1, color=COL["ok"], ls="--", lw=1.0)
    ax.axhline(10, color=COL["alt"], ls=":", lw=1.0)
    ax.text(0.1 * 1.05, 60, "τ/T = 0.1", color=COL["ok"], fontsize=8.5)
    ax.text(0.012, 11.5, "残余 10%", color=COL["alt"], fontsize=8.5)
    ax.set_xlabel("τ / T")
    ax.set_ylabel("平顶中点残余 / 峰值（%）")
    ax.set_title("(a) 微分条件：残余越小越像微分")

    ax = axs[1]
    x = np.array([r["ratio"] for r in int_rows])
    y = np.array([r["curvature_pct"] for r in int_rows])
    ax.loglog(x, y, "s-", color=COL["alt"], lw=1.5, ms=4)
    ax.axvline(10, color=COL["ok"], ls="--", lw=1.0)
    ax.text(10 * 1.08, 2.0, "τ/(T/2) = 10\n（τ = 5T）", color=COL["ok"], fontsize=8.5)
    ax.set_xlabel("τ / (T/2)")
    ax.set_ylabel("弦线曲率误差（%）")
    ax.set_title("(b) 积分条件：曲率误差越小线性越好")
    fig.tight_layout()
    fig.savefig(OUT / "fig7_criteria.png", bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------- 主流程
def main():
    print("== 实验 5.15：一阶 RC（ngspice 真实仿真 + 误差模型） ==")

    base = measure_tau_case(R_NOM, C_NOM, 4, 0.5e-6, 1e-7, rng=np.random.default_rng(1))
    print(f"标称 τ(RC)      = {R_NOM * C_NOM * 1e6:.3f} µs")
    print(f"含寄生 τ_eff    = {base['tau_eff'] * 1e6:.3f} µs  (Rs, Cp 已计入)")
    print(f"仿真 0.632 判据 = {base['tau_cross'] * 1e6:.3f} µs  "
          f"(偏差 {(base['tau_cross'] / base['tau_eff'] - 1) * 100:+.2f}%)")
    print(f"仿真 0.368 判据 = {base['tau_fall'] * 1e6:.3f} µs  "
          f"(偏差 {(base['tau_fall'] / base['tau_eff'] - 1) * 100:+.2f}%)")
    print(f"单次示波器读数  = {base['tau_scope'] * 1e6:.1f} µs  "
          f"(时基 {timebase_for(base['tau_eff']) * 1e6:.0f} µs/div)")

    mc_comp = mc(MC_N, tol=True, read=False, seed=SEED)
    mc_read = mc(MC_N, tol=False, read=True, seed=SEED + 1)
    mc_all = mc(MC_N, tol=True, read=True, seed=SEED + 2)
    sig_comp = mc_comp["tau"]["rel_std_pct"]
    sig_read = mc_read["tau_read"]["rel_std_pct"]
    sig_all = mc_all["tau_read"]["rel_std_pct"]
    sig_rss = float(np.hypot(sig_comp, sig_read))
    print(f"MC 器件容差     : {mc_comp['tau']['mean'] / 1e-6:.2f} ± "
          f"{mc_comp['tau']['std'] / 1e-6:.2f} µs (σ {sig_comp:.2f}%), "
          f"95% {mc_comp['tau']['p2_5'] / 1e-6:.1f}–{mc_comp['tau']['p97_5'] / 1e-6:.1f}")
    print(f"MC 仅读数       : σ {sig_read:.2f}%")
    print(f"MC 合计         : {mc_all['tau_read']['mean'] / 1e-6:.2f} ± "
          f"{mc_all['tau_read']['std'] / 1e-6:.2f} µs (σ {sig_all:.2f}%), "
          f"95% {mc_all['tau_read']['p2_5'] / 1e-6:.1f}–{mc_all['tau_read']['p97_5'] / 1e-6:.1f}")
    print(f"RSS 合成        : {sig_rss:.2f}%  vs MC 合计 {sig_all:.2f}%")

    c_rows = []
    for c, per, dt, tm in ((C_NOM, 4, 0.5e-6, 1e-7),
                           (0.01e-6, 4, 0.5e-6, 1e-7),
                           (0.1e-6, 8, 5e-6, 1e-6)):
        t, us, uo, _ = simulate("lowpass", R_NOM, c, periods=per, dt=dt, tmax=tm)
        te = last_edge(t, us, U0, T0)
        end = float(np.interp(te + T0 / 2, t, uo))
        c_rows.append(dict(C=c, tau=tau_eff(R_NOM, c), ratio=tau_eff(R_NOM, c) / (T0 / 2),
                           end_voltage=end, t=t, us=us, uo=uo, te=te))
        print(f"C={c * 1e12:7.0f} pF  τ={tau_eff(R_NOM, c) * 1e6:8.1f} µs  "
              f"τ/(T/2)={c_rows[-1]['ratio']:5.2f}  半周期末 u_C={end:.3f} V")

    small_rows = small_change()
    for r in small_rows:
        print(f"微调 R={r['R'] / 1e3:5.1f} kΩ  τ_eff={r['tau'] * 1e6:7.2f} µs  "
              f"过阈={r['tau_cross'] * 1e6:7.2f} µs (偏差 {r['dev_pct']:+.2f}%)  "
              f"单次读数={r['tau_scope'] * 1e6:5.1f} µs")

    diff_rows = [diff_metrics(R) for R in (1e3, 10e3, 100e3)]
    for r in diff_rows:
        print(f"微分 R={r['R'] / 1e3:5.0f} kΩ  τ={r['tau'] * 1e6:7.1f} µs  "
              f"τ/(T/2)={r['ratio']:5.2f}  峰值 +{r['peak']:.3f}/{r['neg']:.3f} V  "
              f"平顶中点残余={r['residual_pct']:5.1f}%  "
              f"10%宽度={r['width_to_10pct'] * 1e6 if np.isfinite(r['width_to_10pct']) else float('nan'):6.1f} µs")

    int_rows = [int_metrics(R) for R in (10e3, 30e3)]
    for r in int_rows:
        print(f"积分 R={r['R'] / 1e3:5.0f} kΩ  τ={r['tau'] * 1e3:6.2f} ms  "
              f"τ/(T/2)={r['ratio']:5.1f}  ΔU={r['du']:.4f} V "
              f"(理想 {r['du_ideal']:.4f}, {r['du_vs_ideal_pct']:+.1f}%)  "
              f"曲率误差={r['curvature_pct']:.2f}%")

    diff_sweep = [diff_metrics(R) for R in (330, 1e3, 3.3e3, 10e3, 33e3, 100e3, 330e3)]
    int_sweep = [int_metrics(R) for R in (1e3, 3e3, 10e3, 30e3, 100e3)]

    fig_setup()
    fig_tau(base)
    fig_small_R(small_rows)
    fig_c_sweep(c_rows)
    fig_diff(diff_rows)
    fig_int(int_rows)
    fig_criteria(diff_sweep, int_sweep)

    budget = [
        dict(name="R ±5%", sigma_pct=float(TOL_R / np.sqrt(3) * R_NOM / (R_NOM + RS_NOM) * 100)),
        dict(name="C ±5%", sigma_pct=float(TOL_C / np.sqrt(3) * C_NOM / (C_NOM + CP_NOM) * 100)),
        dict(name="源内阻 ±5 Ω", sigma_pct=float(5 / np.sqrt(3) / (R_NOM + RS_NOM) * 100)),
        dict(name="探头电容 ±5 pF", sigma_pct=float(5e-12 / np.sqrt(3) / (C_NOM + CP_NOM) * 100)),
        dict(name="示波器读数", sigma_pct=float(sig_read)),
        dict(name="RSS 合计", sigma_pct=sig_rss),
    ]

    mc_all_out = dict(
        n=MC_N, tau_theory=R_NOM * C_NOM, tau_nominal=base["tau_eff"],
        mean_us=mc_all["tau_read"]["mean"] * 1e6, std_us=mc_all["tau_read"]["std"] * 1e6,
        rel_std_pct=sig_all, p2_5_us=mc_all["tau_read"]["p2_5"] * 1e6,
        p97_5_us=mc_all["tau_read"]["p97_5"] * 1e6,
        samples_us=(np.array(mc_all["samples"], dtype=float) * 1e6).tolist(),
    )

    fig_mc(mc_all_out, budget)

    checks = []
    def chk(name, err, tol):
        checks.append(dict(name=name, value=float(err), tol=float(tol),
                           ok=bool(np.isfinite(err) and err <= tol)))

    chk("标称 τ 仿真 vs 解析", abs(base["tau_cross"] / base["tau_eff"] - 1), 5e-3)
    chk("0.632 vs 0.368 判据", abs(base["tau_fall"] / base["tau_cross"] - 1), 5e-3)
    chk("MC 单次 τ vs 解析（最大）", mc_comp["max_cross_vs_analytic"], 5e-3)
    chk("RSS 合成 vs 合计 MC", abs(sig_rss - sig_all) / sig_all, 0.15)
    chk("积分 ΔU vs tanh 解析（10k）",
        abs(int_rows[0]["du"] / (U0 * np.tanh(T0 / (4 * int_rows[0]["tau"]))) - 1), 1e-2)
    chk("积分 ΔU vs tanh 解析（30k）",
        abs(int_rows[1]["du"] / (U0 * np.tanh(T0 / (4 * int_rows[1]["tau"]))) - 1), 1e-2)
    chk("积分 ΔU 随 R 单调下降", 0.0 if int_rows[1]["du"] < int_rows[0]["du"] else 1.0, 0.0)
    chk("微分平顶残余随 R 单调上升",
        0.0 if diff_rows[2]["residual_pct"] > diff_rows[0]["residual_pct"] else 1.0, 0.0)
    chk("微分峰值 vs 分压解析（1k）",
        abs(diff_rows[0]["peak"] / diff_rows[0]["peak_ideal"] - 1), 1e-2)
    chk("微分峰值 vs 分压解析（10k）",
        abs(diff_rows[1]["peak"] / diff_rows[1]["peak_ideal"] - 1), 1e-2)
    chk("C 增大后半周期末 u_C 单调下降",
        0.0 if c_rows[0]["end_voltage"] > c_rows[1]["end_voltage"] > c_rows[2]["end_voltage"] else 1.0, 0.0)
    chk("少量改变 R：τ 随 R 单调上升",
        0.0 if small_rows[0]["tau"] < small_rows[1]["tau"] < small_rows[2]["tau"] else 1.0, 0.0)
    chk("少量改变 R：过阈 vs 解析（最大）",
        max(abs(r["tau_cross"] / r["tau"] - 1) for r in small_rows), 5e-3)

    res = dict(
        meta=dict(note="所有数值来自 ngspice-36 真实运行；解析式只做对账",
                  seed=SEED, mc_n=MC_N, f_hz=F0, U_v=U0,
                  R_ohm=R_NOM, C_f=C_NOM, Rs_ohm=RS_NOM, Rp_ohm=RP_NOM, Cp_f=CP_NOM),
        nominal=dict(tau_theory_us=R_NOM * C_NOM * 1e6,
                     tau_eff_us=base["tau_eff"] * 1e6,
                     tau_cross_us=base["tau_cross"] * 1e6,
                     tau_fall_us=base["tau_fall"] * 1e6,
                     u_hi_v=base["u_hi"], u_lo_v=base["u_lo"],
                     tau_scope_us=base["tau_scope"] * 1e6,
                     timebase_us=timebase_for(base["tau_eff"]) * 1e6,
                     u0_v=base["u0"]),
        mc=dict(comp=mc_comp["tau"], read=mc_read["tau_read"], total=mc_all["tau_read"],
                sigma_comp_pct=sig_comp, sigma_read_pct=sig_read,
                sigma_total_pct=sig_all, sigma_rss_pct=sig_rss,
                max_cross_vs_analytic=mc_comp["max_cross_vs_analytic"]),
        budget=budget,
        c_sweep=[{k: v for k, v in r.items() if k not in ("t", "us", "uo")} for r in c_rows],
        small_change=[{k: v for k, v in r.items() if k not in ("t", "us", "uo")} for r in small_rows],
        diff=[{k: v for k, v in r.items() if k not in ("t", "us", "uo")}
              for r in diff_rows + diff_sweep],
        integral=[{k: v for k, v in r.items() if k not in ("t", "us", "uo")}
                  for r in int_rows + int_sweep],
        checks=checks,
    )
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=2))

    ok = all(c["ok"] for c in checks)
    print("\n== 对账 ==")
    for c in checks:
        print(f"  [{'OK ' if c['ok'] else 'FAIL'}] {c['name']}: {c['value']:.3e} (限 {c['tol']:g})")
    print(f"\nRESULT {'PASS' if ok else 'FAIL'}: lab5_15 -> {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())