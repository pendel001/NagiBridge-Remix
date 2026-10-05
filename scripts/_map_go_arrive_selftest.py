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

    print("\n⑦ M1：`_wait_arrival` **走位失败警报旁路**（只用于失败早退 · 2026-10-05）")
    # 判据出处（游戏侧）：`ModEntry.cs:2294 walk_failed` / `:2281 walk_blocked`；
    # 读法 `ModEntry.cs:8086-8104`（**默认消费**、`:8089 peek=true` 才只读不拿）；
    # 同 type+文案 **4 秒去重** `ModEntry.cs:1182-1184` ⇒ 有可能整段收不到 ⇒ 退回旧行为。
    # ⚠️ 这一批**一行真机都没验**（游戏关着）：下面是桩，钉的是"代码在造出来的回包下怎么走"。
    import time as _time
    import datetime as _dt

    def _utc_iso(offset_s=0.0):
        """造一个跟游戏同款的 `timeUtc`（`DateTime.UtcNow.ToString("O")`，7 位小数秒 + Z）。"""
        _now = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=offset_s)
        return _now.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0000Z"

    class _AlertApi:
        """人**不在**目标格（永远到不了）⇒ 只能靠"图名早退 / 满 timeout / 警报旁路"收工。"""
        def __init__(self, alerts):
            self._alerts = alerts
            self.peeks = []

        def state(self, **kw):
            return {"location": {"name": "Town"}, "player": {"x": 40, "y": 40, "isMoving": False},
                    "time": {}, "inventory": []}

        def alerts(self, peek=False):
            self.peeks.append(peek)
            return {"ok": True, "count": len(self._alerts), "alerts": self._alerts}

    _wa_bak, _wa_api_bak = N._wait_arrival, N.api
    # ⚠️ ⑥ 的 finally 把 `N._walk_and_wait` 还原成了 ④ 留下的**桩**（不是真函数）——
    #    ⑦-7 要端到端跑**真的** `_walk_and_wait`，所以从 `_real`（脚本开头抓的真身）取回来。
    _ww_real = _real["_walk_and_wait"]
    N._walk_and_wait = _ww_real
    try:
        # ⑦-1 **本次之后**的 walk_failed ⇒ 立刻收工（不再干等满 8s，也不等它超时）
        _a1 = _AlertApi([{"type": "walk_failed", "timeUtc": _utc_iso(0),
                          "message": "走不到 Town (68,74)，附近也没有可站格——原地不动"}])
        N.api = _a1
        _o1, _t1 = {}, _time.time()
        _r1 = N._wait_arrival("Town", 68, 74, timeout=8, since=_time.time() - 5, alert_out=_o1)
        _e1 = _time.time() - _t1
        ck("⑦-1 收到**本次之后**的 `walk_failed` ⇒ 立刻 return False（不等满 8s）",
           _r1 is False and _e1 < 3.0, f"r={_r1} 用了 {_e1:.1f}s")
        ck("⑦-1 …把**游戏原话**带回来（供调用方印出来）",
           "原地不动" in (_o1.get("game_alert") or ""), str(_o1))
        ck("⑦-1 …读警报**必须带 `peek=True`**（默认消费会偷走状态条要读的队列）",
           _a1.peeks == [True], str(_a1.peeks))

        # ⑦-2 **本次之前**的旧 walk_failed ⇒ 不受影响（不许假报"没到"）
        _a2 = _AlertApi([{"type": "walk_failed", "timeUtc": _utc_iso(-60),
                          "message": "走不到 Town (1,1)，附近也没有可站格——原地不动"}])
        N.api = _a2
        _o2, _t2 = {}, _time.time()
        _r2 = N._wait_arrival("Town", 68, 74, timeout=2, since=_time.time() - 1, alert_out=_o2)
        _e2 = _time.time() - _t2
        ck("⑦-2 收到**本次之前**的旧 `walk_failed` ⇒ **不受影响**（照旧等满 2s、不写 game_alert）",
           _r2 is False and _e2 >= 1.6 and not _o2, f"r={_r2} 用了 {_e2:.1f}s o={_o2}")

        # ⑦-3 walk_blocked 与 walk_failed 同档（都是游戏权威的"这一步没过去"）
        _a3 = _AlertApi([{"type": "walk_blocked", "timeUtc": _utc_iso(0),
                          "message": "⛔ 走不到 (68,74)：落点和你现在站的不是同一片连通区——这一步没走过去"}])
        N.api = _a3
        _o3, _t3 = {}, _time.time()
        _r3 = N._wait_arrival("Town", 68, 74, timeout=8, since=_time.time() - 5, alert_out=_o3)
        ck("⑦-3 `walk_blocked` 同样早退 False + 带原话",
           _r3 is False and (_time.time() - _t3) < 3.0 and "连通区" in (_o3.get("game_alert") or ""),
           str(_o3))

        # ⑦-4 `walk_completed` **既不算失败、也不许提前判成功**（那是另一回事，本批不做）
        _a4 = _AlertApi([{"type": "walk_completed", "timeUtc": _utc_iso(0),
                          "message": "Arrived at Town (68,74)"}])
        N.api = _a4
        _o4, _t4 = {}, _time.time()
        _r4 = N._wait_arrival("Town", 68, 74, timeout=2, since=_time.time() - 5, alert_out=_o4)
        _e4 = _time.time() - _t4
        ck("⑦-4 收到 `walk_completed` ⇒ 不判失败、也**不提前判成功**（人没站到位就照旧等满）",
           _r4 is False and _e4 >= 1.6 and not _o4, f"r={_r4} 用了 {_e4:.1f}s")

        # ⑦-5 成功判据一个字没放宽：图名对 + ±2 内 + 不在动 ⇒ 照旧 True（队列里有失败警报也不改）
        class _AlertArrivedApi(_AlertApi):
            def state(self, **kw):
                return {"location": {"name": "Town"},
                        "player": {"x": 70, "y": 76, "isMoving": False},   # 差 (2,2) ＝ 恰好 ±2
                        "time": {}, "inventory": []}

        _a5 = _AlertArrivedApi([{"type": "walk_failed", "timeUtc": _utc_iso(0), "message": "走不到…"}])
        N.api = _a5
        _o5 = {}
        _r5 = N._wait_arrival("Town", 68, 74, timeout=5, since=_time.time() - 5, alert_out=_o5)
        ck("⑦-5 图名对上 + ±2 内 + 不在动 ⇒ **照旧 return True**（成功判据一个字没放宽）",
           _r5 is True and not _o5, f"r={_r5} o={_o5}")

        # ⑦-6 没给 `since`（调用方没说是哪一发走位）⇒ **旁路关掉**，一个警报都不许读
        _a6 = _AlertApi([{"type": "walk_failed", "timeUtc": _utc_iso(0), "message": "走不到…"}])
        N.api = _a6
        _t6 = _time.time()
        _r6 = N._wait_arrival("Town", 68, 74, timeout=2, alert_out={})
        _e6 = _time.time() - _t6
        ck("⑦-6 没给 `since` ⇒ **旁路关掉**（不吃无法归因的警报）、照旧等满",
           _r6 is False and _e6 >= 1.6 and not _a6.peeks, f"用了 {_e6:.1f}s peeks={_a6.peeks}")

        # ⑦-7 端到端（真 `_walk_and_wait` + 真 `_wait_arrival`）：失败说明里是**游戏原话**
        class _WalkFailApi(_AlertApi):
            def _post(self, ep, data=None):
                # ⚠️ 假警报必须在**发车之后**才进队列（游戏真实时序就是这样：收到 /walk_to →
                #    update 里判失败 → EnqueueAlert）⇒ 时间戳晚于 `_t_sent`，旁路才认。
                #    在 `_post` 里盖时间戳 = 整个测试里最贴近真机的那一点。
                self._alerts = [{"type": "walk_failed", "timeUtc": _utc_iso(0.05),
                                 "message": "走不到 Town (68,74)，附近也没有可站格——原地不动"}]
                return {"ok": True, "destination": {"x": 68, "y": 74}}

        _a7 = _WalkFailApi([])
        N.api = _a7
        _ok7, _note7 = N._walk_and_wait("Town", 68, 74, timeout=8)
        ck("⑦-7 `_walk_and_wait` 端到端：ok=False 且说明里是**游戏原话**（不再是'走位超时没到'）",
           _ok7 is False and "原地不动" in _note7 and "走位超时没到" not in _note7, _note7)
        ck("⑦-7 …还是 `peek=True`（没被改成消费式）", _a7.peeks == [True], str(_a7.peeks))
    finally:
        N._wait_arrival, N.api = _wa_bak, _wa_api_bak

    # ⑦-8 `walk_to`(坐标) 那条路也把**发车时刻**传下去了（同图坐标走位同样不再白等）
    _wa_bak2, _api_bak2 = N._wait_arrival, N.api
    _seen8 = {}

    class _CoordApi:
        def state(self, **kw):
            return {"location": {"name": "Town"}, "player": {"x": 1, "y": 2},
                    "time": {}, "inventory": []}

        def walk_to_coord(self, loc, x, y):
            return {"ok": True, "destination": {"x": 10, "y": 20}}

    try:
        N.api = _CoordApi()
        N._wait_arrival = (lambda target_loc, target_x, target_y, timeout=30, since=None, alert_out=None:
                           (_seen8.update(loc=target_loc, x=target_x, y=target_y, since=since), True)[1])
        _t8 = _time.time()
        N._walk_to_coord(10, 20)
        ck("⑦-8 `walk_to`(坐标) 也传了 `since`＝**发车时刻**（不是开始等的时刻）",
           isinstance(_seen8.get("since"), float) and _t8 <= _seen8["since"] <= _t8 + 5, str(_seen8))
    finally:
        N._wait_arrival, N.api = _wa_bak2, _api_bak2

    print("\n⑧ M4：`map_go` 农场建筑兜底那条**不再谎报'已到门口'**（2026-10-05）")
    # 位置：`navigation.py` 的 `dest not in MAP_LINKS` → `_resolve_place` 兜底分支（M4）。
    # 旧行为：`_walk_and_wait(loc, x, y, timeout=35)` 的返回值被丢掉 ⇒ 走位超时/失败**照样**回
    #         「🗺️ 已到「X」门口」＝谎报到达。

    class _BarnApi:
        """人在 Farm 农场里朝畜棚门口走；`_get('/farm_buildings')` 给一栋带门格的畜棚。"""
        def state(self, **kw):
            return {"location": {"name": "Farm"}, "player": {"x": 60, "y": 14, "isMoving": False},
                    "time": {}, "inventory": []}

        def _get(self, ep, params=None):
            if ep == "/farm_buildings":
                return {"ok": True, "buildings": [{"type": "Barn", "doorX": 70, "doorY": 15,
                                                   "x": 74, "y": 17}]}
            return {"ok": True, "chests": []}

        def _post(self, ep, data=None):
            return {"ok": True}

        def player_tile(self):
            return (60, 14)

    _ww_bak, _m4_api_bak = N._walk_and_wait, N.api
    try:
        N.api = _BarnApi()
        N._walk_and_wait = lambda loc, x, y, timeout=0: (
            False, "走位失败（Farm 70,15）—— 游戏警报原话：走不到 Farm (70,15)，附近也没有可站格——原地不动")
        _m4 = N.map_go("畜棚")
        ck("⑧-1 走位没到 ⇒ 回包**不许**出现「已到「…」门口」", "已到「" not in _m4, _m4)
        ck("⑧-1 …如实说「没走到」+ 点明人在哪", "没走到" in _m4 and "(60,14)" in _m4, _m4)
        ck("⑧-1 …带上走位那段的原话（游戏警报原文）", "原地不动" in _m4, _m4)
        ck("⑧-1 …并给**下一步**（项目规矩：报错必须给下一步）", "下一步" in _m4, _m4)

        N._walk_and_wait = lambda loc, x, y, timeout=0: (True, "")
        _m4b = N.map_go("畜棚")
        ck("⑧-2 走位到了 ⇒ **照旧**印「🗺️ 已到「畜棚」门口」", "已到「畜棚」门口" in _m4b, _m4b)
    finally:
        N._walk_and_wait, N.api = _ww_bak, _m4_api_bak

    print("\n⑨ 另外两处漏网的「谎报到达」+ 失败说明必须带**这一刻**的落点（2026-10-05）")
    # ⑧ 只收了 `_resolve_place` 兜底那一处。全文件还有两条同族（审计点名）：
    #   · `_minecart_route_go` 的「矿车到站后最后小走到 POI」——`_walk_and_wait` 返回值被丢
    #   · `map_go` 的「从农场室内出屋后走到**本图 POI**」——同上
    # 现在两条都走 `_poi_walk_honest`（同一份判据），没走到就**不许**出现「到达 <目的地>」。

    # ⑨-1 失败说明里的落点必须是**现读**的（真机：以前只印"我们请求的那个格"，读起来像"人在这"）
    _wa_bak3, _api_bak3, _post_bak3 = N._walk_and_wait, N.api, N.api._post
    try:
        class _StillTown:
            def state(self, **kw):
                return {"location": {"name": "Town"}, "player": {"x": 68, "y": 74, "isMoving": False},
                        "time": {}, "inventory": []}

            def _post(self, ep, data=None):
                if ep == "/walk_to":
                    return {"ok": True, "destination": {"x": 3, "y": 93}}
                return {"ok": True}

            def _get(self, ep, params=None):
                return {"ok": True}

        N.api = _StillTown()
        N._walk_and_wait = _real["_walk_and_wait"]         # **真函数**（这一条测的就是它）
        N._wait_arrival = lambda *a, **k: False            # 只把"等"这一步停掉
        _ok9, _note9 = N._walk_and_wait("Town", 3, 93, timeout=1)
        ck("⑨-1 走位失败说明里有「人现在在」+ **现读**的坐标 (68,74)（不是请求格 (3,93)）",
           (not _ok9) and "人现在在" in _note9 and "(68,74)" in _note9, _note9)
        ck("⑨-1 …失败说明里**两种坐标都在**：等的是哪格 `Town 3,93` ＋ 人现在在哪 `Town (68,74)`（后者现读）",
           "人现在在 Town (68,74)" in _note9 and "Town 3,93" in _note9, _note9)
    finally:
        N._walk_and_wait, N.api = _wa_bak3, _api_bak3
        N.api._post = _post_bak3

    # ⑨-2 矿车到站后的"最后小走到 POI"：没走到 ⇒ 不许回「到达 <目的地>」
    _mcgo_bak = getattr(N, "_minecart_go", None)
    try:
        N._minecart_go = lambda *a, **k: (True, "🚂 矿车站→镇矿车站 → Town")
        N._walk_and_wait = _timeout
        _mc = N._minecart_route_go(None, "矿车站", {"map": "Town"}, "镇矿车站",
                                   "Town", "镇鲶鱼钓点", "镇鲶鱼钓点")
        ck("⑨-2 矿车到站、最后那段没走到 ⇒ 回包**不许**出现「到达 镇鲶鱼钓点」",
           "到达 镇鲶鱼钓点" not in _mc, _mc)
        ck("⑨-2 …如实说「还没走到」+ 人还在半路", "还没走到" in _mc and "半路" in _mc, _mc)
        N._walk_and_wait = lambda loc, x, y, timeout=0: (True, "")
        _mc2 = N._minecart_route_go(None, "矿车站", {"map": "Town"}, "镇矿车站",
                                    "Town", "镇鲶鱼钓点", "镇鲶鱼钓点")
        ck("⑨-2 …走到了 ⇒ 照旧印「到达 镇鲶鱼钓点（(3, 93)）」", "到达 镇鲶鱼钓点" in _mc2, _mc2)
    finally:
        if _mcgo_bak is not None:
            N._minecart_go = _mcgo_bak
        N._walk_and_wait = _timeout

    # ⑨-3 从农场室内出屋后走到**本图 POI**：没走到 ⇒ 不许回「到达 <POI>」
    class _CabinApi:
        """人在自家小屋里；`_exit_farm_building` 一叫就"出屋"（当前图翻成 Farm）。"""
        def __init__(self):
            self.loc = "Cabin"

        def state(self, **kw):
            return {"location": {"name": self.loc}, "player": {"x": 6, "y": 6, "isMoving": False},
                    "time": {}, "inventory": []}

        def _post(self, ep, data=None):
            if ep == "/walk_to":
                return {"ok": True, "destination": {"x": 8, "y": 8}}
            return {"ok": True}

        def _get(self, ep, params=None):
            return {"ok": True}

    _exit_bak, _api_bak4 = N._exit_farm_building, N.api
    _fa = _CabinApi()
    try:
        N.api = _fa
        N._exit_farm_building = lambda cur, dest: (setattr(_fa, "loc", "Farm"), True)[1]
        N._walk_and_wait = _timeout
        _po = N.map_go("农场洞穴(外)")
        ck("⑨-3 出屋了、POI 没走到 ⇒ 回包**不许**出现「到达 农场洞穴(外)」",
           "到达 农场洞穴(外)" not in _po, _po)
        ck("⑨-3 …如实说「还没走到」", "还没走到" in _po, _po)
        ck("⑨-3 …⛔ 也没设站位/朝向（离得远时设朝向是假的）", "[站位朝向]" not in _po, _po)
        _fa.loc = "Cabin"          # ⚠️ 上一次调用已经把"出屋"翻成 Farm 了 —— 放回室内，这条才走**出屋分支**
        N._walk_and_wait = lambda loc, x, y, timeout=0: (True, "")
        _po2 = N.map_go("农场洞穴(外)")
        ck("⑨-3 …走到了 ⇒ 照旧印「到达 农场洞穴(外)（(34, 7)）」+ 站位朝向",
           "到达 农场洞穴(外)" in _po2 and "(34, 7)" in _po2 and "[站位朝向]" in _po2, _po2)
    finally:
        N._exit_farm_building, N.api = _exit_bak, _api_bak4
        N._walk_and_wait = _timeout

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(N, k, v)
sys.exit(1 if FAIL else 0)
