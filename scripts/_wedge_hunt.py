# -*- coding: utf-8 -*-
"""🔪 卡死凶手猎手（2026-09-12）—— 一次只打一个工具，每次打完探活。

**为什么写**：跑批次A时，`script.async show` 之后连着几个调用全部 60s 超时，
但游戏端口（7842/7843）0.03s 就回 —— 僵的是 **:8000 的 MCP 服务本身**，
而且是**整个 uvicorn 事件循环**僵住（连 `GET /` 都不回），CPU 只烧了 2.6s ⇒ **阻塞**不是死循环。
一串调用一起打，看不出是哪一发把事件循环钉住的，所以拆成"一次一发 + 每次探活"。

**判据**：某发之后 `GET /`（3s 超时）不回了 ⇒ **上一条就是凶手**，立刻停，不再往下打
（事件循环僵住后，后面的调用根本没被处理，接着打只会白等 60s）。

用法:
    python _wedge_hunt.py                 # 跑内置的嫌疑清单
    python _wedge_hunt.py --only fish     # 只跑某几条（按名字子串筛）
    python _wedge_hunt.py --file x.json   # 跑外部清单（[[标签,工具,参数JSON],…]），测试批次靠这个

⚠️ 全工具测试的 T2/T3 **一律走这里**，别写 for 循环一口气打 —— 一发把事件循环钉住，
   后面的调用根本不会被处理，你会白等 N×60s 还看不出是谁干的（当天就是这么丢了一轮）。
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# ⚠️ mcp_cli 自己的调用超时是 60s（`_call_retry` 里 timeout=60）。shell 侧的 timeout 要比它大，
#    否则看到的是"被 shell 砍掉"而不是"客户端等满 60s"，两种现象会混。
CLIENT_TIMEOUT = 60
SHELL_TIMEOUT = CLIENT_TIMEOUT + 15

# (标签, 工具, 参数JSON) —— 批次A当天打过的，按原样重放
SUSPECTS = [
    ("A1 script.async show",  "script", '{"ops":"async","kw":{"show":true}}'),
    ("A2 script.stop",        "script", '{"ops":"stop"}'),
    ("A3 script.continue",    "script", '{"ops":"continue"}'),
    ("A4 fish.bobber",        "fish",   '{"ops":"bobber","kw":{"style":"dice"}}'),
    ("A5 menu.know",          "menu",   '{"ops":"know"}'),
]


def alive(timeout=3.0) -> bool:
    """裸 HTTP 探活：事件循环还转得动 = 能回任何 HTTP 响应（哪怕 404）。"""
    import requests
    try:
        requests.get("http://localhost:8000/", timeout=timeout)
        return True
    except requests.exceptions.ReadTimeout:
        return False          # 连上了但没人应 —— 事件循环被占住
    except requests.exceptions.ConnectionError:
        return False          # 进程没了
    except Exception:
        return True           # 其它（405/别的报错）说明还活着


def call(tool: str, args: str):
    """跑一次 mcp_cli → (返回首几行, 是否超时/失败)"""
    cmd = [PY, os.path.join(SCRIPT_DIR, "mcp_cli.py"), tool, args]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        p = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace",
                           timeout=SHELL_TIMEOUT, env=env, cwd=SCRIPT_DIR)
        out = (p.stdout or "") + (p.stderr or "")
        return out, p.returncode != 0
    except subprocess.TimeoutExpired:
        return "⏱ 客户端等满 %ds 仍无返回" % CLIENT_TIMEOUT, True


def _head(out: str, n: int = 3) -> str:
    """挑出**真正的正文**前几行。
    ⚠️ 2026-09-12 踩过：原来图省事写 `out.strip().splitlines()[1:4]` —— 但 mcp_cli 的输出里
    横幅/空行/分隔线/状态条会占位，按固定下标取会**取到空白**，看着像"工具返回空"，
    白查了一轮（真身 `script.stop` 明明打了"📭 没有后台脚本任务。"）。改成按内容筛。"""
    keep = []
    for l in out.splitlines():
        s = l.strip()
        if not s or l.startswith("🌿") or set(s) == {"╌"} or s.startswith("📍"):
            continue
        keep.append(s)
        if len(keep) >= n:
            break
    return "\n".join(keep)[:260] or "（空返回）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="按标签子串筛选")
    ap.add_argument("--file", default="", help="外部清单 json：[[标签,工具,参数JSON],…]")
    a = ap.parse_args()

    base = SUSPECTS
    if a.file:
        with open(a.file, encoding="utf-8") as f:
            base = [tuple(x) for x in json.load(f)]
    suspects = [s for s in base if a.only.lower() in s[0].lower()] or base

    if not alive():
        print("❌ 开跑前 :8000 就不响应了 —— 先确认服务起来了")
        return 1
    print(f"🔪 逐发试 {len(suspects)} 条（每条打完探活一次）\n")

    for tag, tool, args in suspects:
        print(f"── {tag} ──")
        t0 = time.time()
        out, bad = call(tool, args)
        ms = int((time.time() - t0) * 1000)
        head = "\n".join(out.strip().splitlines()[1:4])[:200] or "（空返回）"
        print(f"   调用 {ms}ms {'❌' if bad else '✅'}  {head}")
        if not alive():
            print(f"\n{'!'*66}")
            print(f"🔪 凶手 = 【{tag}】 —— 这一发之后 :8000 整个事件循环僵住")
            print(f"   （后面的调用不会再被处理，已停手，不白等 60s）")
            print(f"{'!'*66}")
            return 2
        print("   探活 ✅ 事件循环还在转")
        time.sleep(0.5)

    print("\n✅ 全部打完，没有一发把服务打死 —— 凶手不在这个清单里")
    return 0


if __name__ == "__main__":
    sys.exit(main())
