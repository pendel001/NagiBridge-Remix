# -*- coding: utf-8 -*-
"""🪨 探针：在**会刷大石头的那段层**（40~79，`getMineArea()==40`）找一块 resourceClump 出来（2026-09-20）

反编译给的生成条件（`MineShaft.cs:1598`）：
    else if (mineRandom.NextDouble() <= 0.005 && !isDarkArea() && !mustKillAllMonstersToAdvance()
             && (GetAdditionalDifficulty() <= 0 || (getMineArea() == 40 && mineLevel % 40 < 30)))
        resourceClumps.Add(new ResourceClump(Choose(752,754) /* 困难区: 600树桩/602原木 */, 2, 2, tile));
⇒ 我们这存档是**困难矿井** ⇒ 只有 `getMineArea()==40`（=40~79 层）才刷，0.5%/格、且要 2×2 全空。
做法：把 40~79 里的若干层挨个扫（层会随 10 分钟滴答刷新，扫不到的可以回头再扫）。
"""
import sys, time, collections
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"
FLOORS = [41, 45, 51, 55, 61, 65, 71, 75]


def g(p, **kw):
    return requests.get(f"{AI}{p}", params=kw or None, timeout=20).json()


def post(p, d):
    return requests.post(f"{AI}{p}", json=d, timeout=20).json()


def loc():
    return (g("/state").get("location") or {}).get("name")


hits = []
seen = collections.Counter()
for fl in FLOORS:
    L = f"UndergroundMine{fl}"
    post("/heal", {})
    post("/warp", {"location": L, "x": 5, "y": 5})
    time.sleep(2.5)
    if loc() != L:
        print(f"[{fl}] ⚠️ warp 没到（{loc()}）跳过")
        continue
    tiles = g("/surroundings", radius=30).get("tiles") or []
    res = [(t["x"], t["y"], t.get("resource")) for t in tiles if t.get("resource")]
    for _, _, r in res:
        seen[r] += 1
    flag = "  🔴 找到大石头！" if res else ""
    print(f"[{fl}] {len(tiles)} 格 · resource {len(res)} 个 {res}{flag}")
    if res:
        hits.append((fl, res))

post("/heal", {})

print("\n══ 汇总 ══")
if hits:
    for fl, res in hits:
        print(f"  ✅ {fl} 层: {res}")
    print("  ⇒ **矿井里确实有四格大石头，而且我们的挖矿代码一个字都没读 `resource`** —— 恒说的坐实了")
else:
    print(f"  ⏭ 扫了 {len(FLOORS)} 层没碰上（0.5%/格本来就稀）—— 结论停在：机制上**会刷**，但没抓到实物样本")
    print("     下次刷铁时（farm ore=Iron 走 41 层）留意日志/截图即可，不必专门蹲")
