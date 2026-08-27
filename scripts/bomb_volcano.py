# -*- coding: utf-8 -*-
"""
🌋 bomb_volcano.py — 火山联机骑行炸矿（跟user换层）

火山矿洞没有可自动检测的下梯（/ladder 报 "Not in a mine"），只能联机骑行：
- user人工下梯换层，AI 定时 poll user的位置，user换层/换地点就 warp 到user身边
- warp 落点选user身边的安全格（user站着的地方必是安全格，绕开熔岩/单向门/压力板）
- 同层：瞬移炸矿簇（position 到石头旁放炸弹，熔岩/门挡不住）——和 bomb_mine 同一套贪心锚点
- 打怪：帮user增援（只打血量不满的对手，reinforce_host）+ 近身反击（不追远，防走熔岩）
- 撤退：user离开火山 → 姜岛火山口 IslandNorth(40,24)

和 bomb_mine 的差异：
- 不自己找梯子/下潜（火山没梯子检测），完全靠user换层 warp 跟随
- 不造楼梯跳关（火山楼梯机制不同，且user在导航）
- 不写进度文件（骑行模式没有"自己冲层"的进度概念）

用法:
  python bomb_volcano.py                        # 默认 Bomb，等user进火山后骑行炸矿
  python bomb_volcano.py --min-covered 3 --poll 2 --max-minutes 30
  python bomb_volcano.py --bomb "Mega Bomb" --hp-threshold 40
"""

import sys
import time
import argparse
import os

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
os.environ.setdefault("NAGI_HOST_URL", "http://localhost:7842")

from bomb_common import (BombMiner, log, is_rock, extract_mine_level,
                         BOMB_NAMES)
from bomb_mine import BombMineBot, backpack_plan

# 火山地点前缀（VolcanoDungeon1-10 + VolcanoCaldera 锻造台区都算"在火山"）
VOLCANO_PREFIX = "Volcano"
# 火山出口（姜岛火山口）
VOLCANO_EXIT = ("IslandNorth", 40, 24)


