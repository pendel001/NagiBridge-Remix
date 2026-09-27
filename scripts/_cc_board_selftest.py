#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🏛️ 献祭板 `menu read` 自验（不碰游戏：stub 掉 M.api，直接打裸函数）。

恒 2026-09-25：「献祭 ai 也不会传参！点哪里看收集包详细呢？怎么对应上呢？」
          「他现在在猜哪个图标包裹对应哪个收集包。这个我们得写好才行……」

要证的就是**图标 ↔ 收集包**这层对应：C# 传回来的 `bundleBounds` 是按**槽序号**排的，
必须跟 `bundles` **按下标**对齐，再拿 `index` 去 `/bundles` 反查显示名 —— 三者错一个都会指错包。
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


# ── 假数据：照真机抓下来的那一条（鱼缸 6 包 / 列表页）──
AREA2 = {
    "whichArea": 2, "areaName": "鱼缸", "specificBundlePage": False, "heldItem": None,
    "bundleBounds": [{"x": 592, "y": 136, "w": 64, "h": 64}, {"x": 392, "y": 384, "w": 64, "h": 64},
                     {"x": 784, "y": 388, "w": 64, "h": 64}, {"x": 304, "y": 252, "w": 64, "h": 64},
                     {"x": 892, "y": 252, "w": 64, "h": 64}, {"x": 588, "y": 276, "w": 64, "h": 64}],
    "bundles": [
        {"index": 6, "complete": False, "ingredients": [
            {"id": "145", "name": "太阳鱼", "count": 1, "completed": False},
            {"id": "143", "name": "鲶鱼", "count": 1, "completed": True},
            {"id": "706", "name": "西鲱", "count": 1, "completed": True},
            {"id": "699", "name": "虎纹鳟鱼", "count": 1, "completed": False}]},
        {"index": 7, "complete": True, "ingredients": [
            {"id": "136", "name": "大嘴鲈鱼", "count": 1, "completed": True}]},
        {"index": 8, "complete": False, "ingredients": [
            {"id": "131", "name": "沙丁鱼", "count": 1, "completed": False},
            {"id": "130", "name": "金枪鱼", "count": 1, "completed": False},
            {"id": "150", "name": "红鲷鱼", "count": 1, "completed": False},
            {"id": "701", "name": "罗非鱼", "count": 1, "completed": False}]},
        {"index": 9, "complete": False, "ingredients": [
            {"id": "140", "name": "大眼鱼", "count": 1, "completed": False},
            {"id": "132", "name": "鲷鱼", "count": 1, "completed": False},
            {"id": "148", "name": "鳗鱼", "count": 1, "completed": False}]},
        {"index": 10, "complete": False, "ingredients": [
            {"id": "128", "name": "河豚", "count": 1, "completed": False},
            {"id": "156", "name": "鬼鱼", "count": 1, "completed": False}]},
        {"index": 11, "complete": False, "ingredients": [
            {"id": "715", "name": "龙虾", "count": 1, "completed": False},
            {"id": "372", "name": "蛤", "count": 1, "completed": False}]},
    ],
}
LABELS = {6: "河鱼", 7: "湖鱼", 8: "海鱼", 9: "夜间鱼", 10: "特产鱼", 11: "蟹笼"}
HAVE = {"太阳鱼", "大嘴鲈鱼", "史莱姆泥"}


def _run(cc, labels=LABELS, have=HAVE):
    """把 read_menu 里那段逻辑跑起来：stub 掉 api.menu/state/_get 三个出口。"""
    M.api = type("A", (), {
        "menu": staticmethod(lambda: {"open": True, "type": "JunimoNoteMenu", "characterCust": cc,
                                      "buttons": [{"name": "backButton", "x": 120, "y": 116}]}),
        "state": staticmethod(lambda: {"inventory": [{"displayName": n} for n in have]}),
        "_get": staticmethod(lambda p, params=None: {"areas": [
            {"area": 2, "name": "鱼缸", "bundles": [{"index": i, "name": n} for i, n in labels.items()]}]}),
    })()
    M._ensure_background = lambda *a, **k: None
    M._with_state = lambda s: s
    return M.read_menu.__wrapped__()


