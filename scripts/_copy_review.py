# -*- coding: utf-8 -*-
"""🗣️ 返回文案预筛 —— 把日志里的真实往返做成"给新鲜 AI 看"的题包（2026-09-11）

**为什么需要**：文案好不好懂，**只有"不知道源码"的人能判**。写这段代码的人（或读过源码的 AI）
看什么都懂——因为懂的不是文案，是代码。所以判定必须交给一个**上下文里没有本仓库**的 AI：
它只拿到「AI 真能看到的东西」+「调用」+「返回原文」，复述它理解到了什么。

**这份脚本只做前半段**（出题）：从 `session_log.jsonl` 里挑真实调用，配齐
「AI 当时能看到的工具清单 + help(域) + 这次调用 + 返回原文」写成题包。
**后半段（判题）由 Claude Code 开空上下文 subagent 跑** —— 脚本开不了 subagent。
结果落 `COPY_REVIEW.md`，和 `TOOL_TEST_CHECKLIST.md` 一样是**能复查的产物**，不是口头结论。

用法:
    python _copy_review.py --list                  # 看日志里有哪些可出题的往返
    python _copy_review.py --build --limit 20      # 出前 20 道题 → _copy_review_packets.json
    python _copy_review.py --build --tier T1       # 只挑 T1 层的（只读，题包最安全）

⚠️ **它只出题，不判题、不打勾**。判定归空上下文 subagent，终判归手机端真机 AI。
"""
import argparse
import io
import json
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M            # noqa: E402
import gen_tool_checklist as G         # noqa: E402  复用清单的 op→层 判定

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "session_log.jsonl")
OUT_PATH = os.path.join(SCRIPT_DIR, "_copy_review_packets.json")

PROMPT = """你是星露谷里的一个 AI 农工，靠 MCP 工具操控角色干活。下面是你**这次**能看到的东西。

──── ① 你能看到的工具（就这 17 个） ────
{tools}

──── ② 你查过的 help(域) ────
{guide}

──── ③ 你刚才的调用 ────
{tool}({call})

──── ④ 工具返回的原文（含尾部状态条）────
{ret}

──── 请回答三个问题，每个问题**一到两句话**，别长篇大论 ────
1. **发生了什么**？只根据上面这段文字说你理解到了什么，不要推测背后的代码。
2. **下一步你会做什么**？具体到下一次工具调用。
3. **有没有让你误解/看不懂的地方**？原文引用那句话或那个词；没有就写"没有"。
   （特别留意：数字没有单位、状态条里的缩写、❌/⚠️ 分不清是警告还是失败、
     同一件事两种说法、返回里出现了你没传过的参数名。）
"""


def _tools_block() -> str:
    """AI 真看到的 17 个工具：名字 + 描述（就是注入进它上下文的那份）。"""
    keep = set(getattr(M, "_KEEP_TOOLS", []) or [])
    out = []
    for t in sorted(M.mcp._tool_manager.list_tools(), key=lambda x: x.name):
        if keep and t.name not in keep:
            continue
        desc = (t.description or "").split("\n")[0].strip()
        out.append(f"· {t.name}：{desc[:220]}")
    return "\n".join(out)


def _guide_block(tool: str) -> str:
    g = M._DOMAIN_GUIDES.get(tool, "")
    return g if g else "（这个工具没有 help 正文）"


def _call_str(r: dict) -> str:
    """把 args 还原成 AI 视角的调用样子：`ops="till", kw={...}`。"""
    a = r.get("args") or {}
    parts = []
    for k, v in a.items():
        parts.append(f'{k}={json.dumps(v, ensure_ascii=False)}')
    return ", ".join(parts)


def load_entries() -> list:
    if not os.path.exists(LOG_PATH):
        return []
    out = []
    for i, ln in enumerate(open(LOG_PATH, encoding="utf-8"), 1):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if not (r.get("ops") or "").strip():
            continue          # 没 op 的（help/screenshot）先不出题
        r["_line"] = i
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出日志里可出题的往返")
    ap.add_argument("--build", action="store_true", help="出题包")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--tier", default="", help="只挑某层（T1/T2/T3），空=全部")
    args = ap.parse_args()

    entries = load_entries()
    if args.list or not args.build:
        print(f"日志里可出题的往返：{len(entries)} 条")
        for r in entries:
            print(f"  :{r['_line']:<4} {r['tool']:<8} ops={r['ops']:<12} "
                  f"bytes={r['bytes']:<6} {r['ret'][:40].replace(chr(10), ' ')}")
        if not entries:
            print("  （空的 —— 先跑几个工具调用，走 :8000 的 MCP 就会自动落进 session_log.jsonl）")
        return 0

    # 层过滤：拿清单里的 op→层 映射（同一份数据，别另判一套）
    tier_of = {}
    if args.tier:
        for row in G._rows():
            tier_of[(row["domain"], row["op"])] = row["tier"]

    tools_block = _tools_block()
    packets = []
    for r in entries:
        if args.tier:
            ops = r["ops"].split()
            if not any(tier_of.get((r["tool"], o)) == args.tier for o in ops):
                continue
        packets.append({
            "line": r["_line"],
            "tool": r["tool"],
            "ops": r["ops"],
            "prompt": PROMPT.format(tools=tools_block, guide=_guide_block(r["tool"]),
                                    tool=r["tool"], call=_call_str(r), ret=r["ret"]),
        })
        if len(packets) >= args.limit:
            break

    json.dump(packets, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✅ 出题 {len(packets)} 道 → {OUT_PATH}")
    print("   下一步：Claude Code 拿每道题的 prompt 开**空上下文** subagent，"
          "把回答汇总进 COPY_REVIEW.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
