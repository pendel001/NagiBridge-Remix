# -*- coding: utf-8 -*-
"""🚪 `map go 农舍` 进不了门 —— 不是"故意让你自己推"，是**门格形状写错了**。2026-11-08

恒：「**为什么 ai 调 farmhouse 总是报进不来门**。我还以为是故意的要自己推门」。

真机账（`/farm_buildings` 现场读的，不是猜）：
```
type        x  y  w h  doorX doorY indoorsName
Farmhouse  59 12  9 5   64    14     （空）
Cabin      52 14  5 3   54    15   Cabin
```
`Farmhouse` **不在** `locations.BUILDING_DOORS` 里 ⇒ 走 `_resolve_place()` 那条动态兜底；
而那条写的是 `door = (t[1], t[2])`（两个 int），下面却按 `(图, (x, y))` 解包
⇒ **`TypeError: cannot unpack non-iterable int object`** ⇒ 门格 `None`、
报成「走到门格 None 也推了门，游戏没让进」——**一步都没走**（`session_log` 13:35:18 那行原话）。
⚠️ 小屋/畜棚/鸡舍/工棚/农舍**都走这条**（它们都不在静态门表里）⇒ 整个"农场建筑族"进不去。

判据：
  ① `_resolve_place("FarmHouse")` 能解析出真机那个门 `("Farm", 64, 14)`；
  ② `_enter_building_door` 拿到的是 `(图, (x,y))` 形状，**不再 TypeError**；
  ③ 形状再错（被谁改坏）⇒ 报一句**看得懂**的话，而不是把 `TypeError` 糊在"游戏没让进"后面；
  ④ 农场建筑族（Cabin/Shed/Coop/Barn/Greenhouse…）逐个都不许掉进 `error` 这一档。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import locations
import navigation as N

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# 🔧 真机 `/farm_buildings` 原样（2026-11-08 现场读的）
LIVE_BUILDINGS = [
    {"type": "Pet Bowl", "x": 53, "y": 7, "width": 2, "height": 2, "doorX": 52, "doorY": 6},
    {"type": "Shipping Bin", "x": 71, "y": 14, "width": 2, "height": 1, "doorX": 70, "doorY": 13},
    {"type": "Greenhouse", "x": 25, "y": 10, "width": 7, "height": 6, "doorX": 28, "doorY": 15},
    {"type": "Farmhouse", "x": 59, "y": 12, "width": 9, "height": 5, "doorX": 64, "doorY": 14},
    {"type": "Cabin", "x": 52, "y": 14, "width": 5, "height": 3, "doorX": 54, "doorY": 15,
     "indoorsName": "Cabin"},
]


class FakeApi:
    """假 7843。`enter=True` 时"推门"会真的把人送进屋（`/interact` 把 location 改成目标图）。"""

    def __init__(self, enter=False, loc="Farm"):
        self.loc = loc
        self.enter = enter
        self.posts = []

    def state(self):
        return {"location": {"name": self.loc}, "player": {"x": 40, "y": 5, "isMoving": False}}

    def _post(self, ep, data=None, **k):
        self.posts.append((ep, data))
        if ep == "/interact" and self.enter:
            d = data or {}
            if (d.get("x"), d.get("y")) == (64, 14):
                self.loc = "FarmHouse"
        return {"ok": True}

    def interact_at(self, x, y):
        self.posts.append(("/interact", {"x": x, "y": y}))
        if self.enter and (x, y) == (64, 14):
            self.loc = "FarmHouse"
        return {"ok": True}

    def _get(self, ep, params=None):
        return {"open": False}

    def _ai_post(self, ep, data=None):
        return {"ok": True}


# —— 全程不真走路、不真睡 ——
_SLEEP_BAK = N.time.sleep
N.time.sleep = lambda *_a, **_k: None


def _stub_walk(ok=True):
    calls = []

    def _w(loc, x, y, timeout=10, **kw):
        calls.append((loc, x, y))
        return ok, f"（假走位到 {loc}({x},{y})）"

    N._walk_and_wait = _w
    return calls


_BAK = (N.api, N._buildings, N._walk_and_wait, N._ai_pos)
N._buildings = lambda: [dict(b) for b in LIVE_BUILDINGS]

try:
    print("\n① 真机那份 `/farm_buildings` ⇒ `_resolve_place('FarmHouse')` 认得农舍门")
    _r = N._resolve_place("FarmHouse")
    ck("①解析成 ('Farm', 64, 14)（真机 doorX/doorY）", _r == ("Farm", 64, 14), str(_r))
    _r2 = N._resolve_place("农舍")
    ck("①中文「农舍」也到同一个门", _r2 == ("Farm", 64, 14), str(_r2))
    ck("①农舍确实不在静态门表里（所以才走动态兜底）",
       "FarmHouse" not in locations.BUILDING_DOORS, "在表里的话这题不成立")

    print("\n② `_enter_building_door` 不再 TypeError：门格 = (64,14)，而且真去走了")
    _a = FakeApi(enter=False)
    N.api = _a
    _w = _stub_walk(ok=True)
    N._ai_pos = lambda: (40, 5)
    _ok, _why, _dt, _note = N._enter_building_door("FarmHouse")
    ck("②没掉进 error 这档", _why != "error", f"why={_why} note={_note}")
    ck("②门格不是 None", _dt == (64, 14), str(_dt))
    ck("②真的去走了那个门格", ("Farm", 64, 14) in _w, str(_w))
    ck("②备注里没有 TypeError", "TypeError" not in (_note or ""), str(_note))

    print("\n③ 推门成了 ⇒ 判「进去了」，并且真发了 interact(64,14)")
    _a = FakeApi(enter=True)
    N.api = _a
    _stub_walk(ok=True)
    _ok2, _why2, _dt2, _note2 = N._enter_building_door("FarmHouse")
    ck("③报成功", _ok2 is True and _why2 == "", f"{_ok2} {_why2} {_note2}")
    ck("③interact 打的是门格 (64,14)",
       ("/interact", {"x": 64, "y": 14}) in _a.posts, str(_a.posts[:6]))

    print("\n④ 农场建筑族逐个过一遍：谁都不许掉进 `error`")
    for _loc in ("FarmHouse", "Cabin", "Greenhouse", "Shed", "Coop", "Barn", "Big Shed"):
        _a = FakeApi(enter=False)
        N.api = _a
        _stub_walk(ok=True)
        _o, _wh, _d, _nt = N._enter_building_door(_loc)
        ck(f"④{_loc}：why 不是 error", _wh != "error", f"{_wh} {_nt}")
        ck(f"④{_loc}：备注里没有 TypeError", "TypeError" not in (_nt or ""), str(_nt))

    print("\n⑤ 形状被改坏时**说人话**（别再糊一句 TypeError 在「游戏没让进」后面）")
    _a = FakeApi()
    N.api = _a
    _stub_walk(ok=True)
    # 模拟"静态门表里谁抄错了一行"：值写成 (图, x) 而不是 (图, (x,y))
    _had = "FarmHouse" in locations.BUILDING_DOORS
    _old = locations.BUILDING_DOORS.get("FarmHouse")
    locations.BUILDING_DOORS["FarmHouse"] = ("Farm", 64)
    try:
        _o5, _w5, _d5, _n5 = N._enter_building_door("FarmHouse")
    finally:
        if _had:
            locations.BUILDING_DOORS["FarmHouse"] = _old
        else:
            locations.BUILDING_DOORS.pop("FarmHouse", None)
    ck("⑤拦下来了（no_door）", _w5 == "no_door", f"{_w5} {_n5}")
    ck("⑤说清了形状不对", "形状" in (_n5 or ""), str(_n5))
    ck("⑤不是 TypeError", "TypeError" not in (_n5 or ""), str(_n5))

    print("\n⑥ 源码钉")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "navigation.py"), encoding="utf-8").read()
    ck("⑥动态兜底写的是 (t[0], (t[1], t[2]))", "door = (t[0], (t[1], t[2]))" in _src,
       "又回成 (t[1], t[2]) 就是原病")
    # ⚠️ 注释里**允许**提旧写法（那是案情记录）；代码行里不许有。
    _code_bad = [ln.strip() for ln in _src.splitlines()
                 if ln.lstrip().startswith("door = (t[1], t[2])")]
    ck("⑥代码里没有旧的坏写法 `door = (t[1], t[2])`", not _code_bad, str(_code_bad[:2]))
    ck("⑥有形状防御（说人话那一句）", "门格表形状不对" in _src)
finally:
    N.api, N._buildings, N._walk_and_wait, N._ai_pos = _BAK
    N.time.sleep = _SLEEP_BAK

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 农舍/小屋/畜棚那族：门格形状对了，`map go 农舍` 会真去推门。")
