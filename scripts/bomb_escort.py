"""
👥 bomb_escort.py — 协同模式 v3（贴身保镖炸矿）

跟在user（房主，host 7842）身边下矿：
- 1️⃣ 跟随：同层自然走路（不闪现），跨层才 warp；user静止就走到她身后
- 2️⃣ 攻击：主动进攻 2 格内怪物（自然走过去砍，不被动等）；每次战斗前刷新武器检测
- 5️⃣ 清石头：用镐子敲玩家前进方向 3长×2宽 矩形（以玩家为中轴）内的石头，帮user开路
- 4️⃣ 炸矿：user 8 格内自主放炸弹（复用贪心机制），纯石头不捡掉落直接跟上
- 6️⃣ 敲矿：途径的高价值矿用镐子敲（不浪费炸弹）
- 吃东西是基本保底行为（不列入优先级）
- 撤退：user离开矿井 / HP 低没吃的 → 爬梯撤到矿口

行为优先级：自保(回血/安全)最高 > 跟随是主线 > 2攻击 > 5清石头 > 4炸矿 > 6敲价值矿
（中途做了攻击/清石/炸矿/敲矿，做完再跟上user这条主线）

用法:
  python bomb_escort.py                    # 默认打 7843(AI)，跟随 7842(user)
  python bomb_escort.py --port 7843 --host-port 7842 --bomb Bomb
  python bomb_escort.py --ore-radius 8 --cooldown 25 --max-minutes 30
"""

import sys
import time
import argparse
import os

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
os.environ.setdefault("NAGI_HOST_URL", "http://localhost:7842")

from bomb_common import (BombMiner, log, is_mine_location, BOMB_RADIUS,
                         HIGH_VALUE_ORES, rock_score)


