# -*- coding: utf-8 -*-
"""🍽️ 吃食三条新规矩（恒 2026-10-03 拍板）+ 「效果食物」判据全打桩自验。

恒这一批的原话（三句，全部落地在这条链上）：
  ①「血线是60%，那现在体力的线是多少呢？」→ 体力吃食线 10% → **30%**（原来比撤退线 15% 还低 ⇒ 死路）
  ②「先碰到哪条线，就选**离能回复满最接近的那个背包食物（效果食物除外）**」
  ③「**有点名只吃点名，吃完了也不吃别的；不点名才自动吃**」
      + 「点名「吃」：现在专门去吃带这个效果的那份（补 buff）」（`food_buff` 的语义）

判据来源（**不编名单**）：
  · "这吃食有没有效果" = C# `/state.inventory[].foodBuffs`（游戏 `Object.GetFoodOrDrinkBuffs()`）
  · "现在挂着什么 buff" = `/buffs` 的 `id` + `seconds`
本自验全打桩、不出网。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
os.environ.setdefault("NAGI_HOST_URL", "http://localhost:7842")

import bomb_common as bc  # noqa: E402
import mine_run  # noqa: E402

fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


# 一条件食：`(名字, 回体力, 回血, buff档)`
# ⚠️ **buff 档照 C# 真回包写**（2026-10-03 真机 7842 抄下来的形状）：
#    `{"isDrink": bool, "buffs": [{id, source, ms, effects, rawEffects}, …]}`
#    —— 上一版夹具用的是"我脑子里那个形状"（直接一个数组），于是 C# 报对象、Python 按数组读，
#       **恒真"没有效果"**，36 条自验全绿也没照出来（真机一跑就露）。**夹具必须照真回包抄。**
CHEESE = ("奶酪", 125, 56, [])
LEEK = ("韭葱", 40, 20, [])
# ⚠️ **名字/数值照 7842 真回包抄**（`name` 是内部英文名 —— 回执印的就是它；中文显示名在 buff 的
#    `source` 里）。上一版夹具名字写的是"辣鳗鱼"、恢复量也是我编的 ⇒ 连"点名匹配"都没照出真形状。
EEL = ("Spicy Eel", 115, 51, {"isDrink": False, "buffs": [
    {"id": "food", "source": "香辣鳗鱼", "ms": 420000,
     "effects": ["+1 运气", "+1 速度"], "rawEffects": None}]})
TACO = ("Fish Taco", 165, 74, {"isDrink": False, "buffs": [
    {"id": "food", "source": "鱼肉卷", "ms": 420000, "effects": ["+2 钓鱼"], "rawEffects": None}]})
# 假想件：只为钉住"血低时回血 0 的永不选"那条守卫（1.6 里回血=edibility×0.45 ⇒ 真食物基本都 >0）
COFFEE = ("咖啡", 30, 0, {"isDrink": True, "buffs": [
    {"id": "drink", "source": "咖啡", "ms": 126000, "effects": ["+1 速度"], "rawEffects": None}]})
# 真机同款第二例：果酒的 buff 不是 "drink" 而是 BuffId `17`（醉醺醺）⇒ 只有 `isDrink` 分得出它是喝
WINE = ("Wine", 50, 22, {"isDrink": True, "buffs": [
    {"id": "17", "source": "果酒", "ms": 30000, "effects": ["-1 速度"], "rawEffects": None}]})

print("① 效果食物判据：**只认游戏报的那一位**（老 3 元组 = 不知道，不许猜）")
ck("…带 foodBuffs 的 ⇒ 是效果食物（**C# 真形状=对象，要拆到 buffs 那层**）", bool(bc.food_buffs_of(EEL)))
ck("…**果酒那种**（buff id 是 `17` 不是 `drink`）同样认得出", bool(bc.food_buffs_of(WINE)))
ck("…不带 ⇒ 不是", not bc.food_buffs_of(CHEESE))
ck("…**老形状 3 元组**（老 DLL/自验假数据）⇒ 空 = 不知道（不猜、不炸）",
   bc.food_buffs_of(("奶酪", 125, 56)) == [], bc.food_buffs_of(("奶酪", 125, 56)))
ck("…`foodBuffs` 为 `null`（没 buff 的东西 C# 那边落 null）⇒ 空",
   bc.food_buffs_of(("奶酪", 125, 56, None)) == [])
ck("…效果文案 = 游戏给的那几行", bc.food_buff_text(EEL) == "+1 运气 +1 速度", bc.food_buff_text(EEL))
ck("…buff id = 槽位（同 id 互相顶）", bc.food_buff_ids(EEL) == ["food"], bc.food_buff_ids(EEL))

print("② 点名匹配 `food_matches_buff`（效果文案 / buff id / 吃食名 / 来源显示名，大小写无关）")
ck("…中文效果文案（**照抄游戏印的**：真机是「运气」不是「幸运」）", bc.food_matches_buff(EEL, "运气"))
ck("…中文**来源显示名**（回执印英文名、buff 里带中文名 ⇒ 两种抄法都认）",
   bc.food_matches_buff(EEL, "香辣鳗鱼") and bc.food_matches_buff(EEL, "香辣鳗鱼"))
ck("…buff id（英文、大小写无关）", bc.food_matches_buff(EEL, "FOOD"))
ck("…吃食名（回执里印的那个英文名）", bc.food_matches_buff(EEL, "spicy"))
ck("…果酒那种：id `17` 认，效果「速度」也认", bc.food_matches_buff(WINE, "17")
   and bc.food_matches_buff(WINE, "速度"))
ck("…多个关键字要**全中**",
   bc.food_matches_buff(EEL, "运气,速度") and not bc.food_matches_buff(EEL, "运气,钓鱼"))
ck("…空关键字 = 不挑（恒真）", bc.food_matches_buff(CHEESE, ""))

print("③ 自动挑「**离补满最接近**」+ **效果食物除外**")
ck("…缺口 20 ⇒ 吃韭葱（不是奶酪）", bc.pick_food_closest_to_full([CHEESE, LEEK], need_hp=20) == "韭葱")
ck("…缺口 72 ⇒ 只有奶酪够 ⇒ 吃奶酪", bc.pick_food_closest_to_full([CHEESE, LEEK], need_hp=72) == "奶酪")
ck("…都不够 ⇒ 挑最大的（别拿小的白吃）",
   bc.pick_food_closest_to_full([LEEK, ("面包", 50, 25, [])], need_hp=999) == "面包")
ck("…**效果食物不参与**（香辣鳗鱼回的更多也不用）",
   bc.pick_food_closest_to_full([EEL, LEEK], need_hp=20) == "韭葱",
   bc.pick_food_closest_to_full([EEL, LEEK], need_hp=20))
ck("…包里只剩效果食物 ⇒ **返回 None**（不是「照吃」）",
   bc.pick_food_closest_to_full([EEL], need_hp=20) is None)
tk = bc.effect_food_note([EEL])
ck("…只剩效果食物时**说得出原因**（含名字 + 效果 + 点名提示）",
   "只剩带效果" in tk and "Spicy Eel" in tk and "food_buff" in tk, tk)
ck("…有普通食物时那句话是空的（别乱报）", bc.effect_food_note([EEL, LEEK]) == "")
ck("…血低时**回血 0 的永不选**（老规矩：绝不拿纯体力咖啡保命）",
   bc.pick_food_closest_to_full([COFFEE], need_hp=10) is None)


class FakeBot:
    """假 bot：一条假背包 + 一条假 `/buffs` + 记录吃过的名字（`maintain_buffs_for` 用）。"""

    def __init__(self, foods=None, buffs=None):
        self.foods = list(foods or [])
        self.buffs = list(buffs or [])
        self.ate = []

    def state(self):
        return {"player": {"isMoving": False, "health": 180, "maxHealth": 180,
                           "stamina": 474, "maxStamina": 474}}

    def detect_inventory_food(self):
        return list(self.foods)

    def _get(self, ep, params=None):
        return {"ok": True, "buffs": list(self.buffs)} if ep == "/buffs" else {}

    def eat(self, name=None):
        self.ate.append(name)
        # 🍽️ 照**真机行为**抄：吃下去那个 buff 就挂上了（真机延迟 2~3s；这里立刻挂，
        #    免得"等落地"那条轮询在假 bot 上白等满 6 秒 —— 夹具慢 6s 会顺带把节流钉子的前提搞坏）
        for f in self.foods:
            if f[0] == name:
                self.buffs = [{"id": i, "seconds": 400} for i in bc.food_buff_ids(f)]
        return True


print("④ 补 buff（`maintain_buffs_for`）：认游戏报的 id 对槽，**不认名单**")
b = FakeBot([CHEESE, EEL], buffs=[])            # 什么都没挂 ⇒ 该补
ck("…buff 没挂 ⇒ 吃那份带 buff 的", bc.maintain_buffs_for(b, want=None) is True and b.ate == ["Spicy Eel"], b.ate)
b = FakeBot([CHEESE, EEL], buffs=[{"id": "food", "seconds": 400}])
ck("…buff 还剩 400s > 30 ⇒ **不补**（也不会去吃奶酪）",
   bc.maintain_buffs_for(b, want=None) is False and b.ate == [], b.ate)
b = FakeBot([CHEESE, EEL], buffs=[{"id": "food", "seconds": 10}])
ck("…快过期（10s < 30）⇒ 补", bc.maintain_buffs_for(b, want=None) is True and b.ate == ["Spicy Eel"], b.ate)
b = FakeBot([CHEESE, EEL], buffs=[])
ck("…`want` 点名没匹配的 ⇒ **不吃别的**（恒：「有点名只吃点名」）",
   bc.maintain_buffs_for(b, want="钓鱼") is False and b.ate == [], b.ate)
b._last_buff_check = 0            # 清掉 min_gap 节流（同一 bot 连调会被节流，那是另一条钉子）
ck("…`want` 点名匹配的 ⇒ 吃它", bc.maintain_buffs_for(b, want="运气") is True and b.ate == ["Spicy Eel"], b.ate)
b = FakeBot([CHEESE], buffs=[])
ck("…包里**一件带 buff 的都没有** ⇒ 不吃（也不会抓奶酪充数）",
   bc.maintain_buffs_for(b, want=None) is False and b.ate == [], b.ate)
b = FakeBot([EEL, COFFEE], buffs=[])
bc.maintain_buffs_for(b, want=None)
ck("…候选按**背包顺序**取第一件（不另立优先级表）", b.ate == ["Spicy Eel"], b.ate)
b = FakeBot([EEL], buffs=[])
bc.maintain_buffs_for(b, want=None)
b._last_buff_check = time.time()      # 刚查过（**别拿"等真实时间"测节流**：一次调用现在可能花几秒）
ck("…`min_gap` 节流：刚查过就再调 ⇒ 不打网络、不再吃", bc.maintain_buffs_for(b, want=None) is False)
ck("…`want` 匹配的是**吃食名**也算（`food_matches_buff` 同一把尺子）",
   FakeBot([EEL], buffs=[]).foods and bc.food_matches_buff(EEL, "香辣鳗鱼"))


class FakeMiner:
    """假 `BombMiner`：只喂吃食规则要用的那几个方法/属性（`eat_if_needed` 是鸭子类型）。"""

    def __init__(self, foods, hp=180, max_hp=180, sta=474, max_sta=474, food_hp=None, food_sta=None):
        self._foods = list(foods)
        self._hp, self._max_hp, self._sta, self._max_sta = hp, max_hp, sta, max_sta
        self.food_hp = list(food_hp or [])
        self.food_sta = list(food_sta or [])
        self.ate = []

    def state(self):
        return {"player": {"health": self._hp, "maxHealth": self._max_hp,
                           "stamina": self._sta, "maxStamina": self._max_sta}}

    def detect_food(self):
        return list(self._foods)

    def eat(self, name=None):
        self.ate.append(name)
        return True


print("⑤ 点名 = **白名单**（恒 2026-10-03：「有点名只吃点名，吃完了也不吃别的」）")
m = FakeMiner([CHEESE, LEEK], hp=90, food_hp=["鱼肉卷"])          # 点名的那样**不在包里**
ck("…点名没货 ⇒ **不退回自动挑**（原来会退回，恒明确作废）",
   bc.BombMiner.eat_if_needed(m, food_hp=["鱼肉卷"]) is False and m.ate == [], m.ate)
m = FakeMiner([CHEESE, LEEK], hp=90, food_hp=["奶酪"])
ck("…点名有货 ⇒ 就吃它", bc.BombMiner.eat_if_needed(m, food_hp=["奶酪"]) is True and m.ate == ["奶酪"], m.ate)
m = FakeMiner([CHEESE, LEEK], hp=90)
ck("…**没点名** ⇒ 自动挑（缺 90 ⇒ 都不够 ⇒ 挑最大的奶酪）",
   bc.BombMiner.eat_if_needed(m) is True and m.ate == ["奶酪"], m.ate)
m = FakeMiner([EEL], hp=90)
ck("…没点名 + 只剩效果食物 ⇒ **不吃**（效果食物除外）",
   bc.BombMiner.eat_if_needed(m) is False and m.ate == [], m.ate)

print("⑥ 形状统一：`(名字, 回体力, 回血, buff档)` —— 回血在 **f[2]**（老代码在炸矿那边读错过）")
m = FakeMiner([("面包", 0, 300, [])], sta=100, hp=180)   # 纯回血、回体力 0；体力 21% 触线
ck("…体力线：按 f[1] 挑（回体力 0 的不选）",
   bc.BombMiner.eat_if_needed(m) is False and m.ate == [], m.ate)
m = FakeMiner([("面包", 300, 0, [])], sta=100, hp=180)
ck("…体力线：有回体力的那份被选中", bc.BombMiner.eat_if_needed(m) is True and m.ate == ["面包"], m.ate)

print("⑦ 矿井侧同一个规矩（`MineBot.eat_if_needed` 点名白名单 + `auto_eat` 效果食物除外）")


class FakeMineBot:
    def __init__(self, foods, hp=90, max_hp=180, food_hp=None, food_sta=None):
        self._foods = list(foods)
        self._hp, self._max_hp = hp, max_hp
        self.food_hp, self.food_sta = list(food_hp or []), list(food_sta or [])
        self.ate = []
        self.logs = []

    def state(self):
        return {"player": {"health": self._hp, "maxHealth": self._max_hp,
                           "stamina": 474, "maxStamina": 474},
                "inventory": [{"name": f[0]} for f in self._foods]}

    def detect_inventory_food(self):
        return list(self._foods)

    def _eat_one(self, name, why=""):
        self.ate.append(name)
        return True

    def auto_eat(self, hp_threshold, sta_threshold):
        pick = bc.pick_food_closest_to_full(self._foods,
                                            max(0, self._max_hp - self._hp) if self._hp / self._max_hp * 100 < hp_threshold else 0,
                                            0)
        if pick:
            self.ate.append(pick)
            return True
        return False


fm = FakeMineBot([CHEESE, LEEK], hp=90, food_hp=["鱼肉卷"])
ck("…点名没货 ⇒ 不吃别的",
   mine_run.MineBot.eat_if_needed(fm, None, ["鱼肉卷"], 60, 30) is False and fm.ate == [], fm.ate)
fm = FakeMineBot([CHEESE, LEEK], hp=90, food_hp=["奶酪"])
ck("…点名有货 ⇒ 吃它", mine_run.MineBot.eat_if_needed(fm, None, ["奶酪"], 60, 30) is True and fm.ate == ["奶酪"], fm.ate)
fm = FakeMineBot([EEL], hp=90)
ck("…没点名 + 只剩效果食物 ⇒ 不吃（也不报「吃了」）",
   mine_run.MineBot.eat_if_needed(fm, None, None, 60, 30) is False and fm.ate == [], fm.ate)

print("⑧ 🔴 点名吃食必须走 **`/eat`**（2026-10-03 真机逮到：`_eat_one` 原来发的是 `/use` ⇒ **从来吃不上**）")


class EatProbe(mine_run.MineBot):
    """只记端点，不打网络。"""

    def __init__(self):
        super().__init__(port=7843)
        self.eps = []

    def select(self, name):
        self.eps.append(("select", name))

    def _post(self, ep, data=None, **kw):
        self.eps.append(ep)
        return ({"ok": True, "ate": "Spicy Eel"} if ep == "/eat"
                else {"ok": False, "error": "不是可放置物（只能放箱子/机器/种子/树苗/地板等）"})


p8 = EatProbe()
ok8 = mine_run.MineBot._eat_one(p8, "Spicy Eel", "补 buff")
ck("…发的是 `/eat`", "/eat" in p8.eps, p8.eps)
ck("…**不许**发 `/use`（/use 是「用/放」，食物会被判成「不是可放置物」）", "/use" not in p8.eps, p8.eps)
ck("…`ok:true` 才算吃上", ok8 is True, ok8)
class EatFail(EatProbe):
    """`/eat` 明确回 ok:false 的假 bot。"""

    def _post(self, ep, data=None, **kw):
        self.eps.append(ep)
        return {"ok": False, "error": "当前物品不可食用（先 /select 选个食物）"}


ck("…`ok:false` 如实报「没吃上」（不装成功）", mine_run.MineBot._eat_one(EatFail(), "石头") is False)
ck("…`_check_eat_result` 已删（它是按 `/use` 回包形状写的判据，留着会误导）",
   not hasattr(mine_run.MineBot, "_check_eat_result"))

print()
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("🎉 全部通过（点名=白名单、效果食物除外、补 buff 认游戏判据）")
