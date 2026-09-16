"""
🌾 scythe_crops.py — 作物收获（拟人：逐个走位 → 面朝 → 动手）

实测（2026-08-06）确定的两条收获行为：
  1. 手摘作物（蒜/西瓜）→ 直接进背包
  2. 镰刀作物（小麦等）→ 收获物掉地上（Wheat+Hay 成 debris），要走过去捡

⚠️ 2026-09-16 大改：原来这里 `import crops`，拿本地那张 **crop ID → 名字/镰刀/再生**
的手抄表翻译作物身份。恒拍板用 `/give` 逐条核完，**39 条错 20 条**（454 写"杨桃"实为
上古水果、20 写"甘蓝"实为韭葱、282 写"草莓"实为蔓越莓，还有几条指向"蛋黄酱/铜矿石/
错误物品"）——名字错、**工具选错**、阶段播报歪。⇒ `crops.py` 整张表删掉，
改**问游戏**：`/surroundings` 现在直接报 `cropName` / `cropScythe` / `cropRegrow`
（C# 那边读 `ItemRegistry` + `Crop.GetHarvestMethod()` / `Crop.RegrowsAfterHarvest()`）。
判据一律问游戏、别在消费侧猜——同 `Object.isForage()` 替三张名单那条规矩。

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
parser.add_argument("--radius", type=int, default=15, help="扫描成熟作物的半径（默认15）")
# 🎓 2026-09-16 恒：**有耕种精通 + 背包有铱镰刀才挥镰刀，否则手摘**。
#    门禁判据在服务器侧（`_mastery_claimed("farming")` 读游戏自己的 `mastery_0` 统计），
#    因为脚本这边拿不到那份带缓存的判据。传了不代表一定会挥——脚本还会确认铱镰刀真在背包里。
parser.add_argument("--scythe", action="store_true", help="已领耕种精通：优先挥镰刀（背包没有铱镰刀则退回手摘）")
parser.add_argument("--dry-run", action="store_true", help="只扫不收")
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

    作物名/是否要镰刀，**全是游戏报的**（`/surroundings` 的 `cropName`/`cropScythe`）——
    见 `ModEntry.cs` 5469 那段。别再维护本地 ID 表了。

    mature: [{'x','y','crop','name','scythe'}] 成熟株
    growing_by_name: {作物名: 数量} 生长中统计
    """
    data = get("/surroundings", {"radius": args.radius + 5})
    loc = data.get("location", "?")
    mature = []
    growing = {}
    unnamed = 0
    for t in data.get("tiles", []):
        if not t.get("crop"):
            continue
        # ⚠️ 旧 DLL 不报 cropName（本改造是 2026-09-16 才加的）→ **如实说"这格名字游戏没报"**，
        #    不要拿本地表凑一个（那张表错 20/39，凑出来的名字比"未知"更坏）。
        nm = t.get("cropName")
        if not nm:
            nm = f"作物#{t['crop']}"
            unnamed += 1
        if t.get("harvestable"):
            mature.append({
                "x": t["x"], "y": t["y"], "crop": t["crop"],
                "name": nm, "scythe": bool(t.get("cropScythe")),
            })
        else:
            growing[nm] = growing.get(nm, 0) + 1
    if unnamed:
        log(f"⚠️ 有 {unnamed} 格游戏没报作物名（cropName 缺失⇒多半是旧 DLL 没重启，"
            f"名字显示成『作物#ID』）")
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


