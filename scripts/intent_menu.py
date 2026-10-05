# -*- coding: utf-8 -*-
"""🎯 意图选项单（senses 分支）——把「现在能做什么」渲染成一张可敲的单子。

═══════════════════════════════════════════════════════════════════════
这一层在解决什么
═══════════════════════════════════════════════════════════════════════
旧接口是**陈述式**：状态条说「背包里有草莓」，然后 AI 自己跑去一堆工具和参数里找路。
→ 于是长出两条规矩：「报错必须给下一步」「文案不许写改动史」——
  它们存在的唯一理由，就是**接口只陈述、不下令**。

新接口是**祈使式**：单子直接写「 1  卖 草莓×5 」，AI 只需要说 "1"。
→ 那两条规矩可以退休。

═══════════════════════════════════════════════════════════════════════
三条铁律（这一层的全部风险就在这三条上，改代码前先读它们）
═══════════════════════════════════════════════════════════════════════
1. **单子上每个字都必须从游戏读出来。**
   推出来的（哪怕我们很有把握）要么标 `？`，要么不进单子。
   ⚠️ 理由：一键跳转把「AI 做错」变成「**我们**做错」，而且 **AI 会照做、不会怀疑**——
   正是本项目最痛的那类伤（"工具说成功但事没发生"）。

2. **不许静默截断。** 被前 N 条挤掉的必须如实报「还有 K 项」。
   静默砍掉 = 手工制造新的"够不着"（域 op 断档那个病）。

3. **槽位是短命句柄，不是持久 ID。** 背包一整理/一吃东西，位次就漂。
   ⇒ 永远当场重读；**回执必须回显对象和数量**，让 AI 靠眼睛纠错（人也是这么干的）。

═══════════════════════════════════════════════════════════════════════
can() 的三档
═══════════════════════════════════════════════════════════════════════
    CAN_YES   → 进单子
    CAN_NO    → 不进（人也不列做不到的事）
    CAN_MAYBE → **问不出来 → 也不进**（但登记在 PENDING 里，等接线）

第三档不透支信任：宁可这一条不出现，也不给一个可能错的选项。
"""

from dataclasses import dataclass, field, replace
from typing import Callable, Optional

# 🎨 色名（🟪粉 / 🟪紫 那种）——**和 storage 域共用同一份**，别在这儿再抄一张表。
from storage_common import _color_display

# ── can() 的三档 ──────────────────────────────────────────────────────
CAN_YES: bool = True
CAN_NO: bool = False
CAN_MAYBE = None          # 问不出来


# ═══════════════════════════════════════════════════════════════════════
# ① 扫描器 · 背包
# ═══════════════════════════════════════════════════════════════════════

def slot_of(item: dict) -> Optional[int]:
    """背包里给 AI 看的位次（1-based）——取游戏自己的 `slotIndex`。

    ⚠️ **不是列表下标**。`/state` 每件带 `slotIndex` = 游戏真实背包槽
    （点坐标、点菜单全用它），而列表顺序可能被消费侧过滤过。
    两个来源的数字**不能混着当一把尺子**（同族教训：2026-09-27 catNum 那次）。

    老 DLL 没这字段 → 返回 None。调用侧显示 `--` 且**不可选**——
    **不兜底猜位次**（猜错就是"我们替它点错格子"）。
    """
    si = item.get("slotIndex")
    return si + 1 if isinstance(si, int) else None


def scan_backpack(state: dict) -> list:
    """把 `/state` 的背包搬运成「槽位单」。**只搬运、不推断**。

    ⚠️ 必须喂 **full** `/state`，不能喂 light。light 模式下 `catNum` 对工具落 0、
    full 落 -99——同一字段两个口径，拿 light 去判"是不是书"会**静默判错**。
    """
    out = []
    for i in (state or {}).get("inventory") or []:
        if not i:
            continue
        out.append({
            "idx": slot_of(i),                              # None = 老 DLL，不可选
            "name": i.get("displayName") or i.get("name") or "?",
            "stack": i.get("stack", 1),
            # ⚠️ **不拿 0 兜底**：`0` 是"普通品质"这个**真值**（`/select` 的 quality=0 = 只要普通的），
            #    而缺字段是"不知道"。两者折叠成一个 0，就会**挑不中银/金/铱星那一摞**。
            #    缺就是 None，往下走由消费方判（同 can() 三档：算不出 ≠ 不是）。
            "quality": i.get("quality"),
            "value": i.get("value") or 0,
            "sellable": i.get("sellable", True),
            # 下面几个**原样搬，不猜**：字段不在就是 None，跟着走 CAN_MAYBE
            "cat_num": i.get("catNum"),
            "edible": i.get("edibleValue"),
            "health": i.get("healthRecovered"),
            # 🗑️ **能不能投出货箱**（C# 的 `Item.canBeShipped()`）。
            #    ⚠️ 跟 `sellable` **不是同一把尺子**（后者只排 工具/武器/靴/戒）——
            #       真机上照 `sellable` 列「投哪件」，14 件里 6 件根本进不去出货箱。
            #    ⚠️ 老 DLL 没这个键 ⇒ `None` ⇒ 那一行**不出现**（同三档，见 `_bin_can`）。
            "shippable": i.get("shippable"),
            # 🪨 2026-10-01：**能不能拿去铁匠铺砸**（C# 的 `/state.inventory[].isGeode`，
            #    游戏自己的 `Utility.IsGeode()` —— `GeodeMenu.HighlightItems` 同一把尺子）。
            #    ⚠️ **不许在 Python 编 id 名单**（C# 里那份 `{535,536,537,749,791,887,891}`
            #    就是编的，这一批已换成 `IsGeode`）。
            #    ⚠️ 这一位 C# **恒写**（真/假都写），所以"所有件都缺它"才等于"这版 DLL 不吐"。
            "is_geode": i.get("isGeode"),
            "raw": i,
        })
    return out


# 🌟 「这是本书」——游戏自己的判据（`Object.Category == -102`）。
#    2026-09-27 那次把状态条的判据从**名字名单**改成问游戏就是这个：
#    `Jewels Of The Sea` 在旧名单里零命中，AI 白丢一条提示。
BOOK_CAT = -102


def is_book(slot: dict):
    """🟢 识别层：这东西**是不是**一本书。→ True / False / None(问不出来)"""
    c = slot.get("cat_num")
    if c is None:
        return CAN_MAYBE
    return c == BOOK_CAT


# 👕 「这东西能穿」+「是哪一类」——**游戏自己的分类号**。
#    逐个从反编译核过（`G:\wingheng\Claude\NagiBridge\decomp\full\StardewValley\Object.cs:243-259`）：
#      · `hatCategory=-95` · `ringCategory=-96` · `bootsCategory=-97`
#      · `clothingCategory=-100` · `trinketCategory=-101`
#    ⚠️⚠️ **没有 `pantsCategory`** —— 我一度以为裤子是 `-101`，那是**饰品 Trinket**。
#       裤子走 `Clothing`：`StardewValley.Objects/Clothing.cs:100/118/132` 三处构造函数
#       **都写 `base.Category = -100`**，衬衫/裤子靠 `clothesType` 枚举分（`Clothing.cs:44`）。
#    ⚠️ 为什么不"问 C# 要类型"：`TryEquip`（`ModEntry.cs:6255`）判的是 **CLR 类型**
#       （`item is Clothing` / `is Hat` / …），Python 看不见类型 —— 而 `catNum` 就是
#       `Item.Category`（`ModEntry.cs:5193` 原样吐出来），是**同一件事的可读投影**。
#       ⇒ 这一条**不需要改 DLL**（改 DLL 要恒关游戏，本机活的那份在 F 盘且被锁着）。
WEARABLE_CATS = {-95: "帽子", -96: "戒指", -97: "靴子", -100: "衣服", -101: "饰品"}

# 👕 `/worn` 的槽位 → 中文。⚠️ **槽名必须跟 C# 一致**：权威清单就是 `TryTakeOff`
#    报错里那句 `（boots/leftRing/rightRing/trinket/hat/shirt/pants）`（`ModEntry.cs:6366`）。
#    ⚠️ **没有 `accessory`** —— `/worn` 会吐这个键（面部饰品），可 C# 的槽位表**不认它**，
#       列出来就是"按了不成"的行。`_intent_wiring_selftest.py` 有一条会**读 C# 源码**
#       核这张表（漂了就红，不靠人记）。
# 👕 2026-10-02：「穿戴」**整行撤出单子**（恒拍板，恒原话：「我看了一下，这样的话穿戴会在单子上
#    常驻哦！考虑挪出去跟 sleep 一起放 daily 吗？因为也不怎么常用来着」）——
#    连同原来那套 `_WORN_SLOTS` / `_wear_cat` / `_worn_name` 一起删（撤干净，没别的地方用了）。
#    **为什么撤**：它权重 38 = 单子最低 ⇒ 在"没别的事可做"的场景里会**常驻**
#    （棚里那种只剩「捡」+「穿戴…」的屏）；**不是不好用，是位置错了**。
#    **替代路**：`daily(ops="wear", kw={"name": 内部名})` / `daily(ops="wear", kw={"slot": "hat"})`
#    —— 跟 `sleep` 同一个家（`daily` 的 dispatch 里本来就有 `wear`，2026-10-01 就在）。


# 🛠 「这是不是工具」——游戏自己的判据（`Tool.Category == -99`，反编译 + CHANGELOG 09-27 那条）。
#    ⚠️ 比 `item is Tool` **略宽**：镰刀是 `(W)47` 也叫 -99（已知差异，不影响用途）。
TOOL_CAT = -99


def is_tool(slot: dict):
    """🛠 识别层：这东西**是不是工具** → True / False / None(问不出来)。"""
    c = slot.get("cat_num")
    if c is None:
        return CAN_MAYBE
    return c == TOOL_CAT


def is_edible(slot: dict):
    """🍽 识别层：这东西能不能吃（游戏 `staminaRecoveredOnConsumption`）。"""
    e = slot.get("edible")
    if e is None:
        return CAN_MAYBE
    return e > 0


# ═══════════════════════════════════════════════════════════════════════
# ② 世界快照 + 动词表
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Ctx:
    """can() 拿到的全部世界快照。**只读**，谁都不许在 can() 里改它。"""
    px: int = 0
    py: int = 0
    loc: str = ""
    held: Optional[dict] = None                      # 手持槽（scan_backpack 的一条）
    inv: list = field(default_factory=list)          # scan_backpack 的结果
    tiles: dict = field(default_factory=dict)        # {(x,y): /surroundings 的 tile}
    menu: Optional[dict] = None
    # 🚪 **界面出口**（2026-10-01）：菜单开着时，单子上那一行「关掉界面」的**标题**。
    #    `""` = **这一刻不该给**（捏人页 / 钓鱼小游戏 / 对话框 —— 给了就是劝 AI 去干错事，
    #    分类表在服务器 `_menu_exit_of`，挨着 `_close_hint`）。
    #    ⚠️ **由服务器算好递进来，这一层不自己判菜单类型**：同一张判据放两处 = 早晚漂
    #       （本项目的老病；`caps` / `shop` 都是这个形状）。
    menu_exit: str = ""
    # 📄 菜单开着却**没有出口行**时，空白屏上该印的那句指路（服务器 `_close_hint` 的**原话**）。
    #    ⚠️ 它存在的唯一理由：**菜单态绝不能再印「at x,y 指过去」**——
    #       `at` 指出来的世界动作，`do_row` 的菜单态守卫**全挡**。那是**假门**
    #       （2026-10-01 真机三步走完：show 空 → at 给"坐椅子" → do 被挡）。
    menu_hint: str = ""
    # 🎓 2026-10-02 恒：「补一下缺门」—— 有些菜单**这一刻的正事不是"关掉"**：
    #    精通碑开着时抬头写着"可以领"，而单子上只有「关掉界面」⇒ **领取那件事没有行**。
    #    `menu_claim` = 服务器算好的**那一行的标题**（`""` = 这个菜单没有可领的）。
    #    判据在服务器（`/state.activeMenu.mastery.canClaim`，跟抬头那句话**同源**），这一层只消费。
    menu_claim: str = ""
    # 📜 2026-10-04 恒：「**先做领奖**」——任务日志里"已完成、有钱、还没领"的任务。
    #    `{}` = 没开日志 / 这一刻一条都没有（⇒ 整行不出现）；有值时：
    #      `{"items":[{"index","name","description","money","x","y","source"}],
    #        "n":N, "sum":G, "reward_box":{"x","y"}, "page":P}`
    #    ⚠️ **由服务器算好递进来**（`_im_quests` → 只在 QuestLog 开着时读一次 `/menu`）：
    #       这一层**不认菜单名、不打 HTTP**（同 `menu_claim`/`menu_exit`/`shop` 的形状）。
    #    ⚠️ `description` = 详细页那段正文（老 DLL 没这一栏 ⇒ 空串，回执少印一栏、不编）。
    quests: dict = field(default_factory=dict)
    # 🧬 2026-10-04 恒（B 批）：「升级职业的**没有叉叉，不能关掉**」+「把那行撤掉，换成**真选项**」。
    #    `{}` = 这一刻不是"职业选择屏"（普通升级 C# 自己会点 OK）；有值时：
    #      `{"skill":"钓鱼","level":10,"left":{"id":8,"name":"垂钓者"},"right":{…}}`
    #    ⚠️ **由服务器算好递进来**（`_im_levelup`，**零额外 HTTP** —— 就在 `/state.activeMenu.levelUp`
    #       里，跟状态条那条引导同一份）；这一层不认菜单名、不编职业名。
    levelup: dict = field(default_factory=dict)
    # 🏛️ 2026-10-04 恒「动A」：「看看你觉得方便，又**保留一种一件件物品捧上槽位的趣味感**？」
    #    `{}` = 这一刻没开献祭板（或老 DLL 报不出"能捧上什么"）；有值时（服务器 `_im_cc` 算好）：
    #      `{"area":"鱼缸","specific":False,"current":-1,"new_fields":True,
    #        "bundles":[{"index":6,"name":"河鱼收集包","complete":False,
    #                    "missing":[{"name":"鲤鱼","need":1,"category":None}],
    #                    "give":[{"slot":3,"name":"鲤鱼","id":"(O)147","count":3,
    #                             "want":"鲤鱼","need":1,"full":True}],
    #                    "click":{"x":…,"y":…}}],
    #        "slots":[…],"inventory":[…],"donatables":[…],"partial":…}`
    #    ⚠️ **判据全在游戏**（`Bundle.IsValidItemForThisIngredientDescription` /
    #       `canAcceptThisItem` / `CanBePartiallyOrFullyDonated`）——这一层不认 id、不认类别号。
    cc: dict = field(default_factory=dict)
    # 🏛️ 2026-10-05 恒三条件定的**世界侧入口**（_im_cc_board）：{} = 不给那行（不在社区中心 /
    #    包里没有它收的 / 板子已不在）；有值 {x,y,area,have,pos} ⇒ 给一行「看 献祭板（走过去）」。
    cc_board: dict = field(default_factory=dict)
    # 🏛️ 2026-10-05（补24c）**世界侧那一行**「去博物馆捐赠（包里 N 件可捐）」的账 ——
    #    恒原话：「背包有可捐能跟献祭一样打标吗？」（形状照 `cc_board` 那一行）。
    #    `{}` = **不给那行**（三种情形合并：包里 0 件可捐 / 老 DLL 报不出 `donatable` 这一位 /
    #    服务器没算）；有值 `{"have": N}` ⇒ 给一行「去博物馆捐赠（包里 N 件可捐）」。
    #    ⚠️ 判据全在服务器（`_im_museum_go`）：`/state.inventory[].donatable` = **游戏自己那把尺子**
    #       （`LibraryMuseum.IsItemSuitableForDonation`，"已经捐过"它自己就回 false）——
    #       这一层**不认类别号、不认 id、也不判"捐过没有"**。
    museum_go: dict = field(default_factory=dict)
    # 📋 **开着的菜单里摊出来的东西**（2026-10-01 · P-menus）。
    #    ⚠️ **由服务器挑好递进来**（`_im_menu_data`），这一层**不自己打 `/menu`、也不认菜单名**
    #       —— 跟 `menu_exit`/`shop`/`caps` 同一个形状：判据只有一处，消费侧只管用。
    #    `{}` = 没开菜单 / 开的这种菜单**没有能摊的东西**（老行为一字不动）。
    #    现在会填的只有容器那一种（`items`）。
    menu_data: dict = field(default_factory=dict)
    # 🔨 **这一刻"能不能在铁砧上重铸一件饰品"**（2026-10-01 · 恒问的那三条铱锭/精通状态）。
    #    `{}` = 不能（没铁砧 / 没战斗精通 / 背包里没有能重铸的饰品 / **铱锭不够** / 老 DLL 没那端点）。
    #    有值时形如 `{"item": "仙女盒", "x": 25, "y": 23, "need": 3, "have": 3}`。
    #    ⚠️ **由服务器探针算好递进来**（`/machine_reqs` 的 `canPlace` + `AdditionalConsumedItems`，
    #       **只问不做**）：这一层不许自己打 HTTP（`can()` 是纯函数），也不许编"3"这个数。
    reforge: dict = field(default_factory=dict)
    # 🧺🔁 **这一刻"收放"这回事**（2026-10-01 · 恒拍板 (b)：单子那条改成"先挑放什么料 →
    #    走 `machine_loader --here` 拟人收放"）。`{}` = 本图没机器可伺候（那行不出现）。
    #    有值时形如 `{"ready": 19, "empty": 12, "products": {"翡翠": 17},
    #                "loadable": [{"name": "Jade", "display_name": "翡翠", "count": 17,
    #                              "machines": "宝石复制机×18"}]}`。
    #    ⚠️ **由服务器算好递进来**（`/machines` 的 `status` + `/machine_reqs` 的 `canPlace`
    #       探针 = 游戏自己的 `PlaceInMachine(probe:true)`）：这一层是纯函数，不打 HTTP、不编。
    mwork: dict = field(default_factory=dict)
    # 🚪🐄 **这一刻"能不能开门放牧"**（2026-10-01 · 恒：「放牧（开关畜棚鸡舍门）做进选项了吗？」）。
    #    `{}` = **算不出来**（不在农场 / 天气·季节读不到 / `/farm_buildings` 读不到）⇒ 那两行不出现。
    #    有值时形如 `{"builds": 2, "rain": False, "winter": False}`：
    #      · `builds` = **本档动物建筑的个数**（`/farm_buildings` 里 `type` 含 `Coop`/`Barn` 的），
    #        可以是 0 = **问清了：这个档没有动物建筑**（跟 `{}` 的"不知道"是两回事）；
    #      · `rain` / `winter` = 今天能不能放牧（雨/雷暴/绿雨算雨）。
    #    ⚠️ **由服务器算好递进来**（`_im_doors`：`/state.time` 的 `weather`/`season` + `/farm_buildings`）——
    #       这一层是**纯函数**：`can()` 不许打 HTTP（同 `caps`/`shop`/`reforge`/`mwork`），
    #       也不许自己手抄一份"哪些建筑算动物建筑"的名单（名单会烂，本项目的老病）。
    doors: dict = field(default_factory=dict)
    # 🎬 **正在播的剧情/事件**（`/state` 的 `activeEvent`，没有就是 `None`）。
    #    ⚠️ 必须跟 `menu` 分开看：事件**不是菜单**（`activeMenu` 那时可能是 null），
    #       而且节日期间 `activeEvent` **恒在播** —— 那是"这一刻的事实"，不是"有个弹窗挡路"。
    event: Optional[dict] = None
    # 🪑 我此刻是不是坐着（`/sittable` 的 `me.sitting`）。坐着时**不该再给"坐"的行**
    #    ——要先起身（`scene stand`）。这是**处境**，不是格子的属性。
    sitting: bool = False
    # 👕 我现在**身上穿着什么**（`/worn` 的 `worn` 那份）。
    #    ⚠️ 空字典 = "什么都没穿" **或** "读不到" —— 两者在这一层**都没行可出**，
    #       所以这儿不分开（不同于 `shop` 那三态：那个折叠会让 AI 以为"这店不收东西"）。
    #    ⚠️ 读的必须是 **AI 自己那端**（`_ai_get`）——穿戴物是"我的"，不是恒的。
    worn: dict = field(default_factory=dict)
    # 🌿 **六件"顺手就做"的活**（2026-10-01 恒「接吧」= 把 P1 那批**空参行**接上单子）。
    #    `{}` = 一件都推不出来（那 6 行**全不出现**）。有值形如：
    #      `{"berry": 1, "spot": 2, "moss": 5, "crab": 4, "pan": {"x": 33, "y": 36},
    #        "milk": 2, "shear": 1}`
    #    ⚠️ **由服务器算好递进来**（`_im_chores`：`/surroundings` + `/crab_pots` + `/state.orePan`
    #       + `/animals`）：这一层是**纯函数**，`can()` 不许打 HTTP（同 `caps`/`shop`/`doors`），
    #       也不许自己编"哪些算斑点/苔藓"的名单 —— 那几个字段的判据跟状态条「🌿 可采集」
    #       **共用 `_forage_counts()` 一份**。
    chores: dict = field(default_factory=dict)
    # 🏪 **铁匠铺现在营业吗**（恒 2026-10-01：「砸晶球…做成背包检测：有各种晶球**且克林特营业中**
    #    可以报」）。判据由服务器算好递进来（`_im_clint_open()`：现成的休息日表 + `SHOP_HOURS`
    #    前导时段）——这一层不打 HTTP。
    #    ⚠️ `False` **同时**代表"关门"和"读不到"（两种都**不给那一行**，见 `_geode_can`）：
    #       方向是"宁可少给一行"，不是"没有"——所以这儿不做三态（跟 `shop` 那个三态不是一回事）。
    clint_open: bool = False
    # 🌾 **这一刻"能不能铺干草"**（恒 2026-10-01：「支持上单子」）。`{}` = 不给那一行。
    #    有值时形如 `{"silo": 3, "bench_used": 3, "bench_total": 12}`。
    #    ⚠️ **由服务器算好递进来**（`_im_hay` → `feed_hay.read_hay_status()`，**脚本铺草前读的
    #       同一份判据**；只在**动物建筑内**才推）⇒ 这一层不打 HTTP、也不自己扫 Trough。
    hay: dict = field(default_factory=dict)
    # 🎁 **这一刻地上有什么能捡的**（恒 2026-10-01：「复用原来的捡蛋工具」）。
    #    `{}` = 一件都没有（**「捡」那行不出现** = 待办语义）；有值形如
    #    `{"n": 17, "near": 3, "keys": [[11, 14], …]}`。
    #    ⚠️ **判据在 `pickup_scene.scan_pickables()`（脚本那一处）**：`(O)` 开头的普通物件
    #       + 不在它那份 `BLACKLIST` 里 + **允许目标格站不住**（棚里的蛋/毛挡路，
    #       走不过去就"站旁边 face+interact"，工具里本来就有这条路）⇒ 服务器只搬结果过来，
    #       这一层做**成员判断**，不打 HTTP、不抄名单。
    pick: dict = field(default_factory=dict)
    # 🐾 本图的宠物（猫狗）——来自 `/surroundings` 的 `npcs` 里 `kind=="pet"` 的那几个。
    #    它们是**世界级**的（不属于某一格的动作），所以不进 tiles。
    pets: list = field(default_factory=list)
    # 🐄 **棚里还没摸的**（按建筑汇总：`{"Deluxe Coop": 12}`）。
    #    ⚠️ 2026-09-30 真机抓的缺口：`/animals` 只扫**当前图** ⇒ 站在农场上它回 0 条，
    #    而同一刻 `/farm_report` 是 24 只待摸（全在 Coop/Barn 里）⇒ 「摸 还没摸的动物」**整行不出现**，
    #    可 `_exec_pet` 本来就会自己走进棚里摸 —— **判据比执行器窄**。
    #    ⚠️ 只存**汇总**（不存完整列表）：完整列表太肥，会在每次 `intent show` 上白烧 token。
    animals_away: dict = field(default_factory=dict)
    stamina: int = 0
    # 🎒 背包容量（游戏 `Farmer.MaxItems`：12/24/36 三档）。
    #    “取”那条行要判“背包放得下吗”——**只能问游戏要**，写死 36 就是编表。
    #    拿不到（老 DLL）→ 0 ⇒ 算不出 ⇒ 那条行不出现（同 can() 第三档）。
    max_items: int = 0
    # 🔌 连接级能力表（字段名 → True=这版 DLL 会吐它 / 缺失=不知道）。
    # ⚠️ **必须由调用方从版本信息填，不许从格子里猜**。
    #    `/surroundings` 对这几个字段用的是"**只在为真时才写键**"的约定
    #    （C# `if (objForage) tile["forage"] = true;`）⇒ 单看一格，
    #    「这格不是」和「这版不给」**长得一模一样**。想在逐格层面分辨，
    #    结论只能是编的——那是这一层最不能犯的错。
    #    ✅ 2026-09-30：C# `/status.caps` 已落地 ⇒ 服务器那份 `_im_caps()` 改成**问游戏**填这张表
    #       （老 DLL 才退回构建标记猜）。这里只管**问**（`ctx.cap(name)`），不管怎么填。
    caps: dict = field(default_factory=dict)
    # 🀄 英文物品名 → 中文显示名（**缺就用英文**，不编）。
    #    来源全是"我们手里已经有的数据"：AI 背包的 displayName + 本图箱子里物品的
    #    displayName + **机器的 `heldItemDisplay`**。**不为此新打 HTTP**。
    #    ✅ 2026-09-30：最后那一份就是正解 —— 原先机器产物只能靠"别处碰巧有同名物品"
    #    才凑得出中文（真机实拍过 `翡翠×17、Diamond×1、Iridium Ore×1`），现由 `/machines` 直供。
    #    ⇒ **别在这儿堆名单**（名单会烂，本项目的老病）。
    zh: dict = field(default_factory=dict)
    # 💰 钱包（`/state` 的 `player.money`）——买那条行要给"买得起吗"的判断面。
    #    同 `max_items`：**问游戏要**，不写死。
    money: int = 0
    # 🏪 商店（**只在 ShopMenu 开着时才有**）—— 货架 + 「这家收什么」。
    #    ⚠️ 三态**不许折叠**（"没有"和"读不到"混成一个就是静默）：
    #      · `None`              —— **没开商店**（确定的"没有"）
    #      · 有 `items` 键的字典  —— 读到了
    #      · 字典但没有 `items`   —— 商店开着**读不出来**（"不知道" ⇒ CAN_MAYBE）
    #    数据取自 `/menu`（**不是** `/state`）：`/state.activeMenu` 只有类型，
    #    货架明细与「这家收什么」**只在 `/menu` 那份里**（同族坑：`/state` 瘦 `/menu` 详
    #    会漂移，09-27 喂错源那次的形状）。
    shop: Optional[dict] = None
    # 🕐 钟点（`/state` 的 `time`，如 `"13:20"`）。
    #    「睡觉」要用它把**白天别过夜**这条说出来（理由栏），并按钟点调权重 —— 见 `_sleep_weight`。
    time: str = ""
    # ❤️💪 「躺一下」抬权重的判据要用 —— 同 `stamina`，**都是问游戏要的**（不写死上限）。
    health: int = 0
    max_health: int = 0
    max_stamina: int = 0
    # 🗿 **这一刻"能不能去摸雕像"**（2026-10-04 恒拍板 (b)：单子上要有摸雕像那行）。
    #    `{}` = **本场景没有雕像**（那行不出现 —— 待办语义，同「捡」那行）。
    #    有值时形如 `{"names": ["Statue Of Blessings"], "used_today": False}`：
    #      · `names` = 雕像的**游戏内部名**（不编中文译名：那是会烂的名单）；
    #      · `used_today` = **今天摸过没**，`None` = **这版 DLL 报不出来**（消费侧给 MAYBE）。
    #    ⚠️ **由服务器算好递进来**（`_im_statue`：`/surroundings` 的 object 层 + `/state.player.
    #       blessedByStatueToday`）—— 这一层是**纯函数**，`can()` 不许打 HTTP（同 `caps`/`doors`/
    #       `chores`），也**不许自己抄一份"哪些雕像算数"**：那个判据只有一处（`blessing_statue.py`
    #       扫的也是它，脚本是唯一的执行器）。
    statue: dict = field(default_factory=dict)
    # 🐟 **鱼塘里等着领的产出**（2026-10-04 恒：鱼塘产出也上单子）。
    #    `{}` = 不该给行（**不在农场** / 读不到 —— 两者在这一层都没行可出）；
    #    有值形如 `{"ready": [{"x": 12, "y": 30, "output": "鲑鱼子"}], "total": 3}`。
    #    ⚠️ **由服务器算好递进来**（`_im_ponds` → `_fetch_fish_ponds()`，跟晨报/`farm ops=pond`
    #       同一份）—— 这一层不打 HTTP、也不自己算"哪座塘有货"。
    ponds: dict = field(default_factory=dict)
    # 🐟 **这一刻"每一口能动的鱼缸"的账**（2026-10-04 恒：「开完鱼缸以后，理应也可以指导 AI
    #    『背包还有什么东西能够手持放进鱼缸』吧」，随后把整条链子定成"包办"）。
    #    **按键是坐标**：`{"44,23": {…}, "41,29": {…}}` —— 一口缸一份账，
    #    `ctx.tank_at(x, y)` 按**被点的那一行**取。
    #    ⚠️ 原来只存"离我最近的那一口"⇒ 屋里两口缸时，**点开大缸那行看到的是豪华缸的内容**
    #       （子行坐标却是大缸的）= 两条错答案混在一屏，按下去就是"按 A 缸的判断动 B 缸"。
    #       2026-10-04 真机当场照到，已改。
    #    ⚠️ **由服务器算好递进来**（`_im_tank` → C# `/tank`，判据是**游戏自己的**
    #       `CanBeDeposited`/`HasRoomForThisItem`/`GetCategoryFromItem`/`CatchWearHat`）——
    #       这一层是纯函数：不打 HTTP，也**不许自己抄 `Data/AquariumFish` 或那 14 个装饰 ID**。
    tank: dict = field(default_factory=dict)

    def zh_of(self, name: str) -> str:
        return (self.zh or {}).get(name) or name

    def cap(self, name: str):
        """这版 DLL 认不认这个字段？→ True / None(不知道)"""
        return self.caps.get(name)

    def tile(self, x: int, y: int):
        return self.tiles.get((x, y))

    def tank_at(self, x, y):
        """🐟 **这一格那口缸**的账（键 = `"x,y"`）。

        ⚠️ **必须按坐标取**（见 `Ctx.tank` 那段）：屋里两口缸时，"最近那口"是**另一个问题的答案**。
        取不到（这版 DLL 没有 `/tank`、或那口缸没被问到）⇒ `None` ⇒ 那一行退回"开 家具"。
        """
        if not isinstance(x, int) or not isinstance(y, int):
            return None
        return (self.tank or {}).get(f"{x},{y}")

    # ⚠️ 这里原来有个 `around()` =「当前格 + 四邻」，**已删**（2026-09-27 恒纠正）。
    #    我第一版自己把「当前场景」偷偷缩成四邻（怕选项爆炸），结果 AI 站自家屋中间
    #    单子**恒空**。说好的是②**管当前地图**⇒ 候选就是 `ctx.tiles` 的**全部**。
    #    爆炸由「前 N 条 + 无损 at()」解决，**不靠掐死范围解决**。


@dataclass
class Verb:
    """一个动词 = 稳定 key + 三档 can() + **理由** + 正文渲染。

    `reason` 不是装饰，是**审计面**：AI 能据此判断"这推荐对不对"（不必盲信），
    恒一眼能看出排序错没错。**理由错了 = 判据错了**，当场现形。
    """
    key: str
    label: str
    weight: int                      # 排序权重（大的在前）
    can: Callable                    # (Ctx, target) -> True/False/None
    reason: Callable                 # (Ctx, target) -> str    ← 那截理由
    show: Callable                   # (Ctx, target) -> str    ← 正文（不含编号）
    target: str = "tile"             # 'tile' | 'held' | 'inv' | 'world'
    #   'inv' = **逐个背包格**（2026-09-30 为"饿极了列全部能吃的"加的，见 `_eat_can`）；
    #          ⚠️ 用它的动词**必须**让 `show()` 带上能区分的名字（星级前缀），
    #          否则两格同名会被并成一行、而执行器只吃 `targets[0]` = 假承诺。
    # ⚠️ **没有 exec 的动词不进单子**（所见即所得：单子上有的，按了就成）。
    #    宁可单子短，也不列"看得见按不动"的东西——那跟旧接口"列出来再说不行"是一回事。
    exec: Callable = None            # (Ctx, targets, run) -> str 回执
    # 🗂 **目录行**的下一层：(Ctx, targets) -> Level。
    #    「接了」= 有 exec（动作行）**或** 有 subs（目录行）——两个都没有才不上单子。
    subs: Callable = None
    # 多选定稿后的执行：(Ctx, [(Row, 数量)], run) -> 回执（qty 层用）
    exec_multi: Callable = None
    # True = 所有目标并成一行（执行器本来就是批量的，别假装能挑单个）
    merge: bool = False
    # 并成一行时用的理由（单个目标时用 `reason`）
    reason_many: Callable = None
    # 🗂 目录行那截「（N 件）」的出处：(Ctx, targets) -> str。
    #    不写 = 数下一层有几行。容器要报的是**箱里几件**（见 `Row.count_text`）。
    count: Callable = None
    # 🗂 分组（2026-09-29 恒：「嵌套还要分一下设备和家具」）。空 = 不进任何组。
    #    ⚠️ **只在同一层真的两组都出现时才印组头** —— 农场那种清一色的图**原样不动**
    #    （排序是照权重精心排的；没必要的分组只会把"最近的要先做"这条理由搅浑）。
    group: str = ""
    # ⚡ **执行器会不会把这一行的目标全做了**。
    #    `×N` 在单子上的语义是「**这一按会把 N 个都做了**」（收 20 台机器、捡 5 处…）。
    #    但有的执行器**只吃 `targets[0]`**（锄一格、坐一张椅子）——
    #    给它印 `×N` 就是**假承诺**：2026-09-29 真机，`锄地 ×185` 按下去只锄了 1 格
    #    （世界实查：`(50,11)` 由 `Grass` 变 `HoeDirt`、邻居一格没动，回执写 `1/1 锄出`）。
    #    ⇒ `batch=True` 才许印 `×N`；`False` 印「最近那一格 + 附近另有 N-1 格 + 一次做一格」。
    batch: bool = False
    # ⚖️ **动态权重**（`(ctx) -> int`）。给了就**盖过 `weight`**。
    #    为什么要它：排序是"这一刻最该做什么"，而有的动词的"该不该靠前"**取决于钟点**
    #    —— 最典型的是「睡觉」（2026-09-29 恒：「床…当前场景有就该置顶」）：
    #    夜里它该顶在第一行，**中午顶在第一行就是把不可逆的过夜摆在最顺手的位置**
    #    （正是恒那条「菜单是强暗示、别给不划算的路」要挡的）。静态 int 表达不了这个。
    # ⚠️ 排序**只走 `_weight_of()` 这一条路**，别的地方别再直接读 `.weight`。
    weight_fn: Callable = None
    # 🚧 **菜单态可做**：True = 这个动词**本来就是通过菜单干活**的（买/卖走 `/menu/click`、`/sell_to_shop`），
    #    所以"菜单开着"对它**不是障碍**。
    #    ⚠️ 2026-09-30 之前**没有这个位**，于是菜单开着时单子照样把世界动作（坐/搬/收）列出来、
    #       而 MCP 的菜单闸门又把 `intent do` 整个挡掉 ⇒ 整屏都是**按不动的行**。
    #       现在判据收到 `_candidates`：菜单态**只列 `menu_ok` 的**（其余整行不出现，不是灰掉）。
    menu_ok: bool = False


# ── 以下每个 can() 都只用**端点已经吐出来的**字段，一个都不用猜 ──────────

def _pick_can(ctx, t):
    """🎁 捡：判据 = **服务器递进来的那份可捡清单**（`Ctx.pick`）。

    ⚠️ 那份清单是 **`pickup_scene.scan_pickables()`** 算的 —— 名单（`BLACKLIST`）和规则
       **只在那个脚本里**，这一层**只做成员判断**（这一格在不在清单里），不打 HTTP。
    ⚠️ 2026-10-01 恒：「**复用原来的捡蛋工具**」⇒ 判据放宽成「**`objId` 以 `(O)` 开头
       （普通物件）+ 不在那份 `BLACKLIST`**」，而且**允许目标格站不住**：棚里的蛋/毛所在格
       是 `passable=False`（物件挡路），老判据 `（passable 或 forage）` 把它们全排除了
       （恒真机：鸡舍地上 17 件，`/surroundings` 给 `passable=False` 且没有 `forage` 键）。
       站不住的目标由执行侧「**站旁边 face+interact**」兜（工具里本来就有，野梅真机验过）。
    ⚠️ 远古斑点（要锄头）在那份判据里**排除**了 —— 它归「挖 远古斑点」那一行。
    ⚠️ 清单为空 ⇒ 这行不出现（恒要的"**有东西才出现，没有就不出现**，等于待办"）。
    """
    if not t:
        return CAN_NO
    keys = {tuple(k) for k in ((ctx.pick or {}).get("keys") or [])}
    if not keys:
        return CAN_NO
    return CAN_YES if (t.get("x"), t.get("y")) in keys else CAN_NO


def _pick_reason(ctx, t):
    return "游戏判可手捡"


def _pick_show(ctx, t):
    return f"捡 {t.get('object') or '地上的东西'}"


def _harvest_can(ctx, t):
    """🌾 收：游戏 `HoeDirt.readyForHarvest()`（`/surroundings` 的 `harvestable`）。

    这个键**只在有作物时才写**（C# `if (cropName != null || forageCropType != null)`），
    ⇒ 键不在 = **这格没作物**，不是"不知道"。
    """
    if not t:
        return CAN_NO
    if ctx.cap("harvestable") is None:
        return CAN_MAYBE
    return CAN_YES if t.get("harvestable") is True else CAN_NO


def _harvest_reason(ctx, t):
    # ⚠️ `crop` 是**产物 item ID**（`crop.indexOfHarvest`），不是名字 —— 拿它兜底会印出"24"。
    name = t.get("cropName") or "作物"
    extra = ""
    if t.get("cropScythe"):
        extra = " · 得用镰刀"
    return f"{name} 已成熟{extra}"


def _harvest_show(ctx, t):
    return f"收 {t.get('cropName') or '作物'}"


# ═══════════ 💧 浇水（2026-10-04 恒拍板上单） ═══════════
# 恒：「**浇水肯定是浇没湿的有作物格子**，这个**不用圈地**也可以做，而且前期洒水器没到会经常做。」
# ⇒ 行为**现成**（`water_crops()`：只浇"有作物且没浇过"的格、壶空了自己走去水边真打水、晚10点后不浇），
#    这里只做一件事：把它接上单子（**聚合行** —— 一片一次浇完，不用给矩形/半径）。
def _water_can(ctx, t):
    """💧 这一格**有作物且没浇过**才算 —— 判据**跟执行器同一把尺**（照抄它那一行）。

    ⚠️⚠️ 2026-10-04 **真机逮到的洞**（恒种了一块上古水果田给我测：32 格全干、就在 30 格内，
        可单子上**一条浇水都没有**）：C# 写这个键的约定是
        `if (watered) tile["watered"] = true;`（`ModEntry.cs:7314`）——**只在"浇过"时才写键**。
        我第一版把它当成"缺键 = 这版 DLL 不报 ⇒ MAYBE"，而**没浇过的格恰恰都没有这个键**
        ⇒ `can()` 恒不返回 True ⇒ **「浇水」那行结构性永远不会出现**。
        两处独立证据：① 真机该格 payload = `{passable, terrain:HoeDirt, crop:454, cropPhase,
        harvestable:false, cropName, cropScythe, cropRegrow}` —— **没有 `watered` 键**；
        ② `water_crops.py:45`（唯一执行器）读的就是 `not t.get("watered")`（**缺键 = 没浇**）。
    ⇒ 判据**照执行器那一行写**（`terrain==HoeDirt` + 有作物 + `not watered` + 跳过已成熟），
      这样"单子印的 N 格"与"脚本真会浇的 N 格"**是同一批**（否则又是一次假承诺）。
      📌 通式：**"只在为真时才写键"的字段（C# 那一族的约定）不许当"缺键=不知道"读** ——
        先看执行器怎么读它；`Ctx.caps` 那段讲的正是这个坑。
    """
    if not t:
        return CAN_NO
    if t.get("terrain") != "HoeDirt" or t.get("crop") is None:
        return CAN_NO          # 没翻的地/空地：浇了也没用（脚本也不浇它们）
    if t.get("harvestable"):
        return CAN_NO          # 已成熟 ⇒ 脚本显式跳过（先收），印出来就是假承诺
    # ⚠️ **缺键 = 没浇**（`ModEntry.cs:7314` 只在为真时才写键；执行器读的也是 `not watered`）
    return CAN_YES if not t.get("watered") else CAN_NO


def _water_reason(ctx, t):
    return f"{t.get('cropName') or '作物'} 该浇水了"


def _water_show(ctx, t):
    return f"浇 {t.get('cropName') or '作物'}"


# ⛔ `_dig_can`/`_dig_reason`/`_dig_show`/`_exec_dig` 2026-09-29 **整套删掉**了
#    （不是注释掉）—— 留着当死代码，下一个人只会看见"哦这儿有个现成的锄"又接回去。
#    为什么删见 `VERBS` 里那段 ⛔ 注释（恒：**"能用但不划算"的路 = 走偏的路**）。
#    判据本身没浪费：`diggable`（`/surroundings` 的 `Diggable` 地图属性）农活域那边还在用。


# 🛏 床（2026-09-29 恒：「床的重要性比其他家具大得多，**没有办法放在交互家具的选项里**。
#    反而是，**当前场景有就该置顶**。」）
#
# 数据：`scan_world` 挂的 `tile["bed"]` —— 来自 `crawl_bed locate`（"谁的床 + 在哪一格"）。
# ⚠️ **不走 `/furniture` 的 `furnitureType == 15`**：那能认出**所有** `BedFurniture`
#    （真机实测本屋 3 张：1 双人 + 2 儿童床），但**儿童床不能睡**，而"哪张是儿童床"的
#    游戏属性 `bedSize` 在这版参考程序集里**不存在** ⇒ C# 的 `IsChildBed` 也只能按名字判。
#    `crawl_bed` 走的是 `FindMasterBed`（**本来就跳过儿童床**）⇒ "能睡的床"和"是谁的床"
#    一次拿全，**一个名字名单都不用编**（本项目的老病）。

# 🛏 游戏常量：`Furniture.bed = 15`（反编译 `StardewValley.Objects/Furniture.cs:54`）。
#    ⚠️ **用游戏自己的枚举，不编名字名单** —— 真机双向对过：本屋 `furnitureType == 15`
#    恰好那 3 张床（1 双人 + 2 儿童），且**没有**"名字带床却非 15"的。
#    ⚠️ 也**不能按 `itemId` 认**：那 3 张是 `(F)2076` 和 `(F)BluePinstripeDoubleBed`
#    两种完全不同的形态 —— 名单式判据在这个项目里烂过太多次了。
FURNITURE_BED = 15

# 🌙 过了这个点，「躺一下」**整行不出现**（恒 2026-09-30）。不是"排后面"，是**不给**
#    —— 见 `_bed_can` 里为什么必须进 `can()`。
_LIE_LATE_H = 20

# 🛏 「躺一下」抬权重的线：体力或血**低于这个比例**就抬到最前。
#    恒 2026-09-29：「能不能是低 hp/体力的时候，权重提高？」—— 能，而且**游戏代码背书**
#    （反编译 `Farmer.cs:7637`：躺在床格上、联机、时间在走 ⇒ 每 500ms 体力+1、血+1）。
_LIE_LOW_FRAC = 0.30


def _bed_can(ctx, t):
    """🛏 能不能睡/躺 —— **只看"这格是不是一张能睡的床"**。

    ⚠️ **不把"够不够急"写进 `can()`**：`can()` 管"能不能"，急不急是**排序**的事
    （`_lie_weight`）。混进来 = 体力好的时候这行**整条消失**，AI 想躺下等人/等时间都找不到
    —— 那是"藏起来"，不是"排后面"，两回事。
    """
    if not (t or {}).get("bed"):
        return CAN_NO
    # 🌙 **超过 20:00 就不给这一行**（恒 2026-09-30 拍板：「**晚上不出现就好了，不需要踹**」）。
    #    ⚠️ 为什么必须进 `can()` 而不是只调权重：菜单第一条规矩是
    #       「**单子上出现的那条，按了就成**」—— 夜里把它摆上去、按了却拒绝，
    #       就是自己打自己（而且夜里体力低时它还会被顶到**第一行**）。
    #    ⚠️ 这跟恒把「睡觉」撤出单子是**同一个道理**：床不是菜单该管的事，
    #       **过夜的意图由 AI 自己带**（`daily sleep who=…`）。
    #    ⚠️ 钟读不出来（解析失败）**按"不能确定不是夜里"处理 ⇒ 不给**
    #       —— 同铁律"算不出来 ⇒ 那行不出现"。
    if _is_night(ctx):
        return CAN_NO
    return CAN_YES


def _is_night(ctx) -> bool:
    """现在过 20:00 了吗。⚠️ 读不出钟 ⇒ **当夜里**（保守：宁可不给，也别给一条按了不成的）。"""
    try:
        return int((ctx.time or "").split(":")[0]) >= _LIE_LATE_H
    except Exception:
        return True


def _bed_owner(t) -> str:
    return ((t or {}).get("bed") or {}).get("owner") or ""


def _bed_desc(t) -> str:
    o = _bed_owner(t)
    return f"（{o}的床）" if o else ""


def _is_low(ctx) -> bool:
    """⚖️ **"资源见底"的唯一判据**：体力或血**任一**低于 `_LIE_LOW_FRAC`（三成）。

    ⚠️ 2026-09-30：`躺一下` 和 `吃` 都用它 —— **同一根判据别写两份**（写两份必然漂：
    一个改了另一个忘，症状是"同一件事两行说法不一致"，本项目最不缺这种）。
    ⚠️ 上限读不出来（0）时**不误判成低**（除零 / 瞎报警都是"拿错尺子"）。
    """
    try:
        return any(m and v / m < _LIE_LOW_FRAC
                   for v, m in ((ctx.stamina, ctx.max_stamina),
                                (ctx.health, ctx.max_health)))
    except Exception:
        return False


def _lie_weight(ctx) -> int:
    """🛏 「躺一下」的权重：平时**跟别的家具交互差不多**；**血/体力低了抬到最前**。

    ⚠️ **只看资源，不看钟** —— 夜里那行压根不出现（`_bed_can` 里拦掉了），
    所以不会出现"被顶到第一行、按了却拒绝"那种自相矛盾（2026-09-30 恒：**晚上不出现就够了**）。

    ⚠️ 这条判据**不是想当然**（反编译 `Farmer.cs:7637`）——躺下**真的回**：
        `if (isInBed && Game1.IsMultiplayer && shouldTimePass()) { regenTimer = 500;
          stamina++; health++; }` ⇒ **联机下每 500ms 回 1 点体力 + 1 点血**。
        所以"快没体力了 ⇒ 躺一下"是**真有用**的路，不是"能用但不划算"那种。
    """
    return 96 if _is_low(ctx) else 68


def _lie_show(ctx, t):
    return "躺一下" + _bed_desc(t)


def _pct(v, m) -> str:
    """`25%` —— **上限读不出来（0）就说"不知道"**，别印成 `0%`（那是拿错尺子）。"""
    try:
        return f"{round(int(v) / int(m) * 100)}%" if m else "?"
    except Exception:
        return "?"


def _lie_reason(ctx, t):
    """🛏 理由栏四样（恒 2026-09-29 定前三样）：
      ① **当前百分比** —— 「算了，不设目标了。**给它当前百分比了，够不够它自己看着办**」；
      ② **还差多久** —— 每 500ms 各回 1 点（`Farmer.cs:7637`）⇒ **按当前缺口**算；
      ③ **不过夜 + 怎么才过夜** —— 免得 AI 以为躺一下＝过夜（那两条后果天差地别）。
         ⚠️ 2026-10-01 恒：「「这是躺不是睡」后面加「睡请/sleep」了吗，**不然莫名其妙警告它
         它还以为自己做出了**」⇒ **警告必须带路**：光说"不过夜"是个**没出口的否定**，
         AI 只能理解成"我是不是做错了什么"。这里补上 `daily sleep` 那条路。
      ④ （2026-10-01 补）**已经满了就说满了**。
    ⚠️ 2026-10-01 恒还砍掉一句：「**回满我叫你**」——「叫醒是异步 wake 的事，
       谁能在回满时叫醒它」。那句话（以及单子上同款的「躺满再叫你」）**两句都删了**：
       唤醒是 `_bg_block_until_wake` 的机制，**不该由一个动作行来许诺**。

    ⚠️⚠️ ②**原来算错了、真机照出来的**（2026-10-01，体力 100% 时屏上照样印「回满约 4 分钟」）：
       旧式子是 `max_stamina / 120` = **从空躺到满**的**容量**数，而文案写的是「回满约 N 分钟」
       —— 那是**容量冒充"还差多久"**（同族：把计划数当结果数）。
       现在按 `缺口 / 120` 算；缺口为 0 就直说「已经满了」（那一刻躺下去**确实没用**，
       而单子一条规矩就是"出现的那条按了就成"）。
    """
    # 每 500ms 各回 1 点 ⇒ 每分钟 120 点。缺口取**体力/血里更大的那个**（两个都在回）。
    need = max(0, (ctx.max_stamina or 0) - (ctx.stamina or 0),
               (ctx.max_health or 0) - (ctx.health or 0))
    if not ctx.max_stamina:
        eta = ""                      # 上限读不到 ⇒ **一个字都不说**（不猜，同 `_pct` 的 `?`）
    elif need <= 0:
        eta = "已经满了 · "
    else:
        eta = f"还差约 {-(-need // 120)} 分钟回满 · "
    return (f"现在 {ctx.time} · 体力 {_pct(ctx.stamina, ctx.max_stamina)}"
            f" 血 {_pct(ctx.health, ctx.max_health)} · "
            + eta + "**不过夜**（日不结束；要过夜走 `daily sleep`）")


def _exec_lie(ctx, targets, run):
    t = targets[0]
    return _receipt_from_helper("躺一下", _bed_desc(t),
                                run("lie_bed", {"who": _bed_owner(t)}))


# 🧺 **「收 已好的机器」**（2026-10-01 定形 · 恒：「完全撤出选项你觉得怎么样？」）
#
# 这一条只干一件事：**拟人走过去，把好了的机器收掉**（`machine_loader --here`，逐台真交互）。
#
# ⚠️⚠️ **"放料"故意不在单子上**（三轮改形的结论，别再往这儿加选项）：
#   · 最早它接的是**快捷路** `/machine_collect`（原子瞬收、不走路）⇒ 恒真机：「不是撤掉非拟人了吗！
#     还是一键收了hhh」；
#   · 改成"先挑料 → 再挑机器"的目录行后，恒：「这个传参好像还是有点复杂的……要传料又要传机器」+
#     「场景交互也很多，这样动可能要每次都走三级 1.收放→选机器→选料」；
#   · ⇒ **放料是"规划"**（哪件进哪类机器），按恒退役「锄」那条规矩交给原路线：
#     `farm(ops="load", kw={"item": "Starfruit", "machine_type": "Keg", "here": True})`
#     —— AI 自己带意图去调（同 `farm till` / `daily sleep`：`item`/`who` 这类"放什么/去哪儿"
#     本来就不进单子）。单子只把**出路**写在理由栏里。
#
# ⚠️ 判据全在 `Ctx.mwork`（服务器算的 `/machines` 的 status）——这一层是纯函数，不打 HTTP。


def _mwork_can(ctx, t):
    """这一刻有没有可收的 = **本图有 ready 的机器**（`/machines` 的 status）。"""
    d = ctx.mwork or {}
    return CAN_YES if int(d.get("ready") or 0) > 0 else CAN_NO


def _mwork_show(ctx, t):
    return "收 已好的机器"


def _mwork_reason(ctx, t):
    """理由栏 = **这批是什么**（产物摊开）+ 空着几台 + **放料的出路**（警告必须带路）。"""
    d = ctx.mwork or {}
    parts = []
    r, e = int(d.get("ready") or 0), int(d.get("empty") or 0)
    prod = "、".join(f"{k}×{v}" for k, v in
                     sorted((d.get("products") or {}).items(), key=lambda x: -x[1]))
    parts.append(f"本图 {r} 台好了" + (f"（{prod}）" if prod else ""))
    if e:
        parts.append(f"{e} 台空着")
    parts.append("拟人逐台收；要**收完顺手放料**走 `farm load`（`item` + `machine_type`）")
    return " · ".join(parts)


def _exec_mwork(ctx, targets, run):
    """🧺 收：走 `machine_loader --here`（**拟人、只伺候脚下这间**），把它的话原样带回来。

    ⚠️ 走 `helpers` 那档（回一句话）⇒ `_im_run` 照它**开头**判 yes/maybe/no，
       所以这里**不替它下结论**（它可能回"已后台启动 job N"——那也必须照原样说）。
    ⚠️ `item` 留空 = **只收不放**（要放料是 AI 自己调 `farm load`，见上面那段）。
    """
    r = run("mwork", {"item": "", "machine_type": "", "location": ctx.loc}) or {}
    return _receipt_from_helper("收机器", f"{int((ctx.mwork or {}).get('ready') or 0)} 台好了",
                                r)


MACHINE_V = Verb("mwork", "收 已好的机器", 88, _mwork_can, _mwork_reason, _mwork_show,
                 "world", exec=_exec_mwork, group="设备")


def _eat_can(ctx, t):
    """🍽 吃：**手上那件**随时可吃；**饿扁扁 / 快死**（`_is_low`）时，背包里**能吃的全上单子**。

    ⚠️ 恒 2026-09-30 拍板：「ai 饿扁扁或者快死的时候，把吃食物的权重提到最前」，
       并选了**扁平**形状（`1 吃海藻汤 / 2 吃沙拉`，**不再套一层"吃食…"目录行**）——理由：
         ① 铁律是"**按了就成**"：扁平每行都是直接动作，**紧急时少点一层**；
         ② 他自己点的：**下矿有自动吃食兜底** ⇒ 单子上不必为"挑哪种"留一层（省地方）。
    ⚠️ 平时**只列手持那件**（省地方；而且"此刻最该吃哪份"本来就是手上那份）。
    ⚠️ `is_edible` 的**算不出（None）照原样往上传**（三档别折叠成"不能吃"）。
    """
    if not t:
        return CAN_NO
    ed = is_edible(t)
    if ed is not True:
        return ed
    if ctx is not None and t is getattr(ctx, "held", None):
        return CAN_YES
    return CAN_YES if _is_low(ctx) else CAN_NO


def _eat_weight(ctx) -> int:
    """🍽 吃的权重：平时 **50**（跟"看"同档、压在家具批之下）；**资源见底抬到 99（最前）**。

    ⚠️ 判据就是 `_is_low`（跟「躺一下」**同一根**）—— 同族教训：同一件事别写两份判据。
    """
    return 99 if _is_low(ctx) else 50


def _eat_reason(ctx, t):
    parts = []
    if t.get("edible"):
        parts.append(f"体力 +{t['edible']}")
    if t.get("health"):
        parts.append(f"血 +{t['health']}")
    # ⚠️ 别在这写"手持"——`_where()` 已经在前面写了一次，会印成"手持 手持 · …"（同 `_read_reason`）。
    return " / ".join(parts)


def _eat_show(ctx, t):
    # ⚠️ 带**星级前缀**（`_name_with_q`）：两摞同名不同星的食物是两个背包格，
    #    标签一模一样就会被 `_render_level` 并成一行（而执行器只吃 `targets[0]`）
    #    ⇒ 那就成了"印了 ×2 却只吃一份"的假承诺。
    return f"吃 {_name_with_q(t)}"


def _read_can(ctx, t):
    """📖 看书——**识别层有、判定层没有**的典型。

    "这是不是书" 问游戏问得到（`catNum == -102`）；
    但"**这本读不读得了**"（读过的书再读没反应）游戏没有事前判据，
    只能 `performUseAction()` 返回 false 才知道（反编译定论，2026-08-29）。
    ⇒ 这里按识别层进单子，**真失败了由回执如实报**「这本读过了，没反应」。
    ⚠️ 2026-09-29：这里原来还有一句 `if t.get("read_done"): return CAN_NO`——
    全仓没有任何地方生产 `read_done`（`/state`、`/scan_chests`、`scan_backpack` 都没有），
    是个**看着像闸门、其实永远不触发**的键 ⇒ 已删。**要真有这个判据，得先有人生产它。**
    """
    if not t:
        return CAN_NO
    return is_book(t)


def _read_reason(ctx, t):
    # ⚠️ 别在这写"手持"——`_where()` 已经在前面写了一次，会印成"手持 手持"。
    return "是书（读没读过要读了才知道）"


def _read_show(ctx, t):
    return f"看 {t.get('name')}"


# ═══════════════════════════════════════════════════════════════════
# 🪑 坐 / 🛋 搬家具 / 🐾 摸 —— 2026-09-29 接线
# ═══════════════════════════════════════════════════════════════════

# 回执头一个字的**三档**（要跟正文一致，见 `_receipt_from_helper`）。
_MARK = {"yes": "✅", "maybe": "⚠️", "no": "❌"}


def _receipt_from_helper(verb_cn, desc, r, planned=""):
    """**高阶层动作**（那种 Python 里已经写好的、自己会读回验证的 op）的回执。

    ⚠️ 回执**优先用它自己的话**（`text`），别在这儿重拼一遍：
    那些 op 里带着复核（`furniture_pickup` 就是**靠前后 diff 才没报错名字**的，
    恒 2026-09-19 真机抓到过"报的是地毯、动的是椅子"）。
    我们重拼 = 把它们的复核丢掉，又回到"嘴上说成功"。

    ⚠️⚠️ **`desc` 只写"做的是哪一件"**（名字/坐标）；**计划里的数量一律走 `planned`**，
    印成「（去之前看见 N）」—— 那是**动手前**看到的事实，不是结果。
    2026-09-29 真机照出来的活标本：单子写「捡 地上的东西 ×5」、按下去**只捡到 3 颗**
    （地上还剩 2 颗，实查过），而头一行照样印「✅ 捡 **附近 5 处**」
    —— **把计划数当结果数**，正是这条规矩要挡的"嘴上说成功"。
    数字只能由**实测**来说：它自己的话里那个「拾取完成：3 个」才是结果。
    """
    if not isinstance(r, dict):
        return render_receipt(verb_cn, desc, False, note=f"回包看不懂：{r!r}")
    txt = (r.get("text") or "").strip()
    ok = bool(r.get("ok"))
    # ⚠️⚠️ 头一个字的档位**必须跟正文一致**（2026-09-29 真机抓的活标本：
    #    `✅ 搬走家具 蓝白条纹双人床` 配着正文「…**没拿起来**…物品没动。」
    #    —— 同一屏自己打自己，就是"嘴上说成功"）。
    #    三档：`yes`→✅ · `maybe`（工具自己说的 ⚠️ = **没成/存疑**）→⚠️ · 其余→❌。
    #    没带 `st` 的（裸端点那族）退回老的两档。
    _st = r.get("st") or ("yes" if ok else "no")
    head = f"{_MARK.get(_st, '❌')} {verb_cn} {desc}".rstrip()
    if planned:
        head += f"（去之前看见 {planned}）"
    if txt:
        return head + "\n   " + txt.replace("\n", "\n   ")
    if not ok:
        return render_receipt(verb_cn, desc, False, note=f"游戏回：{r.get('error') or r}")
    return render_receipt(verb_cn, desc, True)


def _sit_can(ctx, t):
    """🪑 坐——判据**问游戏**（`/sittable` 里 C# 照抄了 `GetSeatCapacity()` / `mapSeats`，
    连"吃不吃朝向"都是照抄 `Furniture.GetSittingDirection()`，见 CHANGELOG 09-11）。
    ⚠️ **坐着时不给**（要先 `scene stand` 起身）；座位满了也不给。
    """
    s = (t or {}).get("seat")
    if not s or ctx.sitting:
        return CAN_NO
    free = s.get("free")
    if free is None:
        return CAN_MAYBE
    return CAN_YES if free > 0 else CAN_NO


def _sit_show(ctx, t):
    return f"坐 {t['seat'].get('name') or '座位'}"


def _sit_reason(ctx, t):
    return "吃朝向" if (t["seat"] or {}).get("face") else ""


def _exec_sit(ctx, targets, run):
    s = targets[0]["seat"]
    r = run("sit", {"x": s.get("x"), "y": s.get("y")})
    return _receipt_from_helper("坐", s.get("name") or f"({s.get('x')},{s.get('y')})", r)


# 🪑 2026-10-04 恒：「**把坐合成一下**，这些 4-8（胡桃木椅子/红色餐椅/stool tall/stool/乡村椅）
#    做**同一个选项的第二层选择题**」⇒ 顶层只留一行「坐…（N 处）」，点开才是各把椅子/凳子。
#    ⚠️ 结构跟 `BIN`/`BIN_V` 那一对**同构**：
#      · 顶层 `SIT_PICK_V`（`world` 目录行，**key 仍是 `sit`** —— 脚本/钉子/文档引的是键）
#      · 子层 `SIT_ONE_V`（`tile`，真正干活的）**不进 `VERBS`** ⇒ 它不会自己在顶层长出一行
#        （`_candidates` 只遍历 `VERBS`）。
#    ⚠️ 为什么以前是"一把椅子一行"：`_candidates` 按 `show()` 分桶，同名座位会并成一行、
#       不同名就各占一行 —— 恒屋里五把不同名的椅子 = 五行，把单子前面挤满了。
def _sit_seats(ctx):
    """图里**现在真坐得下**的座位（`/sittable` 挂在格子上的 `seat`）——离我近的在前。"""
    if ctx.sitting:
        return []                      # 坐着时不给（要先 `scene stand` 起身）
    out = [t for t in ctx.tiles.values() if _sit_can(ctx, t) is True]
    out.sort(key=lambda t: _dist(ctx, t))
    return out


def _sit_name_count(ctx):
    """图里有**几种**座位名（"胡桃木椅子/红色餐椅/stool" 各算一种）。"""
    return len({(t.get("seat") or {}).get("name") for t in _sit_seats(ctx)})


def _sit_one_can(ctx, t):
    """🪑 **只有一种座位**时，照旧直接给「坐 木椅」那一行（省一次点击）。

    ⚠️ 两种以上才合成目录行（恒 2026-10-04：「把坐合成一下…做同一个选项的第二层选择题」
       —— 他屋里五把不同名的椅子，原来是五行把单子前面挤满）。**一种座位还分两层就是白加一次点击**。
    """
    if _sit_can(ctx, t) is not True:
        return CAN_NO
    return CAN_YES if _sit_name_count(ctx) <= 1 else CAN_NO


def _sit_pick_can(ctx, t):
    return CAN_YES if _sit_name_count(ctx) >= 2 else CAN_NO


def _sit_pick_reason(ctx, t):
    seats = _sit_seats(ctx)
    if not seats:
        return ""
    s = seats[0].get("seat") or {}
    return (f"{len(seats)} 处能坐 · 最近 {s.get('name') or '座位'}"
            f"({s.get('x')},{s.get('y')})")


def _sit_pick_count(ctx, targets):
    n = len(_sit_seats(ctx))
    return f"{n} 处" if n else None


def _sit_pick_subs(ctx, targets):
    seats = _sit_seats(ctx)
    if not seats:
        return None
    rows = [Row(SIT_ONE_V, [t], _sit_show(ctx, t), _sit_reason(ctx, t), 0)
            for t in seats]
    return Level(rows, title="坐哪儿？（一次坐一张）")


SIT_ONE_V = Verb("sit", "坐", 26, _sit_one_can, _sit_reason, _sit_show, "tile",
                 exec=_exec_sit, group="家具")# ⚠️ 键的分工：**`sit` 仍是"那行真正干活的"**（一种座位名时它直接上单子；≥2 种时它只活在第二层）
#    —— 脚本/钉子/文档引的一直是 `sit`，别把键挪走（`_VERB_BY_KEY["sit"]` 好几处在用）。
#    目录行另起一个键 `sit_pick`。
SIT_PICK_V = Verb("sit_pick", "坐", 26, _sit_pick_can, _sit_pick_reason,
                  lambda c, t: "坐", "world",
                  subs=_sit_pick_subs, count=_sit_pick_count, group="家具")


# 🐟💄📺 2026-10-04 恒：「鱼缸梳妆柜和电视……**只是没接到单子上**」＋「交互的时候**自己帮读
#    鱼缸/衣柜里有什么**返回 result，可以放什么进去和接线选项」。
#    数据**早就到了**这一层（`ctx.tiles[(x,y)]["furniture"]`，`/furniture` 给的
#    `isTV`/`isStorage`/`isFishTank`/`heldCount` —— 后两个是 2026-10-04 新加的 C# 字段，
#    没有它们鱼缸压根认不出：它的 `furnitureType` 是 9、跟普通装饰同号）⇒
#    这里只补"长一行 + 走过去开 + 把里面的东西念出来"。
def _furn_kind(f):
    """这件家具属于哪一类 —— **只认 C# 给的布尔**，别按名字猜（恒：「名单会烂」）。"""
    if (f or {}).get("isFishTank"):
        return "tank"
    if (f or {}).get("isStorage"):
        return "store"
    if (f or {}).get("isTV"):
        return "tv"
    return ""


def _furn_here(ctx):
    """本图能开的家具（电视/梳妆柜/鱼缸）——离我近的在前。"""
    out = [t for t in ctx.tiles.values() if _furn_kind(t.get("furniture"))]
    out.sort(key=lambda t: _dist(ctx, t))
    return out


def _furn_can(ctx, t):
    return CAN_YES if _furn_kind((t or {}).get("furniture")) else CAN_NO


def _furn_show(ctx, t):
    f = t.get("furniture") or {}
    k = _furn_kind(f)
    return f"{'看' if k == 'tv' else '开'} {f.get('name') or _FURN_LABEL.get(k, '家具')}"


def _furn_reason(ctx, t):
    f = t.get("furniture") or {}
    k = _furn_kind(f)
    # ⚠️ **别在这儿写坐标** —— `_where()` 已经在尾巴前面印了 `(x,y)`（第一版重复成 `(39,23) (39,23)`）
    bits = []
    if k in ("tank", "store"):
        n = int(f.get("heldCount") or 0)
        bits.append(f"里面 {n} 件" if n else "里面是空的")
    elif k == "tv":
        bits.append("看农务小贴士/明日天气")
    return " · ".join(bits)


def _exec_furn(ctx, targets, run):
    f = (targets[0].get("furniture") or {})
    # `kind` 递过去 ⇒ 回执才能按"梳妆柜/鱼缸"分别说清**放东西**那半（鱼缸菜单里放不进去）
    r = run("furn", {"x": f.get("x"), "y": f.get("y"), "kind": _furn_kind(f),
                     "w": int(f.get("width") or 0)})
    return _receipt_from_helper("开", f.get("name") or "家具", r)


def _furn_subs(ctx, targets):
    """🐟 `FURN_V` 的下一层：**鱼缸**才点得开（电视/梳妆柜照旧是"开"那个动作行）。

    ⚠️ 返回 `None` 时这一行**仍是动作行**（`开 家具`）—— 老 DLL 没 `/tank`、
       或者这口缸里外都没东西可动，都该退回老行为（别给一屏空目录）。
    ⚠️ **定义位置必须在 `FURN_V` 之前**：那个 `Verb(...)` 在模块级就要拿到这个函数对象
       （`_tank_flow` 在下面才定义，没关系 —— 那是调用时才解析的全局名）。
    """
    t = (targets or [None])[0] or {}
    if _furn_kind(t.get("furniture")) != "tank":
        return None
    return _tank_flow(ctx, t)


_FURN_LABEL = {"tv": "电视", "tank": "鱼缸", "store": "梳妆柜"}
# ⚠️ `subs=_furn_subs`：**鱼缸**那一行点开是恒要的那条包办流程（`_tank_flow`）；
#    返回 `None` 时它照旧是「开 家具」那个**动作行**（电视/梳妆柜/读不到 `/tank` 都是这一档）。
FURN_V = Verb("furn", "开 家具", 70, _furn_can, _furn_reason, _furn_show, "tile",
              exec=_exec_furn, subs=_furn_subs, group="家具")


# 🐟 2026-10-04 恒**亲自定的形状**（他看完鱼缸真相之后）：
#    「单子可以包办放吗，我以为关菜单才能放导致不能包办了。如果你打算包办的话，满缸时把取出也包了算了。
#      即：①列鱼缸里有的，**海胆要是有帽子用括号标注**；列背包里可以放入的列表类别。
#      1.取走 2.添加鱼或装饰 3.算了；②如果选 2 → 选想放的东西但是满了，再做一级选项
#      『鱼缸里这种类别满了，要与哪种进行替换？』1.A鱼 2.B鱼 3.算了 ——
#      **包办这个取→关闭菜单→手持放的替换过程**。」
#
# 形状（三层，全在这一屏上敲）：
#   鱼缸…（`FURN_V` 的目录行）  ← 标题带容量；两行的理由栏就是恒要的那两张单子
#     ① 取走…           → 里面每件一行（`tank_take`）
#     ② 添加鱼或装饰…    → 背包里"游戏收的"每件一行：
#                            · `room=True` ⇒ 直接放（`tank_add`）
#                            · `room=False` 且**能靠取出腾位** ⇒ 再下一层「要与哪种进行替换？」
#                              （`tank_swap`：取 → 收界面 → 放，全包办）
#                            · `block="duplicate"`（宽缸里已经有一个同款装饰）⇒ **不给替换层**
#                              （取出任何东西都救不了它 —— 给了就是**假门**）
#     0 这些都不是（返回上一层 —— 单子自己给的出口）
#
# ⚠️ 判据全在服务器的 `_im_tank`（问 C# `/tank` = 游戏自己的 `CanBeDeposited` /
#    `HasRoomForThisItem` / `GetCategoryFromItem` / `TankFish.CanWearHat`）—— 这一层纯消费：
#    不打 HTTP、**不抄 `Data/AquariumFish`、也不抄那 14 个装饰 ID**（名单会烂，本项目老病）。
# ⚠️ 三个动作 verb **不进 `VERBS`**（跟箱子的 `OPEN_V`/`TAKE_V`/`STORE_V` 同款）：它们只在
#    **子层**里出现，顶层候选里不该有"取走/添加"这种没有目标的空行。
# ⚠️ `menu_ok=True`：**取**本来就是**通过缸的 `ShopMenu`** 干的；**添加/替换**内部会
#    **自己把界面收掉**（恒那句"关菜单才能放"就是它们包办的）⇒ 菜单开着时这几行也该能敲
#    （否则恒那条"取 → 关菜单 → 放"的链子在单子上会断成两截）。
_TANK_CAT_ZH = {"Swim": "游鱼", "Ground": "底层生物", "Decoration": "装饰"}


def _tank_cat_zh(it):
    """这一件在缸里算哪一类 —— **用游戏 `GetCategoryFromItem` 的原话**（服务器递进来的）。"""
    if (it or {}).get("isHat"):
        return "帽子"
    c = (it or {}).get("category") or "?"
    return _TANK_CAT_ZH.get(c, c)


def _tank_item_zh(it) -> str:
    """一件东西写成「名字(类别)」——**戴了帽子就把帽子用括号标出来**（恒 ① 那句）。"""
    it = it or {}
    nm = it.get("displayName") or it.get("name") or "?"
    st = int(it.get("stack") or 1)
    hat = it.get("wornHat")
    inner = f"{_tank_cat_zh(it)}·戴「{hat}」" if hat else _tank_cat_zh(it)
    return f"{nm}" + (f"×{st}" if st > 1 else "") + f"（{inner}）"


def _tank_name(tk) -> str:
    return (tk or {}).get("name") or "鱼缸"


def _tank_inside(tk) -> list:
    return (tk or {}).get("inside") or []


def _tank_inv(tk) -> list:
    return (tk or {}).get("inventory") or []


def _tank_addable(tk) -> list:
    return [i for i in _tank_inv(tk) if i.get("room")]


def _tank_blocked(tk) -> list:
    return [i for i in _tank_inv(tk) if not i.get("room")]


def _tank_cap_line(tk) -> str:
    """容量那一行（**游戏口径**：`capacity`/`counts`/`hatsAllowed` 全是 C# `/tank` 给的）。"""
    tk = tk or {}
    cap, cnt = tk.get("capacity") or {}, tk.get("counts") or {}
    bits = []
    for k in ("Swim", "Ground", "Decoration"):
        c = cap.get(k)
        if c is None:
            continue
        used = int(cnt.get(k) or 0)
        bits.append(f"{_TANK_CAT_ZH.get(k, k)} {used}/{int(c)}" if int(c) >= 0
                    else f"{_TANK_CAT_ZH.get(k, k)} {used} 件（宽缸**每种各 1**）")
    ha, hi = int(tk.get("hatsAllowed") or 0), int(tk.get("hatsInside") or 0)
    bits.append(f"帽子 {hi}/{ha}" if ha else "帽子 0（缸里没有可戴帽的生物）")
    return " · ".join(bits)


def _tank_tile_can(ctx, t):
    """这三个动作只对**点的那一格那口缸**成立 —— 顶层候选里不该出现"取走/添加"这种没目标的空行。"""
    if _furn_kind((t or {}).get("furniture")) != "tank":
        return CAN_NO
    return CAN_YES if ctx.tank_at((t or {}).get("x"), (t or {}).get("y")) else CAN_NO


def _tank_reason_take(ctx, t):
    tk = ctx.tank_at((t or {}).get("x"), (t or {}).get("y")) or {}
    ins = _tank_inside(tk)
    txt = "里面：" + "、".join(_tank_item_zh(i) for i in ins[:6]) + ("…" if len(ins) > 6 else "")
    # ⚠️ **戴帽子那几只单独再点一次名**：缸里东西多的时候上面那截会被 `…` 截掉，
    #    而"谁戴着哪顶帽子"正是恒要的那条信息（2026-10-04 真机：海胆排在第 8 个，
    #    第一屏那一行里根本看不见它）。
    hats = [i for i in ins if i.get("wornHat")]
    if hats:
        txt += " ｜ 戴着帽子：" + "、".join(
            f"{i.get('displayName') or i.get('name')}（{i.get('wornHat')}）" for i in hats[:3])
    return txt


def _tank_reason_add(ctx, t):
    tk = ctx.tank_at((t or {}).get("x"), (t or {}).get("y")) or {}
    add, blk = _tank_addable(tk), _tank_blocked(tk)
    bits = []
    if add:
        bits.append("能放：" + "、".join(_tank_item_zh(i) for i in add[:4])
                    + ("…" if len(add) > 4 else ""))
    if blk:
        # ⚠️ 「会问你换哪一件」**只在真有替换层时**才说 —— 老 DLL（没有 `replaceWith`）上
        #    那句话是**空承诺**（点进去一件都没有）。2026-10-04 真机就是这么照出来的。
        _swapable = any(i.get("replaceWith") for i in blk)
        bits.append("满了：" + "、".join(_tank_item_zh(i) for i in blk[:3])
                    + ("…" if len(blk) > 3 else "")
                    + ("（满了的会问你换哪一件）" if _swapable
                       else "（这几件放不进：这一类满了，先取出一件同类的再来）"))
    return " ｜ ".join(bits) if bits else "背包里没有游戏收的东西"


def _tank_flow(ctx, tile) -> "Level":
    """🐟 鱼缸那一屏（恒 ①：取走 / 添加 / 算了）。→ `Level` / `None`（没行可出）。"""
    tk = ctx.tank_at((tile or {}).get("x"), (tile or {}).get("y"))
    if not tk:
        return None                       # 读不到这口缸（老 DLL / 没问到）⇒ 退回"开 家具"
    ins, add, blk = _tank_inside(tk), _tank_addable(tk), _tank_blocked(tk)
    rows = []
    base = dict(tile or {})
    if ins:
        # ⚠️ 标签**不写尾巴那个 `…`** —— 目录行的省略号由渲染层统一加（写了会变成「取走……」）。
        rows.append(Row(TANK_TAKE_V, [dict(base)], "取走", _tank_reason_take(ctx, base), 0,
                        level=_tank_take_level(ctx, base, tk)))
    if add or blk:
        rows.append(Row(TANK_ADD_V, [dict(base)], "添加鱼或装饰", _tank_reason_add(ctx, base), 0,
                        level=_tank_add_level(ctx, base, tk)))
    if not rows:
        return None
    return Level(rows, title=f"{_tank_name(tk)} · 里面 {len(ins)} 件 · {_tank_cap_line(tk)}")


def _tank_take_level(ctx, tile, tk) -> "Level":
    rows = []
    for i in _tank_inside(tk):
        rows.append(Row(TANK_TAKE_V, [dict(tile, tank_item=dict(i))],
                        f"取 {_tank_item_zh(i)}", "取出来进背包（走缸的界面，价 0）", 0))
    return Level(rows, title=f"从{_tank_name(tk)}里取哪一件？（一次一件）")


def _tank_add_level(ctx, tile, tk) -> "Level":
    """「往缸里放什么」那层：有位的直接放；满了的**再开一层选换掉哪件**（恒 ②）。"""
    rows = []
    for i in _tank_addable(tk):
        rows.append(Row(TANK_ADD_V, [dict(tile, tank_item=dict(i))],
                        f"放 {_tank_item_zh(i)}", "有位，直接放（手持 + 右键缸本体）", 0))
    dups = []
    for i in _tank_blocked(tk):
        if i.get("block") == "duplicate":
            # ⚠️ **不给它一行**：宽缸那档是"同一种不能放第二个"，**取出任何东西都救不了**
            #    ⇒ 给一层"要替换哪件"就是**假门**（按了必不成）；给自己一行也是"按了不成"。
            #    照实写进标题就行（恒 ② 那层只留给"真能靠取出腾位"的那些）。
            dups.append(_tank_item_zh(i))
            continue
        lv = _tank_swap_level(ctx, tile, i)
        if lv is None:
            continue
        rows.append(Row(TANK_ADD_V, [dict(tile, tank_item=dict(i))],
                        f"放 {_tank_item_zh(i)}", "这一类满了 ⇒ 点开选**换掉缸里哪一件**", 0,
                        level=lv))
    title = f"往{_tank_name(tk)}里放什么？（一次一件）"
    if dups:
        title += " · 缸里已有同款、放不进的：" + "、".join(dups[:3]) + "（宽缸每种各 1）"
    return Level(rows, title=title)


def _tank_swap_level(ctx, tile, add_item) -> "Level":
    """「鱼缸里这种类别满了，要与哪种进行替换？」—— 恒 ② 那一层。

    ⚠️ **同款合并**：缸里三条一样的大海参只该占**一行**（`/menu/click` 按名字取的本来就是
       "第一个同名的"，分成三行等于假装能挑具体哪一条 —— 2026-10-04 真机上是
       `1 大海参 / 2 大海参 / 4 大海参` 三行同名）。合并时把件数写进标签。
    """
    rows, seen = [], {}
    for rw in (add_item.get("replaceWith") or []):
        key = rw.get("itemId") or rw.get("displayName") or rw.get("name")
        if key in seen:
            seen[key] += 1
            continue
        seen[key] = 1
        rows.append(Row(TANK_SWAP_V, [dict(tile, tank_add=dict(add_item), tank_take=dict(rw))],
                        f"{rw.get('displayName') or rw.get('name') or '?'}",
                        f"用它换出「{rw.get('displayName') or rw.get('name')}」"
                        f"（取 → 收界面 → 放，**全包办**）", 0))
    for r in rows:
        key = (r.targets[0].get("tank_take") or {}).get("itemId")
        if seen.get(key, 1) > 1:
            r.label = f"{r.label}×{seen[key]}"
    if not rows:
        return None
    return Level(rows, title=f"{_tank_cat_zh(add_item)}满了：要与**哪一种进行替换**？"
                             f"（换上：{add_item.get('displayName') or add_item.get('name')}）")


def _tank_find(tk, want_id, want_name, where):
    """从**新鲜**的那口缸的账里找回那一件（`where` = "inside"/"inventory"）。

    ⚠️ 单子的号**不跨屏**，但世界会动（那件可能已经被取走/放进去了）⇒ 执行前按 **itemId**
       在**现在的**账里再找一遍；找不到就**如实说**（别拿渲染时那份旧字典硬做）。
    """
    src = _tank_inside(tk) if where == "inside" else _tank_inv(tk)
    for i in src:
        if want_id and (i.get("itemId") or "") == want_id:
            return i
        if (not want_id) and ((i.get("displayName") or i.get("name")) == want_name):
            return i
    return None


def _tank_of(ctx, targets):
    """这一行的目标 = **哪一格那口缸** → `(x, y, tk)`；取不到回 `(None, None, None)`。

    ⚠️ 坐标**从行上取**（`targets[0]`），账**按坐标查** —— 这两件事必须同源，
       否则就是"按 A 缸的判断动 B 缸"（2026-10-04 真机照到的那个形状错误）。
    """
    t0 = (targets or [{}])[0] or {}
    x, y = t0.get("x"), t0.get("y")
    return x, y, ctx.tank_at(x, y)


def _exec_tank_take(ctx, targets, run):
    x, y, tk = _tank_of(ctx, targets)
    if not tk:
        return "⏳ 读不到这一格的鱼缸账（单子是**上一次**看的）—— 敲 `show` 重开一张"
    t0 = (targets or [{}])[0]
    stale = (t0.get("tank_item") or {})
    it = _tank_find(tk, stale.get("itemId"), stale.get("displayName") or stale.get("name"), "inside")
    if it is None:
        return "⏳ 缸里已经没有这一件了（单子是**上一次**看的）—— 敲 `show` 重开一张"
    r = run("tank_take", {"x": x, "y": y,
                          "item": it.get("displayName") or it.get("name") or "",
                          "item_id": it.get("itemId") or ""})
    return _receipt_from_helper("取", f"{it.get('displayName') or it.get('name')} 出{_tank_name(tk)}", r)


def _exec_tank_add(ctx, targets, run):
    x, y, tk = _tank_of(ctx, targets)
    if not tk:
        return "⏳ 读不到这一格的鱼缸账（单子是**上一次**看的）—— 敲 `show` 重开一张"
    t0 = (targets or [{}])[0]
    stale = (t0.get("tank_item") or {})
    it = _tank_find(tk, stale.get("itemId"), stale.get("displayName") or stale.get("name"),
                    "inventory")
    if it is None:
        return "⏳ 背包里已经没有这一件了（单子是**上一次**看的）—— 敲 `show` 重开一张"
    if not it.get("room"):
        # ⚠️ 渲染时是"有位"、执行时已经满了（这中间世界动了）⇒ **不硬放**，说清并指路那一层。
        if it.get("block") == "duplicate":
            return (f"⏳ 缸里**已经有一个同款「{it.get('displayName') or it.get('name')}」**了"
                    f"（宽缸每种各 1）—— 换别的种类才放得进")
        return ("⏳ 这一刻这一类**已经满了**（你手上那张单子是上一次看的）—— 敲 `show` 重开一张，"
                "它会给你「要与哪种进行替换？」那一层")
    r = run("tank_add", {"x": x, "y": y,
                         "item": it.get("displayName") or it.get("name") or "",
                         "item_id": it.get("itemId") or ""})
    return _receipt_from_helper("放", f"{it.get('displayName') or it.get('name')} 进{_tank_name(tk)}", r)


def _exec_tank_swap(ctx, targets, run):
    x, y, tk = _tank_of(ctx, targets)
    if not tk:
        return "⏳ 读不到这一格的鱼缸账（单子是**上一次**看的）—— 敲 `show` 重开一张"
    t0 = (targets or [{}])[0]
    sa, st = (t0.get("tank_add") or {}), (t0.get("tank_take") or {})
    add_it = _tank_find(tk, sa.get("itemId"), sa.get("displayName") or sa.get("name"), "inventory")
    take_it = _tank_find(tk, st.get("itemId"), st.get("displayName") or st.get("name"), "inside")
    if add_it is None:
        return "⏳ 要放的那件**已经不在背包里**了（单子是上一次看的）—— 敲 `show` 重开一张"
    if take_it is None:
        return "⏳ 要换出来的那件**已经不在缸里**了（单子是上一次看的）—— 敲 `show` 重开一张"
    nm_a = add_it.get("displayName") or add_it.get("name") or "?"
    nm_t = take_it.get("displayName") or take_it.get("name") or "?"
    r = run("tank_swap", {"x": x, "y": y,
                          "item": nm_a, "item_id": add_it.get("itemId") or "",
                          "take": nm_t, "take_id": take_it.get("itemId") or ""})
    return _receipt_from_helper("换", f"{nm_a} ⇄ {nm_t}（缸里）", r)


# ⚠️ 这三个**不进 `VERBS`**（只在子层出现，跟箱子的 `OPEN_V`/`TAKE_V`/`STORE_V` 同款）。
TANK_TAKE_V = Verb("tank_take", "取走", 0, _tank_tile_can, _tank_reason_take,
                   lambda c, t: "取走", "tile", exec=_exec_tank_take, menu_ok=True)
TANK_ADD_V = Verb("tank_add", "添加", 0, _tank_tile_can, _tank_reason_add,
                  lambda c, t: "添加", "tile", exec=_exec_tank_add, menu_ok=True)
TANK_SWAP_V = Verb("tank_swap", "替换", 0, _tank_tile_can,
                   lambda c, t: "替换", lambda c, t: "替换", "tile", exec=_exec_tank_swap,
                   menu_ok=True)


# ═══════════════════════════════════════════════════════════════════
# 🏛️ 献祭板「一件件物品捧上槽位」（2026-10-04 · 恒 A 批）
# ═══════════════════════════════════════════════════════════════════
# 恒原话：「**看看你觉得方便，又保留一种一件件物品捧上槽位的趣味感**？」
# ⇒ 形状**照鱼缸那条**（都是"一层层进去、一件一件动手"），但**不做"一键捐完"**：
#    顶层一行 →（点开）本间**手上能捧上东西**的包各一行 →（点开）**一件一行**「捧上 鲤鱼」。
#    按下去就是**真人那两步**：点底部背包那格（`inventory.leftClick` 把那件拿到光标）→
#    点那一格槽位（游戏自己 `canAcceptThisItem`→`tryToDepositThisItem` /
#    `HandlePartialDonation`）—— **投料动画、音效、"格子被填上"全是游戏自己的**，
#    回执再把「背包 ×3 → ×2 · 那一格 0/1 → 1/1」念出来（这就是那个趣味感的账）。
# ⚠️ 判据**全在游戏**（`/menu.characterCust` 的 `canGive`/`donatables`/`ingredientSlots`）：
#    这一层**不认 id、不认类别号、不认品质**（类别型需求 `-4`=鱼 / `-5`=蛋 只有游戏分得清）。
# ⚠️ **老 DLL 没有那两栏**（`new_fields=False`）⇒ 这条**整条不出行**，退回 `menu read` 那张
#    「图标↔收集包」表（2026-09-25 那份，仍然是权威）—— 宁可不给，也不给一行按了不成的。
def _cc_bundles(ctx) -> list:
    return [b for b in ((ctx.cc or {}).get("bundles") or []) if isinstance(b, dict)]


def _cc_workable(ctx) -> list:
    """**手上现在就能捧上东西**的包（`give` 非空）—— 单子只列这些（按了不成的别占行）。
    ⚠️ `give` 由服务器给，而服务器那份**已经把"已完成的包"滤掉了**（真机 2026-10-05：
    整包完成时游戏把每一格都标成已放，旧口径会继续报「还差 4 件」+ 4 行按了不成的「捧上 …」）。"""
    return [b for b in _cc_bundles(ctx) if b.get("give")]


def _cc_give_all(ctx) -> int:
    return sum(len(b.get("give") or []) for b in _cc_workable(ctx))


def _cc_missing_line(b) -> str:
    """「这一包还能填什么」——⚠️ 措辞必须跟**游戏的口径**一致：完成看**格数**
    （`numberOfIngredientSlots`，填满就算成），不是"每样材料都交齐"。
    写成「还差 N 件（必须给）」就是**劝人做不需要的事**（真机 2026-10-05 恒当场点破）。"""
    miss = b.get("missing") or []
    if not miss:
        return ""
    bit = "、".join(f"{m.get('name') or '?'}" + (f"×{m.get('need')}" if (m.get("need") or 1) > 1 else "")
                    for m in miss[:4])
    if len(miss) > 4:
        bit += f"…（共 {len(miss)} 样）"
    slots = b.get("slots")
    tail = f"**这一包 {slots} 格 · 填满就算成**（不必每样都交）" if slots else "填满就算成"
    return f"还能填：{bit} ｜ {tail}"


def _cc_can(ctx, t):
    """🏛️ 只在**这版 DLL 报得出"能捧上什么"**、且真开着献祭板、且**有得捧**时才给这一行。"""
    cc = ctx.cc or {}
    if not cc or not cc.get("new_fields"):
        return CAN_NO
    return CAN_YES if (_cc_workable(ctx) or cc.get("specific")) else CAN_NO


def _cc_show(ctx, t):
    cc = ctx.cc or {}
    if cc.get("specific"):
        b = next((x for x in _cc_bundles(ctx) if x.get("index") == cc.get("current")), None)
        if b:
            return f"献祭板·正翻着「{b.get('name') or b.get('index')}」"
    return f"献祭板（{cc.get('area') or '本间'} · 手上能捧上 {_cc_give_all(ctx)} 件）"


def _cc_reason(ctx, t):
    cc = ctx.cc or {}
    if cc.get("specific"):
        b = next((x for x in _cc_bundles(ctx) if x.get("index") == cc.get("current")), None)
        if b:
            return f"{_cc_missing_line(b) or '这一包要的都齐了'} · 一件一件捧上去"
    return (f"本间 {len(_cc_bundles(ctx))} 包还没做完 · **手上就能捧上 {_cc_give_all(ctx)} 件**"
            f"（一件一行，投料动画是游戏自己的）")


# ⚠️ 三个子动词都带 `menu_ok=True`：`do_row` 在**菜单态**会拦"菜单态做不了的动作"的号，
#    而这一族**整族都活在菜单态里**（不写这个位，点开第二层就被自己人挡住——自验当场撞到过）。
CC_BUNDLE_V = Verb("cc_bundle", "开", 0, lambda c, t: CAN_YES, None,
                   lambda c, t: f"开「{t.get('name') or t.get('index')}」", "world", menu_ok=True)


def _exec_cc_offer(ctx, targets, run):
    """🏛️ 「捧上 这一件」—— 执行侧那一句话来自服务器（它自己回读"背包少了没"）。"""
    t = (targets or [{}])[0]
    g = t.get("give") or {}
    return _receipt_from_helper("捧上", f"「{g.get('name') or '?'}」",
                                run("cc_offer", {"item_id": g.get("id") or "",
                                                 "name": g.get("name") or ""}))


def _exec_cc_back(ctx, targets, run):
    """🏛️ 返回收集包列表 —— 服务器那句话**本身就是完整回执**（`✅ 回到…` / `⚠️ 手上还拿着…`），
    所以**原样带出去**，不再套一层 `✅ 返回`（那样一屏两个 ✅，跟"同一屏自己打自己"同族）。"""
    r = run("cc_back", {})
    if isinstance(r, dict):
        return (r.get("text") or "").strip() or _receipt_from_helper("返回", "", r)
    return _receipt_from_helper("返回", "", r)


def _cc_offer_rows(b) -> list:
    """这一包里**手上能捧上的每一件**各一行（个数/需求数都印在理由栏里）。

    ⚠️ 同一件（同 `id` + 同背包格）**可能对上这一包的多个格子** —— 真机现成的例子：
    「建筑」包要 **2 格木材 ×99**，于是一件 198 的木材会给出**两条 `give` 记录**
    ⇒ 照记录直接出两行就是**两行一模一样的「捧上 木材」**（假装能挑哪一格；
    跟 2026-10-04 鱼缸替换层"三行同名大海参"是同一个形状）。
    ⇒ 按 `(id, 背包格)` **合并成一行**，在理由里写明「这一包要它 N 格（捧一次填一格）」。
    """
    groups = {}
    for g in (b.get("give") or []):
        if not isinstance(g, dict):
            continue
        key = (g.get("id") or g.get("name") or "?", g.get("slot"))
        groups.setdefault(key, []).append(g)
    rows = []
    for (fid, _slot), gs in groups.items():
        g = gs[0]
        need = int(g.get("need") or 1)
        n_slots = len(gs)
        why = (f"背包 ×{int(g.get('count') or 0)} · 这一格要「{g.get('want') or g.get('name')}」"
               + (f"×{need}" if need > 1 else "")
               + (f" · **这一包要它 {n_slots} 格**（捧一次填一格）" if n_slots > 1 else "")
               + ("（一次就能填满）" if g.get("full") else "（先放一部分，凑齐了它自己算完成）"))
        rows.append(Row(CC_OFFER_V, [{"give": dict(g)}], f"捧上 {g.get('name') or '?'}", why, 0))
    return rows


def _cc_page_level(b) -> "Level":
    rows = _cc_offer_rows(b)
    rows.append(Row(CC_BACK_V, [{}], "返回收集包列表", "回去看本间还有哪几包能捧（不丢东西）", 0))
    title = (f"「{b.get('name') or b.get('index')}」 —— 捧哪一件上去？"
             + (f"\n  {_cc_missing_line(b)}" if _cc_missing_line(b) else ""))
    return Level(rows, title=title)


def _cc_flow(ctx, targets):
    """🏛️ 献祭板那两层 —— 由**游戏自己的状态**决定给你看哪一层（不是靠我们记"点到第几层"）。"""
    cc = ctx.cc or {}
    if not cc or not cc.get("new_fields"):
        return None
    if cc.get("specific"):
        b = next((x for x in _cc_bundles(ctx) if x.get("index") == cc.get("current")), None)
        if b:
            return _cc_page_level(b)
    rows = []
    for b in _cc_workable(ctx):
        rows.append(Row(CC_BUNDLE_V, [{"index": b.get("index"), "name": b.get("name")}],
                        f"开「{b.get('name') or b.get('index')}」",
                        f"{_cc_missing_line(b)} · 手上能捧上 {len(b.get('give') or [])} 件", 0,
                        level=_cc_page_level(b)))
    if not rows:
        rows.append(Row(CC_BACK_V, [{}], "返回收集包列表", "这一层没有能捧的", 0))
    done = [b for b in _cc_bundles(ctx) if b.get("complete")]
    return Level(rows, title=(f"{cc.get('area')} · 本间 {len(_cc_bundles(ctx))} 包"
                              f"（已做完 {len(done)} 包）—— 先开哪一包？"))


CC_V = Verb("cc", "献祭板", 74, _cc_can, _cc_reason, _cc_show, "world",
            subs=_cc_flow, menu_ok=True)


# 🎁 「领 收集包奖励」—— 恒 2026-10-05 真机：「**然后有奖励可以领**」。
#    它是列表页上游戏自己的**礼物按钮**（`presentButton` → `openRewardsMenu()` → 一个 ItemGrabMenu）：
#    以前那个按钮**不在我们的按钮表里** ⇒ AI 眼里根本没有"领奖励"这条路（缺门）。
#    ⚠️ 判据 = 服务器从 `/menu.buttons` 里认出了 `presentButton`（有它 = 本间真有奖可领）。
#    ⚠️ 权重 80：那一刻它就是**正事**（跟精通碑/任务日志那两条"领取"同一档）。
def _cc_gift_can(ctx, t):
    return CAN_YES if (ctx.cc or {}).get("gift") else CAN_NO


def _cc_gift_show(ctx, t):
    return "领 收集包奖励"


def _cc_gift_reason(ctx, t):
    return "本间有已完成收集包的奖励 · 点游戏自己的礼物按钮，开出来照「箱子里…」一件件取"


CC_GIFT_V = Verb("cc_gift", "领 收集包奖励", 80, _cc_gift_can, _cc_gift_reason,
                 _cc_gift_show, "world",
                 exec=lambda c, t, run: _exec_chore(c, t, run, "cc_gift", "领"),
                 menu_ok=True)


# 🏛️ 「看 献祭板（走过去）」—— **世界侧**入口（恒 2026-10-05 定的三条件：地点 / 背包 / 板子在）。
#    ⚠️ `menu_ok` **故意不设**：它只在**没开菜单**时才该出现（板子没开），给了这个位
#       就会在别的菜单开着时也劝 AI 去走位（那是菜单闸门要挡的事）。
#    ⚠️ 判据全在服务器（`Ctx.cc_board` = `_im_cc_board`）：这一层不认地点名、也不认"包里有没有货"。
def _cc_go_can(ctx, t):
    if (ctx.cc or {}):            # 板子已经开着 ⇒ 该走的是那三层，不是这一行
        return CAN_NO
    return CAN_YES if (ctx.cc_board or {}) else CAN_NO


def _cc_go_show(ctx, t):
    # 🏚 补25：废弃超市那间是**第 6 区「遗失的收集包」**（`AbandonedJojaMart`），
    #    板子跟社区中心同一套 ⇒ 同一行、换个更准的说法（免得 AI 在废弃超市里找"献祭板"找不到）。
    _a = str((ctx.cc_board or {}).get("area") or "")
    if ("遗失" in _a) or ("废弃" in _a):
        return "看 收集包板子（走过去）"
    return "看 献祭板（走过去）"


def _cc_go_reason(ctx, t):
    cb = ctx.cc_board or {}
    area = cb.get("area") or "本间"
    have = int(cb.get("have") or 0)
    return (f"{area} 的板子还在 · 你包里有 {have} 件它收的"
            f" · 走过去交互把板子开出来")


CC_GO_V = Verb("cc_go", "看 献祭板", 74, _cc_go_can, _cc_go_reason, _cc_go_show, "world",
               exec=lambda c, t, run: _receipt_from_helper(
                   "看 献祭板", "",
                   run("cc_go", {"x": (c.cc_board or {}).get("x"),
                                 "y": (c.cc_board or {}).get("y")})))


# 🏛️ 「去博物馆捐赠（包里 N 件可捐）」—— **世界侧**入口（补24c；恒 2026-10-05：
#    「背包有可捐能跟献祭一样打标吗？」）。形状与判据位置**照 `CC_GO_V` 那条一比一镜像**。
#    ⚠️ `menu_ok` **故意不设**（同 `CC_GO_V`）：它只在**没开菜单**时才该出现 —— 开着菜单时
#       这一行会劝 AI 去走位，而那件事归菜单闸门管。
#    ⚠️ 这一层**不认物品类别、也不判"捐过没有"**（那是游戏自己的尺子：已捐过 ⇒ `donatable=false`）
#       ⇒ 这里只数件数；`0` 件 ⇒ `Ctx.museum_go` 是 `{}` ⇒ 那行不出现。
#    ⛔ 2026-10-05（补24e）**这里原来写着"不限地点"—— 那就是假门**：执行侧 `museum_donate()`
#       第一句 `_require_counter("博物馆(柜台)", …)` **只在同图带路、跨图直接拒绝** ⇒ 人在别处也会出这行、
#       按下去只报「不在ArchaeologyHouse，不能操作」（**真机验收当场逮到**：AI 在 JojaMart、
#       包里有 1 件没捐过的碧玉 ⇒ 那行出现、敲下去什么都没发生）。
#       ⇒ 现在**跟献祭那行一样要地点**：闸在**服务器**（`_im_museum_go` 比地名 —— 这一层不认地名，
#         见 `_cc_go_can` 上面那段注释的规矩）；跨图要过去走普通的 `map go 博物馆(柜台)`，
#         **别把"走过去"和"捐"揉进同一行**。
def _museum_go_can(ctx, t):
    return CAN_YES if int((ctx.museum_go or {}).get("have") or 0) > 0 else CAN_NO


def _museum_go_show(ctx, t):
    return f"去博物馆捐赠（包里 {int((ctx.museum_go or {}).get('have') or 0)} 件可捐）"


def _museum_go_reason(ctx, t):
    n = int((ctx.museum_go or {}).get("have") or 0)
    return (f"包里 {n} 件博物馆还没收的（游戏自己判的：没捐过、且是古物/矿物）"
            f" · 走过去交给柜台，捐完才算成")


MUSEUM_GO_V = Verb("museum_go", "去博物馆捐赠", 73, _museum_go_can, _museum_go_reason,
                   _museum_go_show, "world",
                   exec=lambda c, t, run: _receipt_from_helper(
                       "去博物馆捐赠", "", run("museum_go", {})))
# ⚠️ 子层那两个动词**必须在 exec 函数之后**建（`exec=` 是**定义时求值**的）——
#    写在前面就是 NameError（这一批我自己踩了一次，import 当场炸）。
CC_OFFER_V = Verb("cc_offer", "捧上", 0, lambda c, t: CAN_YES, None,
                  lambda c, t: f"捧上 {t.get('name')}", "world", exec=_exec_cc_offer,
                  menu_ok=True)
CC_BACK_V = Verb("cc_back", "返回收集包列表", 0, lambda c, t: CAN_YES, None,
                 lambda c, t: "返回收集包列表", "world", exec=_exec_cc_back,
                 menu_ok=True)


# 🪑 「起身」（2026-10-01）—— **坐着时的唯一出路**。
#
# ⚠️ 缺口是这么来的：`_sit_can` 见 `ctx.sitting` 就回 `CAN_NO`（坐着不该再给「坐」，
#    那一句本身是对的）—— 可**没有别的行接上**，于是：
#      「坐 木椅」是单子**推荐**的动作 → 按下去坐下 → **单子上从此既没有「坐」也没有「起身」**
#    ⇒ 单子把 AI 领进了一个**自己不给出口**的姿势。它想走动 / 搬东西 / 躺下，
#      只能靠"记得住 scene 域里有个 stand"（这层的承诺是"看单子就够了"，不该要它背）。
#    📌 **床上那份我一度以为同款病 —— 查清了，不是**（2026-10-01 真机单变量测过）：
#      · `isInBed` **不是"主动躺下了"**，它就是"**脚下这格是床**"
#        （`Farmer.cs:7553` 每 tick 从瓦片属性重算）⇒ 站在双人床的任一半上就恒为 true；
#      · 床上 `key menu` **照常开界面**（真机验过：`activeMenu=GameMenu`）；
#      · 想离开**走一步就行**（实测走开后 `isInBed` 自动变 false）—— 单子上那些行本来都要走动。
#      ⇒ **没有"陷在床上"这回事，不需要「下床」那一行。**
#    ⚠️ 我最初写在这儿的原话是「轮回躺在床上时 `key cancel` 开不出界面（按键被游戏吃掉）」——
#      **那句是错的、已删**：真相是我自己两个错叠出来的（`walk_to` 漏传 `location` → 400；
#      拿 `key cancel` 去"开界面"，而它没菜单时是**挥工具**，本来就不可能开）。
#      **别把两个自己的错读成游戏行为** —— 这是那条"先怀疑自己的尺子"的新样本。
#
# ⚠️ `exec` 走现成的 `stand()`：它自己会轮询确认（`StopSitting` 的 lerp 要 0.3~0.5s 才收尾），
#    成没成**由它说**，这一层不替它下结论。
def _stand_can(ctx, t):
    return CAN_YES if ctx.sitting else CAN_NO


def _stand_show(ctx, t):
    return "起身"


def _stand_reason(ctx, t):
    # ⚠️ 理由要说清"这一刻为什么有它" —— 否则 AI 会以为「起身」是随时能按的通用动作。
    return "坐着 · 起来才能走动 / 搬东西"


def _exec_stand(ctx, targets, run):
    r = run("stand", {})
    return _receipt_from_helper("起身", "", r)


# 🛋⛔ **「搬走」2026-10-02 撤出单子**（恒拍板，恒原话：「更多涉及家居装饰场景……**一般优先级非常低**。
#    跟壁纸墙纸一样**干脆不做了，保持原样传参式域工具**算了」）——
#    照 190 撤「放料」/ 195b 撤「穿戴」的办法**整套删干净**（顶层行 + 点开那层 + 判据 + 执行）。
#    **为什么撤**：它是**家居装饰场景专用**的活（装修图里搬家具），日常优先级极低，
#    却因为是"目录行"而在屋里（尤其自己家）常驻，占掉第一屏的格子。
#    **替代路（现成的域工具，原样传参，一个都没少）**：
#      · 看屋里有什么家具 → `scene(ops="furniture")`（= 原来点开那一层印的清单）
#      · 搬起某一格那件   → `scene(ops="pickup", kw={"tile_x":X,"tile_y":Y})`（就是原来的 `furniture_pickup`）
#      · 放下/摆好       → `scene(ops="place", kw={...})` · 装修/地板墙纸 → `scene(ops="decor")`
#    ⚠️ 判据（"床永不进搬走"那条 `Furniture.bed`）随行一起删：那是**单子那层**的过滤
#       （防摆一行"按了不成"的）；域工具那条路**不筛**，拿不动由回执如实报（原样）。


def _animals_left(ctx):
    """**眼前**还没摸的牲畜——`/animals` 的 `wasPetToday`（**游戏自己的字段**，不是我们记的账）。

    ⚠️ 只看**当前图**。棚里的那些在 `ctx.animals_away` 里（见 `_pet_can`）。
    """
    return [t for t in ctx.tiles.values() if (t.get("animal") or {}).get("wasPetToday") is False]


def _away_total(ctx) -> int:
    """棚里还没摸的**总数**（`ctx.animals_away` 是 `{建筑名: 只数}`）。"""
    try:
        return sum(int(v) for v in (ctx.animals_away or {}).values())
    except Exception:
        return 0


def _pet_can(ctx, t):
    """🐄 摸动物：**眼前有** 或 **棚里有**（后者要走进棚，`_exec_pet` 本来就会走）。

    ⚠️ 2026-09-30 真机抓的缺口：原来只看 `_animals_left(ctx)`（= 当前图的 `/animals`）⇒
       站 `Farm (40,0)` 时它回 **0 条**（动物都在 Coop/Barn 里），而 `/farm_report` 同刻 **24 只待摸**
       ⇒ 那行**整行不出现**。可 `_exec_pet` → `_pet_animals_in_building()` **本来就会自己走进棚里摸**
       ⇒ **判据比执行器窄**（同一件事两个视野）。恒的偏好是"必要信息主动注入"，这条正好反着。
    """
    return CAN_YES if (_animals_left(ctx) or _away_total(ctx)) else CAN_NO


def _pet_show(ctx, t):
    return "摸 还没摸的动物"


def _pet_reason(ctx, t):
    left = _animals_left(ctx)
    cnt = {}
    for a in left:
        ty = (a.get("animal") or {}).get("type") or "?"
        cnt[ty] = cnt.get(ty, 0) + 1
    if cnt:
        return f"{len(left)} 只（" + "、".join(f"{k}×{v}" for k, v in cnt.items()) + "）"
    # 眼前没有 ⇒ 说清"在棚里"（**别只报个 0**：那等于什么都没说，AI 也不知道该往哪走）
    away = ctx.animals_away or {}
    return f"在棚里 {_away_total(ctx)} 只（" + "、".join(f"{k}×{v}" for k, v in away.items()) + "）"


def _exec_pet(ctx, targets, run):
    left = _animals_left(ctx)
    r = run("pet_animals", {})
    planned = f"{len(left)} 只" if left else f"棚里 {_away_total(ctx)} 只"
    return _receipt_from_helper("摸动物", "", r, planned=planned)


def _pets_can(ctx, t):
    return CAN_YES if ctx.pets else CAN_NO


def _pets_show(ctx, t):
    return "摸 猫狗"


def _pets_reason(ctx, t):
    return "、".join(p.get("name") or "宠物" for p in ctx.pets)


def _exec_pets(ctx, targets, run):
    who = "、".join(p.get("name") or "宠物" for p in ctx.pets)
    r = run("pets", {})   # 🔀 2026-10-04：键从 `pet_pets`（退役作弊函数的旧名）改成 `pets`，见 runner 里的注释
    return _receipt_from_helper("摸猫狗", who, r)


# ═══════════════════════════════════════════════════════════════════
# 🌿 捡 / 🌾 收作物 / ⛏ 锄 —— 2026-09-29 接线
# ═══════════════════════════════════════════════════════════════════
# ⚠️ 这三个**形状不一样**，是查过实现才定的（别看着都是"农活"就长一样）：
#   · **捡**：拟人那条是 `pickup_scene`（走过去 → 转身 → interact）**一次捡一片**，
#     它**不是逐格动作** ⇒ 聚合行（同 166 ⑨：一片走行）。
#     （另有 `/interact{x,y}` 能捡，但**没有距离校验 = 隔空捡**，不走它。）
#   · **收作物**：`/harvest` 是**隔空半径批量**（快捷路），farm 域的 `harvest` 才是拟人
#     （`scythe_crops` 脚本，逐个走位）—— 两者**都只吃半径、不吃坐标** ⇒ 也只能聚合。
#     ⚠️ 半径**别超 25**：C# 那边 `radius` 收 `>0 && <=30`，超了**静默落回 10**
#     （"报成功而事没发生"那个病，见 CHANGELOG 那条）。
#   · **锄**：`_farm_till(x,y)` **x/y 必填、缺省 1×1**，且单格**恒走拟人逐格**（走过去→转身→挥锄）
#     ⇒ 这个**可以逐格**，就按逐格接。
# 📌 一句话：**目标算不算得出 / 端点认不认坐标，决定了它长成行还是聚合行**（同 166 ⑨）。

def _pick_reason_many(ctx, targets):
    cnt = {}
    for t in targets:
        nm = t.get("object") or "地上的东西"
        cnt[nm] = cnt.get(nm, 0) + 1
    return "、".join(f"{k}×{v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1]))


def _exec_pick(ctx, targets, run):
    """🌿 捡——走**拟人那条**（`pickup_scene`：走过去、转身、interact），一次把附近能捡的捡了。"""
    r = run("pickup_scene", {})
    return _receipt_from_helper("捡", "附近", r, planned=f"{len(targets)} 处")


def _harvest_reason_many(ctx, targets):
    cnt = {}
    for t in targets:
        nm = t.get("cropName") or "作物"
        cnt[nm] = cnt.get(nm, 0) + 1
    return "、".join(f"{k}×{v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1]))


def _exec_harvest(ctx, targets, run):
    """🌾 收作物——走 farm 域的**拟人**收（`scythe_crops` 脚本，自己选镰刀、逐个走位）。

    ⚠️ **不是** C# 的 `/harvest`：那条是隔空批量、产物直进包（恒 2026-09-17 否掉过）。
    ⚠️ 半径给 **25**：C# 的 `/surroundings` 收 `>0 && <=30`，超了**静默落回 10**。
    ⚠️ 它是**长脚本（异步）** ⇒ 回执是"跑起来了 + 怎么查"，不是"收完了"——**别把这句当成了**。
    """
    r = run("harvest_crops", {"radius": 25})
    return _receipt_from_helper("收作物", "半径 25 内", r,
                                planned=f"{len(targets)} 格熟的")


def _water_reason_many(ctx, targets):
    """💧 聚合行理由：`萝卜×3、土豆×2`（跟收作物同一口径 —— 别只报「N 格」，那看不出种了啥）。"""
    cnt = {}
    for t in targets:
        nm = t.get("cropName") or "作物"
        cnt[nm] = cnt.get(nm, 0) + 1
    return "、".join(f"{k}×{v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1]))


def _exec_water(ctx, targets, run):
    """💧 浇水——走 farm 域的 `water_crops()`。

    端点语义（`nagi_mcp_server.py:6733`）：**只浇"有作物且没浇过"的格**（空地/没翻的地不碰）；
    壶空了自己**走去水边真打水**再回来接着浇；**晚 10 点后不浇**（明天再说）；浇完报"还剩几格"。
    ⚠️ 它是**同步**的（一簇几十格可能要走近一分钟）——回执是"浇完了"，不是 job 号。
    ⚠️ 端点**无参**（`def water_crops()`）⇒ 这里也别传半径/坐标，传了是白传（会被静默丢掉）。
    """
    r = run("water", {})
    return _receipt_from_helper("浇水", "当前图上有作物的干格", r,
                                planned=f"{len(targets)} 格该浇")


# ═══════════════════════════════════════════════════════════════════
# 🗿 摸雕像 / 🐟 收鱼塘产出 —— 2026-10-04 接线
# ═══════════════════════════════════════════════════════════════════
# 两条都是**情境行**（`target="world"`）：问的是"这一刻这个处境下有没有这件事"，
# 不指某一格 —— 同 `摸 猫狗` / `摸 还没摸的动物`。
#   · 🗿 **雕像**：场上有没有（`ctx.statue`）＋ 今天摸过没（`used_today`）。
#     ⚠️ 判据的**作者**是游戏自己（`Farmer.hasBeenBlessedByStatueToday`，每天重置）——
#        恒 2026-10-04 拍板 (b)：**别拿提醒器的 `shown` 冒充它**（那个一调就把当天提醒吃掉）。
#        老 DLL 没这个字段 ⇒ `None` ⇒ **MAYBE**（宁可不给"按了就成"的假承诺）。
#   · 🐟 **鱼塘**：产出就绪的塘（`ctx.ponds["ready"]`，`output` 非空）。
#     执行是**逐塘**（每座塘一个坐标、走过去交互），所以 `batch=True` 印 `×N` 是**真承诺**。

def _statue_can(ctx, t):
    """🗿 摸雕像：**场上有雕像** 且 **今天还没摸过**。

    三档（跟全表一套）：没有雕像 ⇒ ❌；`used_today is None`（老 DLL）⇒ **MAYBE**；
    没摸过 ⇒ ✅；摸过了 ⇒ ❌（那行的意义就是"今天这一次"，摸完就该消失）。
    """
    s = ctx.statue or {}
    if not s.get("names"):
        return CAN_NO
    if s.get("used_today") is True:
        return CAN_NO
    return CAN_MAYBE if s.get("used_today") is None else CAN_YES


def _statue_show(ctx, t):
    return "摸 雕像"


def _statue_reason(ctx, t):
    s = ctx.statue or {}
    names = "、".join(s.get("names") or [])
    # 📍 带上坐标/距离：2026-10-04 恒「当前图有就报」之后，雕像**可能在图的那一头**（真机 42 格）——
    #    不说位置，AI 就不知道这一按要走多远（同"竹筒倒豆"那条：必要信息要主动给）。
    tiles = s.get("tiles") or []
    where = ""
    if tiles:
        tx, ty = tiles[0]
        d = max(abs(tx - (ctx.px or 0)), abs(ty - (ctx.py or 0)))
        where = f"｜📍({tx},{ty}) 约 {d} 格"
    # 🗿 多座雕像时**逐座说清**还能不能摸（各有各的门：祝福=今天摸过没；矮人国王=身上有没有
    #    `dwarfStatue` buff —— 反编译实据，见 `_im_statue`）——
    #    ⚠️ 只说一个行级结论会把"哪座还能摸"藏起来（AI 想摸矮人那座就不知道行不行）。
    st = s.get("statues") or []
    if len(st) > 1:
        bits = []
        for e in st:
            u = e.get("used_today")
            mark = "✅还能摸" if u is False else ("❌已用过" if u is True else "？读不到")
            bits.append(f"{e.get('name')}({e.get('x')},{e.get('y')}) {mark}")
        return "；".join(bits)
    if s.get("used_today") is None:
        # ⚠️ **读不到就直说读不到**（别印"今天还没摸过"——那是替游戏回答）。
        return f"{names}{where} · ⚠️ 这版 mod 报不出「今天摸过没」"
    return f"{names}{where} · 今天还没摸过（摸一次给祝福，每天一次）"


def _exec_statue(ctx, targets, run):
    """🗿 走现成的 `blessing_statue`（它自己找雕像 → 走过去 → 交互 → 弹选项就选）。

    ⚠️ 回执**用它自己的话**（`_receipt_from_helper`）—— 那里面连"选了哪个祝福"都是它报的，
       这一层重拼一遍就是把它那层复核丢掉。
    """
    names = "、".join((ctx.statue or {}).get("names") or [])
    r = run("statue", {})
    return _receipt_from_helper("摸雕像", names, r)


def _pond_can(ctx, t):
    """🐟 收鱼塘产出：**有塘且那座塘有产出**才给行（`{}` = 不在农场/读不到 ⇒ 没行）。"""
    return CAN_YES if (ctx.ponds or {}).get("ready") else CAN_NO


def _pond_show(ctx, t):
    return "收 鱼塘产出"


def _pond_reason(ctx, t):
    """理由 = **产出是什么** + **哪几座塘**（AI 要知道这一按会走哪几处）。"""
    ready = (ctx.ponds or {}).get("ready") or []
    cnt = {}
    for p in ready:
        k = p.get("output") or "产出"
        cnt[k] = cnt.get(k, 0) + 1
    outs = "、".join(f"{k}×{v}" for k, v in sorted(cnt.items(), key=lambda x: -x[1]))
    where = "、".join(f"({p.get('x')},{p.get('y')})" for p in ready)
    total = (ctx.ponds or {}).get("total")
    extra = f"（本档 {total} 座塘）" if isinstance(total, int) and total > len(ready) else ""
    return f"{outs} · 塘 {where}{extra}"


def _exec_pond(ctx, targets, run):
    """🐟 **逐塘**领产出：每座一个坐标 → `pond_collect`（走位 + 交互 + **回读产出没了没**）。

    ⚠️ 逐条报（同「取/存」那两条的规矩）：**不许整批报成功**，也不许把计划数当结果数 ——
       成了几座由每一发自己的回读来说（`_pond_collect` 的 ✅/⚠️ 就是它的回读结论）。
    ⚠️ 它**不另写 HTTP**：走位那一套在 `_pond_do_interact` 里（只有一处）。
    """
    ready = (ctx.ponds or {}).get("ready") or []
    lines, ok_n = [], 0
    for p in ready:
        out = p.get("output") or "产出"
        r = run("pond_collect", {"x": p.get("x"), "y": p.get("y")}) or {}
        st = r.get("st") or ("yes" if r.get("ok") else "no")
        if st == "yes":
            ok_n += 1
        # ⚠️ 它自己的话**原样带出来**（多行压成一行只为省屏，一个字都没删）。
        txt = " ".join(str(r.get("text") or r.get("error") or r).split())
        lines.append(f"  · ({p.get('x')},{p.get('y')}) {out} —— {_MARK.get(st, '❌')} {txt}")
    return render_receipt("收鱼塘产出", f"{len(ready)} 座", ok_n > 0, note="\n".join(lines))


def _held_name(slot) -> str:
    """手持/背包那件东西的**内部名**（英文）——端点按它匹配，别拿中文显示名去撞。"""
    return (slot.get("raw") or {}).get("name") or slot.get("name") or ""


def _quality_of(slot) -> int:
    """`/select` 的 `quality`：**-1 = 不限品质**（游戏那边的原话）。

    ⚠️ 别拿 `scan_backpack` 的 0 当默认——`0` 在 `/select` 里是**硬筛"只要普通品质"**，
    会把银/金/铱星那一摞挑掉。字段缺 = 不知道 ⇒ **-1（不限）**，不是 0。
    """
    q = slot.get("quality")
    return q if isinstance(q, int) and q >= 0 else -1


def _exec_select_then(ctx, slot, run, ep, payload, verb_cn, note_ok, note_fail):
    """🍽📖 吃/看的共同形状：**先 `/select` 锁到单子上那一件，再动手**。

    ⚠️ 为什么必须 select：`/eat` 吃的是 `farmer.CurrentItem`、`/use mode=read` 读的也是
    `CurrentItem` —— 它们**不认名字**。不先锁，敲"吃 草莓"可能吃掉手上别的。
    ⚠️ **select 的成败必须看**（2026-09-29 审查抓出来的洞）：槽位漂了 / 那件没了 /
       品质档对不上 ⇒ select 回 `ok:false`，此时**还往下走就是吃掉手上那件别的**，
       而回执写着单子上那件 —— 静默错误动作（同族："以为在收钻石、收的却是翡翠"）。
       ⇒ 没锁上就**停在这儿**，什么都不做，把它如实说出来。
    ⚠️ `quality` 一起传：同名两摞（不同品质）时 `/select` 才选得准（2026-09-19 那条老账）。
    回执**只报游戏回的**，不替它算（铁律 3 的反面：替它算 = 我们编数字）。
    """
    nm = _held_name(slot)
    if not nm:
        return render_receipt(verb_cn, slot.get("name") or "?", False,
                              note="拿不到它的内部名 —— 不敢乱点（宁可不做）")
    label = slot.get("name") or nm
    sel = run("select", {"name": nm, "quality": _quality_of(slot)}) or {}
    if not sel.get("ok"):
        return render_receipt(verb_cn, label, False,
                              note=f"**没选中它**（游戏回：{sel.get('error') or sel}）"
                                   f"—— 手上的东西**没动**，先看一眼背包再敲")
    r = run(ep, payload) or {}
    # ⚠️ 回执里回显的是**单子上那件**（AI 看见的中文名），不是端点回的 `item.Name`——
    #    那个是英文内部名（"Strawberry"），AI 在单子上没见过它，等于换了把尺子。
    if not r.get("ok"):
        return render_receipt(verb_cn, label, False, note=note_fail.format(r=r.get("error") or r))
    return render_receipt(verb_cn, f"{label}×1", True, note=note_ok.format(r=r))


def _exec_eat(ctx, targets, run):
    """🍽 吃（手持那件）。"""
    return _exec_select_then(
        ctx, targets[0], run, "eat", {}, "吃",
        note_ok="游戏回：体力 {r[stamina]} · 血 {r[health]}",
        note_fail="游戏回：{r}")


def _exec_read(ctx, targets, run):
    """📖 看/读（手持那件）——「读没读过」游戏没有事前判据，
    ⇒ **真失败了由回执如实报**「读过了，没反应」（见 `_read_can`）。"""
    return _exec_select_then(
        ctx, targets[0], run, "use", {"mode": "read"}, "看",
        note_ok="读了（游戏消耗 1 个）",
        note_fail="游戏回：{r}")


# ═══════════════════════════════════════════════════════════════════════
# 📦 容器（箱子 / 冰箱）—— 166 ⑤
# ═══════════════════════════════════════════════════════════════════════
# 🔬 「容器收什么」的判据（2026-09-29 反编译 **C 盘 1.6.15.24356** 实锤）
#
#   `Chest.ShowMenu()` 按 `SpecialChestType` 挂筛子：
#     · 迷你出货箱 → `Utility.highlightShippableObjects`（只收可出货的）
#     · 富集器     → `Object.HighlightFertilizers`（只收肥料，容量 1）
#     · **其余全走 `InventoryMenu.highlightAllItems`**（普通箱 / 大箱子 / 祝尼魔箱 / 自动装载器）
#   ⇒ **普通箱子 = 全收**，筛子只长在特型箱上。
#
#   🔑 而 C# 的 `/scan_chests` 名单 `IsStorageChest` **已经把迷你出货箱、祝尼魔箱排除**
#      ⇒ **单子上出现的箱子都是"全收"那一族**。所以这一层**不需要**再判容器类型——
#      真正要守的是那条反向的：**别把名单外的容器放上单子**。
#   ⚠️ 已知洞（**挂账，走 C# 批次，别在这儿编表**）：`Enricher`（施肥器）没被
#      `IsStorageChest` 排除，它只收肥料、只有 1 格。判据在游戏里（`SpecialChestTypes`），
#      我们这边看不见 ⇒ 修法是把名单补齐 / 补一个能力位，见 CHANGELOG 167。
#
#   ⚠️ 冰箱 = `Chest{ fridge = true }`（**不是独立类**）：`ShowMenu` 只把开箱音效换成
#      `doorCreak`，**筛子还是 highlightAllItems** ⇒ **冰箱也是全收**（反直觉，但实锤）。
#   ⚠️ 鱼缸(`FishTankFurniture`) / 梳妆柜(`StorageFurniture`) 是 **Furniture 不是 Chest**
#      ⇒ C# `HandleStore` 硬判 `is Chest`，**压根够不着**：鱼缸按 `HasRoomForThisItem()`
#      （`Data/AquariumFish` 分类 + 容量）、梳妆柜按 `{帽-95, 衣-100, 靴-97, 戒-96}`。
#      ⇒ 它们**因此不会出现在单子上**——不是我们排除的，是端点本来就不认。
#   ⚠️ 「看」＝**走过去真开**（画面通道：菜单给恒看）+ 内容进回执（数据通道：给我们）。
#      它要一个新端点（`chest.ShowMenu()` 是 public，C# 一行的事），**还没做**
#      ⇒ 由能力位 `caps["chest_open"]` 把关：这版 DLL 没有，那行就**不出现**（不糊弄）。
#      ⚠️ 别拿 `/interact` 代替：`Chest.checkForAction` 开头卡 `didPlayerJustRightClick`
#      （反编译 + 2026-09-12 实测），API 驱动没有真鼠标右键 ⇒ 开不了。

def _box(t):
    """这一格的容器明细（`/scan_chests` 认过的那份）。没认过 = 不是我们的仓库。"""
    return (t or {}).get("chest")


def _slots_text(used, cap) -> str:
    """「u/c 格」——**任一个数拿不到就一个字都不印**（宁可短，也别把 `None/None` 摆给 AI）。
    ⚠️ 老 DLL / 字段缺失时，印 `None/None 格` 比不印更坏：AI 会把它当成一个数。"""
    if used is None or cap is None:
        return ""
    return f"{used}/{cap} 格"


def _box_space(box):
    """容器放得下吗 → True / False / None(算不出)。

    **空位数用游戏给的 `freeSlots`**（= `GetActualCapacity() - used`），不是我们数出来的。
    ⚠️ 空位 0 一律判「放不下」：`/scan_chests` **不吐品质**，而品质不同的同类**不能叠**
    ⇒ 赌"能叠进已有那摞"就是把拿不准的事装成准的。宁可少给一条行，也不给一条会失败的行。
    """
    if box is None:
        return CAN_MAYBE
    free = box.get("freeSlots")
    if free is None:
        return CAN_MAYBE
    return CAN_YES if free > 0 else CAN_NO


def _box_accepts(box, slot):
    """这个容器收这件东西吗 → True / False / None(算不出)。

    ⚠️ 现在恒 `True`，**而且这个 True 是有出处的**：能走到这儿的 `box` 全来自
    `/scan_chests` 的 `IsStorageChest` 名单，而那份名单已经把带筛子的特型箱
    （迷你出货箱／祝尼魔箱）排除了 —— 剩下的正是游戏挂 `highlightAllItems` 的那族
    （含冰箱，见文件上方那段反编译说明）。
    ⇒ 这一层的正确做法**不是**在这儿列"冰箱只收食材"之类的表（那是**编表**，
      本项目因为名单会烂栽过：1.6 矿节点 ID、`Jewels Of The Sea` 两次），
      而是**保证带筛子的容器进不了这张单子**。名单补全的活在 C#（见上面挂的账）。
    """
    if box is None:
        return CAN_MAYBE
    return CAN_YES


def _pack_space(ctx):
    """背包放得下吗 → True / False / None(算不出)。

    容量取 **`/state.player.maxItems`**（游戏自己的 `Farmer.MaxItems`，12/24/36 三档）
    ——**不是**我们写死 36（写死就是编表）。同 `_box_space`：装不下就判装不下，不赌能叠。
    """
    cap = ctx.max_items
    if not cap:
        return CAN_MAYBE
    return CAN_YES if len(ctx.inv) < cap else CAN_NO


def _storable_slots(ctx, box):
    """🎒 背包里**能存进这个容器**的那些格子 → `(留下的, 挑出去几摞)`。

    ⚠️ **两条路共用**：箱子**关着**时（`_chest_subs` ③）和箱子**开着**时（`_menu_store_subs`）
    —— 这是同一件事（"哪些能放进去"），写两遍必然漂（本项目的老病）。
    ⚠️ **工具不进这张候选**：`/store` 默认 `keepTools=True`（恒设的保护），它会在端点里
    **静默跳过工具** ⇒ 列出来就是"按了不成"的行。不在这儿顺手把 `keepTools` 翻成 false
    —— 那是**动恒设的安全阀**，得他拍板。判据问游戏（`cat_num == -99`）；
    **问不出（None）也不列**（同三档：不透支信任）。
    ⚠️ 同名两摞（不同品质）：新版 DLL 的 `/store` 认 `slot`（= **背包格号**）⇒ 分得开；
    **老 DLL** 才要把认不出的挑出去（`store_slot_quality` 那位不在 = 老 DLL）。
    """
    can = [s for s in ctx.inv
           if s.get("idx") and is_tool(s) is False and _box_accepts(box, s) is True]
    if not ctx.cap("store_slot_quality"):
        can, amb = _unambiguous(can, _held_name)
        return can, amb
    return can, 0


def _exec_take_multi(ctx, pairs, run):
    """🧺 取：**逐条报**（哪条成了、哪条没成）。

    ✅ **2026-10-01 同「存」一起补上走位**：这一层打的 `chest_take` op，服务器那边先
       `_walk_to_chest` 再 `/chest_take`（`_im_chest_op`）—— C# 那条同样是**不校验距离**的原子直操。
       （"看"那行（`_exec_chest_open`）本来就走过去，所以旧流程下常常"人已经在箱边"、
        看不出这个洞；但单子上从**箱子目录**直接按「取」时，人是站的别处。）
    ⚠️ 不许整批报成功、也不许整批回滚——两个都是替 AI 圆场（166 ④ 配套硬要求）。
    ⚠️ **只报事实，不替游戏编原因**（2026-09-29 审查）：`took < cnt` 既可能是箱里不够，
    也可能是**背包中途塞满**（`HandleChestTake` 装不下就提前 break）——
    原来那句写死"（箱里只有 N 个）"是**我们把猜测当成了游戏的话**。现在只报差额。
    """
    lines, ok_n = [], 0
    for row, cnt in pairs:
        it, box = row.targets[0]["item"], row.targets[0]["box"]
        cn = it.get("displayName") or it.get("name")
        payload = {"x": box["x"], "y": box["y"], "count": cnt, "name": it.get("name")}
        # 🆕 2026-09-30(178)：有**真实格号**就按格号指（同名两摞才分得开）。
        # ⚠️ `name`/`quality` **一起给**、不是二选一：万一"列出来 → 按下去"之间箱子被动过，
        #    `slot` 会指到别的东西 —— 此时三个判据对不上 ⇒ **一件都取不到**（如实报），
        #    而不是"悄悄拿错一摞"（那正是这条老账最怕的形状）。
        if isinstance(it.get("slot"), int):
            payload["slot"] = it["slot"]
            if isinstance(it.get("quality"), int):
                payload["quality"] = it["quality"]
        r = run("chest_take", payload) or {}
        took = r.get("taken") or 0
        if r.get("ok") and took:
            ok_n += 1
            extra = "" if took == cnt else f"（要 {cnt} 个，到手 {took} 个）"
            lines.append(f"  · {cn} ×{took}{extra}")
        else:
            lines.append(f"  · {cn} ×{cnt} —— **没取到**（游戏回：{r.get('error') or 'taken=0'}）")
    return render_receipt("取", f"{len(pairs)} 种", ok_n > 0, note="\n".join(lines))


def _exec_store_multi(ctx, pairs, run):
    """📥 存：同样**逐条报**。

    ✅ **2026-10-01 补上走位了**（恒：「当时说先接 store，**没把前面的跑过去箱子接进来**。
       那就补吧」）：这一层打的 `store` op，服务器那边**先 `_walk_to_chest` 再 `/store`**
       （`_im_chest_op`）—— 因为 C# `HandleStore`（`ModEntry.cs:10429`）是**原子直操**
       （`farmer.Items` ↔ `chest.addItem`，**不校验距离**），人站半张图外也能"存进去"。
    ⚠️ 判据只有一处：走位在服务器那一层做（`_im_chest_op`），**这一层不许再写一套"走多近、站哪边"**。
    """
    lines, ok_n = [], 0
    for row, cnt in pairs:
        slot, box = row.targets[0]["slot"], row.targets[0]["box"]
        cn = slot.get("name") or slot.get("raw", {}).get("name")
        payload = {"x": box["x"], "y": box["y"], "count": cnt,
                   "name": _held_name(slot), "keepTools": True}
        # 🆕 2026-09-30(178)：`/store` 认 `slot`（= 游戏那个**背包格号**）之后，同名两摞分得开了。
        #    ⚠️⚠️ 2026-10-01 真机 A/B 照出来的**差一位**：传的必须是 **`idx - 1`**，不是 `idx`。
        #       · `idx` = `slot_of()` 的 **给 AI 看的 1-based 位次**（`slotIndex + 1`，单子上印的就是它）；
        #       · `/store` 的 `slot` = **游戏那把 0-based 尺子**（C# 直接拿它索引 `farmer.Items`）。
        #       实证（同一只箱子、同一时刻、同一样东西）：
        #         `slot=7` ⇒ `stored:[{Moss,3}]`；`slot=8`（= 单子发的 `idx`）⇒ `stored:[]`。
        #       ⇒ 这一行原来每次都发 `idx` ⇒ **「存」整条路静默不干活**（回包只给 `stored:[]`，
        #         而单子上那行写着"按了就成"）。⚠️ 两个数字**不能混着当一把尺子**（09-27 `catNum` 同族）。
        #    ⚠️ `quality` 用 `_quality_of()`：**缺字段 ⇒ -1（不限）**，别拿 0 当默认
        #       （`0` 在端点那边是**硬筛"只要普通品质"**，会把银/金星那摞挑掉）。同理给 name 兜底。
        #       ⚠️ 非 `Object` 的件（工具/武器/帽子…）也报 `quality: 0`，而端点**只对有品质的东西**
        #         用这把筛子（`HandleStore` 里 `item is StardewValley.Object` 那一版）——
        #          两边配套，见那条注释。
        if slot.get("idx") is not None:
            payload["slot"] = slot["idx"] - 1
            payload["quality"] = _quality_of(slot)
        r = run("store", payload) or {}
        got = sum(s.get("count") or 0 for s in (r.get("stored") or []))
        if r.get("ok") and got:
            ok_n += 1
            extra = "" if got == cnt else f"（要放 {cnt} 个，进去了 {got} 个）"
            lines.append(f"  · {cn} ×{got}{extra}")
        else:
            lines.append(f"  · {cn} ×{cnt} —— **没存进去**（游戏回：{r.get('error') or 'stored=[]'}）")
    return render_receipt("存", f"{len(pairs)} 种", ok_n > 0, note="\n".join(lines))


def _exec_chest_open(ctx, targets, run):
    """👀 看：**走过去真开**（画面通道）+ 内容进回执（数据通道）。开完**不关**。

    ⚠️ 恒 2026-09-28 拍板「开完不关」：① 很多操作本来就要点菜单；
       ② 他那边看着像人在操作（拟人的验收判据）。
    ⚠️ 这里**只走路 + 开**，不读内容——内容由 `/menu` 另外读，
       两条通道分开（一次调用干两件事，接了也能漂）。
    ⚠️ **拟人的走位在服务器侧**（`_im_chest_open` → `_walk_to_chest`，会**等到真的站到箱边**，
       因为 C# 的 `/walk_to` 是发射后不管的）。这儿只管敲一下，把它的话**如实带回来**
       —— 连那行走位实况也要带出来（吞掉就等于"嘴上说开好了、人还在半路"）。
    """
    t = targets[0]
    x, y = t.get("x"), t.get("y")
    r = run("chest_open", {"x": x, "y": y}) or {}
    walk = (r.get("walk") or "").strip()
    if not r.get("ok"):
        note = "\n   ".join(s for s in (walk, f"游戏回：{r.get('error') or r}") if s)
        return render_receipt("开箱", f"({x},{y})", False, note=note)
    # ⚠️ 2026-10-01（P-menus）：这句原来写死「内容用 `menu read` 看」—— 现在容器菜单的内容
    #    **就摊在单子上**（「箱子里…」那一行）⇒ 还劝人去 `menu read` 是指错路。
    #    但**这一刻的 `ctx` 是开箱之前拍的**（菜单是执行中才开的），拿它判不出摊没摊
    #    ⇒ 改成一句**两边都真**的话：让 AI 去看单子本身。单子若真的什么都没有，
    #      它会照 `_close_hint` 的原话把路指出来（"内容怎么读"的判据只有那一处）。
    note = "\n   ".join(s for s in (walk, "菜单开着（不关）—— 敲 `show` 看单子：这一刻能按的都列在上面") if s)
    return render_receipt("开箱", f"({x},{y})", True, note=note)


# 「看 / 取 / 存」三个动作——**只长在容器那一层里**，不进顶层动词表：
# 顶层扫的是"图上的格子"，而它们的目标是"这个容器"，由 `_chest_subs` 现场算。
#
# ⚠️ 2026-10-01：这三个原来 `can=None`（"进不了顶层，不需要"）。加 `_recheck` 之后
#    那句话不成立了 —— **执行前复验会调 `can()`**，`None` 一调就炸（自验当场抓到）。
#    ⇒ 补上真判据（"这格还是个容器吗"）：它们由 `_chest_subs` 保证目标本来是容器，
#      而复验要的正是"**现在还是不是**"（箱子可能被搬走 / 换图了）。
_CAN_IS_CHEST = lambda c, t: CAN_YES if (t or {}).get("is_chest") else CAN_NO  # noqa: E731
OPEN_V = Verb("chest_open", "看（走过去开箱）", 0, _CAN_IS_CHEST, None,
              lambda c, t: "看（走过去开箱）", "tile", exec=_exec_chest_open)
TAKE_V = Verb("chest_take", "取", 0, _CAN_IS_CHEST, None, lambda c, t: "取", "tile",
              exec_multi=_exec_take_multi)
STORE_V = Verb("chest_store", "存", 0, _CAN_IS_CHEST, None, lambda c, t: "存", "tile",
               exec_multi=_exec_store_multi)


def _unambiguous(objs, *keyfns):
    """把**分不清是哪一摞**的挑出去 → `(留下的, 挑出去几摞)`。

    ⚠️ 为什么必须挑出去（2026-09-29 审查）：`/chest_take`、`/store`、`/sell_to_shop`
    **只按名字认**（`Name` / `DisplayName`）⇒ 两摞在单子上印出来**一模一样**时，
    AI 指哪摞都解析不出，端点按自己的遍历顺序拿一摞
    ⇒ **可能动错那一摞、而且不报错**（"会照做、不会怀疑"的那类静默错误）。
    ⇒ 认不出的**不列**（宁缺勿编）；挑出去几摞要**如实说**（铁律 2：不许静默截断）。
    📌 一条游戏事实帮着理解为什么"同名=可疑"：**同物品同品质会自动叠**（`canStackWith`）
    ⇒ 同名两摞必然是品质/状态不同，正是端点分不清的那种。

    ⚠️⚠️ **可以给多把尺子**（`*keyfns`），这是 2026-09-29 审查抓出来的必修：
    单子上"AI 看到的那把尺子"（**行标签 = 显示名**）和"端点认的那把尺子"
    （**内部名**）**可以是两个字段**。只按其中一把去重，另一把撞车时就会漏：
      · 两件**不同物品撞同一个显示名**（`Wine`/`Juice` 都叫「酒」）——按显示名才看得出来；
      · 两摞**同内部名不同显示名**（地板/墙纸 `Name` 恒是 `Flooring`/`Wallpaper`）
        ——只有按内部名才看得出来，可端点正是按内部名卖，它会卖掉**第一摞**。
    ⇒ 调用方把**两把尺子都给**，任何一把撞车就整组挑出去。
    """
    dup = set()
    for kf in keyfns:
        seen = {}
        for o in objs:
            k = kf(o)
            if k:
                seen[k] = seen.get(k, 0) + 1
        dup |= {k for k, n in seen.items() if n > 1}
    if not dup:
        return objs, 0
    kept = [o for o in objs if not any(kf(o) in dup for kf in keyfns)]
    return kept, len(objs) - len(kept)


def _q_prefix(slot) -> str:
    """星级标签：`[银]/[金]/[铱]`，无品质/读不到 → 空串。

    ⚠️ 档位照抄**项目唯一那张表**（`nagi_mcp_server` 的拾取小新闻里那份）：
    **0=无 · 1=银 · 2=金 · 4=铱**，`3` 是**空的**（1.6 里不存在）——
    老表把 3 当铱 ⇒ `quality=4` 查不到、标没了（CHANGELOG 2026-09-25 那条真机账）。
    4 是真值、3 留着防老档/控制台手搓值。
    """
    q = slot.get("quality")
    if not isinstance(q, int) or q <= 0:
        return ""
    return {1: "[银]", 2: "[金]", 3: "[铱]", 4: "[铱]"}.get(q, "")


def _name_with_q(slot) -> str:
    """给人看的物品名 = **星级前缀 + 显示名**。

    ⚠️ 2026-09-30(178) 真机：箱里三摞啤酒花（铱 ×150 / 金 ×281 / 普通 ×256）在候选里
    印成**三行一模一样的「啤酒花」**——格号是能指准了，可 AI **看不出哪摞是哪摞**
    （"菜单是强暗示"那条：给了就得让人分得清）。
    ⚠️ **幂等**：`/state` 那条路的 `displayName` 有的**自带** `[金]` 前缀（拾取小新闻就是），
    箱内那份**不带**（反编译：`Object.DisplayName` 只是本地化名，不含星级）⇒
    已经带 `[` 开头就不再加，**绝不叠成 `[金][金]`**。
    """
    nm = slot.get("displayName") or slot.get("name") or "?"
    p = _q_prefix(slot)
    return nm if nm.startswith("[") else f"{p}{nm}"


def _item_row(it, box, verb):
    """容器里的一样东西 = pick 层的一行。**不在世界里 ⇒ 不印定位**（印"手持"就是撒谎）。"""
    return Row(verb, [{"item": it, "box": box}], _name_with_q(it),
               f"箱里 ×{it.get('count')}", 0, where="")


def _slot_row(slot, box, verb):
    """背包里的一样东西 = pick 层的一行，目标是"存进这个容器"。"""
    return Row(verb, [{"slot": slot, "box": box}], _name_with_q(slot),
               f"背包 ×{slot.get('stack')}", 0, where="")


def _chest_subs(ctx, targets):
    """📦 一个容器的动作面（166 ⑤）——**三行都由处境算，算不出的行根本不出现**：
      · `看` —— 这版 DLL 有 `chest_open` 能力位才长
      · `取` —— 容器里有东西 **且** 背包放得下
      · `存` —— 背包里有它收的 **且** 容器放得下
    """
    t = targets[0]
    box = _box(t)
    if box is None:
        return None
    x, y = t.get("x"), t.get("y")
    here = f"({x},{y})"
    rows = []

    # ① 看＝走过去真开（画面通道，恒要的拟人观感）
    if ctx.cap("chest_open"):
        rows.append(Row(OPEN_V, [t], "看（走过去开箱）",
                        _slots_text(box.get("used"), box.get("capacity")), 0, where=""))

    # ⚠️ 行**只在算得出来("YES")时才出现**：第三档（算不出）与"不"一样不上单子
    #    （166 ⑤：「算不出 ⇒ 那行根本不出现」；同 can() 三档，不透支信任）。
    #    差别在**要不要说一句**：算得出"装不下"⇒ 给一句事实 + 下一步（恒：「报缺了要给出路」）；
    #    算不出（老 DLL）⇒ 闭嘴（那是**连接级**信息，逐格里翻不出来）。
    notes = []

    # ② 取…：容器里有 ∩ 背包放得下
    mine = [it for it in (box.get("items") or []) if (it.get("count") or 0) > 0]
    # 🆕 2026-09-30(178)：**能不能按格号指哪一摞**是**连接级**的事 —— 问 `caps`，别从格子里猜：
    #    · 新 DLL：`/scan_chests` 给每样东西带了**真实格号** `slot`（+星级 `quality`），
    #      `/chest_take` 也认 `slot` ⇒ 同名两摞**分得开了** ⇒ 不用再挑出去，**全都列**。
    #    · 老 DLL：没有格号，端点只按名字认 ⇒ 同名两摞**照样不许列**
    #      （宁缺勿编：印出来一样的两行，AI 指哪摞都解析不出，端点会按自己的遍历顺序拿一摞）。
    amb = 0
    if not ctx.cap("scan_chests_item_slot"):
        mine, amb = _unambiguous(mine, lambda it: it.get("name"))
    pack = _pack_space(ctx)
    if mine and pack is CAN_YES:
        rows.append(Row(TAKE_V, [t], "取", f"背包 {len(ctx.inv)}/{ctx.max_items}", 0,
                        level=Level([_item_row(it, dict(box, x=x, y=y), TAKE_V) for it in mine],
                                    title=f"取哪几样？{here}（可以多选，如 `1,4`）",
                                    mode="pick", verb=TAKE_V),
                        where="", count_text=f"{len(mine)} 种"))
    elif mine and pack is CAN_NO:
        notes.append(f"背包满了（{len(ctx.inv)}/{ctx.max_items} 格）"
                     f"—— 先卖或存掉点东西，「取」才放得下")
    if amb:
        notes.append(f"有 {amb} 摞**同名但不同品质**的没列出来 —— 这版 DLL 拿不到箱子里的格号，"
                     f"端点只按名字认，认不出是哪一摞（要精确挑就用 storage 域）")

    # ③ 存…：背包 ∩ 容器收的 ∩ 容器放得下
    space = _box_space(box)
    if space is CAN_YES:
        # ⚠️ "哪些能存"那把尺子**只有一处**（`_storable_slots`）——
        #    箱子开着时那条「存…」用的是**同一个函数**（别在这儿再抄一遍过滤）。
        can, amb2 = _storable_slots(ctx, box)
        if can:
            rows.append(Row(STORE_V, [t], "存", f"箱空 {box.get('freeSlots')} 格", 0,
                            level=Level([_slot_row(s, dict(box, x=x, y=y), STORE_V) for s in can],
                                        title=f"存哪几样去 {here}？（可以多选，如 `1,4`）",
                                        mode="pick", verb=STORE_V),
                            where="", count_text=f"{len(can)} 种"))
        if amb2:
            notes.append(f"背包里有 {amb2} 摞**同名但不同品质**的没列出来 —— "
                         f"这版 DLL 的 `/store` 不认格号，只按名字认，认不出是哪一摞")
    elif space is CAN_NO:
        notes.append(f"箱子满了（{box.get('used')}/{box.get('capacity')} 格）"
                     f"—— 先取点东西出来，「存」才放得下")

    title = f"📦 {_chest_label(box)} {here}"
    slots = _slots_text(box.get("used"), box.get("capacity"))
    if slots:
        title += f" · {slots}"
    for n in notes:
        title += f"\n  ⚠️ {n}"
    if not rows:
        # 与顶层同款：**只报事实，不编推荐**（理由栏要有出处，编不出来就别给）
        title += "\n  （这个箱子现在没有能做的——空着就是空着）"
    return Level(rows, title=title)


def _chest_can(ctx, t):
    """📦 这一格是个容器吗——判据 = **它出现在 `/scan_chests` 里**（见上面那段名单说明）。

    ⚠️ **不按 `object == "Chest"` 的名字认**（本项目栽过无数次），也不按贴图/名字猜。
    """
    return CAN_YES if _box(t) else CAN_NO


def _chest_reason(ctx, t):
    """理由栏 = 审计面。容器这条只留**空几格**（"还剩多少地方"才是要判断的东西）；
    里面有几件由目录行的计数报（`count_text`），**别在这儿说第二遍**（同屏两个数=两把尺子）。"""
    b = _box(t) or {}
    free = b.get("freeSlots")
    return f"空 {free} 格" if free is not None else ""


def _chest_count(ctx, targets):
    """目录行那截计数（**合一那行**）= `N 个 · 共 X 件 · 空 Y 格`。

    ⚠️ 数不出来时回 `""`（**不印**），**不是**回 `None` —— 回 None 会掉进
    `_render_level` 的兜底 `len(rows)`，那就变成另一个意思了（两把尺子）。
    """
    used = [(_box(t) or {}).get("used") for t in targets]
    free = [(_box(t) or {}).get("freeSlots") for t in targets]
    bits = [f"{len(targets)} 个"]
    if all(u is not None for u in used):
        bits.append(f"共 {sum(used)} 件")
    if all(f is not None for f in free):
        bits.append(f"空 {sum(free)} 格")
    return " · ".join(bits)


def _chest_weight(ctx) -> int:
    """📦 容器行的权重：平时 **80**（**故意压在 `collect` 88 之下**——它是目录行，理由见 VERBS 那段）；
    **满包时抬到 90**（恒 2026-09-30 拍板：「把「箱子…」提前 + 理由点明」）。

    ⚠️ 为什么满包该压过「收放」：满包时**收根本收不进去**（收放那条走脚本，它自己会报
       「背包满了，还有 N 件没收」）⇒ 那一刻的**唯一正解**是先去存/卖。
       「菜单是强暗示」要挡的正是"给了但按了白按"的排位。
    ⚠️ 判据用 `_pack_space`（跟「取」那条行**同一根**），别另写一个 `len(inv) >= max_items`。
    """
    return 90 if _pack_space(ctx) is CAN_NO else 80


def _chest_reason_many(ctx, targets):
    """合一那行的理由：**最近一个箱子几步**；**满包时点明"背着满了"**（恒 2026-09-30）。

    ⚠️ 补这一句的原因（真机实测）：背包 36/36 时状态条有 `🎒 36/36格⚠️`，
       而**菜单里一个字都没提**该去存 —— 状态条和单子是两张屏，别指望 AI 自己串起来。
    """
    bits = [f"最近 {min(_dist(ctx, t) for t in targets)} 步"]
    if _pack_space(ctx) is CAN_NO:
        bits.append("背着满了，先存点")
    return " · ".join(bits)


def _chest_one_count(ctx, targets):
    """一览里那一行的计数 = **`已用/容量 格`**（理由栏只留"箱里是什么"）。

    ⚠️ 必须显式给（不能靠 `_render_level` 的兜底）：兜底是数**下一层有几行**
       ⇒ 印出来是「2 件」（=取/存两条动作），而那只箱子其实有 3 件 ——
       两把尺子并排（2026-09-29 自验当场照出来的）。
    ⚠️ 容量放**计数位**、内容放**理由栏**：两个都是"几件"的数摆在一行里
       就是同一个数说两遍（同族于"两把尺子"）。
    """
    b = _box(targets[0]) or {}
    return _slots_text(b.get("used"), b.get("capacity"))


def _chest_label(box) -> str:
    """这只容器**叫什么**（给人看的）：**人工名 > 容器类型名 > 「箱子」**。

    ⚠️ 2026-09-30(178)：原先只有 `name or '箱子'` ⇒ 一台小冰箱的动作面标题和它那一行
    也印成「箱子」，跟屏②/屏③（都印「迷你冰箱」）**又是两把尺子**。
    类型名一律问游戏要（`/scan_chests` 的 `typeName`），**不在这儿编表**；老 DLL 没有 ⇒ 落到「箱子」。
    """
    b = box or {}
    return b.get("name") or b.get("typeName") or "箱子"


def _chest_show(ctx, t):
    b = _box(t) or {}
    return f"{_chest_label(b)}({t.get('x')},{t.get('y')})"


def _chest_tag(box) -> str:
    """一箱的标签：**色名 + 人工名（优先）/ 类型名 + 自动类目标签**。

    ⚠️ 口径**照抄 `storage_layout`**——✅ 2026-09-30 起反过来：`storage_layout` **调这一个**，
       同一批箱子在两张屏上**不可能**再长得不一样（以前是"两份代码 + 注释里互相提醒"，迟早要漂）。
       ⚠️ 那边多一个 `⭐`（本场景默认箱）——那个记号归 storage 域，单子这边**不搬**：
          默认箱是"存去哪"的设置，不是"这里有什么"。
    ⚠️ `typeName`（2026-09-30 的新 DLL 才有）：**没人起名时印容器类型**（宝箱/迷你冰箱/石箱）。
       起因：屋里三台小冰箱原来只能印 `⬜ (18,23)`——分不出"这是台小冰箱"。
       ⚠️ 类型的显示名一律**问游戏要**（`/scan_chests` 的 `typeName`），**不在 Python 里编表**。
       ⚠️ 老 DLL 没这个键 ⇒ 这段自然为空（不是"这箱没类型"，是"这版问不出来"，同三档）。
    """
    emo, czh = _color_display((box or {}).get("color") or "")
    tag = (emo + czh) if czh else "⬜"
    if box.get("name"):
        tag += f"「{box['name']}」"        # 人工标注的名字优先（同 storage_layout，别双标）
    else:
        if box.get("typeName"):
            tag += box["typeName"]        # 🆕 容器类型（游戏给的显示名）
        if box.get("autoTag"):
            tag += f"【{box['autoTag']}】"
    return tag


def _chest_overview(ctx, targets):
    """📦 「箱子」那行的下一层 —— **当前场景箱子一览**（一行一箱，点开才是取/存）。

    ⚠️ 恒 2026-09-29：「**箱子好多哇！**…选择该项应该是接到 storage 的原有功能去
       （本来就是**当前图的所有箱子一览**）」⇒ 顶层只留**一行**（报总箱数/总余格），
       点开是**一览**，再点才是动作面。（原来一箱一行，5 个箱子就把第一屏占满了。）
    ⚠️ 只有**一个**箱子时**直接给动作面**（`_chest_subs`）——再套一层"一览"是白点一下。
    """
    if len(targets) == 1:
        return _chest_subs(ctx, targets)
    rows = []
    for t in targets:
        b = _box(t) or {}
        slots = _slots_text(b.get("used"), b.get("capacity"))
        label = " ".join(x for x in (_chest_tag(b), f"({t.get('x')},{t.get('y')})") if x)
        items = b.get("items") or []
        if items:
            # ⚠️ 印 **displayName（中文）**，不是 `name`（英文内部名）——单子上的东西一律用
            #    AI 看得懂的那个名字（同「收放」理由栏那套口径）。
            #    ⚠️ `storage_layout`（storage 域那张屏）印的是 `name` ⇒ 同一批箱子两张屏
            #       长得不一样，是**旧账**，记在 CHANGELOG 里待收，别在这儿跟着错。
            head = "、".join(f"{i.get('displayName') or i.get('name')}×{i.get('count')}"
                             for i in items[:4])
            reason = head + (f"…共{len(items)}种" if len(items) > 4 else "")
        else:
            reason = "空箱"
        # ⚠️ `level=` 必须**当场算出来**（`_chest_subs`）—— 一览里这一行的下一层就是
        #    那只箱子的动作面。忘了挂 = 行在、点开是空（2026-09-29 自验当场抓到）。
        # ⚠️ `count_text` 也得显式给（手动造行不走 `_row_for`）——不给就会掉进
        #    `_render_level` 的兜底"下一层有几行"，印成「2 件」冒充"箱里几件"。
        rows.append(Row(BOX_V, [t], label, reason, _dist(ctx, t),
                        level=_chest_subs(ctx, [t]), count_text=slots, group="设备"))
    if not rows:
        return None
    rows.sort(key=lambda r: r.dist)
    return Level(rows, title=f"📦 箱子一览（{len(rows)} 个 · 敲开哪一个）")


# 📦 一览里"某一只箱子"那一行 —— 点开就是它的动作面（取/存）。
#    和顶层的「箱子」分开：顶层是**合一**的目录行（报总数），这只是**一只**。
BOX_V = Verb("chest_box", "箱子", 0, _chest_can, _chest_reason, _chest_show, "tile",
             subs=_chest_subs, count=_chest_one_count, group="设备")


# ═══════════════════════════════════════════════════════════════════════
# 🏪 商店（买 / 卖）—— 166 ③ 「多选 / 配对」那条的正身
# ═══════════════════════════════════════════════════════════════════════
# 数据来源 = **`/menu`**（不是 `/state`）。`/state.activeMenu` 只给类型，
# 货架明细（`shopItems`）和「这家收什么」（`sellableHere`）**只在 `/menu` 那份里**
# ——同一个东西两个端点各序列化一份，正是 09-27 那次"喂错源、对着能领的碑喊领不了"的形状。
#
# 🔬 两条判据都**问游戏**，都不是我们编的表：
#   · 买：`ShopMenu.forSale` + `itemPriceAndStock`（名字 / 单价 / **库存**）——C# 端点直接吐。
#   · 卖：`ShopMenu.highlightItemToSell(Item)` —— **游戏自己的"这家收不收"**
#     （只读 `categoriesToSellHere` + `tagsToSellHere`，无副作用）。
#     威利鱼店只收鱼和浮漂、皮埃尔不收矿石 —— **不用我们列**。
#
# ⚠️⚠️ 一条游戏事实让两边的**形状不一样**（反编译 + CHANGELOG 实锤）：
#   · **买** = `menu click(item=, quantity=N)`，要几个是几个
#     ⇒ 值得长一层「各多少」（`do(1=4,2=2)` = 1 号买 4 个、2 号买 2 个）。
#   · **卖** = 游戏**单击卖整个堆叠**（`inventory.leftClick` → `chargePlayer(-stack)`），
#     C# `/sell_to_shop` 只认一个名字、卖掉**第一组**就 `break`。
#     ⇒ 卖**没有数量层**。长一屏能填数量的界面 = **骗 AI**（填 3 也是整摞走）。
#     要卖一部分得先拆堆（`menu click action=split`）——那是另一件事，别混进这张单子。
#   ⇒ 同一个动词表里，两个动词的层数不一样，**这是游戏决定的，不是我们偷懒**。

def _shop_goods(ctx):
    """货架上的商品 → `list`，或者 `None` = **这一层判不出来**。

    ⚠️ 调用方要把两种"判不出来"分开（见 `_buy_can`）：
       `ctx.shop is None` ⇒ **没开商店**（确定的"没有"）；
       `ctx.shop` 是真字典但没有 `items` 键 ⇒ 商店开着却**读不出来**（"不知道"）。
    """
    if not isinstance(ctx.shop, dict) or "items" not in ctx.shop:
        return None
    return ctx.shop.get("items") or []


def _sellable_here(ctx):
    """这家**收**我背包里哪些 → `list[显示名]`，或 `None` = 判不出来。

    ⚠️ `sellableHere` 为空和缺失是**两回事**（C# 两处分别写 `[]` 和 `null`）：
       空列表 = 这家**确实什么都不收**；`None` = 问不出来（`heldItem != null` 时游戏
       的判据语义会变，C# 特意不报——**别把那时的结果当"不收"**）。
    """
    if not isinstance(ctx.shop, dict) or "sellable" not in ctx.shop:
        return None
    return ctx.shop.get("sellable")


def _stock_text(g):
    """库存那截。**无限量** ⇒ 一个字都不印。

    ⚠️ 2026-09-30 真机：原来只认 `stock < 0`（"游戏用 `-1` 表示无限"），
    可**真机吐的是 `2147483647`（`int.MaxValue`）** ⇒ 单子上印出 `剩 2147483647`
    （防风草种子那种"无限供应"的货）。⇒ 判据补上 `int.MaxValue` 这一种。
    📌 通式：**"我以为是 -1" ≠ "游戏真的给 -1"** —— 拿真机数据核一遍再用。
    """
    st = g.get("stock")
    if not isinstance(st, int):
        return ""
    if st < 0 or st >= 2 ** 31 - 1:      # -1（老写法）与 int.MaxValue（真机）都是**无限**
        return ""
    return f"剩 {st}"


def _price_text(g) -> str:
    """单价的**真成本** —— 钱 **或** 材料。

    ⚠️⚠️ 2026-09-29 审查抓的：原来只印 `price`，而**易货商品 `Price` 恒 0**
    （克林特升级 / 沙漠商人 / 姜岛商人那种"5 个铜锭换"）⇒ 印出来是 `0g`，
    **看着白拿，点下去真扣材料**。`/menu` 现成带着 `trade`/`tradeCount`/`tradeName`
    （`ModEntry.cs:12349`），兄弟实现 `menu read` 早就印了（`（需 铜锭×5）`）——
    新行不该把这截丢掉。
    ⚠️ 原来还写 `price or 0`：字段缺了印 `0g`，把"不知道"说成"不要钱"。
        ⇒ **缺就不印**（同族老账：`catNum` 缺→0 当硬筛）。
    """
    t = g.get("trade")
    if t:
        tn = g.get("tradeName") or t
        tc = g.get("tradeCount")
        cost = f"{tn}×{tc}" if tc else str(tn)
    else:
        p = g.get("price")
        cost = "" if p is None else f"{p}g"
    st = _stock_text(g)
    return " · ".join(x for x in (cost, st) if x)


def _buy_can(ctx, t):
    """🏪 现在能买吗——商店开着（且读得到货架）才有这一行。

    ⚠️ 不在商店里**不该出现"买"**（铁律：单子上的字都得从游戏读出来；
       "买"对着空气说就是编）。所以判据是 `ctx.shop` 存在，不是"附近有没有店"。
    """
    if ctx.shop is None:
        return CAN_NO
    goods = _shop_goods(ctx)
    if goods is None:
        return CAN_MAYBE          # 开着但读不出来 ⇒ 同三档：不上单子
    return CAN_YES if goods else CAN_NO


def _buy_reason(ctx, t):
    """理由栏 = 审计面。条数由目录行的 `count_text` 报，**这儿不重复**（同屏两个数=两把尺子）。"""
    return f"钱包 {ctx.money}g"


def _buy_count(ctx, targets):
    goods = _shop_goods(ctx) or []
    return f"{len(goods)} 样"


def _buy_subs(ctx, targets):
    """🛒 买：货架 → 选哪几样 → 各多少。

    ⚠️ 货架**整页列出**（不只当前可见那 4 个）：`/menu/click` 是**按名字/ID 找**的
    （内部会 `currentItemIndex` 翻到那一页再点）⇒ 翻页这步**本来就不用 AI 操心**。
    这是新接口白赚的一条：旧接口下 AI 得自己读 `shopPage` 再敲 upArrow/downArrow。
    """
    # ⚠️ 买这边**只按行标签（显示名）去重**，够用：端点是**我们传 `id` 进去**的
    #    （`_exec_buy_multi` 传 `g["id"]`），id 由货架本身保证唯一
    #    ⇒ 没有"另一个字段会撞车"的问题（跟**卖**不一样，那边只能传名字、端点自己遍历）。
    #    真有两样货同名 ⇒ 行分不清 ⇒ 照挑。
    goods, amb = _unambiguous(_shop_goods(ctx) or [],
                              lambda g: g.get("displayName") or g.get("name"))
    rows = []
    for g in goods:
        cn = g.get("displayName") or g.get("name")
        rows.append(Row(BUY_V, [{"good": g}], cn, _price_text(g), 0, where=""))
    lv = Level(rows, title="买哪几样？（可以多选，如 `1,4`）", mode="pick", verb=BUY_V)
    if amb:
        lv.title += (f"\n  ⚠️ 有 {amb} 样**同名**的货没列出来 —— 点了也说不清买的哪一个")
    return lv


def _exec_buy_multi(ctx, pairs, run):
    """🛒 买：**逐条报**（哪样成了、成交几个）。

    ⚠️ 不替游戏编原因：C# 那句 `note` 已经把"可能钱不够/库存不足/背包放不下"
       三件事一起说了，而且它**不知道**是哪个 —— 我们也别猜（同 `_exec_take_multi`）。
    ⚠️ 一个**真后果**要照说：背包满时 C# 会把买到的**丢到脚边**
       （`Game1.createItemDebris`，见 `ModEntry.cs:13828`）⇒ 不是"没买到"，是"买到地上了"。
    """
    lines, ok_n = [], 0
    for row, cnt in pairs:
        g = row.targets[0]["good"]
        cn = g.get("displayName") or g.get("name")
        r = run("buy", {"item": g.get("id") or g.get("name"), "quantity": cnt}) or {}
        got = int(r.get("quantity") or 0)
        if r.get("ok") and got:
            ok_n += 1
            # ⚠️ 少买时把那句原因**原样端过来**；游戏没给就**只说差额**，
            #    **别把 `None` 印给 AI**（那比不说更坏：它会把 None 当成一个值）。
            why = f" —— {r['note']}" if r.get("note") else ""
            extra = f"（要 {cnt} 个，成交 {got} 个{why}）" if got != cnt else ""
            lines.append(f"  · {cn} ×{got}{extra}")
        else:
            lines.append(f"  · {cn} ×{cnt} —— **没买成**"
                         f"（游戏回：{r.get('error') or '一件都没成交'}）")
    return render_receipt("买", f"{len(pairs)} 样", ok_n > 0, note="\n".join(lines))


def _sell_can(ctx, t):
    """💰 现在能卖吗——同 `_buy_can`，另外**得真有东西可卖**（这家收的 ∩ 背包里有的）。"""
    if ctx.shop is None:
        return CAN_NO
    sellable = _sellable_here(ctx)
    if sellable is None:
        return CAN_MAYBE
    return CAN_YES if _sell_pairs(ctx, sellable)[0] else CAN_NO


def _sell_pairs(ctx, sellable):
    """背包里**这家收的** → `([行], 挑出去几摞)`。

    ⚠️ 去重**必须同时给两把尺子**（2026-09-29 审查抓的洞）：
      · **行标签** = `slot["name"]`（= 显示名）——游戏侧 `sellableHere` 也是 `i.DisplayName`
        （`ModEntry.cs:12276`），**筛选就是按这把尺子做的**；
      · **端点认的** = `raw["name"]`（英文内部名，C# `item.Name.Equals(name)`）。
    这个函数原来**筛选用显示名、去重却用内部名** —— 两件不同物品撞同一个显示名时
    两把尺子都放行 ⇒ 单子上出两行**印得一模一样**的候选 ⇒ AI 指哪行都可能卖错东西。
    （`Wine`/`Juice` 都叫「酒」这类在游戏里是有的：货架上就有同显示名不同 id 的商品。）
    """
    here = set(sellable)
    cand = [s for s in ctx.inv if s.get("name") in here and s.get("raw", {}).get("name")]
    return _unambiguous(cand, lambda s: s.get("name"), lambda s: s["raw"]["name"])


def _sell_rows(ctx, sellable):
    rows = []
    for s in _sell_pairs(ctx, sellable)[0]:
        n = s.get("stack") or 1
        val = (s.get("value") or 0) * n
        rows.append(Row(SELL_V, [{"slot": s}], s.get("name"),
                        f"×{n} · {val}g", 0, where=""))
    return rows


def _sell_reason(ctx, t):
    """理由栏 = 审计面：这条能到手多少钱。

    ⚠️ 2026-09-29 审查：原来写「**这家收** N 样」是**说错话** —— `sellableHere`
       的语义是「这家收的 **∩ 我背包里真有的**」（C# `Game1.player.Items.Where(...)`,
       `ModEntry.cs:12276`；`menu read` 印的是"这店收（**背包里卖得掉的**）"，
       一直带着限定语）。照字面读会变成"**这店只收 N 种**"，
       而事实是"我手上只有这 N 种能卖给它"。理由栏说错 = 判据看起来就错。
    ⚠️ 条数由目录行的 `count_text` 报、**跟这里同一个尺子**（都是"点开会看到几行"），
       所以**不在这儿说第二遍**（同屏两个数=两把尺子）。
    """
    total = sum((r.targets[0]["slot"].get("value") or 0) * (r.targets[0]["slot"].get("stack") or 1)
                for r in _sell_rows(ctx, _sellable_here(ctx) or []))
    return f"共 {total}g"


def _sell_count(ctx, targets):
    return f"{len(_sell_rows(ctx, _sellable_here(ctx) or []))} 摞"


def _sell_subs(ctx, targets):
    """💰 卖：**选哪几摞** → 整摞走（没有数量层，见上面那段）。

    ⚠️ 层数比买少一层，**这是游戏决定的**：单击卖整个堆叠。宁可少一层，
       也不给一屏"能填数量"的假界面 —— 那个填了不生效、还不报错。
    """
    sellable = _sellable_here(ctx)
    rows = _sell_rows(ctx, sellable or [])
    amb = _sell_pairs(ctx, sellable or [])[1]
    lv = Level(rows, title="卖哪几摞？（可以多选，如 `1,4`）—— **整摞走**",
               mode="pick", verb=SELL_V, exec_on_pick=True,
               hint="> 敲编号，可以多选（`1,4`）—— 敲了就卖，**这一摞整个走**")
    if amb:
        # ⚠️ 措辞**不写死原因**（原来写"同名但不同品质"，而现在挑出去的有两种：
        #    同显示名 / 同内部名）—— 只说"分不清"，别替游戏编一个它没说的理由。
        lv.title += (f"\n  ⚠️ 背包里有 {amb} 摞**分不清是哪一摞**的没列出来 —— "
                     f"游戏一次只卖第一摞，名字撞车的认不出是哪个")
    return lv


def _exec_sell_multi(ctx, pairs, run):
    """💰 卖：**逐条报**（哪一摞成了、到手多少金）。

    ⚠️ 回执**必须写"整摞卖了 N 个"**（铁律 3）：AI 脑子里那个数（它只报了"1 号"）
       跟真实发生的（整摞走）差着量级，不回显它就会以为自己只卖了 1 个。
    """
    lines, ok_n = [], 0
    for row, _cnt in pairs:
        s = row.targets[0]["slot"]
        cn = s.get("name")
        nm = s["raw"]["name"]
        r = run("sell", {"name": nm}) or {}
        sold = (r.get("sold") or [{}])[0] if r.get("sold") else {}
        n = sold.get("sold") or 0
        # ⚠️ 金额**拿到才印**：缺字段时印 `→ 0g` 就是把"不知道"说成"一分钱没给"
        #    （同族老账：`catNum` 缺 → 0 当硬筛、"空位"缺 → None/None 格）。
        gold = f" → {r['totalGold']}g" if r.get("totalGold") is not None else ""
        if r.get("ok") and n:
            ok_n += 1
            # ⚠️ 只报**事实**（2026-09-29 审查）：
            #   ① 原来写 `n != stack ⇒ "比预想少"` —— 条件是"不等"、话是"少"，
            #      卖出**更多**时回执会自己打自己脸；② 那句"它只卖了第一摞"是**编原因**
            #      （候选层已经保证同名只列一摞，这原因压根不成立），跟 `_exec_take_multi`
            #      刚立的"只报差额、不替游戏编原因"相冲。
            #   `/state` 那份 stack 是**渲染当时**的快照，中间东西变多是可能的
            #   ⇒ 只把两个数摊开，哪个是对的让 AI 自己看。
            was = s.get("stack") or 0
            diff = f"（看单子时是 {was} 个）" if n != was else ""
            lines.append(f"  · {cn} **整摞 {n} 个**{gold}{diff}")
        else:
            lines.append(f"  · {cn} —— **没卖成**（游戏回：{r.get('error') or '一件都没卖'}）")
    return render_receipt("卖", f"{len(pairs)} 摞", ok_n > 0, note="\n".join(lines))


# 🚪 「关掉界面」（2026-10-01）—— **菜单态的那扇真门**。
#
# 起因（恒 2026-10-01 真机抓到、10-02 三步复现的**假门**）：
#   开一个界面（GameMenu / ItemGrabMenu）⇒ `_candidates` 只留 `menu_ok` 的动词，
#   而当时**只有 买/卖** ⇒ **一屏空**，只剩 `0 做点别的…（at x,y 指哪打哪）`。
#   可 `at` 指出来的世界动作**正是** `do_row` 菜单态守卫要挡的东西：
#     ① show     → （这一刻没有可做的 · … —— at x,y 指过去）
#     ② at 24 26 → `1 坐 胡桃木椅子`          ← **看着有路**
#     ③ do 1     → 🚧 菜单开着（GameMenu）—— 1 号是菜单态做不了的动作
#   ⇒ 单子**自己画了一条自己堵死的路**。留一行真能按的出口，这个环才闭合。
#
# ⚠️ `menu_ok=True` 是**必需**的：`_candidates` 在菜单态只放行 `menu_ok`，
#    少了这个位它会被自己那条过滤规则滤掉（= 修了个寂寞）。
# ⚠️ 权重 30（低于 吃50 / 看40 / 买72 / 卖74）：商店开着时正事是买卖，
#    出口行该在、但不该抢头条；而一屏空时它是**唯一**一行，权重无所谓。
def _close_can(ctx, t):
    """🚪 该不该给这一行 —— **只看服务器递来的标题**（`ctx.menu_exit`）。

    ⚠️ 菜单没开 ⇒ `menu_exit` 是空串 ⇒ 不给（这是**菜单态专属**的一行）。
    ⚠️ 捏人页 / 钓鱼小游戏 / 对话框那三族由服务器 `_menu_exit_of` 挡在外面，
       这一层**不自己认菜单名**（认两遍 = 早晚漂）。
    """
    return CAN_YES if (ctx.menu and ctx.menu_exit) else CAN_NO


def _close_show(ctx, t):
    return ctx.menu_exit or "关掉界面"


def _close_reason(ctx, t):
    m = (ctx.menu or {}).get("type") or "?"
    # ⚠️ 2026-10-02 改口：原来写「菜单态**唯一**能按的世界动作」——**这是假话**。
    #    真机上菜单一开，别的行照样在（取/存/选/推进/跳过/确认结算/买/卖…），
    #    而 2026-10-02 真机在 `MasteryTrackerMenu` 里更明显：那一刻**正事是「领取」**
    #    （抬头都写着"可以领"），这行却自称唯一。⇒ 只说**它为真的是那件事**：
    #    菜单开着时**菜单闸门挡着几乎所有工具**，收掉它才能用别的工具。
    return f"{m} 开着 · 收掉它才能用别的工具（菜单闸门）"


def _exec_close(ctx, targets, run):
    m = (ctx.menu or {}).get("type") or "?"
    r = run("close_menu", {})
    # ⚠️ 回执走 `_receipt_from_helper`：**成没成看服务器回读的那句话**，
    #    不在这儿替它下结论（`cancel()` 自己那句是发射后不管的，见 `_im_close_menu`）。
    return _receipt_from_helper(ctx.menu_exit or "关掉界面", m, r)


CLOSE_V = Verb("close_menu", "关掉界面", 30, _close_can, _close_reason, _close_show, "world",
               exec=_exec_close, menu_ok=True)


# 🎓 「领取」（**菜单里的一次性正事**）—— 恒 2026-10-02 点名的**缺门**：
#    精通碑开着那一刻，抬头写着「可以领：神秘树种/宝藏图腾 → `menu click(button=mainButton)`」，
#    可单子上**只有「关掉界面」** ⇒ AI 只看单子就会把那件正事错过去。
#    ⚠️ 判据**全在服务器**（`Ctx.menu_claim` = 这一行的标题；空串 = 这块没得领 ⇒ 整行不出现），
#       这一层**不认菜单名**（跟 `menu_exit` 同一个形状）。
#    ⚠️ 权重 78：菜单态里它排在「关掉界面」（30）前面 —— **那一刻它才是正事**。
#    📌 通式（这一批两次踩到同一个形状）：**"菜单态只有出口行"本身就是一种缺门** ——
#       凡是"抬头上写着某件可做的事、而单子上没有对应行"的地方，都得补一行，
#       否则 AI 的注意力（和它的手）就只剩"关掉界面"。
_CLAIM_V = Verb("menu_claim", "领取", 78,
                lambda c, t: CAN_YES if (c.menu_claim or "") else CAN_NO,
                lambda c, t: f"{c.menu_claim} · 敲了就去领（领完这块就点亮了）",
                lambda c, t: c.menu_claim or "领取", "world",
                exec=lambda c, t, run: _exec_chore(c, t, run, "menu_claim", "领取"),
                menu_ok=True)


# 📜 「领取奖励」（**任务日志里的一次性正事**）—— 恒 2026-10-04：
#    「已完成的任务**打个括号在清单上标注**，完全可以交给我们**一件领取**，
#      再**一条条返回**领取结算的任务详细页面明细（合计结算 n 项，xxx g。
#      任务名字 1 · 详细页里的任务描述 · 3500g；任务 2 · 描述 · 300g）」
# ⚠️ 判据**全在服务器**（`Ctx.quests`＝卡片上游戏自己的 `completed` + `money>0`），
#    这一层**不认菜单名**（同 `menu_claim`/`menu_exit`：认两遍早晚漂）。
# ⚠️ 权重 78（跟精通碑那条同档）：**日志开着的那一刻，领奖就是正事**，压在「关掉界面」(30) 之上。
# ⚠️ 「一件领取」= **一行**；逐条明细走**回执**（`_im_claim_quests` 的原话）——
#    不摊成 N 行：恒要的正是"别一条条点"，摊开就是把它往"一条条敲"上引。
def _quest_items(ctx) -> list:
    return [i for i in ((ctx.quests or {}).get("items") or []) if isinstance(i, dict)]


def _quest_claim_can(ctx, t):
    return CAN_YES if _quest_items(ctx) else CAN_NO


def _quest_claim_show(ctx, t):
    n = len(_quest_items(ctx))
    return f"领 {n} 项奖励" if n else "领取奖励"


def _quest_claim_reason(ctx, t):
    """理由栏 = **已完成的任务（括号标注）** —— 恒原话：「打个括号在清单上标注」。"""
    items = _quest_items(ctx)
    if not items:
        return ""
    bit = "、".join(f"{i.get('name') or '?'} {int(i.get('money') or 0)}g" for i in items[:3])
    if len(items) > 3:
        bit += f"…（共 {len(items)} 项）"
    return (f"合计 {int((ctx.quests or {}).get('sum') or 0)}g（{bit}）"
            f" · 敲了**一次全领**，逐条明细在回执里")


QUEST_CLAIM_V = Verb("quest_claim", "领取奖励", 78, _quest_claim_can, _quest_claim_reason,
                     _quest_claim_show, "world",
                     exec=lambda c, t, run: _receipt_from_helper("领取奖励", "", run("quest_claim", {})),
                     menu_ok=True)


# ═══════════════════════════════════════════════════════════════════
# 📋 菜单摊开：**开着的容器菜单里有什么**（2026-10-01 · P-menus 第一刀）
# ═══════════════════════════════════════════════════════════════════
# 恒 2026-10-01：「**我怕 ai 并不会同时又看选项又看工具**……开着菜单直接把相关内容摊给它」
# 真机病根（`_verify_menu_state_menu.py`）：开箱那一刻单子**一屏空** —— 菜单态只放行
# `menu_ok` 的动词（买/卖），而买/卖要 `ctx.shop`，`ItemGrabMenu` 时它是 `None`。
# ⇒ AI 只能自己去 `menu read`，再把那 500 行文本解析成"箱里有什么"。
#
# ⚠️⚠️ 这条路**只有一条**：开着的容器菜单里点物品 = **领取侧**
#    （`ModEntry.cs` 的 `wantClaim` → `slot=序号`；C# 自己的报错原文就是
#     「领取菜单没有槽位 N（**领取侧共 X 格；read_menu 看 items 序号**）」）。
#    ⇒ `items[].index` **就是**能点的槽位号。所以这里**不套** `_unambiguous`：
#      那是"老 DLL 拿不到格号、端点只按名字认"时的将就，而这条路天生认格号
#      —— 同名两摞（`quality` 相同、`count` 不同）也**点得准**。
#
# ⚠️ 为什么顶层是**一行目录行**、不是把 34 件摊在第一屏：恒那条「菜单是多路口的强暗示」
#    —— 一屏 34 行「取 X」就是在喊"全拿走"。顶层只留一行「箱子里…（34 件）」，
#    点开才是"取哪一件"（同 `箱子…`/`搬走…` 那个形状；`_SUB_N=60` 一次看得完）。
def _menu_box_items(ctx) -> list:
    """开着的容器菜单里**能取的东西**（服务器递进来的 `/menu.items`）。

    ⚠️ 只认 `index` 在的（那是能点的槽位号）。`/menu` 的 `items` 还有**另一种形状**
       （老 DLL 的反射兜底：`{index, field, name, id, stack}`，没有真实格号）
       —— 那种**不认**：列出来就是"点了不知道点的是哪一摞"。
    """
    out = []
    for it in (ctx.menu_data or {}).get("items") or []:
        if isinstance(it, dict) and it.get("index") is not None:
            out.append(it)
    return out


def _menu_take_can(ctx, t):
    """能不能取这一件 —— 判据只有一条：**它有槽位号**（`slot` 是执行那条路的钥匙）。"""
    if not isinstance(t, dict):
        return CAN_NO
    return CAN_YES if t.get("index") is not None else CAN_NO


def _menu_take_show(ctx, t):
    # 同 `_item_row` 的规矩：正文只写名字（**带星级前缀**，否则同名的两摞印成一样的两行），
    # 个数走理由栏 —— 见下。
    return f"取 {_name_with_q(t)}"


def _menu_take_reason(ctx, t):
    # 个数写成 `箱内 ×N`：**跟现成的容器行同一套措辞**（`_item_row` 就是 `箱里 ×{count}`）。
    # ⚠️ 别把它写成正文里的 `×N` —— 那个记号在单子正文里的语义是"这一按会把 N 个都做了"
    #    （`Verb.batch`），两个意思挤在同一列就是"拿错尺子"。
    n = t.get("count")
    return f"箱内 ×{n}" if n else "箱内"


def _exec_menu_take_multi(ctx, pairs, run):
    """取走选中的那几摞（都在这一个开着的容器菜单里）。

    ⚠️ 一回一件、**逐件核实**：C# 的 `claimed` 就是"那件东西还在不在领取侧"
       （它自己回读），我们只转述 —— `ok:true` ≠ 东西动了，这是本项目的老教训。
    ⚠️ 一行可能并着两摞（同名同品质同数量）⇒ 按 `row.targets` 全做，做完如实报几摞。
    """
    got, bad = [], []
    for row, _cnt in pairs:
        for t in (row.targets or []):
            nm = _name_with_q(t)
            try:
                r = run("menu_take", {"slot": t.get("index")})
            except Exception as e:                 # noqa: BLE001
                bad.append(f"{nm}（{type(e).__name__}: {e}）")
                continue
            if not isinstance(r, dict) or not r.get("ok"):
                err = (r or {}).get("error") if isinstance(r, dict) else repr(r)
                bad.append(f"{nm}（游戏回：{err}）")
                continue
            if r.get("claimed") is False:
                # 点了，但那件还在领取侧 ⇒ **没动**（背包满 / 该格被压实挪位）。
                bad.append(f"{nm}（点了，但箱里那摞**没动**——多半是背包满了）")
                continue
            got.append(nm)
    if got and not bad:
        return _receipt_from_helper("取", "、".join(got),
                                    {"ok": True, "st": "yes",
                                     "text": f"已经从开着的箱子里取走 {len(got)} 摞。"})
    if got and bad:
        return _receipt_from_helper("取", "、".join(got),
                                    {"ok": False, "st": "maybe",
                                     "text": "取到了 " + "、".join(got)
                                             + "；**没成的是**：" + "；".join(bad)})
    return _receipt_from_helper("取", "", {"ok": False, "st": "no",
                                          "text": "一件都没取到：" + "；".join(bad)})


def _menu_box_can(ctx, t):
    """该不该给「箱子里…」这一行 —— **只看服务器递没递 `items`**，不看菜单名。

    ⚠️ 这正是"判据只有一处"那条：哪个菜单该摊、摊哪一份，是**服务器**（挨着 C# 契约）
       决定的；这一层照着数据出行为。在这儿再写一遍菜单名名单 = 早晚漂。
    """
    if not ctx.menu:
        return CAN_NO
    return CAN_YES if _menu_box_items(ctx) else CAN_NO


def _menu_box_reason(ctx, t):
    items = _menu_box_items(ctx)
    total = sum(int(i.get("count") or 0) for i in items)
    return f"共 {total} 个" if total else ""


def _menu_box_count(ctx, targets):
    """目录行那截计数 —— **报的是"箱里有几摞"**（`Row.count_text` 那段：别拿"点开有几条"冒充）。"""
    return f"{len(_menu_box_items(ctx))} 摞"


def _menu_box_show(ctx, t):
    # 🏛️ 2026-10-05（补24）**这一份不是箱子**：真机博物馆领奖那份 `ItemGrabMenu` 是"游戏递给你的一堆东西"
    #    （`containerAt`/`grabBehavior` 都空、又不是送礼菜单 ⇒ 服务器给的那位 `reward`）——
    #    恒点名「『箱子里…』这个行文对奖励菜单**不贴**」⇒ 这一档改口叫「奖励」。
    #    ⚠️ 判据**在服务器**（C# 那三个事实），这一层只认 `reward` 这一位，不认菜单名。
    return "奖励" if (ctx.menu_data or {}).get("reward") else "箱子里"


def _menu_box_subs(ctx, targets):
    """「箱子里…」的下一层 —— **一摞一行**（每行一个能点的槽位）。

    ⚠️ 形状照 `_sell_subs`：`mode="pick"` + `exec_on_pick`（**敲了当场做，不再进数量层**）。
       为什么**不给数量层**：单击整摞取走是**游戏自己定的**量，给一屏能填数量的假界面
       = 填了不生效、还不报错（那条老陷阱）。
    """
    items = _menu_box_items(ctx)
    rows = [Row(MENU_TAKE_V, [it], _menu_take_show(ctx, it), _menu_take_reason(ctx, it),
                0, where="") for it in items]
    return Level(rows, title="📦 取哪几摞？（可以多选，如 `1,4`）—— **整摞进背包**",
                 mode="pick", verb=MENU_TAKE_V, exec_on_pick=True,
                 hint="> 敲编号，可以多选（`1,4`）—— 敲了就取，**那一摞整个进背包**")


MENU_BOX_V = Verb("menu_box", "箱子里", 58, _menu_box_can, _menu_box_reason, _menu_box_show,
                  "world", subs=_menu_box_subs, count=_menu_box_count, menu_ok=True)
# ⚠️ **只给 `exec_multi`、不给 `exec`** —— 这不是省事，是**它不配出现在顶层**：
#    `_candidates` 的判据是「有 exec **或** 有 subs」（两个都没有 = 看得见按不动），
#    给了 `exec` 它就会被顶层扫出来 —— 真机当场照出过一屏 `取 [金]啤酒花 / 取 蔓越莓…`
#    （`_menu_box_items` 的目标是**全箱**，跟"哪一格"无关）⇒ 顶层 34 行、目录行反而被淹。
#    ⚠️ 这也正是现成的 `TAKE_V`/`STORE_V` 的做法（它们同样只有 `exec_multi`）：
#      **只活在子层里**的动词，就该长成"顶层扫不到"的样子。
MENU_TAKE_V = Verb("menu_take", "取", 58, _menu_take_can, _menu_take_reason, _menu_take_show,
                   "menu", exec_multi=_exec_menu_take_multi)


# 📥 「存…」——**开着的容器**那一侧（2026-10-01 · P-menus 第五刀）。
#
# 恒定的形（设计稿 §10.2 那张表）：`ItemGrabMenu` 开着时单子该长成「箱内容摊成行（取/存）」。
# 182 只做了「取」（真点领取侧那一下），这一刀把「存」补齐。
#
# ⚠️⚠️ **存这一侧不去点界面，走现成的 `/store`** —— 三条理由：
#   ① 单子上「存」在**箱子关着**时早就有（`_chest_subs` ③，166 ⑤ 那批）⇒ 两条路必须
#      **同一套语义**（同一个 `_storable_slots` 过滤、同一份逐条回执、同样按 `slot`/`quality`
#      精确挑摞）。另写一套"点背包格 → 点箱子格"必然跟它漂开（本项目的老病）。
#   ② 真机实测（2026-10-01，同一只箱子）：菜单开着时 `/store` 放进去的东西
#      **当场出现在菜单里**（`Chest.GetItemsForPlayer()` 交出去的就是那份 `Items`，
#      菜单画的正是同一个列表）⇒ 恒在屏幕上**看得见**，不是"数据动了、画面没动"。
#   ③ 「取」之所以非点界面不可：领取侧那一下**游戏自己会做压实/换格**，直操列表反而会写出
#      菜单画不出来的状态；而"往里放"这边 `/store` 用的正是游戏自己的 `chest.addItem`。
#
# ⚠️ 坐标**不猜**：由服务器递进来的 `menu_data["at"]`（C# 报"这个界面属于哪一格容器"，
#    判据在 C# 的 `BuildContainerAt`：拿 `ItemGrabMenu` 的 `sourceItem`/`context` 去
#    `CollectStorageChests` 那张表里认对象身份 —— 冰箱那种"不在 loc.objects 里"的容器
#    因此也对得上）。拿不到 / 在这张图上对不出容器 ⇒ **整行不出现**（同三档：绝不猜一只箱子往里面放）。
def _menu_box_at(ctx):
    """这只开着的容器是**哪一格** → `box`（`/scan_chests` 认过的那份，**带 x/y**）/ `None`。

    ⚠️ `x/y` 是**自己补上去的**：`/scan_chests` 的箱子明细里没有坐标（坐标在 tile 上），
       而 `/store` 只认坐标 ⇒ 补成 `_chest_subs` 那个形状（`dict(box, x=…, y=…)`），
       两条路的执行器（`_exec_store_multi`）才吃同一份。
    """
    at = (ctx.menu_data or {}).get("at") or {}
    x, y = at.get("x"), at.get("y")
    if not (isinstance(x, int) and isinstance(y, int)):
        return None
    b = _box(ctx.tile(x, y))
    return dict(b, x=x, y=y) if b else None


def _menu_store_pick(ctx) -> list:
    """这一刻**放得进去**的背包格子（判据与"箱子关着"那条**同一个函数**）。"""
    box = _menu_box_at(ctx)
    if box is None or _box_space(box) is not CAN_YES:
        return []
    return _storable_slots(ctx, box)[0]


def _menu_store_can(ctx, t):
    """该不该给「存…」这一行 —— 真判据 = "这一刻**真有东西**放得进去吗"。

    ⚠️ 箱子满了 ⇒ `_menu_store_pick` 是空的 ⇒ 整行不出现（**不是**给一行按了不成的）。
       那一刻的出路就在同屏的「箱子里…」上（先取点出来）—— 同 `_chest_subs` 那条笔记的用意。
    """
    return CAN_YES if _menu_store_pick(ctx) else CAN_NO


def _menu_store_show(ctx, t):
    return "存"


def _menu_store_reason(ctx, t):
    """理由栏 = **容器还剩几格**（跟关着箱子时那条一字不差 —— 同一件事同一把尺子）。"""
    b = _menu_box_at(ctx) or {}
    free = b.get("freeSlots")
    return f"箱空 {free} 格" if free is not None else ""


def _menu_store_count(ctx, targets):
    return f"{len(_menu_store_pick(ctx))} 种"


def _menu_store_subs(ctx, targets):
    """「存…」的下一层 —— 一摞一行，形状**照抄** `_chest_subs` ③（含那个数量层）。"""
    box = _menu_box_at(ctx) or {}
    here = f"({box.get('x')},{box.get('y')})"
    rows = [_slot_row(s, box, MENU_STORE_ROW_V) for s in _menu_store_pick(ctx)]
    return Level(rows, title=f"存哪几样去 {here}？（可以多选，如 `1,4`）",
                 mode="pick", verb=MENU_STORE_ROW_V)


MENU_STORE_V = Verb("menu_store", "存", 57, _menu_store_can, _menu_store_reason,
                    _menu_store_show, "world", subs=_menu_store_subs,
                    count=_menu_store_count, menu_ok=True)
# ⚠️ 子层那个动词**只给 `exec_multi`**（同 `MENU_TAKE_V`/`STORE_V`）：只活在子层里、
#    顶层扫不到。它走的是**现成的** `_exec_store_multi` —— 存法一份，两条路共用。
# ⚠️ `target="inv"`：这一行说的是"背包里那一摞"（复验那条路认得 `idx`）。这里的 `idx`
#    跟 `_exec_store_multi` 发给端点的是**两把尺子**（1-based 位次 vs 0-based 格号）——
#    差一位那件事的账记在 `_exec_store_multi` 里，别在这儿再算一遍。
MENU_STORE_ROW_V = Verb("menu_store_row", "存", 57, _menu_store_can, None,
                        _menu_store_show, "inv", exec_multi=_exec_store_multi)


# ⏭ 「跳过整段」（2026-10-01 · 恒要的第二行）。
#
# 恒原话：「**给选项的话就给 1 接 advance、2 跳过 好了**」——所以它必须**排在「推进对话」后面**
# （advance 76，这个 74），而不是抢在它前面：那一刻的正事是"接着看"，跳过是**退路**。
#
# ⚠️ 判据 = 游戏自己的 `skippable`（`/state.activeEvent.skippable`）——
#    `menu skip` 走 `currentEvent.skipEvent()`，**没有这个位就跳不动**（跳不动它会明说）。
#    没事件时**整行不出现**（不是灰掉）：`skipEvent()` 那时会退化成"按 ESC 关菜单"，
#    那是**另一件事**，写在这一行上就是假承诺。
# ⚠️ 理由栏**必须写代价**（恒那条"警告/危险必须带路、带代价"）：跳过 = 这段剧情**不播了**。
def _skip_can(ctx, t):
    ev = ctx.event or {}
    if not ev.get("id"):
        return CAN_NO
    return CAN_YES if ev.get("skippable") else CAN_NO


def _skip_show(ctx, t):
    return "跳过整段"


def _skip_reason(ctx, t):
    ev = ctx.event or {}
    return f"这场演出可跳过（{ev.get('id')}）—— **剧情就不播了**"


def _exec_skip(ctx, targets, run):
    r = run("skip", {})
    return _receipt_from_helper("跳过整段", "", r)


SKIP_V = Verb("skip_event", "跳过整段", 74, _skip_can, _skip_reason, _skip_show, "world",
              exec=_exec_skip, menu_ok=True)


# 🗳 「选 …」——**对话选项各一行**（2026-10-01 真机：罗宾那句「美学设计棒极了 / 四根柱子有点浪费」）。
#
# 为什么非有不可：`_advance_can` 在有选项时**故意不给「推进」**（按了只会读回同一屏），
# 那一刻要是选项也不上单子，单子就只剩「跳过整段」—— 等于**把 AI 领进一个自己不给路的状态**
# （同「坐着没给起身」那次的病）。
#
# ⚠️ 选项数据从 `/state.activeMenu.responses` 来（**字符串数组**，`ModEntry.cs:5323`），
#    服务器已把它整成 `[{index, text}]` —— `index` 就是 C# 点选项用的那个号
#    （`selectedResponse = option` → `responseCCs[option]`），所以**位次即答案号**。
# ⚠️ 执行走 `/menu/click {option: N}`（就是状态条一直在教 AI 的那条路），
#    **不自己发明按键**（`confirm` 选不了选项，那是老坑）。
def _menu_options(ctx) -> list:
    """这一刻能选的答案（没有就空）——**三档**（前两档从服务器递进来的 `menu_data` 拿）：

    · 💬 **对话选项**（`dialogue.options`）：`index` 就是 C# 点选项用的号（位次即答案号）；
    · 🗿 **图标选择题**（`choose.options`，2026-10-04 恒：「矮人国王雕像是有选项的，
      **按理来说要套一层选择题**」）：那屏 `responses` 是 null、**只能按坐标点** ⇒
      每条带 `kind="choose"` + 自己的 `x/y`（真机那屏还有两个空文本诱饵，服务器已经滤掉）；
    · 🧬 **升级选职业**（`ctx.levelup`，2026-10-04 恒 B 批：「升级职业的**没有叉叉，不能关掉**」
      +「把那行撤掉，换成**真选项**」）：那一刻这屏**没有别的出路**，两个分支就是全部能做的事
      ⇒ 跟对话选项同一个形状（`kind="levelup"`，`side` = 左/右，执行走 `menu levelup_choose`）。
      ⚠️ 它**不在 `menu_data` 里**（那屏的 `offered` 在 `/state.activeMenu`），所以是唯一
        从 `ctx.levelup` 取的一档 —— 判据仍只有一处（服务器 `_im_levelup`）。
    ⚠️ 三档**共用这一张表**（单子上都印成「选 …」）——执行侧按 `kind` 分岔，别再各写一套。
    """
    md = ctx.menu_data or {}
    out = [o for o in ((md.get("dialogue") or {}).get("options") or [])
           if isinstance(o, dict) and o.get("index") is not None]
    for o in ((md.get("choose") or {}).get("options") or []):
        if isinstance(o, dict) and o.get("text"):
            out.append(dict(o, kind="choose"))
    for _side in ("left", "right"):
        _o = (ctx.levelup or {}).get(_side) or {}
        if _o.get("name"):
            out.append({"kind": "levelup", "side": _side, "index": None,
                        "id": _o.get("id"), "text": _o.get("name")})
    return out


def _option_can(ctx, t):
    if not isinstance(t, dict):
        return CAN_NO
    # 🧬 职业分支**没有位次号**（那屏不是 responses）—— 判据是"服务器给了这个 side"。
    if t.get("kind") == "levelup":
        return CAN_YES if t.get("side") in ("left", "right") else CAN_NO
    # 🏛️ 柜台那两档（2026-10-05 补24）：判据**在服务器**（`kind` 只按游戏自己的
    #    `responseKey` 给：`Donate`/`Collect`）—— 这一层**不认文本、不认菜单名**。
    if t.get("kind") == "museum_donate":
        return CAN_YES
    return CAN_YES if t.get("index") is not None else CAN_NO


def _option_show(ctx, t):
    txt = t.get("text") or "?"
    # 🧬 职业分支印「选 垂钓者」（**不加书名号**：那不是一句台词，是一个职业名）。
    if t.get("kind") == "levelup":
        return f"选 {txt}"
    # 🏛️ 柜台那两档（恒 2026-10-05：「柜台的菜单 1.捐 2.领 3.走 的 1 **接我们自己的捐赠方法**」）：
    #    行文必须让 AI **一眼看出"捐"不是游戏那个手动摆界面的入口**（恒特别强调这句）。
    #    ⚠️ 不写「选「向博物馆捐赠」」那种话——那是游戏原文的文案，按下去走的却是我们的路。
    if t.get("kind") == "museum_donate":
        return "捐（我们的自动捐）"
    # ⚠️ 两条选项**一字不差**时会撞车（同一个桶键 ⇒ 并成一行，而执行器只发一个答案）。
    #    真出现时补个位次区分 —— 只在撞车时才付这个字数（同 `_eat_show` 那条星级前缀的理由）。
    same = [o for o in _menu_options(ctx) if (o.get("text") or "") == (t.get("text") or "")]
    return f"「{txt}」" if len(same) < 2 else f"「{txt}」（第 {t.get('index', 0) + 1} 个）"


def _option_reason(ctx, t):
    """⛔ 2026-10-04 恒：「**这个（1/2）是什么意思，会误导吗**？选项不是 123 吗」——
    对，**会误导**：单子上的行号本身就是 1/2/3，理由里再印一个「(1/2)」是**两套编号打架**
    （而那个 1/2 其实是"第几个答案／共几个"，只有对话那档才有位次的含义）。
    ⇒ 不再印位次，只印**总数**（真事实、且跟行号不冲突）。
    位次只在两条选项**一字不差**时才补 —— 那时不补就分不开（见 `_option_show`）。
    🧬 职业分支那档要说的是**代价**（恒那句"选了就定了"是这类决策的关键信息）。
    🏛️ 柜台那两档（2026-10-05 补24）要说的是**"这一按到底走哪条路"** ——
       恒点名的那件事：**捐**走的是我们自己的 `/museum_donate`（一次把包里能捐的都捐上），
       **不是**游戏那个手动摆放界面；**领**是选游戏那个「领」再把奖励一次收进包。
    """
    if t.get("kind") == "levelup":
        lu = ctx.levelup or {}
        other = "right" if t.get("side") == "left" else "left"
        alt = (lu.get(other) or {}).get("name") or "?"
        return (f"{lu.get('skill') or '技能'} Lv{lu.get('level')} 分支 · **选了就定了**（不可逆）"
                f"｜另一个是「{alt}」")
    if t.get("kind") == "museum_donate":
        return ("**我们的自动捐**：一次把包里能捐的都捐上 —— **不走**游戏那个手动摆放界面"
                "（游戏原文那项是「向博物馆捐赠」）")
    n = len(_menu_options(ctx))
    return f"共 {n} 个选项，敲哪个就选哪个" if n > 1 else "唯一的选项"


def _exec_option(ctx, targets, run):
    """🗳 选一个答案 —— 回执走 helper 那条（**它自己会回读核实**：选项那屏过没过）。

    ⚠️ 别在这儿替它下结论，也别自己拼"键发出去了"那种话（本项目的老账：
       `ok:true` ≠ 事真发生了）。
    ⚠️ 两档走**两条路**（判据在服务器算好递进来的 `kind` 上，这一层不认菜单类型）：
       · 💬 对话选项 ⇒ `menu_option {option: N[, real]}`（`real` 判据 = `_question_needs_real`）；
       · 🗿 图标选择题 ⇒ `menu_icon {x, y}`（那屏没有 responses，只能按坐标点）。
    🏛️ 2026-10-05（补24）柜台那一档：`museum_donate` ⇒ **我们的自动捐**（不点游戏那项，
       所以**根本不发 `menu_option`**；回执在服务器 helper 里回读后生成）。
    ⛔ 「领」**没有专属 kind** —— 恒 2026-10-05 拍板「**好，保持吧。**」＝ 保持三步
       （选游戏那一项 `Collect` → 「箱子里…」→ 「取 …」→ 关掉界面），所以它走**普通选项行**；
       原来那条"一键领完"的 `museum_collect` / `museum_collect_row` 已拆干净，**别再引回来**。
    """
    t = targets[0] if targets else {}
    txt = t.get("text") or "?"
    # 🧬 升级选职业（2026-10-04 恒 B 批）：那屏 `responses` 是 null、点也点不动
    #    （`LevelUpMenu.receiveLeftClick` 空），只有 `menu levelup_choose` 一条路。
    if t.get("kind") == "levelup":
        r = run("levelup_choose", {"side": t.get("side")})
        return _receipt_from_helper("选职业", f"「{txt}」", r)
    if t.get("kind") == "choose":
        r = run("menu_icon", {"x": t.get("x"), "y": t.get("y"), "key": t.get("key")})
        return _receipt_from_helper("选", f"「{txt}」", r)
    # 🏛️ 柜台「捐」：走我们自己的自动捐（服务器 helper 自己回读、自己出回执）
    if t.get("kind") == "museum_donate":
        return _receipt_from_helper("捐（我们的自动捐）", "", run("museum_donate_row", {}))
    # 🗳 「要不要 real=true」由**服务器**算好递进来（判据 `_question_needs_real`）——
    #    这一层**不自己认框种类**（猜错就是静默点空，真机 2026-10-01 当场照过一次）。
    _d = (ctx.menu_data or {}).get("dialogue") or {}
    r = run("menu_option", {"option": t.get("index"), "real": _d.get("real")})
    return _receipt_from_helper("选", f"「{txt}」", r)


OPTION_V = Verb("menu_option", "选", 76, _option_can, _option_reason, _option_show,
                "option", exec=_exec_option, menu_ok=True) 


# 👕 「穿戴」那**一行目录行 + 两个子动词（穿 / 脱）2026-10-02 整体撤出单子** ——
#    理由与替代路见文件上方（`_WORN_SLOTS` 那段墓碑注释）：权重最低 ⇒ 空场景里常驻；
#    功能**没少**，只是搬到 `daily(ops="wear", …)`（跟 `sleep` 同一个家）。



# 🎬 「推进对话」（2026-10-01）—— 剧情/对话框那一刻**唯一该按的**东西。
#
# ⚠️ 为什么它得进单子：对话/事件开着时，`_candidates` 在菜单态只留 `menu_ok` 的动词，
#    而当时没有任何一个是"推进" ⇒ **又是一屏空**（同 `CLOSE_V` 那扇假门，只是换了个由头）。
#    真机上这正是最常见的一屏：AI 走到 NPC 面前搭话，然后单子什么都不给。
#
# ⚠️ **有选项时不给这一行**：那一刻该按的是 `menu click(option=N)`（选项号游戏自己发）。
#    给了「推进对话」，按下去只会原地读回同一屏选项 —— 那是"看得见、按了白按"。
#    没这一行时空白屏会印 `_close_hint` 的原话，那里面就写着"有选项走 click(option=N)"。
def _advance_can(ctx, t):
    m = ctx.menu or {}
    # ⚠️⚠️ 2026-10-01 真机抓到的洞：**有选项时不许给「推进」** —— 而原来"事件在播"那条
    #    排在**前面**，于是"事件 + 选项"这个组合（真机就是它：罗宾那句美学 vs 浪费）
    #    照样给行 ⇒ 按下去 `advance_story` 走到选项就**停**、只把同一屏选项读回来
    #    = **看得见、按了白按**（这段注释的下半段早就写着这条规矩，是**顺序**把它架空了）。
    #    ⇒ 判据顺序改成：**选项优先**（那一刻该按的是「选 …」那几行）。
    if m.get("type") == "DialogueBox" and m.get("responses"):
        return CAN_NO
    if (ctx.event or {}).get("id"):
        return CAN_YES                      # 事件在播（节日期间恒真，那是事实不是挡路）
    if m.get("type") == "DialogueBox":
        return CAN_YES
    return CAN_NO


def _advance_show(ctx, t):
    return "推进对话"


def _advance_reason(ctx, t):
    if (ctx.event or {}).get("id"):
        e = ctx.event or {}
        # ⚠️ 2026-10-01：这里原来尾巴挂着「· 可整段跳」—— 歧义（说的是"这个事件**可以**跳"，
        #    读起来像"这一按会整段跳"）。**整段跳现在有自己的行**（`SKIP_V`）⇒ 这句只说
        #    它自己干什么：一句句往下推，**到选项或演完为止**（`advance_story` 是循环，不是推一句）。
        return f"剧情在播（{e.get('id')}）—— 一句句往下推，**到选项或演完为止**"
    return "对话框开着，一句句推"


def _exec_advance(ctx, targets, run):
    # 回执走 helper 那条（它自己会回读确认 + 报"推了几次、收了几句"）——
    # 这一层**不替它下结论**（`advance_story` 内部有 stuck/选项/结束三种结局）。
    r = run("advance", {})
    return _receipt_from_helper("推进对话", "", r)


# 🧾 「确认结算」（2026-10-01）—— **过夜结算屏（ShippingMenu）上的正确那一下**。
#
# ⚠️ 为什么不能靠 P0-a 那个通用的「关掉界面」：`cancel()` 走的是「按 ESC + menu_close」，
#    而结算屏是**要点 ok 才算完**的那一族（`_close_hint` 对 shipping 的原话就是
#    "menu click(button=ok) 确认关掉（交付/结算类要点 ok 才算完）"）。
#    我那条"关不掉就如实说"的验收用例，用的正是 `ShippingMenu` —— 也就是说：
#    只给「关掉界面」，AI 按下去大概率得到一句"还开着"。
#    ⇒ 这一族**不给出口行**（见服务器 `_menu_exit_of`），改给这一行。
#
# ⚠️ 它同时是**和恒的双人确认**：结算屏是两人一起进新一天的门。
#    单子上出现它，AI 才知道"该等的人到齐了没有、要不要现在推"。
def _settle_can(ctx, t):
    return CAN_YES if (ctx.menu or {}).get("type") == "ShippingMenu" else CAN_NO


def _settle_show(ctx, t):
    return "确认结算"


def _settle_reason(ctx, t):
    return "过夜结算屏 · 点了两人一起进新一天"


def _exec_settle(ctx, targets, run):
    r = run("settle", {})
    return _receipt_from_helper("确认结算", "", r)


# 🗑 「投出货箱」（2026-10-01）—— **目录行**（一件一行，跟「卖」同形）。
#
# ⚠️ **为什么不给一行"全部投放"**：`sell_to_bin(sell_all=True)` 是把背包里**能卖的全投**，
#    那是一把大锤（"我这批是要留着的"它也照投）。单子第一条规矩是"出现的那条按了就成"，
#    所以摆上来的必须是**具体投哪件**，而不是"投一切"。要"全投"走 `menu ops=bin sell_all=True`
#    ——那条路是 AI **自己带着意图**去调的（同「睡觉」撤出单子的道理）。
#
# ⚠️ **门禁 = 站在农场**：箱子在农场（`sell_to_bin` 自己会从 `map_data` 的
#    `buildings[type=="Shipping Bin"]` 找出来 + 走过去）。在矿洞里摆一行"投出货箱"
#    等于劝它跑一趟腿 —— 那是**规划**，不是这一刻的"最优解"（恒那条规矩）。
#    ⚠️ 这里**不自己写"农场"以外的箱子名单**：岛上的箱子是另一回事，等真机碰到再说。
#
# ⚠️⚠️ **判据必须问游戏**（`Item.canBeShipped()`），**不能用 `sellable`**：
#    2026-10-01 真机抓到 —— 照 `sellable`（`IsSellable`，只排 -99/-98/-97/-96）
#    列出来 14 件，里面有 `水手帽`/`宝箱`/`熔炉`/`小桶`/`珍奇乌鸦`，而这些**一件都进不去**：
#      · 反编译 `Object.canBeShipped()`：`bigCraftable` ⇒ **false**（宝箱/熔炉/小桶…）
#      · 反编译 `Item.canBeShipped()`：**基类直接 `return false`** —— 帽/衣/靴/戒/饰品
#        那些**根本不是 `Object`** ⇒ 全都投不了。
#    ⇒ 这一位由 C# 的 `/state` 直接吐（`canBeShipped()`），并用 **caps 把住关**：
#      **老 DLL 没有这一位 ⇒ 这一行整个不出现**（宁可不给，也不给一行"按了不成"的）。
def _bin_items(ctx):
    return [t for t in ctx.inv if t.get("shippable") is True]


def _bin_can(ctx, t):
    if ctx.cap("state_shippable") is None:
        return CAN_NO          # 这版 DLL 不吐 `shippable` ⇒ 判不出来 ⇒ 不给（不猜）
    return CAN_YES if (ctx.loc == "Farm" and _bin_items(ctx)) else CAN_NO


def _bin_show(ctx, t):
    return "投出货箱"


def _bin_reason(ctx, t):
    n = len(_bin_items(ctx))
    return f"{n} 件可投 · 箱子在这张图" if n else ""


def _bin_count(ctx, targets):
    n = len(_bin_items(ctx))
    return f"{n} 件" if n else None


def _bin_subs(ctx, targets):
    # 🗑 2026-10-04 恒：「**投一件不实用。一般这格的堆叠全投。然后报一下这些物品的数量**」
    #    ⇒ 两处都按他的话改：① 行上**直接印数量**（`×N`）——`/sell` 本来就是**整摞走**
    #    （C# `HandleSell` 把 `item.Stack` 整个塞进出货箱，`ModEntry.cs:12531`），
    #    旧文案「敲一件投一件」既不准确、也让人以为只投 1 个；② 标题写明**整摞**。
    rows = [Row(BIN_V, [t], f"投 {_name_with_q(t)}",
                f"×{int(t.get('stack') or 1)} 可卖", 0)
            for t in _bin_items(ctx)]
    if not rows:
        return None
    return Level(rows, title="🗑 投哪几件进出货箱？（**每样整摞全投** · 要全投走 `menu ops=bin`）")


def _exec_bin(ctx, targets, run):
    t = targets[0]
    # ⚠️ 传**内部名**（`raw.name`）—— 跟「卖」同一个口径（C# 按 `item.Name` 找）。
    nm = (t.get("raw") or {}).get("name") or t.get("name")
    r = run("bin", {"name": nm})
    return _receipt_from_helper("投出货箱", t.get("name") or "", r)


BIN_V = Verb("bin_one", "投出货箱", 0, lambda c, t: CAN_YES,
             lambda c, t: "可卖", lambda c, t: f"投 {_name_with_q(t)}",
             "inv", exec=_exec_bin)


# 🪨 「砸 晶球…」（2026-10-01 · 恒「**锻造晶球先吧**」）—— 铁匠铺柜台前的**批量**动作。
#
# 形状（跟「投出货箱」同族）：顶层一行目录，点开是**一件事**（砸晶球），再进 `各多少` 填数量。
# ⚠️ 为什么不做成"一件一行"（跟「投」那样）：执行器 `process_geodes(count)` 是**按游戏自己的
#    顺序**砸的（Geode→Frozen→Magma→Omni），**不收"砸哪一种"** ⇒ 列成一行一件就是
#    "按了砸的不是你说的那颗"（假承诺）。要精确挑种类得先给那条 op 加 `type`（记进账）。
# ⚠️ 判据（"背包里哪些是晶球"）**问游戏**：`is_geode` = C# 的 `Utility.IsGeode()`
#    （`GeodeMenu.HighlightItems` 用的同一把尺子）。**老 DLL 没这一位 ⇒ 整行不出现**。
# ⚠️ 门禁 = **在铁匠铺**（同「投出货箱」在农场的写法）：砸晶球得站克林特柜台前，
#    在别处摆这一行 = 劝它跑一趟腿（那是**规划**，不是这一刻的最优解）。
# ⚠️ `GEODE_COST` 是**游戏常量**（`GeodeMenu.cs:133` `Game.Money >= 25`；C# 那两条
#    `/process_geode*` 里也是写死的 25）—— 这里复制一份只为"钱不够就别给这一行"。
#    要彻底干净得让 C# 把它报出来（`/status` 或那条 op 的返回值），**下次动 C# 时收拾**。
GEODE_COST = 25


def _geodes(ctx) -> list:
    """背包里的晶球（老 DLL / 背包里没有 ⇒ 空表 ⇒ 那一行不出现）。"""
    return [t for t in ctx.inv if t.get("is_geode") is True]


def _geode_total(ctx) -> int:
    return sum(int(t.get("stack") or 0) for t in _geodes(ctx))


def _geode_can(ctx, t):
    """🪨 砸晶球：**背包里有晶球 + 钱够 + 克林特营业中**（恒 2026-10-01 新门禁）。

    ⚠️ 老形状是"**只有站在铁匠铺里**才给"——恒：「**不建议放农场**，做成背包检测：
       有各种晶球**且克林特营业中**可以报」⇒ 现在两条任一路：
         · **在铁匠铺**（老判据，保留：人在店里/营业时段内）；
         · **背包检测**：有晶球 + `ctx.clint_open`（服务器用现成的休息日表 + `SHOP_HOURS` 算的）。
       ⚠️ 但**农场不给**（恒那句"不建议放农场"）：那是"顺路顺手"的地方，跑一趟铁匠铺是**规划**。
    """
    if ctx.loc == "Farm":
        return CAN_NO
    n = _geode_total(ctx)
    if not n:
        return CAN_NO
    if int(ctx.money or 0) < GEODE_COST:
        return CAN_NO
    return CAN_YES if (ctx.loc == "Blacksmith" or ctx.clint_open) else CAN_NO


def _geode_show(ctx, t):
    return "砸 晶球"


def _geode_reason(ctx, t):
    n = _geode_total(ctx)
    if not n:
        return ""
    _shop = "（铁匠铺营业中，走过去砸）" if (ctx.clint_open and ctx.loc != "Blacksmith") else ""
    return f"背包共 {n} 颗 · {GEODE_COST}g/颗{_shop}"


def _geode_count(ctx, targets):
    n = _geode_total(ctx)
    return f"{n} 颗" if n else None


def _geode_subs(ctx, targets):
    """下一层 = **一件事**（砸晶球）→ 再进数量层（`号=数量`）。"""
    n = _geode_total(ctx)
    if not n:
        return None
    return Level([Row(GEODE_V, [None], "砸 晶球",
                      f"背包共 {n} 颗 · {GEODE_COST}g/颗 · 铁匠铺一次一颗", 0, where="")],
                 title="🪨 砸晶球（铁匠铺柜台前）", mode="pick", verb=GEODE_V)


def _exec_geode_multi(ctx, pairs, run):
    """🪨 砸：走现成的 `menu geode`（它自己会走到克林特柜台前），**把它的话原样带回来**。

    ⚠️ 走 `helpers` 那档（回一句话）：`_im_run` 照它**开头**判 yes/maybe/no（❌/⚠️/其余），
       所以这里**不替它下结论** —— 逐条把它的原话贴进回执。
    """
    parts, ok_n = [], 0
    for _row, cnt in pairs:
        r = run("geode", {"count": int(cnt or 1)}) or {}
        if r.get("st") == "yes":
            ok_n += 1
        parts.append(str(r.get("text") or r.get("error") or "（没回话）").strip())
    return render_receipt("砸晶球", f"{len(pairs)} 批", ok_n > 0, note="\n".join(parts))


GEODE_V = Verb("geode", "砸晶球", 72, _geode_can, _geode_reason, _geode_show, "world",
               subs=_geode_subs, count=_geode_count, exec_multi=_exec_geode_multi)


# 🔨 「重铸饰品」（2026-10-01 · 恒「锻造的话，**铱锭够/不够/没开饰品精通**检查一下有没有写好」）。
#
# 游戏事实（真机量的 + wiki/反编译同源，见 CHANGELOG 187）：
#   · 铁砧（`(BC)Anvil`）**每次重铸吃 3 块铱锭**，10 个游戏分钟后出料（实测：铱锭 3→0）；
#   · **不是所有饰品都能重铸** —— `Object.OutputAnvil`：`if (!trinket.GetTrinketData().CanBeReforged)`
#     弹红字返回 null。实测：**蜥怪的爪子（Basilisk Paw）** 就是"交互回报 true 却什么都不做"那颗
#     （wiki 原话：「other than the Basilisk Paw or Magic Hair Gel」）。
#     ⚠️ 这一问**不能**拿 `/machine_reqs` 的 `canPlace` 探针代答（我原来正是这么以为的）：
#        反编译 `PlaceInMachine`（`Object.cs:2472-2476`）是 `if (probe) return true;` ——
#        探针**在 `OutputMachine` 之前就返回**，而 `CanBeReforged` 在 `OutputMachine` 里；
#        真机实测蜥怪的爪子探针照样回 `true`。⇒ 判据取 C# `/state.inventory[].canReforge`。
#   · 铁砧/迷你锻造台/饰品槽都**由战斗精通解锁**（`MasteryTrackerMenu.cs:128` case 4）。
#
# ⇒ 三条状态是这么落的：
#   ① **够** —— 这一行出现，理由栏写「要 3 铱锭（有 3）」；
#   ② **不够** —— 这一行**不出现**（按了不成的不许上单子），改由**状态条**说清缺几块（见 `_im_reforge_hint`）；
#   ③ **没开精通** —— 结构上就不可能：没有战斗精通 ⇒ 没有铁砧 ⇒ `_anvils(ctx)` 空 ⇒ 这一行不出现。
#
# ⚠️ 判据**全问游戏**：这一行要的两件事——
#    · "背包里哪颗饰品**能**重铸" = C# `/state.inventory[].isTrinket` + `.canReforge`（见 `_bag_trinkets`）；
#    · "还要几块铱锭 / 我有几块" = `/machine_reqs` 的 `requirements`（`AdditionalConsumedItems` + `CountId`）。
#    两件事都在服务器算好后塞进 `Ctx.reforge`（这一层**不许自己打 HTTP**、也不许编"3"这个数）。
# 🔨 重铸（铁砧）：判据/形状的账写在 `REFORGE_V` 上面那一大段。
# ⚠️ 背包那侧的判据（"哪几件是饰品"）**不在这一层** —— 在服务器的 `_bag_trinkets()`：
#    真机量过 `/state` 里饰品是 `catNum: 0`（**不是 -101**）⇒ 曾经那个 `TRINKET_CAT = -101`
#    是**错的、而且是死代码**（写在这儿没人用，真判据在服务器），已删。


def _reforge_can(ctx, t):
    """这一刻**能不能重铸** —— 判据 = 服务器探针递进来的那一份（`Ctx.reforge`）。

    ⚠️ **三条门禁全在服务器那一处**（`_im_reforge_probe`，恒 2026-10-01 复述的形状）：
       ① **战斗精通已领**（`_mastery_claimed("combat")` —— 没领就 `{}`，这一行不出现）；
       ② 背包里有**能重铸**的饰品（`/state.inventory[].isTrinket` + `canReforge`）；
       ③ 场景里有一台**空着的**铁砧 + 铱锭够（`/machine_reqs` 的 `canPlace`/`requirements` 只问不做）。
       ⇒ 这一层只读 `can is True`（**别自己再判一次精通**：判据只有一处，两处必然漂）。
    ⚠️⚠️ 必须 `can is True`，**不能只看"有 item"**：`Ctx.reforge` 在**铱锭不够**时也带着
        `item`（那是抬头要用来说缺口的信息）⇒ 只看 item 就会把"按了不成"的那一行摆上去。
       （这条是自验当场逮到的：`🔨 铱锭不够 ⇒ 不给` 报了红。判据要盯**动作能不能做**，
         不是"这条信息在不在"。）
    """
    r = ctx.reforge or {}
    return CAN_YES if (r.get("can") is True and r.get("item")) else CAN_NO


def _reforge_show(ctx, t):
    return "重铸饰品"


def _reforge_reason(ctx, t):
    r = ctx.reforge or {}
    if not r.get("item"):
        return ""
    return f"要 {r.get('need')} 铱锭（有 {r.get('have')}）· {r.get('item')} · 10 分钟"


def _reforge_count(ctx, targets):
    r = ctx.reforge or {}
    return f"{r.get('need')} 铱锭" if r.get("item") else None


def _reforge_subs(ctx, targets):
    """一件一行（这一刻**探针说能重铸**的那件）；敲了当场做（**没有数量层**）。"""
    r = ctx.reforge or {}
    if r.get("can") is not True or not r.get("item"):
        return None
    return Level([Row(REFORGE_V, [dict(r)], f"重铸 {r.get('item')}",
                      f"({r.get('x')},{r.get('y')}) 的铁砧 · 吃 {r.get('need')} 铱锭",
                      0, where="")],
                 title="🔨 重铸哪件饰品？（敲了就开炉 —— **属性会重掷**）",
                 mode="pick", verb=REFORGE_V, exec_on_pick=True)


def _exec_reforge_multi(ctx, pairs, run):
    """🔨 重铸：走 `_im_reforge`（hold + 走过去 + 交互 + **回读核实**），逐条把原话带回来。"""
    parts, ok_n = [], 0
    for row, _cnt in pairs:
        t = row.targets[0] or {}
        r = run("reforge", {"x": t.get("x"), "y": t.get("y"), "item": t.get("item")}) or {}
        if r.get("st") == "yes":
            ok_n += 1
        parts.append(str(r.get("text") or r.get("error") or "（没回话）").strip())
    return render_receipt("重铸饰品", f"{len(pairs)} 件", ok_n > 0, note="\n".join(parts))


REFORGE_V = Verb("reforge", "重铸饰品", 76, _reforge_can, _reforge_reason, _reforge_show, "world",
                 subs=_reforge_subs, count=_reforge_count, exec_multi=_exec_reforge_multi)




# 「买 / 卖」两个动词 = **目录行**（顶层只报有几样，点开才发号）。
# ⚠️ 它们**同一个对象**既是顶层那条（`subs`/`count`）又是子层的执行者（`exec_multi`）——
#    容器那边分成了 `chest` / `chest_take` 两个对象，是因为顶层扫的是"图上的格子"（`tile`）
#    而子层的目标是"这个容器"。买卖没有"格子"：它在 **world** 这一档（问的是"现在这个处境"），
#    顶层和子层指向的是同一个东西 ⇒ 一个对象就够，分成两个反而多一处会漂的重复。
BUY_V = Verb("buy", "买", 72, _buy_can, _buy_reason, lambda c, t: "买", "world",
             subs=_buy_subs, count=_buy_count, exec_multi=_exec_buy_multi, menu_ok=True)
# ⚠️⚠️ 2026-10-04 **真机抓的误导**（恒让我「菜单交互多去试试」时撞出来的）：
#     这个 `buy` **是"商店货架"**（`_buy_can` 判 `ctx.shop`、`_buy_subs` 摊的是 `/menu` 的 goods），
#     **不是买动物**。买动物是 `farm(ops="buy")` → `buy_animal`（走到玛妮柜台那条），两码事。
#     上一版把 label 改成了「买 动物（玛妮柜台）」——**改错了对象**：真机站在皮埃尔种子店，
#     单子第 2 行印成「2 买动物… ← 55 样 · 钱包 2364712g」（55 样＝种子）。
#     ⇒ label 收回光秃秃的「买」。**这条 label 不准再出现"动物"三个字**（有钉子钉住）。
#     ⚠️ 恒那个问题（「买动物叫 buy 会不会被误会」）问的是**域 op**，答案在 `farm` 的 help
#        （「**买动物**(会先走到玛妮柜台再下单)」）＋ `_INTENT_INDEX` 的「买动物,买鸡,买牛 → farm buy」
#        —— 域名 `farm` 已经把"这是农活那一支"说清了，不需要动这行的 label。
SELL_V = Verb("sell", "卖", 74, _sell_can, _sell_reason, lambda c, t: "卖", "world",
              subs=_sell_subs, count=_sell_count, exec_multi=_exec_sell_multi, menu_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# 🚪🐄 放牧（开棚门）/ 关棚门 —— 2026-10-01 恒：「放牧（开关畜棚鸡舍门）做进选项了吗？」
# ═══════════════════════════════════════════════════════════════════════
# 当时**没做进单子**：只有 `farm doors`（关），而且 `farm` 域把 `放牧` 错接到了 `pet_walk`（摸动物）。
#
# ⚠️ **判据全在 `Ctx.doors`**（服务器 `_im_doors` 算好的：本档几个动物建筑 + 今天雨不雨/冬不冬）——
#    这一层是**纯函数**：`can()` 不打 HTTP、也不自己认"哪些建筑算动物建筑"（同 `mwork`/`reforge`）。
#
# ⚠️ **一次只给一行，靠钟点分**（写进 `can()`，不是靠排序）：
#    早上开门放牧、晚上关门防野生动物 —— 两件事**互斥**，同屏既有"开"又有"关"就是自己打自己。
#    06:00–15:00 给「放牧」；≥17:00 或 <06:00 给「关棚门」；**16:00 那一小时两行都不给**
#    （"算不准就不出现"，不兜底）。
# ⚠️ 为什么"雨天/冬天不给开"要进 `can()` 而不是只调权重：单子第一条规矩是
#    「**出现的那条，按了就成**」—— 雨天摆一行"去开门放牧"就是劝 AI 白跑一趟
#    （雨/冬天动物本来也不出去吃草）。
#
# ⚠️ **两行打的是同一个 op**（`run("doors", …)`）：C# `/toggle_doors` **忽略 action、纯翻转**，
#    端点就一个 ⇒ 方向只能是**这一行的意图**，由 exec **看回执里的门态按目标态最多再翻一次**
#    （见 `_doors_exec`）；**不许**再长出"保证开/保证关"的第二条实现（名字带方向却翻成反面=谎报）。
# ⚠️ **两行都缺一条"沉底"判据**（恒 2026-10-01：「门已经开着就沉底」/「关好之后沉底」）——
#    它要**只读门态**，而 C# 里**没有**：`/toggle_doors` 是**翻转**端点，读一次 = 翻一下。
#    ⇒ **这版不做**；记成 C# 批次待办（跟 `building.animalDoor` 的偏移一起）：
#      给 `/farm_buildings` 补 `animalDoorOpen`（只读）+ `animalDoorX/Y`；
#      拿到之后这两行就能按"当前门态 vs 本行目标态"沉底（已经是目标态 ⇒ 不给行/压到最底）。
_DOORS_OPEN_H0, _DOORS_OPEN_H1 = 6, 15     # 放牧：06:00–15:00（含两端）
# 🚪 2026-10-02：门态**现在能只读**了（C# `/farm_buildings` 给 `animalDoorOpen` + `animalDoorX/Y`）
#    ⇒ 两行按"当前门态 vs 本行目标态"**沉底**（恒：「门已经开着就沉底 / 关好之后也沉底」）——
#    重量压到下面这个值，`can()` 那条时间闸照旧（沉底 ≠ 不给行）。
_DOORS_SUNK_W = 10
_DOORS_CLOSE_H = 17                        # 关棚门：≥17:00 或 <06:00


def _hour_of(ctx):
    """钟点的小时数（`ctx.time` = `"13:20"`）；读不出来 → `None`（**不是 0**）。

    ⚠️ 读不出来**当"算不出"**（那两行都不出现），别退成 0 —— 0 点会变成"夜里"，
       于是早上那行永远不出现、晚上那行永远出现（静默错一整类，同 `_clock_of` 那个坑）。
    """
    try:
        return int((ctx.time or "").split(":")[0])
    except (TypeError, ValueError):
        return None


def _doors_ready(ctx):
    """两条共同的闸门：**站在农场** + **本档确实有动物建筑**。→ (那份账, 过没过)"""
    d = ctx.doors or {}
    if ctx.loc != "Farm":
        return d, False                  # 门在农场；在矿里/城里摆这两行 = 劝它跑一趟腿
    if int(d.get("builds") or 0) <= 0:
        return d, False                  # `builds=0` = 问清了没有；`{}` 也走这条（0 兜底=算不出）
    return d, True


def _doors_state_clause(ctx) -> str:
    """门态那半句（**只在读得出来时才写**）。

    🚪 2026-10-02：C# 现在给 `animalDoorOpen`（只读）⇒ 这儿可以**如实**说一句；
       **缺键（`unknown>0`）或没建筑就一个字都不写**（宁缺勿编：不能把"读不出来"说成"都开着"）。
    """
    d = ctx.doors or {}
    n = int(d.get("builds") or 0)
    if n <= 0 or int(d.get("unknown") or 0) > 0:
        return ""
    o = int(d.get("open") or 0)
    c = int(d.get("closed") or 0)
    if o >= n:
        state = f"{n} 栋门**都开着**"
    elif c >= n:
        state = f"{n} 栋门**都关着**"
    else:
        state = f"{n} 栋里**开着 {o} 栋**、关着 {c} 栋"
    return f" · 现在{state}"


def _doors_sink_weight(static_w: int, want_open: bool):
    """门态决定权重：**已经是本行目标态 ⇒ 沉底**（恒：「**门已经开着就沉底 / 关好之后也沉底**」）。

    ⚠️ 沉底 = **权重压到最低**（`_DOORS_SUNK_W`），**不是不给这行** ——
       翻门端点是无状态的翻转，人偶尔确实要"反着来再敲一次"；把它藏掉反而更难用。
    ⚠️ 门态**读不出来**（缺键 / `unknown>0` / 没建筑）⇒ **不沉底**（照原权重出现），
       也**不假称**门态（理由栏见 `_doors_state_clause`）。
    ⚠️ 动态权重**只能走** `Verb.weight_fn` → `_weight_of()` 这一条路（文件上方那条规矩）。
    """
    def _fn(ctx) -> int:
        d = ctx.doors or {}
        n = int(d.get("builds") or 0)
        if n <= 0 or int(d.get("unknown") or 0) > 0:
            return static_w                      # 读不出来 ⇒ 不沉底、不改权重
        if want_open and int(d.get("open") or 0) >= n:
            return _DOORS_SUNK_W
        if (not want_open) and int(d.get("closed") or 0) >= n:
            return _DOORS_SUNK_W
        return static_w
    return _fn


def _doors_open_can(ctx, t):
    """🐄 放牧：农场 + 有动物建筑 + **非雨天 + 非冬天** + 06:00–15:00。"""
    d, ok = _doors_ready(ctx)
    if not ok or d.get("rain") is True or d.get("winter") is True:
        return CAN_NO
    h = _hour_of(ctx)
    if h is None:
        return CAN_NO
    return CAN_YES if _DOORS_OPEN_H0 <= h <= _DOORS_OPEN_H1 else CAN_NO


def _doors_open_show(ctx, t):
    return "放牧（开棚门）"


def _doors_open_reason(ctx, t):
    """理由栏：**为什么** + **下一步**（能直接照抄的 op+参数，见"警告必须带路"那条规矩）。

    ⚠️ 别在这儿写"开完门记得 `farm animals` 摸一遍"（恒 2026-10-01：「**关着门也可以 animals 摸一遍**，
       我记得是自动跨建筑摸的。不建议加这一句」）—— `care_animals` 自己会走进每一栋畜舍。
    ⚠️ 门态那半句只在**读得出来**时才拼（`_doors_state_clause`）——读不到**一个字都不许提门态**。
    """
    n = int((ctx.doors or {}).get("builds") or 0)
    return (f"本档 {n} 栋动物建筑 · **开了门动物才会出去棚外吃草**（雨天/冬天不给这行）"
            f"{_doors_state_clause(ctx)}"
            f" · 敲了先走到棚门口再翻；回执**逐栋报执行后的门态**，"
            f"想反着来再敲一次 `farm(ops=\"doors\")`")


def _doors_close_can(ctx, t):
    """🚪 关棚门：农场 + 有动物建筑 + 钟点 ≥17:00（或 <06:00）。"""
    d, ok = _doors_ready(ctx)
    if not ok:
        return CAN_NO
    h = _hour_of(ctx)
    if h is None:
        return CAN_NO
    return CAN_YES if (h >= _DOORS_CLOSE_H or h < _DOORS_OPEN_H0) else CAN_NO


def _doors_close_show(ctx, t):
    return "关棚门"


def _doors_close_reason(ctx, t):
    n = int((ctx.doors or {}).get("builds") or 0)
    return (f"本档 {n} 栋动物建筑 · **天黑了：关门防野生动物袭击牲畜**"
            f"{_doors_state_clause(ctx)}"
            f" · 敲了先走到棚门口再翻；回执**逐栋报执行后的门态**，"
            f"想反着来再敲一次 `farm(ops=\"doors\")`")


def _doors_at_target(states, want: bool) -> bool:
    """这一串门态**到目标态了吗**（`want=True` 要全开）。

    ⚠️ 吃的是**状态串**（`snap`/`entries` 里那一列），**不是**"名字→态"的字典 ——
       同名两栋（两个 Deluxe Coop）在字典里会并成一条，**少算一栋就可能误判"全到了"**（2026-10-03 改）。
    ⚠️ 有一条 `None`（未确认/够不着）或空表就**不算到了** —— 那两种都"不知道"，不能当成了。
    """
    states = list(states or [])
    if not states:
        return False
    return all((v is True) if want else (v is False) for v in states)


def _doors_exec(ctx, targets, run, want: bool):
    """🚪🐄 放牧/关棚门：**都只调同一个翻转 op**（`doors`），再按目标态**只对没到位的那几栋**收敛一次。

    ⚠️ 方向是**这一行的意图**，不是端点的能力（`/toggle_doors` 忽略 action、纯翻转）⇒
       敲完**看回执里逐栋的门态**：没到目标态就**再翻那几栋**（翻转端点翻两次回原状，所以**每扇门最多一次**，
       不来回抖）。
    ⚠️ 收敛那一发**也走位**（`only=[没到位的门坐标]`）：🧭 2026-10-03 起 C# **只翻玩家 4 格内的门**，
       "人已经在门口"这个前提不再成立（第一趟本来就是**逐栋**走的）；而且"再翻一次全部"会把
       刚翻好、就在旁边的那栋**翻回去** —— 所以收敛必须**按门坐标点名**，不是 `walk=False` 再翻全部。
    ⚠️ 回执把话说全：**目标态 + 实际门态**（读不到就明说读不到），没到目标态时给下一步。
    """
    verm, tgt = ("开棚门", "全开") if want else ("关棚门", "全关")
    # 🚪 「关棚门」带上**意图**：服务器那侧会先问游戏"外面还有动物吗"——
    #    有（或判不出来）就**报错、不翻**（恒 2026-10-01：「还有在棚外的话报错不关」）。
    _args = {"walk": True, "want": "open" if want else "close"}
    r = run("doors", _args) or {}
    _d = (r.get("doors") if isinstance(r, dict) else None) or {}
    _ents = (r.get("entries") if isinstance(r, dict) else None) or []
    if not _ents:
        # 老 DLL / 老形状（没有 `entries`）⇒ 退回"名字→态"那份（**没有门坐标**，也就没法点名收敛）
        _ents = [{"name": k, "state": v} for k, v in _d.items()]
    # 🚪 目标态判据**用服务器递过来的 `bad`**（`_doors_bad` 一处，2026-10-04 收成一份）——
    #    老形状没有这个键时才退回本地这份等价的 lambda（只为兼容，别在这边长出第二套判据）。
    _bad = (r.get("bad") if isinstance(r, dict) else None)
    if not isinstance(_bad, list):
        _tgt_ok = (lambda st: st is True) if want else (lambda st: st is False)
        _bad = [e for e in _ents if not _tgt_ok(e.get("state"))]
    if _bad and all(isinstance(e.get("x"), int) and isinstance(e.get("y"), int) for e in _bad):
        r2 = run("doors", {"walk": True,
                           "only": [{"x": e["x"], "y": e["y"]} for e in _bad]}) or {}
        if isinstance(r2, dict) and r2.get("entries"):
            # ⚠️ 把**第一趟的走位事实**补回最终回执（`walk` 是结构化字段，不是从文案里抠）——
            #    2026-10-01 真机逮到的洞：人**真走到**了门口（`[walk] … 到位`），
            #    可 AI 看到的回执里一个字都没提 —— 那两头都是谎。
            _wl = str(r.get("walk") or "")
            _tx = str(r2.get("text") or "")
            if _wl and _wl not in _tx:
                r2 = dict(r2, text=(_wl + "\n" + _tx), walk=_wl)
            r = r2
            # 🐛 2026-10-03 自验代理逮到：收敛那发**只重翻了没到位的几栋** ⇒ 直接拿它的 `entries`
            #    会把**已经到位**的栋从下面「🎯 实际=…」那行里抹掉（不是谎报，但 AI 读不出全貌，
            #    想核对"到底几栋开着"还得再敲一次）。⇒ 按「名字 + 门坐标」**合并**：
            #    第一趟的顺序保序，第二趟的同键条目覆盖成新状态；只在第二趟出现的也补进来。
            def _ek(e):
                return (e.get("name"), e.get("x"), e.get("y"))
            _merged = {_ek(e): e for e in _ents}
            for e in (r2.get("entries") or []):
                _merged[_ek(e)] = e
            _seen, _new = set(), []
            for e in _ents:
                _new.append(_merged[_ek(e)])
                _seen.add(_ek(e))
            for k, e in _merged.items():
                if k not in _seen:
                    _new.append(e)
            _ents = _new
    got = "、".join(
        f"{e.get('name')} " + ("开" if e.get("state") is True
                              else ("关" if e.get("state") is False else "**未确认**"))
        for e in _ents) or "**没读到门态**"
    head = _receipt_from_helper(verm, f"（目标 {tgt}）", r)
    # ⚠️ 被"外面还有动物"那道闸拦下时（`blocked`）：**一个字都不许提门态** ——
    #    我们压根没翻、也没读门态，"实际=没读到门态"读起来像"翻了但读不到"（两回事）。
    if isinstance(r, dict) and r.get("blocked"):
        return head
    line = f"\n   🎯 目标={tgt} · 实际={got}"
    if _ents and not _doors_at_target([e.get("state") for e in _ents], want):
        line += "—— 还没到就**再敲一次** `farm(ops=\"doors\")`（翻转端点，敲一次变一次）"
    return head + line


def _exec_open_doors(ctx, targets, run):
    """🐄 放牧：收敛到**全开**。"""
    return _doors_exec(ctx, targets, run, want=True)


def _exec_close_doors(ctx, targets, run):
    """🚪 关棚门：收敛到**全关**。"""
    return _doors_exec(ctx, targets, run, want=False)


OPEN_DOORS_V = Verb("opendoors", "放牧（开棚门）", 84, _doors_open_can,                    _doors_open_reason, _doors_open_show, "world", exec=_exec_open_doors,
                    # 🚪 门态已经是"全开" ⇒ 沉底（恒 2026-10-02）；读不出来就不沉
                    weight_fn=_doors_sink_weight(84, want_open=True))
CLOSE_DOORS_V = Verb("doors", "关棚门", 70, _doors_close_can,
                     _doors_close_reason, _doors_close_show, "world", exec=_exec_close_doors,
                     # 🚪 门态已经是"全关" ⇒ 沉底（恒 2026-10-02）；读不出来就不沉
                     weight_fn=_doors_sink_weight(70, want_open=False))


# ═══════════════════════════════════════════════════════════════════════
# 🌿 六件"顺手就做"的活（2026-10-01 恒「接吧」＝ 把 P1 那批**空参行**接上单子）
# ═══════════════════════════════════════════════════════════════════════
# 这些 op **早就有了**（`scene ops="berry"/"spot"/"moss"/"pan"` · `fish ops="crab_collect"`
# · `farm ops="milk"`），`_INTENT_INDEX` 里也一直指着它们 —— **只是没上单子**。
# ⚠️ 判据**全在 `Ctx.chores`**（服务器 `_im_chores` 算好的）——这一层不打 HTTP、不认名单。
# ⚠️ 执行**只调现成 op**（`run("berry"/"spot"/…)`，`_im_run` 里挂的就是那几个域 op）——
#    **不在 exec 里另写一套 HTTP**。
# ⚖️ 权重按"顺手 + 收益"分开给（别堆同一个数）：蟹笼 68（有货就是钱、且就在水边）·
#    浆果 66（走过去摇一下就有）· 斑点 64（收益高但要带锄头、要逐格挖）· 挤奶剪毛 62（日常，慢）·
#    淘金 60（点固定、要先有闪光点）· 苔藓 58（只有绿雨/开了设置才有）。
def _chore_n(ctx, key):
    """那笔账里的个数（读不出来 → 0）。"""
    try:
        return int((ctx.chores or {}).get(key) or 0)
    except Exception:
        return 0


def _exec_chore(ctx, targets, run, op, cn):
    """🌿 顺手活：**只调现成的那个 op**，把它的话原样带回来（不替它下结论）。"""
    r = run(op, {})
    return _receipt_from_helper(cn, "", r)


# 1) 摇/摘 树上的东西（`scene ops="berry"` → `berry_run` 现成脚本）
#    ⚠️ 2026-10-02 恒：「**茶树也值得摇**，不过确实不是同一件事」+「**对哦……好多果树也可以摇**」——
#       **动作是同一个**（走过去对着树/丛按一下），摇出来的东西不同：
#       野浆果丛(size0/1/2)→树莓/黑莓直接进包 · 茶树丛(size3)→茶叶 · 核桃丛(size4)→金核桃（存档计数）
#       · **果树(`FruitTree`)→果子掉地上，得再走上去捡**（`berry_run` 摇完补了"走过去捡"那一段）。
#    ⇒ 所以**只留一行**（一个动作一行是恒的口径），行文按本图有什么自己念。
def _shake_kinds(c):
    """本图现在摇得出什么 → `[("berry", n, "摇 浆果丛"), …]`（判据都在服务器 `_forage_counts`）。"""
    out = []
    for _k, _lbl in (("berry", "摇 浆果丛"), ("tea", "摘 茶叶"),
                     ("walnut_bush", "摇 金核桃"), ("fruit_tree", "摇 果树(摘果子)")):
        _n = _chore_n(c, _k)
        if _n:
            out.append((_k, _n, _lbl))
    return out


def _shake_label(c, t):
    """这一行怎么念：本图有什么就念什么。"""
    _k = _shake_kinds(c)
    if not _k:
        return "摇 树上的"
    return " + ".join(x[2] for x in _k)


def _shake_reason(c, t):
    """理由栏：**分开说清**每一类几处 + 果子那类要"摇下来再捡"（别让 AI 以为它会自己进包）。

    🍵 2026-10-03：本图**有茶树但一丛都摇不出来**时，这里**必须说为什么**（恒当天真机就问过
    「暂时摇不下来茶」）—— 沉默会被 AI 读成"这图没茶树"。判据是游戏自己的 `Bush.inBloom()`（size3）：
    「长了 ≥20 天 **且** 当月 22 号起 **且**（非冬季 或 室内/盆栽）」。
    """
    _k = _shake_kinds(c)
    if not _k:
        _w = _chore_n(c, "tea_wait")
        if _w:
            return (f"🍵 本图有 **{_w} 丛茶树**，但现在**摇不出茶叶** —— 游戏的原条件"
                    f"（反编译 `Bush.inBloom()` size3）是「长了 ≥20 天 **且** 当月 **22 号**起 "
                    f"**且**（非冬季 或 室内/盆栽）」；盆栽茶树算「室内」⇒ 只等**日期/成熟度**。到点再敲这一行")
        return ""
    _bits = []
    for _key, _n, _lbl in _k:
        if _key == "fruit_tree":
            _bits.append(f"🍎果树×{_n}(挂果 {_chore_n(c, 'fruit_n')} 个——**摇下来在地上，要再走上去捡**)")
        elif _key == "tea":
            _tp = _chore_n(c, "tea_pot")
            _bits.append(f"🍵茶树丛×{_n}(茶叶好了" + (f"·其中盆栽 {_tp}" if _tp else "") + ")")
        elif _key == "walnut_bush":
            _bits.append(f"🌰核桃丛×{_n}(金核桃是**存档计数**，不进背包)")
        else:
            _bits.append(f"🍓浆果丛×{_n}")
    return ("本图该摇：" + "、".join(_bits) + " · **拟人逐处走过去摇**（敲了不用给参数）"
            " · 摇完回读背包/核桃数，不空口说")


BERRY_V = Verb("berry", "摇 浆果丛", 66,
               lambda c, t: CAN_YES if _shake_kinds(c) else CAN_NO,
               _shake_reason,
               _shake_label, "world",
               exec=lambda c, t, run: _exec_chore(c, t, run, "berry", "摇/摘树上的"))
# 2) 挖 远古斑点（`scene ops="spot"` → `spot_run`；**要带锄头**，没锄头服务器不给这笔账）
#    ⚠️ **只算身边**（恒 2026-10-01：「不需要特定跑大老远锄！**扫一下周围**」）——
#       半径由服务器递（`chores["spot_r"]`，常量在 `_SPOT_RADIUS` 一处），理由栏**写出来**。
SPOT_V = Verb("spot", "挖 远古斑点", 64,
              lambda c, t: CAN_YES if _chore_n(c, "spot") else CAN_NO,
              lambda c, t: (f"附近 {int((c.chores or {}).get('spot_r') or 0)} 格内 "
                            f"{_chore_n(c, 'spot')} 处**可挖的斑点/姜点**（锄头在手）"
                            f" · 出古物/矿物/季节种子 · 敲了逐格挖完，不用给坐标"),
              lambda c, t: "挖 远古斑点", "world",
              exec=lambda c, t, run: _exec_chore(c, t, run, "spot", "挖斑点"))
# 3) 刮 苔藓（`scene ops="moss"` → `moss_run`；**只有绿雨天或 `settings moss on` 才有账**）
# 🌿 2026-10-04 恒：「**农场里面的苔藓可以不用报，除非特地只读** —— 因为有的玩家会特地培养
#    等苔藓扩散，**我不建议刮家里的**」⇒ 「刮 苔藓」那行**从单子上撤掉**（定义删掉，别留死代码；
#    `VERBS` 里也不再列它）。
#    ⚠️ **能力一条没删**：要读就走 `scene ops=moss kw={"dry_run":True}`（只列不刮，
#    `moss_run.py:316`）—— 那正是他说的"特地只读"的口子；`Ctx.chores["moss"]` 那笔账也照旧算
#    （`_forage_summary` 的"🌿 可采集"还在用它）。
# 4) 收 蟹笼（`fish ops="crab_collect"`；只算 `readyForHarvest` 的那几个）
CRAB_V = Verb("crab", "收 蟹笼", 72,
              lambda c, t: CAN_YES if _chore_n(c, "crab") else CAN_NO,
              lambda c, t: (f"本图 {_chore_n(c, 'crab')} 个蟹笼**有货**（收完笼是空的）"
                            f" · 收完笼是空的 —— 想继续抓得再放饵（`fish ops=\"crab_bait\"`）"),
              lambda c, t: "收 蟹笼", "world",
              exec=lambda c, t, run: _exec_chore(c, t, run, "crab", "收蟹笼"))
# 5) 淘 金（`scene ops="pan"` → `_pan_run`；账里带闪光点坐标 + 锅**在手/戴头上**——
#    ⚠️ 2026-10-04 恒：「只有手上有各种级别的陶盘（或者头上…）才报」⇒ 判据在服务器
#    `_im_chores`（`panInHand` / 帽子栏名字带盘），这一层的理由栏照抄它给的 `how`，**别再写死"在手"**。
PAN_V = Verb("pan", "淘 金", 60,
             lambda c, t: CAN_YES if (c.chores or {}).get("pan") else CAN_NO,
             lambda c, t: ("水下闪光点 ({x},{y}) · 锅{how} · 淘完**回到出发那岸**"
                           " · 敲了自己走过去淘，不用给坐标").format(
                               x=(c.chores.get("pan") or {}).get("x"),
                               y=(c.chores.get("pan") or {}).get("y"),
                               how=(c.chores.get("pan") or {}).get("how") or "在手"),
             lambda c, t: "淘 金", "world",
             exec=lambda c, t, run: _exec_chore(c, t, run, "pan", "淘金"))
# 6) 挤奶 / 剪毛（`farm ops="milk"`；只算 **本图** `productReady` 的牛·山羊/绵羊）
MILK_V = Verb("milk", "挤奶 / 剪毛", 62,
              lambda c, t: CAN_YES if (_chore_n(c, "milk") or _chore_n(c, "shear")) else CAN_NO,
              lambda c, t: ("本图能挤 " + str(_chore_n(c, "milk")) + " 只（牛/山羊）"
                            " · 能剪 " + str(_chore_n(c, "shear")) + " 只（绵羊）"
                            " · 会先走到动物旁边再动手（棚里那批也一起）"),
              lambda c, t: "挤奶 / 剪毛", "world",
              exec=lambda c, t, run: _exec_chore(c, t, run, "milk", "挤奶剪毛"))


# 7) 🗑️ 翻垃圾桶（`scene ops="garbage"` → `trash_run`；恒 2026-10-02：「捡垃圾可以上」）
#    权重 56：**每天每桶一次**、掉落看运势（刮刮乐），**不是待办** —— 排在顺手活那批的下面，
#    别去抢"每天一次、过了就作废"那几档（蟹笼 72 / 放牧 84 / 收作物 88…）的位。
#    ⚠️ 账有两份（2026-10-02 晚 C# 之后）：`garbage` = 本图桶数 · `garbage_left` = **今天还没翻的**
#       （= 游戏 `CheckedGarbage`，由 `/scan` 的 `garbageChecked` 带上来）。
#       · 知道还剩 0 个 ⇒ **整行不出现**（"按了也是空的"这种事不该占单子 —— 恒的"过了就作废"口径）；
#       · 老 DLL 报不出这个字段 ⇒ `None` = **不知道**（不是"都没翻"）⇒ 照老文案说，别下结论。
def _trash_can(c, t):
    """这行该不该给：桶数 > 0 **且**（翻没翻不知道 **或** 今天还有没翻的）。"""
    if not _chore_n(c, "garbage"):
        return CAN_NO
    _left = (c.chores or {}).get("garbage_left")
    if _left is not None and int(_left) <= 0:
        return CAN_NO
    return CAN_YES


def _trash_show(c, t):
    """理由栏：知道剩几个就**说清楚**（这才是"你按下去会不会有东西"的答案）。"""
    _n = _chore_n(c, "garbage")
    _left = (c.chores or {}).get("garbage_left")
    if _left is None:
        return (f"本图 {_n} 个垃圾桶"
                f" · **每天每桶一次**（翻过的再翻是空的，掉落看当天运势）"
                f" · 敲了自己扫图逐个翻，不用给坐标")
    return (f"本图 {_n} 个垃圾桶，**今天还没翻的 {int(_left)} 个**"
            f" · 掉落看当天运势（翻过的再翻是空的）"
            f" · 敲了自己扫图逐个翻，不用给坐标")


TRASH_V = Verb("garbage", "翻垃圾桶", 56, _trash_can, _trash_show,
               lambda c, t: "翻垃圾桶", "world",
               exec=lambda c, t, run: _exec_chore(c, t, run, "garbage", "翻垃圾桶"))


# 8) 🥤 买 Joja 可乐（`_im_run("cola")`；恒 2026-10-02 当天两改：先要"状态条提示"，看过就说
#    「**不用了，不要加状态条了，上单吧**」+「酒吧的交互项本来也不多」）
#    权重 54：**花钱的趣味项**（75g/瓶，效果=一瓶可乐）⇒ 排在"顺手活"那批的下面。
#    账 = `Ctx.chores["cola"]`（服务器从 Action 瓦片 `ColaMachine` 认出来的**右半台**坐标，
#    判据只那一处、不问地图名）—— 理由栏把**价格**和**能得到什么**都写出来（花钱的必须先说价）。
def _cola_can(c, t):
    return CAN_YES if (c.chores or {}).get("cola") else CAN_NO


COLA_V = Verb("cola", "买 Joja 可乐 (75g)", 54,
              _cola_can,
              lambda c, t: (f"可乐机在 ({(c.chores.get('cola') or {}).get('x')},"
                            f"{(c.chores.get('cola') or {}).get('y')}) · 花 **75g** 买一瓶 Joja 可乐"
                            f"（谢恩最爱 / 雷欧喜欢）· 敲了自己走过去买，不用给坐标"),
              # ⚠️ 屏上那行取的是 `show`（不是 `label`）——**花钱的行要把价格顶在行上**，
              #    别让 AI 敲了才知道 75g（理由栏里再说一遍坐标/效果）。
              lambda c, t: "买 Joja 可乐 (75g)", "world",
              exec=lambda c, t, run: _exec_chore(c, t, run, "cola", "买可乐"))


# 9) 🌾 铺 干草（`farm ops="hay"` → `feed_hay()`；恒 2026-10-01：「**支持上单子**」）
#    can：**人此刻在动物建筑内**（服务器只在 `FARM_ANIMAL_BUILDINGS` 里推这笔账）+
#         **筒仓有干草** + **喂食台没满**。权重 68：**它不喂也饿不着**（动物在外面吃草）
#         ⇒ 别去抢"每天一次"那几档（蟹笼 72 / 放牧 84 / 收作物 88…）的位。
#    ⚠️ 理由栏**只报数字 + 下一步**：恒刚说过那个槽的机制（「不会补的，那个槽只是方便你取草
#       铺上去」）⇒ **机制一句都不许写**（不写"会/不会自己补"），只说事实与动作。
def _hay_can(ctx, t):
    d = ctx.hay or {}
    if int(d.get("silo") or 0) <= 0:
        return CAN_NO                      # 筒仓空了 ⇒ 按了也是白跑（`feed_hay` 会当场劝退）
    if int(d.get("bench_total") or 0) <= 0:
        return CAN_NO                      # 这间没有喂食台 ⇒ 没得铺
    if int(d.get("bench_used") or 0) >= int(d.get("bench_total") or 0):
        return CAN_NO                      # 槽已经满了
    return CAN_YES


def _hay_show(ctx, t):
    return "铺 干草"


def _hay_reason(ctx, t):
    """只报**数字** + 下一步；**不写机制断言**（"槽会不会自己补"这类一句都不提）。"""
    d = ctx.hay or {}
    silo = int(d.get("silo") or 0)
    used = int(d.get("bench_used") or 0)
    total = int(d.get("bench_total") or 0)
    head = f"筒仓 {silo} 草 · 喂食台 {used}/{total} 格有草"
    if silo <= 0:
        # 警告必须带下一步（单子上一般看不到这一支：silo=0 时 `can()` 就不给行，
        # 这一句是给"点开时刚好用光"和自验用的）
        return (head + "；**先去弄草**：`farm(ops=\"clear\", kw={\"x\":…, \"y\":…})` 割草，"
                       "或去皮埃尔买干草")
    return head + "；铺一次把槽填上"


HAY_V = Verb("hay", "铺 干草", 68, _hay_can, _hay_reason, _hay_show, "world",
             exec=lambda c, t, run: _exec_chore(c, t, run, "hay", "铺干草"))
# ⚠️ 两个 Verb 的 `key` 只是**单子这一层的稳定标识**（`key` 决定排序/去重，不是 op 名）；
#    它们跑起来**打的是同一个 op**：`run("doors", …)`（见 `_doors_exec`）——
#    C# 那边本来就只有 `/toggle_doors` 一个**翻转**端点，**没有**"保证开/保证关"两条路。


VERBS: list = [
    # 📦 容器（箱子/冰箱）：**一行一个箱子**，点开是它的动作面（看/取/存）。
    #    判据在 `_chest_can`（"在 /scan_chests 名单里"），收容判据在文件上方那段反编译说明。
    # ⚠️ 权重**故意低于 `collect`(88)**（2026-09-29 审查）：容器行是**目录行**（点开还有一层），
    #    而单子第一屏的承诺是"**动作面**"（按了就成）。农场/主屋常态有 5+ 个箱子，
    #    权重一高，第一屏就被"点开还有一层"占满，真正能一下做完的（收机器）反被挤到"还有 N 项"。
    # 📦 箱子：**顶层只一行**（`箱子… ← 5 个 · 共 93 件 · 空 87 格`），点开才是**一览**，
    #    再点才是动作面（恒 2026-09-29：「箱子好多哇！…接到 storage 的原有功能去」）。
    Verb("chest", "箱子", 80, _chest_can, _chest_reason, _chest_show, "tile",
         subs=_chest_overview, count=_chest_count, merge=True,
         reason_many=_chest_reason_many, group="设备", weight_fn=_chest_weight),
    # 🧺 **收 已好的机器**（2026-10-01 定形）：不带参数的一行动作（**拟人逐台收**）。
    #    **放料不在单子上**（"哪件进哪类机器"是规划 ⇒ 走原路线 `farm load`）——
    #    三轮改形的全部理由在 `MACHINE_V` 上面那一大段，别再把选项加回来。
    #    ⚠️ 权重**沿用 88**：`_chest_weight`（满包 90 压过它）与各处的排序说明都按这个数写的。
    MACHINE_V,
    # ⚠️ 下面这些**只有渲染没有执行**（`exec=None`）⇒ **不上单子**，只在 `at(x,y)` 里
    #    标「⏳ 还没接执行」——**那行就是缺口探测器**（见 `render_at` 的注释）。
    #    没接的原因**不是懒**，是这两族各有各的形状问题：
    #      · 捡/收作物/锄：**不是逐格动作**。`/harvest` 是**半径批量**（`radius` 默认 15），
    #        农活域本来就在算「哪片可耕/哪块熟了」⇒ 按 166 ⑨ 它们该长成**聚合行**
    #        （`收这块地（12 格成熟）`），不是"对着这一格收"。**塞进逐格动词表就是走错形状**。
    #      · 坐/搬家具/摸动物：要"走过去 + 转向 + 交互"的编排，且**观感要恒验收**
    #        （拟人那条路），不该在没有真机的情况下先接上。
    # 🐾 摸（**情境动词**：问的是"现在这个处境"，不指某一格）——2026-09-29 接线
    #    `wasPetToday` 就在 `/animals` 里 ⇒ "今天摸过没"**问得到**（PENDING 里那句是旧的）。
    Verb("pet",     "摸 还没摸的动物", 84, _pet_can,  _pet_reason,  _pet_show,  "world",
         exec=_exec_pet),
    Verb("pets", "摸 猫狗",      87, _pets_can, _pets_reason, _pets_show, "world",
         exec=_exec_pets),   # 🔀 2026-10-04 改名（旧键 `pet_pets` = 已退役作弊函数的残留名）
    # 🪑 坐 / 🛋 搬家具（逐格）——2026-09-29 接线
    # ⚠️ `batch=False`：`_exec_sit` 只吃 `targets[0]`（人只能坐一张）
    #    ⇒ 街上两张长椅时**不许印 `坐 现代长椅 ×2`**（那是"两张都要坐"）。
    # 🪑 2026-10-02 恒：「**坐可以放在不靠上的位置**」⇒ 权重从 70 压到 26。
    #    理由：坐下是"歇一下/等人/看景"的活，**很少是这一刻的最优解**；而它按格发号，
    #    有椅子就容易挤进第一屏。**没座位就不会出现**（判据是逐格的 `_sit_can`，
    #    夹具/真机里没有 seat 格 → 这行压根不存在），所以压低不会"藏掉该做的事"。
    # 🪑 2026-10-04 恒：「把坐合成一下」⇒ **两种以上座位**才合成顶层这一行目录行（点开才是各把椅子）；
    #    **只有一种**时走 `SIT_ONE_V`（直接给「坐 木椅」，不多点一层）。见上面那段的账。
    SIT_ONE_V,
    SIT_PICK_V,
    # 🐟💄📺 2026-10-04 恒：「鱼缸梳妆柜和电视……只是没接到单子上」⇒ 上单（见上面那段的账）。
    #    ⚠️ 鱼缸那条包办流程（`tank_take`/`tank_add`/`tank_swap`）**不进这张表** ——
    #       它们只活在 `FURN_V` 的**子层**里（同箱子那三个）。
    FURN_V,    # 🪑 起身：**坐着才出现**（见上面 `_stand_can` 那段）——它是「坐」的**出口**，
    #    没有它，单子就把 AI 留在一个自己不给路的状态里。
    # ⚠️ 权重贴着 `sit`(70) 下面一点：同一个"姿势"家族，坐/起 该挨着看。
    #    压不过 收放(88)/箱子(80) 是对的 —— 坐着不影响收放（`_mwork_can` 不看坐姿）。
    Verb("stand",   "起身",   66, _stand_can,   _stand_reason,   _stand_show,   "world",
         exec=_exec_stand),
    # 🛏 床（2026-09-29 恒：「床的重要性比其他家具大得多…当前场景有就该置顶」）。
    #    ⚠️ **两项分开，不是"床…"目录行**：
    #      ① 睡觉是这套系统里**唯一"不可逆 + 要房主配合"**的动作（日结束、存档、ReadyCheckDialog），
    #         它该**直接出现在眼前**，藏一层就是把那个暗示削一半；
    #      ② 第一屏本来就有好几个目录行（箱子…/搬走…/买…/卖…）—— 171 的病正是
    #         "目录行点开还有一层"把真动作挤成"还有 N 项"，再来一个「床…」就是往那个方向走；
    #      ③ 多一次调用的代价**正好发生在最要紧的那一刻**（夜里该睡了），那边省不起。
    #    ⚠️ 差别靠**措辞**写死（理由栏一个"过夜"一个"不过夜"），不靠藏 —— 同 `坐`/`搬走` 并排。
    #    ⚠️ **不进"家具"组**（恒："没有办法放在交互家具的选项里"）⇒ `group=""`，
    #       靠 `_apply_groups` 的"高权重可以越过组"排到最前（夜里 92）。
    Verb("lie",     "躺一下", 68, _bed_can,     _lie_reason,     _lie_show,     "tile",
         exec=_exec_lie, weight_fn=_lie_weight),
    # ⛔ 🛋 2026-10-02 **「搬走」（`pickup_f`）撤出单子**（恒拍板：家居装饰场景专用、优先级极低，
    #    「跟壁纸墙纸一样干脆不做了，保持原样传参式域工具算了」）—— 原来这里是：
    #    `Verb("pickup_f", "搬走", 68, …, subs=_pickup_subs, count=_pickup_count, merge=True,
    #          reason_many=_pickup_reason_many, group="家具")`。
    #    替代路（**现成的域工具**）：`scene(ops="furniture")` 看清单 ·
    #    `scene(ops="pickup", kw={"tile_x":X,"tile_y":Y})` 搬起 · `scene(ops="place", …)` 放下。
    # 🌿 捡 / 🌾 收作物：**聚合行**（一次一片，端点的语义本来就不是逐格）
    Verb("pick",    "捡 地上的东西", 90, _pick_can, _pick_reason, _pick_show, "tile",
         exec=_exec_pick, merge=True, batch=True,             # 帮手一片全捡（真机：×2 捡到 2）
         reason_many=_pick_reason_many),
    Verb("harvest", "收 成熟作物", 88, _harvest_can, _harvest_reason, _harvest_show, "tile",
         exec=_exec_harvest, merge=True, batch=True,          # `harvest_crops` 是半径批量
         reason_many=_harvest_reason_many),
    # 💧 2026-10-04 恒拍板上单：「浇水肯定是浇没湿的有作物格子，不用圈地也可以做，前期经常做」
    #    ⇒ **聚合行**（同一片一次浇完）；判据是瓦片自己的 `watered`；exec 走无参的 `water_crops()`。
    Verb("water", "浇水", 86, _water_can, _water_reason, _water_show, "tile",
         exec=_exec_water, merge=True, batch=True,
         reason_many=_water_reason_many),
    # 🐟 2026-10-04 恒：鱼塘产出上单（`ponds` = 服务器递进来的"哪几座塘有货"）。
    #    **情境行**（`world`）：塘不在 `/surroundings` 的瓦片上（是 Building），逐格目标凑不出来；
    #    执行走 `pond_collect` **逐塘**（每座一个坐标）⇒ `batch=True` 印 `×N` 是真承诺。
    #    权重 82：压在 箱子(80) 之上、摸动物(84)/浇水(86) 之下 —— 它是**真动作**（不是目录行），
    #    而且**只在有货时出现**，不会常驻占位。
    Verb("pond", "收 鱼塘产出", 82, _pond_can, _pond_reason, _pond_show, "world",
         exec=_exec_pond),
    # 🗿 2026-10-04 恒拍板 (b)：摸雕像上单（判据 = 场上有雕像 + **游戏自己的**
    #    `blessedByStatueToday`，见上面 `_statue_can` 那段）。
    #    权重 56：**顺手活那一档的最低**（苔藓 58 之下、吃 50 之上）—— 它一天只有一次、
    #    摸完当天就消失，所以**不该挤掉农活**；但也不埋进"还有 N 项"里（下矿前值得看一眼）。
    Verb("statue", "摸 雕像", 56, _statue_can, _statue_reason, _statue_show, "world",
         exec=_exec_statue),
    # ⛔ **「锄」2026-09-29 摘掉了，别再往上加**（恒拍板，理由比"它没用"重要得多）：
    #    > 「这种需要 AI 参与规划的行为，还是让它自己调我们的原路线吧。不然你给了它锄，
    #    >  它可能反而会觉得：哦，第一眼给我返回了这个。然后锄一大块地，全靠自己走位
    #    >  一格一格弄。**毕竟 LLM 有多条路可以走的时候，就有走偏的可能。**」
    #    ⇒ **菜单是多路口的强暗示**：第一屏返回什么，AI 就倾向照着做。
    #      所以单子上只放「**这条就是最优解**」的动作；"能用但不划算"的一律别放
    #      —— 多一条路 = 多一分走偏。
    #    当时它长这样：`锄地 ← (50,11) · 附近另有 184 格 · 一次做一格`（`farm_till` 单格）。
    #    而真正的锄地路径在**农活域**，都**比它好**：
    #      · 整块地 → `farm till x1 y1 x2 y2 [layout=N]`：裁边、算蓄力站位、**一锄 18 格**、蛇形走位
    #      · 挖斑点 → `scene ops=spot`：扫 `(O)590`/`(O)SeedSpot`/姜点，**批量全挖**
    #    留着它只剩一个后果：拿农场满地裸泥（`Diggable` 是地图属性，整片农田都 true）
    #    当噪音，占掉第一屏一个真动作的位置。
    # 🍽📖 吃 / 看：**接上了**（2026-09-29）。两条都是 `held` 目标、都走"先 select 再动手"，
    #     共用同一个执行器形状（见 `_exec_select_then`）。
    #     ⚠️ 它们能不能出现，取决于 `ctx.held` —— 而 `ctx_from` 原先读 `currentTool`
    #     （书/食物都不是 Tool）⇒ **这两条结构性永不出现**。今晚一并修了（见 `ctx_from`）。
    Verb("eat",     "吃",     50, _eat_can,     _eat_reason,     _eat_show,     "inv",
         exec=_exec_eat, weight_fn=_eat_weight),
    Verb("read",    "看",     40, _read_can,    _read_reason,    _read_show,    "inv",
         exec=_exec_read),
    # 🏪 买 / 卖（**只在商店开着时才有**，见上面那段商店说明）。两条都是**目录行**。
    # ⚠️ 权重压在 `collect`(88)/`chest`(80) 之下、`eat`(50) 之上：站在柜台前，买卖是正事；
    #    但商店**开着**的时候才会出现，所以它不会跟农场那批抢第一屏。
    SELL_V, BUY_V,
    # 🚪 界面出口（**菜单态专属**，见上面 `CLOSE_V` 那段）。放最后只为读着顺——
    #    排序走权重（30），跟它在列表里的位置无关。
    CLOSE_V,
    # ⛔ 👕 2026-10-02 **「穿戴」撤出单子**（恒拍板）—— 原来这里是一行 `Verb("wear", "穿戴", 38, …)`，
    #    权重 38 = 单子最低 ⇒ 在"没别的事可做"的场景里（棚里只剩「捡」+「穿戴…」）会**常驻**。
    #    **功能没少**：`daily(ops="wear", kw={"name": 内部名})` / `kw={"slot": "hat"}`（跟 sleep 同家）。
    #    撤法照 190 撤「放料」：**整套删干净**（行 + 子动词 + 那三个 helper 全删，不留半截）。
    # 🎬 推进对话（见上面那一段）。权重 76：**剧情在播时它就是正事**
    #    （压过 收机器88？不 —— 收机器那行在菜单态根本不会出现，两者不会同屏争位；
    #     76 只在"事件在播、单子照常全量"那种处境里起作用，那时它就该靠前）。
    Verb("advance", "推进对话", 76, _advance_can, _advance_reason, _advance_show, "world",
         exec=_exec_advance, menu_ok=True),
    # ⏭ 跳过整段（恒：「1 接 advance、2 跳过」）——**排在 advance 后面**（74 < 76），
    #    那一刻的正事是接着看，跳过是退路（见上面那一段）。
    SKIP_V,
    # 🗳 对话选项（真机罗宾那句"美学 vs 浪费"）——权重**跟 advance 同档 76**：两者
    #    **互斥**（有选项时 `_advance_can` 故意不给），所以同权重不会打架。
    OPTION_V,
    # 🧾 确认结算（**只长在 ShippingMenu 上**，见上面那一段）。权重 78：
    #    结算屏那一刻它是**唯一**该按的（那一屏别的行全被菜单态过滤掉了）。
    Verb("settle", "确认结算", 78, _settle_can, _settle_reason, _settle_show, "world",
         exec=_exec_settle, menu_ok=True),
    # 🗑 投出货箱（目录行，见上面那一段）。权重 36：**站在农场 + 背包有能卖的**才出现，
    #    而且"投哪件"是个取舍 ⇒ 压在 穿戴(38) 之下，别抢第一屏。
    Verb("bin", "投出货箱", 36, _bin_can, _bin_reason, _bin_show, "world",
         subs=_bin_subs, count=_bin_count),
    # 📋 菜单摊开（2026-10-01 · P-menus）：**开着的容器菜单**里的东西。
    #    权重 58：菜单开着时 `_candidates` 只剩 menu_ok 的行，这一刻它就是正事
    #    （压在 关掉界面(30) 之上 —— "先看看箱里有什么"比"马上关掉"更常是下一步）。
    #    57 = 「存…」：挨着「箱子里…」排在它后面（取在前、存在后），**都在 关掉界面 之上**。
    MENU_BOX_V, MENU_TAKE_V, MENU_STORE_V, MENU_STORE_ROW_V,
    # 🪨🔨 2026-10-01（恒「锻造晶球先吧」）：**砸晶球**（铁匠铺柜台前）与**重铸饰品**（铁砧前）。
    #    两条的判据都**问游戏**（`is_geode` / `/machine_reqs` 的只问不做探针）——
    #    老 DLL 没这些位 ⇒ **两行都不出现**（宁可不给，也不给一行按了不成的）。
    GEODE_V, REFORGE_V,
    # 🚪🐄 2026-10-01（恒：「放牧（开关畜棚鸡舍门）做进选项了吗？」）：**放牧（开棚门）**与**关棚门**。
    #    两条互斥（钟点分，见上面那段的账）：早上 06:00–15:00 给开、≥17:00/<06:00 给关，
    #    16:00 那一小时两行都不给。判据全在 `Ctx.doors`（服务器递进来），这一层不打 HTTP。
    OPEN_DOORS_V, CLOSE_DOORS_V,
    # 🌿 2026-10-01 恒「接吧」：P1 那批**空参行**（早就有的 6 个 op，一直没上单子）。
    #    判据全在 `Ctx.chores`（服务器算好的账）；执行只调现成 op —— 见上面那一段的账。
    BERRY_V, SPOT_V, CRAB_V, PAN_V, MILK_V,
    # 🎓 2026-10-02 恒「补一下缺门」：菜单里的一次性正事（精通碑领取）也要有行。
    _CLAIM_V,
    # 📜 2026-10-04 恒「先做领奖」：**任务日志里的一件领取**（已完成+有钱的任务，一次全领、
    #    回执逐条报 名字·详细页描述·金额）。判据 = `Ctx.quests`（服务器只在日志开着时读 `/menu`）。
    QUEST_CLAIM_V,
    # 🗑️ 2026-10-02 恒「捡垃圾可以上」：**翻垃圾桶**（账 = `Ctx.chores["garbage"]`，
    #    判据是服务器那侧的 `_trash_cans_here()`，跟状态条那条提示共用一份）。
    TRASH_V,
    # 🥤 2026-10-02 恒「**上单吧**」：买 Joja 可乐（账 = `Ctx.chores["cola"]`，判据在服务器
    #    那侧的 `_cola_machine_here()`；坐标＋价格全在理由栏里）。
    COLA_V,
    # 🌾 2026-10-01 恒「支持上单子」：**铺 干草**（只在动物建筑内、筒仓有草、槽没满时出现）。
    HAY_V,
    # 🏛️ 2026-10-04 恒「动A」：**献祭板**（一件件捧上槽位；只在板子开着 + 这版 DLL 报得出
    #    "能捧上什么" 时出现 —— 判据见 `_cc_can` 那段）。
    CC_V,
    # 🎁 2026-10-05 恒真机「然后有奖励可以领」：**领本间已完成的收集包奖励**
    #    （列表页那个礼物按钮；判据 = 服务器从 `/menu.buttons` 认出了 `presentButton`）。
    CC_GIFT_V,
    # 🏛️ 2026-10-05 恒三条件（地点/背包/板子在）定的**世界侧入口**：
    #    menu_ok=False（**故意**）—— 它只在没开菜单时才该出现，不然会劝 AI 开着菜单去走位。
    CC_GO_V,
    # 🏛️ 2026-10-05（补24c）世界侧「去博物馆捐赠（包里 N 件可捐）」（恒：「背包有可捐能跟献祭一样打标吗？」）
    MUSEUM_GO_V,
]


# ═══════════════════════════════════════════════════════════════════════
# ③ 还没接线的动词（**不露给 AI**，只给我们自己看缺口）
# ═══════════════════════════════════════════════════════════════════════
# 这张表是**动词表的缺口探测器**：AI 老指着某类东西而我们没给选项，
# 就是这表缺了一行。它是需求来源，比我们拍脑袋列准得多。
#
# 想让某条上桌 → 把它从这儿挪进 VERBS，并写死它的 can()。
PENDING: list = [
    # (key, 中文, 缺什么)
    # ⚠️⚠️ 2026-10-02 恒**当场拍板的三条**（记在这儿，免得又出"设计稿说上、代码里没有"的漂）：
    #    · **`emote`（表情）不上** —— 原话：「常驻的而且**对玩家的**、不常用的，要么被挤到底下看不到
    #      要么占一个单子位置，**不推荐**」（同族：`send` 发消息）
    #    · **NPC `chat`（搭话）/ `gift`（送礼）「可上可不上」** —— 先不占位（`gift` 那条见下，仍卡在 P2）
    #    · **游戏机（街机 `Arcade_Prairie`/`Arcade_Minecart`）不做** —— 「太难了不做」
    #    📌 通式：**对玩家的、且天天在的**动词，价值不在"能不能做"，在"占了位会不会把正事挤下去"。
    ("special_drop", "交 特别任务物品", "⭐ 特别订单的**交付点**（如酒馆冰箱 `DropBox GusFridge` (18,16) 收 24 个蛋）："
                                    "判据要「这单接了没 + 背包够不够数」，两者都得问游戏（订单状态有端点、"
                                    "物品数走 `has_item`）⇒ 形状是**两级**（选单 → 选物品）⇒ 跟 `gift` 一起等 `next_of`"),
    ("island_walnut_bush", "姜岛摇核桃丛", "🌰 姜岛的「核桃丛」**也是 `Bush`**（`size==4`），`tileSheetOffset==1` = 还挂着核桃"
                                        "⇒ 跟浆果丛**同一个字段**（`bushBloom`）。现在靠「**非浆果季就不摇**」挡着，"
                                        "可**浆果季**在姜岛跑 `scene ops=berry` 会去摇核桃丛（白摇）。"
                                        "根治要 C# 多报 `Bush.size`（进批）；恒 2026-10-02：「姜岛我还没想好怎么办」"),
    # ⚠️ 2026-10-01 对账（恒：「好，划掉吧」）—— **`sit`(坐) / `pickup_f`(搬走家具) / `pet`(摸动物)
    #    三条早就上线了**，却一直挂在这张表里：真机单子上就印着「坐 胡桃木椅子」「搬走…」
    #    「摸 还没摸的动物」。这张表自称是**缺口探测器**，结果**它自己在骗人**。
    #    📌 通式：**加动词那一刻才改这张表、之后没人回头对账** ⇒ 它一漂，缺口清单就假了。
    #    （同族证据：`_pet_can` 上面那行注释 2026-09-29 就写过「PENDING 里那句是旧的」——
    #      当时只改了注释、没划表 ⇒ **知道旧了还不改 = 照样骗下一个人**。）
    ("gift",     "送礼",    "要面前是 NPC + `tryToReceiveActiveObject`——问游戏有，但**是动作级的**（试了才知道收不收）"
                            "；形状是**两级**（选人 → 选物）⇒ 跟锻造台一起等 `next_of` 钩子（设计稿 P2）"),
    # 下面两条**新缺口**是 2026-09-29 反编译核容器时才看清的（都在 C# 那边，不在这一层）：
    ("tank",     "鱼缸/梳妆柜", "**是 Furniture 不是 Chest** ⇒ `/store` 够不着（`HandleStore` 硬判 `is Chest`）。"
                                "鱼缸判据 `FishTankFurniture.HasRoomForThisItem()`（`Data/AquariumFish` 分类+容量）、"
                                "梳妆柜 `categoriesToSellHere{帽-95,衣-100,靴-97,戒-96}`"),
    ("enricher", "施肥器",  "⚠️ `IsStorageChest` **没排 Enricher**（只收肥料、容量 1）⇒ 它会被当成普通仓库列出来。"
                            "判据在游戏 `SpecialChestTypes` 里，我们看不见 ⇒ 补名单，**别编表**"),
    # ⚠️ 墙纸/地板：恒 2026-09-27 拍板「趣味功能，放后面」。
    #    而且它是**全场最难的一条**：`Furniture.isPlaceable()` **恒 true**（等于没有判据），
    #    墙纸连"这卷铺哪面墙、归哪个房间"都没有端点（`GetFloorID` 要房间号，要不到静默 false）。
    #    ⇒ 单独立项，别让它拖住第一版。
    ("decor",    "铺墙纸/地板", "⚠️ 已知最难：`isPlaceable()` 恒 true、房号算不出。恒拍板后置"),
    # ───────────────────────────────────────────────────────────────────
    # 📋 **这张表不装"待上的动词"**（那两档另有账，别在这儿重开一份会漂）：
    #    · **P1 目录行**（现成 `subs`，不用新机制）：`cook` 做饭 · `craft` 合成 · `donate` 捐赠 ·
    #      `milk` 挤奶 · `berry`/`spot`/`moss` 摇浆果/斑点/苔藓 · `crab_collect` 收蟹笼 · `pan` 淘金
    #      （`cook`/`donate` 的判据要**一小批 C# caps 位**：厨房/展板在不在面前）。
    #    · **P2 两级参数**（要 `next_of` 钩子）：锻造台（选件→选料）· 送礼（选人→选物）· `give`/`hand`。
    #    账在记忆库设计稿 `intent-expansion-design-2026-10-01.md`（P0/P1/P2 + 域收敛）。
    # ⚠️ **只放"缺判据/缺机制"的**：能上而没上的，是排期问题，不是这张表的事。
]


# ═══════════════════════════════════════════════════════════════════════
# ④ 渲染
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Row:
    """单子上的一行 = 一个动词 × 一批同类目标（恒要的「钻石×1，翡翠×n」）。"""
    verb: "Verb"
    targets: list
    label: str          # 正文，如「收 钻石」
    reason: str         # 理由（审计面）
    dist: int           # 最近那个目标走几步（曼哈顿）
    # 🗂 非 None ⇒ **目录行**（这行还要选，句尾印 `…`）；None ⇒ **动作行**（按了就成）。
    #    判据不是「有选择就嵌套」，是「**这行有没有把自己说完**」。
    level: "Level" = None
    # 它在这一层被印出来的号（渲染时填）。qty 层**沿用**上一层发的号，不重排位置。
    no: int = 0
    # 印在正文前的那截定位。`None` = 用 `_where(目标)`；`""` = **不印**
    # （货架上的商品/菜单里的项**不在世界里**，印"手持"就是撒谎）。
    where: str = None
    # 🗂 目录行那截「（N 件）」的**替身**。默认数下一层有几行；容器行要报的是
    # **箱里有多少件**，不是"点开有几个动作"——两个数并排就是"拿错尺子"的温床
    # （2026-09-27 真机照出来过：同屏两个"格"两个意思）。
    count_text: str = None
    # 🗂 这行归哪一组（从 `verb.group` 抄来）——渲染时决定要不要印组头。
    group: str = ""


def _dist(ctx: Ctx, t) -> int:
    """走几步——**曼哈顿**。4 向移动下这才是"几步路"，直线距离会骗人。"""
    return abs((t.get("x") or 0) - ctx.px) + abs((t.get("y") or 0) - ctx.py)


def _row_for(ctx: Ctx, v, targets: list, label=None) -> "Row":
    """把「一个动词 × 一批目标」变成一行 —— **单子和逃生口共用这一条路**。

    ⚠️ 共用的理由不是省几行，是**别让两张屏的号漂开**：`at x,y` 那屏也是"敲编号"，
       它发出来的号必须和单子的号是同一种东西（2026-09-29 真机照出来的洞：
       逃生口那屏压根没进栈 ⇒ 敲它的号打的是**上一屏**）。
    """
    # 🌍 情境动词的 targets 是 `[None]`（没有格）⇒ 距离 0、定位留空，理由由动词自己看 ctx 说。
    world = bool(targets) and targets[0] is None
    near = None if world else min(targets, key=lambda t: _dist(ctx, t))
    dist = 0 if world else _dist(ctx, near)
    if v.merge and not world:
        reason = v.reason_many(ctx, targets) if v.reason_many else v.reason(ctx, near)
    else:
        reason = v.reason(ctx, near)
    # 🗂 目录行的下一层**在这就算出来**（顶层要拿它报 `（N 件）`，也得知道它长不长）。
    lv = v.subs(ctx, targets) if v.subs else None
    # 标签照抄原来的三档：情境动词拿 `show(ctx, None)`（它不指某一格），
    # 合一的（merge）用动词自己的名字当正文，其余用**最近那个**目标的正文
    # ——同桶里所有目标的正文本来就一样（那是分桶的键）。
    if label is None:
        label = v.label if v.merge else v.show(ctx, None if world else near)
    return Row(verb=v, targets=targets,
               label=label, reason=reason, dist=dist, level=lv, group=v.group,
               # 📋 菜单里的项 / 对话选项**都不在世界里**：定位列留空（同货架上的商品）。
               #    ⚠️ 不能留给 `_where` 兜底 —— 它认不出这两种目标，会印成"手持"（撒谎）。
               where=("" if v.target in ("menu", "option") else None),
               count_text=v.count(ctx, targets) if (v.count and lv) else None)


def _candidates(ctx: Ctx) -> list:
    """跑一遍动词表 × **整张图**的目标，按「动词 + 对象名」聚合成行。

    ⚠️ 候选是**全屋/全场景**（见 `Ctx` 里那段删除说明）——别再加范围限制。
    聚合的键 = **对象名**（`show()` 的输出，本身不含坐标）：
    三台水冷塔都出钻石 ⇒ 一行「收 钻石 ×3」，敲一下 = 走过去全收。

    返回**已排序但未截断**——截断是 render 的事，而且它必须把砍掉的数量报出来。
    """
    buckets = {}
    # 🚧 菜单开着时，**只留"本来就是通过菜单干活"的动词**（`menu_ok`，目前是 买/卖）。
    #    ⚠️ 2026-09-30 真机抓到的**假门**：站在皮埃尔柜台前（ShopMenu 开着），
    #       单子照常列出 `1 卖… / 2 买…`，可 **`intent do` 被 MCP 的菜单闸门整个挡掉**
    #       ⇒ 看得见、按不动 —— 正好踩在恒那条「单子上出现的那条，**按了就成**」上。
    #    根因不是闸门太严，是**这层把不该出现的行也列出来了**（同一屏还列着 `坐 stool/couch/chair`
    #    三条商店家具 —— 菜单态下那些根本按不了）。⇒ 判据收到这里：**菜单态就只列菜单态能做的**。
    menu_open = bool(getattr(ctx, "menu", None))
    for v in VERBS:
        # 「接了」= 有 exec（动作行）**或** 有 subs（目录行）。两个都没有 = 看得见按不动，不上单子。
        if v.exec is None and v.subs is None:
            continue
        if menu_open and not getattr(v, "menu_ok", False):
            continue
        if v.target == "world":
            # 🌍 **情境动词**：不属于某一格，也不属于手持那件——它问的是"现在这个处境"。
            #    （「摸 还没摸的（3 只）」「下到下一层」这类。）
            #    目标传 `None`：动词自己看 ctx（它要什么自己拿），**不给它编一个假格子**。
            if v.can(ctx, None) is True:
                buckets.setdefault((v.key, None), []).append(None)
        elif v.target == "held":
            t = ctx.held
            if t and v.can(ctx, t) is True:
                buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)
        elif v.target == "inv":
            # 🎒 **背包格**目标：一行一件。`吃` 在资源见底时列**全部能吃的**（判据在 `_eat_can`），
            #    平时它只放行"手上那件" ⇒ 这里通常还是只有一行。
            #    ⚠️ 只吃 `can(...) is True`：`None`（算不出）**不上单子**，跟别的目标同一套三档。
            for t in ctx.inv:
                if v.can(ctx, t) is True:
                    buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)
        elif v.target == "menu":
            # 📋 **开着的菜单里摊出来的东西**（2026-10-01 P-menus）：不在世界里、也不在背包里，
            #    所以它既不能走 tile 那支（`_where` 会印成"手持"）、也不能走 inv 那支。
            #    ⚠️ 目标清单取自服务器递进来的 `menu_data`（`_menu_box_items` 只管"有没有格号"）。
            for t in _menu_box_items(ctx):
                if v.can(ctx, t) is True:
                    buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)
        elif v.target == "option":
            # 🗳 **对话选项**（同一份 `menu_data`，另一档）：一条答案一行。
            for t in _menu_options(ctx):
                if v.can(ctx, t) is True:
                    buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)
        else:
            for t in ctx.tiles.values():
                if v.can(ctx, t) is True:
                    buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)

    rows = [_row_for(ctx, _VERB_BY_KEY[vkey], targets, label)
            for (vkey, label), targets in buckets.items()]
    # 排序：先按动词权重，再按**距离**。
    # ⚠️ 距离是个**合法且可审计**的排序理由（"近的先做"）——比一个黑盒启发式诚实得多。
    #    而且同权重的一批（20 台机器）全靠它拉开，否则前 N 条就是**随便挑的**。
    rows.sort(key=lambda r: (-_weight_of(r.verb, ctx), r.dist))
    return rows


def _weight_of(v, ctx) -> int:
    """动词的**排序权重**（动态的走 `Verb.weight_fn`，否则用静态 `weight`）。

    ⚠️ **全项目只此一条路** —— 别的地方再直接读 `.weight`，动态权重就会被静默忽略
    （那种 bug 的样子是"改了没用"，最难查的一类）。
    """
    fn = getattr(v, "weight_fn", None)
    if fn is None:
        return int(v.weight)
    try:
        return int(fn(ctx))
    except Exception:
        return int(v.weight)          # 算不出来就退回静态值，**绝不让排序崩**


_VERB_BY_KEY = {v.key: v for v in VERBS}


def _addressable_fact(ctx: Ctx) -> str:
    """身边没有可做时给的那句**事实**（不是推荐）——恒那个"不知道旁边有箱子"的坑。"""
    ready = sum(1 for t in ctx.tiles.values()
                if (t.get("machine") or {}).get("status") == "ready")
    boxes = sum(1 for t in ctx.tiles.values() if t.get("is_chest"))
    bits = []
    if ready:
        bits.append(f"机器已好 {ready} 台")
    if boxes:
        bits.append(f"箱子 {boxes} 个")
    return "、".join(bits)


def _where(t, ctx=None) -> str:
    """目标**在哪**——**不带方位词猜**（"面前"要朝向数据，宁可写坐标）。

    ⚠️ 2026-09-30：加了 `inv`（背包格）目标之后，**"不是坐标"不再等于"手持"**了 ——
       背包里**没拿在手上**的那件也会走到这儿（`吃` 饿极了时列全部能吃的）。
       判据 = `t is ctx.held`：`ctx_from` 里 `held` 就是 `inv` 里的**同一个对象**、不是副本。
       ⚠️ **拿不到 ctx 就退回"手持"**（老行为）——宁可退回，也别印成空串：
          空串比错更难查（肉眼看不见它少了一截）。
    ⚠️ 2026-10-01：`menu`（菜单里的项）目标**不走这里** —— `_row_for` 给它们把 `where`
       直接定成 `""`（既不在世界里也不在背包里，印"手持"就是撒谎）。
       这里认不出那种目标，别指望这条兜底能救。
    """
    if isinstance(t, dict) and isinstance(t.get("x"), int):
        return f"({t['x']},{t['y']})"
    if ctx is not None and t is not None and t is not getattr(ctx, "held", None):
        return "背包"
    return "手持"


# 🧠 最近一次**显示给 AI**的那几条。`do_row` 打的是它，不是重算的第 n 条（见 `do_row`）。
_LAST_ROWS: list = []

# ═══════════════════════════════════════════════════════════════════════
# ⑥ 单子是一叠，不是一张（2026-09-28 定稿）
# ═══════════════════════════════════════════════════════════════════════
# 判据只有一条：**这行有没有把自己说完**。
#   说完了（「收 3 台桶 → 上古水果酒 ×3」）⇒ **动作行**，按了就成；
#   没说（「取出」——取哪条？）⇒ **目录行**（句尾印 `…`），点开才发号。
# ⚠️ **行没说完的，不许拿默认去补**——那正是恒那条「宁报错别兜底」。
#    让它变成目录行，比给它编一个"最常见"的默认值安全得多。

_STACK: list = []       # [顶层, 子层, 孙层…]；空 = 还没看过单子


@dataclass
class Level:
    """一屏单子。

    `mode` = 这一层**接受什么形态的敲法**。它是「静默陷阱」的解法：
    **形态不对就报错，绝不猜。**
        act  → `do(n)`              敲了就做
        pick → `do(n)` / `do(1,4)`  选一个或多个 → 进下一层
        qty  → `do(1=1,4=4)`        **必须**写「号=数量」；只写号 = 报错

    ⚠️ 为什么 qty 层**必须**拒绝「只写号」：那是恒最初提的形状（两屏都 `do(1,4)`、
    靠位置对齐）。**上一屏序无关、下一屏序有关的同一个写法** ⇒ 写反（`do(4,1)`）
    **不报错**，只是买对东西、买错数量，而 AI「会照做、不怀疑」。
    ⇒ 改成 **`号=数量` 配对**（自包含 ⇒ 序消失），并在这儿把旧形态**明确拒掉**。
    """
    rows: list
    title: str = ""
    mode: str = "act"
    verb: "Verb" = None          # pick/qty 层的执行者
    keep_no: bool = False        # True = 沿用上层发的号（qty 层），不重排位置
    fp: tuple = ()               # 世界指纹（见 `_fingerprint`）
    # 💰 pick 层**选完直接做，不进「各多少」那一层**。
    #    ⚠️ 只在**游戏自己决定量**的动词上开（现在只有「卖」：单击卖整个堆叠）。
    #    开着的理由：长一屏"能填数量"的假界面 = 填了不生效、还不报错——
    #    正是 166 ③ 花大力气删掉的那类静默陷阱。层数由**游戏**决定，不是我们偷懒。
    exec_on_pick: bool = False
    # 这一层的提示行（不写 = 按 `mode` 取默认）。只在 `exec_on_pick` 那类
    # "形态跟默认不一样"的屏上用 —— 提示得说清**这一屏能怎么敲**。
    hint: str = ""


def _fingerprint(ctx: Ctx) -> tuple:
    """世界指纹——**只取会让子层单子作废的那两样**：换图 / 开了菜单。

    ⚠️ 刻意**不含坐标**：人走两步不该把手里那屏单子清掉。
    """
    return (ctx.loc, (ctx.menu or {}).get("type"))


def reset_menu():
    """把单子收回顶层（换场 / 断线重连时调）。"""
    _STACK[:] = []
    _LAST_ROWS.clear()


# 🗂 子层一次给几条。**顶层是"决策屏"（越短越好），子层是"你刚点开的那一摞"（要看完）**。
#
# ⚠️ 2026-09-29 真机照出来的洞：子层由 `do_row` 写死 `n=5` 渲染，而**没有任何法子翻页**
#    ⇒ 尾巴那句「—— 还有 36 项（more）」是个**空承诺**（`more` 压根不存在）。
#    单独看只是难看；**跟"搬走收成一行"撞在一起就成了骗人**：
#    把 41 件家具收进目录行、点开却只给 5 件 ⇒ AI 想搬第 6 件时**无路可走**
#    （`at x,y` 也得先知道坐标）。⇒ 子层一次给够。
#    顶层仍按调用方给的 `n`（默认 5）——那才是"一屏之内做决定"的地方。
_SUB_N = 60


_LEVEL_HINT = {
    "act": "> 敲编号，或 at x,y",
    "pick": "> 敲编号，可以多选（`1,4`）",
    "qty": "> 写「号=数量」（`1=1,4=4`）—— **只写号不认**",
}


# 🗂 组名（恒 2026-09-29：「移动家具做单行。甚至嵌套还要分一下设备和家具。」）
_GROUPS = ("设备", "家具")


def _apply_groups(ctx: Ctx, lv: Level) -> bool:
    """把一屏里的行按 `设备` / `家具` 拢成**块**。**返回这一屏到底分没分组。**

    ⚠️ 只在**两块都非空**时才动 —— 农场那种清一色的图（只有农活、或只有家具）
       原样不动：排序是照权重精心排的，没必要的分组只会把"最近的要先做"这条理由搅浑。
    ⚠️ 只在 `act` 层分：选/填那一层是**刚点开的一小撮**，别在里面再分家。

    ## 口径 = **B：组＝块，块按「组内最高权重」参与全局排序**（恒 2026-10-02 拍板改的口径）
    旧口径（171 那版）是 `[比组最高还重的未分组行] + [组整块…] + [其余未分组]` ——
    那等于**让组无条件插到"比它轻的所有未分组行"前面**：真机照出 `坐`(26) 印在
    `摸 猫狗`(87) / `收 成熟作物`(88) **上面**（恒当场看出来"不是全局按权重排"）。
    ⇒ 现在：**块拿"组内最高权重"当自己的权重，跟未分组行一起全局排序**。
      · **为什么还要分块**（恒 2026-09-29：「移动家具做单行。甚至嵌套还要分一下设备和家具。」）：
        目的是让同类行**挨着**（AI 一眼看清"这坨是设备、那坨是家具"），**不是**让它们插队；
      · **块内**：照旧按权重排（`lv.rows` 进来时就是权重序，这里只搬块，不再打散）；
      · `设备`（最高 88）⇒ 落在 `摸猫狗 87` 之前；`家具`（最高 26，只有「坐」）⇒ 落到
        `投出货箱 36` / `看 古书 40` **之后** —— 恒 2026-10-02「**坐可以放在不靠上的位置**」
        要的正是这个效果（顶层 `n=5` 时它看不见也没关系）。
    ⚠️ **"比组最高还重的未分组行可以越过去"这条规则保留**（2026-09-29 加「睡觉」时补的）：
       它现在**不是特例**，而是"按权重比较"的自然结果 —— `睡觉 92 > 设备块 88` ⇒ 排在块前面。
    ⚠️ **权重相等时块在前**（跟旧口径一致：旧代码里 `== cut` 的未分组行是垫在组后面的）。
    ⚠️ **组头只在"相邻同组"时印**（`_render_level` 那句 `r.group != group_now`）——
       块是连续的 ⇒ 组头一块只印一次，且它后面那段**确实都是这一组的行**。
       （这也是**不选"全局权重序 + 组头"那个口径**的唯一理由：那种排法会把组打散，
         组头后面跟着别的组/未分组的行 ⇒ 组头本身变成**新的假门**。）
    """
    if lv.mode != "act":
        return False
    parts = {g: [r for r in lv.rows if r.group == g] for g in _GROUPS}
    if not all(parts.values()):
        return False
    rest = [r for r in lv.rows if r.group not in _GROUPS]
    # （权重, 是不是块, 行们）—— 块用"组内最高权重"去比；同权重时块在前（见上面那条）
    items = [(max(_weight_of(r.verb, ctx) for r in rows), True, rows)
             for rows in (parts[g] for g in _GROUPS)]
    items += [(_weight_of(r.verb, ctx), False, [r]) for r in rest]
    items.sort(key=lambda it: (-it[0], not it[1]))
    lv.rows = [r for _w, _blk, rows in items for r in rows]
    return True


def _render_level(ctx: Ctx, lv: Level, n: int = 5) -> str:
    global _LAST_ROWS
    lines = []
    if lv.title:
        lines.append(lv.title)

    grouped = _apply_groups(ctx, lv)
    shown = lv.rows[:n]
    group_now = None
    for i, r in enumerate(shown, 1):
        # ⚠️ 行号是**动作编号**，不是背包位次——两个数字混用就是"拿错尺子"。
        r.no = r.no if lv.keep_no else i
        # 🗂 组头**不占号**（号只发给我们真能敲的行）
        if grouped and r.group and r.group != group_now:
            lines.append(f"  ── {r.group} ──")
            group_now = r.group
        if r.level is not None:
            # 🗂 目录行：句尾 `…` = 这行还要选。**只报数量，不发号**——号点开才印在眼前
            #    ⇒（a）AI 永远不用数数；（b）号不跨屏，"短命句柄"从风险变成设计。
            # ⚠️ `count_text` 为 `""` = **这行的计数说不出来**（不是"没有计数"）⇒ 一个字都不印，
            #    否则会掉回 `len(rows)`，把"点开有几条"冒充成"箱里几件"（两把尺子）。
            base = r.count_text if r.count_text is not None else f"{len(r.level.rows)} 件"
            tail = " · ".join(x for x in (base, r.reason) if x)
            lines.append(f" {r.no}  {r.label}…   ← {tail}")
            continue

        # ⚠️⚠️ **`×N` = "这一按会把 N 个都做了"** ⇒ 只有执行器**真会全做**（`batch`）才许印。
        #    2026-09-29 真机：`锄地 ×185` 按下去只锄了 **1 格**（世界实查 `(50,11)`
        #    `Grass`→`HoeDirt`、邻居没动、回执 `1/1 锄出`）—— 印 `×185` 就是假承诺。
        #    只吃 `targets[0]` 的动词（锄/坐）改印「最近那一格 + 附近另有 N-1 格 + 一次做一格」。
        n_t = len(r.targets)
        many = n_t > 1
        disp = r.label + (f" ×{n_t}" if (many and r.verb.batch) else "")
        if many and not r.verb.batch:
            loc = r.where if r.where is not None else _where(r.targets[0], ctx)
            # ⚠️ 「附近另有 N 格」只对**世界里的格子**成立。`read` 2026-10-01 扩到背包
            #    （`target="inv"`）之后，同一支会把背包里的两件印成"附近另有 1 格"
            #    —— 那件东西压根不在世界里。措辞**跟目标档走**，别一句话管两种目标。
            #    📋 `menu` 是 2026-10-01 P-menus 加的第三种：菜单里两摞并成一行时，
            #       执行器**会把两摞都取走** ⇒ 说"这一按取 2 摞"，而**没有**"一次做一格"那回事。
            if r.verb.target == "menu":
                _more, _once = f"这一按取 {n_t} 摞", ""
            elif r.verb.target == "option":
                # 🗳 一字不差的选项撞在一起（`_option_show` 会给它们补位次 ⇒ 正常撞不上；
                #    真撞上时执行器**只发一个答案**）⇒ 说清，别写成"附近另有 N 格"。
                _more, _once = f"（同一句有 {n_t} 条，只发一条）", ""
            else:
                _more = (f"背包另有 {n_t - 1} 件" if r.verb.target == "inv"
                         else f"附近另有 {n_t - 1} 格")
                _once = "一次只做其中一个"
            tail = " · ".join(x for x in (loc, _more, r.reason, _once) if x)
            lines.append(f" {r.no}  {disp}   ← {tail}")
            continue
        if n_t == 1:
            # 单目标 → 印坐标（AI 可能想用别的工具精确指它）
            # ⚠️ **情境动词（`target="world"`）没有坐标**（2026-09-29 真机照出来的）：
            #    它的目标**就是 `None`**（"现在这个处境"，不属于任何一格），走 `_where(None)`
            #    会印成「**手持 20 只**（Rabbit×2、Duck×3…）」—— 牛成了"拿在手里"的。
            #    "手持"只对 `held` 动词成立：那一层 `None` **就是**手持那件。
            loc = (r.where if r.where is not None
                   else ("" if r.verb.target == "world" else _where(r.targets[0], ctx)))
            tail = " ".join(x for x in (loc, r.reason) if x)
        else:
            # 多目标 → 坐标**省掉**（省了才有你说的那个效果），只给"几处 + 最近几格"。
            # ⚠️ 但**不能不给定位信息**——否则 AI 不知道这一敲要跑多远。
            # ⚠️ 距离说「**步**」，不说「格」——「格」在这条线上另有含义（箱子 22 格）。
            #    同屏两个"格"是两个意思，就是"拿错尺子"的温床（2026-09-27 真机照出来的）。
            # ⚠️ 用 `join`，**不用 `.rstrip()`**：`rstrip` 只吃空白，`reason` 为空时会留下
            #    一个**光秃秃的 `·`**（真机 2026-09-29：「2 坐 现代长椅 ×2 ← 2 处 · 最近 22 步 ·」）。
            tail = " · ".join(x for x in (f"{len(r.targets)} 处", f"最近 {r.dist} 步",
                                          r.reason) if x)
        lines.append(f" {r.no}  {disp}   ← {tail}")
    _LAST_ROWS = list(shown)

    if not shown:
        # ⚠️ 这里只报**事实计数**，**不做推荐排序**——排序必须带理由（`Verb.reason` 那段
        #    的审计面）。宁可先给计数 + 出口，也不给一个编出来的"建议"。
        # 🚪⚠️ **菜单态是个例外，而且必须例外**：这一刻 `at x,y` 指出来的东西
        #    **一条都按不动**（`do_row` 的菜单态守卫全挡）⇒ 原来那句
        #    「—— at x,y 指过去」是**假门**（2026-10-01 真机三步走完的结论）。
        #    没有出口行（捏人页/鱼机/对话框）时，就照抄服务器 `_close_hint` 的原话 ——
        #    那句话本来就是"这个菜单该怎么处理"的唯一出处。
        if ctx.menu:
            _mt = (ctx.menu or {}).get("type") or "?"
            lines.append(f"  （菜单开着「{_mt}」· 这一刻没有能敲的动作）")
            if ctx.menu_hint:
                lines.append(f"  📄 {ctx.menu_hint}")
        else:
            fact = _addressable_fact(ctx)
            if fact:
                lines.append(f"  （这一刻没有可做的 · 本图另有 {fact} —— at x,y 指过去）")
            else:
                lines.append("  （这一刻没有可做的动作——试试 at(x,y) 指一样东西）")

    hidden = len(lv.rows) - len(shown)
    if hidden > 0:
        # ⚠️ 这里**不能写「（more）」**：压根没有 `more` 这个 op，敲了回「❌ 看不懂」
        #    ⇒ 那是一扇**假门**（跟 166 删掉的"点了没反应"同类）。铁律 2 要的是**如实报数**。
        # ⚠️ 但**光报数也不够**——得同时说清**下一步敲什么**才看得见（恒：「只报'缺失N格'
        #    害 AI 干瞪眼」）。出口**是有的**：`show` 带 `n`（`kw={"n": 20}`）。
        #    2026-09-29 真机：农场顶层 7 条只印 5 条 ⇒ 后 2 条 AI 压根看不见。
        lines.append(f'—— 还有 {hidden} 项没印出来（看全：show 时带 kw={{"n": 20}}）')

    if len(_STACK) > 1:
        lines.append(" 0  这些都不是（返回上一层）")     # 与顶层 `0` 同义：这些都不是
    elif ctx.menu:
        # 🚪⚠️ **顶层菜单态不发 `0`**（2026-10-01 恒当场问出来的）：
        #    > 「选 0 的话，其后有什么？自由操作吗？但是菜单又有门禁。理论上确实只能做 1、2。」
        #    对。那一刻的行**就是全部**能按的（`_candidates` 只放行 `menu_ok`），
        #    而敲 `0` 得到的回答是「界面还开着，先把界面处理掉：<menu_hint>」——
        #    那条路**正是上面第 1 行**（推进/跳过），或者空屏时**已经印过**的那句提示。
        #    ⇒ 它不发号、不通向任何新动作，只会白烧一次调用。**改成把那句提示直接印出来**
        #      （恒那句"其后有什么"的答案，本来就该**摆在眼前**，不该藏在一敲之后）。
        # ⚠️ 空屏那条分支（`if not shown`）已经印过同样的提示，这里靠 `shown` 去重。
        if shown and ctx.menu_hint:
            lines.append(f"  📄 {ctx.menu_hint}")
    else:
        lines.append(" 0  做点别的…  （at x,y 指哪打哪）")
    # 提示优先取这一层自己的（`exec_on_pick` 那类形态跟默认不一样，得说清怎么敲）。
    if lv.hint:
        lines.append(lv.hint)
    elif ctx.menu and lv.mode == "act":
        # 🚪 菜单态**别再把 `at x,y` 写进提示行** —— 它是那条假门的门牌之一
        #    （这一刻 `at` 指出来的动作全被 `do_row` 的守卫挡掉）。
        #    一行都没有时（捏人页/鱼机/对话框）更不该叫人"敲编号"。
        lines.append("> 敲编号" if shown else "> 先照上面那句把界面处理掉")
    else:
        lines.append(_LEVEL_HINT.get(lv.mode, _LEVEL_HINT["act"]))
    return "\n".join(lines)


def render_menu(ctx: Ctx, n: int = 5, header: str = "") -> str:
    """渲染**当前这一屏**单子（顶层，或 AI 点开的子层）。

    - 前 n 条 + **理由列**
    - 被挤掉的**如实报**「还有 K 项」（铁律 2）
    - 末尾永远留 `0`——顶层是**逃出牢笼**的口子，子层是**返回**
    """
    root = Level(_candidates(ctx), header)
    if not _STACK:
        _STACK.append(root)
    else:
        _STACK[0] = root                      # 顶层每次都重算（世界一直在动）
        # ⚠️ 换图 / 开了菜单 ⇒ 上面的子层**全作废**：它那几行的目标格已经不是这个地方的了。
        #    宁可直接收回顶层重给，也不留一屏指向旧世界的号。
        if len(_STACK) > 1 and _fingerprint(ctx) != _STACK[-1].fp:
            _STACK[:] = [root]
    _STACK[-1].fp = _fingerprint(ctx)
    # ⚠️⚠️ **深层的 `show` 不是顶层**（2026-09-29 真机照出来的洞）：`n=5` 只该管**顶层第一屏**
    #    （那里是"从一大堆里挑头几条"）；AI 点开一层后敲 `show`「再看一眼」，它要的是
    #    **刚点开的那一摞本身**。一刀切按 5 印 ⇒ 21 条只剩 5 条，还配一句打不开的假门
    #    （实测：`do 1` 印全 21 条，紧接着 `show` 只剩 5 条 —— 同一层，两个答案）。
    return _render_level(ctx, _STACK[-1], n if len(_STACK) == 1 else _SUB_N)


def _parse_code(code):
    """把 AI 敲的那串拆成 `[(号, 数量或 None)]`。**混着写就报错，不猜**。

    认这三种（中文逗号也认，AI 会打）：
        `1`          单号
        `1,4`        多选 —— **集合，序无关**
        `1=1,4=4`    号=数量 —— **配对自包含 ⇒ 序也无关**

    ⚠️ 混着写（`1,4=4`）**必须拒**：一个"序无关的集合"里混进一个"带了量的项"，
    分不清哪截是哪个意思——猜错了就是静默错误。
    """
    if isinstance(code, int):
        return [(code, None)], None
    s = str(code if code is not None else "").strip().replace("，", ",")
    if not s:
        return None, "❌ 敲个编号（如 `1`），或者 at x,y 指过去"
    out, forms = [], set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            return None, f"❌ 「{code}」里有个空档 —— 写成 `1,4` 或 `1=1,4=4`"
        if "=" in part:
            a, _, b = part.partition("=")
            a, b = a.strip(), b.strip()
            if not a.isdigit() or not b.isdigit():
                return None, f"❌ 「{part}」看不懂 —— 数量写成「号=数量」，如 `1=5`"
            out.append((int(a), int(b)))
            forms.add("=")
        else:
            if not part.isdigit():
                return None, f"❌ 「{part}」看不懂 —— 编号就写数字，如 `1` 或 `1,4`"
            out.append((int(part), None))
            forms.add("n")
    if len(forms) > 1:
        return None, ("❌ 这一串**混了两种写法** —— 要么全写号（`1,4` 选哪些），"
                      "要么全写「号=数量」（`1=1,4=4`）。混着写分不清哪截是哪个意思。")
    if len({n for n, _ in out}) != len(out):
        return None, "❌ 同一个号写了两遍 —— 想改数量写一遍就够，我不猜你要哪个。"
    return out, None


def _row_by_no(lv: Level, no: int):
    if lv.keep_no:
        return next((r for r in lv.rows if r.no == no), None)
    return lv.rows[no - 1] if 1 <= no <= len(lv.rows) else None


def _nos(lv: Level) -> str:
    return "、".join(str(r.no) for r in lv.rows)


def _open_qty(ctx: Ctx, lv: Level, chosen: list):
    """pick 层选完 → qty 层。

    **号沿用上一层发的那些号**（不重排位置）：AI 在下一屏看到的还是它刚选的那几个号，
    不用在两个编号系统之间换算——"跨屏对数"正是我们要删掉的那件事。
    """
    rows = []
    for no in chosen:
        r = _row_by_no(lv, no)
        if r is None:
            return None, f"❌ 这一层只有 {_nos(lv)} 号 —— 没有 {no} 号"
        r.no = no
        rows.append(r)
    sub = Level(rows, title="各多少？（写「号=数量」，如 `1=5`）",
                mode="qty", verb=lv.verb, keep_no=True, fp=lv.fp)
    _push_level(sub, ctx)
    return sub, None


def _push_level(lv: "Level", ctx: Ctx):
    """把一屏推进栈，并**给它盖当场的世界指纹**。

    ⚠️⚠️ 2026-09-29 审查抓出来的洞：子层是 `_candidates` 现场造的，`fp` 是默认的 `()`，
    而 `_fingerprint(ctx)` 恒是真元组 ⇒ `render_menu` 里那句"换图/开菜单才作废"的判据
    **恒成立** ⇒ **AI 只要再看一眼单子，子层就被砍回顶层**，
    而它手上的号已经从"取"变成"第 2 个箱子"了 —— **号跨屏换了意思**，
    正是 166 ③ 说好要删掉的那件事。⇒ **谁进栈谁盖指纹**。
    """
    lv.fp = _fingerprint(ctx)
    _STACK.append(lv)
    return lv


def _do_qty(ctx: Ctx, lv: Level, sel, run) -> str:
    """qty 层：`号=数量` 配对。**只写号的一律拒**——那正是"位置对齐"那个老陷阱。"""
    held = {r.no: r for r in lv.rows}
    pairs = []
    for no, cnt in sel:
        if cnt is None:
            return ("❌ 这一层要写「**号=数量**」（如 `1=1,4=4`）—— 只写号我不猜你要几个。\n"
                    "   （只写号就变成「按位置对齐」，写反了会买对东西、买错数量，还不报错。）")
        if no not in held:
            return (f"❌ 这一层是你刚选的那几样（{_nos(lv)} 号）—— 里面没有 {no} 号。\n"
                    f"   想改选哪些，敲 0 回去重选。")
        if cnt <= 0:
            return f"❌ {no} 号写的是 {cnt} —— 数量得是正整数，不猜。"
        pairs.append((held[no], cnt))
    if lv.verb is None or lv.verb.exec_multi is None:
        return "❌ 这一步还没接执行"
    out = lv.verb.exec_multi(ctx, pairs, run)
    _STACK[:] = _STACK[:1]          # 做完了 ⇒ 收回顶层
    return out


def _recheck(ctx: Ctx, row: "Row"):
    """执行前**复验**：这一行的目标，在**现在的世界**里还成立吗？→ `None`(过) / 拒绝文案。

    ⚠️ 为什么非有不可（`do_row` 那段老注释一直挂着这笔账）：
       号**不跨屏**，可 `_LAST_ROWS` 是**上一次渲染**留下的 —— AI 看一眼单子、过几秒才敲，
       中间世界会动（机器被收走 / 那本书被吃掉 / 椅子被人坐了 / 背包那格被挪了）。
       不复验就会"以为在丢钻石、丢的却是翡翠"，而且**它会照做、不怀疑**。
       ⇒ 这一条**必须先于** 丢东西 / 送礼 / 给东西 / 捐赠 这类"做错就不可逆"的动词落地。
    ⚠️⚠️ 判据打的是**新 ctx 里的同一个目标**，不是拿渲染时那份旧字典再问一遍 ——
       旧字典**自己就是当时的证据**，问它永远"还成立"，复验就成了走过场。
       这跟"单子上的号打的是 `_LAST_ROWS` 而不是重算的第 n 条"**不矛盾**：
       号仍指向它看过的那一行（不会替你改主意），这里只是**再确认那件事还做不做得了**。
    ⚠️ 判据用 `is not True`：`CAN_MAYBE`（算不出来）**也要拦** ——
       "不知道还能不能做"跟"不能做"在**动作**这一层的代价是一样的，
       而单子第一条规矩是"**出现的那条，按了就成**"。（顶层候选那边 MAYBE 本来就不上行。）
    """
    v = row.verb
    if v is None:
        return None
    if v.can is None:
        # ⚠️ 没有 `can()` 的动词**验不了** ⇒ 放行，但这是**已知的洞**：
        #    现在全仓只有容器子层那三个是这种（已补上真判据，见 `_CAN_IS_CHEST`）。
        #    ⇒ **新加的动词一律要有 `can()`**，否则它永远绕过后验
        #      （丢东西/送礼那类"做错不可逆"的更不能例外）。
        return None
    t0 = row.targets[0] if row.targets else None
    which = ""
    if v.target == "world" or t0 is None:
        fresh = None
        # ⚠️ 世界级动词**没有坐标可点名**（"这一刻的处境"），第一版让 `which` 空着，
        #    真机屏上印出来是「（ 的情况变了」—— 一个空格（2026-10-01 真机照出来的）。
        which = "这一刻的处境"
    elif v.target in ("inv", "held"):
        fresh = None
        for it in (ctx.inv or []):
            if t0.get("idx") is not None and it.get("idx") == t0.get("idx"):
                fresh = it
                break
            if (t0.get("idx") is None and (it.get("raw") or {}).get("itemId")
                    and (it.get("raw") or {}).get("itemId")
                    == ((t0.get("raw") or {}).get("itemId"))):
                fresh = it
                break
        which = "那件东西"
        if fresh is None:
            return (f"⏳ 「{row.label}」{which}**已经不在背包里了**"
                    f"（你手上那张单子是**上一次**看的）。\n"
                    f"   敲 `show` 重开一张 —— 号会当场重发，别按着旧号敲。")
    elif v.target == "menu":
        # 📋 菜单里的那一摞：**按格号**在"现在这个菜单"里找同一个槽位。
        #    ⚠️ 必须复验：菜单可能已经关了（`_fingerprint` 只清**子层**，`_LAST_ROWS` 是上一屏的），
        #       也可能那格已经被拿走/压实挪位 ⇒ 不复验就会"以为在取钻石、取的是翡翠"。
        want = t0.get("index") if isinstance(t0, dict) else None
        fresh = None
        for it in _menu_box_items(ctx):
            if it.get("index") == want:
                fresh = it
                break
        which = "箱内那一摞"
        if fresh is None:
            return (f"⏳ 「{row.label}」{which}**已经不在这个菜单里了**"
                    f"（菜单关了，或者那格已经被拿走 / 挪位了）。\n"
                    f"   敲 `show` 重开一张 —— 号会当场重发，别按着旧号敲。")
    elif v.target == "option":
        # 🗳 对话选项：按**位次 + 文字**两条一起核。⚠️ 只核位次不够 ——
        #    对话往下走了之后，同一个位次上**换成了另一句**，那就会"以为在选 A、实际选了 B"。
        want = t0.get("index") if isinstance(t0, dict) else None
        want_txt = (t0.get("text") or "") if isinstance(t0, dict) else ""
        fresh = None
        for o in _menu_options(ctx):
            if o.get("index") == want and (o.get("text") or "") == want_txt:
                fresh = o
                break
        which = "那个选项"
        if fresh is None:
            return (f"⏳ 「{row.label}」{which}**已经不在这一屏了**"
                    f"（对话往下走了，选项换了一批）。\n"
                    f"   敲 `show` 重开一张 —— 号会当场重发，别按着旧号敲。")
    else:
        x, y = t0.get("x"), t0.get("y")
        fresh = ctx.tile(x, y) if isinstance(x, int) and isinstance(y, int) else None
        which = f"({x},{y}) 那个位置"
        if fresh is None:
            return (f"⏳ 「{row.label}」{which}**现在什么都没有了**"
                    f"（你手上那张单子是**上一次**看的）。\n"
                    f"   敲 `show` 重开一张 —— 号会当场重发。")
    try:
        ok_now = v.can(ctx, fresh) is True
    except Exception as e:
        return f"⏳ 「{row.label}」复验时出错（{type(e).__name__}: {e}）—— 不敢硬做，敲 `show` 重来"
    if not ok_now:
        return (f"⏳ 「{row.label}」**这一刻做不了了** —— {which}变了"
                f"（你手上那张单子是**上一次**看的）。\n"
                f"   敲 `show` 重开一张：它会按**现在的世界**重列，号也当场重发。")
    return None


def do_row(code, run: Callable, ctx: Ctx = None) -> str:
    """敲单子。`code` 可以是 `1` / `"1,4"` / `"1=1,4=4"` / `0`。

    ⚠️ **打的是上一次渲染出来的那一行**（AI 实际看见的），**不是重算的第 n 条**。
    重算出来的第 n 条可能**已经不是它看见的那条**了（它读单子的时候世界变了）
    ⇒ 它会以为在收钻石、我们收的却是翡翠，**而它会照做、不怀疑**。

    ⚠️ **敲错号的代价**：本层打的是**上一次渲染出来的那一行**（见上），所以误敲 = 做了另一件事。
       （2026-10-03 记：这里原来写着"`/machine_collect` 自己会跳过已收的机器 ⇒ 误敲代价是啥也没发生"——
        那个端点**已经删了**，这句话作废。**接了会"做错事"的动词（丢东西/送礼）必须先补验**。）
    """
    if not _STACK:
        return "❌ 手上还没有单子 —— 先看一眼，再敲编号"
    lv = _STACK[-1]
    sel, err = _parse_code(code)
    if err:
        return err

    # ── 0 = 这些都不是（顶层是"做点别的"，子层是"返回"——同一个语义）────────
    if len(sel) == 1 and sel[0] == (0, None):
        if len(_STACK) > 1:
            _STACK.pop()
            return _render_level(ctx, _STACK[-1], 5)
        if ctx is not None and getattr(ctx, "menu", None):
            # 🚪⚠️ 菜单态下「用 at x,y 指一样东西」是**假门**（指了也按不动）——
            #    换成本刻真正的出口（`menu_hint` 是服务器 `_close_hint` 的原话）。
            #    ⚠️ 连 `at x,y` 这几个字都别写出来：那正是假门的门牌
            #       （自验里有一条就查这个 token —— 见 `_selftest` 的 🚪 用例）。
            _mt = (ctx.menu or {}).get("type") or "?"
            return (f"👌 好。⚠️ 界面还开着（{_mt}）—— 这一刻**指哪一格都按不动**，"
                    f"先把界面处理掉：\n   {ctx.menu_hint or '走 menu 域处理它'}")
        return "👌 好，做点别的去 —— 想指哪一样东西，用 at x,y"

    if lv.mode == "qty":
        return _do_qty(ctx, lv, sel, run)

    # 🚧 **菜单态守卫**（2026-09-30）：菜单开着时只放行"本来就通过菜单干活"的动词。
    #    ⚠️ 为什么**执行这一层还要判一次**：号虽然不跨屏，但 `_LAST_ROWS` 可能是
    #       **菜单开起来之前**那一屏留下来的 ⇒ AI 敲一个旧号，就会在菜单开着的时候
    #       跑一个世界动作（那正是 MCP 那道菜单闸门要挡的事）。
    #       根因已经收在 `_candidates`（菜单态只列 `menu_ok`），这里是**执行侧兜底**。
    #    ⚠️⚠️ 2026-10-01：文案里的「菜单态能做的只有 买 / 卖」**过期了** ——
    #       现在多了「关掉界面」（`CLOSE_V`）。**别再写死一份清单**：出口行在不在
    #       取决于菜单类型（捏人页/鱼机/对话框压根没有），死清单会当场说错话。
    if getattr(ctx, "menu", None) and lv.mode == "act":
        _bad = []
        for _no, _c in sel:
            _r = _row_by_no(lv, _no)
            if _r is not None and _r.verb is not None and not getattr(_r.verb, "menu_ok", False):
                _bad.append(str(_no))
        if _bad:
            _m = (ctx.menu or {}).get("type") or "?"
            _can = "关掉界面" if ctx.menu_exit else "（这个界面没有「敲一下就好」的出口）"
            return (f"🚧 菜单开着（{_m}）—— {'、'.join(_bad)} 号是**菜单态做不了**的动作，先别敲。\n"
                    f"   这一刻能按的：{_can}"
                    + ("、买 / 卖" if ctx.shop is not None else "") + "。\n"
                    f"   敲 `show` 重开一张单子（它会**只列能按的**，号也当场重发）"
                    + (f"\n   📄 {ctx.menu_hint}" if ctx.menu_hint else ""))

    if any(c is not None for _, c in sel):
        return ("❌ 这一层没有数量要填 —— 直接写号就行（`1` 或 `1,4`）。\n"
                "   要填数量是**选完**之后那一屏的事。")
    if lv.mode == "act" and len(sel) > 1:
        return ("❌ 这一层一次只能敲一个 —— 要连着做几件，一件一件来。\n"
                "   （「多选」是**选哪些**那一层才有的）")

    # ── pick 层：可以一次选多个（`1,4`）──────────────────────────
    if lv.mode == "pick":
        # 💰 `exec_on_pick`：选完**直接做**，不进「各多少」——因为**游戏自己定量**
        #    （卖=单击卖整个堆叠）。给一屏能填数量的假界面 = 填了不生效还不报错。
        # ⚠️ 数量传 `None`：执行器要能分辨"没数量这回事"和"数量是 0"。
        if lv.exec_on_pick:
            if lv.verb is None or lv.verb.exec_multi is None:
                return "❌ 这一步还没接执行"
            rows = []
            for no, _c in sel:
                r = _row_by_no(lv, no)
                if r is None:
                    return (f"❌ 这一层只有 {_nos(lv)} 号 —— 没有 {no} 号。\n"
                            f"   想指别的东西，用 at x,y")
                rows.append((r, None))
            # 🔍 复验（同 act 那条；`exec_on_pick` 是"选完直接做"，一样会碰到世界变了的号）
            for _r, _c in rows:
                _stale = _recheck(ctx, _r)
                if _stale:
                    return _stale
            out = lv.verb.exec_multi(ctx, rows, run)
            _STACK[:] = _STACK[:1]          # 做完了 ⇒ 收回顶层
            return out
        sub, e = _open_qty(ctx, lv, [n for n, _ in sel])
        return e if e else _render_level(ctx, sub, _SUB_N)

    no = sel[0][0]
    row = _row_by_no(lv, no)
    if row is None:
        return (f"❌ 这一层只有 {_nos(lv)} 号 —— 没有 {no} 号。\n"
                f"   想指别的东西，用 at x,y")

    # 🗂 目录行：点开下一层（**本身不执行任何东西**）
    # ⚠️ 子层用 `_SUB_N`（不是 5）：这是"AI 刚点开的那一摞"，**要能看完**
    #    ——写死 5 会让尾巴那句「还有 N 项（more）」变成空承诺（没有 `more` 这个口子）。
    if row.level is not None:
        _push_level(row.level, ctx)          # ⚠️ 推栈必须盖指纹，否则"再看一眼"就把这层砍掉
        return _render_level(ctx, row.level, _SUB_N)

    if row.verb.exec is None:
        return f"❌ 「{row.label}」还没接执行"
    # 🔍 **执行前复验**（见 `_recheck`）—— 单子可能是上一次看的，世界会动。
    _stale = _recheck(ctx, row)
    if _stale:
        return _stale
    out = row.verb.exec(ctx, row.targets, run)
    _STACK[:] = _STACK[:1]          # 做完了 ⇒ 收回顶层
    return out


def render_at(ctx: Ctx, x: int, y: int) -> str:
    """🎯 指哪打哪——AI 自己指定目标，**绕过排序**，无损。

    ⚠️ 指到**没账**的（`ctx.tiles` 里没有这一格）就如实说「我这儿没有这一格的账」，
       **绝不给"附近有什么"** —— 编一个像样的答案比报错危害大得多。
    🔴 2026-10-04 **真机改了措辞**（恒让我「at 多试试」时撞出来的）：原来这句写的是
       「那里**什么都没有**」—— 那是**对世界的断言**，而我们的 `tile` 表**根本不含**建筑层/水/处境动词：
         · `at 71,14` 出货箱     → 「空地」（实际有个出货箱）
         · `at 48,56` 鱼塘       → 「那里什么都没有」
         · `at 70,13` 我自己脚下 → 「那里什么都没有」
       而 `at 74,16` 祝福雕像 ⇒ 「Statue Of Blessings /（**这里没有它能做的动作**）」
       —— 可**顶层单子上明明有**「摸 雕像」（它是 `target="world"` 的动词，`at` 这层只收
       `target == "tile"` 的）⇒ AI 据此会得出"这雕像不能摸"的**假结论**。
       ⇒ 措辞改成**说我自己的账**（"没有这一格的账"）+ 明说**不等于"不能做事"**、
         按处境算的动作去 `show`。**不许再出现「那里什么都没有」这种对世界的断言。**
    """
    t = ctx.tile(x, y)
    if not t:
        return (f"📍 ({x},{y}) —— **我这儿没有这一格的账**"
                f"（不在视野里，或它是地面/水/建筑这类**不进 `tile` 表**的东西）\n"
                f"   ⚠️ 这**不是**「这儿不能做事」：摸雕像、收鱼塘、投出货箱那种"
                f"**按处境算**的动作在 `show` 那张单子上（`at` 只看「格子上长了什么」）")

    # 作物优先：有作物的格子上 `object` 通常是空的，只看 object 会把萝卜地印成"空地"。
    # 📦 箱子格同理：它的名字在 `/scan_chests` 那份明细里（`DisplayChestName`）——
    #    不补这一档，下面列着「1 矿石箱…」、抬头却写「空地」，同一屏自相矛盾。
    # ⚠️ **不拿 `crop` 兜底**：它是产物 item ID（`crop.indexOfHarvest`），印出来是个数字。
    name = (t.get("object") or t.get("cropName") or (_box(t) or {}).get("name")
            or ("作物" if t.get("harvestable") else None)
            or t.get("terrain") or "空地")
    head = f"📍 ({x},{y})  {name}"

    # ⚠️ 这里跟 `render_menu` 不一样：**接没接执行都列**，但分开列。
    #    理由：`at(x,y)` 是**问句**（"这东西能干什么"），人问的时候想要**完整**答案；
    #    单子是**动作面**，那儿才必须"按了就成"。
    #    顺带——那行「⏳ 还没接执行」就是我们的**缺口探测器**：
    #    AI 老指着某类东西而我们接不上，那就是动词表欠的账，比拍脑袋想准得多。
    ready, pending = [], []
    for v in VERBS:
        if v.target != "tile":
            continue
        if v.can(ctx, t) is True:
            # ⚠️ **有 `subs` 的目录动词也算"按得动"**——它点开就是动作面（166 ②）。
            #    原来只认 `exec`，会把「箱子」错判成"还没接执行"，摆进 ⏳ 那行。
            (ready if (v.exec or v.subs) else pending).append(v)
    ready.sort(key=lambda v: -_weight_of(v, ctx))

    title = head
    if not ready:
        # 🔴 2026-10-04 真机：`at 74,16`（祝福雕像）走到这儿 —— 可顶层单子上有「摸 雕像」
        #    （它是 `target="world"`，这一层只收 `target == "tile"` 的）⇒ 原话
        #    「这里没有它能做的动作」会被读成"这雕像不能摸"。措辞收成**按格算**的，并指路 `show`。
        title += "\n  （这一格没有**按格算**的动作 —— 按处境算的那些看 `show` 那张单子）"
    # 🚪 **菜单态**：下面列的世界动作**这一刻全按不动**（`do_row` 的菜单态守卫会挡）。
    #    ⚠️ 所以必须**把话说在前面 + 把真门摆在第一行** —— 否则 `at` 就是那条假门：
    #       它给出一屏"坐/搬走"，AI 敲了却碰壁（2026-10-01 真机复现的三步）。
    #    ⚠️ `menu_hint` 那一行照抄服务器 `_close_hint` 的原话（**不许这儿另编一句**）。
    if ctx.menu:
        _mt = (ctx.menu or {}).get("type") or "?"
        title += f"\n  🚧 菜单开着（{_mt}）—— 下面这些**这一刻按不动**"
        if not ctx.menu_exit:
            title += f"\n  📄 {ctx.menu_hint}" if ctx.menu_hint else ""
    if pending:
        # ⏳ 这行仍是**缺口探测器**：AI 老指着某类东西而我们接不上 = 动词表欠的账。
        title += "\n  ⏳ 还没接执行：" + "、".join(v.label for v in pending)

    # ⚠️⚠️ **必须推栈**（2026-09-29 真机照出来的洞）：这一屏也有号、也写着"敲编号"，
    #    不推栈的话号是**悬空的** —— `do(1)` 会落到 `_STACK[-1]`，也就是**上一屏**的第 1 行。
    #    实测：`at 24 26`（椅子上）显示「1 坐 胡桃木椅子」，敲 `1` **却去收了机器**。
    #    **屏幕上有号、号指向别处** —— 正是 166③ 花大力气删掉的那类静默错误动作。
    #    ⇒ 走**和单子同一条造行路径**（`_row_for`），保证两屏的号是同一种东西。
    lv = Level([_row_for(ctx, v, [t]) for v in ready], title=title)
    # 🚪 菜单态：把真门**插到第一行**（`ready` 里那些按不动，谁在前都无所谓）。
    if ctx.menu and ctx.menu_exit:
        lv.rows = [_row_for(ctx, CLOSE_V, [None])] + lv.rows
    _push_level(lv, ctx)
    return _render_level(ctx, lv, max(9, len(lv.rows)))


def render_receipt(action: str, target_desc: str, ok: bool,
                   note: str = "", backpack_after: str = "") -> str:
    """✅ 回执——**铁律 3 的落点**。

    必须回显"你刚做了什么对象、多少数量"，以及**做完之后背包什么样**。
    槽位会漂，AI 靠回执刷新自己；不写回执 = 逼它拿旧位次去点下一枪。
    """
    mark = "✅" if ok else "❌"
    lines = [f"{mark} {action} {target_desc}"]
    if note:
        lines.append(f"   {note}")
    if backpack_after:
        lines.append(f"   回执：{backpack_after}")
    return "\n".join(lines)


def scan_world(surr: dict, machines: list = None, chests: list = None,
               seats: dict = None, furniture: dict = None, animals: dict = None,
               beds: list = None) -> dict:
    """把 `/surroundings` + `/machines` + `/scan_chests` 三个回包**叠成一格一栈**。

    ⚠️ 为什么叠进同一格：一格上本来就能有多层（地板 / 物体 / 机器 / 家具）——
    恒提的"它脚下的地毯"就是这个。分开存会让"搬走"搬的是哪样说不清。

    ⚠️ 机器/箱子是**整张图**的（`/machines` 给全图），surroundings 只有扫描半径内。
    这是故意的：`at(x,y)` 要能指到屋子那头的机器（人也是远远看见、再走过去），
    而单子的**候选格**仍旧只有 当前格+四邻——那条线由 `around()` 管，不在这儿收。
    """
    tiles = {}
    for t in (surr or {}).get("tiles") or []:
        if isinstance(t.get("x"), int) and isinstance(t.get("y"), int):
            tiles[(t["x"], t["y"])] = t

    for m in machines or []:
        x, y = m.get("x"), m.get("y")
        if not (isinstance(x, int) and isinstance(y, int)):
            continue
        tiles.setdefault((x, y), {"x": x, "y": y})["machine"] = {
            "type": m.get("type"), "status": m.get("status"),
            "item": m.get("heldItem"), "minutes": m.get("minutesLeft"),
        }

    for c in chests or []:
        x, y = c.get("x"), c.get("y")
        if not (isinstance(x, int) and isinstance(y, int)):
            continue
        t = tiles.setdefault((x, y), {"x": x, "y": y})
        t["is_chest"] = True
        t["chest_items"] = len(c.get("items") or [])
        # 📦 容器明细**整份搬过来**（166 ⑤ 的存/取行全靠它算）。
        #    ⚠️ 字段名照抄 `/scan_chests` 的原样，别在这儿改名——
        #    `capacity`/`used`/`freeSlots` 都是**游戏算给我们的数**（`GetActualCapacity()`）。
        t["chest"] = {
            "name": c.get("name") or "",
            "items": c.get("items") or [],
            "capacity": c.get("capacity"),
            "used": c.get("used"),
            "freeSlots": c.get("freeSlots"),
            # 🆕 2026-09-30(178) **真机抓到的洞**：这四个字段原先被上面那张白名单**丢掉了**，
            #    而 `_chest_tag()` 正是照 `storage_layout` 的口径去读 `color`/`autoTag`/`typeName` 的
            #    ⇒ 166 写的"一览的标签照抄 storage_layout"**从来没成立过**：一览里一直只剩
            #    色块位 `⬜` 和人工名（`autoTag` 也一直是空的，只是没人注意）。
            #    实证（178 真机同一时刻）：屏① 印 `⬜ (25,23)`，屏②/屏③ 同一只箱子印 `⬜迷你冰箱`。
            #    ⚠️ **摘字段是静默的**——外面看不出"少读了什么"，所以这张白名单只许**加**、
            #    加的时候顺手在 `_chest_tag` 那边也扫一眼它读哪些键。
            "color": c.get("color"),
            "autoTag": c.get("autoTag"),
            "typeId": c.get("typeId"),
            "typeName": c.get("typeName"),
        }

    # 🪑 座位（`/sittable`）。判据在 C# 里照抄游戏（`GetSeatCapacity()` + `mapSeats`），
    #    我们只搬结果。**只挂锚点那一格**——座位点是游戏给的交互格。
    for s in (seats or {}).get("seats") or []:
        x, y = s.get("x"), s.get("y")
        if isinstance(x, int) and isinstance(y, int):
            tiles.setdefault((x, y), {"x": x, "y": y})["seat"] = s

    # 🛋 家具（`/furniture`）。⚠️ **只挂锚点格**（`TileLocation`）：
    #    大件（沙发/钢琴）覆盖多格，若逐格都挂，同一件会在单子上出现好几行
    #    （聚合键是"名字+坐标"，坐标不同 = 好几行），**AI 会以为有好几件**。
    for f in (furniture or {}).get("furniture") or []:
        x, y = f.get("x"), f.get("y")
        if isinstance(x, int) and isinstance(y, int):
            tiles.setdefault((x, y), {"x": x, "y": y})["furniture"] = f

    # 🛏 床（`crawl_bed locate` 的 `bed`）——**只挂"是谁的床"这一条**，由调用方给。
    #    ⚠️ 为什么不由 `/furniture` 的 `furnitureType == 15` 认：那能认出**所有** `BedFurniture`
    #    （实测本屋 3 张：1 双人 + 2 儿童床），但**儿童床不能睡**，而"哪些是儿童床"的游戏属性
    #    `bedSize` 在这版 SDV 参考程序集里**不存在** ⇒ C# 那边也只能按名字判（`IsChildBed`）。
    #    改走 `crawl_bed` 的好处：它找的是 `FindMasterBed`（**本来就跳过儿童床**）⇒
    #    **"能睡的床"和"是谁的床"一次拿全，一个名字名单都不用编**。
    for b in beds or []:
        x, y = b.get("x"), b.get("y")
        if isinstance(x, int) and isinstance(y, int):
            tiles.setdefault((x, y), {"x": x, "y": y})["bed"] = b

    # 🐄 牲畜（`/animals`）——`wasPetToday` **就在回包里**（"今天摸过没"问得到）。
    for a in (animals or {}).get("animals") or []:
        x, y = a.get("x"), a.get("y")
        if isinstance(x, int) and isinstance(y, int):
            tiles.setdefault((x, y), {"x": x, "y": y})["animal"] = a
    return tiles


def ctx_from(state: dict, surr: dict, machines: list = None, chests: list = None,
             caps: dict = None, seats: dict = None, furniture: dict = None,
             animals: dict = None, shop: dict = None, beds: list = None,
             menu_exit: str = "", menu_hint: str = "", menu_claim: str = "", worn: dict = None,
             menu_data: dict = None, reforge: dict = None, mwork: dict = None,
             doors: dict = None, chores: dict = None, clint_open: bool = False,
             hay: dict = None, pick: dict = None,
             ponds: dict = None, statue: dict = None, tank: dict = None,
             quests: dict = None, levelup: dict = None, cc: dict = None,
             cc_board: dict = None, museum_go: dict = None) -> Ctx:
    """把 `/state`(**full**) + `/surroundings`(+`/machines`/`/scan_chests`) 拼成 Ctx。

    ⚠️ 只搬运，**不补默认值**：缺什么就让它缺着（`can()` 遇到缺失自然回 假/？）。
    ⚠️ `caps` **必须调用方给**（见 `Ctx.caps` 那段），这里不猜。
    ⚠️ `shop` 同理：**没开商店传 `None`**，开了但读不出来传一个空字典
       （两种"判不出来"在 `_buy_can`/`_sell_can` 里要分开，见 `Ctx.shop` 那段）。
    ⚠️ `menu_exit`/`menu_hint` 也一样**必须调用方给**（判菜单类型的表在服务器
       `_close_hint`/`_menu_exit_of` 那儿，只有一处）——这里**不照着菜单类型自己推**。
    """
    p = (state or {}).get("player") or {}
    inv = scan_backpack(state)

    # 手持：**`currentItem` 才是手持**。
    # ⚠️⚠️ 2026-09-29 修：原先读的是 `currentTool`（"手上的**工具**"）——
    #    书 / 食物 / 种子都不是 Tool，`CurrentTool` 恒 null ⇒ **手持恒为 None**，
    #    「吃」「看」这两条行**结构性地永远不会出现**。CHANGELOG 165 ③ 真机当晚就写了
    #    「`currentItem` 才是手持」，但那句话没落回这里（写在别处的账，代码没跟上）。
    # ⚠️⚠️ 比的是 **`raw.name`（英文内部名）**，不是 `displayName`（中文）——
    #    2026-09-27 真机第一次跑就栽在这：`currentTool="Galaxy Hammer"`
    #    而显示名是"银河之锤"，拿显示名比 ⇒ **手持恒为 None**，静默。
    #    （fixture 编不出这个 bug：我编的两边是自洽的。这就是真机的价值。）
    # 精确匹配优先用 **`currentItemId`（QualifiedItemId）**：地板/墙纸那类「同名多款」
    # 只差一个 id（同 `/select` 那条老账，2026-09-19）。
    # ⚠️ 名字仍可能重（两把同名工具）⇒ 名字那条是**近似**。找不到就**没有手持**
    #    （不猜一个最像的）。
    held = None
    ci, ciid = p.get("currentItem"), p.get("currentItemId")
    if ciid:
        for it in inv:
            if (it.get("raw") or {}).get("itemId") == ciid:
                held = it
                break
    if held is None and ci:
        for it in inv:
            if (it.get("raw") or {}).get("name") == ci:
                held = it
                break

    tiles = scan_world(surr, machines, chests, seats, furniture, animals, beds)

    # 🀄 英文→中文对照：只用**手里已有的数据**堆，不为此打 HTTP。
    zh = {}
    for it in inv:
        rn = (it.get("raw") or {}).get("name")
        if rn:
            zh[rn] = it["name"]
    for c in chests or []:
        for ci in c.get("items") or []:
            n, dn = ci.get("name"), ci.get("displayName")
            if n and dn:
                zh.setdefault(n, dn)
    # 🆕 2026-09-30：**机器里那件产物**也进对照表。
    #    原先它只能靠"背包/箱子里碰巧有同名物品"才凑得出中文 ⇒ 真机实拍过中英混排的丑行：
    #    `翡翠×17、Diamond×1、Iridium Ore×1`（`Diamond` 当时背包和箱子里都没有 ⇒ 查不到）。
    #    现在 `/machines` 直接吐 `heldItemDisplay`（**问游戏要的名字**）——
    #    ✅ 消费侧（「收放」理由栏）直接用服务器递来的 `products` —— 中文名由 `/machines` 直供。
    #    ⚠️ **不在这儿堆名单**（名单会烂，本项目的老病：1.6 矿节点 ID、`Jewels Of The Sea`）。
    for m in machines or []:
        n, dn = m.get("heldItem"), m.get("heldItemDisplay")
        if n and dn:
            zh.setdefault(n, dn)

    # 🐾 猫狗（宠物）：`/surroundings` 的 `npcs` 里 `kind == "pet"` 那几个。
    #    它们**不是** NPC、也不在 tiles 上 ⇒ 世界级（`target="world"` 的动词看这个）。
    pets = [n for n in ((surr or {}).get("npcs") or []) if n.get("kind") == "pet"]

    return Ctx(px=p.get("x") or 0, py=p.get("y") or 0,
               loc=((state or {}).get("location") or {}).get("name") or "",
               held=held, inv=inv, tiles=tiles,
               menu=(state or {}).get("activeMenu"),
               event=(state or {}).get("activeEvent"),
               # 🚪 界面出口（服务器算好的；`""` = 这一刻不该给这一行）。
               menu_exit=menu_exit or "", menu_hint=menu_hint or "", menu_claim=menu_claim or "",
               # 📋 菜单内容（同上：服务器挑好递进来，这里**不猜**）。
               menu_data=menu_data or {},
               # 🔨 铁砧能不能重铸（同上：服务器探针算好递进来，「铱锭要几块」这种数**不在这儿编**）。
               reforge=reforge or {},
               # 🧺🔁 收放那行的账（同上：`/machines` + `/machine_reqs` 探针，服务器算好递进来）。
               mwork=mwork or {},
               # 🚪🐄 放牧/关棚门那两行的账（同上：`/state.time` 的天气季节 + `/farm_buildings`，
               #    服务器算好递进来 —— 这里**不猜**"哪些建筑算动物建筑"）。
               doors=doors or {},
               # 🌿 六件"顺手活"那 6 行的账（同上：服务器算好递进来 ——
               #    这一层不认"哪些算斑点/苔藓"，也不打 HTTP）。
               chores=chores or {},
               # 🏪 铁匠铺营业中吗（同上：服务器算好递进来；`False` = 关门**或**读不到）。
               clint_open=bool(clint_open),
               # 🌾 铺干草那行的账（同上：`feed_hay.read_hay_status()` 那份，服务器递进来）。
               hay=hay or {},
               # 🎁 地上可捡清单（同上：`pickup_scene.scan_pickables()` 那份）。
               pick=pick or {},
               # 🗿🐟 2026-10-04 那两行的账（同上：`_im_statue` / `_im_ponds` 算好递进来 ——
               #    这一层不认"哪些雕像算数"，也不自己问鱼塘）。
               statue=statue or {}, ponds=ponds or {},
               # 🐟 「放 … 进鱼缸」那两行的账（同上：`_im_tank` → C# `/tank` —— 这一层
               #    不认"游戏收什么"，也不自己问背包）。
               tank=tank or {},
               # 📜 「领取奖励」那行的账（同上：`_im_quests` 只在 QuestLog 开着时读一次 `/menu`，
               #    这一层不认菜单名、也不自己问任务）。
               quests=quests or {},
               # 🧬 「选职业」那两行的账（同上：`_im_levelup` 从 `/state.activeMenu.levelUp` 拿，
               #    这一层不认菜单名、也不编职业名）。
               levelup=levelup or {},
               # 🏛️ 献祭板那两层的账（同上：`_im_cc` 读 `/menu.characterCust` + `/bundles`，
               #    判据全在游戏那边 —— 这一层不认 id/类别/品质）。
               cc=cc or {},
               # 🏛️ 世界侧入口那行的账（同上：_im_cc_board 算好递进来）。
               cc_board=cc_board or {},
               # 🏛️ 世界侧那行「去博物馆捐赠（包里 N 件可捐）」的账（补24c，同上：
               #    `_im_museum_go` 算好递进来，`{}` = 不给行 —— 包里没有可捐的 / 老 DLL 报不出）。
               museum_go=museum_go or {},
               sitting=bool(((seats or {}).get("me") or {}).get("sitting")),
               worn=(worn or {}),
               pets=pets,
               # 🆕 2026-09-30：棚里还没摸的（`/animals` 的 `inBuildings` 汇总）——
               #    老 DLL 没这个键 ⇒ `{}` ⇒ 行为跟以前一模一样（不留新默认值）。
               animals_away=(animals or {}).get("inBuildings") or {},
               stamina=p.get("stamina") or 0, max_items=p.get("maxItems") or 0,
               money=p.get("money") or 0,
               caps=caps or {}, zh=zh, shop=shop,
               time=_clock_of(state),
               health=p.get("health") or 0, max_health=p.get("maxHealth") or 0,
               max_stamina=p.get("maxStamina") or 0)


def _clock_of(state) -> str:
    """`/state` 的 `time` → **钟点串**（`"13:20"`）。

    ⚠️⚠️ `/state` 的 `time` 是个**字典**（`{"timeOfDay": 1320, "season": "summer", …}`），
    **不是字符串**。2026-09-29 真机当场照出来的：我原来写 `str(state["time"])`
    ⇒ 理由栏印出 `现在 {'timeOfDay': 1320, 'dayOfMonth': 7, …}`；
    **更坏的是它不报错** —— `_is_nightish` 解析失败 ⇒ 恒回 False ⇒ **夜里"睡觉"永远上不去**
    （整条功能静默失效，屏上一切正常）。同族坑：`/state` 瘦 `/menu` 详。
    """
    t = (state or {}).get("time") or {}
    try:
        tod = int(t.get("timeOfDay") or 0)
    except Exception:
        return ""
    return f"{tod // 100:02d}:{tod % 100:02d}"


# ═══════════════════════════════════════════════════════════════════════
# ⑤ 不吃游戏自验（fixture 用**真实字段名**造的形，值全是占位的）
# ═══════════════════════════════════════════════════════════════════════
# ⚠️ 这是形，不是真值——别拿它当"验过了"。真值要等开游戏灌进去。

def _fixture():
    def item(idx, name, stack=1, cat=None, edible=None, sellable=True, val=0, shippable=True):
        # 🗑️ `shippable`（2026-10-01）：形照新 DLL 的 `/state`（`Item.canBeShipped()`）。
        #    ⚠️ 跟 `sellable` **不是一回事**：`sellable` 只排 工具/武器/靴/戒，
        #       而大型可制造物 / 帽子 / 衣服那些 `shippable=False`。
        return {"slotIndex": idx, "displayName": name, "stack": stack,
                "catNum": cat, "edibleValue": edible, "sellable": sellable,
                "shippable": shippable, "value": val, "quality": 0}

    state = {"inventory": [
        item(2, "古书", cat=BOOK_CAT),        # ① 是书（catNum 问出来的）
        item(3, "草莓", stack=5, cat=-79, edible=20, val=120),
        item(4, "锄头", sellable=False),       # 老式：catNum 缺失 → 吃/看都该是 ？
    ]}
    inv = scan_backpack(state)
    tiles = {
        # 手推的形：字段名照抄 /surroundings 的真实输出，值全是占位的
        (12, 9): {"x": 12, "y": 9, "passable": False, "object": "木椅"},
        (13, 12): {"x": 13, "y": 12, "passable": True, "forage": True, "object": "野莓",
                   "objId": "(O)296"},
        (11, 11): {"x": 11, "y": 11, "passable": True, "forage": True, "object": "野莓B",
                   "objId": "(O)296"},
        (13, 11): {"x": 13, "y": 11, "passable": True, "forage": True, "object": "野莓C",
                   "objId": "(O)296"},
        (11, 12): {"x": 11, "y": 12, "passable": True, "diggable": True},
        (12, 13): {"x": 12, "y": 13, "passable": True, "harvestable": True,
                   "cropName": "萝卜", "crop": "24", "cropScythe": False},
        (12, 11): {"x": 12, "y": 11, "passable": True},   # 空地：什么都不该出
        # 两台出同样的东西 —— 专门用来验「聚合成一行」
        (14, 12): {"x": 14, "y": 12, "machine": {"status": "ready", "item": "Diamond"}},
        (15, 12): {"x": 15, "y": 12, "machine": {"status": "ready", "item": "Diamond"}},
        # 📦 容器：形照 `/scan_chests` 的真实回包（字段名原样），值全是占位的。
        (13, 13): {"x": 13, "y": 13, "is_chest": True, "chest_items": 3, "chest": {
            "name": "矿石箱", "capacity": 36, "used": 3, "freeSlots": 33,
            "items": [{"name": "Diamond", "displayName": "钻石", "count": 2, "qualifiedId": "(O)72"},
                      {"name": "Jade", "displayName": "翡翠", "count": 7, "qualifiedId": "(O)70"},
                      {"name": "Stone", "displayName": "石头", "count": 99, "qualifiedId": "(O)390"}]}},
        # 满箱（freeSlots=0）→ 「存」那条行**不该出现**（不赌"能叠上去"）
        (11, 13): {"x": 11, "y": 13, "is_chest": True, "chest_items": 1, "chest": {
            "name": "满箱", "capacity": 36, "used": 36, "freeSlots": 0,
            "items": [{"name": "Stone", "displayName": "石头", "count": 99, "qualifiedId": "(O)390"}]}},
        # 🪑 座位 / 🛋 家具 / 🐄 牲畜（形照 `/sittable` `/furniture` `/animals` 的真实回包）
        (14, 13): {"x": 14, "y": 13, "seat": {"kind": "furniture", "name": "木椅",
                                             "x": 14, "y": 13, "capacity": 1, "free": 1,
                                             "face": False}},
        (15, 13): {"x": 15, "y": 13, "furniture": {"name": "红沙发", "x": 15, "y": 13,
                                                   "width": 2, "height": 1, "furnitureType": 0}},
        (11, 14): {"x": 11, "y": 14, "animal": {"name": "牛牛", "type": "White Cow",
                                                "wasPetToday": False, "friendship": 120}},
        (12, 14): {"x": 12, "y": 14, "animal": {"name": "哞哞", "type": "White Cow",
                                                "wasPetToday": True, "friendship": 60}},
    }
    return Ctx(px=12, py=12, loc="FarmHouse", inv=inv, held=inv[0],
               tiles=tiles, stamina=268, max_items=36,
               pets=[{"name": "喵喵", "kind": "pet"}],
               caps={"forage": True, "diggable": True, "harvestable": True},
               # 🎁 「捡」那行的账（2026-10-01 恒：复用 `pickup_scene` 的捡蛋判据）——
               #    形照服务器 `_im_pick` 的产物：那份清单**只在 `pickup_scene.scan_pickables()`
               #    里算**，夹具只搬结果（三处野莓 = 老判据下那三格）。
               pick={"n": 3, "near": 1, "keys": [[11, 11], [13, 11], [13, 12]]},
               # 🧺🔁 收放那行的账（2026-10-01 (b)）：形照**服务器** `_im_mwork` 的产物
               #    （`/machines` 的 status + `/machine_reqs` 的 canPlace 探针）。
               #    ⚠️ 这一层**自己不算**这个字典 —— 夹具手写 = 跟真机那一处**必然漂**
               #    （所以 `_intent_wiring_selftest.py` 那边还有一条**走真探针**的用例）。
               mwork={"ready": 2, "empty": 1, "products": {"钻石": 2},
                      "loadable": [{"name": "Jade", "display_name": "翡翠", "count": 7,
                                    "type": "Crystalarium", "type_display": "宝石复制机",
                                    "n": 1}]})


def _selftest():
    ok = []
    reset_menu()          # 单子是一叠，会跨用例留下来 —— 每个用例开头自己清
    ctx = _fixture()

    def _no_of(needle):
        """在**当前这一屏**里找含 `needle` 的那一行的号。

        ⚠️ 号**不写死**：动词权重/排序一改，写死的号就指到别的行上去，
        测试会假红（更坏的是**假绿**——敲对了号却敲错了行）。同 ④ 那条"别把条数写死"。
        ⚠️⚠️ **找不到就抛**（2026-09-29 审查抓的测试自己的洞）：返回 None 的话
        `do_row(None, …)` 只回一句"敲个编号"，而那一堆 `not in` 的**否定断言照样为真**
        ⇒ **整批判成假绿**。测试自己先得是可信的。
        """
        no = next((r.no for r in _LAST_ROWS if needle in (r.label or "")), None)
        if no is None:
            raise AssertionError(
                f"这一屏里没有含「{needle}」的行 —— 单子是："
                + " / ".join((r.label or "?") for r in _LAST_ROWS))
        return no

    def _open_box(name, run, c):
        """点**两下**把某只箱子开出来：顶层「箱子…」→ 一览里那**一只**。

        ⚠️ 2026-09-29 箱子合一之后，顶层不再有「矿石箱(13,13)」这行了
           （恒：「箱子好多哇！…选择该项应该是接到 storage 的原有功能去」）。
        """
        reset_menu()
        render_menu(c, n=40)
        do_row(_no_of("箱子"), run, c)
        return do_row(_no_of(name), run, c)

    # ① 三档：真 / 假 / 连接级未知
    empty_tile = {"x": 12, "y": 11, "passable": True}
    ok.append(("空地（清单里没有它）→ CAN_NO", _pick_can(ctx, empty_tile) is CAN_NO))
    ok.append(("野莓 → CAN_YES", _pick_can(ctx, ctx.tiles[(13, 12)]) is CAN_YES))
    # ⚠️ 2026-10-01：判据搬到 `pickup_scene.scan_pickables()`（恒「复用原来的捡蛋工具」），
    #    **这行不再看 `caps`**（老 `forage` 那一套的"连接级 MAYBE"随之退役）——
    #    清单为空（读不到/没东西）就是 **CAN_NO**，那行不出现（宁缺勿编，方向跟老规矩一致）。
    old = Ctx(px=12, py=12, tiles={}, caps={})
    ok.append(("清单为空（老 DLL / 读不到）→ CAN_NO，不进单子",
               _pick_can(old, ctx.tiles[(13, 12)]) is CAN_NO))

    # ② 识别层：catNum 有/无
    ok.append(("catNum=-102 → 是书", is_book(ctx.inv[0]) is True))
    ok.append(("catNum 缺失 → ？(不猜)", is_book(ctx.inv[2]) is CAN_MAYBE))

    # ③ 单子是**动作面**：没接执行的动词**一个都不许上**
    # ⚠️ 这条**必须用假动词验**（2026-09-29）：原来拿真动词（捡/收作物）当反例，
    #    等它们真接上执行，断言就变成**过时的假红**。机制要用**构造出来的反例**验。
    # ⚠️ n 给足（**别写死小 n**）：夹具一长，小 n 就把后面的行挤掉 ⇒ 假红。截断由 ④ 专门测。
    probe = Verb("probe_unwired", "还没接的假动词", 1, lambda c, t: True,
                 lambda c, t: "", lambda c, t: "假动作", "tile")
    VERBS.append(probe)
    _VERB_BY_KEY["probe_unwired"] = probe
    try:
        menu = render_menu(ctx, n=40)
        ok.append(("没接执行的动词**不上单子**（机制）", "假动作" not in menu))
    finally:
        VERBS.remove(probe)
        _VERB_BY_KEY.pop("probe_unwired", None)
    ok.append(("接了的动词在单子上（收 已好的机器）", "收 已好的机器" in menu))
    # 🍽📖 2026-09-29：吃/看**接上执行了** ⇒ 手持那件（fixture 是古书）该出现
    ok.append(("接了的「看」在单子上（手持是书）", "看 古书" in menu))
    # 🌿🌾 同一天接的：捡/收作物（**聚合行**）
    ok.append(("接了的「捡」在单子上（聚合）", "捡 地上的东西" in menu))
    ok.append(("接了的「收作物」在单子上（聚合）", "收 成熟作物" in menu))
    # ⛔ 原来这儿有一条 `接了的「锄」在单子上` —— 2026-09-29 随动词退役一起**删掉**了。
    #    ⚠️ 别改成 `not in` 就完事：那是**另一条闸**（"不许回来"，在下面），
    #    混在这里会让人以为"锄本来就该在、只是今天不在"。
    ok.append(("两台同产物 → 理由栏里聚合成一条", "×2" in menu))
    ok.append(("产物摊在**理由**栏（钻石×2）", "钻石×2" in menu))
    ok.append(("单子带理由列", "←" in menu))
    ok.append(("单子留 0 出口", "做点别的" in menu))

    # ④ 铁律 2：不许静默截断
    # ⚠️ 得先有足够长的单子才测得出来 ⇒ 临时多挂几个占位动词（**用完原样还回去**）。
    # ⚠️⚠️ 2026-09-29 踩过：这里原来是"临时给 pick 挂占位 exec、用完 `pick.exec = None`"——
    #    那在 pick **本来就没接执行**时是对的；等 pick 真接上执行，那行就**把真执行器抹掉了**
    #    整个后半场的用例跟着一起错（表现出来是"捡那条行不见了"）。
    #    ⇒ **凡"临时改共享对象"的测试，必须存原值再还原**，不许写死成你以为的那个值。
    probe_verbs = []
    for i in range(4):
        pv = Verb(f"probe_pad{i}", f"占位{i}", 1, lambda c, t: True,
                  lambda c, t: "", lambda c, t: f"占位动作{i}", "tile",
                  exec=lambda c, ts, run: "（自验占位）")
        probe_verbs.append(pv)
        VERBS.append(pv)
        _VERB_BY_KEY[pv.key] = pv
    try:
        _total = len(_candidates(ctx))
        m1 = render_menu(ctx, n=1)
        ok.append(("超出 N 条如实报「还有 K 项」", "还有" in m1))
        # ⚠️ 别把条数写死——第一次写"3 个野莓 - 1 = 2"就漏算了 collect 那行也在单子上。
        #    算出来再比，尺子才跟着代码走。
        ok.append((f"报的数对得上（共 {_total} 条 - 显示 1）", f"还有 {_total - 1} 项" in m1))
        # ⚠️ 同样别写死 n：拿**算出来的条数**当尺子（夹具一变，写死的 n 就假红）
        ok.append(("没超 N 条时不该报", "还有" not in render_menu(ctx, n=_total)))
        # ⚠️ 报「还有 K 项」时**不许配一句「（more）」**——没有 `more` 这个 op，
        #    敲了回「❌ 看不懂」⇒ 那是扇**假门**（2026-09-29 真机敲出来的）。
        ok.append(("截断那行不给假门（没有 `more` 这个 op）", "more" not in m1))
        # ⚠️ 但**光报数不算完**——得说清下一步敲什么才看得见（恒：「只报'缺失N格'
        #    害 AI 干瞪眼」）。出口真存在（`show` 带 `n`），写了它 AI 才够得着。
        ok.append(("截断那行**给出下一步**（怎么看见剩下的）", 'kw={"n":' in m1))

        # ⚠️⚠️ 2026-09-29 真机洞：`show`（= `render_menu`）打在**深层**上时，
        #    原来不管在第几层都按 `n=5` 印 ⇒ 同一个单子，`do` 印全 21 条、
        #    紧接着 `show` 只剩 5 条。`show` 是 AI 最常用的动作（再看一眼），
        #    一走这条路就**看不见自己刚点开的东西**。
        render_menu(ctx, n=40)                     # 先回顶层，免得下面这层叠在别人头上
        deep = Level([Row(probe_verbs[0], [None], f"深层{i}", "", 0)
                      for i in range(20)], mode="pick")
        _push_level(deep, ctx)
        big = render_menu(ctx)                     # ← 默认那条路（n=5）
        ok.append(("深层的 show 不被截成 5 条", big.count("深层") == 20))
        ok.append(("深层没截就不该报「还有」", "还有" not in big))
        _STACK[:] = _STACK[:1]
    finally:
        for pv in probe_verbs:
            VERBS.remove(pv)
            _VERB_BY_KEY.pop(pv.key, None)

    # ⑤ 指哪打哪：接没接**都列**，但分开列（`at` 是问句不是动作面）
    # 🔴 2026-10-04 真机改口径（恒：「at 多去试试」）：原来断言「什么都没有」——
    #    可 `tile` 表里**根本没有**建筑层/水（出货箱/鱼塘/脚下那格真机全报"什么都没有"，
    #    祝福雕像还印「这里没有它能做的动作」而顶层单子上有「摸 雕像」）⇒ 这话是说世界的。
    #    ⇒ 改成断言「**没有这一格的账**」+ 不编"附近" + 指路 `show`。
    at_empty = render_at(ctx, 99, 99)
    ok.append(("指到没账的格 → 说「没有这一格的账」", "没有这一格的账" in at_empty))
    ok.append(("🔴 不许断言「那里什么都没有」+ 指路 `show`",
               "什么都没有" not in at_empty and "show" in at_empty))
    ok.append(("指到空 → 不编「附近有」", "附近" not in at_empty))
    at_bush = render_at(ctx, 13, 12)
    ok.append(("指到野莓 → 出「捡」", "捡" in at_bush))
    # ⚠️ 2026-09-29：捡**接上执行了** ⇒ 它该是"按得动的"，不再落 ⏳ 那行（原来那条断言过时了）。
    ok.append(("接了的「捡」**不在** ⏳ 那行",
               "捡" in at_bush and "还没接执行：捡" not in at_bush))
    # 「⏳ 还没接执行」是**缺口探测器**——同样用**假动词**验（真动词迟早全接上）
    gapv = Verb("probe_gap", "没接的假动作", 1, lambda c, t: True,
                lambda c, t: "", lambda c, t: "假动作", "tile")
    VERBS.append(gapv)
    _VERB_BY_KEY["probe_gap"] = gapv
    try:
        ok.append(("没接执行的动词落 ⏳ 那行（缺口探测器）",
                   "还没接执行" in render_at(ctx, 13, 12)))
    finally:
        VERBS.remove(gapv)
        _VERB_BY_KEY.pop("probe_gap", None)

    # 🚨 逃生口那屏的号**必须打在那一格上**（2026-09-29 真机照出来的洞）
    #    `render_at` 原来**不推栈** ⇒ 敲它的号会落到 `_STACK[-1]`，也就是**上一屏**的第 N 行。
    #    实测：`at 24 26`（椅子上）显示「1 坐 胡桃木椅子」，敲 `1` **去收了机器**。
    #    屏幕上有号、号指向别处 = 166③ 说好要删掉的那类静默错误动作。
    reset_menu()
    render_menu(ctx, n=40)                       # 先摆一屏"上一屏"（敲错就会打到它）
    render_at(ctx, 14, 13)                       # 木椅那格 → 该屏 1 号 = 坐
    at_calls = []

    def at_run(ep, payload):
        at_calls.append((ep, payload))
        return {"ok": True, "text": "它自己的话"}

    do_row(1, at_run, ctx)
    ok.append(("🚨 逃生口那屏的号打在**那一格**上（不是上一屏）",
               bool(at_calls) and at_calls[0][0] == "sit"))

    # ⑥ 回执必须回显对象
    rc = render_receipt("卖出", "草莓×5", True, "+600g", "背包③ 现在是 菠萝×2")
    ok.append(("回执回显对象/数量", "草莓×5" in rc))
    ok.append(("回执回显新槽位", "菠萝×2" in rc))

    # ⑦ 敲单子——**假 run**，一个字节都不碰游戏
    calls = []

    def fake_run(ep, payload):
        calls.append((ep, payload))
        return {"ok": True, "collected": 2, "skippedFull": 0}

    # ⚠️ 2026-09-29：`at x,y` 现在**会推栈**了（逃生口那屏的号必须能敲，见 `render_at`）
    #    ⇒ 上面那几发 `at` 把栈留在了"那一格"上。这儿要**回顶层**再验单子，
    #    否则 `_no_of` 找的是那一格的单子（测试自己的假红）。
    reset_menu()
    render_menu(ctx, n=40)                      # 先看一眼，才有单子可敲
    # ⚠️ 按**标签**找那一行，不写死 1 号（2026-09-29 加容器行后，1 号已经变成箱子了）
    # 🧺🔁 2026-10-01 恒拍板 (b)：这条从**动作行**（「收 已好的机器」→ 一键 `machine_collect`）
    #    换成了**目录行**（「收放…」→ 挑放什么料 → `machine_loader --here` 拟人收放）。
    #    ⇒ 用例跟着换成：**点开 → 挑料 → 打 `mwork`**（+ 挑「只收不放」那条打空 item）。
    # 🧺 2026-10-01 定形（恒：「完全撤出选项你觉得怎么样？」）：**不带参数的一行动作**。
    #    三轮改形都记在 `MACHINE_V` 上面那段 —— 这里只锁最终形状：
    #    ① 单子上**没有目录层**（放料撤出单子，走原路线 `farm load`）；
    #    ② 执行打的是 `mwork`（拟人那条），`item`/`machine_type` 都空 = 只收；
    #    ③ 理由栏必须**给出放料的出路**（警告必须带路）。
    def fake_mwork(ep, payload):
        calls.append((ep, payload))
        return {"st": "yes", "text": "🏠 收机器：收了 2 台（拟人走位）"}

    reset_menu()
    screen = render_menu(ctx, n=40)
    out_m = do_row(_no_of("收 已好的机器"), fake_mwork, ctx)
    ok.append(("🧺 单子上是**不带参数的一行动作**「收 已好的机器」（敲一下就开跑，没有目录层）",
               bool(calls) and calls[-1][0] == "mwork"
               and calls[-1][1].get("item") == "" and calls[-1][1].get("machine_type") == ""))
    ok.append(("🧺 它是**拟人**那条（`mwork` → `machine_loader --here`），"
               "不再是旧的一键 `machine_collect`",
               not any(c[0] == "machine_collect" for c in calls)))
    ok.append(("🧺 回执把脚本的原话带回来（长脚本，**不许说成收完了**）", "收了 2 台" in out_m))
    ok.append(("🧺 理由栏**给出放料的出路**（警告必须带路：`farm load` + 两个参数）",
               "farm load" in screen and "machine_type" in screen))
    # ⚠️ 越界那条要**回顶层**再敲（上面刚执行过，层级已经不是顶层了）
    reset_menu()
    render_menu(ctx, n=40)
    ok.append(("敲越界的号 → 拒绝并给出路", "at x,y" in do_row(len(_LAST_ROWS) + 5, fake_mwork, ctx)))

    # ⑧ ⚠️ 2026-10-01：回执**照原样转述脚本的话**（脚本报"没收完"时不许替它编成功）
    def warn_mwork(ep, payload):
        return {"st": "no", "text": "⚠️ 背包满了，还有 7 件没收 —— 先去卖或存，回来再敲一次"}

    reset_menu()
    render_menu(ctx, n=40)
    ok.append(("🧺 脚本报「没收完」⇒ 回执**照原样转述**（替它编成功就是老病）",
               "先去卖或存" in do_row(_no_of("收 已好的机器"), warn_mwork, ctx)))

    # ⑨ 不崩：空世界 + 没单子就敲
    render_menu(Ctx())
    ok.append(("空世界不崩", True))

    # ⑩ 敲法的**形态**规则（0928 定稿）——动作层，跟哪个动词无关。
    #    ⚠️ 2026-09-29 改：这一段的「目录行/多选/配对」原来靠一个**假买卖动词**演
    #    （`VERBS.append` + `finally: remove`，还用 `_VERB_BY_KEY.pop("buy")` 收尾）。
    #    现在真买卖在 ⑫ 上线了 ⇒ 那个夹具**必须撤**：它的收尾会把**真 "buy"** 从
    #    `_VERB_BY_KEY` 里 pop 掉 ⇒ 单子一渲染就 `KeyError: 'buy'`（真接线之后它才发作）。
    #    留下的是**跟动词无关**的那几条形状规则。
    reset_menu()
    render_menu(ctx, n=5)
    ok.append(("混着写 → 拒", "混了两种写法" in do_row("1,4=4", fake_run, ctx)))
    render_menu(ctx, n=5)
    ok.append(("act 层写数量 → 拒", "没有数量要填" in do_row("1=1", fake_run, ctx)))
    ok.append(("act 层多选 → 拒", "一次只能敲一个" in do_row("1,2", fake_run, ctx)))
    ok.append(("越界的号 → 拒并给出路", "at x,y" in do_row(97, fake_run, ctx)))

    # ⑪ 📦 容器（166 ⑤ / 2026-09-29 恒：**箱子合一**）
    #    顶层只一行「箱子…」→ 点开是**一览**（一行一箱）→ 再点才是动作面（取/存）。
    reset_menu()
    top = render_menu(ctx, n=40)
    ok.append(("📦 顶层只有**一行**箱子（不再一箱一行）",
               "箱子…" in top and "矿石箱" not in top))
    ok.append(("📦 那一行报的是**总箱数 / 总件数 / 总余格**",
               "2 个" in top and "共 39 件" in top and "空 33 格" in top))

    n_before = len(calls)
    ov = do_row(_no_of("箱子"), fake_run, ctx)
    ok.append(("📦 点开 → **一览**：一行一箱、带箱里是什么（本身什么都不做）",
               "矿石箱" in ov and "满箱" in ov and "钻石" in ov
               and len(calls) == n_before))
    # ⚠️ 一览那行的计数必须是 **`已用/容量 格`**：不给 `count_text` 会掉进
    #    `_render_level` 的兜底"下一层有几行" ⇒ 印成「2 件」（=取/存两条动作）冒充"箱里几件"。
    ok.append(("📦 一览报的是**箱里几件**（不是「点开有几条动作」）",
               "3/36 格" in ov and "2 件" not in ov))
    box = do_row(_no_of("矿石箱"), fake_run, ctx)
    ok.append(("📦 一览里再点 → 进动作面",
               "矿石箱" in box and len(calls) == n_before))
    ok.append(("动作面里有「取」", " 1  取…" in box or "取…" in box))
    ok.append(("动作面里有「存」", "存…" in box))
    ok.append(("⚠️ 没有 `chest_open` 能力位 ⇒ 「看」**不出现**（不糊弄）",
               "走过去开箱" not in box))
    # ⚠️ 逐条报的假 run：一条成、一条没成——**不许整批报成功**
    def take_run(ep, payload):
        calls.append((ep, payload))
        if ep == "chest_take":
            return {"ok": True, "taken": payload["count"] if payload["name"] == "Diamond" else 0}
        if ep == "store":
            return {"ok": True, "stored": [{"item": payload["name"], "count": payload["count"]}]}
        return {"ok": True}

    do_row(0, fake_run, ctx)                       # 0 = 这些都不是（回顶层）
    render_menu(ctx, n=40)
    _open_box("矿石箱", fake_run, ctx)
    pick = do_row(_no_of("取"), fake_run, ctx)
    ok.append(("「取」点开 → 列箱里的东西（号印在眼前）", "钻石" in pick and "翡翠" in pick))
    q = do_row("1,2", fake_run, ctx)
    ok.append(("取 多选 `1,2` → 「各多少」那层", "各多少" in q))
    done = do_row("1=2,2=7", take_run, ctx)
    ok.append(("取 配对 `1=2,2=7` 真走 `chest_take`", any(c[0] == "chest_take" for c in calls)))
    ok.append(("取 回执**逐条列**（哪条成了）", "钻石 ×2" in done and "翡翠 ×7" in done))
    ok.append(("取 回执**逐条报失败**（没成的那条不装成功）", "没取到" in done))

    # 满箱 ⇒ 「存」那条行**不出现**（不赌"能叠上去"）
    do_row(0, fake_run, ctx)
    render_menu(ctx, n=40)
    full = _open_box("满箱", fake_run, ctx)
    ok.append(("满箱（freeSlots=0）⇒ 「存」不出现", "存…" not in full))
    ok.append(("满箱仍能「取」", "取…" in full))
    ok.append(("满箱**说清下一步**（报缺了要给出路）", "箱子满了" in full and "先取点" in full))

    # 「看」：能力位在 ⇒ 那行出现、**按了真走 `chest_open`**（不是"还没接执行"）
    def open_run(ep, payload):
        calls.append((ep, payload))
        return {"ok": True}

    capok = _fixture()
    capok.caps = dict(capok.caps, chest_open=True)
    reset_menu()
    render_menu(capok, n=40)
    lv = _open_box("矿石箱", fake_run, capok)
    ok.append(("有 `chest_open` 能力位 ⇒ 「看」出现", "走过去开箱" in lv))
    calls.clear()
    op = do_row(_no_of("看（走过去开箱）"), open_run, capok)
    ok.append(("看 真走 `chest_open`（**不是**「还没接执行」）",
               any(c[0] == "chest_open" for c in calls)))
    ok.append(("看 说明白**开完不关**（恒拍板）", "不关" in op))

    # 存：背包 ∩ 容器收的 ∩ 放得下
    do_row(0, fake_run, ctx)
    render_menu(ctx, n=40)
    _open_box("矿石箱", fake_run, ctx)
    spick = do_row(_no_of("存"), fake_run, ctx)
    ok.append(("「存」点开的候选来自**背包**", "草莓" in spick))
    # ⚠️ `/store` 默认 keepTools=True 会**静默跳过工具** ⇒ 工具不进候选（否则是"按了不成"的行）
    ok.append(("工具不进「存」的候选（锄头 catNum 缺失 ⇒ 问不出 ⇒ 不列）",
               "锄头" not in spick))
    ok.append(("知道不是工具的照常列（古书 catNum=-102）", "古书" in spick))
    q2 = do_row("1", take_run, ctx)                # pick 层先选"存哪几样"
    ok.append(("存 选完 → 进「各多少」（**不是**在 pick 层直接填数量）", "各多少" in q2))
    calls.clear()
    sdone = do_row("1=3", take_run, ctx)           # qty 层：号=量
    ok.append(("存 配对 `1=3` 真走 `/store`", any(c[0] == "store" for c in calls)))
    ok.append(("存 回执回显物品和数量", "×3" in sdone))

    # ⑫ 🍽📖 吃 / 看：**先 select 再动手**（`/eat`、`/use mode=read` 都不认名字，吃的是 CurrentItem）
    def eat_run(ep, payload):
        calls.append((ep, payload))
        if ep == "eat":
            return {"ok": True, "ate": "Strawberry", "health": 176, "stamina": 288}
        return {"ok": True}

    eatctx = _fixture()
    eatctx.held = eatctx.inv[1]                    # 草莓（edibleValue=20）
    reset_menu()
    ok.append(("手持草莓 ⇒ 「吃 草莓」在单子上", "吃 草莓" in render_menu(eatctx, n=40)))
    calls.clear()
    rc = do_row(_no_of("吃 草莓"), eat_run, eatctx)
    ok.append(("吃 **先 select**（锁到单子上那一件）", bool(calls) and calls[0][0] == "select"))
    ok.append(("吃 再 eat", any(c[0] == "eat" for c in calls)))
    ok.append(("吃 回执回显对象 + **游戏回**的体力/血", "草莓" in rc and "288" in rc and "176" in rc))

    def read_fail_run(ep, payload):
        calls.append((ep, payload))
        if ep == "use":
            return {"ok": False, "error": "读取没反应（可能已读过/或该物品不能读）"}
        return {"ok": True}

    reset_menu()
    render_menu(ctx, n=40)                          # fixture 手持 = 古书
    calls.clear()
    rc2 = do_row(_no_of("看 古书"), read_fail_run, ctx)
    ok.append(("看 走 `use mode=read`",
               any(c[0] == "use" and c[1].get("mode") == "read" for c in calls)))
    ok.append(("看 **读没读得了由回执如实报**（不装成读了）", "没反应" in rc2))

    # ⑬ 回归：手持判据必须是 `currentItem` —— 书/食物不是 Tool，`currentTool` 恒 null
    st = {"player": {"x": 12, "y": 12, "maxItems": 36,
                     "currentItem": "Book", "currentItemId": "(O)Book", "currentTool": None},
          "inventory": [{"slotIndex": 2, "name": "Book", "displayName": "古书",
                         "itemId": "(O)Book", "catNum": BOOK_CAT, "stack": 1}]}
    c2 = ctx_from(st, {}, None, None)
    ok.append(("手持走 `currentItem`（书不是 Tool ⇒ 原判据恒空手）", c2.held is not None))
    ok.append(("手持按 `currentItemId` 精确匹配", (c2.held or {}).get("name") == "古书"))
    ok.append(("背包容量取 `maxItems`（**不写死 36**）", c2.max_items == 36))
    st2 = {"player": {"x": 12, "y": 12, "currentTool": "Axe"},
           "inventory": [{"slotIndex": 0, "name": "Axe", "displayName": "斧头"}]}
    ok.append(("只有 `currentTool` 时**不兜底**（宁可没有手持，不猜）",
               ctx_from(st2, {}, None, None).held is None))

    # ⑭ 三档：容量/空位拿不到 ⇒ `None` ⇒ **那条行不出现**（宁缺勿编）
    ok.append(("空位字段缺失 → CAN_MAYBE", _box_space({}) is CAN_MAYBE))
    ok.append(("背包容量缺失 → CAN_MAYBE", _pack_space(Ctx()) is CAN_MAYBE))
    ok.append(("空位 0 → CAN_NO", _box_space({"freeSlots": 0}) is CAN_NO))
    nospace = _fixture()
    nospace.max_items = 0                          # 老 DLL：算不出背包容量
    reset_menu()
    render_menu(nospace, n=40)
    lv0 = _open_box("矿石箱", fake_run, nospace)
    ok.append(("背包容量**算不出** ⇒ 「取」不出现（第三档同「不」）", "取…" not in lv0))
    # ⚠️ 但"算不出"**不解释**（那是连接级的事）；只有"算得出装不下"才给一句+下一步
    ok.append(("算不出 ⇒ **不编解释**", "背包满了" not in lv0))

    fullbag = _fixture()
    fullbag.max_items = len(fullbag.inv)           # 背包**满了**（算得出）
    reset_menu()
    render_menu(fullbag, n=40)
    lv1 = _open_box("矿石箱", fake_run, fullbag)
    ok.append(("背包满（算得出）⇒ 「取」不出现", "取…" not in lv1))
    ok.append(("背包满 ⇒ 说清下一步", "背包满了" in lv1 and "先卖或存" in lv1))

    # ⑮ 2026-09-29 审查抓出来的洞——**每一条都钉一条断言**（不钉就是修了个寂寞）
    # (a) ⚠️ 子层被"再看一眼"砍掉 ⇒ 号会悄悄换意思
    reset_menu()
    render_menu(ctx, n=40)
    _open_box("矿石箱", fake_run, ctx)
    again = render_menu(ctx, n=40)                  # 模拟 AI 又看一眼单子
    ok.append(("⚠️ 再看一眼单子**仍停在子层**（号不换意思）", "取…" in again))

    # (b) ⚠️ `/select` 没锁上 ⇒ **不许接着吃/读**（否则吃掉手上那件别的）
    def sel_fail_run(ep, payload):
        calls.append((ep, payload))
        if ep == "select":
            return {"ok": False, "error": "家里没有「Strawberry」"}
        return {"ok": True, "ate": "Pale Ale", "health": 1, "stamina": 1}

    reset_menu()
    render_menu(eatctx, n=40)
    calls.clear()
    r_sel = do_row(_no_of("吃 草莓"), sel_fail_run, eatctx)
    ok.append(("⚠️ select 没锁上 ⇒ **不动手**（不吃错东西）",
               not any(c[0] == "eat" for c in calls)))
    ok.append(("select 失败 ⇒ 如实报「没选中」", "没选中" in r_sel))

    # (c) `/select` 的品质：缺就是"不限"(-1)，**不是** 0（0 = 硬筛"只要普通品质"）
    ok.append(("品质缺 → 传 -1（不限），不传 0（硬筛）",
               _quality_of({"quality": None}) == -1 and _quality_of({"quality": 2}) == 2))

    # (d) 作物真名的键是 **`cropName`**（`crop` 是产物 item ID，印出来是个数字）
    ok.append(("作物名走 `cropName`（不是印 ID）", "萝卜" in render_at(ctx, 12, 13)))

    # (e) 吃那一行**不重复印「手持」**（`_where()` 已经印过一次）
    ok.append(("吃 的理由栏不重复印「手持」", "手持 手持" not in render_menu(eatctx, n=40)))

    # 🪑 195c（2026-10-02）：恒「**坐可以放在不靠上的位置**」+「场景里没有可以坐的地方就不显示」——
    #    ① 权重压到 26；② 判据仍是**逐格**的（`_sit_can`）⇒ 本图没座位 / 座位被占都**不出现**。
    ok.append(("🪑 195c：`坐` 的权重 = 26（明显不靠上）", _VERB_BY_KEY["sit"].weight == 26))
    _noseat = _fixture()
    _noseat.tiles = {k: v for k, v in _noseat.tiles.items() if "seat" not in v}
    reset_menu()
    ok.append(("🪑 195c：本图**一个座位都没有** ⇒ 单子上没有「坐」",
               "坐 " not in render_menu(_noseat, n=40)))
    _busy = _fixture()
    _busy.tiles[(14, 13)]["seat"] = dict(_busy.tiles[(14, 13)]["seat"], free=0)
    reset_menu()
    ok.append(("🪑 195c：座位**被占**（free=0）⇒ 也不出现", "坐 " not in render_menu(_busy, n=40)))

    # 🗂 197（2026-10-02）：**跨组排序** —— 口径已按恒的拍板改成 **B：组＝块，块按"组内最高权重"
    #    参与全局排序**（恒原话「**坐可以放在不靠上的位置**」；不选"全局权重序"是因为那样会把组打散、
    #    组头就骗人了）。这几条**钉 B**：`坐`(26) 沉到块的位置（`看 古书` 40 之后）。
    #    ⚠️ 这几条会**第一个红**地拦下"再动排序口径"的人 —— 改它们＝改口径，得先问恒。
    _grp = _fixture()          # 设备 = mwork 88 / chest 80 · 家具 = sit 26 · 未分组 = pick 90 / harvest 88 / pets 87 / pet 84 / read 40
    reset_menu()
    render_menu(_grp, n=40)
    _gkeys = [r.verb.key for r in _LAST_ROWS]
    _gw = [_weight_of(r.verb, _grp) for r in _LAST_ROWS]
    ok.append(("🗂 197B：**比块最高还重的未分组行**排最前（pick 90 > 设备块 88）",
               _gkeys[:1] == ["pick"]))
    ok.append(("🗂 197B：接着是**设备块整块**（组内 88→80）",
               _gkeys[1:3] == ["mwork", "chest"]))
    ok.append(("🗂 197B：然后按权重轮到未分组行（harvest 88 → pets 87 → pet 84 → read 40）",
               _gkeys[3:7] == ["harvest", "pets", "pet", "read"]))
    ok.append(("🗂 197B：**家具块（坐 26）沉到它自己该在的位置**（`看 古书` 40 之后，全屏最后）",
               _gkeys[-1] == "sit" and _gw[-1] == 26))
    ok.append(("🗂 197B：口径 B 下**组是连续的**（设备那两条挨着 ⇒ 组头一块只印一次、不骗人）",
               _gkeys[1:3] == ["mwork", "chest"]))

    # 👕 2026-10-02 **「穿戴」已撤出单子**（恒拍板：权重最低 ⇒ 空场景里常驻）——
    #    这一段原来有 9 条"目录行/子层/穿传内部名/脱传槽名/两种 `/worn` 形状"的用例，
    #    整套随功能一起删；现在改钉**撤干净没留半截**：
    #    ① 顶层没有「穿戴」；② 那两个子动词**也不在** `_VERB_BY_KEY` 里（防"撤了行没撤动词"）；
    #    ③ `WEARABLE_CATS` 也不再被任何动词引用（只留常数，不再有消费方）。
    _gone_ctx = _fixture()
    _gone_ctx.inv = scan_backpack({"inventory": [
        {"slotIndex": 2, "name": "Straw Hat", "displayName": "草帽", "catNum": -95, "stack": 1},
        {"slotIndex": 3, "name": "Cowboy Boots", "displayName": "牛仔靴", "catNum": -97,
         "stack": 1}]})
    _gone_ctx.worn = {"hat": "草帽", "shirt": "蓝衬衫"}
    reset_menu()
    _gone_txt = render_menu(_gone_ctx, n=40)
    ok.append(("👕⛔ 撤干净：单子上**没有「穿戴」**（身上有 2 件、背包能穿 2 件也不出现）",
               "穿戴" not in _gone_txt))
    ok.append(("👕⛔ 撤干净：子动词 `wear_on` / `wear_off` **也不在动词表**里",
               "wear_on" not in _VERB_BY_KEY and "wear_off" not in _VERB_BY_KEY
               and "wear" not in _VERB_BY_KEY))
    ok.append(("👕⛔ 撤干净：整份 VERBS 里**没有** `_wear_*` 的引用（不留半截）",
               not [v for v in VERBS if "wear" in (v.key or "").lower()]))

    # (f) 同名两摞：**端点只按名字认** ⇒ 认不出的不列，且如实说（铁律 2）
    dupctx = _fixture()
    dupctx.tiles[(13, 13)]["chest"]["items"].append(
        {"name": "Diamond", "displayName": "钻石", "count": 5, "qualifiedId": "(O)72"})
    reset_menu()
    render_menu(dupctx, n=40)
    lv_dup = _open_box("矿石箱", fake_run, dupctx)
    ok.append(("同名两摞 ⇒ **如实说**挑出去了几摞", "同名但不同品质" in lv_dup))
    ok.append(("同名两摞确实没进候选", "钻石" not in do_row(_no_of("取"), fake_run, dupctx)))

    # (f2) 🆕 2026-09-30(178)：这版 DLL 给了**箱子里的真实格号** ⇒ 同名两摞**分得开了**：
    #      ① 该**列出来**（不再挑出去）；② 按下去**必须真的按格号指** ——
    #      光"列出来"不指格号，跟以前一样还是拿错那一摞（**同一个意图只落地一半** = 本项目老账）。
    slotctx = _fixture()
    slotctx.caps = dict(slotctx.caps, scan_chests_item_slot=True, store_slot_quality=True)
    slotctx.tiles[(13, 13)]["chest"]["items"] = [
        {"name": "Diamond", "displayName": "钻石", "count": 5, "qualifiedId": "(O)72",
         "slot": 0, "quality": 0},
        {"name": "Diamond", "displayName": "钻石", "count": 2, "qualifiedId": "(O)72",
         "slot": 7, "quality": 2},
    ]
    reset_menu()
    render_menu(slotctx, n=40)
    lv_slot = _open_box("矿石箱", fake_run, slotctx)
    ok.append(("有格号 ⇒ 同名两摞**不再挑出去**（那句「没列出来」不该再出现）",
               "同名但不同品质" not in lv_slot))
    calls.clear()
    _pick = do_row(_no_of("取"), fake_run, slotctx)
    # 🆕 2026-09-30(178) 真机：格号能指准了，但三行印成**一模一样的「啤酒花」** ⇒ AI 看不出哪摞是哪摞。
    ok.append(("候选行带**星级前缀** ⇒ 同名两摞一眼分得开",
               "[金]钻石" in _pick and "钻石" in _pick and _pick.count("钻石") == 2))
    ok.append(("无品质的**不加前缀**（别给普通也扣个帽子）", "[金]钻石" in _pick and "[银]钻石" not in _pick))
    reset_menu()
    render_menu(slotctx, n=40)
    _open_box("矿石箱", fake_run, slotctx)
    do_row(_no_of("取"), fake_run, slotctx)
    do_row("1,2", fake_run, slotctx)
    do_row("1=5,2=2", take_run, slotctx)
    sent = [p for ep, p in calls if ep == "chest_take"]
    ok.append(("取：同名两摞**各按自己的格号**指（slot 0/7 + 星级一起给、名字也一并给）",
               len(sent) == 2 and {p.get("slot") for p in sent} == {0, 7}
               and {p.get("quality") for p in sent} == {0, 2}
               and all(p.get("name") == "Diamond" for p in sent)))

    # (f3) 存的同名两摞同理：有 `store_slot_quality` ⇒ 列出来**并带背包格号** `idx`
    calls.clear()
    do_row(0, fake_run, slotctx)
    render_menu(slotctx, n=40)
    _open_box("矿石箱", fake_run, slotctx)
    do_row(_no_of("存"), fake_run, slotctx)
    do_row("1", fake_run, slotctx)
    do_row("1=1", take_run, slotctx)
    sents = [p for ep, p in calls if ep == "store"]
    ok.append(("存：带上**背包格号** `slot` + `quality`（缺字段时给 -1「不限」，绝不拿 0 硬筛）",
               len(sents) == 1 and isinstance(sents[0].get("slot"), int)
               and isinstance(sents[0].get("quality"), int)))

    # (g) 取少了：**只报差额，不替游戏编原因**（装不下也会少给，不只是"箱里没有"）
    def short_run(ep, payload):
        calls.append((ep, payload))
        if ep == "chest_take":
            return {"ok": True, "taken": max(0, payload["count"] - 3)}
        return {"ok": True}

    reset_menu()
    render_menu(ctx, n=40)
    _open_box("矿石箱", fake_run, ctx)
    do_row(_no_of("取"), fake_run, ctx)
    do_row("1", fake_run, ctx)
    dshort = do_row("1=5", short_run, ctx)
    ok.append(("取少了 ⇒ 报差额", "到手 2 个" in dshort))
    ok.append(("取少了 ⇒ **不编原因**（不再写死「箱里只有」）", "箱里只有" not in dshort))

    # (h) 字段缺 ⇒ **一个字都不印**，不把 `None/None` 摆给 AI
    badbox = _fixture()
    badbox.tiles[(13, 13)]["chest"].pop("used")
    badbox.tiles[(13, 13)]["chest"].pop("capacity")
    reset_menu()
    render_menu(badbox, n=40)
    lv_none = _open_box("矿石箱", fake_run, badbox)
    ok.append(("字段缺 ⇒ 不印 `None`", "None" not in lv_none))

    # ⑯ 2026-09-29 接线：坐 / 搬家具 / 摸（agent 说的"全转接个大概"）
    def act_run(ep, payload):
        calls.append((ep, payload))
        return {"ok": True, "text": f"{ep} 走过了（这是它自己的话）"}

    reset_menu()
    top2 = render_menu(ctx, n=40)
    ok.append(("🪑 「坐 木椅」在单子上", "坐 木椅" in top2))
    # ⛔ 🛋 2026-10-02：原来这条验"顶层只有一行「搬走…」"（恒 2026-09-29 拍板"移动家具做单行"）。
    #    行已撤 ⇒ 改钉撤干净：**顶层和下一层都不再有「搬走」**。
    ok.append(("🛋⛔ 撤干净：家具摆在屋里也不再出「搬走」",
               "搬走" not in top2))
    ok.append(("🐾 摸动物只数**只算没摸过的**（2 头里 1 头摸过了）",
               "摸 还没摸的动物" in top2 and "1 只" in top2))
    ok.append(("🐾 猫狗那一行也在", "摸 猫狗" in top2))
    # ⚠️ **情境动词（`target="world"`）不许印「手持」**（2026-09-29 真机照出来）：
    #    它的目标**就是 `None`**，走 `_where(None)` ⇒ 印成「手持 20 只（Rabbit×2…）」，
    #    牛成了拿在手里的。`held` 动词印「手持」是对的（那层 `None` 就是手持那件）
    #    —— 判据得看 `verb.target`，不能看"目标是不是 None"（两者都是 None）。
    pet_line = next((l for l in top2.splitlines() if "摸 还没摸的动物" in l), "")
    ok.append(("🐾 情境动词那行不写「手持」", "手持" not in pet_line))

    # 🛏 床（2026-09-29 恒：「床的重要性比其他家具大得多，**没有办法放在交互家具的选项里**」）。
    #    ⚠️ **只有「躺一下」**：`睡觉` **故意不进单子** —— 它的 `who` 是"**去哪儿**"不是"点哪个"
    #       （不在那栋屋会跨图走过去），而菜单的号是**眼前那一格的号** ⇒ 两个坐标系；
    #       而且单子一列就等于**替 AI 把"今晚睡谁家"这个意图先答了**
    #       （恒：「不然肯定往自己家钻」）；姜岛的 `who` 更是另一套语义（大通铺 = "挤到谁床上"）。
    #       ⇒ 过夜走原路线 `daily sleep who=…`，**AI 自己带着意图**去调（想睡恒的床也点得到）。
    bedctx = _fixture()
    bedctx.tiles[(20, 20)] = {"x": 20, "y": 20,
                              "bed": {"x": 20, "y": 20, "owner": "轮回"},
                              "furniture": {"name": "蓝白条纹双人床", "x": 20, "y": 20,
                                            "width": 3, "height": 3, "furnitureType": 15}}
    bedctx.time = "13:20"
    reset_menu()
    bm = render_menu(bedctx, n=40)
    ok.append(("🛏 本场景有床 ⇒ 出「躺一下」", "躺一下" in bm))
    # 🌙 **夜里整行不给**（恒 2026-09-30：「**晚上不出现就好了，不需要踹**」）。
    #    ⚠️ 必须拦在 `can()` —— 夜里把它摆上去、按了却拒绝，正好踩菜单第一条
    #       「**出现的那条按了就成**」；而且夜里体力低时它还会被顶到**第一行**。
    #    ⚠️ 20:00 是**闭区间**（`>=`）· 钟读不出来 ⇒ **不给**（算不出 ⇒ 不出现，同铁律）。
    for _t, _want in (("13:20", True), ("19:59", True), ("20:00", False),
                      ("23:10", False), ("", False)):
        _c = _fixture()
        _c.tiles[(20, 20)] = bedctx.tiles[(20, 20)]
        _c.time = _t
        reset_menu()
        _shown = "躺一下" in render_menu(_c, n=40)
        ok.append((f"🌙 钟「{_t or '读不出'}」⇒ 躺一下{'在' if _want else '**不在**'}单子上",
                   _shown is _want))
    ok.append(("⛔ **单子上没有「睡觉」**（它该走原路线 `daily sleep who=…`）", "睡觉" not in bm))
    ok.append(("🛏 说的是**谁的床**（`lie_bed` 吃 who 不吃坐标）", "轮回的床" in bm))
    ok.append(("🛏 理由栏写清**不过夜**（跟过夜后果天差地别，别让 AI 猜）", "不过夜" in bm))
    # 🛏 恒 2026-09-29：「算了，**不设目标了。给它当前百分比了，够不够它自己看着办**。」
    bedctx.stamina, bedctx.max_stamina = 118, 474
    bedctx.health, bedctx.max_health = 96, 180
    reset_menu()
    bm = render_menu(bedctx, n=40)
    ok.append(("🛏 给**当前百分比**（体力 25% / 血 53%）",
               "体力 25%" in bm and "血 53%" in bm))
    # 🛏 说清**还差多久** —— ⚠️ 按**当前缺口**算，不是按上限算（2026-10-01 真机照出来的：
    #    旧式子是 `max_stamina/120`（从空到满的**容量**），体力 100% 时照样印「回满约 4 分钟」）。
    #    这里 fixture = 体力 118/474、血 96/180 ⇒ 缺口取大的那个：474-118=356
    #    ⇒ ceil(356/120) = 3 分钟。
    ok.append(("🛏 说清**还差多久回满**（缺口 356 ÷ 120 ⇒ 3 分钟）", "还差约 3 分钟" in bm))
    # 🛏 **已经满了就说满了** —— 那一刻躺下去确实没用，别拿"还差 4 分钟"骗它按。
    _full = _fixture()
    _full.tiles[(20, 20)] = bedctx.tiles[(20, 20)]
    _full.time = bedctx.time          # ⚠️ 必须给白天钟点：读不出钟 = 当夜里 ⇒ 那行**整条不出现**
    _full.stamina, _full.max_stamina = 474, 474
    _full.health, _full.max_health = 180, 180
    reset_menu()
    _fm = render_menu(_full, n=40)
    ok.append(("🛏 体力血都满 ⇒ 直说「已经满了」（不印「还差 N 分钟」）",
               "已经满了" in _fm and "还差" not in _fm))
    # ⚠️ 上限读不到 ⇒ **一个字都不提时间**（不猜；同 `_pct` 的 `?` 那条规矩）
    _unk = _fixture()
    _unk.tiles[(20, 20)] = bedctx.tiles[(20, 20)]
    _unk.time = bedctx.time
    _unk.max_stamina = 0
    reset_menu()
    ok.append(("🛏 上限读不到 ⇒ 不提时间（不猜）",
               "分钟" not in render_menu(_unk, n=40)))
    # 🛏 恒 2026-10-01：「「这是躺不是睡」后面加「睡请/sleep」了吗，**不然莫名其妙警告它
    #    它还以为自己做出了**」⇒ **警告必须带路**：光说"不过夜"是个没出口的否定。
    ok.append(("🛏 「不过夜」后面**必须给出路**（`daily sleep`），不能只警告",
               "不过夜" in bm and "daily sleep" in bm))
    # 🛏 恒 2026-10-01：「**回满我叫你**——叫醒是异步 wake 的事，谁能在回满时叫醒它」
    #    ⇒ 唤醒是 `_bg_block_until_wake` 的机制，**不该由一个动作行来许诺**。两句都删。
    ok.append(("🛏 **不许再许诺「我叫你」**（唤醒是异步机制的事，不是这一行的功劳）",
               "叫你" not in bm and "叫我" not in bm))
    # ⚠️ 上限读不出来时说「不知道」，**不许印 0%**（那是拿错尺子，同 `/state` 那条老病）。
    ok.append(("🛏 上限读不出来 ⇒ `?` 不是 `0%`", _pct(10, 0) == "?"))
    # 🛏 **低血/低体力 ⇒ 抬权重**（恒：「能不能是低 hp/体力的时候，权重提高？」）。
    #    游戏代码背书（`Farmer.cs:7637`：躺床格上、联机、时间在走 ⇒ 每 500ms 体力+1、血+1）。
    def _lw(stam, ms, hp, mh):
        c = _fixture()
        c.stamina, c.max_stamina, c.health, c.max_health = stam, ms, hp, mh
        return _lie_weight(c)
    ok.append(("🛏 平时**跟别的家具差不多**（不顶在最前）", _lw(400, 474, 180, 180) == 68))
    ok.append(("🛏 **体力低 ⇒ 抬到最前**", _lw(100, 474, 180, 180) == 96))
    ok.append(("🛏 **血低 ⇒ 也抬**", _lw(400, 474, 40, 180) == 96))
    # ⚠️ 边界两侧都钉住（474×0.3 = 142.2 ⇒ 142 在下、143 在上）——
    #    只钉一侧的话，判据写成 `<=` 也照样绿。
    ok.append(("🛏 边界：**差一点就抬**（142/474 < 三成）", _lw(142, 474, 180, 180) == 96))
    ok.append(("🛏 边界：**刚好过三成不抬**（143/474）", _lw(143, 474, 180, 180) == 68))
    ok.append(("🛏 上限读不出来（0）**不误判**成低", _lw(400, 0, 180, 0) == 68))
    # ⚠️ 抬权重**得真能把组越过去**（`_apply_groups` 原来把"不进组"的一律垫底）。
    lowctx = _fixture()
    lowctx.tiles[(20, 20)] = bedctx.tiles[(20, 20)]
    lowctx.stamina, lowctx.max_stamina = 50, 474
    lowctx.time = "13:20"          # ⚠️ 得是白天：钟读不出/过了 20:00 ⇒ 那行压根不给（见上面那条闸）
    reset_menu()
    lowm = render_menu(lowctx, n=40)
    # ⚠️ **2026-09-30 按恒的拍板改了这条**（不是回归）：恒说「ai 饿扁扁或者快死的时候，
    #    **把吃食物的权重提到最前**」⇒ 资源见底时 `吃`(99) 压过 `躺一下`(96)，
    #    第一行由 `吃` 占。原来只钉"躺一下 在第一行"，新决定一来它必然红 ——
    #    那是**产品按他要求改了、测试没跟上**（老病），所以这儿改钉**两者都越过了分组**。
    _nums = [l for l in lowm.splitlines() if l.strip()[:1].isdigit()]
    _top2 = _nums[:2]
    ok.append(("🍽🛏 资源见底时「吃」(99) 压过「躺一下」(96)、**两者都排到分组之前**",
               len(_top2) == 2 and "吃" in _top2[0] and "躺一下" in _top2[1]))
    # 🆕 2026-09-30：**平时只列手持那件**能吃的 —— "把背包里所有食物都摆上单子"是
    #    **资源见底才开的闸**（否则背包里几份干粮就把第一屏占了）。
    norm = _fixture()          # held = 古书（不是吃的）、背包里有草莓（能吃）
    reset_menu()
    _nm = render_menu(norm, n=40)
    ok.append(("🍽 平时（资源够）**不列**背包里没拿在手上的食物", "吃 草莓" not in _nm))

    # 🆕 2026-09-30 恒拍板（⑦）：**满包时把「箱子…」抬到"收机器"之上 + 理由点明**。
    ok.append(("📦 平时容器行权重 80（**故意压在收机器 88 之下**）", _chest_weight(_fixture()) == 80))
    _full = _fixture()
    _full.inv = list(_full.inv) * 12          # 3×12 = 36 = max_items ⇒ 满
    ok.append(("📦 **满包 ⇒ 抬到 90**（压过「收放」88）", _chest_weight(_full) == 90))
    reset_menu()
    _fm = render_menu(_full, n=40)
    ok.append(("📦 满包时理由栏**点明**「背着满了」（状态条和单子是两张屏，别指望 AI 自己串）",
               "背着满了" in _fm))
    ok.append(("📦 平时**不点**这句（别没事喊狼来了）",
               "背着满了" not in render_menu(_fixture(), n=40)))
    # ⛔ 🛋 2026-10-02 **「搬走」撤出单子** ⇒ 原来这一段（床/儿童床不许进搬走 + 非床家具照旧给）
    #    连同 `_pickup_can` 一起删了 —— 那是**单子那层**的过滤，域工具那条路本来就不筛。
    #    改钉"撤干净、且屋里全是家具时单子也不再出现那一行"：
    ok.append(("🛋⛔ 撤干净：`pickup_f` / `pickup_one` 都不在动词表里",
               "pickup_f" not in _VERB_BY_KEY and "pickup_one" not in _VERB_BY_KEY))
    reset_menu()
    ok.append(("🛋⛔ 一屋子家具（含普通椅子/床）时，单子上**没有「搬走」**",
               "搬走" not in render_menu(bedctx, n=40)))
    # 🛏 敲下去要带**对的那个 who**（不带 who = 躺错床/报错）。
    calls.clear()
    reset_menu()
    render_menu(bedctx, n=40)
    do_row(_no_of("躺一下"), act_run, bedctx)
    ok.append(("🛏 躺一下 走 `lie_bed` 且 `who` 是床边那个人",
               any(c[0] == "lie_bed" and c[1].get("who") == "轮回" for c in calls)))
    # 🕐 钟点（2026-09-29 真机当场照出来的）：
    # ⚠️⚠️ `/state` 的 `time` **是字典不是字符串**（`{"timeOfDay": 1320, …}`）。
    #    我原来写 `str(state["time"])` ⇒ 理由栏印出 `现在 {'timeOfDay': 1320, …}`；
    #    **更坏的是它不报错** —— 解析失败是**静默**的（当时用来喂"夜里才顶上去"的判据，
    #    恒回"白天" ⇒ 那条规则永远不生效，而屏上一切正常）。同族坑：`/state` 瘦 `/menu` 详。
    # ⚠️ 这条**必须走 `ctx_from`**（喂真的 `/state` 形状）—— 夹具里直接 `ctx.time = "22:10"`
    #    会**绕过**这段转换，那正是真机上漏掉它的原因。
    ok.append(("🕐 `ctx_from` 从 `/state` 的**字典**里取出钟点",
               ctx_from({"time": {"timeOfDay": 1320}, "player": {}, "location": {}}, {}).time == "13:20"))
    reset_menu()
    render_menu(ctx, n=40)
    # ⚡⚡ **`×N` 只许给"真会全做"的动词**（2026-09-29 真机：`锄地 ×185` 按下去
    #    只锄了 1 格，世界实查 `(50,11)` `Grass`→`HoeDirt`、邻居没动、回执 `1/1 锄出`）。
    #    `×N` 在单子上的语义是"这一按会把 N 个都做了"——只吃 `targets[0]` 的动词印它 = **假承诺**。
    #    ⚠️ 那天照出来的两个罪犯一个被修（锄）、一个被**删**（锄整行退役）⇒ 现在只剩 `sit`
    #    一个 `batch=False` 的多目标样本了，就拿它当闸（**别让闸跟着动词一起消失**）。
    ctxs = _fixture()
    ctxs.tiles[(15, 13)] = {"terrain": "Wood", "seat": {
        "kind": "furniture", "name": "木椅", "x": 15, "y": 13, "capacity": 1, "free": 1}}
    reset_menu()
    sm = render_menu(ctxs, n=40)
    sline = next((l for l in sm.splitlines() if "坐 木椅" in l), "")
    ok.append(("🪑 两把同名椅子**不印 `坐 木椅 ×2`**（人只能坐一张）", "×" not in sline))
    ok.append(("🪑 说清只坐一张", "一次只做其中一个" in sline))
    # 🪑 2026-10-04 恒：「**把坐合成一下**……做**同一个选项的第二层选择题**」——
    #    两种以上座位名 ⇒ 顶层只留一行目录行「坐…（N 处）」，点开才是各把椅子。
    #    （只有一种时不合成、照样直接给那一行 —— 见 `_sit_one_can` 的注释。）
    ctxd = _fixture()
    ctxd.tiles[(16, 13)] = {"terrain": "Wood", "seat": {
        "kind": "furniture", "name": "红色餐椅", "x": 16, "y": 13, "capacity": 1, "free": 1}}
    reset_menu()
    dm = render_menu(ctxd, n=40)
    ok.append(("🪑 两种座位名 ⇒ 顶层合成**一行目录行**（句尾 `…`）",
               "坐…" in dm and "坐 木椅" not in dm and "坐 红色餐椅" not in dm))
    ok.append(("🪑 目录行报「几处能坐」", "处" in dm))
    _dno = next((r.no for r in _LAST_ROWS if r.verb.key == "sit_pick"), None)
    _dsub = do_row(str(_dno), lambda *a, **k: {}, ctxd) if _dno else ""
    ok.append(("🪑 点开 ⇒ 第二层把**各把椅子**都列出来",
               "坐 木椅" in _dsub and "坐 红色餐椅" in _dsub))
    # 反面闸：**真会全做**的动词（捡/收机器）照旧要印 `×N` —— 别一刀切。
    ok.append(("🌿 反面：捡**照旧**印 `×N`（它真的一片全捡）", "捡 地上的东西 ×3" in top2))
    # ⛔ **「锄」不许回来**（2026-09-29 恒：「LLM 有多条路可以走的时候，就有走偏的可能」）。
    #    需要 AI 参与规划的（整块地/挖斑点）让它自己调原路线：`farm till` / `scene spot`。
    ok.append(("⛔ 单子上**没有**「锄」（它该走农活域，别给多一条路）",
               "锄" not in top2 and "锄" not in sm))
    reset_menu()
    render_menu(ctx, n=40)
    # ⚠️ 空 `reason` 不许在尾巴留一个**光秃秃的 `·`**（`.rstrip()` 只吃空白，吃不掉它）。
    # ⚠️⚠️ **夹具里所有多目标行的 reason 都非空** ⇒ 光靠 `top2` 这条闸**根本红不了**
    #    （A/B 当场照出来的：把代码改回 `.rstrip()`，它照样绿 = 白写的闸）。
    #    ⇒ 自己造一行**空 reason 的多目标行**，那条支路才真的被走到。
    ok.append(("现有那一屏没有以光秃秃的 `·` 结尾的行",
               not [l for l in top2.splitlines() if l.rstrip().endswith("·")]))
    _push_level(Level([Row(VERBS[0], [{"x": 1, "y": 1}, {"x": 2, "y": 2}],
                           "空理由行", "", 5)], mode="act"), ctx)
    d_empty = _render_level(ctx, _STACK[-1], 40)
    ok.append(("空 `reason` 的多目标行**不留**光秃秃的 `·`",
               not [l for l in d_empty.splitlines() if l.rstrip().endswith("·")]))
    _STACK[:] = _STACK[:1]
    reset_menu()
    # 🍽 反面：**手持**动词（吃/看）照旧要写「手持」——别为了修上面那条把 loc 一律砍掉。
    #    ⚠️ 这行会改 `_LAST_ROWS` ⇒ **用完必须把 ctx 那一屏渲染回来**，否则下面
    #    `_no_of("坐 木椅")` 会在别人的单子上找号（`do_row` 打的是 `_LAST_ROWS`）。
    ok.append(("🍽 手持动词照旧写「手持」（是它才该写）", "手持" in render_menu(eatctx, n=40)))
    reset_menu()
    render_menu(ctx, n=40)

    calls.clear()
    r_sit = do_row(_no_of("坐 木椅"), act_run, ctx)
    ok.append(("坐 走 `sit`（高阶层，带读回验证）",
               any(c[0] == "sit" for c in calls)))
    ok.append(("坐 的回执**用它自己的话**（不重拼）", "它自己的话" in r_sit))

    # ⛔ 🛋 2026-10-02：原来「搬走」点开/下一层/搬家具执行那 5 条用例，随功能一起删。
    #    留一条**承接**它们真正守护的东西：头一个字跟正文同档（`_receipt_from_helper` 那套
    #    是**所有**动词共用的，不只搬家具）——所以这条挪到这儿、换一个动词的壳来验。
    # ⚠️⚠️ **头一个字的档位必须跟正文一致**（2026-09-29 真机抓的活标本：
    #    `✅ 搬走家具 蓝白条纹双人床` 配着正文「…**没拿起来**…**物品没动。**」
    #    —— 同一屏自己打自己）。根因：`_im_run` 的 `ok` **只认开头的 `❌`**，
    #    而工具的话有三档（`❌` 确定没成 / `⚠️` 没成或存疑 / 其余=成）。
    r_maybe = _receipt_from_helper("搬走家具", "蓝白条纹双人床",
                                   {"ok": True, "st": "maybe",
                                    "text": "⚠️ 「床」没拿起来。**物品没动。**"})
    m_head = r_maybe.splitlines()[0]
    ok.append(("⚠️ 正文说没成 ⇒ 头一行**不许是 ✅**", not m_head.startswith("✅")))
    ok.append(("⚠️ 头一行**跟正文同档**（⚠️）", m_head.startswith("⚠️")))
    ok.append(("⚠️ 正文照旧原样带出来", "物品没动" in r_maybe))
    # 反面闸：**真成了**的还得是 ✅（别为了修上面那条把 ✅ 全干掉）。
    r_yes = _receipt_from_helper("搬走家具", "红沙发",
                                 {"ok": True, "st": "yes", "text": "🪑 拿起了 红沙发"})
    ok.append(("✅ 反面：真成了照旧 ✅", r_yes.splitlines()[0].startswith("✅")))

    # 🗂 子层**要能看完**（2026-09-29 真机照出来的洞）：子层原来写死 `n=5`，而**没有翻页的口子**
    #    ⇒ 尾巴那句「还有 N 项（more）」是**空承诺**（`more` 压根不存在）。
    #    ⚠️ 2026-10-02：搬走撤了，这条改拿**还活着的目录行**（`箱子…`）来验同一件事。
    many = dict(ctx.tiles)
    for i in range(9):
        many[(20 + i, 20)] = {"x": 20 + i, "y": 20,
                              "furniture": {"name": f"柜{i}", "x": 20 + i, "y": 20,
                                            "width": 1, "height": 1, "furnitureType": 0}}
    mctx = replace(ctx, tiles=many)
    reset_menu()
    render_menu(mctx, n=40)
    _sub_chest = do_row(_no_of("箱子"), act_run, mctx)
    ok.append(("子层说得清「还有多少项」就不再印 `more` 那种空承诺（拿目录行验）",
               "more" not in _sub_chest.lower()))

    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    do_row(_no_of("摸 还没摸的动物"), act_run, ctx)
    ok.append(("摸动物 走 `pet_animals`", any(c[0] == "pet_animals" for c in calls)))
    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    do_row(_no_of("摸 猫狗"), act_run, ctx)
    ok.append(("摸猫狗 走 `pets`", any(c[0] == "pets" for c in calls)))

    # ⚠️ 已经坐着 ⇒ **不该再给「坐」**（要先 scene stand 起身）
    sitctx = _fixture()
    sitctx.sitting = True
    reset_menu()
    ok.append(("坐着时不给「坐」的行", "坐 木椅" not in render_menu(sitctx, n=40)))
    # 🪑 但**必须给「起身」**（2026-10-01）—— 否则单子把 AI 领进一个自己不给出口的姿势：
    #    「坐 木椅」是单子推荐的动作，按下去坐下之后，「坐」消失了而**没有任何行接上**。
    reset_menu()
    _sm = render_menu(sitctx, n=40)
    ok.append(("坐着时**给「起身」**（「坐」的出口）", "起身" in _sm))
    ok.append(("理由栏说清「为什么这一刻有它」", "坐着" in _sm))
    # 没坐着 ⇒ **不给**（按了只会得到"没在坐着，无需起身" —— 那就是"看得见按不成"）
    reset_menu()
    ok.append(("没坐着 ⇒ 不给「起身」", "起身" not in render_menu(_fixture(), n=40)))
    # 敲下去：必须真走 `stand`（现成那个自带轮询复核的）
    _scalls = []

    def _srun(ep, payload):
        _scalls.append((ep, payload))
        return {"ok": True, "st": "yes", "text": "🪑 站起来了（现在 (12,12)）"}

    reset_menu()
    render_menu(sitctx, n=40)
    _sout = do_row(next(r.no for r in _LAST_ROWS if (r.label or "") == "起身"), _srun, sitctx)
    ok.append(("敲「起身」⇒ 真走 `stand`", bool(_scalls) and _scalls[0][0] == "stand"))
    ok.append(("起身回执照抄它自己的话（站没站起来由它说）", "站起来了" in _sout))
    # ⚠️ 养着宠物才给「摸猫狗」那行
    nopet = _fixture()
    nopet.pets = []
    reset_menu()
    ok.append(("没猫狗就不给「摸猫狗」", "摸 猫狗" not in render_menu(nopet, n=40)))

    # 🌿🌾⛏ 捡 / 收作物 / 锄（2026-09-29 接线）——形状不一样，各测各的
    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    r_pick = do_row(_no_of("捡 地上的东西"), act_run, ctx)
    ok.append(("捡 走**拟人那条** `pickup_scene`（不是隔空的 /interact）",
               any(c[0] == "pickup_scene" for c in calls)
               and not any(c[0] == "interact" for c in calls)))
    ok.append(("捡 是**聚合行**（一次一片，不是逐格）", "它自己的话" in r_pick))

    # ⚠️⚠️ **计划数不许当结果数**（2026-09-29 真机活标本：单子写 `捡 ×5`、按下去只到 3 颗，
    #    地上还剩 2 颗（实查过），而头一行照印「✅ 捡 **附近 5 处**」）。
    #    造一个"计划 5、只干成 3"的假帮手 —— 夹具里让它的话报 3，
    #    头一行就**不许**出现把 5 说成做完的说法。
    ctx5 = _fixture()
    for i in range(5):                      # 地上摆 5 样能捡的
        ctx5.tiles[(20 + i, 20)] = {"terrain": "Grass", "forage": True,
                                    "object": {"name": "Truffle", "forage": True}}
    reset_menu()

    def _run_half(op, payload):
        calls.append((op, payload))
        return {"ok": True, "text": "🎁 拾取完成：3 个"}

    r_pick5 = _exec_pick(ctx5, [ctx5.tiles[(20 + i, 20)] for i in range(5)], _run_half)
    head5 = r_pick5.splitlines()[0]
    ok.append(("捡 头一行**不把计划数说成结果**（不许「附近 5 处」）", "附近 5 处" not in head5))
    ok.append(("捡 头一行**如实标明那是动手前看到的**", "去之前看见 5 处" in head5))
    ok.append(("捡 结果数仍是帮手自己说的那个（3 个）", "完成：3 个" in r_pick5))

    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    r_hv = do_row(_no_of("收 成熟作物"), act_run, ctx)
    ok.append(("收作物 走 farm 域的**拟人** `harvest_crops`（不是 C# 的 /harvest）",
               any(c[0] == "harvest_crops" for c in calls)
               and not any(c[0] == "harvest" for c in calls)))
    ok.append(("收作物 半径 **25**（超 30 会被 C# 静默落回 10）",
               all(c[1].get("radius") == 25 for c in calls if c[0] == "harvest_crops")))

    # ⛔ 「锄」那三条 2026-09-29 **随动词一起删了**（它退役了，不再有 `farm_till` 这条路）。
    #    别把它们改写成"跳过"——**留个空壳用例比删掉更坏**：它还是个绿勾，
    #    下一个人会以为"锄 验过了"。删干净，退役的理由在 `VERBS` 那段 ⛔ 里。
    # ⚠️ 开菜单时家具拿不了 ⇒ 不给那行
    menuctx = _fixture()
    menuctx.menu = {"type": "ItemGrabMenu"}
    reset_menu()
    ok.append(("开着菜单 ⇒ 不给「搬家具」", "搬走 红沙发" not in render_menu(menuctx, n=40)))

    # ⑫ 🏪 买 / 卖 —— **同一个"多选 + 配对"机制的真身**（0928 恒听我演的那一套：
    #    「1=4，2=2」）。fixture 用**真字段名**，值全是占位的。
    def _shopctx(shelf=None, sellable=("小嘴鲈鱼", "鲶鱼", "浮木"), money=1234):
        c = _fixture()
        c.money = money
        # 🎒 背包**照 `/state` 的真形**重建：`name`=英文内部名 / `displayName`=中文。
        # ⚠️ 上面 `_fixture()` 那三条**只有 displayName**（它们服务的是"吃/看"，不碰内部名）。
        #    卖走的是**内部名**（C# 是 `item.Name.Equals(name)`）⇒ 这里必须带全，
        #    否则测试自己会**假绿**：行不出现，而"不出现"在别的地方是合法结果。
        #    形照 `/state`（`slotIndex`/`stack`/`value`/`quality`），值全是占位的。
        c.inv = scan_backpack({"inventory": [
            {"slotIndex": 6, "name": "Smallmouth Bass", "displayName": "小嘴鲈鱼",
             "stack": 2, "value": 50, "quality": 0, "sellable": True},
            {"slotIndex": 7, "name": "Catfish", "displayName": "鲶鱼",
             "stack": 1, "value": 200, "quality": 0, "sellable": True},
            {"slotIndex": 8, "name": "Driftwood", "displayName": "浮木",
             "stack": 5, "value": 0, "quality": 0, "sellable": True},
        ]})
        c.held = None          # 手持那件是 `_fixture()` 的，跟这个场景无关（免得多出一条「看」）
        c.shop = {"items": (shelf if shelf is not None else [
                    {"name": "Strawberry Seeds", "displayName": "草莓种子", "id": "(O)745",
                     "price": 100, "stock": 5},
                    {"name": "Parsnip Seeds", "displayName": "防风草种子", "id": "(O)472",
                     "price": 20, "stock": -1},
                    {"name": "Potato Seeds", "displayName": "土豆种子", "id": "(O)475",
                     "price": 50, "stock": 3},
                ]),
                "sellable": (list(sellable) if sellable is not None else None)}
        # ⚠️ 2026-09-30：**商店开着 = `activeMenu` 就是 ShopMenu**（`ctx_from` 从 `/state` 读的那个）。
        #    夹具原先只填 `c.shop`、`c.menu` 留 None ⇒ 跟真机**不是同一个处境**，
        #    于是"菜单态只列买卖"这条判据在自验里**永远走不到**（假绿）。
        c.menu = {"type": "ShopMenu"}
        return c

    sctx = _shopctx()
    reset_menu()
    top = render_menu(sctx, n=40)
    ok.append(("🏪 商店开着 ⇒ 「买」是**目录行**（句尾 `…`）", "买…" in top))
    ok.append(("🏪 顶层**只报几样**、不发号", "3 样" in top))
    ok.append(("🏪 理由栏给钱包（从游戏读的）", "钱包 1234g" in top))
    ok.append(("💰 「卖」也在（背包里有这家收的）", "卖…" in top))
    # 🚧 2026-09-30 真机抓到的**假门**：菜单开着时这层照样把世界动作（坐/搬/收）列出来，
    #    而 MCP 的菜单闸门把 `intent do` 整个挡掉 ⇒ 整屏按不动。判据收到 `_candidates`：
    #    **菜单态只列 `menu_ok`（买/卖）的**。
    ok.append(("🚧 商店开着 ⇒ 单子**只剩买卖**（`坐 stool` 那种世界动作整行不出现）",
               "坐" not in top and "搬走" not in top))
    # ⚠️ 执行侧兜底：号不跨屏，但 `_LAST_ROWS` 可能是**菜单开起来之前**那一屏留下的
    #    ⇒ 拿一个"世界动作"的旧屏 + 菜单态的 ctx 去敲，必须被挡住，不能真跑。
    _stale = _fixture()                      # 菜单 None 的一屏（含坐/搬走…）
    reset_menu()
    render_menu(_stale, n=40)
    _stale.menu = {"type": "ShopMenu"}       # 菜单**现在**开着（模拟"旧屏 + 新状态"）
    _hit = next((r.no for r in _LAST_ROWS
                 if r.verb is not None and not getattr(r.verb, "menu_ok", False)), None)
    ok.append(("🚧 菜单态敲**旧屏**的世界动作 ⇒ 挡住（不真跑）",
               _hit is not None and "菜单开着" in do_row(_hit, fake_run, _stale)))

    # 🚪 守卫文案（2026-10-01 改）：**不许再写死「菜单态能做的只有 买 / 卖」** ——
    #    出口行加进来之后那句话就错了，而且它会把 AI 支去 `menu` 域绕一圈
    #    （出口**本来就在单子上**）。判据：点出「关掉界面」+ 叫它 `show` 重开单子。
    # ⚠️ 这几条**必须紧挨着上面那条**：`_hit` 是照着上一个用例那一屏算出来的号，
    #    中间插几发 `render_*` 就会把 `_STACK`/`_LAST_ROWS` 换掉 ⇒ 打到的不是这一屏
    #    （第一版就是这么假绿的：号落到了出口行上，反而"成功"执行了一次关界面）。
    _stale.menu_exit = "关掉界面"        # 服务器这会儿会给出口行
    _stale.menu_hint = "menu read 看商品 → menu click(button=upperRightCloseButton) 关掉"
    _guard = do_row(_hit, fake_run, _stale)
    ok.append(("🚧 守卫文案点了「关掉界面」", "关掉界面" in _guard))
    ok.append(("🚧 守卫文案叫它 `show` 重开单子（别支去 menu 域绕远）", "show" in _guard))
    # 同一处境、但**没有出口行**（捏人页/鱼机/对话框那三族）：不许谎称"有这一行"
    _stale.menu_exit = ""
    ok.append(("🚧 没有出口行时，守卫文案**不谎称**有",
               "关掉界面" not in do_row(_hit, fake_run, _stale)))
    _stale.menu_exit = "关掉界面"        # 复原，别把状态漏给后面的用例

    # ⑫b 🚪 **界面出口**（2026-10-01）—— 修的是当天真机抓到的**假门**：
    #     开个界面（GameMenu / ItemGrabMenu）⇒ `_candidates` 只留 `menu_ok` 的动词，
    #     而当时只有 买/卖 ⇒ **一屏空**，只剩 `0 做点别的…（at x,y 指哪打哪）`；
    #     可 `at` 指出来的世界动作**正是**上面那条守卫要挡的东西。真机三步走完：
    #       ① show 空 → ② `at 24 26` 给「坐 胡桃木椅子」（**看着有路**）→ ③ `do 1` 被挡。
    #     ⇒ 判据两条：**给一行真能按的出口** + **`at x,y` 那类门牌一句都不许再出现**。
    _gexit = "menu read 看内容 → menu click(button=upperRightCloseButton) 关掉"
    _closectx = _fixture()
    _closectx.menu = {"type": "ItemGrabMenu"}
    _closectx.menu_exit = "关掉界面"
    _closectx.menu_hint = _gexit
    reset_menu()
    _cm = render_menu(_closectx, n=40)
    ok.append(("🚪 菜单态 ⇒ 给「关掉界面」那一行", "关掉界面" in _cm))
    ok.append(("🚪 菜单态 ⇒ **不再**指 `at x,y`（假门拆了）",
               "指哪打哪" not in _cm and "at x,y" not in _cm))
    ok.append(("🚪 菜单态 ⇒ 提示行也不提 `at x,y`", "> 敲编号" in _cm))
    # 出口行**必须**带 `menu_ok`，否则 `_candidates` 在菜单态会把它自己滤掉（= 修了个寂寞）
    _closev = _VERB_BY_KEY.get("close_menu")
    ok.append(("🚪 出口行自带 `menu_ok`（否则会被菜单态过滤掉）",
               bool(_closev and _closev.menu_ok)))
    # 按下去：必须真的走 `close_menu` 这道口，回执**用它自己的话**（别在这儿替它下结论）
    _ccalls = []

    def _crun(ep, payload):
        _ccalls.append((ep, payload))
        return {"ok": True, "st": "yes", "text": "界面已关（原 ItemGrabMenu）"}

    reset_menu()
    render_menu(_closectx, n=40)
    _cout = do_row("1", _crun, _closectx)
    ok.append(("🚪 敲出口 ⇒ 真的调 `close_menu`",
               bool(_ccalls) and _ccalls[0][0] == "close_menu"))
    ok.append(("🚪 出口回执照抄服务器那句话", "界面已关" in _cout))
    # ⚠️ 出口行的**标题**来自服务器（`ctx.menu_exit`）——换了标题，屏上就得跟着换
    #    （这是"判据只留一处"的落点：这一层不认菜单名）。
    _rctx = _fixture()
    _rctx.menu = {"type": "ReadyCheckDialog"}
    _rctx.menu_exit = "撤就绪 / 关屏"
    _rctx.menu_hint = "撤就绪"
    reset_menu()
    ok.append(("🚪 标题照抄服务器（就绪屏 ⇒ 「撤就绪 / 关屏」）",
               "撤就绪 / 关屏" in render_menu(_rctx, n=40)))

    # ⚠️ 三个"**关不得**"的族（捏人页 / 钓鱼小游戏 / 对话框）**不给出口行** ——
    #    给了就是劝 AI 去干错事（捏人页按 ok = 不可逆定型；鱼机是正在干的正事；
    #    ESC 对对话框无效）。这一刻该印的是服务器 `_close_hint` 的**原话**：
    #    那条路确实存在，只是**不在这层**，别冒充成一行动作。
    for _mt, _hint, _what in (
            ("CharacterCustomization", "🎭 别关它、别乱按", "捏人页"),
            ("BobberBar", "🎣 别去动它", "钓鱼小游戏")):
        _nc = _fixture()
        _nc.menu = {"type": _mt}
        _nc.menu_exit = ""            # ← 服务器 `_menu_exit_of` 对这三族返回 ""
        _nc.menu_hint = _hint
        reset_menu()
        _nm = render_menu(_nc, n=40)
        ok.append((f"🚫 {_what} ⇒ **不给**出口行（给了就是劝 AI 干错事）",
                   "关掉界面" not in _nm))
        ok.append((f"📄 {_what} ⇒ 改印 `_close_hint` 的原话（指真路）", _hint in _nm))
        ok.append((f"🚫 {_what} ⇒ 也不指 `at x,y`", "指哪打哪" not in _nm))

    # 💬 对话框**从这一批起有行了**（「推进对话」，见 VERBS 里 `_advance_can` 那段）
    #    ⇒ 它不再走"空白屏印原话"那条路。两种处境要分开判：
    _dlg = _fixture()
    _dlg.menu = {"type": "DialogueBox"}
    _dlg.menu_exit = ""
    _dlg.menu_hint = "menu read 看内容 → 纯对话用 menu advance 推掉"
    reset_menu()
    _dm = render_menu(_dlg, n=40)
    ok.append(("💬 纯对话框 ⇒ 给「推进对话」（不再是空白屏）", "推进对话" in _dm))
    ok.append(("💬 纯对话框 ⇒ 照旧**不给**出口行（ESC 对它无效）", "关掉界面" not in _dm))
    # ⚠️ **有选项**时不给这一行：那一刻该按的是 `menu click(option=N)`（选项号游戏自己发），
    #    给「推进对话」按下去只会原地读回同一屏选项 = "看得见、按了白按"。
    _dlg2 = _fixture()
    _dlg2.menu = {"type": "DialogueBox", "responses": [{"index": 0, "key": "a"}]}
    _dlg2.menu_exit = ""
    _dlg2.menu_hint = "menu read 看内容 → 有选项走 menu click(option=N) 选"
    reset_menu()
    _dm2 = render_menu(_dlg2, n=40)
    ok.append(("💬 **有选项** ⇒ 不给「推进对话」（按了只原地读回选项）",
               "推进对话" not in _dm2))
    ok.append(("💬 有选项 ⇒ 空白屏印 `_close_hint` 原话（指 click(option=N)）",
               "click(option=N)" in _dm2))

    # 🎬 事件（不是菜单）：`activeEvent` 在播时**照样**给「推进对话」
    #    ⚠️ 这是**独立的一条路**：那时 `activeMenu` 可能是 null（不在菜单态里），
    #       所以 `menu_ok` 那套过滤管不着它 —— 它得靠自己的 `can()` 说话。
    _ev = _fixture()
    _ev.event = {"id": "festival_spring13", "skippable": False}
    reset_menu()
    _em = render_menu(_ev, n=40)
    ok.append(("🎬 事件在播 ⇒ 给「推进对话」", "推进对话" in _em))
    ok.append(("🎬 理由栏点名是哪个事件", "festival_spring13" in _em))
    _ev2 = _fixture()
    _ev2.event = {"id": "x", "skippable": True}
    reset_menu()
    # ⚠️ 2026-10-01：这条原来钉的是「理由栏挂着『可整段跳』」——**那半句已经撤了**
    #    （歧义：读起来像"这一按会整段跳"，而整段跳现在**有自己的行** `SKIP_V`）
    #    ⇒ 改成钉**撤掉之后**的契约。⚠️ 钉的是行为，不是"某个词在不在"这种易碎的东西：
    #    ①它说清自己怎么干（一句句推）②不再挂那句有歧义的旧话。
    #    📌 它红了这么久没人发现，是因为 `intent_menu.py` **不匹配全量 runner 的
    #      `*selftest*.py`** ⇒ 已把它加进 `_run_all_selftests.py` 的 EXTRA（同一个坑别再踩）。
    _er2 = render_menu(_ev2, n=40)
    ok.append(("🎬 理由栏说清它**一句句推**，且不再挂那句有歧义的旧话",
               "一句句往下推" in _er2 and "可整段跳" not in _er2))
    reset_menu()
    ok.append(("🎬 没有事件也没有对话 ⇒ **不给**这一行",
               "推进对话" not in render_menu(_fixture(), n=40)))
    # 敲下去：走 `advance`，回执照抄 helper 的话
    _acalls = []

    def _arun(ep, payload):
        _acalls.append((ep, payload))
        return {"ok": True, "st": "yes", "text": "🎬 已推进（推了 3 次、收了 3 句新台词）"}

    reset_menu()
    render_menu(_ev, n=40)
    _aout = do_row(next(r.no for r in _LAST_ROWS if (r.label or "") == "推进对话"),
                   _arun, _ev)
    ok.append(("🎬 敲「推进对话」⇒ 真走 `advance`", bool(_acalls) and _acalls[0][0] == "advance"))
    ok.append(("🎬 回执照抄它自己的话（推了几次由它说）", "推了 3 次" in _aout))

    # 📖 「看」扩到**背包**（2026-10-01 恒：「read 扩到背包」）。
    #    ⚠️ 原来 `target="held"` ⇒ **书揣在包里就读不了**（单子只认手持那件）。
    #       而 `_exec_read` 走的 `_exec_select_then` **本来就会先 `/select` 锁到那一件**
    #       —— 判据比执行器窄，正是这类"整条能力看得见却够不着"的老毛病。
    _bk = _fixture()
    _bk.held = None                       # 手上什么都不拿
    reset_menu()
    _bm = render_menu(_bk, n=40)
    ok.append(("📖 书在背包里（没拿手上）也出「看 古书」", "看 古书" in _bm))
    # 敲下去：必须先 `select` 再 `use`（不然读的是手上别的）
    _rcalls = []

    def _rrun(ep, payload):
        _rcalls.append((ep, payload))
        if ep == "select":
            return {"ok": True}
        return {"ok": True}

    reset_menu()
    render_menu(_bk, n=40)
    do_row(next(r.no for r in _LAST_ROWS if (r.label or "") == "看 古书"), _rrun, _bk)
    ok.append(("📖 敲「看」⇒ 先 `select` 再 `use`（锁到那一本，不读手上别的）",
               [c[0] for c in _rcalls] == ["select", "use"]))
    ok.append(("📖 `use` 带 `mode=read`", _rcalls[-1][1].get("mode") == "read"))

    # 🔍 执行前**复验**（2026-10-01，P0-g）—— `do_row` 的老注释一直挂着这笔账：
    #    "将来接了会做错事的动词（丢东西/送礼），这里必须先补验"。
    #    号不跨屏，可 `_LAST_ROWS` 是**上一次渲染**的 ⇒ 中间世界会动。
    #    ⚠️ 这几条是**闸门自己的可信度**：闸门不可信，后面所有"做错不可逆"的动词都别落地。
    _rk_calls = []

    def _rk_run(ep, payload):
        _rk_calls.append(ep)
        return {"ok": True, "collected": 1}

    _rk = _fixture()
    reset_menu()
    render_menu(_rk, n=40)
    _rk_no = _no_of("捡 地上的东西")
    _rk_row = next(r for r in _LAST_ROWS if (r.label or "") == "捡 地上的东西")
    # (a) 世界没变 ⇒ 照常执行（复验**不许**把正常的活儿也拦了）
    _rk_calls.clear()
    do_row(_rk_no, _rk_run, _rk)
    ok.append(("🔍 世界没变 ⇒ 照常执行（复验不误伤）", _rk_calls == ["pickup_scene"]))
    # (b) 那些东西**已经被捡走了** ⇒ 服务器推的清单里没有它们了 ⇒ 拒绝，且**一个字节都不执行**
    #     ⚠️ 2026-10-01：这里原来是把格子上的 `forage` 抹掉 —— 判据搬去 `pickup_scene` 之后
    #        "世界变了"在这一层的形状就是**清单变了**（`Ctx.pick`），所以改成清清单。
    _rk_calls.clear()
    _gone = _fixture()
    _gone.pick = {}
    _g = do_row(_rk_no, _rk_run, _gone)
    ok.append(("🔍 世界变了（东西没了、清单里没有它们了）⇒ 拒绝", "做不了" in _g))
    ok.append(("🔍 拒绝时**不执行**（这是复验存在的全部意义）", _rk_calls == []))
    # (c) 目标格整个没了 ⇒ 说"什么都没有了"，不说"做不了"（两句是两件事）
    _rk_calls.clear()
    _vanish = _fixture()
    _vanish.tiles = {}
    ok.append(("🔍 目标格消失了 ⇒ 说「什么都没有了」",
               "什么都没有了" in do_row(_rk_no, _rk_run, _vanish)))
    ok.append(("🔍 消失时也不执行", _rk_calls == []))
    # (d) `CAN_MAYBE` 也拦 —— "不知道还能不能做"跟"不能做"在**动作**这层代价一样
    #     ⚠️ 2026-10-01：`捡` 那行已经没有 MAYBE 这条路了（判据搬去脚本、清单为空就是 NO）
    #        ⇒ 拿**现存的**一条 MAYBE 来验：座位容量读不出来（`_sit_can` 的 `free is None`）。
    #        ⚠️ MAYBE 的行**本来就上不了单子**（`_candidates` 只收 `can(...) is True`）
    #        ⇒ 这里直接**手搭那一行**来验复验那层（渲染拿不到它）。
    _may = _fixture()
    _may.tiles[(14, 13)]["seat"]["free"] = None
    _sitrow = Row(_VERB_BY_KEY["sit"], [_may.tiles[(14, 13)]], "坐 木椅", "", 1)
    ok.append(("🔍 `CAN_MAYBE` 也拦（不知道 ≠ 能做；用「座位满没满读不到」验）",
               _recheck(_may, _sitrow) is not None))
    # (e) 背包那件没了 ⇒ 拦（`read` 扩到 inv 之后，这条是真会发生的）
    _bk2 = _fixture()
    _bk2.held = None
    reset_menu()
    render_menu(_bk2, n=40)
    _bk_row = next(r for r in _LAST_ROWS if (r.label or "") == "看 古书")
    _no_book = _fixture()
    # ⚠️ 按 **`idx`（背包位次）** 删，别按 `itemId` —— `_fixture()` 那三件**没有 itemId**
    #    （它们的 `item()` 助手只填 displayName/catNum…），按 id 过滤等于没过滤
    #    ⇒ 测试会假红成"复验没拦住"（第一版就是这么错的）。
    _no_book.inv = [i for i in _fixture().inv if i.get("idx") != 3]
    ok.append(("🔍 背包那件没了 ⇒ 说「已经不在背包里了」",
               "不在背包" in _recheck(_no_book, _bk_row)))
    # (f) 目录行**不触发复验**（它本来就不执行东西，只推一层）
    reset_menu()
    render_menu(_fixture(), n=40)
    ok.append(("🔍 目录行点开照旧（复验只长在**执行**那一步）",
               "箱子一览" in do_row(_no_of("箱子"), _rk_run, _fixture())))

    # 🧾 确认结算（2026-10-01）—— **ShippingMenu 上的正确那一下**，不是「关掉界面」。
    #    `cancel()` 走 ESC + menu_close，而结算屏要点 `ok` 才算完 ⇒ 只给通用出口，
    #    AI 按下去大概率得到一句"还开着"（我那条诚实验收用例用的正是 ShippingMenu）。
    _st = _fixture()
    _st.menu = {"type": "ShippingMenu"}
    _st.menu_exit = ""                      # 服务器 `_menu_exit_of` 对它返回 ""
    _st.menu_hint = "menu read 看内容 → menu click(button=ok) 确认关掉"
    reset_menu()
    _stm = render_menu(_st, n=40)
    ok.append(("🧾 结算屏 ⇒ 给「确认结算」", "确认结算" in _stm))
    ok.append(("🧾 结算屏 ⇒ **不给**「关掉界面」（`cancel()` 对它不管用）",
               "关掉界面" not in _stm))
    reset_menu()
    ok.append(("🧾 不是结算屏 ⇒ 不给「确认结算」",
               "确认结算" not in render_menu(_fixture(), n=40)))
    _stcalls = []

    def _strun(ep, payload):
        _stcalls.append(ep)
        return {"ok": True, "st": "yes", "text": "🧾 已确认过夜结算，和恒一起进入新的一天！"}

    reset_menu()
    render_menu(_st, n=40)
    _sto = do_row(next(r.no for r in _LAST_ROWS if (r.label or "") == "确认结算"), _strun, _st)
    ok.append(("🧾 敲「确认结算」⇒ 真走 `settle`", _stcalls == ["settle"]))
    ok.append(("🧾 回执照抄它自己的话", "进入新的一天" in _sto))

    # 🗑 投出货箱（目录行）——**门禁 = 站在农场**（箱子在那儿），且**一件一行**。
    #    ⚠️ 故意**不做**"全部投放"那一行：`sell_all=True` 是大锤（要留着的也照投），
    #       而单子第一条规矩是"出现的那条按了就成" ⇒ 摆上来的必须是**具体哪件**。
    _bfarm = _fixture()
    _bfarm.loc = "Farm"
    _bfarm.caps = dict(_bfarm.caps, state_shippable=True)
    # ⚠️ 加一件**可卖但投不了**的（大型可制造物：宝箱/熔炉那类）—— 真机上正是这批
    #    让「投出货箱」列表变成假承诺（`sellable=True` 而 `shippable=False`）。
    _bfarm.inv = _bfarm.inv + scan_backpack({"inventory": [
        {"slotIndex": 9, "name": "Keg", "displayName": "小桶", "catNum": -9,
         "stack": 1, "sellable": True, "shippable": False}]})
    reset_menu()
    _bm2 = render_menu(_bfarm, n=40)
    ok.append(("🗑 站在农场 ⇒ 给「投出货箱…」目录行", "投出货箱…" in _bm2))
    ok.append(("🗑 顶层**不**直接铺「投 X」", "投 古书" not in _bm2))
    # ⚠️ 老 DLL 没有 `state_shippable` 这一位 ⇒ **整行不出现**（宁可不给，也别列一堆按不成的）
    _nocap = _fixture()
    _nocap.loc = "Farm"
    reset_menu()
    ok.append(("🗑 老 DLL（没有 `state_shippable`）⇒ **不给**这一行（不猜）",
               "投出货箱" not in render_menu(_nocap, n=40)))
    _bfarm.loc = "FarmHouse"
    reset_menu()
    ok.append(("🗑 不在农场 ⇒ **不给**这一行（箱子在农场，别劝它跑腿）",
               "投出货箱" not in render_menu(_bfarm, n=40)))
    _bfarm.loc = "Farm"
    _bfarm.inv = []                          # 无可投的
    reset_menu()
    ok.append(("🗑 背包没可投的 ⇒ **不给**这一行",
               "投出货箱" not in render_menu(_bfarm, n=40)))
    _bfarm = _fixture()
    _bfarm.loc = "Farm"
    _bfarm.caps = dict(_bfarm.caps, state_shippable=True)
    _bfarm.inv = _bfarm.inv + scan_backpack({"inventory": [
        {"slotIndex": 9, "name": "Keg", "displayName": "小桶", "catNum": -9,
         "stack": 1, "sellable": True, "shippable": False}]})
    reset_menu()
    render_menu(_bfarm, n=40)
    _bl = do_row(next(r.no for r in _LAST_ROWS if (r.label or "") == "投出货箱"), fake_run, _bfarm)
    ok.append(("🗑 点开 = 一件一行（跟「卖」同形）", "投 古书" in _bl and "投 草莓" in _bl))
    ok.append(("🗑 **可卖但投不了的**（小桶 catNum=-9）**不列** —— "
               "真机上正是这批让列表变成假承诺", "投 小桶" not in _bl))
    _bcalls = []

    def _brun(ep, payload):
        _bcalls.append((ep, payload))
        return {"ok": True, "st": "yes", "text": "📦 已投放 1 种物品到出货箱"}

    reset_menu()
    render_menu(_bfarm, n=40)
    do_row(next(r.no for r in _LAST_ROWS if (r.label or "") == "投出货箱"), _brun, _bfarm)
    do_row(next(r.no for r in _LAST_ROWS if (r.label or "") == "投 草莓"), _brun, _bfarm)
    ok.append(("🗑 敲「投 X」⇒ 走 `bin`（带名字）", _bcalls and _bcalls[0][0] == "bin"))

    # 🚪 `at x,y` 那一屏同样要防假门：菜单态下**先把话说清、把真门摆第一行**
    #    （否则又是一屏"看着能按"的世界动作）。真机 ③ 就是在这条路上碰的壁。
    _atctx = _fixture()
    _atctx.menu = {"type": "GameMenu"}
    _atctx.menu_exit = "关掉界面"
    _atctx.menu_hint = _gexit
    reset_menu()
    _at = render_at(_atctx, 14, 13)         # (14,13) = fixture 里那把「木椅」
    ok.append(("📍 菜单态 `at` ⇒ 明说下面**按不动**", "按不动" in _at))
    ok.append(("📍 菜单态 `at` ⇒ 出口摆在**第一行**",
               bool(_LAST_ROWS) and _LAST_ROWS[0].verb is not None
               and _LAST_ROWS[0].verb.key == "close_menu"))

    # 🚪 `0`（这些都不是）在菜单态**不许再顺手指 `at x,y`**（那也是假门的门牌）。
    #    ⚠️ 这里**重新摆一屏**再敲：上面那串用例已经把栈挪过好几处了。
    reset_menu()
    render_menu(_closectx, n=40)
    _z = do_row(0, _crun, _closectx)
    ok.append(("🚪 菜单态敲 `0` ⇒ 不指 `at x,y`（改指真出口）",
               "at x,y" not in _z and _gexit in _z))

    # ⚠️⚠️ 三态不许折叠：没开商店 / 开着读不出来 —— **两种情况都不许出现「买」**，
    #    但原因不一样（一个是"没有"，一个是"不知道"）。混成一个就是静默。
    noshop = _fixture()
    reset_menu()
    ok.append(("🏪 **没开商店** ⇒ 不给「买」", "买…" not in render_menu(noshop, n=40)))
    unknown = _fixture()
    unknown.shop = {}          # 开着但读不出来（"不知道"）
    reset_menu()
    ok.append(("🏪 商店开着但**读不出来** ⇒ 也不给「买」（不糊弄）",
               "买…" not in render_menu(unknown, n=40)))

    # ── 买：目录 → 选哪几样 → 各多少 → 真走端点 ──────────────
    reset_menu()
    render_menu(sctx, n=40)
    n_before = len(calls)
    shelf = do_row(_no_of("买"), fake_run, sctx)
    ok.append(("🏪 敲「买」→ 进货架，**本身什么都不做**",
               "买哪几样" in shelf and len(calls) == n_before))
    ok.append(("🏪 货架行带**单价**", "100g" in shelf))
    ok.append(("🏪 有限量的报**库存**", "剩 5" in shelf))
    ok.append(("🏪 无限量（stock=-1）**不印库存**", "剩 -1" not in shelf))
    # 🆕 2026-09-30 真机：无限量**真机给的是 `int.MaxValue`**，老判据只认 `-1`
    #    ⇒ 单子上印出 `剩 2147483647`（防风草种子那种无限供应的货）。
    ok.append(("🏪 真机的 `int.MaxValue`（也是无限）**同样不印**",
               _stock_text({"stock": 2147483647}) == "" and _stock_text({"stock": 5}) == "剩 5"))

    # 🐄 2026-09-30 真机缺口：站 `Farm (40,0)` 时 `/animals` 回 **0 条**（只看当前图），
    #    而棚里 **24 只待摸** ⇒ 那行**整行不出现**；可 `_exec_pet` 本来就会走进棚里摸。
    _away = _fixture()
    _away.tiles = {k: v for k, v in _away.tiles.items() if not v.get("animal")}
    _away.animals_away = {"Deluxe Coop": 12, "Deluxe Barn": 12}
    ok.append(("🐄 眼前没动物、**棚里有** ⇒ 照样给那一行", _pet_can(_away, None) == CAN_YES))
    ok.append(("🐄 理由栏说清「**在棚里** 24 只」（不是光报个 0）",
               "在棚里 24 只" in _pet_reason(_away, None)))
    _none = _fixture()
    _none.tiles = {k: v for k, v in _none.tiles.items() if not v.get("animal")}
    ok.append(("🐄 眼前没有、棚里也没有 ⇒ **不给**（别没事喊狼来了）",
               _pet_can(_none, None) == CAN_NO))

    q = do_row("1,3", fake_run, sctx)
    ok.append(("🛒 多选 `1,3` → 进「各多少」那层", "各多少" in q))
    ok.append(("🛒 qty 层**沿用**上层发的号", " 1  草莓种子" in q and " 3  土豆种子" in q))

    # ⚠️⚠️ 这条就是那个**静默陷阱**的正身：qty 层只写号，必须**拒**，不许按位置对齐。
    bad = do_row("1,3", fake_run, sctx)
    ok.append(("⚠️ qty 层只写号 → **拒**（写反了会买对东西买错数量）",
               "号=数量" in bad and "❌" in bad))
    # ⚠️ 放在"拒"**之后**：`do_row(0)` 会把 qty 层弹掉 ⇒ 上面那条就变成在 pick 层敲，
    #    结果是"进各多少"而不是"拒"（**测试自己把它测没了**）。
    ok.append(("🛒 子层 `0` → 回上一层", "草莓种子" in do_row(0, fake_run, sctx)))

    def buy_run(ep, payload):
        calls.append((ep, payload))
        if payload.get("item") == "(O)745":
            return {"ok": True, "clicked": "shop_item", "quantity": payload.get("quantity")}
        # 第二样一件都没成交（钱不够/库存没了）
        return {"ok": False, "error": "商店里没买成「土豆种子」（一件都没成交，**钱没动**）"}

    reset_menu()
    render_menu(sctx, n=40)
    do_row(_no_of("买"), fake_run, sctx)
    do_row("1,3", fake_run, sctx)
    calls.clear()
    got = do_row("1=4,3=2", buy_run, sctx)
    ok.append(("🛒 配对 `1=4,3=2` → 真走 buy，**各是各的数量**",
               [c[1].get("quantity") for c in calls] == [4, 2]))
    ok.append(("🛒 回执**逐条列**", "草莓种子 ×4" in got))
    ok.append(("🛒 没成交的那条**不装成功**", "没买成" in got and "土豆种子" in got))

    # ⚠️⚠️ 单价那截必须说**真成本**（2026-09-29 审查）：易货商品的 `Price` 恒 0
    #    ⇒ 只印 `price` 的话，克林特升级/沙漠商人那种"5 个铜锭换"会印成 `0g`，
    #    **看着白拿、点下去真扣材料**。`/menu` 现成带着 trade/tradeCount/tradeName。
    ok.append(("🛒 易货商品印**材料**（不是 0g）",
               _price_text({"price": 0, "trade": "(O)378", "tradeCount": 5,
                            "tradeName": "铜锭"}) == "铜锭×5"))
    ok.append(("🛒 `price` 字段缺 ⇒ **一个字都不印**（不拿 0 兜底）",
               _price_text({"displayName": "神秘种子"}) == ""))

    # ── 卖：**没有数量层**（游戏单击卖整个堆叠）────────────────
    reset_menu()
    render_menu(sctx, n=40)
    n_before = len(calls)
    sell = do_row(_no_of("卖"), fake_run, sctx)
    ok.append(("💰 敲「卖」→ 进候选，**本身什么都不做**",
               "卖哪几摞" in sell and len(calls) == n_before))
    ok.append(("💰 候选写清**整摞走**", "整摞" in sell))
    ok.append(("💰 候选带**这一摞几件 + 值多少**", "×2" in sell))
    # ⚠️⚠️ 这条原来写 `"各多少" not in sell` —— **恒真、拦不住它自称要拦的退化**
    #    （2026-09-29 审查用退化实现复现过：把 `exec_on_pick` 去掉，那条断言**照样过**，
    #     因为「各多少」只出现在 **qty 层**的标题里，pick 层的渲染永远不含它）。
    #    ⇒ 直接钉**机制本身**：这一层得是 pick + `exec_on_pick`。
    #    （「敲了真执行」由下面 `do_row("1,2", …)` 那条钉。）
    ok.append(("⚠️ 卖那一层标了 `exec_on_pick`（**这才是「不进数量层」的判据**）",
               _STACK[-1].mode == "pick" and _STACK[-1].exec_on_pick is True))

    def sell_run(ep, payload):
        calls.append((ep, payload))
        if payload.get("name") == "Smallmouth Bass":
            return {"ok": True, "sold": [{"item": "Smallmouth Bass", "sold": 2,
                                          "unitPrice": 50, "totalPrice": 100}],
                    "totalGold": 100}
        return {"ok": False, "error": "Item 'Catfish' not found in inventory"}

    calls.clear()
    done = do_row("1,2", sell_run, sctx)
    ok.append(("💰 多选 `1,2` → **直接就卖了**（没中间那层）",
               [c[0] for c in calls] == ["sell", "sell"]))
    ok.append(("💰 走的是**内部名**（C# 只认 `item.Name`）",
               calls[0][1].get("name") == "Smallmouth Bass"))
    ok.append(("💰 回执写清**整摞卖了几个**（AI 只报了'1 号'，得替它把量说回来）",
               "整摞 2 个" in done and "100g" in done))
    ok.append(("💰 没卖成的那条**如实报**", "没卖成" in done))

    # ⚠️ 差额那句**只报事实**（2026-09-29 审查）：原来条件写 `n != stack`、话写"比预想少"
    #    ⇒ 卖出**更多**时回执自打脸；后半句"它只卖了第一摞"是**编原因**（候选层已保证
    #    同名只列一摞，这原因压根不成立）。`/state` 的 stack 只是**渲染当时**的快照。
    reset_menu()
    render_menu(sctx, n=40)
    do_row(_no_of("卖"), fake_run, sctx)
    _rows = [r for r in _STACK[-1].rows if (r.label or "") == "小嘴鲈鱼"]
    _out = _exec_sell_multi(sctx, [(_rows[0], None)],
                            lambda ep, p: {"ok": True, "totalGold": 250,
                                           "sold": [{"item": "Smallmouth Bass", "sold": 5}]})
    ok.append(("💰 卖出**更多** ⇒ 不印「比预想少」（条件只写'不等'、话却断言'少'）",
               "比预想少" not in _out and "整摞 5 个" in _out))
    ok.append(("💰 差额只**摊开两个数**、不替游戏编原因",
               "看单子时是 2 个" in _out and "只卖了第一摞" not in _out))

    # ⚠️ 卖那一层**形状跟默认 pick 不一样** ⇒ 提示得说清怎么敲（不然 AI 会照默认少写一层）
    reset_menu()
    render_menu(sctx, n=40)
    sell2 = do_row(_no_of("卖"), fake_run, sctx)
    ok.append(("💰 提示说清**敲了就卖**（不是'可以多选'就完事）", "敲了就卖" in sell2))
    ok.append(("💰 卖那一层写「号=数量」→ 拒（这一层没有数量）",
               "没有数量要填" in do_row("1=2", fake_run, sctx)))

    # ⚠️ 分不清是哪一摞 ⇒ **两摞都不列** + 如实说挑出去几摞（宁缺勿编）。
    #    ⚠️⚠️ 必须**两把尺子各演一遍**（2026-09-29 审查抓的假绿）：
    #    原来只演了"同内部名"那一种 —— 于是"筛选用显示名、去重却用内部名"这个
    #    **两把尺子对不上**的洞照样全绿（第二例就是它）。
    def _sell_pick(c, needle="卖"):
        reset_menu()
        render_menu(c, n=40)
        return do_row(_no_of(needle), fake_run, c)

    # ① 同**内部名**（同物品、品质不同 ⇒ 叠不成一摞）：端点按 Name 卖会拿第一组
    dup1 = _shopctx(sellable=("小嘴鲈鱼", "鲶鱼"))
    dup1.inv.append(scan_backpack({"inventory": [
        {"slotIndex": 9, "name": "Smallmouth Bass", "displayName": "小嘴鲈鱼",
         "stack": 4, "value": 75, "quality": 2, "sellable": True}]})[0])
    d1 = _sell_pick(dup1)
    ok.append(("💰 ①同**内部名**两摞 ⇒ 一个都不印（端点是'卖第一组'，列了就是让它指空气）",
               "小嘴鲈鱼" not in d1))
    ok.append(("💰 ①同内部名 ⇒ **如实说**挑出去了", "分不清" in d1))

    # ② 异**内部名**、同**显示名**（Wine/Juice 都叫「酒」这类）：
    #    行标签一样 ⇒ AI 分不出哪行是哪件；而端点按**内部名**卖 ⇒ 两行各自卖各自那件，
    #    AI 指哪个号都可能卖错东西。**只按内部名去重会漏掉这一例。**
    #    ⚠️ 得留一条**不撞车**的（鲶鱼）：两件「酒」都被挑出去之后、若没有别的可卖，
    #       「卖」那一行**整行不出现**（`_sell_can` 的正确行为）⇒ 测不到那条注释。
    dup2 = _shopctx(sellable=("酒", "鲶鱼"))
    dup2.inv = scan_backpack({"inventory": [
        {"slotIndex": 6, "name": "Wine", "displayName": "酒",
         "stack": 1, "value": 10, "quality": 0, "sellable": True},
        {"slotIndex": 7, "name": "Juice", "displayName": "酒",
         "stack": 1, "value": 10, "quality": 0, "sellable": True},
        {"slotIndex": 8, "name": "Catfish", "displayName": "鲶鱼",
         "stack": 1, "value": 200, "quality": 0, "sellable": True},
    ]})
    d2 = _sell_pick(dup2)
    ok.append(("💰 ②同**显示名**两件 ⇒ 也一个都不印（否则两行印得一模一样）",
               " 1  酒" not in d2))
    ok.append(("💰 ②同显示名 ⇒ **如实说**挑出去了", "分不清" in d2))

    # ⚠️ 这家不收的**一根都不动**（判据问游戏，不是我们编名单）
    nostock = _shopctx(sellable=("海胆",))
    reset_menu()
    ok.append(("💰 这家不收我背包里的 ⇒ 不给「卖」（不编名单）",
               "卖…" not in render_menu(nostock, n=40)))

    # ⚠️ 理由栏**不能说错语义**（2026-09-29 审查）：`sellableHere` 是
    #    「这家收的 **∩ 我背包里真有的**」，写成「这家收 N 样」会被读成
    #    "这店只收 N 种"（漏掉"还有多少种我手上没有"）。理由栏是审计面，说错=显形。
    reset_menu()
    top_sell = render_menu(sctx, n=40)
    ok.append(("💰 理由栏不说「这家收 N 样」（那是交集，不是全集）", "这家收" not in top_sell))
    ok.append(("💰 理由栏只报**能到手多少钱**", "共 300g" in top_sell))

    print("\n—— 意图选项单 · 不吃游戏自验 ——")
    for name, good in ok:
        print(("  ✅ " if good else "  ❌ ") + name)
    bad = [n for n, g in ok if not g]
    print(f"\n{len(ok) - len(bad)}/{len(ok)} 过")
    if bad:
        print("未过：" + " / ".join(bad))

    print("\n—— 样例单（形，值全是占位的）——")
    reset_menu()          # ⚠️ 必须清：上面那些用例会把子层留在栈上，
                          #    不清就印出"上一个用例的那一屏"（而且夹具还是那个缺字段的）
    print(render_menu(ctx, header="🎯 FarmHouse (12,12) · 🔋268"))
    print("\n> at 12,13   （接没接执行都列，分开列）")
    print(render_at(ctx, 12, 13))
    print("\n> at 13,13   （指着一个箱子：目录动词也列出来）")
    print(render_at(ctx, 13, 13))
    print("\n> do(箱子) → do(矿石箱)   （箱子合一：先一览，再进那一只的动作面）")
    reset_menu()
    render_menu(ctx, n=40)
    print(do_row(_no_of("箱子"), lambda e, p: {"ok": True}, ctx))
    print(do_row(_no_of("矿石箱"), lambda e, p: {"ok": True}, ctx))

    print("\n> 站在柜台前（商店 menu 开着）—— 买/卖怎么长")
    reset_menu()
    shopshow = _shopctx()
    render_menu(shopshow, n=40)
    print(render_menu(shopshow, n=40))
    print("\n> do(买)   （点开货架——翻页那步**不用 AI 操心**）")
    print(do_row(_no_of("买"), lambda e, p: {"ok": True}, shopshow))
    print("\n> do(1,3) → do(1=4,3=2)   （各多少：号=数量，配对，序无关）")
    do_row("1,3", lambda e, p: {"ok": True}, shopshow)
    print(do_row("1=4,3=2", lambda e, p: {
        "ok": True, "clicked": "shop_item", "quantity": 4}, shopshow))
    print("\n> do(卖)   （**没有数量层**——游戏单击卖整个堆叠）")
    reset_menu()
    render_menu(shopshow, n=40)
    print(do_row(_no_of("卖"), lambda e, p: {"ok": True}, shopshow))
    return len(bad) == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
