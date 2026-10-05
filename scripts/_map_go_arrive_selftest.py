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

    print("\n④ 门那条路（恒 2026-10-05：「**根本没走到博物馆门口推门就直接进来**」）")
    # 人在 Town ⇒ 直接点真目标 `ArchaeologyHouse`（MAP_LINKS 里 Town→它 就是 door 那一步），
    # 再把 `_enter_building_door` 换成"造出来的失败形状"，看门分支怎么报、有没有 warp。
    _ebd_bak = N._enter_building_door
    _warp_bak = getattr(N.api, "warp", None)
    warps = []
    N.api.warp = lambda *a, **k: (warps.append(a), {"ok": True})[1]
    try:
        N._walk_and_wait = lambda loc, x, y, timeout=0: (True, "")
        for _why, _tile, _want in (
                ("walk_failed", (101, 89), "没走到"),
                ("other_map", (101, 89), "不在这张图"),
                ("no_door", None, "查不到"),
                ("pushed_no_effect", (101, 89), "推了门")):
            warps.clear()
            N._enter_building_door = (lambda _w, _t: (lambda loc: (False, _w, _t, "自验假细节")))(_why, _tile)
            _out = N.map_go("ArchaeologyHouse")
            ck(f"门失败 why={_why} ⇒ **一次 warp 都没打**（旧版这里瞬移穿墙进屋）",
               not warps, str(warps))
            ck(f"门失败 why={_why} ⇒ 如实报「{_want}」", _want in _out, _out)
            ck(f"门失败 why={_why} ⇒ 带上走位原话（旧版把这句话丢了，只剩「推门没成」）",
               "走位原话" in _out, _out)
        warps.clear()
        N._enter_building_door = lambda loc: (True, "", (101, 89), "")
        _out = N.map_go("ArchaeologyHouse")
        ck("门成功 ⇒ 日志点明「走到门格 (101, 89) 推门进屋」（恒靠这句分'推门'还是'瞬移'）",
           "走到门格 (101, 89)" in _out and "推门进屋" in _out, _out)
        ck("门成功 ⇒ 也没有 warp", not warps, str(warps))
    finally:
        N._enter_building_door = _ebd_bak
        if _warp_bak is None:
            try:
                del N.api.warp
            except Exception:
                pass
        else:
            N.api.warp = _warp_bak

    print("\n⑤ 源码钉：门那两处再也不许出现 `api.warp`（防回归）")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "navigation.py"),
                encoding="utf-8").read()
    _ia = _src.index("def _enter_building_door(")
    _ib = _src.index("\ndef ", _ia + 10)
    ck("_enter_building_door 自己一次 warp 都不打", "api.warp(" not in _src[_ia:_ib],
       _src[_ia:_ib][-160:])
    _ja = _src.index('        elif kind == "door":')
    _jb = _src.index('        elif kind == "portal":', _ja)
    ck("map_go 的 door 分支里没有 `api.warp(`", "api.warp(" not in _src[_ja:_jb],
       _src[_ja:_jb][-160:])

    print("\n⑥ 门那条的「走位超时／人还在走」档（恒：「**还在走就提前兜底**」）")
    # 这一档**不 stub** `_enter_building_door` —— 直接用真函数 + 造出来的走位回包，
    # 钉住"超时那一刻先判人还在不在动，还在动就照同图 POI 先例再补一段"。
    _walk_bak, _api_bak = N._walk_and_wait, N.api

    class _DoorApi:
        """人在 Town 往博物馆门口走；`push_works` = 推门真能进屋。"""
        def __init__(self, x, y, moving, push_works=True):
            self.x, self.y, self.moving = x, y, moving
            self.push_works, self.entered = push_works, False
            self.warps, self.interacts = [], []

        def state(self, **kw):
            return {"location": {"name": "ArchaeologyHouse" if self.entered else "Town"},
                    "player": {"x": self.x, "y": self.y, "isMoving": self.moving},
                    "time": {}, "inventory": []}

        def _get(self, ep):
            return {"ok": True, "chests": [], "buildings": []}

        def _post(self, ep, data=None):
            return {"ok": True}

        def interact_at(self, x, y):
            self.interacts.append((x, y))
            if self.push_works:
                self.entered = True
            return {"ok": True}

        def warp(self, *a, **k):
            self.warps.append(a)
            return {"ok": True}

        def player_tile(self):
            return (self.x, self.y)

    def _stub_walks(seq):
        """按 `seq` 顺序回包（每项 `(ok, note)`），同时把每次的 timeout 记下来。"""
        calls = []

        def _f(loc, x, y, timeout=0):
            calls.append(timeout)
            i = len(calls) - 1
            return seq[i] if i < len(seq) else (False, f"走位超时没到（{loc} {x},{y}）")
        return calls, _f

    try:
        # ⑥-1 超时那刻人**还在动** ⇒ 续一段（25 → 30）；续走成了 ⇒ 照旧推门进屋
        _d1 = _DoorApi(96, 89, True)
        N.api = _d1
        _w1, N._walk_and_wait = _stub_walks([(False, "走位超时没到（Town 101,89）"), (True, "")])
        _r1 = N._enter_building_door("ArchaeologyHouse")
        ck("⑥-1 超时但仍在动 ⇒ **续了一段 30s**（不是一次就判死）", _w1 == [25, 30], str(_w1))
        ck("⑥-1 …续走成了 ⇒ 真推门进屋（ok=True）", _r1[0] is True, str(_r1))
        ck("⑥-1 …走位原话里点明「还在动」（恒要的分档）", "还在动" in (_r1[3] or ""), str(_r1[3]))
        ck("⑥-1 …一次 warp 都没打", not _d1.warps, str(_d1.warps))

        # ⑥-2 超时那刻人**已停**（坐标没变、moving=False）⇒ 不许续走，如实判 walk_failed
        _d2 = _DoorApi(60, 74, False)
        N.api = _d2
        _w2, N._walk_and_wait = _stub_walks([(False, "走位超时没到（Town 101,89）")])
        _r2 = N._enter_building_door("ArchaeologyHouse")
        ck("⑥-2 人已停（坐标没变、moving=False）⇒ **只有一次走位**，不续走", _w2 == [25], str(_w2))
        ck("⑥-2 …如实判 `walk_failed`（不是「推门没成」）", _r2[1] == "walk_failed", str(_r2))
        ck("⑥-2 …原话点明「已停」+ 人当时在哪",
           "已停" in (_r2[3] or "") and "(60, 74)" in (_r2[3] or ""), str(_r2[3]))
        ck("⑥-2 …人停在远处时**不做 4×10s 的退邻格打转**（不再白花 40 秒）",
           10 not in _w2, str(_w2))
        ck("⑥-2 …一次 warp 都没打", not _d2.warps, str(_d2.warps))

        # ⑥-3 两段都没成 ⇒ 最多两段，仍不许 warp
        _d3 = _DoorApi(80, 74, True)
        N.api = _d3
        _w3, N._walk_and_wait = _stub_walks([(False, "走位超时没到（Town 101,89）"),
                                             (False, "走位超时没到（Town 101,89）")])
        _r3 = N._enter_building_door("ArchaeologyHouse")
        ck("⑥-3 两段都没成 ⇒ **最多两段**（`[25, 30]`，没有第三段续走）", _w3 == [25, 30], str(_w3))
        ck("⑥-3 …如实判失败", _r3[0] is False and _r3[1] == "walk_failed", str(_r3))
        ck("⑥-3 …仍**一次 warp 都没打**", not _d3.warps, str(_d3.warps))

        # ⑥-4 全路径（真 `_enter_building_door` + `map_go`）：走位没到 ⇒ 如实说"没走到"
        _d4 = _DoorApi(60, 74, False)
        N.api = _d4
        _w4, N._walk_and_wait = _stub_walks([(False, "走位超时没到（Town 101,89）")])
        _o4 = N.map_go("ArchaeologyHouse")
        ck("⑥-4 map_go：走位没到 ⇒ 回包如实说「没走到」（不再谎报进屋）", "没走到" in _o4, _o4)
        ck("⑥-4 map_go：带上**走位原话**（恒靠这句分'推门'还是'瞬移'）", "走位原话" in _o4, _o4)
        ck("⑥-4 map_go：**一次 warp 都没打**（旧版这里瞬移穿墙进屋）", not _d4.warps, str(_d4.warps))
        ck("⑥-4 map_go：没说「推门进屋」（那是推成了才配说的话）", "推门进屋" not in _o4, _o4)
    finally:
        N._walk_and_wait, N.api = _walk_bak, _api_bak

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(N, k, v)
sys.exit(1 if FAIL else 0)