# ── 🌀 铱镰刀一挥的范围：**四面照抄实测表**（2026-09-16 温室真机，站 (10,15) 四面各挥一发）──
# 恒原以为"面前长3×2、站长边中点"（6 格）；恒拍板"按实测来"，于是逐面量：
#     朝下(2) 5宽×3高 **居中**（含脚下）   ← 唯一规整的一面
#     朝上(0) 偏上（rel y -3..0）
#     朝右(1) 偏上、偏右       朝左(3) 偏上、偏左
# ⚠️ **四面全都偏上**——这不是随机噪声，是稳定的系统性偏心（多半是像素级判定框跟瓦片对齐的锅），
#    所以**别去推公式**，照抄这张表最实在。每面一发盖 13~15 格。
# ⚠️ 表里有"漏格"：实测 (12,15) 在朝下的框里却没被割到（正常成熟的上古水果，只是没扫上）。
#    ⇒ **站位器只管"尽量盖住"，漏的全靠每轮重扫兜回来**（见 _harvest_scythe_area）。
#    ⚠️ 反过来说：**别拿这张表当"挥了就一定清空"的保证**，它只是调度用的。
SWING_SHAPE = {
    0: [(-1, -3), (0, -3), (1, -3), (-1, -2), (0, -2), (1, -2), (2, -2),
        (-1, -1), (0, -1), (1, -1), (2, -1), (0, 0), (1, 0)],
    1: [(0, -3), (1, -3), (0, -2), (1, -2), (0, -1), (1, -1), (2, -1),
        (-1, 0), (0, 0), (1, 0), (2, 0), (-1, 1), (0, 1), (1, 1)],
    2: [(-2, -1), (-1, -1), (0, -1), (1, -1), (2, -1),
        (-2, 0), (-1, 0), (0, 0), (1, 0), (2, 0),
        (-2, 1), (-1, 1), (0, 1), (1, 1)],
    3: [(-2, -2), (-1, -2), (0, -2), (1, -2), (-2, -1), (-1, -1), (0, -1), (1, -1),
        (-2, 0), (-1, 0), (0, 0), (1, 0), (-1, 1), (0, 1), (1, 1)],
}


def swing_box(px, py, face):
    """站在 (px,py) 朝 face 一挥能割到的格子集合（照 SWING_SHAPE 平移）。"""
    return {(px + dx, py + dy) for dx, dy in SWING_SHAPE[face]}


_PASS_CACHE = {}
TELEPORTS = [0]     # 🌟 走路没送到、靠 `/position` 瞬移兜底的次数（屏幕上会闪一下）——收工时报出来


def _passable(x, y):
    """`/passable` 带缓存——站位器一轮要问几百格，不能一格一发 HTTP。"""
    k = (x, y)
    if k not in _PASS_CACHE:
        try:
            _PASS_CACHE[k] = bool(post("/passable", {"x": x, "y": y}).get("passable"))
        except Exception:
            _PASS_CACHE[k] = False
    return _PASS_CACHE[k]


def _plan_swings(mature, px, py, exclude=(), exclude_stands=()):
    """站位器：算出**这一轮所有还算划算的挥法**，按优先级排好返回。

    每项 = (站位x, 站位y, 朝向, 能盖住的成熟格数)。盖得多优先，同样多挑离自己近的。

    `mature`  = 成熟格集合（**每轮重扫后再调**，所以"已经割掉的范围"自动不在里面）。
    `exclude` = 已经挥过的 (站位,朝向)。**这里要的是"跳过已挥的、接着用次优的"，
               而不是"最优先的挥过了就整轮收工"**——2026-09-16 第一版就是一刀切停全局，
                结果 111 株才割 80 就收工了（剩 31 株明明还有别的站位可挥）。
    """
    if not mature:
        return []
    stands = set()
    for (cx, cy) in mature:
        for dx in range(-3, 4):
            for dy in range(-3, 4):
                stands.add((cx + dx, cy + dy))

    cand = []
    for (sx, sy) in stands:
        if not _passable(sx, sy):        # 洒水器/机器/墙 → 站上去会把设施刨了
            continue
        if (sx, sy) in exclude_stands:   # 这格上挥过且**一株没掉** → 整格作废，别再换朝向试
            continue
        dist = abs(sx - px) + abs(sy - py)
        # 🎯 **站到成熟作物本格 = 那一发保底收 1 株**：实测铁律，四面形状里 `(0,0)` 全在
        #    ⇒ **脚下那格一定会被割到**。给这种站位压倒性加分，首选"保底不空挥"的挥法。
        #    ⚠️ 起因（恒 2026-09-16 看出来的）："**补漏的时候偶尔能挥的范围却一直转，
        #       不知道在找什么呢**" —— 剩株那几发会在**同一个站位把四个朝向挨个试一遍**
        #       （日志：站(14,16)朝0/1/2/3 全"一株没掉"）。模型对"边缘那一两株"经常判错，
        #       四个朝向**都**预测能盖到、实际**都**盖不到 ⇒ 原地转圈。
        #    加分而非硬限制：万一有盖得极多的空位站位，仍允许它翻盘。
        on_crop = 100 if (sx, sy) in mature else 0
        for face in (0, 1, 2, 3):
            if (sx, sy, face) in exclude:
                continue
            n = len(swing_box(sx, sy, face) & mature)
            if n:
                cand.append((-(n + on_crop), dist, sx, sy, face))   # 保底 > 盖得多 > 离得近
    cand.sort()
    out = []
    for (n, _d, sx, sy, face) in cand:
        # 返回的 cover 报**真实**预测（不含 on_crop 加成），别让日志吹牛
        out.append((sx, sy, face, len(swing_box(sx, sy, face) & mature)))
    return out


