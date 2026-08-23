"""
扫描所有地图的入口坐标
用法: python scripts/scan_entries.py --port 7842
输出: 打印所有可达地图及其安全入口坐标
"""
import requests, sys, time
BASE = f"http://localhost:{sys.argv[sys.argv.index('--port')+1] if '--port' in sys.argv else 7842}"

def get(path):
    try: return requests.get(f"{BASE}{path}", timeout=10).json()
    except: return {}

def post(path, data):
    try: return requests.post(f"{BASE}{path}", json=data, timeout=10).json()
    except: return {}

# 先看当前在什么地图
state = get("/state")
if not state.get("worldReady"):
    print("游戏未就绪")
    sys.exit(1)

current = state["location"]["name"]
print(f"当前位置: {current}")

# 用 warp 跳转到每个能找到的地图，记录入口坐标
entries = {}
discovered = set()
discovered.add(current)
# 从当前位置开始 BFS 探索所有地图
queue = [current]

while queue:
    loc = queue.pop(0)
    if loc in entries:
        continue
    # 看看这个地图有哪些出口
    # warp 到这个地图
    if loc != current:
        r = post("/warp", {"location": loc})
        if not r.get("ok"):
            continue
        time.sleep(0.5)

    map_data = get("/map")
    if not map_data.get("warps"):
        continue

    entries[loc] = {"x": state["player"]["x"], "y": state["player"]["y"]}

    for w in map_data["warps"]:
        target = w["targetLocation"]
        if target not in discovered:
            discovered.add(target)
            queue.append(target)

print(f"\n=== 发现 {len(entries)} 个地图 ===")
for loc, pos in sorted(entries.items()):
    print(f"  {loc}: ({pos['x']}, {pos['y']})")
