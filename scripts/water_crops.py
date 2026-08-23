"""Water all unwatered crops — tool_area 蓄力拟人（对齐锄地 _till_rect）。
2026-08-15 恒：浇水主方法用 tool_area 蓄力——基础壶自动逐格挥壶（一格一格走位浇），
升级壶蓄力覆盖范围（3线/5线/3×3/6×3），站位/取余数补边界全由 ModEntry 锚点机制处理；
漏格由 DLL 自动补（取余补站位蓄力补，不直接改地块，till/water 统一）。
⚠️ 不用 (已删)/water_area 直接改地块（作弊）；不 /tool（实测无效，check_design.py 有挡）。
流程：装备水壶(无→报错) → cluster 未浇作物 → 每簇 tool_area 蓄力浇 → 中途没水 /refill →
补浇：重扫漏的 cluster 再 tool_area（兜底）→ 验证报告。
"""
import argparse
import os
import time

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=7842)
args = parser.parse_args()

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api


def find_unwatered(radius=30):
    data = api.surroundings(radius)
    tiles = data.get("tiles", [])
    return [(t["x"], t["y"]) for t in tiles
            if t.get("terrain") == "HoeDirt"
            and not t.get("watered")
            and t.get("crop")
            and not t.get("harvestable")]  # 跳过已成熟


def cluster(pts, gap=10):
    """按距离聚类（gap=曼哈顿距离上限），散点聚成几簇。
    tool_area 的蓄力锚点会覆盖整个 bbox，分簇避免跨空地"挪两格浇一格"。"""
    groups = []
    for pt in sorted(pts):
        placed = False
        for g in groups:
            if min(abs(pt[0] - px) + abs(pt[1] - py) for px, py in g) <= gap:
                g.append(pt)
                placed = True
                break
        if not placed:
            groups.append([pt])
    return groups


def equip_watering_can():
    """装备最好的水壶 + 灌满。返回 (是否装备成功, 水壶名)。"""
    try:
        inv = api.state().get("inventory", [])
        # 水壶名：Iridium/Copper/Iron/Gold Watering Can / Watering Can（优先级降序）
        can_names = ["Iridium Watering Can", "Copper Watering Can", "Iron Watering Can",
                     "Gold Watering Can", "Watering Can"]
        for name in can_names:
            if any((i.get("name") or "") == name for i in inv):
                api.select(name)
                time.sleep(0.3)
                # 灌满水壶
                st = api.state()
                wc = next((i for i in st.get("inventory", []) if (i.get("name") or "") == name), {})
                if (wc.get("waterLeft") or 0) == 0:
                    try:
                        api.refill_water()
                        time.sleep(0.3)
                    except Exception:
                        pass
                return True, name
    except Exception:
        pass
    return False, None


def _refill_if_needed():
    """水壶剩水不多就自动补满。返回 True=补过。"""
    try:
        st = api.state()
        wc = next((i for i in st.get("inventory", []) if "Watering Can" in (i.get("name") or "")), {})
        if (wc.get("waterLeft") or 0) <= 2:
            api.refill_water()
            api.log("  🔄 水壶没水，自动补水后继续")
            time.sleep(0.3)
            return True
    except Exception:
        pass
    return False


def _water_rect(x1, y1, x2, y2):
    """对一块矩形调 /tool_area 蓄力浇水（基础壶逐格挥壶 / 升级壶蓄力覆盖）。
    ⚠️ 走位+蓄力可能很久（基础壶逐格挥壶），必须长超时——10s 会把蓄力截断。
    补漏由 ModEntry 自动做（取余补站位蓄力补，不直接改地块），返回 patches / still_missing。"""
    r = api._post("/tool_area",
                  {"operation": "water", "x1": x1, "y1": y1, "x2": x2, "y2": y2},
                  timeout=600)
    if not r.get("ok"):
        return f"  ❌ tool_area 浇水失败: {r.get('error', r)}"
    patches = r.get("patches", 0)
    still = r.get("still_missing") or []
    s = f"  ✅ tool_area 蓄力浇 ({x1},{y1})-({x2},{y2})"
    if patches:
        s += f"，取余补站位自动补 {patches} 格"
    if still:
        s += f"，仍漏 {len(still)} 格"
        for m in still[:3]:
            rsn = m.get('reason') or ''
            s += f" ({m.get('x')},{m.get('y')})「{rsn}」" if rsn else f" ({m.get('x')},{m.get('y')})"
    return s


