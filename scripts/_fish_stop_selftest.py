"""🎣 收手语义：**"抛够 N 竿"必须把第 N 竿钓完**，不许半路拽线。纯 Python 自验（不碰游戏）。2026-09-24

恒真机原话：「原来你的抛竿真的只是抛竿，**抛了却不钓完鱼，就跟异常停止一样**」。

现场：`fish go location=Beach max_casts=2` → 钓上一条沙丁鱼，第二条**刚甩出去就被拽回来**
（`cast_count` 是"进入钓鱼态"的**边沿**，一数到 2 就 `finish_cast` = `fishbot off`+2s+cancel×3）。

测三件：
  ① `let_cast_finish`：等这一竿收束的**全过程**里，**不许** fishbot off、**不许** cancel
     （一按 cancel/一关鱼机，正在小游戏里的鱼就废了）
  ② **顺序**：`fishbot off` 必须发生在"这一竿收束"**之后**（顺序反了等于白改）
  ③ `finish_cast`：线不在水里时**不许空按 cancel**（cancel=使用工具键，空按=又甩一竿）
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fish_run as F

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


ON = {"isFishing": True, "isCasting": True, "isReeling": False}
REEL = {"isFishing": True, "isCasting": False, "isReeling": True}
OFF = {"isFishing": False, "isCasting": False, "isReeling": False}


class FakeBot:
    """按剧本吐钓鱼状态；把**所有**动作按时间顺序记进 calls（顺序本身就是要验的东西）。"""

    def __init__(self, seq, sticky_last=True, menu=None):
        self.seq = list(seq)
        self.sticky_last = sticky_last
        self.menu = menu
        self.calls = []

    def state(self):
        f = self.seq.pop(0) if self.seq else OFF
        if not self.seq and self.sticky_last:
            self.seq = [f]                      # 剧本用完就停在最后一格
        self.calls.append(("state", "in" if (f.get("isFishing") or f.get("isCasting")
                                             or f.get("isReeling")) else "out"))
        return {"player": {"fishing": f},
                "activeMenu": ({"type": self.menu} if self.menu else None)}

    def key(self, k):
        self.calls.append(("key", k))

    def fishbot(self, a):
        self.calls.append(("fishbot", a))

    def select(self, n):
        self.calls.append(("select", n))
        return {"ok": False}                    # 一个都换不了 → stow_rod 直接罢手（不阻断收工）


print("\n① let_cast_finish —— 等这一竿收束，期间绝不动手")
bot = FakeBot([ON, ON, REEL, REEL, OFF])
got = F.let_cast_finish(bot, timeout_s=5)
ck("这一竿钓完才返回（鱼没上来就一直等）", got is True, str(got))
ck("等待期间**没有**关鱼机（关了小游戏就没人玩了）",
   ("fishbot", "off") not in bot.calls, str(bot.calls))
ck("等待期间**没有**按 cancel（按了就是把线拽回来）",
   ("key", "cancel") not in bot.calls, str(bot.calls))
ck("确实是一路盯着状态（不是瞎等一会儿就收）",
   sum(1 for c in bot.calls if c[0] == "state") >= 4, str(bot.calls))

bot2 = FakeBot([ON, ON, ON])
t0 = time.time()
got2 = F.let_cast_finish(bot2, timeout_s=1.0)
ck("死水/卡住 → 超时返回 False（不许把工具挂死）",
   got2 is False and 0.8 <= time.time() - t0 < 3.0, f"{got2} / {time.time()-t0:.1f}s")

print("\n② 顺序 —— fishbot off 必须在『这一竿收束』之后")
bot3 = FakeBot([ON, REEL, OFF])
F.let_cast_finish(bot3, timeout_s=5)
F.finish_cast(bot3, "数到第 2 竿，这一竿也钓完了")
_names = [c[0] + (":" + c[1] if len(c) > 1 else "") for c in bot3.calls]
_i_out = _names.index("state:out") if "state:out" in _names else -1
_i_off = next((i for i, n in enumerate(_names) if n == "fishbot:off"), -1)
ck("state:out 先出现、fishbot off 在后", _i_out != -1 and _i_out < _i_off,
   " → ".join(_names))

print("\n③ finish_cast —— 线不在水里时不许空按 cancel")
bot4 = FakeBot([OFF])
F.finish_cast(bot4, "数到第 2 竿，这一竿也钓完了")
ck("线已收（isFishing 全 False）→ **一下都不按**（空按=又甩一竿）",
   ("key", "cancel") not in bot4.calls, str(bot4.calls))
ck("但仍然关了鱼机（自动抛竿必须停）", ("fishbot", "off") in bot4.calls, str(bot4.calls))

bot5 = FakeBot([ON, ON, ON])                # 超时硬收：线还甩着
F.finish_cast(bot5, "等这一竿收束超时（硬收）")
_presses = sum(1 for c in bot5.calls if c == ("key", "cancel"))
ck("线还在水里 → 照旧按 cancel 收线（兜底不能丢）", _presses >= 1, str(bot5.calls))

print("\n④ stop_after_cast —— 满包待领的鱼**不能**被我们的收手动作碰掉")
bot6 = FakeBot([OFF], menu="ItemGrabMenu")
F.stop_after_cast(bot6, "数到第 1 竿，这一竿也钓完了")
ck("弹着满包待领 → 鱼机照关（别让它再甩）", ("fishbot", "off") in bot6.calls, str(bot6.calls))
ck("弹着满包待领 → **一个键都不按**（cancel 会把这个菜单顶掉=鱼没了）",
   not any(c[0] == "key" for c in bot6.calls), str(bot6.calls))
ck("弹着满包待领 → **不换手持工具**（stow_rod 那下也是动菜单态）",
   not any(c[0] == "select" for c in bot6.calls), str(bot6.calls))

bot7 = FakeBot([OFF], menu="BobberBar")     # 小游戏自己弹的，不算"待领"
F.stop_after_cast(bot7, "数到第 1 竿")
ck("小游戏菜单(BobberBar) → 照常走正常收手（别把它当待领）",
   any(c[0] == "select" for c in bot7.calls), str(bot7.calls))

bot8 = FakeBot([OFF])
F.stop_after_cast(bot8, "数到第 1 竿")
ck("没菜单 → 照常收手（换手持工具）", any(c[0] == "select" for c in bot8.calls), str(bot8.calls))

print("\n" + ("=" * 46))
print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