print("① 列表页：图标 ↔ 收集包 ↔ 坐标 三者对齐")
out = _run(AREA2)
chk("河鱼" in out and "湖鱼" in out and "蟹笼" in out, "六包都报出了名字（不是「收集包#6」）")
# 每个**未完成**的包都得指到**它自己那块图标**的中心（bounds[i] + 32）——错位就是把 AI 指去别的包
for _nm, _xy in (("河鱼", (624, 168)), ("海鱼", (816, 420)),
                 ("夜间鱼", (336, 284)), ("特产鱼", (924, 284)), ("蟹笼", (620, 308))):
    chk(f"menu click(x={_xy[0]}, y={_xy[1]})" in out, f"{_nm} ↦ 图标中心 {_xy}")
chk("✅ 湖鱼（已做完" in out, "已做完的包标 ✅ 且说明点不开")
chk("🖱️ menu click" not in out.split("✅ 湖鱼")[1].split("⬜")[0], "已做完的包**不给**点击坐标")
chk("缺: 太阳鱼、虎纹鳟鱼" in out, "河鱼只列**没捐的**两件（鲶鱼/西鲱已捐就不再列）")
chk("🎒 **你现在就能捐**: 太阳鱼" in out, "背包里有太阳鱼 ⇒ 标出来（这是「怎么对应上」的另一半）")
chk("现在就能捐**: 大嘴鲈鱼" not in out, "已完成的包不掺进「现在就能捐」")
chk("这一页的图标不用猜" in out or "上面的图标不用猜" in out, "给 AI 一句「不用猜」的定心话")

print("② 具体页：新 DLL 有 currentBundleIndex ⇒ 认得出是哪一包")
SP = dict(AREA2, specificBundlePage=True, currentBundleIndex=6)
out = _run(SP)
chk("「河鱼」具体页" in out, "报出包名 + 「具体页」")
chk("⭕已捐 鲶鱼" in out and "⬜ 太阳鱼" in out, "逐槽标已捐/未捐")
chk("menu click(item=太阳鱼)" in out, "「现在就能捐」直接给可复制的 item= 命令")
chk("backButton" in out, "给「退回列表页」的路")

print("③ 具体页：老 DLL 没有 currentBundleIndex ⇒ 如实说读不到，**不许瞎猜**")
SP_OLD = dict(AREA2, specificBundlePage=True)
out = _run(SP_OLD)
chk("读不到这一页是**哪一包**" in out, "明说读不到")
chk("别按槽位数猜" in out, "写明不许按槽位数猜")
chk("menu click(button=backButton)" in out, "给下一步（退回列表页能看到每包坐标）")
chk("河鱼」具体页" not in out, "没有硬编一个包名出来")

print("④ 金库：要的是钱不是物 ⇒ 不说「捐物品」")
AREA4 = {
    "whichArea": 4, "areaName": "金库", "specificBundlePage": True, "currentBundleIndex": 23,
    "heldItem": None, "bundleBounds": [{"x": 592, "y": 136, "w": 64, "h": 64}],
    "bundles": [{"index": 23, "complete": False, "ingredients": [
        {"id": None, "name": "", "count": 2500, "quality": 2500, "completed": False}]}],
}
out = _run(AREA4, labels={23: "2500 金"})
chk("💰 2500 金" in out, "金库包按钱显示")
chk("purchaseButton" in out, "指到花钱买（purchaseButton）")
chk("menu click(item=" not in out, "金库**不**给 item= 捐赠指令（它没有物品可捐）")

print("⑤ 老 DLL 的键名对不上时：bounds 数 ≠ 包数 ⇒ 报出来、别乱点")
BAD = dict(AREA2, bundleBounds=AREA2["bundleBounds"][:3])
out = _run(BAD)
chk("图标坐标读不到" in out or "对不上" in out, "bounds/包数不齐 ⇒ 明说读不到")
chk("menu click(x=" not in out, "不齐时一个坐标都不给（宁缺勿错）")

print()
if FAIL:
    print(f"❌ {len(FAIL)} 项没过：")
    for f in FAIL:
        print("   - " + f)
    sys.exit(1)
print("✅ 全绿")
