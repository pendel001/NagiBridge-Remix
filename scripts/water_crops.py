"""Water all unwatered crops — tool_area 蓄力拟人（对齐锄地 _till_rect）。
2026-08-15 恒：浇水主方法用 tool_area 蓄力——基础壶自动逐格挥壶（一格一格走位浇），
升级壶蓄力覆盖范围（3线/5线/3×3/6×3），站位/取余数补边界全由 ModEntry 锚点机制处理；
漏格由 DLL 自动补（取余补站位蓄力补，不直接改地块，till/water 统一）。
⚠️ 不用 (已删)/water_area 直接改地块（作弊）；不 /tool（实测无效，check_design.py 有挡）。
流程：装备水壶(无→报错) → cluster 未浇作物 → 每簇 tool_area 蓄力浇 →
      **中途壶空了就停下、跑水边拟人打水、回来拿同一块矩形接着浇** → 补浇兜底 → 验证报告。

🚨 2026-09-23 恒真机两条，都在这一版修掉：
  ① 「没有作物、没有耕的地也在浇！很笨笨」= **C# `/tool_area` 的矩形分支套的是锄地判据**
     （`IsTillTarget`），不是浇水判据 ⇒ 目标格变成"矩形里的可耕空地"。现在 C# 按 operation 分岔、
     与"没传 rect 的自动检测分支"共用同一份 `IsWaterTarget`（HoeDirt+有作物+未浇+没熟）。
  ② 「ai 的小人每走一步都弹空水壶那个没水的疑问表情」= 壶挥空了没人管（`/refill` 那条**作弊**只在
     簇边界补一次，簇一大就中途见底）。现在 C# 壶空即**停手报缺**（`out_of_water`），
     这边收到就 `api.refill_can_natural()` **走去水边真打水**（不再是原地改数字）。
"""
import argparse
import os
import time

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=7842)
args = parser.parse_args()

os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
import stardew_api as api
# ⚡ 低体力线 —— **全局唯一一份**（恒 2026-09-24：「耕种相关（锄/浇）也要…低过 20 都停」）。
#    浇水一格 2 点体力，一整片能一次烧空 ⇒ **每簇动手前**先看一眼，低于线就停手（已浇的算数）。
from stamina_common import is_low as _sta_low, stop_note as _sta_note


def _stam():
    """(当前体力, 上限) —— 读不到 (None, None)。取数口只有一个（同 server 的 `_stamina_now`）。"""
    try:
        return api.player_stamina()
    except Exception:
        return (None, None)


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
    """装备最好的水壶。返回 (是否装备成功, 水壶名)。
    ⚠️ 2026-09-23 恒：这里**不再顺手灌满**——补水只走 `refill_can_natural()`（人真去水边打水），
    而它"只在水真的空了才去"。壶要是空着来的，第一块地会让 C# 停手报 `out_of_water`，
    循环里自然会先跑一趟水边，不必在这儿预先作弊补满。"""
    try:
        inv = api.state().get("inventory", [])
        # 水壶名：Iridium/Copper/Iron/Gold Watering Can / Watering Can（优先级降序）
        can_names = ["Iridium Watering Can", "Copper Watering Can", "Iron Watering Can",
                     "Gold Watering Can", "Watering Can"]
        for name in can_names:
            if any((i.get("name") or "") == name for i in inv):
                api.select(name)
                time.sleep(0.3)
                return True, name
    except Exception:
        pass
    return False, None


def _water_rect(x1, y1, x2, y2):
    """对一块矩形调 /tool_area 蓄力浇水（基础壶逐格挥壶 / 升级壶蓄力覆盖）。
    ⚠️ 走位+蓄力可能很久（基础壶逐格挥壶），必须长超时——10s 会把蓄力截断。
    补漏由 ModEntry 自动做（取余补站位蓄力补，不直接改地块）。
    返回 dict：{ok, text, out_of_water}——`out_of_water=True` = 浇到一半壶空了、C# **已停手报缺**
    （剩下没浇的格子还在，打水后拿同一块矩形重发即可：矩形过滤只挑"还没浇的"，天然幂等）。"""
    try:
        r = api._post("/tool_area",
                      {"operation": "water", "x1": x1, "y1": y1, "x2": x2, "y2": y2},
                      timeout=600)
    except Exception as e:
        return {"ok": False, "out_of_water": False, "text": f"  ❌ tool_area 浇水调用失败: {e}"}
    oow = bool(r.get("out_of_water"))
    if not r.get("ok"):
        return {"ok": False, "out_of_water": oow,
                "text": f"  ❌ tool_area 浇水失败: {r.get('error', r)}"}
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
    if oow:
        s += "　💧 中途壶空了（C# 已停手，剩格打水后续浇）"
    return {"ok": True, "out_of_water": oow, "text": s}


