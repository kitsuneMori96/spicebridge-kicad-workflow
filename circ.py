#!/usr/bin/env python3
"""电路分析（邱关源体系）工作流：ngspice 真实仿真 + 解析式逐点比对。

和 `bode.py` 同一条铁律：**曲线和数字只认真实仿真**，脚本另算一遍解析式，
对不上就报错退出，绝不用公式直接冒充仿真结果。

覆盖的是"电路分析"而不是"模电"：

    thévenin()      戴维南/诺顿等效（含受控源，加压求流 + Voc/Isc 双法交叉验证）
    three_element() 一阶电路三要素，瞬态实测曲线反解 τ 并与 R·C 比对
    phasor()        正弦稳态相量：端口阻抗、P/Q、功率因数
    resonance()     谐振：f0、半功率点、Q、通频带
    switch_lines()  换路开关：真 `sw` 器件 + 方波控制极，t=0 动作（参数名见 SW_MODEL）

用法：
    python3 circ.py circuits/thevenin
    python3 circ.py circuits/rlc

spec.json 片段（全部可选，缺哪项就跳过哪项；每项也可写成列表一次跑几个工况）：
    "circ": {
      "thévenin": {                     // 也可以写 thevenin / 戴维南
        "port": "RP",                   // 跨在端口两节点之间的负载电阻
        "nodes": ["c", "0"],            // [高电位端, 低电位端]
        "expect": {"voc": 1.5, "isc": 1.2, "req": 1.25}
      },
      "three_element": {
        "probe": "v(c)",                // 观测向量
        "tstop": 10.0, "dt": 0.002,
        "expect": {"t0": 0.0, "tinf": 1.5, "tau": 1.0}
      },
      "phasor": {                       // 端口阻抗：Z = 端口电压 / 流入端口的电流
        "port": "RP",                   // 写成 `RP 电源侧 网络侧 1k`，会被换成 0V 电流表
        "nodes": ["a", "0"],            // 网络的两个端子
        "omega": 10000.0,
        "expect": {"r": 20, "x": 0, "z": 20, "pf": 1}
      },
      "resonance": {"start": 100, "stop": 1e5, "ppd": 40, "out": "n2",
                    "expect": {"f0": 1591.5, "q": 5.0, "bw": 318.3}}
    }
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

NGSPICE = "ngspice"

R_OPEN = 1e12       # 端口"开路"用电阻近似，保证节点不悬空
R_SHORT = 1e-6      # 端口"短路"用电阻近似

_TOL_DEFAULT = dict(voc=1e-3, isc=1e-3, req=1e-3, t0=1e-2, tinf=1e-2,
                    tau=1e-2, z=1e-2, f0=1e-2, q=5e-2, bw=5e-2)


# ---------------------------------------------------------------- 网表手术
def ensure_title(netlist: str) -> str:
    """ngspice 无条件把第一行当标题卡；首行是元件会被吞掉。自动补注释行。"""
    first = next((ln for ln in netlist.splitlines() if ln.strip()), "")
    if first.lstrip().startswith(("*", ";", ".")):
        return netlist
    return "* circ.py\n" + netlist


def set_value(netlist: str, name: str, value: float) -> str:
    """把元件 `name` 的取值换成 `value`，保留节点。支持 R/V/I。"""
    pat = re.compile(r"^(" + re.escape(name) + r")(\s+.*)$", re.IGNORECASE | re.MULTILINE)
    hit = pat.search(netlist)
    if not hit:
        raise SystemExit(f"网表里找不到元件 {name!r}")
    head, tail = hit.group(1), hit.group(2)
    tok = tail.split()
    val = f"{value:g}".lower().replace("e+", "e")     # 只是统一写法，ngspice 两种都吃
    kind = head[0].upper()
    if kind == "R":
        tok[-1] = val
    elif kind in ("V", "I"):                          # V/I: name n+ n- value
        num = re.compile(r"^[+\-]?\d[\d.eE+\-]*$")
        if len(tok) >= 3 and num.match(tok[2]):
            tok[2] = val
        else:
            tok.insert(2, val)
    else:
        raise SystemExit(f"元件 {name!r} 是 {kind} 型，set_value 只会改 R/V/I 的取值；"
                         f"端口元件请起 R 开头的名字")
    return netlist[:hit.start()] + head + " " + " ".join(tok) + netlist[hit.end():]


def zero_independent(netlist: str) -> str:
    """独立源置零：V 源→0 V（短路，保住节点），I 源→0 A（保留元件，等效开路）。
    受控源（E/F/G/H）原样保留——它们是网络系数矩阵的一部分，不是激励。"""
    num = re.compile(r"^[+\-]?\d[\d.eE+\-]*$")
    out = []
    for ln in netlist.splitlines():
        s = ln.strip()
        if s[:1].upper() in ("V", "I"):
            tok = s.split()
            if len(tok) >= 4:
                rest = tok[3:]
                if num.match(rest[0]):
                    rest = ["0"] + rest[1:]
                else:
                    rest = ["0"] + rest
                out.append(" ".join(tok[:3] + rest))
                continue
        out.append(ln)
    return "\n".join(out)


def free_name(netlist: str, base: str) -> str:
    """挑一个网表里没被占用的元件名。同名元件会被 ngspice 判为 "device already
    exists" 并直接中断仿真，而端口元件名由用户起，撞名很常见。"""
    taken = {ln.split()[0].upper() for ln in netlist.splitlines()
             if ln.strip() and not ln.lstrip().startswith(("*", ";", ".", "+"))}
    name, i = base, 1
    while name.upper() in taken:
        name, i = f"{base}{i}", i + 1
    return name


