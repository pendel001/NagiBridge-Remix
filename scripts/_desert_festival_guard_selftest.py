# -*- coding: utf-8 -*-
"""🏜️ 沙漠节播报的**两道闸** —— 纯 Python 自验（离线打桩，不碰游戏、不发 HTTP）。

## 恒原话
> 「沙漠节的话刚刚提示说的好像是**沙漠节钓鱼游戏进行中**（估计没有判钓鱼地点，**在鹈鹕镇钓鱼
>  也算进去了**）+ 沙漠节**最后一天，推荐换啥啥**的提醒。」

## 根因（`_festival_activity_hint` 只看日期，两道闸都没有）
  ① 它只问 `calendar_data.get_festival_today(season, day)` —— **只看日期**：
     今天有节、菜单是 `BobberBar` ⇒ 就喊「🎣 沙漠节 钓鱼小游戏中（限时）：…」。
     可 `BobberBar` **在任何有水的地方钓鱼都会开**（鹈鹕镇、农场、山湖都算）
     ⇒ 「在镇里钓个鱼」被说成"沙漠节钓鱼比赛进行中"。
  ② 它**不问解锁**：沙漠要**修好巴士**才去得了（`LOCKED_MAPS["Desert"] = ("bus", …)`），
     没解锁也在播「沙漠节玩法」（同一条里就带着 17 号那句「**优先换武器/Magic Rock Candy**」）。
  ⇒ 现在：**没解锁 ⇒ 一律不注入该节日的玩法引导**（日历里「今天沙漠节」那行是**事实**，照留）；
     **「钓鱼游戏进行中」只许在今天的节日场地里说**（`_fest_venue_ok`：Desert / DesertFestival /
     Temp 三种叫法），在别处钓鱼照旧当普通钓鱼。

## 钉死这些条
  ① 没解锁沙漠 ⇒ 那两条（「钓鱼小游戏中」+「优先换…」）都不出现
  ② 解锁 + 人在 `DesertFestival` ⇒ 两条都出现（不是"把整段删了"）
  ③ 解锁但**在鹈鹕镇钓鱼** ⇒ 「钓鱼游戏进行中」不出现（本次的第二个真机症状）
  ④ 同一把尺子对别的节日/别的场地也成立（冰雪节：Forest 出、Town 不出）
  ⑤ 日历那行「今天沙漠节」作为事实**照留**（别把事实一起删掉）
  ⑥ 源码钉：`_festival_activity_hint` 收 `loc_name`、走 `_fest_unlocked` + `_fest_venue_ok`；
     `_fest_unlocked` 用的是 `navigation._map_go_unlock_check`（跟 `map go` 同一把尺子）；
     `_build_state_strip` 真的把 `loc_name` 传下去了；`_festival_recommend_hint` 也过解锁闸。

跑法：`python -X utf8 scripts/_desert_festival_guard_selftest.py`（退出码 0 = 全绿）
"""
import ast
import inspect
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import nagi_mcp_server as M       # noqa: E402
import calendar_data              # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 打桩：日期 / 解锁状态（都不碰网络）──────────────────────────────
_REAL = {
    "now": M._festival_now_data,
    "unlock": M.navigation._map_go_unlock_check,
    "host_name": M._host_name,
    "recommend_key": dict(M._FEST_RECOMMEND_KEY),
}
LOCKED = {"on": True}


def _stub(unlocked: bool, season: str = "spring", day: int = 17):
    """钉住"今天"和"沙漠解锁了没"。`_map_go_unlock_check` 是**那唯一一把尺子**。

    ⚠️ 只把 **Desert** 说成没解锁：别的图（Town/Forest/Beach…）本来就不在锁表里，
       假桩要是把全图都说成锁住，就测不出"究竟是哪把尺子"了。
    """
    LOCKED["on"] = not unlocked
    M._festival_now_data = lambda: {"ok": True, "season": season, "day": day, "year": 1}
    M.navigation._map_go_unlock_check = (
        lambda dest: (f"❌ {dest} 未解锁（bus fix），不能 map_go"
                      if (LOCKED["on"] and dest == "Desert") else ""))
    M._host_name = lambda: "房主"
    M._FEST_RECOMMEND_KEY["last"] = None       # 让"每天一次"的推荐不被上一次吃掉


