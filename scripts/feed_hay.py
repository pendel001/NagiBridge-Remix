"""
🌾 feed_hay.py — 加干草：筒仓 → 玩家背包 → 喂食台

⚠️ 2026-09-19 恒真机坐实（**这脚本以前从来没真加过草**，一直挂着一个假绿）：

  反编译 `Object.cs:4538-4589` `CheckForActionOnFeedHopper`：
    · `who.ActiveObject != null` → **直接 return false**（手上有东西就不理你）
    · 空手点「饲料槽/Feed Hopper」= 从筒仓抽干草进**玩家背包**
      （一次抽 `min(动物数, 存量)` 个，再受台子余位限制）——**不是**铺到台子上！
  铺台子是**另一条路**（`AnimalHouse.cs:60` 的 checkAction）：
    **手持干草 `(O)178` + 点「Trough」格** → 那一格变成 Hay 物件（恒："应该要手持干草吧？"——对）。

  ⇒ 正确三步：①**空手**点饲料槽把草取进背包 → ②**走过去**、手持干草逐格点 Trough → ③**回读台子**确认。
  （`/interact {x,y}` 其实**没有距离校验**，隔老远也点得着——但恒 2026-09-19 说
   「你都不走路的**啵啵啵**就铺了一路」，铺草是要一格格挪的活儿，所以 `walk_beside()` 先走过去。）
  旧版拿 `/use` 去"放"干草——干草压根不是可放置物（`CanPlaceOnGround` 直接拒），
  于是第一格就 `break` 收工，还印了一句自相矛盾的 `✅ 加完：…喂食台上 0 格干草`。
  （恒当场看见："脚本走到第一格（可以铺的地方）就自己停了"。）

  ⚠️ Trough 格**别再靠偏移猜**（旧版写死"饲料槽右移 2 格再往右"）——
  `/tile_props?scan=Trough` 直接吐游戏自己的真值表（本畜棚是 (8..19, 3) 共 12 格）。

用法:
  python feed_hay.py --port 7843        # 给当前场景所有喂食台加干草（先站在畜棚/鸡舍里）
  python feed_hay.py --dry-run          # 只报筒仓/饲料槽/喂食台状态，不动

判据全程**回读那一格**（`/dump_tile` 看有没有 `(O)178`），不看工具回包。
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
HAY_QID = "(O)178"


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
    return requests.get(f"{NAGI}{ep}", params=params, timeout=15).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=15).json()


def hay_in_inventory():
    s = get("/state")
    return sum(int(i.get("stack", 1)) for i in s.get("inventory", []) if i.get("name") == "Hay")


def tile_object(x, y):
    """回读一格上的物件 id（没有=空字符串）。**判据用它，不用工具回包。**"""
    try:
        t = get("/dump_tile", {"x": x, "y": y}).get("tile") or {}
        return ((t.get("object") or {}).get("qualifiedId")) or ""
    except Exception:
        return ""


def me():
    """我现在的格子 (x,y)；读不到返回 None。"""
    try:
        p = get("/state").get("player") or {}
        return (int(p.get("x", -9)), int(p.get("y", -9)))
    except Exception:
        return None


def walk_beside(loc, tx, ty, timeout=6.0):
    """**走到** (tx,ty) 这一格的旁边（四邻之一）。

    恒 2026-09-19：「你都不走路的**啵啵啵**就铺了一路」—— `/interact {x,y}` 没有距离校验，
    隔老远也能点着，但看着像隔空施法。铺草是要一格格挪的活儿，那就**走过去铺**。
    返回 True=已经在旁边（或走到了）；False=走不过去（会照样点，但调用方要如实提一句）。
    """
    cur = me()
    if cur and max(abs(cur[0] - tx), abs(cur[1] - ty)) <= 1:
        return True                                   # 已经贴着这格了，不用动
    cands = [(tx, ty + 1), (tx, ty - 1), (tx - 1, ty), (tx + 1, ty)]
    if cur:
        cands.sort(key=lambda c: abs(c[0] - cur[0]) + abs(c[1] - cur[1]))
    for sx, sy in cands:
        try:
            if not post("/passable", {"x": sx, "y": sy}).get("passable"):
                continue
        except Exception:
            continue
        try:
            post("/walk_to", {"location": loc, "x": sx, "y": sy})
        except Exception:
            continue
        deadline = time.time() + timeout
        while time.time() < deadline:
            c = me()
            if c and max(abs(c[0] - tx), abs(c[1] - ty)) <= 1:
                return True                           # 贴到槽边了（差半格也算，目的是"走过去"）
            time.sleep(0.2)
    return False


def hold_a_tool():
    """把手里的**物件**放下——选一把工具（Tool 不是 Object ⇒ `ActiveObject` 变 null）。

    ⚠️ 这条是 2026-09-19 读反编译才发现的前置条件：`CheckForActionOnFeedHopper` 第一行就是
    `if (who.ActiveObject != null) return false;` —— 手上还攥着干草去点饲料槽，**它一声不吭**。
    """
    try:
        inv = get("/state").get("inventory", [])
    except Exception:
        return False
    for kw in ("Hoe", "Scythe", "Axe", "Pickaxe", "Watering Can", "Rod"):
        if any(kw.lower() in (i.get("name") or "").lower() for i in inv):
            post("/select", {"name": kw})
            time.sleep(0.25)
            return True
    return False


def main():
    try:
        st = get("/status")
        if not st.get("worldReady"):
            log("❌ 游戏未就绪")
            sys.exit(1)
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)

    # 1) 筒仓状态
    silo = {}
    try:
        silo = get("/silo")
    except Exception as e:
        log(f"⚠️ /silo 不可用: {e}")
    if silo.get("noSilo"):
        log("🌾 还没建筒仓——干草没地方存，建议先建一个")
    elif silo:
        log(f"🌾 筒仓×{silo.get('silos')} | 干草 {silo.get('hay')}/{silo.get('capacity')}"
            f" | 空余 {silo.get('room')}" + (" ⚠️满了" if silo.get("full") else ""))

    # 2) 扫喂食台（Trough 真值表）+ 饲料槽
    s = get("/state")
    loc = (s.get("location") or {}).get("name", "")
    troughs = []
    try:
        for hit in (get("/tile_props", {"scan": "Trough"}).get("hits") or []):
            troughs.append((hit["x"], hit["y"]))
    except Exception as e:
        log(f"⚠️ 扫 Trough 失败: {e}")
    troughs.sort()
    d = get("/surroundings", {"radius": 30})
    hoppers = [(t["x"], t["y"]) for t in d.get("tiles", [])
               if "Feed Hopper" in (t.get("object") or "")]
    log(f"📍 {loc} | 喂食台 {len(troughs)} 格 | 饲料槽 {len(hoppers)} 个")
    if not troughs:
        log("  这图没有喂食台（不在畜棚/鸡舍里？）——先 map go 畜棚/鸡舍 走进去再加")
        return

    empty = [c for c in troughs if tile_object(*c) != HAY_QID]
    if args.dry_run:
        log(f"  空着的喂食台格: {len(empty)}/{len(troughs)}（dry-run 不动）")
        return
    if not empty:
        log("  ✅ 喂食台已经铺满了，不用加")
        return

    # 3) 缺多少草 → 背包不够就**空手**去饲料槽取
    have = hay_in_inventory()
    if have < len(empty) and hoppers:
        if hold_a_tool():
            for hx, hy in hoppers:
                try:
                    post("/interact", {"x": hx, "y": hy})
                except Exception:
                    pass
                time.sleep(0.8)
                have = hay_in_inventory()
                if have > 0:
                    break
            log(f"  🌾 饲料槽取草：背包现在 {have} 个（空手点才给；手上攥着东西它不理你）")

    if have <= 0:
        log("  ⚠️ 背包没有干草、筒仓也是空的 ⇒ 先去割草（镰刀）或玛妮那买干草")
        return

    # 4) 逐格铺：**走过去** → **手持干草** → checkAction 那一格
    placed, failed, walked, reached_far = 0, [], 0, []
    for tx, ty in troughs:
        if hay_in_inventory() <= 0:
            break
        if tile_object(tx, ty) == HAY_QID:
            continue                                   # 这格已经铺过了
        # ⚠️ "走不过去"和"没铺上"是两回事：走不过去照样点得着（`/interact` 没有距离校验），
        #    所以它只记一笔"这格是隔空点的"，**不算失败**（原来混进 failed 里，收工报成
        #    「没铺上的格」，可那两格明明铺上了 —— 又是一句话里自己打自己）。
        if walk_beside(loc, tx, ty):
            walked += 1
        else:
            reached_far.append((tx, ty))
        try:
            post("/select", {"name": "Hay"})
            time.sleep(0.2)
            post("/interact", {"x": tx, "y": ty})
            time.sleep(0.35)
        except Exception as e:
            failed.append((tx, ty, str(e)[:40]))
            continue
        if tile_object(tx, ty) == HAY_QID:             # 判据 = 回读那一格
            placed += 1
        else:
            failed.append((tx, ty, "回读还是空的"))

    # 5) 收工回读（不看过程，只看台子最后长什么样）
    time.sleep(0.4)
    bench = sum(1 for c in troughs if tile_object(*c) == HAY_QID)
    left = hay_in_inventory()
    silo2 = {}
    try:
        silo2 = get("/silo")
    except Exception:
        pass
    tail = f"，筒仓剩 {silo2.get('hay')}" if silo2 and "hay" in silo2 else ""
    if placed:
        extra = f"，另有 {len(reached_far)} 格走不过去（隔空点的）" if reached_far else ""
        log(f"\n✅ 铺上 {placed} 格（其中 {walked} 格是**走过去铺的**）{extra}："
            f"喂食台 {bench}/{len(troughs)} 格有草{tail}，背包剩干草 {left}")
    else:
        log(f"\n❌ 一格都没铺上（喂食台 {bench}/{len(troughs)}{tail}，背包干草 {left}）")
    if failed:
        log(f"   真没铺上的格：{[(a, b) for a, b, _ in failed][:8]}" + ("…" if len(failed) > 8 else ""))
        log("   （那格可能已经被占了 / 手里没干草了；不是喂食台本身的问题）")
    if reached_far and bench:
        log(f"   走不过去的格（照样铺上了，只是站得远）：{reached_far[:8]}")


if __name__ == "__main__":
    main()
