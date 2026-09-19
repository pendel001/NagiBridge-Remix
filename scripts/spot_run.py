"""
🪱 spot_run.py — 挖当前场景所有斑点（蚯蚓点 + 远古斑点 + 姜点）

原理（2026-08-17 恒+实测；**2026-09-19 反编译订正**）：
- 两种斑点，**中文显示名都叫「远古斑点」**（别按中文名分，看内部 id）：
    · `(O)590` 内部名 **Artifact Spot** —— 宝藏图腾撒的就是它（`Object.cs:3265`）
    · `(O)SeedSpot` 内部名 **Seed Spot** —— 地图随机/山湖那种（`GameLocation.cs:15233`、
      `Mountain.cs:272`）。⚠️ 工程里老把它俩混着叫，跟恒对话时**说内部名**最保险。
  **两者挖法相同、而且那是唯一一条路**：`Hoe.DoFunction` → 锄头挥中物件 →
  `Object.performToolAction(Hoe)`（`Object.cs:1310-1337`）→ `digUpArtifactSpot(...)`
  + `makeHoeDirt(ignoreChecks: true)` + `objects.Remove(...)`。
  ⚠️ **在此之前这个脚本一个斑点都挖不动**：`/tool_area` 的 till 过滤是
  「格上有物件 ⇒ 整格跳过」（防锄到箱子/洒水器），可**斑点自己就是那个物件**
  ⇒ 恒真机叫出的 15 个斑点全报 `No diggable tiles nearby`。C# 侧已加 `IsDiggableSpot`
  例外（斑点格放行，另走 `isTilePassable`）；**这是 C# 改动，要重启游戏才生效**。
- 姜点 = HoeDirt 上的 forageCrop 类型"2"（crop.forageCrop + whichForageCrop=="2"），
  锄它 `Crop.hitWithHoe()`（`Crop.cs:470-482`）出姜 `(O)829`，作物没了、HoeDirt 留着。
  ⚠️ 它卡的是**第二道门**：`IsTileBlockedBy` 连"可通行的 HoeDirt"也算挡路
  （我们传 `ignorePassables=None` ⇒ `GameLocation.cs:7405` 前半句恒真）——
  同期加了 `IsGingerTile` 例外。surroundings 报 forageCrop="2"。
- 全部靠锄头；AI 背包没锄头就不挖（状态注入也不报）。

流程：扫 surroundings 找 (O)590 + (O)SeedSpot + forageCrop="2" → 检查锄头 →
**逐格：走到四邻之一 → 面朝它 → `/tool` 挥一下（不蓄力）→ 回读那格确认**（脚本自己算坐标，
不靠 AI 报）→ 循环到扫不出斑点为止。判据是**回读游戏状态**，不是工具回包的 ok。

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

    # 🥢 四邻站位 → 面朝目标的方向（0上1右2下3左）。**只用正四向**：
    #    对角站位要靠 getGeneralDirectionTowards 猜朝向，容易面错格。
    _STANDBY = ((0, -1, 2), (0, 1, 0), (-1, 0, 1), (1, 0, 3))

    def _tile_at(x, y):
        """回读一格（判据用；失败返回 None）。"""
        try:
            d = requests.get(f"{base}/surroundings", params={"radius": 2}, timeout=10).json()
        except Exception:
            return None
        for t in d.get("tiles", []):
            if t.get("x") == x and t.get("y") == y:
                return t
        return None

    def _dug(x, y):
        """这格挖成了吗 —— **回读游戏状态**，不看工具自报。"""
        t = _tile_at(x, y)
        if t is None:
            return False
        if t.get("objId") in SPOT_IDS:
            return False              # 斑点还在
        if t.get("forageCrop") == "2":
            return False              # 姜还在
        return True

    def _me():
        """我现在的格子 (x,y)；读不到返回 None。"""
        try:
            p = requests.get(f"{base}/state", timeout=10).json().get("player") or {}
            return (int(p.get("x", -9)), int(p.get("y", -9)))
        except Exception:
            return None

    def _moving():
        """还在走吗（`/state` 的 `isMoving`）。读不到就当"停了"，别把等待拖满。"""
        try:
            p = requests.get(f"{base}/state", timeout=10).json().get("player") or {}
            return bool(p.get("isMoving"))
        except Exception:
            return False

    def _face_toward(face_dir):
        try:
            requests.post(f"{base}/face", json={"direction": face_dir}, timeout=8)
            time.sleep(0.2)
            return True
        except Exception:
            return False

    def _walk_exact(sx, sy):
        """把小人**正好**放到 (sx,sy) 这一格上：**先等它自己走过去**，到了再做像素校正。

        ⚠️ 2026-09-19 恒真机（「**如果能自然走路衔接就好了，突然就飞走了**」）：
        `/walk_to` 是**排好路线就返回**（实测回包 **0.01 秒**、`ok:true`），真正走完得按格数等
        —— 实测 **≈4.8 格/秒**（10 格 ≈ 2.1 秒）。这里原来写死 `sleep(0.6)`，**只够走 3 格**；
        姜点彼此隔 6~27 格 ⇒ **每一次都落到下面那句 `/position`** ⇒ 换一棵姜瞬移一次，
        肉眼看就是"**突然就飞走了**"。
        ⇒ 改成**等它自己走到**（同 `moss_run._walk_to_stand` / `berry_run.walk_near` 的写法）；
          `/position` 只留作**贴到格上之后的像素校正**（它本来的用途，见 `_snap`）。
        ⚠️ 没走到就**如实返回 False**（宁报错别兜底）——让 `_stand_and_face` 换下一个邻格，
          不许拿瞬移糊过去。
        ⚠️ 2026-09-19 恒真机（「**朝向有时不是很准**」）：单挥只命中**面朝的那一格**，
        而这游戏按**像素**算朝向格（`GetToolLocation()` = 包围盒边缘 ± 48~64px）。
        小人骑在瓦片边界上，同样的"面朝下"会四舍五入到**隔壁列/行** ⇒ 对着空气挥。
        已知 `/walk_to` 与 `/position` 落点差 16px（CHANGELOG 09-10「走位落点精度」：
        `walk_to` 落 `y*64-32`、`position` 落 `y*64`）—— 差这一截就够挥空。
        ⇒ 走停之后**回读确认**，只校正**一次**。
        ⚠️ 只校正一次：原来写成 3 轮循环，恒当场看见「**左跑一下右跑一下很傻**」。
        """
        cur = _me()
        if cur == (sx, sy):
            return True                                   # 已经站着了，别动
        try:
            requests.post(f"{base}/walk_to", json={"x": sx, "y": sy, "location": loc}, timeout=20)
        except Exception:
            return False
        # 按距离等（≈4.8 格/秒 + 余量，上限 25s）；走停（isMoving=False）就提前收
        # ⚠️ **必须先看见它走过**才认"走停"：`/walk_to` 刚返回那一瞬间小人还没迈步，
        #    `isMoving` 是 False —— 直接拿它当"走完了"会让**每一趟都当场判失败**。
        cur = _me() or (10 ** 9, 10 ** 9)
        deadline = time.time() + min(25.0, 3.0 + (abs(sx - cur[0]) + abs(sy - cur[1])) * 0.45)
        seen_moving = False
        while time.time() < deadline:
            if _me() == (sx, sy):
                break
            if _moving():
                seen_moving = True
            elif seen_moving:
                break                                     # 走起来了又停 = 这一趟结束
            time.sleep(0.25)
        cur = _me() or (10 ** 9, 10 ** 9)
        # 走停了却差一格（walk_to 常差一格）⇒ 下面贴正；差得远 = 压根没走到，如实失败
        if abs(cur[0] - sx) + abs(cur[1] - sy) > 1:
            return False
        try:
            requests.post(f"{base}/position", json={"x": sx, "y": sy}, timeout=10)  # 像素级贴正
            time.sleep(0.25)
        except Exception:
            return False
        return _me() == (sx, sy)

    def _snap(sx, sy):
        """**原地贴正**：把像素坐标对齐到格子中心（`/position` 写 `x*64`）。
        人在同一格上也可能"骑在瓦片边界"，`GetToolLocation()` 便会四舍五入到隔壁 ⇒ 挥空。
        只在这一下**挥空之后**才用（同一个格子，看不出位移，但朝向就准了）。"""
        try:
            requests.post(f"{base}/position", json={"x": sx, "y": sy}, timeout=10)
            time.sleep(0.25)
            return True
        except Exception:
            return False

    def _stand_and_face(x, y, k=0, snap=False):
        """站到 (x,y) 的四邻之一并面朝它。成功返回站位，失败 None。

        ⚠️ 2026-09-19 恒真机（「**能不能走邻近格，左跑一下右跑一下很傻**」）：
        原来固定按 上→下→左→右 的顺序找站位，挖完一个还得**横穿整块地**去下一个的"上面那格"。
        可挖斑点是一圈一圈来的 —— 人**多半本来就站在某个能站的邻格上**。
        ⇒ 现在：①**已经在能站的邻格上就原地转身挥**（一步不走）；
              ②否则**挑离自己最近的那个邻格**走一趟。`k`=重试时换起点方向（别在同一坏站位反复挥）。
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
            # 距离只用来排序：已在脚下 = 距离 0，最优先
            cands.append((abs(sx - cur[0]) + abs(sy - cur[1]), sx, sy, face_dir))
        if not cands:
            return None
        cands.sort(key=lambda c: c[0])
        for _, sx, sy, face_dir in cands:
            if (sx, sy) == cur:                            # ① 就站在这格上：转身即可（一步不走）
                if snap:
                    _snap(sx, sy)
                return (sx, sy) if _face_toward(face_dir) else None
        for _, sx, sy, face_dir in cands:                  # ② 挑最近的那格走一趟
            if _walk_exact(sx, sy) and _face_toward(face_dir):
                return (sx, sy)
        return None

    def dig_one(x, y):
        """**走到旁边 → 面朝它 → 挥一下**（不蓄力），再回读那格确认挖成了。

        ⚠️ 2026-09-19 恒真机改（原来复用 `/tool_area till` 单格矩形）：那条路是给**整块田**
        设计的**蓄力横扫**（铱锄头 power4 一次挥 18 格），拿来挖单格斑点两头不讨好 ——
        ①**挖不动**：蓄力释放的落点由挥击动画按当时的 `GetToolLocation()` 算，站位/朝向一偏，
           实际命中的就不是目标格（真机 16 个斑点只中了 2 个，工具却每条都打了 ✓）；
        ②**白烧体力**：每挥 18 格，实测 7 下从 474 掉到 362。
        单格就单挥（power0 只命中面朝那一格），跟真人蹲下挖一铲是一个道理。
        判据是**回读那一格**（斑点没了/姜没了），不是工具说了什么。
        """
        if _dug(x, y):
            return True, "本来就没了"
        try:
            requests.post(f"{base}/select", json={"name": "Hoe"}, timeout=8)
        except Exception:
            pass
        # 挥空就**换个站位重来**（不是原地再挥一次——原地挥一百下还是那个错朝向）
        last = ""
        for attempt in range(3):
            # attempt 0 最省事（多半原地转身就挥）；挥空了才贴正像素、再不行才换邻格
            stand = _stand_and_face(x, y, k=attempt, snap=(attempt > 0))
            if not stand:
                return False, (last or "四邻") + "：四邻没有能站定的格（水/设施挡着/走不过去）"
            last = f"站{stand}"
            try:
                requests.post(f"{base}/tool", json={}, timeout=20)
            except Exception as e:
                return False, f"挥击失败: {e}"
            time.sleep(0.7)
            if _dug(x, y):
                return True, f"{last}挥中"
        return False, f"{last} 换了 3 个站位都没挥中（回读那格斑点/姜还在）"

    # ── 锄头检查（没锄头不挖）──
    if not has_hoe():
        log("⚠️ 背包没有锄头，跳过斑点挖掘（带锄头再来挖）")
        return

    spots, loc = scan_spots()
    if not spots:
        log(f"🎉 {loc or '当前场景'}没有斑点（蚯蚓点/远古斑点）")
        return

    total0 = len(spots)
    log(f"🪱 {loc} 找到 {len(spots)} 个斑点: {[(x, y, n) for x, y, n in spots]}")
    if args.dry_run:
        log("--dry-run：不挖")
        return

    dug = failed = 0
    for _ in range(args.rounds):
        spots, loc = scan_spots()
        if not spots:
            break
        for x, y, name in spots:
            ok, info = dig_one(x, y)
            if ok:
                dug += 1
                log(f"  · 挖 ({x},{y}) {name} ✓ {info}")
            else:
                failed += 1
                log(f"  · 挖 ({x},{y}) {name} ⚠️ {info}")
            time.sleep(0.4)
        # 一轮下来一个都没挖成（水/设施围着之类）⇒ 别空转下一轮，如实报
        if dug == 0 and failed:
            break

    # ⚠️ 收工按**真剩几个**报，别按"挥了几次/失败几次"——重试成功的会各算一次 ⚠️，
    #    上一条版本就闹过"13 个全挖完了，汇总却写另有 6 个没挖动"的笑话。
    left, _ = scan_spots()
    done_n = total0 - len(left)
    if done_n:
        log(f"✅ 挖成 {done_n}/{total0} 个斑点"
            + (f"；还剩 {len(left)} 个没挖动：{[(a, b) for a, b, _ in left]}（看上面每行 ⚠️ 的原因）"
               if left else "")
            + "。蚯蚓点出古物/矿物/种子，远古斑点出季节作物种子（掉落吸附进包）")
    else:
        log(f"⚠️ 一个都没挖成（共 {total0} 个）——看上面每行的原因")


if __name__ == "__main__":
    main()
