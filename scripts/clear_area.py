"""
开垦skill：扫描区域 → 粗清(move_to) → 重扫 → 精补(warp)

🧹 **在哪就在哪清**（恒 2026-09-20 拍板）——清的是**角色当前所在那张图**的指定区域，
   农场、矿井、野外都行。以前不是这样：不在农场会被**静默搬回农场**、
   然后在**农场的同名坐标**上清场（在矿里喊一声"清这块"，被清掉的是农场那片）。

用法:
    python clear_area.py <x1> <y1> <x2> <y2> [options]      # 4 个数 = 矩形
    python clear_area.py <cx> <cy> <r>          [options]      # 3 个数 = 圆形（圆心 + 半径）

选项:
    --port PORT   NagiBridge端口（默认 7842）
    --hits N      硬目标额外敲击次数（默认 2）

示例:
    python clear_area.py 50 20 70 30 --port 7842
    python clear_area.py 60 25 8    --port 7842   # 以 (60,25) 为圆心、半径 8 的圆

⚠️ **清场建议用圆形、且比田块外扩 2~3 格**（恒 2026-09-19）：只清方正一块的话，
   四角还是草窝，**田边的杂草很快会长进田里、把作物顶掉**。
"""

import argparse
import math
import os
import time
from collections import defaultdict

import area_spec

parser = argparse.ArgumentParser()
parser.add_argument("coords", nargs="+", type=int,
                    help="4 个数=矩形 x1 y1 x2 y2；3 个数=圆形 圆心x 圆心y 半径")
parser.add_argument("--port", type=int, default=7842)
parser.add_argument("--hits", type=int, default=2)
# 🪓 放行名单（恒 2026-09-12）：同 chop_trees —— 默认只清橡/枫/松，特殊树受保护
#    （不然"清一块地"顺手就把蘑菇树/桃花心木铲了）。由 settings 域的 `chop` 设置经服务器传进来。
parser.add_argument("--allow", default="", help="放行的特殊树种（名字/树号，逗号分隔；none/all）")
args = parser.parse_args()

# 🍥 区域写法：4 个数=矩形 / 3 个数=圆（见 area_spec.py）。**个数不对直接报错退出**，
#    不兜底猜形状——猜错会跑去清错地方（宁报错别兜底）。
try:
    _AREA = area_spec.parse(args.coords)
except ValueError as e:
    print(f"❌ 区域参数错：{e}")
    raise SystemExit(2)
# 兼容老代码里的 args.x1..y2 引用（外接矩形）
args.x1, args.y1, args.x2, args.y2 = area_spec.bounds(_AREA)

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api
import tree_types as tt

_ALLOW, _ALLOW_ERR = tt.parse_allow(args.allow)
if _ALLOW_ERR:
    print(f"❌ --allow 参数错：{_ALLOW_ERR}")
    raise SystemExit(2)

TOOL_DELAY = 0.55
STAMINA_MIN = 20

TOOL_MAP = {
    "Weeds": ("Scythe", 1),
    "Grass": ("Scythe", 1),
    "Stone": ("Pickaxe", 2),
    "Twig": ("Axe", 1),
    "Tree": ("Axe", 18),
    "LargeStump": ("Axe", 15),
    "LargeLog": ("Axe", 15),
    "LargeBoulder": ("Pickaxe", 10),
    "MeteoriteOre": ("Pickaxe", 10),
}

