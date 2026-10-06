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
          caps=True, prizes=0):
    """装桩。⚠️ `S` 是**可变的世界状态**（走位/交互/领券都会改它）——
    桩按真端点的语义改它，这样 `_tank_go`/回读那几段才测得到。
    ⚠️ `xy` = **我这一刻站哪格**（默认板前 (62,94)）。领券那几条要传 (60,94) ——
       走位本身留给真机验，但**"没站到旁边就去走"这条闸**在这里钉：桩里的 `walk_to` 会照
       `/walk_to` 的语义把人挪过去（目标格站不住时落到它下面那格，就是真机 `adjusted` 那形状）。
    ⚠️ `prizes` = **手头**的兑奖券（`/state.player.prizeTickets`）；传 `None` = **老 DLL 上没这一位**
       （`/state` 里那个键整个不出现 —— "读不到"那根钉子要的形状，⛔ 别拿 0 顶替它）。"""
    CALLS.clear()
    S.clear()
    S.update({
        "loc": loc, "x": int(xy[0]), "y": int(xy[1]), "facing": 0,
        "vouchers": int(vouchers),
        "unlocked": bool(unlocked), "accepted": bool(accepted),
        "left": bool(left), "right": bool(right),
        "board_open": bool(board_open), "orders": 0,
        # 🎰 手头券 + 那台机器开着没（`PrizeTicketMenu`）——见 `_state`/`p` 里 `/interact` 那支
        "prizes": prizes, "prize_open": False,
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
                "inventory": [], "activeMenu": am}

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
        这里只记一笔，让钉子能证明"**走去兑奖机那一步真的发生了**"。"""
        CALLS.append(("MAPGO", destination, {"npc": npc}))
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
    ck("…理由栏写清机器位置 + 一次一张 + 手头张数",
       "(1,6)" in out and "(1,5)" in out and "一次一张" in out and "手头 1 张" in out, out[:600])

    # ⑫ 不在镇上 ⇒ 不给（跨图那一步不该在农场/矿洞里劝"去兑奖机"）
    print("\n⑫ 不在镇上（Farm）⇒ 不给那一行（哪怕手头有券）")
    _stub(loc="Farm", prizes=3)
    ctx = M._im_ctx()
    ck("…`prizes` 空", ctx.prizes == {}, str(ctx.prizes))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有那一行", "prize" not in _rows(), str(_rows()))

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

    # ⑭ 执行侧两根：没券 ⇒ **当场回绝、一个 `/interact` 都不打**；
    #    有券 ⇒ 真去 `map_go` + 点 (1,5) + **照 `activeMenu.type` 认成没成**
    print("\n⑭ `_im_prize_go`：没券当场回绝（零 `/interact`）；有券真开出来才算成")
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
    ck("…回执说机器开了 + 手头 1 张 + 怎么换（`mainButton`）",
       "兑奖机开了" in r and "手头 1 张" in r and "mainButton" in r, r)
    ck("…⛔ **没替 AI 花券**（`menu/click` 一下都没打）",
       not any(c[1] == "/menu/click" for c in CALLS), str(CALLS))

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
    M.navigation.walk_to = _walk
    M.navigation.map_go = _mapgo
sys.exit(1 if FAIL else 0)
