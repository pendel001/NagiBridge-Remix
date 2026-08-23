"""
🛋️ 计划引擎（纯逻辑，不碰 HTTP）— 2026-08-14「全自动一天」收官
供 nagi_mcp_server.py 的 `plan` 域工具 + `_autopilot_loop` 调度器调用。

🚫 已退役（2026-08-17 恒：计划模式暂不实现，AI 连续跑脚本的自动化取消）。
   本文件整体保留作存档（git 有备份）——nagi_mcp_server.py 仍 import 它（保持存档代码可解析），
   但 plan 工具未注册、settings mode=plan 失效、调度器不跑计划状态机，全部逻辑不再被调用。
   若将来恢复计划模式：恢复 import + 重新注册 plan 工具 + settings mode 放行 + 启动 _plan_load_state()。


职责：
- SCRIPT_META：每个自动化脚本的结束标志分类（success/recoverable/fatal）+ 限制参数
- parse_spec：行式计划字符串 → 任务列表（"水浇 | 钓鱼 --max-casts 30 | 炸矿 --target 80 until 14:00"）
- validate_tasks：不会自己结束的脚本（炸矿/钓鱼/冲层）必须写 until 几点 或 限制参数，否则拒收
- classify_end：脚本退出后按输出+返回码分类结局 → success / recoverable / fatal
- festival_start_hhmm：今天节日的开始游戏时间（calendar_data）
- plan.json 读写：{state, planned_for, end_time, tasks, abort_reason}

`python scripts/plan_engine.py --selftest` 离线自检（不连游戏）。
"""

import json
import os
import re
import shlex
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PLAN_FILE = os.path.join(SCRIPT_DIR, "plan.json")

# calendar_data 只在真正需要节日时间时 import（独立跑也兼容）
try:
    import calendar_data
except Exception:
    calendar_data = None

# ═══════════════════════════════════════════
#  脚本元数据
#  success   正常完成 → 继续下一个任务
#  recoverable 可恢复提前结束（体力/背包/没了可干）→ 暂停计划，AI 决策后 plan resume
#  fatal     高风险结束（撤退/失败/炸弹用完）→ 中止整个计划，回自主模式
#  limit_args 该脚本会自己结束的参数（--target/--max-casts/--max-minutes/--count/--cycles）
#             （标记的每个子串命中一个即可算"有自我终止条件"）
# ═══════════════════════════════════════════
SCRIPT_META = {
    "fish_run": {
        "success": ["=== fish run complete ==="],
        "recoverable": ["背包", "体力", "stamina", "菜单", "太晚", "2300"],
        "fatal": [],
        "limit_args": ["--max-casts"],
        "hint": "钓鱼要写清几次（--max-casts N）或 until 几点，否则跑起来停不下来",
    },
    "bomb_mine": {
        "success": ["===BOMB_SUMMARY===", "=== 炸矿结束 ==="],
        "recoverable": ["背包"],
        "fatal": ["撤退", "炸弹用完了", "❌", "先去买"],
        "limit_args": ["--target"],
        "hint": "炸矿要写 --target 层数 或 until 几点（不会自己停，会一直冲/撤）",
    },
    "bomb_escort": {
        "success": ["=== 协同模式结束 ==="],
        "recoverable": [],
        "fatal": ["撤退", "❌"],
        "limit_args": ["--max-minutes"],
        "hint": "协同炸矿要写 --max-minutes 分钟 或 until 几点",
    },
    "bomb_volcano": {
        "success": ["=== 火山骑行结束 ==="],
        "recoverable": ["背包", "血"],
        "fatal": ["撤退", "❌", "user离开"],
        "limit_args": ["--max-minutes"],
        "hint": "火山炸矿要写 --max-minutes 分钟 或 until 几点（需 user 在矿井陪同）",
    },
    "mine_run": {
        "success": ["=== 冲层结束 ===", "=== 刷矿结束 ==="],
        "recoverable": ["体力", "stamina", "没有食物"],
        "fatal": ["撤退", "❌"],
        "limit_args": ["--target", "--cycles"],
        "hint": "冲层/刷矿要写 --target 层数 / --cycles 轮 或 until 几点",
    },
    "farm_row": {
        "success": ["=== done ==="],
        "recoverable": ["stamina low", "stopping"],
        "fatal": [],
        "limit_args": [],
        "hint": "",
    },
    "chop_trees": {
        "success": ["Chopped"],
        "recoverable": ["No more trees", "体力", "stamina"],
        "fatal": [],
        "limit_args": ["--count"],
        "hint": "",
    },
    "clear_area": {
        "success": ["All clear"],
        "recoverable": ["Still"],
        "fatal": [],
        "limit_args": [],
        "hint": "",
    },
    "water_crops": {
        "success": ["Done!", "Nothing to water"],
        "recoverable": ["Still"],
        "fatal": [],
        "limit_args": [],
        "hint": "",
    },
    "scythe_crops": {
        "success": ["✅ 完成", "收获结果"],
        "recoverable": ["没有可收"],
        "fatal": ["❌", "没有镰刀", "连不上"],
        "limit_args": [],
        "hint": "",
    },
    "harvest": {
        "success": ["=== harvest done ===", "nothing to harvest"],
        "recoverable": ["背包快满了", "harvest aborted"],
        "fatal": ["❌", "cannot connect", "game not ready"],
        "limit_args": [],
        "hint": "",
    },
    "nav": {
        "success": ["✅ 到达", "已经在"],
        "recoverable": [],
        "fatal": ["❌", "失败", "没找到"],
        "limit_args": [],
        "hint": "",
    },
}


