"""NagiBridge API helper — thin wrapper around the HTTP endpoints."""

import requests
import base64
import time
import sys
import socket

import os
BASE_URL = os.environ.get("NAGI_URL", "http://localhost:7842")

# AI 角色（被 MCP 控制的 farmhand）的进程端口 — 彩蛋/分身类操作打到这个进程。
# Game1.player 在 host 进程是房主，在 farmhand 进程才是 AI 角色；角色名不写死，随 NAGI_URL 决定。
AI_BASE_URL = os.environ.get("NAGI_AI_URL", "http://localhost:7843")

# host 进程（房主）端口 — 广播/聊天等"房主必须能看到"的操作打到这
HOST_URL = os.environ.get("NAGI_HOST_URL", "http://localhost:7842")


def _get(endpoint, params=None):
    r = requests.get(f"{BASE_URL}{endpoint}", params=params, timeout=10)
    return r.json()


def _post(endpoint, data=None, timeout=10):
    r = requests.post(f"{BASE_URL}{endpoint}", json=data or {}, timeout=timeout)
    return r.json()


def _ai_get(endpoint, params=None):
    """打到 AI 角色进程（AI_BASE_URL，默认7843）。"""
    r = requests.get(f"{AI_BASE_URL}{endpoint}", params=params, timeout=10)
    return r.json()


def _ai_post(endpoint, data=None):
    """打到 AI 角色进程（AI_BASE_URL，默认7843）。"""
    r = requests.post(f"{AI_BASE_URL}{endpoint}", json=data or {}, timeout=10)
    return r.json()


def _host_post(endpoint, data=None, timeout=30):
    """打到 host 进程（房主/权威，默认7842）。

    世界状态的写操作（机器收放等）走这里——host 是权威端，直写能落档同步；
    farmhand(7843) 裸写 heldObject/MinutesUntilReady 这类字段不保证同步。
    """
    r = requests.post(f"{HOST_URL}{endpoint}", json=data or {}, timeout=timeout)
    return r.json()


# ── Basic queries ──

def status():
    return _get("/status")


def state(consume_events=False, light=False):
    """consume_events=True 时返回 recent_events 并从 mod 缓冲清空（"看过即清空"）。
    light=True 时不返回背包物品明细（只有 name/stack/slotIndex）——状态条只需格数，省解析。
    想看明细用 check_backpack（不带 light）。"""
    params = {}
    if consume_events:
        params["consume_events"] = "true"
    if light:
        params["light"] = "true"
    return _get("/state", params or None)


def surroundings(radius=10):
    return _get("/surroundings", {"radius": radius})


def alerts(peek=False):
    """Return queued game/system alerts. By default this drains the queue."""
    return _get("/alerts", {"peek": str(bool(peek)).lower()})


# ── Actions ──

def move_to(x, y, timeout=15):
    """⚠️ 已弃用（2026-08-13 恒拍板）：走路统一用 walk_to_coord（/walk_to，mod FindPath）。
    这个走 /move（效果几乎一样），行为保留给旧脚本/耕种下矿用，勿改。
    ⚠️ 别在 warp 瓦片上移动（会触发传送）。"""
    _post("/move", {"x": x, "y": y})
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = state()
        p = s.get("player", {})
        if not p.get("isMoving", False):
            return True
        time.sleep(0.25)
    stop()
    return False


def stop():
    return _post("/stop")


def stand():
    """🪑 主动起身（POST /stand，2026-09-11 新增）。

    调的就是游戏自家那条：`GameLocation.checkAction` → `who.StopSitting()`（animate=true，
    播起身动画+音效，跟玩家自己点起身一模一样）。没坐着时明确报错
    `{ok:False, error:"没在坐着，无需起身"}`。

    ⚠️ **返回 ok 不等于已经站起来**：animate 版只是置 `isStopSitting=true`，真正的清空发生在
    下一次 update 的 lerp 收尾（Farmer.cs:7626）⇒ 调用方必须轮询 /sittable 的 me.sitting 确认。
    """
    return _post("/stand")


def set_pause(out_of_focus=True):
    """设置"失焦暂停"选项（POST /set_pause）。
    AI 自动化进程设 False → 该窗口后台也能走位，不用抢前台焦点（不打扰 user/房主）。
    跑完记得设 True 还原。"""
    return _post("/set_pause", {"outOfFocus": out_of_focus})


def focus_game():
    """把游戏窗口切到前台。SDV 后台(失焦)会暂停走位/拾取，自动化前必调。
    优先调 /focus 端点（游戏进程自己 SetForegroundWindow，最可靠）；
    端点不存在（旧 DLL）时退回 ctypes 按进程名找 Stardew Valley.exe 窗口。"""
    # 1) /focus 端点
    try:
        r = requests.get(f"{BASE_URL}/focus", timeout=5)
        if r.json().get("ok"):
            return True
    except Exception:
        pass
    # 2) ctypes 兜底：按进程名找窗口（游戏窗口标题是路径，不能靠猜）
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        found = []

        def _proc_name(pid):
            h = kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(300)
                psapi.GetModuleBaseNameW(h, None, buf, 300)
                return buf.value.lower()
            finally:
                kernel32.CloseHandle(h)

        def _cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if "stardew" in _proc_name(pid.value):
                    found.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        user32.EnumWindows(WNDENUMPROC(_cb), 0)
        if found:
            user32.SetForegroundWindow(found[0])
            return True
    except Exception:
        pass
    return False


def face(direction):
    """Set facing direction: 0=up 1=right 2=down 3=left"""
    return _post("/face", {"direction": direction})


def select(name):
    """Select an inventory item by name."""
    return _post("/select", {"name": name})


def use_tool(name="current", force=False, power=-1):
    """Swing a tool by name (or 'current'). force=True 跳过耕地检测等验证。
    power=N 蓄力等级（用于水壶/锄头蓄力：0=单格, 1=铜, 2=铁, 3=金, 4=铱）"""
    data = {"name": name}
    if force:
        data["force"] = True
    if power >= 0:
        data["power"] = power
    return _post("/tool", data)


def use_item(force=False):
    """Use the currently held item (place seed, use tool, etc.)."""
    return _post("/use", {"force": force} if force else {})


def interact():
    return _post("/interact")


def interact_at(x, y):
    return _post("/interact", {"x": x, "y": y})


def furniture_pickup(x, y):
    """拿起瓦片上的家具（游戏左键 LowPriorityLeftClick 路径，SDV 1.6 右键拿不起家具）。
    要求：站家具旁边、家具可移除（自己摆的）、没开菜单、背包有空位。
    家具由游戏下一 tick 自动放回背包。"""
    return _post("/furniture_pickup", {"x": x, "y": y})


def furniture_scan():
    """扫描当前地点所有家具：名字/瓦片坐标/尺寸/类型/isPassable/isTV。
    AI 要知道家具摆哪、哪些能交互（TV/日历/壁炉）就靠这个。"""
    return _get("/furniture")


def sittable(radius=7):
    """🪑 扫玩家附近**能坐的**东西（椅子/长凳/沙发/钢琴 + 地图座椅）。

    反编译定论（2026-09-10）：能坐的只有两类，都经 checkAction → BeginSitting：
      ① 家具 Furniture.GetSeatCapacity() > 0（furniture_type 0=chair/1=bench/2=couch/3=armchair
         + 直立/黑钢琴）；
      ② loc.mapSeats 里的 MapSeat（Buildings 层瓦片经 Data/ChairTiles 匹配生成）——
         **萨隆的凳子/桌椅走这条，不在 /furniture 里**（/furniture 在 Saloon 回 count=0）。
    ⚠️ 落座硬约束：玩家须距座位 96px(1.5 格)内，否则游戏静默不落座 ⇒ 要先就位再坐。

    返回 {ok, location,
          me:{x,y,sitting,seatX,seatY,seatKind,seatName}, radius, count,
          seats:[{kind,name,x,y,seatX,seatY,capacity,free,blocked,dist,face,direction}]}
    （x,y = 要 /interact 的座位格；kind = "furniture" | "map"）

    🆕 2026-09-11：`face`(bool) = 该座位**吃不吃 `sit(face=…)`**（判据在 C# 里照抄游戏，
      见 Furniture.cs:712 / MapSeat.cs:317-334）；`direction` = MapSeat 原始朝向
      （-2=opposite，家具恒 null）。
    `me.seatKind`("furniture"/"map") + `me.seatName`：家具是本地化 DisplayName（"红色餐椅"），
      地图座椅是**内部英文 seatType**（"bench"）——没本地化名，别硬塞进中文句子。"""
    return _get("/sittable", {"radius": radius})


