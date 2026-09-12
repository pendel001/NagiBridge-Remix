# -*- coding: utf-8 -*-
"""
🎣 fair_fishing.py — 秋收节(星露谷展览会)钓鱼小游戏兜底（钓上一条→补下一竿）

前置：AI 已选中"游戏（50金）"，进入 FishingGame 小游戏（player.minigame=='FishingGame'）。

机制（2026-08-28 恒实测反编译；2026-09-12 把"补竿"改成**自适应**）：
- 秋收钓鱼是**独立 Minigame**（Maps/FishingGame），水是画的 ⇒ fishbot（普通水域 mod）在这里**可能**只认一竿。
- fishbot 能走完整周期：on → isCasting(抛)→isFishing(入水)→isNibbling/hit(咬钩)→isReeling(收线=钓上)。
- **解**：靠 `/state.player.fishing` 状态机数鱼（isNibbling/hit/isReeling 活跃后回到空 = 钓上一条，
  绿藻不走 isReeling）；钓上后**先看它自己补没补**，没补才 off→on（见下面 ⚠️）。
- 结算期(玩家已回摊位/loc!=fishingGame)绝不能抛。读 festivalScore 差值=星币（starTokensWon 加进去）。

⚠️ **别押注"它一定不自己补"**（2026-09-12 恒）：08-28 写这脚本时 fishbot 是 **0.3.0**，观测到
  "假水 minigame 里钓上后不自己补"，于是每钓一条人工 off→on 踢一竿。但后来升到 **0.6.1**，
  **真水那边（冬钓节冰洞）它已经会自己 re-cast 了**——脚本再踢就是"双抛"（恒 09-12 真机原话：
  "鱼刚到手，抛竿入水后马上又抛了一杆"）。**假水这边到底补不补，没验过** ⇒ 本脚本改成
  **钓上后先等 `SELF_RECAST_GRACE` 秒看竿有没自己动起来**：动了=不踢（记 self_recasts），
  没动才踢（记 kicks）。**两种 fishbot 行为都能正确跑**，且收工行会报"自补 N 次/踢 N 次"——
  这就是"它到底补不补"的实测证据（别猜，看日志）。

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
# 🆕 2026-09-12：判"它自己补竿了没"的键——**故意不含 isReeling**（收线是上一条的尾巴，
#   拿它判会把"刚钓上"误读成"新一竿已开始"）。只认抛/入水/咬钩这三个"新一竿"的相位。
_RECAST_KEYS = ("isCasting", "isFishing", "isNibbling", "hit")
SELF_RECAST_GRACE = 2.0   # 钓上后等它自补的观察窗（真自补 ~1s 内就进 isCasting）；**等满也不比原来的 sleep(2.0) 慢**
FIRST_CAST_GRACE = 1.0    # 开局那一踢前的观察窗：小游戏注册要 ~1.5s，这期间 fishbot 可能已自己起竿


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

    def _wait_self_recast(self, sec):
        """等 `sec` 秒看 fishbot **自己**有没有补下一竿：窗口内竿重新进抛/入水/咬钩相位 → True。

        ⚠️ 窗口本身就是"鱼/藻飞到手"的结算时间（原来这里是固定 `sleep(2.0)`）⇒ **没自补时等满也不慢**，
           自补时还能提前返回。**别改成"看一眼就决定"**——那 0.2s 里竿必然是空的，等于每次都踢，
           就是双抛的老毛病。"""
        end = time.time() + sec
        while time.time() < end:
            f = (self.state().get("player") or {}).get("fishing") or {}
            if any(f.get(k) for k in _RECAST_KEYS):
                return True
            time.sleep(POLL)
        return False

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

        # 先 kick 第一竿（⚠️ 2026-09-12：**竿已经在动就别踢**——小游戏注册 ~1.5s，这段里
        #   fishbot 可能自己就起竿了，再 off→on 等于把第一竿丢掉，白白浪费小游戏里的一竿时间）
        if self._wait_self_recast(FIRST_CAST_GRACE):
            log("  ✅ 开局竿已在动（fishbot 自己起竿了），不踢")
        else:
            self.fishbot("off")
            self.fishbot("on")
            time.sleep(1.0)   # 等抛竿动画
        catches = 0
        self_recasts = 0   # fishbot 自己补了几次（2026-09-12：这俩计数就是"它到底补不补"的实测证据）
        kicks = 0          # 脚本 off→on 踢了几次
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
                #    切太快会没结算、钓到绿藻就停 → 窗口要等满（`_wait_self_recast` 的窗口就是这段）。
                catches += 1
                was_biting = False
                if self._wait_self_recast(SELF_RECAST_GRACE):
                    self_recasts += 1
                    log(f"  🐟 钓上第 {catches} 条 → fishbot 自己补上了下一竿（不踢）")
                else:
                    # 没自补（= 0.3.0 时代的老行为）→ 才由脚本踢。**别再改回无条件踢**：
                    # fishbot 若已适配假水（0.6.1 起真水那边就自补了），无条件踢=每钓一条双抛一次。
                    kicks += 1
                    log(f"  🐟 钓上第 {catches} 条 → fishbot 没自己补 → off→on 踢一竿")
                    self.fishbot("off")
                    self.fishbot("on")
                    time.sleep(1.0)          # 等抛竿动画
                last_work = time.time()
            elif time.time() - last_work > STALL:
                # ⚠️ 兜底只在这两种情况触发：鱼竿彻底没在干活（根本没抛/真卡住）。
                #    绝不能在"等咬钩"(working 保持)时触发——那会掐掉慢咬钩的竿（恒 2026-08-28）。
                # 🆕 2026-09-12：顺手把**当前菜单**打出来——`st` 这一轮本来就取好了，**零成本**。
                #    意义：恒怀疑"Fishbot 补饵兜底会弹背包菜单"把竿卡死（未坐实），这行日志就是
                #    下次真机的证据（鱼竿空 6s 时到底有没有菜单）。**只报不做**：真抓到再写关闭护栏。
                _mt = ((st.get("activeMenu") or {}).get("type") or "")
                _mtxt = ("⚠️当前开着菜单=" + _mt) if _mt else "无菜单"
                log(f"  ⚠️ 兜底：鱼竿空 {STALL}s 没动静（{_mtxt}）→ 补一竿")
                kicks += 1
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
        # ⚠️ "自补/踢"两个数**别删**：这是"fishbot 0.6.1 在假水 minigame 里到底补不补竿"的
        #    实测证据（恒 2026-09-12：要是它适配了，脚本就得跟着改）。自补=0 且踢≈条数 ⇒ 还是老行为；
        #    自补≈条数 ⇒ 它已经会自己补，脚本只在兜底踢（本该如此）。
        log(f"🎣 秋收钓鱼完成：钓 {catches} 条（fishbot 自补 {self_recasts} 次 / 脚本踢 {kicks} 次），"
            f"得 ⭐星币 +{delta}（总 {after}）")
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
