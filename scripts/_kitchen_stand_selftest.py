# -*- coding: utf-8 -*-
"""钉子：做饭前**就位到灶台**的站位/朝向 —— 纯离线（不连游戏）。

来历（恒 2026-10-06 真机）：
  「厨房的位置你是硬编的吗，**走偏了一点点**，到厨房旁边的格子去了」
  「整个厨房五格长都可以交互，**一般我会站中间**，刚刚是走到斜角去了，所以看着不是很习惯
   （这么长了还站到旁边）」

⇒ 三条判据：
  ① **灶台格 = 扫地图得来**（`/tile_props?scan=Action` 过滤 `kitchen`/`Kitchen`，任意图层），
     ⛔ **不许硬编码坐标**（农舍/小屋/岛屋厨房布局与长度都不同）。
  ② **候选站位 = 灶台的正邻格**（上下左右）：
     · ⛔ 斜角**不算"就位"**（所以站在斜角会真的挪一步）
     · ⛔ 斜角也**不当候选**（候选里根本不该出现斜角）
     · 一排灶台 ⇒ **站到这一排的中间**（不是"离自己最近"那格：那样长灶台会站到边上）
  ③ 到位后**摆正朝向**（面朝灶台那一格）——光"够得着"不等于"正对着"。

跑：`python scripts/_kitchen_stand_selftest.py`
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, HERE)
import nagi_mcp_server as M   # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_src = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8").read()


def _func_body(name):
    i = _src.index(f"def {name}(")
    j = _src.index("\ndef ", i + 10)
    return _src[i:j]


_body = _func_body("_go_to_kitchen")
# 注释里**必须**能写旧形状（不然下一个读的人又不知道坑在哪）⇒ 判据只看代码行
_code = "\n".join(l for l in _body.splitlines() if not l.lstrip().startswith("#"))

print("① 站位/朝向判据（源码级）")
ck("…候选＝**正邻格**（四方向 + 各自该朝哪边）",
   "((0, 1, 0), (0, -1, 2), (-1, 0, 1), (1, 0, 3))" in _code)
ck("…⛔ 老形状（8 邻 `for dx in (-1, 0, 1)`）**已经不在代码里**",
   "for dx in (-1, 0, 1)" not in _code, "斜角候选还在")
ck("…「就位」判据是 `abs+abs == 1`（**斜角不算就位**）",
   "abs(px - tx) + abs(py - ty) == 1" in _code)
ck("…排序先看**离灶台簇中点**、同分才比离自己近",
   "mid_x = sum(" in _code and "cands.sort()" in _code
   and "abs(cx - mid_x) + abs(cy - mid_y)" in _code)
ck("…到位后 `/face` 摆正朝向", '"/face"' in _code and "_face" in _code)

print("② 灶台格必须扫地图（恒：「你是硬编的吗」）")
_fk = _func_body("_find_kitchen_tiles")
ck("…走 `/tile_props` 扫 `Action` 再筛 `kitchen`", "/tile_props" in _fk and '"kitchen"' in _fk)
ck("…注释里明写「必须扫地图不能硬编码」", "必须扫地图不能硬编码" in _fk)

# ── 运行时：桩 ──────────────────────────────────────────────────────────────
_real = (M.api.state, M.api._post, M._find_kitchen_tiles, M.navigation.walk_to, M._with_state)
WALKED, POSTS = [], []
_COUNTER = []


class _St:
    def __init__(self):
        self.pos = (41, 24)

    def state(self, *a, **k):
        return {"player": {"x": self.pos[0], "y": self.pos[1]}}


_st = _St()


def _post(ep, data=None, *a, **k):
    POSTS.append((ep, data))
    if ep == "/passable":                     # ⚠️ 灶台格自己**站不住**（真机：走位会落到邻格）
        return {"passable": (data.get("x"), data.get("y")) not in _COUNTER}
    return {"ok": True}


def _walk_to(**k):
    WALKED.append((k.get("x"), k.get("y")))
    _st.pos = (k.get("x"), k.get("y"))        # 模拟"真走到了"（下面的到达回读靠它）
    return "🚶 ok"


M.api.state = _st.state
M.api._post = _post
M.navigation.walk_to = _walk_to
M._with_state = lambda s: s

try:
    _flat = [(19, 23), (20, 23), (21, 23), (22, 23), (23, 23)]   # 恒家那排 5 格

    print("③ 一排 5 格（x=19..23, y=23）：从远处过来")
    _COUNTER[:] = _flat
    M._find_kitchen_tiles = lambda location=None: list(_flat)
    _st.pos = (41, 24)
    WALKED.clear(); POSTS.clear()
    r = M._go_to_kitchen()
    ck("…不发错误", r is None, str(r))
    ck("…站到**这一排的中间**（x=21），⛔ 不是最近那格（x=23）",
       bool(WALKED) and WALKED[0][0] == 21, str(WALKED))
    ck("…站的是**正邻格**（y=24 或 22），⛔ 不是斜角",
       bool(WALKED) and WALKED[0][1] in (22, 24), str(WALKED))
    ck("…到位后**面朝灶台**（站下面 ⇒ 朝上 0／站上面 ⇒ 朝下 2）",
       any(p[0] == "/face" and (p[1] or {}).get("direction") in (0, 2) for p in POSTS), str(POSTS))

    print("④ 已经在灶台**正旁边** ⇒ 一步都不走（恒 09-11：已在厨房就不折腾）")
    _st.pos = (18, 23)
    WALKED.clear(); POSTS.clear()
    r = M._go_to_kitchen()
    ck("…正邻格（左边那格）⇒ 不挪人", r is None and WALKED == [], str(WALKED))

    print("⑤ 只在**斜角**够得着 ⇒ 要挪（斜角不算就位）")
    _st.pos = (18, 24)
    WALKED.clear(); POSTS.clear()
    r = M._go_to_kitchen()
    ck("…斜角 ⇒ 真的走过去", r is None and len(WALKED) == 1, str(WALKED))
    ck("…且还是去**中间**（x=21）", bool(WALKED) and WALKED[0][0] == 21, str(WALKED))

    print("⑥ 竖排灶台（x=10, y=10..12）⇒ 中间=(10,11) 的上下邻格 + 朝向左右")
    _vert = [(10, 10), (10, 11), (10, 12)]
    _COUNTER[:] = _vert
    M._find_kitchen_tiles = lambda location=None: list(_vert)
    _st.pos = (30, 30)
    WALKED.clear(); POSTS.clear()
    r = M._go_to_kitchen()
    ck("…站到竖排中间那格的**正邻格**",
       bool(WALKED) and WALKED[0][1] == 11 and WALKED[0][0] in (9, 11), str(WALKED))
    ck("…朝向是**左右**（面朝灶台）",
       any(p[0] == "/face" and (p[1] or {}).get("direction") in (1, 3) for p in POSTS), str(POSTS))
finally:
    (M.api.state, M.api._post, M._find_kitchen_tiles, M.navigation.walk_to, M._with_state) = _real

print()
if FAIL:
    print(f"❌ 失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("✅ 全过（0 失败）")
