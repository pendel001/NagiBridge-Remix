"""🧭 navigation.py — 星露谷导航（走位 / 跨图 BFS / 交通 / 门 / 买票 / 回家 / 卡墙）

2026-09-11 从 `nagi_mcp_server.py`（当时 16000+ 行）拆出的 task#7。
本模块只管"怎么从 A 到 B"，不含地图知识查询（`map_lookup` / `map_query` 仍留在 server）。

分层纪律照抄 `storage_common.py`：
  - 只依赖标准库 + `stardew_api`（`api`）+ `locations`
  - **不 import FastMCP**：`@mcp.tool()` 的注册点在 server，本模块只提供本体；
    保留 4 个薄壳（`walk_to` / `go_to` / `map_go` / `warp_safe`）在 server 上，
    AI 看到的工具名 / schema 与拆分前逐字节一致。

⚠️ **工具文案（docstring）的权威在 server 的壳上**：这 4 个工具在本模块里的同名 docstring
   是**搬运时原样带过来的原文**（为保住"搬过去的代码逐字未改"这条可验证性质）；
   **AI 真读得到的只有 server 壳上那一份**。要改工具说明 → **改 `nagi_mcp_server.py` 的壳**，
   别只改这边（否则两份漂移，就成了本仓库最忌讳的"货不对板"）。

⚠️ **import 顺序是硬约束**：`stardew_api` 在 **import 期**就把 `NAGI_URL` / `NAGI_AI_URL`
固化成 `BASE_URL` / `AI_BASE_URL`。server 是先 `os.environ.setdefault(...)` 再 `import stardew_api` 的，
所以 server 里 `from navigation import ...` **只能放在 `import stardew_api` 之后**（现与
`from storage_common import ...` 同处）。放早了本模块会先导入 `stardew_api`，**整个进程**
都拿到 7842 的默认值 → 所有操作打到房主身上（不是 AI 角色）。

🤖 **角色由端口定**：本模块里 `_minecart_walk_plan` 等读 `api.state()["player"]["x/y"]` 拿 AI 实时坐标
（走 AI 端口）。抽模块不改变这点 —— `api` 仍是 server 的那一个模块对象。
"""

import datetime
import difflib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata          # 🔤 近似名判据的归一（全角括号/标点→半角，见 `_norm_sim`）

import calendar_data
import locations
import stardew_api as api

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ═══════════════════════════════════════════
#  🔌 宿主注入（本模块**不定义**下面这些名字，由 nagi_mcp_server 末尾 navigation.bind(...) 填）
# ═══════════════════════════════════════════
# 为什么是注入而不是 import：它们住在 nagi_mcp_server 里，而 server 又要 import 本模块
# —— 反向 import 会成环。
#
# ⚠️ 必须是**同一对象引用**，不许在本模块里复制一份实现：
#    `_with_state` 内部读 server 的 `_OPS_INNER["n"]` 来决定"变化才报"的注入闭不闭嘴、消不消费
#    （2026-09-11「域 op 内嵌状态条」那个坑）。复制一份就分叉，AI 侧表现是整行失踪。
#    同理 `_STATE_SEP` 与三个节日常量也按引用注入、只读使用。
#
# ⚠️ 未注入就被用 → **立刻报错**（宁报错别兜底：静默 None 会装成"没配就跳过"，
#    把问题挪到下一次调用才炸）。`bind()` 是唯一入口、必填 8 项，漏一项在 server 启动时就炸。


class _Unbound:
    """宿主服务未注入时的占位：任何用法（调用/取属性/成员判断/迭代/取真值）都立刻报错。"""

    __slots__ = ("_name",)

    def __init__(self, name):
        object.__setattr__(self, "_name", name)

    def _boom(self, *a, **k):
        raise RuntimeError(
            f"navigation.{self._name} 还没被注入 —— "
            f"nagi_mcp_server 模块末尾漏了 navigation.bind(...)？（见本文件顶部「宿主注入」）"
        )

    __call__ = _boom
    __getitem__ = _boom
    __contains__ = _boom
    __iter__ = _boom
    __bool__ = _boom
    __len__ = _boom

    def __getattr__(self, item):
        self._boom()


_with_state = _Unbound("_with_state")                        # server: _with_state（状态条包装）
_plan_notify = _Unbound("_plan_notify")                      # server: _plan_notify（卡墙/事件提醒注入）
_STATE_SEP = _Unbound("_STATE_SEP")                          # server: _STATE_SEP（状态条分隔符）
_festival_poi_active = _Unbound("_festival_poi_active")      # 节日限定 POI 门禁
_FESTIVAL_ONLY_MAPS = _Unbound("_FESTIVAL_ONLY_MAPS")        # 只节日开放的图 → 日期
_FESTIVAL_TEMP_MAPS = _Unbound("_FESTIVAL_TEMP_MAPS")        # 节日临时图（走不了）
_FEST_SEASON_CN = _Unbound("_FEST_SEASON_CN")                # 季节中文
_mark_festival_poi_name = _Unbound("_mark_festival_poi_name")  # 到达节日 POI → 记交互历史


def bind(*, with_state, plan_notify, state_sep, festival_poi_active,
         festival_only_maps, festival_temp_maps, fest_season_cn,
         mark_festival_poi_name):
    """由 `nagi_mcp_server` 在**模块末尾**调用（必须在 `_with_state` / `_plan_notify` /
    所有 `_festival_*` 都定义之后 —— 其中 `_plan_notify` 定义得最晚）。
    全部按**引用**注入，幂等，可重复调用（测试里可重绑）。"""
    globals().update(
        _with_state=with_state,
        _plan_notify=plan_notify,
        _STATE_SEP=state_sep,
        _festival_poi_active=festival_poi_active,
        _FESTIVAL_ONLY_MAPS=festival_only_maps,
        _FESTIVAL_TEMP_MAPS=festival_temp_maps,
        _FEST_SEASON_CN=fest_season_cn,
        _mark_festival_poi_name=mark_festival_poi_name,
    )

# ═══════════════════════════════════════════
#  🧭 搬运正文（以下为 nagi_mcp_server.py 原文，逐字未改）
# ═══════════════════════════════════════════


# ═══════════════════════════════════════════
#  导航工具
# ═══════════════════════════════════════════

