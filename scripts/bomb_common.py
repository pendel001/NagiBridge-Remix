"""
💣 bomb_common.py — 炸矿三模式（协同/自动/手动）共享核心

纯 Python，复用现有端点，不依赖 ModEntry 新端点：
  select("Bomb") + /use  → placementAction 放炸弹（自动引爆）
  /surroundings          → 扫矿物/怪物/可走格
  /debris                → 扫地上掉落
  /ladder + /key confirm → 下楼
  /state.otherPlayers    → 跟踪房主(user)位置

默认打 AI 角色进程 7843（炸矿是 AI 自己角色的自主活动），
user的位置从 host 进程 7842 读——角色名不写死，端口决定角色。

用法（供脚本 import）:
  from bomb_common import BombMiner
  bot = BombMiner(port=7843, host_port=7842, bomb_type="Bomb")
"""

import os
import re
import sys
import time
import requests

# ── 端口 ──
# 炸矿=AI 自主活动 → 默认 7843（AI 角色）；host useruser在 7842
NAGI_URL = os.environ.get("NAGI_URL", "http://localhost:7843")
HOST_URL = os.environ.get("NAGI_HOST_URL", "http://localhost:7842")


class ManualChestFull(Exception):
    """开箱弹出满包领取菜单（战利品卡领取侧）→ 交 AI 手动处理（claim_swap/ok），脚本停下不撤退。
    恒 2026-08-23：满包领不走就停，不自动丢物。str(e)=战利品名列表。"""
    pass

# ── 炸弹爆炸形状（⚡ 2026-09-06 恒 游戏 tooltip 实测，非 wiki 模糊数） ──
#   樱桃 Bomb：边长7 的十字(缺4角，共~12格) — "边长为7的正方形缺少4个角"  bombRadius=3
#   黑炸弹    ：边长11 方块 — "向左6 向上6 向右4 向下4（即边长为11格的正方形）"  bombRadius=5
#   超级 Mega ：15×15 方块 — "向上8 向左8 向右6 向下6（即边长15的正方形）"  bombRadius=7
#   🔥 反编译实锤（TemporaryAnimatedSprite 构造函数 804-815）：樱桃3 / 黑5 / 超级7，
#   爆炸盒=2r+1=7/11/15，与 tooltip 边长完全对上。见 blast_tiles()/blast_reach()。
#   BOMB_RADIUS 仅剩 _selftest 钻石近似兜底用，已按游戏真值改过（樱桃3/黑5/超级7）。
BOMB_RADIUS = {"Cherry Bomb": 3, "Bomb": 5, "Mega Bomb": 7}
BOMB_NAMES = set(BOMB_RADIUS)
# 最大单向偏移（躲远/防重叠/安全距离用）：樱桃=3(十字臂长)、黑=6(方块向左6)、超级=8(方块单向)
BOMB_REACH = {"Cherry Bomb": 3, "Bomb": 6, "Mega Bomb": 8}
# 🥇 炸矿炸弹优先级（恒 2026-09-06：黑 > 超级 > 樱桃）——背包里挑实际有的、按此序选
BOMB_PRIORITY = ["Bomb", "Mega Bomb", "Cherry Bomb"]

# 锤子右键重砸（Super Slam）冷却（秒）：直接砸不蓄力、有冷却。
# 反编译确认 clubCooldown=6000ms（6秒）；Artful 附魔/职业28 减半→3秒。取 6 保守（冷却中调用会被游戏静默跳过）
HAMMER_SPECIAL_COOLDOWN = 6.0

# 🍽️ **吃东西的线**（恒 2026-09-06 拍板：**HP<60% 就吃**）——与 `--hp-threshold`（出门/撤退线，默认 30）**两回事**。
# 🔴 2026-10-03 恒真机逮到：深层 87 层血掉到 **33% 还在打、不吃**。查下来是**这两条线被搅在一起了** ——
#    `--hp-threshold`（我按 MCP 默认传了 30）**同时**当了吃的线 ⇒ 吃被压到 <30%（满 180 血 = 54 才吃）。
#    而本文件那条注释早就写着"两者分开，别重合"（注释对了、代码没做到）。
#    ⇒ 吃的线**固定**在这个常量上，`hp_threshold` 形参从此只管出门/撤退那两条（**参数保留但不再影响吃**）。
EAT_HP_PCT = 60

# ⚡ **吃食物的体力线**（与上面 HP 线对称）。原来是 10%，而"体力该撤了"的线是 **15%**
#    （`mine_run.unsafe_reason(sta_threshold=15)`）⇒ **吃在撤之后 = 这条吃食路径是死的**
#    （体力掉到 15% 人已经撤了，永远轮不到 10%）。2026-10-03 恒一问"体力线是多少"才照出来 ——
#    同族病：**两条线的相对大小没人核过**。⇒ 提到 30%（稳稳在撤退线之上）。
#    ⚠️ 以后动这两条线，先核"**吃 < 撤** 才成立"（吃必须**高于**撤）。
EAT_STA_PCT = 30

# ⏳ 补 buff 吃完后**等它真挂上**的上限（秒）。2026-10-03 真机量到：吃下去到 `/buffs` 里出现
#    大约 **2~3 秒**（动画播完 → doneEating → applyBuff）。**别拿"吃完立刻读"当结论** ——
#    那一枪必然读到空的，于是"没挂上 ⇒ 再吃一份"（现场连吃两份香辣鳗鱼）。
BUFF_APPLY_TIMEOUT = 6.0


# ── 🍽️ 食物条目：**一个形状** ────────────────────────────────────────────────
# 全项目统一成 `(名字, 回体力, 回血, buff档)`（第 4 位可缺 = 老形状/拿不到）。
# ⚠️ 2026-10-03 顺带查出的**形状漂移旧账**：`bomb_common.detect_food()` 原来返回
#    `(名字, 回血, 性价比)`，而 `pick_food_closest_to_full` 按 `(名字, 回体力, 回血)` 读
#    ⇒ 炸矿脚本里"血低"那一支其实在**按性价比挑**、"体力低"那一支在**按回血量挑**。
#    同族病（`/state` 瘦/`/menu` 详、`catNum` 两种口径）：**同一个字段名两种口径**。
#    ⇒ 从此两边都是 `(名字, 回体力, 回血, buff档)`，下标含义只此一份。


def food_buffs_of(f):
    """🍽️ 这条食物"吃下去会挂什么 buff"（条目第 4 位 = C# `/state` 的 `foodBuffs`）。

    **判据只此一处**：游戏自己的 `Object.GetFoodOrDrinkBuffs()`（反编译 `Farmer` 吃食结算那条链
    就是遍历它逐个 `applyBuff`）。消费侧**不许再编名单** —— 恒 2026-10-03 拍的就是这个清理
    （`BUFF_DISH_PRIORITY` / `BUFF_DRINK_PRIORITY` / `DRINK_BUFFS` / `buff_duration` 四张手抄表已删）。

    ⚠️ **C# 真形状是对象**：`{"isDrink": bool, "buffs": [ {id, source, ms, effects, rawEffects}, … ]}`
       —— 本函数**负责拆到 `buffs` 那一层**；调用方拿到的是 buff 列表（不是那个对象）。
    🐛 2026-10-03 真机逮到的（**我自己造的假绿灯**）：C# 那边写的是对象，Python 这边却按**数组**读
       ⇒ `isinstance(v, (list, tuple))` 不成立 ⇒ **恒真返回"没有效果"** ⇒
       「效果食物除外」「✨带效果的」「点名补 buff」全成了哑巴，而自验还是全绿 ——
       因为**夹具用的是我脑子里那个形状**，不是 C# 真报的形状。
       ⇒ 教训：跨语言的那一层，**夹具必须照真回包抄**（这次照抄的是 7842 的真 `/state`）。
    也**兼容**直接给数组的老写法（自验里的假数据），但**别把它当真形状用**。
    拿不到这一位（老 DLL / 3 元组）⇒ 空列表 = "不知道"，**不猜**（宁缺勿编）。
    """
    if not isinstance(f, (list, tuple)) or len(f) < 4:
        return []
    v = f[3]
    if isinstance(v, dict):                 # ⬅️ C# 真形状：{isDrink, buffs:[…]}
        v = v.get("buffs")
    return list(v) if isinstance(v, (list, tuple)) else []


def food_buff_text(f):
    """这条食物的效果文案（**游戏自己**的本地化描述，如 `+1 运气 +1 速度`）；没 buff 给 `""`。"""
    parts = []
    for b in food_buffs_of(f):
        if not isinstance(b, dict):
            continue
        for key in ("effects", "rawEffects"):
            for e in (b.get(key) or []):
                s = str(e)
                if s and s not in parts:
                    parts.append(s)
    return " ".join(parts)


def food_buff_ids(f):
    """这条食物会挂的 buff id（= 游戏里的 buff **槽位**，同 id 互相顶掉，见 `BuffManager.Apply`）。"""
    out = []
    for b in food_buffs_of(f):
        if isinstance(b, dict) and b.get("id"):
            out.append(str(b["id"]))
    return out


def food_matches_buff(f, keyword):
    """这条食物是否符合效果关键字（`food_buff` 点名用）：**只打游戏报的那些字**（效果文案/buff id/
    吃食名/buff 的来源显示名），大小写无关。关键字为空 ⇒ 恒真（等于"不挑"）。

    ⚠️ 四种字面都认，是因为**同一个东西在不同地方名字不同**（真机 7842 的样本）：
       · 效果文案 `+1 运气`（游戏本地化）· buff id `food`/`17` · 背包里的名字 `Spicy Eel`（英文内部名，
         就是回执里印的那个）· buff 的来源显示名 `香辣鳗鱼`（中文）。AI 从回执里抄哪个都该命中。
    """
    kw = (keyword or "").strip().lower()
    if not kw:
        return True
    parts = [food_buff_text(f)] + food_buff_ids(f) + [str(f[0])]
    for b in food_buffs_of(f):
        if isinstance(b, dict) and b.get("source"):
            parts.append(str(b["source"]))
    hay = " ".join(parts).lower()
    return all(k in hay for k in [x for x in re.split(r"[,，\s]+", kw) if x])


def pick_food_closest_to_full(foods, need_hp=0, need_sta=0):
    """🍽️ 按「**离补满最接近**」挑一件吃的（恒 2026-10-03 的口径）。

    恒原话：「**先碰到哪条线，就选"离能回复满最接近的那个背包食物（效果食物除外）"**，
    比如奶酪 +60、韭葱 +20，我的血是 20/40 触发了吃食，那就**吃韭葱不浪费**。」

    规则：
      · 有**够补**（恢复量 ≥ 缺口）的 ⇒ 挑其中**恢复量最小**的（刚好够、不浪费好东西）；
      · **都不够** ⇒ 挑**恢复量最大**的（别拿小的白吃一顿）；
      · **血低时恢复量为 0 的永不选**（保留 2026-09-06 的老规矩"绝不拿纯体力咖啡保命"）；
      · **带 buff 的（效果食物）不参与**（恒那句"效果食物除外"）—— 判据是 `food_buffs_of()`
        （游戏报的），不是名单。⚠️ **包里除了效果食物没别的 ⇒ 就是不吃**（不是"退回吃效果食物"）：
        恒 2026-10-03 把规矩说死了 ——「**有点名只吃点名，吃完了也不吃别的；不点名才自动吃**」
        ⇒ 没有"自作主张兜底"这一档。**调用方必须吭一声**（拿 `effect_food_note()` 打一行），
        让 AI 知道"不是没吃的，是只有带效果的、按规矩没动" —— 想吃就自己点名（`food_buff`）。
      · `need_hp`/`need_sta` **只该有一条 > 0**（哪条线触发就补哪条；调用方决定"血优先"）。
    `foods` = `(名字, 回体力, 回血[, buff档])`；返回**食物名**或 `None`。
    """
    cand = [f for f in (foods or []) if isinstance(f, (list, tuple)) and len(f) >= 3]
    if not cand:
        return None
    pool = [f for f in cand if not food_buffs_of(f)]   # 效果食物除外（恒的规矩，见 docstring）
    if not pool:
        return None
    use_hp = need_hp > 0
    val = (lambda f: f[2] or 0) if use_hp else (lambda f: f[1] or 0)
    need = need_hp if use_hp else need_sta
    ok = [f for f in pool if val(f) > 0]            # 血低时不选"回血 0"的（咖啡那种）
    if not ok:
        return None
    if need > 0:
        enough = [f for f in ok if val(f) >= need]
        if enough:
            return min(enough, key=val)[0]          # 刚好够 ⇒ 挑最小的，不浪费
    return max(ok, key=val)[0]                      # 都不够 ⇒ 挑最大的


def effect_food_note(foods):
    """「包里**只剩**带效果的那些了」这句话（没这种情况给 `""`）—— 自动挑食**一条都没挑出来**时,
    调用方打这一行：不吃饭的原因要**说得出来**（同族教训：静默 = "我点名了"和"点名没生效"长得一样）。"""
    if not foods:
        return ""
    plain = [f for f in foods if not food_buffs_of(f)]
    if plain:
        return ""
    buffed = [f for f in foods if food_buffs_of(f)]
    if not buffed:
        return ""
    names = "、".join(f"{f[0]}({food_buff_text(f) or '效果游戏没报数值'})" for f in buffed[:4])
    more = f" 等 {len(buffed)} 样" if len(buffed) > 4 else ""
    return f"⚠️ 包里只剩带效果的食物：{names}{more} —— 按「效果食物除外」没动它们（要吃就点名 food_buff）"


# ⚰️ `pick_buff_food(foods, keyword)` 2026-10-03 删：写完发现**没有调用点** ——
#    补 buff 那条路（`maintain_buffs_for`）要的是**整条记录**（id/ms/效果文案），不是光一个名字
#    ⇒ 它自己按 `food_buffs_of` + `food_matches_buff` 遍历就够了。**没有调用点的函数不许留着**
#    （本项目 2026-10-03 刚清过两个这种孤儿：`/petall` `/waterbowl`）。


# ── 🔌 两个脚本族的"机器人"接口不同名，这里各收一处 ──────────────────────────────
#   `mine_run.MineBot` 与 `bomb_common.BombMiner` 是**两个类**（不是继承），吃食/状态入口名字不同。
#   ⚠️ 同族教训（2026-09-20 真机）：给一个类写好分支**不等于**另一个类也走那条路
#      （当时 `eat_recovery` 压根没读 `self.food_hp`，点名在炸矿脚本里是死的）。
#      ⇒ 凡是两边都要用的判据，**只写在这里一份**，别在各自的类里各抄一遍。

def bot_foods(bot):
    """背包里的吃食列表（`MineBot.detect_inventory_food` / `BombMiner.detect_food`；
    形状同一个 `(名字, 回体力, 回血, buff档)`，见文件顶部的形状说明）。"""
    for attr in ("detect_inventory_food", "detect_food"):
        fn = getattr(bot, attr, None)
        if callable(fn):
            try:
                return fn() or []
            except Exception:
                return []
    return []


def bot_eat(bot, name):
    """让 bot 吃**指定**那样。两个族的吃法入口不同名（`BombMiner.eat` / `MineBot._eat_one`）。"""
    fn = getattr(bot, "eat", None)
    if callable(fn):
        try:
            return bool(fn(name))
        except Exception:
            return False
    fn2 = getattr(bot, "_eat_one", None)
    if callable(fn2):
        try:
            return bool(fn2(name, "补 buff"))
        except Exception:
            return False
    return False


def eat_patiently(bot, name, after=2.0):
    """拟人吃：**先把走位停干净**（`/stop` 清掉异步走位队列 —— 边走边吃会让动画被覆盖、
    `doneEating` 不跑 ⇒ 白吃，恒真机「边走边吃吃不上」）→ 吃 → 等动画播完。"""
    try:
        bot._post("/stop")          # `/walk_to` 是异步的：不清队列，人会被继续推着走
    except Exception:
        pass
    try:
        for _ in range(6):
            s = bot.state() or {}
            if not (s.get("player") or {}).get("isMoving", True):
                break
            time.sleep(0.3)
    except Exception:
        pass
    time.sleep(0.4)          # 等游戏 tick 完全空闲（刚瞬移/切层完立刻吃会"吃掉但不加 buff"）
    ok = bot_eat(bot, name)
    if ok:
        time.sleep(after)    # 等吃喝动画播完（buff 才真正生效）
    return ok


def active_buffs(bot):
    """现在挂着的 buff：`{id: 剩余秒}`（**id 就是槽位**，同 id 互相顶，见 `BuffManager.Apply`）。"""
    out = {}
    try:
        r = bot._get("/buffs") or {}
    except Exception:
        return out
    for b in (r.get("buffs") or []):
        b = b or {}
        bid = str(b.get("id") or "")
        try:
            secs = float(b.get("seconds") or 0)
        except Exception:
            secs = 0.0
        if bid:
            out[bid] = max(out.get(bid, 0.0), secs)
    return out


def maintain_buffs_for(bot, threshold=30, want=None, min_gap=8.0):
    """🍽️ 补 buff（**数据驱动**：认游戏报的 buff，不认名单）—— 返回是否吃了一次。

    判据两侧全是游戏给的：
      · **该吃什么**：背包里 `food_buffs_of()` 非空的那些（C# = `Object.GetFoodOrDrinkBuffs()`），
        每条buff 自带 `id`/`ms`/效果文案；
      · **现在挂着什么**：`/buffs` 的 `id` + 剩余秒。
    两侧**同一个 id 就是同一个槽**（`BuffManager.Apply` 先 `Remove(buff.id)` 再放进去）⇒
    原先那两张"菜品/饮品优先级"手抄表 + `DRINK_BUFFS` + `buff_duration`（时长也是抄的）**全部删掉**。

    `want` = 恒的 `food_buff` 点名（效果关键字，如 `"运气"`）：
      · 点名 ⇒ **只认匹配的那份**；没有 ⇒ **不吃别的**（恒 2026-10-03：「有点名只吃点名，
        吃完了也不吃别的；不点名才自动吃」）；
      · 不点名 ⇒ 包里任意带 buff 的都算候选，**按背包顺序**（不另立优先级表）。
    """
    now = time.time()
    if now - getattr(bot, "_last_buff_check", 0.0) < min_gap:
        return False
    bot._last_buff_check = now

    cands = [f for f in bot_foods(bot) if food_buffs_of(f) and food_buff_ids(f)]
    if want:
        cands = [f for f in cands if food_matches_buff(f, want)]
    if not cands:
        if want:
            log(f"  ⚠️ 背包里没有「{want}」那份带 buff 的吃食 —— 不吃别的（有点名只吃点名的）")
        else:
            log("  ⚠️ 背包里没有带 buff 的吃食 —— 跳过补 buff")
        return False

    active = active_buffs(bot)
    best_left, best_name = -1.0, ""
    for f in cands:                                   # 背包顺序，第一个"该补"的吃
        left = max([active.get(i, 0.0) for i in food_buff_ids(f)] or [0.0])
        if left > threshold:
            if left > best_left:
                best_left, best_name = left, f[0]
            continue
        name = f[0]
        if eat_patiently(bot, name):
            # ⏳ 等 buff **真挂上**再走（2026-10-03 真机：吃完 2 秒内 `/buffs` 还是空的 ⇒
            #    紧接着再调一次会**把同一份又吃一遍** —— 现场就是连吃了两份香辣鳗鱼）。
            #    老代码用 `_buff_track`（自家记时间）糊过去，那份记账 2026-10-03 已删（会漂）
            #    ⇒ 改成**问游戏**：轮询到该 id 出现为止。写法照 `eat_recovery` 那条"轮询到真回血"
            #    （同一族教训：**别拿采样太早当结论**）。
            landed = 0.0
            deadline = time.time() + BUFF_APPLY_TIMEOUT
            while time.time() < deadline:
                time.sleep(0.3)
                got = max([active_buffs(bot).get(i, 0.0) for i in food_buff_ids(f)] or [0.0])
                if got > 0:
                    landed = got
                    break
            if landed > 0:
                log(f"  🍽️ 补 buff：{name}（{food_buff_text(f) or '效果游戏没报数值'}；"
                    f"补前剩 {left:.0f}s → 挂上 {landed:.0f}s）")
            else:
                log(f"  ⚠️ 补 buff：{name} 吃下去了，但 {BUFF_APPLY_TIMEOUT:.0f}s 内 `/buffs` 没见它挂上"
                    f"（动画被打断？）—— **如实报，不装成功**")
            return True
        log(f"  ⚠️ 补 buff 要吃的 {name} 没吃上 —— 跳过（不换别的）")
        return False
    # 🍽️ 一件都不用补时**也要说一句**（2026-10-03 真机我自己踩的）：原来这里静默 `return False`，
    #    于是**「查过了、还没到线」和「压根没跑这一步」在日志里长得一模一样** —— 我为了查
    #    "矿井里 food_buff 为什么一声没吭"白跑了两趟（真因就是它当时还剩 35s > 30s，正常跳过）。
    #    ⇒ 同族规矩：**判过就要留痕**（`/passable_rect` 静默无视 location 也是这个病）。
    if best_left >= 0:
        log(f"  🍽️ 补 buff：还没到线（{best_name} 还剩 {best_left:.0f}s > {threshold}s）—— 不吃")
    return False

