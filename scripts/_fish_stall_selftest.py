# -*- coding: utf-8 -*-
"""🎣 钓鱼"空等"看门狗 —— 菜单关掉后必须**重新支起自动抛竿**，支不起来就**如实收工**。2026-10-07

恒：「好像背包菜单弹出来，于是脚本关掉了但**没能继续抛竿**。脚本却还一直在空 continue
     （不检查状态）。」

现场账（`sessions/session_log.jsonl`，同一个 job2 201s / 253s / 305s 三次 `script continue`
各 ~45.4s）：`🎮 小游戏结束：钓鱼小游戏` 之后**再没有一竿抛出去**，体力从 35 掉到 21 就停在那儿。

判据：
  ① 真抛竿的是游戏里的 fishbot —— 菜单一弹它自己就暂停 ⇒ **关掉菜单 ≠ 续上抛竿**；
  ② 空等的线要**按拍数**定（一拍 = 循环的 2s），两次重支还不动 ⇒ 收工，**绝不无限空 continue**；
  ③ 收工时**不再 `let_cast_finish` 空等 45s**（压根没竿在水里，等着只是又白烧一个 45s）；
  ④ 正常换竿的 1~3 拍间隙**绝不能**误判成卡住（钉子要能证明这一点）。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FAIL = []
HERE = os.path.dirname(os.path.abspath(__file__))


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


import fish_run as F          # noqa: E402  （import 不跑主流程：入口在 `if __name__` 里）

SRC = open(os.path.join(HERE, "fish_run.py"), encoding="utf-8").read()


def run_watchdog(frames, rearm_at=F.IDLE_REARM_AT, give_up=F.IDLE_GIVE_UP):
    """拿一串"每一拍**是否在钓**"跑**真的** `idle_step`，返回 `(最终计数, 动作流水, 用了几拍)`。

    这就是循环里那段看门狗的等价体：每 2s 一拍，`active` 由 `isFishing|isCasting|isReeling` 并出来。
    """
    n, acts, used = 0, [], 0
    for f in frames:
        used += 1
        n, a = F.idle_step(f, n, rearm_at=rearm_at, give_up=give_up)
        if a:
            acts.append((n, a))
        if a == "give_up":
            break
    return n, acts, used


print("\n① 判据表：空多少拍 → 重支 / 收工（两拍 = 2s，一拍 = 循环一次 sleep(2)）")
n, acts, _ = run_watchdog([True] * 10)
ck("①一直在钓 ⇒ 计数恒 0、**一个动作都不发**", n == 0 and acts == [], f"{n} {acts}")

n, acts, _ = run_watchdog([False] * 4)
ck("①空 4 拍（8s）⇒ 还只是等，不急着重支", n == 4 and acts == [], f"{n} {acts}")

n, acts, _ = run_watchdog([False] * 5)
ck("①空到第 5 拍（10s）⇒ 重支一次", acts == [(5, "rearm")], f"{acts}")

n, acts, _ = run_watchdog([False] * 10)
ck("①空到第 10 拍（20s）⇒ 第二次重支（不是「每拍都 on」）", acts == [(5, "rearm"), (10, "rearm")], f"{acts}")

n, acts, _ = run_watchdog([False] * 19)
ck("①空 19 拍 ⇒ 只有两次重支、还没收工", n == 19 and acts == [(5, "rearm"), (10, "rearm")], f"{n} {acts}")

n, acts, used = run_watchdog([False] * 999)
ck("①空到第 20 拍（40s）⇒ **收工**（不再无限空 continue）", acts[-1] == (20, "give_up"), f"{acts}")
ck("①收工用的是 40s 那条线（≈2~3 个完整竿周期）", used == 20 and F.IDLE_GIVE_UP * 2 == 40,
   f"used={used} give_up={F.IDLE_GIVE_UP}")

print("\n② 重支**成功**就立刻复位（别把「又抛起来了」记成卡住）")
n, acts, _ = run_watchdog([False] * 7 + [True] * 3)
ck("②空 7 拍后钓起来 ⇒ 计数归 0 并喊一句 reset", n == 0 and acts == [(5, "rearm"), (0, "reset")], f"{n} {acts}")
n, acts, _ = run_watchdog([False] * 6 + [True] * 1 + [False] * 6)
ck("②中途钓起来过 ⇒ 后面重新从 0 数（不会攒满 20 拍冤枉收工）",
   acts == [(5, "rearm"), (0, "reset"), (5, "rearm")], f"{acts}")

print("\n③ **不许误伤**：正常换竿的间隙（1~2 拍没在钓）连日志都不该打、更不该重支")
for gap in (1, 2):
    n, acts, _ = run_watchdog(([False] * gap + [True]) * 12)
    ck(f"③每竿之间空 {gap} 拍 × 12 轮 ⇒ 完全静默（不重支、不喊话）", acts == [], f"{acts}")
n, acts, _ = run_watchdog(([False] * 3 + [True]) * 8)
ck("③空 3 拍（6s）才值得喊一句 reset，而且**不重支**",
   set(a for _n, a in acts) == {"reset"} and len(acts) == 8, f"{acts}")

print("\n④ 判据必须是「三个信号的并集」（小游戏开着也算在钓）")
ck("④`idle_step` 只看传进来的 active 一个口", "def idle_step(active, idle_rounds" in SRC)
ck("④循环里传的是 `is_fishing or is_reeling`（is_fishing 已含 isCasting）",
   re.search(r"idle_step\(bool\(is_fishing or is_reeling\)", SRC) is not None,
   "循环里没有按并集判")
ck("④`is_fishing` 确实并了 isCasting",
   "isCasting" in SRC.split("is_fishing = bool(")[1].split("\n")[0])

print("\n⑤ 源码接线：看门狗真的挂在主循环里，而且**收工走统一出口**")
ck("⑤循环里有 `_idle_rounds = 0` 初始化",
   re.search(r"^\s*_idle_rounds = 0\b", SRC, re.M) is not None)
ck("⑤循环里真的调了 `idle_step`",
   re.search(r"^\s*_idle_rounds, _idle_act = idle_step\(", SRC, re.M) is not None)
_loop = SRC.split("while True:")[1]
# 只截「give_up 那一支」到它自己的 `break` 为止（再往后就是 max_casts 那一支，别串进来看）
_gu_raw = _loop.split('elif _idle_act == "give_up":')[1][:400] if '"give_up"' in _loop else ""
_gu = _gu_raw.split("break")[0]
ck("⑤收工走的还是 `stop_after_cast`（统一出口：会先看菜单，不当着待领槽乱按）",
   "stop_after_cast(bot" in _gu, _gu[:120])
ck("⑤收工**不许** `let_cast_finish` 空等 45s（没竿在水里）",
   "let_cast_finish(" not in _gu, _gu[:160])
ck("⑤收工确实 `break` 出循环", "break" in _gu_raw, _gu_raw[:160])
ck("⑤重支调的是 `bot.fishbot(\"on\")`",
   'bot.fishbot("on")' in _loop.split('elif _idle_act == "rearm":')[1][:300])
ck("⑤重支失败要记日志、不许静默吞掉（`except` 里有 log）",
   "重支自动抛竿失败" in SRC)

print("\n⑥ 根因那一半：**关掉菜单之后必须重新支起来**（关菜单 ≠ 续抛竿）")
# 只截「关菜单成功那一支」（到"菜单关不掉"的 else 前为止），别把失败分支和后面的体力段串进来
_close = SRC.split("if bot.close_menu():")[1].split("菜单关不掉")[0] if "if bot.close_menu():" in SRC else ""
ck("⑥`close_menu()` 成功之后调了 `fishbot(\"on\")`",
   'bot.fishbot("on")' in _close, _close[:120] or "找不到 close_menu 分支")
ck("⑥**只在成功那一支**重支（关不掉就别傻抛）",
   'bot.fishbot("on")' in _close and "menu_close" not in _close.split('bot.fishbot("on")')[1][:80],
   _close[:200])
ck("⑥关菜单那句日志说了「重新支起」（别只报「继续钓」、实际没续上）",
   "重新支起" in _close, _close[:200])
ck("⑥`ItemGrabMenu` 那条路**仍然留给 AI**（不许被看门狗偷偷关了/重支掉）",
   'if mtype == "ItemGrabMenu":' in SRC and
   SRC.split('if mtype == "ItemGrabMenu":')[1].split("break")[0].count('fishbot("on")') == 0,
   "满包待领那条路被动了")

print("\n⑦ 常量口径：一拍 = 2s，检查间隔仍是 10s（文案里的秒数是这么算出来的）")
ck("⑦`CHECK_INTERVAL == 5`（5 拍 × 2s = 10s）", F.CHECK_INTERVAL == 5, str(F.CHECK_INTERVAL))
ck("⑦`IDLE_REARM_AT == (5, 10)`", tuple(F.IDLE_REARM_AT) == (5, 10), str(F.IDLE_REARM_AT))
ck("⑦`IDLE_GIVE_UP == 20`（40s，> 重支的第 10 拍）", F.IDLE_GIVE_UP == 20, str(F.IDLE_GIVE_UP))

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 空等看门狗：菜单关掉即重支、两次不动就收工（不再空 continue、也不再白等 45s）；"
      "正常换竿间隙不误伤。")