# 本机 ngspice-36 的 `sw` 模型只认 ron / roff / vt / vh 四个参数：手册和 PSpice 里常见的
# onres / von / voff / swon_res / swoff_res 全部报 "unrecognized parameter - ignored"。
# 判据是 控制电压 ≥ vt+vh 导通、≤ vt−vh 关断，中间保持原状态。默认 vt=vh=0，也就是
# 控制极给 0V 就已经导通——开关"断不开"不是 bug，是默认阈值把 0V 判成了闭合。
SW_MODEL = ".model swmod sw ron=1e-3 roff=1e12 vt=1 vh=0"


def switch_lines(netlist: str, a: str, b: str, close: bool = True,
                 t_action: float = 0.0, edge: float = 1e-9,
                 pw: float = 1.0, per: float = 2.0) -> str:
    """换路开关片段：S 器件 + 模型卡 + 方波控制源，`close` 决定 t=0 是闭合还是断开。

    控制源用 PULSE 而不是静态电压，直流初始解自然落在 t=0− 的开关状态上（PULSE 取
    初始值），储能元件的 0− 值由仿真自己算出来，不需要 `ic=` + `uic` 手工回填。

    返回的是**片段**，要拼在原网表后面（或整行替换掉某个元件）。同一张图插两只开关凑
    SPDT 时，后一次必须把已拼上的网表传给 `netlist`，否则元件重名直接中断仿真。
    感性回路必须**先合后断**：给 `close=False` 那只加 `t_action` 延迟，让新通路先建立，
    否则电感电流瞬间无路可走，撞上 roff 打出 1e12 V 尖峰，数值解当场塌掉（实测 i_L
    从 1.2 A 塌到 3e-5 A）。重叠的那几十 ns 里各量是过渡值，0+ 要等它结束再读。

    `pw`/`per` 是控制方波的脉宽与周期，默认 1 s / 2 s——**仿真时长超过 1 s 时必须抬高**，
    否则方波在 t=1 s 翻回去，开关自己又变向，后半段曲线测的是第二次换路（题 7-5 的
    τ=0.5 s、要跑到 8τ=4 s 就撞上这条）。
    """
    on, off = (5.0, -5.0) if close else (-5.0, 5.0)
    s, v = free_name(netlist, "S1"), free_name(netlist, "Vswctl")
    return (f"{s} {a} {b} {v}n 0 swmod\n"
            f"{SW_MODEL}\n"
            f"{v} {v}n 0 PULSE({off:g} {on:g} {t_action:g} {edge:g} {edge:g} {pw:g} {per:g})\n")


