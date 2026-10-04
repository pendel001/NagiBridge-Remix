# -*- coding: utf-8 -*-
"""🚃 煤炭矿车 = `Buildings` 层**索引 194 的地图瓦片**（不是 object）+ 顺手捡煤（恒拍板：开，全开）。

## 恒 2026-10-04 现场 → 拍板
「这个，面朝的」→（我 dump 一圈全空）→「——哦不。似乎又不是同一层了。」→
「**会掉一堆煤**。本来我们也是要做**顺手捡煤**才去测这个玩意儿。」
→ 我提了三条自律（有怪不开/只开 10 格内/开完回读）→ 恒：
「**开**。只是点击一下的事，position+interact 一秒钟，跟下一个操作间也有一定延迟不用等，
 **随缘进包**，**没有白捡的不检的理由**。」＋「**接**」（也接进 `mine_run`）

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
⇒ 不在任何 object/terrain/furniture/clump 层，也没有任何地图属性；唯一钥匙 = **原始瓦片索引**：
   `?scan=TileIndex&layer=Buildings&value=194`（194 未开 / 195 已开）。

这条钉子钉四件事：C# 那把钥匙（`scan=TileIndex` + 能力标记）、Python 认得它、
**两个脚本都能用**（方法放进 `WeaponMixin`）、以及**"开成了"必须回读名单**（不许假成功）。
"""
import contextlib
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
MR = src("mine_run.py")

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

print("② Python：认得它、两个脚本共用、旧 DLL 不装懂")
_mix = block(BC, r"^class WeaponMixin", span=260)
ck("…方法住在 `WeaponMixin`（`BombMiner` 与 `MineBot` 都继承它 ⇒ 一套代码两个脚本用）",
   "def loot_coal_carts" in _mix and "def find_coal_carts" in _mix)
ck("…常量 `COAL_CART_TILE = \"194\"`", 'COAL_CART_TILE = "194"' in BC)
_find = block(BC, r"def find_coal_carts", span=30)
ck("…名单走 `scan=TileIndex` + `layer=Buildings` + `value=194`",
   '"scan": "TileIndex"' in _find and '"layer": "Buildings"' in _find and '"value"' in _find)
ck("…旧 DLL 读不了时**如实报**（不假装「没矿车」）",
   "还读不了煤炭矿车" in _find and "return []" in _find)
ck("…**能力标记**当门：没有 `byIndex=true` 就报读不了（老 DLL 会静默回 count:0）",
   'r.get("byIndex") is not True' in _find and "别当成本层没矿车" in _find)
_loot = block(BC, r"def loot_coal_carts", span=50)
ck("…**没有怪门**（恒拍板：只是点击一下的事）", "nearby_monsters" not in _loot)
ck("…**没有距离门**（本层全开，不再 max_dist 过滤）",
   "max_dist" not in _loot and "四个都点" not in _loot)
ck("…点完**不 sleep**（恒：「跟下一个操作间也有一定延迟不用等」）",
   "time.sleep" not in _loot)
ck("…点完**回读名单**验真（194→195 才是权威判据）", "left = len(self.find_coal_carts())" in _loot)
ck("…点过但一辆没开成 ⇒ **明确报出来**、不算开成",
   "一辆都没开成" in _loot and "done = max(0, len(carts) - left)" in _loot)
ck("…两次踏勘都接上了（bomb_mine.clear_floor 每层 / mine_run.run_rush 每层）",
   "self.loot_coal_carts()" in BM and "self.loot_coal_carts()" in MR)
ck("…接的两处都包了 try（顺手活出岔子不许影响主流程）",
   "不影响炸矿" in BM and "不影响下矿" in MR)

# ══════════════════════════════════════════════════════════════════════════
print("③ 行为钉：真跑一遍 `loot_coal_carts`（假 bot，不发 HTTP）")
sys.path.insert(0, HERE)
import bomb_common as _bc                                          # noqa: E402


def _mk(carts, shrink_on_interact=True, by_index=True):
    """假矿工：`/tile_props` 给 carts；`/interact` 后（默认）名单少一辆。
    `position_safe` 会真的把假人挪过去（不然"站不上"那条路会挡掉全部行为）。"""
    bot = _bc.BombMiner(port=7843, host_port=7842)
    st = {"carts": list(carts), "interacts": [], "px": 5, "py": 5}

    def _get(ep, params=None, host=False):
        if ep != "/tile_props":
            return {"ok": True, "debris": []}
        d = {"ok": True, "count": len(st["carts"]),
             "hits": [{"layer": "Buildings", "x": x, "y": y, "value": "194", "sheet": "mine"}
                      for (x, y) in st["carts"]]}
        if by_index:
            d["byIndex"] = True
        return d

    def _post(ep, data=None, host=False):
        if ep == "/interact":
            st["interacts"].append(((data or {}).get("x"), (data or {}).get("y")))
            if shrink_on_interact and st["carts"]:
                st["carts"].pop(0)                     # 开过 ⇒ 194 变 195 ⇒ 名单少一辆
        return {"ok": True}

    bot._get = _get
    bot._post = _post
    bot.state = lambda host=False: {"player": {"x": st["px"], "y": st["py"]},
                                    "location": {"uniqueName": "UndergroundMine131"}}
    bot.position_safe = lambda x, y, **kw: (st.update(px=x, py=y), True)[1]
    bot.face_toward = lambda x, y: None
    bot.nearby_monsters = lambda radius=6, around=None: [("Slime", 6, 6, 10, 10, 1)]   # 有怪也照开
    return bot, st


buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5), (30, 20)])        # 一辆近、一辆远
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…**远的也开**（没有距离门）：两辆都点了", len(st["interacts"]) == 2, f"{st['interacts']}")
ck("…回读确认开成 2 辆（返回 2）", n == 2, f"n={n}")
ck("…`/interact` 打在**矿车那一格**上", {c[0] for c in st["interacts"]} == {9, 30}, f"{st['interacts']}")
ck("…日志报「还剩 N 辆」", "还剩 0 辆" in out, out[-200:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)])
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…⚠️ 贴脸有怪也照开（恒：没有白捡的不捡的理由）", n == 1 and len(st["interacts"]) == 1,
   f"n={n} {st['interacts']}")
ck("…不 sleep（跑完耗时 < 0.5s —— 代码里没有 sleep，这里只是形态检查）", "sleep" not in out)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)], shrink_on_interact=False)   # 点了但名单没变 = 假成功
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…⚠️ 点了但名单没变 ⇒ **不算开成**（返回 0）", n == 0, f"n={n}")
ck("…并且如实说「一辆都没开成」", "一辆都没开成" in out, out[-200:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)], by_index=False)             # 装成老 DLL：收参数但没 byIndex 标记
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…⚠️ 老 DLL（没有 byIndex 标记）⇒ 报「不支持」且**不动手**（不许把 count:0 当没矿车）",
   n == 0 and st["interacts"] == [] and "不支持" in out, f"n={n} {st['interacts']} {out[-160:]}")

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([(9, 5)])
    st["px"], st["py"] = 9, 5                            # 已经站在矿车那一格 ⇒ 四邻都能站（position_safe 恒真）
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…能站上就打点（四邻任一站得住都算）", "四邻都站不上" not in out, out[-200:])

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    bot, st = _mk([])
    n = bot.loot_coal_carts()
out = buf.getvalue()
ck("…本层没矿车 ⇒ 静默返回 0（不刷屏）", n == 0 and "🚃" not in out, out[-160:])

print("")
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("✅ 全部通过（矿车身份 + 全开不挑 + 两个脚本共用 + 回读验真）")
