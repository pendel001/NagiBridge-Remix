"""
🐾 pet_walk.py — 拟人化摸动物（走过去摸，不是作弊）

和捡蛋蛋同一套操作：走到动物旁 → 面朝它 → interact（摸）。
动物会动，所以走到的位置可能过期：先重新查位置，动物动过就 /position 精确定位到它旁边。

实测（2026-08-06）：interact 面朝动物确实能摸（还误摸过恐龙🐉）。

用法:
  python pet_walk.py --port 7842        # 摸当前场景所有没摸的动物
  python pet_walk.py --dry-run          # 只报有哪些没摸
  python pet_walk.py --include-petted   # 连已摸的也重新摸一遍

⚠️ 跑之前游戏窗口保持前台（后台会暂停，走位不动）。
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[pet] 拟人化摸动物")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--dry-run", action="store_true", help="只报不摸")
parser.add_argument("--include-petted", action="store_true", help="连已摸的也摸")
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
        print(f"[pet] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[pet] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def get(ep, params=None):
    return requests.get(f"{NAGI}{ep}", params=params, timeout=10).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=10).json()


def current_location():
    s = get("/state")
    return s.get("location", {}).get("name", "")


def unpetted_animals(loc):
    """当前场景所有动物 (name, type, x, y, wasPetToday)"""
    fr = get("/farm_report")
    out = []
    for a in fr.get("animals", {}).get("animals", []):
        if a.get("building") != loc:
            continue
        if not args.include_petted and a.get("wasPetToday"):
            continue
        out.append((a.get("name"), a.get("type"), a.get("x"), a.get("y")))
    return out


def animal_pos(name):
    """查指定动物当前坐标，没找到返回 None"""
    fr = get("/farm_report")
    for a in fr.get("animals", {}).get("animals", []):
        if a.get("name") == name:
            return a.get("x"), a.get("y")
    return None


def animal_petted(name):
    fr = get("/farm_report")
    for a in fr.get("animals", {}).get("animals", []):
        if a.get("name") == name:
            return a.get("wasPetToday")
    return None


def walk_near(x, y, timeout=20):
    """走过去（动物格可能被当障碍，停在旁边），按位置等到达。"""
    try:
        loc = current_location()
        r = post("/walk_to", {"location": loc, "x": x, "y": y})
        if not r.get("ok"):
            return False
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = get("/state")
            p = s.get("player", {})
            if abs(p.get("x", 0) - x) <= 1 and abs(p.get("y", 0) - y) <= 1:
                return True
            time.sleep(0.25)
        return False
    except Exception:
        return False


def pos_to_adjacent(ax, ay):
    """position 到动物 (ax,ay) 的卡迪纳尔相邻格（动物动过就精确定位）"""
    s = get("/state")
    px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
    # 已经卡迪纳尔相邻就直接返回
    if (px == ax and abs(py - ay) == 1) or (py == ay and abs(px - ax) == 1):
        return True
    # 找个可站的相邻格
    for nx, ny in [(ax + 1, ay), (ax - 1, ay), (ax, ay + 1), (ax, ay - 1)]:
        post("/position", {"x": nx, "y": ny})
        time.sleep(0.3)
        s = get("/state")
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        if (px == ax and abs(py - ay) == 1) or (py == ay and abs(px - ax) == 1):
            return True
    return False


def pet_one(name):
    """摸一只动物，成功返回 True（最多重试 2 次，动物会动）。"""
    for attempt in range(2):
        if attempt:
            time.sleep(0.5)
        pos = animal_pos(name)
        if not pos:
            return False
        x, y = pos
        # 1) 走过去
        if not walk_near(x, y):
            log(f"  ⚠️ {name} 走不到 ({x},{y})")
            continue
        # 2) 动物会动——重新查位置，动过就 position 精确定位
        pos2 = animal_pos(name)
        if pos2:
            ax, ay = pos2
            s = get("/state")
            px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
            # 距离超过 1 格 → 动物跑了，position 精确定位到它旁边
            if abs(px - ax) + abs(py - ay) > 1:
                if not pos_to_adjacent(ax, ay):
                    log(f"  ⚠️ {name} 跑到 ({ax},{ay}) 附近没地方站")
                    continue
            # 3) 面朝动物 + interact
            s = get("/state")
            px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
            if px < ax: d = 1
            elif px > ax: d = 3
            elif py < ay: d = 2
            else: d = 0
            post("/face", {"direction": d})
            time.sleep(0.25)
            post("/interact", {})
            time.sleep(0.5)
        # 4) 验证摸到了
        if animal_petted(name) is True:
            return True
    return False


def main():
    try:
        st = get("/status")
        if not st.get("worldReady"):
            log("❌ 游戏未就绪")
            sys.exit(1)
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)

    # 动物作息：一早醒来能摸，傍晚 6 点后开始睡、摸不了
    tod = get("/state").get("time", {}).get("timeOfDay", 600)
    if tod >= 1800:
        log(f"⚠️ 现在 {tod//100}:{tod%100:02d}，动物傍晚 6 点后就睡觉摸不了了！建议早上 6~17 点摸（最好一早就摸）")
    loc = current_location()
    animals = unpetted_animals(loc)
    log(f"📍 {loc} | 没摸的动物 {len(animals)} 只")
    for name, typ, x, y in animals[:12]:
        log(f"  · {name} [{typ}] ({x},{y})")

    if args.dry_run:
        return
    if not animals:
        log("🐾 都摸过了！")
        return

    petted = 0
    failed = []
    for name, typ, x, y in animals:
        if pet_one(name):
            petted += 1
            log(f"  🐾 摸了 {name}")
        else:
            failed.append(name)
            log(f"  ❌ {name} 没摸到")
        time.sleep(0.2)

    log(f"\n✅ 摸完：{petted}/{len(animals)} 只")
    if failed:
        log(f"  没摸到: {', '.join(failed)}")


if __name__ == "__main__":
    main()
