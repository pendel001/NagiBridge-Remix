# -*- coding: utf-8 -*-
"""🍷 地窖只算「走得进去」的 —— 264 个幽灵木桶（8 间进不去的地窖 × 33 个 Cask）不许再报出来。

2026-11-07 恒真机（`/farm_report` 报 `total=267`）：
  · 拆开是：Farm 上 **3 台 Furnace** + **Cellar / Cellar2 … Cellar8 各 33 个 Cask**（264 个木桶）
  · `/farm_buildings` 只有 Farmhouse + Greenhouse + Shipping Bin + Pet Bowl + **1 间 Cabin**
    ⇒ 最多 2 个地窖；存档 XML 里 `xsi:type="Cellar"` 却是 **8 个**，
    且每个都是 `<upgradeLevel>0</upgradeLevel>`（**房子从没升级过 ⇒ 一个地窖都进不去**）
  · 根因：`ModEntry.ResolveLocations` 把 `Game1.locations` 里**所有** Cellar 都当成「全农场」的一部分
    （地窖是独立 location，不在 `farm.buildings` 里，所以被单独扫了一遍）

判据（`ModEntry.IsReachableCellar`，两条满足任一）：
  ① **能从现存住宅走到**：`FarmHouse` 或农场里每栋 `Building` 的 `indoors`，只要满足任一：
     ⓐ 它们的 `warps` 里有 `TargetName` 指到**这间**地窖（`Cellar` / `Cellar2` …，
       见 `FarmHouse.GetCellarName()`）；
     ⓑ 它们的**瓦片属性**里有 `TouchAction: Warp <这间地窖> <x> <y>`
       —— 1.6 的地窖入口其实是这个（`FarmHouse.updateCellarWarps()` 改的是 Back 层 `TouchAction`），
       而 `cellarWarps` 字段原生从不填充 ⇒ 只看 `.warps` 会**漏掉 Cabin 升级后的地窖**。
  ② **兜底**：`Game1.player.HouseUpgradeLevel >= 3` 且这间 `Name == "Cellar"`。
  读不到/异常 ⇒ **false**（宁可少报一间，也绝不把假数据再报出来）。

这个钉子**离线**跑（不开游戏），钉三件事：
  Ⓐ C# 源码钉：`ResolveLocations` 不再是「无条件 `loc is Cellar`」，走的是新判据；
  Ⓑ 判据函数存在、两条分支（warp ① / `HouseUpgradeLevel` ②）都写在源码切片里；
  Ⓒ **行为钉**：把 C# 判据逐行翻成 Python 小函数 + 假存档场景
     （本存档现场 / 房子升级过 / 只有 Cabin 有门指 `Cellar2` / **Cabin 升级后瓦片上有
     `TouchAction: Warp Cellar2 5 3`**）。
     ⚠️ 复刻与 C# **一一对应**（`# C# ①` / `# C# ②` 就是对应标记，见 `is_reachable_cellar`）——
     这是「离线验不了的 C# 行为」的常用手法（同 `_load_machine_filter_selftest.py`）。
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _read(path):
    return open(path, encoding="utf-8", errors="replace").read()


def _cut_func(src, sig):
    """从 `sig` 那段开始按花括号配平切出**整段函数**（够做源码钉；字符串/行注释里的括号不算）。"""
    i = src.find(sig)
    if i < 0:
        return ""
    j = src.find("{", i)
    if j < 0:
        return ""
    depth, k, quote = 0, j, None
    while k < len(src):
        c = src[k]
        if quote:
            if c == "\\":
                k += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif src.startswith("//", k):
            nl = src.find("\n", k)
            k = len(src) if nl < 0 else nl
            continue
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
        k += 1
    return src[i:]


def _cut_py_func(src, sig):
    """从 Python 的 `def` 行切到下一条**顶格 def** 之前（够做「复刻对应」源码钉）。"""
    i = src.find(sig)
    if i < 0:
        return ""
    lines = src[i:].splitlines()
    out = [lines[0]]
    for ln in lines[1:]:
        if ln.startswith("def "):
            break
        out.append(ln)
    return "\n".join(out)


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# 假存档：只留判据真会用到的几样东西（地点名 / 是不是 Cellar / warps / 住宅的 indoor）
# ─────────────────────────────────────────────────────────────────────────────
class FakeLoc:
    """假的 GameLocation。`kind` 对应存档里的 `xsi:type`；`casks`/`furnaces` 只拿来数机器。

    `touch_tiles` 是**瓦片表**：`{(层名, x, y): "Warp Cellar2 5 3"}`，只收 `TouchAction` 那一格属性
    （C# 侧就是 `Back` / `Buildings` 两层的 `Tiles[x, y].Properties["TouchAction"]`）。
    """

    def __init__(self, name, kind="GameLocation", warps=None, casks=0, furnaces=0,
                 unique_name=None, touch_tiles=None):
        self.name = name
        self.kind = kind
        self.warps = list(warps or [])
        self.casks = casks
        self.furnaces = furnaces
        self.unique_name = unique_name or name
        self.touch_tiles = dict(touch_tiles or {})

    @property
    def is_cellar(self):
        return self.kind == "Cellar"

    def machine_count(self):
        return self.furnaces + self.casks


class BrokenLoc:
    """读 `.warps` 就炸的假住宅 —— 对照 C# 那条 `catch ⇒ return false`（保守）。"""

    name = "FarmHouse"

    def __init__(self, name="FarmHouse"):
        self.name = name
        self.touch_tiles = {}

    @property
    def warps(self):
        raise RuntimeError("读不到 warps")


