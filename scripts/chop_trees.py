"""Chop trees -- walk to target, chop with correct hit counts per stage."""

import argparse
import os
import time

parser = argparse.ArgumentParser()
parser.add_argument("--count", type=int, default=10, help="Max trees to chop")
parser.add_argument("--port", type=int, default=7842)
# 🪓 放行名单（恒 2026-09-12 拍板）：默认**只砍橡/枫/松**，特殊树（蘑菇树/桃花心木/苔雨树/神秘树…）
#    一律受保护。值由 settings 域的 `chop` 设置经服务器传进来；手工跑脚本时也可直接
#    `--allow 蘑菇树,桃花心木`。⚠️ 放行值认不出来会**直接报错退出**，不静默当空（宁报错别兜底）。
parser.add_argument("--allow", default="", help="放行的特殊树种（名字/树号，逗号分隔；none/all）")
args = parser.parse_args()

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api
import tree_types as tt

_ALLOW, _ALLOW_ERR = tt.parse_allow(args.allow)
if _ALLOW_ERR:
    print(f"❌ --allow 参数错：{_ALLOW_ERR}")
    raise SystemExit(2)

# 固定类型（twig/stump/big_stump）直接用次数表，树类逐下检查
_FIXED_HITS = {"twig": 1, "stump": 5, "big_stump": 15}


def axe_fell_hits(axe_name):
    """砍倒"整棵(树+树桩)"所需击数——维基砍树表前后相加：铱 2+1=3 / 金 4+2=6 / 钢 6+3=9 / 铜 8+4=12 / 基础 10+5=15。
    ⚠️ 只对真正的树(Tree:*)。树枝/树桩/大木桩/大圆木沿用自己的 _FIXED_HITS（2026-08-21 恒：大木桩/大圆木那两个对象不清楚，排除）。"""
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

HIT_DELAY = 0.65
STAMINA_RESERVE = 20
MAX_TREE_HITS = 20  # 树类最多敲20下防死循环


def find_trees(radius=20):
    """Find chopable targets: 橡/枫/松(Tree:1~3) / twig / big_stump。

    ⚠️ 2026-09-12 恒拍板：**特殊树种默认不砍**（蘑菇树7/桃花心木8/苔雨树10~12/神秘树13/棕榈6·9），
    要砍得 `--allow` 显式放行。**被挡下的不静默丢**——一并返回计数，由调用方报给 AI/人看见
    （这条 docstring 以前写的就是"Tree:1~3"，但代码是 `startswith("Tree:")` 全收 —— 注释在说谎，
    真机实测站蘑菇树堆旁最近的可砍目标就是 Tree:7）。

    返回 `(targets, skipped)`；skipped = {树种id: 棵数}。
    """
    data = api.surroundings(radius)
    px, py = data["center"]["x"], data["center"]["y"]
    targets = []
    skipped = {}
    for t in data.get("tiles", []):
        terrain = t.get("terrain", "")
        obj = t.get("object", "")
        dist = abs(t["x"] - px) + abs(t["y"] - py)

        if terrain.startswith("Tree:"):
            ttype = tt.tree_type_of(terrain)
            if tt.is_choppable(ttype, _ALLOW):
                targets.append((t["x"], t["y"], terrain, dist))
            else:
                skipped[ttype] = skipped.get(ttype, 0) + 1
        elif obj == "Twig":
            targets.append((t["x"], t["y"], "twig", dist))
        elif obj in ("LargeStump", "LargeLog"):
            targets.append((t["x"], t["y"], "big_stump", dist))

    targets.sort(key=lambda t: t[3])
    return targets, skipped


def tile_has(tx, ty, check):
    data = api.surroundings(3)
    for t in data.get("tiles", []):
        if t["x"] == tx and t["y"] == ty:
            terrain = t.get("terrain", "")
            obj = t.get("object", "")
            if check == "tree" and terrain.startswith("Tree:"):
                return True
            if check == "stump" and (obj == "Twig" or terrain.startswith("Tree:")):
                return True
    return False


def tree_still_there(tx, ty):
    """Check if tree is still standing at position."""
    data = api.surroundings(3)
    for t in data.get("tiles", []):
        if t["x"] == tx and t["y"] == ty:
            if t.get("terrain", "").startswith("Tree:"):
                return True
    return False


def _ensure_at(tx, ty):
    """确保走到 (tx,ty)：walk_natural 走后若差>1格（走到别处/卡住），直接 /position 精确定位兜底。
    防空挥斧头（没站对位置就砍=空耗体力）。2026-08-21 恒"""
    api.walk_natural(tx, ty)
    time.sleep(0.3)
    p = api.state().get("player", {})
    if abs(p.get("x", -99) - tx) > 1 or abs(p.get("y", -99) - ty) > 1:
        api.log(f"  walk_natural 没到 ({tx},{ty})，/position 精确定位兜底")
        api._post("/position", {"x": tx, "y": ty})
        time.sleep(0.4)


