"""
自动钓鱼脚本：默认【就地钓】= 就在当前站位原地钓（不传送）；指定 --location → warp 去校准钓点。
→ 启动Fishbot → 监控体力/背包/时间/空抛 → 安全退出。

🔴🔴 **手工/探针跑，务必带 `--no-sleep`**（2026-10-06 我把恒的一整个早上睡掉了，他笑我"早上六点也去睡觉"）：
   本脚本**收工会 `warp FarmHouse` + `POST /sleep`**（见文末 :846-852，那是"钓完这一趟就过夜"的设计）。
   ⚠️ `/sleep` 会让游戏**受理**这次睡觉（`Sleep_Yes`）⇒ 之后光调 `/cancel_sleep` **撤不掉**
     （`isInBed` 仍为 true、人**动不了**；真机实测：`/walk_to` 报 ok 但一步不动）。
     脱离办法（实测）：`POST /warp` 到**别的图**（如 Farm）⇒ 立刻能走 ✅。
   单子那条路（`nagi_mcp_server._im_fish_go`）**写死了 `--no-sleep`** ⇒ 只有手敲才会踩。

用法:
    PYTHONIOENCODING=utf-8 python3 fish_run.py [--port 7843] --no-sleep        # 就地钓（**手工跑带这个**）
    PYTHONIOENCODING=utf-8 python3 fish_run.py --location Beach --max-casts 20  # 去某钓点

参数:
    --port          NagiBridge端口（默认 7843，AI 角色）
    --location      钓点名（Beach/Mountain/Forest/Town）。不带=就地钓（当前站位；开局一次性 isFishing 判能否抛）
    --max-casts     抛 N 竿就收手（0=不限，钓到体力<20/背包满/太晚/抛不出去停）
    --no-sleep      收工**不回家不过夜**（手工跑/探针必带；全自动循环才不带）
"""

import os
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
        # 🏝️ 2026-10-06 恒给：姜岛南岸海洋（该图 `/fish_areas` count=0 ⇒ 走水格扫描那档）
        "IslandSouth": ("姜岛南岸海钓点", 2),
        # 🏴‍☠️ 2026-10-06 真机：单子按下去自己走到 (6,8) 朝右、**真钓上一条**（`fishCaught` +1）。
        #    ⚠️ 该图 `/fish_areas` `count=0`（没有鱼区）⇒ 归**按图这张表**。
        "IslandSouthEastCave": ("海盗湾内钓点", 1),
}
# 🎣 **按鱼区**的校准钓点（比"按图"更细一层；2026-10-05 加，给「快捷钓鱼上单」的第二层用）。
#    键 = (**图名**, **鱼区 id**)；鱼区 id 就是 `/fish_areas` 的 `areas[].id`
#         （= 游戏 `Data/Locations` 的 `FishAreas` 字典 key，见 CHANGELOG 203z补31）。
#    值 = (POI 名, face)；face 口径 0上 1右 2下 3左（`ModEntry.cs:2406-2409`，= 站格望向水格的方向）。
#
#    ⚠️ **优先于按图那张 `FISHING_TARGETS`**：一张图有两个水域时，按图那张只能说一个点，
#       按区这张才分得清（Forest 就是这档：`Lake` 是猪车旁小池塘、`River` 是那条河）。
#    ⚠️ 只在**有据可依**时才加行（恒亲站 / POI note 自证）；没据的就**空着** ——
#       消费侧（`_im_fish`）会退回"游戏自己算的 spots"，**绝不编一个坐标**。
#    ⚠️ POI 名必须**在 `locations.POI` 里唯一**（同名 key 后写的赢，静默盖掉前面那条 ——
#       2026-10-05 真踩过：`森林河边钓点` 被一条 (70,95)「❌待校准」盖掉了恒亲站的 (20,76)）。
FISHING_AREA_TARGETS = {
    # 恒 2026-10-05 **亲站**：`/state`= Forest (20,76) facing=1，东邻 (21,76) 是河
    # ⇒ 面东抛向河面（POI `森林河边钓点` = `locations.py:191`）；恒原话「**这位置还能钓冬季鱼王**」。
    ("Forest", "River"):   ("森林河边钓点", 1),
    # (34,25) 面下 —— 就是原按图 `FISHING_TARGETS["Forest"]` 那一份，**保持等价**（不是新点）。
    ("Forest", "Lake"):    ("森林小池塘钓点", 2),
    # (9,10) 面下钓 (9,11) 水面 —— POI note 自证（`locations.py:369` + `POI_FACE`；`沙漠钓鱼点`）。
    # ⚠️ `Desert.TopPond` 的 `position` 是 **null**（兜底区）⇒ 按图那条路的"矩形包含"判据用不上，
    #    这张显式表正是为它准备的。
    ("Desert", "TopPond"): ("沙漠钓鱼点", 2),
    # 🏝️ 2026-10-06 恒给：姜岛**农场旁那个湖**（`/fish_areas` 的 `Freshwater` 区，`fishable` 全 false
    #    ⇒ 正好是"鱼区那档挑位口径挑错了"的又一例 ⇒ 走校准点）。
    ("IslandWest", "Freshwater"): ("姜岛农场湖钓点", 3),
}
# ⚡ 收手的体力线（**绝对值**，不是百分比 —— 星之果实会把 maxStamina 拉高、百分比会误判）。
# ⚠️ **只此一处定义**：`stamina_common.MIN_STAMINA`（锄地/浇水/播种/开钓闸门全用它）。
#    这里 re-export，别在本文件里再写一个字面量 20。
from stamina_common import MIN_STAMINA   # noqa: F401

