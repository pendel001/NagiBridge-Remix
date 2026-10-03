# -*- coding: utf-8 -*-
"""🛡️🍽️🧪 2026-10-03 真机逮到的三个 C# 洞：用**读源码原文**的方式钉住，防回潮。

三个洞的现场（恒真机那两句「**这次怎么好像甚至连怪都不打了**」「**因为边走边吃吃不上，于是好像撤了**」）：

① `GuardTick` 的门⑩写的是 `farmer.isEating || farmer.itemToEat != null` ——
   而 `itemToEat` **是个永不清零的字段**（反编译 `Farmer.cs`：只在 3 处被赋值，没有一处置 null）
   ⇒ **吃过一次东西之后 guard 恒不挥**（真机 `/guard` 的 `block` 一直=10、两趟下矿「共挥 0 刀」）。
② `/eat` 老版是"`eatObject` → 立刻 `Stack--` → 立刻回 `ok:true`" —— 而真正结算在动画收尾的
   `doneEating()`；动画被下一步动作打断 ⇒ **东西白扣、一点没补、回包还骗人**。
   （连带：`eat_recovery` 的 `_recover_streak` 因此连中 3 ⇒ 判"回不上来" ⇒ **假撤退**。）
③ `/buffs` 与 `/state.player.buffs` 两处都靠**反射摸黑**（摸 `buffsDisplay` 的图标表 / 猜
   `activeBuffs`、`GetAppliedBuffs` 这些**1.6 不存在**的名字）⇒ 后者**恒空**
   （2026-08-16 那条 buff 提醒从上线起没响过）。权威表是 `farmer.buffs.AppliedBuffs`。

⚠️ 这组钉子读的是 `ModEntry.cs` **原文**（本仓有先例：`_intent_wiring_selftest` 就是拿 C# 报错原话当权威清单）。
   它防的是"下次有人照着旧注释把洞改回来"——源码一改回旧形状，这里立刻红。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CS = os.path.join(HERE, "..", "ModEntry.cs")

fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


src = io.open(CS, encoding="utf-8").read()
lines = src.splitlines()


def _block(start_pat, span=90):
    """取从匹配行开始的 span 行（用来把"某个函数体内"当范围断言）。"""
    for i, ln in enumerate(lines):
        if re.search(start_pat, ln):
            return "\n".join(lines[i:i + span])
    return ""


def _code_only(text):
    """去掉 `//` 注释行 —— 注释里**必须**能写旧代码长什么样（不然下一个读的人又不知道坑在哪）。"""
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//"))


print("① guard 门⑩：**不许**拿 `itemToEat` 当门（它永不清零 ⇒ 吃过一次 guard 就死了）")
guard = _block(r"private void GuardTick\(\)", span=260)
ck("…门⑩判据只剩 `isEating`", "if (farmer.isEating) { _guardBlock = 10; return; }" in guard)
ck("…**代码里**不许再出现 `itemToEat`（注释里写旧形状是允许的、还鼓励）",
   "itemToEat" not in _code_only(guard))
ck("…`GuardBlockName(10)` 文案同步成「正在吃」", '10 => "正在吃（isEating）"' in src)
ck("…顺手把「字段什么时候被清」这条教训写进注释（别只改代码）", "永不清零" in guard)

print("② `/eat`：**等结算**才扣、失败如实报 + 不清走位队列不吃")
eat = _block(r"private object HandleEat\(\)", span=130)
ck("…吃东西前 `ClearMovementState()`（清掉异步走位队列，否则动画被覆盖）",
   "ClearMovementState();" in eat)
ck("…`eatObject` 出现在 `Stack--` **之前**（先吃、确认结算了才扣）",
   eat.index("eatObject") < eat.index("Stack--"), "顺序反了")
ck("…有诚实的失败码 `eat_not_settled`（没结算就说没结算）", '"eat_not_settled"' in eat)
ck("…轮询读的是**权威表** `AppliedBuffs` 数 buff", "AppliedBuffs" in eat)
ck("…吃完不再盲等 2 秒就回 ok（老形状 no more：`Stack--` 紧跟 `eatObject`）",
   not re.search(r"eatObject\([^;]*;\s*\n\s*obj\.Stack--", eat))

print("③ buffs 两处都读**权威表** `AppliedBuffs`（不再反射摸黑）")
eb = _block(r"private List<object>\? EnumerateBuffs", span=60)
hb = _block(r"private object HandleBuffs\(\)", span=60)
ck("…`EnumerateBuffs` 读 `buffs.AppliedBuffs`", "buffs?.AppliedBuffs" in eb)
ck("…`EnumerateBuffs` **不许**再找 `activeBuffs`（1.6 没这个字段 ⇒ 恒空）",
   "activeBuffs" not in eb)
ck("…`HandleBuffs` 读 `buffs?.AppliedBuffs`", "buffs?.AppliedBuffs" in hb)
ck("…`HandleBuffs` **不许**再扒显示层的图标表（会重复）",
   "buffsDisplay" not in hb)
ck("…`/buffs` 回包**不再带** `diagnostic`（没消费方；要查的事已经查清）",
   "diagnostic" not in hb)
ck("…名字取「最像人话的」（食物 buff 的 `displayName` 恒 null ⇒ 要退到 `displaySource`/`source`）",
   "displaySource" in eb and "displaySource" in hb)
ck("…`/buffs` 带上 `visible`（权威表含隐藏 buff，消费侧要能过滤）", "visible = b.visible" in hb)

print()
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("🎉 全部通过（guard 门⑩ / `/eat` 等结算 / buffs 读权威表 —— 三个洞都钉住了）")
