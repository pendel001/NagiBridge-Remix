# -*- coding: utf-8 -*-
"""🧪 「域 op 内层不建状态条」离线自测（2026-09-12 恒拍板 —— 修 B）——不碰游戏。

**治的病**（一族，全是同一个根）：域 op 都是 `return _with_state(正文)`，可这层产物会被 `_ops_run`
**整条砍掉**。以前是"照建、照丢"，于是内层把**带副作用的消费**白白吃掉了：
  · `_is_new_day()` 吃掉 `_last_full_date` → 那天 🌅晨报/🎲运势再也不出现（只有 `check status` 能补）
  · `_statue_reminder` 置 `shown=True` → 当天不再提醒摸雕像（**不可逆**）
  · `_gather_state(consume_events=True)` → 📰 小新闻 / 警报被内层 drain，AI 永远看不到
  · `_machine_ready_hint` / `_plan_drain_notices` / `_chat_phase_line` 同族
  · 外加每多一个 op 就多跑一次全量 `_gather_state`（3 op = 拉 3 次全丢 3 次）

**为什么必须走 `_ops_run` 端到端测**（恒 2026-09-11 定的规矩）：单测 `_with_state()`/`_sit_hint()`
永远全绿 —— 病在"**两层之间**"，只有把内层那层真的套起来才看得见。本文件的核心就是 case C/D。

跑法： `PYTHONIOENCODING=utf-8 python _state_inner_selftest.py`
"""
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAILED = []
GATHERS = []          # 每次 `_gather_state` 被调就 append 一条（跨线程也安全）


def _check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


def _install_stubs(M):
    """把 `_with_state` 的外围全部换成本地假件：本自测只关心**分层**，不关心状态条画成啥样。"""
    def _fake_gather():
        GATHERS.append(1)
        return {"player": {}, "location": {}, "time": {}, "inventory": []}
    M._gather_state = _fake_gather
    M._build_state_strip = lambda data, full=True, morning="": "《假状态条》"
    M._fetch_farm_report = lambda: {}
    M._build_morning_report = lambda fr: ""
    M._bg_activity_line = lambda: ""
    M._plan_drain_notices = lambda: ""
    M._maybe_ice_fishing_auto = lambda data: ""
    M._maybe_egg_run_auto = lambda data: ""
    M._heartbeat_line = lambda data: ""


def case_a(M):
    print("A) 内层（_OPS_INNER>0）：原样返回、**不拉状态、不建条**")
    n0 = len(GATHERS)
    M._OPS_INNER["n"] = 1
    try:
        out = M._with_state("正文")
    finally:
        M._OPS_INNER["n"] = 0
    _check("原样返回正文（没被加料）", out == "正文", repr(out[:40]))
    _check("没拉 _gather_state", len(GATHERS) == n0, f"拉了 {len(GATHERS) - n0} 次")


def case_b(M):
    print("B) 外层（_OPS_INNER==0）：照常建条、拉一次")
    n0 = len(GATHERS)
    out = M._with_state("正文")
    _check("正文 + 分隔符 + 状态条", "正文" in out and M._STATE_SEP in out and "《假状态条》" in out)
    _check("拉了**恰好 1 次** _gather_state", len(GATHERS) - n0 == 1, f"拉了 {len(GATHERS) - n0} 次")


def case_c(M):
    print("C) 🎯 端到端（走 _ops_run，真域 op 的形态）：内层条不出现、全程只拉 1 次")
    n0 = len(GATHERS)
    dispatch = {"x": lambda: M._with_state("干完了"), "y": lambda: M._with_state("也干完了")}
    body = M._ops_run("x y", dispatch, {})
    _check("_ops_run 产物**不带**状态条", M._STATE_SEP not in body, repr(body[:60]))
    _check("_ops_run 期间**一次都没拉**状态", len(GATHERS) == n0, f"拉了 {len(GATHERS) - n0} 次")
    final = M._with_state(body)
    _check("外层补上后：条回来了", M._STATE_SEP in final and "《假状态条》" in final)
    _check("两个 op 的正文都在", "干完了" in final and "也干完了" in final)
    _check("全程**只拉 1 次**（2 个 op 不再各拉一次）", len(GATHERS) - n0 == 1,
           f"拉了 {len(GATHERS) - n0} 次（修前是 3 次：2 内层 + 1 外层）")


def case_d(M):
    print("D) 🎯 一次性注入不被内层吃掉（本病的真身：`_is_new_day`）")
    stale = ("spring", 99, 1)          # 假装"上次记录是别的日子" ⇒ 今天该判为新的一天
    M._last_full_date = stale
    data = {"time": {"season": "spring", "dayOfMonth": 12, "year": 1}}
    M._OPS_INNER["n"] = 1
    try:
        M._with_state("域 op 正文")
    finally:
        M._OPS_INNER["n"] = 0
    _check("内层**没**消费 `_last_full_date`（当天晨报还在）", M._last_full_date == stale,
           f"_last_full_date={M._last_full_date}")
    _check("_is_new_day 这时仍判 True（外层能拿到全量条）", M._is_new_day(data) is True)
    _check("消费后确实变 False（对照，别把尺子也写错）", M._is_new_day(data) is False)


def case_e(M):
    print("E) 域工具确实都会在外层收尾（结构地基 —— 域工具全表扫）")
    import ast
    import inspect
    src = inspect.getsource(M)
    tree = ast.parse(src)
    offenders = []
    checked = 0
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if "_ops_run" not in calls:
            continue
        checked += 1
        if "_with_state" not in calls:
            offenders.append(fn.name)
    _check(f"调 _ops_run 的工具共 {checked} 个，**全部**都有 _with_state 收尾", not offenders,
           f"漏的：{offenders}")
    _check("数量对得上（15 个域 dispatcher，别悄悄少测）", checked == 15, f"实际 {checked} 个")


if __name__ == "__main__":
    import nagi_mcp_server as M
    _install_stubs(M)
    try:
        case_a(M)
        case_b(M)
        case_c(M)
        case_d(M)
        case_e(M)
    finally:
        M._OPS_INNER["n"] = 0
    print()
    if FAILED:
        print("❌ 失败：", "、".join(FAILED))
        sys.exit(1)
    print("✅ 全部通过（⚠️ 离线自测；真机冒烟仍需在重启后调几个域工具看状态条）")
