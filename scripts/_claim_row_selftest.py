# -*- coding: utf-8 -*-
"""🎁 单子上那行「领取」（满包接鱼/矿井宝箱/节日奖励）—— 纯 Python 自验（不起服务、不碰游戏）。2026-10-06

来历（恒当天真机 + 三句纠正）：
  · 「**满包接鱼那个东西我们得做上单子了，确实难操作**」⇒ 领取侧 `ItemGrabMenu` 也要出行
    （`Ctx.menu_claim` → `_CLAIM_V`「领取」那行；判据 = `/menu.items` = C# 的 `grabItems`）。
  · 「刚才石鱼是**粘在 helditem(光标)上面**了」⇒ 满包点 `action=claim` 的真相是**挂到光标上**，
    这时关菜单（ok/close）它**就掉地上**（我们那条就是这么掉出去的，水边还有**放生**风险）。
  · 「**下次再来照样能领**的说法，对于钓鱼/矿井宝箱/节日奖励都是不正确的，对于**吉尔**是对的
    ⇒ **不能回头才是常态**」⇒ 那句旧的放心话必须改成警告。

测四件：
  ① 有得领 + 包里有位 ⇒ 出「领 X×N」那一行；执行 = `action=claim` + **回读**（包里真多了才算成）
  ② 有得领 + **包满** ⇒ 照样出行（标题点明"得先丢一样腾格"），但**执行侧当场拒、一个 POST 都不发**
  ③ 点完发现东西**挂在光标上** ⇒ 如实说"挂光标上了，别点 ok"，⛔ 不许说"已领取"
  ④ 空的领取侧 ⇒ **不出这一行**（原来只有"关掉界面"，保持原样）
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # import 安全：末尾才 if __name__ == "__main__"

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _inv(n_items=0, cap=36):
    """背包夹具：`n_items` 个占格（其余空）——物品名用 `Item{i}`。"""
    inv = [{"name": "Item%d" % i, "itemId": "(O)%d" % (100 + i), "stack": 1}
           for i in range(n_items)]
    inv += [None] * (cap - len(inv))
    return inv


class FakeApi:
    """只回答这次要问的：`/state`（背包/菜单）、`/menu`（领取侧）、`/menu/click`（领/丢）。"""

    def __init__(self, used=10, menu="ItemGrabMenu", items=None, menu_after=None,
                 held_after=None, last=None, click_ok=True, discard_ok=True):
        self.used = used
        self.menu = menu
        self.items = items if items is not None else [{"name": "石鱼", "count": 1, "index": 0}]
        self.menu_after = menu_after          # 点完之后 `/state.activeMenu` 的 type（None=原样）
        self.held_after = held_after          # 点完之后菜单里的 heldItem
        self.last = last                      # 点完之后背包占几格（None=原样）
        self.click_ok = click_ok
        self.discard_ok = discard_ok
        self.posts, self.gets = [], []

    # ── 服务器要的那几个口 ──
    def state(self, light=False):
        return {"location": {"name": "UndergroundMine20"},
                "player": {"x": 26, "y": 13, "maxItems": 36},
                "inventory": _inv(self.used),
                "activeMenu": {"type": self.menu,
                               "heldItem": self.held_after}}
    def _ai_get(self, ep, params=None):
        self.gets.append(ep)
        if ep == "/menu":
            return {"type": "ItemGrabMenu", "items": list(self.items)}
        raise RuntimeError("unexpected GET " + ep)

    def _ai_post(self, ep, data=None):
        self.posts.append((ep, dict(data or {})))
        if ep == "/menu/click":
            _d = dict(data or {})
            # 🗑️ 腾格那条路（C# `itemgrab_discard`：拿起 → 点菜单垃圾桶）
            if _d.get("action") == "discard":
                if self.discard_ok:
                    return {"ok": True, "clicked": "itemgrab_discard",
                            "item": _d.get("item"), "slot": 3}
                return {"ok": False, "error": "领取菜单没垃圾桶，未丢弃「%s」" % _d.get("item")}
            if self.last is not None:
                self.used = self.last
            if self.menu_after is not None:
                self.menu = self.menu_after
            return {"ok": self.click_ok, "clicked": "claim", "item": "石鱼",
                    "error": None if self.click_ok else "stub fail"}
        raise RuntimeError("unexpected POST " + ep)


class _BagApi:
    """只管背包（给 `_menu_claim_subs` 用）—— 换件候选的判据全在背包上。"""

    def __init__(self, items):
        self.items = items

    def state(self, light=False):
        return {"player": {"maxItems": 36}, "inventory": list(self.items)}


_real_api = M.api
try:
    print("\n① 有得领 + 包里有位 ⇒ 出行；执行 = claim **带槽号/名字** + 回读")
    a = FakeApi(used=30, last=31)              # 点完背包占格 30→31（=真多了一件）
    M.api = a
    _lab = M._menu_claim_label({"type": "ItemGrabMenu"})
    ck("有得领 ⇒ 标题 = 「领 石鱼×1」", _lab == "领 石鱼×1", _lab)
    _out = M._menu_claim_now()
    _p = a.posts[-1][1] if a.posts else {}
    ck("…执行发了 `action=claim`", _p.get("action") == "claim", str(a.posts))
    # 🔴 2026-10-06 真机抓到的我自己的 bug：只发 action=claim ⇒ C# 当场拒
    #    「领取需指定 item=名称 或 slot=序号 或 quantity>1」（`ModEntry.cs:15881`）⇒ **必须带一个**。
    ck("…并且**带了 `slot=`/`item=`/`quantity>1`**（不然 C# 直接拒）",
       (isinstance(_p.get("slot"), int) or _p.get("item") or int(_p.get("quantity") or 0) > 1), str(_p))
    ck("…回读包里多了 ⇒ 说“领到了”，⛔ 不说“可能没接住”", "领到了" in _out and "没接住" not in _out, _out)

    print("\n② 有得领 + **包满** ⇒ 照样出行（标题问「和什么替换？」），但**执行侧当场拒、不发 POST**")
    a = FakeApi(used=36)
    M.api = a
    _lab = M._menu_claim_label({"type": "ItemGrabMenu"})
    ck("满包照样出行（不然 AI 只看到“关掉界面”）", _lab.startswith("领 石鱼×1"), _lab)
    ck("…标题按恒的形问「**和什么替换？**」", "和什么替换" in _lab and "包满" in _lab, _lab)
    _out = M._menu_claim_now()
    ck("…执行侧**当场拒**（一个 POST 都不发）", a.posts == [], str(a.posts))
    ck("…并指两条正路（腾格 / 和什么替换）", "腾格" in _out and "和什么替换" in _out, _out)
    # 🚫 恒 2026-10-06：「**你不要丢地上，丢地上容易被吸附回来。扔垃圾桶里最好。**」
    ck("…⛔ 绝不再教 `scene drop`（丢地上）当解法", "别用 `scene drop`" in _out, _out)

    print("\n③ 点完东西**挂在光标上** ⇒ 如实说，⛔ 不许说“已领取”")
    a = FakeApi(used=30, held_after={"name": "Stonefish", "displayName": "石鱼"})
    M.api = a
    _out = M._menu_claim_now()
    ck("…明说「挂在光标上了」", "光标" in _out, _out)
    ck("…并警告**别点 ok**（点了就掉地上）", "别点 ok" in _out, _out)
    ck("…⛔ 没有「已领取」这种话", "已领取" not in _out, _out)

    print("\n④ 空的领取侧 ⇒ 不出这一行；点失败 ⇒ 如实报错")
    a = FakeApi(used=10, items=[])
    M.api = a
    ck("空领取侧 ⇒ 标题是空串（那屏只剩“关掉界面”，原样）",
       M._menu_claim_label({"type": "ItemGrabMenu"}) == "", M._menu_claim_label({"type": "ItemGrabMenu"}))
    a = FakeApi(used=30, click_ok=False)
    M.api = a
    _out = M._menu_claim_now()
    ck("点失败 ⇒ 报“没领成”，⛔ 不报成功", "没领成" in _out and "领到了" not in _out, _out)
    ck("…精通碑那条路没被弄坏（别的菜单仍走 `mainButton`）",
       "masterytrackermenu" not in str(M._menu_claim_label({"type": "MasteryTrackerMenu", "mastery": {}})),
       str(M._menu_claim_label({"type": "MasteryTrackerMenu", "mastery": {}})))

    print("\n⑤ 换件候选：**不分档不排序**（恒：不用（垃圾）（普通）那种判断，AI 自己取舍）")
    M.api = _BagApi([{"name": "Iridium Pickaxe", "itemId": "(T)IronPickaxe", "stack": 1},
                     {"name": "Furnace", "itemId": "(BC)13", "stack": 1},
                     {"name": "Oak Chair", "itemId": "(F)6", "stack": 1},
                     {"name": "Broken CD", "itemId": "(O)171", "stack": 2},
                     {"name": "Stone", "itemId": "(O)390", "stack": 999},
                     {"name": "Ghostfish", "itemId": "(O)156", "stack": 1},
                     {"name": "Trash", "itemId": "(O)168", "stack": 1}])
    _subs = M._menu_claim_subs()
    _names = [s["name"] for s in _subs]
    ck("⛔ 工具（`(T)`）**一条都不列**", "Iridium Pickaxe" not in _names, str(_names))
    ck("⛔ 机器（`(BC)`）/家具（`(F)`）也不列（第一版真机就摆出了熔炉）",
       "Furnace" not in _names and "Oak Chair" not in _names, str(_names))
    # 🔴 恒 2026-10-06：「不用上那种（垃圾）（普通）等的判断，ai自己按需取舍」
    ck("🔴 **不带 `why` 分档**（不再评「垃圾/普通/可能值钱」）",
       all("why" not in s for s in _subs), str(_subs))
    ck("…**顺序 = 背包原顺序**（不按价值重排：Broken CD→Stone→Ghostfish→Trash）",
       _names == ["Broken CD", "Stone", "Ghostfish", "Trash"], str(_names))
    ck("…条目里该有的都有（slot/name/id/stack）",
       all({"slot", "name", "id", "stack"} <= set(s) for s in _subs), str(_subs))

    print("\n⑥ 腾格 = **扔垃圾桶**（恒：「别丢地上」）；没扔掉 ⇒ **绝不接着领**")
    a = FakeApi(used=30, discard_ok=True)
    M.api = a
    _out = M._menu_discard_now("Broken CD")
    _p = a.posts[-1][1] if a.posts else {}
    ck("…发的是 `action=discard` + `item=`（C# 那条拿起→点垃圾桶的路）",
       _p.get("action") == "discard" and _p.get("item") == "Broken CD", str(a.posts))
    ck("…回执说“扔进垃圾桶”", "垃圾桶" in _out, _out)
    a = FakeApi(used=30, discard_ok=False)
    M.api = a
    _out = M._menu_discard_now("Broken CD")
    ck("…没桶/失败 ⇒ 如实说“没扔掉”", "没扔掉" in _out, _out)
    ck("…⛔ 并警告别改用 `scene drop`", "scene drop" in _out, _out)
    a = FakeApi(used=30, discard_ok=False)
    M.api = a
    _before = len(a.posts)
    _out = M._menu_claim_replace("Broken CD")
    ck("🔴 **没扔掉就绝不领**（只发了 discard 一发，没有 claim）",
       all(p[1].get("action") == "discard" for p in a.posts) and len(a.posts) == _before + 1, str(a.posts))
    a = FakeApi(used=30, discard_ok=True, last=31)
    M.api = a
    _out = M._menu_claim_replace("Broken CD")
    _acts = [p[1].get("action") for p in a.posts]
    ck("…扔掉成功 ⇒ 自动接着领（discard → claim 两发，顺序对）",
       _acts == ["discard", "claim"], str(_acts))
    ck("…并把两件事都说清楚（扔桶 + 领到）", "垃圾桶" in _out and "领到了" in _out, _out)
finally:
    M.api = _real_api

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
