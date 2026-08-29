"""Machine Loader — 往空机器批量放原料（拟人走路版）。

每台机器：warp 到地点门口（进建筑走门）→ walk_to 沿机器间过道走到机器旁 → 选中原料 → interact。
机器横排竖排留过道，只走得到的地方（墙/被围自然跳过），不瞬移不穿墙。
走的是游戏自己的 checkForAction，配方时间/品质自动对（Keg 酿酒、Cask 陈化…）。
建筑内部 Game1.warpFarmer 按名进不去、多栋同名建筑（Cabin）靠建筑坐标 /warp_building 定位。

用法:
    python machine_loader.py Starfruit --type Keg
    python machine_loader.py "Starfruit Wine" --type Cask --location Cellar --count 10
"""

import argparse
import time
import stardew_api as api


def get_empty_machines(machine_type="", location=""):
    fr = api.farm_report()
    if not fr.get("ok"):
        return [], fr.get("error", "")
    ml = (fr.get("machines") or {}).get("machines") or []
    out = []
    for m in ml:
        if m.get("status") != "empty":
            continue
        if machine_type and not str(m.get("type", "")).lower() == machine_type.lower():
            continue
        if location and not str(m.get("location", "")).lower() == location.lower():
            continue
        out.append(m)
    return out, ""


