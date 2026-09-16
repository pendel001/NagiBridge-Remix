"""
开垦skill：扫描区域 → 粗清(move_to) → 重扫 → 精补(warp)

用法:
    python clear_area.py <x1> <y1> <x2> <y2> [options]

参数:
    x1,y1  左上角坐标
    x2,y2  右下角坐标

选项:
    --port PORT   NagiBridge端口（默认 7842）
    --hits N      硬目标额外敲击次数（默认 2）

示例:
    python clear_area.py 50 20 70 30 --port 7842
"""

import argparse
import os
import time
from collections import defaultdict

parser = argparse.ArgumentParser()
parser.add_argument("x1", type=int)
parser.add_argument("y1", type=int)
parser.add_argument("x2", type=int)
parser.add_argument("y2", type=int)
parser.add_argument("--port", type=int, default=7842)
parser.add_argument("--hits", type=int, default=2)
# 🪓 放行名单（恒 2026-09-12）：同 chop_trees —— 默认只清橡/枫/松，特殊树受保护
#    （不然"清一块地"顺手就把蘑菇树/桃花心木铲了）。由 settings 域的 `chop` 设置经服务器传进来。
parser.add_argument("--allow", default="", help="放行的特殊树种（名字/树号，逗号分隔；none/all）")
args = parser.parse_args()

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

TOOL_ORDER = ["Scythe", "Pickaxe", "Axe"]


# 🛡️ 被保护树种挡下的。_PASS 是本轮扫描计数（tile_target_name 填），_SEEN 是所有轮次的合并
#    （取每轮最大值——同一棵树会被 3 轮扫描反复数到，直接累加会灌水）。收尾必须报出来。
_SKIPPED_PASS = {}
_SKIPPED_SEEN = {}


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
        ttype = tt.tree_type_of(terrain)
        if not tt.is_choppable(ttype, _ALLOW):
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
    cx = (args.x1 + args.x2) // 2
    cy = (args.y1 + args.y2) // 2
    radius = max(args.x2 - args.x1, args.y2 - args.y1) // 2 + 5

    if api.current_location() == "Farm":
        api._post("/position", {"x": cx, "y": cy})
    else:
        api.warp("Farm", cx, cy)
    time.sleep(0.6)
    data = api.surroundings(min(radius, 30))

    targets = []
    _SKIPPED_PASS.clear()
    for t in data.get("tiles", []):
        x, y = t["x"], t["y"]
        if x < args.x1 or x > args.x2 or y < args.y1 or y > args.y2:
            continue

        name = tile_target_name(t)
        if not name:
            continue

        tool, hits = TOOL_MAP[name]
        targets.append((x, y, tool, name, hits))

    for k, v in _SKIPPED_PASS.items():        # 本轮看见的受保护树，合并进总账（取最大值防灌水）
        _SKIPPED_SEEN[k] = max(_SKIPPED_SEEN.get(k, 0), v)
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
    data = api.surroundings(2)
    for t in data.get("tiles", []):
        if t.get("x") == x and t.get("y") == y:
            return tile_target_name(t) == expected_name
    return False


def stand_for_target(x, y, use_position):
    stand_x, stand_y = x, y - 1
    if use_position:
        if api.current_location() == "Farm":
            api._post("/position", {"x": stand_x, "y": stand_y})
        else:
            api.warp("Farm", stand_x, stand_y)
        time.sleep(0.3)
        api.face(2)
    else:
        api.move_to(stand_x, stand_y, timeout=8)
        d = api.face_toward(x, y)
        api.face(d)
    time.sleep(0.1)


def clear_pass(targets, use_warp=False):
    by_tool = defaultdict(list)
    for x, y, tool, name, hits in targets:
        by_tool[tool].append((x, y, name, hits))

    cleared = 0
    for tool in TOOL_ORDER:
        items = by_tool.get(tool, [])
        if not items:
            continue

        items = snake_sort(items)
        mode = "position" if use_warp else "move"
        api.log(f"--- {tool}: {len(items)} targets ({mode}) ---")
        api.select(tool)
        time.sleep(0.15)

        for x, y, name, hits in items:
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
            if actual_hits < hits:
                api.log(f"  {name} at ({x},{y}) cleared after {actual_hits}/{hits} hits")

            cleared += 1
            if cleared % 20 == 0:
                api.log(f"  {cleared} cleared...")

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
    api.log(f"=== clear area: ({args.x1},{args.y1})-({args.x2},{args.y2}) ===")
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
