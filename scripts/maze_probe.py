"""maze_probe v2 — 万灵节迷宫墙探针（surroundings 扫一片 + dump_tile 精判类型）。

用法：python scripts/maze_probe.py [port=7843] [radius=12]
跑在能连游戏的终端里（我的 CLI 被 hook 拦网络，所以由你跑）。
目的：判断"绿植墙"是地图层碰撞瓦片还是物体/树，决定 BFS 要不要加迷宫障碍特判。
"""
import sys, json, urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7843
RADIUS = int(sys.argv[2]) if len(sys.argv) > 2 else 12
BASE = f"http://localhost:{PORT}"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=12) as r:
        return json.load(r)

try:
    s = get(f"/surroundings?radius={RADIUS}")
except Exception as ex:
    print(f"[ERR] 连不上 {BASE}: {ex}")
    sys.exit(1)

loc = s.get("location")
cx, cy = s.get("center", {}).get("x"), s.get("center", {}).get("y")
tiles = s.get("tiles", [])
print(f"位置: {loc}  中心: ({cx},{cy})  半径: {RADIUS}  上报格数: {len(tiles)}")

# 墙格 = passable=false 的（走不了）；带 object/terrain/resource 的也标出来
walls = [t for t in tiles if t.get("passable") is False]
objs  = [t for t in tiles if t.get("object") or t.get("terrain") or t.get("resource")
         or t.get("largeTerrain")]

print(f"\n—— 墙格(passable=false) 共 {len(walls)} 格 ——")
for t in walls[:300]:
    desc = " ".join(f"{k}={t[k]}" for k in ("object", "terrain", "resource", "largeTerrain", "diggable") if t.get(k))
    print(f"  ({t['x']},{t['y']}) {desc}")

print(f"\n—— 有物体/地形但未必走不过 共 {len(objs)} 格 ——")
for t in objs[:40]:
    print(f"  ({t['x']},{t['y']}) passable={t.get('passable')} object={t.get('object')} terrain={t.get('terrain')} resource={t.get('resource')} largeTerrain={t.get('largeTerrain')}")

# 精判：抽查 3 个墙格，看 mapPassable（地图层）与 object.typeName（实体类型）
print("\n—— 对前 3 个墙格 dump_tile 精判 ——")
seen_types = set()
for t in walls:
    key = (t.get("object"), t.get("terrain"), t.get("resource"))
    if not t.get("object") and not t.get("terrain") and not t.get("resource") and len(seen_types) >= 1:
        pass  # 纯墙(无 object/terrain) 至少抽一个
    if key in seen_types and len(seen_types) >= 3:
        continue
    seen_types.add(key)
    x, y = t["x"], t["y"]
    try:
        d = get(f"/dump_tile?x={x}&y={y}")
        tile = d.get("tile", {}) or {}
        obj = (tile.get("object") or {})
        print(
            f"  ({x},{y}) mapPassable={tile.get('mapPassable')} passable={tile.get('passable')}"
            f"  object.typeName={obj.get('typeName')}  object.name={obj.get('name')}"
            f"  terrain={tile.get('terrain')}  largeTerrain={tile.get('largeTerrain')}"
        )
    except Exception as ex:
        print(f"  ({x},{y}) [ERR {ex}]")
    if len(seen_types) >= 3:
        break
