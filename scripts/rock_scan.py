"""
🔍 rock_scan.py — 扫描当前场景"疑似可破物"(室外挖掘目标摸底,只报不敲)

用途(2026-08-29 恒拍板"先扫一遍再定"):
  采石场/姜岛挖掘场 这类室外图要"镐敲"什么,先别猜。本脚本把当前地图里
  所有"格子上有对象"收集起来按名字归类列出来(含 objId/坐标/passable),
  软性标注疑似可破,咱对着真实 /surroundings 数据再定敲什么。

不敲任何东西——只读 /surroundings。想真挖看后续(等目标名确认再补 dig 循环)。

用法:
  python rock_scan.py                    # 扫当前场景(半径30)
  python rock_scan.py --port 7843        # 指定端口(默认7843=恒)
  python rock_scan.py --radius 40        # 扫更远(小图覆盖整图)
  python rock_scan.py --top 200          # 每个名字最多列多少格坐标(默认30,避免刷屏)
"""

import os
import sys
import argparse
from collections import defaultdict

parser = argparse.ArgumentParser(description="[scan] 扫当前场景疑似可破物(只报不敲)")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口(默认7843)")
parser.add_argument("--radius", type=int, default=30, help="扫描半径(默认30)")
parser.add_argument("--top", type=int, default=30, help="每名字最多列多少格坐标(默认30)")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests

NAGI = os.environ["NAGI_URL"]

# 软性"疑似可破"提示(非最终筛选,只帮咱挑):名字命中这些子串就高亮在候选区。
# 外面还有硬物(树桩/藤蔓/装饰)会漏进了"其他"区照列,不会丢。
BREAK_HINTS = (
    "Node", "Stone", "Geode", "Rock", "Boulder", "Bone", "Crystal",
    "Clump", "Meteor", "Bamboo", "Chunk", "Debris", "Slime",
)


def log(msg):
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(msg.encode(enc, errors='replace').decode(enc, errors='replace'), flush=True)
        except Exception:
            pass


def main():
    try:
        st = requests.get(f"{NAGI}/status", timeout=5).json()
    except Exception:
        log("❌ 连不上 NagiBridge /status——游戏开着吗?端口对吗?")
        return
    if not st.get("worldReady"):
        log("⚠️ 世界未加载(还在读档?),等载入后重试。")
        return

    try:
        d = requests.get(f"{NAGI}/surroundings", params={"radius": args.radius}, timeout=10).json()
    except Exception as e:
        log(f"❌ /surroundings 失败: {e}")
        return

    loc = d.get("location", "?")
    locname = loc.get("name", "?") if isinstance(loc, dict) else loc
    tiles = d.get("tiles", [])
    px = (d.get("center") or {}).get("x", "?")
    py = (d.get("center") or {}).get("y", "?")

    log(f"🗺️  [{locname}]  玩家({px},{py})  扫到 {len(tiles)} 格")

    # 按"object 名 + objId"归类;无 object 但 resource=Clump:* 也归入。
    groups = defaultdict(lambda: {"count": 0, "pts": [], "passable": set()})
    for t in tiles:
        name = t.get("object")
        reft = t.get("resource")
        if not name and reft:
            name = f"[{reft}]"  # 大块资源 Clump:46/… (无普通 object 名)
        if not name:
            continue
        g = groups[name]
        g["count"] += 1
        g["objid"] = g.get("objid") or t.get("objId")
        if len(g["pts"]) < args.top:
            g["pts"].append((t.get("x"), t.get("y")))
        g["passable"].add(bool(t.get("passable")))

    if not groups:
        log("  (这张图没有格子上带着对象?)")
        return

    # 分候选区(命中 BREAK_HINTS / Clump) 与 其他区(可能装饰/硬物,照列不丢)
    cand, other = [], []
    for name, g in groups.items():
        hit = name.startswith("[") or any(h in name for h in BREAK_HINTS)
        (cand if hit else other).append((name, g))

    def show(rows):
        if not rows:
            return
        for name, g in rows:
            passable = "可穿" if g["passable"] == {True} else ("不可穿" if g["passable"] == {False} else "混")
            pts = " ".join(f"({x},{y})" for x, y in sorted(g["pts"])[:args.top])
            log(f"  · {name}  objId={g.get('objid') or '?'}  ×{g['count']}  [{passable}]  {pts}")

    cand.sort(key=lambda kv: (-kv[1]["count"], kv[0]))
    other.sort(key=lambda kv: (-kv[1]["count"], kv[0]))

    log(f"\n💥 疑似可破({len(cand)} 类):")
    show(cand)
    log(f"\n🔹 其他带对象格子({len(other)} 类,可能装饰/树/非破):")
    show(other)

    log(f"\n📌 要不要挖/怎么挖——对着上面名字定。想缩小可另加 --radius/看具体格 /dump_tile。")
    log("💡 软标注只是提示(名字含 Node/Stone/Bone/Clump…);硬判定等确认后再进 dig。")


if __name__ == "__main__":
    main()
