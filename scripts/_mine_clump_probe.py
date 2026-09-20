# -*- coding: utf-8 -*-
"""🪨 探针：矿井里有没有**四格大石头**（resourceClump），我们的挖矿代码摸得到吗？（2026-09-20 恒提问）

恒：「我们的挖矿相关好像也从来没探测过四格大石头」——
  实测 `grep resource mine_run.py` **零命中** ⇒ 挖矿代码只读 `tiles[].object`，**从不读 `tiles[].resource`**。
本探针查两件事：
  ① 矿井各层到底有没有 resourceClump（有的话我们一直在无视它们）；
  ② 顺带看看 `_rock_name` 对它们说什么（应为 None → 直接进不了 find_rocks）。
"""
import sys, time, collections
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"


def g(p, **kw):
    return requests.get(f"{AI}{p}", params=kw or None, timeout=20).json()


def post(p, d):
    return requests.post(f"{AI}{p}", json=d, timeout=20).json()


def loc():
    return (g("/state").get("location") or {}).get("name")


def warp(l, x=5, y=5):
    post("/warp", {"location": l, "x": x, "y": y})
    time.sleep(2.5)


post("/heal", {})
found_total = collections.Counter()
for fl in (21, 41, 61, 81, 101):
    L = f"UndergroundMine{fl}"
    warp(L)
    if loc() != L:
        print(f"[{fl}] ⚠️ warp 没到（{loc()}）跳过")
        continue
    tiles = g("/surroundings", radius=30).get("tiles") or []
    res = [(t["x"], t["y"], t.get("resource")) for t in tiles if t.get("resource")]
    objs = collections.Counter(t.get("objId") for t in tiles if t.get("objId"))
    print(f"\n[{fl}] {L}: {len(tiles)} 格")
    print(f"    有 object 的: {dict(objs)}")
    print(f"    🔴 有 resource 的: {len(res)} 个 {res}")
    for _, _, r in res:
        found_total[r] += 1

print("\n══ 汇总 ══")
print("  resourceClump 分布:", dict(found_total) or "**五层一个都没有**")
if found_total:
    print(f"  ⇒ 矿井里确实有 resourceClump（{sum(found_total.values())} 个），而 mine_run **只读 object** ⇒ 完全无视")
    print("  ⇒ 印证恒：「从来没探测过四格大石头」")
else:
    print("  ⇒ 这五层没扫到 resourceClump —— 不代表别的层没有（大石头在矿井里本来就稀少）")
post("/heal", {})
