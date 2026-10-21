#!/usr/bin/env python3
"""End-to-end runner: SPICE netlist -> SPICEBridge simulation -> KiCad schematic.

    python3 run.py circuits/q17

A circuit directory holds:

    netlist.cir    SPICE netlist (no analysis lines needed)
    layout.py      KiCad layout script; run with the output .kicad_sch path
    spec.json      {
        "op": true,
        "ac": {"start": 10, "stop": 1e7, "ppd": 20},
        "tran": {"source": "V1", "node": "out", "freq": 1000, "amp": 0.03},
        "clipping_wall": {"source": "V1", "node": "out", "freq": 1000},
        "max_undistorted": {"source": "V1", "node": "out", "freq": 1000, "thd": 1.0},
        "anim": {"source": "V1", "amp": 0.05, "freq": 1000, "traces": [...]}
    }

Steps:
  1. simulation through the real SPICEBridge MCP server (one stdio session)
  2. optional transient / max-undistorted scans with tran.py (real ngspice)
  3. schematic generation from the hand-written layout
  4. verification with real KiCad (kicad-cli netlist + ERC)
  5. SVG/PNG render, with the PNG checked before we claim success
  6. optional SVG animation (svg_anim.py) driven by the same netlist

Exit code 0 only if KiCad verification passes.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import flow  # noqa: E402  (local module)
import tran  # noqa: E402


def simulate(netlist: str, spec: dict) -> dict:
    steps = [{"tool": "create_circuit", "args": {"netlist": netlist}, "label": "c"}]
    if spec.get("op", True):
        steps.append({"tool": "run_dc_op", "args": {"circuit_id": "${c.circuit_id}"}, "label": "op"})
    ac = spec.get("ac")
    if ac:
        steps.append({
            "tool": "run_ac_analysis",
            "args": {
                "circuit_id": "${c.circuit_id}",
                "start_freq": ac.get("start", 10),
                "stop_freq": ac.get("stop", 1e7),
                "points_per_decade": ac.get("ppd", 20),
            },
            "label": "ac",
        })
    return asyncio.run(flow.run(steps, verbose=False))


def run_transient(netlist: str, spec: dict) -> None:
    """Optional tran.py checks: steady-state waveform, clipping wall, THD scan."""
    for key in ("tran", "clipping_wall", "max_undistorted"):
        cfg = spec.get(key)
        if not cfg:
            continue
        src, node = cfg["source"], cfg["node"]
        freq = cfg.get("freq", 1000)
        if key == "tran":
            r = tran.run_tran(netlist, node, freq, amp=cfg.get("amp"), source=src,
                              cycles=cfg.get("cycles", 40))
            print(
                f"  tran: {node} peak {r['peak_max']:.4f} / {r['peak_min']:.4f} V, "
                f"THD {r['thd_percent']:.2f}%, settled={r['settled']} "
                f"({r['attempts']} attempts)",
                flush=True,
            )
        elif key == "clipping_wall":
            r = tran.clipping_wall(netlist, src, node, freq,
                                   amp_start=cfg.get("amp_start", 1e-3))
            print(
                f"  wall: +{r['positive_peak_V']:.3f} / -{r['negative_peak_V']:.3f} V, "
                f"max undistorted Uo = {r['wall_peak_V']:.3f} V peak "
                f"({r['wall_rms_V']:.3f} V rms)",
                flush=True,
            )
        else:
            r = tran.max_undistorted(netlist, src, node, freq,
                                     thd_limit=cfg.get("thd", 1.0))
            print(
                f"  thd : limit {r.get('thd_limit_percent')}% at input "
                f"{r.get('input_amp'):.4f} V, output peak {r.get('output_peak_V', 0):.4f} V "
                f"(converged={r.get('converged', True)})",
                flush=True,
            )


def render(sch: Path, out: Path, name: str) -> bool:
    """Export SVG + PNG and verify the PNG actually opens. Returns success."""
    r = subprocess.run(
        ["kicad-cli", "sch", "export", "svg", "-o", str(out), str(sch)],
        text=True, capture_output=True,
    )
    if r.returncode != 0:
        print(f"  WARNING: kicad-cli svg export failed: {r.stderr.strip()[:300]}", flush=True)
        return False
    svg = out / f"{name}.svg"
    png = out / f"{name}.png"
    if not svg.exists():
        print(f"  WARNING: expected {svg} was not produced", flush=True)
        return False
    try:
        import cairosvg
        from PIL import Image
    except ImportError as e:
        print(f"  WARNING: render skipped, missing dependency ({e})", flush=True)
        return False

    # cairosvg can emit a file with a PNG header but corrupt body, so
    # reopen the image and force a full decode before trusting it.
    for attempt in (1, 2):
        try:
            cairosvg.svg2png(url=str(svg), write_to=str(png), output_width=2400)
            img = Image.open(png)
            img.verify()
            Image.open(png).load()
            print(f"  {png} ({img.size[0]}x{img.size[1]})", flush=True)
            return True
        except Exception as e:
            print(f"  WARNING: PNG attempt {attempt} invalid: {e}", flush=True)
    return False


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    cdir = Path(sys.argv[1]).resolve()
    name = cdir.name
    netlist = tran.ensure_title((cdir / "netlist.cir").read_text())
    spec = json.loads((cdir / "spec.json").read_text()) if (cdir / "spec.json").exists() else {}

    out = HERE / "out" / name
    out.mkdir(parents=True, exist_ok=True)

    print(f"== {name}: simulation ==", flush=True)
    sim = simulate(netlist, spec)
    for label, result in sim.items():
        if not isinstance(result, dict) or "results" not in result:
            continue
        r = result["results"]
        if r.get("analysis_type") == "Operating Point":
            nodes = {k: round(v, 4) for k, v in r.get("nodes", {}).items()}
            print(f"  op : {nodes}", flush=True)
        else:
            print(
                f"  ac : gain {r.get('peak_gain_dB'):.2f} dB at "
                f"{r.get('peak_gain_freq_hz'):.0f} Hz, "
                f"dc gain {r.get('gain_dc_dB'):.2f} dB, "
                f"f_3dB {r.get('f_3dB_hz')}"
            )

    if any(k in spec for k in ("tran", "clipping_wall", "max_undistorted")):
        print(f"== {name}: transient ==", flush=True)
        run_transient(netlist, spec)

    sch = out / f"{name}.kicad_sch"
    print(f"== {name}: schematic ==", flush=True)
    r = subprocess.run([sys.executable, str(cdir / "layout.py"), str(sch)], text=True)
    if r.returncode != 0:
        print("layout script failed", flush=True)
        print("RESULT: FAIL")
        return 1

    print(f"== {name}: KiCad verification ==", flush=True)
    v = subprocess.run([sys.executable, str(HERE / "verify_kicad.py"), str(cdir / "netlist.cir"), str(sch)])
    if v.returncode != 0:
        # NETLIST MISMATCH is nearly always a wire that missed a pin. Dump the
        # absolute pin table so the layout edit is guess-free.
        subprocess.run([sys.executable, str(cdir / "layout.py"), "--pins"])
        print("VERIFICATION FAILED", flush=True)
        print("RESULT: FAIL")
        return 1

    anim = spec.get("anim")
    if anim:
        print(f"== {name}: SVG animation ==", flush=True)
        import svg_anim
        ap = svg_anim.build(cdir, out / f"{name}_anim.svg")
        print(f"  anim: {ap} ({ap.stat().st_size / 1024:.0f} KiB)", flush=True)

    print(f"== {name}: render ==", flush=True)
    ok = render(sch, out, name)
    print(f"\nRESULT: {'PASS' if ok else 'PASS (render failed)'} -> {sch}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
