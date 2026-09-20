# -*- coding: utf-8 -*-
"""🪨 只读探针：验证 `IsTilePassable` 现在认 `resourceClumps`（2026-09-20）

**为什么要有它**：这次改的是寻路那把**唯一的尺子**（`ModEntry.cs` 的 `IsTilePassable`）——
以前它**一个字都不查资源堆**（反编译 `GameLocation.isTilePassable` 只查 Back 层 `Passable`
属性 + Buildings 层实心块），而走位是**直接改 `farmer.Position`** ⇒ 尺子说能走，人真穿过去。
改完的判据是 `ResourceClump.occupiesTile(x,y)`。

**它证明什么**：站在有大石头的存档里，`/passable_rect` 报的 `passable` 对**大石头占的每一格**
都该是 `False`；而 `resource` 字段（**与 passable 无关、单独算的**）应当照旧报出
`LargeBoulder`/`LargeStump`。
⇒ **两个字段一个"认出是什么"、一个"说能不能走"**：`resource` 有值 + `passable=False` = 对上了。
   修之前会是 `resource=LargeBoulder` 但 `passable=True`（正是"看得见、照样踩"）。

用法:
    python _clump_passable_probe.py                  # 当前地点，整图
    python _clump_passable_probe.py 0 0 80 65        # 指定矩形
    python _clump_passable_probe.py 0 0 80 65 --loc Farm   # 指定地点（不必人在那）

⚠️ 只读：**不 warp、不动角色、不碰游戏状态**（`/passable_rect` 是纯读端点）。
"""
import sys
import requests

BASE = "http://localhost:7843"          # AI(轮回) 端口
LIMIT = 96                              # 端点硬上限：maxX-minX 超过会被**静默截断**


def g(path, **kw):
    return requests.get(f"{BASE}{path}", params=kw or None, timeout=30).json()


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    loc = None
    if "--loc" in sys.argv:
        loc = sys.argv[sys.argv.index("--loc") + 1]

    st = g("/state")
    if not st.get("worldReady"):
        print("❌ 世界没就绪（worldReady=False）——先把游戏跑起来")
        return 2
    here = (st.get("location") or {}).get("name")
    who = (st.get("player") or {}).get("name")
    print(f"🌍 当前 {who}@{here}  时间 {(st.get('timeOfDay'))}")

    if len(argv) >= 4:
        x1, y1, x2, y2 = (int(v) for v in argv[:4])
        where = f"指定矩形 {x1},{y1} → {x2},{y2}"
    else:
        x1, y1, x2, y2 = 0, 0, 80, 65
        where = "默认整图 0,0 → 80,65（没给矩形）"
    if (x2 - x1) > LIMIT or (y2 - y1) > LIMIT:
        print(f"⚠️ 矩形超过端点上限 {LIMIT}×{LIMIT} —— 端点会**静默截断**，下面的结果只覆盖左上角一块")
    print(f"📐 {where}" + (f"  loc={loc}" if loc else ""))

    kw = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if loc:
        kw["loc"] = loc
    r = g("/passable_rect", **kw)
    if not r.get("ok"):
        print(f"❌ /passable_rect 报错：{r.get('error')}")
        return 1
    tiles = r.get("tiles") or []
    print(f"   扫到 {len(tiles)} 格（location={r.get('location')}）\n")

    clump = [t for t in tiles if t.get("resource")]
    if not clump:
        print("🕳️ 这片区域**一件资源堆都没有** —— 本探针的前提不成立。")
        print("   ⇒ 换个有大石头的存档/区域再跑；**别把'没东西可测'记成'测过了'**。")
        return 3

    by_name = {}
    for t in clump:
        by_name.setdefault(t["resource"], []).append(t)

    bad = []
    print("═══ 资源堆逐件体检（`resource` 有没有认出来 × `passable` 说能不能走）═══")
    for name, ts in sorted(by_name.items()):
        xs = [t["x"] for t in ts]
        ys = [t["y"] for t in ts]
        walk_ok = [t for t in ts if t.get("passable")]
        print(f"  🪨 {name}: {len(ts)} 格  x∈[{min(xs)},{max(xs)}] y∈[{min(ys)},{max(ys)}]")
        for t in ts:
            mark = "❌ 说能走" if t.get("passable") else "✅ 说不能走"
            print(f"       ({t['x']:>3},{t['y']:>3})  {mark}   resource={t['resource']}")
        bad += walk_ok

    print("\n═══ 判定 ═══")
    if bad:
        print(f"❌ **{len(bad)} 格资源堆仍被尺子判成可走** —— 修复没生效，逐条：")
        for t in bad:
            print(f"     ({t['x']},{t['y']}) {t['resource']}")
        print("   ⇒ 先确认两边 DLL 的 md5 一致、且**游戏重启过**（改的是 C#，不重启加载的是旧库）。")
        return 1
    print(f"✅ 全部 {len(clump)} 格资源堆都报 `passable=False` —— 尺子认它们了。")
    print("   （`resource` 字段照旧报得出名字，说明是**新加的一道关卡**，不是把老判据改坏了。）")
    print("   ⚠️ 这只证明**尺子**；「人真的绕开而不是撞上」要看走位实测（`walk_to` 穿过大石头来回）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
