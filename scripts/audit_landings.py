"""🧭 落点体检：把 `locations.py` 里**所有**落点逐个问游戏「这格站得住吗」。

为什么需要（2026-09-19 恒「要不要多一层保底，微调出入口的落点」）：
  `MAP_LINKS` 的 `tile` 契约是"**出口站格**"，可它的来源是 `/warps`（**warp 触发格**）——
  两者不是一回事。码头 `(17,44)` 就是活教材：站不住 ⇒ `/walk_to` 就近改到 `(20,44)`
  ⇒ 老的 ±2 容差**永远等不到** ⇒ 每次返航白等 25 秒（恒：「就跟超时兜底一样」）。
  几百个落点里还有多少这样的，**没人验过**。

依赖（C#，2026-09-19 同批加的）：`POST /passable {location, x, y}` 的**可选 `location`**
  —— 没有它就只能审"人正在那张图"。

用法：
    python audit_landings.py            # 出报告
    python audit_landings.py --suggest  # 顺带给"站不住的"找最近可站格（多花点时间）
    python audit_landings.py --only MAP_LINKS   # 只审某一类

⚠️ 只读：**只调 /passable，不移动角色、不改任何数据**。
"""
import argparse
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import locations  # noqa: E402

PORT = int(os.environ.get("NAGI_AI_PORT", "7843"))
_cache = {}


def _passable(loc, x, y):
    """问游戏：loc 的 (x,y) 站得住吗。返回 True/False/None(问不到)。"""
    key = (loc, x, y)
    if key in _cache:
        return _cache[key]
    body = json.dumps({"location": loc, "x": int(x), "y": int(y)}).encode()
    req = urllib.request.Request(f"http://localhost:{PORT}/passable", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        r = json.load(urllib.request.urlopen(req, timeout=10))
        v = bool(r.get("passable")) if r.get("ok") else None
    except Exception:
        v = None
    _cache[key] = v
    return v


# ── 落点按「**用途语义**」分三类。**这是本脚本最重要的东西** ──────────────────────────
# 起因（2026-09-19 恒问「体检表有要动的地方吗」）：我跑完 55 条发现问题**根本不是零散错误** ——
#   建议改的格几乎全是 `(x-1,y+1)`/`(x,y+1)`，也就是"**门前那一格**"。因为那几张表标的是
#   「**门格本身**」，不是「**站格**」。**"站不住"对其中绝大多数是设计如此，不是错。**
# ⇒ 这段分类把那次人工判断固化下来，下次跑完直接告诉你"**只需看这 N 条**"。
_CLASS_DOOR = "🚪 门格·设计如此"
_CLASS_STAND = "❗ 站格契约·真可疑"
_CLASS_MANUAL = "❓ 要人工看"


def _classify(cat: str, kind) -> str:
    """给一条落点归属判定「它本该是什么」。"""
    if cat == "BUILDING_DOORS":
        # 坐标语义＝「**门在哪**」（`_enter_building_door` 拿它 `interact_at` **点门**），不是「站哪」。
        # ⛔ **一条都不该按体检建议改** —— 改成"门前一格"会点空、进不了屋。
        return _CLASS_DOOR
    if cat.startswith("MAP_LINKS"):
        if kind == "door":
            return _CLASS_DOOR          # 走位到附近 → 点门瓦片，门格不需要能站
        if cat.endswith(".arrive"):
            return _CLASS_MANUAL        # warp 落点：游戏把人放这儿，站不住才是问题
        if kind in ("warp", "portal"):
            return _CLASS_STAND         # **只有这条是「站格」契约**（码头 (17,44) 就是这类真错）
        return _CLASS_MANUAL
    if cat == "ARRIVE":
        return _CLASS_MANUAL            # 同上：warp 落点
    return _CLASS_MANUAL                # POI：大量是"目标**本身就是那个物件**"（出货箱/柜台/入口）


def _collect():
    """→ `{(map,x,y): [(类别, 标签, 判定), ...]}`。

    ⚠️ **按格去重、但不是丢弃类别**：同一个 (map,x,y) 常常被好几个类目共用
    （实测 39 条 `BUILDING_DOORS` 里有 33 条与别的类目同格）。探针按格去重省调用，
    但报告必须把「**这格是谁在用**」全列出来 —— 否则修的时候不知道该动哪张表。
    """
    out = {}

    def add(cat, label, loc, x, y, kind=None):
        if loc is None or x is None or y is None:
            return
        k = (loc, int(x), int(y))
        out.setdefault(k, []).append((cat, str(label), _classify(cat, kind)))

    for frm, links in locations.MAP_LINKS.items():
        for l in links:
            tgt = l.get("target", "?")
            kd = l.get("kind")
            if l.get("tile"):
                add("MAP_LINKS.tile", f"{frm}→{tgt}", frm, l["tile"][0], l["tile"][1], kd)
            for v in (l.get("via") or []):
                add("MAP_LINKS.via", f"{frm}→{tgt}", frm, v[0], v[1], kd)
            if l.get("stand"):
                add("MAP_LINKS.stand", f"{frm}→{tgt}", frm, l["stand"][0], l["stand"][1], kd)
            if l.get("arrive"):
                add("MAP_LINKS.arrive", f"{frm}→{tgt}", tgt, l["arrive"][0], l["arrive"][1], kd)

    # ⚠️ 形状是 `{'SeedShop': ('Town', (43, 56))}` —— **两层**：(map, (x,y))，别当成 (map,x,y)。
    for loc, v in locations.BUILDING_DOORS.items():
        if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[1], (list, tuple)):
            add("BUILDING_DOORS", str(loc), v[0], v[1][0], v[1][1])

    for loc, v in locations.ARRIVE.items():
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            add("ARRIVE", str(loc), loc, v[0], v[1])

    for name, v in locations.POI.items():
        if isinstance(v, dict) and v.get("map") and v.get("pos"):
            add("POI", str(name), v["map"], v["pos"][0], v["pos"][1])

    return out


