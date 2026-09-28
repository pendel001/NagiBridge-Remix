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

from dataclasses import dataclass, field
from typing import Callable, Optional

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
            "quality": i.get("quality", 0),
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
    stamina: int = 0
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
    name = t.get("cropRealName") or t.get("crop") or "作物"
    extra = ""
    if t.get("cropScythe"):
        extra = " · 得用镰刀"
    return f"{name} 已成熟{extra}"


def _harvest_show(ctx, t):
    return f"收 {t.get('cropRealName') or t.get('crop') or '作物'}"


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


def _open_can(ctx, t):
    """📦 开箱：**只认 `/scan_chests` 认过的**。

    ⚠️ 不拿 `object == "Chest"` 猜——那又是按名字认（本项目栽过无数次）。
    迷你出货箱也是 `IsStorageChest` 的排除对象（2026-09-12 定论：显式点名才认，别拆）。
    """
    if not t:
        return CAN_NO
    return CAN_YES if t.get("is_chest") else CAN_NO


def _open_reason(ctx, t):
    n = t.get("chest_items")
    return f"箱子（{n} 格）" if n is not None else "箱子"


def _open_show(ctx, t):
    return "开箱"


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
    return "手持 · " + " / ".join(parts)


def _eat_show(ctx, t):
    return f"吃 {t.get('name')}"


def _read_can(ctx, t):
    """📖 看书——**识别层有、判定层没有**的典型。

    "这是不是书" 问游戏问得到（`catNum == -102`）；
    但"**这本读不读得了**"（读过的书再读没反应）游戏没有事前判据，
    只能 `performUseAction()` 返回 false 才知道（反编译定论，2026-08-29）。
    ⇒ 这里按识别层进单子，**真失败了由回执如实报**「这本读过了，没反应」。
    """
    if not t:
        return CAN_NO
    if t.get("read_done"):
        return CAN_NO
    return is_book(t)


def _read_reason(ctx, t):
    # ⚠️ 别在这写"手持"——`_where()` 已经在前面写了一次，会印成"手持 手持"。
    return "是书（读没读过要读了才知道）"


def _read_show(ctx, t):
    return f"看 {t.get('name')}"


VERBS: list = [
    Verb("collect", "收 已好的机器", 88, _collect_can, _collect_reason, _collect_show, "tile",
         exec=_exec_collect, merge=True, reason_many=_collect_reason_many),
    # ⚠️ 下面这些**只有渲染没有执行**（`exec=None`）⇒ **今晚不上单子**。
    #    接执行要一个个来：`/harvest`、`/tool_area`、`/eat`、`/use mode=read` 端点都有，
    #    但都要在真机上验一遍"敲了之后到底发生什么"才敢放出来（v1 只放最稳的那个）。
    Verb("pick",    "捡",     90, _pick_can,    _pick_reason,    _pick_show,    "tile"),
    Verb("harvest", "收作物", 85, _harvest_can, _harvest_reason, _harvest_show, "tile"),
    Verb("dig",     "锄",     60, _dig_can,     _dig_reason,     _dig_show,     "tile"),
    Verb("open",    "开箱",   55, _open_can,    _open_reason,    _open_show,    "tile"),
    Verb("eat",     "吃",     50, _eat_can,     _eat_reason,     _eat_show,     "held"),
    Verb("read",    "看",     40, _read_can,    _read_reason,    _read_show,    "held"),
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
    ("open",     "开箱",    "要问游戏「这东西是不是容器」——`/surroundings` 现在只给 object 名，没给容器标记"),
    ("gift",     "送礼",    "要面前是 NPC + `tryToReceiveActiveObject`——问游戏有，但**是动作级的**（试了才知道收不收）"),
    ("machine",  "收机器",  "要 `readyForHarvest`——`/machines` 现成，搬进来即可"),
    ("pet",      "摸动物",  "要牲畜的「今天摸过没」——现成端点里没这字段"),
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


def _dist(ctx: Ctx, t) -> int:
    """走几步——**曼哈顿**。4 向移动下这才是"几步路"，直线距离会骗人。"""
    return abs((t.get("x") or 0) - ctx.px) + abs((t.get("y") or 0) - ctx.py)


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
        if v.target == "held":
            t = ctx.held
            if t and v.can(ctx, t) is True:
                buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)
        else:
            for t in ctx.tiles.values():
                if v.can(ctx, t) is True:
                    buckets.setdefault((v.key, None if v.merge else v.show(ctx, t)), []).append(t)

    rows = []
    for (vkey, label), targets in buckets.items():
        v = _VERB_BY_KEY[vkey]
        near = min(targets, key=lambda t: _dist(ctx, t))
        if v.merge:
            reason = v.reason_many(ctx, targets) if v.reason_many else v.reason(ctx, near)
        else:
            reason = v.reason(ctx, near)
        # 🗂 目录行的下一层**在这就算出来**（顶层要拿它报 `（N 件）`，也得知道它长不长）。
        rows.append(Row(verb=v, targets=targets,
                        label=label or v.label, reason=reason, dist=_dist(ctx, near),
                        level=v.subs(ctx, targets) if v.subs else None))
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


