"""
🎁 pickup_scene.py — 捡当前场景的可拾取地面物品

用途：
- 畜棚/鸡舍收集鸡蛋、鸭蛋、鸭毛、羊毛、松露等（动物产物掉地上）
- 采集物（Spring Onion、Truffle 等）
- 玩家自己丢在地上的物品

原理：扫 /surroundings，凡"可走(passable=True) + 有 object"的格就是地上物品，
走过去自动拾取。黑名单排除箱子/洒水器/火把等能踩上去但不是要捡的结构。

📐 **扫描范围 = 以你为中心的「方形」±30 格（61×61）**，不是圆——C# 的判据是
   `Math.Abs(ex-cx) <= r && Math.Abs(ey-cy) <= r`。**超出范围的它看不见**，
   所以"找到 0 个"只说明**附近没有**，不说明这张图没有。
   （`--radius` 最大 30；再大 `/surroundings` 会**静默退回 10**，脚本已收回上限并警告。）

用法:
  python pickup_scene.py                    # 捡当前场景全部可拾取物
  python pickup_scene.py --port 7843
  python pickup_scene.py --max 20 --dry-run # 只看不捡
"""

import os
import sys
import time
import argparse


def _cli_args():
    p = argparse.ArgumentParser(description="[pickup] 捡当前场景地面可拾取物品")
    p.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
    p.add_argument("--radius", type=int, default=30,
                   help="扫描半径（默认30，上限30；**方形** ±r 格，不是圆——见文件头）")
    p.add_argument("--max", type=int, default=30, help="一次最多捡几个（默认30）")
    p.add_argument("--dry-run", action="store_true", help="只扫不捡")
    p.add_argument("--host-port", type=int, default=None, help="host端口（默认7842，仅读状态用）")
    return p.parse_args()


# ⚠️⚠️ 2026-10-01：argparse **挪进 `__main__`**（原来在模块级 `args = parser.parse_args()`）——
#    服务器要 `import pickup_scene` 复用 `scan_pickables()`（判据只一处，见那个函数），
#    带着服务器自己的 argv 去 import 会**当场 SystemExit(2)**（`feed_hay.py` 同款改动）。
#    ⚠️ **不在 import 期写 `NAGI_URL`**（feed_hay 那次踩过：import 期 setdefault 会让 CLI 的
#       `--port` 永远不生效 ⇒ 冒烟打到真机）。CLI 行为一字未变：`--port/--radius/--max/--dry-run` 照旧。
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import requests

# ⚠️ 这里**故意不写 env**（见上面那段）：`NAGI` 只是**脚本自带 requests 调用**的默认地址；
#    服务器那一路（`_im_pick` → `scan_pickables(tiles=…)`）**根本不看它**。
NAGI = os.environ.get("NAGI_URL") or "http://localhost:7843"


def _apply_port(port):
    """CLI 的 `--port` → `NAGI_URL`（**显式给的 env 仍然优先**，同老行为）。"""
    global NAGI
    if port and not os.environ.get("NAGI_URL"):
        os.environ["NAGI_URL"] = f"http://localhost:{port}"
    NAGI = os.environ.get("NAGI_URL") or "http://localhost:7843"
    return NAGI


