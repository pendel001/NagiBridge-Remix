# -*- coding: utf-8 -*-
"""🎯 意图选项单**接线**自验 —— **不吃游戏**。

验的是"服务器那一段"：`intent` 工具 → 拉世界快照 → 交给 `intent_menu` → 敲号 → 执行器。
（`intent_menu.py` 自己的机制由它自己的 `__main__` 自验管，这里只管**接线**接对没有。）

⚠️ 手法：把 `stardew_api` 的 `_ai_get/_ai_post` 换成桩（**一个字节都不碰游戏、不起服务**），
   回包形状**照抄 C# 端点的真实字段**（`/scan_chests` 的 items/capacity/freeSlots、
   `/sittable` 的 seats/me、`/animals` 的 wasPetToday …）——形状抄错就等于没测。
⚠️ `_with_state` 打桩成恒等：它后面拖着整条状态机（心跳/晨报/雕像…），
   跟"接线接对没有"无关，带着它跑只会让这个自验变脆。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # noqa: E402  （import 安全：不起线程、不连游戏）
import stardew_api as api            # noqa: E402

CALLS = []

# ── 桩数据（字段名照抄端点真回包）────────────────────────────────────
STATE = {
    "player": {"x": 12, "y": 12, "stamina": 268, "maxItems": 36, "money": 1234,
               "currentItem": "Book", "currentItemId": "(O)Book", "currentTool": None},
    "location": {"name": "FarmHouse"},
    "inventory": [
        {"slotIndex": 2, "name": "Book", "displayName": "古书", "itemId": "(O)Book",
         "catNum": -102, "stack": 1, "quality": 0, "sellable": False, "shippable": False},
        {"slotIndex": 3, "name": "Strawberry", "displayName": "草莓", "itemId": "(O)400",
         "catNum": -79, "stack": 5, "quality": 0, "edibleValue": 20, "healthRecovered": 0,
         "sellable": True, "shippable": True},
        {"slotIndex": 4, "name": "Hoe", "displayName": "锄头", "itemId": "(T)Hoe",
         "catNum": -99, "stack": 1, "quality": 0, "sellable": False, "shippable": False},
    ],
    "activeMenu": None,
}
SURR = {
    "tiles": [{"x": 13, "y": 12, "passable": True, "forage": True, "object": "野莓"}],
    "npcs": [{"name": "喵喵", "kind": "pet", "x": 15, "y": 12}],
}
CHESTS = [
    {"x": 13, "y": 13, "name": "矿石箱", "capacity": 36, "used": 1, "freeSlots": 35,
     # 🆕 2026-09-30(178)：箱子层现在带身份（`typeId`/`typeName`）+ 色/标签 —— 形照真机回包
     "typeId": "(BC)130", "typeName": "宝箱", "autoTag": "矿", "color": "#303030",
     "items": [{"name": "Diamond", "displayName": "钻石", "count": 2,
                "qualifiedId": "(O)72", "slot": 0, "quality": 0}]},
    # ⚠️ **必须两个箱子**：只有一个时 `_chest_overview` 会走"单箱直通动作面"那条捷径
    #    （再套一层"一览"是白点一下）⇒ 那条路测不到"先一览再进动作面"这个主形状。
    {"x": 11, "y": 13, "name": "木材箱", "capacity": 36, "used": 30, "freeSlots": 6,
     "typeId": "(BC)130", "typeName": "宝箱", "autoTag": "", "color": "",
     "items": [{"name": "Wood", "displayName": "木材", "count": 300,
                "qualifiedId": "(O)388", "slot": 0, "quality": 0}]},
    # 🆕 **没人工名的小冰箱**：专门验"没人起名时印容器类型"那条 —— 真机上三台小冰箱
    #    原来只能印 `⬜ (18,23)`，分不出"这是台小冰箱"。
    {"x": 12, "y": 14, "name": "", "capacity": 36, "used": 0, "freeSlots": 36,
     "typeId": "(BC)216", "typeName": "迷你冰箱", "autoTag": "", "color": "",
     "items": []},
]
SEATS = {"seats": [{"kind": "furniture", "name": "木椅", "x": 14, "y": 13,
                    "capacity": 1, "free": 1, "face": False, "dist": 2}],
         "me": {"sitting": False}}
FURNITURE = {"furniture": [{"name": "红沙发", "x": 15, "y": 13, "width": 2, "height": 1}]}
ANIMALS = {"animals": [{"name": "牛牛", "type": "White Cow", "x": 11, "y": 14,
                        "wasPetToday": False, "friendship": 120}]}
# 👕 穿戴物（形照 `/worn` 的真回包）——⚠️ **两种形状并存**：
#    `shirt`/`pants`/`hat`/`accessory` 回**字符串**，`boots`/`leftRing`/`rightRing`/`trinket`
#    回**字典或 null**（`ModEntry.cs:6960-7030`）。少判一种，"脱"那几行就少一半。
WORN = {"worn": {"shirt": "Blue Shirt", "pants": None, "hat": "Straw Hat",
                 "accessory": None, "boots": {"name": "Old Boots"},
                 "leftRing": None, "rightRing": None, "trinket": None}}


# 🏪 商店那一份（`/menu` 的真回包形状）——货架 + 这家收什么
MENU_SHOP = {
    "type": "ShopMenu",
    "shopItems": [
        {"name": "Strawberry Seeds", "displayName": "草莓种子", "id": "(O)745",
         "price": 100, "stock": 5, "visible": True},
        {"name": "Parsnip Seeds", "displayName": "防风草种子", "id": "(O)472",
         "price": 20, "stock": -1, "visible": True},
    ],
    "shopPage": {"index": 0, "pageSize": 4, "total": 2},
    "sellableHere": ["草莓"],
}

# 📋 开着的**容器**菜单（`/menu` 的真回包形状，2026-10-01 真机照下来的）——
#    `items` 是**领取侧**（`ItemsToGrabMenu.actualInventory`），`index` 就是能点的槽位号。
#    ⚠️ 第 4 条是**反射兜底那条 pass 的形状**（带 `field`）：真机上它可能指向**我自己那侧**
#       ⇒ 必须被滤掉（列出去就是"点了不知道点的是哪一边"）。
MENU_BOX = {
    "ok": True, "open": True, "type": "ItemGrabMenu",
    "dialogue": None, "responses": None, "shopItems": None, "isChoice": False,
    "buttons": [{"name": "okButton", "x": 1116, "y": 616},
                {"name": "trashCan", "x": 1116, "y": 500}],
    "items": [
        # ⚠️ `name` 是**显示名**（C# 那条 pass 写的是 `DisplayName ?? Name`，`ModEntry.cs:13347`）
        #    —— 桩里照抄成中文，才测得到"真机那份形状"（英文名是另一条路）。
        {"index": 0, "name": "啤酒花", "count": 150, "quality": 2, "id": "(O)304"},
        {"index": 1, "name": "啤酒花", "count": 281, "quality": 1, "id": "(O)304"},
        {"index": 2, "name": "钻石", "count": 1, "quality": 0, "id": "(O)72"},
        {"index": 3, "field": "inventory", "name": "木材", "id": "(O)388", "stack": 9},
    ],
    "slots": [], "letterTitle": None, "letterBody": None, "letterFrom": None,
    # ⚠️⚠️ **`gift` 是真机宝箱的真实值（True）** —— `Chest.cs` 给箱子设了
    #    `grabItemFromInventory`，于是 `gift = reverseGrab || behaviorFunction != null` 为真。
    #    我第一版拿 `gift` 当"能不能摊"的闸门 ⇒ 真机上开了箱、单子上**什么都没有**。
    "gift": True, "grabBehavior": "grabItemFromInventory", "menuTitle": None,
}


def _stub(build="2026-09-29 12:00:00 @abc1234", shop=False, menu_get_raises=False,
          caps=None, menu="", menu_raw=None, menu_extra=None, event=None):
    CALLS.clear()
    state = dict(STATE)
    if shop:
        state = dict(STATE, activeMenu={"type": "ShopMenu"})
    elif menu:
        # 🚪 2026-10-01：**菜单态**用例要能指定是哪种界面（出口行给不给看类型）。
        #    `menu_extra` = 那个菜单的**内容**（对话正文/说话人/选项…），形照 `/state.activeMenu`。
        state = dict(STATE, activeMenu=dict({"type": menu}, **(menu_extra or {})))
    elif menu_extra:
        state = dict(STATE, activeMenu=dict(menu_extra))
    if event is not None:
        # 🎬 `/state.activeEvent` 的真形状：`{id, skippable, message}`
        state = dict(state, activeEvent=event)

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/menu":
            if menu_get_raises:
                raise RuntimeError("模拟：商店开着但 /menu 读不出来")
            return MENU_SHOP if menu_raw is None else menu_raw
        return {
            # 🆕 2026-09-30：新 DLL 会带 `caps`（能力位）；`caps=None` = **老 DLL 的形状**（只有 build）。
            "/status": ({"ok": True, "build": build} if caps is None
                        else {"ok": True, "build": build, "caps": caps}),
            "/state": state,
            "/surroundings": SURR,
            "/machines": {"machines": []},
            "/scan_chests": {"chests": CHESTS},
            "/sittable": SEATS,
            "/furniture": FURNITURE,
            "/animals": ANIMALS,
            "/worn": WORN,
        }.get(ep, {})

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        if ep == "/menu/click":            # 买：C# 回那一坨
            return {"ok": True, "clicked": "shop_item",
                    "item": (data or {}).get("item"),
                    "quantity": (data or {}).get("quantity", 1)}
        if ep == "/sell_to_shop":          # 卖：整摞走
            return {"ok": True, "totalGold": 100,
                    "sold": [{"item": (data or {}).get("name"), "sold": 5,
                              "unitPrice": 20, "totalPrice": 100}]}
        return {"ok": True,
                "taken": (data or {}).get("count", 1),
                "stored": [{"item": (data or {}).get("name"),
                            "count": (data or {}).get("count", 1)}]}

    api._ai_get, api._ai_post = g, p
    M._with_state = lambda x, *a, **k: x      # 状态机跟"接线"无关，打桩掉
    # 这两道闸门跟"接线"无关（它们要真游戏在场）；买卖那条路会过它们，先打桩掉。
    M._ensure_background = lambda *a, **k: None
    M._peer_econ_mute = lambda *a, **k: None
    M.intent_menu.reset_menu()                # 单子是全局状态，用例间要清


def ok(name, cond, *extra):
    print(("  ✅ " if cond else "  ❌ ") + name + ("  " + str(extra[0]) if extra else ""))
    return bool(cond)


def main():
    res = []

    # ① caps：**只能由版本信息填，不许从格子里猜**
    _stub()
    caps = M._im_caps()
    res.append(ok("有构建标记 ⇒ 认 forage/diggable/harvestable",
                  caps.get("forage") and caps.get("diggable") and caps.get("harvestable")))
    res.append(ok("⚠️ 但**不认** `chest_open`（这一份是**老 DLL 的兜底**，那版没有这个端点）",
                  "chest_open" not in caps))
    _stub(build="未生成(非 MSBuild 构建)")
    res.append(ok("不是我们编的 DLL ⇒ **空表**（什么都不敢认）", M._im_caps() == {}))

    # ①-b 🆕 2026-09-30：新 DLL 直接把 `caps` 报出来 ⇒ **问游戏，别再猜构建标记**
    _stub(caps={"forage": True, "chest_open": True, "store_slot_quality": False})
    c2 = M._im_caps()
    res.append(ok("有 `caps` ⇒ 以 DLL 自报的为准（`chest_open` 认了 ⇒ 「看」那行才长）",
                  c2.get("chest_open") is True and c2.get("forage") is True))
    res.append(ok("`caps` 里**显式为假**的键不当真（缺键 / 假值 = 这版不会）",
                  "store_slot_quality" not in c2))
    _stub(caps={})
    res.append(ok("`caps` 是**空表**也算 DLL 自报 ⇒ 空表，**不许**退回构建标记多认",
                  M._im_caps() == {}))

    # ② 世界快照：六种料都要拼进 Ctx
    _stub()
    ctx = M._im_ctx()
    res.append(ok("手持认得出来（走 `currentItem`）",
                  (ctx.held or {}).get("name") == "古书"))
    res.append(ok("容器拼进来了（带 freeSlots）",
                  (ctx.tiles.get((13, 13)) or {}).get("chest", {}).get("freeSlots") == 35))
    res.append(ok("座位拼进来了", (ctx.tiles.get((14, 13)) or {}).get("seat", {}).get("name") == "木椅"))
    res.append(ok("家具拼进来了", (ctx.tiles.get((15, 13)) or {}).get("furniture", {}).get("name") == "红沙发"))
    res.append(ok("牲畜拼进来了（`wasPetToday` 就在回包里）",
                  (ctx.tiles.get((11, 14)) or {}).get("animal", {}).get("wasPetToday") is False))
    res.append(ok("猫狗走 npcs 的 kind=pet", [p.get("name") for p in ctx.pets] == ["喵喵"]))

    # ③ show：单子出得来，且抬头是"我在哪/多少体力"
    _stub()
    out = M.intent(ops="show")
    res.append(ok("show 出单子（抬头报位置/体力）", "🎯 FarmHouse (12,12)" in out and "🔋268" in out))
    # ⚠️ 2026-09-29 恒「箱子好多哇！…接到 storage 的原有功能去」⇒ **箱子合一**：
    #    顶层只剩一行「箱子…」，点开是**一览**（一行一箱），再点才是动作面。
    res.append(ok("单子只列**一行**容器（箱子合一）",
                  "箱子…" in out and "矿石箱" not in out))

    # ④ do：**敲号要真打到那个动作上**
    def _top_chest():
        return next(r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "箱子")

    _stub()
    M.intent(ops="show")
    ov = M.intent(ops="do", kw={"code": str(_top_chest())})
    res.append(ok("敲容器行 → 先出**一览**（一行一箱，带箱里是什么）",
                  "矿石箱" in ov and "木材箱" in ov and "钻石" in ov))
    chest_no = next(r.no for r in M.intent_menu._LAST_ROWS if "矿石箱" in (r.label or ""))
    into_box = M.intent(ops="do", kw={"code": str(chest_no)})
    res.append(ok("再敲那一只 → 进动作面（**不是**又做了一遍顶层的事）",
                  "取…" in into_box and "矿石箱 (13,13)" in into_box))
    # 🆕 2026-09-30(178)：**"搬进 tile 时被白名单吃掉字段"** 的回归。
    #    ⚠️ 这个洞**单元用例抓不到**——它长在 `scan_world` → `ctx_from` → 渲染那条**全链**上，
    #    而手搓 box 字典的用例正好绕开它（`_chest_tag` 单测是绿的、真机却是 `⬜ (18,23)`）。
    #    真机实证：C# 吐了 `typeName`，屏②/屏③ 都印「迷你冰箱」，唯独单子印不出。
    _tw = M.intent_menu.scan_world({}, [], [{
        "x": 9, "y": 9, "name": "", "typeId": "(BC)216", "typeName": "迷你冰箱",
        "autoTag": "建材", "color": "#303030", "capacity": 36, "used": 1,
        "freeSlots": 35, "items": []}])
    _cb = (_tw.get((9, 9)) or {}).get("chest") or {}
    res.append(ok("`scan_world` 搬箱子时**不许丢掉** color/autoTag/typeName",
                  _cb.get("typeName") == "迷你冰箱" and _cb.get("autoTag") == "建材"
                  and _cb.get("color") == "#303030"))
    res.append(ok("一览那一行印得出**容器类型**（没人工名时用 `typeName`，不再只剩 `⬜ (x,y)`）",
                  "迷你冰箱" in ov))
    # 取：pick → qty → 真打到 /chest_take
    M.intent_menu.reset_menu()
    M.intent(ops="show")
    M.intent(ops="do", kw={"code": str(_top_chest())})
    M.intent(ops="do", kw={"code": str(next(r.no for r in M.intent_menu._LAST_ROWS
                                            if "矿石箱" in (r.label or "")))})
    take_no = next(r.no for r in M.intent_menu._LAST_ROWS if "取" in (r.label or ""))
    M.intent(ops="do", kw={"code": str(take_no)})
    M.intent(ops="do", kw={"code": "1"})              # 选"钻石"
    r_take = M.intent(ops="do", kw={"code": "1=2"})   # 取 2 个
    res.append(ok("取 真打到 `/chest_take`",
                  any(c[0] == "POST" and c[1] == "/chest_take" for c in CALLS)))
    res.append(ok("取 回执回显对象和数量", "钻石" in r_take and "×2" in r_take))

    # ⑤ at：指哪打哪
    _stub()
    M.intent_menu.reset_menu()
    at = M.intent(ops="at", kw={"x": 13, "y": 12})
    res.append(ok("at 指到野莓 → 出「捡」", "捡" in at))
    at2 = M.intent(ops="at", kw={"x": 99, "y": 99})
    res.append(ok("at 指到空 → 如实说没有，**不编**", "什么都没有" in at2 and "附近" not in at2))
    res.append(ok("at 缺坐标 → 报错 + 说清要什么",
                  "❌" in M.intent(ops="at", kw={})))

    # ⑥ 高阶层动作走的是**拟人那条**（不是裸端点）
    _stub()
    M.intent_menu.reset_menu()
    M.intent(ops="show")
    sit_no = next(r.no for r in M.intent_menu._LAST_ROWS if "坐 木椅" in (r.label or ""))
    M.intent(ops="do", kw={"code": str(sit_no)})
    res.append(ok("坐 走的是 Python 那个高阶层 `sit`（真走过去+读回验证），不是裸端点",
                  not any(c[1] == "/sittable" and c[0] == "POST" for c in CALLS)))

    # ⑦ 🏪 买 / 卖 —— 接线那一段（`/menu` 什么时候打、打了什么）
    _stub()
    ctx = M._im_ctx()
    res.append(ok("没开商店 ⇒ `shop` 是 `None`（**确定的「没有」**）", ctx.shop is None))
    res.append(ok("⚠️ 没开商店时 **`/menu` 一次都不打**（平时不多花一发）",
                  not any(c[1] == "/menu" for c in CALLS)))

    _stub(shop=True)
    ctx = M._im_ctx()
    res.append(ok("商店开着 ⇒ 货架拼进 ctx",
                  len((ctx.shop or {}).get("items") or []) == 2))
    res.append(ok("商店开着 ⇒ 「这家收什么」拼进 ctx",
                  (ctx.shop or {}).get("sellable") == ["草莓"]))
    res.append(ok("钱包从 `/state` 拼进来（不问 `/menu` 要）", ctx.money == 1234))

    # ⚠️ 三态不许折叠：开着但读不出来 ⇒ `{}`（"不知道"），**不是** `None`（"没有"）
    _stub(shop=True, menu_get_raises=True)
    ctx = M._im_ctx()
    res.append(ok("⚠️ 商店开着但 `/menu` 读不出来 ⇒ `{}`（**不是** None）",
                  ctx.shop == {}))

    # 买：show → 点开 → 选 → 各多少 → 真打到 `/menu/click`
    # ⚠️ `n=40` 是必须的：`show` 默认只给前 5 条，而买卖的权重（74/72）**排在后面**
    #    （捡 90 / 摸 84 / 箱子 80 …）⇒ 不放大就**根本看不到那两行**，测试会假红。
    _stub(shop=True)
    out = M.intent(ops="show", kw={"n": 40})
    res.append(ok("商店开着 ⇒ 单子上有「买…」", "买…" in out))
    res.append(ok("商店开着 ⇒ 单子上有「卖…」（背包有这家收的）", "卖…" in out))
    buy_no = next(r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "买")
    shelf = M.intent(ops="do", kw={"code": str(buy_no)})
    res.append(ok("点开「买」→ 出货架（名/价/库存都在）",
                  "草莓种子" in shelf and "100g" in shelf and "剩 5" in shelf))
    M.intent(ops="do", kw={"code": "1,2"})
    CALLS.clear()
    r_buy = M.intent(ops="do", kw={"code": "1=3,2=1"})
    hits = [c[2] for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"]
    res.append(ok("买 真打到 `/menu/click`，**各是各的数量**",
                  [h.get("quantity") for h in hits] == [3, 1]))
    res.append(ok("买 用的是**物品 id**（不是中文名）", hits[0].get("item") == "(O)745"))
    res.append(ok("买 回执逐条列", "草莓种子" in r_buy and "×3" in r_buy))

    # 卖：**没有数量层**，多选直接卖，走 `/sell_to_shop` + 内部名
    M.intent_menu.reset_menu()
    M.intent(ops="show", kw={"n": 40})
    sell_no = next(r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "卖")
    cand = M.intent(ops="do", kw={"code": str(sell_no)})
    res.append(ok("点开「卖」→ 出候选，写清**整摞走**", "整摞" in cand))
    CALLS.clear()
    r_sell = M.intent(ops="do", kw={"code": "1"})
    shits = [c[2] for c in CALLS if c[0] == "POST" and c[1] == "/sell_to_shop"]
    res.append(ok("卖 真打到 `/sell_to_shop`（**没有中间的 qty 层**）", len(shits) == 1))
    res.append(ok("卖 传的是**内部名**（C# 只认 `item.Name`）",
                  shits and shits[0].get("name") == "Strawberry"))
    res.append(ok("卖 回执写清**整摞几个**", "整摞 5 个" in r_sell and "100g" in r_sell))

    # ⑧ ops 写错要有出路（不许静默）
    res.append(ok("不认识的 ops → 报错并列出可用的",
                  "❌" in M.intent(ops="nonsense") and "show" in M.intent(ops="nonsense")))
    res.append(ok("do 不带编号 → 报错 + 给下一步",
                  "❌" in M.intent(ops="do", kw={}) and "code" in M.intent(ops="do", kw={})))

    # ⑨ 🚨 方法闸：**凡是从 body 读参数的裸端点，必须 POST**
    #
    # 2026-09-29 真机抓到：`machine_collect` 漏在 `_IM_POST_OPS` 外 ⇒ `_im_run` 拿它当 GET 打
    # ⇒ 参数落在 **query string** 上，而 C# 的 `ReadJson()` **只读 body**（`GetParamOr` 也只
    # 从那个 dict 取）⇒ `location` 静默变 `""` ⇒ `ResolveLocations("")` 从"脚下这张图"
    # 变成 **农场+所有建筑室内+地窖** —— 单子写 `×20`，按下去收了 **678 台**、背包当场爆掉。
    #
    # ⚠️ 判据**不是**"端点一律要 POST"：`/surroundings` 这类**读 QueryString** 的端点
    #    （`HandleSurroundings` 用的就是 `ctx.Request.QueryString`），GET 带参本来就对。
    #    所以这里只静态扫 `intent_menu` 里**写死了参数**的 `run(op, {...})`，要求它们
    #    要么是 POST 名单里的、要么是**根本不过 HTTP 的 helper**（Python 函数，不走网络）。
    #    ⇒ 以后新接一个裸端点，不在这儿过一道就红了。
    import ast as _ast

    _here = os.path.dirname(os.path.abspath(__file__))

    def _non_http_ops():
        """`_im_run` 里**不走通用 GET/POST 分派**的那些 op——**从源码读**，改名不用改这里。

        两类：`helpers`（Python 函数，压根不过网络）、`raw_ops`（自己 `_ai_post`，
        方法已经写死在函数体里 ⇒ 不受 `_IM_POST_OPS` 管）。
        """
        tree = _ast.parse(open(os.path.join(_here, "nagi_mcp_server.py"), encoding="utf-8").read())
        names, out = {"helpers", "raw_ops"}, set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Assign) and isinstance(node.value, _ast.Dict):
                if any(isinstance(t, _ast.Name) and t.id in names for t in node.targets):
                    out |= {k.value for k in node.value.keys
                            if isinstance(k, _ast.Constant) and isinstance(k.value, str)}
        return out

    helpers = _non_http_ops()
    bad = []
    for node in _ast.walk(_ast.parse(open(os.path.join(_here, "intent_menu.py"),
                                          encoding="utf-8").read())):
        if not (isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name)
                and node.func.id == "run" and len(node.args) >= 2):
            continue
        op, argd = node.args[0], node.args[1]
        # 只看**写死了 op 名 + 参数非空**的那种；变量 op（`_exec_select_then` 的 `ep`）扫不到，
        # 它由 `_IM_POST_OPS` 里的 eat/use 覆盖。
        if (isinstance(op, _ast.Constant) and isinstance(op.value, str)
                and isinstance(argd, _ast.Dict) and argd.keys
                and op.value not in M._IM_POST_OPS and op.value not in helpers):
            bad.append(f"{op.value}(L{node.lineno})")
    res.append(ok(f"带参裸端点一律 POST（没过的：{'、'.join(bad) if bad else '无'}）", not bad))

    # ⑩ 🚪 界面出口（2026-10-01）—— 修的是当天真机抓到的那条**假门**：
    #    开个界面（GameMenu/ItemGrabMenu）⇒ `_candidates` 只留 `menu_ok` ⇒ **一屏空**，
    #    只剩 `0 做点别的…（at x,y 指哪打哪）`，而 `at` 指出来的世界动作**正是**
    #    `do_row` 菜单态守卫要挡的。真机三步：show 空 → at 给「坐 木椅」→ do 被挡。
    #
    # ⚠️ 这一节同时是**防漂移闸门**：`_menu_exit_of`（给不给出口行）跟 `_close_hint`
    #    （这个菜单该怎么处理）**必须同源** —— 后者是唯一的事实地图，两处各写一份早晚漂。
    res.append(ok("🚪 普通界面 ⇒ 给出口行",
                  M._menu_exit_of("GameMenu") == "关掉界面"
                  and M._menu_exit_of("ItemGrabMenu") == "关掉界面"
                  and M._menu_exit_of("ShopMenu") == "关掉界面"
                  and M._menu_exit_of("LetterViewerMenu") == "关掉界面"))
    res.append(ok("🚪 就绪屏 ⇒ 标题说实话（`cancel()` 为它单独写了一条分支）",
                  M._menu_exit_of("ReadyCheckDialog") == "撤就绪 / 关屏"))
    res.append(ok("🚪 没开界面 ⇒ 不给出口行",
                  M._menu_exit_of("") == "" and M._menu_exit_of(None) == ""))
    _drift = []
    for _mt, _what in (("CharacterCustomization", "捏人页"), ("BobberBar", "钓鱼小游戏"),
                       ("DialogueBox", "对话框")):
        _h = M._close_hint(_mt)
        # 同源判据：这一族的原话里必须**明确叫它别关/别动**、或给出**另一条路**
        # （对话框就是"走 advance"）—— 出口行给不给，跟着这句话走。
        _says_other = ("别关" in _h) or ("别去动" in _h) or ("advance" in _h)
        if M._menu_exit_of(_mt) != "" or not _says_other:
            _drift.append(f"{_what}(exit={M._menu_exit_of(_mt)!r})")
    res.append(ok(f"🚪 三个**关不得**的族：不给出口行 **且** `_close_hint` 同源"
                  f"（漂了的：{'、'.join(_drift) if _drift else '无'}）", not _drift))

    # 接线：这两个字段**必须由服务器递进 Ctx**（单子层自己不认菜单名）
    _stub(shop=True)
    ctx = M._im_ctx()
    res.append(ok("🚪 `_im_ctx` 把**出口标题**递进 ctx", ctx.menu_exit == "关掉界面"))
    res.append(ok("🚪 `_im_ctx` 把 `_close_hint` 的**原话**递进 ctx",
                  ctx.menu_hint == M._close_hint("ShopMenu")))
    _so = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🚪 商店开着 ⇒ 「关掉界面」跟 买/卖 **同屏**",
                  "关掉界面" in _so and "买…" in _so))
    # 捏人页：**不给**出口行，改印原话（按 ok = 不可逆定型，劝它就关等于害它）
    _stub(menu="CharacterCustomization")
    ctx = M._im_ctx()
    res.append(ok("🚪 捏人页 ⇒ 不给出口行", ctx.menu_exit == ""))
    _cm = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🚪 捏人页 ⇒ 改印原话，且**不指** `at x,y`（假门拆了）",
                  "别关它" in _cm and "指哪打哪" not in _cm))

    # 关界面那一下：**必须回读核实** —— `cancel()` 自己那句是发射后不管的，
    # 对 `ShippingMenu` 那类"要点 ok 才算完"的界面可能压根没关上。
    # ⚠️ 只打桩网络层（`api.key` / `_menu_close` / `ensure_roles`），**真跑 `cancel()`**：
    #    这一节要验的正是"cancel 之后有没有回读"，替身跑就测不到。
    _stub()
    api.key = lambda *a, **k: None
    api.ensure_roles = lambda *a, **k: None
    M._menu_close = lambda *a, **k: None
    _seen = {"n": 0}

    def _gclose(ep, params=None):
        if ep == "/state":
            _seen["n"] += 1
            # 第 1 次（动手前）= ShopMenu；第 2 次（回读）= 已关
            return dict(STATE, activeMenu=({"type": "ShopMenu"} if _seen["n"] == 1 else None))
        return {}

    api._ai_get = _gclose
    _msg = M._im_close_menu()
    res.append(ok("🚪 关成了 ⇒ 如实说「界面已关」并点名原界面",
                  "界面已关" in _msg and "ShopMenu" in _msg))
    res.append(ok("🚪 回读了 `/state`（动手前 + 动手后）",
                  # ⚠️ 至少 2 次，**不写死 3**：`cancel()` 自己还会读一次
                  #    （它要按菜单类型分流）—— 写死就等于把它的实现细节冻进测试。
                  _seen["n"] >= 2, f"实际 {_seen['n']} 次"))
    _r = M._im_run("close_menu", {})
    res.append(ok("🚪 `_im_run` 认 `close_menu`（走 helpers 那一档、判成 ok）",
                  _r.get("st") == "yes" and "界面已关" in (_r.get("text") or "")))

    # 关不掉 ⇒ **不许谎报成功**，照搬 `_close_hint` 的原话（不另编一句）
    def _gstuck(ep, params=None):
        if ep == "/state":
            return dict(STATE, activeMenu={"type": "ShippingMenu"})
        return {}

    api._ai_get = _gstuck
    _msg2 = M._im_close_menu()
    res.append(ok("🚪 关不掉 ⇒ 如实说「还开着」（**不谎报成功**）",
                  "还开着" in _msg2 and "ShippingMenu" in _msg2))
    res.append(ok("🚪 关不掉 ⇒ 照搬 `_close_hint` 的原话",
                  M._close_hint("ShippingMenu") in _msg2))
    _r2 = M._im_run("close_menu", {})
    res.append(ok("🚪 关不掉 ⇒ `_im_run` 判成 ⚠️（不是 ✅）", _r2.get("st") == "maybe"))

    # ⑪ 👕 穿戴（2026-10-01）—— 接线 + **对着 C# 源码核槽名表**。
    #
    # ⚠️ 槽名是**跨语言的契约**：Python 这边列 `hat`/`shirt`/…，C# 那边 `TryTakeOff`
    #    按同一批字符串分派。两边各写一份 = 早晚漂（漂了的样子是"脱 帽子"按下去回
    #    `未知槽位 'hat'`）。⇒ 这一条**去读 `ModEntry.cs` 的报错原话**当权威清单。
    _slots_cs = set()
    try:
        _src = open(os.path.join(_here, "..", "ModEntry.cs"), encoding="utf-8").read()
        _m = re.search(r"未知槽位[^（]*（([^）]+)）", _src)
        if _m:
            _slots_cs = {x.strip() for x in _m.group(1).split("/") if x.strip()}
    except Exception as e:
        print(f"     （读 ModEntry.cs 失败：{e}）")
    _slots_py = {k for k, _cn in M.intent_menu._WORN_SLOTS}
    res.append(ok(f"👕 「脱」的槽名跟 C# `TryTakeOff` 一致（C#={sorted(_slots_cs)}）",
                  bool(_slots_cs) and _slots_py == _slots_cs,
                  f"Python 多/少的：{sorted(_slots_py ^ _slots_cs)}"))

    # 数据来源：`/worn` **必须打 AI 自己那端**（打错端 = 劝 AI 去脱恒的帽子）
    _stub()
    ctx = M._im_ctx()
    _worn_calls = [c for c in CALLS if c[1] == "/worn"]
    res.append(ok("👕 `_im_ctx` 会读 `/worn`", bool(_worn_calls)))
    res.append(ok("👕 `/worn` 拼进 ctx（字符串槽和字典槽都在）",
                  ctx.worn.get("hat") == "Straw Hat"
                  and (ctx.worn.get("boots") or {}).get("name") == "Old Boots"))
    # ⚠️ 只查"CALLS 里打过 /worn"**不够**：`_ai_get` 被桩掉了，看不出端口。
    #    真判据在**源码**里（`api._ai_get("/worn")`）——同 `_IM_POST_OPS` 那条的守法。
    _worn_src = open(os.path.join(_here, "nagi_mcp_server.py"), encoding="utf-8").read()
    res.append(ok("👕 `/worn` 走的是 `_ai_get`（AI 自己那端），不是 `_get`",
                  'api._ai_get("/worn")' in _worn_src))
    # 单子上要真长出来（顶层一行目录 + 点开是"脱/穿"）
    _stub()
    _wt = M.intent(ops="show", kw={"n": 40})
    res.append(ok("👕 顶层有「穿戴…」一行", "穿戴…" in _wt))
    _wn = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "穿戴"), None)
    _wl = M.intent(ops="do", kw={"code": str(_wn)})
    res.append(ok("👕 点开 = 字符串槽 + 字典槽都列出",
                  "脱 帽子（Straw Hat）" in _wl and "脱 靴子（Old Boots）" in _wl))
    res.append(ok("👕 空槽不列（leftRing/pants 都是 None）",
                  "左戒指" not in _wl and "裤子" not in _wl))
    # ⚠️ `accessory`（面部饰品）**`/worn` 会吐、C# 的槽位表却不认** ⇒ 永远不许列出来
    res.append(ok("👕 `accessory` **不列**（C# 槽位表不认它 → 列了就是按不成）",
                  "面部" not in _wl and "accessory" not in _wl))

    # ⑫ 🧾 过夜结算屏（2026-10-01）——**不给通用出口**，它有自己的行（「确认结算」）。
    #    ⚠️ 判据：`cancel()` 那套是 ESC + menu_close，而结算屏要点 `ok` 才算完
    #       —— 我那条"关不掉就如实说"的验收用例用的**正是** `ShippingMenu`。
    res.append(ok("🧾 结算屏 ⇒ `_menu_exit_of` **不给**出口行", M._menu_exit_of("ShippingMenu") == ""))
    _stub(menu="ShippingMenu")
    _sc = M._im_ctx()
    res.append(ok("🧾 结算屏 ⇒ 递进 ctx 的 `menu_hint` 仍指 `ok`（不是空话）",
                  "ok" in (_sc.menu_hint or "")))
    _so2 = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🧾 结算屏 ⇒ 单子给「确认结算」、**不给**「关掉界面」",
                  "确认结算" in _so2 and "关掉界面" not in _so2))

    # ⑬ 🎬 `activeEvent` 要拼进 ctx（事件**不是菜单** —— 那一刻 activeMenu 可能是 null）
    _stub()
    _ev_state = dict(STATE, activeEvent={"id": "ev1", "skippable": True})
    _orig_g = api._ai_get

    def _gev(ep, params=None):
        if ep == "/state":
            return _ev_state
        return _orig_g(ep, params)

    api._ai_get = _gev
    _ec = M._im_ctx()
    res.append(ok("🎬 `activeEvent` 拼进 ctx", (_ec.event or {}).get("id") == "ev1"))
    _eo = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🎬 事件在播 ⇒ 单子给「推进对话」（那一刻没菜单也照样给）",
                  "推进对话" in _eo))
    api._ai_get = _orig_g

    # ⑭ 📋 菜单摊开：开着的**容器**菜单（2026-10-01 · P-menus 第一刀）
    #    恒：「**开着菜单直接把相关内容摊给它**」——开箱那一刻单子原来是一屏空的。
    _stub(menu="ItemGrabMenu", menu_raw=MENU_BOX)
    _bo = M.intent(ops="show", kw={"n": 40})
    res.append(ok("📋 容器菜单开着 ⇒ 顶层给「箱子里…」", "箱子里…" in _bo))
    # 3 摞 = 三条真格号；第 4 条是**反射兜底形状**（带 `field`）⇒ 必须被滤掉
    res.append(ok("📋 目录行报**几摞**（带 `field` 的兜底形状被滤掉 ⇒ 3）", "3 摞" in _bo))
    res.append(ok("📋 那一刻**也**给「关掉界面」（两行同屏，不互相顶掉）", "关掉界面" in _bo))
    res.append(ok("📋 目录行不印 `at x,y`（菜单态那是假门）", "at x,y" not in _bo))
    _bn = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "箱子里"), None)
    res.append(ok("📋 「箱子里」拿得到号", _bn is not None))
    # ⚠️⚠️ 真机上抓到的**我自己的错**：给「取」配了 `exec` ⇒ 它被顶层扫出来，
    #    一屏 34 行 `取 [金]啤酒花 / 取 蔓越莓…`，目录行反而被淹掉。
    #    判据是 `_candidates` 那条「有 exec **或** subs 才上单子」⇒ 只能给 `exec_multi`。
    res.append(ok("📋 顶层**不许**直接列「取 …」（它只活在子层里）",
                  "取 [金]啤酒花" not in _bo and "取 钻石" not in _bo))
    _bl = M.intent(ops="do", kw={"code": str(_bn)})
    res.append(ok("📋 「箱子里…」那一层是 pick + `exec_on_pick`"
                  "（敲了当场取，**不编一个填了没用的数量层**）",
                  M.intent_menu._STACK[-1].mode == "pick"
                  and M.intent_menu._STACK[-1].exec_on_pick is True))
    res.append(ok("📋 点开 ⇒ 标星的两摞**分得开**（[金]/[银]）",
                  "取 [金]啤酒花" in _bl and "取 [银]啤酒花" in _bl))
    res.append(ok("📋 点开 ⇒ 个数在理由栏（`箱内 ×150`），不在正文里冒充 `×N`",
                  "箱内 ×150" in _bl and "啤酒花 ×150" not in _bl))
    res.append(ok("📋 点开 ⇒ 带 `field` 那条**不列**（点它不知道点的是哪一侧）",
                  "木材" not in _bl))
    # 🚪 子层的 `0` 是**返回上一层**（真动作）——跟顶层那个"这些都不是"不是一回事
    res.append(ok("📋 子层的 `0` = 返回上一层（**真动作，留着**）", "返回上一层" in _bl))
    # 敲下去：真打到 `/menu/click` 的**领取侧**，且用的是**格号**（不是名字）
    _dn = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "取 钻石"), None)
    _do = M.intent(ops="do", kw={"code": str(_dn)})
    _clicks = [c for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"]
    res.append(ok("📋 「取 钻石」真打到 `/menu/click` 的领取侧 + 格号",
                  bool(_clicks) and _clicks[-1][2].get("action") == "claim"
                  and _clicks[-1][2].get("slot") == 2, _clicks[-1][2] if _clicks else None))
    res.append(ok("📋 回执说清取了什么", "✅" in _do and "钻石" in _do))
    # 🔍 复验：菜单关了 / 那格没了 ⇒ 旧号必须被拒（`_recheck` 那条新分支）。
    #    ⚠️ 得**先真进到子层**（就是"看着旧号敲"那个处境），**不能**重开单子 ——
    #       重开就等于替 AI 刷新了世界，那测的就不是复验了。
    _stub(menu="ItemGrabMenu", menu_raw=MENU_BOX)
    M.intent(ops="show", kw={"n": 40})
    _bn2 = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "箱子里"), None)
    M.intent(ops="do", kw={"code": str(_bn2)})
    _dn2 = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "取 钻石"), None)
    _g2 = api._ai_get                       # 世界变了：箱子**空了**（单子还停在上一屏）
    api._ai_get = lambda ep, params=None: (dict(MENU_BOX, items=[])
                                           if ep == "/menu" else _g2(ep, params))
    _stale = M.intent(ops="do", kw={"code": str(_dn2)})
    res.append(ok("📋 箱里那摞没了 ⇒ 敲旧号被**拦住**（不复验就会点错格）",
                  _stale.startswith("⏳") and "已经不在这个菜单里" in _stale,
                  (_stale.splitlines() or [""])[0]))
    # ⚠️⚠️ 这一族是**真机当场照出来的教训**：普通宝箱 `gift=True` **照样要摊**
    #    ⇒ 判据只能是**行为函数叫什么**（`grabBehavior`），不是 `gift`。
    _stub(menu="ItemGrabMenu", menu_raw=dict(MENU_BOX, gift=True))
    res.append(ok("📋 `gift=True` 的**普通宝箱**照样摊（判据是行为函数名，不是 gift）",
                  "箱子里…" in M.intent(ops="show", kw={"n": 40})))
    # 🚫 同样 `type=ItemGrabMenu`、同样 `gift=True`，但点物品**不是"取"** ⇒ 一个字都不许摊
    #    （写着「取 钻石」= 让 AI 把钻石投进箱/加进汤/当礼物送掉，**不可逆**）
    for _gb, _cn in (("shipItem", "投出货箱"), ("clickToAddItemToLuauSoup", "百乐汤"),
                     ("chooseSecretSantaGift", "冬星节礼物"), ("SomeNewBehavior", "没见过的行为")):
        _stub(menu="ItemGrabMenu", menu_raw=dict(MENU_BOX, gift=True, grabBehavior=_gb))
        _xo = M.intent(ops="show", kw={"n": 40})
        res.append(ok(f"🚫 `{_gb}`（{_cn}）⇒ **不摊**（认不出来也当不能取）",
                      "箱子里" not in _xo and "取 钻石" not in _xo))
    # 没开菜单时**一个字节都不多花**（不能为了这个新功能给每次 show 都加一次 `/menu`）
    _stub()
    M.intent(ops="show", kw={"n": 40})
    res.append(ok("📋 没开菜单 ⇒ **不打** `/menu`",
                  not [c for c in CALLS if c[1] == "/menu"]))

    # ⑮ 🎬 对话/演出摊开（2026-10-01 · 恒：「**进剧情要不要藏掉坐标？反正也不给移动**」
    #    + 「**给选项的话就给 1 接 advance、2 跳过 好了**」）
    _stub(menu="DialogueBox", event={"id": "1053978", "skippable": True},
          menu_extra={"dialogue": "瞧！我的最新作品。", "speaker": "罗宾", "responses": None})
    _dl = M.intent(ops="show", kw={"n": 20})
    res.append(ok("🎬 演出中 ⇒ 抬头**不印坐标**（真机那一刻是 `-99,-99` 哨兵，照印=让 AI 去 at 它）",
                  "(-99,-99)" not in _dl and "🎬 演出中" in _dl))
    res.append(ok("🎬 正文 + 说话人**进抬头**（不看内容就选 = 瞎选）",
                  "💬 罗宾：瞧！我的最新作品。" in _dl))
    _adv = next((r.no for r in M.intent_menu._LAST_ROWS if r.verb.key == "advance"), None)
    _skp = next((r.no for r in M.intent_menu._LAST_ROWS if r.verb.key == "skip_event"), None)
    res.append(ok("🎬 就是恒要的那个形状：**1=推进、2=跳过**",
                  _adv == 1 and _skp == 2, f"advance={_adv} skip={_skp}"))
    res.append(ok("🎬 「跳过」的理由写清**代价**（剧情就不播了）", "剧情就不播了" in _dl))
    # 🚪 恒当场问的：「选 0 的话，其后有什么？自由操作吗？但是菜单又有门禁。理论上确实只能做 1、2。」
    #    ⇒ 菜单态顶层**不发 `0`**（它只通向"先把界面处理掉"= 就是上面第 1 行，白烧一次调用）；
    #      那句"其后有什么"改成**直接印在眼前**。
    res.append(ok("🚪 菜单态顶层**不发 `0`**（它不通向任何新动作）",
                  "\n 0  这些都不是" not in _dl and "\n 0 " not in _dl))
    res.append(ok("🚪 菜单态把那句**界面怎么处理**直接印出来（不必再敲一下才知道）",
                  "📄" in _dl and "menu advance" in _dl))
    # ⚠️ 跳不动就不给那一行（`skipEvent()` 没这个位会退化成"按 ESC 关菜单"= 另一件事）
    _stub(menu="DialogueBox", event={"id": "9", "skippable": False},
          menu_extra={"dialogue": "嗯。", "speaker": "罗宾"})
    _ns = M.intent(ops="show", kw={"n": 20})
    res.append(ok("🎬 `skippable=false` ⇒ **没有**「跳过整段」（不是灰掉，是整行不出现）",
                  "跳过整段" not in _ns and "推进对话" in _ns))
    # 没事件、只是普通搭话 ⇒ 坐标照常印，且只有「推进对话」
    _stub(menu="DialogueBox", menu_extra={"dialogue": "早啊。", "speaker": "罗宾"})
    _nd = M.intent(ops="show", kw={"n": 20})
    res.append(ok("💬 普通对话（没事件）⇒ 坐标**照常印**、只有「推进对话」",
                  "(12,12)" in _nd and "推进对话" in _nd and "跳过整段" not in _nd))
    # 对话那份数据**从 `/state` 拿**（`/state.activeMenu` 里就带 dialogue/speaker）——
    # 多打一次 `/menu` 是白花一次调用
    _stub(menu="DialogueBox", menu_extra={"dialogue": "嗯。", "speaker": "罗宾"})
    M.intent(ops="show", kw={"n": 20})
    res.append(ok("💬 对话**不打** `/menu`（`/state` 里就有）",
                  not [c for c in CALLS if c[1] == "/menu"]))
    # 执行侧：跳过调的是现成的 `skip_event()`（**不自己按 ESC** —— 没事件时那是另一件事）。
    # ⚠️ 这里**只查源码不真调**：`skip_event()` 走 `api.state()`（`_get` 不是 `_ai_get`），
    #    桩换不掉它 ⇒ 真调会打到**正在跑的游戏**（7842 = 恒）—— 自验绝不许碰游戏。
    res.append(ok("⏭ `_im_run` 认 `skip` 且调的是 `skip_event()`",
                  '"skip": lambda: skip_event()' in _worn_src))
    # 📍 坐标那条判据**只有一处**（`_coord_seg`）：真机上我改完抬头、忘了状态条，
    #    同一屏就印出两种说法（抬头 `🎬 演出中`、状态条 `(-99,-99)`）——恒一眼看得见。
    res.append(ok("📍 `_coord_seg` 四种组合都对（演出在播 / 坐标不在图里 / 都正常）",
                  M._coord_seg(12, 12, None) == " (12,12)"
                  and M._coord_seg(-99, -99, None) == ""
                  and M._coord_seg(12, 12, {"id": "1"}) == " · 🎬 演出中"
                  and M._coord_seg(-99, -99, {"id": "1"}) == " · 🎬 演出中"))
    res.append(ok("📍 状态条 📍 那行**用的也是它**（不是各写一份）",
                  'lines.append(f"📍 {loc_name}{_pos_seg}' in _worn_src))

    # ⑯ 🗳 对话选项各一行（2026-10-01 真机撞上的：罗宾那句「美学 vs 浪费」）
    #    ⚠️ 顺带钉一个**真 bug 的回归**：原来 `_advance_can` 把"事件在播"排在"有选项"前面
    #    ⇒ 有选项时照样给「推进对话」，可按下去只会把同一屏选项读回来（看得见、按了白按）。
    _opts = ["美学设计棒极了", "四根柱子看起来有点浪费"]
    _stub(menu="DialogueBox", event={"id": "1053978", "skippable": True},
          menu_extra={"dialogue": "你觉得呢？", "speaker": "罗宾", "responses": list(_opts)})
    _ol = M.intent(ops="show", kw={"n": 20})
    res.append(ok("🗳 有选项 ⇒ **不给「推进对话」**（事件在播也一样 —— 按了只会读回同一屏）",
                  "推进对话" not in _ol))
    res.append(ok("🗳 两个答案各一行、引号里是原话",
                  "「美学设计棒极了」" in _ol and "「四根柱子看起来有点浪费」" in _ol))
    res.append(ok("🗳 那一刻「跳过整段」还在（退路不丢）", "跳过整段" in _ol))
    _o1 = next((r.no for r in M.intent_menu._LAST_ROWS if "美学设计棒极了" in (r.label or "")), None)
    # ⚠️ 桩要**模拟"点了之后那屏才过去"**：复验看的是**点之前**的世界（选项还在），
    #    而 helper 的回读看的是**点之后**的世界（选项没了）。一个静态桩两处都喂同一份，
    #    就变成"复验把这一敲拦下来"，测的就不是这条路了。
    _g3 = api._ai_get
    _p3 = api._ai_post
    _flip = {"done": False}

    def _g3x(ep, params=None):
        if ep == "/state":
            return dict(STATE, activeMenu=(
                {"type": "DialogueBox", "dialogue": "谢谢！", "speaker": "罗宾"} if _flip["done"]
                else {"type": "DialogueBox", "dialogue": "你觉得呢？", "speaker": "罗宾",
                      "responses": list(_opts)}))
        return _g3(ep, params)

    def _p3x(ep, data=None):
        r = _p3(ep, data)
        if ep == "/menu/click":
            _flip["done"] = True
        return r

    api._ai_get, api._ai_post = _g3x, _p3x
    _oo = M.intent(ops="do", kw={"code": str(_o1)})
    _clicks = [c for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"]
    res.append(ok("🗳 敲它 = `/menu/click` 带**位次**（option=0）",
                  bool(_clicks) and _clicks[-1][2].get("option") == 0,
                  _clicks[-1][2] if _clicks else None))
    res.append(ok("🗳 回执**回读核实**（说清选了哪个 + 选项那屏过了）",
                  "✅" in _oo and "美学设计棒极了" in _oo and "已经过去了" in _oo,
                  (_oo.splitlines() or [""])[-1]))
    # 🔍 复验：对话往下走了 ⇒ 同一个位次上**换成了另一句** ⇒ 旧号必须被拦
    #    （只核位次会"以为在选 A、实际选了 B"）
    _stub(menu="DialogueBox", menu_extra={"dialogue": "你觉得呢？", "speaker": "罗宾",
                                          "responses": list(_opts)})
    M.intent(ops="show", kw={"n": 20})
    _o1b = next((r.no for r in M.intent_menu._LAST_ROWS if "美学设计棒极了" in (r.label or "")), None)
    _g4 = api._ai_get
    api._ai_get = lambda ep, params=None: (
        dict(STATE, activeMenu={"type": "DialogueBox", "dialogue": "换个问题", "speaker": "罗宾",
                                "responses": ["要", "不要"]})
        if ep == "/state" else _g4(ep, params))
    _st = M.intent(ops="do", kw={"code": str(_o1b)})
    res.append(ok("🗳 选项换了一批 ⇒ 旧号被**拦住**（位次+文字一起核）",
                  _st.startswith("⏳") and "已经不在这一屏了" in _st,
                  (_st.splitlines() or [""])[0]))
    # ⚠️ 两条选项**一字不差**时不许并成一行（执行器只发一个答案 = 假承诺）
    _stub(menu="DialogueBox", menu_extra={"dialogue": "?", "speaker": "罗宾",
                                          "responses": ["好", "好"]})
    _dup = M.intent(ops="show", kw={"n": 20})
    res.append(ok("🗳 一字不差的两条选项**不并成一行**（补位次区分）",
                  "（第 1 个）" in _dup and "（第 2 个）" in _dup))
    res.append(ok("🗳 `_im_run` 认 `menu_option` 且走 `_im_menu_option`（回读到 `_ai_get`）",
                  '"menu_option": lambda: _im_menu_option(args.get("option"), args.get("real"))'
                  in _worn_src))
    # 🗳⚠️ `real=true` 那一档（真机 2026-10-01：不带 real 时 C# 回 ok:true 而**选项还在屏上**）：
    #    判据由**服务器**给（`_question_needs_real`），执行照它发。
    for _qk, _want in (("ask", True), ("npc", True), (None, None), ("plain", None)):
        _extra = {"dialogue": "你觉得呢？", "speaker": "罗宾", "responses": list(_opts)}
        if _qk:
            _extra["questionKind"] = _qk
        _stub(menu="DialogueBox", menu_extra=_extra)
        M.intent(ops="show", kw={"n": 20})
        _n2 = next((r.no for r in M.intent_menu._LAST_ROWS
                    if "美学设计棒极了" in (r.label or "")), None)
        M.intent(ops="do", kw={"code": str(_n2)})
        _c2 = [c for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"]
        _got = _c2[-1][2].get("real") if _c2 else "没发"
        res.append(ok(f"🗳 questionKind={_qk!r} ⇒ 执行带 real={_want!r}（猜错=静默点空）",
                      _got == _want, _c2[-1][2] if _c2 else None))
    # 判据只有一处：`_menu_advice` 的**说法**和它必须同源（不然又是一处漂）
    res.append(ok("🗳 `_question_needs_real` 与 `_menu_advice` 的说法**同源**",
                  M._question_needs_real({"questionKind": "ask"}) is True
                  and M._question_needs_real({"questionKind": "npc"}) is True
                  and M._question_needs_real({}) is None
                  and "real=true" in M._menu_advice("dialoguebox",
                                                    {"responses": ["a"], "questionKind": "ask"}, {})
                  and "real=true" in M._menu_advice("dialoguebox",
                                                    {"responses": ["a"], "questionKind": "npc"}, {})))
    # ❓ 问句读不出来时（游戏那一档 `getCurrentString()` 就是空串）⇒ 用**手里那份真事实**
    #    （台词缓冲的"上一句"）如实顶上，**不冒充"这就是问句"**。
    _stub(menu="DialogueBox", menu_extra={"dialogue": None, "speaker": None,
                                          "responses": list(_opts)})
    M._story_buffer[:] = ["罗宾「你说，是不是大家看着都觉得赏心悦目？」"]
    _sq = M.intent(ops="show", kw={"n": 20})
    res.append(ok("❓ 问句读不出来 ⇒ 抬头如实说「刚说的：上一句」（不冒充问句）",
                  "刚说的" in _sq and "赏心悦目" in _sq))
    M._story_buffer[:] = []

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
