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
         "catNum": -102, "stack": 1, "quality": 0, "sellable": False},
        {"slotIndex": 3, "name": "Strawberry", "displayName": "草莓", "itemId": "(O)400",
         "catNum": -79, "stack": 5, "quality": 0, "edibleValue": 20, "healthRecovered": 0},
        {"slotIndex": 4, "name": "Hoe", "displayName": "锄头", "itemId": "(T)Hoe",
         "catNum": -99, "stack": 1, "quality": 0},
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


def _stub(build="2026-09-29 12:00:00 @abc1234", shop=False, menu_get_raises=False,
          caps=None):
    CALLS.clear()
    state = dict(STATE)
    if shop:
        state = dict(STATE, activeMenu={"type": "ShopMenu"})

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/menu":
            if menu_get_raises:
                raise RuntimeError("模拟：商店开着但 /menu 读不出来")
            return MENU_SHOP
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

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
