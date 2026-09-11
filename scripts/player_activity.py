"""
Player Activity Detection
=========================
检测玩家当前行为，生成有温度的中文描述。
只读 /state 数据，不额外调 API。

判定优先级（高 → 低）：
  0. 👥 另一位玩家在附近（同场景 + 距离近）——覆盖一切
  1. 🆘 紧急状况（低血量逃跑 / 濒危）
  1.5 📋 窗口/菜单打开（整理背包/箱子、搭话、逛商店）
  2. ⛏️ 矿井探索/战斗（鹈鹕镇矿井/火山/头骨矿洞）+ 沙漠战斗
  3. 🌿 清杂草/收割（物品数量近期增长 → 实际发生的事）
  4. 🎣 钓鱼 / 工具体力消耗（砍树/敲石头/耕地/浇水）
  4.5 💭 静止发呆（N 秒没动 + 没开窗口，20s）
  5. 🛍️ 室内商店 / 🐄 动物建筑 / 🌱 温室
  6. 🏝️ 姜岛 / 🌾 农场 / ♨️ 浴场 / 🏖️ 海滩 / 🏜️ 沙漠
  7. 🚶 赶路 / 💬 社交 / 🎉 节日
  8. 💭 发呆 / 🚶 歇脚 / 😮‍💨 休息

心跳视角（2026-08-02 修正）：
  心跳是给 AI 看"用户(房主)"在干嘛的。入口 detect_activity：
    1. detect_player_nearby() —— 用户是否在 AI 附近（AI 进程的 otherPlayers），在则"👥 user 正与你同在"
    2. describe_activity()   —— 描述"用户(房主)"的状态（nagi_mcp_server 从 host 进程取状态喂进来）
  描述的主语是**用户**，不是 AI 自己。

心跳机制：
  每隔 N 分钟在状态条注入一行 👤 玩家动态。
  用 set_heartbeat_interval(分钟) 配置。

物品追踪（跨次调用）：
  _item_history = {name: (stack, timestamp)}
  比对上/下两次调用间的 stack 变化 + 时效窗口，
  推断玩家在干嘛：纤维多了 → 在清杂草。

体力追踪：
  _last_stamina 记录上次体力值。
  比对上/下两次调用间的体力变化 + 工具，
  推断玩家在干嘛：斧头+体力掉了 → 在砍树（树要砍多下才爆，靠物品来不及）。
"""

import time
import random
import re

# ═══════════════════════════════════════════════
#  物品历史追踪（基于结果推断行为）
# ═══════════════════════════════════════════════

_item_history = {}  # {item_name: (stack_count, timestamp)}
_ITEM_RECENCY_WINDOW = 90  # 秒内增幅才算"正在做"

# ── 体力追踪（跨次调用，判断正在用啥工具干活） ──
_last_stamina: float | None = None


def _stamina_dropped(current_stamina: float) -> bool:
    """检测体力是否比上次记录时下降了。

    原理：星露谷里体力下降只因为挥了工具/武器/鱼竿。
    拿着斧头 + 体力掉了 → 在砍树（不需要等 Wood 涨）。
    拿着剑 + 体力掉了 → 在战斗。
    返回 True 后会自动更新记录，避免重复触发。
    """
    global _last_stamina
    if _last_stamina is None:
        _last_stamina = current_stamina
        return False
    dropped = current_stamina < _last_stamina
    _last_stamina = current_stamina
    return dropped


def _update_item(inventory: list, item_name: str):
    """将某个物品的当前数量写入历史（只写，不做判断）。"""
    global _item_history
    for item in inventory:
        if item.get("name") == item_name:
            _item_history[item_name] = (item.get("stack", 0), time.time())
            return


def _item_just_increased(inventory: list, item_name: str) -> bool:
    """检测某个物品的数量是否在近期增加了。

    比较当前值与上次记录的 stack 数。
    如果增加了且在 ITEM_RECENCY_WINDOW 秒内 → True
    否则（含首次见到）→ False

    副作用：每次调用都会把当前值写回历史（用于下一次对比）。
    """
    global _item_history

    current = 0
    for item in inventory:
        if item.get("name") == item_name:
            current = item.get("stack", 0)
            break

    now = time.time()
    prev_stack, prev_time = _item_history.get(item_name, (0, 0))

    # 写回当前值（供下次比较）
    _item_history[item_name] = (current, now)

    if prev_stack == 0:
        return False  # 首次看到，没法判断

    increased = current > prev_stack
    is_recent = (now - prev_time) <= _ITEM_RECENCY_WINDOW

    return increased and is_recent


