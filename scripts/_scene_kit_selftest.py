"""🧰 本图箱子/设备那一行 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-27

恒：「能一直看到当前场景的设备和箱子」→ 想清楚后拍板 **次次切图报一次，不常驻**。
这个文件把那条口径钉死，免得后人（或我自己）手一抖改成常驻：

  ① 切图报一次；**同一张图再调用 → 什么都不出**（这条就是"不常驻"的判据）
  ② 箱子**带坐标**（不带坐标 AI 还得再查一次，这行等于白加）；超 3 个只列前 3 + 「…另N个」
  ③ 设备**只报数量+类型、不含坐标**（农场上千台）
  ④ 箱子/稻草人/装饰**不算设备**（`_MACHINE_NON_PRODUCER`），别把箱数两遍
  ⑤ **室内照样报** —— 与 `_forage_summary` 相反（箱子/设备大多在屋里）
  ⑥ 空图不出行
  ⑦ 域 op 内层闭嘴**且不消费**（否则外层重建时判定"同图" ⇒ 整行对 AI 失踪）
  ⑧ 读不到时**不消费**（一次抖动不该把整张图静音到离开为止）
"""
import os
import re
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class KitApi:
    """假 API：按端点吐我们喂进去的箱子/机器。boom=True 模拟读不到。"""

    def __init__(self, chests=(), machines=(), boom=False):
        self.chests, self.machines, self.boom = list(chests), list(machines), boom
        self.calls = []

    def _ai_get(self, ep, *a, **k):
        self.calls.append(ep)
        if self.boom:
            raise RuntimeError("读不到")
        if ep == "/scan_chests":
            return {"ok": True, "chests": self.chests}
        if ep == "/machines":
            return {"ok": True, "machines": self.machines}
        return {}


_real = {n: getattr(M, n) for n in ("api", "_OPS_INNER")}

C1 = {"x": 58, "y": 15, "autoTag": "矿", "used": 3, "freeSlots": 5}
C2 = {"x": 58, "y": 14, "autoTag": "", "used": 1, "freeSlots": 7}
C3 = {"x": 52, "y": 10, "autoTag": "木", "used": 0, "freeSlots": 8}
C4 = {"x": 40, "y": 20, "autoTag": "", "used": 0, "freeSlots": 8}
C5 = {"x": 41, "y": 21, "autoTag": "", "used": 0, "freeSlots": 8}
M1 = {"type": "Keg", "status": "processing"}
M2 = {"type": "Keg", "status": "ready"}
M3 = {"type": "Furnace", "status": "empty"}
M4 = {"type": "Chest", "status": "empty"}          # ⚠️ 是箱子，不是设备
M5 = {"type": "Stone Junimo", "status": "empty"}   # ⚠️ 装饰


def fresh():
    """复位"切图才报"的闸门（每个用例都从'刚进这张图'开始）。"""
    M._SCENE_KIT_SEEN["loc"] = None


