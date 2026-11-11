"""Smoke-test mcp_server.py over stdio, exactly as an MCP client would.

Usage: .venv/bin/python scripts/mcp_smoke.py [fake|archil]
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent


async def main(kind: str) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=[str(ROOT / "mcp_server.py")], env={**os.environ, "ENGINE": kind}
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        print("server:", init.server_info.name, "| instructions:", bool(init.instructions))
        print("tools:", [t.name for t in (await session.list_tools()).tools])

        async def call(name: str, **args):
            result = await session.call_tool(name, args)
            data = result.structured_content or json.loads(result.content[0].text)
            print(f"{name}({args}) ->", json.dumps(data)[:300])
            return data

        await call("run", command="echo 'print(1)' > main.py")
        await call("run", command="echo x > notes.txt")
        bad = await call("run", command="rm main.py notes.txt")
        await call("run", command="ls")
        await call("rewind", step=bad["step"], reason="deleted the project by mistake")
        restored = await call("run", command="ls")
        tl = await call("timeline")
        print("rewinds:", json.dumps(tl["rewinds"]))
        await call("rewind", step=99)
        ok = all(f in restored["stdout"] for f in ("main.py", "notes.txt"))
        logged = tl["rewinds"] and tl["rewinds"][0]["reason"] == "deleted the project by mistake"
        print("PASS" if ok else "FAIL", "- files back after rewind")
        print("PASS" if logged else "FAIL", "- rewind and reason kept in timeline")


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "fake"))
