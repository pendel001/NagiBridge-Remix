# -*- coding: utf-8 -*-
"""🧪 通知缓冲（`_plan_notices` / `_plan_notify` / `_plan_drain_notices`）—— **离线自验**。

跑法: PYTHONIOENCODING=utf-8 python scripts/_plan_notice_selftest.py

为什么有它（恒 2026-10-07 真机）：「兜底睡觉一直谎报，现在还在持续吗？」
  · 那条「💤 新的一天开始了」是 11:18 发的，他随后**不保存重进这一天**（世界倒回 19 日）
    ⇒ 它在**后来那次回执**里读起来就像谎报。根因不是内容错，是**通知没时间戳、也永不过期**。
  · 本钉子钉三件事：① 每条都带 `[HH:MM]` 发出时刻 ② 过期（> `_PLAN_NOTICE_TTL`）整条丢
    ③ 上限 20 条不涨。
"""
import io
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import nagi_mcp_server as M          # noqa: E402  —— 安全：末尾才有 __main__ 守卫

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _reset():
    with M._plan_lock:
        M._plan_notices.clear()


def main():
    print("① 每条通知都带发出时刻 `[HH:MM]`")
    _reset()
    M._plan_notify("🌙 兜底睡觉结果: 💤 已睡在轮回的床上，新的一天开始了！")
    out = M._plan_drain_notices()
    ck("回包带 `[HH:MM] ` 前缀", bool(re.match(r"^\[\d{2}:\d{2}\] ", out)), repr(out[:40]))
    ck("……且原文没被改写", "新的一天开始了" in out, out)

    print("② drain 过就清空（第二次取是空的）")
    ck("第二次 drain ⇒ 空串", M._plan_drain_notices() == "", "")

    print("③ 过期通知**整条丢掉**（这就是恒撞到的那条：发完世界被倒回，它却还在后面回执里出现）")
    _reset()
    with M._plan_lock:
        M._plan_notices.append((time.time() - (M._PLAN_NOTICE_TTL + 5), "🌙 兜底睡觉结果: 💤 新的一天开始了！"))
        M._plan_notices.append((time.time(), "🆕 刚刚发生的事"))
    out2 = M._plan_drain_notices()
    ck("旧的**没了**（不许冒充刚发生）", "新的一天开始了" not in out2, out2)
    ck("新的还在", "刚刚发生的事" in out2, out2)
    ck("……且缓冲被清干净", M._plan_drain_notices() == "", "")

    print("④ 全是过期 ⇒ 回空串（不吐一行空壳）")
    _reset()
    with M._plan_lock:
        M._plan_notices.append((time.time() - 999, "⏳ 很旧的通知"))
    ck("回空串", M._plan_drain_notices() == "", "")
    ck("……缓冲也清了", not M._plan_notices, str(M._plan_notices))

    print("⑤ 上限 20 条（超了丢最老的）")
    _reset()
    for i in range(25):
        M._plan_notify(f"#{i}")
    kept = len(M._plan_notices)
    ck("缓冲 ≤20", kept <= 20, str(kept))
    txt = M._plan_drain_notices()
    ck("留的是**最新的**那批（#5 之后）", "#24" in txt and "#0" not in txt, txt[:60])

    print("⑥ 源码钉子：三处生产者都走 `_plan_notify`、没有谁再直接 append 字符串")
    import inspect
    src = inspect.getsource(M)
    ck("`_plan_drain_notices` 里带 TTL 过滤的写法在",
       "_PLAN_NOTICE_TTL" in inspect.getsource(M._plan_drain_notices), "")
    ck("节假哨兵那处改走 `_plan_notify`",
       "_plan_notices.append(note)" not in src, "")
    ck("`_plan_load_state` 那处改走 `_plan_notify`",
       '_plan_notices.append("🔄' not in src, "")

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 项没过: {FAIL}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
