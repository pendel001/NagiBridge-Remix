"""
⛏️⭐ mine_run.py v2 — 鹈鹕镇矿井双模式自动下矿

功能:
  冲层模式: warp → 敲石头 → 找梯子 → 下楼 → 循环到目标层
  刷矿模式: warp 到电梯层 → 敲指定矿 → 出门重进 → 循环
  精准敲击: 根据镐子级别 + 岩石类型 + 层数算好次数，不浪费体力
  自动战斗: 附近有怪自动切剑砍
  自动进食: 状态食物/回血食物分开吃
  顺手捡拾: 路过看到石英/山洞萝卜等就捡

用法:
  python mine_run.py --mode rush --target 80              # 从进度恢复，冲到80
  python mine_run.py --mode rush --start 1 --target 80    # 强制从1层开始
  python mine_run.py --mode rush --no-resume --target 80  # 不读进度
  python mine_run.py --mode farm --ore Iron --cycles 5    # 刷铁矿
  python mine_run.py --mode rush --target 40 --food-sta Salad --food-hp Cheese
  python mine_run.py --check-progress                     # 查看已到达最深

参数:
  --mode          rush / farm（默认 rush）
  --start         起始层数（默认 1，配合 --no-resume 可强制从1开始）
  --target        目标层数（rush）/ --ore 指定矿（farm）
  --cycles        刷矿循环次数（farm 模式，默认 5）
  --resume / --no-resume   是否从已到达最深恢复（rush默认resume）
  --hp-threshold  血量低于此 % 吃食物（默认 50）
  --sta-threshold 体力低于此 % 吃食物（默认 20）
  --food-sta      体力食物名称（如 Salad）
  --food-hp       回血食物名称（如 Cheese）
  --port          NagiBridge 端口（默认 7842）
  --check-progress  查看已到达的最深层数，不挖矿
  --reset-progress  重置进度文件
"""

import sys
import os
import json
import time
import re
import argparse
import math
import requests

# ⚔️ 2026-09-06 复用 bomb 的武器系统（WeaponMixin：选武器/类别/挥速自适应/锤子重砸）
from bomb_common import WeaponMixin

# ── 常量 ──

TOOL_DELAY = 0.85          # 每次挥工具后的等待（秒）
SCAN_RADIUS = 14           # surroundings 扫描半径
WALK_TIMEOUT = 45          # 单次 /walk_to 超时
LADDER_SCAN_INTERVAL = 4   # 每敲 N 块石头扫一次梯子
MONSTER_SCAN_INTERVAL = 3  # 每敲 N 块石头扫一次怪物
PICKUP_SCAN_INTERVAL = 3   # 每敲 N 块石头扫一次地上物品

NAGI_URL = os.environ.get("NAGI_URL", "http://localhost:7842")

# 跳过不敲的矿石（暂时没有需要跳过的）
SKIP_ROCKS = set()

# 可拾取的矿井地面物品
FORAGABLES = {
    "Cave Carrot", "Quartz", "Earth Crystal", "Frozen Tear",
    "Fire Quartz", "Diamond", "Prismatic Shard",
    "Purple Mushroom", "Red Mushroom", "Common Mushroom",
}

# 进食挑食兜底：老 DLL 无 edibleValue/healthRecovered 时按这些关键词/排除项认食物
_FOOD_KEYWORDS = {"Salad", "Bread", "Cheese", "Fish", "Soup", "Stew",
                  "Berry", "Fruit", "Mushroom", "Egg", "Milk", "Juice",
                  "Coffee", "Tea", "Cake", "Cookie", "Pie", "Brew",
                  "Wine", "Beer", "Mead", "Pale", "Sashimi", "Sushi",
                  "Curry", "Stir", "Pancake", "Omelet", "Porridge",
                  "Bagel", "Tortilla", "Wrap", "Taco", "Burrito",
                  "Hashbrowns", "Pancakes", "Bacon", "Carp", "Chub",
                  "Perch", "Salmon", "Trout", "Tuna", "Bream", "Bass",
                  "Algae", "Seaweed", "Crab", "Lobster", "Shrimp",
                  "Chowder", "Escargot", "Oyster", "Ceviche",
                  "Hot Pepper", "Cheese Cauli", "Parmesan", "Pizza",
                  "Chocolate", "Ice Cream", "Rice Pudding",
                  "Plum Pudding", "Blackberry Cobbler", "Apple",
                  "Apricot", "Banana", "Cherry", "Coconut", "Mango",
                  "Orange", "Peach", "Pomegranate", "Tomato"}
_NON_FOOD = {"Keg", "Preserves Jar", "Cheese Press", "Loom", "Spinning Wheel",
             "Mayonnaise Machine", "Oil Maker", "Seed Maker", "Crystalarium",
             "Furnace", "Charcoal Kiln", "Tapper", "Recycling Machine",
             "Worm Bin", "Slime Egg", "Slime Incubator", "Crab Pot"}

# 吃东西阈值（HP<60% 吃食物）；撤退阈值用 --hp-threshold（默认 30）——两者分开，别重合
EAT_HP = 60

# 矿节点 itemId → 矿石名（SDV 1.6 矿节点 Name 报 'Stone'，靠 objId 区分）
# 实测 1.6 矿节点 itemId：751铜 / 290铁 / 764金 / 765铱 / 767神秘石
ORE_NODE_IDS = {
    "(O)751": "Copper Node", "(O)290": "Iron Node",
    "(O)764": "Gold Node", "(O)765": "Iridium Node", "(O)767": "Mystic Stone",
    "(O)CalicoEggStone_1": "Calico Egg Stone",   # 🥚 沙漠节骷髅洞蛋矿（2026-08-18 实测：objId 名字ID，Name 报 Stone）
}

# objId → dump_tile 真名(宝石/放射矿等 Name 也报 'Stone' 的节点解析用,https 2026-08-29)
_TILENAME = {}

# 梯子/竖井名称
LADDER_NAMES = {"Ladder", "MineShaft"}

# 武器关键词（用于在背包里找武器）
WEAPON_KEYWORDS = {"Sword", "Blade", "Saber", "Club", "Hammer", "Dagger",
                   "Galaxy", "Katana", "Kunai", "Rapier", "Whip", "Foam",
                   "SlingShot", "Slingshot", "Scythe"}

# ── 进度记录（已到达最深层） ──
PROGRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mine_progress.json")

