"""map_bounds_selftest.py — 静态校验 locations.py 的坐标有没有越界(回一格硬化)。

背景(2026-08-31):Farm→BusStop 出口 tile 标 (80,17),但 Farm 地图宽 80(x=0..79),
x=80 超出合法范围 → walk_to 到不了 → "到 BusStop 失败"。根因=Farm→Backwoods 修过的那类
"出口瓦片在地图外多一格"。这次把 MAP_LINKS 所有 warp/portal 的 tile/arrive/stand 全量核查,
越界的一律"回一格"(正值越界→size-1,负值越界 -1→0),并补 arrive 锁定落地。

用法:
    python map_bounds_selftest.py                 # 静态查 MAP_LINKS 越界(不连游戏)
    python map_bounds_selftest.py --refresh       # 连 AI 进程(7843)重走各图刷真实尺寸(dims 随农场地型变)
退出码: 0=无越界, 1=有越界。
"""
import sys, os, json, time

# ── 各图真实尺寸(瓦片数 = DisplayWidth/64)。--refresh 可重扫。Farm 尺寸随农场类型变,其余固定。 ──
MAP_DIMS = {
    "Farm": (80, 65), "BusStop": (65, 30), "Backwoods": (50, 40), "Town": (130, 110),
    "Mountain": (135, 41), "Forest": (120, 120), "Beach": (104, 50), "Railroad": (70, 62),
    "Desert": (50, 60), "IslandSouth": (43, 60), "IslandWest": (110, 110),
    "IslandNorth": (70, 90), "IslandEast": (43, 60), "SkullCave": (16, 10),
    "Greenhouse": (20, 24), "FarmCave": (12, 14),
    # 内室/锁图(2026-08-31 补扫;固定尺寸)
    "WizardHouse": (14, 25), "WizardHouseBasement": (20, 8), "SandyHouse": (20, 10),
    "Sewer": (40, 50), "Tunnel": (40, 15), "WitchWarpCave": (10, 10), "WitchSwamp": (40, 50),
    "WitchHut": (15, 16), "BeachNightMarket": (104, 50), "MermaidHouse": (9, 12),
    "Submarine": (25, 18), "Summit": (20, 30), "Club": (34, 14), "MasteryCave": (15, 14),
    "MovieTheater": (23, 17),
}
AI = "http://localhost:7843"


def _refresh_dims(maps):
    """连游戏,按名 /warp 到每张图读 /state 的 mapWidth/mapHeight(和 /warps 同机制:getLocationFromName)."""
    import urllib.request
    sizes = dict(MAP_DIMS)
    for m in maps:
        try:
            r = urllib.request.Request(AI + "/warp", json.dumps({"location": m, "x": 5, "y": 5}).encode(),
                                       headers={"Content-Type": "application/json"})
            urllib.request.urlopen(r, timeout=30).read()
            time.sleep(0.8)
            st = json.loads(urllib.request.urlopen(urllib.request.Request(AI + "/state"), timeout=30).read())
            loc = st.get("location", {})
            if str(loc.get("name", "")).lower() == m.lower():
                sizes[m] = (loc.get("mapWidth"), loc.get("mapHeight"))
        except Exception as e:
            print(f"  ⚠️ {m}: {e}")
    return sizes


def check(sizes):
    import locations
    hits = []
    for loc, links in locations.MAP_LINKS.items():
        for L in links:
            # tile/stand 是源图坐标; arrive 是目标图坐标 —— 分别按对应图尺寸卡。
            for label, c in (("tile", L.get("tile")), ("stand", L.get("stand")), ("arrive", L.get("arrive"))):
                if c is None:
                    continue
                # portal 用 stand/arrive, tile 是"真瓦片但不可用"的标注(如女巫小屋特意写7,16) → 不算越界。
                if label == "tile" and L.get("kind") == "portal":
                    continue
                # arrive→target 图, tile/stand→源图
                probe = (L.get("target") or loc) if label == "arrive" else loc
                sz = sizes.get(probe)
                x, y = c
                if not sz:
                    hits.append((loc, label, c, "无尺寸(内室/锁图,未验证)"))
                    continue
                w, h = sz
                bad = []
                if x < 0:
                    bad.append(f"x={x}<0(左越界,应回0)")
                elif x >= w:
                    bad.append(f"x={x}>=w{w}(右越界,应回{w-1})")
                if y < 0:
                    bad.append(f"y={y}<0(上越界,应回0)")
                elif y >= h:
                    bad.append(f"y={y}>=h{h}(下越界,应回{h-1})")
                if bad:
                    hits.append((loc, label, c, " & ".join(bad)))
    return hits


def main():
    refresh = "--refresh" in sys.argv
    sizes = _refresh_dims(list(MAP_DIMS)) if refresh else dict(MAP_DIMS)
    hits = check(sizes)
    if not hits:
        print("✅ MAP_LINKS 坐标全部边界内(越界 0 处)")
        return 0
    print(f"⚠️ 发现 {len(hits)} 处越界/未验证坐标:")
    for loc, label, c, why in hits:
        print(f"  [{loc}] {label}{c[0]},{c[1]}  {why}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