def _settle_after_walk(timeout: float = 3.0) -> bool:
    """等走路真正收工（`isMoving` 转 false）。返回是否已静止。

    ⚠️ 2026-09-17 真机坐实：`map walk` 到 POI 后**紧接着**写朝向不生效——
    `go_to.py` 一退出就 `api.face()`，人还在滑步，**游戏的移动逻辑立刻把朝向覆盖回去**；
    而这次写失败**照样被记成"朝上"**，AI 于是"站在碑前朝错方向 interact" ⇒
    回一个"已互动"却什么都没开（真机四条分支：POI 报"朝上"、`/state` 实为朝左/朝右，
    手动补一次 face 就开出来了；同一个端点、同一个值，**只差零点几秒**）。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            p = api.state(light=True).get("player") or {}
        except Exception:
            return False
        if not p.get("isMoving"):
            return True
        time.sleep(0.05)
    return False


def _face_verified(face: int, retries: int = 4) -> bool:
    """写朝向 + **读回 `FacingDirection` 校验** + 重试；返回是否真的生效。

    判据用 `/state` 的 `facingDirection`——它就是 `farmer.FacingDirection` 原字段，
    与 `/face` 写的是**同一个**，所以"读回相等"= 游戏里真的转了。
    （宁报错别兜底：验不过就让调用方如实报，别打印一个没发生的"朝X"。）
    """
    for i in range(max(1, retries)):
        try:
            api.face(int(face))
        except Exception:
            pass
        time.sleep(0.12 if i == 0 else 0.2)
        try:
            cur = (api.state(light=True).get("player") or {}).get("facingDirection")
        except Exception:
            return False
        if cur == int(face):
            return True
        _settle_after_walk(0.6)   # 还没停稳就再等一会儿，然后重来
    return False


def _apply_poi_stand_face(poi_name: str) -> str:
    """POI 到达后应用结构化站位+朝向（2026-08-16 恒，locations.POI_FACE）。
    返回"，朝X/站位"日志串；无配置或失败返回空串。交互仍交给 AI（interact/interact_at）。
    ⚠️ 农场设施不在 POI_FACE（动态检测），这里只处理固定可交互 POI。
    ⚠️ 2026-09-17：朝向**按有没有 `stand` 分成两条路**（恒当天定的边界）——
       · **有 `stand`（52 条，含全部节庆 POI）**：`position()` 瞬移已把人定住，朝向本来就可靠 ⇒
         **原样不动**（不加延时、不做校验）。恒：「节日已经验证成功的了，改动可能不宜牵扯到那边」。
       · **只有 `face`、没有 `stand`（14 条）**：就是那个"报朝上、实则没转、interact 点空"的 bug 的全部
         受害者 ⇒ 走"等停下 → 写 → **读回 farmer.FacingDirection 校验** → 重试"，**验不过如实报**
         （见 `_settle_after_walk` / `_face_verified`）。"""
    try:
        _mark_festival_poi_name(poi_name)   # 导航到达节日 POI → 记入交互历史
        cfg = getattr(locations, "POI_FACE", {}).get(poi_name)
        if not cfg:
            return ""
        logs = []
        stand = cfg.get("stand")
        if stand:
            try:
                px, py = api.player_tile()
                if (int(px), int(py)) != (int(stand[0]), int(stand[1])):
                    # ⚠️ 2026-08-17：/move BFS 落点会偏（木匠 (7,20) 落成 (8,20)）——
                    #    站位微调改 position 瞬移（就在附近，精准不依赖寻路）
                    api.position(int(stand[0]), int(stand[1]))
                    time.sleep(0.25)
                    logs.append(f"站位({stand[0]},{stand[1]})")
            except Exception:
                pass
        face = cfg.get("face")
        if face is not None:
            face = int(face)
            if stand:
                # 🛡️ 2026-09-17 恒：「**节日已经验证成功的了，改动可能不宜牵扯到那边**」——
                #    有 `stand` 的 52 条**刻意保持原样**：先 `position()` 瞬移把人了定住，朝向本来就可靠，
                #    而**节庆 POI 全在这 52 条里**（夜市钓鱼潜艇/沙漠钓鱼点/酒吧冰箱/齐先生冰箱/
                #    冰淇淋摊…都带 stand，实测过）。这里**不加任何延时、不做校验**，零行为变更。
                api.face(face)
                time.sleep(0.15)
                logs.append(f"朝{'上右下左'[face]}")
            else:
                # 🐛 只有 face、没有 stand 的那 **14 条** = 2026-09-17 那个 bug 的**全部受害者**
                #    （皮埃尔商店/鱼店柜台/博物馆柜台/桑迪商店/两个矿车/精通山洞 8 处/蜗牛教授）：
                #    人还在滑步就写朝向 → 被游戏下一 tick 覆盖，而失败**照样被记成"朝X"**
                #    ⇒ AI 照着它 interact，回"已互动"却什么都没开。
                #    修法：等停下 → 写 → **读回 farmer.FacingDirection 校验** → 重试；验不过如实报。
                _settle_after_walk()
                if _face_verified(face):
                    logs.append(f"朝{'上右下左'[face]}")
                else:
                    logs.append(f"⚠️朝向没能转成「{'上右下左'[face]}」（现朝向不对，"
                                f"交互前先 `scene face {face}`）")
        return "，" + "，".join(logs) if logs else ""
    except Exception:
        return ""


# ═══════════════════════════════════════════
#  ⛩️ 爷爷神龛：坐标**随农场类型变**，动态定位（2026-09-11 恒）
# ═══════════════════════════════════════════
# 为什么不能写死：每种农场类型是**各自独立的一整张地图文件**——
#   Farm.xnb(标准) / Farm_Fishing.xnb(河畔) / Farm_Foraging.xnb(森林) / Farm_Mining.xnb(山顶) /
#   Farm_Combat.xnb(荒野) / Farm_FourCorners.xnb(四角) / Farm_Ranching.xnb(草地，1.6 新增) /
#   海滩(Beach)。
#   所以"神龛在农场西北角"这种常识**不能当坐标用**（相对方位固定 ≠ 绝对坐标固定）。
#   ⚠️ 2026-09-11 恒开了 **8 种农场各一个档**逐个实测，结论：**坐标真的会变**——
#      7 种都在交互格 (8,7)/站位 (8,8)，**唯独草地是 (14,9)/(14,10)**（差 6 格）。
#      ⇒ 按前几个采样"打表写死"会在草地上把 AI 领到错格。
#
# 怎么定位：神龛的**交互瓦片**是 Farm 图上带 `Action: Message "…"` 的那一格
#   （游戏自己在图里写的，触发出来就是爷爷的字条），**站位 = 它正下方一格**。
#   ⇒ 运行时读当前这张 Farm 图，跨农场类型通用，不需要预先知道任何坐标。
#   ⚠️ 匹配条件**只能**用「Buildings 层 + 值以 `Message "` 开头」：草地的 Action 值是
#      `Message "Farm.1"`，**和标准农场一字不差**（没带地图名后缀）⇒ 靠地图名筛会漏。
#
# ✅ 8 种农场全验过（2026-09-11 恒开档带路 + 隔空 `/interact` 触发爷爷字条）：
#   标准/河畔/森林/山地/荒野/四角/海滩 = 交互格 (8,7) 站位 (8,8)；**草地 = (14,9)/(14,10)**。
#   其中 6 种读到字条原文、2 种只做了扫描；**每种农场图上整图只有这一条 Action**，匹配无歧义。
# ⚠️ 草地档**早期走不到神龛**（藏在被大木桩堵住的桥后面，要升级斧头）——那是游戏设计，
#    不是定位错了；此时 map_go 会报导航失败，属正常。
# ⚠️ 定位不到时**明确报错**、绝不退回写死坐标（宁报错别兜底：拿旧坐标摸过去 = 把问题藏起来）。
_GRANDPA_SHRINE = "爷爷的神龛"
_GRANDPA_SHRINE_TTL = 300.0
_GRANDPA_SHRINE_CACHE = {"ts": 0.0, "pos": None}


def _grandpa_shrine_stand():
    """扫 Farm 图的 Action 瓦片，返回爷爷神龛的**站位** (x, y)；扫不到 → None。
    命中条件：Buildings 层 + Action 以 `Message "` 开头；多条时取**最靠西北角**的那条
    （神龛历来在农场西北角，用它消歧义）。TTL 缓存（神龛不会动）。
    ✅ 8 种农场实测全部命中，且**每种农场图上整图只有这一条 Action**（消歧义分支目前没被触发过）。"""
    import time as _t
    _c = _GRANDPA_SHRINE_CACHE
    if _c["pos"] is not None and _t.time() - _c["ts"] < _GRANDPA_SHRINE_TTL:
        return _c["pos"]
    pos = None
    try:
        r = api._get("/tile_props", {"scan": "Action", "location": "Farm"})
        cands = []
        for h in (r.get("hits") or []):
            if h.get("layer") != "Buildings":
                continue
            v = str(h.get("value") or "")
            if not v.startswith('Message "'):
                continue
            cands.append((int(h["x"]) + int(h["y"]), int(h["x"]), int(h["y"])))
        if cands:
            _, sx, sy = min(cands)          # 最靠西北角
            pos = (sx, sy + 1)              # 站位 = 交互瓦片正下方一格
    except Exception:
        pos = None
    _GRANDPA_SHRINE_CACHE.update({"ts": _t.time(), "pos": pos})
    return pos


def _grandpa_shrine_gate(destination: str) -> str:
    """目的地是爷爷神龛时：动态定位并**刷新 locations.POI 的坐标**（go_to.py 也读它，一并受益）。
    定位不到 → 返回拦截串（别拿写死坐标硬走）。不是神龛 / 已刷新过 → 返回 ""（放行）。"""
    if destination != _GRANDPA_SHRINE:
        return ""
    pos = _grandpa_shrine_stand()
    if not pos:
        return ("❌ 定位不到「爷爷的神龛」：本档 Farm 图上没扫到它的交互瓦片"
                "（`Action: Message \"…\"`）。神龛坐标**随农场类型变**，不能写死——"
                "换农场类型的档需要人工确认一次（目前只在河畔农场验证过）。")
    try:
        locations.POI[_GRANDPA_SHRINE]["pos"] = pos
    except Exception:
        return "❌ 爷爷的神龛 POI 条目异常，无法写入动态坐标"
    return ""


# ⚠️ 卡墙检测（2026-08-17 恒：屡次移动没进展 → 提醒 AI 用 warp_safe 紧急脱离）
# 移动工具(walk_to/move_to_tile/go_to/map_go)每次调用后检查位置：
# 连续 _STUCK_THRESHOLD 次位置没变 = 卡墙 → _plan_notify 注入提醒（去重，位置变化才重置）。
# 节日/菜单对话中跳过（可能正常等待/看菜单）。
_stuck_streak = 0


_stuck_notified = False


_stuck_last_pos = None


_STUCK_THRESHOLD = 5


def _stuck_zone() -> bool:
    """节日/菜单对话中不判卡死（可能正常等待/看菜单）。"""
    try:
        s = api.state()
        if s.get("activeEvent"):
            return True
        if s.get("in_dialogue"):
            return True
        if (s.get("activeMenu") or {}).get("type"):
            return True
    except Exception:
        pass
    return False


def _tile_passable(loc_name: str, x: int, y: int) -> bool:
    """当前地图该格是否可通行（AI position 乱飞到石头/墙里 → False）。"""
    try:
        d = api._get(f"/dump_tile?x={x}&y={y}")
        return bool(d.get("tile", {}).get("passable", True))
    except Exception:
        return True


def _ai_pos():
    """AI 当前所在格 (x,y)。"""
    p = api.state().get("player") or {}
    return (p.get("x"), p.get("y"))


def _via_step(frm: str, vx: int, vy: int) -> bool:
    """把角色**精确**弄到 (vx,vy) 这一格上，返回是否落格成功。

    用途：`MAP_LINKS` 的 `via` 途经点——浴场换装格是 Back 层 `TouchAction`，
    **只认"真踩上那一格"**，差一格就是白走（恒 2026-09-10「只要能保证换衣服」）。

    🕐 **历史（已修的坑，留档）**：走位落点精度修好**之前**（2026-09-10 白天），
       `/walk_to` 的 X 会随机 ±1，实测浴场更衣室追 `(2,17)` 会停在**墙格 `(3,17)`**
       （x=2 是 1 格宽走廊，两侧都不可走）；**人一旦站在不可走的格上就废了**——
       `/move` 的路径是 `FindPath(farmer.TilePoint, …)`，起点非法 ⇒ 之后怎么走都带不动。
       当时只能"先站邻格 → `/move` 走最后一格"。**那两个坑都已随落点精度修复消失**。

    ✅ **现在的顺序**：
       ① **直接 `/walk_to` 目标格**——落点已经准了，一步到位（恒："去换衣服应该不用来回蹭一下换衣间了吧"）；
       ② 只在 ① 落偏时才退回老办法：8 向邻格挨个试 → `/move` 走最后一格
          （邻格**自验证**，落点一致才算站上；**不预判 passable**——`/passable` 和 `/dump_tile`
          两个端点的 passable 历史上就不一致，别拿它们当裁判）。
          ⚠️ `/move` 是**排队异步**的：必须轮询到真站上，别 `sleep` 死等，
          等短了后面的"走向出口"会把这一步顶掉（2026-09-10 真机踩过）。
    """
    if _ai_pos() == (vx, vy):
        return True
    # ① **直接 walk_to**（2026-09-10 恒："现在精准了，去换衣服应该不用来回蹭一下换衣间了吧"）。
    #    走位落点精度修好之后（见 CHANGELOG「走位落点精度」），`/walk_to` 已经能**精确收在目标格**，
    #    一步到位即可 —— 不用再"先站邻格再 /move 走最后一格"那套来回蹭。
    _walk_and_wait(frm, vx, vy, timeout=25)
    if _ai_pos() == (vx, vy):
        return True
    # ② 兜底（老办法，只在 ① 落偏时才用）：邻格 → /move 走最后一格
    for ax, ay in ((vx, vy + 1), (vx, vy - 1), (vx + 1, vy), (vx - 1, vy),
                   (vx + 1, vy + 1), (vx - 1, vy - 1), (vx + 1, vy - 1), (vx - 1, vy + 1)):
        if _ai_pos() == (vx, vy):
            return True
        if _ai_pos() != (ax, ay):
            _walk_and_wait(frm, ax, ay, timeout=20)
        if _ai_pos() != (ax, ay):
            continue                      # 这个邻格站不上去，换下一个
        for _ in range(4):                # 最多纠偏 4 次
            api._post("/move", {"x": vx, "y": vy})
            for _ in range(8):            # /move 异步：轮询到站定（≈4.8s）
                time.sleep(0.6)
                if _ai_pos() == (vx, vy):
                    return True
            if _ai_pos() != (ax, ay):
                break                     # 已走歪，别空转
    return _ai_pos() == (vx, vy)


def _track_move() -> None:
    """移动工具调用后：位置没变 或 站进不可通行区 → 卡墙计数 → 连续 _STUCK_THRESHOLD 次 → 注入提醒。
    ⚠️ 2026-08-17 恒：AI 卡墙一慌会乱试乱飞，position 站进石头里也累计（不可通行=更卡）。"""
    global _stuck_streak, _stuck_notified, _stuck_last_pos
    if _stuck_zone():
        return
    try:
        s = api.state()
        loc = s.get("location", {}).get("name")
        px = s.get("player", {}).get("x")
        py = s.get("player", {}).get("y")
    except Exception:
        return
    # 🪨 站进不可通行区域（position 飞到石头/墙里）→ 直接累计（位置变没变都算，乱飞也卡死）
    if not _tile_passable(loc, px, py):
        _stuck_streak += 1
    else:
        pos = (loc, px, py)
        if pos == _stuck_last_pos:
            _stuck_streak += 1
        else:
            _stuck_last_pos = pos
            _stuck_streak = 0
            _stuck_notified = False
    if _stuck_streak >= _STUCK_THRESHOLD and not _stuck_notified:
        _stuck_notified = True
        _plan_notify(
            f"⚠️ 疑似卡墙：连续 {_STUCK_THRESHOLD} 次移动没进展。"
            f"可试 scene 逃脱（warp_safe）紧急脱离；节日/菜单对话中不可用，请联系人类。"
        )


def _stuck_track(fn):
    """装饰器：包住移动工具，调用后检测卡墙。"""
    import functools as _ft

    @_ft.wraps(fn)
    def _w(*a, **kw):
        r = fn(*a, **kw)
        _track_move()
        return r
    return _w


# ⚠️ 2026-08-29 恒：紧急脱离改"warp 回上次 walk_to/map_go 失败目标"（否则自家门口）。
#    用 dict 容器（可变）避免跨函数写 global。
_NAV_LAST = {"name": None, "loc": None, "x": None, "y": None}   # 上次导航目标（解析成坐标）


_NAV_FAILED = {"v": False}                                      # 上次导航是否失败

# 🚂 2026-09-13 恒「矿车的门禁也没做好，有点严重了」——
#   病根：`_minecart_go` **先走再试**：走到矿车站 → 交互开菜单 → 轮询找站名 → 找不到才 `return False`。
#   全程**没有"这存档的矿车通没通"的前置检查**（游戏的口径是 `Data/Minecarts` 里那个网络的
#   `UnlockCondition` 过 `GameStateQuery.CheckConditions`，反编译 `GameLocation.cs:10226`；
#   不通就 `drawObjectDialogue(MineCart_OutOfOrder)` —— 真机看到的就是威利那句「已损坏」）。
#   后果两条，都是恒说的"严重"：
#     ① 失败后**不关那个对话框** ⇒ AI 被晾在站台上（真机：小星停在 `Town (105,80)`，框开着）；
#     ② 失败后**不回退走路** ⇒ `_NAV_FAILED=True` 直接把「未到 Mountain」抛上去，明明走路能到。
#   修法（不依赖新增端点，游戏在跑也能立刻生效）：
#     · 失败即 `/menu_close` 收掉对话框；
#     · **本次会话拉黑矿车**（`_MINECART_DEAD`）—— 一次不通就别再花钱走过去试第二回，
#       而且**出声记账**（恒：「别静默兜底」），下一次 `map go` 直接走路。
#     · `_minecart_route_go` 失败时返回 **空串**表示"落穿到走路"，由 `map_go` 接着走 BFS 那条路。
#   ⏳ 真正的"前置门禁"（读 `Data/Minecarts` 的 UnlockCondition）需要新端点 + 重编重启，留作下一件。
_MINECART_DEAD = {"v": False, "why": "", "day": ""}             # 矿车已被证不可用（**只认当天**）
_MINECART_LEAD = {"text": ""}                                   # 落穿到走路时带上的叙事


def _minecart_dead_now() -> bool:
    """矿车在当前这一天被证过不可用？**跨天自动失效** —— 存档里修好矿车当天就恢复，
    不会因为一次失败把矿车永久拉黑（恒：「别静默兜底」，也别静默禁用）。"""
    if not _MINECART_DEAD["v"]:
        return False
    try:
        t = api.state().get("time") or {}
        today = f"{t.get('season')}-{t.get('dayOfMonth')}-{t.get('year')}"
    except Exception:
        return True                       # 读不到时间 → 沿用旧判断（宁可少绕一趟）
    if _MINECART_DEAD["day"] and _MINECART_DEAD["day"] != today:
        _MINECART_DEAD["v"] = False       # 换天了 → 重新给矿车一次机会
        _MINECART_DEAD["why"] = ""
        _MINECART_DEAD["day"] = ""
        return False
    return True


def _cart_gate(cart_target: str) -> tuple:
    """🚂 **规划期**就问游戏"这车坐不坐得成"（2026-09-13 恒「门禁根本没做好」）。

    【为什么】原流程是**先走再试**：走到站台 → 交互 → 开菜单 → 找不到站名才失败 ⇒ 真机把 AI
      晾在站台上（`Town (105,80)`、对话框开着），而且整趟导航判失败 —— 走路明明能到。
    【依据】游戏 `/minecarts`（C# 端照 `GameLocation.ShowMineCartMenu` 的两道门评估过：
      网络级 `UnlockCondition`、每站级 `Condition`）—— **不再是我们手写的 flag 表**。
    【返回】`(可行, 说明)`。**读不到 `/minecarts`（老 DLL / 游戏未就绪）⇒ `(True, "")` 不拦** ——
      宁可照老行为走（后面还有"当天拉黑"兜底），也不能因为读不到就把路禁掉。
    """
    try:
        d = api._get("/minecarts")
    except Exception:
        return True, ""
    if not isinstance(d, dict) or not d.get("ok"):
        return True, ""
    nets = d.get("networks") or []
    if not nets:
        return True, ""
    locked = ""
    for n in nets:
        if not n.get("unlocked"):
            locked = locked or (n.get("lockedMessage") or "")
            continue
        for dest in (n.get("destinations") or []):
            if dest.get("ok", True) and str(dest.get("targetLocation") or "") == str(cart_target):
                return True, ""
    return False, locked


def _nav_resolve(name):
    """把 POI/地点名解析成 {name,loc,x,y}（供紧急脱离 warp 回失败点）。"""
    if not name:
        return None
    try:
        p = locations.POI.get(name) or {}
        if p.get("map") and "pos" in p:
            return {"name": name, "loc": p["map"], "x": p["pos"][0], "y": p["pos"][1]}
        if p.get("map"):
            pf = locations.POI_FACE.get(name) or {}
            st = pf.get("stand")
            if st:
                return {"name": name, "loc": p["map"], "x": st[0], "y": st[1]}
    except Exception:
        pass
    return None


def _walk_to_coord(x: int, y: int) -> str:
    """🎯 同图坐标走位 —— 底座就是 `/walk_to`（**和 POI 那条路同一个寻路器**，不是另开一条）。

    ⚠️ 2026-09-11 恒：这是 `movetile` 的替代品。老的 `movetile` 走 `/move`+BFS，
       而 CLAUDE.md 关键坑#1 早就写着「**别用 /move+BFS**」—— 它是仅存的一个绕过口子。
       实测 `/walk_to` 对**能站的格**落点 **8/8 精确**（那 3 次"落偏"的目标格本身
       `passable=false`，它**正确地**落在最近的能站格上）⇒ 坐标走位根本不需要另开 BFS。
       ⚠️ 老 `move_to_tile` 还有个谎报：C# 回 `No path` 时它照样印 `✅ 已移动到 (x,y)`
       （因为它不看返回、只靠"玩家没在动"猜——而没开始走的人显然"没在动"）。这里改判返回。

    ⚠️ 传送核对：`/walk_to` 走到**门/出口瓦片**上会触发 warp 换图。这**不能一刀切禁掉**
       （`map_go` 的出口**就是**靠踩上去触发的），所以改成**走完核对一次图变没变**，
       变了就如实报，并提示该用 `map go`。📌 这正是 2026-09-11 真机踩的：用旧 movetile
       从 Farm(55,12) 走一格到 (55,11)（小屋门）→ 静默 warp 进屋，落在主厅之外。
    """
    if x is None or y is None:
        return _with_state("❌ 坐标走位要**同时**给 x 和 y（缺一个我不猜，见恒 2026-09-10「宁报错别兜底」）")
    try:
        st = api.state()
        loc_before = (st.get("location") or {}).get("name", "")
    except Exception as e:
        return _with_state(f"❌ 读不到当前状态: {e}")
    try:
        _t_sent = time.time()      # 🕒 发车时刻（走位失败警报旁路的时间闸，见 `_wait_arrival`）
        r = api.walk_to_coord(loc_before, x, y)
    except Exception as e:
        return _with_state(f"❌ 走位请求失败: {e}")
    if isinstance(r, dict) and r.get("ok") is False:
        # 🚪 门/传送格闸门（2026-10-06）：mod 入口校验直接拒了，原话已经是给 AI 的指引
        #    （「要进门请用 interact；跨图请用 map ops=go」）—— 照转，别自己另编一句。
        if r.get("warp_tile"):
            _walk_log(loc_before, x, y, x, y, "门/传送格被拒")
            return _with_state(f"🚪 ({x},{y}) 走位被拒：{r.get('error') or r}")
        return _with_state(f"❌ 走不过去 ({x},{y})：{r.get('error') or r}")
    # ⚠️ 等**游戏回包里的**坐标：`/walk_to` 会把站不住的目标格就近改掉
    #    （`ModEntry.cs:19782`，详见 `_walk_and_wait`）。等我们请求的那个 ⇒ 必然是满 30s 超时。
    #    落点对不上照旧由下面那句「已到 (x,y) 附近，实际站在…」如实说 —— 判据一个字没放宽。
    _d = ((r or {}).get("destination") or {}) if isinstance(r, dict) else {}
    _ax, _ay = _d.get("x", x), _d.get("y", y)
    # 🛑 `since=_t_sent`：游戏报 `walk_failed`/`walk_blocked`（`ModEntry.cs:2294/2281`）
    #    且**晚于**本次发车 ⇒ 立刻收工，不再干等满 30s（判据与成功条件都没动，见 `_wait_arrival`）。
    arrived = _wait_arrival(loc_before, _ax, _ay, timeout=30, since=_t_sent)
    # ⚠️ 踩上去型的传送点（地图上 `TouchAction: Warp …`，例如小屋地下室楼梯 (19,35)）是
    #    **踩上去的下一 tick** 才换图 —— 刚落到格子上就立刻读，会读到"还没换图" ⇒ **漏判**。
    #    2026-09-11 真机就是这么漏的：走 Cabin(19,35) 返回「🚶 已到 (19,35)」，
    #    而**同一次调用**末尾拼的状态条已经写着 `📍 Cellar2 (3,2)`。
    #    所以核对前**等一拍**再读（只在这条坐标路上花这 0.6s；POI 那条路不受影响）。
    time.sleep(0.6)
    try:
        st2 = api.state()
    except Exception:
        st2 = {}
    loc_after = (st2.get("location") or {}).get("name", "")
    px = (st2.get("player") or {}).get("x")
    py = (st2.get("player") or {}).get("y")
    if loc_after != loc_before:
        # 🚪 2026-10-06：mod 侧现在**半路换图就停手**，并把结构化原因挂进 `walk_changed_map` 警报
        #    （`{ok:false, changed_map:true, from, to, error}`）。有它就用**游戏自己的原话**
        #    （判据来源是游戏，不是我们猜）；没收到（4 秒同文案去重／人已离开但警报还没排到）
        #    就退回下面这句按状态读出来的话 —— **如实**，两条都不是兜底。
        _cm = _walk_changed_map_alert(_t_sent)
        if _cm:
            return _with_state(
                f"⚠️ {_cm.get('error')}（「{_cm.get('from')}」→「{_cm.get('to')}」）。"
                f"坐标走位只管同图；要跨图请用 `map ops=go`。")
        return _with_state(
            f"⚠️ ({x},{y}) 那格是**传送点** —— 已经离开「{loc_before}」、到了「{loc_after}」。"
            f"跨图该用 `map ops=go`（走门/出口/交通由它负责）；坐标走位只管同图。")
    if not arrived:
        return _with_state(f"⚠️ 没走到 ({x},{y})，停在 ({px},{py})")
    if (px, py) != (x, y):
        return _with_state(f"🚶 已到 ({x},{y}) 附近，实际站在 ({px},{py})（目标格可能站不了人）")
    return _with_state(f"🚶 已到 ({x},{y})")


@_stuck_track
def walk_to(poi_name: str = "", x: int = None, y: int = None) -> str:
    """🚶 导航到指定地点（POI 落点）—— **也可直接给坐标 x/y**（同图精确走位）

    两种用法（二选一）：
      • `walk_to(poi_name="皮埃尔商店")` —— POI 名，见 locations.py 数据库
      • `walk_to(x=33, y=24)`           —— 同图坐标，底座同样是 `/walk_to`
    ⚠️ 两个都不给 → 明确报错（不猜、不兜底）。

    ⚠️ 2026-08-16 恒：**跨场景不瞬移**——POI 在别的图 → 自动走 map_go 真实路径（出口瓦片/门）；
    只有 POI 在当前图内才走过去。日常跨场景切换首选 map_go（walk_to 走出口瓦片不可靠）。
    ⚠️ 到 POI 后自动应用结构化站位+朝向（locations.POI_FACE，如水碗朝右、柜台朝上）——
    交互交给 AI（面前的目标用 interact / interact_at 触发）。农场设施走动态检测。

    常用地点：
    - 矿井入口 / 头骨矿洞 / 采石场
    - 秘密森林 / 巫师塔 / 玛妮牧场
    - 山湖 / 海边 / 河流各种钓鱼点
    - 皮埃尔商店 / 餐吧 / 铁匠铺 / 博物馆
    - 自己小屋(床) / 温室 / 农场洞穴 / 邮箱（动态定位，读信走它）
    - 巴士站 / 沙漠 / 姜岛船
    - 浴场 / 铁路 / 隧道

    Args:
        poi_name: POI 名称（见 locations.py 数据库）
        x, y: 同图目标坐标（与 poi_name 二选一；两个都不给 → 报错）
    """
    if x is not None or y is not None:
        return _walk_to_coord(x, y)
    if not (poi_name or "").strip():
        return _with_state("❌ walk 要**么给 poi_name、么给 x+y** —— 两个都不给我不知道该走哪（不猜）")
    _nr = _nav_resolve(poi_name)
    if _nr:
        _NAV_LAST.update(_nr)
    _NAV_FAILED["v"] = False
    try:
        # ⛩️ 爷爷神龛的坐标随农场类型变 → 先动态刷新，定位不到就明确报错（别拿写死坐标硬走）
        _gs = _grandpa_shrine_gate(poi_name)
        if _gs:
            return _with_state(_gs)
        # 🎇 节日限定 POI 门禁（2026-08-19 恒：非节日 map_go/walk_to 隐藏）
        if poi_name in locations.POI and not _festival_poi_active(poi_name, locations.POI[poi_name]):
            return _with_state(f"❌ {poi_name} 只在节日开放（现在去不了）")
        # ⚠️ 2026-09-03 恒：宠物碗浇水是"动作"不是"走位"——locations 明确 map walk 不扛浇水；
        #    AI 误用 walk 去宠物碗→BFS 找不到可直接站的落点→报 BFS failed/已到达但没动。直接引导走 farm 喂水。
        if any(k in poi_name for k in ("宠物碗", "水碗", "宠物水")):
            return _with_state(f"💡 「{poi_name}」用 `farm ops=喂水`（自动定位所有碗灌满）")
        # 跨图 → 走 map_go 真实路径（不飞）：解析 POI 的目标图，不在当前图就转 map_go
        poi_map = None
        if poi_name in locations.POI:
            poi_map = locations.POI[poi_name].get("map")
        elif poi_name in locations.MAP_LINKS:
            poi_map = poi_name
        if poi_map:
            cur = (api.state().get("location") or {}).get("name", "")
            if cur != poi_map:
                go = map_go(poi_name)
                face_log = _apply_poi_stand_face(poi_name)
                # map_go 自带状态条 → 取正文，face_log 接后，最后统一 _with_state
                base = go.split(_STATE_SEP)[0] if _STATE_SEP in go else go
                return _with_state(base + face_log)
        # 🏠 动态别名（回家 / 自己小屋 / 我的小屋）→ 走**同进程**的 go_to()，别丢给子进程。
        #    go_to() 认得这些别名（回家=`_go_home(door_only=True)` **推门进屋就停**；裸"小屋"=`_nav_home_door()` 只到门外），
        #    而且它用 `api`（= AI 端口 7843）—— 子进程那条路默认打 7842 房主，见下面 ⚠️。
        if any(k in poi_name for k in ("回家", "自己小屋", "我的小屋")):
            return go_to(poi_name)
        # 🧭 名字先自己核一遍：**不在 POI 表、也不是地图名 ⇒ 当场给下一步**，别丢给子进程。
        #    子进程只会回一句「[FAIL] no route from X to Y」，AI 拿着它只能干瞪眼
        #    （2026-09-25 恒真机：「你跟他说可：格斯柜台，结果他搜格斯柜台又报找不到」——
        #     状态条那行 `🗺️ 可:` 是**自由文本**（`locations.MAP_FEATURES`），119 条里 110 条
        #     **都不是能走的 POI 名**（「格斯柜台」vs 真键 `星之果实餐吧(柜台)`、「哈维医院」vs
        #     `哈维医院(门口)`…），AI 照抄它必然撞墙）。
        if poi_name not in locations.POI and poi_name not in locations.MAP_LINKS:
            try:
                _st = api.state()
                _cur = (_st.get("location") or {}).get("name", "") or ""
                _px, _py = (_st.get("player") or {}).get("x"), (_st.get("player") or {}).get("y")
            except Exception:
                _cur, _px, _py = "", None, None
            # 🔎 **先模糊对一次**（恒 2026-09-25：「猪车也犯了一样的毛病，poi_name 搜猪车搜不出来，
            #    可却告诉他"可：猪车"」）—— 状态条那行是**自由文本**、渲染时还 `split("(")[0]`
            #    **把括号砍掉**（`猪车(周五周日)` → 打出「猪车」；真键是 `猪车(旅行货车)`）
            #    ⇒ 光"列出本图落点"不够，**它得能自己认出来**。
            #    **唯一命中**就直接走（回执里写的也是完整名字，AI 顺带学到正确叫法）；
            #    **多个命中不猜**（宁报错别兜底）—— 摊开让它挑。
            _cand = sorted(k for k in locations.POI if poi_name and (poi_name in k or k in poi_name))
            # 「猪车」会同时命中 `猪车(旅行货车)`(Forest) 和 `夜市猪车(旅行货车)`(夜市)——
            # **同名的两地**。先按**当前所在图**收窄：人就在那种图里时它就是那一个。
            _cand_here = [k for k in _cand if locations.POI[k].get("map") == _cur]
            if len(_cand_here) == 1:
                _cand = _cand_here
            if len(_cand) == 1:
                poi_name = _cand[0]        # ← 认出来了，照它的完整名字继续走（下面全走正常流程）
            elif len(_cand) > 1:
                _lines = "\n".join(
                    f"     · {k}（{locations.POI[k].get('map')}）" for k in _cand[:8])
                return _with_state(
                    f"❓ 「{poi_name}」不是落点名，但**有 {len(_cand)} 个相近的** —— 我不替你猜：\n"
                    f"{_lines}\n"
                    f"   👉 挑一个**完整的名字**再 `map walk`。")
        if poi_name not in locations.POI and poi_name not in locations.MAP_LINKS:
            try:
                _st = api.state()
                _cur = (_st.get("location") or {}).get("name", "") or ""
                _px, _py = (_st.get("player") or {}).get("x"), (_st.get("player") or {}).get("y")
            except Exception:
                _cur, _px, _py = "", None, None
            _here = [k for k, v in locations.POI.items() if v.get("map") == _cur]
            if _px is not None and _py is not None:
                _here.sort(key=lambda k: abs(locations.POI[k]["pos"][0] - _px)
                           + abs(locations.POI[k]["pos"][1] - _py))
            _fallback = ("、".join(_here[:8]) + ("…" if len(_here) > 8 else "")) if _here else "（本图没登记可走落点）"
            return _with_state(
                f"❌ 认不出「{poi_name}」——POI 表里没有这个名字，也不是地图名。\n"
                f"   「{_cur}」**能走的落点**（就近排）：{_fallback}\n"
                f"   👉 照上面挑一个再 `map walk`；⚠️ **别照状态条 `🗺️ 可:` 那行抄名字**"
                f"（那是「这地方能干嘛」的说明，不是落点名）。\n"
                f"   👉 想走具体格子：`map walk x=.. y=..`；找 **NPC** 用 `map npc` 那一路"
                f"（人不是落点，走不到）。")
        # 同图 → go_to.py 走过去
        # ⚠️⚠️ 2026-09-12 真机抓到两个**会打到恒身上**的坑，都在这一段：
        #  ① **没传 --port**：go_to.py 的 `--port` 默认 `NAGI_PORT`、再默认 **7842=房主**，
        #     而 `_set_roles` 只写了 NAGI_URL/NAGI_AI_URL、**漏了 NAGI_PORT** ⇒ 子进程全程在
        #     **操作恒的角色**。这正是 CLAUDE.md 坑#5「自动注入 --port 防挪恒角色」漏掉的一条路。
        #     ⇒ 显式传 AI 端口；**拿不到端口就明确报错**，绝不退回 7842（宁报错别兜底）。
        #  ② `returncode == 0` **不等于走到了**：go_to.py 的 `go()` 返回 False 时 main 也 exit 0
        #     （离线实测 `go_to.py "自己小屋(床)"` → 打 `[FAIL] game not ready` 而 `exit=0`）；
        #     更糟的是原先 stdout 为空时 `short` 兜底成**字面量 "已到达"** ⇒ 报「已导航到「x」已到达」
        #     而 AI 纹丝没动（真机见过）。**没有输出 = 没有证据**，现在报错，不报"到达"。
        _port = (getattr(api, "BASE_URL", "") or "").rsplit(":", 1)[-1].strip("/")
        if not _port.isdigit():
            _NAV_FAILED["v"] = True
            return _with_state(f"❌ 不敢跑导航子进程：拿不到 AI 端口（api.BASE_URL={getattr(api, 'BASE_URL', None)!r}）"
                               f"—— go_to.py 没端口会默认打到 7842 房主身上，宁可这一趟不走。")
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "go_to.py"), poi_name, "--port", _port],
            capture_output=True, text=True, timeout=45,
            cwd=SCRIPT_DIR,
            # ⚠️⚠️ **必须显式 utf-8**（2026-09-12 破案）：子进程继承 `PYTHONIOENCODING=utf-8`，
            #    stdout 是 UTF-8；而 `text=True` 默认按**本地编码(GBK)** 解 → 解不动时
            #    `subprocess` 的**读取线程直接抛 UnicodeDecodeError 死掉**，`result.stdout` 变成 **None**
            #    （**不向调用方抛异常、`returncode` 照样 0**，静默得离谱）⇒ 下游 `(stdout or "")` 拿到空串
            #    ⇒ 老代码 `... if summary else "已到达"` 兜底成**字面量"已到达"**。
            #    **这就是"报了已到达、AI 纹丝没动"的真凶**（真机 + 离线双复现：
            #    `UnicodeDecodeError: 'gbk' codec can't decode byte 0xb9 in position 57`）。
            #    📌教训：`text=True` **不是**"帮我解码"的同义词，它按本地编码解；
            #    子进程是 UTF-8 时一定要 `encoding="utf-8"`，否则失败方式极其隐蔽（None 而非异常）。
            encoding="utf-8", errors="replace",
            # ⚠️⚠️ **子进程的 stdout 编码也要一起钉住**（2026-10-01 真机逮到，而且是我自己造的）：
            #    上一行 `encoding="utf-8"` 只管**父进程怎么解**；子进程写什么，取决于**子进程的 env**。
            #    `启动NagiBridge.bat` 里有 `set PYTHONIOENCODING=utf-8` ⇒ 官方启动方式下一切正常；
            #    可 MCP 服务器**重启时若丢掉那个环境变量**（我那天用手工 Start-Process 重启，没带），
            #    子进程就按 **cp936** 写中文、父进程按 utf-8 + `errors="replace"` 解 ⇒
            #    回执里出现 `[target] ������(��̨) -> Blacksmith` 这种**给 AI 看的乱码**。
            #    ⇒ 别指望"启动环境一直对"：**从这儿显式给子进程钉上 UTF-8**（跟 `--port` 同一个道理：
            #      这一层自己负责，不靠外面记得）。
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
        out = (result.stdout or "")[-1000:]
        err = (result.stderr or "")[-500:]

        if result.returncode == 0:
            # 从输出里找关键信息
            lines = out.strip().split("\n")
            # 只保留最后几行非空信息
            summary = [l for l in lines if l.strip() and "log" not in l.lower()]
            # 🚫 没输出 / go_to.py 自己报了 [FAIL] ⇒ 都是"没走到"，别硬说到达
            if not summary or any("[FAIL]" in l for l in summary):
                _NAV_FAILED["v"] = True
                why = "; ".join(summary[-2:])[:200] if summary else (err.strip()[:200] or "一句话都没输出")
                return _with_state(f"❌ 「{poi_name}」没走到：{why}")
            short = "\n".join(summary[-5:])
            face_log = _apply_poi_stand_face(poi_name)
            # 🔑 一键开门：同图走到 POI→若落点是建筑门瓦片则推门进屋（跨图已由上面 map_go 分支自带）
            door_log = ""
            if poi_name in locations.POI:
                door_log = _step_into_building(poi_map, locations.POI[poi_name].get("pos"))
            return _with_state(f"🚶 已导航到「{poi_name}」{face_log}{door_log}\n{short[:500]}")
        else:
            _NAV_FAILED["v"] = True
            return _with_state(f"❌ 导航失败: {err or out[:300] or '无响应'}")
    except subprocess.TimeoutExpired:
        _NAV_FAILED["v"] = True
        return _with_state(f"⚠️ 导航超时，可能未到达「{poi_name}」")
    except Exception as e:
        _NAV_FAILED["v"] = True
        return _with_state(f"❌ {e}")


def move_to_tile(x: int, y: int) -> str:
    """📍 当前地图内移动到指定格子（BFS 走路）
    ⚠️ 2026-08-16 恒：**只走同图、不跨场景**（矿井楼层/精确站位用）。跨场景切换用 map_go。

    Args:
        x: 目标 X 坐标
        y: 目标 Y 坐标
    """
    try:
        ok = api.move_to(x, y, timeout=15)
        if ok:
            return _with_state(f"✅ 已移动到 ({x}, {y})")
        else:
            return _with_state(f"⚠️ 移动超时，部分路径未完成")
    except Exception as e:
        return _with_state(f"❌ {e}")


# ═══════════════════════════════════════════
#  go_to：动态地点解析（建筑实时查 /farm_buildings，POI 走 locations.py 兜底）
# ═══════════════════════════════════════════

def _buildings() -> list:
    """实时查 /farm_buildings（农场建筑列表）。"""
    try:
        return api._get("/farm_buildings").get("buildings", [])
    except Exception:
        return []


def _stand_beside(location: str, x: int, y: int):
    """找 (x,y) 旁边**站得住**的一格（目标格本身是建筑/物件、踩不上去时用，如邮箱）。

    顺序固定（左→下→右→上），判据只用游戏自己的 `/passable`（2026-09-24：别自己猜，
    实测邮箱那格 `Farm(56,16)=False` —— 它在小屋的建筑占位里，人永远站不上去）。
    找不到返回 None。
    """
    for dx, dy in ((-1, 0), (0, 1), (1, 0), (0, -1)):
        try:
            if api._post("/passable", {"x": x + dx, "y": y + dy,
                                       "location": location}).get("passable"):
                return (x + dx, y + dy)
        except Exception:
            continue
    return None


def _resolve_place(place: str):
    """把目的地解析成 (location, x, y)；解析不出返回 None（走 POI 兜底）。

    - "回家/自己小屋/我的小屋" → homeLocation 动态找自己的小屋（farmhand 各自的 Cabin）
    - 建筑名（畜棚/鸡舍/温室/鱼塘/出货箱…）→ /farm_buildings 实时定位门，抗建筑搬家
    - "邮箱/信箱" → /state.mailbox 的动态坐标（跟着自己那间小屋/农舍走）
    门都在 Farm 外立面，walk_to 到门前即可。
    """
    p = (place or "").strip()
    if not p:
        return None
    bs = _buildings()

    # 1. 回家 / 自己的小屋 → 导航到门口（Farm 外立面），不是屋里
    #    优先用 /state 的 homeDoor（新DLL，精确）；老DLL 兜底：房主按 Farmhouse 建筑匹配。
    if any(k in p for k in ("回家", "自己小屋", "我的小屋")):
        home_door = api.state().get("player", {}).get("homeDoor")
        if home_door and home_door.get("location"):
            return (home_door["location"], home_door["x"], home_door["y"])
        home = api.state().get("player", {}).get("homeLocation")
        for b in bs:
            bt = b.get("type", "").lower()
            if home == "FarmHouse" and "farmhouse" in bt and "doorX" in b:
                return ("Farm", b["doorX"], b["doorY"])
            if home and home != "FarmHouse" and "cabin" in bt and b.get("indoorsName") == home and "doorX" in b:
                return ("Farm", b["doorX"], b["doorY"])
        # 兜底：农舍门
        for b in bs:
            if "farmhouse" in b.get("type", "").lower() and "doorX" in b:
                return ("Farm", b["doorX"], b["doorY"])
        return ("Farm", 59, 12)  # 最后兜底：农舍位置

    # 2. 邮箱/信箱 → **动态坐标**（`/state.mailbox` = `Game1.player.getMailboxPosition()`：
    #    跟着自己那间小屋/农舍走，小屋挪过位置它就变 ⇒ 绝不能写死坐标）。
    #    ⚠️ **落点是旁边的可站格，不是邮箱那格**：邮箱格在建筑的占位里、踩不上去（见 `_stand_beside`）；
    #       真正要敲的那一格由状态条的 📬 行写给 AI（"到了再 scene at x y"）。
    if any(k in p for k in ("邮箱", "信箱", "mailbox")):
        mb = (api.state().get("mailbox") or {})
        if not mb.get("location"):
            return None
        stand = _stand_beside(mb["location"], mb["x"], mb["y"])
        return (mb["location"], stand[0], stand[1]) if stand else None

    # 3. 建筑关键字 → 实时定位门
    KEYWORDS = {
        # ⚠️ 2026-09-19（恒）：**这里必须走"最长匹配"**（见下面 sorted），否则单字键会抢走长键。
        #    真机实录（只读探出来的一族同病）：`农舍`→**鸡舍的门**、`工棚`→**畜棚的门**——
        #    都是 `"舍": "coop"` / `"棚": "barn"` 排在 `"鸡舍"` / `"工棚"` 前面，字典顺序先撞谁算谁。
        #    跟当初 `_loc_label` 把 FarmHouse 叫成"农场"是同一个病、同一个药（改最长匹配）。
        #    恒原话：「农舍两个字就精确指 farmhouse 吧，鸡舍就指 coop。毕竟人类或 AI 都不会单传
        #    一个"舍"字来着」⇒ 顺手**删掉裸 `"舍"`**（没人会这么喊，留着只会抢匹配）。
        "畜棚": "barn", "谷仓": "barn", "棚": "barn",
        "鸡舍": "coop",
        "温室": "greenhouse",
        "小屋": "cabin",
        # 🏠 主屋（恒的 Farmhouse）：**1.6 里主屋本身就是一栋 Building**（`type == "Farmhouse"`），
        #    所以它跟畜棚鸡舍一样能动态定位。原先这里没有对应键 ⇒ `_resolve_place` 返回 None
        #    ⇒ `_enter_building_door` 查不到门 ⇒ 掉进 `ARRIVE['FarmHouse']=(10,6)` **warp 硬进**
        #    （恒最烦的"飞"）⇒ 现在补上，走门。
        "农舍": "farmhouse", "主屋": "farmhouse", "大屋": "farmhouse",
        "出货": "shipping",
        "筒仓": "silo", "粮仓": "silo",
        "鱼塘": "fish pond", "池塘": "fish pond",
        "马厩": "stable",
        "工棚": "shed", "仓库": "shed",
        "地窖": "cellar",
        "传送": "obelisk",
        "金钟": "gold clock",
        # 🇬🇧 英文键（2026-09-16 补）：**AI 与 `/map` 用的就是英文地点名**（"Greenhouse"/"Big Shed"），
        #    原来只有中文键 ⇒ `map go Greenhouse` 这里解析成 None ⇒ 推门路断 ⇒ 走兜底 warp 硬进
        #    ⇒ 恒真机看到"进温室飞到墙外"。⚠️ 键必须配下面那句 `p.lower()` 才有用。
        "barn": "barn", "coop": "coop", "greenhouse": "greenhouse",
        "cabin": "cabin", "shipping": "shipping", "silo": "silo",
        "fish pond": "fish pond", "stable": "stable", "shed": "shed",
        "cellar": "cellar", "obelisk": "obelisk", "gold clock": "gold clock",
        "farmhouse": "farmhouse",
    }
    pl = p.lower()   # ⚠️ 英文键要小写比对；中文无大小写，写在这里对中文键是 no-op
    # 🔑 **最长匹配优先**：先把命中的键按长度从长到短排，长的先试。
    #    这样 `农舍`/`鸡舍`/`工棚` 一定压过 `棚` —— 不再依赖字典书写顺序（那种"顺序对就对、
    #    谁插一行就崩"的隐式依赖，正是这一族 bug 的来源）。
    for k in sorted((k for k in KEYWORDS if k in pl), key=len, reverse=True):
        typekw = KEYWORDS[k]
        for b in bs:
            if typekw in b.get("type", "").lower():
                if "doorX" in b:
                    return ("Farm", b["doorX"], b["doorY"])
                return ("Farm", b["x"], b["y"])
    return None


def _go_to_bed(bed_loc: str, bx: int, by: int) -> tuple[bool, str]:
    """进屋后走到床边（自然走，别站床 tile——会被 game redirect 弹回门口）。
    ⚠️ 到达判定用**玩家格距离**，不用 _wait_arrival（它拿 location.name 显示名，跟床唯一名比恒假→必超时）。
    返回 (ok, msg)——**判据是位置**，别让调用方去猜 msg 里有没有 ❌。"""
    r3 = api._post("/walk_to", {"location": bed_loc, "x": bx, "y": by + 1})
    if not r3.get("ok"):
        return False, f"❌ 到床失败: {r3.get('error', r3)}"
    deadline = time.time() + 25
    while time.time() < deadline:
        s = api.state()
        px, py = s.get("player", {}).get("x"), s.get("player", {}).get("y")
        if px is not None and py is not None and abs(px - bx) <= 2 and abs(py - by) <= 2 \
                and not s.get("player", {}).get("isMoving"):
            return True, f"已到床边 ({bed_loc} {bx},{by})"
        time.sleep(0.8)
    return False, f"⚠️ 到床超时（{bed_loc} 床在 {bx},{by}）——人可能卡在门口/被家具挡住"


def _go_island_house() -> tuple[bool, str]:
    """🏝️ 岛上"去睡觉的地方"= 走进**共用姜岛小屋**（大通铺在那）。返回 (ok, msg)。

    ⚠️ 为什么走不了 `_go_home` 那套"门口 → 推门"：姜岛小屋**不在 Farm 上**，
       而 `door` 来自 C# `FindHomeDoor`——它只扫 Farm 上的建筑 ⇒ 岛屋**压根没有 door 字段**。
       改用 `map_go` 的跨图路径（`locations.MAP_LINKS["IslandWest"] → IslandFarmHouse`，
       tile (77,40)，kind=door）。
    成败**回读场景唯一名**才算数（`map_go` 回的是给人看的一句话，文案变了也不该影响判据）。
    """
    def _cur() -> str:
        try:
            return (api._post("/crawl_bed", {"action": "locate"}) or {}).get("curLoc") or ""
        except Exception:
            return ""

    if _cur() == api.ISLAND_HOUSE:
        return True, "🏝️ 已在姜岛小屋里"
    _m = map_go(api.ISLAND_HOUSE)
    if _cur() == api.ISLAND_HOUSE:
        return True, "🏝️ 已走进姜岛小屋"
    return False, (f"❌ 没能进姜岛小屋（现在在 {_cur() or '?'}）——{_m}；"
                   f"也可以自己 `map go 姜岛小屋` 走过去，再喊一次 sleep（who 照样要传）")


def _go_home(who: str = "", door_only: bool = False) -> tuple[bool, str]:
    """回家：走到「谁的床」那个屋的门口（Farm 外立面）→ 互动进门 →（默认再）走到床边。
    `who` 空 = 自己；传别人名字 = 去那个人的屋子/床边（爬床彩蛋那套）。
    `door_only=True` ⇒ **推门进屋就收工**，不往床边走。

    ⚠️ 2026-09-19 恒：「现在回家怎么默认都是会床边了呀。**推门就够了哇**」
       ⇒ AI 说「回家/去小屋」多半只是要**进屋**（避雨、拿东西、进室内场景），不是要睡觉。
         一路走到床边既绕又莫名（人无端站到床前）。
         `go_to("回家")` 已改走 `door_only=True`；**要躺床请点名「自己小屋(床)」或走 sleep**
         （那条自己会到床边，见 `_aim_sleep_home`）。

    出门不能自动化（walk_to 不肯踩上传送格），所以回家只做"进门"；
    出门用 /warp 传门外（见 go_to 兜底逻辑）。

    ⚠️ 2026-09-05 修（恒实测，根因=显示名/唯一名混比 + 找错床）：
    1. 进屋判定：用 crawl_bed locate 返回的 curLoc(当前场景**唯一名**)跟床所在唯一名比，
       别拿 location.name(显示名"Cabin") 跟 homeLocation(唯一名"FarmHouse<guid>")比——恒不等 →
       明明进屋却反复"当门外"反复点门 → 进不去/报"进门失败"。
    2. 找床：crawl_bed locate 必须带 player=目标人，否则默认找 host(房主)的床，不是要找的那张。
    3. 到床：walk 到床边，别 walk_to 床 tile（会被 game redirect 弹回门口）。

    ⚠️ 2026-09-19 恒：**拆掉"只回自己家"的限制**。门不再从 `/state.homeDoor`（那只有自己）取，
    改用 `/crawl_bed locate` 一并返回的 `door`（C# `FindHomeDoor(目标玩家)`，同一套逻辑：
    自己=按室内名匹配小屋、房主=按 "Farmhouse" 建筑类型匹配，**都是动态定位、没写死坐标**）。
    ⇒ 睡觉工具传谁的名字，就把人走到谁家床边；全程走（map_go 跨图 → 门口 → 推门 → 床边），不 warp。
    返回 **(ok, msg)**：`msg` 是给人看的一句话（含 👍/❌ 语气），`ok` 才是判据。
    走不到就**如实报 + 说清下一步**，绝不静默瞬移过去充数。
    """
    try:
        # ‑ 空 who = 自己：先问游戏"这个进程的玩家叫什么"（crawl_bed locate 回 player2）
        if not who:
            who = (api._post("/crawl_bed", {"action": "locate"}) or {}).get("player2") or ""
        if not who:
            return False, "❌ 认不出自己是谁（crawl_bed locate 没回 player2）"

        # 🏝️ 姜岛分流（2026-09-19 恒）：岛上睡的是**共用姜岛小屋**的大通铺，没有"谁的床"——
        #    再往下走就会拿着**大陆那张床**的坐标导航 ⇒ 把人从姜岛带回农场。
        if api.on_island():
            return _go_island_house()

        bl = api._post("/crawl_bed", {"action": "locate", "player": who})
        if not bl.get("ok"):
            # 名字写错/查无此人时 C# 会在这里直接把可选名字列出来（别再往下猜）
            return False, f"❌ 找不到{who}的床: {bl.get('error')}"
        bed = bl.get("bed") or {}
        door = bl.get("door") or {}
        bed_loc, bx, by = bed.get("location"), bed.get("x"), bed.get("y")
        if not bed_loc or bx is None or by is None:
            return False, f"❌ 不知道{who}的床在哪（crawl_bed 没回 bed.location）"

        def _cur() -> str:
            """当前场景唯一名（crawl_bed locate 的 curLoc），进屋判定用它。"""
            return (api._post("/crawl_bed", {"action": "locate", "player": who}).get("curLoc")
                    or "?")

        # ‑ 已在床所在场景 → 直接去床边（door_only 就是"已经在家了"，一步不用走）
        if _cur() == bed_loc:
            return (True, f"🏠 已经在家了（{bed_loc}）") if door_only else _go_to_bed(bed_loc, bx, by)

        # 1. 走到那栋屋子门口（Farm 外立面）
        #    🚪 人在别的图（镇上/别人屋里）时，`_walk_on_map` 先 map_go 回农场再走——
        #       原来直接拿 Farm 坐标 walk_to = 跨图瞬移（恒 2026-09-19 抓的"飞"同款）。
        if not door.get("location") and who == (bl.get("player2") or ""):
            # 🔧 兼容窗口：老 DLL 的 locate 还没有 `door` 字段，而"回自己家"这条路**以前是好的**。
            #    `/state.player.homeDoor` 与 locate 的 `door` 是**同一个 C# 函数**（`FindHomeDoor(Game1.player)`），
            #    取值必然相同 ⇒ 这是"同源换个入口"，不是找一个近似值来凑（不算兜底）。
            #    别人的门没有等价来源 ⇒ 不硬凑，往下走如实报错（那本来就是本次新加的能力）。
            _sd = (api.state().get("player") or {}).get("homeDoor") or {}
            if _sd.get("location"):
                door = _sd
        if not door.get("location"):
            return False, (f"❌ 找不到{who}家的门（床上报的是 {bed_loc}，但没查到对应建筑的门口）"
                           f"——先 map go Farm 再手动走过去；这栋屋子可能不在农场（如姜岛小屋），"
                           f"或游戏还没重启加载新 DLL")
        _err = _walk_on_map(door["location"], door["x"], door["y"], timeout=35)
        if _err:
            return False, f"❌ 去{who}家门口失败: {_err}"

        # 2. 若还在门外 → 下马 + 站门口正下方(门在正上) + 面朝门 + /interact 触发 checkAction
        if _cur() != bed_loc:
            if api.state().get("player", {}).get("riding"):
                api._post("/key", {"key": "confirm"})  # 下马
                time.sleep(1.5)
            api._post("/position", {"x": door["x"], "y": door["y"] + 1})
            time.sleep(0.5)
            api._post("/face", {"direction": 0})  # 0=上，门在头顶
            time.sleep(0.3)
            api._post("/interact")
            for _ in range(10):
                time.sleep(0.8)
                if _cur() == bed_loc:
                    break
            # ‑ 仍没进屋 → 精确点门瓦片兜底（interact_at 直接点 tile，不依赖面朝）
            if _cur() != bed_loc:
                api.interact_at(door["x"], door["y"])
                time.sleep(1.5)
        if _cur() != bed_loc:
            return False, (f"⚠️ 进{who}家失败（人还在 {_cur()}）——门可能锁着/被挡/在菜单里；"
                           f"门在 {door['location']} ({door['x']},{door['y']})")

        # 3. 到床边（`door_only` 就在这儿收工：已经站在屋里了）
        if door_only:
            return True, f"🏠 已进{who}家（推门进屋，没往床边走）"
        return _go_to_bed(bed_loc, bx, by)
    except Exception as e:
        return False, f"❌ 回家失败: {e}"


# ═══════════════════════════════════════════
#  🛑 走位失败警报旁路（2026-10-05 · **只用于失败早退**）
# ═══════════════════════════════════════════
# 判据出处（游戏侧，权威）：
#   · `ModEntry.cs:2294` `walk_failed`  —— "附近也没有可站格——原地不动"（**这一步真没动**）
#   · `ModEntry.cs:2281` `walk_blocked` —— "和你现在站的不是同一片连通区…这一步没走过去"
#   · 读法 `ModEntry.cs:8086-8104` `GET /alerts`；⚠️ **默认会消费队列**（`:8094-8095`），
#     **`peek=true` 才只读不拿**（`:8089`）；状态条正是靠消费它来喂的（`nagi_mcp_server.py:1275`）
#   · ⚠️ 游戏对 **同 type + 同文案** 有 **4 秒去重**（`ModEntry.cs:1182-1184`）⇒ 连续两次
#     一模一样的失败可能**整段一条都收不到** —— 那时**退回旧行为**（等满 timeout / 图名早退），
#     **不会更糟**。
#   · 先例：`mine_run.py:459-464` 早就在读 `walk_completed`（⚠️ 它没传 peek ⇒ 是**消费式**的，
#     这里**刻意不学**那一点）。
#
# `DateTime.UtcNow.ToString("O")`（`ModEntry.cs:1188`）＝ ISO8601 **UTC**、**7 位**小数秒，
# 形如 `2026-10-05T12:34:56.7890123Z`。下面这条正则只干一件事：把小数秒**截到 6 位**
# （Python <3.11 的 `fromisoformat` 只认 3/6 位）。
_ALERT_UTC_RE = re.compile(r"^(.*\.)(\d{6})\d+([+-]\d{2}:?\d{2})$")


def _alert_epoch_utc(alert) -> float:
    """把游戏警报的 `timeUtc` 解析成 **epoch 秒**（与 `time.time()` 同一把尺子）。

    解析不出来（字段缺 / 格式变了 / Python 太老）→ 返回 `None` ⇒ 调用方**当成没收到**：
    **宁可不早退，也不拿猜出来的时间判"没到"**（假失败比白等更糟）。
    """
    raw = str((alert or {}).get("timeUtc") or "").strip()
    if not raw:
        return None
    try:
        _s = raw.replace("Z", "+00:00")
        _m = _ALERT_UTC_RE.match(_s)
        if _m:
            _s = _m.group(1) + _m.group(2) + _m.group(3)
        return datetime.datetime.fromisoformat(_s).timestamp()
    except Exception:
        return None


def _walk_failed_alert(since):
    """取"**本次走位开始之后**游戏自己报的走位失败警报"（没读到 / 认不出 → `None`）。

    · `since` = 本次发 `/walk_to` 的**时刻**（`time.time()`）；`None` ⇒ **整条旁路关掉**
      （调用方没给发车时刻 ⇒ 我们没法把队列里的警报归到"这次" ⇒ 宁可不早退）。
    · ⚠️ **必须 `peek=True`**：不带 peek 会**消费**队列（`ModEntry.cs:8094-8095`），
      而状态条靠这份队列喂（`nagi_mcp_server.py:1275`）—— 偷走它＝状态条瞎掉。
    · ⚠️ **必须比时间戳**：队列里可能还躺着**上一次**走位的失败警报
      （游戏在 update 里补发的、或我们用自己 timeout 提前收工时它才发出来）
      ⇒ 只认 `timeUtc` **晚于** `since` 的那条，否则会把旧失败当"这次没到"（假失败）。
    · 只看 `walk_failed` / `walk_blocked` 两种（`ModEntry.cs:2294/2281`）；
      **不看** `walk_completed` —— "收到完成就判成功"是另一回事，本批不做（成功判据一个字没动）。
    """
    if since is None:
        return None
    try:
        a = api.alerts(peek=True)      # ⚠️ 关键字**必须是 peek=True**（别改成消费式，见上）
    except Exception:
        return None
    _best, _best_t = None, None
    for _al in ((a or {}).get("alerts") or []):
        if str((_al or {}).get("type") or "") not in ("walk_failed", "walk_blocked"):
            continue
        _t = _alert_epoch_utc(_al)
        if _t is None or _t <= since:
            continue
        if _best_t is None or _t > _best_t:    # 取**最新**那条（要印给调用方的就是它）
            _best, _best_t = _al, _t
    return _best


def _wait_arrival(target_loc: str, target_x: int, target_y: int, timeout: int = 30,
                  since: float = None, alert_out: dict = None) -> bool:
    """轮询等 walk_to 到达（含跨地图自动寻路）。⚠️ 传**游戏回包里的**坐标，见 `_walk_and_wait`。

    🩺 **慢就出声**（2026-09-19）：这个循环判据三条（名字对 / ≤2 格 / 不在移动），任一不满足
      就干等满 timeout —— 而调用方多半把返回值丢掉、**等完照样往下走**。
      `TimeOut` 一旦跑满，症状就是恒说的"谜之停顿"。所以**超过 3 秒就 print 一行**：
      哪张图、哪个格、等了多久、成没成、以及**当时人到底在哪**（一眼看出是哪条判据不满足）。
      常态下这些等待都是 1~2 秒 ⇒ 不打印；**打印了就是有事**，别当噪音忽略。

    🛑 **`since` / `alert_out` ＝ 走位失败警报旁路**（2026-10-05 加；**只用于失败早退**）：

      · `since`：**本次发 `/walk_to` 的时刻**（`time.time()`，由 `_walk_and_wait` 从它真正
        发车那一行传进来）。只有 `timeUtc` **晚于**它的 `walk_failed`/`walk_blocked` 才认。
        `None` ⇒ **整条旁路关掉**（认不出"这次"就宁可不早退，见 `_walk_failed_alert`）。
      · `alert_out`：**出参**（调用方传个 dict 进来）。命中时写
        `{"game_alert": 游戏原话, "game_alert_type": 警报 type}`，供调用方并进失败说明。
      · ⚠️ **成功判据（图名 + ±2 + 静止）一个字没放宽**；本旁路只在成功判据不满足时才看，
        且**不会**因为收到 `walk_completed` 就提前判成功。
      · ⚠️ 游戏有 **4 秒同文案去重**（`ModEntry.cs:1182-1184`）⇒ 有可能整段一次警报都收不到；
        那种情况**退回加这条之前的老行为**（等满 / 图名早退），**不是更糟**。
    """
    _t0 = time.time()
    deadline = _t0 + timeout
    _off = 0
    while time.time() < deadline:
        try:
            s = api.state()
            _l = s.get("location", {}).get("name")
            if _l == target_loc:
                _off = 0
                px, py = s.get("player", {}).get("x"), s.get("player", {}).get("y")
                if px is not None and py is not None:
                    if abs(px - target_x) <= 2 and abs(py - target_y) <= 2 and not s.get("player", {}).get("isMoving"):
                        return True
            elif _l:
                # 🚪➡️🟢 人**已经不在目标图了** —— 多半是走位途中踩上了 warp 格/门瓦片，
                #    被**游戏自己**送走了（`_walk_trigger_warp` 特意避开 warp 格，但出口那一片
                #    常常挨着门/桥）。这时"名字对得上"这条判据**定义上永远不会成立**，
                #    再等只能干等满 timeout。实测：BusStop 走去 Town 出口，人已被送到 Town(0,54)，
                #    这里还在等「BusStop (44,22)」⇒ **白等 27 秒**（恒 2026-09-19「到town也超长延迟了」）。
                # ⚠️ 连续 3 次（≈2.4s）才算 —— 单次读到空串/抖动不算数，别把正常走位误判成"走了"。
                _off += 1
                if _off >= 3:
                    _dt = time.time() - _t0
                    print(f"[walk-left] 等「{target_loc} ({target_x},{target_y})」时人已离开该图"
                          f"（现在 {_l}）—— 提前 {_dt:.1f}s 收工（目标已作废）", flush=True)
                    return False
        except Exception:
            pass
        # 🛑 游戏自己的走位终局警报（**只用于失败早退**，2026-10-05）
        #    `walk_failed`（`ModEntry.cs:2294` 附近全站不住 ⇒ 原地不动）/
        #    `walk_blocked`（`:2281` 落点不在同一连通区 ⇒ 这一步没过去）都是**游戏权威**的"这步没成"。
        #    加这条之前：走位**已经失败**了这儿还在干等满 timeout（预算 `min(max(25, dist*0.5+10), 60)`
        #    = `navigation.py:2017`）⇒ 这就是恒报「走不到」里那段"白等"的来源。
        #    ⚠️ 放在**成功判据之后**：人真站在目标 ±2 内就先判成功（警报晚到一步也不算失败）。
        _al = _walk_failed_alert(since)
        if _al:
            _dt = time.time() - _t0
            _gt = str(_al.get("type") or "")
            _gm = str(_al.get("message") or "")
            if alert_out is not None:
                alert_out["game_alert"] = _gm
                alert_out["game_alert_type"] = _gt
            print(f"[walk-gamefail] 等「{target_loc} ({target_x},{target_y})」时游戏报走位失败"
                  f"（{_gt}）—— 提前 {_dt:.1f}s 收工：{_gm}", flush=True)
            return False
        time.sleep(0.8)
    try:
        _s = api.state()
        _where = (f"人在 {(_s.get('location') or {}).get('name')} "
                  f"({(_s.get('player') or {}).get('x')},{(_s.get('player') or {}).get('y')}) "
                  f"moving={( _s.get('player') or {}).get('isMoving')}")
    except Exception:
        _where = "读不到位置"
    print(f"[walk-slow] 等「{target_loc} ({target_x},{target_y})」满 {timeout}s 没到 —— {_where}",
          flush=True)
    return False


def _walk_changed_map_alert(since):
    """🚪 取"**本次走位开始之后**游戏报的 `walk_changed_map`"（= 同图坐标走位半路被换图、**已停手**）。

    · 游戏那条警报（`ModEntry.cs:AbortWalkIfMapChanged`）除了中文文案，还挂着**结构化字段**
      `{ok:false, changed_map:true, from:<旧图>, to:<新图>, error:"走到一半换图了…"}` ——
      `/walk_to` 是发射后不管的，只能靠警报把这份"回包"送回来。
    · 与 `_walk_failed_alert` 同规矩：`since=None` ⇒ 旁路整个关掉；必须 `peek=True`（别消费状态条的队列）；
      必须比 `timeUtc`（队列里可能还躺着上一次走位的那条）。
    """
    if since is None:
        return None
    try:
        a = api.alerts(peek=True)
    except Exception:
        return None
    _best, _best_t = None, None
    for _al in ((a or {}).get("alerts") or []):
        if str((_al or {}).get("type") or "") != "walk_changed_map":
            continue
        _t = _alert_epoch_utc(_al)
        if _t is None or _t <= since:
            continue
        if _best_t is None or _t > _best_t:
            _best, _best_t = _al, _t
    return _best


def _walk_log(loc: str, x: int, y: int, ax, ay, verdict: str):
    """🧾 每次走位**无条件**打一行到 stdout（MCP 的 stdout 落到 `_mcp_out.log`）。

    ⚠️ 为什么必须有（2026-10-01 恒：「**为什么你验完老是走回爷爷神龛**」）：
      那天我和恒查了两轮都查不出来 —— **因为走位这件事当时一行日志都不留**：
      `/walk_to` 是发射后不管的（挂了路线就返回），人还在走的时候工具早就"办完了"；
      MCP 的工具日志只记"哪个工具被调了"，**记不到"这一步把人送到了哪"**。
      最后是靠一层网络拦截才钉出真凶（自验里两条老用例在打真机）。
      ⇒ 从这行起：**每一次走位都留痕**（请求坐标 → 游戏"就近改格"后的落点 → 到位/超时）。
      排查顺序也定死：先看 `_mcp_out.log` 里的 `[walk]`，再谈别的。
    ⚠️ 只打日志、**不改行为**（超时那条照样如实返回 False 给调用方 —— 要不要中止由调用方定）。
    """
    try:
        print(f"[walk] {time.strftime('%H:%M:%S')} {loc} 请求({x},{y}) → 落点({ax},{ay}) {verdict}",
              flush=True)
    except Exception:
        pass


def _walk_and_wait(loc: str, x: int, y: int, timeout: int = 25):
    """`/walk_to` 到 (loc,x,y) 并等人**真的站定**。返回 `(是否到位, 说明)`。

    ⚠️ 2026-09-19 恒真机（「谜之停顿了至少20s才warp」）—— 本函数存在的**唯一**理由：

      `/walk_to` 对**站不住的目标格**会「就近改到最近可走格」，并把**改后**的坐标放进
      回包 `destination`（`ModEntry.cs:19782-19800`，注释原话就是"并在返回里注明 adjusted"）。

      而原先 15 处写法全都是 `/walk_to` 完就 `_wait_arrival(我们自己那个坐标)`，
      **回包整个扔掉** ⇒ 一旦被调整，`_wait_arrival` 的 ±2 容差就**永远满足不了**
      ⇒ 干等满整个 timeout，**再照样往下走**（返回值多半还被丢）＝ 纯浪费 + 谎报到达。

      🔬 真机实测（`_tmp_navprobe.py` 秒表 + `/walk_to` 回包三方对齐）：
        `IslandSouth` 码头出口格 `(17,44)` **站不住** → 游戏改到 `(20,44)`（差 3 格）
        ⇒ 轮回在原地**干站 25.1 秒**（isMoving 全程 0，排除了"卡在移动中"那个嫌疑）
        ⇒ 才终于 `/warp`。恒的原话：「就跟超时兜底一样」。

    ⇒ **等回包里的 `destination`，不是等我们自己请求的那个坐标。**
      这不是"放宽容差"那种兜底 —— 是**换成语义上就对的数源**：那个数本来就是游戏
      告诉我们"我实际去了哪"。容差 ±2 一个字没动。

    🚪 **门/传送格放行**（2026-10-06）：本函数的**每一个调用方都是导航内部**
      —— POI 落点、`_enter_building_door` 的门格、`_walk_trigger_warp` 的出口格、
      售票机站位、`_walk_on_map`…… 这些格**本来就可能是门/传送格**（矿井入口、帐篷、地窖楼梯
      都是"踩上去就是入口"的 warp 格）⇒ 一律带 `allowWarp=true`，行为跟加闸门之前**一模一样**。
      ⛔ 闸门（不传 `allowWarp`）留给 **AI 直调的坐标走位** `_walk_to_coord` ——
         它才是"踩门格 → 被游戏送走 → 在新图同坐标收尾 = 飞墙外"那条路。
      ⚠️ 放行**不等于**不禁换图：同图坐标走位半路被换图，mod 侧照样**立刻停手**
         （`ModEntry.cs:AbortWalkIfMapChanged`）。

    **返回契约**：`(ok, note)`
      · `ok=True`  → `note` 是 ""，或"目标被调整"的提示（`（⚠️ (x,y) 站不住，游戏就近改到 (ax,ay)）`）
      · `ok=False` → `note` **一定是可直接展示的失败原因**，调用方 `return note` 就行，别再自己编。

    🛑 **走位失败警报旁路**（2026-10-05）：发车**之前**先记下时刻（`_t_sent`），交给
      `_wait_arrival(since=…)` —— 游戏自己报的 `walk_failed`/`walk_blocked`
      （`ModEntry.cs:2294/2281`）**只认晚于这个时刻**的那条 ⇒ 走位已经失败就**立刻收工**，
      不再干等满 timeout；并把它**原话**并进失败说明（`_to`），调用方印出来就是游戏自己的话。
      ⚠️ `since` 必须是"**发车时刻**"而不是"开始等的时刻"：游戏可能在 `/walk_to` 那一发
      里就已经判失败（回包还没回到我们手上）⇒ 用开始等的时刻会漏掉它。
    """
    _t_sent = time.time()          # 🕒 发车时刻（失败警报旁路的时间闸，见 _wait_arrival docstring）
    try:
        # 🚪 `allowWarp=true`：导航内部站点允许落在门/传送格上（见 docstring「门/传送格放行」）
        r = api._post("/walk_to", {"location": loc, "x": x, "y": y, "allowWarp": True})
    except Exception as e:
        _walk_log(loc, x, y, x, y, "请求就炸了")      # 🧾 留痕（见 _walk_log）
        return False, f"walk_to 出错: {e}"
    if not r.get("ok"):
        _walk_log(loc, x, y, x, y, "寻路失败")
        return False, f"寻路失败: {r.get('error', r)}"
    d = r.get("destination") or {}
    ax, ay = d.get("x", x), d.get("y", y)
    note = ""
    if (ax, ay) != (x, y):
        note = f"（⚠️ ({x},{y}) 站不住，游戏就近改到 ({ax},{ay})）"
    _aout = {}
    if _wait_arrival(loc, ax, ay, timeout=timeout, since=_t_sent, alert_out=_aout):
        _walk_log(loc, x, y, ax, ay, "到位")
        return True, note
    # ⚠️ 超时原因里写**我们真正等的那个格**（ax,ay），不是请求的那个 —— 否则排查时被带偏
    _ga = str(_aout.get("game_alert") or "").strip()
    if _ga:
        # 游戏自己说"这步没成" ⇒ 失败说明里放**游戏原话**（判据来源是游戏，不是我们猜）
        _walk_log(loc, x, y, ax, ay,
                  f"游戏警报说走位失败（{_aout.get('game_alert_type')}）· 没走到")
        _to = f"走位失败（{loc} {ax},{ay}）—— 游戏警报原话：{_ga}"
    else:
        _walk_log(loc, x, y, ax, ay, "超时没到（人可能还在路上）")
        _to = f"走位超时没到（{loc} {ax},{ay}）"
    # 🔴 2026-10-05 恒真机逮到（「失败时印的是**旧坐标**」）：上面两句写的 `(loc ax,ay)` 是
    #    **我们请求的那个格**，不是"人现在在哪" ⇒ 读起来像"人在这"，其实人可能在大老远。
    #    ⇒ 失败说明里必须带**这一刻现读**的落点（读不到就如实说读不到，⛔不拿旧值/目标值冒充）。
    try:
        _s2 = api.state()
        _p2 = _s2.get("player") or {}
        _l2 = ((_s2.get("location") or {}).get("name") or "?")
        _to += f"；**人现在在 {_l2} ({_p2.get('x')},{_p2.get('y')})**"
    except Exception:
        _to += "；人现在在哪**读不到**（别当成已经到了目标格）"
    return False, (note + _to) if note else _to


def _walk_on_map(loc: str, x: int, y: int, timeout: int = 35) -> str:
    """**同图**走位到 (loc, x, y)。返回 `""` = 到了；否则一句**能直接照做**的失败原因。

    ⚠️ 2026-09-19 恒真机（「这里又是直接从畜棚**飞**到农场再走到鸡舍了」）：
    `/walk_to` 的 `location` 一旦**不是当前图**，就是**跨图瞬移**（不是寻路）。
    所以这个口子先确认人在不在 `loc`：不在就先交给 `map_go` 正常走过去，再走最后一段。
    ⇒ 凡是"拿着别图坐标直接 walk_to"的地方，都该走这个函数，别各自裸写。
    （审计时全文件扫过一遍：其余 `/walk_to` 站点传的都是**当前图**的 `frm/loc`，
      或上游有 `cur == X` 守卫；`go_to` / `_go_home` 这两处是真漏的，已收编。）
    """
    try:
        cur = (api.state().get("location") or {}).get("name", "") or ""
    except Exception:
        cur = ""
    if cur and cur != loc:
        map_go(loc)
        try:
            cur = (api.state().get("location") or {}).get("name", "") or ""
        except Exception:
            cur = ""
        if cur != loc:
            # 到不了目标图：如实报，不做跨图瞬移（宁报错别兜底）
            return f"到不了 {loc}（现在在 {cur or '?'}）——先 map go {loc}"
    _ok, _note = _walk_and_wait(loc, x, y, timeout=timeout)
    if not _ok:
        return _note
    return ""


@_stuck_track
def go_to(place: str) -> str:
    """📍 自动导航到任意地点（多地图寻路：走→出口→传送→走→…目的地）

    建筑名实时查 /farm_buildings 定位，不依赖静态坐标（建筑搬家也不怕）；
    POI 名走 map_go 真实路径（2026-08-16 恒：不再 go_to.py 跨图瞬移）。
    ⚠️ 日常跨场景切换首选 map_go；go_to 用于建筑门口/回家/POI 落点。

    支持：
    - 回家 / 自己小屋 / 我的小屋 → 动态回自己的小屋
    - 邮箱 / 信箱 → 走到**自己的邮箱**旁边（/state.mailbox 动态定位；到了再 scene at 敲那一格）
    - 建筑名：畜棚/鸡舍/温室/鱼塘/筒仓/出货箱/马厩/工棚/传送…
    - POI 名：皮埃尔商店/海滩/矿井入口/头骨矿洞/巴士站/沙漠…

    Args:
        place: 目的地名称（建筑或 POI）
    """
    try:
        # 回家/自己小屋 → 走门流程（走到门口→互动进门→**推门进屋就停**）
        # ⚠️ 2026-09-05 恒：裸"小屋"也算自家（排除女巫/巫师/魔法/神殿，那些是真女巫小屋）
        _pl = str(place or "").lower()
        _excl = ("女巫", "巫师", "魔法", "神殿", "witch")
        if "回家" in _pl and not any(k in _pl for k in _excl):
            # ⚠️ 2026-09-19 恒：「现在回家怎么默认都是会床边了呀。**推门就够了哇**」
            #    ⇒ 「回家」= 进到屋里就收工；**要躺床请点名「自己小屋(床)」或走 sleep**
            #      （sleep 自己会到床边，那条走的是 `_aim_sleep_home` 里的 `_go_home()` 全流程）。
            _ok, _m = _go_home(door_only=True)
            return _with_state(_m)
        if any(k in _pl for k in ("小屋", "cabin")) \
                and not any(k in _pl for k in _excl):
            # ⚠️ 2026-09-12：**点名要床的**（walk 的 POI 表里就叫「自己小屋(床)」）走全流程进屋到床边
            #    —— 原来一律 `_nav_home_door()` 只到门口，却回「已导航到「自己小屋(床)」已到达」，
            #    名字写"床"、人站在门外 = 货不对板（恒 09-12 抓的）。裸"小屋/cabin"仍只到门口（原设计）。
            if "床" in _pl:
                _ok, _m = _go_home()
                return _with_state(_m)
            return _nav_home_door()    # "进小屋/cabin"→只导航到门口（进屋交给 AI interact_at）

        target = _resolve_place(place)
        if target is None:
            # 非建筑 → POI 兜底走 map_go（真实出口瓦片路径，不瞬移）
            return map_go(place)

        loc, x, y = target
        # 🚪 人在别的图（比如在畜棚里说 go_to 鸡舍）→ `_walk_on_map` 会先 map_go 过去再走，
        #    绝不再"拿着 Farm 坐标 walk_to"跨图瞬移（恒 2026-09-19 抓的"飞"）。
        _err = _walk_on_map(loc, x, y, timeout=35)
        if _err:
            return _with_state(f"❌ 没到「{place}」({loc} {x},{y})：{_err}")
        return _with_state(f"🚶 已到「{place}」({loc} {x},{y})")
    except Exception as e:
        return _with_state(f"❌ {e}")


# 🗺️ 场景名中文别名（2026-08-30 恒：map_go 的 MAP_LINKS 键是英文，AI 想的是中文场景名——
#    "go 铁路" 报"知识库没有"。加这张表：destination 先查中文别名→认成 MAP_LINKS 键。
#    ⚠️ 选近口已由 _map_bfs 天然正确(自动挑最少段数入口)，这里只补"名字认不出"。
#    键=中文场景名(可含多个 alias)，值=MAP_LINKS 键；只收录"AI 会当作场景整体去"的地点名空间。）
SCENE_NAME_ALIAS = {
    # 主城区/农场
    "农场": "Farm", "农庄": "Farm",
    "巴士站": "BusStop", "车站": "BusStop",
    "深山": "Backwoods", "林间小径": "Backwoods", "边远森林": "Backwoods",
    # ⚠️ 2026-09-17 恒：「那模糊匹配加城镇吧」——表里有 "镇"/"小镇"/"鹈鹕镇"，但**没有"城镇"**；
    #    而下面那条模糊匹配**故意跳过单字别名**（`len(alias) < 2`，防"山/岛/镇"误伤），
    #    所以 "城镇" 两边都不命中 ⇒ 报"知识库没有"。补进去（同义、无疑义，不会误路由）。
    "镇": "Town", "小镇": "Town", "鹈鹕镇": "Town", "城镇": "Town",
    "山": "Mountain", "山岭": "Mountain", "矿山": "Mountain",
    "森林": "Forest",
    "海滩": "Beach", "海边": "Beach",
    "铁路": "Railroad", "火车站": "Railroad",
    "沙漠": "Desert", "巴士沙漠": "Desert",
    "山顶": "Summit",
    "隧道": "Tunnel",
    # 矿/冒险
    "矿井": "Mine", "矿洞": "Mine", "下矿": "Mine",
    "头骨矿洞": "SkullCave", "头骨洞穴": "SkullCave",
    "下水道": "Sewer",
    "秘密森林": "Woods", "硬木森林": "Woods",
    "探险家公会": "AdventureGuild", "怪物公会": "AdventureGuild", "冒险家协会": "AdventureGuild", "冒险者公会": "AdventureGuild",
    "精通山洞": "MasteryCave",
    # 商业/服务
    "皮埃尔": "SeedShop", "种子商店": "SeedShop", "商店": "SeedShop",
    "医院": "Hospital", "诊所": "Hospital",
    "餐吧": "Saloon", "酒吧": "Saloon", "星之果实": "Saloon",
    "铁匠": "Blacksmith", "铁匠铺": "Blacksmith",
    "博物馆": "ArchaeologyHouse", "图书馆": "ArchaeologyHouse",
    "电影院": "MovieTheater",
    "木匠": "ScienceHouse", "木匠店": "ScienceHouse", "罗宾": "ScienceHouse",
    "鱼店": "FishShop", "威利": "FishShop",
    "玛妮": "AnimalShop", "牧场": "AnimalShop",
    "桑迪": "SandyHouse", "绿洲": "SandyHouse",
    "赌场": "Club",
    # 居民房（2026-09-10 恒校准补：海莉&艾米丽家 / 乔迪家）
    "海莉": "HaleyHouse", "海莉家": "HaleyHouse", "艾米丽": "HaleyHouse", "艾米丽家": "HaleyHouse",
    "乔迪": "SamHouse", "乔迪家": "SamHouse", "山姆": "SamHouse", "山姆家": "SamHouse",
    "文森特": "SamHouse", "文森特家": "SamHouse", "肯特": "SamHouse", "肯特家": "SamHouse",
    # 魔法/女巫区
    "法师塔": "WizardHouse", "巫师塔": "WizardHouse", "法师家": "WizardHouse",
    "法师地下室": "WizardHouseBasement", "幻觉神龛地下室": "WizardHouseBasement",
    "女巫沼泽": "WitchSwamp", "沼泽": "WitchSwamp",
    "女巫小屋": "WitchHut", "巫师小屋": "WitchHut",
    "魔女沼泽洞穴": "WitchWarpCave", "黑暗护身符洞穴": "WitchWarpCave",
    "温泉": "BathHouse_Entry", "浴场": "BathHouse_Entry",
    "温泉池": "BathHouse_Pool", "泳池": "BathHouse_Pool",  # ♨️ 泡澡的池子（室内最里；入口→大厅→更衣室→泳池，见 POI 温泉(更衣室女/男)）
    "莱纳斯帐篷": "Tent", "帐篷": "Tent",  # ⛺ 莱纳斯住帐篷室内（2026-09-06 恒：map_go 进帐篷，warp 瓦片自动传）
    "雷欧树屋": "LeoTreeHouse", "树屋": "LeoTreeHouse",  # 🌳 雷欧住树屋（星露谷树屋，雷欧6心搬来/常在），门交互进
    # 姜岛
    "姜岛": "IslandSouth", "岛": "IslandSouth",
    "姜岛农场": "IslandWest",
    "火山": "VolcanoDungeon0", "火山矿井": "VolcanoDungeon0", "火山矿洞": "VolcanoDungeon0",  # 火山=入口层(第一层)，先到这准备/站位
    "火山入口": "VolcanoEntrance", "火山区域": "IslandNorth", "火山入口区": "IslandNorth",
}


def _norm_key(s):
    """🔤 英文地名归一：小写 + 去空格（"farm"→"farm"、"Skull Cave"→"skullcave"）。

    ⚠️ 只用来做**精确归一命中**（见 `_MAP_KEYS_CI`），不做编辑距离/模糊——认不出就原样返回、
    照旧报"知识库没有"，别让"名字差不多"被悄悄路由到别的图（宁报错别兜底）。"""
    return "".join(str(s).lower().split())


# MAP_LINKS 键的归一索引（模块加载时建一次；MAP_LINKS 是静态常量，运行期不改）。
# ⚠️ 建表时**当场查冲突**：两个键若归一后撞车（只差大小写/空格），路由就变成"看字典顺序"，
#    这种错只在真机上偶发、离线测不出来 ⇒ 宁可 import 期就炸（宁报错别兜底）。
_MAP_KEYS_CI = {}
for _k in locations.MAP_LINKS:
    _n = _norm_key(_k)
    if _n in _MAP_KEYS_CI:
        raise RuntimeError(f"MAP_LINKS 键归一后冲突：{_k} vs {_MAP_KEYS_CI[_n]}")
    _MAP_KEYS_CI[_n] = _k
del _k, _n


# 🏠 「自家小屋/家」的**整串**白名单（判据见 `_is_home_word`）。
# ⚠️ 2026-10-05（真机 A）：以前 `map_go` 里是**子串**判据（`"小屋" in 目的地`）⇒
#    `map go 姜岛小屋(门内六人房)`（`locations.py:432` 里**有这条全名**）被"小屋"两个字劫持到
#    **自家 Cabin 门口 Farm(55,12)**，还回「🏠 已到自家小屋门口」＝**认错地方还报"到了"**。
_HOME_WORDS = ("小屋", "我的小屋", "自己小屋", "自家小屋", "我的家", "我家", "自己家", "自家", "家",
               "cabin", "小屋(床)", "我的小屋(床)", "自己小屋(床)", "自家小屋(床)")
_HOME_VERBS = ("去", "进", "回", "到", "往", "走")


def _is_home_word(s) -> bool:
    """这个目的地是不是在说「**自家**小屋/家」？—— **整串**判据（去掉一个前缀动词后整串相等）。

    恒 2026-09-05 拍板的语义**原样保留**：`去小屋/进小屋/回家/我家/小屋(床)` 都算"自家小屋"，
    统一走 `_nav_home_door()`（只到门口，进屋交给 AI）。
    ⚠️ 2026-10-05（真机 A）改成整串：`姜岛小屋(门内六人房)` / `雷欧小屋(内)` / `女巫小屋(门口)`
    这类**带修饰的全名**一律**不算**（它们该走 POI/场景那条路，别被两个字劫持）。
    """
    t = str(s or "").strip().lower().replace(" ", "")
    for _v in _HOME_VERBS:
        if t.startswith(_v):
            t = t[len(_v):]
            break
    return t in _HOME_WORDS


def _poi_ambiguous(q):
    """半截名 `q` 在 POI 表里**多候选、且落点不止一张图** ⇒ 返回候选全名（否则 `[]`）。

    判据（**别再放宽**）：
      ① `q` **精确**命中 `locations.POI` / `MAP_LINKS` 键 / `SCENE_NAME_ALIAS`（或归一后命中键）
         ⇒ `[]` —— **精确名优先**：全名/别名命中时不许再拿子串去搅（真机 A 就是全名被子串赢走的）；
      ② 否则取**名字里含 `q`** 的 POI 当候选；
      ③ 候选 ≥2 **且 `map` 不止一个** ⇒ 报歧义。⚠️ 命中**同一张图**的多个 POI **不算**歧义
         （导航目的地本来就是那张图，交给下游既有的建筑/场景兜底）——这里**不替谁挑任何一个 POI**。
    ⚠️ 只在 `map_go` 门口调（它是跨场景入口）；`walk_to` / `go_to` 的既有行为一个字不动。
    """
    s = str(q or "").strip()
    if not s:
        return []
    if s in locations.POI or s in locations.MAP_LINKS or s in SCENE_NAME_ALIAS:
        return []
    if _norm_key(s) in _MAP_KEYS_CI:
        return []
    cand = [n for n in locations.POI if s in n]
    if len(cand) < 2:
        return []
    if len({(locations.POI[n].get("map") or "") for n in cand}) < 2:
        return []
    return sorted(cand)


# 🚫 「变体名」判据零件（2026-10-05 真机 B）——
#   现场：`map go 姜岛小屋(门内六房)`（POI 表里的真名带"人"字：`姜岛小屋(门内六人房)`，`locations.py:432`）
#   ⇒ 回执「🗼 姜岛图腾柱(→岛) → IslandSouth (11,11) → 到达 IslandSouth」：**没报错、没提歧义**，
#   被别名「姜岛」→`IslandSouth`（`SCENE_NAME_ALIAS`，本文件 `:1430`）当**子串**命中，
#   **悄悄把目的地改写成岛枢纽**。❌ 不是假到达（确实到了它自己选的地方），是**静默改写目的地**
#   ⇒ 违反铁律「宁报错别兜底」。⇒ 判据拆成下面两个纯函数，`map_go` 门口与 `_resolve_scene_name` 共用。
_VARIANT_VERBS = ("前往", "去往", "去", "进", "回", "到", "往", "走")   # 口语前缀（"去铁路"/"回姜岛"）


def _strip_lead_verb(s):
    """剥掉一个**口语前缀动词**（"去铁路"→"铁路"、"回姜岛"→"姜岛"）；没前缀返回 ""。

    ⚠️ 只服务于"剥完**精确**命中"这一条（保住 `map go 去铁路` 这类既有写法）；剥完还认不出 ⇒ 照旧报错。
    ⚠️ 按**长度降序**试（"去往农场"必须先剥"去往"，否则剥出"往农场"⇒ 认不出）。"""
    t = str(s or "").strip()
    for _v in sorted(_VARIANT_VERBS, key=len, reverse=True):
        if t.startswith(_v) and len(t) > len(_v):
            return t[len(_v):].strip()
    return ""


def _alias_variant(q):
    """`q` 与别名表里短名的**真子串**关系分类 —— 返回 `(kind, hits)`，`hits=[(别名, 目标键)]`。

      · `("exact",   [])` —— 精确命中（MAP_LINKS 键/归一键/别名键/POI 键，含剥前缀动词后的精确命中）
                             ⇒ **合法入口，一个字都不改**（`map go 姜岛` 仍是 IslandSouth）；
      · `("shorter", …)` —— `q` 是别名键的真子串（**输入更短**，如 `岛小屋`）⇒ 候选，多候选交调用方报歧义；
      · `("longer",  …)` —— 别名键是 `q` 的真子串（**输入更长** = 真机 B：`姜岛` ⊂ `姜岛小屋(门内六房)`）
                             ⇒ **绝对不许静默选中**；
      · `("none",    [])` —— 无关，放行。
    ⚠️ 单字别名（`len<2`）照旧不算（恒 2026-08 的"防山/岛/镇误伤"口径）；单字**输入**本批也不动。
    ⚠️ 两头都沾的输入（`姜岛农`：`姜岛` ⊂ 它 ⊂ `姜岛农场`）按 **shorter 先**判 —— 与
       `_resolve_scene_name` 的取用顺序、以及老代码"取最长命中"同一个解（否则声明会和真解析打架）。
    """
    s = str(q or "").strip()
    if not s:
        return ("none", [])
    if s in locations.MAP_LINKS or s in SCENE_NAME_ALIAS or s in locations.POI:
        return ("exact", [])
    if _norm_key(s) in _MAP_KEYS_CI:
        return ("exact", [])
    _v = _strip_lead_verb(s)
    if _v and (_v in locations.MAP_LINKS or _v in SCENE_NAME_ALIAS or _v in locations.POI
               or _norm_key(_v) in _MAP_KEYS_CI):
        return ("exact", [])
    if len(s) >= 2:
        # ⚠️ 顺序 = `_resolve_scene_name` 的顺序（**输入更短**那条在前）：含 `s` 的别名一定**长过** `s`，
        #    而被 `s` 含着的别名一定**短过** `s` ⇒ 前者总是"更长命中"、与老代码的"取最长"同解。
        #    （`姜岛农` 这种两头都沾的输入：老代码取 `姜岛农场`⇒IslandWest，这里也必须落 A，别落 B。)
        _short = sorted([(a, k) for a, k in SCENE_NAME_ALIAS.items() if len(a) >= 2 and s in a],
                        key=lambda x: -len(x[0]))
        if _short:
            return ("shorter", _short)
        _long = sorted([(a, k) for a, k in SCENE_NAME_ALIAS.items() if len(a) >= 2 and a in s],
                       key=lambda x: -len(x[0]))
        if _long:
            return ("longer", _long)
    return ("none", [])


def _variant_name_error(q):
    """**变体名闸**：认不出、却"含"着表里的短名/别名（输入更长）且**像真名的错字变体** ⇒ 报错 + 列候选。

    判据（**别放宽**；阈值与实测带见 `_VARIANT_SIM_THRESHOLD`）：
      ① 精确命中 ⇒ `""`（放行；现有合法行为一条不改）；
      ② 输入**比**别名短 ⇒ 只在**多候选且目标不在一张图**时报歧义（与 补30 `_poi_ambiguous` 同口径：
         指向同一张图的多个候选不算歧义，交给下游既有兜底）；单候选 ⇒ `""`（`_resolve_scene_name` 照旧认）；
      ③ 别名**是输入的真子串**（输入更长）⇒ 先算它与**真名集合**的最高近似度：
         · **≥ 阈值** ⇒ 报错（宁报错别兜底）：候选①那个**最高分真名**摆最前 ②别名原本指向的场景键；
         · **< 阈值** ⇒ `""`（放行 ⇒ 按短名解析）。⚠️ 放行**不等于静默**：回执由
           `_variant_shortname_note` 明写「按短名「X」理解 → <场景>」。
    ⚠️ 只在 `map_go` 门口调；`walk_to` / `go_to` / POI 那条既有路一个字不动。
    """
    kind, hits = _alias_variant(q)
    if kind in ("none", "exact"):
        return ""
    _sim, _simname = 0.0, ""
    if kind == "longer":
        _sim, _simname = _variant_sim_best(q)
        if _sim < _VARIANT_SIM_THRESHOLD:
            return ""      # 不像真名的错字 ⇒ 按短名解析，但回执会**显式声明**（见 `_variant_shortname_note`）
    elif len({k for _, k in hits}) < 2:
        return ""          # 单目标半截名：`_resolve_scene_name` 照旧认成那个场景（既有行为）
    s = str(q or "").strip()
    # 候选①：POI 全名 —— **最高分真名摆最前**，再补双向整串 + `difflib` 近似名
    _poi = []
    if _simname and _simname in locations.POI:
        _poi.append(_simname)
    for _n in locations.POI:
        if (_n in _poi) or not (s in _n or _n in s):
            continue
        _poi.append(_n)
    for _c in difflib.get_close_matches(s, list(locations.POI), n=4, cutoff=0.6):
        if _c not in _poi:
            _poi.append(_c)
    _lines = []
    for _n in _poi[:6]:
        _p = locations.POI.get(_n) or {}
        _lines.append(f"     · POI「{_n}」（{_p.get('map')} {_p.get('pos')}）")
    for _a, _k in hits[:4]:
        _lines.append(f"     · 短名「{_a}」→ 场景 {_k}")
    _why = (f"它比表里名字只差一点（与「{_simname}」的相似度 {_sim:.2f} ≥ {_VARIANT_SIM_THRESHOLD}），"
            f"像是**真名的错字变体**，我不拿短名/别名当子串猜（那会把目的地悄悄改写掉）"
            if kind == "longer"
            else "它是**半截名**，能同时对上好几个短名、还指向不同的图，我不替你挑")
    return _with_state(
        f"❌ 认不出「{s}」这个地点：{_why}。\n"
        f"  你可能想说的是：\n" + "\n".join(_lines) + "\n"
        f"  👉 换个名字再来（POI 全名要连括号里那截写全），或用 `map lookup 关键词` 查真名。")


# 🔢 **近似名阈值**（2026-10-05 真机 B 精修；判据入口 `_variant_name_error` / `_variant_shortname_note`）。
#   含义：只有"与某个真名相似度 ≥ 0.95"的更长输入才算"真名的错字变体"⇒ 报错；
#         其余（相似度低）⇒ 按短名解析，但回执**必须显式声明**（`🗺️ 按短名「X」理解 → <场景>`）。
#   ⚠️ **为什么是 0.95 而不是 0.80**（实测带，`SequenceMatcher.ratio` + `_norm_sim` 归一）：
#       恒钦定"必须放行（按短名）"的四个真机惯用名，最高分是 `皮埃尔店(柜台)` vs `皮埃尔商店(柜台)` = **0.941**；
#       而"必须报错"的病样本 `姜岛小屋(门内六房)` vs 真名 `姜岛小屋(门内六人房)` = **0.952**。
#       ⇒ 阈值只能落在 (0.941, 0.952] 这个**窄带**里；取 0.95（余量 ±0.01，要挪就挪这一个数）。
#       （0.80 会把 `皮埃尔店(柜台)` 0.941 / `皮埃尔店` 0.889 一起拦下 —— 那正是恒不要的。）
_VARIANT_SIM_THRESHOLD = 0.95


def _norm_sim(s):
    """近似度比较用的归一：**去空格 + 全角括号/标点→半角（NFKC）+ 小写**。

    ⚠️ 只用于"近似名"打分；地名认不认得出仍然只看 `_norm_key`/精确表（两把尺子，别混）。"""
    return "".join(unicodedata.normalize("NFKC", str(s or "")).lower().split())


def _variant_sim_best(q):
    """`q` 与**真名集合**（POI 键 + MAP_LINKS 键/图名 + 别名键）的最高近似度 ⇒ `(分数, 真名)`。

    ⚠️ 实时扫表（不预烤）：`locations.POI` 在自验里会被临时塞夹具（`_map_go_resolve_selftest` ⑩c/⑬），
       预烤的常量表会看不到它们 ⇒ 判据就测不到了。代价只在"输入更长"那条窄路上（~800 次比对，约 10ms）。"""
    a = _norm_sim(q)
    if not a:
        return (0.0, "")
    best = (0.0, "")
    for _n in list(locations.POI) + list(locations.MAP_LINKS) + list(SCENE_NAME_ALIAS):
        b = _norm_sim(_n)
        if not b:
            continue
        r = difflib.SequenceMatcher(None, a, b).ratio()
        if r > best[0]:
            best = (r, _n)
    return best


def _variant_shortname_note(q):
    """近似度低、按短名解析时**必须显式声明**的那一行（把"静默改写"变成"贴标签改写"）；否则 ""。

    判据与 `_variant_name_error` **互补**（同一个 `_alias_variant` + 同一个 `_VARIANT_SIM_THRESHOLD`）：
      · `longer` 且最高近似度 **< 阈值** ⇒ `🗺️ 按短名「木匠店」理解 → ScienceHouse（要指定别的地点：map lookup）`
        （「X」取**最长命中**那个别名 —— 与 `_resolve_scene_name` 真正采用的解析**同一个**，不许两套口径）；
      · 精确名 / 半截名 / 会被报错拦下的近似名 / 无关 ⇒ `""`。
    """
    kind, hits = _alias_variant(q)
    if kind != "longer" or not hits:
        return ""
    if _variant_sim_best(q)[0] >= _VARIANT_SIM_THRESHOLD:
        return ""          # 这条会被 `_variant_name_error` 报错拦下 ⇒ 不能再贴"按短名理解"（自相矛盾）
    _a, _k = hits[0]
    return f"🗺️ 按短名「{_a}」理解 → {_k}（要指定别的地点：map lookup）"


def _near_map_hint(dest):
    """认不出的目的地 → 回一句"你是不是想去 X"（没把握就返回空串）。

    ⚠️ 只**提示**、绝不改道：名字没认出来就如实报错，路线由 AI 自己重敲决定。
    （2026-09-22 恒：报错必须给下一步——只报"没有」等于让 AI 干瞪眼。）"""
    try:
        hit = difflib.get_close_matches(_norm_key(dest), list(_MAP_KEYS_CI), n=1, cutoff=0.8)
    except Exception:
        return ""
    if not hit:
        return ""
    key = _MAP_KEYS_CI[hit[0]]
    return f"；你是想去「{key}」吗？那就 `map go {key}`"