class FakeBuilding:
    """假的 `Building`：判据只问它的 `indoors`。"""

    def __init__(self, indoors=None):
        self.indoors = indoors


class FakeSave:
    def __init__(self, farm, buildings, locations, farmhouse, upgrade=0, has_player=True):
        self.farm = farm
        self.buildings = [FakeBuilding(x) for x in buildings]
        self.locations = list(locations)
        self.farmhouse = farmhouse
        self.house_upgrade_level = upgrade
        self.has_player = has_player

    def entrance_locations(self):
        """C#：`Game1.getLocationFromName("FarmHouse")` + `farm.buildings` 每栋的 `indoors`。"""
        out = []
        if self.farmhouse is not None:
            out.append(self.farmhouse)
        for b in self.buildings:
            if b.indoors is not None:
                out.append(b.indoors)
        return out


# ─────────────────────────────────────────────────────────────────────────────
# C# 判据的 Python 复刻（⚠️ 一一对应：改 C# 请顺手改这里）
# ─────────────────────────────────────────────────────────────────────────────
def warp_targets_cellar(target_name, cellar):
    """C# `WarpTargetsCellar(string? targetName, GameLocation cellar)`。"""
    # C#：`if (string.IsNullOrEmpty(targetName)) return false;`
    if not target_name:
        return False
    # C#：`if (!targetName.StartsWith("Cellar", StringComparison.OrdinalIgnoreCase)) return false;`
    if not target_name.lower().startswith("cellar"):
        return False
    # C#：`cellar.Name.Equals(targetName, OrdinalIgnoreCase) || cellar.NameOrUniqueName.Equals(...)`
    #     —— 只认**相等**，不认前缀（一放宽 `Cellar` 就把 `Cellar2…Cellar8` 全串进来）
    return (target_name.lower() == cellar.name.lower()
            or target_name.lower() == cellar.unique_name.lower())


def touch_action_targets_cellar(touch_action, cellar):
    """C# `TouchActionTargetsCellar(string? touchAction, GameLocation cellar)`。

    `TouchAction: Warp <地点> <x> <y>`（`performTouchAction` 的 `case 4:` ⇒ 地点名 = 第 2 个 token）。
    """
    # C#：`if (string.IsNullOrEmpty(touchAction)) return false;`
    if not touch_action:
        return False
    # C#：`var parts = touchAction.Split(' ');` —— 保留空 token（和 C# 的 Split(' ') 一模一样）
    parts = touch_action.split(" ")
    # C#：`if (parts.Length < 2) return false;`
    if len(parts) < 2:
        return False
    # C#：`if (parts[0] != "Warp" && parts[0] != "MagicWarp") return false;`
    if parts[0] not in ("Warp", "MagicWarp"):
        return False
    target_name = parts[1]
    # C#：`if (string.IsNullOrEmpty(targetName)) return false;`
    if not target_name:
        return False
    # C#：`cellar.Name.Equals(targetName, …) || cellar.NameOrUniqueName.Equals(...)`
    #     —— 只认**相等**（`Warp Cellar2 5 3` 绝不能被 `Cellar` 认走）
    return (target_name.lower() == cellar.name.lower()
            or target_name.lower() == cellar.unique_name.lower())


