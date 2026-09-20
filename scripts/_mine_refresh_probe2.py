# -*- coding: utf-8 -*-
"""🏔️ 探针 2：层的缓存**什么时候才丢**？（2026-09-20）

探针 1 已证：21→5→21 和 21→Mountain→21 **都不换图**。
但今天早先确实扫到过**有铜**的 21 层 —— 所以它变过。唯一不同的是：那之后 AI 去过 **61/101/111/121**。
假设：层不是"离图就丢"，而是**走远（层号差很大）才被淘汰**。
做法：记 21 的指纹 → 下到 121 → 回 21 → 再比。
"""
import sys, time
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"


def g(p, **kw):
    return requests.get(f"{AI}{p}", params=kw or None, timeout=20).json()


def post(p, d):
    return requests.post(f"{AI}{p}", json=d, timeout=20).json()


def warp(l, x=5, y=5):
    post("/warp", {"location": l, "x": x, "y": y})
    time.sleep(2.5)


def fp():
    tiles = g("/surroundings", radius=30).get("tiles") or []
    return sorted(f"{t.get('objId')}@{t['x']},{t['y']}" for t in tiles if t.get("objId"))


def show(tag, s):
    cu = [x for x in s if x.startswith("(O)849") or x.startswith("(O)751")]
    print(f"  {tag}: {len(s)} 个物体, 铜矿 {len(cu)} 个 {cu}")


post("/heal", {})

print("① 进 21 层")
warp("UndergroundMine21"); a = fp(); show("A", a)

print("\n② 下到 121（走远）")
warp("UndergroundMine121")
print("     现在:", (g("/state").get("location") or {}).get("name"))

print("\n③ 回 21 层")
warp("UndergroundMine21"); b = fp(); show("B", b)

print("\n④ 再去 61，然后回 21（中等距离）")
warp("UndergroundMine61"); warp("UndergroundMine21"); c = fp(); show("C", c)

print("\n══ 结论 ══")
print(f"  A == B ? {a == b}   （下到 121 再回来）")
print(f"  B == C ? {b == c}   （去 61 再回来）")
if a != b:
    print("  ⇒ **走远会淘汰层缓存**（下到 121 后再回 21 拿到的是新图）")
    print("     ⇒ `出门重进` 刷不动，是因为它只走到 Mountain/邻层，**离得不够远**")
else:
    print("  ⇒ 走远也不换 ⇒ 这层今天就是钉死了（要换只能等翻日）")