def _wait_arrival(tx, ty, timeout=18):
    """轮询等 walk_to 到达 (tx,ty)（1 格内）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = api.state()
            px, py = s.get("player", {}).get("x"), s.get("player", {}).get("y")
            if px is not None and py is not None:
                if abs(px - tx) <= 1 and abs(py - ty) <= 1 and not s.get("player", {}).get("isMoving"):
                    return True
        except Exception:
            pass
        time.sleep(0.7)
    return False


def _current_location():
    try:
        return api.machines().get("location")
    except Exception:
        return None


def enter_building(loc, b):
    """拟人进门：walk_to 到门口 → 站门下方 → 面朝门 → interact 开门进去。
    不瞬移。walk_to 走不到门口时（不肯出门）才回退 warp 到门外。返回 True/False。"""
    api.log(f"🚪 走门口进 {loc} Farm({b['doorX']},{b['doorY']})")
    api._post("/walk_to", {"location": "Farm", "x": b["doorX"], "y": b["doorY"]})
    if not _wait_arrival(b["doorX"], b["doorY"], timeout=20):
        api.log("  walk_to 走不到，warp 到门外")
        wr = api.warp_into("Farm", b["doorX"], b["doorY"])
        if not wr.get("ok"):
            return False
        time.sleep(0.5)

    if _current_location() and str(_current_location()).lower() == str(loc).lower():
        return True

    api._post("/position", {"x": b["doorX"], "y": b["doorY"] + 1})
    time.sleep(0.5)
    api._post("/face", {"direction": 0})   # 0=上，门在头顶
    time.sleep(0.3)
    api._post("/interact")
    for _ in range(10):
        time.sleep(0.8)
        if _current_location() and str(_current_location()).lower() == str(loc).lower():
            return True
    return False


# ── 机器需求判定(2026-08-29 细化装载失败报错, 数据源=恒提供的星露谷设备输入映射表) ──
# 键=mod 报的机器 type(英文), 值=正确输入的描述(含煤条件)。2026-08-29 恒拍板:只列种类,给 AI 自查装对没
_MACHINE_NEED = {
    "Furnace": "矿产(铜/铁/金/铱矿石)+1煤炭",
    "Heavy Furnace": "矿产+3煤炭",
    "Fish Smoker": "鱼+1煤炭",
    "Keg": "水果/蔬菜/蜂蜜/咖啡豆/茶叶",
    "Seed Maker": "作物(水果/蔬菜)",
    "Preserves Jar": "水果/蔬菜",
    "Cheese Press": "牛奶/大壶牛奶",
    "Mayonnaise Machine": "鸡蛋/鸭蛋/鸵鸟蛋/虚空蛋",
    "Loom": "兔毛/绵羊毛",
    "Oil Maker": "向日葵种子/玉米/油菜籽/松露",
    "Dehydrator": "5个同星级同名果蔬/蘑菇",
    "Crystalarium": "宝石(钻/翡翠/红宝/黄玉/紫/海蓝/绿宝石)",
    "Charcoal Kiln": "木材×10",
    "Bones Mill": "骨头碎片/古代骨头",
    "Geode Crusher": "晶球",
    "Recycling Machine": "垃圾/破碎CD/废报纸等",
    "Wood Chipper": "硬木",
    "Slime Egg Press": "史莱姆泥×100",
    "Bait Maker": "鱼",
}
# 自动/放置类(无需放料、随时间自动产)——注意 Bait Maker 要放鱼,不算自动
_AUTO_MACHINE = {"Bee House", "Tapper", "Heavy Tapper", "Lightning Rod", "Solar Panel",
                 "Worm Bin", "Deluxe Worm Bin", "Mushroom Log",
                 "Incubator", "Slime Incubator", "Ostrich Incubator",
                 "Statue Of Perfection", "Statue Of Endless Fortune", "Garden Pot"}


def _machine_missing_reason(mtype):
    """机器 type(英文) → 一句话:这台机器要什么(列正确种类, AI 自己对照是否装错品类)。"""
    mt = (mtype or "").strip()
    if mt in _AUTO_MACHINE:
        return f"{mt} 是自动/放置类,无需放料"
    need = _MACHINE_NEED.get(mt)
    if not need:
        return f"{mt} 需求未登记(查 help 或让恒确认)"
    return f"{mt} 需要 {need}"
    """拟人走路版：走门口开门进去（或已在屋内跳过）→ walk_to 走到机器旁过道 → 选原料 → interact。
    机器横排竖排留过道，walk_to 沿过道走（只走得到的地方，墙/被围就跳过）。
    返回 (成功?, 说明)。"""
    loc, x, y = m["location"], m["x"], m["y"]
    b = m.get("building")   # 建筑内机器带 Farm 建筑坐标（多栋同名建筑精确定位用）

    # 1. 进地点：建筑走门（开门），非建筑 warp_into 入口。已在同屋则跳过（避免重复进门）
    if not skip_enter:
        if b:
            if not enter_building(loc, b):
                return False, f"进门失败: {loc}"
        else:
            wr = api.warp_into(loc)
            if not wr.get("ok"):
                return False, f"warp 失败: {wr.get('error', '')}"
            time.sleep(0.5)

    # 2. 依次尝试机器四邻格，walk_natural 走过去（/move 游戏 BFS 真走路；走不到才 position 兜底）
    # ⚠️ 2026-08-13：原 walk_to 对鱼饵机"纯粹没走过去"（长距离 interact 装上了）——改 walk_natural 强制走路
    for ax, ay in [(x, y + 1), (x, y - 1), (x - 1, y), (x + 1, y)]:
        try:
            api.walk_natural(ax, ay)
        except Exception:
            pass
        if not _wait_arrival(ax, ay, timeout=18):
            continue

        # 2b. 精确站到 (ax,ay)：walk_to 落点可能偏 1 格（容忍判定），偏了 face/interact 会点错机器
        api._post("/position", {"x": ax, "y": ay})
        time.sleep(0.3)

        # 3. 选中原料 → 对机器瓦片直接 interact（/interact {x,y}，8 方向都行）
        s = api.select(item_name)
        if not s.get("ok"):
            return False, f"背包选中失败: {s.get('error', '')}"
        time.sleep(0.15)

        r2 = api._post("/interact", {"x": x, "y": y})
        time.sleep(0.4)

        # 4. 验证：重新扫当前地点，(x,y) 这台是否已进入 processing
        verified = False
        try:
            for mm in (api.machines().get("machines") or []):
                if int(mm.get("x", -1)) == x and int(mm.get("y", -1)) == y:
                    verified = mm.get("status") == "processing"
                    break
        except Exception:
            pass
        if verified:
            return True, ""
        if r2.get("actionTriggered"):
            return True, "触发未验证"
        # 细化报错:列这台机器要什么,给 AI 对照是否装错品类(2026-08-29 恒拍板:只说种类,别啰嗦)
        return False, _machine_missing_reason(m["type"])
    return False, "四邻都走不到（被围死）"


def run(item_name, machine_type="", location="", count=0, no_enter=False):
    api.log(f"=== Machine Loader: item={item_name} type={machine_type or 'any'} loc={location or 'all'} no_enter={no_enter} ===")
    empty, err = get_empty_machines(machine_type, location)
    if err:
        api.log(f"获取机器列表失败: {err}")
        return
    api.log(f"找到空机器 {len(empty)} 台")
    if not empty:
        api.log("没有空机器")
        return

    loaded = 0
    skipped = 0
    last_key = None   # (location, building x, building y) — 同地点连续机器不再重复 warp
    for m in empty:
        if count and loaded >= count:
            break
        b = m.get("building") or {}
        key = (m.get("location"), b.get("x"), b.get("y"))
        skip_enter = no_enter or (key == last_key)
        ok, msg = load_one(m, item_name, skip_enter=skip_enter)
        last_key = key
        if ok:
            loaded += 1
            api.log(f"  ✅ 装上 {m['type']} @ {m['location']} ({m['x']},{m['y']})" + (f" [{msg}]" if msg else ""))
        else:
            skipped += 1
            api.log(f"  ⚠️ 跳过 {m['type']} @ {m['location']} ({m['x']},{m['y']}): {msg}")
    api.log(f"完成: 装上 {loaded} 台, 跳过 {skipped} 台")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load raw materials into empty machines (game-native)")
    parser.add_argument("item", help="原料英文名/ID，如 Starfruit；Cask 用成品如 Starfruit Wine")
    parser.add_argument("--type", default="", help="机器类型，如 Keg / Cask（留空=所有空机器）")
    parser.add_argument("--location", default="", help="限定地点，如 Cellar / Big Shed（留空=全农场）")
    parser.add_argument("--count", type=int, default=0, help="最多装几台（0=不限）")
    parser.add_argument("--no-enter", action="store_true", help="已在目标屋内，跳过进门的 warp")
    parser.add_argument("--port", type=int, default=7843)   # AI 角色进程（恒批注 2026-08-13：别打到 host 恒的号）
    args = parser.parse_args()

    import os
    os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
    import importlib
    importlib.reload(api)

    run(args.item, args.type, args.location, args.count, args.no_enter)
