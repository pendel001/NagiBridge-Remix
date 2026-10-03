"""🔧 `storage store` 的**工具点名**（借镐子给恒）—— 纯 Python 自验。2026-09-27

恒真机（session_log 1790481204~1790481281）：恒想借镐子，AI 三连失败、最后冲恒喊
「老婆救我！工具存不进箱子！系统不让我放！」。三次都不怪 AI：

  ① `kw={"name":"Pickaxe"}` —— `name` 不是 `store` 的参数，**被静默丢掉**、op 照跑 ⇒
     它以为在存镐子，实际做的是"归位整理"，还回一句「✅ 全部存下，没剩」（假成功）。
  ② `kw={"items":["Pickaxe"]}` —— 参数对了，但 C# 的 `if (keepTools && item is Tool) continue;`
     **静默跳过**工具（`keepTools` 默认 True）⇒ 回包反过来说
     「⚠️ 没匹配到可存的物品（背包里没有指定的？）」——**锅甩给背包**，而镐子明明就在背包里。
  ③ `kw={"what":"tool","items":["Pickaxe"]}` —— `what or items` 让 `what` 优先，`items` 又被吞。

而**工具放进箱子在游戏里完全合法**（恒原话：「工具虽然无法丢弃和卖出，但是放在箱子里借予
是合法的」）—— C# 也只是"默认不搬"，不是"不许搬"。

测四件：
  ① 点名了工具 → **自动放行**（keepTools 翻 False），真存下，并在回包说明为什么
  ② **保护没丢**：没点名（all/归位）时工具照旧不搬（2026-09-11 立的规矩还在）
  ③ 名字在背包里却没搬动 → **不许反过来说"背包里没有指定的"**（给真因，不给猜的那个）
  ④ `what`+`items` 同传 → 合并成一份名单并明说；`name` → `what` 别名归一
"""
import inspect
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


# 真机那一刻的背包（照 session_log 的 `check backpack` 抄；catNum=-99 是 /state 真发的口径）
BAG = [
    {"displayName": "斧头", "name": "Axe", "itemId": "(T)Axe", "catNum": -99, "category": "工具"},
    {"displayName": "十字镐", "name": "Pickaxe", "itemId": "(T)Pickaxe", "catNum": -99, "category": "工具"},
    {"displayName": "镰刀", "name": "Scythe", "itemId": "(W)47", "catNum": -99, "category": "工具"},
    {"displayName": "鲶鱼", "name": "Catfish", "itemId": "(O)Catfish", "catNum": -4, "category": "鱼"},
    {"displayName": "浮木", "name": "Driftwood", "itemId": "(O)Driftwood", "catNum": -20, "category": "垃圾"},
]


def _hit(item, n):
    low = str(n).lower()
    return low in ((item.get("name") or "").lower(),
                   (item.get("displayName") or "").lower(),
                   str(item.get("itemId") or "").lower())


class FakeApi:
    """照抄 C# `HandleStoreAll` 的两条关键判据：`keepTools && item is Tool` 跳过 + `WhatMatches` 过滤。"""

    def __init__(self, inv=None, drop_everything=False):
        self._inv = inv if inv is not None else BAG
        self._drop_everything = drop_everything
        self.calls = []

    def state(self, **kw):
        return {"inventory": self._inv, "player": {}}

    def store_all(self, **kw):
        self.calls.append(kw)
        keep = kw.get("keepTools", True)
        req = kw.get("what") or []
        if req:
            cand = [i for i in self._inv if any(_hit(i, n) for n in req)]
        else:
            # 空 what：只有显式 all=True 才是"全存腾空间"，否则归位（这里当没东西可归位）
            cand = list(self._inv) if kw.get("clear_all") else []
        stored = []
        for i in cand:
            if keep and i.get("catNum") == -99:
                continue                     # ← C# 的 `if (keepTools && item is Tool) continue;`
            stored.append(i)
        if self._drop_everything:
            stored = []
        return {"ok": True, "mode": "smart", "scope": "specified" if req else "all",
                "location": "Farm", "totalFree": 60,
                "stored": [{"item": i["name"], "count": 1, "to": {"x": 58, "y": 14}} for i in stored],
                "leftovers": []}


