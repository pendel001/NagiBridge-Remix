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
import re
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
    "bomb_escort",   # 🗑️ 2026-10-03 恒拍板**真删**（脚本 + MCP 工具一起删）。原来只是"不对外暴露"，
                     #   但它**全仓没有任何启动点**（协同是 `bomb_mine._run_cooperate()` 内联的），
                     #   留着 = "看着像在用、其实没人起"的坑。⇒ 留在本表里只为记账（惰性项）。
    "menu_claim_swap", # 🚫 2026-08-28 恒：claim_swap(替换领取)退役——改 menu click action=discard 丢桶 + action=claim/slot 领；不判断档
    # 🔍 2026-09-11 恒：下面三个 storage 老工具**不是误报**（自检说"没人调用"是对的），
    #   但**功能都由 storage 域 op 覆盖** ⇒ 对 AI 不存在断档，按本集合的定义（"隐藏安全"）收编。
    #   ⚠️ 它们的覆盖方式**不一样**，别混为一谈（写清楚免得后人又当成误报跳过）：
    "chest_take",           # ✅ **真·被子函数调用**：`storage_take` 内部 `return chest_take(x,y,name,count)`
    "chest_store",          # ⚠️ **不是被调用，是被"重新实现"**：`storage_store` 走 `api.store_all(what/target/all)`
                            #    （原工具=存进指定格 (x,y) 的箱子；该能力现由 storage store 的 target 参数给）
                            #    ⇒ 此函数**已无调用者**，是死包装，**留着只为兼容、可删**
    "storage_default_clear",# ⚠️ 同上：清默认箱的能力现由 `storage_default(clear=True)` 给 ⇒ **死包装，可删**
    # ⛔ 2026-10-01 恒：「**做完给其他收放路打一下退役标吧**」——两条旧收放路都撤下顶层，功能并进 `load`：
    #    ⚠️ 2026-10-03 更新：恒口径收成「**要么删掉收放兼容之外的所有口，要么…留一个一键快捷收在单子上**」
    #       ⇒ 收官选前者，`collect_machines()` 与 C# `/machine_collect` 已**真删**（不是只退役）。
    #       留在本表里只是记账（它已不存在 ⇒ 这条黑名单项现在是惰性的）。
    "collect_machines",     # → farm ops="load"（`load_machines`）：**同一件事的拟人版**；**2026-10-03 已删除**
                            #   旧的是 C# `/machine_collect` 原子瞬收、**不要求人在机器旁边**
                            #   （恒真机：「不是撤掉非拟人了吗！还是一键收了hhh」）。
    "work_building",        # → farm ops="load"（同上）：旧的跑 `fruit_round.py`，**料尽/机器不收会提前收工**，
                            #   跟恒定的「收放一条过」不是一套；而且同一件事只该有一条拟人路。
    # 🖱️ 2026-10-04 恒「失焦暂停可以退役了」：`daily ops=pause` 这个口**撤了**，
    #    所以 `set_pause()` 函数**不再被任何域 op 引用**（自检会把它算成"断档"）。
    #    ⚠️ 这不是误报、也不是断档：**它是故意不暴露的**——每条会动菜单/锻造/吃东西的路
    #    内部都自己 `_ensure_background()`（= `set_pause(False)`），AI 没有理由去动这个开关。
    #    C# `/set_pause` 端点同样留着（排查口）。⇒ 记在这张"隐藏安全"表里，**别再接回域 op**。
    "set_pause",            # ⛔ AI 够不着（2026-10-04 退役）；内部 `_ensure_background()` 仍在用
}

PROBLEMS = []
NOTES = []


