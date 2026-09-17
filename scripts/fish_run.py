"""
自动钓鱼脚本：默认【就地钓】= 就在当前站位原地钓（不传送）；指定 --location → warp 去校准钓点。
→ 启动Fishbot → 监控体力/背包/时间/空抛 → 安全退出。

用法:
    PYTHONIOENCODING=utf-8 python3 fish_run.py [--port 7843]                 # 就地钓（当前站位）
    PYTHONIOENCODING=utf-8 python3 fish_run.py --location Beach --max-casts 20  # 去某钓点

参数:
    --port          NagiBridge端口（默认 7843，AI 角色）
    --location      钓点名（Beach/Mountain/Forest/Town）。不带=就地钓（当前站位；开局一次性 isFishing 判能否抛）
    --max-casts     抛 N 竿就收手（0=不限，钓到体力<20/背包满/太晚/抛不出去停）
    --stamina-pct   体力低于此百分比停止（默认 15）
"""

import sys
import time
import argparse
import json
import urllib.request

# ⚠️ 2026-08-15 恒：FISHING_SPOTS 是原版作者的硬编码坐标（没校准），弃用！
# 改用我们 locations.py 校准的钓点 POI（✅ 已验证的）。face 为抛竿朝向（待实测校准）。
FISHING_TARGETS = {
    "Beach":    ("海滩钓鱼点(码头)", 2),   # (52,25) 码头，面下
    "Mountain": ("山湖钓鱼点(左)", 2),     # (68,24) 山湖左岸，面下（2026-09-05 恒：抛竿朝向下更准）
    "Forest":   ("森林小池塘钓点", 2),     # (34,25) 猪车旁小池塘，面下
    "Town":     ("镇鲶鱼钓点", 2),        # (3,93) 雨天鲶鱼钓点（2026-08-15恒：暴雨天钓鲶鱼）
}
FISHING_SPOTS = {
    "Beach": {"x": 42, "y": 36, "face": 2},
    "Mountain": {"x": 69, "y": 14, "face": 2},
    "Forest": {"x": 69, "y": 28, "face": 2},
}


def get_spot(location):
    """解析钓点：优先用我们 locations.py 的校准 POI，原版 FISHING_SPOTS 兜底。"""
    from locations import POI
    if location in FISHING_TARGETS:
        name, face = FISHING_TARGETS[location]
        poi = POI.get(name)
        if poi:
            return {"x": poi["pos"][0], "y": poi["pos"][1], "face": face, "poi": name}
    return FISHING_SPOTS.get(location)

CHECK_INTERVAL = 5
STARTUP_WAIT = 5    # 🎣 一次性开局判定：fishbot 开后等 STARTUP_WAIT(s)，isFishing(等咬钩) 建立=能抛；没建立=没水→收手
def log(msg):
    try:
        print(f"[fish] {msg}", flush=True)
    except UnicodeEncodeError:
        # ⚠️ GBK 控制台 emoji 编码炸弹：encode 成控制台编码(replace emoji)再 decode → 安全打印（恒 2026-08-23 修）
        enc = sys.stdout.encoding or "utf-8"
        print(f"[fish] {msg.encode(enc, errors='replace').decode(enc)}", flush=True)


