#!/usr/bin/env python3
"""场景数据层：读 scene.json、校验、跑/复用仿真、构建 context 与 series。

渲染由 scene_anim.py 负责；本模块不产生 SVG。

用法（库）:
    import scene
    cfg, data = scene.build_data(Path("circuits/q17"))

    或命令行只看数据:
    python3 scene.py circuits/q17 --check
"""
from __future__ import annotations

import copy
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import tran  # noqa: E402

LAYOUTS = {"card", "plot", "derivation", "compare", "summary"}
ELEMENT_TYPES = {
    "text", "bullets", "formula", "schematic", "plot", "table",
    "answer", "badge", "arrow", "readout",
}
_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][\w.]*)\s*(?::([^}]+))?\s*\}\}")
_SI = {"t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3, "m": 1e-3,
       "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}


class SceneError(ValueError):
    """scene.json / simulation data 不一致。"""


# ---------------------------------------------------------------- schema
def load(cdir: Path, spec: dict | None = None) -> dict:
    cdir = Path(cdir)
    path = cdir / "scene.json"
    if not path.exists():
        raise SceneError(f"{cdir} 下没有 scene.json")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if spec is None:
        sp = cdir / "spec.json"
        spec = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
    validate_cfg(cfg)
    cfg["_spec"] = spec
    cfg["_cdir"] = str(cdir)
    return cfg


def validate_cfg(cfg: dict) -> None:
    if cfg.get("version") != 1:
        raise SceneError(f"scene.json version 必须是 1，得到 {cfg.get('version')!r}")
    scenes = cfg.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise SceneError("scene.json 至少需要一个 scenes[]")
    seen = set()
    for i, sc in enumerate(scenes):
        where = f"scenes[{i}]"
        sid = sc.get("id")
        if not sid or not isinstance(sid, str):
            raise SceneError(f"{where} 缺少字符串 id")
        if sid in seen:
            raise SceneError(f"{where} id 重复: {sid}")
        seen.add(sid)
        if sc.get("layout") not in LAYOUTS:
            raise SceneError(f"{where} layout 非法: {sc.get('layout')!r}")
        dur = _number(sc.get("duration"), f"{where}.duration")
        if dur <= 0:
            raise SceneError(f"{where}.duration 必须 > 0")
        if not sc.get("title"):
            raise SceneError(f"{where} 缺少 title")
        _validate_elements(sc.get("elements", []), where)

    total = sum(_number(sc["duration"], f"{sc['id']}.duration") for sc in scenes)
    declared = cfg.get("total_duration")
    if declared is not None:
        declared = _number(declared, "total_duration")
        if abs(declared - total) > 0.01:
            raise SceneError(
                f"total_duration={declared:.3f}s 与各场景之和 {total:.3f}s 不一致"
            )
        total = declared
    starts, acc = [], 0.0
    for sc in scenes:
        starts.append(acc)
        acc += float(sc["duration"])
    cfg["_total"] = total
    cfg["_starts"] = starts


def _validate_elements(elements, where: str) -> None:
    if not isinstance(elements, list):
        raise SceneError(f"{where}.elements 必须是数组")
    for j, el in enumerate(elements):
        w = f"{where}.elements[{j}]"
        typ = el.get("type")
        if typ not in ELEMENT_TYPES:
            raise SceneError(f"{w} type 非法: {typ!r}")
        if "pos" in el:
            pos = el["pos"]
            if not isinstance(pos, list) or len(pos) not in (2, 4):
                raise SceneError(f"{w}.pos 必须是 [x,y] 或 [x,y,w,h]")
            for v in pos:
                _number(v, f"{w}.pos")
        for key in ("at", "until"):
            if key in el:
                _number(el[key], f"{w}.{key}")
        if typ == "plot":
            for s in el.get("series", []):
                if not s.get("ref"):
                    raise SceneError(f"{w}.series 缺少 ref")
            if not el.get("xlim") or not el.get("ylim"):
                raise SceneError(f"{w} 缺少 xlim/ylim")
        elif typ == "schematic":
            view = el.get("view")
            if not isinstance(view, list) or len(view) != 4:
                raise SceneError(f"{w}.view 必须是 [vx,vy,vw,vh]（mm）")
        elif typ == "table":
            if not el.get("columns") or not el.get("rows"):
                raise SceneError(f"{w} 缺少 columns/rows")
        elif typ == "bullets":
            if not el.get("items"):
                raise SceneError(f"{w} 缺少 items")


def _number(v, what: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise SceneError(f"{what} 不是数字: {v!r}") from None


def validate_series_refs(cfg: dict, series: dict) -> None:
    missing = []
    for sc in cfg["scenes"]:
        for el in sc.get("elements", []):
            if el.get("type") != "plot":
                continue
            for s in el.get("series", []):
                if s["ref"] not in series:
                    missing.append(f"{sc['id']}: plot.series {s['ref']}")
            for g in el.get("guides", []):
                if g.get("from") not in series:
                    missing.append(f"{sc['id']}: guide {g.get('from')}")
            for tr in el.get("triangles", []):
                if tr.get("origin") not in series:
                    missing.append(f"{sc['id']}: triangle {tr.get('origin')}")
            for a in el.get("annotations", []):
                if "from" in a and a["from"] not in series:
                    missing.append(f"{sc['id']}: annotation {a['from']}")
    if missing:
        raise SceneError("未知 series 引用:\n  " + "\n  ".join(missing))


# ---------------------------------------------------------------- 网表
def _spice_value(s) -> float:
    """'3k' -> 3000.0，'10u' -> 1e-5，'1meg' -> 1e6。"""
    text = str(s).strip()
    m = re.fullmatch(r"([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]+)?", text)
    if not m:
        raise SceneError(f"无法解析 SPICE 数值: {s!r}")
    val = float(m.group(1))
    suf = (m.group(2) or "").lower()
    if suf:
        if suf.startswith("meg"):
            val *= 1e6
        elif suf[0] in _SI:
            val *= _SI[suf[0]]
        else:
            raise SceneError(f"未知 SPICE 后缀: {s!r}")
    return val


def _components(netlist: str) -> dict[str, str]:
    out = {}
    for line in netlist.splitlines():
        s = line.strip()
        if not s or s.startswith(("*", ".", "+")):
            continue
        parts = s.split()
        if len(parts) >= 4:
            out[parts[0]] = parts[3]
    return out


def _emitter_node(netlist: str, qname: str) -> str | None:
    for line in netlist.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].lower() == qname.lower():
            return parts[3]
    return None


def _collector_node(netlist: str, qname: str) -> str | None:
    for line in netlist.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].lower() == qname.lower():
            return parts[1]
    return None