# ═══════════════════════════════════════════════════════════════════════
# 🗜️ 被撤下顶层的**整个域**：必须**逐 op 写明替代路**（2026-10-01 加）
# ═══════════════════════════════════════════════════════════════════════
# 为什么单开这一张表，而不是往 `_KNOWN_SUBSUMED` 里塞个域名了事：
#   `_KNOWN_SUBSUMED` 是个**字符串黑名单**——塞进去就静默通过。本来是"人工核实过"的白名单，
#   但对**整域收编**它就是个**万能后门**：塞一个域名，几十条 op 的替代路一条都不用写。
#   2026-10-01 实测过（`_guardrail_sim.py`）：撤 `social` 会报 1 条断档，
#   而「撤 social + 写进 _KNOWN_SUBSUMED」报 **0 条** —— 唯一的护栏就这么没了，且看不出来。
#   ⇒ 整域收编改走这张表：**键 = 该域 dispatch 里的每个 value 函数名，
#      值 = 替代路（一句人能核的话）**。少写一条就报错 ⇒ 它是**逐 op 审计表**，不是后门。
#
# ⚠️ 判据是"**覆盖该域 dispatch 的全部 value**"，不是"字段看着差不多"——
#    漏掉的那条恰好就是"AI 够不着、也没人发现"的那条（域 op 断档那个病的正身）。
_SUBSUMED_DOMAINS = {
    # 🧠 2026-10-01 恒拍板：session 三条 op 并进 `settings`（**消除重复**，不是搬家）——
    #    `settings_status` 早在印会话设置，而 `settings(setting="context_turns")` 与
    #    `session set max_turns` 改的是同一个 `SESSION_CFG`。
    "session": {
        "_session_status": "settings(ops='session_status')（settings_status 本来就印着会话那两行）",
        "_session_set":    "settings(ops='session_set', kw={setting,value})；老路 settings(setting='context_turns') 仍可",
        "_session_exportop": "settings(ops='session_export')（**给人看的 md 子集**；实时全量档案 session_<ts>.jsonl 本来就一直落盘 ⇒ 恒 2026-10-01 确认：真正有用的只有 max_turns）",
    },
    # 🏠 2026-10-01 恒拍板：`cabin` 撤出顶层（"能收就收"）——
    #    它 11 条 op 里**有 9 条本来就是别的域的同一个函数**（scene/farm/daily 都有同名 op），
    #    真正只在 cabin 的只有 `_cabin_enum`（扫屋待收）和 `_cabin_collect`（收本屋机器），
    #    而这两条的能力**由 check + 单子覆盖**。⚠️ `cook` 是这次唯一**搬家**的：
    #    恒指定进 `daily`（"做饭是吃的上游"，daily 本来就有 eat）。
    "cabin": {
        "cook":              "daily(ops='cook', kw={recipe_name,count}) —— 2026-10-01 恒指定搬进 daily",
        "go_sleep":          "daily(ops='sleep', kw={who})（同一函数；过夜意图由 AI 自己带）",
        "blessing_statue":   "farm(ops='statue')（同一函数）",
        "interact_at":       "scene(ops='at'/'interact', kw={tile_x,tile_y})（同一函数）",
        "place_item":        "scene(ops='place', kw={name,x,y})（同一函数）",
        "break_tile":        "scene(ops='break', kw={x,y,steps,radius})（同一函数）",
        "decor_report":      "scene(ops='decor')（同一函数；地板/墙纸真值表）",
        "furniture_pickup":  "scene(ops='pickup')（同一函数）或单子「搬走…」",
        "scan_furniture":    "scene(ops='furniture')（同一函数）",
        "_cabin_collect":    "单子「收 已好的机器」/ farm(ops='load', kw={'here': True}) —— **2026-10-01 起改走拟人那条**（同一个 machine_loader，不再是一键瞬收）",
        "_cabin_enum":       "check(what='machines') + 单子「收 已好的机器」—— 扫屋待收的聚合视图已由这两条覆盖",
    },
}


def _domain_op_values(domain: str) -> set:
    """某域 dispatch 里的全部 value 函数名（复用 `_collect_dict_values` 的 AST 口径）。"""
    fn = getattr(M, domain, None)
    if fn is None:
        return set()
    return _collect_dict_values(fn)