class EscortBot(BombMiner):
    """协同模式：跟user + 主动打怪 + 清石头 + 偶尔炸 + 敲价值矿"""

    def __init__(self, port, host_port, bomb_type="Bomb", ore_radius=8,
                 cooldown=20, hp_threshold=30):
        super().__init__(port=port, host_port=host_port, bomb_type=bomb_type)
        self.ore_radius = ore_radius   # 炸矿范围：user周围 8 格
        self.cooldown = cooldown
        self.hp_threshold = hp_threshold
        self._last_bomb = 0.0

    # ═══════════ 辅助 ═══════════

    def my_pos(self):
        s = self.state()
        p = s.get("player", {})
        return p.get("x", 0), p.get("y", 0)

    def rock_at(self, x, y, radius=6):
        """检查 (x,y) 是否还有可敲岩体（逐下检测）。"""
        rocks, _, _ = self.scan_rocks(radius)
        return any(ax == x and ay == y for ax, ay, _ in rocks)

    def find_stand_near(self, tx, ty):
        """找 (tx,ty) 旁边可站位，返回 (sx, sy, dx, dy) 或 (None,None,0,0)。"""
        rocks, occupied, _ = self.scan_rocks(14)
        return self.find_stand_tile(tx, ty, occupied)

    # ═══════════ 镐子敲石头（逐下检测） ═══════════

    def smash_rock(self, x, y, max_swings=6):
        """站到石头旁→面向→镐子敲直到碎（逐下检测）。返回是否敲碎。
        走路可能 4s 超时没到位，用 position 兜底确保贴脸（敲得到，否则一直敲空）。"""
        sx, sy, dx, dy = self.find_stand_near(x, y)
        if sx is None:
            return False
        loc = self.my_location()
        self.natural_walk(sx, sy, loc, walk_only=True)
        # 走路可能没到位，position 兜底到石头旁（矿内可靠安全）
        self.position(sx, sy)
        time.sleep(0.15)
        self.face_toward(x, y)
        self.select("Pickaxe")
        for _ in range(max_swings):
            self.use_tool("Pickaxe")
            time.sleep(0.45)
            if not self.rock_at(x, y):
                self.select(self.bomb_type)
                return True
        self.select(self.bomb_type)
        return False

    # ═══════════ 5️⃣ 清玩家前方石头 ═══════════

    def clear_ahead_stones(self, hx, hy, facing, rocks, rect_len=3):
        """敲掉玩家前进方向 3长×2宽 矩形（以玩家为中轴）内的石头，帮user开路。
        facing: 0上 1右 2下 3左。rocks 为主循环共享的岩体列表。返回是否敲了。"""
        fx, fy = [(0, -1), (1, 0), (0, 1), (-1, 0)][facing % 4]
        cells = set()
        for d in range(1, rect_len + 1):
            for s in (-1, 1):
                # 面向方向 d 格 + 横向 ±1（垂直于面向方向的 ±1）
                cells.add((hx + fx * d + fy * s, hy + fy * d - fx * s))
        targets = [r for r in rocks if (r[0], r[1]) in cells]
        if not targets:
            return False
        px, py = self.my_pos()
        targets.sort(key=lambda r: abs(r[0] - px) + abs(r[1] - py))
        x, y, name = targets[0]
        log(f"  🪨 帮user敲 {name} ({x},{y})")
        if self.smash_rock(x, y):
            try:
                rocks.remove((x, y, name))
            except ValueError:
                pass
            return True
        return False

    # ═══════════ 2️⃣ 主动攻击 ═══════════

    def combat_aggressive(self, around=None, engage_dist=3, kill_timeout=30):
        """主动进攻：AI 周围 3 格 + user周围 3 格（各一个黑炸弹范围）内的怪，
        追击到砍死为止。甲虫(Bug)打不死/会飞，跳过。超时/怪追太远放弃。
        循环内检查 HP，危险就停（回主循环自保）。返回是否在打。
        目标距离统一按"离 AI"算；最近怪太远(>engage_dist+3)返回 False（不追，先跟近）。"""
        def _targets():
            px, py = self.my_pos()
            ms = list(self.nearby_monsters(engage_dist))
            if around:
                ms += list(self.nearby_monsters(engage_dist, around=around))
            seen, out = set(), []
            for m in ms:
                k = (m[1], m[2])
                if k in seen or "Bug" in m[0]:
                    continue
                seen.add(k)
                ai_d = abs(m[1] - px) + abs(m[2] - py)
                out.append((m[0], m[1], m[2], m[3], ai_d))
            out.sort(key=lambda m: m[4])
            return out

        targets = _targets()
        if not targets:
            return False
        if targets[0][4] > engage_dist + 3:
            return False  # 最近怪也离 AI 太远，先不追（跟近user再打）
        start = time.time()
        swings = 0
        while time.time() - start < kill_timeout:
            if swings % 2 == 0:  # 每 2 刀查一次血（降 HTTP 请求频率）
                self.eat_recovery(hard=self.hp_threshold, target=60)
                if not self.is_safe(self.hp_threshold):
                    return True  # 回不上来，回主循环自保
            targets = _targets()
            if not targets:
                return True  # 范围内怪清完/跑光
            name, mx, my, hp, dist = targets[0]
            if dist > engage_dist + 3:
                return True  # 追太远放弃（避免追出视野）
            if dist > 1:
                self.natural_walk(mx, my, self.my_location(), walk_only=True)
                time.sleep(0.3)
            self.face_toward(mx, my)
            self.detect_weapon()  # 每次战斗前刷新武器
            if self.weapon_name:
                self.select(self.weapon_name)
                time.sleep(0.1)
                self.use_tool()
                time.sleep(0.35)
                self.select(self.bomb_type)
            else:
                self.use_tool("Pickaxe")
                time.sleep(0.4)
            swings += 1
        return True

    # ═══════════ 4️⃣ 8 格内炸矿（贪心复用） ═══════════

    def find_escort_anchor(self, around, rocks, occupied, min_rocks=8):
        """在user一定范围内找满足密度的炸点（简化决策，路径上自己走过去放）：
        - 只扫user附近 ore_radius(8) 的石头
        - 锚点=石头旁空格，爆炸范围内石头 >= min_rocks(8) 就炸
        - 排除已炸锚点 _bombed_anchors（防重复炸上一颗炸弹影响区）
        - 排序 (高价值, 覆盖数, 价值分)
        返回 (ax, ay, covered, has_high) 或 None。"""
        bomb_radius = self.blast_reach()
        eff_radius = bomb_radius + 1
        hx, hy = around
        near = [(x, y, n) for x, y, n in rocks
                if abs(x - hx) + abs(y - hy) <= self.ore_radius]
        if len(near) < min_rocks:
            return None
        # 候选锚点：石头旁空格（排除已炸锚点）
        candidates = set()
        for x, y, n in near:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    ax, ay = x + dx, y + dy
                    if (ax, ay) in occupied or (ax, ay) in self._bombed_anchors:
                        continue
                    if abs(ax - hx) + abs(ay - hy) > self.ore_radius:
                        continue
                    if abs(ax - hx) + abs(ay - hy) <= bomb_radius + 1:
                        continue  # 离user太近，防炸到人
                    candidates.add((ax, ay))
        best = None
        best_key = None
        best_inside = []
        for ax, ay in candidates:
            inside = [(x, y, n) for x, y, n in near
                      if abs(x - ax) + abs(y - ay) <= eff_radius]
            if len(inside) < min_rocks:
                continue  # 密度不够（<8块），镐子敲
            has_high = any(n in HIGH_VALUE_ORES for _, _, n in inside)
            score = sum(rock_score(n) for _, _, n in inside)
            key = (has_high, len(inside), score)
            if best is None or key > best_key:
                best = (ax, ay)
                best_key = key
                best_inside = inside
        if best is None:
            return None
        has_high = any(n in HIGH_VALUE_ORES for _, _, n in best_inside)
        return best[0], best[1], len(best_inside), has_high

    def try_bomb_escort(self, around, rocks, occupied):
        now = time.time()
        if now - self._last_bomb < self.cooldown:
            return False
        if self.count_bombs() <= 0:
            return False
        anchor = self.find_escort_anchor(around, rocks, occupied)
        if not anchor:
            return False
        ax, ay, covered, has_high = anchor
        tag = "💎" if has_high else "🪨"
        log(f"  {tag} 炸 {covered} 块{'高价值矿' if has_high else '石头'} ({ax},{ay})")
        ok, msg, _ = self.bomb_and_collect(ax, ay, collect=has_high)
        if ok:
            self._last_bomb = now
            # 从共享 rocks 移除爆炸影响区（防同轮重复推荐已炸区域）
            eff = self.blast_reach() + 1
            rocks[:] = [r for r in rocks if abs(r[0] - ax) + abs(r[1] - ay) > eff]
            time.sleep(0.5)
            return True
        return False

    # ═══════════ 6️⃣ 敲途径价值矿（镐子） ═══════════

    def mine_surroundings(self, hx, hy, rocks, abandon_dist=10, max_mine=5):
        """炸矿条件不满足时，快速敲周围的矿（高价值优先，普通石头也敲）。
        rocks 为主循环共享的岩体列表（敲碎的从中移除，避免重复）。持续敲到user离开/没矿/敲满。"""
        mined = 0
        while mined < max_mine and rocks:
            px, py = self.my_pos()
            if abs(hx - px) + abs(hy - py) > abandon_dist:
                break  # user走远了，停止敲去跟随
            # 高价值优先，其次最近
            rocks.sort(key=lambda r: (-(r[2] in HIGH_VALUE_ORES),
                                      abs(r[0] - px) + abs(r[1] - py)))
            x, y, name = rocks[0]
            log(f"  ⛏️ 敲 {name} ({x},{y})")
            if not self.smash_rock(x, y):
                break  # 敲不动/失败
            rocks.pop(0)  # 敲掉了，从共享列表移除
            mined += 1
        return mined > 0

    # ═══════════ 主循环 ═══════════

    def run(self, max_minutes=None, grace_checks=3):
        log("👥 === 协同模式启动（1跟随>2攻击>5清石>4炸矿>6敲矿）===")
        self.no_pause_on_unfocus()   # 后台也能走位，不抢user的焦点
        try:
            return self._run_inner(max_minutes, grace_checks)
        finally:
            self.restore_pause_on_unfocus()

    def _run_inner(self, max_minutes=None, grace_checks=3):
        if not self.detect_weapon():
            log("  ⚠️ 没找到武器，只能用镐子防身")
        self.select(self.bomb_type)
        time.sleep(0.2)

        start = time.time()
        deadline = start + max_minutes * 60 if max_minutes else None
        out_of_mine_count = 0
        bombs_placed = 0
        monsters_killed = 0

        # 进矿前就跟user（地面跟随由主循环处理）；若user已在矿、AI 还在外则先追进去
        hl = self.host_location()
        if is_mine_location(hl) and not is_mine_location(self.my_location()):
            log(f"  user在 {hl}，追进去")
            self.safe_warp(hl, x=5, y=5)
            time.sleep(1.0)
        else:
            log("  👣 开始跟随user（地面/矿井通用，进矿后加载行为）")

        while True:
            # ── 时长上限 ──
            if deadline and time.time() > deadline:
                log(f"  ⏱️ 到达时长上限 {max_minutes} 分钟")
                break

            # ── 自保 + 状态（一次读 AI，减少 HTTP） ──
            s_ai = self.state()
            p_ai = s_ai.get("player", {})
            my_loc = s_ai.get("location", {}).get("name", "")
            hp = p_ai.get("health", 0)
            maxhp = p_ai.get("maxHealth", 1)
            hp_pct = hp / maxhp * 100 if maxhp else 0
            tod = s_ai.get("time", {}).get("timeOfDay", 600)
            if hp <= 0 or (maxhp and hp_pct < self.hp_threshold) or tod >= 2430:
                # 紧急：拟人吃 + /heal 兜底（farmhand 吃食物不回血）
                self.eat_if_needed(self.hp_threshold)
                self.eat_recovery(hard=self.hp_threshold, target=60)
                if not self.is_safe(self.hp_threshold):
                    self.retreat_to_entrance("血回不上来，撤退")
                    return False
            elif hp_pct < 60:
                self.eat_recovery(hard=self.hp_threshold, target=60)

            # ── user在哪？（一次 host_state） ──
            hs = self.host_state()
            hl = hs.get("location", {}).get("name", "")
            h = hs.get("player", {})
            hx, hy = h.get("x", 0), h.get("y", 0)

            if is_mine_location(hl):
                # ═══════ user在矿里：追层 + 加载矿内行为 ═══════
                if hl != my_loc:
                    # 跨层/不同地点 → warp 追（AI 在矿外也要追进去）
                    hlv = self.host_mine_level()
                    log(f"  user在 第{hlv}层，追过去")
                    self.safe_warp(hl, x=5, y=5)
                    time.sleep(1.0)
                    continue

                # ═══ 已同层，按优先级执行 ═══
                px, py = p_ai.get("x", 0), p_ai.get("y", 0)
                dist_to_host = abs(hx - px) + abs(hy - py)

                # 2️⃣ 主动攻击（AI/user 3 格黑炸弹范围内怪，追击到砍死）——有怪优先打
                if self.combat_aggressive(around=(hx, hy)):
                    monsters_killed += 1
                    time.sleep(0.3)
                    continue

                # 1️⃣ 跟随（没怪才跟上user；还没贴身不扫石头）
                if dist_to_host > 3:
                    self.follow_host_once(walk_only=True)
                    time.sleep(0.4)
                    continue

                # 一次扫描，清石/炸矿/敲矿共享（3 次 → 1 次 HTTP）
                rocks, occupied, _ = self.scan_rocks(10)

                # 5️⃣ 清玩家前方石头（镐子）
                if self.clear_ahead_stones(hx, hy, h.get("facingDirection", 2), rocks):
                    continue

                # 4️⃣ 炸 8 格内高密度矿（≥8块才炸，路径上自己走过去放）
                if self.try_bomb_escort((hx, hy), rocks, occupied):
                    bombs_placed += 1
                    continue

                # 6️⃣ 炸矿条件不满足 → 快速敲周围的矿（直到user离开一定范围）
                if self.mine_surroundings(hx, hy, rocks, abandon_dist=10):
                    continue

                # 都没做 → 保持跟随（跟紧一点）
                self.follow_host_once(walk_only=True)
                time.sleep(0.8)

            else:
                # ═══════ user在地面：只跟随，不下矿不加载行为 ═══════
                if is_mine_location(self.my_location()):
                    # AI 还在矿里user走了 → 撤退出矿再跟
                    self.retreat_to_entrance("user离开矿井，撤退出矿")
                    continue
                self.follow_host_once(walk_only=True)
                time.sleep(0.5)

        # ── 结束 ──
        log("\n👋 === 协同模式结束 ===")
        log(f"  跟随user | 炸了 {bombs_placed} 个炸弹 | 砍了 {monsters_killed} 个怪")
        self.retreat_to_entrance("协同结束")
        return True


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="[bomb] 协同模式 — 跟着user下矿炸矿")
    parser.add_argument("--port", type=int, default=None, help="AI 角色端口（默认7843）")
    parser.add_argument("--host-port", type=int, default=None, help="房主(user)端口（默认7842）")
    parser.add_argument("--bomb", type=str, default="Bomb", help="炸弹类型：Bomb/Mega Bomb/Cherry Bomb")
    parser.add_argument("--ore-radius", type=int, default=8, help="炸矿范围：user周围多少格内（默认8）")
    parser.add_argument("--cooldown", type=int, default=20, help="两次炸弹最小间隔秒数（默认20）")
    parser.add_argument("--hp-threshold", type=int, default=30, help="血量低于此%撤退（默认30）")
    parser.add_argument("--max-minutes", type=int, default=None, help="最多跟随分钟数")
    args = parser.parse_args()

    from bomb_common import NAGI_URL, HOST_URL
    import re as _re
    port = args.port or int(_re.search(r'(\d+)', NAGI_URL).group(1))
    hport = args.host_port or int(_re.search(r'(\d+)', HOST_URL).group(1))

    bot = EscortBot(port, hport, bomb_type=args.bomb,
                    ore_radius=args.ore_radius, cooldown=args.cooldown,
                    hp_threshold=args.hp_threshold)
    bot.run(max_minutes=args.max_minutes)


if __name__ == "__main__":
    main()