def insert_series_ammeter(netlist: str, port: str, name: str | None = None) -> tuple[str, str, str]:
    """把 0V 电流表源串进电阻 `port`：`RP a c 1k` → `VAM a p 0` + `RP p c 1k`。

    ngspice 只能读独立电压源的电流，所以端口电流必须靠串电流表取。
    返回 (新网表, 电流表向量名, 端口电阻靠近电流表的那一端)。
    """
    name = free_name(netlist, name or "VAM")
    m = re.search(r"^(" + re.escape(port) + r")(\s+)(\S+)\s+(\S+)\s+(.*)$",
                  netlist, re.IGNORECASE | re.MULTILINE)
    if not m:
        raise SystemExit(f"端口元件 {port} 不是标准四字段电阻行")
    head, sep, n1, n2, rest = m.groups()
    mid = f"{n1}_p"
    return (netlist[:m.start()] + f"{head}{sep}{mid} {n2} {rest.strip()}\n"
            + f"{name}{sep}{n1} {mid} 0" + netlist[m.end():],
            name.lower(), mid)        # 电流表名(小写=向量名)、端口电阻靠近电流表的那一端


def replace_with_ammeter(netlist: str, port: str, name: str | None = None) -> tuple[str, str]:
    """把电阻 `port` 整只换成 0V 电压源（= 理想电流表）：`RP in a 1` → `VAM in a 0`。

    用于测"网络阻抗"：电流表本身不引入阻抗，端口电压落在网络两端而不是仪表上。
    约定 `port` 行的第二个节点是网络侧，也就是 `nodes[0]`。
    """
    name = free_name(netlist, name or "VAM")
    m = re.search(r"^(" + re.escape(port) + r")(\s+\S+\s+\S+)\s+\S+.*$",
                  netlist, re.IGNORECASE | re.MULTILINE)
    if not m:
        raise SystemExit(f"端口元件 {port} 不是标准电阻行")
    return (netlist[:m.start()] + f"{name}{m.group(2)} 0" + netlist[m.end():],
            name.lower())


GND = ("0", "gnd", "#ground")     # .control 里 v(0) 取不到，地对端只能读单点电位


def vport(hi: str, lo: str) -> str:
    """端口电压的 .control 表达式；`hi` 是高电位端，也是电流参考方向的流入端。"""
    if lo in GND:
        return f"v({hi})"
    if hi in GND:
        raise SystemExit(f"端口高电位端不能是地（{hi!r}）")
    return f"v({hi})-v({lo})"


# ---------------------------------------------------------------- 仿真后端
def _ctl(vecs: list[str], analysis: str) -> str:
    """生成 .control 片段：只有含括号外运算符的表达式才需要 let 命名。

    注意 .control 语言里 `v(0)` 取不到地节点，调用方应避免对地做差分。
    每个向量单独写一个 .dat：`wrdata` 对实向量输出两列（点号、值），多个向量会各自
    成对拼在同一行里，列数变成 2n，混在一个文件里读极易把列标对错。
    """
    lines = [analysis]
    for v in vecs:
        tag = re.sub(r"[^\w]", "_", v)
        bare = re.sub(r"\([^()]*\)", "", v)          # 去掉所有括号内容
        if re.search(r"[+\-*/]", bare):              # 还剩运算符 → 是表达式
            lines.append(f"let {tag} = {v}")
            lines.append(f"wrdata {tag}.dat {tag}")
        else:
            lines.append(f"wrdata {tag}.dat {v}")
    return "\n".join(lines)


