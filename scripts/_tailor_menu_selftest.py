# -*- coding: utf-8 -*-
"""🧵 缝纫机（`TailoringMenu`）的钉子 —— **不吃游戏**（假的 `_ai_get`/`_ai_post`）。

恒 2026-10-06：「两三个槽位也不复杂，跟锻造台差不多」+「**完全没有做过，没有 enum 原生操作指导**」
⇒ 这一批要的是：**读**（槽里有什么 + 产出预览）· **能放料/取产物**（`/tailor_set` + `menu tailor`）
· **连引导文案一起写**（AI 看单子就知道：放什么、放哪槽、产出什么、怎么取）。

判据来源（全是游戏自己的，见 C# `HandleMenu` 的 `TailoringMenu` 分支）：
  · 左/右料槽 = `leftIngredientSpot.item` / `rightIngredientSpot.item`（public，`TailoringMenu.cs:82-90`）；
  · 产出预览 = `craftResultDisplay.item`（`_ValidateCraft()` 现算）；
    `resultKnown` = 游戏**肯不肯显示**它（`_isDyeCraft || HasTailoredThisItem`，`:1139`）；
  · 能不能开缝 = `IsValidCraft` + `CanFitCraftedItem` + `!IsBusy` + 有预览；
  · "哪件能进哪一槽" = 游戏自己的 `BuildHighlightCache` + `TailorHighlight`（`:24-54/:278-338`）。
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
    "player": {"x": 8, "y": 10, "stamina": 240, "maxItems": 36, "money": 4000,
               "currentItem": None, "currentItemId": None},
    "location": {"name": "SeedShop", "uniqueName": "SeedShop"},
    "inventory": [],
    "activeMenu": {"type": "TailoringMenu"},
}
# 形照 C# 那一档（字段名就是 `/menu.tailor` 的真形状）
TAILOR = {
    "left": {"name": "布", "id": "(O)428", "stack": 1},
    "right": {"name": "海蓝宝石", "id": "(O)66", "stack": 3},
    "result": {"name": "蓝染上衣", "id": "(C)1120", "stack": 1},
    "resultKnown": False,
    "busy": False, "canFit": True, "canStart": True, "heldItem": None,
    "placeable": [
        {"name": "布", "id": "(O)428", "stack": 6, "left": True, "right": False},
        {"name": "海蓝宝石", "id": "(O)66", "stack": 9, "left": False, "right": True},
        {"name": "五彩碎片", "id": "(O)74", "stack": 1, "left": True, "right": True},
    ],
}
MENU = {
    "ok": True, "open": True, "type": "TailoringMenu", "shopId": None,
    "buttons": [{"field": "leftIngredientSpot", "name": "", "x": 100, "y": 200, "w": 96, "h": 96},
                {"field": "rightIngredientSpot", "name": "", "x": 500, "y": 100, "w": 96, "h": 96},
                {"field": "startTailoringButton", "name": "", "x": 600, "y": 200, "w": 96, "h": 96}],
    "shopItems": [{"field": "leftIngredientSpot", "name": "Cloth", "stack": 1}],
    "tailor": dict(TAILOR),
    "slots": [{"index": 0, "x": 50, "y": 500}],
}
CALLS = []
SET_RESULT = {"ok": True, "note": "", "left": "布", "leftId": "(O)428",
              "right": "海蓝宝石", "rightId": "(O)66",
              "result": "蓝染上衣", "resultId": "(C)1120", "resultKnown": False,
              "busy": False, "canStart": True, "heldItem": None}


def _stub(menu=None, tailor=None, set_result=None, menu_raises=False):
    CALLS.clear()
    st = dict(STATE)
    if menu is None:
        st["activeMenu"] = None
    else:
        st = dict(st, activeMenu={"type": menu})
    _m = dict(MENU)
    if tailor is not None:
        _m["tailor"] = tailor

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/state":
            return st
        if ep == "/menu":
            if menu_raises:
                raise RuntimeError("模拟：缝纫机开着但 /menu 读不出来")
            return _m if menu == "TailoringMenu" else {}
        if ep == "/status":
            return {"ok": True, "build": "2026-10-06 00:00:00 @test"}
        return {}

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        if ep == "/tailor_set":
            return dict(set_result if set_result is not None else SET_RESULT)
        return {"ok": True}

    api._ai_get, api._ai_post = g, p
    api._get, api._post = g, p
    api.menu = lambda *a, **k: _m            # `read_menu` 走它
    M._with_state = lambda x, *a, **k: x
    M._ensure_background = lambda *a, **k: None
    M._peer_econ_mute = lambda *a, **k: None
    M.intent_menu.reset_menu()


def _rows():
    return [r.verb.key for r in M.intent_menu._LAST_ROWS]


_real = {n: getattr(M, n) for n in
         ("api", "_with_state", "_ensure_background", "_peer_econ_mute", "tailor_menu")}
try:
    # ① 菜单没开 ⇒ 零额外 HTTP、不给行（也就不会碰 `/tailor_set`）
    print("\n① 菜单没开：**零额外 HTTP** + 不给那一行")
    _stub(menu=None)
    ctx = M._im_ctx()
    ck("`tailor` 是空表", ctx.tailor == {})
    ck("…一次 `/menu` 都不打", not any(c[1] == "/menu" for c in CALLS), str(CALLS))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有「缝纫」", "tailor" not in _rows(), str(_rows()))

    # ② 开着：账进 Ctx + 单子给目录行
    print("\n② 开着 ⇒ 槽/预览/能放哪一槽都进 `Ctx.tailor`，单子给「缝纫…」")
    _stub(menu="TailoringMenu")
    ctx = M._im_ctx()
    t = ctx.tailor
    ck("左/右槽读到了", (t.get("left") or {}).get("name") == "布"
       and (t.get("right") or {}).get("name") == "海蓝宝石", str(t))
    ck("产出预览 + `resultKnown=False`（游戏对没做过的是打问号的）",
       (t.get("result") or {}).get("name") == "蓝染上衣" and t.get("resultKnown") is False, str(t))
    ck("`placeable` 带 left/right 两位（游戏自己算的）",
       len(t.get("placeable") or []) == 3
       and (t["placeable"][2]["left"] is True and t["placeable"][2]["right"] is True), str(t))
    out = M.intent(ops="show", kw={"n": 40})
    ck("单子上有「缝纫…」目录行", "tailor" in _rows(), str(_rows()))
    ck("…理由栏说清两槽 + 产出（且点明打问号）",
       "左 布 / 右 海蓝宝石" in out and "打问号" in out, out)

    # ③ 点开 ⇒ 动作行；敲了当场做 ⇒ 打到 `/tailor_set`
    print("\n③ 点开 ⇒ 一个动作一行；敲了**当场做**（打到 `/tailor_set`）")
    no = next(r.no for r in M.intent_menu._LAST_ROWS if r.verb.key == "tailor")
    acts = M.intent(ops="do", kw={"code": str(no)})
    ck("放料两行（左/右各一行）", "放 布 进左槽" in acts and "放 海蓝宝石 进右槽" in acts, acts)
    ck("…同一件两边都能放的**两行都摆**（五彩碎片）",
       "放 五彩碎片 进左槽" in acts and "放 五彩碎片 进右槽" in acts, acts)
    ck("开缝那行在（`canStart` 为真）", "开缝" in acts, acts)
    CALLS.clear()
    r = M.intent(ops="do", kw={"code": "1"})
    hits = [c[2] for c in CALLS if c[0] == "POST" and c[1] == "/tailor_set"]
    ck("…打的是 `/tailor_set`", bool(hits), str(CALLS))
    ck("…**带着 place + slot**（不是空手去打）",
       hits and hits[0].get("left") == "(O)428" and "right" not in hits[0], str(hits))
    ck("回执照端点的回读说话（左槽/右槽/产出）", "左槽 布" in r and "右槽 海蓝宝石" in r, r)

    # ④ 开缝 / 收产物 / 退料那几行各自打到正确的 action
    #    ⚠️ **每一发都要重新点开那一层**：`exec_on_pick` 那一敲会动单子的栈，
    #       拿上一发算出来的号去敲下一发 = 打到别的行上（第一版就是这么假红的）。
    print("\n④ 开缝 / 收产物 / 退料：各自带对 `action`")

    def _open_tailor(tailor=None):
        _stub(menu="TailoringMenu", tailor=tailor)
        M.intent(ops="show", kw={"n": 40})
        M.intent(ops="do", kw={"code": str(next(r.no for r in M.intent_menu._LAST_ROWS
                                               if r.verb.key == "tailor"))})

    def _hit(prefix):
        no = next(r.no for r in M.intent_menu._LAST_ROWS
                  if (r.label or "").startswith(prefix))
        CALLS.clear()
        M.intent(ops="do", kw={"code": str(no)})
        return [c[2] for c in CALLS if c[0] == "POST" and c[1] == "/tailor_set"]

    _open_tailor(dict(TAILOR, canStart=True,
                      heldItem={"name": "蓝染上衣", "id": "(C)1120", "stack": 1}))
    hits = _hit("收下产物")
    ck("「收下产物」→ `action=take`（且**不带** place）",
       hits and hits[0].get("action") == "take" and not hits[0].get("place"), str(hits))
    _open_tailor(dict(TAILOR, canStart=True,
                      heldItem={"name": "蓝染上衣", "id": "(C)1120", "stack": 1}))
    hits = _hit("开缝")
    ck("「开缝」→ `action=start`", hits and hits[0].get("action") == "start", str(hits))
    _open_tailor(dict(TAILOR, canStart=False))
    hits = _hit("把槽里的料")
    ck("「退料」→ `action=clear`", hits and hits[0].get("action") == "clear", str(hits))

    # ⑤ 没得做 ⇒ 不给行（宁缺勿编）
    print("\n⑤ 空槽 + 背包没料 + 光标空 ⇒ **不给行**")
    _stub(menu="TailoringMenu",
          tailor={"left": None, "right": None, "result": None, "resultKnown": False,
                  "busy": False, "canFit": True, "canStart": False, "heldItem": None,
                  "placeable": []})
    ctx = M._im_ctx()
    ck("`tailor` 有账但没动作", bool(ctx.tailor))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上没有「缝纫」", "tailor" not in _rows(), str(_rows()))

    # ⑥ 端点的 `ok:false` ⇒ 回执不装成功
    print("\n⑥ 端点回 `ok:false` ⇒ 回执如实报（别拿「发出去了」当成了）")
    _stub(menu="TailoringMenu",
          set_result={"ok": False, "error": "背包里没有能放进缝纫机的「布」（或它压根不能当料）"})
    M.intent(ops="show", kw={"n": 40})
    M.intent(ops="do", kw={"code": str(next(r.no for r in M.intent_menu._LAST_ROWS
                                            if r.verb.key == "tailor"))})
    bad = M.intent(ops="do", kw={"code": "1"})
    ck("回执带端点的原话", "不能当料" in bad, bad)
    ck("…**不出现**「左槽 … / 右槽 …」那种读回的快照", "左槽 " not in bad, bad)

    # ⑦ **引导文案**（恒点名的那条）：`menu tailor` + `menu read` + `_close_hint` 三处都要在
    print("\n⑦ 引导文案：放什么、放哪槽、产出什么、怎么取 —— 三处都要有")
    doc = M.tailor_menu.__doc__ or ""
    ck("`menu tailor` 的 docstring 讲清三种 action",
       "start" in doc and "take" in doc and "clear" in doc, doc[:80])
    ck("…讲清左右槽各放什么", "左槽" in doc and "右槽" in doc, doc[:80])
    _stub(menu="TailoringMenu")
    rm = M.read_menu.__wrapped__() if hasattr(M.read_menu, "__wrapped__") else M.read_menu()
    ck("`menu read` 印出两槽内容", "缝纫机 · 左槽" in rm and "海蓝宝石" in rm, rm)
    ck("…印出产出预览并点明**没做过会打问号**", "蓝染上衣" in rm and "打问号" in rm, rm)
    ck("…印出「能放进去的 + 进哪一槽」（引导核心）", "能放进去" in rm and "→" in rm, rm)
    ck("…**给出取产物的那条命令**", "action=take" in rm, rm)
    ck("`_close_hint` 也点名声 `menu tailor`（菜单态的指路）",
       "menu tailor" in M._close_hint("TailoringMenu", content_on_sheet=True)
       and "menu tailor" in M._close_hint("TailoringMenu", content_on_sheet=False))

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