def _fingerprint(ctx: Ctx) -> tuple:
    """世界指纹——**只取会让子层单子作废的那两样**：换图 / 开了菜单。

    ⚠️ 刻意**不含坐标**：人走两步不该把手里那屏单子清掉。
    """
    return (ctx.loc, (ctx.menu or {}).get("type"))


def reset_menu():
    """把单子收回顶层（换场 / 断线重连时调）。"""
    _STACK[:] = []
    _LAST_ROWS.clear()


_LEVEL_HINT = {
    "act": "> 敲编号，或 at x,y",
    "pick": "> 敲编号，可以多选（`1,4`）",
    "qty": "> 写「号=数量」（`1=1,4=4`）—— **只写号不认**",
}


def _render_level(ctx: Ctx, lv: Level, n: int = 5) -> str:
    global _LAST_ROWS
    lines = []
    if lv.title:
        lines.append(lv.title)

    shown = lv.rows[:n]
    for i, r in enumerate(shown, 1):
        # ⚠️ 行号是**动作编号**，不是背包位次——两个数字混用就是"拿错尺子"。
        r.no = r.no if lv.keep_no else i
        if r.level is not None:
            # 🗂 目录行：句尾 `…` = 这行还要选。**只报数量，不发号**——号点开才印在眼前
            #    ⇒（a）AI 永远不用数数；（b）号不跨屏，"短命句柄"从风险变成设计。
            tail = f"{len(r.level.rows)} 件"
            if r.reason:
                tail += f" · {r.reason}"
            lines.append(f" {r.no}  {r.label}…   ← {tail}")
            continue

        disp = r.label + (f" ×{len(r.targets)}" if len(r.targets) > 1 else "")
        if len(r.targets) == 1:
            # 单目标 → 印坐标（AI 可能想用别的工具精确指它）
            loc = r.where if r.where is not None else _where(r.targets[0])
            tail = f"{loc} {r.reason}".strip()
        else:
            # 多目标 → 坐标**省掉**（省了才有你说的那个效果），只给"几处 + 最近几格"。
            # ⚠️ 但**不能不给定位信息**——否则 AI 不知道这一敲要跑多远。
            # ⚠️ 距离说「**步**」，不说「格」——「格」在这条线上另有含义（箱子 22 格）。
            #    同屏两个"格"是两个意思，就是"拿错尺子"的温床（2026-09-27 真机照出来的）。
            tail = f"{len(r.targets)} 处 · 最近 {r.dist} 步 · {r.reason}".rstrip()
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
        lines.append(f"—— 还有 {hidden} 项（more）")     # 铁律 2：不许静默截断

    if len(_STACK) > 1:
        lines.append(" 0  这些都不是（返回上一层）")     # 与顶层 `0` 同义：这些都不是
    else:
        lines.append(" 0  做点别的…  （at x,y 指哪打哪）")
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
    return _render_level(ctx, _STACK[-1], n)


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


