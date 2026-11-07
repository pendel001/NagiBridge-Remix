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
  ① **能从现存住宅走到**：`FarmHouse` 或农场里每栋 `Building` 的 `indoors`，它们的 `warps` 里有
     `TargetName` 指到**这间**地窖（`Cellar` / `Cellar2` …，见 `FarmHouse.GetCellarName()`）；
  ② **兜底**：`Game1.player.HouseUpgradeLevel >= 3` 且这间 `Name == "Cellar"`。
  读不到/异常 ⇒ **false**（宁可少报一间，也绝不把假数据再报出来）。

这个钉子**离线**跑（不开游戏），钉三件事：
  Ⓐ C# 源码钉：`ResolveLocations` 不再是「无条件 `loc is Cellar`」，走的是新判据；
  Ⓑ 判据函数存在、两条分支（warp ① / `HouseUpgradeLevel` ②）都写在源码切片里；
  Ⓒ **行为钉**：把 C# 判据逐行翻成 Python 小函数 + 三个假存档场景
     （本存档现场 / 房子升级过 / 只有 Cabin 有门指 `Cellar2`）。
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
    """假的 GameLocation。`kind` 对应存档里的 `xsi:type`；`casks`/`furnaces` 只拿来数机器。"""

    def __init__(self, name, kind="GameLocation", warps=None, casks=0, furnaces=0,
                 unique_name=None):
        self.name = name
        self.kind = kind
        self.warps = list(warps or [])
        self.casks = casks
        self.furnaces = furnaces
        self.unique_name = unique_name or name

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


# ── 黑化对照（证明下面这三个场景**真能咬到**坏判据）──────────────────────────
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


# ── 三个假存档场景 ───────────────────────────────────────────────────────────
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
    ck("Ⓑ说「进得去」的路**只有两条**（多一条就是又放宽了）",
       _reach.count("return true") == 2, f"{_reach.count('return true')} 条")

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

    print("\nⒸh 黑化对照：这两把坏判据必须被上面的场景咬到")
    _bad1 = _accepted(_s1, _bad_unconditional)
    ck("Ⓒh无条件收所有 Cellar ⇒ 场景一由 0 间变 8 间（咬到了）",
       len(_bad1) == 8 and _accepted(_s1) == [], str(_bad1))
    _bad2 = _accepted(_s2, _bad_no_upgrade)
    ck("Ⓒh删掉 HouseUpgradeLevel 兜底 ⇒ 场景二由 1 间变 0 间（咬到了）",
       _bad2 == [] and _accepted(_s2) == ["Cellar"], str(_bad2))
    ck("Ⓒh场景三咬的是 warp 那条（删 ② 不影响它）",
       _accepted(_s3, _bad_no_upgrade) == _accepted(_s3) == ["Cellar2"])

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
