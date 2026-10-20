#!/usr/bin/env python3
"""Run a multi-step SPICEBridge MCP flow inside ONE stdio session.

Circuit IDs live only inside the server process, so every step of a design
(create -> simulate -> measure -> export) must share one session.

Usage:
  python3 flow.py steps.json
  python3 flow.py -            # read steps JSON from stdin

steps.json:
{
  "steps": [
    {"tool": "create_circuit", "args": {"netlist": "..."}, "label": "rc"},
    {"tool": "run_ac_analysis", "args": {"circuit_id": "${rc.id}"}},
    {"tool": "export_kicad", "args": {"circuit_id": "${rc.id}", "filename": "x.kicad_sch"},
     "save": {"kicad_content": "out/x.kicad_sch"}, "quiet": ["kicad_content"]},
    {"tool": "draw_schematic", "args": {"circuit_id": "${rc.id}"}, "image": "out/x.png"}
  ]
}

Placeholders: ${label.field} or ${label.field.sub} resolve against previous results.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ENV = dict(os.environ)
ENV["PATH"] = os.path.expanduser("~/.local/bin") + ":" + ENV.get("PATH", "")


def _subst(value, results):
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}") and value.count("${") == 1:
            path = value[2:-1].split(".")
            cur = results
            for key in path:
                cur = cur[key] if isinstance(cur, dict) else cur[int(key)]
            return cur
        return value
    if isinstance(value, list):
        return [_subst(v, results) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, results) for k, v in value.items()}
    return value


def _short(obj, limit=1200):
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [{len(text) - limit} more chars]"


async def run(steps, verbose=True):
    params = StdioServerParameters(command="spicebridge", args=[], env=ENV)
    results: dict = {}
    errlog = sys.stderr if verbose else open(os.devnull, "w")
    async with stdio_client(params, errlog=errlog) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for idx, step in enumerate(steps, 1):
                tool = step["tool"]
                args = _subst(step.get("args", {}), results)
                res = await session.call_tool(tool, args)

                payload = None
                if res.content:
                    first = res.content[0]
                    text = getattr(first, "text", None)
                    if text is not None:
                        try:
                            payload = json.loads(text)
                        except json.JSONDecodeError:
                            payload = {"raw_text": text}

                if payload is None and getattr(res, "structuredContent", None):
                    payload = res.structuredContent

                if payload is None:
                    # binary / image content
                    payload = {}
                    for part in res.content:
                        if getattr(part, "type", "") == "image" and step.get("image"):
                            import base64

                            Path(step["image"]).parent.mkdir(parents=True, exist_ok=True)
                            Path(step["image"]).write_bytes(base64.b64decode(part.data))
                            payload.setdefault("saved_images", []).append(step["image"])

                label = step.get("label") or f"step{idx}"
                results[label] = payload

                for field, dest in (step.get("save") or {}).items():
                    val = payload.get(field)
                    if isinstance(val, str):
                        Path(dest).parent.mkdir(parents=True, exist_ok=True)
                        Path(dest).write_text(val, encoding="utf-8")
                        payload[f"saved_{field}"] = dest

                if verbose:
                    shown = dict(payload)
                    for field in step.get("quiet", []):
                        shown.pop(field, None)
                    print(f"### [{idx}] {label}: {tool}  ({'ERROR' if res.isError else 'ok'})")
                    print(_short(shown))
                    print()
    return results


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    raw = sys.stdin.read() if sys.argv[1] == "-" else Path(sys.argv[1]).read_text(encoding="utf-8")
    data = json.loads(raw)
    asyncio.run(run(data["steps"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