def chop_at(tx, ty, target_type):
    """走到目标旁挥斧头。固定类型按次数表敲完；树类逐下检查，倒了就停。"""
    axe_name = "Axe"
    # 找一下背包里的斧头真名
    for item in api.state().get("inventory", []):
        name = item.get("name", "")
        if "Axe" in name:
            axe_name = name
            break

    # 树→斧头等级上限(整棵)；树枝/树桩/大木桩/大圆木→ _FIXED_HITS（大木桩/大圆木除外，沿用原方法 2026-08-21 恒）
    max_hits = _FIXED_HITS.get(target_type)
    if max_hits is None:
        max_hits = axe_fell_hits(axe_name)
    api.log(f"  [{target_type}] max {max_hits}h ({axe_name})")

    _ensure_at(tx, ty - 1)
    api.face(2)
    time.sleep(0.15)
    api.select(axe_name)
    time.sleep(0.15)

    is_tree = target_type.startswith("Tree:")
    for hit in range(max_hits):
        cur, _ = api.player_stamina()
        if cur < STAMINA_RESERVE:
            api.log(f"  Stamina low ({cur:.0f})")
            return "stamina"
        api.use_tool(axe_name)
        time.sleep(HIT_DELAY)
        if is_tree and not tree_still_there(tx, ty):
            if hit > 0:
                api.log(f"  down after {hit+1}h")
            return "done"

    return "done"


def chop_target(tx, ty, target_type):
    """砍一个目标。树类倒了检查树桩；固定类型直接敲完。"""
    is_tree = target_type.startswith("Tree:")

    result = chop_at(tx, ty, target_type)
    if result == "stamina":
        return "stamina"

    # 树倒了可能留树桩
    if is_tree:
        time.sleep(0.3)
        _ensure_at(tx, ty)
        if tile_has(tx, ty, "stump"):
            api.log(f"  Stump remaining...")
            result = chop_at(tx, ty, "stump")
            if result == "stamina":
                return "stamina"
            time.sleep(0.4)
            _ensure_at(tx, ty)
        # 转圈吸掉落：往4个方向走一步再走回来
        for step in ['D', 'A', 'W', 'S', 'D', 'A', 'W', 'S']:
            api.key(step)
            time.sleep(0.12)

    return "chopped"


def run():
    axe_name = "Axe"
    for item in api.state().get("inventory", []):
        n = item.get("name", "")
        if "Axe" in n:
            axe_name = n
            break
    api.log(f"=== Chop Trees (max {args.count}) ===")
    api.log(f"Axe: {axe_name} | twig/stump/big=fixed | Tree: hit&check up to 20")
    api.log(f"🌳 放行: {tt.allow_label(_ALLOW)}"
            + ("" if _ALLOW else "（特殊树种受保护；要砍用 settings chop 蘑菇树,桃花心木 …）"))

    s = api.state()
    inv = s.get("inventory", [])
    wood_before = sum(i["stack"] for i in inv if i and i["name"] == "Wood")
    api.log(f"Wood before: {wood_before}")

    _skipped_seen = {}
    chopped = 0
    for attempt in range(args.count):
        trees, skipped = find_trees()
        for k, v in skipped.items():          # 累计"看见过但没砍"的（同一棵会重复出现，取最大值）
            _skipped_seen[k] = max(_skipped_seen.get(k, 0), v)
        if not trees:
            if skipped:
                api.log(f"No more chopable trees nearby —— 剩下的都是受保护树种："
                        f"{tt.skipped_summary(skipped)}")
            else:
                api.log("No more trees nearby")
            break

        tx, ty, ttype, dist = trees[0]
        max_h = _FIXED_HITS.get(ttype, MAX_TREE_HITS)
        api.log(f"#{chopped+1} ({tx},{ty}) [{ttype}] max {max_h}h")

        result = chop_target(tx, ty, ttype)
        api.log(f"  -> {result}")

        if result == "chopped":
            chopped += 1
        elif result == "stamina":
            break

    s = api.state()
    inv = s.get("inventory", [])
    wood_after = sum(i["stack"] for i in inv if i and i["name"] == "Wood")
    api.log(f"Chopped {chopped} trees. Wood: {wood_before} -> {wood_after} (+{wood_after - wood_before})")
    # 🪓 被保护树种挡下的**必须报出来**（恒：跳过要看得见，不能静默"砍完了"）
    if _skipped_seen:
        _n1 = tt.tree_name(sorted(_skipped_seen, key=lambda k: -_skipped_seen[k])[0])
        api.log(f"🛡️ 受保护跳过：{tt.skipped_summary(_skipped_seen)}"
                f"　要砍其中某一种：settings chop {_n1}（可多种逗号并列）")
    api.log(f"Stamina: {s['player']['stamina']:.0f}/{s['player']['maxStamina']}")
    api.log(f"Time: {s['time']['timeOfDay']}")


if __name__ == "__main__":
    run()
