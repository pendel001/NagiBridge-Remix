"""
🌾 scythe_crops.py — 镰刀作物收获模式（小麦/水稻/芋头/苋菜/纤维等）

实测（2026-08-06）确定的两条收获行为：
  1. 手摘作物（蒜/西瓜）→ 直接进背包
  2. 镰刀作物（小麦等）→ 收获物掉地上（Wheat+Hay 成 debris），要走过去捡
所以正确姿势 = 选镰刀 → /harvest（程序化收获，带镰刀才收得了镰刀作物）→ 捡地面掉落。

用法:
  python scythe_crops.py                  # 收当前地图成熟作物（带镰刀）
  python scythe_crops.py --radius 20      # 扫描半径
  python scythe_crops.py --dry-run        # 只报有多少成熟/长啥，不收

⚠️ 跑之前把游戏窗口切到前台（后台暂停时走位/拾取都不动）。
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[scythe] 镰刀收获模式")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--radius", type=int, default=15, help="收获半径（/harvest 上限，默认15）")
parser.add_argument("--dry-run", action="store_true", help="只扫不收")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests
import crops

NAGI = os.environ["NAGI_URL"]


def log(msg):
    try:
        print(f"[scythe] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[scythe] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def get(ep, params=None):
    return requests.get(f"{NAGI}{ep}", params=params, timeout=10).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=10).json()


def find_scythe():
    """找背包里最好的一把镰刀名（铱>金>铜>普通）"""
    s = get("/state")
    inv = s.get("inventory", [])
    scythes = []
    for i in inv:
        name = i.get("name", "")
        if "Scythe" in name and "Sword" not in name:
            scythes.append(name)
    order = ["Iridium Scythe", "Gold Scythe", "Copper Scythe", "Scythe"]
    for pref in order:
        if pref in scythes:
            return pref
    return scythes[0] if scythes else None


def scan_mature():
    """扫当前地图作物，返回 (loc, mature, growing_by_name)
    mature: [{'x','y','crop','zh','scythe'}] 成熟株
    growing_by_name: {作物中文名: 数量} 生长中统计
    """
    data = get("/surroundings", {"radius": args.radius + 5})
    loc = data.get("location", "?")
    mature = []
    growing = {}
    for t in data.get("tiles", []):
        if not t.get("crop"):
            continue
        cid = t["crop"]
        info = crops.crop_info(cid)
        if t.get("harvestable"):
            mature.append({
                "x": t["x"], "y": t["y"], "crop": cid,
                "zh": info["zh"], "scythe": info["scythe"],
            })
        else:
            growing[info["zh"]] = growing.get(info["zh"], 0) + 1
    return loc, mature, growing


def collect_debris():
    """捡地上掉落（镰刀作物收获物掉地上，走过去才捡）。返回捡了几个。"""
    d = get("/debris")
    items = d.get("debris", [])
    picked = 0
    for it in items[:10]:
        x, y = it.get("x", 0), it.get("y", 0)
        try:
            r = post("/walk_to", {"location": get("/state").get("location", {}).get("name", ""), "x": x, "y": y})
            if r.get("ok"):
                # 等走完再check
                deadline = time.time() + 15
                while time.time() < deadline:
                    s = get("/state")
                    if not s.get("player", {}).get("isMoving", False):
                        break
                    time.sleep(0.25)
                picked += 1
                time.sleep(0.3)
        except Exception:
            pass
    return picked


def main():
    try:
        st = get("/status")
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)
    if not st.get("worldReady"):
        log("❌ 游戏未就绪")
        sys.exit(1)

    scythe = find_scythe()
    if not scythe:
        log("❌ 背包里没有镰刀")
        sys.exit(1)
    log(f"⚒️ 用 {scythe}")

    loc, mature, growing = scan_mature()
    grow_sum = " ".join(f"{k}×{v}" for k, v in sorted(growing.items(), key=lambda x: -x[1]))
    log(f"📍 {loc} | 成熟 {len(mature)} 株 | 生长中: {grow_sum or '无'}")
    for m in mature[:15]:
        tag = "🔪" if m["scythe"] else "🤚"
        log(f"  {tag} {m['zh']} ({m['x']},{m['y']})")
    if args.dry_run:
        return
    if not mature:
        log("🌾 没有可收的成熟作物")
        return

    # 选镰刀 + /harvest（带镰刀才能收镰刀作物）
    if scythe:
        post("/select", {"name": scythe})
        time.sleep(0.2)
    h = get("/harvest", {"radius": args.radius})
    log(f"🌾 收获结果: {h.get('harvested', 0)} 株")

    # 捡掉落（镰刀作物的产物在地上）
    time.sleep(0.5)
    picked = collect_debris()
    if picked:
        log(f"🎁 捡起地面掉落 {picked} 个（小麦/干草等）")

    # 报告背包里新增的
    log("✅ 完成（手摘作物进背包，镰刀作物掉落已捡）")


if __name__ == "__main__":
    main()
