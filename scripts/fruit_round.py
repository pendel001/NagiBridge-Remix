"""fruit_round.py — 一间屋里果酒收放，按"过道格"一站处理一圈（拟人，逻辑简单）。

核心思路（用户 2026-08-04 提出）：机器横排竖排留过道，站在一个过道格里，
上下左右最多 4 台机器一次性收/放 —— 不用每台机器跑一趟。

流程: 走门口开门进 → 该屋熟/空机器 → 算过道格集合(去重,去机器本格)
       → 就近排路径 → 逐格: 检测 4 邻，熟的空手收、空的选果放 → 完成

用法:
    python fruit_round.py --location Cabin --fruit Starfruit --machine Keg
"""

import argparse
import sys
import os
import time
import stardew_api as api
from machine_loader import _AUTO_MACHINE as AUTO_MACHINE   # 自动/放置类设备(蜂房/避雷针/太阳能板/树液采集器/虫饵盒等)——只收不放

EMPTY_HAND = "Pickaxe"   # 收产物时空手（选工具使 ActiveObject=null）
# 行走只走 4 方向（正交）——之前 8 方向会把对角墙边格拉进行走集合导致绕墙
WALK_NEIGHBORS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
# 交互 8 方向：站在一格能右键到对角机器（SDV 站在 (1,1) 能右键 (2,2)，"孤岛"布局靠对角收）
INTERACT_NEIGHBORS = WALK_NEIGHBORS + [(1, 1), (1, -1), (-1, 1), (-1, -1)]


def _wait_arrival(tx, ty, timeout=18):
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
    """走门口 → 开门进去（拟人）。返回 True/False。

    ⚠️ 2026-09-16 恒观察到「**重跑脚本时先传到屋外再进来**」——就是这里造成的：
       下面那句 `walk_to {location:"Farm"}` 是**无条件**发的，而人在屋里时它走**跨图分支**
       （先 warp 到目标图=屋外，再走回门口、再进门）⇒ 肉眼看就是"被传到屋外又跑进来"。
       ⇒ 开头先确认是不是**已经在里面**，是就直接收工（恒："建议也是从当前位置出发过去建筑"）。
    """
    if str(_current_location() or "").lower() == str(loc).lower():
        api.log(f"🚪 已经在 {loc} 里了，不重复进出")
        return True
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
    api._post("/face", {"direction": 0})
    time.sleep(0.3)
    api._post("/interact")
    for _ in range(10):
        time.sleep(0.8)
        if _current_location() and str(_current_location()).lower() == str(loc).lower():
            return True
    return False


def _filter_passable(tiles):
    """只保留可站的过道格（剔除墙边/不可站，避免 walk_to 绕墙）。"""
    keep = set()
    for ax, ay in tiles:
        r = api._post("/passable", {"x": ax, "y": ay})
        if r.get("passable"):
            keep.add((ax, ay))
    return keep


def plan_path(tiles, start):
    """最近邻规划：从当前位置起，反复挑最近的未访问过道格。"""
    remaining = list(tiles)
    path = []
    px, py = start
    while remaining:
        t = min(remaining, key=lambda k: abs(k[0] - px) + abs(k[1] - py))
        path.append(t)
        px, py = t
        remaining.remove(t)
    return path


def go_to_tile(loc, ax, ay):
    """走到过道格 + 精确落点。返回 True/False。"""
    r = api._post("/walk_to", {"location": loc, "x": ax, "y": ay})
    if not r.get("ok"):
        return False
    if not _wait_arrival(ax, ay, timeout=18):
        return False
    api._post("/position", {"x": ax, "y": ay})
    time.sleep(0.3)
    return True


def interact_at(x, y):
    """对指定瓦片直接 checkAction（8 方向对角交互也行，不需要朝向）。"""
    r = api._post("/interact", {"x": x, "y": y})
    time.sleep(0.4)
    return r


