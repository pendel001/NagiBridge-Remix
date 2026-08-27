# -*- coding: utf-8 -*-
"""
🎣 fair_fishing.py — 秋收节(星露谷展览会)钓鱼小游戏兜底（钓上一条→补下一竿）

前置：AI 已选中"游戏（50金）"，进入 FishingGame 小游戏（player.minigame=='FishingGame'）。

机制（2026-08-28 恒实测反编译）：
- 秋收钓鱼是**独立 Minigame**（Maps/FishingGame），水是画的，fishbot（普通水域 mod）只认得一次，
  钓上一条后**不会自己补抛**。
- 但 fishbot 能走完整周期：on → isCasting(抛)→isFishing(入水)→isNibbling/hit(咬钩)→isReeling(收线=钓上)。
- **解**：靠 `/state.player.fishing` 的 isReeling（收线=钓上鱼）。每钓上一条(isReeling 由 T 变空)，
  立刻 fishbot off→on 逼它补下一竿，循环到游戏进结算为止。
- 结算期(玩家已回摊位/loc!=fishingGame)绝不能抛。读 festivalScore 差值=星币（starTokensWon 加进去）。

⚠️ 必须进 FishingGame 才跑；需 Fishbot mod。失焦暂停会让游戏时间不走→先 set_pause(False)。
⚠️ 勿 Esc 中途退出（会搞坏节日交互、把玩家甩回农场）——让它自然钓完。

用法:
  python fair_fishing.py [--port 7843] [--deadline 95]
"""

import sys
import time
import os
import argparse

os.environ.setdefault("NAGI_URL", "http://localhost:7843")

# 钓鱼阶段时长上限（游戏 gameEndTimer=100s；留余量别等太久，结算前停）
DEFAULT_DEADLINE = 95
SETTLE_WAIT = 9      # 进结算后等它发星币（showResultsTimer<=5000 才加奖，~5s 后；多留余量）
POLL = 0.2           # 钓鱼状态轮询间隔（轻，只判"钓上没"）
STALL = 6            # 停滞超时：鱼竿一直没咬钩/失败卡住 N 秒 → 强制补一竿（恒 2026-08-28 防失败卡死）
_REEL_KEYS = ("isCasting", "isFishing", "isNibbling", "isReeling")


