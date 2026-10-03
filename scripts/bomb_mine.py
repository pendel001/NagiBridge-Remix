"""
💣 bomb_mine.py — 炸矿模式（自主下矿，贪心炸弹覆盖）

每层：扫全层岩体 → 贪心找"能覆盖最多岩体的空格"放炸弹 → 躲远 → 等爆炸 → 拾取 →
循环到目标层。生存优先：血量阈值吃食物/撤退、炸弹库存不足撤退、卡死检测。
玩家(user)检测：user在矿井里就一起冲层（目标层 = user层数 ± lead），user不在就自己冲。

背包规划：快满时输出背包清单，自动丢低价值物品（Stone/Quartz 等）腾格；
--autodrop 0 时改成"报清单并撤退"，把取舍决策留给 AI。

用法:
  python bomb_mine.py --target 80                 # 冲到80层（自动从炸矿进度恢复）
  python bomb_mine.py --no-resume --start 1 --target 40
  python bomb_mine.py --bomb "Mega Bomb" --min-covered 5
  python bomb_mine.py --follow-host 0              # 不一起冲层，自己冲
  python bomb_mine.py --autodrop 0                 # 背包满时只报规划不自动丢
  python bomb_mine.py --check-progress / --reset-progress
"""

import sys
import os
import json
import time
import argparse
import requests

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
os.environ.setdefault("NAGI_HOST_URL", "http://localhost:7842")

from bomb_common import (BombMiner, log, is_mine_location, extract_mine_level,
                         ManualChestFull, parse_food_list,
                         BOMB_RADIUS, backpack_plan, is_rock, drop_value, is_volcano)

# ═══════════ 已移除：主动打怪（2026-08-09，按user要求移到协同模式） ═══════════
# 移除原因：position 瞬移后足够安全，bomb_mine 只炸矿不主动打怪；战斗归协同模式。
# 注意：受击反击（retaliate_if_hit / inline 回击）保留当防御。
# 若要恢复，把下面两段放回原处即可：
#
# 【原主循环】clear_floor 每轮开头（在受击反击之后）：
#             # 主动打怪（AI 周围 2 格，追击到死；有怪就优先处理）
#             if self.combat_aggressive(engage_dist=2):
#                 continue
#
# 【原感染层】clear_floor 感染层检测里的"没石头清怪出梯子"：
#                 # 没石头：清怪（怪死可能出梯子）
#                 if rcount <= 0:
#                     log("  没石头，清怪出梯子")
#                     self.combat_aggressive(engage_dist=4)
#                     time.sleep(1.0)
#                     ladder = self.find_ladder()
#                     if ladder and can_descend() and self.descend():
#                         return "DONE", self.my_mine_level()
# ═══════════════════════════════════════════════════════════════════════════

PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bomb_progress.json")
ORGANIZE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bomb_organize.json")

# ⭐ 2026-08-23 恒：禁用所有矿井的逐层整理（竖井一跳3~15层，逐层等AI响应一次太慢→暴毙概率大增），
# 只留异步后台整理（连续模式后台跑，背包满自动停本层拾取、不逐层停等AI）。要恢复改 True。
PER_FLOOR_ORGANIZE = False


