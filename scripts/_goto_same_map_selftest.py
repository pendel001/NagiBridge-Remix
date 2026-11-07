# -*- coding: utf-8 -*-
"""🗺️ 同图导航**不许瞬移** —— 已经在这张图就老老实实走。2026-11-08

恒：「刚才转头去鱼店的时候**出发点好像瞬移到了海滩的其他地方**再过去的，也麻烦帮看看」

现场账（`scripts/sessions/session_log.jsonl`，`map walk poi_name=鱼店(门口)` 那一条，9445ms）：

    [pos] Beach (52, 25)                       ← 人站在海滩码头（海滩钓鱼点(码头)）
    [target] 鱼店(门口) -> Beach (30, 34)        ← 目标**同一张图**
      [warp] Beach... done at (38,10)          ← 🔴 同图也先 /warp 到"入口"再走
      [walk] to (30,34)

根因在 `go_to.py` 的同图分支：无条件 `warp_to(current_map, entry)`。而 `entry`（这里 (38,10)）是
**从别的图进来时游戏把人放在哪**，是跨图落客的副作用，不是"这张图的起点" ⇒ 同图先传入口属于凭空瞬移。
📌 这也不是新原则：`machine_loader` 早有「同图不瞬移」的钉子（`_no_teleport_and_pickup_order_selftest.py`），
   `go_to.py` 这条老路一直漏着。

判据：
  ① 同图 ⇒ **一个 `/warp` 都不发**，直接 `/walk_to` 目标格；
  ② 跨图**照旧**先 warp（别把闸设成「永远不 warp」，那会让所有远门都走断了）；
  ③ 同图真走不到（挡死/站在出不去的地方）⇒ 才退回"回入口再走"，**只此一次**且**明说是兜底**；
     兜底也到不了 ⇒ 报 `[FAIL]`（`navigation` 认这个标记，不会再把"没走到"说成「已导航到」）；
  ④ 这张图没登记入口 ⇒ 不许乱 warp，明说停在这儿。
"""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAIL = []
HERE = os.path.dirname(os.path.abspath(__file__))


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


import go_to as G                          # noqa: E402
from locations import POI, ROUTES           # noqa: E402

SRC = open(os.path.join(HERE, "go_to.py"), encoding="utf-8").read()

# 🎣 POI 真值（不是硬编码坐标：真机那一趟的"起点/终点都在 Beach"这件事本身要被钉住）
_SHOP = "鱼店(门口)"
SHOP_MAP, SHOP_POS = POI[_SHOP]["map"], POI[_SHOP]["pos"]
DOCK = "海滩钓鱼点(码头)"
DOCK_MAP, DOCK_POS = POI[DOCK]["map"], POI[DOCK]["pos"]


class World:
    """假的游戏端：`api()` 收 POST、`/state` 反映**当前**位置（warp/walk 会真的改它）。

    `walk_ok` 是每次 `/walk_to` 的成败流水（最后一项一直重复）——用来造"走不到"的世界。
    """

    def __init__(self, map_name, x, y, walk_ok=(True,)):
        self.map, self.x, self.y = map_name, x, y
        self.walk_ok = tuple(walk_ok)
        self.posts = []          # 只记 warp / walk_to（按发生顺序）
        self._walk_n = 0

    def st(self):
        return {"location": {"name": self.map}, "player": {"x": self.x, "y": self.y},
                "worldReady": True}

    def api(self, method, path, data=None, retries=3):
        data = dict(data or {})
        if path == "/state":
            return self.st()
        if path == "/warp":
            self.posts.append(("/warp", data))
            self.map = data.get("location", self.map)
            if "x" in data:
                self.x, self.y = data["x"], data.get("y", 10)
            return {"ok": True}
        if path == "/walk_to":
            self.posts.append(("/walk_to", data))
            ok = self.walk_ok[min(self._walk_n, len(self.walk_ok) - 1)]
            self._walk_n += 1
            if ok:
                self.map = data.get("location", self.map)
                self.x, self.y = data.get("x", self.x), data.get("y", self.y)
            return {"ok": True}
        return {"ok": True}

    def install(self):
        G.api = self.api
        return self


