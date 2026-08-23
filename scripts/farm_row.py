"""
种田skill：翻地 → 播种 → 浇水（蛇形走位，带体力/水量检测）

用法:
    python farm_row.py <start_x> <start_y> <length> [options]

选项:
    --seed NAME        种子名（默认 Parsnip Seeds）
    --dir DIR          种植方向: right/left/down/up（默认 right）
    --rows N           种几排（默认 1）
    --row-spacing N    排间距（默认 1）
    --skip-water       跳过浇水
    --hoe-only         仅锄地，不播种不浇水（无需种子）
    --port PORT        NagiBridge端口（默认 7843）
"""

import sys
import time
import argparse
import os

parser = argparse.ArgumentParser()
parser.add_argument("start_x", type=int)
parser.add_argument("start_y", type=int)
parser.add_argument("length", type=int)
parser.add_argument("--seed", default="Parsnip Seeds")
parser.add_argument("--dir", default="right", choices=["right", "left", "down", "up"])
parser.add_argument("--rows", type=int, default=1)
parser.add_argument("--row-spacing", type=int, default=1)
parser.add_argument("--skip-water", action="store_true")
parser.add_argument("--hoe-only", action="store_true",
                    help="仅锄地，不播种不浇水（无需种子）")
parser.add_argument("--plant-only", action="store_true",
                    help="仅播种（在已锄好的地上种，不锄不浇）——2026-08-15 恒：独立播种工具")
parser.add_argument("--port", type=int, default=7843)
args = parser.parse_args()

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api

TOOL_DELAY = 0.55
STAMINA_MIN = 20  # 体力低于此值强制停，绝对值比百分比更合理（星之果实后不会浪费）

DIR_MAP = {
    "right": (1, 0),
    "left":  (-1, 0),
    "down":  (0, 1),
    "up":    (0, -1),
}

ROW_OFFSET = {
    "right": (0, 1),
    "left":  (0, 1),
    "down":  (1, 0),
    "up":    (1, 0),
}


def calc_tiles(sx, sy, length, direction, rows, row_spacing):
    dx, dy = DIR_MAP[direction]
    rdx, rdy = ROW_OFFSET[direction]
    tiles = []
    for r in range(rows):
        row = []
        for i in range(length):
            tx = sx + dx * i + rdx * r * row_spacing
            ty = sy + dy * i + rdy * r * row_spacing
            row.append((tx, ty))
        if r % 2 == 1:
            row.reverse()
        tiles.append(row)
    return tiles


def check_stamina():
    cur, mx = api.player_stamina()
    if cur < STAMINA_MIN:
        api.log(f"stamina low: {cur}/{mx} (min {STAMINA_MIN}), stopping")
        return False
    return True


def refill_water():
    api.log("watering can empty, refilling...")
    result = api.refill_water()
    if result.get("ok"):
        api.log(f"refilled: {result.get('water')}/{result.get('max')}")
        api.select("Watering Can")
        time.sleep(0.15)
        return True
    api.log("could not refill")
    return False


def flatten_tiles(tiles):
    """Flatten multi-row tiles into a single snake-ordered list."""
    flat = []
    for row in tiles:
        flat.extend(row)
    return flat


def do_action(tx, ty, tool_or_item, is_watering):
    """Move to tile and use tool/item — 自然走路 + position 兜底"""
    if not check_stamina():
        api.log(f"stopped at ({tx},{ty}) due to low stamina")
        return False

    if is_watering:
        water, _ = api.watering_can_water()
        if water is not None and water <= 0:
            if not refill_water():
                return False
            api.select("Watering Can")
            time.sleep(0.15)

    stand_x, stand_y = tx, ty - 1
    # 先试自然走，走不到会 position 兜底（反正人一定到目标）
    walked = api.walk_natural(stand_x, stand_y)
    if not walked:
        api.log(f"  ({tx},{ty}) position fallback")
    # ⚠️ 2026-08-15 恒：走位/瞬移会重置选中——每次操作前重新 select（种到第三排才拿种子=没重选）
    api.select(tool_or_item)
    time.sleep(0.1)
    api.face(2)
    time.sleep(0.1)
    api.use_item()
    time.sleep(TOOL_DELAY)
    return True


def run():
    # --- Pre-checks ---
    api.log("=== pre-check ===")
    if args.hoe_only:
        api.log("  --hoe-only 模式，跳过种子检查")
    else:
        errors = api.precheck_farm(args.seed)
        if errors:
            for e in errors:
                api.log(f"FAIL: {e}")
            api.log("=== pre-check failed, aborting ===")
            return

    api.clear_menu()
    api.drain_alerts()

    tiles = calc_tiles(args.start_x, args.start_y, args.length,
                       args.dir, args.rows, args.row_spacing)
    flat = flatten_tiles(tiles)
    total = len(flat)

    blocked = api.precheck_area(flat)
    if blocked:
        api.log(f"BLOCKED: {len(blocked)} obstacles in planting area:")
        for pos, name in blocked:
            api.log(f"  ({pos[0]},{pos[1]}): {name}")
        api.log("=== aborting — clear obstacles first or choose another area ===")
        sys.exit(2)

    api.log(f"=== farm skill: {len(flat)}/{total} tiles, dir={args.dir}, rows={args.rows}, seed={args.seed} ===")

    # ⚠️ 2026-08-15 恒：--plant-only = 独立播种（在已锄好地上种，不锄不浇）
    if args.plant_only:
        phases = [(args.seed, "plant", False)]
    else:
        phases = [("Hoe", "till", False)]
        if not args.hoe_only:
            phases.append((args.seed, "plant", False))
            if not args.skip_water:
                phases.append(("Watering Can", "water", True))

    # ⚠️ 2026-08-15 恒：播种前扫已种地块，跳过（不重复种/不覆盖）
    planted = set()
    if any(pn == "plant" for _, pn, _ in phases):
        try:
            ps = api.surroundings(max(args.length, args.rows) // 2 + 8)
            for t in ps.get("tiles", []):
                if t.get("crop"):
                    planted.add((t["x"], t["y"]))
        except Exception:
            pass

    for phase_idx, (tool, phase_name, is_watering) in enumerate(phases):
        api.log(f"--- {phase_name}: {tool} ---")
        api.select(tool)
        time.sleep(0.15)

        order = flat if phase_idx % 2 == 0 else list(reversed(flat))
        skipped = 0
        for tx, ty in order:
            if phase_name == "plant" and (tx, ty) in planted:
                skipped += 1
                continue  # 已种，跳过（2026-08-15 恒）
            if not do_action(tx, ty, tool, is_watering):
                api.log(f"=== stopped during {phase_name} ===")
                return
        if skipped:
            api.log(f"  ↪ 跳过 {skipped} 个已种地块")

    # --- Post-check ---
    api.postcheck_menu()
    api.log(f"=== done ===")


if __name__ == "__main__":
    try:
        st = api.status()
        if not st.get("worldReady"):
            print("game not ready")
            sys.exit(1)
    except Exception as e:
        print(f"cannot connect: {e}")
        sys.exit(1)
    run()
