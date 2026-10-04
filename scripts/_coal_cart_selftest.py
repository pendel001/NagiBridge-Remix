# -*- coding: utf-8 -*-
"""🚃 煤炭矿车 = `Buildings` 层**索引 194 的地图瓦片**（不是 object）+ 顺手捡煤的三条自律。

## 恒 2026-10-04 现场
「这个，面朝的」→ 我 dump 了一圈全都是空；「——哦不。似乎又不是同一层了。」；
「会掉一堆煤。本来我们也是要做顺手捡煤才去测这个玩意儿。」

## 为什么三处探针都指不出它（反编译 `MineShaft.checkAction`，`MineShaft.decompiled.cs:3097`）
```
case 194:
    playSound("openBox"); playSound("Ship");
    map.RequireLayer("Buildings").Tiles[tileLocation].TileIndex++;          // 194 → 195（开过就变）
    map.RequireLayer("Front").Tiles[tileLocation.X, tileLocation.Y-1].TileIndex++;
    Game1.createRadialDebris(this, 382, x, y, 6, resource:false, -1, item:true);  // 382 = 煤
    updateMineLevelData(2, -1);                                            // 煤炭矿车计数 -1
    return true;
```
⇒ 它**不在任何 object/terrain/furniture/clump 层**，也**没有任何地图属性**（所以 `scan=Action` 是 0）；
   唯一能认出它的是**原始瓦片索引**：`?scan=TileIndex&layer=Buildings&value=194`（194 未开 / 195 已开）。
   这也解释了为什么"`/dump_tile` 加了 otherLayers 之后还是空" —— 那四层加得对，但这个活在第五层。

这条钉子钉三件事：C# 那把钥匙（scan=TileIndex）、Python 认得它、以及**"开了没有"必须回读名单**（不许假成功）。
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


def block(text, start_pat, span=60):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.search(start_pat, ln):
            return "\n".join(lines[i:i + span])
    return ""


CS = src(os.path.join("..", "ModEntry.cs"))
BC = src("bomb_common.py")
BM = src("bomb_mine.py")

print("① C# 那把钥匙：`/tile_props` 能读**原始瓦片索引**（属性那条路永远看不见矿车）")
_tp = block(CS, r"模式一：全图扫某个属性", span=50)
ck("…认 `scan=TileIndex`（大小写都认）",
   'scan.Equals("TileIndex", StringComparison.OrdinalIgnoreCase)' in _tp
   or 'scan.Equals("tileIndex", StringComparison.OrdinalIgnoreCase)' in _tp)
ck("…读的是 `t.TileIndex`（原始索引，不是属性表）", "t.TileIndex.ToString()" in _tp)
ck("…回包带 `sheet`（哪张图集）", "t.TileSheet?.Id" in _tp)
ck("…注释里留了反编译现场（194→195 / 382 煤 / 计数 -1）",
   "194" in _tp and "382" in _tp and "195" in _tp)
ck("…回包带**能力标记** `byIndex`（老 DLL 收这个参数会静默回 count:0 ⇒ 必须有这道门）",
   "byIndex, count = hits.Count" in CS)
_single = block(CS, r"模式二：单格全属性", span=40)
ck("…单格模式也加了 `layerTiles`（每层原始索引，补掉「装饰看不见」这个盲区）",
   "layerTiles" in _single and "tileIndex = t.TileIndex" in _single)
ck("…`layerTiles` 进了回包", "layerTiles," in CS)

print("② Python 认得它：名单 + 顺手 + 回读验真")
ck("…常量 `COAL_CART_TILE = \"194\"`", 'COAL_CART_TILE = "194"' in BC)
_find = block(BC, r"def find_coal_carts", span=30)
ck("…名单走 `scan=TileIndex` + `layer=Buildings` + `value=194`",
   '"scan": "TileIndex"' in _find and '"layer": "Buildings"' in _find and '"value"' in _find)
ck("…旧 DLL 读不了时**如实报**（不假装「没矿车」）",
   "还读不了煤炭矿车" in _find and "return []" in _find)
ck("…**能力标记**当门：没有 `byIndex=true` 就报读不了（老 DLL 会静默回 count:0）",
   'r.get("byIndex") is not True' in _find and "别当成本层没矿车" in _find)
_loot = block(BC, r"def loot_coal_carts", span=55)
ck("…4 格内有怪就不开（不为一车煤挨打）", "nearby_monsters(4)" in _loot)
ck("…只开 max_dist 内的（远的报一句、不绕路）", "max_dist" in _loot and "不绕路" in _loot)
ck("…开完**回读名单**验真（194→195 是权威判据）",
   "left = len(self.find_coal_carts())" in _loot and "名单没变" in _loot)
ck("…开成功才去踩煤", "collect_coal_near(cx, cy)" in _loot)
_bm = block(BM, r"顺手捡煤", span=16)
ck("…`bomb_mine.clear_floor` 每层顺手看一眼（刚进这层时）",
   "self.loot_coal_carts()" in _bm and "for attempt in range(MAX_FLOOR_ATTEMPTS)" in _bm)
ck("…顺手捡煤出岔子**不许**影响炸矿（包了 try）", "不影响炸矿" in _bm)

# ══════════════════════════════════════════════════════════════════════════
print("③ 行为钉：真跑一遍 `loot_coal_carts`（假 bot，不发 HTTP）")
sys.path.insert(0, HERE)
import bomb_common as _bc                                          # noqa: E402

LOG = []


def _mk(carts, monsters_near=False, shrink_on_interact=True, by_index=True):
    """造一个假矿工：`/tile_props` 给 carts，`/interact` 后名单会少一辆。
    `by_index=False` = 装成"老 DLL"（收参数但回包里没有 byIndex 标记）。"""
    bot = _bc.BombMiner(port=7843, host_port=7842)
    state = {"carts": list(carts), "interacts": [], "props_calls": 0, "monsters": monsters_near}

    def _get(ep, params=None, host=False):
        if ep != "/tile_props":
            return {"ok": True, "debris": []}
        d = {"ok": True, "count": len(state["carts"]),
             "hits": [{"layer": "Buildings", "x": x, "y": y, "value": "194", "sheet": "mine"}
                      for (x, y) in state["carts"]]}
        if by_index:
            d["byIndex"] = True
        return d

    bot._get = _get
    bot.state = lambda host=False: {"player": {"x": 5, "y": 5}, "location": {"uniqueName": "UndergroundMine131"}}
    bot.my_location = lambda: "UndergroundMine131"
    bot.nearby_monsters = lambda radius=6, around=None: ([("Slime", 6, 6, 10, 10, 1)] if state["monsters"] else [])
    bot.scan_rocks = lambda radius=14: ([], {(4, 5), (5, 4)}, (5, 5))
    bot.find_stand_tile = lambda tx, ty, occupied: (4, 5, tx - 4, ty - 5)
    bot.position_safe = lambda x, y, **kw: True
    bot.face_toward = lambda x, y: None
    bot.debris = lambda host=False: {"ok": True, "debris": []}
    bot.inventory_free_slots = lambda: 20
    bot.natural_walk = lambda *a, **k: True

    def _interact(ep, data=None, host=False):
        if ep == "/interact":
            state["interacts"].append((data or {}).get("x", -1))
            if shrink_on_interact and state["carts"]:
                state["carts"].pop(0)          # 开过 ⇒ 194 变 195 ⇒ 名单少一辆
        return {"ok": True}
    bot._post = _interact
    return bot, state


import contextlib                                                # noqa: E402

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)])
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…名单里近处那辆被开掉（返回 1）", n == 1, f"n={n} out={out[-200:]}")
ck("…`/interact` 打在**矿车那一格** (9,5) 上", st["interacts"] == [9], f"{st['interacts']}")
ck("…日志如实报「还剩 N 辆」", "还剩 0 辆" in out, out[-200:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)], monsters_near=True)
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…⚠️ 有怪贴脸 ⇒ **一辆都不开**（`/interact` 一次没发）", n == 0 and st["interacts"] == [], f"n={n} {st['interacts']}")
ck("…并且如实说「有怪贴脸」", "有怪贴脸" in out, out[-160:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)], shrink_on_interact=False)   # 点了但名单没变 = 假成功
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…⚠️ 点了但名单没变 ⇒ **不算开成功**（不报假成功）", n == 0, f"n={n}")
ck("…并且如实说「名单没变」", "名单没变" in out, out[-200:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(40, 40)])                            # 40 格以外
    n = bot.loot_coal_carts(max_dist=10)
out = buf.getvalue()
ck("…太远 ⇒ 不绕路（0 辆）", n == 0 and "不绕路" in out, out[-160:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([])
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…本层没矿车 ⇒ 静默返回 0（不刷屏）", n == 0 and "🚃" not in out, out[-160:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)], by_index=False)          # 装成老 DLL：收参数但没 byIndex 标记
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…⚠️ 老 DLL（没有 byIndex 标记）⇒ 报「不支持」且**不动手**（不许把 count:0 当没矿车）",
   n == 0 and st["interacts"] == [] and "不支持" in out, f"n={n} {st['interacts']} {out[-160:]}")

print("")
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("✅ 全部通过（矿车身份 + 顺手三条自律 + 回读验真）")
