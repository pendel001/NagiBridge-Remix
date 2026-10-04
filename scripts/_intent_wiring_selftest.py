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
#    `DOOR_STATES` = **逐扇门**一张表 `[{building, doorX, doorY, open}]`（同名两栋各自成行）；
#    `AI_POS` = "我"在桩里站哪格（`/state.player.x/y` 现读它）。
#    ⚠️ 桩照抄 **2026-10-03 之后**的 C# `/toggle_doors` 真契约：
#      · `action` 忽略、**纯翻转**（`netBool.Value = !netBool.Value`）；
#      · 支持点名（`building` 类型子串 / `doorX,doorY` 精确到门 —— 同名两栋只能靠坐标分开）；
#      · **只翻玩家 `ReachTiles` 格内**的门，够不着/不在农场/没门坐标的一律进 `skipped`。
#    ⇒ 少了"够得着"这层，`_doors_flip_all` 的 `left`/「没翻成」那条路**根本测不到**（假绿）。
DOOR_STATES = []
# 🚶 "我"在桩里站哪格：`_stub(ai_xy=…)` 给初值；**走位成功会把人挪过去**（`M._walk_and_wait` 的桩）
#    —— C# 的 4 格闸判的就是这个位置，桩不挪人就等于"人站在原地遥控翻门"（真机上翻不动）。
AI_POS = [12, 12]
# 🚪 C# 侧 `ReachTiles` 的口径（`ModEntry.cs`）：与门格的**切比雪夫**距离 ≤ 4。
_REACH_TILES = 4
# 🚶 走位调用记录（`_stub` 每次清空；门那条路要证"**逐栋都走**"—— 一栋一发）。
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