def _check_subsumed_domains(hidden: set) -> None:
    """撤下去的域：逐 op 必须写明替代路（见 `_SUBSUMED_DOMAINS` 那段）。"""
    for d in sorted(hidden & set(DOMAINS)):
        book = _SUBSUMED_DOMAINS.get(d)
        if book is None:
            PROBLEMS.append(
                f"  域「{d}」不在 _KEEP_TOOLS 里，却**没有** _SUBSUMED_DOMAINS 审计表 → "
                f"请逐条写明它的每个 op 现在走哪条路（别往 _KNOWN_SUBSUMED 塞域名了事）")
            continue
        ops = _domain_op_values(d)
        miss = sorted(ops - set(book))
        extra = sorted(set(book) - ops)
        if miss:
            PROBLEMS.append(
                f"  _SUBSUMED_DOMAINS['{d}'] 漏了 {len(miss)} 个 op 的替代路: {', '.join(miss)}"
                f" → 收编一个域时**每条 op 都要有一句话**（漏的正是没人发现的那条）")
        if extra:
            NOTES.append(f"  _SUBSUMED_DOMAINS['{d}'] 里这几条不是它的 op（改名了？）: {', '.join(extra)}")
        if not miss:
            print(f"  ✅ 撤下去的域「{d}」{len(ops)} 个 op 全写了替代路")


def _check_no_domain_backdoor() -> None:
    """域名**不许**走 `_KNOWN_SUBSUMED` 后门（那会把整域审计变成一句话）。"""
    bad = sorted(set(_KNOWN_SUBSUMED) & set(DOMAINS))
    if bad:
        PROBLEMS.append(
            f"  _KNOWN_SUBSUMED 里混进了域名 {', '.join(bad)} → 整域收编请走 _SUBSUMED_DOMAINS "
            f"逐 op 写清；塞域名会让「无断档」检查静默通过（唯一的护栏就没了）")
    else:
        print(f"  ✅ _KNOWN_SUBSUMED 里没有域名（整域收编只能走审计表）")


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


