"""🧵 工具管线形态钉子 —— 纯 Python 自验（不起服务、不碰游戏）。2026-10-07

起因（恒问"升级 mcp 包那条指什么"时的复盘）：`補77` 把那次会话崩溃归因成
「**长同步 op 卡住事件循环** × 前端超时」，但翻 git 才发现 **补43 `dd8dabf`（2026-10-06 11:26）
早就把工具体丢到工作线程了**，而崩的那个进程（PID 24036，见 CHANGELOG 补76 那条
`32632→24036`）是 **10-07 11:22** 起的 ⇒ **它身上已经带着这套管线** ⇒ 原来那个归因**站不住**。
更糟的是 `nagi_mcp_server.py` 里那段路标注释还停在补41 的结论
（"丢线程那条路我试过、当场撤回，别再照它写"），**跟它正上方的代码正好相反** ——
谁照着那段注释动手，就会把服务改回"一挂住就不接请求"。这个钉子就是拦这个的。

测六件：
  ① 注册表里 16 个白名单工具**全部** `is_async=True` 且真的是 coroutine function
  ② **模块名仍是同步版**（域内直调 / 自验 `M.daily(...)` 靠它；变成 async 就会拿到 coroutine）
  ③ 菜单闸门深度持有者是 **threading.local**（并发调用不许互相算错）
  ④ 行为证据：工作线程里睡觉时**事件循环照样推进**（`anyio.to_thread.run_sync` —— 注册版走的就是这条）
  ⑤ 库实据：`mcp` 1.x 的**同步**分支是**在事件循环里直接调**（无 threadpool）——这条是"为什么必须自己丢线程"；
     哪天升级到 2.x（那里已经是 `to_thread`）它会**报红**，那是提醒你回来改注释、不是管线坏了
  ⑥ 那段历史路标的**更正版还在**，且旧的错误结论（"等恒点头再做"那套）没有被人搬回来
"""
import asyncio
import inspect
import os
import sys
import threading
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M  # noqa: E402

SRC = os.path.abspath(__file__).replace("_tool_thread_selftest.py", "nagi_mcp_server.py")

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


tools = {t.name: t for t in M.mcp._tool_manager.list_tools()}
kept = sorted(M._KEEP_TOOLS)

print("\n① 16 个白名单工具在注册表里**全是 async**（这是「活干在工作线程」的前提）")
ck("白名单 16 个", len(kept) == 16, f"实际 {len(kept)}")
missing = [n for n in kept if n not in tools]
ck("16 个都在注册表里（没有被域过滤误杀）", not missing, f"缺 {missing}")
not_async = [n for n in kept if n in tools and not tools[n].is_async]
ck("全部 is_async=True", not not_async, f"还是同步的: {not_async}")
not_coro = [n for n in kept if n in tools and not inspect.iscoroutinefunction(tools[n].fn)]
ck("注册的函数对象都是 coroutine function", not not_coro, f"{not_coro}")

print("\n② 模块名 = **同步版**（内部直调 / 自验直调不许拿到 coroutine）")
sync_names = ["farm", "mine", "fish", "daily", "map", "script", "intent", "menu", "check", "help"]
bad = [n for n in sync_names if inspect.iscoroutinefunction(getattr(M, n, None))]
ck("M.farm/M.daily/M.intent… 仍不是 coroutine", not bad, f"被改成 async 了: {bad}")
ck("但注册表里那个同名工具是 async（两者不同物）",
   all(tools[n].is_async for n in sync_names if n in tools))

print("\n③ 闸门深度 = 线程局部")
ck("_MENU_GATE_DEPTH 是 threading.local", isinstance(M._MENU_GATE_DEPTH, threading.local),
   type(M._MENU_GATE_DEPTH).__name__)

print("\n④ 行为：工作线程睡觉时事件循环照样推进（注册版走的就是这条路）")


async def _liveness():
    ticks = 0

    async def _tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    t = asyncio.create_task(_tick())
    import anyio
    t0 = time.time()
    await anyio.to_thread.run_sync(lambda: time.sleep(0.8))
    dt = time.time() - t0
    t.cancel()
    return ticks, dt


ticks, dt = asyncio.run(_liveness())
ck(f"0.8s 的工作线程睡觉期间 loop 推进了 {ticks} 次 tick", ticks >= 10, f"只 {ticks} 次（{dt:.2f}s）")

print("\n⑤ 库实据：mcp 1.x 的同步分支**没有** threadpool（所以必须我们自己丢）")
try:
    from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
    body = inspect.getsource(FuncMetadata.call_fn_with_arg_validation)
    has_thread = "to_thread" in body or "run_sync" in body
    ck("1.x: 同步分支直接 fn(...)，无 threadpool", (not has_thread) and "return fn(" in body,
       "库形态变了（疑似升到 2.x）⇒ 回来更新注释与这段钉子，别当管线坏了")
except Exception as e:  # noqa: BLE001
    ck("读得到 FuncMetadata.call_fn_with_arg_validation 源码", False, f"{type(e).__name__}: {e}")

print("\n⑥ 历史路标是**更正版**（旧错误结论不许搬回来）")
src = open(SRC, encoding="utf-8").read()
ck("含有更正版标记「补43 `dd8dabf` 落地」", "补43 `dd8dabf` 落地" in src)
ck("含有历史路标「补41 …失败」", "补41 `91d5eee` 那版" in src)
ck("旧的'等恒点头再做'结论已清掉", "等恒点头再做" not in src)
ck("旧的'别再照它写'（指的是**现状**那条）已清掉", "别再照它写" not in src)

print()
if FAIL:
    print(f"❌ {len(FAIL)} 项不过：" + " / ".join(FAIL))
    sys.exit(1)
print("✅ 工具管线形态：全部通过")
