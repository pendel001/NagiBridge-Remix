"""
⛏️ rock_run.py — 室外镐击(采石场/姜岛挖掘场/南滩蚌矿),默认只扫不敲

规则(2026-08-29 恒拍板):
  蚌矿(IslandWest南滩)→全敲得蚌;采石场→全敲只**跳过普通 Stone**;
  挖掘场→黏土/骨头都敲。一句话:**敲一切可破物,唯独跳过普通石头**。

复用 mine_run.MineBot 的走位/面向/挥镐/surroundings 真循环(战过矿洞),
但对室外补两点:
  1. 目标判定不硬编码——扫 /surroundings 按名字+objId 分类,未知名字**上报**给咱(先扫一遍再定)。
  2. 1.6 矿节点 Name 报 'Stone' 靠 objId 区分(bonener/clay/clam 这类可能也是),SDV 1.6 里
     纯石头 vs 节点只看 objId,不只看名字。
  ⚠️ 默认 **--dry-run 只扫不敲**;加 --dig 才真动手。

用法:
  python rock_run.py                      # 扫当前地图,报候选 + 未知名字(不敲)
  python rock_run.py --dig                # 真敲(跳过普通 Stone)
  python rock_run.py --dig --break-stone  # 连普通 Stone 也敲
  python rock_run.py --radius 30 --max 40 # 扫更远/最多敲40个
  python rock_run.py --port 7843          # 指定端口(默认7843=恒)
"""

import os
import sys
import time
import argparse
from collections import defaultdict

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 先设 NAGI_URL(mine_run 在 import 时读它),默认 7843=恒
_DEFAULT_PORT = 7843
os.environ.setdefault("NAGI_URL", f"http://localhost:{_DEFAULT_PORT}")

from mine_run import MineBot, is_mine_location, _get  # noqa: E402

# ── 目标分类(名字命中这些子串 → 疑似可破) ──
BREAK_PREFIXES = (
    "Node", "Stone", "Geode", "Boulder", "Rock", "Bone", "Crystal",
    "Clay", "Ore", "Shell", "Clam", "Mussel", "Oyster", "Meteor",
    "Bamboo", "Chunk", "Hollow", "Calico", "Moss",
)
# 明确非镐破/不敲的(杂草/树/装饰/采集),扫到不进目标也不算 unknown
EXCLUDE = {
    "Weeds", "Grass", "Twig", "Wild Grass", "Flower", "Bush",
    "Torch", "Campfire",
}
# (O)xxx → 名字: 1.6 里 Name 报 'Stone' 的节点靠 objId 认(纯石头 vs 节点只看 objId)。
# 已确认(2026-08-29 wiki+真机):
#   挖掘场 816=Fossil Stone(骨/化石) 817=Bone Node 818=Clay Node(黏土)
#   蚌矿场(IslandWest 南滩) 25=Mussel Stone(蚌)
#   采石场/矿井:煤矿节点 BasicCoalNode0/1(恒拍板也算);矿节点 751/290/764/765/767 兜底(报真名时走前缀,抱 Stone 时靠这里)
# 其余(如 32/38/40/42/668/670 等)默认当普通石跳过;dry-run 会把 Stone 按 objId 列出来,确认后再补。
BREAK_IDS = {
    "(O)816": "Fossil Stone",
    "(O)817": "Bone Node",
    "(O)818": "Clay Node",
    "(O)25": "Mussel Stone",
    "(O)BasicCoalNode0": "Coal Node",
    "(O)BasicCoalNode1": "Coal Node",
    "(O)751": "Copper Node",
    "(O)290": "Iron Node",
    "(O)764": "Gold Node",
    "(O)765": "Iridium Node",
    "(O)767": "Mystic Stone",
}


def _name_of(oid):
    """objId → 该节点真名;普通石/未知返回 None(当作跳过)。BasicCoal 前缀兜底。"""
    if not oid:
        return None
    if oid in BREAK_IDS:
        return BREAK_IDS[oid]
    if str(oid).startswith("BasicCoal"):
        return "Coal Node"
    return None


def log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(msg.encode(enc, errors='replace').decode(enc, errors='replace'), flush=True)
        except Exception:
            pass


_REALNAME = {}   # objId → dump_tile 真名(只解析一次)


def _real_name(bot, x, y, oid):
    """石头格真名(调 /dump_tile),按 objId 缓存。返回真名(如 'Jade Stone'/'石头')或 oid。"""
    if oid in _REALNAME:
        return _REALNAME[oid]
    nm = oid
    try:
        r = bot._get("/dump_tile", {"x": x, "y": y})
        o = ((r or {}).get("tile") or {}).get("object") or {}
        nm = o.get("name") or oid
    except Exception:
        pass
    _REALNAME[oid] = nm
    return nm


def is_node_name(nm):
    """上报名/真名是否像矿节点: 含 'Node' 或 以 'Stone' 结尾。
    普通石(中文'石头'/'Stone')不含这些 → False;'X Node'/'X Stone' 节点 → True。"""
    s = str(nm or "")
    return "Node" in s or s.endswith("Stone")


