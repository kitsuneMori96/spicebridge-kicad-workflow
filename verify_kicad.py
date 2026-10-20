#!/usr/bin/env python3
"""Verify that a .kicad_sch is loadable by real KiCad and electrically identical
to the SPICE netlist it was built from.

Two independent checks, both run through the real KiCad 8 binary:

1. Netlist partition comparison.  `kicad-cli sch export netlist --format spice`
   makes KiCad compute its own netlist; we then compare *which pins share a
   net* with the source netlist.  Net names are ignored (KiCad renames them),
   and the comparison is restricted to terminals that both sides export, so a
   transistor without a SPICE model (or a power flag) does not create a false
   mismatch.

2. ERC pin check.  `kicad-cli sch erc` must report no unconnected pins.  This
   covers the terminals the netlist export cannot express (Q/M/X symbols).

Usage: python3 verify_kicad.py netlist.cir schematic.kicad_sch
Exit code 0 = pass.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from spicebridge.schematic import parse_netlist


def _pairs_to_groups(pairs):
    nets: dict[str, set] = {}
    for key, node in pairs:
        nets.setdefault(node.lower(), set()).add(key)
    return nets


def source_pairs(netlist: str):
    pairs = []
    for c in parse_netlist(netlist):
        # Both netlists are SPICE: node order is the element's terminal order,
        # so ordinal numbers keep the two sides comparable.
        nums = [str(i + 1) for i in range(len(c.nodes))]
        for num, node in zip(nums, c.nodes):
            pairs.append(((c.ref, num), node))
    return pairs


def kicad_pairs(net_path: Path):
    pairs = []
    for line in net_path.read_text().splitlines():
        tok = line.split()
        if not tok or line.lstrip().startswith(("*", ".", "(")):
            continue
        ref, rest = tok[0], tok[1:]
        if ref[0] in "RCLVID":
            nodes = rest[:2]
        elif ref[0] in "QM":
            nodes = rest[:3]
        elif ref[0] == "X":
            nodes = rest[:-1]
        else:
            continue
        nums = [str(i + 1) for i in range(len(nodes))]
        for num, node in zip(nums, nodes):
            if node.startswith("__"):
                # KiCad's placeholder for a symbol it cannot express in SPICE
                # (no model). Such a terminal is covered by the ERC check.
                continue
            pairs.append(((ref, num), node))
    return pairs


def _restricted(pairs, common):
    """Partition of `common` terminals induced by the given netlist."""
    nets = _pairs_to_groups(pairs)
    groups = set()
    for members in nets.values():
        cut = frozenset(t for t in members if t in common)
        if len(cut) > 1:
            groups.add(cut)
    return groups


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    netlist_path, sch_path = Path(sys.argv[1]), Path(sys.argv[2])
    src_text = netlist_path.read_text()
    tmp = Path(tempfile.mkdtemp(prefix="kicad_verify_"))
    out = tmp / "kicad.net"

    r = subprocess.run(
        ["kicad-cli", "sch", "export", "netlist", "--format", "spice",
         "-o", str(out), str(sch_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not out.exists():
        print(f"FAIL: KiCad could not load {sch_path}")
        print(f"  {(r.stdout + r.stderr).strip()}")
        return 1
    noisy = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
    for line in noisy:
        print(f"kicad-cli: {line.strip()}")

    src_pairs = source_pairs(src_text)
    kic_pairs = kicad_pairs(out)
    common = {k for k, _ in src_pairs} & {k for k, _ in kic_pairs}

    src = _restricted(src_pairs, common)
    got = _restricted(kic_pairs, common)

    # ERC: every pin must land on a wire.
    erc = tmp / "erc.rpt"
    subprocess.run(
        ["kicad-cli", "sch", "erc", "-o", str(erc), str(sch_path)],
        capture_output=True, text=True,
    )
    erc_text = erc.read_text() if erc.exists() else ""
    unconnected = re.findall(r"^\[pin_not_connected\]:.*\n(?:.*\n){0,2}", erc_text, re.M)

    print(f"schematic : {sch_path}")
    print(f"components: {len(parse_netlist(src_text))}")
    print(f"terminals : source {len(src_pairs)}, kicad {len(kic_pairs)}, common {len(common)}")
    print(f"nets      : source {len(src)}, kicad {len(got)}")

    ok = True
    if src != got:
        ok = False
        print("\nNETLIST MISMATCH")
        for m in sorted(src - got, key=len, reverse=True):
            print("  only in source:", sorted(m))
        for e in sorted(got - src, key=len, reverse=True):
            print("  only in kicad :", sorted(e))
    if unconnected:
        ok = False
        print("\nUNCONNECTED PINS")
        for block in unconnected:
            print("  " + block.strip().splitlines()[-1].strip())

    shutil.rmtree(tmp, ignore_errors=True)
    if ok:
        print("\nPASS: KiCad loads the file, pins are connected, netlist matches")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
