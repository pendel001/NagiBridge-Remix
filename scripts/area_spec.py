"""🍥 区域写法（恒 2026-09-19）：**4 个数 = 矩形，3 个数 = 圆形**。

    parse([50, 20, 70, 30])  ->  {"shape": "rect", "x1":50, "y1":20, "x2":70, "y2":30}
    parse([60, 25, 4])       ->  {"shape": "circle", "cx":60, "cy":25, "r":4}

**为什么要圆形**（恒原话）：
    「我建议 clear_area 也做成圆形区域，而且跟 ai 说清楚选大一点 ——
      因为如果只清扫严格的方形出来耕地，**旁边的杂草生长时很快就会侵入农田把作物顶掉了**。」
    ⇒ 清场时按"田块外扩一圈"的圆来清，四角不留死角；方块田地清完四角还是草窝，
      杂草会从那儿一路长进田里。

⚠️ 只在**个数**上分流（3 vs 4），别的个数**直接报错**——猜错形状会跑去清错地方，
   宁报错别兜底（同 `tree_types.parse_allow` 那条规矩）。
"""

import re


def parse(nums):
    """`[a,b,c]` → 圆（a,b=圆心、c=半径）；`[a,b,c,d]` → 矩形（两角，自动理成左上/右下）。

    个数不对 / 半径非正 → `ValueError`（调用方负责报错退出，别兜底猜）。
    """
    vals = [int(v) for v in nums]
    if len(vals) == 3:
        cx, cy, r = vals
        if r <= 0:
            raise ValueError(f"圆的半径要 > 0，收到 {r}")
        return {"shape": "circle", "cx": cx, "cy": cy, "r": r}
    if len(vals) == 4:
        x1, y1, x2, y2 = vals
        return {"shape": "rect", "x1": min(x1, x2), "y1": min(y1, y2),
                "x2": max(x1, x2), "y2": max(y1, y2)}
    raise ValueError(f"区域要么 4 个数（矩形 x1,y1,x2,y2）、要么 3 个数"
                     f"（圆 圆心x,y,半径），收到 {len(vals)} 个：{vals}")


def parse_str(s):
    """`"50,20,70,30"` / `"60 25 4"` → `parse()`。逗号/空格/中文逗号都认。空串 → ValueError。"""
    parts = [p for p in re.split(r"[\s,，]+", str(s or "").strip()) if p]
    if not parts:
        raise ValueError("区域是空的（要 4 个数=矩形，或 3 个数=圆 圆心x,y,半径）")
    return parse(parts)


def contains(a, x, y):
    """(x,y) 在不在区域里。圆按**格坐标**算：`(x-cx)² + (y-cy)² ≤ r²`。"""
    if a["shape"] == "rect":
        return a["x1"] <= x <= a["x2"] and a["y1"] <= y <= a["y2"]
    return (x - a["cx"]) ** 2 + (y - a["cy"]) ** 2 <= a["r"] ** 2


def bounds(a):
    """外接矩形 `(x1,y1,x2,y2)`（扫描范围/掉落物过滤用）。"""
    if a["shape"] == "rect":
        return (a["x1"], a["y1"], a["x2"], a["y2"])
    return (a["cx"] - a["r"], a["cy"] - a["r"], a["cx"] + a["r"], a["cy"] + a["r"])


def center(a):
    if a["shape"] == "rect":
        return ((a["x1"] + a["x2"]) // 2, (a["y1"] + a["y2"]) // 2)
    return (a["cx"], a["cy"])


def reach(a):
    """从中心到区域**最远格**的曼哈顿距离（拿 `surroundings(半径)` 时够不够用）。"""
    cx, cy = center(a)
    if a["shape"] == "circle":
        return a["r"]
    x1, y1, x2, y2 = bounds(a)
    return max(abs(x - cx) + abs(y - cy) for x in (x1, x2) for y in (y1, y2))


def tiles(a):
    """区域内的**全部格**（外接矩形逐格过 `contains`）。小区域用，别拿它扫大队列。"""
    x1, y1, x2, y2 = bounds(a)
    return [(x, y) for y in range(y1, y2 + 1) for x in range(x1, x2 + 1) if contains(a, x, y)]


def describe(a):
    if a["shape"] == "rect":
        return f"矩形 ({a['x1']},{a['y1']})-({a['x2']},{a['y2']})"
    return f"圆形 圆心({a['cx']},{a['cy']}) 半径{a['r']}（外接 {2 * a['r'] + 1}×{2 * a['r'] + 1}）"