def select_fruit(fruit):
    """选待放果物，并**回读 `currentItem` 核对手上真的是不是它**。返回 bool。

    ⚠️ 2026-09-16 真机抓到的坑（"放不进"报错的真凶，我一开始还错怪了斜角）：
       `/select` 在精确匹配失败后有 **Contains 兜底**（`"Pickaxe"→"Iridium Pickaxe"`，是特意加的），
       于是 `select("Ancient Fruit")` 在果子用完时**会匹配到 "Ancient Fruit Wine"**——
       背包里 583 瓶上古水果果酒当场被选上手，然后拿去往小桶里塞（桶不收成品酒）⇒
       日志报"放不进"，看着像"机器不匹配"，其实**根本是没果子了**。
       更早的版本还会把 `m["status"]` 改成 "processing" 假装放成功——错得无声无息。
       ⇒ 选完必须回读核对：`/state` 的 `player.currentItem` 得**正好等于**要放的那个名字。
    """
    s = api.select(fruit)
    if not s.get("ok"):
        return False
    time.sleep(0.15)
    try:
        cur = (api.state().get("player") or {}).get("currentItem") or ""
    except Exception:
        return False
    return str(cur).strip().lower() == str(fruit).strip().lower()


def process_tile(ax, ay, machines_by_pos, fruit):
    """站在 (ax,ay)，检测上下左右 4 邻机器：熟的空手收，空的选果放。
    自动设备(蜂房/避雷针/太阳能板等)只收不放——收完置 "auto" 终态，不当空机塞料。
    返回 (收了几台, 放了几台, 日志, 停因)。
    停因: "" = 正常继续 / "no_fruit" = 待放物品已用完 / "rejected" = 机器不收这东西。

    ⚠️ 2026-09-16 恒（"拿着电池去放它居然不拦你"）：原来这两个失败**都被吞掉继续走**——
      ①没果了只记一句 `没果(x,y)`，接着把整间屋的过道格全走完（一台都放不上，纯浪费）；
      ②`interact_at` 没收下东西（`actionTriggered=false`）时，**照样把 `m["status"]` 改成 "processing"**
        —— 等于**假装放成功了**，日志上看着像放上了，机器其实还是空的。
      恒定的规矩：收放要"**待放物品放完 / 手上的东西放不进设备**"时就收工返回结果，别硬走完。
      ⇒ 现在这两种情况都当**终止信号**抛给主循环（见 run() 里的 break）。
    """
    collected = 0
    loaded = 0
    logs = []
    stop = ""
    for dx, dy in INTERACT_NEIGHBORS:
        m = machines_by_pos.get((ax + dx, ay + dy))
        if not m:
            continue
        x, y = m["x"], m["y"]
        auto = str(m.get("type") or "") in AUTO_MACHINE
        if m["status"] == "ready":
            api.select(EMPTY_HAND)          # 空手
            time.sleep(0.1)
            r = interact_at(x, y)
            if r.get("actionTriggered"):
                collected += 1
                logs.append(f"收({x},{y})")
            # 收了变空 → 接着放新果；但自动设备(蜂房/避雷针/太阳能板…)只收不放
            m["status"] = "auto" if auto else "empty"
        if fruit and m["status"] == "empty" and not auto:
            if select_fruit(fruit):
                r = interact_at(x, y)
                if r.get("actionTriggered"):
                    loaded += 1
                    logs.append(f"放({x},{y})")
                    m["status"] = "processing"
                else:
                    # 游戏没收下（物品与机器不匹配等）→ 别再假装"已放"，报停
                    # ⚠️ 斜角**不是**这里的成因：`interact_at` 走 `/interact{x,y}`，docstring 明写
                    #    "8 方向对角交互也行，不需要朝向"。2026-09-16 我一度把失败归给"SDV 不能斜着交互"
                    #    —— **错了**（恒当场纠正："之前我们测过收放斜角的，成功了才有这个工具"）。
                    #    那两次失败的真因见下面 `_select_fruit`：水果用完了，select 兜底抓成了果酒。
                    logs.append(f"放不进({x},{y})")
                    stop = stop or "rejected"
            else:
                logs.append(f"没果({x},{y})")
                stop = stop or "no_fruit"
    return collected, loaded, " ".join(logs), stop


