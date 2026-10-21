#!/usr/bin/env python3
"""瞬态分析与最大不失真输出扫描（ngspice 批处理后端）。

op/ac 走 SPICEBridge MCP；瞬态需要反复改幅值、跳稳态、算 THD，
用 MCP 一层层包不划算，这里直接调 ngspice -b（工作流本来就依赖 ngspice）。

自动稳态：只取最后若干个周期做测量；若最后两周期峰值相差 >2%，
自动加倍仿真时长重试（上限 8 次），避免被耦合电容充电时间常数骗过。

用法（库）:
    from tran import ensure_title, run_tran, max_undistorted
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np

NGSPICE = "ngspice"
_WARNED: set[str] = set()


def ensure_title(netlist: str) -> str:
    """ngspice 无条件把第一行当标题卡；若首行是元件会被吞掉。自动补一行。"""
    first = next((ln for ln in netlist.splitlines() if ln.strip()), "")
    if first.lstrip().startswith("*"):
        return netlist
    return "* spicebridge-kicad-workflow\n" + netlist


def set_sin(netlist: str, source: str, amp: float, freq: float) -> str:
    """把 source 行的 SIN( off amp freq ... ) 的幅值/频率替换掉。"""
    line_pat = re.compile(r"^(" + re.escape(source) + r"\b)(.*)$", re.IGNORECASE | re.MULTILINE)
    sin_pat = re.compile(r"(SIN\(\s*[+\-\w.]+\s+)([+\-\w.]+)(\s+)([+\-\w.]+)")

    def repl_line(m):
        head, rest = m.group(1), m.group(2)
        new_rest, n = sin_pat.subn(lambda s: f"{s.group(1)}{amp}{s.group(3)}{freq}", rest)
        if n == 0:
            raise ValueError(f"网表里 {source} 行没有 SIN() 参数")
        return head + new_rest

    new, n = line_pat.subn(repl_line, netlist, count=1)
    if n == 0:
        raise ValueError(f"网表里找不到源 {source}")
    return new


def run_tran(netlist: str, node: str, freq: float, amp: float | None = None,
             source: str | None = None, cycles: int = 40, tstep_per_cycle: int = 1000,
             max_retries: int = 8) -> dict:
    """跑瞬态并自动等待稳态，返回最后几个周期的峰值与 THD。"""
    if amp is not None and source:
        netlist = set_sin(netlist, source, amp, freq)
    dt = 1.0 / (freq * tstep_per_cycle)
    total = cycles / freq
    last_stats = None
    for attempt in range(max_retries):
        data = _ngspice_tran(netlist, dt, total)
        t, v = data[node]
        stats = _analyze_last(t, v, freq, min(5, cycles // 2) or 1, tstep_per_cycle)
        if last_stats and _settled(last_stats, stats):
            stats["settled"] = True
            stats["attempts"] = attempt + 1
            return stats
        last_stats = stats
        total *= 2
        cycles *= 2
    stats["settled"] = False
    return stats


def _settled(a: dict, b: dict) -> bool:
    ref = max(abs(a["peak_max"]), abs(a["peak_min"]), 1e-9)
    return max(abs(a["peak_max"] - b["peak_max"]), abs(a["peak_min"] - b["peak_min"])) / ref < 0.02


def _analyze_last(t: np.ndarray, v: np.ndarray, freq: float, cycles: int, spc: int) -> dict:
    """重采样为等间隔后取尾部整数个周期；每行恰好一个信号周期 -> 基波落在 bin 1。"""
    t_end = t[-1]
    grid = np.linspace(t_end - cycles / freq, t_end, cycles * spc, endpoint=False)
    y = np.interp(grid, t, v)
    one = y[-spc:]
    spec = np.fft.rfft(one - one.mean())
    mag = np.abs(spec)
    fundamental = mag[1] if len(mag) > 1 else 0.0
    harm = [mag[h] for h in range(2, min(11, len(mag)))]
    thd = 100.0 * float(np.sqrt(sum(h * h for h in harm))) / fundamental if fundamental > 0 else 0.0
    return {"peak_max": float(one.max()), "peak_min": float(one.min()),
            "peak_pp": float(one.max() - one.min()),
            "thd_percent": thd, "rms": float(np.sqrt(np.mean(one ** 2)))}


def _ngspice_tran(netlist: str, dt: float, total: float) -> dict[str, np.ndarray]:
    """执行 .tran，用 wrdata 导出全部节点，返回 {node: samples}。"""
    body = re.sub(r"(?im)^\s*\.end\s*$", "", netlist)
    body = re.sub(r"(?im)^\s*\.tran[^\n]*$", "", body)
    vectors = _voltage_nodes(netlist)
    script = (
        body
        + f"\n.tran {dt:g} {total:g}\n.control\nrun\n"
        + "".join(f"wrdata {n}.dat v({n})\n" for n in vectors)
        + "quit\n.endc\n.end\n"
    )
    with tempfile.TemporaryDirectory() as td:
        cir = Path(td) / "t.cir"
        cir.write_text(script)
        r = subprocess.run([NGSPICE, "-b", str(cir)], capture_output=True, text=True, cwd=td)
        out = {}
        for n in vectors:
            f = Path(td) / f"{n}.dat"
            if f.exists():
                arr = np.loadtxt(f)
                out[n] = (arr[:, 0], arr[:, 1]) if arr.ndim == 2 else (None, arr)
        if not out:
            raise RuntimeError(f"ngspice 瞬态失败: {r.stdout[-800:]} {r.stderr[-400:]}")
        return out


def _voltage_nodes(netlist: str) -> list[str]:
    nodes = set()
    for line in netlist.splitlines():
        s = line.strip()
        if not s or s.startswith(("*", ".", "+")):
            continue
        parts = s.split()
        head = parts[0][0].upper()
        if head == "Q":
            toks = parts[1:4]          # 只有 c b e 是节点，第 4 项是模型名
        elif head in "RCILVI":
            toks = parts[1:4]
        else:
            continue
        for tok in toks:
            if re.fullmatch(r"[A-Za-z_][\w.]*", tok):
                nodes.add(tok)
    nodes.discard("0")
    return sorted(nodes)


def add_current_probe(netlist: str, qname: str, source: str = "VIC") -> tuple[str, str]:
    """在晶体管集电极串一支 0 V 电流表，返回 (新网表, 电流向量表达式)。

    0 V 源不改变任何工作点，但能让 ngspice 直接给出 i_c(t)。
    （@q1[ic] 只在工作点求值，瞬态里是常数，不能用来画轨迹。）
    """
    lines = netlist.splitlines()
    for i, line in enumerate(lines):
        toks = line.split()
        if len(toks) >= 4 and toks[0].lower() == qname.lower():
            node = toks[1] + "__ic"
            toks[1] = node
            lines[i] = " ".join(toks)
            src = f"{source} {node} {toks[1][:-4]} 0"
            for j, ln in enumerate(lines):
                if ln.strip().lower().startswith(".end"):
                    lines.insert(j, src)
                    break
            else:
                lines.append(src)
            return "\n".join(lines), f"i({source})"
    raise ValueError(f"网表里找不到晶体管 {qname}")


def waveform(netlist: str, vectors: list[str], freq: float, amp: float | None = None,
             source: str | None = None, cycles: int = 40, window_cycles: int = 3,
             points_per_cycle: int = 400, settle_vector: str | None = None,
             max_retries: int = 8) -> dict:
    """跑瞬态直到稳态，返回末尾 window_cycles 个周期的等间隔波形（给 SVG 动画用）。

    vectors 是 ngspice 向量表达式，例如 ["v(out)", "i(vcc)"]；
    返回 {"t": 秒（从 0 起）, "y": {表达式: ndarray}, "settled", "attempts"}。
    """
    if amp is not None and source:
        netlist = set_sin(netlist, source, amp, freq)
    dt = 1.0 / (freq * 1000)
    total = cycles / freq
    settle = settle_vector or vectors[0]
    data, settled, attempts, last = {}, False, 0, None
    for attempt in range(max_retries):
        data = _wrdata(netlist, vectors, dt, total)
        if settle not in data:
            raise RuntimeError(f"稳态判据向量 {settle} 不存在；网表里没有这个节点")
        t, v = data[settle]
        stats = _analyze_last(t, v, freq, min(5, max(1, cycles // 2)), 1000)
        attempts = attempt + 1
        if last is not None and _settled(last, stats):
            settled = True
            break
        last, total, cycles = stats, total * 2, cycles * 2
    grid = np.linspace(t[-1] - window_cycles / freq, t[-1],
                       window_cycles * points_per_cycle, endpoint=False)
    y = {k: np.interp(grid, t, v) for k, (t, v) in data.items()}
    return {"t": grid - grid[0], "y": y, "settled": settled, "attempts": attempts}


def _wrdata(netlist: str, vectors: list[str], dt: float, total: float):
    """执行 .tran 并用 wrdata 把指定向量导出为文本列。"""
    body = re.sub(r"(?im)^\s*\.end\s*$", "", netlist)
    body = re.sub(r"(?im)^\s*\.tran[^\n]*$", "", body)

    def fname(v: str) -> str:
        return re.sub(r"[^\w]", "_", v)

    script = (
        body
        + f"\n.tran {dt:g} {total:g}\n.control\nrun\n"
        + "".join(f"wrdata {fname(v)}.dat {v}\n" for v in vectors)
        + "quit\n.endc\n.end\n"
    )
    with tempfile.TemporaryDirectory() as td:
        cir = Path(td) / "t.cir"
        cir.write_text(script)
        r = subprocess.run([NGSPICE, "-b", str(cir)], capture_output=True, text=True, cwd=td)
        data = {}
        for v in vectors:
            f = Path(td) / f"{fname(v)}.dat"
            if not f.exists():
                continue
            arr = np.loadtxt(f)
            if arr.ndim == 2 and arr.shape[1] >= 2:
                data[v] = (arr[:, 0], arr[:, 1])
        missing = [v for v in vectors if v not in data and v not in _WARNED]
        if missing:
            # 例如 v(e) 而发射极直接接 0：该节点不存在，调用方按 0 处理
            _WARNED.update(missing)
            print(f"  note: 向量 {', '.join(missing)} 不存在（按 0 处理）", flush=True)
        return data


def max_undistorted(netlist: str, source: str, node: str, freq: float,
                    thd_limit: float = 1.0, amp_lo: float = 1e-3,
                    amp_hi_cap: float = 10.0) -> dict:
    """几何加倍 + 二分：找 THD 首次超过 thd_limit 时的输入幅值与输出幅度。"""
    lo, hi = None, None
    amp = amp_lo
    probe = {}
    while amp <= amp_hi_cap:
        stats = run_tran(netlist, node, freq, amp=amp, source=source, cycles=20)
        probe[amp] = stats
        if stats["thd_percent"] > thd_limit:
            hi, lo = amp, max(amp_lo, amp / 2)
            break
        amp *= 2
    if hi is None:
        last = max(probe)
        return {"converged": False, "note": "幅值到上限仍未超限",
                "input_amp": last, **probe[last]}
    for _ in range(12):
        mid = (lo + hi) / 2
        stats = run_tran(netlist, node, freq, amp=mid, source=source, cycles=20)
        if stats["thd_percent"] > thd_limit:
            hi = mid
        else:
            lo = mid
        if hi / lo < 1.05:
            break
    stats = probe.get(lo) or run_tran(netlist, node, freq, amp=lo, source=source, cycles=20)
    peak = max(abs(stats["peak_max"]), abs(stats["peak_min"]))
    return {"converged": True, "input_amp": lo, "thd_limit_percent": thd_limit,
            "output_peak_V": peak, "output_rms_V": peak / np.sqrt(2), **stats}


def clipping_wall(netlist: str, source: str, node: str, freq: float,
                  amp_start: float = 1e-3, amp_cap: float = 10.0,
                  stop_ratio: float = 0.01) -> dict:
    """教材口径的"最大不失真"：幅值倍增直到输出峰值不再增长（硬削顶渐近线）。

    返回正/负峰值渐近值，wall_peak_V = 两者较小者（对称正弦的上限），
    wall_rms_V = wall_peak / sqrt(2)。
    """
    amp = amp_start
    prev = None
    pos_flat = neg_flat = False
    last = None
    pos_best = neg_best = 0.0
    while amp <= amp_cap:
        stats = run_tran(netlist, node, freq, amp=amp, source=source, cycles=20)
        last = stats
        pos_best = max(pos_best, stats["peak_max"])
        neg_best = max(neg_best, abs(stats["peak_min"]))
        if prev:
            pos_flat = stats["peak_max"] < prev["peak_max"] * (1 + stop_ratio)
            neg_flat = abs(stats["peak_min"]) < abs(prev["peak_min"]) * (1 + stop_ratio)
            if pos_flat and neg_flat:
                break
            # 一侧已封顶且另一侧早已超过它：墙已确定，不必再扫
            if pos_flat and neg_best >= pos_best:
                break
            if neg_flat and pos_best >= neg_best:
                break
        prev = stats
        amp *= 2
    wall = min(pos_best, neg_best)
    return {"amp_at_wall": amp, "positive_peak_V": pos_best,
            "negative_peak_V": neg_best,
            "wall_peak_V": wall, "wall_rms_V": wall / np.sqrt(2),
            "pos_clipped": pos_flat, "neg_clipped": neg_flat}


if __name__ == "__main__":
    import json, sys
    cir = Path(sys.argv[1]).read_text()
    node = sys.argv[2] if len(sys.argv) > 2 else "out"
    print(json.dumps(run_tran(cir, node, 1000, amp=0.03, source="V1", cycles=20), indent=2))
