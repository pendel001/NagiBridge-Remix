"""🛤️ 「温泉在铁路石堆后面，夏3日前别叫 AI 去泡」 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-25

恒：「之前观察到**低体力警告的解决办法可能有点长**。而且 railroad 我记得有门禁，特定天数之后才解锁。
**查一下，然后在那个日期之前都不要误导 AI 去泡温泉**。」

两件都办了，这里钉住：
  ① **门禁日期是年1 夏3日**（不是"特定天数"那么含糊）——出处 `ModEntry.cs` 的 `RuntimeBlockers` 表：
     `("Mountain", "railroadAreaBlocked", "railroadBlockRect")` 后面那行注释就写着「🛤️ 铁路石堆：夏3日地震后清」。
     石堆堵的是 **Mountain 上去铁路那条路** ⇒ 清掉之前**温泉根本到不了**。
  ② **低体力警告里那一整套浴场攻略挪走了**（它本来长在警告里：大厅(2,4)面0推更衣室门→走到底往下蹭进泳池…）——
     攻略归 `locations.POI["温泉(大厅)"]` + 进浴场时的一次性「♨️ 浴场引导」，警告只留三条路 + 敲哪个 op。
  ③ **塌方那条提示也在骗人**：它把「温泉」列进"现在能去的地方"，而温泉正在石堆后面 ⇒ 铁路没通就不列。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import locations as L
import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


print("\n① 门禁判据：年1 夏3日起才通")
for label, se, d, y, want in [("春5（今天）", "spring", 5, 1, False),
                              ("夏1", "summer", 1, 1, False),
                              ("夏2（前一天）", "summer", 2, 1, False),
                              ("**夏3（地震当天）**", "summer", 3, 1, True),
                              ("秋1", "fall", 1, 1, True),
                              ("冬1", "winter", 1, 1, True),
                              ("年2春5（早清了）", "spring", 5, 2, True)]:
    got = L.railroad_open(se, d, y)
    ck(f"{label} → 通={want}", got == want, f"得到 {got}")
ck("**读不到日期 → 当没通**（宁少提一条路，也别让人白跑）", L.railroad_open(None, None, None) is False)


class NoNet:
    def _get(self, *a, **k):
        return {}

    def _post(self, *a, **k):
        return {}

    def state(self, **k):
        return {}


def strip(season, day, hp=10, st=20):
    return {"raw": {},
            "player": {"name": "Claude", "money": 100, "maxItems": 24,
                       "health": hp, "maxHealth": 100, "stamina": st, "maxStamina": 270,
                       "x": 5, "y": 5, "currentTool": "Axe"},
            "location": {"name": "Farm"},
            "time": {"season": season, "dayOfMonth": day, "year": 1, "timeOfDay": 900},
            "inventory": [], "activeMenu": None, "activeEvent": None, "otherPlayers": [],
            "mailbox": None, "alerts": []}


print("\n② 低体力/低血警告：短了，而且温泉按门禁出现")
_real = M.api
try:
    M.api = NoNet()
    M._MAIL_CACHE.update(ts=0, list=None)
    M._STATE_DELTA.clear()
    _spring = [ln for ln in M._build_state_strip(strip("spring", 5), full=False).splitlines() if "🚨" in ln]
    _summer = [ln for ln in M._build_state_strip(strip("summer", 3), full=False).splitlines() if "🚨" in ln]
    ck("春5：警告里有", bool(_spring), "没有 🚨 行")
    _s = _spring[0] if _spring else ""
    _m = _summer[0] if _summer else ""
    ck("…**不列温泉**（石堆还堵着）", "温泉" not in _s, _s)
    # ⚠️ 2026-10-01：`cabin` 撤出顶层（cook→daily、其余→scene/farm/daily/check）⇒ 这行文案
    #    从 `cabin ops=sleep` 改成 `daily ops=sleep`。「吃 / 睡」这两条路本身没变。
    ck("…但另两条路都在（吃 / 睡）", "daily ops=eat" in _s and "daily ops=sleep" in _s, _s)
    ck("…**整套浴场攻略不再长在警告里**（「推更衣室门/蹭进泳池」那两句没了）",
       "更衣室" not in _s and "泳池" not in _s, _s)
    ck("…整个警告明显变短（< 120 字）", len(_s) < 120, f"{len(_s)} 字: {_s}")
    ck("夏3：**列**温泉", "温泉" in _m and "map go 温泉" in _m, _m)
    ck("…夏3 也还是短的", len(_m) < 160, f"{len(_m)} 字")
finally:
    M.api = _real

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
