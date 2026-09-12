# -*- coding: utf-8 -*-
"""复现 crab_bait 卡死并抓栈（2026-09-12）。
走**和 MCP 工具完全同一条链**（M.fish(ops=...) → _ops_run → _crab_bait → _with_state），
30s 后 faulthandler 把栈打出来并退出——不用猜是哪个调用卡住。"""
import faulthandler, io, os, sys
if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
faulthandler.dump_traceback_later(30, exit=True)   # 30s 还没完 → 打栈 + 退出
import nagi_mcp_server as M
print(">>> 调 M.fish(ops='crab_bait') …", flush=True)
print(M.fish(ops="crab_bait"))
print(">>> 正常返回了", flush=True)