class VolcanoBot(BombMineBot):
    """火山骑行炸矿：跟user换层（warp）+ 同层清矿簇（瞬移炸弹）"""

    def __init__(self, port, host_port, bomb_type="Bomb", min_covered=3,
                 hp_threshold=30, poll=2.5, autodrop=15, weapon=None):
        super().__init__(port=port, host_port=host_port, bomb_type=bomb_type,
                         min_covered=min_covered, hp_threshold=hp_threshold,
                         follow_host=True, autodrop=autodrop, weapon=weapon)
        self.poll = poll
        self._last_wait_log = 0.0
        self._last_swept_loc = None  # 一进层捡拾记忆（防止同一层重复扫，2026-08-27）

    def preflight(self):
        """火山预检（2026-08-16 恒）：只硬拦血量过低；没炸弹/没武器 → 黄（骑行模式可切镐子硬跟）。
        ⚠️ 覆盖 bomb_mine 的 preflight——火山没炸弹也能当保镖跟，不硬拦。"""
        try:
            s = self.state()
            p = s.get("player", {})
            mhp = p.get("maxHealth") or 1
            hp = p.get("health") or 0
            if mhp > 0 and hp * 100 / mhp < self.hp_threshold:
                return f"❌ 当前血量 {hp}/{mhp}（{hp*100/mhp:.0f}%）低于阈值 {self.hp_threshold}%——先回血/睡觉再来"
            if self.count_bombs() <= 0:
                log("  ⚠️ 背包没有炸弹 → 切镐子敲石硬跟模式（能帮打，但炸不了矿簇）")
            if not self.detect_weapon():
                log("  ⚠️ 没找到武器（建议带剑防身）")
        except Exception as e:
            log(f"  ⚠️ 预检异常（继续启动）: {e}")
        return None

    # ═══════════ 基础 ═══════════

    def my_pos(self):
        s = self.state()
        p = s.get("player", {})
        return p.get("x", 0), p.get("y", 0)

    def is_volcano_loc(self, loc):
        """火山范围：VolcanoDungeonN + Caldera(锻造台)。⚠️ 2026-08-10 user到 Caldera 被误判
        离开火山导致 AI 撤退——Caldera 名不带 'Volcano' 前缀，必须单独认。"""
        return bool(loc and (loc.startswith(VOLCANO_PREFIX) or loc == "Caldera"))

    # ═══════════ warp 跟user ═══════════

    def warp_to_host(self, hloc, hx, hy):
        """warp 到user所在层（尽量贴user身边安全格）。
        试 8 个偏移（±1/±2），落岩浆/被引擎弹走就试下一个。
        只要落到正确层就算成功（不要求精确贴user 1 格——实测 warp 会落附近安全格）。
        ⚠️ 地图加载延迟：user刚进新层时 AI 客户端要加载那层地图，0.5s 内判失败会
        反复 warp 导致"每层闪很多下"（user 2026-08-10 反馈）。改为每个偏移 poll 到
        1.5s 确认进层，整轮失败等 1s 再试一轮，减少无效闪烁。"""
        offsets = ((1, 0), (-1, 0), (0, 1), (0, -1),
                   (2, 0), (-2, 0), (0, 2), (0, -2))
        for attempt in range(2):
            for dx, dy in offsets:
                tx, ty = hx + dx, hy + dy
                self.warp(hloc, tx, ty)
                for _ in range(3):  # poll 到 1.5s（地图加载/换层延迟）
                    time.sleep(0.5)
                    s = self.state()
                    if s.get("location", {}).get("name", "") == hloc:
                        p = s.get("player", {})
                        log(f"  🛸 warp 到user所在层 {hloc}（({p.get('x')},{p.get('y')})）")
                        return True
            time.sleep(1.0)  # 整轮失败可能是地图还在加载，等 1s 再试
        log(f"  ⚠️ warp 到 {hloc} 失败，等下轮重试")
        return False

    # ═══════════ 一进层先捡（防炸弹炸没龙牙/采集物） ═══════════

    def sweep_pickups_on_entry(self):
        """进层先捡——一进图就扫地上采集物/高价值掉落，捡完再炸（恒 2026-08-27）。
        火山龙牙=(O)852、熔岩菇、宝石等是 /surroundings 的 object，会被炸弹炸没；
        /debris 的高价值掉落（银河之魂/五彩/铱/放射/龙牙）同理。先捡再炸就保得住。
        仅每层扫一次（_last_swept_loc 记上次扫过的层，同层不重复），捡完这轮停下、
        下轮才进炸矿/打怪逻辑，保证"有就先捡、再炸"。返回捡了几个。"""
        loc = self.my_location()
        if not self.is_volcano_loc(loc):
            return 0
        if loc == getattr(self, "_last_swept_loc", None):
            return 0  # 本层已经扫过了（防同层反复扫描）
        self._last_swept_loc = loc
        picked = 0
        # 1) 地上采集物/龙牙（surroundings object：熔岩菇/龙牙/宝石等）——炸弹会炸没，优先捡
        try:
            picked += self.pick_forage_nearby(radius=14, max_items=6)
        except Exception:
            pass
        # 2) 高价值掉落（/debris：银河之魂/五彩碎片/铱/放射/龙牙）
        try:
            picked += self.pick_valuable_drops(max_items=4)
        except Exception:
            pass
        return picked

    # ═══════════ 火山近身战斗（不追远，防走熔岩） ═══════════

    def volcano_defend(self, radius=4):
        """自保近战：只打 3 格内最近的怪，面向砍，不追远。
        火山的怪（熔岩精灵/虎皮史莱姆/熔岩潜伏者）都主动贴脸，近身砍够用。"""
        try:
            ms = self.nearby_monsters(radius)
        except Exception:
            return False
        ms = [m for m in ms if "Bug" not in m[0]]
        if not ms:
            return False
        px, py = self.my_pos()
        mx, my = ms[0][1], ms[0][2]
        dist = abs(mx - px) + abs(my - py)
        if dist > 3:
            return False  # 不追远（熔岩/门，追容易死）
        self.face_toward(mx, my)
        self.detect_weapon()
        if self.weapon_name:
            self.select(self.weapon_name)
            time.sleep(0.1)
            self.use_tool()
            self.select(self.bomb_type)
        else:
            self.use_tool("Pickaxe")
        time.sleep(0.4)
        return True

    # ═══════════ 撤退 ═══════════

    def retreat_volcano(self, reason):
        log(f"  🏳️ 火山撤退：{reason}")
        loc = self.my_location()
        if self.is_volcano_loc(loc):
            self.warp(*VOLCANO_EXIT)
        return True

    # ═══════════ 主循环 ═══════════

    def run(self, max_minutes=None, wait_host=True):
        log(f"🌋 === 火山骑行炸矿启动（跟user换层，炸弹={self.bomb_type}，poll={self.poll}s）===")
        self.no_pause_on_unfocus()
        try:
            return self._run_inner(max_minutes, wait_host)
        finally:
            self.restore_pause_on_unfocus()

    def _run_inner(self, max_minutes, wait_host):
        if self.detect_weapon():
            log(f"  ⚔️ 武器: {self.weapon_name} ({self.weapon_class}) wspeed={self.weapon_speed}")
        else:
            log("  ⚠️ 没找到武器，只能用镐子防身")
        if self.count_bombs() <= 0:
            # 没炸弹不拒跑——user设计：炸弹用完切镐子敲石硬跟到出口层（5/10层）
            log("  ⚠️ 背包没有炸弹 → 切镐子敲石硬跟模式（跟user换层，到出口层才能出）")
        else:
            self.select(self.bomb_type)
            time.sleep(0.2)

        start = time.time()
        deadline = start + max_minutes * 60 if max_minutes else None
        was_in_volcano = False
        bombs_placed = 0
        follow_count = 0
        deepest = 0

        while True:
            if deadline and time.time() > deadline:
                log(f"  ⏱️ 到达时长上限 {max_minutes} 分钟")
                break

            # ── 自保（每轮一次 AI state）──
            s_ai = self.state()
            p_ai = s_ai.get("player", {})
            my_loc = s_ai.get("location", {}).get("name", "")
            hp = p_ai.get("health", 0)
            maxhp = p_ai.get("maxHealth", 1)
            hp_pct = hp / maxhp * 100 if maxhp else 0
            tod = s_ai.get("time", {}).get("timeOfDay", 600)
            if hp <= 0 or (maxhp and hp_pct < self.hp_threshold) or tod >= 2430:
                self.eat_if_needed(self.hp_threshold)
                self.eat_recovery(hard=self.hp_threshold, target=60)
                if not self.is_safe(self.hp_threshold):
                    self.retreat_volcano("血回不上来 / 快昏迷，撤退")
                    return False
            elif hp_pct < 60:
                self.eat_recovery(hard=self.hp_threshold, target=60)

            # ── user在哪（一次 host state）──
            hs = self.host_state()
            hloc = hs.get("location", {}).get("name", "")
            h = hs.get("player", {})
            hx, hy = h.get("x", 0), h.get("y", 0)
            hfloor = extract_mine_level(hloc) or 0

            if not self.is_volcano_loc(hloc):
                # user不在火山
                if was_in_volcano:
                    log("  🚪 user离开了火山，撤退")
                    self.retreat_volcano("user离开火山")
                    break
                if wait_host:
                    now = time.time()
                    if now - self._last_wait_log > 8:
                        log("  🧍 等user进火山（他去姜岛火山要自己导航）…")
                        self._last_wait_log = now
                    time.sleep(self.poll)
                    continue
                log("  user不在火山且不等待，结束")
                break

            was_in_volcano = True
            deepest = max(deepest, hfloor or 0)

            # ── 换层/换地点 → warp 跟user ──
            if hloc != my_loc:
                if self.warp_to_host(hloc, hx, hy):
                    follow_count += 1
                    # ⭐ 一进层先捡（龙牙/熔岩菇/宝石/高价值掉落）再炸——防炸弹把龙牙炸没（2026-08-27）
                    if self.sweep_pickups_on_entry():
                        log("  🍄 进层捡拾完毕")
                time.sleep(0.5)
                continue

            # ── 同层：进层先捡 → 帮user打怪 → 炸矿簇 → 自保近战 → 等user ──
            # ⭐ 一进层就扫掉落（龙牙/熔岩菇/宝石/高价值），捡完才进炸矿/打怪——_last_swept_loc
            #    同层只跑一次，捡了就 continue，下一轮自然落到炸矿（恒 2026-08-27）
            if self.sweep_pickups_on_entry():
                log("  🍄 进层捡拾完毕")
                time.sleep(0.3)
                continue
            if self.reinforce_host():
                time.sleep(0.3)
                continue

            # 💣 炸弹用完：不撤退！火山只有 5/10 层有出口，硬着头皮贪心敲石跟user到出口层
            #（user换层照常 warp 跟；user退出火山(在5/10层离开) → AI 自然跟着出）
            if self.count_bombs() <= 0:
                # 恒定空闲优先级：踩机关 → 贪心敲矿 → 捡采集物/掉落 → 敲user身边三圈石头（帮开路）
                if self.step_mechanisms(max_steps=2):
                    time.sleep(0.3)
                    continue
                if self.smash_nearby_rocks(max_n=5, radius=8, ores_only=True):
                    log("  ⛏️ 炸弹用完，贪心敲矿硬撑中…")
                    time.sleep(0.2)
                    continue
                if self.volcano_defend(radius=5):
                    continue
                if self.pick_forage_nearby(max_items=3):
                    continue
                try:
                    if self.pick_valuable_drops(max_items=2):
                        continue
                except Exception:
                    pass
                if self.smash_around_host(max_n=3, ring=3):
                    time.sleep(0.2)
                    continue
                log("  ⛏️ 炸弹用完没石头敲，等user换层（跟到出口层才能出）…")
                time.sleep(self.poll)
                continue

            anchor = self.best_bomb_anchor(min_covered=self.min_covered, max_dist=12)
            if anchor:
                ax, ay, covered, _ = anchor
                ok, msg, _ = self.bomb_and_collect(ax, ay, collect=True)
                if ok:
                    bombs_placed += 1
                    # 爆炸后顺手捡贵重掉落（position 站旁边，火山可靠）
                    try:
                        self.pick_valuable_drops(max_items=3)
                    except Exception:
                        pass
                # 背包规划：快满时（复用 bomb_mine 的）
                if self.inventory_free_slots() <= 3:
                    freed, plan = backpack_plan(self, drop_below=self.autodrop, need_slots=4)
                    for ln in plan:
                        log(ln)
                    if freed == 0 and self.inventory_free_slots() <= 2 and self.autodrop <= 0:
                        # ⭐ 恒 2026-08-23：自动丢物退役后，满包不撤退（火山骑行跟房主，撤了会丢下房主），
                        #    只停拾取继续跟——战利品收不了就不收，不丢人不丢物。拾取函数自己会在 free<=0 时跳过。
                        #    （不 break，落到下方 time.sleep(0.3)+continue 继续骑车；拾取函数内部 free<=0 自动跳过）
                        log("  ⚠️ 背包满了（自动丢物已退役）→ 本层停止拾取，继续跟房主骑乘")
                time.sleep(0.3)
                continue

            # 没炸点 → 近身打怪（火山怪主动贴脸）
            if self.volcano_defend(radius=5):
                time.sleep(0.3)
                continue

            # 恒定空闲优先级：踩机关 → 贪心敲矿 → 捡采集物/掉落 → 敲user身边三圈石头（帮开路）
            if self.step_mechanisms(max_steps=2):
                time.sleep(0.3)
                continue
            if self.smash_nearby_rocks(max_n=5, radius=8, ores_only=True):
                time.sleep(0.2)
                continue
            if self.pick_forage_nearby(max_items=3):
                continue
            try:
                if self.pick_valuable_drops(max_items=2):
                    continue
            except Exception:
                pass
            if self.smash_around_host(max_n=3, ring=3):
                time.sleep(0.2)
                continue

            # 都没做 → 等user换层
            log(f"  🧍 第{hfloor}层没矿簇，等user换层…")
            time.sleep(self.poll)

        # ── 结束 ──
        s = self.state()
        p = s.get("player", {})
        if self.is_volcano_loc(self.my_location()):
            self.retreat_volcano("骑行结束")
        log("\n🏁 === 火山骑行结束 ===")
        log(f"  最深处: 火山第 {deepest} 层")
        log(f"  跟随换层: {follow_count} 次 | 炸了 {bombs_placed} 颗炸弹")
        log(f"  剩余 ❤️ {p.get('health')}/{p.get('maxHealth')}  ⚡ {p.get('stamina', 0):.0f}/{p.get('maxStamina')}")
        return True


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="[bomb] 火山联机骑行炸矿 — 跟user换层")
    parser.add_argument("--port", type=int, default=None, help="AI 角色端口（默认7843）")
    parser.add_argument("--host-port", type=int, default=None, help="房主(user)端口（默认7842）")
    parser.add_argument("--bomb", type=str, default="Bomb", help="炸弹类型：Bomb/Mega Bomb/Cherry Bomb")
    parser.add_argument("--min-covered", type=int, default=3, help="至少覆盖N块岩体才炸（火山簇小，默认3）")
    parser.add_argument("--hp-threshold", type=int, default=30, help="血量低于此%撤退（默认30）")
    parser.add_argument("--poll", type=float, default=2.5, help="user位置轮询间隔秒（默认2.5）")
    parser.add_argument("--max-minutes", type=int, default=None, help="最多运行分钟数")
    parser.add_argument("--autodrop", type=int, default=0, help="背包满时自动丢价值≤此值的物品（已退役，恒2026-08-23全退役：0=只报不丢，满包撤退交给AI）")
    parser.add_argument("--weapon", type=str, default=None, help="武器绑定（如 'Galaxy Hammer'）")
    parser.add_argument("--no-wait", action="store_true", help="user不在火山时不等待直接结束")
    args = parser.parse_args()

    import re as _re
    from bomb_common import NAGI_URL, HOST_URL
    port = args.port or int(_re.search(r'(\d+)', NAGI_URL).group(1))
    hport = args.host_port or int(_re.search(r'(\d+)', HOST_URL).group(1))

    bot = VolcanoBot(port, hport, bomb_type=args.bomb,
                     min_covered=args.min_covered,
                     hp_threshold=args.hp_threshold,
                     poll=args.poll,
                     autodrop=args.autodrop,
                     weapon=args.weapon)

    # ⚠️ 2026-08-16 预检：只硬拦血量过低（没炸弹/没武器黄，骑行可切镐子硬跟）
    block = bot.preflight()
    if block:
        log(block)
        sys.exit(1)

    ok = bot.run(max_minutes=args.max_minutes, wait_host=not args.no_wait)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
