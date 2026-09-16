"""
NagiBridge MCP 测试客户端
用法: python scripts/mcp_test_client.py [工具名] [参数...]

不带参数 -> 列出所有工具
带工具名 -> 调用该工具（参数用 key=value 形式传）
例: python scripts/mcp_test_client.py check_status
     python scripts/mcp_test_client.py walk_to poi_name=农场
     python scripts/mcp_test_client.py look_around radius=8
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, "scripts")
os.environ["PYTHONIOENCODING"] = "utf-8"

# Force stdout/stderr to UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

async def main():
    # ⚠️ 127.0.0.1 而非 localhost —— 见 mcp_cli.py 同处的说明（localhost 先试 ::1，拒连烧 2 秒）
    url = os.environ.get("NAGI_MCP_URL", "http://127.0.0.1:8000/mcp")  # streamable-http（/sse 已废弃）

    if len(sys.argv) == 1:
        # ── 列出工具 ──
        print(f"Connecting to MCP: {url}")
        async with mcp_client_session(url) as session:
            result = await session.list_tools()
            tools = result.tools if hasattr(result, 'tools') else result
            print(f"\n=== {len(tools)} tools registered ===\n")
            for t in tools:
                name = t.name
                desc = t.description.split("\n")[0] if t.description else ""
                print(f"  [{name}]  {desc}")
            print()

    else:
        tool_name = sys.argv[1]
        args = {}
        for a in sys.argv[2:]:
            if "=" in a:
                k, v = a.split("=", 1)
                args[k] = v

        print(f"🔌 连接 MCP 服务器: {url}")
        async with mcp_client_session(url) as session:
            print(f"\n▶️  调用工具: {tool_name}({args})")
            try:
                result = await session.call_tool(tool_name, args)
                print(f"\n📤 返回结果:\n{result}")
            except Exception as e:
                print(f"\n❌ 错误: {e}")


from contextlib import asynccontextmanager
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

@asynccontextmanager
async def mcp_client_session(url: str):
    async with streamablehttp_client(url=url) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            yield session


if __name__ == "__main__":
    asyncio.run(main())