def chat(message):
    return _post("/chat", {"message": message})


def host_chat(message):
    """💬 广播到 host 进程(7842)——房主窗口可见（正常聊天框）。
    ⚠️ 2026-08-15 恒拍板：给 user 的通知/推送**统一走这个**（HUD 过夜复盘看不到）。
    host 进程 /chat 已改成只 addMessage 本地显示——不再顶掉 user 正在输入的内容，
    结算复盘（聊天窗口）也看得见；要广播成 AI 自己的消息用 chat()（走 AI 进程）。"""
    return requests.post(f"{HOST_URL}/chat", json={"message": message}, timeout=10).json()


def host_push(message, sender="DeeSeek"):
    """💬 推送消息到房主屏幕 ChatHud 覆盖层（/chat/push）——不碰聊天框。
    ⚠️ 2026-08-15 实测：ChatHud 恒看不到！给 user 的通知统一用 host_chat()（聊天框）。"""
    try:
        return requests.post(f"{HOST_URL}/chat/push",
                             json={"sender": sender, "message": message}, timeout=10).json()
    except Exception:
        return {"ok": False, "error": "host_push failed"}


def host_hud(message):
    """💬 HUD 通知到房主屏幕左下角（/hud，Game1.addHUDMessage）——不碰聊天框。
    ⚠️ 2026-08-15 恒拍板：**已弃用**——过夜复盘/结算时恒看不到 HUD。给 user 的通知统一走 host_chat()。"""
    try:
        return requests.post(f"{HOST_URL}/hud", json={"message": message}, timeout=10).json()
    except Exception:
        return {"ok": False, "error": "host_hud failed"}


def host_state():
    """从 host 进程(房主/用户，默认7842)取完整状态。

    心跳检测"用户在干嘛"用——给 AI 看的玩家动态必须描述用户(房主)，
    而不是 AI 自己的角色。"""
    r = requests.get(f"{HOST_URL}/state", timeout=10)
    return r.json()


def host_sittable(radius=7):
    """🪑 在 host 进程(7842)读"房主是否坐着"——心跳坐着彩蛋用。

    理由同 host_state：给 AI 看的玩家动态描述的是**用户/房主**，不是 AI 自己。
    返回 {ok, location, me:{x,y,sitting,seatX,seatY,seatKind,seatName}, seats:[...]}。

    ⚠️ `seatName` 两类性质不同（2026-09-11）：家具=本地化 DisplayName（"红色餐椅"），
    地图座椅=内部英文 seatType（"bench"）⇒ **只有 seatKind=="furniture" 的名字能进中文句子**。"""
    r = requests.get(f"{HOST_URL}/sittable", params={"radius": radius}, timeout=10)
    return r.json()


def host_pool():
    """♨️ 在 host 进程(7842)读"房主是否在水里"——心跳泡澡彩蛋用。

    `/pool` 的 `me` 段有 swimming / bathingClothes（泳池"游没游泳"的唯一权威是
    `Character.swimming` 这个 NetBool，跟水格无关）。"""
    r = requests.get(f"{HOST_URL}/pool", timeout=10)
    return r.json()


def host_mine_rock():
    """🏔️ 矮人商店堵路石状态（读 host/权威端 7842）。

    石头 (BC)78 是 Mine 层的 loc.objects——属于世界状态，联机下以宿主/房主(7842)为权威。
    farmhand(7843) 进程可能读到自己 Mine 实例不同步的石头（AI 进程≠权威）。
    ⚠️ _dwarf_rock_blocked 必须读 host——否则联机 AI 端读到未炸→误拦。"""
    return requests.get(f"{HOST_URL}/mine_rock", timeout=10).json()


def unlock_debug():
    """取解锁 debug（走 AI 进程 7843）。

    /unlock_debug 返回 relevantMail(=mailReceived=背包的钱包)，用于钱包物品门禁
    （矮人商店=HasDwarvishTranslationGuide 等，同 HasRustyKey/HasSkullKey 检测源）。
    ⚠️ 读 MasterPlayer（房主）的 mailReceived——和 unlock_status 同一 AI 端口(7843)。
    """
    return _ai_get("/unlock_debug")


def emote(emote_id):
    return _post("/emote", {"id": emote_id})


# ── Convenience helpers ──

def player_tile():
    """Return (x, y) of current player tile."""
    s = state()
    p = s["player"]
    return p["x"], p["y"]


def player_health():
    """Return (current, max) health."""
    s = state()
    p = s["player"]
    return p["health"], p["maxHealth"]


def player_stamina():
    """Return (current, max) stamina."""
    s = state()
    p = s["player"]
    return p["stamina"], p["maxStamina"]


def current_location():
    s = state()
    return s["location"]["name"]


def watering_can_water():
    """Return (waterLeft, waterMax) or (None, None) if not found.
    匹配任何级别的水壶（Copper/Gold/Iridium/... Watering Can）。"""
    s = state()
    for item in s.get("inventory", []):
        name = item.get("name", "")
        if "Watering Can" in name:
            return item.get("waterLeft"), item.get("waterMax")
    return None, None


def refill_water():
    """Refill watering can via /refill endpoint."""
    return _post("/refill")


def menu():
    return _get("/menu")


def menu_click(option=None, button=None, x=None, y=None, item=None, right=None, quantity=1, action=None, real=False, slot=None, category=None):
    data = {}
    if option is not None: data["option"] = option
    if button is not None: data["button"] = button
    if x is not None: data["x"] = x
    if y is not None: data["y"] = y
    if item is not None: data["item"] = item
    if right: data["right"] = True
    if quantity != 1: data["quantity"] = quantity
    if action: data["action"] = action
    if real: data["real"] = True
    if slot is not None: data["slot"] = slot
    if category is not None: data["category"] = category
    return _post("/menu/click", data)


def menu_number(value=None, confirm=False):
    """🔢 NumberSelectionMenu（数量输入）——反射写 numberSelectedBox.Text（同捏人 nameBox）。
    星露谷展览会 50g 换 1 星星币兑换台 / 转盘押注都弹它。value 省略=只读（返回 box 文本 + min/max/price）；
    value=N=设数量；confirm=True=填完直接点确定。取消用 /menu/click button=cancel。
    返回 currentValue/min/max/price。"""
    data = {}
    if value is not None: data["value"] = value
    if confirm: data["confirm"] = True
    return _post("/menu/number", data)


def minigame_click(action=None, x=None, y=None):
    """🎰 赌场小游戏（老虎机 Slots/21点 CalicoJack）点按钮——游戏原生 Minigame，/menu/click 对它无效。
    action 用语义点名：老虎机 bet10/bet100/done；21点 hit/stand/double/play_again/quit（反射定位按钮 bounds）。
    不传 action 则用裸坐标 x,y（由游戏判是否落按钮内）。返回当前小游戏 minigame 名。"""
    data = {}
    if action is not None: data["action"] = action
    if x is not None: data["x"] = x
    if y is not None: data["y"] = y
    return _post("/minigame_click", data)


