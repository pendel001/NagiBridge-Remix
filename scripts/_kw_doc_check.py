# -*- coding: utf-8 -*-
"""📐 参数速查一致性静态自检（2026-09-11，无游戏可跑 headless import）

**查什么**：AI 真读得到的 `help(域)`（= `_DOMAIN_GUIDES`）和 `TOOL_INVENTORY.md` 里的
「参数速查」写出来的**参数键名**，是不是真的能传进那个 op。

**为什么值得有**：`_ops_run` 对签名里没有的键是**静默丢掉**的（TOOL_INVENTORY 顶部自己
写着"本项目最容易踩且最难发现的坑"）。所以文档写错**不会报错**——AI 照抄、调用成功、
参数压根没进去、看起来一切正常。2026-09-11 手工核参数表挖出过 4 处"货不对板"，
靠人眼只能碰运气；这里把它变成一条能重复跑的闸门。

**两条判据**（都只看"能不能传进去"，不管语义对不对）：
  1. **键名存在**：文档写的参数名必须出现在**同组 op 的签名并集**里。
     用并集而不是逐个 op 判，是因为文档常把多个 op 写一行共享一列参数
     （`collect` / `load` 那一行），逐 op 判会误报。
  2. **有路可传**：该域的 dispatcher 得**真的把 kw 转发给 op**。
     `check(what)` 就是反例——它 `fn()` 裸调，一个参数都传不进去，
     所以它那份速查表（`chests`/`look`）写什么都是够不着的。
     判据 = AST 扫域函数体里有没有 `_ops_run` 调用。

用法: PYTHONIOENCODING=utf-8 python scripts/_kw_doc_check.py [--dump]
      --dump 只打印解析出来的「域 → op → 参数」表，不判定（加新域时用它核解析对不对）
退出码: 干净=0, 有可疑=1

⚠️ **已知边界**：
  - 解析是**启发式**的（文档是散文，不是语法）。**漏**（没解析到）能接受，**误报**不能
    —— 所以宁可少认几个，也不猜。报出来仍要人看一眼。
  - 只查**键名**，不查默认值/取值。`radius(10)` 写成 `radius(99)` 它不管。
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INVENTORY = os.path.join(SCRIPT_DIR, "TOOL_INVENTORY.md")

# 域 dispatcher（与 domain_selftest.py / _guide_orphan_check.py 保持同一份清单）
DOMAINS = [
    "check", "farm", "mine", "cabin", "social", "scene",
    "menu", "storage", "daily", "map", "festival", "fish", "settings",
    "script", "session",
]

# 文档里合法出现、但**不是参数名**的词（人工维护；加之前先想清楚它是不是真的合法）
_ALLOW = {
    "kw",           # 行文里到处写 kw={'参数名':值}
    "ops",          # 同上
    "what",         # check 的 what 本身；已由 pass-through 判据单独覆盖
    "name",         # 太泛，散文里到处都是；真参数名有同名的也算它过（宁漏不误报）
    "value", "item", "count", "x", "y",  # 同理：单字母/极常用的，散文里也常出现
    "scene", "farm", "check", "menu", "daily", "map", "mine", "fish", "storage",
    "social", "cabin", "festival", "settings", "session", "script",  # 域名
}

_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
# op 组：`till/clear/plant` 或单个 `plot`（斜杠连写的多个 op 共享一段参数说明）
_OP_GROUP = r"([A-Za-z_][A-Za-z_0-9]*(?:\s*/\s*[A-Za-z_][A-Za-z_0-9]*)*)"
_OP_PAREN = re.compile(_OP_GROUP + r"\s*\(")
_OP_EQ = re.compile(_OP_GROUP + r"\s*=")
# `op=参数` 形式的终止符：` / ` 之后是新 op；这几个符号之后是散文/别的段落
_EQ_END = re.compile(r"\s/\s|；|。|⚠|💡|📌|🚫|🤖|⭐|📐|→|（同")


def _strip_parens(s: str) -> str:
    """去掉成对括号（含中文括号）及其内容——`count(0=整叠)` → `count`。
    散文里的括号常夹着 `/`（`(**action=claim领 / discard丢桶腾格**)`），
    不先摘掉会把 `/` 误当 op 分隔符，把后面整段参数读串。"""
    out, depth = [], 0
    for ch in s:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _take_params(s: str) -> list:
    """从一段文字里抠参数名：按 , + 、 切开，每段取开头的标识符；**两种情况下停**：
    ① 这段开头不是标识符（散文，如 `须自己已站到水边`）；
    ② 这段是 `y break=x` 这种"参数名 + 散文 + 下一个 op=' ——
       说明参数列表已经完了、后面是下一个 op 的说明（不停的话 `place=` 会把 `break=` 的参数一起吞掉，
       这是第一版真实的误报来源）。判据：标识符之后、`=` 之前夹着空格。"""
    out = []
    for tok in re.split(r"[,+、]", s):
        tok = tok.strip().lstrip("*").strip()
        if not tok:
            continue
        m = _IDENT.match(tok)
        if not m:
            break                      # ① 已经不是参数名了，停
        out.append(m.group(0))
        rest = tok[m.end():]
        if "=" in rest and rest.split("=", 1)[0].strip():
            break                      # ② 后面跟着 `下一个op=`，停
    return out


def _ops_of(domain: str) -> set:
    """该域 dispatch 的**字符串 key** 全集（= AI 能写的 op 名，含中文别名）。"""
    if domain == "settings":
        return {str(k) for k in getattr(M, "_SETTINGS_DISPATCH", {})}
    fn = getattr(M, domain, None)
    if fn is None:
        return set()
    try:
        tree = ast.parse(inspect.getsource(fn))
    except Exception:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            for k in n.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
    return out


def _targets_of(domain: str) -> dict:
    """op → 目标函数名（<非Name> 的值跳过）。settings 走模块级字典。"""
    if domain == "settings":
        return {str(k): getattr(v, "__name__", "") for k, v in getattr(M, "_SETTINGS_DISPATCH", {}).items()}
    fn = getattr(M, domain, None)
    out = {}
    try:
        tree = ast.parse(inspect.getsource(fn))
    except Exception:
        return out
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and isinstance(v, ast.Name):
                    out[k.value] = v.id
    return out


# "这个域把 kw 转发给 op 了"的判据：函数体里出现下面任一调用。
# `_ops_run` = 标准域入口；`_filter_kw` = 不走 ops 链、自己转发的入口（`check(what, kw)`）。
_KW_FORWARDERS = {"_ops_run", "_filter_kw"}


def _passes_kw_through(domain: str) -> bool:
    """该域会不会把 kw 转发给 op。裸调 `fn()`（既没有 ops 链也没有 _filter_kw）→ False。"""
    fn = getattr(M, domain, None)
    if fn is None:
        return False
    try:
        tree = ast.parse(inspect.getsource(fn))
    except Exception:
        return False
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in _KW_FORWARDERS for n in ast.walk(tree))


def _sig_params(fn) -> set:
    try:
        return set(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        return set()


def parse_guide(domain: str) -> list:
    """从 `help(域)` 正文里解析**分组** [(ops, 参数集), …]。两种写法都认：
    ① `op(参数,参数)`（scene/mine 那种带括号的）　② `op=参数,参数`（多数域）。
    ⚠️ 返回分组而不是摊平到每个 op：`place/break=…` 这种一行写几个 op 共享一列参数的写法很常见，
    摊平后 `place` 会背上 `break` 的参数、变成误报（第一版就是这么炸的）。"""
    guide = M._DOMAIN_GUIDES.get(domain, "")
    if not guide:
        return []
    # ✂️ 只看 📐（参数速查）那一段。ops 速览段里全是 `key(ok/esc/数字按键)` 这种**取值**列表，
    #    和参数列表长得一模一样，不分段就会把 `ok` / `esc` 当成参数名（第一版的误报）。
    cut = guide.find("📐")
    if cut > 0:
        guide = guide[cut:]
    ops = _ops_of(domain)
    out = []

    def _record(group: str, params):
        names = [g.strip() for g in group.split("/") if g.strip() in ops]
        if names and params:
            out.append((names, set(params)))

    # ① 括号写法 —— 必须在**没摘括号**的原文上找（参数就写在括号里）
    for m in _OP_PAREN.finditer(guide):
        _record(m.group(1), _take_params(_balanced(guide, m.end() - 1)))

    # ② `op=参数` 写法 —— 先摘掉括号，免得括号里的 `/` 把 op 分组切乱
    flat = _strip_parens(guide)
    for m in _OP_EQ.finditer(flat):
        tail = _EQ_END.split(flat[m.end():], 1)[0]
        _record(m.group(1), _take_params(tail))
    return out


def _balanced(s: str, i_open: int) -> str:
    """取 s[i_open] 那个左括号配对的括号内容（不跨括号）。"""
    depth = 0
    for j in range(i_open, len(s)):
        if s[j] in "（(":
            depth += 1
        elif s[j] in "）)":
            depth -= 1
            if depth == 0:
                return s[i_open + 1:j]
    return s[i_open + 1:]


_ROW = re.compile(r"^\|(.+)\|\s*$")
_BT = re.compile(r"`([^`]+)`")


_OP_NAME_CACHE = {}


def _all_op_names() -> set:
    """全部域的 op 名（含中文别名）——用来把"表格里被反引号包起来的 op 名"从参数列里剔掉
    （如 `take` 那行的 `` `pond_add` 另有 `item` `` 里的 pond_add 是 op 不是参数）。"""
    if "v" not in _OP_NAME_CACHE:
        names = set()
        for d in DOMAINS:
            names |= _ops_of(d)
        _OP_NAME_CACHE["v"] = names
    return _OP_NAME_CACHE["v"]


def parse_inventory() -> dict:
    """从 TOOL_INVENTORY.md 的「📐 … 参数速查」表格里解析 {域: [(ops, 参数集), …]}。
    一行里 op 列可以写多个 op（共享参数列），**整行作为一组**返回，判定时按并集
    （见 parse_guide 的说明：摊平到单个 op 会误报）。"""
    if not os.path.exists(INVENTORY):
        return {}
    lines = open(INVENTORY, encoding="utf-8").read().split("\n")
    out, dom, in_param = {}, None, False
    for ln in lines:
        if ln.startswith("### ") or ln.startswith("## "):
            dom, in_param = None, False
            for d in DOMAINS:
                if f"`{d}`" in ln or f"`{d}(" in ln:
                    dom = d
                    break
            continue
        if "参数速查" in ln:
            in_param = True
            continue
        if in_param and ln.startswith("> "):
            in_param = False           # 表格后的说明文字，出了表
            continue
        if not (in_param and dom):
            continue
        m = _ROW.match(ln.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 2:
            continue
        c0, c1 = cells[0], cells[1]
        if c0.startswith("↳") or c0.startswith("—") or c1 in ("—", "无参", "参数"):
            continue
        ops = [t for t in _BT.findall(c0) if not t.startswith(("x=", "y="))]
        # 表头分隔行 / 说明行：op 列一个反引号都没有就跳过
        if not ops:
            continue
        params = {t for t in _BT.findall(c1) if t not in _all_op_names()}
        if params:
            out.setdefault(dom, []).append((ops, params))
    return out


def doc_groups(domain: str) -> list:
    """该域文档里的全部 (ops, 参数集) 分组：TOOL_INVENTORY.md 的表 + help(域) 的正文，去重。"""
    inv = parse_inventory().get(domain, [])
    seen, out = set(), []
    for ops, params in inv + parse_guide(domain):
        key = (tuple(sorted(ops)), tuple(sorted(params)))
        if key in seen:
            continue
        seen.add(key)
        out.append((ops, params))
    return out


def main() -> int:
    dump = "--dump" in sys.argv
    targets = {d: _targets_of(d) for d in DOMAINS}
    pass_through = {d: _passes_kw_through(d) for d in DOMAINS}
    groups = {d: doc_groups(d) for d in DOMAINS}

    if dump:
        for d in DOMAINS:
            flat = {}
            for ops, params in groups[d]:
                for o in ops:
                    flat.setdefault(o, set()).update(params)
            print(f"===== {d} =====")
            for op in sorted(flat):
                print(f"  {op:18s} {sorted(flat[op])}")
        return 0

    problems, notes = [], []
    n_checked, n_groups = 0, 0
    for d in DOMAINS:
        tmap = targets.get(d, {})

        # 判据 2：域根本不转发 kw → 表上写什么都是够不着的
        if groups[d] and not pass_through[d]:
            allops = sorted({o for ops, _ in groups[d] for o in ops})
            problems.append(
                f"  [{d}] 域 dispatcher **不转发 kw**（源码里是 `fn()` 裸调，没有 `_ops_run`），"
                f"但它写了参数速查（{allops}）—— 这些参数**一个都传不进去**，"
                f"AI 照着调必然静默无效。要么给域函数加 `kw` 并转发，要么删掉这些行。")

        for ops, params in groups[d]:
            real = {p for p in params if p not in _ALLOW and p not in _all_op_names()}
            if not real:
                continue
            resolved = [tmap.get(o) for o in ops if callable(getattr(M, tmap.get(o, ""), None))]
            if not resolved:
                notes.append(f"  [{d}] {'/'.join(ops)} 没解析到目标函数，跳过（{sorted(real)}）")
                continue
            # 判据 1 的并集：同组 op 里只要有一个收得下这个键名就放行
            if any(p.kind == inspect.Parameter.VAR_KEYWORD
                   for p in inspect.signature(getattr(M, resolved[0])).parameters.values()):
                notes.append(f"  [{d}] {'/'.join(ops)} 吃 `**kw`（自己去过滤），不逐名判")
                continue
            allowed = set()
            for t in resolved:
                allowed |= _sig_params(getattr(M, t))
            n_groups += 1
            n_checked += len(real)
            bad = sorted(real - allowed)
            if bad:
                hint = [p for p in allowed if p.lower().replace("_", "") in
                        [b.lower().replace("_", "") for b in bad]]
                problems.append(
                    f"  [{d}] {'/'.join(ops)} 文档写了 {bad}，但 {'/'.join(resolved)}() 的签名里没有"
                    f"（可用: {', '.join(sorted(allowed))}）"
                    + (f"　←　疑似大小写/下划线写错: {hint}" if hint else ""))

    print(f"  · 解析 {len(DOMAINS)} 个域，共核对 {n_groups} 组 / {n_checked} 个参数键名")
    if notes:
        print("  ℹ️ 跳过:")
        for n in notes:
            print(n)
    if problems:
        print("\n❌ 发现可疑（AI 照文档写会静默失效 / 报参数错）:")
        for p in problems:
            print(p)
        print("\n  ⚠️ 报出来仍要人看一眼：确认是**文档写错**还是**函数该加参数**，"
              "两边改哪边都行，但必须改到一致。")
        return 1
    print("\n✅ 参数速查的键名全部传得进去（无货不对板）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