FISHING_SPOTS = {
    "Beach": {"x": 42, "y": 36, "face": 2},
    "Mountain": {"x": 69, "y": 14, "face": 2},
    "Forest": {"x": 69, "y": 28, "face": 2},
}

# 📏 抛竿**落点距离**的实测账（2026-10-06 恒：「这个落点也得自动算，前期会有变化」）。
#    为什么必须实测 + 现算、**绝不写死 7**：
#      · 距离 = `max(128, 蓄力 × (等级加成+4) × 64)` px（`FishingRod.cs:1950` 左右 / `:1976` 上下）
#      · 等级加成 `getAddedDistance`（`:357`）≥15⇒4 / **≥8⇒3** / ≥4⇒2 / ≥1⇒1 / 否则 0
#        ⇒ 等级每跨一档，距离就变（**前期一直在变**）
#      · 蓄力由 Fishbot 的 `CastDistance` 决定（实测顶到 1.0）—— 他改了配置，距离也变
#    ⇒ 所以每次抛竿都把「这一竿的落点距离 + 当时的蓄力 + 等级」写进下面这个文件；
#      消费侧（`nagi_mcp_server._fish_cast_d`）**实测优先**、没有实测才按公式＋实测蓄力现算。
CAST_DIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fish_cast_dist.json")