def minigame_state():
    """🎰 读当前赌场小游戏状态摘要（牌面/组合/赌注/结果），不截图也能知道现状。
    CalicoJack(21点)：playerCards/dealerCards(点数)、currentBet、showingResultsScreen、playerWon、highStakes；
    Slots(老虎机)：slots(3转盘当前组合)、currentBet、spinning、showResult、payoutModifier、clubCoins。"""
    return _get("/minigame_state")


def dance_invite(target="", direct=False, mirror=True):
    """💃 花舞节邀请跳舞（2026-08-20）：玩家默认提案（对方弹接受框）；NPC 或兜底走 direct=True 直接设 dancePartner。"""
    data = {}
    if target: data["target"] = target
    if direct: data["direct"] = True
    if not mirror: data["mirror"] = False
    return _post("/dance_invite", data)


def craft(name, count=1):
    return _post("/craft", {"name": name, "count": count})


def machines():
    return _get("/machines")


def machine_collect(location="", type="", limit=0):
    """POST /machine_collect — 批量收机器产物（全农场/指定地点/指定类型），打 AI 进程 7843。
    产物进 AI(轮回)背包（恒批注 2026-08-13：别打到 host 恒的号，会塞满恒背包）。"""
    return _ai_post("/machine_collect", {"location": location, "type": type, "limit": limit})


def machine_load(item_id, location="", type="", count=0):
    """POST /machine_load — 往空机器批量放原料（游戏自己算配方时间），打 AI 进程 7843。
    用 AI(轮回)背包装料（恒批注 2026-08-13）。"""
    return _ai_post("/machine_load", {"itemId": item_id, "location": location, "type": type, "count": count})


def farm_report():
    """GET /farm_report — 全农场扫描（作物聚合/动物逐只/机器逐台，跨所有建筑+温室+地窖）"""
    return _get("/farm_report")


def store_all(keepTools=True, what=None, target=None, default=None, clear_all=False, counts=None):
    """POST /store_all — 场景内智能存储（"堆高高"）或用户指定箱直存。只处理当前场景箱子。
    keepTools: 工具不存（默认 True）
    what: 限定物品名/ID（list 或逗号分隔字符串），给了=只存这些
    counts: dict {物品名: 数量}，给数量则只存那 N 份（余量留背包，拆堆）
    clear_all: True=显式存全部非工具腾空间（默认 False → 只归位：只存某箱已有同类堆的物品，不清背包）
    target: dict 指定箱 —— {"color": "#C0C0C0"} / {"name": "矿石"} / {"x": 5, "y": 8}
    default: dict {"x": 20, "y": 15} 默认箱（None=自动选空位最多箱）
    返回 {ok, mode, scope('specified'/'all'/'tidy'), noHome, stored[], leftovers[], chests[], totalFree}
    """
    data = {"keepTools": bool(keepTools)}
    if clear_all:
        data["all"] = True
    if what:
        if isinstance(what, (list, tuple)):
            data["what"] = list(what)
        else:
            data["what"] = str(what)
    if counts:
        data["counts"] = counts
    if target:
        data["target"] = target
    if default:
        data["default"] = default
    return _post("/store_all", data)


def chest_take_list(items):
    """POST /chest_take_list — 从当前场景所有存储箱一次性取多项（智能路由，自动找对箱；单箱不够跨箱凑）。
    items: list of {"name": "...", "count": int} 或字符串 "西瓜,铜矿石"（count=-1 缺省=取全量）。
    不改玩家位置（原子直操）。返回 {ok, location, items:[{item,wanted,taken,from:[{x,y,name,color,autoTag,got}]}]}"""
    return _post("/chest_take_list", {"items": items})


def animals():
    return _get("/animals")


def map_data():
    """GET /map — 返回**当前地图**的建筑物、动物、NPC、传送点
    ⚠️ 2026-08-26 恒：名副其实——跟着玩家当前位置走。站在室内（FarmHouse/棚内）
    读到的 buildings 是 0 个。要"农场上有哪些建筑"这种与站位无关的事实，用 farm_buildings()。"""
    return _get("/map")


def farm_buildings():
    """GET /farm_buildings — 农场所有建筑（直接读 Farm.buildings，与玩家站在哪无关）。
    返回 {"buildings":[{type,x,y,width,height,doorX,doorY,indoorsName}, ...]}"""
    return _get("/farm_buildings")


def warp(location, x=None, y=None):
    data = {"location": location}
    if x is not None: data["x"] = x
    if y is not None: data["y"] = y
    return _post("/warp", data)


# ── 🛋️ 计划/兜底辅助（2026-08-14 全自动一天）──

def ai_port():
    """AI farmhand 进程端口（从 AI_BASE_URL 解析）。计划调度器给脚本统一注入 --port 用。"""
    import re as _re
    m = _re.search(r":(\d+)$", AI_BASE_URL)
    return int(m.group(1)) if m else 7843


def ai_name():
    """AI farmhand 角色名（/state player.name）。兜底睡觉传 who=自己名（睡自家床）。"""
    try:
        return (_ai_get("/state").get("player") or {}).get("name") or ""
    except Exception:
        return ""


def day_key(state_data=None):
    """游戏日期键 "season|day|year"（调度器判"日翻转"用）。state_data 缺省时现拉。"""
    t = (state_data or {}).get("time", {}) or {}
    if not t:
        try:
            t = state(light=True).get("time", {}) or {}
        except Exception:
            return None
    return f"{t.get('season')}|{t.get('dayOfMonth')}|{t.get('year')}"


def warp_safe():
    """把 AI farmhand 传回安全位（家 homeLocation；没有则 Farm）。
    节日暂停 / 中止兜底用——别让脚本半路把角色丢在矿里/野外。"""
    try:
        s = _ai_get("/state")
        home = (s.get("player") or {}).get("homeLocation") or "Farm"
        r = _ai_post("/warp", {"location": home})
        time.sleep(1)
        return f"🏠 已传回 {home}"
    except Exception as e:
        return f"⚠️ warp 安全位失败: {e}"


def warp_into(location, x=None, y=None, near=False):
    """同步切进任意地点（含建筑内部，游戏原生 /warp 进不去的）。
    机器收放用：直设 currentLocation + 位置，不依赖异步 tick。
    near=True 绝不落目标格本身（避免站到可踩踏的机器如 Cask 上），只落四邻。"""
    data = {"location": location}
    if x is not None: data["x"] = x
    if y is not None: data["y"] = y
    if near: data["near"] = True
    return _post("/warp_into", data)


def warp_building(bx, by, x=None, y=None, near=False):
    """按建筑在农场的坐标 (bx,by) 切进其室内（多栋同名建筑如 Cabin 用）。
    near=True 绝不落目标格本身，只落四邻。"""
    data = {"bx": bx, "by": by}
    if x is not None: data["x"] = x
    if y is not None: data["y"] = y
    if near: data["near"] = True
    return _post("/warp_building", data)


def sell_to_shop(name, count=-1):
    """Sell item(s) to the currently open shop menu.
    count=-1 sells all; count=N sells N items.
    Requires an active ShopMenu. Use /sell for shipping bin.
    Returns {ok, sold[], totalGold, remainingGold}.
    """
    return _post("/sell_to_shop", {"name": name, "count": count})


def sell(name=None, sell_all=False):
    data = {}
    if name: data["name"] = name
    if sell_all: data["all"] = True
    return _post("/sell", data)


def key(k, count=1, hold=0):
    """模拟按键。hold>0 时长按（走到边缘/传送瓦片用，如 400ms）。"""
    return _post("/key", {"key": k, "count": count, "hold": hold})


def wait_tool_animation(seconds=0.6):
    """Wait for tool animation to finish."""
    time.sleep(seconds)


