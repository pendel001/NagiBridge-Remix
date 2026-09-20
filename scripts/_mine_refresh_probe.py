# -*- coding: utf-8 -*-
"""🏔️ 探针：矿井的层**到底刷不刷新**？（2026-09-20 恒提问）

起因：`farm ore=Copper` 连刷的 3 次「出门重进刷新」+ 我复验的 6 次换层，
      **每次都扫出同一份 25 个物体、0 个铜** —— 这不叫随机，这叫同一张图。
假设 A：层一旦建出来就**留在 `Game1.locations` 里**，同一天内反复进同一层拿到的是同一个实例（SDV 的层缓存）。
假设 B：只有**真正离开矿井**（回 Mountain/农场）才会把它丢掉重建 ⇒ `出门重进` 该有效。
做法：在固定落点 (5,5) 扫 radius=30，把"物体签名"（objId@坐标 排序后）当指纹，比较三种走法前后的指纹。
"""
import sys, json, time
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"


def g(p, **kw):
    return requests.get(f"{AI}{p}", params=kw or None, timeout=20).json()


def post(p, d):
    return requests.post(f"{AI}{p}", json=d, timeout=20).json()


def loc():
    s = g("/state")
    return (s.get("location") or {}).get("name"), (s.get("player") or {}).get("health")


def fingerprint():
    """当前层的物体指纹：objId@x,y 排序。"""
    tiles = g("/surroundings", radius=30).get("tiles") or []
    sig = sorted(f"{t.get('objId')}@{t['x']},{t['y']}" for t in tiles if t.get("objId"))
    return sig


def warp(l, x=5, y=5):
    post("/warp", {"location": l, "x": x, "y": y})
    time.sleep(2.5)


def show(tag, sig):
    cu = [s for s in sig if s.startswith("(O)849") or s.startswith("(O)751")]
    print(f"  {tag}: {len(sig)} 个物体, 铜矿 {len(cu)} 个")


# 先回血，别探着探着死在里面
post("/heal", {})
print("回血后:", loc())

print("\n① 第一次进 21 层")
warp("UndergroundMine21")
a = fingerprint(); show("A", a)

print("\n② 换一层再回来（21 → 5 → 21）")
warp("UndergroundMine5")
warp("UndergroundMine21")
b = fingerprint(); show("B", b)

print("\n③ 真正离开矿井再回来（21 → Mountain → 21）")
warp("Mountain", 54, 5)
print("     已离开，现在:", loc()[0])
warp("UndergroundMine21")
c = fingerprint(); show("C", c)

print("\n══ 结论 ══")
print(f"  A == B ? {a == b}   （换层再回来刷不刷新）")
print(f"  A == C ? {a == c}   （离开矿井再回来刷不刷新）")
if a == b == c:
    print("  ⇒ 三种走法拿到**同一张图**：层是缓存的，两条'刷新'路都**没刷**")
elif a == b and a != c:
    print("  ⇒ 换层没用，**只有真正离开矿井**才会重建")
elif a != b and b == c:
    print("  ⇒ 换层就能刷，离开矿井反而没差别")
else:
    print("  ⇒ 每次都不一样（确实在随机重建）")

# 顺便看看这层到底有什么
from collections import Counter
print("\n  A 层物体构成:", dict(Counter(s.split("@")[0] for s in a)))
