"""Furnace Manager — load ore+coal, collect bars."""

import argparse
import time
import stardew_api as api

MACHINE_NAME = "Furnace"

ORE_TO_BAR = {
    "Copper Ore": "Copper Bar",
    "Iron Ore": "Iron Bar",
    "Gold Ore": "Gold Bar",
    "Iridium Ore": "Iridium Bar",
}


def get_furnaces():
    data = api.machines()
    return [m for m in data.get("machines", []) if m["name"] == MACHINE_NAME]


def collect_ready(furnaces):
    collected = 0
    for f in furnaces:
        if f["status"] != "ready":
            continue
        api.log(f"Collecting from furnace at ({f['x']},{f['y']}): {f.get('heldItem','?')}")
        api.interact_machine(f["x"], f["y"])
        collected += 1
    return collected


def load_empty(furnaces, ore_name):
    loaded = 0
    inv = api.state().get("inventory", [])
    ore_count = sum(i["stack"] for i in inv if i and i["name"] == ore_name)
    coal_count = sum(i["stack"] for i in inv if i and i["name"] == "Coal")

    if ore_count < 5:
        api.log(f"Need at least 5 {ore_name} (have {ore_count})")
        return 0
    if coal_count < 1:
        api.log(f"Need coal (have {coal_count})")
        return 0

    batches = min(ore_count // 5, coal_count)

    for f in furnaces:
        if f["status"] != "empty":
            continue
        if batches <= 0:
            api.log("Ran out of ore or coal")
            break
        api.log(f"Loading {ore_name} into furnace at ({f['x']},{f['y']})")
        api.select(ore_name)
        time.sleep(0.3)
        api.interact_machine(f["x"], f["y"])
        batches -= 1
        loaded += 1
    return loaded


def run(ore_name):
    api.log(f"=== Furnace Manager: ore={ore_name} ===")

    furnaces = get_furnaces()
    api.log(f"Found {len(furnaces)} furnaces")
    if not furnaces:
        api.log("No furnaces found. Craft and place some first!")
        return

    ready = [f for f in furnaces if f["status"] == "ready"]
    empty = [f for f in furnaces if f["status"] == "empty"]
    processing = [f for f in furnaces if f["status"] == "processing"]
    api.log(f"  Ready: {len(ready)}, Empty: {len(empty)}, Processing: {len(processing)}")

    if ready:
        n = collect_ready(ready)
        api.log(f"Collected {n} bars")

    furnaces = get_furnaces()
    empty = [f for f in furnaces if f["status"] == "empty"]
    if empty and ore_name:
        n = load_empty(empty, ore_name)
        api.log(f"Loaded {n} furnaces")

    api.log("Done!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Furnace automation")
    parser.add_argument("--ore", default="Copper Ore", help="Ore type to smelt")
    parser.add_argument("--port", type=int, default=7842)
    args = parser.parse_args()

    import os
    os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
    import importlib
    importlib.reload(api)

    run(args.ore)