def _snapshot_items(inventory: list):
    """批量记录所有物品的当前数量（初始化 / 兜底对齐用）。"""
    global _item_history
    now = time.time()
    for item in inventory:
        name = item.get("name")
        if name:
            _item_history[name] = (item.get("stack", 0), now)


# ═══════════════════════════════════════════════
#  心跳配置
# ═══════════════════════════════════════════════

_heartbeat_interval = 5  # 默认 5 分钟
_last_heartbeat = 0


# ── 商店/建筑室内名称映射 ──
SHOP_NAMES = {
    "seedshop": "皮埃尔杂货店",
    "saloon": "星之果实餐吧",
    "blacksmith": "铁匠铺",
    "fishop": "鱼店",
    "animalshop": "玛妮牧场",
    "sciencehouse": "木匠店(Robin)",
    "sandyhouse": "桑迪商店",
    "hospital": "哈维诊所",
    "archaeologyhouse": "博物馆",
    "manorhouse": "镇长家",
    "joshouse": "Alex家",
    "sebastianroom": "Sebastian房间",
    "wizardhouse": "法师塔",
    "tent": "莱纳斯帐篷",
    # 注意：bathhouse 不在商店列表——它是泡澡的地方，不该报"在浴场里走来走去"（泡澡会到处游）
    "club": "赌场",
    "sewer": "下水道",
    "bugland": "变异虫穴",
    "communitycenter": "社区中心",
    "movie theater": "电影院",
    "greenhouse": "温室",
    "farmcave": "农场洞穴",
    "cabin": "联机小屋",
    "boatunnel": "姜岛船坞",
}

# ── 矿井识别（含地点定语：鹈鹕镇矿井 / 火山 / 头骨矿洞） ──
def _mine_display_name(loc_lower: str) -> str | None:
    """根据位置名判断是哪个矿井，返回显示名或 None（不是矿井）。

    注意（2026-08-02 实测）：用户的游戏里"头骨矿洞"的位置名是
    `UndergroundMine121+`（mod 改了命名）——标准矿井最多 120 层，
    所以 `UndergroundMine` 超过 120 层就当"头骨矿洞"。
    """
    if "undergroundmine" in loc_lower:
        m = re.search(r"undergroundmine(\d+)", loc_lower)
        level = int(m.group(1)) if m else 0
        return "头骨矿洞" if level > 120 else "鹈鹕镇矿井"
    if "skullcave" in loc_lower:
        return "头骨矿洞"
    if "volcanodungeon" in loc_lower:
        return "火山"
    return None


# ── 常用地图中文名（骑马/赶路等场景用） ──
LOCATION_LABELS = {
    "farm": "农场", "farmhouse": "小屋", "town": "镇上", "beach": "海滩",
    "mountain": "深山", "forest": "森林", "busstop": "巴士站", "desert": "沙漠",
    "railroad": "铁路", "backwoods": "边远森林", "sewers": "下水道",
    "greenhouse": "温室", "shed": "小屋", "barn": "畜棚", "coop": "鸡舍",
}
# ⚠️ 没有 "cabin" 条目是**故意的**：`_loc_label` 先查 SHOP_NAMES，而那里已有
#    "cabin": "联机小屋"（先于本表命中）⇒ 在这加是死代码。查不到中文名的地点由
#    🪑 坐着彩蛋的"泛称兜底"处理（见 describe_activity 第 2.5 级）。

# ⛪ 由巴教堂（恒 2026-09-11 带图圈的）：**皮埃尔商店(SeedShop)里祭坛正下方那间**，
#    3 格宽 × 5 格高 = 15 格。祭坛是 Buildings 层 (36,17)(37,17)(38,17) 的 `Action: Yoba`；
#    其下 5 行：y=18 站位 / y=19 坐垫 / y=20 站位 / y=21 坐垫 / y=22 站位（坐垫=mapSeat stool）。
#    触发：人在这 15 格内 + **面朝上(0)**（站或坐都算；坐着时朝向就是坐姿朝向）。
YOBA_CHAPEL = {"location": "seedshop", "xs": (36, 37, 38), "ys": (18, 19, 20, 21, 22)}