def _water_cluster(x1, y1, x2, y2, tries=4):
    """浇一块矩形：**壶空了就跑水边拟人打水、回来拿同一块矩形接着浇**（恒 2026-09-23：
    只在水真的空了才去，不提前补）。返回 True=这块浇完了/尽力了，False=打不到水被迫半途停下。"""
    for _ in range(tries):
        r = _water_rect(x1, y1, x2, y2)
        api.log(r["text"])
        if not r["out_of_water"]:
            return True
        ok, msg = api.refill_can_natural(log=api.log)
        api.log("  " + msg)
        if not ok:
            api.log("  ⏭ 打不到水 —— 这块剩下没浇的格子先留着（补浇轮/下一趟再处理）")
            return False
    api.log(f"  ⚠️ ({x1},{y1})-({x2},{y2}) 来回打水 {tries} 次还没浇完 —— 先停，别再空转")
    return False


def run():
    api.log("=== Water Crops (tool_area 蓄力拟人) ===")
    unwatered = find_unwatered()
    if not unwatered:
        # 🌧️ 2026-09-23：把"为什么没得浇"说清楚（恒 2026-09-12 记过：异步下这句回不到 AI，
        #    它以为自己浇过一遍）。晴天/温室也可能真的没得浇，所以只在下雨时才提雨。
        why = ""
        try:
            if bool((api.state().get("time") or {}).get("isRaining")):
                why = "（今天下雨，作物已经被雨浇过了）"
        except Exception:
            pass
        api.log(f"Nothing to water! {why}本图没有「有作物且没浇过」的格，没用壶、没走位、没耗体力")
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
    no_water = []
    stopped = ""
    for i, g in enumerate(groups, 1):
        x1 = min(p[0] for p in g); x2 = max(p[0] for p in g)
        y1 = min(p[1] for p in g); y2 = max(p[1] for p in g)
        api.log(f"  [{i}/{len(groups)}] 簇 ({x1},{y1})-({x2},{y2}) {len(g)}格")
        # ⚡ 低体力保护（恒 2026-09-24）：**每簇动手前**看一眼——一簇 = 一次蓄力覆盖一大片，
        #    低于线就停在这儿，别为了剩下的几簇把人累趴（已浇的算数，剩下的如实报）。
        _cur, _mx = _stam()
        if _sta_low(_cur):
            _left = sum(len(x) for x in groups[i - 1:])
            stopped = _sta_note(_cur, _mx, f"剩 {_left} 格没浇（第 {i}/{len(groups)} 簇起）")
            api.log("  " + stopped)
            break
        try:
            if not _water_cluster(x1, y1, x2, y2):
                no_water.append(f"({x1},{y1})-({x2},{y2})")
        except Exception as e:
            api.log(f"  ⚠️ 簇 ({x1},{y1})-({x2},{y2}) 失败: {e}")
        time.sleep(0.3)

    # 补浇兜底：重扫漏的格子（初始扫描范围外/打水没赶上的一批），cluster 后再 tool_area 补——不 (已删)/water_area（恒）
    missed = find_unwatered()
    # ⚡ 体力已经停手了就别再补浇（补浇也是一簇一次蓄力，同样是体力）
    if missed and not stopped:
        api.log(f"  ⚠️ 补浇 {len(missed)} 格（cluster 后再 tool_area 蓄力补，不 (已删)/water_area 作弊）")
        for g in cluster(missed, gap=10):
            x1 = min(p[0] for p in g); x2 = max(p[0] for p in g)
            y1 = min(p[1] for p in g); y2 = max(p[1] for p in g)
            try:
                if not _water_cluster(x1, y1, x2, y2):
                    no_water.append(f"({x1},{y1})-({x2},{y2})")
            except Exception:
                api.log(f"    ⚠️ 补浇 ({x1},{y1})-({x2},{y2}) 失败")
        time.sleep(0.3)

    # 验证（真浇了才算）——⚠️ 只剩熟作物/空地不算漏（判据与 C# IsWaterTarget 同一把尺）
    still = find_unwatered()
    if still:
        api.log(f"  ⚠️ 还有 {len(still)} 格没浇上：{still[:6]}{' …' if len(still) > 6 else ''}")
    else:
        api.log("  All watered!")

    s = api.state()
    left, mx = api.watering_can_water()
    api.log(f"Done! {len(groups)} 簇蓄力浇水，覆盖 {len(unwatered)} 格（真走位+蓄力挥壶）")
    api.log(f"  壶里剩水: {left}/{mx}")
    if stopped:
        # ⚡ 停手原因**说在最前**（否则下面那句"还有 N 格干的"会让人以为是水/站位的问题）
        api.log(f"  {stopped}")
        api.log(f"  🔎 下一步：补完体力再叫一次 `farm water`（这次浇过的不会白浇）")
    elif still:
        api.log(f"  🔎 下一步：还有 {len(still)} 格干的——多半是水没打上（上面有「水边跑了…没涨」那条）"
                f"或站位被挡；把壶灌满再叫一次 `farm water` 就行")
    api.log(f"Stamina: {s['player']['stamina']:.0f}/{s['player']['maxStamina']}")
    api.log(f"Time: {s['time']['timeOfDay']}")


if __name__ == "__main__":
    run()
