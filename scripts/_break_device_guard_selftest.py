"""⛏️ 划范围挥镐的「设备闸」+ 空地跳过如实报 —— 纯 Python 自验（不起服务、不碰游戏）。2026-10-05 补28d

恒的拍板（原话）：
  「目前的机制是只跳箱子和其他容器是吗？**那就给个警告吧，划范围的地上有设备就拒绝+报告一次区域内的设备坐标，
    第二次执行相同区域不再拦。**」

真机/代码事实（补28b 查证）：
  · `_break_worth`（本文件）**指名单格根本不走它** ⇒ 除箱子外什么都不跳（恒要的"指名格不跳"已成立）；
  · **划范围**那条对**任何 object 都 True** ⇒ 洒水器/机器/摆设会被一起敲（镐子敲它们 = 捡起来/敲掉）。

这里只验 **Python 这一半**（判据 / 闸门 / 一次性确认 / 回执怎么写）；真机三段见 CHANGELOG `203z补28d`。
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """只实现 `break_tile` 会用到的那几件；挥镐记账在 `swings`。"""

    def __init__(self):
        self.loc = "Farm"
        self.tiles = []
        self.player = {"x": 50, "y": 52}
        self.swings = []

    def select(self, *_a, **_k):
        return {"ok": True}

    def _get(self, path, params=None):
        p = params or {}
        if path == "/surroundings":
            return {"tiles": [dict(t) for t in self.tiles]}
        if path == "/dump_tile":
            t = next((t for t in self.tiles
                      if (t.get("x"), t.get("y")) == (p.get("x"), p.get("y"))), {})
            return {"tile": dict(t)}
        return {}

    def state(self):
        return {"player": dict(self.player), "location": {"name": self.loc}}

    def position(self, *_a):
        return {"ok": True}

    def face(self, *_a):
        return {"ok": True}

    def face_toward(self, *_a):
        return 0

    def use_tool(self, tool):
        self.swings.append(tool)
        return {"ok": True}


_api = FakeApi()
M.api = _api
M._with_state = lambda s: s
M._stamina_now = lambda: (270, 270)
M._stand_near = lambda tx, ty: (tx, ty + 1)


def _tiles(center=(50, 50), radius=1, extra=None):
    """造一片 /surroundings 夹具：默认全空，`extra` 里给的格覆盖上去。"""
    out = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            t = {"x": center[0] + dx, "y": center[1] + dy, "passable": True}
            if extra and (t["x"], t["y"]) in extra:
                t.update(extra[(t["x"], t["y"])])
            out.append(t)
    return out


def _reset(loc="Farm", player=(50, 52)):
    M._BREAK_DEVICE_CONFIRM.clear()
    _api.loc = loc
    _api.player = {"x": player[0], "y": player[1]}
    _api.swings = []


try:
    print("\n① 判据：什么算「设备」（**问游戏结构，不编名字表**）")
    ck("花盆 `(BC)62` ⇒ 设备", M._is_device_tile({"objId": "(BC)62"}) is True)
    ck("洒水器 `(BC)621` ⇒ 设备", M._is_device_tile({"objId": "(BC)621"}) is True)
    ck("石头 `(O)390` ⇒ **不是**设备（正是要敲的）", M._is_device_tile({"objId": "(O)390"}) is False)
    # ⚠️ 真机 2026-10-05 逮到第一版漏了洒水器：**1.6 的洒水器是 `(O)` 物件**（`(O)621` = 优质洒水器）⇒
    #    `(BC)` 那条抓不到它 ⇒ 必须有第 ② 条判据（有 object 且名字不是 "Stone"）。
    ck("洒水器 `(O)621`（有名字）⇒ 设备 —— **这才是恒最担心的那件**",
       M._is_device_tile({"object": "Quality Sprinkler", "objId": "(O)621"}) is True)
    ck("蟹笼 `(O)710` ⇒ 设备（是恒摆的设施）",
       M._is_device_tile({"object": "Crab Pot", "objId": "(O)710"}) is True)
    ck("矿节点名字 **就是** `Stone` ⇒ **不是**设备（1.6 全报 Stone，见 AGENTS 坑 6）",
       M._is_device_tile({"object": "Stone", "objId": "(O)32"}) is False)
    ck("只有 objId 没名字的 `(O)` ⇒ 不误判成设备（宁漏不误拦）",
       M._is_device_tile({"objId": "(O)999"}) is False)
    ck("箱子 `(BC)130` ⇒ **不算**设备（另有 chest_skip 那条路）",
       M._is_device_tile({"objId": "(BC)130", "object": "Chest"}) is False)
    ck("空/None ⇒ 不炸也不误判",
       M._is_device_tile({}) is False and M._is_device_tile({"objId": None}) is False)

    print("\n② 区域身份：同图+同中心+同半径 才是「同一个区域」")
    _reset()
    _k1 = M._break_area_key(50, 50, 2)
    ck("同参数 ⇒ 同 key", _k1 == M._break_area_key(50, 50, 2))
    ck("换半径 ⇒ 不同 key", _k1 != M._break_area_key(50, 50, 3))
    ck("挪中心 ⇒ 不同 key", _k1 != M._break_area_key(51, 50, 2))
    _reset(loc="FarmHouse")
    ck("换图 ⇒ 不同 key", _k1 != M._break_area_key(50, 50, 2))

    print("\n③ 划范围遇设备 ⇒ **拦下 + 报坐标**，且**一格都不挥**")
    _reset()
    _api.tiles = _tiles(extra={(51, 50): {"object": "Sprinkler", "objId": "(BC)621"},
                               (49, 51): {"object": "Stone", "objId": "(O)390"}})
    _out = M.break_tile(x=50, y=50, radius=1)
    ck("回执说「已拦下」", "已拦下" in _out, _out[:120])
    ck("回执报出洒水器坐标 (51,50)", "(51,50)" in _out, _out[:200])
    ck("回执带路（要再执行一次 / 或用单格）", "再执行一次" in _out and "单格" in _out, "")
    ck("**一格都没挥**（swings 为空）", _api.swings == [], str(_api.swings))

    print("\n④ 对**同一区域**再执行一次 ⇒ 放行（一次性确认）")
    _out2 = M.break_tile(x=50, y=50, radius=1)
    ck("这次真的挥了镐", _api.swings != [], str(_api.swings))
    ck("回执点明「确认过的同一区域」", "确认过的同一区域" in _out2, _out2[:160])
    ck("设备也进了目标（洒水器那格被挥到）", _api.swings.count("Pickaxe") >= 2, str(_api.swings))

    print("\n⑤ 第三次（同一区域）⇒ **又拦**（确认是一次性的，不会一直免检）")
    _api.swings = []
    _out3 = M.break_tile(x=50, y=50, radius=1)
    ck("又拦下", "已拦下" in _out3 and _api.swings == [], _out3[:120])

    print("\n⑥ 换一片区域 ⇒ 照旧拦")
    _api.swings = []
    _api.tiles = _tiles(center=(60, 60), extra={(61, 60): {"object": "Keg", "objId": "(BC)12"}})
    _out4 = M.break_tile(x=60, y=60, radius=1)
    ck("换区域也拦", "已拦下" in _out4 and _api.swings == [], _out4[:120])

    print("\n⑦ 确认过期（>10 分钟）⇒ 重新拦")
    _reset()
    _api.tiles = _tiles(extra={(51, 50): {"object": "Sprinkler", "objId": "(BC)621"}})
    M._BREAK_DEVICE_CONFIRM[M._break_area_key(50, 50, 1)] = time.time() - 3600
    _out5 = M.break_tile(x=50, y=50, radius=1)
    ck("过期后照旧拦（不许拿很久以前的确认顶）", "已拦下" in _out5 and _api.swings == [], _out5[:120])

    print("\n⑧ **单格模式不拦**（恒要的「指名格不跳」保持原样）")
    _reset()
    _api.tiles = _tiles(extra={(50, 50): {"object": "Sprinkler", "objId": "(BC)621"}})
    _out6 = M.break_tile(x=50, y=50, radius=0)
    ck("单格：挥了（没拦）", _api.swings == ["Pickaxe"], str(_api.swings))
    ck("单格回执里没有「已拦下」", "已拦下" not in _out6, _out6[:120])

    print("\n⑨ 空地跳过**如实报**（原来是静默的）")
    _reset()
    _api.tiles = _tiles(extra={(50, 50): {"object": "Stone", "objId": "(O)390"}})
    _out7 = M.break_tile(x=50, y=50, radius=1)
    ck("回执报「跳过 N 格空地」", "跳过" in _out7 and "空地" in _out7, _out7[:200])
    ck("石头仍然照敲", _api.swings == ["Pickaxe"], str(_api.swings))

    print("\n⑩ 箱子的老规矩没被这次改动碰坏")
    _reset()
    _api.tiles = _tiles(extra={(50, 50): {"object": "Chest", "objId": "(BC)130"}})
    _out8 = M.break_tile(x=50, y=50, radius=1)
    ck("箱子格跳过（不砸）且**不**被误判成设备", "箱子/容器格跳过" in _out8, _out8[:200])
    ck("箱子那格没挥", _api.swings == [], str(_api.swings))

    print(f"\n{0 if FAIL else 1} 组结论：{'全部通过' if not FAIL else '有失败'}"
          f"  （{len(FAIL)} 条未过）")
    if FAIL:
        print("  未过：" + " / ".join(FAIL))
    sys.exit(0 if not FAIL else 1)
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"❌ 自验炸了: {e}")
    sys.exit(1)
