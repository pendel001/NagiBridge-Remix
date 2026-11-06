# -*- coding: utf-8 -*-
"""🧺 出货箱能不能被 AI 看见 —— 纯 Python 自验（不起服务、不碰游戏）。2026-11-01

## 恒原话
> 「ai **似乎没找到出货箱**。难怪老是把东西存箱子。**出货箱有任何提示或上单子吗？**」

## 真机现场（`scripts/sessions/session_log.jsonl` 11-01）
```
10:48:04  map query function="出货箱 shipping bin"
          -> 🗺️ 没找到「出货箱 shipping bin」相关地点——换个说法（买种子/钓鱼/挖矿/商店…）
10:48:24  storage store all=True
          -> 🧺 智能堆叠·全存腾空间 | Farm | ✅ 进 (58,14) 17种: Seaweed…Herring…Sardine…
10:48:35  social send "鱼全存箱子了，**出货箱我不太会操作**，你帮我丢吧。"
```
⇒ 两条病根（都不是"AI 笨"）：
  ① `map query` 把**整句**当一个关键词（`kws = {q}`）⇒ **带空格就永远命不中**；
  ② 状态条里**根本没有出货箱**：`.locations.POI` 刻意不收农场设施（`locations.py:535`），
     而 `🗺️ 可:` 那行对 **Farm 整行都不印**（`nagi_mcp_server.py:2525` 的排除名单）
     ⇒ 在自家农场，AI 从任何一处都读不到"箱子在哪、怎么投"。

## 钉死五条
  ① 多词查询会被切开（`出货箱 shipping bin` ⇒ 三个词）；单短语/同义词照旧；
  ② 农场**报主出货箱**：坐标问游戏（`/map` buildings），且必须写明"**过夜才结算**"+"不是存箱子"；
  ③ 别的图**不报**主出货箱（它不在那儿）；
  ④ 迷你出货箱那一条照旧（海滩那只是恒真放的）；
  ⑤ 同图不重扫（缓存还在，别每调都刷 `/map`）。
"""
import io
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []
CALLS = {"map": 0, "surr": 0}


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_real = {"map_data": M.api.map_data, "surroundings": M.api.surroundings}


def _fake_map(buildings):
    def _f():
        CALLS["map"] += 1
        return {"buildings": buildings}
    return _f


def _fake_surr(tiles):
    def _f(radius=10):
        CALLS["surr"] += 1
        return {"tiles": tiles, "center": {"x": 50, "y": 25}}
    return _f


