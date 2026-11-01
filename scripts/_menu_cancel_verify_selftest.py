# -*- coding: utf-8 -*-
"""🚶 `menu cancel` **必须回读**（关没关掉，不许嘴上说关掉）—— 纯 Python 自验，不碰游戏。2026-11-01

## 恒原话
> 「帮忙看看 ai 收到的信息**为什么有延迟**呢？具体就是，**菜单已经执行了关掉了，但是提醒说菜单还在。
>  再关一次，又说已经没有菜单了**」

## 病根（老 `cancel()` 三处都不诚实）
  ① 无条件 `return _with_state("🚶 已按取消键/关掉弹窗")` —— **关没关掉根本没看**；
  ② 关完立刻拼状态条，而菜单可能还没真消失 ⇒ **同一屏**上既写"关掉了"、又挂着一行
     「📋 菜单打开: X」⇒ 就是恒看到的那种自相矛盾（他再关一次，这次读的是真值 ⇒ "已经没有了"）；
  ③ 压根没菜单时也照按 cancel —— 而 `key cancel` **在没有菜单时 = 挥工具**
     （本项目栽过：`clear_area.py:431`、`intent_menu.py:1716`）。

## 钉死五条
  ① 关掉了就说关掉了（还要说明"回读确认过"）；
  ② 没关掉就**如实说还开着** + 指路（`_close_hint`），**不许**出现"已关掉"；
  ③ 本来就没菜单 ⇒ **一次键都不许按**（防挥工具）；
  ④ 收工时作废闸门缓存（`_MENU_GATE_CACHE`），别让下一个调用方吃到旧值；
  ⑤ 老那口"已按取消键/关掉弹窗"**不许**再出现在源码里（防人改回去）。
"""
import io
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []
BOX = {"menu": None}
CALLS = {"key": 0, "close": 0}


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _state():
    return {"activeMenu": ({"type": BOX["menu"]} if BOX["menu"] else None),
            "player": {"x": 1, "y": 2}}


def _fake_key(k, n=1):
    CALLS["key"] += 1
    if BOX.get("closes_on_key", False):
        BOX["menu"] = None


def _fake_close():
    CALLS["close"] += 1
    if BOX.get("closes_on_close", False):
        BOX["menu"] = None
    return {"ok": True}


_real = {
    "with_state": M._with_state,
    "ensure_roles": M.api.ensure_roles,
    "ai_get": M.api._ai_get,
    "state": M.api.state,
    "key": M.api.key,
    "menu_close": M._menu_close,
    "hint": M._close_hint,
}


def _reset(menu, **flags):
    BOX.clear()
    BOX.update({"menu": menu})
    BOX.update(flags)
    CALLS.update(key=0, close=0)
    M._MENU_GATE_CACHE.update(ts=0.0, menu=None)


try:
    M._with_state = lambda s, force_full=False: s
    M.api.ensure_roles = lambda: None
    M.api._ai_get = lambda path: _state()
    M.api.state = _state
    M.api.key = _fake_key
    M._menu_close = _fake_close

    print("① 真关掉了 ⇒ 说关掉了，并说明是回读确认的")
    _reset("ShopMenu", closes_on_key=True)
    _out = M.cancel()
    ck("…说「已关掉「ShopMenu」」", "已关掉「ShopMenu」" in _out, _out)
    ck("…点明「回读确认过」", "回读确认过" in _out, _out)
    ck("…按了一次取消键", CALLS["key"] == 1, str(CALLS))
    ck("…收工作废了闸门缓存", M._MENU_GATE_CACHE["ts"] == 0.0, str(M._MENU_GATE_CACHE))

    print("\n② 关不掉 ⇒ **如实说还开着**（不许说关掉了）")
    _reset("MuseumMenu")            # key/close 都不生效
    _out2 = M.cancel()
    ck("…说「还开着（MuseumMenu）」", "还开着" in _out2 and "MuseumMenu" in _out2, _out2)
    ck("…**不许**出现「已关掉」", "已关掉" not in _out2, _out2)
    ck("…给了那一刻该怎么走（`_close_hint` 点名真路 `menu donate`）",
       "menu donate" in _out2, _out2)
    ck("…连试 3 次（每次都要重读真值）", CALLS["key"] == 1, str(CALLS))

    print("\n③ 本来就没菜单 ⇒ **一次键都不许按**（没菜单时 cancel = 挥工具）")
    _reset(None)
    _out3 = M.cancel()
    ck("…说「没有菜单开着（不用关）」", "没有菜单开着" in _out3, _out3)
    ck("…`key cancel` **一次都没发**", CALLS["key"] == 0, str(CALLS))
    ck("…`/menu_close` 也没发", CALLS["close"] == 0, str(CALLS))

    print("\n④ 源码闸：老那口无条件成功话术不许回来")
    _src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nagi_mcp_server.py"),
                   encoding="utf-8").read()
    ck("…那句无条件的「已按取消键/关掉弹窗」return 不许回来",
       '_with_state("🚶 已按取消键/关掉弹窗")' not in _src)
    ck("…`cancel()` 里确有回读循环", "_menu_gate_now(fresh=True)" in _src)

finally:
    M._with_state = _real["with_state"]
    M.api.ensure_roles = _real["ensure_roles"]
    M.api._ai_get = _real["ai_get"]
    M.api.state = _real["state"]
    M.api.key = _real["key"]
    M._menu_close = _real["menu_close"]
    M._close_hint = _real["hint"]
    M._MENU_GATE_CACHE.update(ts=0.0, menu=None)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
