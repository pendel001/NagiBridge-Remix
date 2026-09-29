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
    "player": {"x": 12, "y": 12, "stamina": 268, "maxItems": 36,
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
CHESTS = [{"x": 13, "y": 13, "name": "矿石箱", "capacity": 36, "used": 1, "freeSlots": 35,
           "items": [{"name": "Diamond", "displayName": "钻石", "count": 2,
                      "qualifiedId": "(O)72"}]}]
SEATS = {"seats": [{"kind": "furniture", "name": "木椅", "x": 14, "y": 13,
                    "capacity": 1, "free": 1, "face": False, "dist": 2}],
         "me": {"sitting": False}}
FURNITURE = {"furniture": [{"name": "红沙发", "x": 15, "y": 13, "width": 2, "height": 1}]}
ANIMALS = {"animals": [{"name": "牛牛", "type": "White Cow", "x": 11, "y": 14,
                        "wasPetToday": False, "friendship": 120}]}


def _stub(build="2026-09-29 12:00:00 @abc1234"):
    CALLS.clear()

    def g(ep, params=None):
        CALLS.append(("GET", ep, params))
        return {
            "/status": {"ok": True, "build": build},
            "/state": STATE,
            "/surroundings": SURR,
            "/machines": {"machines": []},
            "/scan_chests": {"chests": CHESTS},
            "/sittable": SEATS,
            "/furniture": FURNITURE,
            "/animals": ANIMALS,
        }.get(ep, {})

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        return {"ok": True,
                "taken": (data or {}).get("count", 1),
                "stored": [{"item": (data or {}).get("name"),
                            "count": (data or {}).get("count", 1)}]}

    api._ai_get, api._ai_post = g, p
    M._with_state = lambda x, *a, **k: x      # 状态机跟"接线"无关，打桩掉
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
    res.append(ok("⚠️ 但**不认** `chest_open`（端点还没写，认了就是骗自己）",
                  "chest_open" not in caps))
    _stub(build="未生成(非 MSBuild 构建)")
    res.append(ok("不是我们编的 DLL ⇒ **空表**（什么都不敢认）", M._im_caps() == {}))

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
    res.append(ok("单子列了容器行", "矿石箱(13,13)" in out))

    # ④ do：**敲号要真打到那个动作上**
    _stub()
    M.intent(ops="show")
    box_no = next(r.no for r in M.intent_menu._LAST_ROWS if "矿石箱" in r.label)
    into_box = M.intent(ops="do", kw={"code": str(box_no)})
    res.append(ok("敲容器行 → 进它的动作面（**不是**又做了一遍顶层的事）",
                  "取…" in into_box and "矿石箱 (13,13)" in into_box))
    # 取：pick → qty → 真打到 /chest_take
    M.intent_menu.reset_menu()
    M.intent(ops="show")
    box_no = next(r.no for r in M.intent_menu._LAST_ROWS if "矿石箱" in r.label)
    M.intent(ops="do", kw={"code": str(box_no)})
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

    # ⑦ ops 写错要有出路（不许静默）
    res.append(ok("不认识的 ops → 报错并列出可用的",
                  "❌" in M.intent(ops="nonsense") and "show" in M.intent(ops="nonsense")))
    res.append(ok("do 不带编号 → 报错 + 给下一步",
                  "❌" in M.intent(ops="do", kw={}) and "code" in M.intent(ops="do", kw={})))

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