def _harvest_scythe_area(cur_loc, mature0):
    """🌀 铱镰刀范围收获：**每一挥之前都重新扫一遍**，再挑一发最划算的站位。

    ⚠️ 2026-09-16 恒："还是一格一格走。…或者这样可能更简单：**改成每次都重新扫下一个
       可收获有果实的格子**（镰刀已经割掉的范围就会被跳过）。" —— 采纳。
    为什么必须重扫：一挥盖 14 格左右，拿**开场那份固定名单**逐条走位的话，名单立刻过期
      ⇒ 走到一半都是"已经被上一挥割掉的空位"，白白走位（旧版 111 株就走 111 趟）。
    重扫还有个白捡的好处：**那个框不是严丝合缝的矩形**（实测 (12,15) 漏网），
      漏掉的格子下一轮自己会重新冒出来，不用做任何特判。

    返回 (挥了几发, 最后还剩下几株没弄掉)。
    """
    log(f"🌀 范围挥舞（一发盖 ~14 格，每轮重扫）起始成熟 {len(mature0)} 株")
    swings = 0
    leftover = []
    seen_swing = set()          # 已经挥过的 (站位,朝向)
    dead_stands = set()         # 🌟 挥过且**一株没掉**的站位——整格作废，**别再换朝向在同一格转圈**
    t0 = time.time()
    while True:
        _loc, mature, _g = scan_mature()
        cur = {(m["x"], m["y"]) for m in mature}
        if not cur:
            break
        p = get("/state").get("player", {})
        px, py = p.get("x", 0), p.get("y", 0)
        plans = _plan_swings(cur, px, py, exclude=seen_swing, exclude_stands=dead_stands)
        if not plans:
            leftover = sorted(cur)
            log(f"  ⚠️ 剩 {len(leftover)} 株没辙了（能站的站位都挥过了 / 被设施围死），收工")
            break
        sx, sy, face, cover = plans[0]
        seen_swing.add((sx, sy, face))

        before = len(cur)
        _walk_stand(cur_loc, sx, sy)
        post("/face", {"direction": face})
        time.sleep(0.1)
        post("/key", {"key": "confirm"})     # 收获不走 checkAction，要 pressActionButton
        time.sleep(0.45)
        swings += 1

        _l2, m2, _g2 = scan_mature()
        after = len({(m["x"], m["y"]) for m in m2})
        got = before - after
        if got <= 0:
            # 🌟 **整格作废**（不是只废这一个朝向）：恒看到"能挥的范围却一直转"就是因为
            #    这里只排除了 (站位,朝向)，同一格换 90° 还会被再选中一次 ⇒ 原地转圈。
            dead_stands.add((sx, sy))
            log(f"  ⚠️ 站({sx},{sy})朝{face} 这一发一株没掉 → 整个站位作废（不再换朝向原地转）")
        elif swings % 5 == 0 or got < 3:
            # 只报"每5发"和"异常少的一发"，省得刷屏
            log(f"  🌀 第{swings}发 站({sx},{sy})朝{face} 预计盖{cover} 实割{got}｜剩 {after}")

    dt = time.time() - t0
    log(f"🌾 收获完成（镰刀范围）：挥 {swings} 发，用时 {dt:.0f}s，"
        f"从 {len(mature0)} 株收到剩 {len(leftover) if leftover else 0} 株"
        + (f"｜⚠️ 走路兜底瞬移 {TELEPORTS[0]} 次（屏幕上会闪）" if TELEPORTS[0] else "｜全程自然走位（0 次瞬移）"))
    if leftover:
        log(f"  ⏭ 没弄掉的：{leftover[:12]}{' …' if len(leftover) > 12 else ''}")
    return swings, len(leftover)


