"""sit/seats 纯逻辑自测（不打游戏，monkeypatch 假 /sittable 数据）。

重点验**「一排椅子只显示一张椅子的坐标」**（8 邻接聚类取最近）——这是恒点名的验收点，
且完全不需要游戏在场就能验。跑法：PYTHONIOENCODING=utf-8 python scripts/_sit_selftest.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
os.environ.setdefault("NAGI_HOST_URL", "http://localhost:7842")

import nagi_mcp_server as M

fails = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name} {extra}")


def seat(x, y, dist=None, kind="map", name="stool", free=1, cap=1, blocked=False):
    return {"kind": kind, "name": name, "x": x, "y": y, "seatX": float(x), "seatY": float(y),
            "capacity": cap, "free": free, "blocked": blocked,
            "dist": dist if dist is not None else ((x - ME[0]) ** 2 + (y - ME[1]) ** 2) ** 0.5}


ME = (20, 12)
SITTING = False


def fake_sittable(radius=7):
    """假端点：只回半径内的（和 C# 端点的过滤语义一致）。"""
    ss = [s for s in _ALL if s["dist"] <= radius]
    return {"ok": True, "location": "Saloon",
            "me": {"x": ME[0], "y": ME[1], "sitting": SITTING,
                   "seatX": 99 if SITTING else None, "seatY": 99 if SITTING else None},
            "radius": radius, "count": len(ss), "seats": ss}


def run(seats, me=(20, 12), sitting=False):
    global _ALL, ME, SITTING
    _ALL, ME, SITTING = seats, me, sitting
    M._SIT_CACHE.update({"t": 0.0, "radius": None, "data": None})   # 清 TTL 缓存
    M._SIT_HINT_KEY["sig"] = None                                   # 清变化签名
    return M._sit_hint()


M.api.sittable = fake_sittable
_ALL = []

# ── ① 一排 3 把椅子 → 只报最近那一把 ──
_all1 = [seat(20, 10), seat(21, 10), seat(22, 10)]                  # 一条横排（互相 8 邻接）
h = run(_all1)
check("一排3把椅子只报1个坐标", h.count("sit(") == 1, f"→ {h}")
check("报的是离玩家最近的那把 (20,10)", "sit(20,10)" in h, f"→ {h}")

# ── ② 两处分开的座位 → 两个坐标 ──
h = run([seat(20, 10), seat(21, 10), seat(25, 15)])
check("两簇 → 两个坐标", h.count("sit(") == 2, f"→ {h}")
check("两簇都取簇内最近的", "sit(20,10)" in h and "sit(25,15)" in h, f"→ {h}")

# ── ③ 长凳（一件家具多座位点，8 邻接）也并成一个 ──
h = run([seat(19, 11, kind="furniture", name="Wood Bench", cap=3, free=3),
         seat(20, 11, kind="furniture", name="Wood Bench", cap=3, free=3),
         seat(21, 11, kind="furniture", name="Wood Bench", cap=3, free=3)])
check("长凳多座点并成1个坐标", h.count("sit(") == 1, f"→ {h}")

# ── ④ 被占的座位不推荐 ──
h = run([seat(20, 10, blocked=True)])
check("被 NPC 占的座位不出现", h == "", f"→ {h!r}")
h = run([seat(20, 10, free=0, cap=1)])
check("坐满的座位不出现", h == "", f"→ {h!r}")

# ── ⑤ 变化才报：签名不变 → 第二次空 ──
run([seat(20, 10)])
h2 = M._sit_hint()
check("同一处座位不重复刷屏（变化才报）", h2 == "", f"→ {h2!r}")
h3 = run([seat(20, 10), seat(25, 15)])                             # 换了坐标集合 → 应该重新报
check("座位集合变了 → 重新报", h3.count("sit(") == 2, f"→ {h3!r}")

# ── ⑥ 最多只报 3 处（4 簇 → 3 个坐标） ──
h = run([seat(20, 10), seat(24, 10), seat(20, 15), seat(24, 15)])
check("最多报3处（4簇截断）", h.count("sit(") == 3, f"→ {h}")

# ── ⑦ 坐着 → 只留"scene at 起身"这一行指引 ──
#    2026-09-11 恒拍板：坐着时其余 enum 引导（可用域/地图/节日/可采集）全收掉，
#    这行成了**唯一指引** ⇒ 必须每次都报，不能按"变化才报"（否则坐久了只剩个光标题）。
h = run([], sitting=True)
check("坐着报起身提示", "坐着" in h and "scene at" in h, f"→ {h!r}")
check("起身文案留了「没起来就再点一次」后手", "再点一次" in h, f"→ {h!r}")
check("坐着这行每次都报（不按变化才报）", "scene at" in M._sit_hint(), "→ 被吞了")
check("坐姿签名写进去 = 站起来会重新枚举座位",
      M._SIT_HINT_KEY["sig"] is not None and M._SIT_HINT_KEY["sig"][0] == "sit",
      f"→ {M._SIT_HINT_KEY['sig']}")

# ── ⑧ 空座位表 → 不报 ──
check("没座位不报", run([]) == "")

# ── ⑨ seats op 输出格式（含「坐(x,y)」可直喂 scene sit） ──
_ALL = [seat(20, 10), seat(25, 15)]
_out = M.seats(12)
check("seats 列了坐标", "坐(20,10)" in _out, f"→ {_out!r}")

