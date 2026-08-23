"""
🍓 berry_run.py — 摇当前场景所有结果的浆果灌木（树莓/黑莓）

原理（2026-08-17 恒+克劳德实测）：
- 灌木 Bush 在 GameLocation.largeTerrainFeatures（不在 terrainFeatures），surroundings 报 terrain="Bush"
- 真正结果（有莓果可摇）看 tileSheetOffset==1 → surroundings 的 bushBloom=True
- 摇 = checkAction（interact）→ tileSheetOffset 1→0（无果）+ 莓果掉落吸附进包
- 场景里只有季节结果的那几棵能摇（如 Backwoods 11 棵灌木里 3 棵结果）

流程：扫 surroundings 找 bushBloom=True 的灌木 → 逐棵站相邻格+面朝+interact → 重扫直到没有可摇灌木。

用法:
  python berry_run.py                    # 摇当前场景全部结果灌木
  python berry_run.py --port 7842        # 指定端口（solo 恒在 7842）
  python berry_run.py --dry-run          # 只报有几棵可摇，不摇
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[berry] 摇当前场景结果浆果灌木")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--radius", type=int, default=30, help="扫描半径（默认30，覆盖整图小图）")
parser.add_argument("--rounds", type=int, default=4, help="重扫轮数上限（防漏，默认4）")
parser.add_argument("--dry-run", action="store_true", help="只扫不摇")
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
        print(f"[berry] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[berry] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
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

    def scan_blooming():
        """扫当前场景结果灌木列表 [(x,y),...]"""
        try:
            d = requests.get(f"{base}/surroundings", params={"radius": args.radius}, timeout=10).json()
        except Exception:
            return [], ""
        loc = d.get("location", "?")
        bushes = [(t["x"], t["y"]) for t in d.get("tiles", [])
                  if t.get("terrain") == "Bush" and t.get("bushBloom")]
        return bushes, loc

    def find_stand(bx, by, tiles):
        """找灌木相邻可走格（优先下方），避开其他灌木/树。"""
        cands = [(bx, by + 1), (bx + 1, by), (bx, by - 1), (bx - 1, by)]
        blocked = {(bx, by)}
        for t in tiles:
            if t.get("terrain") in ("Bush", "Tree:0", "Tree:1", "Tree:2", "Tree:3",
                                    "Tree:5", "Tree:6", "Tree:7", "Tree:8", "Tree:9", "Tree:10"):
                blocked.add((t["x"], t["y"]))
        passable = {(t["x"], t["y"]) for t in tiles if t.get("passable", True)}
        for c in cands:
            if c in passable and c not in blocked:
                return c
        return cands[0]

    def walk_near(x, y, timeout=20):
        """walk_to 目标格并按位置等到达。"""
        try:
            loc = requests.get(f"{base}/state", timeout=10).json().get("location", {})
            loc_name = loc.get("name") if isinstance(loc, dict) else loc
            r = requests.post(f"{base}/walk_to", json={"location": loc_name, "x": x, "y": y}, timeout=10)
            if not r.json().get("ok"):
                return False
            deadline = time.time() + timeout
            while time.time() < deadline:
                p = requests.get(f"{base}/state", timeout=10).json().get("player", {})
                if abs(p.get("x", 0) - x) <= 1 and abs(p.get("y", 0) - y) <= 1:
                    time.sleep(0.3)
                    return True
                time.sleep(0.25)
            return False
        except Exception:
            return False

    def face_and_shake(bx, by):
        """站格朝灌木方向，interact 摇。"""
        s = requests.get(f"{base}/state", timeout=10).json()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        if py > by: direction = 0   # 站下方 → 朝上
        elif py < by: direction = 2
        elif px < bx: direction = 1
        else: direction = 3
        requests.post(f"{base}/face", json={"direction": direction}, timeout=10)
        time.sleep(0.2)
        requests.post(f"{base}/interact", {}, timeout=10)   # 无参数=面前格=灌木
        time.sleep(0.6)

    # ── 主循环：扫→逐棵摇→重扫 ──
    all_bushes, loc = scan_blooming()
    if not all_bushes:
        log(f"🎉 {loc or '当前场景'}没有结果的浆果灌木")
        return

    log(f"🍓 {loc} 找到 {len(all_bushes)} 棵结果灌木: {all_bushes}")

    if args.dry_run:
        log("--dry-run：不摇")
        return

    total = 0
    shaken = set()
    for _ in range(args.rounds):
        bushes, loc = scan_blooming()
        if not bushes:
            break
        data = requests.get(f"{base}/surroundings", params={"radius": args.radius}, timeout=10).json()
        tiles = data.get("tiles", [])
        for bx, by in bushes:
            if (bx, by) in shaken:
                continue
            stand = find_stand(bx, by, tiles)
            log(f"  · 摇 ({bx},{by}) 站 {stand}")
            if not walk_near(*stand):
                log(f"    ⚠️ 走不到 {stand}，跳过")
                continue
            face_and_shake(bx, by)
            shaken.add((bx, by))
            total += 1
        time.sleep(0.5)

    if total:
        log(f"✅ 摇完 {total} 棵结果灌木，树莓已进背包（掉落吸附）")
    else:
        log("⚠️ 没有摇到任何灌木")


if __name__ == "__main__":
    main()
