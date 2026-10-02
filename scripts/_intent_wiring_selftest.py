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

# 🚪🐄 门那条路的桩状态（`_stub` 每次按 `farm_buildings=` / `doors_open=` 重算）：
#    `door_buildings` = 翻哪几栋；`door_open_state` = **现在**这些门开着没。
#    ⚠️ 桩**照抄 C# 的真行为**：`/toggle_doors` 忽略 action、**纯翻转**（`netBool.Value = !netBool.Value`）
#    —— 这一位就是"翻转后的门态"，`doors()` 的回读 + 单子按目标态收敛全靠它才测得出来。
door_buildings = []
door_open_state = False
# 🚶 走位调用记录（`_stub` 每次清空；门那条路要证"**只走一次**"—— 收敛的第二下不许再走）。
WALK_CALLS = []
# ⚠️ `api.close_doors()` 走的是 **`api._post`**（不是 `_ai_post`）⇒ 敲门那几条用例要把它接上桩。
_LAST_P = None
# 🏗 农场建筑（形照 `/farm_buildings` 真回包）。`type` = `Data/Buildings` 的键
#    （英文内部名 `Coop`/`Deluxe Barn`，不是显示名）；`doorX/doorY` 是**人类门**的格子。
FARM_BUILDINGS = [
    {"type": "Deluxe Coop", "x": 40, "y": 12, "width": 7, "height": 4,
     "doorX": 44, "doorY": 16, "indoorsName": "Deluxe Coop"},
    {"type": "Deluxe Barn", "x": 48, "y": 12, "width": 7, "height": 4,
     "doorX": 52, "doorY": 16, "indoorsName": "Deluxe Barn"},
    # ⚠️ **非动物建筑**（温室就在 `/farm_buildings` 里）：它**不许**被数进 `doors.builds`
    #    —— 判据是 `type` 含 `Coop`/`Barn`，不是"农场上的建筑个数"。
    {"type": "Greenhouse", "x": 28, "y": 20, "width": 7, "height": 7},
]

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
    # ⚠️ 2026-10-01：`objId` 是**真回包本来就有的**字段（`(O)` 开头 = 普通物件）；
    #    「捡」那行的新判据（`pickup_scene.scan_pickables()`）就是看它 ⇒ 夹具补上，
    #    否则这条野莓在夹具里"不可捡"、后面那些「捡」的用例会假红。
    "tiles": [{"x": 13, "y": 12, "passable": True, "forage": True, "object": "野莓",
               "objId": "(O)296"}],
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
# 🔧 机器（形照 `/machines` 的真回包）。默认**空表**（老用例一个字都不变）；
#    铁砧用例显式传 `machines=[...]`。
MACHINES = []


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
          caps=None, menu="", menu_raw=None, menu_extra=None, event=None,
          chests=None, inv=None, machines=None, mastery=None, loc=None,
          farm_buildings=(), time_dict=None, doors_open=False, walk_ok=True,
          chore_tiles=None, crab_ready=0, ore_pan=None, chore_animals=None, money=None,
          silo=None, troughs=None, trough_filled=0, trough_raise=False,
          surr_tiles=None):
    CALLS.clear()
    WALK_CALLS.clear()
    state = dict(STATE)
    if time_dict is not None:
        # 🕐 `/state.time` 的真形状（**字典**，不是标量串）：`{"timeOfDay":1320,"season":"summer",
        #    "weather":1,...}` —— 钟点/天气/季节都在这份里（`_clock_of` / `_im_doors` 读它）。
        #    ⚠️ 键名照状态条那份（`weather_texts`/`season_icons`）；写错**不报错**、只是永远不成立。
        state = dict(state, time=time_dict)
    if inv is not None:
        # 🎒 背包换一份（🥕 用例：背包里没东西能存 ⇒ 「存…」那行不该出现）
        state = dict(state, inventory=inv)
    if shop:
        state = dict(state, activeMenu={"type": "ShopMenu"})
    elif menu:
        # 🚪 2026-10-01：**菜单态**用例要能指定是哪种界面（出口行给不给看类型）。
        #    `menu_extra` = 那个菜单的**内容**（对话正文/说话人/选项…），形照 `/state.activeMenu`。
        #    ⚠️ 从 `state` 起（**不是** `STATE`）—— 否则上面刚塞进去的 `inv` 会被**静默盖回**
        #      （同族：180 那次"同一个 dict 里键写两遍，前一个被吃掉"）。
        state = dict(state, activeMenu=dict({"type": menu}, **(menu_extra or {})))
    elif menu_extra:
        state = dict(state, activeMenu=dict(menu_extra))
    if event is not None:
        # 🎬 `/state.activeEvent` 的真形状：`{id, skippable, message}`
        state = dict(state, activeEvent=event)
    if loc is not None:
        # 🗺 换图（用例：砸晶球只在铁匠铺给那一行）
        state = dict(state, location={"name": loc, "uniqueName": loc})
    # 🌿 六件"顺手活"（P1 那批）的料：`/state.player.orePan` + 追加的采集格
    if ore_pan is not None:
        state = dict(state, player=dict(state.get("player") or {}, orePan=ore_pan))
    if money is not None:
        # 💰 用例要"买不起/砸不起"就传 money=0（`_geode_can` 用 `ctx.money` 判 25g/颗）
        state = dict(state, player=dict(state.get("player") or {}, money=money))
    if chore_animals is None:
        _animals_fixture = ANIMALS
    elif isinstance(chore_animals, list):
        # ⚠️ 端点真形状是 `{"animals":[…]}` —— 用例里图省事传 list 也认（第一版直接把 list
        #    当回包发出去 ⇒ `_im_chores` 里 `list.get` 当场炸、6 行全空，自验当场逮到）。
        _animals_fixture = {"animals": chore_animals}
    else:
        _animals_fixture = chore_animals
    # ⚠️ `surr_tiles=[]` = "这一带**一件可捡的都没有**"（验「捡」那行不出现）
    _tiles_fixture = ((list(SURR.get("tiles") or []) if surr_tiles is None else list(surr_tiles))
                      + list(chore_tiles or []))

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/menu":
            if menu_get_raises:
                raise RuntimeError("模拟：商店开着但 /menu 读不出来")
            return MENU_SHOP if menu_raw is None else menu_raw
        if ep == "/crab_pots":
            # 🦀 真回包形状：`{ok, count, location, pots:[{...readyForHarvest...}]}`
            return {"ok": True, "count": crab_ready, "location": "Farm",
                    "pots": [{"x": 20 + i, "y": 21, "readyForHarvest": True,
                              "bait": "鱼饵"} for i in range(int(crab_ready))]}
        if ep == "/silo":
            # 🌾 `/silo` 真回包形状（`silo_status()` 读的就是这几个键）
            return ({"ok": True, "noSilo": True} if silo is None and troughs is not None
                    else dict({"ok": True, "silos": 1, "capacity": 240, "room": 237,
                               "full": False}, **(silo or {"hay": 3})))
        if ep == "/tile_props":
            if trough_raise:
                raise RuntimeError("模拟：/tile_props 读不到")
            return {"ok": True, "scan": "Trough",
                    "hits": [{"x": x, "y": y} for x, y in (troughs or [])]}
        if ep == "/dump_tile":
            # 前 `trough_filled` 格有干草（判据 = 那格的物件 id 是不是 Hay `(O)178`）
            _x = int(((params or {}).get("x")) or -1)
            _y = int(((params or {}).get("y")) or -1)
            _idx = ([tuple(t) for t in (troughs or [])].index((_x, _y))
                    if (_x, _y) in [tuple(t) for t in (troughs or [])] else -1)
            _has = 0 <= _idx < int(trough_filled)
            return {"ok": True, "tile": {"object": {"qualifiedId": "(O)178" if _has else None}}}
        if str(ep).startswith("/process_geode_batch"):
            # 🪨 砸晶球（那条 op 把 count 放在 query 里，所以按前缀匹配）
            return {"ok": True, "processed": 3, "cost": 75, "remainingGold": 1234,
                    "results": [{"itemName": "钻石"}, {"itemName": "石英"}, {"itemName": "粘土"}]}
        return {
            # 🆕 2026-09-30：新 DLL 会带 `caps`（能力位）；`caps=None` = **老 DLL 的形状**（只有 build）。
            "/status": ({"ok": True, "build": build} if caps is None
                        else {"ok": True, "build": build, "caps": caps}),
            "/state": state,
            "/surroundings": dict(SURR, tiles=_tiles_fixture),
            "/machines": {"machines": MACHINES if machines is None else machines},
            "/scan_chests": {"chests": CHESTS if chests is None else chests},
            "/sittable": SEATS,
            "/furniture": FURNITURE,
            "/animals": _animals_fixture,
            "/worn": WORN,
            # 🚪🐄 农场建筑（`/farm_buildings` 的真回包形状）：`type` = `Data/Buildings` 的键。
            #    默认**空表** = "这个档没有动物建筑" ⇒ 放牧/关棚门那两行不出现（老用例一个字不变）。
            "/farm_buildings": {"ok": True, "count": len(farm_buildings),
                                "buildings": list(farm_buildings)},
            # 🎓 精通（`_mastery_claimed` 的源）：默认**读不到**（= 谁都没领），
            #    用例要"已领战斗精通"就显式传 `mastery=[...]`。
            "/mastery": ({"ok": True, "plaques": []} if mastery is None
                         else {"ok": True, "plaques": mastery}),
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
        if ep == "/select":                # 拿在手上（重铸那条路的第一步）
            return {"ok": True, "selected": (data or {}).get("name")}
        if ep == "/toggle_doors":
            # 🚪 C# `/toggle_doors` 的真形状：**忽略 action**、纯翻转，回 `{ok, toggled, details}`；
            #    `details` 每项 `{building, door_open}`（⚠️ 没有 `door` 坐标对 —— 旧代码就是栽在这个键上）。
            #    桩照抄这个"翻转"语义 ⇒ `doors()` 那条**回读 + 单子按目标态再翻一次**的路才走得到
            #    （只回一个固定值的桩会把收敛那段测成假绿）。
            global door_open_state
            door_open_state = not door_open_state
            _dl = [{"building": b, "door_open": door_open_state} for b in door_buildings]
            return {"ok": True, "toggled": len(_dl), "details": _dl}
        if ep == "/interact":              # 交互（真机形状：ok 恒真、actionTriggered 才是真话）
            return {"ok": True, "actionTriggered": True, "object": "Anvil"}
        return {"ok": True,
                "taken": (data or {}).get("count", 1),
                "stored": [{"item": (data or {}).get("name"),
                            "count": (data or {}).get("count", 1)}]}

    api._ai_get, api._ai_post = g, p
    # 🚫🚫 **出口全部封死**（2026-10-01：这个文件原来会**打到真机**）——
    #    真凶是 `_walk_to_chest`（`_im_chest_op` 里那条"走到箱子边"）：它走 `api.state()`（`_get`，
    #    **没桩** ⇒ 读的是**真游戏的当前图**）+ `navigation._walk_and_wait`（**真走位**），
    #    兜底还会 `api.position()`（`_post`，**瞬移**）⇒ 我跑自验时**把 AI 角色从 Farm(48,42)
    #    搬到了 Farm(11,12)**（夹具里箱子的坐标 + 真实地图）。⚠️ 直连游戏端口的调用**不进 MCP 工具日志**
    #    —— 所以那晚"日志里一次调用都没有，人却换了地方"。
    #    ⇒ 四个出口一律接桩：`_get`/`_post`（**同一发都不许出网**）+ 走位那两处（别真走路）。
    api._get, api._post = g, p
    M._walk_to_chest = lambda x, y: "  🚶（自验桩：没走）"
    # ⚠️⚠️ **必须桩 `M._walk_and_wait`**（服务器命名空间里的那个）：它是 `from navigation import …`
    #    绑进来的**名字**，桩 `M.navigation._walk_and_wait` **改不到它** —— 第一次就是只桩了
    #    navigation 那份，结果门那条路**真走位**：`/walk_to`（桩回了个不含 destination 的空包）
    #    → `_wait_arrival` 拿夹具坐标去等真人 → **每发死等 15 秒**（自验从 18 秒变卡死）。
    M._walk_and_wait = lambda loc, x, y, timeout=25: (
        WALK_CALLS.append((loc, x, y)),
        (True, "") if walk_ok else (False, "走位超时没到"))[1]
    M.navigation.walk_to = lambda *a, **k: None
    api.position = lambda x, y: {"ok": True, "stub": True}
    # 把桩函数也留一份在模块级：有些 op 走的是 **`api._get`**（不是 `_ai_get`），
    # 用例要临时把 `_get` 也接到同一个桩上（例：`process_geodes`）。
    global _LAST_G, _LAST_P, door_buildings, door_open_state
    _LAST_G = g
    # ⚠️ 有些 op 走的是 **`api._post`**（不是 `_ai_post`）—— `api.close_doors()` 就是
    #    （它内部 `_post("/toggle_doors", …)`）。用例要临时把 `_post` 也接到同一个桩上，
    #    否则"敲放牧/关棚门"会真的去敲 localhost:7842（真机端口！）而不是走桩。
    _LAST_P = p
    # 🚪 门那条路的**桩状态**：`door_buildings` 决定翻哪几扇（`/toggle_doors` 的桩读它）。
    #    默认（没给 farm_buildings 的老用例）给两栋 —— 那两栋只在**真去敲门**时才会被用到，
    #    而老用例一个都不敲门 ⇒ 行为一个字不变。
    door_buildings = [str(b.get("type") or "?") for b in (farm_buildings or ())
                      if "Coop" in str(b.get("type") or "") or "Barn" in str(b.get("type") or "")]
    if not door_buildings:
        door_buildings = ["Deluxe Coop", "Deluxe Barn"]
    door_open_state = bool(doors_open)          # 桩的"现在门开着没"（照 C# 的翻转语义用）
    M._with_state = lambda x, *a, **k: x      # 状态机跟"接线"无关，打桩掉
    # 🔁 两个**跨用例的缓存**（精通 30s / `/machine_reqs` 10min）在这里清干净：
    #    它们是给热路径省调用用的，可"上一个用例问到的答案"会被下一个用例读到
    #    —— 真机不会（世界一直变），桩会 ⇒ **每个用例从头开始**（2026-10-01 自验现场逮到）。
    M._MASTERY_CACHE.update(ts=0.0, claimed=None)
    M._MACHINE_REQS_CACHE.update(ok=None, ts=0.0)
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
    # ⚠️ 197 口径 B（2026-10-02）之后 `坐`(26) **沉到第一屏之外**了 ⇒ 这里要 `n=40` 才拿得到它
    #    （`_LAST_ROWS` 只存**这一屏印出来的那 n 条** —— 原来写死默认 n=5，B 一落地就 StopIteration）。
    M.intent(ops="show", kw={"n": 40})
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
    # ⚠️ 2026-10-02：「穿戴」撤出单子 ⇒ **Python 那份槽表（`_WORN_SLOTS`）已删**，
    #    所以这条从"两边对齐"改成"C# 那份权威清单**还在**、而 Python 侧确实没留半截"。
    #    哪天恢复穿戴，**回到这条**把两张表重新对齐。
    _slots_cs = set()
    try:
        _src = open(os.path.join(_here, "..", "ModEntry.cs"), encoding="utf-8").read()
        _m = re.search(r"未知槽位[^（]*（([^）]+)）", _src)
        if _m:
            _slots_cs = {x.strip() for x in _m.group(1).split("/") if x.strip()}
    except Exception as e:
        print(f"     （读 ModEntry.cs 失败：{e}）")
    res.append(ok(f"👕 C# 的槽位权威清单**还在**（C#={sorted(_slots_cs)}）；Python 侧 `_WORN_SLOTS` 已随穿戴撤掉",
                  bool(_slots_cs) and not hasattr(M.intent_menu, "_WORN_SLOTS"),
                  f"C#={sorted(_slots_cs)}"))

    # 数据来源：`/worn` **必须打 AI 自己那端**（打错端 = 劝 AI 去脱恒的帽子）
    # ⚠️ 2026-10-02：单子上**已经没有穿戴那行**了（撤走），但 `/worn` 这条读取**留着**
    #    （`Ctx.worn` 仍是"我自己穿着什么"的事实来源，将来谁要用直接有）。用例照旧守它。
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
    # ⛔ 195b：单子上**不再**长出来（原来这里验"顶层一行目录 + 点开是脱/穿"那一整套）
    _stub()
    _wt = M.intent(ops="show", kw={"n": 40})
    res.append(ok("👕⛔ 195b：`/worn` 里有东西也**不再**长出「穿戴」那行",
                  "穿戴" not in _wt))
    res.append(ok("👕⛔ 195b：`_im_run` 的 `wear` helper 也删了（墓碑注释在，指向 `daily wear`）",
                  "穿 / 脱的 helper 2026-10-02 删了" in _worn_src))

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

    # ⑰ 📧 信件摊开（2026-10-01 · P-menus 第四刀）—— 恒有 18 封未读，真机拿 Linus 那封验的。
    #    ⚠️ 这一档**必须打 `/menu`**：`/state.activeMenu` 里**压根没有** letterTitle/letterBody（真机核过）。
    _stub(menu="LetterViewerMenu",
          menu_raw={"type": "LetterViewerMenu", "letterTitle": "Linus",
                    "letterBody": "你好，朋友。^最近我在山湖那里的收获不错。 ^——莱纳斯 ",
                    "buttons": [{"name": "upperRightCloseButton", "x": 1268, "y": 16}]})
    _lo = M.intent(ops="show", kw={"n": 20})
    res.append(ok("📧 信件正文进抬头（标题 + 逐行缩进）",
                  "📧 Linus 的信：" in _lo and "   你好，朋友。" in _lo and "——莱纳斯" in _lo))
    res.append(ok("📧 游戏自己的换行符 `^` 渲染成真换行（不是一串 `^` 丢给 AI）",
                  "^" not in _lo))
    res.append(ok("📧 信件那一刻给「关掉界面」（`_menu_exit_of` 没排除它）", "关掉界面" in _lo))
    # ⚠️ 内容既然进了抬头，提示行就不该再叫 AI 去 `menu read`（同 dialoguebox 那次的理由）
    res.append(ok("📧 提示行不再指 `menu read 看内容`（内容就在抬头）",
                  "menu read 看内容" not in _lo))
    res.append(ok("📧 信件那一档**确实打了** `/menu`（`/state` 里没这些字段）",
                  bool([c for c in CALLS if c[1] == "/menu"])))

    # ⑱ 📥 「存…」——**开着的容器**那一侧（2026-10-01 · P-menus 第五刀）。
    #    恒定的形（设计稿 §10.2 那张表）：`ItemGrabMenu` 开着时单子 = 「箱内容摊成行（取/存）」。
    #    ⚠️ 坐标**不猜**：C# 新报的 `containerAt`（"这个界面属于哪一格容器"）→ 拿坐标回
    #       `/scan_chests` 那张表里认容器（`_menu_box_at` → `_box`）；认不出 ⇒ **整行不出现**。
    _at13 = {"x": 13, "y": 13}          # = CHESTS[0]「矿石箱」（freeSlots 35）
    _stub(menu="ItemGrabMenu", menu_raw=dict(MENU_BOX, containerAt=_at13))
    _s0 = M.intent(ops="show", kw={"n": 40})
    res.append(ok("📥 容器开着 ⇒ 顶层多一行「存…」", "存…" in _s0))
    res.append(ok("📥 排在「箱子里…」后面（取在前、存在后，且压着「关掉界面」）",
                  _s0.index("箱子里…") < _s0.index("存…") < _s0.index("关掉界面")))
    _sn = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "存"), None)
    _sl = M.intent(ops="do", kw={"code": str(_sn)})
    res.append(ok("📥 点开 = 背包里**能存进去**的那几样（工具不进候选）",
                  "古书" in _sl and "草莓" in _sl and "锄头" not in _sl))
    res.append(ok("📥 点开那层的形状跟**箱子关着**时同款（多选 + 号=数量）",
                  "可以多选" in _sl))
    _s1 = M.intent(ops="do", kw={"code": "1"})
    res.append(ok("📥 选完进**数量层**（同一套：号=数量）", "各多少" in _s1))
    _s2 = M.intent(ops="do", kw={"code": "1=1"})
    _st = [c for c in CALLS if c[0] == "POST" and c[1] == "/store"]
    # ⚠️⚠️ 这一条钉的是**真机 A/B 照出来的差一位**：单子印的是 1-based 位次（`idx`），
    #    而 `/store` 的 `slot` 是**游戏那把 0-based 尺子**。以前发 `idx` ⇒ `stored:[]`
    #    （"按了就成"的行静默不干活）。古书在 `slotIndex=2` ⇒ 发的必须是 **2**（idx=3）。
    res.append(ok("📥 发的是 **`idx-1`**（位次 3 → 格号 2）——差一位就是静默不干活",
                  bool(_st) and _st[-1][2].get("slot") == 2, _st[-1][2] if _st else None))
    res.append(ok("📥 坐标取的是**那只容器**（`/store` 只认坐标）",
                  bool(_st) and (_st[-1][2].get("x"), _st[-1][2].get("y")) == (13, 13)))
    res.append(ok("📥 回执如实报存了什么", "✅" in _s2 and "古书" in _s2))
    # ⚠️ 三档"算不出来就别给"（恒最恨的那类：行出现了、按下去不成）
    _stub(menu="ItemGrabMenu", menu_raw=MENU_BOX)       # 老 DLL：没有 containerAt
    res.append(ok("📥 没有 `containerAt`（老 DLL）⇒ **不给**「存…」",
                  "存…" not in M.intent(ops="show", kw={"n": 40})))
    _stub(menu="ItemGrabMenu", menu_raw=dict(MENU_BOX, containerAt={"x": 99, "y": 99}))
    res.append(ok("📥 坐标对不上本图任何容器 ⇒ **不给**（宁缺勿猜一只箱子往里放）",
                  "存…" not in M.intent(ops="show", kw={"n": 40})))
    _stub(menu="ItemGrabMenu", menu_raw=dict(MENU_BOX, containerAt=_at13), inv=[])
    res.append(ok("📥 背包里没东西能存 ⇒ **不给**「存…」（那一刻只有「箱子里…」）",
                  "存…" not in M.intent(ops="show", kw={"n": 40})))
    _stub(menu="ItemGrabMenu", menu_raw=dict(MENU_BOX, items=[], containerAt=_at13))
    _se = M.intent(ops="show", kw={"n": 40})
    res.append(ok("📥 **空箱子**照样给「存…」（开一只空箱正是要塞东西那一刻）",
                  "存…" in _se and "箱子里" not in _se))
    _stub(menu="ItemGrabMenu", menu_raw=dict(MENU_BOX, containerAt={"x": 14, "y": 14}),
          chests=[dict(CHESTS[0], x=14, y=14, freeSlots=0, used=36, items=[])])
    res.append(ok("📥 容器满了 ⇒ **不给**「存…」（出路是同屏的「箱子里…」）",
                  "存…" not in M.intent(ops="show", kw={"n": 40})))
    # 🔌 跨语言契约（**读 C# 源码核**，别靠记）：
    #    ① 键名：C# 序列化的那个键 = Python 读的那个键（名字漂了两边都静默）；
    #    ② `quality` 筛子**只对 `Object` 成立** —— 单子对**每件带 quality 字段的东西**都发
    #       quality（非 Object 的件 `/state` 也报 0）⇒ 两条必须配套。真机上不配套的样子：
    #       「存 水手帽 ⇒ stored:[]」「取 铱金鱼竿 ⇒ taken:0」（行都在、按了不成）。
    res.append(ok("🔌 C# 报的键名 = Python 读的键名（`containerAt`）",
                  "containerAt = BuildContainerAt(menu)" in _src
                  and 'raw.get("containerAt")' in _worn_src))
    res.append(ok("🔌 C# 的 `BuildContainerAt` 有定义 + 两处序列化都调它（/state 与 /menu 共用）",
                  _src.count("BuildContainerAt(") == 3))
    res.append(ok("🔌 `/store`·`/chest_take` 的品质筛子都只对 `Object` 成立（两处，缺一处就有一族假行）",
                  _src.count("item is StardewValley.Object qObj && qObj.Quality != quality") == 2))

    # ⑲ 🪨🔨 砸晶球 / 重铸饰品（2026-10-01 · 恒「锻造晶球先吧」+「铱锭够/不够/没开饰品精通…」）
    #    两条的判据都**问游戏**：`is_geode`（C# `Utility.IsGeode()`）与
    #    `/machine_reqs`（`PlaceInMachine(probe:true)` = **只问不做**）。
    #    ⚠️ 两个模块级缓存（精通 30s / machine_reqs 10min）会被别的用例污染 ⇒ 每段先清干净。
    M._MASTERY_CACHE.update(ts=0.0, claimed=None)
    M._MACHINE_REQS_CACHE.update(ok=None, ts=0.0)

    # ── 🪨 砸晶球：铁匠铺 + 有晶球 + 钱够 ⇒ 顶层一行；别处不给 ──
    _geo = [dict(i) for i in STATE["inventory"]]
    _geo.append({"slotIndex": 9, "name": "Geode", "displayName": "晶球", "itemId": "(O)535",
                 "catNum": -12, "stack": 3, "quality": 0, "sellable": True, "shippable": True,
                 "isGeode": True})
    _geo.append({"slotIndex": 10, "name": "Stone", "displayName": "石头", "itemId": "(O)390",
                 "catNum": -15, "stack": 99, "quality": 0, "sellable": True, "shippable": True,
                 "isGeode": False})
    _stub(inv=_geo, loc="Blacksmith")
    _go = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🪨 铁匠铺 + 背包有晶球 ⇒ 给「砸 晶球…」", "砸 晶球" in _go))
    res.append(ok("🪨 计数只数**晶球**（石头不算）", "3 颗" in _go))
    res.append(ok("🪨 理由栏写清单价（25g/颗）", "25g/颗" in _go))
    _gn = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "砸 晶球"), None)
    _gl = M.intent(ops="do", kw={"code": str(_gn)})
    res.append(ok("🪨 点开 = **一件事**（不列「砸哪一颗」——那条 op 不认种类，列了就是假承诺）",
                  "砸 晶球" in _gl))
    _g1 = M.intent(ops="do", kw={"code": "1"})
    res.append(ok("🪨 再进数量层（号=数量）", "各多少" in _g1))
    _rc0 = M._require_counter
    M._require_counter = lambda *a, **k: "（桩：已在柜台前）"   # 走路要真游戏 ⇒ 打桩
    # ⚠️ `process_geodes` 走的是 `api._get`（**不是 `_ai_get`**）⇒ 那个也得打桩，
    #    否则自验会**真打到正在跑的游戏**（2026-10-01 现场：真机回了一句
    #    「No geodes in inventory」——自验绝不许碰游戏）。
    _get0 = api._get
    api._get = lambda ep, params=None: _LAST_G(ep, params)
    _g2 = M.intent(ops="do", kw={"code": "1=3"})
    api._get = _get0
    M._require_counter = _rc0
    res.append(ok("🪨 执行走 `menu geode`（回执把它的话带回来）", "✅" in _g2 and "钻石" in _g2))
    # 🚫 换到别的图 ⇒ 不给（同「投出货箱」的写法：只在自己那张图给，别劝它跑腿）
    _stub(inv=_geo, loc="FarmHouse")
    res.append(ok("🚫 不在铁匠铺 ⇒ **不给**「砸晶球…」",
                  "砸晶球" not in M.intent(ops="show", kw={"n": 40})))
    # 🚫 老 DLL（没有 `isGeode` 这一位）⇒ 判不出 ⇒ 不给
    _stub(inv=[dict(i, isGeode=None) for i in _geo], loc="Blacksmith")
    res.append(ok("🚫 老 DLL 不吐 `isGeode` ⇒ **不给**（不猜「哪些是晶球」）",
                  "砸晶球" not in M.intent(ops="show", kw={"n": 40})))

    # ── 🔨 重铸饰品：三条状态 ──
    # ⚠️ 形状照**真机**抄（2026-10-01 逮到的那个 bug）：饰品在 `/state` 里是 `catNum: 0`
    #    —— **不是 -101**。以前这条假数据写着 -101，于是"按分类号筛饰品"一路全绿，
    #    可真机上**一件饰品都筛不出来** ⇒ 「重铸饰品」那行永远不出现。
    _tr = dict(STATE["inventory"][0])
    _tr.update({"slotIndex": 9, "name": "FairyBox", "displayName": "仙女盒",
                "itemId": "(TR)FairyBox", "catNum": 0, "isTrinket": True,
                "canReforge": True, "stack": 1,
                "sellable": False, "shippable": False})
    # 🐾 唯一不能重铸的那两颗之一（真机 + wiki + `Object.cs:2235` 三处同源）。
    #    ⚠️ 它的 `canReforge=False` **探针答不出来**（真机实测 `canPlace=true`）⇒ 只能靠这一位筛。
    _paw = dict(_tr, slotIndex=12, name="BasiliskPaw", displayName="蜥怪的爪子",
                itemId="(TR)BasiliskPaw", canReforge=False)
    # 背包里放 3 块铱锭：回执要报"铱锭 3 → 3"（**回读**那一步得拿得到数）
    _bar = {"slotIndex": 11, "name": "Iridium Bar", "displayName": "铱锭",
            "itemId": "(O)337", "catNum": -15, "stack": 3, "quality": 0,
            "sellable": True, "shippable": True}
    _bag = [_tr, _bar]
    _anvil = {"type": "Anvil", "typeDisplay": "铁砧", "x": 25, "y": 23,
              "location": "FarmHouse", "location_unique": "FarmHouse", "status": "empty"}
    _cb = {"skill": "combat", "claimed": True}
    _reqs = {"id": "(O)337", "name": "铱锭", "need": 3, "have": 3, "enough": True}

    def _mreq(can=True, met=True, have=3):
        """装一次 `/machine_reqs` 的回包（形照 C# 新端点）。"""
        _p0 = api._ai_post
        api._ai_post = lambda ep, data=None: (
            {"ok": True, "count": 1, "machines": [
                {"type": "Anvil", "x": 25, "y": 23, "empty": True, "canPlace": can,
                 "requirementsMet": met,
                 "requirements": [dict(_reqs, have=have, enough=(have >= 3))]}]}
            if ep == "/machine_reqs" else _p0(ep, data))

    # ① 铱锭够 ⇒ 给那一行
    _stub(inv=_bag, machines=[_anvil], mastery=[_cb])
    _mreq()
    _ro = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🔨 战斗精通已领 + 铁砧空着 + 饰品能重铸 + 铱锭够 ⇒ 给「重铸饰品…」",
                  "重铸饰品" in _ro))
    res.append(ok("🔨 理由栏是**游戏给的那两个数**（要 3 铱锭 / 有 3）",
                  "要 3 铱锭（有 3）" in _ro))
    _rn = next((r.no for r in M.intent_menu._LAST_ROWS if (r.label or "") == "重铸饰品"), None)
    _rl = M.intent(ops="do", kw={"code": str(_rn)})
    res.append(ok("🔨 点开 = 那件饰品（敲了当场开炉，没有数量层）", "仙女盒" in _rl and "属性会重掷" in _rl))
    _rc1 = M._require_counter
    _wk0 = M.navigation.walk_to
    M.navigation.walk_to = lambda **k: None
    # 交互之后铁砧要**变成 processing**（C# 的 `/machines` 会这么回）——
    # 这就是 `_im_reforge` 回读的那一步；不翻这个旗，回读会说"还是空的"。
    _flip_anvil = {"on": False}
    _g_ok = api._ai_get
    _p_ok = api._ai_post
    api._ai_get = lambda ep, params=None: (
        {"machines": [dict(_anvil, status="processing" if _flip_anvil["on"] else "empty",
                           heldItem="FairyBox" if _flip_anvil["on"] else None, minutesLeft=10)]}
        if ep == "/machines" else _g_ok(ep, params))
    api._ai_post = lambda ep, data=None: (
        (_flip_anvil.update(on=True), _p_ok(ep, data))[1] if ep == "/interact" else _p_ok(ep, data))
    _rr = M.intent(ops="do", kw={"code": "1"})
    api._ai_get, api._ai_post = _g_ok, _p_ok
    M.navigation.walk_to = _wk0
    _MREQ_CALLS = [c for c in CALLS if c[1] == "/select"]
    res.append(ok("🔨 执行先**拿在手上**（`/select` 打 AI 自己那端）", bool(_MREQ_CALLS),
                  _MREQ_CALLS[-1][2] if _MREQ_CALLS else None))
    res.append(ok("🔨 回执**回读**了铁砧（说清进去了 + 扣了几块）", "✅" in _rr and "铱锭 3 → 3" in _rr,
                  (_rr.splitlines() or [""])[0]))
    # ② 铱锭不够 ⇒ **不给那一行**，但抬头要如实说（"按了不成"的不许上单子）
    _stub(inv=_bag, machines=[_anvil], mastery=[_cb])
    _mreq(can=False, met=False, have=0)
    _r2 = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🔨 铱锭不够 ⇒ **不给**「重铸饰品…」", "重铸饰品" not in _r2))
    res.append(ok("🔨 但抬头**如实说缺口**（要 3 有 0 还差 3 —— 单子上没有它，得有别处说）",
                  "还差 3" in _r2 and "重铸不了" in _r2))
    # ⚠️ 真机逮到的第二个 bug：那句缺口语的**后半截忘了 `f`**，`{rf.get('item')}` 原样漏给了 AI。
    #    自验原来只查"还差 3"和"重铸不了"，正好一个字都没碰到漏出去的那半截 ⇒ 加这条。
    _hint = next((ln for ln in _r2.splitlines() if "重铸不了" in ln), "")
    res.append(ok("🔨 缺口语里**带上那件饰品的名字**、且**不漏模板占位符**（`{rf.get(...)}` 那种）",
                  "仙女盒" in _hint and "rf.get" not in _hint and "{" not in _hint))
    # ③ 没开战斗精通 ⇒ 结构上就不给（没有铁砧）
    _stub(inv=_bag, machines=[_anvil], mastery=[{"skill": "farming", "claimed": True}])
    _mreq()
    res.append(ok("🔨 没领战斗精通 ⇒ **不给**（铁砧/饰品槽都是它解锁的）",
                  "重铸饰品" not in M.intent(ops="show", kw={"n": 40})))
    # ④ **假想**场景：探针说"这台机器不收这件"（`canPlace=false`）而铱锭够 ⇒ 也不给。
    #    ⚠️ 标注"假想"是认真的：真机上**探针答不了"能不能重铸"**（反编译 `Object.cs:2472`
    #    `if (probe) return true;` 在 `OutputMachine` 之前就返回）⇒ 这一格游戏未必给得出。
    #    留着是因为它对"探针说 no"这种回包仍然必须不给（防线不嫌多），但**别把它当主判据**。
    _stub(inv=_bag, machines=[_anvil], mastery=[_cb])
    _mreq(can=False, met=True, have=3)
    res.append(ok("🔨 （假想）万一探针说这机器不收这件（`canPlace=false`）⇒ 也不给",
                  "重铸饰品" not in M.intent(ops="show", kw={"n": 40})))
    # ④a **真机逮到的第二个 bug**：唯一不能重铸的两颗之一（蜥怪的爪子）**不许上单子**。
    #     判据只能是 C# 的 `canReforge`（= `Object.OutputAnvil` 那道门）——
    #     探针在这颗上也回 `canPlace=true`（真机 + 反编译双证）。
    _stub(inv=[_paw, _bar], machines=[_anvil], mastery=[_cb])
    _mreq()
    res.append(ok("🔨 蜥怪的爪子（`canReforge=false`）**不许上单子**（探针在这颗上回 true，靠不住）",
                  "重铸饰品" not in M.intent(ops="show", kw={"n": 40}))
               and ok("🔨 只有它一颗时 ⇒ 这一行整体不出现（不是「列出来让你白按」）",
                      "重铸饰品" not in M.intent(ops="show", kw={"n": 40})))
    # ④b **真机形状**：饰品 `catNum=0`（不是 -101）也照样认得出来 —— 判据是 C# 的 `isTrinket`
    res.append(ok("🔨 饰品在真机 `/state` 里是 `catNum: 0` ⇒ 靠 `isTrinket` 照样认出来"
                  "（照 -101 筛会一件都筛不出来）",
                  M._bag_trinkets({"inventory": [_tr]}) == [_tr]
                  and M._bag_trinkets({"inventory": [dict(_tr, catNum=-101, isTrinket=False)]}) == []))
    # ④c **老 DLL**（没有 `isTrinket`/`canReforge` 这两位）⇒ 退到游戏自己的类型标签 `(TR)`
    _old_tr = {k: v for k, v in _tr.items() if k not in ("isTrinket", "canReforge")}
    _stub(inv=[_old_tr, _bar], machines=[_anvil], mastery=[_cb])
    _mreq()
    res.append(ok("🔨 老 DLL 没 `isTrinket`/`canReforge` ⇒ 退到 `(TR)` 类型标签选候选"
                  "（判据是游戏的 `itemId`，不是手抄名单；那一层洞随下次 DLL 加载消失）",
                  "重铸饰品" in M.intent(ops="show", kw={"n": 40})))
    # ⑤ 老 DLL（没有 `/machine_reqs`）⇒ 判不出 ⇒ 不给
    _stub(inv=_bag, machines=[_anvil], mastery=[_cb])
    api._ai_post = lambda ep, data=None: (_ for _ in ()).throw(RuntimeError("404")) \
        if ep == "/machine_reqs" else {"ok": True}
    res.append(ok("🔨 老 DLL 没有 `/machine_reqs` ⇒ **不给**（不猜「要几块铱锭」）",
                  "重铸饰品" not in M.intent(ops="show", kw={"n": 40})))
    M._MACHINE_REQS_CACHE.update(ok=None, ts=0.0)
    M._MASTERY_CACHE.update(ts=0.0, claimed=None)
    # 🔌 跨语言契约：C# 那边**真的**有这两样（读源码核，别靠记）
    res.append(ok("🔌 C# 有 `/machine_reqs` 路由 + 处理器（Python 依赖它）",
                  '"/machine_reqs" => HandleMachineReqs(ctx)' in _src
                  and "private object HandleMachineReqs(" in _src))
    res.append(ok("🔌 C# 的 `/state` 吐 `isGeode`（`Utility.IsGeode`，不是手抄名单）",
                  '["isGeode"] = StardewValley.Utility.IsGeode(i)' in _src
                  and "geodeIds" not in _src))
    res.append(ok("🔌 C# 的 `/state` 吐 `isTrinket`（Python 靠它挑饰品候选；分类号对饰品是 0）",
                  '["isTrinket"] = IsTrinket(i)' in _src))
    res.append(ok("🔌 C# 的 `/state` 吐 `canReforge`（= `Object.OutputAnvil` 那道门；探针答不了它）",
                  '["canReforge"] = CanReforgeTrinket(i)' in _src
                  and "GetTrinketData()?.CanBeReforged" in _src))

    # ── 🧺🔁 收放（2026-10-01 恒拍板 (b)：单子那条改成"挑料 → 拟人收放"）──
    #    判据全在服务器 `_im_mwork`：ready/empty 来自 `/machines` 的 status，
    #    **"可放什么"来自游戏探针**（`/machine_reqs` 的 `canPlace`）。
    _mst = {"location": {"uniqueName": "FarmHouse"},
            "inventory": [
                {"name": "Jade", "displayName": "翡翠", "stack": 17, "catNum": -2},
                {"name": "Hoe", "displayName": "锄头", "stack": 1, "catNum": -99},
                {"name": "BasiliskPaw", "displayName": "蜥怪的爪子", "stack": 1,
                 "catNum": 0, "isTrinket": True},
            ]}
    _mms = [{"type": "Crystalarium", "typeDisplay": "宝石复制机", "status": "ready",
             "heldItemDisplay": "翡翠"},
            {"type": "Crystalarium", "typeDisplay": "宝石复制机", "status": "empty"},
            {"type": "Keg", "typeDisplay": "小桶", "status": "empty"}]
    _p_reqs, _asked = M._machine_reqs, []

    def _fake_reqs(type_="", item=""):
        _asked.append(item)
        if item == "Jade":
            # ⚠️ 形照 **`/machine_reqs` 的真实回包**：它给的是 `empty`（bool），**没有 `status`**
            #    （`status` 是 `/machines` 的字段）—— 第一版夹具写了 `status: empty`，
            #    于是自验绿、真机上那行不出现（真机当场逮到）。
            return {"ok": True, "machines": [
                {"type": "Crystalarium", "typeDisplay": "宝石复制机", "empty": True,
                 "canPlace": True},
                {"type": "Keg", "typeDisplay": "小桶", "empty": True, "canPlace": False}]}
        return {"ok": True, "machines": [{"type": "Keg", "empty": True, "canPlace": False}]}

    M._machine_reqs = _fake_reqs
    _d = M._im_mwork(_mst, _mms)
    res.append(ok("🧺 收机器：本图「好了几台 / 空着几台」数对得上（判据= `/machines` 的 status）",
                  _d.get("ready") == 1 and _d.get("empty") == 2, _d))
    res.append(ok("🧺 产物按名字摊开（`翡翠×1`）", _d.get("products") == {"翡翠": 1}, _d.get("products")))
    res.append(ok("🧺 **一次探针都不发**（放料撤出单子后，`/machine_reqs` 那套整个下线）",
                  _asked == [] and "loadable" not in _d, _asked))
    # 什么都没得做 ⇒ `{}` ⇒ 那行不出现
    res.append(ok("🧺 本图没机器 ⇒ `{}`（单子那行不出现）",
                  M._im_mwork(_mst, []) == {}))
    res.append(ok("🧺 有机器但没一台好了 ⇒ 只报空着几台（`ready=0` ⇒ 那行不给）",
                  M._im_mwork(_mst, [_mms[1]]) == {"ready": 0, "empty": 1, "products": {}}))
    M._machine_reqs = _p_reqs
    # 🔌 单子↔服务器 的合同：单子那行**不带参数**（item/machine_type 都空 = 只收），
    #    服务器调到 `load_machines(..., here=True)`。
    #    ⚠️ `--here` 不是可选项：`/farm_report` 不含房主 FarmHouse ⇒ 传名字会扫到 0 台
    #       （2026-09-27 真机空跑两千多次的账）。
    _lm0, _lm_calls = M.load_machines, []
    M.load_machines = lambda **kw: (_lm_calls.append(kw), "🚀 已后台启动 job 7")[1]
    M._im_run("mwork", {"item": "", "machine_type": "", "location": "FarmHouse"})
    M.load_machines = _lm0
    res.append(ok("🔌 单子那行 **只收**（item/machine_type 皆空）⇒ 服务器调 "
                  "`load_machines(item='', machine_type='', here=True)`"
                  "（**漏了 `here` 就是 09-27 那个 0 台**）",
                  bool(_lm_calls) and _lm_calls[-1].get("item") == ""
                  and _lm_calls[-1].get("machine_type") == ""
                  and _lm_calls[-1].get("here") is True, _lm_calls))
    _lm_calls.clear()
    M.load_machines = lambda **kw: (_lm_calls.append(kw), "🚀 已后台启动 job 7")[1]
    M._im_run("mwork", {"item": "Starfruit", "machine_type": "Keg", "location": "Big Shed"})
    M.load_machines = _lm0
    res.append(ok("🔌 放料那条（AI 自己调的 `farm load`）参数也照传（item + machine_type 一起）"
                  "—— 只传 item 会撒进所有收得下它的机器、还会去点缝纫机",
                  bool(_lm_calls) and _lm_calls[-1].get("item") == "Starfruit"
                  and _lm_calls[-1].get("machine_type") == "Keg", _lm_calls))
    # 📥📤 存 / 取：**必须先走过去**（恒 2026-10-01：「当时说先接 store，**没把前面的跑过去箱子
    #    接进来**。那就补吧」）—— C# 那两条都是**不校验距离**的原子直操，不走位 = 隔空动箱子。
    _walk0, _post0 = M._walk_to_chest, api._ai_post
    _seq = []
    M._walk_to_chest = lambda x, y: (_seq.append(("walk", x, y)), "  🚶 已走到箱边")[1]
    api._ai_post = lambda ep, data=None: (
        _seq.append(("post", ep)), {"ok": True, "stored": [{"count": 3}]})[1]
    _rs = M._im_run("store", {"x": 58, "y": 16, "name": "Purple Mushroom", "count": 3})
    res.append(ok("📥 「存」**先走过去再存**（顺序：walk → post /store）",
                  _seq and _seq[0][0] == "walk" and _seq[-1] == ("post", "/store")
                  and _rs.get("ok") is True, _seq))
    _seq.clear()
    api._ai_post = lambda ep, data=None: (
        _seq.append(("post", ep)), {"ok": True, "taken": 2})[1]
    M._im_run("chest_take", {"x": 58, "y": 16, "name": "Purple Mushroom", "count": 2})
    res.append(ok("📤 「取」也**先走过去再取**（同一条拟人路）",
                  _seq and _seq[0][0] == "walk" and _seq[-1] == ("post", "/chest_take"), _seq))
    _seq.clear()
    _rn = M._im_run("store", {"name": "Purple Mushroom"})      # 没 x/y ⇒ 不猜、直接报错
    res.append(ok("📥 缺 x/y ⇒ 明说「缺 x/y」，**不发请求也不瞎走**",
                  _rn.get("ok") is False and "缺 x/y" in str(_rn.get("error")) and _seq == [],
                  _rn))
    M._walk_to_chest, api._ai_post = _walk0, _post0
    # 🧵 类型门：**传类型 = 只伺候那类**；不传 = 缝纫机也在名单里（真机就是它去点了）
    import machine_loader as ML
    _ms0 = ML.api.machines
    ML.api.machines = lambda: {"machines": [
        {"x": 1, "y": 1, "type": "Crystalarium", "status": "empty"},
        {"x": 2, "y": 2, "type": "SewingMachine", "status": "empty"},
        {"x": 3, "y": 3, "type": "SewingMachine", "status": "ready"}]}
    _tg, _ = ML.get_serviceable_machines("Crystalarium", with_items=["Jade"], here=True)
    _tg2, _ = ML.get_serviceable_machines("", with_items=["Jade"], here=True)
    ML.api.machines = _ms0
    _k1 = sorted((m["x"], m["y"]) for m in _tg)
    _k2 = sorted((m["x"], m["y"]) for m in _tg2)
    res.append(ok("🧵 传了 `machine_type` ⇒ **只伺候那一类**（缝纫机不进名单）",
                  _k1 == [(1, 1)], _k1))
    res.append(ok("🧵 不传类型 ⇒ 本图**所有**空机器都在名单里（缝纫机 (2,2) 就是真机被点的那台）"
                  "—— 所以单子那一层**必须发类型**",
                  _k2 == [(1, 1), (2, 2), (3, 3)], _k2))
    _mm_src = open(os.path.join(_here, "intent_menu.py"), encoding="utf-8").read()
    res.append(ok("🔌 单子那侧真的打的是 `mwork`，且**旧的一键收执行器已下线**"
                  "（找 `_exec_collect`/`_collect_can` —— 注释里提 `machine_collect` 不算，"
                  "那正是记账用的）",
                  'run("mwork"' in _mm_src and "MACHINE_V = Verb(" in _mm_src
                  and "_exec_collect" not in _mm_src and "_collect_can" not in _mm_src))

    # ⑪ 🚪🐄 放牧（开棚门）/ 关棚门（2026-10-01 恒：「放牧（开关畜棚鸡舍门）做进选项了吗？」）
    #
    # 这一节钉四件事：
    #   ① `_im_doors` 把"能不能放牧"算对（建筑数**不手抄名单**、天气/季节从 `/state.time` 读）；
    #   ② 两行**靠钟点互斥**（06:00/15:00/17:00 三个边界 + 16:00 那一小时两行都不给）；
    #   ③ 雨天/冬天/不在农场/`doors={}` ⇒ **一行都不给**（"算不出来 ⇒ 不出现"，不许兜底）；
    #   ④ 敲下去真的走 `opendoors`/`doors`（`_im_run` 认这两个名字），**不是**去打不存在的 C# 端点。
    def _labels():
        return [(r.label or "") for r in M.intent_menu._LAST_ROWS]

    def _no_of(sub):
        for r in M.intent_menu._LAST_ROWS:
            if sub in (r.label or ""):
                return r.no
        return None

    def _show(**kw):
        _stub(**kw)
        M.intent(ops="show", kw={"n": 40})
        return _labels()

    # ① 账算得对：两栋动物建筑 + 温室（**温室不算**）+ 晴/夏 ⇒ `{"builds": 2, "rain": False, "winter": False}`
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0, "dayOfMonth": 3})
    _d = M._im_ctx().doors
    res.append(ok("🚪🐄 `_im_doors`：`type` 含 Coop/Barn 的才算（温室**不数**）",
                  _d.get("builds") == 2 and _d.get("rain") is False
                  and _d.get("winter") is False, _d))
    # 天气码照状态条那张表：1雨 / 2雷暴 / 7绿雨 都算"不能放牧"
    _rain_ok = []
    for _w in (0, 1, 2, 3, 5, 7):
        _stub(loc="Farm", farm_buildings=FARM_BUILDINGS,
              time_dict={"timeOfDay": 800, "season": "summer", "weather": _w})
        _rain_ok.append(M._im_ctx().doors.get("rain"))
    res.append(ok("🚪🐄 天气码：0晴/3风/5雪 ⇒ 不算雨；1雨/2雷暴/7绿雨 ⇒ 算雨",
                  _rain_ok == [False, True, True, False, False, True], _rain_ok))
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS,
          time_dict={"timeOfDay": 800, "season": "winter", "weather": 0})
    res.append(ok("🚪🐄 冬天认得出来（`season` 从 `/state.time` 那份**字典**读）",
                  M._im_ctx().doors.get("winter") is True))
    # 读不到 ⇒ `{}`：不在农场（**连 `/farm_buildings` 都不打**）、没季节、`/state.time` 不是字典
    _stub(loc="FarmHouse", farm_buildings=FARM_BUILDINGS,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    res.append(ok("🚪🐄 不在农场 ⇒ `{}`（那两行本来就不会出现）", M._im_ctx().doors == {}))
    res.append(ok("🚪🐄 不在农场时**一次都不多打** `/farm_buildings`",
                  not any(c[1] == "/farm_buildings" for c in CALLS)))
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, time_dict={"timeOfDay": 800, "weather": 0})
    res.append(ok("🚪🐄 `season` 读不到 ⇒ `{}`（**不拿默认值兜底**）", M._im_ctx().doors == {}))
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, time_dict="13:20")   # 老形状：字符串
    res.append(ok("🚪🐄 `/state.time` 不是字典（老形状/缺席）⇒ `{}`，**不当成「没下雨」**",
                  M._im_ctx().doors == {}))
    _stub(loc="Farm", farm_buildings=[], time_dict={"timeOfDay": 800, "season": "summer",
                                                    "weather": 0})
    res.append(ok("🚪🐄 农场上一个动物建筑都没有 ⇒ `builds=0`（**问清了**，不是「读不到」）",
                  M._im_ctx().doors.get("builds") == 0))

    # ② 钟点边界：06:00 给开、15:00 给开（含两端）、17:00 给关、16:00 **两行都不给**
    def _rows_at(tod, **kw):
        return _show(loc="Farm", farm_buildings=FARM_BUILDINGS,
                     time_dict=dict({"timeOfDay": tod, "season": "summer", "weather": 0,
                                     "dayOfMonth": 3}, **kw))

    res.append(ok("🚪 06:00 ⇒ 给「放牧（开棚门）」", "放牧（开棚门）" in _rows_at(600)))
    res.append(ok("🚪 15:00 ⇒ 还给「放牧（开棚门）」", "放牧（开棚门）" in _rows_at(1500)))
    res.append(ok("🚪 16:00 ⇒ **两行都不给**（既不太晚开门、也没到关门点）",
                  "放牧（开棚门）" not in _rows_at(1600) and "关棚门" not in _rows_at(1600)))
    res.append(ok("🚪 17:00 ⇒ 给「关棚门」", "关棚门" in _rows_at(1700)))
    res.append(ok("🚪 05:00 ⇒ 给「关棚门」（<06:00 那一档）", "关棚门" in _rows_at(500)))
    res.append(ok("🚪 两行**互斥**：同一屏里不会既有「放牧」又有「关棚门」",
                  all(not ("放牧（开棚门）" in _l and "关棚门" in _l)
                      for _l in (_rows_at(600), _rows_at(1200), _rows_at(1600),
                                 _rows_at(1700), _rows_at(500)))))
    # ③ 四个"不给"闸门（开/关两行**都**不许出现）
    for _kw, _why in ((dict(loc="FarmHouse"), "不在农场"),
                      (dict(loc="Farm", time_dict={"timeOfDay": 800, "season": "summer",
                                                   "weather": 1}, farm_buildings=FARM_BUILDINGS),
                       "雨天"),
                      (dict(loc="Farm", time_dict={"timeOfDay": 800, "season": "winter",
                                                   "weather": 0}, farm_buildings=FARM_BUILDINGS),
                       "冬天"),
                      (dict(loc="Farm", farm_buildings=FARM_BUILDINGS),
                       "`doors` 算不出来（`/state.time` 读不到）")):
        _l8 = _show(**_kw)
        res.append(ok(f"🚪 {_why} ⇒ **一行都不给**",
                      "放牧（开棚门）" not in _l8 and "关棚门" not in _l8, _l8[:4]))
    # 冬天/雨天的**晚上**照样给「关棚门」（"雨天不开门"跟"晚上要关门"是两件事）
    _lw = _show(loc="Farm", farm_buildings=FARM_BUILDINGS,
                time_dict={"timeOfDay": 1900, "season": "winter", "weather": 1})
    res.append(ok("🚪 冬天/雨天的**晚上**照样给「关棚门」（不开门 ≠ 不关门）",
                  "关棚门" in _lw and "放牧（开棚门）" not in _lw, _lw[:4]))

    # ④ 敲下去真走**那一个翻转 op**（`doors`）：`_im_run` 认它、别掉进裸端点兜底；
    #    两行的**方向**由 exec 看回执里的门态收敛（最多再翻一次）。
    def _doors_hit(tod, sub, doors_open, chore_animals=None):
        """敲那一行 → (回执, 打了几次 `/toggle_doors`, 那几次的 (方法, 端点), 全部端点)。不出网。

        `chore_animals` = 站在农场时 `/animals` 报的那批（= 棚外那批）——
        「关棚门」那行现在会先看它（外面有动物就不关），所以要能按用例指定。
        """
        _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, doors_open=doors_open,
              chore_animals=chore_animals,
              time_dict={"timeOfDay": tod, "season": "summer", "weather": 0})
        M.intent(ops="show", kw={"n": 40})
        _rc = M.intent(ops="do", kw={"code": str(_no_of(sub))})
        _td = [(c[0], c[1]) for c in CALLS if c[1] == "/toggle_doors"]
        return _rc, len(_td), _td, [c[1] for c in CALLS]

    # 门关着 ⇒ 翻一下就该全开（**一次收敛**）
    _r_open, _n_open, _td_open, _eps_open = _doors_hit(800, "放牧（开棚门）", doors_open=False)
    res.append(ok("🚪 敲「放牧」⇒ 真打 `/toggle_doors`（**POST**；`/opendoors` 这个端点不存在）",
                  _n_open == 1 and _td_open[0] == ("POST", "/toggle_doors")
                  and not any("/opendoors" in str(e) for e in _eps_open), _td_open))
    res.append(ok("🚪 敲「放牧」⇒ 回执**逐栋报执行后的门态** + 目标态（『门现在是：…开』）",
                  "门现在是：Deluxe Coop 开" in _r_open and "目标=全开" in _r_open,
                  _r_open[:200]))
    res.append(ok("🚪 回执**不许**再提「farm animals 摸一遍」（恒：关着门也能摸）",
                  "animals" not in _r_open, _r_open[:200]))
    res.append(ok("🚪 回执带**下一步**（反着来敲哪一下，op+参数都在）",
                  'farm(ops="doors")' in _r_open, _r_open[:200]))
    # 门本来就开着 ⇒ C# 纯翻转会把它关上 ⇒ exec 必须**再翻一次**收敛到目标态
    _r_open2, _n_open2, _, _ = _doors_hit(800, "放牧（开棚门）", doors_open=True)
    res.append(ok("🚪 门本来就开着时敲「放牧」⇒ **再翻一次收敛到全开**（C# 是纯翻转）",
                  _n_open2 == 2 and "门现在是：Deluxe Coop 开" in _r_open2
                  and "目标=全开" in _r_open2, (_n_open2, _r_open2[:160])))

    _r_close, _n_close, _, _ = _doors_hit(1900, "关棚门", doors_open=True, chore_animals=[])
    res.append(ok("🚪 敲「关棚门」⇒ 一次收敛，回执报『门现在是：…关』+ 目标=全关",
                  _n_close == 1 and "门现在是：Deluxe Coop 关" in _r_close
                  and "目标=全关" in _r_close, (_n_close, _r_close[:160])))
    _r_close2, _n_close2, _, _ = _doors_hit(1900, "关棚门", doors_open=False, chore_animals=[])
    res.append(ok("🚪 门本来就关着时敲「关棚门」⇒ **再翻一次收敛到全关**",
                  _n_close2 == 2 and "门现在是：Deluxe Coop 关" in _r_close2,
                  (_n_close2, _r_close2[:160])))
    # ⚠️ 收敛第二下**不许再走一遍路**（人已经站在门口了）—— 由 `walk=False` 控
    _doors_hit(800, "放牧（开棚门）", doors_open=True)      # 这一发会翻两次
    res.append(ok("🚪 收敛的第二下**不再走位**（只有第一下走过去；否则白等 15 秒）",
                  len(WALK_CALLS) == 1, WALK_CALLS))

    # ⚠️⚠️ 2026-10-01 真机逮到的洞：**收敛那发会把走位那条事实盖掉** ——
    #    人真走到了门口（`[walk] … 到位`），可 AI 看到的回执里一个字都没提。
    #    「翻完了却不说人到没到门口」两头都是谎 ⇒ 两条用例各钉一头（走到 / 没走到）。
    _r_conv_ok, _n_conv_ok, _, _ = _doors_hit(800, "放牧（开棚门）", doors_open=True)
    res.append(ok("🚪 **收敛后**最终回执里**仍含走位那行**（走到了：带棚名 + 门坐标）",
                  "🚶 已走到" in _r_conv_ok and "Deluxe" in _r_conv_ok
                  and "旁边" in _r_conv_ok, _r_conv_ok[:220]))
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, doors_open=True, walk_ok=False,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    M.intent(ops="show", kw={"n": 40})
    _r_conv_bad = M.intent(ops="do", kw={"code": str(_no_of("放牧（开棚门）"))})
    res.append(ok("🚪 收敛后最终回执里**仍含走位那行**（没走到：明说「遥控翻的，人还在半路」）",
                  "遥控翻的" in _r_conv_bad and "门现在是" in _r_conv_bad,
                  _r_conv_bad[:220]))

    # ⚠️ 门的 op **必须是 `_im_run` 认的那个**（回 dict 带结构化门态）；
    #    不认识就会掉进最后的裸端点兜底（`/{op}`）＝ 打一个**不存在的 C# 端点**。
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, doors_open=False,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    _hits_open = M._im_run("doors", {"walk": True})
    _hits_flip = M._im_run("doors", {"walk": False})
    res.append(ok("🚪 `_im_run` 认 `doors`，且回的是**带 `doors` 门态的 dict**（不是裸端点那个 `{ok:true,…}`）",
                  isinstance(_hits_open, dict) and isinstance(_hits_open.get("doors"), dict)
                  and _hits_open.get("doors") == {"Deluxe Coop": True, "Deluxe Barn": True}
                  and "门现在是" in str(_hits_open.get("text")), _hits_open))
    res.append(ok("🚪 `walk=False` ⇒ **不调走位**（收敛的第二下用它）",
                  isinstance(_hits_flip, dict) and "🚶" not in str(_hits_flip.get("text")),
                  _hits_flip))
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, doors_open=False, walk_ok=False,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    _hf = M._im_run("doors", {"walk": True})
    res.append(ok("🚪 走位没到 ⇒ 回执里明写「遥控翻的，人还在半路」（且门还是照翻、状态照报）",
                  "遥控翻的" in str(_hf.get("text")) and "门现在是" in str(_hf.get("text")),
                  _hf))

    # ⑤ `farm` 域的**别名**：`doors`/`放牧`/`开关门`/`棚门` → **同一个函数**；
    #    ⚠️ **不许**再有 `开门`/`关门`/`开棚门`/`关棚门`/`opendoors` 这种**带方向**的别名
    #       （翻转端点不保证方向，叫「关门」却把门开了就是谎报）。
    # ⚠️ 用**它自己那份提取器**（`_dispatch_keys` 的 AST 口径）去掉"只收 ≥2 字键"那道过滤 ——
    #    `遛` 是单字键，`_dispatch_keys()` 故意不收（收了 `help(随便说点啥)` 会撞出个域来）
    #    ⇒ 想核 `遛` 就得照它再取一次，**别手抄一份快照**（快照会漂）。
    import inspect as _insp, textwrap as _tw
    _ft = None
    for _n in _ast.walk(_ast.parse(_tw.dedent(_insp.getsource(M.farm)))):
        if isinstance(_n, _ast.FunctionDef) and _n.name == "farm":
            for _s in _ast.walk(_n):
                if (isinstance(_s, _ast.Assign) and isinstance(_s.value, _ast.Dict)
                        and any(isinstance(t, _ast.Name) and t.id.startswith("dispatch")
                                for t in _s.targets)):
                    _ft = {k.value: (v.id if isinstance(v, _ast.Name) else "?")
                           for k, v in zip(_s.value.keys, _s.value.values)
                           if isinstance(k, _ast.Constant)}
    res.append(ok("🚪 `farm` 的 `doors`/`放牧`/`开关门`/`棚门` 全指向**同一个** `doors`"
                  "（**放牧不许再挂在摸动物那条上**）",
                  {(_ft or {}).get(k) for k in ("doors", "放牧", "开关门", "棚门")}
                  == {"doors"}, {k: (_ft or {}).get(k)
                                 for k in ("doors", "放牧", "开关门", "棚门")}))
    res.append(ok("🚪 带方向的别名（`开门`/`关门`/`开棚门`/`关棚门`/`opendoors`）**一个都不许留**",
                  not [k for k in ("开门", "关门", "开棚门", "关棚门", "opendoors")
                       if k in (_ft or {})],
                  {k: (_ft or {}).get(k) for k in ("开门", "关门", "开棚门", "关棚门", "opendoors")}))
    res.append(ok("🚪🐄 `petwalk`/`遛` 还是「拟人摸」（`放牧` 从它身上摘掉了）",
                  (_ft or {}).get("petwalk") == "pet_walk"
                  and (_ft or {}).get("遛") == "pet_walk",
                  {k: (_ft or {}).get(k) for k in ("petwalk", "遛")}))
    res.append(ok("🚪🐄 意图索引里有放牧那条，且指向 `farm.放牧`（= 同一个 doors 函数）",
                  any(d == "farm" and o == "放牧" and "放牧" in k
                      for k, d, o, _ in M._INTENT_INDEX)))

    # ⑫ 🌿 六件"顺手活"（2026-10-01 恒「接吧」＝ P1 那批空参行接上单子）
    #     判据**全在服务器**（`_im_chores`）→ 先直接验它，再验单子那 6 行与执行参数。
    _HOE = [{"slotIndex": 4, "name": "Hoe", "displayName": "锄头", "itemId": "(T)Hoe",
             "catNum": -99, "stack": 1, "sellable": False, "shippable": False}]
    _T_BUSH = {"x": 75, "y": 10, "terrain": "Bush", "bushBloom": True}
    # ⚠️ 斑点那一格要摆在**人身边**（`_SPOT_RADIUS` = 8 格）：第一版摆了 (60,18)、人 (12,12)
    #    ⇒ 新加的"只扫周围"当场把它滤掉，两条老用例假红（自验逮到）。
    _T_SPOT = {"x": 14, "y": 14, "objId": "(O)590", "object": "Artifact Spot"}
    _T_MOSS = {"x": 23, "y": 9, "terrain": "Tree:1", "moss": True}
    _PAN = {"hasGlint": True, "x": 33, "y": 36, "hasPan": True, "panUpgrade": 1}
    _MOO = {"animals": [{"name": "牛牛", "type": "White Cow", "x": 11, "y": 14, "productReady": True},
                        {"name": "羊羊", "type": "Goat", "x": 12, "y": 14, "productReady": True},
                        {"name": "毛毛", "type": "Sheep", "x": 13, "y": 14, "productReady": True},
                        {"name": "猪猪", "type": "Pig", "x": 14, "y": 14, "productReady": True},
                        {"name": "空牛", "type": "White Cow", "x": 15, "y": 14, "productReady": False}]}

    def _chores(chore_tiles=None, crab_ready=0, ore_pan=None, animals=None, weather=0,
                hoe=True):
        # ⚠️ 走**真路径** `_im_ctx()`（不是手搓 state/surr 递给 `_im_chores`）——
        #    第一版手搓，`api.has_item`/`api._ai_get("/crab_pots")` 两个口子**没桩到**
        #    ⇒ "没带锄头"那条假红、蟹笼那笔账也拿不到（自验当场逮到）。
        _stub(chore_tiles=chore_tiles, crab_ready=crab_ready, ore_pan=ore_pan,
              chore_animals=(animals if animals is not None else []),
              time_dict={"timeOfDay": 900, "season": "summer", "weather": weather})
        M.api.has_item = lambda n: bool(hoe) and ("Hoe" in str(n))
        return M._im_ctx().chores

    _old_expose = M._moss_cfg.get("expose_all_days")
    M._moss_cfg["expose_all_days"] = False
    _ch = _chores([_T_BUSH, _T_SPOT, _T_MOSS], crab_ready=4, ore_pan=_PAN,
                  animals=_MOO, weather=0, hoe=True)
    res.append(ok("🌿 `_im_chores`：浆果灌木认得出来（`terrain==Bush`+`bushBloom`）",
                  _ch.get("berry") == 1, _ch))
    res.append(ok("🌿 斑点按 **objId**（`(O)590`）认，**不是 `diggable`**（地图属性当判据 = 满地噪音）",
                  _ch.get("spot") == 1, _ch))
    res.append(ok("🌿 苔藓：**非绿雨天 + 没开 expose_all_days ⇒ 不给**（跟 `moss_run` 同一道闸）",
                  "moss" not in _ch, _ch))
    M._moss_cfg["expose_all_days"] = True
    _ch2 = _chores([_T_BUSH, _T_SPOT, _T_MOSS], weather=0, hoe=True)
    res.append(ok("🌿 开了 `settings moss on` ⇒ 苔藓那笔账才给", _ch2.get("moss") == 1, _ch2))
    M._moss_cfg["expose_all_days"] = False
    _ch3 = _chores([_T_BUSH, _T_SPOT, _T_MOSS], weather=7, hoe=True)
    res.append(ok("🌿 绿雨天（weather=7）⇒ 苔藓给", _ch3.get("moss") == 1, _ch3))
    _ch4 = _chores([_T_BUSH, _T_SPOT], crab_ready=0, ore_pan=None, animals=None, hoe=False)
    res.append(ok("🌿 **没带锄头** ⇒ 斑点/姜那笔账不给（`spot_run` 没锄头不挖）",
                  "spot" not in _ch4 and _ch4.get("berry") == 1, _ch4))
    res.append(ok("🦀 蟹笼只数 `readyForHarvest` 的（回包字段）", _ch.get("crab") == 4, _ch))
    res.append(ok("🪙 淘金看 `/state.player.orePan`（`hasGlint`+`hasPan`），坐标一起递",
                  (_ch.get("pan") or {}).get("x") == 33, _ch.get("pan")))
    res.append(ok("🪙 没闪光点 / 没铜锅 ⇒ 不给这笔账",
                  "pan" not in _chores(ore_pan={"hasGlint": False, "hasPan": True})
                  and "pan" not in _chores(ore_pan={"hasGlint": True, "hasPan": False})))
    res.append(ok("🐮🐑 只数 **productReady** 的牛·山羊/绵羊（猪不算、没货的不算）",
                  _ch.get("milk") == 2 and _ch.get("shear") == 1, _ch))
    res.append(ok("🌿 什么都没推出来 ⇒ **空账**（那 6 行全不出现）", _chores() == {}, _chores()))

    # 单子那 6 行：有账就出现、执行**只调现成 op 且不带参数**
    def _labels2(**kw):
        _stub(**kw)
        _out = M.intent(ops="show", kw={"n": 40})
        return [(r.label or "") for r in M.intent_menu._LAST_ROWS], _out

    _L, _Ltxt = _labels2(chore_tiles=[_T_BUSH, _T_SPOT, _T_MOSS], crab_ready=4, ore_pan=_PAN,
                         chore_animals=_MOO["animals"], inv=_HOE,
                         time_dict={"timeOfDay": 900, "season": "summer", "weather": 7})
    for _lab in ("摇 浆果丛", "挖 远古斑点", "刮 苔藓", "收 蟹笼", "淘 金", "挤奶 / 剪毛"):
        res.append(ok(f"🌿 单子上出现「{_lab}」", _lab in _L, (_L, _Ltxt[:200])))
    _L0, _ = _labels2(inv=_HOE, time_dict={"timeOfDay": 900, "season": "summer", "weather": 0})
    res.append(ok("🌿 一件都推不出来 ⇒ **6 行全不出现**（宁缺勿编）",
                  not [x for x in _L0 if x in ("摇 浆果丛", "挖 远古斑点", "刮 苔藓",
                                               "收 蟹笼", "淘 金", "挤奶 / 剪毛")], _L0))
    # 执行：`_im_run` 认这 6 个名字，且**打的是现成 op**（把 op 换成桩看参数）
    _stubs = {}
    for _k, _fn in (("berry", "berry_run"), ("spot", "spot_run"), ("moss", "moss_run"),
                    ("pan", "_pan_run"), ("crab", "_crab_collect"), ("milk", "milk_shear")):
        _stubs[_fn] = getattr(M, _fn)
        setattr(M, _fn, (lambda k=_k: (lambda *a, **kw: f"（桩：{k} 跑了）"))())
    _rr = {k: M._im_run(k, {}) for k in ("berry", "spot", "moss", "pan", "crab", "milk")}
    for _fn, _o in _stubs.items():
        setattr(M, _fn, _o)
    res.append(ok("🔌 `_im_run` 认这 6 个 op（都回一句话，不是裸端点兜底）",
                  all(isinstance(v, dict) and v.get("text") and "桩" in str(v.get("text"))
                      for v in _rr.values()), _rr))
    res.append(ok("🔌 6 个 op **一个参数都不带**（空参行）",
                  all("{" not in str(v.get("text")) for v in _rr.values()), _rr))

    # ⑬ 🚪 「关棚门」动手前先问游戏：**外面还有动物就不关**（恒 2026-10-01）
    #     判据只有一处（`_animals_outside()` → `_doors_close_guard()`），执行侧与傍晚提醒共用。
    _OUT3 = [{"name": "康康", "type": "White Chicken", "x": 73, "y": 12},
             {"name": "你好鸭", "type": "Duck", "x": 76, "y": 16},
             {"name": "安妮", "type": "White Chicken", "x": 74, "y": 17}]

    def _close_click(outside_animals, loc="Farm", **kw):
        """敲一次「关棚门」那一行 → (回执, 打了几次 /toggle_doors)。全打桩。"""
        _stub(loc=loc, farm_buildings=FARM_BUILDINGS, doors_open=True,
              chore_animals=outside_animals,
              time_dict={"timeOfDay": 1900, "season": "summer", "weather": 0}, **kw)
        M.intent(ops="show", kw={"n": 40})
        _rc = M.intent(ops="do", kw={"code": str(_no_of("关棚门"))})
        return _rc, len([c for c in CALLS if c[1] == "/toggle_doors"])
    _rc_out, _n_out = _close_click(_OUT3)
    res.append(ok("🚪 棚外还有动物 ⇒ 敲「关棚门」**一次都不翻**（`/toggle_doors` 零调用）",
                  _n_out == 0, _n_out))
    res.append(ok("🚪 而且**点名**是哪几只在棚外（带坐标）",
                  "康康" in _rc_out and "(73,12)" in _rc_out, _rc_out[:220]))
    res.append(ok("🚪 而且**明说没关门** + 给下一步（先弄回棚）",
                  "没关门" in _rc_out and "animals" in _rc_out, _rc_out[:260]))
    res.append(ok("🚪 被拦下时**一个字都不提门态**（没翻也没读 ⇒ 别说成『没读到门态』）",
                  "门现在是" not in _rc_out and "实际=" not in _rc_out, _rc_out[:200]))
    _rc_in, _n_in = _close_click([])
    res.append(ok("🚪 外面没动物 ⇒ 照常翻（`/toggle_doors` 打了一次）", _n_in == 1, _n_in))
    res.append(ok("🚪 照常翻时回执照旧给「门现在是…」+ 目标/实际",
                  "门现在是" in _rc_in and "目标=全关" in _rc_in, _rc_in[:220]))
    # 判不出来 ⇒ **也不许关**（⚠️ 这里**不能**拿单子那行验：`关棚门` 那行本来就要求站在农场，
    #    人不在农场它根本不在单子上 ⇒ 直接调 op，验"判不出来 ⇒ 不翻 + 如实说原因"）
    _stub(loc="Deluxe Barn", farm_buildings=FARM_BUILDINGS, doors_open=True, chore_animals=_OUT3,
          time_dict={"timeOfDay": 1900, "season": "summer", "weather": 0})
    _ro_unk = M._im_doors_op({"want": "close", "walk": True})
    _n_unk = len([c for c in CALLS if c[1] == "/toggle_doors"])
    res.append(ok("🚪 **判不出来也不许关**（人不在农场时不翻）", _n_unk == 0, _n_unk))
    res.append(ok("🚪 判不出来 ⇒ 如实说原因（不是「外面没有」）",
                  _ro_unk.get("blocked") is True and "没关门" in str(_ro_unk.get("text"))
                  and "不在农场" in str(_ro_unk.get("text")), _ro_unk))

    # ⑭ 193 批：排序 / 砸晶球门禁 / 重铸精通 / 斑点只扫周围
    def _w(key):
        return M.intent_menu._VERB_BY_KEY[key].weight

    res.append(ok("⚖️ 193：`收 成熟作物` 85 → **88**（做完就不播了 ⇒ 往前挪）", _w("harvest") == 88,
                  _w("harvest")))
    res.append(ok("⚖️ 193：`摸 猫狗` 83 → **87**", _w("pet_pets") == 87, _w("pet_pets")))
    res.append(ok("⚖️ 193：`放牧（开棚门）` 70 → **84**（早晨跟摸动物一个档）",
                  _w("opendoors") == 84, _w("opendoors")))
    res.append(ok("⚖️ 193：`收 蟹笼` 68 → **72**（有货时可抬）", _w("crab") == 72, _w("crab")))
    res.append(ok("⚖️ 其余别跟着动（关棚门仍 70 / 浆果 66 / 斑点 64 / 挤奶 62 / 淘金 60 / 苔藓 58）",
                  (_w("doors"), _w("berry"), _w("spot"), _w("milk"), _w("pan"), _w("moss"))
                  == (70, 66, 64, 62, 60, 58)))

    # 🏪 克林特营业中：**现成两份数据**（休息日表 + SHOP_HOURS 前导时段），别编新表
    _stub(time_dict={"timeOfDay": 1000, "season": "summer", "dayOfMonth": 6, "weather": 0})
    res.append(ok("🏪 周六 10:00（不在休息日表里、且在 9:00-16:00 内）⇒ 营业中",
                  M._im_clint_open() is True))
    _stub(time_dict={"timeOfDay": 1700, "season": "summer", "dayOfMonth": 6, "weather": 0})
    res.append(ok("🏪 周六 17:00（过了 16:00 打烊）⇒ **不营业**", M._im_clint_open() is False))
    _stub(time_dict={"timeOfDay": 1000, "season": "summer", "dayOfMonth": 5, "weather": 0})
    res.append(ok("🏪 周五（休息日表里就是「铁匠铺 (Clint)」，状态条那句「休:」同源）⇒ **不营业**",
                  M._im_clint_open() is False))
    _stub(time_dict={"timeOfDay": 800, "season": "summer", "dayOfMonth": 6, "weather": 0})
    res.append(ok("🏪 周六 08:00（还没开门）⇒ 不营业", M._im_clint_open() is False))

    # 🪨 砸晶球门禁：老那条（在铁匠铺）保留 + 新那条（背包有晶球 + 克林特营业中），农场不给
    _GEO = [{"slotIndex": 1, "name": "Geode", "displayName": "晶球", "itemId": "(O)535",
             "catNum": -12, "stack": 4, "isGeode": True, "sellable": True, "shippable": True},
            {"slotIndex": 2, "name": "Iridium Bar", "displayName": "铱锭", "itemId": "(O)337",
             "catNum": -15, "stack": 3, "sellable": True, "shippable": True}]

    def _has_geode(**kw):
        _stub(**{"inv": _GEO, **kw})     # ⚠️ 合并成一份 dict 再展开（直接写 `inv=_GEO, **kw`
        _o = M.intent(ops="show", kw={"n": 40})   #    撞上用例自己传的 inv 会 TypeError，自验逮到）
        return "砸 晶球" in _o, _o

    _ok_g, _ = _has_geode(loc="Blacksmith", time_dict={"timeOfDay": 1000, "season": "summer",
                                                      "dayOfMonth": 6, "weather": 0})
    res.append(ok("🪨 在铁匠铺（老判据保留）⇒ 给「砸 晶球」", _ok_g))
    _ok_g2, _g2 = _has_geode(loc="Town", time_dict={"timeOfDay": 1000, "season": "summer",
                                                   "dayOfMonth": 6, "weather": 0})
    res.append(ok("🪨 **背包有晶球 + 克林特营业中**（人在镇上）⇒ 也给（新判据）", _ok_g2, _g2[:200]))
    _ok_g3, _ = _has_geode(loc="Town", time_dict={"timeOfDay": 1700, "season": "summer",
                                                 "dayOfMonth": 6, "weather": 0})
    res.append(ok("🪨 打烊后（非铁匠铺）⇒ **不给**", not _ok_g3))
    _ok_g4, _ = _has_geode(loc="Town", time_dict={"timeOfDay": 1000, "season": "summer",
                                                 "dayOfMonth": 5, "weather": 0})
    res.append(ok("🪨 休息日（非铁匠铺）⇒ **不给**", not _ok_g4))
    _ok_g5, _ = _has_geode(loc="Farm", time_dict={"timeOfDay": 1000, "season": "summer",
                                                 "dayOfMonth": 6, "weather": 0})
    res.append(ok("🪨 **农场不给**（恒：「不建议放农场」）", not _ok_g5))
    _ok_g6, _ = _has_geode(loc="Town", money=0, time_dict={"timeOfDay": 1000, "season": "summer",
                                                          "dayOfMonth": 6, "weather": 0})
    res.append(ok("🪨 钱不够 ⇒ 不给（老判据保留：25g/颗）", not _ok_g6))
    _ok_g7, _ = _has_geode(loc="Town", inv=[_GEO[1]], time_dict={"timeOfDay": 1000,
                                                                "season": "summer",
                                                                "dayOfMonth": 6, "weather": 0})
    res.append(ok("🪨 背包里**没有晶球** ⇒ 不给", not _ok_g7))

    # 🔨 重铸：**战斗精通那一条**（③）在 ⑲ 已有行为用例；这里再钉一条**源码级**的，
    #    防止哪天有人把 `_im_reforge_probe` 开头那道精通闸删了却没人发现。
    import inspect as _insp2
    _rp_src = _insp2.getsource(M._im_reforge_probe)
    res.append(ok("🔨 重铸探针里**确实有**战斗精通那道闸（`_mastery_claimed(\"combat\")`）",
                  '_mastery_claimed("combat")' in _rp_src))

    # 🪱 斑点只扫**身边**（切比雪夫 ≤ `_SPOT_RADIUS`；恒：「不需要特定跑大老远锄」）
    def _spot_at(dx, dy):
        """把一格斑点摆在离人 (12,12) 曼哈顿 dx,dy 的位置 → 那笔账里 spot 的数。"""
        _stub(inv=_HOE, chore_tiles=[{"x": 12 + dx, "y": 12 + dy, "objId": "(O)590"}],
              time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 6, "weather": 0})
        M.api.has_item = lambda n: "Hoe" in str(n)
        return int((M._im_ctx().chores or {}).get("spot") or 0)

    res.append(ok(f"🪱 身边 {M._SPOT_RADIUS} 格内的斑点 ⇒ 算（理由栏写半径）",
                  _spot_at(0, M._SPOT_RADIUS) == 1, _spot_at(0, M._SPOT_RADIUS)))
    res.append(ok(f"🪱 {M._SPOT_RADIUS + 1} 格外的斑点 ⇒ **不算**（不跑大老远）",
                  _spot_at(0, M._SPOT_RADIUS + 1) == 0, _spot_at(0, M._SPOT_RADIUS + 1)))
    res.append(ok("🪱 半径常量**只有一处**（`M._SPOT_RADIUS`），可调", isinstance(M._SPOT_RADIUS, int)))
    _Lspot, _Lstxt = _labels2(inv=_HOE, chore_tiles=[{"x": 14, "y": 12, "objId": "(O)590"}],
                              time_dict={"timeOfDay": 900, "season": "summer",
                                         "dayOfMonth": 6, "weather": 0})
    res.append(ok(f"🪱 单子那行把**半径写进理由栏**（附近 {M._SPOT_RADIUS} 格内）",
                  "挖 远古斑点" in _Lspot and f"附近 {M._SPOT_RADIUS} 格内" in _Lstxt, _Lstxt[:200]))

    # ⑮ 🌾 「铺 干草」（193b · 恒「支持上单子」）——
    #     判据复用 `feed_hay.read_hay_status()`（**脚本铺草前读的同一份**），
    #     只在**动物建筑内**推；五条分支各钉一头。
    _TROUGHS = [(8, 3), (9, 3), (10, 3), (11, 3)]      # 「喂食台 4 格」那种真值表

    def _hay_row(**kw):
        """在棚内/棚外看单子上有没有那一行 → (有没有, 单子正文)。"""
        _stub(farm_buildings=FARM_BUILDINGS, time_dict={"timeOfDay": 900, "season": "summer",
                                                       "dayOfMonth": 6, "weather": 0}, **kw)
        _o = M.intent(ops="show", kw={"n": 40})
        return ("铺 干草" in _o), _o

    _h_in, _h_txt = _hay_row(loc="Deluxe Coop", silo={"hay": 3, "capacity": 240}, troughs=_TROUGHS,
                             trough_filled=1)
    res.append(ok("🌾 在棚内 + 筒仓有草 + 槽没满 ⇒ 给「铺 干草」", _h_in, _h_txt[:200]))
    res.append(ok("🌾 理由栏只报**数字**（`筒仓 3 草 · 喂食台 1/4 格有草`）+ 下一步「铺一次把槽填上」",
                  "筒仓 3 草" in _h_txt and "喂食台 1/4" in _h_txt and "铺一次把槽填上" in _h_txt,
                  _h_txt[:240]))
    res.append(ok("🌾 理由栏**不提槽的机制**（不写「会不会自己补」那类断言）",
                  "自己补" not in _h_txt and "自动补" not in _h_txt, _h_txt[:200]))
    _h_no, _ = _hay_row(loc="Deluxe Coop", silo={"hay": 0, "capacity": 240}, troughs=_TROUGHS,
                        trough_filled=1)
    res.append(ok("🌾 筒仓 0 ⇒ **不给**（按了也是白跑）", not _h_no))
    _h_full, _ = _hay_row(loc="Deluxe Coop", silo={"hay": 3, "capacity": 240}, troughs=_TROUGHS,
                          trough_filled=4)
    res.append(ok("🌾 喂食台**已满** ⇒ 不给", not _h_full))
    _h_out, _ = _hay_row(loc="FarmHouse", silo={"hay": 3, "capacity": 240}, troughs=_TROUGHS,
                         trough_filled=1)
    res.append(ok("🌾 **不在动物建筑内** ⇒ 不给（服务器只在 `FARM_ANIMAL_BUILDINGS` 里推这笔账）",
                  not _h_out))
    _h_bad, _ = _hay_row(loc="Deluxe Coop", silo={"hay": 3, "capacity": 240}, troughs=_TROUGHS,
                         trough_raise=True)
    res.append(ok("🌾 读不到（`/tile_props` 炸）⇒ 不给", not _h_bad))
    # 筒仓 0 时那句"先去弄草"（给"点开时刚好用光"与自检用；单子上看不到这一支）
    _stub(loc="Deluxe Coop", silo={"hay": 0, "capacity": 240}, troughs=_TROUGHS, trough_filled=1,
          time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 6, "weather": 0})
    _ctx0 = M._im_ctx()
    res.append(ok("🌾 筒仓 0 时那行**带下一步**（割草 / 买草 —— 警告必须带路）",
                  "先去弄草" in M.intent_menu._VERB_BY_KEY["hay"].reason(_ctx0, None), _ctx0.hay))
    res.append(ok("🌾 账推对了：`{silo, bench_used, bench_total}` 三个数来自同一次读数",
                  _ctx0.hay == {"silo": 0, "bench_used": 1, "bench_total": 4}, _ctx0.hay))
    # 执行：`_im_run` 认 `hay`，且**调的是现成的 `feed_hay()`**（不是裸端点）
    _fh0 = M.feed_hay
    M.feed_hay = lambda *a, **k: "🌾（桩：feed_hay 跑了）"
    _rh = M._im_run("hay", {})
    M.feed_hay = _fh0
    res.append(ok("🔌 `_im_run` 认 `hay`（回一句话，且走的是现成的 `feed_hay()`）",
                  isinstance(_rh, dict) and "feed_hay 跑了" in str(_rh.get("text")), _rh))

    # ⑯ 194 批：A「捡」复用 `pickup_scene` 的判据（含放宽到蛋/毛）· B「收 蟹笼」如实报剩几个
    # ── A. 判据本身（`pickup_scene.scan_pickables` —— **只此一处**）──
    import pickup_scene as _PS
    _egg = {"x": 20, "y": 20, "passable": False, "object": "Egg", "objId": "(O)176"}
    _wool = {"x": 21, "y": 20, "passable": False, "object": "Wool", "objId": "(O)440"}
    _grab = {"x": 22, "y": 20, "passable": True, "object": "Auto-Grabber", "objId": "(BC)99"}
    _incu = {"x": 23, "y": 20, "passable": True, "object": "Incubator", "objId": "(BC)101"}
    _hay = {"x": 24, "y": 20, "passable": False, "object": "Hay", "objId": "(O)178"}
    _furn = {"x": 25, "y": 20, "passable": False, "object": "红沙发", "objId": "(F)1234"}
    _tool = {"x": 26, "y": 20, "passable": False, "object": "Hoe", "objId": "(T)Hoe"}
    _spot = {"x": 27, "y": 20, "passable": False, "object": "远古斑点", "objId": "(O)590"}
    _onion = {"x": 28, "y": 20, "passable": True, "forageCrop": "1"}
    _pk = _PS.scan_pickables([_egg, _wool, _grab, _incu, _hay, _furn, _tool, _spot, _onion,
                              dict(_egg)], (20, 20))
    _names = [o for _x, _y, o in _pk]
    res.append(ok("🎁 A：棚里 `passable=False` 的**蛋/毛**现在算「能捡」（老判据把它们全排除了）",
                  "Egg" in _names and "Wool" in _names, _pk))
    _coords = [(x, y) for x, y, _o in _pk]
    res.append(ok("🎁 A：同名多格**去重**（同一格只出现一次）", len(_coords) == len(set(_coords)), _pk))
    res.append(ok("🎁 A：离中心近的排前面（20,20 那格在最前）", _coords[0] == (20, 20), _pk))
    res.append(ok("🎁 A：那份 `BLACKLIST` 照旧管用（自动采集器/孵化器/Hay/家具/工具**都不捡**）",
                  not ({"Auto-Grabber", "Incubator", "Hay", "红沙发", "Hoe"} & set(_names)), _names))
    res.append(ok("🎁 A：**远古斑点不归「捡」**（要锄头，归「挖 远古斑点」那行）",
                  "远古斑点" not in _names, _names))
    res.append(ok("🎁 A：成熟大葱那条照旧（没 `object` + `forageCrop==\"1\"`）",
                  "成熟大葱" in _names, _names))

    # ── A. 服务器推的账 + 单子那行（有东西才出现 / 没有就不出现）──
    def _pick_info(extra_tiles=None, **kw):
        _stub(chore_tiles=extra_tiles or [], **kw)
        _ctxp = M._im_ctx()
        _out = M.intent(ops="show", kw={"n": 40})
        return _ctxp.pick, ("捡 地上的东西" in _out or "捡 Egg" in _out), _out

    _pi, _has, _ = _pick_info()
    res.append(ok("🎁 A：`Ctx.pick` 带着 n/最近距离/坐标清单（服务器用 `scan_pickables` 算）",
                  _pi.get("n") == 1 and _pi.get("near") == 1 and _pi.get("keys") == [[13, 12]], _pi))
    res.append(ok("🎁 A：有东西 ⇒ 「捡 地上的东西」在单子上", _has))
    _pi2, _has2, _ = _pick_info(extra_tiles=[_grab, _incu, _hay, _furn, _spot])
    res.append(ok("🎁 A：只有黑名单/斑点类的格子 ⇒ 清单**不含**它们（n 还是 1）",
                  _pi2.get("n") == 1, _pi2))
    _pi3, _has3, _ = _pick_info(surr_tiles=[])
    res.append(ok("🎁 A：**一件都没有 ⇒ 那行不出现**（恒要的「等于待办」）",
                  _pi3 == {} and not _has3, (_pi3, _has3)))
    res.append(ok("🎁 A：走的是**现成的那条 op**（`_im_run` 认 `pickup_scene`，不由这层写 HTTP）",
                  isinstance(M._im_run("pickup_scene", {}), dict)))

    # ── B. 「收 蟹笼」收完**回读真值**、如实报剩几个 ──
    def _crab_after(ready_left, scan_raises=False):
        """跑一次 `_crab_collect`：收完回读 `/crab_pots` 的 `readyForHarvest`。全打桩。"""
        _old = (M._crab_scan_placed, M._crab_stand_for, M._crab_pots_scan, M.api._post,
                M._wait_arrival, M.time.sleep)
        M._crab_scan_placed = lambda: [(42, 1), (43, 1), (44, 1)]
        M._crab_stand_for = lambda x, y: (x, y + 1, 0)
        M._wait_arrival = lambda *a, **k: True
        M.api._post = lambda ep, data=None: {"ok": True}
        M.time.sleep = lambda *a, **k: None
        if scan_raises:
            def _boom(*a, **k):
                raise RuntimeError("模拟：/crab_pots 读不到")
            M._crab_pots_scan = _boom
        else:
            M._crab_pots_scan = lambda *a, **k: (
                [{"x": 42, "y": 1, "readyForHarvest": True},
                 {"x": 43, "y": 1, "readyForHarvest": False},
                 {"x": 44, "y": 1, "readyForHarvest": False}] if ready_left == 1 else
                [{"x": 42, "y": 1, "readyForHarvest": bool(ready_left)},
                 {"x": 43, "y": 1, "readyForHarvest": False},
                 {"x": 44, "y": 1, "readyForHarvest": False}])
        try:
            return M._crab_collect()
        finally:
            (M._crab_scan_placed, M._crab_stand_for, M._crab_pots_scan, M.api._post,
             M._wait_arrival, M.time.sleep) = _old

    _rb = _crab_after(ready_left=1)
    res.append(ok("🦀 B：收完**还剩 1 个** ⇒ 如实报出来 + 点名坐标（真机那个 (42,1) 的洞）",
                  "还剩 1 个没收到" in _rb and "(42,1)" in _rb, _rb.splitlines()[-3:]))
    res.append(ok("🦀 B：而且给下一步（再收一次 / 记得放饵）",
                  "crab_collect" in _rb and "crab_bait" in _rb, _rb.splitlines()[-2:]))
    _rb0 = _crab_after(ready_left=0)
    res.append(ok("🦀 B：真收干净了 ⇒ 明说**全收干净了**", "全收干净了" in _rb0, _rb0.splitlines()[-1:]))
    _rbe = _crab_after(ready_left=1, scan_raises=True)
    res.append(ok("🦀 B：**读不到** ⇒ 如实说「到底收没收干净我不知道」（不编）",
                  "不知道" in _rbe and "回读" in _rbe, _rbe.splitlines()[-1:]))

    # ⑰ 195 批：门态**现在能只读**了（`animalDoorOpen` + `animalDoorX/Y`）
    #     ⇒ 两行按"当前门态 vs 本行目标态"沉底；走位目标改**动物小门**。
    def _farm_b(want_open=None, animal_door=True):
        """`FARM_BUILDINGS` 的深拷 + 按用例给门态/小门坐标（**只在动物建筑上加**）。"""
        out = []
        for b in FARM_BUILDINGS:
            b2 = dict(b)
            if "Coop" in b["type"] or "Barn" in b["type"]:
                if animal_door:
                    # 形照真机：`Deluxe Coop: animalDoor(49,38)` / `Deluxe Barn: animalDoor(41,39)`
                    b2["animalDoorX"] = 45 if "Coop" in b["type"] else 53
                    b2["animalDoorY"] = 20
                if want_open is not None:
                    b2["animalDoorOpen"] = want_open
            out.append(b2)
        return out

    def _doors_ctx(buildings):
        _stub(loc="Farm", farm_buildings=buildings,
              time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 6, "weather": 0})
        return M._im_ctx()

    _c_all_open = _doors_ctx(_farm_b(want_open=True))
    res.append(ok("🚪 195：`/farm_buildings` 的门态被数出来（全开 ⇒ open=2 / closed=0 / unknown=0）",
                  _c_all_open.doors.get("open") == 2 and _c_all_open.doors.get("closed") == 0
                  and _c_all_open.doors.get("unknown") == 0, _c_all_open.doors))
    _c_mixed = _doors_ctx([dict(_farm_b(want_open=True)[0]),
                           dict(_farm_b(want_open=False)[1]), dict(FARM_BUILDINGS[2])])
    res.append(ok("🚪 195：一开一关 ⇒ open=1 / closed=1（温室不算进 builds）",
                  _c_mixed.doors.get("open") == 1 and _c_mixed.doors.get("closed") == 1
                  and _c_mixed.doors.get("builds") == 2, _c_mixed.doors))
    _c_nokey = _doors_ctx(_farm_b(want_open=None))
    res.append(ok("🚪 195：**缺键 ⇒ unknown=2**（不许当成「开着」或「关着」）",
                  _c_nokey.doors.get("unknown") == 2, _c_nokey.doors))

    _IM = M.intent_menu
    _w_open, _w_close = _IM._VERB_BY_KEY["opendoors"], _IM._VERB_BY_KEY["doors"]
    res.append(ok("🚪 195：**门都开着** ⇒ 「放牧」沉底、「关棚门」照常",
                  _IM._weight_of(_w_open, _c_all_open) == _IM._DOORS_SUNK_W
                  and _IM._weight_of(_w_close, _c_all_open) == 70,
                  (_IM._weight_of(_w_open, _c_all_open), _IM._weight_of(_w_close, _c_all_open))))
    _c_all_closed = _doors_ctx(_farm_b(want_open=False))
    res.append(ok("🚪 195：**门都关着** ⇒ 反过来（放牧 84 / 关棚门沉底）",
                  _IM._weight_of(_w_open, _c_all_closed) == 84
                  and _IM._weight_of(_w_close, _c_all_closed) == _IM._DOORS_SUNK_W,
                  (_IM._weight_of(_w_open, _c_all_closed), _IM._weight_of(_w_close, _c_all_closed))))
    res.append(ok("🚪 195：**一开一关 ⇒ 两行都不沉**（都没到目标态）",
                  _IM._weight_of(_w_open, _c_mixed) == 84
                  and _IM._weight_of(_w_close, _c_mixed) == 70,
                  (_IM._weight_of(_w_open, _c_mixed), _IM._weight_of(_w_close, _c_mixed))))
    res.append(ok("🚪 195：**读不到（缺键）⇒ 两行都不沉**（宁缺勿编）",
                  _IM._weight_of(_w_open, _c_nokey) == 84
                  and _IM._weight_of(_w_close, _c_nokey) == 70,
                  (_IM._weight_of(_w_open, _c_nokey), _IM._weight_of(_w_close, _c_nokey))))
    res.append(ok("🚪 195：读得出来时理由栏**如实**带门态（「都开着」）",
                  "都开着" in _w_open.reason(_c_all_open, None),
                  _w_open.reason(_c_all_open, None)[:120]))
    res.append(ok("🚪 195：**读不到时理由栏一个字都不提门态**",
                  "现在" not in _w_open.reason(_c_nokey, None)
                  and "现在" not in _w_close.reason(_c_nokey, None),
                  _w_open.reason(_c_nokey, None)[:120]))
    _L_doors, _ = _labels2(loc="Farm", farm_buildings=_farm_b(want_open=True),
                           time_dict={"timeOfDay": 900, "season": "summer",
                                      "dayOfMonth": 6, "weather": 0})
    _i_open = _L_doors.index("放牧（开棚门）") if "放牧（开棚门）" in _L_doors else -1
    res.append(ok("🚪 195：单子上「放牧」真的**掉到后面**了（排在其它行之后）",
                  _i_open > 3 and _i_open >= len(_L_doors) - 2, _L_doors))

    # 🚶 走位目标 = **动物小门旁边那格**（缺键退回人类门那一套）
    #    ⚠️ `_stub()` **要在打桩 `_walk_and_wait` 之前**调 —— 它会重新封一遍走位函数
    #       （第一版先打桩后 `_stub`，记录器被覆盖 ⇒ 记到空（这个坑今天第二次踩）。
    _WALK_SEEN = []
    _adj = {(45, 21), (45, 19), (44, 20), (46, 20)}      # 小门 (45,20) 的四邻

    def _patch_walk():
        _old = (M._walk_and_wait, M.api.face)
        M._walk_and_wait = lambda loc, x, y, timeout=None: (
            _WALK_SEEN.append((loc, x, y)), (True, ""))[1]
        M.api.face = lambda *a, **k: None
        return _old

    _stub(loc="Farm", farm_buildings=_farm_b(want_open=True),
          time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 6, "weather": 0})
    _o_ww = _patch_walk()
    try:
        _nm, _note = M._walk_to_animal_door()
    finally:
        M._walk_and_wait, M.api.face = _o_ww
    res.append(ok("🚶 195：走位目标是**小门旁边那格**（小门(45,20) ⇒ 站它四邻之一，朝门）",
                  bool(_WALK_SEEN) and tuple(_WALK_SEEN[-1][1:]) in _adj and "小门" in _note,
                  (_WALK_SEEN, _note)))
    _WALK_SEEN.clear()
    _stub(loc="Farm", farm_buildings=_farm_b(animal_door=False),
          time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 6, "weather": 0})
    _o_ww2 = _patch_walk()
    try:
        _nm2, _note2 = M._walk_to_animal_door()
    finally:
        M._walk_and_wait, M.api.face = _o_ww2
    res.append(ok("🚶 195：**缺小门键 ⇒ 退回人类门那一套**（door(44,16) ⇒ 站 (44,17)）",
                  bool(_WALK_SEEN) and tuple(_WALK_SEEN[-1][1:]) == (44, 17)
                  and "门(44,16)" in _note2, (_WALK_SEEN, _note2)))
    import inspect as _insp3
    res.append(ok("🚶 195：翻门**仍然走 `/toggle_doors`**（注释写清为什么不用 interact：未验）",
                  "/toggle_doors" in _insp3.getsource(M._doors_flip_once)
                  and "先在真机 A/B" in _insp3.getsource(M._walk_to_animal_door)))

    # ⑱ 195b：**「穿戴」撤出单子**（恒拍板）—— 功能搬到 `daily(ops="wear", …)`
    import inspect as _insp4
    _wear_bag = [{"slotIndex": 2, "name": "Straw Hat", "displayName": "草帽", "catNum": -95,
                  "stack": 1}]
    # ⚠️ `/worn` 的夹具是模块级 `WORN`（`_stub` 没有 `worn=` 这个旋钮）——穿戴行撤了之后
    #    也不改了：这一条要的就是"**身上戴着 + 背包有得穿**，单子照样不出现"。
    _stub(inv=_wear_bag,
          time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 6, "weather": 0})
    _wo = M.intent(ops="show", kw={"n": 40})
    res.append(ok("👕 195b：单子上**没有「穿戴」**（身上戴着 + 背包有得穿也不出现）",
                  "穿戴" not in _wo, _wo[:160]))
    res.append(ok("👕 195b：动词表里 `wear` / `wear_on` / `wear_off` **都不在**（撤干净）",
                  all(k not in M.intent_menu._VERB_BY_KEY for k in ("wear", "wear_on", "wear_off"))))
    res.append(ok("👕 195b：`daily` 的 dispatch 里**仍然有** `wear`（功能没少）",
                  '"wear": wear' in _insp4.getsource(M.daily)))
    _dw = ""
    try:
        _dw = str(M.daily(ops="wear", kw={"name": "Straw Hat"}))
    except Exception as e:
        _dw = f"（抛了 {type(e).__name__}: {e}）"      # 自验里闸会把 POST 拦掉，这就是预期
    res.append(ok("👕 195b：`daily(ops=\"wear\")` **认得这个 op**（不是「未知 ops」）",
                  "未知" not in _dw, _dw[:180]))
    res.append(ok("👕 195b：意图索引里「穿戴」指向 `daily wear`",
                  any(d == "daily" and o == "wear" and "穿戴" in k
                      for k, d, o, _h in M._INTENT_INDEX)))
    res.append(ok("👕 195b：`daily` 的域说明里写着 `wear`（别「删了单子又没写路」）",
                  "wear(穿/脱衣物" in str(M._DOMAIN_GUIDES.get("daily") or "")))

    # ⑲ 195c：`搬走` 撤出单子（替代路 = scene 域）· 棚里"没东西可捡"的原因标注
    import inspect as _insp5
    res.append(ok("🛋⛔ 195c：`pickup_f` / `pickup_one` **都不在动词表**里（撤干净）",
                  all(k not in M.intent_menu._VERB_BY_KEY for k in ("pickup_f", "pickup_one"))))
    _scene_src = _insp5.getsource(M.scene)
    res.append(ok("🛋 195c：替代路**确实在**（`scene` 的 dispatch 有 `pickup`→`furniture_pickup` + "
                  "`furniture`→`scan_furniture`）",
                  '"pickup": furniture_pickup' in _scene_src
                  and '"furniture": scan_furniture' in _scene_src))
    _stub(loc="Deluxe Coop", surr_tiles=[])
    M._BARN_EMPTY_KEY.update(loc=None, txt="")
    _bn = M._barn_empty_hint("Deluxe Coop")
    res.append(ok("🥚 195c：棚里地上没东西可捡 ⇒ 标注**两种可能**（采集器收走了 / 今天没下蛋没吃草）",
                  "自动采集器" in _bn and "没吃上草" in _bn, _bn[:220]))
    res.append(ok("🥚 195c：**不说死、不写成失败**（两种并列 + 明说「不是出错」「分不出来」）",
                  "不是出错" in _bn and "分不出来" in _bn))
    _stub(loc="Deluxe Coop")
    M._BARN_EMPTY_KEY.update(loc=None, txt="")
    res.append(ok("🥚 195c：地上**有**可捡的 ⇒ 不标这句（那行自己会出现）",
                  M._barn_empty_hint("Deluxe Coop") == ""))
    M._BARN_EMPTY_KEY.update(loc=None, txt="")
    res.append(ok("🥚 195c：**不在动物建筑里**（农场）⇒ 不标（别在矿洞/农场刷这句）",
                  M._barn_empty_hint("Farm") == ""))
    res.append(ok("🌾 195c：顺手确认 193b 那条 —— **喂食台铺满 ⇒ 「铺 干草」不出现**（本轮再核一次）",
                  not _hay_row(loc="Deluxe Coop", silo={"hay": 3, "capacity": 240},
                               troughs=_TROUGHS, trough_filled=4)[0]))

    # ⑳ 195d 追加：落点**最近优先**（恒：「走不通才换个方向试，最好是先试试离自己近的那一个落点」）
    _ap = _PS.approach_tiles(1, 5, 3, 6)          # 目标 (1,5)，人在 (3,6)
    _ad = [abs(a - 3) + abs(b - 6) for a, b in _ap]
    res.append(ok("🎯 195d：落点候选**按离人距离升序**（最近的在最前）",
                  _ad == sorted(_ad), (_ap, _ad)))
    res.append(ok("🎯 195d：候选就是目标的**四个正邻**（能站到它正旁边才够得着）",
                  sorted(_ap) == sorted([(1, 6), (1, 4), (2, 5), (0, 5)]), _ap))
    _ad2 = [abs(a - 1) + abs(b - 9) for a, b in _PS.approach_tiles(1, 5, 1, 9)]
    res.append(ok("🎯 195d：换个站位，顺序跟着变（**离得近的那个先试**，不是写死方向）",
                  _ad2 == sorted(_ad2) and _PS.approach_tiles(1, 5, 1, 9)[0] == (1, 6), _ad2))
    import inspect as _insp7
    _pu_src = _insp7.getsource(_PS.main)
    res.append(ok("🎯 195d：**走不通才换下一个**（循环里够到就 `break`）",
                  "for nx, ny in approach_tiles(" in _pu_src and "break" in _pu_src))

    # ㉑ 197 追补：**自动采集器 = 容器，不是机器**（恒 2026-10-02：「它就像一个箱子一样」）
    _grab_m = {"type": "Auto-Grabber", "location": "Deluxe Coop", "x": 5, "y": 5,
               "status": "processing", "heldItem": "Error Item",
               "heldItemDisplay": "错误物品 (-1)", "heldItemId": "(O)-1"}
    res.append(ok("🤖 197：`_is_container_obj` 认得出采集器/抚摸机（按**类型**，不按名字猜）",
                  M._is_container_obj(_grab_m) and M._is_container_obj({"type": "Auto-Petter"})
                  and not M._is_container_obj({"type": "Keg"})))
    res.append(ok("🏷️ 197：`_bogus_held_name` 认得出「错误物品 (-1)」那族假名字",
                  M._bogus_held_name("错误物品 (-1)") and M._bogus_held_name("")
                  and not M._bogus_held_name("钻石")))
    _mw = M._im_mwork(None, [_grab_m, {"type": "Keg", "status": "ready",
                                       "heldItemDisplay": "钻石"}])
    res.append(ok("🤖 197：`_im_mwork` **不把采集器算进机器账**（ready/empty 都不含它）",
                  _mw.get("ready") == 1 and _mw.get("empty") == 0
                  and _mw.get("products") == {"钻石": 1}, _mw))
    _mw2 = M._im_mwork(None, [{"type": "Keg", "status": "ready",
                               "heldItemDisplay": "错误物品 (-1)"}])
    res.append(ok("🤖 197：产物名是**假名字** ⇒ 不进产物表（宁缺勿编）",
                  _mw2.get("products") == {} and _mw2.get("ready") == 1, _mw2))
    # ⚠️ `machine_report` 的数据源是 `api.farm_report()`（不是 `_ai_get`）—— 第一版桩错了口子，
    #    自验当场红（"❌ 获取失败"）。
    _old_fr = M.api.farm_report
    # ⚠️ 真回包是**两层**：`{"ok":True, "machines":{"machines":[…]}}`（第一版写平了 ⇒ "获取失败"）
    M.api.farm_report = lambda *a, **k: {
        "ok": True,
        "machines": {"machines": [_grab_m, {"type": "Keg", "location": "Farm",
                                            "status": "ready",
                                            "heldItemDisplay": "钻石"}]}}
    try:
        _mr = M.machine_report()
    finally:
        M.api.farm_report = _old_fr
    res.append(ok("🤖 197：机器表把采集器**单列**（「按容器读，不算机器」）且不印假名字",
                  "自动采集器" in _mr and "按容器读，不算机器" in _mr
                  and "错误物品" not in _mr, _mr[:220]))
    res.append(ok("🤖 197：机器台数**不含**采集器（那一屏只该有 Keg 一台）",
                  "全农场机器 (1 台)" in _mr, _mr[:120]))
    _stub(loc="Deluxe Coop", surr_tiles=[])
    M._BARN_EMPTY_KEY.update(loc=None, txt="")
    _old_am = M.api.machines
    M.api.machines = lambda *a, **k: {"machines": [_grab_m]}
    try:
        _bn2 = M._barn_empty_hint("Deluxe Coop")
    finally:
        M.api.machines = _old_am
    res.append(ok("🥚 197：读得到「这间装了采集器」⇒ 写成**事实**（两种可能更具体）",
                  "装了自动采集器" in _bn2 and "也可能" in _bn2, _bn2[:200]))
    M._BARN_EMPTY_KEY.update(loc=None, txt="")
    _stub(loc="Deluxe Coop", surr_tiles=[])
    _bn3 = M._barn_empty_hint("Deluxe Coop")       # 夹具默认 `/machines` 是空表
    res.append(ok("🥚 197：读不到采集器 ⇒ 退回「两种可能并列」（不硬编）",
                  "装了自动采集器" not in _bn3 and "也可能是" in _bn3, _bn3[:200]))

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
