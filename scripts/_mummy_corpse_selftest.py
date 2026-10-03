# -*- coding: utf-8 -*-
"""🧟 木乃伊**尸体形态**：不许当目标、不许追着砍（恒 2026-10-03 深夜）。

恒的原话：「有一个问题——**对木乃伊的尸体形态发生了贴脸的无限追击**。这玩意儿的正确处理方法应该是
**打死了用炸弹炸掉尸体，或者武器十字军附魔**。**能跳过对于这个形态的攻击吗？**」

反编译事实（`_live/Mummy.decompiled.cs`，`ilspycmd -t StardewValley.Monsters.Mummy`）：
  · `takeDamage`（:50）第一句：`if (reviveTimer.Value > 0) { if (isBomb) { Health = 0; …; return 999; }
    return -1; }` ⇒ **尸体形态下普通武器恒 -1（一点伤害都没有）**，**只有炸弹能清**。
  · 打倒那一下（:81-95）：武器带 `CrusaderEnchantment` ⇒ **直接消灭**；否则
    `reviveTimer.Value = 10000; base.Health = base.MaxHealth;` ⇒ 变尸体、**10 秒**后复活。
  · ⚠️ 关键陷阱：进尸体形态时 `Health` 被**重设成 MaxHealth** ⇒ **光看 health 认不出来**，
    在 AI 眼里它跟满血木乃伊一模一样 —— 这就是"贴脸无限追击"的病根。
  · 权威信号 = **`Mummy.reviveTimer.Value > 0`**（`NetInt`）。

这条钉子钉的是"**两处都按权威信号跳过**"：C# 的 guard + C# 的 /surroundings 清单 + 两个 Python 战斗循环。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


def src(fn):
    return io.open(os.path.normpath(os.path.join(HERE, fn)), encoding="utf-8").read()


def block(text, start_pat, span=70):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.search(start_pat, ln):
            return "\n".join(lines[i:i + span])
    return ""


CS = src(os.path.join("..", "ModEntry.cs"))
BC = src("bomb_common.py")
MR = src("mine_run.py")

print("① C# `/surroundings` 的怪物清单**必须报这个形态**（不然 Python 侧永远看不出来）")
_payload = block(CS, r"var nearbyMonsters = loc\.characters", span=22)
ck("…清单里有 `reviving` 字段", "reviving =" in _payload)
ck("…判据是 **`Mummy.reviveTimer.Value > 0`**（权威信号，不是猜血量）",
   "reviveTimer.Value > 0" in _payload and "m is Mummy" in _payload)
ck("…注释里写明「`Health` 会被重设成 MaxHealth ⇒ 光看 health 认不出来」这个陷阱",
   "MaxHealth" in _payload and "认不出来" in _payload)

print("② C# guard：**打得动才当目标**那一族里加上尸体形态（恒问的就是这条）")
_gt = block(CS, r"private static bool GuardTargetable\(", span=45)
ck("…`GuardTargetable` 里有木乃伊尸体那条", "m is Mummy" in _gt and "reviveTimer.Value > 0" in _gt)
ck("…和「装壳岩蟹/沙漠甲虫」同一族（打在 `return true` **之前**）",
   _gt.index("m is Mummy") < _gt.rindex("return true"))
ck("…注释点明「只有炸弹能清 / 十字军附魔那一刀才真死」",
   "炸弹" in _gt and "CrusaderEnchantment" in _gt)

print("③ Python 两个战斗循环都跳过（不然脚本照样追着绷带堆跑）")
_bc = block(BC, r"    def nearby_monsters\(", span=30)
ck("…`bomb_common.nearby_monsters` 跳过 `reviving`",
   'm.get("reviving")' in _bc and "continue" in _bc)
ck("…至少**如实打一行日志**（别静默跳过）",
   "_logged_reviving" in _bc and "尸体形态" in _bc)
_mr = block(MR, r"    def nearby_monsters\(", span=26)
ck("…`mine_run.nearby_monsters` 同样跳过", 'm.get("reviving")' in _mr)
ck("…同样有一次性日志", "_logged_reviving" in _mr and "尸体形态" in _mr)
ck("…两处都写了「炸弹/十字军附魔」这条出路（免得下一个人以为「就是不打」）",
   "炸弹" in _bc and "炸弹" in _mr)

print("④ 顺手钉一条**反回潮**：不许再用 `health` 判尸体（那是被重置过的假象）")
ck("…C# 里没有把 `Health <= 0` 当成「尸体」的写法（旧写法会把真怪也滤掉）",
   "reviveTimer" in CS and "Health <= 0 && " not in _payload)

print()
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("🎉 全过：木乃伊尸体形态（C# guard 不选它 + 清单报 reviving + 两个 Python 循环跳过）")
