# -*- coding: utf-8 -*-
"""
🎣 ice_fishing.py — 冰雪节(冬8)冰钓比赛自动化（钓上一条→补下一竿，钓满2分钟 max 防御威利）

前置：AI 已在冰雪节场地(冬8),脚本自己走：站位→等比赛开始(festivalTimer>0)→fishbot循环补竿→钓满2分钟→结算。

机制（2026-08-28 恒反编译 Data/Festivals/winter8 + 实测）：
- 真实的是**常规钓鱼**(BobberBar)，但**冰洞和秋收 FishingGame 一样只认一次**——fishbot 抛一竿钓上后
  **不会自己补**，必须 off→on toggle 逼它补下一竿（恒确认沿用秋收那套）。
- 比赛**限时 2 分钟**，`festivalTimer`(ms) 倒计时；`afterIceFishing`→`endContest` 结算，赢家
  `awardFestivalPrize` 自动发奖（首胜=水手帽套件，之后=奖券）。
- **只算从冰洞钓上来的鱼**；比赛鱼计入比赛计数、**不进背包**(结束后放生)。
- **赢线 ≥5 条**(vs 潘姆/威利/艾利欧特；威利是强手→**钓满 2 分钟**，不提前收)。
- festivalScore 是否实时=鱼数**不确定**(恒)→ 脚本**用 /state.player.fishing 状态机自己数**，
  festivalTimer 只判开始/结束，festivalScore 结尾校验。

⚠️ 站位(恒实测)：最上面的窟窿上钩率最高。满级(10/15级)站 (69,36) 朝上满力抛正好落洞 (69,30)。
  **默认就该从满级位置(69,36)起抛**（恒 2026-09-12：玩到冬天基本钓鱼满级，从"0/3级距离"起抛不合常理——
  上次轮回就是因为站太近、抛过头，得重抛才进）；抛不进（没咬钩）才**往前挪**(朝洞口 y-1/-2)，
  最多挪到低钓技的最近点 (69,34)。见 `_POS_TRY` / `_calibrate`。
⚠️ 勿 Esc/dismount 中断比赛(会甩回农场)；让它自然钓完等结算发奖。

用法:
  python ice_fishing.py [--port 7843] [--spot 69,36] [--face 0] [--deadline 165]
"""

import sys
import time
import os
import argparse

os.environ.setdefault("NAGI_URL", "http://localhost:7843")

# 冰钓比赛 2 分钟(120000ms)；deadline 留足 校准(≤10s)+等开赛(≤30s)+比赛(120s)+结算(15s)。
DEFAULT_DEADLINE = 175
CONTEST_WAIT = 30    # 站位等比赛开始(festivalTimer>0)上限：等不来=没开赛，退出别干耗
SETTLE_WAIT = 15     # 进结算后等 awardFestivalPrize 发奖（endContest→winner→award，~数秒）
POLL = 0.2           # 钓鱼状态轮询间隔（轻，只判"钓上没"）
STALL = 6            # 停滞超时：鱼竿一直没咬钩/失败卡住 N 秒 → 强制补一竿（防失败卡死）
CALIBRATE = 12       # 站位校准：fishbot 开后等 N 秒让**第一竿抛完+咬钩**，没咬到再挪位（别没抛完就撤）
_POS_TRY = [(0, 0), (0, -1), (0, -2)]  # 恒 2026-09-12 翻方向：**从满级位(69,36)起**，抛不进才往前挪(y-1/-2)
                                       # 走到低钓技最近点(69,34)为止。别反过来从 34 起——冬季基本满级，
                                       # 从"短抛距"位起抛会抛过头，得重抛才进（上次真机就是这么浪费掉一竿的）
                                       # ⚠️ 恒 2026-08-28：低钓技抛竿短，只能站**最近点**且**往后退**；往前挪(靠洞)会越抛越远
_REEL_KEYS = ("isCasting", "isFishing", "isNibbling", "isReeling")


