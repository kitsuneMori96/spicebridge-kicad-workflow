#!/usr/bin/env python3
"""波特图折线近似（答案图）：只画渐近线，不画仿真曲线。

幅频：+20 / 0 / −20 / −40 dB/dec 四段折线
相频：每个极点用三段折线（fp/10 → fp → 10fp，每十倍频 −45°）

用法：
    python3 bode_asym.py circuits/q4_6
    python3 bode_asym.py circuits/q4_6 --png
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bode  # noqa: E402

W, H = 1180, 700
PAGE = "#0b1017"
PANEL = "#111a23"
BORDER = "#25323f"
GRID = "#1d2834"
TEXT = "#e6edf3"
MUTED = "#8fa3b8"
MAIN_C = "#ffb703"
ASYM_C = "#4cc9f0"
MARK_C = "#f472b6"
GOOD = "#22c55e"
FONT = ("WenQuanYi Zen Hei", "Noto Sans CJK SC", "DejaVu Sans")


def fmt_hz(f: float) -> str:
    if f >= 1e6 - 1:
        return f"{f / 1e6:g} MHz"
    if f >= 1e3 - 1:
        return f"{f / 1e3:g} kHz"
    return f"{f:g} Hz"


def mag_asym(f, gain, poles):
    """折线近似：20lg(gain) + 20lg(f) - Σ 20·max(0, lg(f/fp))。"""
    f = np.atleast_1d(np.asarray(f, dtype=float))
    out = []
    for fv in f:
        db = 20 * math.log10(gain) + 20 * math.log10(fv)
        for fp in poles:
            db -= 20 * max(0.0, math.log10(fv / fp))
        out.append(db)
    return np.array(out)


def phase_asym(f, poles):
    """折线近似：每个极点 fp/10→10fp 线性转过 −90°。"""
    f = np.atleast_1d(np.asarray(f, dtype=float))
    out = []
    for fv in f:
        ph = 90.0
        for fp in poles:
            if fv >= 10 * fp:
                ph -= 90.0
            elif fv > fp / 10:
                ph -= 45.0 * math.log10(fv / (fp / 10))
        out.append(ph)
    return np.array(out)


def build(cdir: Path, png: bool = False) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, FixedLocator, LogLocator

    matplotlib.rcParams["font.sans-serif"] = FONT
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["axes.unicode_minus"] = False

    spec = json.loads((cdir / "spec.json").read_text(encoding="utf-8"))
    cfg = spec["bode"]
    tf = cfg["tf"]
    poles = [float(p) for p in tf["poles_hz"]]
    gain = float(tf["gain"])
    nz = int(tf.get("zeros_at_origin", 0))
    f0, f1 = float(cfg["freq"]["start"]), float(cfg["freq"]["stop"])

    plateau = 20 * math.log10(gain * poles[0])
    mag_pts = [f0] + poles + [f1]
    cand = {f0, f1}
    for fp in poles:
        for g in (fp / 10, fp, fp * 10):
            if f0 <= g <= f1:
                cand.add(round(g, 10))
    ph_pts = sorted(cand)
    my_ = mag_asym(mag_pts, gain, poles)
    mpy = phase_asym(ph_pts, poles)

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=PAGE)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.25, 1], left=0.085, right=0.975,
                          top=0.85, bottom=0.10, hspace=0.20)
    ax1 = fig.add_subplot(gs[0], facecolor=PANEL)
    ax2 = fig.add_subplot(gs[1], facecolor=PANEL, sharex=ax1)

    for ax in (ax1, ax2):
        ax.set_xscale("log")
        ax.set_xlim(f0, f1)
        ax.grid(True, which="major", color=GRID, lw=0.9)
        ax.grid(True, which="minor", color=GRID, lw=0.4, alpha=0.4)
        for sp in ax.spines.values():
            sp.set_color(BORDER)
        ax.tick_params(colors=MUTED, labelsize=9.5, which="both")
        for fp in poles:
            ax.axvline(fp, color=MARK_C, ls="--", lw=1.0, alpha=0.55)

    # ---------------- 幅频折线
    ax1.semilogx(mag_pts, my_, color=MAIN_C, lw=2.6, solid_capstyle="round", zorder=3)
    ax1.plot(mag_pts, my_, "o", ms=6, mfc=PAGE, mec=MAIN_C, mew=1.8, zorder=4)

    ax1.set_ylim(-45, 80)
    lo, hi = ax1.get_ylim()
    for i, (fx, fy) in enumerate(zip(mag_pts, my_)):
        dy = 13 if i % 2 == 0 else -23
        ha, dx = "center", 0
        if i == 0:
            ha, dx = "left", 6
        elif i == len(mag_pts) - 1:
            ha, dx = "right", -6
        ax1.annotate(f"{fy:.1f} dB", xy=(fx, fy), xytext=(dx, dy), textcoords="offset points",
                     color=MAIN_C, fontsize=9.5, ha=ha,
                     bbox=dict(boxstyle="round,pad=0.2", fc=PANEL, ec="none", alpha=0.8))

    # 各段斜率
    slopes = [(mag_pts[i], mag_pts[i + 1],
               (my_[i + 1] - my_[i]) / math.log10(mag_pts[i + 1] / mag_pts[i]))
              for i in range(len(mag_pts) - 1)]
    for f_a, f_b, sl in slopes:
        xm = math.sqrt(f_a * f_b)
        ym = float(mag_asym([xm], gain, poles)[0])
        ax1.annotate(f"{sl:+.0f} dB/dec", xy=(xm, ym), xytext=(0, 16 if sl <= 0 else -22),
                     textcoords="offset points", color=ASYM_C, fontsize=10.5, ha="center",
                     bbox=dict(boxstyle="round,pad=0.25", fc=PANEL, ec="none", alpha=0.85))

    # 中频标注
    ax1.annotate(f"中频 {gain * poles[0]:.0f} 倍 = {plateau:.1f} dB",
                 xy=(math.sqrt(poles[0] * poles[1]), plateau), xytext=(0, 30),
                 textcoords="offset points", color=GOOD, fontsize=11, ha="center",
                 bbox=dict(boxstyle="round,pad=0.25", fc=PANEL, ec="none", alpha=0.85))

    ax1.set_ylabel("|A_u| (dB)", color=TEXT, fontsize=11)
    ax1.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax1.xaxis.set_major_locator(LogLocator(base=10, numticks=12))
    ax1.tick_params(labelbottom=False)

    # ---------------- 相频折线
    ax2.semilogx(ph_pts, mpy, color=ASYM_C, lw=2.6, solid_capstyle="round", zorder=3)
    ax2.plot(ph_pts, mpy, "o", ms=6, mfc=PAGE, mec=ASYM_C, mew=1.8, zorder=4)
    ax2.set_ylim(-200, 110)
    keep = {f0, *poles, poles[0] * 10, poles[-1] * 10}
    for fx, fy in zip(ph_pts, mpy):
        if fx not in keep:
            continue
        ha, dx = ("left", 6) if fx == f0 else ("center", 0)
        ax2.annotate(f"{fy:+.0f}°", xy=(fx, fy), xytext=(dx, -23),
                     textcoords="offset points", color=ASYM_C, fontsize=9.5, ha=ha,
                     bbox=dict(boxstyle="round,pad=0.2", fc=PANEL, ec="none", alpha=0.8))

    # 合并同斜率的相邻段（1 Hz→100 Hz 都是 -45°/dec）
    runs, i = [], 0
    while i < len(ph_pts) - 1:
        f_a, sl = ph_pts[i], (mpy[i + 1] - mpy[i]) / math.log10(ph_pts[i + 1] / ph_pts[i])
        j = i + 1
        while j < len(ph_pts) - 1:
            s2 = (mpy[j + 1] - mpy[j]) / math.log10(ph_pts[j + 1] / ph_pts[j])
            if abs(s2 - sl) > 0.5:
                break
            j += 1
        runs.append((f_a, ph_pts[j], sl))
        i = j
    for f_a, f_b, sl in runs:
        if abs(sl) < 1:
            continue
        xm = math.sqrt(f_a * f_b) * 10 ** -0.12
        ym = float(phase_asym([xm], poles)[0])
        ax2.annotate(f"{sl:+.0f}°/dec", xy=(xm, ym), xytext=(0, 21),
                     textcoords="offset points", color=MUTED, fontsize=10, ha="center",
                     bbox=dict(boxstyle="round,pad=0.25", fc=PANEL, ec="none", alpha=0.85))

    ax2.set_ylabel("∠A_u (°)", color=TEXT, fontsize=11)
    ax2.set_xlabel("频率 f (Hz)", color=TEXT, fontsize=11)
    ax2.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))

    # 极点标签（放在幅频面板底部，避开数值标注）
    for fp in poles:
        ax1.annotate(fmt_hz(fp), xy=(fp, ax1.get_ylim()[0]), xytext=(4, 6),
                     textcoords="offset points", color=MARK_C, fontsize=9.5,
                     rotation=90, va="bottom")

    fig.text(0.025, 0.955, "教材 4.6 · 波特图折线近似（答案）", color=TEXT,
             fontsize=19, weight="bold")
    fig.text(0.025, 0.915,
             "A_u(jf) = 200jf / [(1+jf/10)(1+jf/10^4)(1+jf/10^5)]　"
             "零点在原点（+20 dB/dec，+90°），三个极点 10 Hz / 10 kHz / 100 kHz",
             color=MUTED, fontsize=11)
    fig.text(0.025, 0.02,
             "幅频折线在每处转折频率比真实曲线高 3 dB；相位折线在每个 fp/10~10fp 区间线性变化，"
             "转折点处与真实相位最大差 5.7°",
             color=MUTED, fontsize=9)

    outdir = HERE / "out" / cdir.name
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{cdir.name}_bode_asym.svg"
    fig.savefig(target, format="svg", facecolor=PAGE)
    if png:
        fig.savefig(target.with_suffix(".png"), format="png", dpi=160, facecolor=PAGE)

    print("幅频折点：" + "  ".join(f"{fmt_hz(a)}:{b:.1f}dB" for a, b in zip(mag_pts, my_)))
    print("相频折点：" + "  ".join(f"{fmt_hz(a)}:{c:+.0f}°" for a, c in zip(ph_pts, mpy)))
    print(f"wrote {target}")
    return target


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    build(Path(argv[0]), png="--png" in argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
