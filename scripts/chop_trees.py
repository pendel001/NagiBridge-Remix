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
# 🍥 限定区域（恒 2026-09-19）：4 个数=矩形 x1,y1,x2,y2；3 个数=圆 圆心x,y,半径（见 area_spec.py）。
#    留空 = 老行为（找身边的树）。⚠️ 脚本原来**没有这个参数**，而 MCP 那侧一直在把 `area` 原样塞进
#    命令行 ⇒ 传任何值都是 `unrecognized arguments` + exit 2（"域 op 断档"，2026-09-19 真机逮到）。
parser.add_argument("--area", default="",
                    help="限定区域：4 个数=矩形 x1,y1,x2,y2；3 个数=圆 圆心x,y,半径")
args = parser.parse_args()

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api
import tree_types as tt
import area_spec

try:
    _AREA = area_spec.parse_str(args.area) if args.area else None
except ValueError as e:
    print(f"❌ --area 参数错：{e}")
    raise SystemExit(2)

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
# ⚡ 低体力线 —— **全局唯一一份**（恒 2026-09-24：「锄/浇…低过 20 都停」，见 `stamina_common`）。
#    这里原本自己写了个 `20`：本文件、fish_run、nagi_mcp_server 各一份 —— 正是要收掉的东西。
from stamina_common import MIN_STAMINA as STAMINA_RESERVE
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
    _loc = data.get("location") or ""       # 🌲 农场外不保护苔雨树（见 tree_types.is_choppable）
    targets = []
    skipped = {}
    for t in data.get("tiles", []):
        terrain = t.get("terrain", "")
        obj = t.get("object", "")
        dist = abs(t["x"] - px) + abs(t["y"] - py)

        # 🍥 限定区域时，框外的树/树枝/大木桩**一律不看**（连"受保护跳过"都不计——
        #    那不是"看见了没砍"，是压根不在这趟活的范围内）。
        if _AREA is not None and not area_spec.contains(_AREA, t["x"], t["y"]):
            continue

        if terrain.startswith("Tree:"):
            ttype = tt.tree_type_of(terrain)
            if tt.is_choppable(ttype, _ALLOW, _loc):
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
        # 转圈吸掉落：绕四个邻格一圈再回到树格，把掉在地上的木头/树液吸附进包。
        # ⚠️ 2026-09-17 恒：「这个砍树会动我的键盘灯!A!w！S!!d」——原实现是
        #     `for step in ['D','A','W','S',...]: api.key(step)`，而 `api.key` → `POST /key` →
        #     `ModEntry.HandleKey` 里调的是 **Win32 `keybd_event()`**：操作系统级真按键
        #     （还顺带 SetForegroundWindow 抢前台）⇒ 会在恒的键盘上**真的**敲出 D A W S
        #     （当晚日志/灯都对得上），而且打进当时有焦点的任何窗口。
        #     改用本文件已在用的 walk_natural（走 /move 的 FindPath，会寻路不穿墙）：
        #     人走过去就吸附，一个系统按键都不再发。
        for dx, dy in ((1, 0), (-1, 0), (0, -1), (0, 1)):
            try:
                api.walk_natural(tx + dx, ty + dy)
            except Exception:
                pass
            time.sleep(0.1)
        try:
            api.walk_natural(tx, ty)   # 回树格收尾（树已倒，这格是通的）
        except Exception:
            pass
        time.sleep(0.1)

    return "chopped"


def _goto_area():
    """有 `--area` 时先走到区域里能站的格。

    ⚠️ 脚本原来只在**身边**（`surroundings` 半径 20）找树 —— 指定了一片远处的林子却不先过去的话，
    会一棵都找不到，还报 "No more trees nearby"，看着像"那片没树"（假阴性）。
    落脚点挑**区域内离中心最近的能站格**（`/passable_rect` 说了算，别硬闪到树上）。
    """
    if _AREA is None:
        return
    x1, y1, x2, y2 = area_spec.bounds(_AREA)
    tiles = area_spec.tiles(_AREA)
    try:
        wk = api.walk_ok_tiles(x1 - 1, y1 - 1, x2 + 1, y2 + 1)
        pk = api.stand_near(tiles, wk, *area_spec.center(_AREA)) if tiles else None
    except Exception:
        pk = None
    if pk:
        api.log(f"→ 先走到区域内 ({pk[0]},{pk[1]})（区域 {area_spec.describe(_AREA)}）")
        _ensure_at(pk[0], pk[1])


def _search_radius():
    """找树的扫描半径：没限定区域就用老的 20；限定了就按区域大小来（别够不着边）。"""
    if _AREA is None:
        return 20
    return min(30, max(20, area_spec.reach(_AREA) + 3))


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

    if _AREA is not None:
        api.log(f"🍥 限定区域：{area_spec.describe(_AREA)}")
        _goto_area()

    _skipped_seen = {}
    chopped = 0
    for attempt in range(args.count):
        trees, skipped = find_trees(_search_radius())
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