def tiles_warp_to_cellar(home, cellar):
    """C# `TilesWarpToCellar(GameLocation home, GameLocation cellar)`。"""
    # C#：`if (home.Map == null) return false;`
    #   —— 复刻里没有 Map，`home is None` 就是「这栋住宅根本没有瓦片表」那一档
    if home is None:
        return False
    # C#：`foreach (var layerName in new[] { "Back", "Buildings" })` —— 两层都扫
    for layer in ("Back", "Buildings"):
        for (lay, _x, _y), raw in home.touch_tiles.items():
            if lay != layer:
                continue
            # C#：`layer.Tiles[x, y]?.Properties["TouchAction"]`（取不到就跳过）
            if touch_action_targets_cellar(raw, cellar):
                return True
    return False


def is_reachable_cellar(loc, save):
    """C# `IsReachableCellar(GameLocation? loc)`。"""
    # C#：`if (loc is not Cellar) return false;`
    if not loc.is_cellar:
        return False
    try:
        # C# ①：住宅（FarmHouse + 每栋 Building 的 indoors）的 warps 里，有 TargetName 指到**这间**
        for src in save.entrance_locations():
            for target_name in src.warps:
                if warp_targets_cellar(target_name, loc):
                    return True
            # C# ①（瓦片）：`TouchAction: Warp <地窖> <x> <y>` 也算门
            #   —— 必须放在 `warps` 之外：warp 表空不代表瓦片上没门（Cabin 升级后就是这样）
            if tiles_warp_to_cellar(src, loc):
                return True
        # C# ②：本机房子升级到 3 级（地窖那档）才有地窖，且这间得是 `Cellar`
        if save.has_player and save.house_upgrade_level >= 3 and loc.name == "Cellar":
            return True
    except Exception:
        # C#：`catch { return false; }` —— 读不到/炸了 ⇒ 保守
        return False
    return False


def resolve_locations(save, is_reachable=is_reachable_cellar):
    """C# `ResolveLocations("")` 的复刻：farm + 每栋建筑 indoors + **走得进去的**地窖。"""
    out = []
    if save.farm is not None:
        out.append(save.farm)
    for b in save.buildings:
        if b.indoors is not None and b.indoors not in out:
            out.append(b.indoors)
    for loc in save.locations:
        if is_reachable(loc, save) and loc not in out:
            out.append(loc)
    return out


# ── 黑化对照（证明下面这些场景**真能咬到**坏判据）──────────────────────────
def _bad_unconditional(loc, _save):
    """黑化版：无条件收所有 Cellar（就是被打回的老写法）。"""
    return loc.is_cellar


def _bad_no_upgrade(loc, save):
    """黑化版：把 ② `HouseUpgradeLevel` 那条兜底删掉。"""
    if not loc.is_cellar:
        return False
    for src in save.entrance_locations():
        for target_name in src.warps:
            if warp_targets_cellar(target_name, loc):
                return True
    return False


def _bad_no_touch(loc, save):
    """黑化版：把 ① 里 **TouchAction（瓦片）那条分支删掉** —— 负向刀 B 的离线版。"""
    if not loc.is_cellar:
        return False
    try:
        for src in save.entrance_locations():
            for target_name in src.warps:
                if warp_targets_cellar(target_name, loc):
                    return True
        if save.has_player and save.house_upgrade_level >= 3 and loc.name == "Cellar":
            return True
    except Exception:
        return False
    return False


def _bad_touch_prefix(loc, save):
    """黑化版：瓦片里解析出的地点名改用**前缀**比
    （`Warp Cellar2 5 3` 就会被 `Cellar` 认走 = 8 间幽灵地窖全回来的那种写法）。"""
    if not loc.is_cellar:
        return False
    try:
        for src in save.entrance_locations():
            for target_name in src.warps:
                if warp_targets_cellar(target_name, loc):
                    return True
            for (_lay, _x, _y), raw in src.touch_tiles.items():
                parts = raw.split(" ")
                if len(parts) < 2 or parts[0] not in ("Warp", "MagicWarp"):
                    continue
                low = parts[1].lower()
                if low.startswith(loc.name.lower()):   # 黑化点：前缀
                    return True
        if save.has_player and save.house_upgrade_level >= 3 and loc.name == "Cellar":
            return True
    except Exception:
        return False
    return False


