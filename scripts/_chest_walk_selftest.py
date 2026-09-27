"""🚶 存取箱子的「拟人走位」：走最近的箱 + 等到真的走到 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒真机：「刚才 AI 只是调用了钓鱼域的 help，为什么跑到了书摊老板前面的空地？」
—— `help` 是纯文本（回包连状态条都不附），碰不到人。真凶是**十秒前的 `storage store`**：

    `/walk_to` 在 C# 里**发射后不管**（`HandleWalkTo` 挂上 `_walkRoute` 就 return，
    注释原话"返回后需轮询等待到达"），而 `_walk_to_chest` 从前**只 sleep(0.3)、从不轮询**
    ⇒ 工具 1.3 秒就"办完了"，**人从那一刻起才慢慢往箱子走**。而挑的箱又是**空位最多**的，
    挑中了镇子另一头 (114,17) 那个 ⇒ 恒看着角色一路跑过半个镇子，而 AI 只是调了个 help。

恒拍板：**走最近的箱 + 等到真的走到**。测四件：
  ① 主箱挑**离我最近**的（不是空位最多）—— 走位只是观感，东西进哪箱由 C# 决定
  ② 读不到位置 → **不走位**（宁可不演，也别瞎挑一个远箱让人跑过去）
  ③ 走位**没到就说没到**（宁报错别兜底：不许静默当"已到位"）
  ④ 走位那行要出现在 storage store 的回包里（否则 AI/恒都看不到"人还在路上"）
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# 三个箱：近的小而满、远的空 —— 老口径（空位最多）会挑远的，新口径必须挑近的
CHESTS = [
    {"x": 8, "y": 93, "freeSlots": 1, "items": []},      # 就在我旁边
    {"x": 114, "y": 17, "freeSlots": 34, "items": []},   # 书摊那片山坡（老口径会挑它）
    {"x": 123, "y": 58, "freeSlots": 33, "items": []},
]


class FakeApi:
    def __init__(self):
        self.calls = []

    def state(self, **kw):
        return {"location": {"name": "Town"}, "player": {"x": 3, "y": 91, "name": "轮回"},
                "inventory": [{"displayName": "木材", "name": "Wood", "itemId": "(O)388"}]}

    def _get(self, ep):
        if ep == "/scan_chests":
            return {"ok": True, "chests": CHESTS}
        raise AssertionError("意外端点 " + ep)

    def position(self, x, y):
        self.calls.append(("position", x, y))
        return {"ok": True}

    def store_all(self, **kw):
        self.calls.append(kw)
        return {"ok": True, "mode": "smart", "scope": "specified", "location": "Town",
                "stored": [{"item": "Wood", "count": 1, "to": {"x": 8, "y": 93}}],
                "leftovers": [], "totalFree": 30}


_real = {n: getattr(M, n) for n in ("api", "_walk_and_wait", "_ai_pos", "_storage_default_for_loc",
                                    "_walk_to_chest")}
try:
    M._storage_default_for_loc = lambda: None
    M._ai_pos = lambda: (3, 91)
    M.api = FakeApi()

    print("\n① 主箱挑**离我最近**的（不是空位最多）")
    # (3,91) 到 (8,93)=7 格 / (114,17)=185 格 / (123,58)=153 格 → 必须挑 (8,93)
    got = M._primary_chest_for_smart()
    ck("挑中旁边的 (8,93)，而不是空位最多但远在书摊山坡的 (114,17)",
       got == {"x": 8, "y": 93}, str(got))

    print("\n② 读不到位置 → 不走位（宁可不演，别瞎挑）")
    M._ai_pos = lambda: (None, None)
    ck("位置读不到 → 返回 None（调用方就不走位了）", M._primary_chest_for_smart() is None)
    M._ai_pos = lambda: (3, 91)

    print("\n③ 走位没到就说没到（不许静默当已到位）")
    M._walk_and_wait = lambda loc, x, y, timeout=0: (True, "")
    line = M._walk_to_chest(8, 93)
    ck("到了 → 回「已走到 (8,92) 箱边」", "已走到" in line and "(8,92)" in line, line)

    M._walk_and_wait = lambda loc, x, y, timeout=0: (False, "走位超时没到（Town 8,92）")
    line = M._walk_to_chest(114, 17)
    ck("没到 → 如实说「走位没到」+ 原文原因",
       "走位没到" in line and "走位超时没到" in line, line)
    ck("…并提醒别以为已经站到箱边（AI 会照这句判断自己在哪）", "别以为" in line, line)

    def _boom(loc, x, y, timeout=0):
        raise RuntimeError("定位服务没醒")
    M._walk_and_wait = _boom
    line = M._walk_to_chest(8, 93)
    ck("走位这条路整个炸了 → 明确说「走位失败」+ 直接落位（老兜底保留、但不再装没事）",
       "走位失败" in line and "已直接落到" in line, line)

    print("\n④ 走位那行要出现在 storage store 的回包里")
    M.api = FakeApi()
    M._walk_and_wait = lambda loc, x, y, timeout=0: (True, "")
    out = M.storage_store.__wrapped__(items="Wood")
    ck("store 回包里有 🚶 走位实况（恒/AI 都能看到人到底在不在箱边）",
       "🚶" in out and "已走到" in out, out)
    ck("…存的账照旧", "进 (8,93)" in out, out)

    print("\n⑤ 走位目标=站在箱**上方**一格（朝下），不是箱子本体")
    seen = {}
    M._walk_and_wait = lambda loc, x, y, timeout=0: (seen.update(t=(loc, x, y)), (True, ""))[1]
    M._walk_to_chest(8, 93)
    ck("走到 (8,92) 而不是 (8,93)（站箱子上会被自己挡住）",
       seen.get("t") == ("Town", 8, 92), str(seen))

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
