# -*- coding: utf-8 -*-
"""🗜️ 2026-09-11 工具收编（20→17）离线冒烟：证明三个收编者经域 op 可达且真能被调度。
不碰游戏：把 api 打桩，只验 ①dispatch 查得到 ②返回值非"未知查询" ③状态条照附着。
用法: PYTHONIOENCODING=utf-8 python scripts/_toolmerge_selftest.py
"""
import io, os, sys
if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M

FAIL = []

# ── 打桩：不进游戏 ──
# ⚠️ 2026-09-16：`profile()` 改走 **`_ai_get`** 了（查 AI 自己；原来错走 `_get`=房主，见该函数注释）。
#    桩**两个都要打** —— 只打 `_get` 的话 profile 会真去连 7843，而这条自测号称"不碰游戏"。
_SKILLS_NOT10 = {"farming": 10, "fishing": 7, "foraging": 5, "mining": 4, "combat": 3}
_SKILLS_ALL10 = {k: 10 for k in ("farming", "fishing", "foraging", "mining", "combat")}


def _stub_profile_side(path, *a, **k):
    return ({"ok": True, "name": "轮回", "skills": _SKILLS_NOT10, "professions": [11]}
            if "profile" in path else {"ok": False, "error": "stub"})


M.api._get = _stub_profile_side
M.api._ai_get = _stub_profile_side
M.api.which_role = lambda: {"ok": True, "ai": {"name": "轮回", "port": 7843}, "host": {"name": "恒", "port": 7842}}
M._with_state = lambda text, force_full=False: text   # 隔离状态注入，只看 op 本体

def case(label, got, want_in):
    ok = want_in in got
    print(("  ✅ " if ok else "  ❌ ") + label + f" → {got.splitlines()[0][:70]}")
    if not ok:
        FAIL.append(label)

# 1) 三个收编者都还在模块里（函数照旧注册，只是不给 AI 直调）
for n in ("advance_story", "which_role", "profile"):
    case(f"{n}() 函数仍在", "ok" if callable(getattr(M, n, None)) else "MISSING", "ok")

# 2) check 域新 ops 真能调度
case('check(what="profile")', M.check("profile"), "轮回")
case('check(what="role")   ', M.check("role"), "7843")

# 3) 中文别名
case('check(what="技能")', M.check("技能"), "技能等级")

# 3b) 🎓 精通进度条并进 profile（恒 2026-09-16）：**技能全满才出现**——
#     未满级时 MasteryExp 不涨，画个空条只会误导，直接不显示。
M.api.mastery = lambda: {"ok": True, "who": "轮回", "level": 4, "exp": 74365,
                         "expThisLevel": 4365, "expThisLevelNeed": 30000,
                         "levelsSpent": 4, "unspent": 0}
M.api._get = M.api._ai_get = lambda path, *a, **k: (
    {"ok": True, "name": "轮回", "skills": _SKILLS_ALL10, "professions": [11]}
    if "profile" in path else {"ok": False, "error": "stub"})
# ⚠️ 数字要跟**游戏经验条**一致（本级内/本级总需）= 4365/30000，
#    不是拿总量算的 74365/100000（恒当场指出过）。
case("技能全满 → 出精通条(游戏同款 xxx/xxx)", M.check("profile"), "4365/30000")

M.api._get = M.api._ai_get = _stub_profile_side   # 退回"未满级"那套桩
if "🏆" in M.check("profile"):
    print("  ❌ 技能未满 → 不该显示精通条")
    FAIL.append("技能未满不该显示精通条")
else:
    print("  ✅ 技能未满 → 不显示精通条")

# 4) 未知 what 的报错要列上新 op（防 AI 猜不中时找不到路）
case("未知 what 报错列 profile/role", M.check("nope"), "profile")

# 5) menu advance 直指同一函数（收编的前提）
import inspect
src = inspect.getsource(M.menu)
case("menu dispatch 的 advance 指向 advance_story", src, '"advance": advance_story')

# 6) keep-set 里已经没有它们
for n in ("advance_story", "which_role", "profile"):
    case(f"{n} 已移出顶层 keep-set", "ok" if n not in M._KEEP_TOOLS else "STILL", "ok")

# 7) 引导文案不再把 AI 指向隐藏工具名
case("状态条剧情行点名 menu advance", inspect.getsource(M._build_state_strip), "menu advance 继续")
case("   其自身文案不再说 advance_story", "再调 advance_story" not in inspect.getsource(M.advance_story) and "ok" or "STILL", "ok")
case("   其自身文案不再说 menu_click", "menu_click" not in inspect.getsource(M.advance_story) and "ok" or "STILL", "ok")

print("\n" + ("❌ 失败: " + ", ".join(FAIL) if FAIL else "✅ 全过"))
sys.exit(1 if FAIL else 0)
