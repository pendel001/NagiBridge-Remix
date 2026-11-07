# -*- coding: utf-8 -*-
"""🌙 **只有「AI 自己已经睡上了还再按一次 sleep」才拦** —— 纯 Python 自验。

历史两拍（都是恒的真机原话）：
  · 2026-11-07「**ai 反复过夜 sleep 导致我这边结算卡死了。不把他踢出去今天的存档就存不了了。
    我很无语**」⇒ 第一版闸写成「**任一端**开着结算/存盘/就绪屏就禁止 sleep」。
  · 2026-11-08「**你这个睡觉门禁把 ai 后睡、user 先睡的情况给拦截了**（……）结算界面、技能升级
    界面等等睡后界面能抓到吗？能的话在那里拦反复睡觉比较好。**上次就是 ai 在技能升级菜单
    强行反复睡觉搞卡的**」⇒ 第一版**误伤**：房主先睡（他那端结算屏开着）时，AI 连正常去睡都被拦。

⇒ 现在钉的语义（恒 2026-11-08 拍板）：
  ① **AI 自己那一端**已经是「睡中/睡后」⇒ 拦，且**一个写请求都不发**。睡后状态集合（真机实测，
     字段全部来自 `/state`，见 `stardew_api._OVERNIGHT_MENUS` 上方那段账）：
       · `activeMenu.type` = `LevelUpMenu`（技能升级，**上次卡死就是它**）/ `ShippingMenu`（出货结算）
         / `SaveGameMenu`（存盘）/ `ReadyCheckDialog`（就绪屏；`readyCheck.isSleep=false` 的
         **节日入场**就绪不算）；
       · `player.sleepReady.ready=True`（就绪已登记 ⇒ 过夜在等，再按就是「反复按」）；
       · `player.isInBed=True` **且** `time.timeOfDay<800`（清早刚在新一天里从床上醒来）。
     真机逮到的时钟证据：`dayOfMonth 16→17` 且 `timeOfDay 2450→600`（过夜真的过去了）。
  ② **房主先睡 ⇒ 放行**（本次修的回归点，单独钉死）：他那端开着结算/存盘/就绪屏**都不拦**，
     最多在回执末尾附一句「玩家正在结算，你睡下去要等他」。
  ③ 量不到（端口不通 / 老 DLL 没这个字段 / `/state` 形状不对）⇒ **不许把人锁死**。
  ④ 文案：说清「谁那一端 + 什么界面 + 现在该干什么」，中性词「玩家」、不写人名、不夹历史账、
     **不谎报「已上床/已就绪」**、一句话读完（≤60 字）。
  ⑤ 拦在**走位之前**（`_aim_sleep_home` / `_resolve_target_bed` 都是副作用），拦时**零副作用**。
"""
import ast
import contextlib
import os
import sys
import time as _time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import stardew_api as api
import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class _Clock:
    """假时钟：`sleep` 立即返回、`time()` 每次前进 3s ⇒ `wait_night()` 的超时循环秒过。"""

    def __init__(self):
        self.t = 1000.0

    def time(self):
        self.t += 3.0
        return self.t

    def sleep(self, _s):
        pass


@contextlib.contextmanager
def fast_clock():
    """把 `time.sleep`/`time.time` 换成假时钟（只在 `go_sleep_flow` 那几条真流程用）。"""
    _bak = (_time.sleep, _time.time)
    _c = _Clock()
    _time.sleep, _time.time = _c.sleep, _c.time
    try:
        yield _c
    finally:
        _time.sleep, _time.time = _bak


def _st(name, menu="", in_bed=False, ready=False, tod=2200, ready_check=None, levelup=None):
    """造一份 `/state`（只放我们真正读的那几个键，形状跟真机 2026-11-08 读到的一致）。"""
    am = None
    if menu or (ready_check is not None) or (levelup is not None):
        am = {"type": menu, "readyCheck": ready_check, "levelUp": levelup}
    return {
        "ok": True,
        "worldReady": True,
        "player": {"name": name, "isInBed": in_bed,
                   "sleepReady": {"ready": ready,
                                  "state": "Ready" if ready else "NotReady",
                                  "numberReady": 2 if ready else 0,
                                  "numberRequired": 2, "allReady": False}},
        "activeMenu": am,
        "location": {"name": "FarmHouse", "uniqueName": "FarmHouse"},
        "time": {"season": "spring", "dayOfMonth": 17, "timeOfDay": tod},
    }