def _run(netlist: str, control: str) -> dict[str, np.ndarray]:
    """跑一段 .control 脚本，把里面 wrdata 写出的文件读回来。

    `.end` 必须先摘掉：ngspice 在它之后就不再读网表，`.control` 块会被整段忽略。
    """
    body = re.sub(r"(?im)^\s*\.end\s*$", "", ensure_title(netlist))
    script = body + f"\n.control\nrun\n{control}\nquit\n.endc\n.end\n"
    with tempfile.TemporaryDirectory() as td:
        cir = Path(td) / "c.cir"
        cir.write_text(script, encoding="utf-8")
        r = subprocess.run([NGSPICE, "-b", str(cir)], capture_output=True, text=True, cwd=td)
        files = sorted(Path(td).glob("*.dat"))
        if not files:
            raise SystemExit(f"ngspice 无输出：\n{r.stdout[-2000:]}\n{r.stderr[-800:]}")
        out = {}
        for fp in files:
            a = np.atleast_2d(np.loadtxt(fp))
            if a.shape[-1] == 3:                      # ac: index real imag
                out[fp.stem] = (a[:, 0], a[:, 1] + 1j * a[:, 2])
            elif a.shape[-1] == 2:                    # op / tran 实向量: index value
                out[fp.stem] = (a[:, 0], a[:, 1] + 0j)
            else:
                raise SystemExit(f"{fp} 列数异常 {a.shape}")
        return out


def ac(netlist: str, start: float, stop: float, ppd: int, vec: str) -> tuple[np.ndarray, np.ndarray]:
    """`.ac dec` → (频率, 复相量)。vec 例："v(out)"、"i(vvam)"、"v(a)-v(c)"。"""
    return _run(netlist, _ctl([vec], f"ac dec {ppd} {start:g} {stop:g}"))[
        re.sub(r"[^\w]", "_", vec)]


def op(netlist: str, vecs: list[str]) -> dict[str, complex]:
    """直流工作点。返回 {向量: 值}。

    注意：ngspice 的 op 分析**没有电流向量**，要取支路电流请用 `dcop()`。
    """
    res = _run(netlist, _ctl(vecs, "op"))
    return {v: res[re.sub(r"[^\w]", "_", v)][1][0] for v in vecs}


def dcop(netlist: str, vecs: list[str], tend: float = 1e4) -> dict[str, complex]:
    """用一次长瞬态取稳态值——这是唯一能读支路电流的直流手段（op 没有电流向量）。"""
    data = tran(netlist, vecs, tend / 200, tend)
    return {v: complex(np.real(data[v][1][-1]), np.imag(data[v][1][-1])) for v in vecs}