# ── 假存档场景（本档现状 / 升级 / Cabin warp / Cabin 瓦片 …）────────────────
def _scene_this_save():
    """**本存档现场**：Farm 3 台 Furnace；Cellar / Cellar2…Cellar8 各 33 个 Cask；
    房子 `<upgradeLevel>0</upgradeLevel>`、**一条 Cellar warp 都没有**。"""
    cellars = [FakeLoc("Cellar", "Cellar", casks=33)]
    cellars += [FakeLoc(f"Cellar{i}", "Cellar", casks=33) for i in range(2, 9)]
    return FakeSave(farm=FakeLoc("Farm", "Farm", furnaces=3),
                    buildings=[FakeLoc("Cabin", "Cabin")],
                    locations=cellars,
                    farmhouse=FakeLoc("FarmHouse", "FarmHouse"),
                    upgrade=0)


def _scene_upgraded():
    """房子升到 3 级（有地窖那档），但 warp 表还是空的：只有名字叫 `Cellar` 的那间进得去。"""
    s = _scene_this_save()
    s.house_upgrade_level = 3
    return s


def _scene_cabin_cellar2():
    """Cabin 的 indoors 有一条 warp 指到 `Cellar2`（别的门一条没有）。"""
    s = _scene_this_save()
    s.buildings = [FakeBuilding(FakeLoc("Cabin", "Cabin", warps=["Cellar2"]))]
    return s


def _scene_cabin_tile_cellar2():
    """**Cabin 升级后**（1.6 真相）：瓦片上有 `TouchAction: Warp Cellar2 5 3`，`warps` 表空空
    （`cellarWarps` 原生从不填充 ⇒ 出口只在瓦片属性里）。主屋没升级 ⇒ `Cellar` / `Cellar3~8` 仍是幽灵。"""
    s = _scene_this_save()
    s.buildings = [FakeBuilding(FakeLoc(
        "Cabin", "Cabin", touch_tiles={("Back", 5, 3): "Warp Cellar2 5 3"}))]
    return s


def _scene_farmhouse_tile_cellar():
    """主屋瓦片上有 `TouchAction: Warp Cellar 6 9`（房子没升级、warp 表空）⇒ 只认 `Cellar`。"""
    s = _scene_this_save()
    s.farmhouse = FakeLoc("FarmHouse", "FarmHouse",
                          touch_tiles={("Back", 6, 9): "Warp Cellar 6 9"})
    return s


def _scene_cabin_tile_buildings_layer():
    """Cabin 的门画在 `Buildings` 层 ⇒ 那一层也得扫（只扫 Back 会漏）。"""
    s = _scene_this_save()
    s.buildings = [FakeBuilding(FakeLoc(
        "Cabin", "Cabin", touch_tiles={("Buildings", 1, 2): "Warp Cellar3 7 8"}))]
    return s


def _accepted(save, is_reachable=is_reachable_cellar):
    return [l.name for l in resolve_locations(save, is_reachable) if l.is_cellar]


def _machines(save, is_reachable=is_reachable_cellar):
    return sum(l.machine_count() for l in resolve_locations(save, is_reachable))


def _casks(save, is_reachable=is_reachable_cellar):
    return sum(l.casks for l in resolve_locations(save, is_reachable) if l.is_cellar)


CS = _read(os.path.join(REPO, "ModEntry.cs"))
SELF = _read(os.path.abspath(__file__))