# ── ⑩ 🐛 回归：域 op 内嵌 strip 会被 _ops_run 丢掉，那一层必须"闭嘴且不消费" ──
#    2026-09-11 踩坑：`scene seats` 里死活不出 🪑 行，单进程调 _sit_hint() 却正常。
#    根因=`scene` 包了两层 _with_state，_ops_run 把内层整条砍掉——内层先把"变化"吃了，
#    外层重建时签名相同 → 判"没变化" → 整行对 AI 永久失踪。
_ALL = [seat(20, 10)]
M._SIT_CACHE.update({"t": 0.0, "radius": None, "data": None})
M._SIT_HINT_KEY["sig"] = None
M._OPS_INNER["n"] = 1                      # 假装"正在域 op 里，这层 strip 会被丢"
check("域op内层 strip 不产出提示", M._sit_hint() == "", "→ 内层也只想出，会被外层吃掉")
M._OPS_INNER["n"] = 0
check("内层不消费变化 → 外层照样出", "sit(" in M._sit_hint(), "→ 变化被内层白白吃掉了(BUG)")

# ── ⑪ 🐛 回归：站位要挑"离座位最近"的，不是固定顺序的第一个 ──
#    2026-09-11：`stool tall` 座位点比格子中心高 0.3 格 → 站着只剩 ~83px/96px 余量；
#    而 `/walk_to` 收尾在 y*64-32、`/position` 落在 y*64，**差 16px** ⇒ 同一个"站 (54,28)"，
#    走过去够得着、贴过去就超 96px，游戏还**静默不落座**。挑最近格把余量拉满。
M.api._post = lambda ep, data=None, timeout=10: (
    {"passable": True} if ep == "/passable" else {"ok": True})
_best = M._best_stand_tile(54, 26.7, (54, 27))
check("挑离座位最近的可站格 (54,26)", _best == (54, 26), f"→ {_best}")
_old = M._stand_near(54, 27)
check("对照：固定顺序的 _stand_near 会挑 (54,28)（余量更小）", _old == (54, 28), f"→ {_old}")
check("最近格确实比它更近",
      M._seat_dist(54, 26, 54, 26.7) < M._seat_dist(54, 28, 54, 26.7))

print()
# ═══════════════════════════════════════════
#  sit() 流程（含失败路径：宁报错别兜底）
# ═══════════════════════════════════════════
print()
print("── sit() 流程 ──")

_STATE = {"sitting": False}


def fake_interact(x, y):
    """假装 checkAction 落座了（只在目标是座位格时）。"""
    if any(int(s["x"]) == x and int(s["y"]) == y for s in _ALL):
        _STATE["sitting"] = True
        return {"ok": True, "actionTriggered": True}
    return {"ok": True, "actionTriggered": False}


def fake_ai_sittable(radius=7):
    d = fake_sittable(radius)
    sitting = _STATE["sitting"]
    d["me"]["sitting"] = sitting
    d["me"]["seatX"], d["me"]["seatY"] = (20, 10) if sitting else (None, None)
    return d


M.api.sittable = fake_ai_sittable
M.api.interact_at = fake_interact
M.api._post = lambda ep, data=None, timeout=10: (
    {"passable": True} if ep == "/passable" else {"ok": True})
_POS = {"p": (20, 12)}


def fake_position(x, y):
    _POS["p"] = (x, y)          # 假瞬移：真的能把人挪过去，否则走位分支永远到不了位
    return {"ok": True}


M.api.position = fake_position
M.api.face = lambda d: {"ok": True}
M.api.face_toward = lambda tx, ty: 2
M._wait_arrival = lambda *a, **k: True
M._stand_near = lambda tx, ty: (tx, ty + 1)
M._ai_pos = lambda: _POS["p"]


def sit_case(seats, target, me=(20, 12), sitting=False):
    global _ALL, ME
    _ALL, ME = seats, me
    _POS["p"] = me
    _STATE["sitting"] = sitting
    M._SIT_CACHE.update({"t": 0.0, "radius": None, "data": None})
    return M.sit(*target)


_o = sit_case([seat(20, 10)], (20, 10))                       # 就位→交互→落座
check("坐上目标座位 → 成功", "坐上" in _o and "没坐上" not in _o, f"→ {_o!r}")

_o = sit_case([seat(20, 10)], (13, 21))                       # 目标不是座位
check("非座位格 → 明确报错(不谎报)", "不是可坐物" in _o, f"→ {_o!r}")

_o = sit_case([seat(20, 10, blocked=True)], (20, 10))
check("被 NPC 占 → 明确报错", "被占" in _o, f"→ {_o!r}")

_o = sit_case([seat(20, 10, free=0, cap=1)], (20, 10))
check("坐满 → 明确报错", "被占" in _o, f"→ {_o!r}")

_o = sit_case([seat(20, 10)], (20, 10), sitting=True)
check("已坐着 → 提示先起身", "已经坐在" in _o, f"→ {_o!r}")

_o = sit_case([], (20, 10))
check("附近没座位 → 明确报错", "不是可坐物" in _o, f"→ {_o!r}")

# 交互没落座（比如被挡住）→ 必须报失败，不能假装成功
_orig = M.api.interact_at
M.api.interact_at = lambda x, y: {"ok": True, "actionTriggered": False}
_o = sit_case([seat(20, 10)], (20, 10))
check("交互没落座 → 明确报错(不谎报)", "没坐上" in _o, f"→ {_o!r}")
M.api.interact_at = _orig

print()
if fails:
    print(f"❌ {len(fails)} 项没过: {fails}")
    sys.exit(1)
print("✅ 全部通过")
