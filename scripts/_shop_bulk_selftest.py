#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🛒 商店批量卖自验（不碰游戏：stub 掉 M.api）。

恒 2026-09-26：「商店 menu 的 enum 居然没有告诉 ai 可以批量买卖……它现在艰难地一条一条卖鱼中」。
恒 2026-09-27：「**还是会一件一件卖东西！**它可能害怕全卖是把所有东西都卖出去，包括那些种子。」

要证四件事：
  ① `sell_all=True` 真的**逐类循环**调 C#，且**钱包实收**是判据（C# 那个 sold 数是点之前记的，
     商店不收也照报）；
  ② 没给 name 也没点名 sell_all 时**明确报错 + 列出能卖的**，绝不默认"全卖"（不可逆，宁报错别兜底）；
  ③ **同一类的多组品质堆要全卖掉**（C# 一次只卖第一组同名堆，不反复卖就只走一堆）；
  ④ C# **明确报错**（"商店没开"）≠「这店不收」，两件事别说成一句。

⚠️ **2026-09-27 改写说明**：这一版之前假背包里**只有 `displayName`（中文）没有 `name`（英文内部名）**，
  而真机 `/state` 两个字段都有、且 C# 是按 **`name`** 匹配的 ⇒ 旧 fixture 恰好绕开了
  「传中文永远匹配不上」那个真 bug，把坏的验成了绿的。
  旧版还有一条断言是「同类散在两格也只卖一次」——**那正是"只卖掉一堆"的根源**，现已反向。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def chk(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAIL.append(msg)


# ⚠️ 两个字段都给（真机 /state 的形状）；大嘴鲈鱼**两格**（不同品质是不同槽）
BAG = [
    {"name": "Largemouth Bass", "displayName": "[金]大嘴鲈鱼", "stack": 2, "sellable": True},
    {"name": "Largemouth Bass", "displayName": "[银]大嘴鲈鱼", "stack": 1, "sellable": True},
    {"name": "Carp", "displayName": "鲤鱼", "stack": 1, "sellable": True},
    {"name": "Emerald", "displayName": "绿宝石", "stack": 2, "sellable": True},
    {"name": "Axe", "displayName": "斧头", "stack": 1, "sellable": False},   # 工具：游戏不收
]


def _run(bag, sell_ok=True, calls=None):
    """stub：C# `/sell_to_shop` **一次只卖掉一组同名堆**（现实里就是 `break;`），钱包随之上涨。
    没得卖了就回 `ok:false` —— 正好把"反复卖到钱不再涨"那个循环收敛掉。"""
    st = {"money": 7000, "inv": [dict(x) for x in bag]}

    def fake_post(path, data=None, **kw):
        if path != "/sell_to_shop":
            return {}
        nm = data["name"]
        if calls is not None:
            calls.append(nm)
        if not sell_ok:
            return {"ok": False, "error": "No shop menu open"}
        it = next((i for i in st["inv"] if i["name"] == nm), None)
        if it is None:
            return {"ok": False, "error": f"Item '{nm}' not found in inventory"}
        st["inv"].remove(it)
        st["money"] += 100 * it["stack"]
        return {"ok": True, "sold": [{"item": nm, "sold": it["stack"], "totalPrice": 100 * it["stack"]}],
                "remainingGold": st["money"]}

    M.api = type("A", (), {
        "state": staticmethod(lambda **k: {"inventory": st["inv"], "player": {"money": st["money"]}}),
        "_post": staticmethod(fake_post),
    })()
    M._ensure_background = lambda *a, **k: None
    M._with_state = lambda s: s
    M._peer_econ_mute = lambda *a, **k: None
    return M.sell_to_shop.__wrapped__


print("① sell_all=True：逐类循环，不落下任何一类")
calls = []
fn = _run(BAG, calls=calls)
out = fn(name="", sell_all=True)
chk(calls[0] == "Largemouth Bass" and set(calls) == {"Largemouth Bass", "Carp", "Emerald"},
    f"三类都调到、且传的是**英文内部名** → {calls}")
chk("斧头" not in calls, "**工具不卖**（sellable=False 的跳过）")
chk(calls.count("Largemouth Bass") >= 2, "同类两组品质堆**都卖掉**了（旧版只卖一堆）")
chk("大嘴鲈鱼 +300g" in out, f"两堆合计如实报（200+100）→ {out!r}")
chk("卖出 3/3 类" in out, "报清了几类")
chk("实收 +600g" in out, "给**钱包实收**（C# 那个 sold 数是点之前记的，商店不收也照报）")

print("② 没给 name、也没点名 sell_all ⇒ 报错 + 列清单，绝不默认全卖")
calls2 = []
fn = _run(BAG, calls=calls2)
out = fn()
chk(not calls2, "**一次都没调** /sell_to_shop（没有「默认全卖」这种兜底）")
chk("name 必填" in out, "明说 name 必填")
chk("大嘴鲈鱼×3" in out and "绿宝石×2" in out, f"列出能卖的（含数量）×{out!r}")
chk("斧头" not in out, "清单里不掺工具")
chk("sell_all=true" in out, "报错**同时给下一步**（一次全卖怎么敲）")
chk("只卖这店收的" in out, "…并**说清边界**（这才是它敢用 sell_all 的关键）")

print("③ 背包没可卖的 ⇒ 明说，别报成功")
fn = _run([{"name": "Axe", "displayName": "斧头", "stack": 1, "sellable": False}])
chk("没有可卖" in fn(name="", sell_all=True), "sell_all 且无货 ⇒ 明说没有")
chk("没有可卖" in fn(), "单卖且无名字 ⇒ 明说没有")

print("④ C# 报错 ≠「这店不收」（两件事别说成一句）")
fn = _run(BAG, sell_ok=False)
out = fn(name="鲤鱼")
chk("No shop menu open" in out, "C# 的 error 原样上报")
chk("没卖成" in out and "这店不收" not in out, f"**不能把它说成「这店不收」** → {out!r}")

print()
if FAIL:
    print(f"❌ {len(FAIL)} 项没过：")
    for f in FAIL:
        print("   - " + f)
    sys.exit(1)
print("✅ 全绿")
