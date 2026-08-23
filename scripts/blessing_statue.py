"""
🗿 blessing_statue.py — 摸农场祝福雕像/矮人雕像，选效果

用途（下矿前）：找到雕像 → 走过去 → 交互（有选项就选）。
祝福雕像每天给随机祝福（摸一次）；如果选项里有"免疫炸弹伤害/防御"类就优先选。

用法:
  python blessing_statue.py --port 7842        # 摸当前场景第一座雕像
  python blessing_statue.py --port 7842 --list # 只列出找到的雕像
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[statue] 摸雕像")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--list", action="store_true", help="只列雕像不摸")
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
        print(f"[statue] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[statue] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def get(ep, params=None):
    return requests.get(f"{NAGI}{ep}", params=params, timeout=10).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=10).json()


def find_statues():
    """扫当前场景找雕像（英文内部名含 Statue）"""
    d = get("/surroundings", {"radius": 30})
    loc = d.get("location", "?")
    statues = []
    for t in d.get("tiles", []):
        obj = t.get("object", "")
        if "Statue" in obj:
            statues.append((t["x"], t["y"], obj))
    return loc, statues


def main():
    try:
        st = get("/status")
        if not st.get("worldReady"):
            log("❌ 游戏未就绪")
            sys.exit(1)
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)

    loc, statues = find_statues()
    if not statues:
        log(f"📍 {loc} | 没找到雕像（可能雕像不在当前场景，或没摆雕像）")
        return
    log(f"📍 {loc} | 找到雕像:")
    for x, y, name in statues:
        log(f"  · {name} ({x},{y})")
    if args.list:
        return

    # 摸第一座（祝福雕像一般一座就够）
    x, y, name = statues[0]
    # 走过去
    post("/walk_to", {"location": loc, "x": x, "y": y + 1})
    time.sleep(1.5)
    post("/position", {"x": x, "y": y + 1})
    time.sleep(0.3)
    post("/face", {"direction": 0})
    time.sleep(0.3)
    r = post("/interact", {})
    time.sleep(1.0)
    log(f"🗿 摸了 {name}")

    # 选效果：读菜单图标选项，优先免疫炸弹（"无法对你造成伤害"）
    try:
        m = get("/menu")
        if m and m.get("open") and m.get("type") == "ChooseFromIconsMenu":
            icons = [b for b in (m.get("buttons") or []) if b.get("hoverText")]
            for b in icons:
                log(f"  选项: {b.get('hoverText')} @ ({b.get('x')},{b.get('y')})")
            # 优先免疫炸弹
            pick = None
            for b in icons:
                ht = (b.get("hoverText") or "")
                if "无法对你造成伤害" in ht or "炸弹" in ht:
                    pick = b
                    break
            if pick is None:
                # 其次找梯子/竖井
                for b in icons:
                    if "梯子" in (b.get("hoverText") or ""):
                        pick = b
                        break
            if pick is None and icons:
                pick = icons[0]
            if pick:
                post("/click", {"x": pick["x"], "y": pick["y"]})
                time.sleep(1.0)
                log(f"✅ 选了: {pick.get('hoverText')}")
            else:
                log("  ⚠️ 没找到可选项")
    except Exception as e:
        log(f"  ⚠️ 选效果失败: {e}")

    log("✅ 摸完雕像")


if __name__ == "__main__":
    main()
