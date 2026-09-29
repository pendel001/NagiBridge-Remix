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
            # 下面三个**原样搬，不猜**：字段不在就是 None，跟着走 CAN_MAYBE
            "cat_num": i.get("catNum"),
            "edible": i.get("edibleValue"),
            "health": i.get("healthRecovered"),
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
    # 🪑 我此刻是不是坐着（`/sittable` 的 `me.sitting`）。坐着时**不该再给"坐"的行**
    #    ——要先起身（`scene stand`）。这是**处境**，不是格子的属性。
    sitting: bool = False
    # 🐾 本图的宠物（猫狗）——来自 `/surroundings` 的 `npcs` 里 `kind=="pet"` 的那几个。
    #    它们是**世界级**的（不属于某一格的动作），所以不进 tiles。
    pets: list = field(default_factory=list)
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
    #    （等 C# 批次里加一个 build 能力位，就能自动填了。）
    caps: dict = field(default_factory=dict)
    # 🀄 英文物品名 → 中文显示名（**缺就用英文**，不编）。
    #    来源全是"我们手里已经有的数据"：AI 背包的 displayName + 本图箱子里物品的
    #    displayName。**不为此新打 HTTP**。
    #    ⚠️ 正解是让 C# 的 `/machines` 直接吐 `heldItemDisplay`（复用现成的
    #    `IngredientLabel`——`/machines` 的 `heldItemId` 都给全了，一行的事）。
    #    进 C# 批次，**别在这儿堆名单**（名单会烂，本项目的老病）。
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

    def zh_of(self, name: str) -> str:
        return (self.zh or {}).get(name) or name

    def cap(self, name: str):
        """这版 DLL 认不认这个字段？→ True / None(不知道)"""
        return self.caps.get(name)

    def tile(self, x: int, y: int):
        return self.tiles.get((x, y))

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
    target: str = "tile"             # 'tile' | 'held'
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


# ── 以下每个 can() 都只用**端点已经吐出来的**字段，一个都不用猜 ──────────

def _pick_can(ctx, t):
    """🌿 捡：问游戏 `Object.isForage()`（`/surroundings` 的 `forage` 字段）。

    ⚠️ 这个键**只在为真时才写**（C# `if (objForage) tile["forage"] = true;`）
    ⇒ 键不在 = **这格不可捡**（不是"不知道"）。"不知道"只可能是整版 DLL 老，
    那是**连接级**的事 → 查 `ctx.cap`，别在图里找。
    """
    if not t:
        return CAN_NO
    if ctx.cap("forage") is None:
        return CAN_MAYBE
    return CAN_YES if t.get("forage") is True else CAN_NO


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


def _dig_can(ctx, t):
    """⛏ 锄：`/surroundings` 的 `diggable`（游戏地图属性 `Diggable`）。

    同 `forage`：**只在为真时才写键** ⇒ 键不在 = 不可锄。
    """
    if not t:
        return CAN_NO
    if ctx.cap("diggable") is None:
        return CAN_MAYBE
    return CAN_YES if t.get("diggable") is True else CAN_NO


def _dig_reason(ctx, t):
    return "游戏判可锄（挖斑点/开地）"


def _dig_show(ctx, t):
    return "锄地"


def _collect_can(ctx, t):
    """📦 收机器：`/machines` 的 `status`（C# 由 `readyForHarvest` 定，见 ModEntry 18503）。"""
    if not t:
        return CAN_NO
    m = t.get("machine")
    if m is None:
        return CAN_NO
    st = m.get("status")
    if st is None:
        return CAN_MAYBE
    return CAN_YES if st == "ready" else CAN_NO


def _collect_reason(ctx, t):
    # 产物名已经在**标签**里了（`收 钻石`），别在理由栏再说一遍。
    return "机器已好"


def _collect_show(ctx, t):
    m = t.get("machine") or {}
    item = m.get("item")
    return f"收 {ctx.zh_of(item) if item else (t.get('object') or '机器')}"


def _collect_reason_many(ctx, targets):
    """把一行里的产物按数量摊开——恒要的「可以收的钻石×1，翡翠×n」。

    ⚠️ 摊在**理由栏**，不摊在标签里：标签是**动作**（收 已好的机器），
    理由是**这批是什么**。两件事别混（同屏两个"格"两个意思那种病）。
    """
    cnt = {}
    for t in targets:
        it = (t.get("machine") or {}).get("item") or "?"
        cnt[it] = cnt.get(it, 0) + 1
    return "、".join(f"{ctx.zh_of(k)}×{v}"
                     for k, v in sorted(cnt.items(), key=lambda x: -x[1]))


def _exec_collect(ctx, targets, run):
    """🧺 收机器——**复用现成的 `/machine_collect`**。

    ⚠️⚠️ **这是快捷路，不是拟人路。** C# 里是 `farmer.addItemToInventory(held)`
    （`ModEntry.cs:18725`），**不要求角色在机器旁边**——AI 会"隔着半个屋子把 20 台
    机器一次收干净"。拟人那条（走过去逐台 `interact`）在 `machine_loader.py` 里，
    **一个字没动**。恒 2026-09-27 拍板「暂时复用现在的那个」，所以就这儿接。

    ⇒ **要换拟人时只改这一个函数**——单子、动词表、can() 全都不用动。

    ⚠️ 执行器是**整屋批量**的 ⇒ 这一行必须 `merge=True`，**不能假装能只挑三台收**。
    """
    r = run("machine_collect", {"location": ctx.loc})
    if not r.get("ok"):
        return render_receipt("收机器", ctx.loc, False,
                              note=f"游戏回：{r.get('error') or r}")
    n, skip = r.get("collected", 0), r.get("skippedFull", 0)
    note = f"实际收到 {n} 件"
    if skip:
        # ⚠️ 背包满**不是成功**，而且必须同时说清**下一步**（报缺了要给出路，别让 AI 干瞪眼）
        note += f" · 背包满了，还有 {skip} 件没收 —— 先去卖或存，回来再敲一次"
    return render_receipt("收机器", ctx.loc, True, note=note)


