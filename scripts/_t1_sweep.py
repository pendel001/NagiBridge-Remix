# -*- coding: utf-8 -*-
"""🧪 T1 批量扫描器（2026-09-11）

**干什么**：把清单里 **T1（只读/幂等）** 的 op 逐个真调一遍，打屏汇总 PASS/FAIL。
**为什么单独写**：T1 全是只读，可以无脑扫；手敲 42 条 mcp_cli 命令行又慢又容易抄错 op 名。
**结果不落这里**——调用走的是 :8000 的 MCP，服务器自己写 `session_log.jsonl`，
之后 `python gen_tool_checklist.py --from-log` 就能按日志打勾（省得脚本再抄一份判定）。

⚠️ **只跑 T1**。T2 要摆场、T3 有副作用，别拿这个脚本去跑（会动游戏状态）。
⚠️ 走的是 `mcp_cli` 的同一套 transport（streamable-http 双 Accept 头 / SSE utf-8），
    别在这儿另写一份 HTTP —— 那份坑（Latin-1 乱码）已经踩过一遍了。

用法:
    python _t1_sweep.py                # 扫全部去重后的 T1
    python _t1_sweep.py --only check   # 只扫某个域
    python _t1_sweep.py --show         # 顺带打每个返回的前几行
"""
import argparse
import io
import json
import os
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcp_cli                       # noqa: E402  复用已验证的 transport
import gen_tool_checklist as G       # noqa: E402  op 清单的唯一来源

# ── 极少数 T1 op 需要值才能走完（其余留空就够）──
# ⚠️ 这里给的是**为了把这条路径走通**的值，不是"正确用法示例"；缺参报错也算有效结论。
ARGS = {
    ("check", "chests"): {"chest": -1},
    ("check", "look"): {"radius": 10},
    ("scene", "seats"): {"radius": 7},
    ("storage", "view"): {"box": -1},
    ("map", "lookup"): {"location": "Town"},
    ("map", "query"): {"function": "种子"},
    ("social", "friendship"): {"name": "Leah"},
    ("storage", "find"): {"name": "Wood"},
    ("fish", "info"): {"location": "Mountain"},
}

# 结构化的失败标志（和 gen_tool_checklist._BAD_PAT 同源，保持判定一致）
BAD_PAT = ("❌ op「", "❌ 未知操作「", "❌ 未知查询「", "❌ ops 为空", "⚠️ op「", "Traceback")


def call_tool(name: str, args: dict) -> tuple:
    """调一次工具 → (文本, 是否失败)。transport 抄 mcp_cli，别另写。"""
    res, _sid = mcp_cli._call_retry("tools/call", {"name": name, "arguments": args})
    if not res:
        return "❌ 无响应", True
    if res.get("error"):
        return "❌ " + json.dumps(res["error"], ensure_ascii=False), True
    content = (res.get("result") or {}).get("content") or []
    text = "\n".join(c.get("text") or "" for c in content if c.get("type") == "text")
    bad = text.startswith("❌") or any(p in text for p in BAD_PAT)
    return text, bad


def build_args(domain: str, op: str) -> dict:
    """域工具 → {ops: op, kw: {...}}；check 域特殊（what= 而非 ops=）。"""
    kw = ARGS.get((domain, op), {})
    if domain == "check":
        return {"what": op, "kw": kw}
    if domain == "help":
        return {"topic": op}
    return {"ops": op, "kw": kw}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只扫某域")
    ap.add_argument("--show", action="store_true", help="打返回前几行")
    args = ap.parse_args()

    rows = [r for r in G._rows() if r["tier"] == "T1" and not r["alias_of"]]
    if args.only:
        rows = [r for r in rows if r["domain"] == args.only]
    print(f"🧪 扫 {len(rows)} 个 T1 op（只读）\n")

    ok, bad, errs = [], [], []
    for i, r in enumerate(rows, 1):
        d, op = r["domain"], r["op"]
        a = build_args(d, op)
        t0 = time.time()
        try:
            text, is_bad = call_tool(d, a)
        except Exception as e:
            text, is_bad = f"❌ {e}", True
        ms = int((time.time() - t0) * 1000)
        tag = "❌" if is_bad else "✅"
        head = text.strip().split("\n")[0][:88] if text.strip() else "（空返回）"
        print(f"  {tag} {d}.{op:<13} {ms:>5}ms  {head}")
        (bad if is_bad else ok).append(f"{d}.{op}")
        if is_bad:
            errs.append((f"{d}.{op}", a, text))
        if args.show:
            for ln in text.strip().split("\n")[1:6]:
                print(f"       │ {ln[:110]}")
        time.sleep(0.15)   # 别把 MCP 打满，也留点时间给状态缓冲

    print(f"\n{'─'*70}\n✅ {len(ok)} ｜ ❌ {len(bad)}")
    if bad:
        print(f"\n❌ 失败清单：{' '.join(bad)}")
        print("\n" + "─" * 70)
        for name, a, text in errs:
            print(f"\n【{name}】 args={json.dumps(a, ensure_ascii=False)}")
            print(text.strip()[:900])
    print("\n下一步：python gen_tool_checklist.py --from-log")
    return 0


if __name__ == "__main__":
    sys.exit(main())