# ── Farm layout constants ──
SHIPPING_BIN = (71, 14)
CHEST_POSITIONS = [(70, 14), (69, 14)]
FURNACE_POSITIONS = [(73, 14), (74, 14)]


def face_toward(tx, ty):
    """Calculate face direction from actual player position to target tile."""
    s = state()
    px, py = s["player"]["x"], s["player"]["y"]
    dx, dy = tx - px, ty - py
    if dx == 0 and dy == 0:
        return 2
    if abs(dx) > abs(dy):
        return 1 if dx > 0 else 3
    return 2 if dy > 0 else 0


def interact_machine(mx, my):
    """Walk to a machine and interact — tries multiple angles until facing matches."""
    approaches = [(mx, my+1), (mx, my-1), (mx-1, my), (mx+1, my)]
    for nx, ny in approaches:
        move_to(nx, ny, timeout=10)
        time.sleep(0.2)
        d = face_toward(mx, my)
        face(d)
        time.sleep(0.15)
        s = state()
        px, py = s["player"]["x"], s["player"]["y"]
        fd = s["player"]["facingDirection"]
        fdx = [0, 1, 0, -1][fd]
        fdy = [-1, 0, 1, 0][fd]
        if px + fdx == mx and py + fdy == my:
            interact()
            time.sleep(0.5)
            return True
    interact()
    time.sleep(0.5)
    return False


def log(msg):
    try:
        print(f"[NagiBridge] {msg}", flush=True)
    except UnicodeEncodeError:
        # Windows GBK 终端无法显示 emoji，替换为 ASCII
        safe = msg.encode('gbk', errors='replace').decode('gbk', errors='replace')
        print(f"[NagiBridge] {safe}", flush=True)


# ── 捏脸 ──

def set_appearance(**kwargs):
    """Hot-change farmer appearance at runtime.
    Supported keyword args: hair (int), hairColor (hex str), skin (int),
    skinColor (hex str), shirt (int), pants (int), acc (int),
    eyeColor (hex str), pantsColor (hex str).
    """
    return _post("/appearance", kwargs)


# ── 烹饪 ──

def cook(name: str, count: int = 1):
    """Cook a recipe. Must learn it first + have ingredients."""
    return _post("/cook", {"name": name, "count": count})


def list_recipes():
    """List all cooking recipes the player knows, with ingredient status."""
    return _get("/recipes")


# ── Pre-checks ──

def inventory_items():
    s = state()
    return s.get("inventory", [])

def has_item(name):
    """按名匹配（2026-08-15 修：工具用包含匹配——"Hoe" 认 Iridium Hoe/Copper Hoe 等；
    种子名也兼容）。
    ⚠️ 2026-09-04 恒改：种子「霜瓜种子」DisplayName 是中文、Name 是英文 → 只匹配 name 会漏检
    （冒烟实测：till 成功但 precheck "背包没有种子"）。补 displayName 中英混双，与 ModEntry
    store/take、HandleSelect 一致；大小写不敏感防英文名大小写差异。"""
    n = (name or "").lower()
    if not n:
        return False
    for i in inventory_items():
        nm = (i.get("name") or "").lower()
        dnm = (i.get("displayName") or "").lower()
        if n in nm or n in dnm:
            return True
    return False

def inventory_free_slots():
    s = state()
    inv = s.get("inventory", [])
    total = s.get("player", {}).get("maxItems", 36)
    return total - len([i for i in inv if i.get("name")])

def clear_menu():
    for _ in range(10):
        m = menu()
        if not m or m.get("type") is None or m.get("type") == "none":
            return True
        key("confirm")
        time.sleep(0.3)
    return False

def drain_alerts():
    try:
        return alerts(peek=False)
    except:
        return []

def precheck_farm(seed_name):
    errors = []
    if not has_item("Hoe"):
        errors.append("背包没有锄头")
    if not has_item(seed_name):
        errors.append(f"背包没有种子: {seed_name}")
    if not has_item("Watering Can"):
        errors.append("背包没有水壶")
    return errors

def precheck_area(tiles, radius=10):
    blocked = []
    data = surroundings(radius)
    tile_set = set(tiles)
    for t in data.get("tiles", []):
        pos = (t.get("x"), t.get("y"))
        if pos not in tile_set:
            continue
        obj_name = t.get("object", "")
        terrain_name = t.get("terrain", "")
        if obj_name:
            # 洒水器不是障碍物
            if "Sprinkler" in obj_name:
                continue
            blocked.append((pos, obj_name))
        elif terrain_name and "Tree" in terrain_name:
            blocked.append((pos, terrain_name))
    return blocked

def postcheck_menu():
    clear_menu()

def crawl_bed(action="locate", player2=None):
    """🛏️ 爬床彩蛋：在 AI 角色进程(AI_BASE_URL)执行，爬到房主的床上。
    locate=找房主床坐标+当前是否在床上(不动玩家) / sleep=自动切换(不在床=爬床躺下, 在床上=起身偷溜)。
    返回: {"ok", "bed": {...}, "player2", "player", "isInBed", "farmers"}
    注意：走 AI_BASE_URL(默认7843)，不是主连接——演员是 AI 角色不是房主。
    """
    data = {"action": action}
    if player2:
        data["player2"] = player2
    return _ai_post("/crawl_bed", data)

# ── 角色/端口自动检测（2026-08-14 睡觉攻坚）─────────────────────────────
# 端口↔角色映射按"谁先开游戏谁占 7842"分配，不写死。
# detect_roles 探测 7842/7843：/crawl_bed locate 返回 player2=本进程玩家名、player=房主名，
# 相等→该进程是 host(恒)；不等→该进程是 AI/farmhand(轮回)。
# 探测成功即改模块全局 AI_BASE_URL/HOST_URL（每次调用读全局，立即生效）+ env（spawn 脚本读 env）。
_ROLES_CACHE = {"ts": 0.0, "map": None}


