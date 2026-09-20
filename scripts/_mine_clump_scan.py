# -*- coding: utf-8 -*-
"""🪨 扫矿井各层找资源堆（2026-09-20）——困难矿井

**为什么是这些层**：反编译 `MineShaft.cs` 刷层循环里生成资源堆的那个分支（0.5%/格）：

    else if (rand <= 0.005 && !isDarkArea() && !mustKillAllMonstersToAdvance()
             && (GetAdditionalDifficulty() <= 0 || (getMineArea() == 40 && mineLevel % 40 < 30)))

· **普通矿井**（`GetAdditionalDifficulty()<=0`）：左边那项恒真 ⇒ 各层都可能刷
  `Choose(752,754)`；`getMineArea()==40` 的层改刷 `Choose(756,758)`。
· **困难矿井**（恒这个档 `mineHardMode=True`）：`GetAdditionalDifficulty()>0`
  ⇒ **只有 `getMineArea()==40 && mineLevel%40<30`** 才刷 ⇒ **层 80~109**；
  且因为 area==40、难度>0，类型被改成 **600 树桩 / 10% 602 原木**。

⇒ 所以本探针默认扫 **80~109**（困难矿井唯一会刷资源堆的段）。

⚠️ 会 `/warp` 进矿（**非拟人瞬移，是摆场**）；**跑完一定把人带出来**（`finally` 里回农场 + `/heal`）
   —— 09-20 有过"把人丢在矿里不管"的教训。
"""
import sys
import time
import requests

BASE = "http://localhost:7843"
FLOORS = list(range(40, 70, 3))   # getMineArea()==40 且 mineLevel%40<30 ⇒ 40~69 层
HOME = ("Farm", 64, 15)


def g(path, **kw):
    return requests.get(f"{BASE}{path}", params=kw or None, timeout=60).json()


def post(path, d):
    return requests.post(f"{BASE}{path}", json=d, timeout=60).json()


def where():
    s = g("/state")
    l = (s.get("location") or {}).get("name")
    p = s.get("player") or {}
    return l, p.get("x"), p.get("y"), p.get("health")


def scan(loc):
    r = g("/passable_rect", x1=0, y1=0, x2=90, y2=90)
    return r.get("location"), (r.get("tiles") or [])


def main():
    print(f"🌍 出发前 {where()}")
    clumps_by_floor = {}
    try:
        for fl in FLOORS:
            loc = f"UndergroundMine{fl}"
            post("/warp", {"location": loc, "x": 5, "y": 5})
            time.sleep(2.5)
            post("/heal", {})
            cur, x, y, hp = where()
            if cur != loc:
                print(f"  [{fl:>3}] ⚠️ warp 没到（现在 {cur}）——跳过")
                continue
            got_loc, tiles = scan(cur)
            cl = [(t["x"], t["y"], t["resource"]) for t in tiles if t.get("resource")]
            clumps_by_floor[fl] = cl
            by = {}
            for cx, cy, n in cl:
                by.setdefault(n, []).append((cx, cy))
            desc = ", ".join(f"{k}×{len(v)//4}件{v}" for k, v in by.items()) if by else "（本层没有）"
            print(f"  [{fl:>3}] {got_loc:22s} {len(tiles):>5}格  HP={hp}  资源堆={len(cl):>3}格  {desc[:110]}")

        print("\n═══ 汇总 ═══")
        tot = sum(len(v) for v in clumps_by_floor.values())
        if tot == 0:
            print("🕳️ 这 8 层一件资源堆都没有。")
            print("   （0.5%/格是**低概率**，不是每层必有 —— 换层/多扫几层再看，别下'游戏里没有'的结论。）")
        else:
            for fl, cl in clumps_by_floor.items():
                if cl:
                    print(f"  [{fl}] {len(cl)} 格: {cl[:8]}")
    finally:
        print("\n🚶 收尾：把人带出矿井（教训：摆场是借，借完必须还）")
        post("/warp", {"location": HOME[0], "x": HOME[1], "y": HOME[2]})
        time.sleep(2)
        post("/heal", {})
        print(f"   {where()}")


if __name__ == "__main__":
    raise SystemExit(main())
