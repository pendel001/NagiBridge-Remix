"""🧪 截图状态条 / 🗺️ 可门禁 —— **离线自验**（不起 MCP 服务、不碰正在跑的游戏）。

跑法:
    PYTHONIOENCODING=utf-8 python scripts/_screenshot_gate_selftest.py

为什么要有这个文件：`import nagi_mcp_server` 是**安全**的（末尾才有 `if __name__ == "__main__"`，
不起线程、不连游戏），于是把 `M.api` / `M._gather_state` 换掉就能把状态条与门禁的判据打干净。
⚠️ **绝不能在这条路上真调 `_gather_state`** —— 它是 `consume_events=True`，会把正在玩的那局
   的 📰 小新闻/未读信**从游戏里消费掉**，AI 那边就永远看不见了（见 `_OPS_INNER` 那段长注释）。

覆盖（对应 2026-09-23 恒的四条实测反馈）：
  ① `screenshot()` 返回 [状态条文本, Image]，且**空正文不加分隔线**
  ② `_with_state(正文)` 仍然 = 欢迎横幅 + 正文 + 后缀（抽函数前后**逐字一致**）
  ③ `🗺️ 可:` 会藏掉未解锁的地图条目（矿井=春5日）与推不开的门（探险家公会）
  ④ 判据读不到时**一律不藏**（不误伤）
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nagi_mcp_server as M          # noqa: E402  —— 安全：末尾才有 __main__ 守卫
import navigation                    # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 假状态（形状照抄 /state 的 light 版；不联网） ──
FAKE_DATA = {
    "time": {"season": "spring", "dayOfMonth": 2, "year": 1,
             "timeOfDay": 1210, "weather": 0},
    "player": {"name": "轮回", "x": 15, "y": 40, "health": 100, "maxHealth": 100,
               "stamina": 200, "maxStamina": 270, "money": 500, "maxItems": 12,
               "currentTool": "", "dailyLuck": 0.0},
    "location": {"name": "Mountain"},
    "inventory": [],
    "raw": {"recent_events": []},
    "alerts": [],
}


class _NoGame:
    """任何一次"真去碰游戏"的调用都当场炸出来 —— 自验跑到这里说明桩没打全。"""
    def __getattr__(self, name):
        def _boom(*a, **kw):
            raise RuntimeError(f"自验不该碰游戏（api.{name}）")
        return _boom


def _stub_module():
    M.api = _NoGame()
    M._gather_state = lambda: dict(FAKE_DATA)
    M._fetch_farm_report = lambda *a, **kw: {}
    M._build_morning_report = lambda *a, **kw: ""
    M._maybe_ice_fishing_auto = lambda *a, **kw: ""
    M._maybe_egg_run_auto = lambda *a, **kw: ""
    M._bg_activity_line = lambda *a, **kw: ""
    M._plan_drain_notices = lambda *a, **kw: ""
    M._delta_show = lambda *a, **kw: ""
    M._statue_reminder = lambda *a, **kw: ""
    M._machine_ready_hint = lambda *a, **kw: ""
    M._sit_hint = lambda *a, **kw: ""
    M._worn_summary = lambda *a, **kw: ""
    M._forage_summary = lambda *a, **kw: ""
    M._shipbin_hint = lambda *a, **kw: ""
    M._backpack_upgrade_poi = lambda *a, **kw: ""
    M._special_orders_inject = lambda *a, **kw: ""
    M._sittable_cached = lambda *a, **kw: {}
    M.player_activity.should_inject = lambda *a, **kw: False
    M._first_call_since_start = False          # 不掺欢迎横幅，便于逐字比对


def main():
    _stub_module()

    # ── ① 空正文的后缀：不带分隔线，但状态条在 ──
    print("① 截图那条路（result=''）")
    note = M._state_suffix("")
    check("不含前导 ╌╌╌ 分隔线", not note.startswith("\n\n╌"), repr(note[:24]))
    check("状态条在（📍 Mountain）", "📍 Mountain" in note)
    check("镜头/时间行在", "12:10" in note)

    body = "📸 举例"
    full = M._with_state(body)
    check("非空正文**仍带**分隔线", "\n\n╌" in full)
    check("正文在前、状态条在后", full.index(body) < full.index("📍 Mountain"))
    check("拼法 = 正文 + _state_suffix(正文)",
          full == body + M._state_suffix(body), repr(full[:30]))

    # ── ② 截图工具真的返回 [文本, Image] ──
    print("② screenshot() 返回形态")
    M._take_welcome = lambda: ""
    M._state_suffix = lambda *a, **kw: "📋 状态条stub"
    M.api = _NoGame()
    M.api.screenshot_ai = lambda: {"ok": True, "width": 800, "height": 600,
                                   "image": _tiny_png_b64()}
    out = M.screenshot.__wrapped__()
    check("是 list（文本块 + 图）", isinstance(out, list) and len(out) == 2, type(out).__name__)
    if isinstance(out, list):
        check("第 0 块是状态条文本", out[0] == "📋 状态条stub")
        check("第 1 块是 Image", out[1].__class__.__name__ == "Image")
    # FastMCP 会把 list 递归转成 [TextContent, ImageContent] —— 见 `_convert_to_content`
    from mcp.server.fastmcp.utilities.func_metadata import _convert_to_content
    blocks = _convert_to_content([out[0], out[1]]) if isinstance(out, list) else []
    check("转出的内容块 = [text, image]",
          [b.type for b in blocks] == ["text", "image"], [b.type for b in blocks])
    # 非文本返回 + 菜单闸门捎话：别 str+list 炸掉（_gated_tool）
    check("_gated_tool 的拼接分支不炸",
          isinstance(_join_note("提示", out), list))

    # ── ③④ 门禁 ──
    print("③ 🗺️ 可 门禁（春2日年1：矿井未开 / 公会推不开）")
    _stub_module()
    M._last_full_date = ("spring", 2, 1)        # 装成"今天已经报过"，走精简版
    M.map_feature_hidden = lambda loc: {"矿井", "探险家公会"} if loc == "Mountain" else set()
    strip = M._build_state_strip(dict(FAKE_DATA), full=False)
    check("藏掉「矿井」", "矿井" not in strip)
    check("藏掉「探险家公会」", "探险家公会" not in strip)
    check("**没藏**别的（罗宾木匠店还在）", "罗宾木匠店" in strip, strip[-160:])

    print("④ 判据读不到 → 一律不藏")
    M.map_feature_hidden = lambda loc: set()
    strip2 = M._build_state_strip(dict(FAKE_DATA), full=False)
    check("矿井还在", "矿井" in strip2)
    check("探险家公会还在", "探险家公会" in strip2)

    print("⑤ navigation.map_feature_hidden 本身")
    navigation.api.day_key = lambda: "spring|2|1"
    navigation._DOOR_BLOCKED.update(day="spring|2|1", keys=set())
    navigation._DAYKEY_CACHE.update(ts=0.0, key=None)
    navigation._locked_maps = lambda: {"Mine"}
    nav_hidden = navigation.map_feature_hidden("Mountain")
    check("未解锁地图 → 藏「矿井」", "矿井" in nav_hidden, nav_hidden)
    check("塌方挡路(map:Mine) → 也藏「探险家公会」（物理上到不了）",
          "探险家公会" in nav_hidden, nav_hidden)

    navigation._locked_maps = lambda: set()          # 塌方通了
    check("塌方通了但门没推过 → 公会**不**藏",
          "探险家公会" not in navigation.map_feature_hidden("Mountain"))
    navigation.mark_door_blocked("AdventureGuild", "Mountain")
    check("……推过打不开的门 → 公会照样藏（list 里第二条生效）",
          "探险家公会" in navigation.map_feature_hidden("Mountain"))
    navigation._DOOR_BLOCKED["day"] = "spring|4|1"
    navigation._locked_maps = lambda: {"Mine"}

    print("⑤b 塌方拦导航（map_go 拒发，别让 AI 穿石头）")
    navigation._DOOR_BLOCKED.update(day="spring|2|1", keys=set())
    navigation._locked_maps = lambda: {"Mine"}
    refusal = navigation._map_go_unlock_check("AdventureGuild")
    check("矿井没开时拒发 map_go 公会", "塌方" in refusal, refusal[:60])
    check("拒发文案给了下一步（山西南侧能去哪）", "罗宾木匠店" in refusal, refusal[:80])
    navigation._locked_maps = lambda: set()
    check("塌方通了就放行", navigation._map_go_unlock_check("AdventureGuild") == "")
    navigation._locked_maps = lambda: {"Mine"}

    navigation.mark_door_blocked("AdventureGuild", "Mountain")
    nav_hidden = navigation.map_feature_hidden("Mountain")
    check("推过打不开的门 → 藏「探险家公会」", "探险家公会" in nav_hidden, nav_hidden)
    check("换张图不受影响", navigation.map_feature_hidden("Town") == set())

    navigation._DOOR_BLOCKED["day"] = "spring|3|1"      # 睡一觉
    _saved_locked = navigation._locked_maps
    navigation._locked_maps = lambda: set()             # 把塌方那条摘掉，单独看 door 记忆清没清
    check("换天后门禁记忆自动清空",
          navigation.map_feature_hidden("Mountain") == set(),
          navigation.map_feature_hidden("Mountain"))
    navigation._locked_maps = _saved_locked

    navigation.mark_door_blocked("AdventureGuild", "Mountain")   # 门禁记忆仍在
    navigation._locked_maps = lambda: (_ for _ in ()).throw(RuntimeError("读不到 /unlocks"))
    check("/unlocks 读不到 → **不误伤矿井**",
          "矿井" not in navigation.map_feature_hidden("Mountain"),
          navigation.map_feature_hidden("Mountain"))
    check("/unlocks 读不到 → 推不开的门照藏",
          "探险家公会" in navigation.map_feature_hidden("Mountain"),
          navigation.map_feature_hidden("Mountain"))

    print("⑥ 门禁表自身的自洽（改了 MAP_FEATURES 会在这里被拦下）")
    import locations as L
    bad = []
    for loc, gates in L.MAP_FEATURE_GATES.items():
        feats = L.MAP_FEATURES.get(loc)
        if feats is None:
            bad.append(f"{loc} 不在 MAP_FEATURES")
            continue
        toks = {f.split("(")[0].strip() for f in feats}
        for token, dep in gates.items():
            for one in (dep if isinstance(dep, (list, tuple, set)) else [dep]):
                kind, _, target = str(one).partition(":")
                if kind not in ("map", "door"):
                    bad.append(f"{loc}.{token} 依赖写法非法: {one}")
                if token not in toks:
                    bad.append(f"{loc}.{token} 在 MAP_FEATURES 里找不到对应条目")
                # `map:` 指向一个不在 LOCKED_MAPS 里的名字 = **永远藏不掉**（静默失效，最坑的一种）
                if kind == "map" and target not in navigation.LOCKED_MAPS:
                    bad.append(f"{loc}.{token} 的 {target} 不在 LOCKED_MAPS（这条门禁永远不会生效）")
    check("每条门禁都能对上 MAP_FEATURES 与 LOCKED_MAPS", not bad, bad)

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 项没过: {FAIL}")
        return 1
    print("✅ 全部通过")
    return 0


def _tiny_png_b64():
    """1×1 透明 PNG（真图，免得 PIL 分支抛异常影响判据）。"""
    import base64
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
           b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    return base64.b64encode(png).decode()


def _join_note(note, out):
    """复刻 `_gated_tool` 收尾那段（非 str 返回时把闸门说明拼成独立文本块）。"""
    if not note:
        return out
    if isinstance(out, str):
        return note + "\n" + out
    if isinstance(out, list):
        return [note + "\n"] + out
    return out


if __name__ == "__main__":
    sys.exit(main())
