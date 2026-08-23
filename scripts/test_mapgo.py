"""
map_go 全路线实测（2026-08-15，旧档专用）
驱动 MCP map_go 工具逐条跑路线，验证导航到点 + 返回路径，覆盖：
  - warp 双向（室外↔室外，含沙漠回程修复）
  - door 进门（镇/山/森林/沙漠建筑）
  - 多跳（Farm→沙漠→头骨矿洞）
  - 下水道两口 + 变异虫穴
  - 姜岛船路（FishShop→BoatTunnel→IslandSouth）＋返航
用法:
    python scripts/test_mapgo.py                # 全跑（默认打 7842，旧档）
    python scripts/test_mapgo.py --port 7842    # 指定端口
    python scripts/test_mapgo.py --group basic  # basic|doors|island|roundtrip
退出码: 全过=0, 有失败=1
"""
import argparse, io, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import stardew_api
import nagi_mcp_server as M

# 每条: (标签, 目的地, 期望最终地图)
ROUTES = {
    "basic": [
        ("农场→深山", "深山隧道(入口)", "Backwoods"),
        ("深山→农场", "农场上口(→深山)", "Farm"),
        ("农场→巴士站", "巴士站(售票处)", "BusStop"),
        ("巴士站→镇", "镇传送(广场)", "Town"),
        ("镇→森林", "中转(Forest入口)", "Forest"),
        ("森林→农场", "农场下口(→森林)", "Farm"),
        ("镇→海滩", "海滩(入口)", "Beach"),
        ("海滩→镇", "镇传送(左下)", "Town"),
        ("镇→山", "矿洞入口(外)", "Mountain"),
        ("山→铁路", "铁路(入口)", "Railroad"),
        ("铁路→山", "Mountain上口(去铁路)", "Mountain"),
        ("农场→沙漠", "沙漠(巴士站)", "Desert"),
        ("沙漠→农场(回程🔥)", "巴士站(售票处)", "BusStop"),
    ],
    "doors": [
        ("镇→皮埃尔", "皮埃尔商店(入口)", "SeedShop"),
        ("皮埃尔→镇", "镇传送(广场)", "Town"),
        ("镇→医院", "哈维医院(柜台)", "Hospital"),
        ("镇→餐吧", "星之果实餐吧(入口)", "Saloon"),
        ("镇→铁匠铺", "铁匠铺(入口内)", "Blacksmith"),
        ("镇→社区中心", "社区中心(献祭大厅)", "CommunityCenter"),
        ("镇→镇长家", "镇长家(门内)", "ManorHouse"),
        ("镇→博物馆", "博物馆(门内)", "ArchaeologyHouse"),
        ("山→矿井", "矿洞入口(内)", "Mine"),
        ("矿井→山", "矿洞入口(外)", "Mountain"),
        ("山→木匠店", "木匠商店(门口内)", "ScienceHouse"),
        ("山→公会", "探险家公会(内)", "AdventureGuild"),
        ("森林→法师塔", "巫师塔(门内)", "WizardHouse"),
        ("森林→玛妮", "玛妮牧场(门内)", "AnimalShop"),
        ("森林→秘密森林", "秘密森林(入口内)", "Woods"),
        ("沙漠→桑迪", "桑迪商店(门内)", "SandyHouse"),
        ("沙漠→头骨矿洞", "头骨矿洞(内)", "SkullCave"),
        ("农场→洞穴", "农场洞穴(内)", "FarmCave"),
    ],
    "sewer": [
        ("镇→下水道", "科罗布斯商店", "Sewer"),
        ("下水道→镇(井盖🔥)", "镇传送(右下)", "Town"),
        ("镇→下水道→虫穴", "变异虫穴(入口)", "BugLand"),
        ("虫穴→下水道", "科罗布斯商店", "Sewer"),
        ("下水道→森林(管口)", "下水道(出口)", "Forest"),
    ],
    "island": [
        ("海滩→鱼店", "鱼店(门内)", "FishShop"),
        ("鱼店→船坞", "姜岛船坞(售票)", "BoatTunnel"),
        ("船→姜岛码头", "姜岛(码头)", "IslandSouth"),
        ("岛南→岛西农场", "姜岛农场(东入口)", "IslandWest"),
        ("岛西→岛南(桥)", "姜岛(码头)", "IslandSouth"),
        ("岛南→岛北火山区", "火山区域(鹦鹉特快)", "IslandNorth"),
        ("岛北→岛南", "姜岛(码头)", "IslandSouth"),
        ("岛南→返航农场(船)", "巴士站(售票处)", "BusStop"),
    ],
}

GROUP_ORDER = ["basic", "doors", "sewer", "island"]


def run_one(label, dest, expect, api):
    try:
        out = M.map_go(dest)
        st = api.state()
        cur = st.get("location", {}).get("name", "")
        ok = (cur == expect)
        marker = "✅" if ok else "❌"
        print(f"  {marker} {label}: map_go({dest}) → {cur}" + ("" if ok else f" (期望 {expect})"))
        return ok
    except Exception as e:
        print(f"  ❌ {label}: 异常 {e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7842)
    ap.add_argument("--group", default="all", help="all|basic|doors|sewer|island")
    args = ap.parse_args()

    # 强制目标端口（双 solo 档时 detect_roles 会失败：两边都是 host 无 ai）
    #   → 直接把 AI=host=目标端口，让所有工具打这个档
    stardew_api._set_roles(args.port, args.port)
    st = M.api.state()
    loc = st.get("location", {}).get("name")
    tm = st.get("time", {}) or {}
    print(f"🎯 目标端口 {args.port}: {loc} | 年{tm.get('year')} {tm.get('season')}{tm.get('dayOfMonth')}日")
    if not st.get("worldReady"):
        print("⚠️ world 未就绪——确认游戏加载完成")
        sys.exit(1)

    groups = GROUP_ORDER if args.group == "all" else [args.group]
    passed = failed = 0
    for g in groups:
        print(f"\n=== 组: {g} ===")
        for label, dest, expect in ROUTES[g]:
            ok = run_one(label, dest, expect, M.api)
            passed += ok
            failed += (not ok)
            time.sleep(1)  # 冷静一下，别太赶
    print(f"\n结果: ✅ {passed} 过 | ❌ {failed} 失败")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