# 🪨 大石头/树桩/原木：**敲击次数随工具等级变，不能写死**（恒 2026-09-20：
#    "在 clear_area 里检查镐子等级去敲大石头是相当有必要的"）。
#    反编译 `ResourceClump`（decomp/full/StardewValley.TerrainFeatures/ResourceClump.cs）：
#      · 血量 = `GetDefaultHealth(parentSheetIndex)`：600树桩=10 / 672大石=10 / 602原木=20 / 622陨石=20
#      · 每击 = `Math.Max(1f, (t.upgradeLevel.Value + 1) * 0.75f)`（`performToolAction` 第一行）
#      · 门槛 = 同一个 switch 里的 `upgradeLevel` 检查 —— **不够就弹"需要更好的工具"、根本敲不动**，
#        挥多少下都是白费（`return false`，血量一点都不掉）。
#    ⇒ 旧写法 TOOL_MAP 里写死 (Pickaxe,10)/(Axe,15) 的两个后果：
#        **镐子差 → 白挥十下然后静默走开**（还被计进 cleared）；
#        **镐子好 → 多挥六七下白掉体力**（每挥都扣，这块地越大越亏）。
#    ⚠️ 下面 `TOOL_MAP` 那四条**保留不动**：`tile_target_name` 靠它认名字，删了就不认识大石头了。
#       等级换算在 `scan_targets` 里覆盖，真数值一律走 `CLUMP_SPEC`。
CLUMP_SPEC = {
    #               需求工具     血量  最低升级等级（0=Basic 1=铜 2=钢 3=金 4=铱）
    "LargeStump":   ("Axe",     10, 1),   # 树桩：要**铜斧**
    "LargeLog":     ("Axe",     20, 2),   # 原木：要**钢斧**
    "LargeBoulder": ("Pickaxe", 10, 2),   # 农场大石：要**钢镐**
    "MeteoriteOre": ("Pickaxe", 20, 3),   # 陨石：要**金镐**
}
_TIER_CN = {0: "普通", 1: "铜", 2: "钢", 3: "金", 4: "铱"}
_TOOL_CN = {"Axe": "斧头", "Pickaxe": "镐子"}

_TOOL_LV = {}     # 本轮读到的工具等级，scan_targets 开头刷新
_TOO_WEAK = {}    # 等级不够、敲不动的（收着统一报，别一件件刷屏）


def dmg_per_hit(level):
    """每击伤害——照抄 `ResourceClump.performToolAction`，**别自己编系数**。"""
    return max(1.0, (level + 1) * 0.75)


def detect_tool_levels():
    """从**背包**读镐子/斧头的升级等级（0=Basic…4=铱）。

    ⚠️ 判据是**背包**、不是"手里拿的"——2026-09-20 在 mine_run 踩过一模一样的坑
    （读 `player.currentTool` ⇒ 手里是锤子时报出 `Basic Lv.0`）。见 CHANGELOG 09-20(94)④。
    """
    lv = {"Pickaxe": 0, "Axe": 0}
    tiers = (("Iridium", 4), ("Gold", 3), ("Steel", 2), ("Copper", 1))
    for it in api.state().get("inventory", []):
        name = it.get("name") or ""
        for tool in ("Pickaxe", "Axe"):
            # 注意大小写：`"Iridium Pickaxe".endswith("Axe")` 是 False（末尾是 "axe"），不会串台
            if name.endswith(tool):
                for prefix, l in tiers:
                    if name.startswith(prefix):
                        lv[tool] = max(lv[tool], l)
    return lv

TOOL_ORDER = ["Scythe", "Pickaxe", "Axe"]


# 🛡️ 被保护树种挡下的。_PASS 是本轮扫描计数（tile_target_name 填），_SEEN 是所有轮次的合并
#    （取每轮最大值——同一棵树会被 3 轮扫描反复数到，直接累加会灌水）。收尾必须报出来。
_SKIPPED_PASS = {}
_SKIPPED_SEEN = {}


# 🌲 当前地图名（`_scan()` 每扫一次钉一次）—— `is_choppable` 的"**农场外不保护苔雨树**"
#    （恒 2026-10-02）判据要用它。判据本身在 `tree_types.is_choppable`，这里只负责**告诉它我在哪**。
_LOC = ""


def _scan(radius):
    """一次 `/surroundings`，顺手把"我在哪张图"钉进 `_LOC`（只此一处赋值，别在别处再读 location）。"""
    global _LOC
    data = api.surroundings(radius)
    _LOC = data.get("location") or ""
    return data


