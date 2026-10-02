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
# 🍓 浆果窗口的判据在 `calendar_data`（**只此一处**：服务器显示侧 + 这儿的执行侧共用）。
#    本脚本**不能** import `nagi_mcp_server`（那会把 MCP 服务器起起来），所以窗口放数据模块里。
import calendar_data

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

    # 🍓🍓 2026-10-02 真机（恒：「**看起来有些不会长树莓的树丛也摇摇了！**」）：
    #    `bushBloom` = 游戏那个 `Bush.tileSheetOffset == 1`，意思是"**贴图切到第 1 帧**"，
    #    **不等于"这丛有果子"**——山地实测：7 棵 `bushBloom=True`，摇了 6 棵，
    #    **背包一件都没多**、摇完那几棵 `bushBloom` 还是 true。
    #    ⇒ 只在**浆果窗口**（春15~18 / 秋8~11，判据在 `calendar_data`，跟显示侧共用）里才摇。
    #    🌰 例外（恒 2026-10-02 拍板）：「**姜岛地图摇晃树丛可以一直放行，直到当前图的金核桃
    #       都被摇掉了**」——姜岛的"核桃丛"也是 `Bush`（`size==4`），**同一个 `tileSheetOffset` 字段**。
    #       ⚠️ 判据**不靠地图名**（那又是一张会烂的名单），直接问游戏：`/nuts` 里还有没有
    #       `kind=="bush"` 且没拿走的 ⇒ 有就一直放行，摇到没有为止（摇完 offset 变 0、重扫自然没了）。
    try:
        _st = requests.get(f"{base}/state", timeout=10).json()
        _t = _st.get("time") or {}
    except Exception:
        _t = {}
    _in_season = calendar_data.in_berry_season(_t.get("season"), _t.get("dayOfMonth"))

    def bush_nuts_left():
        """本图**还挂在树丛上**的金核桃：`/nuts` 里 `kind=="bush"` 且 `taken` 为假。读不到返回 None。"""
        try:
            nn = requests.get(f"{base}/nuts", timeout=10).json() or {}
        except Exception:
            return None
        return [n for n in (nn.get("nuts") or [])
                if n.get("kind") == "bush" and not n.get("taken")]

    _nuts0 = bush_nuts_left()
    _walnut_mode = bool(_nuts0)

    if not _in_season and not _walnut_mode:
        log(f"🌿 {loc} 有 {len(all_bushes)} 丛灌木贴图是「有货」那帧，但**今天不在浆果季**"
            f"（树莓=春15~18 / 黑莓=秋8~11，今天 {_t.get('season')} {_t.get('dayOfMonth')} 日）、"
            f"本图也没有挂着的金核桃 —— **不摇**。那些多半是茶树丛之类（同一帧）。")
        return

    if _walnut_mode:
        log(f"🌰 {loc} 有 {len(_nuts0)} 个**挂着的金核桃丛**{'(顺带也在浆果季)' if _in_season else ''}"
            f" —— 姜岛的树丛一直放行，摇到本图摇干净为止。")

    log(f"🍓 {loc} 找到 {len(all_bushes)} 棵结果灌木: {all_bushes}")

    if args.dry_run:
        log("--dry-run：不摇")
        return

    def _inv_counts():
        """背包按名字计数 —— 用来**回读"到底摇到没有"**（别只说"按键发出去了"）。"""
        try:
            inv = requests.get(f"{base}/state", timeout=10).json().get("inventory") or []
        except Exception:
            return None
        c = {}
        for i in inv:
            if i.get("name"):
                c[i["name"]] = c.get(i["name"], 0) + int(i.get("stack") or 0)
        return c

    _before = _inv_counts()

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

    # ⚠️ 回执必须**回读真值**：原来不管有没有摇到都说「✅ …树莓已进背包」（真机上那是假的）。
    _after = _inv_counts()
    _gain = {}
    if _before is not None and _after is not None:
        for k, v in _after.items():
            d = v - _before.get(k, 0)
            if d > 0:
                _gain[k] = d
    if _gain:
        log(f"✅ 摇了 {total} 棵结果灌木，**背包 +{sum(_gain.values())}**："
            + "、".join(f"{k}×{v}" for k, v in _gain.items()))
    elif _in_season and _before is not None and _after is not None:
        log(f"⚠️ 摇了 {total} 棵，可**背包一件都没多** —— 这些灌木现在并没有可摘的果子"
            f"（`bushBloom` 只是「贴图那一帧」，不等于有货）。别重复摇，白走路。")
    elif _before is None or _after is None:
        log(f"⚠️ 摇了 {total} 棵，但**没能回读背包**（读不到 /state）—— 到底摇到没有我不知道，"
            f"自己看一眼背包")

    # 🌰 **金核桃不进背包**（它是存档计数）⇒ 背包 diff 说不了它的话，得单独回读 `/nuts`。
    #    恒 2026-10-02：「姜岛地图摇晃树丛可以一直放行，**直到当前图的金核桃都被摇掉了**」
    #    ⇒ 回执就得回答"摇掉几个 / 本图还剩几个"。
    if _walnut_mode:
        _nuts1 = bush_nuts_left()
        if _nuts1 is None:
            log("⚠️ 核桃那边**没能回读** `/nuts` —— 本图还剩几个我不知道，自己看一眼")
        elif len(_nuts1) < len(_nuts0):
            log(f"🌰 摇掉 {len(_nuts0) - len(_nuts1)} 个核桃丛，本图**还剩 {len(_nuts1)} 个**没拿"
                + ("（本图摇干净了）" if not _nuts1 else " —— 再调一次 `scene ops=berry` 接着摇"))
        else:
            log(f"⚠️ 核桃丛**一个都没摇掉**（本图还剩 {len(_nuts1)} 个）—— 多半是走不到 / 没对准，"
                f"自己看一眼再决定")


if __name__ == "__main__":
    main()