def run(location, machine_type, fruit):
    api.log(f"=== Fruit Round(过道格版): loc={location} machine={machine_type or 'any'} fruit={fruit or '-'} ===")
    if not fruit:
        # ⚠️ 恒 2026-09-16："如果只收，那可能没必要用这个，用刚刚的一键收就行"
        api.log("⚠️ 没传待放物品 ⇒ 这是**只收不放**。只收请直接用 farm ops=collect"
                "（全农场一遍瞬收、不走路）；本 op 的价值在「收完顺手放」，要收放请传 item。")

    fr = api.farm_report()
    if not fr.get("ok"):
        api.log(f"farm_report 失败: {fr.get('error','')}")
        return
    ml = (fr.get("machines") or {}).get("machines") or []

    machines = []
    b = None
    for m in ml:
        if str(m.get("location", "")).lower() != str(location).lower():
            continue
        if machine_type and str(m.get("type", "")).lower() != machine_type.lower():
            continue
        if m.get("status") in ("ready", "empty"):
            machines.append(m)
        if m.get("building"):
            b = m["building"]

    if not machines:
        api.log("没有可收/可放的机器")
        return

    # 进门
    if b:
        if not enter_building(location, b):
            api.log(f"❌ 进不去 {location}")
            return
    else:
        # 非建筑（Farm 室外/地窖等）→ warp_into 入口；但已在目标图就跳过（warp_into 无"已在此图"守卫，
        # 会把你拉到图入口瓦——室外收蜂房/避雷针时别被拖走）
        cur = (api.state().get("location") or {}).get("name", "")
        if str(cur).lower() != str(location).lower():
            wr = api.warp_into(location)
            if not wr.get("ok"):
                api.log(f"❌ warp 进 {location} 失败")
                return
            time.sleep(0.5)

    # 过道格集合：所有目标机器的 4 方向邻（只走正交，不走对角墙边格），去掉机器本格，去重
    machines_by_pos = {(m["x"], m["y"]): m for m in machines}
    tiles = set()
    for m in machines:
        for dx, dy in WALK_NEIGHBORS:
            t = (m["x"] + dx, m["y"] + dy)
            if t not in machines_by_pos:      # 排除机器本格
                tiles.add(t)
    api.log(f"目标机器 {len(machines)} 台 → 过道格(原始) {len(tiles)} 个")
    tiles = _filter_passable(tiles)           # 只留可站的，剔除墙边
    api.log(f"过道格(可站) {len(tiles)} 个")

    # 就近排路径
    pos = api.state().get("player", {})
    start = (pos.get("x", 0), pos.get("y", 0))
    path = plan_path(list(tiles), start)

    total_collected = 0
    total_loaded = 0
    visited = 0
    stop_reason = ""
    for ax, ay in path:
        if not go_to_tile(location, ax, ay):
            api.log(f"  ⚪ 过道格 ({ax},{ay}) 走不到，跳过")
            continue
        c, l, log, stop = process_tile(ax, ay, machines_by_pos, fruit)
        if c or l:
            total_collected += c
            total_loaded += l
            visited += 1
            api.log(f"  🟢 格({ax},{ay}) 收{c}放{l}  {log}")
        else:
            visited += 1
            api.log(f"  ⚪ 格({ax},{ay}) 无操作")
        if stop:
            # ⚠️ 恒 2026-09-16：放不下去就**立刻收工返回结果**，别把剩下的过道格硬走完
            stop_reason = stop
            _why = ("待放物品已用完" if stop == "no_fruit"
                    else f"「{fruit}」放不进这里的机器（游戏没收下）")
            api.log(f"  ⏹ {_why} —— 提前收工（已走 {visited}/{len(path)} 格，还剩 {len(path) - visited} 格没走）")
            break
    _tail = ""
    if stop_reason:
        _tail = ("，⏹ 提前收工：待放物品已用完" if stop_reason == "no_fruit"
                 else f"，⏹ 提前收工：「{fruit}」放不进机器")
    api.log(f"完成: 走了 {visited}/{len(path)} 格, 收 {total_collected} 台, 放 {total_loaded} 台{_tail}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Per-building fruit round via aisle tiles (batch collect/load per stop)")
    parser.add_argument("--location", required=True, help="屋子/地点名，如 Cabin / Big Shed")
    parser.add_argument("--machine", default="", help="机器类型，如 Keg / Cask（留空=熟/空都做）")
    parser.add_argument("--fruit", default="", help="要放的原料英文名（留空=只收不放）")
    parser.add_argument("--port", type=int, default=7843)
    args = parser.parse_args()

    os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
    import importlib
    importlib.reload(api)

    run(args.location, args.machine, args.fruit)
