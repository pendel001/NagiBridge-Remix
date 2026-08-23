"""
🌾 feed_hay.py — 加干草（从筒仓抽干草铺到饲料槽）

原理：SDV 里对着 Feed Hopper 交互，会把筒仓里的干草抽到喂食台（放一排 Hay 格子）。
如果筒仓没干草，交互无效。要先割草（镰刀）或玛妮那买干草，让筒仓有存货。

用法:
  python feed_hay.py --port 7842        # 给当前场景所有饲料槽加干草
  python feed_hay.py --dry-run          # 只报筒仓/饲料槽状态

⚠️ 需 NagiBridge 新 DLL（有 /silo 端点）；跑之前游戏窗口前台。
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[feed] 加干草")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--dry-run", action="store_true", help="只报不操作")
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
        print(f"[feed] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[feed] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def get(ep, params=None):
    return requests.get(f"{NAGI}{ep}", params=params, timeout=10).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=10).json()


def silo_status():
    r = get("/silo")
    if not r.get("ok"):
        return None, r.get("error")
    return r, None


def main():
    try:
        st = get("/status")
        if not st.get("worldReady"):
            log("❌ 游戏未就绪")
            sys.exit(1)
    except Exception as e:
        log(f"❌ 连不上游戏（/silo 需新 DLL？）: {e}")
        sys.exit(1)

    # 1) 筒仓状态
    silo, err = silo_status()
    if err:
        log(f"⚠️ /silo 不可用: {err}（需要 NagiBridge 新 DLL）")
        silo = None
    elif silo.get("noSilo"):
        log("🌾 还没建筒仓——干草没地方存，建议先建一个")
    else:
        log(f"🌾 筒仓×{silo.get('silos')} | 干草 {silo.get('hay')}/{silo.get('capacity')}"
            f" | 空余 {silo.get('room')}" + (" ⚠️满了" if silo.get("full") else ""))
        if silo.get("hay", 0) <= 0:
            log("  ⚠️ 筒仓没干草！先去割草（镰刀割草自动进筒仓）或玛妮那买干草")

    # 2) 扫饲料槽
    s = get("/state")
    loc = s.get("location", {}).get("name", "")
    d = get("/surroundings", {"radius": 30})
    hoppers = []
    for t in d.get("tiles", []):
        if "Feed Hopper" in (t.get("object") or ""):
            hoppers.append((t["x"], t["y"]))
    log(f"📍 {loc} | 饲料槽 {len(hoppers)} 个")
    if not hoppers:
        log("  没有饲料槽（不在鸡舍/畜棚？）")
        return
    if args.dry_run:
        return

    # 3) 逐个饲料槽交互 → 拿缺的干草 → 铺到喂食台
    added = 0
    for hx, hy in hoppers:
        # 走到饲料槽旁
        try:
            post("/walk_to", {"location": loc, "x": hx, "y": hy + 1})
            deadline = time.time() + 15
            while time.time() < deadline:
                p = get("/state").get("player", {})
                if abs(p.get("x", 0) - hx) <= 1 and abs(p.get("y", 0) - (hy + 1)) <= 1:
                    break
                time.sleep(0.25)
        except Exception:
            pass
        # 面向饲料槽（上=0）交互 → 干草进背包
        post("/face", {"direction": 0})
        time.sleep(0.25)
        post("/interact", {})
        time.sleep(0.8)
        added += 1
        log(f"  🌾 饲料槽 ({hx},{hy}) 交互，干草进背包")

    # 4) 把背包里的干草铺到喂食台（沿饲料槽那一行，两个方向探）
    def hay_inv():
        s = get("/state")
        return sum(i.get("stack", 1) for i in s.get("inventory", []) if i.get("name") == "Hay")

    def place_hay(px, py, face_dir):
        post("/select", {"name": "Hay"})
        time.sleep(0.2)
        post("/position", {"x": px, "y": py})
        time.sleep(0.2)
        post("/face", {"direction": face_dir})
        time.sleep(0.15)
        r = post("/use", {})
        time.sleep(0.25)
        return r.get("ok") and r.get("action") == "placed"

    if hay_inv() > 0:
        log("  🧹 开始铺干草到喂食台…")
        for hx, hy in hoppers:
            # 规则（user确认）：第一格 = 饲料槽右移 2 格 (hx+2)，然后 +1 往右铺；
            # 取到几格铺几格，铺完单次取的干草为止（适配不同畜棚长度）
            x = hx + 2
            while hay_inv() > 0:
                if place_hay(x, hy + 1, 0):
                    x += 1
                    time.sleep(0.15)
                else:
                    break  # 铺不下了（到墙/尽头）
        log(f"  铺完，背包剩余干草 {hay_inv()}")

    # 5) 复查
    time.sleep(0.5)
    silo2, _ = silo_status()
    d = get("/surroundings", {"radius": 30})
    bench_hay = sum(1 for t in d.get("tiles", []) if t.get("object") == "Hay")
    if silo2 and "hay" in silo2:
        log(f"\n✅ 加完：筒仓剩余 {silo2.get('hay')}/240，喂食台上 {bench_hay} 格干草")
    else:
        log(f"\n✅ 加完（{added} 个饲料槽，喂食台 {bench_hay} 格）")


if __name__ == "__main__":
    main()
