# -*- coding: utf-8 -*-
"""⛏️ 收口复验：重构后的 `_rock_name` + `is_ore_node` 在**活数据**上认不认困难矿井的铜（2026-09-20）

为什么需要：`_mine_dbg3` 那轮 3 次重进的 21 层**恰好都没铜矿**（矿层随机）⇒ 那轮啥也没验到，
不能拿它当"重构没坏"的证据（恒的规矩：没验到就如实说，别包装成绿的）。
做法：反复换层直到扫到 Copper Node，再断言 `is_ore_node(_rock_name(t))` 为 True。
安全：HP 低于 60 立即停手（层里有怪，别把角色耗死）。
"""
import sys, time
sys.path.insert(0, ".")
import mine_run as M

bot = M.MineBot(7843)
MAX_TRIES = 6
HP_BAIL = 60

for i in range(1, MAX_TRIES + 1):
    hp = (bot.state().get("player") or {}).get("health", 0)
    if hp < HP_BAIL:
        print(f"[{i}] ❌ HP {hp} < {HP_BAIL}，停手不测了")
        break
    bot.warp("UndergroundMine21", 5, 5)
    time.sleep(2.5)
    cur = (bot.state().get("location") or {}).get("name", "")
    if cur != "UndergroundMine21":
        print(f"[{i}] ⚠️ warp 没到（{cur}），重试")
        continue
    rocks = bot.find_rocks(priority_ore="Copper Node", radius=30)
    names = [t[2] for t in rocks]
    kept = [t for t in rocks if M.is_ore_node(t[2])]
    cu = [t for t in kept if t[2] == "Copper Node"]
    print(f"[{i}] HP={hp} 扫到 {len(rocks)} 个；is_ore_node 放行 {len(kept)} 个；铜 {len(cu)} 个 {[(t[2],t[0],t[1]) for t in cu]}")
    if cu:
        print(f"\n✅ 收口通过：第 {i} 次换层遇到铜矿，`_rock_name` 认成 {cu[0][2]!r}、"
              f"`is_ore_node` 放行 ⇒ 重构后这条链是通的")
        break
else:
    print(f"\n⚠️ {MAX_TRIES} 次换层没遇到有铜的层 —— 没拿到样本，不敢说验过了")