def classify(t, bot, skip_stone=True):
    """判定一个 tile 是否为目标。返回 dict 或 None(target)。

    dict: {key, val, name, breakable, unknown, plain_stone, stone_skip}
    """
    obj = t.get("object")
    oid = t.get("objId")
    resource = t.get("resource")
    name = obj or resource or (oid if oid else None)
    if not name:
        return None

    if obj and obj in EXCLUDE:
        return None
    terr = t.get("terrain") or ""
    if terr.startswith("Tree:"):
        return None
    if t.get("crop") or t.get("forageCrop"):
        return None

    # 已知 objId(coal/ore/island) → 直接目标(快,不用 dump_tile)
    known = _name_of(oid)
    if known:
        return {"key": "objId", "val": oid, "name": known, "objId": oid,
                "breakable": True, "unknown": False, "plain_stone": False, "stone_skip": False}

    # 石头类(obj=='Stone' 或仅有 objId): dump_tile 真名区分 普通石(跳) vs 矿节点(敲)
    # —— 宝石家族(Jade/Ruby/Aquamarine/Mystic/Emerald/Diamond/Topaz/Amethyst)全走这,不用挨个实测
    if obj in ("Stone", None):
        if not oid:
            return None
        real = _real_name(bot, t["x"], t["y"], oid)
        is_plain = (real in ("石头",)) or (real and str(real).lower() == "stone")
        if is_plain and skip_stone:
            return {"key": "objId", "val": oid, "name": real or name, "objId": oid,
                    "breakable": False, "unknown": False, "plain_stone": True, "stone_skip": True}
        # 非普通石(矿节点),或 --break-stone 连普通石也敲
        return {"key": "objId", "val": oid, "name": real or name, "objId": oid,
                "breakable": True, "unknown": False, "plain_stone": is_plain, "stone_skip": False}

    # 直报名字的节点(Copper Node / Gem Node / ...)
    if is_node_name(name):
        return {"key": "objId" if oid else "object", "val": oid or name, "name": name, "objId": oid,
                "breakable": True, "unknown": False, "plain_stone": False, "stone_skip": False}
    return {"key": "object", "val": name, "name": name, "objId": oid,
            "breakable": False, "unknown": True, "plain_stone": False, "stone_skip": False}


def scan(data, bot, skip_stone=True):
    """扫 tiles → targets(目标) + unknowns(不认识的上报) + stones(普通石,按objId,dig跳过)。"""
    targets, unknown, stones = [], [], []
    for t in data.get("tiles", []):
        c = classify(t, bot, skip_stone)
        if not c:
            continue
        c["x"], c["y"] = t["x"], t["y"]
        if c["unknown"]:
            unknown.append(c)
        elif c.get("stone_skip"):
            stones.append(c)
        elif c["breakable"]:
            targets.append(c)
    return targets, unknown, stones


def still_there(t, tgt):
    """校验 tile 是否还是目标(按稳定键匹配;objId 最可靠)。"""
    key, val = tgt["key"], tgt["val"]
    if key == "objId":
        return t.get("objId") == val
    if key == "resource":
        return t.get("resource") == val
    return t.get("object") == val


def face_to(bot, dx, dy, x, y):
    if dx == 1: bot.face(3)
    elif dx == -1: bot.face(1)
    elif dy == 1: bot.face(0)
    elif dy == -1: bot.face(2)
    else: bot.face_toward(x, y)


MAX_BLOWS = 30           # 硬骨节/粘土的敲击上限(矿井软石15够,室外硬节点放宽)

def break_rock(bot, tgt, location):
    """走→面对→敲→校验碎没碎→碎了走过去捡。复用 MineBot 走位/挥镐。
    返回 'ok'/'skip'/'dead'。"""
    x, y = tgt["x"], tgt["y"]
    name = tgt["name"]
    adj_x, adj_y, dx, dy = bot.find_adjacent_tile(x, y)

    if not bot.safe_walk_to(adj_x, adj_y, location, timeout=30):
        log(f"  ⚠️ 走不到 ({x},{y}) {name}，跳过")
        return "skip"

    bot.select("Pickaxe")     # 确保手持镐子(不然空挥)
    face_to(bot, dx, dy, x, y)
    time.sleep(0.15)

    empty = 0
    for _ in range(MAX_BLOWS):
        if bot.state()["player"]["health"] <= 0:
            log("  倒下，撤退")
            return "dead"

        bot.use_tool("Pickaxe")
        time.sleep(0.4)

        r = bot.surroundings(3)
        # 只看目标那一格(x,y)是否还是同 objId —— 别被旁边同类的节点误判成"没碎"
        present = any(t.get("x") == x and t.get("y") == y and still_there(t, tgt)
                      for t in r.get("tiles", []))
        if not present:
            # 敲碎:等掉落落地,走过去捡(掉落自动吸附进包)
            time.sleep(1.0)
            try:
                bot.safe_walk_to(x, y, location, timeout=15)
                time.sleep(0.3)
            except Exception:
                pass
            return "ok"

        empty += 1
        if empty >= 3:
            # 连续几下没碎:多半是站位/朝向错了 → 重新找相邻格+重面向(不是原地站同一格)
            adj_x, adj_y, dx, dy = bot.find_adjacent_tile(x, y)
            if (adj_x, adj_y) != (x, y):   # 只有真换格才走
                bot.safe_walk_to(adj_x, adj_y, location, timeout=15)
            bot.select("Pickaxe")
            face_to(bot, dx, dy, x, y)
            time.sleep(0.15)
            empty = 0

    log(f"  ⚠️ {name} ({x},{y}) 敲了{MAX_BLOWS}下没碎，跳过")
    return "skip"


