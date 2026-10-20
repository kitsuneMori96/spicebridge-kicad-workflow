#!/usr/bin/env python3
"""End-to-end runner: SPICE netlist -> SPICEBridge simulation -> KiCad schematic.

    python3 run.py circuits/ce_amp

A circuit directory holds:

    netlist.cir    SPICE netlist (no analysis lines needed)
    layout.py      KiCad layout script; run with the output .kicad_sch path
    spec.json      {"op": true, "ac": {"start": 10, "stop": 1e7, "ppd": 20}}

Steps:
  1. simulation through the real SPICEBridge MCP server (one stdio session)
  2. schematic generation from the hand-written layout
  3. verification with real KiCad (kicad-cli netlist + ERC)
  4. SVG/PNG render for review

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


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    cdir = Path(sys.argv[1]).resolve()
    name = cdir.name
    netlist = (cdir / "netlist.cir").read_text()
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

    sch = out / f"{name}.kicad_sch"
    print(f"== {name}: schematic ==", flush=True)
    r = subprocess.run([sys.executable, str(cdir / "layout.py"), str(sch)], text=True)
    if r.returncode != 0:
        print("layout script failed", flush=True)
        return 1

    print(f"== {name}: KiCad verification ==", flush=True)
    v = subprocess.run([sys.executable, str(HERE / "verify_kicad.py"), str(cdir / "netlist.cir"), str(sch)])
    if v.returncode != 0:
        print("VERIFICATION FAILED", flush=True)
        return 1

    print(f"== {name}: render ==", flush=True)
    subprocess.run(["kicad-cli", "sch", "export", "svg", "-o", str(out), str(sch)],
                   check=False, capture_output=True)
    svg = out / f"{name}.svg"
    if svg.exists():
        import cairosvg
        cairosvg.svg2png(url=str(svg), write_to=str(out / f"{name}.png"), output_width=2400)
        print(f"  {out / f'{name}.png'}", flush=True)
    print(f"\nOK -> {sch}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
