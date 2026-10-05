"""🪴 花盆播种 + 「点自己脚下那格」的实情提示 —— 纯 Python 自验（不起服务、不碰游戏）。2026-10-05 补27

恒旧档真机逮到两处：

① **`farm ops=plant` 种不进花盆**（功能缺口）
   花盆那格 `/surroundings` 报的是 `{"object":"Garden Pot","objId":"(BC)62","terrain":"HoeDirt"}`
   —— **terrain 是 HoeDirt**！于是它骗过两道判据（`_tile_obstacle` 说"不是障碍"、`_farm_plant` 说"已锄好"），
   最后走**左键 `use_item()`** ⇒ 游戏回 `Cannot place '…' here`。
   真相：花盆是 `Object(BC)62` + 内部 `hoeDirt`，**不是 HoeDirt terrain feature**
   ⇒ 播种只认游戏自己的路：`select 种子 → interact 那一格`（真机验过：种得上，随后出 `crop/cropName`）。

② **`scene at x y` 传了 POI 的「站位格」** ⇒ 游戏如实回 `actionTriggered:false`，读起来却像"柜台没了"。
   现场：玛妮柜台 POI `pos=(12,16) face=0` ⇒ 目标是 `(12,15)`；站 (12,16) 朝上无坐标 interact
   **四个选项全出来**（真机 ✅），而 `scene at 12 16`（点自己脚下）⇒ false。
   ⇒ 不拦（自己的格子也可能是合法目标，导航推门也走 `interact_at`），只在"没触发 + 目标就是脚下"时说清。

这里只验 **Python 这一半**（判据/分派/报告怎么写）；真机那一半见 CHANGELOG `203z补27`。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []
ROWS = {}          # 假地图：{(x,y): tile}
CALLS = []         # 记：这次播种走了哪条路（"interact_at" / "use_item"）


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _fake_scan(pts, margin=1):
    """第一次扫（开工前）= ROWS；第二次扫（验收）= 一律给 crop（=种上了）。"""
    p = list(pts)
    if not p:
        return {}, ""
    x1, y1 = min(t[0] for t in p) - margin, min(t[1] for t in p) - margin
    x2, y2 = max(t[0] for t in p) + margin, max(t[1] for t in p) + margin
    out = {}
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            out[(x, y)] = {"terrain": None}
    for k, v in ROWS.items():
        if k in out:
            out[k] = dict(v)
    if _fake_scan.n:
        for k in list(out):
            out[k] = dict(out[k])
            out[k]["crop"] = "262"          # 验收：判据只认 crop
    _fake_scan.n += 1
    return out, ""


_fake_scan.n = 0


class FakeApi:
    """只实现 `_farm_plant` 会用到的那几件；`interact_at`/`use_item` 记账。"""

    def state(self):
        return {"player": {"x": 50, "y": 50, "stamina": 200, "maxStamina": 270},
                "location": {"name": "Farm"},
                "inventory": [{"name": "Parsnip Seeds", "stack": 5}]}

    def walk_ok_tiles(self, *_a):
        return {(x, y) for x in range(40, 60) for y in range(40, 60)}

    def stand_tile(self, tx, ty, _ok):
        return (tx, ty - 1, 2)

    def walk_natural(self, *_a):
        return True

    def position(self, *_a):
        return {"ok": True}

    def select(self, *_a):
        return {"ok": True}

    def face(self, *_a):
        return {"ok": True}

    def use_item(self, *_a, **_k):
        CALLS.append("use_item")
        return {"ok": True}

    def interact_at(self, x, y):
        CALLS.append("interact_at")
        return {"ok": True, "actionTriggered": True}


_real = {n: getattr(M, n) for n in ("api", "_scan_tiles", "_with_state", "_warp_home_if_needed",
                                    "_stamina_now", "_ensure_background", "_cross_map_guard",
                                    "_cc_note_guard", "_face_toward", "_sit_cache_clear",
                                    "_sittable_cached")}


def _reset():
    _fake_scan.n = 0
    ROWS.clear()
    CALLS.clear()


try:
    M.api = FakeApi()
    M._scan_tiles = _fake_scan
    M._with_state = lambda s: s
    M._warp_home_if_needed = lambda *a, **k: ""
    M._stamina_now = lambda: (200, 270)

    print("\n① 判据：什么算花盆（`(BC)62` / 名字 `Garden Pot`）")
    ck("objId `(BC)62` ⇒ 花盆", M._is_garden_pot({"objId": "(BC)62"}) is True)
    ck("名字 `Garden Pot` ⇒ 花盆", M._is_garden_pot({"object": "Garden Pot"}) is True)
    ck("普通已锄地（terrain=HoeDirt）⇒ **不是**花盆", M._is_garden_pot({"terrain": "HoeDirt"}) is False)
    ck("别的设施（熔炉）⇒ 不是花盆", M._is_garden_pot({"object": "Furnace"}) is False)
    ck("空/None ⇒ 不炸也不误判", M._is_garden_pot({}) is False and M._is_garden_pot(None) is False)
    ck("花盆**不是障碍**（`_tile_obstacle` 回 (None, False)）",
       M._tile_obstacle({"object": "Garden Pot", "objId": "(BC)62", "terrain": "HoeDirt"}) == (None, False))

    print("\n② 往花盆里种：必须走 `interact_at`（游戏自己的路），**不许** `use_item`")
    _reset()
    ROWS.update({(40, 40): {"object": "Garden Pot", "objId": "(BC)62", "terrain": "HoeDirt"}})
    out = M._farm_plant(seed_name="Parsnip Seeds", x=40, y=40)
    ck("走了 interact_at", "interact_at" in CALLS, str(CALLS))
    ck("**没走** use_item", "use_item" not in CALLS, str(CALLS))
    ck("验收说种上了 1/1（判据=这格真长出 crop）", "1/1" in out, out)
    ck("没被当成「设施」跳过", "🏗️" not in out, out)

    print("\n③ 普通大田格：照旧走 `use_item`（别把大田那条正经路改坏）")
    _reset()
    ROWS.update({(40, 40): {"terrain": "HoeDirt"}})
    out = M._farm_plant(seed_name="Parsnip Seeds", x=40, y=40)
    ck("走了 use_item", "use_item" in CALLS, str(CALLS))
    ck("**没走** interact_at", "interact_at" not in CALLS, str(CALLS))
    ck("照样报 1/1", "1/1" in out, out)

    print("\n④ 锄地永远不碰花盆（花盆 terrain 也报 HoeDirt ⇒ 天然算「早就翻好了」）")
    _reset()
    ROWS.update({(40, 40): {"object": "Garden Pot", "objId": "(BC)62", "terrain": "HoeDirt"}})
    out = M._farm_till(x1=40, y1=40, x2=40, y2=40)
    ck("一格都不用锄", "一格都不用锄" in out, out)
    ck("…点明是**早就翻好了**（不是「没判据」）", "早就翻好了" in out, out)

    print("\n⑤ 「点自己脚下那格」的实情提示（`scene at` 传错格时要看得出来）")
    M._ensure_background = lambda *a, **k: None
    M._cross_map_guard = lambda x, y: None
    M._cc_note_guard = lambda x, y: (None, "")
    M._face_toward = lambda x, y: None
    M._sit_cache_clear = lambda: None
    M._sittable_cached = lambda r: {"me": {}}

    class TileApi(FakeApi):
        def __init__(self, px, py, trig):
            self._px, self._py, self._trig = px, py, trig

        def state(self):
            return {"player": {"x": self._px, "y": self._py}, "location": {"name": "AnimalShop"}}

        def interact_at(self, x, y):
            return {"ok": True, "actionTriggered": self._trig}

    M.api = TileApi(12, 16, False)
    out = M._interact_at_core(12, 16)
    ck("点自己脚下 ⇒ 报没有可交互的东西", "没有可交互的东西" in out, out)
    ck("…并点明**那是你自己站的格**", "你自己站着的那格" in out, out)
    ck("…并说清 `scene at` 的坐标是**目标格**", "要点的目标格" in out, out)
    ck("…并给了下一步（`map walk` + `scene interact` 或传目标格）",
       "map walk" in out and "scene interact" in out, out)

    M.api = TileApi(12, 16, False)
    out2 = M._interact_at_core(12, 15)
    ck("目标不是脚下（正常的 (12,15)）⇒ **不出现**那句提示", "你自己站着的那格" not in out2, out2)

    M.api = TileApi(12, 16, True)
    out3 = M._interact_at_core(12, 16)
    ck("脚下那格**真触发了** ⇒ 也不加提示（不误报）", "你自己站着的那格" not in out3, out3)
finally:
    for k, v in _real.items():
        setattr(M, k, v)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