def _resolve_scene_name(name):
    """把中文/别名目的地认成 MAP_LINKS 场景键（**精确优先；变体名宁报错**）。
    精确命中→返回场景键；找不到→返回原值(交给既有逻辑走 POI/建筑兜底)。
    选近口不在这做——_map_bfs 会挑最少段数入口。

    ⚠️ 2026-10-05（真机 B，两批）：
       · 旧第 3 步里**无条件**的"`alias in s`（更长的输入里含短别名）⇒ 悄悄选中"**没了**
         ——`map go 姜岛小屋(门内六房)` 就是被别名「姜岛」当子串命中、静默改写成 `IslandSouth` 的；
       · 现在那条路只在**相似度低**（不像真名错字）时**按最长短名**解析，而且**必须**由 `map_go` 的薄壳
         贴一句 `🗺️ 按短名「X」理解 → <场景>`（判据见 `_VARIANT_SIM_THRESHOLD`/`_variant_shortname_note`）；
       · 相似度 ≥ 阈值（像真名错字）⇒ 这里**原样返回**，由门口 `_variant_name_error` 报错 + 列候选。
    保留：① 精确（含剥口语前缀动词后精确）；② 输入**比**别名短（`岛小屋` ⊂ `姜岛小屋(门内六人房)`）——
       但多候选且**目标不在一张图**时**不猜**（返回原值 ⇒ 门口那道闸报歧义）；单字输入照旧走老口径（本批不动）。"""
    if not name:
        return name
    s = str(name).strip()
    # 1. 本来就是 MAP_LINKS 键(英文) → 直接用
    if s in locations.MAP_LINKS:
        return s
    # 1.2 剥掉口语前缀动词后再**精确**命中（"去铁路"/"回姜岛"——老代码靠子串歪打正着，这里明写、判据更死）
    _v = _strip_lead_verb(s)
    if _v:
        if _v in locations.MAP_LINKS:
            return _v
        _vh = _MAP_KEYS_CI.get(_norm_key(_v))
        if _vh:
            return _vh
        if _v in SCENE_NAME_ALIAS:
            return SCENE_NAME_ALIAS[_v]
        if _v in locations.POI:
            return locations.POI[_v]["map"]
    # 1.5 🔤 大小写/空格不敏感（2026-09-22 恒真机撞见）：AI 满屏看到的域名叫**小写** `farm`
    #     （状态条「🛠️ 可用域: farm」、引导文案「farm 通常在 Farm 做」），于是照着敲
    #     `map go farm` ⇒ 报「知识库没有「farm」的地点链接」，人在自家小屋出不了门。
    #     MAP_LINKS 键是 CamelCase(Farm/SeedShop/BusStop)，原来只做精确比对 ⇒ 大写才对得上。
    #     ⚠️ 归一后**无冲突**（72 键实测，见 `_MAP_KEYS_CI` 的 import 期断言）⇒ 不会误路由。
    _hit = _MAP_KEYS_CI.get(_norm_key(s))
    if _hit:
        return _hit
    # 2. 精确命中别名
    if s in SCENE_NAME_ALIAS:
        return SCENE_NAME_ALIAS[s]
    # 3. `s` 是别名的**真子串**（输入更短，如 `岛小屋`）—— 既有候选行为：取最长命中。
    #    ⚠️ 多候选**指向不同的图**时**不猜**（返回原值 ⇒ `map_go` 门口的 `_variant_name_error` 报歧义）；
    #    ⚠️ 单字输入（`len(s)==1`）照旧走"最长命中"老口径（恒 2026-08 "防单字误伤"那条，本批不动）。
    _cands = [(a, k) for a, k in SCENE_NAME_ALIAS.items() if len(a) >= 2 and s in a]
    if len(s) >= 2 and len({k for _, k in _cands}) >= 2:
        return s
    best = None
    for alias, key in _cands:
        if best is None or len(alias) > len(best[0]):
            best = (alias, key)
    if best:
        return best[1]
    # 4. 别名**是 `s` 的真子串**（输入更长，真机 B：`姜岛` ⊂ `姜岛小屋(门内六房)`）——
    #    2026-10-05 精修后**不是**无条件静默选中了：
    #      · 相似度 ≥ `_VARIANT_SIM_THRESHOLD`（像真名的错字变体）⇒ **原样返回**，交 `map_go` 门口
    #        的 `_variant_name_error` 报错 + 列候选（宁报错别兜底）；
    #      · 相似度低（真机惯用名：`罗宾木匠店`/`威利鱼店`/`皮埃尔店`）⇒ 按**最长短名**解析，
    #        但 `map_go` 的薄壳会贴一句 `🗺️ 按短名「X」理解 → <场景>`（恒：「绝不静默」）。
    #    取最长命中：与 3. 同一个口径、也与 `_variant_shortname_note` 声明的那一个**必须同一个**。
    _bcands = [(a, k) for a, k in SCENE_NAME_ALIAS.items() if len(a) >= 2 and a in s]
    if _bcands:
        if _variant_sim_best(s)[0] >= _VARIANT_SIM_THRESHOLD:
            return s                      # 像真名错字 ⇒ 这里一个字都不猜
        return max(_bcands, key=lambda x: len(x[0]))[1]
    return s