def _loc_label(loc_lower: str, loc_name: str) -> str:
    """位置的中文名（矿井/商店/常用地图），查不到就用原始名。"""
    mine = _mine_display_name(loc_lower)
    if mine:
        return mine
    for key, d in SHOP_NAMES.items():
        if key in loc_lower:
            return d
    # ⚠️ 取**最长**匹配 key：按 dict 顺序匹配时 "farm" 会先于 "farmhouse" 命中，
    #    把 FarmHouse 叫成"农场"（🪑 坐着彩蛋真机当场暴露："乖巧地坐在农场里"，人在屋里）。
    #    最长匹配才够具体。
    best_key, best_label = "", None
    for key, d in LOCATION_LABELS.items():
        if key in loc_lower and len(key) > len(best_key):
            best_key, best_label = key, d
    if best_label:
        return best_label
    return loc_name


def _is_riding(state_data: dict) -> bool:
    """是否在骑马：优先用 /state 的 `riding` 字段（新DLL），
    老DLL没有该字段 → 用"马NPC 与玩家同格"兜底判断。
    下马后马停在旁边（差≥1格）不算骑。"""
    p = state_data.get("player", {})
    if p.get("riding"):
        return True
    try:
        px, py = p.get("x"), p.get("y")
        for n in (state_data.get("raw", {}).get("npcs") or []):
            nm = (n.get("name") or "")
            if "马" in nm or "horse" in nm.lower() or "mount" in nm.lower():
                if n.get("x") == px and n.get("y") == py:
                    return True
    except Exception:
        pass
    return False

# ── 静止发呆阈值（秒）：人物 N 秒没动 + 没开别的窗口 → 发呆 ──
IDLE_STATIONARY_SECONDS = 20

# ── 动物建筑 ──
ANIMAL_BUILDINGS = {"barn", "coop", "deluxe barn", "deluxe coop", "big barn", "big coop"}

# ── 特殊时机 ──
FESTIVAL_LOCATIONS = {
    "town": "节日广场",
    "beach": "海滩",
    "forest": "森林",
}

# ── 玩家附近检测 ──
# 同场景下，曼哈顿距离 ≤ 该值 ≈ 能听到声音/四分之一个农场的范围
PLAYER_NEARBY_RANGE = 20


# ═══════════════════════════════════════════════
#  心跳控制
# ═══════════════════════════════════════════════

def set_interval(minutes: int) -> str:
    """设置玩家动态检测的心跳间隔（分钟）。0=每次工具调用都显示。"""
    global _heartbeat_interval
    _heartbeat_interval = max(0, minutes)
    if minutes == 0:
        return "✅ 玩家动态: 每次操作都显示"
    return f"✅ 玩家动态: 每 {minutes} 分钟注入一次"


def get_interval() -> int:
    """当前心跳间隔（分钟）。0=每次都显示。"""
    return _heartbeat_interval


def should_inject() -> bool:
    """判断本轮是否应该注入玩家动态。"""
    global _last_heartbeat
    if _heartbeat_interval <= 0:
        return True  # 0 = 每次都显示
    now = time.time()
    if now - _last_heartbeat >= _heartbeat_interval * 60:
        _last_heartbeat = now
        return True
    return False


def reset_heartbeat():
    """重置心跳计时（比如 AI 主动问了玩家在干嘛）。"""
    global _last_heartbeat
    _last_heartbeat = time.time()


# ═══════════════════════════════════════════════
#  行为检测
# ═══════════════════════════════════════════════

def _get_player_name(state_data: dict) -> str:
    """获取玩家名字。"""
    p = state_data.get("player", {})
    return p.get("name", "TA")


def _check_poi_nearby(loc_name: str, px: int, py: int) -> list:
    """在 locations.POI 中查附近有没有已知兴趣点。"""
    try:
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
        from locations import POI
        nearby = []
        for name, info in POI.items():
            if info.get("map", "") == loc_name:
                ix, iy = info.get("pos", (0, 0))
                dist = abs(px - ix) + abs(py - iy)
                if dist <= 3:
                    nearby.append(name)
        return nearby
    except Exception:
        return []