def tile_target_name(tile):
    obj = tile.get("object", "")
    terrain = tile.get("terrain", "")
    resource = tile.get("resource", "")

    if obj in TOOL_MAP:
        return obj
    if resource in TOOL_MAP:
        return resource
    if terrain and terrain.startswith("Tree:"):
        # 🪓 2026-09-12 恒拍板：特殊树种（蘑菇树/桃花心木/苔雨树/神秘树…）默认不动
        # 🌲 2026-10-02 恒：「不要保护**农场之外**的绿雨树，免得绿雨天收集不了苔藓了」
        #    ⇒ 传 `_LOC`（`is_choppable` 认农场外苔雨树=可砍）
        ttype = tt.tree_type_of(terrain)
        if not tt.is_choppable(ttype, _ALLOW, _LOC):
            _SKIPPED_PASS[ttype] = _SKIPPED_PASS.get(ttype, 0) + 1
            return None
        return "Tree"
    if terrain == "Grass":
        return "Grass"
    return None


def inventory_counts():
    counts = defaultdict(int)
    for item in api.state().get("inventory", []):
        name = item.get("name")
        if name:
            counts[name] += int(item.get("stack", 1))
    return counts


def log_inventory_delta(before, after):
    gains = {}
    for name in sorted(set(before) | set(after)):
        delta = after.get(name, 0) - before.get(name, 0)
        if delta:
            gains[name] = delta
    api.log(f"Inventory delta: {gains if gains else '{}'}")


def stamina_ok():
    cur, mx = api.player_stamina()
    if cur < STAMINA_MIN:
        api.log(f"Stamina low: {cur:.1f}/{mx:.0f}, stopping")
        return False
    return True


def scan_area():
    cx, cy = area_spec.center(_AREA)
    radius = area_spec.reach(_AREA) + 5      # 从中心到最远格 + 余量

    # 🧹 2026-09-20 恒拍板：「**哪里都允许它清当前场景**」。
    #    原来这里是 `if 在农场: position / else: warp("Farm", cx, cy)` ——
    #    不在农场就**静默把人搬回农场**，然后在**农场的同名坐标**上清场
    #    （⚠️ 真破坏力：在矿里/姜岛喊一句"清这块"，被清的却是农场那片，作物可能就没了）。
    #    ⇒ 改成**就地**：一律在**当前地图**里挪到区域中心去扫描。
    api._post("/position", {"x": cx, "y": cy})
    time.sleep(0.6)
    data = _scan(min(radius, 30))

    targets = []
    _SKIPPED_PASS.clear()
    _TOO_WEAK.clear()
    _TOOL_LV.clear()
    _TOOL_LV.update(detect_tool_levels())
    for t in data.get("tiles", []):
        x, y = t["x"], t["y"]
        if not area_spec.contains(_AREA, x, y):
            continue

        name = tile_target_name(t)
        if not name:
            continue

        tool, hits = TOOL_MAP[name]
        if name in CLUMP_SPEC:
            need_tool, health, min_lv = CLUMP_SPEC[name]
            have = _TOOL_LV.get(need_tool, 0)
            if have < min_lv:
                # 等级不够 = **游戏层面敲不动**（`performToolAction` 直接 return，血量一点不掉）。
                # 别排进去白挥十下还装作清掉了，收着统一报（恒：报错要给下一步、能拦就拦）。
                _TOO_WEAK.setdefault(name, []).append((x, y, min_lv, have, need_tool))
                continue
            tool = need_tool
            hits = math.ceil(health / dmg_per_hit(have))   # 几下能碎 = 血量 ÷ 每击
        targets.append((x, y, tool, name, hits))

    for k, v in _SKIPPED_PASS.items():        # 本轮看见的受保护树，合并进总账（取最大值防灌水）
        _SKIPPED_SEEN[k] = max(_SKIPPED_SEEN.get(k, 0), v)

    if _TOO_WEAK:
        for nm, items in _TOO_WEAK.items():
            x, y, min_lv, have, need_tool = items[0]
            more = f"等 {len(items)} 处" if len(items) > 1 else ""
            api.log(f"  ⛔ {nm} {more}({x},{y}) **敲不动**：需要"
                    f"【{_TIER_CN.get(min_lv, min_lv)}级{_TOOL_CN[need_tool]}】，"
                    f"当前只有【{_TIER_CN.get(have, have)}级】——"
                    f"游戏会弹「需要更好的工具」并**一点血都不掉**，挥多少下都一样")
        api.log("  ⇒ 这些格**已跳过、没排进待清名单**——不是「没扫到」，是「扫到了但打不破」。"
                "下一步：升级工具再来；或它们不挡耕种就先留着（大石头不吃作物）。")
    return targets


