"""
🎁 pickup_scene.py — 捡当前场景的可拾取地面物品

用途：
- 畜棚/鸡舍收集鸡蛋、鸭蛋、鸭毛、羊毛、松露等（动物产物掉地上）
- 采集物（Spring Onion、Truffle 等）
- 玩家自己丢在地上的物品

原理：扫 /surroundings，凡"可走(passable=True) + 有 object"的格就是地上物品，
走过去自动拾取。黑名单排除箱子/洒水器/火把等能踩上去但不是要捡的结构。

用法:
  python pickup_scene.py                    # 捡当前场景全部可拾取物
  python pickup_scene.py --port 7843
  python pickup_scene.py --max 20 --dry-run # 只看不捡
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[pickup] 捡当前场景地面可拾取物品")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--radius", type=int, default=30, help="扫描半径（默认30）")
parser.add_argument("--max", type=int, default=30, help="一次最多捡几个（默认30）")
parser.add_argument("--dry-run", action="store_true", help="只扫不捡")
parser.add_argument("--host-port", type=int, default=None, help="host端口（默认7842，仅读状态用）")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests

NAGI = os.environ["NAGI_URL"]


def log(msg):
    try:
        print(f"[pickup] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[pickup] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


# 能踩上去但不是要捡的结构（排除）
BLACKLIST = {
    "Chest", "Big Chest", "Stone Chest",
    "Sprinkler", "Quality Sprinkler", "Iridium Sprinkler", "Pressure Nozzle",
    "Scarecrow", "Deluxe Scarecrow", "Campfire", "Torch", "Stump",
    "Sign", "Wood Sign", "Stone Sign", "Workbench", "Grave Stone",
    "Crab Pot", "Furnace", "Charcoal Kiln", "Recycling Machine",
    "Worm Bin", "Keg", "Preserves Jar", "Cheese Press", "Loom",
    "Mayonnaise Machine", "Oil Maker", "Seed Maker", "Crystalarium",
    "Tapper", "Heavy Tapper", "Bee House", "Silo", "Slime Egg",
    "Slime Incubator", "Mini-Fridge", "End Table", "TV", "Radio",
    "Picture", "Floor", "Path", "Cobblestone Path", "Wood Floor",
    "Stone Floor", "Brick Floor", "Wood Path", "Crystal Floor",
    "Rug", "Fish Pond", "Mill", "Coop", "Barn", "Shed", "Obelisk",
    "Greenhouse", "Mushroom Box", "Statue Of Endless Fortune", "Safe",
    "Skeleton", "Museum", "Horse", "Milk Pail", "Shears", "Scythe",
    "Furniture", "Armchair", "Bench", "Chair", "Table", "Couch",
    "Dresser", "Stool", "Bookshelf", "Fireplace",
}
# 工具/武器绝对不捡
BLACKLIST |= {"Pickaxe", "Axe", "Hoe", "Watering Can", "Sword", "Scythe",
              "Fishing Rod", "Copper Pickaxe", "Iron Pickaxe", "Gold Pickaxe",
              "Iridium Pickaxe", "Copper Axe", "Iron Axe", "Gold Axe", "Iridium Axe",
              "Copper Hoe", "Iron Hoe", "Gold Hoe", "Iridium Hoe",
              "Copper Watering Can", "Iron Watering Can", "Gold Watering Can",
              "Iridium Watering Can"}
# 可走但不是收集品的地面装饰/杂物（2026-08-06 实测：沙滩杂草/鸡舍家具会被误走）
# 注意：珊瑚/蛤蜊/牡蛎/海草等是【可采集物】不能黑，黑掉的是真杂物/家具。
BLACKLIST |= {"Weeds", "Stone", "Rock", "Glass Shards", "Rotten Plant",
              "Twig", "Grass Starter", "Fiber",
              # 鸡舍/畜棚/棚子里的家具（能踩但绝对不能捡）
              "Incubator", "Heater", "Feed Hopper", "Auto-Grabber", "Auto-Petter",
              "Coop", "Barn", "Shed", "Slime Hutch", "Fish Pond", "Milk Pail",
              "Shears", "Egg Basket", "Duck Egg Basket", "Mini-Fridge",
              "Ostrich Incubator", "Hay", "Automatic Feeders"}


def main():
    base = NAGI
    try:
        st = requests.get(f"{base}/status", timeout=5).json()
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)
    if not st.get("worldReady"):
        log("❌ 游戏未就绪")
        sys.exit(1)

    data = requests.get(f"{base}/surroundings", params={"radius": args.radius}, timeout=10).json()
    loc = data.get("location", "?")
    cx, cy = data.get("center", {}).get("x", 0), data.get("center", {}).get("y", 0)

    # 可拾取 = 可走 + 有 object + 不在黑名单；🌱 成熟大葱（forageCrop=1 + harvestable）也摘
    targets = []
    for t in data.get("tiles", []):
        if not t.get("passable", True):
            continue
        if t.get("forageCrop") == "1" and t.get("harvestable") is not False:
            targets.append((t["x"], t["y"], "成熟大葱"))   # interact 摘 crop（不用锄头）
            continue
        obj = t.get("object")
        if not obj:
            continue
        if any(blk in obj for blk in BLACKLIST):
            continue
        targets.append((t["x"], t["y"], obj))

    # 去重（多格可能重复报）
    seen = set()
    uniq = []
    for x, y, obj in targets:
        if (x, y) in seen:
            continue
        seen.add((x, y))
        uniq.append((x, y, obj))
    uniq.sort(key=lambda t: (abs(t[0] - cx) + abs(t[1] - cy)))

    log(f"📍 {loc} | 找到 {len(uniq)} 个可拾取物品")
    for x, y, obj in uniq[:15]:
        log(f"  · {obj} ({x},{y})")

    if args.dry_run:
        log("--dry-run：不捡")
        return

    if not uniq:
        log("🎉 场景里没有要捡的东西")
        return

    def walk_near(x, y, timeout=20):
        """走过去（物体格本身会被当障碍，自动停在旁边）并按位置等到达。
        注意：游戏窗口后台暂停时 isMoving=False 但走位排队——按位置判断。"""
        try:
            r = requests.post(f"{base}/walk_to", json={"location": loc, "x": x, "y": y}, timeout=10)
            if not r.json().get("ok"):
                return False
            deadline = time.time() + timeout
            while time.time() < deadline:
                s = requests.get(f"{base}/state", timeout=10).json()
                p = s.get("player", {})
                if abs(p.get("x", 0) - x) <= 1 and abs(p.get("y", 0) - y) <= 1:
                    time.sleep(0.3)
                    return True
                time.sleep(0.25)
            return False  # 真没走到，别假成功
        except Exception:
            return False

    def object_still_there(x, y):
        """物体还在 (x,y) 吗？（验证拾取是否成功）"""
        try:
            d = requests.get(f"{base}/surroundings", params={"radius": 5}, timeout=10).json()
            return any(t.get("x") == x and t.get("y") == y and t.get("object")
                       for t in d.get("tiles", []))
        except Exception:
            return True

    def face_and_interact(x, y):
        """面向物体格并交互（捡拾动作）。"""
        s = requests.get(f"{base}/state", timeout=10).json()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        if px < x: direction = 1   # 物体在右
        elif px > x: direction = 3 # 物体在左
        elif py < y: direction = 2 # 物体在下
        elif py > y: direction = 0 # 物体在上
        else: direction = 2
        requests.post(f"{base}/face", json={"direction": direction}, timeout=10)
        time.sleep(0.25)
        requests.post(f"{base}/interact", {}, timeout=10)
        time.sleep(0.5)

    def pick_up_object(x, y):
        """捡地上物体（蛋蛋/野菜/松露）：走过去 → 确保卡迪纳尔相邻 → 面朝 → interact。
        实测（2026-08-06）：蛋蛋/野菜不是走过去自动收，要捡拾动作；对角够不着。"""
        if not walk_near(x, y):
            return False
        s = requests.get(f"{base}/state", timeout=10).json()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        # 若对角（px!=x 且 py!=y），挪到卡迪纳尔相邻格
        if px != x and py != y:
            for nx, ny in [(x, py), (px, y), (x, y + 1), (x, y - 1), (x + 1, y), (x - 1, y)]:
                try:
                    requests.post(f"{base}/walk_to", json={"location": loc, "x": nx, "y": ny}, timeout=10)
                    time.sleep(1.0)
                except Exception:
                    pass
                s = requests.get(f"{base}/state", timeout=10).json()
                cx, cy = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
                if (cx == x and abs(cy - y) == 1) or (cy == y and abs(cx - x) == 1):
                    break  # 卡迪纳尔相邻了
        face_and_interact(x, y)
        # 验证真的捡到
        return not object_still_there(x, y)

    picked = 0
    for x, y, obj in uniq[:args.max]:
        if pick_up_object(x, y):
            picked += 1
            time.sleep(0.1)

    # 顺带清 debris（动画掉落物：镰刀作物/怪掉落，走过去才捡）
    try:
        d = requests.get(f"{base}/debris", timeout=10).json()
        for it in d.get("debris", [])[:8]:
            if picked >= args.max:
                break
            x, y = it.get("x", 0), it.get("y", 0)
            if walk_near(x, y):
                picked += 1
                time.sleep(0.1)
    except Exception:
        pass

    log(f"🎁 拾取完成：{picked} 个")


if __name__ == "__main__":
    main()
