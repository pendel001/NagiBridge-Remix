# -*- coding: utf-8 -*-
"""🔍 指南「文档孤儿」静态自检（2026-09-11，无游戏可跑 headless import）

**查什么**：`_DOMAIN_GUIDES`（= `help(域)` 的正文，AI 真读得到）里出现的 `xxx(` 形式的
**op 名**，是不是真的能在**该域 dispatch** 里调到。查的是"AI 照着指南调 → 报未知 op"这类坑。

**为什么值得有**：2026-09-11 补 15 域参数表时，光靠"把参数名核准确"这个纪律就顺带挖出
`help(mine)` 里的 `retreat` —— 真名是 `bomb_retreat`（少 `bomb_` 前缀），**AI 读得到、照调就废**。
这类错**没人专门去看就永远发现不了**（文档不会报错，只会慢慢长歪）。所以留一把尺，改任何指南都能量一下。

用法: PYTHONIOENCODING=utf-8 python scripts/_guide_orphan_check.py
退出码: 干净=0, 有可疑=1

⚠️ **已知盲区（别被吓到）**：
  1. `settings` 域的 dispatch 是**模块级** `_SETTINGS_DISPATCH`（不在函数体内），
     AST 扫函数体扫不到 ⇒ 会把它的真 op（retire/reactivate/confirm_look…）误报。本脚本已单独取该字典。
  2. 指南里**合法出现**的非 op 词很多：**参数名**（`max_casts(0=不限)`）、**跨域引用**（`check(what=profile)`）、
     **已退役的历史名**（`claim_swap(替换领取)已退役`）、纯英文词（`horizontal(默认)`）。
     本脚本用「全部签名参数名 + 全部域别名 + 域名/工具名 + 显式白名单」四道闸过滤，
     剩下的才可能是真孤儿 —— **报出来仍要人看一眼**，它不是判官。
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

# 域 dispatcher（与 domain_selftest.py 保持同一份清单）
DOMAINS = [
    "check", "farm", "mine", "cabin", "social", "scene",
    "menu", "storage", "daily", "map", "festival", "fish", "settings",
    "script", "session",
]

# 指南里合法出现、但**不是任何域的 op** 的词（人工维护；加之前先想清楚它是不是真的合法）
_ALLOW = {
    "op",            # "带参 op(" 这种行文
    "check",         # 指南里常写 check(what="profile") 跨域引用
    "settings",      # "旧配置 settings(setting='async'…)" 讲的是旧调用路径
    "script",        # "如 script(ops='continue', kw={job_id})" 举例
    "tool_area",     # farm 指南提的 raw 端点（非 AI 可调 op），历史遗留行文
    "claim_swap",    # 已退役，指南里明确标注"(已退役)"
    "which_role",    # check 指南讲"原顶层 which_role()"的来历
    "horizontal",    # ⚠️ 是 direction 的**取值**不是 op——farm 指南写"horizontal(默认)/vertical"
}

# 正则：抓 `xxx(` 形式的候选 op 名
# ⚠️ `(?<![A-Za-z_])` 必须有：否则 "Luremaster(职业11)" 会被从大写词中间切出 `uremaster(` 来假报
#    （op 名一律小写开头，前面不该再跟字母）。
_OP_CALL = re.compile(r"(?<![A-Za-z_])([a-z][a-z_0-9]{1,24})\s*\(")


def domain_aliases(domain: str) -> set:
    """某域 dispatch 的真实别名集（dict 的字符串 key）。settings 走模块级字典。"""
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
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    out.add(k.value)
    return out


def all_param_names() -> set:
    """所有 dispatch 目标函数的**参数名**——指南里 `max_casts(0=不限)` 这种不是 op，得先过滤掉。"""
    names = set()
    targets = set()
    # 域 dispatcher 自己的签名也算（`ops` / `kw` / check 的 `what`）——
    # 指南里会写 "script(ops='continue')"，不带上就会把 ops 当孤儿假报。
    for d in DOMAINS:
        f = getattr(M, d, None)
        if callable(f):
            try:
                names |= set(inspect.signature(f).parameters)
            except (TypeError, ValueError):
                pass
    for d in DOMAINS:
        if d == "settings":
            targets |= {getattr(v, "__name__", "") for v in getattr(M, "_SETTINGS_DISPATCH", {}).values()}
            continue
        fn = getattr(M, d, None)
        if fn is None:
            continue
        try:
            tree = ast.parse(inspect.getsource(fn))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for v in node.values:
                    if isinstance(v, ast.Name):
                        targets.add(v.id)
    for nm in targets:
        f = getattr(M, nm, None)
        if not callable(f):
            continue
        try:
            names |= set(inspect.signature(f).parameters)
        except (TypeError, ValueError):
            pass
    return names


def main() -> int:
    aliases = {d: domain_aliases(d) for d in DOMAINS}
    all_ops = set().union(*aliases.values()) if aliases else set()
    params = all_param_names()
    allowed = all_ops | params | set(DOMAINS) | _ALLOW

    problems, notes = [], []
    for d in DOMAINS:
        guide = M._DOMAIN_GUIDES.get(d, "")
        if not guide:
            problems.append(f"  [{d}] 没有指南（AI 的 help({d}) 会是空的）")
            continue
        seen = set()
        for m in _OP_CALL.finditer(guide):
            tok = m.group(1)
            if tok in allowed or tok in seen:
                continue
            seen.add(tok)
            # 近失名（少前缀/少后缀）单列——这正是 2026-09-11 抓到 `retreat`→`bomb_retreat` 的形态
            near = sorted(a for a in aliases[d] if a != tok and (a.endswith(tok) or tok.endswith(a)))
            hint = f"　←　疑似近失：{'/'.join(near)}" if near else ""
            problems.append(f"  [{d}] 指南里的「{tok}(」在该域 dispatch 里不存在{hint}")

    print(f"  · 扫描 {len(DOMAINS)} 个域的指南，域别名合计 {len(all_ops)} 个，参数名合计 {len(params)} 个")
    if problems:
        print("\n❌ 可疑（可能是文档孤儿，AI 照调会报未知 op）:")
        for p in problems:
            print(p)
        print("\n  ⚠️ 报出来仍要人看一眼：近失名多半是真错，"
              "没有近失名的可能是新出现的参数名/跨域引用（那就补进 _ALLOW）。")
        return 1
    print("\n✅ 指南里提到的 op 名全部可达（无文档孤儿）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
