# -*- coding: utf-8 -*-
"""🗜️ 工具收编离线冒烟：证明收编者**经新路可达且真能被调度**。
不碰游戏：把 api 打桩，只验 ①dispatch 查得到 ②返回值非"未知查询" ③状态条照附着
④**文案不再指向隐藏域名**（漏一处 = AI 照旧文案调隐藏名 = 当场卡死）。

覆盖两次收编：
  · 2026-09-11 20→17：advance_story→menu advance · profile/which_role→check
  · 2026-10-01 18→16：session→settings · cabin→scene/farm/daily/check（cook 搬进 daily）
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

# ═══════════════════════════════════════════════════════════════════════
# 8) 🗜️ 2026-10-01 收编（18→16）：session→settings · cabin→scene/farm/daily/check
# ═══════════════════════════════════════════════════════════════════════
# ⚠️ 这一批的判据**不在"函数引用得到吗"**，而在 `domain_selftest._SUBSUMED_DOMAINS` 那张
#    **逐 op 审计表**（撤一个域必须每条 op 写清替代路）。这里锁的是**外面看得见的那几处**：
#    工具在不在顶层 / 函数还在不在 / 新家调得通吗 / 文案有没有还指着旧域名。
import domain_selftest as DS          # noqa: E402  （只为读那张审计表）

for n in ("session", "cabin"):
    case(f"{n} 已移出顶层 keep-set", "ok" if n not in M._KEEP_TOOLS else "STILL", "ok")
    case(f"   {n}() 函数仍在（兼容旧前端，只是不给 AI 直调）",
         "ok" if callable(getattr(M, n, None)) else "MISSING", "ok")
    case(f"   {n} 不在 _KNOWN_SUBSUMED（不许走后门）",
         "ok" if n not in DS._KNOWN_SUBSUMED else "BACKDOOR", "ok")
    case(f"   {n} 在 _SUBSUMED_DOMAINS 审计表里",
         "ok" if n in DS._SUBSUMED_DOMAINS else "MISSING", "ok")

# 8a) session 三条 op 真能通过 settings 调到
case("settings ops=session_status", M.settings(ops="session_status"), "会话缓冲")
case("settings ops=会话状态（中文别名）", M.settings(ops="会话状态"), "会话缓冲")
case("settings ops=session_set 未知项会点名合法项",
     M.settings(ops="session_set", kw={"setting": "bogus", "value": "1"}), "未知设置项")

# 8a-bis) 🧹 死旋钮（恒 2026-10-01：「服务器应该是实时生成日志的吧…那确实就是这个 max turns 而已了」）
#   ⚠️ 判据是"**设了会不会真有事发生**"：`auto_export`/`include_npc` 在代码里**没有读取点**，
#      留着它们只会让 AI 拿到一句「✅ 改好了」的**假成功**（本项目最恨的那类）。
case("session_set 不再收 auto_export（死旋钮已删）",
     M.settings(ops="session_set", kw={"setting": "auto_export", "value": "false"}), "未知设置项")
case("session_set 不再收 include_npc（死旋钮已删）",
     M.settings(ops="session_set", kw={"setting": "include_npc", "value": "false"}), "未知设置项")
#   ⚠️ 断言只认两个字：**"实时档案"** —— 空缓冲和 jsonl 两条分支都会这么说，
#      两条都是"没写文件就如实交代"（旧版是一句"已导出 N 条"的假回执）。
case("settings session_export：没写文件时**如实交代**（不是假回执）",
     M.settings(ops="session_export"), "实时档案")

# 8b) cook 的新家是 daily（恒 2026-10-01 指定：做饭是吃的上游）
case("daily dispatch 里有 cook", inspect.getsource(M.daily), '"cook": cook')
case("daily ops=cook 缺 recipe_name 会点名参数（**不真做饭、也不走位**）",
     M.daily(ops="cook"), "recipe_name")

# 8c) 文案：不许再有"调隐藏域名"的指引（09-11 立的规矩：漏一处 = AI 照旧文案当场卡死）
# ⚠️ **只扫字符串字面量**（AST），不扫整份源码 —— 注释里的旧域名是**历史**，该留着
#    （本项目的老规矩：grep 分不清注释与字符串，审计要扫 return 的字符串）。
import ast as _ast    # noqa: E402

_SRC = inspect.getsource(M)
_TREE = _ast.parse(_SRC)
# ⚠️ **docstring 不算 AI 文案** —— 按项目自己的规矩，日期/人名/根因/沿革**就该放 docstring**
#    （[[no-dev-history-in-ai-facing-copy-2026-09-23]]：报错/回执/help 只写"要什么+下一步"）。
#    所以扫之前先把 docstring 那些节点挑出去，否则"历史的旧域名"会被当成"还在指引 AI"。
_DOC_NODES = set()
for _fn in _ast.walk(_TREE):
    if isinstance(_fn, (_ast.Module, _ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
        _b = getattr(_fn, "body", None)
        if _b and isinstance(_b[0], _ast.Expr) and isinstance(_b[0].value, _ast.Constant) \
                and isinstance(_b[0].value.value, str):
            _DOC_NODES.add(id(_b[0].value))
_STRINGS = [n.value for n in _ast.walk(_TREE)
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) and id(n) not in _DOC_NODES]
for _bad in ("cabin ops=", "cabin collect", "cabin statue", "cabin pickup"):
    _hit = [s for s in _STRINGS if _bad in s]
    case(f"**文案**里不再有「{_bad}」（注释不算）", "ok" if not _hit else f"STILL:{_hit[0][:60]}", "ok")
for _d in ("cabin", "session"):
    case(f"_DOMAIN_GUIDES 里没有 {_d} 键（留壳=help 反查成隐藏域）",
         "ok" if _d not in M._DOMAIN_GUIDES else "STILL", "ok")
case("别名「会话」指到 settings", M._HELP_ALIAS.get("会话") or "?", "settings")
case("别名「家」指到 map", M._HELP_ALIAS.get("家") or "?", "map")

print("\n" + ("❌ 失败: " + ", ".join(FAIL) if FAIL else "✅ 全过"))
sys.exit(1 if FAIL else 0)