def _eat_can(ctx, t):
    return is_edible(t) if t else CAN_NO


def _eat_reason(ctx, t):
    parts = []
    if t.get("edible"):
        parts.append(f"体力 +{t['edible']}")
    if t.get("health"):
        parts.append(f"血 +{t['health']}")
    # ⚠️ 别在这写"手持"——`_where()` 已经在前面写了一次，会印成"手持 手持 · …"（同 `_read_reason`）。
    return " / ".join(parts)


def _eat_show(ctx, t):
    return f"吃 {t.get('name')}"


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

def _receipt_from_helper(verb_cn, desc, r):
    """**高阶层动作**（那种 Python 里已经写好的、自己会读回验证的 op）的回执。

    ⚠️ 回执**优先用它自己的话**（`text`），别在这儿重拼一遍：
    那些 op 里带着复核（`furniture_pickup` 就是**靠前后 diff 才没报错名字**的，
    恒 2026-09-19 真机抓到过"报的是地毯、动的是椅子"）。
    我们重拼 = 把它们的复核丢掉，又回到"嘴上说成功"。
    """
    if not isinstance(r, dict):
        return render_receipt(verb_cn, desc, False, note=f"回包看不懂：{r!r}")
    txt = (r.get("text") or "").strip()
    ok = bool(r.get("ok"))
    if txt:
        return f"{'✅' if ok else '❌'} {verb_cn} {desc}\n   " + txt.replace("\n", "\n   ")
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


def _pickup_can(ctx, t):
    """🛋 拿得起家具吗。

    ⚠️ **游戏没有"这件能不能拿"的事前判据**（`canBeRemoved()` 要传人、且装修图里恒真）
    ⇒ 这一条**没有真正的 can()**，只能"有家具就给行、拿不动由回执如实报"
       （背包满 / 别人家的床 —— 那两样 `furniture_pickup` 都会点名说）。
    ⚠️ 开菜单时拿不了（helper 自己写的）⇒ 开菜单就不给。
    """
    if not t or ctx.menu:
        return CAN_NO
    return CAN_YES if t.get("furniture") else CAN_NO


def _pickup_show(ctx, t):
    return f"搬走 {t['furniture'].get('name') or '家具'}"


def _pickup_reason(ctx, t):
    f = t["furniture"]
    wh = f"{f.get('width')}×{f.get('height')}"
    return f"{wh} · 装修图可隔屋拿"


def _exec_pickup(ctx, targets, run):
    f = targets[0]["furniture"]
    r = run("furniture_pickup", {"x": f.get("x"), "y": f.get("y")})
    return _receipt_from_helper("搬走家具", f.get("name") or "", r)


# 🛋 顶层那一行只报总数（`搬走…（34 件）`）——**一条一行的活在下一层**
#    ⚠️ 恒 2026-09-29 拍板：「移动家具做单行」。
#    理由不是好看：一屋子家具 30+ 行，会把「收机器 / 开箱子」这些**一下能做完的**
#    挤成"还有 35 项"——第一屏的承诺是**动作面**，不该被"点开还有一层"占满。
PICKUP_ITEM_V = Verb("pickup_one", "搬走", 0, _pickup_can, _pickup_reason, _pickup_show,
                     "tile", exec=_exec_pickup, group="家具")


def _pickup_reason_many(ctx, targets):
    """合一那行的理由：**最近一件几步**（同"20 处 · 最近 6 步"那个口径）。"""
    return f"最近 {min(_dist(ctx, t) for t in targets)} 步"


def _pickup_count(ctx, targets):
    return f"{len(targets)} 件"


def _pickup_subs(ctx, targets):
    """🛋 「搬走」的下一层：**屋里能拿走的东西，一件一行**。

    ⚠️ 排序只用**距离**（近的先搬，省得来回跑）——这一层没有权重表可用，
       距离是这里**唯一"合法且可审计"**的理由（跟顶层同一条规矩：理由要有出处）。
    """
    rows = [Row(PICKUP_ITEM_V, [t], _pickup_show(ctx, t), _pickup_reason(ctx, t),
                _dist(ctx, t), group="家具")
            for t in targets if t.get("furniture")]
    if not rows:
        return None
    rows.sort(key=lambda r: r.dist)
    return Level(rows, title=f"🛋 搬走哪一件？（{len(rows)} 件 · 敲了就搬走）")


def _animals_left(ctx):
    """还没摸的牲畜——`/animals` 的 `wasPetToday`（**游戏自己的字段**，不是我们记的账）。"""
    return [t for t in ctx.tiles.values() if (t.get("animal") or {}).get("wasPetToday") is False]


def _pet_can(ctx, t):
    return CAN_YES if _animals_left(ctx) else CAN_NO


def _pet_show(ctx, t):
    return "摸 还没摸的动物"


