#!/usr/bin/env python3
"""波特图：ngspice AC 扫频 → 幅频/相频图（SVG + PNG）。

和 `svg_anim.py` / `tran.py` 一样，曲线只用**真实仿真数据**：
脚本自己跑 `.ac` 扫频，再与 spec.json 里写的解析式逐点比对，
对不上就报错退出，绝不用公式直接画图冒充仿真。

用法：
    python3 bode.py circuits/q4_6
    python3 bode.py circuits/q4_6 --png

spec.json 片段：
    "bode": {
      "title": "...", "subtitle": "...",
      "freq": {"start": 0.1, "stop": 1e7, "ppd": 40},   # ppd = 每十倍频点数
      "tf":   {"gain": 200, "zeros_at_origin": 1, "poles_hz": [10, 1e4, 1e5]},
      "out":  "out"
    }

`tf` 只用于交叉验证，不参与绘图。
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
NGSPICE = "ngspice"

W, H = 1180, 660
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
BAD = "#ef4444"

FONT = ("WenQuanYi Zen Hei", "Noto Sans CJK SC", "DejaVu Sans")


# ---------------------------------------------------------------- 仿真
def run_ac(netlist: str, start: float, stop: float, ppd: int, vec: str) -> tuple[np.ndarray, np.ndarray]:
    """跑 `.ac dec` 并把复向量写成 freq/real/imag 三列读回来。"""
    body = re.sub(r"(?im)^\s*\.end\s*$", "", netlist)
    body = re.sub(r"(?im)^\s*\.(tran|ac|op|dc)[^\n]*$", "", body)
    fname = re.sub(r"[^\w]", "_", vec)
    script = (body + f"\n.ac dec {ppd} {start:g} {stop:g}\n.control\nrun\n"
              f"wrdata {fname}.dat {vec}\nquit\n.endc\n.end\n")
    with tempfile.TemporaryDirectory() as td:
        cir = Path(td) / "ac.cir"
        cir.write_text(script, encoding="utf-8")
        r = subprocess.run([NGSPICE, "-b", str(cir)], capture_output=True,
                           text=True, cwd=td)
        raw = Path(td) / f"{fname}.dat"
        if not raw.exists():
            raise SystemExit(f"ngspice 没有输出 {fname}.dat\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
        a = np.loadtxt(raw)
    f = a[:, 0]
    h = a[:, 1] + 1j * a[:, 2]
    return f, h


def analytic(f: np.ndarray, tf: dict) -> np.ndarray:
    """按题给表达式算一遍，仅用于验证仿真结果。"""
    h = np.full(f.shape, float(tf.get("gain", 1.0)), dtype=complex)
    h *= (1j * f) ** int(tf.get("zeros_at_origin", 0))
    for fp in tf.get("poles_hz", []):
        h /= (1 + 1j * f / float(fp))
    return h


# ---------------------------------------------------------------- 辅助
def db(x: np.ndarray) -> np.ndarray:
    return 20 * np.log10(np.maximum(np.abs(x), 1e-30))


def phase_deg(x: np.ndarray) -> np.ndarray:
    return np.degrees(np.unwrap(np.angle(x)))


def at(f: np.ndarray, y: np.ndarray, f0: float) -> float:
    return float(np.interp(f0, f, y))


def fmt_hz(f0: float) -> str:
    for unit, s in (("MHz", 1e6), ("kHz", 1e3)):
        if f0 >= s * 0.999:
            return f"{f0 / s:g} {unit}"
    return f"{f0:g} Hz"


def corner_3db(f: np.ndarray, y: np.ndarray, plateau: float, rising: bool) -> float:
    """在 plateau 附近找 -3 dB 点（rising=True 找下限，否则找上限）。"""
    target = plateau - 3.0103
    idx = int(np.argmax(y))
    if rising:
        seg = slice(0, idx + 1)
        xs, ys = f[seg], y[seg]
        k = int(np.argmin(np.abs(ys - target)))
        return float(xs[k])
    seg = slice(idx, len(f))
    xs, ys = f[seg], y[seg]
    k = int(np.argmin(np.abs(ys - target)))
    return float(xs[k])


# ---------------------------------------------------------------- 绘图
def plot(f, h, cfg, cfgall, out_svg: Path, stats: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter, LogLocator

    matplotlib.rcParams["font.sans-serif"] = FONT
    matplotlib.rcParams["font.family"] = "sans-serif"
    matplotlib.rcParams["axes.unicode_minus"] = False

    mag = db(h)
    ph = phase_deg(h)
    poles = [float(p) for p in cfg.get("tf", {}).get("poles_hz", [])]
    plateau = stats["plateau_db"]

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=PAGE)
    gs = fig.add_gridspec(2, 1, height_ratios=[1.35, 1], left=0.085, right=0.975,
                          top=0.86, bottom=0.135, hspace=0.16)
    ax1 = fig.add_subplot(gs[0], facecolor=PANEL)
    ax2 = fig.add_subplot(gs[1], facecolor=PANEL, sharex=ax1)

    for ax in (ax1, ax2):
        ax.set_xscale("log")
        ax.set_xlim(f[0], f[-1])
        ax.grid(True, which="major", color=GRID, lw=0.9)
        ax.grid(True, which="minor", color=GRID, lw=0.4, alpha=0.45)
        for sp in ax.spines.values():
            sp.set_color(BORDER)
        ax.tick_params(colors=MUTED, labelsize=9.5, which="both")
        for fc in poles:
            ax.axvline(fc, color=MARK_C, ls="--", lw=1.0, alpha=0.55)

    # ---- 幅频：精确曲线 + 渐近线
    ax1.semilogx(f, mag, color=MAIN_C, lw=2.0, label="仿真 |A_u|", zorder=3)

    zero = int(cfg.get("tf", {}).get("zeros_at_origin", 0))
    slope, base_f = 20.0 * zero, f[0]
    base = plateau - slope * math.log10(poles[0] / base_f) if poles else plateau
    xs = [f[0]]
    ys = [base]
    for fp in poles:
        xs.append(fp)
        ys.append(ys[-1] + slope * math.log10(fp / xs[-2]))
        slope -= 20.0
    xs.append(f[-1])
    ys.append(ys[-1] + slope * math.log10(xs[-1] / xs[-2]))
    ax1.semilogx(xs, ys, color=ASYM_C, lw=1.3, ls=(0, (6, 4)),
                 label="渐近线（折线近似）", zorder=2)

    ax1.set_ylim(-20, plateau + 18)
    ax1.set_ylabel("幅值 |A_u| (dB)", color=TEXT, fontsize=11)
    ax1.axhline(plateau, color=GOOD, lw=0.8, ls=":", alpha=0.7)
    tf = cfg.get("tf", {})
    nz = int(tf.get("zeros_at_origin", 0))
    mid_gain = float(tf.get("gain", 1)) * (poles[0] ** nz) if (poles and nz) else stats["plateau_gain"]
    ax1.annotate(f"中频 ≈{mid_gain:.0f} 倍（{plateau:.1f} dB）",
                 xy=(math.sqrt(poles[0] * poles[1]) if len(poles) > 1 else 1e3, plateau),
                 xytext=(0, 8), textcoords="offset points",
                 color=GOOD, fontsize=10.5, ha="center")

    # -3 dB 点
    for fl, tag, dy in ((stats["f_lo"], "下限", 12), (stats["f_hi"], "上限", -20)):
        ax1.plot([fl], [at(f, mag, fl)], "o", ms=5, mfc=PAGE, mec=MARK_C, mew=1.6, zorder=4)
        ax1.annotate(f"{tag} {fmt_hz(fl)}\n{at(f, mag, fl):+.1f} dB", xy=(fl, at(f, mag, fl)),
                     xytext=(0, dy), textcoords="offset points", color=MARK_C,
                     fontsize=9, ha="center")

    for fp in poles:
        if min(abs(fp - stats["f_lo"]) / fp, abs(fp - stats["f_hi"]) / fp) < 0.02:
            continue                      # 该极点已有 -3 dB 标注，不重复贴标签
        ax1.annotate(fmt_hz(fp), xy=(fp, ax1.get_ylim()[0]), xytext=(3, 4),
                     textcoords="offset points", color=MARK_C, fontsize=9,
                     rotation=90, va="bottom")

    # 折线各段斜率
    for i in range(len(xs) - 1):
        f0, f1, y0, y1 = xs[i], xs[i + 1], ys[i], ys[i + 1]
        s = 0.0 if f1 <= f0 else (y1 - y0) / math.log10(f1 / f0)
        if abs(s) < 0.5:
            continue
        ax1.annotate(f"{s:+.0f} dB/dec", xy=(math.sqrt(f0 * f1), (y0 + y1) / 2),
                     xytext=(0, -14 if s > 0 else 10), textcoords="offset points",
                     color=ASYM_C, fontsize=9.5, ha="center", zorder=5,
                     bbox=dict(boxstyle="round,pad=0.25", fc=PANEL, ec="none", alpha=0.75))

    ax1.legend(loc="lower left", facecolor=PANEL, edgecolor=BORDER,
               labelcolor=TEXT, fontsize=9.5, framealpha=0.9)
    ax1.set_title("")

    # ---- 相频
    ax2.semilogx(f, ph, color=ASYM_C, lw=2.0, label="仿真 ∠A_u")
    # 相频折线近似
    import bode_asym as _ba
    ph_pts = sorted({float(f[0]), float(f[-1])} |
                    {round(v, 10) for fp in poles for v in (fp / 10, fp, fp * 10)
                     if f[0] <= v <= f[-1]})
    ax2.semilogx(ph_pts, _ba.phase_asym(ph_pts, poles), color=ASYM_C, lw=1.2,
                 ls=(0, (6, 4)), alpha=0.65, label="相位折线近似")
    ax2.legend(loc="lower left", facecolor=PANEL, edgecolor=BORDER,
               labelcolor=TEXT, fontsize=9, framealpha=0.9)

    ax2.set_ylim(min(-190, ph.min() - 10), max(100, ph.max() + 10))
    ax2.set_ylabel("相位 (度)", color=TEXT, fontsize=11)
    ax2.set_xlabel("频率 f (Hz)", color=TEXT, fontsize=11)
    for y in (90, 45, 0, -45, -90, -135, -180):
        if ax2.get_ylim()[0] <= y <= ax2.get_ylim()[1]:
            ax2.axhline(y, color=GRID, lw=0.7, alpha=0.9)
            ax2.annotate(f"{y:+d}°", xy=(f[0], y), xytext=(2, 2), textcoords="offset points",
                         color=MUTED, fontsize=8.5, va="bottom")
    for f0 in poles:
        ax2.plot([f0], [at(f, ph, f0)], "o", ms=4, mfc=PAGE, mec=MARK_C, mew=1.4, zorder=4)
        ax2.annotate(f"{at(f, ph, f0):+.0f}°", xy=(f0, at(f, ph, f0)), xytext=(7, -13),
                     textcoords="offset points", color=MARK_C, fontsize=9, zorder=5,
                     bbox=dict(boxstyle="round,pad=0.22", fc=PANEL, ec="none", alpha=0.75))

    ax1.xaxis.set_major_locator(LogLocator(base=10, numticks=12))
    ax1.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax1.tick_params(labelbottom=False)

    fig.text(0.025, 0.955, cfg.get("title", "波特图"), color=TEXT, fontsize=19, weight="bold")
    fig.text(0.025, 0.915, cfg.get("subtitle", ""), color=MUTED, fontsize=11.5)
    fig.text(0.025, 0.018,
             f"曲线：ngspice {NGSPICE_VER} 实跑 .ac dec {cfg['freq']['ppd']} "
             f"{cfg['freq']['start']:g}→{cfg['freq']['stop']:g} Hz（{len(f)} 点）"
             f"   校验：与解析式最大偏差 {stats['d_db']:.2e} dB / {stats['d_ph']:.2e}°",
             color=MUTED, fontsize=9)

    fig.savefig(out_svg, format="svg", facecolor=PAGE)
    return fig


def matplotlib_png(fig, path: Path) -> None:
    fig.savefig(path, format="png", dpi=160, facecolor=PAGE)


# ---------------------------------------------------------------- 主流程
def build(cdir: Path, png: bool = False) -> Path:
    cdir = Path(cdir)
    spec = json.loads((cdir / "spec.json").read_text(encoding="utf-8"))
    cfg = spec.get("bode")
    if not cfg:
        raise SystemExit(f"{cdir}/spec.json 里没有 bode 段")

    netlist = (cdir / "netlist.cir").read_text(encoding="utf-8")
    fr = cfg["freq"]
    vec = f"v({cfg.get('out', 'out')})"
    f, h = run_ac(netlist, float(fr["start"]), float(fr["stop"]), int(fr["ppd"]), vec)

    mag, ph = db(h), phase_deg(h)
    ref = analytic(f, cfg.get("tf", {}))
    d_db = float(np.max(np.abs(mag - db(ref))))
    d_ph = float(np.max(np.abs(ph - phase_deg(ref))))
    if d_db > 0.05 or d_ph > 0.5:
        raise SystemExit(f"仿真与解析式不一致：{d_db:.3g} dB / {d_ph:.3g}°")

    idx = int(np.argmax(mag))
    plateau_db = float(mag[idx])
    f_lo = corner_3db(f, mag, plateau_db, rising=True)
    f_hi = corner_3db(f, mag, plateau_db, rising=False)
    stats = dict(plateau_db=plateau_db, plateau_gain=float(abs(h[idx])),
                 f_lo=f_lo, f_hi=f_hi, d_db=d_db, d_ph=d_ph)

    outdir = HERE / "out" / cdir.name
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{cdir.name}_bode.svg"
    fig = plot(f, h, cfg, spec, target, stats)
    if png:
        matplotlib_png(fig, target.with_suffix(".png"))

    # 关键点表（全部取自仿真数据）
    print(f"仿真点数 {len(f)}，扫频 {f[0]:g}–{f[-1]:g} Hz")
    print(f"校验：与题给解析式最大偏差 {d_db:.2e} dB / {d_ph:.2e}°")
    print(f"峰值 |A| {stats['plateau_gain']:.1f}（{plateau_db:.2f} dB），"
          f"-3 dB 带宽 {f_lo:.3g} Hz – {f_hi:.3g} Hz")
    print(f"{'f (Hz)':>12} {'|A| (dB)':>10} {'∠A (°)':>9}")
    for f0 in (0.1, 1, 10, 100, 1e3, 1e4, 1e5, 1e6, 1e7):
        if f[0] <= f0 <= f[-1]:
            print(f"{f0:>12g} {at(f, mag, f0):>10.2f} {at(f, ph, f0):>9.1f}")
    return target


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    target = build(Path(argv[0]), png="--png" in argv)
    print(f"wrote {target}")
    return 0


def _ngspice_version() -> str:
    out = subprocess.run([NGSPICE, "-v"], capture_output=True, text=True).stdout
    m = re.search(r"ngspice-(\d+)", out)
    return m.group(1) if m else "?"


NGSPICE_VER = _ngspice_version()

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
