"""Keg Manager — scan kegs, load fruit, collect finished products, sell."""

import argparse
import time
import stardew_api as api

MACHINE_NAME = "Keg"


def get_kegs():
    data = api.machines()
    return [m for m in data.get("machines", []) if m["name"] == MACHINE_NAME]


def collect_ready(kegs):
    collected = 0
    for k in kegs:
        if k["status"] != "ready":
            continue
        api.log(f"Collecting from keg at ({k['x']},{k['y']}): {k.get('heldItem','?')}")
        api.interact_machine(k["x"], k["y"])
        collected += 1
    return collected


def load_empty(kegs, fruit_name):
    loaded = 0
    inv = api.state().get("inventory", [])
    fruit_count = sum(i["stack"] for i in inv if i and i["name"] == fruit_name)
    if fruit_count == 0:
        api.log(f"No {fruit_name} in inventory")
        return 0

    for k in kegs:
        if k["status"] != "empty":
            continue
        if fruit_count <= 0:
            api.log("Ran out of fruit")
            break
        api.log(f"Loading {fruit_name} into keg at ({k['x']},{k['y']})")
        api.select(fruit_name)
        time.sleep(0.3)
        api.interact_machine(k["x"], k["y"])
        fruit_count -= 1
        loaded += 1
    return loaded


def run(fruit_name, sell_products=False):
    api.log(f"=== Keg Manager: fruit={fruit_name}, sell={sell_products} ===")

    kegs = get_kegs()
    api.log(f"Found {len(kegs)} kegs")
    if not kegs:
        api.log("No kegs found. Place some kegs first!")
        return

    ready = [k for k in kegs if k["status"] == "ready"]
    empty = [k for k in kegs if k["status"] == "empty"]
    processing = [k for k in kegs if k["status"] == "processing"]
    api.log(f"  Ready: {len(ready)}, Empty: {len(empty)}, Processing: {len(processing)}")

    if ready:
        n = collect_ready(ready)
        api.log(f"Collected {n} products")

    kegs = get_kegs()
    empty = [k for k in kegs if k["status"] == "empty"]
    if empty and fruit_name:
        n = load_empty(empty, fruit_name)
        api.log(f"Loaded {n} kegs")

    if sell_products:
        api.log("Walking to shipping bin...")
        api.move_to(api.SHIPPING_BIN[0], api.SHIPPING_BIN[1] - 1, timeout=15)
        time.sleep(0.5)
        api.sell(sell_all=True)
        api.log("Sold")

    api.log("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Keg automation")
    parser.add_argument("--fruit", default="", help="Fruit to load into empty kegs")
    parser.add_argument("--sell", action="store_true", help="Sell finished products")
    parser.add_argument("--port", type=int, default=7842)
    args = parser.parse_args()

    import os
    os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
    import importlib
    importlib.reload(api)

    run(args.fruit, args.sell)
