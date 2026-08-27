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
    """当前场景所有动物 (name, type, x, y, wasPetToday)。
    ⚠️ 2026-08-24 恒：改读 /animals（物理当前场景）而非 /farm_report（按归属建筑过滤）——
    放牧动物 home 写原建筑、人在 Farm 上会被 farm_report 滤掉，导致自然界摸不到跑出去的；
    /animals 站哪读哪（站 Farm=放牧动物、站在鸡舍=室内动物）。"""
    d = get("/animals")
    out = []
    for a in d.get("animals", []):
        if args.include_petted or not a.get("wasPetToday"):
            out.append((a.get("name"), a.get("type"), a.get("x"), a.get("y")))
    return out


def animal_pos(name):
    """查指定动物当前坐标（物理位置），没找到返回 None"""
    d = get("/animals")
    for a in d.get("animals", []):
        if a.get("name") == name:
            return a.get("x"), a.get("y")
    return None


def animal_petted(name):
    d = get("/animals")
    for a in d.get("animals", []):
        if a.get("name") == name:
            return a.get("wasPetToday")
    return None


def player_pos():
    s = get("/state")
    p = s.get("player", {})
    return p.get("x", 0), p.get("y", 0)


def is_cardinal(px, py, ax, ay):
    """玩家在动物的正上下左右（interact 能摸到的唯一站位关系）"""
    return (px == ax and abs(py - ay) == 1) or (py == ay and abs(px - ax) == 1)


def tile_passable(x, y):
    """这格能不能站——问 /passable（寻路同款判定，含家具/物体/牲畜）。
    ⚠️ 2026-08-26 恒：别用 /surroundings 或 /dump_tile 的 passable 去判断站位，
    那两个历史上是裸 isTilePassable（虽已同步修好，但 /passable 才是权威）。"""
    try:
        return bool(post("/passable", {"x": x, "y": y}).get("passable"))
    except Exception:
        return False


def walk_near(x, y, timeout=12):
    """走过去（动物格是障碍，会停在旁边），按位置等到达。
    ⚠️ 2026-08-26 恒：以前 timeout=20 且只会干等——路被别的动物堵死时
    每只都要白白耗满 20 秒，这就是"动作延迟明显很长"的来源。
    现在加卡住快判：位置连续 ~1.5 秒没变就认输走兜底。"""
    try:
        loc = current_location()
        r = post("/walk_to", {"location": loc, "x": x, "y": y})
        if not r.get("ok"):
            return False
        deadline = time.time() + timeout
        last, still = None, 0
        while time.time() < deadline:
            px, py = player_pos()
            if abs(px - x) <= 1 and abs(py - y) <= 1:
                return True
            if (px, py) == last:
                still += 1
                if still >= 6:      # 6 × 0.25s ≈ 1.5s 纹丝不动 = 顶住了
                    return False
            else:
                last, still = (px, py), 0
            time.sleep(0.25)
        return False
    except Exception:
        return False


def pos_to_adjacent(ax, ay):
    """position 到动物 (ax,ay) 的卡迪纳尔相邻格。
    ⚠️ 2026-08-26 恒：以前按固定顺序 右→左→下→上 硬传，且**不问那格能不能站**——
    畜棚四邻全是小桶时，它会一格一格传到桶上、再传回来，肉眼看到的就是
    "反复落到同一个位置 + 不停 warp + 摸不到"。/position 是直接改坐标不做任何校验的。
    现在：先用 /passable 过滤掉站不了的，再按离当前位置的距离排序就近传。"""
    px, py = player_pos()
    if is_cardinal(px, py, ax, ay):
        return True

    cands = [(ax + 1, ay), (ax - 1, ay), (ax, ay + 1), (ax, ay - 1)]
    cands = [c for c in cands if tile_passable(*c)]
    cands.sort(key=lambda c: abs(c[0] - px) + abs(c[1] - py))
    if not cands:
        return False

    for nx, ny in cands:
        post("/position", {"x": nx, "y": ny})
        time.sleep(0.3)
        px, py = player_pos()
        if is_cardinal(px, py, ax, ay):
            return True
    return False


