# -*- coding: utf-8 -*-
"""⛏️ 活体 A/B：`scan_floor_ores` 新旧过滤器在同一层、同一份扫描上比结果（2026-09-20）

只调 `find_rocks`（**只读**：surroundings + dump_tile），不触发 collect_foragables 的走动。
  旧 = `name == node_name or "Node" in name or "Geode" in name`
  新 = `is_ore_node(name)`
"""
import sys, time
sys.path.insert(0, ".")
import mine_run as M

bot = M.MineBot(7843)


def old_filter(name, node_name):
    return name == node_name or "Node" in name or "Geode" in name


for loc, node in [("UndergroundMine21", "Copper Node"), ("UndergroundMine61", "Iron Node")]:
    bot.warp(loc, 5, 5)
    time.sleep(2.5)
    cur = (bot.state().get("location") or {}).get("name", "")
    if cur != loc:
        print(f"\n[{loc}] ⚠️ warp 没到（现在 {cur}），跳过")
        continue
    rocks = bot.find_rocks(priority_ore=node, radius=30)
    o = [t for t in rocks if old_filter(t[2], node)]
    n = [t for t in rocks if M.is_ore_node(t[2])]
    print(f"\n══ {loc}（priority={node}）══  扫到可敲 {len(rocks)} 个")
    from collections import Counter
    print("  find_rocks 全量:", dict(Counter(t[2] for t in rocks)))
    print(f"  旧过滤器 → {len(o)} 个:", dict(Counter(t[2] for t in o)))
    print(f"  新过滤器 → {len(n)} 个:", dict(Counter(t[2] for t in n)))
    only_new = [t for t in n if t not in o]
    print(f"  🆕 只有新判据收的 {len(only_new)} 个:",
          [(t[2], t[0], t[1]) for t in only_new])