def _walk_stand(cur_loc, sx, sy):
    """自然走到站位格；**走不到才** `/position` 兜底精确落位。返回 True/False。

    ⚠️ 2026-09-16 **试过"走完一律 /position 收尾、让落点对齐标定口径"，真机反而更差**
      （空挥 7/21 → 14/32：`/position` 是瞬移，跟走路落地的像素/动作状态不是一回事）。
      ⇒ **退回只在走不到时兜底**。剩下那些"预计盖 N 株、实割 0 株"的空挥，成因还没定论，
        别照下面这条已经被证伪的猜想再改一次：
        ~~"`/walk_to` 落 `y*64-32`、`/position` 落 `y*64` 差半格 ⇒ 判定框错位"~~
      真正兜住正确性的是**每轮重扫**（空挥不发散，只是慢），不是这张表准不准。
    """
    post("/walk_to", {"location": cur_loc, "x": sx, "y": sy})
    deadline = time.time() + 15
    while time.time() < deadline:
        p = get("/state").get("player", {})
        if p.get("x") == sx and p.get("y") == sy and not p.get("isMoving"):
            return True
        time.sleep(0.25)
    # ⚠️ 能走到这儿 = 走路没送到（`walk_to` ±2 容差停偏了，或真走不到）⇒ 才瞬移兜底。
    #    恒 2026-09-16："好像还是有 position 的屏幕闪烁" —— 计入 TELEPORTS 并在收工时报出来，
    #    **别靠嘴说"现在没有了"**（上一次"一律瞬移"那版就是这么被看出来的）。
    TELEPORTS[0] += 1
    post("/position", {"x": sx, "y": sy})   # 只落到已验可站的格
    time.sleep(0.2)
    return True


def _find_stand(tx, ty):
    """给作物 (tx,ty) 挑一个**游戏确认站得住**的邻格当站位。返回 (x,y) 或 None。

    ⚠️ **必须问 `/passable`**：洒水器/机器占着的格 `passable=false`，硬瞬移上去再挥工具
       会把那个设施刨成掉落物（2026-09-16 温室实测掉 3 个洒水器就是这么来的）。
    优先**正交**四邻（朝向精确、挥得准），正交都不行再退对角。

    ⚠️ 这是**手摘**路径（一次一株）。铱镰刀是**范围挥舞**，走 `_plan_swing`，别用这个。
    """
    for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0),
                   (1, 1), (-1, 1), (1, -1), (-1, -1)):
        cx, cy = tx + dx, ty + dy
        try:
            if post("/passable", {"x": cx, "y": cy}).get("passable"):
                return cx, cy
        except Exception:
            pass
    return None


