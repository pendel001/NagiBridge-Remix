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

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