def _pet_reason(ctx, t):
    left = _animals_left(ctx)
    cnt = {}
    for a in left:
        ty = (a.get("animal") or {}).get("type") or "?"
        cnt[ty] = cnt.get(ty, 0) + 1
    return f"{len(left)} 只（" + "、".join(f"{k}×{v}" for k, v in cnt.items()) + "）"


def _exec_pet(ctx, targets, run):
    left = _animals_left(ctx)
    r = run("pet_animals", {})
    return _receipt_from_helper("摸动物", f"{len(left)} 只", r)


def _pets_can(ctx, t):
    return CAN_YES if ctx.pets else CAN_NO


def _pets_show(ctx, t):
    return "摸 猫狗"


def _pets_reason(ctx, t):
    return "、".join(p.get("name") or "宠物" for p in ctx.pets)


def _exec_pets(ctx, targets, run):
    who = "、".join(p.get("name") or "宠物" for p in ctx.pets)
    r = run("pet_pets", {})
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
    return _receipt_from_helper("捡", f"附近 {len(targets)} 处", r)


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
    return _receipt_from_helper("收作物", f"半径 25 内（看见 {len(targets)} 格熟的）", r)


def _exec_dig(ctx, targets, run):
    """⛏ 锄——`farm till` 的单格路（`_farm_till(x,y)`：x/y 必填、缺省 1×1、单格恒走拟人）。"""
    t = targets[0]
    r = run("farm_till", {"x": t.get("x"), "y": t.get("y")})
    return _receipt_from_helper("锄", f"({t.get('x')},{t.get('y')})", r)


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


def _exec_take_multi(ctx, pairs, run):
    """🧺 取：**逐条报**（哪条成了、哪条没成）。

    ⚠️ 不许整批报成功、也不许整批回滚——两个都是替 AI 圆场（166 ④ 配套硬要求）。
    ⚠️ **只报事实，不替游戏编原因**（2026-09-29 审查）：`took < cnt` 既可能是箱里不够，
    也可能是**背包中途塞满**（`HandleChestTake` 装不下就提前 break）——
    原来那句写死"（箱里只有 N 个）"是**我们把猜测当成了游戏的话**。现在只报差额。
    """
    lines, ok_n = [], 0
    for row, cnt in pairs:
        it, box = row.targets[0]["item"], row.targets[0]["box"]
        cn = it.get("displayName") or it.get("name")
        r = run("chest_take", {"x": box["x"], "y": box["y"],
                               "name": it.get("name"), "count": cnt}) or {}
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

    ⚠️⚠️ **这是快捷路，不是拟人路**（同 `_exec_collect` 的记账）：
    C# `HandleStore`（`ModEntry.cs:10429`）是**原子直操**——`farmer.Items` ↔ `chest.addItem`，
    **不校验距离** ⇒ 人站在半张图外也能"存进去"。
    拟人那条是 MCP 工具 `chest_store`（`nagi_mcp_server.py` 里先 `_walk_to_chest` 再 `/store`）。
    ⇒ **接线时必须补 `/walk_to`**（或直接改调 `chest_store`），否则恒一眼看出不是人在走。
      今晚没补：走路是**观感**，没真机验过的走路不该先写死一套"走多近、站哪边"。
    """
    lines, ok_n = [], 0
    for row, cnt in pairs:
        slot, box = row.targets[0]["slot"], row.targets[0]["box"]
        cn = slot.get("name") or slot.get("raw", {}).get("name")
        r = run("store", {"x": box["x"], "y": box["y"],
                          "name": _held_name(slot), "count": cnt, "keepTools": True}) or {}
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
    ⚠️ 端点 `/chest_open` **这版 C# 还没有**（在批次里）⇒ 由 `caps` 把关，那行不出现。
       真接上了，这里也要先 `/walk_to` 走到箱子边再开（拟人那条）。
    """
    t = targets[0]
    x, y = t.get("x"), t.get("y")
    r = run("chest_open", {"x": x, "y": y}) or {}
    if not r.get("ok"):
        return render_receipt("开箱", f"({x},{y})", False,
                              note=f"游戏回：{r.get('error') or r}")
    return render_receipt("开箱", f"({x},{y})", True,
                          note="菜单开着（不关）—— 内容用 menu read 看")


# 「看 / 取 / 存」三个动作——**只长在容器那一层里**，不进顶层动词表：
# 顶层扫的是"图上的格子"，而它们的目标是"这个容器"，由 `_chest_subs` 现场算。
OPEN_V = Verb("chest_open", "看（走过去开箱）", 0, None, None,
              lambda c, t: "看（走过去开箱）", "tile", exec=_exec_chest_open)
TAKE_V = Verb("chest_take", "取", 0, None, None, lambda c, t: "取", "tile",
              exec_multi=_exec_take_multi)
