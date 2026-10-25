# -*- coding: utf-8 -*-
"""🧱 边界钉子 —— "玩家局走 MCP，内部件只给排障"（纯离线，不碰游戏、不起服务）。2026-10-25

来历（恒那一天原话）：
  「**我们的内部工具放得七七八八的，agent 端的 llm 就会不走 mcp 乱来**」
实测成立，三条都是真的：
  · `AGENTS.md` 原来那句「`scripts/` 下大量自动化脚本，Python 直接调 HTTP API」= **邀请函**；
    `AGENTS.md:41` 只说了"原始端点不是 AI 能直调的 **MCP 工具**"，限定的只是工具清单，
    **没有一句**说别用 shell 打 HTTP。
  · 守卫**全在 Python 层**（`stardew_api.warp()` 落点校验、`machine_loader` 同图不瞬移、菜单闸门），
    所以 `curl :7843/warp` = 整层跳过；跑 `scripts/xxx_run.py` 走 `stardew_api`，守卫还在，
    但绕过状态注入 / 单子 / 自动 `--port`（手跑不带 `--port` 会改到房主身上）。
  · 最讽刺：`.claudeignore` 把 `AGENTS.md` 标成"原作者遗留旧文档"**藏了起来** ——
    而那份恰恰是最新最全、唯一写着这条边界的。⇒ Claude 只看到半份，dsh 看到另一份。

钉四条（每条都防一类**已经发生过或一步就会发生**的漂移）：
  ① `AGENTS.md` 与 `CLAUDE.md` **各含且只含**一个 `<!-- NAGI-BOUNDARY -->` 块（边界段还在、没复制成两份）
  ② 边界块里必须同时出现 **`MCP`** 与 **`排障`**（说清"走哪条路"和"什么时候才允许离开"）
  ③ `.claudeignore` **不许**再把 `AGENTS.md` / `CLAUDE.md` / `README.md` 藏起来
  ④ `AGENTS.md` 不许再出现「Python 直接调 HTTP API」这句无条件引导（改成"经 stardew_api、是排障工具"）

跑法：`python scripts/_boundary_selftest.py`   （退出码 0 = 全绿）
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

START = "<!-- NAGI-BOUNDARY -->"
END = "<!-- /NAGI-BOUNDARY -->"

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _read(p):
    try:
        with io.open(p, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _blocks(text):
    """返回所有边界块正文。块数不对 = ①失败。"""
    out = []
    rest = text
    while True:
        i = rest.find(START)
        if i < 0:
            return out
        j = rest.find(END, i + len(START))
        if j < 0:
            out.append(None)          # 只有开头没有结尾
            return out
        out.append(rest[i + len(START):j])
        rest = rest[j + len(END):]


print("\n① AGENTS.md / CLAUDE.md 各含且只含一个边界块")
for fn in ("AGENTS.md", "CLAUDE.md"):
    t = _read(os.path.join(ROOT, fn))
    if t is None:
        ck(f"{fn} 存在且可读", False)
        continue
    bs = _blocks(t)
    ck(f"{fn}：边界块 = 1 个", len(bs) == 1, f"实际 {len(bs)} 个")
    if len(bs) == 1 and bs[0] is not None:
        body = bs[0]
        print("\n② " + fn + " 边界块讲清了两件事")
        ck(f"{fn}：提到 MCP", "MCP" in body)
        ck(f"{fn}：提到排障", "排障" in body)

print("\n③ .claudeignore 不许藏说明书")
ci = _read(os.path.join(ROOT, ".claudeignore"))
if ci is None:
    ck(".claudeignore 存在且可读", False)
else:
    hidden = [ln.strip() for ln in ci.splitlines()
              if ln.strip() in ("AGENTS.md", "CLAUDE.md", "README.md", "PROJECT.md")]
    ck(".claudeignore 未藏 AGENTS/CLAUDE/README/PROJECT.md", not hidden, f"被藏：{hidden}")

print("\n④ AGENTS.md 没有无条件的「直连」引导")
t = _read(os.path.join(ROOT, "AGENTS.md")) or ""
ck("AGENTS.md 无「Python 直接调 HTTP API」", "Python 直接调 HTTP API" not in t)

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
    print("   ⇒ 说明书漂了。修 AGENTS.md/CLAUDE.md/.claudeignore，**别改这个钉子**。")
    sys.exit(1)
print("✅ 边界四条全绿：玩家局走 MCP 的 16 个工具，内部件留给排障。")
sys.exit(0)