def _dc_op(netlist: str, coll: str, emit: str | None, vcc_src: str) -> tuple[float, float]:
    """用 .op 取静态工作点（SIN 源按 DC 0 处理），返回 (U_CEQ, I_CQ mA)。"""
    body = re.sub(r"(?im)^\s*\.(end|tran|op|ac|dc)\b[^\n]*$", "", netlist)
    vectors = [f"v({coll})"]
    if emit and emit != "0":
        vectors.append(f"v({emit})")
    vectors.append(f"i({vcc_src})")

    def fname(v: str) -> str:
        return re.sub(r"[^\w]", "_", v)

    script = (
        body
        + "\n.op\n.control\nop\n"
        + "".join(f"wrdata {fname(v)}.dat {v}\n" for v in vectors)
        + "quit\n.endc\n.end\n"
    )
    with tempfile.TemporaryDirectory() as td:
        cir = Path(td) / "op.cir"
        cir.write_text(script)
        r = subprocess.run(["ngspice", "-b", str(cir)], capture_output=True, text=True, cwd=td)
        vals = {}
        for v in vectors:
            f = Path(td) / f"{fname(v)}.dat"
            if not f.exists():
                break
            arr = np.loadtxt(f, ndmin=2)
            if arr.ndim == 2 and arr.shape[1] >= 2:
                vals[v] = float(arr[-1, 1])
        if len(vals) != len(vectors):
            raise SceneError(f"ngspice .op 失败: {r.stdout[-500:]} {r.stderr[-300:]}")
    u_ce = vals[f"v({coll})"] - (vals[f"v({emit})"] if emit and emit != "0" else 0.0)
    return u_ce, -vals[f"i({vcc_src})"] * 1000.0


def _circuit_values(netlist: str, data: dict) -> dict[str, float]:
    comps = _components(netlist)
    mapping = data.get("circuit") or {}
    defaults = {"vcc": "VCC", "rc": "R2", "rl": "R3"}
    overrides = mapping.get("values") or {}

    def pick(name: str) -> float:
        if name in overrides:
            return float(overrides[name])
        ref = mapping.get(name, defaults[name])
        if ref in comps:
            return _spice_value(comps[ref])
        raise SceneError(
            f"网表里找不到 {name} 对应的器件 {ref!r}；"
            f"请在 scene.json data.circuit.values 里显式给出"
        )

    return {"vcc": pick("vcc"), "rc": pick("rc"), "rl": pick("rl")}


