"""
一键导航：AI 说"去海滩钓鱼" -> 自动跨地图走到钓点

用法:
    python scripts/go_to.py "皮埃尔商店" --port 7842
    python scripts/go_to.py "海滩钓鱼点(码头)" --from Farm
"""
import argparse, json, os, sys, time
import requests

sys.path.insert(0, os.path.dirname(__file__))
from locations import POI, ROUTES, plan_route

BASE = "http://localhost:7842"

def api(method, path, data=None, retries=3):
    for i in range(retries):
        try:
            if method == "GET":
                r = requests.get(f"{BASE}{path}", timeout=10)
            else:
                r = requests.post(f"{BASE}{path}", json=data or {}, timeout=30)
            return r.json()
        except Exception as e:
            if i < retries - 1:
                time.sleep(1)
                continue
            return {"error": str(e)}

def state():
    return api("GET", "/state")

def wait_arrival(target_map, target_x=None, target_y=None, timeout=20):
    """轮询直到到达目标地图（和可选坐标），最多等 timeout 秒"""
    start = time.time()
    while time.time() - start < timeout:
        s = state()
        loc = s.get("location", {}).get("name", "")
        p = s.get("player", {})
        if loc == target_map:
            if target_x is None or (abs(p.get("x", 0) - target_x) <= 3 and abs(p.get("y", 0) - target_y) <= 3):
                return s
        time.sleep(0.5)
    return state()

def warp_to(location, entry_x=None, entry_y=None):
    """warp 并等待到达，可指定入口坐标"""
    cur = state()
    cur_map = cur.get("location", {}).get("name", "")
    data = {"location": location}
    if entry_x is not None:
        data["x"] = entry_x
        data["y"] = entry_y or 10
    r = api("POST", "/warp", data)
    if r.get("ok"):
        print(f"  [warp] {location}...", end="", flush=True)
        s = wait_arrival(location, timeout=15)
        arrived = s.get("location", {}).get("name", "")
        p = s.get("player", {})
        if arrived == location:
            print(f" done at ({p.get('x')},{p.get('y')})")
        else:
            print(f" ? at {arrived} ({p.get('x')},{p.get('y')})")
    return r

def walk_to(location, x, y):
    """walk_to 并等待到达"""
    r = api("POST", "/walk_to", {"location": location, "x": x, "y": y})
    if r.get("ok"):
        print(f"  [walk] to ({x},{y})...", end="", flush=True)
        s = wait_arrival(location, x, y, timeout=20)
        p = s.get("player", {})
        cur = s.get("location", {}).get("name", "")
        print(f" at {cur} ({p.get('x')},{p.get('y')})")
    return r

def go(poi_name, from_map=None):
    """一键到达 POI"""

    # 查 POI 是否存在
    if poi_name in POI:
        target = POI[poi_name]
        dest_map = target["map"]
        dest_pos = target["pos"]
    else:
        dest_map = poi_name
        dest_pos = None
        target = None

    # 确定当前位置
    s = state()
    if not s.get("worldReady"):
        print("[FAIL] game not ready")
        return False
    current_map = from_map or s["location"]["name"]
    current_pos = (s["player"]["x"], s["player"]["y"])

    print(f"[pos] {current_map} {current_pos}")
    print(f"[target] {poi_name} -> {dest_map} {dest_pos}")

    # 规划路线
    route = plan_route(current_map, poi_name)
    if not route:
        print(f"[FAIL] no route from {current_map} to {poi_name}")
        return False

    # Already on target map: warp to entry first, then walk
    if len(route) == 1 and route[0][0] == current_map:
        map_entries = {}
        for src, direction, dst, entry in ROUTES:
            map_entries[dst] = entry
        entry = map_entries.get(current_map)
        if entry:
            warp_to(current_map, entry[0], entry[1])
        if dest_pos:
            print(f"  [walk] to ({dest_pos[0]},{dest_pos[1]})")
            walk_to(dest_map, dest_pos[0], dest_pos[1])
        return True

    print(f"[route] {' -> '.join(m for m,_,_ in route)}")
    print("-" * 40)

    # walk each route segment
    map_entries = {}
    for src, direction, dst, entry in ROUTES:
        map_entries[dst] = entry

    for i, (map_name, pos, note) in enumerate(route):
        is_last = (i == len(route) - 1)

        if target and is_last:
            print(f"\n  [walk] to {poi_name} ({pos})")
            walk_to(dest_map, pos[0], pos[1])
        elif pos:
            entry = map_entries.get(map_name)
            if entry:
                print(f"\n  [warp] {i+1}. -> {map_name} @ {entry}")
                warp_to(map_name, entry[0], entry[1])
            else:
                print(f"\n  [warp] {i+1}. -> {map_name}")
                warp_to(map_name)
        else:
            print(f"\n  [warp] {i+1}. -> {map_name}")
            warp_to(map_name)

    # 最终位置
    time.sleep(1)
    s = state()
    p = s.get("player", {})
    print(f"\n[arrived] {s.get('location',{}).get('name')} ({p.get('x')},{p.get('y')})")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Navigate to Stardew Valley POI")
    parser.add_argument("destination", nargs="?", default=os.environ.get("GOAL", ""),
                        help="POI name or map name")
    parser.add_argument("--port", type=int, default=int(os.environ.get("NAGI_PORT", "7842")))
    parser.add_argument("--from", dest="from_map", help="start map (default: auto-detect)")
    args = parser.parse_args()

    BASE = f"http://localhost:{args.port}"
    dest = args.destination or os.environ.get("GOAL", "")
    if not dest:
        print("Usage: python go_to.py <POI name or number>")
        print("Or: set GOAL env var")
        print(f"Available POIs ({len(POI)}):")
        for i, name in enumerate(sorted(POI)):
            p = POI[name]
            print(f"  [{i}] {name}  -> {p['map']} {p['pos']}  {p['note']}")
        sys.exit(1)
    # Support numeric index
    if dest.isdigit():
        idx = int(dest)
        names = sorted(POI)
        if 0 <= idx < len(names):
            dest = names[idx]
    go(dest, args.from_map)