def sec_gate():
    print("\n① 没解锁沙漠 ⇒ 沙漠节那两条播报**都不出现**")
    _stub(unlocked=False, day=17)
    _fish = M._festival_activity_hint("BobberBar", "DesertFestival")
    ck("①没解锁 + 菜单是钓鱼小游戏 ⇒ 一句都不给", _fish == "", _fish[:120])
    ck("①…连「钓鱼小游戏中」都没有", "钓鱼小游戏中" not in _fish, _fish[:120])
    ck("①…连「优先换…」（17 号那句）都没有", "优先换" not in _fish, _fish[:160])
    ck("①没解锁 + 就绪屏 ⇒ 一句都不给",
       M._festival_activity_hint("ReadyCheckDialog", "DesertFestival") == "",
       M._festival_activity_hint("ReadyCheckDialog", "DesertFestival")[:120])
    _rec = M._festival_recommend_hint("DesertFestival")
    ck("①没解锁 + 人在 DesertFestival ⇒ 「玩法推荐」也不给", _rec == "", _rec[:120])
    for _m in ("StorageContainer", "NumberSelectionMenu"):
        ck(f"①没解锁 + {_m} ⇒ 也不注入节日专用引导",
           M._festival_activity_hint(_m, "DesertFestival") == "",
           M._festival_activity_hint(_m, "DesertFestival")[:80])

    print("\n② 解锁且人在 DesertFestival ⇒ 两条**都出现**（不是把整段删了）")
    _stub(unlocked=True, day=17)
    _fish = M._festival_activity_hint("BobberBar", "DesertFestival")
    ck("②解锁 + 沙漠节场地钓鱼 ⇒ 出现「钓鱼小游戏中」", "钓鱼小游戏中" in _fish, _fish[:120])
    ck("②…并带上今天的玩法引导（17 号那句「优先换…」）", "优先换" in _fish, _fish[:200])
    ck("②…名字点的是今天的节日", "沙漠节" in _fish, _fish[:60])
    _rec = M._festival_recommend_hint("DesertFestival")
    ck("②解锁 + 沙漠节场地 ⇒ 出现「沙漠节玩法推荐」", "沙漠节玩法推荐" in _rec, _rec[:120])
    ck("②…里面有「钓鱼挑战」那一项（逛店清单没被吞）", "钓鱼挑战" in _rec, _rec[:200])
    ck("②解锁 + 就绪屏（节日入场）⇒ 出现「就绪确认」",
       "就绪确认" in M._festival_activity_hint("ReadyCheckDialog", "DesertFestival"), "")

    print("\n②' 场地叫法认三种（Desert / DesertFestival / Temp）")
    for _loc in ("Desert", "DesertFestival", "Temp"):
        ck(f"②'{_loc} 都算沙漠节场地", "钓鱼小游戏中" in M._festival_activity_hint("BobberBar", _loc),
           M._festival_activity_hint("BobberBar", _loc)[:80])

    print("\n③ 解锁但**人在鹈鹕镇钓鱼** ⇒ 「钓鱼游戏进行中」不出现（第二个真机症状）")
    _stub(unlocked=True, day=17)
    for _loc in ("Town", "FarmHouse", "Farm", "Mountain", "Forest", "Beach"):
        _h = M._festival_activity_hint("BobberBar", _loc)
        ck(f"③{_loc} 钓鱼 ⇒ 不说「沙漠节钓鱼游戏进行中」", _h == "", _h[:120])
        ck(f"③{_loc} 钓鱼 ⇒ 也不夹 17 号「优先换…」那句", "优先换" not in _h, _h[:160])
    ck("③…但节日场地的**就绪屏**不受这条影响（那屏只在节日图弹）",
       "就绪确认" in M._festival_activity_hint("ReadyCheckDialog", "Town"),
       M._festival_activity_hint("ReadyCheckDialog", "Town")[:80])
    ck("③读不到当前位置（空串）⇒ 也**不说**在钓鱼比赛（宁可不说）",
       M._festival_activity_hint("BobberBar", "") == "",
       M._festival_activity_hint("BobberBar", "")[:120])

    print("\n④ 同一把尺子对别的节日/别的场地也成立（冰雪节：Forest 出、Town 不出）")
    _stub(unlocked=True, season="winter", day=8)
    ck("④冰雪节 + Forest 钓鱼 ⇒ 出现节日钓鱼提示",
       "钓鱼小游戏中" in M._festival_activity_hint("BobberBar", "Forest"),
       M._festival_activity_hint("BobberBar", "Forest")[:80])
    ck("④冰雪节 + Town 钓鱼 ⇒ 不出现（同一把尺子）",
       M._festival_activity_hint("BobberBar", "Town") == "",
       M._festival_activity_hint("BobberBar", "Town")[:80])
    _stub(unlocked=False, season="winter", day=8)
    ck("④冰雪节没解锁的是**沙漠**、不是 Forest ⇒ Forest 照出（闸只认那把尺子，不认节日名）",
       "钓鱼小游戏中" in M._festival_activity_hint("BobberBar", "Forest"),
       M._festival_activity_hint("BobberBar", "Forest")[:80])

    print("\n⑤ 日历那行「今天沙漠节」作为**事实**照留（别把事实一起删掉）")
    _stub(unlocked=False, day=17)
    _f = calendar_data.get_festival_today("spring", 17)
    ck("⑤没解锁也照样查得到「今天沙漠节」（那是日期事实，不归玩法闸管）",
       bool(_f) and "沙漠节" in (_f.get("name") or ""), str(_f)[:120])
    ck("⑤…它的场地写的是 Desert（导航名，跟解锁检查同一套）",
       (_f or {}).get("map") == "Desert", str((_f or {}).get("map")))

    print("\n⑤' 非节日日 ⇒ 两条都不出现（闸不是「永远给」）")
    _stub(unlocked=True, season="spring", day=2)
    ck("⑤'平常一天 + 钓鱼小游戏 ⇒ 不给沙漠节引导",
       M._festival_activity_hint("BobberBar", "DesertFestival") == "",
       M._festival_activity_hint("BobberBar", "DesertFestival")[:80])
    ck("⑤'平常一天 + 人在农场 ⇒ 也没有玩法推荐",
       M._festival_recommend_hint("Farm") == "", M._festival_recommend_hint("Farm")[:80])