def _face_to(sx, sy, tx, ty):
    """从站位 (sx,sy) 朝作物 (tx,ty) 的朝向（SDV：0上 1右 2下 3左）。"""
    if ty < sy:
        return 0
    if ty > sy:
        return 2
    if tx > sx:
        return 1
    return 3


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
        log(f"  {tag} {m['name']} ({m['x']},{m['y']})")
    scythe_only = [m for m in mature if m["scythe"]]
    if args.dry_run:
        return
    if not mature:
        log("🌾 没有可收的成熟作物")
        return

    # ── 🌾 拟人收获：逐个走到作物上方 → 面朝下 → 动手 ──
    # ⚠️ 2026-09-16 恒拍板**改回拟人**。之前这一句是：
    #       h = get("/harvest", {"radius": ...});  log(f"收获结果: {h.get('harvested',0)} 株")
    #    —— C# `HandleHarvest` 一次遍历 `terrainFeatures` 直接 `crop.harvest()`，
    #   **不走路、不挥工具、产物直进背包**（恒："没有挥镰刀也没有一个个摘，直接全作弊进包了…
    #   这真的是我们做的工具吗"）。那次"工具收敛"（2026-09-06）把拟人实现换成了程序化实现。
    # 恒定两条路：**有耕种精通 + 背包有铱镰刀 → 挥镰刀；否则 → 手摘**。
    #   （精通门禁由服务器侧 `_mastery_claimed("farming")` 判——读游戏自己的 `mastery_0` 统计——
    #     用 `--scythe` 传进来。）
    # ⚠️ **挥舞站位待恒校准**：现在沿用 `harvest.py` 的老约定（站作物**上方一格**、面朝下）。
    use_scythe = bool(args.scythe) and bool(scythe)
    if use_scythe:
        post("/select", {"name": scythe})
        log(f"🔪 挥镰刀（{scythe}）")
    else:
        # 手摘：选中一件**工具**使 ActiveObject=null（空手），否则按下去是"用手上的东西"
        post("/select", {"name": "Pickaxe"})
        log("🤚 手摘" + ("（没走镰刀：未领耕种精通 或 背包没有铱镰刀）" if not args.scythe else ""))
        # 🎓 2026-09-16：`cropScythe` 是**游戏自己报的**（`Crop.GetHarvestMethod()` 抄的，
        #    不是本地表的猜测）。它在这儿只干一件事：**如实说"这几株空手够不着"**——
        #    小麦/水稻/芋头这类必须有镰刀，手摘只会把它们**打掉**（不产物的那种）。
        if scythe_only:
            log(f"⚠️ 其中 {len(scythe_only)} 株是**镰刀作物**（{'、'.join(sorted({m['name'] for m in scythe_only}))}），"
                f"手摘收不了——要么去领耕种精通拿铱镰刀，要么背包里带把普通镰刀。")
    time.sleep(0.25)

    cur_loc = get("/state").get("location", {}).get("name", loc)
    done = 0
    skipped = 0

    if use_scythe:
        done, skipped = _harvest_scythe_area(cur_loc, mature)
    else:
        for m in mature:
            tx, ty = m["x"], m["y"]
            # ⚠️ 2026-09-16 恒真机抓到**洒水器被刨**：原来固定站 `(tx, ty-1)`（作物上方一格），
            #    可**洒水器恰恰夹在作物中间**（"中级洒水器布局"）——那个"上方格"往往就是洒水器格，
            #    瞬移站上去再挥镰刀 ⇒ 把洒水器刨成地上掉落物（温室掉了 3 个：2 铱 1 优质，
            #    实测 (8,12)/(12,12) 正下方 (8,13)/(12,13) 都是作物，坐实"站到洒水器上挥"）。
            #    ⇒ **站位格必须先问游戏"这格站不站得住"**（洒水器/机器占着 `passable=false`），
            #      并且**朝着作物的方向**挥，而不是无脑面朝下。
            stand = _find_stand(tx, ty)
            if not stand:
                log(f"  ⚠️ ({tx},{ty}) 四邻没有能站的格（被围死），跳过")
                skipped += 1
                continue
            sx, sy = stand
            _walk_stand(cur_loc, sx, sy)
            post("/face", {"direction": _face_to(sx, sy, tx, ty)})
            time.sleep(0.1)
            post("/key", {"key": "confirm"})     # 收获不走 checkAction，要 pressActionButton
            time.sleep(0.35)
            done += 1
            if done % 10 == 0:
                log(f"  …已收 {done}/{len(mature)}")
        log(f"🌾 收获结果: {done}/{len(mature)} 株（手摘·逐个走位）"
            + (f"，{skipped} 株四邻站不住被跳过" if skipped else ""))

    # 捡掉落（镰刀作物/手摘不进的产物会掉地上）
    time.sleep(0.5)
    picked = collect_debris()
    if picked:
        log(f"🎁 捡起地面掉落 {picked} 个（小麦/干草等）")

    log("✅ 完成")


if __name__ == "__main__":
    main()
