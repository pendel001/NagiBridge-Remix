"""🌰 walnut_run.py — 姜岛金核桃：把**还没拿**的找出来，埋的挖、丛的摇。2026-09-25

恒：「你打算做姜岛金核桃收集吗？…**挖核桃就是人类受罪然后 AI 也毫无参与感**。
让 AI 也参与进去帮忙找些。AI 应该参与不了弹弓的金核桃，但是**摇树的、挖掘的应该都可以弄到**。」

两类（列表由 `GET /nuts` 给，判据全在 C# 侧从游戏自己身上问 —— 出处见 `ModEntry.cs` 的 `HandleNuts`）：

  · `buried` **埋点** —— `IslandLocation.buriedNutPoints`（每张岛图构造里硬编码的坐标表）。
    **锄头单挥那一格**（⛔ 别走 `/tool_area till` 的蓄力横扫：那是给整块田设计的，
      蓄力落点由挥击动画按 `GetToolLocation()` 算，站位一偏就打空+白烧 18 格体力，
      理由与血泪记录同 `spot_run.py` 的 `dig_one`）。
    拿没拿过 = `/nuts` 的 `taken`（背后是 `netWorldState.FoundBuriedNuts`）。

  · `bush` **核桃丛** —— `Bush` 的 `size==4` 就是核桃丛，`tileSheetOffset==1` = 上面还挂着。
    摇它 = **动作键** `/interact` → `Bush.performUseAction` → `shake()`。
    ⚠️ **别拿工具去砍**：`Bush.performToolAction` 第一行就是 `if (size.Value == 4) return false;`
       —— 砍它一点反应都没有，AI 在那儿会白挥到怀疑人生。

⛔ **本脚本做不到的，如实说**：弹弓那只（`IslandNorth` 的 `TreeNutShot`，要瞄准射）
   和 `IslandHut` 的一次性 `TreeNut` —— 不在这条线里，别让 AI 以为"核桃都能靠这个拿完"。

判据一律**回读 `/nuts`**（那一点 `taken` 翻成 True），**不看工具/端点自报**。

用法：
  python walnut_run.py --max 1            # 做**最近的一个**（默认，AI 平时就这么调）
  python walnut_run.py --max 99           # 这张图能做的都做掉
  python walnut_run.py --radius 9         # 只做 9 格内的（跟状态条那条提示同一个口径）
  python walnut_run.py --dry-run          # 只报还剩哪几个，不动手
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[walnut] 挖/摇当前场景还没拿的金核桃")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--loc", default="", help="扫指定地图（默认=人站的这张）；配 --dry-run 可「不去也能看还剩几个」")
parser.add_argument("--radius", type=int, default=0, help="只做这个半径内的（0=整张图不限）")
parser.add_argument("--max", type=int, default=1, help="最多做几个（默认1=最近那个）")
parser.add_argument("--dry-run", action="store_true", help="只报不做")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests

NAGI = os.environ["NAGI_URL"]
LOC = (args.loc or "").strip()          # 🌰 空 = 人站的这张；给了就扫那张（C# 侧走 /nuts?location=）

# 🥢 四邻站位 → 面朝目标的方向（0上 1右 2下 3左）。**只用正四向**：
#    对角站位要靠 getGeneralDirectionTowards 猜朝向，容易面错格（同 spot_run）。
_STANDBY = ((0, -1, 2), (0, 1, 0), (-1, 0, 1), (1, 0, 3))
_KIND_CN = {"buried": "埋的", "bush": "丛上的"}


def log(msg):
    try:
        print(f"[walnut] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[walnut] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
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

    # ── 读游戏状态的小工具 ────────────────────────────────────────
    def _nuts():
        """`/nuts` 原样返回；读不到返回 None（**别兜底成空列表**——那会报"一个都没有"）。

        ⚠️ 不给 `--loc` 时**必须**由 C# 用 `Game1.currentLocation` —— 想知道"**我脚边**"就不能替它选图。
        """
        try:
            url = f"{base}/nuts" + (f"?location={LOC}" if LOC else "")
            return requests.get(url, timeout=10).json()
        except Exception:
            return None

    def _left(d):
        """这张图还没拿的 [(x,y,kind)]，近的排前面。"""
        if not d or not d.get("ok"):
            return []
        out = [(int(n["x"]), int(n["y"]), str(n.get("kind")))
               for n in (d.get("nuts") or []) if not n.get("taken")]
        return out

    def _taken_now(x, y, kind):
        """回读：那一点翻成 taken 了吗（**判据只用这个**）。"""
        d = _nuts()
        if not d or not d.get("ok"):
            return None                       # 读不到 = 不知道，别谎报成功
        for n in (d.get("nuts") or []):
            if int(n["x"]) == x and int(n["y"]) == y and str(n.get("kind")) == kind:
                return bool(n.get("taken"))
        return None

    def _loc_display(fallback: str = "?") -> str:
        """给人看的地名。

        ⚠️ `/nuts` 报的是 `NameOrUniqueName` —— **小屋室内**那种 instanced 地图会糊上来一串 GUID
        （`FarmHoused2ef3068-6b60-481a-860d-1e23d77dbbe5`），AI 读着难受。
        `/state` 的 `location.name` 是干净的 `Cabin`/`FarmHouse` ⇒ 展示一律用它，读不到才退回。
        （判"是不是姜岛"**不看这个**，只看 `/nuts` 的 `isIsland` —— 游戏自己说的才算数。）
        """
        if LOC:
            return LOC              # 扫的是指定图 → 报那张图的名字（别拿人站的这张糊弄）
        try:
            loc = requests.get(f"{base}/state", timeout=10).json().get("location", {})
            nm = loc.get("name") if isinstance(loc, dict) else loc
            return str(nm) if nm else fallback
        except Exception:
            return fallback

    def _me():
        try:
            p = requests.get(f"{base}/state", timeout=10).json().get("player") or {}
            return (int(p.get("x", -9)), int(p.get("y", -9)))
        except Exception:
            return None

    def _moving():
        try:
            p = requests.get(f"{base}/state", timeout=10).json().get("player") or {}
            return bool(p.get("isMoving"))
        except Exception:
            return False

    def _has_hoe():
        try:
            inv = requests.get(f"{base}/state", timeout=10).json().get("inventory", [])
            return any("Hoe" in (i.get("name") or "") for i in inv)
        except Exception:
            return False

    def _face(direction):
        try:
            requests.post(f"{base}/face", json={"direction": direction}, timeout=8)
            time.sleep(0.2)
            return True
        except Exception:
            return False

    def _snap(sx, sy):
        """原地贴正像素（**只在挥空之后用**：同格看不出位移，但朝向就准了）。"""
        try:
            requests.post(f"{base}/position", json={"x": sx, "y": sy}, timeout=10)
            time.sleep(0.25)
            return True
        except Exception:
            return False

    def _walk_exact(sx, sy):
        """把小人**正好**放到 (sx,sy)：先等它自己走过去，到了才做像素校正。

        ⚠️ `/walk_to` 是**排好路线就返回**，真正走完得按格数等（≈4.8 格/秒）。
        别写死 sleep，也别一到就 `/position` —— 那在肉眼里就是"突然飞走"。
        """
        cur = _me()
        if cur == (sx, sy):
            return True
        try:
            loc = requests.get(f"{base}/state", timeout=10).json().get("location", {})
            loc_name = loc.get("name") if isinstance(loc, dict) else loc
            requests.post(f"{base}/walk_to",
                          json={"x": sx, "y": sy, "location": loc_name}, timeout=20)
        except Exception:
            return False
        cur = _me() or (10 ** 9, 10 ** 9)
        deadline = time.time() + min(25.0, 3.0 + (abs(sx - cur[0]) + abs(sy - cur[1])) * 0.45)
        seen_moving = False
        while time.time() < deadline:
            if _me() == (sx, sy):
                break
            if _moving():
                seen_moving = True
            elif seen_moving:
                break                          # 走起来了又停 = 这一趟结束
            time.sleep(0.25)
        cur = _me() or (10 ** 9, 10 ** 9)
        if abs(cur[0] - sx) + abs(cur[1] - sy) > 1:
            return False                       # 差得远 = 压根没走到，如实失败
        _snap(sx, sy)                          # 贴正
        return _me() == (sx, sy)

    def _stand_and_face(x, y, k=0, snap=False):
        """站到 (x,y) 的四邻之一并面朝它。成功返回站位，失败 None。

        `k` = 重试时换个起点方向（**别在同一个坏站位反复挥**）。
        人在能站的邻格上就原地转身，一步不走。
        """
        cur = _me() or (10 ** 9, 10 ** 9)
        cands = []
        for i in range(4):
            dx, dy, face_dir = _STANDBY[(i + k) % 4]
            sx, sy = x + dx, y + dy
            try:
                if not requests.post(f"{base}/passable", json={"x": sx, "y": sy},
                                     timeout=8).json().get("passable"):
                    continue
            except Exception:
                continue
            cands.append((abs(sx - cur[0]) + abs(sy - cur[1]), sx, sy, face_dir))
        if not cands:
            return None
        cands.sort(key=lambda c: c[0])
        for _, sx, sy, face_dir in cands:
            if (sx, sy) == cur:                # ① 就站在这格上：转身即可
                if snap:
                    _snap(sx, sy)
                return (sx, sy) if _face(face_dir) else None
        for _, sx, sy, face_dir in cands:      # ② 挑最近的那格走一趟
            if _walk_exact(sx, sy) and _face(face_dir):
                return (sx, sy)
        return None

    def do_one(x, y, kind):
        """站到旁边 → 面朝它 → 动手（埋的挥锄 / 丛的摇）→ **回读确认**。"""
        if kind == "buried":
            try:
                requests.post(f"{base}/select", json={"name": "Hoe"}, timeout=8)   # 拿锄
            except Exception:
                pass
        last = ""
        for attempt in range(3):
            stand = _stand_and_face(x, y, k=attempt, snap=(attempt > 0))
            if not stand:
                return False, (last or "四邻") + "：四邻没有能站定的格（水/树/走不过去）"
            last = f"站{stand}"
            try:
                if kind == "buried":
                    requests.post(f"{base}/tool", json={}, timeout=20)         # 单挥，不蓄力
                else:
                    requests.post(f"{base}/interact", json={}, timeout=10)     # 动作键=摇
            except Exception as e:
                return False, f"动手失败: {e}"
            time.sleep(0.7)
            got = _taken_now(x, y, kind)
            if got is True:
                return True, f"{last}拿到了"
            if got is None:
                return False, f"{last}动手了，但 /nuts 读不回来 —— **不知道成没成，别当成功**"
        return False, f"{last} 换了 3 个站位还是没拿到（回读 /nuts 那一点仍是没拿）"

    # ── 开局：这张图有哪些没拿的 ─────────────────────────────────
    d0 = _nuts()
    if d0 is None:
        log("❌ 读不到 /nuts（老 DLL 没这个端点？端点报错？）—— 这次不猜，先别动")
        sys.exit(1)
    if not d0.get("ok"):
        log(f"❌ /nuts 报错：{d0.get('error')}")
        sys.exit(1)

    # ⚠️⚠️ **别信"我要求了哪张图"**：C# 那边要是**没认** `?location=`（老 DLL、拼错、端点被改回去），
    #     它会**安静地扫人站的这张**，而 `_loc_display` 照样显示我们要的那张 ⇒
    #     结果看上去完全正常，其实问错了地图（"工具说成功但事没发生"那一类）。
    #     ⇒ 回包里的 `location` 对不上就直接判**不算数**，宁可报错也别给个漂亮但错的数。
    _got = str(d0.get("location") or "")
    if LOC and LOC.lower() not in _got.lower():
        log(f"⚠️ 我要扫的是 {LOC}，端点回的是 {_got} —— **它没认 `?location=`**（老 DLL？）"
            f"⇒ 这次结果**不算数**，别照它下结论；重启游戏加载新 DLL 再试")
        sys.exit(1)

    loc = _loc_display(d0.get("location", "?"))
    if not d0.get("isIsland"):
        log(f"🏝️ 这里是 {loc}，不是姜岛 —— 金核桃只长在姜岛（IslandWest/North/South/East…）")
        return

    all_left = _left(d0)
    total_left = len(all_left)                       # ⚠️ 收工按**真剩几个**报，别按"试了几次"
    log(f"🌰 {loc}：这张图还剩 {total_left} 个没拿"
        + (f"（全档已收集 {d0.get('walnutsFound')} 个）" if d0.get("walnutsFound", -1) >= 0 else ""))

    if not all_left:
        log("🎉 这张图的金核桃都拿过了")
        return

    me = _me() or (0, 0)
    if LOC:
        # 🗺️ 扫的是**别的图**：只有一枚"还剩几个"有意义 —— 人又不在这张图上，谈不上"走去拿"。
        #    恒 2026-09-25：「现在存档扫得到姜岛吗？没有解锁过。等会儿我给你换全收集的旧档看变化」
        #    ⇒ 这两趟 A/B 就是靠这条。
        # ⚠️ 表头上面**已经打过了**（通用那条），这里别再打一遍 —— 曾经重复输出了一模一样的两行。
        if all_left:
            log("  未拿的点：" + "、".join(f"{_KIND_CN.get(k, k)}({x},{y})" for x, y, k in all_left))
        else:
            log("  🎉 这张图的核桃都拿过了")
        return

    if args.radius > 0:
        # 「附近半径 N 格」跟状态条那条提示同一个口径（切比雪夫：格子是方的，不是圆）
        all_left = [t for t in all_left
                    if max(abs(t[0] - me[0]), abs(t[1] - me[1])) <= args.radius]
        if not all_left:
            log(f"🔍 {args.radius} 格内没有没拿的（这张图别处还有 {total_left} 个，走过去再说）")
            return
    all_left.sort(key=lambda t: abs(t[0] - me[0]) + abs(t[1] - me[1]))
    todo = all_left[:max(1, args.max)]

    log("  待做：" + "、".join(f"{_KIND_CN.get(k, k)}({x},{y})" for x, y, k in todo))

    if args.dry_run:
        log("--dry-run：不动手")
        return

    if any(k == "buried" for _, _, k in todo) and not _has_hoe():
        # ⚠️ 如实拒绝 + **给下一步**（别让 AI 干瞪眼）
        log("⚠️ 背包里没有锄头 —— 埋在地里的金核桃挖不动，"
            "先去把锄头带上（农场的工具箱/家里箱子；`storage` 域能翻箱子）")
        todo = [t for t in todo if t[2] != "buried"]      # 丛上的照样能摇
        if not todo:
            return

    ok_n = 0
    for x, y, kind in todo:
        ok, info = do_one(x, y, kind)
        if ok:
            ok_n += 1
            log(f"  · {_KIND_CN.get(kind, kind)} ({x},{y}) ✓ {info}")
        else:
            log(f"  · {_KIND_CN.get(kind, kind)} ({x},{y}) ⚠️ {info}")
        time.sleep(0.4)

    d1 = _nuts()
    left_now = _left(d1) if d1 else None
    if left_now is None:
        log(f"⚠️ 做了 {ok_n} 个，但收工读不回 /nuts —— 剩几个不知道（别照旧数报）")
        return
    done = total_left - len(left_now)
    log(f"✅ 拿到 {done}/{total_left} 个"
        + (f"；这张图还剩 {len(left_now)} 个：{[(a, b) for a, b, _ in left_now]}"
           if left_now else "，这张图清完了")
        + "。⚠️ 弹弓那只（IslandNorth 的树上的）本脚本拿不了，别在这儿耗")


if __name__ == "__main__":
    main()
