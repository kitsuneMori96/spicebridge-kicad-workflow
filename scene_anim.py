#!/usr/bin/env python3
"""把 scene.json 渲染成自包含的 SMIL SVG（无 JS、无外部资源）。

时间模型：所有动画共用一条全局时间轴 T，全部用全局 keyTimes，禁止 begin/end。
静态回退：动画层基础 opacity=0；海报层基础 opacity=1，用一次性 0.01s 淡出。
cairosvg/打印不支持 SMIL 时会停在完整海报，不会白图。

用法:
    python3 scene_anim.py circuits/q17
    python3 scene_anim.py circuits/q17 --check
    python3 scene_anim.py circuits/q17 --still all --png
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scene as scene_mod  # noqa: E402
import svg_anim  # noqa: E402

W, H = 1000, 560
BG = "#0b1017"
PANEL = "#111a23"
GRID = "#1d2834"
BORDER = "#25323f"
TEXT = "#e6edf3"
MUTED = "#8fa3b8"
MAIN = "#ffb703"
INPUT = "#4cc9f0"
LOCUS = "#7ee787"
LIMIT = "#ff6b6b"
ACCENT = "#ffcf5c"
DC = "#58a6ff"
SCHEM_HL = "#d97706"
SCHEM_SIG = "#0969da"
CREAM = "#F5F4EF"
FONT = svg_anim.FONT
MONO = svg_anim.MONO
CJK_PREVIEW = svg_anim.CJK_PREVIEW

COLORS = {
    "bg": BG, "panel": PANEL, "grid": GRID, "border": BORDER,
    "text": TEXT, "muted": MUTED, "main": MAIN, "input": INPUT,
    "locus": LOCUS, "limit": LIMIT, "accent": ACCENT, "dc": DC,
    "cream": CREAM, "schematics": SCHEM_HL,
}

LAYOUT_DEFAULTS = {
    "card": {
        "bullets": [42, 130], "formula": [42, 130], "text": [42, 130],
        "schematic": [500, 112, 440, 310], "plot": [500, 112, 440, 310],
        "table": [42, 130, 620, 300], "answer": [500, 230],
    },
    "plot": {
        "plot": [70, 112, 860, 320], "formula": [70, 452], "text": [70, 452],
        "table": [70, 112, 860, 320], "schematic": [70, 112, 860, 320],
        "bullets": [70, 452], "answer": [500, 230],
    },
    "derivation": {
        "formula": [42, 130], "bullets": [42, 130], "text": [42, 130],
        "plot": [470, 112, 470, 320], "schematic": [470, 112, 470, 320],
        "table": [42, 130, 400, 300], "answer": [500, 230],
    },
    "compare": {
        "table": [42, 130, 620, 300], "text": [690, 130],
        "plot": [690, 112, 250, 320], "bullets": [690, 130],
        "schematic": [690, 112, 250, 320], "formula": [690, 130],
        "answer": [690, 300],
    },
    "summary": {
        "answer": [500, 220], "bullets": [70, 320], "formula": [70, 430],
        "schematic": [690, 140, 270, 240], "text": [70, 300],
        "plot": [690, 140, 270, 240], "table": [70, 320, 550, 220],
    },
}

SEP = "\n"


def _esc(s) -> str:
    return _xml_escape(str(s), {'"': "&quot;"})


def _color(v, default: str = "text") -> str:
    if not v:
        return COLORS[default]
    return COLORS.get(v, v)


def _keys(vals, T: float) -> list[float]:
    """把秒换成 [0,1] 的 keyTimes：首 0、末 1、严格非递减。"""
    if T <= 0:
        return [0.0, 1.0]
    ks = [min(1.0, max(0.0, float(v) / T)) for v in vals]
    ks[0] = 0.0
    ks[-1] = 1.0
    for i in range(1, len(ks)):
        if ks[i] <= ks[i - 1]:
            ks[i] = min(1.0, ks[i - 1] + 1e-5)
    ks[-1] = 1.0
    for i in range(len(ks) - 2, -1, -1):
        if ks[i] >= ks[i + 1]:
            ks[i] = max(0.0, ks[i + 1] - 1e-5)
    return ks


def _kt(vals) -> str:
    return ";".join(f"{v:.6f}" for v in vals)


_RICH_TOKEN = re.compile(r"(_\{[^{}]*\}|\^\{[^{}]*\}|\[\[[^\]]*\]\])")
_CJK = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff\uff00-\uffef\u3000-\u303f]")
_WIDE = set("≈→←↔∥√Ω±×÷∑∫")


def _plain_width(s: str, size: float) -> float:
    w = 0.0
    for ch in s:
        if _CJK.match(ch):
            w += size
        elif ch == " ":
            w += size * 0.34
        elif ch in ".,:;'|!()[]":
            w += size * 0.34
        elif ch in _WIDE:
            w += size * 0.95
        elif ch in "mw":
            w += size * 0.82
        elif ch in "ilt":
            w += size * 0.32
        elif ch.isupper():
            w += size * 0.66
        elif ch.isdigit():
            w += size * 0.56
        else:
            w += size * 0.55
    return w


def _text_width(text: str, size: float) -> float:
    """粗估渲染宽度（含标记），只用于居中/标签框，不追求像素精确。"""
    text = str(text)
    w, pos = 0.0, 0
    for m in _RICH_TOKEN.finditer(text):
        if m.start() > pos:
            w += _plain_width(text[pos:m.start()], size)
        tok = m.group(0)
        if tok.startswith(("_{", "^{")):
            w += _plain_width(tok[2:-1], size * 0.72)
        else:
            w += _plain_width(tok[2:-2], size)
        pos = m.end()
    if pos < len(text):
        w += _plain_width(text[pos:], size)
    return w


def _rich(text: str, size: float, accent: str = ACCENT) -> str:
    """轻量行内标记：_{sub}、^{sup}、[[accent]]。

    用 dy 而不是 baseline-shift：cairosvg/打印不认 baseline-shift，
    PNG 预览里下标会长在基线上。dy 在每个片段后显式归零，两个渲染器一致。
    """
    token = re.compile(r"(_\{[^{}]*\}|\^\{[^{}]*\}|\[\[[^\]]*\]\])")
    out, pos, shift = [], 0, 0.0

    def span(content: str, target: float, extra: str = "") -> str:
        nonlocal shift
        delta = target - shift
        shift = target
        dy = f' dy="{delta:.2f}"' if abs(delta) > 1e-6 else ""
        return f"<tspan{dy}{extra}>{content}</tspan>"

    for m in token.finditer(str(text)):
        if m.start() > pos:
            out.append(span(_esc(text[pos:m.start()]), 0.0))
        tok = m.group(0)
        if tok.startswith("_{"):
            out.append(span(_esc(tok[2:-1]), size * 0.22,
                            f' font-size="{size*0.72:.1f}"'))
        elif tok.startswith("^{"):
            out.append(span(_esc(tok[2:-1]), -size * 0.42,
                            f' font-size="{size*0.72:.1f}"'))
        else:
            out.append(span(f'<tspan fill="{accent}" font-weight="600">'
                            f'{_esc(tok[2:-2])}</tspan>', 0.0))
        pos = m.end()
    if pos < len(str(text)):
        out.append(span(_esc(text[pos:]), 0.0))
    return "".join(out)


class Renderer:
    def __init__(self, cfg: dict, data: dict, sch_path: Path | None = None,
                 static: bool = False, only_scene: int | None = None):
        self.cfg = cfg
        self.data = data
        self.ctx = data["context"]
        self.series = data["series"]
        self.T = float(cfg["_total"])
        self.starts = [float(x) for x in cfg["_starts"]]
        self.scenes = cfg["scenes"]
        self.static = static
        self.only_scene = only_scene
        self.sch_path = Path(sch_path) if sch_path else None
        self.sch_inner = self._read_schematic()

    # ------------------------------------------------------------ 基础
    def _read_schematic(self) -> str:
        if not self.sch_path or not self.sch_path.exists():
            return ""
        text = self.sch_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"<svg\b[^>]*>(.*)</svg>\s*$", text, re.S)
        return m.group(1) if m else ""

    def _ctext(self, x, y, text, size=14, color=TEXT, anchor="start",
               weight=400, mono=False, accent=ACCENT) -> str:
        if text is None or str(text) == "":
            return ""
        fam = MONO if mono else FONT
        body = _rich(text, size, accent)
        # cairosvg 会把 text-anchor=middle 的每个 tspan 各自居中，多段文本会叠在一起；
        # 改成按估算宽度左对齐到中线左侧，两个渲染器结果一致。
        if anchor == "middle" and body.count("<tspan") > 1:
            x = float(x) - _text_width(text, size) / 2
            anchor = "start"
        return (f'<text x="{float(x):.1f}" y="{float(y):.1f}" font-family="{fam}" '
                f'font-size="{float(size):.1f}" fill="{color}" text-anchor="{anchor}" '
                f'font-weight="{weight}">{body}</text>')

    def _panel(self, x, y, w, h, rx=6) -> str:
        return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'rx="{rx}" fill="{PANEL}" stroke="{BORDER}"/>')

    def _window(self, t0, t1, fade=0.18) -> str:
        if self.static:
            return ""
        T = self.T
        a = min(T, max(0.0, float(t0)))
        b = min(T, max(a, float(t1)))
        if b - a < 2 * fade:
            fade = max(0.0, (b - a) / 3.0)
        pts = [0.0, a - fade, a, b, b + fade, T]
        ks = _keys(pts, T)
        return (f'<animate attributeName="opacity" values="0;0;1;1;0;0" '
                f'keyTimes="{_kt(ks)}" dur="{T:g}s" repeatCount="indefinite"/>')

    def _scene_window(self, t0, t1, fade=0.2) -> str:
        """幕内淡入淡出：上一幕归零后才进下一幕，避免两幕文字叠在一起。"""
        if self.static:
            return ""
        T = self.T
        a = min(T, max(0.0, float(t0)))
        b = min(T, max(a, float(t1)))
        if b - a < 2 * fade:
            fade = max(0.0, (b - a) / 3.0)
        ks = _keys([0.0, a, a + fade, b - fade, b, T], T)
        return (f'<animate attributeName="opacity" values="0;0;1;1;0;0" '
                f'keyTimes="{_kt(ks)}" dur="{T:g}s" repeatCount="indefinite"/>')

    def _vis(self, content: str, t0, t1, fade=0.16) -> str:
        if self.static or not content:
            return content
        return f'<g opacity="0">{self._window(t0, t1, fade)}{content}</g>'

    def _element_vis(self, el, sc, start, content) -> str:
        t0 = start + float(el.get("at", 0))
        t1 = start + min(float(sc["duration"]), float(el.get("until", sc["duration"])))
        return self._vis(content, t0, t1)

    def _draw(self, length, t0, t1) -> str:
        if self.static or length <= 0:
            return ""
        T = self.T
        a = min(T, max(0.0, float(t0)))
        b = min(T, max(a, float(t1)))
        ks = _keys([0.0, a, b, T], T)
        return (f'<animate attributeName="stroke-dashoffset" '
                f'values="{length:.2f};{length:.2f};0;0" keyTimes="{_kt(ks)}" '
                f'dur="{T:g}s" repeatCount="indefinite"/>')

    def _move_dot(self, xs, ys, t0, t1, n=64) -> str:
        if self.static or len(xs) < 2:
            return ""
        idx = np.linspace(0, len(xs) - 1, min(n, len(xs))).astype(int)
        times = np.linspace(float(t0), float(t1), len(idx))
        T = self.T
        ks = _keys([0.0] + list(times) + [T], T)
        vx = ";".join(f"{xs[i]:.1f}" for i in idx)
        vy = ";".join(f"{ys[i]:.1f}" for i in idx)
        kt = _kt(ks)
        return (f'<animate attributeName="cx" values="{xs[idx[0]]:.1f};{vx};{xs[idx[-1]]:.1f}" '
                f'keyTimes="{kt}" dur="{T:g}s" repeatCount="indefinite"/>'
                f'<animate attributeName="cy" values="{ys[idx[0]]:.1f};{vy};{ys[idx[-1]]:.1f}" '
                f'keyTimes="{kt}" dur="{T:g}s" repeatCount="indefinite"/>')

    def _pos2(self, el, layout: str, typ: str) -> tuple[float, float]:
        pos = el.get("pos") or LAYOUT_DEFAULTS.get(layout, {}).get(typ) or [42, 130]
        return float(pos[0]), float(pos[1])

    def _pos4(self, el, layout: str, typ: str) -> tuple[float, float, float, float]:
        pos = el.get("pos") or LAYOUT_DEFAULTS.get(layout, {}).get(typ) or [42, 130]
        if len(pos) == 4:
            return tuple(float(v) for v in pos)  # type: ignore[return-value]
        x, y = float(pos[0]), float(pos[1])
        defaults = {"plot": (860, 320), "schematic": (440, 310), "table": (620, 300)}
        w, h = defaults.get(typ, (400, 200))
        return x, y, w, h

    # ------------------------------------------------------------ 顶层
    def render(self) -> str:
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'font-family="{FONT}" role="img" aria-label="电路讲解动画">',
            f'<rect width="{W}" height="{H}" fill="{BG}"/>',
        ]
        if self.sch_inner:
            parts.append(f'<defs><g id="kicad_sch">{self.sch_inner}</g></defs>')
        # 全局小标题
        title = self.cfg.get("title", "")
        if title:
            parts.append(self._ctext(32, 30, title, 12.5, MUTED))

        parts.append('<g id="scenes">')
        if self.only_scene is not None:
            i = self.only_scene
            parts.append(self._scene_group(self.scenes[i], self.starts[i], i))
        else:
            for i, sc in enumerate(self.scenes):
                parts.append(self._scene_group(sc, self.starts[i], i))
        parts.append("</g>")

        parts.append(self._progress(self.only_scene))
        if self.only_scene is None:
            parts.append(self._poster())
        parts.append("</svg>")
        return SEP.join(p for p in parts if p)

    def _scene_group(self, sc, start: float, i: int) -> str:
        end = start + float(sc["duration"])
        if self.static:
            op, anim = 'opacity="1"', ""
        else:
            op, anim = 'opacity="0"', self._scene_window(start, end, 0.2)
        parts = [f'<g id="scene_{i}" {op}>{anim}']
        parts.append(self._ctext(968, 30, f"{i+1:02d} / {len(self.scenes):02d}",
                                 12, MUTED, "end", mono=True))
        parts.append(self._ctext(32, 72, sc["title"], 26, TEXT, weight=600))
        if sc.get("subtitle"):
            parts.append(self._ctext(32, 98, sc["subtitle"], 12.5, MUTED))
        parts.append(f'<line x1="32" y1="106" x2="968" y2="106" stroke="{BORDER}" opacity=".65"/>')
        for j, el in enumerate(sc.get("elements", [])):
            parts.append(self._element(el, sc, start, j))
        parts.append("</g>")
        return SEP.join(p for p in parts if p)

    def _element(self, el, sc, start: float, idx: int) -> str:
        typ = el["type"]
        if typ == "text":
            return self._text_el(el, sc, start)
        if typ == "bullets":
            return self._bullets(el, sc, start)
        if typ == "formula":
            return self._formula(el, sc, start)
        if typ == "schematic":
            return self._schematic(el, sc, start, idx)
        if typ == "plot":
            return self._plot(el, sc, start, idx)
        if typ == "table":
            return self._table(el, sc, start)
        if typ == "answer":
            return self._answer(el, sc, start)
        if typ == "badge":
            return self._badge(el, sc, start)
        if typ == "arrow":
            return self._arrow(el, sc, start)
        if typ == "readout":
            return self._readout(el, sc, start)
        raise scene_mod.SceneError(f"{sc['id']}: 未知元素类型 {typ}")

    # ------------------------------------------------------------ 文本类
    def _text_el(self, el, sc, start: float) -> str:
        x, y = self._pos2(el, sc["layout"], "text")
        size = float(el.get("size", 14))
        color = _color("accent" if el.get("accent") else el.get("color", "text"))
        lines = el.get("lines") or str(el.get("text", "")).split("\n")
        gap = float(el.get("gap", size * 1.45))
        body = "".join(
            self._ctext(x, y + i * gap, line, size, color,
                        el.get("anchor", "start"), mono=bool(el.get("mono")))
            for i, line in enumerate(lines)
        )
        return self._element_vis(el, sc, start, body)

    def _bullets(self, el, sc, start: float) -> str:
        x, y = self._pos2(el, sc["layout"], "bullets")
        size = float(el.get("size", 16))
        gap = float(el.get("gap", 40))
        parts = []
        for i, item in enumerate(el.get("items", [])):
            yy = y + i * gap
            accent = bool(item.get("accent"))
            color = ACCENT if accent else TEXT
            dot = ACCENT if accent else MAIN
            body = (f'<circle cx="{x+4:.1f}" cy="{yy-size*0.32:.1f}" r="3.2" fill="{dot}"/>'
                    + self._ctext(x + 18, yy, item["text"], size, color))
            t0 = start + float(item.get("at", 0))
            t1 = start + float(sc["duration"])
            parts.append(self._vis(body, t0, t1))
        return "".join(parts)

    def _formula(self, el, sc, start: float) -> str:
        x, y = self._pos2(el, sc["layout"], "formula")
        size = float(el.get("size", 18))
        gap = float(el.get("gap", 34))
        parts = []
        for i, line in enumerate(el.get("lines", [])):
            if isinstance(line, dict):
                text = line.get("text", "")
                accent = bool(line.get("accent"))
                at = float(line.get("at", 0))
            else:
                text, accent, at = str(line), False, 0.0
            color = ACCENT if accent else TEXT
            body = self._ctext(x, y + i * gap, text, size, color)
            parts.append(self._vis(body, start + at, start + float(sc["duration"])))
        return "".join(parts)

    def _answer(self, el, sc, start: float) -> str:
        x, y = self._pos2(el, sc["layout"], "answer")
        size = float(el.get("size", 96))
        body = self._ctext(x, y, el.get("text", ""), size, ACCENT, "middle", weight=700)
        if el.get("sub"):
            body += (f'<line x1="{x-120:.1f}" y1="{y+22:.1f}" x2="{x+120:.1f}" '
                     f'y2="{y+22:.1f}" stroke="{BORDER}"/>')
            body += self._ctext(x, y + 54, el["sub"], 19, TEXT, "middle")
        return self._element_vis(el, sc, start, body)

    def _badge(self, el, sc, start: float) -> str:
        x, y = self._pos2(el, sc["layout"], "text")
        text = el.get("text", "")
        size = float(el.get("size", 11))
        color = _color(el.get("kind", "accent"))
        w = max(48.0, len(str(text)) * size * 0.95 + 18)
        h = size + 12
        body = (f'<rect x="{x-w/2:.1f}" y="{y-h/2:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'rx="{h/2:.1f}" fill="{color}" opacity=".14" stroke="{color}" stroke-width="1"/>'
                + self._ctext(x, y + size * 0.35, text, size, color, "middle"))
        return self._element_vis(el, sc, start, body)

    def _arrow(self, el, sc, start: float) -> str:
        x1, y1 = map(float, el["from"])
        x2, y2 = map(float, el["to"])
        color = _color(el.get("color", "accent"))
        ang = math.atan2(y2 - y1, x2 - x1)
        head = 9.0
        p1 = (x2 - head * math.cos(ang - 0.45), y2 - head * math.sin(ang - 0.45))
        p2 = (x2 - head * math.cos(ang + 0.45), y2 - head * math.sin(ang + 0.45))
        body = (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{color}" stroke-width="1.8"/>'
                f'<polygon points="{x2:.1f},{y2:.1f} {p1[0]:.1f},{p1[1]:.1f} '
                f'{p2[0]:.1f},{p2[1]:.1f}" fill="{color}"/>')
        if el.get("text"):
            body += self._ctext((x1 + x2) / 2, (y1 + y2) / 2 - 8,
                                el["text"], float(el.get("size", 11)), color, "middle")
        return self._element_vis(el, sc, start, body)

    def _readout(self, el, sc, start: float) -> str:
        x, y = self._pos2(el, sc["layout"], "text")
        body = self._ctext(x, y, el.get("text", ""), float(el.get("size", 13)),
                           _color(el.get("color", "muted")), el.get("anchor", "start"),
                           mono=True)
        return self._element_vis(el, sc, start, body)

    # ------------------------------------------------------------ 表格
    def _table(self, el, sc, start: float) -> str:
        x, y, w, h = self._pos4(el, sc["layout"], "table")
        size = float(el.get("size", 13))
        rh = float(el.get("row_height", 34))
        hh = float(el.get("header_height", 30))
        cols = el["columns"]
        widths = [float(c.get("width", 1)) for c in cols]
        tot = sum(widths) or 1.0
        widths = [ww * w / tot for ww in widths]
        xs = [x]
        for ww in widths:
            xs.append(xs[-1] + ww)

        parts = [self._panel(x, y, w, h)]
        # header
        for c, x0, ww in zip(cols, xs, widths):
            align = c.get("align", "start")
            if align == "end":
                tx, anchor = x0 + ww - 8, "end"
            elif align == "middle":
                tx, anchor = x0 + ww / 2, "middle"
            else:
                tx, anchor = x0 + 8, "start"
            parts.append(self._ctext(tx, y + hh - 9, c.get("label", ""),
                                     size * 0.9, MUTED, anchor))
        parts.append(f'<line x1="{x:.1f}" y1="{y+hh:.1f}" x2="{x+w:.1f}" '
                     f'y2="{y+hh:.1f}" stroke="{BORDER}"/>')

        for r, row in enumerate(el.get("rows", [])):
            ry = y + hh + r * rh
            content = []
            if row.get("highlight"):
                content.append(f'<rect x="{x+1:.1f}" y="{ry+1:.1f}" width="{w-2:.1f}" '
                               f'height="{rh-2:.1f}" rx="4" fill="{ACCENT}" opacity=".10"/>')
                content.append(f'<rect x="{x+1:.1f}" y="{ry+4:.1f}" width="3" '
                               f'height="{rh-8:.1f}" fill="{ACCENT}"/>')
            for c, cell, x0, ww in zip(cols, row.get("cells", []), xs, widths):
                align = c.get("align", "start")
                if align == "end":
                    tx, anchor = x0 + ww - 8, "end"
                elif align == "middle":
                    tx, anchor = x0 + ww / 2, "middle"
                else:
                    tx, anchor = x0 + 8, "start"
                color = ACCENT if row.get("highlight") else TEXT
                content.append(self._ctext(tx, ry + rh * 0.66, cell, size, color, anchor))
            if r < len(el.get("rows", [])) - 1:
                content.append(f'<line x1="{x:.1f}" y1="{ry+rh:.1f}" x2="{x+w:.1f}" '
                               f'y2="{ry+rh:.1f}" stroke="{GRID}"/>')
            t0 = start + float(row.get("at", 0))
            parts.append(self._vis("".join(content), t0, start + float(sc["duration"])))
        return "".join(parts)

    # ------------------------------------------------------------ 原理图
    def _mm_screen(self, view, pos):
        vx, vy, vw, vh = [float(v) for v in view]
        px, py, pw, ph = [float(v) for v in pos]
        scale = min(pw / vw, ph / vh)
        ox = px + (pw - vw * scale) / 2
        oy = py + (ph - vh * scale) / 2
        return (lambda mx, my: (ox + (float(mx) - vx) * scale,
                                oy + (float(my) - vy) * scale)), scale

    def _schematic(self, el, sc, start: float, idx: int) -> str:
        x, y, w, h = self._pos4(el, sc["layout"], "schematic")
        view = el.get("view", [0, 0, 297, 210])
        sid = sc["id"]
        clip = f"sch_{sid}_{idx}"
        mm, scale = self._mm_screen(view, [x, y, w, h])

        parts = [
            f'<defs><clipPath id="{clip}"><rect x="{x:.1f}" y="{y:.1f}" '
            f'width="{w:.1f}" height="{h:.1f}" rx="6"/></clipPath></defs>',
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="6" fill="{CREAM}" stroke="{BORDER}"/>',
        ]
        if self.sch_inner:
            parts.append(
                f'<g clip-path="url(#{clip})">'
                f'<svg x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'viewBox="{view[0]} {view[1]} {view[2]} {view[3]}" '
                f'preserveAspectRatio="xMidYMid meet">'
                f'<use href="#kicad_sch" xlink:href="#kicad_sch"/>'
                f'</svg></g>'
            )
        else:
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                         f'fill="{CREAM}" opacity=".08"/>')
            parts.append(self._ctext(x + w / 2, y + h / 2, "原理图 SVG 未生成",
                                     13, MUTED, "middle"))

        # 高亮 / 回路 / 标签
        overlay = []
        for hl in el.get("highlights", []):
            box = hl.get("box")
            if not box or len(box) != 4:
                continue
            x0, y0 = mm(box[0], box[1])
            x1, y1 = mm(box[2], box[3])
            rx0, ry0 = min(x0, x1), min(y0, y1)
            rw, rh = abs(x1 - x0), abs(y1 - y0)
            color = SCHEM_SIG if hl.get("style") == "signal" else SCHEM_HL
            dash = ' stroke-dasharray="5 3"' if hl.get("style") == "dashed" else ""
            glow = ""
            if hl.get("style", "glow") == "glow":
                glow = (f'<rect x="{rx0-3:.1f}" y="{ry0-3:.1f}" width="{rw+6:.1f}" '
                        f'height="{rh+6:.1f}" rx="5" fill="none" stroke="{color}" '
                        f'stroke-width="5" opacity=".16"/>')
            body = (glow
                    + f'<rect x="{rx0:.1f}" y="{ry0:.1f}" width="{rw:.1f}" height="{rh:.1f}" '
                      f'rx="4" fill="{color}" fill-opacity=".08" stroke="{color}" '
                      f'stroke-width="2"{dash}/>')
            if hl.get("label"):
                lx = rx0 + rw / 2
                body += (f'<line x1="{lx:.1f}" y1="{ry0-6:.1f}" x2="{lx:.1f}" '
                         f'y2="{ry0-1:.1f}" stroke="{color}" stroke-width="1"/>')
                body += self._schem_label(hl["label"], lx, ry0 - 14, color, center=True)
            t0 = start + float(hl.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(hl.get("until", sc["duration"])))
            overlay.append(self._vis(body, t0, t1))

        for path in el.get("paths", []):
            pts = [mm(px, py) for px, py in path.get("points", [])]
            if len(pts) < 2:
                continue
            color = SCHEM_SIG if path.get("style") == "signal" else SCHEM_HL
            d = "M " + " L ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
            thickness = float(path.get("stroke_width", 2))
            body = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{thickness}" '
                    f'stroke-dasharray="7 5" stroke-linecap="round" opacity=".95">'
                    f'<animate attributeName="stroke-dashoffset" values="0;-24" '
                    f'dur="1s" repeatCount="indefinite"/></path>')
            t0 = start + float(path.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(path.get("until", sc["duration"])))
            overlay.append(self._vis(body, t0, t1))

        for lab in el.get("labels", []):
            lx, ly = mm(*lab.get("at_mm", [0, 0]))
            color = _color(lab.get("color", "text"))
            overlay.append(self._schem_label(lab.get("text", ""), lx, ly, color,
                                             center=lab.get("anchor") == "middle"))
        if overlay:
            parts.append(f'<g clip-path="url(#{clip})">' + "".join(overlay) + "</g>")
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
                     f'rx="6" fill="none" stroke="{BORDER}"/>')
        if el.get("caption"):
            parts.append(self._ctext(x, y + h + 16, el["caption"], 10.5, MUTED))
        return "".join(parts)

    def _schem_label(self, text: str, x: float, y: float, color: str,
                     center=False) -> str:
        size = 10.5
        w = max(22.0, _text_width(text, size) + 12)
        h = size + 7
        bx = x - w / 2 if center else x
        by = y - h / 2
        return (f'<rect x="{bx:.1f}" y="{by:.1f}" width="{w:.1f}" height="{h:.1f}" '
                f'rx="3" fill="#ffffff" fill-opacity=".88" stroke="{color}" stroke-width="1"/>'
                + self._ctext(bx + w / 2, y + size * 0.35, text, size, "#1f2328",
                              "middle"))

    # ------------------------------------------------------------ 绘图
    def _plot(self, el, sc, start: float, idx: int) -> str:
        x, y, w, h = self._pos4(el, sc["layout"], "plot")
        xmin, xmax = [float(v) for v in el["xlim"]]
        ymin, ymax = [float(v) for v in el["ylim"]]
        if xmax == xmin:
            xmax = xmin + 1.0
        if ymax == ymin:
            ymax = ymin + 1.0
        xscale = float(el.get("x_scale", 1.0))
        yscale = float(el.get("y_scale", 1.0))
        pl, pr, pt, pb = 54.0, 18.0, 30.0, 44.0
        px0, px1 = x + pl, x + w - pr
        py0, py1 = y + pt, y + h - pb
        sid = sc["id"]
        clip = f"plot_{sid}_{idx}"

        def mx(v):
            return px0 + (px1 - px0) * (float(v) - xmin) / (xmax - xmin)

        def my(v):
            return py1 - (py1 - py0) * (float(v) - ymin) / (ymax - ymin)

        parts = [
            f'<defs><clipPath id="{clip}"><rect x="{px0:.1f}" y="{py0:.1f}" '
            f'width="{px1-px0:.1f}" height="{py1-py0:.1f}"/></clipPath></defs>',
            self._panel(x, y, w, h),
        ]
        if el.get("title"):
            parts.append(self._ctext(x + 14, y + 20, el["title"], 12.5, MUTED))

        # 网格 + 刻度
        for v in svg_anim._nice_ticks(xmin, xmax, 5):
            X = mx(v)
            parts.append(f'<line x1="{X:.1f}" y1="{py0:.1f}" x2="{X:.1f}" y2="{py1:.1f}" '
                         f'stroke="{GRID}" stroke-dasharray="2 4"/>')
            parts.append(self._ctext(X, py1 + 17, svg_anim._num2(v * xscale, 2),
                                     10.5, MUTED, "middle"))
        for v in svg_anim._nice_ticks(ymin, ymax, 5):
            Y = my(v)
            parts.append(f'<line x1="{px0:.1f}" y1="{Y:.1f}" x2="{px1:.1f}" y2="{Y:.1f}" '
                         f'stroke="{GRID}"/>')
            parts.append(self._ctext(px0 - 7, Y + 4, svg_anim._num2(v * yscale, 1),
                                     10.5, MUTED, "end"))
        parts.append(f'<line x1="{px0:.1f}" y1="{py1:.1f}" x2="{px1:.1f}" y2="{py1:.1f}" '
                     f'stroke="{BORDER}"/>')
        parts.append(f'<line x1="{px0:.1f}" y1="{py0:.1f}" x2="{px0:.1f}" y2="{py1:.1f}" '
                     f'stroke="{BORDER}"/>')
        if el.get("xlabel"):
            parts.append(self._ctext((px0 + px1) / 2, y + h - 10, el["xlabel"],
                                     11.5, MUTED, "middle"))
        if el.get("ylabel"):
            cx, cy = x + 14, (py0 + py1) / 2
            parts.append(f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="{FONT}" '
                         f'font-size="11.5" fill="{MUTED}" text-anchor="middle" '
                         f'transform="rotate(-90 {cx:.1f} {cy:.1f})">'
                         f'{_rich(el["ylabel"], 11.5)}</text>')

        # 阴影带
        for band in el.get("bands", []):
            bx0, bx1 = mx(band["x0"]), mx(band["x1"])
            color = _color(band.get("color", "accent"))
            body = (f'<rect x="{min(bx0,bx1):.1f}" y="{py0:.1f}" width="{abs(bx1-bx0):.1f}" '
                    f'height="{py1-py0:.1f}" fill="{color}" '
                    f'opacity="{float(band.get("opacity",0.10))}"/>')
            t0 = start + float(band.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(band.get("until", sc["duration"])))
            parts.append(self._vis(body, t0, t1))

        # 曲线
        series_parts = []
        label_overlay = []
        for s in el.get("series", []):
            ref = s["ref"]
            d = self.series[ref]
            color = _color(s.get("color", "main"))
            sw = float(s.get("stroke_width", 2))
            style = s.get("style", "line")
            reveal = s.get("reveal")
            t0 = start + float(s.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(s.get("until", sc["duration"])))
            content = []
            if ("x" in d and "y" in d and np.ndim(d["x"]) > 0):
                xs = np.asarray(d["x"], dtype=float)
                ys = np.asarray(d["y"], dtype=float)
                obj = self._series_path(xs, ys, mx, my, color, sw,
                                        s.get("dash") if style == "dash" else None,
                                        reveal, start, t0)
                content.append(obj)
            elif "points" in d:
                for p in d["points"]:
                    cx, cy = mx(p["x"]), my(p["y"])
                    r = float(s.get("r", 4.2))
                    content.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" '
                                   f'fill="{color}" stroke="{BG}" stroke-width="1.2"/>')
                    if p.get("label") and s.get("labels", True):
                        label_overlay.append(self._vis(
                            self._ctext(cx + 8, cy - 8, p["label"],
                                        float(s.get("label_size", 10)), color),
                            t0, t1))
            elif "x" in d and "y" in d:
                cx, cy = mx(d["x"]), my(d["y"])
                r = float(s.get("r", 5))
                content.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r}" '
                               f'fill="{BG}" stroke="{color}" stroke-width="2"/>')
                if s.get("label"):
                    label_overlay.append(self._vis(
                        self._ctext(cx + 8, cy - 8, s["label"],
                                    float(s.get("label_size", 11)), color),
                        t0, t1))
            if content:
                series_parts.append(self._vis("".join(content), t0, t1))
        if series_parts:
            parts.append(f'<g clip-path="url(#{clip})">' + "".join(series_parts) + "</g>")
        parts.extend(label_overlay)

        # 削顶墙
        for lim in el.get("limits", []):
            val = float(lim["value"])
            color = _color(lim.get("color", "limit"))
            dash = lim.get("dash", "6 5")
            if lim.get("axis", "y") == "x":
                X = mx(val)
                body = (f'<line x1="{X:.1f}" y1="{py0:.1f}" x2="{X:.1f}" y2="{py1:.1f}" '
                        f'stroke="{color}" stroke-width="1.3" stroke-dasharray="{dash}" '
                        f'opacity=".85"/>')
                if lim.get("label"):
                    body += self._ctext(X + 5, py0 + 14, lim["label"], 10.5, color)
            else:
                Y = my(val)
                body = (f'<line x1="{px0:.1f}" y1="{Y:.1f}" x2="{px1:.1f}" y2="{Y:.1f}" '
                        f'stroke="{color}" stroke-width="1.3" stroke-dasharray="{dash}" '
                        f'opacity=".85"/>')
                if lim.get("label"):
                    if lim.get("label_anchor") == "start":
                        body += self._ctext(px0 + 4, Y - 6, lim["label"], 10.5, color)
                    else:
                        body += self._ctext(px1 - 4, Y - 6, lim["label"], 10.5, color, "end")
            t0 = start + float(lim.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(lim.get("until", sc["duration"])))
            parts.append(self._vis(body, t0, t1))

        # Q 点投影
        for g in el.get("guides", []):
            p = self.series[g["from"]]
            X, Y = mx(p["x"]), my(p["y"])
            color = _color(g.get("color", "muted"))
            if g.get("axis", "x") == "x":
                body = (f'<line x1="{X:.1f}" y1="{Y:.1f}" x2="{X:.1f}" y2="{py1:.1f}" '
                        f'stroke="{color}" stroke-dasharray="4 4" opacity=".8"/>')
                if g.get("label"):
                    body += self._ctext(X + 5, py1 - 6, g["label"], 10.5, color)
            else:
                body = (f'<line x1="{X:.1f}" y1="{Y:.1f}" x2="{px0:.1f}" y2="{Y:.1f}" '
                        f'stroke="{color}" stroke-dasharray="4 4" opacity=".8"/>')
                if g.get("label"):
                    body += self._ctext(px0 + 5, Y - 6, g["label"], 10.5, color)
            t0 = start + float(g.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(g.get("until", sc["duration"])))
            parts.append(self._vis(body, t0, t1))

        # 斜率三角形
        for tr in el.get("triangles", []):
            p = self.series[tr["origin"]]
            ox, oy = p["x"], p["y"]
            dx, dy = float(tr.get("dx", 1.0)), float(tr.get("dy", 0.0))
            X0, Y0 = mx(ox), my(oy)
            X1, Y1 = mx(ox + dx), my(oy)
            X2, Y2 = mx(ox + dx), my(oy + dy)
            color = _color(tr.get("color", "locus"))
            body = (f'<polygon points="{X0:.1f},{Y0:.1f} {X1:.1f},{Y1:.1f} '
                    f'{X2:.1f},{Y2:.1f}" fill="{color}" opacity=".12" '
                    f'stroke="{color}" stroke-width="1.4"/>')
            labels = tr.get("labels", {})
            if labels.get("dx"):
                body += self._ctext(X0 - 6, Y0 + 20, labels["dx"], 10, color, "end")
            if labels.get("dy"):
                body += self._ctext(X1 + 6, Y2 - 4, labels["dy"], 10, color)
            t0 = start + float(tr.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(tr.get("until", sc["duration"])))
            parts.append(self._vis(body, t0, t1))

        # 数据坐标注释
        for a in el.get("annotations", []):
            if "from" in a:
                p = self.series[a["from"]]
                X = mx(p["x"]) + float(a.get("dx", 8))
                Y = my(p["y"]) + float(a.get("dy", -8))
            else:
                X, Y = mx(a["x"]), my(a["y"])
            color = _color(a.get("color", "muted"))
            body = self._ctext(X, Y, a["text"], float(a.get("size", 10.5)),
                               color, a.get("anchor", "start"))
            t0 = start + float(a.get("at", 0))
            t1 = start + min(float(sc["duration"]), float(a.get("until", sc["duration"])))
            parts.append(self._vis(body, t0, t1))

        return "".join(parts)

    def _series_path(self, xs, ys, mx, my, color, sw, dash, reveal, start, t0):
        d = svg_anim._path(xs, ys, mx, my)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        if self.static:
            return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
                    f'stroke-linecap="round"{dash_attr}/>')
        if reveal:
            L = svg_anim._path_len(xs, ys, mx, my)
            ghost = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
                     f'stroke-linecap="round" opacity=".14"{dash_attr}/>')
            main = (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
                    f'stroke-linecap="round" stroke-dasharray="{L:.1f}" '
                    f'stroke-dashoffset="{L:.1f}"{dash_attr}>'
                    f'{self._draw(L, start + float(reveal[0]), start + float(reveal[1]))}'
                    f'</path>')
            return ghost + main
        return (f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{sw}" '
                f'stroke-linecap="round"{dash_attr}/>')

    # ------------------------------------------------------------ 进度 / 海报
    def _progress(self, active: int | None = None) -> str:
        x0, x1, y, h = 32.0, 968.0, 512.0, 5.0
        parts = [f'<rect x="{x0}" y="{y}" width="{x1-x0}" height="{h}" rx="2.5" fill="{GRID}"/>']
        acc = x0
        for i, sc in enumerate(self.scenes):
            ww = (x1 - x0) * float(sc["duration"]) / self.T
            if active is None:
                t0, t1 = self.starts[i], self.starts[i] + float(sc["duration"])
                seg = (f'<rect x="{acc:.1f}" y="{y}" width="{max(1.0,ww-2):.1f}" '
                       f'height="{h}" rx="2.5" fill="{ACCENT}" opacity=".9"/>')
                parts.append(self._vis(seg, t0, t1, 0.08))
            elif i == active:
                parts.append(f'<rect x="{acc:.1f}" y="{y}" width="{max(1.0,ww-2):.1f}" '
                             f'height="{h}" rx="2.5" fill="{ACCENT}" opacity=".9"/>')
            acc += ww
        if active is None:
            parts.append(f'<circle cx="{x0}" cy="{y+h/2}" r="3.4" fill="{ACCENT}">'
                         f'<animate attributeName="cx" values="{x0};{x1}" '
                         f'dur="{self.T:g}s" repeatCount="indefinite"/></circle>')
        foot = self.cfg.get("footnote", "")
        if foot:
            parts.append(self._ctext(32, 550, foot, 10.5, MUTED))
        return "".join(parts)

    def _first_schematic(self) -> dict | None:
        for sc in self.scenes:
            for el in sc.get("elements", []):
                if el.get("type") == "schematic":
                    return el
        return None

    def _poster(self) -> str:
        old = self.static
        self.static = True
        try:
            ctx = self.ctx
            sc = {"id": "poster", "layout": "card", "duration": 1.0}
            parts = [f'<g id="poster" opacity="1">'
                     f'<animate attributeName="opacity" values="1;0" dur="0.01s" fill="freeze"/>',
                     f'<rect width="{W}" height="{H}" fill="{BG}"/>']
            parts.append(self._ctext(40, 42, self.cfg.get("title", ""), 21, TEXT, weight=600))
            parts.append(self._ctext(40, 64, self.cfg.get("subtitle", ""), 12.5, MUTED))

            sch = self._first_schematic()
            if sch:
                el = {**sch, "pos": [36, 92, 300, 190], "caption": None,
                      "highlights": [{k: v for k, v in h.items() if k != "label"}
                                     for h in sch.get("highlights", [])]}
                parts.append(self._schematic(el, sc, 0, 0))

            plot = {
                "type": "plot", "pos": [356, 92, 300, 220],
                "xlim": [0.0, 0.003], "ylim": [-8.8, 3.6],
                "x_scale": 1000.0, "xlabel": "t (ms)", "ylabel": "u_o (V)",
                "series": [
                    {"ref": "wave.out", "color": "main", "stroke_width": 2.0, "reveal": [0, 0.1]},
                    {"ref": "wave.peaks", "style": "points", "color": "main"},
                ],
                "limits": [
                    {"value": ctx["walls"]["pos"], "label": f"+{ctx['walls']['pos']:.2f} V",
                     "color": "accent", "dash": "3 3"},
                    {"value": ctx["walls"]["neg"], "label": f"{ctx['walls']['neg']:.2f} V",
                     "color": "accent", "dash": "3 3"},
                ],
            }
            parts.append(self._plot(plot, sc, 0, 1))

            rows = [
                {"cells": ["教材", f"+{ctx['textbook']['pos']:.2f}", f"-{ctx['textbook']['neg_abs']:.2f}",
                           f"{ctx['textbook']['rms']:.2f}"], "at": 0},
                {"cells": ["50 mV", f"{ctx['wave']['pk_pos']:+.2f}", f"-{ctx['wave']['pk_neg_abs']:.2f}",
                           f"{ctx['wave']['u_o_rms']:.2f}"], "at": 0},
                {"cells": ["扫描", f"{ctx['walls']['pos']:+.2f}", f"-{ctx['walls']['neg_abs']:.2f}",
                           f"{ctx['walls']['rms']:.2f}"], "at": 0},
            ]
            table = {
                "type": "table", "pos": [676, 92, 292, 188], "size": 10.5,
                "row_height": 34, "header_height": 28,
                "columns": [
                    {"label": "口径", "width": 1.0},
                    {"label": "正峰", "width": 1.0, "align": "end"},
                    {"label": "负峰", "width": 1.0, "align": "end"},
                    {"label": "Uo", "width": 1.0, "align": "end"},
                ],
                "rows": rows,
            }
            parts.append(self._table(table, sc, 0))

            parts.append(self._ctext(822, 350, "A", 64, ACCENT, "middle", weight=700))
            parts.append(self._ctext(822, 386,
                                     f"U_{{o}} ≈ {ctx['answer']['rms_lo']:.2f}–{ctx['answer']['rms_hi']:.2f} Vrms ≈ 2 V",
                                     15, TEXT, "middle"))
            parts.append(self._ctext(40, 330, "U_{o} = min(正峰, |负峰|) / √2", 17, TEXT))
            parts.append(self._ctext(40, 366,
                                     f"≈ {ctx['walls']['peak']:.2f}–{ctx['wave']['pk_pos']:.2f} / 1.414 = "
                                     f"{ctx['walls']['rms']:.2f}–{ctx['wave']['u_o_rms']:.2f} Vrms",
                                     17, ACCENT))
            parts.append(self._ctext(40, 402, "截止侧正峰先碰墙 → 答案 A（2 V）", 14, TEXT))
            parts.append(self._ctext(40, 520,
                                     "数据：ngspice 瞬态 + 自动稳态；原理图：KiCad 导出",
                                     10.5, MUTED))
            parts.append("</g>")
            return "".join(parts)
        finally:
            self.static = old


# ---------------------------------------------------------------- 输出
def _render_png(svg_text: str, path: Path, width=1600) -> None:
    import cairosvg
    from PIL import Image
    preview = svg_text.replace(FONT, CJK_PREVIEW)
    cairosvg.svg2png(bytestring=preview.encode("utf-8"), write_to=str(path),
                     output_width=width, background_color=BG)
    img = Image.open(path)
    img.verify()
    Image.open(path).load()


def validate_svg(svg: str, path: Path, total: float, require_anim=True) -> None:
    import xml.etree.ElementTree as ET
    # 只解析本脚本刚生成的 SVG；仍然拒绝 DTD/实体，避免实体扩展类问题。
    if "<!DOCTYPE" in svg or "<!ENTITY" in svg:
        raise ValueError(f"{path}: 拒绝 DTD/实体声明")
    root = ET.fromstring(svg)
    if not root.tag.endswith("svg"):
        raise ValueError(f"{path}: 根元素不是 svg")
    if "{{" in svg:
        raise ValueError(f"{path}: 残留模板占位符")
    if re.search(r"\b(begin|end)\s*=", svg):
        raise ValueError(f"{path}: 禁止 begin/end（全局时间轴约束）")
    anims = root.findall(".//{http://www.w3.org/2000/svg}animate")
    if require_anim and not anims:
        raise ValueError(f"{path}: 没有任何 animate 元素")
    allowed_dur = {f"{total:g}s", "0.01s", "1s", "1.4s", "1.6s"}
    for a in anims:
        dur = a.get("dur")
        if dur and dur not in allowed_dur:
            raise ValueError(f"{path}: animate dur={dur!r} 不在全局时间轴白名单 {allowed_dur}")
        kt = a.get("keyTimes")
        if not kt:
            continue
        vals = a.get("values")
        if not vals:
            raise ValueError(f"{path}: keyTimes 没有 values")
        ks = [float(x) for x in kt.split(";")]
        vs = vals.split(";")
        if len(ks) != len(vs):
            raise ValueError(f"{path}: keyTimes={len(ks)} 与 values={len(vs)} 数量不一致")
        if abs(ks[0]) > 1e-9 or abs(ks[-1] - 1.0) > 1e-9:
            raise ValueError(f"{path}: keyTimes 必须从 0 到 1")
        for u, v in zip(ks, ks[1:]):
            if v < u - 1e-9:
                raise ValueError(f"{path}: keyTimes 不是非递减: {u} -> {v}")


def build(cdir: Path, out: Path | None = None, sim: dict | None = None,
          png: bool = False, still: str | None = None, sweep: bool = True,
          verbose: bool = True) -> list[Path]:
    cdir = Path(cdir)
    cfg, data = scene_mod.build_data(cdir, sim=sim, sweep=sweep, verbose=verbose)
    target = Path(out) if out else HERE / "out" / cdir.name / f"{cdir.name}_scene.svg"
    target.parent.mkdir(parents=True, exist_ok=True)
    sch = target.parent / f"{cdir.name}.svg"

    written: list[Path] = []
    if still is not None:
        if still == "all":
            indices = list(range(len(cfg["scenes"])))
        else:
            indices = [int(still)]
        for i in indices:
            sc = cfg["scenes"][i]
            r = Renderer(cfg, data, sch, static=True, only_scene=i)
            svg = r.render()
            p = target.parent / f"{cdir.name}_scene_{i+1:02d}_{sc['id']}.svg"
            validate_svg(svg, p, cfg["_total"], require_anim=False)
            p.write_text(svg, encoding="utf-8")
            written.append(p)
            if png:
                png_path = p.with_suffix(".png")
                _render_png(svg, png_path)
                written.append(png_path)
            print(f"  wrote {p} ({p.stat().st_size/1024:.1f} KiB)", flush=True)
        return written

    r = Renderer(cfg, data, sch)
    svg = r.render()
    validate_svg(svg, target, cfg["_total"], require_anim=True)
    target.write_text(svg, encoding="utf-8")
    written.append(target)
    print(f"  wrote {target} ({target.stat().st_size/1024:.1f} KiB)", flush=True)
    if png:
        png_path = target.with_suffix(".png")
        _render_png(svg, png_path)
        written.append(png_path)
        print(f"  wrote {png_path}", flush=True)
    return written


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("circuit", help="电路目录，例如 circuits/q17")
    ap.add_argument("--out", help="输出 SVG 路径（默认 out/<name>/<name>_scene.svg）")
    ap.add_argument("--png", action="store_true", help="顺带输出 PNG 预览")
    ap.add_argument("--still", help="只渲染静态图：all 或场景序号（从 1 开始）")
    ap.add_argument("--no-sweep", action="store_true", help="跳过幅值族扫描")
    ap.add_argument("--check", action="store_true", help="只校验数据和模板，不写文件")
    ns = ap.parse_args(argv)

    cdir = Path(ns.circuit)
    if ns.check:
        cfg, data = scene_mod.build_data(cdir, sweep=not ns.no_sweep)
        ctx = data["context"]
        print(f"OK: {len(cfg['scenes'])} scenes, {cfg['_total']:.2f}s, "
              f"{len(data['series'])} series")
        print(f"  Q = {ctx['op']['u_ceq']:.3f} V / {ctx['op']['icq']:.3f} mA")
        print(f"  walls = {ctx['walls']['pos']:+.3f} / {ctx['walls']['neg']:+.3f} V")
        print(f"  answer = {ctx['answer']['rms_lo']:.2f}–{ctx['answer']['rms_hi']:.2f} Vrms")
        return 0

    still = ns.still
    if still is not None and still != "all":
        still = str(int(still) - 1)
    build(cdir, Path(ns.out) if ns.out else None, png=ns.png,
          still=still, sweep=not ns.no_sweep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
