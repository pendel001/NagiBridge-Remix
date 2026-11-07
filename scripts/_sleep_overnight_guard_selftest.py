# -*- coding: utf-8 -*-
"""🌙 过夜菜单开着的时候**一次 sleep 都不许再按** —— 纯 Python 自验。2026-11-07

恒：「**ai 反复过夜 sleep 导致我这边结算卡死了。不把他踢出去今天的存档就存不了了。我很无语。**
     能不能禁止它在过夜菜单开着的时候 sleep。」

现场账（同一时刻两端 `/state`）：
  · host(7842) `activeMenu=ShippingMenu`、人已上床（`spring15 06:00`，结算屏还开着）
  · AI(7843)   `menu=none`、还赖在床格里（`sleepReady=NotReady`）
  ⇒ 模型看到"夜还没过"就**再喊一次 sleep** ⇒ 每次都往房主**正开着的结算屏**上再压一次
    sleep/ready 请求。结算→存盘那一段一天只跑一次、单线程，被反复打断就卡死。

判据：
  ① 「谁的过夜菜单开着，谁那一夜就还在结算里」：`ShippingMenu` / `SaveGameMenu` / `ReadyCheckDialog`；
  ② **拦在走位之前**（`_aim_sleep_home` 会真的把人走过去/warp，一下都不该落地）；
  ③ 量不到房主（单机/老 DLL/断连）⇒ **不许把人锁死**（自己那头没事就放行）；
  ④ 文案必须说清"是谁那一端、为什么不能按、现在该干什么"，且**不许谎报"已上床"**。
"""
import os
import sys

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


class Fake:
    """两端的 `/state`（只关心 player.name + activeMenu.type）。"""

    def __init__(self, ai_menu="", host_menu="", ai_name="Claude", host_name="恒",
                 host_dead=False, ai_dead=False):
        self.ai_st = {"player": {"name": ai_name, "isInBed": True},
                      "activeMenu": ({"type": ai_menu} if ai_menu else None),
                      "time": {"season": "spring", "dayOfMonth": 15}}
        self.host_st = {"player": {"name": host_name, "isInBed": True},
                        "activeMenu": ({"type": host_menu} if host_menu else None)}
        self.host_dead = host_dead
        self.ai_dead = ai_dead
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
            return {"ok": True}

        api._ai_get = _ai_get
        api.host_state = _host_state
        api._ai_post = _ai_post
        api.detect_roles = lambda *a, **k: {"ok": True}
        return self


_OM = ("ShippingMenu", "SaveGameMenu", "ReadyCheckDialog")