_ORIG = (G.api, G.ROUTES, G.wait_arrival)
# ⏩ 把"等到达"的轮询压到 0.05s —— 假世界是**瞬时**到位的，真等 15/20s 只是让钉子跑成钟
G.wait_arrival = (lambda f: (lambda *a, **k: f(*a, **{**k, "timeout": 0.05})))(G.wait_arrival)


def run(poi, world):
    """>跑一趟 `go()`，返回 (它的 stdout, world)。"""
    world.install()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        G.go(poi)
    return buf.getvalue(), world


def posts(world, kinds=("/warp", "/walk_to")):
    return [(p, d) for (p, d) in world.posts if p in kinds]


try:
    print("\n① 同图：**一个 warp 都不发**（真机那一趟：Beach (52,25) → 鱼店(门口) Beach (30,34)）")
    ck("①前提：码头和鱼店门口确实在**同一张图**",
       DOCK_MAP == SHOP_MAP and DOCK_MAP == "Beach", f"{DOCK_MAP} vs {SHOP_MAP}")
    out, w = run(_SHOP, World(DOCK_MAP, *DOCK_POS))
    ck("①同图零 warp", [p for p, _d in w.posts if p == "/warp"] == [],
       str([p for p, _d in w.posts if p == "/warp"]))
    ck("①直接走到目标格", any(p == "/walk_to" and (d.get("x"), d.get("y")) == tuple(SHOP_POS)
                             for p, d in w.posts), str(posts(w)))
    ck("①人确实在目的地附近（不是「报到达、没动」）",
       abs(w.x - SHOP_POS[0]) <= 5 and abs(w.y - SHOP_POS[1]) <= 5, f"({w.x},{w.y})")
    ck("①输出里没有那行 `[warp]`（恒就是看见这个才觉得瞬移）", "[warp]" not in out, out[:200])

    print("\n② 跨图**照旧先 warp**（别把闸设成「永远不 warp」）")
    out2, w2 = run(_SHOP, World("Farm", 64, 15))
    _wp = [d for p, d in w2.posts if p == "/warp"]
    ck("②跨图有 warp，且最后一段传到 Beach", _wp and _wp[-1].get("location") == "Beach", str(_wp))
    _seq = [p for p, _d in w2.posts]
    ck("②顺序：先 warp 后 walk", _seq and _seq[0] == "/warp" and _seq[-1] == "/walk_to", str(_seq))
    ck("②落在目标格", (w2.x, w2.y) == tuple(SHOP_POS), f"({w2.x},{w2.y})")

    print("\n③ 同图走不到 ⇒ **才**退回「回入口再走」，且明说是兜底、只传一次")
    out3, w3 = run(_SHOP, World(DOCK_MAP, *DOCK_POS, walk_ok=(False, False)))
    _w3 = [d for p, d in w3.posts if p == "/warp"]
    ck("③先走、走不到才 warp（warp 在第一次 walk **之后**）",
       [p for p, _d in w3.posts] == ["/walk_to", "/warp", "/walk_to"], str([p for p, _d in w3.posts]))
    ck("③只传一次（不反复瞬移）", len(_w3) == 1, str(_w3))
    ck("③传的是**本图登记的入口**",
       (w3.posts[1][1].get("x"), w3.posts[1][1].get("y")) == tuple(
           [e for (s, di, dst, e) in ROUTES if dst == DOCK_MAP][0]), str(w3.posts[1][1]))
    ck("③输出里明说这是兜底（`[fallback]`）", "[fallback]" in out3, out3[:300])
    ck("③兜底也到不了 ⇒ 报 `[FAIL]`（别再被 navigation 说成「已导航到」）", "[FAIL]" in out3, out3[:300])

    print("\n④ 兜底成功那种：走不到 → 回入口 → 走得到 ⇒ 不报 FAIL、人真的到了")
    out4, w4 = run(_SHOP, World(DOCK_MAP, *DOCK_POS, walk_ok=(False, True)))
    ck("④有 `[fallback]` 但不报 `[FAIL]`", "[fallback]" in out4 and "[FAIL]" not in out4, out4[:300])
    ck("④人到了鱼店门口附近", abs(w4.x - SHOP_POS[0]) <= 5 and abs(w4.y - SHOP_POS[1]) <= 5,
       f"({w4.x},{w4.y})")

    print("\n⑤ 这张图**没登记入口** ⇒ 不许乱 warp，明说停在这儿")
    G.ROUTES = []
    out5, w5 = run(_SHOP, World(DOCK_MAP, *DOCK_POS, walk_ok=(False,)))
    ck("⑤零 warp", [p for p, _d in w5.posts if p == "/warp"] == [], str(posts(w5)))
    ck("⑤明说没入口、不再乱动", "没登记入口" in out5 or "[FAIL]" in out5, out5[:300])
    G.ROUTES = _ORIG[1]

    print("\n⑥ 源码钉：同图分支**先走**，warp 只在兜底里；老注释那句「先传入口」必须消失")
    ck("⑥老的 `Already on target map: warp to entry first` 已删",
       "warp to entry first" not in SRC, "老注释还在")
    _same = SRC.split("if len(route) == 1 and route[0][0] == current_map:")[1].split("# walk each route segment")[0]
    ck("⑥同图分支里 `walk_to(` 出现在 `warp_to(current_map` **之前**",
       0 < _same.find("walk_to(dest_map") < _same.find("warp_to(current_map"),
       f"walk@{_same.find('walk_to(dest_map')} warp@{_same.find('warp_to(current_map')}")
    ck("⑥兜底那句带 `[fallback]`（人能看见发生了兜底）", "[fallback]" in _same, "兜底没说话")
    ck("⑥判据 `near()` 只认「同图 + ≤NEAR_TOL 格」",
       "def near(" in SRC and "NEAR_TOL" in SRC and "!= location" in SRC)
    ck("⑥容差 ≤5 格（POI 常是不可走格，人会停旁边；再大就等于「停半路也算到」）",
       0 < G.NEAR_TOL <= 5, str(G.NEAR_TOL))

    print("\n⑦ 入口坐标 y=0 不许被 `or 10` 吃掉（Beach 的登记入口就是 (38,0)）")
    _be = [e for (_s, _d, dst, e) in ROUTES if dst == "Beach"]
    ck("⑦前提：Beach 登记入口的 y 确实是 0", bool(_be) and _be[0][1] == 0, str(_be))
    # 走的是**同图兜底**那条：跨图那条按 `plan_route` 给的 pos 恒为 None ⇒ 传的是整张图，
    # 落点由游戏自己决定；坐标只在同图兜底这里真正落地，所以 y 的判据要在这一条上验。
    _o7, w7 = run(_SHOP, World(DOCK_MAP, *DOCK_POS, walk_ok=(False, False)))
    _wh = [d for p, d in w7.posts if p == "/warp"]
    ck("⑦同图兜底传入口时 y 就是 0（不是被悄悄改成 10）",
       bool(_wh) and _wh[-1].get("location") == DOCK_MAP and _wh[-1].get("y") == 0, str(_wh))
    ck('⑦源码里不再有 `data["y"] = entry_y or 10`（`0` 是假值，判「有没有给」不能判真假）',
       'data["y"] = entry_y or 10' not in SRC, "老的 or 10 还在")

    print("\n⑧ 读不到状态 ⇒ **不许**借机瞬移（「不知道」≠「没走到」）")
    _bakapi = G.api
    G.api = lambda *a, **k: {"error": "boom"}
    _r8 = G.near(DOCK_MAP, *SHOP_POS)
    ck("⑧near() 读不到时返回 None（不是 False）", _r8 is None, repr(_r8))
    G.api = _bakapi
    ck("⑧兜底只在 `is not False` 时触发（只有确证走不到才 warp）",
       "near(dest_map, dest_pos[0], dest_pos[1]) is not False" in SRC, "判据被改成真值判断了")
finally:
    G.api, G.ROUTES, G.wait_arrival = _ORIG

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 同图直接走（不再先传入口瞬移一段）；跨图照旧 warp；真走不到才兜底回入口，且说清/报 FAIL。")
