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


# 「走到了」的判据：跟目标格差 ≤5 格（POI 常常是**不可走格** —— 门口、柜台、床，
# 人会停在旁边 1~3 格；5 格是留余量，不是让人停半路就报到达）。
NEAR_TOL = 5


def near(location, x, y, tol=NEAR_TOL):
    """现在是不是「已经在这张图、且离目标 ≤tol 格」。

    ⚠️ 三态，别把「不知道」当成「没走到」：`True`=到了 / `False`=确证还差得远 / `None`=**读不到状态**。
       只有 `False` 才允许走「回入口再走」那一档 —— 读不到就去 warp，等于**凭一次网络抖动瞬移**，
       正是恒这次报的那种毛病。
    """
    try:
        s = state()
    except Exception:
        return None
    if not (s.get("location") or {}).get("name"):
        return None
    if s["location"]["name"] != location:
        return False
    p = s.get("player") or {}
    try:
        return abs(p.get("x", 0) - x) <= tol and abs(p.get("y", 0) - y) <= tol
    except TypeError:
        return None

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
    """warp 并等待到达，可指定入口坐标

    ⚠️ 2026-11-08：`y` 原来写的是 `entry_y or 10` —— **`0` 是假值** ⇒ 所有 y=0 的入口
       （`Beach` 就是 **(38,0)**）都被悄悄改成 10。差点又变成"瞬移到海滩别处"的另一半原因
       （真机回执 `[warp] Beach... done at (38,10)`）。
       ⇒ 判"有没有给"，不判"真假值"。
    """
    cur = state()
    cur_map = cur.get("location", {}).get("name", "")
    data = {"location": location}
    if entry_x is not None:
        data["x"] = entry_x
        data["y"] = entry_y if entry_y is not None else 10
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

    # 同图：**直接走**（2026-11-08 恒：「转头去鱼店的时候出发点好像瞬移到了海滩的其他地方再过去的」）
    #   ⚠️ 老版这里**无条件** `warp_to(current_map, entry)` —— 真机那一趟（`map walk poi_name=鱼店(门口)`）：
    #      人已经在 Beach **(52,25)**（码头），目标 `鱼店(门口)` 也在 Beach **(30,34)**，
    #      它却先 `/warp Beach (38,10)` 再走过去 ⇒ 凭空瞬移一段，路还变长了（回执里那行
    #      `[warp] Beach... done at (38,10)` 就是恒看到的那一下）。
    #   📌 为什么那是错的：`entry` 是**从别的图进来时游戏把人放在哪**，是跨图落客的副作用，
    #      不是"这张图的起点"。同图**没有任何**理由先传入口 —— 游戏自己会放，我们不用代劳。
    #   ⇒ 先走；真走不到（挡死 / 站在出不去的地方）才退回"回入口再走"，而且**明说**做了这件事。
    if len(route) == 1 and route[0][0] == current_map:
        map_entries = {}
        for src, direction, dst, entry in ROUTES:
            map_entries[dst] = entry
        if dest_pos:
            print(f"  [walk] to ({dest_pos[0]},{dest_pos[1]})")
            walk_to(dest_map, dest_pos[0], dest_pos[1])
            # `near()` 三态：只有**确证**走不到（False）才兜底；读不到状态（None）就当走完了 ——
            # 宁可停在原地让人看见，也不凭一次读失败去瞬移（恒报的正是"凭空瞬移"）。
            if near(dest_map, dest_pos[0], dest_pos[1]) is not False:
                return True
            entry = map_entries.get(current_map)
            if entry:
                print(f"  [fallback] 走不到（挡死 / 站的地方出不去）→ 先回入口 {entry} 再走一次")
                warp_to(current_map, entry[0], entry[1])
                walk_to(dest_map, dest_pos[0], dest_pos[1])
                if near(dest_map, dest_pos[0], dest_pos[1]) is False:
                    # 🚫 `navigation` 认 `[FAIL]` 这个标记 —— 兜底也没到就如实报，别让它说成「已导航到」
                    print(f"  [FAIL] 走不到「{poi_name}」：回入口再走一次也没到"
                          f"（挡死 / 目标在够不着的地方）")
            else:
                print(f"  [fallback] 走不到，且 {current_map} 没登记入口 → 停在这儿（不再乱动）")
                print(f"  [FAIL] 没走到「{poi_name}」：{current_map} 没登记入口，不敢乱 warp")
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
