"""maze_seg — 迷宫"直线段"分解器：把可走格拆成连续直走廊列表，并拼 P→G 的走法链。

给 AI 一块"棋盘"：保留 walk_to BFS 一键直达，但也让 AI 读全图后自己拼
"左走到(x,y)→上走到(x,y)→右走到(x,y)…"的多段直线链，每段 walk_to 走过去（BFS 只当腿）。

CLI 用法：python scripts/maze_seg.py [port=7843] [gx=63] [gy=16] [radius=28]
对外暴露 compile_chain(walk, cx, cy, gx, gy)：纯逻辑，MCP scene maze seg 与 CLI 共用。
"""
import sys, json, urllib.request
from collections import deque


def compile_chain(walk, cx, cy, gx, gy):
    """在可走格集 walk 上：拆最大横/纵直线走廊 → 建走廊交叉图 → 拼 P(cx,cy)→G(gx,gy) 走法链。

    返回一行摘要 + 走廊列表 + 走法链文本（给 AI 读/走）。walk 是 set[(x,y)] 且含 (cx,cy)/(gx,gy) 附近。
    """
    hor = {}; ver = {}
    # 行扫描（连续可走 → 1 条横向走廊）
    for y in sorted({p[1] for p in walk}):
        xs = sorted(x for (x, yy) in walk if yy == y)
        i = 0
        while i < len(xs):
            j = i
            while j + 1 < len(xs) and xs[j + 1] == xs[j] + 1:
                j += 1
            hor[(y, xs[i], xs[j])] = True
            i = j + 1
    # 列扫描 → 纵向走廊
    for x in sorted({p[0] for p in walk}):
        ys = sorted(y for (xx, y) in walk if xx == x)
        i = 0
        while i < len(ys):
            j = i
            while j + 1 < len(ys) and ys[j + 1] == ys[j] + 1:
                j += 1
            ver[(x, ys[i], ys[j])] = True
            i = j + 1

    # 格 -> run 映射（key_v/key_h 查这个，别查 ver/hor 那是 run 键）
    cellH = {}; cellV = {}
    node_id = {}
    def nid(k):
        if k not in node_id:
            node_id[k] = f"{k[0]}:{k[1]}-{k[2]}"
        return node_id[k]
    for (y, x1, x2) in hor:
        for x in range(x1, x2 + 1):
            cellH[(x, y)] = (y, x1, x2)
    for (x, y1, y2) in ver:
        for y in range(y1, y2 + 1):
            cellV[(x, y)] = (x, y1, y2)
    def key_h(cell): return cellH.get(cell)
    def key_v(cell): return cellV.get(cell)

    # 走廊交叉图：横 <-> 纵 run 共享格则相连
    adjset = {}
    def add(u, v):
        adjset.setdefault(u, set()).add(v)
        adjset.setdefault(v, set()).add(u)
    for cell in walk:
        hk = key_h(cell); vk = key_v(cell)
        if hk is not None and vk is not None:
            add(f"H:{nid(hk)}", f"V:{nid(vk)}")

    def describe(key):
        kind, a, rng = key.split(":", 2)
        lo, hi = rng.split("-")
        if kind == "H":
            return f"横向 y={a}  x={lo}→{hi}  长{int(hi)-int(lo)+1}"
        return f"纵向 x={a}  y={lo}→{hi}  长{int(hi)-int(lo)+1}"

    def nearest_walkable(pt):
        if pt in walk:
            return pt
        best, bd = None, 10 ** 9
        for c in walk:
            d = abs(c[0] - pt[0]) + abs(c[1] - pt[1])
            if d < bd:
                best, bd = c, d
        return best
    start_cell = nearest_walkable((cx, cy))
    end_cell = nearest_walkable((gx, gy))
    def run_of(cell):
        rns = []
        if key_h(cell): rns.append(f"H:{nid(key_h(cell))}")
        if key_v(cell): rns.append(f"V:{nid(key_v(cell))}")
        return rns
    sns = run_of(start_cell); gns = run_of(end_cell)

    out = []
    all_runs = [f"H:{nid(k)}" for k in hor] + [f"V:{nid(k)}" for k in ver]
    shown, total = set(), 0
    for rk in all_runs:
        if rk not in shown:
            shown.add(rk); total += 1
    out.append(f"==== 直线走廊列表（可走路径, 连续格=一段）共{total}条，先列40 ====")
    shown = set()
    for rk in all_runs:
        if rk not in shown:
            shown.add(rk); total += 1
            if total <= 40:
                out.append(f"  {rk} | {describe(rk)}")
    if total > 40:
        out.append(f"  … 共 {total} 条")

    out.append(f"==== 走法链（你=({cx},{cy}) 目标=({gx},{gy}),实际落点=({start_cell})→({end_cell})） ====")
    if not sns or not gns:
        out.append("⚠️ 起/终点未落在可走走廊（目标可能被物体挡/不在视野）。")
        return "\n".join(out)
    pm = path_nodes(sns, gns, adjset)
    if not pm:
        out.append("⚠️ 走廊图不通（起终点不连通）。")
        return "\n".join(out)
    moves = []
    cur_cell = start_cell
    for i, rn in enumerate(pm):
        kind, a, rng = rn.split(":", 2)
        lo, hi = (int(v) for v in rng.split("-"))
        this_cells = [(x, int(a)) for x in range(lo, hi + 1)] if kind == "H" else [(int(a), y) for y in range(lo, hi + 1)]
        if i + 1 < len(pm):
            nk, na, nrng = pm[i + 1].split(":", 2)
            nlo, nhi = (int(v) for v in nrng.split("-"))
            nxt_cells = [(x, int(na)) for x in range(nlo, nhi + 1)] if nk == "H" else [(int(na), y) for y in range(nlo, nhi + 1)]
            junction = next((c for c in this_cells if c in nxt_cells), None) or this_cells[-1]
        else:
            junction = end_cell
        if junction[0] != cur_cell[0]:
            d = "右walk_to" if junction[0] > cur_cell[0] else "左walk_to"
        elif junction[1] != cur_cell[1]:
            d = "下walk_to" if junction[1] > cur_cell[1] else "上walk_to"
        else:
            d = None
        if d:
            moves.append(f"{d}({junction[0]},{junction[1]})")
            cur_cell = junction
    out.append("  ➜ " + (" → ".join(moves) if moves else "(起终点无需移动)"))
    out.append("（每段一条直线走廊；执行时逐段 walk_to，BFS 只当腿。）")
    return "\n".join(out)