def main():
    parser = argparse.ArgumentParser(description="[rock] 室外镐击(默认只扫不敲)")
    parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口(默认7843)")
    parser.add_argument("--radius", type=int, default=14, help="扫描半径(默认14)")
    parser.add_argument("--max", type=int, default=0, help="最多敲多少个(0=不限)")
    parser.add_argument("--rounds", type=int, default=4, help="连续无新目标就停的轮数上限(默认4)")
    parser.add_argument("--dig", action="store_true", help="真敲(默认只扫)")
    parser.add_argument("--break-stone", action="store_true", help="连普通 Stone 也敲(默认跳过)")
    parser.add_argument("--dry-run", action="store_true", help="只扫(默认已是)")
    args = parser.parse_args()
    port = args.port or _DEFAULT_PORT
    skip_stone = not args.break_stone

    import mine_run
    mine_run.NAGI_URL = f"http://localhost:{port}"

    bot = MineBot(port)
    try:
        st = _get("/status")
    except Exception as e:
        log(f"❌ 连不上 /status({port}): {e}")
        return
    if not st.get("worldReady"):
        log("⚠️ 世界未加载(还在读档?)")
        return
    loc = bot.state().get("location", {}).get("name", "?")

    # 镐子检查
    inv = _get("/state").get("inventory", [])
    if not any("Pickaxe" in (i.get("name") or "") for i in inv):
        log("⚠️ 背包没有镐子(Pickaxe)——带镐再来,或调 /give")

    rounds_no_target = 0
    total_broken = 0
    while rounds_no_target < args.rounds:
        data = bot.surroundings(args.radius)
        targets, unknown, stones = scan(data, bot, skip_stone)

        if not args.dig:
            # ── 纯扫描:报候选 + 普通石(objId) + unknown ──
            seen_t = defaultdict(list)
            for tg in targets:
                seen_t[tg["name"]].append((tg["x"], tg["y"]))
            log(f"🗺️  [{loc}] 扫到 {len(targets)} 个候选(跳过普通石头={skip_stone})")
            for nm in sorted(seen_t, key=lambda k: -len(seen_t[k])):
                pts = " ".join(f"({x},{y})" for x, y in seen_t[nm])
                log(f"  · {nm}  ×{len(seen_t[nm])}  {pts}")
            if stones:
                st = defaultdict(int)
                for s in stones:
                    st[s.get("objId") or s["name"]] += 1
                log(f"\n🪨 普通石头(跳过;objId 没进 BREAK_IDS,若是可破物告诉我补):")
                log("  " + "  ".join(f"{k}×{v}" for k, v in sorted(st.items(), key=lambda kv: -kv[1])))
            unk = defaultdict(int)
            for u in unknown:
                unk[u["name"]] += 1
            if unk:
                log(f"\n🤔 没认出的(object 名非破前缀,可能是新可破物——补进 BREAK_PREFIXES/BREAK_IDS):")
                log("  " + "  ".join(f"{k}×{v}" for k, v in sorted(unk.items(), key=lambda kv: -kv[1])))
            log("\n💡 加 --dig 真敲;---跳过普通Stone默认;--break-stone 连石头也敲")
            return

        # ── 真敲 ──
        if not targets:
            rounds_no_target += 1
            continue
        rounds_no_target = 0
        # 按离玩家近到远敲(省走路)
        pp = bot.state().get("player", {})
        px, py = pp.get("x", 0), pp.get("y", 0)
        targets.sort(key=lambda t: abs(t["x"] - px) + abs(t["y"] - py))
        for tg in targets:
            if args.max and total_broken >= args.max:
                log(f"  达上限 {args.max},停")
                return
            res = break_rock(bot, tg, loc)
            if res == "dead":
                log("❌ 血量耗尽,撤退")
                return
            if res == "ok":
                total_broken += 1
                log(f"  ✅ 敲碎 {tg['name']} ({tg['x']},{tg['y']})  [#{total_broken}]")
        loc = bot.state().get("location", {}).get("name", loc)  # 地图可能变(误传送)

    log(f"\n🏁 [{loc}] 敲完(共 {total_broken} 块,连敲{args.rounds}轮无新目标)。")


if __name__ == "__main__":
    main()
