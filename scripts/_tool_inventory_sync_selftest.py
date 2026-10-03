# -*- coding: utf-8 -*-
"""📖 文档 ↔ 代码 同步钉子：`scripts/TOOL_INVENTORY.md` 里**当调用写出来的 op**，必须真在对应域的 dispatch 里。

为什么要有这条（2026-10-03 审计发现）：
  `domain_selftest.py` 只审"域 → op → 隐藏工具"的**可达性**和 `_DOMAIN_GUIDES`/`_HELP_ALIAS`/
  `_INTENT_INDEX` 的一致性，**从来不读 `TOOL_INVENTORY.md`** ⇒ 文档里写着一个**不存在的 op**
  （历史残留）能**长期存活**，而 AI 照文档写就会吃「❌ 未知操作」白跑一趟。
  真逮到过：`storage(ops="scan")`（`scan` 从来不是 op）、`farm collect`/`building`（2026-10-03 已删）、
  `pond_add/feed/collect/fish`（简写不是 op，得写全名）。

判据（只查"调用形状"，避免把"讲历史"的行当错）：
  · `域(ops="a b")` / `域(ops="a"/"b")` / `域 ops=a` ⇒ 里面的每个 token 必须是该域 dispatch 的键
  · 带墓碑标记的行整体跳过（🗑️/已删/退役/旧版/不存在/~~ …）——那些地方**就是要写旧名字**
  · `check` 域参数叫 `what` 不是 `ops`，不在本钉子范围内（另有 `_state_inner_selftest` 管）
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8").read()
DOC = io.open(os.path.join(HERE, "TOOL_INVENTORY.md"), encoding="utf-8").read()

fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


# ── 1. 从源码里抠出每个域的 dispatch 键（静态解析：`dispatch = { … }` 的字符串键）──
DOMAINS = ["farm", "mine", "storage", "scene", "menu", "map", "daily",
           "fish", "festival", "settings", "script", "social"]


def _dispatch_keys(domain):
    """找 `def <domain>(` 之后（到下一个顶层 `def` 为止）的那个 `dispatch(er)? = {`，抓里面的字符串键。

    ⚠️ 两种写法都有：多数域叫 `dispatch`，`settings`/`check` 叫 `dispatcher` —— 只认一种就会漏
    （2026-10-03 第一版就漏了 `settings`，报了一串假红）。
    """
    m = re.search(r"\ndef %s\(" % re.escape(domain), SRV)
    if not m:
        return set()
    nxt = re.search(r"\ndef ", SRV[m.end():])
    seg = SRV[m.end():m.end() + (nxt.start() if nxt else 20000)]
    d = re.search(r"dispatch(?:er)?\s*=\s*\{", seg)
    if not d:
        # 有的域把字典放在**模块级**，函数里只写 `_ops_run(ops, _SETTINGS_DISPATCH, kw)`
        # （`settings` 就是这样；2026-10-03 第一版因此把它当"没有 op"⇒ 报了一串假红）
        ref = re.search(r"_ops_run\(\s*ops\s*,\s*(\w+)", seg)
        if not ref:
            return set()
        d = re.search(r"\n%s\s*=\s*\{" % re.escape(ref.group(1)), SRV)
        if not d:
            return set()
        seg, i = SRV, d.end() - 1
        depth = 0
        for j in range(i, len(seg)):
            if seg[j] == "{":
                depth += 1
            elif seg[j] == "}":
                depth -= 1
                if depth == 0:
                    body = seg[i:j]
                    break
        else:
            return set()
        body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
        return set(re.findall(r'"([^"]+)"\s*:', body))
    i = d.end() - 1
    depth = 0
    for j in range(i, len(seg)):
        if seg[j] == "{":
            depth += 1
        elif seg[j] == "}":
            depth -= 1
            if depth == 0:
                body = seg[i:j]
                break
    else:
        return set()
    # 去掉注释行，再抓键（键一定带引号）
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    return set(re.findall(r'"([^"]+)"\s*:', body))


KEYS = {d: _dispatch_keys(d) for d in DOMAINS}
empty = [d for d, k in KEYS.items() if not k]
ck("…12 个域的 dispatch 键都解析出来了（解析不到就是本钉子自己瞎了，先修它）",
   not empty, f"没解析出键的域：{empty}")
print("     " + " · ".join(f"{d}={len(KEYS[d])}" for d in DOMAINS))

# ── 2. 扫文档里的"调用形状" ──
TOMB = ("🗑️", "已删", "退役", "旧版", "不存在", "~~", "从来不是", "已撤", "历史残留")
CALL = re.compile(r"\b(" + "|".join(DOMAINS) + r")\s*\(\s*ops\s*=")


def _tokens(seg):
    """从 `ops=` 后面那一小段里抠 token（到第一个逗号或右括号为止，只认引号里的）。"""
    cut = len(seg)
    for ch in (",", ")"):
        k = seg.find(ch)
        if k >= 0:
            cut = min(cut, k)
    seg = seg[:cut]
    toks = []
    for q in re.findall(r'"([^"]*)"', seg):
        for t in re.split(r"[\s/,]+", q):
            t = t.strip()
            if t and t not in ("…", "..."):
                toks.append(t)
    return toks


bad = []
lines = DOC.splitlines()
for no, ln in enumerate(lines, 1):
    if any(t in ln for t in TOMB):
        continue
    for m in CALL.finditer(ln):
        dom = m.group(1)
        for t in _tokens(ln[m.end():]):
            if t not in KEYS.get(dom, set()):
                bad.append((no, dom, t, ln.strip()[:110]))
    for m in re.finditer(r"\b(" + "|".join(DOMAINS) + r")\s+ops=([A-Za-z_][\w]*)", ln):
        dom, t = m.group(1), m.group(2)
        if t not in KEYS.get(dom, set()):
            bad.append((no, dom, t, ln.strip()[:110]))

for no, dom, t, ln in bad:
    print(f"  ❌ 文档 :{no} 把 `{t}` 当成 `{dom}` 的 op —— dispatch 里没有这个键")
    print(f"       {ln}")
fails.extend(f"文档 :{no} {dom} ops={t}" for no, dom, t, _ in bad)
ck("…文档里**每一个当调用写出来的 op** 都在对应域 dispatch 里", not bad,
   f"{len(bad)} 处（见上）" if bad else "")

print()
if fails:
    print(f"❌ {len(fails)} 条没过")
    sys.exit(1)
print("🎉 全过：`TOOL_INVENTORY.md` 里当调用写的 op 全部真实存在（历史残留会被这条挡住）")