try:
    M._OPS_INNER["n"] = 0

    # ── ① 切图报一次，同图不再报 ──────────────────────────────────
    print("\n① 次次切图报一次；同图再调用 → 什么都不出（**这条就是「不常驻」**）")
    fresh()
    M.api = KitApi(chests=[C1, C2], machines=[M1, M2, M3])
    out = M._scene_kit_hint("Farm")
    ck("切图 → 出那一行", out.startswith("🧰 本图"), out)
    ck("…同一张图再来 → **空**（不是重播缓存那行）", M._scene_kit_hint("Farm") == "", M._scene_kit_hint("Farm"))
    ck("…再来一次还是空", M._scene_kit_hint("Farm") == "")
    fresh()
    out2 = M._scene_kit_hint("Farm")
    ck("…复位后（= 又切到这张图）→ 又能出", out2.startswith("🧰 本图"), out2)

    # ── ② 箱子带坐标 + 上限 ──────────────────────────────────────
    print("\n② 箱子：带坐标（不带就白加）+ 超 3 个只列前 3")
    fresh()
    M.api = KitApi(chests=[C1, C2], machines=[])
    out = M._scene_kit_hint("Farm")
    ck("…报了箱数和坐标", "箱×2" in out and "(58,15)" in out and "(58,14)" in out, out)
    ck("…带标签的箱子把标签也带上", "[矿]" in out, out)
    fresh()
    M.api = KitApi(chests=[C1, C2, C3, C4, C5], machines=[])
    out = M._scene_kit_hint("Farm")
    ck("…5 个箱子只列前 3 个坐标", len(re.findall(r"\(\d+,\d+\)", out)) == 3, out)
    ck("…并明说还有几个", "…另2个" in out, out)
    ck("…报的总数是 5（不是 3）", "箱×5" in out, out)

    # ── ③ 设备只报数量+类型、不含坐标；④ 箱子/装饰不算设备 ──────────
    print("\n③④ 设备：只报数量+类型（不含坐标）；箱子/装饰不算设备")
    fresh()
    M.api = KitApi(chests=[], machines=[M1, M2, M3, M4, M5])
    out = M._scene_kit_hint("Farm")
    ck("…箱子(Chest)/装饰(Stone Junimo) **不计入设备**（机×3 而不是 ×5）", "机×3" in out, out)
    ck("…点名了真正要收的两种（走 MACHINE_CN 的中文名）", "小桶×2" in out and "熔炉×1" in out, out)
    ck("…就绪台数单独报", "(就绪1)" in out, out)
    ck("…**不含任何机器坐标**（设备那一半一个括号都没有）",
       "(" not in out.replace("(就绪1)", ""), out)

    # ── ⑤ 室内照样报（与 _forage_summary 相反）──────────────────
    print("\n⑤ 室内照样报 —— 箱子/设备大多在屋里，照抄可采集那条的室内跳过会把最该报的地方全跳过")
    fresh()
    M.api = KitApi(chests=[{"x": 5, "y": 4, "autoTag": ""}], machines=[])
    for store in ("FarmHouse", "Cabin", "Big Shed", "Deluxe Barn"):
        fresh()
        out = M._scene_kit_hint(store)
        ck(f"…{store} 里有箱子 → 照报", "箱×1" in out, out)
    fresh()
    M.api = KitApi(chests=[C1], machines=[])
    ck("…农场也照报（对照组）", "箱×1" in M._scene_kit_hint("Farm"))

    # ── ⑥ 空图不出行 ─────────────────────────────────────────────
    print("\n⑥ 空图 → 不出行（不刷一个空壳占位）")
    fresh()
    M.api = KitApi(chests=[], machines=[])
    ck("…没箱没机 → 空串", M._scene_kit_hint("Town") == "", M._scene_kit_hint("Town"))
    fresh()
    M.api = KitApi(chests=[], machines=[M4, M5])   # 只有箱子和装饰 ≠ 有设备
    ck("…只有箱子/装饰（都被排除）→ 也空串", M._scene_kit_hint("Town") == "", M._scene_kit_hint("Town"))
    ck("…空图名 → 空串", M._scene_kit_hint("") == "")

    # ── ⑦ 域 op 内层闭嘴且不消费 ────────────────────────────────
    print("\n⑦ 域 op 内层：闭嘴，且**不消费**这次机会")
    fresh()
    M.api = KitApi(chests=[C1], machines=[])
    M._OPS_INNER["n"] = 1
    ck("…内层 → 闭嘴", M._scene_kit_hint("Farm") == "")
    ck("…而且没消费掉（外层还有机会报）", M._SCENE_KIT_SEEN["loc"] is None)
    M._OPS_INNER["n"] = 0
    ck("…外层照常报得出来", "箱×1" in M._scene_kit_hint("Farm"))

    # ── ⑧ 读不到时不消费 ────────────────────────────────────────
    print("\n⑧ 读不到（端点抛异常）→ 空串，且**不消费**（一次抖动别把整张图静音）")
    fresh()
    M.api = KitApi(boom=True)
    ck("…读不到 → 空串", M._scene_kit_hint("Farm") == "")
    ck("…没消费（还当自己没进过这张图）", M._SCENE_KIT_SEEN["loc"] is None)
    M.api = KitApi(chests=[C2], machines=[])
    ck("…恢复了 → 同一张图照样报得出来", "箱×1" in M._scene_kit_hint("Farm"))

finally:
    for k, v in _real.items():
        setattr(M, k, v)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