def sec_source():
    print("\n⑥ 源码钉（闸真的在代码里，不只在文档里）")
    _src = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8").read()
    _tree = ast.parse(_src)
    _fns = {n.name: n for n in ast.walk(_tree) if isinstance(n, ast.FunctionDef)}

    _fa = _fns["_festival_activity_hint"]
    _args = [a.arg for a in _fa.args.args]
    ck("⑥`_festival_activity_hint` 收 `loc_name` 参数（判地点必须拿得到位置）",
       "loc_name" in _args, str(_args))

    def _code(fn):
        body = list(fn.body)
        if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        txt = "\n".join(ast.get_source_segment(_src, n) or "" for n in body)
        return "\n".join(ln.split("#", 1)[0] for ln in txt.splitlines())

    _fa_c = _code(_fa)
    ck("⑥`_festival_activity_hint` 里走 `_fest_unlocked(`（没解锁就不注入）",
       "_fest_unlocked(" in _fa_c, "没看到解锁闸")
    ck("⑥`_festival_activity_hint` 里走 `_fest_venue_ok(`（钓鱼提示要判地点）",
       "_fest_venue_ok(" in _fa_c, "没看到地点闸")
    _i_unlock = _fa_c.find("_fest_unlocked(")
    _i_guide = _fa_c.find("FESTIVAL_GUIDE")
    ck("⑥解锁闸在取玩法文本**之前**（不然还是会拼出来）",
       -1 < _i_unlock < _i_guide, f"unlock@{_i_unlock} guide@{_i_guide}")

    _fu = _code(_fns["_fest_unlocked"])
    ck("⑥`_fest_unlocked` 用的是 `navigation._map_go_unlock_check`（跟 map go 同一把尺子）",
       "_map_go_unlock_check" in _fu, _fu[:160])
    ck("⑥`_fest_unlocked` 没另编一张解锁表（代码里不许出现 LOCKED_MAPS / bus 之类的自造名册）",
       "LOCKED_MAPS" not in _fu, _fu[:160])

    _fv = _code(_fns["_fest_venue_names"])
    ck("⑥`_fest_venue_names` 认「DesertFestival」这种内部图叫法", '"Festival"' in _fv, _fv[:160])
    ck("⑥`_fest_venue_names` 认「Temp」（其它节日的内部图）", '"Temp"' in _fv, _fv[:160])

    _bs = _code(_fns["_build_state_strip"])
    ck("⑥`_build_state_strip` 真把 `loc_name` 传给 `_festival_activity_hint`",
       "_festival_activity_hint(" in _bs and "loc_name" in _bs.split("_festival_activity_hint(")[1][:80],
       _bs.split("_festival_activity_hint(")[1][:80] if "_festival_activity_hint(" in _bs else "没调用")

    _fr = _code(_fns["_festival_recommend_hint"])
    ck("⑥`_festival_recommend_hint`（17 号那句「强烈推荐」也在里面）也过解锁闸",
       "_fest_unlocked(" in _fr, _fr[:160])


def main():
    try:
        sec_gate()
        sec_source()
    finally:
        M._festival_now_data = _REAL["now"]
        M.navigation._map_go_unlock_check = _REAL["unlock"]
        M._host_name = _REAL["host_name"]
        M._FEST_RECOMMEND_KEY.update(_REAL["recommend_key"])

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 沙漠节那两条播报又漏了闸：没解锁地形/没判地点就会瞎播。别改钉子去凑。")
        sys.exit(1)
    print("✅ 全绿：没解锁沙漠 ⇒ 不注入沙漠节玩法；钓鱼提示只在节日场地里说；日历那行事实照留。")
    sys.exit(0)


if __name__ == "__main__":
    main()