def log(msg):
    try:
        print(f"[fairfishing] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[fairfishing] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


class FairFish:
    """秋收节钓鱼兜底：钓上一条(收线结束) → fishbot off→on 补下一竿，循环到结算。"""

    def __init__(self, port=7843):
        self.base = f"http://localhost:{port}"

    def _post(self, ep, data=None):
        import requests
        try:
            return requests.post(f"{self.base}{ep}", json=data or {}, timeout=10).json()
        except Exception:
            return {}

    def _get(self, ep):
        import requests
        try:
            return requests.get(f"{self.base}{ep}", timeout=10).json()
        except Exception:
            return {}

    def state(self):
        return self._get("/state")

    def fishbot(self, action):
        return self._post("/fishbot", {"action": action})

    def set_pause(self, out_of_focus):
        return self._post("/set_pause", {"outOfFocus": out_of_focus})

    def festival_score(self):
        return int((self.state().get("player") or {}).get("festivalScore") or 0)

    def _idle(self, f):
        return not any(f.get(k) for k in _REEL_KEYS)

    def run(self, deadline=DEFAULT_DEADLINE):
        st = self.state()
        if (st.get("player") or {}).get("minigame") != "FishingGame":
            log(f"⚠️ 不在 FishingGame（minigame={(st.get('player') or {}).get('minigame')}），退出")
            return False
        # ⚠️ 2026-08-28 恒：防跑错场景——只认秋收节。冬钓大赛(森林=BobberBar)不是 fishingGame minigame，
        #    更保险：必须 正处 fishingGame 图 或 秋16 才跑，否则退出（防误触发）。
        loc = (st.get("location") or {}).get("name", "") or ""
        t = st.get("time") or {}
        if not ((str(t.get("season") or "").lower() == "fall" and int(t.get("dayOfMonth") or 0) == 16)
                or loc == "fishingGame"):
            log(f"⚠️ 场景不匹配(非秋收节钓鱼, loc={loc})，退出")
            return False
        try:
            self.set_pause(False)
        except Exception:
            pass
        before = self.festival_score()
        log(f"🌋 秋收钓鱼兜底启动（钓上一条→补下一竿），deadline={deadline}s，start festivalScore={before}")

        # 先 kick 第一竿
        self.fishbot("off")
        self.fishbot("on")
        time.sleep(1.0)   # 等抛竿动画
        catches = 0
        was_biting = False
        last_work = time.time()          # 上次鱼竿"在干活"的时间（含等咬钩）
        start = time.time()
        while time.time() - start < deadline:
            st = self.state()
            p = st.get("player") or {}
            mg = p.get("minigame")
            loc = (st.get("location") or {}).get("name", "")
            f = p.get("fishing") or {}
            # 离开钓鱼区（回摊位/结算/游戏结束）→ 停
            if mg != "FishingGame" or loc != "fishingGame":
                log(f"  🏁 离开钓鱼区(minigame={mg}, loc={loc})，鱼竿状态={f}")
                break
            # 普适"钓上"检测：鱼走 isNibbling→hit→isReeling；绿藻等不走 isReeling。
            # 只要"在钓"(nibbling/hit/reeling 任一)活跃后回到空 = 钓上一条（鱼或绿藻都算）。
            working = bool(f.get("isCasting") or f.get("isFishing") or f.get("isNibbling") or f.get("isReeling"))
            biting = bool(f.get("isNibbling") or f.get("hit") or f.get("isReeling"))
            if working:
                last_work = time.time()      # 鱼竿在干活(含等咬钩)——绝不误判为停滞
            if biting:
                was_biting = True            # 在钓（咬钩/命中/收线）→ 别打断，等它收完
            elif was_biting:
                # 从"在钓"回到空 = 钓上来了。⚠️ 恒 2026-08-28：钓上后 ~2s 才飞到手/结算，
                #    切太快会没结算、钓到绿藻就停 → 等 2s 让鱼/藻真进手，再补下一竿。
                catches += 1
                log(f"  🐟 钓上第 {catches} 条 → 等进手 2s，再补下一竿")
                was_biting = False
                time.sleep(2.0)              # 鱼/藻飞到手/结算完（勿切太快）
                self.fishbot("off")
                self.fishbot("on")
                last_work = time.time()
                time.sleep(1.0)              # 等抛竿动画
            elif time.time() - last_work > STALL:
                # ⚠️ 兜底只在这两种情况触发：鱼竿彻底没在干活（根本没抛/真卡住）。
                #    绝不能在"等咬钩"(working 保持)时触发——那会掐掉慢咬钩的竿（恒 2026-08-28）。
                log(f"  ⚠️ 兜底：鱼竿空 {STALL}s 没动静 → 补一竿")
                self.fishbot("off")
                self.fishbot("on")
                last_work = time.time()
                time.sleep(1.0)              # 等抛竿动画
            time.sleep(POLL)

        self.fishbot("off")
        log(f"  ⏹️ 钓鱼阶段结束，钓了 {catches} 条；等结算发完星币…")
        # ⚠️ 恒 2026-08-28：星币是结算屏里 showResultsTimer<=5000 才加（掉鱼/绿藻后 ~几秒）。
        #    固定 9s 会读太早（读到结算前=0 增量）→ 等游戏彻底结束(minigame 离开 FishingGame)再读。
        _end = time.time() + 20
        while time.time() < _end:
            if (self.state().get("player") or {}).get("minigame") != "FishingGame":
                break
            time.sleep(0.5)
        time.sleep(1.0)   # 缓冲，确保星币落帐

        after = self.festival_score()
        delta = after - before
        try:
            self.set_pause(True)
        except Exception:
            pass
        log(f"  💰 festivalScore {before} → {after}（+{delta}）")
        log("===FAIRFISH_RESULT===")
        log(f"🎣 秋收钓鱼完成：钓 {catches} 条，得 ⭐星币 +{delta}（总 {after}）")
        log("===END===")
        return True


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="[festival] 秋收节钓鱼小游戏兜底（钓上一条补一竿）")
    parser.add_argument("--port", type=int, default=None, help="AI 角色端口（默认7843）")
    parser.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE, help="钓鱼阶段时长上限秒（默认95）")
    args = parser.parse_args()
    import re as _re
    from bomb_common import NAGI_URL
    port = args.port or int(_re.search(r'(\d+)', NAGI_URL).group(1))
    bot = FairFish(port)
    ok = bot.run(deadline=args.deadline)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