def save_cast_obs(axis, dist, power, level):
    """把一竿的实测落点距离存进 `_fish_cast_dist.json`（按**等级**和**轴向**分开记）。

    ⚠️ 轴向由**观测到的位移**判（`h` = 左右向、`v` = 上下向），⛔ 不靠"当时朝哪"——朝向会变，
       而落点位移不会骗人（真机：(29,23) 面左 → 落点 (22,24)，|dx|=7>|dy|=1 ⇒ h）。
    ⚠️ 一个等级要**两个轴向**都量过才算齐（横向 `+4`、纵向 `+3`，公式不一样）。
    ⚠️ 写文件失败**绝不影响钓鱼**（全 try 包住）。返回存下来之后的那个 dict（给日志用）。
    """
    if axis not in ("h", "v"):
        return {}
    try:
        data = {}
        try:
            with open(CAST_DIST_FILE, encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
        obs = dict(data.get("obs") or {})
        lv = str(int(level if level is not None else -1))
        cur = dict(obs.get(lv) or {})
        cur[axis] = int(dist)
        obs[lv] = cur
        if isinstance(power, (int, float)) and power > 0:
            data["power"] = round(float(power), 3)
        data["obs"] = obs
        data["last_level"] = int(level) if level is not None else None
        data["last_axis"] = axis
        data["last_d"] = int(dist)
        data["ts"] = time.time()
        with open(CAST_DIST_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return data
    except Exception:
        return {}


def get_spot(location, area_id=None):
    """解析钓点：**先按 (图名, 鱼区 id) 查**（`area_id` 非空时）→ 再按图查校准 POI → 最后原版 `FISHING_SPOTS` 兜底。

    ⚠️ **老调用点（不传 `area_id`）走的路一个字都没变**：`fish_run` 自己的 `--location` 那条照旧按图解析。
    ⚠️ 传了 `area_id` 但**那张表里没有这一条** ⇒ **退回按图那张**（不是"没找到就放弃"）。
       要区分「命中的是按区表还是按图表」的调用方**自己查 `FISHING_AREA_TARGETS`**
       （`nagi_mcp_server._fish_calibrated_poi` 就是这么做的：按图表那条必须再做"矩形包含"验证）。
    """
    from locations import POI
    if area_id:
        hit = FISHING_AREA_TARGETS.get((location, area_id))
        if hit:
            poi = POI.get(hit[0])
            if poi:
                return {"x": poi["pos"][0], "y": poi["pos"][1], "face": hit[1], "poi": hit[0]}
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


def caught_summary(fc_start, fc_end, edge_count) -> str:
    """🎣 收工那行的条数文案 —— **优先用游戏自己的累计计数**，读不到才退回估算。

    恒 2026-09-24：「数量好像还不准，**都是说钓了 0 条**。只报共抛了几竿钓了几条就好了。」
      · `fc_start/fc_end` = `Stats.FishCaught`（**只增不减**的游戏计数）开头/收工两次读数
        ⇒ 差值 = 这一趟**真钓上几条**（脱钩不算、采样也不漏，比数 `isReeling` 边沿准得多）。
      · 读不到（老 DLL 没这字段 ⇒ -1）才退回边沿计数，并**标明"估算"** ——
        **别把估的报成准的**（"报成功但事没发生"的同族：数字也会骗人）。
    """
    if isinstance(fc_start, int) and fc_start >= 0 and isinstance(fc_end, int) and fc_end >= 0:
        return f"钓上{max(0, fc_end - fc_start)}条"
    return f"钓上{edge_count}条（估算）"


def stow_rod(bot):
    """🎣 把鱼竿**从手上收起来**（换拿别的工具），别攥着竿走路。

    ⚠️ 2026-09-17 恒：「**脚本结束记得收杆啊，或者你没结束就执行下一条了，拉着竿跑老远**」。
    这里说的**不是"收线"**——`finish_cast` 的 cancel 已经把线收了；是**竿还拿在手里**：
    脚本一收工，AI 下一个动作常常就是 `map go` 走去别处，于是画面上成了"**拖着竿满地图跑**"。
    同一个毛病 **2026-08-14 恒就点过**（原话"鱼竿在路上就装备会'边走边钓'的样子"），
    所以脚本**开头**早有一段"走路前先收起鱼竿"（`for putaway in ("Pickaxe","Axe",...)`）——
    收工这半边对称的逻辑当时**漏了**，这次补上。
    换的是镐/斧/锄/镰刀这类**背包里一定有**的工具；一个都换不了就作罢（不报错、不阻断收工）。
    """
    for putaway in ("Pickaxe", "Axe", "Hoe", "Scythe"):
        try:
            r = bot.select(putaway)
            if isinstance(r, dict) and r.get("ok"):
                return True
        except Exception:
            pass
    return False


# 竿的名字里一定有这两个词（Bamboo Pole / Training·Fiberglass·Iridium Rod）——
#   `/select` 是按名字精确然后 Contains 兜底匹配的，所以拿回来那句也照这个认，不写死单一名字。
_ROD_WORDS = ("rod", "pole")


def hand_kind(p, rod_names) -> str:
    """🖐️ 手上拿的是什么：`rod` 竿 / `tool` 别的工具 / `item` 物品 / `empty` 空手。

    ⚠️ **不能只看 `currentTool`**（这是本函数存在的唯一理由）：`/state` 里
       `currentTool = farmer.CurrentTool?.Name` —— `Farmer.CurrentTool` 是
       `Items[CurrentToolIndex] as Tool`，**拿着物品和空手时都返回 null**。
       只读它就分不清"空手"（吃完东西那格空了）和"正拿着一个要吃的"，
       而这两种要**反向处理**：前者补竿，后者千万别动。
    """
    ct = str((p or {}).get("currentTool") or "")
    ci = str((p or {}).get("currentItem") or "")
    low = f"{ct} {ci}".lower()
    if any(r.lower() in low for r in rod_names) or any(w in low for w in _ROD_WORDS):
        return "rod"
    if ct:
        return "tool"
    if ci:
        return "item"
    return "empty"


def ensure_rod(bot, p, rod_names, has_menu=False) -> str:
    """🎣 **手上没竿就装回去**；返回 `"ok:<竿名>"` / `"missing"` / `""`（本来就不用动）。

    ⚠️ 2026-09-25 恒真机：「钓鱼的 continue 好像没有切换回手持工具导致钓鱼不成功」。
       病根不是 continue，是**整条路上没人再看手上的东西**：脚本只在开局 `select` 一次竿，
       之后 AI 在异步窗口里干的事会把它挤掉 —— 最典型的是 `daily eat`（`/eat` 吃的就是
       **手持那一格**，吃完那格空了 ⇒ `CurrentTool` 变 null ⇒ **鱼机抛不出去**），
       而脚本照样一句一句打 `check: …`，看上去跟正常没两样（恒是从**画面上**看出来的）。
    ⚠️ 三种手上状态**分开处理**（`hand_kind` 的 docstring 有为什么）：
       拿别的工具/空手 → 抢回来；**拿着物品 → 一动不动**（那多半是 AI 正要把这东西吃掉）。
    ⚠️ 小游戏（BobberBar）开着时 `has_menu=True` → 不动：那是游戏自己的菜单，别在这时候改手持。
    """
    if has_menu:
        return ""
    if hand_kind(p, rod_names) not in ("tool", "empty"):
        return ""
    for rn in rod_names:
        try:
            if (bot.select(rn) or {}).get("ok"):
                return f"ok:{rn}"
        except Exception:
            pass
    return "missing"


def cast_settled(bot):
    """(线还在不在水里, 现在开着什么菜单) —— 收手判据的**唯一来源**（别两处各读一份、慢慢长歪）。

    ⚠️ 为什么把菜单一起读出来（2026-09-24 恒真机）：「鱼没上来，**拿到鱼的一瞬间脚本退了**」——
       背包满(12/12)时鱼**不是直接进背包**，是先弹「满包待领」菜单（鱼在待领槽里等你丢一件换它）。
       老收手路径只盯 `fishing` 三个布尔，线一落空就往下走 `stow_rod`+退出 ⇒ **那个菜单没人管**，
       鱼就卡死在待领槽（/state 里 `activeMenu` 一转眼也没了，鱼也就没了）。
    """
    st = bot.state()
    f = (st.get("player") or {}).get("fishing") or {}
    in_water = bool(f.get("isFishing") or f.get("isCasting") or f.get("isReeling"))
    menu = ((st.get("activeMenu") or {}).get("type") or "")
    return in_water, menu


def let_cast_finish(bot, timeout_s=45.0):
    """🎣 **让手里这一竿钓完**，别半路把线拽回来。返回 True=这一竿收束了 / False=等超时了。

    ⚠️ 2026-09-24 恒：「原来你的抛竿真的只是抛竿，**抛了却不钓完鱼，就跟异常停止一样**」——
       老版一数到第 N 竿就 `finish_cast`（= `fishbot off` + 2s + cancel×3），
       而 `cast_count` 是"进入钓鱼态"的**边沿** ⇒ 那一下正是**第 N 竿刚甩出去**：
       鱼还没咬/刚咬/正在小游戏里，线被硬拽回来，这条白抛 —— 看起来跟脚本崩了没两样。

    做法：**鱼机全程开着**（小游戏要靠它自动玩），只等这一竿收束
    （`isFishing/isCasting/isReeling` 全落回 False = 钓上来了 / 这竿没鱼）。
    ⚠️ 调用方**必须先等完再** `fishbot off`；顺序反了等于白改。

    ⚠️ 已知让步：万一鱼机在我们关掉它之前又极快地甩出一竿，那一竿会**一起钓完**才收
       （宁可多钓一条，也不要半路拽线）。0.4s 采样已经很密，这个窗口很小。
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            in_water, _m = cast_settled(bot)
        except Exception:
            return False                      # 状态读不到 → 不硬猜，交给调用方兜底收
        if not in_water:
            # ⏳ **再给 0.8s 落定**：线一落空的那一帧，满包待领菜单往往还没弹出来。
            #    抢在它前面往下走 = 菜单刚弹就没人管了（恒看到的"拿到鱼的一瞬间脚本退了"）。
            time.sleep(0.8)
            return True
        time.sleep(0.4)
    return False


def stop_after_cast(bot, reason):
    """🎬 自然收手的**统一出口**：这一竿已经钓完了，现在该怎么停。

    ⚠️ 2026-09-24 恒真机：「鱼没上来，**拿到鱼的一瞬间脚本退了**」——背包满(12/12)时，
       鱼是先进「满包待领」菜单的（在待领槽里等你丢一件换它）。这条路径**绝不能**再往下走
       `finish_cast` 那套（按 cancel / 换手持工具）：菜单开着时乱动 = 替 AI 瞎做主，
       而那条鱼正等人决定丢哪个。做法 = 沿用 monitor loop 里**早就有**的那条政策：
       **停脚本，把菜单原样留给 AI**，并说清鱼可能还在待领槽。
    """
    try:
        _, menu = cast_settled(bot)
    except Exception:
        menu = ""
    if menu and menu != "BobberBar":
        try:
            bot.fishbot("off")          # 自动抛竿必须停（否则它还会接着甩）
        except Exception:
            pass
        log(f"  🎒 收手时弹着「{menu}」——**一样都不动**（满包时刚钓上来的鱼就在待领槽里），"
            f"交给 AI：menu read 看内容 → 想领就 menu click(action=discard item=某件低价值物) 腾格后领，"
            f"不想领直接关掉（放弃这条）。⚠️ 处理完记得**把竿换下来**（scene select 换件工具）"
            f"——收手走这条时鱼竿还在手上（菜单态不让动），别拖着竿走路")
        return
    finish_cast(bot, reason)


def finish_cast(bot, reason):
    """🎣 停止条件触发：关鱼机自动抛(防停不掉/再抛下一竿) + cancel 收线(即时停，小游戏中也可退出)。
    竿抛着没咬(isFishing/isCasting)→ cancel 收线(实时)；正收线(isReeling)→ 短等放行当前竿(钓上就钓上，
    没钓上也无妨——能即时停就是好事，恒 2026-09-05 确认)。不再问 AI 等一杆钓完/别操作。"""
    try:
        bot.fishbot("off")   # 停自动抛竿（≠收杆，线留在原地）——"钓完这竿就暂停、不抛下一竿"的关键
    except Exception:
        pass
    time.sleep(0.5)   # 给 fishbot off 落一拍（"钓完这一竿"已由 let_cast_finish 负责，不在这儿干等）
    try:
        for _ in range(3):
            # ⚠️ 2026-09-24：**先看线还在不在**，再决定按不按 —— `cancel` 就是"使用工具"键，
            #    空按一下 = **又甩一竿出去**（收工时抛一竿，恒 08-14 骂的就是这个形状）。
            #    跑完那条自然收尾的循环本来就有这一步判断，finish_cast 里一直漏着。
            f = (bot.state().get("player") or {}).get("fishing") or {}
            if not (f.get("isFishing") or f.get("isReeling") or f.get("isCasting")):
                break
            try:
                bot.key("cancel")   # 竿还悬着/还在收 → 收线（实时终止，别让竿悬空/声效残留）
            except Exception:
                pass
            time.sleep(0.3)
    except Exception:
        pass
    # 🎣 线收了，还要把**竿从手上收起来**——否则 AI 收工后一走路就是"拖着竿跑"（恒 2026-09-17 当场看见）
    if stow_rod(bot):
        log(f"⏸ 已停钓（{reason}，鱼机已关、竿已收、**竿已收起换手**）")
    else:
        log(f"⏸ 已停钓（{reason}，鱼机已关、竿已收）⚠️ 手上还拿着鱼竿（背包里没别的工具可换）")


def run(port, location, max_casts=0, no_sleep=False):
    # ⚠️ 2026-09-24 删掉 `stamina_pct` 参数（原来叫 `--stamina-pct`，文档写"体力低于此百分比停止(默认15)"）：
    #    **它是个摆设** —— 只有一条日志用 `bot.stamina_pct()` 这个方法（同名不同物），
    #    真正管收手的判据是监控循环里那句**绝对值** `current_stamina < 20`（不走百分比，
    #    因为星之果实会把上限拉高、百分比会误判）。文档说 A、代码做 B ⇒ 删参数、文档改成实话。
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
    # 优先级（2026-09-25 问过恒）：铱金 ＞ 玻璃纤维 ＞ **训练竿** ＞ 竹竿。
    #   ⚠️ **训练竿排在竹竿前面是有意的**，恒：「真有人去买训练竿，那应该确实是不想使用默认赠送的竹竿」
    #      —— 买了就是偏好它（竿更稳），不是"比竹竿差"。别按等级直觉把它挪到竹竿后面。
    #   ⚠️ 表里**没有** `Advanced Iridium Rod`（Lv.4，1.6 精通奖励）：背包里只有它时
    #      `/select "Iridium Rod"` 靠 Contains 兜底能选中；**两根都在**时会先命中普通铱金竿 ⇒ 挑走差的那根。
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
    rod_name = ""
    for name in rod_names:
        r = bot.select(name)
        if r.get("ok"):
            log(f"selected: {name}")
            rod_found = True
            rod_name = name
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
    # 🎣 收工报数的**基准**（恒 2026-09-24：「数量好像还不准」）：游戏自己的累计计数
    #    `Stats.FishCaught`（只增不减）—— 收工时再读一次，**差值 = 这一趟真钓上几条**。
    #    -1 = 读不到（老 DLL 没这字段）⇒ 收工时退回边沿计数并**标明是估算**。
    _fc0 = p.get("fishCaught")
    _fc0 = _fc0 if isinstance(_fc0, int) else -1

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
    # ⚠️ 2026-09-24 真机（同一个命令、同一个钓点，一次成一次败）：**1 秒采样会漏**。
    #    `isFishing` 在**垃圾**（海草/垃圾这类没小游戏的）身上只亮零点几秒 —— 抛出去→立马"上钩"→收回来，
    #    1 秒一采正好整段跳过 ⇒ 判成"抛竿方向没有水"直接收手，**而它其实刚钓上来一条海草**
    #    （脚本自己的日志里连个"抛过竿"的字都没有，只有那句冤枉的"没有水"）。
    #    ⇒ 两处加固：采样 1s→0.3s；**并加一条独立判据：背包少了格 = 这一竿确实进了水**（垃圾也算）。
    _space0 = None
    try:
        _space0 = bot.inventory_space()
    except Exception:
        pass
    _ok = False
    _hard_end = time.time() + STARTUP_WAIT + 20
    _deadline = time.time() + STARTUP_WAIT
    _menu_hits = 0
    _menu_types = []
    _obs = {}                      # 📏 观测到的最远鱼漂落点（量"这孩子能扔多远"，见 :1950 那段注释）
    _maxpow = 0.0                  # 这一趟见到的**最大蓄力**（= Fishbot 的 CastAmount；量距离要连它一起记）
    while time.time() < _deadline and time.time() < _hard_end:
        time.sleep(0.3)
        _st = bot.state()
        _mt = (_st.get("activeMenu") or {}).get("type")
        if _mt and _mt != "BobberBar":
            log(f"  🚧 启动期被「{_mt}」挡住 → 关掉再等（这段不算数）")
            bot.close_menu()
            _menu_hits += 1
            _menu_types.append(_mt)
            # 🔁 **重新给一整段"无菜单"窗口**（2026-10-05 真机：原来只 `+= 1.5`，可关掉菜单之后
            #    Fishbot 要再抛一次、鱼漂飞到落点，实测要 2~3s ⇒ 1.5s 不够，窗口被菜单吃光后
            #    照样落到下面那句**假诊断**上）。硬上限仍是 `_hard_end`（防反复弹拖成死循环）。
            _deadline = max(_deadline, time.time() + STARTUP_WAIT)
            continue
        _p0 = _st.get("player") or {}
        _f = _p0.get("fishing") or {}
        # 📏 **量落点**（2026-10-05 补36）：`bobber` 在抛竿那一刻就是"满距离落点"（`FishingRod.cs:1958/1994`）
        #    ⇒ 抛成功与否都读得到。取**最远的那次**（同一趟可能抛了两竿），把距离/蓄力/等级/离岸一起记下。
        try:
            _bx, _by = _f.get("bobberX"), _f.get("bobberY")
            _pw = _f.get("castingPower")
            if isinstance(_pw, (int, float)) and _pw > _maxpow:
                _maxpow = float(_pw)          # 蓄力会从 0 爬到 1（实测顶到 1.0）⇒ 记峰值
            if isinstance(_bx, int) and isinstance(_by, int):
                _ddx, _ddy = _bx - int(_p0.get("x", 0)), _by - int(_p0.get("y", 0))
                _d = max(abs(_ddx), abs(_ddy))
                if _d > _obs.get("d", -1):
                    _obs = {"d": _d, "x": _bx, "y": _by,
                            "axis": "h" if abs(_ddx) >= abs(_ddy) else "v",
                            "power": _f.get("castingPower"),
                            "level": _f.get("fishingLevel"),
                            "clear": _f.get("clearWaterDistance")}
        except Exception:
            pass
        if _f.get("isFishing"):
            _ok = True
            break
        # 🔎 独立判据（不依赖采样抓得准不准）：**背包少了格 = 这一竿钓上东西了 = 水绝对没问题**。
        if _space0 is not None:
            try:
                if bot.inventory_space() < _space0:
                    log(f"  🎣 背包已少格（{_space0}→{bot.inventory_space()}）= 这一竿进水了 → 能抛")
                    _ok = True
                    break
            except Exception:
                pass
    if not _ok:
        # 🎯 这里判的只是 **`isFishing` 有没有建立**，而"没建立"**不等于**"方向没水" ——
        #    上面 :491 那段注释早就写过一次，2026-10-05 真机**又栽在同一个坑**：
        #    Fishbot 自动补饵弹的 `GameMenu` 把 5 秒启动窗口吃光 ⇒ 报「抛竿方向没有水」，
        #    可人站的格、朝向、面前那格水**全是对的**（探针坐实：同一格水从侧面抛 2.0 秒就成）
        #    ⇒ 这就是一条**假诊断**（会把人支去调站位/朝向，越调越远）。
        #    ⚠️ 2026-10-05 **第二轮真机又教了一次**：那次 `GameMenu` 其实是"抛失败**之后**" Fishbot
        #       弹的（探针里 `isCasting` 先真起来、随后菜单才出现）⇒ **见到菜单 ≠ 菜单是原因**。
        #       ⇒ 所以现在**两条事实都报**（见过几次界面 ＋ 游戏自己怎么判那格水），并且
        #          **判不了就明说判不了** —— 不再用一个"多半是…"去顶替证据。
        _facts = (f"启动期 Fishbot 弹了 {_menu_hits} 次界面"
                  f"（{'/'.join(dict.fromkeys(_menu_types))}），已关掉"
                  if _menu_hits else "启动期没见到界面")
        _land_txt = ""
        if _obs:
            # 📏 有实测落点就先说它（**最硬的一条**：游戏自己把鱼漂放那儿了）
            try:
                _lw = bot._get(f"/water?x={_obs['x']}&y={_obs['y']}&radius=1").get("water") or []
                _lh = [t for t in _lw if isinstance(t, dict)
                       and t.get("x") == _obs["x"] and t.get("y") == _obs["y"]]
                if not _lh:
                    _lwhy = "**不是水格** ⇒ 这一竿就是抛空/抛过头了"
                elif "fishable" not in _lh[0]:
                    _lwhy = "是水（`fishable` 这一位老 DLL 没有 ⇒ 判不了能不能钓）"
                elif _lh[0].get("fishable"):
                    _lwhy = "是水、游戏也说**能钓** ⇒ 那就不是落点的问题（查背包满没满/界面）"
                else:
                    _lwhy = "是水、但**游戏判它钓不了**（`isTileFishable=false`）"
            except Exception:
                _lwhy = "（那一格没问成）"
            _land_txt = (f"📏 **实测落点** ({_obs['x']},{_obs['y']}) = 距站格 **{_obs['d']} 格**"
                         f"（蓄力 {_obs.get('power')} · 等级 {_obs.get('level')} · 离岸 {_obs.get('clear')}）："
                         f"{_lwhy}\n   ")
        try:
            _p = bot.state().get("player") or {}
            _fx, _fy = int(_p.get("x")), int(_p.get("y"))
            _fd = int(_p.get("facingDirection"))
            _dx, _dy = (0, 1, 0, -1)[_fd], (-1, 0, 1, 0)[_fd]
            # 朝这条线看 10 格：水/岸（有 `fishable` 就带上"钓不了"）——**事实**，不猜原因
            _w = bot._get(f"/water?x={_fx}&y={_fy}&radius=10").get("water") or []
            _by = {(t.get("x"), t.get("y")): t for t in _w if isinstance(t, dict)}
            _has_flag = any("fishable" in t for t in _by.values())
            _ray = []
            for _i in range(1, 11):
                _it = _by.get((_fx + _dx * _i, _fy + _dy * _i))
                if _it is None:
                    _ray.append(f"{_i}:岸")
                elif _has_flag and _it.get("fishable") is not True:
                    _ray.append(f"{_i}:水(钓不了)")
                else:
                    _ray.append(f"{_i}:水")
            _verdict = (f"朝{['上', '右', '下', '左'][_fd]}这条线（第1格起）：{' '.join(_ray)}\n"
                        f"   ⚠️ **鱼漂不是落在旁边那一格**：落点 = 站格 + 朝向 × 抛竿距离，"
                        f"距离 = `max(128, 蓄力 × (等级加成+4) × 64)` px（`FishingRod.cs:1950`，上下向 +3）"
                        f"⇒ **10 级（加成+3）满蓄力左右≈7 格、上下≈6 格，下限也有 2 格**。"
                        f"落点那格不是「水」就是抛过头/不够 —— 换离水更远或更近的站格，"
                        f"或把 Fishbot 的 `CastDistance` 调小（窄河/小池塘尤其要）。")
        except Exception:
            _verdict = "面前这条线**没问成**（读不到）⇒ 这一句断不了。"
        log(f"🚫 没能抛出竿（`isFishing` 未建立 —— 这**不等于**「方向没有水」）：{_facts}。\n"
            f"   {_land_txt}{_verdict}")
        if _obs:
            # 📏 抛失败也把落点量下来（这正是最需要它的场合：**落点那格不是水**才失败的）
            _sv = save_cast_obs(_obs.get("axis"), _obs["d"], _maxpow,
                                _obs.get("level"))
            log(f"  📏 已记账：落点距离 {_obs['d']} 格（朝向 {_p.get('facingDirection')} · "
                f"蓄力 {_maxpow} · 等级 {_obs.get('level')}）→ `_fish_cast_dist.json`"
                + (f"（该等级实测：{_sv.get('obs', {}).get(str(_obs.get('level')))}）" if _sv else
                   "（写文件失败，不影响这次收手）"))
        bot.fishbot("off")
        # 收杆兜底：鱼漂若在空中/甩着，按 cancel 收回
        for _ in range(4):
            _f = (bot.state().get("player") or {}).get("fishing") or {}
            if not (_f.get("isFishing") or _f.get("isReeling") or _f.get("isCasting")):
                break
            bot.key("cancel")
            time.sleep(0.8)
        stow_rod(bot)   # 收工时同样把竿从手上收起（别攥着竿走）
        return
    log("🎯 能抛，开始钓（水域固定，之后无需再判死水）")
    if _obs:
        # 📏 成功的这一竿也把距离记下来 —— 这组数就是"按落点挑钓点"要用的**实测**距离
        #    （⛔ 别拿公式反推：公式只是解释，实测才是据；两者不一致时以实测为准并回头查公式）
        log(f"  📏 本竿落点 ({_obs['x']},{_obs['y']}) = 距站格 **{_obs['d']} 格**"
            f"（蓄力 {_obs.get('power')} · 等级 {_obs.get('level')} · 离岸 {_obs.get('clear')}）")
        _sv = save_cast_obs(_obs.get("axis"), _obs["d"], _maxpow, _obs.get("level"))
        if _sv:
            log(f"  📏 已记账 → `_fish_cast_dist.json`：蓄力={_sv.get('power')} "
                f"各等级实测={_sv.get('obs')}")

    # monitor loop（max_casts=0 不限竿数 → 钓到体力<20 / 背包满 / 太晚才停）
    # 2026-09-01 恒拍板：0.6.1 fishbot 自动玩，靠"体力降/8"估抛竿的旧法（每轮降幅<8→恒0）已废 → 计数恒 0。
    #   改按**状态边沿**数：not钓鱼→钓鱼 = 抛一竿；not reel→reel ≈ 钓上一条（fishbot 自动玩必胜）。
    #   采样 3s→2s 提分辨率；极快竿漏掉可接受（不影响 max_casts 大致收手）。标签诚实化：抛N竿·钓上M条。
    cast_count = 0       # 抛竿数（边沿：not钓鱼 → 钓鱼）
    fish_caught = 0      # 钓上数（近似：not reel → reel）
    check_counter = 0
    prev_fishing = False
    prev_reeling = False
    _rod_gone_logged = False   # "竿不在手上也不在包里"只喊一次（别每 2 秒刷一行）

    while True:
        time.sleep(2)

        s = bot.state()
        p = s["player"]
        current_stamina = p["stamina"]
        fishing = (p.get("fishing") or {})
        is_fishing = bool(fishing.get("isFishing") or fishing.get("isCasting") or fishing.get("isReeling"))
        # 🎣 2026-09-24 恒：「数量好像还不准，**都是说钓了 0 条**」——根因是只认 `isReeling`：
        #    在双开 + 鱼机自动玩这套下它的窗口极小，2 秒采样基本抓不到（真机实测抛 9 竿只抓到 2 次边沿）。
        #    钓鱼小游戏（`BobberBar` 菜单）**开着就是正在收线** —— 那是游戏自己弹的菜单，稳得多
        #    ⇒ 两个信号**取并集**（边沿判据不变，只是不再漏）。状态条的 `📋 菜单打开: BobberBar`
        #    早就用同一件事，不是新判据。
        #    ⚠️ 仍是"**收线次数**"、不是"进包的鱼"：鱼脱钩也算一次。要精确条数得加
        #    `player.stats.FishCaught`（C# 一行，攒下次关游戏的批次）。
        is_reeling = bool(fishing.get("isReeling")) or \
            ((s.get("activeMenu") or {}).get("type") == "BobberBar")

        # 🎣 **手上没竿就装回去**（理由见 `ensure_rod` 的 docstring；判据用 `s` 里已有的字段，
        #    不多打一次 HTTP）。竿被存进箱子/丢掉时 `str` 只会成功一次，别每 2 秒刷一行。
        _rr = ensure_rod(bot, p, rod_names, bool((s.get("activeMenu") or {}).get("type")))
        if _rr.startswith("ok:"):
            log(f"  🎣 手上没竿 → 重新装上 {_rr[3:]}")
        elif _rr == "missing" and not _rod_gone_logged:
            _rod_gone_logged = True
            log("  ⚠️ 手上没竿、背包里也找不到竿 —— 这一趟钓不成了"
                "（竿被 `storage store` 存进箱子了？取回来再 `fish go`）")

        # 抛竿/钓上：进入对应状态的一次边沿
        if is_fishing and not prev_fishing:
            cast_count += 1
        if is_reeling and not prev_reeling:
            fish_caught += 1
        prev_fishing = is_fishing
        prev_reeling = is_reeling

        if max_casts > 0 and cast_count >= max_casts:
            # 🎣 2026-09-24 恒：数到第 N 竿时**这一竿还在水里**（`cast_count` 是"进入钓鱼态"的边沿）
            #    ⇒ 先让它钓完，再收工。老版直接 finish_cast = 把刚甩出去的线拽回来（"抛了却不钓完鱼"）。
            _done = let_cast_finish(bot)
            stop_after_cast(bot, f"数到第 {max_casts} 竿"
                            + ("，这一竿也钓完了" if _done else "，等这一竿收束超时（硬收）"))
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
                            "action=claim item=鱼名 领取；不想要就 menu click(button=ok) 直接退出放弃这条鱼；处理完再跑 fish_run。"
                            "⚠️ 记得**把竿换下来**（scene select 换件工具）——这条路不收竿，别拖着竿走路")
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

            # check stamina（绝对值 MIN_STAMINA，百分比不合理因为星之果实会拉高上限）
            if current_stamina < MIN_STAMINA:
                # 同样的道理（2026-09-24）：手上有鱼就钓完再走，别拽线（这一竿的能量早花掉了）
                _done = let_cast_finish(bot)
                stop_after_cast(bot, f"体力不足 ({current_stamina}/{p['maxStamina']})"
                                + ("，这一竿也钓完了" if _done else ""))
                break

            # check time
            game_time = s.get("time", {}).get("timeOfDay", 600)
            if game_time >= 2300:
                _done = let_cast_finish(bot)
                stop_after_cast(bot, f"太晚 ({game_time})" + ("，这一竿也钓完了" if _done else ""))
                break

            sta_pct = bot.stamina_pct()
            log(f"  check: stamina {sta_pct:.0f}%, time {game_time}, 抛~{cast_count}竿 · 钓上~{fish_caught}条")

    # stop fishbot
    bot.fishbot("off")
    # 🎣 收工那行（恒：「**只报共抛了几竿钓了几条就好了**」）：
    #    条数**优先用游戏自己的累计计数**（`fishCaught` 差值，精确：脱钩不算、采样也不漏）；
    #    读不到才退回边沿计数，且**标明"估算"**——别把估的报成准的。
    _fcz = None
    try:
        _fcz = (bot.state().get("player") or {}).get("fishCaught")
    except Exception:
        pass
    log(f"fishbot off, 抛{cast_count}竿 · {caught_summary(_fc0, _fcz, fish_caught)}")

    # 🎣 收杆：鱼线还甩着（isFishing/isReeling/isCasting）就按 cancel(=use-tool) 收线，
    # 避免直接传送后"嘎啦嘎啦"收线音效一直残留。
    for _ in range(8):
        f = (bot.state().get("player") or {}).get("fishing") or {}
        if not (f.get("isFishing") or f.get("isReeling") or f.get("isCasting")):
            break
        bot.key("cancel")
        time.sleep(0.8)
    log("收杆完成（鱼线已收回）")

    # 🚫 2026-10-06 恒：「**这个钓鱼里的 sleep，只有坏处没有好处**……可以把它去掉吗」⇒ **删了**。
    #    原来这里收工会 `warp FarmHouse` + `POST /sleep`（"钓完这一趟就过夜"），实测两宗罪：
    #      ① **脚本替 AI/人决定结束这一天** —— 那天早上 6 点就把轮回睡掉了（恒："早上六点也去睡觉"）；
    #      ② `/sleep` 会让游戏**受理**这次睡觉（`Sleep_Yes`）⇒ 此后 `/cancel_sleep` **撤不干净**：
    #         `isInBed` 仍为 true、人**动不了**（真机：`/walk_to` 报 ok 但一步不动），
    #         只能 `POST /warp` 到**别的图**才解开（实测）。
    #    ⇒ **睡觉是上层的事**（`go_sleep`／兜底／人），脚本不再插手。
    #      ⚠️ `--no-sleep` 参数**保留**（老调用方都传着它），现在它是个**兼容用的空开关**。
    log("留在钓点不睡觉（本脚本**不再自己睡** —— 要过夜用 go_sleep / 兜底）")
    log("=== fish run complete ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7843)  # 2026-08-14: 默认 AI 端口（曾 7842 误控 host）
    parser.add_argument("--location", default=None)  # None=就地钓（当前站位）；指定=去钓点 warp
    parser.add_argument("--max-casts", type=int, default=0,
                        help="抛 N 竿就收手（0=不限，钓到体力<20/背包满/太晚停）")
    parser.add_argument("--no-sleep", action="store_true")   # 测试用：不自动睡
    args = parser.parse_args()

    run(args.port, args.location, args.max_casts, no_sleep=args.no_sleep)