def _describe_menu(active_menu: dict, name: str, loc_lower: str, is_moving: bool) -> str | None:
    """根据打开的菜单类型推断玩家在干嘛（"开了窗口"分支）。

    返回 None 表示不拦截（让后续判定继续，比如要带商店名的购物）。
    """
    mtype = (active_menu.get("type") or "").lower()
    if not mtype:
        return None
    if "dialoguebox" in mtype:
        speaker = active_menu.get("speaker")
        if speaker:
            return f"💬 **{name}** 好像在和 **{speaker}** 搭话"
        return f"💬 **{name}** 好像在跟人搭话"
    if "itemgrabmenu" in mtype:
        return f"📦 **{name}** 正在整理箱子"
    if "gamemenu" in mtype:
        sub = (active_menu.get("submenu") or "").lower()
        if "crafting" in sub:
            return f"🔨 **{name}** 正在捣鼓合成台"
        if "social" in sub:
            return f"👥 **{name}** 正在查看社交"
        return f"🎒 **{name}** 正在整理背包"
    if "crafting" in mtype:
        return f"🔨 **{name}** 正在捣鼓合成台"
    if "shopmenu" in mtype:
        # 已知商店：直接带店名（更具体）
        display = "商店"
        for key, d in SHOP_NAMES.items():
            if key in loc_lower:
                display = d
                break
        if is_moving:
            return f"🛍️ **{name}** 正在{display}里走来走去"
        return f"🛍️ **{name}** 正在{display}里精挑细选"
    if "animalquerymenu" in mtype:
        return f"🐄 **{name}** 正在查看动物的状况"
    if "shippingmenu" in mtype:
        return f"💰 **{name}** 正在结算今日的收获"
    if "readych" in mtype:
        return f"💤 **{name}** 准备睡觉了"
    if "forgemenu" in mtype:
        return f"🔨 **{name}** 正在锻造武器"
    if "questlog" in mtype:
        return f"📜 **{name}** 正在翻看任务"
    return f"📋 **{name}** 正忙着处理一个菜单"


def nearby_player_name(state_data: dict) -> str | None:
    """返回"在附近的其他玩家"名字（用户/房主），没有返回 None。

    视角是"被控制角色"（AI/轮回）——otherPlayers 里同场景 + 距离 ≤ PLAYER_NEARBY_RANGE。
    心跳用它判断用户是否在身边：附近+移动 → "和你在一起做某事"；附近+窗口/静止 → 活动优先。
    """
    try:
        my_loc = state_data.get("location", {}).get("name", "")
        myp = state_data.get("player", {})
        my_name = myp.get("name", "")
        mx, my = myp.get("x", 0), myp.get("y", 0)
        for op in (state_data.get("otherPlayers") or []):
            # 跳过自己（otherPlayers 可能把本机角色也算进去）
            if op.get("name", "") == my_name:
                continue
            if op.get("location", "") == my_loc:
                dist = abs(op.get("x", 9999) - mx) + abs(op.get("y", 9999) - my)
                if dist <= PLAYER_NEARBY_RANGE:
                    return op.get("name", "TA")
    except Exception:
        pass
    return None


def detect_player_nearby(state_data: dict) -> str | None:
    """附近提示文案（兼容旧调用）：有玩家在附近返回"👥 user 正与你同在。"，没有返回 None。"""
    oname = nearby_player_name(state_data)
    if not oname:
        return None
    return random.choice([
        f"👥 **{oname}** 正与你同在。",
        f"👥 **{oname}** 跟你形影不离。",
        f"👥 **{oname}** 和你在一起。",
    ])


