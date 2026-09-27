"""🍽️ 吃东西要**等效果落地**再返回 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-27

恒真机：「刚才 eat 吃了之后还是报低体力，是**那次调用有延迟**还是**没阻塞到吃完**导致的？
但 ai 决定不管所以也成功了。」

**是没阻塞。** 反编译实锤（`decomp/full/StardewValley/Farmer.cs`）：
  · `eatObject()`（:9111）**只开始进食状态** —— `itemToEat = o; isEating = true; freezePause = 20000;`
    **一点都不加**体力/血；
  · 真正结算是**动画结束**的 `doneEating()`（:8941）：
    `Stamina = Math.Min(MaxStamina, Stamina + num3)` / `health = Math.Min(maxHealth, health + num4)`。
⇒ C# `/eat` 回包里那两个 `stamina`/`health` **必然是吃之前的旧值**，紧跟其后的状态条也是旧的
  ⇒ AI 明明吃过了，状态条还顶着「🚨 体力危险」（实录 `session_log 1790488410`：
  「🍽️ 吃了 Salad … 🚨 体6%」）。

测四件：
  ① 体力真变了 → `_wait_eat_effect` 等到 True；
  ② 一直不变 → 超时返回 False（**别挂死**）；
  ③ **读不到状态** → 立刻返回 False，且**不拿"我瞎了"当"没生效"**；
  ④ `eat_item` 端到端：**返回时体力已经变了**（证明确实等了，而不是回了个旧值）。
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class EatApi:
    """吃下去**不当场涨**，要再过 `mutate_after` 次 state 读才涨（模拟"动画结束才结算"）。"""

    def __init__(self, mutate_after=2, boom=False):
        self.st, self.hp = 20.0, 100
        self.reads, self.ate_at = 0, None
        self.mutate_after, self.boom = mutate_after, boom

    def state(self, **kw):
        self.reads += 1
        if self.boom:
            raise RuntimeError("state 读不到")
        if self.ate_at is not None and self.reads >= self.ate_at + self.mutate_after:
            self.st = 133.0                                   # ← 结算了
        return {"player": {"stamina": self.st, "health": self.hp, "currentItem": "Salad"}}

    def _post(self, ep, data=None, **kw):
        assert ep == "/eat", ep
        self.ate_at = self.reads
        # ⚠️ C# 回包里的 stamina **是吃之前的旧值** —— 这正是要防的那个坑（别拿它当判据）
        return {"ok": True, "ate": "Salad", "stamina": int(self.st), "health": self.hp}

    def select(self, *a, **k):
        return {"ok": True}


_real = {n: getattr(M, n) for n in ("api", "_with_state", "_ensure_background", "_best_quality_for")}
try:
    M._with_state = lambda s: s
    M._ensure_background = lambda: None
    M._best_quality_for = lambda *a, **k: -1

    print("\n① 体力真变了 → 等到 True")
    _a = EatApi(mutate_after=2)
    _a.ate_at = 0          # ⚠️ 这个假 api 是"调过 /eat 才开始煮"，直接调 waiter 得先把它置上
    M.api = _a
    t0 = time.time()
    got = M._wait_eat_effect(20.0, 100)
    ck("等到了", got is True, str(got))
    ck("…确实等了几拍（不是立刻返回）", (time.time() - t0) >= M._EAT_POLL, f"{time.time()-t0:.2f}s")

    print("\n② 一直不变 → 超时返回 False（别挂死）")
    M.api = EatApi(mutate_after=9999)
    t0 = time.time()
    got = M._wait_eat_effect(20.0, 100, timeout=0.8)
    ck("超时返回 False", got is False, str(got))
    ck("…没有超出上限太多", (time.time() - t0) < 2.5, f"{time.time()-t0:.2f}s")

    print("\n③ 读不到状态 → 立刻 False（「我瞎了」≠「没生效」）")
    M.api = EatApi(boom=True)
    t0 = time.time()
    got = M._wait_eat_effect(20.0, 100)
    ck("返回 False", got is False, str(got))
    ck("…立刻返回、不空转满超时", (time.time() - t0) < 1.0, f"{time.time()-t0:.2f}s")
    ck("…两边都读不到（st0/hp0 都是 None）也立刻返回",
       M._wait_eat_effect(None, None) is False)

    print("\n④ eat_item 端到端：**返回时体力已经变了**（这才叫「阻塞到有结果」）")
    api = EatApi(mutate_after=2)
    M.api = api
    out = M.eat_item.__wrapped__(name="Salad")
    ck("回包说吃了", "吃了" in out, out)
    ck("**返回时体力已涨到 133**（而不是还停在 20）", api.st == 133.0, f"st={api.st}")
    ck("…确实多轮询了几次 state", api.reads >= 3, f"reads={api.reads}")

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
