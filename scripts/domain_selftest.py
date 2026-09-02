# -*- coding: utf-8 -*-
"""🔒 域工具模式静态自检（2026-08-22，无游戏可跑 headless import）
验证：
  1. fish 域已注册（回归：曾缺 @mcp.tool() → 钓鱼/蟹笼对 AI 不可达）。
  2. _KEEP_TOOLS 每个名字都是真实注册工具（防白名单写错 / 域没注册）。
  3. 域 ops dispatch 无断链：dispatch dict 引用的每个名字在模块里都是存在的 callable（防 NameError）。
  4. 无"被隐藏却无任何域 op 到达"的工具（防断档功能）。下划线内部注册工具 + _KNOWN_SUBSUMED（已由域 op 以另一函数名覆盖）只警告/不报错。

用法: python scripts/domain_selftest.py
退出码: 全过=0, 有失败=1
"""
import ast
import inspect
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M

# 域 dispatcher（2026-09-02 合并后 15 个：13 原域 + script + session；quest→menu, care→farm 已并入）
DOMAINS = [
    "check", "farm", "mine", "cabin", "social", "scene",
    "menu", "storage", "daily", "map", "festival", "fish", "settings",
    "script", "session",
]

# 已被域 op 以"另一函数名"覆盖的独立工具（隐藏安全，不判断档）——人工核实，改 keep-set 时同步更新
_KNOWN_SUBSUMED = {
    "clear_area",    # → farm ops="clear"（_farm_clear）
    "go_to",         # → map ops="go"（map_go）/ walk_to POI
    "dance_invite",  # → festival ops="dance"（_festival_dance）
    "bomb_escort",   # 🚫 2026-08-22 恒：不对外暴露（协同内建进 bomb_mine 没炸弹自动转内部），AI 不主动启用
    "menu_claim_swap", # 🚫 2026-08-28 恒：claim_swap(替换领取)退役——改 menu click action=discard 丢桶 + action=claim/slot 领；不判断档
}

PROBLEMS = []
NOTES = []


def _fn_source(fn):
    return inspect.getsource(fn)


def _collect_dict_values(fn) -> set:
    """AST 扫单个 dispatcher 函数体：只收集 ops dispatch 字典里的 value（即被路由的子函数名）。"""
    names = set()
    try:
        tree = ast.parse(_fn_source(fn))
    except Exception as e:
        PROBLEMS.append(f"  读取 {getattr(fn, '__name__', '?')} 源码失败: {e}")
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for v in node.values:
                if isinstance(v, ast.Name):
                    names.add(v.id)
    return names


def _collect_disp_names(fn) -> set:
    """AST 扫单个 dispatcher：收集所有可达子函数引用（dict value Names + 直接调用 Name）。
    用于 reachable 分析——宁可多收（少误报断档），本地变量/内建会一并进入但不会落进 registered 交集。"""
    names = set()
    try:
        tree = ast.parse(_fn_source(fn))
    except Exception as e:
        PROBLEMS.append(f"  读取 {getattr(fn, '__name__', '?')} 源码失败: {e}")
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
        if isinstance(node, ast.Dict):
            for v in node.values:
                if isinstance(v, ast.Name):
                    names.add(v.id)
    return names


def dom_reachable() -> set:
    """所有域（含 settings 模块级 dispatch）可到达的子函数名集合。"""
    names = set()
    for d in DOMAINS:
        fn = getattr(M, d, None)
        if fn is None:
            continue
        names |= _collect_disp_names(fn)
    for f in getattr(M, "_SETTINGS_DISPATCH", {}).values():
        nm = getattr(f, "__name__", None)
        if nm:
            names.add(nm)
    return names


def main():
    registered = {t.name for t in M.mcp._tool_manager.list_tools()}

    # 1. fish 已注册（回归）
    if "fish" not in registered:
        PROBLEMS.append("  fish 域未注册（缺 @mcp.tool()）——钓鱼/蟹笼对 AI 不可达")
    else:
        print("  ✅ fish 域已注册")

    # 2. _KEEP_TOOLS ⊆ registered
    missing_keep = set(M._KEEP_TOOLS) - registered
    if missing_keep:
        for m in sorted(missing_keep):
            PROBLEMS.append(f"  _KEEP_TOOLS 里的「{m}」不是已注册工具（白名单写错 / 域没注册）")
    else:
        print(f"  ✅ _KEEP_TOOLS 全部 {len(M._KEEP_TOOLS)} 个都是已注册工具")

    # 3. 各域 ops dispatch 无断链（dispatch dict value 引用的名字要存在且 callable）
    for d in DOMAINS:
        fn = getattr(M, d, None)
        if fn is None:
            continue
        for n in _collect_dict_values(fn):
            if not callable(getattr(M, n, None)):
                PROBLEMS.append(f"  {d} dispatch 引用「{n}」但模块里不存在/不可调用")
    for v in getattr(M, "_SETTINGS_DISPATCH", {}).values():
        if not callable(v):
            PROBLEMS.append(f"  settings dispatch 含非 callable: {v}")

    # 4. 无断档：被隐藏工具（registered - _KEEP_TOOLS）必须能被任一域 op 到达
    reachable = dom_reachable()
    hidden = registered - set(M._KEEP_TOOLS)
    stranded = sorted(hidden - reachable)
    real_stranded = [s for s in stranded if not s.startswith("_") and s not in _KNOWN_SUBSUMED]
    subsume_stranded = [s for s in stranded if s in _KNOWN_SUBSUMED]
    priv_stranded = [s for s in stranded if s.startswith("_")]
    if real_stranded:
        for s in real_stranded:
            PROBLEMS.append(f"  被隐藏工具「{s}」无任何域 op 可达 → 功能断档（需加进 _KEEP_TOOLS 或补域 op）")
    else:
        print(f"  ✅ 被隐藏的 {len(hidden)} 个工具都能被 {len(reachable)} 个域 op 引用到达（或无断档）")
    if subsume_stranded:
        NOTES.append(f"  已被域 op 覆盖、安全隐藏: {', '.join(subsume_stranded)}")
    if priv_stranded:
        NOTES.append(f"  下划线内部注册工具未被子域引用（容忍，仅无 AI 直调入口）: {', '.join(priv_stranded)}")

    # 5. 汇总
    print(f"  · 注册工具总数: {len(registered)}")
    print(f"  · keep-set 白名单: {len(M._KEEP_TOOLS)}（15 域 + {len(M._KEEP_TOOLS) - 15} 独立）")

    if NOTES:
        print("  ℹ️ 提示:")
        for n in NOTES:
            print(n)
    if PROBLEMS:
        print("\n❌ 发现问题:")
        for p in PROBLEMS:
            print(p)
        return 1
    print("\n✅ domain_selftest 全绿")
    return 0


if __name__ == "__main__":
    sys.exit(main())
