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
eat = _block(r"private object HandleEat\(\)", span=210)
# ⚠️ span 170 → 210：2026-10-03 晚在轮询里加了"动画没了先给 400ms 宽限"那段（+13 行），
#    170 行的窗口够不到尾部那句"HTTP 兜底超时" ⇒ 假红。
ck("…吃东西前 `ClearMovementState()`（清掉异步走位队列，否则动画被覆盖）",
   "ClearMovementState();" in eat)
ck("…`eatObject` 出现在 `Stack--` **之前**（先吃、确认结算了才扣）",
   eat.index("eatObject") < eat.index("Stack--"), "顺序反了")
ck("…有诚实的失败码 `eat_not_settled`（没结算就说没结算）", '"eat_not_settled"' in eat)
ck("…轮询读的是**权威表** `AppliedBuffs` 数 buff", "AppliedBuffs" in eat)
ck("…吃完不再盲等 2 秒就回 ok（老形状 no more：`Stack--` 紧跟 `eatObject`）",
   not re.search(r"eatObject\([^;]*;\s*\n\s*obj\.Stack--", eat))
# 🔴 2026-10-03 我自己踩的第二个坑（真机 25s 超时 + 请求永久挂住）：`EnqueueMainThread` 是**异步**的，
#    却在 enqueue 之后立刻判 `eaten == null` ⇒ 那一刻主线程还没跑、条件恒真 ⇒ 提前 return，
#    而 tcs 只有后面被跳过的块才置 ⇒ 请求永不返回。⇒ 必须有「吃这步跑完了」的信号量，且请求有兜底超时。
ck("…有「吃这步跑过了」的信号量（不许 enqueue 后立刻读它写的变量）",
   "issued.TrySetResult(true)" in eat and "issued.Task.GetAwaiter().GetResult()" in eat)
ck("…**不许**再有 `if (eaten == null) return tcs.Task...` 那种形状（异步排队后立刻读=恒读旧值）",
   not re.search(r"if \(eaten == null\)\s*\n\s*return tcs\.Task", eat))
ck("…HTTP 请求有兜底超时（主线程卡住也不让请求挂死）",
   "eat_verify_timeout" in eat and "tcs.Task.Wait(" in eat)

print("②之二 🔴 2026-10-03 第二趟真机：结算判据**不能**看「血/体力/buff 数变了没」")
# 两个反例（都在这一趟真机上撞到）：
#   · 满血满体力吃不带 buff 的（芝士）⇒ `Math.Min` 夹住，数值一个不动；
#   · 续**同一个槽**的 buff（咖啡续咖啡）⇒ `BuffManager.Apply` = Remove 同 id + 放进去 ⇒ 条数不变。
# 两种都**结算跑了**却被报成没结算（假失败）。⇒ 权威信号 = postfix `Farmer.doneEating` 记 tick。
ck("…`/eat` 拿 `DoneEatingPatch.LastTick` 当结算判据（不是拿数值倒推）",
   "DoneEatingPatch.LastTick" in eat and "settled = true" in eat)
ck("…吃之前先记下 tick（同一块主线程里读，避开竞态）", "eatTick0 = DoneEatingPatch.LastTick" in eat)
ck("…数值变化降级成附带情报（`statsChanged`），不再当门",
   "statsChanged" in eat and "if (!settled)" in eat)
ck("…**不许**再拿 `s.hp != hp0 || s.sta != sta0 || s.nb != nb0` 当结算判据",
   not re.search(r"landed\s*=\s*s\.hp", eat))
dep = _block(r"internal static class DoneEatingPatch", span=30)
# ⚠️ 特性和类是**上下两行**（特性在类上面）⇒ 断言要跨行找，不能用从类名往下的窗口
ck("…`DoneEatingPatch` 的特性挂的确实是 `Farmer.doneEating`",
   re.search(r"\[HarmonyPatch\(typeof\(Farmer\),\s*nameof\(Farmer\.doneEating\)\)\]\s*\n\s*"
             r"internal static class DoneEatingPatch", src) is not None)
