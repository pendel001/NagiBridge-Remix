# -*- coding: utf-8 -*-
"""🐄 买动物菜单（`PurchaseAnimalsMenu`）的钉子 —— **不吃游戏**（假的 `_ai_get`/`_ai_post`）。

恒 2026-10-06：「`/menu` 对它只回 `shopItems:null, listTotal:0` ⇒ 读不出来（所以单子没法给行）」。
这一批给它三件套：**读**（C# 报 `animalShop`）· **点**（`/menu/click {animal, animal_name}`）·
**单子给一行**（`Ctx.animals_for_sale` → 「买动物」）。

判据要盯的三件事（都在服务器那一处，`_im_animals`）：
  ① 「能不能买」= **游戏自己那条点击守卫**（`readOnly` / 缺必需建筑），不是我们编的名单；
  ② 「买不买得起」= 钱包 vs 游戏报的价格；
  ③ 「有没有空棚」= 游戏自己的 `CanLiveIn` + `AnimalHouse.isFull()`（C# 里算好，Python 只读）。
⇒ 三样全过才进 `buy`（单子上出现的那只，按了就成）；没过的进 `nobuy` 并**写明为什么**。

⚠️ 手法同 `_intent_wiring_selftest`：只打桩网络层与外壳（`_with_state` 等），
   **一个字节都不碰游戏**（`_net_guard` 还盯着——真发了请求会当场 ConnectionRefusedError）。
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


# ── 桩的形状**照 C# 回包原样**（字段名一个都不许自己改）────────────────────
STATE = {
    "player": {"x": 12, "y": 16, "stamina": 200, "maxItems": 36, "money": 12000,
               "currentItem": None, "currentItemId": None},
    "location": {"name": "AnimalShop", "uniqueName": "AnimalShop"},
    "inventory": [],
    "activeMenu": {"type": "PurchaseAnimalsMenu"},
}
# 四只动物分别覆盖四条判据：能买 / 钱不够 / 缺建筑 / 没空棚
ANIMALS = [
    {"index": 0, "id": "White Chicken", "name": "白色鸡", "price": 800,
     "canBuy": True, "affordable": True, "missingBuilding": False, "missingText": None,
     "house": "Coop", "freeHouse": "Coop", "visible": True,
     "bounds": {"x": 100, "y": 100, "w": 128, "h": 64}},
    {"index": 1, "id": "Pig", "name": "猪", "price": 16000,
     "canBuy": True, "affordable": False, "missingBuilding": False, "missingText": None,
     "house": "Deluxe Barn", "freeHouse": "Deluxe Barn", "visible": True,
     "bounds": {"x": 200, "y": 100, "w": 128, "h": 64}},
    {"index": 2, "id": "Ostrich", "name": "鸵鸟", "price": 60000,
     "canBuy": False, "affordable": True, "missingBuilding": True,
     "missingText": "你需要一间畜棚", "house": "Barn", "freeHouse": None, "visible": True,
     "bounds": {"x": 300, "y": 100, "w": 128, "h": 64}},
    {"index": 3, "id": "Duck", "name": "鸭", "price": 4000,
     "canBuy": True, "affordable": True, "missingBuilding": False, "missingText": None,
     "house": "Big Coop", "freeHouse": None, "visible": True,
     "bounds": {"x": 100, "y": 200, "w": 128, "h": 64}},
]
MENU = {
    "ok": True, "open": True, "type": "PurchaseAnimalsMenu",
    "shopItems": None, "shopId": None, "buttons": [{"name": "okButton", "x": 700, "y": 600}],
    "animalShop": {"readOnly": False, "targetLocation": "Farm", "money": 12000,
                   "onFarm": False, "namingAnimal": False, "chosen": None, "chosenPrice": 0,
                   "animals": [dict(a) for a in ANIMALS]},
}
CALLS = []
BUY_RESULT = {"ok": True, "clicked": "animal_buy", "animal": "White Chicken",
              "name": "小鸡一号", "price": 800, "cost": 800, "building": "Coop",
              "animals": "4/4", "money": 11200}


def _stub(menu=None, state=None, menu_raises=False, buy_result=None):
    """装桩：`/state`/`/menu`/…… 全从这里出；`menu=None` = **菜单没开**。"""
    CALLS.clear()
    st = dict(STATE, **(state or {}))
    if menu is None:
        st["activeMenu"] = None
    else:
        st = dict(st, activeMenu=dict({"type": menu}))
    _menu_raw = MENU if menu == "PurchaseAnimalsMenu" else {}

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        if ep == "/state":
            return st
        if ep == "/menu":
            if menu_raises:
                raise RuntimeError("模拟：菜单开着但 /menu 读不出来")
            return _menu_raw
        if ep == "/status":
            return {"ok": True, "build": "2026-10-06 00:00:00 @test"}
        return {}

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        if ep == "/menu/click":
            return dict(buy_result if buy_result is not None else BUY_RESULT)
        return {"ok": True}

    api._ai_get, api._ai_post = g, p
    api._get, api._post = g, p
    M._with_state = lambda x, *a, **k: x
    M._ensure_background = lambda *a, **k: None
    M._peer_econ_mute = lambda *a, **k: None
    M.intent_menu.reset_menu()


def _row_keys():
    """这一屏**单子上的行**（按 verb.key）—— ⚠️ 别拿整屏文本去 `in` 判：
    屏上还有**服务器给的指引**（`ctx.menu_hint`/`_close_hint` 里就写着"买动物屏"那几个字），
    拿它当判据会把"指引里提了一句"误判成"给了那一行"。"""
    return [r.verb.key for r in M.intent_menu._LAST_ROWS]


_real = {n: getattr(M, n) for n in ("api", "_with_state", "_ensure_background", "_peer_econ_mute")}
try:
    # ① 菜单没开 ⇒ 一个字都不多花，也不给行
    print("\n① 菜单没开：**零额外 HTTP** + 不给那一行")
    _stub(menu=None)
    ctx = M._im_ctx()
    ck("没开买动物菜单 ⇒ `animals_for_sale` 是空表", ctx.animals_for_sale == {})
    ck("…**一次 `/menu` 都不打**（平时不多花一发）",
       not any(c[1] == "/menu" for c in CALLS), str(CALLS))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上**没有那一行**", "animal" not in _row_keys(), str(_row_keys()))

    # ② 开着：三条件全过才进 `buy`，没过的**写明为什么**
    print("\n② 开着：`buy` = 三条件全过；`nobuy` = 各自写明原因")
    _stub(menu="PurchaseAnimalsMenu")
    ctx = M._im_ctx()
    a = ctx.animals_for_sale
    ck("能买的那只进了 `buy`（且带游戏报的空棚类型）",
       [x["name"] for x in (a.get("buy") or [])] == ["白色鸡"]
       and bool(a.get("buy")) and (a["buy"][0].get("house") == "Coop"), str(a.get("buy")))
    why = {x["name"]: x["why"] for x in a.get("nobuy") or []}
    ck("…钱不够的那只：理由里**带钱数**（不是一句「买不了」）",
       "钱不够" in why.get("猪", "") and "16000" in why.get("猪", ""), str(why))
    ck("…缺建筑的那只：照抄**游戏自己那句原文**", why.get("鸵鸟") == "你需要一间畜棚", str(why))
    ck("…没空棚的那只：理由点名是**哪种棚没有空的**",
       "Big Coop" in why.get("鸭", "") and "空" in why.get("鸭", ""), str(why))

    # ③ 单子：目录行 → 一只一行 → 敲了真打到 `/menu/click`
    print("\n③ 单子：目录行 → 敲 1 ⇒ 真打到 `/menu/click`（带 `animal`）")
    out = M.intent(ops="show", kw={"n": 40})
    ck("单子上有「买动物…」目录行（句尾 `…`）", "买动物…" in out or "买动物" in out, out)
    ck("…理由栏给钱包", "钱包 12000g" in out, out)
    no = next(r.no for r in M.intent_menu._LAST_ROWS if r.verb.key == "animal")
    shelf = M.intent(ops="do", kw={"code": str(no)})
    ck("点开 ⇒ 能买的那一只在（名/价/棚）",
       "白色鸡" in shelf and "800g" in shelf and "Coop" in shelf, shelf)
    ck("…**买不了的那几只不在这层**（别摆按不成的行）",
       "鸵鸟" not in shelf and "猪" not in shelf, shelf)
    CALLS.clear()
    r = M.intent(ops="do", kw={"code": "1"})
    hits = [c[2] for c in CALLS if c[0] == "POST" and c[1] == "/menu/click"]
    ck("敲 1 ⇒ 打的是 `/menu/click` 且带 `animal`",
       bool(hits) and hits[0].get("animal") == "White Chicken", str(hits))
    ck("…传的是**游戏那个 id**（不是中文名 —— C# 那边按 id 找组件的）",
       hits and hits[0].get("animal") == "White Chicken", str(hits))
    ck("回执逐条报**游戏回读的事实**（花了多少/进哪栋/现在几只）",
       "800g" in r and "Coop" in r and "4/4" in r, r)

    # ④ 端点说没买成 ⇒ 回执**不许装成功**
    print("\n④ 端点回 `ok:false` ⇒ 回执如实报、不装成功")
    _stub(menu="PurchaseAnimalsMenu",
          buy_result={"ok": False, "error": "没买成、钱没动：名字「小鸡一号」已经有别的动物用了"})
    M.intent(ops="show", kw={"n": 40})
    M.intent(ops="do", kw={"code": str(next(r.no for r in M.intent_menu._LAST_ROWS
                                            if r.verb.key == "animal"))})
    bad = M.intent(ops="do", kw={"code": "1"})
    ck("回执写**没买成**并带游戏原话", "没买成" in bad and "名字" in bad, bad)
    ck("…**不出现**「花了 …g → 进 …」那种像成功的话", "→ 进" not in bad, bad)

    # ⑤ 读不出来 ⇒ 整行不出现（宁缺勿编）
    print("\n⑤ `/menu` 读不出来（老 DLL / 报错）⇒ 整行不出现")
    _stub(menu="PurchaseAnimalsMenu", menu_raises=True)
    ctx = M._im_ctx()
    ck("读不出来 ⇒ `{}`（不是编一份假货架）", ctx.animals_for_sale == {}, str(ctx.animals_for_sale))
    M.intent(ops="show", kw={"n": 40})
    ck("…单子上**没有那一行**", "animal" not in _row_keys(), str(_row_keys()))
    _stub(menu="PurchaseAnimalsMenu")
    _saved = MENU.get("animalShop")
    MENU["animalShop"] = None
    try:
        ctx = M._im_ctx()
        ck("`animalShop` 缺失（老 DLL 的形状）⇒ 也不给行", ctx.animals_for_sale == {})
    finally:
        MENU["animalShop"] = _saved

    # ⑥ 已经在"挑棚"阶段 ⇒ 不给行（货架在这一刻不算数）
    print("\n⑥ 菜单已经进了「挑棚」阶段 ⇒ 不给行（走位那一步不在我们手里）")
    _saved_as = MENU["animalShop"]
    MENU["animalShop"] = dict(_saved_as, onFarm=True, chosen="鸭", chosenPrice=4000,
                              namingAnimal=False)
    try:
        _stub(menu="PurchaseAnimalsMenu")
        ctx = M._im_ctx()
        ck("phase 报 placing、`buy` 空", (ctx.animals_for_sale or {}).get("phase") == "placing"
           and not (ctx.animals_for_sale or {}).get("buy"), str(ctx.animals_for_sale))
        M.intent(ops="show", kw={"n": 40})
        ck("…单子上**没有那一行**", "animal" not in _row_keys(), str(_row_keys()))
    finally:
        MENU["animalShop"] = _saved_as

    # ⑦ 别跟商店货架那行混（这是恒点名的那条）
    print("\n⑦ 买动物菜单**不是商店** ⇒ `ctx.shop` 是 None、「买…」那行不出现")
    _stub(menu="PurchaseAnimalsMenu")
    ctx = M._im_ctx()
    ck("`ctx.shop is None`（菜单类型不含 shop 子串）", ctx.shop is None, str(ctx.shop))
    M.intent_menu.render_menu(ctx, n=40)
    ck("…单子上没有商店那行「买…」",
       not any(r.verb.key == "buy" for r in M.intent_menu._LAST_ROWS),
       str([r.verb.key for r in M.intent_menu._LAST_ROWS]))

    # ⑧ 🔴 2026-10-06 真机逮到（玛妮柜台）：**AI 那扇门根本没接上**
    #    `menu read` 的指引写着「下单走 `menu click(animal=名字…)`」，C# 的 `/menu/click`
    #    也**早就有** `animal`/`animal_name` 分支 —— 可 Python 的 `menu_click()` **没有这两个参数**
    #    ⇒ `_ops_run` 的"参数白名单"把它们**静默丢掉**、回包只提一句「忽略了无法识别的参数 ['animal']」，
    #    然后**点了一下菜单正中央**（真机实测；菜单还开着、什么都没买）。⇒ 现在把它接上，并钉死。
    print("\n⑧ `menu click(animal=…)` 真能走到 C#（⛔ 别再被参数白名单丢掉）")
    _seen = {}
    _saved_api_click = api.menu_click
    api.menu_click = lambda **kw: (_seen.update(kw), {"ok": True, "clicked": "animal_buy"})[1]
    try:
        M.menu_click(animal="White Chicken", animal_name="小鸡一号")
        ck("`animal` 传到了 `menu_click` 底下那层（`api.menu_click`）",
           _seen.get("animal") == "White Chicken", str(_seen))
        ck("…`animal_name` 也一起传到（起名那半）",
           _seen.get("animal_name") == "小鸡一号", str(_seen))
        _seen.clear()
        M.menu_click(button="close")
        ck("…不带 animal 时**不多发**这两个键（别污染别的点击）",
           "animal" not in _seen and "animal_name" not in _seen, str(_seen))
    finally:
        api.menu_click = _saved_api_click

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
