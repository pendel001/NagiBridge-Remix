"""
🗑️ trash_run.py — 翻垃圾桶刮刮乐（傻瓜版：记坐标,每处 position→interact→等掉落进包）

原理（2026-08-24 反编译实测）：
  - 垃圾桶【不是】loc.objects，是地图瓦片 Action 属性 "Garbage <id>" → GameLocation.CheckGarbage(id,tile,who)。
    /surroundings、/dump_tile 都看不到它，翻它 = 对桶瓦片 checkAction（现有 /interact?x=&y= 直接就是）。
    2026-08-24 实测 interact 52,63 → actionTriggered:true 真翻了，邻居瓦片全 false。
  - 每天每桶 1 次（CheckedGarbage）;掉物= dailyLuck+每桶确定性RNG,空翻正常;钓技 Salvager perk 强化。
  - ID 在桶瓦片自己的 Action 属性里，工具只需坐标。

流程（傻瓜）：逐桶 → /position 到桶旁可站格 → /interact 桶瓦片 → sleep wait 等掉落物自行进包 → 报掉物。

用法:
  python trash_run.py --dry-run                 # 只报桶不翻
  python trash_run.py --loc Town                # 只翻 Town 的桶（默认翻全场预设）
  python trash_run.py --pos "Town:52,63|Town:40,76"  # 临时指定追加坐标
  python trash_run.py --port 7842 --wait 1.0    # 端口/停留秒
"""

import os
import sys
import time
import argparse

# ── 桶清单：场景名 → [(x,y)]。坐标是桶【瓦片】(Action="Garbage <id>"，passable:false)。 ──
# ✅ 2026-08-24 /scan 枚举 Town 全部 8 桶（另有 /scan 自动发现，优先用,这里当兜底）。
TRASH_PRESET = {
    "Town": [
        (52, 63),   # Evelyn(Alex家)  ✅ 实测交互真翻
        (13, 86),   # JodiAndKent     1 Willow Ln
        (19, 89),   # EmilyAndHaley   2 Willow Ln
        (47, 70),   # Saloon          酒馆
        (56, 85),   # Mayor           镇长家
        (97, 80),   # Blacksmith      铁匠铺
        (108, 91),  # Museum          博物馆
        (110, 56),  # JojaMart        影院/Joja
    ],
}

# 桶旁站格偏好顺序：南,北,东,西（桶通常贴墙，站它对面那侧）
_DIRS = [(0, 1), (0, -1), (1, 0), (-1, 0)]

parser = argparse.ArgumentParser(description="[trash] 翻垃圾桶刮刮乐（傻瓜版）")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--loc", type=str, default=None, help="只翻该场景的桶（默认全场预设）")
parser.add_argument("--pos", type=str, default=None, help="临时坐标 场景:x,y|场景:x,y（追加,不进预设）")
parser.add_argument("--dry-run", action="store_true", help="只报桶不翻")
parser.add_argument("--wait", type=float, default=1.0, help="翻后停留秒(等掉落物进包,默认1.0)")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests

NAGI = os.environ["NAGI_URL"]


def log(msg):
    try:
        print(f"[trash] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[trash] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def get(ep, params=None):
    return requests.get(f"{NAGI}{ep}", params=params, timeout=12).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=12).json()


def bag():
    """背包物品名→数量，用于翻桶前后 diff 报掉物。"""
    d = {}
    for i in get("/state").get("inventory", []):
        d[i.get("name", "?")] = d.get(i.get("name", "?"), 0) + 1
    return d


def stand_tile(can_x, can_y):
    """桶旁第一个可站格（南优先）。用 /dump_tile 判断（/surroundings 的 x,y 参数被忽略、始终以玩家为中心，
    查远处桶会误判，2026-08-24；dump_tile 才接受任意 x,y 且报 passable）。桶/墙 passable:false 自动排除。"""
    for dx, dy in _DIRS:
        sx, sy = can_x + dx, can_y + dy
        t = get("/dump_tile", {"x": sx, "y": sy})
        if t.get("ok") and t.get("tile", {}).get("passable"):
            return (sx, sy)
    return None


def rummage_one(x, y, gid=""):
    """傻瓜翻单桶：position → interact → 等掉落进包 → 报结果。gid=桶 ID（来自 /scan）。"""
    tag = f"({x},{y})" + (f"[{gid}]" if gid else "")
    stand = stand_tile(x, y)
    if stand is None:
        return f"⚠️ {tag} 周围无可站格，跳过"
    before = bag()
    post("/position", {"x": stand[0], "y": stand[1]})   # 直接站桶旁（傻瓜式,不走位）
    time.sleep(0.3)
    r = post("/interact", {"x": x, "y": y})             # 对桶瓦片 checkAction→CheckGarbage
    time.sleep(args.wait)                               # 等掉落物飞进包
    after = bag()
    if not r.get("actionTriggered"):
        return f"❌ {tag} 没触发（可能不是桶/已翻过）"
    gains = {n: after[n] - before.get(n, 0) for n in after if after[n] > before.get(n, 0)}
    if gains:
        return f"🎁 {tag} 翻出：" + ",".join(f"{n}×{c}" for n, c in sorted(gains.items()))
    return f"🫙 {tag} 翻了，空的"


def auto_cans():
    """自动发现当前场景的垃圾桶：读 /scan（枚举所有 Action 瓦片）→ 过滤 "Garbage" 前缀。
    返回 [(loc,x,y,id)]；/scan 不可用或无桶 → []。"""
    try:
        d = get("/scan")
    except Exception:
        return []
    if not d.get("ok"):
        return []
    loc = d.get("location")
    out = []
    for a in d.get("actions", []):
        act = str(a.get("action", ""))
        if act.upper().startswith("GARBAGE"):
            gid = act.split(None, 1)[-1] if " " in act else act
            out.append((loc, int(a["x"]), int(a["y"]), gid))
    return out


def build_positions():
    """优先 /scan 自动发现（--loc 过滤），无则用预设；--pos 追加。返回 [(loc,x,y,id)]。"""
    lst = []
    scanned = auto_cans() if not args.pos else []
    if scanned:
        lst.extend((l, x, y, gid) for l, x, y, gid in scanned
                   if (not args.loc) or l == args.loc)
    else:
        for name, cans in TRASH_PRESET.items():
            if args.loc and name != args.loc:
                continue
            lst.extend((name, x, y, "") for x, y in cans)
    if args.pos:
        for seg in args.pos.split("|"):
            seg = seg.strip()
            if not seg:
                continue
            name, _, xy = seg.partition(":")
            try:
                x, y = xy.split(",")
                lst.append((name.strip(), int(x), int(y), ""))
            except Exception:
                log(f"⚠️ 无法解析 '{seg}'（格式 场景:x,y）")
    return lst


def main():
    cur = get("/state").get("location", {}).get("name")
    log(f"当前场景：{cur}")
    cans = build_positions()
    if not cans:
        log("无桶坐标。补 TRASH_PRESET 或用 --pos。")
        return
    log(f"待翻 {len(cans)} 个桶")
    for loc, x, y, gid in cans:
        if get("/state").get("location", {}).get("name") != loc:
            log(f"⏭️ {loc}({x},{y}) 在 {loc}，AI 不在，跳过（先 map_go 过去）")
            continue
        if args.dry_run:
            log(f"  (dry) {loc}({x},{y})" + (f"[{gid}]" if gid else ""))
            continue
        log(rummage_one(x, y, gid))
    log("完成 ✅")


if __name__ == "__main__":
    main()