class FishBot:
    def __init__(self, port):
        self.base = f"http://localhost:{port}"

    def _get(self, ep):
        return json.load(urllib.request.urlopen(f"{self.base}{ep}", timeout=10))

    def _post(self, ep, data=None):
        req = urllib.request.Request(
            f"{self.base}{ep}",
            json.dumps(data or {}).encode(),
            {"Content-Type": "application/json"},
        )
        return json.load(urllib.request.urlopen(req, timeout=10))

    def state(self):
        return self._get("/state")

    def warp(self, location):
        return self._post("/warp", {"location": location})

    def move_to(self, x, y):
        # ⚠️ /move 是 BFS 路径规划有病（CLAUDE.md 2026-07-26，森林农场会撞墙/绕远）。
        # 改游戏自带 /walk_to（跨地图自动找有效着地点），按位置判断到达。
        loc = (self.state().get("location") or {}).get("name", "Farm")
        self._post("/walk_to", {"location": loc, "x": x, "y": y})
        deadline = time.time() + 12
        while time.time() < deadline:
            s = self.state()
            p = s["player"]
            if abs(p.get("x", 0) - x) <= 2 and abs(p.get("y", 0) - y) <= 2 and not p.get("isMoving"):
                return True
            time.sleep(0.4)
        self._post("/stop")
        return False

    def face(self, direction):
        self._post("/face", {"direction": direction})

    def select(self, name):
        return self._post("/select", {"name": name})

    def sell(self):
        return self._post("/sell", {})

    def fishbot(self, action):
        return self._post("/fishbot", {"action": action})

    def stamina_pct(self):
        s = self.state()
        p = s["player"]
        return (p["stamina"] / p["maxStamina"] * 100) if p["maxStamina"] > 0 else 0

    def game_time(self):
        s = self.state()
        return s.get("time", {}).get("timeOfDay", 600)

    def inventory_space(self):
        # ⚠️ 2026-08-14 双重修复：/state 的 inventory 在顶层（非 player）+ 是压缩列表（只含非空格）
        # → 总容量要用 player.maxItems，不能 len(inv)（否则永远算 0 空格）
        s = self.state()
        inv = s.get("inventory") or []
        total = s.get("player", {}).get("maxItems", 36)
        used = sum(1 for i in inv if i)
        return total - used

    def key(self, key):
        return self._post("/key", {"key": key})

    def close_menu(self, tries=3, gap=0.35):
        """用**端点**把挡路的菜单关掉（`/menu_close`），返回是否真关掉了。

        ⚠️ 2026-09-17 恒：「fishbot 开菜单叫你补鱼饵，这个优化真是太蠢了 —— 加一个自动关掉吧。」
        症结不是"没自动关"，是**关不掉**：原来只 `key("esc")` —— `/key` 是**合成按键**，
           AI 窗口在后台时**根本不生效**（真机：关一次读回来还在、再关一次还在 ⇒ 判"关不掉"停脚本），
           而同族坑早就记过（`ReadyCheckDialog` 不响应 Escape、只有端点能关）。
           ⇒ 改用端点：它直接改游戏状态、**不挑窗口焦点**（手动 POST /menu_close 一次就干净了）。
        为什么带重试：fishbot 补饵是**反复弹**的，关一次可能正好撞在它又弹的那一帧上。
        """
        for _ in range(max(1, tries)):
            try:
                self._post("/menu_close", {})
            except Exception:
                pass
            time.sleep(gap)
            try:
                if not ((self.state().get("activeMenu") or {}).get("type")):
                    return True
            except Exception:
                pass
        return False

    def count_fish(self):
        s = self.state()
        p = s["player"]
        fishing = p.get("fishing", {})
        return fishing

    def is_fishing(self):
        s = self.state()
        f = s["player"].get("fishing", {})
        return f.get("isFishing", False) or f.get("isCasting", False) or f.get("isReeling", False)


def finish_cast(bot, reason):
    """🎣 停止条件触发：关鱼机自动抛(防停不掉/再抛下一竿) + cancel 收线(即时停，小游戏中也可退出)。
    竿抛着没咬(isFishing/isCasting)→ cancel 收线(实时)；正收线(isReeling)→ 短等放行当前竿(钓上就钓上，
    没钓上也无妨——能即时停就是好事，恒 2026-09-05 确认)。不再问 AI 等一杆钓完/别操作。"""
    try:
        bot.fishbot("off")   # 停自动抛竿（≠收杆，线留在原地）——"钓完这竿就暂停、不抛下一竿"的关键
    except Exception:
        pass
    time.sleep(2)   # 给当前竿收完的时间（钓完这一竿）
    try:
        for _ in range(3):
            try:
                bot.key("cancel")   # 竿还悬着/还在收 → 收线（实时终止，别让竿悬空/声效残留）
            except Exception:
                pass
            time.sleep(0.3)
    except Exception:
        pass
    log(f"⏸ 已停钓（{reason}，鱼机已关、竿已收）")