# ---------------------------------------------------------------- 仿真
def collect(cdir: Path, cfg: dict, sim: dict | None = None,
            sweep: bool = True, verbose: bool = True) -> dict:
    cdir = Path(cdir)
    spec = cfg.get("_spec") or {}
    data = cfg.get("data") or {}
    anim = spec.get("anim") or {}

    netlist = tran.ensure_title((cdir / "netlist.cir").read_text(encoding="utf-8"))
    source = data.get("source") or anim.get("source") or "V1"
    amp = float(data.get("amp", anim.get("amp", 0.05)))
    freq = float(data.get("freq", anim.get("freq", 1000)))
    cycles = int(data.get("cycles", 20))
    window_cycles = int(data.get("window_cycles", anim.get("window_cycles", 3)))
    ppc = int(data.get("points_per_cycle", 400))
    probe_cfg = data.get("probe") or {}
    qname = probe_cfg.get("q", "Q1")
    probe_src = probe_cfg.get("source", "VIC")

    probe_netlist, _ = tran.add_current_probe(netlist, qname, probe_src)
    em_node = _emitter_node(netlist, qname)
    vectors = ["v(out)", "v(in)", "v(c)", f"i({probe_src})"]
    em_key = None
    if em_node and em_node != "0":
        em_key = f"v({em_node})"
        vectors.append(em_key)

    if verbose:
        print(f"  scene: tran {amp*1000:g} mV / {freq:g} Hz, window {window_cycles} cycles",
              flush=True)
    wf = tran.waveform(
        probe_netlist, vectors, freq, amp=amp, source=source,
        cycles=cycles, window_cycles=window_cycles,
        points_per_cycle=ppc, settle_vector="v(out)",
    )
    t = wf["t"]
    y = wf["y"]
    if "v(out)" not in y or "v(in)" not in y or "v(c)" not in y or f"i({probe_src})" not in y:
        raise SceneError(f"瞬态缺少必需向量，实际得到: {sorted(y)}")
    vo = y["v(out)"]
    vi = y["v(in)"]
    vc = y["v(c)"]
    ic = y[f"i({probe_src})"]
    ve = y.get(em_key, np.zeros_like(vc)) if em_key else np.zeros_like(vc)

    u_ce = vc - ve
    ic_ma = -ic * 1000.0
    u_ce_mean = float(np.mean(u_ce))
    ic_mean = float(np.mean(ic_ma))

    ipos = int(np.argmax(vo))
    ineg = int(np.argmin(vo))
    pk_pos = float(vo[ipos])
    pk_neg = float(vo[ineg])
    u_o_peak = min(pk_pos, abs(pk_neg))
    u_o_rms = u_o_peak / math.sqrt(2.0)

    coll = _collector_node(netlist, qname)
    if not coll:
        raise SceneError(f"网表里找不到晶体管 {qname} 的集电极节点")
    vcc_ref = (data.get("circuit") or {}).get("vcc", "VCC")
    try:
        u_ceq, icq = _dc_op(probe_netlist, coll, em_node, vcc_ref)
        op_src = ".op"
    except SceneError as exc:
        u_ceq, icq = u_ce_mean, ic_mean
        op_src = f"waveform mean ({exc})"

    # 增量轨迹仍以末窗均值为原点，和原 svg_anim 的画法一致
    locus = {"x": u_ce - u_ce_mean, "y": ic_ma - ic_mean}

    # ---- 削顶墙：run.py 已经扫过就直接复用，独立运行时自己扫
    walls_raw = _sim_result(sim, "clipping_wall")
    if walls_raw is None:
        cw = spec.get("clipping_wall") or {}
        if verbose:
            print("  scene: clipping_wall scan ...", flush=True)
        walls_raw = tran.clipping_wall(
            netlist,
            cw.get("source", source),
            cw.get("node", "out"),
            cw.get("freq", freq),
            amp_start=cw.get("amp_start", 1e-3),
        )
    walls_pos = float(walls_raw["positive_peak_V"])
    walls_neg_abs = float(walls_raw["negative_peak_V"])
    walls_neg = -walls_neg_abs
    walls_peak = float(walls_raw["wall_peak_V"])
    walls_rms = float(walls_raw["wall_rms_V"])

    limits = anim.get("limits") or []
    tb_pos = next((float(l["value"]) for l in limits if float(l["value"]) > 0), walls_pos)
    tb_neg = next((float(l["value"]) for l in limits if float(l["value"]) < 0), walls_neg)
    tb_peak = min(tb_pos, abs(tb_neg))
    tb_rms = tb_peak / math.sqrt(2.0)

    circuit = _circuit_values(netlist, data)
    vcc, rc, rl = circuit["vcc"], circuit["rc"], circuit["rl"]
    if rc <= 0 or rl <= 0:
        raise SceneError("Rc/RL 必须 > 0")
    rac = rc * rl / (rc + rl)
    calc = {
        "rac_ohm": rac,
        "rac_kohm": rac / 1000.0,
        "slope_ma_v": -1000.0 / rac,
        "slope_abs_ma_v": 1000.0 / rac,
        "ic_dc_max_ma": vcc / rc * 1000.0,
        "u_ce_cutoff_v": u_ceq + icq * rac / 1000.0,
        "ic_ac_max_ma": icq + u_ceq / rac * 1000.0,
    }

    context = {
        "op": {"u_ceq": u_ceq, "icq": icq,
               "u_ce_transient": u_ce_mean, "ic_transient": ic_mean},
        "calc": calc,
        "wave": {
            "pk_pos": pk_pos,
            "pk_neg": pk_neg,
            "pk_neg_abs": abs(pk_neg),
            "t_pk_pos": float(t[ipos]),
            "t_pk_neg": float(t[ineg]),
            "u_o_peak": u_o_peak,
            "u_o_rms": u_o_rms,
            "rms": float(np.sqrt(np.mean(vo ** 2))),
            "vpp": float(vo.max() - vo.min()),
        },
        "walls": {
            "pos": walls_pos,
            "neg": walls_neg,
            "neg_abs": walls_neg_abs,
            "peak": walls_peak,
            "rms": walls_rms,
        },
        "textbook": {
            "pos": tb_pos,
            "neg": tb_neg,
            "neg_abs": abs(tb_neg),
            "peak": tb_peak,
            "rms": tb_rms,
        },
        "answer": {
            "rms_lo": min(walls_rms, u_o_rms),
            "rms_hi": max(walls_rms, u_o_rms),
        },
        "data": {
            "amp_v": amp,
            "amp_mv": amp * 1000.0,
            "freq_hz": freq,
            "window_cycles": window_cycles,
        },
        "circuit": {
            "vcc": vcc,
            "rc_ohm": rc,
            "rl_ohm": rl,
            "rc_kohm": rc / 1000.0,
            "rl_kohm": rl / 1000.0,
        },
    }

    series: dict[str, dict] = {
        "wave.out": {"x": t, "y": vo},
        "wave.in": {"x": t, "y": vi},
        "wave.in_mv": {"x": t, "y": vi * 1000.0},
        "locus": locus,
        "qpoint": {"x": u_ceq, "y": icq},
        "wave.peaks": {"points": [
            {"x": float(t[ipos]), "y": pk_pos, "label": f"{pk_pos:+.2f} V"},
            {"x": float(t[ineg]), "y": pk_neg, "label": f"{pk_neg:+.2f} V"},
        ]},
        "load.dc": {"x": np.array([0.0, vcc]), "y": np.array([calc["ic_dc_max_ma"], 0.0])},
        "load.ac": {"x": np.array([calc["u_ce_cutoff_v"], 0.0]),
                    "y": np.array([0.0, calc["ic_ac_max_ma"]])},
    }

    # ---- 幅值族：看正峰先长后压、负峰继续变深
    if sweep and data.get("sweep"):
        sc = data["sweep"]
        amps = [float(a) for a in sc.get("amplitudes", [])]
        sw_wc = int(sc.get("window_cycles", 1))
        sw_ppc = int(sc.get("points_per_cycle", 200))
        for a in amps:
            label = _amp_label(a)
            if verbose:
                print(f"  scene: sweep {a*1000:g} mV ...", flush=True)
            sw = tran.waveform(
                probe_netlist, ["v(out)"], freq, amp=a, source=source,
                cycles=cycles, window_cycles=sw_wc,
                points_per_cycle=sw_ppc, settle_vector="v(out)",
            )
            ty = sw["y"]["v(out)"]
            kp = int(np.argmax(ty))
            kn = int(np.argmin(ty))
            series[f"sweep.out.{label}"] = {"x": sw["t"], "y": ty}
            series[f"sweep.peaks.{label}"] = {
                "points": [
                    {"x": float(sw["t"][kp]), "y": float(ty[kp]),
                     "label": f"{ty[kp]:+.2f} V"},
                    {"x": float(sw["t"][kn]), "y": float(ty[kn]),
                     "label": f"{ty[kn]:+.2f} V"},
                ]
            }
            context.setdefault("sweep", {})[label] = {
                "amp_mv": a * 1000.0,
                "pk_pos": float(ty[kp]),
                "pk_neg": float(ty[kn]),
                "pk_neg_abs": abs(float(ty[kn])),
                "u_o_rms": min(float(ty[kp]), abs(float(ty[kn]))) / math.sqrt(2.0),
            }

    if verbose:
        print(
            f"  scene: Q {u_ceq:.3f} V / {icq:.3f} mA [{op_src}]; "
            f"transient mean {u_ce_mean:.3f} V / {ic_mean:.3f} mA; "
            f"50mV peaks {pk_pos:+.2f}/{pk_neg:+.2f} V; "
            f"walls {walls_pos:+.3f}/{walls_neg:+.3f} V; settled={wf['settled']}",
            flush=True,
        )

    return {
        "context": context,
        "series": series,
        "settled": wf["settled"],
        "attempts": wf["attempts"],
    }


