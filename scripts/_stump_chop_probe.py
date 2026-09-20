# -*- coding: utf-8 -*-
"""🪓 真机验「树桩敲几下真碎」——`clear_area.CLUMP_SPEC` 那几个数字对不对（2026-09-20）

**为什么要在矿井里做**：`clear_area` 是**农场独占**的（`scan_area()` 里"不在农场就 warp 回农场"），
而恒两个档的农场**一件资源堆都没有** ⇒ 那条路**结构上测不了**。
但困难矿井的 **40~69 层**会随机刷 `LargeStump`（反编译实锤，见 `_mine_clump_scan.py`），
而 `LargeStump` 跟农场树桩**同一个 `parentSheetIndex`=600、同一套公式** ⇒ 拿它验数字是等效的。

**验什么**（`CLUMP_SPEC["LargeStump"] = ("Axe", 10, 1)`）：
  血量 10、要**铜斧以上**；每击 `max(1, (镐/斧升级级 + 1) * 0.75)`。
  铱斧（Lv4）⇒ 每击 `5*0.75 = 3.75` ⇒ **`ceil(10/3.75) = 3` 下**。
  旧代码写死 **15** 下 ⇒ 这次该看到**第 3 下碎**，不是第 15 下。

**注意本探针测的是 `/use`（mod 那条），不是 `clear_area` 的循环** ——
  共用的是**同一套数字**；`clear_area` 自己的"算 hits / 收 stuck"是 Python 逻辑。
  ⚠️ 别把这次算成"`clear_area` 清场真机验过"。

⚠️ 会 `/warp` 进矿摆场；`finally` 里**把人带回农场 + `/heal`**。
"""
import sys
import time
import requests

BASE = "http://localhost:7843"
FLOORS = list(range(40, 70, 3))
HOME = ("Farm", 64, 15)
AXE_NAME = "Iridium Axe"


def g(path, **kw):
    return requests.get(f"{BASE}{path}", params=kw or None, timeout=60).json()


def post(path, d=None):
    return requests.post(f"{BASE}{path}", json=d or {}, timeout=60).json()


def where():
    s = g("/state")
    l = (s.get("location") or {}).get("name")
    p = s.get("player") or {}
    return l, p.get("x"), p.get("y"), p.get("health")


def clump_at(loc, cx, cy):
    r = g("/passable_rect", x1=cx - 2, y1=cy - 2, x2=cx + 2, y2=cy + 2)
    for t in (r.get("tiles") or []):
        if (t["x"], t["y"]) == (cx, cy):
            return t.get("resource")
    return None


def find_stump(loc):
    """扫一层，返回第一件资源堆的左上角格 + 名字。"""
    r = g("/passable_rect", x1=0, y1=0, x2=90, y2=90)
    cl = [(t["x"], t["y"], t["resource"]) for t in (r.get("tiles") or []) if t.get("resource")]
    if not cl:
        return None
    cl.sort(key=lambda c: (c[1], c[0]))
    return cl[0]


def free_neighbor(sx, sy):
    """找树桩旁边一个能站的格（优先左边，再右边/上/下）。"""
    for dx, dy, face in ((-1, 0, 1), (1, 0, 3), (0, -1, 2), (0, 1, 0)):
        x, y = sx + dx, sy + dy
        r = g("/passable_rect", x1=x, y1=y, x2=x, y2=y)
        for t in (r.get("tiles") or []):
            if (t["x"], t["y"]) == (x, y) and t.get("passable"):
                return x, y, face
    return None


def main():
    print(f"🌍 出发前 {where()}")
    try:
        for fl in FLOORS:
            loc = f"UndergroundMine{fl}"
            post("/warp", {"location": loc, "x": 5, "y": 5})
            time.sleep(2.5)
            post("/heal", {})
            if where()[0] != loc:
                continue
            hit = find_stump(loc)
            if not hit:
                continue
            sx, sy, name = hit
            print(f"\n🪨 第 {fl} 层找到 {name} @ ({sx},{sy})")

            stand = free_neighbor(sx, sy)
            if not stand:
                print("   ⚠️ 旁边没有能站的格 —— 跳过本层")
                continue
            vx, vy, face = stand

            post("/select", {"name": AXE_NAME})
            post("/position", {"x": vx, "y": vy})
            time.sleep(0.6)
            post("/face", {"direction": face})
            time.sleep(0.3)
            s = g("/state")
            held = (s.get("player") or {}).get("currentItem")
            print(f"   站位 ({vx},{vy}) 朝 {face}  手持={held}")

            swings, gone_at = 0, None
            for i in range(1, 9):                       # 上限 8：够验"3 下"也能暴露"15 下"
                r = post("/use", {})
                swings = i
                time.sleep(0.9)
                still = clump_at(loc, sx, sy)
                ok = r.get("ok")
                print(f"   第 {i} 下: /use ok={ok} {str(r.get('error') or r.get('result') or '')[:60]}"
                      f"   这块还在={bool(still)}")
                if not still:
                    gone_at = i
                    break
            print(f"   ⇒ **{gone_at if gone_at else '>'+str(swings)} 下**才碎"
                  f"（`CLUMP_SPEC` 算的是 3；旧代码写死 15）")
            if gone_at:
                print(f"   {'✅ 对上了' if gone_at == 3 else '⚠️ 对不上，得查'}")
            break
        else:
            print("🕳️ 48 层扫完没找到资源堆 —— 换层再来（0.5%/格，不是每层必有）")
    finally:
        print("\n🚶 收尾：带出矿井")
        post("/warp", {"location": HOME[0], "x": HOME[1], "y": HOME[2]})
        time.sleep(2)
        post("/heal", {})
        post("/select", {"name": AXE_NAME})
        print(f"   {where()}")


if __name__ == "__main__":
    raise SystemExit(main())
