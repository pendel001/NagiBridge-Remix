# -*- coding: utf-8 -*-
"""🌾 收获**只有一个口**：镰刀作物自动换镰刀、没镰刀跳过并报（恒 2026-10-04 两次拍板）。

## 恒的两句话（中间隔了我一次删错）
1. 「镰刀的口应该是用来**普通镰刀金镰刀收小麦苋菜**那些的！！」← 我先把 `scythe_crops()` 当重复口删了
2. 「按理来说，确实也可以**合**……**镰刀作物自动切换成镰刀去收**。**没有镰刀就跳过**，
   等手摘的作物都摘完了**然后报**。」

## 结论（现在的事实口径）
- **只有一个收获口**：`farm(ops='harvest')` / 单子「收 成熟作物」→ `harvest_crops(radius)`
- 能力**搬进脚本** `scythe_crops.py`，按游戏报的 `cropScythe` 分类：
  · **镰刀作物**（小麦/苋菜/水稻/芋头…）→ **自动换最好的那把镰刀**收（普通/金/铱都行，`find_scythe()`）
  · **背包里没有镰刀** → **跳过它们 + 记账**，手摘作物照收，**收完在结果里报**「跳过 N 株镰刀作物：小麦×3…」
  · **手摘作物**（蒜/西瓜…）→ 只有"已领耕种精通 + 有**铱**镰刀"才用镰刀扫
    （⚠️ 普通/金镰刀会把它们**打掉不产物** ⇒ 一律手摘）
- ⚠️ 脚本 `scythe_crops.py` **一个字节没删**（`harvest_crops` 就在跑它；`_ASYNC_TOOLS` 那行是**脚本名**）

📌 教训（第一次删错）：**删口之前要问"这个口承担的哪种场景是别人覆盖不了的"** ——
   当时只比了"同脚本同参数"，漏了"镰刀作物必须用镰刀、而 harvest 的精通分支不一定适用"。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


def src(fn):
    return io.open(os.path.normpath(os.path.join(HERE, fn)), encoding="utf-8").read()


CS = src(os.path.join("..", "ModEntry.cs"))
S = src("nagi_mcp_server.py")
SC = src("scythe_crops.py")

print("① 只有一个收获口（裸镰刀口真删、能力在脚本里）")
ck("…`scythe_crops()` 这个 MCP 口已经没了（tombstone 留档）",
   "def scythe_crops(" not in S and "`scythe_crops()`（裸镰刀口）2026-10-04 真删" in S)
ck("…`farm ops=scythe` 指向 `harvest_crops`（别名，不再是一条独立路）",
   re.search(r'"scythe":\s*harvest_crops,', S) is not None)
ck("…农场指南写了「镰刀作物自动换镰刀 / 没镰刀跳过并报」",
   "镰刀作物自动换镰刀" in S and "背包没镰刀就跳过它们" in S)

print("② 脚本按作物类别分流（能力真的在，不是只在注释里）")
ck("…读到游戏报的 `cropScythe` 后分类", 'hand_only = [m for m in mature if not m["scythe"]]' in SC)
ck("…镰刀作物：有镰刀就自动进镰刀组（**任何镰刀**都行，不要求铱）",
   "scythe_targets = list(scythe_only) if scythe else []" in SC)
ck("…没镰刀：镰刀作物进「跳过」账（**不再 sys.exit 整个退出**）",
   "skip_no_scythe = [] if scythe else list(scythe_only)" in SC
   and 'log("❌ 背包里没有镰刀")' not in SC)
ck("…手摘作物只认「精通 + **铱**镰刀」（普通/金会把它们打掉）",
   "have_iridium = bool(scythe) and scythe.startswith(\"Iridium\")" in SC
   and "if args.scythe and have_iridium:" in SC)
ck("…两趟都跑（镰刀组 / 手摘组各自执行）", "if scythe_targets:" in SC and "if hand_targets:" in SC)
ck("…收完**如实报跳过**（数量 + 都是啥 + 下一步）",
   "株**镰刀作物**（背包里没有镰刀）" in SC and "带把镰刀（普通就行）再来一趟就能收" in SC)
ck("…结果行同时报镰刀/手摘各收了几株", "how = f\"镰刀 {done_scythe} / 手摘 {done_hand}\"" in SC)
ck("…没镰刀时**不再**一句「手摘收不了」了事（旧口径已替换）",
   "手摘收不了——要么去领耕种精通拿铱镰刀" not in SC)

print("③ 交代清楚（防下一个人再删一次）")
ck("…C# 侧 `/surroundings` 仍报 `cropScythe`（判据来源，不许动）", "cropScythe" in CS)
ck("…脚本头部注释写明「它为什么不能删」", "一个字节没删" in SC or "一个字节不删" in SC)
ck("…MCP 侧 tombstone 写明教训", "删口之前要问" in S)

print("")
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("✅ 全部通过（一个收获口 + 自动换镰刀 + 没镰刀跳过并报）")
