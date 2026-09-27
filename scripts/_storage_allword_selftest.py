"""🗣️ 「全存」保留字当物品名 + 参数/op 名写错的推荐 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒真机：AI 想"把背包存完"，调的是
    storage(ops="store", kw={"items": "all"})          ← session_log 1790258224
回包是「⚠️ 没匹配到可存的物品（背包里没有指定的？storage view 看看背包）」。
AI 从这句里**只能学到"背包里没有叫 all 的东西"**，永远学不到「**all 是个参数**」——
而它下一句就抱怨"想存完但是 all 传参不认"。恒当日拍的判据：
**「在 AI 输入不认的参数时给它推荐一点」**。

测四组：
  ① 保留字（all / 全部）当物品名 → **当场拦住 + 给确切命令**，且**一件都没动**
  ② 真传了 all=True → 不拦，照常全存（别把正确用法也误伤）
  ③ all=True 与 items="all" 同时出现 → 把保留字从名单里剔掉（留着会被 C# WhatMatches 卡死）
  ④ 参数名 / op 名写错 → 「你是不是想写 X」，且**不像就不硬凑**
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


BAG = [{"displayName": "木材", "name": "Wood", "itemId": "(O)388"},
       {"displayName": "石头", "name": "Stone", "itemId": "(O)390"}]


class FakeApi:
    """只回答本测试要问的；store_all 把"怎么被调的"记下来当尺子。"""
    def __init__(self):
        self.calls = []

    def state(self, **kw):
        return {"inventory": BAG, "player": {}}

    def store_all(self, **kw):
        self.calls.append(kw)
        what = kw.get("what") or []
        scope = "all" if kw.get("clear_all") else ("specified" if what else "tidy")
        return {"ok": True, "mode": "smart", "scope": scope, "location": "Town",
                "stored": [{"item": n, "count": 1, "to": {"x": 8, "y": 93}} for n in what],
                "leftovers": [], "totalFree": 30}


_real = {n: getattr(M, n) for n in ("api", "_walk_to_chest", "_storage_default_for_loc",
                                    "_primary_chest_for_smart")}
try:
    M._walk_to_chest = lambda x, y: None
    M._storage_default_for_loc = lambda: None
    M._primary_chest_for_smart = lambda: None

    def fresh():
        M.api = FakeApi()
        return M.api

    print("\n① 保留字当物品名 → 拦住 + 给命令 + 一件都没动")
    a = fresh()
    out = M.storage_store.__wrapped__(items="all")
    ck("真机原样重放 items=\"all\" → **当场拦住**（不再说「没匹配到可存的物品」）",
       "不是物品名" in out and "没匹配到" not in out, out)
    ck("…并给出**能直接抄的那行命令**", '{"all": True}' in out and "storage(ops=" in out, out)
    ck("…且**一件都没动**（api 压根没被调）", a.calls == [], str(a.calls))

    a = fresh()
    out = M.storage_store.__wrapped__(items="全部")
    ck("中文「全部」同样拦（AI 两个语种都会猜）", "不是物品名" in out and a.calls == [], out)

    a = fresh()
    out = M.storage_store.__wrapped__(items="全部,Wood")
    ck("混着真名字（全部,Wood）→ **照样拦**（不替它猜一半）",
       "不是物品名" in out and a.calls == [], out)

    print("\n② 真传了 all=True → 不拦（别把正确用法误伤）")
    a = fresh()
    out = M.storage_store.__wrapped__(all=True)
    ck("all=True → 照常全存，回包「全存腾空间」",
       "全存腾空间" in out and "不是物品名" not in out, out)
    ck("…且 clear_all=True 真传到 C#", a.calls and a.calls[0].get("clear_all") is True, str(a.calls))

    print("\n③ all=True 与 items=\"all\" 同时出现 → 保留字不许留在名单里")
    a = fresh()
    out = M.storage_store.__wrapped__(items="all", all=True)
    ck("不拦（all=True 已经说清意图）", "不是物品名" not in out, out)
    ck("…what 里**没有** all（留着会被 C# WhatMatches 逐个精确比对卡死 → 一件都存不下）",
       a.calls and "all" not in (a.calls[0].get("what") or []), str(a.calls))
    ck("…clear_all=True 仍在", a.calls and a.calls[0].get("clear_all") is True, str(a.calls))

    print("\n④ 正常用法不受影响")
    a = fresh()
    out = M.storage_store.__wrapped__(items="Wood×50")
    ck("普通点名 Wood×50 → 照旧「只存指定」",
       "只存指定" in out and "不是物品名" not in out, out)
    ck("…名字与数量都照旧传下去",
       a.calls and a.calls[0].get("what") == ["Wood"] and a.calls[0].get("counts") == {"Wood": 50},
       str(a.calls))

    print("\n⑤ 参数名写错 → 「你是不是想写 X」")
    a = fresh()
    out = M.storage.__wrapped__(ops="store", kw={"itemss": "Wood"})
    ck("itemss → 点名 + 推荐 items", "你是不是想写" in out and "items" in out, out)
    ck("…但**照旧执行**（写错的键只是被忽略，没让整次调用陪葬）", len(a.calls) == 1, str(a.calls))

    a = fresh()
    out = M.storage.__wrapped__(ops="store", kw={"banana": "Wood"})
    ck("毫不相干的键（banana）→ **不硬凑推荐**，只列可用参数",
       "你是不是想写" not in out and "可用参数" in out, out)

    print("\n⑥ op 名写错 → 「你是不是想写 X」")
    a = fresh()
    out = M.storage.__wrapped__(ops="stor", kw={})
    ck("stor → 推荐 store", "未知操作" in out and "你是不是想写「store」" in out, out)

    a = fresh()
    out = M.storage.__wrapped__(ops="zzzz", kw={})
    ck("zzzz → 不硬凑（宁可只说可用 ops）",
       "未知操作" in out and "你是不是想写" not in out, out)

    print("\n⑦ _typo_suggest 的尺子本身")
    ck("item → item_name（近，推荐）", M._typo_suggest("item", ["item_name", "name"]) == "item_name")
    ck("x → tile_x **不推荐**（只共一个字母，硬凑会把人带沟里）",
       M._typo_suggest("x", ["tile_x", "tile_y", "name"]) == "")
    ck("大小写不敏感仍能找回原名", M._typo_suggest("POI_NAME", ["poi_name"]) == "poi_name")

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
