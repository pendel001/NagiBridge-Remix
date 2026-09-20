# -*- coding: utf-8 -*-
"""🏔️ 探针 3：**时钟在走时**，矿井层到底刷不刷新？（2026-09-20 正面验证）

背景：探针 1/2 在**时钟冻住**时测出「怎么走都是同一张图」；反编译给出机制 =
      刷新挂在 `Game1.cs:6015` 的「每 10 分钟滴答」→ `clearInactiveMines()`。
      恒 09-20 把 TimeSpeed 的流动开回来了 ⇒ 现在可以**正面验**这条结论：
        离开矿井 → **等时钟跨过一个 10 分钟边界**（等 `UpdateMines10Minutes` 真的跑过）
        → 回来 → 指纹应该**变**。
判据：`timeOfDay` 在等待期间必须**至少跳变一次**（否则这次等待不算数，别拿它当证据）。
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


def show(tag, s):
    cu = [x for x in s if x.startswith("(O)849") or x.startswith("(O)751")]
    print(f"  {tag}: {len(s)} 个物体, 铜矿 {len(cu)} 个 {cu}")


def wait_tick():
    """等到时钟确实跨过一个 10 分钟边界（保证 UpdateMines10Minutes 跑过）。"""
    t0 = tod()
    print(f"    等时钟跨 10 分钟边界（现在 {t0}）…")
    for _ in range(40):
        time.sleep(1.0)
        t1 = tod()
        if t1 != t0:
            print(f"    ✅ 跨过了：{t0} → {t1}")
            return True
    print(f"    ❌ 等了 40s 时钟没动（{t0}）—— 又冻住了？这次等待不算数")
    return False


print("① 进 21 层")
warp("UndergroundMine21"); a = fp(); show("A", a)

print("\n② 离开矿井（回 Mountain），等时钟跨过一个 10 分钟边界")
warp("Mountain", 54, 5)
print("     现在:", loc())
ticked = wait_tick()

print("\n③ 回 21 层")
warp("UndergroundMine21"); b = fp(); show("B", b)

print("\n══ 结论 ══")
if not ticked:
    print("  ⏭ 没等到时钟跳变 ⇒ **本次没条件测**，别拿它当证据")
elif a != b:
    print("  ✅ **时钟一走，层就刷新了** —— 正面坐实「刷新由 10 分钟滴答驱动」")
    print(f"     A({len(a)}) → B({len(b)})，指纹不同")
else:
    print("  ⚠️ 跨过了边界指纹还是相同 ⇒ 机制结论要重审（可能不是 lifespan 那条）")
    print(f"     A({len(a)}) == B({len(b)})")
