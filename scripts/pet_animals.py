"""Pet Animals — visit all animals, pet unpetted ones, report status."""

import argparse
import time
import stardew_api as api


def run():
    api.log("=== Pet Animals ===")

    data = api.animals()
    all_animals = data.get("animals", [])

    if not all_animals:
        loc = data.get("location", "?")
        api.log(f"No animals found at {loc}. Go to Farm or enter a barn/coop.")
        return

    unpetted = [a for a in all_animals if not a["wasPetToday"]]
    api.log(f"Found {len(all_animals)} animals, {len(unpetted)} need petting")

    for a in all_animals:
        status = "OK" if a["wasPetToday"] else "NEED PET"
        api.log(f"  {a['name']} ({a['type']}) @ ({a['x']},{a['y']}) "
                f"friendship={a['friendship']} happiness={a['happiness']} [{status}]")

    for a in unpetted:
        api.log(f"Petting {a['name']}...")
        api.interact_machine(a["x"], a["y"])

        m = api.menu()
        if m.get("open"):
            api.log(f"  Menu popped up: {m.get('type')}")
            if m.get("dialogue"):
                api.log(f"  \"{m['dialogue']}\"")
            api.key("confirm")
            time.sleep(0.3)

    data = api.animals()
    still_unpetted = [a for a in data.get("animals", []) if not a["wasPetToday"]]
    api.log(f"Done! {len(all_animals) - len(still_unpetted)}/{len(all_animals)} petted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pet all animals")
    parser.add_argument("--port", type=int, default=7842)
    args = parser.parse_args()

    import os
    os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
    import importlib
    importlib.reload(stardew_api)

    run()
