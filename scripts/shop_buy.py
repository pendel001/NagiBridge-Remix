"""
购物skill：检查背包 → 清理到箱子 → 传送商店 → 点掉对话 → 购买 → 传回

用法:
    python shop_buy.py --items "493:10,491:6" --port 7843

    物品格式: item_id:count,item_id:count
    例: 493:10 = 买10个Cranberry Seeds
        491:6  = 买6个Bok Choy Seeds

选项:
    --items ITEMS       物品ID:数量，逗号分隔
    --chest-x X         箱子X坐标（默认70）
    --chest-y Y         箱子Y坐标（默认14）
    --return-x X        购买后返回农场X坐标（默认58）
    --return-y Y        购买后返回农场Y坐标（默认17）
    --port PORT         NagiBridge端口（默认7843）
"""

import argparse
import os
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--items", required=True, help="item_id:count pairs, comma separated")
parser.add_argument("--chest-x", type=int, default=70)
parser.add_argument("--chest-y", type=int, default=14)
parser.add_argument("--return-x", type=int, default=58)
parser.add_argument("--return-y", type=int, default=17)
parser.add_argument("--port", type=int, default=7843)
parser.add_argument("--skip-backpack-check", action="store_true",
                    help="Skip screenshot backpack verification (caller already confirmed)")
args = parser.parse_args()

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api

MIN_FREE_SLOTS = 3
RETURN_RETRIES = 3


def parse_items(items_str):
    result = []
    for pair in items_str.split(","):
        pair = pair.strip()
        if ":" in pair:
            item_id, count = pair.split(":")
            result.append((item_id.strip(), int(count.strip())))
    return result


def count_free_slots():
    s = api.state()
    inventory = s.get("inventory", [])
    used = len(inventory)
    return 36 - used, used


def store_to_chest(cx, cy):
    api.log(f"Storing items to chest at ({cx},{cy})...")
    api.warp("Farm", cx, cy - 1)
    time.sleep(0.5)
    api.face(2)
    time.sleep(0.2)
    result = api._post("/store", {"x": cx, "y": cy})
    time.sleep(0.3)
    api.log(f"  Store result: {result.get('stored', 0)} items stored")
    return result


def dismiss_dialogues(max_attempts=5):
    for i in range(max_attempts):
        s = api.state()
        if s.get("activeMenu") or s.get("activeEvent"):
            api.key("confirm")
            time.sleep(0.4)
        else:
            return True
    return False


def buy_item(item_id, count):
    bought = 0
    for i in range(count):
        result = api._post("/buy", {"id": item_id, "count": 1})
        if result.get("ok"):
            bought += 1
        else:
            err = result.get("error", "unknown")
            api.log(f"  Buy failed at {bought}/{count}: {err}")
            break
    return bought


def warp_back_to_farm(x, y):
    s = api.state()
    if s["location"]["name"] != "Farm":
        api.stop()
        time.sleep(0.2)
        result = api.warp("Farm")
        api.log(f"  Return map warp: {result}")
        for _ in range(20):
            time.sleep(0.25)
            if api.state()["location"]["name"] == "Farm":
                break

    for attempt in range(1, RETURN_RETRIES + 1):
        api.stop()
        time.sleep(0.2)
        result = api._post("/position", {"x": x, "y": y})
        time.sleep(0.8)

        s = api.state()
        loc = s["location"]["name"]
        px = s["player"]["x"]
        py = s["player"]["y"]
        api.log(f"  Return attempt {attempt}: position={result}, actual={loc} ({px},{py})")

        if loc == "Farm" and px == x and py == y:
            return True

    return False


def run():
    items = parse_items(args.items)
    if not items:
        api.log("No items to buy!")
        return

    total_needed = sum(count for _, count in items)
    api.log(f"=== Shopping: {len(items)} item types, {total_needed} total ===")

    # Check inventory space — API can be inaccurate in multiplayer, so screenshot for verification
    free, used = count_free_slots()
    api.log(f"Inventory (API): {used}/36 used, {free} free")

    if not args.skip_backpack_check:
        img_path = api.screenshot()
        api.log(f"BACKPACK_SCREENSHOT: {img_path}")
        api.log("=== verify backpack has space, then re-run with --skip-backpack-check ===")
        sys.exit(2)

    if free < MIN_FREE_SLOTS:
        api.log(f"Less than {MIN_FREE_SLOTS} free slots, storing to chest first...")
        store_to_chest(args.chest_x, args.chest_y)
        time.sleep(0.5)
        free, used = count_free_slots()
        api.log(f"After storing: {used}/36 used, {free} free")

    # Warp to shop
    api.log("Warping to SeedShop...")
    api.warp("SeedShop")
    time.sleep(1.0)

    # Dismiss Pierre's dialogue
    api.log("Dismissing dialogue...")
    dismiss_dialogues()
    time.sleep(0.3)

    # Buy items
    s = api.state()
    gold_before = s["player"]["money"]
    api.log(f"Gold: {gold_before}g")

    for item_id, count in items:
        api.log(f"Buying item {item_id} x{count}...")
        bought = buy_item(item_id, count)
        api.log(f"  Bought {bought}/{count}")

    # Post-check: verify items in inventory
    s = api.state()
    gold_after = s["player"]["money"]
    api.log(f"Spent: {gold_before - gold_after}g, remaining: {gold_after}g")

    inv_names = [i.get("name", "") for i in s.get("inventory", [])]
    for item_id, count in items:
        api.log(f"  Post-check: item {item_id} in inventory: {item_id in ' '.join(inv_names) or 'checking by gold spent'}")

    if gold_before == gold_after and total_needed > 0:
        api.log("WARNING: no gold spent — purchase may have failed entirely!")

    # Post-buy screenshot for verification
    img_path = api.screenshot(os.path.join(os.path.expanduser("~"), "nagi", "post_buy_check.png"))
    api.log(f"POST_BUY_SCREENSHOT: {img_path}")

    # Clear any remaining menus
    api.clear_menu()
    api.drain_alerts()

    # Warp back to a visible farm tile for observation.
    api.log(f"Warping back to Farm ({args.return_x},{args.return_y})...")
    returned = warp_back_to_farm(args.return_x, args.return_y)
    if not returned:
        api.log("  WARNING: did not land at requested return tile")
    dismiss_dialogues()

    api.log("=== Shopping done ===")


if __name__ == "__main__":
    try:
        st = api.status()
        if not st.get("worldReady"):
            print("Game not ready")
            sys.exit(1)
    except Exception as e:
        print(f"Cannot connect: {e}")
        sys.exit(1)
    run()
