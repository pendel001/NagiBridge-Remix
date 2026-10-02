# -*- coding: utf-8 -*-
"""🌳 树种判定 + 「砍树放行名单」（恒 2026-09-12 拍板）。

**为什么有它**：`chop_trees.py` 的 docstring 一直写"可砍 Tree:1~3"，**代码却是 `startswith("Tree:")` 全收**
——真机无伤实测（只调 `find_trees()`，纯 `/surroundings` 读）：站在恒的蘑菇树堆旁，**最近的可砍目标就是
`Tree:7`**，`farm 砍树` 一跑第一斧就砍掉蘑菇树（Farm 上有 15 棵）。`clear_area.py` 同一个口子。
恒拍板：**特殊树种默认保护**，要砍得**显式放行**；放行名单存 settings（`settings chop ...`）。

**分类**（反编译 `StardewValley.TerrainFeatures/Tree.cs` 实锤）：
· 普通树（永远可砍）：`1` 橡 / `2` 枫 / `3` 松。
  ⚠️ `4`/`5` 是**冬态马甲**，构造函数里就归一成 `1`/`2`（Tree.cs:178-185），不会存续到存档；
  `setSeason()`（Tree.cs:1611）只改 `localSeason`、**不动 `treeType`** ⇒ 不需要给它们留位置。
· 特殊树（默认保护）：`7` 蘑菇树 / `8` 桃花心木 / `10`~`12` 苔雨树 / `13` 神秘树 / `6`·`9` 棕榈。
· **认不出的类型一律当"保护"处理** —— 宁报错别兜底：宁可少砍，也不猜着砍掉东西。

放行值写法：`settings chop 蘑菇树,桃花心木` / `settings chop 7` / `settings chop 苔雨树`
（一个名字可对应多个 id，如苔雨树=10,11,12）；`none` 收回；`all` 全放行（慎用）。
"""
import re

# 树种 id → 中文名（跟游戏里 `Tree.cs` 的 `public const string xxxTree` 一一对）
TREE_NAMES = {
    "1": "橡树", "2": "枫树", "3": "松树",
    "6": "棕榈树", "7": "蘑菇树", "8": "桃花心木",
    "9": "棕榈树", "10": "苔雨树", "11": "苔雨树", "12": "苔雨蕨树",
    "13": "神秘树",
}

# 永远可砍的普通树
BASE_CHOPPABLE = ("1", "2", "3")

# 🌲 苔雨树（10~12）——**只在农场里受保护**（恒 2026-10-02，见 `is_choppable` 那段）
GREEN_RAIN_TYPES = ("10", "11", "12")
FARM_MAPS = {"Farm"}

# 说人话的输入别名 → id 列表（中文/英文/简称都能写）
NAME2IDS = {
    "橡树": ["1"], "橡": ["1"], "oak": ["1"],
    "枫树": ["2"], "枫": ["2"], "maple": ["2"],
    "松树": ["3"], "松": ["3"], "pine": ["3"],
    "蘑菇树": ["7"], "蘑菇": ["7"], "mushroom": ["7"],
    "桃花心木": ["8"], "红木": ["8"], "mahogany": ["8"],
    "苔雨树": ["10", "11", "12"], "绿雨树": ["10", "11", "12"], "greenrain": ["10", "11", "12"],
    "神秘树": ["13"], "mystic": ["13"],
    "棕榈树": ["6", "9"], "棕榈": ["6", "9"], "palm": ["6", "9"],
}


def tree_type_of(terrain: str) -> str:
    """`"Tree:7"` → `"7"`（不是树就返回空串）。"""
    t = terrain or ""
    return t.split(":", 1)[1] if t.startswith("Tree:") else ""


def tree_name(tree_type: str) -> str:
    """`"7"` → `"蘑菇树"`；认不出就老实说认不出（别拿"树"糊弄过去）。"""
    return TREE_NAMES.get(tree_type or "", f"未知树种({tree_type})")


def parse_allow(text: str):
    """解析放行值 → `(allow, err)`。`allow=[]`=只砍普通树；`['all']`=全放行；否则 id 列表。

    ⚠️ 认不出来就**报错**、不静默当成空（恒：宁报错别兜底）—— 否则"我以为放行了、其实没放"最难查。
    """
    t = (text or "").strip()
    if not t:
        return [], None
    low = t.lower()
    if low in ("none", "off", "no", "clear", "清空", "默认", "收回", "取消"):
        return [], None
    if low in ("all", "全部", "全", "所有", "*"):
        return ["all"], None
    out, bad = [], []
    for tok in re.split(r"[,，、\s/]+", t):
        if not tok:
            continue
        if tok in TREE_NAMES:
            out.append(tok)
            continue
        ids = NAME2IDS.get(tok) or NAME2IDS.get(tok.lower())
        if ids:
            out.extend(ids)
            continue
        bad.append(tok)
    if bad:
        avail = "、".join(sorted(set(TREE_NAMES.values())))
        return [], (f"认不出的树种：{'、'.join(bad)}"
                    f"（能写的：{avail}；或树号 1~13；或 none/all）")
    # 按树号数字序排（字符串序会把 "13" 排到 "7" 前面，回给恒看很别扭）
    return sorted(set(out), key=lambda s: (0, int(s)) if s.isdigit() else (1, s)), None


def is_choppable(tree_type: str, allow, loc: str = None) -> bool:
    """这个树种现在准不准砍。`allow` = `parse_allow()` 的产物；`loc` = 当前地图名。

    ⚠️ **农场之外不保护苔雨树**（恒 2026-10-02：「**不要保护农场之外的绿雨树，免得绿雨天收集不了
       苔藓了**」）。为什么：苔雨树的保护本意是"留着长苔藓"（农场里当苔藓来源）；可**绿雨天全谷
       都会长苔雨树**，在森林/后山那种地方还挡着 = 那天**收不了苔藓**（斧头砍正是收苔藓的手势，
       见 `moss_run.py`：`greenRainTree` → `axe`）。
    ⚠️ `loc` **不传 / 传空 ⇒ 按老规矩保护**（不知道自己在哪就别乱砍 —— 宁少砍，别猜）。
    """
    if tree_type in BASE_CHOPPABLE:
        return True
    if "all" in (allow or ()):
        return True
    if tree_type in (allow or ()):
        return True
    if loc and tree_type in GREEN_RAIN_TYPES and loc not in FARM_MAPS:
        return True
    return False


def scope_note() -> str:
    """一行说清"保护/放行"的**适用范围**（设置页/报表用 —— 判据只此一处，别处别抄一遍）。"""
    return ("苔雨树(10~12)只在「Farm」受保护；**农场外一律可砍**"
            "（绿雨天那些树上挂着苔藓，砍下来才收得到）")


def allow_label(allow) -> str:
    """一行说清"现在能砍啥"——报表/设置页都用它，别让人猜。"""
    allow = allow or []
    if not allow:
        return "只砍 橡/枫/松"
    if "all" in allow:
        return "全部树种（含特殊树，慎用）"
    names = []
    for i in allow:
        n = tree_name(i)
        if n not in names:
            names.append(n)
    return "橡/枫/松 + " + "、".join(names)


def skipped_summary(skipped: dict) -> str:
    """`{"7": 13, "8": 2}` → `蘑菇树×13、桃花心木×2`（按数量降序）。"""
    if not skipped:
        return ""
    items = sorted(skipped.items(), key=lambda kv: -kv[1])
    return "、".join(f"{tree_name(k)}×{v}" for k, v in items)
