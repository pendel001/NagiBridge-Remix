# -*- coding: utf-8 -*-
"""📋🎟 鹈鹕镇那两个"顺手办"的钉子 —— **不吃游戏**（假的 `_ai_get`/`_ai_post`）。

恒 2026-10-06 追加（原话）：
  「领券顺便也一起做选项算了。因为**领券就在旁边**，别让 ai 再走过去自己点了。这样：
    town 的选项单：… 3.**查看社区特别任务**→1.接左边 2.接右边（**仅当还没接取时有这两个选项，
    否则只有关闭**）…（**有券可领时**）**领取兑奖券×n**」

坐标与判据**全是查出来的**（不是我编的）：
  · 板子交互格 **(62,93)** / 站位 (62,94)：反编译 `Town.cs:547-549` 那三格挂 `SpecialOrders` Action；
  · 领奖箱交互格 **(60,93)** / 站位 (60,94)：`Town.cs:553-554` 挂 `SpecialOrdersPrizeTickets`；
  · 两格**同生同死** = `SpecialOrder.IsSpecialOrdersBoardUnlocked()`（`Town.cs:534-555`）；
  · "还没接取" = `team.acceptedSpecialOrderTypes.Contains("")`（板子藏 accept 按钮用的**就是这条**，
    `SpecialOrdersBoard.cs:94-98`）——⛔ **不是**"板上有没有卡"（接单不移卡，`:130-132`）；
  · 券数 = `/state.player.voucherPending`（`Game1.player.stats.Get("specialOrderPrizeTickets")`）；
  · 一次交互**只给一张**（`GameLocation.cs:9006-9020`）⇒ 行上印 `×N`。

🎰 ⑪~⑮ 是**兑奖机那一行**（牛头不对马嘴的另一件事，别跟上面两条混）：
  · `Town`/`ManorHouse` + `/state.player.prizeTickets > 0` ⇒ 「去兑奖机换奖品×N」；
  · 那一按**包办**：自己走过去（不在镇长家才跨图）→ 开机器 → **一张一张兑到底**，
    判据是**每按一次 `prizeTickets` 真掉 1**（桩里的 `mainButton` 按反编译真语义减数）。
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


ORDER_BOARD = {"ok": True, "orderType": "", "accepted": False, "readable": True,
               "boardUnlocked": True, "activeOrders": 0, "activeKeys": [],
               "left": {"questKey": "Robin", "name": "罗宾的资源 Rush", "requester": "Robin"},
               "right": {"questKey": "Lewis", "name": "给镇长的甜菜", "requester": "Lewis"}}
CALLS = []
S = {}


def _stub(loc="Town", unlocked=True, accepted=False, vouchers=0,
          left=True, right=True, board_open=False, state_raises=False, xy=(62, 94),
          caps=True, prizes=0, prize_name="神秘盒子"):
    """装桩。⚠️ `S` 是**可变的世界状态**（走位/交互/领券/兑奖都会改它）——
    桩按真端点的语义改它，这样 `_tank_go`/回读那几段才测得到。
    ⚠️ `xy` = **我这一刻站哪格**（默认板前 (62,94)）。领券那几条要传 (60,94) ——
       走位本身留给真机验，但**"没站到旁边就去走"这条闸**在这里钉：桩里的 `walk_to` 会照
       `/walk_to` 的语义把人挪过去（目标格站不住时落到它下面那格，就是真机 `adjusted` 那形状）。
    ⚠️ `prizes` = **手头**的兑奖券（`/state.player.prizeTickets`）；传 `None` = **老 DLL 上没这一位**
       （`/state` 里那个键整个不出现 —— "读不到"那根钉子要的形状，⛔ 别拿 0 顶替它）。
    ⚠️ `prize_name` = 兑奖机那一按**要吐出来的那件**（`/menu.items[0]` = C# 的
       `currentPrizeTrack[0]`）—— 回执里"逐张带回游戏自己的回读"就靠它。"""
    CALLS.clear()
    S.clear()
    S.update({
        "loc": loc, "x": int(xy[0]), "y": int(xy[1]), "facing": 0,
        "vouchers": int(vouchers),
        "unlocked": bool(unlocked), "accepted": bool(accepted),
        "left": bool(left), "right": bool(right),
        "board_open": bool(board_open), "orders": 0,
        # 🎰 手头券 + 那台机器开着没（`PrizeTicketMenu`）+ 奖品带第一件 + 背包（见 `_state`）
        "prizes": prizes, "prize_open": False, "prize_name": prize_name, "inv": [],
    })

    def _state():
        mt = ""
        if S["board_open"]:
            mt = "SpecialOrdersBoard"
        elif S["prize_open"]:
            mt = "PrizeTicketMenu"
        am = {"type": mt} if mt else None
        pl = {"x": S["x"], "y": S["y"], "stamina": 200, "maxItems": 36,
              "money": 3000, "facingDirection": S["facing"],
              "voucherPending": S["vouchers"],
              "currentItem": None, "currentItemId": None}
        if S["prizes"] is not None:            # ⚠️ `None` = **这一位整个不出现**（老 DLL 的形状）
            pl["prizeTickets"] = S["prizes"]
        return {"player": pl,
                "location": {"name": S["loc"], "uniqueName": S["loc"]},
                "inventory": list(S["inv"]), "activeMenu": am}

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/state":
            if state_raises:
                raise RuntimeError("模拟：/state 读不出来")
            return _state()
        if ep == "/order_board":
            return dict(ORDER_BOARD,
                        accepted=S["accepted"], boardUnlocked=S["unlocked"],
                        activeOrders=S["orders"],
                        left=(ORDER_BOARD["left"] if S["left"] else None),
                        right=(ORDER_BOARD["right"] if S["right"] else None))
        if ep == "/menu":
            # 🎰 兑奖机开着时 `/menu.items[0]` = `currentPrizeTrack[0]`（`ModEntry.cs:14616-14634`）
            if not S["prize_open"]:
                return {"ok": True, "type": "", "items": []}
            return {"ok": True, "type": "PrizeTicketMenu",
                    "items": [{"index": 0, "name": S["prize_name"],
                               "id": "(O)MysteryBox", "stack": 3}],
                    "buttons": [{"name": "mainButton"}]}
        if ep == "/status":
            # ⚠️ 新 DLL 会自报 `caps`（`_im_order_read` 的闸门就是 `caps["order_board"]`）——
            #    `caps=False` = **老 DLL 的形状**（没有这一位 ⇒ 一次都不许打）。
            return ({"ok": True, "build": "2026-10-06 00:00:00 @test",
                     "caps": {"order_board": True}} if caps
                    else {"ok": True, "build": "2026-10-06 00:00:00 @test"})
        return {}

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        d = data or {}
        if ep == "/face":
            S["facing"] = int(d.get("direction") or 0)
            return {"ok": True, "direction": S["facing"]}
        if ep == "/interact":
            # 🎯 两格各自的动作（照游戏 `GameLocation.cs` 的 Action 分支语义）
            if int(d.get("x") or -1) == 62 and int(d.get("y") or -1) == 93:
                S["board_open"] = True                 # `SpecialOrders` → 开板
            if int(d.get("x") or -1) == 60 and int(d.get("y") or -1) == 93:
                if S["vouchers"] > 0:
                    S["vouchers"] -= 1                 # `SpecialOrdersPrizeTickets` → 一张
            # 🎰 兑奖机那一格 (1,5)（ManorHouse）：手头有券 ⇒ 弹出 `PrizeTicketMenu`
            #    （⚠️ **开机器不扣券** —— 扣是 `mainButton` 那一下的事，这一层不碰）
            if int(d.get("x") or -1) == 1 and int(d.get("y") or -1) == 5:
                if (S["prizes"] or 0) > 0:
                    S["prize_open"] = True
            return {"ok": True, "actionTriggered": True}
        if ep == "/menu/click":
            btn = str(d.get("button") or "")
            if btn in ("acceptLeftQuestButton", "acceptRightQuestButton"):
                if S["accepted"]:
                    return {"ok": True, "clicked": "button"}   # 按钮压根不在（游戏自己藏了）
                S["accepted"] = True
                S["orders"] += 1
            # 🎰 `mainButton` = 兑奖券那一下 —— **照反编译的真语义**（`PrizeTicketMenu.cs:145-192`）：
            #    · **消耗 1 张**（`:184`，且**不看背包满不满**：塞不进包就 `createItemDebris` 丢脚下）；
            #    · 奖品进**背包**（`:180`；桩只做"进包"这一支，"落地"那支留给真机/回执的兜底话术）；
            #    · 菜单**兑完不关**（`:186-190` 只挪 track，不 `exitThisMenu`）。
            #    ⚠️ 真机是**点完 2000ms 才结算**（`:173-192`）—— 桩里立刻减，这样钉子测的是
            #       "逐张回读券数"那条判据，不是游戏那 2 秒的节拍（节拍由 `_PRIZE_*` 常量管）。
            if btn == "mainButton" and S["prize_open"]:
                if (S["prizes"] or 0) > 0:
                    S["prizes"] -= 1
                    S["inv"].append({"itemId": "(O)MysteryBox", "name": S["prize_name"],
                                     "displayName": S["prize_name"], "stack": 3})
            return {"ok": True, "clicked": "button"}
        return {"ok": True}

    api._ai_get, api._ai_post = g, p

    def _fake_walk(*a, **k):
        """照 `/walk_to` 的语义挪人：**目标格站不住就落到它下面那格**（真机的 `adjusted`）。

        🔴 2026-10-06：`_tank_go` 那把"离得 >3 格才走"的尺子改了（人在 2~3 格外时它一步都不走、
        接着判"没就位"失败 —— 领奖券真机就是这么失败的）⇒ 桩必须真的会挪人，否则新闸测不到。
        """
        tx, ty = k.get("x"), k.get("y")
        if (tx, ty) == (60, 93):        # 领奖箱那格自己站不住 ⇒ 就近落它下面 (60,94)
            tx, ty = 60, 94
        if isinstance(tx, int) and isinstance(ty, int):
            S["x"], S["y"] = tx, ty
        return "🚶 已到"

    api._get, api._post = g, p
    M.navigation.walk_to = _fake_walk

    def _fake_map_go(destination="", npc=""):
        """🧭 跨图那一发**在离线钉子里不许真跑**（真 `map_go` 会打 HTTP/BFS）——
        这里只记一笔 + **照真语义把图换过去**（走成了才会到），这样钉子才能证明
        "**先 `map_go`**"与"**已在 ManorHouse 就不重复 `map_go`**"两件事。"""
        CALLS.append(("MAPGO", destination, {"npc": npc}))
        if destination == M.PRIZE_MACHINE_POI:
            S["loc"] = "ManorHouse"
        return "🚶 已到"

    M.navigation.map_go = _fake_map_go
    M._with_state = lambda x, *a, **k: x
    M._ensure_background = lambda *a, **k: None
    M._peer_econ_mute = lambda *a, **k: None
    M.intent_menu.reset_menu()


def _rows():
    return [r.verb.key for r in M.intent_menu._LAST_ROWS]


_real = {n: getattr(M, n) for n in ("api", "_with_state", "_ensure_background", "_peer_econ_mute")}
_walk = M.navigation.walk_to
_mapgo = M.navigation.map_go
try:
    # ① 不在镇上 ⇒ 两行都不给，且**一次 `/order_board` 都不打**
    print("\n① 不在镇上：**零额外 HTTP** + 两行都不给")
    _stub(loc="Farm", vouchers=3)
    ctx = M._im_ctx()
    ck("`orders`/`vouchers` 都是空表", ctx.orders == {} and ctx.vouchers == {}, str(ctx.orders))
    ck("…一次 `/order_board` 都不打",
       not any(c[1] == "/order_board" for c in CALLS), str(CALLS))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有这两行", "order" not in _rows() and "voucher" not in _rows(), str(_rows()))

    # ② 板子没解锁 ⇒ 两行都不给（那两格瓦片根本不存在 ⇒ `/interact` 是打空）
    print("\n② 板子没解锁（`boardUnlocked=false`）⇒ 两行都不给")
    _stub(unlocked=False, vouchers=5)
    ctx = M._im_ctx()
    ck("`orders`/`vouchers` 都空", ctx.orders == {} and ctx.vouchers == {}, str(ctx.vouchers))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有这两行", "order" not in _rows() and "voucher" not in _rows(), str(_rows()))

    # ②b **老 DLL**（`/status.caps` 没有 `order_board` 这一位）⇒ **一次都不打**（别白烧 + 别吞异常）
    print("\n②b 老 DLL 没有 `order_board` 能力位 ⇒ 一次都不打、两行都不给")
    _stub(vouchers=5, caps=False)
    ctx = M._im_ctx()
    ck("…一次 `/order_board` 都不打",
       not any(c[1] == "/order_board" for c in CALLS), str(CALLS))
    ck("…`orders`/`vouchers` 都空", ctx.orders == {} and ctx.vouchers == {}, str(ctx.vouchers))

    # ③ 没接取 + 两边有卡 ⇒ 「查看社区特别任务…」+ 下级两行
    print("\n③ 没接取 ⇒ 「查看社区特别任务…」，点开是**接左边/接右边**两行")
    _stub(vouchers=0)
    out = M.intent(ops="show", kw={"n": 40})
    ck("目录行在", "order" in _rows(), str(_rows()))
    ck("…理由栏点名两张卡", "罗宾的资源 Rush" in out and "给镇长的甜菜" in out, out)
    no = next(r.no for r in M.intent_menu._LAST_ROWS if r.verb.key == "order")
    sub = M.intent(ops="do", kw={"code": str(no)})
    ck("下级两行都在", "接左边的订单" in sub and "接右边的订单" in sub, sub)
    ck("…标题说清**自己走过去**", "自己走过去" in sub, sub)

    # ④ 敲「接左边」⇒ 走过去 + 开板 + 点 acceptLeftQuestButton + **回读订单数**
    print("\n④ 敲「接左边」⇒ `/interact` 开板 + `/menu/click button=acceptLeftQuestButton`")
    _stub(vouchers=0)
    M.intent(ops="show", kw={"n": 40})
    M.intent(ops="do", kw={"code": str(next(r.no for r in M.intent_menu._LAST_ROWS
                                            if r.verb.key == "order"))})
    _no_left = next(r.no for r in M.intent_menu._LAST_ROWS
                    if (r.label or "").startswith("接左边的订单"))
    CALLS.clear()
    r = M.intent(ops="do", kw={"code": str(_no_left)})
    posts = [c for c in CALLS if c[0] == "POST"]
    ck("…先 `/interact` 打的是**板子那一格 (62,93)**",
       any(c[1] == "/interact" and (c[2] or {}).get("x") == 62
           and (c[2] or {}).get("y") == 93 for c in posts), str(posts))
    ck("…再 `/menu/click button=acceptLeftQuestButton`",
       any(c[1] == "/menu/click" and (c[2] or {}).get("button") == "acceptLeftQuestButton"
           for c in posts), str(posts))
    ck("回执说**接下来了**并给订单数", "接下来了" in r and "1 单" in r, r)

    # ⑤ 已经接过了 ⇒ **那行照样给**，只摆板子上的内容、已接取那张标「（已接取）」
    #    🔴 2026-10-06 恒拍板（**推翻**原来那版"整行不出现"）：
    #    「接过单而且没有券的情况下应该就只是显示一下菜单上的内容，在接取的任务标题打上（已接取）而已。
    #      详细的截止时间之类让它去看它的任务栏」
    print("\n⑤ 已经接过了 ⇒ **照样给那一行**（摆内容 + 标「已接取」；不再是整行消失）")
    _stub(accepted=True)
    ctx = M._im_ctx()
    ck("`orders` **不空**（照板子内容给）", bool(ctx.orders) and ctx.orders.get("accepted") is True, str(ctx.orders))
    _out5 = M.intent(ops="show", kw={"n": 40})
    ck("…单子上**有**那一行", "order" in _rows(), str(_rows()))
    ck("…**已接取**那张标了「（已接取）」", "已接取" in _out5, _out5[:400])
    # ⛔ 截止时间/进度**不进这一行的账**（恒：「让它去看它的任务栏」）——
    #    判据用**服务器递来的字段**（精确），不靠"整屏文案里别出现某字"（那种会误伤别行）
    ck("…⛔ 账里**不带**截止/进度字段（`daysLeft`/`deadline`/`progress` 一个都不许有）",
       not any(k in ctx.orders for k in ("daysLeft", "deadline", "progress", "days")), str(ctx.orders))
    # ⚠️ 老回包（只有数量、没有 `activeKeys`）⇒ 只能整体标，别假装知道是哪张；而且**不给下级接单行**
    ck("…已接取时**不给**「接左边/接右边」下级（游戏自己也把 accept 按钮藏了）",
       "接左边的订单" not in _out5 and "接右边的订单" not in _out5, _out5[:400])

    # ⑥ 有券 ⇒ 「领取兑奖券×N」；敲它 ⇒ **连点 N 次**、回执报领到几张
    print("\n⑥ 有券 ⇒ 「领取兑奖券×N」；敲它连点 N 次")
    _stub(vouchers=3, xy=(60, 94))       # 站在领奖箱正下方（朝上就是一格）
    out = M.intent(ops="show", kw={"n": 40})
    ck("行标题带 `×3`", "领取兑奖券×3" in out, out)
    CALLS.clear()
    r = M.intent(ops="do", kw={"code": str(next(r.no for r in M.intent_menu._LAST_ROWS
                                               if r.verb.key == "voucher"))})
    hits = [c for c in CALLS if c[0] == "POST" and c[1] == "/interact"
            and (c[2] or {}).get("x") == 60 and (c[2] or {}).get("y") == 93]
    ck("…真点了 **3 次**领奖箱那一格 (60,93)", len(hits) == 3, str(len(hits)))
    ck("回执报「领到 3 张」", "领到 3 张" in r, r)

    # ⑦ 没券 ⇒ 不给那一行
    print("\n⑦ 没券可领 ⇒ 不给那一行")
    _stub(vouchers=0, xy=(60, 94))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有「领取兑奖券」", "voucher" not in _rows(), str(_rows()))

    # ⑧ 背包满（数不掉）⇒ 回执**不谎报**（游戏只弹红字、不扣数）
    print("\n⑧ 背包满（点了几次数都不掉）⇒ 如实说、不谎报")
    _stub(vouchers=2, xy=(60, 94))
    # ⚠️ 直接改桩：把"领奖箱那支"变成不动数（背包满的形状）
    _orig_p = api._ai_post

    def _p_full(ep, data=None):
        d = data or {}
        if ep == "/interact" and int(d.get("x") or -1) == 60:
            return {"ok": True, "actionTriggered": True}      # 游戏：只弹红字、不扣数
        return _orig_p(ep, data)

    api._ai_post = _p_full
    r = M._im_voucher_take()
    ck("回执说**一张都没领到**并点名背包", "一张都没领到" in r and "背包" in r, r)
    api._ai_post = _orig_p

    # ⑧b 🔴 2026-10-06 真机修的那把尺子：站得还差两格 ⇒ **先走过去**再按
    #     （原来写的是"离得 >3 格才走" ⇒ 人在 2~3 格外时**一步都不走**、接着判"没就位"失败 ——
    #      领奖券真机就是这么失败的：人在 Town (62,94)、领奖箱 (60,93)，就差两步）
    print("\n⑧b 站得还差两格 ⇒ **先走过去**（邻格）再按，⛔ 不许站着不动就判失败")
    _stub(vouchers=2, xy=(62, 94))       # 板前，离领奖箱 (60,93) 差两步
    CALLS.clear()
    _walks = []
    _orig_walk2 = M.navigation.walk_to

    def _walk_log(*a, **k):
        _walks.append((k.get("x"), k.get("y")))
        return _orig_walk2(*a, **k)

    M.navigation.walk_to = _walk_log
    r = M._im_voucher_take()
    ck("…真去走了（`walk_to` 被调到）", bool(_walks), str(_walks))
    ck("…走了以后**照领**（回执说领到 2 张）", "领到 2 张" in r, r)
    ck("…按的是领奖箱那一格 (60,93)",
       any(c[1] == "/interact" and (c[2] or {}).get("x") == 60 and (c[2] or {}).get("y") == 93
           for c in CALLS if c[0] == "POST"), str(CALLS))
    # ⑧c 走位**真走不到**（桩不动人）⇒ 照旧"不按"，如实说没能站到（那道闸还在）
    print("\n⑧c 真的走不到 ⇒ 如实说「没能站到」且**一次都不按**")
    _stub(vouchers=2, xy=(62, 94))
    M.navigation.walk_to = lambda *a, **k: None
    CALLS.clear()
    r = M._im_voucher_take()
    ck("回执说没能站到正旁边", "没能站到" in r, r)
    ck("…**一次都没点**领奖箱那一格",
       not any(c[1] == "/interact" for c in CALLS if c[0] == "POST"), str(CALLS))

    # ⑨ 板子已经开着时**不重复走位/开板**（跟 `menu` 域那条路一致）
    print("\n⑨ 板子已经开着 ⇒ 直接点按钮（不重复 `/interact`）")
    _stub(vouchers=0, board_open=True)
    CALLS.clear()
    r = M._im_order_accept("right")
    ck("没再打 `/interact`", not any(c[1] == "/interact" for c in CALLS), str(CALLS))
    ck("…直接 `/menu/click button=acceptRightQuestButton`",
       any(c[1] == "/menu/click" and (c[2] or {}).get("button") == "acceptRightQuestButton"
           for c in CALLS), str(CALLS))
    ck("回执说接下来了", "接下来了" in r, r)

    # ⑩ 真没接上 ⇒ **不许谎报**（按钮还在 = 没生效）
    print("\n⑩ 点了按钮但订单数没涨 ⇒ 如实说「没看到订单数增加」")
    _stub(vouchers=0, board_open=True)

    def _p_dead(ep, data=None):
        CALLS.append(("POST", ep, data))
        return {"ok": True, "clicked": "button"}      # 什么都不发生

    api._ai_post = _p_dead
    r = M._im_order_accept("left")
    ck("回执不装成功", "没看到订单数增加" in r, r)
    api._ai_post = None

    # ⑪ 🎰 在镇上 + **手头真有一张券** ⇒ 单子上有「去兑奖机换奖品×1」
    #    ⚠️ 判据是 `/state.player.prizeTickets`（**手头**），**不是** `voucherPending`（待领）——
    #       这两根钉子故意给不同的数，混了就会当场照出来。
    print("\n⑪ Town + `prizeTickets=1` ⇒ 有那一行、标题带 `×1`")
    _stub(prizes=1, xy=(1, 6))
    ctx = M._im_ctx()
    ck("`prizes` 有 `n=1`（**手头**那个数）", (ctx.prizes or {}).get("n") == 1, str(ctx.prizes))
    out = M.intent(ops="show", kw={"n": 40})
    ck("…单子上**有**那一行", "prize" in _rows(), str(_rows()))
    ck("…标题带 `×1`", "去兑奖机换奖品×1" in out, out)
    ck("…理由栏写清机器位置 + **一次一张兑到底** + 手头张数",
       "(1,6)" in out and "(1,5)" in out and "一次一张兑到底" in out and "手头 1 张" in out,
       out[:600])
    ck("…理由栏写清**自己走过去** + **换到哪件是随机的**",
       "自己走过去" in out and "随机" in out, out[:600])
    # 🔴 2026-10-06 恒改口径（包办兑奖）⇒ 那一按真会把 N 张都兑掉 ⇒ `batch=True`
    #    （本项目"真会全做才许印 ×N"的规矩，见 `Verb.batch`）。
    ck("…`PRIZE_V.batch is True`（这一按真兑 N 张）",
       M.intent_menu.PRIZE_V.batch is True, str(M.intent_menu.PRIZE_V.batch))

    # ⑫ 不在镇上/镇长家 ⇒ 不给（跨图那一步不该在农场/矿洞里劝"去兑奖机"）
    print("\n⑫ 不在镇上（Farm / FarmHouse）⇒ 不给那一行（哪怕手头有券）")
    _stub(loc="Farm", prizes=3)
    ctx = M._im_ctx()
    ck("…`prizes` 空", ctx.prizes == {}, str(ctx.prizes))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有那一行", "prize" not in _rows(), str(_rows()))
    # ⚠️ 放行的只有 `Town`/`ManorHouse` **两张图**，⛔ 不是"只要是屋里就行"
    _stub(loc="FarmHouse", prizes=3)
    ctx = M._im_ctx()
    ck("…别的屋（FarmHouse）也**不给**（放行的是那两张图，不是「只要是室内」）",
       ctx.prizes == {}, str(ctx.prizes))

    # ⑬ 手头 0 张 ⇒ 不给；**读不到那一位**（老 DLL）⇒ 也 `{}`（⛔ 不许猜成 0 更不许编一张）
    print("\n⑬ Town + `prizeTickets=0` / **读不到那一位** ⇒ 都不给那一行")
    _stub(prizes=0)
    ctx = M._im_ctx()
    ck("0 张 ⇒ `prizes` 空", ctx.prizes == {}, str(ctx.prizes))
    M.intent(ops="show", kw={"n": 40})
    ck("…0 张 ⇒ 单子上没有那一行", "prize" not in _rows(), str(_rows()))
    _stub(prizes=None)                                  # 老 DLL：`/state` 里根本没这一位
    ctx = M._im_ctx()
    ck("读不到 ⇒ `prizes` 也是空（**不猜 0**）", ctx.prizes == {}, str(ctx.prizes))
    M.intent(ops="show", kw={"n": 40})
    ck("…读不到 ⇒ 单子上也没有那一行", "prize" not in _rows(), str(_rows()))

    # ⑭ 执行侧：没券 ⇒ **当场回绝、一个 `/interact` 都不打**（也不白跨图）；
    #    有券 ⇒ 走过去 + 开机器 + **一张一张兑掉**（判据 = 券数每按一次真掉 1）；
    #    开出来的**不是兑奖机** ⇒ 如实说"什么都没兑"且**一张都不按**
    print("\n⑭ `_im_prize_go`：没券当场回绝（零 `/interact`）；有券真兑掉才算成")
    _stub(prizes=0, xy=(1, 6))
    CALLS.clear()
    r = M._im_prize_go()
    ck("没券 ⇒ 回绝并点名「一张兑奖券都没有」", "一张兑奖券都没有" in r, r)
    ck("…**一个 `/interact` 都不打**",
       not any(c[0] == "POST" and c[1] == "/interact" for c in CALLS), str(CALLS))
    ck("…也**没白跑一趟**（连 `map_go` 都没调）",
       not any(c[0] == "MAPGO" for c in CALLS), str(CALLS))
    _stub(prizes=1, xy=(1, 6))
    CALLS.clear()
    r = M._im_prize_go()
    ck("有券 ⇒ 真走了跨图那一步（`map_go` 到兑奖机 POI）",
       any(c[0] == "MAPGO" and c[1] == "刘易斯家(特别订单兑奖机)" for c in CALLS), str(CALLS))
    ck("…交互打的是兑奖机那一格 (1,5)",
       any(c[0] == "POST" and c[1] == "/interact" and (c[2] or {}).get("x") == 1
           and (c[2] or {}).get("y") == 5 for c in CALLS), str(CALLS))
    ck("…回执报「兑了 1 张」+ 手头 1 → 0",
       "兑了 1 张" in r and "手头 1 → 0" in r, r)
    ck("…逐张带回**游戏自己的回读**（`/menu.items[0]` 的奖品名 + 背包多了什么）",
       "神秘盒子" in r and "背包多了" in r, r)
    # 开出来的不是兑奖机（点了 (1,5) 可菜单没弹出来）⇒ 如实拒 + **一张都不按**
    print("⑭b 站到了也点了、可**开出来的不是兑奖机** ⇒ 如实说 + 一张都不按")
    _stub(prizes=1, xy=(1, 6), loc="ManorHouse")
    _orig_p14 = api._ai_post

    def _p_noopen(ep, data=None):
        d = data or {}
        if ep == "/interact" and int(d.get("x") or -1) == 1:
            return {"ok": True, "actionTriggered": True}      # 点了，可什么都没弹出来
        return _orig_p14(ep, data)

    api._ai_post = _p_noopen
    CALLS.clear()
    r = M._im_prize_go()
    ck("回执说「开出来的不是兑奖机」+「什么都没兑」",
       "开出来的不是兑奖机" in r and "什么都没兑" in r, r)
    ck("…⛔ **一张都没按**（`mainButton` 一下都没打）",
       not any(c[1] == "/menu/click" and (c[2] or {}).get("button") == "mainButton"
               for c in CALLS if c[0] == "POST"), str(CALLS))
    api._ai_post = _orig_p14

    # ═══ ⑮ 🔴 2026-10-06 恒追加的四根（原话：「手上有券且在镇子上**或者刘易斯家**就上单显示，
    #     **帮忙包办走过去兑奖的过程**」）—— 前四块钉的是"上不上单"，这四块钉的是"包办得对不对"
    #     （桩里的 `mainButton` 已按反编译真语义减 `prizeTickets`，见 `_stub` 里那段）。
    # ⑮① `ManorHouse`（镇长家屋里）+ 有券 ⇒ **也有那一行**（原来是"只在 Town"）
    print("\n⑮① `ManorHouse`（镇长家屋里）+ 有券 ⇒ **照样给那一行**")
    _stub(loc="ManorHouse", prizes=2)
    ctx = M._im_ctx()
    ck("`prizes` 有 `n=2`（人在屋里也给）", (ctx.prizes or {}).get("n") == 2, str(ctx.prizes))
    out = M.intent(ops="show", kw={"n": 40})
    ck("…单子上**有**那一行", "prize" in _rows(), str(_rows()))
    ck("…标题带 `×2`", "去兑奖机换奖品×2" in out, out[:400])

    # ⑮② 两张券 ⇒ **真按了 2 次 `mainButton`**（每一按都以"券数掉了 1"记账）
    print("\n⑮② 两张券 ⇒ 真按 **2 次** `mainButton`，回执逐张报")
    _stub(loc="ManorHouse", prizes=2, xy=(1, 6))
    CALLS.clear()
    r = M._im_prize_go()
    _mb = [c for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"
           and (c[2] or {}).get("button") == "mainButton"]
    ck("…`mainButton` 真按了 **2 次**", len(_mb) == 2, str(len(_mb)))
    ck("…回执报「兑了 2 张」+ 手头 2 → 0", "兑了 2 张" in r and "手头 2 → 0" in r, r)
    ck("…逐张带游戏自己的回读（两张都报了奖品名）", r.count("神秘盒子") >= 2, r)
    ck("…⛔ 没谎报（不含「还剩」/「停手」那句）", "停手" not in r and "还剩" not in r, r)

    # ⑮③ 按了但**券数不掉** ⇒ 回执**不说"兑了"**、如实说清原因、**只按 1 次就停手**
    print("\n⑮③ 按了但券数不掉 ⇒ 不说「兑了」+ 说清原因 + **只按 1 次**")
    _stub(loc="ManorHouse", prizes=2, xy=(1, 6))
    _orig_p15 = api._ai_post

    def _p_noconsume(ep, data=None):
        d = data or {}
        if ep == "/menu/click" and str(d.get("button") or "") == "mainButton":
            CALLS.append(("POST", ep, d))
            return {"ok": True, "clicked": "button"}     # 游戏：这一按没被吃进去
        return _orig_p15(ep, data)

    api._ai_post = _p_noconsume
    CALLS.clear()
    r = M._im_prize_go()
    _mb = [c for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"
           and (c[2] or {}).get("button") == "mainButton"]
    ck("…**只按 1 次**就停手（券没掉就不再按）", len(_mb) == 1, str(len(_mb)))
    ck("…回执**不说「兑了」**", "兑了" not in r, r)
    ck("…如实说「一张都没兑成」+ 券数没掉 + 手头还是 2 张",
       "一张都没兑成" in r and "没被游戏吃掉" in r and "还是 2 张" in r, r)
    ck("…⛔ 不拿「背包满」顶锅（反编译：满包照样扣券、东西落地）",
       "不是背包满" in r, r)
    api._ai_post = _orig_p15

    # ⑮④ 不在 `ManorHouse` ⇒ **先 `map_go`**；已在 ⇒ **不重复 `map_go`**（⛔ 别白跨一趟）
    print("\n⑮④ 不在 ManorHouse ⇒ 先 `map_go`；已在 ⇒ **一次都不打**")
    _stub(loc="Town", prizes=1, xy=(1, 6))
    CALLS.clear()
    r = M._im_prize_go()
    _maps = [c for c in CALLS if c[0] == "MAPGO"]
    ck("…不在 ⇒ 真跨了那一图、而且**只跨一次**",
       len(_maps) == 1 and _maps[0][1] == "刘易斯家(特别订单兑奖机)", str(_maps))
    ck("…跨完照样把它兑成了", "兑了 1 张" in r, r)
    _stub(loc="ManorHouse", prizes=1, xy=(1, 6))
    CALLS.clear()
    r = M._im_prize_go()
    ck("…已在 ⇒ **一次 `map_go` 都不打**",
       not any(c[0] == "MAPGO" for c in CALLS), str(CALLS))
    ck("…并在屋里直接兑成", "兑了 1 张" in r, r)

    # ── 📍 交付点提示表（补75：补上「皮埃尔优选」「历史的碎片」两条；坐标=游戏自己的 DropBox 瓦片）──
    print("\n📍 交付点提示表：两条新补的要跟游戏 Action 瓦片对得上（203z补75）")
    _dh = M._DELIVERY_HINT_BY_NAME
    ck("`皮埃尔优选` 有交付提示、写的是 SeedShop(18,28)/(19,28)、站(19,29)",
       "SeedShop(18,28)/(19,28)" in _dh.get("皮埃尔优选", "")
       and "站(19,29)" in _dh.get("皮埃尔优选", ""), _dh.get("皮埃尔优选"))
    ck("`历史的碎片` 有交付提示、写的是 ArchaeologyHouse(6,9)、站(6,10)",
       "ArchaeologyHouse(6,9)" in _dh.get("历史的碎片", "")
       and "站(6,10)" in _dh.get("历史的碎片", ""), _dh.get("历史的碎片"))
    ck("……`_delivery_hint` 按卡名认得出（精确名 + 查无此单要回空串）",
       "SeedShop" in M._delivery_hint("皮埃尔优选")
       and "博物馆" in M._delivery_hint("历史的碎片")
       and M._delivery_hint("查无此单") == "", M._delivery_hint("皮埃尔优选"))
    ck("表里**每条**提示都不是空串（没有占位行）", all(str(v).strip() for v in _dh.values()), "")
    ck("四条已真机核过的交付点（Pam/Robin/Linus/Qi）跟 2026-08-29 那批 `/scan` 结论一致",
       "Trailer(10,6)" in _dh.get("烈酒", "")
       and "ScienceHouse(10,19)" in _dh.get("罗宾的项目", "")
       and "Railroad(28,36)" in _dh.get("社区清理", "")
       and "QiNutRoom(1,4)" in _dh.get("四颗宝石", ""), "")

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
    M.navigation.walk_to = _walk
    M.navigation.map_go = _mapgo
sys.exit(1 if FAIL else 0)