def _warps_to(target_loc: str):
    """当前地图上通往 target_loc 的 warp 数据（/warps 实时，2026-08-13）。
    返回 [(出口x, 出口y, 目标入口x, 目标入口y), ...]。比静态标注准。"""
    try:
        cur = api.state().get("location", {}).get("name", "")
        r = api._get("/warps")
        maps = (r.get("maps") or {}).get(cur, []) or []
        return [(w["x"], w["y"], w.get("targetX"), w.get("targetY"))
                for w in maps if w.get("targetLocation") == target_loc]
    except Exception:
        return []


def _enter_building_door(loc: str):
    """map_go 进门：走到建筑门口 → 推门进屋。**返回 `(进去了没, why, 门格, 细节原话)`**。

    门口坐标：BUILDING_DOORS（固定建筑）优先，_resolve_place（农场建筑动态）兜底。

    ⚠️ 2026-10-05 恒：「刚才好像走到一半就 warp 进博物馆了。」／「问题在于根本没走到博物馆
       门口推门就直接进来。」—— 病根在这个函数的**返回值太穷**：旧版只回 bool，调用方
       **分不出**三种失败：
         ① 根本没走到门口（走位超时 / 人不在门那张图 / 表里没门格）
         ② 走到门口、推了门、门锁着（游戏会说话 ⇒ `_locked_door_dialogue()` 读得到）
         ③ 走到门口、也推了、游戏没反应
       旧调用方把 ①③ 都当"真·导航失败" ⇒ 掉进兜底 `api.warp` **瞬移穿墙**进屋
       （人从没正对过门 —— 恒看画面一眼看穿）。
    ⇒ 现在把 `why` + 门格 + **走位那一发的原话**一起回上去（`why`：`""` 成了 / `no_door` /
       `other_map` / `walk_failed` / `pushed_no_effect` / `error`），调用方按它如实报、
       **一律不再 warp**。

    🔁 2026-10-05（补24b 尾巴 · 恒的假设「**还在走就提前兜底**」）：① 走位超时后**先判人还在不在动**
       （`isMoving` 为真 **或** 两次采样坐标变了）—— 还在动就**照同图 POI 那条先例再补一段**
       （25 → 30，**最多两段**），真停了才允许判 `walk_failed`；③ 退邻格也只在"人就在门附近"
       （走位成了、或离门 ≤6 格）时才试，免得在远处用 4×10s 原地打转把"没走到"这一档盖住。

    🔬 2026-10-05 真机读出来的门格事实（`/tile_props?x=101&y=89&location=Town`，只读）：
       `Buildings` 层 `Action: "LockedDoorWarp 3 14 ArchaeologyHouse 800 1800"` —— 即
       **门就是那一格的 Buildings 瓦片属性、开放时段 800~1800（8:00-18:00）**
       （所以 09:30 那次失败跟"没开门"无关，恒的判断对）。这种**门瓦片人往往站不上去**
       （Buildings 层），⇒ 现在门格没走成时**会退到门格四邻再推一次**（见下面 ③）。
    """
    tile = None
    try:
        cur = api.state().get("location", {}).get("name", "")

        def _inside() -> bool:
            """进没进屋 —— 判据**只有**这一条（别拿"站到门口那格"当代理指标）。"""
            return api.state().get("location", {}).get("name", "") == loc

        # 1. 固定建筑门口
        door = locations.BUILDING_DOORS.get(loc)
        if door is None:
            # 2. 农场建筑动态门口（/farm_buildings）
            try:
                t = _resolve_place(loc)
                if t and t[0] == cur:
                    door = (t[1], t[2])
            except Exception:
                pass
        if door is None:
            return False, "no_door", None, ""
        out_map, (dx, dy) = door
        tile = (dx, dy)
        if out_map != cur:
            # 先到门口所在的地图（一般就在当前图；不在就走 MAP_LINKS 到门口那张图）
            return False, "other_map", tile, f"人在 {cur}，门在 {out_map}"

        def _push() -> bool:
            """推门两发：① `interact_at(门格)`（`/interact {x,y}` → 直接 `checkAction` 那一格，
            不要求面朝）② 面朝上 + `/interact` 兜底。返回"进没进屋"。"""
            try:
                api.interact_at(dx, dy)
            except Exception:
                pass
            time.sleep(1.2)
            if _inside():
                return True
            try:
                api._post("/face", {"direction": 0})   # 兜底：面朝门 + /interact
                time.sleep(0.3)
                api._post("/interact")
            except Exception:
                pass
            time.sleep(1.5)
            return _inside()

        # ① 走到门格。`/walk_to` 对**站不住的目标格**会「就近改到最近可走格」并把改后坐标
        #    放进回包（`ModEntry.cs:19782-19800`）；`_walk_and_wait` 等的就是**改后那格**。
        _wok, _wnote = _walk_and_wait(out_map, dx, dy, timeout=25)
        # 若走位已触发进门（踩上门瓦片被游戏送进屋），提前返回。
        # ⚠️ 这步必须在 `if not _wok` **之前** —— 人踩上门瓦片、被游戏自己送进屋时，
        #    `_wait_arrival` 会因"人已离开该图"**如实**报失败，可我们**明明已经进屋了**
        #    （修 2026-09-19 那个"提前收工"时一并发现的顺序问题）。
        if _inside():
            return True, "", tile, _wnote
        # 🔁 2026-10-05（补24b 尾巴 · 恒的假设）：「我以为是**等待时间**还是什么出了问题导致
        #    **还在走就提前兜底**。」—— `_wait_arrival` 超时那一刻人**可能还在路上**
        #    （从 Town 一头走到门格 (101,89) 是长距离），旧代码在这儿**直接判死**，
        #    于是"走位还没走完"和"根本没到门口"混成同一档。
        #    ⇒ 先判"还在不在动"：还在动就**照同图 POI 那条先例再补一段**（`:3236` 附近注释、
        #    自验钉成 `waits == [20, 30]`）。**最多补一段**（25 → 30），不写无限续走
        #    （恒讨厌"谜之停顿"）；真停了才允许往 `walk_failed` 报。
        #    判据**两个取或**：`/state.player.isMoving` 为真 **或** 两次采样（~1.2s）坐标变了 ——
        #    单看 `isMoving` 会漏"后台暂停时 isMoving=False 但走位排着队"那一档（项目多处踩过）。
        _carry = ""
        if not _wok:
            _p1 = _ai_pos()
            try:
                _mv1 = bool((api.state().get("player") or {}).get("isMoving"))
            except Exception:
                _mv1 = False
            time.sleep(1.2)
            _p2 = _ai_pos()
            if _mv1 or _p1 != _p2:
                _wok2, _wn2 = _walk_and_wait(out_map, dx, dy, timeout=30)
                if _inside():
                    return True, "", tile, (_wnote or "") + "；续走那一段时被游戏送进屋"
                _wok = _wok2
                _carry = (f"；⚠️ 超时那刻人**还在动**（moving={_mv1}，{_p1}→{_p2}）"
                          f"⇒ 照先例再补一段 30s：" + (_wn2 or ("到了" if _wok2 else "仍没到")))
            else:
                _carry = f"；超时那刻人**已停且不在门格**（moving=False，坐标没变 {_p1}）"
            _wnote = (_wnote or "走位超时") + _carry
        _trail = [f"走门格({dx},{dy})：" + (_wnote or ("到了" if _wok else "没到"))]
        _pushed = False
        # ② 到了（或已在门格 ±2 内 —— `/walk_to` 自带容差、`interact_at` 是"点门格"）⇒ 推门
        _px, _py = _ai_pos()
        try:
            _near = (abs(int(_px) - int(dx)) <= 2 and abs(int(_py) - int(dy)) <= 2)
        except Exception:
            _near = False
        if _wok or _near:
            _pushed = True
            if _push():
                return True, "", tile, "；".join(_trail)
            _trail.append(f"在 {_ai_pos()} 推了门({dx},{dy})：游戏没让进")
        # ③ 门格站不住 / 没走到 / 人离门还远 ⇒ **退到门格四邻再推一次**。
        #    理由（真机读到的）：门常常是 `Buildings` 层那一格的 `Action`（门瓦片），
        #    **人站不上去**；而 `/walk_to` 的"就近改"未必改到门旁边。
        #    ⚠️ 只在"人现在不贴着门"时才试（贴着了再走邻格是白走）。
        try:
            _far = (abs(int(_px) - int(dx)) > 1 or abs(int(_py) - int(dy)) > 1)
            _dist = max(abs(int(_px) - int(dx)), abs(int(_py) - int(dy)))
        except Exception:
            _far, _dist = True, 999
        # ⚠️ 2026-10-05（补24b 尾巴）：退邻格只在"**人就在门附近**"时才值得 ——
        #    若走位**超时且人停在远处**，再走四个邻格就是 4×10s 的原地打转（恒讨厌"谜之停顿"），
        #    还会把"没走到"这一档盖成"试过了"。
        #    ⇒ ② 走位**成了**（`_wok`，只是游戏"就近改格"把落点改到门旁边、够不着门）照旧退邻格；
        #       走位**没成**时，只在人已到门格 6 格内才试。
        if _far and (_wok or _dist <= 6):
            for (_cx, _cy) in ((dx, dy + 1), (dx, dy - 1), (dx - 1, dy), (dx + 1, dy)):
                _ok2, _n2 = _walk_and_wait(out_map, _cx, _cy, timeout=10)
                if _inside():
                    return True, "", tile, "；".join(_trail + [f"走到邻格({_cx},{_cy})时被游戏送进屋"])
                _trail.append(f"邻格({_cx},{_cy})：" + (_n2 or ("到了" if _ok2 else "没到")))
                if not _ok2:
                    continue
                _pushed = True
                if _push():
                    return True, "", tile, "；".join(_trail)
                _trail.append(f"从邻格({_cx},{_cy})推了门({dx},{dy})：游戏没让进")
        return False, ("pushed_no_effect" if _pushed else "walk_failed"), tile, "；".join(_trail)
    except Exception as _e:
        return False, "error", tile, f"{type(_e).__name__}: {_e}"