def log(msg):
    try:
        print(f"[pickup] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[pickup] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


# 能踩上去但不是要捡的结构（排除）
BLACKLIST = {
    "Chest", "Big Chest", "Stone Chest",
    "Sprinkler", "Quality Sprinkler", "Iridium Sprinkler", "Pressure Nozzle",
    "Scarecrow", "Deluxe Scarecrow", "Campfire", "Torch", "Stump",
    "Sign", "Wood Sign", "Stone Sign", "Workbench", "Grave Stone",
    "Crab Pot", "Furnace", "Charcoal Kiln", "Recycling Machine",
    "Worm Bin", "Keg", "Preserves Jar", "Cheese Press", "Loom",
    "Mayonnaise Machine", "Oil Maker", "Seed Maker", "Crystalarium",
    "Tapper", "Heavy Tapper", "Bee House", "Silo", "Slime Egg",
    "Slime Incubator", "Mini-Fridge", "End Table", "TV", "Radio",
    "Picture", "Floor", "Path", "Cobblestone Path", "Wood Floor",
    "Stone Floor", "Brick Floor", "Wood Path", "Crystal Floor",
    "Rug", "Fish Pond", "Mill", "Coop", "Barn", "Shed", "Obelisk",
    "Greenhouse", "Mushroom Box", "Statue Of Endless Fortune", "Safe",
    "Skeleton", "Museum", "Horse", "Milk Pail", "Shears", "Scythe",
    "Furniture", "Armchair", "Bench", "Chair", "Table", "Couch",
    "Dresser", "Stool", "Bookshelf", "Fireplace",
}
# 工具/武器绝对不捡
BLACKLIST |= {"Pickaxe", "Axe", "Hoe", "Watering Can", "Sword", "Scythe",
              "Fishing Rod", "Copper Pickaxe", "Iron Pickaxe", "Gold Pickaxe",
              "Iridium Pickaxe", "Copper Axe", "Iron Axe", "Gold Axe", "Iridium Axe",
              "Copper Hoe", "Iron Hoe", "Gold Hoe", "Iridium Hoe",
              "Copper Watering Can", "Iron Watering Can", "Gold Watering Can",
              "Iridium Watering Can"}
# 可走但不是收集品的地面装饰/杂物（2026-08-06 实测：沙滩杂草/鸡舍家具会被误走）
# 注意：珊瑚/蛤蜊/牡蛎/海草等是【可采集物】不能黑，黑掉的是真杂物/家具。
BLACKLIST |= {"Weeds", "Stone", "Rock", "Glass Shards", "Rotten Plant",
              "Twig", "Grass Starter", "Fiber",
              # 鸡舍/畜棚/棚子里的家具（能踩但绝对不能捡）
              "Incubator", "Heater", "Feed Hopper", "Auto-Grabber", "Auto-Petter",
              "Coop", "Barn", "Shed", "Slime Hutch", "Fish Pond", "Milk Pail",
              "Shears", "Egg Basket", "Duck Egg Basket", "Mini-Fridge",
              "Ostrich Incubator", "Hay", "Automatic Feeders"}
# 🌿 2026-09-12（恒拍板）：「拾取白名单可以考虑加所有可拾取物品了。贝壳啊水果啊我们也都没有加」
#    ⇒ **不再维护名单，直接问游戏**：`/surroundings` 现在每格带 `forage`
#    （C# 侧读 `Object.isForage()`，反编译 `Object.cs:2806`：`Category ∈ {-79,-81,-80,-75,-23}`
#      或带 tag `forage_item`，**外加硬编码 `(O)430`=松露** —— 游戏自己给松露开的特例）。
#    一份判据盖住 野菜/浆果/水果/贝壳/海胆/珊瑚/松露 全类，判据从「**必须 passable**」
#    改成「**passable 或 forage**」：不可站但可手捡的，走过去 face+interact 就进包
#    （真机验过：站 (8,10) 朝 (8,9) 野梅 interact → `[金]野梅 120g` 进包 + 📰 小新闻）。
#
# ⚠️ 旧 DLL（2026-09-12 之前）**没有 forage 字段** ⇒ 这类东西会被下面那行静默跳过（回到"找到 0 个"）。
#    这正是本项目最怕的"报成功而事没发生"，所以启动时用 `/status.build` 显式查一次、**明说**（见下）。
_FORAGE_DLL_MIN = "2026-09-12"   # 带 forage 字段的最早构建日（BuildStamp 是 MSBuild 自动烤进 DLL 的）


# 🪱 远古斑点：**不是手捡的**（要锄头）—— 单子那边有独立的「挖 远古斑点」那一行管它。
#    它们也是 `(O)` 开头的物件（`(O)590` / `(O)SeedSpot`），放宽判据后会被当成"地上能捡的"，
#    按了只会空手一下 ⇒ 在这儿排除（**跟 `spot_run.py` 的 `SPOT_IDS` 同源**，别处不再抄一份）。
_SPOT_LIKE = ("(O)590", "590", "(O)SeedSpot", "SeedSpot")


def scan_pickables(tiles, center=None):
    """🎁 「这一带地上有什么能捡的」→ `[(x, y, 物件名), …]`（按离中心由近到远）。

    ⚠️ **判据只此一处**：脚本自己的 `main()` 与 **MCP 单子那行「捡 地上的东西」**共用它
       （服务器把 ctx 已经拉到的 `/surroundings` tiles 递进来 —— **不再多打一发**）。

    判据（2026-10-01 恒：「**复用原来的捡蛋工具**」之后放宽的形状）：
      · 有 `object`（没物体的格只剩"成熟大葱"那条：`forageCrop=="1"` 且 harvestable）；
      · **不在 `BLACKLIST`**（箱子/洒水器/火把/孵化器/加热器/饲料斗/自动采集器/自动抚摸机/
        蟹笼/工具/杂草石头… —— 那份名单就在本文件，**别处不许再抄**）；
      · **`objId` 以 `(O)` 开头** = 普通物件（蛋/毛/兔脚/掉落物/采集物）；
        `(BC)` 大型可制造物 / `(F)` 家具 / `(T)` 工具 / `(W)` 武器 **一律不要**；
      · **不再要求 `passable`/`forage`**：棚里的蛋/毛**站不上去**（物件挡路），老判据
        `（passable 或 forage）` 把它们全排除了（恒真机：鸡舍地上 17 件、`/surroundings`
        给 `passable=False` 且没有 `forage` 键）⇒ 现在放宽；站不住的目标由执行侧
        「**站旁边 face+interact**」那条路兜（工具里本来就有，野梅真机验过）；
      · 远古斑点（`_SPOT_LIKE`）**排除**（要锄头，归「挖 远古斑点」那行）。
    """
    cx, cy = (center or (0, 0))
    targets = []
    for t in tiles or []:
        obj = t.get("object") or ""
        if not obj:
            # 没物体的格只剩"成熟大葱"那条（长在 HoeDirt 上，crop.indexOfHarvest 为空但 forageCrop=1）
            if t.get("forageCrop") == "1" and t.get("harvestable") is not False:
                targets.append((t.get("x"), t.get("y"), "成熟大葱"))
            continue
        if any(blk in obj for blk in BLACKLIST):
            continue
        if str(t.get("objId") or "") in _SPOT_LIKE:
            continue                      # 远古斑点：要锄头，不归「捡」
        if not str(t.get("objId") or "").startswith("(O)"):
            continue                      # 只认普通物件（蛋/毛/掉落/采集物）
        targets.append((t.get("x"), t.get("y"), obj))
    # 去重（多格可能重复报）+ 按离中心距离排序
    seen, uniq = set(), []
    for x, y, obj in targets:
        if (x, y) in seen:
            continue
        seen.add((x, y))
        uniq.append((x, y, obj))
    uniq.sort(key=lambda t: (abs(t[0] - cx) + abs(t[1] - cy)))
    return uniq


def _dll_has_forage(base):
    """当前 DLL 带不带 `forage` 字段。True/False/读不到=None（不猜）。"""
    try:
        build = (requests.get(f"{base}/status", timeout=5).json().get("build") or "")
    except Exception:
        return None
    head = build[:10]
    if len(head) != 10 or head.count("-") != 2:
        return None                      # "未生成(非 MSBuild构建)" 之类 ⇒ 认不出就说认不出
    return head >= _FORAGE_DLL_MIN


def approach_tiles(x, y, px, py):
    """🎯 想够到 (x,y) 那件东西，**先试哪个落点** → 四邻里按**离人最近**排好的一串。

    ⚠️ 2026-10-02 恒：「**走不通才换个方向试，最好是先试试离自己近的那一个落点**」——
       以前这里写死一个固定顺序（`(x,py) → (px,y) → (x,y+1) → …`），
       一上来就可能挑到**离自己最远的边角**（真机那次挑的是 `(1,5)`）⇒ 白走一趟。
    ⚠️ 顺序 = **离人曼哈顿距离升序**（同距离时按 下/上/右/左 的固定序，保证可复现）；
       **只有前一个真走不通，才轮到下一个**（调用方按这个顺序试、够到了就 break）。
    """
    cands = [(x, y + 1), (x, y - 1), (x + 1, y), (x - 1, y)]
    cands.sort(key=lambda c: abs(c[0] - px) + abs(c[1] - py))
    return cands


def main(radius=30, max_n=30, dry_run=False):
    base = NAGI
    # ⚠️ 2026-09-12 真机踩到（就在验 forage 那次）：`/surroundings` 的 radius **超过 30 会静默退回 10**
    #    （`ModEntry.cs` 的 clamp 分支，退回的不是 30 而是**默认 10**，比 30 还小）——我拿 `--radius 40`
    #    扫海滩，回"找到 0 个"，差点误判成 forage 没生效。这里收回上限并**明说**，别让尺子骗人。
    if radius > 30:
        log(f"⚠️ --radius {radius} 超出 /surroundings 上限 30"
            f"（再大它会**静默退回 10**，比 30 还小）——已按 30 跑")
        radius = 30
    # 📐 半径语义（恒 2026-09-23「采集物的探测范围得说清楚」）：C# 是
    #    `Math.Abs(ex-cx) <= r && Math.Abs(ey-cy) <= r` ⇒ **方形**（切比雪夫），
    #    以你为中心 (2r+1)×(2r+1) —— **不是**圆。报告里把范围写出来，别让 AI 拿
    #    "找到 0 个"当"这张图没有"，它可能只是站在离东西 31 格的地方。
    _span = 2 * radius + 1
    _scope = f"附近 {radius} 格内（以你为中心的方形 {_span}×{_span}）"
    try:
        st = requests.get(f"{base}/status", timeout=5).json()
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)
    if not st.get("worldReady"):
        log("❌ 游戏未就绪")
        sys.exit(1)

    # 🌿 旧 DLL **明说**（见文件头）：不拦（农场日常大部分不受影响），但绝不让它静默变哑。
    if _dll_has_forage(base) is False:
        log(f"⚠️ DLL 是旧版（build={st.get('build')}）——没有 `forage` 字段，"
            f"水果/贝壳/松露这类「不可站但可手捡」的东西会被漏掉。"
            f"请重编并部署 NagiBridge.dll（≥ {_FORAGE_DLL_MIN}）后重启游戏。")

    data = requests.get(f"{base}/surroundings", params={"radius": radius}, timeout=10).json()
    loc = data.get("location", "?")
    cx, cy = data.get("center", {}).get("x", 0), data.get("center", {}).get("y", 0)

    # 可拾取 = **共用 `scan_pickables()`**（判据/名单都在那一处；单子那行读的也是它）
    uniq = scan_pickables(data.get("tiles", []), (cx, cy))

    log(f"📍 {loc} | {_scope} 找到 {len(uniq)} 个可拾取物品")
    for x, y, obj in uniq[:15]:
        log(f"  · {obj} ({x},{y})")

    if dry_run:
        log("--dry-run：不捡")
        return

    if not uniq:
        log(f"🎉 {_scope}没有要捡的东西（走远点再扫一次才知道更外面有没有）")
        return

    def walk_near(x, y, timeout=20):
        """走过去（物体格本身会被当障碍，自动停在旁边）并按位置等到达。
        注意：游戏窗口后台暂停时 isMoving=False 但走位排队——按位置判断。"""
        try:
            r = requests.post(f"{base}/walk_to", json={"location": loc, "x": x, "y": y}, timeout=10)
            if not r.json().get("ok"):
                return False
            deadline = time.time() + timeout
            while time.time() < deadline:
                s = requests.get(f"{base}/state", timeout=10).json()
                p = s.get("player", {})
                if abs(p.get("x", 0) - x) <= 1 and abs(p.get("y", 0) - y) <= 1:
                    time.sleep(0.3)
                    return True
                time.sleep(0.25)
            return False  # 真没走到，别假成功
        except Exception:
            return False

    def object_still_there(x, y):
        """物体还在 (x,y) 吗？（验证拾取是否成功）"""
        try:
            d = requests.get(f"{base}/surroundings", params={"radius": 5}, timeout=10).json()
            return any(t.get("x") == x and t.get("y") == y and t.get("object")
                       for t in d.get("tiles", []))
        except Exception:
            return True

    def face_and_interact(x, y):
        """面向物体格并交互（捡拾动作）。"""
        s = requests.get(f"{base}/state", timeout=10).json()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        if px < x: direction = 1   # 物体在右
        elif px > x: direction = 3 # 物体在左
        elif py < y: direction = 2 # 物体在下
        elif py > y: direction = 0 # 物体在上
        else: direction = 2
        requests.post(f"{base}/face", json={"direction": direction}, timeout=10)
        time.sleep(0.25)
        requests.post(f"{base}/interact", {}, timeout=10)
        time.sleep(0.5)

    def pick_up_object(x, y):
        """捡地上物体（蛋蛋/野菜/松露）：走过去 → 确保卡迪纳尔相邻 → 面朝 → interact。
        实测（2026-08-06）：蛋蛋/野菜不是走过去自动收，要捡拾动作；对角够不着。"""
        if not walk_near(x, y):
            return False
        s = requests.get(f"{base}/state", timeout=10).json()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        # 若对角（px!=x 且 py!=y），挪到卡迪纳尔相邻格
        # 🎯 2026-10-02：落点顺序走 `approach_tiles()`（**离人最近的先试**，走不通才换下一个）——
        #    原来那个写死的顺序一上来就可能挑到离自己最远的边角（真机挑的是 (1,5)）。
        if px != x and py != y:
            for nx, ny in approach_tiles(x, y, px, py):
                try:
                    requests.post(f"{base}/walk_to", json={"location": loc, "x": nx, "y": ny}, timeout=10)
                    time.sleep(1.0)
                except Exception:
                    pass
                s = requests.get(f"{base}/state", timeout=10).json()
                cx, cy = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
                if (cx == x and abs(cy - y) == 1) or (cy == y and abs(cx - x) == 1):
                    break  # 卡迪纳尔相邻了
        face_and_interact(x, y)
        # 验证真的捡到
        return not object_still_there(x, y)

    def _free_slots():
        """背包还剩几格。读不到 → None（**不拿它做判断**，别用猜的当尺子）。"""
        try:
            s = requests.get(f"{base}/state", params={"light": "true"}, timeout=10).json()
            inv = s.get("inventory", [])
            used = len([i for i in inv if i.get("name")])
            return ((s.get("player", {}) or {}).get("maxItems") or 36) - used
        except Exception:
            return None

    # 🎒 2026-09-12 真机：恒"包包满了，所以捡不动了" —— 那天海滩 12 个只进 3 个，我**先猜成"走位够不到"**
    #    （错），回读才看见 `36/36 空位 0`。捡不动**必须说清是哪种捡不动**（背包满 vs 真够不着），
    #    否则 AI/人都以为东西没了 —— 同"报成功而事没发生"那一类，只是反着来。
    _free = _free_slots()
    if _free == 0:
        log("🎒 背包满了（0 空位）——不是捡不到，是装不下："
            "先去出货箱卖 / storage 存箱子，再回来捡")
        return

    picked = 0
    for x, y, obj in uniq[:max_n]:
        if pick_up_object(x, y):
            picked += 1
            time.sleep(0.1)
        elif _free_slots() == 0:
            log("🎒 背包满了（0 空位）——剩下的不是捡不到，是装不下："
                "先去出货箱卖 / storage 存箱子，再回来捡")
            break

    # 顺带清 debris（动画掉落物：镰刀作物/怪掉落，走过去才捡）
    try:
        d = requests.get(f"{base}/debris", timeout=10).json()
        for it in d.get("debris", [])[:8]:
            if picked >= max_n:
                break
            x, y = it.get("x", 0), it.get("y", 0)
            if walk_near(x, y):
                picked += 1
                time.sleep(0.1)
    except Exception:
        pass

    log(f"🎁 拾取完成：{picked} 个")


if __name__ == "__main__":
    # ⚠️ argparse 在这儿解析（见文件上方那段：import 期解析会吃服务器 argv、当场 SystemExit）
    _args = _cli_args()
    _apply_port(_args.port)
    main(radius=_args.radius, max_n=_args.max, dry_run=_args.dry_run)