try:
    print("\n① 判据表：谁那一端开着过夜菜单，谁就在结算里")
    for _ai_m, _host_m, _exp_busy, _exp_who in (
            ("", "", False, ""),
            ("", "ShippingMenu", True, "host"),
            ("", "SaveGameMenu", True, "host"),
            ("", "ReadyCheckDialog", True, "host"),
            ("ShippingMenu", "", True, "ai"),
            ("SaveGameMenu", "", True, "ai"),
            ("ReadyCheckDialog", "", True, "ai"),
            ("ReadyCheckDialog", "ShippingMenu", True, "ai"),   # 自己那头优先说
            ("InventoryPage", "", False, ""),                   # 普通背包不算
            ("", "JunimoNoteMenu", False, ""),                  # 别的菜单不算
    ):
        _f = Fake(ai_menu=_ai_m, host_menu=_host_m).install()
        _r = api.overnight_busy()
        _ok = (_r["busy"] is _exp_busy) and (_r["who"] == _exp_who)
        ck(f"①ai={_ai_m or '-'} host={_host_m or '-'} ⇒ busy={_exp_busy} who={_exp_who or '-'}",
           _ok, f"实际 busy={_r['busy']} who={_r['who']!r}")

    print("\n② 量不到房主 ⇒ **不许把人锁死**（睡眠是刚需）")
    api.overnight_busy()
    _f = Fake(host_dead=True).install()
    _r = api.overnight_busy()
    ck("②房主读不到、自己这头没事 ⇒ 放行", _r["busy"] is False, str(_r))
    _f = Fake(host_dead=True, ai_menu="ShippingMenu").install()
    _r = api.overnight_busy()
    ck("②自己这头开着结算 ⇒ 仍然拦（不因房主读不到而漏）",
       _r["busy"] is True and _r["who"] == "ai", str(_r))
    _f = Fake(ai_dead=True, host_menu="ShippingMenu").install()
    _r = api.overnight_busy()
    ck("②自己读不到、房主在结算 ⇒ 照样拦", _r["busy"] is True and _r["who"] == "host", str(_r))

    print("\n③ 文案：说清「谁 + 为什么 + 现在干什么」，且不谎报成功")
    _f = Fake(host_menu="ShippingMenu").install()
    _m = api.overnight_busy()["msg"]
    ck("③点了房主的名", "恒" in _m, _m)
    ck("③说了是「过夜结算」", "结算" in _m, _m)
    ck("③明令「现在不许按」", ("不许按" in _m) or ("别按" in _m), _m)
    ck("③给了出路（等着/进新一天）", "等" in _m, _m)
    ck("③不许出现「已上床/已就绪」这种假成功", not any(w in _m for w in ("已上床", "已就绪")), _m)

    _f = Fake(host_menu="SaveGameMenu").install()
    _m2 = api.overnight_busy()["msg"]
    ck("③存盘那句说了「存盘」", "存盘" in _m2, _m2)

    _f = Fake(host_menu="ReadyCheckDialog").install()
    _m3 = api.overnight_busy()["msg"]
    ck("③就绪屏那句点明「就差他按确认」", "确认" in _m3, _m3)
    ck("③并说明「再按会破坏就绪同步」", "就绪同步" in _m3, _m3)

    _f = Fake(host_menu="ShippingMenu", ai_name="恒", host_name="恒").install()
    _m4 = api.overnight_busy()["msg"]
    ck("③单人档（两边同名）⇒ 不说「恒那边」，直接说「房主」", "那边" not in _m4, _m4)

    print("\n④ 真流程：过夜中调 go_sleep_flow ⇒ **零副作用**（一步不走、一次不摸）")
    _f = Fake(host_menu="ShippingMenu").install()
    _r = api.go_sleep_flow("恒")
    ck("④返回 ok=False", _r.get("ok") is False, str(_r.get("ok")))
    ck("④summary 就是那句拦截话", "结算" in str(_r.get("summary", "")), str(_r.get("summary")))
    ck("④**一个写请求都没发**（walk_to/position/crawl_bed/sleep/warp…全 0）",
       _f.posts == [], str(_f.posts))
    ck("④账上记了 gate 这一步", any(s.get("step") == "gate" and s.get("ok") is False
                                    for s in (_r.get("steps") or [])), str(_r.get("steps")))

    print("\n⑤ 反过来：没开过夜菜单 ⇒ 该走还得走（别把闸设成「永远拦」）")
    _f = Fake().install()
    api.go_sleep_flow("恒")
    ck("⑤过闸了（开始找床）", any(ep == "/crawl_bed" for (ep, _d) in _f.posts), str(_f.posts))

    print("\n⑥ MCP 那层：拦在**走位之前**（`_aim_sleep_home` 一下都不该跑）")
    _bak = (M.api, M._with_state, M._aim_sleep_home)
    M._with_state = lambda s: s
    _aimed, _flowed = [], []
    M._aim_sleep_home = lambda who: (_aimed.append(who) or "")
    _real_api = api
    try:
        _f = Fake(host_menu="ShippingMenu").install()
        _q = Fake()
        class _MCPApi:
            ensure_roles = staticmethod(lambda *a, **k: {"ok": True})
            overnight_busy = staticmethod(api.overnight_busy)
            go_sleep_flow = staticmethod(lambda *a, **k: (_flowed.append(a) or {"ok": True, "summary": "X"}))
        M.api = _MCPApi
        _out = M.go_sleep("恒")
        ck("⑥回了拦截话", "结算" in str(_out), str(_out))
        ck("⑥**没有走位**（_aim_sleep_home 没被调用）", _aimed == [], str(_aimed))
        ck("⑥**也没有进睡觉流程**", _flowed == [], str(_flowed))

        print("\n⑦ 没开过夜菜单时 MCP 照常走位（闸不误伤正常睡觉）")
        Fake().install()
        _aimed.clear()
        _out2 = M.go_sleep("恒")
        ck("⑦走位被调用了", _aimed == ["恒"], str(_aimed))
    finally:
        M.api, M._with_state, M._aim_sleep_home = _bak

    print("\n⑧ 源码钉")
    _sp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stardew_api.py")
    _mp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nagi_mcp_server.py")
    _src = open(_sp, encoding="utf-8").read()
    _msrc = open(_mp, encoding="utf-8").read()
    for _n in _OM:
        ck(f"⑧`_OVERNIGHT_MENUS` 里有 {_n}", _n in _src.split("_OVERNIGHT_MENUS = ")[1][:90])
    _flow = _src.split("def go_sleep_flow")[1]
    ck("⑧`go_sleep_flow` 里 gate 在 `_resolve_target_bed` **之前**",
       0 < _flow.find("overnight_busy()") < _flow.find("_resolve_target_bed("),
       f"gate@{_flow.find('overnight_busy()')} locate@{_flow.find('_resolve_target_bed(')}")
    _tool = _msrc.split("def go_sleep(")[1]
    ck("⑧MCP `go_sleep` 里 gate 在 `_aim_sleep_home` **之前**",
       0 < _tool.find("overnight_busy()") < _tool.find("_aim_sleep_home("),
       f"gate@{_tool.find('overnight_busy()')} aim@{_tool.find('_aim_sleep_home(')}")
    _ob_src = _src.split("def overnight_busy")[1][:2600]
    ck("⑧拦 sleep 时**不替谁按**结算屏（`overnight_busy` 里没有任何写请求）",
       "_ai_post(" not in _ob_src and "_post(" not in _ob_src.replace("host_state()", ""),
       "闸里出现了写请求")
finally:
    pass

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 过夜/存盘/就绪屏开着时 sleep 一步都不走；量不到房主也不锁死；正常睡觉照旧。")
