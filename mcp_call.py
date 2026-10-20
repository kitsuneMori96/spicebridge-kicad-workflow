#!/usr/bin/env python3
"""Minimal SPICEBridge MCP (stdio) client for scripting / agent use.

Usage:
  python3 mcp_call.py list
  python3 mcp_call.py schema <tool>
  python3 mcp_call.py call <tool> '<json args>'
  python3 mcp_call.py call <tool> @args.json
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ENV = dict(os.environ)
ENV["PATH"] = os.path.expanduser("~/.local/bin") + ":" + ENV.get("PATH", "")


def _params() -> StdioServerParameters:
    return StdioServerParameters(command="spicebridge", args=[], env=ENV)


def _render(result) -> str:
    out = []
    for item in result.content:
        text = getattr(item, "text", None)
        if text is not None:
            out.append(text)
        else:
            out.append(json.dumps(item.model_dump(), ensure_ascii=False))
    if getattr(result, "isError", False):
        out.insert(0, "!! MCP tool returned isError=true")
    return "\n".join(out)


async def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    async with stdio_client(_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            if cmd == "list":
                tools = (await session.list_tools()).tools
                for t in tools:
                    desc = (t.description or "").strip().splitlines()[0]
                    print(f"{t.name}\t{desc}")
                print(f"\n# {len(tools)} tools", file=sys.stderr)
                return 0

            if cmd == "schema":
                tools = {t.name: t for t in (await session.list_tools()).tools}
                t = tools[sys.argv[2]]
                print(json.dumps(
                    {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": t.inputSchema,
                    },
                    indent=2,
                    ensure_ascii=False,
                ))
                return 0

            if cmd == "call":
                name = sys.argv[2]
                raw = sys.argv[3] if len(sys.argv) > 3 else "{}"
                if raw.startswith("@"):
                    raw = open(raw[1:], encoding="utf-8").read()
                args = json.loads(raw)
                result = await session.call_tool(name, args)
                print(_render(result))
                return 0

    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