def _dispatch_ops():
    """各域 dispatch 表。返回 (全部键 {(域,键)}, 键→值函数名 {(域,键): 函数名})。

    抄 **dispatch 表**不抄文档——文档会漂，表不会（`gen_tool_checklist.py` 同一条规矩）。
    ⚠️ 提取逻辑**只有一份**，在 `nagi_mcp_server._dispatch_keys()`（`help` 的 op 名反查
    用的是同一份）——这里只是把它翻成自检要的形状，**别在这儿再写一遍 AST**：
    早先各写一遍时，我那份用"行首正则"，mine 把两个 op 写在同一行就漏了一个，
    于是**报出一个假的"意图索引悬空"**。判据只有一份是 CLAUDE.md 级规矩。
    """
    raw = M._dispatch_keys()
    keys, k2f = set(), {}
    for k, bucket in raw.items():
        for d, fn in bucket:
            keys.add((d, k))
            k2f[(d, k)] = fn
    return keys, k2f


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
    # ⚠️ 2026-10-01：**整域收编**（`_SUBSUMED_DOMAINS` 里有审计表的）**不算断档** ——
    #    但它的"没断档"由 4b 那张**逐 op 审计表**证明，比这条函数级检查**更严**（这是关键：
    #    不能因为"域名本身没人引用"就把它当断档，也不能因为"我写了张表"就放过没写的 op）。
    subsumed_domains = sorted(set(stranded) & set(_SUBSUMED_DOMAINS))
    real_stranded = [s for s in stranded
                     if not s.startswith("_")
                     and s not in _KNOWN_SUBSUMED
                     and s not in _SUBSUMED_DOMAINS]
    subsume_stranded = [s for s in stranded if s in _KNOWN_SUBSUMED]
    priv_stranded = [s for s in stranded if s.startswith("_")]
    if real_stranded:
        for s in real_stranded:
            PROBLEMS.append(f"  被隐藏工具「{s}」无任何域 op 可达 → 功能断档（需加进 _KEEP_TOOLS 或补域 op）")
    else:
        print(f"  ✅ 被隐藏的 {len(hidden)} 个工具都能被 {len(reachable)} 个域 op 引用到达（或无断档）")
    if subsumed_domains:
        NOTES.append(f"  整域收编（走 _SUBSUMED_DOMAINS 逐 op 审计，不算断档）: {', '.join(subsumed_domains)}")
    if subsume_stranded:
        NOTES.append(f"  已被域 op 覆盖、安全隐藏: {', '.join(subsume_stranded)}")
    if priv_stranded:
        NOTES.append(f"  下划线内部注册工具未被子域引用（容忍，仅无 AI 直调入口）: {', '.join(priv_stranded)}")

    # 4b. 🗜️ 撤下顶层的**整域**：逐 op 审计表 + 域名不许走 _KNOWN_SUBSUMED 后门（2026-10-01）
    _check_subsumed_domains(hidden)
    _check_no_domain_backdoor()

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

    # 8. 📖 help 别名表不悬空：`_HELP_ALIAS` 的**值**必须真的是 `_DOMAIN_GUIDES` 的键
    #    2026-09-25 实测长出来过：「脚本」→"scripts"，可域键是单数 "script"
    #    ⇒ `help(脚本)` 走别名那条路 `_DOMAIN_GUIDES[...]` **当场 KeyError 炸**，
    #    不是"没找到"那种优雅降级 —— AI 正卡在"我不知道该怎么办"来查指引，反挨一记异常。
    #    别名是手写的、域键也手写，改名/新加时漏对一次就中招，所以在这里按"值 ∈ 域键"逐条对。
    _dangling = sorted(f"{k}→{v}" for k, v in M._HELP_ALIAS.items() if v not in M._DOMAIN_GUIDES)
    if _dangling:
        for _d in _dangling:
            _k = _d.split("→")[0]
            PROBLEMS.append(f"  help 别名「{_d}」的域名不在 _DOMAIN_GUIDES 里"
                            f" → help({_k}) 会 KeyError（改成存在的域名）")
    else:
        print(f"  ✅ help 别名表 {len(M._HELP_ALIAS)} 条全部指向存在的域")

    # 9. 🎯 意图索引（`_INTENT_INDEX`，AI 说"我想干嘛"→直给敲哪条）
    #    (a) **悬空**=拦：索引指向的 (域,op) 必须真的在 dispatch 表里 —— 写错 op 名的话，
    #        AI 会照着一条**不存在的命令**去敲（比"搜不到"更坏），所以和别名表同规格。
    #    (b) **覆盖**=只提示：168 个 op 里没被任何意图句覆盖的，只是"少一个口语入口"，
    #        不等于坏 —— 逐个列出当待办，别拿去卡构建。
    _idx_ops = {(d, op) for _, d, op, _ in M._INTENT_INDEX}
    _keys, _k2f = _dispatch_ops()
    # 🔴 空表 = 提取器瘸了（`except` 会把它吞成"没有悬空"这种假绿）：`_dispatch_keys`
    #    当初漏 import `textwrap` 就是这个形态——op 反查从此永远"查无此 op"却不报错。
    if not _keys:
        PROBLEMS.append("  `_dispatch_keys()` 返回空表 → op 反查（help(craft)）永远查无此 op，"
                        "而下面的悬空检查还会**假绿**（没键自然没悬空）")
    _dangling_idx = sorted(f"{d}.{op}" for d, op in _idx_ops if (d, op) not in _keys)
    if _dangling_idx:
        for _x in _dangling_idx:
            PROBLEMS.append(f"  意图索引指向「{_x}」，但该域 dispatch 里没有这个 op"
                            f" → AI 会照着一条不存在的命令敲")
    else:
        print(f"  ✅ 意图索引 {len(M._INTENT_INDEX)} 条全部指向真实 op")

    # 覆盖按 **能力**（dispatch 的值函数）算，不按键：一个函数常挂好几个别名键
    # （`check.building`/`building_list`/`buildings` 是同一个 `building_list`），
    # 按键算会把 1 个能力数成 3 个，覆盖率看着虚高、缺口看着虚多。
    _caps = {(d, fn) for (d, _k), fn in _k2f.items()}
    _covered = {(d, _k2f[(d, op)]) for d, op in _idx_ops if (d, op) in _k2f}
    _miss = sorted(_caps - _covered)
    print(f"  · 🎯 意图索引覆盖 {len(_caps) - len(_miss)}/{len(_caps)} 个能力（{len(M._INTENT_INDEX)} 条）"
          f"；未覆盖 {len(_miss)} 个 = 少一句口语入口，不影响正确性")
    if _miss:
        _head = "、".join(f"{d}.{o}" for d, o in _miss[:30])
        print(f"     未覆盖(前30): {_head}{' …' if len(_miss) > 30 else ''}")

    # 10. 🎯 意图检索**回归样本**（每条都是真踩出来的，别让它悄悄松回去）
    #     A「火山」：恒 09-25 的原话——"工具要配地点用"，同一个词既可能是"怎么去/解锁没"
    #       也可能是"在那儿怎么炸"，所以**地点和工具两条都得给**，不能只给 `bomb_volcano`
    #       （那还是个"得先人在火山里"才放行的工具，猜错代价最高）。
    #     B「给恒送个东西」：**说明文不许当索引**——`收银台卖东西` 的"东西"曾被当成地点。
    #     C「随便说点啥」：说明文里的"随便钓"同理；无关的话就该老老实实回 ❌。
    _cases = [
        ("火山",     lambda r: "📍" in r and "🛠" in r, "地点+工具两条都要给（工具要配地点用）"),
        ("给恒送个东西", lambda r: "📍" not in r and "social" in r, "别被说明文里的「卖东西」拐去当地点"),
        ("随便说点啥",  lambda r: "❌" in r, "无关的话就该回没有，别硬凑一个地点"),
        ("我想去河边钓鱼", lambda r: "📍" in r and "fish" in r, "河=Forest/Town 那个老翻车点"),
        # D `craft`/`制作`：日志里 AI 真实打过的两个（`help(craft)` 现在必须走 op 名反查，
        #   不能被地点截胡——`craft` 的 `ra` 曾是 `Railroad` 的前缀；`help(制作)` 原先是 ❌）
        ("craft",  lambda r: "🛠" in r and "menu" in r, "op 名反查；别被「Ra(ilroad)」截胡"),
        ("制作",   lambda r: "menu" in r, "日志实锤：原先回 ❌ 没有「制作」的指引"),
        ("畜舍",   lambda r: "farm" in r, "中文 op 键（dispatch 表里本来就有）"),
    ]
    _bad = []
    for _q, _ok, _why in _cases:
        try:
            _r = M.help.__wrapped__(_q)
        except Exception as e:
            _bad.append(f"help({_q}) 抛异常 {type(e).__name__}: {e}")
            continue
        if not _ok(_r):
            _bad.append(f"help({_q}) 结果不对——{_why}")
    if _bad:
        for _b in _bad:
            PROBLEMS.append(f"  {_b}")
    else:
        print(f"  ✅ 意图检索回归样本 {len(_cases)}/{len(_cases)} 通过")

    # 11. 汇总
    # ⚠️ 域/独立的条数**算出来**，别写死（原来硬编码 15 —— 2026-10-01 收编 session 之后
    #    它会印出「13 域 + 4 独立」这种假账，而独立其实只有 3 个）。
    _vis = [d for d in DOMAINS if d in M._KEEP_TOOLS]
    _hidden_dom = [d for d in DOMAINS if d not in M._KEEP_TOOLS]
    print(f"  · 注册工具总数: {len(registered)}")
    print(f"  · keep-set 白名单: {len(M._KEEP_TOOLS)}"
          f"（{len(_vis)} 域 + {len(M._KEEP_TOOLS) - len(_vis)} 独立）")
    if _hidden_dom:
        print(f"  · 已撤下顶层的域: {', '.join(_hidden_dom)}（函数仍在，走审计表的替代路）")

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