try:
    print("\nⒶ C# 源码钉：ResolveLocations 不再「无条件收所有 Cellar」")
    _resolve = _cut_func(CS, "private static List<GameLocation> ResolveLocations(")
    ck("Ⓐ切到了 ResolveLocations", bool(_resolve))
    ck("Ⓐ老写法「if (loc is Cellar && seen.Add(loc))」已经不在了",
       "if (loc is Cellar && seen.Add(loc))" not in _resolve)
    ck("ⒶCellar 那一档走的是新判据",
       "if (IsReachableCellar(loc) && seen.Add(loc))" in _resolve, _resolve[-260:])
    ck("Ⓐ判据只在这一个循环里用（没在别处偷偷放宽）",
       _resolve.count("IsReachableCellar(loc)") == 1, f"{_resolve.count('IsReachableCellar(loc)')} 处")

    print("\nⒷ 判据函数：warp ① / HouseUpgradeLevel ② 两条都在源码切片里")
    _reach = _cut_func(CS, "private static bool IsReachableCellar(")
    ck("Ⓑ函数存在", "private static bool IsReachableCellar(GameLocation? loc)" in CS, "签名对不上")
    ck("Ⓑ先挡「不是地窖」", "if (loc is not Cellar) return false;" in _reach)
    ck("Ⓑ① 走的是 warps + WarpTargetsCellar（问游戏，不猜）",
       ".warps" in _reach and "WarpTargetsCellar(w.TargetName, loc)" in _reach)
    ck("Ⓑ① 入口 = FarmHouse + 农场里每栋建筑的 indoors",
       'Game1.getLocationFromName("FarmHouse")' in _reach
       and "farm.buildings" in _reach and "b.indoors?.Value" in _reach)
    ck("Ⓑ② HouseUpgradeLevel 兜底在", "HouseUpgradeLevel >= 3" in _reach)
    ck("Ⓑ② 兜底还要求 Name 是 Cellar", 'loc.Name == "Cellar"' in _reach)
    ck("Ⓑ读不到/异常 ⇒ false（catch 里 return false）",
       "catch" in _reach and _reach[_reach.find("catch"):].count("return false") >= 1)
    ck("Ⓑ说「进得去」的路**只有三条**（①warp / ①瓦片 / ②；多一条就是又放宽了）",
       _reach.count("return true") == 3, f"{_reach.count('return true')} 条")

    print("\nⒷc 1.6 瓦片入口：① 补上 TouchAction（`Warp <地点> <x> <y>`），且只认相等")
    _touch = _cut_func(CS, "private static bool TouchActionTargetsCellar(")
    _tiles = _cut_func(CS, "private static bool TilesWarpToCellar(")
    ck("Ⓑc函数存在（TouchActionTargetsCellar）",
       "private static bool TouchActionTargetsCellar(string? touchAction, GameLocation cellar)" in CS,
       "签名对不上")
    ck("Ⓑc函数存在（TilesWarpToCellar）",
       "private static bool TilesWarpToCellar(GameLocation home, GameLocation cellar)" in CS,
       "签名对不上")
    ck("ⒷcIsReachableCellar 的 ① 里真的接了瓦片这条", "TilesWarpToCellar(from, loc)" in _reach)
    ck("Ⓑc瓦片这条**不在** `warps == null` 后面（warp 表空也要扫瓦片）",
       "if (from.warps != null)" in _reach and "if (from.warps == null) continue;" not in _reach)
    ck("Ⓑc扫的是 Back / Buildings 两层瓦片",
       'new[] { "Back", "Buildings" }' in _tiles and "GetLayer(layerName)" in _tiles)
    ck('Ⓑc读的是瓦片自身的 Properties["TouchAction"]',
       "Tiles[x, y]?.Properties" in _tiles and 'TryGetValue("TouchAction"' in _tiles)
    ck("Ⓑc瓦片里的地点名取 `Warp <地点> <x> <y>` 的第 2 个 token",
       "touchAction.Split(' ')" in _touch and "var targetName = parts[1];" in _touch)
    ck("Ⓑc只认 Warp / MagicWarp 这两种 TouchAction",
       'parts[0] != "Warp"' in _touch and '"MagicWarp"' in _touch)
    ck("Ⓑc地点名只认**相等**（Name / NameOrUniqueName 两条 Equals）",
       "cellar.Name.Equals(targetName" in _touch
       and "cellar.NameOrUniqueName.Equals(targetName" in _touch)
    ck("Ⓑc瓦片匹配**没有**用 StartsWith 比地点名（⛔ `Cellar` 会把 `Cellar2~8` 全认走）",
       "StartsWith" not in _touch)
    ck("Ⓑc扫瓦片炸了也要当「没扫到」（catch ⇒ return false）",
       "catch" in _tiles and _tiles[_tiles.find("catch"):].count("return false") >= 1)

    print("\nⒷb warp 目标只认「相等」，不认前缀（一放宽就串 8 间）")
    _warp = _cut_func(CS, "private static bool WarpTargetsCellar(")
    ck("Ⓑb函数存在", bool(_warp))
    ck("Ⓑb只认 TargetName 以 Cellar 开头",
       'StartsWith("Cellar", StringComparison.OrdinalIgnoreCase)' in _warp)
    ck("Ⓑb地点名用 Equals（Name / NameOrUniqueName 两条都比）",
       "cellar.Name.Equals(targetName" in _warp
       and "cellar.NameOrUniqueName.Equals(targetName" in _warp)
    ck("Ⓑb没有拿 StartsWith 去比地点名（那是前门）",
       _warp.count('StartsWith("Cellar", StringComparison') == 1)

    print("\nⒸ 行为钉（本存档现场）：8 间进不去的地窖 ⇒ 0 间，264 个木桶不许再出现")
    _s1 = _scene_this_save()
    ck("Ⓒ现场先有 8 间地窖（和存档一致）", len(_s1.locations) == 8, str(len(_s1.locations)))
    ck("Ⓒ采纳 0 间（upgradeLevel=0、没有任何 warp）", _accepted(_s1) == [], str(_accepted(_s1)))
    ck("ⒸFarm 的 3 台 Furnace 还在", _machines(_s1) == 3, f"{_machines(_s1)} 台")
    ck("Ⓒ不再是 267（3 + 8×33）", _machines(_s1) != 267, f"{_machines(_s1)} 台")
    ck("Ⓒ264 个幽灵木桶一个都不算", _casks(_s1) == 0, f"{_casks(_s1)} 个")
    ck("ⒸFarm + Cabin 室内照样在遍历范围里（只砍了地窖）",
       [l.name for l in resolve_locations(_s1)] == ["Farm", "Cabin"],
       str([l.name for l in resolve_locations(_s1)]))
    ck("Ⓒ现场住宅的**瓦片表也空**（1.6 没升级 ⇒ 一格 Warp TouchAction 都没有）",
       not _s1.farmhouse.touch_tiles and not _s1.buildings[0].indoors.touch_tiles,
       f"{_s1.farmhouse.touch_tiles} / {_s1.buildings[0].indoors.touch_tiles}")

    print("\nⒸb 行为钉（房子升到 3 级）：只收 Cellar，Cellar2~8 仍是幽灵")
    _s2 = _scene_upgraded()
    ck("Ⓒb只收名字叫 Cellar 的那间", _accepted(_s2) == ["Cellar"], str(_accepted(_s2)))
    ck("ⒸbCellar2 / Cellar8 一个都不收",
       "Cellar2" not in _accepted(_s2) and "Cellar8" not in _accepted(_s2), str(_accepted(_s2)))
    ck("Ⓒb木桶只算 33 个（就一间）", _casks(_s2) == 33, f"{_casks(_s2)} 个")

    print("\nⒸc 行为钉（Cabin 的门指 Cellar2）：只收 Cellar2")
    _s3 = _scene_cabin_cellar2()
    ck("Ⓒc只收 Cellar2", _accepted(_s3) == ["Cellar2"], str(_accepted(_s3)))
    ck("ⒸcCellar3~8 一个都不收",
       all(f"Cellar{i}" not in _accepted(_s3) for i in range(3, 9)), str(_accepted(_s3)))
    ck("ⒸcCellar 本身也不收（门只指 Cellar2、房子也没升级）",
       "Cellar" not in _accepted(_s3), str(_accepted(_s3)))
    ck("Ⓒc木桶只算 33 个（就 Cellar2 那间）", _casks(_s3) == 33, f"{_casks(_s3)} 个")

    print("\nⒸc2 行为钉（Cabin 升级后**瓦片**上有 `Warp Cellar2 5 3`）：只收 Cellar2")
    _s3b = _scene_cabin_tile_cellar2()
    _cabin3b = _s3b.buildings[0].indoors
    ck("Ⓒc2夹具对得上 1.6：Cabin 的 warps 表**是空的**（门只在瓦片上）",
       _cabin3b.warps == [] and _cabin3b.touch_tiles, str(_cabin3b.warps))
    ck("Ⓒc2只收 Cellar2", _accepted(_s3b) == ["Cellar2"], str(_accepted(_s3b)))
    ck("Ⓒc2Cellar3~8 一个都不收",
       all(f"Cellar{i}" not in _accepted(_s3b) for i in range(3, 9)), str(_accepted(_s3b)))
    ck("Ⓒc2Cellar 本身也不收（瓦片写的是 Cellar2，只认相等）",
       "Cellar" not in _accepted(_s3b), str(_accepted(_s3b)))
    ck("Ⓒc2木桶只算 33 个（就 Cellar2 那间）", _casks(_s3b) == 33, f"{_casks(_s3b)} 个")
    ck("Ⓒc2机器 = Farm 3 台 + Cellar2 的 33 个（不是 267）",
       _machines(_s3b) == 36, f"{_machines(_s3b)} 台")

    print("\nⒸc3 只认相等（反例）：`Warp Cellar2 …` 不许被 `Cellar` 认走")
    _cellar = FakeLoc("Cellar", "Cellar")
    _cellar2 = FakeLoc("Cellar2", "Cellar")
    ck("Ⓒc3`Warp Cellar2 5 3` 对 Cellar ⇒ False（前缀不算门）",
       touch_action_targets_cellar("Warp Cellar2 5 3", _cellar) is False)
    ck("Ⓒc3`Warp Cellar 5 3` 对 Cellar2 ⇒ False（反过来也不行）",
       touch_action_targets_cellar("Warp Cellar 5 3", _cellar2) is False)
    ck("Ⓒc3`Warp Cellar2 5 3` 对 Cellar2 ⇒ True",
       touch_action_targets_cellar("Warp Cellar2 5 3", _cellar2) is True)
    ck("Ⓒc3`MagicWarp Cellar2 1 1` 同序也认",
       touch_action_targets_cellar("MagicWarp Cellar2 1 1", _cellar2) is True)
    ck("Ⓒc3不是换图那两种 TouchAction ⇒ 不认（ChangeIntoSwimsuit）",
       touch_action_targets_cellar("ChangeIntoSwimsuit", _cellar2) is False)
    ck("Ⓒc3只有 `Warp` 一个 token（缺地点名/坐标）⇒ 不认",
       touch_action_targets_cellar("Warp", _cellar2) is False)
    ck("Ⓒc3空字符串 / None ⇒ 不认",
       touch_action_targets_cellar("", _cellar2) is False
       and touch_action_targets_cellar(None, _cellar2) is False)

    print("\nⒸc4 主屋瓦片那条也认；`Buildings` 层同样要扫")
    _s3c = _scene_farmhouse_tile_cellar()
    ck("Ⓒc4主屋瓦片 `Warp Cellar 6 9` ⇒ 只收 Cellar",
       _accepted(_s3c) == ["Cellar"], str(_accepted(_s3c)))
    _s3d = _scene_cabin_tile_buildings_layer()
    ck("Ⓒc4`Buildings` 层的 `Warp Cellar3 7 8` ⇒ 只收 Cellar3",
       _accepted(_s3d) == ["Cellar3"], str(_accepted(_s3d)))

    print("\nⒸd 农舍自己那条 warp 也认（不只 Cabin）")
    _s4 = _scene_this_save()
    _s4.farmhouse = FakeLoc("FarmHouse", "FarmHouse", warps=["Cellar5"])
    ck("Ⓒd农舍 warp 指哪间就只收哪间", _accepted(_s4) == ["Cellar5"], str(_accepted(_s4)))

    print("\nⒸe 相等不前缀：warp 写 Cellar 不能认下一间 Cellar3")
    _s5 = FakeSave(farm=FakeLoc("Farm", "Farm"), buildings=[],
                   locations=[FakeLoc("Cellar3", "Cellar", casks=33)],
                   farmhouse=FakeLoc("FarmHouse", "FarmHouse", warps=["Cellar"]), upgrade=0)
    ck("Ⓒe这间叫 Cellar3 ⇒ 不收（前缀不算门）", _accepted(_s5) == [], str(_accepted(_s5)))
    _s5.farmhouse = FakeLoc("FarmHouse", "FarmHouse", warps=["Cellar3"])
    ck("Ⓒe反过来 warp 写 Cellar3 ⇒ 收", _accepted(_s5) == ["Cellar3"], str(_accepted(_s5)))
    _s6 = FakeSave(farm=FakeLoc("Farm", "Farm"), buildings=[],
                   locations=[FakeLoc("Cellar", "Cellar", casks=33)],
                   farmhouse=FakeLoc("FarmHouse", "FarmHouse", warps=["Farm", ""]),
                   upgrade=0)
    ck("Ⓒe不是 Cellar 开头的 warp 一律不认", _accepted(_s6) == [], str(_accepted(_s6)))

    print("\nⒸf 读不到 warps ⇒ 保守 false（宁可少报，不许报假数据）")
    _s7 = FakeSave(farm=FakeLoc("Farm", "Farm", furnaces=3), buildings=[],
                   locations=[FakeLoc("Cellar", "Cellar", casks=33), FakeLoc("Cellar2", "Cellar", casks=33)],
                   farmhouse=BrokenLoc(), upgrade=0)
    _got7 = _accepted(_s7)
    ck("Ⓒf炸了也当进不去（0 间）", _got7 == [], str(_got7))
    ck("Ⓒf夹具**确实会炸**（不是假测）", _raises(lambda: _s7.farmhouse.warps))

    print("\nⒸg 复刻与 C# 一一对应（两条分支两边都有）")
    _repl = _cut_py_func(SELF, "def is_reachable_cellar(")
    _repl_warp = _cut_py_func(SELF, "def warp_targets_cellar(")
    ck("Ⓒg复刻里有 ① 的标记 + 走 warps", "# C# ①" in _repl and "src.warps" in _repl)
    ck("Ⓒg复刻里有 ② 的标记 + house_upgrade_level >= 3",
       "# C# ②" in _repl and "house_upgrade_level >= 3" in _repl)
    ck("ⒸgC# 的两条判据在复刻里都有对应",
       all(m in _reach for m in (".warps", "HouseUpgradeLevel >= 3"))
       and all(m in _repl for m in ("src.warps", "house_upgrade_level >= 3")))
    ck("Ⓒg「只认相等」两边一致",
       "cellar.Name.Equals(targetName" in _warp
       and "cellar.name.lower()" in _repl_warp
       and "cellar.unique_name.lower()" in _repl_warp)

    _repl_touch = _cut_py_func(SELF, "def touch_action_targets_cellar(")
    _repl_tiles = _cut_py_func(SELF, "def tiles_warp_to_cellar(")
    ck("Ⓒg复刻里 ① 的**瓦片**那条也在（带 `# C# ①` 标记）",
       "# C# ①（瓦片）" in _repl and "tiles_warp_to_cellar(src, loc)" in _repl)
    ck("ⒸgC# 与复刻的瓦片扫描一一对应（两层 + TouchAction + 第 2 个 token）",
       all(m in _tiles for m in ('"Back", "Buildings"', '"TouchAction"'))
       and all(m in _repl_tiles for m in ('("Back", "Buildings")', "touch_tiles"))
       and "parts[1]" in _touch and "parts[1]" in _repl_touch)
    ck("Ⓒg瓦片匹配的「只认相等」两边一致（复刻里也不许 startswith）",
       "cellar.Name.Equals(targetName" in _touch
       and "target_name.lower() == cellar.name.lower()" in _repl_touch
       and "startswith" not in _repl_touch)

    print("\nⒸh 黑化对照：这几把坏判据必须被上面的场景咬到")
    _bad1 = _accepted(_s1, _bad_unconditional)
    ck("Ⓒh无条件收所有 Cellar ⇒ 场景一由 0 间变 8 间（咬到了）",
       len(_bad1) == 8 and _accepted(_s1) == [], str(_bad1))
    _bad2 = _accepted(_s2, _bad_no_upgrade)
    ck("Ⓒh删掉 HouseUpgradeLevel 兜底 ⇒ 场景二由 1 间变 0 间（咬到了）",
       _bad2 == [] and _accepted(_s2) == ["Cellar"], str(_bad2))
    ck("Ⓒh场景三咬的是 warp 那条（删 ② 不影响它）",
       _accepted(_s3, _bad_no_upgrade) == _accepted(_s3) == ["Cellar2"])
    _bad3 = _accepted(_s3b, _bad_no_touch)
    ck("Ⓒh删掉 TouchAction（瓦片）那条分支 ⇒ Cabin 瓦片场景由 1 间变 0 间（负向刀必红）",
       _bad3 == [] and _accepted(_s3b) == ["Cellar2"], str(_bad3))
    _bad4 = _accepted(_s3b, _bad_touch_prefix)
    ck("Ⓒh瓦片匹配改用**前缀** ⇒ `Cellar` 也被认走（相等那条必须咬得住）",
       "Cellar" in _bad4 and "Cellar" not in _accepted(_s3b), str(_bad4))

    print("\nⒹ 输出结构没动：每台 machine 仍带 location / location_unique")
    _scan = _cut_func(CS, "private static void ScanLocationMachines(")
    ck("ⒹScanLocationMachines 还在", bool(_scan))
    ck("Ⓓ每台 machine 带 location", '["location"] = locationKey' in _scan)
    ck("Ⓓ每台 machine 带 location_unique", '["location_unique"] = loc.NameOrUniqueName' in _scan)
    ck("Ⓓ`/farm_report` 与 `/machines` 都还是走 ResolveLocations",
       'var farmLocs = ResolveLocations("")' in CS
       and "ScanLocationMachines(loc, loc.Name, machines, BuildBuildingMap())" in CS)
except Exception as _ex:   # 钉子自己炸了也得是「红」，不是 traceback
    print(f"\n❌ 钉子本身炸了：{_ex!r}")
    sys.exit(2)

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 地窖只算走得进去的：现场那 264 个幽灵木桶不再进 `/farm_report`；升级过的房子 / "
      "Cabin 有门的那间照样收得到；读不到就保守不报。")