STORE_V = Verb("chest_store", "存", 0, None, None, lambda c, t: "存", "tile",
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


def _item_row(it, box, verb):
    """容器里的一样东西 = pick 层的一行。**不在世界里 ⇒ 不印定位**（印"手持"就是撒谎）。"""
    cn = it.get("displayName") or it.get("name")
    return Row(verb, [{"item": it, "box": box}], cn, f"箱里 ×{it.get('count')}", 0, where="")


def _slot_row(slot, box, verb):
    """背包里的一样东西 = pick 层的一行，目标是"存进这个容器"。"""
    return Row(verb, [{"slot": slot, "box": box}], slot.get("name"),
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
        notes.append(f"有 {amb} 摞**同名但不同品质**的没列出来 —— 端点只按名字认，"
                     f"认不出是哪一摞（要精确挑就用 storage 域）")

    # ③ 存…：背包 ∩ 容器收的 ∩ 容器放得下
    space = _box_space(box)
    if space is CAN_YES:
        # ⚠️ **工具不进这张候选**：`/store` 默认 `keepTools=True`（恒的保护设置），
        #    它会在端点里**静默跳过工具** ⇒ 列出来就是"按了不成"的行。
        #    不在这儿顺手把 `keepTools` 翻成 false ——那是**动恒设的安全阀**，得他拍板。
        #    判据问游戏（`catNum == -99`）；**问不出（None）也不列**（同三档：不透支信任）。
        can = [s for s in ctx.inv
               if s.get("idx") and is_tool(s) is False and _box_accepts(box, s) is True]
        can, amb2 = _unambiguous(can, _held_name)
        if can:
            rows.append(Row(STORE_V, [t], "存", f"箱空 {box.get('freeSlots')} 格", 0,
                            level=Level([_slot_row(s, dict(box, x=x, y=y), STORE_V) for s in can],
                                        title=f"存哪几样去 {here}？（可以多选，如 `1,4`）",
                                        mode="pick", verb=STORE_V),
                            where="", count_text=f"{len(can)} 种"))
        if amb2:
            notes.append(f"背包里有 {amb2} 摞**同名但不同品质**的没列出来 —— "
                         f"端点只按名字认，认不出是哪一摞")
    elif space is CAN_NO:
        notes.append(f"箱子满了（{box.get('used')}/{box.get('capacity')} 格）"
                     f"—— 先取点东西出来，「存」才放得下")

    title = f"📦 {box.get('name') or '箱子'} {here}"
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


def _chest_reason_many(ctx, targets):
    """合一那行的理由：**最近一个箱子几步**（同"20 处 · 最近 6 步"那个口径）。"""
    return f"最近 {min(_dist(ctx, t) for t in targets)} 步"


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


def _chest_show(ctx, t):
    b = _box(t) or {}
    return f"{b.get('name') or '箱子'}({t.get('x')},{t.get('y')})"


def _chest_tag(box) -> str:
    """一箱的标签：**色名 + 人工名（优先）/ 自动类目标签**。

    ⚠️ 口径**照抄 `storage_layout`**（服务器那边那份"当前场景箱子一览"）——
       同一批箱子在两张屏上不能长得不一样，不然就是两把尺子。
       ⚠️ 那边多一个 `⭐`（本场景默认箱）——那个记号归 storage 域，单子这边**不搬**：
          默认箱是"存去哪"的设置，不是"这里有什么"。
    """
    emo, czh = _color_display((box or {}).get("color") or "")
    tag = (emo + czh) if czh else "⬜"
    if box.get("name"):
        tag += f"「{box['name']}」"        # 人工标注的名字优先（同 storage_layout）
    elif box.get("autoTag"):
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
            #    AI 看得懂的那个名字（同 `_collect_reason_many` 的口径）。
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
    """库存那截。`stock < 0` = **无限量**（游戏用 `-1` 表示）⇒ 一个字都不印。"""
    st = g.get("stock")
    if isinstance(st, int) and st >= 0:
        return f"剩 {st}"
    return ""


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


# 「买 / 卖」两个动词 = **目录行**（顶层只报有几样，点开才发号）。
# ⚠️ 它们**同一个对象**既是顶层那条（`subs`/`count`）又是子层的执行者（`exec_multi`）——
#    容器那边分成了 `chest` / `chest_take` 两个对象，是因为顶层扫的是"图上的格子"（`tile`）
#    而子层的目标是"这个容器"。买卖没有"格子"：它在 **world** 这一档（问的是"现在这个处境"），
#    顶层和子层指向的是同一个东西 ⇒ 一个对象就够，分成两个反而多一处会漂的重复。
BUY_V = Verb("buy", "买", 72, _buy_can, _buy_reason, lambda c, t: "买", "world",
             subs=_buy_subs, count=_buy_count, exec_multi=_exec_buy_multi)
SELL_V = Verb("sell", "卖", 74, _sell_can, _sell_reason, lambda c, t: "卖", "world",
              subs=_sell_subs, count=_sell_count, exec_multi=_exec_sell_multi)


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
         reason_many=_chest_reason_many, group="设备"),
    Verb("collect", "收 已好的机器", 88, _collect_can, _collect_reason, _collect_show, "tile",
         exec=_exec_collect, merge=True, reason_many=_collect_reason_many, group="设备"),
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
    Verb("pet_pets", "摸 猫狗",      83, _pets_can, _pets_reason, _pets_show, "world",
         exec=_exec_pets),
    # 🪑 坐 / 🛋 搬家具（逐格）——2026-09-29 接线
    Verb("sit",     "坐",     70, _sit_can,     _sit_reason,     _sit_show,     "tile",
         exec=_exec_sit, group="家具"),
    # 🛋 搬走：**目录行**（一屋子家具一件一行 ⇒ 顶层只留一行报总数，点开才发号）。
    Verb("pickup_f", "搬走", 68, _pickup_can, _pickup_reason, _pickup_show, "tile",
         subs=_pickup_subs, count=_pickup_count, merge=True,
         reason_many=_pickup_reason_many, group="家具"),
    # 🌿 捡 / 🌾 收作物：**聚合行**（一次一片，端点的语义本来就不是逐格）
    Verb("pick",    "捡 地上的东西", 90, _pick_can, _pick_reason, _pick_show, "tile",
         exec=_exec_pick, merge=True, reason_many=_pick_reason_many),
    Verb("harvest", "收 成熟作物", 85, _harvest_can, _harvest_reason, _harvest_show, "tile",
         exec=_exec_harvest, merge=True, reason_many=_harvest_reason_many),
    # ⛏ 锄：**逐格**（`_farm_till(x,y)` 单格恒走拟人）
    Verb("dig",     "锄",     60, _dig_can,     _dig_reason,     _dig_show,     "tile",
         exec=_exec_dig),
    # 🍽📖 吃 / 看：**接上了**（2026-09-29）。两条都是 `held` 目标、都走"先 select 再动手"，
    #     共用同一个执行器形状（见 `_exec_select_then`）。
    #     ⚠️ 它们能不能出现，取决于 `ctx.held` —— 而 `ctx_from` 原先读 `currentTool`
    #     （书/食物都不是 Tool）⇒ **这两条结构性永不出现**。今晚一并修了（见 `ctx_from`）。
    Verb("eat",     "吃",     50, _eat_can,     _eat_reason,     _eat_show,     "held",
         exec=_exec_eat),
    Verb("read",    "看",     40, _read_can,    _read_reason,    _read_show,    "held",
         exec=_exec_read),
    # 🏪 买 / 卖（**只在商店开着时才有**，见上面那段商店说明）。两条都是**目录行**。
    # ⚠️ 权重压在 `collect`(88)/`chest`(80) 之下、`eat`(50) 之上：站在柜台前，买卖是正事；
    #    但商店**开着**的时候才会出现，所以它不会跟农场那批抢第一屏。
    SELL_V, BUY_V,
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
    ("sit",      "坐",     "要 `/sittable`（`GetSeatCapacity()>0` + `mapSeats`）——现成端点，搬进来即可"),
    ("pickup_f", "搬走家具", "要 `/furniture` + `canBeRemoved()`——现成端点"),
    ("gift",     "送礼",    "要面前是 NPC + `tryToReceiveActiveObject`——问游戏有，但**是动作级的**（试了才知道收不收）"),
    ("pet",      "摸动物",  "要牲畜的「今天摸过没」——现成端点里没这字段"),
    # ⚠️ 2026-09-29：`open`（开箱）与 `machine`（收机器）两条**已上线**，从这里挪走了：
    #    开箱 → 顶层「箱子」目录行；收机器 → `collect`（快捷路，恒拍板复用）。
    #    剩下的两条**新缺口**是今晚反编译核容器时才看清的（都在 C# 那边，不在这一层）：
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
               count_text=v.count(ctx, targets) if (v.count and lv) else None)