def run():
    api.log("=== Water Crops (tool_area 蓄力拟人) ===")
    unwatered = find_unwatered()
    if not unwatered:
        api.log("Nothing to water!")
        return

    api.log(f"Found {len(unwatered)} unwatered crops — cluster 聚类后 tool_area 蓄力浇")
    has_can, can_name = equip_watering_can()
    if not has_can:
        # ⚠️ 2026-08-15 恒：没水壶不能浇！(已删)/water_area 直接改地块是作弊，不能当无工具替代。
        api.log("  ⚠️ 没带水壶，无法浇水——回家拿水壶再来")
        return

    try:
        st0 = api.state()
        can_level = st0.get("player", {}).get("currentToolUpgrade", 0)
    except Exception:
        can_level = 0
    shape = {0: "1格", 1: "3线", 2: "5线", 3: "3×3", 4: "6×3"}.get(can_level, "?")
    api.log(f"  → {can_name} ({can_level}级:{shape})，tool_area 按等级蓄力覆盖范围")

    # 主浇：聚类 → 每簇 tool_area 蓄力浇水（站位/补边界由 ModEntry 锚点机制处理）
    groups = cluster(unwatered, gap=10)
    api.log(f"  → {len(groups)} 簇")
    for i, g in enumerate(groups, 1):
        x1 = min(p[0] for p in g); x2 = max(p[0] for p in g)
        y1 = min(p[1] for p in g); y2 = max(p[1] for p in g)
        _refill_if_needed()
        try:
            api.log(f"  [{i}/{len(groups)}] 簇 ({x1},{y1})-({x2},{y2}) {len(g)}格")
            api.log(_water_rect(x1, y1, x2, y2))
            time.sleep(0.3)
        except Exception as e:
            api.log(f"  ⚠️ 簇 ({x1},{y1})-({x2},{y2}) 失败: {e}")

    # 补浇兜底：重扫漏的格子（初始扫描范围外/被跳过的新未浇），cluster 后再 tool_area 补——不 (已删)/water_area（恒）
    missed = find_unwatered()
    if missed:
        api.log(f"  ⚠️ 补浇 {len(missed)} 格（cluster 后再 tool_area 蓄力补，不 (已删)/water_area 作弊）")
        for g in cluster(missed, gap=10):
            x1 = min(p[0] for p in g); x2 = max(p[0] for p in g)
            y1 = min(p[1] for p in g); y2 = max(p[1] for p in g)
            _refill_if_needed()
            try:
                api.log(_water_rect(x1, y1, x2, y2))
                time.sleep(0.3)
            except Exception:
                api.log(f"    ⚠️ 补浇 ({x1},{y1})-({x2},{y2}) 失败")
        time.sleep(0.3)

    # 验证（真浇了才算）
    still = find_unwatered()
    if still:
        api.log(f"  Still {still} unwatered（tool_area 补浇后仍漏——报告，不 (已删)/water_area 作弊）")
    else:
        api.log("  All watered!")

    s = api.state()
    api.log(f"Done! {len(groups)} 簇蓄力浇水，覆盖 {len(unwatered)} 格（真走位+蓄力挥壶）")
    api.log(f"Stamina: {s['player']['stamina']:.0f}/{s['player']['maxStamina']}")
    api.log(f"Time: {s['time']['timeOfDay']}")


if __name__ == "__main__":
    run()