class Fake:
    """两端的 `/state`（AI=7843 / 房主=7842）+ 一份「发出去的写请求」账。"""

    def __init__(self, ai_menu="", host_menu="", ai_name="Claude", host_name="恒",
                 host_dead=False, ai_dead=False,
                 ai_in_bed=False, ai_ready=False, ai_tod=2200,
                 host_in_bed=False, host_ready=False, host_tod=2200,
                 ai_rc=None, host_rc=None, ai_levelup=None,
                 ready_dialog_on_sleep=True):
        self.ai_st = _st(ai_name, ai_menu, ai_in_bed, ai_ready, ai_tod, ai_rc, ai_levelup)
        self.host_st = _st(host_name, host_menu, host_in_bed, host_ready, host_tod, host_rc)
        self.host_dead, self.ai_dead = host_dead, ai_dead
        self.ready_dialog_on_sleep = ready_dialog_on_sleep
        self.posts = []

    # ── 装到 stardew_api 上 ──
    def install(self):
        def _ai_get(ep, params=None):
            if self.ai_dead:
                raise RuntimeError("AI 端读不到")
            return self.ai_st

        def _host_state():
            if self.host_dead:
                raise RuntimeError("host 读不到")
            return self.host_st

        def _ai_post(ep, data=None):
            self.posts.append((ep, data))
            act = (data or {}).get("action") if isinstance(data, dict) else None
            if ep == "/crawl_bed" and act == "locate":
                # 真机：`/crawl_bed action=locate` 顺带报 isInBed
                return {"ok": True, "isInBed": self.ai_st["player"]["isInBed"]}
            if ep == "/crawl_bed" and act == "sleep":
                self.ai_st["player"]["isInBed"] = True
            if ep == "/sleep" and self.ready_dialog_on_sleep:
                # 真机：按下 sleep ⇒ 人登记就绪、就绪屏弹出（下一拍 /state 才看得到）
                self.ai_st["player"]["sleepReady"]["ready"] = True
                self.ai_st["player"]["sleepReady"]["state"] = "Ready"
                self.ai_st["activeMenu"] = {"type": "ReadyCheckDialog",
                                            "readyCheck": {"isSleep": True}, "levelUp": None}
            return {"ok": True}

        api._ai_get = _ai_get
        api.host_state = _host_state
        api._ai_post = _ai_post
        api._post = _ai_post          # 走 `_post` 的那几条（如真 `_resolve_target_bed`）也记账
        api.detect_roles = lambda *a, **k: {"ok": True}
        return self


def fake_bed(who=""):
    """`_resolve_target_bed` 的替身（真流程只验「闸开没开」，床本身不是重点）。"""
    return {"ok": True, "island": False, "loc": "FarmHouse", "bx": 5, "by": 8, "tiles": [],
            "p1": "恒", "p2": "Claude", "co_sleep": False,
            "locate": {"curLoc": "FarmHouse"}, "note": "床 FarmHouse(5,8)",
            "crawl_kw": {"player": who}}


@contextlib.contextmanager
def flow_env(fake):
    """装 Fake + 假床 + 假时钟，跑完还原（真流程那几条用）。"""
    _bak_bed = api._resolve_target_bed
    api._resolve_target_bed = fake_bed
    fake.install()
    try:
        with fast_clock():
            yield fake
    finally:
        api._resolve_target_bed = _bak_bed


_WRITES = ("/walk_to", "/position", "/crawl_bed", "/sleep", "/warp", "/cancel_sleep")


