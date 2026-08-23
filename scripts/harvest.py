"""
收割+出售skill：扫描成熟作物 → 收割 → 送去shipping bin出售

用法:
    python harvest.py <x1> <y1> <x2> <y2> [options]

选项:
    --port PORT        NagiBridge端口（默认 7843）
    --sell             收割后自动送shipping bin出售
    --bin-x X          shipping bin的x坐标（默认 71）
    --bin-y Y          shipping bin的y坐标（默认 14）

示例:
    python harvest.py 55 24 68 28 --sell --port 7843
"""

import argparse
import os
import time

parser = argparse.ArgumentParser()
parser.add_argument("x1", type=int)
parser.add_argument("y1", type=int)
parser.add_argument("x2", type=int)
parser.add_argument("y2", type=int)
parser.add_argument("--port", type=int, default=7843)
parser.add_argument("--sell", action="store_true")
parser.add_argument("--bin-x", type=int, default=71)
parser.add_argument("--bin-y", type=int, default=14)
args = parser.parse_args()

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api

HARVEST_DELAY = 0.4


def scan_harvestable():
    cx = (args.x1 + args.x2) // 2
    cy = (args.y1 + args.y2) // 2
    radius = max(args.x2 - args.x1, args.y2 - args.y1) // 2 + 5

    api.move_to(cx, cy)
    data = api.surroundings(min(radius, 30))

    ready = []
    not_ready = 0
    for t in data.get("tiles", []):
        x, y = t["x"], t["y"]
        if x < args.x1 or x > args.x2 or y < args.y1 or y > args.y2:
            continue
        if t.get("harvestable"):
            ready.append((x, y))
        elif t.get("crop"):
            not_ready += 1

    return ready, not_ready


def harvest_crops(tiles):
    tiles.sort(key=lambda t: (t[1], t[0]))

    # snake pattern for efficiency
    rows = {}
    for x, y in tiles:
        rows.setdefault(y, []).append(x)

    ordered = []
    for i, y in enumerate(sorted(rows.keys())):
        xs = sorted(rows[y])
        if i % 2 == 1:
            xs.reverse()
        for x in xs:
            ordered.append((x, y))

    for tx, ty in ordered:
        # 先自然走，走不到就 position 兜底
        ok = api.move_to(tx, ty - 1)
        # 验证位置，没到位就 position
        s = api.state()
        px, py = s["player"]["x"], s["player"]["y"]
        if px != tx or py != ty - 1:
            api.position(tx, ty - 1)
            time.sleep(0.1)
        api.face(2)
        time.sleep(0.1)
        api.key("confirm")  # 收获作物不走 checkAction，要用 pressActionButton
        time.sleep(HARVEST_DELAY)

    return len(ordered)


def sell_crops():
    api.log(f"walking to shipping bin ({args.bin_x},{args.bin_y})...")
    api.move_to(args.bin_x, args.bin_y - 1)
    api.face(2)
    time.sleep(0.3)

    s = api.state()
    inv = s.get("inventory", [])

    sold = []
    crop_categories = ["作物", "花", "蔬菜", "水果",
                       "Crops", "Flowers", "Vegetables", "Fruit",
                       "采集"]

    for item in inv:
        cat = item.get("category", "")
        name = item.get("name", "")
        # sell anything that's not a tool, seed, or resource
        if cat in ["工具", "Tools", "种子", "Seeds"]:
            continue
        if name in ["Axe", "Hoe", "Watering Can", "Pickaxe", "Scythe",
                     "Bamboo Pole", "Chest"]:
            continue
        sold.append(name)

    if not sold:
        api.log("nothing to sell")
        return

    # interact with shipping bin to open, then add items
    api.interact()
    time.sleep(0.5)

    api.log(f"selling {len(sold)} items: {sold}")
    # For each crop item, select it and interact with the bin
    for name in sold:
        api.select(name)
        time.sleep(0.2)
        api.interact()
        time.sleep(0.3)


def run():
    api.log(f"=== harvest skill: ({args.x1},{args.y1})-({args.x2},{args.y2}) ===")

    # 检查背包空间
    inv = api.state().get("inventory", [])
    used = len([i for i in inv if i.get("name")])
    max_slots = api.state().get("player", {}).get("maxItems", 36)
    free = max_slots - used
    api.log(f"背包: {used}/{max_slots} ({free} 空)")

    ready, not_ready = scan_harvestable()
    api.log(f"harvestable: {len(ready)}, growing: {not_ready}")

    if not ready:
        api.log("nothing to harvest yet")
        return

    # 每种作物占 1 格（可堆叠），但保险起见留至少 2 格
    if free < 2:
        api.log(f"背包快满了！只有 {free} 格空位，先存箱子再收")
        api.log("=== harvest aborted — need inventory space ===")
        return

    count = harvest_crops(ready)
    api.log(f"harvested {count} crops")

    if args.sell:
        sell_crops()

    api.log("=== harvest done ===")


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
