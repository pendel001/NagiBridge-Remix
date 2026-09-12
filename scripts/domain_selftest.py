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
    # 🔍 2026-09-11 恒：下面三个 storage 老工具**不是误报**（自检说"没人调用"是对的），
    #   但**功能都由 storage 域 op 覆盖** ⇒ 对 AI 不存在断档，按本集合的定义（"隐藏安全"）收编。
    #   ⚠️ 它们的覆盖方式**不一样**，别混为一谈（写清楚免得后人又当成误报跳过）：
    "chest_take",           # ✅ **真·被子函数调用**：`storage_take` 内部 `return chest_take(x,y,name,count)`
    "chest_store",          # ⚠️ **不是被调用，是被"重新实现"**：`storage_store` 走 `api.store_all(what/target/all)`
                            #    （原工具=存进指定格 (x,y) 的箱子；该能力现由 storage store 的 target 参数给）
                            #    ⇒ 此函数**已无调用者**，是死包装，**留着只为兼容、可删**
    "storage_default_clear",# ⚠️ 同上：清默认箱的能力现由 `storage_default(clear=True)` 给 ⇒ **死包装，可删**
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


def _scan_with_state_under_bg_lock():
    """🔴 静态找「持 `_bg_lock` 时调 `_with_state`」——这类自锁死会僵住整个 MCP 服务。

    ⚠️ 用 AST 而不是 grep：要判"调用点**在这个 with 块体内**"，纯文本按缩进猜会漏/会误报。
    只认 `_with_state` 直接调用；间接调用（比如转手给别的函数再拼状态条）静态看不出来 ——
    那种靠真机跑，别假装这个检查是完备的。
    返回 [(函数名, with 行号, _with_state 行号)]，空列表=干净。
    """
    src = _fn_source(M)   # 模块源码；读不到就退化成"检查没跑"，不瞎报绿
    if not src:
        PROBLEMS.append("  读不到 nagi_mcp_server 源码 → 自锁死检查**没跑**（不是通过）")
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        PROBLEMS.append(f"  自锁死检查：源码解析失败 {e}")
        return []
    hits, stack = [], []

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, n):
            stack.append(n.name)
            self.generic_visit(n)
            stack.pop()

        def visit_With(self, n):
            locked = any(isinstance(i.context_expr, ast.Name)
                         and i.context_expr.id == "_bg_lock" for i in n.items)
            if locked:
                for sub in ast.walk(n):
                    if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                            and sub.func.id == "_with_state"):
                        hits.append((stack[-1] if stack else "?", n.lineno, sub.lineno))
            self.generic_visit(n)

    V().visit(tree)
    # 同一个 with 块里可能有多处 _with_state，按行号去重后返回
    return sorted(set(hits))


_N_OPS_TOOLS = [0]   # 被检查过的"调 _ops_run 的工具"个数（写回给 print 用，顺带防"扫到 0 个也算绿"）


def _scan_tools_missing_wstate() -> list:
    """调 `_ops_run` 却没用 `_with_state` 收尾的**工具**函数名。

    为什么这是一条**地基级**检查：2026-09-12 恒拍板让 `_with_state` 在域 op 内层**直接返回正文**
    （内层那条本来就被 `_ops_run` 砍掉、却还在消费一次性注入 —— 见 `_OPS_INNER` 注释）。
    于是"**外层一定会再调一次**"成了整套机制的前提。⚠️ 扫到 0 个不是"通过"、是"尺子坏了"，一并报出来。
    """
    src = _fn_source(M)
    if not src:
        PROBLEMS.append("  读不到 nagi_mcp_server 源码 → 域工具收尾检查**没跑**（不是通过）")
        return []
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        PROBLEMS.append(f"  域工具收尾检查：源码解析失败 {e}")
        return []
    offenders = []
    for fn in tree.body:                       # 工具都是模块级 def
        if not isinstance(fn, ast.FunctionDef):
            continue
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if "_ops_run" not in calls:
            continue
        _N_OPS_TOOLS[0] += 1
        if "_with_state" not in calls:
            offenders.append(fn.name)
    if _N_OPS_TOOLS[0] < 10:
        PROBLEMS.append(f"  只扫到 {_N_OPS_TOOLS[0]} 个调 _ops_run 的工具（预期 ≥15）—— 检查本身可能失效")
    return offenders


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

    # 5. 🔴 持 `_bg_lock` 时调 `_with_state` = 自锁死（2026-09-12 真机踩到）
    #    机理：`_with_state` 要拼状态条 → 走 `_bg_activity_line()` → 那里也 `with _bg_lock:`，
    #    而 `_threading.Lock()` **不可重入** ⇒ 同线程把自己锁死。⚠️ 死的不是这一次调用：
    #    锁再也放不掉，之后**每个**要拼状态条的工具都排队等它 ⇒ 整个 :8000 事件循环僵住
    #    （`GET /` 都不回、CPU 不涨 = 阻塞不是死循环），只能重启服务。
    #    当天凶手 = `script_stop` 的三个提前返回；这个检查就是防它换个函数再长出来。
    _dl = _scan_with_state_under_bg_lock()
    if _dl:
        for _fn, _wl, _cl in _dl:
            PROBLEMS.append(f"  {_fn}() 在持 _bg_lock 时调 _with_state（L{_wl}/L{_cl}）"
                            f" → 自锁死，会僵住整个 MCP 服务（把 _with_state 挪到出锁之后）")
    else:
        print("  ✅ 没有「持 _bg_lock 时调 _with_state」的自锁死")

    # 7. 🧱 结构地基：调 `_ops_run` 的域工具**必须**用 `_with_state` 收尾
    #    2026-09-12 起 `_with_state` 在**域 op 内层直接返回正文**（见 `_OPS_INNER`）⇒ 状态条**只由外层那一次**
    #    产生。谁新加域工具漏了这层包装，表现是**该工具从此完全不带状态条**（AI 瞎着眼操作），
    #    而且不报错、只在真机上"好像没看见状态"——跟 09-12 那个"报成功而事没发生"同一类难查。
    _missing = _scan_tools_missing_wstate()
    if _missing:
        for _fn in _missing:
            PROBLEMS.append(f"  {_fn}() 调了 _ops_run 却没 _with_state 收尾 → 该工具**永远没有状态条**"
                            f"（改成 `return _with_state(_ops_run(...))`）")
    else:
        print(f"  ✅ 调 _ops_run 的 {_N_OPS_TOOLS[0]} 个域工具都有 _with_state 收尾（状态条外层兜底成立）")

    # 8. 汇总
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