def _parse_time(s: str):
    """时间 token → 游戏时间整数（"14:00"/"1400"/"9:00"/"下午2点" → 1400/1400/900/1400）。
    解析不了返回 None。"""
    s = str(s).strip().lower().replace("点", ":")
    pm = 0
    for mark in ("下午", "晚上", "夜"):
        if mark in s:
            pm = 1200
            s = s.replace(mark, "")
            break
    s = s.replace("上午", "").replace("凌晨", "").replace("中午", "")
    if ":" in s:
        h, _, m = s.partition(":")
        try:
            h = int(h)
            m = int(m or 0)
        except ValueError:
            return None
        if pm:
            h = (h + 12) % 24
        return h * 100 + m
    s = s.strip()
    if not s.isdigit():
        return None
    if len(s) == 4:
        return int(s)          # "1400" → 1400
    if len(s) == 3:
        return int(s)          # "600"→600(6:00) / "900"→900(9:00)，别乘10
    if len(s) == 1:
        return int(s) * 100    # "9" → 900
    return None


def parse_spec(spec: str):
    """行式计划字符串 → (tasks, errors)。
    任务间用 | 或换行分隔；首 token=脚本名；`until HH:MM`/`到HH:MM`/`至HH:MM` 提取为截止游戏时间；
    其余 token 为脚本参数。返回 [{script,args,until,status,result,job_id,note}] + 错误提示列表。"""
    if not spec or not str(spec).strip():
        return [], ["计划是空的——写点脚本，比如 plan write \"water_crops | fish_run --max-casts 30\""]
    tasks, errors = [], []
    segs = [s for s in re.split(r"[|\n]", str(spec)) if s.strip()]
    for idx, seg in enumerate(segs, 1):
        try:
            toks = shlex.split(seg.strip())
        except Exception:
            toks = seg.strip().split()
        if not toks:
            continue
        script = toks[0].strip()
        args, until = [], None
        i = 1
        # 允许 until 前置（"到14:00 钓鱼 ..."）：首 token 是时间标记 → 脚本后移一位
        m0 = re.match(r"^(?:until|到|至)(?:=)?(.+)$", script, re.I)
        if m0 and not script.startswith("-"):
            until = _parse_time(m0.group(1))
            if until is not None and i < len(toks):
                script = toks[i]
                i += 1
            else:
                until = None
        if script.endswith(".py"):
            script = script[:-3]
        while i < len(toks):
            t = toks[i]
            if t in ("until", "到", "至", "up") and i + 1 < len(toks):
                until = _parse_time(toks[i + 1])
                i += 2
                continue
            # until=14:00 / until14:00 / 到14:00 / 至14:00 粘连形式
            m = re.match(r"^(?:until|到|至)(?:=)?(.+)$", t, re.I)
            if m and not t.startswith("-"):
                until = _parse_time(m.group(1))
                i += 1
                continue
            args.append(t)
            i += 1
        if until is None:
            # 只有"不会自己停"的脚本（limit_args 非空 / 未知脚本）才提醒写 until；有限任务不用
            _meta = SCRIPT_META.get(script)
            if _meta is None or _meta.get("limit_args"):
                errors.append(f"⚠️ 任务{idx}「{script}」没写 until 截止时间——{_meta.get('hint') if _meta else '未知脚本'}；不加的话只能靠它自己停或兜底1点")
        tasks.append({
            "script": script, "args": args, "until": until,
            "status": "pending", "result": None, "job_id": None, "note": "",
        })
    return tasks, errors


