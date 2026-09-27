"""🚶 `map_go` 走位没到就别报"已走到" —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒真机：「**是不是路途太遥远了**，从错误箱调用 fish 跑过来，见它每次都朝向错报面前没水。」

现场三个数三个地方（同一次调用）：
    map_go 的回包：🗺️ 已在 Town，走到 镇鲶鱼钓点（(3, 93)）      ← 说到了
    fish_run 读到： [fish] pos: (68,74)                          ← 人还在半路
    状态条：       📍 Town (52,91)
根因：`map_go` 同图 POI 那条分支里 `_walk_and_wait(...)` 的**返回值被丢掉**，
20 秒超时**照样**往下走、回包还写"已走到" = **谎报到达**（"报成功但事没发生"家族）。
从书摊那片小山坡（Town 114,17）走到 (3,93) 一百多格，20 秒走不完 —— 恒的"路途太遥远"说对了。

后果：上层 `go_fishing` 拿这句当"到点了"就地开钓 ⇒ **鱼机朝着走路方向抛竿** ⇒「抛竿方向没有水」。

测三件：
  ① 第一次等到超时 → **再补一段**（长走位常见 30s+）
  ② 补完还没到 → 回包里是「**还没走到**」「人还在半路」，**绝不出现"已走到"**
  ③ 真走到了 → 照旧「已在 X，走到 POI」+ 站位朝向
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # 先 import 它：navigation 的宿主注入发生在它末尾
import navigation as N

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """人在 Town (68,74)（真机那一刻脚本读到的位置）。"""
    def state(self, **kw):
        return {"location": {"name": "Town"}, "player": {"x": 68, "y": 74},
                "time": {}, "inventory": []}

    def _get(self, ep):
        return {"ok": True, "chests": []}

    def _post(self, ep, data=None):
        return {"ok": True}

    def player_tile(self):
        return (68, 74)


_PATCH = ("api", "_walk_and_wait", "_apply_poi_stand_face",
          "_step_into_building", "_with_state", "_festival_poi_active")
_real = {n: getattr(N, n) for n in _PATCH}
try:
    N.api = FakeApi()
    N._with_state = lambda s: s                                   # 不拼状态条（离线）
    N._apply_poi_stand_face = lambda name: " [站位朝向]"
    N._step_into_building = lambda loc, pos: ""
    # ⚠️ 这个 host 函数的语义是"**这个 POI 现在去得了吗**"（节日 POI 非节日时=去不了）——
    #    返回 False 会在 map_go 更前面被拦成「只在节日开放」，根本走不到走位分支 ⇒ 必须 True。
    N._festival_poi_active = lambda *a, **k: True

    waits = []

    def _timeout(loc, x, y, timeout=0):
        waits.append(timeout)
        return False, f"走位超时没到（{loc} {x},{y}）"

    print("\n① 没走到 → 补一段再等（别 20 秒就认了）")
    N._walk_and_wait = _timeout
    out = N.map_go("镇鲶鱼钓点")
    ck("第一次 20s 超时后**又等了一次**（长走位要 30s+）", waits == [20, 30], str(waits))

    print("\n② 还是没到 → 如实说，绝不说「已走到」")
    ck("回包里有「还没走到」", "还没走到" in out, out)
    ck("…并点明人还在半路（别让上层当到点了）", "半路" in out, out)
    ck("…**没有**「已走到」（这句以前会骗上层就地开钓）", "已走到" not in out, out)
    ck("…也没白设站位/朝向", "站位朝向" not in out, out)

    print("\n③ 真走到了 → 照旧报到达 + 应用站位朝向")
    waits.clear()
    N._walk_and_wait = lambda loc, x, y, timeout=0: (waits.append(timeout), (True, ""))[1]
    out = N.map_go("镇鲶鱼钓点")
    ck("报「已在 Town，走到 镇鲶鱼钓点」", "已在" in out and "镇鲶鱼钓点" in out, out)
    ck("…并应用了站位/朝向", "站位朝向" in out, out)
    ck("…而且**没白等第二段**（第一次就成了）", waits == [20], str(waits))

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(N, k, v)
sys.exit(1 if FAIL else 0)
