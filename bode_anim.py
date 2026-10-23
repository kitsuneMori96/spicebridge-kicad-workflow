#!/usr/bin/env python3
"""把 AC 扫频结果渲染成自包含的可交互 SVG（单个文件，无外部依赖）。

数据一律来自 ngspice 实跑，不画公式。

画面：
  - 上：幅频 |A_u| (dB) + 渐近线折线 + -3 dB 点
  - 下：相频 ∠A_u (度)
  - 底部：进度条；有脚本时还有播放/暂停按钮与时间码
  - 右上角读数随扫描线实时刷新 f / |A| / ∠A

两种播放方式，同一份文件：
  - 直接打开（浏览器 file:// 或 http）：JS 接管，可暂停、拖进度条、
    点图表跳转、空格键播放暂停；
  - 被 <img> 内嵌（OpenCode web、GitHub、Markdown 渲染器）：脚本不执行，
    改由内嵌的 SMIL 关键帧循环播放，读数与进度条照常走，只是不能交互。

用法：
    python3 bode_anim.py circuits/q4_6
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import bode  # noqa: E402

# ---------------------------------------------------------------- 配色 / 版面
W, H = 1060, 760
BG = "#0b1017"
PANEL = "#111a23"
GRID = "#1d2834"
BORDER = "#25323f"
TEXT = "#e6edf3"
MUTED = "#8fa3b8"
MAIN_C = "#ffb703"
ASYM_C = "#4cc9f0"
MARK_C = "#f472b6"
GOOD = "#22c55e"
FONT = ("ui-sans-serif, -apple-system, 'Segoe UI', 'Noto Sans CJK SC', "
        "'WenQuanYi Zen Hei', 'Microsoft YaHei', sans-serif")
MONO = ("ui-monospace, 'JetBrains Mono', 'DejaVu Sans Mono', Consolas, monospace")

X0, X1 = 88, 996
MY0, MY1 = 120, 392
PY0, PY1 = 442, 618
CY = 690            # 控件条中心线
DUR = 12.0          # 一次扫频的秒数
DOTS = 160          # 采样点数（SMIL 关键帧与 JS 插值共用）
RO_READOUTS = 72    # SMIL 后备读数的切片数


# ---------------------------------------------------------------- 小工具
def _fmt_hz(f: float) -> str:
    if f >= 1e6 - 1:
        return f"{f / 1e6:.3g} MHz"
    if f >= 1e3 - 1:
        return f"{f / 1e3:.3g} kHz"
    return f"{f:.3g} Hz"


def _readout(fv: float, mv: float, pv: float) -> str:
    g = 10 ** (mv / 20.0)
    gs = f"{g:.3g}" if g < 1e4 else f"{g:.2g}"
    return (f"f = {_fmt_hz(fv):>9}   |A| = {mv:+6.1f} dB (\u00d7{gs})"
            f"   \u2220A = {pv:+7.1f}\u00b0")


def _slice(t0: float, t1: float, dur: float = DUR) -> str:
    return (f'<animate attributeName="opacity" values="0;1;0" '
            f'keyTimes="0;{t0:.4f};{t1:.4f}" calcMode="discrete" '
            f'dur="{dur}s" repeatCount="indefinite"/>')


def _path(xs, ys, xmap, ymap) -> str:
    return "M " + " L ".join(f"{xmap(x):.1f},{ymap(y):.1f}" for x, y in zip(xs, ys))


# ---------------------------------------------------------------- 数据
def collect(cdir: Path) -> dict:
    spec = json.loads((cdir / "spec.json").read_text(encoding="utf-8"))
    cfg = spec["bode"]
    netlist = (cdir / "netlist.cir").read_text(encoding="utf-8")
    fr = cfg["freq"]
    vec = f"v({cfg.get('out', 'out')})"
    f, h = bode.run_ac(netlist, float(fr["start"]), float(fr["stop"]),
                       int(fr["ppd"]), vec)
    if len(f) < 10:
        raise SystemExit("AC 扫频点数太少，检查 netlist 是否含 .ac")

    ref = bode.analytic(f, cfg.get("tf", {}))
    d_db = float(np.max(np.abs(bode.db(h) - bode.db(ref))))
    d_ph = float(np.max(np.abs(bode.phase_deg(h) - bode.phase_deg(ref))))
    if d_db > 0.05 or d_ph > 0.5:
        raise SystemExit(f"仿真与解析式不一致：{d_db:.3g} dB / {d_ph:.3g}°")

    return {"f": f, "h": h, "cfg": cfg, "spec": spec,
            "d_db": d_db, "d_ph": d_ph}


# ---------------------------------------------------------------- 渲染
def render(data: dict) -> str:
    f, h, cfg = data["f"], data["h"], data["cfg"]
    mag = bode.db(h)
    ph = bode.phase_deg(h)
    poles = [float(p) for p in cfg.get("tf", {}).get("poles_hz", [])]
    nz = int(cfg.get("tf", {}).get("zeros_at_origin", 0))

    k = int(np.argmax(mag))
    plateau = float(mag[k])
    f_lo = bode.corner_3db(f, mag, plateau, rising=True)
    f_hi = bode.corner_3db(f, mag, plateau, rising=False)
    mid_gain = (float(cfg.get("tf", {}).get("gain", 1)) * (poles[0] ** nz)
                if poles and nz else float(abs(h[k])))

    lo, hi = math.log10(f[0]), math.log10(f[-1])
    vlo, vhi = -20.0, math.ceil((plateau + 16) / 10) * 10
    plo, phi = -190.0, math.ceil((ph.max() + 8) / 10) * 10

    def lx(fv: float) -> float:
        return X0 + (X1 - X0) * (math.log10(fv) - lo) / (hi - lo)

    def my(v: float) -> float:
        return MY1 - (MY1 - MY0) * (v - vlo) / (vhi - vlo)

    def py(v: float) -> float:
        return PY1 - (PY1 - PY0) * (v - plo) / (phi - plo)

    s: list[str] = []
    add = s.append
    add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" '
        f'aria-label="4.6 题放大器频率响应波特图动画">')
    add("<defs>")
    add(f'<clipPath id="sweep"><rect id="clip" x="{X0}" y="{MY0 - 6}" '
        f'width="{X1 - X0}" height="{PY1 - MY0 + 12}" rx="2">'
        f'<animate attributeName="width" values="0;{X1 - X0}" dur="{DUR}s" '
        f'repeatCount="indefinite" calcMode="linear"/></rect></clipPath>')
    add("</defs>")
    add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

    # ---- 标题 / 副标题 / 读数
    add(f'<text x="28" y="38" fill="{TEXT}" font-size="21" font-weight="600">'
        f'{cfg.get("title", "波特图")}</text>')
    add(f'<text x="28" y="62" fill="{MUTED}" font-size="12.5">'
        f'{cfg.get("subtitle", "")}</text>')
    add(f'<text id="ro" x="{X1}" y="38" fill="{TEXT}" font-size="12.5" '
        f'font-family="{MONO}" text-anchor="end" style="display:none">'
        f'f = {_fmt_hz(f[-1]):>9}   |A| = {mag[-1]:+6.1f} dB   ∠A = {ph[-1]:+7.1f}°</text>')
    # SMIL 后备读数（块内 72 条切片，逐段切换）；有 JS 时整组隐藏
    add(f'<g id="roSlices">')
    for i in range(RO_READOUTS):
        u = (i + 0.5) / RO_READOUTS
        fv = 10 ** (lo + (hi - lo) * u)
        mv = float(np.interp(fv, f, mag))
        pv = float(np.interp(fv, f, ph))
        add(f'<text x="{X1}" y="38" fill="{TEXT}" font-size="12.5" '
            f'font-family="{MONO}" text-anchor="end" opacity="0">{_readout(fv, mv, pv)}'
            f'{_slice(i / RO_READOUTS, (i + 1) / RO_READOUTS)}</text>')
    add('</g>')

    # ---- 面板
    for (y0, y1, title) in ((MY0, MY1, "幅值 |A_u| (dB)"), (PY0, PY1, "相位 ∠A_u (°)")):
        add(f'<rect x="{X0}" y="{y0}" width="{X1 - X0}" height="{y1 - y0}" rx="6" '
            f'fill="{PANEL}" stroke="{BORDER}"/>')
        add(f'<text x="{X0}" y="{y0 - 9}" fill="{MUTED}" font-size="12">{title}</text>')

    # ---- 横向网格
    def ylab(v: float) -> str:
        return "0" if v == 0 else f"{v:+d}"

    v = math.ceil(vlo / 20) * 20
    while v <= vhi:
        y = my(v)
        add(f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" stroke="{GRID}"/>')
        add(f'<text x="{X0 - 8}" y="{y + 4:.1f}" fill="{MUTED}" font-size="11" '
            f'text-anchor="end">{ylab(v)}</text>')
        v += 20
    for v in (90, 45, 0, -45, -90, -135, -180):
        y = py(v)
        add(f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" stroke="{GRID}"/>')
        add(f'<text x="{X0 - 8}" y="{y + 4:.1f}" fill="{MUTED}" font-size="11" '
            f'text-anchor="end">{ylab(v)}</text>')

    # ---- 十倍频刻度
    dec = 10 ** math.ceil(lo - 1e-9)
    while dec <= f[-1] * 1.0001:
        x = lx(dec)
        add(f'<line x1="{x:.1f}" y1="{MY0}" x2="{x:.1f}" y2="{PY1}" stroke="{GRID}" '
            f'stroke-dasharray="2 5" opacity=".8"/>')
        lab = (f"{dec / 1e6:g}M" if dec >= 1e6 else
               f"{dec / 1e3:g}k" if dec >= 1e3 else f"{dec:g}")
        add(f'<text x="{x:.1f}" y="{PY1 + 18}" fill="{MUTED}" font-size="11" '
            f'text-anchor="middle">{lab}</text>')
        dec *= 10
    add(f'<text x="{X1}" y="{PY1 + 36}" fill="{MUTED}" font-size="11" '
        f'text-anchor="end">f (Hz，对数坐标)</text>')

    # ---- 极点竖线
    for fp in poles:
        x = lx(fp)
        add(f'<line x1="{x:.1f}" y1="{MY0}" x2="{x:.1f}" y2="{PY1}" stroke="{MARK_C}" '
            f'stroke-dasharray="6 5" opacity=".45"/>')
    names = ["f₁", "f₂", "f₃"]
    for i, fp in enumerate(poles[:3]):
        add(f'<text x="{lx(fp):.1f}" y="{MY0 + 16}" fill="{MARK_C}" font-size="10.5" '
            f'text-anchor="middle">{names[i]}={_fmt_hz(fp)}</text>')

    # ---- 渐近线折线
    slope, base = 20.0 * nz, plateau
    base -= slope * math.log10(poles[0] / f[0]) if poles else 0
    ax, ay = [f[0]], [base]
    for fp in poles:
        ax.append(fp)
        ay.append(ay[-1] + slope * math.log10(fp / ax[-2]))
        slope -= 20.0
    ax.append(f[-1])
    ay.append(ay[-1] + slope * math.log10(ax[-1] / ax[-2]))
    add(f'<path d="{_path(ax, ay, lx, my)}" fill="none" stroke="{ASYM_C}" '
        f'stroke-width="1.4" stroke-dasharray="7 5" opacity=".85"/>')
    for i in range(len(ax) - 1):
        f0, f1, y0, y1 = ax[i], ax[i + 1], ay[i], ay[i + 1]
        sl = 0.0 if f1 <= f0 else (y1 - y0) / math.log10(f1 / f0)
        if abs(sl) < 0.5:
            continue
        add(f'<text x="{lx(math.sqrt(f0 * f1)):.1f}" '
            f'y="{my((y0 + y1) / 2) + (26 if sl < 0 else -12):.1f}" fill="{ASYM_C}" '
            f'font-size="10.5" text-anchor="middle">{sl:+.0f} dB/dec</text>')

    # ---- 相频渐近线（与幅频同一套折点）
    import bode_asym as _ba
    ph_pts = sorted({f[0], f[-1]} |
                    {round(v, 10) for fp in poles for v in (fp / 10, fp, fp * 10)
                     if f[0] <= v <= f[-1]})
    pvals = _ba.phase_asym(ph_pts, poles)
    add(f'<path d="{_path(ph_pts, pvals, lx, py)}" fill="none" stroke="{ASYM_C}" '
        f'stroke-width="1.3" stroke-dasharray="6 5" opacity=".55"/>')

    add(f'<line x1="{X0}" y1="{my(plateau):.1f}" x2="{X1}" y2="{my(plateau):.1f}" '
        f'stroke="{GOOD}" stroke-width="1" stroke-dasharray="1 4" opacity=".8"/>')
    add(f'<text x="{lx(math.sqrt(poles[0] * poles[1])) if len(poles) > 1 else 300:.1f}" '
        f'y="{my(plateau) - 10:.1f}" fill="{GOOD}" font-size="11.5" '
        f'text-anchor="middle">中频 ≈{mid_gain:.0f} 倍（{plateau:.1f} dB）</text>')

    # ---- -3 dB 点
    for fx, tag, dy in ((f_lo, "下限", -12), (f_hi, "上限", 16)):
        mv = float(np.interp(fx, f, mag))
        add(f'<circle cx="{lx(fx):.1f}" cy="{my(mv):.1f}" r="4" fill="{PANEL}" '
            f'stroke="{MARK_C}" stroke-width="1.6"/>')
        add(f'<text x="{lx(fx):.1f}" y="{my(mv) + dy:.1f}" fill="{MARK_C}" '
            f'font-size="10.5" text-anchor="middle">{tag} {_fmt_hz(fx)}（{mv:+.1f} dB）</text>')

    # ---- 曲线：淡影 + 被裁剪层遮住的实线
    d_mag = _path(f, mag, lx, my)
    d_ph = _path(f, ph, lx, py)
    add(f'<path d="{d_mag}" fill="none" stroke="{MAIN_C}" stroke-width="1.6" opacity=".13"/>')
    add(f'<path d="{d_ph}" fill="none" stroke="{ASYM_C}" stroke-width="1.6" opacity=".13"/>')
    add('<g clip-path="url(#sweep)">')
    add(f'<path d="{d_mag}" fill="none" stroke="{MAIN_C}" stroke-width="2.4" '
        f'stroke-linejoin="round"/>')
    add(f'<path d="{d_ph}" fill="none" stroke="{ASYM_C}" stroke-width="2.2" '
        f'stroke-linejoin="round"/>')
    add("</g>")

    # ---- 扫描线 + 跟随圆点（带回 SMIL 关键帧，无 JS 时也能动）
    u = (np.arange(DOTS) + 0.5) / DOTS
    fs = 10 ** (lo + (hi - lo) * u)
    xs_ = [lx(x) for x in fs]
    mv_ = np.interp(fs, f, mag)
    pv_ = np.interp(fs, f, ph)
    yms_ = [my(y) for y in mv_]
    yps_ = [py(y) for y in pv_]
    kt = ";".join(f"{i / (DOTS - 1):.4f}" for i in range(DOTS))

    def keyed(name: str, vals: list[float]) -> str:
        v = ";".join(f"{x:.1f}" for x in vals)
        return (f'<animate attributeName="{name}" values="{v}" keyTimes="{kt}" '
                f'dur="{DUR}s" repeatCount="indefinite" calcMode="linear"/>')

    add(f'<line id="cur" x1="{X1}" y1="{MY0}" x2="{X1}" y2="{PY1}" stroke="{TEXT}" '
        f'stroke-width="1" opacity=".35">'
        f'<animate attributeName="x1" values="{X0};{X1}" dur="{DUR}s" repeatCount="indefinite"/>'
        f'<animate attributeName="x2" values="{X0};{X1}" dur="{DUR}s" repeatCount="indefinite"/>'
        f'</line>')
    add(f'<circle id="dm" r="5" cx="{X1}" cy="{my(mag[-1]):.1f}" fill="{BG}" '
        f'stroke="{MAIN_C}" stroke-width="2">{keyed("cx", xs_)}{keyed("cy", yms_)}</circle>')
    add(f'<circle id="dp" r="4.5" cx="{X1}" cy="{py(ph[-1]):.1f}" fill="{BG}" '
        f'stroke="{ASYM_C}" stroke-width="2">{keyed("cx", xs_)}{keyed("cy", yps_)}</circle>')

    # ---- 透明热区：点/拖图表直接跳转
    add(f'<rect id="hot" x="{X0}" y="{MY0}" width="{X1 - X0}" height="{PY1 - MY0}" '
        f'fill="transparent" style="cursor:crosshair"/>')

    # ---- 控件条（进度条始终显示；按钮/时间码只有 JS 接管时才出现）
    add('<g id="ctrl">')
    add(f'<rect x="28" y="{CY - 24}" width="{W - 56}" height="48" rx="10" '
        f'fill="{PANEL}" stroke="{BORDER}"/>')
    add('<g id="jsc" style="display:none">')
    add(f'<g id="btn" style="cursor:pointer">')
    add(f'<circle cx="56" cy="{CY}" r="15" fill="#16212c" stroke="{BORDER}"/>')
    add(f'<g id="icPlay" style="display:none">'
        f'<path d="M 51 {CY - 7} L 64 {CY} L 51 {CY + 7} Z" fill="{MAIN_C}"/></g>')
    add(f'<g id="icPause">'
        f'<rect x="49" y="{CY - 7}" width="4.5" height="14" rx="1.4" fill="{MAIN_C}"/>'
        f'<rect x="57.5" y="{CY - 7}" width="4.5" height="14" rx="1.4" fill="{MAIN_C}"/></g>')
    add("</g>")   # </g id=btn>
    add("</g>")   # </g id=jsc>
    add(f'<line x1="92" y1="{CY}" x2="{W - 190}" y2="{CY}" stroke="{GRID}" '
        f'stroke-width="7" stroke-linecap="round"/>')
    add(f'<line id="prog" x1="92" y1="{CY}" x2="92" y2="{CY}" stroke="{MAIN_C}" '
        f'stroke-width="7" stroke-linecap="round">'
        f'<animate attributeName="x2" values="92;{W - 190}" dur="{DUR}s" '
        f'repeatCount="indefinite"/></line>')
    add(f'<circle id="knob" cx="92" cy="{CY}" r="8" fill="{MAIN_C}" '
        f'stroke="{BG}" stroke-width="2">'
        f'<animate attributeName="cx" values="92;{W - 190}" dur="{DUR}s" '
        f'repeatCount="indefinite"/></circle>')
    add(f'<text id="pos" x="{W - 46}" y="{CY + 4}" fill="{MUTED}" font-size="11" '
        f'font-family="{MONO}" text-anchor="end" style="display:none">'
        f't = 12.0 / 12.0 s</text>')
    add('</g>')

    # ---- 脚注
    add(f'<text x="28" y="{H - 12}" fill="{MUTED}" font-size="10.5">'
        f'曲线：ngspice {bode.NGSPICE_VER} 实跑 .ac dec {cfg["freq"]["ppd"]} '
        f'{cfg["freq"]["start"]:g}→{cfg["freq"]["stop"]:g} Hz（{len(f)} 点）　'
        f'校验：与题给解析式最大偏差 {data["d_db"]:.2e} dB / {data["d_ph"]:.2e}°'
        f'　·　拖动进度条 / 点图表跳转 / 空格暂停</text>')

    # ---- JS 数据（与 SMIL 关键帧用同一组采样点）
    js_data = {
        "x": [round(v, 1) for v in xs_],
        "ym": [round(v, 1) for v in yms_],
        "yp": [round(v, 1) for v in yps_],
        "dbm": [round(v, 2) for v in mv_],
        "phd": [round(v, 2) for v in pv_],
        "lo": lo, "hi": hi, "dur": DUR,
        "X0": X0, "X1": X1, "K0": 92, "K1": W - 190,
    }
    add("<script type=\"text/javascript\"><![CDATA[")
    add(_SCRIPT.replace("__DATA__", json.dumps(js_data, separators=(",", ":"))))
    add("]]></script>")
    add("</svg>")
    return "\n".join(s)


_SCRIPT = r"""
(function(){
  var D = __DATA__;
  var svg = document.documentElement;
  var clip = document.getElementById('clip'),
      cur = document.getElementById('cur'),
      dm = document.getElementById('dm'),
      dp = document.getElementById('dp'),
      prog = document.getElementById('prog'),
      knob = document.getElementById('knob'),
      ro = document.getElementById('ro'),
      pos = document.getElementById('pos'),
      btn = document.getElementById('btn'),
      icPlay = document.getElementById('icPlay'),
      icPause = document.getElementById('icPause'),
      hot = document.getElementById('hot'),
      ctrl = document.getElementById('ctrl'),
      jsc = document.getElementById('jsc'),
      slices = document.getElementById('roSlices');
  var N = D.x.length, T = 0, playing = true, last = null, drag = false;
  var SPAN = D.X1 - D.X0;

  function fmtHz(f){
    if (f >= 1e6 - 1) return (f/1e6).toPrecision(3).replace(/\.?0+$/,'') + ' MHz';
    if (f >= 1e3 - 1) return (f/1e3).toPrecision(3).replace(/\.?0+$/,'') + ' kHz';
    return f.toPrecision(3).replace(/\.?0+$/,'') + ' Hz';
  }

  function refresh(){
    var x = D.X0 + SPAN * T;
    clip.setAttribute('width', Math.max(0, x - D.X0).toFixed(1));
    cur.setAttribute('x1', x.toFixed(1));
    cur.setAttribute('x2', x.toFixed(1));

    var i = T * (N - 1), i0 = Math.floor(i), i1 = Math.min(N - 1, i0 + 1), u = i - i0;
    var px = D.x[i0] + (D.x[i1] - D.x[i0]) * u;
    var ym = D.ym[i0] + (D.ym[i1] - D.ym[i0]) * u;
    var yp = D.yp[i0] + (D.yp[i1] - D.yp[i0]) * u;
    dm.setAttribute('cx', px.toFixed(1)); dm.setAttribute('cy', ym.toFixed(1));
    dp.setAttribute('cx', px.toFixed(1)); dp.setAttribute('cy', yp.toFixed(1));

    var kx = D.K0 + (D.K1 - D.K0) * T;
    prog.setAttribute('x2', kx.toFixed(1));
    knob.setAttribute('cx', kx.toFixed(1));
    pos.textContent = 't = ' + (T * D.dur).toFixed(1) + ' / ' + D.dur.toFixed(1) + ' s';

    var fq = Math.pow(10, D.lo + (D.hi - D.lo) * T);
    var mv = D.dbm[i0] + (D.dbm[i1] - D.dbm[i0]) * u;
    var pv = D.phd[i0] + (D.phd[i1] - D.phd[i0]) * u;
    var g = Math.pow(10, mv / 20);
    var gs = g >= 1e4 ? g.toPrecision(3) : (g >= 100 ? g.toFixed(0) : g.toPrecision(3));
    ro.textContent = 'f = ' + fmtHz(fq) + '   |A| = ' + (mv >= 0 ? '+' : '') +
      mv.toFixed(1) + ' dB (×' + gs + ')   ∠A = ' + (pv >= 0 ? '+' : '') + pv.toFixed(1) + '°';
  }

  function icons(){
    icPlay.style.display = playing ? 'none' : '';
    icPause.style.display = playing ? '' : 'none';
  }

  function loop(ts){
    if (last === null) last = ts;
    var dt = (ts - last) / 1000; last = ts;
    if (playing && !drag){
      T += dt / D.dur;
      if (T > 1) T = 0;
    }
    refresh();
    requestAnimationFrame(loop);
  }

  function userX(evt){
    var r = svg.getBoundingClientRect();
    var k = r.width ? (1060 / r.width) : 1;
    var x = (evt.clientX - r.left) * k;
    return Math.min(D.X1, Math.max(D.X0, x));
  }
  function seekTo(x){
    T = (x - D.X0) / SPAN;
    if (T < 0) T = 0; if (T > 1) T = 1;
    refresh();
  }

  btn.addEventListener('click', function(e){ e.stopPropagation(); playing = !playing; icons(); });
  document.addEventListener('keydown', function(e){
    if (e.code === 'Space'){ e.preventDefault(); playing = !playing; icons(); }
    if (e.code === 'ArrowRight'){ e.preventDefault(); T = Math.min(1, T + 0.02); }
    if (e.code === 'ArrowLeft'){ e.preventDefault(); T = Math.max(0, T - 0.02); }
  });
  hot.addEventListener('pointerdown', function(e){ drag = true; playing = false; icons(); seekTo(userX(e)); });
  knob.addEventListener('pointerdown', function(e){ drag = true; playing = false; icons(); e.preventDefault(); e.stopPropagation(); });
  window.addEventListener('pointermove', function(e){ if (drag) seekTo(userX(e)); });
  window.addEventListener('pointerup', function(){ drag = false; });

  // 接管 SMIL：拆掉后备动画，改用可控的 JS 驱动
  Array.prototype.forEach.call(svg.querySelectorAll('animate'), function(n){
    n.parentNode && n.parentNode.removeChild(n);
  });
  if (slices) slices.style.display = 'none';
  if (jsc) jsc.style.display = '';
  if (ctrl) ctrl.style.display = '';
  if (pos) pos.style.display = '';
  knob.style.cursor = 'grab';
  ro.style.display = '';

  T = 0; icons(); refresh(); requestAnimationFrame(loop);
})();
"""


# ---------------------------------------------------------------- 校验 / 落盘
def _validate(svg: str, path: Path) -> None:
    root = ET.fromstring(svg)
    if "requestAnimationFrame" not in svg:
        raise ValueError(f"{path}: 缺少交互脚本")
    if "nan" in svg.lower() or "None" in svg:
        raise ValueError(f"{path}: 含 NaN / None")
    print(f"校验通过：XML 合法，含交互脚本（{path.stat().st_size / 1024:.0f} KB）")


def build(cdir: Path) -> Path:
    cdir = Path(cdir)
    data = collect(cdir)
    svg = render(data)
    outdir = HERE / "out" / cdir.name
    outdir.mkdir(parents=True, exist_ok=True)
    target = outdir / f"{cdir.name}_bode_anim.svg"
    target.write_text(svg, encoding="utf-8")
    _validate(svg, target)
    print(f"wrote {target}")
    return target


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    build(Path(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