def snake_sort(items):
    rows = defaultdict(list)
    for item in items:
        rows[item[1]].append(item)
    ordered = []
    for i, y in enumerate(sorted(rows.keys())):
        row = sorted(rows[y], key=lambda t: t[0])
        if i % 2 == 1:
            row.reverse()
        ordered.extend(row)
    return ordered


def target_still_present(x, y, expected_name):
    data = _scan(2)
    for t in data.get("tiles", []):
        if t.get("x") == x and t.get("y") == y:
            return tile_target_name(t) == expected_name
    return False


def stand_for_target(x, y, use_position):
    stand_x, stand_y = x, y - 1
    if use_position:
        # 🧹 同 `scan_area`：**就地**挪（当前地图），不再静默搬回农场（恒 2026-09-20）。
        api._post("/position", {"x": stand_x, "y": stand_y})
        time.sleep(0.3)
        api.face(2)
    else:
        api.move_to(stand_x, stand_y, timeout=8)
        d = api.face_toward(x, y)
        api.face(d)
    time.sleep(0.1)


# 🔁 挥完一下之后的重扫半径。**镰刀/镐/斧都是"范围动作"**：一挥能顺手清掉身边一片
#    （Iridium Scythe 尤其大），所以挥完该**重扫一次**、把"顺带清掉"的格子从待办里划掉，
#    直接挪到下一个**还有草**的地方 —— 而不是按老样子一格格挪过去对着空地挥
#    （恒 2026-09-19：「除草的话因为镰刀是范围的，不需要一格格挪……挪到挥舞之后
#      下一个有草的地方比较好」）。
_SWEEP_R = 4


def _sweep_alive(radius=_SWEEP_R):
    """挥完重扫身边：`{(x,y): 目标名}`（判据=`tile_target_name`，**问游戏**、不猜范围）。

    扫描失败返回 `None` —— 调用方**别剪**待办（宁可多走一格，也别把没清的格子误划掉）。
    """
    try:
        data = _scan(radius)
    except Exception:
        return None
    out = {}
    for t in data.get("tiles", []):
        nm = tile_target_name(t)
        if nm:
            out[(t["x"], t["y"])] = nm
    for k, v in _SKIPPED_PASS.items():      # 重扫也会撞见受保护树种，合并进总账（同 scan_area 的"取最大值防灌水"）
        _SKIPPED_SEEN[k] = max(_SKIPPED_SEEN.get(k, 0), v)
    return out


