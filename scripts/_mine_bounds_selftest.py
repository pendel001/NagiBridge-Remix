# -*- coding: utf-8 -*-
"""🧭 矿井「贴近反击」**不许把角色瞬移到地图外**（恒 2026-10-03 真机：「它太凶狠了，直接串到了墙外去杀怪」）。

真机现场（`requests.log` + `mine_run` 回执）：
    `⚔️ 怪物 Frost Bat 近身 (7,-1)，贴近反击` → 人落在 `(6,-1)`（**负坐标 = 墙外**）
    → 之后 `③ 走回入口梯：走不到入口梯 (13,5)（现在 (6,-1)）` → 只能靠 warp 兜底出矿。
病根三层（全部在脚本侧，与"怪=实心"那条无关 —— 这条瞬移路径**不查可通行性**）：
    ① 蝙蝠/幽灵**能飞出地图外**，`/surroundings` 如实报负坐标；
    ② `find_adjacent_tile` 只拿 `/surroundings` 报的格当"不可站"，而它**只报图内**
       ⇒ **图外格从来没进 blocked ⇒ 被当成能站**；
    ③ `mine_teleport` 调裸 `POST /position`，**一个边界都不查** ⇒ 真瞬移过去。
本自验钉死这三条（全打桩、不出网）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
os.environ.setdefault("NAGI_HOST_URL", "http://localhost:7842")

import mine_run  # noqa: E402

fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


class FakeBot(mine_run.MineBot):
    """假矿井：固定玩家位置 + 一张假 `/surroundings` + 记录所有发出的 `/position`。"""

    def __init__(self, px=6, py=0, wh=(40, 40), tiles=None):
        super().__init__(port=7843)
        self._px, self._py, self._wh, self._tiles = px, py, wh, (tiles or [])
        self.positions = []

    def state(self):
        return {"location": {"name": "UndergroundMine60",
                             "mapWidth": self._wh[0], "mapHeight": self._wh[1]},
                "player": {"x": self._px, "y": self._py,
                           "health": 180, "maxHealth": 180, "stamina": 474, "maxStamina": 474}}

    def surroundings(self, radius=6):
        return {"ok": True, "center": {"x": self._px, "y": self._py},
                "location": "UndergroundMine60", "tiles": list(self._tiles),
                "npcs": [], "monsters": [], "farmers": []}

    def _post(self, ep, data=None, **kw):
        if ep == "/position":
            self.positions.append(((data or {}).get("x"), (data or {}).get("y")))
            return {"ok": True}
        return {"ok": True}

    # 挥刀/睡觉这些一律不碰游戏
    def swing(self, *a, **kw):
        return True


print("① find_adjacent_tile：怪在图外（负坐标）⇒ **绝不返回图外格**")
b = FakeBot(px=6, py=0)
nx, ny, dx, dy = b.find_adjacent_tile(7, -1)          # 蝙蝠在图外
ck("…怪 (7,-1) 的「旁边格」不会给出图外格（(6,-1)/(8,-1)/(7,-2) 全在图外）",
   ny >= 0 and nx >= 0, f"got {(nx, ny)}")
ck("…给出的落点确实在图内（负坐标一律排除）", nx >= 0 and ny >= 0, f"got {(nx, ny)}")
nx2, ny2, _, _ = b.find_adjacent_tile(-3, 5)          # 整只怪在图外左边
ck("…怪整个在图外 (-3,5) ⇒ 返回**原地**（不回一个图外格）", (nx2, ny2) == (6, 0), f"got {(nx2, ny2)}")

print("② mine_teleport：图外坐标 ⇒ 拒绝，且**一个 `/position` 都不发**")
b.positions.clear()
ck("…负坐标 (-1,5) 被拒", b.mine_teleport(-1, 5) is False and b.positions == [], str(b.positions))
ck("…(6,-1) 被拒（真机落点）", b.mine_teleport(6, -1) is False and b.positions == [], str(b.positions))
ck("…超出右/下边界 (40,5)/(5,40) 被拒（本图 40x40）",
   b.mine_teleport(40, 5) is False and b.mine_teleport(5, 40) is False and b.positions == [],
   str(b.positions))
ck("…图内 (6,1) 照常放行（没把正常瞬移一起堵死）",
   b.mine_teleport(6, 1) is True and b.positions == [(6, 1)], str(b.positions))

print("③ combat_step：怪在图外 ⇒ 不追出去（原地挥一下，绝不瞬移）")
b2 = FakeBot(px=6, py=0)
b2.nearby_monsters = lambda radius=4: [("Frost Bat", 7, -1, 10, 2)]   # dist=2 ⇒ 会走"贴近反击"那条
b2.positions.clear()
st = b2.combat_step("UndergroundMine60")
ck("…仍然返回 fighting（这一下照挥，只是不追出去）", st == "fighting", st)
ck("…**没有**发出任何 `/position`（以前会瞬移到 (6,-1)）", b2.positions == [], str(b2.positions))

print("④ 对照组：怪在图内 ⇒ 照旧「贴近反击」（把正常路径保住）")
b3 = FakeBot(px=6, py=0)
b3.nearby_monsters = lambda radius=4: [("Green Slime", 8, 0, 10, 2)]
b3.positions.clear()
st3 = b3.combat_step("UndergroundMine60")
ck("…怪 (8,0) 在图内 ⇒ 照常贴近（有 `/position`，落点 (7,0)）",
   st3 == "fighting" and b3.positions == [(7, 0)], str(b3.positions))

print("⑤ 敲矿：**远处照旧走 walk_to，但走完一定核对落点、不准则 position 兜底**"
      "（恒 2026-10-03：「我的本意是 walk_to 之后不校验有没有准确站在目标，100% position 兜底一下，"
      "而不是弃用 walk_to 全部走 position」）")
calls = []


class MineBot2(FakeBot):
    """走完**没站到**目标格 ⇒ 必须补一发 position。"""

    def __init__(self, *a, land_offset=(-1, 0), **kw):
        super().__init__(*a, **kw)
        self._land_offset = land_offset
        self._guard = False

    def _post(self, ep, data=None, **kw):
        calls.append(ep)
        if ep == "/walk_to":
            self._px, self._py = 19 + self._land_offset[0], 20 + self._land_offset[1]   # 走完落在哪
        return super()._post(ep, data, **kw)

    def walk_to_coord(self, *a, **kw):
        calls.append("/walk_to")
        self._px, self._py = 19 + self._land_offset[0], 20 + self._land_offset[1]
        return {"ok": True}

    def wait_arrival(self, *a, **kw):
        calls.append("/wait_arrival")
        return True

    def surroundings(self, radius=6):
        return {"ok": True, "center": {"x": self._px, "y": self._py},
                "location": "UndergroundMine60", "tiles": [], "npcs": [], "monsters": [], "farmers": []}


def _rock_calls(land_offset):
    calls.clear()
    b = MineBot2(px=6, py=0, land_offset=land_offset)
    b.find_adjacent_tile = lambda tx, ty, radius=3: (tx - 1, ty, 1, 0)      # 固定"左边那格" = (19,20)
    b.mine_teleport = lambda x, y: (calls.append(("/position", x, y)), True)[1]
    b.COUNT_BLOWS = 0
    b.mine_rock(20, 20, "Stone", "UndergroundMine60")                        # 距 20 格（远）
    return list(calls)


_c_aligned = _rock_calls((0, 0))        # 走完正好站在 (19,20)
_c_off = _rock_calls((1, 1))            # 走完偏了
ck("…远石头（距 20 格）**照旧走 `/walk_to`**（没有弃用走路）",
   "/walk_to" in _c_off, str(_c_off))
ck("…走完**正好在目标格** ⇒ **不多补** position（不重复动作）",
   ("/position", 19, 20) not in _c_aligned, str(_c_aligned))
ck("…走完**没站到**目标格 ⇒ **一定补一发 position 到 (19,20)**（恒要的 100% 兜底）",
   ("/position", 19, 20) in _c_off, str(_c_off))


print("⑥ 两条线的相对大小：**吃必须高于撤**（否则那条吃食路径是死的）")
import bomb_common  # noqa: E402
ck("…HP：吃 60% > 撤 RETREAT_HP_ABS=35 绝对值（约 19%）",
   bomb_common.EAT_HP_PCT == 60 and bomb_common.EAT_HP_PCT > 35, bomb_common.EAT_HP_PCT)
ck("…体力：吃 30% > 撤 15%（原来吃 10% < 撤 15% ⇒ 永远轮不到吃，2026-10-03 恒一问才照出来）",
   bomb_common.EAT_STA_PCT == 30 and bomb_common.EAT_STA_PCT > 15, bomb_common.EAT_STA_PCT)

print("⑦ 吃的摊开：每样**同时给血/体两列**（恒：「没有只补其一的食物，奶酪(hp+56,体力+125)可以合并写」）")
b7 = FakeBot(px=6, py=0)
# 形状 = `(名字, 回体力, 回血, buff档)`（2026-10-03 统一；第 4 位 = C# 报的 foodBuffs）
b7.detect_inventory_food = lambda: [("奶酪", 125, 56, []), ("韭葱", 40, 20, [])]
_line = b7.food_menu_line()
ck("…合并成一行：`奶酪 血+56 体+125`", "奶酪 血+56 体+125" in _line, _line)
ck("…给了**可照抄的名字** + 点名写法（免去翻背包/大小写）",
   "照抄" in _line and 'food_hp' in _line, _line)
ck("…没带效果的 ⇒ 不出现「带效果的」那一段（不编、不吓人）", "带效果的" not in _line, _line)
b7.detect_inventory_food = lambda: [("Spicy Eel", 115, 51,
                                     {"isDrink": False, "buffs": [
                                         {"id": "food", "source": "香辣鳗鱼", "ms": 420000,
                                          "effects": ["+1 运气", "+1 速度"], "rawEffects": None}]})]
_lb = b7.food_menu_line()
ck("…带效果的**单独标出来**（自动挑不动它 ⇒ 得让 AI 看得见、点得了名）",
   "带效果的" in _lb and "+1 运气" in _lb and "food_buff" in _lb, _lb)
b7.detect_inventory_food = lambda: []
ck("…包里没吃的 ⇒ 明确说没有（别让 AI 以为自动挑会凭空变出食物）", "没有" in b7.food_menu_line(), b7.food_menu_line())
print()
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("🎉 全部通过（矿井「贴近反击」不再把角色传出地图）")