def _sim_result(sim: dict | None, key: str) -> dict | None:
    if not isinstance(sim, dict):
        return None
    v = sim.get(key)
    if isinstance(v, dict):
        return v
    if key == "clipping_wall" and "positive_peak_V" in sim:
        return sim
    return None


def _amp_label(amp: float) -> str:
    mv = amp * 1000.0
    if abs(mv - round(mv)) < 1e-9:
        return f"{int(round(mv))}m"
    return f"{mv:g}m"


# ---------------------------------------------------------------- 模板
def render_context(cfg: dict, context: dict) -> dict:
    """复制 cfg，只替换展示字段里的 {{path[:fmt]}}；不碰 _spec/data。"""
    out = copy.deepcopy(cfg)
    for key in ("title", "subtitle", "footnote"):
        if key in out and isinstance(out[key], str):
            out[key] = render_text(out[key], context)
    for sc in out.get("scenes", []):
        for key in ("title", "subtitle"):
            if key in sc and isinstance(sc[key], str):
                sc[key] = render_text(sc[key], context)
        sc["elements"] = _render_value(sc.get("elements", []), context)
    return out


def _render_value(v, context: dict):
    if isinstance(v, str):
        return render_text(v, context)
    if isinstance(v, list):
        return [_render_value(x, context) for x in v]
    if isinstance(v, dict):
        return {k: _render_value(x, context) for k, x in v.items()}
    return v