def describe_activity(state_data: dict) -> str:
    """检测玩家当前行为，返回中文描述。

    心跳里传给它的通常是"用户(房主/user)"的状态 → 描述玩家在干嘛给 AI 看。
    判定优先级（同级内第一个匹配的返回）：
      [紧急]  →  [实际发生了啥]  →  [正在用啥工具]  →  [在哪]  →  [兜底]
    """
    p = state_data.get("player", {})
    loc_name = state_data.get("location", {}).get("name", "")
    t = state_data.get("time", {})
    inv = state_data.get("inventory", [])
    alerts = state_data.get("alerts", [])

    name = _get_player_name(state_data)
    px, py = p.get("x", 0), p.get("y", 0)
    tool = p.get("currentTool", "") or ""
    is_moving = p.get("isMoving", False)
    health = p.get("health", 1)
    max_health = p.get("maxHealth", 1)
    stamina = p.get("stamina", 1)
    max_stamina = p.get("maxStamina", 1)
    tod = t.get("timeOfDay", 600)
    loc_lower = loc_name.lower()
    is_fishing = p.get("fishing") is not None or "FishingRod" in tool

    hp_ratio = health / max(1, max_health)
    stam_ratio = stamina / max(1, max_stamina)

    # 菜单/窗口状态（/state 的 activeMenu）
    active_menu = state_data.get("activeMenu") or {}
    menu_open = bool(active_menu.get("type"))
    # 静止秒数（ModEntry 每帧按 TilePoint 未变统计）
    stationary_seconds = p.get("stationarySeconds", 0) or 0

    # 武器 & 体力下降（提前算好，矿井/沙漠战斗 + 工具层共用）
    sw = any(w in tool for w in ["Weapon", "Sword", "Dagger", "Club", "Slingshot"])
    stamina_down = _stamina_dropped(stamina)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 1 级：紧急状况（最高优先级，不判断工具/位置）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    if hp_ratio < 0.25 and is_moving:
        return f"⚔️ **{name}** 正在踉跄撤退，血量见底……似乎需要帮助！"
    if hp_ratio < 0.25:
        return f"🆘 **{name}** 情况危急，只剩{int(hp_ratio*100)}%血量！"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 1.5 级：窗口/菜单打开（不是发呆，是在开界面做事）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    if menu_open:
        msg = _describe_menu(active_menu, name, loc_lower, is_moving)
        if msg:
            return msg

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 2 级：矿井 → 探索/战斗提示（矿井里优先）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    _mine_name = _mine_display_name(loc_lower)
    if _mine_name:
        if hp_ratio < 0.5:
            return f"⚔️ **{name}** 正在{_mine_name}里探索，已经受了些伤"
        if sw and stamina_down:
            return f"⚔️ **{name}** 正在{_mine_name}里战斗"
        return f"⛏️ **{name}** 正在{_mine_name}中探索"

    # 沙漠（战斗/探索，带地点定语）
    if "desert" in loc_lower:
        if sw and stamina_down:
            return f"⚔️ **{name}** 正在沙漠中战斗"
        if hp_ratio < 0.5:
            return f"⚔️ **{name}** 正在沙漠中探索，已经受了些伤"

    # 骑马（/state riding 字段，或老DLL用"马NPC与玩家同格"兜底）
    if _is_riding(state_data):
        return f"🐴 **{name}** 正骑马在{_loc_label(loc_lower, loc_name)}飞驰"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 2.5 级：彩蛋（由巴教堂祷告 / 温泉泡澡 / 坐着歇脚）
    #    恒 2026-09-11：今天做的坐椅子 + 浴室泡澡"都可以记进心跳作为彩蛋"。
    #    数据：教堂=位置+朝向（/state 自带）；sitting/swimming 由 _gather_user_state 从
    #    host 端口的 /sittable、/pool 取（只在心跳注入时读，默认 5 分钟一次，开销可忽略）。
    #    优先级：教堂 > 泡澡 > 坐着（恒："在教堂坐着会顶掉上一条 sitting 的判断"）。
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # ⛪ 由巴教堂：SeedShop 祭坛下那 15 格，站或坐 + 面朝上 ⇒ 祷告（**顶掉下面的 sitting 判断**）
    if (loc_lower == YOBA_CHAPEL["location"] and px in YOBA_CHAPEL["xs"]
            and py in YOBA_CHAPEL["ys"] and p.get("facingDirection") == 0 and not is_moving):
        return random.choice([
            f"🙏 **{name}** 在教堂对由巴虔诚地祷告。",
            f"🕯️ **{name}** 正在由巴教堂聆听神谕与火苗声。",
        ])

    # ♨️ 泡澡（/pool 的 swimming；泳池"游没游泳"的唯一权威是它，跟水格无关）
    if state_data.get("swimming"):
        return random.choice([
            f"♨️ **{name}** 正在温泉享受泡澡时光。",
            f"♨️ **{name}** 正在温泉静养。",
            f"🛁 **{name}** 在浴室玩水。暖烘烘！",
        ])

    # 🪑 坐着（/sittable 的 me.sitting；座位名 2026-09-11 加）
    if state_data.get("sitting"):
        _where = _loc_label(loc_lower, loc_name)
        _generic = not any("一" <= c <= "鿿" for c in _where)
        if _generic:
            _where = "这里"           # 查不到中文名（小屋实例名等）→ 泛称，别把英文塞进中文句子
        _where_in = _where if _generic else f"{_where}里"   # "这里"+"里" 会变"这里里"
        # 🪑 点名坐的是什么——**只认家具**（`me.seatName` 两类性质不同，见 stardew_api.host_sittable）：
        #    家具 = 本地化 DisplayName（"红色餐椅"）⇒ 能进中文句子；
        #    地图座椅 = 内部英文 token（"bench"/"stool"）⇒ 硬塞会出"正坐在 bench 上"，
        #    而游戏里**没有**这些 seatType 的本地化名可查（MapSeat 压根没这字段）
        #    ⇒ 拿不到中文名就不编，退回泛称（延续"宁报错别兜底"）。取不到 seat（老 DLL）同样退泛称。
        _seat = state_data.get("seat") or {}
        _sname = (_seat.get("name") or "").strip()
        if _seat.get("kind") == "furniture" and _sname and _sname != "?":
            return random.choice([
                f"🪑 **{name}** 正坐在**{_sname}**上，安静感受时光在星露谷淌过的痕迹。",
                f"🪑 **{name}** 窝在{_where}的**{_sname}**里，惬意得很。",
                f"🪑 **{name}** 坐在{_where}的**{_sname}**上歇脚。",
            ])
        return random.choice([
            f"🪑 **{name}** 正在{_where}歇脚。",
            f"🪑 **{name}** 乖巧地坐在{_where_in}。",
            f"🪑 **{name}** 坐在{_where}，安静感受时光在星露谷淌过的痕迹。",
        ])

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 3 级：非矿井 → 杂草/播种检测
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # 先拍照种子类物品的旧值（_item_just_increased 会覆盖历史，提前保存）
    seed_prev = {}  # {item_name: prev_stack}
    for item in inv:
        if item.get("category") == "种子":
            iname = item.get("name")
            if iname:
                seed_prev[iname], _ = _item_history.get(iname, (0, 0))

    # ⚠️ 必须全部 eval（_item_just_increased 有副作用：更新历史）
    weed_items = ["Fiber", "Mixed Seeds", "Moss"]
    weed_results = [_item_just_increased(inv, item) for item in weed_items]

    if any(weed_results):
        return f"🌿 **{name}** 正在清理杂草"

    # 播种检测：种子类物品数量减少（用提前保存的旧值比）
    for item in inv:
        if item.get("category") == "种子":
            iname = item.get("name")
            if not iname:
                continue
            current = item.get("stack", 0)
            prev = seed_prev.get(iname, 0)
            if prev > 0 and current < prev:
                _item_history[iname] = (current, time.time())
                return f"🌱 **{name}** 正在播种"

    _snapshot_items(inv)

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 4 级：工具 + 体力消耗
    #  树/石头要打多下，等物品涨来不及
    #  体力掉了 = 正在挥工具
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    if "FishingRod" in tool or is_fishing:
        return f"🎣 **{name}** 正在钓鱼"

    # 武器：非矿井/沙漠不报战斗，体力掉了就是砍草/乱挥
    if sw and stamina_down:
        return f"🌿 **{name}** 正在清理杂草"

    if "Axe" in tool:
        if stamina_down:
            return random.choice([
                f"🪓 **{name}** 正在砍树",
                f"🪓 **{name}** 正在收集木材",
            ])
        # fall through

    if "Pickaxe" in tool:
        if stamina_down:
            return random.choice([
                f"⛏️ **{name}** 正在敲石头",
                f"⛏️ **{name}** 正在凿矿",
            ])
        # fall through

    if "Hoe" in tool:
        if stamina_down:
            return random.choice([
                f"🌾 **{name}** 正在耕地",
                f"🌾 **{name}** 正在翻土",
            ])
        # fall through

    if "Watering Can" in tool:
        if stamina_down:
            return random.choice([
                f"💧 **{name}** 正在浇水",
                f"💧 **{name}** 正在灌溉",
            ])
        # fall through

    if "Scythe" in tool or "Sickle" in tool:
        if stamina_down:
            return random.choice([
                f"🌿 **{name}** 正在挥舞镰刀",
                f"🌿 **{name}** 正在收割",
            ])
        # fall through

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 4.5 级：静止发呆（N 秒没动 + 没开窗口 → 发呆）
    #  比"在哪/闲逛"类提示更诚实；保留赖床/泡澡等风味
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    if not is_moving and not menu_open and stationary_seconds >= IDLE_STATIONARY_SECONDS:
        if "farmhouse" in loc_lower and (tod < 600 or tod >= 2400):
            return f"💤 **{name}** 还在赖床"
        if "bathhouse" in loc_lower:
            return f"♨️ **{name}** 正在泡澡，好悠闲"
        return f"💭 **{name}** 似乎在发呆"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 5 级：位置推断
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # 室内商店
    for key, display in SHOP_NAMES.items():
        if key in loc_lower:
            return f"🛍️ **{name}** 正在{display}里{'精挑细选' if not is_moving else '走来走去'}"

    # 动物建筑
    for keyword in ANIMAL_BUILDINGS:
        if keyword in loc_lower:
            return f"🐄 **{name}** 正在照顾动物"

    # 温室
    if "greenhouse" in loc_lower:
        if "Watering Can" in tool:
            return f"💧 **{name}** 正在温室里浇水"
        return f"🌱 **{name}** 正在温室里忙活"

    # 姜岛
    if "island" in loc_lower or "ginger" in loc_lower:
        return f"🏝️ **{name}** 正在姜岛上探索"

    # 农场
    if loc_lower in ("farm", "farmhouse"):
        if "farmhouse" in loc_lower:
            if tod < 600 or tod >= 2400:
                return f"💤 **{name}** 还在赖床"
            if not is_moving:
                # 🧭 2026-08-17 恒：删去"似乎在思念谁"（其他发呆提示词都是"似乎在发呆"，
                #    前半句"正在发呆"与其重合 → 后半句改成"不知道在想些什么呢"）
                return f"💭 **{name}** 正在发呆，不知道在想些什么呢"
            return f"🏠 **{name}** 在小屋里走动"
        if not is_moving and hp_ratio > 0.9:
            return f"🌾 **{name}** 正在农场里闲逛，享受田园时光"
        if is_moving:
            return f"🚶 **{name}** 正在农场里忙活"

    # 浴场
    if "bathhouse" in loc_lower or "bath" in loc_lower:
        return f"♨️ **{name}** 正在泡澡，好悠闲"

    # 海滩
    if "beach" in loc_lower:
        return f"🏖️ **{name}** 正在海滩上散步"

    # 沙漠
    if "desert" in loc_lower or ("sandy" in loc_lower and "house" in loc_lower):
        return f"🏜️ **{name}** 正在沙漠中探索"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 6 级：动态推断
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # 赶路
    if is_moving:
        return f"🚶 **{name}** 正在赶路"

    # 社交
    npcs = state_data.get("raw", {}).get("npcs", [])
    if npcs:
        names = ", ".join(n.get("name", "") for n in npcs[:3])
        if names:
            return f"💬 **{name}** 和 {names} 在一起"

    # 节日
    for keyword, display in FESTIVAL_LOCATIONS.items():
        if keyword in loc_lower:
            return f"🎉 **{name}** 正在{display}参加节日活动"

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  第 7 级：兜底（发呆需静止 N 秒；刚停下不算发呆）
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    if stam_ratio < 0.2:
        return f"😮‍💨 **{name}** 看起来很累，在休息"

    if stationary_seconds >= IDLE_STATIONARY_SECONDS:
        return f"💭 **{name}** 似乎在发呆"
    if not is_moving:
        return f"🚶 **{name}** 刚停下来歇了歇脚"
    return f"🚶 **{name}** 正在赶路"


def detect_activity(state_data: dict) -> str:
    """行为检测入口（兼容旧调用）：先看有没有玩家在附近，再看该玩家在干嘛。"""
    nearby = detect_player_nearby(state_data)
    if nearby:
        return nearby
    return describe_activity(state_data)