def _open_qty(lv: Level, chosen: list):
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
    _STACK.append(sub)
    return sub, None


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

    # ── pick 层：可以一次选多个（`1,4`）→ 进 qty 层 ──────────────
    if lv.mode == "pick":
        sub, e = _open_qty(lv, [n for n, _ in sel])
        return e if e else _render_level(ctx, sub, 5)

    no = sel[0][0]
    row = _row_by_no(lv, no)
    if row is None:
        return (f"❌ 这一层只有 {_nos(lv)} 号 —— 没有 {no} 号。\n"
                f"   想指别的东西，用 at x,y")

    # 🗂 目录行：点开下一层（**本身不执行任何东西**）
    if row.level is not None:
        _STACK.append(row.level)
        return _render_level(ctx, row.level, 5)

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
    name = (t.get("object") or t.get("cropRealName") or t.get("crop")
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
            (ready if v.exec else pending).append(v)
    ready.sort(key=lambda v: -v.weight)

    lines = [head]
    if not ready:
        lines.append("  （这里没有它能做的动作）")
    for i, v in enumerate(ready, 1):
        lines.append(f" {i}  {v.show(ctx, t)}   ← {v.reason(ctx, t)}")
    if pending:
        lines.append("  ⏳ 还没接执行：" + "、".join(v.label for v in pending))
    lines.append(" 0  返回")
    return "\n".join(lines)


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


def scan_world(surr: dict, machines: list = None, chests: list = None) -> dict:
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
    return tiles


def ctx_from(state: dict, surr: dict, machines: list = None, chests: list = None,
             caps: dict = None) -> Ctx:
    """把 `/state`(**full**) + `/surroundings`(+`/machines`/`/scan_chests`) 拼成 Ctx。

    ⚠️ 只搬运，**不补默认值**：缺什么就让它缺着（`can()` 遇到缺失自然回 假/？）。
    ⚠️ `caps` **必须调用方给**（见 `Ctx.caps` 那段），这里不猜。
    """
    p = (state or {}).get("player") or {}
    inv = scan_backpack(state)

    # 手持：游戏 `/state` 只给 `currentTool` **名字**（不给槽位）。
    # ⚠️⚠️ 比的是 **`raw.name`（英文内部名）**，不是 `displayName`（中文）——
    #    2026-09-27 真机第一次跑就栽在这：`currentTool="Galaxy Hammer"`
    #    而显示名是"银河之锤"，拿显示名比 ⇒ **手持恒为 None**，静默。
    #    （fixture 编不出这个 bug：我编的两边是自洽的。这就是真机的价值。）
    # ⚠️ 名字仍可能重（两把同名工具）⇒ 这里是**近似**。真接进服务前换成 C# 的手持槽位。
    #    找不到就**没有手持**（不猜一个最像的）。
    held = None
    ct = p.get("currentTool")
    if ct:
        for it in inv:
            if (it.get("raw") or {}).get("name") == ct:
                held = it
                break

    tiles = scan_world(surr, machines, chests)

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

    return Ctx(px=p.get("x") or 0, py=p.get("y") or 0,
               loc=((state or {}).get("location") or {}).get("name") or "",
               held=held, inv=inv, tiles=tiles,
               menu=(state or {}).get("activeMenu"),
               stamina=p.get("stamina") or 0, caps=caps or {}, zh=zh)


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
                   "cropRealName": "萝卜", "cropScythe": False},
        (12, 11): {"x": 12, "y": 11, "passable": True},   # 空地：什么都不该出
        # 两台出同样的东西 —— 专门用来验「聚合成一行」
        (14, 12): {"x": 14, "y": 12, "machine": {"status": "ready", "item": "Diamond"}},
        (15, 12): {"x": 15, "y": 12, "machine": {"status": "ready", "item": "Diamond"}},
    }
    return Ctx(px=12, py=12, loc="FarmHouse", inv=inv, held=inv[0],
               tiles=tiles, stamina=268,
               caps={"forage": True, "diggable": True, "harvestable": True})