def _candidates(ctx: Ctx) -> list:
    """跑一遍动词表 × **整张图**的目标，按「动词 + 对象名」聚合成行。

    ⚠️ 候选是**全屋/全场景**（见 `Ctx` 里那段删除说明）——别再加范围限制。
    聚合的键 = **对象名**（`show()` 的输出，本身不含坐标）：
    三台水冷塔都出钻石 ⇒ 一行「收 钻石 ×3」，敲一下 = 走过去全收。

    返回**已排序但未截断**——截断是 render 的事，而且它必须把砍掉的数量报出来。
    """
    buckets = {}
    for v in VERBS:
        # 「接了」= 有 exec（动作行）**或** 有 subs（目录行）。两个都没有 = 看得见按不动，不上单子。
        if v.exec is None and v.subs is None:
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
        else:
            for t in ctx.tiles.values():
                if v.can(ctx, t) is True:
                    buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)

    rows = [_row_for(ctx, _VERB_BY_KEY[vkey], targets, label)
            for (vkey, label), targets in buckets.items()]
    # 排序：先按动词权重，再按**距离**。
    # ⚠️ 距离是个**合法且可审计**的排序理由（"近的先做"）——比一个黑盒启发式诚实得多。
    #    而且同权重的一批（20 台机器）全靠它拉开，否则前 N 条就是**随便挑的**。
    rows.sort(key=lambda r: (-r.verb.weight, r.dist))
    return rows


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


def _where(t) -> str:
    """目标那一格写清楚——**不带方位词猜**（"面前"要朝向数据，宁可写坐标）。"""
    if isinstance(t, dict) and isinstance(t.get("x"), int):
        return f"({t['x']},{t['y']})"
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


def _apply_groups(lv: Level) -> bool:
    """把一屏里的行按 `设备` / `家具` 拢成两块。**返回这一屏到底分没分组。**

    ⚠️ 只在**两块都非空**时才动 —— 农场那种清一色的图（只有农活、或只有家具）
       原样不动：排序是照权重精心排的，没必要的分组只会把"最近的要先做"这条理由搅浑。
    ⚠️ 组间先后按**组内最高权重**，不是写死"设备在前" —— 权重表才是"急不急"的正主。
    ⚠️ 只在 `act` 层分：选/填那一层是**刚点开的一小撮**，别在里面再分家。
    """
    if lv.mode != "act":
        return False
    parts = {g: [r for r in lv.rows if r.group == g] for g in _GROUPS}
    if not all(parts.values()):
        return False
    rest = [r for r in lv.rows if r.group not in _GROUPS]
    order = sorted(_GROUPS, key=lambda g: -max(r.verb.weight for r in parts[g]))
    lv.rows = [r for g in order for r in parts[g]] + rest
    return True