# 🚪 **翻门那条路**专用的建筑夹具：照**真机形状**带动物小门坐标（`/farm_buildings.animalDoorX/Y`，
#    C# 反射读 `building.animalDoor`；2026-10-02 真机验通）。
#    ⚠️ 为什么不能直接拿上面那份：`_doors_flip_all(only=[…])` 筛建筑时**只读 `animalDoorX/Y`**
#       （**没有** `_door_goal()` 那种"缺键退回人类门"的兜底）⇒ 少了这两键就点不回来，
#       收敛那一下会**静默什么都不做**（口径不一致，只记在案、**没动产品代码**）。
FARM_BUILDINGS_DOORS = [
    {"type": "Deluxe Coop", "x": 40, "y": 12, "width": 7, "height": 4,
     "doorX": 44, "doorY": 16, "animalDoorX": 45, "animalDoorY": 20},
    {"type": "Deluxe Barn", "x": 48, "y": 12, "width": 7, "height": 4,
     "doorX": 52, "doorY": 16, "animalDoorX": 53, "animalDoorY": 20},
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
    # 👥 `/state.npcs` —— 真机形状：**只有村民**（ModEntry 5217 把宠物/马/怪物/祝尼魔滤掉了，
    #    猫狗走另一个 `pets` 字段），字段只有 name/displayName/x/y（**没有 `kind`**）。
    #    ⚠️ 原来这里**没有这一项**（"喵喵"那只猫只活在 `SURR` 里）—— 夹具比真机更空，
    #    于是"👥 本图 NPC"那行**整天印不出来**都没被自验抓到（详见下面 ㉒ 那条用例）。
    #    坐标故意放远（人站在 (12,12)）：`nearby_npcs` 只收 2 格内，别影响别的用例。
    "npcs": [{"name": "Gus", "displayName": "格斯", "x": 25, "y": 24}],
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
          surr_tiles=None, trash_cans=None, cola=None, npcs=None, nuts=None,
          trash_checked=None, pet_bowls=None, passable_ret=None, ai_xy=None,
          statues=(), blessed=None, ponds=None, buffs=None):
    CALLS.clear()
    WALK_CALLS.clear()
    # 🚶 "我"站哪格：默认照 `STATE`（(12,12)），用例要"人已经站在棚门口"就传 `ai_xy=`。
    if ai_xy is None:
        _p0 = STATE.get("player") or {}
        AI_POS[0], AI_POS[1] = int(_p0.get("x") or 0), int(_p0.get("y") or 0)
    else:
        AI_POS[0], AI_POS[1] = int(ai_xy[0]), int(ai_xy[1])
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
    if npcs is not None:
        # 👥 `/state.npcs` 的真形状：**只有村民**（C# 侧已经滤掉宠物/马/怪物/祝尼魔，见 ModEntry
        #    5217），字段就 name/displayName/x/y —— **没有 `kind`**（宠物走另一个 `pets` 字段）。
        #    ⚠️ 2026-10-02：这条参数是补"那一行从来印不出来"的用例时才加的 —— 在此之前夹具的
        #    `/state` **连 `npcs` 键都没有**（只有 `SURR` 里有），而真机 `/state` 一直是有人的
        #    ⇒ 夹具比现实更空，把"字段压根没透传"这个病**完美地遮住了**（自验全绿、真机全瞎）。
        state = dict(state, npcs=npcs)
    # 🌿 六件"顺手活"（P1 那批）的料：`/state.player.orePan` + 追加的采集格
    if ore_pan is not None:
        state = dict(state, player=dict(state.get("player") or {}, orePan=ore_pan))
    if money is not None:
        # 💰 用例要"买不起/砸不起"就传 money=0（`_geode_can` 用 `ctx.money` 判 25g/颗）
        state = dict(state, player=dict(state.get("player") or {}, money=money))
    if blessed is not None:
        # 🗿 2026-10-04：`/state.player.blessedByStatueToday`（**游戏自己的**"今天摸过祝福雕像没"，
        #    `Farmer.hasBeenBlessedByStatueToday`）。`blessed=None`（默认）⇒ **不吐这个键**
        #    = **老 DLL 的形状**（同 `trash_checked`）。
        state = dict(state, player=dict(state.get("player") or {},
                                        blessedByStatueToday=blessed))
    if buffs is not None:
        # 🗿 矮人国王雕像的门是 **buff**（`dwarfStatue`），不是"每天一次"（反编译实据）⇒
        #    用例要能喂 buff 表（`[{"id": "dwarfStatue_3", …}]`）。
        state = dict(state, player=dict(state.get("player") or {}, buffs=buffs))
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
    # 🗿 雕像（2026-10-04）：`object` 名里含 `Statue` 的格 —— 判据跟 `blessing_statue.py` 同源
    #    （`(x, y, 内部名)` 三元组，题面照真机那份 `Statue Of Blessings` 写）。
    _tiles_fixture += [{"x": x, "y": y, "object": nm} for x, y, nm in (statues or ())]

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/scan":
            # 🗑️🥤 垃圾桶/可乐机**都不在 `loc.objects`**，只能从 `/scan` 的 Action 瓦片里认
            #    （`Garbage <id>` / `ColaMachine`）—— 判据在 `_scan_actions_here()`，
            #    这里照真回包形状给（真机 Saloon 的机器是 (37,17)+(38,17) 两格）。
            #    ⚠️ `trash_checked=None`（默认）⇒ **不吐 `garbageChecked` 键** = **老 DLL 的形状**
            #       （新 DLL 每格都带这个键，真假都有 ⇒ "键在不在"就是"这版报不报得出"的判据）。
            acts = [dict({"action": f"Garbage C{i}", "x": x, "y": y},
                         **({} if trash_checked is None
                            else {"garbageChecked": i in set(trash_checked)}))
                    for i, (x, y) in enumerate(trash_cans or [])]
            acts += [{"action": "ColaMachine", "x": x, "y": y} for x, y in (cola or [])]
            return {"ok": True, "actions": acts}
        if ep == "/menu":
            if menu_get_raises:
                raise RuntimeError("模拟：商店开着但 /menu 读不出来")
            return MENU_SHOP if menu_raw is None else menu_raw
        if ep == "/nuts":
            # 🌰 金核桃真回包形状：`{ok, nuts:[{kind, taken, …}]}` —— 姜岛"核桃丛"那条例外读它
            return {"ok": True, "nuts": list(nuts or [])}
        if ep == "/petbowl":
            # 🍽️ 宠物碗真回包形状：`{ok, bowl:{x,y}, bowlWatered, bowls:[{x,y,watered,doorX,doorY}]}`
            #    ⚠️ `pet_bowls=None` ⇒ **不吐 `bowls` 键** = 老 DLL 形状（消费侧必须退回"全浇"）
            _bl = list(pet_bowls or [])
            _r = {"ok": True, "bowl": {"x": 53, "y": 7},
                  "bowlWatered": any(b.get("watered") for b in _bl)}
            if pet_bowls is not None:
                _r["bowls"] = _bl
            return _r
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
        if ep == "/state":
            # 🚶 "我"的坐标**每次现读 `AI_POS`**（走位那一发的桩会挪它）——
            #    写死成夹具 (12,12) 的话，C# 那 4 格闸在桩里就成了"永远够不着/永远够得着"。
            return dict(state, player=dict(state.get("player") or {},
                                           x=AI_POS[0], y=AI_POS[1]))
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
        if ep == "/passable":
            # 🚶🐄 `/passable` 真回包：`{ok, passable, x, y, location, blocker}` ——
            #    `passable_ret=None` ⇒ 走通用兜底（**没有 `passable` 键** = 老 DLL 的形状）
            if passable_ret is not None:
                return dict(passable_ret)
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
            # 🚪 C# `/toggle_doors` 的**新**契约（2026-10-03 起，照 `ModEntry.cs` 抄）：
            #    · 忽略 `action`（翻转端点，没有方向）；
            #    · 支持点名：`building` = 类型子串 / `doorX,doorY` = **精确到门**；
            #    · **只翻玩家 `ReachTiles` 格内的门** —— 够不着/不在农场/没门坐标 ⇒ 进 `skipped` 点名；
            #    · 回 `details:[{building, door_open, doorX, doorY}]`（⚠️ 旧桩**没有** `doorX/doorY`
            #      ⇒「逐栋报执行后的门态」那条在旧桩上恒成「未确认」，这就是它变红的原因）。
            #    ⚠️ 桩不模拟"点名了但一扇都没匹配上"的糊弄：C# 那边也没这条路，匹配不上就是空回包
            #       （消费侧会如实记成 `no_reply`，别在这儿编一个 reason）。
            _d = data or {}
            _wb = str(_d.get("building") or "")
            _wdx, _wdy = _d.get("doorX"), _d.get("doorY")
            _loc_nm = str((state.get("location") or {}).get("name") or "")
            _on_farm = (_loc_nm == "Farm")
            _px, _py = AI_POS[0], AI_POS[1]
            _det, _skip = [], []
            for _st_d in DOOR_STATES:
                if _wb and _wb.lower() not in str(_st_d["building"]).lower():
                    continue
                if _wdx is not None and int(_wdx) != _st_d["doorX"]:
                    continue
                if _wdy is not None and int(_wdy) != _st_d["doorY"]:
                    continue
                _dist = max(abs(_st_d["doorX"] - _px), abs(_st_d["doorY"] - _py))
                if not _on_farm:
                    _skip.append(dict(_st_d, reason="not_on_farm", distance=_dist))
                    continue
                if _dist > _REACH_TILES:
                    _skip.append(dict(_st_d, reason="too_far", distance=_dist))
                    continue
                _st_d["open"] = not _st_d["open"]      # ← 纯翻转（照 C#）
                _det.append({"building": _st_d["building"], "door_open": _st_d["open"],
                             "doorX": _st_d["doorX"], "doorY": _st_d["doorY"]})
            return {"ok": True, "location": _loc_nm, "onFarm": _on_farm,
                    "reachTiles": _REACH_TILES, "playerX": _px, "playerY": _py,
                    "toggled": len(_det), "details": _det,
                    "skipped": [{k: s[k] for k in ("building", "doorX", "doorY", "reason", "distance")}
                                for s in _skip]}
        if ep == "/interact":              # 交互（真机形状：ok 恒真、actionTriggered 才是真话）
            return {"ok": True, "actionTriggered": True, "object": "Anvil"}
        if ep == "/fish_pond":
            # 🐟 鱼塘（`/fish_pond action=list` 的真回包形状：`{ok, ponds:[…]}`）——
            #    `output` 非空 = 有产出等着领（`_im_ponds` 就认这一个字段）。
            return {"ok": True, "ponds": list(ponds or [])}
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
    def _walk_stub(loc, x, y, timeout=25):
        """走位（桩）：记一笔；**走成了就把"我"挪过去**。

        ⚠️ 必须挪：`/toggle_doors` 现在**只翻玩家 4 格内的门**，桩若让"人"钉在 (12,12)，
           真机那条"走到门口就翻得成"的路在自验里**永远走不通**（会全变成 `too_far`）。
        """
        WALK_CALLS.append((loc, x, y))
        if walk_ok:
            AI_POS[0], AI_POS[1] = int(x), int(y)
            return True, ""
        return False, "走位超时没到"      # ⚠️ 没走到 ⇒ 人留在原地（C# 那边也就够不着了）

    M._walk_and_wait = _walk_stub
    M.navigation.walk_to = lambda *a, **k: None
    api.position = lambda x, y: {"ok": True, "stub": True}
    # 把桩函数也留一份在模块级：有些 op 走的是 **`api._get`**（不是 `_ai_get`），
    # 用例要临时把 `_get` 也接到同一个桩上（例：`process_geodes`）。
    global _LAST_G, _LAST_P
    _LAST_G = g
    # ⚠️ 有些 op 走的是 **`api._post`**（不是 `_ai_post`）—— `api.close_doors()` 就是
    #    （它内部 `_post("/toggle_doors", …)`）。用例要临时把 `_post` 也接到同一个桩上，
    #    否则"敲放牧/关棚门"会真的去敲 localhost:7842（真机端口！）而不是走桩。
    _LAST_P = p
    # 🚪 门那条路的**桩状态**：逐扇门一张表（`/toggle_doors` 的桩读它）。
    #    `type` 含 Coop/Barn 的建筑才算；门坐标优先 `animalDoorX/Y`（小门），缺了退回人类门
    #    `doorX/doorY` —— 跟 `_door_goal()` 一个口径，**别在桩里另立一套**。
    #    初始开合：建筑自己带 `animalDoorOpen` 就用它（`_farm_b()` 就是这么造"一开一关"的），
    #    否则统一用 `doors_open=` 那个旋钮（老用例一个字不用改）。
    DOOR_STATES.clear()
    for _b in (farm_buildings or ()):
        _ty = str((_b or {}).get("type") or "")
        if "Coop" not in _ty and "Barn" not in _ty:
            continue
        _ax, _ay = (_b or {}).get("animalDoorX"), (_b or {}).get("animalDoorY")
        if not (isinstance(_ax, int) and isinstance(_ay, int)):
            _ax, _ay = (_b or {}).get("doorX"), (_b or {}).get("doorY")
        if not (isinstance(_ax, int) and isinstance(_ay, int)):
            continue          # 连人类门坐标都没有 ⇒ 这条桩路没法点名（真机会进 no_door_coords）
        DOOR_STATES.append({"building": _ty, "doorX": int(_ax), "doorY": int(_ay),
                            "open": bool((_b or {}).get("animalDoorOpen", doors_open))})
    M._with_state = lambda x, *a, **k: x      # 状态机跟"接线"无关，打桩掉
    # 🔁 两个**跨用例的缓存**（精通 30s / `/machine_reqs` 10min）在这里清干净：
    #    它们是给热路径省调用用的，可"上一个用例问到的答案"会被下一个用例读到
    #    —— 真机不会（世界一直变），桩会 ⇒ **每个用例从头开始**（2026-10-01 自验现场逮到）。
    M._MASTERY_CACHE.update(ts=0.0, claimed=None)
    M._MACHINE_REQS_CACHE.update(ok=None, ts=0.0)
    # 🗑️🥤 Action 瓦片清单（垃圾桶/可乐机）按**图名**缓存（`_scan_actions_here`）⇒ 用例之间必须清，
    #    否则第一个用例把 `[]` 缓存住，后面所有"本图有桶/有可乐机"的用例都会假红。
    M._SCAN_ACTIONS.update(key=None, actions=[])
    M._TRASH_CANS.update(key=None, cans=[])
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
    # ⚠️ 2026-10-04：**按 verb.key 判**，别按 label 文本判 —— label 会随口径改名
    #    （「买」→「买 动物（玛妮柜台）」），按文本找的钉子一改名就红/StopIteration（这次两处都这么红的）。
    res.append(ok("商店开着 ⇒ 单子上有「买 动物（玛妮柜台）…」",
                  any(r.verb.key == "buy" for r in M.intent_menu._LAST_ROWS)))
    res.append(ok("商店开着 ⇒ 单子上有「卖…」（背包有这家收的）", "卖…" in out))
    buy_no = next(r.no for r in M.intent_menu._LAST_ROWS if r.verb.key == "buy")
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
    res.append(ok("🚪 `_im_ctx` 把 `_close_hint` 的**原话**递进 ctx（同一次调用、同一个 `content_on_sheet`）",
                  ctx.menu_hint == M._close_hint("ShopMenu", content_on_sheet=True), ctx.menu_hint))
    _so = M.intent(ops="show", kw={"n": 40})
    res.append(ok("🚪 商店开着 ⇒ 「关掉界面」跟 买/卖 **同屏**",
                  "关掉界面" in _so and any(r.verb.key == "buy" for r in M.intent_menu._LAST_ROWS)))
    # 📄 **read 包办**（2026-10-04 恒的口径，写死在 `_close_hint` 的 docstring 里）：
    #    「**菜单类型已知、内容我们读得到**的 ⇒ 内容摊在单子上，别叫 AI 再 `menu read` 一遍；
    #      **参数确实只有菜单里才有、我们又读不到**的 ⇒ 保留那句 read（那是真路）。」
    #    ⚠️ 判据是**这一刻那份内容到底摊没摊出来**（事实），不是菜单类型（类型只决定摊不摊得出来）。
    #    ⚠️ 这几条**自带 `_stub`**：别插在上面那段"`_stub(shop=True)` → 逐条验"的中间，
    #       否则会把那个桩冲掉、后面依赖它的用例集体假红（2026-10-04 现场踩过）。
    _stub(shop=True)
    _h_shop = M._im_ctx().menu_hint
    res.append(ok("📄 商店货架**摊成了** ⇒ 提示语里**不再**叫它 `menu read`（白烧一次调用）",
                  "menu read" not in _h_shop and "就在单子上" in _h_shop, _h_shop))
    res.append(ok("📄 读不到货架（老 DLL / `/menu` 报错）⇒ **退回叫它 read**（不许拍胸脯说摊好了）",
                  "menu read" in M._close_hint("ShopMenu", content_on_sheet=False)))
    res.append(ok("📄 容器同理：摊成了不提 read / 没摊成照旧提",
                  "menu read" not in M._close_hint("ItemGrabMenu", content_on_sheet=True)
                  and "menu read" in M._close_hint("ItemGrabMenu", content_on_sheet=False)))
    res.append(ok("📄 `ShippingMenu` **永远保留** read（发货清单本来就不在那几档里，摊不出来）",
                  "menu read" in M._close_hint("ShippingMenu", content_on_sheet=True)))
    # 端到端：开着一只**能取的**箱子 ⇒ 提示语不提 read；同一种菜单但 `/menu` 读不出来 ⇒ 提
    _stub(menu="ItemGrabMenu", menu_raw=MENU_BOX)
    _hbox = M._im_ctx().menu_hint
    res.append(ok("📄 端到端：开箱且内容读到了 ⇒ 提示语不提 read",
                  "menu read" not in _hbox and "就在单子上" in _hbox, _hbox))
    _stub(menu="ItemGrabMenu", menu_get_raises=True)
    _hbox2 = M._im_ctx().menu_hint
    res.append(ok("📄 端到端：同一种菜单但 `/menu` 读不出来 ⇒ **退回 read**（宁可叫它读，不假装）",
                  "menu read" in _hbox2, _hbox2))
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
    # ⏭️⏭️ 2026-10-02 **真机第一次按「跳过整段」**（恒：「哦！刚好有剧情！你可以试试跳了」）：
    #      **跳成功了**，可回执写着「发了跳过键但事件还在播 —— 这段可能跳不动」
    #      = **报失败而事做成了**（跟"报成功而事没发生"是一对，都会把 AI 带沟里）。
    #      根因：跳过要放完退场那段（淡出 + 把玩家挪回去），原来 `sleep(0.4)` **只读一发**太早
    #      ⇒ 改成轮询。下面用桩钉住（⚠️ `api.key` 必须一起桩掉，否则真按键盘打到游戏）。
    def _skip_after(n_ev, skippable=True):
        _calls = {"n": 0}

        def _fake_state(*a, **k):
            _calls["n"] += 1
            ev = {"id": "2", "skippable": skippable} if _calls["n"] <= n_ev else None
            return {"activeEvent": ev, "activeMenu": None}
        _old_st, _old_key, _old_sleep = M.api.state, M.api.key, M.time.sleep
        M.api.state, M.api.key, M.time.sleep = _fake_state, (lambda *a, **k: None), (lambda *a, **k: None)
        try:
            return M.skip_event(), _calls["n"]
        finally:
            M.api.state, M.api.key, M.time.sleep = _old_st, _old_key, _old_sleep

    _sk_out, _sk_n = _skip_after(2)     # 前两发还在播、第三发没了 ⇒ 必须报"跳成功"
    res.append(ok("⏭️ 跳过**会轮询**（事件退场要时间）—— 真机那次报「还在播」，两秒后一读其实早没了",
                  "已跳过" in _sk_out and _sk_n >= 3, (_sk_out.splitlines()[0], _sk_n)))
    _sk_bad, _ = _skip_after(10 ** 9)   # 一直不消失（⚠️ 轮询是**忙转**的，给 99 会被转过去）⇒ 如实说没跳掉
    res.append(ok("⏭️ 真跳不掉 ⇒ 说清「等了 4 秒还在播」（不假装成功、也不编原因）",
                  "还在播" in _sk_bad and "已跳过" not in _sk_bad, _sk_bad.splitlines()[0]))
    _sk_no, _ = _skip_after(0)          # 压根没事件 ⇒ 老规矩：明说无事可跳
    res.append(ok("⏭️ 没事件 ⇒ 照旧「无事可跳」（别把 skip 静默变成关菜单）",
                  "没有剧情" in _sk_no, _sk_no.splitlines()[0]))
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
    # ⚠️ 判据走 **`verb.key`**，不是屏上的字：2026-10-04「read 包办」给这族的提示语
    #    （`_close_hint`）里**本来就会写「箱子里…」**（它指的正是那几行）⇒ 拿字面量判
    #    「箱子里不在屏上」会把**提示语**误当成**行**（这条当场假红过一次）。
    _se_keys = {r.verb.key for r in M.intent_menu._LAST_ROWS}
    res.append(ok("📥 **空箱子**照样给「存…」（开一只空箱正是要塞东西那一刻）",
                  "menu_store" in _se_keys and "menu_box" not in _se_keys, sorted(_se_keys)))
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
    #    🧭 2026-10-03 起 C# **只翻玩家 4 格内的门** ⇒ 执行侧改成**逐栋走位 + 按门坐标点名翻**
    #       （一栋一发；收不收敛都**必须走位**，因为"人已经在门口"这个前提不再成立）。
    def _doors_posts():
        """这一发打出去的 `/toggle_doors` 请求体（`api.close_doors` 走 `api._post`）。"""
        return [c[2] for c in CALLS if c[1] == "/toggle_doors"]

    def _doors_hit(tod, sub, doors_open, chore_animals=None, **kw):
        """敲那一行 → (回执, 打了几次 `/toggle_doors`, 那几次的 (方法, 端点), 全部端点)。不出网。

        `chore_animals` = 站在农场时 `/animals` 报的那批（= 棚外那批）——
        「关棚门」那行现在会先看它（外面有动物就不关），所以要能按用例指定。
        """
        _stub(loc="Farm", farm_buildings=FARM_BUILDINGS_DOORS, doors_open=doors_open,
              chore_animals=chore_animals,
              time_dict={"timeOfDay": tod, "season": "summer", "weather": 0}, **kw)
        M.intent(ops="show", kw={"n": 40})
        _rc = M.intent(ops="do", kw={"code": str(_no_of(sub))})
        _td = [(c[0], c[1]) for c in CALLS if c[1] == "/toggle_doors"]
        return _rc, len(_td), _td, [c[1] for c in CALLS]

    # 门关着 ⇒ **逐栋各走一趟、各翻一发**（老行为是"走到最近一栋、翻全部"一发，
    #   现在 C# 只翻 4 格内 ⇒ 站在 A 门口叫"全翻"根本翻不到 B）。
    _r_open, _n_open, _td_open, _eps_open = _doors_hit(800, "放牧（开棚门）", doors_open=False)
    _post_open = _doors_posts()
    res.append(ok("🚪 敲「放牧」⇒ 真打 `/toggle_doors`（**POST**）、**逐栋各一发**"
                  "（2 栋 = 2 发；`/opendoors` 这个端点不存在）",
                  _n_open == 2 and {e for e in _td_open} == {("POST", "/toggle_doors")}
                  and not any("/opendoors" in str(e) for e in _eps_open), _td_open))
    res.append(ok("🚪 **每一发都带 `doorX/doorY` 点名**（同名两栋靠门坐标分开，不靠名字）"
                  "—— 两发点的正是那两扇**动物小门**，且**互不相同**",
                  len(_post_open) == 2
                  and all(isinstance(b.get("doorX"), int) and isinstance(b.get("doorY"), int)
                          for b in _post_open)
                  and {(b["doorX"], b["doorY"]) for b in _post_open} == {(45, 20), (53, 20)},
                  _post_open))
    res.append(ok("🚪 敲「放牧」⇒ 回执**逐栋报执行后的门态** + 目标态（『门现在是：…开』）",
                  "门现在是：" in _r_open and "Deluxe Coop 开" in _r_open
                  and "Deluxe Barn 开" in _r_open and "目标=全开" in _r_open,
                  _r_open[:260]))
    # 🚪 **同名两栋**（两个 Deluxe Coop）：名字分不开 ⇒ 只能靠门坐标。这是这轮 C# 加 `doorX/doorY`
    #    点名的**唯一理由**，所以单独立一条 —— 名字并成一条就会少翻一栋、还报"全到位了"。
    _TWIN = [{"type": "Deluxe Coop", "x": 40, "y": 12, "width": 7, "height": 4,
              "doorX": 44, "doorY": 16, "animalDoorX": 45, "animalDoorY": 20},
             {"type": "Deluxe Coop", "x": 50, "y": 12, "width": 7, "height": 4,
              "doorX": 54, "doorY": 16, "animalDoorX": 55, "animalDoorY": 20}]
    _stub(loc="Farm", farm_buildings=_TWIN, doors_open=False,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    _htw = M._im_run("doors", {"walk": True})
    _post_tw = _doors_posts()
    res.append(ok("🚪 同名两栋：**两发各点一扇门**（(45,20) 与 (55,20)）—— 靠坐标分开，不靠名字",
                  len(_post_tw) == 2
                  and {(b["doorX"], b["doorY"]) for b in _post_tw} == {(45, 20), (55, 20)},
                  _post_tw))
    res.append(ok("🚪 同名两栋：回执里**两条都报**（`snap`/文案不许并成一条）",
                  _htw.get("snap") == [("Deluxe Coop", True), ("Deluxe Coop", True)]
                  and str(_htw.get("text")).count("Deluxe Coop 开") == 2, _htw.get("snap")))
    #    ⚠️ `walk=False` 得**人真站在门口**才有意义（C# 只翻 4 格内）⇒ `ai_xy` 放到 (55,19)。
    _stub(loc="Farm", farm_buildings=_TWIN, doors_open=True, ai_xy=(55, 19),
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    _honly = M._im_run("doors", {"walk": False, "only": [{"x": 55, "y": 20}]})
    _post_only = _doors_posts()
    res.append(ok("🚪 同名两栋：`only` 点**第二栋的门坐标** ⇒ 真的**只翻那一扇**"
                  "（第一栋不动：两栋名字一样，只能靠坐标认）",
                  len(_post_only) == 1 and (_post_only[0]["doorX"], _post_only[0]["doorY"]) == (55, 20)
                  and _honly.get("entries") == [{"name": "Deluxe Coop", "x": 55, "y": 20,
                                                 "state": False}]
                  and _honly.get("left") == [], (_post_only, _honly.get("entries"))))
    res.append(ok("🚪 两栋门态都**确认到了**（不许有「未确认」、也不许进「没翻成」）",
                  "未确认" not in _r_open and "没翻成" not in _r_open, _r_open[:260]))
    res.append(ok("🚪 回执**不许**再提「farm animals 摸一遍」（恒：关着门也能摸）",
                  "animals" not in _r_open, _r_open[:200]))
    res.append(ok("🚪 回执带**下一步**（反着来敲哪一下，op+参数都在）",
                  'farm(ops="doors")' in _r_open, _r_open[:200]))
    # 门本来就开着 ⇒ C# 纯翻转会把它关上 ⇒ exec 必须**再翻一次**收敛到目标态
    #   （第一趟 2 发 + 收敛趟 2 发 = 4 发；且**收敛趟也走位**，见下面那条）
    _r_open2, _n_open2, _, _ = _doors_hit(800, "放牧（开棚门）", doors_open=True)
    res.append(ok("🚪 门本来就开着时敲「放牧」⇒ **再翻一次收敛到全开**（C# 是纯翻转）"
                  "—— 两栋各翻两下 = 4 发，且最终两栋都报『开』",
                  _n_open2 == 4 and "Deluxe Coop 开" in _r_open2
                  and "Deluxe Barn 开" in _r_open2
                  and "目标=全开" in _r_open2 and "未确认" not in _r_open2,
                  (_n_open2, _r_open2[:260])))

    _r_close, _n_close, _, _ = _doors_hit(1900, "关棚门", doors_open=True, chore_animals=[])
    res.append(ok("🚪 敲「关棚门」⇒ 一次收敛，回执报『门现在是：…关』+ 目标=全关",
                  _n_close == 2 and "门现在是：" in _r_close
                  and "Deluxe Coop 关" in _r_close and "Deluxe Barn 关" in _r_close
                  and "目标=全关" in _r_close, (_n_close, _r_close[:260])))
    _r_close2, _n_close2, _, _ = _doors_hit(1900, "关棚门", doors_open=False, chore_animals=[])
    res.append(ok("🚪 门本来就关着时敲「关棚门」⇒ **再翻一次收敛到全关**"
                  "（两栋各两下 = 4 发，最终两栋都报『关』）",
                  _n_close2 == 4 and "Deluxe Coop 关" in _r_close2
                  and "Deluxe Barn 关" in _r_close2 and "未确认" not in _r_close2,
                  (_n_close2, _r_close2[:260])))

    # 🧭 收敛那一发**按门坐标点名**（不是 `walk=False` 再翻全部）——
    #    ⚠️ 老用例断言的是"第二下不走位"，那条**行为已经反了**：C# 只翻 4 格内 ⇒
    #       不走过去就翻不到，收敛趟**必须带 walk**；而且"再翻全部"会把刚翻好、就在旁边的
    #       那栋**翻回去**（来回翻，永远收敛不了）。
    #    造"**一开两关**"（小门坐标都不同）：目标=全开时第一趟只有**一栋**没到位
    #    ⇒ 收敛趟**只该带那一扇门**（带多了就是把刚翻好的翻回去）。
    _FARM3 = ([{"type": "Deluxe Coop", "x": 40, "y": 12, "width": 7, "height": 4,
                "doorX": 44, "doorY": 16, "animalDoorX": 45, "animalDoorY": 20,
                "animalDoorOpen": True},
               {"type": "Deluxe Barn", "x": 48, "y": 12, "width": 7, "height": 4,
                "doorX": 52, "doorY": 16, "animalDoorX": 53, "animalDoorY": 20,
                "animalDoorOpen": False},
               {"type": "Big Barn", "x": 56, "y": 12, "width": 7, "height": 4,
                "doorX": 60, "doorY": 16, "animalDoorX": 61, "animalDoorY": 20,
                "animalDoorOpen": False}])
    _run_log = []
    _orig_im_run = M._im_run

    def _rec_im_run(op, args):
        _run_log.append((op, dict(args or {})))
        return _orig_im_run(op, args)

    _stub(loc="Farm", farm_buildings=_FARM3, doors_open=False,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    M.intent(ops="show", kw={"n": 40})
    M._im_run = _rec_im_run
    try:
        _rc3 = M.intent(ops="do", kw={"code": str(_no_of("放牧（开棚门）"))})
    finally:
        M._im_run = _orig_im_run
    # ⚠️ 2026-10-04：第一趟现在**带 `want`**（域 op 也要方向/目标态 ⇒ 判据 `_doors_bad` 由服务器算好递回来，
    #    单子这边不再自己写一份 lambda）。断言跟着改成"`want` 在、`only` 不在"。
    res.append(ok("🚪 收敛：第一趟**不带 `only`**（全量逐栋走一遍，`walk=True`）",
                  bool(_run_log) and _run_log[0][0] == "doors"
                  and _run_log[0][1].get("walk") is True and "only" not in _run_log[0][1]
                  and _run_log[0][1].get("want") in ("open", "close"), _run_log))
    _only2 = (_run_log[1][1].get("only") if len(_run_log) > 1 else None)
    res.append(ok("🚪 收敛：第二发**只带没到位的那一扇门的坐标**（`only`）——"
                  "**不许**再翻全部（那会把刚翻好的两栋翻回去）",
                  len(_run_log) == 2 and _run_log[1][0] == "doors"
                  and _run_log[1][1].get("walk") is True
                  and [(o.get("x"), o.get("y")) for o in (_only2 or [])] == [(45, 20)],
                  _run_log))
    res.append(ok("🚪 收敛：第二发**没带**已经到位的两栋（53,20 与 61,20 不在名单里）+ "
                  "**没有** `walk=False` 那种「人已经站在门口」的旧写法",
                  bool(_only2) and {(o.get("x"), o.get("y")) for o in _only2}.isdisjoint(
                      {(53, 20), (61, 20)})
                  and _run_log[1][1].get("walk") is not False, _only2))
    res.append(ok("🚪 收敛：**逐栋走的是小门那一格**（三栋的动物小门 (45/53/61,20) ⇒ "
                  "每一趟的站格都挨着**某扇小门**，**不回退到人类门 (44/52/60,16)**）",
                  len(WALK_CALLS) == 4
                  and all(any(max(abs(wx - ax), abs(wy - ay)) == 1
                              for ax, ay in [(45, 20), (53, 20), (61, 20)])
                          for (_lc, wx, wy) in WALK_CALLS),
                  WALK_CALLS))
    res.append(ok("🚪 收敛：最终回执里那栋**如实报成『开』**（收敛真的翻回来了，不是嘴上说收敛）",
                  "门现在是：Deluxe Coop 开" in _rc3 and "目标=全开" in _rc3
                  and "没翻成" not in _rc3, _rc3[:260]))

    # ⚠️⚠️ 2026-10-01 真机逮到的洞：**收敛那发会把走位那条事实盖掉** ——
    #    人真走到了门口（`[walk] … 到位`），可 AI 看到的回执里一个字都没提。
    #    「翻完了却不说人到没到门口」两头都是谎 ⇒ 两条用例各钉一头（走到 / 没走到）。
    _r_conv_ok, _n_conv_ok, _, _ = _doors_hit(800, "放牧（开棚门）", doors_open=True)
    res.append(ok("🚪 **收敛后**最终回执里**仍含走位那行**（走到了：带棚名 + 门坐标）",
                  _r_conv_ok.count("🚶 已走到") == 4 and "Deluxe Coop" in _r_conv_ok
                  and "Deluxe Barn" in _r_conv_ok and "旁边" in _r_conv_ok, _r_conv_ok[:260]))
    res.append(ok("🚪 收敛趟**也走位**（`len(WALK_CALLS)` = 4：两栋 × 两趟 —— 老行为是 1 次）",
                  _n_conv_ok == 4 and len(WALK_CALLS) == 4, (WALK_CALLS, _n_conv_ok)))
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
    res.append(ok("🚪 而且**逐栋带门坐标**（`entries`）—— 收敛要靠它点名，字典会并同名两栋",
                  [ (e.get("name"), e.get("x"), e.get("y"), e.get("state"))
                    for e in (_hits_open.get("entries") or []) ]
                  == [("Deluxe Coop", 44, 16, True), ("Deluxe Barn", 52, 16, True)]
                  and _hits_open.get("left") == [], _hits_open.get("entries")))
    res.append(ok("🚪 `walk=False` ⇒ **不调走位**（收敛的第二下用它）",
                  isinstance(_hits_flip, dict) and "🚶" not in str(_hits_flip.get("text")),
                  _hits_flip))

    # 🧭 够不着 ⇒ 门态记 `None`、回执里**点名那栋** + 说**没翻成**，绝不许说成"翻了/门是开是关"。
    #    造法照真机：走不到 ⇒ 人留在远处 ⇒ C# 那 4 格闸把它丢进 `skipped(reason=too_far)`。
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, doors_open=False, walk_ok=False,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    _hfar = M._im_run("doors", {"walk": True})
    _hfar_txt = str(_hfar.get("text"))
    _stub(loc="Farm", farm_buildings=FARM_BUILDINGS, doors_open=False, walk_ok=False,
          time_dict={"timeOfDay": 800, "season": "summer", "weather": 0})
    M.intent(ops="show", kw={"n": 40})
    _r_far = M.intent(ops="do", kw={"code": str(_no_of("放牧（开棚门）"))})
    res.append(ok("🚪 够不着（`skipped reason=too_far`）⇒ 门态**记 `None`**、进 `left`，"
                  "**不许**报成「门现在是开的/关的」",
                  _hfar.get("doors") == {"Deluxe Coop": None, "Deluxe Barn": None}
                  and [x.get("reason") for x in (_hfar.get("left") or [])] == ["too_far", "too_far"],
                  _hfar.get("doors")))
    res.append(ok("🚪 够不着 ⇒ 回执里**点名那一栋**（棚名 + 门坐标）+ 明说「**没翻成**」+ 给原因",
                  "没翻成" in _hfar_txt and "Deluxe Coop(44,16)" in _hfar_txt
                  and "Deluxe Barn(52,16)" in _hfar_txt and "too_far" in _hfar_txt,
                  _hfar_txt[:300]))
    res.append(ok("🚪 够不着时**不许**出现「门现在是：… 开/关」（那两栋都没确认过）",
                  "门现在是：Deluxe Coop 开" not in _r_far
                  and "门现在是：Deluxe Coop 关" not in _r_far
                  and "未确认" in _r_far and "没翻成" in _r_far, _r_far[:260]))

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
                hoe=True, season="spring", day=16, trash_cans=None, cola=None, nuts=None):
        # ⚠️ 日期默认 **春 16**（浆果窗口内）—— 浆果那笔账现在**只在浆果季**才给
        #    （见下面 `_BERRY_WINDOWS` 那两条用例：2026-10-02 恒「现在是夏天，不会有的」）。
        # ⚠️ 走**真路径** `_im_ctx()`（不是手搓 state/surr 递给 `_im_chores`）——
        #    第一版手搓，`api.has_item`/`api._ai_get("/crab_pots")` 两个口子**没桩到**
        #    ⇒ "没带锄头"那条假红、蟹笼那笔账也拿不到（自验当场逮到）。
        _stub(chore_tiles=chore_tiles, crab_ready=crab_ready, ore_pan=ore_pan,
              chore_animals=(animals if animals is not None else []),
              trash_cans=trash_cans, cola=cola, nuts=nuts,
              time_dict={"timeOfDay": 900, "season": season, "dayOfMonth": day,
                         "weather": weather})
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
    # 🍓🍓 2026-10-02 恒真机：「**不对啊，现在是夏天，不会有的**」—— 那天农场报了「🍓浆果灌木×2」，
    #      而 `bushBloom` 只是游戏那个 `Bush.tileSheetOffset==1`（茶树丛/核桃丛的"有货"也是这一帧）
    #      ⇒ 判据收紧成"**只有浆果季才算浆果**"。这两条就是那天的回归。
    _ch_sum = _chores([_T_BUSH, _T_SPOT], weather=0, hoe=True,
                      season="summer", day=17)
    res.append(ok("🍓 **夏天（17 日）+ 灌木贴图是有货那帧 ⇒ 一个字都不许说浆果**"
                  "（恒：「现在是夏天，不会有的」——那天我报了 2 棵）",
                  "berry" not in _ch_sum, _ch_sum))
    _ch_fall = _chores([_T_BUSH], weather=0, hoe=True, season="fall", day=10)
    res.append(ok("🍓 秋天 10 日（黑莓窗口 8~11）⇒ 说浆果（不是一刀切掉这个功能）",
                  _ch_fall.get("berry") == 1, _ch_fall))
    # 🆕 203f：C# 补了 `bushSize`/`bushInSeason` ⇒ 判据从"日历窗口"**升级成问游戏**
    #    （老 DLL 形状的夹具 = `_T_BUSH`，上面那几条一个字没改 ⇒ 兼容性也在这一屏里验着）。
    _T_TEA = {"x": 75, "y": 10, "terrain": "Bush", "bushBloom": True, "bushSize": 3,
              "bushInSeason": True}
    _T_WAL = {"x": 76, "y": 10, "terrain": "Bush", "bushBloom": True, "bushSize": 4,
              "bushInSeason": True}     # ⚠️ size4 的 `inBloom()` **就是** `readyForHarvest()`
    #                                       ⇒ "帧亮着但不在季"对核桃丛是**不可能的一格**（别拿假数据喂自验）
    _T_BERRY_G = {"x": 77, "y": 10, "terrain": "Bush", "bushBloom": True, "bushSize": 0,
                  "bushInSeason": True}
    _T_BERRY_NS = {"x": 78, "y": 10, "terrain": "Bush", "bushBloom": True, "bushSize": 0}
    _c_tea = M._forage_counts([_T_TEA], True, True)      # 就算在浆果季
    res.append(ok("🍵 茶树丛(size3)：**在浆果季也不算浆果**（病根就是这一条 —— 帧字段分不出它）",
                  _c_tea.get("bush") == 0 and _c_tea.get("bush_bloom") == 1, _c_tea))
    _c_wal = M._forage_counts([_T_WAL], True, False)
    res.append(ok("🌰 核桃丛(size4)：不算浆果，但记进 `walnut_bush`（姜岛那条例外要用它）",
                  _c_wal.get("bush") == 0 and _c_wal.get("walnut_bush") == 1
                  and M._walnut_bush_count(_c_wal) == 1, _c_wal))
    _c_g = M._forage_counts([_T_BERRY_G], True, False)   # 日历说不在季、游戏说在季
    res.append(ok("🍓 有 `bushInSeason` ⇒ **听游戏的**（日历说不在季也算）",
                  _c_g.get("bush") == 1 and _c_g.get("walnut_bush") == 0, _c_g))
    _c_ns = M._forage_counts([_T_BERRY_NS], True, False)  # 半新 DLL：有 size 没 season
    res.append(ok("🍓 只有 `bushSize` 没 `bushInSeason`（半新 DLL）⇒ 退回日历窗口（不在季就不算）",
                  _c_ns.get("bush") == 0, _c_ns))
    _c_oldshape = M._forage_counts([_T_BUSH], True, True)
    res.append(ok("🍓 老 DLL 瓦片（`bushSize` 都没这键）⇒ **原样退回**老口径（帧 + 日历窗口）",
                  _c_oldshape.get("bush") == 1 and _c_oldshape.get("bush_bloom") == 1
                  and M._walnut_bush_count(_c_oldshape) == 1, _c_oldshape))
    res.append(ok("🍵 茶树丛**整行不出现**（单子那边也一样：`_im_chores` 不给 berry）",
                  "berry" not in _chores([_T_TEA], season="spring", day=16)))
    _ch_wal2 = _chores([_T_WAL], season="summer", day=17,
                       nuts=[{"kind": "bush", "taken": False}])
    res.append(ok("🌰 非浆果季 + 本图还有挂着的核桃 ⇒ 那行照给（姜岛：摇到没有为止）",
                  _ch_wal2.get("berry") == 1, _ch_wal2))
    # 🆕 203g（恒：「**茶树也值得摇**，不过确实不是同一件事」+「**对哦……好多果树也可以摇**」）：
    #    三种（浆果/茶叶/果子）**是同一次动作** ⇒ **只留一行**，行文按本图有什么自己念。
    _TEATA = {"x": 79, "y": 10, "terrain": "Bush", "bushBloom": True, "bushSize": 3,
              "bushInSeason": True}
    _FRUIT = {"x": 12, "y": 7, "terrain": "FruitTree", "passable": True,
              "fruitCount": 3, "fruitName": "香蕉"}
    _c_tea2 = M._forage_counts([_TEATA], True, False)
    res.append(ok("🍵 `_forage_counts`：茶树丛(size3) 现在**记进 `tea`**（旧版它只是被排除、什么都不记）",
                  _c_tea2.get("tea") == 1 and _c_tea2.get("bush") == 0, _c_tea2))
    _c_fr = M._forage_counts([_FRUIT], True, False)
    res.append(ok("🍎 `_forage_counts`：果树按 `fruitCount` 记账（几棵 + 一共几个果）",
                  _c_fr.get("fruit_tree") == 1 and _c_fr.get("fruit_n") == 3
                  and _c_fr.get("bush") == 0, _c_fr))
    _ch_tf = _chores([_TEATA, _FRUIT], season="summer", day=17)
    res.append(ok("🔌 `_im_chores` 把 tea / fruit_tree 也递给单子（跟浆果同一笔账一起给）",
                  _ch_tf.get("tea") == 1 and _ch_tf.get("fruit_tree") == 1
                  and _ch_tf.get("fruit_n") == 3, _ch_tf))
    _Ltf, _Ltf_txt = (None, "")   # ⚠️ 标签那两条断言要等 `_labels2` 定义（在下面垃圾桶那段之后）⇒ 挪过去
    # 🆕 203g：`bushShakeable` = 游戏 `Bush.shake()` 的原条件（tier ①）——**它说了算**
    _TOWN = {"x": 80, "y": 10, "terrain": "Bush", "bushBloom": True, "bushSize": 0,
             "bushInSeason": True, "bushShakeable": False}     # Town 装饰丛：帧亮、在季、但不给东西
    _c_town = M._forage_counts([_TOWN], True, True)
    res.append(ok("🏛️ 有 `bushShakeable=false` ⇒ **不摇**（Town 装饰丛：帧亮+在季也白摇，"
                  "`!townBush` 这项只有游戏知道）",
                  _c_town.get("bush") == 0, _c_town))
    _SHK = dict(_T_BERRY_G, bushShakeable=True, bushInSeason=False)
    _c_shk = M._forage_counts([_SHK], True, False)
    res.append(ok("🎯 有 `bushShakeable=true` ⇒ **就算 `bushInSeason` 是假也摇**（tier ① 优先，问游戏最准）",
                  _c_shk.get("bush") == 1, _c_shk))

    # ㉔b 203j 🍵 **盆栽茶树**：那丛住在 `IndoorPot.bush` 里 ⇒ `terrain` **不是** "Bush"
    #     ⇒ 老判据（只认 `terrain=="Bush"`）把农场主屋/温室那两排茶树**整个漏掉**。
    #     C# 新报 `bushInPot:true`（同一套 `bushSize/bushInSeason/bushShakeable`）。
    #     ⚠️ 盆栽**不记 `bush_bloom`**：那个计数是"地形灌木贴图那一帧"，`_walnut_bush_count()`
    #        拿它当老 DLL 的退化路径 ⇒ 混进盆栽会把"姜岛还有核桃丛"判错。
    _POT_OK = {"x": 4, "y": 9, "object": "Garden Pot", "objId": "(BC)62",
               "bushInPot": True, "bushSize": 3, "bushInSeason": True, "bushShakeable": True}
    _POT_WAIT = dict(_POT_OK, bushShakeable=False)
    _WINTER12 = {"timeOfDay": 900, "season": "winter", "dayOfMonth": 12, "weather": 0}
    _c_pot = M._forage_counts([_POT_OK], True, False)
    res.append(ok("🍵 盆栽茶树（`bushInPot` + 摇得出来）⇒ `tea==1` + `tea_pot==1`"
                  "（`terrain` 不是 Bush 也认），且**不记 `bush_bloom`**",
                  _c_pot.get("tea") == 1 and _c_pot.get("tea_pot") == 1
                  and _c_pot.get("bush") == 0 and _c_pot.get("bush_bloom") == 0, _c_pot))
    _c_potw = M._forage_counts([_POT_WAIT], True, False)
    res.append(ok("🍵 盆栽茶树**摇不出来**（`bushShakeable=false`）⇒ 不进 `tea`、"
                  "**单独记 `tea_wait==1`**（别沉默：沉默 = AI 以为这图没茶树）",
                  _c_potw.get("tea") == 0 and _c_potw.get("tea_pot") == 0
                  and _c_potw.get("tea_wait") == 1 and _c_potw.get("bush_bloom") == 0, _c_potw))
    # 状态条那一行：摇不出来的也要**报出来**（`_forage_summary` 走真路径，`surroundings` 是桩）
    _old_fl = M._last_forage_loc
    _old_hi = M.api.has_item
    M._last_forage_loc = None
    M.api.has_item = lambda n: False
    try:
        _stub(surr_tiles=[_POT_WAIT], time_dict=_WINTER12)
        _sum_wait = M._forage_summary(time_dict=dict(_WINTER12))
        M._last_forage_loc = None
        _stub(surr_tiles=[_POT_OK], time_dict=_WINTER12)
        _sum_ok = M._forage_summary(time_dict=dict(_WINTER12))
    finally:
        M._last_forage_loc = _old_fl
        M.api.has_item = _old_hi
    res.append(ok("🍵 `_forage_summary`：摇不出来时**多报一句**「还没好」+ 游戏原条件"
                  "（别让 AI 以为这图没茶树）",
                  "还没好" in _sum_wait and "22号" in _sum_wait, _sum_wait))
    res.append(ok("🍵 `_forage_summary`：摇得出来时**不报「还没好」**，且标出「盆栽 M」",
                  "还没好" not in _sum_ok and "茶树丛×1" in _sum_ok and "盆栽1" in _sum_ok,
                  _sum_ok))
    _ch_potw = _chores([_POT_WAIT], season="winter", day=12)
    res.append(ok("🍵 `_im_chores`：`tea_wait` **单独成一笔账**（跟 `tea` 是两笔，"
                  "而且**不会带出 `berry`**）",
                  _ch_potw.get("tea_wait") == 1 and not _ch_potw.get("tea")
                  and not _ch_potw.get("berry"), _ch_potw))
    # 单子那两行：只有 `tea_wait` ⇒ **一行都不出现**，但**理由栏必须说清为什么**
    _Lw = _show(chore_tiles=[_POT_WAIT], time_dict=_WINTER12)
    _stub(chore_tiles=[_POT_WAIT], chore_animals=[], time_dict=_WINTER12)
    _old_hi2 = M.api.has_item
    M.api.has_item = lambda n: False          # ⚠️ 别让"有没有锄头"这条真去打 `/state`
    try:
        _c_wait = M._im_ctx()
    finally:
        M.api.has_item = _old_hi2
    _why_wait = M.intent_menu._shake_reason(_c_wait, None)
    res.append(ok("🍵 只有 `tea_wait` ⇒ 「摇 树上的」那行**不进单子**（摇不出来就不给这行）",
                  not [x for x in _Lw if x.startswith("摇")], _Lw))
    res.append(ok("🍵 但理由栏**非空**且明说「**摇不出茶叶**」+ 游戏原条件"
                  "（沉默会被读成「这图没茶树」，恒 2026-10-03 当天就问过）",
                  bool(str(_why_wait).strip()) and "摇不出茶叶" in _why_wait
                  and "22 号" in _why_wait, _why_wait))
    res.append(ok("🍵 而且这一行的 `can` 仍是 **CAN_NO**（理由栏有话说 ≠ 这行能做）",
                  M.intent_menu._VERB_BY_KEY["berry"].can(_c_wait, None)
                  is M.intent_menu.CAN_NO))
    # 🍓 `berry_run.scan_targets`：盆栽**也收**（老判据 `_terr != "Bush"` 会把它整条漏掉）。
    #    ⚠️ 全打桩：`berry_run` 是独立脚本（自己 `requests`）⇒ 换掉它模块里的 `requests`，
    #       并用 `--dry-run` 拿"扫到几处"（扫完就 return，不走位、不 interact）。
    import io as _io
    import contextlib as _ctx
    _saved_argv2 = sys.argv
    sys.argv = ["berry_run.py", "--dry-run"]
    try:
        import berry_run as _BR
    finally:
        sys.argv = _saved_argv2
    _BR.args.dry_run = True                      # 双保险：绝不进"真摇"那段循环

    class _BRResp:
        def __init__(self, d):
            self._d = d

        def json(self):
            return self._d

    class _BRReq:
        """桩：只回答 `berry_run` 会问的那几个端点（**一个字节都不出网**）。"""

        def __init__(self, tiles):
            self._tiles = tiles

        def get(self, url, *a, **k):
            if url.endswith("/status"):
                return _BRResp({"worldReady": True})
            if url.endswith("/surroundings"):
                return _BRResp({"location": "Greenhouse", "tiles": self._tiles})
            if url.endswith("/nuts"):
                return _BRResp({"ok": True, "nuts": []})
            return _BRResp({"location": {"name": "Greenhouse"},
                            "player": {"x": 4, "y": 10}, "inventory": [],
                            "time": {"season": "spring", "dayOfMonth": 16}})

        def post(self, url, *a, **k):
            return _BRResp({"ok": True})

    def _br_run(tiles):
        _old_req = _BR.requests
        _buf = _io.StringIO()
        _BR.requests = _BRReq(tiles)
        try:
            with _ctx.redirect_stdout(_buf):
                _BR.main()
        finally:
            _BR.requests = _old_req
        return _buf.getvalue()

    _br_pot = _br_run([_POT_OK])
    _br_wait = _br_run([_POT_WAIT])
    res.append(ok("🍓 `berry_run`：**盆栽**（`bushInPot` + 摇得出来）也收进目标（kind=tea）"
                  "—— `terrain` 不是 Bush 不再被漏掉",
                  "该摇 1 处" in _br_pot and "茶树丛×1" in _br_pot, _br_pot[-160:]))
    res.append(ok("🍓 `berry_run`：盆栽**摇不出来** ⇒ 不收（不进目标、也不假装扫到了）",
                  "该摇 1 处" not in _br_wait and "没有该摇的东西" in _br_wait, _br_wait[-160:]))

    # ㉕ 203h 🍽️ 宠物碗：新 DLL 报得出"哪碗已经满了"（`/petbowl.bowls[].watered`）
    #    ⇒ **满了别白挥壶**（旧版只报第一个碗，只能靠天气一刀切，见 `_water_pet_bowls` 的 docstring）。
    _WC = [{"slotIndex": 1, "name": "Iridium Watering Can", "displayName": "铱水壶",
            "itemId": "(T)WateringCan", "stack": 1, "waterLeft": 40, "waterMax": 40}]
    _BOWLS = [{"type": "Pet Bowl", "x": 53, "y": 7, "doorX": 52, "doorY": 6},
              {"type": "Pet Bowl", "x": 34, "y": 26, "doorX": 33, "doorY": 25}]
    _old_wtc, _old_pos = M.api.walk_to_coord, M.api.position
    M.api.walk_to_coord = lambda *a, **k: None
    M.api.position = lambda *a, **k: {"ok": True}
    try:
        _stub(inv=_WC, farm_buildings=_BOWLS,
              pet_bowls=[{"x": 53, "y": 7, "watered": True}, {"x": 34, "y": 26, "watered": False}],
              time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 17, "weather": 0})
        _rep = M._water_pet_bowls()
        _rep_s = "\n".join(_rep)
        _tools = len([c for c in CALLS if c[1] == "/tool"])
        _stub(inv=_WC, farm_buildings=_BOWLS, pet_bowls=None,
              time_dict={"timeOfDay": 900, "season": "summer", "dayOfMonth": 17, "weather": 0})
        _rep_old = "\n".join(M._water_pet_bowls())
        _tools_old = len([c for c in CALLS if c[1] == "/tool"])
    finally:
        M.api.walk_to_coord, M.api.position = _old_wtc, _old_pos
    res.append(ok("🍽️ 新 DLL：**满的那个碗跳过**（报「已经是满的」）", "已经是满的" in _rep_s, _rep))
    res.append(ok("🍽️ 只给没满的那 1 个碗挥壶（4 个碗的时代是挥 4 次 ⇒ 现在 2 个碗挥 1 次）",
                  _tools == 1 and _tools_old == 2, (_tools, _tools_old)))
    res.append(ok("🍽️ 老 DLL（没 `bowls` 键）⇒ **照旧两个都浇**（读不到 ≠ 都没满）",
                  "已经是满的" not in _rep_old, _rep_old))

    # ㉖ 203i 🚶🐄 `/passable` 现在**把挡路的那只点出来**（恒：「passable 原本不报动物吗？」+
    #    「我查过**牛羊两格、鸡鸭一格**」）—— 判据是 `Character.GetBoundingBox()` 与该格相交（游戏那套），
    #    **连占几格都算进去了**；旧口径只拿 `/animals` 的格坐标对 ⇒ 牛羊的第二格会被误判成硬阻挡。
    _old_an_at = api.animals_at
    try:
        api.animals_at = lambda *a, **k: {}          # 名单里"没有动物站在这一格"
        _stub(passable_ret={"ok": True, "passable": False, "x": 10, "y": 11,
                            "blocker": {"kind": "animal", "name": "牛牛", "x": 10, "y": 10}})
        _sp1 = api.soft_passable(10, 11)
        api.animals_at = lambda *a, **k: {(10, 11): "牛牛"}
        _stub(passable_ret={"ok": True, "passable": False, "x": 10, "y": 11})   # 老 DLL：没 blocker 键
        _sp2 = api.soft_passable(10, 11)
        _stub(passable_ret={"ok": True, "passable": False, "x": 10, "y": 11, "blocker": None})
        _sp3 = api.soft_passable(10, 11)             # 新 DLL 明说没人挡 ⇒ 硬阻挡
        _stub(passable_ret={"ok": True, "passable": True, "x": 10, "y": 11,
                            "blocker": {"kind": "animal", "name": "牛牛", "x": 10, "y": 10}})
        _sp4 = api.soft_passable(10, 11)
    finally:
        api.animals_at = _old_an_at
    res.append(ok("🐄 新 DLL：牛站在 (10,10) 挡住 (10,11) ⇒ **游戏点名的那只**直接算软阻挡（占两格也对得上）",
                  _sp1 == (True, "路上有只动物（牛牛）挡了道，挤开了"), _sp1))
    res.append(ok("🐄 老 DLL（没 `blocker` 键）⇒ 退回问 `/animals` 名单（行为不变）",
                  _sp2 == (True, "路上有只动物（牛牛）挡了道，挤开了"), _sp2))
    res.append(ok("🧱 新 DLL 说 `blocker=null`（没人挡）⇒ **就是硬阻挡**，不再多问一发名单",
                  _sp3 == (False, ""), _sp3))
    res.append(ok("🚶 能走就直接过（`blocker` 有值也不管）", _sp4 == (True, ""), _sp4))

    # 👹 2026-10-03 恒：「**怪给一个不可穿行吧**，碰到怪或者站在怪上的情况还是比较频繁的」
    #    + 优先级「**绕过怪 ＞ 走路被挡穿过 npc ＞ 马**」。
    #    C# 侧：`IsTilePassable` 现在把**怪**算实心（`GetMonsterTiles`，包围盒相交）；
    #    NPC/马/宠物**故意不算**（穿过就够用）。消费侧：怪 = 硬阻挡 + **点名 + 给下一步**。
    try:
        api.animals_at = lambda *a, **k: {}
        _stub(passable_ret={"ok": True, "passable": False, "x": 10, "y": 11,
                            "blocker": {"kind": "monster", "name": "史莱姆", "x": 10, "y": 11}})
        _sp5 = api.soft_passable(10, 11)
        # 🧑 NPC 的**真实形状**：C# 不把 NPC 算实心 ⇒ `/passable` 回 `passable=true`（AI 直接走过去）
        _stub(passable_ret={"ok": True, "passable": True, "x": 10, "y": 11})
        _sp6 = api.soft_passable(10, 11)
    finally:
        api.animals_at = _old_an_at
    res.append(ok("👹 那格站着**怪** ⇒ **硬阻挡（不可穿行）**，而且**点名 + 让 AI 绕开**（别让它以为是堵墙）",
                  _sp5[0] is False and "史莱姆" in _sp5[1] and "绕开" in _sp5[1], _sp5))
    res.append(ok("🧑 NPC/马**照旧穿过**（恒排的第二/三档：C# 不算它们实心 ⇒ `passable=true`）",
                  _sp6 == (True, ""), _sp6))
    # 👹 源码断言：判据长在 C# 那处就别只测消费侧 —— 钉死"怪算实心"、并防止以后有人"顺手补全" NPC/马。
    try:
        _i0 = _src.index("private bool IsTilePassable(")
        _i1 = _src.index("\n    private ", _i0 + 10)
        _ip = _src[_i0:_i1]
    except ValueError:
        _ip = ""
    res.append(ok("👹 C# `IsTilePassable` 把**怪**算实心（`GetMonsterTiles(location).Contains(tile)`）",
                  "GetMonsterTiles(location).Contains(tile)" in _ip, _ip[:160]))
    res.append(ok("🧑🐴 C# **故意不算** NPC/马/宠物（恒排的序：绕过怪 ＞ 穿过 npc ＞ 马）——别顺手补全",
                  bool(_ip) and "GetNpcTiles" not in _ip and "Horse" not in _ip, ""))
    # ⚠️ **认不出的季节必须直接不算** —— 早先 `get(s, (0,0))` 会让 `(None,None)` 落进
    #    `0<=0<=0` ⇒ **返回 True**（"不知道 ⇒ 当在季"），正好反了（自验当场逮到）。
    import calendar_data as _cd
    res.append(ok("🍓 季节/日期**读不到** ⇒ 不算浆果季（不是「不知道就当真」）",
                  _cd.in_berry_season(None, None) is False
                  and _cd.in_berry_season("winter", 10) is False
                  and _cd.in_berry_season("spring", 0) is False))
    _br_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "berry_run.py"),
                   encoding="utf-8").read()
    res.append(ok("🍓 而且**执行侧用的是同一份窗口**（`berry_run.py` import `calendar_data`，"
                  "不是自己抄一份 15~18/8~11）",
                  "import calendar_data" in _br_src
                  and "calendar_data.in_berry_season" in _br_src))
    res.append(ok("🌿 苔藓：**真扫到了苔藓目标就给**（非绿雨天、没开设置也行 —— "
                  "恒 2026-10-02「农场有些树可以刮苔藓」，那天农场躺着 50 棵，老规矩一个字都不报）",
                  _ch.get("moss") == 1, _ch))
    _ch2 = _chores([_T_BUSH, _T_SPOT], weather=0, hoe=True)
    res.append(ok("🌿 但**一个苔藓目标都没有 + 非绿雨天 ⇒ 不给**（那行=待办，没活不出现）",
                  "moss" not in _ch2, _ch2))
    M._moss_cfg["expose_all_days"] = True
    res.append(ok("🌿 `settings moss on` 仍然有效：**扫不到也只是不显示这行**，"
                  "它的作用是让 `moss_run` 肯去跑一趟（扫描半径没覆盖到时的人工兜底）",
                  M._moss_visible({}, False) is True))
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

    # ── 🗑️ 翻垃圾桶（恒 2026-10-02：「**捡垃圾可以上**」）──
    _ch_trash = _chores([_T_BUSH], trash_cans=[(13, 86), (19, 89)])
    res.append(ok("🗑️ `Ctx.chores` 从 `/scan` 的 `Garbage` 瓦片数出本图桶数（垃圾桶不在 objects 里）",
                  _ch_trash.get("garbage") == 2, _ch_trash))
    res.append(ok("🗑️ 本图没桶 ⇒ 那笔账不给（别给一行按了没反应的）",
                  "garbage" not in _chores([_T_BUSH]), _chores([_T_BUSH])))
    # 执行侧：`_im_run` 认 `garbage`，且调的是现成的 `trash_run()`（不自己打端点）
    _tr_orig = M.trash_run
    M.trash_run = lambda *a, **k: "🗑️（桩：trash_run 跑了）"
    try:
        _rtr = M._im_run("garbage", {})
    finally:
        M.trash_run = _tr_orig
    res.append(ok("🗑️ `_im_run` 认 `garbage`，走的是现成的 `trash_run()`",
                  isinstance(_rtr, dict) and "trash_run 跑了" in str(_rtr.get("text")), _rtr))

    # ── 🥤 可乐机（恒 2026-10-02：「**不用了，不要加状态条了，上单吧**」）──
    #    判据**不问地图名**：Action 瓦片里写着 `ColaMachine` 就是有（跟垃圾桶共用同一份 `/scan` 缓存）。
    _stub(cola=[(37, 17), (38, 17)])
    res.append(ok("🥤 可乐机从 Action 瓦片里认出来，取**右半台**（真机 Saloon 是 (37,17)+(38,17)）",
                  M._cola_machine_here("Saloon") == (38, 17), M._cola_machine_here("Saloon")))
    res.append(ok("🥤 两格机器只打**一次** `/scan`（两个消费点共用一份缓存）",
                  len([c for c in CALLS if c[1] == "/scan"]) == 1,
                  [c[1] for c in CALLS if c[1] == "/scan"]))
    _ch_cola = _chores([_T_BUSH], cola=[(37, 17), (38, 17)])
    res.append(ok("🥤 `Ctx.chores[\"cola\"]` 带着机器坐标（右半台）",
                  (_ch_cola.get("cola") or {}).get("x") == 38
                  and (_ch_cola.get("cola") or {}).get("y") == 17, _ch_cola))
    res.append(ok("🥤 本图没机器 ⇒ 那笔账不给", "cola" not in _chores([_T_BUSH])))
    # 执行侧：`_im_run` 认 `cola`，走专用小流程 `_cola_buy()`（不自己打端点）
    _cb_orig = M._cola_buy
    M._cola_buy = lambda *a, **k: "🥤（桩：_cola_buy 跑了）"
    try:
        _rcb = M._im_run("cola", {})
    finally:
        M._cola_buy = _cb_orig
    res.append(ok("🥤 `_im_run` 认 `cola`，走的是 `_cola_buy()`",
                  isinstance(_rcb, dict) and "_cola_buy 跑了" in str(_rcb.get("text")), _rcb))

    # 单子那 6 行：有账就出现、执行**只调现成 op 且不带参数**
    def _labels2(**kw):
        _stub(**kw)
        _out = M.intent(ops="show", kw={"n": 40})
        return [(r.label or "") for r in M.intent_menu._LAST_ROWS], _out

    # ⚠️ 2026-10-02：原来这个夹具写的是 `season="summer", weather=7` —— 那是**不可能的一格**：
    #    「摇 浆果丛」只在浆果季（春15~18/秋8~11）给、而绿雨天只在夏天 ⇒ 这两行**真机上永远不会
    #    同时出现**。拿"不可能的状态"喂自验正是本项目的老病（假数据一路绿灯）⇒ 改成
    #    **春 16 的晴天 + 图上有苔藓树**（新规矩下"真扫到苔藓"就给这行，完全真实）。
    _L, _Ltxt = _labels2(chore_tiles=[_T_BUSH, _T_SPOT, _T_MOSS], crab_ready=4, ore_pan=_PAN,
                         chore_animals=_MOO["animals"], inv=_HOE,
                         time_dict={"timeOfDay": 900, "season": "spring", "dayOfMonth": 16,
                                    "weather": 0})
    for _lab in ("摇 浆果丛", "挖 远古斑点", "刮 苔藓", "收 蟹笼", "淘 金", "挤奶 / 剪毛"):
        res.append(ok(f"🌿 单子上出现「{_lab}」", _lab in _L, (_L, _Ltxt[:200])))
    _L0, _ = _labels2(inv=_HOE, time_dict={"timeOfDay": 900, "season": "summer", "weather": 0})
    res.append(ok("🌿 一件都推不出来 ⇒ **6 行全不出现**（宁缺勿编）",
                  not [x for x in _L0 if x in ("摇 浆果丛", "挖 远古斑点", "刮 苔藓",
                                               "收 蟹笼", "淘 金", "挤奶 / 剪毛")], _L0))
    # 🗑️ 垃圾桶那两行（`_labels2` 在这儿才定义 ⇒ 断言放这儿）
    _Lt, _Lt_txt = _labels2(chore_tiles=[_T_BUSH], trash_cans=[(13, 86)])
    res.append(ok("🗑️ 单子上出现「翻垃圾桶」", "翻垃圾桶" in _Lt, _Lt))
    res.append(ok("🗑️ 老 DLL（报不出翻没翻过）⇒ 理由栏退回「每天每桶一次」（**不许**说成「还有 N 个要翻」）",
                  "每天每桶一次" in _Lt_txt, _Lt_txt[:220]))
    _Lt2, _ = _labels2(chore_tiles=[_T_BUSH])
    res.append(ok("🗑️ 本图没桶 ⇒ 单子上**没有**那一行", "翻垃圾桶" not in _Lt2, _Lt2))
    # 🆕 203f：游戏那边现在报得出"今天翻过没有"了（C# `/scan.garbageChecked` ← `CheckedGarbage`）
    #    ⇒ 那行的账从"本图有 N 个桶"升级成"**今天还剩 M 个没翻**"，全翻完就整行不出现。
    _stub(trash_cans=[(13, 86), (19, 89)])            # 不传 trash_checked = 老 DLL 形状
    _tot_old, _left_old = M._trash_left_here("Town")
    _stub(trash_cans=[(13, 86), (19, 89)], trash_checked=[0, 1])
    _tot_new, _left_new = M._trash_left_here("Town")
    res.append(ok("🗑️ `_trash_left_here`：老 DLL（瓦片没 `garbageChecked`）⇒ 总数报得出、**剩几个 = None（不知道）**",
                  (_tot_old, _left_old) == (2, None), (_tot_old, _left_old)))
    res.append(ok("🗑️ `_trash_left_here`：新 DLL 两个都翻过 ⇒ (2, 0)",
                  (_tot_new, _left_new) == (2, 0), (_tot_new, _left_new)))
    _Lt3, _ = _labels2(chore_tiles=[_T_BUSH], trash_cans=[(13, 86), (19, 89)],
                       trash_checked=[0, 1])
    res.append(ok("🗑️ 两个桶**今天都翻过了** ⇒ 整行不出现（按了也是空的，别占单子）",
                  "翻垃圾桶" not in _Lt3, _Lt3))
    _Lt4, _Lt4txt = _labels2(chore_tiles=[_T_BUSH], trash_cans=[(13, 86), (19, 89)],
                             trash_checked=[0])
    res.append(ok("🗑️ 翻过 1 个、还剩 1 个 ⇒ 那行在，而且理由栏**说清「今天还没翻的 1 个」**",
                  "翻垃圾桶" in _Lt4 and "还没翻的 1 个" in _Lt4txt, _Lt4txt[:200]))
    _Lt5, _Lt5txt = _labels2(chore_tiles=[_T_BUSH], trash_cans=[(13, 86)], trash_checked=[])
    res.append(ok("🗑️ 新 DLL、一个都没翻（`garbageChecked=false`）⇒ 照旧出现（**假值 ≠ 读不到**）",
                  "翻垃圾桶" in _Lt5 and "还没翻的 1 个" in _Lt5txt, _Lt5txt[:200]))
    # 🍵🍎 203g：三种（浆果/茶叶/果子）是**同一次动作** ⇒ **只留一行**，行文按本图有什么自己念
    _SUM17 = {"timeOfDay": 900, "season": "summer", "dayOfMonth": 17, "weather": 0}
    _SPR16 = {"timeOfDay": 900, "season": "spring", "dayOfMonth": 16, "weather": 0}
    _Ltf, _Ltf_txt = _labels2(chore_tiles=[_TEATA, _FRUIT], time_dict=_SUM17)
    res.append(ok("🍵🍎 单子**只有一行**，标签按本图念：「摘 茶叶 + 摇 果树(摘果子)」",
                  any(("摘 茶叶" in x and "摇 果树" in x) for x in _Ltf), _Ltf))
    res.append(ok("🍎 理由栏明说「果子摇下来**在地上**，要再走上去捡」（别让 AI 以为会自己进包）",
                  "走上去捡" in _Ltf_txt, _Ltf_txt[:260]))
    _Lb_only, _ = _labels2(chore_tiles=[_T_BUSH], time_dict=_SPR16)
    res.append(ok("🍓 只有浆果时，标签**照旧是「摇 浆果丛」**（老习惯不断档）",
                  "摇 浆果丛" in _Lb_only, _Lb_only))
    _Lc, _Lc_txt = _labels2(chore_tiles=[_T_BUSH], cola=[(37, 17), (38, 17)])
    res.append(ok("🥤 单子上出现「买 Joja 可乐 (75g)」（**花钱的必须把价格写在行上**）",
                  any("Joja 可乐" in x and "75g" in x for x in _Lc), _Lc))
    res.append(ok("🥤 理由栏写到坐标 + 价格 + 能干嘛（谢恩最爱）",
                  "(38,17)" in _Lc_txt and "75g" in _Lc_txt and "谢恩" in _Lc_txt,
                  [x for x in _Lc_txt.splitlines() if "可乐" in x][:2]))
    res.append(ok("🥤 没机器 ⇒ 单子上**没有**那一行",
                  not any("Joja 可乐" in x for x in _labels2(chore_tiles=[_T_BUSH])[0])))
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
    res.append(ok("🚪 外面没动物 ⇒ 照常翻（**逐栋各一发**：2 栋 = 2 次 `/toggle_doors`）",
                  _n_in == 2, _n_in))
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
    res.append(ok("⚖️ 193：`摸 猫狗` 83 → **87**", _w("pets") == 87, _w("pets")))

    # 💧 2026-10-04 恒拍板上单：浇水（「浇没湿的有作物格子，不用圈地」）
    #    判据走**瓦片自己的 `watered`**（不走 caps：加字段要重编 C#），缺键 = MAYBE（宁缺勿编）。
    res.append(ok("💧 浇水行在册（权重 86：收 88 / 摸 87 之后，摸动物 84 之前）",
                  _w("water") == 86, _w("water")))
    _wc = M.intent_menu._water_can
    # ⚠️⚠️ 2026-10-04 **真机逮到的洞**（恒种的 32 格上古水果田）：真机上"没浇过的格"
    #    **根本没有 `watered` 键**（C# `ModEntry.cs:7314` = `if (watered) tile["watered"] = true;`
    #    —— 只在为真时才写键）⇒ 我第一版"缺键 ⇒ MAYBE"让这一行**结构性永不出现**。
    #    现在判据**照执行器 `water_crops.py:45` 那一行**写（缺键 = 没浇），所以下面的夹具
    #    故意**不带 `watered` 键**（= 真机那份形状），不是"False"。
    _D = {"terrain": "HoeDirt", "crop": "454", "cropName": "上古水果",
          "harvestable": False, "cropScythe": False}
    res.append(ok("💧 有作物 + **没浇过（真机那份形状：连 `watered` 键都没有）** ⇒ 该浇",
                  _wc(None, dict(_D)) is True))
    res.append(ok("💧 有作物 + `watered=true`（浇过了才有这个键）⇒ **不浇**",
                  _wc(None, dict(_D, watered=True)) is False))
    res.append(ok("💧 空地 / 没翻的地 ⇒ 不浇（执行器也不浇它们）",
                  _wc(None, {"watered": False}) is False
                  and _wc(None, {"terrain": "Grass", "crop": "454"}) is False))
    res.append(ok("💧 **已成熟 ⇒ 不浇**（执行器显式跳过 ⇒ 印出来就是按了不成）",
                  _wc(None, dict(_D, harvestable=True)) is False))
    # 🔌 防漂移：判据那一行**必须跟执行器同一把尺**（源码对读，不靠记）
    _wc_src = open(os.path.join(_here, "water_crops.py"), encoding="utf-8").read()
    res.append(ok("💧 判据与执行器同源：`water_crops.find_unwatered` 也是 `not t.get(\"watered\")`",
                  'not t.get("watered")' in _wc_src and 'terrain") == "HoeDirt"' in _wc_src))
    _w_call = {}
    _exec_w = M.intent_menu._exec_water
    try:
        _orig_run = M.intent_menu.__dict__.get("_im_run")
        M.intent_menu._exec_water(None, [{"crop": "24"}],
                                  lambda k, a: _w_call.update(key=k, args=a) or "🌱 浇完了（桩）")
    finally:
        pass
    res.append(ok("💧 exec 打的 op 是**无参**的 `water`（端点不吃半径/坐标，传了会被静默丢）",
                  _w_call.get("key") == "water" and _w_call.get("args") == {}, _w_call))

    # 🗿🐟 2026-10-04：**摸雕像**（恒拍板 (b)）+ **收鱼塘产出** 上单
    #    🗿 的"今天摸过没"**只能**来自 `/state.player.blessedByStatueToday`
    #       （游戏自己的 `Farmer.hasBeenBlessedByStatueToday`，每天重置）——
    #       老 DLL 没这个键 ⇒ MAYBE（宁缺勿编）；**绝不许**拿 `_statue_reminder` 的 shown 冒充
    #       （那个一调就把当天提醒吃掉，是"提醒过没"不是"摸过没"）。
    _Ctx = M.intent_menu.Ctx
    res.append(ok("🗿 摸雕像行在册（权重 56 = 顺手活那档最低：苔藓 58 之下、吃 50 之上）",
                  _w("statue") == 56, _w("statue")))
    _st_can = M.intent_menu._statue_can
    res.append(ok("🗿 场上有雕像 + 今天没摸过 ⇒ ✅",
                  _st_can(_Ctx(statue={"names": ["Statue Of Blessings"],
                                       "used_today": False}), {}) is True))
    res.append(ok("🗿 今天摸过了 ⇒ ❌（摸完当天那行就该消失，不然是催着重摸）",
                  _st_can(_Ctx(statue={"names": ["Statue Of Blessings"],
                                       "used_today": True}), {}) is False))
    res.append(ok("🗿 老 DLL 报不出「今天摸过没」⇒ **MAYBE**（不替游戏说「还没摸」）",
                  _st_can(_Ctx(statue={"names": ["Statue Of Blessings"],
                                       "used_today": None}), {}) is None))
    res.append(ok("🗿 场上没雕像 ⇒ ❌（`{}` = 那行不出现）",
                  _st_can(_Ctx(statue={}), {}) is False))
    _st_call = {}
    M.intent_menu._exec_statue(_Ctx(statue={"names": ["Statue Of Blessings"],
                                           "used_today": False}), [{}],
                               lambda k, a: _st_call.update(key=k, args=a) or "🗿 摸完雕像")
    res.append(ok("🗿 exec 打的 op 是**无参**的 `statue`（跟 `farm ops=statue` 同一个脚本）",
                  _st_call.get("key") == "statue" and _st_call.get("args") == {}, _st_call))
    # 服务器那层：雕像**零额外 HTTP**（从已经拿到的 `/machines` 认整图 + 从 `state` 读那一位）
    # ⚠️ 2026-10-04 恒：「**改成当前图有就报**」⇒ 判据从 `/surroundings` 30 格窗口换成
    #    `/machines`（整图 + 带 `location`）—— 真机站 (53,58)、雕像在 (74,16)（45 格）照样报。
    _ST_ROW = {"type": "Statue Of Blessings", "x": 74, "y": 16, "location": "Farm"}
    _stub(loc="Farm", machines=[_ST_ROW], blessed=False)
    _c_st = M._im_ctx()
    res.append(ok("🗿 服务器那层：从 **`/machines`（整图）**认出雕像（远在图那一头也报）+ 读 `blessedByStatueToday`",
                  _c_st.statue.get("names") == ["Statue Of Blessings"]
                  and _c_st.statue.get("tiles") == [[74, 16]]
                  and _c_st.statue.get("used_today") is False, _c_st.statue))
    _stub(loc="Farm", machines=[dict(_ST_ROW, location="Big Shed")], blessed=False)
    res.append(ok("🗿 **别的屋/别的图**的雕像不出这行（跨图走位执行器做不到 ⇒ 出了是假门）",
                  M._im_ctx().statue == {}, M._im_ctx().statue))
    _stub(loc="Farm", machines=[_ST_ROW], blessed=True)
    res.append(ok("🗿 服务器那层：`blessedByStatueToday=true` ⇒ `used_today=True` ⇒ 单子不给那行",
                  M._im_ctx().statue.get("used_today") is True, M._im_ctx().statue))
    _stub(loc="Farm", machines=[_ST_ROW])       # 老 DLL：**不吐这个键**
    _c_old = M._im_ctx()
    res.append(ok("🗿 老 DLL（`/state.player` 没这个键）⇒ `used_today=None`（**不是 False**）",
                  _c_old.statue.get("used_today", "缺键") is None, _c_old.statue))
    _stub(loc="Farm")                           # 本图没有 Statue 类机器
    res.append(ok("🗿 服务器那层：本图没有雕像 ⇒ 账为空 `{}`（那行不出现）",
                  M._im_ctx().statue == {}, M._im_ctx().statue))
    # 🗿⚠️ **两座雕像的门不是一个**（2026-10-04 反编译 `Object.checkForAction` + 真机双向核实）：
    #    · 祝福雕像 ⇒ `Farmer.hasBeenBlessedByStatueToday`；
    #    · 矮人国王 ⇒ `!who.hasBuffWithNameContainingString("dwarfStatue")` —— **buff 门、
    #      不是"每天一次"**（buff 过期就又能摸）。
    #    真机实证：先摸祝福（那位=True）之后矮人国王**照样弹菜单给 buff**；
    #    ⇒ 拿祝福那位去拦矮人 = **白拦**（会把还能摸的那行藏掉）。
    _DK = {"type": "Statue Of The Dwarf King", "x": 5, "y": 4, "location": "SkullCave"}
    _stub(loc="SkullCave", machines=[_DK], blessed=True,
          buffs=[{"id": "dwarfStatue_3", "name": "矮人之王雕像"}])
    res.append(ok("🗿 矮人国王：身上**还挂着 `dwarfStatue` buff** ⇒ 已用过（那行不给）",
                  M._im_ctx().statue.get("used_today") is True, M._im_ctx().statue))
    _stub(loc="SkullCave", machines=[_DK], blessed=True)     # 祝福那位 True、但**没有** dwarf buff
    _c_dk = M._im_ctx().statue
    res.append(ok("🗿 矮人国王：**祝福那位是 true 也照样给行**（它不吃那个门 —— 真机实证）",
                  _c_dk.get("used_today") is False, _c_dk))
    _stub(loc="SkullCave", machines=[_DK, {"type": "Statue Of Blessings", "x": 7, "y": 7,
                                           "location": "SkullCave"}],
          blessed=False, buffs=[{"id": "dwarfStatue_3"}])
    _c_two = M._im_ctx().statue
    res.append(ok("🗿 两座同图：**只要还有一座能摸就给行**（矮人用过、祝福没过 ⇒ `used_today=False`）",
                  _c_two.get("used_today") is False and len(_c_two.get("statues") or []) == 2, _c_two))
    # 🗿 **图标选择题**（矮人国王雕像那屏，2026-10-04 恒：「按理来说要套一层选择题」）
    #    真机那屏（`SkullCave` (5,4)，我站 (5,5) 朝上）：`responses: null`、
    #    2 个真有文字的图标 + **2 个空文本诱饵**（`iconFronts`）。
    _ICON = {"ok": True, "open": True, "type": "ChooseFromIconsMenu", "isChoice": True,
             "responses": None,
             "buttons": [{"field": "icons", "name": "2", "hoverText": "找到煤炭的几率更高。",
                          "x": 504, "y": 394},
                         {"field": "icons", "name": "3", "hoverText": "炸弹无法对你造成伤害。",
                          "x": 776, "y": 394},
                         {"field": "iconFronts", "name": "", "hoverText": "", "x": 388, "y": 325},
                         {"field": "iconFronts", "name": "", "hoverText": "", "x": 660, "y": 325}]}
    _stub(menu="ChooseFromIconsMenu", menu_raw=_ICON)
    _md = M._im_menu_data(dict(STATE, activeMenu={"type": "ChooseFromIconsMenu"}))
    _opts = ((_md.get("choose") or {}).get("options")) or []
    res.append(ok("🗿 图标菜单摊成数据：**只认有文字的图标**（诱饵滤掉）",
                  [o["text"] for o in _opts] == ["找到煤炭的几率更高。", "炸弹无法对你造成伤害。"],
                  _md))
    res.append(ok("🗿 每条带 `key`（图标 name）+ 坐标 —— 回来能验「点的就是那个」",
                  bool(_opts) and _opts[1].get("key") == "3"
                  and (_opts[1].get("x"), _opts[1].get("y")) == (776, 394), _opts[:1]))
    _ictx = _Ctx(menu_data=_md)
    _irows = M.intent_menu._menu_options(_ictx)
    res.append(ok("🗿 单子那层：两条都成行、且标了 `kind=choose`（执行侧据此分岔）",
                  len(_irows) == 2 and all(r.get("kind") == "choose" for r in _irows), _irows))
    _icall = {}
    M.intent_menu._exec_option(_ictx, [_irows[1]], lambda k, a: _icall.update(key=k, args=a) or "✅ 选了")
    res.append(ok("🗿 敲下去走 `menu_icon{x,y,key}`（那屏没有 responses，`option=N` 对它没用）",
                  _icall.get("key") == "menu_icon"
                  and _icall.get("args") == {"x": 776, "y": 394, "key": "3"}, _icall))
    res.append(ok("🗿 提示语：选项摊成行 ⇒ **不再叫它 `menu read`**，并说清没有右上角关闭键",
                  "menu read" not in M._close_hint("ChooseFromIconsMenu", content_on_sheet=True)
                  and "关掉界面" in M._close_hint("ChooseFromIconsMenu", content_on_sheet=True),
                  M._close_hint("ChooseFromIconsMenu", content_on_sheet=True)))
    res.append(ok("🗿 摊不出来（老 DLL / 读不到）⇒ **退回** `menu read`（那是真路）",
                  "menu read" in M._close_hint("ChooseFromIconsMenu", content_on_sheet=False)))
    # 🔎 点的回读：**必须轮询**（真机假警报：0.6s 时那屏还开着、1s 后已关且 buff 挂上，
    #    我却按"这一屏没变"报了 ⚠️）⇒ 成功的样子 = 菜单关了 / 这屏换了 / buff 表多了东西。
    _seq = {"n": 0}
    _buf = [[]]

    def _g2(ep, params=None):
        if ep == "/menu":
            _seq["n"] += 1
            if _seq["n"] <= 2:
                return dict(_ICON)
            return {"ok": True, "open": False, "type": None, "buttons": []}
        if ep == "/state":
            return dict(STATE, player=dict(STATE.get("player") or {}, buffs=_buf[0]))
        return {}

    def _p2(ep, data=None):
        if ep == "/menu/click":
            _buf[0] = [{"id": "dwarfStatue_3", "name": "矮人之王雕像"}]
            return {"ok": True, "clicked": "position", "x": (data or {}).get("x"), "y": (data or {}).get("y")}
        return {"ok": True}

    _og, _op = M.api._ai_get, M.api._ai_post
    M.api._ai_get, M.api._ai_post = _g2, _p2
    try:
        _msg_icon = M._im_menu_icon(776, 394, "3")
    finally:
        M.api._ai_get, M.api._ai_post = _og, _op
    res.append(ok("🗿 点图标：**等它落地再判**（0.6s 那下没变不算失败）+ 用 buff id 说清点了哪个",
                  "✅" in _msg_icon and "dwarfStatue_3" in _msg_icon, _msg_icon))
    # 🗿 理由栏要带坐标/距离（远的那座雕像：不说位置 AI 不知道要走多远）
    _st_ctx_far = _Ctx(px=53, py=58, statue={"names": ["Statue Of Blessings"],
                                             "tiles": [[74, 16]], "used_today": False})
    _st_reason = M.intent_menu._statue_reason(_st_ctx_far, {})
    res.append(ok("🗿 理由栏带 📍坐标 + 距离（远了也要让 AI 知道要走多久）",
                  "(74,16)" in _st_reason and "42 格" in _st_reason, _st_reason))
    # 🗿 执行器**同一把尺**：`blessing_statue.py` 也必须走 `/machines`（不是 /surroundings 30 格）
    #    ⚠️ 查的是**调用形态**，不是"文件里出现过这个词"——它的注释里正解释着"以前扫 30 格窗口"。
    _bs_src = open(os.path.join(_here, "blessing_statue.py"), encoding="utf-8").read()
    _bs_calls_surr = ('get("/surroundings"' in _bs_src) or ('post("/surroundings"' in _bs_src)
    res.append(ok("🗿 执行器换源了：`blessing_statue.py` 调 `/machines`（不再调 /surroundings），并带 `location` 过滤",
                  'get("/machines")' in _bs_src and not _bs_calls_surr and "location" in _bs_src,
                  "还在打 /surroundings" if _bs_calls_surr else ""))
    # 🗿⛔ **假成功防线**（2026-10-04 真机当场抓到）：脚本"站在雕像**斜角** ⇒ interact 打空
    #    （triggered=False、游戏那位还是 false）"却印「🗿 摸了…」；而单子那层（`_im_run` 的
    #    helpers 档）是**看第一个字符**判成没成的 ⇒ 前面那句「🗿 雕像报告」把 ⚠️ 洗成了 ✅。
    res.append(ok("🗿 成功判据 = `actionTriggered` + `facingTile` 就是雕像格（或游戏自己的 `blessedByStatueToday`）",
                  "facingTile" in _bs_src and "actionTriggered" in _bs_src
                  and "blessedByStatueToday" in _bs_src))
    res.append(ok("🗿 站位要求**精确**（`exact=True`：差一格的斜角站位就是打空的根因，不许再放行）",
                  "wait_arrive(px, py, timeout=20.0, exact=True)" in _bs_src))
    _orig_rs = M._run_script
    try:
        M._run_script = lambda *a, **k: ("[statue] 📍 Farm | 找到雕像\n"
                                         "[statue] ⚠️ 没摸到 Statue Of Blessings：站在斜角打空了")
        _txt_bad = M.blessing_statue()
        M._run_script = lambda *a, **k: ("[statue] 📍 Farm | 找到雕像\n"
                                         "[statue] ✅ 摸到雕像 Statue Of Blessings（全程真走位）")
        _txt_ok = M.blessing_statue()
        M._run_script = lambda *a, **k: "[statue] ⚠️ 没摸到 Statue Of Blessings"
        _r_st_bad = M._im_run("statue", {})
    finally:
        M._run_script = _orig_rs
    res.append(ok("🗿 脚本报 ⚠️ ⇒ 工具文本**以 ⚠️ 起头**（`_im_run` 看首字符 ⇒ 回执不会印 ✅）",
                  str(_txt_bad).lstrip().startswith("⚠️"), str(_txt_bad)[:60]))
    res.append(ok("🗿 脚本报 ✅ ⇒ 不糊 ⚠️（正常成功照旧）",
                  not str(_txt_ok).lstrip().startswith("⚠️") and "✅" in str(_txt_ok)))
    res.append(ok('🗿 端到端：没摸到时 `_im_run("statue")` **不是 yes**（单子不会印 ✅）',
                  isinstance(_r_st_bad, dict) and _r_st_bad.get("st") != "yes", _r_st_bad))

    # 🐟 收 鱼塘产出
    res.append(ok("🐟 收鱼塘产出行在册（权重 82：压在 箱子 80 之上、摸动物 84 之下 —— 它是真动作）",
                  _w("pond") == 82, _w("pond")))
    _pd_can = M.intent_menu._pond_can
    _PD = {"ready": [{"x": 12, "y": 30, "output": "鲑鱼子"}], "total": 2}
    res.append(ok("🐟 有座塘有产出 ⇒ ✅", _pd_can(_Ctx(ponds=_PD), {}) is True))
    res.append(ok("🐟 有塘但都没产出 / 不在农场（`{}`）⇒ ❌（那行不出现）",
                  _pd_can(_Ctx(ponds={"ready": [], "total": 3}), {}) is False
                  and _pd_can(_Ctx(ponds={}), {}) is False))
    # 服务器那层：**不在农场一发都不多打**（鱼塘只建在农场，领产出的走位也写死走 Farm）
    _stub(loc="FarmHouse", ponds=[{"x": 12, "y": 30, "output": "鲑鱼子"}])
    _pd_off = M._im_ponds({"location": {"name": "FarmHouse"}})
    res.append(ok("🐟 不在农场 ⇒ 账为空 **且一发 `/fish_pond` 都不打**",
                  _pd_off == {} and not [c for c in CALLS if c[1] == "/fish_pond"],
                  (_pd_off, [c for c in CALLS if c[1] == "/fish_pond"])))
    _stub(loc="Farm", ponds=[{"x": 12, "y": 30, "output": "鲑鱼子"},
                             {"x": 40, "y": 30},                    # 没产出 ⇒ 不进账
                             {"x": 44, "y": 30, "output": "蚌"}])
    _pd_on = M._im_ponds({"location": {"name": "Farm"}})
    res.append(ok("🐟 农场：只列**有产出的**塘，`total` 报全档塘数（3 座里 2 座有货）",
                  [p["output"] for p in _pd_on["ready"]] == ["鲑鱼子", "蚌"]
                  and _pd_on["total"] == 3, _pd_on))
    _stub(loc="Farm", ponds=[{"x": 12, "y": 30, "output": "鲑鱼子"},
                             {"x": 44, "y": 30, "output": "蚌"}])
    res.append(ok("🐟 端到端：`_im_ctx()` 把账递给单子（`ctx.ponds` 就是那份）",
                  len(M._im_ctx().ponds.get("ready") or []) == 2, M._im_ctx().ponds))
    _pd_calls = []
    _exec_pd = M.intent_menu._exec_pond
    _pd_txt = _exec_pd(_Ctx(ponds=_pd_on), [{}],
                       lambda k, a: _pd_calls.append((k, a)) or
                       {"ok": True, "st": "yes",
                        "text": "🐟 交互鱼塘: triggered=True ✅ 已领产出（背包收下）"})
    res.append(ok("🐟 exec **逐塘**各一发 `pond_collect`（坐标是那座塘自己的）",
                  _pd_calls == [("pond_collect", {"x": 12, "y": 30}),
                                ("pond_collect", {"x": 44, "y": 30})], _pd_calls))
    res.append(ok("🐟 回执**逐条报**（两座各一行、带它自己的话）",
                  _pd_txt.count("·") >= 2 and "已领产出" in _pd_txt, _pd_txt[:200]))
    _seq = [{"ok": True, "st": "yes", "text": "✅ 已领产出（背包收下）"},
            {"ok": False, "st": "maybe", "text": "⚠️ 产出没领到（可能背包满了）"}]
    _pd_txt2 = _exec_pd(_Ctx(ponds=_pd_on), [{}], lambda k, a: _seq.pop(0))
    res.append(ok("🐟 一座没领到 ⇒ 正文里带**它自己那句 ⚠️**（不许整批报成功）",
                  "产出没领到" in _pd_txt2, _pd_txt2[:220]))
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
    # ⚠️⚠️ 2026-10-02 **恒真机逮到的假自验**：这一格原来写的是
    #     `isinstance(M._im_run("pickup_scene", {}), dict)` —— **真执行**！
    #     它 →`pickup_scene()`→`_run_script`→**起真子进程 `pickup_scene.py`**→直连 7843
    #     **把农场地上那 7 个松露全捡了**（10:34 / 10:35 跑两遍各捡一批，共 18 发交互）。
    #     为什么以前没发现：① `pickup_scene.py` **不 import `stardew_api`** ⇒ 那道网闸压根没上；
    #     ② 它用**裸 `requests`** ⇒ `_game_calls.log` 一条不记；③ 不是 MCP 工具调用 ⇒ 会话日志也没有。
    #     ⇒ 症状正是恒那句「**它怎么自己又跑起来了**」。
    #     ⇒ 现在**打桩**（只验"接线接对了"，一个字节都不出网），并且名字里点明不许真跑。
    _pick_seen = []
    _ps_orig = M.pickup_scene
    M.pickup_scene = lambda *a, **k: (_pick_seen.append((a, k)), "🎁（自验桩：没捡）")[1]
    try:
        _rp = M._im_run("pickup_scene", {})
    finally:
        M.pickup_scene = _ps_orig
    res.append(ok("🎁 A：走的是**现成的那条 op**（`_im_run` 认 `pickup_scene`，且**打桩后一个字节都不出网**）",
                  isinstance(_rp, dict) and len(_pick_seen) == 1
                  and "自验桩" in str(_rp.get("text")), (_rp, _pick_seen)))

    # 🚫🚫 同一件事的**机械**堵法（别再靠"记得打桩"）：自验进程里 `_run_script` **拒绝起真脚本**。
    #     为什么必须堵在这一层：子进程 `pickup_scene.py` / `feed_hay.py` 这类**不 import
    #     `stardew_api`** ⇒ `_net_guard` 按"谁 import 我"上闸，**它们自己永远不上闸**；
    #     而它们又用**裸 `requests`** ⇒ Python 侧请求日志也一条不记。三张网全漏。
    #     ⇒ 唯一的 choke point 是父进程的 spawn 出口。
    import subprocess as _sp
    _spawned = []
    _sr_orig = _sp.run
    _sp.run = lambda cmd, *a, **k: (_spawned.append(list(cmd)), None)[1]
    try:
        _ref = M._run_script("pickup_scene", ["--max", "1"], timeout=5)
    finally:
        _sp.run = _sr_orig
    res.append(ok("🚫 自验里 `_run_script` **拒绝起真脚本**（一个子进程都不许 spawn —— "
                  "子进程不 import stardew_api ⇒ 它自己不上闸，这是唯一堵得住的出口）",
                  "自验不许出网" in _ref and not _spawned, (_ref, _spawned)))

    # 🎓 2026-10-02：**名额是有限的** —— 真机 Lv4/已花 3 ⇒ 只剩 1 个名额，
    #    而屏上两块没领的碑**都**被盖了「可领」⇒ AI 以为两块都能领（按第二块游戏不理它）。
    def _mastery_txt(unspent, claimed_flags):
        _sk = [{"skill": s, "cn": s, "claimed": c}
               for s, c in zip(("farming", "fishing", "foraging", "mining", "combat"),
                               claimed_flags)]
        _stub(menu=None)
        M.api.mastery = lambda *a, **k: {"ok": True, "level": 4, "exp": 4588, "expForNext": 30000,
                                         "levelsSpent": 3, "unspent": unspent,
                                         "canClaim": unspent > 0, "plaques": _sk}
        return M.mastery_status()

    _mt = _mastery_txt(1, (True, False, False, True, True))       # 只剩 1 个名额、2 块没领
    res.append(ok("🎓 名额不够时**不许给每块没领的碑都盖「可领」**（真机：2 块都写可领，其实只有 1 个名额）",
                  _mt.count("**可领**") == 0 and "**未领**" in _mt, _mt.splitlines()[:8]))
    res.append(ok("🎓 而且**说清有几个名额**（挑哪块都行）", "只有 1 个名额" in _mt, _mt.splitlines()[-2:]))
    _mt2 = _mastery_txt(3, (True, False, False, True, True))      # 名额 ≥ 没领块数
    res.append(ok("🎓 名额够 ⇒ 照旧逐块说「可领」（别把原来能领的也说成未领）",
                  _mt2.count("**可领**") == 2 and "**未领**" not in _mt2, _mt2.splitlines()[:8]))
    _mt3 = _mastery_txt(0, (True, True, True, True, True))
    res.append(ok("🎓 全领完了 ⇒ 一句「没得领」都不多说", "没得领" in _mt3 and "可领" not in _mt3,
                  _mt3.splitlines()[:3]))
    # 洞内那条提示是**第二个消费点**（真机上写着两块 🟢 的就是它）—— 必须走同一份判据。
    _mastery_txt(1, (True, False, False, True, True))
    M._MASTERY_CAVE_SEEN.update(loc="", ts=0.0)
    _cv = M._mastery_cave_hint("MasteryCave")
    res.append(ok("🎓 洞内提示也改口：说清「没领的有 2 块、只有 1 个名额」（原来两块都画 🟢）",
                  "没领的有 2 块" in _cv and "1 个名额" in _cv, _cv))

    # ── B. 「收 蟹笼」收完**回读真值**、如实报剩几个 ──
    def _crab_after(ready_left, scan_raises=False):
        """跑一次 `_crab_collect`：开工问一次 `/crab_pots`、收完回读一次。全打桩。

        ⚠️ 2026-10-02：`_crab_collect` 现在**开工先问 `/crab_pots`（整图）**拿"哪些笼有货"，
          收完**再问一次**回读真值 ⇒ 打桩必须**按调用次序给两次不同答案**：
            第 1 次（开工）＝只有 (42,1) 有货；第 2 次（回读）＝由 `ready_left` 决定还剩几个。
          `_crab_scan_placed`（脚边 20 格）故意给**另外 3 个**坐标：这样"到底问了哪张单子"
          用 `/interact` 打到哪几格就能一眼证死（走脚边 ⇒ 会打 3 格，走整图 ⇒ 只打 1 格）。
        返回 `(输出, 被打过 interact 的坐标列表)`。"""
        _old = (M._crab_scan_placed, M._crab_stand_for, M._crab_pots_scan, M.api._post,
                M._wait_arrival, M.time.sleep)
        M._crab_scan_placed = lambda *a, **k: [(90, 90), (91, 90), (92, 90)]
        M._crab_stand_for = lambda x, y: (x, y + 1, 0)
        M._wait_arrival = lambda *a, **k: True
        _hits = []

        def _stub_post(ep, data=None):
            if ep == "/interact" and data:
                _hits.append((data.get("x"), data.get("y")))
            return {"ok": True}
        M.api._post = _stub_post
        M.time.sleep = lambda *a, **k: None
        if scan_raises:
            def _boom(*a, **k):
                raise RuntimeError("模拟：/crab_pots 读不到")
            M._crab_pots_scan = _boom
        else:
            _calls = {"n": 0}

            def _scan(*a, **k):
                _calls["n"] += 1
                if _calls["n"] == 1:      # 开工：只有 (42,1) 有货
                    return [{"x": 42, "y": 1, "readyForHarvest": True},
                            {"x": 43, "y": 1, "readyForHarvest": False},
                            {"x": 44, "y": 1, "readyForHarvest": False}]
                return [{"x": 42, "y": 1, "readyForHarvest": bool(ready_left)},   # 回读
                        {"x": 43, "y": 1, "readyForHarvest": False},
                        {"x": 44, "y": 1, "readyForHarvest": False}]
            M._crab_pots_scan = _scan
        try:
            return M._crab_collect(), _hits
        finally:
            (M._crab_scan_placed, M._crab_stand_for, M._crab_pots_scan, M.api._post,
             M._wait_arrival, M.time.sleep) = _old

    _rb, _rb_hits = _crab_after(ready_left=1)
    res.append(ok("🦀 B：收笼清单问的是 `/crab_pots`（**整图**）—— 不是脚边 20 格"
                  "（真机那个「单子说本图 4 个有货、敲下去却说**附近**没找到」的假门）",
                  _rb_hits == [(42, 1)], _rb_hits))
    res.append(ok("🦀 B：收完**还剩 1 个** ⇒ 如实报出来 + 点名坐标（真机那个 (42,1) 的洞）",
                  "还剩 1 个没收到" in _rb and "(42,1)" in _rb, _rb.splitlines()[-3:]))
    res.append(ok("🦀 B：而且给下一步（再收一次 / 记得放饵）",
                  "crab_collect" in _rb and "crab_bait" in _rb, _rb.splitlines()[-2:]))
    res.append(ok("🦀 B：「本轮 N/N 次交互」**不冒充收干净**（明说只是次数、真值看回读）",
                  "不代表收干净" in _rb, _rb.splitlines()[-4:-3]))
    _rb0, _ = _crab_after(ready_left=0)
    res.append(ok("🦀 B：真收干净了 ⇒ 明说**全收干净了**", "全收干净了" in _rb0, _rb0.splitlines()[-1:]))
    _rbe, _ = _crab_after(ready_left=1, scan_raises=True)
    res.append(ok("🦀 B：**读不到** ⇒ 如实说「到底收没收干净我不知道」（不编）",
                  "不知道" in _rbe and "回读" in _rbe, _rbe.splitlines()[-1:]))

    # ── B2. 「放饵」也只挂**真缺饵的**笼（已挂过/已出货的不白走一趟）+ 同样问整图 ──
    def _bait_hits():
        """跑一次 `_crab_bait`：3 个笼（已挂饵 / 已出货 / 空着）⇒ 只该动"空着"那个。"""
        _old = (M._crab_scan_placed, M._crab_stand_for, M._crab_pots_scan, M.api._post,
                M._wait_arrival, M.time.sleep, M.api.has_item, M.api.select)
        _hits = []
        M._crab_scan_placed = lambda *a, **k: [(90, 90), (91, 90), (92, 90)]
        M._crab_stand_for = lambda x, y: (x, y + 1, 0)
        M._wait_arrival = lambda *a, **k: True
        M.api.has_item = lambda *a, **k: True
        M.api.select = lambda *a, **k: True

        def _stub_post(ep, data=None):
            if ep == "/interact" and data:
                _hits.append((data.get("x"), data.get("y")))
            return {"ok": True, "actionTriggered": True}
        M.api._post = _stub_post
        M.time.sleep = lambda *a, **k: None
        M._crab_pots_scan = lambda *a, **k: [
            {"x": 42, "y": 1, "bait": "Bait", "readyForHarvest": False},   # 已挂饵 → 别动
            {"x": 43, "y": 1, "bait": None, "readyForHarvest": True},      # 已出货 → 该收不该挂
            {"x": 44, "y": 1, "bait": None, "readyForHarvest": False},     # 空着 → 就它
        ]
        try:
            return M._crab_bait(), _hits
        finally:
            (M._crab_scan_placed, M._crab_stand_for, M._crab_pots_scan, M.api._post,
             M._wait_arrival, M.time.sleep, M.api.has_item, M.api.select) = _old

    _bb, _bb_hits = _bait_hits()
    res.append(ok("🦀 B2：`crab_bait` 只挂**真缺饵的**（已挂饵/已出货的笼不白走一趟）",
                  _bb_hits == [(44, 1)], _bb_hits))
    res.append(ok("🦀 B2：而且报的是「本图共 N 个」中**缺饵的**几个（不把不动的算进去）",
                  "缺饵的" in _bb and "本图共 3 个" in _bb, _bb.splitlines()[:2]))

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
    #    🧭 2026-10-03：`_walk_to_animal_door()`（"挑最近那栋"）**已删**（没有生产调用点了），
    #       走位这条路现在只有 `_walk_to_door_of(b)` 一处 ⇒ 下面**直接对着它**验。
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
        _note = M._walk_to_door_of(_farm_b(want_open=True)[0])
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
        _note2 = M._walk_to_door_of(_farm_b(animal_door=False)[0])
    finally:
        M._walk_and_wait, M.api.face = _o_ww2
    res.append(ok("🚶 195：**缺小门键 ⇒ 退回人类门那一套**（door(44,16) ⇒ 站 (44,17)）",
                  bool(_WALK_SEEN) and tuple(_WALK_SEEN[-1][1:]) == (44, 17)
                  and "门(44,16)" in _note2, (_WALK_SEEN, _note2)))
    import inspect as _insp3
    res.append(ok("🚶 195：翻门**仍然走 `/toggle_doors`**（`_doors_flip_once` 调 `api.close_doors`，"
                  "端点名收在 `stardew_api` 那一层 —— **不许**改成对着小门 interact）",
                  "api.close_doors" in _insp3.getsource(M._doors_flip_once)
                  and "/toggle_doors" in _insp3.getsource(M.api.close_doors)))
    res.append(ok("🚶 195：那句**重要告诫**（真要换 interact 得**先在真机 A/B**）跟着代码搬到"
                  "`_doors_flip_all()` 上了（删函数不等于删这条口径）",
                  "先在真机 A/B" in _insp3.getsource(M._doors_flip_all)))
    res.append(ok("🚶 195：孤儿 `_walk_to_animal_door` **真的没了**（没人调就删，别留两套）",
                  not hasattr(M, "_walk_to_animal_door")))

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
        "machines": {"machines": [_grab_m,
                                  {"type": "Auto-Petter", "location": "Deluxe Coop", "x": 20, "y": 4,
                                   "status": "empty", "heldItemDisplay": ""},
                                  {"type": "Keg", "location": "Farm", "status": "ready",
                                   "heldItemDisplay": "钻石"}]}}
    try:
        _mr = M.machine_report()
    finally:
        M.api.farm_report = _old_fr
    # ⚠️ 2026-10-02(203e) 真机把这两台**分开**了 —— 原来说一句「按容器读，不算机器」，
    #    可**抚摸机打不开也没内容**（鸡舍 (20,4) `interact` 没触发、`activeMenu` 空）⇒ 那是半句假话。
    res.append(ok("🤖 197/203e：机器表把**采集器**单列成「当箱子用」（给 `scene at` 这条路 + 坐标），且不印假名字",
                  "自动采集器 1 台" in _mr and "当箱子用" in _mr and "scene at" in _mr
                  and "Deluxe Coop(5,5)" in _mr and "错误物品" not in _mr, _mr[:400]))
    res.append(ok("🤖 203e：**抚摸机单独一行**、如实说「不产东西、也打不开」（**不许**混进「容器」那行）",
                  "自动抚摸机 1 台" in _mr and "打不开" in _mr
                  and "当箱子用" not in _mr.split("自动抚摸机")[1][:60], _mr[:400]))
    res.append(ok("🤖 203e：`_is_auto_grabber` 分得出这两台（判据只此一处）",
                  M._is_auto_grabber(_grab_m) and not M._is_auto_grabber({"type": "Auto-Petter"})))
    res.append(ok("🤖 197：机器台数**不含**这两台（那一屏只该有 Keg 一台）",
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

    # ㉒ 203c 🎓👥 **"写了但看不见"** 那两笔（恒 2026-10-02：「补一下缺门」 + 「👥 那行一次都没见过」）
    #    ① 🎓 菜单里**能领却没那行**：抬头喊"可以领"，单子上只有「关掉界面」⇒ AI 只看单子就把正事错过。
    #    ② 👥 状态条那行**整整一天印不出来**：`_npcs_hint` 写对了，可 `_gather_state()` 漏透传
    #       `/state.npcs` ⇒ `data["npcs"]` 恒空 ⇒ 每次都"本图没人"。
    #    ⚠️ ② 的病根值得单独记一笔：我当时的"验证"是**手搓一份带 npcs 的 dict** 喂进去 ⇒ 假绿灯
    #       （夹具比真机更空：`/state` 连 `npcs` 键都没有）。所以下面那条**必须真跑 `_gather_state()`**。
    _MK = {"skill": "Foraging", "title": "采集精通", "canClaim": True, "claimed": False,
           "isOverview": False, "rewards": [{"name": "神秘树种"}, {"name": "宝藏图腾"}]}
    _Lmk, _ = _labels2(menu="MasteryTrackerMenu", menu_extra={"mastery": _MK})
    res.append(ok("🎓 能领 ⇒ 单子上出现「领 采集精通碑的奖励（神秘树种、宝藏图腾）」",
                  any(x.startswith("领 采集精通碑") and "神秘树种" in x for x in _Lmk), _Lmk))
    _i_cl = next((i for i, x in enumerate(_Lmk) if x.startswith("领 采集精通碑")), None)
    _i_ex = next((i for i, x in enumerate(_Lmk) if "关掉界面" in x), None)
    res.append(ok("🎓 而且它排在「关掉界面」**前面**（那一刻的正事在前，别让 AI 顺手把界面关了）",
                  _i_cl is not None and _i_ex is not None and _i_cl < _i_ex, (_i_cl, _i_ex, _Lmk)))
    for _bad, _why in ((dict(_MK, canClaim=False), "没名额"), (dict(_MK, claimed=True), "已经领过"),
                       (dict(_MK, isOverview=True), "总览屏")):
        _Lb, _ = _labels2(menu="MasteryTrackerMenu", menu_extra={"mastery": _bad})
        res.append(ok(f"🎓 {_why} ⇒ **整行不出现**（宁缺勿编）",
                      not any("碑的奖励" in x for x in _Lb), _Lb))
    _Lno, _ = _labels2(menu="MasteryTrackerMenu", menu_extra={})
    res.append(ok("🎓 连 `mastery` 段都没有（老 DLL / 别的菜单）⇒ 不出现、也不炸",
                  not any("碑的奖励" in x for x in _Lno), _Lno))
    _mc_orig = M._menu_claim_now
    M._menu_claim_now = lambda *a, **k: "🎓（桩：_menu_claim_now 跑了）"
    try:
        _rmc = M._im_run("menu_claim", {})
    finally:
        M._menu_claim_now = _mc_orig
    res.append(ok("🎓 `_im_run` 认 `menu_claim`，走的是 `_menu_claim_now()`（不自己打端点）",
                  isinstance(_rmc, dict) and "_menu_claim_now 跑了" in str(_rmc.get("text")), _rmc))

    # ── 👥 本图 NPC 那一行（**穿 `_gather_state` 全链**，不是手搓 dict）──
    # 🚫 真机出口闸：`host_sittable`/`host_pool`/`host_state` 走**裸 requests**（不经 `api._get`），
    #    文件头那四个桩拦不住 ⇒ 不闸住的话这条用例会**真打 7842**（直连端口不进 MCP 日志，出事查不到）。
    _old_hs, _old_hp, _old_hst = M.api.host_sittable, M.api.host_pool, M.api.host_state
    _old_req = M.api.requests
    _NET_TRIES = []

    class _Resp:
        def json(self):
            return {"ok": False}

    class _NoNet:
        def get(self, url, *a, **k):
            _NET_TRIES.append(("GET", url)); return _Resp()

        def post(self, url, *a, **k):
            _NET_TRIES.append(("POST", url)); return _Resp()

    M.api.requests = _NoNet()
    M.api.host_sittable = lambda *a, **k: {}
    M.api.host_pool = lambda *a, **k: {}
    M.api.host_state = lambda *a, **k: {}
    try:
        _stub()
        _d = M._gather_state()
        _d_npcs = _d.get("npcs")
        _d_strip = M._build_state_strip(_d, full=False)
        _stub(npcs=[{"name": "喵喵", "kind": "pet", "x": 12, "y": 13}])
        _strip_pet = M._build_state_strip(M._gather_state(), full=False)
        _stub(npcs=[])
        _strip_none = M._build_state_strip(M._gather_state(), full=False)
    finally:
        M.api.requests = _old_req
        M.api.host_sittable, M.api.host_pool, M.api.host_state = _old_hs, _old_hp, _old_hst
    res.append(ok("👥 `/state.npcs` **真透传**进 `data`（2026-10-02 漏掉的就是这根线）",
                  _d_npcs == STATE["npcs"], _d_npcs))
    res.append(ok("👥 **精简版**状态条（`full=False`，check/intent 那种回包）里真印出那一行",
                  "👥 本图 NPC：格斯" in _d_strip,
                  [x for x in _d_strip.splitlines() if "👥" in x]))
    res.append(ok("👥 而且把 chat/gift 两条路的**完整写法** + 送礼次数写全",
                  'social(ops="chat"' in _d_strip and 'social(ops="gift"' in _d_strip
                  and "每周最多 2 次" in _d_strip, _d_strip[-400:]))
    res.append(ok("👥 只有猫狗（第二道保险：C# 那边万一放宽口径）⇒ 那行不出现（别劝 AI 去跟猫聊天）",
                  "👥" not in _strip_pet, [x for x in _strip_pet.splitlines() if "👥" in x]))
    res.append(ok("👥 本图没人 ⇒ 不出现（不硬编一句空话）", "👥" not in _strip_none))
    res.append(ok("🚫 这条用例**零 raw 网络**（状态条这条路不许绕过 `api._get` 打真机）",
                  _NET_TRIES == [], _NET_TRIES))

    # ㉓ 203d 🐮 挤奶/剪毛**一只都没成**时点名原因（恒 2026-10-02：「报错排除自动采集器后基本就是
    #    这个原因」）—— 判据必须**问游戏**（数格子），真满才说满，没满就明说"不是满包挡的"。
    _stub(inv=[{"name": f"物{i}", "stack": 1, "slotIndex": i} for i in range(36)])
    _mn_full = M._milk_empty_note()
    res.append(ok("🐮 背包真满（36/36）⇒ 点名「背包满了」+ 给下一步（腾格子再回来）",
                  "背包满了" in _mn_full and "storage" in _mn_full, _mn_full))
    _stub()
    _mn_ok = M._milk_empty_note()
    res.append(ok("🐮 包里还有空位 ⇒ **明说不是满包挡的**（不许把猜的说成原因）",
                  "不是满包" in _mn_ok and "背包满了" not in _mn_ok, _mn_ok))

    # ㉔ 203e 😴 **动物睡了**（恒 2026-10-02 真机：「太晚了，动物都想要睡觉，挤奶抚摸都不行」）——
    #    这条只能**查源码**：本档两个动物建筑都装了自动采集器 ⇒ 挤奶支路走不到（真机那一趟正是被它拦下的），
    #    而 pet_walk 是个独立脚本（要真端口才跑得起来）。⚠️ 所以判据是"这两处必须长成这个样子"，
    #    不是"跑过了" —— 记在账上别当已验。
    import inspect as _insp9
    _mss = _insp9.getsource(M._milk_shear_animals)
    res.append(ok("😴 挤/剪：撞上「睡觉」**不算成功**（老代码没有「不产/没有」两词 ⇒ 会算成 ✅ 假成功）",
                  "_SLEEP_MARKS" in _mss and "sleep_msg" in _mss, None))
    res.append(ok("😴 挤/剪：撞上就**整趟收工**（牛试完别接着白试羊）+ 报原因给下一步",
                  "_stop.append" in _mss and "not _stop" in _mss and "明天早上 6~17 点" in _mss, None))
    _pw = open(os.path.join(os.path.dirname(os.path.abspath(M.__file__)),
                            "pet_walk.py"), encoding="utf-8").read()
    res.append(ok("😴 pet_walk：18:00 后**不摸了**（原来只打一句警告、照样跑 12 只×3 轮还留个对话框堵路）",
                  "if tod >= 1800:" in _pw and "不摸了" in _pw, None))
    res.append(ok("😴 pet_walk：循环里也认游戏那句（`sleep_dialogue()`）+ 收掉对话框（别留堵塞）",
                  "def sleep_dialogue" in _pw and "close_dialogue()" in _pw, None))

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