def tran(netlist: str, vecs: list[str], dt: float, tstop: float,
         tmax: float | None = None, uic: bool = False
         ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """瞬态。返回 {向量: (t, 复值)}；实信号取 .real。

    `uic=True` 跳过直流工作点，直接用元件的 `ic=` / `.ic` 起步——换路问题必须靠它
    把 0− 时刻的储能状态带进 t≥0 的仿真，否则电容会被悄悄重置成换路后的稳态值。

    `tmax` 是内部积分最大步长（`.tran` 的第 4 个位置参数）。想用开关跳变沿读出 0+
    值就得靠它把沿细分；注意第 3 个位置参数是 Tstart 不是步长，填错会整段丢掉起点。
    """
    step = f" 0 {tmax:g}" if tmax else ""
    res = _run(netlist, _ctl(vecs, f"tran {dt:g} {tstop:g}{step}{' uic' if uic else ''}"))
    return {v: res[re.sub(r"[^\w]", "_", v)] for v in vecs}


# ---------------------------------------------------------------- 戴维南
def thévenin(netlist: str, port: str, nodes: list[str],
             expect: dict | None = None, tol: dict | None = None) -> dict:
    """一端口等效：开路电压 + 短路电流 + 独立源置零加测试源，三条路互相交叉验证。

    `port` 是跨在端口两节点之间的电阻（充当可装卸的负载）；`nodes = [高电位端, 低端]`。
    含受控源照样成立——置零只动独立源，受控源是矩阵系数。
    """
    tol = {**_TOL_DEFAULT, **(tol or {})}
    hi, lo = nodes
    vp = vport(hi, lo)
    nl, am, _ = insert_series_ammeter(netlist, port)

    # --- A. 开路电压：负载置极大，无电流无压降，端口电压即 Voc
    voc = abs(dcop(set_value(nl, port, R_OPEN), [vp])[vp])

    # --- B. 短路电流：负载置 0Ω（独立源保留），串着的电流表读到的就是 Isc
    isc = abs(dcop(set_value(nl, port, R_SHORT), [f"i({am})"])[f"i({am})"])

    # --- C. 加压求流：独立源置零、负载摘掉（置极大），从端口灌 1A 测试电流
    #        此时端口电压数值上就是 Req。这是对 B 的完全独立复核。
    zl = zero_independent(set_value(nl, port, R_OPEN))
    zl += f"\n{free_name(zl, 'ITEST')} {hi} {lo} 1\n"
    req_test = abs(dcop(zl, [vp])[vp]) / 1.0

    # 端口网络内部可能一个独立源都没有（只求 Req 的常见场合）。此时 Voc=Isc=0，
    # Voc/Isc 是 0/0，只有加压求流那一路成立，另一路没法做交叉验证。
    dead = voc < 1e-9 and isc < 1e-9
    req_sc = float("nan") if dead else voc / isc
    req = req_test if dead else req_sc

    out = dict(voc=voc, isc=isc, req=req, req_by_test_source=req_test,
               req_consistency=abs(req - req_test) / req_test)
    checks = ([("端口内无独立源，仅加压求流成立", 0.0, tol["req"])] if dead else
              [("Voc vs Isc·Req", abs(voc - isc * req_sc) / voc, tol["voc"]),
               ("短路法 vs 加压求流 Req", out["req_consistency"], tol["req"])])
    for k in ("voc", "isc", "req"):
        if expect and k in expect:
            checks.append((f"{k} vs 期望", abs(out[k] - expect[k]) / abs(expect[k]), tol[k]))
    out["checks"] = checks
    out["fail"] = [(n, e) for n, e, t in checks if e > t]
    return out


# ---------------------------------------------------------------- 三要素
def three_element(netlist: str, probe: str, tstop: float, dt: float,
                  expect: dict | None = None, tol: dict | None = None,
                  uic: bool = False, tmax: float | None = None) -> dict:
    """一阶电路三要素：从实测瞬态曲线反解 f(0+)、f(∞)、τ，并与期望比对。"""
    tol = {**_TOL_DEFAULT, **(tol or {})}
    t, x = tran(netlist, [probe], dt, tstop, uic=uic, tmax=tmax)[probe]
    y = np.real(x)
    tinf = float(np.mean(y[int(len(y) * 0.9):]))
    t0 = float(y[0])
    A = tinf - t0
    if abs(A) < 1e-9:
        raise SystemExit(f"探针 {probe} 全程不变，读不出三要素（初始状态已平衡？）")
    # 三要素式 f(∞)−f(t) = A·e^(−t/τ) → ln 后是直线，斜率即 −1/τ。
    # 只取衰减 5%~98% 的区间：换路沿附近和末端数值噪声都会把拟合带偏。
    r = (tinf - y) / A
    ok = (r > 0.02) & (r < 0.98)
    coef = np.polyfit(t[ok], np.log(r[ok]), 1)
    tau = -1.0 / coef[0]
    model = tinf - A * np.exp(-t / tau)
    out = dict(t0=t0, tinf=tinf, tau=tau, five_tau=5 * tau,
               exp_model_err=float(np.max(np.abs(y[ok] - model[ok])) / abs(A)))
    checks = [("τ 为正且有限", 0.0 if np.isfinite(tau) and tau > 0 else 1.0, tol["tau"]),
              ("实测曲线 vs 三要素式", out["exp_model_err"], tol["tau"])]
    if expect:
        for k in ("t0", "tinf", "tau"):
            if k in expect:
                ref = abs(expect[k]) if expect[k] else 1.0
                checks.append((f"{k} vs 期望", abs(out[k] - expect[k]) / ref, tol[k]))
    out["checks"] = checks
    out["fail"] = [(n, e) for n, e, t_ in checks if e > t_]
    return out


# ---------------------------------------------------------------- 相量 / 谐振
def phasor(netlist: str, port: str, nodes: list[str], omega: float,
           expect: dict | None = None, tol: dict | None = None) -> dict:
    """单频相量：一次 `.ac` 同时取端口电压与流入端口的电流 → Z、S、功率因数。

    `port` 行写成 `RP 电源侧 网络侧 <值>`；函数把它换成 0V 理想电流表，
    于是 `nodes = [网络侧, 参考侧]` 上的电压全部落在网络上，Z = V/I 就是网络阻抗。
    电流参考方向 = 流入 `nodes[0]`，无源符号约定下 R ≥ 0。
    """
    tol = {**_TOL_DEFAULT, **(tol or {})}
    f = omega / (2 * np.pi)
    nl, am = replace_with_ammeter(netlist, port)
    vp = vport(*nodes)
    _, vv = ac(nl, f, f, 2, vp)
    _, vi = ac(nl, f, f, 2, f"i({am})")
    v = complex(vv[0])
    i = complex(vi[0])
    if abs(i) < 1e-15:
        raise SystemExit(f"端口电流为 0，ω={omega} 处无法求阻抗（端口悬空？）")
    z = v / i
    s = v * np.conj(i)
    out = dict(f_hz=f, omega=omega, absz=abs(z), argz_deg=float(np.degrees(np.angle(z))),
               r=z.real, x=z.imag, p=s.real, q=s.imag,
               pf=(abs(s.real) / abs(s) if abs(s) else 0.0))
    out["checks"] = []
    for k, key in (("z", "absz"), ("r", "r"), ("x", "x"), ("pf", "pf"), ("q", "q")):
        if expect and k in expect:
            ref = abs(expect[k]) or 1.0            # 期望为 0 时退化成绝对误差
            out["checks"].append((f"{k} vs 期望", abs(out[key] - expect[k]) / ref, tol["z"]))
    out["fail"] = [(n, v_) for n, v_, t in out["checks"] if v_ > t]
    return out


def _cross(f: np.ndarray, m: np.ndarray, target: float, i0: int, step: int) -> float:
    """从峰点往 `step`(±1) 方向走到 |H| 首次跌破 target，在两格点间线性插值。"""
    i = i0
    while 0 <= i + step < len(f):
        j = i + step
        if m[j] <= target:
            den = m[i] - m[j]
            w = (m[i] - target) / den if den else 0.0
            return float(f[i] + w * (f[j] - f[i]))
        i = j
    return float("nan")


def resonance(netlist: str, start: float, stop: float, ppd: int, out_node: str,
              expect: dict | None = None, tol: dict | None = None) -> dict:
    """谐振：扫频找峰、两侧半功率点，算 Q 与通频带。

    对数栅格很稀，峰点和穿越点都做了插值细化，否则 Q 会有百分之几十的假误差。
    """
    tol = {**_TOL_DEFAULT, **(tol or {})}
    f, h = ac(netlist, start, stop, ppd, f"v({out_node})")
    m = np.abs(h)
    i0 = int(np.argmax(m))
    if not 0 < i0 < len(f) - 1:
        raise SystemExit("峰值落在扫描端点上，请放大扫描范围")
    # 三点抛物线细化峰点；峰值也一并细化，半功率门限才准
    y0, y1, y2 = m[i0 - 1], m[i0], m[i0 + 1]
    d = y0 - 2 * y1 + y2
    off = float(np.clip(0.5 * (y0 - y2) / d, -1, 1)) if d else 0.0
    f0 = float(f[i0] + off * (f[i0 + 1] - f[i0]))
    peak = float(y1 - 0.25 * (y0 - y2) * off)
    half = peak / np.sqrt(2)
    lo = _cross(f, m, half, i0, -1)
    hi = _cross(f, m, half, i0, +1)
    bw = hi - lo
    q = f0 / bw if bw and np.isfinite(bw) else float("inf")
    out = dict(f0=f0, f_lo=lo, f_hi=hi, bw=bw, q=q, peak=peak)
    out["checks"] = [("半功率点落在扫描范围内", 0.0 if np.isfinite(bw) else 1.0, tol["q"])]
    if expect:
        for k, key in (("f0", "f0"), ("q", "q"), ("bw", "bw")):
            if k in expect:
                out["checks"].append(
                    (f"{k} vs 期望", abs(out[key] - expect[k]) / abs(expect[k]), tol[k]))
    out["fail"] = [(n, v) for n, v, t in out["checks"] if v > t]
    return out


# ---------------------------------------------------------------- 报告
def _report(title: str, res: dict) -> bool:
    print(f"\n== {title} ==")
    for k, v in res.items():
        if k in ("checks", "fail"):
            continue
        print(f"  {k:18s} = {v:.6g}" if isinstance(v, (int, float, np.floating)) else f"  {k:18s} = {v}")
    ok = True
    for name, err, t in res.get("checks", []):
        flag = "OK " if err <= t else "FAIL"
        if err > t:
            ok = False
        print(f"  [{flag}] {name:24s} 误差 {err:.2e} (限 {t:g})")
    return ok


def build(cdir: Path) -> int:
    cdir = Path(cdir)
    spec = json.loads((cdir / "spec.json").read_text(encoding="utf-8"))
    cfg = spec.get("circ")
    if not cfg:
        raise SystemExit(f"{cdir}/spec.json 里没有 circ 段")
    netlist = (cdir / "netlist.cir").read_text(encoding="utf-8")

    runs = [
        ("thévenin", "戴维南等效",
         lambda c: thévenin(netlist, c["port"], c["nodes"], c.get("expect"))),
        ("three_element", "一阶三要素",
         lambda c: three_element(netlist, c["probe"], float(c["tstop"]),
                                 float(c["dt"]), c.get("expect"))),
        ("phasor", "正弦稳态相量",
         lambda c: phasor(netlist, c["port"], c["nodes"], float(c["omega"]), c.get("expect"))),
        ("resonance", "谐振",
         lambda c: resonance(netlist, float(c["start"]), float(c["stop"]),
                             int(c["ppd"]), c["out"], c.get("expect"))),
    ]
    # thévenin 带变音符，命令行/JSON 里打不出来，多认几种写法
    alias = {"thevenin": "thévenin", "戴维南": "thévenin", "一端口": "thévenin"}
    cfg = {alias.get(k, k): v for k, v in cfg.items()}

    all_ok = True
    ran = 0
    for key, title, fn in runs:
        cfgs = cfg.get(key)
        if cfgs is None:
            continue
        cfgs = cfgs if isinstance(cfgs, list) else [cfgs]
        for i, c in enumerate(cfgs):
            ran += 1
            all_ok &= _report(title if len(cfgs) == 1 else f"{title} #{i + 1}", fn(c))
    if not ran:
        raise SystemExit(f"{cdir.name}: circ 段里没有任何可跑的项，键名需为 "
                         f"{'/'.join(k for k, _, _ in runs)}")

    print(f"\nRESULT {'PASS' if all_ok else 'FAIL'}: {cdir.name}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(build(Path(sys.argv[1])))