try:
    print("① `map query` 多词查询要能切开（真机：`出货箱 shipping bin` 查不到）")
    _kws = M._map_query_keywords("出货箱 shipping bin")
    for _t in ("出货箱", "shipping", "bin"):
        ck(f"…关键词里有 `{_t}`", _t in _kws, str(sorted(_kws)))
    ck("…单短语照旧（`钓鱼` 仍带同义词 钓/鱼竿）",
       {"钓鱼", "钓", "鱼竿"} <= M._map_query_keywords("钓鱼"))
    ck("…空串不炸", isinstance(M._map_query_keywords(""), set))
    ck("…这个词表**真的能命中**那条 POI（出货箱在 `locations.POI`，note 里有 bin）",
       any(k.lower() in (p.get("note", "") + n).lower()
           for n, p in M.locations.POI.items() for k in _kws),
       "no hit")

    print("\n② 农场要报主出货箱（坐标问游戏）+ **站在箱子周围**才给说明")
    M._SHIPBIN_SEEN.update({"loc": None, "farm_bin": None, "mini": None})
    M.api.map_data = _fake_map([{"type": "Shipping Bin", "x": 71, "y": 14,
                                 "doorX": 72, "doorY": 15}])
    M.api.surroundings = _fake_surr([])
    # 🔴 2026-11-06 恒：「说明注入在**箱子周围一两圈**，**常驻的只要坐标**」⇒ 两档分开验：
    #    · 离得远（人在 (30,30)）：一行只有坐标，**一个字说明都不许有**（省 token 的全部意义）
    #    · 走到跟前（(72,16)，箱子在 (72,15) ⇒ 相邻 1 格）：才把说明补齐
    _far = M._shipbin_hint("Farm", 30, 30)
    ck("…**离得远 ⇒ 只有坐标**", _far == "🧺 出货箱 (72,15)", repr(_far))
    ck("…远处**不许**带「过夜/结算/取不回来」这些说明",
       not any(w in _far for w in ("过夜", "结算", "取不回来", "intent", "存箱子")), _far)
    _farm = M._shipbin_hint("Farm", 72, 16)
    ck("…走到旁边（相邻 1 格）⇒ 有那一行", "出货箱" in _farm, _farm)
    ck("…坐标取**游戏的 doorX/doorY**（(72,15)），不是写死的 (71,14)", "(72,15)" in _farm, _farm)
    ck("…写明「**过夜才结算**」（别让它以为立刻到账）", "过夜" in _farm and "结算" in _farm, _farm)
    # 🔴 2026-11-05 改口：这一行原来教 `menu(ops="bin", sell_all=True)` 全投 ——
    #    真机上 AI **照念了**，一把把整包全投了，而**取不回来**。
    #    ⇒ 现在只许指单子（一件一件挑），并且必须说清「取不回来」。
    #    ⚠️ 判据写成"**不许出现** sell_all"是一条**反向**断言：它专门防"顺手把这句加回去"。
    ck("…**不再**教 `sell_all`（那正是 AI 全投的来路）", "sell_all" not in _farm, _farm)
    ck("…改成指单子：`intent(ops=\"show\")` → 「投出货箱…」",
       "intent" in _farm and "投出货箱" in _farm, _farm)
    ck("…并写明「**取不回来**」（这是不可逆操作的前提）", "取不回来" in _farm, _farm)
    ck("…单件仍可点名 `menu(ops=\"bin\", name=\"物品名\")`",
       'menu(ops="bin", name="物品名")' in _farm, _farm)
    ck("…点明「**不是**存箱子」（真机就是存箱子了）", "不是" in _farm and "存箱子" in _farm, _farm)
    ck("…必须写「**不是立刻拿钱**」（不许让人以为当晚到账）", "不是立刻拿钱" in _farm, _farm)
    # 周围"一两圈"的边界：隔 2 格还算旁边；隔 3 格就只剩坐标（太远给说明=白付 token）
    ck("…离 2 格（箱子 (72,15) / 人在 (74,17)）仍给说明",
       "取不回来" in M._shipbin_hint("Farm", 74, 17))
    ck("…离 4 格就只给坐标",
       M._shipbin_hint("Farm", 76, 19) == "🧺 出货箱 (72,15)",
       repr(M._shipbin_hint("Farm", 76, 19)))

    print("\n③ 别的图**不报**主出货箱（它不在那儿）")
    M._SHIPBIN_SEEN.update({"loc": None, "farm_bin": None, "mini": None})
    M.api.map_data = _fake_map([{"type": "Shipping Bin", "x": 71, "y": 14}])
    M.api.surroundings = _fake_surr([])
    _beach = M._shipbin_hint("Beach")
    ck("…`Beach` ⇒ 空（没有迷你箱时）", _beach == "", _beach)

    print("\n④ 迷你出货箱同款两档（恒海滩那只）")
    M._SHIPBIN_SEEN.update({"loc": None, "farm_bin": None, "mini": None})
    M.api.surroundings = _fake_surr([{"x": 12, "y": 7, "object": "Mini-Shipping Bin"}])
    _mini_far = M._shipbin_hint("Beach", 30, 30)
    ck("…离得远 ⇒ 只有坐标", _mini_far == "📦 迷你出货箱 (12,7)", repr(_mini_far))
    _mini = M._shipbin_hint("Beach", 12, 8)
    ck("…报出迷你箱坐标", "(12,7)" in _mini, _mini)
    ck("…走近才写「当夜出货卖掉」「别当仓库」",
       "当夜出货卖掉" in _mini and "别当仓库" in _mini, _mini)

    print("\n⑤ 同图不重扫（缓存还在 —— 别每调都刷 `/map`）")
    _c0 = CALLS["map"], CALLS["surr"]
    M._shipbin_hint("Beach", 12, 8)
    ck("…第二次调 `/surroundings` 没再打", CALLS["surr"] == _c0[1], str(CALLS))
    # ⚠️ 这一条正是 2026-11-06 那个改动的**关键**：缓存的是**坐标**、文案按距离现拼 ——
    #    老实现把整行缓存起来原样返回，走远走近平铺同一句，"周围一两圈才注入"根本做不到。
    M._SHIPBIN_SEEN.update({"loc": None, "farm_bin": None, "mini": None})
    _c1 = CALLS["map"]
    M.api.map_data = _fake_map([{"type": "Shipping Bin", "x": 71, "y": 14, "doorX": 72, "doorY": 15}])
    M.api.surroundings = _fake_surr([])
    M._shipbin_hint("Farm", 72, 16)
    M._shipbin_hint("Farm", 30, 30)
    ck("…在农场连调两次只打一发 `/map`", CALLS["map"] - _c1 == 1, str(CALLS))
    ck("…**同一张图里换位置 ⇒ 文案跟着变**（缓存的是坐标不是整行）",
       "取不回来" in M._shipbin_hint("Farm", 72, 16)
       and M._shipbin_hint("Farm", 30, 30) == "🧺 出货箱 (72,15)")

finally:
    M.api.map_data = _real["map_data"]
    M.api.surroundings = _real["surroundings"]
    M._SHIPBIN_SEEN.update({"loc": None, "line": ""})

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