def clear_pass(targets, use_warp=False):
    by_tool = defaultdict(list)
    for x, y, tool, name, hits in targets:
        by_tool[tool].append((x, y, name, hits))

    cleared = 0
    stuck = []      # 敲了上限仍没掉的（不能混进 cleared，见下面循环里的注释）
    for tool in TOOL_ORDER:
        items = by_tool.get(tool, [])
        if not items:
            continue

        items = snake_sort(items)
        mode = "position" if use_warp else "move"
        api.log(f"--- {tool}: {len(items)} targets ({mode}) ---")
        api.select(tool)
        time.sleep(0.15)

        _trimmed_total = 0
        i = 0
        while i < len(items):
            x, y, name, hits = items[i]
            if not stamina_ok():
                return cleared

            stand_for_target(x, y, use_warp)

            actual_hits = 0
            for h in range(hits):
                if not stamina_ok():
                    return cleared
                if h > 0 and not target_still_present(x, y, name):
                    break
                api.use_tool(tool)
                time.sleep(TOOL_DELAY)
                actual_hits += 1
                if not target_still_present(x, y, name):
                    break
            # ⚠️ 2026-09-20 恒：这里原来是**无条件** `cleared += 1` —— **敲不碎也算"清掉了"**。
            #    工具等级不够 / 差几下的时候，日志会报"清完 N 格"而东西还在原地
            #    （同 09-20(94)④ 在 mine_run 抓到的那个"静默成功"病，一个模子）。
            #    改成**只认真的没了**；没碎的收进 stuck，收尾统一报出来。
            _before = cleared
            if not target_still_present(x, y, name):
                if actual_hits < hits:
                    api.log(f"  {name} at ({x},{y}) cleared after {actual_hits}/{hits} hits")
                cleared += 1
            else:
                stuck.append((x, y, name, actual_hits, hits))

            # ⚠️ 必须带 `cleared != _before`：现在 cleared 不再每次必增，
            #    单写 `cleared % 20 == 0` 会在 cleared=0/20/40 时**每个没碎的目标都重刷一行**。
            if cleared != _before and cleared % 20 == 0:
                api.log(f"  {cleared} cleared...")

            i += 1
            if i >= len(items):
                break
            # 🔁 范围动作：挥完重扫，把**这一挥顺带清掉**的格子从待办里划掉（人不会一格格挪过去）。
            #    只剪**这趟重扫看得见**的那圈：更远的目标这趟扫不到，剪了会漏清。
            alive = _sweep_alive()
            if alive is None:
                continue
            keep = []
            for t in items[i:]:
                if (t[0] - x) ** 2 + (t[1] - y) ** 2 > _SWEEP_R ** 2:
                    keep.append(t)                       # 太远：这次重扫看不到，别误删
                elif alive.get((t[0], t[1])) == t[2]:
                    keep.append(t)                       # 还在，接着清
                else:
                    _trimmed_total += 1                  # 已经被顺手清掉了 ⇒ 不用再挪过去
            if _trimmed_total:
                items = items[:i] + keep

        if _trimmed_total:
            api.log(f"  ↩ {tool}: 顺手清掉 {_trimmed_total} 格，省下 {_trimmed_total} 次挪位"
                    f"（范围动作，挥完重扫划线）")
            # 这些格**也是这一趟清掉的**（只是没为它们单独挥一下）⇒ 计数算进去，
            # 别让"cleared N"看着像漏了（真机：17 格只挥 4 下，日志却报 cleared 4）。
            cleared += _trimmed_total

    # ⚠️ 敲满上限仍没掉的 —— **必须露出来**，否则"cleared N"会把它们盖过去（恒：别静默）。
    if stuck:
        api.log(f"  ⚠️ {len(stuck)} 件**敲满上限仍没清掉**（已从 cleared 里剔除，没算成清的）：")
        for sx, sy, sname, ah, hh in stuck[:6]:
            api.log(f"     · {sname} ({sx},{sy}) 敲了 {ah}/{hh} 下")
        if len(stuck) > 6:
            api.log(f"     · …还有 {len(stuck) - 6} 件")
        api.log("     ⇒ scan 阶段会先拦掉「工具等级不够」那一类，所以剩下的多半是**站位/朝向没对上**"
                "（人在砍空气）。把这几条发我，我按坐标查 `stand_for_target` 的落点。")

    return cleared