# 协同模式只在路径上炸这些高价值矿（不浪费炸弹炸普通石头）
HIGH_VALUE_ORES = {
    "Iridium Node", "Gem Node", "Diamond Node", "Gold Node",
    "Amethyst Node", "Topaz Node", "Emerald Node", "Aquamarine Node",
    "Jade Node", "Ruby Node",
}

# 掉落物价值排序（拾取优先级 + 背包规划用）
DROP_RANK = {
    "Prismatic Shard": 200, "Galaxy Soul": 400, "Radioactive Bar": 300, "Radioactive Ore": 220,
    "Iridium Bar": 180, "Gold Bar": 150, "Diamond": 120,
    "Iridium Ore": 110, "Ruby": 70, "Emerald": 70, "Aquamarine": 65,
    "Topaz": 60, "Amethyst": 60, "Jade": 60, "Omni Geode": 55,
    "Dwarf Scroll": 60, "Fire Quartz": 45, "Frozen Tear": 40, "Earth Crystal": 40,
    "Magma Geode": 40, "Frozen Geode": 35, "Gold Ore": 45, "Geode": 32,
    "Cinder Shard": 30, "Coal": 30, "Iron Ore": 25, "Quartz": 20, "Copper Ore": 22,
    "Cave Carrot": 15, "Stone": 2, "Clay": 5, "Bone Fragment": 12,
    "Dragon Tooth": 100,  # 龙牙=(O)852 附魔燃料，火山岩浆怪掉
}

# 必捡掉落关键词（游戏中文版 /debris 返回中文名，中英都认；每层一次扫描只挑这些）
VALUABLE_KEYWORDS = ("Iridium", "铱", "Radioactive", "放射性", "Prismatic", "五彩",
                     "Galaxy", "银河", "Cinder", "火山晶石", "Dragon", "龙牙")  # 龙牙=(O)852 附魔燃料

# 武器关键词（背包里找剑/斧防身）
WEAPON_KEYWORDS = ("Sword", "Blade", "Saber", "Club", "Hammer", "Dagger",
                   "Galaxy", "Katana", "Kunai", "Rapier", "Whip", "Foam",
                   "Scythe", "Dwarvish", "Dragonscale")

# 恢复食物 → (体力恢复, 生命恢复)（SDV 1.6 edibility 换算；用于自适应选"补满最合适那一个"）
FOOD_RECOVERY = {
    "Salad": (100, 50), "Farmer's Lunch": (200, 100),
    "Cave Carrot": (45, 23), "Spring Onion": (13, 7),
    "Common Mushroom": (38, 19), "Carp": (30, 15), "Chub": (40, 20),
    "Bread": (50, 25), "Field Snack": (45, 23), "Winter Root": (25, 13),
    "Snow Yam": (25, 13), "Sea Jelly": (25, 13), "Seaweed": (20, 10),
    "Pepper Poppers": (90, 45), "Sashimi": (75, 38), "Omelet": (50, 25),
    "Fried Egg": (20, 10), "Cheese": (75, 38), "Maple Bar": (45, 23),
    "Pineapple": (100, 50),  # 菠萝：姜岛后期水果，100体力/50血
    "Magma Cap": (35, 16), "熔岩菇": (35, 16),  # 火山熔岩菇：能吃回血（user 2026-08-10 说大补）
}


