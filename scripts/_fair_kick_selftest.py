# -*- coding: utf-8 -*-
"""🧪 fair_fishing「钓上后自己补没补」自适应的**离线**自测（2026-09-12）——不碰游戏。

为什么有它：秋收钓鱼脚本改成"钓上后先等 fishbot 自己补竿，没补才 off→on 踢"，可这**没条件真机测**
（要正好秋收节 + 进得了小游戏）。这里用**假时间线**喂真循环，把两条分支都跑一遍：

  A 老行为（钓上后它不自己补）→ 期望：踢一竿（kicks≥1、self_recasts==0）
  B 新行为（钓上后它自己补）  → 期望：不踢（self_recasts≥1、kicks==0）
  C 键集保险：只看 `isReeling` 不算"新一竿"（收线是上一条的尾巴）→ 期望 `_wait_self_recast` 返 False

⚠️ **这只是逻辑自测，不等于真机验过**：fishbot 在假水 minigame 里到底补不补竿，只有真机跑一次
   秋收钓鱼才知道（收工行会报"自补 N 次 / 踢 N 次"）。

跑法： `PYTHONIOENCODING=utf-8 python _fair_kick_selftest.py`
"""
import sys
import time

import fair_fishing as ff

FAILED = []


def _check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


class FakeBot(ff.FairFish):
    """把 /state、/fishbot 换成脚本化时间线（t = 从 run() 第一次取状态算起的秒数）。"""

    def __init__(self, profile, end_at=4.6):
        super().__init__(port=0)
        self.profile = profile
        self.end_at = end_at
        self.t0 = None
        self.fb_calls = []

    def _el(self):
        return 0.0 if self.t0 is None else (time.time() - self.t0)

    def state(self):
        if self.t0 is None:
            self.t0 = time.time()
        t = self._el()
        if t >= self.end_at:                      # 小游戏结束（让脚本别在结算等待里干耗）
            return {"player": {"minigame": None, "fishing": None, "festivalScore": 100},
                    "location": {"name": "Temp"}, "time": {"season": "fall", "dayOfMonth": 16},
                    "activeMenu": None}
        f = self.profile(t)
        return {"player": {"minigame": "FishingGame", "fishing": f, "festivalScore": 100},
                "location": {"name": "fishingGame"}, "time": {"season": "fall", "dayOfMonth": 16},
                "activeMenu": None}

    def fishbot(self, action):
        self.fb_calls.append(action)
        return {}

    def set_pause(self, out_of_focus):
        return {}


# ⏱️ 时间线要**像真的**：开局 1s 内竿是空的（脚本踢第一竿），1.5s 才咬钩——
#    否则"开局那脚"会被第一口咬钩顶掉，测出来的分支不是我们想测的那条（第一版就踩了这个）。
BITE = (1.5, 2.3)


def _bite(t):
    return BITE[0] <= t <= BITE[1]


def profile_no_selfrecast(t):
    """咬钩后一直空（= fishbot 不自己补，0.3.0 时代的老行为）。"""
    return {"isNibbling": True} if _bite(t) else {}


def profile_selfrecast(t):
    """咬钩后 2.6s 起自己抛下一竿（= 若 0.6.1 已适配假水的新行为）。"""
    if _bite(t):
        return {"isNibbling": True}
    if 2.6 <= t <= 5.8:
        return {"isCasting": True}
    return {}


def profile_open_casting(t):
    """开局就在抛（小游戏注册那段 fishbot 已自己起竿）+ 钓上后也自补 → **应该一脚都不踢**。"""
    if t <= 1.0:
        return {"isCasting": True}
    if _bite(t):
        return {"isNibbling": True}
    return {"isCasting": True} if 2.6 <= t <= 5.8 else {}


def _run_captured(bot):
    """跑一遍 run() 并把日志收起来（判据就看**收工行**——那是 AI/人真看得到的东西）。"""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        bot.run(deadline=5)
    text = buf.getvalue()
    for ln in text.splitlines():
        print(f"     │ {ln}")
    return text


def case_a():
    print("A) 老行为：钓上后它不自己补 → 该踢")
    bot = FakeBot(profile_no_selfrecast, end_at=6.0)
    text = _run_captured(bot)
    _check("钓上 1 条", "钓上第 1 条" in text)
    _check("走了「没自己补」那条分支", "fishbot 没自己补" in text)
    _check("收工行报了 自补0/踢1", "（fishbot 自补 0 次 / 脚本踢 1 次）" in text)
    _check("开局也踢了（= 两脚 on）", bot.fb_calls.count("on") == 2,
           f"on×{bot.fb_calls.count('on')}")


def case_b():
    print("B) 新行为：钓上后它自己补 → 不该踢")
    bot = FakeBot(profile_selfrecast, end_at=6.0)
    text = _run_captured(bot)
    _check("钓上 1 条", "钓上第 1 条" in text)
    _check("走了「自己补上了」那条分支", "fishbot 自己补上了下一竿" in text)
    _check("收工行报了 自补1/踢0", "（fishbot 自补 1 次 / 脚本踢 0 次）" in text)
    _check("钓上后确实没再踢（on 只 1 次 = 开局那脚）",
           bot.fb_calls.count("on") == 1, f"on×{bot.fb_calls.count('on')}")


def case_d():
    print("D) 开局它就已经在抛（小游戏注册那段）→ 开局那脚也不该踢")
    bot = FakeBot(profile_open_casting, end_at=6.0)
    text = _run_captured(bot)
    _check("走了「开局竿已在动」分支", "开局竿已在动" in text)
    _check("钓上后也自补 → 全程一脚都没踢", bot.fb_calls.count("on") == 0,
           f"on×{bot.fb_calls.count('on')} / off×{bot.fb_calls.count('off')}")


class _ReelOnlyBot(ff.FairFish):
    def __init__(self):
        super().__init__(port=0)

    def state(self):
        return {"player": {"fishing": {"isReeling": True}, "minigame": "FishingGame"}}


def case_c():
    print("C) 键集保险：只有 isReeling（上一条的收线尾巴）不算「新一竿」")
    ok = _ReelOnlyBot()._wait_self_recast(0.6)
    _check("_wait_self_recast 返 False", ok is False, "isReeling 已从 _RECAST_KEYS 排除")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    case_a()
    case_b()
    case_c()
    case_d()
    print()
    if FAILED:
        print("❌ 失败：", "、".join(FAILED))
        sys.exit(1)
    print("✅ 全部通过（⚠️ 仅逻辑自测；fishbot 实际行为仍需真机）")