ck("…Postfix 记的是 tick + 吃了什么（诊断要看得出『结算是哪一口』）",
   "LastTick = Game1.ticks" in dep and "LastItem" in dep)
ck("…补丁只在**本进程自己那个人**身上记（联机时别的 farmer 不算）",
   "__instance != Game1.player" in dep)
ck("…⚠️ 手工 `harmony.Patch` 也挂了（本模组**没有 PatchAll()**，光写特性不生效）",
   "doneEatingMethod" in src and "donePostfix" in src)

print("②之三 🔴 失败路径**必须复位**：`isEating` 卡住 ⇒ CanMove=false + guard 门⑩ 恒 10")
# 真机实测（2026-10-03）：`/warp` 在吃东西动画中途插进来 ⇒ 动画冻住、`isEating` **10 秒后还是 true**、
# `doneEating` 再没跑过；下一个成功的吃才把它清掉。⇒ 失败分支自己复位，别把毒留在场上。
ck("…没结算时调 `completelyStopAnimatingOrDoingAction()`（游戏自己的『停止一切动作』）",
   "completelyStopAnimatingOrDoingAction()" in eat)
ck("…复位结果如实回包（`reset`）", "reset," in eat or "reset = true" in eat)
ck("…回包文案点明『否则 guard 会卡门⑩』", "门⑩" in eat)

print("②之四 `isEating`/`canMove` 摆到 `/state` 上（看不见的状态=只能猜的状态）")
st = _block(r"buffs = EnumerateBuffs\(farmer\),", span=14)
ck("…`/state.player.isEating` 有暴露", "isEating = farmer.isEating" in st)
ck("…`/state.player.canMove` 有暴露", "canMove = farmer.CanMove" in st)

print("②之五 🔴 同日真机第三趟：『动画没了』**不许当场判失败**（边界竞态）")
# 现场：同一条（满值吃芝士）第一次回 `eat_not_settled`、物品没扣；紧接着同一格连吃 3 次**全过**
# （`settledMs` 2400 / 2600 / 2400）⇒ 不是信号坏，是**边界**：动画收尾与 `doneEating` 是
# `FarmerSprite` 里**同一帧的两句话**，200ms 的采样点会正压在那条边界上。
# ⇒ ① 判失败前给一段宽限 ② 跨线程读的 tick 要 `volatile`（否则读侧可能吃旧值）。
ck("…`LastTick` 声明成 `volatile`（主线程写 / HTTP 线程读）", "volatile int LastTick" in dep)
ck("…记下『头一次看到动画没了』的时刻（`animGoneAt`）", "animGoneAt" in eat)
ck("…宽限内不算失败（有 `waited - animGoneAt >=` 这条判据）",
   re.search(r"waited - animGoneAt >=", eat) is not None)
ck("…**不许**再有『一看到 `!s.eating` 就 `animationOver = true`』的当场判死形状",
   not re.search(r"if \(!s\.eating && waited >= 600\)\s*\{\s*\n\s*animationOver = true;", eat))

print("④ 🔴 `/give` 传**名字**会静默造 Error Item（真机：9 发 `Wood` 占满 9 格、回包还说 ok:true）")
give = _block(r"private object HandleGive\(HttpListenerContext ctx\)", span=90)
_gv_code = _code_only(give)   # ⚠️ 必须去注释再比顺序：我给这段写的**说明注释**里先出现 Create、后出现 Exists
ck("…先 `ItemRegistry.Exists` 验 ID（假的直接 ok:false）", "ItemRegistry.Exists(" in _gv_code)
ck("…再兜一层 Error Item 名字判定", '"Error Item"' in _gv_code)
ck("…验 ID 在 `ItemRegistry.Create` **之前**（顺序反了就等于没验）",
   ("ItemRegistry.Exists" in _gv_code and "ItemRegistry.Create" in _gv_code
    and _gv_code.index("ItemRegistry.Exists") < _gv_code.index("ItemRegistry.Create")))
ck("…错误文案点明『要用限定 ID』+ 给出现场核过的例子",
   "限定 ID" in _gv_code and "(O)388" in _gv_code and "(O)395" in _gv_code)

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