def _render_level(ctx: Ctx, lv: Level, n: int = 5) -> str:
    global _LAST_ROWS
    lines = []
    if lv.title:
        lines.append(lv.title)

    grouped = _apply_groups(lv)
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

        disp = r.label + (f" ×{len(r.targets)}" if len(r.targets) > 1 else "")
        if len(r.targets) == 1:
            # 单目标 → 印坐标（AI 可能想用别的工具精确指它）
            # ⚠️ **情境动词（`target="world"`）没有坐标**（2026-09-29 真机照出来的）：
            #    它的目标**就是 `None`**（"现在这个处境"，不属于任何一格），走 `_where(None)`
            #    会印成「**手持 20 只**（Rabbit×2、Duck×3…）」—— 牛成了"拿在手里"的。
            #    "手持"只对 `held` 动词成立：那一层 `None` **就是**手持那件。
            loc = (r.where if r.where is not None
                   else ("" if r.verb.target == "world" else _where(r.targets[0])))
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
    else:
        lines.append(" 0  做点别的…  （at x,y 指哪打哪）")
    # 提示优先取这一层自己的（`exec_on_pick` 那类形态跟默认不一样，得说清怎么敲）。
    lines.append(lv.hint or _LEVEL_HINT.get(lv.mode, _LEVEL_HINT["act"]))
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


def do_row(code, run: Callable, ctx: Ctx = None) -> str:
    """敲单子。`code` 可以是 `1` / `"1,4"` / `"1=1,4=4"` / `0`。

    ⚠️ **打的是上一次渲染出来的那一行**（AI 实际看见的），**不是重算的第 n 条**。
    重算出来的第 n 条可能**已经不是它看见的那条**了（它读单子的时候世界变了）
    ⇒ 它会以为在收钻石、我们收的却是翡翠，**而它会照做、不怀疑**。

    ⚠️ 今晚**不重新验 `can()`**：`/machine_collect` 自己会跳过已经收掉的机器，
       所以误敲的代价是"什么也没发生"（回执会报 collected=0）。
       **将来接了会"做错事"的动词（丢东西/送礼），这里必须先补验。**
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
        return "👌 好，做点别的去 —— 想指哪一样东西，用 at x,y"

    if lv.mode == "qty":
        return _do_qty(ctx, lv, sel, run)

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
    out = row.verb.exec(ctx, row.targets, run)
    _STACK[:] = _STACK[:1]          # 做完了 ⇒ 收回顶层
    return out


def render_at(ctx: Ctx, x: int, y: int) -> str:
    """🎯 指哪打哪——AI 自己指定目标，**绕过排序**，无损。

    ⚠️ 指到空的就**如实说"那里什么都没有"**，绝不给"附近有什么"。
    这是兜底的反面：编一个像样的答案比报错危害大得多。
    """
    t = ctx.tile(x, y)
    if not t:
        return f"📍 ({x},{y}) —— **那里什么都没有**（不在你能看到的范围内，或本来就没东西）"

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
    ready.sort(key=lambda v: -v.weight)

    title = head
    if not ready:
        title += "\n  （这里没有它能做的动作）"
    if pending:
        # ⏳ 这行仍是**缺口探测器**：AI 老指着某类东西而我们接不上 = 动词表欠的账。
        title += "\n  ⏳ 还没接执行：" + "、".join(v.label for v in pending)

    # ⚠️⚠️ **必须推栈**（2026-09-29 真机照出来的洞）：这一屏也有号、也写着"敲编号"，
    #    不推栈的话号是**悬空的** —— `do(1)` 会落到 `_STACK[-1]`，也就是**上一屏**的第 1 行。
    #    实测：`at 24 26`（椅子上）显示「1 坐 胡桃木椅子」，敲 `1` **却去收了机器**。
    #    **屏幕上有号、号指向别处** —— 正是 166③ 花大力气删掉的那类静默错误动作。
    #    ⇒ 走**和单子同一条造行路径**（`_row_for`），保证两屏的号是同一种东西。
    lv = Level([_row_for(ctx, v, [t]) for v in ready], title=title)
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
               seats: dict = None, furniture: dict = None, animals: dict = None) -> dict:
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

    # 🐄 牲畜（`/animals`）——`wasPetToday` **就在回包里**（"今天摸过没"问得到）。
    for a in (animals or {}).get("animals") or []:
        x, y = a.get("x"), a.get("y")
        if isinstance(x, int) and isinstance(y, int):
            tiles.setdefault((x, y), {"x": x, "y": y})["animal"] = a
    return tiles


def ctx_from(state: dict, surr: dict, machines: list = None, chests: list = None,
             caps: dict = None, seats: dict = None, furniture: dict = None,
             animals: dict = None, shop: dict = None) -> Ctx:
    """把 `/state`(**full**) + `/surroundings`(+`/machines`/`/scan_chests`) 拼成 Ctx。

    ⚠️ 只搬运，**不补默认值**：缺什么就让它缺着（`can()` 遇到缺失自然回 假/？）。
    ⚠️ `caps` **必须调用方给**（见 `Ctx.caps` 那段），这里不猜。
    ⚠️ `shop` 同理：**没开商店传 `None`**，开了但读不出来传一个空字典
       （两种"判不出来"在 `_buy_can`/`_sell_can` 里要分开，见 `Ctx.shop` 那段）。
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

    tiles = scan_world(surr, machines, chests, seats, furniture, animals)

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

    # 🐾 猫狗（宠物）：`/surroundings` 的 `npcs` 里 `kind == "pet"` 那几个。
    #    它们**不是** NPC、也不在 tiles 上 ⇒ 世界级（`target="world"` 的动词看这个）。
    pets = [n for n in ((surr or {}).get("npcs") or []) if n.get("kind") == "pet"]

    return Ctx(px=p.get("x") or 0, py=p.get("y") or 0,
               loc=((state or {}).get("location") or {}).get("name") or "",
               held=held, inv=inv, tiles=tiles,
               menu=(state or {}).get("activeMenu"),
               sitting=bool(((seats or {}).get("me") or {}).get("sitting")),
               pets=pets,
               stamina=p.get("stamina") or 0, max_items=p.get("maxItems") or 0,
               money=p.get("money") or 0,
               caps=caps or {}, zh=zh, shop=shop)