def log(msg):
    """GBK 终端安全打印"""
    try:
        print(f"[bomb] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[bomb] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def extract_mine_level(loc_name):
    """'UndergroundMine42' → 42；'VolcanoDungeon3' → 3；不是矿井返回 None。
    火山矿洞层号(0-10)是独立空间，用 is_volcano() 区分（别和普通矿井 1-121+ 混算）。"""
    m = re.search(r'(?:UndergroundMine|VolcanoDungeon)(\d+)', loc_name or "")
    return int(m.group(1)) if m else None


def is_mine_location(loc_name):
    """矿井判断：普通矿井/头骨矿洞深层 = UndergroundMine 前缀；
    头骨矿洞入口 = SkullCave；火山矿洞 = VolcanoDungeon（user在火山也算矿井，一起冲层/撤退用）。"""
    if not loc_name:
        return False
    return ("UndergroundMine" in loc_name or loc_name == "SkullCave"
            or "VolcanoDungeon" in loc_name)


def is_volcano(loc_name):
    """是否火山矿洞（VolcanoDungeon0-10，撤退回姜岛入口）。"""
    return bool(loc_name and "VolcanoDungeon" in loc_name)


def is_rock(name):
    """可被炸弹炸碎的矿洞岩体"""
    if not name:
        return False
    if name == "Stone":
        return True
    if "Node" in name or "Geode" in name:
        return True
    return False


def rock_score(name):
    """矿石价值分（头骨矿洞贪心用：放射>铱>钻>宝石>火山晶石>金>铁>铜>晶球>石头）"""
    if not name:
        return 0
    if "Radioactive" in name:
        return 120  # 放射性矿石（1.6 高级矿，最值钱）
    if "CalicoEggStone" in name:
        return 100  # 🥚 卡利科三花蛋矿（沙漠节骷髅洞，1块=15卡利科蛋，优先级≈铱矿；只在沙漠节骷髅洞出现）
    if "Iridium" in name:
        return 100
    if "Diamond" in name:
        return 90
    if any(g in name for g in ("Gem", "Amethyst", "Ruby", "Emerald", "Aquamarine", "Jade", "Topaz")):
        return 80
    if "Cinder" in name:
        return 70  # 火山晶石
    if "Gold" in name:
        return 60
    if "Iron" in name:
        return 40
    if "Copper" in name:
        return 30
    if "Magma" in name:
        return 35  # 岩浆晶球
    if "Geode" in name:
        return 20
    return 5  # 普通石头


def drop_value(name):
    v = DROP_RANK.get(name)
    if v is not None:
        return v
    # 中文名兜底（游戏中文版 /debris 返回中文名）：铱/放射/五彩/银河都是高价值
    for kw, val in (("铱", 110), ("Iridium", 110),
                    ("放射", 220), ("Radioactive", 220),
                    ("五彩", 200), ("Prismatic", 200),
                    ("银河", 400), ("Galaxy", 400),
                    ("火山", 70), ("Cinder", 70),
                    ("龙牙", 100), ("Dragon Tooth", 100)):
        if kw in name:
            return val
    return 10


def item_keep_score(name, value):
    """物品保留优先级（越高越该留）。背包满取舍用：丢最低分垃圾，保留工具/炸弹/石头/稀有。"""
    if name in BOMB_NAMES or "Pickaxe" in name or "Axe" in name or "Hoe" in name \
            or "Can" in name or "Sword" in name or "Blade" in name:
        return 100  # 工具/武器/炸弹
    if name == "Stone":
        return 90  # 石头造楼梯跳关
    if name in ("Iridium Ore", "Prismatic Shard", "Diamond", "Golden Walnut", "Qi Gem",
                "Gold Ore", "Omni Geode", "Mystic Stone Seed",
                "Radioactive Ore", "Radioactive Bar", "Cinder Shard", "Galaxy Soul",
                "Dragon Tooth"):
        return 80  # 高价值稀有（含放射矿/放射锭/火山晶石/银河之魂/龙牙(附魔燃料)）
    if name in ("Salad", "Cheese", "Pineapple", "Crab Cakes", "Spicy Eel", "Lucky Lunch",
                "Pepper Poppers", "Miner's Treat", "Fish Taco", "Pumpkin Soup"):
        return 70  # 回血/buff 食物
    if value >= 500:
        return 65
    if value >= 200:
        return 50
    if value >= 60:
        return 30
    return 10  # 低价值垃圾


def set_pause(base_url=None, out_of_focus=True):
    """设置"失焦暂停"（POST /set_pause）。
    AI 进程设 False → 后台也能走位，不抢前台焦点（user的窗口不受影响）。
    返回 (ok, was)。端点不存在（旧 DLL）时静默失败。"""
    base = base_url or NAGI_URL
    try:
        r = requests.post(f"{base}/set_pause", json={"outOfFocus": out_of_focus}, timeout=5).json()
        return r.get("ok", False), r.get("was", None)
    except Exception:
        return False, None


def parse_food_list(raw):
    """🍽️『奶酪,鱼肉卷, 沙拉』→ ['奶酪','鱼肉卷','沙拉']（2026-09-20 恒：自定义吃食）。

    容忍用户/AI 手写的各种分隔与空白（全角逗号、顿号、分号、空格都当分隔）——
    这类"看着传对了、其实没匹配上"的静默失败最难查（工具会当成"包里没有"，
    然后一路降级到自动挑，**把点名的意图整个吃掉**）。
    · `None` / 空串 → `[]`
    · **单个名字 → `[名字]`**：故意兼容旧用法（以前 `--food-hp` 只收一个名字），
      老调用方一个字都不用改。
    · 已经是 list/tuple → 原样清洗（去空白、丢空项），方便内部调用。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        s = str(raw)
        for ch in ("，", "、", "；", ";", " "):
            s = s.replace(ch, ",")
        items = s.split(",")
    return [x.strip() for x in items if x and x.strip()]


def pick_food_by_priority(names, available):
    """🕐 按**给定顺序**在背包里找第一个有的 → 返回名字；整张表都没货 → `None`。

    这就是"优先级"的**全部**含义：表里靠前的先吃。
    ⚠️ 匹配的是**物品名**（`/state` 的 `name`），跟 `select` 同一口径；
       名字写错不会报错、只会静默跳过 —— 所以调用方在"整张表都没货"时**必须吭声**
       （见 `BombMiner.eat_if_needed` 里那条 ⚠️ 日志）。
    """
    if not names:
        return None
    for n in names:
        if n in available:
            return n
    return None


def set_guard(base_url=None, on=True, weapon=None):
    """🛡️ 开关 C# 侧的「贴身自动防御」（POST /guard，2026-09-20 恒）。

    为什么必须搬到 C# 侧（症状：AI 挖矿被怪打了两三下才还手）：
      Python 的自卫是**轮询** —— `retaliate_if_hit()` 挂在外层 while 每轮一次
      （mine_run.py:1502），而一轮要敲最多 3 块石头、每块 1.5~4s ⇒ **HP 检查间隔 2~10 秒**；
      怪物 1~2s 打一下 ⇒ 正好差 2~3 下，跟恒实测完全对上。
      Python 侧无解：它 ~90% 的时间在 `time.sleep()` 里睡着（HTTP 其实不慢：/state 13ms、
      /tool 26ms），没有任何"优先级"能叫醒一个睡着的进程。C# 的 OnUpdateTicked 每 tick 都醒着。

    weapon：**点名要哪把**。`WeaponMixin.detect_weapon` 已经有唯一一套优先级，
      传名字过去就够，别让 C# 再挑一套（两份必然漂移）。传 None = C# 自己挑。

    ⚠️ 端点不存在（旧 DLL）时**静默失败** —— 形状照抄上面的 `set_pause`：
      老 DLL 上跑新脚本不该直接炸，但返回 `(False, None)` 让调用方**自己决定要不要吭声**。
    """
    base = base_url or NAGI_URL
    try:
        body = {"on": bool(on)}
        if weapon:
            body["weapon"] = weapon
        r = requests.post(f"{base}/guard", json=body, timeout=5).json()
        return r.get("ok", False), r
    except Exception:
        return False, None


def focus_game(base_url=None):
    """把游戏窗口切前台（SDV 后台失焦会暂停走位/拾取）。
    优先 /focus 端点（游戏进程自己切，最可靠），退回 ctypes 按进程名找窗口。"""
    base = base_url or NAGI_URL
    try:
        r = requests.get(f"{base}/focus", timeout=5)
        if r.json().get("ok"):
            return True
    except Exception:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        found = []

        def _proc_name(pid):
            h = kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(300)
                psapi.GetModuleBaseNameW(h, None, buf, 300)
                return buf.value.lower()
            finally:
                kernel32.CloseHandle(h)

        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if "stardew" in _proc_name(pid.value):
                    found.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        if found:
            user32.SetForegroundWindow(found[0])
            return True
    except Exception:
        pass
    return False


class WeaponMixin:
    """⚔️ 武器复用（2026-09-06 抽成 mixin，BombMiner & MineBot 共用）：选武器(跳过工具镰)+武器类别+挥速自适应+锤子重砸。
    依赖宿主类属性：weapon_name/weapon_class/weapon_speed/weapon_override/_last_special/bomb_type，
    及方法 select()/use_tool()/face_toward()/state()/_post()/detect_weapon()。"""

    def detect_weapon(self):
        """选当前武器：武器绑定优先 > 真实武器（带 wtype，跳过工具镰） > 关键词兜底。
        实测 AI 背包 Iridium Scythe（工具镰，无 wtype）排在 Galaxy Hammer 前，旧逻辑会拿镰刀打架。
        ⚠️ 2026-08-10 user反馈"拿大镰刀挥挥挥"——兜底分支用 WEAPON_KEYWORDS 含 Scythe，
        若 wtype 路径失效会选中工具镰。加固：兜底跳过"工具镰"（名字含 Scythe 且类别=工具）。"""
        s = self.state()
        inv = s.get("inventory", [])
        # 1️⃣ 武器绑定：指定了就优先用指定的
        if self.weapon_override:
            for item in inv:
                name = item.get("name", "")
                if name == self.weapon_override or self.weapon_override in name:
                    return self._adopt_weapon(item)
        # 2️⃣ 真实武器优先（带 wtype 的 MeleeWeapon）。⚠️ 2026-08-10 实测 Iridium Scythe
        #    是 MeleeWeapon(wtype=3) 不是 Tool，背包排第3位会抢在 Galaxy Hammer(第18位)前被选！
        #    → 跳过 wtype=3(镰) 优先选剑/锤/匕首(0/1/2)，只有镰时才用镰。
        for item in inv:
            if item.get("wtype") not in (None, 3) and "Sling" not in (item.get("name") or ""):
                return self._adopt_weapon(item)
        for item in inv:
            if item.get("wtype") == 3 and "Sling" not in (item.get("name") or ""):
                return self._adopt_weapon(item)
        # 3️⃣ 兜底：名字/类别关键词（旧 DLL 无 wtype）
        weapon_cat = ("武器", "Weapon", "剑", "匕首", "锤", "刀", "刃", "鞭", "杖", "爪", "戟")
        for item in inv:
            name = item.get("name", "")
            if "Sling" in name:
                continue
            cat = str(item.get("category", "") or "")
            # ⚠️ 工具镰（Iridium Scythe 等 Tool）不是武器——名字含 Scythe 且类别=工具（"工具"）就跳过
            if "Scythe" in name and ("工具" in cat or cat == "Tool"):
                continue
            if any(kw in name for kw in WEAPON_KEYWORDS) or any(k in cat for k in weapon_cat):
                return self._adopt_weapon(item)
        self.weapon_name = None
        self.weapon_class = None
        self.weapon_speed = 0
        return False

    def _adopt_weapon(self, item):
        """把物品设为当前武器（name/class/speed）。"""
        name = item.get("name", "")
        cat = str(item.get("category", "") or "")
        wtype = item.get("wtype")
        self.weapon_name = name
        try:
            self.weapon_speed = int(item.get("wspeed") or 0)
        except (TypeError, ValueError):
            self.weapon_speed = 0
        # 武器类型：优先 wtype。⚠️ SDV 1.6 反编译确认：0=剑(旋风) 1=匕首(冲刺) 2=锤(重砸) 3=格挡剑/镰
        if wtype == 2:
            self.weapon_class = "hammer"
        elif wtype == 1:
            self.weapon_class = "dagger"
        elif wtype is not None:
            self.weapon_class = "sword"
        else:
            cls_src = f"{name} {cat}"
            if any(k in cls_src for k in ("Hammer", "Club", "Slammer", "锤", "棒", "压碎")):
                self.weapon_class = "hammer"
            elif any(k in cls_src for k in ("Dagger", "Kunai", "匕首")):
                self.weapon_class = "dagger"
            else:
                self.weapon_class = "sword"
        return True

    def weapon_special(self):
        """触发当前武器特殊攻击（锤=右键重砸 Super Slam，直接砸不蓄力、有冷却）。
        需要 ModEntry /tool {special:true}（重编译后可用）。返回是否 ok。"""
        try:
            r = self._post("/tool", {"special": True})
            return r.get("ok", False)
        except Exception:
            return False

    def _swing_wait(self):
        """按武器类型+速度算挥击等待（秒）：速度越快动画越短，下限防 spam 判定丢失。
        反编译确认（2026-08-09）：攻击动画时长 swipeSpeed = 400 - speed×40 ms（setFarmerAnimating）。
        脚本要等"下一刀能判定"，加 ~0.12s 安全垫；锤子挥击动画更长加 ~0.1s。
        附魔附速度后 wspeed 实时变，这里自动跟随。"""
        sp = getattr(self, "weapon_speed", 0) or 0
        anim_ms = max(100, 400 - sp * 40)   # 速度8→80ms，保底100ms
        base = anim_ms / 1000.0 + 0.12
        if self.weapon_class == "hammer":
            return max(0.28, base + 0.10)   # 锤平砍也慢
        if self.weapon_class == "dagger":
            return max(0.20, base - 0.05)
        return max(0.24, base)              # sword（含镰）

    def swing(self, toward=None, special=False):
        """统一武器挥击（等待按类型+速度自适应）→ 挥完切回 bomb_type。
        toward=(x,y) 先面向目标；special=True 且手持锤子 → 右键重砸（范围杀伤，直接砸不蓄力，有冷却）。
        返回是否挥了。"""
        if toward:
            self.face_toward(*toward)
        self.detect_weapon()
        if not self.weapon_name:
            self.use_tool("Pickaxe")
            time.sleep(0.25)
            self.select(self.bomb_type)
            return True
        self.select(self.weapon_name)
        time.sleep(0.1)
        if special and self.weapon_class == "hammer":
            if time.time() - self._last_special >= HAMMER_SPECIAL_COOLDOWN:
                log("  🔨 锤子重砸！")
                self.weapon_special()
                self._last_special = time.time()
                time.sleep(0.8)   # 重砸动画+判定落地
            else:
                self.use_tool()   # 冷却中 → 平砍
                time.sleep(self._swing_wait())
        else:
            self.use_tool()
            time.sleep(self._swing_wait())
        self.select(self.bomb_type)
        return True

    def guard_on(self, weapon=None):
        """🛡️ 开贴身自动防御（C# 每 tick 找 3×3 内的怪并挥刀，**只转向+挥、不移动**）。
        weapon 默认 = 本次 `detect_weapon()` 挑出来的那把。
        ⚠️ 开不上就**明说**（宁报错别兜底）—— 不然脚本后面会假装有人保它。"""
        if weapon is None:
            if not self.weapon_name:
                self.detect_weapon()
            weapon = self.weapon_name
        ok, r = set_guard(self.base, True, weapon)
        self._guard_on = bool(ok)
        if ok:
            log(f"  🛡️ 贴身防御已开（武器：{(r or {}).get('weapon') or '自动挑'}）")
        else:
            why = (r or {}).get("error") or "端点不存在（旧 DLL？）"
            log(f"  ⚠️ 贴身防御没开上（{why}）—— 本趟只有 Python 的受击回击，反应会晚 2~3 下")
        return ok

    def guard_off(self):
        """🛡️ 关贴身自动防御。
        ⚠️ 必须放 finally 里。**但 Windows 上被杀的子进程走不到 finally**
           （`_bg_kill` = TerminateProcess）—— 那条路由 MCP 服务器补刀，见
           nagi_mcp_server._bg_kill 里那段注释。"""
        ok, r = set_guard(self.base, False)
        self._guard_on = False
        if ok:
            log(f"  🛡️ 贴身防御已关（本趟共挥 {(r or {}).get('swings', '?')} 刀）")
        return ok

    def retaliate_if_hit(self):
        """受击及时回击：HP 比上次低 → 回击两下（补刀）。不依赖扫描循环（动作后立即调用，减少回击延迟）。
        锤子第一下能重砸就重砸（受击反击也吃重砸），第二下在冷却里自动平砍。2026-09-06 复用给 MineBot。

        🛡️ 2026-09-20：**C# 的 guard 开着时本函数退化成空操作** —— guard 在受击的**同一 tick**
        就还手了，这里再砍两下 = 对着空气多挥 2 刀 + 2 次 HTTP（~360ms），还会把 guard 刚摆好的
        朝向搅乱。
        ⚠️ **但绝不删**：guard 没开上（旧 DLL / 没武器 / `--no-guard` / 手动单跑）时，
        它是**唯一还活着的自卫**。"""
        if getattr(self, "_guard_on", False):
            return
        try:
            cur_hp = self.state().get("player", {}).get("health", 0)
        except Exception:
            return
        if cur_hp < getattr(self, "_last_hp", 9999):
            log("  🛡️ 受击！立刻回击两下")
            for _ in range(2):
                self.swing(special=self.weapon_class == "hammer")
        self._last_hp = cur_hp


class BombMiner(WeaponMixin):
    """炸弹矿工：协同/自动/手动三模式共用的一套底层动作"""

    def __init__(self, port=None, host_port=None, bomb_type="Bomb"):
        self.base = f"http://localhost:{port if port else 7843}"
        self.host = f"http://localhost:{host_port if host_port else 7842}"
        self.sess = requests.Session()
        self.bomb_type = bomb_type if bomb_type in BOMB_NAMES else "Bomb"
        self.weapon_name = None
        self.weapon_class = None   # hammer/sword/dagger——挥击节奏 & 锤子重砸判定用
        self.weapon_speed = 0      # 武器速度 stat（附魔附速度也在内）——挥击间隔自适应用
        self.weapon_override = None  # 武器绑定：指定用某把武器（bomb_mine --weapon）
        self.mine_level = 0
        self._last_special = 0.0   # 上次特殊攻击时间（锤子重砸冷却跟踪）
        self._bombed_anchors = set()   # 炸过的锚点，贪心跳过，防重复选同点
        self._bombed_rocks = set()     # 已爆炸覆盖的石头，贪心排除，防同范围重复放炸弹
        self._pending_bombs = []       # [(ax, ay, 放置时间, bomb_type)] 还没爆炸的炸弹——选锚点/敲石头要避开其范围
        self._floor_entrance = None    # 当前层入口梯子（逃出用）
        self._recover_streak = 0       # 连续吃了几次血还没回上去（"站着吃挨打"的收敛计数，见 unsafe_reason）
        self._guard_on = False         # 🛡️ C# 侧贴身自动防御是否开上了（见 guard_on / retaliate_if_hit）
        # 🍽️ 2026-09-20 恒：自定义吃食 —— **点名 + 优先级**（`--food-hp`/`--food-sta`）。
        #    空 = 不点名（走"自动挑"，见 eat_if_needed）。
        #    为什么要有它：自动挑按"回血量×10"打分，**会把想留着卖的山羊奶酪吃了**；
        #    点了名就只在点名的几样里挑，靠前的先吃。
        #    ⚠️ 2026-10-03 恒把这条规矩说死了：「**有点名只吃点名，吃完了也不吃别的；不点名才自动吃**」
        #       —— 原来第 ③ 条"整张表都没货就退回自动挑"**已作废**（那是我 09-20 自己加的保命兜底）。
        self.food_hp = []              # 回血：按优先级排的食物名列表
        self.food_sta = []             # 体力：同上
        # 🍽️ 2026-10-03 恒：`food_buff` —— **点名"现在去吃带这个效果的那份"**（如 "运气"/"钓鱼"）。
        #    判据是游戏报的 `foodBuffs` 效果文案/buff id/吃食名（见 `food_matches_buff`），不是名单。
        #    维护走 `maintain_buffs_for`：该 buff 没了/快过期就吃；点名了就不吃别的。
        self.food_buff = ""

    # ═══════════ 炸弹类型选择（黑>超级>樱桃，背包实际有才算数） ═══════════

    def choose_bomb_type(self, prefer=None):
        """按优先级挑"背包里实际有的"炸弹：prefer(显式要的，如黑)有就用，否则按 黑>超级>樱桃 回调。
        全没有→返回 ''（调用方自己降级成镐子/提示）。避免"有了超级/樱桃却只认黑而报没炸弹"。"""
        cand = (prefer or self.bomb_type)
        if cand in BOMB_NAMES and self.count_bombs(cand) > 0:
            return cand
        for b in BOMB_PRIORITY:
            if b != cand and self.count_bombs(b) > 0:
                return b
        return ""

    def absent_cause(self):
        """（已没炸弹时）给 AI 一句人话。"""
        return "背包里黑/超级/樱桃炸弹都没有了——先去买/拿炸弹再来（可 /give 作弊）"

    def blast_tiles(self, bx, by, bomb_type=None):
        """某炸弹放在 (bx,by) 爆炸覆盖的瓦片（含中心）列表。
        🔥 2026-09-06 恒 tooltip 实测 + 反编译 bombRadius：樱桃=边长7十字(缺4角)；黑=边长11方块；超级=15×15方块。"""
        bt = bomb_type or self.bomb_type
        out = []
        if bt == "Mega Bomb":
            # 超级：向上8 向左8 向右6 向下6 → 15×15 整方块（dx∈[-8,6], dy∈[-8,6]，bombRadius=7）
            for dx in range(-8, 7):
                for dy in range(-8, 7):
                    out.append((bx + dx, by + dy))
        elif bt == "Cherry Bomb":
            # 樱桃：边长7 十字(缺4角)——横臂 |dx|<=3 且 dy==0 + 竖臂 |dy|<=3 且 dx==0（13格含中心；bombRadius=3）
            for dx in range(-3, 4):
                out.append((bx + dx, by))
            for dy in range(-3, 4):
                if dy != 0:
                    out.append((bx, by + dy))
        else:
            # 黑炸弹：向左6 向上6 向右4 向下4 → 边长11 整方块（dx∈[-6,4], dy∈[-6,4]，bombRadius=5）
            for dx in range(-6, 5):
                for dy in range(-6, 5):
                    out.append((bx + dx, by + dy))
        return out

    def blast_reach(self, bomb_type=None):
        """该炸弹最大单向偏移（躲远/安全距离/防重叠粗略用）。樱桃3、黑6、超级8。"""
        return BOMB_REACH.get(bomb_type or self.bomb_type, 3)

    def plan_anchors(self, radius=14, min_covered=3, top=None):
        """扫本层岩体，按贪心算出最值得放炸弹的锚点（覆盖最多岩体的空格）。
        返回 (排序候选列表, 本层岩体数)；候选 [(count, -曼哈顿距离, ax, ay)] 按 count 降序。
        用 blast_tiles 真实爆炸形状判覆盖（樱桃十字/黑方块/超级方块形状不同）。
        2026-09-06 恒：从 bomb_plan MCP 壳下沉，壳留守 nagi_mcp_server；top=None 返回全部、否则取前 top。"""
        rocks, occupied, center = self.scan_rocks(radius)
        if not rocks:
            return [], 0
        blast = set(self.blast_tiles(0, 0))   # 以 (0,0) 算形状偏移
        cx, cy = center
        cands = []
        for ax in range(cx - radius, cx + radius + 1):
            for ay in range(cy - radius, cy + radius + 1):
                if (ax, ay) in occupied:
                    continue
                count = sum(1 for x, y, n in rocks if (x - ax, y - ay) in blast)
                if count >= min_covered:
                    cands.append((count, -(abs(ax - cx) + abs(ay - cy)), ax, ay))
        cands.sort(reverse=True)
        if top:
            cands = cands[:top]
        return cands, len(rocks)

    # ═══════════ 底层 API ═══════════

    def _get(self, ep, params=None, host=False):
        base = self.host if host else self.base
        try:
            return requests.get(f"{base}{ep}", params=params, timeout=10).json()
        except Exception:
            return {}

    def _post(self, ep, data=None, host=False):
        base = self.host if host else self.base
        try:
            return requests.post(f"{base}{ep}", json=data or {}, timeout=10).json()
        except Exception:
            return {}

    def state(self, host=False):
        return self._get("/state", host=host)

    def host_state(self):
        return self._get("/state", host=True)

    def surroundings(self, radius=14, host=False):
        return self._get("/surroundings", {"radius": radius}, host=host)

    def debris(self, host=False):
        return self._get("/debris", host=host)

    def alerts(self):
        return self._get("/alerts")

    def select(self, name):
        return self._post("/select", {"name": name})

    def use_item(self, force=False):
        return self._post("/use", {"force": force} if force else {})

    def use_tool(self, name=None):
        d = {}
        if name:
            d["name"] = name
        return self._post("/tool", d)

    def face(self, direction):
        return self._post("/face", {"direction": direction})

    def key(self, k, count=1):
        return self._post("/key", {"key": k, "count": count})

    def warp(self, location, x=5, y=5):
        return self._post("/warp", {"location": location, "x": x, "y": y})

    def position(self, x, y, check_passable=False, check_connectivity=False):
        """瞬移到 (x,y)。check_passable=True 时（炸矿用）C# 校验落点可走（墙/岩石/水拒），
        墙/孤岛格拒绝（返回 ok=false），防止 AI position 落进矿井迷宫死穴。
        check_connectivity=True 时再加**墙圈连通域**校验：目标格必须与 AI 当前格同一个墙圈胞腔，
        否则是"被墙圈死的隔区"，position 进去够不到梯子/矿就会打转卡死。"""
        d = {"x": x, "y": y}
        if check_passable:
            d["check_passable"] = True
        if check_connectivity:
            d["check_connectivity"] = True
        return self._post("/position", d)

    def position_safe(self, x, y, exact=False, check_passable=False, check_connectivity=False):
        """position 前检查地图内 + 后验证位置（站位不可走被游戏传送就放弃，防反复重试）。
        exact=True 时要求精确落在 (x,y)（梯子/楼梯站位必须精确，差1格 confirm 无效）。
        check_passable=True（炸矿用）时 C# 拒"不可走格"——本方法读返回，被拒立即放弃，
        否则落地验证只见位置被弹就误判成功，把 AI 留在墙/孤岛格。
        check_connectivity=True（炸矿用）时 C# 再校验墙圈连通域（防瞬移进隔区卡死）。"""
        s = self.state()
        loc = s.get("location", {})
        w = loc.get("mapWidth", 100)
        h = loc.get("mapHeight", 100)
        if x < 0 or y < 0 or x >= w or y >= h:
            log(f"  ⚠️ 目标 ({x},{y}) 在地图外（{w}x{h}），放弃")
            return False
        r = self.position(x, y, check_passable=check_passable, check_connectivity=check_connectivity)
        # ⚠️ position 被拒（落点不可走）→ 立即放弃，别把被弹走的位置当成功
        if not r.get("ok"):
            log(f"  ⚠️ position ({x},{y}) 被拒（不可走/孤岛），放弃")
            return False
        time.sleep(0.4)
        s = self.state()
        p = s.get("player", {})
        dx = abs(p.get("x", 0) - x) + abs(p.get("y", 0) - y)
        if dx > (0 if exact else 1):
            log(f"  ⚠️ position ({x},{y}) 后不在位（不可走被传送），放弃")
            return False
        return True

    def walk_to_coord(self, location, x, y):
        return self._post("/walk_to", {"location": location, "x": x, "y": y})

    def stop(self):
        return self._post("/stop")

    # ═══════════ 状态读取 ═══════════

    def player(self):
        s = self.state()
        return s.get("player", {})

    def host_player(self):
        s = self.host_state()
        return s.get("player", {})

    def host_location(self):
        s = self.host_state()
        return s.get("location", {}).get("name", "")

    def my_location(self):
        s = self.state()
        return s.get("location", {}).get("name", "")

    def my_mine_level(self):
        loc = self.my_location()
        return extract_mine_level(loc) or 0

    def host_mine_level(self):
        return extract_mine_level(self.host_location()) or 0

    def no_pause_on_unfocus(self):
        """关掉本进程的"失焦暂停"（后台也能走位，不抢user的焦点）。"""
        return set_pause(self.base, False)

    def restore_pause_on_unfocus(self):
        """还原"失焦暂停"（跑完调用）。"""
        return set_pause(self.base, True)

    def count_bombs(self, bomb_type=None):
        bt = bomb_type or self.bomb_type
        s = self.state()
        for item in s.get("inventory", []):
            if item.get("name") == bt:
                return item.get("stack", 0)
        return 0

    def count_all_bombs(self):
        s = self.state()
        total = 0
        for item in s.get("inventory", []):
            if item.get("name") in BOMB_NAMES:
                total += item.get("stack", 0)
        return total

    def ground_bombs(self, cache_ttl=1.5):
        """读 /bombs——当前地图**躺在地上还没爆炸**的真炸弹列表 [{x,y,bomb_type}]。
        🔥 2026-09-06 反编译：放炸弹=塞 TemporaryAnimatedSprite(bombRadius>0) 进 temporarySprites，
        爆炸后 endFunction 移除→自动清空。这比自家 _pending_bombs 记账可靠——记账会漏
        "被游戏拒绝的放置"、"房主放的炸弹"等。带短 TTL 缓存：best_bomb_anchor 嵌套循环里
        每候选格都判重叠，避免每格都打一次网络（1.5s 内复用）。"""
        now = time.time()
        cache = getattr(self, "_gb_cache", None)
        if cache is not None and now - getattr(self, "_gb_ts", 0) < cache_ttl:
            return cache
        try:
            out = self._get("/bombs").get("bombs", [])
        except Exception:
            out = []
        self._gb_cache = out
        self._gb_ts = now
        return out

    def _rock_in_ground_bomb(self, rx, ry):
        """(rx,ry) 是否落在某个真实地面未爆炸弹的爆炸覆盖范围内（敲石头排除用）。
        读 /bombs 按各弹真实爆炸形状判，防止浪费镐子敲"马上会被炸弹炸掉的石头"。"""
        try:
            for b in self.ground_bombs():
                bt = b.get("bomb_type") or "Bomb"
                if (rx, ry) in set(self.blast_tiles(b.get("x", -99), b.get("y", -99), bt)):
                    return True
        except Exception:
            pass
        return False

    def inventory_free_slots(self):
        s = self.state()
        inv = s.get("inventory", [])
        max_items = s.get("player", {}).get("maxItems", 36)
        return max_items - len([i for i in inv if i.get("name")])

    def inventory_items(self):
        s = self.state()
        return s.get("inventory", [])

    # 可吃类别（中文版游戏类别是中文：菜品/采集品/蔬菜/水果/鱼/花）
    FOOD_CATEGORIES = {"Cooking", "Vegetable", "Fruit", "Fish", "Forage", "Flower",
                       "菜品", "蔬菜", "水果", "鱼", "采集品", "花"}

    # ⚰️ 2026-10-03 删：`DRINK_BUFFS`（饮品名单）+ `BUFF_DISH_PRIORITY` / `BUFF_DRINK_PRIORITY`
    #    （buff 菜/饮优先级）+ `buff_duration()`（时长手抄表）+ `_buff_track`（自家时间记账）。
    #    理由（恒：「顺带清理那两张手抄表，有判据之后就该问游戏，别再维护名单」）：
    #    C# 现在把每件吃食**吃下去会挂什么 buff**（id/时长/本地化效果文案）直接报出来
    #    （`/state.inventory[].foodBuffs` = 游戏自己的 `Object.GetFoodOrDrinkBuffs()`），
    #    补 buff 那一支改成 `maintain_buffs_for()`：**按 buff id 对槽**，时长也用游戏报的。
    #    ⚠️ 名单认物品在本项目栽过不止一次（1.6 矿节点 ID、`Jewels Of The Sea`、`isForage`）——
    #       下次想加"某类物品的名单"之前，先问一句：**游戏自己报不报这一位？**

    # 恢复食物参考表（不再用于排序——eat_recovery 改按性价比=恢复量/价格实时算；此处仅作兜底参考/文档）
    RECOVERY_PRIORITY = ["Salad", "沙拉", "Pineapple", "菠萝",
                         "Cave Carrot", "山洞萝卜", "Spring Onion", "野葱",
                         "Common Mushroom", "普通蘑菇", "Farmer's Lunch", "农夫午餐",
                         "Carp", "鲤鱼", "Chub", "鲦鱼"]

    # buff 菜品/饮品优先级表：⚰️ 2026-10-03 已删（见类上方那条 tombstone）——
    # 补 buff 改走 `maintain_buffs_for()`：拿 C# 报的 `foodBuffs`（游戏自己的判据）对 `/buffs`。

    def detect_food(self):
        """找背包里可回血/补体力的食物列表。⚠️ 2026-09-06 复用 /state 的 edibleValue/healthRecovered
        判真实回血/体力值（老 DLL 无字段时按类别兜底、回血当0）。

        形状 = **全项目统一那一份** `(名字, 回体力, 回血, buff档)`：
          · ⚠️ 2026-10-03 改：原来是 `(名字, 回血, 性价比)`，而 `pick_food_closest_to_full`
            按 `(名字, 回体力, 回血)` 读 ⇒ 炸矿脚本里"血低"在**按性价比挑**、"体力低"在**按回血量挑**
            （同一个字段名两种口径，同族病）。性价比是 `eat_recovery` 自己那份排序的事，
            要算就在**那里**算（它自己算 eff），别硬塞进这个形状里。
          · `buff档` = `/state` 的 `foodBuffs`（游戏报的；拿不到 = 空 = "不知道"，见 `food_buffs_of`）。
        """
        s = self.state()
        inv = s.get("inventory", [])
        has_info = any("edibleValue" in (it or {}) for it in inv)
        foods = []
        seen = set()
        for item in inv:
            if not item:
                continue
            name = item.get("name", "")
            if not name or name in seen or name in BOMB_NAMES:
                continue
            cat = item.get("category", "")
            buffs = item.get("foodBuffs") or []
            if has_info:
                ed = int(item.get("edibleValue") or 0)
                hp = int(item.get("healthRecovered") or 0)
                if (ed > 0 or hp > 0) and cat in self.FOOD_CATEGORIES:
                    seen.add(name)
                    foods.append((name, ed, hp, buffs))
            elif cat in self.FOOD_CATEGORIES:
                seen.add(name)
                foods.append((name, 1, 0, buffs))
        return foods

    # ── 🏳️ 撤退触发线（恒 2026-09-19 拍板）──
    #   血量用**绝对值**不用百分比——恒："不到快死都可以跟着房主继续下"。
    #   ⚠️ 吃东西的**目标线**（eat_recovery 的 target=60%）是另一回事，别跟这条混。
    #   ⚠️ 这条同时是"被怪打到死"的根治点：老代码撤退线是百分比、且与吃食线之间有 30~60%
    #      的死区，人在里面永远判"安全"→ 一直吃、一直挨打。
    # ⚠️ 2026-09-20 恒：**20 → 35**（"20 可能太危险了，我看着心惊胆战的，加到 35 生命值撤吗？所有矿洞"）。
    #    现场：真机一趟冲层打到 **14/180** 才撤（`撤退原因: 血量 14 < 20（危险线）`）——
    #    20 这条线**成立**（确实把人捞出来了），但留给"反应过来"的余量只剩 20 点，
    #    而困难矿井里一次受击/一脚炸弹轻松 20+ ⇒ 常常是"刚判危险就没了"。
    #    35 留出约两下的缓冲，同时仍远低于 `EAT_RECOVER_MAX` 那条吃食线，不会互相打架。
    #    ⚠️ **只动这一个常量**：下矿(mine_run) 有一份**镜像**，改一处必须同步另一处（两边都改了）。
    RETREAT_HP_ABS = 35
    RETREAT_TOD = 2430
    EAT_RECOVER_MAX = 3   # 连吃这么多次仍没回上血 → 判"回不上来"，当撤退信号
    # ⏳ `eat_recovery` 吃完等回血的**上限**（轮询到真回血就提前走，不盲等满）。
    #    ⚠️ 2026-09-20 实测（恒这趟沙漠跑掐出来的）：结算发生在 **~2.5 秒**
    #       —— 手动吃一块奶酪，采样 0.6/1.1/1.6/2.1s 都还是 131，**2.7s 才变 180**。
    #       原来盲等 **2.0 秒**采样一次就当作"回没回"的判据 ⇒ **每次都在结算前读到"没回"**。
    EAT_SETTLE_TIMEOUT = 4.0

    def unsafe_reason(self):
        """返回"该撤了"的原因串（None=安全）。血/时间/**吃回不上来**三类分开报——
        原来混成一个布尔，凌晨满血会被报成"状态不足(HP<30%)"，再经 eat_if_needed 落到
        "没有食物，撤退"，误导 AI 去补食物（2026-09-19 修）。"""
        s = self.state()
        p = s.get("player", {})
        hp = p.get("health", 0)
        max_hp = p.get("maxHealth", 1)
        tod = s.get("time", {}).get("timeOfDay", 600)
        if hp <= 0:
            return "死亡"
        if hp < self.RETREAT_HP_ABS:
            return f"血量 {hp} < {self.RETREAT_HP_ABS}（危险线）"
        if self._recover_streak >= self.EAT_RECOVER_MAX:
            return f"连吃 {self._recover_streak} 次血回不上来（{hp}/{max_hp}）"
        if tod >= self.RETREAT_TOD:
            return f"时间 {tod // 100}:{tod % 100:02d} 已过 {self.RETREAT_TOD // 100} 点半"
        return None

    def is_safe(self, hp_threshold=40):
        """（兼容壳）形参 `hp_threshold` **已废弃**——撤退线改成 HP<20 绝对值（恒 2026-09-19），
        保留它只为不打断老调用方；真判据一律看 unsafe_reason()。"""
        return self.unsafe_reason() is None

    def eat_if_needed(self, hp_threshold=40, sta_threshold=EAT_STA_PCT, food_hp=None, food_sta=None):
        """按需进食。`food_hp`/`food_sta` 传**列表（靠前的先吃）**或逗号串；空 = 不点名。

        🕐 恒的两版规矩（**新版覆盖旧版**，别照旧注释办事）：

        2026-09-20（① ② 仍然有效）：
          ① **点名就在点名的里挑**：血低 → 只看 `food_hp` 那张表；体力低 → 只看 `food_sta`。
             两张表分开是有意的 —— 同一个顺序表对两种需求不可能都对（奶酪补血、沙拉补体力，
             合成一条「奶酪,沙拉」时体力低会先把奶酪吃了）。
          ② **列表内降级**：表里靠前的没货就试下一项（这才是"优先级"的意义）。

        2026-10-03（恒原话：「**有点名只吃点名，吃完了也不吃别的；不点名才自动吃**」）：
          ③ **点名 = 白名单**。点名的几样没了/吃不上 ⇒ **报一句就不吃了**，
             ~~退回自动挑~~ **作废**（那条"保命优先"的兜底是我 09-20 自己加的，恒现在不要）。
          ④ **不点名才自动挑** —— 挑的时候**效果食物除外**（带 buff 的那份不动，
             想吃就 `food_buff` 点名）。
          ⚠️ **别把"没吃上"改成静默**：那样"我明明点名了"和"点名根本没生效"
             在日志里长得一模一样（同族教训：`/passable_rect` 静默无视 location）。
        """
        hp_list = parse_food_list(food_hp) if food_hp is not None else list(self.food_hp)
        sta_list = parse_food_list(food_sta) if food_sta is not None else list(self.food_sta)

        foods = self.detect_food()
        if not foods:
            return False
        s = self.state()
        p = s.get("player", {})
        hp = p.get("health", 0)
        max_hp = p.get("maxHealth", 1)
        sta = p.get("stamina", 0)
        max_sta = p.get("maxStamina", 1)
        need = (max_hp > 0 and hp / max_hp * 100 < EAT_HP_PCT) or \
               (max_sta > 0 and sta / max_sta * 100 < sta_threshold)
        if not need:
            return False
        hp_pct = (hp / max_hp * 100) if max_hp > 0 else 100
        sta_pct = (sta / max_sta * 100) if max_sta > 0 else 100

        # ── ① 点名（白名单）──
        if hp_list or sta_list:
            hp_low = hp_pct < EAT_HP_PCT
            want = hp_list if hp_low else sta_list
            label = "回血" if hp_low else "体力"
            pick = pick_food_by_priority(want, {f[0] for f in foods})
            if pick:
                try:
                    if self.eat(pick):
                        vals = next((f for f in foods if f[0] == pick), (pick, 0, 0, []))
                        log(f"  🍽️ 吃了 {pick}（点名·{label} 体{vals[1]} 血{vals[2]}）")
                        return True
                except Exception as e:
                    log(f"  ⚠️ 吃 {pick} 出错：{e}")
            # ── ③ 点名白名单：没了就**不吃了**，不换别的（恒 2026-10-03）──
            if want:
                log(f"  ⚠️ 点名的{label}食物一个都没吃上（表：{','.join(want)}）"
                    f" —— 按「有点名只吃点名」**不吃别的**")
            elif hp_low:
                log(f"  ⚠️ 这次需要{label}，但 `food_hp` 没点名 —— 按「不点名才自动吃」去自动挑")
            else:
                log(f"  ⚠️ 这次需要{label}，但 `food_sta` 没点名 —— 按「不点名才自动吃」去自动挑")
            # 一侧点名、另一侧没点名时：**没点名的那一侧照样走自动挑**（两张表两条线）
            if want:
                return False   # 点了名而没吃上 ⇒ 到此为止，不换别的
            # 触发的那一侧**没点名** ⇒ 按「不点名才自动吃」继续往下走自动挑

        # ── ④ 自动挑：🍽️ 2026-10-03 恒 —— 「**离补满最接近**」（见 `pick_food_closest_to_full`：
        #    够补的挑**最小**、都不够挑**最大**）。老逻辑是"回血最多优先"（`hpv*10` 主导）
        #    ⇒ **满 180 缺 20 也掏奶酪** —— 恒举的"20/40 该吃韭葱+20 而不是奶酪+60"就是这个浪费。
        #    ⚠️ 老规矩保留：血低时**回血为 0 的绝不选**（"绝不拿纯体力咖啡保命"）；
        #       并且**效果食物除外**（恒那句"效果食物除外"，判据=游戏报的 `foodBuffs`）。
        _need_hp = max(0, max_hp - hp) if hp_pct < EAT_HP_PCT else 0
        _need_sta = max(0, max_sta - sta) if (hp_pct >= EAT_HP_PCT and sta_pct < sta_threshold) else 0
        pick = pick_food_closest_to_full(foods, _need_hp, _need_sta)
        if not pick:
            note = effect_food_note(foods)
            if note:
                log("  " + note)
            return False
        try:
            if self.eat(pick):
                vals = next((f for f in foods if f[0] == pick), (pick, 0, 0, []))
                log(f"  🍽️ 吃了 {pick}（体{vals[1]} 血{vals[2]}；缺口 血{_need_hp}/体{_need_sta}）")
                return True
        except Exception as e:
            log(f"  ⚠️ 吃 {pick} 出错：{e}")
        return False

    def eat(self, name=None):
        """吃当前选中（或指定）食物，返回是否成功。"""
        if name:
            self.select(name)
            time.sleep(0.2)
        try:
            r = self._post("/eat")
            return r.get("ok", False)
        except Exception:
            return False

    def count_item(self, name):
        for i in self.state().get("inventory", []):
            if i.get("name") == name:
                return i.get("stack", 0)
        return 0

    def check_buffs(self):
        """读 `/buffs`（按 `(source, id)` 去重）→ `[{id, source, displayName, seconds}]`。

        ⚠️ 补 buff 的判据**已经不在这里**了（`maintain_buffs_for` 直接读 `id`/`seconds` 对槽）——
        本方法只留给"想知道自己挂着什么"的调用方。
        """
        try:
            r = self._get("/buffs")
        except Exception:
            return []
        out = []
        seen = set()
        for b in r.get("buffs", []):
            key = (b.get("source", ""), b.get("id", ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
        return out

    # ⚰️ `buff_duration()` 2026-10-03 删：时长是**手抄表**，而游戏在 `foodBuffs[].ms` 里
    #    已经报得明明白白（含星级 ×1.5、戒指减半这些游戏自己的修正）。

    def maintain_buffs(self, threshold=30, want=None):
        """🍽️ 补 buff —— 实际逻辑在**模块级** `maintain_buffs_for()`（`MineBot` 也要用同一份，
        两个类各抄一遍就是 2026-09-20 那个"点名在炸矿脚本里是死的"病的温床）。

        `want` = 恒的 `food_buff` 点名（效果关键字）；点名就只认那份，没有就不吃别的。"""
        return maintain_buffs_for(self, threshold=threshold, want=want)

    def heal(self):
        """作弊回满血（救急用）。IsActive 补丁修好后台冻结后，正常吃食物回血已可靠；
        只在没食物/吃完仍低血时兜底用。"""
        try:
            return self._post("/heal").get("ok", False)
        except Exception:
            return False

    def eat_recovery(self, hard=30, target=60):
        """自保（最高优先级）——优先级+性价比+星级自适应吃东西（真实吃法，2026-08-11）：
        - 吃哪份：菠萝 > 沙拉（获取容易/免费无限收）> 其他按性价比
        - 星级：同种优先吃低星（普通>银>金>铱——高星卖价贵、恢复量不变，留卖）
          性价比用"实际卖价=value×(1+0.5×星级)"算，高星自动排后
        - 阈值：HP<target(60) 主动吃，HP<hard(30) 硬兜底吃排序最前
        - 自适应血量：优先吃恢复量>=缺口的（补得满）
        - 真实吃法：eatObject 播动画 → doneEating 结算回血（不再先 /heal 作弊回满；
          IsActive 补丁修好后台冻结后动画能完整播完，回血真实生效）
        - 兜底：吃完动画仍低于 hard（吃被打断/异常）才 /heal 救急，防死
        """
        s = self.state()
        p = s.get("player", {})
        hp = p.get("health", 0)
        maxhp = p.get("maxHealth", 1)
        hp_pct = hp / maxhp * 100 if maxhp else 0
        if hp_pct >= target:
            return False
        now = time.time()
        last_eat = getattr(self, "_last_eat", 0.0)
        gap = maxhp - hp
        foods = []
        eff_of = {}          # 名字 → 性价比（排序用；**不进食物条目形状**，见 detect_food 的注释）
        for it in s.get("inventory", []):
            name = it.get("name", "")
            if not name or name in BOMB_NAMES:
                continue
            buffs = it.get("foodBuffs") or []
            if buffs:
                continue     # 🍽️ 效果食物除外（恒的规矩；判据=游戏报的 foodBuffs，不再拿名单认）
            cat = it.get("category", "")
            if name in FOOD_RECOVERY:
                pass  # 已知食物直接认（沙拉/菠萝/胡萝卜等）
            elif cat not in self.FOOD_CATEGORIES:
                continue  # 类别不对，不是食物
            elif cat in ("Forage", "采集品", "Flower", "花"):
                continue  # 采集/花类未知物品不可食（Sap/花），别浪费（2026-08-10 火山吃 Sap bug）
            q = int(it.get("quality", 0) or 0)
            # 🩹 回血量**优先用游戏给的真值**（`/state` 的 `healthRecovered` = 反编译的
            #    `healthRecoveredOnConsumption()`），只在它缺席时才退到手抄表。
            #    ⚠️ 手抄表（`FOOD_RECOVERY`）是 1.5 年代的数：只列了十几种，**且数算错**
            #    —— 1.6 是 `ceil(Edibility*2.5)+Quality*Edibility` 再 ×0.45，
            #    表里"Cheese→38"而真值 56、"Fish Taco"干脆没有（于是被当成默认 15）。
            #    后果不只是显示错：`eff = hp_rec / 卖价` 是**排序依据**，用它挑"补得满的"会挑错人。
            #    （同族教训：`ModEntry.cs` 状态条也是同一套 1.5 老公式 —— 那份要重启游戏才改得动。）
            hp_rec = it.get("healthRecovered")
            if hp_rec is None:
                hp_rec = FOOD_RECOVERY.get(name, (30, 15))[1]   # 拿不到真值才用手抄表（未知食物默认15）
            hp_rec = int(hp_rec)
            value = max(int(it.get("value", 0) or 0), 1)
            eff = hp_rec / (value * (1 + 0.5 * q))          # 含星级：高星实际卖价贵→性价比低→留卖
            # ⚠️ 2026-10-03：条目形状跟全项目统一成 `(名字, 回体力, 回血)`，性价比**另存 eff_of**
            #    （原来把它塞在 `f[2]` 上 ⇒ 跟 `pick_food_closest_to_full` 读的"回血"撞车）。
            sta_rec = int(it.get("edibleValue") or 0)
            foods.append((name, sta_rec, hp_rec))
            eff_of[name] = eff
        # 吃东西冷却：动画 2 秒播完效果才生效，吃完 3 秒内不重复吃（防连续炫）
        if now - last_eat < 3.0:
            return False
        # 背包没食物可吃：血线危险(<hard)才 /heal 救急（没东西吃就作弊一回保命）
        if not foods:
            if hp_pct < hard:
                try:
                    self.heal()
                    log("  🚑 背包没食物，/heal 救急")
                except Exception:
                    pass
            return False
        # 排序：菠萝(-2) > 沙拉(-1) > 其他(0)，同级内性价比高（低星）在前
        def rank(f):
            n = f[0]
            if n in ("Pineapple", "菠萝"):
                priority = 2
            elif n in ("Salad", "沙拉"):
                priority = 1
            else:
                priority = 0
            return (-priority, -eff_of.get(n, 0.0))
        foods.sort(key=rank)
        # ── ① 点名（白名单）2026-09-20 恒拍板，2026-10-03 改成**不兜底**（与 `eat_if_needed` 同一套）──
        #    ⚠️ 原来这里**根本不读 `self.food_hp`**：恒点名「鱼肉卷,奶酪」，真机日志却是
        #       `🍽️ 自保吃 Cheese(回38) HP 42%` —— 鱼肉卷 20 个**一个没动**，点名在三个炸矿
        #       脚本里是**死的**。根因：`bomb_*` 的血线自保走的是**本函数**（`bomb_mine.py:458`），
        #       不是 `eat_if_needed`；我当初只改了后者。
        #       通式教训：**给消费方写了分支 ≠ 消费方拿得到数据**（同族：`/state` 瘦/`/menu` 详）。
        #    · 点名命中 → 就吃它（不再走"补得满/菠萝优先"那套排序）
        #    · 点名全没货 → **吭一声就不吃了**（恒 2026-10-03：「有点名只吃点名，吃完了也不吃别的」）
        named = parse_food_list(self.food_hp) if getattr(self, "food_hp", None) else []
        chosen = None
        if named:
            picked = pick_food_by_priority(named, {f[0] for f in foods})
            if picked:
                chosen = picked
            else:
                log(f"  ⚠️ 点名的回血食物一个都没吃上（表：{','.join(named)}）"
                    f" —— 按「有点名只吃点名」**不吃别的**")
                return False
        # ② 没点名：自适应血量——优先吃补得满的；硬兜底/补不满吃排序最前
        if chosen is None:
            if hp_pct >= hard:
                for f in foods:
                    if f[2] >= gap:          # 🩹 回血在 f[2]（形状统一后；原来读的是 f[1]=回血）
                        chosen = f[0]
                        break
            if chosen is None:
                chosen = foods[0][0]
        # 拟人吃：先停下（边走边吃动画不生效），再吃 + 等 2 秒动画播完（回血在 doneEating 结算）
        try:
            for _ in range(5):
                s = self.state()
                if not s.get("player", {}).get("isMoving", True):
                    break
                time.sleep(0.3)
        except Exception:
            pass
        # 🔴 2026-10-03：**先停下走位队列**再吃。`/walk_to` 是异步的（排队走、当次就返回），
        #    所以"边走边吃"是真的会发生的：队列继续推人走 ⇒ 吃动画被覆盖 ⇒ `doneEating` 不跑 ⇒
        #    白吃（恒真机「因为边走边吃吃不上」）。C# 侧 `/eat` 现在也会自己 `ClearMovementState`，
        #    这里再显式停一次（+ 等真的不动了），双保险。
        try:
            self._post("/stop")
        except Exception:
            pass
        for _ in range(6):
            try:
                if not (self.state().get("player") or {}).get("isMoving", False):
                    break
            except Exception:
                break
            time.sleep(0.2)

        ate_ok = False
        try:
            ate_ok = bool(self.eat(chosen))
        except Exception:
            ate_ok = False
        self._last_eat = time.time()
        _hp_show = next((f[2] for f in foods if f[0] == chosen), None)
        if _hp_show is None:      # 拿不到真值才退回手抄表（显示用，别拿它当判据）
            _hp_show = FOOD_RECOVERY.get(chosen, (0, 0))[1]
        log(f"  🍽️ 自保吃 {chosen}(回{_hp_show}) HP {hp_pct:.0f}%")
        # ⏳ 等回血落地：由 eatObject→doneEating 结算。
        # ⚠️ 2026-09-20 改（恒这趟沙漠跑掐出来的，代价=整趟在第 136 层被误判撤退）：
        #    原来这里 `time.sleep(2.0)` 后**只采样一次**，那一枪就定"回没回"。而实测结算在 **~2.5s**
        #    （手动吃奶酪：0.6/1.1/1.6/2.1s 都还是 131，2.7s 才 180）⇒ **每一枪都打在结算之前**
        #    ⇒ `_recover_streak` **必然** 1→2→3 ⇒ `unsafe_reason()` 判"回不上来" ⇒ 撤退。
        #    真机现场：103/180（57%）撤的，而血其实一直在回（76→103）。
        #    注意它**测的从来不是"回不上来"，是"我采样太早"** —— 判据错在尺子上，不在世界。
        #    改：轮询到真回血为止（真回了立刻走，常态反而比盲等快）。
        hp2, max2 = hp, maxhp
        deadline = time.time() + self.EAT_SETTLE_TIMEOUT
        while time.time() < deadline:
            time.sleep(0.25)
            try:
                p2 = self.state().get("player", {})
                hp2 = p2.get("health", hp2)
                max2 = p2.get("maxHealth", max2) or 1
            except Exception:
                continue
            if hp2 > hp + 1:
                break
        # 兜底：吃完仍低于 hard → /heal 救急（吃动画被打断/异常时不至于死）
        try:
            # 🩸 收敛计数（2026-09-19 新增，2026-09-20 修）：**等满 EAT_SETTLE_TIMEOUT 仍**一点血
            #    都没回（+1 容差）才计数——典型就是"边吃边挨打"。连中 EAT_RECOVER_MAX 次 ⇒
            #    unsafe_reason() 判"回不上来" ⇒ 主循环撤退。
            #    **这条是砍掉"站着吃挨打直到死"的关键**（光有 HP<35 只解决"何时该撤"，
            #    解决不了"撤之前一直在原地吃"）。所以它必须准 —— 误报的代价是**好端端把整趟掐停**。
            if hp2 <= hp + 1:
                # 🔴 2026-10-03：**「没吃上」和「吃了没回血」必须分开**。老版不管 `self.eat()` 成没成
                #    都照样 `_recover_streak += 1` ⇒ 动画被打断（边走边吃）时，连中 3 次就判
                #    「连吃 3 次血回不上来」⇒ **假撤退**（恒真机：「因为边走边吃吃不上，于是好像撤了
                #    （也没到撤退线啊？）」—— 血还在 30+ 就撤了，就是这个）。
                #    ⇒ 只有**真吃上了**（C# `/eat` 现在会等 `doneEating` 结算并如实回 ok）才计数；
                #      没吃上就不记、也不动 `_recover_streak`（下一步会重试）。
                if not ate_ok:
                    log(f"  ⚠️ 这次没吃上（`/eat` 没结算：动画被打断？）—— **不记入「回不上来」**"
                        f"（仍是 {self._recover_streak}/{self.EAT_RECOVER_MAX}），会重试")
                else:
                    self._recover_streak += 1
                    log(f"  🩸 吃完血没回（{hp}→{hp2}，等满 {self.EAT_SETTLE_TIMEOUT:.0f}s），"
                        f"连 {self._recover_streak}/{self.EAT_RECOVER_MAX} 次")
            else:
                self._recover_streak = 0
            if max2 and hp2 / max2 * 100 < hard:
                self.heal()
                log(f"  🚑 吃完仍低血({hp2 / max2 * 100:.0f}%)，/heal 兜底")
        except Exception:
            pass
        return True

    # ═══════════ 矿洞扫描 ═══════════

    def scan_rocks(self, radius=14):
        """扫周围，返回 (rocks, occupied, center)
        rocks: [(x, y, name)] 可炸岩体
        occupied: {(x,y)} 不能站/不能放炸弹的格（石头/树/杂物/不可走）
        """
        data = self.surroundings(radius)
        cx = data.get("center", {}).get("x", 0)
        cy = data.get("center", {}).get("y", 0)
        rocks = []
        occupied = set()
        for t in data.get("tiles", []):
            x, y = t["x"], t["y"]
            if not t.get("passable", True):
                occupied.add((x, y))
            obj = t.get("object")
            if obj:
                occupied.add((x, y))
                if is_rock(obj):
                    # 🥚 卡利科三花蛋矿（沙漠节骷髅洞 objId=CalicoEggStone_1，Name 报 Stone）→ 覆盖名让 rock_score 高优先级
                    oid = str(t.get("objId") or "")
                    rock_name = "CalicoEggStone" if oid.endswith("CalicoEggStone_1") else obj
                    rocks.append((x, y, rock_name))
            terrain = t.get("terrain")
            if terrain and terrain != "HoeDirt":
                occupied.add((x, y))
            if t.get("resource"):
                occupied.add((x, y))
        return rocks, occupied, (cx, cy)

    # 火山矿石 objId → 价值分（user 2026-08-10 逐一 dump 实测确认）：
    #   844=Cinder Shard Stone(晶石) / 850=Iron Stone(铁) / 849=Copper Stone(铜)
    #   VolcanoGoldNode=金 / 845-847=普通石头(分5)
    # ⚠️ VolcanoCoalNode0 不是煤——user 2026-08-10 确认是火山**机关**（名字报石头骗了 dump，API 能看见机关）
    # ⚠️ 844 在矿井是普通石头变体，必须按火山地点门控。
    VOLCANO_ORE_SCORES = {
        "844": 70,                # 火山晶石矿（Cinder Shard Stone）
        "VolcanoGoldNode": 60,    # 火山金矿（字符串 ID）
        "850": 40,                # 火山铁矿（Iron Stone）
        "849": 30,                # 火山铜矿（Copper Stone）
        "VolcanoIronNode": 40,    # 火山铁矿（字符串 ID 兜底）
        "VolcanoCopperNode": 30,  # 火山铜矿（字符串 ID 兜底）
    }

    def volcano_ore_scores(self, radius=14):
        """火山矿石坐标 → 价值分 dict（非火山返回空）。
        镐子贪心/炸矿优先用：user 2026-08-10 反馈"没见火山晶石突出优先级"。"""
        loc = self.my_location() or ""
        if "Volcano" not in loc:
            return {}
        try:
            data = self.surroundings(radius)
        except Exception:
            return {}
        out = {}
        for t in data.get("tiles", []):
            oid = (t.get("objId") or "").replace("(O)", "").replace("(BC)", "")
            if oid in self.VOLCANO_ORE_SCORES and is_rock(t.get("object")):
                out[(t["x"], t["y"])] = self.VOLCANO_ORE_SCORES[oid]
        return out

    # 采集物（空闲时顺手捡，像真人收集矿物）：熔岩菇 + 龙牙 + 矿井宝石（地晶/泪晶/火水晶）
    FORAGE_ITEMS = {
        "Magma Cap": 60, "熔岩菇": 60,       # 火山熔岩菇，能吃大补（user 2026-08-10）
        "Dragon Tooth": 100, "龙牙": 100,    # 火山岩浆怪掉的地上掉落，附魔燃料（ID=(O)852，user 2026-08-11）
        "Earth Crystal": 50, "地晶": 50,      # 矿井宝石
        "Frozen Tear": 50, "泪晶": 50,
        "Fire Quartz": 50, "火水晶": 50,
        "Diamond": 120, "钻石": 120,
        "Emerald": 70, "绿宝石": 70,
        "Aquamarine": 65, "海蓝宝石": 65,
    }

    def pick_forage_nearby(self, radius=10, max_items=3):
        """捡周围采集物（非石头 object：熔岩菇/地晶/泪晶/火水晶等），走上去自动拾取。
        空闲时用（没矿簇没怪时顺手捡）。返回是否捡了。"""
        try:
            data = self.surroundings(radius)
        except Exception:
            return False
        s = self.state()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        candidates = []
        for t in data.get("tiles", []):
            name = t.get("object") or ""
            if not name or is_rock(name):
                continue  # 石头/矿节点跳过（那是炸/敲的，不是捡的）
            if "Barrel" in name or "Barrel" in (t.get("objId") or ""):
                continue  # 爆炸桶跳过
            oid = (t.get("objId") or "").replace("(O)", "").replace("(BC)", "")
            val = self.FORAGE_ITEMS.get(name) or self.FORAGE_ITEMS.get(oid)
            if val is None:
                continue
            candidates.append((t["x"], t["y"], name, val))
        if not candidates:
            return False
        candidates.sort(key=lambda c: (-c[3], abs(c[0] - px) + abs(c[1] - py)))
        loc = self.my_location()
        picked = 0
        for x, y, name, _ in candidates[:max_items]:
            if self.inventory_free_slots() <= 0:
                break
            stood = False
            for dx, dy in ((0, 1), (1, 0), (-1, 0), (0, -1)):
                if self.position_safe(x + dx, y + dy):
                    stood = True
                    break
            if not stood:
                continue
            time.sleep(0.2)
            try:
                self.walk_to_coord(loc, x, y)  # 走最后一步踩上自动拾取
                time.sleep(0.4)
            except Exception:
                pass
            picked += 1
            log(f"  🍄 捡 {name} ({x},{y})")
        return picked > 0

    def scan_high_value(self, radius=8, around=None):
        """扫高价值矿石（协同模式用）
        around=(x,y) 时只看以该点为中心的框
        """
        rocks, occupied, (cx, cy) = self.scan_rocks(radius)
        out = []
        for x, y, name in rocks:
            if name in HIGH_VALUE_ORES:
                if around:
                    if abs(x - around[0]) + abs(y - around[1]) > radius:
                        continue
                out.append((x, y, name))
        return out

    # ═══════════ 贪心锚点 ═══════════

    def _prune_pending_bombs(self, ttl=6.0):
        """清理已爆炸的炸弹记录（普通炸弹 ~3 秒爆，6 秒后清掉）。"""
        now = time.time()
        self._pending_bombs = [(bx, by, t, bt) for bx, by, t, bt in self._pending_bombs
                               if now - t < ttl]

    def _pending_overlaps(self, x, y, bomb_type=None):
        """(x,y) 放炸弹是否会跟某个未爆炸炸弹的爆炸区**重叠**（防重叠浪费）。
        精确按各自 blast_tiles 形状判交集——樱桃十字/黑方块/超级方块两两不同，不能用单一半径近似。
        除自家 _pending_bombs 记账，还会读 /bombs 叠加**真实躺地上的炸弹**（记账漏的：被拒放置/房主放的）。"""
        bt = bomb_type or self.bomb_type
        self._prune_pending_bombs()
        mine = set(self.blast_tiles(x, y, bt))
        for bx, by, _, pbt in self._pending_bombs:
            if mine & set(self.blast_tiles(bx, by, pbt)):
                return True
        # 真实地面炸弹（反编译 /bombs）：与任一颗的爆炸形状相交就跳过
        try:
            for b in self.ground_bombs():
                gbt = b.get("bomb_type") or "Bomb"
                if mine & set(self.blast_tiles(b.get("x", -99), b.get("y", -99), gbt)):
                    return True
        except Exception:
            pass
        return False

    def _pending_near(self, x, y, radius):
        """是否在某个未爆炸炸弹的 radius（曼哈顿）内（粗略，钻石近似/敲石头避开用）。"""
        self._prune_pending_bombs()
        return any(abs(x - bx) + abs(y - by) <= radius for bx, by, _, _ in self._pending_bombs)

    def best_bomb_anchor(self, radius=14, bomb_radius=None, min_covered=3, max_dist=6):
        """贪心：找最值得放炸弹的空格（矿石价值分优先 + 覆盖数 + 距离）。
        头骨矿洞等有高价值矿的地方，优先炸铱/钻石/宝石簇。
        返回 (ax, ay, covered_count, rocks) 或 None
        """
        # 🔥 2026-09-06 恒：覆盖/防重叠按 bomb_type 的**真实爆炸形状**算（樱桃十字/黑钻石/超级方块）。
        #   显式传 bomb_radius（_bomb_selftest 用）→ 退回钻石近似。
        use_shape = bomb_radius is None
        eff_type = self.bomb_type
        radius_override = bomb_radius

        def _blast(ax, ay):
            if use_shape:
                return set(self.blast_tiles(ax, ay, eff_type))
            R = radius_override
            return {(ax + dx, ay + dy) for dx in range(-R, R + 1)
                    for dy in range(-R, R + 1) if abs(dx) + abs(dy) <= R}

        rocks, occupied, (cx, cy) = self.scan_rocks(radius)
        # 怪物格不能放炸弹（surroundings 的 monsters 位置）
        try:
            data = self.surroundings(radius)
            for m in data.get("monsters", []):
                occupied.add((m.get("x", -1), m.get("y", -1)))
        except Exception:
            pass
        # 梯子/竖井/入口格不能放炸弹（surroundings 识别不到它们，放上面会空炸/误触入口）
        try:
            r = self._get("/ladder")
            if r.get("ok"):
                for k in ("ladder", "shaft", "entrance"):
                    cand = r.get(k)
                    if cand and cand.get("x") is not None and cand.get("x") >= 0:
                        occupied.add((cand["x"], cand["y"]))
        except Exception:
            pass
        if not rocks:
            return None
        self._prune_pending_bombs()
        best = None
        best_score = -1
        best_count = 0
        best_dist = 10 ** 9
        for ax in range(cx - radius, cx + radius + 1):
            for ay in range(cy - radius, cy + radius + 1):
                if (ax, ay) in occupied or (ax, ay) in self._bombed_anchors:
                    continue
                # 防重叠：精确判两颗炸弹爆炸区不相交（避免浪费）；钻石兜底路径用半径*2 近似
                if use_shape:
                    if self._pending_overlaps(ax, ay, eff_type):
                        continue
                else:
                    if self._pending_near(ax, ay, radius_override * 2):
                        continue
                dist = abs(ax - cx) + abs(ay - cy)
                if dist > max_dist:  # 锚点别离 AI 太远（避免长距离瞬移/空炸）
                    continue
                blast = _blast(ax, ay)
                # 覆盖的岩体
                inside = [(x, y, n) for x, y, n in rocks
                          if (x, y) in blast
                          and (x, y) not in self._bombed_rocks]
                # 数量门槛：普通簇要 ≥min_covered；但高价值宝石簇（≥3块铱/钻/宝石）网开一面——
                # 1钻石+2铱 或 3铱 紧挨就值得炸，哪怕总数不够5块（评分里宝石分优先自然胜出）
                high_value = sum(1 for _, _, n in inside if rock_score(n) >= 80)
                if len(inside) < min_covered and high_value < 3:
                    continue
                # 矿石价值分优先，其次覆盖数，再其次离得近
                score = sum(rock_score(n) for _, _, n in inside)
                count = len(inside)
                if (score > best_score or
                        (score == best_score and count > best_count) or
                        (score == best_score and count == best_count and dist < best_dist)):
                    best_score = score
                    best_count = count
                    best = (ax, ay)
                    best_dist = dist
        if best is None:
            return None
        return best[0], best[1], best_count, rocks

    def find_stand_tile(self, tx, ty, occupied):
        """找目标旁的水平/垂直紧邻可站位（4方向）。对角站位会导致面向偏差放偏炸弹，不用。"""
        for dx, dy in [(1, 0), (0, -1), (0, 1), (-1, 0)]:
            nx, ny = tx + dx, ty + dy
            if (nx, ny) not in occupied:
                return nx, ny, dx, dy
        return None, None, 0, 0

    def find_safe_spot(self, anchor, min_dist, radius=16, occupied=None, center=None,
                       need_free_neighbor=False):
        """找一个距 anchor 至少 min_dist 的可站格（放完炸弹躲远 / 换落脚点）。
        - 只挑地图内格子（position 出界会被游戏弹回）＋ 永远排除当前层入口（防 confirm 误触入口梯子）
        - need_free_neighbor=True 时要求该格至少有一个空的紧邻格（落脚后能放楼梯/走动）"""
        if occupied is None or center is None:
            rocks, occ, ctr = self.scan_rocks(radius)
            if occupied is None:
                occupied = occ
            if center is None:
                center = ctr
        occupied = set(occupied)
        ent = self.find_entrance()
        if ent:
            occupied.add(ent)  # 入口梯子格不落脚（防误触出去）
        s = self.state()
        loc = s.get("location", {})
        mw = loc.get("mapWidth", 100)
        mh = loc.get("mapHeight", 100)
        cx, cy = center
        best = None
        best_d = -1
        fallback = None
        fallback_d = -1
        for ax in range(max(0, cx - radius), min(mw - 1, cx + radius) + 1):
            for ay in range(max(0, cy - radius), min(mh - 1, cy + radius) + 1):
                if (ax, ay) in occupied:
                    continue
                if need_free_neighbor and not any(
                        (ax + dx, ay + dy) not in occupied
                        for dx, dy in ((1, 0), (0, -1), (0, 1), (-1, 0))):
                    continue
                d = abs(ax - anchor[0]) + abs(ay - anchor[1])
                if d >= min_dist and d > best_d:
                    best_d = d
                    best = (ax, ay)
                elif d > fallback_d:
                    fallback_d = d
                    fallback = (ax, ay)
        # 没有够远的格（小洞穴）→ 退而求其次，能站多远站多远
        return best if best is not None else fallback

    # ═══════════ 清路（敲挡路石头） ═══════════

    def rock_at(self, x, y, radius=6):
        """检查 (x,y) 是否还有可敲岩体（逐下检测）。"""
        rocks, _, _ = self.scan_rocks(radius)
        return any(ax == x and ay == y for ax, ay, _ in rocks)

    def smash_rock(self, x, y, max_swings=6):
        """position 到石头旁→面向→镐子敲直到碎（逐下检测）。返回是否敲碎。"""
        rocks, occupied, _ = self.scan_rocks(14)
        sx, sy, dx, dy = self.find_stand_tile(x, y, occupied)
        if sx is None:
            return False
        # 🔥 2026-09-06 恒：敲石站位走 check_passable+check_connectivity（墙格拒瞬移 + 墙圈隔区拒瞬移防卡死）
        if not self.position_safe(sx, sy, check_passable=True, check_connectivity=True):
            return False
        time.sleep(0.3)
        self.face_toward(x, y)
        # 找背包实际镐子名（如 Iridium Pickaxe——select "Pickaxe" 精确名匹配不到）
        pick_name = None
        s0 = self.state()
        for it in s0.get("inventory", []):
            if "Pickaxe" in it.get("name", ""):
                pick_name = it.get("name")
                break
        if not pick_name:
            return False
        self.select(pick_name)
        for _ in range(max_swings):
            self.use_tool()  # 用当前选中的镐子
            time.sleep(0.45)
            if not self.rock_at(x, y):
                self.select(self.bomb_type)
                return True
        self.select(self.bomb_type)
        return False

    def smash_nearby_rocks(self, max_n=3, radius=8, ores_only=False):
        """镐子贪心：优先敲高价值矿（放射/铱/钻/宝石/火山晶石），其次最近的石头。
        ores_only=True：只敲矿（火山矿石/rock_score>5），不敲普通石头——user 2026-08-10 定的
        空闲优先级"贪心敲矿→捡东西→敲普通石头"的第一档。
        无梯子时刷梯子/攒石头，顺带把散的高价值矿捡走。返回是否敲了。
        跳过还没爆炸炸弹范围内的石头（浪费——马上会被炸掉）。"""
        bradius = self.blast_reach()
        knocked = 0
        for _ in range(max_n):
            rocks, _, _ = self.scan_rocks(radius)
            if not rocks:
                break
            # 剔除未爆炸炸弹覆盖范围内的石头（被炸是迟早的事，别浪费镐击）
            # 除自家记账外，还叠加 /bombs 读到的真实地面炸弹——两者都排除
            if self._pending_bombs or self.ground_bombs():
                self._prune_pending_bombs()
                rocks = [r for r in rocks
                         if not self._pending_near(r[0], r[1], bradius)
                         and not self._rock_in_ground_bomb(r[0], r[1])]
                if not rocks:
                    break
            s = self.state()
            px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
            # 火山矿石价值优先（晶石70>金60>铁40>铜30>普通0），再 rock_score，同价值就近
            ore_scores = self.volcano_ore_scores(radius)
            if ores_only:
                # 只留真矿（火山矿石 或 rock_score>5 的矿节点），普通石头(5)过滤掉
                rocks = [r for r in rocks
                         if ore_scores.get((r[0], r[1]), 0) > 0 or rock_score(r[2]) > 5]
                if not rocks:
                    return False  # 没矿可敲，让给捡采集物/敲普通石头
            rocks.sort(key=lambda r: (-ore_scores.get((r[0], r[1]), 0),
                                      -rock_score(r[2]),
                                      abs(r[0] - px) + abs(r[1] - py)))
            x, y, name = rocks[0]
            log(f"  ⛏️ 敲 {name} ({x},{y})")
            if not self.smash_rock(x, y):
                break
            knocked += 1
        return knocked > 0

    def smash_around_host(self, max_n=3, ring=3):
        """敲user身边 ring 圈的石头（帮user开路）。火山不爆梯子，普通石头只在user身边敲才有用
        （user 2026-08-10 定：火山空闲逻辑第3档=敲玩家身边三圈）。返回是否敲了。"""
        try:
            h = self.host_player()
        except Exception:
            return False
        hx, hy = h.get("x", 0), h.get("y", 0)
        rocks, _, _ = self.scan_rocks(ring + 2)
        near = [r for r in rocks if abs(r[0] - hx) + abs(r[1] - hy) <= ring]
        if not near:
            return False
        px, py = self.my_pos()
        # 先敲离user近的，同距离敲离 AI 近的（省走路）
        near.sort(key=lambda r: (abs(r[0] - hx) + abs(r[1] - hy),
                                 abs(r[0] - px) + abs(r[1] - py)))
        knocked = 0
        for x, y, name in near[:max_n]:
            log(f"  🪨 帮user敲 {name} ({x},{y})")
            if not self.smash_rock(x, y):
                break
            knocked += 1
        return knocked > 0

    def step_mechanisms(self, max_steps=2, radius=25, max_dist=15):
        """踩火山机关（门锁）：自然走路过去踩，路上石头挡路敲掉（walk_to_with_clear）。
        不 position 瞬移（user 2026-08-10：瞬移踩机关太开挂，要走过去）。
        机关 objId 含 VolcanoCoalNode（/surroundings 报名字'石头'，SafeObjectName 不认）。
        踩一个消失一个，踩完全部才开门。
        ⚠️ 只踩 max_dist 内的机关（骑行模式不追着满图机关跑；远处的留user）。
        返回是否踩了。"""
        if "Volcano" not in (self.my_location() or ""):
            return False
        try:
            data = self.surroundings(radius)
        except Exception:
            return False
        s = self.state()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        mechs = [(t["x"], t["y"]) for t in data.get("tiles", [])
                 if "VolcanoCoalNode" in (t.get("objId") or "").replace("(O)", "")
                 and abs(t["x"] - px) + abs(t["y"] - py) <= max_dist]
        if not mechs:
            return False
        mechs.sort(key=lambda m: abs(m[0] - px) + abs(m[1] - py))
        loc = self.my_location()
        stepped = 0
        for mx, my in mechs[:max_steps]:
            if not self.walk_to_with_clear(mx, my, max_clear=8):
                log(f"  ⚠️ 机关 ({mx},{my}) 走不到（熔岩/卡死），跳过")
                continue
            # 走最后一步踩上去（踩了就没了）
            try:
                self.walk_to_coord(loc, mx, my)
                time.sleep(0.5)
            except Exception:
                pass
            s2 = self.state()
            p2 = s2.get("player", {})
            if abs(p2.get("x", 0) - mx) + abs(p2.get("y", 0) - my) <= 1:
                log(f"  🚶 踩机关 ({mx},{my})")
                stepped += 1
        return stepped > 0

    def find_blocker(self, tx, ty, radius=6):
        """找 AI 到目标 (tx,ty) 路径上（包围盒内）最近的石头——挡路的。返回 (x,y) 或 None。"""
        s = self.state()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        rocks, _, _ = self.scan_rocks(radius)
        lo_x, hi_x = min(px, tx) - 1, max(px, tx) + 1
        lo_y, hi_y = min(py, ty) - 1, max(py, ty) + 1
        between = [(x, y, n) for x, y, n in rocks
                   if lo_x <= x <= hi_x and lo_y <= y <= hi_y]
        if not between:
            return None
        between.sort(key=lambda r: abs(r[0] - px) + abs(r[1] - py))
        return between[0][0], between[0][1]

    def walk_to_with_clear(self, tx, ty, max_clear=6):
        """走路到目标，路上有石头挡路就敲掉（不 position 瞬移穿墙）。
        用于下楼/到站位等：遇到螺旋墙挡路就一圈圈敲石头开路。
        返回是否到达目标。"""
        for _ in range(max_clear + 1):
            self.natural_walk(tx, ty, self.my_location(), walk_only=True)
            s = self.state()
            p = s.get("player", {})
            px, py = p.get("x", 0), p.get("y", 0)
            if abs(px - tx) + abs(py - ty) <= 1:
                return True  # 到了
            blocker = self.find_blocker(tx, ty)
            if blocker:
                log(f"  🪨 敲掉挡路石头 {blocker}")
                if not self.smash_rock(blocker[0], blocker[1]):
                    return False  # 敲不动，放弃
            else:
                return False  # 没石头挡但走不到（真卡了）

    def walk_to_exact(self, tx, ty, timeout=10):
        """用 walk_to_coord 精确走到 (tx,ty)（不用 position 瞬移）。
        梯子/敲石头站位/放楼梯等必须站在确切格子上的场景用。
        返回 True=精确站在(tx,ty), False=没到但可能在邻格。
        timeout 10 秒：给游戏导航足够时间（偶尔慢/绕路）。"""
        s = self.state()
        if s["player"]["x"] == tx and s["player"]["y"] == ty:
            return True
        loc = self.my_location()
        try:
            r = self.walk_to_coord(loc, tx, ty)
            if not r.get("ok"):
                return False
        except Exception:
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.state()
            p = s.get("player", {})
            if p.get("x") == tx and p.get("y") == ty:
                return True
            time.sleep(0.2)
        return False

    def select_pickaxe(self):
        """切到镐子，防 confirm 时手持炸弹放炸弹。"""
        for it in self.state().get("inventory", []):
            if "Pickaxe" in it.get("name", ""):
                self.select(it["name"])
                time.sleep(0.15)
                return True
        return False
        return False

    def face_toward(self, tx, ty):
        s = self.state()
        px, py = s["player"]["x"], s["player"]["y"]
        dx, dy = tx - px, ty - py
        if abs(dx) >= abs(dy):
            self.face(1 if dx > 0 else 3)
        else:
            self.face(2 if dy > 0 else 0)

    # ═══════════ 炸弹核心 ═══════════

    def open_treasure_chests(self):
        """宝箱层：开完所有 Chest。开箱弹 ItemGrabMenu（宝箱物品菜单）——满包领不走 → raise ManualChestFull
        停脚本交 AI 手动（menu_claim_swap/ok，恒 2026-08-23 不自动丢物）；有空位 → 循环 claim_swap(prefer空槽) 拿完再关；
        DialogueBox 则推进。开完直接走下楼逻辑，不卡死循环。"""
        data = self.surroundings(30)
        chests = [(t["x"], t["y"]) for t in data.get("tiles", []) if t.get("object") == "Chest"]
        opened = 0
        for cx, cy in chests:
            sx, sy, _, _ = self.find_stand_tile(cx, cy, set())
            if sx is None:
                sx, sy = cx, cy
            self.position(sx, sy)
            time.sleep(0.3)
            # 🔥 2026-09-07 恒：/interact 按"玩家**面向**的格子"触发，不面向目标=开不了箱（actionTriggered false）。
            #    find_stand_tile 只给站位不面向——position 后必须 face_toward(宝箱) 再 interact
            #    （城镇真机40层踩坑：stand(10,9)朝(11,9)却要开(9,9)宝箱→interact 没触发、菜单没弹）。
            self.face_toward(cx, cy)
            time.sleep(0.2)
            try:
                self._post("/interact", {"x": cx, "y": cy})
                time.sleep(1.0)
                # 处理菜单：确保完全关掉（click 隔 2 秒）再继续——残留对话框会挡后续行动
                for _ in range(4):
                    s = self.state()
                    menu = s.get("activeMenu") or {}
                    mt = menu.get("type")
                    if mt == "ItemGrabMenu":
                        loot = self.read_grab_items()
                        if not loot:
                            # 空箱（上次已领）直接关
                            self._post("/menu/click", {"button": "ok"})
                            time.sleep(2.0)
                        elif self.inventory_free_slots() <= 0:
                            # ⭐ 满包领不走 → 停脚本交 AI 手动（不自动丢物，菜单留给 AI）——恒 2026-08-23
                            log(f"  ⭐ 宝箱满包领不走（战利品: {loot}）→ 停脚本交AI手动: "
                                f"menu read 看待领取 → menu_claim_swap(替换物)领取 或 menu_click(button=ok)放弃；"
                                f"处理完重开脚本原地续层")
                            raise ManualChestFull(", ".join(loot))
                        else:
                            # 有空位：循环 claim_swap（replace="" 优先放空槽，不丢物）拿完领取侧，再关
                            while self.read_grab_items() and self.inventory_free_slots() > 0:
                                r = self._post("/menu/claim_swap", {"replace": ""})
                                if not r.get("ok"):
                                    break
                                log(f"  🎁 开箱领取: {r.get('claimed')}")
                                time.sleep(0.3)
                            self._post("/menu/click", {"button": "ok"})
                            time.sleep(2.0)
                    elif mt == "DialogueBox":
                        self._post("/menu/click", {})
                        time.sleep(2.0)
                    else:
                        break
                opened += 1
                log(f"  🎁 开宝箱 ({cx},{cy})")
            except ManualChestFull:
                raise   # ⭐ 满包停：向上抛（run_rush 接住干净停），不能被下面的 except Exception 吞掉——恒 2026-08-23
            except Exception:
                pass
            time.sleep(1.0)
        return opened

    def autodrop_cheap(self):
        """背包快满时丢低价值物腾格（开箱拿取需要空位）。"""
        try:
            s = self.state()
            inv = s.get("inventory", [])
            used = sum(1 for i in inv if i)
            max_items = s.get("player", {}).get("maxItems", 36)
            if used < max_items - 3:
                return
            for i in inv:
                if not i:
                    continue
                name = i.get("name", "")
                if name in ("Stone", "Copper Ore", "Iron Ore", "Slime", "Coal", "Weeds") \
                        or "Slingshot" in name:
                    self._post("/drop", {"name": name, "count": i.get("stack", 1)})
                    log(f"  🗑️ 开箱腾格：丢 {name}")
                    return
        except Exception:
            pass

    def inventory_free_slots(self):
        """背包空位数（拿战利品需要空位；=0 即满包）。"""
        try:
            s = self.state()
            inv = s.get("inventory", [])
            used = sum(1 for i in inv if i)
            return s.get("player", {}).get("maxItems", 36) - used
        except Exception:
            return 0

    def read_grab_items(self):
        """读当前 ItemGrabMenu 领取侧真物品名列表（/menu items=ItemsToGrabMenu.actualInventory，
        恒 2026-08-23 治本读端，非 stale inventory）。"""
        try:
            m = self._get("/menu")
            return [it.get("name") for it in (m.get("items") or []) if it.get("name")]
        except Exception:
            return []

    def place_bomb_at(self, x, y, bomb_type=None):
        """在指定格放炸弹（站在旁边→面向它→use）。返回 (ok, msg)
        炸弹放在 (x,y)，自动引爆。"""
        bt = bomb_type or self.bomb_type
        if self.count_bombs(bt) <= 0:
            return False, f"没有 {bt} 了"
        # 用大范围扫描确保 occupied 完整覆盖锚点紧邻格（碰撞检查），站位才选得准
        rocks, occupied, _ = self.scan_rocks(16)
        occupied = set(occupied)
        ent = self.find_entrance()
        if ent:
            occupied.add(ent)  # 站位不站入口格（防放炸弹/面向时误触入口梯子）
        sx, sy, dx, dy = self.find_stand_tile(x, y, occupied)
        if sx is None:
            return False, f"({x},{y}) 旁边没有可站位"
        # 🔥 2026-09-06 恒：炸矿站位走 check_passable+check_connectivity——墙/孤岛格拒瞬移 + 墙圈隔区拒瞬移防卡死
        if not self.position_safe(sx, sy, check_passable=True, check_connectivity=True):
            return False, f"站位 ({sx},{sy}) 不可走，跳过"
        time.sleep(0.3)
        # 面向目标
        if dx == 1:
            self.face(3)
        elif dx == -1:
            self.face(1)
        elif dy == 1:
            self.face(0)
        elif dy == -1:
            self.face(2)
        time.sleep(0.15)
        # 选炸弹 + 放
        self.select(bt)
        time.sleep(0.15)
        r = self.use_item()
        time.sleep(0.3)
        # ⚠️ 以 /use 返回 placed 为准：炸弹自动引爆快，surroundings 验证会因炸弹已爆而误报"没放到"（2026-08-08 实测）
        if isinstance(r, dict) and r.get("ok") and r.get("action") == "placed":
            return True, f"{bt} 放在 ({x},{y})"
        err = (r or {}).get("error", "use 失败")
        return False, f"放炸弹失败: {err}"

    def wait_explosion(self, x, y, timeout=9.0):
        """等 (x,y) 的炸弹爆炸（炸弹对象从 surroundings 消失）。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.surroundings(6)
            still = any(
                t.get("x") == x and t.get("y") == y and "Bomb" in (t.get("object") or "")
                for t in data.get("tiles", [])
            )
            if not still:
                return True
            time.sleep(0.35)
        return False

    # ═══════════ 已移除：躲炸弹+等爆炸捡矿（2026-08-09 简化，按user要求） ═══════════
    # 原因：position 瞬移安全，下一次放炸弹的落点自然离开爆炸范围；且"站着等爆炸"
    #       从未真实发生（高价值路径在 121~164 层 0 触发）。若要恢复（沙漠 220 层验证
    #       高价值矿时可能有用），在放完炸弹标记 _bombed_rocks 之后加回：
    #
    #         # 躲到安全点（防炸到自己）
    #         safe_dist = safe_dist or BOMB_RADIUS.get(self.bomb_type, 3) + 2
    #         spot = self.find_safe_spot((ax, ay), safe_dist)
    #         if spot is None or abs(spot[0] - ax) + abs(spot[1] - ay) < safe_dist:
    #             log(f"  ⚠️ 没有够远的躲避点，跳过 ({ax},{ay})")
    #             return False, "无安全躲避点", 0
    #         if not self.position_safe(spot[0], spot[1]):
    #             log(f"  ⚠️ 躲避 ({spot[0]},{spot[1]}) 没落到位")
    #         # 覆盖区高价值矿 → 等爆炸再捡
    #         if self.anchor_has_high_value(ax, ay):
    #             self.wait_explosion(ax, ay)
    #             if collect and self.has_rich_drops():
    #                 self.collect_drops(max_items=12)
    # ═══════════════════════════════════════════════════════════════════════════

    def bomb_and_collect(self, ax, ay, collect=True, safe_dist=None):
        """放一颗炸弹就完事（2026-08-09 简化：不再躲炸弹/等爆炸捡矿）。
        下一次放炸弹的 position 落点自然瞬移离开爆炸范围；高价值等爆炸路径从未触发过，一并删掉。
        collect/safe_dist 参数保留（协同模式调用传的，暂不使用）。
        返回 (ok, message, broken_estimate)"""
        _bt = self.bomb_type
        ok, msg = self.place_bomb_at(ax, ay)
        if not ok:
            self._bombed_anchors.add((ax, ay))  # 失败锚点也跳过（不可放置），防反复选死循环
            return False, msg, 0
        log(f"  💣 {msg}")
        self._bombed_anchors.add((ax, ay))  # 记下炸过的点，贪心不再选
        self._pending_bombs.append((ax, ay, time.time(), _bt))  # 记下还没爆炸的炸弹（选锚点/敲石头避开）
        # 标记爆炸覆盖的石头（防同范围重复放炸弹）；按该炸弹真实爆炸形状标（樱桃十字/黑钻石/超级方块）
        try:
            blast = set(self.blast_tiles(ax, ay, _bt))
            b_rocks, _, _ = self.scan_rocks(8)
            for rx, ry, rn in b_rocks:
                if (rx, ry) in blast:
                    self._bombed_rocks.add((rx, ry))
        except Exception:
            pass
        return True, "炸完", 0

    # ═══════════ 拾取 ═══════════

    def anchor_has_high_value(self, ax, ay, radius=4):
        """锚点爆炸范围内高价值矿：3+ 铱 或 1+ 放射性（密集铱/放射性值得站着等爆炸捡，再下楼）。
        无则放完立即走动（保持效率）。"""
        try:
            rocks, _, _ = self.scan_rocks(radius)
            iridium = 0
            radioactive = 0
            for x, y, n in rocks:
                if abs(x - ax) + abs(y - ay) <= radius:
                    if "Iridium" in n or "铱" in n:
                        iridium += 1
                    if "Radioactive" in n or "放射性" in n:
                        radioactive += 1
            return iridium >= 3 or radioactive >= 1
        except Exception:
            pass
        return False

    def has_rich_drops(self, min_iridium=2):
        """爆破区掉落有 2+ 铱矿 或 1+ 放射性矿石（鹈鹕镇高难矿井）——值得站着等爆炸/回头捡。"""
        try:
            d = self.debris()
            iridium = sum(1 for it in d.get("debris", [])
                          if "Iridium" in it.get("itemName", "") or "铱" in it.get("itemName", ""))
            radioactive = sum(1 for it in d.get("debris", [])
                              if "Radioactive" in it.get("itemName", "") or "放射性" in it.get("itemName", ""))
            return iridium >= min_iridium or radioactive >= 1
        except Exception:
            return False

    def collect_drops(self, max_items=10, radius=14):
        """按价值排序捡地上掉落（走过去自动拾取）。返回捡了多少。"""
        d = self.debris()
        items = []
        for it in d.get("debris", []):
            name = it.get("itemName", "?")
            items.append((it.get("x", 0), it.get("y", 0), name, drop_value(name)))
        items.sort(key=lambda t: -t[3])  # 高价值优先
        loc = self.my_location()
        picked = 0
        for x, y, name, _ in items[:max_items]:
            if self.inventory_free_slots() <= 0:
                log("  🎒 背包满了，先不捡")
                break
            try:
                self.natural_walk(x, y, loc, walk_only=True)  # 捡掉落纯走路（防 position 传送）
                time.sleep(0.25)
                picked += 1
            except Exception:
                pass
        if picked:
            log(f"  🎁 捡了 {picked} 个掉落")
        return picked

    def pick_valuable_drops(self, max_items=2):
        """扫一次 /debris 只捡必捡物（银河之魂/五彩碎片/放射锭/放射矿/铱锭/铱矿）。
        position 到掉落物旁（不穿复杂地形，可靠），再走一步踩上去自动拾取。
        每层下楼前调用一次（~1 次扫描/层，成本低）。返回捡了几个。"""
        try:
            d = self.debris()
        except Exception:
            return 0
        items = [(it.get("x", 0), it.get("y", 0), it.get("itemName", "?"),
                  drop_value(it.get("itemName", "?")))
                 for it in d.get("debris", [])
                 if any(k in (it.get("itemName") or "") for k in VALUABLE_KEYWORDS)]
        if not items:
            return 0
        items.sort(key=lambda t: -t[3])  # 高价值优先
        loc = self.my_location()
        picked = 0
        for x, y, name, _ in items[:max_items]:
            if self.inventory_free_slots() <= 0:
                break
            # position 到掉落物旁边（可靠，不穿地形），再走一步踩上自动拾取
            stood = False
            for dx, dy in ((0, 1), (1, 0), (-1, 0), (0, -1)):
                if self.position_safe(x + dx, y + dy):
                    stood = True
                    break
            if not stood:
                log(f"  ⚠️ 必捡 {name} 旁站不上，跳过")
                continue
            time.sleep(0.2)
            try:
                self.walk_to_coord(loc, x, y)  # 走最后一步踩上掉落
                time.sleep(0.4)
            except Exception:
                pass
            picked += 1
            log(f"  💎 必捡 {name} ({x},{y})")
        return picked

    # ═══════════ 矿洞内移动 ═══════════

    def natural_walk(self, tx, ty, location=None, walk_only=False):
        """近的用 /walk_to 走路，远的 position 闪现；walk_only=True 全程走路。
        捡掉落/站楼梯等必须"走过去"才触发的场景用 walk_only（position 不触发拾取）。"""
        if not location:
            location = self.my_location()
        s = self.state()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        dist = abs(tx - px) + abs(ty - py)
        if dist <= 1:
            return True
        if walk_only or (dist <= 4 and is_mine_location(location)):
            try:
                r = self.walk_to_coord(location, tx, ty)
                if r.get("ok"):
                    # 后台暂停时 isMoving=False 但走位排着队 → 按位置判断，不按 isMoving
                    # walk_only 最长等 4s 就返回，避免卡在走向目标
                    deadline = time.time() + (4 if walk_only else 15)
                    while time.time() < deadline:
                        s = self.state()
                        p = s.get("player", {})
                        if abs(p.get("x", 0) - tx) <= 1 and abs(p.get("y", 0) - ty) <= 1:
                            return True
                        time.sleep(0.25)
            except Exception:
                pass
        if walk_only:
            return True  # 走路没到位就不传，下轮再走
        # 远距离直接传（除非 walk_only 强制走路）
        self.position(tx, ty)
        return True

    def wait_arrival(self, target_map, target_x, target_y, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = self.state()
            p = s.get("player", {})
            loc = s.get("location", {}).get("name", "")
            # 位置接近才算到（后台暂停时 isMoving=False 但走位排着队）
            if loc == target_map and abs(p.get("x", 0) - target_x) <= 2 and abs(p.get("y", 0) - target_y) <= 2:
                return True
            time.sleep(0.3)
        self.stop()
        return False

    def safe_walk_to(self, x, y, location=None, timeout=30):
        if not location:
            location = self.my_location()
        try:
            r = self.walk_to_coord(location, x, y)
            if not r.get("ok"):
                return False
        except Exception:
            return False
        return self.wait_arrival(location, x, y, timeout)

    def safe_warp(self, location, x=5, y=5):
        """warp 到 (location, x, y)，**并校正落点**。

        ⚠️ 2026-09-20 恒真机：「这个**入侵层插楼梯兜底，还是会 warp 到墙外**。好在后面回来了。」
          根因不是"落点算错"，是**根本没人看过落点**：`/warp` 走的是 `Game1.warpFarmer`，
          落到哪算哪；原来这里只验证 **地点** 变没变，**从没验证那格能不能站**。
          而调用方清一色硬编码 `(5,5)`（`bomb_mine.py:447` 感染层保命那条、
          `bomb_common.py:2657` 追 user 那条、进矿 121 那条），**很多层的 (5,5) 就是墙里/地图外**。
          最要命的是它是 **`unsafe_reason()` 非空（血<35 / 超时 / 吃回不上来）时的逃生路** ——
          在最该跑的时候把人定在墙里，恒这次是"运气好后面自己回来了"。
        """
        r = self.warp(location, x, y)
        if not r.get("ok"):
            return False
        time.sleep(1.5)
        arrived = False
        for _ in range(10):
            if self.my_location() == location:
                arrived = True
                break
            time.sleep(0.5)
        if not arrived:
            return False
        self.fix_landing()   # 🩹 落点体检（原来完全没有这一步）
        return True

    def fix_landing(self, radius=4):
        """🩹 warp 之后体检落点：**这一格站得住吗**？站不住就在附近螺旋找一格站得住的位置过去。
        返回 True=把落点修好了（说明原来那格是坏的）、False=原本就是好格或修不了。

        ⚠️ 判据用 `/position {check_passable:true}`（C# 侧 `IsTilePassable`，**寻路唯一那把尺子**），
          **不用"看着像墙"之类的猜**。先试自己脚下那格：ok ⇒ 落点没问题，一次调用就收工。
        ⚠️ **修不了就原地不动**（宁报错别兜底）：找不到可走格时人至少还在原来那格，
          乱挪只会挪得更糟。
        """
        try:
            s = self.state().get("player", {})
            px, py = s.get("x", 0), s.get("y", 0)
            if self.position(px, py, check_passable=True).get("ok"):
                return False                      # 落点本来就是好的
            log(f"  🩹 warp 落点在 ({px},{py}) **站不住**，附近找可走格…")
            for d in range(1, radius + 1):
                ring = [(px + dx, py + dy)
                        for dx in range(-d, d + 1) for dy in range(-d, d + 1)
                        if max(abs(dx), abs(dy)) == d]
                for (cx, cy) in ring:
                    if self.position(cx, cy, check_passable=True).get("ok"):
                        log(f"  🩹 落点已校正 → ({cx},{cy})（原格 ({px},{py}) 不可走，偏了 {d} 格）")
                        return True
            log(f"  ⚠️ ({px},{py}) 站不住，且 {radius} 格内找不到可走格 —— 原地不动，交给上层")
            return False
        except Exception as e:
            log(f"  ⚠️ 落点体检异常：{e}")
            return False

    # ═══════════ 战斗 ═══════════

    def nearby_monsters(self, radius=6, around=None):
        """扫周围怪，返回 [(name, x, y, health, maxHealth, dist)]——maxHealth 用于判断"user在打谁"
        （health<maxHealth=被打过=user的对手）。around=(x,y) 时只取该点附近。"""
        data = self.surroundings(radius)
        cx, cy = data.get("center", {}).get("x", 0), data.get("center", {}).get("y", 0)
        out = []
        for m in data.get("monsters", []):
            d = abs(m["x"] - cx) + abs(m["y"] - cy)
            if around:
                d2 = abs(m["x"] - around[0]) + abs(m["y"] - around[1])
                if d2 > radius:
                    continue
            out.append((m["name"], m["x"], m["y"], m.get("health", 1),
                        m.get("maxHealth", 1), d))
        out.sort(key=lambda m: m[5])
        return out

    def combat_step(self, around=None):
        """贴脸就砍。around=(x,y) 时只打该点附近（协同=玩家附近）。"""
        monsters = self.nearby_monsters(4, around=around)
        if not monsters:
            return "safe"
        name, mx, my, hp, maxhp, dist = monsters[0]
        if dist <= 1:
            log(f"  ⚔️ 砍 {name} ({mx},{my}) HP={hp}")
            self.swing((mx, my), special=self.weapon_class == "hammer")
            return "fighting"
        elif dist <= 2:
            self.face_toward(mx, my)
            return "watching"
        return "safe"

    def combat_aggressive(self, around=None, engage_dist=2, kill_timeout=25, host_targets_only=False):
        """主动进攻：AI 周围 engage_dist 格（铱洒水器5x5≈2格）+ around(user) 周围格内的怪，
        追击到砍死为止。甲虫(Bug)/螃蟹(Crab)打不死跳过。战斗内每2刀查血（eat_recovery heal 兜底）。
        host_targets_only=True：只打血量不满(health<maxHealth)的怪=user正在对抗的对象，满血怪不纠缠（增援用）。
        锤子：能重砸就重砸（6s 冷却内自动降级平砍），主动反击都吃重砸。
        ⚠️ 2026-09-19 返回**状态串**，不再是布尔——原来 `True` 同时表示"有怪在打"和
        "我不安全了"，调用方分不出来，只能一律 continue 接着打（恒观察到的"被怪打到死"
        有一份就长在这）。现在：
          "fight"  = 有怪，继续打（含超时退出——怪还在但时间到）
          "clear"  = 没怪/怪清完/追太远，可以回去挖矿
          "unsafe" = 危险该撤了（unsafe_reason 判的），调用方**必须**真的撤
        """
        def _targets():
            s0 = self.state()
            px, py = s0.get("player", {}).get("x", 0), s0.get("player", {}).get("y", 0)
            ms = self.nearby_monsters(engage_dist)
            if around:
                ms += list(self.nearby_monsters(engage_dist, around=around))
            seen, out = set(), []
            for m in ms:
                name, mx, my, hp, maxhp, _ = m
                k = (mx, my)
                if k in seen or "Bug" in name or "Crab" in name:
                    continue  # 甲虫打不死/螃蟹缩起来0伤害，都不主动打
                if host_targets_only and hp >= maxhp:
                    continue  # 只打user在打的（血量不满的），满血怪不纠缠
                seen.add(k)
                ai_d = abs(mx - px) + abs(my - py)
                out.append((name, mx, my, hp, maxhp, ai_d))
            out.sort(key=lambda m: m[5])
            return out

        targets = _targets()
        if not targets:
            return "clear"
        if targets[0][5] > engage_dist + 2:
            return "clear"  # 最近怪也离太远
        self.detect_weapon()
        start = time.time()
        swings = 0
        while time.time() - start < kill_timeout:
            if swings % 2 == 0:  # 每2刀查血（降HTTP）
                self.eat_recovery(hard=self.hp_threshold, target=60)
                if not self.is_safe(self.hp_threshold):
                    return "unsafe"   # ← 原来是 return True（=接着打），等于危险了还继续
            targets = _targets()
            if not targets:
                return "clear"  # 怪清完/跑光
            name, mx, my, hp, maxhp, dist = targets[0]
            if dist > engage_dist + 2:
                return "clear"  # 追太远放弃
            if dist > 1:
                self.natural_walk(mx, my, self.my_location(), walk_only=True)
                time.sleep(0.3)
            # 锤子：能重砸就重砸（冷却 6s 自动降级平砍，重砸 AoE 贴脸必中），别一刀刀磨
            self.swing((mx, my), special=self.weapon_class == "hammer")
            swings += 1
        return "fight"   # 超时退出：怪还在，只是不打了

    # ═══════════ 协同：帮user开路 ═══════════

    def clear_ahead_stones(self, hx, hy, facing, rocks, rect_len=3):
        """敲掉目标(user)前进方向 3长×2宽 矩形（以user为中轴）内石头，帮开路。
        facing: 0上 1右 2下 3左。rocks 为主循环共享的岩体列表（敲掉的从中移除）。
        返回是否敲了。"""
        fx, fy = [(0, -1), (1, 0), (0, 1), (-1, 0)][facing % 4]
        cells = set()
        for d in range(1, rect_len + 1):
            for s in (-1, 1):
                # 面向方向 d 格 + 横向 ±1（垂直于面向方向的 ±1）
                cells.add((hx + fx * d + fy * s, hy + fy * d - fx * s))
        targets = [r for r in rocks if (r[0], r[1]) in cells]
        if not targets:
            return False
        s = self.state()
        px, py = s["player"]["x"], s["player"]["y"]
        targets.sort(key=lambda r: abs(r[0] - px) + abs(r[1] - py))
        x, y, name = targets[0]
        log(f"  🪨 帮user敲 {name} ({x},{y})")
        if self.smash_rock(x, y):
            try:
                rocks.remove((x, y, name))
            except ValueError:
                pass
            return True
        return False

    # ═══════════ 梯子 ═══════════

    def find_ladder(self):
        """找可下的梯子/竖井（参考 mine_run.find_ladder_api）。
        关键：用 /ladder 的 entrance 坐标明确排除入口梯子（上楼用），AI 不会飞回入口交互。"""
        try:
            r = self._get("/ladder")
            if r.get("ok"):
                ladder = r.get("ladder")
                shaft = r.get("shaft")
                entrance = r.get("entrance")
                ent = None
                if entrance and entrance.get("x") is not None and entrance.get("x") >= 0:
                    ent = (entrance["x"], entrance["y"])
                for cand in (shaft, ladder):  # 竖井优先（跳层快），梯子次选
                    if not cand:
                        continue
                    lx, ly = cand["x"], cand["y"]
                    if ent and (lx, ly) == ent:
                        continue  # 排除入口梯子（上楼用）
                    return lx, ly
        except Exception:
            pass
        return None

    def descend(self):
        """position 精确站上梯子/竖井→confirm→验证下楼。
        不走路（走路窗口会被飞蛇白打、且常差1格）；position 直接落梯子格最可靠。
        不 warp 回（warp 回会再次触发入口循环），失败直接返回 False 让主循环重试。"""
        # 下楼前顺手捡必捡物（银河之魂/五彩碎片/铱/放射性）——每层一次扫描，成本低
        try:
            self.pick_valuable_drops(max_items=2)
        except Exception:
            pass
        old_level = self.my_mine_level()
        ladder = self.find_ladder()
        if not ladder:
            return False
        lx, ly = ladder
        for attempt in range(3):
            # 1. position 精确站上梯子格（差1格 confirm 无效）。⚠️ 不加连通校验——梯子就是要去的出口，
            #    即使它在另一胞腔，瞬移到梯子=成功逃出（比"卡隔区回不去"好）；加了反而会拒掉合法逃生
            if not self.position_safe(lx, ly, exact=True):
                log(f"  ⚠️ 站不上梯子 ({lx},{ly})，重试 {attempt + 1}/3")
                time.sleep(0.5)
                continue
            # 2. 站上梯子可能已自动下楼
            chk = self.my_mine_level()
            if chk and chk > old_level:
                self.mine_level = chk
                log(f"  ⬇️ 站上梯子直接下到第 {chk} 层（跳了 {chk - old_level} 层）")
                return True
            # 3. 切镐子（防 confirm 手持炸弹=放炸弹）+ confirm 下楼（竖井需交互确认"跳下去"）
            self.select_pickaxe()
            self.key("confirm")
            time.sleep(0.5)
            # 竖井对话→选"跳下去"(option 0)
            for _ in range(6):
                s = self.state()
                if s.get("in_dialogue"):
                    self._post("/menu/click", {"option": 0})
                    time.sleep(1.5)
                    self.key("confirm")
                    time.sleep(0.5)
                    s2 = self.state()
                    if s2.get("in_dialogue"):
                        # ⚠️ 2026-09-26：原来这里发裸 `/click {}` —— 那是**真 OS 鼠标**：
                        #   无菜单分支会 `SetCursorPos(窗口中心)` + `mouse_event` 真点一下
                        #   ⇒ **拽走恒的光标**，而且真实点击会**把光标所在窗口顶到最前**。
                        #   恒那晚的描述正是这对组合：「献祭那个强切前台+鼠标漂移」。
                        #   这条只是"对话推不动了兜底推一下"，本来就该用进程内动作键 ⇒ 两个 no_ 都开。
                        self._post("/click", {"no_move": True, "no_mouse": True})
                        time.sleep(0.5)
                    break
                time.sleep(0.5)
            # 4. 验证下楼
            for _ in range(10):
                s = self.state()
                new_level = extract_mine_level(s.get("location", {}).get("name", ""))
                if new_level and new_level > old_level:
                    self.mine_level = new_level
                    jump = f"（跳了 {new_level - old_level} 层）" if new_level - old_level > 1 else ""
                    log(f"  ⬇️ 下到第 {new_level} 层 {jump}")
                    return True
                time.sleep(0.3)
            # 5. 没下去：下轮重试 position
            log("  ⚠️ confirm 后没下楼，重试")
        return False
    # ═══════════ 撤退 ═══════════

    def find_entrance(self):
        """当前层的入口梯子坐标（逃出用，/ladder 有 entrance）"""
        try:
            r = self._get("/ladder")
            if r.get("ok"):
                e = r.get("entrance")
                if e and e.get("x") is not None and e.get("y") is not None and e["x"] >= 0:
                    return e["x"], e["y"]
        except Exception:
            pass
        return None

    # ── 🏳️ 撤退链（恒 2026-09-19 定案，顺序即优先级，别改次序）──
    #   ① 返回权杖         → 落点=农场门口（游戏原生）
    #   ② 传送图腾：农场    → 落点=农场   （游戏原生）
    #   ③ 走回入口梯出矿    → 落点=矿口   （我们手写的）
    #   ④ /warp 兜底       → 落点=矿口   （我们手写的）
    # ⚠️ 山岭/沙漠/海滩图腾**一律不用**——恒："太远了，落到那里也不知道要干嘛"。
    # ⚠️ 每级都必须**校验落点**：`/warp` 回包里那个 `actual` 是**旧值**
    #    （2026-09-19 实测：回包写 Mine(23,8)，人其实已经在 Farm 了）⇒ 只认**另读的 /state**。
    RETREAT_SCEPTER = "Return Scepter"
    RETREAT_TOTEM = "Warp Totem: Farm"

    def _loc_now(self):
        return self.state().get("location", {}).get("name", "")

    def _pos_now(self):
        """(地图, x, y) —— 落点校验必须带坐标，**只看地图名会漏**。
        ⚠️ 2026-09-19 真机踩到：权杖/农场图腾的落点都在**农场**，人在农场时用它们
        "地图没变" ⇒ 被误判成"没走"⇒ 白烧一个图腾还错降级（实测图腾 65→64、却被报失败）。"""
        s = self.state()
        p = s.get("player", {})
        return (s.get("location", {}).get("name", ""), p.get("x"), p.get("y"))

    def _wait_pos_change(self, old_pos, timeout=6.0):
        """等 (地图,x,y) 真的变了再返回；超时返回当前位置，由调用方判成败。
        ⚠️ 权杖要 1 秒后才真传（反编译 Wand.DoFunction → fadeAfterDelay(wandWarpForReal,1000)），
        所以这里超时给到 6s。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            cur = self._pos_now()
            if cur[0] and cur != old_pos:
                return cur
            time.sleep(0.3)
        return self._pos_now()

    def _use_retreat_item(self, item_name, mode, label):
        """用一件撤退道具并校验落点。mode 直接透给 C# 的 `/use`：
          · "read"    = Object.performUseAction（图腾——右键语义，2026-09-19 真机验过）
          · "scepter" = Wand.DoFunction（返回权杖是 Tool，`/use` 的 Tool 分支只抬手不落）
        返回 (ok, msg)。"""
        have = self.count_item(item_name)
        if have <= 0:
            return False, f"背包没有 {item_name}"
        old_pos = self._pos_now()
        r = self.select(item_name)
        if not r.get("ok"):
            return False, f"选不中（{r.get('error', '')}）"
        time.sleep(0.4)
        rr = self._post("/use", {"mode": mode})
        if not rr.get("ok"):
            return False, f"没生效（{rr.get('error', '无回包/端点不存在')}）"
        # ⚠️ 精确判据（2026-09-19 真机踩到）：`mode=scepter` 在老 DLL 上**不报错**——
        #    它会静静落到普通 Tool 分支（只抬手），回包 `action:"tool"` 照样 ok:true。
        #    这时"位置后来变了"**不能算它干的**（实测：真有别的东西把人挪走了，我的
        #    "位置变了=成功"当场误报成"权杖生效"）。所以先认回包的 action 对不对得上。
        if mode == "scepter" and rr.get("action") != "scepter":
            return False, ("当前 DLL 没有 scepter 分支（回包 action="
                           f"{rr.get('action')!r}）——权杖这条路还没生效，需要重启游戏加载新 DLL")
        # ⚠️ 回包 ok 只说明"请求被接了"，**不算用过**——老 DLL 里 mode=scepter 会静静落到
        #    普通 Tool 分支（只抬手），照样回 ok:true。所以一律**另读位置**确认。
        new_pos = self._wait_pos_change(old_pos, timeout=6.0)
        if new_pos == old_pos:
            return False, f"用了但没走（还在 {new_pos[0]}({new_pos[1]},{new_pos[2]})）"
        if mode == "read" and self.count_item(item_name) >= have:
            # 走了却没消耗——图腾是一次性的，数量没减说明走成的那一下不是它干的，得说出来
            return True, f"生效→{new_pos[0]}({new_pos[1]},{new_pos[2]})（⚠️ 数量没减，请留意）"
        return True, f"生效→{new_pos[0]}({new_pos[1]},{new_pos[2]})"

    def retreat_leave_mine(self):
        """走回入口梯出矿：站到 entrance → 面向楼梯格 interact → 按 **key** 选 Leave。
        ⚠️ 2026-09-19 反编译 + 真机双证（**推翻** CHANGELOG 那句"矿层只能往下、无上行楼梯"）：
          · `MineShaft.findLadder`（MineShaft.cs:848-851）：瓦片 **115 = 上楼梯图案**，
            `tileBeneathLadder = (j, i+1)` = **楼梯正下方那格** = /ladder 报的 `entrance`
            = 人该站的格（`find_entrance()` 拿的就是它）。
          · `MineShaft.checkAction` case 115（MineShaft.cs:3073-3082）：面向楼梯格交互 →
            对话 **[Leave「离开矿井」, Do「不进行任何操作」]**。
          · 选 Leave → `warpFarmer("Mine", 23, 8)`（城镇）/ `("SkullCave", 3, 4)`（>120，
            GameLocation.cs:12388-12401）。
        真机实测（UndergroundMine2 / 7843）：站 (4,5)=entrance → interact (4,4) →
        `in_dialogue=True`、`/menu` 响应 key=[Leave,Do] → 点 Leave → **落 Mine(23,8)** ✓。
        ⚠️ 按 **key** 选，不硬编码 index——游戏在别处（`performAction:ExitMine`，GameLocation.cs:9818）
        给的是**三选项**版 Leave/Go/Do，index 会错位。
        ⚠️ 本层没有 entrance → 直接 False，不猜、不兜底（头骨/火山可能就没有）。
        返回 (ok, msg)。"""
        ent = self.find_entrance()
        if not ent:
            return False, "本层 /ladder 没报 entrance（没有上楼梯）"
        ex, ey = ent
        loc = self.my_location()
        pos_before = self._pos_now()   # 落点校验基线（点 Leave 前的位置）
        for _ in range(3):
            p = self.state().get("player", {})
            if abs(p.get("x", 0) - ex) <= 1 and abs(p.get("y", 0) - ey) <= 1:
                break
            self.safe_walk_to(ex, ey, loc, timeout=20)   # 拟人：走过去，不 position
        p = self.state().get("player", {})
        if abs(p.get("x", 0) - ex) > 1 or abs(p.get("y", 0) - ey) > 1:
            return False, f"走不到入口梯 ({ex},{ey})（现在 ({p.get('x')},{p.get('y')})）"
        self.face(0)
        time.sleep(0.3)
        self._post("/interact", {"x": ex, "y": ey - 1})
        time.sleep(1.2)
        resps = self._get("/menu").get("responses") or []
        idx = next((r.get("index") for r in resps
                    if str(r.get("key", "")).lower() in ("leave", "exitmine_leave")), None)
        if idx is None:
            if resps:
                return False, f"对话里没有 Leave（只有 {[r.get('key') for r in resps]}）"
            return False, "交互后没弹对话（可能没站在入口梯上）"
        self._post("/menu/click", {"option": idx})
        new_pos = self._wait_pos_change(pos_before, timeout=6.0)
        if new_pos[0] == loc:
            return False, f"选了 Leave 但没出去（还在 {loc}）"
        return True, f"走出矿井→{new_pos[0]}({new_pos[1]},{new_pos[2]})"

    def _retreat_warp(self):
        """原 retreat_to_entrance 的裸 warp，补上落点校验。
        火山→IslandNorth(40,24)；头骨→Desert(8,6)；普通矿井→Mountain(54,5)。"""
        loc = self.my_location()
        level = self.my_mine_level()
        # ⚠️ SkullCave 本身 extract_mine_level 返 None ⇒ level=0 ⇒ 老代码落到 else 传去
        #    Mountain（从沙漠传回鹈鹕镇，传错大陆）。这里显式认 SkullCave。
        if is_volcano(loc):
            dest = ("IslandNorth", 40, 24)
        elif loc.startswith("SkullCave") or level >= 121:
            dest = ("Desert", 8, 6)
        else:
            dest = ("Mountain", 54, 5)
        if self.safe_warp(*dest):
            return True, f"warp 到 {dest[0]}({dest[1]},{dest[2]})"
        return False, f"warp 到 {dest[0]} 失败或被拒"

    def retreat(self, reason):
        """🏳️ 撤退：按定案四级链逐级降级，每级**校验落点**；全失败就明确报错，绝不谎报成功。
        返回 True/False（调用方据此上报）。"""
        log(f"  🏳️ 撤退：{reason}")
        failed = []
        for label, fn in (
            ("① 返回权杖", lambda: self._use_retreat_item(self.RETREAT_SCEPTER, "scepter", "返回权杖")),
            ("② 农场图腾", lambda: self._use_retreat_item(self.RETREAT_TOTEM, "read", "农场图腾")),
            ("③ 走回入口梯", self.retreat_leave_mine),
            ("④ warp 兜底", self._retreat_warp),
        ):
            ok, msg = fn()
            log(f"    {label}：{msg}")
            if ok:
                return True
            failed.append(f"{label}({msg})")
        log(f"  ❌ 四级撤退全失败：{' | '.join(failed)}")
        return False

    def retreat_to_entrance(self, reason):
        """（兼容壳）老调用方不用改——内部已升级为四级撤退链。"""
        return self.retreat(reason)

    def touch_skull_statue(self):
        """头骨矿洞入口（沙漠）摸雕像：加竖井概率（下矿前调用）。
        雕像位置每存档不同——动态扫描找含 Statue 的对象，不写死坐标。"""
        try:
            if not self.safe_warp("Desert", 8, 6):
                log("  ⚠️ 传不到沙漠，跳过摸雕像")
                return False
            time.sleep(0.4)
            d = self.surroundings(30)
            statues = [(t["x"], t["y"], t.get("object", ""))
                       for t in d.get("tiles", []) if "Statue" in (t.get("object") or "")]
            if not statues:
                log("  🗿 沙漠没扫到雕像（可能没摆/在别处）")
                return False
            x, y, name = statues[0]
            log(f"  🗿 摸雕像 {name} ({x},{y})")
            # 站雕像旁边（下方→右→左→上）→ 面向雕像 → 交互
            stood = False
            for dx, dy, f in ((0, 1, 0), (1, 0, 3), (-1, 0, 1), (0, -1, 2)):
                if self.position_safe(x + dx, y + dy):
                    self.face(f)
                    time.sleep(0.3)
                    stood = True
                    break
            if not stood:
                log("  ⚠️ 雕像旁站不上，跳过")
                return False
            self._post("/interact", {})
            time.sleep(1.0)
            # 选效果：优先竖井/梯子（加竖井概率），其次免疫炸弹，再随便选
            try:
                m = self._get("/menu")
                if m and m.get("open") and m.get("type") == "ChooseFromIconsMenu":
                    icons = [b for b in (m.get("buttons") or []) if b.get("hoverText")]
                    pick = None
                    for kw in ("竖井", "梯子", "运气", "速度", "炸弹", "无法对你造成伤害"):
                        for b in icons:
                            if kw in (b.get("hoverText") or ""):
                                pick = b
                                break
                        if pick:
                            break
                    if pick is None and icons:
                        pick = icons[0]
                    if pick:
                        # ⚠️ 2026-09-26：同上——有菜单时 /click 一样会先 `setMousePosition` 拽光标。
                        #   ChooseFromIconsMenu **不在**"真读 Game1.getMouseX"的名单里
                        #   （decomp grep 实查）⇒ 传进去的 x,y 就够，不用挪光标。
                        self._post("/click", {"x": pick["x"], "y": pick["y"], "no_move": True})
                        time.sleep(0.8)
                        log(f"  🗿 选效果: {pick.get('hoverText')}")
            except Exception:
                pass
            return True
        except Exception:
            return False

    # ═══════════ 楼梯（99石头造，感染层跳关） ═══════════

    def ensure_free_slot(self, need=1):
        """确保背包有 need 个空格：满了丢一个低价值物。
        不丢石头（造楼梯要用）、不丢工具/炸弹/高价值食物。返回是否腾出格。"""
        if self.inventory_free_slots() >= need:
            return True
        s = self.state()
        for i in s.get("inventory", []):
            name = i.get("name", "")
            if not name:
                continue
            keep = item_keep_score(name, i.get("value") or 0)
            if keep < 40 and name != "Stone":  # 只丢真正低价值的，石头除外
                self._post("/drop", {"name": name, "count": i.get("stack", 1)})
                log(f"  🎒 腾格：丢 {name}")
                return True
        return False

    def craft_staircase(self):
        """99石头造一个楼梯（需要学会配方）。返回是否成功且背包真有楼梯。
        ⚠️ /craft 在背包满时会把成品丢地上仍返回 ok——先腾格再造，并检查 warning 验证。"""
        try:
            if self.inventory_free_slots() <= 0:
                if not self.ensure_free_slot():
                    log("  ⚠️ 背包满且腾不出格，没法造楼梯")
                    return False
            r = self._post("/craft", {"name": "Staircase", "count": 1})
            if not r.get("ok"):
                return False
            if r.get("warning"):
                log(f"  ⚠️ 造楼梯被丢地上了（{r.get('warning')}）")
                return False  # 下轮腾格后再造
            return True
        except Exception:
            return False

    def use_staircase(self):
        """造好的楼梯放面前空格→站上→confirm 下楼。返回是否成功。
        确认背包有楼梯（防手持切到沙拉丢地上）。
        - 入口梯子格计入 occupied（放楼梯/面朝不碰入口，防误触出去）
        - 站在入口/入口邻格先 position 挪到附近落脚点
        - 4 方向都不可放 → position 换落脚点重试（最多3轮）"""
        try:
            # 确认背包真有楼梯，再 select（防手持是别的物品把东西丢地上）
            if self.count_item("Staircase") <= 0:
                log("  ⚠️ 背包没有楼梯")
                return False
            if not self.select("Staircase"):
                return False
            time.sleep(0.2)
            entrance = self.find_entrance()
            dirs = [(0, -1, 0), (1, 0, 1), (0, 1, 2), (-1, 0, 3)]  # (dx, dy, face) face=面对该格的方向
            for _round in range(3):  # 最多 3 轮，每轮换位置
                rocks, occupied, _ = self.scan_rocks(8)
                occupied = set(occupied)
                if entrance:
                    occupied.add(entrance)  # 入口格不放楼梯/不面朝（防 confirm 误触入口梯子出去）
                s = self.state()
                px, py = s["player"]["x"], s["player"]["y"]
                # 站在入口/入口邻格 → 先 position 挪到附近落脚点（防误触）
                if entrance and abs(px - entrance[0]) + abs(py - entrance[1]) <= 1:
                    log("  🚶 在入口旁，position 挪开再放楼梯")
                    spot = self.find_safe_spot((px, py), 3, radius=8, occupied=occupied,
                                               center=(px, py), need_free_neighbor=True)
                    if spot:
                        self.position_safe(spot[0], spot[1])
                        time.sleep(0.3)
                        s = self.state()
                        px, py = s["player"]["x"], s["player"]["y"]
                for dx, dy, f in dirs:
                    nx, ny = px + dx, py + dy
                    if (nx, ny) in occupied:
                        continue
                    self.face(f)
                    time.sleep(0.15)
                    r_use = self.use_item()       # 放楼梯到面前格
                    time.sleep(0.6)
                    data = self.surroundings(6)
                    placed = any(t.get("x") == nx and t.get("y") == ny and
                                 ("Staircase" in (t.get("object") or "") or "楼梯" in (t.get("object") or ""))
                                 for t in data.get("tiles", []))
                    # use_item 确认放上也算（surroundings 可能读不到楼梯 object）
                    if not placed and isinstance(r_use, dict) and r_use.get("action") == "placed":
                        placed = True
                    if not placed:
                        continue  # 这格放不上，换方向
                    # position 精确站上楼梯格（283 轮实测可行）→ confirm
                    if not self.position_safe(nx, ny, exact=True):
                        log(f"  ⚠️ 站不上楼梯格 ({nx},{ny})，放弃这轮")
                        return False
                    time.sleep(0.2)
                    self.select_pickaxe()  # 切镐子（防 confirm 手持炸弹=放炸弹）
                    old_level = self.mine_level
                    self.key("confirm")
                    time.sleep(1.2)
                    new_level = self.my_mine_level()
                    return new_level > 0 and new_level != old_level
                # 4 方向都不可放 → position 换落脚点（有空格的地方）再试
                log(f"  🚶 楼梯周围放不上，position 换落脚点（第{_round+1}轮）")
                spot = self.find_safe_spot((px, py), 3, radius=8, occupied=occupied,
                                           center=(px, py), need_free_neighbor=True)
                if not spot:
                    log("  ⚠️ 找不到可落脚点，放弃")
                    break
                self.position_safe(spot[0], spot[1])
                time.sleep(0.3)
            log("  ⚠️ 楼梯放不上去（多轮多方向都不可放）")
            return False
        except Exception:
            return False

    # ═══════════ 协同：跟随user ═══════════

    def shadow_host(self, hp_threshold=50, max_minutes=5):
        """没炸弹时的兜底：跟在user身边当保镖（跟随+打怪，不炸）。
        user离开矿井→返回 False 撤退。"""
        log("  👥 没炸弹了，转为跟随user")
        deadline = time.time() + max_minutes * 60
        while time.time() < deadline:
            hl = self.host_location()
            if not is_mine_location(hl):
                log("  user离开了矿井")
                return False
            if not self.is_safe(hp_threshold):
                if not self.eat_if_needed(hp_threshold):
                    log("  状态不行，撤退")
                    return False
            # 打怪
            self.combat_step()
            # 跟随
            self.follow_host_once()
            time.sleep(0.8)
        return True

    def follow_host_once(self, walk_only=False):
        """读user位置，站到他身后/侧后 2 格（滞后跟随，不贴脸）。
        walk_only=True 强制自然走路（协同模式用，不闪现）。"""
        h = self.host_player()
        hx, hy = h.get("x", 0), h.get("y", 0)
        hl = self.host_location()
        ml = self.my_location()
        if hl != ml:
            # 不同层：追过去
            hlv = self.host_mine_level()
            mlv = self.my_mine_level()
            if hlv and mlv and hlv != mlv:
                log(f"  🚶 user在第{hlv}层，去追")
                self.safe_warp(hl, x=5, y=5)
                time.sleep(0.6)
            return
        s = self.state()
        px, py = s["player"]["x"], s["player"]["y"]
        dist = abs(hx - px) + abs(hy - py)
        if dist <= 3:
            return  # 已经贴身了
        # 站到user身后 2 格
        fd = h.get("facingDirection", 2)
        fvec = [(0, -1), (1, 0), (0, 1), (-1, 0)][fd % 4]
        tx, ty = hx - fvec[0] * 2, hy - fvec[1] * 2
        rocks, occupied, _ = self.scan_rocks(8)
        if (tx, ty) in occupied:
            for fx, fy in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
                if (hx + fx, hy + fy) not in occupied:
                    tx, ty = hx + fx, hy + fy
                    break
        self.natural_walk(tx, ty, ml, walk_only=walk_only)


# ═══════════ 背包规划（自动模式用） ═══════════

def backpack_plan(bot, drop_below=15, need_slots=3):
    """背包快满时输出规划，可选丢弃低价值物品腾格。
    返回 (freed_slots, plan_lines)
    """
    inv = bot.inventory_items()
    lines = ["🎒 背包规划："]
    for i in inv:
        name = i.get("name", "?")
        val = i.get("value") or drop_value(name)
        # ⚠️ 品质只有 0/1/2/**4**（反编译 `Item.cs:308`：4=铱星，3 这一档不存在）——老表写 3 ⇒ 铱星物品**标不出来**
        q = {0: "", 1: "[银]", 2: "[金]", 3: "[铱]", 4: "[铱]"}.get(i.get("quality", 0), "")
        lines.append(f"  · {q}{name}×{i.get('stack', 1)} ≈{val}g")
    free = bot.inventory_free_slots()
    lines.append(f"  剩余 {free} 格")
    freed = 0
    if free < need_slots:
        # AI 取舍：丢保留分最低的物品（垃圾优先；工具/炸弹/石头/稀有/高价值自动保留）
        drop_candidates = []
        for i in inv:
            name = i.get("name", "?")
            val = i.get("value") or drop_value(name)
            keep = item_keep_score(name, val)
            if keep < 50:  # 保留分 <50 才考虑丢（≥50 的高价值/食物/石头保留）
                drop_candidates.append((keep, val, i))
        drop_candidates.sort(key=lambda x: (x[0], -x[1]))  # 最低保留分、其次低价值先丢
        for keep, val, i in drop_candidates:
            if bot.inventory_free_slots() >= need_slots:
                break
            name = i.get("name")
            if not name:
                continue
            # ⭐ 2026-08-23 恒拍板：自动丢物全退役 → autodrop<=0 = 只规划不丢（交 AI 手动整理腾格）
            if drop_below <= 0:
                lines.append(f"  🚫 背包满（自动丢物已退役）→ 交AI手动整理（menu/丢 或 bomb_organize）")
                break
            bot._post("/drop", {"name": name, "count": i.get("stack", 1)})
            log(f"  🗑️ 丢了 {name}×{i.get('stack', 1)} (保留{keep})")
            freed += 1
            lines.append(f"  🗑️ 丢掉 {name}")
    return freed, lines
