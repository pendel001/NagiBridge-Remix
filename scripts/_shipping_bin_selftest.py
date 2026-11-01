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

    print("\n② 农场要报主出货箱（坐标问游戏，说明「过夜才结算」）")
    M._SHIPBIN_SEEN.update({"loc": None, "line": ""})
    M.api.map_data = _fake_map([{"type": "Shipping Bin", "x": 71, "y": 14,
                                 "doorX": 72, "doorY": 15}])
    M.api.surroundings = _fake_surr([])
    _farm = M._shipbin_hint("Farm")
    ck("…有那一行", "出货箱" in _farm, _farm)
    ck("…坐标取**游戏的 doorX/doorY**（(72,15)），不是写死的 (71,14)", "(72,15)" in _farm, _farm)
    ck("…写明「**过夜才结算**」（别让它以为立刻到账）", "过夜" in _farm and "结算" in _farm, _farm)
    ck("…点名怎么投：`menu(ops=\"bin\", sell_all=True)`", 'menu(ops="bin", sell_all=True)' in _farm, _farm)
    ck("…点明「**不是**存箱子」（真机就是存箱子了）", "不是" in _farm and "存箱子" in _farm, _farm)
    ck("…必须写「**不是立刻拿钱**」（不许让人以为当晚到账）", "不是立刻拿钱" in _farm, _farm)

    print("\n③ 别的图**不报**主出货箱（它不在那儿）")
    M._SHIPBIN_SEEN.update({"loc": None, "line": ""})
    M.api.map_data = _fake_map([{"type": "Shipping Bin", "x": 71, "y": 14}])
    M.api.surroundings = _fake_surr([])
    _beach = M._shipbin_hint("Beach")
    ck("…`Beach` ⇒ 空（没有迷你箱时）", _beach == "", _beach)

    print("\n④ 迷你出货箱照旧（恒海滩那只）")
    M._SHIPBIN_SEEN.update({"loc": None, "line": ""})
    M.api.surroundings = _fake_surr([{"x": 12, "y": 7, "object": "Mini-Shipping Bin"}])
    _mini = M._shipbin_hint("Beach")
    ck("…报出迷你箱坐标", "(12,7)" in _mini, _mini)
    ck("…写着「当夜出货卖掉」「别当仓库」", "当夜出货卖掉" in _mini and "别当仓库" in _mini, _mini)

    print("\n⑤ 同图不重扫（缓存还在 —— 别每调都刷 `/map`）")
    _c0 = CALLS["map"], CALLS["surr"]
    M._shipbin_hint("Beach")
    ck("…第二次调 `/surroundings` 没再打", CALLS["surr"] == _c0[1], str(CALLS))
    M._SHIPBIN_SEEN.update({"loc": None, "line": ""})
    _c1 = CALLS["map"]
    M.api.map_data = _fake_map([{"type": "Shipping Bin", "x": 71, "y": 14, "doorX": 72, "doorY": 15}])
    M.api.surroundings = _fake_surr([])
    M._shipbin_hint("Farm")
    M._shipbin_hint("Farm")
    ck("…在农场连调两次只打一发 `/map`", CALLS["map"] - _c1 == 1, str(CALLS))

finally:
    M.api.map_data = _real["map_data"]
    M.api.surroundings = _real["surroundings"]
    M._SHIPBIN_SEEN.update({"loc": None, "line": ""})

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