def _selftest():
    ok = []
    reset_menu()          # 单子是一叠，会跨用例留下来 —— 每个用例开头自己清
    ctx = _fixture()

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
    menu = render_menu(ctx, n=5)
    ok.append(("没接执行的动词不上单子（野莓/萝卜/古书都不该出现）",
               "野莓" not in menu and "萝卜" not in menu and "古书" not in menu))
    ok.append(("接了的动词在单子上", "收 已好的机器" in menu))
    ok.append(("两台同产物 → 聚合成一行", "×2" in menu))
    ok.append(("产物摊在**理由**栏（Diamond×2）", "Diamond×2" in menu))
    ok.append(("单子带理由列", "←" in menu))
    ok.append(("单子留 0 出口", "做点别的" in menu))

    # ④ 铁律 2：不许静默截断
    # ⚠️ 得**临时**给 pick 接个占位 exec——否则单子永远只有 1 行，这条根本测不到。
    #    （这是"只列能执行的"的副作用：可测性被单子长度绑住了，记着。）
    pick = _VERB_BY_KEY["pick"]
    pick.exec = lambda c, ts, run: "（自验占位）"
    _total = len(_candidates(ctx))
    m1 = render_menu(ctx, n=1)
    ok.append(("超出 N 条如实报「还有 K 项」", "还有" in m1))
    # ⚠️ 别把条数写死——第一次写"3 个野莓 - 1 = 2"就漏算了 collect 那行也在单子上。
    #    算出来再比，尺子才跟着代码走。
    ok.append((f"报的数对得上（共 {_total} 条 - 显示 1）", f"还有 {_total - 1} 项" in m1))
    ok.append(("没超 N 条时不该报", "还有" not in render_menu(ctx, n=9)))
    pick.exec = None

    # ⑤ 指哪打哪：接没接**都列**，但分开列（`at` 是问句不是动作面）
    at_empty = render_at(ctx, 99, 99)
    ok.append(("指到空 → 说「什么都没有」", "什么都没有" in at_empty))
    ok.append(("指到空 → 不编「附近有」", "附近" not in at_empty))
    at_bush = render_at(ctx, 13, 12)
    ok.append(("指到野莓 → 出「捡」", "捡" in at_bush))
    ok.append(("指到野莓 → 同时标明还没接执行", "还没接执行" in at_bush))

    # ⑥ 回执必须回显对象
    rc = render_receipt("卖出", "草莓×5", True, "+600g", "背包③ 现在是 菠萝×2")
    ok.append(("回执回显对象/数量", "草莓×5" in rc))
    ok.append(("回执回显新槽位", "菠萝×2" in rc))

    # ⑦ 敲单子——**假 run**，一个字节都不碰游戏
    calls = []

    def fake_run(ep, payload):
        calls.append((ep, payload))
        return {"ok": True, "collected": 2, "skippedFull": 0}

    render_menu(ctx, n=5)                      # 先看一眼，才有单子可敲
    out = do_row(1, fake_run, ctx)
    ok.append(("do_row 打的是批量端点", bool(calls) and calls[0][0] == "machine_collect"))
    ok.append(("do_row 报实际收到几件", "收到 2 件" in out))
    ok.append(("敲越界的号 → 拒绝并给出路", "at x,y" in do_row(len(_LAST_ROWS) + 5, fake_run, ctx)))

    # ⑧ 背包满**不是成功**，且必须说清下一步（报缺了要给出路）
    def full_run(ep, payload):
        return {"ok": True, "collected": 1, "skippedFull": 7}

    render_menu(ctx, n=5)
    ok.append(("背包满 → 说清下一步", "先去卖或存" in do_row(1, full_run, ctx)))

    # ⑨ 不崩：空世界 + 没单子就敲
    render_menu(Ctx())
    ok.append(("空世界不崩", True))

    # ⑩ 目录行 / 多选 / 配对（0928 定稿）—— 用一个**假买卖动词**演一遍，一个字节不碰游戏。
    #    ⚠️ 这是**形**，不是真买卖。真买卖要等商店那条路（#9）。
    shop = Verb("buy", "买", 70, lambda c, t: True, lambda c, t: "店里有货",
                lambda c, t: "买", "tile")

    def _shop_subs(c, ts):
        rows = [Row(shop, [{"good": g, "price": p}], g, f"{p}g", 0, where="")
                for g, p in [("鲤鱼", 30), ("鲫鱼", 40), ("蚌", 60), ("蛤蜊", 10)]]
        return Level(rows, title="买哪几样？（可以多选，如 `1,4`）",
                     mode="pick", verb=shop)

    def _shop_exec_multi(c, chosen, run):
        bits = " · ".join(f"{r.label}×{n}（{r.targets[0]['price'] * n}g）" for r, n in chosen)
        total = sum(r.targets[0]["price"] * n for r, n in chosen)
        return render_receipt("买", bits, True, note=f"共 {total}g")

    shop.subs, shop.exec_multi = _shop_subs, _shop_exec_multi
    VERBS.append(shop)
    _VERB_BY_KEY["buy"] = shop
    try:
        sctx = Ctx(px=5, py=5, loc="SeedShop", tiles={(5, 6): {"x": 5, "y": 6}})
        reset_menu()
        top = render_menu(sctx, n=5)
        ok.append(("🗂 目录行句尾带 `…`", "买…" in top))
        ok.append(("🗂 顶层**只报数量**、不发号", "4 件" in top))

        n_before = len(calls)
        sub = do_row(1, fake_run, sctx)
        ok.append(("敲目录行 → 进下一层，**本身什么都不做**",
                   "买哪几样" in sub and len(calls) == n_before))
        ok.append(("子层的号印在眼前（AI 不用数）", "1  鲤鱼" in sub))

        q = do_row("1,4", fake_run, sctx)
        ok.append(("多选 `1,4` → 进「各多少」那层", "各多少" in q))
        ok.append(("qty 层**沿用**上层发的号", " 1  鲤鱼" in q and " 4  蛤蜊" in q))

        # ⚠️⚠️ 这条就是今晚那个**静默陷阱**的正身：qty 层只写号，必须**拒**，不许按位置对齐。
        bad = do_row("1,4", fake_run, sctx)
        ok.append(("⚠️ qty 层只写号 → **拒**（写反了会买对东西买错数量）",
                   "号=数量" in bad and "❌" in bad))

        ok.append(("子层 `0` → 回上一层", "鲤鱼" in do_row(0, fake_run, sctx)))
        do_row("1,4", fake_run, sctx)                 # 再选一次，这回真买
        done = do_row("1=1,4=4", fake_run, sctx)
        ok.append(("配对 `1=1,4=4` → 执行", "鲤鱼×1" in done and "蛤蜊×4" in done))
        ok.append(("回执**逐条列**、带小计", "30g" in done and "40g" in done and "共 70g" in done))

        # 形态不对 / 混写：**一律报错，不猜**
        reset_menu()
        render_menu(sctx, n=5)
        ok.append(("混着写 → 拒", "混了两种写法" in do_row("1,4=4", fake_run, sctx)))
        render_menu(ctx, n=5)
        ok.append(("act 层写数量 → 拒", "没有数量要填" in do_row("1=1", fake_run, ctx)))
        ok.append(("act 层多选 → 拒", "一次只能敲一个" in do_row("1,2", fake_run, ctx)))
        ok.append(("越界的号 → 拒并给出路", "at x,y" in do_row(97, fake_run, ctx)))
    finally:
        VERBS.remove(shop)
        _VERB_BY_KEY.pop("buy", None)
        reset_menu()

    print("\n—— 意图选项单 · 不吃游戏自验 ——")
    for name, good in ok:
        print(("  ✅ " if good else "  ❌ ") + name)
    bad = [n for n, g in ok if not g]
    print(f"\n{len(ok) - len(bad)}/{len(ok)} 过")
    if bad:
        print("未过：" + " / ".join(bad))

    print("\n—— 样例单（形，值全是占位的）——")
    print(render_menu(ctx, header="🎯 FarmHouse (12,12) · 🔋268"))
    print("\n> at 12,13   （接没接执行都列，分开列）")
    print(render_at(ctx, 12, 13))
    return len(bad) == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
