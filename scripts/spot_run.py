"""
🪱 spot_run.py — 挖当前场景所有斑点（蚯蚓点 + 远古斑点 + 姜点）

原理（2026-08-17 恒+实测）：
- 蚯蚓点 Artifact Spot = object (O)590：digUpArtifactSpot 挖，出古物/矿物/种子
- 远古斑点 SeedSpot = object (O)SeedSpot"远古斑点"：必须真实锄地（tool_area till）才触发，
  出季节作物种子（春=胡萝卜种子）。till_area 直接改地块/checkAction/digUpArtifactSpot 都挖不了它。
- 姜点 = HoeDirt 上的 forageCrop 类型"2"（crop.forageCrop + whichForageCrop=="2"），
  锄它 crop.hitWithHoe() 出姜。同 tool_area till。surroundings 报 forageCrop="2"。
- 全部靠锄头；AI 背包没锄头就不挖（状态注入也不报）。

流程：扫 surroundings 找 (O)590 + (O)SeedSpot + forageCrop="2" → 检查锄头 → 逐格复用 tool_area till
（脚本自己从 surroundings 算坐标，构造单格矩形，不靠 AI 报坐标）→ 循环到挖完。

用法:
  python spot_run.py                    # 挖当前场景全部斑点
  python spot_run.py --port 7842        # 指定端口
  python spot_run.py --dry-run          # 只报有几点，不挖
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[spot] 挖当前场景蚯蚓点/远古斑点")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--radius", type=int, default=30, help="扫描半径（默认30）")
parser.add_argument("--rounds", type=int, default=4, help="重扫轮数上限（默认4）")
parser.add_argument("--dry-run", action="store_true", help="只扫不挖")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests

NAGI = os.environ["NAGI_URL"]
SPOT_IDS = ("(O)590", "590", "(O)SeedSpot", "SeedSpot")


def log(msg):
    try:
        print(f"[spot] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[spot] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def main():
    try:
        st = requests.get(f"{NAGI}/status", timeout=5).json()
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)
    if not st.get("worldReady"):
        log("❌ 游戏未就绪")
        sys.exit(1)

    base = NAGI

    def has_hoe():
        """背包有没有锄头（任意等级）。"""
        try:
            inv = requests.get(f"{base}/state", timeout=10).json().get("inventory", [])
            return any("Hoe" in (i.get("name") or "") for i in inv)
        except Exception:
            return False

    def scan_spots():
        try:
            d = requests.get(f"{base}/surroundings", params={"radius": args.radius}, timeout=10).json()
        except Exception:
            return [], ""
        loc = d.get("location", "?")
        spots = []
        for t in d.get("tiles", []):
            if t.get("objId") in SPOT_IDS:
                spots.append((t["x"], t["y"], "斑点:" + str(t.get("object", "?"))))
            elif t.get("forageCrop") == "2":   # 🫚 姜点：forageCrop=2，锄出（同 tool_area till）
                spots.append((t["x"], t["y"], "姜"))
        return spots, loc

    def dig_one(x, y):
        """复用耕地 tool_area till 单格（走位+蓄力+挥锄，脚本自动算矩形坐标）。
        返回 (ok, 信息)。"""
        try:
            r = requests.post(f"{base}/tool_area",
                              json={"operation": "till", "x1": x, "y1": y, "x2": x, "y2": y},
                              timeout=40).json()
            return r.get("ok", False), r
        except Exception as e:
            return False, str(e)

    # ── 锄头检查（没锄头不挖）──
    if not has_hoe():
        log("⚠️ 背包没有锄头，跳过斑点挖掘（带锄头再来挖）")
        return

    spots, loc = scan_spots()
    if not spots:
        log(f"🎉 {loc or '当前场景'}没有斑点（蚯蚓点/远古斑点）")
        return

    log(f"🪱 {loc} 找到 {len(spots)} 个斑点: {[(x, y, n) for x, y, n in spots]}")
    if args.dry_run:
        log("--dry-run：不挖")
        return

    done = set()
    total = 0
    for _ in range(args.rounds):
        spots, loc = scan_spots()
        if not spots:
            break
        for x, y, name in spots:
            if (x, y) in done:
                continue
            ok, info = dig_one(x, y)
            if ok:
                log(f"  · 挖 ({x},{y}) {name} ✓")
            else:
                log(f"  · 挖 ({x},{y}) {name} ⚠️ {str(info)[:120]}")
            done.add((x, y))
            total += 1
            time.sleep(0.5)

    if total:
        log(f"✅ 挖完 {total} 个斑点。蚯蚓点出古物/矿物/种子，远古斑点出季节作物种子（掉落吸附进包）")
    else:
        log("⚠️ 没有挖到任何斑点")


if __name__ == "__main__":
    main()