def log(msg):
    try:
        print(f"[icefishing] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[icefishing] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


class IceFish:
    """冰雪节冰钓兜底：站位→等开赛→钓上一条(收线结束)→fishbot off→on 补下一竿，钓到 festivalTimer=0。"""

    def __init__(self, port=7843):
        self.base = f"http://localhost:{port}"
        self._spot = (69, 36, 0)     # (x, y, face|0=上) **满级满力落洞点**（恒 2026-09-12：默认从满级位起）；
                                     # 抛不进由 _calibrate 往前挪到 (69,34)。可 --spot 覆盖

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

    def festival(self):
        return self._get("/festival")

    def fishbot(self, action):
        return self._post("/fishbot", {"action": action})

    def set_pause(self, out_of_focus):
        return self._post("/set_pause", {"outOfFocus": out_of_focus})

    def _festival_timer(self):
        return int((self.festival() or {}).get("festivalTimer") or -1)

    def _festival_score(self):
        return int((self.festival() or {}).get("festivalScore") or 0)

    def _is_winter8(self, st):
        t = st.get("time") or {}
        return str(t.get("season") or "").lower() == "winter" and int(t.get("dayOfMonth") or 0) == 8

    # ---------- 站位 ----------
    def _go_spot(self, x, y, face):
        """走到 (x,y) 并朝向 face(0=上)。赛后 warpFarmers 已把 AI 丢在洞口行，直接补定位到最佳洞。"""
        loc = (self.state().get("location") or {}).get("name", "Temp")
        self._post("/walk_to", {"location": loc, "x": x, "y": y})
        deadline = time.time() + 8
        while time.time() < deadline:
            p = (self.state().get("player") or {})
            if abs(p.get("x", 0) - x) <= 1 and abs(p.get("y", 0) - y) <= 1 and not p.get("isMoving"):
                break
            time.sleep(0.3)
        self._post("/face", {"direction": face})

    def _calibrate(self):
        """开赛/warpFarmers 后才做（fishbot 已能抛）：走到该站位→fishbot 开后等咬钩，
        没落洞(没 isNibbling/hit)就往洞口挪 y-1/-2 重试。返回站稳 (x,y) 或 None（全部失败）。
        满级默认(69,36)一次就够；低等级自动往前挪。⚠️赛后 warpFarmers 已把 AI 甩到洞口行，这里补定位到最佳洞。"""
        base_x, base_y, face = self._spot
        for (dx, dy) in _POS_TRY:
            tx, ty = base_x + dx, base_y + dy
            # ⚠️ 恒 2026-08-28：先 off 停当前竿，再走位（别在抛竿时走位把竿打断）——上次就是"没抛完就挪"导致抛不到。
            self.fishbot("off")
            self._go_spot(tx, ty, face)
            self.fishbot("on")
            log(f"  📍 试站位 ({tx},{ty}) 朝{face}…")
            end = time.time() + CALIBRATE
            established = False
            while time.time() < end:
                f = (self.state().get("player") or {}).get("fishing") or {}
                if f.get("isNibbling") or f.get("hit") or f.get("isReeling"):
                    established = True
                    break
                time.sleep(POLL)
            if established:
                log(f"  ✅ ({tx},{ty}) 落洞咬钩，定站位")
                return tx, ty
        return None

    # ---------- 主流程 ----------
    def run(self, deadline=DEFAULT_DEADLINE):
        st = self.state()
        if not self._is_winter8(st):
            t = st.get("time") or {}
            log(f"⚠️ 非冰雪节（season={t.get('season')}, day={t.get('dayOfMonth')}），退出")
            return False
        loc = (st.get("location") or {}).get("name", "") or ""
        if loc not in ("Temp", "Forest-IceFestival"):
            log(f"⚠️ 不在冰雪节场地（loc={loc}），退出")
            return False
        try:
            self.set_pause(False)
        except Exception:
            pass
        before = self._festival_score()
        log(f"🌋 冰雪冰钓兜底启动（等开赛→走位→钓满2分钟→结算），deadline={deadline}s，start festivalScore={before}")

        # ① 等比赛开始（festivalTimer>0）——开赛瞬间 warpFarmers 会把 AI 甩到洞口行，故走位必须挪到开赛后做
        saw_contest = self._festival_timer() > 0
        wait_start = time.time()
        while not saw_contest and time.time() - wait_start < CONTEST_WAIT:
            saw_contest = self._festival_timer() > 0
            time.sleep(0.5)
        if not saw_contest:
            log(f"⚠️ {CONTEST_WAIT}s 内比赛未开始（festivalTimer 一直<=0），退出（先让 AI/玩家对话刘易斯开赛）")
            self.fishbot("off")
            return False
        log("  🏁 比赛开始，开钓！")

        # ② 走位：**先站满级落洞点 (69,36) 朝上**（玩到冬天基本满级）；抛不进由 _calibrate 往前挪 (69,35)/(69,34)
        #    ⚠️ 恒 2026-08-28：比赛限时 2min，走位是**必需的另一步**——开赛后 AI 被 warp 到洞口行，但未必是
        #        落洞点；此步把它带到位，咬钩确认后才进主循环，保证抛竿落洞。低钓技抛竿短只能站近点。
        if self._calibrate() is None:
            log("⚠️ 站位校准失败（可能没站到洞口），仍尝试默认点位；真不行请人工挪位")

        # ③ 钓鱼主循环：钓上一条→off→on 补竿，钓到 festivalTimer=0（比赛结束）
        catches = 0
        was_biting = False
        last_work = time.time()
        start = time.time()
        while time.time() - start < deadline:
            st = self.state()
            f = (st.get("player") or {}).get("fishing") or {}
            tm = self._festival_timer()
            if tm <= 0:      # 比赛结束（afterIceFishing 结算开始）→ 停
                log(f"  🏁 比赛结束(festivalTimer={tm})，钓鱼状态={f}")
                break
            # 普适"钓上"检测：鱼走 isNibbling→hit→isReeling；绿藻等不走 isReeling。
            working = bool(f.get("isCasting") or f.get("isFishing") or f.get("isNibbling") or f.get("isReeling"))
            biting = bool(f.get("isNibbling") or f.get("hit") or f.get("isReeling"))
            if working:
                last_work = time.time()
            if biting:
                was_biting = True
            elif was_biting:
                catches += 1
                log(f"  🐟 钓上第 {catches} 条 → fishbot 自己补下一竿")
                was_biting = False
                # ⚠️ 2026-08-28 恒实测：冰洞是**真水**，fishbot 钓上后**自己 re-cast 补竿**（不像秋收假水只认一竿）
                #      → 千万别 off→on，否则双抛（恒看到"鱼刚到手，抛竿入水后马上又抛了一杆"）。
                #        只让它自然循环；真卡住交给下面 STALL 兜底 re-kick。
                last_work = time.time()
                time.sleep(0.6)   # 轻节奏，等 fishbot 进下个 cast 周期
            elif time.time() - last_work > STALL:
                # 兜底只在这两种情况下触发：鱼竿彻底没干活（fishbot 真停了/卡住）。绝不在等咬钩时触发（防掐慢咬钩）。
                # 🆕 2026-09-12：顺手把**当前菜单**打出来（`st` 这轮本来取好了，**零成本**）——恒怀疑
                #    "Fishbot 补饵兜底会弹背包菜单"卡死钓竿（未坐实），这行就是下次真机的证据。
                #    **只报不做**：真抓到菜单再写关闭护栏（别提前上兜底）。
                _mt = ((st.get("activeMenu") or {}).get("type") or "")
                _mtxt = ("⚠️当前开着菜单=" + _mt) if _mt else "无菜单"
                log(f"  ⚠️ 兜底：鱼竿空 {STALL}s 没动静（{_mtxt}）→ re-kick 一竿")
                self.fishbot("off")
                self.fishbot("on")
                last_work = time.time()
                time.sleep(1.0)
            time.sleep(POLL)

        self.fishbot("off")
        log(f"  ⏹️ 比赛结束，钓了 {catches} 条；等结算发奖…")
        # endContest → afterIceFishing(叙述) → cutscene iceFishingWinner → awardFestivalPrize，~数秒。
        _end = time.time() + SETTLE_WAIT
        while time.time() < _end:
            if self._festival_timer() <= 0 and ((self.state().get("player") or {}).get("fishing") is None):
                break
            time.sleep(0.5)
        time.sleep(1.0)
        after = self._festival_score()
        try:
            self.set_pause(True)
        except Exception:
            pass
        win = "✅ 赢线(≥5)" if catches >= 5 else "❌ 不足5条"
        log(f"  💰 festivalScore {before} → {after}（Δ{after - before}）")
        log("===ICEFISH_RESULT===")
        log(f"🎣 冰雪冰钓完成：钓 {catches} 条 {win}（festivalScore={after}）")
        log("===END===")
        return True


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="[festival] 冰雪节冰钓比赛自动化（钓上一条补一竿，钓满2分钟）")
    parser.add_argument("--port", type=int, default=None, help="AI 角色端口（默认7843）")
    parser.add_argument("--spot", default="69,36", help="站位 (x,y)，默认 69,36（**满级满力落洞点**；抛不进自动往前挪到 69,34）")
    parser.add_argument("--face", type=int, default=0, help="抛竿朝向 0=上（默认）")
    parser.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE, help="整段上限秒（默认175）")
    args = parser.parse_args()
    import re as _re
    from bomb_common import NAGI_URL
    port = args.port or int(_re.search(r'(\d+)', NAGI_URL).group(1))
    sx, sy = (int(v) for v in args.spot.split(","))
    bot = IceFish(port)
    bot._spot = (sx, sy, args.face)
    ok = bot.run(deadline=args.deadline)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
