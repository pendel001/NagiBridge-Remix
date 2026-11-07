# -*- coding: utf-8 -*-
"""🌊 浇水把 AI 浇晕了 —— 低体力闸在大田里**只判一次**。2026-11-07

恒：「浇水是不是没有低体力保护哇！居然晕倒了还一直在浇水……我笑死了。」

🩸 真相：闸是有的（`每簇动手前`看一眼，`stamina_common.is_low`），但
   `cluster(pts, gap=10)` 在 220 格的大田里**把整片并成一簇** ⇒ 闸整场只判一次，
   判完那一簇就一口气浇到 0 体力（`tool_area` 那边不会替我们停）⇒ 晕倒还在浇。

判据：
  ① 大簇要切小（`cluster` 的 max_tiles/max_span）：每块 ≤24 格、高 ≤9 行，闸才真的每块都问；
  ② `_water_cluster` 每次进场都问一次体力，低了返回 `"low"`（一下都不挥）；
  ③ `run()` 进门先判一次（已经累了 → 一格都不浇）；
  ④ 补浇那一轮也过同一把闸。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠️ `water_crops.py` 在**模块级** parse_args()（用 `if __name__` 收 run）⇒ import 前先把 argv 摆好
sys.argv = ["water_crops.py", "--port", "7843"]
import water_crops as W

_REAL_API = W.api     # ⚠️ 一定要换掉：不换就会**真的往游戏打 /tool_area**

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """只喂 `_water_cluster` 要的：体力 / 浇水那条 POST / 打水。"""
    def __init__(self, stamina=200, max_sta=270, low_after=None):
        self.sta, self.max = stamina, max_sta
        self.low_after = low_after      # 挥几次之后把体力打到 0（模拟"浇着浇着烧空"）
        self.rects = 0
        self.logs = []

    def log(self, m):
        self.logs.append(str(m))

    def _post(self, ep, data=None, timeout=10):
        if ep == "/tool_area":
            self.rects += 1
            if self.low_after is not None and self.rects > self.low_after:
                self.sta = 0
            # 第一次就"壶空了"⇒ 会走打水再回来那一轮，正好验"回来时体力已经下去了"
            return {"ok": True, "watered": 0, "patches": 0,
                    "out_of_water": (self.rects == 1 and self.low_after == 0)}
        return {"ok": True}

    def player_stamina(self):
        return (self.sta, self.max)

    def refill_can_natural(self, log=None):
        return True, "（假打水）"


try:
    print("\n① 大簇切小：一条 30×8 的田不再是一整簇")
    big = [(x, y) for y in range(0, 8) for x in range(0, 30)]     # 240 格
    groups = W.cluster(big, gap=10)
    ck("①切成多块（不是整片一簇）", len(groups) > 1, f"{len(groups)} 簇")
    ck("①每块 ≤24 格", all(len(g) <= 24 for g in groups), str(sorted(len(g) for g in groups))[-3:])
    ck("①每块高 ≤9 行", all(max(p[1] for p in g) - min(p[1] for p in g) <= 9 for g in groups))
    _all = sorted(p for g in groups for p in g)
    ck("①切完**一格不少**（并集 = 原集合）", _all == sorted(big), f"{len(_all)} vs {len(big)}")

    print("\n①b 小簇**不拆**（别把正常的几簇拆碎、多跑几趟蓄力）")
    small = [(10, 10), (11, 10), (10, 11)]
    ck("①b 3 格仍是一簇", len(W.cluster(small, gap=10)) == 1)

    print("\n② `_water_cluster` 进来先看体力：低了**一下都不挥**")
    a = FakeApi(stamina=5)
    W.api = a
    a_ret = W._water_cluster(0, 0, 3, 3)
    ck("②返回 \"low\"", a_ret == "low", str(a_ret))
    ck("②一次 /tool_area 都没发", a.rects == 0, str(a.rects))
    ck("②日志说清是体力", any("体力" in m or "体力" in m for m in a.logs), str(a.logs[-2:]))

    print("\n②b 体力够时照常浇（别把正常路径拦了）")
    b = FakeApi(stamina=200)
    W.api = b
    ck("②b 返回 True", W._water_cluster(0, 0, 3, 3) is True)
    ck("②b 真发了 /tool_area", b.rects >= 1, str(b.rects))

    print("\n②c 浇到一半体力掉下去 ⇒ 下一次进场就停（返回 \"low\"）")
    c = FakeApi(stamina=200, low_after=0)     # 第一次 tool_area 后体力归 0
    W.api = c
    r = W._water_cluster(0, 0, 3, 3, tries=4)
    ck("②c 打水回来那一轮**先判体力** ⇒ 返回 \"low\"", r == "low", str(r))
    ck("②c 只发了那一次 /tool_area（回来没再硬浇）", c.rects == 1, str(c.rects))

    print("\n③ 源码接线：run() 进门也判一次；补浇那一轮也判")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "water_crops.py"), encoding="utf-8").read()
    ck("③`run()` 里有进门预检（真的判 `_sta_low(_c0)`）",
       "if _sta_low(_c0):" in _src and "一格都没浇" in _src, _src[:0])
    ck("③主循环认 `\"low\"`", '"low"' in _src and "_rc == \"low\"" in _src, _src[:0])
    ck("③补浇那一轮也过闸", "_rc2 == \"low\"" in _src, _src[:0])
    ck("③`cluster` 带 max_tiles/max_span", "max_tiles=24" in _src and "max_span=9" in _src)
finally:
    W.api = _REAL_API

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 浇水不再把人浇晕：大簇切小 + 每块进场都判体力 + 进门预检。")