def _pickup_drops():
    """智能捡拾：**只捡游戏自己认定为掉落物的东西**（走 /debris），区域内就过去捡。

    ⚠️ 2026-09-16 恒真机抓到，**旧判据是名单减法，会薅走设施**：
        `if obj and obj not in ("Weeds","Stone","Twig","Grass")` —— 意思是"格子有 object 且名字
        不在排除表里 ⇒ 当成敲出来的掉落"。可是**洒水器/稻草人/火把也全是 object**，一个都跑不掉。
        实测：`farm clear` 一个 9×9，日志 `掉落物: 6 处`，紧接着把 **6 个优质洒水器**
        挨个 `position` 站上去按 `confirm` 刨了起来，留了一地（恒在游戏里看见并问
        "把 6 个洒水器全部精准站位薅了出来是什么意思"）。位置全在 /debris 里躺着，可回收，但**本不该发生**。
        病根同 2026-09-12 那次：**拾取判据别在消费侧猜名字，去问游戏**
        （那次是 `Object.isForage()` 替掉三张名单）。
    ✅ 现在直接读 `/debris`（游戏 Debris 层的真·掉落物），再按目标矩形过滤。
    """
    try:
        d = api._ai_get("/debris")
        dropped = [(it["x"], it["y"], it.get("itemName") or "?")
                   for it in (d.get("debris") or [])
                   if args.x1 <= it["x"] <= args.x2 and args.y1 <= it["y"] <= args.y2]
        if dropped:
            api.log(f"  掉落物: {len(dropped)} 处")
            for x, y, name in dropped:
                api.position(x, y)
                # ⚠️ 这里**绝不能**再跟一发 `api.key("confirm")`（原版就是这么写的）。
                #    `confirm` 在没有菜单时 = `Game1.pressActionButton` = **挥手里的工具**：
                #    打在掉落物格是捡，打在设施格是**铲掉**——同一个动作两种命运。2026-09-16 那次
                #    6 个优质洒水器就是这么没的（旧判据把它们当成了掉落物）。
                #    ✅ 实测（活世界，2026-09-16）：**光 `/position` 站上去就会自动吸附**
                #      —— 掉落 3 木材在 (60,18) → `/position` 过去 → 地上清空、背包 +3。
                #      走位经过同样会吸附（`walk_to` 路过即收）。所以那一发 confirm 从来就是多余的。
                #    ⏳ 留个睡：给 Debris.update 的包围盒判定跑一两帧。
                time.sleep(0.25)
    except Exception as e:
        api.log(f"  捡拾跳过: {e}")


def run():
    api.log(f"=== clear area: {area_spec.describe(_AREA)} ===")
    api.log(f"🌳 放行: {tt.allow_label(_ALLOW)}"
            + ("" if _ALLOW else "（特殊树种受保护；要清用 settings chop 蘑菇树,桃花心木 …）"))
    inv_before = inventory_counts()

    # Pass 1: scan + fast clear with move_to
    targets = scan_area()
    by_type = defaultdict(int)
    for _, _, _, name, _ in targets:
        by_type[name] += 1
    api.log(f"Pass 1: {dict(by_type)}, total={len(targets)}")

    if targets:
        n = clear_pass(targets, use_warp=False)
        api.log(f"Pass 1 done: cleared {n}")

    # Pass 2: rescan + precision clear with warp
    remaining = scan_area() if stamina_ok() else []
    if remaining:
        by_type = defaultdict(int)
        for _, _, _, name, _ in remaining:
            by_type[name] += 1
        api.log(f"Pass 2 (warp): {dict(by_type)}, total={len(remaining)}")
        n = clear_pass(remaining, use_warp=True)
        api.log(f"Pass 2 done: cleared {n}")

        # final check
        leftover = scan_area()
        if leftover:
            api.log(f"Still {len(leftover)} left (may need tool upgrade)")
        elif _SKIPPED_SEEN:
            # ⚠️ 别报 "All clear!" —— 地是被保护树种占着的，说"清干净了"是谎报
            api.log(f"🛡️ 能清的都清了；剩 {tt.skipped_summary(_SKIPPED_SEEN)} 是受保护树种，没动")
        else:
            api.log("All clear!")
    else:
        api.log("All clear after pass 1!")

    # 智能捡拾：只去有掉落物的格子捡，不散步
    _pickup_drops()
    log_inventory_delta(inv_before, inventory_counts())
    if _SKIPPED_SEEN:
        api.log(f"🛡️ 受保护跳过：{tt.skipped_summary(_SKIPPED_SEEN)}"
                f"　要清：settings chop <树种名>")


if __name__ == "__main__":
    try:
        st = api.status()
        if not st.get("worldReady"):
            print("game not ready")
            exit(1)
    except Exception as e:
        print(f"cannot connect: {e}")
        exit(1)
    run()