# ═══════════════════════════════════════════════════════════════════════
# ⑤ 不吃游戏自验（fixture 用**真实字段名**造的形，值全是占位的）
# ═══════════════════════════════════════════════════════════════════════
# ⚠️ 这是形，不是真值——别拿它当"验过了"。真值要等开游戏灌进去。

def _fixture():
    def item(idx, name, stack=1, cat=None, edible=None, sellable=True, val=0):
        return {"slotIndex": idx, "displayName": name, "stack": stack,
                "catNum": cat, "edibleValue": edible, "sellable": sellable,
                "value": val, "quality": 0}

    state = {"inventory": [
        item(2, "古书", cat=BOOK_CAT),        # ① 是书（catNum 问出来的）
        item(3, "草莓", stack=5, cat=-79, edible=20, val=120),
        item(4, "锄头", sellable=False),       # 老式：catNum 缺失 → 吃/看都该是 ？
    ]}
    inv = scan_backpack(state)
    tiles = {
        # 手推的形：字段名照抄 /surroundings 的真实输出，值全是占位的
        (12, 9): {"x": 12, "y": 9, "passable": False, "object": "木椅"},
        (13, 12): {"x": 13, "y": 12, "passable": True, "forage": True, "object": "野莓"},
        (11, 11): {"x": 11, "y": 11, "passable": True, "forage": True, "object": "野莓B"},
        (13, 11): {"x": 13, "y": 11, "passable": True, "forage": True, "object": "野莓C"},
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
               caps={"forage": True, "diggable": True, "harvestable": True})


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
    ok.append(("空地（键不在）→ CAN_NO", _pick_can(ctx, empty_tile) is CAN_NO))
    ok.append(("野莓 → CAN_YES", _pick_can(ctx, ctx.tiles[(13, 12)]) is CAN_YES))
    # ⚠️ 老 DLL：**连接级**不知道，不是逐格猜——这是那次修复的核心
    old = Ctx(px=12, py=12, tiles={}, caps={})
    ok.append(("老 DLL（caps 空）→ CAN_MAYBE，不进单子",
               _pick_can(old, ctx.tiles[(13, 12)]) is CAN_MAYBE))

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
    ok.append(("接了的动词在单子上", "收 已好的机器" in menu))
    # 🍽📖 2026-09-29：吃/看**接上执行了** ⇒ 手持那件（fixture 是古书）该出现
    ok.append(("接了的「看」在单子上（手持是书）", "看 古书" in menu))
    # 🌿🌾⛏ 同一天接的：捡/收作物（**聚合行**）+ 锄（逐格）
    ok.append(("接了的「捡」在单子上（聚合）", "捡 地上的东西" in menu))
    ok.append(("接了的「收作物」在单子上（聚合）", "收 成熟作物" in menu))
    ok.append(("接了的「锄」在单子上", "锄地" in menu))
    ok.append(("两台同产物 → 聚合成一行", "×2" in menu))
    ok.append(("产物摊在**理由**栏（Diamond×2）", "Diamond×2" in menu))
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
    at_empty = render_at(ctx, 99, 99)
    ok.append(("指到空 → 说「什么都没有」", "什么都没有" in at_empty))
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
    out = do_row(_no_of("收 已好的机器"), fake_run, ctx)
    ok.append(("do_row 打的是批量端点", bool(calls) and calls[0][0] == "machine_collect"))
    ok.append(("do_row 报实际收到几件", "收到 2 件" in out))
    ok.append(("敲越界的号 → 拒绝并给出路", "at x,y" in do_row(len(_LAST_ROWS) + 5, fake_run, ctx)))

    # ⑧ 背包满**不是成功**，且必须说清下一步（报缺了要给出路）
    def full_run(ep, payload):
        return {"ok": True, "collected": 1, "skippedFull": 7}

    render_menu(ctx, n=40)
    ok.append(("背包满 → 说清下一步",
               "先去卖或存" in do_row(_no_of("收 已好的机器"), full_run, ctx)))

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

    # (f) 同名两摞：**端点只按名字认** ⇒ 认不出的不列，且如实说（铁律 2）
    dupctx = _fixture()
    dupctx.tiles[(13, 13)]["chest"]["items"].append(
        {"name": "Diamond", "displayName": "钻石", "count": 5, "qualifiedId": "(O)72"})
    reset_menu()
    render_menu(dupctx, n=40)
    lv_dup = _open_box("矿石箱", fake_run, dupctx)
    ok.append(("同名两摞 ⇒ **如实说**挑出去了几摞", "同名但不同品质" in lv_dup))
    ok.append(("同名两摞确实没进候选", "钻石" not in do_row(_no_of("取"), fake_run, dupctx)))

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
    # 🛋 2026-09-29 恒拍板「移动家具做单行」⇒ 顶层只剩**一行**「搬走…」，
    #    家具一件一行挪到**下一层**（原来 30+ 行会把"收机器/开箱子"挤成"还有 N 项"）。
    ok.append(("🛋 顶层只有一行「搬走…」（不再一件一行）",
               "搬走…" in top2 and "搬走 红沙发" not in top2))
    ok.append(("🐾 摸动物只数**只算没摸过的**（2 头里 1 头摸过了）",
               "摸 还没摸的动物" in top2 and "1 只" in top2))
    ok.append(("🐾 猫狗那一行也在", "摸 猫狗" in top2))
    # ⚠️ **情境动词（`target="world"`）不许印「手持」**（2026-09-29 真机照出来）：
    #    它的目标**就是 `None`**，走 `_where(None)` ⇒ 印成「手持 20 只（Rabbit×2…）」，
    #    牛成了拿在手里的。`held` 动词印「手持」是对的（那层 `None` 就是手持那件）
    #    —— 判据得看 `verb.target`，不能看"目标是不是 None"（两者都是 None）。
    pet_line = next((l for l in top2.splitlines() if "摸 还没摸的动物" in l), "")
    ok.append(("🐾 情境动词那行不写「手持」", "手持" not in pet_line))
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

    reset_menu()
    render_menu(ctx, n=40)
    sub = do_row(_no_of("搬走"), act_run, ctx)     # 点开目录行 → 下一层发号
    ok.append(("🛋 点开「搬走…」→ 下一层把家具印出来", "搬走 红沙发" in sub))
    reset_menu()
    render_menu(ctx, n=40)
    do_row(_no_of("搬走"), act_run, ctx)           # 再点开一次，才有号可敲
    calls.clear()
    r_fur = do_row(_no_of("搬走 红沙发"), act_run, ctx)
    ok.append(("搬家具 走 `furniture_pickup`", any(c[0] == "furniture_pickup" for c in calls)))
    ok.append(("搬家具 不回编结果", "它自己的话" in r_fur))

    # 🗂 子层**要能看完**（2026-09-29 真机照出来的洞）：子层原来写死 `n=5`，而**没有翻页的口子**
    #    ⇒ 尾巴那句「还有 N 项（more）」是**空承诺**（`more` 压根不存在）。
    #    单独看只是难看；**跟"搬走收成一行"撞在一起就成了骗人**——41 件收进目录行、点开只给 5 件。
    many = dict(ctx.tiles)
    for i in range(9):
        many[(20 + i, 20)] = {"x": 20 + i, "y": 20,
                              "furniture": {"name": f"柜{i}", "x": 20 + i, "y": 20,
                                            "width": 1, "height": 1, "furnitureType": 0}}
    mctx = replace(ctx, tiles=many)
    reset_menu()
    render_menu(mctx, n=40)
    sub_many = do_row(_no_of("搬走"), act_run, mctx)
    ok.append((f"子层把 10 件家具**全印出来**（不是只给 5 条）",
               "还有" not in sub_many and sub_many.count("搬走 柜") == 9))

    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    do_row(_no_of("摸 还没摸的动物"), act_run, ctx)
    ok.append(("摸动物 走 `pet_animals`", any(c[0] == "pet_animals" for c in calls)))
    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    do_row(_no_of("摸 猫狗"), act_run, ctx)
    ok.append(("摸猫狗 走 `pet_pets`", any(c[0] == "pet_pets" for c in calls)))

    # ⚠️ 已经坐着 ⇒ **不该再给「坐」**（要先 scene stand 起身）
    sitctx = _fixture()
    sitctx.sitting = True
    reset_menu()
    ok.append(("坐着时不给「坐」的行", "坐 木椅" not in render_menu(sitctx, n=40)))
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

    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    r_hv = do_row(_no_of("收 成熟作物"), act_run, ctx)
    ok.append(("收作物 走 farm 域的**拟人** `harvest_crops`（不是 C# 的 /harvest）",
               any(c[0] == "harvest_crops" for c in calls)
               and not any(c[0] == "harvest" for c in calls)))
    ok.append(("收作物 半径 **25**（超 30 会被 C# 静默落回 10）",
               all(c[1].get("radius") == 25 for c in calls if c[0] == "harvest_crops")))

    reset_menu()
    render_menu(ctx, n=40)
    calls.clear()
    do_row(_no_of("锄地"), act_run, ctx)
    ok.append(("锄 走 `farm_till` 且**带坐标**（单格恒走拟人逐格）",
               any(c[0] == "farm_till" and c[1].get("x") is not None for c in calls)))
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
        return c

    sctx = _shopctx()
    reset_menu()
    top = render_menu(sctx, n=40)
    ok.append(("🏪 商店开着 ⇒ 「买」是**目录行**（句尾 `…`）", "买…" in top))
    ok.append(("🏪 顶层**只报几样**、不发号", "3 样" in top))
    ok.append(("🏪 理由栏给钱包（从游戏读的）", "钱包 1234g" in top))
    ok.append(("💰 「卖」也在（背包里有这家收的）", "卖…" in top))

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
