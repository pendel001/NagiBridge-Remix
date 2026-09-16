# -*- coding: utf-8 -*-
"""通过 streamable-http 调 MCP 工具的最小客户端（测试用）。
用法: python _mcp_call.py <tool_name> [json_args]
例:   python _mcp_call.py map_go '{"destination":"Town"}'
"""
import sys, os, json, asyncio
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    url = os.environ.get("NAGI_MCP_URL", "http://127.0.0.1:8000/mcp")   # ⚠️ 别用 localhost，见 mcp_cli.py 说明
    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            r = await session.call_tool(tool, args)
            for c in r.content:
                if c.type == "text":
                    print(c.text, end="\n" if not c.text.endswith("\n") else "")
                else:
                    print(f"[{c.type}]", c)
            if r.isError:
                sys.exit(1)

asyncio.run(main())