# ── 门反查表：门口瓦片 → 建筑（2026-09-10 恒拍板"map_go/walk_to 一键开门"）──
#   **门**专指"交互推门进屋"的建筑门（哈维医院/皮埃尔店/铁匠铺…：进入靠 interact 打开室内门）。
#   ⚠️ 三类**不是门**，不进反查表：
#   ① 出货箱/筒仓/马厩/传送阵/金钟——在 _resolve_place，站外面用，非门。
#   ② 走上去就 warp 的"入口"（矿井/头骨矿洞/农场洞穴/帐篷/秘密森林/隧道/赌场）——进入靠的是 warp 瓦片
#      不是推门，别当一键开门（_enter_building_door 的 interact 对它们无意义；2026-09-10 恒：矿井入口是 warp 不是门）。
#   作用：walk_to / map_go 落脚恰站在**真门**瓦片上时，补一次 interact 推门进屋，站在室内门口。
_REVERSE_DOORS = {}


_DOOR_WARP_ENTRANCES = {"Mine", "SkullCave", "FarmCave", "Tent", "Woods", "Tunnel", "Club"}


for _b, (_om, (_dx, _dy)) in locations.BUILDING_DOORS.items():
    if _b in _DOOR_WARP_ENTRANCES:
        continue
    _REVERSE_DOORS[(_om, (_dx, _dy))] = _b


def _locked_door_dialogue():
    """推门没推开时读一眼菜单：门禁/营业时间/好感/性别拦下时游戏**一定会说句话**。
    返回那句话的文本(str，可能空串)；没有弹窗 → None（=不是"门锁着"，是真·导航失败）。
    2026-09-10：用来把「门锁着」和「路走不到」分开——前者不该触发兜底 warp 硬闯。

    ⚠️ 2026-09-23 扩：**也认 `LetterViewerMenu`（信件）** —— 探险家公会那扇门的锁弹的是
       **信件**不是 DialogueBox（反编译 `StardewValley.Locations/Mountain.cs:74`：
       `!who.mailReceived.Contains("guildMember") && !who.hasQuest("16")` 时
       `Game1.drawLetterMessage(...)`）。只认 DialogueBox 的旧版**认不出它** ⇒ 被判成
       "真·导航失败" ⇒ 掉进兜底 `api.warp(nxt, ARRIVE[nxt])` **穿墙进公会**，
       把"杀 10 只绿史莱姆"的门禁整个绕掉（`locations.ARRIVE["AdventureGuild"] = (6,12)`）。
    """
    try:
        m = api._get("/menu")
        if not m.get("open"):
            return None
        t = m.get("type")
        if t == "DialogueBox":
            return (m.get("dialogue") or "").strip()
        if t == "LetterViewerMenu":
            title = (m.get("letterTitle") or "").strip()
            body = (m.get("letterBody") or "").strip()
            if title and body:
                return f"{title}：{body}"
            return title or body
    except Exception:
        pass
    return None


# ── 🚪 门禁记忆（恒 2026-09-23）──
# 推门被游戏挡回来（弹对话/信件）⇒ 记一笔 `(地图, 建筑英文名)`，**当天**不再在状态条
# `🗺️ 可:` 里推荐它——春2日 AI 一到 Mountain 就被告知"能去探险家公会"，可那门根本没开。
# **换天自动清空**（营业时间/好感/任务进度都可能过夜变好）。
# ⚠️ 只影响**显示**，不影响导航：那扇门本来就不该拦着 AI 去试——公会的锁门信正是
#    "去哪接杀绿史莱姆任务"的线索，拦住反而是错的。
_DOOR_BLOCKED = {"day": None, "keys": set()}
_DAYKEY_CACHE = {"ts": 0.0, "key": None}


def _day_key_cached(ttl: float = 30.0):
    """游戏日期键（30s TTL）——门禁记忆要按天清，但状态条不该为此每调多打一次 HTTP。"""
    now = time.time()
    if _DAYKEY_CACHE["key"] is not None and now - _DAYKEY_CACHE["ts"] < ttl:
        return _DAYKEY_CACHE["key"]
    try:
        k = api.day_key()
    except Exception:
        k = None                       # 读不到 → None；当天判据退化成"永不跨天清"，宁可不误伤
    _DAYKEY_CACHE.update(ts=now, key=k)
    return k


def _door_today() -> set:
    """"今天"那批推不开的门（跨天自动清空）。"""
    dk = _day_key_cached()
    if _DOOR_BLOCKED["day"] != dk:
        _DOOR_BLOCKED["day"] = dk
        _DOOR_BLOCKED["keys"] = set()
    return _DOOR_BLOCKED["keys"]


def door_blocked(loc: str = "") -> set:
    """本图今天"推门被挡回来"的建筑英文名集合；loc 留空 = 全部。"""
    try:
        keys = _door_today()
    except Exception:
        return set()
    return {b for (m, b) in keys if not loc or m == loc}


def mark_door_blocked(building: str, loc: str = "") -> None:
    """记一笔"这扇门现在进不去"（`loc` 留空 = 当前所在图）。只在 `🗺️ 可:` 里生效，不拦导航。"""
    if not building:
        return
    try:
        keys = _door_today()
        if not loc:
            loc = (api.state().get("location") or {}).get("name", "") or ""
        if loc:
            keys.add((loc, building))
    except Exception:
        pass


def map_feature_hidden(loc: str) -> set:
    """`locations.MAP_FEATURES[loc]` 里**现在用不了**的条目前缀（渲染 `🗺️ 可:` 时摘掉）。

    判据来自 `locations.MAP_FEATURE_GATES`：
      `map:X`  → `_locked_maps()`（/unlocks 权威，30s 缓存）里有 X 就藏
      `door:X` → 今天推过这扇门、被游戏挡回来过（`door_blocked()`）
    值可以是**一条依赖（str）或一组（list）**——list 里**任意一条**不满足就藏
    （例：探险家公会 = 塌方挡路 map:Mine **或** 门锁着 door:AdventureGuild）。
    ⚠️ **读不到一律不藏** —— 渲染侧出的错不该变成"把能去的地方也藏了"。
    """
    gates = getattr(locations, "MAP_FEATURE_GATES", {}).get(loc) or {}
    if not gates:
        return set()
    try:
        locked = _locked_maps()
    except Exception:
        locked = set()
    try:
        blocked = door_blocked(loc)
    except Exception:
        blocked = set()
    hidden = set()
    for token, dep in gates.items():
        for one in (dep if isinstance(dep, (list, tuple, set)) else [dep]):
            kind, _, target = str(one).partition(":")
            if kind == "map" and target in locked:
                hidden.add(token)
            elif kind == "door" and target in blocked:
                hidden.add(token)
    return hidden


def _step_into_building(arrive_map: str, arrive_pos) -> str:
    """落点在建筑**门瓦片**上 → 推门进屋（复用 _enter_building_door）。
    返回"…推门进屋"日志；非门瓦片 / 已在屋内 / 进屋失败 → 返回空串（不卡导航）。
    ⚠️ 不无脑进：只在 (arrive_map, 瓦片) 命中 _REVERSE_DOORS 且玩家仍在门外时触发。"""
    try:
        px, py = int(arrive_pos[0]), int(arrive_pos[1])
        b = _REVERSE_DOORS.get((arrive_map, (px, py)))
        if not b:
            return ""
        cur = (api.state().get("location") or {}).get("name", "")
        if cur != arrive_map:          # 已进门/不在门外 → 不重复进
            return ""
        # ⚠️ 2026-10-05：`_enter_building_door` 现在回三元组（`(成没成, 为什么, 门格)`）——
        #    这一处**只要"成没成"**：没成的话下面那条 `_locked_door_dialogue()` 会把
        #    游戏的原话（锁门/信件）如实转述出来，`why` 在这儿没有新增信息。
        _ok_b, _why_b, _ = _enter_building_door(b)
        if _ok_b:
            return f"，推门进屋已站在{b}室内门口"
    except Exception:
        return ""
    # 没进屋：大概率门锁着（未到营业时间/未解锁/好感不够）→ 游戏会弹对话或**信件**
    # （公会那扇门弹的就是信件，见 `_locked_door_dialogue`），顺手关掉别留菜单给 AI，并明确回报
    # （2026-09-10 恒：医院07:00门没开、弹了菜单）。
    # ⚠️ 关门之前**先把游戏的原话抄进回报里**：公会的锁门信正是"去哪接杀史莱姆任务"的唯一线索，
    #    关掉又不转述 = 把 AI 该看到的东西吃掉了（2026-09-23 扩信件时想通的）。
    txt = _locked_door_dialogue()
    if txt is not None:
        mark_door_blocked(b, arrive_map)     # 🚪 记一笔：今天 `🗺️ 可:` 别再推荐它
        try:
            api._post("/key", {"key": "ok"})
            api._post("/menu_close")
        except Exception:
            pass
        _txt = (txt or "").replace("\n", " ").strip()[:120]
        return (f"（~这扇门没开：{_txt}~）" if _txt
                else "（~这扇门没开，未到营业时间/未解锁~）")
    return ""


# ═══════════════════════════════════════════
#  🎫 买票旅行（2026-08-15 恒：巴士/姜岛船要真实交互买票，不 warp 直达）
# ═══════════════════════════════════════════
# (起点, 终点) → {machine: 售票机触发瓦片, stand: 站位(机子下方), face: 面向, wait: 等动画秒, note}
# 流程：走到 stand 站位 → 面向机子 → /interact（面前=machine）→ 对话框选"是" → 等游戏自动旅行到终点
# ⚠️ 2026-08-15 实测：巴士售票机触发瓦片=(17,11)，站位=(17,12)（恒校准"偏太上了"=要站下方朝上）；
#   船票触发瓦片=(4,9) 站位=(4,10)；等动画要耐心(~25s，恒：报失败前一秒才到)
TICKET_TRAVEL = {
    ("BusStop", "Desert"):         {"machine": (17, 11), "stand": (17, 12), "face": 0, "wait": 25, "note": "巴士票(500g)"},
    ("BoatTunnel", "IslandSouth"): {"machine": (4, 9),  "stand": (4, 10),  "face": 0, "wait": 25, "note": "姜岛船票(1000g)"},
}


def _ticket_select_yes(tries: int = 4) -> bool:
    """对话框里选"是/Yes/购买"选项（选完菜单消失/切换即算成功）。"""
    for _ in range(tries):
        try:
            m = api._get("/menu")
            for r in (m.get("responses") or []):
                t = (r.get("text") or "").strip().lower()
                if t in ("是", "yes", "买", "购买", "y", "上车", "上船"):
                    api.menu_click(option=r["index"])
                    return True
        except Exception:
            pass
        time.sleep(0.8)
    return False


def _ticket_travel(frm: str, nxt: str, tkt: dict) -> bool:
    """买票旅行：走到站位(机子下方) → 面向机子 → 交互 → 选"是" → 等游戏自动旅行到 nxt。"""
    try:
        mx, my = tkt["machine"]
        sx, sy = tkt.get("stand", (mx, my))
        face = tkt.get("face", 0)
        # 1. 走到站位（机子下方/面前）
        _walk_and_wait(frm, sx, sy, timeout=15)
        # ⚠️ 精确对齐站位（±2容差可能差1格→交互打偏；巴士站 walk_to 不可靠）→ 必须站到位
        try:
            p = api.state().get("player", {})
            if (p.get("x"), p.get("y")) != (sx, sy):
                api._post("/position", {"x": sx, "y": sy})
                time.sleep(0.6)
        except Exception:
            pass
        # 走位途中可能已触发上车 → 直接看是否到终点
        if api.state().get("location", {}).get("name", "") != frm:
            return api.state().get("location", {}).get("name", "") == nxt
        # 2. 面向机子 + 显式交互 machine 瓦片（防朝向偏差打偏；恒：右键任意位置能弹窗，API 要精确）
        api._post("/face", {"direction": face})
        time.sleep(0.3)
        api._post("/interact", {"x": mx, "y": my})
        time.sleep(1.5)
        # 3. 选"是"
        if not _ticket_select_yes():
            return False
        # 4. 等自动旅行（巴士/船动画+加载，2026-08-15 恒：要等小动画）
        #    ⚠️ 动画/加载期间 /state 可能短暂报错或未换图——容错轮询，别被异常打断
        deadline = time.time() + tkt.get("wait", 25)
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                if api.state().get("location", {}).get("name", "") == nxt:
                    return True
            except Exception:
                pass  # 加载中 /state 报错 → 继续等
        # 5. 动画可能还在放：再多等一会兜底
        for _ in range(8):
            time.sleep(1.5)
            try:
                if api.state().get("location", {}).get("name", "") == nxt:
                    return True
            except Exception:
                pass
        try:
            return api.state().get("location", {}).get("name", "") == nxt
        except Exception:
            return False
    except Exception:
        return False


# ═══════════════════════════════════════════
#  🚌 沙漠返程（2026-09-21 恒）：**原生交互优先**，warp 只做兜底
# ═══════════════════════════════════════════
# 现状：Desert→BusStop 是 `locations.MAP_LINKS["Desert"]` 里一条 `kind="warp"`，
#   `_map_go_walk` 交给 `_walk_trigger_warp`（走位到出口站格 → `/warp` 跳）——
#   这是当初为了"稳定触发"选的捷径（同 `locations.py:639` 那条"AI 不用原生 warp 触发(不稳定)"）。
# 恒真机观察：走到沙漠巴士上车点，**经过时弹原生交互对话框的概率挺大**——
#   人既然已经被游戏拉住问话了，就**顺着它走原生那条路**，别再绕开它硬 warp：
#     walk_to 到站台 → 检测到原生菜单 → 选 option 0（是/否类框的"是"）→ 等 ~10s 动画 → 人已在 BusStop。
# 没弹菜单 / 没走成 → 返回 False，调用方照旧 warp 兜底（不卡死；写法同 `TICKET_TRAVEL` 的兜底）。
# ⚠️ 只挂**返程**：去程 BusStop→Desert 有售票机，走 `TICKET_TRAVEL`（那边是恒 2026-08-15 校准过的）。
BUS_RETURN = {
    ("Desert", "BusStop"): {"stand": (18, 27), "bus": (18, 26), "wait": 10, "note": "沙漠巴士返程"},
}


def _bus_return_travel(frm: str, nxt: str, cfg: dict) -> bool:
    """沙漠返程：走到巴士站台 → 等原生菜单弹出 → 选 option 0（"是"）→ 等动画到 nxt。

    返回 True  = **真靠原生交互回**到了 nxt（调用方别再 warp）；
    返回 False = 没弹菜单 / 没走成（调用方照旧 warp 兜底）。
    """
    def _cur() -> str:
        try:
            return (api.state().get("location") or {}).get("name", "")
        except Exception:
            return ""

    def _settle() -> bool:
        """点完选项 → 先等 `wait` 秒动画（恒点名 10s），再多等一会防加载慢。"""
        time.sleep(cfg.get("wait", 10))
        deadline = time.time() + 25
        while time.time() < deadline and _cur() != nxt:
            time.sleep(1.0)
        return _cur() == nxt

    def _probe(probe_s: float) -> bool:
        """在 probe_s 秒内等原生菜单弹出并作答；返回"人是否已到 nxt"。"""
        deadline = time.time() + probe_s
        while time.time() < deadline:
            cur = _cur()
            if cur == nxt:
                return True            # 走位途中被游戏自己送回去了（那格本就是返程出口）
            if cur != frm:
                return False           # 到了别的图 → 交回兜底
            try:
                m = api._get("/menu") or {}
            except Exception:
                m = {}
            if m.get("open"):
                rs = m.get("responses") or []
                if rs:
                    # 恒点名：是/否类框的 **option 0 就是"是"**；能按文本认出来就按文本认，认不出就用 0。
                    idx = rs[0].get("index", 0)
                    for r in rs:
                        if (r.get("text") or "").strip().lower() in ("是", "yes", "y", "上车", "坐车"):
                            idx = r.get("index", 0)
                            break
                    api.menu_click(option=idx)
                else:
                    api._post("/key", {"key": "ok"})   # 纯文本 DialogueBox（没选项）→ 推一下 ok
                return _settle()
            time.sleep(0.8)
        return False

    try:
        # 1. 走到巴士站台（下车/上车点 (18,27)）
        sx, sy = cfg["stand"]
        _walk_and_wait(frm, sx, sy, timeout=20)
        if _cur() == nxt:
            return True
        if _probe(6.0):
            return True
        # 2. 站台没弹 → 再往巴士格 (18,26) 上靠一步：那是 /warps 里的返程出口，
        #    踩上去要么弹框（我们接住）、要么游戏直接把人送走（那也到 BusStop 了）。
        bx, by = cfg.get("bus", (sx, sy))
        _walk_and_wait(frm, bx, by, timeout=10)
        if _cur() == nxt:
            return True
        return _probe(6.0)
    except Exception:
        return False


def _exit_farm_building(frm: str, nxt: str) -> bool:
    """室内(农场建筑: 小屋/农舍/洞穴/温室等)→室外。
    ✍️ 2026-08-30 恒改：**以 /map 读到的室内真实出口 warp 瓦片为核心**，不再依赖 /farm_buildings 的
    indoorsName 匹配——那套名字体系跟 /state 报的室内名对不上（室内名="FarmHouse"/"Cabin"，而
    /farm_buildings 里主屋 indoorsName=None、小屋 indoorsName="Cabin"），导致 out_door 匹配失败、
    掉进 ARRIVE 兜底瞬移（= 从床上瞬移到农场上口/河边）。

    新流程（与固定地图 _walk_trigger_warp 同一套衔接）：
      1. /map 读室内通往 nxt 的 warp 瓦片 (wx,wy) + 落点 (tx,ty)（游戏原生定义）
      2. walk_to 走到瓦片旁一格（自然走路到门口）
      3. /warp 到 (tx,ty) 出门（外部落点，地图框架通用）
    任何室内建筑通用，名字对不上也能正确出门。"""
    try:
        # ⚠️ /map 读的是**当前**室内进程的 warp（player.currentLocation），不是跨图读——
        #    本函数只在"人在室内、要出去"时调用，故直接读当前图即可。
        wx = wy = tx = ty = None
        try:
            m = api._get('/map')
            for w in (m.get('warps') or []):
                if w.get('targetLocation') == nxt:
                    wx, wy = w['x'], w['y']
                    tx, ty = w.get('targetX'), w.get('targetY')
                    break
        except Exception:
            pass
        if wx is None or tx is None:
            return False  # 室内没有通往 nxt 的原生 warp → 交给调用方处理

        # 门前可走格：挑「离玩家最近」的门瓦片邻格。
        # ⚠️ 2026-08-30 恒：原按 (0,1)=下 优先，会把玩家领到门的对面/更外侧，走路必穿过门瓦片
        #   (= 穿墙一两步)。改挑"离玩家最近的可走邻格" → 玩家在门哪一侧就停哪一侧门口站定 → /warp，
        #   不穿门/墙折返。若玩家根本不在邻格(远在房间另一侧)，最近邻格也即其同侧那格，自然走过去再跳。
        px = api.state().get("player", {}).get("x", 0)
        py = api.state().get("player", {}).get("y", 0)
        approach = None
        best = None
        for dx, dy in ((0, 1), (0, -1), (-1, 0), (1, 0)):
            cand = (wx + dx, wy + dy)
            try:
                if api._post('/passable', {'x': cand[0], 'y': cand[1]}).get('passable'):
                    score = abs(cand[0] - px) + abs(cand[1] - py)
                    if best is None or score < best:
                        approach, best = cand, score
            except Exception:
                pass
        # walk_to 走到门前可走格（自然走路；BFS 失败则内部退化为临近可站落点，不飞墙外）
        if approach:
            try:
                _walk_and_wait(frm, approach[0], approach[1], timeout=20)
            except Exception:
                pass
        # 面向门（warp 瓦片方向）再显式 /warp 出门（落点用游戏原生 targetX/targetY）
        try:
            face = 2  # 默认朝下；按 approach 相对门瓦片方向推断更准
            if approach and wx is not None and wy is not None:
                if approach[1] < wy: face = 2   # 站在门上方 → 脸朝下(进门方向)
                elif approach[1] > wy: face = 0 # 站在门下方 → 脸朝上
                elif approach[0] < wx: face = 1 # 站门左 → 脸朝右
                elif approach[0] > wx: face = 3 # 站门右 → 脸朝左
            api._post("/face", {"direction": face})
        except Exception:
            pass
        api.warp(nxt, tx, ty)
        time.sleep(1.5)
        return api.state().get("location", {}).get("name", "") == nxt
    except Exception:
        pass
    return False


