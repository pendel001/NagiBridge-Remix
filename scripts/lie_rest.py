#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""🛏 躺床监控：轮询体力/血，**回满再收工播报**。

为什么单独一个脚本（恒 2026-09-29）：
    我算了回复速度（`Farmer.cs:7637`：躺在床上、联机、时间在走 ⇒ **每 500ms 各回 1 点**），
    恒：「既然能查到这个回复速度！我们要不要做'回复到百分之几'的阻塞+异步？**睡好再唤醒 AI**。」
    ⇒ 躺下那半截走**现成的路**（`lie_bed`：走回去 + `approach_bed`，同步做完，**不动**），
      这个脚本只管**等** —— 进 `_ASYNC_SCRIPTS` 白名单，MCP 一转后台，
      AI 的回合就挂到唤醒点，收工播报（`_bg_block_until_wake`）直接弹回它眼前。

⚠️ **不设目标百分比**（恒 2026-09-29：「算了，不设目标了。**给它当前百分比了，够不够它自己看着办**」）
   ⇒ 这里就是**躺到回满**；播报里写清"回到多少了"，下次要不要躺 AI 自己判。
⚠️ **>20:00 那一脚不在这里**（恒：「超过当天 20:00 踹它一脚…**如果它执意执行第二次躺，那就让它躺**」）
   —— 那一脚在 MCP 那侧（第一次直接不开长等待）。放这里就会把"执意第二次"也踹掉，**跟恒的原话相反**。
⚠️ 只读 + 只汇报：**不快进、不动角色、不替 AI 做决定**。
"""
import argparse
import json
import sys
import time
import urllib.request

POLL_S = 2.0        # 轮询间隔（回 1 点要 500ms，2 秒够密了）
SAFETY_MIN = 30     # 兜底上限（分钟）：正常回满 474 体力 ≈ 4 分钟，30 分钟还没满 = 出事了


def _get(port: int, path: str) -> dict:
    with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _fmt(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m} 分 {s} 秒" if m else f"{s} 秒"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7843, help="AI 的端口（由 MCP 注入，别写死）")
    ap.add_argument("--who", default="", help="躺谁的床（只用来写进播报）")
    ap.add_argument("--safety-min", type=float, default=SAFETY_MIN)
    args = ap.parse_args()

    t0 = time.time()
    try:
        p = (_get(args.port, "/state").get("player") or {})
    except Exception as e:
        print(f"❌ 读不到状态（游戏没开 / 端口不对）：{type(e).__name__}: {e}")
        return 1

    s0, ms0 = int(p.get("stamina") or 0), int(p.get("maxStamina") or 0)
    h0, mh0 = int(p.get("health") or 0), int(p.get("maxHealth") or 0)
    if not ms0 or not mh0:
        print(f"❌ 读不出体力和血的上限（maxStamina={ms0} maxHealth={mh0}）—— 不猜，直接收工")
        return 1

    if s0 >= ms0 and h0 >= mh0:
        print(f"🛏 本来就是满的（体力 {s0}/{ms0} · 血 {h0}/{mh0}）—— 不用躺，收工。")
        return 0

    def pct(v, m):
        return round(v / m * 100)

    print(f"🛏 躺下了（{args.who or '自己'}的床）：体力 {s0}/{ms0}（{pct(s0, ms0)}%）"
          f" · 血 {h0}/{mh0}（{pct(h0, mh0)}%）—— 回满我叫你。", flush=True)

    deadline = t0 + args.safety_min * 60
    while True:
        time.sleep(POLL_S)
        try:
            p = (_get(args.port, "/state").get("player") or {})
        except Exception:
            continue                      # 单次读失败不算数，接着读（游戏卡一下很常见）
        s, ms = int(p.get("stamina") or 0), int(p.get("maxStamina") or 0) or ms0
        h, mh = int(p.get("health") or 0), int(p.get("maxHealth") or 0) or mh0
        if s >= ms and h >= mh:
            print("\n" + _receipt(t0, s0, ms0, h0, mh0, s, ms, h, mh, args.who, "回满"))
            return 0
        if time.time() > deadline:
            print("\n" + _receipt(t0, s0, ms0, h0, mh0, s, ms, h, mh, args.who, "兜底超时"))
            return 0


def _receipt(t0, s0, ms0, h0, mh0, s, ms, h, mh, who, why) -> str:
    """收工播报 —— 三段（恒 2026-09-29 定稿）：
    ① 躺了多久 ② **回到多少了**（他点名要的） ③ **这是躺不是睡 + 睡要怎么做**。"""
    pct = lambda v, m: round(v / m * 100)
    tail = ""
    if why == "兜底超时":
        tail = f"\n   ⚠️ 等了 {SAFETY_MIN} 分钟还没满就收工了（读数可能一直在变，也可能卡住了）"
    return (f"🛏 躺了 {_fmt(time.time() - t0)}（{why}）\n"
            f"   💪 体力 {s0} → **{s}/{ms}**（{pct(s0, ms0)}% → {pct(s, ms)}%）\n"
            f"   ❤️ 血 {h0} → **{h}/{mh}**（{pct(h0, mh0)}% → {pct(h, mh)}%）\n"
            f"   ⚠️ 这是**躺**（不过夜，日没结束）—— 想真过夜走 `daily sleep who={who or '自己'}`"
            f"{tail}")


if __name__ == "__main__":
    sys.exit(main())