def validate_tasks(tasks):
    """校验任务是否可执行：不会自己结束的脚本必须有 until 或限制参数。
    返回 (ok, msgs)。ok=False 时整个计划拒收（AI 改完再 plan write）。"""
    if not tasks:
        return False, ["计划是空的"]
    msgs, fatal = [], False
    for i, t in enumerate(tasks, 1):
        meta = SCRIPT_META.get(t["script"])
        if meta is None:
            if not t["until"]:
                fatal = True
                msgs.append(f"❌ 任务{i}「{t['script']}」不在已知脚本表，且没写 until 截止——给不出停止保证，拒收")
            else:
                msgs.append(f"⚠️ 任务{i}「{t['script']}」是未知脚本，只能靠 until {t['until']} 兜底")
            continue
        limit_hit = any(any(a == la or a.startswith(la + "=") for a in t["args"])
                        for la in meta.get("limit_args", []))
        if meta.get("limit_args") and not limit_hit and not t["until"]:
            fatal = True
            msgs.append(f"❌ 任务{i}「{t['script']}」{meta['hint']}")
        elif not meta.get("limit_args") and not t["until"]:
            msgs.append(f"ℹ️ 任务{i}「{t['script']}」是有限任务（干完就停），不用写 until")
    return (not fatal), msgs


def classify_end(script: str, returncode, output: str):
    """脚本结束后分类结局：
    fatal（rc≠0 或撤退/失败/炸弹用完）→ 中止计划；success（正常标志）→ 下一个；
    recoverable（体力/背包/没可干）→ 暂停计划；unknown → recoverable（保守暂停）。"""
    meta = SCRIPT_META.get(script, {})
    out = output or ""
    if returncode not in (0, None):
        return "fatal"
    for m in meta.get("fatal", []):
        if m in out:
            return "fatal"
    for m in meta.get("success", []):
        if m in out:
            return "success"
    for m in meta.get("recoverable", []):
        if m in out:
            return "recoverable"
    return "recoverable"


def festival_start_hhmm(season, day):
    """今天节日开始游戏时间（如 花舞节 → 900；全天/解析失败 → 600）。非节日日返回 None。"""
    try:
        if calendar_data is None:
            return None
        key = (str(season).lower(), int(day))
        detail = calendar_data.FESTIVALS.get(key)
        if not detail:
            return None
        after = detail.split("—")[-1]
        m = re.search(r"(\d{1,2}):(\d{2})", after)
        if m:
            return int(m.group(1)) * 100 + int(m.group(2))
        return 600  # "全天" 等
    except Exception:
        return 600


# ═══════════════════════════════════════════
#  plan.json 持久化
#  state ∈ idle|waiting_day|running|paused|done|aborted
#  mode 只存 settings.json（单一真源）；plan.json 存计划内容与执行状态
# ═══════════════════════════════════════════
_PLAN_DEFAULTS = {
    "state": "idle",
    "planned_for": None,
    "end_time": None,
    "tasks": [],
    "abort_reason": None,
}


def new_plan() -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in _PLAN_DEFAULTS.items()}


def load_plan() -> dict:
    p = new_plan()
    try:
        if os.path.exists(PLAN_FILE):
            d = json.load(open(PLAN_FILE, encoding="utf-8"))
            if isinstance(d, dict):
                for k in _PLAN_DEFAULTS:
                    if k in d:
                        p[k] = d[k]
    except Exception:
        pass
    return p


