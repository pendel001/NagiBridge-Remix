# -*- coding: utf-8 -*-
"""🎟 马龙的「失物招领」的钉子 —— **不吃游戏**（假的 `_ai_get`/`_ai_post`）。

恒 2026-10-06：「那个菜单有点类似商店」—— 对，**它就是一家 `ShopMenu`**
（反编译 `GameLocation.cs:12289-12291`：对话选项 `adventureGuild_Recovery` →
`Utility.TryOpenShopMenu("AdventureGuildRecovery", "Marlon")`；`Utility.cs:4216` 建的就是
`new ShopMenu(shopId, …)`），货架来自 `Data/Shops` 的 item 查询 `ITEMS_LOST_ON_DEATH`
（`ItemQueryResolver.cs:236-254` = `Game1.player.itemsLostLastDeath`）。

⇒ C# 那份商店分支**本来就报得出来**（不用新写"读"）。这一批补的是**分辨它**：
  · C# 多报一位 `shopId`（`ShopMenu.ShopId`，public 字段 `ShopMenu.cs:188`）；
  · 服务器拿它算 `ctx.shop["recovery"]`（判据**只有这一处**）；
  · 单子给一行「取回失物」——标题/理由把**代价**说清（花钱取回一件），
    而商店那行「买…」在那家店上**主动关掉**（同一件事两行两种措辞 = 噪音）。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # noqa: E402
import stardew_api as api            # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


STATE = {
    "player": {"x": 10, "y": 14, "stamina": 250, "maxItems": 36, "money": 5000,
               "currentItem": None, "currentItemId": None},
    "location": {"name": "AdventureGuild", "uniqueName": "AdventureGuild"},
    "inventory": [],
    "activeMenu": {"type": "ShopMenu"},
}
# 货架照 `/menu` 的真形状（`shopItems[]` + `sellableHere` + `shopPage`）——
# 马龙那家店收不到玩家任何东西（`sellableHere` 是空列表）。
LOST = [
    {"name": "Iridium Pickaxe", "displayName": "铱十字镐", "id": "(T)IridiumPickaxe",
     "price": 7000, "stock": 1, "visible": True, "bounds": {"x": 300, "y": 300}},
    {"name": "Spicy Eel", "displayName": "香辣鳗鱼", "id": "(O)226",
     "price": 90, "stock": 2, "visible": True, "bounds": {"x": 420, "y": 300}},
]
MENU_RECOVERY = {
    "ok": True, "open": True, "type": "ShopMenu", "shopId": "AdventureGuildRecovery",
    "shopItems": [dict(x) for x in LOST], "sellableHere": [],
    "shopPage": {"index": 0, "pageSize": 4, "total": 2}, "buttons": [],
}
MENU_PIERRE = {
    "ok": True, "open": True, "type": "ShopMenu", "shopId": "SeedShop",
    "shopItems": [
        {"name": "Parsnip Seeds", "displayName": "防风草种子", "id": "(O)472",
         "price": 20, "stock": -1, "visible": True, "bounds": {"x": 300, "y": 300}},
        {"name": "Strawberry Seeds", "displayName": "草莓种子", "id": "(O)745",
         "price": 100, "stock": 5, "visible": True, "bounds": {"x": 420, "y": 300}},
    ],
    "sellableHere": ["防风草"], "shopPage": {"index": 0, "pageSize": 4, "total": 2},
    "buttons": [],
}
CALLS = []
TAKE_RESULT = {"ok": True, "clicked": "shop_item", "item": "(O)226", "quantity": 1}


def _stub(menu=None, menu_raises=False, take_result=None):
    CALLS.clear()
    st = dict(STATE)
    if menu is None:
        st["activeMenu"] = None
    else:
        st = dict(st, activeMenu={"type": menu})

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/state":
            return st
        if ep == "/menu":
            if menu_raises:
                raise RuntimeError("模拟：商店开着但 /menu 读不出来")
            return MENU_RECOVERY if menu == "ShopMenu" else {}
        if ep == "/status":
            return {"ok": True, "build": "2026-10-06 00:00:00 @test"}
        return {}

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        if ep == "/menu/click":
            return dict(take_result if take_result is not None else TAKE_RESULT)
        return {"ok": True}

    api._ai_get, api._ai_post = g, p
    api._get, api._post = g, p
    M._with_state = lambda x, *a, **k: x
    M._ensure_background = lambda *a, **k: None
    M._peer_econ_mute = lambda *a, **k: None
    M.intent_menu.reset_menu()


def _rows():
    return [r.verb.key for r in M.intent_menu._LAST_ROWS]


_real = {n: getattr(M, n) for n in ("api", "_with_state", "_ensure_background", "_peer_econ_mute")}
try:
    # ① 普通商店：别把皮埃尔也当成失物招领
    print("\n① 普通商店（`shopId=SeedShop`）⇒ `recovery` 是假，商店那两行照旧")
    _stub(menu="ShopMenu")
    MENU_RECOVERY["shopId"] = "SeedShop"
    MENU_RECOVERY["shopItems"] = [dict(x) for x in MENU_PIERRE["shopItems"]]
    MENU_RECOVERY["sellableHere"] = ["防风草"]
    try:
        ctx = M._im_ctx()
        ck("`recovery` 是假", (ctx.shop or {}).get("recovery") is False, str(ctx.shop))
        ck("…`id` 就是游戏报的那个", (ctx.shop or {}).get("id") == "SeedShop", str(ctx.shop))
        M.intent(ops="show", kw={"n": 40})
        ck("…「买…」那行**还在**", "buy" in _rows(), str(_rows()))
        ck("…「取回失物」**不许出现**", "recover" not in _rows(), str(_rows()))
    finally:
        MENU_RECOVERY["shopId"] = "AdventureGuildRecovery"
        MENU_RECOVERY["shopItems"] = [dict(x) for x in LOST]
        MENU_RECOVERY["sellableHere"] = []

    # ② 失物招领：不给「买…」，改给「取回失物」
    print("\n② 马龙的失物招领 ⇒ 不给「买…」、改给「取回失物…」")
    _stub(menu="ShopMenu")
    ctx = M._im_ctx()
    ck("`recovery` 是真", (ctx.shop or {}).get("recovery") is True, str(ctx.shop))
    out = M.intent(ops="show", kw={"n": 40})
    ck("「买…」那行**关掉了**（同一件事别两行两种措辞）", "buy" not in _rows(), str(_rows()))
    ck("「取回失物…」在", "recover" in _rows(), str(_rows()))
    ck("理由栏把**代价**说清（花钱取回）", "花钱取回" in out, out)

    # ③ 点开 ⇒ 丢件一件一行 + 单价
    print("\n③ 点开 ⇒ 一件一行（名字 · 单价）")
    no = next(r.no for r in M.intent_menu._LAST_ROWS if r.verb.key == "recover")
    shelf = M.intent(ops="do", kw={"code": str(no)})
    ck("两件都在", "铱十字镐" in shelf and "香辣鳗鱼" in shelf, shelf)
    ck("…带**取回价**", "7000g" in shelf and "90g" in shelf, shelf)

    # ④ 敲 1 ⇒ 真打到 `/menu/click`（按 id，一次一件）
    print("\n④ 敲 1 ⇒ 真打到 `/menu/click`（`item` = id，`quantity` = 1）")
    CALLS.clear()
    r = M.intent(ops="do", kw={"code": "1"})
    hits = [c[2] for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"]
    ck("打的是 `/menu/click`", bool(hits), str(CALLS))
    ck("…传的是**游戏那个 id**", hits and hits[0].get("item") == "(T)IridiumPickaxe", str(hits))
    ck("…**一次一件**（quantity=1）", hits and int(hits[0].get("quantity") or 0) == 1, str(hits))
    ck("回执说清哪件取回了 + 花了多少", "铱十字镐" in r and "7000g" in r, r)

    # ⑤ 货架空（没丢过东西）⇒ 不给那一行
    print("\n⑤ 货架空 ⇒ 不给「取回失物」（没丢过东西）")
    _stub(menu="ShopMenu")
    _saved_items = MENU_RECOVERY["shopItems"]
    MENU_RECOVERY["shopItems"] = []
    try:
        ctx = M._im_ctx()
        ck("`ctx.shop` 还是真字典（「这家店在」），只是货架空",
           isinstance(ctx.shop, dict) and ctx.shop.get("recovery") is True, str(ctx.shop))
        M.intent(ops="show", kw={"n": 40})
        ck("…「取回失物」不出现", "recover" not in _rows(), str(_rows()))
    finally:
        MENU_RECOVERY["shopItems"] = _saved_items

    # ⑥ 没开商店 ⇒ 两行都不给
    print("\n⑥ 没开商店 ⇒ 两行都不给、**零额外 HTTP**")
    _stub(menu=None)
    ctx = M._im_ctx()
    ck("`ctx.shop is None`", ctx.shop is None, str(ctx.shop))
    ck("…一次 `/menu` 都不打", not any(c[1] == "/menu" for c in CALLS), str(CALLS))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有这两行", "recover" not in _rows() and "buy" not in _rows(), str(_rows()))

    # ⑦ 取不成 ⇒ 回执**不装成功**
    print("\n⑦ 端点回 `ok:false`（钱不够/背包放不下）⇒ 回执如实报")
    _stub(menu="ShopMenu",
          take_result={"ok": False,
                       "error": "商店里没买成「(T)IridiumPickaxe」（一件都没成交，**钱没动**）"})
    M.intent(ops="show", kw={"n": 40})
    M.intent(ops="do", kw={"code": str(next(r.no for r in M.intent_menu._LAST_ROWS
                                            if r.verb.key == "recover"))})
    bad = M.intent(ops="do", kw={"code": "1"})
    ck("回执写**没取回**并带游戏原话", "没取回" in bad and "钱没动" in bad, bad)
    ck("…**不出现**「取回了」那种像成功的话", "取回了" not in bad, bad)

    # ⑧ 读不出来 ⇒ 三档里的"不知道"（不给行，但**不许**说成"这店不收东西"）
    print("\n⑧ `/menu` 读不出来 ⇒ 不给行（`{}` = 不知道，不是「没有」）")
    _stub(menu="ShopMenu", menu_raises=True)
    ctx = M._im_ctx()
    ck("`ctx.shop == {}`（开着但读不出来）", ctx.shop == {}, str(ctx.shop))
    M.intent(ops="show", kw={"n": 40})
    ck("…「取回失物」不出现", "recover" not in _rows(), str(_rows()))

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