def _probe_role(port: int) -> dict:
    """裸 requests 探测单端口角色（不用 _ai_*/_host_*，避免递归 ensure_roles）。
    先 TCP 连通性快检（Windows 上关闭端口 HTTP 探测会重试卡 ~4s，TCP 0.3s 内跳过），
    再 HTTP 探测（禁重试 + 3s 超时）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        r = s.connect_ex(("localhost", port))
        s.close()
        if r != 0:
            return {"port": port, "role": None, "tcp": False, "error": f"port closed (connect_ex={r})"}
    except Exception as e:
        return {"port": port, "role": None, "tcp": False, "error": str(e)}
    sess = requests.Session()
    sess.mount("http://", requests.adapters.HTTPAdapter(max_retries=0))
    # ① 首选 `/crawl_bed locate`：直接给 `player`/`player2`，判角色最准（还能顺带拿 isMain/home）。
    try:
        rr = sess.post(f"http://localhost:{port}/crawl_bed",
                       json={"action": "locate"}, timeout=3)
        data = rr.json()
        if data.get("ok"):
            p2 = data.get("player2")
            p1 = data.get("player", p2)
            return {"port": port, "role": ("host" if p2 == p1 else "ai"),
                    "tcp": True, "name": p2, "player2": p2, "player": p1}
        _crawl_err = data.get("error", "not ok")
    except Exception as e:
        _crawl_err = str(e)
    # ② 兜底（2026-09-12 实锤）：`/crawl_bed` **会在"有人躺床 / 结算菜单开着"时返回空**
    #    （当晚 7842 实测 HTTP 200 但 **0 字节**）。以前这一下就直接判"本端口没角色"，
    #    进而把映射**折叠成 solo** —— 方向反了就是"所有 AI 操作静默打在房主身上"（09-11 事故）。
    #    改用**只读、无副作用**的 `/state`：房主 `homeLocation` 恒为 "FarmHouse"，
    #    farmhand 的是 `FarmHouse<guid>`（自家小屋）。
    try:
        d2 = sess.get(f"http://localhost:{port}/state", timeout=3).json()
        pl = d2.get("player") or {}
        home = str(pl.get("homeLocation") or "")
        if pl.get("name"):
            return {"port": port, "role": ("host" if home == "FarmHouse" else "ai"),
                    "tcp": True, "name": pl.get("name"), "via": "state-fallback",
                    "crawl_error": _crawl_err, "homeLocation": home}
    except Exception:
        pass
    return {"port": port, "role": None, "tcp": True,
            "error": f"crawl_bed 与 state 都没探到（crawl: {_crawl_err}）"}


def _set_roles(ai_port: int, host_port: int):
    """更新模块全局（立即生效）+ env（spawn 的 fish_run/bomb_* 读 env 的 --port）。"""
    global BASE_URL, AI_BASE_URL, HOST_URL
    AI_BASE_URL = f"http://localhost:{ai_port}"
    HOST_URL = f"http://localhost:{host_port}"
    BASE_URL = AI_BASE_URL  # MCP 服务器以 AI 为主角，主连接跟随 AI
    os.environ["NAGI_URL"] = AI_BASE_URL
    os.environ["NAGI_AI_URL"] = AI_BASE_URL
    os.environ["NAGI_HOST_URL"] = HOST_URL
    # ⚠️ 2026-09-12：**NAGI_PORT 以前漏设** ⇒ 凡是从 env 取端口的辅助脚本（`go_to.py` 的
    #    `--port` 默认 `int(os.environ.get("NAGI_PORT","7842"))`）**静默默认打 7842=房主**，
    #    于是"帮 AI 走个位"变成"挪恒的角色"。这里补上，和上面三个 URL 一起跟着 AI 端口走。
    os.environ["NAGI_PORT"] = str(ai_port)


def detect_roles(ports=(7842, 7843)) -> dict:
    """探测端口↔角色映射。返回 {ok, ai:{port,name}, host:{port,name}, solo, probes}。
    单进程（单人测试）→ AI=host=该端口，且 `solo=True`。都没响应 → ok:False（保持现状）。

    ⚠️ **`solo=True` 是个危险状态**（2026-09-11 真机踩到）：多人世界（房主 + farmhand 两个进程）
    里若在"房主已进世界、farmhand 还没加入"的窗口重启 MCP 服务，就只会探到一个进程、被折叠成
    solo ⇒ **AI 和 host 都指向房主** ⇒ 所有"AI 操作"（含 `_run_script` 起的脚本）静默打在房主身上。
    当天 `cabin statue` 就是这么把恒的角色走掉、还摸了他雕像的。
    ⇒ 调用方**必须**把 solo 当警告处理（启动横幅要印醒目、状态条要标），别当成正常单人模式。
    """
    global _ROLES_CACHE
    probes = [_probe_role(p) for p in ports]
    live = [p for p in probes if p.get("role")]
    ai = next((p for p in live if p["role"] == "ai"), None)
    host = next((p for p in live if p["role"] == "host"), None)
    solo = None
    if live and len(live) == 1 and (ai is None or host is None):
        only = live[0]
        # 🔴 2026-09-12 恒拍板：**别急着折叠**。以前只看"活口只剩一个"就塌成 solo，但
        #    "另一个端口探不到"有两种截然不同的原因，后果天差地别：
        #      (a) 进程真的不在（**TCP 都连不上**）→ 真·单人，折叠是对的；
        #      (b) 进程在、TCP 通，只是探测端点没答上来（**游戏卡在菜单/睡觉流程里** ——
        #          当晚实测：恒躺床 + 结算菜单开着时 `/crawl_bed` 回 HTTP 200 但 0 字节）
        #          → **折叠是灾难**：AI/host 全指同一端口，方向反了就是
        #          "**所有 AI 操作静默打在房主身上**"（2026-09-11 那次事故）。
        #    ⇒ (b) 一律**沿用上次的正确映射**并报警：宁可不动，也不塌。
        _other_alive = any(p.get("tcp") for p in probes if p["port"] != only["port"])
        _prev = _ROLES_CACHE.get("map") or {}
        if _other_alive:
            if _prev.get("ai") and _prev.get("host"):
                return {"ok": True, "ai": _prev["ai"], "host": _prev["host"], "solo": False,
                        "probes": probes, "kept_previous": True,
                        "warning": (f"⚠️ {only['port']} 探到了，但另一端口**只通不答**"
                                    f"（多半卡在菜单/睡觉流程）—— **沿用上次映射，不折叠成 solo**")}
            # 连可沿用的旧映射都没有（冷启动就撞上）→ **宁可保持现状**，也不塌成 solo
            return {"ok": False, "ai": ai, "host": host, "solo": False, "probes": probes,
                    "error": (f"{only['port']} 探到了，但另一端口**只通不答**"
                              f"（多半卡在菜单/睡觉流程）且无可沿用的旧映射 —— "
                              f"**保持现状，不折叠成 solo**")}
        solo = only  # 单人：唯一进程既是 host 也当 AI
        host = host or solo
        ai = ai or solo
    if not ai or not host:
        return {"ok": False, "ai": ai, "host": host, "solo": False, "probes": probes,
                "error": f"探测不完整（{len(live)} 个响应）：需同时有 host+AI 进程"}
    _set_roles(ai["port"], host["port"])
    _ROLES_CACHE = {"ts": time.time(), "map": {"ai": ai, "host": host, "solo": solo is not None}}
    return {"ok": True, "ai": ai, "host": host, "solo": solo is not None, "probes": probes}


def roles_solo() -> bool:
    """当前映射是不是"单进程折叠"（AI 与 host 都指同一个进程）。
    ⚠️ 是的话**所有 AI 操作都会打在那一个角色身上**——多人世界里通常意味着
    "重启 MCP 服务时 farmhand 还没进世界"。读不到缓存 → False（还没探过，不瞎报）。"""
    return bool((_ROLES_CACHE.get("map") or {}).get("solo"))


def ensure_roles(ttl=30) -> dict:
    """带 TTL 缓存的角色检测（ttl 秒内不重复探测，避免热路径发请求）。"""
    global _ROLES_CACHE
    if _ROLES_CACHE["map"] and time.time() - _ROLES_CACHE["ts"] < ttl:
        return {"ok": True, "cached": True, **_ROLES_CACHE["map"]}
    return detect_roles()


def which_role() -> dict:
    """确认当前角色映射（MCP which_role 工具用）。"""
    r = detect_roles()
    if not r.get("ok"):
        return {"ok": False, "ai": None, "host": None, "solo": False, "error": r.get("error"),
                "note": f"游戏进程未全部就绪。AI_BASE_URL={AI_BASE_URL}"}
    return {"ok": True, "solo": bool(r.get("solo")),
            "ai": {"port": r["ai"]["port"], "name": r["ai"].get("name")},
            "host": {"port": r["host"]["port"], "name": r["host"].get("name")}}


def _in_bed_now(retries=3) -> bool:
    """当前 AI 角色是否在床上（/crawl_bed locate 的 isInBed）。
    retries>1 时轮询重试抹平瞬时弹床（游戏 tick 可能短暂复位 isInBed，实测 1s 内会回来）。"""
    for _ in range(max(1, retries)):
        try:
            if _ai_post("/crawl_bed", {"action": "locate"}).get("isInBed"):
                return True
        except Exception:
            pass
        if retries > 1:
            time.sleep(0.5)
    return False


def _sleeping_now() -> bool:
    """是否已进入睡眠流程：isInBed 或 ReadyCheckDialog 任一成立即可（防瞬时弹床误判）。
    ⚠️ 对话框出现后重爬=第二次 Sleep_Yes 会破坏 ready 同步，所以这里宁可放宽判定。"""
    try:
        if _in_bed_now(retries=1):
            return True
        return (_ai_get("/state").get("activeMenu") or {}).get("type") == "ReadyCheckDialog"
    except Exception:
        return _in_bed_now(retries=1)


def _snap_onto_bed(bx, by):
    """精确把玩家放到床的睡眠格上（walk 容差可能站偏 → isInBed 被游戏 tick 弹掉）。
    实测 2×3 床只有中间行(y=by+1)能站住 isInBed；对候选格依次 /position 探测。
    /position 是直接设 Position（非 warpFarmer），传床格不 redirect（实测无弹回门口）。
    返回落点 (x,y)。"""
    for dy in (1, 0, 2):
        _ai_post("/position", {"x": bx, "y": by + dy})
        time.sleep(0.9)   # 0.6→0.9：给游戏 tick 置 isInBed 时间（实测 0.7s 才 True，0.6 卡边界→无谓多探测一轮）
        if _in_bed_now(retries=2):
            return (bx, by + dy)
    for dy in (1, 0, 2):  # 2 宽床兜底右列
        _ai_post("/position", {"x": bx + 1, "y": by + dy})
        time.sleep(0.9)
        if _in_bed_now(retries=2):
            return (bx + 1, by + dy)
    return (bx, by + 1)


def _sleep_bed_gate(locate: dict, bed_location: str) -> str:
    """🔑 睡流程本地门禁（2026-08-22 恒：只睡当前场景的床，不跨图、不 warp）。
    只有 cabin / farmhouse / 姜岛小屋(IslandFarmHouse) 有床，别处一定没床。
    返回空串=通过；非空=拦截原因（AI 应先用 map_go 回家/到对方屋再 go_sleep）。
    - ① 精确（新 DLL 才有 curLoc 当前场景唯一名）：扫到的床必须就在当前场景
      （co-sleep：恒的床在恒 FarmHouse，和当前小屋不同 → 拦）。
    - ② 兜底（老 DLL 无 curLoc）：只按当前场景显示名粗判 Cabin/FarmHouse/IslandFarmHouse。"""
    try:
        cur_loc = locate.get("curLoc")  # 新 DLL：当前场景唯一名
        cur_show = (_ai_get("/state").get("location") or {}).get("name", "")
        if cur_loc and bed_location != cur_loc:
            return f"当前场景没有床（床位在{bed_location}，当前在{cur_loc}）——先用 map_go 回家/到对方屋再 go_sleep"
        if not cur_loc and cur_show not in ("Cabin", "FarmHouse", "IslandFarmHouse"):
            return f"当前场景没有床（{cur_show}）——先用 map_go 回家/到对方屋再 go_sleep"
        return ""
    except Exception as e:
        return f"当前场景没有床（{e}）"


def go_sleep_flow(who="", log=None, humanize=True) -> dict:
    """🛏️ 完整睡觉流程（humanize 二选一）：
    - humanize=True（默认，AI 主动 go_sleep/lie_bed 的"拟人休息"）：**本地扫床、去 warp**。
      当前场景没床（非 Cabin/FarmHouse/姜岛小屋，或床不在当前场景）→ 报「当前场景没有床」终止；
      有床 → 同图 walk 到床边 → crawl_bed sleep → /sleep stay → 等过夜；夜不过则原地重爬重就绪。
      不跨图，远途回小屋/到对方屋由 AI 先 map_go。
    - humanize=False（内部兜底，凌晨自动睡）：**沿用旧版 warp**——warp 进床所在建筑 → walk → 对位 → 躺 → 就绪；
      夜不过走"warp Farm 出建筑 → 回床重爬"。兜底要稳，不用拟人。
    返回 {ok, summary, steps:[{step,ok,detail}], woke_in_expected_bed, wake, co_sleep, slept_in}。
    co_sleep=True 表示睡的是别人的床（爬床彩蛋）；配合 woke_in_expected_bed=True = 一起睡彩蛋成功。
    log(step, ok, detail) 供回归脚本打印/落盘。
    """
    steps = []

    def rec(step, ok, detail):
        steps.append({"step": step, "ok": bool(ok), "detail": str(detail)})
        if log:
            try:
                log(step, bool(ok), str(detail))
            except Exception:
                pass

    def finish(ok, summary, co_sleep=False, slept_in=""):
        return {"ok": ok, "summary": summary, "steps": steps,
                "woke_in_expected_bed": None, "wake": None,
                "co_sleep": co_sleep, "slept_in": slept_in}

    detect_roles()  # 尽力修正端口↔角色（失败则保持现状，不打断）
    try:
        # 1. 找目标玩家的床
        locate = _ai_post("/crawl_bed", {"action": "locate", "player": who})
        if not locate.get("ok"):
            rec("locate", False, locate.get("error", locate))
            return finish(False, f"❌ 找床失败: {locate.get('error', locate)}")
        bed = locate["bed"]
        loc, bx, by = bed["location"], bed["x"], bed["y"]
        p2 = locate.get("player2") or "我"
        p1 = locate.get("player", p2)
        co_sleep = p1 != p2  # 睡的是别人的床 → 爬床彩蛋（睡自己家 False）
        rec("locate", True, f"目标床: {loc}({bx},{by}) 目标玩家: {p1} ({'一起睡' if co_sleep else '自家'})")

        # 🔑 本地门禁（2026-08-22 恒：睡不跨图、不 warp；床必须在当前场景）——拟人版才拦
        if humanize:
            _gate = _sleep_bed_gate(locate, loc)
            if _gate:
                rec("gate", False, _gate)
                return finish(False, f"❌ {_gate}", co_sleep, p1)

        # 2. warp 进目标地点（humanize=False 兜底才用）→ 同图 walk 到床边（自然感，成败不影响）→ 精确对位床格。
        # ⚠️ 2026-08-14 修复：walk 落点容差可能让玩家停在床格旁 → isInBed 被 tick 弹掉。
        #    改用 /position 精确落到床的睡眠格（2×3 床只有中间行能站住，见 _snap_onto_bed）。
        if not humanize:
            _ai_post("/warp", {"location": loc})
            time.sleep(1.5)
        try:
            _ai_post("/walk_to", {"location": loc, "x": bx, "y": by + 1})
            for _ in range(20):  # 最多等 10s，失败交给精确对位兜底
                time.sleep(0.5)
                if not _ai_get("/state").get("player", {}).get("isMoving"):
                    break
        except Exception:
            pass
        snap = _snap_onto_bed(bx, by)
        time.sleep(0.3)
        rec("reach_bed", True, f"床边对位到 {snap}")

        # 3. 爬床（只设 isInBed，不挪位）
        r = _ai_post("/crawl_bed", {"action": "sleep", "player": who})
        if not r.get("ok"):
            rec("crawl", False, r.get("error", r))
            return finish(False, f"❌ 爬床失败: {r.get('error', r)}", co_sleep, p1)
        rec("crawl", True, f"crawled, bed={r.get('bed')}")

        # 4. 就地 ready（不 warp 回家）
        r2 = _ai_post("/sleep", {"stay": True})
        if not r2.get("ok"):
            rec("ready", False, r2.get("error", r2))
            return finish(False, f"❌ 睡觉触发失败: {r2.get('error', r2)}", co_sleep, p1)
        # ⚠️ 2026-08-14 修复：isInBed 会被游戏 tick 瞬时弹掉（1s 内回来）。
        #    只在"既不在床又无 ReadyCheckDialog"时才重试（先精确对位床格）；
        #    对话框已在→绝不能重爬，否则第二次 Sleep_Yes 破坏 ready 同步 → 夜不过。
        time.sleep(1)
        sleeping = _sleeping_now()
        if not sleeping:
            _snap_onto_bed(bx, by)
            _ai_post("/crawl_bed", {"action": "sleep", "player": who})
            _ai_post("/sleep", {"stay": True})
            time.sleep(1)
            sleeping = _sleeping_now()
        rec("ready", sleeping,
            f"sleeping_in_place, isInBed={_in_bed_now(retries=1)}, "
            f"menu={(_ai_get('/state').get('activeMenu') or {}).get('type')}")

        # 5. 等过夜 + walk-out-refresh
        def wait_night(seconds):
            before = _ai_get("/state").get("time", {})
            bk = (before.get("season"), before.get("dayOfMonth"), before.get("year"))
            deadline = time.time() + seconds
            while time.time() < deadline:
                time.sleep(2)
                t = _ai_get("/state").get("time", {})
                if (t.get("season"), t.get("dayOfMonth"), t.get("year")) != bk:
                    return True
            return False

        log_refresh = ""
        if wait_night(20):
            rec("night", True, "20s 内过夜")
            return _after_night(steps, loc, bx, by,
                                f"💤 已睡在{p1}的床上，新的一天开始了！",
                                co_sleep, p1)

        # 刷新同步：humanize=True 原地重爬重就绪；humanize=False 沿用旧版"起身→warp Farm→回床重爬"
        try:
            _ai_post("/cancel_sleep", {})
            time.sleep(1)
            if not humanize:
                _ai_post("/warp", {"location": "Farm"})
                time.sleep(2)
            _snap_onto_bed(bx, by)
            _ai_post("/crawl_bed", {"action": "sleep", "player": who})
            time.sleep(0.5)
            _ai_post("/sleep", {"stay": True})
            log_refresh = "（已原地重趴就绪）" if humanize else "（已自动走刷新：起身→出建筑→回来重爬）"
            if wait_night(45):
                rec("night", True, f"刷新后 45s 内过夜{log_refresh}")
                return _after_night(steps, loc, bx, by,
                                    f"💤 已睡在{p1}的床上，新的一天开始了！{log_refresh}",
                                    co_sleep, p1)
        except Exception as e:
            log_refresh = f"（刷新异常: {e}）"

        _ai_post("/cancel_sleep", {})
        return finish(False,
                      f"⏳ 在等{p1}确认过夜——夜未到，已起身（上床很稳定，别立即重试上床）{log_refresh}。"
                      f"等心跳/异步提示或确认{p1}就绪后再 go_sleep，或决定今晚不睡",
                      co_sleep, p1)
    except Exception as e:
        return finish(False, f"❌ 睡觉失败: {e}")


def approach_bed(who="", log=None) -> dict:
    """🛏️ 走上床去躺（复用 go_sleep 的"上床"半程，但**不调 /sleep 确认 → 不过夜、不结束一天**）。
    本地扫床：当前场景没床（非 Cabin/FarmHouse/姜岛小屋，或床不在当前场景）→ {ok:False}；
    有床 → 同图 walk 到床边 → /position 精确对位床格 → /crawl_bed sleep（只设 isInBed）。
    刻意**不走 warp/teleport 直落床格**（会被游戏 redirect 弹回门口，08-01 实测教训）。
    返回 {ok, bed, player:床主, co_sleep, snap}。要真睡→go_sleep；要起身→ai_cancel_sleep()。
    """
    detect_roles()
    log = log or (lambda *a, **k: None)
    try:
        locate = _ai_post("/crawl_bed", {"action": "locate", "player": who})
        if not locate.get("ok"):
            return {"ok": False, "error": locate.get("error", locate)}
        bed = locate["bed"]
        loc, bx, by = bed["location"], bed["x"], bed["y"]
        p2 = locate.get("player2") or "我"
        p1 = locate.get("player", p2)
        co_sleep = p1 != p2
        log("locate", True, f"目标床:{loc}({bx},{by}) 床主:{p1} ({'一起睡' if co_sleep else '自家'})")

        # 🔑 本地门禁（2026-08-22 恒：只躺当前场景的床，不跨图不 warp）
        _gate = _sleep_bed_gate(locate, loc)
        if _gate:
            return {"ok": False, "error": _gate}

        try:
            _ai_post("/walk_to", {"location": loc, "x": bx, "y": by + 1})
            for _ in range(20):  # 最多等 10s，失败交给精确对位兜底
                time.sleep(0.5)
                if not _ai_get("/state").get("player", {}).get("isMoving"):
                    break
        except Exception:
            pass
        snap = _snap_onto_bed(bx, by)
        time.sleep(0.3)
        log("reach_bed", True, f"床边对位到 {snap}")

        r = _ai_post("/crawl_bed", {"action": "sleep", "player": who})
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error", r)}
        # ⚠️ 防 isInBed 被游戏 tick 瞬时弹掉：1s 后没站稳 → 对位+重爬一次（与 go_sleep 同款防御）
        time.sleep(1)
        if not _in_bed_now(retries=1):
            _snap_onto_bed(bx, by)
            _ai_post("/crawl_bed", {"action": "sleep", "player": who})
            time.sleep(1)
        log("lie", True, f"crawled, inBed={_in_bed_now(retries=1)}, bed={r.get('bed')}")
        return {"ok": True, "bed": bed, "snap": snap, "co_sleep": co_sleep, "player": p1, "location": loc}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ai_cancel_sleep() -> dict:
    """🚶 AI(7843)进程取消睡觉：/cancel_sleep → **关掉睡觉就绪确认弹窗(ReadyCheckDialog)** + 清 isInBed。
    语义=关闭睡觉确认、解除"要睡"状态（夜不会过）。⚠️ 不会把角色从床格挪开——人站床格上 isInBed 仍会判 true。
    与 go_sleep 超时兜底用的"起身"等价，但让 AI 能主动决定"不睡了"。想彻底不躺 → 走离床格。
    （主连接 7842 的取消用上面 cancel_sleep()，这里专走 AI 连接 _ai_post。）"""
    r = _ai_post("/cancel_sleep", {})
    return r if isinstance(r, dict) else {"ok": False, "error": r}


def _after_night(steps, loc, bx, by, summary, co_sleep=False, slept_in=""):
    """过夜后验证醒来位置=睡的那张床（±2 格）。位置比对，地点名只做信息（显示名/真实名可能不同）。
    co_sleep + 在对方床醒来 → 一起睡彩蛋成功（wake_check 步骤标注 🌹）。"""
    time.sleep(2)  # 等新一天位置就位
    s = _ai_get("/state")
    pl = s.get("player", {})
    loc_now = (s.get("location") or {}).get("name", "?")
    px, py = pl.get("x", -1), pl.get("y", -1)
    near = abs(px - bx) <= 2 and abs(py - by) <= 2
    steps.append({"step": "wake_check", "ok": near,
                  "detail": f"醒来 {loc_now}({px},{py}) vs 睡床 {loc}({bx},{by})"
                            + (" 🌹一起睡彩蛋成功" if co_sleep and near else "")})
    return {"ok": True, "summary": summary, "steps": steps,
            "woke_in_expected_bed": near, "wake": {"loc": loc_now, "x": px, "y": py},
            "co_sleep": co_sleep, "slept_in": slept_in}


def cancel_sleep():
    """🚶 取消睡觉：起身 + 关闭 ReadyCheckDialog（像真人一样在别人上床前可取消睡觉）。
    走主连接(默认7842)；AI 进程取消请用 _ai_post('/cancel_sleep')。
    """
    return _post("/cancel_sleep", {})

def confirm_settlement():
    """✅ 关掉过夜结算界面（ShippingMenu）：AI 复盘/规划完，确认新一天开跑。
    打 AI 进程(7843)——结算界面是 AI farmhand 自己的。超过 3 分钟未确认会兜底自动关。
    """
    return _ai_post("/settlement_confirm", {})

def festival_status():
    """🎪 节日实况：当前是否有活动事件 + actors（/festival）。
    打 AI 进程(7843)。无节日时返回 {ok:false, error:"No active event"}。"""
    return _ai_get("/festival")

def festival_interact(name=""):
    """🎪 节日互动：瞬移到节日 NPC 身边并触发 checkAction（/festival/interact）。
    name 不传则选第一个 actor。返回 {ok, target, targetTile, playerTile, triggered}。"""
    return _ai_post("/festival/interact", {"name": name})

def festival_answer(answer=0, key=""):
    """🎪 节日应答：应答事件对话（/festival/answer）。answer 为选项编号，key 可选。"""
    return _ai_post("/festival/answer", {"answer": answer, "key": key})

def unlock_status():
    """🔓 各地点解锁状态（/unlocks）：bus/sewer/secretWoods/skullCavern/island/railroad/casino/summit/mastery/greenhouse。
    打 AI 进程(7843)。旧 DLL 无此端点会 404 → 调用方兜底放行。"""
    return _ai_get("/unlocks")

def screenshot(save_path=None):
    """📸 截取 host 进程(7842)当前画面并保存到 PNG，返回文件路径。
    save_path 省略时存到 ~/nagi/screen_check.png（供 shop_buy 等脚本验证用）。"""
    r = _get("/screenshot")
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "screenshot failed"))
    if save_path is None:
        save_path = os.path.join(os.path.expanduser("~"), "nagi", "screen_check.png")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(base64.b64decode(r["image"]))
    return save_path


def screenshot_ai():
    """📸 截取 AI 角色进程(默认7843)的当前画面，返回 base64 PNG。
    MCP screenshot 工具用这个——AI 看到的是自己角色的视角。"""
    return _ai_get("/screenshot")


def screenshot_portrait_ai():
    """🪞 截取捏人弹窗(CharacterCustomization)的小人展示区(portraitBox)为 base64 PNG。
    无捏人弹窗在开 → {"ok": false, "reason": "no_character_customization_menu"}。
    区域由游戏自己按 uiViewport 居中算 → 分辨率自适应。set_appearance 每次修改后附给 AI 看。"""
    return _ai_get("/screenshot_portrait")

def petbowl():
    """查询游戏内宠物水碗的实际坐标（不再硬编码）。
    返回: {"ok": true, "bowl": {"x": ..., "y": ...}, "pet": {...}}
    """
    return _get("/petbowl")

def position(x, y):
    """直接传送玩家到指定瓦片坐标（跳过寻路）。"""
    return _post("/position", {"x": x, "y": y})

def walk_to_coord(location, x, y):
    """使用游戏自带的 /walk_to 接口导航到目标坐标。
    自动跨地图、找有效落点。返回后需轮询等待到达。
    """
    return _post("/walk_to", {"location": location, "x": x, "y": y})

def walk_natural(target_x, target_y, timeout_scale=2, min_timeout=5):
    """⚠️ 已弃用（2026-08-13 恒拍板）：走路统一用 walk_to_coord（/walk_to）。
    这个走 /move（mod FindPath，效果几乎一样），行为保留给旧脚本（耕种/下矿等），勿改。
    返回 True=走到目标，False=用了 position 兜底。
    """
    s = state()
    px, py = s["player"]["x"], s["player"]["y"]
    steps = abs(target_x - px) + abs(target_y - py)

    if steps == 0:
        return True

    # 太远直接 position（节省时间）
    if steps > 30:
        position(target_x, target_y)
        return False

    try:
        deadline = time.time() + max(steps * timeout_scale + 1, min_timeout)
        _post("/move", {"x": target_x, "y": target_y})
        while time.time() < deadline:
            s = state()
            p = s.get("player", {})
            if not p.get("isMoving", False):
                cx, cy = p.get("x"), p.get("y")
                if cx == target_x and cy == target_y:
                    return True
                break
            time.sleep(0.1)
        # 超时或没走到 → position 兜底
        position(target_x, target_y)
        return False
    except Exception:
        position(target_x, target_y)
        return False

def petall():
    """作弊：直接标记所有宠物/动物为已摸，不用走过去。
    返回: {"ok": true, "petted": N, "details": [...]}
    """
    return _get("/petall")

def waterbowl():
    """作弊：直接标记水碗为已装满，不用真浇水。
    返回: {"ok": true, "watered": true}
    """
    return _get("/waterbowl")


def buy_animal(animal_type, name, building=""):
    """🐔 从玛妮那里购买动物，直接入住建筑。
    支持: White Chicken, Brown Chicken, Duck, Rabbit, Cow, Goat, Sheep, Pig, Ostrich, Golden Chicken

    Args:
        animal_type: 动物类型（如 "White Chicken", "Cow"）
        name: 动物名字
        building: 建筑名称（可选，不填自动选有空位的建筑）

    返回: {ok, animal_type, name, price, building, ...}
    """
    data = {"animal_type": animal_type, "name": name}
    if building:
        data["building"] = building
    return _post("/buy_animal", data)


def quest_progress():
    """📋 获取所有任务的详细进度。
    包括日常求助任务、已接常规任务、特殊订单（含每个子目标的计数）。
    返回: {ok, count, quests: [{source, title, description, ...}]}
    """
    return _get("/quest_progress")


def sprinklers():
    """💧 扫描当前地点的洒水器及其覆盖范围。
    返回: {ok, location, count, sprinklers: [{name, type, x, y, tiles: [{x,y}]}]}
    """
    return _get("/sprinklers")


# ── 2026-08-06 新增：干草/精通/木匠（需 ModEntry 重编译） ──

def silo():
    """🌾 筒仓干草检测：{ok, silos, hay, capacity, room, full, noSilo}"""
    return _get("/silo")


def mastery():
    """🏆 精通状态：反射列出 Farmer 上所有 Mastery 字段/属性的值（经验检测）。
    返回: {ok, fields: [{name, value}], props: [{name, value}]}"""
    return _get("/mastery")


def special_items():
    """💼 钱包特殊物品列表（Game1.player.specialItems，NetStringList）。
    1.6 精通(mastery_farming/mining/combat/foraging/fishing)、小镇钥匙(TownKey)等都在这。
    返回: {ok, specialItems:[...], has:{mastery_farming,TownKey,...:bool}}。
    ⚠️ 打 AI 进程(7843)——权威端/权威玩家的钱包（同 unlock_status）。"""
    return _ai_get("/special_items")


def mastery_claim(type_name=""):
    """🏆 领取精通（当前为探测版：报告候选领取方法 + 当前经验，供确认 API）。
    Args:
        type_name: 目标精通名（如 "Farming"）
    """
    return _post("/mastery_claim", {"type": type_name})


def carpenter():
    """🏗️ 木匠商店建筑清单（只读）：{ok, count, affordable, buildings:[{name, cost, size, materials, affordable}]}"""
    return _get("/carpenter")


def close_doors():
    """🚪 开关畜棚/鸡舍门 — 关所有动物建筑的门（晚上防狼用）
    早上开门已包含在 care_animals 中。
    直接调游戏内部接口开关，不走点击模拟。
    """
    return _post("/toggle_doors", {"action": "close"})


def till_area(tiles=None, x=None, y=None, length=None, direction=None, power=0):
    """🌱 批量耕地 — 直接创建 HoeDirt，跳过工具动画
    支持显式 tile 列表或基于 power 的范围模式。
    power: 0=1格, 1=3格线, 2=5格线, 3=3x3, 4=3x6

    Args:
        tiles: [{x,y}, ...] 显式坐标列表
        x, y: 起始坐标（power 模式用）
        length, direction: 自定义线长度和方向
        power: 蓄力等级
    """
    data = {"power": power}
    if tiles: data["tiles"] = tiles
    if x is not None: data["x"] = x
    if y is not None: data["y"] = y
    if length: data["length"] = length
    if direction: data["direction"] = direction
    return _post("/till_area", data)


# ⚠️ water_area 已删除（2026-08-15 恒：直接改 dirt.state 作弊，误导 AI）。浇水走 tool_area 蓄力 + 补漏。