def save_plan(p: dict):
    try:
        with open(PLAN_FILE, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def plan_summary(p: dict) -> str:
    """计划一行摘要（状态条用）：状态 + 任务数 + 当前任务。"""
    tasks = p.get("tasks", [])
    st = p.get("state", "idle")
    state_names = {"idle": "待命", "waiting_day": "等明日", "running": "执行中",
                   "paused": "已暂停", "done": "已完成", "aborted": "已中止"}
    cur = next((t for t in tasks if t.get("status") == "running"), None)
    done_n = len([t for t in tasks if t.get("status") in ("done",)])
    s = f"📋 {state_names.get(st, st)} {len(tasks)}任务({done_n}完成)"
    if cur:
        s += f" → {cur['script']}"
    return s


# ═══════════════════════════════════════════
#  自检
# ═══════════════════════════════════════════
def _selftest():
    try:  # Windows GBK 终端兜底
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    fails = []

    def check(name, got, want):
        if got != want:
            fails.append(f"  ❌ {name}: got {got!r}, want {want!r}")

    # parse_spec
    t, errs = parse_spec("water_crops | fish_run --location Mountain --max-casts 30 | bomb_mine --target 80 until 14:00")
    check("parse 任务数", len(t), 3)
    check("parse 脚本", [x["script"] for x in t], ["water_crops", "fish_run", "bomb_mine"])
    check("parse args", t[1]["args"], ["--location", "Mountain", "--max-casts", "30"])
    check("parse until", t[2]["until"], 1400)
    check("parse until 缺省警告", len([e for e in errs if "没写 until" in e]), 1)  # 只有 fish（有限任务 water 不用）

    t2, _ = parse_spec("mine_run --target 40 | 到14:00 钓鱼 --max-casts 10")
    check("parse 中文到", t2[1]["until"], 1400)
    check("parse 中文脚本名", t2[1]["script"], "钓鱼")

    t3, _ = parse_spec("bomb_mine --target 80 至17点")
    check("parse 至17点", t3[0]["until"], 1700)

    # validate
    ok, _ = validate_tasks([{"script": "water_crops", "args": [], "until": None}])
    check("validate 有限任务", ok, True)
    ok, msgs = validate_tasks([{"script": "bomb_mine", "args": [], "until": None}])
    check("validate 无限制炸矿拒收", ok, False)
    check("validate 提示", any("炸矿" in m for m in msgs), True)
    ok, _ = validate_tasks([{"script": "bomb_mine", "args": ["--target", "80"], "until": None}])
    check("validate --target 通过", ok, True)
    ok, _ = validate_tasks([{"script": "fish_run", "args": [], "until": 1400}])
    check("validate until 通过", ok, True)
    ok, _ = validate_tasks([{"script": "unknown_script", "args": [], "until": None}])
    check("validate 未知脚本拒收", ok, False)
    # nav 任务
    ok, _ = validate_tasks([{"script": "nav", "args": ["姜岛小屋(门口)"], "until": None}])
    check("validate nav 有限任务", ok, True)
    ok, _ = validate_tasks([{"script": "scythe_crops", "args": ["--radius", "20"], "until": None}])
    check("validate scythe_crops", ok, True)

    # classify_end
    check("classify 成功", classify_end("water_crops", 0, "Done! Total watered: 12"), "success")
    check("classify 无可浇", classify_end("water_crops", 0, "Nothing to water!"), "success")
    check("classify 撤退", classify_end("bomb_mine", 0, "🏁 === 炸矿结束 ===\n撤退原因: 卡死"), "fatal")
    check("classify rc!=0", classify_end("fish_run", 1, "whatever"), "fatal")
    check("classify 体力", classify_end("fish_run", 0, "🎒 体力低, stop"), "recoverable")
    check("classify 未知", classify_end("fish_run", 0, "莫名其妙退出了"), "recoverable")
    check("classify 背包", classify_end("fish_run", 0, "🎒 背包满了，停止钓鱼"), "recoverable")
    check("classify nav 到达", classify_end("nav", 0, "🗺️ 导航 Farm → IslandWest（3 段）\n✅ 到达 IslandWest"), "success")
    check("classify nav 失败", classify_end("nav", 0, "🗺️ 导航 Farm → IslandWest\n❌ 到 IslandWest 失败"), "fatal")
    check("classify nav 未解锁", classify_end("nav", 0, "❌ IslandWest 未解锁，不能 map_go"), "fatal")
    check("classify scythe 完成", classify_end("scythe_crops", 0, "🌾 收获结果: 5 株\n✅ 完成"), "success")
    check("classify scythe 无镰刀", classify_end("scythe_crops", 0, "❌ 背包里没有镰刀"), "fatal")

    # festival
    check("festival 花舞节", festival_start_hhmm("spring", 24), 900)
    check("festival 夜市", festival_start_hhmm("winter", 15), 1700)
    check("festival 鱿鱼节全天", festival_start_hhmm("winter", 12), 600)
    check("festival 鳟鱼", festival_start_hhmm("summer", 20), 610)
    check("festival 非节日", festival_start_hhmm("spring", 1), None)

    # _parse_time
    check("time 14:00", _parse_time("14:00"), 1400)
    check("time 1400", _parse_time("1400"), 1400)
    check("time 9:00", _parse_time("9:00"), 900)
    check("time 900", _parse_time("900"), 900)        # 三位数=6:00/9:00，不是 *10
    check("time 600", _parse_time("600"), 600)
    check("time 下午2点", _parse_time("下午2点"), 1400)
    check("time 晚上10点", _parse_time("晚上10点"), 2200)

    print("── plan_engine selftest ──")
    if fails:
        print("\n".join(fails))
        print(f"❌ {len(fails)} 项失败")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(_selftest())