def pet_one(name):
    """摸一只动物，成功返回 True（自然走路 + 走不到/动物跑了就 position 兜底到相邻格，最多重试 2 次）。
    ⚠️ 2026-08-24 恒：之前"走不到"就直接跳过（continue），没做 position 兜底 → 鸡舍杂物挡路的鸡摸不到（实测 2/4）。
    现在走不到/动物动过 → pos_to_adjacent 瞬移到动物旁格再面朝+interact。"""
    for attempt in range(2):
        if attempt:
            time.sleep(0.5)
        pos = animal_pos(name)
        if not pos:
            return False
        x, y = pos

        # 1) 自然走路过去（位置可能过期）。DLL 已把牲畜算成障碍，
        #    所以 walk_to 打动物格会自动落到它的相邻格，不再顶在动物身上。
        if not walk_near(x, y):
            log(f"  ⚠️ {name} 走不到 ({x},{y})，position 兜底")

        # 2) 动物会动，重查最新位置，然后**务必**站到卡迪纳尔相邻格
        pos2 = animal_pos(name)
        if not pos2:
            continue
        ax, ay = pos2
        px, py = player_pos()
        # ⚠️ 2026-08-26 恒：以前判定是 abs(px-ax)+abs(py-ay) > 1，
        #    同格(距离0)和斜角(距离2)两种都漏——同格时下面朝向会算出 d=0 朝上打空，
        #    interact 摸的是空地。实测同格是常态（24 只里两对）。
        #    改成"不是正相邻就重定位"，同格也会被挪开。
        if not is_cardinal(px, py, ax, ay):
            if not pos_to_adjacent(ax, ay):
                log(f"  ⚠️ {name} ({ax},{ay}) 附近没地方站")
                continue

        # 3) 面朝动物（再重查一次，防它又动）+ interact
        pos3 = animal_pos(name)
        if pos3:
            ax, ay = pos3
        px, py = player_pos()
        if not is_cardinal(px, py, ax, ay):
            continue    # 它又跑了，下一轮重来（别对着空地瞎摸）
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

    # 🚶 2026-08-26 恒：以前按 /animals 的原始顺序（≈插入序）一只只摸，
    #    人在棚里来回横穿，看着很傻。改成贪心最近邻：每摸完一只重读全部位置
    #    （动物一直在动，一次性排好的顺序会立刻过期），再挑离自己最近的那只。
    petted = 0
    failed = []
    total = len(animals)
    pending = [name for name, typ, x, y in animals]

    while pending:
        px, py = player_pos()
        live = {}
        try:
            for a in get("/animals").get("animals", []):
                live[a.get("name")] = (a.get("x"), a.get("y"))
        except Exception:
            pass

        # 离自己最近的优先；查不到位置的排最后（可能跑去别的场景了）
        def far(nm):
            p = live.get(nm)
            return 10**6 if not p else abs(p[0] - px) + abs(p[1] - py)
        pending.sort(key=far)

        name = pending.pop(0)
        if pet_one(name):
            petted += 1
            log(f"  🐾 摸了 {name}")
        else:
            failed.append(name)
            log(f"  ❌ {name} 没摸到")
        time.sleep(0.2)

    # 🔁 2026-08-26 恒：补漏轮。动物（尤其室外的牛）一直在走，主循环常撞上
    #    "算好朝向 → 它挪了一格 → interact 打空" 的竞态；pet_one 只重试 2 次，
    #    赶上连续移动就废了。实测失败的牛单独重试一次就摸到（wasPetToday 立刻 True）。
    #    所以对没摸到的再跑最多 2 轮，每轮重读位置——只针对失败项，很便宜。
    for rnd in range(1, 3):
        if not failed:
            break
        retry, failed = failed, []
        log(f"🔁 补漏第{rnd}轮：{len(retry)} 只")
        for name in retry:
            if animal_petted(name) is True:      # 期间被自动摸摸机/房主摸了
                petted += 1
                log(f"  ✅ {name} 已被摸过")
                continue
            if pet_one(name):
                petted += 1
                log(f"  🐾 补摸了 {name}")
            else:
                failed.append(name)
            time.sleep(0.2)

    log(f"\n✅ 摸完：{petted}/{total} 只")
    if failed:
        log(f"  没摸到: {', '.join(failed)}")


if __name__ == "__main__":
    main()
