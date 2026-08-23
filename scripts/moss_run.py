"""
🌿 moss_run.py — 绿雨搜刮苔藓（自然走路 + 按类型选工具）

恒实测（2026-08-21）：
  1. 大块(Clump:46) 用镰刀/剑挥 3 下碎；小块(GreenRainWeeds*) 按形态不同，保险挥 2 次。
  2. 镰刀/剑是范围攻击，会打到周边苔藓 → 每处理完一个目标必须重扫 /surroundings（下次 walk_to 前重新检测）。
  3. 长苔藓树(moss:True) 挥一下必掉 Moss；长苔藓的苔雨树(greenRainTree) 直接砍(斧头)。
  4. 顺序：先刮草(杂草块) 后砍树。
  5. /walk_to 自然走 + /position 精确定位兜底（防空挥空耗体力——之前砍树空挥斧头狂耗体力）。

用法:
  python moss_run.py                  # 搜刮当前地图苔藓
  python moss_run.py --dry-run        # 只报目标不收
  python moss_run.py --max 60         # 目标上限
  python moss_run.py --radius 25      # 扫描半径
  python moss_run.py --rounds 3       # 空转(无新目标)轮数上限后停
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[moss] 绿雨搜刮苔藓")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--radius", type=int, default=25, help="扫描半径（默认25，小图覆盖整图）")
parser.add_argument("--max", type=int, default=80, help="目标上限（默认80）")
parser.add_argument("--rounds", type=int, default=5, help="连续无新目标即停的轮数上限（默认5）")
parser.add_argument("--grass-dense", type=int, default=4, help="局部草密度判定阈值：AI周边--grass-r格内草数≥此值专注清草，低于则草/树混选（越小越早碰树，2026-08-21 恒）")
parser.add_argument("--grass-r", type=int, default=5, help="草密度统计半径（格，默认5）——局部密度而非全图全数")
parser.add_argument("--pickup", action="store_true", help="每目标后走过去捡掉落(默认不捡——走动磁吸拾取路过的就行,2026-08-21 恒)")
parser.add_argument("--dry-run", action="store_true", help="只报目标不收")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests

NAGI = os.environ["NAGI_URL"]
HIT_DELAY = 0.55   # 挥工具间隔（秒）
WALK_TIMEOUT = 12  # 单次 walk_to 等到达超时（秒）


def log(msg):
    try:
        print(f"[moss] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[moss] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def get(ep, params=None):
    return requests.get(f"{NAGI}{ep}", params=params, timeout=12).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=12).json()


def player_pos():
    s = get("/state")
    p = s.get("player", {})
    return p.get("x"), p.get("y"), s.get("location", {}).get("name")


def find_tool(kind):
    """按类型找背包里最好的一把工具名。kind: scythe / axe"""
    inv = get("/state").get("inventory", [])
    names = [i.get("name", "") for i in inv]
    if kind == "scythe":
        sc = [n for n in names if "Scythe" in n and "Sword" not in n]
        for pref in ["Iridium Scythe", "Gold Scythe", "Copper Scythe", "Scythe"]:
            if pref in sc:
                return pref
        # 没镰刀→用剑（剑也是范围攻击，能打苔藓/草）
        sw = [n for n in names if "Sword" in n]
        return sw[0] if sw else (sc[0] if sc else None)
    if kind == "axe":
        ax = [n for n in names if "Axe" in n]
        return ax[0] if ax else None
    return None


def axe_fell_hits(axe_name):
    """按斧头等级给"砍倒整棵(树+树桩)"所需击数——恒给的维基砍树表，整棵=前数(树)+后数(树桩)相加：
    铱 2+1=3 / 金 4+2=6 / 钢 6+3=9 / 铜 8+4=12 / 基础 10+5=15。用它当上限，不靠 gone_tree 死等倒树动画(多砍2下空挥)。"""
    n = (axe_name or "").lower()
    if "iridium" in n or "铱" in n:
        return 3
    if "gold" in n or "金" in n:
        return 6
    if "steel" in n or "钢" in n:
        return 9
    if "copper" in n or "铜" in n:
        return 12
    return 15


def _flood_blocks(points):
    """把 Clump:46 的多个瓦片按 4-邻域归并成块；每块返回一个代表瓦片（离玩家近的）和所有块瓦片集。
    points: {(x,y)}。返回 [ {'rep':(x,y), 'tiles':{(x,y)...}} ]"""
    if not points:
        return []
    pts = set(points)
    blocks = []
    while pts:
        seed = pts.pop()
        stack = [seed]
        tiles = {seed}
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in pts:
                    pts.remove((nx, ny))
                    tiles.add((nx, ny))
                    stack.append((nx, ny))
        blocks.append(tiles)
    # 选每块离玩家最近的瓦片当 rep（走过去近）
    px, py, _ = player_pos()
    out = []
    for tiles in blocks:
        rep = min(tiles, key=lambda t: abs(t[0] - px) + abs(t[1] - py))
        out.append({'rep': rep, 'tiles': tiles})
    return out


def scan_targets():
    """扫当前地图，返回草类(小块/大块)、树类(砍/刮)目标。重扫=每目标前调用。
    返回: (loc, small_list, big_blocks, tree_list, passable_set)"""
    data = get("/surroundings", {"radius": args.radius})
    loc = data.get("location", "")
    tiles = data.get("tiles", [])
    small = []       # (x,y) 小块 GreenRainWeeds*
    clump_pts = set()
    trees = []       # (kind, x, y) kind='axe'|'scrape'
    passable = set()
    for t in tiles:
        if t.get("passable", True):
            passable.add((t.get("x"), t.get("y")))
        terr = t.get("terrain") or ""
        if terr.startswith("Tree:"):
            if t.get("greenRainTree"):
                trees.append(("axe", t.get("x"), t.get("y")))
            elif t.get("moss"):
                trees.append(("scrape", t.get("x"), t.get("y")))
            continue
        if t.get("resource") == "Clump:46":
            clump_pts.add((t.get("x"), t.get("y")))
            continue
        if (t.get("object") or "").startswith("GreenRainWeeds"):
            small.append((t.get("x"), t.get("y")))
    big_blocks = _flood_blocks(clump_pts)
    return loc, small, big_blocks, trees, passable


def find_stand(tx, ty, exclude, passable):
    """找目标外围一格可站的瓦片（N/E/S/W）。exclude=目标占据瓦片集（如整块clump）。"""
    for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        n = (tx + dx, ty + dy)
        if n not in exclude and n in passable:
            return n
    return (tx, ty - 1)   # 兜底：站目标正上方


def _walk_to_stand(stand, target, loc_name):
    """/walk_to 自然走到目标旁；**到达判定用对目标的曼哈顿**（walk_to 常差一格：停到目标旁格再往前，对站格-1但对目标2格）——
    若没贴到目标旁(≤1)就 /position 精确定位到站格，真正做到"贴到目标旁格"再挥（防空挥，2026-08-21 恒）。"""
    sx, sy = stand
    tx, ty = target
    try:
        r = post("/walk_to", {"location": loc_name, "x": sx, "y": sy})
        if r.get("ok"):
            deadline = time.time() + WALK_TIMEOUT
            while time.time() < deadline:
                p = get("/state").get("player", {})
                if not p.get("isMoving", False):
                    break
                time.sleep(0.2)
    except Exception:
        pass
    # 精确定位兜底：没贴到目标旁 → /position 到站格（站格=目标旁格，落位即贴到目标）
    p = get("/state").get("player", {})
    if abs(p.get("x", -99) - tx) + abs(p.get("y", -99) - ty) > 1:
        try:
            post("/position", {"x": sx, "y": sy})
            time.sleep(0.4)
        except Exception:
            pass


def _face(target_tiles):
    """按 AI **实际位置**朝向最近的目标准格（防空挥：别用预期站位推算步，/walk_to 没走准时脸会偏）。
    target_tiles: 目标占格集合（单格={(tx,ty)}；大块=2×2格子集），取离 AI 最近的一格朝它。0上1右2下3左"""
    px, py, _ = player_pos()
    tx, ty = min(target_tiles, key=lambda t: abs(t[0] - px) + abs(t[1] - py))
    dx, dy = tx - px, ty - py
    if dy < 0:
        d = 0
    elif dy > 0:
        d = 2
    elif dx < 0:
        d = 3
    else:
        d = 1
    post("/face", {"direction": d})
    time.sleep(0.1)


def gone_weed(x, y):
    """该格是否已经不再是苔藓杂草（Clump/GreenRainWeeds）"""
    data = get("/surroundings", {"radius": 2})
    for t in data.get("tiles", []):
        if t.get("x") == x and t.get("y") == y:
            if t.get("resource") == "Clump:46":
                return False
            if (t.get("object") or "").startswith("GreenRainWeeds"):
                return False
            return True
    return True   # 该格不见了=已清


def gone_tree(x, y):
    """该格是否已清树：terrain 不再以 Tree: 开头。⚠️ 站立树 passable=True（isTilePassable 不看树障碍），
    所以**不能用 passable 判定"树倒"**——那会误伤站立树。树砍倒后留的 Tree:* 幽灵，靠 done_trees 标记不再重砍(2026-08-21 恒)。"""
    data = get("/surroundings", {"radius": 2})
    for t in data.get("tiles", []):
        if t.get("x") == x and t.get("y") == y:
            return not (t.get("terrain") or "").startswith("Tree:")
    return True


def swing(target_kind, tx, ty, block_tiles, passable, loc_name, tool, max_hits, gone_fn):
    """站到目标外围 → 面朝 → select → use_tool 循环直到目标消失（面前格没了为止）。
    block_tiles: 目标占用的瓦片集（clump 的 4 格；单个目标={目标格}）。"""
    exclude = block_tiles if block_tiles else {(tx, ty)}
    stand = find_stand(tx, ty, exclude, passable)
    _walk_to_stand(stand, (tx, ty), loc_name)
    # 🔍 防空挥（2026-08-21 恒"挥空"）：确认真的贴到目标旁格（到目标任意格曼哈顿≤1）才挥；没到位跳过不挥（下一轮重扫再挑）。
    px, py, _ = player_pos()
    if min(abs(px - ex) + abs(py - ey) for ex, ey in exclude) > 1:
        log(f"  站位失败：离目标({tx},{ty})太远（应贴到旁格≤1），跳过不挥")
        return 0
    _face(exclude)
    if tool:
        post("/select", {"name": tool})
        time.sleep(0.15)
    if not tool:
        return 0
    swings = 0
    for _ in range(max_hits):
        try:
            if gone_fn(tx, ty):
                break
            post("/tool", {"name": tool, "force": True})
            time.sleep(HIT_DELAY)
            swings += 1
            if gone_fn(tx, ty):
                break
        except Exception:
            break
    return swings


def collect_debris(limit=12):
    """走过去捡地上掉落（Moss 等 debris）。返回捡了几个。"""
    try:
        d = get("/debris")
        items = d.get("debris", [])
    except Exception:
        return 0
    picked = 0
    for it in items[:limit]:
        x, y = it.get("x", 0), it.get("y", 0)
        try:
            _, _, loc_name = player_pos()
            r = post("/walk_to", {"location": loc_name, "x": x, "y": y})
            if r.get("ok"):
                deadline = time.time() + 15
                while time.time() < deadline:
                    p = get("/state").get("player", {})
                    if not p.get("isMoving", False):
                        break
                    time.sleep(0.25)
                picked += 1
                time.sleep(0.3)
        except Exception:
            pass
    return picked


def main():
    try:
        st = get("/status")
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)
    if not st.get("worldReady"):
        log("❌ 游戏未就绪")
        sys.exit(1)

    grass_tool = find_tool("scythe")
    axe_tool = find_tool("axe")
    if not (grass_tool or axe_tool):
        log("❌ 背包里没有镰刀/剑/斧头")
        sys.exit(1)
    log(f"⚒️ 草用 {grass_tool or '无'} | 树用 {axe_tool or '无'}")

    if args.dry_run:
        loc, small, big_blocks, trees, _ = scan_targets()
        log(f"📍 {loc} | 小块 {len(small)} | 大块 {len(big_blocks)} | 树 砍{sum(1 for k,_,_ in trees if k=='axe')}/刮{sum(1 for k,_,_ in trees if k=='scrape')}")
        return

    processed = 0
    collected = 0
    empty_rounds = 0
    tree_started = False   # 砍树阶段标志：草优先(密度判定)，一旦开始砍树→砍完树就结束,不回头清草(2026-08-21 恒)
    done_trees = set()     # 已砍倒/已放弃的树，不再re-target（树桩/幽灵/放弃的都停下）
    tree_tries = {}        # (x,y)->已尝试砍的次数；砍不倒的回头重新定位再砍一次，最多一次，再砍不倒才算(2026-08-21 恒)
    while empty_rounds < args.rounds and processed < args.max:
        loc, small, big_blocks, trees, passable = scan_targets()   # 每目标前重扫（范围攻击会清周边）
        grass_cand = [("small", x, y) for (x, y) in small] + \
                     [("big", b['rep'][0], b['rep'][1]) for b in big_blocks]
        # 树分两类：没试过的 fresh / 试过一次没倒待重试的 retry（已 done 的排除）
        tpk = [t for t in trees if (t[1], t[2]) not in done_trees]
        fresh_trees = [("tree_" + k, x, y) for (k, x, y) in tpk if tree_tries.get((x, y), 0) == 0]
        retry_trees = [("tree_" + k, x, y) for (k, x, y) in tpk if 0 < tree_tries.get((x, y), 0) < 2]
        alive_trees = fresh_trees + retry_trees
        # 草密度判定（2026-08-21 恒）：局部草密(周边--grass-r内草数>=--grass-dense)专注清草；草稀+有树→砍树。
        # 已开始砍树 → 只砍树，先砍 fresh，全试过才回头重试砍不倒的(最多一次)。
        px, py, _ = player_pos()
        local_grass = sum(1 for c in grass_cand if abs(c[1] - px) + abs(c[2] - py) <= args.grass_r)
        if tree_started:
            cand = fresh_trees if fresh_trees else retry_trees
            if not cand:
                if alive_trees:                   # 还有没倒的树但已重试≥2次 → 放弃不砍
                    for (_k, _x, _y) in alive_trees:
                        done_trees.add((_x, _y))
                    log("⚠️ 重试仍砍不倒的树，放弃（树桩不用记）")
                else:
                    log("✅ 树都砍完了，结束（不回头清草）")
                break
        elif grass_cand and local_grass >= args.grass_dense:
            cand = grass_cand                    # 局部草密 → 清草
        elif grass_cand and alive_trees:
            cand = grass_cand + (fresh_trees or retry_trees)   # 草稀+有树 → 草/树混选(选到树→进砍树阶段)
        elif grass_cand:
            cand = grass_cand
        else:
            cand = fresh_trees or retry_trees
        if not cand:
            empty_rounds += 1
            continue
        # 取离玩家最近的一个（px,py 已取）
        target = min(cand, key=lambda c: abs(c[1] - px) + abs(c[2] - py))
        kind, tx, ty = target
        if kind.startswith("tree_"):
            tree_started = True                  # 开始砍树 → 之后只砍树,砍完结束
        # 处理
        if kind == "small":
            exclude = {(tx, ty)}
            swings = swing("weed", tx, ty, exclude, passable, loc, grass_tool, 2, gone_weed)
            log(f"🌿 小块 ({tx},{ty}) 挥{swings}下")
        elif kind == "big":
            exclude = next((b['tiles'] for b in big_blocks if b['rep'] == (tx, ty)), {(tx, ty)})
            swings = swing("big", tx, ty, exclude, passable, loc, grass_tool, 3, gone_weed)
            log(f"🌿 大块 ({tx},{ty}) 挥{swings}下")
        elif kind.startswith("tree_axe"):
            exclude = {(tx, ty)}
            # 上限=整棵(树+树桩)击数：铱3/金6/钢9/铜12/基础15（恒给维基表前后相加）；gone_tree 做提前倒兜底
            _th = axe_fell_hits(axe_tool)
            swings = swing("tree", tx, ty, exclude, passable, loc, axe_tool, _th, gone_tree)
            log(f"🪓 苔雨树 ({tx},{ty}) 砍{swings}下(上限{_th})")
            # 树倒动画掉木头：砍倒后原地等1s把木头吸进包再走(2026-08-21 恒)；没倒→记一次，回头重新定位再砍
            if gone_tree(tx, ty):
                time.sleep(1.0)
                done_trees.add((tx, ty))
            else:
                tree_tries[(tx, ty)] = tree_tries.get((tx, ty), 0) + 1
                if tree_tries[(tx, ty)] >= 2:
                    done_trees.add((tx, ty))
                    log(f"  ⚠️ ({tx},{ty}) 重试一次仍没砍倒，放弃")
        else:  # tree_scrape —— 长苔藓树挥一下必掉 Moss；刮完即收，不再重复刮
            exclude = {(tx, ty)}
            swings = swing("scrape", tx, ty, exclude, passable, loc, grass_tool, 1, lambda a, b: False)
            log(f"🌿 长苔藓树 ({tx},{ty}) 刮{swings}下")
            done_trees.add((tx, ty))
        processed += 1
        empty_rounds = 0
        # 捡掉落（默认不捡——砍完后走动时磁吸拾取路过的小部分就行；--pickup 才转身追远处掉落）
        if args.pickup:
            n = collect_debris()
            collected += n

    _pk = f"，捡起 Moss {collected} 个" if args.pickup else ""  # 默认不追掉落，落地的 Moss 走位时磁吸拾取
    log(f"✅ moss_run 完成：处理 {processed} 个目标{_pk}（empty_rounds={empty_rounds}）")


if __name__ == "__main__":
    main()
