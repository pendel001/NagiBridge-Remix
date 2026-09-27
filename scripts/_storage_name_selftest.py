"""🧺 `storage store` 的**名字点名** —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒真机：AI 传 `items=Clam,Herring,Coral,Anchovy,Sardine,Sardine`（六件），
C# 只搬走四件，剩两个名字（Clam/Anchovy）**背包里根本没有**，而回包写的是
「**✅ 全部存下，没剩**」—— 它以为自己存干净了，其实还有两件躺在背包里。

而且名字是真用错了：**蚌的英文内部名是 Mussel，不是 Clam**（Clam=蛤蜊，是另一样东西），
AI 没有地方能查到"蚌"的英文名（`check backpack` 只给中文显示名）⇒ 只能照英文习惯猜。

测两件：
  ① 请求里有名字背包里没有 → **不许报「全部存下」**，必须点名是哪一个
  ② 口径与 C# 一致：中文显示名 / 英文内部名 / 物品 ID 都算数；**读不到背包就闭嘴**（不误报）
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


# 真机那一刻的背包（照 session_log 抄的：蚌=Mussel、锚=Anchor 在里面，Clam/Anchovy 不在）
BAG = [
    {"displayName": "斧头", "name": "Axe", "itemId": "(T)Axe"},
    {"displayName": "蚌", "name": "Mussel", "itemId": "(O)372"},   # ⚠️ 不是 Clam
    {"displayName": "珊瑚", "name": "Coral", "itemId": "(O)393"},
    {"displayName": "锚", "name": "Anchor", "itemId": "(O)334"},
    {"displayName": "[金]鲱鱼", "name": "Herring", "itemId": "(O)Herring"},
]


class FakeApi:
    def __init__(self, inv=None, boom=False):
        self._inv = inv
        self._boom = boom
        self.calls = []

    def state(self, **kw):
        if self._boom:
            raise RuntimeError("game not reachable")
        return {"inventory": self._inv, "player": {}}

    def store_all(self, **kw):
        self.calls.append(kw)
        # 假装 C# 按 WhatMatches 搬走能对上的那些（这正是真机行为）
        req = kw.get("what") or []
        idx = set()
        now = _bag_index()
        for n in req:
            if n.lower() in now:
                idx.add(n)
        return {"ok": True, "mode": "smart", "scope": "specified", "location": "Beach",
                "stored": [{"item": n, "count": 1, "to": {"x": 53, "y": 24}} for n in idx],
                "leftovers": [], "totalFree": 30}


def _bag_index():
    s = set()
    for i in BAG:
        for k in ("displayName", "name", "itemId"):
            v = str(i.get(k) or "").strip().lower()
            if v:
                s.add(v)
    return s


_real = {n: getattr(M, n) for n in ("api", "_walk_to_chest", "_storage_default_for_loc",
                                    "_primary_chest_for_smart")}
try:
    M._walk_to_chest = lambda x, y: None
    M._storage_default_for_loc = lambda: None
    M._primary_chest_for_smart = lambda: None

    print("\n① 名字没对上 → 不许报「全部存下」")
    M.api = FakeApi(BAG)
    out = M.storage_store.__wrapped__(items="Clam,Herring,Coral,Anchovy,Sardine,Sardine")
    ck("真机原命令：Clam/Anchovy 没对上 → **不再说「全部存下，没剩」**",
       "全部存下" not in out, out)
    ck("…并**点名**是哪几个（Clam、Anchovy）",
       "Clam" in out and "Anchovy" in out and "背包里现在没有" in out, out)
    ck("…顺带点破英文名要照抄、别猜（蚌=Mussel 不是 Clam）",
       "Mussel" in out and "check backpack" in out, out)
    ck("对上的那些照旧报（Herring/Coral 真搬走了）", "Herring" in out, out)

    print("\n② 口径与 C# 一致 + 读不到就不判")
    M.api = FakeApi(BAG)
    out = M.storage_store.__wrapped__(items="蚌,锚")
    ck("**中文显示名**（蚌/锚）→ 全对上，照旧「全部存下」",
       "全部存下" in out and "背包里现在没有" not in out, out)

    M.api = FakeApi(BAG)
    out = M.storage_store.__wrapped__(items="Mussel,Anchor")
    ck("**英文内部名**（Mussel/Anchor）→ 同样全对上（C# 本来就认，别把它说成 bug）",
       "全部存下" in out, out)

    M.api = FakeApi(BAG)
    out = M.storage_store.__wrapped__(items="(O)372")
    ck("**物品 ID**（(O)372）→ 也算数（要的 372 是蛤蜊，背包里 372 是蚌…名字照抄就行）",
       "全部存下" in out, out)

    M.api = FakeApi(boom=True)
    out = M.storage_store.__wrapped__(items="Clam")
    ck("**读不到背包 → 不点名**（不拿「我读不到」当「它没有」）",
       "背包里现在没有" not in out, out)

    print("\n③ 场景里没有箱子 → 照 C# 的 note 说，别替背包下结论")
    # ⚠️ 2026-09-25 恒真机：Forest 没箱子，`storage store all=True` 回的是
    #    「✅ 没有要存的（背包没有非工具物品）」+「📦 剩余总格: 0」——
    #    两句都是编的：C# 明明给了 `note:"当前场景没有箱子"`，Python **从来没读过**。
    def _no_chest(**kw):
        return {"ok": True, "mode": "smart", "scope": "all", "location": "Forest",
                "stored": [], "leftovers": [], "chests": [], "totalFree": 0,
                "note": "当前场景没有箱子"}

    M.api = FakeApi(BAG)
    M.api.store_all = _no_chest
    out = M.storage_store.__wrapped__(all=True, keepTools=True)
    ck("**原话照报**（当前场景没有箱子）", "当前场景没有箱子" in out, out)
    ck("…不再替背包下结论（这句是没查过就说的）", "背包没有非工具物品" not in out, out)
    ck("…也不报 `剩余总格: 0`（读着像「箱子满了」，其实是没箱子）", "剩余总格" not in out, out)
    ck("…给下一步（走到有箱子的场景）", "有箱子的场景" in out, out)

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
