# -*- coding: utf-8 -*-
"""🏔️ 探针 4：时钟在走时，**多跨几个 10 分钟边界**再回来，图换不换？（2026-09-20）

探针 3 只跨了 1 个边界（1230→1240）就回来 ⇒ 可能太紧（层刚建出来、或 tick 与 warp 有竞态）。
这次：离开矿井后**连跨 3 个边界**（≥30 游戏分钟）再进，且全程盯住"人确实在外面"。
"""
import sys, time
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"


def g(p, **kw):
    return requests.get(f"{AI}{p}", params=kw or None, timeout=20).json()


def post(p, d):
    return requests.post(f"{AI}{p}", json=d, timeout=20).json()


def tod():
    return (g("/state").get("time") or {}).get("timeOfDay")


def loc():
    return (g("/state").get("location") or {}).get("name")


def warp(l, x=5, y=5):
    post("/warp", {"location": l, "x": x, "y": y})
    time.sleep(2.5)


def fp():
    tiles = g("/surroundings", radius=30).get("tiles") or []
    return sorted(f"{t.get('objId')}@{t['x']},{t['y']}" for t in tiles if t.get("objId"))


print("① 进 21 层:", end=" ")
warp("UndergroundMine21"); a = fp(); print(f"{len(a)} 个物体 / t={tod()}")

print("\n② 回 Mountain，连跨 3 个 10 分钟边界")
warp("Mountain", 54, 5)
print(f"   人在 {loc()}，t={tod()}")
t0 = tod(); seen = [t0]
deadline = time.time() + 90
while len(set(seen)) < 4 and time.time() < deadline:
    time.sleep(1.0)
    t = tod()
    if t != seen[-1]:
        seen.append(t)
    if loc() != "Mountain":
        print(f"   ⚠️ 人不在 Mountain 了（现在 {loc()}）")
print(f"   跨过的时刻: {seen}（人在 {loc()}）")
crossed = len(set(seen)) - 1
if crossed < 3:
    print(f"   ⚠️ 只跨了 {crossed} 个边界（没到 3）—— 结果要打折看")

print("\n③ 回 21 层:", end=" ")
warp("UndergroundMine21"); b = fp(); print(f"{len(b)} 个物体 / t={tod()}")

print("\n══ 结论 ══")
print(f"  A({len(a)}) vs B({len(b)})  指纹相同? {a == b}")
if a != b:
    print("  ✅ 跨多个边界后**换了图** ⇒ 「10 分钟滴答驱动刷新」正面成立（1 个边界那次是太紧）")
else:
    print("  ⚠️ 跨了 " + str(crossed) + " 个边界仍然是同一张图 ⇒ **机制结论未坐实**，"
          "我之前写进 CHANGELOG 的「lifespan/10 分钟滴答」只能算**假设**，得降级")