def _walk_trigger_warp(frm: str, nxt: str, ex: int, ey: int, wx: int, wy: int, exact: bool = False) -> bool:
    """可靠版传送（恒 2026-08-13 拍板）：走到出口"前一格"（可达自然走）→ 确认人到 → /warp。
    ⚠️ 实测：walk_to 到 warp 瓦片本身(53,110)或往中心偏移(52,109)会 position 瞬移；
       只有到"边缘法线往内 1 格"(53,109)才自然走。别踩 warp 瓦片（/warp 会锁）。
    ✍️ 2026-08-30 恒：exact=True 时按调用方标的 (ex,ey) **直接走**（不做过往退格换算）——
       locations.MAP_LINKS 现在把出口 tile 标成**地图内可达格**（如 Farm→Backwoods (40,1)），
       走到那一格站定再 /warp 跳，避免"出口在地图外(y=-1)边界换算"把 AI 引到错格/瞬移。
    返回是否已到达 nxt。"""
    locinfo = api.state().get("location", {})
    mw = locinfo.get("mapWidth", 80)
    mh = locinfo.get("mapHeight", 65)
    if exact:
        bx, by = ex, ey
        # 🛡️ 2026-09-10 恒：exact 标来的瓦片**可能是图外格**——SDV 常把出口画在边界外一格
        #    （浴场大厅 (5,10) 图只有 10×10；更衣室 (13,28)/(2,28) 图只有 18×28）。
        #    直接交给 /walk_to 会"越界 ok:false" → **整段导航直接失败**（大厅走不出去就是这么来的）。
        #    统一夹回图内（下缘→mh-1、上缘→0、右缘→mw-1、左缘→0），站住再 /warp 模拟。
        try:
            _mw, _mh = int(mw or 0), int(mh or 0)
            if _mw > 0 and _mh > 0:
                _cx = min(max(int(bx), 0), _mw - 1)
                _cy = min(max(int(by), 0), _mh - 1)
                if (_cx, _cy) != (bx, by):
                    bx, by = _cx, _cy
        except Exception:
            pass
    else:
        # 出口"前一格" = 沿边缘法线往地图内退 1 格（保证可达 + 非 warp 瓦片）
        if ey >= mh - 2:
            bx, by = ex, ey - 1       # 下边缘 → 上方一格
        elif ey <= 1:
            bx, by = ex, ey + 1       # 上边缘 → 下方一格
        elif ex >= mw - 2:
            bx, by = ex - 1, ey       # 右边缘 → 左方一格
        elif ex <= 1:
            bx, by = ex + 1, ey       # 左边缘 → 右方一格
        else:
            # 地图内 warp（如 BusStop 44,22 去 Town）：往地图中心退一格
            bx = min(max(ex + (1 if ex < mw // 2 else -1), 0), mw - 1)
            by = min(max(ey + (1 if ey < mh // 2 else -1), 0), mh - 1)
    # 0. 等角色完全停下（⚠️ 全程跑时刚 /warp 到达还在移动，walk_to 会失败→瞬移兜底。恒 2026-08-13）
    for _ in range(20):
        st = api.state()
        if not st.get("player", {}).get("isMoving", False):
            break
        time.sleep(0.4)
    # 1. 走到出口前一格——统一用 /walk_to（恒 2026-08-13 拍板）
    try:
        px = api.state().get("player", {}).get("x", 0)
        py = api.state().get("player", {}).get("y", 0)
        dist = abs(bx - px) + abs(by - py)
    except Exception:
        dist = 20
    walk_timeout = min(max(25, int(dist * 0.5) + 10), 60)
    # ⚠️ 等**回包里的那个坐标** —— `/walk_to` 会把"站不住"的出口格就近改掉（码头 (17,44)→(20,44)
    #    就是这一条让轮回白站了 25 秒）。详见 `_walk_and_wait`。
    _walk_and_wait(frm, bx, by, timeout=walk_timeout)
    # 2. 人到位置了 → /warp 下一图入口
    # ⚠️ 2026-08-23 恒：赌场这类「建筑室内」（Club/SandyHouse 内室）普通 /warp 进不去
    #    （Game1.warpFarmer 对建筑内部切不动）→ 回退 /warp_into（同步直切 currentLocation）。
    if wx is not None and wx >= 0 and wy is not None and wy >= 0:
        api.warp(nxt, wx, wy)
    else:
        api.warp(nxt)
    # 🎪 节日路由例外（2026-09-13 真机抓到，**两个 bug 叠在一起**，恒："还是进不来？什么情况？"）
    #   节日当天去**逻辑场地**（spring13 的 "Town"），游戏会**故意拦下我们的 warp**、走它自己的路由：
    #   `Game1.warpFarmer` 被 `whereIsTodaysFest` 拦住 → ReadyCheckDialog → 事件临时图 **`Temp`**。
    #   ⇒ ① 落地是 **`Temp`，永远不等于 `"Town"`**，原来的 `loc == nxt` 判定**必然判失败**（明明成功了）；
    #      ② 判失败后走 `/warp_into` 兜底 —— 那是 `WarpDirect` **同步直切 `currentLocation`**，
    #         **绕过游戏路由** ⇒ 把人**从节日里踢出去、塞进普通 Town**（`activeEvent=null`，
    #         跟恒所在的 `Temp` 是两张图、互相看不见）。
    #   **所以现象不是"进不来"，是"进来了又被打出去"**（恒 2026-09-13 复现两次）。
    #   ⇒ 节日场地：**等久一点**（游戏要弹确认框+换临时图）、**认事件起来当成功**、**绝不跑 /warp_into**。
    fest = _is_today_festival_dest(nxt)
    _trace = []
    for _i in range(20 if fest else 3):
        time.sleep(0.6)
        st = api.state()
        loc = (st.get("location") or {}).get("name", "")
        _trace.append(f"[{_i}] loc={loc!r} ev={(st.get('activeEvent') or {}).get('id')!r}")
        if loc == nxt:
            return True
        # 🎪 **人在节日临时图（Temp）里，本身就是"到了"** —— 游戏已经把人路由进去了。
        #   ⚠️ 2026-09-13 真机坐实（三轮）：只认 `loc == nxt` 时 `Temp ≠ "Town"` ⇒ 判失败 ⇒
        #      上层兜底**又补一发 `warpFarmer("Town")`**（不是 `warp_into` 的直切——那不会打
        #      `Warping to Town` 日志行）⇒ 人已经在节日里，游戏这回**不拦了**、当成"离场"⇒
        #      **丢回普通 Town `(0,54)`、`activeEvent=null`**。
        #   真机现场：进 Temp 后**原地干等 20 秒**（就是这个循环跑满 20 轮）才被踢 ——
        #   恒："为什么导航延迟收工。按理说到达目的地就应该叫你，但直到被踢你都没动静"。
        #   Temp 只在节日期间存在、不是任何正常导航目的地 ⇒ 进了它**按定义就是到达**。
        if loc in _FESTIVAL_TEMP_MAPS:
            return True
        # 🎪 换到了事件临时图（Temp）且节日事件真的起来了 = 游戏路由成功
        if fest and (st.get("activeEvent") or {}).get("id"):
            return True
    print(f"🎪 [festival-warp-wait] nxt={nxt!r} fest={fest} 跑满 {len(_trace)} 轮: "
          + " | ".join(_trace), flush=True)
    if fest:
        # ⚠️ **绝不 /warp_into**：那是绕过游戏路由直切，会把刚被路由进去的人打回普通 Town。
        #   走到这 = 游戏没路由（没开赛/不在时段/路由本身出问题）——如实报，让 AI 用 festival go。
        return False
    # 兜底：普通 warp 失败（建筑室内）→ /warp_into 同步直切
    try:
        api.warp_into(nxt, wx if wx >= 0 else None, wy if wy >= 0 else None)
        time.sleep(0.3)
        if api.state().get("location", {}).get("name", "") == nxt:
            return True
    except Exception:
        pass
    return False


def _is_today_festival_dest(dest: str) -> bool:
    """🎪 dest 是不是**今天的节日场地**（逻辑图名：spring13 → "Town"）。

    为什么单独一个判据：节日当天游戏会**故意拦下我们的 warp**、走它自己的路由
    （ReadyCheckDialog → 事件临时图 `Temp`）。这时"没到 `Town`"**不是失败**，
    而 `/warp_into` 那种直切兜底会**绕过路由**把人打回普通图（见 `_walk_trigger_warp` 那段）。
    ⚠️ 判据来源与 `festival go` 同一张表（`calendar_data.FESTIVAL_LOCATIONS`），别另攒名单
    （「判据别放消费侧猜」+「别维护白名单」，2026-09-12 恒）。
    读不到时间 ⇒ 返回 False（**按老行为走**，不误伤非节日导航）。
    """
    try:
        t = (api.state().get("time") or {})
        key = (str(t.get("season") or "").lower(), int(t.get("dayOfMonth") or 0))
        return calendar_data.FESTIVAL_LOCATIONS.get(key) == dest
    except Exception:
        return False


def _player_is_male():
    """角色性别（True/False）；旧 DLL 没这字段 → None。
    来源：`/state` 的 `player.isMale`（2026-09-10 恒：浴场性别门禁要按性别选门）。"""
    try:
        v = (api.state().get("player") or {}).get("isMale")
        return None if v is None else bool(v)
    except Exception:
        return None


def _link_allowed(link: dict) -> bool:
    """🚻 按角色性别过滤 MAP_LINKS 的边（2026-09-10 恒）。

    病根：浴场大厅有**两扇外观一样的性别门**——女 `(2,3)→BathHouse_WomensLocker`、
    男 `(7,3)→BathHouse_MensLocker`。BFS 谁排在前面就走谁（女门在前），**male 角色会被带去女门**
    然后被门禁拒（真机：雪落被拦在 "这是女更衣室……你不能进去！"）。

    ⚠️ **必须在规划阶段就滤掉，不能"被拒了再换一扇"**：进错更衣室会改变后面**整条路线**
    （女更衣室→泳池走 `(2,27)`、男更衣室走 `(15,27)`，落点图都不一样）。

    link 里 `gender` 缺省 ⇒ 谁都能走（行为与以前完全一致）；
    读不到玩家性别（旧 DLL）也放行，不误伤。
    """
    g = link.get("gender")
    if not g:
        return True
    m = _player_is_male()
    if m is None:
        return True
    return (g == "male") == m


def _map_bfs(from_loc: str, to_loc: str):
    """在 MAP_LINKS 图上 BFS 找最短路径。返回 [(起点, 目标, link), ...] 或 None。
    ⚠️ 2026-08-30 恒：from==to 时直接返回 []（空路径=原地不动）——不然 BFS 会找
    Farm→BusStop→Farm 这种自环，把 AI 绕地图跑一圈（"出门第一步就乱走"根因）。"""
    if from_loc == to_loc:
        return []
    graph = {}
    for src, links in locations.MAP_LINKS.items():
        for l in links:
            if not _link_allowed(l):      # 🚻 性别门禁：滤掉不对的那扇门（见 _link_allowed）
                continue
            graph.setdefault(src, []).append(l)
    visited = {from_loc}
    queue = [(from_loc, [])]
    while queue:
        cur, path = queue.pop(0)
        for link in graph.get(cur, []):
            nxt = link["target"]
            if nxt == to_loc:
                return path + [(cur, nxt, link)]
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, path + [(cur, nxt, link)]))
    return None


# ⚠️ 未解锁地点（2026-08-14 #13）：map_go 不允许导航到未解锁地点。
# key 对应 /unlocks 的返回键；旧 DLL 无 /unlocks 时兜底放行（不误伤）。
LOCKED_MAPS = {
    "Mine": ("mine", "春5日收到信后可进矿洞"),
    "Desert": ("bus", "修好巴士（社区中心金库/Joja 42,500g）"),
    "Sewer": ("sewer", "博物馆捐60个古物获得生锈钥匙"),
    "Woods": ("secretWoods", "升级到钢斧（砍大木桩）"),
    "SkullCave": ("skullCavern", "到达矿井底部120层获得头骨钥匙"),
    "IslandWest": ("island", "完成社区中心后找威利修船"),
    "IslandNorth": ("island", "完成社区中心后找威利修船"),
    "IslandSouth": ("island", "完成社区中心后找威利修船"),
    "IslandEast": ("island", "完成社区中心后找威利修船"),
    "IslandNorthCave": ("island", "完成社区中心后找威利修船"),
    "GingerIsland": ("island", "完成社区中心后找威利修船"),
    "Railroad": ("railroad", "夏3日地震后开放"),
    "Club": ("casino", "完成神秘的齐任务线获得会员卡"),
    "Summit": ("summit", "100%完美达成"),
    "MasteryCave": ("mastery", "钓鱼/采集/战斗/挖矿/耕种全10级"),
    "Greenhouse": ("greenhouse", "完成社区中心储藏室/Joja温室"),
    "MovieTheater": ("cinema", "完成Joja路线/特殊献祭解锁电影院"),
    "WitchSwamp": ("witchSwamp", "完成黑暗护身符任务（法师）"),
    "WitchHut": ("witchSwamp", "完成黑暗护身符任务（法师）"),
}


_UNLOCK_CACHE = {"ts": 0.0, "locked": None}


def _locked_maps() -> set:
    """当前未解锁的地图名集合（查 /unlocks → LOCKED_MAPS 映射）。
    TTL 30s 缓存（map_lookup/query/go 都查）；读不到/旧 DLL → 空集（不误伤）。"""
    global _UNLOCK_CACHE
    if _UNLOCK_CACHE["locked"] is not None and time.time() - _UNLOCK_CACHE["ts"] < 30:
        return _UNLOCK_CACHE["locked"]
    locked = set()
    try:
        u = api.unlock_status()
        unlocks = u.get("unlocks") or {}
        for m, (key, how) in LOCKED_MAPS.items():
            if not (unlocks.get(key) or {}).get("unlocked"):
                locked.add(m)
    except Exception:
        locked = set()  # 读不到 → 不误伤
    _UNLOCK_CACHE = {"ts": time.time(), "locked": locked}
    return locked


# 🧱 钱包物品门禁（2026-08-23 恒：矮人商店=学会矮人语教程）
# 与钥匙/护身符同一检测源：读 AI 进程(7843) /unlock_debug 的 relevantMail(=mailReceived=背包的钱包)，查 flag。
# ⚠️ AI 进程(7843)是权威端（MasterPlayer=房主）；wallet=每角色自己的 mailReceived（2026-08-23 恒：读7843才对）。
# TTL 30s 缓存；读不到/旧 DLL → False（不误伤，藏而不拦）。
_WALLET_CACHE = {"ts": 0.0, "flags": None}


def _wallet_flag_present(flag: str) -> bool:
    """钱包里是否有指定 flag（如 HasDwarvishTranslationGuide=学会矮人语教程）。
    和 HasRustyKey/HasSkullKey/HasDarkTalisman 同处 mailReceived，检测源一致。"""
    global _WALLET_CACHE
    if _WALLET_CACHE["flags"] is not None and time.time() - _WALLET_CACHE["ts"] < 30:
        return flag in _WALLET_CACHE["flags"]
    flags = set()
    try:
        u = api.unlock_debug()
        flags = set(u.get("relevantMail") or [])
    except Exception:
        flags = set()  # 读不到 → 不误伤
    _WALLET_CACHE = {"ts": time.time(), "flags": flags}
    return flag in flags


# 🧱 矮人商店堵路石门禁（2026-08-23 恒）：矿洞矮人商店前固定可破坏石头（(BC)78 圆石，档档同在 Mine(27,8)，
# 炸掉后不再生）。未炸=走不到矮人（拟人/受限：AI 不能 position 穿墙）；炸掉=通路。
# 读 /mine_rock（C# cross-map，不依赖玩家位置）。TTL 30s；读不到 → False（不误伤放行）。
_ROCK_CACHE = {"ts": 0.0, "blocked": None}


def _dwarf_rock_blocked() -> bool:
    """矮人商店堵路石是否还在（未炸=True → 门禁拦）。炸掉(object 消失)/读不到 → False。"""
    global _ROCK_CACHE
    if _ROCK_CACHE["blocked"] is not None and time.time() - _ROCK_CACHE["ts"] < 30:
        return _ROCK_CACHE["blocked"]
    blocked = False
    try:
        r = api.host_mine_rock()
        blocked = bool(r.get("blocked"))
    except Exception:
        blocked = False  # 读不到 → 不误伤
    _ROCK_CACHE = {"ts": time.time(), "blocked": blocked}
    return blocked


# 🚧 Mountain 上「塌方以东」那片（2026-09-23 恒真机：AI 直接穿过了山体塌方的大石头）。
#    病根：游戏把塌方**只写在 `Mountain.isCollidingPosition` / `isTilePlaceable`** 里
#    （反编译 `StardewValley.Locations/Mountain.cs:352`，`landslide = DaysPlayed < 5`），
#    **瓦片层（isTilePassable）完全没有它** ⇒ 我们的 BFS/`/passable` 判它"可走"，
#    于是规划出一条穿石头的路。拿游戏自己的 `/passable_rect` 跑连通域实测：
#      塌方当可走 → (15,40) 与 公会站格(76,9)/矿井口(54,4) **同一个连通域**（1610 格）；
#      塌方当墙   → 公会/矿井口**整块孤立**（(15,40) 那侧只剩 1226 格）。
#    ⇒ 不是"绕一绕能到"，是真的去不了（与矿井同一天解除：`landslide = DaysPlayed<5`，
#      而 `unlocks.mine` 写的正是"春5日收到信后可进矿洞"）。所以判据直接复用 `unlocks.mine`。
#    ⚠️ 这是**拦导航**，不是藏显示——`🗺️ 可:` 那边走 `locations.MAP_FEATURE_GATES`（同步加了一条）。
#    ⛏️ 根治要动 C#（让寻路的可走性判据也认这类"运行时碰撞矩形"），已记账待批。
_MOUNTAIN_BEHIND_LANDSLIDE = {"Mine", "AdventureGuild"}


def _map_go_unlock_check(dest: str) -> str:
    """未解锁地点 / 塌方挡路 → 返回拦截串；能去/不在表/读不到 → 空串放行。"""
    if dest in _MOUNTAIN_BEHIND_LANDSLIDE and "Mine" in _locked_maps():
        # ⚠️ 2026-09-25 恒：「railroad 有门禁，特定天数后才解锁」—— 对，**夏3日**地震才清石堆，
        #    而**温泉就在石堆后面** ⇒ 原来那句"现在能去的是…**温泉**"是**在骗人**
        #    （出处 `ModEntry.cs` 的 `RuntimeBlockers` 表：`railroadAreaBlocked`/`railroadBlockRect`）。
        #    铁路通了才把温泉列进"能去的地方"。
        try:
            _t = api.state().get("time") or {}
            _rail = locations.railroad_open(_t.get("season"), _t.get("dayOfMonth"), _t.get("year"))
        except Exception:
            _rail = False
        _extra = "/温泉" if _rail else ""
        return (f"❌ 去不了 {dest}：Mountain 那条路上**山体塌方还堵着**——"
                f"矿井口/探险家公会那片与农场侧是断开的（春5日通了才能过去，和矿井同一天）。\n"
                f"   现在能去的是山西南侧：罗宾木匠店(ScienceHouse)/山湖钓点/莱纳斯帐篷{_extra}。"
                f"急事请让 user 帮忙。")
    if dest not in LOCKED_MAPS:
        return ""
    if dest not in _locked_maps():
        return ""
    key, how = LOCKED_MAPS[dest]
    return f"❌ {dest} 未解锁（{how}），不能 map_go；解锁后再来"


def _is_mine_loc(loc) -> bool:
    """是否在矿井/火山里（火山门禁的陪同判定）。Mine/SkullCave/UndergroundMine*/Volcano*/Caldera。"""
    if not loc:
        return False
    if loc in ("Mine", "SkullCave", "Caldera"):
        return True
    return loc.startswith("UndergroundMine") or loc.startswith("Volcano")


def _is_volcano_interior(map_name: str) -> bool:
    """是不是「火山内部层」（VolcanoDungeon1~9）—— **只有这些层**需要 host 陪同。

    2026-09-12 恒修正规则：「入口层其实应该放行的，顶层也放行。只是火山内部9层需要陪同」。
    命名（`/warps` 实测）：入口层 = `VolcanoDungeon0`，往下一层 `VolcanoDungeon1` … 第 10 层 = `VolcanoDungeon9`，
    再往上是山顶 `Caldera`。入口层只是"进洞第一屏"、山顶也不是层，**都没有换层难题**。
    ⚠️ 原来门禁用 `dest.startswith("Volcano")` 一把梭，把**入口层和火山口也一起拦了** ——
       结果连"从山顶挪回入口层"这种正当动作都被挡（2026-09-12 真机撞到）。改判层号。
    ⚠️ 不引 `re`（本文件没导入过），手工解析尾号。"""
    s = map_name or ""
    if not s.startswith("VolcanoDungeon"):
        return False
    tail = s[len("VolcanoDungeon"):]
    return tail.isdigit() and int(tail) >= 1


# ⛏️ 每日第一次到【矿井入口层】的叮咛（2026-09-06 恒）：工具/雕像/清包/占位物 4 件事。
#    ⚠️ 只在"入口层"弹：Mine(城镇1层厅)/SkullCave(沙漠121)/VolcanoDungeon0(火山入口层)。
#      已在矿内深层(UndergroundMine*/VolcanoDungeon1+)不算"入口层"，不重复弹。
#    ⚠️ 用文件持久化——服务重启（本仓库常发生）后当天不重复提醒。key 按矿型分，三种矿每天各一次。
_MINE_ENTRY_STATE_FILE = os.path.join(SCRIPT_DIR, "mine_entry_reminder.json")


def _mine_entry_reminder(loc: str) -> str:
    """每天第一次到某类矿井入口层时返回叮咛文本（①适用tool ②有雕像才摸 ③清包必带+黑炸弹 ④占堆叠格）；
    不在入口层 / 已提醒过 → 返回 ''。只认入口层，矿内深层不算。"""
    if not loc:
        return ""
    if loc == "Mine":
        key, name, tip = "mine", "普通矿井(鹈鹕镇)", "`mine go`（mode：rush=下矿冲层 / farm=刷矿刷指定矿石 ore=Copper铜/Iron铁/Gold金）"
    elif loc == "SkullCave":
        key, name, tip = "skull", "头骨矿洞(沙漠)", "`mine bomb_mine` 自主炸矿（头骨从 121 层开始）"
    elif loc == "VolcanoDungeon0":
        key, name, tip = "volcano", "火山矿洞", "`mine bomb_volcano` 火山炸矿（需房主陪同，特殊瓦片不能程序化换层）"
    else:
        return ""   # 已在矿内深层 / 不在矿井：不算"入口层"，不弹
    _today = time.strftime("%Y-%m-%d")
    try:
        with open(_MINE_ENTRY_STATE_FILE, encoding="utf-8") as f:
            st = json.load(f) or {}
    except Exception:
        st = {}
    if st.get("day") == _today and key in st.get("shown", []):
        return ""   # 今天该矿型已叮咛过
    st["day"] = _today
    st.setdefault("shown", [])
    if key not in st["shown"]:
        st["shown"].append(key)
    try:
        with open(_MINE_ENTRY_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return (
        f"\n📌 第一次到「{name}」入口层，叮咛一次（每天每个矿型仅一次）：\n"
        f"  ① 此处适用tool：{tip}。\n"
        f"  ② 若场景有矮人雕像——先摸雕像拿每日增益（有的场景才有，没有就跳过）。\n"
        f"  ③ 整理好背包、带尽量少的东西；必带品：食物、镐子、武器；炸矿带炸弹（黑>超级>樱桃，约两百黑炸弹或等效）。\n"
        f"  ④ 怕捡拾不及时，包包可先带目标战利品占堆叠格（如一颗铱矿/铱锭/放射性矿石/放射性锭/五彩碎片/钻石）——"
        f"别带银河之魂（太珍贵，死了会丢）；死若丢东西，去马龙领回重要物品（如武器等）。"
    )


def _volcano_gate() -> str:
    """火山门禁（2026-08-16 恒）：火山特殊瓦片无法程序化换层 → host(7842) 不在矿井/火山里就拦，
    要求 AI 请 user 陪同。消息用 host 真实名字（自适应，不写死角色名）。放行返回 ""。
    ⚠️ host 状态读不到（7842 掉线/超时）→ 默认拦（安全优先：宁可多问 user 一次，不冒险放 AI 独进火山）。"""
    try:
        hs = api.host_state()
        hloc = (hs.get("location") or {}).get("name", "")
        if _is_mine_loc(hloc):
            return ""
        hname = (hs.get("player") or {}).get("name") or "房主"
        return (f"❌ 火山矿井特殊瓦片无法程序化换层，需要 {hname} 在矿井/火山陪同才能进入——"
                f"请先 ask user（{hname}）进矿井，再开火山脚本")
    except Exception:
        return ("❌ 火山门禁无法确认房主位置（7842 未响应）——火山需要房主陪同才能进入，"
                "请先 ask user 进矿井再试")


# ═══════════════════════════════════════════
#  🗼 图腾柱 / 🚂 矿车 交通优先级（2026-08-16 恒：图腾柱 > 矿车 > 走路）
# ═══════════════════════════════════════════
# 农场图腾柱是动态建筑（/farm_buildings 实时定位，type/x/y/width/height）。
# 类型 → 落点地图；岛柱落 IslandSouth 枢纽，全岛+火山由 map_go BFS 续走。
#
# 🎯 2026-09-12 恒：「7843 现在站到的这个点应该就是水之图腾柱交互后的到达点」—— **对**，
#    而且四个落点游戏代码里全写着（**不用凭空猜**）：
#    `Building.TryPerformObeliskWarp`（StardewValley.Buildings/Building.cs:1011）：
#      "Desert Obelisk" → PerformObeliskWarp("Desert",      35, 43)
#      "Water Obelisk"  → PerformObeliskWarp("Beach",       20,  4)  ← 轮回当时正站 Beach(20,4)，现场对上
#      "Earth Obelisk"  → PerformObeliskWarp("Mountain",    31, 20)
#      "Island Obelisk" → PerformObeliskWarp("IslandSouth", 11, 11)
#    ⚠️ 落点**写死在游戏里**（跟柱子摆哪无关）⇒ 可以当常量用，**不必等柱子盖起来才能验**
#      —— 这正是恒那句"7843 站在落点上"的价值：它的来源是游戏代码，现场只是**对上了**。
#    用途：①路线日志写明"落在哪"（落点非 dest 时人/AI 一眼知道还要走多远）
#          ②以后要判"坐柱值不值"（落点→目的地 距离 vs 纯走）就靠它。
OBELISK_TARGETS = {
    "Earth Obelisk":  {"dest": "Mountain",    "label": "山岭图腾柱(→山)", "land": (31, 20)},
    "Water Obelisk":  {"dest": "Beach",       "label": "海滩图腾柱(→海滩)", "land": (20, 4)},
    "Desert Obelisk": {"dest": "Desert",      "label": "沙漠图腾柱(→沙漠)", "land": (35, 43)},
    "Island Obelisk": {"dest": "IslandSouth", "label": "姜岛图腾柱(→岛)", "land": (11, 11)},
}


# 岛柱可达地点（落 IslandSouth 后由 map_go 从枢纽续走）：全岛地图 + 火山（走门禁）
ISLAND_MAPS = {
    "IslandSouth", "IslandWest", "IslandEast", "IslandNorth",
    "IslandFarmHouse", "QiNutRoom", "IslandFarmCave", "IslandShrine",
    "IslandHut", "IslandNorthCave1", "IslandFieldOffice",
    "VolcanoEntrance", "VolcanoDungeon0",
}


# 落点图近处建筑（也走图腾柱，落点续走 1 段；只收落点近邻，别收 Town 这类远走的）
OBELISK_NEARBY = {
    # ⚠️ 2026-09-10 恒：浴场也加进来——原来漏了，农场去泡澡白白走 Farm→Backwoods→Mountain 三段腿；
    #    走山岭图腾柱直达 Mountain 再续走 Railroad→浴场，近得多。（落点非 dest 由 map_go 续走 BFS）
    #    ⚠️ 要比对的是 **_try_transport 传进来的最终目的地**（如 `BathHouse_Pool`），
    #      不是中间站——只加 `BathHouse_Entry` 没用（第一次就踩了，route 照走 Backwoods）。
    "Earth Obelisk":  {"Mine", "AdventureGuild", "ScienceHouse", "Tent",
                       "BathHouse_Entry", "BathHouse_Pool"},   # 落点 Mountain
    "Water Obelisk":  {"FishShop"},                                          # 落点 Beach
    "Desert Obelisk": {"SkullCave", "SandyHouse", "Club"},                    # 落点 Desert；2026-08-23 恒：去赌场(Club)也走柱（沙漠区，经 SandyHouse 门进），别坐巴士
}


# 矿车网络：目标地图 → 矿车菜单里的站名（MINE_CART_STATIONS 键）
# ⚠️ Mountain 用"采石场"站（落 Mountain 采石场(124,12)，续走西侧矿洞）
MINE_CART_TO = {
    "Mine": "矿井",
    "Town": "城镇",
    "BusStop": "巴士站",
    "Mountain": "采石场",
}


# 🚂 矿车"直达/近"路由表（2026-09-06 恒：可指定路由——这些 destination 走到最近站坐矿车，**压过图腾柱**）。
#   其余目的地仍走"图腾柱 > 矿车(已站上) > 走路"原序。值 = (矿车坐到的图, 最后小走目标 或 None)。
#   直达: cart_target(如 Mine/Mountain/BusStop)，最后小走 None（到即止）。
#   镇东南 POI(铁匠铺/博物馆/冰淇淋摊): cart 到 Town（镇矿车站就在东南，近）→ 再走动到该 POI。
#   农场: cart 到 BusStop（巴士站旁就是农场）→ 再走回 Farm（跨图续走）。
_AUTO_MINECART_ROUTES = {
    # 直达矿井
    "矿井": ("Mine", None), "矿洞": ("Mine", None), "鹈鹕镇矿井": ("Mine", None),
    "mine": ("Mine", None),
    # 直达采石场
    "采石场": ("Mountain", None), "quarry": ("Mountain", None),
    # 直达巴士站
    "巴士站": ("BusStop", None), "busstop": ("BusStop", None), "bus stop": ("BusStop", None),
    # 镇东南 POI（车到 Town 再走近；final 用 POI 真名落门口，见 locations.POI"铁匠铺(门口)"等）
    "铁匠铺": ("Town", "铁匠铺(门口)"), "blacksmith": ("Town", "铁匠铺(门口)"),
    "博物馆": ("Town", "博物馆(门口)"), "museum": ("Town", "博物馆(门口)"), "考古": ("Town", "博物馆(门口)"),
    "冰淇淋摊": ("Town", "冰淇淋摊位"), "冰淇淋摊位": ("Town", "冰淇淋摊位"), "ice cream": ("Town", "冰淇淋摊位"),
    # 农场（车到巴士站再走回农场）
    "农场": ("BusStop", "Farm"), "farm": ("BusStop", "Farm"),
}


# ⚠️ 2026-09-06 恒：特定地点矿车表(_AUTO_MINECART_ROUTES)只在"从农场/农场建筑出发"时成立
#   （农场离巴士站近，走巴士站坐车省全图）；非农场起点完全无此语义，落穿到下方就近段数比较。
_FARM_STARTS = {"Farm", "FarmHouse", "Cabin", "Greenhouse", "FarmCave"}


_FACE_DELTA = [(0, -1), (1, 0), (0, 1), (-1, 0)]   # 0上/1右/2下/3左


def _dismount_if_riding() -> None:
    """骑着马 key confirm 会下马（不是交互）——交通节点前先下马。"""
    try:
        if api.state().get("player", {}).get("riding"):
            api._post("/key", {"key": "confirm"})
            time.sleep(1.5)
    except Exception:
        pass


def _snap_stand(loc: str, sx: int, sy: int) -> None:
    """walk_to 到站位 + 精确对位（±2 容差可能差 1 格 → 交互/confirm 打偏）。"""
    try:
        _walk_and_wait(loc, sx, sy, timeout=15)
        p = api.state().get("player", {})
        if (p.get("x"), p.get("y")) != (sx, sy):
            api._post("/position", {"x": sx, "y": sy})
            time.sleep(0.6)
    except Exception:
        pass


def _obelisk_plan(dest: str, cur: str):
    """玩家在农场 + 有对应图腾柱 + dest 可达 → 返回 (building, 落点图, 标签)；否则 None。
    dest 可达 = 落点图本身 / 落点图近处建筑（OBELISK_NEARBY）/ 岛柱的全岛。
    落到非 dest 的图由 map_go 从落点续走 BFS。"""
    if cur != "Farm":
        return None
    try:
        for b in _buildings():
            t = b.get("type") or ""
            info = OBELISK_TARGETS.get(t)
            if not info:
                continue
            reach = {info["dest"]} | (OBELISK_NEARBY.get(t) or set())
            if t == "Island Obelisk":
                reach |= ISLAND_MAPS
            if dest in reach:
                return (b, info["dest"], info["label"])
    except Exception:
        pass
    return None


def _obelisk_go(building, landing: str, label: str, from_loc: str = "Farm", expect_land=None) -> tuple:
    """站到图腾柱下方 → 朝上 → key confirm 传送（⚠️ 必须 confirm，interact/右键不触发）。
    返回 (是否离开 from_loc/到达落点, 日志)。

    `from_loc`（2026-09-19 恒）：**从哪根图上点火**。默认 "Farm"＝主农场那几根出岛柱；
      姜岛那根（IslandWest→Farm）传 "IslandWest" —— 原来这里**硬编码 "Farm"**，
      所以姜岛那根接不上（这正是 #28 的一半）。站位算法不变：
      `sx = x + w//2, sy = y + h//2 + 1`（反编译 IslandWest.cs:160 的 perch 就是 `Point(72,37)`，
      用扫描得到的 (71,36,3x1) 算出来正好是它）。"""
    try:
        bx, by = int(building["x"]), int(building["y"])
        w = int(building.get("width") or 3)
        h = int(building.get("height") or 2)
        sx, sy = bx + w // 2, by + h // 2 + 1    # 柱底中部（站柱下 1 格朝上）
        _dismount_if_riding()
        _snap_stand(from_loc, sx, sy)
        time.sleep(0.3)
        api._post("/face", {"direction": 0})
        time.sleep(0.3)
        # ⚠️ 必须 key confirm（interact/右键不触发）；连按 3 次防走位未完全停下的边缘
        for _ in range(3):
            api.key("confirm")                    # pressActionButton
            for _ in range(4):
                time.sleep(0.5)
                try:
                    cur = api.state().get("location", {}).get("name", "")
                    if cur and cur != from_loc:
                        break
                except Exception:
                    pass
            try:
                if api.state().get("location", {}).get("name", "") != from_loc:
                    break
            except Exception:
                pass
        cur = api.state().get("location", {}).get("name", "") or landing
        ok = cur != from_loc
        if not ok:
            # ⚠️ 2026-09-19 恒：失败必须**把现场带出来**（"判据要能被看见"）。
            #    原来这里只回 `🗼 xxx → Farm`，等于什么都没说；而"人站在哪格、朝哪边、
            #    停没停"正是区分「站位偏了 / 朝向不对 / 压根没触发」的唯一线索。
            #    （上层 `_try_transport` 原来还会把这句话整个丢掉 —— 那个也一并修了。）
            _why = "读不到玩家位置"
            try:
                _p = api.state().get("player", {}) or {}
                _px, _py, _pf = _p.get("x"), _p.get("y"), _p.get("facingDirection")
                _why = f"人在 ({_px},{_py}) 朝 {_pf}"
                _dev = []
                if (_px, _py) != (sx, sy):
                    _dev.append(f"⚠️ **站位对不上**（期望 ({sx},{sy})）")
                if _pf is not None and _pf != 0:
                    _dev.append("⚠️ **朝向不是朝上(0)**")
                if _p.get("isMoving"):
                    _dev.append("⚠️ **还在移动**（走位没停稳就被按了）")
                if _dev:
                    _why += "，" + "，".join(_dev)
                else:
                    # 🎯 2026-09-19 恒 —— 这是被今天一整轮排查逼出来的：**排除项也要说出来**。
                    #    站位/朝向/静止全对却还是没起来 ⇒ 问题**不在"人站哪"**。今天大半轮就耗在
                    #    怀疑站位上（walk_to 假 ok、16px、异步打架…查了个遍），而消息本可以一句话免掉。
                    _why += ("（站位、朝向、静止**都对得上** ⇒ 排除这几项，"
                             "问题在「按了 confirm 但前面那格没触发交互」）")
            except Exception:
                pass
            return False, f"🗼 {label} 试了但没起来：{_why} ⇒ 落回常规路径"
        # 🎯 落点自检（2026-09-12 恒给的锚点）：落点是**写死在游戏里**的（Building.cs:1011），
        #    真落点对不上 ⇒ 要么游戏改了、要么这一跳根本不是那根柱子 —— 这种"悄悄不对"得自己冒出来，
        #    别等人踩到才发现。差 >3 格才提（落点附近可能有碰撞微调/被顶开一格，别刷噪音）。
        try:
            # `expect_land`：表里查不到时用调用方给的期望落点（姜岛那根落 Farm(48,7)，
            #   不在 OBELISK_TARGETS 里，靠这个参数照样能自检）。
            exp = expect_land or next((v["land"] for v in OBELISK_TARGETS.values()
                                       if v["dest"] == cur and v.get("land")), None)
            if ok and exp:
                p = api.state().get("player", {})
                ax, ay = p.get("x"), p.get("y")
                if isinstance(ax, int) and isinstance(ay, int):
                    if abs(ax - exp[0]) + abs(ay - exp[1]) > 3:
                        return ok, (f"🗼 {label} → {cur}（⚠️ 落在 ({ax},{ay})，游戏里写的落点是 {exp}"
                                    f"——差得有点远，是游戏改了、还是走的不是这根？）")
                    return ok, f"🗼 {label} → {cur} ({ax},{ay})"
        except Exception:
            pass
        return ok, f"🗼 {label} → {cur}"
    except Exception as e:
        return False, f"🗼 {label} 失败({e})"


# ═══════════════════════════════════════════
#  🏝️ 姜岛 → 大陆：坐船 vs 姜岛那根农场柱（恒 2026-09-19 拍板）
# ═══════════════════════════════════════════
ISLAND_FARM_OBELISK_ACTION = "FarmObelisk"   # IslandWest Buildings 层的 Action 名
ISLAND_BOAT_DOCK = (17, 44)                  # 码头返航触发格（locations.py:754，`/warps` 实测）
ISLAND_OBELISK_LAND = (48, 7)                # 柱落点 Farm(48,7)（locations.py:419，游戏里写死）


def _island_farm_obelisk_rect():
    """动态定位姜岛那根"农场柱"的占位 → building dict / None。

    ⚠️ **这只是几何，不是"解锁了没"** —— 地图静态层**锁着也扫得到**
       （我 2026-09-19 一开始拿它当解锁判据，被恒当场纠正）。解锁与否看 `/unlocks.farmObelisk`。"""
    try:
        d = api._get("/tile_props", {"scan": "Action", "location": "IslandWest"})
    except Exception:
        return None
    hits = [h for h in (d.get("hits") or [])
            if h.get("layer") == "Buildings" and h.get("value") == ISLAND_FARM_OBELISK_ACTION]
    if not hits:
        return None
    xs = [int(h["x"]) for h in hits]
    ys = [int(h["y"]) for h in hits]
    return {"type": ISLAND_FARM_OBELISK_ACTION, "x": min(xs), "y": min(ys),
            "width": max(xs) - min(xs) + 1, "height": max(ys) - min(ys) + 1}


def _unlock_unlocked(key: str):
    """查 `/unlocks.<key>.unlocked`。**读不到 → None（不猜）**。"""
    try:
        v = ((api.unlock_status() or {}).get("unlocks") or {}).get(key)
        return None if v is None else bool(v.get("unlocked"))
    except Exception:
        return None


def _island_return_plan(cur: str, dest: str, pos=None):
    """🏝️ 姜岛 → 大陆：坐船 vs 走姜岛那根农场柱，**比总路程**（恒 2026-09-19 拍板的口径）。

    总段数 = 走出发口 + 1(交通) + 落点续走；**严格更少者胜**；打平比"人→出发口"的**地图内距离**
    （照抄 `_minecart_walk_plan` 那把尺子，别自创第二种量法）。

    返回 `(building, 说明)` ＝ 走柱子；返回 `None` ＝ 走船/按原路。
    前提：`/unlocks.farmObelisk` 已解锁 —— **读不到就保守走船**（不猜、不硬闯）。
    """
    if _unlock_unlocked("farmObelisk") is not True:
        return None                                   # 没解锁 / 打听不到 → 柱子不进候选
    ob = _island_farm_obelisk_rect()
    if not ob:
        return None                                   # 解锁了却扫不到占位 → 交回原路
    try:
        boat_seg = (len(_map_bfs(cur, "IslandSouth") or []) + 1
                    + len(_map_bfs("FishShop", dest) or []))
        obel_seg = (len(_map_bfs(cur, "IslandWest") or []) + 1
                    + len(_map_bfs("Farm", dest) or []))
    except Exception:
        return None
    if obel_seg > boat_seg:
        return None                                   # 船更省 → 原路
    if obel_seg == boat_seg:
        # 段数打平 → 比"人→出发口"的地图内距离。**只在人已经站在某个出发图上时才有意义**；
        # 两个都不在（或柱子不更近）⇒ 保守走船。
        ob_stand = (ob["x"] + int(ob.get("width") or 3) // 2,
                    ob["y"] + int(ob.get("height") or 2) // 2 + 1)
        d_ob = (abs(pos[0] - ob_stand[0]) + abs(pos[1] - ob_stand[1])) \
            if (pos and cur == "IslandWest") else None
        d_boat = (abs(pos[0] - ISLAND_BOAT_DOCK[0]) + abs(pos[1] - ISLAND_BOAT_DOCK[1])) \
            if (pos and cur == "IslandSouth") else None
        if d_ob is None or d_boat is None or d_ob >= d_boat:
            return None
    return ob, f"🏝️ 姜岛回大陆：船 {boat_seg} 段 vs 柱 {obel_seg} 段 → 走柱"


def _cart_closer_than_walk(pos, stn, direct):
    """段数打平时比"首段地图内距离"：同图车站是否真的比直接走首段近。
    pos=(x,y) 玩家坐标；stn 车站数据(interact=((sx,sy),face))；direct=纯走 BFS 路径(首段 link 的 tile=出口瓦片)。
    近→坐车(True)，远→走路(False)。"""
    try:
        st = stn["interact"][0]
        st_d = abs(pos[0] - st[0]) + abs(pos[1] - st[1])          # 玩家→车站格
        f = (direct[0][2] or {}).get("tile") if direct else None   # 直接走首段出口瓦片
        if not f:
            return st_d <= 1                                       # 出口无瓦片(门类)→ 仅紧邻车站才坐
        return st_d < (abs(pos[0] - f[0]) + abs(pos[1] - f[1]))    # 玩家→直接走出口
    except Exception:
        return False


def _minecart_walk_plan(cart_target: str, cur: str, final: str = "", strict: bool = True,
                        pos: tuple = None):
    """矿车直达规划：从 cur 走到"菜单能直达 cart_target"的最近可达站 → 坐车到 cart_target。
    ⚠️ 2026-09-06 恒：_AUTO_MINECART_ROUTES 用——先走到站再坐车（原来 _minecart_plan 只在"已站上"才坐）。
    final：最终目的地（POI 名/地图名，可空）；strict=True 时矿车总段数必须**严格少于**纯走段数才坐；
      False=只比较"走站 vs 走全程"（供农场表 curated 落门口，容忍平段）。pos=(x,y) 给平局比首段距离用。
    返回 (walk_path, 站名, 站数据, 目的站名) 或 None；walk_path 空=cur 即该站（直接坐）。
    ⚠️ 2026-09-07 恒：矿车总段数=走站+1坐车+落点续走，严格少于纯走段数才坐（否则 Mtn门口→Mine 会被误判
      "矿车省路"去采石场绕）。段数打平时比"首段地图内距离"（矿洞口走路近 vs 采石场_上车近——两者都1段但
      地图内差70格）。final 是 POI 名先落到地图；no 早退(原 len<=1 return None 会永久挡掉"站在车站要下矿")。"""
    ds = MINE_CART_TO.get(cart_target)
    if not ds:
        return None                          # cart_target 不在矿车网络
    if final and final in locations.POI:
        final = locations.POI[final].get("map", final)   # POI → 地图，段数对照才准
    final = final or cart_target             # 段数对照基准（默认即车直达目标）
    direct = _map_bfs(cur, final)            # 纯走到 final 的段数（None=走不到）
    walk_seg = len(direct) if direct is not None else 10 ** 9
    best = None
    for sname, stn in locations.MINE_CART_STATIONS.items():
        if not any(opt == ds for opt in (stn.get("menu") or {}).values()):
            continue                          # 此站菜单不能直达 cart_target
        mc_steps = [] if cur == stn["map"] else _map_bfs(cur, stn["map"])
        if mc_steps is None:
            continue                          # 到不了此站 → 跳过
        # 矿车总段数 = 走到站 + 1(坐车) + 落点(cart_target)续走到 final（同图 0，跨图 BFS）
        aft = 0 if final == cart_target or final == stn["map"] else len(_map_bfs(cart_target, final) or [])
        cart_total = len(mc_steps) + 1 + aft
        if strict:
            if cart_total > walk_seg:
                continue                      # 矿车段数更多 → 不省路
            if cart_total == walk_seg:
                # 打平 → 比首段地图内距离：仅当车站在当前图且更近才坐（否则走路）
                if not (pos and cur == stn["map"] and _cart_closer_than_walk(pos, stn, direct)):
                    continue
        else:
            if direct is not None and len(mc_steps) >= walk_seg:
                continue                      # 走站 ≥ 走全程 → 矿车不省路
        if best is None or len(mc_steps) < len(best[0]):
            best = (mc_steps, sname, stn, ds)
    return best


def _minecart_go(sname: str, stn, dest_station: str, dest: str) -> tuple:
    """站到矿车格 → 朝站面 → 交互开菜单 → 选目的站 → 等传送。返回 (是否到 dest, 日志)。"""
    try:
        (sx, sy), face = stn["interact"]
        mx, my = sx + _FACE_DELTA[face][0], sy + _FACE_DELTA[face][1]   # 矿车瓦片（面前格）
        _dismount_if_riding()
        _snap_stand(stn["map"], sx, sy)
        # 走位途中可能已搭上矿车 → 直接看是否到 dest
        if api.state().get("location", {}).get("name", "") != stn["map"]:
            cur = api.state().get("location", {}).get("name", "")
            return cur == dest, f"🚂 矿车 → {cur or '?'}"
        api._post("/face", {"direction": face})
        time.sleep(0.3)
        api._post("/interact", {"x": mx, "y": my})   # 显式交互矿车瓦片（防朝向偏差打偏）
        time.sleep(1.2)
        # 等菜单打开并选目的站
        picked = False
        for _ in range(6):
            try:
                m = api._get("/menu")
                for r in (m.get("responses") or []):
                    t = (r.get("text") or "").strip()
                    if t == dest_station or (dest_station and dest_station in t):
                        api.menu_click(option=r["index"])
                        picked = True
                        break
                if picked:
                    break
            except Exception:
                pass
            time.sleep(0.8)
        if not picked:
            # 🚂 2026-09-13 恒「矿车的门禁也没做好」：不通时游戏弹的是**对话框**（`MineCart_OutOfOrder`
            #   「已损坏」），不是菜单 —— 不关掉就把 AI 晾在站台上（真机小星停在 Town (105,80)，框开着）。
            #   用 `/menu_close`（C# 端会先 CollectOrDrop 再强关，专门治"光标有东西关不掉"）。
            try:
                api._post("/menu_close")
            except Exception:
                pass
            return False, f"🚂 {sname} 菜单没找到「{dest_station}」（矿车未解锁？）"
        # 等传送
        for _ in range(12):
            time.sleep(1.0)
            try:
                if api.state().get("location", {}).get("name", "") == dest:
                    return True, f"🚂 {sname}→{dest_station} → {dest}"
            except Exception:
                pass
        cur = api.state().get("location", {}).get("name", "") or ""
        return cur == dest, f"🚂 矿车 → {cur or '?'}"
    except Exception as e:
        return False, f"🚂 矿车失败({e})"


def _minecart_route_go(walk_path, sname, stn, ds, cart_target, final_dest,
                       destination, npc_target=None, npc0=None, mine_hint: str = "") -> str:
    """执行【先走到矿车站 + 坐车到 cart_target】；最后小走 final_dest（同图 POI / None=直达 / 异图续走 BFS）。
    ⚠️ 2026-09-06 恒：复用 _map_go_walk 走段（**调用不改它**）+ _minecart_go 坐车；到站失败/途中剧情→交回走路结果不硬坐车。"""
    walk_txt = ""
    if walk_path:
        walk_txt = _map_go_walk(walk_path, destination, stn["map"], npc_target=None, npc0=None)
    if api.state().get("location", {}).get("name", "") != stn["map"]:
        # 到站失败/途中触发剧情 → 交回走路结果（不硬坐车）
        return walk_txt if walk_txt else _with_state(f"⚠️ 走向矿车站 {stn['map']} 未达")
    ok, mlog = _minecart_go(sname, stn, ds, cart_target)
    # 前缀 = 走站叙事(_with_state 正文部分) + 车段
    prefix = mlog
    if walk_path and walk_txt and _STATE_SEP in walk_txt:
        prefix = walk_txt.split(_STATE_SEP)[0] + "\n" + mlog
    if not ok:
        # 🚂 2026-09-13 恒「有点严重了」：矿车不通**不该**让整趟导航失败 —— 走路明明能到。
        #   ⇒ 拉黑（本次会话不再规划矿车）+ 出声记账，返回**空串**表示"落穿到走路"，
        #     由 `map_go` 接着走 BFS 那条路（那句记账挂进 mine_hint，AI 看得见）。
        _MINECART_DEAD["v"] = True
        _MINECART_DEAD["why"] = mlog
        try:
            _t = api.state().get("time") or {}
            _MINECART_DEAD["day"] = f"{_t.get('season')}-{_t.get('dayOfMonth')}-{_t.get('year')}"
        except Exception:
            _MINECART_DEAD["day"] = ""
        _MINECART_LEAD["text"] = prefix + f"\n⚠️ 矿车不可用（{mlog}）→ 改走走路"
        return ""
    # ✅ 已到 cart_target；最后小走
    if final_dest and final_dest != cart_target:
        path = _map_bfs(cart_target, final_dest)
        if path:
            return _map_go_walk(path, destination, final_dest, lead_log=prefix,
                                npc_target=npc_target, npc0=npc0, mine_hint=mine_hint)
    body = prefix
    if final_dest and final_dest in locations.POI and locations.POI[final_dest].get("map") == cart_target:
        poi = locations.POI[final_dest]
        # 🔴 2026-10-05：这里原来**把 `_walk_and_wait` 的返回值丢掉**，走位超时/失败**照样**回
        #    「→ 到达 {destination}（pos）」= **谎报到达**（同 `_poi_walk_honest:3466` 记的同族，
        #    那三处已收口，这条是漏网的第四处）。⇒ 走**同一份判据**：没走到就如实说人还在半路，
        #    且**不设站位/朝向**（离得远时设朝向是假的，人一走就没了）。
        _ok, _tail = _poi_walk_honest(cart_target, destination, poi)
        body += (f" → 到达 {destination}（{poi['pos']}）{_tail}" if _ok else f" → {_tail}")
    else:
        # ⚠️ 这一支是"矿车把人送到 cart_target，但目的地不是本图 POI"：**到达判据是 `_minecart_go`
        #    回读的图名**（`navigation.py:3137` 那次 `/state`），坐标这一刻现读——别拿出发前那份写。
        try:
            _mst = api.state()
            _mcur = ((_mst.get("location") or {}).get("name") or "?")
            _mpl = _mst.get("player") or {}
            body += f" → 到达 {_mcur}（{_mpl.get('x')},{_mpl.get('y')}）"
        except Exception:
            body += f" → 到达 {cart_target}"
    if npc_target:
        body += "\n" + _npc_arrive_note(npc_target.get("name") or npc_target.get("displayName") or "", npc0, cart_target)
    return _with_state(body + mine_hint + _mine_entry_reminder(cart_target))


def _interior_to_farm(cur: str) -> bool:
    """当前地图是否是「农场建筑室内」（小屋/农舍/温室/洞穴…），需先走出到 Farm。
    map_go 第一步先走出室内进 Farm，好让图腾柱/矿车在 Farm 触发（2026-08-23 恒：从 Cabin 出发去赌场也要走柱子，别坐公交）。
    ⚠️ 2026-08-30 恒：**只用「门式连接」(target==Farm 且 tile is None) 判定**——室内建筑(FarmHouse/Cabin/
    Greenhouse/FarmCave) 走门连回 Farm，均 tile=None；而 Backwoods/Forest/BusStop 等紧邻农场的**室外图**
    虽也连 Farm，但是**世界 warp 瓦片**(tile=(x,y))，不是室内，不许走这个"出屋"分支。
    (旧版只判"有没有连 Farm"，把室外邻图也误判成室内 → 从深山回农场报"离开小屋"误导。)

    ⚠️ 2026-09-16 恒：**畜棚/鸡舍必须按名字单独认**（`locations.FARM_ANIMAL_BUILDINGS`）。
       上面那条 MAP_LINKS 判据对它们**恒为假**——农场建筑室内根本不是 MAP_LINKS 的节点
       （`/warps` 表里连 "Deluxe Barn" 这个图都没有）⇒ `map go Farm` 从棚里出不去，
       报「知识库没找到从 Deluxe Barn 到 Farm 的路径」。而下游 `_exit_farm_building` 本就通用
       （自己读 /map 的原生出口 warp + 走到门**邻格**再 warp，不踩门瓦片），只是没被叫到。
       ⚠️ 别为了省事改成"有连 Farm 就算室内"——那正是 08-30 修掉的误判（Backwoods 等室外邻图）。"""
    if cur == "Farm":
        return False
    if cur in locations.FARM_INTERIOR_BUILDINGS:   # 畜棚/鸡舍/小桶屋（2026-09-16；地窖见 locations.py 备注）
        return True
    for l in locations.MAP_LINKS.get(cur, []):
        if l["target"] == "Farm" and l.get("tile") is None:
            return True
    return False


def _try_transport(dest: str, cur: str):
    """图腾柱（仅农场/姜岛农场，最高优先级）：玩家在对应位置才有。
    成功 → (新当前地点名, 日志)；无可用/失败 → (None, "").
    ⚠️ 2026-09-07 恒：矿车不再在这"有车坐矿车"(cur 是车站图就无脑坐)——改由 map_go 的
      就近段数比较(_minecart_walk_plan)决定，避免 Mtn门口→Mine 还被领去采石场绕。"""
    tp = _obelisk_plan(dest, cur)
    if tp:
        b, landing, label = tp
        ok, log = _obelisk_go(b, landing, label)
        if ok:
            try:
                nxt = api.state().get("location", {}).get("name", "") or landing
            except Exception:
                nxt = landing
            return nxt, log
        # ⚠️ 2026-09-19 恒：**失败别静默**。原来是 `return None, ""` —— 把 `_obelisk_go`
        #    辛苦写出来的失败原因整个丢掉，`map_go` 回包里**看不出"试了柱子但没成"**，
        #    恒只能在游戏里肉眼看见"站在柱子前站了一会儿，然后改常规走路"。
        #    失败时第二个返回值不再是空串；调用方一律按 `if land:` 判成功，不受影响。
        return None, log
    return None, ""


def _map_go_walk(path, destination: str, dest: str, lead_log: str = "", npc_target=None, npc0=None, mine_hint: str = "", note_log: str = "") -> str:
    """执行 BFS 路径逐段走路（map_go 与交通续走共用；2026-08-16 抽取）。
    lead_log: 交通节点成功日志（前缀显示）。
    note_log: 开跑前就要说出口的**坏消息**（如"图腾柱试了没起来"）。
    ⚠️ 2026-09-19 恒：这类话**必须挂进 `log`**，不能挂 `mine_hint` —— 后者只在 `:2356`
       那条"**走完全程**"的返回路径上渲染，中途早退（门锁 / 某段失败 / 提前 return）
       就整个丢掉，等于没说。第一次改我就挂错了地方，被自己这趟 FishShop 门锁复现打脸。
       （`mine_hint` 的矿车提示有同样毛病，留作后续。）"""
    log = [f"🗺️ 导航 {path[0][0]} → {dest}（{len(path)} 段）"]
    if lead_log:
        log.insert(0, lead_log)
    if note_log:
        log.append(note_log)
    for i, (frm, nxt, link) in enumerate(path):
        kind = link["kind"]
        icon = "🟢" if kind == "warp" else "🚪"
        log.append(f"  {i+1}. {icon} {frm} → {nxt}")
        # 🎫 买票旅行（巴士/姜岛船）：真实交互买票→等自动旅行（2026-08-15 恒）
        if (frm, nxt) in TICKET_TRAVEL:
            tkt = TICKET_TRAVEL[(frm, nxt)]
            log[-1] = f"  {i+1}. 🎫 {frm} → {nxt}（{tkt['note']}）"
            if _ticket_travel(frm, nxt, tkt):
                continue
            # 兜底：买票失败 → warp 直达（不卡死；验证后再修票机坐标）
            ar = locations.ARRIVE.get(nxt)
            if ar:
                api.warp(nxt, ar[0], ar[1])
                time.sleep(1.5)
                if api.state().get("location", {}).get("name", "") == nxt:
                    log[-1] += "（🎫票流程失败，warp兜底）"
                    continue
            _NAV_FAILED["v"] = True
            return _with_state("\n".join(log) + f"\n⚠️ {tkt['note']} 到 {nxt} 失败")
        # 🚌 沙漠返程（2026-09-21 恒）：**先走原生那条路**（走到站台 → 接住弹出的原生对话框选"是"），
        #    没弹菜单就落回下面的 `kind=="warp"` 分支照旧 warp 兜底 —— 两条路都不卡死。
        if (frm, nxt) in BUS_RETURN:
            br = BUS_RETURN[(frm, nxt)]
            log[-1] = f"  {i+1}. 🚌 {frm} → {nxt}（{br['note']}·原生交互优先）"
            if _bus_return_travel(frm, nxt, br):
                log[-1] += "（🚌 顺原生对话上了车）"
                continue
            # 没走成：菜单可能还开着（选了没生效）→ 关掉，别留给 AI 自己收拾
            try:
                if (api._get("/menu") or {}).get("open"):
                    api._post("/menu_close")
            except Exception:
                pass
            log[-1] += "（没弹原生对话 → warp 兜底）"
        arrived = False
        if kind == "warp":
            # ✍️ 2026-08-30 恒：出口瓦片**优先用 MAP_LINKS 里我们自己标的 link['tile']**（必为边界内可达格，
            #    见 locations.Farm→Backwoods 改用 (40,1)），落地用 link['arrive']；只有 link 没标时才回退
            #    _warps_to(读 /warps 实时，可能报地图外负数出口如 (41,-1))。
            #    ⚠️ 不用原生 warp 触发（不稳定），统一"walk_to 到出口站格 → /warp 跳"。
            ltile = link.get("tile")
            larive = link.get("arrive")
            if ltile and ltile[0] >= 0 and ltile[1] >= 0:
                ex, ey = ltile
                if larive:
                    wx, wy = larive
                else:
                    w = _warps_to(nxt)
                    wx, wy = (w[0][2], w[0][3]) if w else (None, None)
                use_exact = True   # 走我们标的边界内瓦片，不做边缘换算
            else:
                warps = _warps_to(nxt)
                if not warps:
                    # ⚠️ 室内(农场建筑)→室外兜底（恒 2026-08-15：/warps 不报室内门）
                    if _exit_farm_building(frm, nxt):
                        log[-1] += "（室内出口warp兜底）"
                        continue
                    return _with_state("\n".join(log) + f"\n❌ /warps 没找到 {frm}→{nxt} 的出口")
                ex, ey, wx, wy = warps[0]
                use_exact = False
            # 🩳 **途经点**（恒 2026-09-10「只要能保证换衣服」）：`link['via']` 里的格子先去站一遍，再去出口。
            #    病根：浴场更衣室→泳池那一跳，进门落点 (13,27) 和 warp 格 (2,27) **同在 y=27**，
            #    顺线走根本不经过换装格 (2,17) ⇒ AI 会**穿着便装直接跳进泳池**。
            #    换装是 Back 层 TouchAction——**必须真踩上那一格**才触发，所以这里要精确落格：
            #      ⚠️ **`/walk_to` 落点不保证精确**：`_wait_arrival` 带 ±2 容差，实测浴场更衣室追 (2,17)
            #         会停在墙格 (3,17)（x=2 是一条 1 格宽走廊，两侧 (1,17)/(3,17) 都不可走）——差一格 = 白走。
            #      ⇒ 用 **`/move`（逐格走、收在目标格上）**纠偏。恒：`/move` 一格一格走稳定命中 TouchAction。
            #      ⚠️⚠️ **`/move` 是排队异步的，必须轮询到真站上那一格才能往下走**——
            #         2026-09-10 真机踩到：原先只 `sleep(1.2)`，人还没走完就继续走出口格 (2,27)，
            #         这一步被顶掉 → 日志明明报了「途经(2,17)」，到泳池 `/pool` 却 `bathingClothes=False`（裸泳）。
            for _vx, _vy in (link.get("via") or []):
                try:
                    if _via_step(frm, _vx, _vy):
                        log[-1] += f"（🩳 途经({_vx},{_vy})）"
                    else:
                        _p = _ai_pos()
                        log[-1] += f"（⚠️ 途经({_vx},{_vy}) 没踩到，停在{_p}）"
                except Exception as _ve:
                    log[-1] += f"（⚠️ 途经({_vx},{_vy}) 失败: {_ve}）"
            # 恒 2026-08-13 可靠版：走到出口可站位 → 确认人到 → /warp 下一图入口
            arrived = _walk_trigger_warp(frm, nxt, ex, ey, wx, wy, exact=use_exact)
            if not arrived:
                _NAV_FAILED["v"] = True
                return _with_state("\n".join(log) + f"\n⚠️ 到 {nxt} 失败")
        elif kind == "door":
            ok, why, dtile, dnote = _enter_building_door(nxt)
            # 🔎 2026-09-10 恒：**推门成功**和**兜底 warp 硬进**结局一样（都落在目标图里），
            #    日志也一模一样 → 恒看不出到底推门了没（"我都没见小人正对过门"）。分开标出来。
            #    2026-10-05 加门格坐标：这样"真走到门口推的"和别的情形一眼可分（恒复核用）。
            if ok:
                log[-1] += f"（🚪走到门格 {dtile} 推门进屋）"
            if not ok:
                # 🔒 门锁着（未到营业时间/未解锁/好感不够/性别不符）→ 推门时游戏会说句话
                #    （**对话或信件**，两种都算，见 `_locked_door_dialogue`）。
                #    停下、**不算导航失败、不兜底 warp 硬闯**——瞬移进去 = 穿墙作弊，
                #    恒 2026-09-10 真机抓到：8:10 皮埃尔店锁着，旧兜底 api.warp 把人塞进了 SeedShop(6,29)。
                #    ⚠️ 公会那扇门弹的是**信件**：只认 DialogueBox 时这里判成"真·导航失败"，兜底
                #       warp 直接把人送进公会，把杀史莱姆的门禁绕掉（2026-09-23 堵上）。
                lock_txt = _locked_door_dialogue()
                if lock_txt is not None:
                    mark_door_blocked(nxt, frm)    # 🚪 今天 `🗺️ 可:` 别再推荐它
                    try:
                        api._post("/menu_close")
                    except Exception:
                        pass
                    return _with_state("\n".join(log) +
                        f"\n🔒 {nxt} 门锁着，没进去：{lock_txt or '未到营业时间/未解锁/好感不够'}"
                        f"\n   停在这里——这是门的条件没满足，不是路走不到；等开门时间/好感够了再来，别硬闯")
                # ⛔ 2026-10-05 恒：「刚才好像走到一半就 warp 进博物馆了。」「问题在于**根本没走到
                #    博物馆门口推门**就直接进来。」—— ⇒ **兜底 warp 撤销**（那句
                #    `api.warp` 那一发与「⚠️推门没成 → 兜底warp 硬进」那句日志都删了）。
                #    旧代码只要"没读到锁门台词"就无条件瞬移进屋，于是**纯走位失败**（没到门口）
                #    也变成穿墙，而日志只说"推门没成"——恒从画面上看穿了（小人没正对过门）。
                #    现在按 `_enter_building_door` 回的 `why` **如实分开报**，哪一种都**不 warp**：
                _p = _ai_pos()
                _here = (api.state().get("location") or {}).get("name", "") or frm
                _tbl = (getattr(locations, "SHOP_HOURS", {}) or {}).get(nxt)
                # 🔬 把 `_enter_building_door` 里**走位那一发的原话**（寻路失败/超时/改到哪格/
                #    推了几次）原样贴出来 —— 旧代码把它丢了，于是只剩"推门没成"这种糊话。
                _dn = f"（走位原话：{dnote}）" if dnote else ""
                if why == "walk_failed":
                    _tail = (f"⚠️ **没走到 {nxt} 的门口就停了**：人在 `{_here}{_p}`，"
                             f"门格在 `{nxt} {dtile}` —— 这是**走位没到**（不是门锁着）。"
                             f"原地停下，别硬闯（旧版这里会 warp 瞬移进屋 = 穿墙，恒 2026-10-05 抓到）"
                             f"{_dn}")
                elif why == "other_map":
                    _tail = (f"⚠️ 人在 `{_here}{_p}`，而 `{nxt}` 的门格 `{dtile}` 不在这张图 ——"
                             f"先 `map go` 到门口那张图；原地停下，别硬闯（旧版会 warp 瞬移）{_dn}")
                elif why == "no_door":
                    _tail = (f"⚠️ 表里查不到 `{nxt}` 的门格（`BUILDING_DOORS`/`_resolve_place` 都没有，"
                             f"人在 `{_here}{_p}`）—— 不敢硬进（旧版这里会 warp 瞬移进屋）{_dn}")
                else:   # pushed_no_effect / error
                    _tail = (f"⚠️ **走到门格 {dtile} 也推了门，游戏没让进**（人在 `{_here}{_p}`）——"
                             f"这既不是走位失败、也没读到锁门的话；原地停下，别硬闯"
                             + (f"（这扇门的营业时间表：{_tbl}）" if _tbl else "") + _dn)
                _NAV_FAILED["v"] = True
                return _with_state("\n".join(log) + "\n" + _tail)
        elif kind == "portal":
            # 🔮 传送阵/模拟出口 warp（2026-08-30 恒：女巫/法师区魔法传送，非原生 warp 瓦片）。
            #    ⚠️ 恒拍板：传送阵要**精确站位**（像门 BUILDING_DOORS）——先 walk_to 到传送阵站格，
            #       再 api.warp 跳过去。否则从远处瞬移、收尾 BFS 乱传。落地格优先 link['arrive'] > ARRIVE。
            stand = link.get("stand")
            if stand:
                _walk_and_wait(frm, stand[0], stand[1], timeout=20)
            ar = link.get("arrive") or locations.ARRIVE.get(nxt)
            if ar:
                api.warp(nxt, ar[0], ar[1])
                time.sleep(1.5)
                if api.state().get("location", {}).get("name", "") == nxt:
                    log[-1] += f"（🔮传送阵前(stand {stand or '—'})warp→{nxt}({ar[0]},{ar[1]})）"
                    continue
            _NAV_FAILED["v"] = True
            return _with_state("\n".join(log) + f"\n⚠️ 传送阵/模拟出口warp 到 {nxt} 失败（缺落地格或未达）")
        # ⚠️ 2026-08-16 恒：每段切图后检测剧情/对话（信件事件/节日等）——
        #    触发了就**停导航**，让 AI 处理（_with_state 自动走剧情），避免边移动边错位
        #    （之前 AI 接到信件以为去鱼店实际去海滩，路过海滩还在走→剧情错位）。
        try:
            _se = api.state(light=True)
            _ev = _se.get("activeEvent") or {}
            _mn = _se.get("activeMenu") or {}
            _ev_ok = bool(_ev.get("id"))
            _dlg_ok = _mn.get("type") == "DialogueBox" and not _mn.get("responses")
            if _ev_ok or _dlg_ok:
                _cloc = api.state().get("location", {}).get("name", "")
                return _with_state("\n".join(log) +
                    f"\n🎬 切图到 {nxt} 后触发剧情/对话（停在 {_cloc}）——事件自动推进中，先处理剧情再继续导航")
        except Exception:
            pass
    # 到目标地点后：带 npc → 贴近人；否则若 POI → 走到 POI 精确位置
    final_txt = f"\n✅ 到达 {dest}"
    if npc_target:
        _nn = npc_target.get("name") or npc_target.get("displayName") or ""
        final_txt += "\n" + _npc_arrive_note(_nn, npc0, dest)
    elif destination in locations.POI:
        poi = locations.POI[destination]
        if poi.get("map") == dest:
            # 2026-08-16 恒：POI 结构化站位+朝向（宠物水碗朝右/柜台朝上；幂等，walk_to 双调无害）
            # 🔴 2026-09-24：没走到就**别报"✅ 到达"**（见 `_poi_walk_honest`）
            _ok, _tail = _poi_walk_honest(dest, destination, poi)
            final_txt = (f"\n✅ 到达 {destination}（{poi['pos']}）{_tail}" if _ok else f"\n{_tail}")
    final_txt += mine_hint + _mine_entry_reminder(dest)
    return _with_state("\n".join(log) + final_txt)


def _npc_arrive_note(npc_name, npc0, at_loc):
    """map_go 带 npc 到场处理：重新查人 → 判是否移动 → 走近 NPC，返回提示给 AI。
    npc0 = 出发时 (location, x, y)，到场再查一次对比位置差 → 判"移动中/延时偏差"。"""
    try:
        # ⚠️ `api.find_npc`（**以 host 为准**）—— 别用 `api._get`：那打的是轮回自己那端，
        #    远处地图会滞留旧位置（2026-09-19）
        fr = api.find_npc(npc_name)
        ns = fr.get("npcs") if fr.get("ok") else []
    except Exception:
        ns = []
    if not ns:
        return f"⚠️ 到 {at_loc} 了，但没找到 {npc_name}——可能移到别的图，重新 find_npc"
    n = ns[0]
    if (n.get("location") or "") != at_loc:
        return (f"⚠️ {npc_name} 不在 {at_loc}（现在在 {n.get('location')}）——"
                "移动走了，重新 find_npc 或 map_go(npc=…) 追")
    # 贴近 NPC（站其正下方 y+1）
    try:
        api.walk_natural(int(n.get("x", 0)), int(n.get("y", 0)) + 1)
    except Exception:
        pass
    moved = bool(npc0 and (n.get("location"), n.get("x"), n.get("y")) != npc0)
    hint = (f"  ⏱️ {npc_name} 正在移动，NPC 位置和导航到达时可能有延时偏差——"
            "贴近后重新 find_npc 确认再互动") if moved else ""
    return f"📍 已在 {at_loc}，走到 {npc_name} 旁边（{n['x']},{n['y']}）{hint}"


def _poi_walk_honest(dest: str, destination: str, poi: dict,
                     timeout: int = 20, retry: int = 30):
    """走到 POI 并应用站位/朝向。返回 `(是否到达, 尾巴文案)`。

    ⚠️ **三处 POI 终止路径共用这一份**（同图 / 交通直达 / BFS 末段）—— 以前三处各写各的，
      而且**都把 `_walk_and_wait` 的返回值丢掉**：超时照样往下走、回包照样写「✅ 到达 X（pos）」，
      = **谎报到达**（"报成功但事没发生"家族里最贵的一种：上层照它决定下一步）。

    真机 2026-09-24（恒：「**是不是路途太遥远了**，从错误箱调用 fish 跑过来，见它每次都朝向错报面前没水」）：
      人从书摊那片小山坡（Town 114,17）去镇鲶鱼钓点 (3,93)，一百多格，20s 走不完，
      于是同一次调用里 map_go 说 (3,93)、fish_run 读到 (68,74)、状态条 (52,91) —— 三个数三个地方；
      `go_fishing` 拿那句"已走到"当到点了**就地开钓** ⇒ 鱼机朝**走路方向**抛竿
      ⇒「🚫 抛竿方向没有水」，白跑一趟。

    ⇒ ①先补一段（长走位常见 30s+）；②仍不到就**如实说"人还在半路"**、且**不设站位/朝向**
      （离得远时设朝向是假的，人一走就没了）。
    """
    _ok, _note = _walk_and_wait(dest, poi["pos"][0], poi["pos"][1], timeout=timeout)
    if not _ok:
        _ok, _note = _walk_and_wait(dest, poi["pos"][0], poi["pos"][1], timeout=retry)
    if not _ok:
        return False, (f"⚠️ 还没走到 {destination}（{_note}）——**人还在半路**，"
                       f"别当成已经站到点上了（走位没走完，朝向/交互都会落空）")
    return True, _apply_poi_stand_face(destination) + _step_into_building(dest, poi["pos"])


def map_go(destination: str = "", npc: str = "") -> str:
    """🗺️ 走地图网络导航到目标地点（交通节点 > BFS 逐段执行）
    ⚠️ 2026-08-16 恒：**跨场景切换的唯一入口**——走出口瓦片/门/买票的真实路径，
    不瞬移。同图 POI 落点才用 walk_to / go_to。
    🚦 交通优先级（2026-08-16 恒：图腾柱 > 矿车 > 走路）：
    - 🗼 图腾柱：玩家在农场且有对应柱（山岭→山/海滩→海滩/沙漠→沙漠/姜岛→全岛），
      动态 /farm_buildings 定位 → 站柱下 key confirm 传送；落点非 dest 则续走 BFS
    - 🚂 矿车：玩家在矿车站图且 dest 在矿车网络（矿井/城镇/巴士站/采石场），
      交互开菜单 → 选目的站
    - 都不可用才沿 MAP_LINKS 走路：出口瓦片(warp) walk_to → 站上自动传下一图；门(door) confirm 进
    每段验证到新地点，最后到目标。（数据：locations.MAP_LINKS / MINE_CART_STATIONS / OBELISK_TARGETS）

    Args:
        destination: 目标地点名（SeedShop / Mine / Town…）或 POI 名（皮埃尔商店）
        npc: 可选，传 NPC 名则直接路由到该 NPC 当前所在场景，到场自动贴近；
             NPC 正在移动会提示"位置可能有延时偏差"（到场建议重新 find_npc 确认）。

    ⚠️ 2026-10-05（真机 B 精修）：真正的大身板在 `_map_go_body`（原样搬过去、逻辑一个字没动）；
       这一层只干一件事——**"按短名理解"必须说出来**（`_variant_shortname_note`）：
       以前 `map go 罗宾木匠店` 是**静默**改写成 ScienceHouse，现在回执顶上会明写
       `🗺️ 按短名「木匠店」理解 → ScienceHouse（要指定别的地点：map lookup）`（恒：「绝不静默」）。
    """
    _out = _map_go_body(destination, npc)
    _note = _variant_shortname_note(destination)
    # ⚠️ 歧义闸（`_poi_ambiguous`）那一类**不贴**——它本来就不替谁挑，贴"按短名理解"会自相矛盾。
    if _note and not _poi_ambiguous(destination):
        return f"{_note}\n{_out}"
    return _out


@_stuck_track
def _map_go_body(destination: str = "", npc: str = "") -> str:
    """`map_go` 的实现体（公开入口是上面的 `map_go`；它只负责贴"按短名理解"那句声明）。"""
    _nr = _nav_resolve(destination)
    if _nr:
        _NAV_LAST.update(_nr)
    _NAV_FAILED["v"] = False
    # 🏠 自家小屋拦截（2026-09-05 恒：裸"小屋"被 SCENE_NAME_ALIAS 的"女巫小屋/巫师小屋"子串劫持
    #   → 误导航去 WitchHut（AI 说"去小屋"走到女巫小屋，找不到自家门）。"去小屋/进小屋/回家/我家"统一走回家**进屋**。
    #   ⚠️ 排除"女巫/巫师/魔法/神殿"——那些是真女巫小屋，别劫持。）
    # ⚠️⚠️ 2026-10-05（真机 A，恒的验收子代理）：这里**以前是子串判据**（`"小屋" in 目的地`）⇒
    #   `map go 姜岛小屋(门内六人房)`（`locations.py:432` 里**有这条全名**）被"小屋"两个字劫持 ⇒
    #   走 `_nav_home_door()` 把人带到**自家 Cabin 门口 Farm(55,12)**，还回「🏠 已到自家小屋门口」
    #   ＝ **认错地方还报"到了"**（假成功）。⇒ 改成**只认整串就是"家/小屋"**（`_is_home_word`），
    #   带修饰的 POI 全名一律落下去走 POI 那条路。
    _hp = str(destination or "").lower()
    _excl = ("女巫", "巫师", "魔法", "神殿", "witch")
    if "回家" in _hp and not any(k in _hp for k in _excl):
        return go_to("回家")          # 明确"回家"→推门进屋就停（要躺床走 sleep / 点名"小屋(床)"）
    if _is_home_word(destination) and not any(k in _hp for k in _excl):
        return _nav_home_door()       # "进小屋/cabin"→只导航到门口（进屋交给 AI interact_at）

    # 🎯 2026-10-05（真机 A）：**精确名优先 + 半截名不猜**（判据与反例见 `_poi_ambiguous`）。
    #    真机现场：`map go 姜岛小屋`（半截）会被子串/别名悄悄挑一个落点 ⇒ 宁报错别兜底：
    #    把候选**全列出来**让人/AI 说全名，**绝不**自己挑一个再报"到了"。
    _amb = _poi_ambiguous(destination)
    if _amb:
        _lst = "\n".join(f"     · 「{n}」（{locations.POI[n].get('map')} {locations.POI[n].get('pos')}）"
                         for n in _amb)
        return _with_state(
            f"❌ 「{destination}」有 {len(_amb)} 个候选、落点**不在同一张图**，我不替你挑：\n{_lst}\n"
            f"  👉 把**全名**（连括号里那截）写全再敲一次，例如 `map ops=go {_amb[0]}`；"
            f"或直接写要去的**图**（如 `map ops=go {locations.POI[_amb[0]].get('map')}`）")

    # 🚫 2026-10-05（真机 B）：**变体名不许静默改写目的地**（判据/真机现场/候选来源见 `_variant_name_error`）。
    #    病样本：`map go 姜岛小屋(门内六房)`（POI 真名带"人"字）被别名「姜岛」当子串命中 ⇒
    #    **不报错、不提示歧义**，悄悄改路去 IslandSouth 还回"到达" ⇒ 宁报错别兜底：这里直接拦下、列候选。
    _verr = _variant_name_error(destination)
    if _verr:
        return _verr

    # 🔍 npc 优先：路由到该 NPC 当前所在场景（2026-09-06 恒：手机实测员建议）
    _npc_target = None
    _npc0 = None
    if npc:
        _nn = str(npc).strip()
        # 🔧 2026-09-06 恒：马龙不在"亮好感"NPC行列（find_npc 查不到/不可按人路由）——AI map_go 传参马龙
        #   → 直接送冒险家协会(探险家公会 AdventureGuild)，别走 find_npc 贴近。
        if _nn in ("马龙", "Marlon", "marlon"):
            destination = "探险家公会"
            _NAV_LAST.update({"name": "马龙", "loc": "AdventureGuild", "x": 6, "y": 12})
        else:
            try:
                fr = api.find_npc(npc)   # ⚠️ 以 host 为准（`_get` 那端会报幽灵位置）
                ns = fr.get("npcs") if fr.get("ok") else []
            except Exception:
                ns = []
            if not ns:
                return _with_state(f"❌ 找不到 NPC「{npc}」——用 find_npc 确认名字再试")
            n0 = ns[0]
            _npc_target = n0
            _npc0 = (n0.get("location"), n0.get("x"), n0.get("y"))
            if n0.get("location"):
                destination = n0["location"]       # 人所在图作为导航目标
                _NAV_LAST.update({"name": npc, "loc": destination,
                                  "x": n0.get("x"), "y": n0.get("y")})
            else:
                return _with_state(f"❌ 「{npc}」没有位置信息")
    if not destination:
        return _with_state("❌ 请给 destination（目的地名）或 npc（NPC 名）再导航")
    try:
        # ⛩️ 爷爷神龛的坐标随农场类型变 → 先动态刷新，定位不到就明确报错（别拿写死坐标硬走）
        _gs = _grandpa_shrine_gate(destination)
        if _gs:
            return _with_state(_gs)
        # 0. 目标解析（POI → 地点名；中文场景名→MAP_LINKS 键）
        dest = destination
        if destination in locations.POI:
            dest = locations.POI[destination]["map"]
        else:
            dest = _resolve_scene_name(destination)
        # 💡 2026-09-06 恒：泛"矿井/矿洞"默认=普通矿井，顺带提示火山/头骨(沙漠)关键词
        _mine_hint = ""
        if dest == "Mine" and any(k in destination for k in ("矿井", "矿洞", "下矿", "挖矿", "采矿")):
            _mine_hint = ("\n💡「矿井/矿洞」默认=普通矿井(地下1-120)；要下**火山**写「火山矿井/火山矿洞」，"
                          "**头骨(沙漠)**写「头骨矿洞/骷髅洞穴/沙漠矿井」")
        # 🎇 节日限定 POI 门禁（2026-08-19 恒：非节日期间 map_go/walk_to 隐藏）
        # 2026-08-23 恒：按门禁类型给针对性文案（石头/矮人语/日期/季节/订单），别一律报"只在节日"
        if destination in locations.POI and not _festival_poi_active(destination, locations.POI[destination]):
            _p = locations.POI[destination]
            if _p.get("rock") and _dwarf_rock_blocked():
                return _with_state(f"❌ {destination} 去不了：矿井 Mine(27,8) 的堵路石还没炸开（炸开才能走到矮人）")
            if _p.get("wallet") and not _wallet_flag_present(_p["wallet"]):
                return _with_state(f"❌ {destination} 进不去：还没学会矮人语（捐赠矮人卷轴/相关任务）——矮人说矮人语")
            _mm = _p.get("map", "")
            _sd = "、".join(f"{_FEST_SEASON_CN.get(s, s)}{d}日" for s, d in sorted(_FESTIVAL_ONLY_MAPS.get(_mm, set()), key=lambda x: (x[1], x[0])))
            return _with_state(f"❌ {destination} 只在节日开放（{_mm} {_sd}）——现在去不了")
        if dest not in locations.MAP_LINKS:
            # 兜底：农场建筑（畜棚/鸡舍/温室/出货箱…）→ 动态定位门口（2026-08-15）
            t = _resolve_place(destination)
            if t:
                loc, x, y = t
                # 🚪 2026-09-19 恒真机（「这里又是直接从畜棚**飞**到农场再走到鸡舍了」）：
                #    人在**室内**时不能直接拿外图坐标 `walk_to` —— `/walk_to` 带别图的 location
                #    是**跨图瞬移**，人就"飞"出去了。正确姿势是**先正常走出门**：
                #    `_exit_farm_building` 本来就通用（自己读 `/map` 的原生出口 warp + 走到门邻格再 /warp），
                #    只是这条兜底分支**没叫它**。
                #    （2026-09-16 修过 `map go Farm` 的同类问题，这里漏了同一个洞。）
                _cur = (api.state().get("location") or {}).get("name", "") or ""
                _pre = ""
                if _cur and _cur != loc:
                    if _interior_to_farm(_cur) and _exit_farm_building(_cur, "Farm"):
                        _pre = f"（先从 {_cur} 走到出口出去）"
                        _cur = (api.state().get("location") or {}).get("name", "") or ""
                    if _cur != loc:
                        # 出了屋还不在目标图（目标在别的图）⇒ 交给正常地图导航过去，别跨图 walk_to。
                        # 判据=**回读当前图**，不看 map_go 的文案（文案格式会变，靠它判成败迟早漂）。
                        map_go(loc)
                        _cur = (api.state().get("location") or {}).get("name", "") or ""
                if _cur != loc:
                    # 到不了目标图：**如实报**，不做跨图瞬移（宁报错别兜底）
                    return _with_state(f"❌ 到不了 {loc}（现在在 {_cur or '?'}）——先 map go {loc} 走过去")
                _w_ok, _w_note = _walk_and_wait(loc, x, y, timeout=35)
                if not _w_ok:
                    # 🚫 2026-10-05：这里的返回值**以前被丢掉**，走位超时/失败**照样**回
                    #    「🗺️ 已到「X」门口」= **谎报到达**（"报成功但事没发生"家族，恒最恨的一类）。
                    #    判据是 `_walk_and_wait` 自己的 `(ok, note)`（ok=图名对上+±2+静止，
                    #    `navigation.py:1068-1093`）——**没有**改成任何新的宽松判据，只是**不再无视**它。
                    #    ⚠️ 本批只改这一处；审计列出的另外 11 处同样丢返回值的调用点**原样不动**
                    #      （逐条判断见 CHANGELOG 203z补30）。
                    try:
                        _wst = api.state()
                        _wp = (_wst.get("player") or {})
                        _wpos = (f"{((_wst.get('location') or {}).get('name') or '?')} "
                                 f"({_wp.get('x')},{_wp.get('y')})")
                    except Exception:
                        _wpos = "读不到位置"
                    return _with_state(
                        f"❌ **没走到「{destination}」门口**（目标 {loc} {x},{y}）：{_w_note}；"
                        f"人现在在 {_wpos}{_pre}。"
                        f"下一步：看 `_mcp_out.log` 里的 `[walk]` 那行落点，再 `map ops=go {destination}` 重试；"
                        f"要进屋请先站到门口那格 `scene interact` 推门（别当自己已经到了）")
                # 📬 邮箱不是"建筑门"：这一格只是**站位**，要敲的是旁边那格邮箱
                #    （2026-09-24 真机：原文案会回"（建筑门，进屋用 interact）"，把 AI 往错的动作上带）
                if any(k in (destination or "") for k in ("邮箱", "信箱", "mailbox")):
                    _mb = (api.state().get("mailbox") or {})
                    _next = (f" → scene at {_mb['x']} {_mb['y']} 读信"
                             if _mb.get("location") == loc else "")
                    return _with_state(f"🗺️ 已到邮箱旁 ({loc} {x},{y}){_pre}{_next}")
                return _with_state(f"🗺️ 已到「{destination}」门口 ({loc} {x},{y}){_pre}（建筑门，进屋用 interact）")
            return _with_state(f"🗺️ 知识库没有「{dest}」的地点链接（试试 SeedShop/Town/Mine…）"
                               f"{_near_map_hint(dest)}")
        # ⚠️ 未解锁地点拦截（2026-08-14 #13）
        _lock = _map_go_unlock_check(dest)
        if _lock:
            return _with_state(_lock)
        # ⚠️ 火山门禁（2026-08-16 恒定；**2026-09-12 恒缩小范围**）：
        #    只拦「火山内部 1~9 层」（那些层换层走特殊瓦片、程序做不了，卡住只能人去救）。
        #    入口层 VolcanoDungeon0 / 火山口 VolcanoEntrance / 山顶 Caldera **一律放行**
        #    —— 恒：「入口层其实应该放行的，顶层也放行。只是火山内部9层需要陪同」。
        #    （原来 `startswith("Volcano")` 一刀切，连"从山顶挪回入口层"都拦。）
        if _is_volcano_interior(dest):
            _vg = _volcano_gate()
            if _vg:
                return _with_state(_vg)
        # 1. 当前地点
        cur = api.state().get("location", {}).get("name", "")
        if cur == dest:
            # npc：人已在当前图 → 直接贴近，不等 POI
            if _npc_target and _npc_target.get("location") == cur:
                return _with_state(_npc_arrive_note(npc, _npc0, cur))
            # 已在目标地点：若指定了 POI 且 POI 就在本图，仍走到 POI 精确位置
            if destination in locations.POI and locations.POI[destination].get("map") == dest:
                poi = locations.POI[destination]
                # ⚠️ 2026-09-24 恒真机（「**是不是路途太遥远了**，从错误箱调用 fish 跑过来，
                #    见它每次都朝向错报面前没水」）：这里原来 `_walk_and_wait(...)` 的**返回值被丢掉**，
                #    20 秒超时**照样**往下走、返回串还写「🗺️ 已在 Town，走到 镇鲶鱼钓点（(3,93)）」
                #    —— **谎报到达**（同"报成功但事没发生"家族，而这是最贵的一种：上层照它决定下一步）。
                #    实测那次：人从书摊那片小山坡（Town 114,17）出发，一百多格，20s 走不完 ⇒
                #    同一秒里 map_go 说 (3,93)、脚本读到 (68,74)、状态条 (52,91)，三个数三个地方；
                #    上层 `go_fishing` 拿这句当"到点了"就地开钓 ⇒ **鱼机朝着走路方向抛竿**
                #    ⇒「🚫 抛竿方向没有水」白跑一趟。
                #    ⇒ ①先补一段（长走位常见 30s+）；②仍不到就**如实说"人还在半路"**、
                #      且**不设站位/朝向**（离得远的时候设朝向是假的，人一走动就没了）。
                # ⚠️ 2026-08-23 恒：已在目标图(如已在 Club)时也要 _apply_poi_stand_face——
                #    walk_to 有 ±2 容差可能停偏1格、且不设 face，interact 会打到错误瓦片。
                #    与另两条 POI 终止路径(transport/BFS)一致：position 瞬移到 stand + 设朝向。
                #    🔴 2026-09-24：没走到就**别报"已走到"**（详见 `_poi_walk_honest` 的注释：
                #    恒那句「是不是路途太遥远了」就是这么来的）。
                _ok, _tail = _poi_walk_honest(dest, destination, poi)
                if not _ok:
                    return _with_state(_tail)
                return _with_state(f"🗺️ 已在 {dest}，走到 {destination}（{poi['pos']}）{_tail}")
            return _with_state(f"🗺️ 已经在 {cur} 了" + _mine_hint + _mine_entry_reminder(cur))
        # 🎪 2026-08-29 恒：节日临时图(Temp/Forest-IceFestival)不在 MAP_LINKS，map_go 到逻辑场地
        #   (Town/Forest/Beach)会误报"没路径"——玩家其实已被游戏自动送到节日场地。只在临时图且目标是
        #   别的地点时兜底；夜市/沙漠节/鱿鱼节/鳟鱼大赛是真实场地图，玩家在对应可走图，不受影响照常导航。
        if cur in _FESTIVAL_TEMP_MAPS and cur != dest:
            return _with_state(f"🎪 节日进行中，你已在节日场地（{cur}）——地图走不了这里，直接玩"
                               "（festival info/interact 互动）；退出/卡住→联系 user 帮忙，MCP 端 warp 已禁用")
        # 2.45 ⚠️ 2026-08-23 恒：站在农场室内(小屋/农舍/温室/洞穴) → 先走出到 Farm，
        #      否则 _try_transport(要求 cur==Farm) 检不到图腾柱 → 白白坐公交。
        #      先出屋再让交通节点触发（图腾柱 > 矿车 > 走路）。
        if cur != "Farm" and _interior_to_farm(cur):
            # ⚠️ 2026-08-30 恒：xlog 是 bool(出口成功与否)，非日志串。出屋后若目标就是
            #   Farm → 直接返回已在农场；否则 BFS 对 Farm→Farm 会走自环绕地图(飞河边/绕圈)。
            xlog = _exit_farm_building(cur, "Farm")
            cur = api.state().get("location", {}).get("name", "") or "Farm"
            if not xlog:
                return _with_state("⚠️ 走出室内到农场失败（可能被挡/在菜单里）")
            if dest == "Farm":
                # 🔑 2026-09-11 恒：**出屋只是"到农场"的第一步** —— 目标若是 Farm 本图的 POI
                #   （爷爷的神龛 / 出货箱 / 农场洞穴(外) / 农场上口·下口…），还得继续走到那个 POI。
                #   ⚠️ 原版在这里一律 return（08-30 加的自环守卫），害得"从自家小屋去爷爷的神龛"
                #      报「🏡 已离开室内回到农场」就收工，人停在农舍门口 (55,13)，离 (8,8) 还差 47 格。
                #   ⚠️ 光删掉早退**不够**：后面 `_map_bfs("Farm","Farm")` 返回 `[]`（from==to 守卫），
                #      而下游是 `if not path:`，空列表判假 → 会报"知识库没找到从 Farm 到 Farm 的路径"。
                #      所以这里必须**就地**把 POI 走完再返回。
                _poi_here = locations.POI.get(destination) if destination in locations.POI else None
                if _poi_here and _poi_here.get("map") == "Farm":
                    _ppx = api.state().get("player", {})      # 出屋瞬间的落点（写进日志，别读走完之后的）
                    # 🔴 2026-10-05：这条原来**把 `_walk_and_wait` 的返回值丢掉** ⇒ 走位没走完照样回
                    #    「→ 到达 {destination}（pos）」= **谎报到达**（同族第四处，见 `_poi_walk_honest:3466`）。
                    #    改走**同一份判据**（它末尾已经带 `_apply_poi_stand_face` + `_step_into_building`）。
                    _ok, _tail = _poi_walk_honest("Farm", destination, _poi_here)
                    _head = (f"🏡 已离开室内回到农场（{cur} {_ppx.get('x')},{_ppx.get('y')}）"
                             f"（目标 {destination} 在本图）")
                    if not _ok:
                        return _with_state(_head + "\n" + _tail)
                    return _with_state(f"{_head}\n→ 到达 {destination}（{_poi_here['pos']}）{_tail}")
                return _with_state(f"🏡 已离开室内回到农场（{cur} {api.state().get('player',{}).get('x')},{api.state().get('player',{}).get('y')}）")
        # 玩家坐标（就近段数比较平局时比"第一段地图内距离"用）
        _pos = None
        try:
            _pp = api.state().get("player", {})
            _pos = (int(_pp.get("x", -1)), int(_pp.get("y", -1)))
        except Exception:
            _pos = None
        # 2.45 ⚠️ 2026-09-06 恒：特定 destination（矿车"直达/近"，见 _AUTO_MINECART_ROUTES）
        #   **仅起点=农场/农场建筑**才成立（农场离巴士站近，车到镇东南 POI 落门口）。
        #   非农场起点无此语义，落穿到下方就近段数比较。strict=False：农场表 curated，容忍平段落门口。
        _mroute = None
        if cur in _FARM_STARTS:
            _mroute = _AUTO_MINECART_ROUTES.get(destination) \
                or _AUTO_MINECART_ROUTES.get(str(destination).lower()) \
                or _AUTO_MINECART_ROUTES.get(str(dest).lower())
        if _mroute and not _minecart_dead_now():
            _cart_target, _final = _mroute
            _cok, _cwhy = _cart_gate(_cart_target)
            if not _cok:
                _mine_hint = f"\n🚂 矿车不通（{_cwhy or '未解锁'}）→ 直接走路" + _mine_hint
            _mc = _minecart_walk_plan(_cart_target, cur, final=_final, strict=False, pos=_pos) if _cok else None
            if _mc:
                _r = _minecart_route_go(_mc[0], _mc[1], _mc[2], _mc[3], _cart_target, _final,
                                        destination, _npc_target, _npc0, mine_hint=_mine_hint)
                if _r:
                    return _r
                _mine_hint = _MINECART_LEAD["text"] + "\n" + _mine_hint   # 矿车废了 → 落穿到走路
        # 2.5 ⚠️ 2026-08-16 恒：图腾柱（仅农场/姜岛，最高；矿车已挪到下方就近比较）
        land, tlog = _try_transport(dest, cur)
        if land:
            if land == dest:
                # 直达 → 若 destination 是 POI 在本图，走到 POI 精确位
                if destination in locations.POI and locations.POI[destination].get("map") == dest:
                    poi = locations.POI[destination]
                    # 🔴 2026-09-24：没走到就**别报"到达"**（见 `_poi_walk_honest`）
                    _ok, _tail = _poi_walk_honest(dest, destination, poi)
                    _lead = (f"{tlog} → 到达 {destination}（{poi['pos']}）{_tail}" if _ok
                             else f"{tlog} → {_tail}")
                    return _with_state(_lead + _mine_entry_reminder(dest))
                return _with_state(f"{tlog} → 到达 {dest}" + _mine_entry_reminder(dest))
            # 落点≠dest（岛柱落岛南等）：从落点续走 BFS
            cur = land
            path = _map_bfs(cur, dest)
            if not path:
                return _with_state(f"{tlog}，但从 {cur} 到 {dest} 缺地图链接（先手动到 {cur} 再走）")
            return _map_go_walk(path, destination, dest, lead_log=tlog, npc_target=_npc_target, npc0=_npc0, mine_hint=_mine_hint)
        # ⚠️ 2026-09-19 恒：柱子**试了但没成** → 这句必须跟着走路日志一起冒出来，别让人只能靠
        #    肉眼在游戏里发现（原来这里**什么都没有**）。挂 `note_log` **不是** `mine_hint` ——
        #    后者中途早退就丢了（教训见 `_map_go_walk` docstring）。
        _tnote = tlog or ""
        # 2.55 🏝️ 2026-09-19 恒：**姜岛 → 大陆：船 vs 姜岛那根农场柱，比总路程**。
        #   原来 BFS 只会走船（IslandSouth→FishShop），柱子**解锁了也从没进过候选**——
        #   `_obelisk_plan` 第一行 `if cur != "Farm": return None` 把它挡在门外（不是比输，是没参赛）。
        if cur in ISLAND_MAPS and dest not in ISLAND_MAPS:
            _ip = _island_return_plan(cur, dest, _pos)
            if _ip:
                _ob, _why = _ip
                _iok, _ilog = _obelisk_go(_ob, "Farm", "姜岛农场柱(→农场)",
                                          from_loc="IslandWest", expect_land=ISLAND_OBELISK_LAND)
                if _iok:
                    if dest == "Farm":
                        return _with_state(f"{_why}\n{_ilog} → 到达 Farm" + _mine_entry_reminder("Farm"))
                    _p2 = _map_bfs("Farm", dest)
                    if not _p2:
                        return _with_state(f"{_why}\n{_ilog}，但从 Farm 到 {dest} 缺地图链接")
                    return _map_go_walk(_p2, destination, dest, lead_log=_why + "\n" + _ilog,
                                        npc_target=_npc_target, npc0=_npc0,
                                        mine_hint=_mine_hint, note_log=_tnote)
                # 柱子没起来 → **别静默**，把原因带进下面的走路日志（今天刚修的那条通道）
                _tnote = (_tnote + "\n" if _tnote else "") + _ilog
        # 2.5b ⚠️ 2026-09-07 恒：矿车"就近段数比较"（任何起点，含非农场）。替代原"有车坐矿车"：
        #   dest 在矿车网络时，矿车总段数严格少于纯走才坐；打平比"首段地图内距离"。
        if dest in MINE_CART_TO and not _minecart_dead_now():
            _cok, _cwhy = _cart_gate(dest)
            if not _cok:
                _mine_hint = f"\n🚂 矿车不通（{_cwhy or '未解锁'}）→ 直接走路" + _mine_hint
            _mc = _minecart_walk_plan(dest, cur, final=dest, strict=True, pos=_pos) if _cok else None
            if _mc:
                _final = destination if destination in locations.POI else ""
                _r = _minecart_route_go(_mc[0], _mc[1], _mc[2], _mc[3], dest, _final,
                                        destination, _npc_target, _npc0, mine_hint=_mine_hint)
                if _r:
                    return _r
                _mine_hint = _MINECART_LEAD["text"] + "\n" + _mine_hint   # 矿车废了 → 落穿到走路
        # 3. BFS 路径
        path = _map_bfs(cur, dest)
        if not path:
            return _with_state(f"🗺️ 知识库没找到从 {cur} 到 {dest} 的路径（缺地图链接）")
        # 4. 逐段执行（恒 2026-08-13 多段走路：走到出口瓦片 → 传送到下一图入口(ARRIVE) → 继续走）
        return _map_go_walk(path, destination, dest, npc_target=_npc_target, npc0=_npc0,
                            mine_hint=_mine_hint, note_log=_tnote)
    except Exception as e:
        return _with_state(f"❌ {e}")


def warp_safe() -> str:
    """🏠 紧急逃脱：优先 warp 回上次 walk_to/map_go **失败的目标点**（2026-08-29 恒：落在失败点方便续走/救回），
    没失败目标则回安全位（家 homeLocation；没有则 Farm）。
    ⚠️ **仅紧急逃脱/中断兜底**（血低被围、脚本卡死、火山被卡、地图转换失败）——
    日常移动请用 map_go 走真实路径，别拿它当导航。
    """
    try:
        if _NAV_FAILED["v"] and _NAV_LAST.get("loc"):
            try:
                r = api.warp(_NAV_LAST["loc"], _NAV_LAST["x"], _NAV_LAST["y"])
                if r.get("ok"):
                    return _with_state(f"🏠 紧急逃脱 → 上次导航失败点「{_NAV_LAST.get('name')}」({_NAV_LAST['loc']} {_NAV_LAST['x']},{_NAV_LAST['y']})")
            except Exception:
                pass
        return _with_state(api.warp_safe())
    except Exception as e:
        return _with_state(f"❌ 紧急逃脱失败: {e}")


def _nav_home_door() -> str:
    """导航到自家小屋门口（Farm 外立面，用动态 homeDoor），**不进屋**（进屋用 interact_at 门）。
    ⚠️ 睡觉要说清谁：`sleep who=自己名` 才睡自家床（`_aim_sleep_home` 会先走回屋）；
       **裸调 sleep 的默认是房主的床**（爬床彩蛋），而且那种情况不会自动导航。
    （恒 2026-09-19 读文案时问到这个，原来那句「睡觉用 sleep(自动回屋)」没写前提，会把人带沟里。）
    解决"进 cabin 找不到门"：不依赖 _enter_building_door 的静态坐标(3,12)。"""
    try:
        door = api.state().get("player", {}).get("homeDoor")
        if not door:
            return _with_state("❌ 拿不到 homeDoor（需要新DLL）")
        # 🚪 同 _go_home：人在别的图时先 map_go 回农场，别拿外图坐标 walk_to（= 跨图瞬移）
        _err = _walk_on_map(door["location"], door["x"], door["y"], timeout=35)
        if _err:
            return _with_state(f"❌ 没到自家门口: {_err}")
        return _with_state(f"🏠 已到自家小屋门口 ({door['location']} {door['x']},{door['y']})"
                           f"——进屋 interact_at 门；睡觉是 `sleep who=<自己名字>`"
                           f"（会自动走回屋到床边，不用先回家）；who **必填**、名字写错会报错列出可选名")
    except Exception as e:
        return _with_state(f"❌ {e}")
