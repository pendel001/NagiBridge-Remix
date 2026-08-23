"""bomb_common 纯逻辑自测（不打游戏，monkeypatch 假数据）"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
os.environ.setdefault("NAGI_HOST_URL", "http://localhost:7842")

from bomb_common import BombMiner, is_rock, drop_value, backpack_plan, BOMB_RADIUS

fails = []

def check(name, cond):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}")

# ── is_rock ──
check("Stone 是可炸", is_rock("Stone"))
check("Copper Node 可炸", is_rock("Copper Node"))
check("Iridium Node 可炸", is_rock("Iridium Node"))
check("Omni Geode Node 可炸", is_rock("Omni Geode Node"))
check("Barrel 不可炸(不浪费)", not is_rock("Barrel"))
check("Chest 不可炸", not is_rock("Chest"))

# ── drop_value ──
check("五彩碎片价值最高", drop_value("Prismatic Shard") > drop_value("Diamond"))
check("石头价值低", drop_value("Stone") < drop_value("Coal"))

# ── 构造假 surroundings：玩家在(0,0)，一堆石头围着一个空档 ──
def make_surroundings(rocks, center=(10, 10), radius=6):
    tiles = []
    for x, y, name in rocks:
        tiles.append({"x": x, "y": y, "passable": False, "object": name})
    return {
        "ok": True, "center": {"x": center[0], "y": center[1]}, "radius": radius,
        "location": "UndergroundMine5", "tiles": tiles,
        "npcs": [], "monsters": [], "farmers": [],
    }

class FakeBot(BombMiner):
    def __init__(self):
        super().__init__(port=7843, host_port=7842)
        self._fake = {}
        self._state = {
            "player": {"x": 10, "y": 10, "health": 100, "maxHealth": 100,
                       "stamina": 100, "maxStamina": 100},
            "inventory": [{"name": "Bomb", "stack": 10}],
            "location": {"name": "UndergroundMine5"},
            "time": {"timeOfDay": 1200},
        }
    def _get(self, ep, params=None, host=False):
        if ep == "/surroundings":
            r = self._fake.get("surroundings")
            if r: return r
        if ep == "/state":
            return self._state
        return {}
    def _post(self, ep, data=None, host=False):
        return {"ok": True}

bot = FakeBot()

# 场景1：石头围成十字，中心空 → 中心应是贪心锚点
cx, cy = 10, 10
rocks = [(cx+2, cy, "Stone"), (cx-2, cy, "Stone"), (cx, cy+2, "Stone"), (cx, cy-2, "Stone"),
         (cx+2, cy+1, "Stone"), (cx-2, cy+1, "Stone"), (cx+1, cy+2, "Stone"), (cx-1, cy+2, "Stone")]
bot._fake["surroundings"] = make_surroundings(rocks, center=(cx, cy))
anchor = bot.best_bomb_anchor(radius=6, bomb_radius=3, min_covered=3)
check("贪心找到锚点", anchor is not None)
if anchor:
    ax, ay, count, _ = anchor
    check(f"锚点覆盖>=6 (实际{count})", count >= 6)

# 场景2：石头分散 → 锚点覆盖数应该小一些
scattered = [(cx+6, cy, "Stone"), (cx-6, cy, "Stone"), (cx, cy+6, "Stone"),
             (cx, cy-6, "Stone"), (cx+6, cy+6, "Stone")]
bot._fake["surroundings"] = make_surroundings(scattered, center=(cx, cy))
anchor2 = bot.best_bomb_anchor(radius=6, bomb_radius=3, min_covered=3)
check("分散时可能没锚点(min_covered=3)", anchor2 is None or anchor2[2] < 3)

# 场景3：背包规划（假背包满）
bot._state["inventory"] = [
    {"name": "Bomb", "stack": 8},
    {"name": "Stone", "stack": 5, "value": 2},
    {"name": "Quartz", "stack": 3, "value": 20},
    {"name": "Diamond", "stack": 1, "value": 120},
]
bot._state["player"]["maxItems"] = 4  # 只有 4 格，已经满了
freed, plan = backpack_plan(bot, drop_below=15, need_slots=3)
check("背包规划建议丢 Stone", any("Stone" in ln for ln in plan))
check("背包规划不丢 Diamond", not any("Diamond" in ln and "🗑️" in ln for ln in plan))

print("\n" + ("🎉 全部通过" if not fails else f"❌ 失败项: {fails}"))
sys.exit(1 if fails else 0)