def path_nodes(sns, gns, adjset):
    seen = set(sns)
    dq = deque((s, [s]) for s in sns)
    for s in sns:
        if s in gns:
            return [s]
    while dq:
        u, path = dq.popleft()
        for v in adjset.get(u, ()):
            if v not in seen:
                if v in gns:
                    return path + [v]
                seen.add(v)
                dq.append((v, path + [v]))
    return None


def fetch_and_compile(port, gx, gy, radius):
    """CLI/MCP 共用的入口：取 tile（/passable_rect 优先，退回 /surroundings）→ compile_chain。"""
    BASE = f"http://localhost:{port}"
    def get(p):
        with urllib.request.urlopen(BASE + p, timeout=20) as r:
            return json.load(r)
    s = get("/state"); p = s.get("player", {})
    sx, sy = p.get("x"), p.get("y")
    PAD = 20   # ⚠️ 放宽：走法链可能用到起点-目标 bbox 外的走廊（迷宫下方连通段），PAD太小会裁断成"不通"
    minX = min(sx, gx) - PAD; maxX = max(sx, gx) + PAD
    minY = min(sy, gy) - PAD; maxY = max(sy, gy) + PAD
    data, src = None, ""
    try:
        data = get(f"/passable_rect?x1={minX}&y1={minY}&x2={maxX}&y2={maxY}")
    except Exception:
        data = None
    if data and data.get("ok"):
        src = "passable_rect"
        walk = {(t["x"], t["y"]) for t in data.get("tiles", []) if t.get("passable") is True}
    else:
        src = "surroundings(退回)"
        sur = get(f"/surroundings?radius={radius}")
        c = sur.get("center") or {}; cx, cy = c.get("x"), c.get("y")
        rw = {(t["x"], t["y"]) for t in sur.get("tiles", []) if t.get("passable") is False}
        objs = {(t["x"], t["y"]) for t in sur.get("tiles", [])
                if t.get("object") or t.get("terrain") or t.get("resource") or t.get("largeTerrain")}
        walk = set()
        for tx in range(cx - radius, cx + radius + 1):
            for ty in range(cy - radius, cy + radius + 1):
                if (tx, ty) not in rw and (tx, ty) not in objs:
                    walk.add((tx, ty))
        sx, sy = cx, cy
    return src, walk, sx, sy


if __name__ == "__main__":
    PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7843
    GX = int(sys.argv[2]) if len(sys.argv) > 2 else 63
    GY = int(sys.argv[3]) if len(sys.argv) > 3 else 16
    RADIUS = int(sys.argv[4]) if len(sys.argv) > 4 else 28
    src, walk, sx, sy = fetch_and_compile(PORT, GX, GY, RADIUS)
    print(f"[maze_seg] port={PORT} 玩家=({sx},{sy}) 目标=({GX},{GY}) 数据源={src} 可走格={len(walk)}")
    print(compile_chain(walk, sx, sy, GX, GY))
