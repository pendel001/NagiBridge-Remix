# -*- coding: utf-8 -*-
"""🏛️ 「向博物馆捐赠」那一项的**改道**——纯 Python 自验（不起服务、不碰游戏）。2026-11-01

## 恒原话（这一条就是"为什么要有这个文件"）
> 「我们的捐赠和砸晶球都不走原生菜单，但是**没有任何对应提示、拦截或者路由**。」
> 「**3.支持做路由。**」

## 真机现场（`scripts/sessions/session_log.jsonl` 11-01 09:50）
柜台对话框 `[0]向博物馆捐赠 [1]收集奖励 [2]离开`，AI 点了 0 ⇒ `MuseumMenu`（游戏原生摆放屏）
真的开了 ⇒ 在屏里 `option=0`（没目标）、`okButton`（白点）各空一次 ⇒ `menu cancel` 走人，
**手里有可捐的东西却一件没捐**。

## 判据（钉死的四条）
  ① **认 `responseKey`，不认文字**（`Donate`；文本是本地化的）；
  ② **只认 `ArchaeologyHouse`**（C# `/museum_donate` 写死这一座馆；姜岛田野办公室同 key 但不归它管）；
  ③ 老 DLL 不报 key ⇒ **不猜**（退回"照旧点"，和改动前一样）；
  ④ 改道**必须写在回执第一行**（点了 A 做了 B，静默就是我们自己造的假成功）。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 桩：把"游戏那一半"全换成假的（本文件一个 HTTP 都不打） ──
_real = {}
_calls = {"menu_click": 0, "museum_donate": 0, "cancel": 0}
_box = {"loc": "ArchaeologyHouse", "responses": [], "menu_type": "DialogueBox", "open": True}


def _patch(name, val):
    _real[name] = getattr(M, name, _MISSING)
    setattr(M, name, val)


class _Missing:
    pass


_MISSING = _Missing()


def _fake_menu():
    return {"open": _box["open"], "type": _box["menu_type"],
            "responses": _box["responses"]}


def _fake_state():
    return {"location": {"name": _box["loc"]},
            "activeMenu": ({"type": _box["menu_type"]} if _box["open"] else None),
            "player": {"x": 3, "y": 10}}


def _fake_menu_click(**kw):
    _calls["menu_click"] += 1
    return {"ok": True, "clicked": "response"}


def _fake_museum_donate():
    _calls["museum_donate"] += 1
    return "🏛️ 捐了 2 件（桩）" + M._STATE_SEP + "📍 状态条（桩）"


def _fake_cancel():
    _calls["cancel"] += 1
    _box["open"] = False          # 现实里 cancel 会把对话框关掉
    return "🚶 已关掉" + M._STATE_SEP + "📍 状态条（桩）"


try:
    _patch("_ensure_background", lambda: None)
    _patch("_with_state", lambda s, force_full=False: s)
    _patch("cancel", _fake_cancel)
    _patch("museum_donate", _fake_museum_donate)
    _patch("_peer_econ_mute", lambda: None)
    _real["api.menu"] = M.api.menu
    _real["api.state"] = M.api.state
    _real["api.menu_click"] = M.api.menu_click
    M.api.menu = _fake_menu
    M.api.state = _fake_state
    M.api.menu_click = _fake_menu_click

    print("① 判据只认 `responseKey == Donate` + 只在 ArchaeologyHouse")
    _box.update(loc="ArchaeologyHouse", open=True, menu_type="DialogueBox",
                responses=[{"key": "Donate", "text": "向博物馆捐赠"},
                           {"key": "Collect", "text": "收集奖励"},
                           {"key": "Leave", "text": "离开"}])
    ck("…柜台第 0 项（Donate）⇒ 认得出", M._donate_option_here(0) is True)
    ck("…第 1 项（Collect）⇒ **不认**（领奖要保持原生三步，恒 10-05 拍板）",
       M._donate_option_here(1) is False)
    ck("…第 2 项（Leave）⇒ 不认", M._donate_option_here(2) is False)
    ck("…选项号越界 ⇒ 不认（不炸）", M._donate_option_here(9) is False)
    _box["loc"] = "IslandFieldOffice"
    ck("…姜岛田野办公室同 key ⇒ **不认**（别把捐投进另一座馆）",
       M._donate_option_here(0) is False)
    _box["loc"] = "ArchaeologyHouse"
    _box["responses"] = ["向博物馆捐赠", "收集奖励", "离开"]      # 老 DLL：只报文本
    ck("…老 DLL 不报 key ⇒ **不猜**（照旧点，行为不变）", M._donate_option_here(0) is False)
    _box["open"] = False
    ck("…没有菜单开着 ⇒ 不认", M._donate_option_here(0) is False)

    print("\n② 改道本身：先收对话框、再走 `menu donate`、回执**第一行就写「改道」**")
    _box.update(open=True, menu_type="DialogueBox", loc="ArchaeologyHouse",
                responses=[{"key": "Donate", "text": "向博物馆捐赠"}])
    _calls.update(menu_click=0, museum_donate=0, cancel=0)
    _out = M._reroute_museum_donate()
    ck("…调了 `cancel`（不然 `menu donate` 里的 walk_to 会被菜单闸门拦）", _calls["cancel"] == 1)
    ck("…调了 `museum_donate`（走现成那条，不另写一套捐法）", _calls["museum_donate"] == 1)
    ck("…第一行明写「改道」（点了 A 做 B，不许静默）", "改道" in _out.split("\n")[0], _out[:80])
    ck("…告诉它正路是 `menu donate`", "menu donate" in _out)
    ck("…带回了自动捐的结果", "捐了 2 件" in _out)
    ck("…内层状态条被剥掉（不留两条分隔线）", _out.count(M._STATE_SEP) == 0, _out[-60:])

    print("\n③ 对话框收不掉 ⇒ 如实说「没能改道」，**一件都不许捐**")
    _box.update(open=True, menu_type="DialogueBox")
    _calls.update(menu_click=0, museum_donate=0, cancel=0)
    M.cancel = lambda: "🚧 关不掉（桩）"           # 收不掉
    _out2 = M._reroute_museum_donate()
    ck("…说了「没能改道」", "没能改道" in _out2, _out2[:80])
    ck("…**没调** `museum_donate`（菜单开着必失败，别硬跑）", _calls["museum_donate"] == 0)
    ck("…给了下一步 `menu cancel`", "menu cancel" in _out2)
    M.cancel = _fake_cancel
    _box["open"] = False

    print("\n④ 端到端 `menu_click(option=0)`：被改道，**不点原生那一下**")
    _box.update(open=True, menu_type="DialogueBox", loc="ArchaeologyHouse",
                responses=[{"key": "Donate", "text": "向博物馆捐赠"}])
    _calls.update(menu_click=0, museum_donate=0, cancel=0)
    _out3 = M.menu_click(option=0, real=True)
    ck("…`api.menu_click` **一次都没调**（原生那下没发生）", _calls["menu_click"] == 0, str(_calls))
    ck("…改走了 `museum_donate`", _calls["museum_donate"] == 1)
    ck("…回执里有「改道」", "改道" in _out3, _out3[:80])

    print("\n⑤ 反例：Collect 照旧点原生的那一下（别把领奖也改道）")
    _box.update(open=True, menu_type="DialogueBox", loc="ArchaeologyHouse",
                responses=[{"key": "Collect", "text": "收集奖励"},
                           {"key": "Donate", "text": "向博物馆捐赠"}])
    _calls.update(menu_click=0, museum_donate=0, cancel=0)
    M.menu_click(option=0, real=True)
    ck("…点了原生（`api.menu_click` 1 次）", _calls["menu_click"] == 1, str(_calls))
    ck("…**没**改道去捐", _calls["museum_donate"] == 0, str(_calls))

    print("\n⑥ 点空时：把这屏该走哪条 + 单子一起说出来（原生屏那两条）")
    for _mt, _want in (("MuseumMenu", "menu donate"), ("GeodeMenu", "menu geodes")):
        _box.update(open=True, menu_type=_mt, loc="ArchaeologyHouse", responses=[])
        M.api.menu_click = lambda **kw: {"ok": False, "error": f"{_mt} 上没有可点的目标（桩）"}
        _miss = M.menu_click(item="Geode")
        ck(f"…`{_mt}` 点空 ⇒ 回执点名真路 `{_want}`", _want in _miss, _miss[:120])
        ck(f"…`{_mt}` 点空 ⇒ 回执点名 `intent show`（11-01 真机：57 次调用 intent 0 次）",
           "intent show" in _miss, _miss[:120])
        ck(f"…`{_mt}` 点空 ⇒ 仍然**如实报没做成**（「别当成完成」不许丢）",
           "没有可点的目标" in _miss or "别当成完成" in _miss, _miss[:120])
    M.api.menu_click = _fake_menu_click

finally:
    for k, v in _real.items():
        if isinstance(k, str) and k.startswith("api."):
            setattr(M.api, k.split(".", 1)[1], v)
        elif v is not _MISSING:
            setattr(M, k, v)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
