# -*- coding: utf-8 -*-
"""🔇 机制类警报不进 AI 视野 —— 纯 Python 自验（不起服务、不碰游戏）。2026-11-01

## 恒原话（这一条就是"为什么要有这个文件"）
> 「（`现在这个门：到（54，3）的唯一的路………………`）这个是什么，**机制方面不必和 ai 说吧**」

## 那句话的真身
`ModEntry.cs` 里 BFS 走不通（把门/传送格挡掉之后）时发的一条 warning：

> 🚪 到 (x,y) 的**唯一的路要穿过门/传送格** —— 照原路走；踩上那格原生 warp 时
> **按游戏规则换图**（不是我们自己算的兜底 warp）

它讲的是**我们的实现选择**，对 AI 的下一步没有任何用处；而且 severity=warning ⇒ 每条状态条
顶上挂一个 ⚠️（`_build_state_strip` 只渲染 warning/error），看起来像出了事。
⇒ `_ALERT_MECHANISM_ONLY` 里挡住它；**换图这件事本来就写在 `walk_to` 的回执里**。

## 钉死三条
  ① `walk_cross_warp` 不出现；② 别的 warning **照旧出现**（尤其 `walk_teleport`：真瞬移，AI 必须知道）；
  ③ 判据只有一处（状态条那行走 `_ai_visible_alerts`，别被人改回裸列表推导）。
"""
import io
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


_ALERTS = [
    {"type": "walk_cross_warp", "severity": "warning",
     "message": "🚪 到 (54,3) 的**唯一的路要穿过门/传送格** —— 照原路走；"
                "踩上那格原生 warp 时**按游戏规则换图**（不是我们自己算的兜底 warp）"},
    {"type": "walk_teleport", "severity": "warning", "message": "瞬时移动到 (12,7)"},
    {"type": "stamina_low", "severity": "warning", "message": "Stamina low: 3/270"},
    {"type": "water_empty", "severity": "error", "message": "Watering can is empty"},
    {"type": "hud", "severity": "info", "message": "（info 本来就不进状态条）"},
]

print("① 机制类警报不进（恒 11-01「机制方面不必和 ai 说吧」）")
_vis = M._ai_visible_alerts(_ALERTS)
_types = [a.get("type") for a in _vis]
ck("…`walk_cross_warp` **不在**可见列表里", "walk_cross_warp" not in _types, str(_types))
ck("…整条消息里也不许出现「唯一的路」", all("唯一的路" not in str(a.get("message")) for a in _vis))

print("\n② 别的警报**照旧**（别一顺手消音太多）")
ck("…`walk_teleport`（真·穿墙瞬移）**要留着** —— 那是「你这次没走到」", "walk_teleport" in _types, str(_types))
ck("…`stamina_low` warning 照旧", "stamina_low" in _types, str(_types))
ck("…`water_empty` error 照旧", "water_empty" in _types, str(_types))
ck("…info 级不进（沿用老规矩）", "hud" not in _types, str(_types))
ck("…判据只挡**名单里那几个 type**，不是「凡是 walk 开头都挡」",
   len(M._ALERT_MECHANISM_ONLY) == 1 and "walk_cross_warp" in M._ALERT_MECHANISM_ONLY,
   str(M._ALERT_MECHANISM_ONLY))

print("\n②·5 `walk_teleport` 的说法改成**结果层**（C# 原话是 `BFS failed, teleporting to (x,y)`）")
_src_alert = {"type": "walk_teleport", "severity": "warning",
              "message": "BFS failed, teleporting to (54,3)"}
_tp = M._ai_visible_alerts([_src_alert])
ck("…还在（这条语义必须留）", len(_tp) == 1)
ck("…但**不许**再把 `BFS failed` 递给 AI", "BFS" not in _tp[0]["message"], _tp[0]["message"])
ck("…要点明「不算走到」（这才是它该知道的）", "不算走到" in _tp[0]["message"], _tp[0]["message"])
ck("…**没改原对象**（`/alerts` 那份原话还要给日志/排查看）",
   _src_alert["message"].startswith("BFS failed"), _src_alert["message"])

print("\n③ 边界：空/脏数据不许炸")
ck("…None ⇒ 空表", M._ai_visible_alerts(None) == [])
ck("…混进非 dict ⇒ 跳过（不抛）", M._ai_visible_alerts([None, "x", {"type": "stamina_low", "severity": "warning"}])
   == [{"type": "stamina_low", "severity": "warning"}])

print("\n④ 判据只此一处（状态条那行必须走它）")
_src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nagi_mcp_server.py"),
               encoding="utf-8").read()
ck("…状态条用的是 `_ai_visible_alerts(alerts)`", "warnings = _ai_visible_alerts(alerts)" in _src)
ck("…**没有**残留裸列表推导那版（`[a for a in alerts if a.get(\"severity\") ...`）",
   'warnings = [a for a in alerts if a.get("severity")' not in _src)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
