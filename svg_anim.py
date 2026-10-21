#!/usr/bin/env python3
"""把瞬态仿真结果渲染成自包含的 SVG 动画（纯 SMIL，无 JS、无外部依赖）。

为什么用 SMIL 而不是 JS：
  - GitHub README 用 <img> 引用 SVG，脚本会被挡掉，SMIL/CSS 动画仍会播放；
  - 单文件离线可播，不需要任何前端运行时；
  - 不支持 SMIL 的渲染器（cairosvg、打印）会看到静态全波形，不会白图。

画面：
  - 左：u_o(t) 主波形 + 教材口径的削顶墙（+3 V / −5.3 V），下方 u_i(t) 小条
  - 右：增量轨迹 Δi_C – Δu_CE，实测出来的就是交流负载线，斜率 −1/(Rc∥RL)
  - 顶部：跟随播放头的瞬时读数；底部：进度条

用法：
    python3 svg_anim.py circuits/q17                 # 读 spec.json 的 anim 段
    python3 svg_anim.py circuits/q17 --png           # 顺带出一张静态预览 PNG
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tran  # noqa: E402

# ---------------------------------------------------------------- 配色 / 版面
W, H = 1000, 520
BG = "#0b1017"
PANEL = "#111a23"
GRID = "#1d2834"
BORDER = "#25323f"
TEXT = "#e6edf3"
MUTED = "#8fa3b8"
MAIN_C = "#ffb703"
IN_C = "#4cc9f0"
LOCUS_C = "#7ee787"
LIMIT_C = "#ff6b6b"
FONT = ("ui-sans-serif, -apple-system, 'Segoe UI', 'Noto Sans CJK SC', "
        "'WenQuanYi Zen Hei', 'Microsoft YaHei', sans-serif")
MONO = ("ui-monospace, 'JetBrains Mono', 'DejaVu Sans Mono', Consolas, monospace")
CJK_PREVIEW = "'WenQuanYi Zen Hei', 'Noto Sans CJK SC', sans-serif"

DUR = 3.4          # 一个播放循环的秒数
DOTS = 90          # 平面轨迹采样点数（≈27 fps）
READOUTS = 18      # 顶部读数刷新次数


# ---------------------------------------------------------------- 小工具
def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    span = hi - lo
    if span <= 0:
        span = abs(hi) or 1.0
    raw = span / max(1, n)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        step = m * mag
        if step >= raw:
            break
    start = math.ceil(lo / step - 1e-9) * step
    out, v = [], start
    while v <= hi + step * 1e-6:
        out.append(round(v, 10))
        v += step
    return out


def _num(v: float) -> str:
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def _num2(v: float, nd: int = 2) -> str:
    return f"{v:.{nd}f}".rstrip("0").rstrip(".") or "0"


def _time_unit(window_s: float) -> tuple[str, float]:
    if window_s < 1e-3:
        return "µs", 1e6
    if window_s < 1:
        return "ms", 1e3
    return "s", 1.0


def _path(xs, ys, xmap, ymap) -> str:
    return "M " + " L ".join(f"{_num(xmap(x))},{_num(ymap(y))}" for x, y in zip(xs, ys))


def _path_len(xs, ys, xmap, ymap) -> float:
    px = [xmap(x) for x in xs]
    py = [ymap(y) for y in ys]
    return float(np.sum(np.hypot(np.diff(px), np.diff(py)))) or 1.0


def _reveal(length: float) -> str:
    """描边绘出；不支持 SMIL 时停在完整长度（可见）。"""
    return (f' stroke-dasharray="{length:.0f}" stroke-dashoffset="0">'
            f'<animate attributeName="stroke-dashoffset" values="{length:.0f};0" '
            f'dur="{DUR}s" repeatCount="indefinite" calcMode="linear"/>')


def _slice_anim(t0: float, t1: float, dur: float = DUR) -> str:
    """只在 [t0,t1] 可见（discrete 步进，没有淡入淡出）。"""
    return (f'<animate attributeName="opacity" values="0;1;0" '
            f'keyTimes="0;{t0:.4f};{t1:.4f}" calcMode="discrete" '
            f'dur="{dur}s" repeatCount="indefinite"/>')


# ---------------------------------------------------------------- 数据
def collect(netlist: str, cfg: dict) -> dict:
    """跑一次瞬态，返回动画需要的所有数组。"""
    traces = cfg["traces"]
    plane = cfg.get("plane")
    if plane and plane.get("probe"):
        # 集电极串 0 V 电流表，得到真正的 i_c(t)（@q1[ic] 在瞬态里是常数）
        netlist, _ = tran.add_current_probe(netlist, plane["probe"]["q"],
                                            plane["probe"].get("source", "VIC"))
    exprs = [t["expr"] for t in traces]
    if plane:
        exprs += _expr_atoms(plane["x_expr"]) + _expr_atoms(plane["y_expr"])
    vectors = list(dict.fromkeys(exprs))

    wf = tran.waveform(
        netlist, vectors, cfg.get("freq", 1000),
        amp=cfg.get("amp"), source=cfg.get("source"),
        cycles=cfg.get("cycles", 20),
        window_cycles=cfg.get("window_cycles", 3),
        points_per_cycle=cfg.get("points_per_cycle", 400),
        settle_vector=cfg.get("settle_vector", traces[0]["expr"]),
    )
    out = {"t": wf["t"], "traces": [], "settled": wf["settled"], "attempts": wf["attempts"]}
    for tr in traces:
        out["traces"].append({**tr, "y": wf["y"][tr["expr"]]})
    if plane:
        alias, env = {}, {}
        for i, e in enumerate(vectors):
            alias[e] = f"a{i}"
            env[f"a{i}"] = wf["y"].get(e, np.zeros_like(wf["t"]))
        px = _eval(plane["x_expr"], alias, env)
        py = _eval(plane["y_expr"], alias, env)
        if plane.get("center", True):        # 增量轨迹：以静态工作点为原点
            px, py = px - px.mean(), py - py.mean()
        out["plane"] = {"x": px, "y": py, **{k: v for k, v in plane.items()
                                             if k in ("xlabel", "ylabel")}}
    return out


def _expr_atoms(expr: str) -> list[str]:
    import re
    return re.findall(r"[vi]\([^)]+\)", expr)


def _eval(expr: str, alias: dict[str, str], env: dict[str, np.ndarray]) -> np.ndarray:
    """向量表达式换名后求值（表达式来自本地 spec，不接受外部输入）。"""
    py = expr
    for atom, name in sorted(alias.items(), key=lambda kv: -len(kv[0])):
        py = py.replace(atom, name)
    py = py.replace("^", "**")
    return eval(py, {"__builtins__": {}}, {**env, "np": np})  # noqa: S307


# ---------------------------------------------------------------- 渲染
def render(data: dict, cfg: dict) -> str:
    t = data["t"]
    traces = data["traces"]
    main = next((x for x in traces if x.get("panel") != "input"), traces[0])
    sub = next((x for x in traces if x.get("panel") == "input"), None)
    plane = data.get("plane")
    limits = cfg.get("limits", [])

    unit, tscale = _time_unit(float(t[-1]))
    ta = t * tscale

    if plane:
        mx0, mx1 = 78, 648
        px0, px1 = 700, 972
    else:
        mx0, mx1 = 78, 972
        px0 = px1 = 0
    my0, my1 = 106, 348
    sy0, sy1 = 380, 426

    vals = [float(main["y"].min()), float(main["y"].max())] + [lim["value"] for lim in limits]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or 0.5
    lo, hi = lo - pad, hi + pad

    def mx(v):            # v: 时间（秒）
        return mx0 + (mx1 - mx0) * (v * tscale) / (ta[-1] or 1.0)

    def my(v):
        return my1 - (my1 - my0) * (v - lo) / (hi - lo)

    def sy(v):
        a = max(abs(float(sub["y"].min())), abs(float(sub["y"].max()))) * 1.25 or 1.0
        return (sy0 + sy1) / 2 - (sy1 - sy0) / 2 * v / a

    s: list[str] = []
    add = s.append
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" '
        f'aria-label="电路仿真波形动画">')
    add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

    # ---- 标题 + 右上角读数
    add(f'<text x="28" y="40" fill="{TEXT}" font-size="21" font-weight="600">'
        f'{cfg.get("title", "仿真波形动画")}</text>')
    add(f'<text x="28" y="64" fill="{MUTED}" font-size="12.5">'
        f'{cfg.get("subtitle", "")}</text>')

    dec = {id(tr): tr.get("digits", 2) for tr in traces}
    for i in range(READOUTS):
        k = int((i + 0.5) / READOUTS * (len(t) - 1))
        parts = []
        for tr in traces:
            val = tr["y"][k] * tr.get("scale", 1.0)
            parts.append(f'{tr.get("label", tr["expr"])} = '
                         f'{val:+.{dec[id(tr)]}f} {tr.get("unit", "V")}')
        line = f"t = {ta[k]:6.2f} {unit}   " + "   ".join(parts)
        add(f'<text x="{W - 28}" y="40" fill="{TEXT}" font-size="13" font-family="{MONO}" '
            f'text-anchor="end" opacity="0">{line}{_slice_anim(i / READOUTS, (i + 1) / READOUTS)}'
            f'</text>')

    # ---- 主面板
    add(f'<rect x="{mx0}" y="{my0}" width="{mx1 - mx0}" height="{my1 - my0}" '
        f'rx="6" fill="{PANEL}" stroke="{BORDER}"/>')
    add(f'<text x="{mx0}" y="{my0 - 10}" fill="{MUTED}" font-size="12">'
        f'{main.get("label", "u_o")} ({main.get("unit", "V")})</text>')

    for v in _nice_ticks(lo, hi, 5):
        y = my(v)
        add(f'<line x1="{mx0}" y1="{y:.1f}" x2="{mx1}" y2="{y:.1f}" stroke="{GRID}"/>')
        add(f'<text x="{mx0 - 8}" y="{y + 4:.1f}" fill="{MUTED}" font-size="11" '
            f'text-anchor="end">{_num2(v, 1)}</text>')

    for tick in _nice_ticks(0, ta[-1], 4):
        x = mx0 + (mx1 - mx0) * tick / (ta[-1] or 1.0)
        add(f'<line x1="{x:.1f}" y1="{my0}" x2="{x:.1f}" y2="{sy1}" stroke="{GRID}" '
            f'stroke-dasharray="2 4"/>')

    for lim in limits:
        y = my(lim["value"])
        add(f'<line x1="{mx0}" y1="{y:.1f}" x2="{mx1}" y2="{y:.1f}" '
            f'stroke="{LIMIT_C}" stroke-width="1.2" stroke-dasharray="6 5" opacity=".85"/>')
        add(f'<text x="{mx1 - 6}" y="{y - 6:.1f}" fill="{LIMIT_C}" font-size="11" '
            f'text-anchor="end">{lim["label"]}</text>')

    d_main = _path(t, main["y"], mx, my)
    add(f'<path d="{d_main}" fill="none" stroke="{main.get("color", MAIN_C)}" '
        f'stroke-width="2.2" stroke-linecap="round" opacity=".20"/>')
    add(f'<path d="{d_main}" fill="none" stroke="{main.get("color", MAIN_C)}" '
        f'stroke-width="2.2" stroke-linecap="round" '
        f'{_reveal(_path_len(t, main["y"], mx, my))}</path>')

    # ---- 输入小条
    if sub:
        add(f'<rect x="{mx0}" y="{sy0}" width="{mx1 - mx0}" height="{sy1 - sy0}" '
            f'rx="6" fill="{PANEL}" stroke="{BORDER}"/>')
        add(f'<text x="{mx0}" y="{sy0 - 8}" fill="{MUTED}" font-size="12">'
            f'{sub.get("label", "u_i")} ({sub.get("unit", "mV")})'
            f'{"  ·  " + sub["note"] if sub.get("note") else ""}</text>')
        add(f'<line x1="{mx0}" y1="{(sy0 + sy1) / 2:.1f}" x2="{mx1}" '
            f'y2="{(sy0 + sy1) / 2:.1f}" stroke="{GRID}"/>')
        d_sub = _path(t, sub["y"], mx, sy)
        add(f'<path d="{d_sub}" fill="none" stroke="{sub.get("color", IN_C)}" '
            f'stroke-width="1.8" stroke-linecap="round" opacity=".20"/>')
        add(f'<path d="{d_sub}" fill="none" stroke="{sub.get("color", IN_C)}" '
            f'stroke-width="1.8" stroke-linecap="round" '
            f'{_reveal(_path_len(t, sub["y"], mx, sy))}</path>')

    # 时间轴
    for tick in _nice_ticks(0, ta[-1], 4):
        x = mx0 + (mx1 - mx0) * tick / (ta[-1] or 1.0)
        add(f'<text x="{x:.1f}" y="{sy1 + 18}" fill="{MUTED}" font-size="11" '
            f'text-anchor="middle">{_num2(tick, 1)}</text>')
    add(f'<text x="{mx1}" y="{sy1 + 34}" fill="{MUTED}" font-size="11" '
        f'text-anchor="end">t ({unit})</text>')

    # ---- 平面：增量轨迹 = 交流负载线
    if plane:
        xs, ys = plane["x"], plane["y"]
        add(f'<rect x="{px0}" y="{my0}" width="{px1 - px0}" height="{sy1 - my0}" '
            f'rx="6" fill="{PANEL}" stroke="{BORDER}"/>')
        xr = float(np.abs(xs).max()) * 1.12 or 1.0
        yr = float(np.abs(ys).max()) * 1.15 or 1.0

        def px(v):
            return (px0 + px1) / 2 + (px1 - px0) / 2 * v / xr

        def py(v):
            return (my0 + sy1) / 2 - (sy1 - my0) / 2 * v / yr

        add(f'<text x="{px0}" y="{my0 - 10}" fill="{MUTED}" font-size="12">'
            f'{cfg.get("plane_title", "增量轨迹")}</text>')
        for v in _nice_ticks(-yr, yr, 4):
            add(f'<line x1="{px0}" y1="{py(v):.1f}" x2="{px1}" y2="{py(v):.1f}" stroke="{GRID}"/>')
            add(f'<text x="{px0 - 6}" y="{py(v) + 4:.1f}" fill="{MUTED}" font-size="10" '
                f'text-anchor="end">{_num2(v, 1)}</text>')
        for v in _nice_ticks(-xr, xr, 4):
            add(f'<line x1="{px(v):.1f}" y1="{my0}" x2="{px(v):.1f}" y2="{sy1}" stroke="{GRID}"/>')
            add(f'<text x="{px(v):.1f}" y="{sy1 + 18}" fill="{MUTED}" font-size="10" '
                f'text-anchor="middle">{_num2(v, 1)}</text>')
        add(f'<line x1="{px0}" y1="{py(0):.1f}" x2="{px1}" y2="{py(0):.1f}" stroke="{BORDER}"/>')
        add(f'<line x1="{px(0):.1f}" y1="{my0}" x2="{px(0):.1f}" y2="{sy1}" stroke="{BORDER}"/>')
        add(f'<text x="{(px0 + px1) / 2:.1f}" y="{sy1 + 34}" fill="{MUTED}" font-size="11" '
            f'text-anchor="middle">{plane.get("xlabel", "Δu_CE (V)")}</text>')
        add(f'<text x="{px0 - 44}" y="{(my0 + sy1) / 2:.1f}" fill="{MUTED}" font-size="11" '
            f'text-anchor="middle" transform="rotate(-90 {px0 - 44} {(my0 + sy1) / 2:.1f})">'
            f'{plane.get("ylabel", "Δi_C (mA)")}</text>')

        add(f'<path d="{_path(xs, ys, px, py)}" fill="none" stroke="{LOCUS_C}" '
            f'stroke-width="1.5" opacity=".45"/>')
        add(f'<circle cx="{px(0):.1f}" cy="{py(0):.1f}" r="4" fill="none" '
            f'stroke="{TEXT}" stroke-width="1.2" opacity=".7"/>')
        add(f'<text x="{px(0) + 7:.1f}" y="{py(0) - 6:.1f}" fill="{MUTED}" '
            f'font-size="10">Q</text>')

        n = len(xs)
        for i in range(DOTS):
            k = int((i + 0.5) / DOTS * (n - 1))
            add(f'<circle cx="{px(xs[k]):.1f}" cy="{py(ys[k]):.1f}" r="4.4" '
                f'fill="{MAIN_C}" opacity="0">'
                f'{_slice_anim(i / DOTS, (i + 1) / DOTS)}</circle>')

    # ---- 播放头：x1/x2 同步动画（静态时停在左边界）
    add(f'<line x1="{mx0}" y1="{my0}" x2="{mx0}" y2="{sy1}" stroke="{TEXT}" '
        f'stroke-width="1" stroke-dasharray="3 3" opacity=".5">'
        f'<animate attributeName="x1" values="{mx0};{mx1}" dur="{DUR}s" repeatCount="indefinite"/>'
        f'<animate attributeName="x2" values="{mx0};{mx1}" dur="{DUR}s" repeatCount="indefinite"/>'
        f'</line>')

    # ---- 进度条 + 脚注
    bw = (px1 if plane else mx1) - mx0
    add(f'<rect x="{mx0}" y="482" width="{bw}" height="3" rx="1.5" fill="{GRID}"/>')
    add(f'<rect x="{mx0}" y="482" width="{bw}" height="3" rx="1.5" fill="{LOCUS_C}" '
        f'opacity=".9"><animate attributeName="width" values="0;{bw}" dur="{DUR}s" '
        f'repeatCount="indefinite"/></rect>')
    add(f'<circle cx="{mx0 + 4}" cy="504" r="3.4" fill="{LOCUS_C}">'
        f'<animate attributeName="opacity" values="1;.15;1" dur="1.6s" repeatCount="indefinite"/>'
        f'</circle>')
    add(f'<text x="{mx0 + 14}" y="508" fill="{MUTED}" font-size="10.5">'
        f'{cfg.get("footnote", "")}</text>')
    add("</svg>")
    return "\n".join(s)


def _validate(svg: str, path: Path) -> None:
    """写出前先自检：XML 合法、含 SMIL 动画、没有未替换的占位符。"""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(svg)
    if not root.tag.endswith("svg"):
        raise ValueError(f"{path}: 根元素不是 svg")
    if root.find(".//{http://www.w3.org/2000/svg}animate") is None:
        raise ValueError(f"{path}: 没有任何 animate 元素，动画没生成")
    if "{" in svg and "}" in svg and "{v}" in svg:
        raise ValueError(f"{path}: 残留模板占位符")


def build(circuit_dir: Path, out: Path | None = None, png: bool = False) -> Path:
    """读 <circuit>/spec.json 的 anim 段，仿真并生成 SVG（可选静态 PNG 预览）。"""
    cdir = Path(circuit_dir)
    spec = json.loads((cdir / "spec.json").read_text(encoding="utf-8"))
    cfg = spec.get("anim")
    if not cfg:
        raise SystemExit(f"{cdir}/spec.json 里没有 anim 段")
    netlist = tran.ensure_title((cdir / "netlist.cir").read_text(encoding="utf-8"))
    data = collect(netlist, cfg)
    svg = render(data, cfg)
    target = Path(out) if out else HERE / "out" / cdir.name / f"{cdir.name}_anim.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    _validate(svg, target)
    target.write_text(svg, encoding="utf-8")

    if png:
        import cairosvg
        # 静态预览：描边动画停在完整长度，字体换成本机 CJK 字体（cairosvg 不做逐字回退）
        preview = svg.replace(FONT, CJK_PREVIEW)
        png_path = target.with_suffix(".png")
        cairosvg.svg2png(bytestring=preview.encode("utf-8"), write_to=str(png_path),
                         output_width=1600, background_color="#0b1017")
    return target


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cdir = Path(argv[0])
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    p = build(cdir, out, png="--png" in argv)
    print(f"wrote {p} ({p.stat().st_size / 1024:.1f} KiB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