try:
    # ═══════════════════════════════════════════════════════════════
    print("\n① AI 自己那一端已经是睡中/睡后 ⇒ 拦（判据表）")
    # ═══════════════════════════════════════════════════════════════
    for _label, _kw, _exp_busy, _exp_why in (
            ("干净（无菜单/没就绪/半夜没躺）", dict(), False, ""),
            ("自己开着 技能升级 LevelUpMenu", dict(ai_menu="LevelUpMenu"), True, "LevelUpMenu"),
            ("自己开着 出货结算 ShippingMenu", dict(ai_menu="ShippingMenu"), True, "ShippingMenu"),
            ("自己开着 存盘 SaveGameMenu", dict(ai_menu="SaveGameMenu"), True, "SaveGameMenu"),
            ("自己开着 睡觉就绪屏 ReadyCheckDialog",
             dict(ai_menu="ReadyCheckDialog", ai_rc={"isSleep": True}), True, "ReadyCheckDialog"),
            ("自己就绪屏弹着但没报 isSleep", dict(ai_menu="ReadyCheckDialog"), True, "ReadyCheckDialog"),
            ("类型名没报、只有 activeMenu.levelUp 有货也认得",
             dict(ai_levelup={"currentSkill": 0, "currentLevel": 10}), True, "LevelUpMenu"),
            ("真机形状：type=LevelUpMenu 且 levelUp 有货（钓鱼 Lv10 那个现场）",
             dict(ai_menu="LevelUpMenu",
                  ai_levelup={"currentSkill": 1, "currentLevel": 10}), True, "LevelUpMenu"),
            ("自己开着 普通背包 InventoryPage", dict(ai_menu="InventoryPage"), False, ""),
            ("自己开着 节日入场就绪屏（isSleep=false）",
             dict(ai_menu="ReadyCheckDialog", ai_rc={"isSleep": False}), False, ""),
            ("自己开着 别的菜单 JunimoNoteMenu", dict(ai_menu="JunimoNoteMenu"), False, ""),
    ):
        _f = Fake(**_kw).install()
        _r = api.overnight_busy()
        ck(f"①{_label} ⇒ busy={_exp_busy}" + (f" why={_exp_why}" if _exp_why else ""),
           _r["busy"] is _exp_busy and _r["why"] == _exp_why,
           f"实际 busy={_r['busy']} why={_r['why']!r} msg={_r['msg']!r}")
        if _exp_busy:
            ck(f"①{_label} ⇒ who 只认自己那端（ai）", _r["who"] == "ai", _r["who"])
            ck(f"①{_label} ⇒ 有能直接甩给 AI 的一句话", bool(_r["msg"]), _r["msg"])

    print("\n①' 自己这端优先于房主那端（两边都脏 ⇒ 先说自己）")
    Fake(ai_menu="LevelUpMenu", host_menu="ShippingMenu").install()
    _r = api.overnight_busy()
    ck("①'两边都有睡后界面 ⇒ why 说自己的 LevelUpMenu（先说自己）",
       _r["busy"] is True and _r["why"] == "LevelUpMenu", f"{_r['why']!r} {_r['msg']!r}")

    # ═══════════════════════════════════════════════════════════════
    print("\n② **房主先睡 ⇒ 放行**（本次修的回归点：AI 后睡不许再被误伤）")
    # ═══════════════════════════════════════════════════════════════
    for _hm, _label in (("ShippingMenu", "出货结算"),
                        ("SaveGameMenu", "存盘"),
                        ("ReadyCheckDialog", "睡觉就绪屏"),
                        ("LevelUpMenu", "技能升级")):
        Fake(host_menu=_hm, host_rc={"isSleep": True}).install()
        _r = api.overnight_busy()
        ck(f"②房主开着{_label}（{_hm}）、AI 自己干净 ⇒ **放行**（busy=False）",
           _r["busy"] is False and _r["who"] == "",
           f"busy={_r['busy']} who={_r['who']!r} {_r['msg']!r}")
        ck(f"②房主开着{_label} ⇒ 回执里有句「玩家…要等他」的提示",
           bool(_r["note"]) and "玩家" in _r["note"] and "等" in _r["note"], _r["note"])

    Fake(host_menu="", host_ready=True, host_in_bed=True).install()
    _r = api.overnight_busy()
    ck("②房主已登记就绪（在等你一起过夜）⇒ 也不拦，只出提示",
       _r["busy"] is False and "玩家" in _r["note"] and "就绪" in _r["note"], _r["note"])
    Fake(host_menu="ReadyCheckDialog", host_rc={"isSleep": False}).install()
    ck("②房主的**节日入场**就绪屏 ⇒ 不拦、也不乱出睡眠提示",
       api.overnight_busy()["note"] == "", api.overnight_busy()["note"])

    print("\n②' 真流程：房主在结算，AI 照睡（不是「只改返回值」）")
    with flow_env(Fake(host_menu="ShippingMenu", host_rc={"isSleep": True})) as _f:
        _r = api.go_sleep_flow("恒")
        _eps = [e for (e, _d) in _f.posts]
        ck("②'房主结算中 ⇒ 流程**真的走下去**（walk_to/crawl_bed/sleep 都有写请求）",
           ("/walk_to" in _eps) and ("/crawl_bed" in _eps) and ("/sleep" in _eps), str(_eps))
        ck("②'没被 gate 挡（steps 里没有失败的那步 gate）",
           not any(s["step"] == "gate" and not s["ok"] for s in (_r.get("steps") or [])),
           str(_r.get("steps")))
        ck("②'成功回执**末尾附了那句「玩家…等你」**",
           (_r.get("ok") is True) and ("（玩家" in str(_r.get("summary"))), str(_r.get("summary")))

    # ═══════════════════════════════════════════════════════════════
    print("\n③ AI 今天已经睡过 / 正睡 ⇒ 拦")
    # ═══════════════════════════════════════════════════════════════
    Fake(ai_ready=True, ai_in_bed=True, ai_tod=2450).install()
    _r = api.overnight_busy()
    ck("③自己 sleepReady.ready=True（就绪已登记、过夜在等）⇒ 拦",
       _r["busy"] is True and _r["why"] == "ready", f"{_r['why']!r} {_r['msg']!r}")
    Fake(ai_in_bed=True, ai_tod=600).install()
    _r = api.overnight_busy()
    ck("③自己 isInBed **且** 清早 06:00（刚在新一天醒来）⇒ 拦",
       _r["busy"] is True and _r["why"] == "morning", f"{_r['why']!r} {_r['msg']!r}")
    ck("③那句里报的是真时间（06:00，不是编的）", "06:00" in _r["msg"], _r["msg"])
    Fake(ai_in_bed=True, ai_tod=2450).install()
    ck("③半夜站在床格里、但没就绪 ⇒ **不拦**（isInBed 单用会误伤）",
       api.overnight_busy()["busy"] is False, api.overnight_busy()["msg"])
    Fake(ai_in_bed=True, ai_tod=1400).install()
    ck("③白天路过床格（isInBed 但 14:00）⇒ **不拦**", api.overnight_busy()["busy"] is False)
    _f = Fake(ai_tod=None).install()
    _f.ai_st["time"].pop("timeOfDay", None)
    ck("③时钟读不到（没有 timeOfDay）⇒ 这条判据跳过、不锁死",
       api.overnight_busy()["busy"] is False)

    # ═══════════════════════════════════════════════════════════════
    print("\n④ 量不到 ⇒ **不许把人锁死**（睡眠是刚需）")
    # ═══════════════════════════════════════════════════════════════
    Fake(host_dead=True).install()
    ck("④房主读不到、自己这头干净 ⇒ 放行", api.overnight_busy()["busy"] is False)
    Fake(host_dead=True, ai_menu="LevelUpMenu").install()
    _r = api.overnight_busy()
    ck("④房主读不到、自己开着技能升级 ⇒ 照样拦（不因房主读不到而漏）",
       _r["busy"] is True and _r["why"] == "LevelUpMenu", str(_r))
    Fake(ai_dead=True, host_menu="ShippingMenu").install()
    ck("④自己读不到、房主在结算 ⇒ 放行（读不到就不拦自己那头）",
       api.overnight_busy()["busy"] is False)
    Fake(ai_dead=True, host_dead=True).install()
    ck("④两端都读不到 ⇒ 放行", api.overnight_busy()["busy"] is False)
    _f = Fake().install()
    _f.ai_st = {"ok": True}
    ck("④`/state` 形状不对（没有 player 段）⇒ 放行", api.overnight_busy()["busy"] is False)
    _f.ai_st = {"player": {"sleepReady": "Ready"}}
    ck("④`sleepReady` 形状不对 ⇒ 放行（不瞎猜成已就绪）", api.overnight_busy()["busy"] is False)

    # ═══════════════════════════════════════════════════════════════
    print("\n⑤ 文案质检（每一句拦截话都必须过）")
    # ═══════════════════════════════════════════════════════════════
    _msgs, _notes = [], []
    for _kw in (dict(ai_menu="LevelUpMenu"), dict(ai_menu="ShippingMenu"),
                dict(ai_menu="SaveGameMenu"), dict(ai_menu="ReadyCheckDialog"),
                dict(ai_ready=True), dict(ai_in_bed=True, ai_tod=600)):
        Fake(**_kw).install()
        _msgs.append((_kw, api.overnight_busy()["msg"]))
    for _kw in (dict(host_menu="ShippingMenu"), dict(host_menu="SaveGameMenu"),
                dict(host_menu="ReadyCheckDialog", host_rc={"isSleep": True}),
                dict(host_menu="LevelUpMenu"), dict(host_ready=True)):
        Fake(**_kw).install()
        _notes.append((_kw, api.overnight_busy()["note"]))

    for _kw, _m in _msgs:
        _tag = ",".join(_kw) or "-"
        ck(f"⑤[{_tag}] 用中性词「玩家」而不是人名", "玩家" in _m, _m)
        ck(f"⑤[{_tag}] 不写死任何角色名（恒/Claude 都不许出现）",
           not any(n in _m for n in ("恒", "Claude")), _m)
        ck(f"⑤[{_tag}] 不夹历史账（不许出现 2026）", "2026" not in _m, _m)
        ck(f"⑤[{_tag}] **不谎报已上床/已就绪**",
           not any(w in _m for w in ("已上床", "已就绪")), _m)
        ck(f"⑤[{_tag}] 一句话读完（≤60 字）", len(_m) <= 60, f"{len(_m)} 字：{_m}")
        ck(f"⑤[{_tag}] 说清**哪一端**（你这边/你）", ("你这边" in _m or "你" in _m), _m)
        ck(f"⑤[{_tag}] 说清**现在干什么**（含动作词）",
           any(w in _m for w in ("先", "等", "别再", "别急")), _m)

    for _kw, _n in _notes:
        _tag = ",".join(_kw) or "-"
        ck(f"⑤note[{_tag}] 提玩家时用「玩家」、不写人名",
           "玩家" in _n and not any(n in _n for n in ("恒", "Claude")), _n)
        ck(f"⑤note[{_tag}] ≤60 字、不夹 2026", len(_n) <= 60 and "2026" not in _n, _n)

    # ═══════════════════════════════════════════════════════════════
    print("\n⑥ 拦的时候 **零副作用**（go_sleep_flow：一步不走、一次不摸）")
    # ═══════════════════════════════════════════════════════════════
    with flow_env(Fake(ai_menu="LevelUpMenu")) as _f:
        _r = api.go_sleep_flow("恒")
        ck("⑥返回 ok=False", _r.get("ok") is False, str(_r.get("ok")))
        ck("⑥summary 就是那句拦截话（说技能升级界面）",
           "技能升级" in str(_r.get("summary", "")), str(_r.get("summary")))
        ck("⑥**一个写请求都没发**（walk_to/position/crawl_bed/sleep/warp 全 0）",
           _f.posts == [], str(_f.posts))
        ck("⑥账上记了 gate 这一步（ok=False）",
           any(s.get("step") == "gate" and s.get("ok") is False
               for s in (_r.get("steps") or [])), str(_r.get("steps")))
        ck("⑥summary 里**没有**任何「已上床/已就绪」的假成功",
           not any(w in str(_r.get("summary")) for w in ("已上床", "已就绪")), str(_r.get("summary")))
    with flow_env(Fake(ai_in_bed=True, ai_tod=600)) as _f:
        _r = api.go_sleep_flow("恒")
        ck("⑥「刚睡醒」那条也零副作用（一个写请求都没发）",
           [e for (e, _d) in _f.posts if e in _WRITES] == [], str(_f.posts))
        ck("⑥「刚睡醒」那条 ok=False 且说的是「新的一天」",
           _r.get("ok") is False and "新的一天" in str(_r.get("summary")), str(_r.get("summary")))

    # ⚠️ 这条**不给 `_resolve_target_bed` 打桩**：真那家伙一跑就会发 `/crawl_bed locate`。
    #    闸只要被挪到"找床之后"，这里立刻见红（刀 3 就是靠它）。
    _f = Fake(ai_menu="LevelUpMenu").install()
    _bak_bed = api._resolve_target_bed
    try:
        with fast_clock():
            _r = api.go_sleep_flow("恒")
    finally:
        api._resolve_target_bed = _bak_bed
    ck("⑥不打桩也一样零写请求（真 `_resolve_target_bed` 一步都没跑）",
       _f.posts == [], str(_f.posts))
    ck("⑥不打桩时 summary 仍是那句拦截话",
       "技能升级" in str(_r.get("summary")), str(_r.get("summary")))

    # ═══════════════════════════════════════════════════════════════
    print("\n⑦ MCP 那层：拦在 **走位之前**（`_aim_sleep_home` 一下都不该跑）")
    # ═══════════════════════════════════════════════════════════════
    _bak = (M.api, M._with_state, M._aim_sleep_home)
    M._with_state = lambda s: s
    _aimed, _flowed = [], []

    class _MCPApi:
        ensure_roles = staticmethod(lambda *a, **k: {"ok": True})
        overnight_busy = staticmethod(lambda: api.overnight_busy())
        go_sleep_flow = staticmethod(lambda *a, **k: (_flowed.append(a) or
                                                      {"ok": True, "summary": "💤 X"}))

    try:
        M.api = _MCPApi
        M._aim_sleep_home = lambda who: (_aimed.append(who) or "")
        Fake(ai_menu="LevelUpMenu").install()
        _out = M.go_sleep("恒")
        ck("⑦自己开着技能升级 ⇒ 回了拦截话", "技能升级" in str(_out), str(_out))
        ck("⑦**没有走位**（_aim_sleep_home 没被调用）", _aimed == [], str(_aimed))
        ck("⑦**也没有进睡觉流程**", _flowed == [], str(_flowed))

        Fake(ai_menu="ShippingMenu").install()
        _aimed.clear()
        _out = M.go_sleep("恒")
        ck("⑦自己开着结算 ⇒ 同样拦在走位之前", _aimed == [] and "结算" in str(_out), str(_out))

        print("\n⑦' 房主在结算时 MCP **不拦**（回归点第二处）")
        Fake(host_menu="ShippingMenu").install()
        _aimed.clear()
        _out = M.go_sleep("恒")
        ck("⑦'房主结算中 ⇒ 走位被调用了", _aimed == ["恒"], str(_aimed))
        ck("⑦'流程也进去了", _flowed != [], str(_flowed))
        ck("⑦'回执里带一句「玩家…等你」（不吞掉 note）",
           "玩家" in str(_out) and "等" in str(_out), str(_out))

        print("\n⑦'' 自己干净时 MCP 照常走位（闸不是「永远拦」）")
        Fake().install()
        _aimed.clear()
        M.go_sleep("恒")
        ck("⑦''走位被调用了", _aimed == ["恒"], str(_aimed))
    finally:
        M.api, M._with_state, M._aim_sleep_home = _bak

    # ═══════════════════════════════════════════════════════════════
    print("\n⑧ 源码钉（判据集合 / 位置顺序 / 零写请求 / 房主不再拦）")
    # ═══════════════════════════════════════════════════════════════
    # ⚠️ 顺序钉**必须走 AST**：docstring/注释里也写着 `overnight_busy()`、`_aim_sleep_home`
    #    ⇒ 按纯文本 `find()` 会被注释骗过（第一版就这么漏了，刀 3 当场没红）。
    _here = os.path.dirname(os.path.abspath(__file__))
    _src = open(os.path.join(_here, "stardew_api.py"), encoding="utf-8").read()
    _msrc = open(os.path.join(_here, "nagi_mcp_server.py"), encoding="utf-8").read()
    _oms = _src.split("_OVERNIGHT_MENUS = ")[1][:120]
    for _n in ("LevelUpMenu", "ShippingMenu", "SaveGameMenu", "ReadyCheckDialog"):
        ck(f"⑧`_OVERNIGHT_MENUS` 里有 {_n}", _n in _oms, _oms)

    def _funcs(src):
        return {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)}

    def _line(fn, pred):
        """函数体里第一条满足 pred 的**真调用**的行号（注释/docstring 不算）；没有 ⇒ -1。"""
        ls = [c.lineno for c in ast.walk(fn) if isinstance(c, ast.Call) and pred(c)]
        return min(ls) if ls else -1

    def _by_name(name):
        return lambda c: (getattr(c.func, "id", None) or getattr(c.func, "attr", None)) == name

    def _post_to(ep):
        return lambda c: ((getattr(c.func, "id", None) or getattr(c.func, "attr", None))
                          in ("_ai_post", "_post") and c.args
                          and isinstance(c.args[0], ast.Constant) and c.args[0].value == ep)

    _fa = _funcs(_src)
    _flow = _fa["go_sleep_flow"]
    _g, _loc = _line(_flow, _by_name("overnight_busy")), _line(_flow, _by_name("_resolve_target_bed"))
    ck("⑧`go_sleep_flow` 里 gate 在 `_resolve_target_bed` **之前**",
       0 < _g < _loc, f"gate@{_g} locate@{_loc}")
    _w = _line(_flow, _post_to("/walk_to"))
    ck("⑧`go_sleep_flow` 里 gate 在 `/walk_to` **之前**", 0 < _g < _w, f"gate@{_g} walk@{_w}")
    ck("⑧`go_sleep_flow` 里 gate 在 `/crawl_bed`(爬床) 与 `/sleep`(就绪) **之前**",
       0 < _g < _line(_flow, _post_to("/crawl_bed")) and 0 < _g < _line(_flow, _post_to("/sleep")),
       f"gate@{_g} crawl@{_line(_flow, _post_to('/crawl_bed'))} sleep@{_line(_flow, _post_to('/sleep'))}")

    _fm = _funcs(_msrc)
    _tool = _fm["go_sleep"]
    _mg, _aim = (_line(_tool, _by_name("overnight_busy")),
                 _line(_tool, _by_name("_aim_sleep_home")))
    ck("⑧MCP `go_sleep` 里 gate 在 `_aim_sleep_home` **之前**", 0 < _mg < _aim,
       f"gate@{_mg} aim@{_aim}")
    ck("⑧MCP `go_sleep` 里 gate 也在 `go_sleep_flow` **之前**",
       0 < _mg < _line(_tool, _by_name("go_sleep_flow")), f"gate@{_mg}")

    _ob = _fa["overnight_busy"]
    ck("⑧`overnight_busy` 里**没有任何写请求**（判据必须一步都还没走）",
       _line(_ob, _by_name("_ai_post")) < 0 and _line(_ob, _by_name("_post")) < 0,
       f"ai_post@{_line(_ob, _by_name('_ai_post'))} post@{_line(_ob, _by_name('_post'))}")

    def _who_kwargs(fn):
        """函数体里所有 `xxx.update(... who="…")` 的 who 值（= 谁会被标成"拦"）。"""
        return [kw.value.value for c in ast.walk(fn) if isinstance(c, ast.Call)
                for kw in c.keywords
                if kw.arg == "who" and isinstance(kw.value, ast.Constant)]

    _whos = _who_kwargs(_ob)
    ck("⑧`overnight_busy` 只认**自己**那端脏（源码里不再有 who=\"host\" 的拦截）",
       _whos == ["ai"], str(_whos))
    ck("⑧房主那端只出 `note`（源码里走的是 `_host_sleep_note(host_st)`）",
       _line(_ob, _by_name("_host_sleep_note")) > 0, "没看到 `_host_sleep_note`")
finally:
    pass

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 只拦「AI 自己已经睡上了还再按 sleep」；房主先睡照放行；量不到不锁死；文案说清谁那一端。")