def render_text(text: str, context: dict) -> str:
    def repl(m):
        path = m.group(1)
        fmt = m.group(2)
        val = _resolve(path, context)
        if fmt:
            try:
                return format(val, fmt)
            except (TypeError, ValueError) as exc:
                raise SceneError(f"占位符 {{{{...}}}} 格式错误 {path}:{fmt} ({exc})") from None
        return str(val)

    return _TEMPLATE.sub(repl, text)


def _resolve(path: str, context: dict):
    cur = context
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise SceneError(f"占位符路径不存在: {path!r}")
        cur = cur[part]
    return cur


def check_context(cfg: dict) -> None:
    """渲染后确认没有残留占位符（render_text 已经负责缺 key 报错）。"""
    def walk(v, where: str):
        if isinstance(v, str):
            if "{{" in v:
                raise SceneError(f"{where}: 残留占位符 {v!r}")
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, f"{where}[{i}]")
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(x, f"{where}.{k}")

    for sc in cfg.get("scenes", []):
        walk(sc, f"scene {sc.get('id')}")
    for key in ("title", "subtitle", "footnote"):
        walk(cfg.get(key, ""), key)


def build_data(cdir: Path, spec: dict | None = None, sim: dict | None = None,
               sweep: bool = True, verbose: bool = True) -> tuple[dict, dict]:
    cfg = load(cdir, spec)
    data = collect(cdir, cfg, sim=sim, sweep=sweep, verbose=verbose)
    validate_series_refs(cfg, data["series"])
    cfg2 = render_context(cfg, data["context"])
    check_context(cfg2)
    return cfg2, data


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cdir = Path(argv[0])
    sweep = "--no-sweep" not in argv
    cfg, data = build_data(cdir, sweep=sweep)
    ctx = data["context"]
    print(f"scenes: {len(cfg['scenes'])}, total {cfg['_total']:.2f}s")
    for sc, start in zip(cfg["scenes"], cfg["_starts"]):
        print(f"  {start:5.2f}s  {sc['duration']:4.2f}s  {sc['id']:<10} {sc['layout']:<10} {sc['title']}")
    print("context:")
    for k in ("op", "calc", "wave", "walls", "textbook", "answer", "data", "circuit"):
        print(f"  {k}: {json.dumps(ctx[k], ensure_ascii=False, default=float)}")
    print(f"series: {', '.join(sorted(data['series']))}")
    print(f"settled={data['settled']} attempts={data['attempts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