_real = {n: getattr(M, n) for n in ("api", "_with_state", "_walk_to_chest",
                                    "_storage_default_for_loc", "_primary_chest_for_smart")}
try:
    M._walk_to_chest = lambda x, y: None
    M._storage_default_for_loc = lambda: None
    M._primary_chest_for_smart = lambda: None
    M._with_state = lambda s: s        # 测的是 op 自己那段话，状态条另有用例，这里拼进来只会干扰

    print("\n① 点名工具 → 自动放行（真机原命令重放）")
    M.api = FakeApi()
    out = M.storage_store.__wrapped__(items=["Pickaxe"])
    ck("**真存下了**（不再「没匹配到可存的物品」）", "没匹配到可存的物品" not in out, out)
    ck("…回包如实报进箱（Pickaxe）", "Pickaxe" in out, out)
    ck("…keepTools 被翻成 False 才可能存下（C# 侧真判据）",
       M.api.calls and M.api.calls[-1].get("keepTools") is False, str(M.api.calls[-1:]))
    ck("…并告诉 AI **为什么**（点名了照存 / 工具只是不能丢卖）",
       "点名了，照存" in out and "不能丢" in out, out)

    print("\n② 保护没丢：没点名时工具照旧不搬（2026-09-11 的规矩还在）")
    M.api = FakeApi()
    out = M.storage_store.__wrapped__(all=True)
    ck("…keepTools 仍是 True（不点名就不翻）",
       M.api.calls and M.api.calls[-1].get("keepTools") is True, str(M.api.calls[-1:]))
    ck("…工具一件都没进箱（Pickaxe/Axe/Scythe 都没在 stored 里）",
       not any(t in out for t in ("Pickaxe", "Axe", "Scythe")), out)
    ck("…非工具的照存（Catfish/Driftwood）", "Catfish" in out and "Driftwood" in out, out)

    print("\n③ 名字在背包里却没搬动 → 给真因，别倒打一耙")
    # 这是 09-27 那天真正害人的那句：锅甩给背包，AI 越查越远
    M.api = FakeApi(drop_everything=True)
    out = M.storage_store.__wrapped__(items=["Pickaxe"])
    ck("**不再**说「背包里没有指定的？」（那是没查过就下的结论）",
       "背包里没有指定的" not in out, out)
    ck("…改说「名字对上了，但一件都没搬动」并给下一步",
       "名字对上了" in out and "storage view" in out, out)

    print("\n④ what + items 同传 → 合并，且明说")
    M.api = FakeApi()
    out = M.storage_store.__wrapped__(what="tool", items=["Pickaxe"])
    ck("`items` 不再被静默吞掉（Pickaxe 进了箱）", "Pickaxe" in out, out)
    ck("…并明说合并了（别让 AI 以为只有哪个生效）", "合并成一份名单" in out, out)
    ck("…`tool` 这个假名字照旧被点名（在不在背包）", "tool" in out, out)

    print("\n⑤ `name` → `what` 别名（AI 那天第一发就是这么写的）")
    sig = inspect.signature(M.storage_store.__wrapped__)
    ck("`name` 归一成 `what`（否则它会被丢掉、op 改做归位整理还报假成功）",
       M._normalize_kw_key("name", sig) == "what", M._normalize_kw_key("name", sig))
    call_kw, dropped = M._filter_kw(sig, {"name": "Pickaxe"})
    ck("…过滤后**不再**落进 dropped", call_kw.get("what") == "Pickaxe" and not dropped, str((call_kw, dropped)))

    # ═══════════════════════════════════════════════════════════════════
    # 🧭 2026-10-03「够得着」闸：C# 只动**玩家 4 格内**的箱子，够不着的在 `tooFar` 里点名
    #    ⇒ Python 这层的活是「**走过去再叫一次**」+ 如实说「还剩几口够不着」。
    #    ⚠️ 关键判据：**「够不着」≠「箱子里没有」** —— 报错那句会把 AI 指反方向。
    #    ⚠️ 老 DLL（回包**没有 `tooFar` 键**）⇒ 行为必须与改前**逐字相同**（一下都不多打）。
    # ═══════════════════════════════════════════════════════════════════
    print("\n⑥ 🧭 取物：够不着 ⇒ **走过去再叫一次**，别报成「箱子没有」")

    class FakeChestApi:
        """桩：`/chest_take_list` / `/store_all` 前 N 轮回 `tooFar`（= 够不着），之后才真给。

        `no_toofar=True` ⇒ 回包里**连 `tooFar` 这个键都没有** = **老 DLL 的形状**。
        """

        def __init__(self, take_rounds=0, store_rounds=0, no_toofar=False):
            self.take_rounds = take_rounds
            self.store_rounds = store_rounds
            self.no_toofar = no_toofar
            self.take_calls = []
            self.store_calls = []

        # ── 读 ──
        def state(self, **kw):
            return {"inventory": [{"name": "Diamond", "displayName": "钻石",
                                   "itemId": "(O)72", "catNum": -12, "stack": 2}],
                    "player": {"x": 11, "y": 13}}

        def _get(self, ep, params=None):
            if ep == "/scan_chests":
                return {"chests": [{"x": 58, "y": 14, "name": "矿石箱", "capacity": 36,
                                    "used": 1, "freeSlots": 35,
                                    "items": [{"name": "Diamond", "displayName": "钻石",
                                               "qualifiedId": "(O)72", "count": 2}]}]}
            return {}

        # ── 写 ──
        def _post(self, ep, data=None):
            if ep != "/chest_take_list":
                return {"ok": True}
            self.take_calls.append(dict(data or {}))
            _req = (data or {}).get("items") or []
            # ⚠️ 照 C# 的口径：`count<=0` = **不限量** ⇒ 回包里的 `wanted` 是 `-1`（不是 0）。
            _want = lambda it: (-1 if int(it.get("count", -1) or 0) <= 0 else int(it["count"]))
            _far = (not self.no_toofar) and len(self.take_calls) <= self.take_rounds
            _got = 0 if (self.no_toofar or _far) else 2
            r = {"ok": True, "location": "Farm", "reachTiles": 4,
                 "items": [{"item": it.get("name"), "wanted": _want(it), "taken": _got,
                            "from": ([] if _got == 0 else [{"name": "矿石箱", "x": 58, "y": 14,
                                                            "got": _got}])}
                           for it in _req]}
            if not self.no_toofar:
                r["tooFar"] = ([{"x": 58, "y": 14, "name": "矿石箱"}] if _far else [])
            return r

        def store_all(self, **kw):
            self.store_calls.append(dict(kw or {}))
            _far = (not self.no_toofar) and len(self.store_calls) <= self.store_rounds
            _ok = (not self.no_toofar) and not _far
            r = {"ok": True, "mode": "smart", "scope": "specified", "location": "Farm",
                 "totalFree": 60,
                 "stored": ([{"item": "Diamond", "count": 2, "to": {"x": 58, "y": 14}}] if _ok else []),
                 # 老 DLL 不会有 `out_of_reach` 这个新 reason ⇒ 连 leftovers 都不给（= 就是没搬动）
                 "leftovers": ([] if self.no_toofar
                               else ([{"item": "Diamond", "count": 2, "reason": "out_of_reach"}]
                                     if _far else []))}
            if not self.no_toofar:
                r["tooFar"] = ([{"x": 58, "y": 14, "name": "矿石箱"}] if _far else [])
            return r

    _WALKED = []
    _old_walk = M._walk_to_chest
    _old_aipos = getattr(M, "_ai_pos", None)
    M._walk_to_chest = lambda x, y: (_WALKED.append((x, y)), "  🚶 已走到箱子 (58,14) 旁边")[1]
    M._ai_pos = lambda: (11, 13)          # ⚠️ 别走 navigation 那份（它读的是**没打桩**的 api）
    try:
        # ① 第一轮够不着、第二轮走过去取到 ⇒ **两发 + 一次走位**，且**不许**说"箱子没有"。
        M.api = FakeChestApi(take_rounds=1)
        _WALKED[:] = []
        out = M.storage_take(items="Diamond")
        ck("🧭 够不着 ⇒ 挑最近的箱**走过去再叫一次**（`/chest_take_list` 真打了两发）",
           len(M.api.take_calls) == 2, str(len(M.api.take_calls)))
        ck("…而且**为重试走了一趟**（第一发前那次是「走到第一个配到的箱」，重试那次走的是 `tooFar` 点名的箱）",
           len(_WALKED) == 2 and _WALKED[-1] == (58, 14), str(_WALKED))
        ck("…第二发**只问没拿到的那件**（`Diamond`，数量 0 = 不限量）",
           M.api.take_calls[1].get("items") == [{"name": "Diamond", "count": 0}],
           str(M.api.take_calls[1]))
        ck("…两轮回包**合并**：最终报取到 x2（不是「第一轮 0 件」那个数）",
           "✅ Diamond x2" in out, out)
        ck("🚫 **绝不说「箱子没有」**（那是把「够不着」读成「没有」，会把 AI 指反方向）",
           "箱子没有" not in out and "够不着" not in out, out)

        # ② 够不着一直够不着（走位也没用）⇒ 打到 `_REACH_ROUNDS` 上限就停，**如实报"够不着"**。
        M.api = FakeChestApi(take_rounds=99)
        _WALKED[:] = []
        out = M.storage_take(items="Diamond")
        ck("🧭 一直够不着 ⇒ 最多 `_REACH_ROUNDS` 轮（1 发 + 3 轮重试 = 4 发，不无限重试）",
           len(M.api.take_calls) == 1 + M._REACH_ROUNDS, str(len(M.api.take_calls)))
        ck("…每轮都走过去（第一发前 1 次 + 重试 3 次）",
           len(_WALKED) == 1 + M._REACH_ROUNDS, str(_WALKED))
        ck("…回执**点名那口箱够不着** + 给下一步", "够不着" in out and "(58,14)" in out, out)
        ck("🚫 而且**把两件事实都摆出来**（「够得着的箱里没有」＋「还有 N 口够不着没翻」）——"
           "2026-10-03 真机拿不存在的名字试闸时逮到：原来写「是那几口箱够不着」= **在断言东西一定在**",
           "（箱子没有" not in out and "够得着的箱里没有" in out and "够不着" in out, out)

        # ③ **老 DLL**：回包连 `tooFar` 键都没有 ⇒ **一下都不多打**，行为与改前逐字相同。
        M.api = FakeChestApi(no_toofar=True)
        _WALKED[:] = []
        out = M.storage_take(items="Diamond")
        ck("🧭 老 DLL（**没有 `tooFar` 键**）⇒ **只打一发**（不许凭空多走一趟）",
           len(M.api.take_calls) == 1 and len(_WALKED) == 1, (len(M.api.take_calls), _WALKED))
        ck("…照旧说「箱子没有」（老口径逐字不变：读不到 ≠ 够不着）", "箱子没有" in out, out)

        # ④ 存物：`leftovers[].reason == "out_of_reach"` 才算"够不着" ⇒ 走过去再叫一次。
        M.api = FakeChestApi(store_rounds=1)
        _WALKED[:] = []
        out = M.storage_store.__wrapped__(items="Diamond")
        ck("🧭 存物够不着 ⇒ 走过去再叫一次（`/store_all` 两发）",
           len(M.api.store_calls) == 2, str(len(M.api.store_calls)))
        ck("…第二发**只重试没存下的那件**（`what=['Diamond']`）",
           M.api.store_calls[1].get("what") == ["Diamond"], str(M.api.store_calls[1]))
        ck("…两轮回包合并：最终报存进 (58,14)", "进 (58,14)" in out, out)
        ck("🚫 也不许报成「名字对上了，但一件都没搬动」那种误判",
           "一件都没搬动" not in out and "够不着" not in out, out)

        # ⑤ 老 DLL 的存物：没有 `tooFar` 键 ⇒ 只一发（`out_of_reach` 那条重试路也走不到）。
        M.api = FakeChestApi(no_toofar=True, store_rounds=99)
        _WALKED[:] = []
        out = M.storage_store.__wrapped__(items="Diamond")
        ck("🧭 老 DLL（没 `tooFar` 键）⇒ 存物也只打一发（重试的判据在 `tooFar` 上）",
           len(M.api.store_calls) == 1 and _WALKED == [], (len(M.api.store_calls), _WALKED))
        ck("…照旧报「一件都没搬动」（老口径逐字不变）", "一件都没搬动" in out, out)
    finally:
        M._walk_to_chest = _old_walk
        if _old_aipos is None:
            del M._ai_pos
        else:
            M._ai_pos = _old_aipos

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
