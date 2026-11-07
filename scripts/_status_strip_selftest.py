# -*- coding: utf-8 -*-
"""🧪 2026-11-08 三笔：状态条被并发吞掉 / fish 超时没打点 / 沙漠没解锁别劝去。逐条验。"""
import os
import sys
import threading

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "nagi_mcp_server.py"), encoding="utf-8").read()

try:
    print("\n① `_OPS_INNER` 是**每线程**的（并发请求不再互相吞状态条）")
    ck("①类型不是全局 dict", not isinstance(M._OPS_INNER, dict), type(M._OPS_INNER).__name__)
    M._OPS_INNER["n"] = 0
    _seen = {}

    def _worker():
        M._OPS_INNER["n"] = 3          # 这个线程里"正在执行域 op"
        _seen["worker"] = M._OPS_INNER["n"]

    _t = threading.Thread(target=_worker)
    _t.start()
    _t.join()
    ck("①别的线程里 n=3", _seen.get("worker") == 3, str(_seen))
    ck("①本线程仍是 0（不被别的请求串味）", M._OPS_INNER["n"] == 0, str(M._OPS_INNER["n"]))

    print("\n② `_with_state`：本线程 n>0 才闭嘴，n=0 就照建")
    _orig_suffix = M._state_suffix
    M._state_suffix = lambda result, force_full=False: "\n\n╌SEP╌\n条"
    try:
        M._OPS_INNER["n"] = 1
        _a = M._with_state("正文")
        ck("②本线程 n>0 ⇒ 只有正文", _a == "正文", repr(_a))
        M._OPS_INNER["n"] = 0
        _b = M._with_state("正文")
        ck("②n=0 ⇒ 带上状态条", "╌SEP╌" in _b, repr(_b))
    finally:
        M._state_suffix = _orig_suffix
        M._OPS_INNER["n"] = 0

    print("\n③ fish 超时那笔：**每个工具调用都打点** + 快撞 60s 就不再挂")
    _wrap = _SRC.split("async def _in_thread(*a, **kw):")[1][:600]
    ck("③注册版里打了 `_CALL_T0`", "_CALL_T0.set(time.time())" in _wrap, _wrap[:200])
    ck("③`_bg_block_until_wake` 扣掉已花时间",
       "interval = max(3.0, interval - _spent)" in _SRC)
    ck("③已花 ≥50s ⇒ 不再挂（interval=0）", "if _spent >= 50:" in _SRC)

    print("\n④ 沙漠节：没解锁沙漠就不注入「festival go」那句")
    _body = _SRC.split("# 🎪 节日提醒")[1][:1400]
    ck("④判据走导航的解锁检查", "_map_go_unlock_check(_fm)" in _body, _body[:300])
    ck("④它挡在「用 festival go 去参加」**之前**",
       0 < _body.find("_map_go_unlock_check") < _body.find("festival go 去参加"),
       f"unlock@{_body.find('_map_go_unlock_check')} hint@{_body.find('festival go 去参加')}")
    ck("④`Desert` 确实在未解锁表里（这条判据有对象）",
       '"Desert"' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "navigation.py"), encoding="utf-8").read())
finally:
    M._OPS_INNER["n"] = 0

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 状态条不再被并发吞、fish 打点+封顶、沙漠没解锁不劝去。")
