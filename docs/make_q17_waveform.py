#!/usr/bin/env python3
"""生成 q17 的静态对比图：25 mV 未削顶 vs 50 mV 正半周削顶。

数据来自真实 ngspice 瞬态（tran.waveform，自动稳态）。
    python3 docs/make_q17_waveform.py
"""
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import tran  # noqa: E402

# 中文字体：本机 WenQuanYi Zen Hei；没有时退回 Noto/DejaVu（只影响字形，不影响数据）
matplotlib.rcParams["font.sans-serif"] = [
    "WenQuanYi Zen Hei", "Noto Sans CJK SC", "Droid Sans Fallback", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

net = tran.ensure_title((ROOT / "circuits/q17/netlist.cir").read_text())
fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)

for ax, amp, title in ((axes[0], 0.025, "u_i = 25 mV 峰值：未削顶"),
                       (axes[1], 0.050, "u_i = 50 mV 峰值：正半周削顶")):
    w = tran.waveform(net, ["v(out)"], 1000, amp=amp, source="V1",
                      cycles=20, window_cycles=3, settle_vector="v(out)")
    t, v = w["t"] * 1e6, w["y"]["v(out)"]
    ax.plot(t, v, lw=1.4, color="#1f77b4")
    ax.axhline(3.0, ls="--", lw=1, color="#d62728")
    ax.axhline(-5.3, ls="--", lw=1, color="#d62728")
    ax.set_title(f"{title}\n峰值 +{v.max():.2f} V / {v.min():.2f} V", fontsize=11)
    ax.set_xlabel("t (µs)")
    ax.grid(alpha=.3)

axes[0].set_ylabel("u_o (V)")
fig.suptitle("共射放大器输出摆幅：电流截止限制 +3 V，饱和限制 -5.3 V", fontsize=12)
fig.tight_layout()
out = Path(__file__).with_name("q17_waveform.png")
fig.savefig(out, dpi=150)
print(f"wrote {out}")
