# -*- coding: utf-8 -*-
"""⛏️ 探针：摸出**危险矿井**（困难模式）矿节点的真实 objId → 真名表（2026-09-20）

起因：`farm ore=Copper` 在 21 层连刷 3 次「本层无 Copper」。肉眼可见铜矿 ⇒ 扫描判据漏。
已确认：
  - `/surroundings` 把 (O)849 报成 object="Stone"（`SafeObjectName` 只映射 751/290/764/765/767）
  - `/dump_tile` 说 (O)849 真名 = "Copper Stone"（普通矿井的铜是 751 "Copper Node"）
⇒ 危险矿井用的是**另一套节点 ID**。本探针换层扫，把所有被叫 "Stone" 的 objId 逐条问真名，
   凑出完整对照表（补 `SafeObjectName` 的 switch + `mine_run.ORE_NODE_IDS` 都靠它）。
"""
import sys, json, time
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"
FLOORS = [5, 21, 41, 61, 81, 101, 111, 121]


def g(p, **kw):
    return requests.get(f"{AI}{p}", params=kw or None, timeout=20).json()


def post(p, d):
    return requests.post(f"{AI}{p}", json=d, timeout=20).json()


def state():
    s = g("/state")
    return (s.get("location") or {}).get("name"), (s.get("player") or {}).get("x"), (s.get("player") or {}).get("y")


def real_name(x, y):
    try:
        t = g("/dump_tile", x=x, y=y).get("tile") or {}
        o = t.get("object") or {}
        return o.get("name"), o.get("itemId")
    except Exception as e:
        return f"<err {e}>", None


table = {}   # objId -> (真名, 样例坐标, 楼层)

for fl in FLOORS:
    loc = f"UndergroundMine{fl}"
    post("/warp", {"location": loc, "x": 5, "y": 5})
    time.sleep(2.5)
    cur = state()[0]
    if cur != loc:
        print(f"[{fl}] ⚠️ warp 没到（现在 {cur}）——跳过")
        continue
    tiles = g("/surroundings", radius=30).get("tiles") or []
    seen_here = {}
    for t in tiles:
        oid = t.get("objId")
        if oid and oid not in seen_here:
            seen_here[oid] = (t["x"], t["y"], t.get("object"))
    print(f"[{fl}] {cur} 物体种类 {len(seen_here)}")
    for oid, (x, y, reported) in sorted(seen_here.items()):
        key = oid
        if key not in table:
            rn, iid = real_name(x, y)
            table[key] = (rn, reported, f"{cur}({x},{y})")
        rn, rep, where = table[key]
        flag = "  ⭐" if rn and any(k in (rn or "") for k in ("Copper", "Iron", "Gold", "Iridium", "Mystic", "Radioactive")) else ""
        print(f"     {oid:14s} 报={rep!r:12s} 真名={rn!r:20s} {where}{flag}")

print("\n══════ 汇总（objId → 真名）══════")
for oid, (rn, rep, where) in sorted(table.items(), key=lambda kv: kv[0]):
    print(f"  {oid:14s} {str(rn):22s} (surroundings 报 {rep!r})  {where}")
