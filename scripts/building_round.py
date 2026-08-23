"""building_round.py — 一屋一屋地做：进门 → 收 → 放（拟人走路版）。

用法:
    python building_round.py --collect --place Starfruit --place-type Keg --locations "Big Shed,Cabin"
    python building_round.py --collect --locations "Cabin"
    python building_round.py --place "Starfruit Wine" --place-type Cask --locations Cellar

每间屋流程:
    （走到/传到门口）→ 开门进去 → machine_collect(location=屋) → machine_loader(item, --location 屋, --no-enter)
    然后下一屋。跨屋用 walk_to（walk_to 不肯出门时回退 warp 到门外）。
"""

import argparse
import subprocess
import sys
import os
import time
import stardew_api as api

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _wait_arrival(tx, ty, timeout=30):
    """轮询等到达 (tx,ty)（2 格内）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = api.state()
            px, py = s.get("player", {}).get("x"), s.get("player", {}).get("y")
            if px is not None and py is not None:
                if abs(px - tx) <= 2 and abs(py - ty) <= 2 and not s.get("player", {}).get("isMoving"):
                    return True
        except Exception:
            pass
        time.sleep(0.8)
    return False


def _current_location():
    try:
        return api.machines().get("location")
    except Exception:
        return None


def _find_building_door(loc):
    """从该地点任意一台机器的 building 字段拿门坐标。"""
    try:
        fr = api.farm_report()
        machines = (fr.get("machines") or {}).get("machines") or []
    except Exception:
        machines = []
    for m in machines:
        if str(m.get("location", "")).lower() == str(loc).lower() and m.get("building"):
            return m["building"]
    return None


def enter_building(loc):
    """走进一间屋子（建筑走门；地窖/室外 warp_into）。返回 True/False。"""
    b = _find_building_door(loc)

    if b and b.get("doorX") is not None:
        # 1. 到门外：先试 walk_to（拟人），走不到（如 walk_to 不肯出门）回退 warp 到门口
        api.log(f"🚶 到 {loc} 门口 Farm({b['doorX']},{b['doorY']})")
        api._post("/walk_to", {"location": "Farm", "x": b["doorX"], "y": b["doorY"]})
        if not _wait_arrival(b["doorX"], b["doorY"], timeout=20):
            api.log("  walk_to 走不到，warp 到门外")
            wr = api.warp_into("Farm", b["doorX"], b["doorY"])
            if not wr.get("ok"):
                return False
            time.sleep(0.5)

        # 2. 已在屋内就不用开门
        if _current_location() and str(_current_location()).lower() == str(loc).lower():
            return True

        # 3. 站门口正下方 → 面朝门 → interact 开门进去
        api.log(f"🚪 开门进 {loc}")
        api._post("/position", {"x": b["doorX"], "y": b["doorY"] + 1})
        time.sleep(0.5)
        api._post("/face", {"direction": 0})   # 0=上，门在头顶
        time.sleep(0.3)
        api._post("/interact")
        for _ in range(10):
            time.sleep(0.8)
            if _current_location() and str(_current_location()).lower() == str(loc).lower():
                return True
        return False
    else:
        # 非建筑（地窖/室外等）→ 直接 warp_into 入口
        api.log(f"🏰 warp 进 {loc}（非建筑）")
        wr = api.warp_into(loc)
        return wr.get("ok", False)


def do_location(loc, collect, place_item, place_type):
    """一间屋子完整一轮：进门 → 收 → 放。返回 True=至少做了一样。"""
    if not enter_building(loc):
        api.log(f"❌ 进不去 {loc}")
        return False
    ok = False

    if collect:
        api.log(f"🧺 收 {loc} ...")
        r = api.machine_collect(location=loc)
        if r.get("ok"):
            n = r.get("collected", 0)
            sk = r.get("skippedFull", 0)
            api.log(f"  ✅ 收 {n} 件" + (f"（背包满跳过 {sk}）" if sk else ""))
            ok = True
        else:
            api.log(f"  ⚠️ 收失败: {r.get('error', '')}")

    if place_item:
        api.log(f"🍶 放 {place_item} -> {loc} ...")
        cmd = [sys.executable, os.path.join(SCRIPT_DIR, "machine_loader.py"),
               place_item, "--location", loc, "--no-enter"]
        if place_type:
            cmd += ["--type", place_type]
        out = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=900, cwd=SCRIPT_DIR,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        ).stdout
        api.log("  " + (out or "").strip()[-400:])
        ok = True
    return ok


def run(collect, place_item, place_type, locations):
    api.log(f"=== Building Round: locations={locations or 'ALL'} collect={collect} place={place_item or '-'} ===")

    if not locations:
        # 没指定 → farm_report 里所有有机器的地方（按出现顺序）
        try:
            fr = api.farm_report()
            ml = (fr.get("machines") or {}).get("machines") or []
            seen = []
            for m in ml:
                if m.get("location") not in seen:
                    seen.append(m.get("location"))
            locations = seen
        except Exception:
            locations = []
    api.log(f"处理屋子: {locations}")

    done = 0
    for loc in locations:
        api.log(f"\n── {loc} ──")
        if do_location(loc, collect, place_item, place_type):
            done += 1
    api.log(f"完成: 处理 {done}/{len(locations)} 间屋子")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Per-building round: enter → collect → place")
    parser.add_argument("--collect", action="store_true", help="收该屋机器产物")
    parser.add_argument("--place", default="", help="放原料（英文名/ID，如 Starfruit）")
    parser.add_argument("--place-type", default="", help="放原料的机器类型，如 Keg / Cask")
    parser.add_argument("--locations", default="", help="要处理的屋子（逗号分隔，空=所有有机器的地方）")
    parser.add_argument("--port", type=int, default=7842)
    args = parser.parse_args()

    os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
    import importlib
    importlib.reload(api)

    locs = [x.strip() for x in args.locations.split(",") if x.strip()] if args.locations else []
    run(args.collect, args.place, args.place_type, locs)