def run(port, location, max_casts=0, stamina_pct=15, no_sleep=False):
    bot = FishBot(port)

    st = bot._get("/status")
    if not st.get("worldReady"):
        log("world not ready")
        return

    at_current = location is None or location in ("", "here", "current")

    spot = None
    if at_current:
        log("=== fish run: 当前站位（就地钓，不自动去钓点）===")
    else:
        spot = get_spot(location)
        if not spot:
            log(f"unknown fishing spot: {location}, known: {list(FISHING_TARGETS.keys())}")
            return
        poi_tag = f" ({spot.get('poi', '')})" if spot.get("poi") else ""
        log(f"=== fish run: {location}{poi_tag} ({spot['x']},{spot['y']}), max_casts={max_casts or '不限'} ===")

    # ⚠️ 先走到钓点再拿竿：鱼竿在路上就装备会"边走边钓"的样子（恒 2026-08-14 点名）
    rod_names = ["Iridium Rod", "Fiberglass Rod", "Training Rod", "Bamboo Pole"]

    # 走路前先把鱼竿收起来（选个别工具），避免上次钓完竿还在手上、走路像在抛竿
    for putaway in ("Pickaxe", "Axe", "Hoe", "Scythe"):
        r = bot.select(putaway)
        if r.get("ok"):
            time.sleep(0.3)
            break

    if at_current:
        # 🎯 就地钓（恒 2026-08-23 定稿：只用 isFishing 当判据——鱼塘建筑/节日搭建水 isWaterTile 检不出，
        # 瓦片扫描会误杀）。不预扫不转向，开 fishbot 让它自己找水抛；抛没抛到水里交给监控 isFishing 判定。
        log("  🎯 就地钓: 当前站位（不预扫不转向；抛没抛进水由 isFishing 判定）")
    else:
        # 🎣 导航到钓点：用 /walk_to 跨图找路（mod 自动找入口+走路），别 warp 回农场再跳（恒 2026-09-05：
        #    钓鱼该像 map_go 一样走真实路径，不是一上来就瞬移）。/walk_to 跨图=选目标图入口 warp + 走，比"回农场再跳"干净。
        r = bot._post("/walk_to", {"location": location, "x": spot["x"], "y": spot["y"]})
        if not r.get("ok"):
            log(f"walk_to 到 {location} 失败: {r.get('error', '?')}")
            return
        # 等到达钓点（真站到 spot 附近、停下才继续）
        deadline = time.time() + 40
        while time.time() < deadline:
            s = bot.state()
            p = s["player"]
            cur = (s.get("location") or {}).get("name", "") or ""
            if cur == location and abs(p.get("x", 0) - spot["x"]) <= 2 and abs(p.get("y", 0) - spot["y"]) <= 2 and not p.get("isMoving"):
                break
            time.sleep(0.5)
        bot.face(spot["face"])
        time.sleep(0.3)

    # 到钓点了，现在拿竿（warp/走路可能重置选中，这里重新装备）
    rod_found = False
    for name in rod_names:
        r = bot.select(name)
        if r.get("ok"):
            log(f"selected: {name}")
            rod_found = True
            break
    if not rod_found:
        log("no fishing rod found!")
        return

    # 🎣 2026-08-29 恒：拿竿后自动上饵/钓具。背包有饵但竿上没饵→上饵；可上钓具且背包有→上钓具。
    #    多数情况背包没饵（要拿虫肉合成/买），这步只在有货时兜底；没货就裸竿钓，靠注入提示去补货。
    try:
        rs = bot.state()
        rinfo = (rs.get("player") or {}).get("rod") or {}
        if not rinfo.get("bait") and (rinfo.get("baitInBag") or 0) > 0:
            rb = bot._post("/rod", {"action": "bait"})
            if rb.get("ok"):
                log(f"auto-bait: {rb.get('equipped')}")
        if rinfo.get("canTackle") and not (rinfo.get("tackle") or []) and (rinfo.get("tackleInBag") or 0) > 0:
            rt = bot._post("/rod", {"action": "tackle"})
            if rt.get("ok"):
                log(f"auto-tackle: {rt.get('equipped')}")
    except Exception as e:
        log(f"autorebait skipped: {e}")

    s = bot.state()
    p = s["player"]
    log(f"pos: ({p['x']},{p['y']}) tool: {p['currentTool']} stamina: {p['stamina']}")
    initial_stamina = p["stamina"]

    # 🚧 2026-09-17 恒：**开钓前先清场**。fishbot 补饵会弹 GameMenu，菜单一开**鱼根本抛不出去**
    #    ⇒ 下面那段启动判定会把"菜单挡着"误判成"抛竿方向没有水"，5 秒直接收手（真机实测：5s 退出、
    #    菜单还杵在那儿）。⚠️ 而且原来**只把关菜单写在了 monitor loop 里** —— 脚本压根走不到那里就被
    #    启动判定 `return` 了，那段逻辑等于挂在一条**永远到不了**的路上。
    _m0 = (bot.state().get("activeMenu") or {}).get("type")
    if _m0 and _m0 != "BobberBar":
        log(f"  🚧 开钓前挡着「{_m0}」→ 先关掉")
        if not bot.close_menu():
            bot.fishbot("off")
            log(f"  ⚠️ 关不掉「{_m0}」，收手（可手动 POST /menu_close）")
            return

    # start fishbot
    r = bot.fishbot("on")
    log(f"fishbot: {r}")

    # 🎯 一次性轻量判定（恒 2026-08-23 定稿）：水域固定，能抛一杆就能抛很多竿 → 只在开头确认一次。
    # isFishing(等咬钩) 抛竿后 ~3s 内建立 = 抛到水、可钓；STARTUP_WAIT(5s) 都没建立 = 抛不进水里/没水 → 收手。
    # ⚠️ 2026-09-17：启动期内**再被菜单挡**就当场关掉、且**这秒不算数**（补饵是反复弹的，
    #    不这样 5 秒会被它耗光、照样误判成"没有水"）。加了硬上限，别被反复弹拖成死循环。
    _ok = False
    _hard_end = time.time() + STARTUP_WAIT + 20
    _deadline = time.time() + STARTUP_WAIT
    while time.time() < _deadline and time.time() < _hard_end:
        time.sleep(1)
        _st = bot.state()
        _mt = (_st.get("activeMenu") or {}).get("type")
        if _mt and _mt != "BobberBar":
            log(f"  🚧 启动期被「{_mt}」挡住 → 关掉再等（这秒不计）")
            bot.close_menu()
            _deadline += 1.5
            continue
        _f = (_st.get("player") or {}).get("fishing") or {}
        if _f.get("isFishing"):
            _ok = True
            break
    if not _ok:
        log("🚫 抛竿方向没有水，请调整站位或朝向（isFishing 未建立）")
        bot.fishbot("off")
        # 收杆兜底：鱼漂若在空中/甩着，按 cancel 收回
        for _ in range(4):
            _f = (bot.state().get("player") or {}).get("fishing") or {}
            if not (_f.get("isFishing") or _f.get("isReeling") or _f.get("isCasting")):
                break
            bot.key("cancel")
            time.sleep(0.8)
        return
    log("🎯 能抛，开始钓（水域固定，之后无需再判死水）")

    # monitor loop（max_casts=0 不限竿数 → 钓到体力<20 / 背包满 / 太晚才停）
    # 2026-09-01 恒拍板：0.6.1 fishbot 自动玩，靠"体力降/8"估抛竿的旧法（每轮降幅<8→恒0）已废 → 计数恒 0。
    #   改按**状态边沿**数：not钓鱼→钓鱼 = 抛一竿；not reel→reel ≈ 钓上一条（fishbot 自动玩必胜）。
    #   采样 3s→2s 提分辨率；极快竿漏掉可接受（不影响 max_casts 大致收手）。标签诚实化：抛N竿·钓上M条。
    cast_count = 0       # 抛竿数（边沿：not钓鱼 → 钓鱼）
    fish_caught = 0      # 钓上数（近似：not reel → reel）
    check_counter = 0
    prev_fishing = False
    prev_reeling = False

    while True:
        time.sleep(2)

        s = bot.state()
        p = s["player"]
        current_stamina = p["stamina"]
        fishing = (p.get("fishing") or {})
        is_fishing = bool(fishing.get("isFishing") or fishing.get("isCasting") or fishing.get("isReeling"))
        is_reeling = bool(fishing.get("isReeling"))

        # 抛竿/钓上：进入对应状态的一次边沿
        if is_fishing and not prev_fishing:
            cast_count += 1
        if is_reeling and not prev_reeling:
            fish_caught += 1
        prev_fishing = is_fishing
        prev_reeling = is_reeling

        if max_casts > 0 and cast_count >= max_casts:
            finish_cast(bot, f"达到 {max_casts} 竿")   # 🎣 2026-09-05：钓完这竿暂停，不抛下一竿
            break

        check_counter += 1
        if check_counter >= CHECK_INTERVAL:
            check_counter = 0

            # ⚠️ 2026-08-14 防呆：菜单挡住（BobberBar=钓鱼小游戏 fishbot 正在自动玩，不能关）
            # 🐟 2026-08-23 恒：满包又钓上鱼 → ItemGrabMenu(鱼在待领槽)。enum引导：先丢替换物领鱼；没有→ok 放弃这条鱼。
            # 🎪 2026-09-01：fishbot 默认pause配置还会开背包补饵(GameMenu)——别一遇菜单就停，esc关+补饵后继续；真关不掉才停。
            menu = s.get("activeMenu") or {}
            mtype = menu.get("type")
            if mtype and mtype != "BobberBar":
                log(f"  ⚠️ 菜单挡住: {mtype}")
                try:
                    if mtype == "ItemGrabMenu":
                        # 🐟 满包接鱼/箱子（恒拍板 2026-08-23）：停脚本，菜单留给 AI 手动处理——
                        #   用 menu_claim_swap(替换物名∈背包) 指定丢哪个，不自动丢（丢错亏大）。
                        log("  🎒 满包接鱼(ItemGrabMenu)→ 停脚本交 AI 手动：menu click action=discard item=低价值物(丢桶腾格) 再 "
                            "action=claim item=鱼名 领取；不想要就 menu click(button=ok) 直接退出放弃这条鱼；处理完再跑 fish_run")
                        break
                    # 其它菜单（GameMenu=fishbot 开背包补饵等）：**先满足它、再用端点关**（恒 2026-09-17）
                    #   顺序有讲究：先补饵是**治本** —— 包里有饵 fishbot 就不再反复弹；没有也无妨，下面照样关得掉。
                    try:
                        bot._post("/rod", {"action": "bait"})
                    except Exception:
                        pass
                    if bot.close_menu():
                        log(f"  已关掉挡路菜单 {mtype}，继续钓")
                    else:
                        log(f"  ⚠️ 菜单关不掉: {mtype}，停止钓鱼"
                            f"（可手动 POST /menu_close；别再用 /key esc——后台窗口不生效）")
                        break
                except Exception as e:
                    log(f"  ⚠️ 关菜单出错: {e}，停止钓鱼")
                    break

            # 🎒 背包提示（恒拍板：满包不一定停——重复鱼能堆叠；停只由"满包待领界面"菜单触发）
            space = bot.inventory_space()
            if space <= 2:
                log(f"  🎒 背包只剩 {space} 格（重复鱼会堆叠；遇非堆叠接鱼→弹满包待领界面才停，交 AI 手动）")

            # check stamina（绝对值 20，百分比不合理因为星之果实会拉高上限）
            if current_stamina < 20:
                finish_cast(bot, f"体力不足 ({current_stamina}/{p['maxStamina']})")   # 钓完这竿暂停
                break

            # check time
            game_time = s.get("time", {}).get("timeOfDay", 600)
            if game_time >= 2300:
                finish_cast(bot, f"太晚 ({game_time})")   # 钓完这竿暂停
                break

            sta_pct = bot.stamina_pct()
            log(f"  check: stamina {sta_pct:.0f}%, time {game_time}, 抛~{cast_count}竿 · 钓上~{fish_caught}条")

    # stop fishbot
    bot.fishbot("off")
    log(f"fishbot off, 抛{cast_count}竿 · 钓上{fish_caught}条")

    # 🎣 收杆：鱼线还甩着（isFishing/isReeling/isCasting）就按 cancel(=use-tool) 收线，
    # 避免直接传送后"嘎啦嘎啦"收线音效一直残留。
    for _ in range(8):
        f = (bot.state().get("player") or {}).get("fishing") or {}
        if not (f.get("isFishing") or f.get("isReeling") or f.get("isCasting")):
            break
        bot.key("cancel")
        time.sleep(0.8)
    log("收杆完成（鱼线已收回）")

    # go home and sleep（--no-sleep 时不睡，测试用/留给全自动循环决定）
    if not no_sleep:
        bot.warp("FarmHouse")
        time.sleep(1.5)
        try:
            bot._post("/sleep", {})
            log("went to bed")
        except Exception:
            log("warped home (sleep failed)")
    else:
        log("no-sleep: 留在钓点不睡觉")
    log("=== fish run complete ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7843)  # 2026-08-14: 默认 AI 端口（曾 7842 误控 host）
    parser.add_argument("--location", default=None)  # None=就地钓（当前站位）；指定=去钓点 warp
    parser.add_argument("--max-casts", type=int, default=0,
                        help="抛 N 竿就收手（0=不限，钓到体力<20/背包满/太晚停）")
    parser.add_argument("--stamina-pct", type=int, default=15)
    parser.add_argument("--no-sleep", action="store_true")   # 测试用：不自动睡
    args = parser.parse_args()

    run(args.port, args.location, args.max_casts, args.stamina_pct, no_sleep=args.no_sleep)