def _nearest(loc, x, y, radius=4):
    """在半径内找最近的可站格（游戏自己 /walk_to 用的是同一类判据）。"""
    for r in range(1, radius + 1):
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                if _passable(loc, x + dx, y + dy):
                    return (x + dx, y + dy)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suggest", action="store_true", help="给站不住的找最近可站格")
    ap.add_argument("--only", default="", help="只审某类（如 MAP_LINKS.tile / POI）")
    a = ap.parse_args()

    tiles = _collect()
    if a.only:
        tiles = {k: v for k, v in tiles.items() if any(c.startswith(a.only) for c, _, _ in v)}
    print(f"🧭 落点体检：**{len(tiles)} 个不同的格**（端口 {PORT}）\n")

    bad, unknown, ok = [], [], 0
    for (loc, x, y), owners in sorted(tiles.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
        v = _passable(loc, x, y)
        if v is None:
            unknown.append(((loc, x, y), owners))
        elif not v:
            bad.append(((loc, x, y), owners))
        else:
            ok += 1

    print(f"✅ 站得住：{ok}")
    print(f"❌ 站不住：{len(bad)}")
    print(f"⚠️ 问不到（图名不认/不在顶层地点表）：{len(unknown)}")

    # 📊 按「用途语义」归类 —— **这一段才是结论**（语义见 `_classify` 上面那段）
    real = [b for b in bad if any(cl == _CLASS_STAND for _, _, cl in b[1])]
    door = [b for b in bad if b not in real and any(cl == _CLASS_DOOR for _, _, cl in b[1])]
    manual = [b for b in bad if b not in real and b not in door]
    print('\n📊 站不住的那些按「用途」归类：')
    print(f'    {_CLASS_STAND}：{len(real)}   ← **只有这几条可能要动**')
    print(f'    {_CLASS_DOOR}：{len(door)}   ← ⛔ **一条都别改**（改成「门前一格」会点空门、进不了屋）')
    print(f'    {_CLASS_MANUAL}：{len(manual)}   ← 多半是「目标本身就是那个物件」')

    if bad:
        print('⚠️⚠️ **先读这段再动手** —— 本表只回答「站得住吗」，**不回答「该不该改」**：')
        print('   · `BUILDING_DOORS` 的坐标语义是「**门在哪**」（`_enter_building_door` 拿它 `interact_at` 点门），')
        print('     **不是「站哪」** ⇒ 照本表的建议改成「门前一格」会**点空、进不了屋**。**这类一条都别改。**')
        print('   · `MAP_LINKS` 里 `kind=door` 同理（走位到附近→点门瓦片）。')
        print('   · 只有 `kind=warp` 的 `tile` 才是「**站格**」契约（码头 (17,44)→(20,44) 就是这类真错）。')
        print('   · `POI`/`ARRIVE` 大量是「**目标本身就是那个物件**」（出货箱/柜台/矿洞入口）⇒ 也多半是设计如此。')
        print()
        for title, group in (
            (f"{_CLASS_STAND} —— **只需看这几条**", real),
            (f"{_CLASS_MANUAL} —— 多半是「目标本身就是那个物件」", manual),
            (f"{_CLASS_DOOR} —— ⛔ **别动**（列出来只为留档）", door),
        ):
            if not group:
                continue
            print(f"\n── {title}（{len(group)}）──")
            for (loc, x, y), owners in group:
                sug = ""
                if a.suggest:
                    n = _nearest(loc, x, y)
                    sug = f"  建议 → ({n[0]},{n[1]})" if n else "  （附近 4 格内也没有可站格）"
                print(f"  {loc} ({x},{y}){sug}")
                for c, lb, _cl in owners:
                    print(f"      · {c}  {lb}")

    if unknown:
        print("\n── ⚠️ 问不到的（多半是图名对不上）──")
        for (loc, x, y), owners in unknown[:15]:
            print(f"  {loc} ({x},{y})   ← {owners[0][0]} {owners[0][1]}")

    print(f"\n（探针调用 {len(_cache)} 次，全部只读）")


if __name__ == "__main__":
    main()
