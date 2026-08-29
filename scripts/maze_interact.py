"""maze_interact — 一次性验证迷宫宝箱(24,53)可交互，并看弹了啥窗口。

用法：python scripts/maze_interact.py [x] [y] [port]
默认打 (24,53) → 7843（AI）。只做：POST /interact + 读 /state 的 activeMenu/dialogue。
"""
import sys, json, urllib.request

x = int(sys.argv[1]) if len(sys.argv) > 1 else 24
y = int(sys.argv[2]) if len(sys.argv) > 2 else 53
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 7843
BASE = f"http://localhost:{PORT}"

def post(path, data=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(data or {}).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.load(r)

st = get("/state")
p = st.get("player", {})
loc = st.get("location", {})
print(f"当前: {loc.get('name')}  玩家=({p.get('x')},{p.get('y')})  朝向={p.get('facingDirection')}")
print(f"目标格: ({x},{y})  距离玩家 dx={x - p.get('x')} dy={y - p.get('y')}  ({'相邻' if abs(x-p.get('x'))+abs(y-p.get('y'))==1 else '不相邻'})")

print("\n→ POST /interact", (x, y))
try:
    res = post("/interact", {"x": x, "y": y})
    print("   返回:", json.dumps(res, ensure_ascii=False))
except Exception as ex:
    print("   [ERR]", ex)
    sys.exit(1)

import time
time.sleep(0.5)

st2 = get("/state")
menu = st2.get("activeMenu") or {}
print(f"\n→ 交互后 activeMenu: type={menu.get('type')}  dialogue={menu.get('dialogue')}")
if menu.get("type"):
    print(f"   弹了菜单「{menu.get('type')}」—— 宝箱/奖励开了（AI 后续自己读/点）")
else:
    print("   没弹菜单——可能不是 Chest 那种会开窗的，或交互方向/距离不对")

# 🔍 追加：dump 目标附近 5x5 窗口，找"宝箱/奖励"到底在哪格、什么类型
print(f"\n—— dump ({x},{y}) 附近 5x5 窗口（找真实奖励格）——")
for wy in range(y - 2, y + 3):
    row = []
    for wx in range(x - 2, x + 3):
        d = get(f"/dump_tile?x={wx}&y={wy}")
        tile = d.get("tile", {}) or {}
        obj = (tile.get("object") or {})
        o = obj.get("typeName") or (obj.get("name") if obj else None) or ""
        tn = tile.get("terrain") or ""
        lt = tile.get("largeTerrain") or ""
        mp = tile.get("mapPassable")
        tag = ""
        if o: tag = f"OBJ:{o}"
        elif tn: tag = f"T:{tn}"
        elif lt: tag = f"LT:{lt}"
        elif mp is False: tag = "#"
        row.append(f"{wx},{wy}{':' + tag if tag else ''}")
    print("  " + "  |  ".join(row))
