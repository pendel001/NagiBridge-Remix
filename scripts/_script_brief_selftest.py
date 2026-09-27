"""🧾 收工/停止简报：别把心跳流水账倒给 AI —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒：「script 能做成简报吗？现在**停止**的时候报所有的**时间体力和条数**实在太多了……
（数量还不准，都是说钓了 0 条）**只报共抛了几竿钓了几条就好了**。」

现场：`script_stop` 原来 `_tail(60)` 整整 60 行、`_bg_finish_tail` 取最后 4 行 ——
而鱼脚本**每 2 秒**打一条 `check: stamina 84%, time 600, 抛~1竿 · 钓上~0条`，
收工时尾巴**全是这种**，真正有用的「**为什么收的手**」被挤出去。

测四件：
  ① 心跳行（`check: …`）**一律不进简报**
  ② 钓鱼脚本 → 简报 = **收手原因 + 总计那一行**（收尾套话不给）
  ③ 别的脚本 → 照旧取最后几行（别把非鱼脚本的尾巴一起砍了）
  ④ 一行都没有 → 空（**不编**）
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def mkjob(name, output):
    j = M._BgJob(name, [])
    j.proc = None
    j.running = False
    j.start_ts = 100.0
    j.end_ts = 190.0
    j.returncode = 0
    j.output = list(output)
    return j


# 真机那次的原样输出（22:18 被 script stop 停掉那趟）
FISH_OUT = [
    "[fish] 🎯 能抛，开始钓（水域固定，之后无需再判死水）",
    "[fish]   check: stamina 29%, time 600, 抛~2竿 · 钓上~0条",
    "[fish]   check: stamina 26%, time 600, 抛~3竿 · 钓上~0条",
    "[fish]   check: stamina 23%, time 600, 抛~4竿 · 钓上~0条",
    "[fish]   check: stamina 20%, time 600, 抛~4竿 · 钓上~0条",
    "[fish]   check: stamina 12%, time 600, 抛~7竿 · 钓上~2条",
    "[fish]   check: stamina 9%, time 600, 抛~8竿 · 钓上~2条",
    "[fish]   收手: 体力不足 (18/270)",
    "[fish] fishbot off, 抛9竿 · 钓上2条",
    "[fish] 收杆完成（鱼线已收回）",
    "[fish] no-sleep: 留在钓点不睡觉",
    "[fish] === fish run complete ===",
]

print("\n① 心跳行不进简报（每 2 秒一条的 `check:` 就是「太多了」的来源）")
j = mkjob("fish_run", FISH_OUT)
ess = M._bg_essence(j)
ck("简报里一行 `check:` 都没有", not any("check:" in ln for ln in ess), str(ess))
ck("…总共 ≤3 行（恒：「只报共抛了几竿钓了几条就好了」）", len(ess) <= 3, str(ess))
ck("12 行的原输出 → 压到 3 行以内（原来 `_tail(60)` 是**整段**倒出来）", len(ess) < len(FISH_OUT) / 3, str(len(ess)))

print("\n② 钓鱼简报 = 收手原因 + 总计那一行")
ck("带出「为什么收的手」", any("收手" in ln for ln in ess), str(ess))
ck("带出总计（抛9竿 · 钓上2条）—— 这就是恒钦点的那句", any("抛9竿" in ln and "钓上2条" in ln for ln in ess), str(ess))
ck("收尾套话（收杆完成/no-sleep/complete）**不给**",
   not any(("收杆完成" in ln or "no-sleep" in ln or "complete" in ln) for ln in ess), str(ess))

print("\n③ 别的脚本照旧取最后几行（别一起砍了）")
j2 = mkjob("mine_run", ["[mine] 下到 41 层", "[mine] 挖到 3 个铁矿", "[mine] 撤退：血低"])
ess2 = M._bg_essence(j2, keep=3)
ck("非鱼脚本：最后 3 行原样", ess2 == ["[mine] 下到 41 层", "[mine] 挖到 3 个铁矿", "[mine] 撤退：血低"], str(ess2))
j3 = mkjob("mine_run", ["[mine] a", "[mine]   check: 心跳", "[mine] 撤退"])
ck("非鱼脚本里的心跳行也滤（同一把尺子）", M._bg_essence(j3, keep=3) == ["[mine] a", "[mine] 撤退"],
   str(M._bg_essence(j3, keep=3)))

print("\n④ 没有可报的不许编")
ck("空输出 → 空（调用方照旧什么也不写）", M._bg_essence(mkjob("fish_run", [])) == [])
ck("全是心跳 → 空，而不是硬凑一行", M._bg_essence(mkjob("fish_run", ["[fish] check: stamina 1%, time 600"])) == [])
ck("`_bg_finish_tail` 空输出 → 空串（不写「脚本原话」空壳）", M._bg_finish_tail(mkjob("fish_run", [])) == "")

print("\n⑤ `_bg_finish_tail` 用的是简报、且仍截断")
tail = M._bg_finish_tail(j)
ck("脚本原话里没有 check: 流水账", "check:" not in tail, tail)
ck("…带着总计那句", "抛9竿" in tail, tail)
long = M._bg_finish_tail(mkjob("x_run", ["y" * 900]))
ck("超长仍截断（别把长输出整段甩给 AI）", len(long) < 400 and long.endswith("…"), str(len(long)))

print("\n⑥ 条数用**游戏自己的累计计数**（恒：「都是说钓了 0 条」）")
import fish_run as F
ck("读得到 fishCaught → 用差值（精确，不是数边沿）",
   F.caught_summary(20, 27, 3) == "钓上7条", F.caught_summary(20, 27, 3))
ck("读不到 → 退回估算并**标明「估算」**（别把估的报成准的）",
   F.caught_summary(-1, 27, 3) == "钓上3条（估算）", F.caught_summary(-1, 27, 3))

print("\n" + ("=" * 46))
print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