def load_progress():
    """读取已到达最深层数，没有则返回 0"""
    try:
        with open(PROGRESS_FILE) as f:
            data = json.load(f)
            return data.get("deepest_level", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

def save_progress(deepest):
    """保存已到达最深层数"""
    with open(PROGRESS_FILE, 'w') as f:
        json.dump({"deepest_level": deepest}, f)

def update_progress(level):
    """如果 level 比记录深，更新进度"""
    deepest = load_progress()
    if level > deepest:
        save_progress(level)
        log(f"  🏆 新纪录！最深到达第 {level} 层")
        return True
    return False

def _is_town_mine(loc: str) -> bool:
    """是否为鹈鹕镇矿井（Mine 建筑 或 UndergroundMine≤120）。头骨(≥121)/火山不算。"""
    if loc == "Mine":
        return True
    m = re.search(r'UndergroundMine(\d+)', loc or "")
    return bool(m) and int(m.group(1)) <= 120


def resume_start_level(port: int = None) -> int:
    """动态算鹈鹕镇矿井起始层（替代静态进度文件，2026-08-22）。
    读当前地点 → **确实在地下矿层(UndergroundMine≤120)就按当前所在层继续**，
    否则（入口建筑 Mine / 户外 / 头骨火山）按起始=1。
    ⚠️ 2026-09-06 缠修：原逻辑在"镇矿地点就读电梯 maxFloor"——正常态电梯恒满 120，
       `start>=target` 判成"不挖了"→ 只要 AI 停在镇矿里（满包停/手动停/下到入口）就**每次 mine go 空跑**。
       改成按玩家当前真实所在层恢复 + 不在矿层就回 1；头骨/火山(≥121)不在这走。"""
    base = f"http://localhost:{port}" if port else NAGI_URL
    try:
        s = requests.get(f"{base}/state", timeout=10).json()
    except Exception:
        return 1
    loc = (s.get("location") or {}).get("name", "")
    lv = extract_mine_level(loc)
    # 只在确实在地下矿层时按"当前层"恢复；入口建筑(Mine)/户外/收矿停上都从1开始
    if lv is not None and lv <= 120:
        return max(1, lv)
    return 1

# ── 日志 ──

def log(msg):
    try:
        print(f"[mine] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            # GBK 系统上 emoji 打不了，用 ? 替换
            enc = sys.stdout.encoding or 'utf-8'
            safe = msg.encode(enc, errors='replace').decode(enc, errors='replace')
            print(f"[mine] {safe}", flush=True)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
#  API 封装
# ═══════════════════════════════════════════════════════════════════

def _get(ep, params=None):
    return requests.get(f"{NAGI_URL}{ep}", params=params, timeout=10).json()

def _post(ep, data=None):
    return requests.post(f"{NAGI_URL}{ep}", json=data or {}, timeout=10).json()


def extract_mine_level(loc_name):
    """从 'UndergroundMine42' 提取 42；不是矿井返回 None"""
    m = re.search(r'UndergroundMine(\d+)', loc_name or "")
    return int(m.group(1)) if m else None


def is_mine_location(loc_name):
    return loc_name and "UndergroundMine" in loc_name


# ═══════════════════════════════════════════════════════════════════
#  MineBot
# ═══════════════════════════════════════════════════════════════════

class MineBot(WeaponMixin):
    def __init__(self, port):
        self.port = port
        self.base = f"http://localhost:{port}"
        self.sess = requests.Session()
        self.sess.timeout = 10

        # 检测到的信息
        self.pickaxe_level = 0       # 0-4
        self.pickaxe_name = "Basic"
        self.weapon_name = None      # 背包里找到的武器
        self.weapon_class = None     # 复用 bomb WeaponMixin：hammer/sword/dagger——挥击节奏 & 锤子重砸判定
        self.weapon_speed = 0        # 武器速度 stat——挥击间隔自适应用
        self.weapon_override = None  # 可选：指定用某把武器
        self._last_special = 0.0     # 锤子重砸冷却跟踪
        self.bomb_type = "Pickaxe"   # WeaponMixin.swing 挥完切回的工具（下矿用镐子挖）
        self.mine_level = 0          # 当前矿井层数
        self._rock_count = 0         # 当前层敲了多少块

    # ── 底层 API ──

    def _get(self, ep, params=None):
        return self.sess.get(f"{self.base}{ep}", params=params, timeout=10).json()

    def _post(self, ep, data=None):
        return self.sess.post(f"{self.base}{ep}", json=data or {}, timeout=10).json()

    # ── 状态 ──

    def state(self):
        return self._get("/state")

    def mine_elevator_max(self):
        """🪜 读鹈鹕镇矿井电梯当前可达最大层 → (maxFloor, reset)。读失败兜底 (1, True)。
        正常态 maxFloor=当前最高可达（5倍数）；接"深处的危险"重置后 floor 空 → reset → 1。"""
        try:
            r = self._get("/mine/elevator")
            if r.get("ok"):
                return int(r.get("maxFloor", 1) or 1), bool(r.get("reset", False))
        except Exception:
            pass
        return 1, True

    def surroundings(self, radius=SCAN_RADIUS):
        return self._get("/surroundings", {"radius": radius})

    def alerts(self):
        return self._get("/alerts")

    def status(self):
        return self._get("/status")

    # ── 行动 ──

    def select(self, name):
        return self._post("/select", {"name": name})

    def use_tool(self, name=None):
        d = {}
        if name: d["name"] = name
        return self._post("/tool", d)

    def face(self, direction):
        return self._post("/face", {"direction": direction})

    def warp(self, location, x=5, y=5):
        d = {"location": location}
        if x is not None: d["x"] = x
        if y is not None: d["y"] = y
        return self._post("/warp", d)

    def use_item(self):
        return self._post("/use", {"force": True})

    def interact(self):
        return self._post("/interact")

    def walk_to_coord(self, location, x, y):
        return self._post("/walk_to", {"location": location, "x": x, "y": y})

    # ── 工具检测 ──

    def detect_pickaxe(self):
        """从 state 读取当前工具，解析镐子级别"""
        s = self.state()
        tool = s.get("player", {}).get("currentTool", "")
        if not tool:
            log("  ⚠️ 没有选中任何工具")
            return False

        # 提取镐子前缀 "Iridium Pickaxe" → "Iridium"
        levels = {"Iridium": 4, "Gold": 3, "Steel": 2, "Iron": 2, "Copper": 1}
        self.pickaxe_name = "Basic"
        self.pickaxe_level = 0
        for prefix, lv in sorted(levels.items(), key=lambda x: -x[1]):
            if prefix in tool:
                self.pickaxe_name = prefix
                self.pickaxe_level = lv
                break
        log(f"  Tool: {tool} (level {self.pickaxe_level})")
        return True

    # ⚔️ detect_weapon 已复用 bomb_common.WeaponMixin（选武器/类别/挥速，跳过工具镰）——不再本地定义

    def preflight(self, hp_threshold):
        """启动预检（2026-08-16 恒）：返回硬性拦截原因（str=阻止启动）；黄色警告只 log 不拦。
        两硬两黄：无镐子/血量过低 → 硬；无武器/背包快满 → 黄。"""
        try:
            s = self.state()
            inv = s.get("inventory", [])
            p = s.get("player", {})
            # 硬性：没镐子 → 没法挖
            if not any("Pickaxe" in (i.get("name") or "") for i in inv):
                return "❌ 背包没有镐子，没法挖矿——先去拿/买镐子再来"
            # 硬性：血量低于阈值 → 会死
            mhp = p.get("maxHealth") or 1
            hp = p.get("health") or 0
            if mhp > 0 and hp * 100 / mhp < hp_threshold:
                return f"❌ 当前血量 {hp}/{mhp}（{hp*100/mhp:.0f}%）低于阈值 {hp_threshold}%——先回血/睡觉再来"
            # 黄色：没武器 → 只能用镐子防身（不拦）
            if not any(any(kw in (i.get("name") or "") for kw in WEAPON_KEYWORDS) for i in inv):
                log("  ⚠️ 背包没武器，只能用镐子防身（下矿危险，建议先带剑）")
            # 黄色：背包快满 → 腾格（不拦）
            max_items = p.get("maxItems") or 36
            free = max_items - len(inv)
            if free < 8:
                log(f"  ⚠️ 背包快满（只剩 {free} 格），挖矿会满包——先 chest_store 腾格")
        except Exception as e:
            log(f"  ⚠️ 预检异常（继续启动）: {e}")
        return None

    def detect_inventory_food(self):
        """查背包里能吃的（按回体力/回血值挑，2026-09-06 修复"按关键词乱抓→血低吃咖啡"）。
        优先用 /state 新字段 edibleValue(体力)+healthRecovered(血)（机器/工具=0 自动排除）；
        旧 DLL 无这些字段时退回关键词+类别兜底。
        返回 [(name, edibleValue, healthRecovered), ...]。"""
        s = self.state()
        inv = s.get("inventory", [])
        has_info = any("edibleValue" in (it or {}) for it in inv)  # 新 DLL 才有
        foods = []
        seen = set()
        for item in inv:
            if not item:
                continue
            name = item.get("name", "")
            if not name or name in seen:
                continue
            if has_info:
                ed = int(item.get("edibleValue") or 0)
                hp = int(item.get("healthRecovered") or 0)
                if ed > 0 or hp > 0:  # 真能吃（机器/工具=0 自动排除）
                    seen.add(name)
                    foods.append((name, ed, hp))
            else:
                # 兜底：关键词 + 类别（老 DLL 无回血/体力值，回血当 0）
                cat = item.get("category", "")
                if (any(kw.lower() in name.lower() for kw in _FOOD_KEYWORDS)
                        or cat in ("Cooking", "Fish", "Vegetable", "Fruit",
                                   "Flower", "Forage", "Artisan Goods", "Syrup")):
                    if name not in _NON_FOOD:
                        seen.add(name)
                        foods.append((name, 1, 0))
        return foods

    # ── 导航 ──

    def wait_arrival(self, target_map=None, target_x=None, target_y=None, timeout=WALK_TIMEOUT):
        """轮询直到到达目标位置"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            # 方法1：查 alerts
            try:
                a = self.alerts()
                for alert in a.get("alerts", []):
                    if alert.get("type") == "walk_completed":
                        return True
            except Exception:
                pass

            # 方法2：查 state
            try:
                s = self.state()
                p = s.get("player", {})
                if not p.get("isMoving", False):
                    # 不移动了，检查是否在目标地图
                    loc = s.get("location", {}).get("name", "")
                    if target_map is None or loc == target_map:
                        if target_x is None or (
                            abs(p.get("x", 0) - target_x) <= 3 and
                            abs(p.get("y", 0) - target_y) <= 3
                        ):
                            return True
            except Exception:
                pass

            time.sleep(0.3)

        # 超时 → 强制停止
        log(f"  ⚠️ 导航超时 ({timeout}s)，强制停止")
        try:
            self._post("/stop")
        except Exception:
            pass
        return False

    def safe_walk_to(self, x, y, location=None, timeout=WALK_TIMEOUT):
        """先读当前地图，再发起 walk_to，等待完成"""
        if not location:
            s = self.state()
            location = s.get("location", {}).get("name", "")
        try:
            r = self.walk_to_coord(location, x, y)
            if not r.get("ok"):
                log(f"  ⚠️ walk_to({x},{y}) 发起失败: {r.get('error','?')}")
                return False
        except Exception as e:
            log(f"  ⚠️ walk_to 请求异常: {e}")
            return False
        return self.wait_arrival(location, x, y, timeout)

    def safe_warp(self, location, x=5, y=5):
        """warp 并等待到达"""
        r = self.warp(location, x, y)
        if not r.get("ok"):
            log(f"  ⚠️ warp({location}) 失败: {r.get('error','?')}")
            return False
        time.sleep(1.5)
        s = self.state()
        loc = s.get("location", {}).get("name", "")
        if loc == location:
            return True
        # 再等等
        for _ in range(10):
            time.sleep(0.5)
            s = self.state()
            loc = s.get("location", {}).get("name", "")
            if loc == location:
                return True
        return False

    def is_worth_mining(self, rock_name):
        """判断值不值得敲——**只认真能敲碎的矿**（名字含 Stone/Node 或 ORE_NODE_IDS）。
        ⚠️ 2026-09-06 空敲根因：find_rocks 把掉落/采集物(Coal/Sea Urchin/蘑菇…)当"矿"返回，
        mine_rock 敲不动→每个掉落就空挥3次。真矿名都带 Stone/Node（1.6 矿节点 Name 报 Stone 靠 objId
        在 _rock_name 里已解析成真名）；掉落/采集名不带，直接排除。"""
        if rock_name in SKIP_ROCKS:
            return False
        if "Stone" in (rock_name or "") or "Node" in (rock_name or ""):
            return True
        if rock_name in ORE_NODE_IDS.values():
            return True
        return False

    # ── 周围扫描 ──

    def _tile_name(self, oid, x, y):
        """objId 隐藏名(宝石/放射矿等 node 的 Name 也报 'Stone')→ dump_tile 真名。cache。
        返回真名;解析失败返回 oid。"""
        if oid in _TILENAME:
            return _TILENAME[oid]
        nm = oid
        try:
            r = self._get("/dump_tile", {"x": x, "y": y})
            o = ((r or {}).get("tile") or {}).get("object") or {}
            nm = o.get("name") or oid
        except Exception:
            pass
        _TILENAME[oid] = nm
        return nm

    def _rock_name(self, t):
        """解析 tile 的矿石名：object 名优先(报 'Iron Node' 等)，
        否则用 objId 兜底映射(ORE_NODE_IDS)；再不行(宝石/放射/未实测节点
        Name 也报 'Stone')→ dump_tile 真名。2026-08-29 恒拍板:别猜,按真名认。"""
        o = t.get("object")
        if o and o != "Stone":
            return o
        oid = t.get("objId")
        if oid in ORE_NODE_IDS:
            return ORE_NODE_IDS[oid]
        if oid:
            real = self._tile_name(oid, t["x"], t["y"])
            if real and real != oid and real != "石头" and str(real).lower() != "stone":
                return real
        return o

    def find_rocks(self, priority_ore=None, radius=SCAN_RADIUS):
        """扫 surroundings，返回可敲的矿石（按优先级 + 距离排序）

        priority_ore: 优先挖的矿石名，如 "Iron Node"
        radius: 扫描半径（farm 扫全层用 30 覆盖整层）
        排序: 优先级矿 > 宝石 > 晶球 > 铜 > 铁 > 金 > 石头
        """
        data = self.surroundings(radius)
        px, py = data["center"]["x"], data["center"]["y"]

        # 优先级排序(2026-08-29 改成关键词:宝石/放射矿 Name 报 'Stone' 或 'X Stone',
        # 不再只认硬编码的 "X Node" 名字)——放射矿最值钱，其次宝石
        def ore_score(name):
            if priority_ore and name == priority_ore:
                return 0         # 最高优先级
            if "Radioactive" in name:
                return 1         # 放射矿(1.6 最值钱)
            if any(g in name for g in ("Gem", "Jade", "Ruby", "Emerald", "Diamond",
                                       "Topaz", "Amethyst", "Aquamarine")):
                return 2         # 宝石(值钱)
            if name == "Copper Node":
                return 3
            if name == "Iron Node":
                return 4
            if name == "Gold Node":
                return 5
            if name == "Stone":
                return 6         # 石头最后
            return 9

        targets = []
        for t in data.get("tiles", []):
            obj = self._rock_name(t)
            if obj and self.is_worth_mining(obj):
                dist = abs(t["x"] - px) + abs(t["y"] - py)
                score = ore_score(obj)
                targets.append((score, dist, t["x"], t["y"], obj))

        targets.sort(key=lambda t: (t[0], t[1]))
        return [(x, y, name) for _, _, x, y, name in targets]

    def try_descend(self):
        """尝试下楼：按 confirm 键，检查是否切换了地图
        返回 True 如果成功下楼，False 如果没反应
        """
        old_loc = self.state().get("location", {}).get("name", "")
        try:
            self._post("/key", {"key": "confirm", "count": 1})
        except Exception:
            pass
        time.sleep(1.0)
        new_loc = self.state().get("location", {}).get("name", "")
        return new_loc != old_loc and "UndergroundMine" in new_loc

    def find_ladder_by_search(self, location):
        """暴力搜梯子：在周围 tile 瞬移并按 Down，直到下楼
        返回 (x, y, "Ladder") 或 None
        """
        # 先在当前位置试
        if self.try_descend():
            s = self.state()
            nx, ny = s["player"]["x"], s["player"]["y"]
            level = extract_mine_level(s.get("location", {}).get("name", ""))
            if level:
                self.mine_level = level
                self._rock_count = 0
                log(f"  ✅ 就踩在梯子上！下到第 {level} 层")
                return (nx, ny, "Ladder")

        # 逐层扩展搜索（从近到远）
        s = self.state()
        px, py = s["player"]["x"], s["player"]["y"]
        data = self.surroundings(SCAN_RADIUS)

        # 收集可走的 tile
        walkable = [(t["x"], t["y"]) for t in data.get("tiles", [])
                     if t.get("passable", True)]
        # 去重 + 按距离排序
        seen = set()
        unique = []
        for x, y in walkable:
            if (x, y) not in seen:
                seen.add((x, y))
                dist = abs(x - px) + abs(y - py)
                unique.append((dist, x, y))
        unique.sort()

        # 先查玩家附近的 tile（最有可能是梯子）
        for dist, tx, ty in unique:
            if dist > 10:  # 先搜 10 格内的
                break
            if dist < 1:
                continue  # 已经试过当前位置了

            self.mine_teleport(tx, ty)
            time.sleep(0.1)
            if self.try_descend():
                s = self.state()
                level = extract_mine_level(s.get("location", {}).get("name", ""))
                if level:
                    self.mine_level = level
                    self._rock_count = 0
                    log(f"  🪜 梯子找到！({tx},{ty}) → 第 {level} 层")
                    return "ALREADY_DOWN"

        # 都没找到 → 可能还没出梯子
        return None

    def find_ladder_api(self):
        """调 /ladder 端点，但过滤掉入口梯子（玩家站的位置）"""
        try:
            r = self._get("/ladder")
            if r.get("ok"):
                s = self.state()
                px, py = s["player"]["x"], s["player"]["y"]
                ladder = r.get("ladder")
                shaft = r.get("shaft")
                # 入口梯子就是玩家出生点，忽略
                if ladder:
                    lx, ly = ladder["x"], ladder["y"]
                    if abs(lx - px) <= 1 and abs(ly - py) <= 1:
                        return None  # 过滤掉入口梯子
                    return (lx, ly, "Ladder")
                if shaft:
                    sx, sy = shaft["x"], shaft["y"]
                    if abs(sx - px) <= 1 and abs(sy - py) <= 1:
                        return None
                    return (sx, sy, "MineShaft")
        except Exception:
            pass
        return None

    def detect_ladder(self, location, brute=False):
        """综合梯子检测：调 API 查梯子，可指定暴力搜
        返回: (x, y, name) 或 "ALREADY_DOWN" 或 None

        brute=True 时在 API 没结果后会暴力搜全图
        """
        # 先试 API
        r = self.find_ladder_api()
        if r:
            log(f"  🪜 梯子 ({r[0]},{r[1]})")
            return r
        # 需要暴力搜？
        if brute:
            log(f"  🔍 暴力搜梯子...")
            return self.find_ladder_by_search(location)
        return None

    def find_foragables(self):
        """扫 surroundings 找可拾取的地面物品"""
        data = self.surroundings(SCAN_RADIUS)
        px, py = data["center"]["x"], data["center"]["y"]
        items = []
        for t in data.get("tiles", []):
            obj = t.get("object")
            if obj and obj in FORAGABLES:
                dist = abs(t["x"] - px) + abs(t["y"] - py)
                items.append((dist, t["x"], t["y"], obj))
        items.sort(key=lambda x: x[0])
        return [(x, y, name) for _, x, y, name in items]

    def nearby_monsters(self, radius=6):
        """扫 surroundings 找怪物，按距离排序"""
        data = self.surroundings(SCAN_RADIUS)
        cx, cy = data["center"]["x"], data["center"]["y"]
        monsters = []
        for m in data.get("monsters", []):
            dist = abs(m["x"] - cx) + abs(m["y"] - cy)
            monsters.append((m["name"], m["x"], m["y"], m["health"], dist))
        monsters.sort(key=lambda m: m[4])
        return monsters

    def find_adjacent_tile(self, tx, ty, radius=3):
        """找目标旁边的可站位，**挑离自己最近的可站边**（2026-09-06 恒：原"优先右边"会绕路跑到右侧朝左敲，改就近省走位+别傻绕）。
        返回 (nx, ny, dx, dy)：dx/dy=目标→站位的偏移，用于决定面向。"""
        data = self.surroundings(radius)
        blocked = set()
        for t in data.get("tiles", []):
            if not t.get("passable", True):
                blocked.add((t["x"], t["y"]))
        # 玩家当前坐标（就近排序用；读不到就退固定序）
        px = py = -1
        try:
            p = self.state().get("player", {})
            px = int(p.get("x", 0) or 0)
            py = int(p.get("y", 0) or 0)
        except Exception:
            pass
        # 候选：目标 4 邻位可站格，按到玩家曼哈顿距离排序（就近；等距按上下右左定序）
        cand = []
        for dx, dy in [(0, -1), (1, 0), (-1, 0), (0, 1)]:
            nx, ny = tx + dx, ty + dy
            if (nx, ny) not in blocked:
                cand.append((abs(nx - px) + abs(ny - py), nx, ny, dx, dy))
        if cand:
            cand.sort()
            _, nx, ny, dx, dy = cand[0]
            return nx, ny, dx, dy
        # 兜底：对角线
        for dx, dy in [(1, -1), (1, 1), (-1, -1), (-1, 1)]:
            nx, ny = tx + dx, ty + dy
            if (nx, ny) not in blocked:
                return nx, ny, dx, dy
        # 兜底：目标本身
        return tx, ty, 0, 0

    def face_toward(self, tx, ty):
        """面向目标坐标"""
        s = self.state()
        px = s["player"]["x"]
        py = s["player"]["y"]
        dx = tx - px
        dy = ty - py
        if abs(dx) > abs(dy):
            self.face(1 if dx > 0 else 3)
        else:
            self.face(2 if dy > 0 else 0)

    # ── 矿洞内移动（近的走两步，远的才闪） ──

    def mine_teleport(self, x, y):
        """矿洞内瞬移到指定坐标"""
        try:
            r = self._post("/position", {"x": x, "y": y})
            if r.get("ok"):
                time.sleep(0.15)
                return True
            return False
        except Exception as e:
            return False

    def safe_teleport(self, x, y):
        """position 到可走格：目标不可走就找相邻可走格（防卡墙）。"""
        self.mine_teleport(x, y)
        time.sleep(0.2)
        try:
            d = self.surroundings(2)
            cx, cy = d.get("center", {}).get("x", 0), d.get("center", {}).get("y", 0)
            walkable = any(t["x"] == cx and t["y"] == cy and t.get("passable", True)
                           for t in d.get("tiles", []))
            if not walkable:
                adj_x, adj_y, _, _ = self.find_adjacent_tile(x, y)
                self.mine_teleport(adj_x, adj_y)
                time.sleep(0.2)
        except Exception:
            pass
        return self.state().get("player", {}).get("x", x), self.state().get("player", {}).get("y", y)

    def natural_walk(self, tx, ty, location=None):
        """装模作样走路：近的用 go_to 走路，远的才闪现"""
        if not location:
            s = self.state()
            location = s.get("location", {}).get("name", "")

        s = self.state()
        px, py = s["player"]["x"], s["player"]["y"]
        dist = abs(tx - px) + abs(ty - py)

        if dist <= 1:
            return True  # 到了
        elif dist <= 4:
            # 短距离：用游戏自带 /walk_to 走路（自然）
            try:
                r = self.walk_to_coord(location, tx, ty)
                if r.get("ok"):
                    return self.wait_arrival(location, tx, ty, timeout=15)
            except:
                pass
            return self.mine_teleport(tx, ty)
        else:
            # 远距离：直接闪现
            return self.mine_teleport(tx, ty)

    # ── 核心操作 ──

    def mine_rock(self, x, y, name, location, teleport=False):
        """走到石头前，敲——检查——敲碎为止"""
        adj_x, adj_y, dx, dy = self.find_adjacent_tile(x, y)

        # teleport=True 直接瞬移（farm 刷矿）；否则矿洞内装模作样走，外面用 /walk_to
        if teleport:
            ok = self.mine_teleport(adj_x, adj_y)
        elif is_mine_location(location):
            ok = self.natural_walk(adj_x, adj_y)
        else:
            ok = self.safe_walk_to(adj_x, adj_y, location, timeout=30)

        if not ok:
            log(f"  ⚠️ 走不到 ({x},{y}) {name}，跳过")
            return "skip"

        # 面向石头
        if dx == 1: self.face(3)
        elif dx == -1: self.face(1)
        elif dy == 1: self.face(0)
        elif dy == -1: self.face(2)
        else: self.face_toward(x, y)
        time.sleep(0.15)

        # 敲——检查——再敲——敲碎或重试
        empty_swings = 0
        for blow in range(15):
            s = self.state()
            if s["player"]["health"] <= 0:
                return "dead"

            self.use_tool("Pickaxe")
            time.sleep(0.4)

            # 检查石头还在不在:目标那格还顶着**可敲的矿**(Stone/Node 之类)才算没碎——
            # ⚠️ 2026-09-06 空敲根因：原来只判"有 object"(掉落物 Coal/矿石也是 object)，
            #    rock 敲碎后掉落物还在那格 → 误判"没碎" → 空挥循环。改按 _rock_name 判是不是真矿。
            r = self.surroundings(3)
            still_there = any(
                t["x"] == x and t["y"] == y
                and self.is_worth_mining(self._rock_name(t))
                for t in r.get("tiles", [])
            )
            if not still_there:
                self._rock_count += 1
                # 敲碎了：等掉落落地（1s），走上去捡——position 瞬移不触发拾取，必须走路
                time.sleep(1.0)
                try:
                    self.safe_walk_to(x, y, location, timeout=15)
                    time.sleep(0.3)
                except Exception:
                    pass
                return "ok"

            # 连续空敲3次 → 重新站位
            empty_swings += 1
            if empty_swings >= 3:
                log(f"  空敲{empty_swings}次，重新站位...")
                if is_mine_location(location):
                    self.mine_teleport(adj_x, adj_y)
                else:
                    self.safe_walk_to(adj_x, adj_y, location, timeout=15)
                if dx == 1: self.face(3)
                elif dx == -1: self.face(1)
                elif dy == 1: self.face(0)
                elif dy == -1: self.face(2)
                time.sleep(0.15)
                empty_swings = 0

        log(f"  ⚠️ {name} ({x},{y}) 敲了15下没碎，跳过")
        return "skip"

    def pickup_foragables(self, location):
        """捡附近的地面物品（只捡 5 格内的，不走远）"""
        items = self.find_foragables()
        picked = 0
        for fx, fy, fname in items[:3]:  # 一次最多捡 3 个
            dist = abs(fx - self._last_px()) + abs(fy - self._last_py())
            if dist > 5:
                continue
            log(f"  🥕 捡 {fname} ({fx},{fy})")
            if is_mine_location(location):
                self.natural_walk(fx, fy)
            else:
                self.safe_walk_to(fx, fy, location, timeout=15)
            time.sleep(0.2)
            picked += 1
        return picked

    def _last_px(self):
        try:
            s = self.state()
            return s["player"]["x"]
        except:
            return 0

    def _last_py(self):
        try:
            s = self.state()
            return s["player"]["y"]
        except:
            return 0

    def combat_step(self, location):
        """检查附近怪物，贴脸/近身就砍。⚠️ 2026-09-06 修：原来距离2只"警戒"不反击（怪物磨血不还手）。
        距离≤1 直接砍；距离2 先贴到旁边再砍（能还手就别傻等）。"""
        monsters = self.nearby_monsters(radius=4)
        if not monsters:
            return "safe"

        name, mx, my, hp, dist = monsters[0]
        if hp <= 0:
            return "safe"  # 已死
        if dist <= 1:
            # 贴脸了！砍它（复用 bomb WeaponMixin.swing：挥速自适应 + 锤子重砸 + 挥完切回镐子）
            log(f"  ⚔️ 怪物 {name} 贴脸！({mx},{my}) HP={hp}")
            self.swing((mx, my), special=self.weapon_class == "hammer")
            return "fighting"
        elif dist <= 2:
            # 近身：先瞬移到旁边再砍，别被磨血还站桩
            log(f"  ⚔️ 怪物 {name} 近身 ({mx},{my})，贴近反击")
            adj_x, adj_y, _, _ = self.find_adjacent_tile(mx, my)
            self.mine_teleport(adj_x, adj_y)
            time.sleep(0.15)
            self.swing((mx, my), special=self.weapon_class == "hammer")
            return "fighting"
        elif dist <= 4:
            # 在附近但不太近
            log(f"  👀 {name} 在 ({mx},{my}) 距离 {dist}，警戒")
            return "aware"
        return "safe"

    # ── 进食 ──

    def auto_eat(self, hp_threshold=50, sta_threshold=15):
        """自动扫背包找吃的，不依赖外部参数。⚠️ 2026-09-06 按需求挑食：
        血低→挑回血(healthRecovered>0)的（奶酪/沙拉，绝不拿纯体力咖啡保命）；
        体力低→挑回体力(edibleValue>0)的；都低→回血优先。"""
        foods = self.detect_inventory_food()
        if not foods:
            return False

        s = self.state()
        p = s["player"]
        hp_pct = (p["health"] / p["maxHealth"] * 100) if p["maxHealth"] > 0 else 100
        sta_pct = (p["stamina"] / p["maxStamina"] * 100) if p["maxStamina"] > 0 else 100

        if sta_pct >= sta_threshold and hp_pct >= hp_threshold:
            return False

        # 先停下（边走边吃动画不生效），再吃 + 等 2 秒动画播完（bomb 同款逻辑）
        try:
            for _ in range(5):
                s = self.state()
                if not s.get("player", {}).get("isMoving", True):
                    break
                time.sleep(0.3)
        except Exception:
            pass

        # 挑食排序：血低→**回血量×10 主导**（山羊奶酪101 >> 咖啡1，别抓早出现的咖啡）；体力低→回体力加分
        def _rank(f):
            name, ed, hp = f
            score = 0
            if hp_pct < hp_threshold:
                score += hp * 10 - (10000 if hp <= 0 else 0)   # 血低：回血越多越优先；0回血重罚垫底
            if sta_pct < sta_threshold:
                score += ed                                    # 体力低：回体力越多越优先
            return score
        foods.sort(key=_rank, reverse=True)

        for fname, ed, hp in foods:
            try:
                self.select(fname)
                time.sleep(0.2)
                r = self._post("/eat")
                time.sleep(2.0)  # 等动画播完效果才生效
                if r.get("ok"):
                    log(f"  🍽️ 吃了 {fname}（体{ed} 血{hp}）")
                    return True
            except Exception:
                continue
        return False

    def eat_if_needed(self, food_sta, food_hp, hp_threshold, sta_threshold):
        """根据当前状态决定吃什么"""
        if not food_sta and not food_hp:
            return self.auto_eat(hp_threshold, sta_threshold)

        s = self.state()
        p = s["player"]
        hp = p["health"]
        max_hp = p["maxHealth"]
        sta = p["stamina"]
        max_sta = p["maxStamina"]

        hp_pct = (hp / max_hp * 100) if max_hp > 0 else 100
        sta_pct = (sta / max_sta * 100) if max_sta > 0 else 100

        # 体力食物和回血食物分开处理
        if sta_pct < sta_threshold and food_sta:
            log(f"  ⚡ 体力 {sta_pct:.0f}% < {sta_threshold}%，吃 {food_sta}")
            try:
                self.select(food_sta)
                time.sleep(0.2)
                r = self.use_item()
                time.sleep(0.5)
                if self._check_eat_result(r):
                    log(f"  ✅ 吃了 {food_sta}")
                    return True
            except Exception as e:
                log(f"  ⚠️ 吃 {food_sta} 失败: {e}")

        elif hp_pct < hp_threshold and food_hp:
            log(f"  ❤️ HP {hp_pct:.0f}% < {hp_threshold}%，吃 {food_hp}")
            try:
                self.select(food_hp)
                time.sleep(0.2)
                r = self.use_item()
                time.sleep(0.5)
                if self._check_eat_result(r):
                    log(f"  ✅ 吃了 {food_hp}")
                    return True
            except Exception as e:
                log(f"  ⚠️ 吃 {food_hp} 失败: {e}")

        # 如果只有一种食物，不管缺什么都吃它
        if (sta_pct < sta_threshold or hp_pct < hp_threshold) and (food_sta or food_hp):
            f = food_sta or food_hp
            if f:
                log(f"  🍽️ 状态不足，吃 {f}")
                try:
                    self.select(f)
                    time.sleep(0.2)
                    r = self.use_item()
                    time.sleep(0.5)
                    log(f"  ✅ 吃了 {f}")
                    return True
                except Exception as e:
                    log(f"  ⚠️ 吃 {f} 失败: {e}")

        return False

    def _check_eat_result(self, result):
        """检查 use_item 结果"""
        if isinstance(result, dict):
            error = result.get("error", "")
            if error and "nothing" in str(error).lower():
                return False
            if result.get("ok") is False:
                return False
        return True

    def is_safe(self, hp_threshold=30, sta_threshold=15):
        """基础安全检查：HP/体力/时间"""
        s = self.state()
        p = s["player"]
        hp = p["health"]
        max_hp = p["maxHealth"]
        sta = p["stamina"]
        max_sta = p["maxStamina"]
        tod = s.get("time", {}).get("timeOfDay", 600)

        hp_pct = (hp / max_hp * 100) if max_hp > 0 else 0
        sta_pct = (sta / max_sta * 100) if max_sta > 0 else 0

        if hp <= 0:
            log("  💀 玩家死亡！")
            return False
        if hp_pct < hp_threshold:
            log(f"  ❤️ HP {hp_pct:.0f}% < {hp_threshold}%")
            return False
        if sta_pct < sta_threshold:
            log(f"  ⚡ 体力 {sta_pct:.0f}% < {sta_threshold}%")
            return False
        if tod >= 2400:
            log(f"  ⏰ 已经凌晨 {tod//100}:{tod%100:02d}，强制回家")
            return False

        return True

    # ── 梯子 ──

    def take_ladder(self, lx, ly, lname, location):
        """走到梯子旁，下楼（直接传梯子上按 confirm）"""
        log(f"  🪜 走向 {lname} ({lx},{ly})...")

        # 矿洞内直接传梯子上，外面用 /walk_to
        if is_mine_location(location):
            self.mine_teleport(lx, ly)
        else:
            self.safe_walk_to(lx, ly, location, timeout=30)

        time.sleep(0.3)

        # 按 confirm（右键/动作键）下楼
        self._post("/key", {"key": "confirm", "count": 1})
        time.sleep(0.5)

        # 检查是否下楼成功
        for _ in range(10):
            s = self.state()
            new_loc = s.get("location", {}).get("name", "")
            new_level = extract_mine_level(new_loc)
            if new_level and new_level != self.mine_level:
                if new_level > self.mine_level:
                    log(f"  ✅ 下到第 {new_level} 层！（跳了 {new_level - self.mine_level} 层）")
                else:
                    log(f"  ✅ 到达第 {new_level} 层")
                self.mine_level = new_level
                self._rock_count = 0
                update_progress(new_level)
                return True
            time.sleep(0.3)

        # 没反应？尝试用方向键下楼
        log("  ⚠️ interact 没反应，试 confirm...")
        try:
            self._post("/key", {"key": "confirm", "count": 1})
            time.sleep(0.5)
        except Exception:
            pass

        for _ in range(5):
            s = self.state()
            new_loc = s.get("location", {}).get("name", "")
            new_level = extract_mine_level(new_loc)
            if new_level and new_level != self.mine_level:
                self.mine_level = new_level
                self._rock_count = 0
                update_progress(new_level)
                return True
            time.sleep(0.3)

        log("  ⚠️ 下梯子失败，用 warp 保底")
        return False

    # ── 战斗主循环（每敲完几块石头调用） ──

    def combat_check(self, location):
        """定时检查怪物 + 处理。⚠️ 2026-09-06 修：原来只砍一下就地回去挖，怪物继续磨血→
        现在靠近/贴脸怪就连续反击到清掉（最多 8 刀，防死循环），再回来挖。"""
        for _ in range(8):
            result = self.combat_step(location)
            if result != "fighting":
                if result == "safe":
                    time.sleep(0.2)
                return result
            time.sleep(0.35)
        return "fighting"

    def hunt_for_coal(self, location, radius=30, kill_timeout=20):
        """刷煤：大范围主动清怪，只打尘埃精灵和蝙蝠（掉煤），其他怪不打（危险+浪费时间）。
        模仿 bomb_common.combat_aggressive（主动追击+每2刀查血），farm 用 natural_walk 追击。"""
        HUNT_NAMES = ("Dust Spirit", "Bat")
        def _targets():
            ms = self.nearby_monsters(radius)
            return [m for m in ms if any(k in m[0] for k in HUNT_NAMES)]

        targets = _targets()
        if not targets:
            return False
        log(f"  🦇 刷煤：{targets[0][0]} 等 {len(targets)} 只")
        start = time.time()
        swings = 0
        while time.time() - start < kill_timeout:
            if swings % 2 == 0:  # 每2刀查血（省 HTTP）
                if not self.is_safe(hp_threshold=30, sta_threshold=15):
                    self.eat_if_needed(None, None, EAT_HP, 15)
                    if not self.is_safe(hp_threshold=30, sta_threshold=15):
                        log("  ❤️ 状态不足，撤出战斗")
                        return True
            targets = _targets()
            if not targets:
                return True  # 清完了
            name, mx, my, hp, dist = targets[0]
            if dist > 1:
                adj_x, adj_y, _, _ = self.find_adjacent_tile(mx, my)
                self.mine_teleport(adj_x, adj_y)
                time.sleep(0.15)
            self.face_toward(mx, my)
            if self.weapon_name:
                self.select(self.weapon_name)
                time.sleep(0.1)
                self.use_tool()
                time.sleep(0.35)
                self.select("Pickaxe")
            else:
                self.use_tool("Pickaxe")
                time.sleep(0.4)
            swings += 1
        return True

    def scan_floor_ores(self, node_name, loc, hp_threshold, sta_threshold):
        """扫层找目标矿：站落点 radius 30 扫一次，没矿直接返回（run_farm 出门重进，不抽抽）。
        挖所有矿节点（目标矿优先，宝石/铱/神秘顺手），顺带采集物（泪晶/地晶/火水晶/石英）。"""
        targets = []
        seen = set()
        for t in self.find_rocks(priority_ore=node_name, radius=30):
            name = t[2]
            if name == node_name or "Node" in name or "Geode" in name:
                key = (t[0], t[1])
                if key not in seen:
                    seen.add(key)
                    targets.append(t)
        self.collect_foragables(loc)
        return targets

    def collect_foragables(self, location):
        """顺手采集地面物（泪晶/地晶/火水晶/石英/洞穴萝卜等），position 到旁 + walk 捡（position 不触发拾取）。"""
        data = self.surroundings(30)
        px, py = data.get("center", {}).get("x", 0), data.get("center", {}).get("y", 0)
        items = []
        for t in data.get("tiles", []):
            o = t.get("object")
            if o and o in FORAGABLES:
                items.append((abs(t["x"] - px) + abs(t["y"] - py), t["x"], t["y"], o))
        items.sort()
        picked = 0
        for _, fx, fy, fname in items:
            if not self.is_safe(30, 15):
                break
            self.mine_teleport(fx, fy)
            time.sleep(0.2)
            self.safe_walk_to(fx, fy, location, timeout=15)
            time.sleep(0.3)
            picked += 1
            log(f"  🍄 采集 {fname}")
        return picked

    # ═══════════════════════════════════════════════════════════════
    #  模式 A：冲层
    # ═══════════════════════════════════════════════════════════════

    def run_rush(self, start_level, target_floor, food_sta, food_hp,
                 hp_threshold=50, sta_threshold=10, resume=True):
        """冲层模式：从 start_level 一路下到 target_floor

        resume=True 时，如果 start_level 是默认值1，则从进度记录的已到达最深恢复
        """
        # ── 动态起始层：读电梯当前可达最高层（替代静态进度，2026-08-22） ──
        if resume and start_level <= 1:
            auto_start = resume_start_level(self.port)
            if auto_start > 1:
                log(f"  🪜 当前就在第 {auto_start} 层 → 从该层继续（重置/入口=1）")
                start_level = auto_start
            else:
                start_level = 1
        else:
            start_level = max(1, start_level)
            # ⚠️ 2026-09-06 恒：AI 可显式设起始层（go_mining start=），但要 ≤ 电梯可达上限且 5 的倍数——
            #    否则打通 120 后"自动送到120层就没得玩"；给 AI 设 start=40→120 能自己挑层刷。
            elevator_max, _reset = self.mine_elevator_max()
            if start_level > 1:
                # 电梯只有 1(入口旁路) 和 5/10/15…的倍数，且**往下取**（88→85，不往上），<5 都落回 1（从入口爬）
                snapped = max(1, (start_level // 5) * 5)
                if snapped != start_level:
                    log(f"  ⚠️ 电梯只有第1层+5的倍数（往下取），{start_level} 折到 {snapped}")
                start_level = snapped
            if start_level > elevator_max:
                log(f"  ⚠️ 起始层 {start_level} 超电梯可达上限 {elevator_max}，钳到 {elevator_max}")
                start_level = elevator_max

        if start_level >= target_floor:
            log(f"  ⚠️ 起始层 {start_level} ≥ 目标层 {target_floor}，不挖了（设 start<target 或 target 更深）")
            return

        # 检查状态，体力太低先去睡觉
        s = self.state()
        sta = s["player"]["stamina"]
        max_sta = s["player"]["maxStamina"]
        sta_pct = (sta / max_sta * 100) if max_sta > 0 else 100
        if sta_pct < 30:
            log(f"  💤 体力只剩 {sta_pct:.0f}%，先去睡觉...")
            try:
                self.warp("Farm")
                time.sleep(2)
                self._post("/sleep")
                time.sleep(5)
                log(f"  ☀️ 新的一天！")
            except Exception as e:
                log(f"  ⚠️ 睡觉失败: {e}")

        log(f"\n🏃 === 冲层模式: {start_level} → {target_floor}层 ===")
        log(f"  镐子: {self.pickaxe_name} (Lv.{self.pickaxe_level})"
            f" | 食物: 体力={food_sta or '无'} 回血={food_hp or '无'}")

        # warp 到起始层（矿洞必须带坐标，不然被重定向）
        loc = f"UndergroundMine{start_level}"
        if not self.safe_warp(loc, x=5, y=5):
            # 试试矿井入口
            log("  warp 目标层失败，先进矿井入口")
            if not self.safe_warp("Mine"):
                log("  ❌ 无法进入矿井")
                return
            s = self.state()
            self.mine_level = extract_mine_level(s.get("location", {}).get("name", "")) or start_level
        else:
            self.mine_level = start_level

        total_rocks = 0
        levels_cleared = 0
        ladder_scan_counter = 0
        monster_scan_counter = 0
        pickup_counter = 0
        consecutive_empty = 0
        retreat_reason = None

        while self.mine_level < target_floor:
            level = self.mine_level
            loc_name = f"UndergroundMine{level}"
            log(f"\n--- ⬇️ 第 {level} 层 ---")

            # ── 安全检查 ──
            if not self.is_safe(hp_threshold, sta_threshold):
                if self.eat_if_needed(food_sta, food_hp, EAT_HP, sta_threshold):
                    if not self.is_safe(hp_threshold, sta_threshold):
                        log("  ❌ 吃了东西还是不行，撤退")
                        retreat_reason = "状态不足"
                        break
                else:
                    log("  ❌ 状态不足且没有食物，撤退")
                    retreat_reason = "状态不足"
                    break

            # ── 先扫一眼有没有现成梯子 ──
            ladder = self.detect_ladder(loc_name, brute=False)
            if ladder == "ALREADY_DOWN":
                consecutive_empty = 0
                continue
            elif ladder:
                # 下楼前顺手捡地面采集物（user 2026-08-10：别敲出梯子就冲下去漏了采集物）
                try:
                    self.pickup_foragables(loc_name)
                except Exception:
                    pass
                lx, ly, lname = ladder
                levels_cleared += 1
                ok = self.take_ladder(lx, ly, lname, loc_name)
                if ok:
                    consecutive_empty = 0
                    continue
                else:
                    # 梯子下不去，换 warp
                    log("  走梯子失败，warp 保底")
                    next_level = level + 1
                    if not self.safe_warp(f"UndergroundMine{next_level}", x=5, y=5):
                        log(f"  ❌ warp 到 {next_level} 也失败，撤退")
                        retreat_reason = "warp失败"
                        break
                    self.mine_level = next_level
                    self._rock_count = 0
                    update_progress(next_level)
                    consecutive_empty = 0
                    continue

            # ── 没梯子，开始敲石头 ──
            rocks_this_level = 0
            attempts = 0
            MAX_ATTEMPTS = 30
            result = None
            ladder = None

            while attempts < MAX_ATTEMPTS and self.mine_level == level:
                attempts += 1

                # 安全检查
                if not self.is_safe(hp_threshold, sta_threshold):
                    if self.eat_if_needed(food_sta, food_hp, EAT_HP, sta_threshold):
                        if not self.is_safe(hp_threshold, sta_threshold):
                            retreat_reason = "状态不足"
                            break
                    else:
                        retreat_reason = "状态不足"
                        break

                # 怪物检查（每 MONSTER_SCAN_INTERVAL 次检查）
                monster_scan_counter += 1
                if monster_scan_counter >= MONSTER_SCAN_INTERVAL:
                    monster_scan_counter = 0
                    self.combat_check(loc_name)

                # 找可敲的石头
                targets = self.find_rocks()
                if not targets:
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        log("  🔍 周围没东西了，直接扫梯子...")
                        ladder = self.detect_ladder(loc_name, brute=True)
                        if ladder:
                            # 下楼前顺手捡采集物（user 2026-08-10）
                            try:
                                self.pickup_foragables(loc_name)
                            except Exception:
                                pass
                            break  # 梯子找到了，跳出去下楼
                        # 真没了，走到中间再看看
                        log("  🚶 换个位置...")
                        self.safe_walk_to(15, 15, loc_name, timeout=15)
                        time.sleep(0.5)
                        continue
                    else:
                        time.sleep(0.3)
                        continue
                consecutive_empty = 0

                # 敲石头（一次最多 3 块）
                for tx, ty, name in targets[:3]:
                    if self.mine_level != level:
                        break  # 已经下楼了

                    if not self.is_safe(hp_threshold, sta_threshold):
                        break

                    result = self.mine_rock(tx, ty, name, loc_name)
                    if result == "dead":
                        retreat_reason = "死亡"
                        break
                    if result == "ok":
                        rocks_this_level += 1
                        total_rocks += 1

                    # 捡附近的地面物品
                    pickup_counter += 1
                    if pickup_counter >= PICKUP_SCAN_INTERVAL:
                        pickup_counter = 0
                        self.pickup_foragables(loc_name)

                    # 梯子检测（每 LADDER_SCAN_INTERVAL 块石头）
                    if self._rock_count % LADDER_SCAN_INTERVAL == 0:
                        ladder = self.detect_ladder(loc_name, brute=False)
                        if ladder:
                            break

                if result == "dead":
                    break
                if ladder:
                    break

            # ── 处理本层结束 ──
            if retreat_reason:
                break

            if result == "dead":
                retreat_reason = "死亡"
                break

            # 有梯子 → 下楼（下楼前顺手捡采集物，user 2026-08-10）
            if ladder == "ALREADY_DOWN":
                continue
            elif ladder:
                try:
                    self.pickup_foragables(loc_name)
                except Exception:
                    pass
                lx, ly, lname = ladder
                levels_cleared += 1
                ok = self.take_ladder(lx, ly, lname, loc_name)
                if ok:
                    continue

            # 没梯子或梯子走不通 → warp 下一层（下楼前顺手捡采集物）
            try:
                self.pickup_foragables(loc_name)
            except Exception:
                pass
            next_level = level + 1
            log(f"  🚀 warp 到第 {next_level} 层")
            if not self.safe_warp(f"UndergroundMine{next_level}", x=5, y=5):
                log(f"  ❌ warp 失败，撤退")
                retreat_reason = "warp失败"
                break
            self.mine_level = next_level
            self._rock_count = 0
            update_progress(next_level)
            levels_cleared += 1

        # ── 结束 ──
        reached = self.mine_level
        log(f"\n🏁 === 冲层结束 ===")
        log(f"  起点: {start_level} → 终点: {reached}")
        log(f"  通过: {levels_cleared} 层 | 敲碎: {total_rocks} 块矿石")
        if retreat_reason:
            log(f"  撤退原因: {retreat_reason}")
        s = self.state()
        p = s["player"]
        log(f"  剩余 ❤️ {p['health']}/{p['maxHealth']}  ⚡ {p['stamina']:.0f}/{p['maxStamina']}")
        log(f"  回矿井口！")
        try:
            self.warp("Mountain", x=54, y=5)
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════════
    #  模式 B：刷矿
    # ═══════════════════════════════════════════════════════════════

    def run_farm(self, ore_type, cycles, food_sta, food_hp,
                 hp_threshold=50, sta_threshold=20):
        """刷矿模式：在指定层反复刷特定矿石"""
        # 矿石 ↔ 层数映射
        ORE_FLOORS = {
            "Copper": 21,    # 铜主要在1-39层
            "Iron": 41,      # 铁主要在41-79层
            "Gold": 71,      # 金主要在81+层
        }
        floor = ORE_FLOORS.get(ore_type, 21)
        node_name = f"{ore_type} Node"

        # 🪜 2026-08-22 动态可达性门控：在鹈鹕镇矿井则确认电梯能到目标矿层，重置/不可达则上报交 AI/user 处理
        try:
            cur_loc = (self.state().get("location") or {}).get("name", "")
        except Exception:
            cur_loc = ""
        if _is_town_mine(cur_loc):
            elevator_max, _reset = self.mine_elevator_max()
            if elevator_max < floor:
                log(f"  ⚠️ 电梯无法直达目标矿层：刷 {ore_type} 需第 {floor} 层，当前电梯只能到 {elevator_max} 层")
                log("     （接「深处的危险」后电梯被重置，需先冲层把电梯层带回，或改刷浅层矿如 Copper 21）")
                return

        log(f"\n⛏️ === 刷矿模式: {ore_type} ===")
        log(f"  目标层: {floor} | 循环 {cycles} 次"
            f" | 镐子: {self.pickaxe_name} (Lv.{self.pickaxe_level})"
            f" | 食物: {food_sta or '无'} / {food_hp or '无'}")

        total_rocks = 0
        total_pickups = 0

        for cycle in range(cycles):
            log(f"\n--- 🔄 第 {cycle+1}/{cycles} 轮 ---")

            # 直接进层（不再每次先固定瞬移出门；层缓存刷新靠"没矿重进"兜底）
            loc = f"UndergroundMine{floor}"
            if not self.safe_warp(loc):
                log(f"  ❌ warp 到 {floor} 层失败")
                continue

            self.mine_level = floor
            self._rock_count = 0
            time.sleep(1.0)

            # 被动回击：贴脸怪还手（任何怪）
            self.combat_check(loc)
            # 主动刷煤仅限 41 层（铁层，蝙蝠/尘埃精灵多）；21/71 不主动追怪
            if floor == 41:
                self.hunt_for_coal(loc)

            # 扫全层找目标矿；没矿就出门重进刷新（层随机，最多重试 3 次）
            round_rocks = 0
            targets = self.scan_floor_ores(node_name, loc, hp_threshold, sta_threshold)
            retry = 0
            while not targets and retry < 3:
                retry += 1
                log(f"  🔁 本层无 {ore_type}，出门重进刷新 ({retry}/3)")
                self.warp("Mountain", x=54, y=5)
                time.sleep(1.2)
                if not self.safe_warp(loc):
                    break
                time.sleep(1.0)
                self.combat_check(loc)
                if floor == 41:
                    self.hunt_for_coal(loc)
                targets = self.scan_floor_ores(node_name, loc, hp_threshold, sta_threshold)

            if not targets:
                log("  多次重进仍无目标矿石")
            else:
                log(f"  🪨 找到 {len(targets)} 个目标矿")
                for tx, ty, name in targets:
                    # 敲矿前确保状态安全：回血吃到安全线再敲
                    for _ in range(4):
                        if self.is_safe(hp_threshold, sta_threshold):
                            break
                        if not self.eat_if_needed(food_sta, food_hp, EAT_HP, sta_threshold):
                            break
                    if not self.is_safe(hp_threshold, sta_threshold):
                        log("  ❤️ 状态不足，放弃敲本层矿")
                        break
                    if name == "Iridium Node":
                        continue  # 不敲铱矿
                    result = self.mine_rock(tx, ty, name, loc, teleport=True)
                    if result == "ok":
                        round_rocks += 1
                        total_rocks += 1
                    time.sleep(0.1)

            # 每层最后都刷煤：有矿敲完刷，没矿也刷（蘑菇怪/蝙蝠=煤，不空手走）
            self.hunt_for_coal(loc)

            # 捡漏
            picked = self.pickup_foragables(loc)
            total_pickups += picked

            log(f"  第 {cycle+1} 轮: 敲了 {round_rocks} 块矿石, 捡了 {picked} 个物品")

        # 结束
        log(f"\n🏁 === 刷矿结束 ===")
        log(f"  目标: {ore_type} | 循环: {cycles} 轮")
        log(f"  共敲: {total_rocks} 块 | 共捡: {total_pickups} 个")
        s = self.state()
        p = s["player"]
        log(f"  剩余 {p['health']}/{p['maxHealth']}  {p['stamina']:.0f}/{p['maxStamina']}")
        log(f"  回矿井口！")
        try:
            self.warp("Mountain", x=54, y=5)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════════

def main():
    # Windows GBK 兼容：强制 stdout 用 UTF-8，不然 emoji 打不了
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="[mine] v2 - Penguins Town Mine Dual Mode")
    parser.add_argument("--mode", choices=["rush", "farm"], default="rush",
                        help="冲层 / 刷矿（默认 rush）")
    parser.add_argument("--start", type=int, default=1,
                        help="起始层数（仅 rush 模式，默认 1）")
    parser.add_argument("--target", type=int, default=40,
                        help="目标层数（rush 模式，默认 40）")
    parser.add_argument("--ore", type=str, default="Iron",
                        help="目标矿石（farm 模式，默认 Iron）")
    parser.add_argument("--cycles", type=int, default=5,
                        help="刷矿循环次数（farm 模式，默认 5）")
    parser.add_argument("--hp-threshold", type=int, default=30,
                        help="血量低于此百分比时撤退（默认 30）——吃食物阈值固定 60%")
    parser.add_argument("--sta-threshold", type=int, default=10,
                        help="体力低于此百分比时吃食物（默认 20）")
    parser.add_argument("--food-sta", type=str, default=None,
                        help="体力食物名称（如 Salad）")
    parser.add_argument("--food-hp", type=str, default=None,
                        help="回血食物名称（如 Cheese）")
    parser.add_argument("--port", type=int, default=7842,
                        help="NagiBridge 端口（默认 7842）")
    parser.add_argument("--resume", action="store_true", default=True,
                        help="rush 模式从进度恢复（默认开启）")
    parser.add_argument("--no-resume", action="store_false", dest="resume",
                        help="rush 模式不从进度恢复，从 --start 开始")
    parser.add_argument("--check-progress", action="store_true",
                        help="查看已到达的最深层数，不挖矿")
    parser.add_argument("--reset-progress", action="store_true",
                        help="重置进度文件")
    args = parser.parse_args()

    # ── 进度查询/重置 ──
    if args.check_progress:
        start = resume_start_level(args.port)
        log(f"🪜 电梯当前可达：第 {start} 层（动态起始层；在鹈鹕镇矿井外/未就绪则报 1）")
        log(f"    进度文件记录：{load_progress()} 层（仅供参考，已不用于起步）")
        return

    if args.reset_progress:
        save_progress(0)
        log("🗑️ 进度已重置")
        return

    # 验证参数
    if args.mode == "rush" and args.target > 120:
        log("⚠️ 鹈鹕镇矿井最高 120 层，目标设为 120")
        args.target = 120
    if args.mode == "rush" and args.start > 120:
        log("⚠️ 起始层超过 120 了，设为 1")
        args.start = 1

    # 检查游戏状态
    s = _get("/status")
    if not s.get("worldReady"):
        log("❌ 游戏未就绪，请先加载存档")
        return

    # 创建 bot
    bot = MineBot(args.port)

    # ⚠️ 2026-08-16 预检：背包/血量告知原因（两硬两黄）
    block = bot.preflight(args.hp_threshold)
    if block:
        log(block)
        return

    # 检测镐子和武器
    bot.detect_pickaxe()
    bot.detect_weapon()

    # 选镐子
    bot.select("Pickaxe")
    time.sleep(0.2)

    # 选食物（提前选中，方便快速吃）
    if args.food_sta:
        try:
            bot.select(args.food_sta)
            time.sleep(0.2)
            bot.select("Pickaxe")
        except Exception:
            pass

    # 启动模式
    if args.mode == "rush":
        bot.run_rush(
            start_level=args.start,
            target_floor=args.target,
            food_sta=args.food_sta,
            food_hp=args.food_hp,
            hp_threshold=args.hp_threshold,
            sta_threshold=args.sta_threshold,
            resume=args.resume,
        )
    else:
        bot.run_farm(
            ore_type=args.ore,
            cycles=args.cycles,
            food_sta=args.food_sta,
            food_hp=args.food_hp,
            hp_threshold=args.hp_threshold,
            sta_threshold=args.sta_threshold,
        )


if __name__ == "__main__":
    main()