def load_organize_state():
    """整理背包状态：{disabled: AI判定后续不需要再整理, floors_since_organize: 距上次整理层数}"""
    try:
        with open(ORGANIZE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_organize_state(state):
    try:
        with open(ORGANIZE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception:
        pass

# 每层最多炸几次（防止卡一层死循环）
MAX_FLOOR_ATTEMPTS = 25
# 卡死判定：连续这么多轮"位置没变 + 没炸成"就撤退
STUCK_ROUNDS = 8
# 炸弹爆炸后捡掉落的时间预算（秒）
COLLECT_BUDGET = 30

# ═══════════ 协同（增援user）═══════════
CO_LOCATED_DIST = 10     # user同层且曼哈顿距离≤此值=贴身（增援站user旁边用）
COOP_CHECK_EVERY = 1     # clear_floor 每次迭代都查user（增援要灵敏，错过窗口就打不到了）
COOP_BURST_SEC = 12      # 增援限时（打完user的对手就回来炸矿）
# 🎁 协同里多久扫一次宝箱（拍数，1 拍≈0.6s）。主循环是"每层开一次"，协同没有"层"的概念
#    ⇒ 用节流。⚠️ 别设太小：`open_treasure_chests()` 内部是 `surroundings(30)` 大扫描
#    （矿层整张图，回包几百 KB）⇒ 15 拍≈9s 是"跟得上逛房间"和"别把 API 打满"之间的折中。
COOP_CHEST_EVERY = 15
LEAD_MAX = 3             # AI 领先user超过这层数就传送回user身边（不然一个人在前面挨打）


def load_progress():
    try:
        with open(PROGRESS_FILE) as f:
            return json.load(f).get("deepest_level", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0


def save_progress(deepest):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump({"deepest_level": deepest}, f)


def _current_mine_level(port):
    """读 AI 当前所在层（在矿里返回内部层号，不在矿里返回 None）。"""
    try:
        s = requests.get(f"http://localhost:{port}/state", timeout=10).json()
    except Exception:
        return None
    loc = (s.get("location") or {}).get("name", "")
    return extract_mine_level(loc or "")


def resume_start_level(port=None):
    """头骨矿洞恢复层（恒 2026-08-23）：在矿里→原地续当前层；不在矿里→一律 121（第一层），
    不准从进度续层（头骨进度只在坑里层层爬才连续，出了坑就回第一层）。"""
    if port is not None:
        cur = _current_mine_level(port)
        if cur is not None and cur >= 121:
            return cur
        return 121
    deepest = load_progress()
    if deepest >= 121:
        return 121
    if deepest < 5:
        return 1
    return (deepest // 5) * 5


def _is_town_mine(loc: str) -> bool:
    """是否为鹈鹕镇矿井（Mine 建筑 或 UndergroundMine≤120）。头骨(≥121)/火山不算。"""
    if loc == "Mine":
        return True
    lv = extract_mine_level(loc or "")
    return lv is not None and lv <= 120


def _town_elevator_start(port: int) -> int:
    """动态读鹈鹕镇矿井电梯可达最高层（替代静态进度，2026-08-22）。
    在鹈鹕镇矿井就读电梯，否则按起始=1。读失败兜底 1（宁可重下，不跳错层）。"""
    base = f"http://localhost:{port}"
    try:
        s = requests.get(f"{base}/state", timeout=10).json()
    except Exception:
        return 1
    loc = (s.get("location") or {}).get("name", "")
    if not _is_town_mine(loc):
        return 1
    try:
        r = requests.get(f"{base}/mine/elevator", timeout=10).json()
        if r.get("ok"):
            return max(1, int(r.get("maxFloor", 1) or 1))
    except Exception:
        pass
    return 1


class BombMineBot(BombMiner):
    """自主炸矿矿工"""

    def __init__(self, port, host_port, bomb_type="Bomb", min_covered=4,
                 hp_threshold=30, follow_host=True, lead=2, autodrop=0, weapon=None):
        super().__init__(port=port, host_port=host_port, bomb_type=bomb_type)
        self.min_covered = min_covered
        self.hp_threshold = hp_threshold
        self.follow_host = follow_host
        self.lead = lead
        self.autodrop = autodrop
        self.weapon_override = weapon   # 武器绑定（bomb_mine --weapon "Galaxy Hammer"）
        self._last_pos = None
        self._stuck_rounds = 0
        self.coop_handoff = False   # 2026-08-22 没炸弹+玩家在同矿井 → 转协同交棒，不自主撤退出矿

    def preflight(self):
        """启动预检（2026-08-16 恒）：返回硬性拦截原因（str=阻止启动）；黄色警告只 log 不拦。
        硬：没炸弹 / 血量过低；黄：没武器。"""
        try:
            s = self.state()
            p = s.get("player", {})
            mhp = p.get("maxHealth") or 1
            hp = p.get("health") or 0
            if mhp > 0 and hp * 100 / mhp < self.hp_threshold:
                return f"❌ 当前血量 {hp}/{mhp}（{hp*100/mhp:.0f}%）低于阈值 {self.hp_threshold}%——先回血/睡觉再来"
            if not self.choose_bomb_type():
                return f"❌ {self.absent_cause()}"
            if not self.detect_weapon():
                log("  ⚠️ 没找到武器（炸矿也能切镐子，但危险，建议带剑）")
        except Exception as e:
            log(f"  ⚠️ 预检异常（继续启动）: {e}")
        return None

    # ── 卡死检测 ──

    def _update_stuck(self):
        """卡死检测：**只看位置有没有变**（连续 STUCK_ROUNDS 轮没挪窝 ⇒ 判卡死）。
        ⚠️ 2026-09-19 修：原签名是 `_update_stuck(bombed)`，而"成功放弹"那处调用传的是
        **字面量 `True`** ⇒ `if bombed or ...` 恒成立 ⇒ 计数恒清零 ⇒ 返回值恒 False ⇒
        整段"卡死 → 扫整层找铱矿密集点 → 作弊给楼梯下楼"**从来没执行过一次**。
        改动理由：判据本就该是"人在不在动"，放没放弹不代表没卡死（正是"边放弹边原地打转"
        才需要被认出来）。所以把 `bombed` 这个参数**整个删掉**，不保留一个会误导的默认值。"""
        s = self.state()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        pos = (px, py)
        if self._last_pos != pos:
            self._stuck_rounds = 0
        else:
            self._stuck_rounds += 1
        self._last_pos = pos
        return self._stuck_rounds >= STUCK_ROUNDS

    def walk_to_rich_ore(self, level):
        """卡死兜底：扫整层按铱/金矿密集点走路（优先高价值），走到继续炸。返回是否移动。"""
        try:
            data = self.surroundings(30)
            rocks = [(t["x"], t["y"], t.get("object")) for t in data.get("tiles", [])
                     if is_rock(t.get("object"))]
        except Exception:
            return False
        if not rocks:
            return False
        s = self.state()
        px, py = s["player"]["x"], s["player"]["y"]
        rocks.sort(key=lambda r: (0 if "Iridium" in r[2] else 1 if "Gold" in r[2] else 2,
                                  abs(r[0] - px) + abs(r[1] - py)))
        tx, ty = rocks[0][0], rocks[0][1]
        adj = self.find_stand_tile(tx, ty, set())
        if adj[0] is not None:
            tx, ty = adj[0], adj[1]
        self.natural_walk(tx, ty, self.my_location(), walk_only=True)  # 纯走路（防 position 传送出界）
        time.sleep(0.5)
        s2 = self.state()
        return (s2["player"]["x"], s2["player"]["y"]) != (px, py)

    def cheat_staircase(self):
        """作弊给楼梯（保底）：给 99 石头 → craft 造。warp 不可行/卡死时用。返回是否造出。"""
        try:
            self._post("/give", {"id": "390", "count": 99})
            time.sleep(0.5)
            if self.craft_staircase():
                log("  🧨 作弊造出楼梯")
                return True
        except Exception:
            pass
        return False

    # ── 协同（检测user在身边）──

    def host_proximity(self):
        """读user一次，返回 ('absent'|'far'|'near', host_level, dist)。
        near = user同层 且 曼哈顿距离≤CO_LOCATED_DIST（贴身，触发协同）。"""
        try:
            hs = self.host_state()
        except Exception:
            return "absent", 0, 9999
        hl = hs.get("location", {}).get("name", "")
        if not is_mine_location(hl):
            return "absent", 0, 9999
        hlv = extract_mine_level(hl) or 0
        h = hs.get("player", {})
        hx, hy = h.get("x", 0), h.get("y", 0)
        s = self.state()
        px, py = s["player"]["x"], s["player"]["y"]
        dist = abs(hx - px) + abs(hy - py)
        near = hlv == self.my_mine_level() and dist <= CO_LOCATED_DIST
        return ("near" if near else "far"), hlv, dist

    def reinforce_host(self):
        """user同层在打架 → position 到user旁边 → 只打user的对手（health<maxHealth 的血量不满怪），
        满血怪（user没在打的）不纠缠。限时爆发做完回来炸矿。
        user不在矿/不同层/没打架 → 返回 False（AI 完全自主炸矿）。"""
        if not self.follow_host:
            return False
        try:
            hs = self.host_state()
            hl = hs.get("location", {}).get("name", "")
            if not is_mine_location(hl) or extract_mine_level(hl) != self.my_mine_level():
                return False  # 不同层：楼层同步由 goal 机制管，不在这增援
            h = hs.get("player", {})
            hx, hy = h.get("x", 0), h.get("y", 0)
        except Exception:
            return False
        # user同层但没在打架（周围没有血量不满的怪）→ 不增援
        try:
            data = self.surroundings(8, host=True)
            damaged = [m for m in data.get("monsters", [])
                       if m.get("health", 0) < m.get("maxHealth", 1)]
        except Exception:
            return False
        if not damaged:
            return False
        log(f"  🤝 user在打架（{len(damaged)}只血量不满），增援！")
        # position 到user旁边一格（别叠身上）
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if self.position_safe(hx + dx, hy + dy):
                break
        # 只打user的对手（限时）
        deadline = time.time() + COOP_BURST_SEC
        while time.time() < deadline:
            st = self.combat_aggressive(around=(hx, hy), host_targets_only=True)
            if st == "unsafe":
                # ⚠️ 增援不是送命：危险了先收手，回主循环自保（原来这里把 unsafe 当成"继续打"）
                log(f"  ⚠️ 增援中状态危险（{self.unsafe_reason()}），收手")
                break
            if st == "clear":
                break
            time.sleep(0.3)
        return True

    # ── 整理背包（逐层模式）──

    def organize_interval(self):
        """整理触发间隔（层数）：鹈鹕镇矿洞困难模式（神庙 mineShrineActivated）满包后每1层，其他每3层。
        只认 UndergroundMine 1-120 且 mineHardMode；头骨矿洞(121+)/火山不因神庙激活按困难算。
        ⚠️ mineHardMode 在 /state 的 player 子对象里（不是顶层）。"""
        try:
            s = self.state()
            p = s.get("player", {})
            loc = s.get("location", {}).get("name", "")
            lv = extract_mine_level(loc) or 0
            if (p.get("mineHardMode") and is_mine_location(loc)
                    and not is_volcano(loc) and lv < 121):
                return 1
        except Exception:
            pass
        return 3

    def build_summary(self, level, target_floor, organize_suggested, os_state,
                      retreat_reason=None):
        """逐层模式结构化摘要（JSON），给 AI 参考背包/掉落。organize_suggested 恒 False（恒 2026-08-23：
        逐层整理已禁），不再主动提示 AI 层间整理——清包交给异步后台；摘要只为可见性。"""
        s = self.state()
        p = s.get("player", {})
        inv = [{"name": i.get("name"), "stack": i.get("stack"),
                "value": i.get("value") or 0, "q": i.get("quality") or 0}
               for i in s.get("inventory", []) if i.get("name")]
        drops = []
        try:
            d = self.debris()
            for it in d.get("debris", []):
                nm = it.get("itemName", "?")
                drops.append({"name": nm, "x": it.get("x"), "y": it.get("y"),
                              "value": drop_value(nm)})
            drops.sort(key=lambda x: -x["value"])
        except Exception:
            pass
        free = self.inventory_free_slots()
        rec = [f"{x['name']}(@{x['x']},{x['y']})≈{x['value']}g" for x in drops[:4]]
        return json.dumps({
            "mode": "one_floor",
            "floor": level,
            "target": target_floor,
            "progress": load_progress(),
            "organize_suggested": organize_suggested,
            "organize_interval": self.organize_interval(),
            "mine_hard_mode": s.get("player", {}).get("mineHardMode", False),
            "floors_since_organize": os_state.get("floors_since_organize", 0),
            "organize_disabled": os_state.get("disabled", False),
            "backpack_free": free,
            "backpack": inv[:12],
            "nearby_drops": drops[:8],
            "recommended_pickups": rec,
            "hp": f"{p.get('health')}/{p.get('maxHealth')}",
            "stamina": f"{p.get('stamina', 0):.0f}/{p.get('maxStamina')}",
            "bombs_left": self.count_all_bombs(),
            "retreat_reason": retreat_reason,
        }, ensure_ascii=False)

    # ── 每层一次完整炸矿 ──

    def _run_cooperate(self):
        """2026-08-22 恒：没炸弹+玩家(恒)在同矿井 → 转【内部】协同保镖：跟随恒+帮忙敲矿/打怪+开路。
        自动接棒、不经 AI 主动启用（**沙漠这档只能被动起**：恒 2026-10-03 确认过；
        `bomb_escort.py` 那个独立脚本**已删**——协同本来就只是这段内联代码）。

        结束/交回的三条路（恒 2026-10-03 晚：「确认一下这个协同可以在异步时随时结束，
        以及随时得到炸弹能『复活』成炸矿模式」）：
          ① **随时结束**：`mine bomb_retreat`（MCP 单步工具）⇒ 先 `/guard off` 再 kill 本进程
             + `retreat_to_entrance` 把人传出矿井 ⇒ 本循环下一拍就因"不在矿井"退出。
             ⚠️ kill 是 `TerminateProcess`（没 finally），所以 guard 那一刀由 MCP 侧补（见 `_bg_kill`）。
          ② **有炸弹了 ⇒ 复活回炸矿**（2026-10-03 新增，照**火山**那套"同一循环里重估炸弹"的形状）：
             每拍先问一次 `choose_bomb_type()`（会顺手换成包里真有的那种：黑>超级>樱桃）
             ⇒ 有 ⇒ 返回 `"bombs_back"`，调用方**接着炸**（不是结束整趟）。
             原版没这条 ⇒ 一旦交棒就**永远回不去**，哪怕 `/give` 补了满包炸弹、或包里本来还有别的类型。
          ③ 恒离开矿井 / 被传出矿 / 自保判危险 ⇒ 结束（危险那条会先撤退）。

        返回值：`"bombs_back"` = 又有炸弹了，请调用方继续冲层；其余 = 协同结束（该收工了）。
        """
        log("\n🔄 背包炸弹不足 → 内部协同保镖：跟随 host + 帮忙敲矿/打怪 + 开宝箱。AI 可随时 bomb_retreat 结束协同并脱离矿井回门口。")
        quiet = 0
        coop_tick = 0
        coop_opened = set()      # 🎁 这一层已经开过的箱子坐标（换层清空）——防"每 9 秒重开同一个空箱"
        coop_chest_loc = ""
        while True:
            try:
                coop_tick += 1
                ml = (self.state().get("location") or {}).get("name", "")
                hl = self.host_location()
                # 退出：AI已被bomb_retreat传出矿 / 恒离开矿井
                if not is_mine_location(ml) or not is_mine_location(hl):
                    break
                # 💣 复活：又有炸弹了（/give 补的、捡到的、或包里本来还有别的类型）⇒ 交回炸矿模式
                _now_bomb = self.choose_bomb_type()
                if _now_bomb:
                    if _now_bomb != self.bomb_type:
                        log(f"  🧨 换用炸弹: {_now_bomb}（回炸矿模式）")
                    else:
                        log(f"  💣 又有炸弹了（{_now_bomb} ×{self.count_bombs(_now_bomb)}）→ 回到炸矿模式")
                    self.bomb_type = _now_bomb
                    return "bombs_back"
                # 生存优先：该撤了先吃，吃完还该撤就撤（原因串分开报，不再笼统"血低无食"）
                why = self.unsafe_reason()
                if why:
                    # ⚠️ 这里原来写 `eat_if_needed(self.hp_threshold)` —— 那个形参**已经废弃**
                    #    （吃的线固定在 `EAT_HP_PCT=60`，见 bomb_common 顶部 2026-10-03 那段），
                    #    留着这个实参只会让人以为"--hp-threshold 能调吃食线"（真机就是这么被坑的）。
                    if self.eat_if_needed():
                        why = self.unsafe_reason()
                    if why:
                        self.retreat(f"协同：{why}")
                        break
                acted = False
                # 恒在打架 → 增援（只打血量不满的对手）
                if self.reinforce_host():
                    acted = True
                # 贴跟随恒（自然走，不闪现）
                if not acted:
                    self.follow_host_once(walk_only=True)
                # 🎁 开宝箱（2026-10-03 恒真机：「**协同不会开箱子！**让协同也加开箱吧」）
                #    主循环是"**每层**开一次"（`_run_rush_inner` 里那个 `% 10` / `(level-120)%100` 判断），
                #    而协同**没有"层"这个概念**（恒换层就跟着 warp）⇒ 改成**节流**：每
                #    `COOP_CHEST_EVERY` 拍扫一次（`open_treasure_chests` 内部是 `surroundings(30)`
                #    的大扫描，每拍都扫会把 API 打满、还会拖慢跟随）。
                #    ⚠️ 满包这条规矩跟主循环**一模一样**（恒 2026-08-23：满包领不走就停脚本交 AI 手动，
                #      不自动丢物）——但协同这层的 `except Exception` 会把 `ManualChestFull`
                #      当普通异常吞掉（主循环那段注释专门警告过），所以这里**必须单独接住**。
                if coop_tick % COOP_CHEST_EVERY == 0:
                    try:
                        # 🎁 记账 `skip`（**只在当次下矿有效**，恒 2026-10-03 确认）：
                        #    同一层里同一个箱子只开一次；**换层清空** —— 因为"整百层宝箱房每次重进都会刷新"，
                        #    回到同一层本来就该再看一眼有没有新箱子；而已经空了的箱子，再点一下会**爆掉消失**，
                        #    所以"重开一次"本身没有害处（这条记账纯粹是为了别每 9 秒白等一次菜单开关）。
                        if ml != coop_chest_loc:
                            coop_chest_loc = ml
                            coop_opened.clear()
                        for _at in (self.open_treasure_chests(skip=coop_opened) or []):
                            coop_opened.add(tuple(_at))
                            acted = True
                    except ManualChestFull as e:
                        log(f"  ⭐ 宝箱满包领不走（{e}）→ 结束协同交 AI 手动"
                            f"（同 run_rush 那条规矩）：menu read 看待领取 → 处理完重开脚本")
                        if is_mine_location(self.my_location()):
                            self.retreat_to_entrance("宝箱满包")
                        log("🔄 === 协同结束 ===")
                        return "ended"
                # 敲恒身边/附近石头开路 + 敲高价值矿
                if self.smash_nearby_rocks(max_n=3, radius=8, ores_only=False):
                    self.retaliate_if_hit()
                    acted = True
                # 🫥 值班心跳（2026-10-03 恒「3我有点不懂……交给你来修」）：
                #    原来 `quiet` 只写不读 ⇒ 纯跟随时**日志一片静**，从日志上看不出它还在不在岗
                #    （恒站着不动时尤其像"脚本死了"）。⇒ 用它打一行心跳；**不**据它自动撤退
                #    （恒只是站着不动而已，悄悄走人才是真的坑）。
                quiet = 0 if acted else quiet + 1
                if quiet == 15 or (quiet > 15 and (quiet - 15) % 50 == 0):
                    log(f"  👥 协同中：跟着恒、暂无活干（静默 {quiet} 拍 ≈{quiet * 0.6:.0f}s）"
                        f" | 血 {self.state().get('player', {}).get('health')}"
                        f" | 炸弹 {self.count_all_bombs()}")
                time.sleep(0.6)
            except Exception as e:
                log(f"  ⚠️ 协同循环异常: {e}")
                time.sleep(1.0)
        # 结束：恒离开但 AI 还在矿 → 撤退出矿；AI 已被 bomb_retreat 传出矿 → 不再撤
        if is_mine_location(self.my_location()):
            self.retreat_to_entrance("协同结束")
        log("🔄 === 协同结束 ===")
        return "ended"

    def clear_floor(self, level, goal):
        """炸穿当前层直到找到梯子/无法继续。
        goal: 允许到达的最高层。⚠️ 恒 2026-08-23 起**恒等于 target_floor**（AI 领先自由冲，
        不再被 user 层数卡住等），所以下面 can_descend() 实际恒为 True。
        返回 ("DONE", next_level) | (None, reason)。
        ⚠️ 2026-09-19：原来还会返回 ("WAIT", None)（"在等 user"），那条路**不可达**
        （goal==target_floor 且循环条件 level<target_floor ⇒ can_descend 恒真），
        已删除；主循环那边改成"真收到 WAIT 就明确报内部不变量被破坏"。
        """
        loc_name = f"UndergroundMine{level}"
        bombs_this_floor = 0
        stuck_msg = None
        explore_count = 0
        no_collect = False   # 背包满之后本层剩余部分只炸不捡（见下面背包规划那段）

        def can_descend():
            """能否下到 level+1。⚠️ 当前 goal 恒等于 target_floor ⇒ 恒 True（见 docstring）；
            保留这个判断是为了将来若给 goal 重新加限制时不用回头补。"""
            return level + 1 <= goal

        # ── 感染层检测：怪多矿少→直接作弊楼梯跳关（约好的：不杀怪，作弊给梯下去） ──
        try:
            data = self.surroundings(14)
            rcount = sum(1 for t in data.get("tiles", []) if is_rock(t.get("object")))
            mcount = len(data.get("monsters", []))
            if mcount >= 3 and rcount <= 2:
                log(f"  👾 感染层！怪{mcount}只 矿{rcount}块 → 作弊楼梯跳关")
                if self.cheat_staircase() and self.use_staircase():
                    return "DONE", self.my_mine_level()
                # 楼梯失败 + 危险 → warp 下一层保底
                why = self.unsafe_reason()
                if why:
                    log(f"  💢 危险（{why}），warp 下一层保底")
                    if self.safe_warp(f"UndergroundMine{level+1}", x=5, y=5):
                        self.mine_level = level + 1
                        return "DONE", self.my_mine_level()
        except Exception:
            pass

        for attempt in range(MAX_FLOOR_ATTEMPTS):
            # buff 维护（按游戏报的 buff id 对槽；点名 food_buff 就只补点名那个）
            self.maintain_buffs(threshold=30, want=getattr(self, "food_buff", "") or None)

            # 自保：HP<60% 真实吃食物回血（IsActive 补丁后 eatObject 回血可靠；吃完仍低才 /heal 救急）
            self.eat_recovery(hard=self.hp_threshold, target=60)
            why = self.unsafe_reason()
            if why:
                if self.eat_if_needed():     # 吃食线固定 EAT_HP_PCT=60；那个 hp_threshold 形参已废弃
                    why = self.unsafe_reason()
                if why:
                    return None, why

            # 受击反击：HP 比上次低 → 立刻回击两下（保底，不依赖怪检测——魔法箭筒击退/延迟也能防）
            self.retaliate_if_hit()

            # ── 协同：user同层在打架 → position 增援，只打user的对手（限时爆发，做完回来炸矿） ──
            if attempt % COOP_CHECK_EVERY == 0 and self.reinforce_host():
                continue

            # 有梯子：能下就下；但下楼前先看附近有没有值得炸的矿簇（别浪费矿就下楼）
            ladder = self.find_ladder()
            if ladder:
                if can_descend():
                    # 下楼前贪心补一发：密集/高价值矿簇先炸了再下（2026-08-09 按user要求）
                    try:
                        anchor = self.best_bomb_anchor(min_covered=self.min_covered, max_dist=12)
                        if anchor:
                            ax, ay, covered, _ = anchor
                            log(f"  💣 下楼前补炸矿簇 ({ax},{ay}) 覆盖 {covered} 块")
                            self.bomb_and_collect(ax, ay, collect=True)
                            ladder = self.find_ladder()  # 炸完可能刷出新梯子
                    except Exception:
                        pass
                    log(f"  🪜 有梯子 ({ladder[0]},{ladder[1]})")
                    if self.descend():
                        return "DONE", self.my_mine_level()
                    log("  梯子下不去，继续炸")
                else:
                    # 一起冲层：已经到user的层差，等user往前
                    pass

            # 炸弹库存
            # ⚠️ 先试**换类型**再判"没炸弹"：`count_bombs()` 只数**当前那种**，而包里可能还有
            #    超级/樱桃（火山那套就是这么做的：`choose_bomb_type()` 回调）。少了这一步 =
            #    手里有樱桃却因为"黑炸弹用完了"就去当保镖。
            _sw = self.choose_bomb_type()
            if _sw and _sw != self.bomb_type:
                log(f"  🧨 换用炸弹: {_sw}（{self.bomb_type or '当前那种'} 用完了）")
                self.bomb_type = _sw
                self.select(self.bomb_type)
            if self.count_bombs() <= 0:
                # 2026-08-22 恒：没炸弹+玩家在同矿井 → 不再自主撤退出矿，转【内部】协同保镖（不经AI启用）
                if self.follow_host and is_mine_location(self.host_location()):
                    self.coop_handoff = True
                    if self._run_cooperate() == "bombs_back":
                        # 💣 2026-10-03：协同期间又拿到炸弹 ⇒ **回到炸矿模式继续这一层**（恒要的"复活"）
                        self.coop_handoff = False
                        continue
                    return None, "__COOP__"
                # 2026-08-09 按user要求：炸弹用完弹明确警告+结束撤退（别再默默转跟随让user以为卡死）
                log("  ⚠️💣 炸弹用完了！脚本结束（先 /give 补炸弹再跑）")
                return None, "炸弹用完了"

            # 找贪心锚点
            # 走路为主后放宽锚点距离（原6格是防position穿墙，走路不穿墙可放宽到12）
            anchor = self.best_bomb_anchor(min_covered=self.min_covered, max_dist=12)
            if anchor is None:
                # 没有值得炸的簇
                ladder = self.find_ladder()
                if ladder and can_descend() and self.descend():
                    return "DONE", self.my_mine_level()
                if not can_descend():
                    return "WAIT", None   # 在等user，本层炸完了
                # 没炸点没梯子：敲周围石头刷梯子（矿优先单遍排序，不拆两遍瞬移贪心——user 2026-08-10）
                if self.smash_nearby_rocks(max_n=4):
                    self.retaliate_if_hit()  # 敲完及时回击（防被木乃伊撞死）
                    explore_count += 1
                    # 敲石头可能敲出梯子——先查一下，有就正常下楼（别浪费楼梯/误撤退）
                    ladder = self.find_ladder()
                    if ladder and can_descend() and self.descend():
                        return "DONE", self.my_mine_level()
                    if explore_count >= 6:
                        log("  🔍 敲石头多次没进展，造楼梯跳关")
                        if self.craft_staircase() and self.use_staircase():
                            return "DONE", self.my_mine_level()
                        # 作弊保底给石头造（说好的作弊保底）
                        if self.cheat_staircase() and self.use_staircase():
                            return "DONE", self.my_mine_level()
                        return None, "没梯子也没楼梯材料"
                    continue
                # 没石头可敲：顺手捡采集物/贵重掉落（熔岩菇/地晶/泪晶/火水晶等）
                if self.pick_forage_nearby(max_items=3):
                    continue
                try:
                    if self.pick_valuable_drops(max_items=2):
                        continue
                except Exception:
                    pass
                # ★ 移动探索：扫大范围走向最近可达石头；探索多次没进展就造楼梯跳关（防死循环）
                explore_count += 1
                if explore_count >= 6:
                    log("  🔍 探索多次还没梯子，造楼梯跳关")
                    if self.craft_staircase() and self.use_staircase():
                        return "DONE", self.my_mine_level()
                    # 造楼梯失败 → 作弊保底给石头造（说好的作弊保底）
                    log("  🧨 造楼梯失败，作弊给石头")
                    if self.cheat_staircase() and self.use_staircase():
                        return "DONE", self.my_mine_level()
                    return None, "没梯子也没楼梯材料"
                try:
                    data = self.surroundings(24)
                    far_rocks = [(t["x"], t["y"]) for t in data.get("tiles", [])
                                 if is_rock(t.get("object"))]
                except Exception:
                    far_rocks = []
                s = self.state()
                px, py = s["player"]["x"], s["player"]["y"]
                if far_rocks:
                    # ⚠️ 2026-09-20 恒真机抓出来的（129 层「**好笨，不会往下走，自己撤退了**」）：
                    #    这一段**原来取的是"离自己最近那块石头"**（`sort` 后取 `[0]`，尽管变量叫
                    #    `far_rocks`）。而"进探索分支"的前提恰恰是
                    #    `best_bomb_anchor(max_dist=12)` **已经在 12 格内判过"没有值得炸的簇"**
                    #    ⇒ 走向最近的石头 = **走过去仍落在刚判过的范围里 = 原地打转**。
                    #    现场日志正好印证：连报两次「探索到 (5,5)」「探索到 (14,8)」——**全在入口边上**，
                    #    人根本没挪窝，转头被 `_update_stuck()` 判"连续放置失败"撤退。
                    #    恒的诊断："是不是看的范围太小了…**实际往下走一会儿就有了**。"
                    #  ⇒ 改成：**优先走锚点半径之外**（那才是没搜过的地），外圈里挑**最密的簇**
                    #    （密度 = 该岩体切比雪夫 3 格内的同伴数 ≈ 一处能炸到几块）；
                    #    外圈没矿才退而求其次走**最远**那块 —— 总之要换片地儿，不能原地磨。
                    #  ⚠️ 这里的 12 必须与上面的 `best_bomb_anchor(max_dist=12)` 一致，改一处要改两处。
                    def _density(r):
                        return sum(1 for q in far_rocks
                                   if max(abs(q[0] - r[0]), abs(q[1] - r[1])) <= 3)
                    outside = [r for r in far_rocks
                               if max(abs(r[0] - px), abs(r[1] - py)) > 12]
                    if outside:
                        outside.sort(key=lambda r: (-_density(r), abs(r[0] - px) + abs(r[1] - py)))
                        target_x, target_y = outside[0]
                    else:
                        far_rocks.sort(key=lambda r: -(abs(r[0] - px) + abs(r[1] - py)))
                        target_x, target_y = far_rocks[0]
                else:
                    target_x, target_y = 20, 20  # 没石头走向层中心（可通行由导航处理）
                log(f"  🚶 无炸点，换片地儿探索到 ({target_x},{target_y})")
                self.natural_walk(target_x, target_y, self.my_location(), walk_only=True)  # 纯走路（walk_only=False 会 position 传送出界）
                time.sleep(0.3)
                continue  # 到目标区后重新找锚点

            ax, ay, covered, _ = anchor
            log(f"  🎯 炸点 ({ax},{ay}) 覆盖 {covered} 块")

            # 炸 + 躲 + 等 + 捡（背包满之后本层就不再捡，见下）
            ok, msg, broken = self.bomb_and_collect(ax, ay, collect=not no_collect)
            if not ok:
                log(f"  ⚠️ {msg}")
                time.sleep(0.5)
                if self._update_stuck():
                    stuck_msg = "连续放置失败，疑似卡死"
                    break
                continue
            bombs_this_floor += 1
            self.retaliate_if_hit()  # 放完炸弹及时回击（防被围殴/木乃伊撞死）

            # 背包规划：快满时
            free = self.inventory_free_slots()
            if free <= 3:
                freed, plan = backpack_plan(self, drop_below=self.autodrop, need_slots=4)
                for ln in plan:
                    log(ln)
                if freed == 0 and self.inventory_free_slots() <= 2:
                    # ⭐ 2026-08-23 恒：背包满**不停脚本**（只停本层拾取），清包交给三层一停整理 /
                    #    异步 bomb_organize；只有弹战利品/待领物 ItemGrabMenu(开箱) 才 ManualChestFull 停脚本。
                    # ⚠️ 2026-09-19 修：原先这里是 `break` —— 那是**整层不炸了**，跟注释承诺的
                    #    "只停本层拾取"不是一回事（而且脚本末尾那段 `startswith("背包满")` 因为
                    #    从没人产出这个 reason，其实永远不执行）。现在按注释的本意来：本层剩下的
                    #    放弹一律 collect=False，脚本照常往下走。
                    if not no_collect:
                        log("  ⚠️ 背包满了（自动丢物已退役）→ 本层剩余部分停止拾取，脚本继续")
                    no_collect = True

            # 卡死检测：先扫整层按铱矿密集点走路，移动了继续炸；没移动就作弊给楼梯下楼
            # ⚠️ 这里原来传字面量 True ⇒ 恒不触发（见 _update_stuck 注释）
            if self._update_stuck():
                log("  ⚠️ 位置没变，扫整层找铱矿密集点")
                if self.walk_to_rich_ore(level):
                    continue
                log("  🧨 卡死，作弊给楼梯下楼")
                if self.cheat_staircase() and self.use_staircase():
                    return "DONE", self.my_mine_level()
                stuck_msg = "位置一直没变，疑似卡死"
                break

            # 每炸 2 次检查一次梯子（石头碎了可能刷出梯子）
            if bombs_this_floor % 2 == 0:
                ladder = self.find_ladder()
                if ladder and can_descend():
                    log(f"  🪜 炸出梯子 ({ladder[0]},{ladder[1]})")
                    if self.descend():
                        return "DONE", self.my_mine_level()

            time.sleep(0.3)

        if stuck_msg:
            return None, stuck_msg
        # 炸满次数没下去
        ladder = self.find_ladder()
        if ladder and can_descend() and self.descend():
            return "DONE", self.my_mine_level()
        if not can_descend():
            return "WAIT", None
        # 头骨矿洞无电梯：不 warp 跳层，造楼梯跳关
        log("  🚀 本层炸满次数没下去，造楼梯跳关")
        if self.craft_staircase() and self.use_staircase():
            return "DONE", self.my_mine_level()
        # 作弊保底给石头造（说好的作弊保底）
        if self.cheat_staircase() and self.use_staircase():
            return "DONE", self.my_mine_level()
        return None, "没梯子也没楼梯材料"

    # ── 主流程 ──

    def run_rush(self, start_level, target_floor, follow_host=True, max_floors=None):
        tag = "逐层" if max_floors else "整段"
        # 🧨 2026-09-06 恒：按 黑>超级>樱桃 从背包挑"实际有的"炸弹（有超级/樱桃别空喊没黑弹）；
        #   全没有则 self.bomb_type=""，内层报"没炸弹"降级。避免"有了不会用"。
        self.bomb_type = self.choose_bomb_type(self.bomb_type)
        log(f"\n💣 === 炸矿模式({tag}): {start_level} → {target_floor}层 | 炸弹: {self.bomb_type} ===")
        self.no_pause_on_unfocus()   # 后台也能走位，不抢user的焦点
        # 🛡️ 2026-09-20：**C# 贴身自动防御由 `main()` 开关**（见 ModEntry.GuardTick），
        #    **不在这个函数里**——理由同 mine_run：这里有一条 `ManualChestFull` 的中间 return，
        #    那时人还在矿里挨打，正需要防御；包在 main() 的 try/finally 才不漏。
        #    ⚠️ **节奏风险仍在、要自己一轮真机**：炸矿是**故意**站在点燃的炸弹旁边等冷却，
        #      且 `combat_aggressive` 本来就主动追击+`mine_teleport` 贴脸 —— guard 插进来
        #      会不会打断炸弹节奏，**没测过**（`--no-guard` 可单独关）。
        #    ✅ "剑的 AoE 会不会误伤同场的恒"已被反编译证伪：`GameLocation.damageMonster`
        #      只处理 `characters[num] is Monster`，玩家不在 `loc.characters` 里 ⇒ 打不到玩家。
        try:
            return self._run_rush_inner(start_level, target_floor, follow_host, max_floors=max_floors)
        except ManualChestFull as e:
            # ⭐ 开箱满包 → 停脚本交 AI 手动（不撤退、菜单留给 AI 处理完重开续层）
            log(f"  ⭐ 开箱满包停脚本 → 交AI手动（战利品: {e}）→ 处理完重开 bomb_mine 原地续层")
            return True
        finally:
            self.restore_pause_on_unfocus()

    def _run_rush_inner(self, start_level, target_floor, follow_host, max_floors=None):
        if not self.detect_weapon():
            log("  ⚠️ 没找到武器")
        # 出发前查炸弹——没炸弹直接报错，别飞矿里空手；有则已按 黑>超级>樱桃 挑好（run_rush 里解析）
        if not self.bomb_type or self.count_bombs() <= 0:
            log(f"  ❌ {self.absent_cause()}")
            return False
        log(f"  🧨 用炸弹: {self.bomb_type}")
        self.select(self.bomb_type)
        time.sleep(0.2)

        # 头骨矿洞（>=121）无电梯。恒 2026-08-23：在矿里原地续，以下最优先——"只要已在头骨矿里就绝不 warp、
        # 直接原地续下一个行为"（防弹窗完重开脚本误 warp 重生成层面/回入口）。不在矿里才 121 开。
        if target_floor >= 121:
            cur_lv = extract_mine_level(self.my_location())
            if cur_lv is not None and cur_lv >= 121:
                # ⭐ 已在头骨矿里 → 绝不 warp、绝不摸雕像！雕像在沙漠入口，摸=传沙漠=重载层面→箱子刷新！
                # 原地续（恒 2026-08-23：弹窗完继续脚本也走这，不重生成）。
                level = cur_lv
                log(f"  从第 {level} 层原地续（已在头骨矿里，不 warp 不重生成，不摸雕像）")
            else:
                # 不在头骨矿里 → 直接 warp 121 开（恒 2026-09-06：不传沙漠摸雕像，直接在矿内开；
                #   要竖井buff自己去沙漠摸——省一次"传沙漠洞口"的来回）
                if start_level > 121:
                    # 不在头骨矿里但显式 --start>121 → warp 到该层跳过浅层
                    if not self.safe_warp(f"UndergroundMine{start_level}", x=5, y=5):
                        log(f"  ❌ warp 到 {start_level} 层失败")
                        return False
                    level = extract_mine_level(self.my_location()) or start_level
                    log(f"  从第 {level} 层开始（跳过浅层）")
                else:
                    if not self.safe_warp("UndergroundMine121", x=5, y=5):
                        log("  ❌ 进不了头骨矿洞第一层")
                        return False
                    level = 121
        else:
            # 城镇矿（<121）：AI 已在矿内某层 → 原地续当前层（跟头骨一致。恒 2026-09-06 实测：
            #   便利工具触发被"读电梯进度层41已是深层"强制 warp 41 秒撤，根因——AI 明明在浅层却
            #   被拉去电梯层，warp 失败即收工。以当前所在层为准，别跟电梯进度层。）
            cur = extract_mine_level(self.my_location())
            if cur is not None and cur <= 120:
                level = cur   # 已在城镇矿内 → 原地续当前层，不 warp（跟头骨一样）
            else:
                # 不在城镇矿内 → warp 到 start（默认电梯层/进度层；或显式 --start N 指定层）
                level = start_level
                if not self.safe_warp(f"UndergroundMine{level}", x=5, y=5):
                    log("  ❌ 进不了矿")
                    return False
                level = extract_mine_level(self.my_location()) or level
        self.mine_level = level

        retreat_reason = None
        os_state = load_organize_state()
        floors_done = 0
        floors_since = os_state.get("floors_since_organize", 0)
        organize_suggested = False

        while level < target_floor:
            log(f"\n--- 💣 第 {level} 层 ---")
            # 每层开打前看一遍 buff（按 buff id 对槽补，不是靠名单猜）
            self.maintain_buffs(threshold=30, want=getattr(self, "food_buff", "") or None)

            # 宝箱：**每层都扫**（2026-10-03 恒真机：「跳了。是不是因为不是整百层也不认？」——**正是**）。
            #    原来这里只在"宝箱层"（城镇整10层 / 头骨 `(level-120)%100==0`）才调一次
            #    ⇒ **别的层上的箱子连扫都不扫**（那天协同在 135 层照样开出一个宝箱，主循环却会错过它）。
            #    `open_treasure_chests()` 内部"扫不到 Chest 就无操作"⇒ 每层调一次只是多一次
            #    `surroundings(30)`（一层一次，可接受；协同那边是 15 拍≈9s 一次）。
            #    ⚠️ 满包时它会抛 `ManualChestFull`（上面 run_rush 有专门处理，交 AI 手动）。
            self.open_treasure_chests()

            # 安全：⚠️ 吃完必须**复检**。clear_floor 那处有复检、主循环这处原来没有——
            #    结果吃一口就当"安全"继续走，跟吃食目标线之间留出一段死区（恒观察到的
            #    "被怪打到死还在吃东西"就长在这）。2026-09-19 补齐，两处对齐。
            why = self.unsafe_reason()
            if why:
                if self.eat_if_needed():     # 吃食线固定 EAT_HP_PCT=60；那个 hp_threshold 形参已废弃
                    why = self.unsafe_reason()
                if why:
                    retreat_reason = why
                    break

            # 一起冲层：AI 领先自由冲（goal=目标层，不再被user层数卡住等——user反馈"下楼有延迟"）
            # AI 落后user太多（user层数 - 我层数 > LEAD_MAX）→ 尽快传送到user身边（别掉队）
            # 头骨矿洞无电梯：AI 一层层走路下，不 warp 跳层追user（user自己玩，AI 自主推进）
            goal = target_floor
            if follow_host and is_mine_location(self.host_location()):
                hlv = self.host_mine_level()
                if hlv and hlv - level > LEAD_MAX:
                    log(f"  📡 AI 落后（user{hlv}层 我{level}层），传送到user身边")
                    if self.safe_warp(f"UndergroundMine{hlv}", x=5, y=5):
                        level = hlv
                        self.mine_level = level
                        try:  # 站到user旁边
                            hh = self.host_player()
                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                                if self.position_safe(hh.get("x", 0) + dx, hh.get("y", 0) + dy):
                                    break
                        except Exception:
                            pass
                        continue

            # 本层清矿
            status, payload = self.clear_floor(level, goal)

            if status is None:
                if payload == "__COOP__":
                    retreat_reason = None   # 转协同：不撤退，主循环退出交棒（结尾只打标记）
                    break
                retreat_reason = payload
                break
            if status == "WAIT":
                # 🚫 不可达：goal 恒等于 target_floor，而循环条件是 level < target_floor
                #    ⇒ can_descend()（level+1 <= goal）恒 True，clear_floor 不可能返回 WAIT。
                #    原来这里有一大段"等 user 两分钟"的代码 + 唯一用到 self.lead 的那行，
                #    全是死的（恒 2026-08-23 把"等 user"改掉之后就没人再产出 WAIT）。
                #    按恒"宁报错别兜底"：真走到这里说明不变量被破坏了，**明确报错**，不静默继续。
                log(f"  ❌ 内部不变量被破坏：clear_floor 返回 WAIT，但 goal={goal} level={level}"
                    f"（goal==target_floor 时 can_descend 恒真，不该出现）")
                retreat_reason = "内部状态异常（WAIT 不可达）"
                break

            nxt = payload
            if nxt <= level:
                retreat_reason = f"卡在{level}层"
                break
            level = nxt
            self.mine_level = level
            save_progress(level)
            floors_done += 1
            floors_since += 1
            os_state["floors_since_organize"] = floors_since
            save_organize_state(os_state)
            # 整理判定：满包 + 到间隔 + 未禁用 + 未关闭逐层整理（摘要仍输出背包/掉落供 AI 参考，
            # 但 organize_suggested 恒 False = 不主动提示整理；清包交给异步后台——恒 2026-08-23）
            organize_suggested = PER_FLOOR_ORGANIZE and \
                                 (not os_state.get("disabled")) and \
                                 (floors_since >= self.organize_interval()) and \
                                 (self.inventory_free_slots() <= 0)
            if level > load_progress():
                log(f"  🏆 新纪录：炸到第 {level} 层")
            if max_floors and floors_done >= max_floors:
                log(f"  ⏹️ 逐层模式：本层完成，返回摘要")
                break

            # 全局炸弹检查（每层结束）
            if self.count_bombs() <= 0:
                if follow_host and is_mine_location(self.host_location()):
                    self.coop_handoff = True
                    if self._run_cooperate() == "bombs_back":
                        # 💣 2026-10-03：协同期间又拿到炸弹 ⇒ **回炸矿模式接着冲**（不结束整趟）
                        self.coop_handoff = False
                        continue
                    break
                retreat_reason = "炸弹用完了"
                break

        # ── 结束 ──
        s = self.state()
        p = s.get("player", {})
        if max_floors:
            # 逐层模式：不撤退，输出结构化摘要（AI 在层间整理背包/捡遗漏掉落，再调下一层）
            log("📋 ===BOMB_SUMMARY===")
            log(self.build_summary(level, target_floor, organize_suggested, os_state, retreat_reason))
            log("===END===")
            return True
        log(f"\n🏁 === 炸矿结束 ===")
        log(f"  终点: 第 {level} 层（目标 {target_floor}）")
        if retreat_reason:
            log(f"  撤退原因: {retreat_reason}")
        log(f"  剩余 ❤️ {p.get('health')}/{p.get('maxHealth')}  ⚡ {p.get('stamina', 0):.0f}/{p.get('maxStamina')}")
        log(f"  炸弹剩余: {self.count_all_bombs()}")
        if self.coop_handoff:
            # 2026-08-22 恒：没炸弹+玩家同矿→已转【内部】协同（_run_cooperate 处理跟随+撤退），这里不重复出矿
            log("🔄 === 协同模式结束 ===")
            return False
        # ⚠️ 2026-09-19 删：这里原来有一段 `if retreat_reason.startswith("背包满")`，
        #    但全文件**没有任何一处**把 retreat_reason 赋成"背包满"（517/573 那两处只 log），
        #    所以它从来没执行过——留着就是"看着像有处理、其实是死的"。背包满的真实行为
        #    现在写在 clear_floor 里（只停本层拾取、脚本继续），跟注释承诺一致。
        # 🔥 2026-09-06 恒：城镇没显式设 target 走默认 120 时，若电梯/进度已到顶(start>=target)，
        #   其实"没层可炸就撤"。点名让 AI/人知道这不是真冲到更深，而是城镇到头了。头骨(≥121)无此概念，不提示。
        if (not self.target_was_default_skull) and getattr(self, "target_was_default", False) \
                and start_level >= target_floor:
            log(f"  🚩 提醒：鹈鹕镇普通矿井 target {target_floor} 层是【默认值】（未显式指定），"
                f"且电梯/进度已到这一层——其实是没层可炸就撤，不是真冲到更深了。")
            log(f"     ⚠️ 城镇【普通矿井最高120层】就到头了，更深处在【头骨矿洞/沙漠】(121+，"
                f"mine bomb_mine 会进头骨)，别再本矿井给 target>120。")
        self.retreat_to_entrance(retreat_reason or "到目标层")
        return retreat_reason is None


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="[bomb] 炸矿模式 — 自主贪心炸弹下矿")
    parser.add_argument("--port", type=int, default=None, help="AI 角色端口（默认7843）")
    parser.add_argument("--host-port", type=int, default=None, help="房主(user)端口（默认7842）")
    parser.add_argument("--start", type=int, default=1, help="起始层（配合 --no-resume）")
    parser.add_argument("--target", type=int, default=0, help="目标层（0=按当前层自适应：头骨/沙漠≥121→500、城镇→120）")
    parser.add_argument("--bomb", type=str, default="Bomb", help="炸弹类型：Bomb/Mega Bomb/Cherry Bomb")
    parser.add_argument("--min-covered", type=int, default=4, help="至少覆盖N块岩体才炸（默认4，爆炸区不重叠后效率够）")
    # ⚠️ 2026-09-19 语义收口：**这不是撤退线**。撤退线已改成 HP<20 **绝对值**（恒定的
    #    "不到快死都可以继续下"）。本阈值只管"吃/兜底"——eat_if_needed 的触发线、
    #    eat_recovery 里 /heal 的救急线、以及 preflight 的拦启动线。
    #    四个入口原来三种值（bomb_mine 30 / mine go 50 / escort 50 / volcano 30），统一成 30。
    parser.add_argument("--hp-threshold", type=int, default=30,
                        # ⚠️ `%%` 是必须的：argparse 会对 help 串做 `%` 格式化，裸 `%` 会让
                        #    `--help` 抛 `ValueError: unsupported format character`（2026-09-20 发现）
                        help="吃/兜底线：血量低于此%% 吃食物（含 /heal 救急、preflight 拦启动）。"
                             "默认30。⚠️ 撤退线不在这里——撤退看 HP<20 绝对值。")
    parser.add_argument("--follow-host", type=int, default=1, help="user在矿里就一起冲层（1开0关）")
    parser.add_argument("--weapon", type=str, default=None, help="武器绑定：指定用某把武器（如 'Galaxy Hammer'），不指定自动选真实武器")
    parser.add_argument("--lead", type=int, default=2,
                        help="⚠️已废弃（恒 2026-08-23 起 AI 领先自由冲，不再被 user 层数卡住）。"
                             "传非默认值会**明确报错**，不再静默无效。")
    parser.add_argument("--autodrop", type=int, default=0, help="自动丢物（已退役，恒 2026-08-23 全退役）：0=只规划不丢，交AI手动整理腾格")
    parser.add_argument("--resume", action="store_true", default=True, help="从炸矿进度恢复（默认开）")
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    parser.add_argument("--check-progress", action="store_true", help="查看炸矿进度")
    parser.add_argument("--reset-progress", action="store_true", help="重置炸矿进度")
    parser.add_argument("--one-floor", action="store_true", help="逐层模式：跑一层返回结构化摘要，不撤退（AI 层间整理背包再调下一层）")
    parser.add_argument("--organize-disable", action="store_true", help="AI 判定后续不需要整理背包：写 bomb_organize.json disabled")
    parser.add_argument("--organize-reset", action="store_true", help="整理完背包后重置间隔计数（bomb_organize.json floors_since_organize=0）")
    parser.add_argument("--no-guard", action="store_true", help="🛡️ 关掉 C# 侧贴身自动防御（A/B 对照用）")
    parser.add_argument("--food-hp", type=str, default=None,
                        help="🍽️ 回血食物（**逗号分隔、靠前的先吃**，如 '奶酪,鱼肉卷'）；"
                             "点名=白名单（吃完了也不吃别的），不传才自动挑")
    parser.add_argument("--food-sta", type=str, default=None,
                        help="🍽️ 体力食物（**逗号分隔、靠前的先吃**，如 '沙拉,面包'）；"
                             "点名=白名单，不传才自动挑")
    parser.add_argument("--food-buff", type=str, default=None,
                        help="🍽️ 点名「现在去吃带这个效果的那份」（效果关键字，如 '运气'/'钓鱼'，"
                             "判据=游戏报的 foodBuffs 效果文案/buff id/吃食名）；"
                             "该 buff 没了/快过期就吃；点名了就不吃别的。不传=包里任意带 buff 的都算候选")
    args = parser.parse_args()

    # ⚠️ 2026-09-19：--lead 自 2026-08-23 起**完全无效**（goal 恒等于 target_floor，
    # 既不等 user 也不看层差），但 CLI 和 MCP 都还在传它——传了没反应是最坏的一种"谎报"。
    # 按恒"宁报错别兜底"：传了非默认值就直接报错，让人知道这条已经不存在了。
    if args.lead != 2:
        log("❌ --lead 已废弃（恒 2026-08-23：AI 领先自由冲，不再按层差等 user）。"
            "这个参数现在不起任何作用，别传它；要限制冲到哪一层请用 --target。")
        return

    if args.check_progress:
        deepest = load_progress()
        if deepest > 0:
            log(f"💣 炸矿进度：已到达最深 {deepest} 层（下次从第 {resume_start_level()} 层恢复）")
        else:
            log("💣 还没有炸矿进度记录")
        return
    if args.reset_progress:
        save_progress(0)
        log("💣 炸矿进度已重置")
        return
    if args.organize_disable:
        st = load_organize_state()
        st["disabled"] = True
        save_organize_state(st)
        log("💼 整理背包已禁用（后续不再提示整理）")
        return
    if args.organize_reset:
        st = load_organize_state()
        st["floors_since_organize"] = 0
        save_organize_state(st)
        log("💼 整理间隔计数已重置（整理过了）")
        return

    # 端口解析
    import re as _re
    from bomb_common import NAGI_URL, HOST_URL
    port = args.port or int(_re.search(r'(\d+)', NAGI_URL).group(1))
    hport = args.host_port or int(_re.search(r'(\d+)', HOST_URL).group(1))

    # ⭐ 2026-08-23 恒：曾默认 target=80 → AI 在头骨/沙漠用会把 AI 拉去鹈鹕镇矿。按当前层自适应。
    # 🔥 2026-09-06 恒：AI 没显式设 --target 就默认到 120（城镇）/500（头骨）——默认是**电梯/进度层数**，
    #   不是"AI 真心想冲的深度"。AI 懵懵懂懂冲到 120 就撤，不知道还能冲。这里必须把默认层数点名，
    #   让 AI（或人）知道这是"没设 target 的兜底"，该不该继续冲得显式给 target 才算数。
    # ── 场景判定（先统一算，显式/默认 target 都要用） ──
    # 🔥 2026-09-06 恒：城镇【普通矿井最高120层】；头骨矿洞(≥121 或 SkullCave 入口)才是更深，无电梯直通深层。
    try:
        s = requests.get(f"http://localhost:{port}/state", timeout=10).json()
        _loc = (s.get("location") or {}).get("name", "")
    except Exception:
        _loc = ""
    _lv = extract_mine_level(_loc) or 0
    # 头骨矿层(≥121) / 头骨入口 SkullCave / **沙漠 Desert**
    # ⚠️ 2026-09-20 补 `Desert`：原来只认前两个 ⇒ 轮回站在**头骨矿洞门口那张图**(Desert(8,6))
    #    时，`--target 145` 会被当成"城镇"硬拦（真机现场那句「城镇普通矿井最高【120层】」）。
    #    沙漠就是去头骨的必经地，在那儿被拦等于"到家门口不让进"。脚本进洞走的是
    #    `safe_warp("UndergroundMine121")`，不需要先踩进 SkullCave，所以 Desert 收下是对的。
    #    ⚠️ MCP 的 `bomb_mine` 工具里有一份**同款名单**（nagi_mcp_server.py），改一处要同步另一处。
    in_skull = _lv >= 121 or _loc.startswith("SkullCave") or _loc == "Desert"

    target = args.target
    target_was_default = False   # 是否走了"未指定→默认"的兜底（城镇：没设→120电梯顶；头骨：没设→500深层目标）
    # 🔥 2026-09-06 恒：城镇普通矿井【最高120层】，target >120 = 到不了的层，**硬拦**（不是提示，防 AI 误设）；
    #   头骨矿洞(≥121)无限制（从121连续深挖，想冲多深都行）。
    if not in_skull and target > 120:
        log(f"  ❌ 城镇普通矿井最高【120层】，target={target} 超了——这不是城镇能炸到的层。"
            f"更深处在【头骨矿洞/沙漠】(121+，mine bomb_mine 会进头骨)。若只想到120，target 设 120 或用默认。")
        return
    if not in_skull:
        target = min(target, 120)   # 城镇 target 上限120
    else:
        target = min(target, 500)   # 头骨/沙漠上限放500（测深层，恒 2026-08-23 确认500不用改）

    if target <= 0:
        target = 500 if in_skull else 120
        target_was_default = True
        if in_skull:
            log(f"  ℹ️ 头骨矿洞：从第一层(121)起连续往下爬（无电梯/进度层数概念），target={target} 是想爬到的最深层。")
            log(f"     （头骨连续深层，爬到 {target} 层是正常目标，不是「默认兜底就撤」；想更深改 --target。）")
        else:
            log(f"  ⚠️ 没指定 --target，用默认目标层: {target}（城镇，≈电梯到顶120）")
            log(f"     注意：鹈鹕镇【普通矿井最高120层】，target=120 就是到头了——这是电梯顶，不是 AI 想冲更深的预设目标。")

    # 起始层：进度恢复
    # 🔥 2026-09-06 恒：城镇(≤120) start 不得超过"电梯/进度层"，跳更深=报错（要往深只能一层层炸上去）。
    #   头骨(≥121)从121连续深挖，start=要爬到的起点（resume 在坑里原地续/不在坑回121）。
    # ⚠️ maxFloor 可能受"深处的危险"重置影响（重置后电梯=1），动态读。
    start = args.start
    if target < 121:
        # ── 城镇：基准=电梯当前到 ──
        elev = _town_elevator_start(port)
        if args.resume and start <= 1:
            start = elev if elev > 1 else 1   # 默认从电梯层继续
            if elev > 1:
                log(f"  🪜 电梯当前到 {elev} 层开始")
        elif start > elev:
            log(f"  ❌ 城镇起始层 start={start} 超过电梯/进度层 {elev}——不能跳到比进度更深的层。"
                f"（只能从进度浅层往上炸；想到更深先把浅层炸到那一层，或 start 设 ≤ {elev}。）")
            return
    else:
        # ── 头骨：从进度/121 恢复 ──
        if args.resume and start <= 1:
            auto = resume_start_level(port)
            if auto > 1:
                log(f"  📋 头骨沙漠恢复 {auto} 层开始")
            start = auto

    bot = BombMineBot(port, hport, bomb_type=args.bomb,
                      min_covered=args.min_covered,
                      hp_threshold=args.hp_threshold,
                      follow_host=bool(args.follow_host),
                      lead=args.lead, autodrop=args.autodrop,
                      weapon=args.weapon)
    # 🍽️ 2026-09-20 恒：自定义吃食 —— 点名 + 优先级（逗号分隔、靠前的先吃）。
    #    2026-10-03 恒：「**有点名只吃点名，吃完了也不吃别的；不点名才自动吃**」⇒ 点名 = 白名单。
    bot.food_hp = parse_food_list(args.food_hp)
    bot.food_sta = parse_food_list(args.food_sta)
    # 🍽️ 2026-10-03：`food_buff` = 点名"去吃带这个效果的那份"（判据=游戏报的 foodBuffs）
    bot.food_buff = args.food_buff or ""
    bot.target_was_default = target_was_default   # 🔥 结束段据此点名"默认target到顶就撤"（2026-09-06 恒）
    bot.target_was_default_skull = target_was_default and in_skull   # 默认 target 且是头骨(≥121)→结束段不提示（头骨无电梯/进度层数概念）

    # ⚠️ 2026-08-16 预检：炸弹/血量/武器告知原因（没炸弹/血低硬拦，没武器黄）
    block = bot.preflight()
    if block:
        log(block)
        return

    max_floors = 1 if args.one_floor else None
    # 🛡️ 2026-09-20 恒：贴身自动防御（见 ModEntry.GuardTick）。
    #    ⚠️ **"剑的 AoE 会不会误伤同场的恒"这条已由反编译证伪**：
    #      `GameLocation.damageMonster` 开头就 `characters[num] is Monster { IsMonster: not false,
    #      Health: >0 }` 才处理（玩家不在 `loc.characters` 里、也不可能是 `Monster`）
    #      ⇒ **武器打不到玩家**。原先那正是把 bomb 系列排除在外的理由之一，现在解除了。
    #    形状同 mine_run：开在 try 前、关在 finally。
    if not args.no_guard:
        bot.guard_on()
    try:
        bot.run_rush(start, target, follow_host=bool(args.follow_host), max_floors=max_floors)
    finally:
        if not args.no_guard:
            bot.guard_off()


if __name__ == "__main__":
    main()
