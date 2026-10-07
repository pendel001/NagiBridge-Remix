"""🚫🧭 两条"不拟人"的钉子（纯离线：桩掉 HTTP，不碰游戏）。

① **收放机器会先瞬移到"地点入口"** —— 恒真机排除法（2026-10-07）：
   他先怀疑"农场太乱 ⇒ BFS 走不到 ⇒ 兜底瞬移"，实测**否**（从 (46,43) 走到避雷针/蜂房都 ok=True）；
   真因是 `machine_loader` 对**露天机器**两处**无条件** `warp_into(loc)`，而注释写着"已在同屋则跳过"。
   ⇒ 这里钉：**已经在这张图就不许 warp**（桩 api：同图 ⇒ 一次 warp_into 都不发）。

② **捡东西不就近**（恒："海滩捡贝壳左一下右一下，这片没捡完又过桥去捡那片，如钟摆"）：
   `uniq` 是**扫描那一刻**按"离扫描中心"排的、之后不重排 ⇒ 人一走开就跨簇来回摆。
   ⇒ 这里钉：**每捡一件都按"现在站哪"重挑最近的**（用桩 HTTP 真跑一遍，验首件是不是最近那件）。
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


print("\n① machine_loader：已经在这张图就**一步都不瞬移**")
import machine_loader as ML  # noqa: E402

real_api = ML.api
warped = []
try:
    class _FakeApi:
        loc = "Farm"

        def current_location(self):
            return self.loc

        def warp_into(self, loc, *a, **k):
            warped.append(loc)
            return {"ok": True}

    ML.api = _FakeApi()
    ck("同图 ⇒ _already_here 认 True", ML._already_here("Farm") is True)
    ML.api.loc = "Beach"
    ck("异图 ⇒ False（该 warp 还是会 warp）", ML._already_here("Farm") is False)
    ML.api = types.SimpleNamespace(current_location=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    ck("读位置炸了 ⇒ False（**不猜**，退回老行为）", ML._already_here("Farm") is False)
finally:
    ML.api = real_api

src = open(os.path.join(HERE, "machine_loader.py"), encoding="utf-8").read()
ck("两处调用点都包了 `if not _already_here(loc):`", src.count("if not _already_here(loc):") == 2,
   f"实际 {src.count('if not _already_here(loc):')} 处")

print("\n② pickup_scene：每捡一件按**当前站位**重挑最近的（钟摆那条）")
psrc = open(os.path.join(HERE, "pickup_scene.py"), encoding="utf-8").read()
ck("动态重排（按当前 px,py 排序）在循环里", "_pending.sort(key=lambda t: abs(t[0] - _px) + abs(t[1] - _py))" in psrc)
ck("旧的「扫完就定序、一路捡到底」已删", "for x, y, obj in uniq[:max_n]:" not in psrc)

# ── 真跑一遍（桩掉 requests）：玩家站 (5,0)，扫描中心 (0,0) ⇒ 旧代码先奔 (0,0)，新代码必须先捡 (5,0) 隔壁那件 ──
import pickup_scene as PS  # noqa: E402

real_requests = PS.requests
state = {"p": [5, 0], "items": {(0, 0): "Shell", (7, 0): "Coral", (5, 0): "Clam"}, "walks": []}


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _FakeRequests:
    """够用的桩：/status /surroundings /state /walk_to /face /interact /debris。"""

    def get(self, url, params=None, timeout=None):
        if url.endswith("/status"):
            return _Resp({"worldReady": True, "build": "2026-10-07-00-00"})
        if url.endswith("/surroundings"):
            tiles = []
            for (x, y), nm in state["items"].items():
                tiles.append({"x": x, "y": y, "object": nm, "objId": "(O)372",
                              "passable": True, "forage": True})
            return _Resp({"location": "Beach", "center": {"x": 0, "y": 0}, "tiles": tiles})
        if url.endswith("/state"):
            return _Resp({"location": {"name": "Beach"},
                          "player": {"x": state["p"][0], "y": state["p"][1], "maxItems": 36},
                          "inventory": []})
        if url.endswith("/debris"):
            return _Resp({"debris": []})
        return _Resp({})

    def post(self, url, json=None, timeout=None):
        if url.endswith("/walk_to"):
            j = json or {}
            state["walks"].append((j.get("x"), j.get("y")))
            # 桩：走过去就到位（物体格站不住 ⇒ 落到旁边；这里直接落目标格够用了）
            state["p"] = [j.get("x", 0), j.get("y", 0)]
            return _Resp({"ok": True})
        if url.endswith("/interact"):
            # 桩：把脚边（或走位目标）那件捡掉
            for k in list(state["items"]):
                if abs(k[0] - state["p"][0]) <= 1 and abs(k[1] - state["p"][1]) <= 1:
                    del state["items"][k]
            return _Resp({"ok": True})
        return _Resp({"ok": True})


PS.requests = _FakeRequests()
try:
    PS.main(radius=30, max_n=3, dry_run=False)
finally:
    PS.requests = real_requests

first_walk = state["walks"][0] if state["walks"] else None
ck(f"第一次走位奔的是**离自己最近**那件 (5,0)，而不是扫描中心那件 (0,0)", first_walk == (5, 0), f"实际 {first_walk}")
print(f"   （走位序列：{state['walks']}）")

print("\n③ 跨图**不许**摆 POI 站位（恒 2026-10-07 真机：「**怎么飞到了不可走格**」）")
import navigation as NAV  # noqa: E402

nsrc = open(os.path.join(HERE, "navigation.py"), encoding="utf-8").read()
ck("_apply_poi_stand_face 里核了地图", "_cur_map != _poi_map" in nsrc)
ck("调用处只在**到了目标图**才摆站位", 'if _now_map == poi_map else ""' in nsrc)

_stand_want = None
try:
    import locations as _LOC
    _cfg = (_LOC.POI_FACE or {}).get("铁匠铺(柜台)") or {}
    _stand_want = tuple(_cfg.get("stand")) if _cfg.get("stand") else None
except Exception:
    pass
ck("铁匠铺(柜台) 的站位格 = (3,15)（这就是被瞬移过去的那格）", _stand_want == (3, 15), str(_stand_want))

_real_api = NAV.api
_real_mark = getattr(NAV, "_mark_festival_poi_name", None)
_calls = []


class _FakeNavApi:
    loc = "Town"

    def current_location(self):
        return self.loc

    def player_tile(self):
        return (0, 0)

    def position(self, x, y):
        _calls.append(("position", x, y))

    def face(self, d):
        _calls.append(("face", d))


NAV.api = _FakeNavApi()
NAV._mark_festival_poi_name = lambda n: None
try:
    note = NAV._apply_poi_stand_face("铁匠铺(柜台)")
    ck("人在 Town、POI 在 Blacksmith ⇒ **一次 position/face 都不发**", _calls == [], str(_calls))
    ck("并如实说「没动站位」", "没动站位" in note, note)
    _FakeNavApi.loc = "Blacksmith"
    _calls.clear()
    NAV._apply_poi_stand_face("铁匠铺(柜台)")
    ck("真到了 Blacksmith ⇒ 照旧摆站位 (3,15)", ("position", 3, 15) in _calls, str(_calls))
finally:
    NAV.api = _real_api
    if _real_mark is not None:
        NAV._mark_festival_poi_name = _real_mark

print()
if FAIL:
    print(f"❌ {len(FAIL)} 项不过：" + " / ".join(FAIL))
    sys.exit(1)
print("✅ 不瞬移 / 就近捡：全部通过")
