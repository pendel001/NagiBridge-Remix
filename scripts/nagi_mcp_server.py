"""
NagiBridge MCP Server — StardewValley × AI
==========================================
让 AI 伙伴通过 MCP 工具像玩文字冒险游戏一样操作星露谷。
每次工具返回自动附带游戏状态速报 (state strip)，AI 不用额外查就知道全局。

传输方式：Streamable HTTP（现代 MCP 协议，手机/Claude Code 连）
  端点: POST http://<主机IP>:8000/mcp   (MCP initialize/工具调用)
  ⚠️ 旧 SSE 端点 /sse 已移除（404），客户端必须配成 Streamable HTTP，且 URL 要带 /mcp。

启动:
    python scripts/nagi_mcp_server.py

环境变量:
    NAGI_MCP_PORT  — 监听端口（默认 8000）
    NAGI_URL       — 星露谷 API 地址（默认 http://localhost:7843，AI 角色进程）
"""

import os
import sys
import json
import subprocess
import time
import base64
import io
import re
import random
from typing import Any, Optional
try:
    from mcp.server.fastmcp import Image
except ImportError:
    Image = None

# 高质量截图缩放（可选）：Pillow 用 LANCZOS 把截图缩到模型最优尺寸，
# 避免手机 Claude App 对超大图做低质量降采样 → 糊成色块。没有 Pillow 就发原图。
try:
    from PIL import Image as _PILImage
    try:
        from PIL.Image import Resampling as _PILResampling   # Pillow >= 9.1
    except ImportError:
        _PILResampling = None                                 # 旧版 Pillow，退回 _PILImage.LANCZOS
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

# ── 截图工具常量（screenshot()） ──
_SCREENSHOT_MIN_WIDTH = 1280     # 源宽度低于此值 → 尝试 /resolution 提升（一次性）；720p=1280 宽，≥1280不撑窗口
_SCREENSHOT_RES_FIXED = False    # 本进程是否已尝试过提升源分辨率（避免反复 ApplyChanges）
_SCREENSHOT_MAX_EDGE = 1568      # 模型最优长边上限（Claude vision 建议 ≤1568），超出用 LANCZOS 缩小

# ── 引入 stardew_api.py ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
# 手机/Claude Code 端 MCP 服务器默认控制 AI 角色(7843) —— 房主通常占 7842，AI 是后开的 farmhand 占 7843。
# 角色名不写死：AI 是哪个 farmhand 取决于 NAGI_URL 打到谁的进程（启动顺序决定端口）。
# 若要控制房主(7842)，用 NAGI_URL 显式指定（Claude Code 桌面端 .mcp.json 已显式设 7842，不受影响）。
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
# ⚠️ 2026-08-14 修复：NAGI_AI_URL 必须跟随 NAGI_URL——stardew_api 的 _ai_post/_ai_get 用 AI_BASE_URL(NAGI_AI_URL)。
#    之前默认 7843 写死，端口一翻（AI 抢到 7842）_ai 调用还是打 7843=恒 → 全操作到房主身上！
#    服务器设哪个 AI 端口，_ai 调用就打哪个。
os.environ.setdefault("NAGI_AI_URL", os.environ["NAGI_URL"])
import stardew_api as api
import appearance_ref_data as ref_data

from mcp.server.fastmcp import FastMCP

# ── 日历数据 ──
import calendar_data
import movie_data

# ── 玩家行为检测 ──
import player_activity

# ── 地图标注（MAP_LINKS 门vs出口 / MAP_FEATURES 地点交互功能）2026-08-13 ──
import locations

# ── 献祭(社区中心收集包)静态知识库 2026-08-22（据中文维基整理；与 bundle_status 实地读板互补）──
import bundles

# ── 🛋️ 计划引擎（2026-08-14 全自动一天）── 🚫 已退役（2026-08-17 恒：计划模式暂不实现）
#    保留 import：存档的 _plan_* 代码仍引用它，若恢复计划模式可直接重新启用（git 有备份）。
import plan_engine

# ═══════════════════════════════════════════
#  🧠 会话上下文缓冲（2026-08-13 #7：A2 长期记忆层）
# ═══════════════════════════════════════════
_session_context = []
_session_file = None
_session_ts = time.strftime("%Y%m%d_%H%M%S")

# 会话缓冲设置（#6 设置类超级工具可改；这里先给默认）
SESSION_CFG = {
    # 🧠 上下文记忆轮次（2026-08-17 恒：200→50）。只限内存 _session_context（FIFO 丢旧），
    #    不影响 session_*.jsonl 实时记录（那份缓存照常全量写盘，方便 user 同步，别动）。
    "max_turns": 50,
    "export_format": "jsonl", # jsonl / markdown / both
    "auto_export": True,      # 游戏退出自动导出
    "include_npc": True,      # 记录 NPC 对话（false 只记玩家间的）
}


def _host_notify(message: str, sender: str = "DeeSeek"):
    """💬 给 user 发通知（host_chat）+ 记入会话上下文。
    ⚠️ 2026-08-15 恒拍板：给 user 的推送**统一走聊天框**（HUD 过夜复盘看不到）。
    host /chat 已改成只 addMessage、不再顶输入，所以普通通知/过夜复盘都能用这个。
    记入会话上下文（/chat 不进游戏聊天上下文，AI 得靠这个看到自己发了啥）。"""
    try:
        api.host_chat(message)
    except Exception:
        pass
    try:
        _session_append(sender, f"💬 {message}", None, "host_chat")
    except Exception:
        pass


def _host_converse(message: str, sender: str = "DeeSeek"):
    """💬 给 user 发聊天（host_chat）+ 记入会话上下文——与 _host_notify 同源（都走聊天框）。
    保留此别名以兼容既有调用；聊天不再顶输入，结算复盘（聊天窗口）恒看得到。"""
    try:
        api.host_chat(message)
    except Exception:
        pass
    try:
        _session_append(sender, f"💬 {message}", None, "host_chat")
    except Exception:
        pass


def _session_append(speaker, content, location=None, event_id=None):
    """推一条会话记录到内存 + jsonl 文件。白板归档/剧情台词/小新闻/聊天都走这。"""
    global _session_context
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "speaker": speaker,
        "content": content,
        "location": location,
        "event_id": event_id,
    }
    _session_context.append(rec)
    if len(_session_context) > SESSION_CFG["max_turns"]:
        _session_context = _session_context[-SESSION_CFG["max_turns"]:]
    try:
        global _session_file
        if _session_file is None:
            _session_file = os.path.join(SCRIPT_DIR, f"session_{_session_ts}.jsonl")
        with open(_session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _session_export():
    """游戏结束导出结构化文件（供 LLM 前端记忆归档）。"""
    try:
        if not _session_context:
            return
        if SESSION_CFG["export_format"] in ("markdown", "both"):
            md = os.path.join(SCRIPT_DIR, f"session_{_session_ts}.md")
            with open(md, "w", encoding="utf-8") as f:
                f.write("# 会话记录\n\n")
                for rec in _session_context:
                    f.write(f"- **{rec['speaker']}** ({rec['ts']}): {rec['content']}\n")
    except Exception:
        pass


# ── MCP 服务器 ──
MCP_PORT = int(os.environ.get("NAGI_MCP_PORT", "8000"))
MCP_HOST = os.environ.get("NAGI_MCP_HOST", "0.0.0.0")

mcp = FastMCP(
    "NagiBridge",
    instructions="""# NagiBridge — 星露谷 AI 控制接口

你是星露谷的 AI 伙伴，通过 MCP 工具控制游戏角色。
**每次工具返回都会自动附带游戏状态速报**，包含位置、时间、体力、背包、提醒等。
先看状态速报里的 ⚠️ 提醒事项再做决策。

用户说中文，请用中文回复。控制游戏时简洁报告结果即可。
""",
    host=MCP_HOST,
    port=MCP_PORT,
)


# ═══════════════════════════════════════════
#  会话日志（D1，2026-08-12）——测试参考指标
# ═══════════════════════════════════════════
# 每次工具调用写一行到 scripts/session_log.jsonl：
#   {"ts":..., "tool":"...", "bytes":文本返回字节数, "err":bool}
# 用途：① A2 工具合并前后 token 对比 ② "跑一年"验证：看哪天哪工具断了
_SESSION_LOG_PATH = os.path.join(SCRIPT_DIR, "session_log.jsonl")


def _log_tool_call(name: str, result, error: bool = False) -> None:
    """记录一次工具调用。bytes=文本返回字节数（含状态条，即每次响应的 token 参考）。"""
    try:
        size = 0
        if isinstance(result, str):
            size = len(result.encode("utf-8"))
        else:
            for block in getattr(result, "content", []) or []:
                if getattr(block, "type", "") == "text":
                    size += len((getattr(block, "text", "") or "").encode("utf-8"))
        with open(_SESSION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f'{{"ts":{time.time():.1f},"tool":{json.dumps(name)},"bytes":{size},"err":{1 if error else 0}}}\n')
    except Exception:
        pass


def _schema_estimate() -> int:
    """估算工具 schema 注入的 token（描述+参数名），A2 合并前后对比用。"""
    try:
        total = 0
        for t in mcp._tool_manager.list_tools():
            total += len((t.description or "")) + sum(len(p) for p in (t.parameters.get("properties", {}) if hasattr(t, "parameters") else {}))
        return total
    except Exception:
        return 0


_orig_call_tool = mcp.call_tool


def _logged_call_tool(*args, **kwargs):
    """包一层 FastMCP.call_tool：记录每次工具调用的名字/返回大小/是否报错。"""
    name = args[0] if args else kwargs.get("name", "?")
    try:
        result = _orig_call_tool(*args, **kwargs)
        _log_tool_call(name, result, error=bool(getattr(result, "isError", False)))
        return result
    except Exception as e:
        _log_tool_call(name, str(e), error=True)
        raise


mcp.call_tool = _logged_call_tool


# ═══════════════════════════════════════════
#  状态注入模块
# ═══════════════════════════════════════════

# 第一天/睡醒后第一次操作显示完整状态，之后只显示待办
_last_full_date = None
# 服务器启动后第一次工具调用 → 显示欢迎横幅
_first_call_since_start = True


def _is_new_day(data: dict) -> bool:
    """判断是否新的一天（睡醒后第一次工具调用）。"""
    global _last_full_date
    t = data.get("time", {})
    season = t.get("season", "")
    day = t.get("dayOfMonth", 0)
    year = t.get("year", 1)
    today = (season, day, year)
    if today != _last_full_date:
        _last_full_date = today
        return True
    return False


WELCOME_BANNER = "🌿 NagiBridge MCP 已就绪 · 基于原作者 小红书@里奈 的MCP适配+全面二改版本。 · 二改作者：恒（小红书@高冷 腿长 偷感重）"

def _player_name() -> str:
    """当前被控制角色的名字（广播兜底用）。

    正常情况下 mod 的 /crawl_bed locate 会返回 player2=实际玩家名；
    这里只在缺失时兜底，避免写死某个名字（角色随时可能换）。
    """
    try:
        return api.state().get("player", {}).get("name", "我")
    except Exception:
        return "我"


_HOST_NAME_CACHE: list = []


def _host_name() -> str:
    """host(房主)玩家的名字——动态读，不写死（开源可通用）。缓存一次。
    从 host 进程(7842)的 /state 拿 Game1.player.Name；拿不到兜底 "user"。
    """
    if _HOST_NAME_CACHE:
        return _HOST_NAME_CACHE[0]
    try:
        s = api.host_state()
        n = (s.get("player") or {}).get("name")
        if n:
            _HOST_NAME_CACHE.append(n)
            return n
    except Exception:
        pass
    return "user"


_last_debris_check_ms = 0.0
_DEBRIS_COOLDOWN_MS = 15000   # 地上掉落 15s 冷却（全地图扫，避免每调用都刷 /debris）


def _gather_state() -> dict:
    """收集完整的游戏状态数据。"""
    data = {}
    try:
        # consume_events=True：小新闻渲染时把 recent_events 从 mod 清空（看过即清空，
        # 天然"截至上一次调用"，AI 不会重复看到同一批拾取）
        # light=True：状态条只需格数，不拉背包明细（check_backpack 按需拉明细）
        s = api.state(consume_events=True, light=True)
        data["raw"] = s
        data["player"] = s.get("player", {})
        data["location"] = s.get("location", {})
        data["time"] = s.get("time", {})
        data["inventory"] = s.get("inventory", [])
        data["activeMenu"] = s.get("activeMenu")
        data["activeEvent"] = s.get("activeEvent")
        data["otherPlayers"] = s.get("otherPlayers", [])
    except Exception as e:
        data["error"] = str(e)

    # 警报队列（只 peek 不消费）
    try:
        alerts_resp = api.alerts(peek=True)
        data["alerts"] = alerts_resp.get("alerts", [])
    except Exception:
        data["alerts"] = []

    # ⚠️ 动物/机器不再每次拉取——农场待办只在晨报(每天第一次)里简述，
    #    想实时看用 collect_machines/check 工具。省 2 个 HTTP/调用。

    # 掉落物（全地图扫——含矿井/火山等；15s 冷却防每调用都刷 /debris。恒批注 2026-08-13）
    try:
        now_ms = time.time() * 1000
        if now_ms - _last_debris_check_ms >= _DEBRIS_COOLDOWN_MS:
            _last_debris_check_ms = now_ms
            r = api._get("/debris")
            if r.get("ok"):
                items = {}
                for d in r.get("debris", []):
                    n = d.get("itemName", "") or "?"
                    items[n] = items.get(n, 0) + 1
                if items:
                    data["debris"] = items
    except:
        data["debris"] = None

    return data


def _gather_user_state() -> dict:
    """收集"用户(房主)"的状态——心跳检测用户在干嘛用。

    心跳视角：AI 需要知道**用户**在干嘛，所以打 host 进程(默认7842)，
    而不是 NAGI_URL(7843, AI自己)。只取 describe_activity 需要的字段，轻量。
    """
    data = {}
    try:
        s = api.host_state()
        data["raw"] = s
        data["player"] = s.get("player", {})
        data["location"] = s.get("location", {})
        data["time"] = s.get("time", {})
        data["inventory"] = s.get("inventory", [])
        data["activeMenu"] = s.get("activeMenu")
    except Exception as e:
        data["error"] = str(e)
    return data


def _heartbeat_line(ai_data: dict) -> str:
    """心跳一行：给 AI 看"用户在干嘛 / 是否在附近"（折中方案）。

    - 附近 + 移动中 → "👥 user 正与你同在，⛏️ 正在沙漠中探索"（"和你在一起做某事"前缀）
    - 附近 + 窗口/静止 → 活动本身优先（"🎒 user 正在整理背包" / "💭 user 似乎在发呆"）
    - 不在附近 → 直接描述用户活动（host 进程 7842 的状态）
    兜底：host 状态拿不到 → 退化为描述 AI 自己（不抛异常）
    """
    user_data = _gather_user_state()
    if not user_data.get("player"):
        return player_activity.describe_activity(ai_data)  # 兜底

    oname = player_activity.nearby_player_name(ai_data)
    if oname:
        p = user_data.get("player", {})
        menu_open = bool((user_data.get("activeMenu") or {}).get("type"))
        stationary = p.get("stationarySeconds", 0) or 0
        paused = menu_open or stationary >= player_activity.IDLE_STATIONARY_SECONDS
        if not paused:
            # 移动中 → "和你在一起做某事"：三种同在句式随机，各自合并通顺
            activity = player_activity.describe_activity(user_data)
            pname = p.get("name", "")
            detail = activity.replace(f"**{pname}**", "") if pname else activity
            # 去掉开头的图标 + "正在/在"，得到动作主体（如"沙漠中探索"）
            detail = re.sub(r"^[^一-鿿]+(?:正在|在)?", "", detail).strip()
            if not detail:
                return player_activity.detect_player_nearby(ai_data)  # 兜底
            return random.choice([
                f"👥 **{oname}** 正与你同在{detail}",              # 同在沙漠中探索
                f"👥 **{oname}** 跟你形影不离，在{detail}",        # 形影不离，在沙漠中探索
                f"👥 **{oname}** 和你一起在{detail}",              # 和你一起在沙漠中探索
            ])
        # 窗口/静止 → 活动本身优先（整理背包/发呆等）
    return player_activity.describe_activity(user_data)


def _luck_flavor(luck) -> str:
    """运势文本（游戏原版占卜用语），只在每天第一次的📺电视里显示。"""
    if luck is None:
        return ""
    if luck > 0.07:
        return "精灵非常开心"
    if luck > 0.02:
        return "精灵很开心"
    if luck >= -0.02:
        return "精灵今天没什么倾向"
    if luck >= -0.07:
        return "精灵很烦恼"
    return "精灵非常不满"


def _calendar_line(data: dict) -> str:
    """📅 日历常驻一行：节日/生日/商店休业。没有则返回空串。"""
    try:
        t = data.get("time", {})
        season = (t.get("season") or "").lower()
        day = t.get("dayOfMonth", 1)
        year = t.get("year", 1)
        day_index = (day - 1) % 7 if isinstance(day, int) else 0
        cal_line = calendar_data.build_calendar_line(
            season, day if isinstance(day, int) else 1, day_index,
            year if isinstance(year, int) else 1,
        )
        return cal_line or ""
    except Exception:
        return ""


# ── 🌱 全新档第一周新手提示（2026-08-15 恒：day1-7 注入起步引导，spring年1）──
NEWBIE_HINTS = {
    # 2026-08-15 恒：只当年1春1注入一次（day2/5 引导交给游戏任务/邮件，如威利信→沙滩）
    1: "🌱 年1春1新手链: ①捏脸(menu customize 起名/喜好/形象, 确认) → ②领小屋新手包(storage scan 看箱子实际内容, 按农场类型: 防风草种子/干草等) → ③除新手包可能给的防风草外, **建议**去皮埃尔商店买献祭(社区中心)相关作物种下(menu ops=\"bundle_kb\" 查要啥) → ④浇水 → 空闲捡采集/砍树; 没体力→睡觉(过夜)自然恢复",
}


def _newbie_hint(season: str, day, year, luck, save_key: str = "") -> str:
    """全新档**年1春1**的一次性新手引导；其它时间返回空串。
    2026-08-15 恒：**当且仅当年1春1注入一次**——用完写标记文件隐藏（当前存档）。
    save_key 用 player.homeLocation（小屋 GUID，每存档唯一稳定）；不同档 key 不同 → 各自一次。
    day2/5 引导交给游戏任务/邮件（威利信→沙滩等），不再这里注入。"""
    try:
        if not (year == 1 and season == "spring" and isinstance(day, int) and day == 1):
            return ""
        # 每存档已消费标记文件（显示前先写，防同存档重复）
        if save_key:
            marker = os.path.join(SCRIPT_DIR, f".newbie_hint_{_safe_fname(save_key)}")
            try:
                if os.path.exists(marker):
                    return ""  # 本存档已注入过 → 隐藏
            except Exception:
                pass
            try:
                with open(marker, "w", encoding="utf-8") as f:
                    f.write("used")
            except Exception:
                pass
        hints = []
        if day in NEWBIE_HINTS:
            hints.append(NEWBIE_HINTS[day])
        # 通用：背包/体力管理
        hints.append("💪 前期体力紧张省着用，背包留空位捡东西")
        return "\n".join(hints)
    except Exception:
        return ""


def _safe_fname(s: str) -> str:
    """存档 key → 安全文件名（GUID 等）。"""
    return "".join(c for c in str(s) if c.isalnum() or c in "-_")[:60] or "unknown"


def _menu_advice(menu_type: str, active_menu: dict, active_event: dict = None) -> str:
    """🧭 按菜单/对话类型提示可用工具（2026-08-15 恒：只细化对话+菜单，一行，不报位置工具）。
    覆盖常见卡点（AI 看见菜单但不知调哪个工具）；ShippingMenu 已有专门处理，这里跳过。
    active_event=当前事件 → createQuestionDialogue 场景问句（事件激活时普通 option 点不中真回调）区分用（2026-08-23 恒）。"""
    m = (menu_type or "").lower()
    if m == "shippingmenu":
        return ""
    if m == "charactercustomization":
        return "🎭 捏人弹窗：menu customize(看状态/填名字喜好) + settings appearance(捏脸) + **settings ops=confirm_look 核对**(请host参谋+screenshot 满意) → menu click button=ok 确认(ok后基相定型，想再改需解锁幻觉神龛；捏脸只在菜单内可用)"
    if m == "shopmenu":
        return "🏪 商店：menu shop 逛店（自动走到柜台）/ menu read 看货 / menu click 买 / menu sell 卖"
    if m == "letterviewermenu":
        return "📜 信件：menu click button=close 关信（附件已在开信时领）"
    if m == "gamemenu":
        return "🎒 背包/菜单：menu click 移动/拆分/丢弃整理"
    if m == "itemgrabmenu":
        # 🎁 带动作的 ItemGrabMenu（送礼/加料/领取）：点物品=触发 behaviorFunction（送出/加汤/领走），不是拿起！
        # 2026-08-21 百乐汤实测：冬星节送礼的通用机制 menu click(item=物品名) 复用，无需反编译。
        if active_menu.get("gift"):
            return "🎁 带动作菜单(送礼/加料/领取)：menu click(item=物品名) 点物品触发（别点okButton/收起——点了物品就送出/加汤/领走，没handle/ok按钮）"
        return "📦 领取/箱子：menu click 取件/领奖励"
    if m == "dialoguebox":
        if active_menu.get("responses"):
            # 🎪 场景问句（createQuestionDialogue：星币商店换奖品/跳舞邀请等）——事件激活时普通 option=N 走
            #   event.answerDialogueQuestion 点不中真回调，须 real=true 走真实 receiveLeftClick（2026-08-23 恒实测）
            if active_event:
                return "🗳️ 场景问句（事件）：menu click(option=N, **real=true**) 选择——事件激活时普通 option 点不中真回调"
            return "🗳️ 对话选项：menu click(option=N) 选择"
        return "💬 对话推进：menu advance(推进剧情/对话)；**有选项用 menu click(option=N) 选**（confirm 选不了选项）"
    if m == "readycheckdialog":
        return "🛏️ 睡觉就绪屏（等全员 ready）：想撤就绪/关屏 → menu cancel；确认就寝过夜 → daily sleep"
    if m == "forgemenu":
        return "🔨 锻造台：menu forge 附魔/幻化/组合戒指"
    if m == "junimonotemenu":
        return "🎁 献祭板：menu bundle(看需求+可捐) → menu click 点bundle进页 → menu click item=物品捐 / areaNextButton切房间"
    if m == "choosefromiconsmenu":
        return "🎨 选效果菜单：menu click 选图标"
    if m == "specialordersboard":
        return "📋 任务板：menu read 看任务卡（名称/目标/奖励/期限/可接）→ menu click(button=acceptLeftQuestButton/acceptRightQuestButton) 点 accept 按钮接取（可靠UI路径，子目标会初始化；特殊订单同时只能接一个，别贪多）"
    if m == "levelupmenu":
        # 🧬 2026-08-30 恒：LevelUpMenu（升级/职业选择）。普通升级 auto-confirm 自会点OK；职业选择须 AI 决策。
        lu = active_menu.get("levelUp") or {}
        if lu.get("isProfessionChooser"):
            off = lu.get("offered") or []
            nm = " / ".join(f"{o.get('name')}({o.get('id')})" for o in off)
            return (f"🔀 升级选职业(Skill {lu.get('skillName')} Lv{lu.get('level')})：{nm}。"
                    f"→ 先 /profile 想清楚走哪条线，再 **menu ops=levelup_choose side=left/right**(或 profession=职业id) 定夺")
        return f"🎉 升级到 {lu.get('skillName') or '?'} Lv{lu.get('level')}——普通升级已自动点OK"
    return ""


# ── 🧾 纯聊天环节（2026-08-17 恒：等睡/结算期 AI 只聊天，30s 轮询 + 新消息推送 + 超时兜底）──
# 环节判定（每次状态条构建重新判定，随状态自然结束）：
#   - settlement：过夜结算菜单 ShippingMenu（复盘今天/商量明天/写白板，纯聊天）
#   - wait_sleep：AI 已在床（isInBed / ReadyCheckDialog）但夜还没过——躺床上等恒入睡，纯聊天
# 行为：进环节发引导横幅（等睡才有，结算由菜单段横幅覆盖）→ 每 30s 轮询注入；
#       有用户新消息（chat/emote 进 recent_events）→ 重置超时计时；持续无消息 → 超时兜底（每 3 分钟加码）。
# 结束：结算关菜单 / 过夜醒来 → 判定不再命中 → 状态自复位（回到正常，不再注入）。
_CHAT_PHASE = {
    "phase": None,          # None | "wait_sleep" | "settlement"
    "poll_ts": 0.0,         # 上次 30s 轮询注入时间
    "last_msg_ts": 0.0,     # 上次看到用户新消息时间（超时计时的基准）
    "timeout_n": 0,         # 超时兜底已发次数（按 3 分钟步进加码，不刷屏）
    "entered": 0.0,         # 进入该环节的时间（诊断用）
    "just_entered": True,   # 刚进环节 → 发一次引导横幅
}
_CHAT_POLL_INTERVAL = 30      # 轮询间隔：30s 一次（恒拍板 2026-08-17）
_CHAT_TIMEOUT_STEP = 180      # 超时兜底步进：每 3 分钟没新消息发一次


def _chat_phase_banner(phase) -> str:
    """进环节引导横幅。结算用空串（菜单段已有"过夜结算…聊完 confirm_settlement"横幅，不重复）。"""
    h = _host_name()
    if phase == "settlement":
        return ""
    return f"🧾 纯聊天环节（等{h}入睡）——在床等{h}一起睡，夜过了自动进结算；可以跟{h}聊天/看状态，别跑脚本别乱动"


def _chat_phase_poll(phase) -> str:
    """30s 轮询一行。"""
    h = _host_name()
    if phase == "settlement":
        return f"🧾 结算中（30s轮询）：看看{h}有没有新消息/复盘是否完成——聊完 daily settle 进下一天"
    return f"🧾 等{h}入睡中（30s轮询）：有新消息就回应，没有就继续等"


def _chat_phase_timeout(phase, secs) -> str:
    """超时兜底一行（持续无消息加码提示）。"""
    h = _host_name()
    mins = secs // 60
    if phase == "settlement":
        return (f"⏰ 已 {mins} 分钟没有{h}的新消息——若已聊完就 daily settle 进下一天；"
                f"若还在等{h}说话，也可以再主动搭一句，或看状态条有没有待办要收尾")
    return (f"⏰ 已 {mins} 分钟没有{h}的消息——若已聊完可以继续躺着等（夜到了自动过夜结算），"
            f"或起身 daily sleep 一起睡；要不要发条消息提醒{h}该睡啦？")


def _chat_phase_line(data, menu_type, tod, loc_name) -> str:
    """🧾 纯聊天环节状态条注入（等睡 / 过夜结算期）。返回一行提示，不在环节返回空串。
    幂等：一次状态条构建至多返回一行（进环节横幅 / 30s 轮询 / 超时兜底 三选一）。
    ⚠️ 只在结算（ShippingMenu）或 AI 在床等睡时生效，其余时间零开销返回空串。"""
    global _CHAT_PHASE
    try:
        p = data.get("player") or {}
        # 判定环节（互斥：结算菜单时 AI 已醒不在床；等睡时还在床上、无结算菜单）
        if menu_type == "ShippingMenu":
            phase = "settlement"
        elif p.get("isInBed") and menu_type == "ReadyCheckDialog":
            # 2026-09-02 恒：等睡注入=【在床 isInBed + 弹准备菜单 ReadyCheckDialog】双条件。
            #   isInBed 太宽(躺一下就成立→全天冻住)；ReadyCheckDialog 又不专属睡觉(一起参与节日/乘车
            #   同样会弹等待菜单)。两者 AND 才=真"躺床等同伴入睡"，节日等车不误冻。
            phase = "wait_sleep"
        else:
            phase = None
        # 环节切换 → 复位计时
        if phase != _CHAT_PHASE["phase"]:
            _CHAT_PHASE = {"phase": phase, "poll_ts": 0.0, "last_msg_ts": time.time(),
                           "timeout_n": 0, "entered": time.time(), "just_entered": True}
        if not phase:
            return ""
        now = time.time()
        # 有新用户消息（chat/emote → recent_events）→ 重置超时计时（消息内容本身由小新闻块显示）
        try:
            for ev in (data.get("raw") or {}).get("recent_events") or []:
                if (ev or {}).get("type") in ("chat", "emote"):
                    _CHAT_PHASE["last_msg_ts"] = now
                    break
        except Exception:
            pass
        # ① 刚进入环节 → 引导横幅（一次性；结算的横幅由菜单段提供）
        if _CHAT_PHASE.get("just_entered"):
            _CHAT_PHASE["just_entered"] = False
            return _chat_phase_banner(phase)
        # ② 超时兜底（每 _CHAT_TIMEOUT_STEP 秒没新消息加码一次）——比轮询更紧急，先判
        if now - _CHAT_PHASE["last_msg_ts"] >= _CHAT_TIMEOUT_STEP * (_CHAT_PHASE["timeout_n"] + 1):
            _CHAT_PHASE["timeout_n"] += 1
            return _chat_phase_timeout(phase, int(now - _CHAT_PHASE["last_msg_ts"]))
        # ③ 30s 轮询
        if now - _CHAT_PHASE["poll_ts"] >= _CHAT_POLL_INTERVAL:
            _CHAT_PHASE["poll_ts"] = now
            return _chat_phase_poll(phase)
        return ""
    except Exception:
        return ""


def _backpack_upgrade_poi(mi=None) -> str:
    """🎒 背包升级兴趣点（2026-08-18 恒：跟节日POI一样条件出现）。
    背包<36格才返回提示，满级返回空串——状态条/map_lookup/map_query 三处共用，
    条件不满足时整个点不出现（满级后任何入口都查不到）。"""
    if mi is None:
        try:
            mi = int((api.state().get("player") or {}).get("maxItems") or 36)
        except Exception:
            mi = 36
    try:
        mi = int(mi or 36)
    except Exception:
        mi = 36
    if mi >= 36:
        return ""
    nxt = {12: "2000g", 24: "10000g"}.get(mi, "?")
    return (f"🎒 背包可升级！{mi}格 → 花 {nxt} 买 Backpack 扩到 {min(mi + 12, 36)} 格"
            "（map walk「皮埃尔商店(背包升级)」站(7,19)朝上 scene at 交互背包货架(7,18)——⚠️不是柜台！）")


def _tv_show_today(day) -> str:
    """📺 今天电视节目名（2026-08-22 恒：离地而居=周一/周四，酱料女皇=周日；无则 None=没新节目）。
    SDV day 1=周一 → (day-1)%7：0Mon 1Tue 2Wed 3Thu 4Fri 5Sat 6Sun。"""
    if not isinstance(day, int) or day < 1:
        return None
    return {0: "离地而居", 3: "离地而居", 6: "酱料女王"}.get((day - 1) % 7)


def _festival_currency_str(data: dict) -> str:
    """🎪 节日货币常驻注入（2026-08-27 恒）：在节日场地把该节日的专属货币跟在钱数后面。
    - 秋收节（星露谷展览会，fall16，Temp 图，9:00 开）= **星币**，读 player.festivalScore（/state 已有）。
    - 沙漠节（春15-17，DesertFestival 图，10:00 开）= **卡利科三花蛋**，= 背包里 name=='CalicoEgg' 的 stack 和
      （反编译确认沙漠节 HUD `eggMoneyDial` 读 `Items.CountId("CalicoEgg")`——是三花蛋物品在背包才计数；恒确认"要带在包里才算"）。
    用 /state 的 time(season/dayOfMonth/tod) + 地图判定，不依赖 activeEvent.id 格式。不是对应节日场地/未开节 → 空串。"""
    try:
        t = data.get("time") or {}
        tod = int(t.get("timeOfDay", 0) or 0)
        season = (t.get("season") or "").lower()
        day = int(t.get("dayOfMonth", 1) or 1)
        loc = (data.get("location") or {}).get("name", "")
        p = data.get("player") or {}
        # 秋收节：Temp 拍卖场图 + 秋16 + 9:00 后 → ⭐星币
        if loc == "Temp" and season == "fall" and day == 16 and tod >= 900:
            return f" | ⭐星币 {int(p.get('festivalScore') or 0)}"
        # 沙漠节：DesertFestival 图 + 春15-17 + 10:00 后 → 🥚三花蛋（数背包 CalicoEgg 物品）
        if "DesertFestival" in loc and season == "spring" and day in (15, 16, 17) and tod >= 1000:
            eggs = sum(int(it.get("stack") or 0)
                       for it in (data.get("inventory") or [])
                       if "CalicoEgg" in (it.get("name") or ""))
            return f" | 🥚三花蛋 {eggs}"
        return ""
    except Exception:
        return ""


# 🟡 step2 真增量(2026-09-02)：状态条"变才报"快照。记录上次注入的资源值；换天重置。
#    血量/体力/钱/背包/手持 平时静默，变了(或当时上下文)才写。——查完整值走 /state / check status。
_STATE_DELTA = {"day_key": None, "health": None, "stamina": None, "money": None, "bag_used": None, "tool": None,
                "loc": None, "health_n": None, "qi": None, "walnut": None}
_MONSTER_WARN = {"ts": 0.0}   # ⚔️ 农场掉血=有怪提醒，30s 节流防刷屏


def _delta_show(key, val):
    """状态条真增量判定：val 与上次注入不同 → 更新快照并返回 True；相同 → False(不重复报)。"""
    try:
        if _STATE_DELTA.get(key) != val:
            _STATE_DELTA[key] = val
            return True
    except Exception:
        return True
    return False


def _build_state_strip(data: dict, full: bool = True, morning: str = "") -> str:
    """从状态数据构建状态速报。

    full=True: 包含日历、完整动物/机器信息（每天第一次）
    full=False: 精简版，只显示必选项 + 待办提醒
    morning: 晨报块（每天第一次工具调用时由 _with_state 传入，插在头部后面）
    """
    if "error" in data and not data.get("raw"):
        return f"⚠️ 游戏未连接: {data['error']}"

    p = data.get("player", {})
    loc = data.get("location", {})
    t = data.get("time", {})
    inv = data.get("inventory", [])
    alerts = data.get("alerts", [])
    active_menu = data.get("activeMenu")
    active_event = data.get("activeEvent")

    # 🟡 step2 真增量：换天重置快照 → 每天首次(晨报)展示整份地基，此后只吐变化项。
    try:
        _dk = api.day_key()
        if _STATE_DELTA.get("day_key") != _dk:
            _STATE_DELTA.update({"day_key": _dk, "health": None, "stamina": None,
                                 "money": None, "bag_used": None, "tool": None,
                                 "loc": None, "health_n": None})
    except Exception:
        pass

    # ── 位置 & 时间（必选） ──

    x, y = p.get("x", "?"), p.get("y", "?")
    loc_name = loc.get("name", "?")

    tod = t.get("timeOfDay", "?")
    if isinstance(tod, int):
        time_str = f"{tod // 100:02d}:{tod % 100:02d}"
    else:
        time_str = str(tod)

    season = (t.get("season") or "").lower()
    day = t.get("dayOfMonth", "?")
    year = t.get("year", "?")

    day_names = ["一", "二", "三", "四", "五", "六", "日"]
    if isinstance(day, int):
        dow = day_names[(day - 1) % 7]
        day_label = f"{day}日(周{dow})"
    else:
        day_label = f"{day}日"

    season_icons = {"spring": "🌸", "summer": "☀️", "fall": "🍂", "winter": "❄️"}
    s_icon = season_icons.get(season, "📅")

    # ── 天气（文字化，方便 AI 直读） ──
    # 🌿 7=绿雨（2026-08-17 恒：SDV 1.6 weather_green_rain=7；需重编译 DLL 才报对，旧 DLL 会误报成 1雨/0晴）
    weather_texts = {0: "☀️晴", 1: "🌧️雨", 2: "⛈️雷暴", 3: "🍃风", 5: "❄️雪", 7: "🌿绿雨"}
    w = t.get("weather", 0)
    weather_text = weather_texts.get(w, "☀️晴")

    # ── 体力 & 金钱（必选） ──

    hp = f"{p.get('health','?')}/{p.get('maxHealth','?')}"
    st = p.get("stamina", "?")
    mst = p.get("maxStamina", "?")
    if isinstance(st, (int, float)):
        st_str = f"{st:.0f}/{mst}"
    else:
        st_str = f"{st}/{mst}"
    money = p.get("money", "?")
    money_str = f"{money:,}g" if isinstance(money, int) else f"{money}g"

    # ── 背包（必选） ──

    total_slots = p.get("maxItems") or 36
    used = len([i for i in inv if i.get("name")])
    free = total_slots - used
    bag_icon = "⚠️" if free <= 3 else "✓"
    bag_str = f"🎒 {used}/{total_slots}格{bag_icon}"

    # ── 当前工具（必选） ──

    ct = p.get("currentTool", "")
    tool_str = f" | 🔧 {ct}" if ct else ""

    # ── 组装 ──

    lines = []
    luck = p.get("dailyLuck")
    # 🎲 运势数值 + (年X)：只在每天第一次(full=True)显示；精简版(后续调用)不重复，省 token（2026-08-22）
    luck_str = f" | 🎲 {luck:+.3f}" if (full and luck is not None) else ""
    year_str = f" (年{year})" if full else ""
    lines.append(f"📍 {loc_name} ({x},{y}) | ⏰ {time_str} | {weather_text} · {s_icon}{season.title()} | {day_label}{year_str}{luck_str}")

    # 🗺️ map enum（#11，2026-08-13）：注入"本图可用"——商店/设施等有功能的地点报前3项，农场/家/路上不报省 token
    # ⚠️ 2026-08-16：单条长提示（节日 Temp 图）不截断到第一个括号——保留 map_go 误报等关键指引
    try:
        _map_enum = locations.MAP_FEATURES.get(loc_name)
        if _map_enum and loc_name == "Forest":
            # 🐷 猪车周五/周日才开档——非出现日/收摊不显示（2026-08-20 恒：跟节日商店一样非出现时间不显示）
            _dow = (day - 1) % 7 if isinstance(day, int) else -1
            _cart_open = _dow in (4, 6) and isinstance(tod, int) and 600 <= tod <= 2000
            if not _cart_open:
                _map_enum = [f for f in _map_enum if "猪车" not in f]
        if _map_enum and loc_name not in ("Farm", "FarmHouse", "Cabin", "Backwoods", "Tunnel", "Mine", "SkullCave"):
            if len(_map_enum) == 1:
                # 单条=完整显示（节日图等一条长指引，砍掉括号就丢了关键信息）
                _hint = _map_enum[0]
            else:
                _hint = "、".join(f.split("(")[0] for f in _map_enum[:3])
                if len(_map_enum) > 3:
                    _hint += f" 等{len(_map_enum)}项"
            lines.append(f"  🗺️ 可: {_hint}")
    except Exception:
        pass

    # 🎪 节日限定 POI enum（2026-08-24 恒：动态——未交互在前、交互过沉底、组内按离 AI 近的先；非节日不显示）
    try:
        _fps = _festival_pois_sorted(loc_name, x, y, maxn=3)   # 省 token，只列前3
        if _fps:
            _names = "、".join(_n.split("(")[0] for _n, _d in _fps)
            _total = len(_festival_pois_here(loc_name))
            _suffix = f" 等{_total}项（细节→festival poi）" if _total > len(_fps) else ""
            lines.append(f"  🎪 可: {_names}{_suffix}")
    except Exception:
        pass

    # 🕐 商店营业时间（2026-08-16 恒：主动注入；2026-08-23 恒：有小镇钥匙隐藏——能随时进镇店）
    _hlines = _shop_hours_line(loc_name)
    if _hlines:
        lines.append(_hlines + "（在柜台交互；用 menu shop 会自动走到柜台）")

    # 🎒 背包升级兴趣点（2026-08-18 恒：跟节日POI一样条件出现）——皮埃尔商店+背包<36格才出现，满级隐藏
    _bh = _backpack_upgrade_poi(p.get("maxItems")) if loc_name == "SeedShop" else ""
    if _bh:
        lines.append(f"  {_bh}")

    # 🛠️ 动态工具检测（2026-08-14 #13）：本图可用的地点绑定域（farm/mine/cabin）——帮 AI 知道调哪些域
    try:
        _dom = _domains_here(loc_name)
        if _dom:
            lines.append(f"  🛠️ 可用域: {'/'.join(_dom)}")
        elif loc_name not in ("Farm", "FarmHouse", "Cabin", "Backwoods", "Tunnel", "Mine", "SkullCave"):
            lines.append("  🛠️ 本图 farm/mine/cabin 不适用")
    except Exception:
        pass

    # 🛋️ 计划模式已退役（2026-08-17）：_plan_status_line 只剩 🌙 兜底睡觉进行中提示
    _ps = _plan_status_line()
    if _ps:
        lines.append(_ps)

    # ── 📺 每天第一次（模仿看电视）：天气 + 运势文本 + 节日/生日 + 农场晨报 ──
    #    之后只显示一行常驻日历，运势只剩第一行的数值
    cal_text = _calendar_line(data)
    if morning:
        tv = "📺 " + " | ".join(x for x in [
            weather_text,
            (f"运势：{_luck_flavor(luck)}" if luck is not None else ""),
            (f"📅 {cal_text}" if cal_text else ""),
        ] if x)
        lines.append(tv)
        # 📺 电视提示（2026-08-22 恒：按播出日显示节目名——离地而居=周一/周四、酱料女皇=周日；明天气象不动态拉，AI 想看自己点电视看今日/明日天气）
        _show = _tv_show_today(day)
        if _show == "离地而居":
            lines.append("📺 今天「离地而居」播出：点电视看农务小贴士/明日天气（scene at 点电视）")
        elif _show == "酱料女王":
            lines.append("📺 今天「酱料女王」播出：点电视学新菜谱/明日天气（scene at 点电视）")
        else:
            lines.append("📺 今日无新节目：点电视看明日天气（scene at 点电视）")
        lines.append(morning)
    elif cal_text and loc_name == "Town" and _CAL_TOWN_KEY["last"] != (season, day, year):
        # 📅 2026-09-04 恒：常驻日历（生日/休店）只在每天第一次(TV) + 顶多进Town再弹一次，避免每条状态都刷
        _CAL_TOWN_KEY["last"] = (season, day, year)
        lines.append(f"📅 {cal_text}")

    # 🎪 节日提醒（2026-08-14，省 token：只在节日当天早上 / 节日前一晚提醒一次）
    try:
        if morning:
            _ft = calendar_data.get_festival_today(season, day)
            if _ft:
                lines.append(f"🎪 今天{_ft['name']}（{_festival_time(_ft['detail'])}）在{_ft['map'] or '?'}！用 festival go 去参加")
        elif isinstance(tod, int) and tod >= 1800:
            _tom = calendar_data.get_festival_tomorrow(season, day)
            if _tom and _FEST_REMIND_KEY["last"] != (season, day):
                _FEST_REMIND_KEY["last"] = (season, day)
                lines.append(f"🌙 明天{_tom['name']}（{_festival_time(_tom['detail'])}）在{_tom['map'] or '?'}，可提前准备")
    except Exception:
        pass

    # 📋 特别任务每周一注入（2026-08-22 恒：镇板年1秋2后 + 齐先生板姜岛解锁，两块板都开才注；周一刷新）
    _so = _special_orders_inject(season, day, year, morning)
    if _so:
        lines.append(_so)

    # 📋 每日求助栏提醒（2026-08-29 恒：每天第一次进 Town 报新求助，没有不报，一天一次）
    try:
        _hw = _helpwanted_inject(loc_name)
        if _hw:
            lines.append(_hw)
    except Exception:
        pass
    # 🧺 集齐提醒（2026-08-29 恒 Part A：每日求助收集够了但没交付 → 提示去交付，一天一次）
    try:
        _hwr = _helpwanted_ready_inject()
        if _hwr:
            lines.append(_hwr)
    except Exception:
        pass
    # 🎯 特别订单奖励链提醒（2026-08-29 恒：接单/完成领奖/兑奖券可拿，到Town每日一次去重）
    try:
        _srw = _special_reward_inject(loc_name)
        if _srw:
            lines.append(_srw)
    except Exception:
        pass

    # 🌿 绿雨天提醒（2026-08-17 恒：weather=7，SDV 1.6 weather_green_rain；需重编译 DLL 才报对）。
    # 鼓励当天放下农活去打草收集苔藓（Moss）——绿雨专属掉落，能做苔藓肥料/树液采集器等。
    # ⚠️ 草/长苔藓的树/变异树的识别需真机实测（恒回来开游戏验证），这里先给方向提示。
    try:
        if w == 7 and _GREENRAIN_KEY["last"] != (season, day, year):
            _GREENRAIN_KEY["last"] = (season, day, year)
            lines.append("🌿 今天是绿雨天！放下农活，去打草收集苔藓（Moss）吧——草、长苔藓的树、变异树都是苔藓来源。\n"
                         "📍 路线：(每站 scene ops=moss)" + _greenrain_guide_brief())
    except Exception:
        pass

    # 🐷 旅行猪车提醒（2026-08-20 恒：周五/周日开店，非出现日不显示；开店当天注入一次让 AI 去逛）
    try:
        _tc = _traveling_cart_hint(season, day, tod)
        if _tc:
            lines.append(_tc)
    except Exception:
        pass

    # 🥚 蛋蛋节寻宝提示（2026-08-17 恒：节日场景 Temp 内一直注入——看坐标→自己规划→egg_note 记→egg_run 捡）
    try:
        _eh = _egg_festival_hint(loc_name)
        if _eh:
            lines.append(_eh)
    except Exception:
        pass

    # 🎪 沙漠节玩法推荐（2026-08-18 恒：节日场地每天一次——下矿流程+逛店推荐）
    try:
        _frh = _festival_recommend_hint(loc_name)
        if _frh:
            lines.append(_frh)
    except Exception:
        pass

    # 🌱 全新档第一周新手提示（morning 每天注入一次；2026-08-15 恒）
    # 🌱 年1春1一次性新手提示（2026-08-15 恒：不依赖 morning 日翻转——每次 state 构建都查，
    #    标记文件限"每存档一次"，保证 AI 第一次调用工具就被注入看到）
    try:
        _nh = _newbie_hint(season, day, year, luck, (data.get("player") or {}).get("homeLocation") or "")
        if _nh:
            lines.append(_nh)
    except Exception:
        pass

    # 🎪 节日货币跟在钱数后（2026-08-27 恒：秋收节星币 / 沙漠节三花蛋；非节日场地空串）
    try:
        _fest_curr = _festival_currency_str(data)
    except Exception:
        _fest_curr = ""
    # 🟡 step2 真增量：血量/体力/钱/背包/手持 变才报(或当时上下文常显)，平时静默省 token。
    #    血量在矿井常显；钱在商店/节日常显；背包接近满(≤3空)常显。完整值 → /state / check status。
    _in_mines = (loc_name in ("Mine", "SkullCave", "VolcanoDungeon", "VolcanoDungeon0")
                 or loc_name.startswith("UndergroundMine"))
    _menu_t2 = (active_menu or {}).get("type", "")
    _res_parts = []
    if _delta_show("health", hp) or _in_mines:
        _res_parts.append(f"❤️ {hp}")
    if _delta_show("stamina", st_str):
        _res_parts.append(f"💪 {st_str}")
    if _delta_show("money", money) or "Shop" in _menu_t2 or bool(active_event) or bool(_fest_curr):
        _res_parts.append(f"💰 {money_str}{_fest_curr}")
    if _delta_show("bag_used", used) or free <= 3:
        _res_parts.append(bag_str)   # bag_str 已含 🎒 图标
    _tool_changed = _delta_show("tool", ct)
    if ct and _tool_changed:
        _res_parts.append(f"🔧 {ct}")

    # ⚔️ 2026-09-02 恒：农场掉血=可能有怪(荒野农场/女巫雕像引到普通农场)。
    #    前一次+当前都在农场 且 血量下降 → 提醒快回家。30s 节流防连续挨打刷屏；基线首次/换天为 None 不误报。
    try:
        _hp_n = p.get("health")
        if (loc_name == "Farm" and _STATE_DELTA.get("loc") == "Farm"
                and _hp_n is not None and _STATE_DELTA.get("health_n") is not None
                and _hp_n < _STATE_DELTA["health_n"]):
            if time.time() - _MONSTER_WARN["ts"] > 30:
                _MONSTER_WARN["ts"] = time.time()
                lines.append("⚔️ 农场掉血！可能有怪(荒野/女巫雕像引来)——快回农舍躲屋里，别在场上硬扛")
        _STATE_DELTA["loc"] = loc_name
        if _hp_n is not None:
            _STATE_DELTA["health_n"] = _hp_n
    except Exception:
        pass

    if _res_parts:
        lines.append(" | ".join(_res_parts))

    # 💎🌰 step2 变才报：齐钻 + 金核桃（2026-09-02 恒）。齐钻=矿/齐先生单变化才报、核桃房(QiNutRoom)全程常显；
    #    金核桃只在姜岛变化才报。持久货币换天不重置基线 → 只在真变化时出现。（/state 尚无这两字段时 p.get=Null，自动静默）
    try:
        _qi = p.get("qiGems"); _wal = p.get("walnuts")
        if _qi is not None and (_delta_show("qi", _qi) or loc_name == "QiNutRoom"):
            lines.append(f"💎 齐钻 {_qi}")
        if _wal is not None and _delta_show("walnut", _wal) and loc_name.startswith("Island"):
            lines.append(f"🌰 金核桃 {_wal}")
    except Exception:
        pass

    # 📚 手持书 → 提示用 read_book 读（别用 /use 放地上收不回；2026-08-16 恒测读书）
    try:
        _ci = p.get("currentItem") or ""
        if _ci and any(k in _ci for k in ("Quarterly", "Treatise", "Cookbook", "Monster", "Seasonal", "书", "秘籍", "Way", "草中窜", "年历")):
            lines.append(f"📚 手持「{_ci}」——用 menu(ops=\"read_book\", name=\"{_ci}\") 读（消耗书领技能；别用 /use 会放地上）")
    except Exception:
        pass

    # 🔧 升级工具待取（2026-08-18 恒：toolBeingUpgraded 非空=有工具在铁匠铺没领。
    #     升级完成早晨的 📢 是一次性的，错过就靠这个状态字段随时发现）
    try:
        _tu = p.get("toolUpgrading")
        if _tu and _tu != _TU_REMIND["last"]:
            _TU_REMIND["last"] = _tu
            lines.append(f"🔧 铁匠铺有「{_tu}」待取（升级好了，去克林特那领：map walk 铁匠铺(柜台) 再 scene interact）")
    except Exception:
        pass

    # ⚠️ 2026-08-16 恒：低血/低体力常驻警告（禁作弊后被打昏出矿）——血<15% 或 体力<10% 一直提示
    try:
        _hp_n = int(p.get("health", 0) or 0)
        _hp_m = int(p.get("maxHealth", 1) or 1)
        _st_n = int(st) if isinstance(st, (int, float)) else 0
        _st_m = int(mst) if isinstance(mst, (int, float)) else 1
        _hp_pct = _hp_n / max(_hp_m, 1)
        _st_pct = _st_n / max(_st_m, 1)
        if _hp_pct < 0.15 or _st_pct < 0.10:
            lines.append("🚨 血量/体力危险（血{:.0f}% 体{:.0f}%）！禁作弊别硬闯——"
                         "吃食物(eat_item) / 泡温泉(BathHouse) / 躺床上(go_sleep 不确认，in_Bed 恢复) 直到健康"
                         .format(_hp_pct * 100, _st_pct * 100))
    except Exception:
        pass

    # ── 小新闻（拾取/穿脱/邮件，只显示新的；头部带背包占位） ──
    news = _news_block(data)
    if news:
        lines.append(news)

    # 🧪 buff 生效/结束提醒（2026-08-16 恒；进小新闻区；矿井内不推）
    try:
        _br = _buff_reminder(loc_name)
        if _br:
            lines.append(_br)
    except Exception:
        pass

    # ── 🌿 当前地图可采集物（有才显示，60s 冷却；采集走 pickup_scene 自动捡） ──
    forage = _forage_summary(is_green_rain=(w == 7))
    if forage:
        lines.append(forage)

    # ── 时间提醒（必选） ──

    if isinstance(tod, int):
        if tod >= 2500:
            lines.append("🌙 凌晨1点后！再不睡会在2点昏倒！")
        elif tod >= 2400:
            lines.append("🌙 过午夜了，该睡觉了")
        elif tod >= 2300:
            lines.append("🌙 晚上11点了，该准备睡觉了")

    # ── 菜单/事件（必选） ──

    menu_type = (active_menu or {}).get("type", "")   # 兜底初始化，_chat_phase_line 需要（等睡/结算判定）
    if active_menu:
        dialog = active_menu.get("dialogue")
        lines.append(f"📋 菜单打开: {menu_type}")
        _adv = _menu_advice(menu_type, active_menu, active_event)
        if _adv:
            lines.append(_adv)
        # 过夜结算：游戏时间暂停的聊天窗口——和 user 复盘今天、商量明天，聊完才确认进下一天
        if menu_type == "ShippingMenu":
            lines.append(f"🧾 过夜结算（游戏时间暂停）——和{_host_name()}聊聊今天发生了啥、明天怎么安排，聊完再调 daily settle 一起进下一天")
            # 💡 结算确认前→聊天通知给 user（2026-08-15 恒拍板：推送统一走聊天框，不走 HUD——
            #    结算时是聊天窗口，一条 chat 恒能看到；/chat 在 host 只 addMessage 不再顶输入）
            try:
                if _SETTLE_REMIND_KEY["last"] != api.day_key():
                    _SETTLE_REMIND_KEY["last"] = api.day_key()
                    _host_converse("💡 过夜结算！跟小机复盘今天、规划明天，写进白板再确认 📝")
            except Exception:
                pass
            # 🧾 30s 轮询/超时兜底由统一 _chat_phase_line 处理（见菜单段之后）。
            #   🚫 原"计划模式白板+计划一起弹出"段已随计划模式退役移除（见 _plan_* 存档）。
        if dialog:
            lines.append(f"💬 「{dialog}」")
        # 对话选项主动呈现给 AI，让它自己选（不自动跳过剧情/选项）
        responses = active_menu.get("responses")
        if responses:
            opts = [f"[{i}]{t}" for i, t in enumerate(responses)]
            lines.append(f"🗳️ 选项: {' | '.join(opts)}")
            lines.append("→ 用 menu click(option=N) 选择")

    if active_event:
        ev_msg = active_event.get("message")
        ev_id = active_event.get("id")
        if ev_msg:
            # 剧情演出中：显示当前台词，可跳过则提示（AI 可按跳过键加速）
            skip_hint = "（可跳过）" if active_event.get("skippable") else ""
            lines.append(f"🎬 演出中: 「{ev_msg}」{skip_hint}")
        elif ev_id not in (None, "-1"):
            lines.append(f"🎬 事件中: id={ev_id}")

    # 🎪 节日限时小游戏提示（2026-08-16）：ReadyCheckDialog/BobberBar 时注入今天节日引导
    try:
        _fh = _festival_activity_hint(active_menu.get("type") if active_menu else "")
        if _fh:
            lines.append(_fh)
    except Exception:
        pass
    # 🎣 鱼竿在手（钓鱼意图）：报竿上饵/钓具 + 背包饵量（2026-08-29 恒：AI 从没上过饵，没饵更要提示去哪补）
    try:
        _rh = _fishing_rod_hint(data)
        if _rh:
            lines.append(_rh)
    except Exception:
        pass
    # 🪙 本图水下闪光点(淘金)提示——只在 orePanPoint != Zero 且未报过时注入(2026-08-29)
    try:
        _ph = _pan_hint(data)
        if _ph:
            lines.append(_ph)
    except Exception:
        pass
    # 🎫 背包有「读到即消耗腾占位」道具（秘密纸条/日记残页/技能书）提醒一句（2026-08-29 恒拍板：只提醒读掉腾格）
    try:
        _sn = _read_to_free_hint(data)
        if _sn:
            lines.append(_sn)
    except Exception:
        pass
    # 🎰 赌场小游戏按钮引导（2026-08-23 恒）：AI 打开老虎机/21点，状态条直接告诉它该点啥
    try:
        _mgh = _minigame_guide_hint()
        if _mgh:
            lines.append(_mgh)
    except Exception:
        pass
    # 🎮 小游戏开始/结束检测（2026-08-24 恒：只报开始/结束，玩法引导在上面已有）
    try:
        _act = _activity_change_notice(data)
        if _act:
            lines.append(_act)
    except Exception:
        pass
    # 🎪 到达节日地点弹一次引导（当天一次，2026-08-16 恒：鱿鱼节到海滩弹）
    try:
        _fa = _festival_arrive_hint(loc_name)
        if _fa:
            lines.append(_fa)
    except Exception:
        pass
    # 🗿 每日雕像提醒（2026-08-16 恒：祝福雕像/矮人国王雕像，摸拿每日buff）
    try:
        _sr = _statue_reminder()
        if _sr:
            lines.append(_sr)
    except Exception:
        pass
    # 🔔 本图设备实时就绪（2026-08-31 恒：短时设备当天中途完成也能看到，一次即止）
    try:
        _mr = _machine_ready_hint(loc_name)
        if _mr:
            lines.append(_mr)
    except Exception:
        pass

    # 🧾 纯聊天环节（2026-08-17 恒）：等睡（AI 在床等恒）/ 过夜结算（ShippingMenu）期——
    #    只聊天，30s 轮询 + 新消息推送（recent_events 的 chat/emote 重置超时）+ 无消息超时兜底。
    try:
        _cpl = _chat_phase_line(data, menu_type, tod, loc_name)
        if _cpl:
            lines.append(_cpl)
    except Exception:
        pass

    # ── 🎬 剧情台词（纯文本对话自动推进累积；到选项/结束停下，一次性报给 AI） ──
    if _story_buffer:
        joined = " ".join(f"「{l}」" for l in _story_buffer[-6:])
        lines.append(f"🎬 剧情: {joined}{'…' if len(_story_buffer) > 6 else ''}")
    # 剧情完全结束（没有菜单也没有事件）→ 清空缓冲
    if _story_buffer and not active_menu and not active_event:
        _story_buffer.clear()

    # ── 系统警报（必选） ──

    warnings = [a for a in alerts if a.get("severity") in ("warning", "error")]
    # 🔧 2026-09-04 恒：/alerts 用 peek=True 不消费 → 旧"inventory_full:36/36"会一直重播（背包已腾出空位还报满）。
    #    按 type 去重留最新 + inventory 用即时格数覆写，纠正"实际格子数"。
    latest = {}
    for a in warnings:
        latest[a.get("type", "")] = a          # later wins → 每 type 留最新一条
    for w in list(latest.values())[-4:]:
        wtype = w.get("type", "")
        msg = w.get("message", "")
        if wtype in ("inventory_full", "inventory_space"):
            if used >= total_slots:           # 即时格数覆写，别信排队旧文本
                msg = f"Inventory full: {used}/{total_slots}"
            else:
                continue                        # 实际还有空位 → 不报满（纠正 stale full）
        icons = {
            "stamina_low": "💫", "inventory_full": "📦",
            "water_empty": "💧", "menu_opened": "📋",
        }
        lines.append(f"{icons.get(wtype, '⚠️')} {msg}")

    # ── 地面掉落物提示（全地图，有才显示） ──
    debris_data = data.get("debris")
    if debris_data:
        parts = [f"{k}x{v}" for k, v in sorted(debris_data.items(), key=lambda x: -x[1])]
        lines.append(f"📦 地上: {', '.join(parts[:4])}{'…' if len(parts) > 4 else ''}")

    # 🏛️ 可捐赠 → 不再状态条常驻（恒批注 2026-08-13），改在 check_backpack 背包详情里注释

    return "\n".join(lines)


def _news_block(data: dict) -> str:
    """📰 小新闻：渲染 recent_events（拾取/穿脱/邮件）。

    mod 端"看过即清空"（_gather_state 用 consume_events=True 消费），所以这里
    拿到的都是没看过的，天然"截至上一次调用"。头部带背包占位（12/12（已满!））。
    """
    raw = data.get("raw") or {}
    events = raw.get("recent_events") or []
    if not events:
        return ""

    inv = data.get("inventory") or []
    p = data.get("player") or {}
    max_items = p.get("maxItems") or 36
    used = len([i for i in inv if i.get("name")])
    full_note = "（已满!）" if used >= max_items else ""
    lines = [f"📰 🎒 {used}/{max_items}{full_note}"]
    for ev in events:
        content = (ev or {}).get("content") or ""
        if not content:
            continue
        # 🧠 会话缓冲：小新闻也归档（拾取/邮件/表情/换装等）
        _session_append("游戏事件", content, data.get("location", {}).get("name"), ev.get("type"))
        for ln in content.split("\n"):
            lines.append(f"  {ln}")
    return "\n".join(lines)


# 穿戴物签名缓存：只在穿戴物变化时才在状态条显示（省 token）
_last_worn_signature = None
_last_worn_check_ms = 0.0
_WORN_COOLDOWN_MS = 30000   # 30 秒内不重复查 /worn（换装是低频事件）


# ── 当前地图可采集物汇报（有什么、几颗；采集走 pickup_scene 脚本） ──
# 黑名单同 pickup_scene.py（机器/家具/工具/杂物不报，珊瑚/蛤蜊/松露等采集物保留）
_FORAGE_BLACKLIST = {
    "Sprinkler", "Quality Sprinkler", "Iridium Sprinkler", "Pressure Nozzle",
    "Scarecrow", "Deluxe Scarecrow", "Campfire", "Torch", "Stump",
    "Sign", "Wood Sign", "Stone Sign", "Workbench", "Grave Stone",
    "Crab Pot", "Furnace", "Charcoal Kiln", "Recycling Machine",
    "Worm Bin", "Keg", "Preserves Jar", "Cheese Press", "Loom",
    "Mayonnaise Machine", "Oil Maker", "Seed Maker", "Crystalarium",
    "Tapper", "Heavy Tapper", "Bee House", "Silo", "Slime Egg",
    "Slime Incubator", "Mini-Fridge", "End Table", "TV", "Radio",
    "Picture", "Floor", "Path", "Cobblestone Path", "Wood Floor",
    "Stone Floor", "Brick Floor", "Wood Path", "Crystal Floor",
    "Rug", "Fish Pond", "Mill", "Coop", "Barn", "Shed", "Obelisk",
    "Greenhouse", "Mushroom Box", "Statue Of Endless Fortune", "Safe",
    "Skeleton", "Museum", "Horse", "Milk Pail", "Shears", "Scythe",
    "Furniture", "Armchair", "Bench", "Chair", "Table", "Couch",
    "Dresser", "Stool", "Bookshelf", "Fireplace",
    "Pickaxe", "Axe", "Hoe", "Watering Can", "Sword",
    "Fishing Rod", "Copper Pickaxe", "Iron Pickaxe", "Gold Pickaxe",
    "Iridium Pickaxe", "Copper Axe", "Iron Axe", "Gold Axe", "Iridium Axe",
    "Copper Hoe", "Iron Hoe", "Gold Hoe", "Iridium Hoe",
    "Copper Watering Can", "Iron Watering Can", "Gold Watering Can",
    "Iridium Watering Can",
    "Weeds", "Stone", "Rock", "Glass Shards", "Rotten Plant",
    "Twig", "Grass Starter", "Fiber",
    "Incubator", "Heater", "Feed Hopper", "Auto-Grabber", "Auto-Petter",
    "Slime Hutch", "Egg Basket", "Duck Egg Basket",
    "Ostrich Incubator", "Hay", "Automatic Feeders",
}
_last_forage_loc = ""   # 上一次采集扫描的地图名（切图才扫一次，恒批注 2026-08-13）
_FORAGE_INDOOR_KEYWORDS = ("House", "Mine", "Underground", "Cave", "Shed",
                           "Barn", "Coop", "Saloon", "Shop", "BathHouse")


def _count_clump_blocks(points: set) -> int:
    """🌿 数苔藓大块（Clump:46）的"块数"——每个连片 2×2 clump 报 4 格，按 4-邻域连通域去重为 1 块，
    避免把一个大块数成 4 格。2026-08-21 恒确认：大块与小块(单格 GreenRainWeeds 对象)是不同类型。"""
    if not points:
        return 0
    seen = set()
    blocks = 0
    for start in points:
        if start in seen:
            continue
        blocks += 1
        stack = [start]
        seen.add(start)
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in points and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    stack.append((nx, ny))
    return blocks


def _forage_summary(is_green_rain: bool = None) -> str:
    """🌿 当前地图可采集物汇总（有什么、几颗）。**切图时扫一次** + 室内跳过。
    只报告数量不报位置——决定采集后走 pickup_scene 自动走过去捡。
    is_green_rain: 是否绿雨天（None=自动查 weather==7）。苔藓类只在绿雨当天暴露，
    除非设置 moss expose_all_days=on（2026-08-21 恒）。"""
    global _last_forage_loc
    try:
        r = api.surroundings(25)
        loc = r.get("location", "") or ""
        if loc == _last_forage_loc:
            return ""          # 同一张图不重复扫（切图才扫）
        _last_forage_loc = loc
        if any(k in loc for k in _FORAGE_INDOOR_KEYWORDS):
            return ""          # 室内跳过
        # 🌿 苔藓暴露门控（2026-08-21 恒）：只在绿雨当天报，除非设置 moss expose_all_days=on
        if is_green_rain is None:
            try:
                is_green_rain = (api.state().get("time", {}).get("weather") == 7)
            except Exception:
                is_green_rain = False
        show_moss = is_green_rain or _moss_cfg.get("expose_all_days", False)
        # 采集/挖掘分类统计（2026-08-17 恒：浆果灌木/斑点/姜/大葱/苔藓树全接入）
        berry_bushes = 0    # 🍓 灌木季节结果（摇）
        spot_count = 0      # 🪱 蚯蚓点590 + 远古斑点SeedSpot（锄）
        ginger_count = 0    # 🫚 姜点 forageCrop="2"（锄，hitWithHoe）
        onion_count = 0     # 🌱 大葱 forageCrop="1" 成熟可收（摘）
        truffle_count = 0   # 🍄 猪产松露（放在 loc.Objects 的 (O)430，isPassable()=false，不靠 passable 判）
        moss_tree_count = 0       # 🌿 长苔藓树 moss:True（镰刀打苔藓）
        greenrain_tree_count = 0  # 🪓 苔雨树 greenRainTree:True（斧头砍）
        moss_weed_small = 0       # 🌿 苔藓杂草-小块 GreenRainWeeds* 对象（镰刀/剑）
        moss_big_tiles = set()    # 🌿 苔藓杂草-大块 Clump:46 瓦片坐标（去重后数块数）
        has_hoe = api.has_item("Hoe")
        counts = {}
        for t in r.get("tiles", []):
            if t.get("terrain") == "Bush" and t.get("bushBloom"):
                berry_bushes += 1
                continue
            if has_hoe and t.get("forageCrop") == "2":
                ginger_count += 1
                continue
            if t.get("forageCrop") == "1" and t.get("harvestable"):
                onion_count += 1
                continue
            terr = t.get("terrain") or ""
            # 🌿 树苔藓（2026-08-21）：长苔藓树 moss:True（镰刀打）+ 苔雨树 greenRainTree:True（斧头砍）；
            #    单个树可同时 moss:True+greenRainTree:True（两方都计）。仅 show_moss 时计入。
            if terr.startswith("Tree:"):
                if show_moss:
                    if t.get("moss"):
                        moss_tree_count += 1
                    if t.get("greenRainTree"):
                        greenrain_tree_count += 1
                continue
            # 🌿 苔藓杂草块（2026-08-21 恒确认：大块/小块是不同类型）——大块=Clump:46 resource(2×2跨4格)，小块=GreenRainWeeds* 对象(单格)
            if show_moss and t.get("resource") == "Clump:46":
                moss_big_tiles.add((t.get("x"), t.get("y")))
                continue
            if show_moss and (t.get("object") or "").startswith("GreenRainWeeds"):
                moss_weed_small += 1
                continue
            if has_hoe and t.get("objId") in ("(O)590", "590", "(O)SeedSpot", "SeedSpot"):
                spot_count += 1
                continue
            # 🍄 2026-09-01 猪松露：isPassable()=false（Category -81 动物产物不在游戏 passable 白名单）
            #    → passable 判定会甩掉它；松露=直接可捡的第一等采集物，按 objId 认、不依赖 passable。
            if t.get("object") == "Truffle" or t.get("objId") in ("430", "(O)430"):
                truffle_count += 1
                continue
            if not t.get("passable", True):
                continue
            obj = t.get("object")
            if not obj:
                continue
            if any(blk in obj for blk in _FORAGE_BLACKLIST):
                continue
            counts[obj] = counts.get(obj, 0) + 1
        moss_weed_big = _count_clump_blocks(moss_big_tiles)
        if not counts and not berry_bushes and not spot_count and not ginger_count \
                and not onion_count and not truffle_count and not moss_tree_count \
                and not greenrain_tree_count and not moss_weed_big and not moss_weed_small:
            return ""
        parts = []
        if berry_bushes: parts.append(f"🍓浆果灌木×{berry_bushes}")
        if ginger_count: parts.append(f"🫚姜×{ginger_count}")
        if onion_count: parts.append(f"🌱大葱×{onion_count}")
        if truffle_count: parts.append(f"🍄松露×{truffle_count}")
        if moss_weed_big or moss_weed_small:
            _lbl = f"🌿苔藓杂草×{moss_weed_big + moss_weed_small}"
            if moss_weed_big and moss_weed_small:
                _lbl += f"(大块{moss_weed_big}/小块{moss_weed_small})"
            elif moss_weed_big:
                _lbl += f"(大块{moss_weed_big})"
            else:
                _lbl += f"(小块{moss_weed_small})"
            parts.append(_lbl)
        if moss_tree_count: parts.append(f"🌿长苔藓树×{moss_tree_count}(镰刀)")
        if greenrain_tree_count: parts.append(f"🪓苔雨树×{greenrain_tree_count}(斧头)")
        if spot_count: parts.append(f"🪱斑点×{spot_count}")
        parts += [f"{k}×{v}" for k, v in sorted(counts.items(), key=lambda x: -x[1])]
        return f"🌿 可采集: {', '.join(parts[:6])}{'…' if len(parts) > 6 else ''}"
    except Exception:
        return ""


def _worn_summary() -> str:
    """🧥 穿戴物一行摘要（名称+属性+戒指合成），只在穿戴物变化时返回。
    挂在状态条背包行后面：换装备/改穿戴时提示一次，不变就不占 token。
    全裸（啥都没穿）时返回空串，不显示。30 秒冷却防每调用都查 /worn。
    """
    global _last_worn_signature, _last_worn_check_ms
    _now_ms = time.time() * 1000
    if _now_ms - _last_worn_check_ms < _WORN_COOLDOWN_MS:
        return ""
    _last_worn_check_ms = _now_ms
    try:
        r = api._get("/worn")
        if not r.get("ok"):
            return ""
        w = r.get("worn", {})
        parts = []
        shirt = w.get("shirt") or "无"
        pants = w.get("pants") or "无"
        hat = w.get("hat") or "无"
        acc = w.get("accessory") or "无"
        parts.append(f"🧥{shirt}|{pants}|{hat}|{acc}")
        b = w.get("boots")
        if b:
            parts.append(f"👢{b.get('name','?')}(防{b.get('defense','?')}免{b.get('immunity','?')})")
        for slot, icon in (("leftRing", "💍左"), ("rightRing", "💍右")):
            ring = w.get(slot)
            if ring:
                if ring.get("combined"):
                    parts.append(f"{icon}{ring.get('name','?')}(合{'&'.join(ring['combined'])})")
                else:
                    parts.append(f"{icon}{ring.get('name','?')}")
        t = w.get("trinket")
        if t:
            eff = t.get("effect") or {}
            eff_str = ", ".join(f"{k}={v}" for k, v in eff.items() if not k.startswith("<")) if eff else "?"
            parts.append(f"🔮{t.get('name','?')}({eff_str})")
        text = "🧥 穿戴: " + " | ".join(parts) if parts else ""
        if text == _last_worn_signature:
            return ""   # 没变化，不显示
        _last_worn_signature = text
        return text
    except Exception:
        return ""


# 机器类型中文名映射（晨报/ machine_report 用）
MACHINE_CN = {
    "Keg": "小桶", "Cask": "酒桶", "Furnace": "熔炉", "Heavy Furnace": "重型熔炉",
    "Dehydrator": "烘干机", "Fish Smoker": "熏鱼机", "Preserves Jar": "罐头瓶",
    "Cheese Press": "压酪机", "Mayonnaise Machine": "蛋黄酱机", "Loom": "织布机",
    "Oil Maker": "产油机", "Seed Maker": "种子机", "Crystalarium": "复制机",
    "Charcoal Kiln": "木炭窑", "Bee House": "蜂房", "Tapper": "树液采集器",
    "Heavy Tapper": "重型树液采集器", "Lightning Rod": "避雷针", "Solar Panel": "太阳能板",
    "Wood Chipper": "碎木机", "Bones Mill": "碎骨机", "Worm Bin": "虫饵盒",
    "Bait Maker": "鱼饵制造机", "Recycling Machine": "回收机",
}


def _fetch_farm_report() -> dict:
    """抓取全农场报告，失败返回 {}（不抛异常）。"""
    try:
        return api.farm_report()
    except Exception:
        return {}


def _fetch_fish_ponds() -> list:
    """抓取所有鱼塘状态（产出就绪=条目 output/outputId 非空），失败返回 []（不抛异常）。
    鱼塘是 Building 不是 bigCraftable，不在 farm_report 的 machines 里——单独走 /fish_pond。"""
    try:
        r = api._ai_post("/fish_pond", {"action": "list"})
        return (r or {}).get("ponds") or []
    except Exception:
        return []


def _pond_ready_summary(ponds: list) -> str:
    """把"有产出"的鱼塘按产物种类+数量聚合成一段（如 `鲑鱼子×1 蚌×1`），无则空串。"""
    agg = {}
    for p in ponds:
        if not p.get("output"):
            continue
        out = p.get("output") or "?"
        agg[out] = agg.get(out, 0) + 1
    return " ".join(f"{k}×{v}" for k, v in agg.items())


def _build_morning_report(fr: dict) -> str:
    """构建全农场晨报块（仿游戏农场电脑），空则返回空串。"""
    if not fr.get("ok"):
        return ""
    lines = ["🌅 农场晨报:"]
    c = fr.get("crops") or {}
    if c.get("total"):
        lines.append(f"  🌱 作物: 已浇水 {c.get('watered', 0)}/{c['total']} | 已成熟 {c.get('ready', 0)}/{c['total']}")
    al = (fr.get("animals") or {}).get("animals") or []
    if al:
        agg = {}
        for a in al:
            k = a.get("building") or "Farm"
            g = agg.setdefault(k, {"total": 0, "unpet": 0, "ready": 0})
            g["total"] += 1
            if not a.get("wasPetToday"):
                g["unpet"] += 1
            if a.get("productReady"):
                g["ready"] += 1
        pend = [f"{k} 待摸{v['unpet']} 可收{v['ready']}"
                for k, v in agg.items() if v["unpet"] or v["ready"]]
        if pend:
            lines.append(f"  🐄 动物: {' | '.join(pend)}")
    ml = (fr.get("machines") or {}).get("machines") or []
    if ml:
        agg = {}
        for m in ml:
            t = agg.setdefault(m.get("type") or "?", {"total": 0, "ready": 0})
            t["total"] += 1
            if m.get("status") == "ready":
                t["ready"] += 1
        # 只报有完成的 + 排除杂物（箱子/稻草人/装饰/储物类），省 token
        # ⚠️ 2026-08-31 恒：回收机/避雷针/鱼饵制造机是"有产出要收"的设备——从 SKIP 去掉，别漏报
        SKIP = {"Chest", "Stone Chest", "Rarecrow", "Scarecrow", "Heater",
                "Feed Hopper", "Incubator", "Mini-Jukebox", "Statue Of Blessings",
                "Stardew Hero Trophy", "Sewing Machine", "Mini-Forge",
                "Mini-Shipping Bin", "Auto-Petter", "Auto-Grabber",
                "Garden Pot", "Anvil", "Stone Junimo"}
        parts = [f"{MACHINE_CN.get(k, k)} 完成 {v['ready']}/{v['total']} 台"
                 for k, v in agg.items() if v["ready"] > 0 and k not in SKIP]
        if parts:
            lines.append(f"  ⚙️ 机器: {' | '.join(parts)}")
    # 🐟 鱼塘产出（2026-08-31 恒：鱼塘是建筑不在 machines 里，单独并入日报）
    try:
        _ps = _pond_ready_summary(_fetch_fish_ponds())
        if _ps:
            lines.append(f"  🐟 鱼塘: {_ps}")
    except Exception:
        pass
    return "\n".join(lines) if len(lines) > 1 else ""


def _progress_line(kind: str) -> str:
    """处理完机器/动物后刷新剩余进度行。kind: 'machines'|'animals'"""
    fr = _fetch_farm_report()
    if not fr.get("ok"):
        return ""
    if kind == "machines":
        ml = (fr.get("machines") or {}).get("machines") or []
        n = sum(1 for m in ml if m.get("status") == "ready")
        return f"📊 还剩 {n} 台机器完成" if n else "📊 机器都收完了"
    else:
        al = (fr.get("animals") or {}).get("animals") or []
        n = sum(1 for a in al if not a.get("wasPetToday"))
        return f"📊 还剩 {n} 只动物待摸" if n else "📊 动物都摸完了"


# 🎬 剧情台词缓冲：纯文本对话/事件自动推进时收集台词，到选项/结束停下一次性报给 AI。
# _build_state_strip 在无 activeMenu 且无 activeEvent 时清空（剧情结束）。
_story_buffer: list = []


def _advance_story(active_menu, active_event) -> bool:
    """剧情自动走：事件/纯文本对话自动推进并缓存台词，到选项/结束停下。
    - activeEvent 在播 → 对话阶段优先进程内 key confirm（不碰 OS 鼠标），连续 2 轮没推进再退 /click；
      缓冲 activeEvent.message；静默阶段(走路)用 key confirm，菜单非对话/有选项/事件结束就停。
    - 无事件 → 退回 _dismiss_dialogue（普通 DialogueBox 用 confirm 推进）。
    - ⚠️ 2026-08-15 恒：触发不稳定时 20s 无台词推进（非选项卡住）→ skip 保底跳过剧情。
    - ⚠️ 2026-08-16 #5：/click 的 no-menu 分支（ModEntry HandleClick）用 OS 鼠标点屏幕中心，
      后台/窗口变化时可能点到错误窗口（用户观察到 AI 操作到 7842）——这里对话优先进程内 key confirm
      （Game1.pressActionButton / receiveActionPress，IsActive 补丁下失焦也能推进），
      2 轮无进展才退 /click（事件 receiveLeftClick 兜底）。DLL 已加 /click no_mouse 参数
      （no-menu 分支走 pressActionButton，不用 OS mouse_event），这里退 /click 也传 no_mouse=true 彻底不碰 OS 鼠标。
    """
    if active_event and active_event.get("id"):
        # 🎪 2026-08-28 恒：撤销"节日事件(festival_*)不自动推进"——当年是原作者的 festival bot 造成对话混乱才加的禁；
        #    festival bot 已删除，现在 AI 用 advance_story 能正常推进节日 monologue/对话（冰钓 monologue 就用它推）。
        #    ⚠️ 仅保留下方 festivalTimer>0（限时小游戏/冰钓进行中=玩家已接管）那条不推进。
        try:
            # 🥚 限时小游戏进行中（festivalTimer>0，如蛋蛋节寻宝/冰钓）= 玩家已接管 → 立刻停止推进
            #    （2026-08-17 恒：之前寻宝开始后 activeEvent 没变，_advance_story 还在点 confirm 造成延迟/干扰）
            if _festival_timer() > 0:
                return True
        except Exception:
            pass
        try:
            # 事件推进（2026-08-12 实测）：对话阶段 /click（→receiveLeftClick）最稳，
            # 但 /click 无菜单时走 OS 鼠标（#5 根因）→ 先试 key confirm，2 轮无进展再退 /click。
            advanced = 0
            last_line = ""
            no_progress_start = time.time()
            no_prog = 0
            for _ in range(15):
                line = (active_event.get("message") or "").strip()
                if line and (not _story_buffer or _story_buffer[-1] != line):
                    _story_buffer.append(line)
                # 非事件对话菜单（商店/背包等）或出现选项 → 停，让 AI 处理
                if active_menu and active_menu.get("type") != "DialogueBox":
                    break
                if active_menu and active_menu.get("responses"):
                    break
                # ⚠️ 20s 无台词推进（非选项卡住）→ skip 保底跳过（2026-08-15 恒）
                if line != last_line:
                    no_progress_start = time.time()
                    last_line = line
                    no_prog = 0
                else:
                    no_prog += 1
                if time.time() - no_progress_start >= 20:
                    api.key("skip")
                    time.sleep(1.5)
                    st = api.state(light=True)
                    if not st.get("activeEvent"):
                        return True  # 跳过成功
                    break
                if active_menu:
                    # 对话：优先进程内 key confirm（不碰 OS 鼠标）；2 轮无推进 → 退 /click 兜底
                    #（no_mouse=true：DLL no-menu 分支走 pressActionButton，不碰 OS 鼠标，#5 根治）
                    if no_prog >= 2:
                        api._post("/click", {"no_move": True, "no_mouse": True})   # 事件 receiveLeftClick/action
                        no_prog = 0
                    else:
                        api.key("confirm")
                else:
                    # 静默（走路/转场）：key confirm 优先；2 轮无推进 → /click 兜底
                    # ⚠️ 2026-08-16 威利沙滩事件实测：某静默段 key confirm 推不动，要真实点击 → 加 /click 兜底
                    if no_prog >= 2:
                        api._post("/click", {"no_move": True, "no_mouse": True})
                        no_prog = 0
                    else:
                        api.key("confirm")       # 静默：pressActionButton
                advanced += 1
                time.sleep(0.7)
                st = api.state(light=True)
                active_event = st.get("activeEvent") or {}
                active_menu = st.get("activeMenu") or {}
                if not active_event.get("id"):
                    break  # 事件结束
            return advanced > 0
        except Exception:
            return False
    return _dismiss_dialogue(active_menu)


@mcp.tool()
def advance_story() -> str:
    """🎬 推进剧情/对话（检测事件还在 → 自动走完当前段 → 返回台词+状态）
    AI 卡剧情/不知道按啥时先调这个：检测 activeEvent/DialogueBox，推进到选项或结束。
    事件对话阶段自动 /click 点（不动鼠标）、静默阶段 key confirm；选项出现停下让 AI 选。
    返回已播放台词 + 当前进度（事件仍在播 / 到选项 / 剧情结束）。"""
    try:
        st = api.state(light=True)
        ev = st.get("activeEvent") or {}
        m = st.get("activeMenu") or {}
        if not (ev.get("id") or m.get("type") == "DialogueBox"):
            return _with_state("🎬 当前没有剧情/对话（不需要推进）")
        _advance_story(m, ev)
        time.sleep(0.3)
        lines = ["🎬 剧情推进:"]
        if _story_buffer:
            lines.append("  " + "｜".join(_story_buffer[-5:]))
        st2 = api.state(light=True)
        ev2 = st2.get("activeEvent") or {}
        m2 = st2.get("activeMenu") or {}
        if ev2.get("id"):
            lines.append(f"  ⏳ 事件仍在播（id={ev2['id']}），再调 advance_story 继续推进")
        elif m2.get("type") == "DialogueBox" and m2.get("responses"):
            lines.append(f"  💬 出现选项: {m2.get('responses')} → menu_click(option=N) 选择")
        elif m2.get("type") == "DialogueBox":
            lines.append("  💬 对话继续，再调 advance_story")
        else:
            lines.append("  ✅ 剧情结束")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 推进剧情失败: {e}")


def _dismiss_dialogue(active_menu=None) -> bool:
    """如果对话框开着就点掉它（纯文本对话），确保后续操作不被挡住。
    active_menu 传最近一次 /state 的 activeMenu；无对话框则直接跳过（省一次 HTTP）。
    点击前把台词收进 _story_buffer ——"剧情自动走"：自动推进 + 缓存台词，
    到选项/结束停下，台词一次性报给 AI（见状态条的 🎬 剧情行）。
    """
    if active_menu is None:
        try:
            active_menu = api.state().get("activeMenu")
        except Exception:
            return False
    if not (active_menu and active_menu.get("type") == "DialogueBox" and not active_menu.get("responses")):
        return False
    try:
        for _ in range(10):
            line = (active_menu.get("dialogue") or "").strip()
            if line and (not _story_buffer or _story_buffer[-1] != line):
                _story_buffer.append(line)
            api.key("confirm")
            time.sleep(0.2)
            active_menu = api.state().get("activeMenu")
            if not (active_menu and active_menu.get("type") == "DialogueBox" and not active_menu.get("responses")):
                break
        return True
    except Exception:
        pass
    return False


def _ensure_background(base_url: str = "") -> None:
    """后台菜单/锻造/吃东西前确保目标进程"失焦不暂停"（Harmony IsActive 补丁依赖它生效）。
    目标进程 pauseWhenOutOfFocus=false → Game.IsActive 强制 true → 后台窗口也完整跑（菜单/动画/吃东西结算）。
    短超时静默失败：旧 DLL 没有 /set_pause 端点也能跑，不阻塞工具。"""
    try:
        if base_url:
            import requests as _req
            _req.post(f"{base_url}/set_pause", json={"outOfFocus": False}, timeout=4)
        else:
            api.set_pause(False)
    except Exception:
        pass


def _menu_close(base_url: str = "") -> dict:
    """可靠关闭菜单 + 清光标（2026-08-11 新端点 /menu_close）。
    解决"光标有物品时商店 readyToClose()=false 关不掉"的死结：C# 端先 CollectOrDrop 放回背包再强关。
    返回响应 dict（含 clearedCursor），失败返回 {}。"""
    try:
        if base_url:
            import requests as _req
            return _req.post(f"{base_url}/menu_close", timeout=8).json()
        return api._post("/menu_close")
    except Exception:
        return {}


def _with_state(result: str, force_full: bool = False) -> str:
    """工具结果末尾附加状态速报 + 玩家动态（按心跳频率）。
    自动点掉挡路的对话框（用刚拉的状态判断，无对话框则零额外 HTTP）。
    force_full=True 强制完整版状态条（check_status 用）。"""
    # 非文本结果（如 bobber_style("see") 返回的菜单图）原样返回，不附状态条
    if not isinstance(result, str):
        return result

    # 状态条策略：
    # - 每天第一次工具调用 → full=True（节日/日历全显示）
    # - 之后 → full=False（仅必选项 + 农场待办）
    # - 服务器启动后第一次调用 → 附加欢迎横幅
    global _first_call_since_start, _bg_last_ai_activity
    # ⚠️ 2026-08-16 恒：状态拉取放后台线程 + 8s 超时兜底——游戏卡住/睡觉转换时 _gather_state 可能挂起，
    #    导致工具调用永不返回、AI 一直干等（实测 script_status 挂死）。超时则只返回工具结果（不附状态条）。
    _box: dict = {}
    def _gather_safe():
        try:
            _box["data"] = _gather_state()
        except Exception as e:
            _box["err"] = str(e)
    _th = _threading.Thread(target=_gather_safe, daemon=True)
    _th.start()
    _th.join(timeout=8)
    if "data" not in _box:
        return result + ("\n⚠️ 状态获取超时/失败（游戏可能未响应）" if result else "⚠️ 状态获取超时/失败")
    data = _box["data"]
    # 🎬 2026-08-19 恒拍板：**不再自动推进剧情**。之前每次工具调用自动 _advance_story，
    #   超出恒只要的"场景切换检测到剧情就停"——还会打断节日事件（festival_*）。
    #   现在状态条照常报「🎬 剧情」，由 AI 自己决定调 advance_story 推进。

    # ── 欢迎横幅（仅启动后第一次） ──
    welcome = ""
    if _first_call_since_start:
        _first_call_since_start = False
        welcome = WELCOME_BANNER + "\n"

    full = True if force_full else _is_new_day(data)

    # ── 晨报（仅每天第一次工具调用，懒加载全农场扫描） ──
    morning = _build_morning_report(_fetch_farm_report()) if full else ""

    # ── 玩家动态（受心跳间隔控制） ──
    # 心跳检测的是"用户(房主)"的状态给 AI 看，不是 AI 自己
    activity_line = ""
    try:
        if player_activity.should_inject():
            activity_line = _heartbeat_line(data) + "\n"
    except Exception:
        pass  # 不影响主流程

    # ── 后台脚本提醒（受 async wake_interval 控制，B1） ──
    # 异步脚本运行期间，按醒来间隔提醒"脚本还在跑"，AI 不用每次手动查
    # ⚠️ 睡觉(ReadyCheckDialog)时也提示（2026-08-16 恒确认：躺床上等恒确认时可以闲聊/做轻量操作，不打断脚本）
    script_line = ""
    try:
        script_line = _bg_activity_line()
    except Exception:
        pass
    if script_line:
        script_line += "\n"
    # ⚠️ 记录本次工具调用时间——下次 _bg_activity_line 判断"AI 距上次操作"用（不打断连续操作）
    _bg_last_ai_activity = time.time()

    # ── 🛋️ 计划通知（调度器/兜底产生的完成/暂停/中止/节日等事件）──
    plan_note = ""
    try:
        plan_note = _plan_drain_notices()
    except Exception:
        pass
    if plan_note:
        plan_note += "\n"

    strip = _build_state_strip(data, full=full, morning=morning)
    sep = "\n\n╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
    # 🎣 冰雪节冰钓自动 hook（2026-08-28 恒）：检测到比赛开钓(festivalTimer>0)且本局未发过 → 阻塞跑 ice_fishing
    #    挂在 _with_state：**任意**工具调用（不等 AI 主动调 festival ice_fish）都能命中，且 AI 正在钓就干等不乱跑。
    ice_line = ""
    try:
        ice_line = _maybe_ice_fishing_auto(data)
        if ice_line:
            ice_line += "\n"
    except Exception:
        pass
    # 🥚 蛋蛋节捡蛋自动 hook（2026-09-05）：寻宝开赛(festivalTimer>0)→有纸条自动阻塞捡/没纸条只提醒。
    #    挂在 _with_state：**任意**工具调用（不等 AI 主动调 festival egg_run）都能命中，且开着就阻塞不能添乱。
    egg_line = ""
    try:
        egg_line = _maybe_egg_run_auto(data)
        if egg_line:
            egg_line += "\n"
    except Exception:
        pass
    return f"{welcome}{result}{sep}{ice_line}{egg_line}{plan_note}{activity_line}{script_line}{strip}"


# ── 工具辅助函数 ──

# ⚠️ 2026-08-15 修复：这些脚本默认打 host 7842（会挪恒的角色/耗恒体力），必须注入 --port AI端口
_PORT_SCRIPTS = {"water_crops", "chop_trees", "clear_area", "mine_run", "fish_run",
                 "bomb_mine", "bomb_escort", "bomb_volcano", "farm_row", "go_to",
                 "berry_run", "spot_run", "moss_run", "trash_run", "fair_fishing",
                 "rock_run", "fruit_round"}   # 🏠 2026-08-31：fruit_round 收放改走严格交互，注入 --port AI
                 # 🍓🪱 2026-08-17：摇树莓/挖斑点脚本注入 AI 端口（防挪恒角色）；🌿 2026-08-21 moss_run；🗑️ 2026-08-24 trash_run；🎣 2026-08-28 fair_fishing(秋收钓鱼兜底)；⛏️ 2026-08-29 rock_run(室外镐击)

# 🚀 自动异步白名单（2026-08-16 恒拍板）：便利工具跑这些长脚本 → 自动后台异步，AI 不用手动 script_start。
# 长任务（钓鱼/挖矿/炸矿/收放机器/浇水可能很久）被动异步；短任务（清地/砍树/摸动物/捡采集等）保持同步。
_ASYNC_SCRIPTS = {"mine_run", "fish_run", "bomb_mine", "bomb_escort", "bomb_volcano",
                  "building_round", "fruit_round", "machine_loader", "water_crops"}


def async_config(show: bool = False, add: str = "", remove: str = "", enable: str = "") -> str:
    """🚀 异步配置（长脚本自动后台=被动异步，AI 不用手动 script start）。show 看白名单+开关 / add·remove 改白名单(name,不带.py) / enable on|off(同 settings async_tools)。细节→help(scripts)。

    """
    global _ASYNC_SCRIPTS
    if enable:
        v = enable.strip().lower()
        if v in ("on", "1", "true", "yes", "开"):
            _bg_cfg["auto_async"] = True
            _settings_save()
            return _with_state("🚀 长脚本自动异步已开启（白名单脚本便利工具自动后台跑）")
        if v in ("off", "0", "false", "no", "关"):
            _bg_cfg["auto_async"] = False
            _settings_save()
            return _with_state("🛑 长脚本自动异步已关闭（便利工具退回同步等结果）")
        return _with_state(f"❌ enable 要 on/off，收到「{enable}」")
    if add:
        name = add.strip().removesuffix(".py")
        if name in _ASYNC_SCRIPTS:
            return _with_state(f"「{name}」已在异步白名单里")
        _ASYNC_SCRIPTS.add(name)
        return _with_state(f"✅ 「{name}」已加入异步白名单（便利工具调它自动后台跑）\n当前白名单: {', '.join(sorted(_ASYNC_SCRIPTS))}")
    if remove:
        name = remove.strip().removesuffix(".py")
        if name not in _ASYNC_SCRIPTS:
            return _with_state(f"「{name}」不在异步白名单里")
        _ASYNC_SCRIPTS.discard(name)
        return _with_state(f"🗑️ 「{name}」已移出异步白名单（退回同步等结果）\n当前白名单: {', '.join(sorted(_ASYNC_SCRIPTS))}")
    # 默认 show
    lines = ["🚀 长脚本自动异步:"]
    lines.append(f"  总开关: {'开' if _bg_cfg.get('auto_async', True) else '关'}（enable=on/off 切换；异步总开关 async on/off）")
    lines.append(f"  白名单({len(_ASYNC_SCRIPTS)}): {', '.join(sorted(_ASYNC_SCRIPTS))}")
    lines.append("  白名单脚本便利工具（go_fishing/mine_run/bomb_mine 等）自动后台跑，AI 不用手动 script_start")
    lines.append("  add=脚本名 加入 / remove=脚本名 移出")
    return _with_state("\n".join(lines))


def _run_script(name: str, args_list: Optional[list] = None, timeout: int = 60,
                tail: int = 1000, async_ok: bool = False) -> str:
    """运行 scripts/ 下的 Python 脚本，返回输出摘要。tail=取输出末尾多少字（逐层摘要要大些）。
    ⚠️ async_ok=True（2026-08-16 恒拍板：长脚本自动异步）：
      脚本在白名单(_ASYNC_SCRIPTS) 且异步开启(_bg_cfg.enabled) → 转 _bg_start 后台跑，
      返回"🚀 已后台启动 jobX…"（调用方检测到 async 前缀就按异步处理返回）。
      短任务/检查调用不传 async_ok，保持同步。
      🚫 2026-08-17：原"计划执行中禁止新开脚本"检查已随计划模式退役移除。"""
    script_path = os.path.join(SCRIPT_DIR, f"{name}.py")
    if not os.path.exists(script_path):
        return f"❌ 脚本不存在: {name}.py"
    # 🚀 长脚本自动异步（2026-08-16 恒拍板：被动异步，AI 不用手动 script_start）
    if async_ok and name in _ASYNC_SCRIPTS and _bg_cfg.get("enabled", True):
        try:
            job, err = _bg_start(name, list(args_list) if args_list else [])
        except Exception as e:
            job, err = None, f"❌ 后台启动失败: {e}"
        if err:
            return err
        return (f"🚀 已后台启动「{name} {' '.join(args_list) if args_list else ''}」→ job {job.job_id}\n"
                f"  查进度: script(ops=\"status\", kw={{\"job_id\":\"{job.job_id}\"}})   停止: script(ops=\"stop\", kw={{\"job_id\":\"{job.job_id}\"}})")

    cmd = [sys.executable, script_path]
    if args_list:
        cmd.extend(args_list)
    # ⚠️ 2026-08-15 修复：脚本默认打 host 7842（挪恒角色），直接调 MCP 工具必须注入 --port <AI端口>
    #    计划调度器已有此逻辑（api.ai_port()），这里补到 _run_script 统一入口
    try:
        if name in _PORT_SCRIPTS and not any(a == "--port" or a.startswith("--port=") for a in cmd):
            cmd.extend(["--port", str(_ai_port())])
    except Exception:
        pass

    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            cwd=SCRIPT_DIR, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        out = (r.stdout or "")[-tail:]
        err = (r.stderr or "")[-500:]

        if r.returncode == 0:
            return out or "(无输出)"
        else:
            return f"返回码 {r.returncode}\n{err or out[:300] or ''}"
    except subprocess.TimeoutExpired:
        return f"⏰ 超时 ({timeout}秒)"
    except Exception as e:
        return f"❌ {e}"


# ⚠️ 2026-08-15 恒：这些地点即使无 HoeDirt 也是种植点（清地后也别 warp 回农场）
_PLANTING_LOCS = {"Greenhouse", "IslandWest", "IslandNorth", "IslandEast"}


def _warp_home_if_needed(loc_name: str) -> str:
    """如果不在指定地图则 warp 回去，返回日志字符串。

    ⚠️ 2026-08-13：已经在能种植的地点（温室/姜岛农场等）不强制挪走，
    免得 AI 在温室种菜/撒肥被拉回农场。
    ⚠️ 2026-08-15 修复：按名字识别种植点（清光 HoeDirt 后靠 HoeDirt 判断会误 warp 回农场）。
    """
    try:
        s = api.state()
        cur = s.get("location", {}).get("name", "")
        if cur == loc_name:
            return ""
        # 温室/姜岛等固定种植点 → 不挪（2026-08-15：按名字，清地后也有效）
        if cur in _PLANTING_LOCS:
            return ""
        # 或附近有 HoeDirt 的可种植点 → 不挪
        try:
            surr = api.surroundings(6)
            if any((t.get("terrain") or "") == "HoeDirt" for t in surr.get("tiles", [])):
                return ""
        except Exception:
            pass
        api.warp(loc_name)
        time.sleep(1)
        return f"🚀 warp到 {loc_name} → "
    except Exception:
        pass
    return ""


# ── 博物馆捐赠检测 ──

MUSEUM_ITEM_IDS = {
    # 矿物 (Mineral) — category -12 在 SDV 中代表矿物
    # 古物 (Artifact) — category -23 在 SDV 中代表古物
}
# 不用硬编码，直接用 category 判断


def _check_museum_donables(data: dict) -> str:
    """检查背包里有没有可以捐赠给博物馆的物品。
    返回空字符串=没有可捐物，否则列出可捐物品。
    优先用 /state 的数字 catNum（-12矿物 -23古物 -26/-2宝石），
    兼容字符串 category（getCategoryName）防止旧数据。
    """
    inv = data.get("inventory", [])
    donatables = []
    for item in inv:
        name = item.get("name", "")
        cat = item.get("catNum")
        if cat is None:
            cat = item.get("category", "")
        ok = cat in (-12, -23, -26, -2) or cat in ("Minerals", "Artifacts", "Gems")
        if ok:
            donatables.append((name, item.get("stack", 1)))

    if not donatables:
        return ""

    parts = [f"{n}×{c}" for n, c in donatables]
    return f"🏛️ 可捐赠: {', '.join(parts)}"


# ═══════════════════════════════════════════
#  感知工具
# ═══════════════════════════════════════════

@mcp.tool()
def check_status() -> str:
    """📊 查看游戏完整状态速报（强制完整版）
    位置/时间/季节/年份 · 生命/体力/金钱 · 背包/当前工具 ·
    菜单/事件/对话选项 · 系统警报 · 农场待办 · 运势 · 日历
    """
    return _with_state("📊 状态速报", force_full=True)


@mcp.tool()
def check_backpack() -> str:
    """🎒 查看背包全部物品（每格一行，含价值/星级/附加属性）
    比状态条里的格数详细得多——卖钱/送礼/种植/合成规划前调用。
    """
    qmarks = {0: "", 1: "[银]", 2: "[金]", 3: "[铱]"}
    try:
        s = api.state()
        inv = s.get("inventory", [])
        p = s.get("player", {})
        max_items = p.get("maxItems") or 36
        lines = [f"🎒 背包 {len(inv)}/{max_items}格"]
        if not inv:
            lines.append("  （空）")
        for i in inv:
            name = i.get("displayName") or i.get("name") or "?"
            q = qmarks.get(i.get("quality", 0), "")
            parts = [f"{q}{name}×{i.get('stack', 1)}"]
            val = i.get("value") or 0
            sellable = i.get("sellable", True)
            if not sellable:
                parts.append("🔒不可卖")   # 工具/武器/戒指/靴子（2026-09-04 恒：别让 AI 拿去卖）
            elif val:
                parts.append(f"{val}g")
            stats = i.get("stats") or ""
            if stats:
                parts.append(stats)
            lines.append("  · " + " ".join(parts))
        # 🏛️ 可捐赠注释（恒批注：只在背包详情里标，不随时报）
        don = _check_museum_donables({"inventory": inv})
        if don:
            lines.append(don)
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 查看背包失败: {e}")


@mcp.tool()
def look_around(radius: int = 10) -> str:
    """👀 观察周围环境
    扫描指定半径内的 NPC、怪物、物品、地形、作物。

    Args:
        radius: 扫描半径（格数，默认 10，最大 20）
    """
    radius = min(radius, 20)
    try:
        surr = api.surroundings(radius)
        tiles = surr.get("tiles", [])
        npcs = surr.get("npcs", [])
        monsters = surr.get("monsters", [])

        # 统计感兴趣的东西
        obj_tiles = [t for t in tiles if t.get("object")]
        crop_tiles = [t for t in tiles if t.get("crop")]
        terrain_trees = [t for t in tiles if t.get("terrain") and "Tree" in str(t.get("terrain", ""))]

        lines = [f"👀 半径 {radius} 格内："]

        if npcs:
            npc_list = [n.get("name", "?") for n in npcs]
            lines.append(f"  👤 NPC: {', '.join(npc_list)}")
        if monsters:
            mon_list = [m.get("name", "?") for m in monsters]
            lines.append(f"  👾 怪物: {', '.join(mon_list)}")
        if obj_tiles:
            summary = {}
            for t in obj_tiles:
                name = str(t.get("object", "?"))
                # 去掉坐标信息，只统计种类
                summary[name] = summary.get(name, 0) + 1
            parts = [f"{k}×{v}" for k, v in sorted(summary.items(), key=lambda x: -x[1])]
            lines.append(f"  📦 物品: {', '.join(parts[:8])}{'…' if len(parts) > 8 else ''}")
        if crop_tiles:
            lines.append(f"  🌱 作物: {len(crop_tiles)} 块")
        if terrain_trees:
            lines.append(f"  🌲 树木: {len(terrain_trees)} 棵")
        lines.append(f"  📊 共 {len(tiles)} 格")

        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 观察失败: {e}")


# ═══════════════════════════════════════════
#  导航工具
# ═══════════════════════════════════════════

def _apply_poi_stand_face(poi_name: str) -> str:
    """POI 到达后应用结构化站位+朝向（2026-08-16 恒，locations.POI_FACE）。
    返回"，朝X/站位"日志串；无配置或失败返回空串。交互仍交给 AI（interact/interact_at）。
    ⚠️ 农场设施不在 POI_FACE（动态检测），这里只处理固定可交互 POI。"""
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
            api.face(int(face))
            time.sleep(0.15)
            logs.append(f"朝{'上右下左'[int(face)]}")
        return "，" + "，".join(logs) if logs else ""
    except Exception:
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


@mcp.tool()
@_stuck_track
def walk_to(poi_name: str) -> str:
    """🚶 导航到指定地点（POI 落点）
    ⚠️ 2026-08-16 恒：**跨场景不瞬移**——POI 在别的图 → 自动走 map_go 真实路径（出口瓦片/门）；
    只有 POI 在当前图内才走过去。日常跨场景切换首选 map_go（walk_to 走出口瓦片不可靠）。
    ⚠️ 到 POI 后自动应用结构化站位+朝向（locations.POI_FACE，如水碗朝右、柜台朝上）——
    交互交给 AI（面前的目标用 interact / interact_at 触发）。农场设施走动态检测。

    常用地点：
    - 矿井入口 / 头骨矿洞 / 采石场
    - 秘密森林 / 巫师塔 / 玛妮牧场
    - 山湖 / 海边 / 河流各种钓鱼点
    - 皮埃尔商店 / 餐吧 / 铁匠铺 / 博物馆
    - 自己小屋(床) / 温室 / 农场洞穴
    - 巴士站 / 沙漠 / 姜岛船
    - 浴场 / 铁路 / 隧道

    Args:
        poi_name: POI 名称（见 locations.py 数据库）
    """
    _nr = _nav_resolve(poi_name)
    if _nr:
        _NAV_LAST.update(_nr)
    _NAV_FAILED["v"] = False
    try:
        # 🎇 节日限定 POI 门禁（2026-08-19 恒：非节日 map_go/walk_to 隐藏）
        if poi_name in locations.POI and not _festival_poi_active(poi_name, locations.POI[poi_name]):
            return _with_state(f"❌ {poi_name} 只在节日开放（现在去不了）")
        # ⚠️ 2026-09-03 恒：宠物碗浇水是"动作"不是"走位"——locations 明确 map walk 不扛浇水；
        #    AI 误用 walk 去宠物碗→BFS 找不到可直接站的落点→报 BFS failed/已到达但没动。直接引导走 farm 喂水。
        if any(k in poi_name for k in ("宠物碗", "水碗", "宠物水")):
            return _with_state(f"💡 「{poi_name}」的正确姿势是 `farm ops=喂水`（自动定位所有碗灌满），不用 walk——")
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
        # 同图 → go_to.py 走过去
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPT_DIR, "go_to.py"), poi_name],
            capture_output=True, text=True, timeout=45,
            cwd=SCRIPT_DIR,
        )
        out = (result.stdout or "")[-1000:]
        err = (result.stderr or "")[-500:]

        if result.returncode == 0:
            # 从输出里找关键信息
            lines = out.strip().split("\n")
            # 只保留最后几行非空信息
            summary = [l for l in lines if l.strip() and "log" not in l.lower()]
            short = "\n".join(summary[-5:]) if summary else "已到达"
            face_log = _apply_poi_stand_face(poi_name)
            return _with_state(f"🚶 已导航到「{poi_name}」{face_log}\n{short[:500]}")
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


def _mini_obelisk_pair() -> list:
    """动态扫农场找迷你图腾尖塔(Mini-Obelisk,238) 对——⚠️农场物品可挪动，每档位置不同，必须实时扫。
    返回 [(x,y), (x,y), ...]（按扫描顺序，通常2个成对）。"""
    try:
        found = []
        # 从多个点位半径扫覆盖全农场（约100x100）
        for cx, cy in [(40, 32), (78, 16), (40, 60), (20, 20), (60, 60), (80, 60), (20, 60), (60, 20)]:
            try:
                s = api._get("/surroundings", {"x": cx, "y": cy, "radius": 26})
                for t in (s.get("tiles") or []):
                    v = t.get("object")
                    if isinstance(v, str) and "Obelisk" in v:
                        pt = (t.get("x"), t.get("y"))
                        if pt not in found:
                            found.append(pt)
            except Exception:
                pass
        return found
    except Exception:
        return []


def _resolve_place(place: str):
    """把目的地解析成 (location, x, y)；解析不出返回 None（走 POI 兜底）。

    - "回家/自己小屋/我的小屋" → homeLocation 动态找自己的小屋（farmhand 各自的 Cabin）
    - 建筑名（畜棚/鸡舍/温室/鱼塘/出货箱…）→ /farm_buildings 实时定位门，抗建筑搬家
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

    # 2. 建筑关键字 → 实时定位门
    KEYWORDS = {
        "畜棚": "barn", "谷仓": "barn", "棚": "barn",
        "鸡舍": "coop", "舍": "coop",
        "温室": "greenhouse",
        "小屋": "cabin",
        "出货": "shipping",
        "筒仓": "silo", "粮仓": "silo",
        "鱼塘": "fish pond", "池塘": "fish pond",
        "马厩": "stable",
        "工棚": "shed", "仓库": "shed",
        "地窖": "cellar",
        "传送": "obelisk",
        "金钟": "gold clock",
    }
    for k, typekw in KEYWORDS.items():
        if k in p:
            for b in bs:
                if typekw in b.get("type", "").lower():
                    if "doorX" in b:
                        return ("Farm", b["doorX"], b["doorY"])
                    return ("Farm", b["x"], b["y"])
    return None


def _go_home() -> str:
    """回家：走到门口（Farm外立面）→ 互动进门 → 动态找床 → 走到床。

    出门不能自动化（walk_to 不肯踩上传送格），所以回家只做"进门"；
    出门用 /warp 传门外（见 go_to 兜底逻辑）。
    """
    try:
        p = api.state().get("player", {})
        home = p.get("homeLocation")
        door = p.get("homeDoor")
        if not door or not home:
            return _with_state("❌ 拿不到 homeDoor/homeLocation（需要新DLL）")

        # 1. 走到门口（Farm 外立面）
        r = api._post("/walk_to", {"location": door["location"], "x": door["x"], "y": door["y"]})
        if not r.get("ok"):
            return _with_state(f"❌ 去门口失败: {r.get('error', r)}")
        if not _wait_arrival(door["location"], door["x"], door["y"], timeout=35):
            return _with_state("⚠️ 走到门口超时")

        # 2. 若还在门外 → 下马 + 站门口正下方 + 面向门 + /interact 触发 checkAction
        if api.state().get("location", {}).get("name") != home:
            if api.state().get("player", {}).get("riding"):
                api._post("/key", {"key": "confirm"})  # 下马
                time.sleep(1.5)
            # 站门口正下方（门在正上方），面向门
            api._post("/position", {"x": door["x"], "y": door["y"] + 1})
            time.sleep(0.5)
            api._post("/face", {"direction": 0})  # 0=上，门在头顶
            time.sleep(0.3)
            api._post("/interact")
            for _ in range(10):
                time.sleep(0.8)
                if api.state().get("location", {}).get("name") == home:
                    break
        if api.state().get("location", {}).get("name") != home:
            return _with_state("⚠️ 进门失败，可能被挡/在菜单里")

        # 3. 动态找床并走过去
        bed = api._post("/crawl_bed", {"action": "locate"}).get("bed", {})
        if not bed:
            return _with_state("⚠️ 进门了但找不到床")
        r3 = api._post("/walk_to", {"location": bed["location"], "x": bed["x"], "y": bed["y"]})
        if not r3.get("ok"):
            return _with_state(f"❌ 到床失败: {r3.get('error', r3)}")
        if _wait_arrival(bed["location"], bed["x"], bed["y"], timeout=25):
            return _with_state(f"🏠 已到家床上 ({bed['location']} {bed['x']},{bed['y']})")
        return _with_state("⚠️ 到床超时")
    except Exception as e:
        return _with_state(f"❌ 回家失败: {e}")


def _wait_arrival(target_loc: str, target_x: int, target_y: int, timeout: int = 30) -> bool:
    """轮询等 walk_to 到达（含跨地图自动寻路）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = api.state()
            if s.get("location", {}).get("name") == target_loc:
                px, py = s.get("player", {}).get("x"), s.get("player", {}).get("y")
                if px is not None and py is not None:
                    if abs(px - target_x) <= 2 and abs(py - target_y) <= 2 and not s.get("player", {}).get("isMoving"):
                        return True
        except Exception:
            pass
        time.sleep(0.8)
    return False


@mcp.tool()
@_stuck_track
def go_to(place: str) -> str:
    """📍 自动导航到任意地点（多地图寻路：走→出口→传送→走→…目的地）

    建筑名实时查 /farm_buildings 定位，不依赖静态坐标（建筑搬家也不怕）；
    POI 名走 map_go 真实路径（2026-08-16 恒：不再 go_to.py 跨图瞬移）。
    ⚠️ 日常跨场景切换首选 map_go；go_to 用于建筑门口/回家/POI 落点。

    支持：
    - 回家 / 自己小屋 / 我的小屋 → 动态回自己的小屋
    - 建筑名：畜棚/鸡舍/温室/鱼塘/筒仓/出货箱/马厩/工棚/传送…
    - POI 名：皮埃尔商店/海滩/矿井入口/头骨矿洞/巴士站/沙漠…

    Args:
        place: 目的地名称（建筑或 POI）
    """
    try:
        # 回家/自己小屋 → 完整走门流程（走到门口→互动进门→走到床）
        if any(k in place for k in ("回家", "自己小屋", "我的小屋")):
            return _go_home()

        target = _resolve_place(place)
        if target is None:
            # 非建筑 → POI 兜底走 map_go（真实出口瓦片路径，不瞬移）
            return map_go(place)

        loc, x, y = target
        r = api._post("/walk_to", {"location": loc, "x": x, "y": y})
        if not r.get("ok"):
            return _with_state(f"❌ 寻路失败: {r.get('error', r)}")
        if _wait_arrival(loc, x, y, timeout=35):
            return _with_state(f"🚶 已到「{place}」({loc} {x},{y})")
        return _with_state(f"⚠️ 导航超时，目标「{place}」({loc} {x},{y})")
    except Exception as e:
        return _with_state(f"❌ {e}")


# ⚠️ read_mail 已退役（2026-08-15 恒：正常路径=找邮箱(/state.mailbox)+交互读信；📬 提醒已覆盖）
def read_mail() -> str:
    """📬 读邮箱邮件（已退役——AI 用 /state.mailbox 找邮箱 + interact 读信领附件；本函数仅留内部参考）。"""
    try:
        r = api._get("/mail")
        unread = r.get("unread") or []
        received = r.get("received") or []
        if not unread and not received:
            return _with_state("📬 邮箱是空的")
        lines = [f"📬 邮箱（未读 {len(unread)} 封）:"]
        for m in unread:
            lines.append(f"  ✉️ [{m.get('title')}]: {m.get('body','')[:60]}")
        if received:
            lines.append(f"  📂 已收 {len(received)} 封（新档最近）")
            for m in received[-3:]:
                lines.append(f"    · {m.get('title')}")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 读邮件失败: {e}")


def map_lookup(location: str) -> str:
    """🗺️ 查某个地点：能做什么 + 出口/门去哪（地图知识库）
    新到一个地方先调这个——"这能干嘛、从哪出去"。
    数据源：locations.MAP_FEATURES（交互功能）+ MAP_LINKS（出口/门）。

    Args:
        location: 地点名（Farm / Town / SeedShop / Mine…）
    """
    try:
        feat = locations.MAP_FEATURES.get(location)
        links = locations.MAP_LINKS.get(location)
        lines = [f"🗺️ {location}"]
        if feat:
            lines.append(f"  🛠️ 能做什么:")
            for f in feat:
                lines.append(f"    · {f}")
        # 🕐 商店营业时间（2026-08-15 恒：新档买种子/升级要知道几点开门；2026-08-23 恒：有小镇钥匙隐藏）
        _hl = _shop_hours_line(location)
        if _hl:
            lines.append(_hl)
        # 🐟 水域鱼种（2026-08-15 恒：map 显示钓鱼兴趣点）
        _fk = locations.FISH_KNOWLEDGE.get(location)
        if _fk:
            lines.append(f"  🐟 {_fk['水']}: {', '.join(f['name'] for f in _fk['fish'][:4])}"
                         + (f" 等{len(_fk['fish'])}种" if len(_fk['fish']) > 4 else ""))
        # 🗼 农场可移动物品动态检测（2026-08-15 恒：农场建筑/物品可挪，每档不同，不记静态）
        if location == "Farm":
            try:
                mobs = _mini_obelisk_pair()
                if len(mobs) >= 2:
                    lines.append(f"  🗼 迷你图腾柱对: {' ↔ '.join(f'({x},{y})' for x, y in mobs[:2])}（confirm 互传）")
            except Exception:
                pass
        # 🎒 背包升级兴趣点（2026-08-18：跟节日POI一样条件出现，满级隐藏）
        if location == "SeedShop":
            _bh = _backpack_upgrade_poi()
            if _bh:
                lines.append(f"  {_bh}")
        # 🎪 节日限定 POI/商店（2026-08-19 恒：节日当天才出现，非节日隐藏）
        _fest_info = False
        for _fn, _fd in _festival_pois_here(location):
            lines.append(f"  {_fd}")
            _fest_info = True
        # 🎪 今天节日横幅：该地点正好是节日地点（map_lookup 看到 "今天花舞节在这"）
        _fd2 = _festival_now_data()
        if _fd2.get("ok"):
            _key = (_fd2["season"], _fd2["day"])
            if calendar_data.FESTIVAL_LOCATIONS.get(_key) == location:
                _ff = calendar_data.get_festival_today(_fd2["season"], _fd2["day"])
                if _ff:
                    lines.append(f"  🎪 今天{_ff['name']}在这！用 festival go 进场地（内部图用 festival info/poi/help）")
                    _fest_info = True
        # 🔒 节日限定图未开放（非节日查这些图 → 提示别去，2026-08-19）
        if location in _FESTIVAL_ONLY_MAPS:
            _dd = _festival_now_data()
            _ok = _dd.get("ok") and (_dd["season"], _dd["day"]) in _FESTIVAL_ONLY_MAPS[location]
            if not _ok:
                _s_days = "、".join(f"{_FEST_SEASON_CN.get(s, s)}{d}日" for s, d in sorted(_FESTIVAL_ONLY_MAPS[location], key=lambda x: (x[1], x[0])))
                lines.append(f"  🔒 {location} 只在节日开放（{_s_days}）——现在去不了")
                _fest_info = True
        if links:
            lines.append(f"  🚪 出口/门:")
            locked = _locked_maps()  # 未解锁地点不显示（2026-08-14 #13）
            for l in links:
                if l["target"] in locked:
                    continue  # 🔒 未解锁出口不显示
                kind_icon = "🟢" if l["kind"] == "warp" else "🚪"
                tile = f"({l['tile'][0]},{l['tile'][1]})" if l.get("tile") else ("门" if l["kind"] == "door" else "边")
                lines.append(f"    {kind_icon} {tile} → {l['target']}（{l['kind']}）{l.get('note','')}")
        if not feat and not links and not _fest_info:
            return _with_state(f"🗺️ 知识库没有「{location}」——是建筑/矿洞/姜岛等，用 check_status 或实际走过去看")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


# map_query 同义词扩展（2026-08-13 恒：搜"挖矿"要能命中"矿井/下矿"，做模糊匹配）
# ⚠️ 别放单字泛词（"矿"会命中矿车/矿物、"买"命中所有店）——用具体词
MAP_QUERY_SYNONYMS = {
    "挖矿": {"矿井", "下矿", "采矿", "头骨"},
    "下矿": {"矿井", "挖矿", "采矿", "头骨"},
    "采矿": {"矿井", "下矿", "挖矿"},
    "钓鱼": {"钓", "鱼竿"},
    "买种子": {"种子"},
    "升级工具": {"升级", "铁匠"},
    "买动物": {"玛妮", "动物"},
    "捐献": {"博物馆", "古物", "矿物"},
    "看病": {"医院", "哈维", "药"},
    "吃饭": {"餐吧", "沙拉", "格斯"},
    "睡觉": {"床"},
    "泡澡": {"温泉", "浴场"},
    "沙漠": {"巴士", "车票"},
    "强化": {"锻造", "附魔", "戒指"},
    "升级背包": {"背包", "Backpack"},
}


def _map_query_keywords(q: str):
    """把用户查询扩展成关键词集合（含同义词），做模糊匹配。"""
    kws = {q}
    for k, v in MAP_QUERY_SYNONYMS.items():
        if k in q:
            kws |= v
    return kws


# 🗺️ 场景名中文别名（2026-08-30 恒：map_go 的 MAP_LINKS 键是英文，AI 想的是中文场景名——
#    "go 铁路" 报"知识库没有"。加这张表：destination 先查中文别名→认成 MAP_LINKS 键。
#    ⚠️ 选近口已由 _map_bfs 天然正确(自动挑最少段数入口)，这里只补"名字认不出"。
#    键=中文场景名(可含多个 alias)，值=MAP_LINKS 键；只收录"AI 会当作场景整体去"的地点名空间。）
SCENE_NAME_ALIAS = {
    # 主城区/农场
    "农场": "Farm", "农庄": "Farm",
    "巴士站": "BusStop", "车站": "BusStop",
    "深山": "Backwoods", "林间小径": "Backwoods", "边远森林": "Backwoods",
    "镇": "Town", "小镇": "Town", "鹈鹕镇": "Town",
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
    "探险家公会": "AdventureGuild", "怪物公会": "AdventureGuild",
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
    # 魔法/女巫区
    "法师塔": "WizardHouse", "巫师塔": "WizardHouse", "法师家": "WizardHouse",
    "法师地下室": "WizardHouseBasement", "幻觉神龛地下室": "WizardHouseBasement",
    "女巫沼泽": "WitchSwamp", "沼泽": "WitchSwamp",
    "女巫小屋": "WitchHut", "巫师小屋": "WitchHut",
    "魔女沼泽洞穴": "WitchWarpCave", "黑暗护身符洞穴": "WitchWarpCave",
    "温泉": "BathHouse_Entry", "浴场": "BathHouse_Entry",
    # 姜岛
    "姜岛": "IslandSouth", "岛": "IslandSouth",
    "姜岛农场": "IslandWest",
    "火山": "IslandNorth", "火山入口": "VolcanoEntrance",
}


def _resolve_scene_name(name):
    """把中文/别名目的地认成 MAP_LINKS 场景键（模糊匹配）。
    精确命中→返回场景键；找不到→返回原值(交给既有逻辑走 POI/建筑兜底)。
    选近口不在这做——_map_bfs 会挑最少段数入口。"""
    if not name:
        return name
    s = str(name).strip()
    # 1. 本来就是 MAP_LINKS 键(英文) → 直接用
    if s in locations.MAP_LINKS:
        return s
    # 2. 精确命中别名
    if s in SCENE_NAME_ALIAS:
        return SCENE_NAME_ALIAS[s]
    # 3. 子串模糊：dest 含某别名 或 某别名含 dest(如 "去铁路"/"铁路(站台)")
    #    ——优先更长匹配，别被单字"山/镇/岛"误伤(用 is 子串的双向 + 长度降序)
    best = None
    for alias, key in SCENE_NAME_ALIAS.items():
        if len(alias) < 2:
            continue
        if alias in s or s in alias:
            if best is None or len(alias) > len(best[0]):
                best = (alias, key)
    if best:
        return best[1]
    return s


@mcp.tool()
def map_query(function: str) -> str:
    """🗺️ 按功能/目的反查地点（"想买种子去哪" → 皮埃尔商店）
    扫 MAP_FEATURES 找含关键词的地点，反着查。带同义词模糊匹配（搜"挖矿"也能命中"矿井/下矿"）。

    Args:
        function: 功能关键词（买种子 / 买鱼竿 / 升级工具 / 下矿 / 挖矿 / 钓鱼 / 捐献…）
    """
    try:
        kws = _map_query_keywords(function)
        locked = _locked_maps()  # 未解锁地点不显示（2026-08-14 #13）
        hits = []
        for loc_name, feats in locations.MAP_FEATURES.items():
            if loc_name in locked:
                continue  # 🔒 未解锁地点排除
            for f in feats:
                if any(k.lower() in f.lower() for k in kws):
                    # 排序：含原查询词(2分) > 含同义词(1分)
                    score = 2 if function.lower() in f.lower() else 1
                    hits.append((score, loc_name, f))
                    break
        # 🎒 背包升级兴趣点（2026-08-18：跟节日POI一样条件出现，满级不显示）——搜"背包/升级背包"且背包<36格才命中
        if any(k in ("背包", "backpack", "Backpack") for k in kws) or "升级背包" in function:
            _bh = _backpack_upgrade_poi()
            if _bh:
                hits.append((2, "SeedShop", _bh))
        # 🎪 节日限定 POI/商店（2026-08-19 恒：节日当天才命中，非节日隐藏）
        for _fn, _fl, _fd in _festival_pois_all():
            if any(k.lower() in _fn.lower() or k.lower() in _fl.lower() or k.lower() in _fd.lower() for k in kws):
                hits.append((2, f"{_fn}（{_fl}）", _fd))
        hits.sort(key=lambda x: -x[0])   # 精确匹配优先
        if not hits:
            # 兜底：按 POI name/note 搜（记分 1，和 MAP_FEATURES 命中格式一致）
            for pname, p in locations.POI.items():
                if (p.get("map") or "") in locked:
                    continue  # 🔒 未解锁地点排除
                if not _festival_poi_active(pname, p):
                    continue  # 🎇 节日限定 POI 非节日隐藏（2026-08-19）
                if any(k.lower() in pname.lower() or k.lower() in p.get("note", "").lower() for k in kws):
                    hits.append((1, pname, p.get("note", p.get("map", ""))))
        if not hits:
            # 🌰 姜岛金核桃升级表（2026-08-15：搜"金核桃/图腾/鹦鹉/传送塔"命中）
            for u in locations.ISLAND_UPGRADES:
                hay = u["name"] + u["desc"] + u["where"] + str(u["cost"])
                if any(k.lower() in hay.lower() for k in kws):
                    hits.append((1, f"🌰 {u['name']}", f"{u['desc']}｜{u['where']}｜{u['cost']}核桃"))
        if not hits:
            return _with_state(f"🗺️ 没找到「{function}」相关地点——换个说法（买种子/钓鱼/挖矿/商店…）")
        lines = [f"🗺️ 「{function}」相关（含同义词）:"]
        for _score, loc_name, f in hits[:10]:
            lines.append(f"  · {loc_name}: {f}")
        if len(hits) > 10:
            lines.append(f"  …（共 {len(hits)} 处）")
        lines.append("💡 去之前用 map go(地点) 导航（跨场景唯一入口）")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


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


def _enter_building_door(loc: str) -> bool:
    """map_go 进门：走到建筑门口 → confirm 进门。
    门口坐标：BUILDING_DOORS（固定建筑）优先，_resolve_place（农场建筑动态）兜底。
    返回是否成功进入目标地点。"""
    try:
        cur = api.state().get("location", {}).get("name", "")
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
            return False
        out_map, (dx, dy) = door
        if out_map != cur:
            # 先到门口所在的地图（一般就在当前图；不在就走 MAP_LINKS 到门口那张图）
            return False
        # /walk_to 到门口瓦片（用户实测 2026-08-13：Saloon 门在 Town(45,71)，不是 dy+1）→ 面向门 → /interact 开门
        r = api._post("/walk_to", {"location": out_map, "x": dx, "y": dy})
        if not r.get("ok"):
            return False
        if not _wait_arrival(out_map, dx, dy, timeout=25):
            return False
        # 若走位已触发进门（走到门瓦片上可能直接传），提前返回
        if api.state().get("location", {}).get("name", "") == loc:
            return True
        # 面朝上（0）+ /interact 开门（实测 key confirm 开不了，/interact 才行）
        api._post("/face", {"direction": 0})
        time.sleep(0.3)
        api._post("/interact")
        time.sleep(1.5)
        return api.state().get("location", {}).get("name", "") == loc
    except Exception:
        return False


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
        r = api._post("/walk_to", {"location": frm, "x": sx, "y": sy})
        if r.get("ok"):
            _wait_arrival(frm, sx, sy, timeout=15)
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


# ℹ️ INTERIOR_EXIT_APPROACH 固定表已于 2026-08-30 删除——_exit_farm_building 改读 /map 原生 warp 瓦片，
#    任何室内建筑/任意档位通用，不再按尺寸查表。


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
                api._post("/walk_to", {"location": frm, "x": approach[0], "y": approach[1]})
                _wait_arrival(frm, approach[0], approach[1], timeout=20)
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
    r = api._post("/walk_to", {"location": frm, "x": bx, "y": by})
    if not r.get("ok"):
        return False
    _wait_arrival(frm, bx, by, timeout=walk_timeout)
    # 2. 人到位置了 → /warp 下一图入口
    # ⚠️ 2026-08-23 恒：赌场这类「建筑室内」（Club/SandyHouse 内室）普通 /warp 进不去
    #    （Game1.warpFarmer 对建筑内部切不动）→ 回退 /warp_into（同步直切 currentLocation）。
    if wx is not None and wx >= 0 and wy is not None and wy >= 0:
        api.warp(nxt, wx, wy)
    else:
        api.warp(nxt)
    for _ in range(3):
        time.sleep(0.6)
        if api.state().get("location", {}).get("name", "") == nxt:
            return True
    # 兜底：普通 warp 失败（建筑室内）→ /warp_into 同步直切
    try:
        api.warp_into(nxt, wx if wx >= 0 else None, wy if wy >= 0 else None)
        time.sleep(0.3)
        if api.state().get("location", {}).get("name", "") == nxt:
            return True
    except Exception:
        pass
    return False


def _map_bfs(from_loc: str, to_loc: str):
    """在 MAP_LINKS 图上 BFS 找最短路径。返回 [(起点, 目标, link), ...] 或 None。
    ⚠️ 2026-08-30 恒：from==to 时直接返回 []（空路径=原地不动）——不然 BFS 会找
    Farm→BusStop→Farm 这种自环，把 AI 绕地图跑一圈（"出门第一步就乱走"根因）。"""
    if from_loc == to_loc:
        return []
    graph = {}
    for src, links in locations.MAP_LINKS.items():
        for l in links:
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


# 💼 精通领取门禁（2026-08-23 恒：5 颗精通星在钱包特殊物品，非 mailReceived）
# 读 AI 进程(7843) /special_items 的 has.mastery_<skill>（farmer.specialItems 预解析）。
# ⚠️ 与 _wallet_flag_present 是两个钱包源——精通/小镇钥匙在 specialItems，钥匙 flag 在 mailReceived。
# TTL 30s 缓存；读不到/旧 DLL → False（不误伤，藏而不拦）。
# ⚠️ 2026-08-23 实测：specialItems 不含 mastery_*（此档=['499','464']），精通奖励配方才是可靠判据。
# 精通奖励配方在 /craft_recipes（=已学配方，ModEntry HandleCraftRecipes 只报已学）里出现=已领取。
_MASTERY_KEYS = {
    "farming": "Statue Of Blessings",      # 耕种精通→祝福雕像配方
    "mining": "Statue Of The Dwarf King",  # 采矿精通→矮人国王配方
    "combat": "Anvil",                      # 战斗精通→铁砧配方（饰品槽/铁砧）
    "foraging": "Mystic Tree Seed",         # 觅食精通→神秘树种配方（或 Treasure Totem）
}
_MASTERY_CACHE = {"ts": 0.0, "learned": None}


def _mastery_claimed(skill: str) -> bool:
    """是否已领取某精通（如 combat=战斗精通→解锁饰品槽/铁砧）。
    skill ∈ farming/mining/combat/foraging；fishing(高级铱鱼竿=物品非配方) 无条件 False。
    判据=/craft_recipes 已学配方含该精通奖励配方名。"""
    global _MASTERY_CACHE
    recipe = _MASTERY_KEYS.get((skill or "").lower())
    if recipe is None:
        return False
    if _MASTERY_CACHE["learned"] is not None and time.time() - _MASTERY_CACHE["ts"] < 30:
        return recipe in _MASTERY_CACHE["learned"]
    learned = set()
    try:
        u = api._get("/craft_recipes")
        learned = {x.get("name") for x in (u.get("recipes") or [])}
    except Exception:
        learned = set()  # 读不到 → 不误伤
    _MASTERY_CACHE = {"ts": time.time(), "learned": learned}
    return recipe in learned


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


# 🏘️ 小镇钥匙隐藏营业时间注入（2026-08-23 恒：有钥匙能随时进镇店，营业时间没意义）。
# 范围（恒拍板）仅这 9 间——镇上 6 间 + 博物馆/鱼店/公会。居民房(镇长家等)本就不注入，
# SandyHouse(沙漠)/Mine 不在镇上保留。判据=wallet 特殊物品含 TownKey（specialItems，非 mailReceived flag）。
_TOWN_KEY_NO_HOURS = {
    "SeedShop",   # 皮埃尔
    "Hospital",   # 哈维
    "Saloon",     # 格斯
    "Blacksmith", # 铁匠克林特
    "AnimalShop", # 玛妮
    "ScienceHouse", # 罗宾木匠
    "ArchaeologyHouse", # 博物馆
    "FishShop",   # 威利鱼店
    "AdventureGuild",   # 公会
}


def _shop_hours_line(loc: str) -> str:
    """🕐 商店营业行（营业时间注入）。有小镇钥匙且是镇店 → 隐藏（返回 ""），
    否则返回 SHOP_HOURS 的 "🕐 营业" 行。读不到(旧DLL/异常) → 按 SHOP_HOURS 正常显示。"""
    if loc in _TOWN_KEY_NO_HOURS:
        try:
            if _wallet_flag_present("HasTownKey"):
                return ""  # 有小镇钥匙 → 隐藏
        except Exception:
            pass  # 读不到 → 按 SHOP_HOURS 显示，不误伤
    _hours = locations.SHOP_HOURS.get(loc)
    return f"  🕐 营业: {_hours}" if _hours else ""


# 📋 已知齐先生挑战 questKey（2026-08-22 实测 2 个；其余注入显示"齐先生"即可，详情靠知识库/quest）
_QI_CHALLENGE_KEY = {
    "QiChallenge9": "深处的危险",
    "QiChallenge2": "齐先生的作物",
}


def _order_display(quest_key: str, req_en: str) -> str:
    """把一次可接特别订单显示成中文（委托人·任务名）。qi 挑战靠 questKey 认；社区靠 req_en 匹配知识库。"""
    try:
        if req_en == "Qi":
            return f"齐先生·{_QI_CHALLENGE_KEY.get(quest_key, '?挑战')}"
        # 社区订单：req_en → 知识库
        by_req = [(name, o) for name, o in calendar_data.SPECIAL_ORDERS.items() if o.get("board") == "town" and o.get("req_en") == req_en]
        if len(by_req) == 1:
            name, o = by_req[0]
            return f"{o.get('requester')}·{name}"
        if by_req:
            return by_req[0][1].get("requester", req_en)  # 多任务委托人(Willy/罗宾/德米/法师) → 只显示名字
        return req_en or "?"
    except Exception:
        return req_en or "?"


def _special_orders_inject(season: str, day, year, morning: bool) -> str:
    """📋 每周一注入一条**轻提醒**"特别任务可接取，去看展板"（2026-08-22 恒：不给清单，AI 自己去展板看）。
    条件：周一 + 两块板都解锁（镇板=年1秋2后；齐板=姜岛解锁 IslandSouth 未锁）。具体可接单让 AI 去展板 menu read，或用 menu journal/menu read 看已接。"""
    try:
        if not morning or not isinstance(day, int):
            return ""
        if (day - 1) % 7 != 0:
            return ""  # 只周一
        # 镇板：年1秋2后
        if _date_ordinal({"year": year, "season": season, "day": day}) < _date_ordinal({"year": 1, "season": "fall", "day": 2}):
            return ""
        # 齐先生板：姜岛解锁（核桃房可进）
        try:
            if "IslandSouth" in _locked_maps():
                return ""  # 姜岛未解锁 → 齐先生板未开
        except Exception:
            return ""
        r = api._get("/quest_list")
        avail = [q for q in (r.get("quests") or []) if q.get("source") == "availableSpecialOrders"]
        if not avail:
            return ""
        # ⚠️ 只提醒，不枚举具体哪单——AI 自己 map go 镇板/核桃房去看展板，或 menu journal/menu read 看已接
        return "📋 特别任务板本周有可接任务：去鹈鹕镇展板或齐先生核桃房 menu read 看（接单走 menu click accept；已接用 menu journal/menu read 查）"
    except Exception:
        return ""


def _helpwanted_inject(loc_name: str) -> str:
    """📋 每天第一次进 Town：皮埃尔店西侧"需要帮助"求助栏（Billboard）有新求助就报，没有则不报（一天一次）。
    求助内容=questOfTheDay（自动在任务簿里，Billboard 无 accept 按钮）；看=menu read 展板，
    做=收集够任务物品→带到对应 NPC 交付领钱（交付能否复用 gift 待真机验证，2026-08-29 恒）。"""
    try:
        if loc_name != "Town":
            return ""
        dk = api.day_key()
        if not dk or _HW_REMIND_KEY["last"] == dk:
            return ""
        r = api.quest_progress()
        q = next((x for x in (r.get("quests") or [])
                  if x.get("source") == "questOfTheDay" and not x.get("completed")), None)
        if not q:
            return ""
        _HW_REMIND_KEY["last"] = dk
        title = q.get("title", "?")
        desc = (q.get("description") or "").replace("\n", " ")[:80]
        npc = re.search(r"我是([一-龥A-Za-z·]+)", q.get("description") or "")
        npcname = npc.group(1) if npc else "对应NPC"
        return (f"📋 皮埃尔店求助栏有今日求助「{title}」！{desc}…看：menu read 展板；"
                f"做：收集够任务物品带去找{npcname}，用 scene interact 交互交付（2026-08-29 恒实测"
                f"交付=对NPC checkAction，未设ActiveObject也成）")
    except Exception:
        return ""


def _helpwanted_ready_inject() -> str:
    """🧺 Part A 集齐提醒（2026-08-29 恒）：每日求助已集齐(collected>=required)但未交付 → 提示去交付。
    questOfTheDay 的 collected/required 由 DLL 返回（ResourceCollectionQuest numberCollected/numberToCollect）。"""
    try:
        r = api.quest_progress()
        q = next((x for x in (r.get("quests") or [])
                  if x.get("source") == "questOfTheDay" and not x.get("completed")), None)
        if not q:
            return ""
        try:
            ci = int(str(q.get("collected") or "0") or "0")
        except Exception:
            ci = -1
        try:
            ri = int(str(q.get("required") or "0") or "0")
        except Exception:
            ri = 0
        if ci < 0 or ri <= 0 or ci < ri:
            return ""
        if _HW_READY_KEY["last"] == api.day_key():
            return ""
        _HW_READY_KEY["last"] = api.day_key()
        return (f"🧺 今日求助「{q.get('title')}」已集齐 {ci}/{ri}，带去找对应NPC用 scene interact 交互交付"
                f"（交付完成奖励在任务日志 rewardBox 领）")
    except Exception:
        return ""


def _special_reward_inject(loc_name: str = "") -> str:
    """🎯 特别订单奖励链提醒（2026-08-29 恒：接单/完成领奖/兑奖券邮箱可拿——到 Town 每日各一次，去重）。
    奖励链：板上 accept 接单 → 交付完成 → 日志 rewardBox 领钱 + 板旁领奖箱(60,93)拿兑奖券 → 刘易斯家兑奖机(mainButton)兑换。
    用 quest_progress 的 availableSpecialOrders / specialOrders(state) + /state.voucherPending 判定。"""
    try:
        if loc_name != "Town":
            return ""
        dk = api.day_key()
        if not dk or _SPECIAL_RW_KEY["last"] == dk:
            return ""
        r = api.quest_progress()
        quests = r.get("quests") or []
        avail = [q for q in quests if q.get("source") == "availableSpecialOrders"]
        done = [q for q in quests if q.get("source") == "specialOrders" and (q.get("state") or "") == "Complete"]
        notes = []
        if avail:
            notes.append("📢 特别任务板有可接订单：去社区板/齐先生板 menu read → click(button=acceptLeftQuestButton/acceptRightQuestButton) 接")
        if done:
            notes.append(f"📢 {len(done)} 单特别订单已完成待领：①日志 rewardBox 领钱 ②板旁领奖箱(60,93)拿兑奖券 ③刘易斯家兑奖机 mainButton 兑换")
        try:
            vp = int((api.state().get("player") or {}).get("voucherPending") or 0)
            if vp > 0:
                notes.append("🎟️ 兑奖券邮箱有未领券（社区板旁领奖箱 60,93 交互拿；无券=静默）")
        except Exception:
            pass
        if not notes:
            return ""
        _SPECIAL_RW_KEY["last"] = dk
        return "\n".join(notes)
    except Exception:
        return ""


def _re_requester(dump: str) -> str:
    m = re.search(r"requester=([^|]+)", dump or "")
    return m.group(1).strip() if m else ""


def _re_questkey(dump: str) -> str:
    m = re.search(r"questKey=([^|]+)", dump or "")
    return m.group(1).strip() if m else ""


def _quest_know_hint() -> str:
    """📖 有已接/可接特别订单时 → 提示用 menu know <名> 查详情（知识库 enum 引导，2026-08-22 恒）。
    挂在 menu read(QuestLog) / 任务相关输出末尾——AI 看"已接任务面板"时知道去哪查详细。"""
    try:
        r = api._get("/quest_list")
        has = any(q.get("source") in ("specialOrders", "availableSpecialOrders") for q in (r.get("quests") or []))
        if not has:
            return ""
        return "📖 查某单详细用 menu know <任务名>（如 menu know 岛屿食材/历史的碎片；知识库 SPECIAL_ORDERS）"
    except Exception:
        return ""


def _map_go_unlock_check(dest: str) -> str:
    """未解锁地点 → 返回拦截串；解锁/不在表/读不到 → 空串放行。"""
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
OBELISK_TARGETS = {
    "Earth Obelisk":  {"dest": "Mountain",    "label": "山岭图腾柱(→山)"},
    "Water Obelisk":  {"dest": "Beach",       "label": "海滩图腾柱(→海滩)"},
    "Desert Obelisk": {"dest": "Desert",      "label": "沙漠图腾柱(→沙漠)"},
    "Island Obelisk": {"dest": "IslandSouth", "label": "姜岛图腾柱(→岛)"},
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
    "Earth Obelisk":  {"Mine", "AdventureGuild", "ScienceHouse", "Tent"},   # 落点 Mountain
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
        r = api._post("/walk_to", {"location": loc, "x": sx, "y": sy})
        if r.get("ok"):
            _wait_arrival(loc, sx, sy, timeout=15)
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


def _obelisk_go(building, landing: str, label: str) -> tuple:
    """站到图腾柱下方 → 朝上 → key confirm 传送（⚠️ 必须 confirm，interact/右键不触发）。
    返回 (是否离开农场/到达落点, 日志)。"""
    try:
        bx, by = int(building["x"]), int(building["y"])
        w = int(building.get("width") or 3)
        h = int(building.get("height") or 2)
        sx, sy = bx + w // 2, by + h // 2 + 1    # 柱底中部（站柱下 1 格朝上）
        _dismount_if_riding()
        _snap_stand("Farm", sx, sy)
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
                    if cur and cur != "Farm":
                        break
                except Exception:
                    pass
            try:
                if api.state().get("location", {}).get("name", "") != "Farm":
                    break
            except Exception:
                pass
        cur = api.state().get("location", {}).get("name", "") or landing
        ok = cur != "Farm"
        return ok, f"🗼 {label} → {cur}"
    except Exception as e:
        return False, f"🗼 {label} 失败({e})"


def _minecart_plan(dest: str, cur: str):
    """玩家在矿车站图 + dest 在矿车网络 → 返回 (站名, 站数据, 目的站名)；否则 None。"""
    ds = MINE_CART_TO.get(dest)
    if not ds:
        return None
    for sname, stn in locations.MINE_CART_STATIONS.items():
        if stn.get("map") != cur:
            continue
        for opt in (stn.get("menu") or {}).values():
            if opt == ds:
                return (sname, stn, ds)
    return None


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


def _interior_to_farm(cur: str) -> bool:
    """当前地图是否是「农场建筑室内」（小屋/农舍/温室/洞穴…），需先走出到 Farm。
    map_go 第一步先走出室内进 Farm，好让图腾柱/矿车在 Farm 触发（2026-08-23 恒：从 Cabin 出发去赌场也要走柱子，别坐公交）。
    ⚠️ 2026-08-30 恒：**只用「门式连接」(target==Farm 且 tile is None) 判定**——室内建筑(FarmHouse/Cabin/
    Greenhouse/FarmCave) 走门连回 Farm，均 tile=None；而 Backwoods/Forest/BusStop 等紧邻农场的**室外图**
    虽也连 Farm，但是**世界 warp 瓦片**(tile=(x,y))，不是室内，不许走这个"出屋"分支。
    (旧版只判"有没有连 Farm"，把室外邻图也误判成室内 → 从深山回农场报"离开小屋"误导。)"""
    if cur == "Farm":
        return False
    for l in locations.MAP_LINKS.get(cur, []):
        if l["target"] == "Farm" and l.get("tile") is None:
            return True
    return False


def _try_transport(dest: str, cur: str):
    """图腾柱 > 矿车：优先用交通节点（玩家在对应位置才有）。
    成功 → (新当前地点名, 日志)；无可用/失败 → (None, "")."""
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
        # 图腾柱失败 → 试矿车（不报错，静默落回走路）
    mc = _minecart_plan(dest, cur)
    if mc:
        sname, stn, ds = mc
        ok, log = _minecart_go(sname, stn, ds, dest)
        if ok:
            return dest, log
    return None, ""


def _map_go_walk(path, destination: str, dest: str, lead_log: str = "") -> str:
    """执行 BFS 路径逐段走路（map_go 与交通续走共用；2026-08-16 抽取）。
    lead_log: 交通节点成功日志（前缀显示）。"""
    log = [f"🗺️ 导航 {path[0][0]} → {dest}（{len(path)} 段）"]
    if lead_log:
        log.insert(0, lead_log)
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
            # 恒 2026-08-13 可靠版：走到出口可站位 → 确认人到 → /warp 下一图入口
            arrived = _walk_trigger_warp(frm, nxt, ex, ey, wx, wy, exact=use_exact)
            if not arrived:
                _NAV_FAILED["v"] = True
                return _with_state("\n".join(log) + f"\n⚠️ 到 {nxt} 失败")
        elif kind == "door":
            ok = _enter_building_door(nxt)
            if not ok:
                # 兜底：直接传送到建筑入口 ARRIVE
                ar = locations.ARRIVE.get(nxt)
                if ar:
                    api.warp(nxt, ar[0], ar[1])
                    time.sleep(1.5)
                    ok = api.state().get("location", {}).get("name", "") == nxt
            if not ok:
                _NAV_FAILED["v"] = True
                return _with_state("\n".join(log) + f"\n⚠️ 进 {nxt} 失败")
        elif kind == "portal":
            # 🔮 传送阵/模拟出口 warp（2026-08-30 恒：女巫/法师区魔法传送，非原生 warp 瓦片）。
            #    ⚠️ 恒拍板：传送阵要**精确站位**（像门 BUILDING_DOORS）——先 walk_to 到传送阵站格，
            #       再 api.warp 跳过去。否则从远处瞬移、收尾 BFS 乱传。落地格优先 link['arrive'] > ARRIVE。
            stand = link.get("stand")
            if stand:
                api._post("/walk_to", {"location": frm, "x": stand[0], "y": stand[1]})
                _wait_arrival(frm, stand[0], stand[1], timeout=20)
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
    # 到目标地点后，如果是 POI，再走到 POI 精确位置
    final_txt = f"\n✅ 到达 {dest}"
    if destination in locations.POI:
        poi = locations.POI[destination]
        if poi.get("map") == dest:
            api._post("/walk_to", {"location": dest, "x": poi["pos"][0], "y": poi["pos"][1]})
            _wait_arrival(dest, poi["pos"][0], poi["pos"][1], timeout=20)
            # 2026-08-16 恒：POI 结构化站位+朝向（宠物水碗朝右/柜台朝上；幂等，walk_to 双调无害）
            face_log = _apply_poi_stand_face(destination)
            final_txt = f"\n✅ 到达 {destination}（{poi['pos']}）{face_log}"
    return _with_state("\n".join(log) + final_txt)


@mcp.tool()
@_stuck_track
def map_go(destination: str) -> str:
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
    """
    _nr = _nav_resolve(destination)
    if _nr:
        _NAV_LAST.update(_nr)
    _NAV_FAILED["v"] = False
    try:
        # 0. 目标解析（POI → 地点名；中文场景名→MAP_LINKS 键）
        dest = destination
        if destination in locations.POI:
            dest = locations.POI[destination]["map"]
        else:
            dest = _resolve_scene_name(destination)
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
                api._post("/walk_to", {"location": loc, "x": x, "y": y})
                _wait_arrival(loc, x, y, timeout=35)
                return _with_state(f"🗺️ 已到「{destination}」门口 ({loc} {x},{y})（建筑门，进屋用 interact）")
            return _with_state(f"🗺️ 知识库没有「{dest}」的地点链接（试试 SeedShop/Town/Mine…）")
        # ⚠️ 未解锁地点拦截（2026-08-14 #13）
        _lock = _map_go_unlock_check(dest)
        if _lock:
            return _with_state(_lock)
        # ⚠️ 火山门禁（2026-08-16 恒）：host 不在矿井/火山 → 禁入火山（特殊瓦片无法换层）
        if dest.startswith("Volcano") or dest == "Caldera":
            _vg = _volcano_gate()
            if _vg:
                return _with_state(_vg)
        # 1. 当前地点
        cur = api.state().get("location", {}).get("name", "")
        if cur == dest:
            # 已在目标地点：若指定了 POI 且 POI 就在本图，仍走到 POI 精确位置
            if destination in locations.POI and locations.POI[destination].get("map") == dest:
                poi = locations.POI[destination]
                api._post("/walk_to", {"location": dest, "x": poi["pos"][0], "y": poi["pos"][1]})
                _wait_arrival(dest, poi["pos"][0], poi["pos"][1], timeout=20)
                # ⚠️ 2026-08-23 恒：已在目标图(如已在 Club)时也要 _apply_poi_stand_face——
                #    walk_to 有 ±2 容差可能停偏1格、且不设 face，interact 会打到错误瓦片。
                #    与另两条 POI 终止路径(transport/BFS)一致：position 瞬移到 stand + 设朝向。
                face_log = _apply_poi_stand_face(destination)
                return _with_state(f"🗺️ 已在 {dest}，走到 {destination}（{poi['pos']}）{face_log}")
            return _with_state(f"🗺️ 已经在 {cur} 了")
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
            if xlog and dest == "Farm":
                return _with_state(f"🏡 已离开室内回到农场（{cur} {api.state().get('player',{}).get('x')},{api.state().get('player',{}).get('y')}）")
            if not xlog:
                return _with_state("⚠️ 走出室内到农场失败（可能被挡/在菜单里）")
        # 2.5 ⚠️ 2026-08-16 恒：图腾柱 > 矿车 > 走路（动态交通节点，玩家在对应位置才有）
        land, tlog = _try_transport(dest, cur)
        if land:
            if land == dest:
                # 直达 → 若 destination 是 POI 在本图，走到 POI 精确位
                if destination in locations.POI and locations.POI[destination].get("map") == dest:
                    poi = locations.POI[destination]
                    api._post("/walk_to", {"location": dest, "x": poi["pos"][0], "y": poi["pos"][1]})
                    _wait_arrival(dest, poi["pos"][0], poi["pos"][1], timeout=20)
                    face_log = _apply_poi_stand_face(destination)
                    return _with_state(f"{tlog} → 到达 {destination}（{poi['pos']}）{face_log}")
                return _with_state(f"{tlog} → 到达 {dest}")
            # 落点≠dest（岛柱落岛南等）：从落点续走 BFS
            cur = land
            path = _map_bfs(cur, dest)
            if not path:
                return _with_state(f"{tlog}，但从 {cur} 到 {dest} 缺地图链接（先手动到 {cur} 再走）")
            return _map_go_walk(path, destination, dest, lead_log=tlog)
        # 3. BFS 路径
        path = _map_bfs(cur, dest)
        if not path:
            return _with_state(f"🗺️ 知识库没找到从 {cur} 到 {dest} 的路径（缺地图链接）")
        # 4. 逐段执行（恒 2026-08-13 多段走路：走到出口瓦片 → 传送到下一图入口(ARRIVE) → 继续走）
        return _map_go_walk(path, destination, dest)
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
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


def _sprinkler_plan(x1: int, y1: int, w: int, h: int, unit: int):
    """生成洒水器布局（高级/铱共用逻辑）。

    unit=5(铱): 每5格单元 前2排满, 第3排 2-空-2(洒水器在中间), 后2排满
    unit=3(高级): 每3格单元 首排满, 次排 1-空-1(洒水器在中间)
    洒水器位置: row%unit==unit//2 且 col%unit==unit//2
    """
    tiles = []       # 播种格（严格）
    sprinklers = []  # 洒水器补位格
    for row in range(h):
        for col in range(w):
            if row % unit == unit // 2 and col % unit == unit // 2:
                sprinklers.append((x1 + col, y1 + row))
            else:
                tiles.append((x1 + col, y1 + row))
    return tiles, sprinklers


def _basic_sprinkler_plan(x1: int, y1: int, w: int, h: int, slant: int = 1):
    """初级洒水器布局：洒水器对角网格，蛇形按洒水器逐格锄。

    规则（实测2026-08-02）：行隔2、列隔3、每行斜移1。
    slant=1 往右下斜，slant=-1 往左下斜（都对）。

    返回 (sprinklers蛇形顺序, till_order=每洒水器4格作物组, plant_tiles去重)
    锄地按 till_order 逐洒水器锄（上右下左），连接下一洒水器。
    """
    # 洒水器网格
    raw = []
    row_idx = 0
    for ry in range(y1, y1 + h, 2):
        offset = slant * row_idx
        for rx in range(x1 + offset, x1 + w, 3):
            raw.append((rx, ry))
        row_idx += 1
    # 按行分组，蛇形排序（偶数行正向、奇数行反向）
    rows = {}
    for s in raw:
        rows.setdefault(s[1], []).append(s)
    ordered = []
    for idx, (_, row_spr) in enumerate(sorted(rows.items())):
        row_spr = sorted(row_spr)
        if idx % 2 == 1:
            row_spr = list(reversed(row_spr))
        ordered.extend(row_spr)
    # 每个洒水器的4格作物（上右下左），蛇形顺序
    till_order = []
    plant_set = set()
    for sx, sy in ordered:
        group = [(sx, sy - 1), (sx + 1, sy), (sx, sy + 1), (sx - 1, sy)]
        till_order.append((sx, sy, group))
        plant_set.update(group)
    return ordered, till_order, sorted(plant_set)


def _till_grid(x1: int, y1: int, w: int, h: int, hoe_level: int = 2):
    """蓄力锄头站位网格（面向下锄，站位在耕地外上方）。

    实测（2026-08-02）：Hoe.DoFunction 的 power 参数完全无效，范围由锄头 UpgradeLevel 决定，
    线从 facing tile 起、沿面向方向延伸。面向下：facing tile = (cx, ry+1)，线往下吃 line 格。
    每列按顶对齐分刀（range(y1, y1+h, line)），最后一刀自然覆盖到底（可能溢出田下 ≤line-1 格）。

    等级→范围: 0→1格, 1→3线, 2→5线, 3→3×3, 4→6×3。
    3/4 的 3×3/6×3 相对 facing tile 落点未实测校准 → 返回空（调用方提示）。

    Returns: [(stand_x, stand_y, facing)]，facing 恒为 2（面向下）。
    """
    if hoe_level >= 3:
        return []   # 金/铱锄 3×3/6×3 几何未校准（需实测），暂不规划
    line = 2 * hoe_level + 1
    stands = []
    for cx in range(x1, x1 + w):
        for top in range(y1, y1 + h, line):
            # 站在线起点的上一格，面向下 → facing tile = (cx, top)，线 = (cx, top..top+line-1)
            stands.append((cx, top - 1, 2))
    return stands


def plan_farm_layout(x1: int, y1: int, x2: int, y2: int, layout: int = 0, hoe_level: int = 2,
                     trellis: bool = False):
    """🌱 规划耕地（纯计算）：方形 → 洒水器布局 → 锄地/播种/洒水器格。

    trellis=True 时是爬架作物（啤酒花/青豆/葡萄——不可通过格）：
    - layout 0/2：**种2留1**（每3行留1行走道，AI 能走进田里浇/收），
      plant_tiles 自动过滤走道格，并按"每组先下排后上排"排序方便逐格种
    - layout 1（初级）：十字布局天然有走道，完全兼容，不做调整
    - layout 3（铱）：不做爬架（种少量交给 7842），plant_tiles 返回空

    Args:
        x1,y1: 方形左上角
        x2,y2: 方形右下角
        layout: 0=标准(整块无洒水器) 1=初级(蛇形) 2=高级(3倍数) 3=铱(5倍数)
        hoe_level: 锄头等级 0-4（范围: 0→1格 1→3线 2→5线 3→3×3 4→6×3）
        trellis: True=爬架作物布局（留走道）
    """
    w = x2 - x1 + 1
    h = y2 - y1 + 1

    if layout == 3:
        unit = 5
    elif layout == 2:
        unit = 3
    else:
        unit = None

    # 取倍数内的最大子矩形（铱/高级）
    if unit:
        w = w // unit * unit
        h = h // unit * unit

    # 播种格 + 洒水器
    till_order = []   # 每洒水器4格作物组（蛇形顺序），layout==1 才用
    if layout == 1:
        # 初级：对角洒水器布局，按洒水器逐格锄（每洒水器完整4格，蛇形连接）
        sprinklers, till_order, plant_tiles = _basic_sprinkler_plan(x1, y1, w, h)
        # 逐洒水器单格锄 4 格（上右下左），不用蓄力整片 → 无锄地站位
        till_stands = []
    else:
        if unit:
            plant_tiles, sprinklers = _sprinkler_plan(x1, y1, w, h, unit)
        else:
            # 默认：整块播种
            plant_tiles = [(x1 + c, y1 + r) for r in range(h) for c in range(w)]
            sprinklers = []
        # 锄地站位网格（蓄力工具，按锄头等级规划）
        till_stands = _till_grid(x1, y1, w, h, hoe_level)

    # ── 爬架走道（trellis）──
    if trellis and layout == 3:
        # 铱布局不做爬架（交给 7842 种少量）
        plant_tiles = []
        sprinklers = []
        till_stands = []
    elif trellis and layout != 1:
        # 种2留1：每3行留1行走道（相对地块 row%3==2），其余格按种植顺序排序
        # 顺序=每组(3行一循环)先种下排(lr=1, 站在上排空地)再种上排(lr=0, 站在走道)，
        #      → 保证站立的格永远是空/走道，不踩爬架
        plant_tiles = [t for t in plant_tiles if (t[1] - y1) % 3 != 2]
        plant_tiles.sort(key=lambda t: ((t[1] - y1) // 3,
                                        0 if (t[1] - y1) % 3 == 1 else 1,
                                        t[0]))

    return {
        "area": {"x1": x1, "y1": y1, "w": w, "h": h},
        "plant_tiles": plant_tiles,
        "sprinklers": sprinklers,
        "till_stands": till_stands,
        "till_order": till_order,
        "layout": layout,
        "hoe_level": hoe_level,
        "trellis": trellis,
        "walkway_rows": max(0, h // 3) if (trellis and layout != 1 and layout != 3) else 0,
    }


def plan_farm_layout_tool(x1: int, y1: int, x2: int, y2: int, layout: int = 0, hoe_level: int = -1,
                          trellis: bool = False) -> str:
    """🌱 规划耕地布局（方形→洒水器布局→锄地/播种格）

    layout: 0=标准整块(无洒水器) 1=初级(蛇形) 2=高级(3倍数) 3=铱(5倍数)
    hoe_level: 锄头等级 0-4（0→1格 1→3线 2→5线 3→3×3 4→6×3），-1=自动读当前手持锄头
    trellis: True=爬架作物（啤酒花/青豆/葡萄，不可通过格）→ 自动留走道（种2留1）

    Args:
        x1,y1,x2,y2: 可耕地方形
        layout: 布局类型 0-3
        hoe_level: 锄头等级 0-4（默认 -1 = 从 /state 自动读）
        trellis: 是否爬架作物布局（默认 False）
    """
    if hoe_level < 0:
        try:
            st = api.state()
            hoe_level = st.get("player", {}).get("currentToolUpgrade", 2)
        except Exception:
            hoe_level = 2
    p = plan_farm_layout(x1, y1, x2, y2, layout, hoe_level, trellis)
    a = p["area"]
    lines = [
        f"🌱 耕地规划 ({a['w']}x{a['h']} @ {a['x1']},{a['y1']}) | 锄头等级 {p['hoe_level']}"
        + (" | 🧗 爬架走道" if trellis else ""),
        f"  播种 {len(p['plant_tiles'])} 格 | 洒水器 {len(p['sprinklers'])} 个 | 锄地站位 {len(p['till_stands'])} 处",
    ]
    if trellis:
        if layout == 3:
            lines.append("  ⚠️ 铱洒水器不做爬架（种少量交给 7842），plant_tiles 为空")
        elif layout == 1:
            lines.append("  ✅ 初级十字布局天然有走道，完全兼容")
        elif p.get("walkway_rows"):
            lines.append(f"  🚶 已留 {p['walkway_rows']} 行走道（每3行留1行），种2留1")
    if p["hoe_level"] >= 3 and layout in (0, 2, 3) and not p["till_stands"]:
        lines.append("  ⚠️ 金/铱锄 3×3/6×3 落点未实测校准，暂不规划蓄力站位")
    if p["sprinklers"]:
        if layout == 1 and p["till_order"]:
            # 初级：显示蛇形逐洒水器锄地路径
            path = " → ".join(f"({sx},{sy})" for sx, sy, _ in p["till_order"][:8])
            if len(p["till_order"]) > 8:
                path += " …"
            lines.append(f"  🐍 蛇形锄地({len(p['till_order'])}洒水器): {path}")
        else:
            lines.append(f"  洒水器: {p['sprinklers'][:6]}{'…' if len(p['sprinklers'])>6 else ''}")
    return _with_state("\n".join(lines))


# ── 地皮规划 plot_plan（可复现，2026-08-15 恒）──
# 连通域分析：Diggable/HoeDirt + 内部设施(洒水器/稻草人/果树/箱子) + 杂草/树/石头 聚成连续地块，
# 只有水/悬崖/路径/建筑/装饰才截断。规划后可接 farm ops="clear"→till→plant→water。
_CLEAR_KW = ("grass", "weed", "stone", "twig", "stump", "log", "boulder",
             "meteorite", "tree", "debris")


def _is_clear_name(name):
    """名字是否是要清杂的类型。果树(FruitTree)/巨大作物(GiantCrop)/祝尼魔 保留不砍。"""
    n = (name or "").lower()
    if not n or "fruit" in n or "giantcrop" in n or "junimo" in n:
        return False
    return any(k in n for k in _CLEAR_KW)


# ⚠️ 2026-09-04 恒：只有这些算"可绕过设施"（种植时留着、绕着走）——洒水器各级/稻草人各级/火把营火。
#    别的 object（箱子/蟹笼/雕像/机器…）工具会铲起来 → 当阻挡，不进连通域、别框进种植区。
_IS_BYPASS_FACILITY = (
    "sprinkler",           # 基础/高品质/铱洒水器 + 压力喷嘴（名称都含 sprinkler）
    "pressure nozzle",     # 洒水器升级件（也含 sprinkler，双保险）
    "scarecrow",           # 稻草人
    "rarecrow",            # 稀有稻草人（稀有稻草人(rare#) / Rarecrow）
    "torch",               # 火把
    "campfire",            # 营火
)


def _is_bypass_facility(name):
    """是否是"可绕过设施"（留格绕过种植，不铲不挖）。名字子串命中即算。"""
    n = (name or "").lower()
    if not n:
        return False
    return any(k in n for k in _IS_BYPASS_FACILITY)


def _plot_classify(t):
    """地块格分类：planted/tilled/diggable/clear/facility，None=阻挡（水/悬崖/路径/建筑/装饰）。"""
    terr = t.get("terrain") or ""
    obj = t.get("object") or ""
    res = t.get("resource") or ""
    if terr == "HoeDirt":
        return "planted" if t.get("crop") else "tilled"
    if terr in ("FruitTree", "GiantCrop"):
        return "facility"          # 果树/巨大作物：保留（不锄不铲）
    if res:
        return "clear"             # 石头/树桩/巨石/陨石
    if terr and _is_clear_name(terr):
        return "clear"             # 草/杂草/普通树
    if obj and _is_clear_name(obj):
        return "clear"
    # ⚠️ 2026-09-04 恒：只有 洒水器各级+稻草人 算"可绕过设施"（留着，种植绕着走）；
    #    别的 object（箱子/蟹笼/火把/雕像/机器…）工具会铲起来，当**阻挡(None)**——
    #    方形会裁掉它、AI 不会把它框进种植区（否则以为能种，实际 object 格种不了）。
    if obj and _is_bypass_facility(obj):
        return "facility"
    if obj:
        return None                # 其它设备：阻挡（不进连通域，裁边绕开）
    # ⚠️ 2026-08-15 修：可耕必须 **passable**——山地/荒野农场的山崖/墙格 Back 层带 Diggable 属性，
    #    但走不上去也耕不了（实测 29 格 diggable+非passable）。只看 diggable 会把它当可耕 → till 失败。
    if t.get("diggable") and t.get("passable"):
        return "diggable"          # 未锄可耕地（可能含装饰格，till 时兜底报 reason）
    return None


def _plot_cell_label(t):
    """格显示名（清杂/设施清单用）。"""
    return t.get("object") or t.get("resource") or t.get("terrain") \
        or ("可耕地" if t.get("diggable") else "?")


def _building_footprints() -> set:
    """当前地图的建筑 footprint 格集合（温室/畜棚/鸡舍/筒仓…）——plot_plan 排除用。
    ⚠️ 只在 Farm 地图应用（/farm_buildings 返回的是 Farm 坐标）；不在农场 → 空集不误伤。
    2026-08-16 恒：AI 规划地皮把温室等建筑圈进可耕区 → 建筑 footprint 一律当阻挡格。"""
    try:
        cur = (api.state().get("location") or {}).get("name", "")
        if "Farm" not in cur:
            return set()
        pts = set()
        for b in _buildings():
            bx, by = b.get("x"), b.get("y")
            w, h = b.get("width"), b.get("height")
            if bx is None or by is None or not w or not h:
                continue
            for dx in range(int(w)):
                for dy in range(int(h)):
                    pts.add((int(bx) + dx, int(by) + dy))
        return pts
    except Exception:
        return set()


def _max_arable_square(pts, arable):
    """在连通域范围内找**最大方形**（裁边，不重算）——2026-09-04 恒改。
    旧版把设施/杂草/树当 0，另算一个"纯可耕无设施"的正方形 → 设施一多就缩成 3×3，
    与 plot 连通域规则（设施纳入，不截断连通）打架。
    ✅ 现在：方形内每一格只需是**连通域成员**（pts，含设施/清杂/可耕——都是种植区一部分），
    设施保留、种的时候绕着转；真正把连通域打断的格（水/悬崖/路径，不在 pts）才裁边。
    ⚠️ 所以这是"给不规则连通域裁方正区块"，不是"再把设施挖掉"。返回 (x,y,size)。
    """
    pts_set = set(pts)
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    if not xs:
        return None
    x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
    W = x2 - x1 + 1; H = y2 - y1 + 1
    dp = [[0] * W for _ in range(H)]
    best = 0; bx = by = 0
    for j in range(H):
        for i in range(W):
            if (x1 + i, y1 + j) in pts_set:   # 连通域成员（含设施）→ 算 1；不在 pts(打断格) → 0
                dp[j][i] = 1 if (i == 0 or j == 0) else \
                    min(dp[j - 1][i], dp[j][i - 1], dp[j - 1][i - 1]) + 1
                if dp[j][i] > best:
                    best = dp[j][i]; bx = x1 + i - best + 1; by = y1 + j - best + 1
    return (bx, by, best) if best else None


@mcp.tool()
def plot_plan(x: int = -1, y: int = -1, radius: int = 15, all_plots: bool = False) -> str:
    """🗺️ 地皮规划（可复现，2026-08-15 恒）：扫描以 (x,y) 为中心的区域，识别**连续可耕地块**——
    Diggable/HoeDirt + 内部设施（洒水器/稻草人/果树/箱子/雕像）+ 杂草/树/石头 聚成连通地块，
    只有水/悬崖/路径/建筑/装饰才截断。⚠️ 2026-08-16：建筑 footprint（温室/畜棚/鸡舍…）一律排除，
    不参与规划。输出地块边界 + 内部设施（保留）+ 需清杂 + 可锄 + 已种。

    默认聚焦包含 (x,y)（或最近）的那块连续地；all_plots=True 列出半径内全部地块摘要。
    可复现：纯扫描+连通域计算，同样输入→同样输出。规划后接
    farm ops="clear"(清杂) → till(锄) → plant(种) → water(浇)。

    Args:
        x, y: 地块中心坐标（-1=用当前位置；离玩家远会自动瞬移过去再扫）
        radius: 扫描半径（1-30，默认 15）
        all_plots: True=列出半径内所有连续地块（默认只聚焦中心那块）
    """
    try:
        st = api.state()
        cx0, cy0 = st["player"]["x"], st["player"]["y"]
        if x < 0 or y < 0:
            x, y = cx0, cy0
        if abs(cx0 - x) + abs(cy0 - y) > max(radius - 2, 2):
            try:
                api.position(x, y)
                time.sleep(0.3)
            except Exception:
                pass
        d = api.surroundings(min(max(radius, 1), 30))
        tiles = d.get("tiles", [])
        if not tiles:
            return _with_state("❌ 扫描无地块数据（可能不在可耕地场景）")
        grid = {(t["x"], t["y"]): t for t in tiles}
        from collections import Counter as _C

        # ⚠️ 2026-08-15 恒：其他玩家站着的格**不推荐耕**（别走到房主脚下开锄）——当阻挡格处理。
        occupied = set((p.get("x"), p.get("y")) for p in (st.get("otherPlayers") or []) if p.get("x") is not None)
        # ⚠️ 2026-08-16 恒：建筑 footprint（温室/畜棚/鸡舍…）当阻挡格——AI 规划别把建筑圈进可耕区。
        bld_fp = _building_footprints()

        def _cls(cx, cy):
            if (cx, cy) in occupied or (cx, cy) in bld_fp:
                return None
            return _plot_classify(grid.get((cx, cy), {}))

        def bfs_plot(start):
            q = [start]
            visited = {start}
            cells = {c: [] for c in ("planted", "tilled", "diggable", "clear", "facility")}
            while q:
                cx, cy = q.pop()
                c = _cls(cx, cy)
                if c:
                    cells[c].append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if (nx, ny) in grid and (nx, ny) not in visited:
                        if _cls(nx, ny) is not None:
                            visited.add((nx, ny))
                            q.append((nx, ny))
            return cells

        def plot_glyphs(cells):
            """一块连通域 cells → 文本行。"""
            pts = [p for c in cells for p in cells[c]]
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            n_tilled = len(cells["tilled"]); n_digg = len(cells["diggable"])
            n_planted = len(cells["planted"]); n_clear = len(cells["clear"]); n_fac = len(cells["facility"])
            # 可耕格数=本质需求（未锄可耕+已锄+已种）——AI 先看这个够不够，别被"最大方形"带偏
            n_arable = n_digg + n_tilled + n_planted
            lines = [f"  ({min(xs)},{min(ys)})-({max(xs)},{max(ys)}) {len(pts)}格"
                     f" | 可耕 {n_arable}（未锄 {n_digg} 已锄 {n_tilled} 已种 {n_planted}）"
                     f" 需清 {n_clear} 设施 {n_fac}"]
            # 🏁 最大方形（在连通域内裁边：设施保留、绕着种；只有打断连通的格才裁掉边）——
            #    2026-09-04 恒：旧版把设施当 0 重算出"纯可耕方形"缩成 3×3，与连通域规则打架。
            #    ★传 pts（连通域全格，含设施）而非 arable——这才是"设施纳入后的裁边"。
            sq = _max_arable_square(pts, None)   # new: 用 pts_set，arable 参数忽略
            if sq and sq[2] >= 1:
                lines.append(f"  🏁 最大可耕方形: ({sq[0]},{sq[1]})-({sq[0]+sq[2]-1},{sq[1]+sq[2]-1})"
                             f" {sq[2]}×{sq[2]}（设施在内，种时绕着留格；可耕共 {n_arable} 格）")
            if cells["facility"]:
                fac = _C(_plot_cell_label(grid[p]) for p in cells["facility"])
                lines.append(f"      设施(保留,种植留格绕过): {' '.join(f'{k}×{v}' for k, v in fac.most_common())}")
            if cells["clear"]:
                clr = _C(_plot_cell_label(grid[p]) for p in cells["clear"])
                lines.append(f"      需清(→ farm ops=clear): {' '.join(f'{k}×{v}' for k, v in clr.most_common())}")
            return lines

        if all_plots:
            # 列出半径内全部连通地块摘要；单格纯清杂（无可耕格）归为"孤立散点"
            visited = set()
            plots = []
            for (gx, gy), t in sorted(grid.items()):
                if (gx, gy) in visited or _plot_classify(t) is None:
                    continue
                cells = bfs_plot((gx, gy))
                plots.append(cells)
                visited |= set(p for c in cells for p in cells[c])
            if not plots:
                return _with_state(f"🗺️ @({x},{y}) r={radius}：没有可耕地块（扫到 {len(tiles)} 格）")
            real, scatter = [], []
            for cells in plots:
                pts = [p for c in cells for p in cells[c]]
                has_arable = cells["tilled"] or cells["diggable"] or cells["planted"]
                if len(pts) <= 1 and not has_arable:
                    scatter.extend(pts)
                else:
                    real.append(cells)
            lines = [f"🗺️ 地皮规划 @({x},{y}) r={radius}：{len(real)} 块连续地块"
                     + (f" + {len(scatter)} 处孤立散点" if scatter else "")]
            for i, cells in enumerate(real, 1):
                glyphs = plot_glyphs(cells)
                lines.append(f"  [{i}]" + glyphs[0])
                lines.extend(glyphs[1:])
            if scatter:
                lines.append(f"  ⚪ 孤立散点（单格杂草/树，可 farm ops=clear 顺路清）: {len(scatter)} 处")
            lines.append('  💡 要单块详细 → plot_plan(x, y)（默认聚焦中心那块）')
            return _with_state("\n".join(lines))

        # 默认：聚焦包含中心（或最近）的连通域——一块地一块地规划
        if (x, y) in grid and _plot_classify(grid[(x, y)]) is not None:
            start = (x, y)
        else:
            cand = [p for p, t in grid.items() if _plot_classify(t) is not None]
            if not cand:
                return _with_state(f"🗺️ @({x},{y}) r={radius}：附近没有可耕地块（扫到 {len(tiles)} 格）")
            start = min(cand, key=lambda p: abs(p[0] - x) + abs(p[1] - y))
        cells = bfs_plot(start)
        lines = [f"🗺️ 地皮规划 @({x},{y}) r={radius}（中心地块）"]
        lines.extend(plot_glyphs(cells))
        lines.append('  💡 接 farm ops="clear" 清杂 → till 锄 → plant 种 → water 浇；'
                     '要全部地块 → plot_plan(x, y, all_plots=True)')
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ plot_plan 失败: {e}")


@mcp.tool()
def check_worn() -> str:
    """🧥 查看穿戴物（衣服/裤子/帽子/捏人饰品/鞋子/左右戒指/饰品）
    显示装备的属性（鞋防御、戒指、饰品间隔等）。
    """
    try:
        r = api._get("/worn")
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', r)}")
        w = r.get("worn", {})
        lines = []
        lines.append(f"🧥 衣服: {w.get('shirt') or '无'} | 裤子: {w.get('pants') or '无'} | 帽子: {w.get('hat') or '无'} | 面部饰品: {w.get('accessory') or '无'}")
        b = w.get("boots")
        lines.append(f"👢 鞋: {b['name'] if b else '无'}" + (f" (防御{b['defense']} 免疫{b['immunity']})" if b else ""))
        for slot in ("leftRing", "rightRing"):
            ring = w.get(slot)
            if ring:
                if ring.get("combined"):
                    lines.append(f"💍 {slot}: {ring['name']}（合成：{' + '.join(ring['combined'])}）")
                else:
                    lines.append(f"💍 {slot}: {ring['name']}")
            else:
                lines.append(f"💍 {slot}: 无")
        t = w.get("trinket")
        if t:
            eff = t.get("effect") or {}
            eff_str = ", ".join(f"{k}={v}" for k, v in eff.items() if not k.startswith("<")) if eff else "?"
            lines.append(f"🔮 饰品: {t['name']} ({eff_str})")
        else:
            lines.append(f"🔮 饰品: 无")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def wear(name: Optional[str] = None, slot: Optional[str] = None, hand: Optional[str] = None) -> str:
    """🧥 穿/脱穿戴物（一个工具搞定，脚本自动判槽位）
    name 模式=从背包找同名物品穿上：自动判定槽位（衣服/裤子/帽/鞋/戒指/饰品），替下旧物回背包；
    slot 模式=把某槽脱下回背包。打 AI 角色(7843)。

    Args:
        name: 物品名（如"铁头靴"）——穿上，自动定位槽位
        slot: 槽位名（boots/leftRing/rightRing/trinket/hat）——脱下该槽；传了 name 则忽略 slot
        hand: 仅戒指用——指定换哪只手：1/left(左手) 或 2/right(右手)，或传"要替换掉的那枚戒指名"
             （脚本自动找它在哪只手就换那只）；不传=左空则左、否则右手
    ⚠️ 饰品需战斗精通（2026-08-23 恒）：未解锁时 /equip 侧权威拦截返回"未解锁战斗精通"，这里透传。
    """
    try:
        if not name and not slot:
            return _with_state("🎯 daily(ops=\"wear\", name=物品名) 穿上 或 daily(ops=\"wear\", slot=boots) 脱下（boots/leftRing/rightRing/trinket/hat）；戒指可加 hand=1/2 指定手")
        body = {}
        if name: body["name"] = name
        else: body["slot"] = slot
        if hand: body["hand"] = hand
        r = api._ai_post("/equip", body)
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error')}")
        if r.get("action") == "off":
            return _with_state(f"🧥 已脱下 {r.get('slot')}: {r.get('removed')}")
        replaced = r.get("replaced")
        rep = f"（旧物 {replaced} 回背包）" if replaced else ""
        return _with_state(f"🧥 已穿上 {r.get('equipped')} → {r.get('slot')}{rep}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def find_npc(name: str) -> str:
    """🔍 找到某个 NPC 现在在哪（社交用）
    扫全地图，返回位置+当前对话。找到后可用 go_to / walk_to 走过去搭话或送礼。

    Args:
        name: NPC 名字（中文名如"莉亚"，或英文名"Leah"，支持子串）
    """
    try:
        r = api._get("/find_npc", {"name": name})
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', r)}")
        nps = r.get("npcs", [])
        if not nps:
            return _with_state(f"🔍 没找到「{name}」——可能出门在外或名字不对")
        lines = []
        for n in nps[:8]:
            d = n.get("dialogue")
            extra = f" 💬「{d}」" if d else ""
            sleep = " 💤在睡" if n.get("isSleeping") else ""
            lines.append(f"  {n.get('displayName') or n.get('name')} @ {n.get('location')} ({n['x']},{n['y']}){sleep}{extra}")
        return _with_state(f"🔍 「{name}」找到 {r.get('count')} 处：\n" + "\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def chat_npc(name: str = "") -> str:
    """💬 跟 NPC 搭话（社交）——自动推进对话，一次性返回台词+选项
    - 不传 name → 列出 20 格内附近 NPC（名字+位置）
    - 传 name → 从当前坐标走路去找（position 兜底；跨图先 map_go 过去）+ 开口 + **自动推进对话**
      （纯文本自动点掉收台词，到选项/结束停下），一次性返回累计台词 + 🗳️ 选项。
    - 🎮 手动操作：推进对话 = press_key(ok) / /click（点画面中心）；选选项 = menu_click option=N；
      关对话 = menu_click button=close。剧情推进别用 key confirm / interact（会点面前格/设备）。

    社交对象由 AI 自己决策（好感度/节日/心情）。节日时全场 NPC 挨个聊。
    """
    try:
        s = api.surroundings(25)
        npcs = s.get("npcs", []) or []
        px, py = api.player_tile()
        # 只搭话 20 格内的 NPC（拟人：太远看不到，跑半天去找人不合理）
        nearby = []
        for n in npcs:
            nx, ny = n.get("x"), n.get("y")
            if nx is None or ny is None:
                continue
            if abs(int(nx) - px) + abs(int(ny) - py) <= 20:
                nearby.append(n)

        if not name:
            if not nearby:
                return _with_state("👥 周围 20 格内没有 NPC（想找远处的人用 chat_npc(\"名字\") 自动找过去）")
            parts = [f"{n.get('name')}({n.get('x')},{n.get('y')})" for n in nearby]
            return _with_state(f"👥 附近 NPC: {', '.join(parts)}\n→ chat_npc(\"名字\") 搭话")

        target = next(
            (n for n in nearby if name in (n.get("name") or "") or name in (n.get("displayName") or "")),
            None)
        nav_log = ""
        if target:
            tx, ty = int(target["x"]), int(target["y"])
            tname = target.get("displayName") or target.get("name") or name
        else:
            # 附近没找到 → find_npc 定位（跨图先 map_go 过去再走）
            f = api._get("/find_npc", {"name": name})
            found = (f.get("npcs") or []) if f.get("ok") else []
            if not found:
                return _with_state(f"❌ 没找到「{name}」——可能出门在外或名字不对")
            t = found[0]
            tx, ty = int(t["x"]), int(t["y"])
            tname = t.get("displayName") or t.get("name") or name
            tloc = t.get("location")
            cur = (api.state().get("location") or {}).get("name", "")
            if tloc and tloc != cur:
                go = map_go(tloc)
                if "✅ 到达" not in go and "已经在" not in go:
                    return _with_state(f"❌ 到不了「{tname}」所在的 {tloc}：\n{go[:300]}")
                nav_log = f"（已到 {tloc}）"
            # 同图或已到 → 走过去（walk_natural 自带 position 兜底，不会卡死）
            api.walk_natural(tx, ty + 1)

        # 走完复查：还是离 NPC 太远（>4格）→ 够不着（节日特殊位/墙后/兜底落错格），优雅跳过
        ax, ay = api.player_tile()
        if abs(ax - tx) + abs(ay - ty) > 4:
            return _with_state(
                f"💤 {tname} 在够不着的位置（离我 {abs(ax-tx)+abs(ay-ty)} 格，可能节日特殊位/墙后），"
                f"就不硬凑了。附近还有人可用 chat_npc() 列一下")
        # 面向 NPC + 开口（checkAction 打面前 NPC）
        api.face(0)
        r = api._post("/interact")
        if not r.get("ok"):
            return _with_state(f"❌ 搭话失败: {r.get('error', r)}")
        # 自动推进对话：纯文本 DialogueBox 用 key confirm 点掉收台词，到选项/事件/结束停下
        collected = []
        for _ in range(15):
            st = api.state(light=True)
            m = st.get("activeMenu") or {}
            ev = st.get("activeEvent") or {}
            if m.get("type") == "DialogueBox":
                d = (m.get("dialogue") or "").strip()
                if d and (not collected or collected[-1] != d):
                    collected.append(d)
                if m.get("responses"):
                    break                      # 出现选项 → 停，让 AI 选
                api.key("confirm")
                time.sleep(0.2)
                continue
            if ev.get("id"):
                _advance_story(m, ev)          # 事件 → 走剧情推进
                continue
            break                              # 没对话了
        # 汇总：一次性返回累计台词 + 选项
        lines = [f"💬 和 {tname} 搭话:{nav_log}"]
        if collected:
            for d in collected:
                lines.append(f"  「{d}」")
        else:
            lines.append("  （没有台词）")
        m = api.menu()
        opts = (m.get("responses") or []) if m.get("type") == "DialogueBox" else []
        if opts:
            lines.append(f"  🗳️ 选项: {' | '.join(f'[{i}]{t}' for i, t in enumerate(opts))}")
            lines.append("  → menu_click(option=N) 选择")
        else:
            lines.append("  → 对话结束（推进/再聊用 chat_npc 重开）")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 搭话失败: {e}")


# ═══════════════════════════════════════════
#  农活工具（4块独立操作）
# ═══════════════════════════════════════════

@mcp.tool()
def clear_area(x1: int, y1: int, x2: int, y2: int) -> str:
    """🧹 清理指定区域内的障碍物
    自动扫描区域内所有杂草/石头/树枝/树桩等，按工具分组清理。
    - 杂草/纤维 → 镰刀
    - 石头/矿石 → 镐子
    - 树枝/树桩 → 斧头
    先瞬移粗清 → 重扫漏网之鱼 → 精补(warp)，最后走一圈拾取掉落物。

    Args:
        x1, y1: 区域左上角坐标
        x2, y2: 区域右下角坐标
    """
    warp_log = _warp_home_if_needed("Farm")
    args_list = [str(x1), str(y1), str(x2), str(y2)]
    out = _run_script("clear_area", args_list, timeout=120)
    return _with_state(f"{warp_log}🧹 清理区域 ({x1},{y1})-({x2},{y2}):\n{out[:600]}")


# ── 化肥知识 ──
# HoeDirt.fertilizer 是 SDV1.6 NetString（限定 item ID，如 "(O)920"），/surroundings 报 fert 字段
# ⚠️ SDV 1.6 化肥编号大改（2026-08-13 游戏实测）：
#    465=Speed-Gro（不是 Deluxe Fertilizer）、466=Deluxe Speed-Gro、919=Deluxe Fertilizer、
#    370/371=保留土壤、920=Deluxe Retaining Soil；旧的 372/373/374 已不是化肥
FERTILIZER_IDS = {
    368: "Basic Fertilizer",
    369: "Quality Fertilizer",
    465: "Speed-Gro",
    466: "Deluxe Speed-Gro",
    919: "Deluxe Fertilizer",
    370: "Basic Retaining Soil",
    371: "Quality Retaining Soil",
    920: "Deluxe Retaining Soil",
    918: "Hyper Speed-Gro",
}
# 中英文别名（SDV 中文版 item 名；2026-08-13 恒：游戏可能切中英文，匹配要兼容）
FERTILIZER_CN = {
    368: "初级肥料",
    369: "高级肥料",
    465: "生长激素",
    466: "高级生长激素",
    919: "顶级肥料",
    370: "初级保湿土壤",
    371: "高级保湿土壤",
    920: "顶级保湿土壤",
    918: "极速生长激素",
}
FERTILIZER_ID_BY_NAME = {v: k for k, v in FERTILIZER_IDS.items()}
FERTILIZER_ID_BY_NAME.update({v: k for k, v in FERTILIZER_CN.items()})


def _norm_fert(fert_val):
    """fert 字段 → int ID 或 None（"(O)368"/"368"→368；非数字→None）"""
    if not fert_val:
        return None
    s = str(fert_val)
    if s.startswith("(O)"):
        s = s[3:]
    try:
        return int(s)
    except ValueError:
        return None


@mcp.tool()
def apply_fertilizer(fertilizer_name: str, x: int = -1, y: int = -1,
                     rows: int = 1, length: int = 1, direction: str = "horizontal") -> str:
    """🌱 撒化肥（照播种逻辑抄的：逐格走→撒→检测兜底）

    撒前扫描目标区：**已有同种化肥的格跳过**（不浪费），不同种才覆盖，
    没锄的格跳过并提醒。撒完 position 检测兜底，数落格报结果。
    ⚠️ 尺寸默认 1×1 不擅自扩；缺坐标→玩家面向格（2026-09-03 恒）。

    Args:
        fertilizer_name: 化肥名（Basic Fertilizer / Quality Fertilizer / Speed-Gro /
            Deluxe Speed-Gro / Deluxe Fertilizer / Hyper Speed-Gro / 保留土壤类）
        x: 起始 X 坐标（缺→玩家面向格）
        y: 起始 Y 坐标（缺→玩家面向格）
        rows: 撒几行（默认 1）
        length: 每行多长（默认 1 格）
        direction: horizontal=横着 / vertical=竖着（默认 horizontal）
    """
    warp_log = _warp_home_if_needed("Farm")

    # 1. 化肥 ID 解析（用于同种/异种检测；未知 ID 退化成"有化肥就跳过"）
    fert_id = FERTILIZER_ID_BY_NAME.get(fertilizer_name)

    # 2. 检查化肥在背包
    st = api.state()
    inv = st.get("inventory", [])
    if not any(fertilizer_name in (i.get("name") or "") for i in inv):
        return _with_state(f"{warp_log}❌ 背包里没有「{fertilizer_name}」")

    # 3. 算目标格
    x, y = _farm_default_xy(x, y)   # 缺坐标→玩家面向格
    dx, dy = (1, 0) if direction == "horizontal" else (0, 1)
    rdx, rdy = (0, 1) if direction == "horizontal" else (1, 0)
    target_tiles = []
    for r in range(rows):
        for i in range(length):
            tx = x + dx * i + rdx * r
            ty = y + dy * i + rdy * r
            target_tiles.append((tx, ty))

    # 4. 扫描现状：分 撒/跳过(同种)/占用(异种)/没锄
    # ⚠️ 先定位到田块中心再扫——不然目标超出扫描半径会被误判"没锄地"（2026-08-13 实测坑）
    # ⚠️ 异种化肥=占用（SDV 不允许已施肥的地改施别的化肥，实测覆盖不生效），跳过并提示
    do, skip, occupied, not_tilled = [], [], [], []
    try:
        api.position(x + dx * length // 2 + rdx * rows // 2,
                     y + dy * length // 2 + rdy * rows // 2)
        time.sleep(0.3)
    except Exception:
        pass
    try:
        surr = api.surroundings(max(length, rows) + 5)
        tiles = {(t["x"], t["y"]): t for t in surr.get("tiles", [])}
        for tx, ty in target_tiles:
            t = tiles.get((tx, ty), {})
            if t.get("terrain") != "HoeDirt":
                not_tilled.append((tx, ty))
                continue
            fert = _norm_fert(t.get("fert"))
            if fert is None:
                do.append((tx, ty))
            elif fert_id is not None and fert == fert_id:
                skip.append((tx, ty))
            else:
                occupied.append((tx, ty))
    except Exception:
        do = target_tiles[:]  # 扫描失败就全撒，事后检测兜底

    if not do:
        return _with_state(f"{warp_log}🌱 目标区 {len(target_tiles)} 格无需撒"
                           f"（同种跳过 {len(skip)}，异种占用 {len(occupied)}，没锄 {len(not_tilled)}）")

    # 5. 逐格撒（同 till_and_plant 的走位）
    api.select(fertilizer_name)
    time.sleep(0.2)
    for tx, ty in do:
        try:
            api.walk_natural(tx, ty - 1)
        except Exception:
            api.position(tx, ty - 1)
        api.face(2)
        time.sleep(0.1)
        api.use_item()
        time.sleep(0.35)

    # 6. 检测兜底：站田中央重扫，精确比对目标化肥 ID（不能只看"有化肥"——旧化肥会误报）
    time.sleep(0.4)
    cx = x + dx * length // 2 + rdx * rows // 2
    cy = y + dy * length // 2 + rdy * rows // 2
    try:
        api.position(cx, cy)
        time.sleep(0.3)
    except Exception:
        pass
    got = 0
    try:
        surr = api.surroundings(max(length, rows) + 5)
        tiles = {(t["x"], t["y"]): t for t in surr.get("tiles", [])}
        for tx, ty in do:
            fert = _norm_fert(tiles.get((tx, ty), {}).get("fert"))
            if fert_id is None:
                got += 1 if fert is not None else 0   # 未知化肥 ID：退化成"有化肥就算"
            elif fert == fert_id:
                got += 1
    except Exception:
        got = None  # 检测失败不误报

    lines = [f"🌱 撒化肥「{fertilizer_name}」 ({x},{y}) 起 {rows}x{length}" ]
    if got is not None:
        lines.append(f"  ✅ 已撒 {got}/{len(do)} 格")
    else:
        lines.append(f"  ✅ 已执行 {len(do)} 格（检测失败）")
    if occupied:
        lines.append(f"  🔒 异种化肥占用 {len(occupied)} 格（一块地只能撒一种，可镐掉重锄再撒）: {occupied[:6]}{'…' if len(occupied)>6 else ''}")
    if skip:
        lines.append(f"  ⏭️ 已有同种跳过 {len(skip)} 格")
    if not_tilled:
        lines.append(f"  ⚠️ 没锄地跳过 {len(not_tilled)} 格: {not_tilled[:8]}{'…' if len(not_tilled)>8 else ''}（先 till_and_plant 再撒）")
    return _with_state(f"{warp_log}\n" + "\n".join(lines))


@mcp.tool()
def till_and_plant(
    seed_name: str,
    x: int = -1,
    y: int = -1,
    rows: int = 1,
    length: int = 1,
    direction: str = "horizontal",
    trellis: bool = False,
) -> str:
    """🌱 翻地 + 播种一条龙
    自动锄地→播种，支持蛇形排列。
    执行前会先扫目标区域有没有障碍物（洒水器不算），有则提醒先除杂。

    trellis=True 时是爬架作物（啤酒花/青豆/葡萄，不可通过格）：
    自动锄整块田（含走道）→ 按爬架布局播种（种2留1留走道，先下排后上排）。
    爬架种子名都是 "Starter" 结尾（Bean/Hops/Grape Starter），没传 trellis 也能自动识别。

    Args:
        seed_name: 种子名称（如 Blueberry Seeds、Parsnip Seeds、Hops Starter）
        x: 起始 X 坐标（默认 60）
        y: 起始 Y 坐标（默认 10）
        rows: 耕几行（默认 5）
        length: 每行多长（默认 5 格）
        direction: horizontal=横着耕 / vertical=竖着耕（默认 horizontal）
        trellis: True=爬架作物留走道（默认 False，种子名含 Starter 自动识别）
    """
    # 爬架种子自动识别（Bean Starter / Hops Starter / Grape Starter）
    if seed_name.strip().lower().endswith("starter"):
        trellis = True
    warp_log = _warp_home_if_needed("Farm")
    x, y = _farm_default_xy(x, y)   # 2026-09-03 恒：缺坐标→玩家面向格（旧常量 (60,10) 会锄播到别处）

    # 计算目标地格
    dx, dy = (1, 0) if direction == "horizontal" else (0, 1)
    rdx, rdy = (0, 1) if direction == "horizontal" else (1, 0)
    target_tiles = []
    for r in range(rows):
        for i in range(length):
            tx = x + dx * i + rdx * r
            ty = y + dy * i + rdy * r
            target_tiles.append((tx, ty))

    # 扫描障碍物（洒水器过滤掉）
    # ⚠️ 先定位到田块中心再扫（同 apply_fertilizer：超出扫描半径会漏检——河流农场实测坑 2026-08-13）
    try:
        api.position(x + dx * length // 2 + rdx * rows // 2,
                     y + dy * length // 2 + rdy * rows // 2)
        time.sleep(0.3)
    except Exception:
        pass
    try:
        surr = api.surroundings(max(length, rows) + 5)
        seen_tiles = {(t["x"], t["y"]): t for t in surr.get("tiles", [])}
        blocked = []
        for tx, ty in target_tiles:
            t = seen_tiles.get((tx, ty))
            if not t:
                continue
            obj = t.get("object", "")
            terrain = t.get("terrain", "")
            # ⚠️ 2026-09-04 恒：可绕过设施（洒水器/稻草人/火把）不算障碍——它们留着、种的时候绕；
            #    只有真播种不了 / 会被铲的（箱子/蟹笼/雕像等其它 object）才报障碍。
            if obj and not _is_bypass_facility(obj):
                blocked.append((tx, ty, obj))
            elif terrain and "Tree" in terrain:
                blocked.append((tx, ty, terrain))
            elif t.get("passable") is False:
                blocked.append((tx, ty, "水/不可走"))   # 河流农场：河不能锄

        # ⚠️ 2026-09-03 恒：旧代码只要有水/设施就整单中止——农场设备+河密布，3×3 也凑不出"全净"
        #    → 一条龙永远跑不成（冒烟实测"反复规划失败"）。但 tool_area 锄地本就会跳过 object/非Diggable
        #    （ModEntry 逐格校验），farm_row --plant-only 也只种进有效格。所以障碍是"提示非中止"：
        #    只在整块全被挡才中止，否则照跑并附跳过清单。
        if blocked and len(blocked) >= len(target_tiles) * 0.8:
            lines = [f"  ({x},{y}): {name}" for x, y, name in blocked]
            return _with_state(
                f"⚠️ 目标区域 {len(blocked)}/{len(target_tiles)} 格被挡（设备/水/树），几乎没法锄——"
                + "请换块干净地或先 clear_area。\n"
                + "\n".join(lines[:10])
                + f"\n   例: clear_area(x1={x}, y1={y}, x2={x+dx*length+rdx*rows}, y2={y+dy*length+rdy*rows})"
            )
        elif blocked:
            # 只提示会跳过哪些，照跑（tool_area / farm_row 自会避开）
            pass
    except Exception:
        pass  # 扫描失败不阻塞，让 farm_row 自己的检测兜底

    # 转成 farm_row.py 的参数
    dir_map = {"horizontal": "right", "vertical": "down"}
    farm_dir = dir_map.get(direction, "right")

    if trellis:
        # ── 爬架作物：整块蓄力锄地（吃满升级锄头范围 + 逐下补漏）→ 爬架布局播种 ──
        x2 = x + dx * (length - 1) + rdx * (rows - 1)
        y2 = y + dy * (length - 1) + rdy * (rows - 1)
        # 1. 蓄力锄整块（_till_rect 内含 tool_area + DLL 自动取余补站位补漏）
        out = "  " + _till_rect(min(x, x2), min(y, y2), max(x, x2), max(y, y2))
        # 2. 爬架播种格（种2留1 + 先下排后上排）
        p = plan_farm_layout(x, y, x2, y2, 0, trellis=True)
        plant_tiles = p["plant_tiles"]
        if not plant_tiles:
            return _with_state(f"{warp_log}⚠️ 爬架布局没有可播种格（{p['walkway_rows']} 行走道）")
        api.select(seed_name)
        time.sleep(0.2)
        for tx, ty in plant_tiles:
            try:
                api.walk_natural(tx, ty - 1)
            except Exception:
                api.position(tx, ty - 1)
            api.face(2)
            time.sleep(0.1)
            api.use_item()
            time.sleep(0.35)
        out += f"\n  🧗 爬架播种 {len(plant_tiles)} 格 | 🚶 留 {p['walkway_rows']} 行走道（种2留1）"
        return _with_state(f"{warp_log}🌱 翻地播种爬架「{seed_name}」:\n{out[:800]}")

    # 2026-09-03 恒：一条龙改用可靠路径——锄地走 tool_area(_till_rect 自验收+补漏)，播种走 farm_row --plant-only。
    #    旧 farm_row 逐格走位+"position fallback"假成功（plot 0/0、种子未消耗）；tool_area 是已验证的可靠锄地。
    x2 = x + dx * (length - 1) + rdx * (rows - 1)
    y2 = y + dy * (length - 1) + rdy * (rows - 1)
    till_out = _till_rect(min(x, x2), min(y, y2), max(x, x2), max(y, y2))
    plant_out = _farm_plant_only(seed_name, x, y, rows, length, direction)
    return _with_state(f"{warp_log}🌱 翻地播种「{seed_name}」:\n{till_out}\n{plant_out}")


# 锄头优先级（中英文名都行——恒 2026-08-13：游戏可能切中英文，匹配要兼容）
HOE_PRIORITY = [
    "Iridium Hoe", "铱锄头", "铱锄",
    "Gold Hoe", "金锄头", "金锄",
    "Steel Hoe", "钢锄头", "钢锄",
    "Copper Hoe", "铜锄头", "铜锄",
    "Hoe", "锄头", "锄",
]


def _best_tool_name(inv, keywords, priority=None):
    """从背包挑优先级最高的工具名。priority 按从好到差排；没有匹配退化为 Contains 任一关键词。
    keywords 可以是 str 或 (str,...)（中英文都认）。"""
    if isinstance(keywords, str):
        keywords = (keywords,)
    if priority:
        for pref in priority:
            for i in inv:
                if (i.get("name") or "") == pref:
                    return pref
    for i in inv:
        name = i.get("name") or ""
        if any(k in name for k in keywords):
            return name
    return None


def _select_best_hoe():
    """自动选中背包里最好的锄头（优先级 铱>金>钢>铜>基础）。返回选中的名字。"""
    try:
        hoe = _best_tool_name(api.state().get("inventory", []), ("Hoe", "锄"), HOE_PRIORITY)
        if hoe:
            api.select(hoe)
            time.sleep(0.15)
            return hoe
    except Exception:
        pass
    api.select("Hoe")
    return "Hoe"


def _till_rect(x1: int, y1: int, x2: int, y2: int) -> str:
    """蓄力锄地矩形（升级锄头范围锄 tool_area；补漏 DLL 内自动——取余补站位蓄力补，不直接改地块）。
    返回报告字符串（不含状态条）——till_field 和爬架锄地共用。
    ⚠️ tool_area 蓄力释放是直接改地块（无挥锄动画，ModEntry charge-release 设计）；
    补漏接住 swing 几何漏掉的边列（2026-08-13 实测 x=4 列漏锄）——DLL 自检漏格→聚矩形→再蓄力补，
    不再靠 Python 逐下检测（2026-08-15 恒：封装进 ModEntry，till/water 统一）。
    """
    try:
        # 1. 确认锄头：当前不是最好的就换最好的（铱>金>钢>铜>基础；恒 2026-08-13 多把排优先级）
        st = api.state()
        tool = st.get("player", {}).get("currentTool") or ""
        best_hoe = _best_tool_name(st.get("inventory", []), ("Hoe", "锄"), HOE_PRIORITY)
        if best_hoe and tool != best_hoe:
            api.select(best_hoe)
            time.sleep(0.2)
            st = api.state()
        elif not best_hoe and "Hoe" not in tool:
            return "❌ 背包里没有锄头！"
        level = st.get("player", {}).get("currentToolUpgrade", 0)
        shape = {0: "1格", 1: "3线", 2: "5线", 3: "3×3", 4: "6×3"}.get(level, "?")

        # 2. tool_area 蓄力模拟执行（走位+蓄力可能 11s+，必须长超时——10s 会把蓄力截断）
        #    补漏由 ModEntry 自动做（至多 3 轮蓄力补），返回 patches / still_missing
        r = api._post("/tool_area", {"operation": "till", "x1": x1, "y1": y1, "x2": x2, "y2": y2}, timeout=600)
        if not r.get("ok"):
            return f"❌ tool_area 失败: {r.get('error', r)}"
        res = r.get("result") or {}
        executed = res.get("executed", 0)
        fail_cnt = sum(1 for x in (res.get("results") or []) if not x.get("ok"))
        patches = r.get("patches", 0)
        still = r.get("still_missing") or []

        # 3. 报告
        w = abs(x2 - x1) + 1
        h = abs(y2 - y1) + 1
        lines = [f"🌾 蓄力锄地 ({min(x1,x2)},{min(y1,y2)})-({max(x1,x2)},{max(y1,y2)}) {w}x{h} | 锄头{level}级({shape})"]
        lines.append(f"  ✅ tool_area 完成 | ⚙️ 蓄力命令 {executed} 条")
        if patches:
            lines.append(f"  🔧 取余补站位自动补漏 {patches} 格（DLL 蓄力补，不直接改地块）")
        if fail_cnt:
            lines.append(f"  ⚠️ {fail_cnt} 条命令失败")
        if still:
            lines.append(f"  ⚠️ 仍漏 {len(still)} 格（补漏仍失败，附原因——2026-08-15 恒）：")
            for m in still[:10]:
                rsn = m.get('reason') or ''
                lines.append(f"    ({m.get('x')},{m.get('y')})「{rsn}」" if rsn else f"    ({m.get('x')},{m.get('y')})")
            lines.append("  💡 漏格通常原因：装饰物品/石头树桩挡着、动物或房主站在那格、建筑贴边不可耕——"
                         "不是 bug，换个地块或清掉障碍再耕即可")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


def till_field(x1: int, y1: int, x2: int, y2: int) -> str:
    """🌾 蓄力锄地一块矩形田（AI 一句话犁地）
    自动读当前锄头等级决定蓄力范围（0:1格 1:3线 2:5线 3:3×3 4:6×3），
    调 tool_area 蓄力模拟执行（角色走位→蓄力→释放）。
    锄完逐下检测+补漏：扫目标区，被杂物/水挡住没锄出的格列出来补上。

    Args:
        x1, y1: 田左上角坐标
        x2, y2: 田右下角坐标
    """
    return _with_state(_till_rect(x1, y1, x2, y2))


@mcp.tool()
def hoe_layout(x1: int, y1: int, x2: int, y2: int, layout: int = 0) -> str:
    """🌾 按洒水器布局锄地（自动选锄地逻辑，AI 给坐标+布局模式）

    layout: 0=标准(整块无洒水器) 1=初级洒水器 2=高级洒水器 3=铱洒水器
    - layout==0（标准）：不预留洒水器，整块蛇形逐格走位锄（walk_natural 自然走+position兜底），
      任何锄头等级都适用
    - layout==1（初级）：洒水器是稀疏十字，用 /till_area 精确锄每个洒水器上下左右 4 格，
      锄头等级无关——升级了高级锄头也能做初级布局
    - layout 2/3（高级/铱）：整块蓄力锄（tool_area，吃满当前锄头等级，含自动选锄头；漏格 DLL 自动取余补站位）

    Args:
        x1, y1: 地块左上角坐标
        x2, y2: 地块右下角坐标
        layout: 洒水器布局模式 0-3
    """
    try:
        p = plan_farm_layout(x1, y1, x2, y2, layout)
        a = p["area"]
        if layout == 1:
            # ── 初级：按锄头等级分流 ──
            cross = p["plant_tiles"]
            n_spr = len(p["sprinklers"])
            st0 = api.state()
            hoe_level = st0.get("player", {}).get("currentToolUpgrade", 0)
            if hoe_level == 0:
                # 普通锄头 → 拟人逐格锄（走位+挥锄，像人一样一块块来）
                _select_best_hoe()
                for _sx, _sy, group in p["till_order"]:
                    for tx, ty in group:
                        api.walk_natural(tx, ty - 1)
                        api.face(2)
                        time.sleep(0.1)
                        api.use_item()
                        time.sleep(0.45)   # 挥锄动画
                method = "拟人逐格(基础锄)"
            else:
                # 高级锄头 → 直接 /till_area（物理挥锄对高级锄失效，一键锄完）
                api.till_area(tiles=[{"x": tx, "y": ty} for tx, ty in cross])
                method = "一键 /till_area(锄头{0}级)".format(hoe_level)
            # 逐下检测
            time.sleep(0.4)
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            try:
                api.position(cx, cy)
                time.sleep(0.3)
            except Exception:
                pass
            surr = api.surroundings(max(a["w"], a["h"]) // 2 + 6)
            tiles = {(t["x"], t["y"]): t for t in surr.get("tiles", [])}
            tilled = [pt for pt in cross if tiles.get(pt, {}).get("terrain") == "HoeDirt"]
            missing = [pt for pt in cross if pt not in tilled]
            lines = [f"🌾 初级洒水器布局锄地 ({a['x1']},{a['y1']})-({a['x1']+a['w']-1},{a['y1']+a['h']-1}) {a['w']}x{a['h']} | {method}"]
            lines.append(f"  🚿 {n_spr} 个洒水器 | 十字 {len(cross)} 格 | ✅ {len(tilled)}/{len(cross)} 锄出")
            if missing:
                lines.append(f"  ⚠️ 缺失 {len(missing)} 格（被杂物/水挡？）:")
                for mx, my in missing[:10]:
                    info = tiles.get((mx, my), {})
                    lines.append(f"    ({mx},{my}) {info.get('object') or info.get('resource') or info.get('terrain') or ''}")
            path = " → ".join(f"({sx},{sy})" for sx, sy, _ in p["till_order"][:8])
            if len(p["till_order"]) > 8:
                path += " …"
            lines.append(f"  🐍 蛇形: {path}")
            return _with_state("\n".join(lines))
        elif layout == 0:
            # ── 标准布局：不预留洒水器，整块蛇形逐格锄地 ──
            # 蛇形顺序（偶行正序、奇行反序，原作者 farm_row 的走位），
            # 逐格 walk_natural（自然走，走不到自动 position 兜底）→ 面向下挥锄
            w0, h0 = a["w"], a["h"]
            ax, ay = a["x1"], a["y1"]
            snake = []
            for r in range(h0):
                row = [(ax + c, ay + r) for c in range(w0)]
                if r % 2 == 1:
                    row.reverse()
                snake.extend(row)
            _select_best_hoe()
            for tx, ty in snake:
                api.walk_natural(tx, ty - 1)   # 走不到自动 position 兜底
                api.face(2)
                time.sleep(0.1)
                api.use_item()
                time.sleep(0.4)
            # 逐下检测
            time.sleep(0.4)
            cx = ax + (w0 - 1) // 2
            cy = ay + (h0 - 1) // 2
            try:
                api.position(cx, cy)
                time.sleep(0.3)
            except Exception:
                pass
            surr = api.surroundings(max(w0, h0) // 2 + 6)
            tiles = {(t["x"], t["y"]): t for t in surr.get("tiles", [])}
            tilled = [pt for pt in snake if tiles.get(pt, {}).get("terrain") == "HoeDirt"]
            missing = [pt for pt in snake if pt not in tilled]
            lines = [f"🌾 标准布局锄地 ({ax},{ay})-({ax+w0-1},{ay+h0-1}) {w0}x{h0}"]
            lines.append(f"  ✅ {len(tilled)}/{len(snake)} 锄出")
            if missing:
                lines.append(f"  ⚠️ 缺失 {len(missing)} 格（被杂物/水挡？）:")
                for mx, my in missing[:10]:
                    info = tiles.get((mx, my), {})
                    lines.append(f"    ({mx},{my}) {info.get('object') or info.get('resource') or info.get('terrain') or ''}")
            return _with_state("\n".join(lines))
        else:
            # ── 密排布局（高级/铱）：整块蓄力锄 ──
            return till_field(x1, y1, x2, y2)
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def plant_layout(x1: int, y1: int, x2: int, y2: int, layout: int, seed: str, direct: bool = False,
                 trellis: bool = False) -> str:
    """🌱 按洒水器布局播种（拟人逐格走位，或 direct 瞬移播种）

    播种位置由 plan_farm_layout 的 plant_tiles 决定：
    - 初级(layout=1): 种所有十字格（地在哪里种哪里）
    - 高级/铱(2/3): 种田里除去洒水器位的格
    - 整块(0): 整块种
    流程：选种子 → 到格上方 → face(下) → use_item 播种。
    direct=True 用瞬移（position）不走路，适合格多的密排布局；默认走位拟人。
    trellis=True 是爬架作物（啤酒花/青豆/葡萄）→ 自动留走道（种2留1），
    并按"先下排后上排"顺序种（保证站的格是空/走道，不踩爬架）。

    Args:
        x1, y1: 地块左上角坐标
        x2, y2: 地块右下角坐标
        layout: 洒水器布局模式 0-3
        seed: 种子名称（如 Blueberry Seeds / Parsnip Seeds）
        direct: True=瞬移播种(快) / False=走位拟人(慢但自然)
        trellis: True=爬架作物留走道（默认 False）
    """
    try:
        # 1. 检查种子在背包
        st = api.state()
        inv = st.get("inventory", [])
        if not any(seed in (i.get("name") or "") for i in inv):
            return _with_state(f"❌ 背包里没有「{seed}」")
        # 2. 算播种格
        p = plan_farm_layout(x1, y1, x2, y2, layout, trellis=trellis)
        plant_tiles = p["plant_tiles"]
        if not plant_tiles:
            return _with_state("⚠️ 这块地没有可播种的格子" + ("（铱洒水器不做爬架，交给 7842）" if trellis and layout == 3 else ""))
        # 3. 选种子 + 逐格播种（站在格上方面向下种；direct 瞬移 / 默认走位）
        api.select(seed)
        time.sleep(0.2)
        failed = []
        for tx, ty in plant_tiles:
            if direct:
                api.position(tx, ty - 1)
                time.sleep(0.1)
            else:
                try:
                    api.walk_natural(tx, ty - 1)
                except Exception:
                    api.position(tx, ty - 1)
            api.face(2)
            time.sleep(0.1)
            api.use_item()
            time.sleep(0.35)
        # 4. 验证（站在田中央扫，看哪些格种上了——terrain 有 crop 的）
        time.sleep(0.4)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        try:
            api.position(cx, cy)
            time.sleep(0.3)
        except Exception:
            pass
        a = p["area"]
        surr = api.surroundings(max(a["w"], a["h"]) // 2 + 6)
        tiles = {(t["x"], t["y"]): t for t in surr.get("tiles", [])}
        # crop 字段可能不存在，fallback 到 HoeDirt 计数
        planted = [pt for pt in plant_tiles
                   if tiles.get(pt, {}).get("crop") or tiles.get(pt, {}).get("terrain") == "HoeDirt"]
        lines = [f"🌱 按{ {0:'整块',1:'初级',2:'高级',3:'铱'}.get(layout,'?') }布局播种「{seed}」"
                 f" ({a['x1']},{a['y1']})-({a['x1']+a['w']-1},{a['y1']+a['h']-1})"
                 + ("（🧗爬架走道）" if trellis else "")]
        lines.append(f"  🚿 洒水器 {len(p['sprinklers'])} 个 | 播种 {len(plant_tiles)} 格")
        if trellis and p.get("walkway_rows"):
            lines.append(f"  🚶 已留 {p['walkway_rows']} 行走道（种2留1）")
        # 尝试数真实作物
        real_crops = [pt for pt in plant_tiles if tiles.get(pt, {}).get("crop")]
        if real_crops:
            lines.append(f"  ✅ {len(real_crops)}/{len(plant_tiles)} 已长出作物")
        else:
            lines.append(f"  ✅ 播种完成 {len(plant_tiles)} 格")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def water_crops() -> str:
    """💧 给作物浇水
    自动检测：
    - 🌧️ 下雨/暴雨 → 跳过浇水
    - 💧 水壶没水 → 自动装满
    - ⏰ 晚10点后不浇（明天再说）
    """
    # ⚠️ 2026-08-15：tool_area 蓄力走位可能很久（基础壶逐格/大田），60s 会掐断浇水脚本
    result = _run_script("water_crops", timeout=600, async_ok=True)
    if result.startswith("🚀"):
        return _with_state(result)   # 长脚本自动异步：立即返回 job_id
    # 尝试从输出判断下雨
    if "rain" in result.lower() or "跳过" in result:
        return _with_state(f"💧 浇水: 下雨天，跳过\n{result[:300]}")
    return _with_state(f"💧 浇水完成\n{result[:600]}")


@mcp.tool()
def _water_pet_bowls() -> list:
    """💧 宠物碗浇水逻辑（2026-08-16 恒实测正确站位）：动态找 Pet Bowl 建筑 →
    逐个站碗建筑位 + 朝右 + /tool 浇碗格。返回报告行列表（没水壶 → ['⚠️ 没带水壶']）。"""
    report_parts = []
    try:
        inv = api.state().get("inventory", [])
        wc = None
        wc_name = None
        for item in inv:
            name = item.get("name", "")
            if "Watering Can" in name:
                wc = item
                wc_name = name
                break
        if not wc:
            return ["⚠️ 没带水壶！请先拿上水壶再调用 pet_water / pet_pets"]

        water = wc.get("waterLeft", 0)
        report_parts.append(f"💧 水壶: {wc_name} ({water}/{wc.get('waterMax','?')})")
        if water == 0:
            report_parts.append("  水壶没水，自动装满")
            api.refill_water()
            time.sleep(0.3)

        # 🐾 2026-09-05 恒：改用 farm_buildings（/farm_buildings 直读 Farm.buildings，与站位无关）。
        #    旧 map_data(/map) 的 buildings 跟着玩家走，站农场中央只看到附近碗 → "发现1个"漏浇其他碗。
        fb = api.farm_buildings()
        bowls = [b for b in fb.get("buildings", []) if b.get("type") == "Pet Bowl"]
        if not bowls:
            pb = api._get("/petbowl")
            if pb.get("ok") and pb.get("bowl"):
                bx, by = pb["bowl"]["x"], pb["bowl"]["y"]
                bowls = [{"x": bx, "y": by}]
            else:
                return ["⚠️ 没找到宠物碗（Pet Bowl）"]

        report_parts.append(f"🔍 发现 {len(bowls)} 个宠物碗")
        s = api.state()
        if s.get("location", {}).get("name") != "Farm":
            api.walk_to_coord("Farm", 64, 15)

        for i, bowl in enumerate(bowls):
            bx, by = bowl["x"], bowl["y"]
            try:
                # ⚠️ 站碗建筑位 + 朝右 + /tool 浇 (bx+1, by) 碗格（恒实测：door_y+1 站远浇不到；别 walk_to）
                # 🐾 2026-09-05 恒：自然走位到碗旁（walk_to 拟人，别硬瞬移）；够不着 position 兜底（同摸宠物）。
                # ⚠️ 站碗建筑位 + 朝右 + /tool 浇 (bx+1, by) 碗格（恒实测：door_y+1 站远浇不到）
                # ⚠️ 2026-09-04 恒：旧版 use_tool 挥壶只 DoFunction（"尿尿"无挥动画）但真浇上碗；新版 interact_at 对宠物碗走不通。
                #    2026-09-05 恒挑战：/tool 水壶分支 BeginUsingTool+DoFunction+reset → 单次挥舞动画+效果+不卡蓄力。精动画留 TryPetBowlInteract。
                _loc = (api.state().get("location") or {}).get("name", "Farm")
                api.walk_to_coord(_loc, bx, by)
                time.sleep(0.4)
                _ax, _ay = api.player_tile()
                if abs(_ax - bx) + abs(_ay - by) > 1:
                    api.position(bx, by)   # 兜底：走不到就贴近（不硬卡）
                    time.sleep(0.3)
                api.face(1)
                time.sleep(0.3)
                api.select(wc_name)
                time.sleep(0.3)
                api.refill_water()
                time.sleep(0.2)
                api.use_tool(wc_name)
                time.sleep(0.5)   # 🐾 挥动画(约0.5s)播完再走下一碗
                report_parts.append(f"  🐾 碗{i+1} @ ({bx},{by}) 完成")
            except Exception as e:
                report_parts.append(f"  ⚠️ 碗{i+1} 浇水失败: {e}")
    except Exception as e:
        report_parts.append(f"❌ 浇水出错: {e}")
    return report_parts


@mcp.tool()
def pet_water() -> str:
    """💧 宠物碗喂水（独立工具）：动态找所有碗 → 逐个站碗位朝右浇水
    检查水壶，没带/没水会提示。不摸宠物（摸用 pet_pet / pet_walk）。"""
    parts = _water_pet_bowls()
    return _with_state("\n".join(parts))


def _pet_pets_natural() -> str:
    """🐱 自然走摸猫狗（当前场景）：/surroundings 找 kind=pet 的 NPC →
    自然走（walk_to）过去，够不着 position 兜底，面朝 + interact 摸。
    只摸当前场景，宠物在家/别的场景不跨图找（恒 2026-08-16）。"""
    try:
        d = api.surroundings(30)
        pets = [n for n in d.get("npcs", []) if n.get("kind") == "pet"]
        if not pets:
            return "当前场景没有猫狗宠物（可能在家/别的场景，不跨图找）"
        loc = api.state()["location"]["name"]
        got = []
        for p in pets:
            try:
                px, py = int(p.get("x")), int(p.get("y"))
                # 自然走（游戏导航）过去，够不着再 position 兜底
                api.walk_to_coord(loc, px, py + 1)
                time.sleep(0.4)
                ax, ay = api.player_tile()
                if abs(ax - px) + abs(ay - py) > 1:
                    api.position(px, py + 1)   # position 兜底
                    time.sleep(0.3)
                api.face(0)
                time.sleep(0.2)
                r = api._post("/interact")      # 右键摸
                time.sleep(0.3)
                got.append(p.get("name"))
            except Exception:
                continue
        return f"🐱 自然走摸了 {len(got)}/{len(pets)} 只宠物（{'、'.join(got) if got else '无'}）"
    except Exception as e:
        return f"摸宠物出错: {e}"


@mcp.tool()
def pet_pet() -> str:
    """🐾 摸摸宠物（猫狗，2026-08-24 恒：pet 只指猫狗）：当前场景自然走摸猫狗（_pet_pets_natural）。
    ⚠️ 牲畜（牛羊鸡鸭）不在这里——用 care_animals（摸+挤奶剪毛+室外放牧）。
    走过去→面朝→interact，够不着 position 兜底。区别于 petall 作弊摸。"""
    return _with_state(_pet_pets_natural())


# ⚠️ pet_pets 已退役（2026-08-16 恒：作弊一条龙不要了）——AI 自己组合 care ops="pet water"
#    （喂水 pet_water + 自然走摸 pet_pet），跟 farm 域 "till plant water" 一样的用法。


def _find_animal_buildings() -> list:
    """找农场上所有动物建筑（鸡舍/畜棚等）。
    ⚠️ 2026-08-26 恒：原来读 /map，而 /map 是**跟着玩家当前位置走**的——
    站在 FarmHouse/棚内时 buildings 返回 0 个 → 误判"没有动物建筑"。
    实测新档站 FarmHouse：/map 0 个，/farm_buildings 7 个（含 Coop+Barn）。
    目前被 care_animals 开头的 _warp_home_if_needed("Farm") 挡着没出事，
    但那是运气不是设计——改读 /farm_buildings（直接读 Farm.buildings，与站位无关）。
    /map 保留兜底：万一新端点出错还能退回老路。"""
    animal_types = {"Deluxe Coop", "Big Coop", "Coop",
                    "Deluxe Barn", "Big Barn", "Barn"}
    try:
        info = api.farm_buildings()
        found = [b for b in info.get("buildings", []) if b.get("type") in animal_types]
        if found:
            return found
    except Exception:
        pass
    try:
        map_info = api.map_data()
        return [b for b in map_info.get("buildings", [])
                if b.get("type") in animal_types]
    except Exception:
        return []


def _pet_digest(out: str, limit: int = 500) -> str:
    """从 pet_walk 输出里挑真正有信息的行（结果/失败/告警）。
    ⚠️ 2026-08-26 恒：以前是 out[:300] / out[:400] 从**头部**硬截——
    pet_walk 开头先打一串"· 名字 [类型] (x,y)"清单，300 字全被清单吃掉
    （实测截在「橙子 [White C」），摸没摸到一个字都看不见，
    只能靠尾巴那句"还剩N只"反推。现在丢清单行、保留结果尾巴。"""
    keep = []
    for ln in (out or "").splitlines():
        t = ln.strip()
        if not t:
            continue
        if "·" in t and "[" in t and "]" in t:      # 动物清单行 → 丢
            continue
        keep.append(t)
    s = "\n".join(keep)
    return s[-limit:] if len(s) > limit else s


def _pet_animals_in_building() -> str:
    """摸当前建筑内牲畜（牛羊鸡鸭，2026-08-24 恒）：拟人自然走路摸（pet_walk 已改读 /animals 物理位置）。
    室内空则提示可能跑 Farm 放牧（室外放牧动物交给 _grazing_care / care_animals）。"""
    try:
        data = api.animals()
        all_a = data.get("animals", [])
        if not all_a:
            return "没有动物（可能跑 Farm 放牧了——farm animals 会去 Farm 处理室外放牧动物）"
        out = _run_script("pet_walk", [], timeout=300)
        return f"🐄 摸牲畜：\n{_pet_digest(out)}"
    except Exception as e:
        return f"摸动物出错: {e}"


def _has_tool(name: str) -> bool:
    """背包里有没有某工具（中英文名匹配）。"""
    try:
        inv = api.state().get("inventory", [])
        for i in inv:
            nm = (i.get("displayName") or "") + "/" + (i.get("name") or "")
            if name in nm or any(k in nm for k in name.split("/")):
                return True
        return False
    except Exception:
        return False


def _milk_shear_animals(skip_grabber: bool = False) -> str:
    """挤牛奶+剪羊毛（2026-08-16）：对当前建筑内奶牛/绵羊选对应工具逐个交互。
    游戏自动处理：有产物收集（进背包），没产物弹提示（"没有奶/没毛"，小牛小羊无产物）。
    不依赖 productReady 预判（自动采集器档 product 恒 None，仍可尝试交互）。
    skip_grabber=True：当在 Farm 上处理室外放牧动物时跳过"自动采集器→不用挤奶剪毛"判断（室外无自动采集器，2026-08-24 恒）。"""
    try:
        # ⚠️ 恒 2026-08-16：畜棚/鸡舍有自动采集器 → 产物已自动收，不用挤奶/剪毛，只摸摸
        if not skip_grabber:
            try:
                _ms = api.machines()
                if any("Grabber" in (x.get("type") or "") for x in (_ms.get("machines") or [])):
                    return "🤖 这间有自动采集器，产物已自动收集——不用挤奶/剪毛，只用 farm animals 摸摸"
            except Exception:
                pass
        data = api.animals()
        all_a = data.get("animals", [])
        if not all_a:
            return "没有动物（可能跑 Farm 放牧了）"
        # ⚠️ 恒 2026-08-16：奶牛+山羊=奶（挤奶桶），绵羊=羊毛（剪刀），猪（松露靠找）不管
        cows = [a for a in all_a if "Cow" in a.get("type", "") or "Goat" in a.get("type", "")]
        sheep = [a for a in all_a if a.get("type") == "Sheep"]
        reports = []
        # 先清任何残留菜单（防止卡住循环）
        try:
            _m = api.menu()
            if isinstance(_m, dict) and _m.get("open"):
                api._post("/menu_close")
                time.sleep(0.4)
        except Exception:
            pass

        def _do(tool, animals, emoji, verb):
            cn, eng = tool   # (中文名给 /select, 英文名给 currentTool 确认)
            if not _has_tool(cn):
                return f"{emoji} 没带{cn}——去玛妮牧场买"
            got = []
            for a in animals:
                try:
                    # ⚠️ 恒 2026-08-16：每轮重新取动物当前位置（动物会走，初始位置过期 → /use 落空）
                    cur = None
                    for _ in range(3):
                        try:
                            fresh = api.animals().get("animals", [])
                            cur = next((x for x in fresh if x.get("name") == a.get("name")), None)
                            if cur:
                                break
                        except Exception:
                            pass
                        time.sleep(0.3)
                    if not cur:
                        continue
                    ax, ay = cur.get("x", 0), cur.get("y", 0)
                    for px, py, fd in [(ax, ay+1, 0), (ax+1, ay, 3), (ax-1, ay, 1), (ax, ay-1, 2)]:
                        # 清残留菜单（防止 select 被卡）
                        try:
                            _mm = api.menu()
                            if isinstance(_mm, dict) and _mm.get("open"):
                                api._post("/menu_close")
                                time.sleep(0.3)
                        except Exception:
                            pass
                        # 选工具（中文名）+ **确认 currentTool 切换成功（重试）**——否则拿错工具对错动物
                        api._post("/select", {"name": cn})
                        tool_ok = False
                        for _ in range(6):
                            time.sleep(0.2)
                            ct = (api.state().get("player") or {}).get("currentTool") or ""
                            if eng in ct:
                                tool_ok = True
                                break
                            api._post("/select", {"name": cn})   # 重试 select
                        if not tool_ok:
                            got.append(f"{a.get('name','?')}[工具没切换]")
                            break
                        api.position(px, py)
                        time.sleep(0.1)
                        api.face(fd)
                        time.sleep(0.1)
                        r = api.use_item()
                        # ⚠️ 弹窗延迟出现：/use 后轮询等 DialogueBox（最多 ~2s），别读错/读早
                        msg = None
                        for _ in range(8):
                            time.sleep(0.25)
                            m = api.menu()
                            if isinstance(m, dict) and m.get("open") and m.get("type") == "DialogueBox":
                                msg = (m.get("dialogue") or "").strip()
                                break
                        if msg is not None:
                            # ⚠️ 关弹窗用 /menu_close（可靠）——key confirm 可能关掉后又触发世界交互
                            api._post("/menu_close")
                            time.sleep(0.3)
                            tag = "✅" if ("不产" not in msg and "没有" not in msg and "没毛" not in msg) else "⭕"
                            got.append(f"{a.get('name','?')}{tag}{msg[:10]}")
                        elif r.get("ok"):
                            got.append(a.get("name", "?"))
                        time.sleep(0.3)  # 每只后多歇一会，稳（恒 2026-08-16）
                        break
                except Exception:
                    continue
            return f"{emoji} {verb} {len(got)}/{len(animals)} 只（{'、'.join(got) if got else '无'}）"

        if cows:
            reports.append(_do(("挤奶桶", "Milk Pail"), cows, "🐮", "挤奶"))
        if sheep:
            reports.append(_do(("剪刀", "Shears"), sheep, "🐑", "剪毛"))
        return "；".join(reports) if reports else "没有奶牛/绵羊"
    except Exception as e:
        return f"剪毛/挤奶出错: {e}"


def _enter_building(b: dict) -> tuple:
    """走进动物建筑，返回 (成功?, 日志)"""
    dx, dy = b.get("doorX", b["x"]), b.get("doorY", b["y"])
    logs = []

    # 🚪 2026-08-26 恒：进门前先确保人已经在 Farm 上。
    #    以前直接 walk_to_coord("Farm", dx, dy)——人要是还在上一个棚里，这就是跨图寻路，
    #    而 C# 跨图 walk_to 会拿"第一条通向该图的 warp 落点"当入口（HandleWalkTo 扫 Game1.locations），
    #    实测扫到的第一条是 FarmHouse(27,31) → Farm(64,15)，也就是农舍门口。
    #    症状：查完一个棚要先大老远传回农舍、再从农舍走到下一个棚。
    #    直接 warp 到目标门口下方，跨图这一段就没了。
    try:
        cur = api.state().get("location", {}).get("name", "")
        if cur != "Farm":
            api.warp("Farm", dx, dy + 1)
            time.sleep(0.8)
            logs.append(f"↩️ 从 {cur} 直接落到门口下方 ({dx},{dy + 1})，不绕农舍")
    except Exception as e:
        logs.append(f"⚠️ 回 Farm 失败: {e}")

    # walk_to 到门瓷砖
    api.walk_to_coord("Farm", dx, dy)
    arrived = False
    for _ in range(20):
        s = api.state()
        if s["player"]["x"] == dx and s["player"]["y"] == dy:
            arrived = True
            break
        time.sleep(0.5)

    # ⚠️ 2026-08-26 恒：这行以前是无条件打印"走到门口"——walk_to 没走到也照报，
    #    把"进门失败"的真实原因（压根没站上门格）盖得死死的。现在报真实落点。
    #    门格必须精确站上才能进，差几格就 position 顶上去。
    s = api.state()
    px, py = s["player"]["x"], s["player"]["y"]
    if arrived:
        logs.append(f"走到门口 ({dx},{dy})")
    else:
        logs.append(f"⚠️ 没走到门格：目标({dx},{dy}) 实际({px},{py})")
        try:
            api.position(dx, dy)
            time.sleep(0.5)
            s = api.state()
            px, py = s["player"]["x"], s["player"]["y"]
            if (px, py) == (dx, dy):
                logs.append(f"  → position 校正成功 ({px},{py})")
            else:
                logs.append(f"  → position 校正后仍在 ({px},{py})")
        except Exception as e:
            logs.append(f"  → position 校正失败: {e}")

    # 进门：interact(confirm) → 当场抓"建造中"对话（2026-08-24 恒：进在建建筑按 confirm 会弹它、被后续按键点掉，
    # 得在它刚出现时抓，别等序列结束后读——对话早已被点没）
    api.key("confirm")
    time.sleep(0.4)
    try:
        dlg = (api.menu().get("dialogue") or "").strip()
        low = dlg.lower()
        if any(k in low for k in ("建造中", "在建", "施工", "未完工", "under construction", "in construction", "being built", "construction")):
            api.key("confirm")   # 点掉"建造中"对话
            time.sleep(0.2)
            logs.append(f"⚠️ 建筑在建中（未完工）：「{dlg}」——跳过")
            return False, "\n".join(logs)
    except Exception:
        pass

    api.face(0)
    time.sleep(0.2)
    api.key("X")
    time.sleep(1.5)

    s = api.state()
    loc = s.get("location", {}).get("name", "")
    if loc == "Farm":
        # 2026-08-26 恒：带上真实站位——光一句"进门失败"查不出是没站上门格还是门本身没反应
        logs.append(f"❌ 进门失败（人在 Farm({s['player']['x']},{s['player']['y']})，门在({dx},{dy})）")
        return False, "\n".join(logs)

    logs.append(f"✅ 进入 {loc} ({s['player']['x']},{s['player']['y']})")
    return True, "\n".join(logs)


@mcp.tool()
def care_building() -> str:
    """🐄 只照顾当前所在建筑内的动物（摸+收产物）
    需站在鸡舍/畜棚内调用，不会影响其他建筑。
    使用 position 瞬移至动物旁交互，不走 BFS 避免卡墙。
    """
    s = api.state()
    loc = s.get("location", {}).get("name", "")
    report_parts = [f"🏠 当前位置: {loc}"]

    out = _pet_animals_in_building()
    report_parts.append(f"🐄 {out}")

    return _with_state("\n".join(report_parts))


def _grazing_care() -> str:
    """🌾 室外放牧牲畜照料（2026-08-24 恒：忘关门牛羊鸡跑 Farm 上）。AI 站在 Farm 上调用——
    读 api.animals()(=farm.animals 放牧动物)，拟人自然走路摸（pet_walk 已改读 /animals 物理位置，
    petall 摸不到室外）+ 挤奶剪毛(_milk_shear_animals skip_grabber=True，室外无自动采集器)。无放牧动物→空串。"""
    try:
        data = api.animals()
        if not data.get("animals") or not data.get("count"):
            return ""   # 全在室内 / 没放牧动物 → 不打扰
        out = _run_script("pet_walk", [], timeout=300)
        parts = [f"🌾 室外放牧:\n{_pet_digest(out)}"]
        mss = _milk_shear_animals(skip_grabber=True)
        if mss and "没有动物" not in mss:
            parts.append(mss)
        return "\n".join(parts)
    except Exception as e:
        return f"🌾 室外放牧出错: {e}"


@mcp.tool()
def care_animals() -> str:
    """🐄 摸牲畜 + 收产物（牛羊鸡鸭，不进不出，不动门）
    进门摸所有牲畜 + 收取产物，然后出门；最后补一遍室外放牧牲畜（忘关门跑 Farm 上的）。
    门由 AI 根据天气/季节决定是否开（open_doors/close_doors）。冬天、雨天不用开门放牧。
    ⚠️ 宠物（猫狗）用 pet_pet；牲畜（牛羊鸡鸭）用本工具。
    """
    warp_log = _warp_home_if_needed("Farm")
    report_parts = []

    buildings = _find_animal_buildings()
    if not buildings:
        report_parts.append("⚠️ 没找到动物建筑（鸡舍/畜棚）——但仍可能有室外放牧牲畜")
    else:
        report_parts.append(f"🔍 发现 {len(buildings)} 个动物建筑")

    for b in buildings:
        name = b["type"]
        dx, dy = b.get("doorX", b["x"]), b.get("doorY", b["y"])
        report_parts.append(f"\n--- {name} @ 门({dx},{dy}) ---")

        # 进门
        ok, enter_log = _enter_building(b)
        report_parts.append(f"  {enter_log}")
        if not ok:
            continue

        # 摸牲畜
        pet_report = _pet_animals_in_building()
        report_parts.append(f"  🐄 {pet_report}")
        # 挤奶+剪毛（2026-08-16：奶牛/绵羊用对应工具交互，游戏自动处理有/无产物）
        ms_report = _milk_shear_animals()
        report_parts.append(f"  {ms_report}")

        # 出门回 Farm
        try:
            api.warp("Farm", dx, dy + 1)
            time.sleep(0.5)
            report_parts.append(f"  ✅ 出门")
        except Exception as e:
            report_parts.append(f"  ⚠️ 出门失败: {e}")

    # 🌾 室外放牧牲畜（忘关门跑 Farm 上的，2026-08-24 恒）
    grazing = _grazing_care()
    if grazing:
        report_parts.append(grazing)

    return _with_state(f"{warp_log}畜牧时间：\n" + "\n".join(report_parts) + "\n" + _progress_line("animals"))


@mcp.tool()
def milk_shear() -> str:
    """🐮🐑 挤牛奶 + 剪羊毛：进所有动物建筑，对奶牛/绵羊选对应工具逐个交互
    游戏自动处理：有产物收集进背包，没产物弹提示（小牛小羊无奶/刚挤过）。
    没带挤奶桶/剪刀会提示去玛妮牧场买。配合 farm animals（摸）一起用。
    """
    warp_log = _warp_home_if_needed("Farm")
    buildings = _find_animal_buildings()
    if not buildings:
        return _with_state(f"{warp_log}⚠️ 没找到动物建筑（鸡舍/畜棚）")
    report_parts = [f"🔍 发现 {len(buildings)} 个动物建筑"]
    for b in buildings:
        dx, dy = b.get("doorX", b["x"]), b.get("doorY", b["y"])
        ok, enter_log = _enter_building(b)
        if not ok:
            report_parts.append(f"  ❌ {b['type']} 进不去")
            continue
        ms = _milk_shear_animals()
        report_parts.append(f"\n--- {b['type']} ---\n  {ms}")
        try:
            api.warp("Farm", dx, dy + 1)
            time.sleep(0.5)
        except Exception:
            pass
    return _with_state(f"{warp_log}挤奶/剪毛：\n" + "\n".join(report_parts))


@mcp.tool()
def close_doors() -> str:
    """🚪 关闭所有动物建筑的门
    晚上调用，防止野生动物袭击牲畜。
    右键切换开关，开门已包含在 care_animals 中。
    """
    try:
        r = api.close_doors()
        if r.get("ok"):
            n = r.get("closed", 0)
            doors = r.get("doors", [])
            detail = ", ".join(f"{d['building']}@({d['door'][0]},{d['door'][1]})" for d in doors)
            return _with_state(f"🚪 已关闭 {n} 扇门: {detail}" if n else "⚠️ 没有动物建筑")
        return _with_state(f"❌ {r.get('error', '关门失败')}")
    except Exception as e:
        return _with_state(f"❌ {e}")


# ═══════════════════════════════════════════
#  产业工具
# ═══════════════════════════════════════════

@mcp.tool()
def harvest_crops(radius: int = 15) -> str:
    """🌾 收割当前场景所有已成熟作物（自动扫描，不用坐标；2026-08-14 改用 scythe_crops）
    ⚠️ 需要背包有镰刀（/harvest 带镰刀才收得了镰刀作物；手摘作物直接进包）。
    radius: 扫描半径（默认15，覆盖周围作物）。

    Args:
        radius: 收获半径
    """
    result = _run_script("scythe_crops", ["--radius", str(radius)], timeout=120)
    return _with_state(f"🌾 收菜完成\n{result[:600]}")


@mcp.tool()
def collect_machines(machine_type: str = "", location: str = "") -> str:
    """⚙️ 批量收集机器产物（全农场一次收完，不走路）
    遍历所有机器，把已完成的产品直接收进背包（返回带当前品质，Cask 用）。
    只收 readyForHarvest 的机器，陈化中的 Cask 不取。

    Args:
        machine_type: 机器类型（Keg / Cask / Preserves Jar…，留空全收）
        location: 限定地点（Cellar / Big Shed…，留空=全农场）
    """
    r = api.machine_collect(location=location, type=machine_type)
    if not r.get("ok"):
        return _with_state(f"❌ 收集失败: {r.get('error', '')}")
    collected = r.get("collected", 0)
    skipped = r.get("skippedFull", 0)
    by_type = r.get("byType") or {}
    lines = [f"⚙️ 机器收集: 收了 {collected} 件"]
    for name, c in sorted(by_type.items()):
        cn = MACHINE_CN.get(name, name)
        lines.append(f"  • {cn} x{c}")
    if skipped:
        lines.append(f"⚠️ 背包满了，跳过 {skipped} 件（先清理背包再收）")
    if not collected and not skipped:
        lines.append("没有待收的机器")
    return _with_state("\n".join(lines))


@mcp.tool()
def load_machines(item: str, machine_type: str = "", location: str = "") -> str:
    """⚙️ 批量往空机器放原料（游戏原生路径：warp过去→选中→interact）
    把背包里的原料装进匹配的空机器，加工时间由游戏自己算（Keg酿酒/Cask陈化）。
    每台机器都真实走过去操作（warp 快速移动），100% 走游戏交互逻辑。

    Args:
        item: 原料英文名（如 Starfruit；Cask 用成品如 Starfruit Wine）
        machine_type: 目标机器类型（Keg / Cask / Preserves Jar…，留空试所有空机器）
        location: 限定地点（Cellar / Big Shed…；**留空=当前场景/建筑**，不跑全农场——恒 2026-08-13）
    """
    if not location:
        # 默认只装当前场景/建筑（AI 在哪装哪，别到处乱串全农场同机器）
        try:
            location = api.state().get("location", {}).get("name", "")
        except Exception:
            location = ""
    args_list = []
    if machine_type:
        args_list += ["--type", machine_type]
    if location:
        args_list += ["--location", location]
    out = _run_script("machine_loader", [item] + args_list, timeout=600, async_ok=True)
    if out.startswith("🚀"):
        return _with_state(out)   # 长脚本自动异步：立即返回 job_id
    return _with_state(f"⚙️ 放置原料 {item}：\n{out[:600]}")


@mcp.tool()
def work_building(location: str, item: str = "", machine_type: str = "") -> str:
    """🏠 一整间屋子收放一轮（进门→收→放，逐台严格交互，拟人走法）
    走到建筑门口开门进去 → 收完该屋所有机器产物 → 把背包原料放进该屋空机器。
    走的是 4 邻+斜对角 8 方向真 checkAction（不是直加作弊）。站过道格一趟处理一圈。
    一屋一轮（单地点），AI 决定去哪些屋子、按什么顺序。

    Args:
        location: 屋子/地点名（Big Shed / Cabin / Cellar / Farm…）
        item: 要放的原料英文名（如 Starfruit；留空=只收不放）
        machine_type: 放原料的机器类型（Keg / Cask…，留空=该屋所有空机器）
    ⚠️ 2026-08-31 恒：作弊直加版 building_round 保留但不再走此路径（后续加作弊模式时再挂回）。
    """
    args_list = ["--location", location]
    if item:
        args_list += ["--fruit", item]
    if machine_type:
        args_list += ["--machine", machine_type]
    out = _run_script("fruit_round", args_list, timeout=1500, async_ok=True)
    if out.startswith("🚀"):
        return _with_state(out)   # 长脚本自动异步：立即返回 job_id
    return _with_state(f"🏠 {location} 收放：\n{out[:600]}")


@mcp.tool()
def machine_report() -> str:
    """⚙️ 全农场机器清点（按类型统计总数 + 按建筑分组待收清单），只报数量不逐台列坐标
    「烘干机 共x台 闲置y 加工z 完成w；…」按类型聚合；「📥 已就绪待收 N台：农舍: 小桶×3 烘干机×1 / 温室: 酿酒桶×5」按建筑分组；「🐟 鱼塘: 鲑鱼子×1」。
    ⚠️ 2026-08-28 恒：1450台机器逐台报坐标会爆 token——不逐台列坐标/持有物，只报建筑+类型+数量；
    真正收机器用 collect_machines / farm collect（内部扫坐标），本工具只给 AI 决策"哪该收"。
    覆盖所有建筑室内 + 温室 + 地窖。随时可查，不受每日首次调用限制。2026-08-31 加鱼塘产出。
    """
    try:
        fr = _fetch_farm_report()
        if not fr.get("ok"):
            return _with_state(f"❌ 获取失败: {fr.get('error', '')}")
        pond_s = _pond_ready_summary(_fetch_fish_ponds())
        ml = (fr.get("machines") or {}).get("machines") or []
        if not ml and not pond_s:
            return _with_state("⚙️ 农场里没有机器/鱼塘产出")
        agg = {}
        for m in ml:
            t = agg.setdefault(m.get("type") or "?", {"total": 0, "idle": 0, "processing": 0, "ready": 0, "byLoc": {}})
            t["total"] += 1
            status = m.get("status")
            key = "idle" if status == "empty" else ("ready" if status == "ready" else "processing")
            t[key] += 1
            loc = m.get("location", "?")
            t["byLoc"][loc] = t["byLoc"].get(loc, 0) + 1
        lines = [f"⚙️ 全农场机器 ({len(ml)} 台):"]
        for name, s in sorted(agg.items()):
            cn = MACHINE_CN.get(name, name)
            locs = ", ".join(f"{k}x{v}" for k, v in s["byLoc"].items())
            lines.append(f"  • {cn}: 共{s['total']}台 闲置{s['idle']} 加工{s['processing']} 完成{s['ready']}  ({locs})")
        ready = [m for m in ml if m.get("status") == "ready"]
        if ready:
            # 📥 就绪清单：按建筑/场景分组只报数量——1450台机器逐台报坐标会爆 token（2026-08-28 恒）。
            #    具体坐标留给 collect_machines / farm collect 内部扫，AI 只需知道"哪、几台、啥"来决策收不收。
            rl = {}
            for m in ready:
                loc = m.get("location", "?")
                t = MACHINE_CN.get(m.get("type", "?"), m.get("type", "?"))
                g = rl.setdefault(loc, {})
                g[t] = g.get(t, 0) + 1
            lines.append(f"📥 已就绪待收 {len(ready)} 台:")
            for loc in sorted(rl):
                parts = [f"{t}×{c}" for t, c in sorted(rl[loc].items())]
                lines.append(f"  • {loc}: {', '.join(parts)}")
        if pond_s:
            lines.append(f"🐟 鱼塘: {pond_s}")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def chop_trees(area: str = "") -> str:
    """🪓 砍树 — 先砍树干再挖树桩

    Args:
        area: 区域（留空则自动找附近的树）
    """
    args_list = area.split() if area else []
    out = _run_script("chop_trees", args_list, timeout=120)
    return _with_state(f"🪓 砍树：\n{out[:600]}")


@mcp.tool()
def buy_animal(animal_type: str, name: str, building: str = "") -> str:
    """🐔 从玛妮那里购买动物 — 直接入住建筑，无需UI操作
    支持所有常见家畜家禽，自动找有空位的建筑。
    送货上门，即买即用。

    Args:
        animal_type: 动物类型 — White Chicken | Brown Chicken | Duck | Rabbit |
                      Cow | Goat | Sheep | Pig | Ostrich | Golden Chicken
        name: 给动物取的名字
        building: 建筑名称（可选，不填自动选有空位的匹配建筑）
    """
    try:
        r = api.buy_animal(animal_type, name, building)
        if r.get("ok"):
            return _with_state(
                f"🐔 购买成功！\n"
                f"   种类: {r['animal_type']}\n"
                f"   名字: {r['name']}\n"
                f"   💰 花费: {r['price']}g\n"
                f"   🏠 入住: {r.get('building_name', r['building'])}\n"
                f"   🐄 当前: {r['total_animals']}/{r['animal_limit']} 只"
            )
        else:
            return _with_state(f"❌ 购买失败: {r.get('error', '未知错误')}")
    except Exception as e:
        return _with_state(f"❌ 购买出错: {e}")


@mcp.tool()
def go_mining(
    mode: str = "rush",
    start: int = 1,
    target: Optional[int] = None,
    ore: Optional[str] = None,
    cycles: int = 5,
    hp_threshold: int = 50,
    food_sta: Optional[str] = None,
    food_hp: Optional[str] = None,
    resume: bool = True,
) -> str:
    """⛏️ 去矿井挖矿（双模式）
    冲层模式(rush)：从进度恢复（或指定层），一路敲石头找梯子下到目标层
    刷矿模式(farm)：在特定层反复刷指定矿石

    自动检测镐子级别算好敲击次数，不浪费体力。
    附近有怪物自动切剑砍。
    背包有食物会自动吃。
    自动记录已到达最深层，下次可从中断处继续。

    调用前请用 check_status 或 peek_player 检查背包：
    - 确保带了镐子和剑
    - 确保有足够空格子装矿石（至少留 10 格）
    - 如有食物（沙拉/奶酪/鱼等）可传 food_sta/food_hp，状态低会自动吃
    - 如果背包满了，先用 chest_store 存到箱子再出发
    💡 占位物技巧（出发前，恒2026-08-23）：可提前往背包放 1 个可堆叠占位物——铱矿/铱锭/五彩碎片——
    背包满时其实已含该类，后续同种战利品会自动**堆叠吸附**进去、少触发满包停。
    ⚠️ 别拿**银河之魂**这类带出去死了丢了划不来的稀有物当占位；用铱矿这类死了不心疼的。

    Args:
        mode: 模式（rush=冲层, farm=刷矿，默认 rush）
        start: 起始层数，仅 rush 模式（默认 1，开了 resume 则被进度覆盖）
        target: 目标层数，仅 rush 模式
        ore: 目标矿石，仅 farm 模式（Copper/Iron/Gold，默认 Iron）
        cycles: 刷矿循环次数，仅 farm 模式（默认 5）
        hp_threshold: 血量低于此 % 吃食物/撤退（默认 50%）
        food_sta: 体力食物名称（如 Salad / Bread），不传就不吃
        food_hp: 回血食物名称（如 Cheese / Fish Taco），不传则共用 food_sta
        resume: 是否从已到达最深层恢复（默认 True，仅 rush 模式）
    """
    args_list = [
        f"--mode", mode,
        f"--hp-threshold", str(hp_threshold),
    ]

    if mode == "rush":
        args_list.extend(["--start", str(start)])
        if target is not None:
            args_list.extend(["--target", str(target)])
        else:
            args_list.extend(["--target", str(120)])
        if not resume:
            args_list.append("--no-resume")
        if food_sta:
            args_list.extend(["--food-sta", food_sta])
        if food_hp:
            args_list.extend(["--food-hp", food_hp])
    else:
        args_list.extend(["--ore", ore or "Iron"])
        args_list.extend(["--cycles", str(cycles)])
        if food_sta:
            args_list.extend(["--food-sta", food_sta])
        if food_hp:
            args_list.extend(["--food-hp", food_hp])

    # 先看进度
    progress_out = _run_script("mine_run", ["--check-progress"], timeout=10)
    out = _run_script("mine_run", args_list, timeout=600, async_ok=True)
    if out.startswith("🚀"):
        return _with_state(out)   # 长脚本自动异步：立即返回 job_id
    return _with_state(f"⛏️ 挖矿报告：\n{progress_out}\n{out[:700]}")


@mcp.tool()
def _ai_port() -> int:
    """AI 角色端口（NAGI_URL，默认 7843）。
    ⚠️ 2026-08-14：fish_run 等脚本用 raw urllib 不读 NAGI_URL，默认 7842（host）。
    脚本类工具调用时必须显式传 --port=_ai_port()，否则会去操作 host 的角色。"""
    try:
        return int(os.environ.get("NAGI_URL", "http://localhost:7843").rsplit(":", 1)[-1])
    except Exception:
        return 7843


def _fmt_rod(rod: dict) -> str:
    """把 /state 的 player.rod 快照格式化成人话。"""
    if not rod:
        return "没有鱼竿（背包/手上都不存在）"
    name = rod.get("name") or "鱼竿"
    parts = [f"{name}（{'手持' if rod.get('inHand') else '背包'}）"]
    bait = rod.get("bait")
    if bait:
        parts.append(f"饵={bait}×{rod.get('baitStack') or 0}")
    else:
        parts.append("饵=无")
    tk = rod.get("tackle") or []
    if tk:
        parts.append("钓具=" + "、".join(f"{t.get('name')}({t.get('uses', 0)}/{t.get('max', 20)})" for t in tk))
    else:
        parts.append("钓具=无")
    parts.append(f"背包饵×{rod.get('baitInBag') or 0}")
    parts.append(f"饵槽{'有' if rod.get('canBait') else '无'}/钓具槽{'有' if rod.get('canTackle') else '无'}")
    return "🎣 " + "；".join(parts)


def _rod_cmd(action: str = "show", item: str = "") -> str:
    """🎣 鱼竿：看状态 / 上鱼饵 / 上钓具 / 摘附件。
    action: show(读竿状态) | bait(上鱼饵 item=名) | tackle(上钓具 item=名) | clear(摘第一个附件回背包)
    item: 物品名（可空；空=自动挑背包里第一个同类）。"""
    st = api.state()
    rod = (st.get("player") or {}).get("rod")
    if action in ("show", ""):
        return _fmt_rod(rod)
    if not rod:
        return "❌ 没有鱼竿——先去 Willy 鱼店买/升级一根再说"
    if action not in ("bait", "tackle", "clear"):
        return "❌ 未知 action，用 show/bait/tackle/clear"
    r = api._post("/rod", {"action": action, "item": item or ""})
    if not r.get("ok"):
        return f"❌ {r.get('error', '上饵/摘失败')}"
    ri = r.get("rodInfo") or (api.state().get("player") or {}).get("rod")
    what = r.get("equipped") or (r.get("action") or "")
    return f"✅ {what}\n{_fmt_rod(ri)}"


def go_fishing(
    location: Optional[str] = None,
    max_casts: int = 0,
    no_sleep: bool = True,
) -> str:
    """🎣 钓鱼（AI 角色）
    默认【就地钓】：就在 AI 当前站位原地钓（不传送）——开 Fishbot 自己找水抛；
    开局一次性用 isFishing(等待咬钩) 判定能否抛：5s 内建立就开钓（水域固定，能抛一杆就能抛很多竿）；没建立(没水/死点)就收手。
    指定 location → 自动 warp 到该校准钓点再钓。开 Fishbot 自动钓鱼 → 抛够竿数/体力不足收杆。

    Args:
        location: None=就地钓（当前站位，须 AI 自己站到水边）；指定（Beach / Mountain / Forest / Town）=自动去钓点
        max_casts: 抛 N 竿就收手（0=不限，钓到体力<20/背包满/太晚/抛不出去收杆）
        no_sleep: True=钓完不睡觉（留在原地）；False=钓完回家睡。
                  ⚠️ 2026-08-15 改默认 True：睡觉由 AI 用 go_sleep 统一控制（白天钓鱼别早睡）。
    """
    args_list = ["--port", str(_ai_port()), "--max-casts", str(max_casts)]
    if location:
        args_list.extend(["--location", location])
    if no_sleep:
        args_list.append("--no-sleep")

    out = _run_script("fish_run", args_list, timeout=180, async_ok=True)
    if out.startswith("🚀"):
        return _with_state(out)   # 长脚本自动异步：立即返回 job_id，script_status 查进度
    return _with_state(f"🎣 钓鱼报告：\n{out[:800]}")


def _bobber_menu_image(icons):
    """截图浮漂样式菜单 + 用 PIL 把每个样式的编号标在图标上方 → 返回 Image 给 AI 看图挑。
    编号和图标的 1:1 对应 /menu 的 icons 坐标；菜单保持开着，AI 看完调 bobber_style("编号") 选中。"""
    try:
        r = api.screenshot_ai()
        if not r.get("ok"):
            return _with_state(f"❌ 截图失败: {r.get('error', '?')}")
        data = base64.b64decode(r["image"])
        img = _PILImage.open(io.BytesIO(data)).convert("RGB")
        from PIL import ImageDraw
        d = ImageDraw.Draw(img)
        for b in icons:
            name = b["name"]
            x, y = b["x"], b["y"]
            # 图标上方画编号（黄字黑描边，压过 UI 也看得清）
            d.text((x - 8, y - 30), name, fill=(255, 255, 0),
                   stroke_width=2, stroke_fill=(0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        print("[bobber_style] see 模式：菜单图上每个图标标了编号，AI 看图后调 bobber_style(\"编号\") 选中", flush=True)
        return Image(data=buf.getvalue(), format="png")
    except Exception as e:
        return _with_state(f"❌ 生成菜单图失败: {e}")


@mcp.tool()
def bobber_style(style: str = "dice") -> str:
    """🎣 浮漂样式机（威利鱼店）选浮漂样式
    骰子 = 每次抛竿随机浮漂（实测生效）。也能选特定样式编号。
    机器在 FishShop(10,4)：交互 → ChooseFromIconsMenu 图标网格 → 点样式 → 关闭。
    style:
      "dice"/"random"/"骰子"(默认, 即最后那个 -2 图标) = 随机浮漂
      "see"/"看" = 截图菜单并给每个样式标上编号 → 返回图片给你看图挑，看完再调 bobber_style("编号")
      数字编号(0-37) = 选该样式
      "list" = 纯文字列编号
    注意：样式存到当前装备的鱼竿上，工具会先自动装备 Iridium Rod。
    """
    try:
        # 1. 关掉可能开着的菜单
        api._post("/menu_close")
        time.sleep(0.3)
        # 2. 装备鱼竿（样式存当前鱼竿）
        st = api.state()
        rods = [it for it in (st.get("inventory") or []) if "rod" in (it.get("name") or "").lower()]
        rod_name = rods[0]["name"] if rods else None
        if rod_name:
            api._post("/tool", {"name": rod_name})
            time.sleep(0.3)
        # 3. 传到机器前（FishShop 10,5 面朝上 10,4 是机器）
        r = api.warp_into("FishShop", 10, 5)
        if not r.get("ok"):
            return _with_state(f"❌ 进不了鱼店: {r.get('error', r)}")
        time.sleep(0.8)
        api._post("/face", {"direction": 0})
        time.sleep(0.3)
        api._post("/interact", {"x": 10, "y": 4})
        time.sleep(0.8)
        m = api._get("/menu")
        icons = [b for b in (m.get("buttons") or []) if b.get("field") == "icons"]
        if not m.get("open") or not icons:
            return _with_state("❌ 没弹出浮漂样式菜单（机器交互失败）")
        # 4. 目标
        s = (style or "dice").strip().lower()
        if s in ("see", "看", "看看", "预览", "select"):
            return _bobber_menu_image(icons)
        if s in ("list", "查看"):
            names = " ".join(b["name"] for b in icons)
            return _with_state(f"🎣 浮漂样式机：共 {len(icons)} 个图标\n  编号: {names}\n  最后 -2 = 骰子(随机)\n  想看长啥样：bobber_style(\"see\") 返回菜单截图")
        target = None
        if s in ("dice", "random", "骰子", "随机", "-2"):
            target = next((b for b in icons if b["name"] == "-2"), None)
            label = "骰子(随机浮漂)"
        else:
            target = next((b for b in icons if b["name"] == s), None)
            label = f"样式 {s}"
        if target is None:
            return _with_state(f"❌ 找不到样式「{style}」（bobber_style(\"see\") 看图挑编号）")
        # 5. 点样式 + 关菜单
        api._post("/menu/click", {"x": target["x"], "y": target["y"]})
        time.sleep(0.5)
        api._post("/menu_close")
        time.sleep(0.3)
        who = f"给 {rod_name} " if rod_name else ""
        return _with_state(f"🎣 {who}选了 {label}（下次抛竿生效；骰子=每次随机浮漂）")
    except Exception as e:
        return _with_state(f"❌ 操作失败: {e}")


@mcp.tool()
def check_mine_progress() -> str:
    """📋 查看矿井进度
    返回已到达的最深层数和建议起始层。
    """
    out = _run_script("mine_run", ["--check-progress"], timeout=10)
    return _with_state(f"📋 矿井进度：\n{out}")


# ═══════════════════════════════════════════════
#  💣 炸矿（手动模式）— 把自动炸矿拆成单步动作，AI 一步步实操
#  ═══════════════════════════════════════════════

def _bomb_bot():
    """按服务器 env 建 BombMiner（炸矿=NAGI_URL 指向的角色自主活动）"""
    import os
    from bomb_common import BombMiner
    def _port(url, default):
        try:
            return int(url.rsplit(":", 1)[-1])
        except Exception:
            return default
    port = _port(os.environ.get("NAGI_URL", "http://localhost:7843"), 7843)
    hport = _port(os.environ.get("NAGI_HOST_URL", "http://localhost:7842"), 7842)
    return BombMiner(port=port, host_port=hport)


@mcp.tool()
def bomb_status() -> str:
    """💣 炸矿状态速报：当前矿井层 + 炸弹库存 + 背包空格 + 本层可炸岩体数
    手动炸矿前先调这个确认装备（炸弹≥几颗、有空格装矿石）。
    """
    from bomb_common import BOMB_RADIUS, log
    bot = _bomb_bot()
    try:
        bot.detect_weapon()
        loc = bot.my_location()
        level = bot.my_mine_level()
        bombs = {bt: bot.count_bombs(bt) for bt in BOMB_RADIUS}
        bombs_str = " ".join(f"{k}×{v}" for k, v in bombs.items() if v > 0) or "无"
        free = bot.inventory_free_slots()
        rocks, occupied, _ = bot.scan_rocks(14)
        lines = [
            f"📍 位置: {loc} (第{level}层)" if level else f"📍 位置: {loc}（不在矿洞）",
            f"💣 炸弹: {bombs_str}",
            f"🎒 背包: {free} 格空",
            f"⚔️ 武器: {bot.weapon_name or '无（镐子防身）'}",
        ]
        if level:
            lines.append(f"🪨 本层可炸岩体: {len(rocks)} 块")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 读炸矿状态失败: {e}")


@mcp.tool()
def bomb_plan(radius: int = 14, min_covered: int = 3, top: int = 3) -> str:
    """💣 炸矿规划：扫本层岩体，按贪心算出最值得放炸弹的锚点（覆盖最多岩体的空格）
    手动模式用——AI 看完规划决定放哪。

    Args:
        radius: 扫描半径（默认14格）
        min_covered: 至少覆盖N块岩体才算值得炸（默认3）
        top: 列出前几个候选锚点（默认3）
    """
    from bomb_common import BOMB_RADIUS
    bot = _bomb_bot()
    try:
        if not bot.my_mine_level():
            return _with_state("❌ 不在矿洞（UndergroundMine）里，先 go_to 矿井")
        bomb_radius = BOMB_RADIUS.get(bot.bomb_type, 3)
        rocks, occupied, center = bot.scan_rocks(radius)
        if not rocks:
            return _with_state("🪨 本层没岩体了，找梯子下楼吧")
        # 全量贪心排序
        eff = bomb_radius + 1
        cx, cy = center
        cands = []
        for ax in range(cx - radius, cx + radius + 1):
            for ay in range(cy - radius, cy + radius + 1):
                if (ax, ay) in occupied:
                    continue
                count = sum(1 for x, y, n in rocks if abs(x - ax) + abs(y - ay) <= eff)
                if count >= min_covered:
                    cands.append((count, -(abs(ax - cx) + abs(ay - cy)), ax, ay))
        cands.sort(reverse=True)
        lines = [f"🪨 本层 {len(rocks)} 块岩体，炸弹半径{bomb_radius}，候选锚点:"]
        for count, negd, ax, ay in cands[:top]:
            lines.append(f"  · ({ax},{ay}) 覆盖 {count} 块")
        if not cands:
            lines.append("  没有达到覆盖阈值的锚点，换个位置或下楼")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 规划失败: {e}")


@mcp.tool()
def bomb_place(x: int, y: int) -> str:
    """💣 放一颗炸弹到指定格（自动站旁边→面向→放→躲远→等爆炸）
    手动模式单步：AI 用 bomb_plan 算好坐标后调用。
    放完自动捡本层掉落。

    Args:
        x: 目标格 x
        y: 目标格 y
    """
    bot = _bomb_bot()
    try:
        if bot.count_bombs() <= 0:
            return _with_state(f"❌ 没有 {bot.bomb_type} 了（用 /give 或先去买）")
        ok, msg = bot.bomb_and_collect(x, y, collect=True)
        bombs_left = bot.count_all_bombs()
        return _with_state(f"💣 {msg}\n剩余炸弹: {bombs_left}")
    except Exception as e:
        return _with_state(f"❌ 放炸弹失败: {e}")


@mcp.tool()
def bomb_collect(max_items: int = 12) -> str:
    """🎁 捡当前地图地上掉落（按价值优先）
    炸完矿捡漏用，自动挑五彩碎片/钻石等高价值优先。

    Args:
        max_items: 一次最多捡几个（默认12）
    """
    bot = _bomb_bot()
    try:
        picked = bot.collect_drops(max_items=max_items)
        return _with_state(f"🎁 捡了 {picked} 个掉落")
    except Exception as e:
        return _with_state(f"❌ 拾取失败: {e}")


@mcp.tool()
def bomb_ladder() -> str:
    """🪜 找梯子并下楼（手动模式单步）
    有梯子就直接传上去 confirm 下楼，没有则报告没梯子。
    """
    bot = _bomb_bot()
    try:
        if not bot.my_mine_level():
            return _with_state("❌ 不在矿洞")
        ladder = bot.find_ladder()
        if not ladder:
            return _with_state("🪜 本层还没有梯子——继续炸石头，炸出梯子再下楼")
        ok = bot.descend()
        if ok:
            return _with_state(f"🪜 下到第 {bot.my_mine_level()} 层")
        return _with_state(f"🪜 有梯子 ({ladder[0]},{ladder[1]}) 但下楼失败，可再试")
    except Exception as e:
        return _with_state(f"❌ 下楼失败: {e}")


@mcp.tool()
def bomb_retreat() -> str:
    """🏳️ 炸矿撤退：回矿井口（Mountain 54,5）——血低/没炸弹/该回家了就撤。
    2026-08-22 恒：若后台正在跑 炸矿/协同(bomb_mine/escort/volcano)，先停掉再撤——即"结束协同+脱离矿井"。
    """
    _stopped = False
    try:
        with _bg_lock:
            for j in list(_bg_jobs.values()):
                if j.running and getattr(j, "name", "") in ("bomb_mine", "bomb_escort", "bomb_volcano"):
                    try:
                        _bg_kill(j)
                        _stopped = True
                    except Exception:
                        pass
    except Exception:
        pass
    bot = _bomb_bot()
    try:
        bot.retreat_to_entrance("手动撤退")
        msg = "🏳️ 已撤到矿井口（并停掉炸矿/协同脚本）" if _stopped else "🏳️ 已撤到矿井口"
        return _with_state(msg)
    except Exception as e:
        return _with_state(f"❌ 撤退失败: {e}")


@mcp.tool()
def bomb_mine(target: int = 0, bomb: str = "Bomb", min_covered: int = 3,
              follow_host: bool = True, lead: int = 2, autodrop: int = 0,
              one_floor: bool = False) -> str:
    """💣 自主炸矿（贪心炸弹下矿）
    每层贪心找覆盖最多岩体的点放炸弹，生存优先（血低吃/撤、没炸弹撤、卡死检测）。
    user 在矿里就一起冲层（目标层=user 层数±lead），user 同层打架就 position 增援只打 user 的对手。
    one_floor=True：逐层模式，只跑一层返回结构化摘要（含背包/附近掉落），不撤退；
    AI 看摘要再调用继续下一层。⚠️ 逐层整理已禁（恒 2026-08-23：竖井一跳3~15层，逐层等AI响应太慢易暴毙）
    ——organize_suggested 恒 False，摘要只为可见性不定决策；清包交给异步后台（冲层模式）。
    ⚠️ 逐层模式=同步（等摘要），冲层模式=自动异步（后台跑）；推荐用冲层模式（异步后台整理）。
    💡 占位物技巧（出发前，恒2026-08-23）：可提前往背包放 1 个可堆叠占位物——铱矿/铱锭/五彩碎片——
    背包满时其实已含该类，后续同种战利品（开箱/拾取）会自动**堆叠吸附**进去、少触发满包停。
    ⚠️ 别拿**银河之魂**这类带出去死了丢了划不来的稀有物当占位；用铱矿这类死了不心疼的。

    Args:
        target: 目标层（0=按当前层自适应：在头骨≥121→500、城镇→120；头骨矿洞也算 UndergroundMine121+）
        bomb: 炸弹类型 Bomb/Mega Bomb/Cherry Bomb（默认 Bomb）
        min_covered: 至少覆盖N块岩体才炸（默认3）
        follow_host: user 在矿里就一起冲层/增援（默认 True）
        lead: 和 user 保持的层差（默认2）
        autodrop: 自动丢物（已退役），0=只规划不丢交AI手动整理（默认0）
        one_floor: 逐层模式，跑一层返回摘要不撤退（默认 False）
    """
    args_list = [f"--target", str(target), f"--bomb", bomb,
                 f"--min-covered", str(min_covered)]
    if not follow_host:
        args_list.extend(["--follow-host", "0"])
    if lead != 2:
        args_list.extend(["--lead", str(lead)])
    if autodrop != 0:
        args_list.extend(["--autodrop", str(autodrop)])
    if one_floor:
        args_list.append("--one-floor")
    progress_out = _run_script("bomb_mine", ["--check-progress"], timeout=10)
    # 逐层模式(one_floor)是快速单层摘要，保持同步看结果；冲层是长任务→自动异步
    out = _run_script("bomb_mine", args_list, timeout=600, tail=3000, async_ok=not one_floor)
    if out.startswith("🚀"):
        return _with_state(out)   # 长脚本自动异步：立即返回 job_id
    if one_floor:
        import re as _re
        m = _re.search(r'===BOMB_SUMMARY===(.*?)===END===', out, _re.S)
        if m:
            return _with_state(f"💣 炸矿摘要（目标{target}层，本层完成）：\n{m.group(1).strip()}")
        return _with_state(f"💣 炸矿逐层（未拿到摘要）：\n{out[:700]}")
    return _with_state(f"💣 炸矿报告：\n{progress_out}\n{out[:700]}")


@mcp.tool()
def bomb_organize(disable: bool = False, reset: bool = False) -> str:
    """💼 整理背包状态控制（⚠️ 逐层整理已禁 2026-08-23，此工具基本不主动用；如需手动重置计数可调）
    仅在逐层模式摘要里 organize_suggested=true（已禁，恒 False）时 AI 才需配合：
    - disable=True：判定后续都不需要腾格（重要的都在格子里）→ 后续不再提示整理
    - reset=True：刚整理完背包 → 重置间隔计数，下次按地点频率再提示
    """
    args = []
    if disable:
        args.append("--organize-disable")
    if reset:
        args.append("--organize-reset")
    out = _run_script("bomb_mine", args, timeout=10)
    return _with_state(f"💼 {out[:300]}")


@mcp.tool()
def bomb_escort(ore_radius: int = 7, cooldown: int = 20, max_minutes: Optional[int] = None,
                hp_threshold: int = 50) -> str:
    """👥 协同模式：跟着 user 下矿炸矿（贴身保镖）
    滞后跟随 user（站身后不贴脸），只在途径处看到高价值矿（铱/宝石/金）才放炸弹，
    帮打怪（user 附近出现怪物就砍）。user 离开矿井就撤，没炸弹就转纯保镖跟随。

    Args:
        ore_radius: 高价值矿离 user 多近才炸（默认7格）
        cooldown: 两次炸弹最小间隔秒数（默认20）
        max_minutes: 最多跟随分钟数（默认不限）
        hp_threshold: 血量低于此%撤退（默认50）
    """
    args_list = [f"--ore-radius", str(ore_radius), f"--cooldown", str(cooldown),
                 f"--hp-threshold", str(hp_threshold)]
    if max_minutes:
        args_list.extend(["--max-minutes", str(max_minutes)])
    out = _run_script("bomb_escort", args_list, timeout=1200, async_ok=True)
    if out.startswith("🚀"):
        return _with_state(out)   # 长脚本自动异步：立即返回 job_id
    return _with_state(f"👥 协同报告：\n{out[:800]}")


@mcp.tool()
def bomb_volcano(bomb: str = "Bomb", min_covered: int = 3, hp_threshold: int = 30,
                 max_minutes: Optional[int] = None, poll: float = 2.5) -> str:
    """🌋 火山骑行炸矿（跟 user 换层）——**火山适配，需 user 陪同**
    ⚠️ 火山特殊瓦片无法程序化换层 → 要求 user(host) 已在矿井/火山里才放行（否则拦下请先 ask user 陪同）。
    跟在 user 身边（warp 换层跟上），同层清矿簇（贪心炸弹）、帮打怪。
    💡 占位物技巧（出发前，恒2026-08-23）：可提前往背包放 1 个可堆叠占位物——铱矿/铱锭/五彩碎片——
    背包满时其实已含该类，后续同种战利品会自动**堆叠吸附**进去、少触发满包停。
    ⚠️ 别拿**银河之魂**这类带出去死了丢了划不来的稀有物当占位；用铱矿这类死了不心疼的。
    Args:
        bomb: Bomb/Mega Bomb/Cherry Bomb（默认 Bomb）
        min_covered: 至少覆盖N块岩体才炸（火山簇小，默认3）
        hp_threshold: 血量低于此%撤退（默认30）
        max_minutes: 最多跟随分钟数（默认不限）
        poll: user位置轮询间隔秒（默认2.5）
    """
    try:
        vg = _volcano_gate()
        if vg:
            return _with_state(vg)
        args_list = [f"--bomb", bomb, f"--min-covered", str(min_covered),
                     f"--hp-threshold", str(hp_threshold)]
        if max_minutes:
            args_list.extend(["--max-minutes", str(max_minutes)])
        if poll != 2.5:
            args_list.extend(["--poll", str(poll)])
        out = _run_script("bomb_volcano", args_list, timeout=1200, async_ok=True)
        if out.startswith("🚀"):
            return _with_state(out)   # 长脚本自动异步：立即返回 job_id
        return _with_state(f"🌋 火山炸矿报告：\n{out[:800]}")
    except Exception as e:
        return _with_state(f"❌ 火山炸矿失败: {e}")


@mcp.tool()
def pickup_scene(max_items: int = 30) -> str:
    """🎁 捡当前场景可拾取地面物品 + 收成熟大葱
    畜棚/鸡舍收鸡蛋鸭毛羊毛松露、采集物、丢地上的东西。
    蛋蛋/野菜用捡拾动作（走过去→面朝→interact）；掉落 debris 走过去自动收。
    🌱 成熟大葱（forageCrop="1" harvestable）也摘（interact，不用锄头）——2026-08-17 恒。
    黑名单排除箱子/洒水器/机器/鸡舍家具等。

    Args:
        max_items: 一次最多捡几个（默认30）
    """
    out = _run_script("pickup_scene", [f"--max", str(max_items)], timeout=120)
    return _with_state(f"🎁 拾取报告：\n{out[:600]}")


@mcp.tool()
def rock_dig(dig: bool = True, radius: int = 14, max_break: int = 0, break_stone: bool = False) -> str:
    """⛏️ 室外镐击当前图可破物(采石场/姜岛挖掘场/南滩蚌矿, scene 域, 2026-08-29 恒)
    自动扫 surroundings 找可破节点(骨节/黏土/蚌矿/矿点/宝石/煤矿/放射矿)→走过去镐敲碎→拾掉落。
    ⚠️ 只跳过普通石头;宝石/放射矿全认(dump_tile 真名, 2026-08-29 恒拍板不猜)。挖蚯蚓点/斑点用锄头(spot op,不归这)。
    一图敲完自动换下一批;默认真敲,dig=false 只扫(报可破+objId)。

    用法: scene ops=rock [dig=true/false] [radius=14] [max_break=0] [break_stone]
    """
    args_list = ["--radius", str(radius)]
    if dig:
        args_list.append("--dig")
    if max_break:
        args_list += ["--max", str(max_break)]
    if break_stone:
        args_list.append("--break-stone")
    out = _run_script("rock_run", args_list, timeout=600)
    return _with_state(f"⛏️ 室外镐击报告：\n{out[:900]}")


@mcp.tool()
def berry_run() -> str:
    """🍓 摇当前场景所有结果的浆果灌木（树莓/黑莓季节，scene 域）
    自动：扫 surroundings 找 bushBloom=True 的灌木 → 逐棵走过去+面朝+interact 摇 →
    循环到采集完所有场景树莓。掉落物吸附直接进背包。
    摇完灌木变无果（tileSheetOffset 1→0），不会重复摇。

    注意：只有季节结果的灌木能摇（如 Backwoods 11 棵灌木里 3 棵有果）。
    用法：scene ops=berry / 摇树莓
    """
    out = _run_script("berry_run", timeout=120)
    return _with_state(f"🍓 摇树莓报告：\n{out[:600]}")


@mcp.tool()
def spot_run() -> str:
    """🪱 挖当前场景所有斑点 + 姜点（scene 域）
    自动：扫 surroundings 找 (O)590 蚯蚓点 + (O)SeedSpot 远古斑点 + forageCrop="2" 姜点 →
    检查锄头 → 逐格复用耕地 tool_area till（脚本自己算坐标，不靠 AI 报）→ 循环到挖完。
    蚯蚓点出古物/矿物/种子；远古斑点出季节作物种子；姜点出姜。掉落吸附进包。

    注意：没带锄头不挖（状态注入也不报）。用法：scene ops=spot / 挖斑点 / 挖蚯蚓
    """
    out = _run_script("spot_run", timeout=120)
    return _with_state(f"🪱 挖斑点/姜报告：\n{out[:600]}")


@mcp.tool()
def moss_run(radius: int = 25, target_max: int = 80, rounds: int = 5, dry_run: bool = False) -> str:
    """🌿 搜刮当前地图苔藓（绿雨天专用，2026-08-21 恒）
    自动：自然走(/walk_to，失败自动 /position 精确定位兜底)到苔藓目标 → 按类型采集——
    苔雨树斧头砍、长苔藓树镰刀/剑刮一下(必掉 Moss)、苔藓杂草块(大块 Clump:46 挥3下 / 小块挥2下)直到面前格没了。
    镰刀/剑是范围攻击会打周边 → 每处理完一个目标自动重扫 /surroundings。先刮草后砍树。

    Args:
        radius: 扫描半径（默认25，小图覆盖整图）
        target_max: 目标上限（默认80；想快测几格就传小）
        rounds: 连续无新目标即停的轮数上限（默认5）
        dry_run: 只扫不采集（报有多少苔藓目标）
    """
    try:
        _w = api.state().get("time", {}).get("weather")
    except Exception:
        _w = None
    if _w != 7 and not _moss_cfg.get("expose_all_days", False):
        return _with_state("🌿 今天不是绿雨天，且未开「平时也暴露苔藓」(settings moss on)——不搜刮。需要时 settings moss on")
    args_list = ["--radius", str(radius), "--max", str(target_max), "--rounds", str(rounds)]
    if dry_run:
        args_list.append("--dry-run")
    out = _run_script("moss_run", args_list, timeout=600)
    return _with_state(f"🌿 苔藓搜刮报告：\n{out[:800]}")


def trash_run(loc: str = "", pos: str = "", wait: float = 1.0, dry_run: bool = False) -> str:
    """🗑️ 翻垃圾桶刮刮乐（scene 域，2026-08-24 恒拍板傻瓜式）
    垃圾桶=地图瓦片 Action="Garbage <id>"（不是 loc.objects，/surroundings、/dump_tile 都看不到它），
    翻 = 对桶瓦片 checkAction → performAction "Garbage <id>" → CheckGarbage；现有 /interact?x=&y= 直接就是
    （源码 loc.checkAction(瓦片)），无需改 C#（2026-08-24 实测 interact 52,63 → actionTriggered:true 真翻了）。
    每天每桶 1 次（CheckedGarbage），掉物= dailyLuck+每桶确定性RNG，空翻正常；钓技 Salvager 拾荒者 perk 强化战利品。
    流程：逐桶 /position 到桶旁可站格 → /interact 桶瓦片 → 停 wait 秒等掉落物飞进包 → 报掉物。

    Args:
        loc: 只翻该场景的桶（默认翻预设全部）
        pos: 临时桶坐标 "场景:x,y|场景:x,y"（追加，不进预设）
        wait: 翻后停留秒（等掉落物进包，默认1.0）
        dry_run: 只报桶不翻
    """
    args_list = []
    if loc:
        args_list += ["--loc", str(loc)]
    if pos:
        args_list += ["--pos", str(pos)]
    args_list += ["--wait", str(wait)]
    if dry_run:
        args_list.append("--dry-run")
    out = _run_script("trash_run", args_list, timeout=300)
    return _with_state(f"🗑️ 翻垃圾桶报告：\n{out[:800]}")


@mcp.tool()
def blessing_statue() -> str:
    """🗿 摸农场祝福雕像/矮人雕像（下矿前做）
    找到当前场景的雕像→走过去→交互。祝福雕像每天给随机祝福；
    如果弹选项就选（优先免疫炸弹/防御类）。结果看 /state 的 buff。

    用法：下矿前先摸，拿到好祝福再放心炸矿。
    """
    out = _run_script("blessing_statue", [], timeout=60)
    return _with_state(f"🗿 雕像报告：\n{out[:500]}")


@mcp.tool()
def pet_walk(include_petted: bool = False) -> str:
    """🐾 拟人化摸动物：走过去→面朝→interact（和捡蛋蛋同一套操作）
    动物会动，动过就 /position 精确定位到它旁边。摸当前场景所有没摸的动物。
    区别于作弊 /petall——这是真走过去摸。
    ⏰ 动物作息：一早醒来（6点）就能摸，傍晚6点后睡觉摸不了——最好早上摸。

    Args:
        include_petted: 连已摸的也重新摸（默认 False）
    """
    args_list = []
    if include_petted:
        args_list.append("--include-petted")
    out = _run_script("pet_walk", args_list, timeout=180)
    return _with_state(f"🐾 摸动物报告：\n{out[:600]}")


@mcp.tool()
def scythe_crops(radius: int = 15) -> str:
    """🌾 镰刀收获模式：选最好镰刀 → /harvest 收当前地图成熟作物 → 捡地面掉落
    手摘作物（蒜/西瓜）直接进背包；镰刀作物（小麦/水稻/芋头/纤维）掉落地上要走过去捡。
    会按作物种类报名字（小麦/杨桃/菠萝…）。

    Args:
        radius: 收获半径（默认15）
    """
    out = _run_script("scythe_crops", [f"--radius", str(radius)], timeout=180)
    return _with_state(f"🌾 镰刀收获报告：\n{out[:600]}")


@mcp.tool()
def feed_hay(dry_run: bool = False) -> str:
    """🌾 加干草：从筒仓抽干草铺到当前场景的饲料槽
    先查筒仓干草（空则提示先割草/买干草），再挨个对饲料槽交互。
    注意：动物 7 点后睡觉，喂食白天做。

    Args:
        dry_run: 只报筒仓/饲料槽状态，不动（默认 False）
    """
    args_list = []
    if dry_run:
        args_list.append("--dry-run")
    out = _run_script("feed_hay", args_list, timeout=120)
    return _with_state(f"🌾 加干草报告：\n{out[:600]}")


@mcp.tool()
def silo_status() -> str:
    """🌾 筒仓干草检测：筒仓数/已存干草/容量/空余
    喂动物/买干草前先看，别让动物饿着。

    （需 NagiBridge 模组重编译到最新版）
    """
    try:
        r = api.silo()
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', 'silo 端点不可用')}（模组需重编译）")
        if r.get("noSilo"):
            return _with_state("🌾 还没建筒仓——干草没地方存，建议先买一个")
        full = "⚠️ 满了！" if r.get("full") else "✅ 还有空间"
        return _with_state(
            f"🌾 筒仓×{r.get('silos')} | 干草 {r.get('hay')}/{r.get('capacity')} | 空余 {r.get('room')} {full}")
    except Exception as e:
        return _with_state(f"❌ 读筒仓失败: {e}")


@mcp.tool()
def mastery_status() -> str:
    """🏆 精通状态：精通经验 + 已领取精通（反射读 Farmer 所有 Mastery 字段）
    武器/钓鱼等技能满级后攒的精通经验检测用。

    （需 NagiBridge 模组重编译到最新版）
    """
    try:
        r = api.mastery()
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', 'mastery 端点不可用')}（模组需重编译）")
        lines = ["🏆 精通状态："]
        for item in (r.get("fields") or []) + (r.get("props") or []):
            name = item.get("name", "?")
            val = item.get("value", "")
            if name and val:
                lines.append(f"  · {name} = {val}")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 读精通失败: {e}")


@mcp.tool()
def building_list() -> str:
    """🏗️ 木匠商店建筑清单（只读）：价格 + 材料 + 尺寸 + 是否买得起
    看建筑预算/规划农场布局用。建造操作暂未开放（防误扣资源）。

    （需 NagiBridge 模组重编译到最新版）
    """
    try:
        r = api.carpenter()
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', 'carpenter 端点不可用')}（模组需重编译）")
        lines = [f"🏗️ 木匠建筑 {r.get('count')} 种（买得起 {r.get('affordable')} 种）："]
        for b in (r.get("buildings") or []):
            mats = " ".join(f"{m.get('amount')}{m.get('id')}" for m in (b.get("materials") or [])) or "无"
            flag = "✅" if b.get("affordable") else "  "
            lines.append(f"  {flag} {b.get('name')} {b.get('cost')}g [{mats}] 尺寸{b.get('size')}")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 读建筑清单失败: {e}")


@mcp.tool()
def screenshot() -> Image:
    """📸 截取 AI 角色当前画面（AI 进程 7843 视角），AI 可直接看图"""
    global _SCREENSHOT_RES_FIXED
    r = api.screenshot_ai()
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "screenshot failed"))

    sw = r.get("width", 0)
    sh = r.get("height", 0)

    # 源分辨率保障：截图=back buffer=窗口大小，窗口被拉小会让源图变糊。
    # 若源太窄且本进程还没试过 → 一次性调 /resolution 提升到 1280x720（720p；带保护，失败不阻断截图）。
    # ⚠️ 2026-09-05 恒：不开 1080(1920x1080)——会把窗口强行撑全屏易卡；720=1280x720 是默认窗口尺寸、普通窗口不撑全屏。
    if sw < _SCREENSHOT_MIN_WIDTH and not _SCREENSHOT_RES_FIXED:
        _SCREENSHOT_RES_FIXED = True
        try:
            api._ai_get("/resolution", {"w": 1280, "h": 720})
            print(f"[screenshot] 源分辨率 {sw}x{sh} 过低，已调 /resolution 1280x720（下次截图生效）", flush=True)
        except Exception as e:
            print(f"[screenshot] 调 /resolution 失败（忽略）: {e}", flush=True)

    data = base64.b64decode(r["image"])

    # 高质量缩放：长边 > _SCREENSHOT_MAX_EDGE 才缩小，只缩不放、保持宽高比。
    # 让手机 App / 模型端没有"低质量降采样大图"的损失空间，文字/UI 更清晰。
    if _HAS_PIL:
        try:
            img = _PILImage.open(io.BytesIO(data))
            iw, ih = img.size
            longest = max(iw, ih)
            if longest > _SCREENSHOT_MAX_EDGE:
                scale = _SCREENSHOT_MAX_EDGE / longest
                nw, nh = max(1, round(iw * scale)), max(1, round(ih * scale))
                resample = _PILResampling.LANCZOS if _PILResampling else _PILImage.LANCZOS
                img = img.resize((nw, nh), resample)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                data = buf.getvalue()
            print(f"[screenshot] 源 {sw}x{sh}（PNG {iw}x{ih}）→ 发送 {img.size}", flush=True)
        except Exception as e:
            print(f"[screenshot] 缩放失败（发送原图）: {e}", flush=True)
    else:
        print(f"[screenshot] 源 {sw}x{sh}（无 Pillow，未缩放）", flush=True)

    return Image(data=data, format="png")


@mcp.tool()
def which_role() -> str:
    """🔌 确认当前端口↔角色映射（AI=farmhand轮回 / host=房主恒）。
    端口按启动顺序分配（谁先开谁占7842），重启后可能翻转——复现睡觉/协作前先调这个确认。
    返回两角色的端口+名字；游戏进程未就绪时返回 ok:false。"""
    r = api.which_role()
    if not r.get("ok"):
        return _with_state(f"⚠️ 角色检测未完成: {r.get('error')}（{r.get('note','')}）")
    a, h = r["ai"], r["host"]
    return _with_state(f"🔌 角色映射: AI({a['name']})={a['port']} | host({h['name']})={h['port']}")


@mcp.tool()
def go_sleep(who: str = "") -> str:
    """💤 上床睡觉（统一入口，已含爬床彩蛋）。who 指定睡谁的床：
    - 不传 / 传房主的名字 → 睡房主的床（一起睡，会在房主床上醒来 + 🌹一起睡彩蛋）
    - 传自己的名字 → 睡自己小屋的床（正常回家睡，无彩蛋）
    - 传其他玩家名 → 睡那个玩家的床
    睡别人家 = 爬床彩蛋：广播"<自己>爬上了<对方>的床！" + 醒来成功检测（在对方床醒来→一起睡彩蛋）。
    流程（2026-08-14 四场景验证锁定）：warp 进小屋→walk 到床边→精确对位→爬床广播→就地 ready→等过夜；
    夜不过自动"走刷新"重爬。房主没配合(卡 ReadyCheckDialog)超时则取消起床，绝不卡死。
    """
    api.ensure_roles()  # 端口↔角色可能翻转，先对齐
    r = api.go_sleep_flow(who)
    msg = r.get("summary", "❌ 睡觉失败")
    # 睡别人家 + 醒来位置核实 = 一起睡彩蛋成功
    if r.get("co_sleep") and r.get("woke_in_expected_bed") is True:
        msg = "🌹 一起睡彩蛋成功！" + msg
    return _with_state(msg)


@mcp.tool()
def lie_bed(who: str = "") -> str:
    """🛏️ 上床躺着（**只躺不睡，不过夜**）：完整走上床——warp 进小屋→walk 到床边→/position 对位→crawl_bed 设 isInBed，
    但**不调 /sleep 确认** → 日不结束、不结束一天。刻意不传送进床格（会 redirect 弹回门口）。
    用于休息/等待/躺一下。who 省略或房主名=躺房主的床（爬床彩蛋）；传自己名=躺自家。
    就寝真过夜→go_sleep；**不想躺了就 walk_to 走离床格**（isInBed 自动变 false，无需特别起身）；
    就绪屏弹出想撤就绪/关屏→cancel。
    """
    api.ensure_roles()  # 端口↔角色可能翻转，先对齐
    r = api.approach_bed(who)
    if not r.get("ok"):
        return _with_state(f"❌ 躺床失败: {r.get('error')}")
    return _with_state(f"🛏️ 已躺上{r.get('player')}的床（没确认睡觉，日没结束）。过夜→go_sleep，撤就绪/关屏→cancel")


@mcp.tool()
def cancel() -> str:
    """🚶 取消 / 关掉当前弹窗（通用）。按当前菜单自动分流：
    - ReadyCheckDialog（睡觉就绪屏，等全员 ready）：强制关屏 + 撤睡觉就绪(isInBed=false)。
      ⚠️ 这张屏不响应 Escape、也不在普通菜单里（/menu_close 报 no menu）——只有它能关；
      就绪屏弹出（等别人 ready）想撤就绪/关屏时调它。
    - 其它菜单/对话/节日弹窗：走通用取消键(escape) + menu_close。
    用法：AI 看到弹窗弹出、想取消/反悔/关掉时统一调它；确认就寝过夜用 go_sleep。"""
    api.ensure_roles()
    try:
        s = api._ai_get("/state")
    except Exception:
        s = {}
    mtype = ((s.get("activeMenu") or {}).get("type") or "").lower()
    if mtype == "readycheckdialog":
        r = api.ai_cancel_sleep()
        if not (isinstance(r, dict) and r.get("ok")):
            return _with_state(f"❌ 取消就绪失败: {r}")
        return _with_state("🚶 已取消（关掉睡觉就绪屏，撤了就绪、夜没过）。若不想躺再 walk_to 走离床格")
    # 其它菜单：通用取消键 + menu_close
    try:
        api.key("cancel", 1)
    except Exception:
        pass
    time.sleep(0.3)
    try:
        _menu_close()
    except Exception:
        pass
    return _with_state("🚶 已按取消键/关掉弹窗")


# ═══════════════════════════════════════════
#  📝 白板系统（A2 工作记忆，2026-08-13 #8）
# ═══════════════════════════════════════════
_WHITEBOARD_FILE = os.path.join(SCRIPT_DIR, "whiteboard.json")


def _wb_load() -> list:
    try:
        if os.path.exists(_WHITEBOARD_FILE):
            with open(_WHITEBOARD_FILE, encoding="utf-8") as f:
                return json.load(f).get("notes", [])
    except Exception:
        pass
    return []


def _wb_save(notes):
    try:
        with open(_WHITEBOARD_FILE, "w", encoding="utf-8") as f:
            json.dump({"notes": notes}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


@mcp.tool()
def whiteboard_write(content: str) -> str:
    """📝 写/追加白板笔记（A2 工作记忆）
    记每日计划、观察、优先级。过夜结算时 AI 读它复盘昨天、规划今天。

    Args:
        content: 笔记内容（追加一条）
    """
    notes = _wb_load()
    notes.append({"content": content, "pinned": False, "ts": time.strftime("%m-%d %H:%M")})
    _wb_save(notes)
    return _with_state(f"📝 白板已记：{content}")


@mcp.tool()
def whiteboard_pin(content: str) -> str:
    """📌 写一条跨天 pin 笔记（不会被 whiteboard_clear 清掉）
    比如"周三带刺身给小恒"、"攒够5个电池再下火山"。

    Args:
        content: 要 pin 的内容
    """
    notes = _wb_load()
    notes.append({"content": content, "pinned": True, "ts": time.strftime("%m-%d %H:%M")})
    _wb_save(notes)
    return _with_state(f"📌 已 pin：{content}")


@mcp.tool()
def whiteboard_read() -> str:
    """📋 读白板全部笔记（含 pin 的）
    新一天/过夜结算时先读——看昨天写了啥，结合晨报决定今天安排。
    """
    notes = _wb_load()
    if not notes:
        return _with_state("📋 白板是空的")
    lines = ["📋 白板:"]
    for n in notes:
        pin = "📌" if n.get("pinned") else "•"
        lines.append(f"  {pin} {n.get('content', '')}（{n.get('ts', '')}）")
    return _with_state("\n".join(lines))


@mcp.tool()
def whiteboard_clear() -> str:
    """🧹 清空白板（pin 的笔记保留；非 pin 的归档进会话日志）
    新的一天开始时用。旧内容自动追加进 session context（#7 会话缓冲）。
    """
    notes = _wb_load()
    keep = [n for n in notes if n.get("pinned")]
    cleared = [n for n in notes if not n.get("pinned")]
    _wb_save(keep)
    # 归档到 session context（#7 实现后生效）
    try:
        for n in cleared:
            _session_append("A2_白板", n.get("content", ""), None, "白板归档")
    except Exception:
        pass
    return _with_state(f"🧹 白板已清（归档 {len(cleared)} 条，保留 pin {len(keep)} 条）")


def session_status() -> str:
    """🧠 会话缓冲状态（条数 / 设置 / 导出文件路径）
    看看这一局记了多少上下文，设了啥。
    """
    lines = [f"🧠 会话缓冲: {len(_session_context)} 条"]
    lines.append(f"  📁 文件: {_session_file or f'session_{_session_ts}.jsonl'}")
    lines.append(f"  ⚙️ 设置: max_turns={SESSION_CFG['max_turns']} | export={SESSION_CFG['export_format']} | auto={SESSION_CFG['auto_export']} | npc={SESSION_CFG['include_npc']}")
    if _session_context:
        lines.append("  最近 3 条:")
        for rec in _session_context[-3:]:
            lines.append(f"    · {rec['speaker']}: {rec['content'][:40]}")
    return _with_state("\n".join(lines))


def session_set(setting: str, value: str) -> str:
    """🧠 改会话缓冲设置
    setting: max_turns(缓冲区轮次) / export_format(jsonl/markdown/both) / auto_export(true/false) / include_npc(true/false)

    Args:
        setting: 设置项名
        value: 新值（max_turns 用数字，其余用 true/false 或格式名）
    """
    global SESSION_CFG
    if setting in ("max_turns",):
        try:
            SESSION_CFG[setting] = int(value)
        except ValueError:
            return _with_state(f"❌ max_turns 要数字，收到「{value}」")
    elif setting in ("export_format",):
        if value not in ("jsonl", "markdown", "both"):
            return _with_state("❌ export_format 要 jsonl/markdown/both")
        SESSION_CFG[setting] = value
    elif setting in ("auto_export", "include_npc"):
        SESSION_CFG[setting] = str(value).lower() in ("true", "1", "yes")
    else:
        return _with_state(f"❌ 未知设置项「{setting}」（max_turns/export_format/auto_export/include_npc）")
    return _with_state(f"🧠 {setting} = {SESSION_CFG[setting]}")


def session_export() -> str:
    """📤 手动导出会话缓冲（供 LLM 前端记忆归档）
    游戏结束自动导出（auto_export=true 时），也可手动调。
    """
    _session_export()
    return _with_state(f"📤 已导出 {len(_session_context)} 条会话（session_{_session_ts}）")


# ═══════════════════════════════════════════
#  🧠 会话域（2026-09-02：session_status/set/export 三合一并入此域）
# ═══════════════════════════════════════════
def _session_status():
    return session_status()


def _session_set(setting: str = "", value: str = ""):
    return session_set(setting, value)


def _session_exportop():
    return session_export()


@mcp.tool()
def session(ops: str = "", kw: dict | None = None) -> str:
    """🧠 会话域（上下文缓冲，多数不用）。ops: status(看缓冲条数/设置) set(改 setting,value) export(手动导出记忆)。
    Args:
        ops: 动作（status/set/export）
        kw: set 的 {setting,value}；其余空参即可
    """
    dispatch = {
        "status": _session_status, "看": _session_status,
        "set": _session_set, "改": _session_set,
        "export": _session_exportop, "导出": _session_exportop,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


# ═══════════════════════════════════════════
#  ⚙️ 设置类超级工具 + 退役召回（2026-08-13 #6）
# ═══════════════════════════════════════════
_SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")
_retired_tools = set()
_look_verified = False   # 🔒 捏人窗确认门禁：调 confirm_look 后置 True，ok 才放行（2026-08-22 恒）

# 🚀 异步脚本配置（B1）：enabled=开关；wake_interval=AI 醒来间隔（秒），
# 异步脚本运行期间状态条多久提醒一次"脚本还在跑"（0=每次工具返回都提醒）。
# 必须定义在 _settings_load() 调用之前（模块加载顺序）。
_BG_CFG_DEFAULTS = {"enabled": True, "wake_interval": 60, "auto_async": True}
_bg_cfg = dict(_BG_CFG_DEFAULTS)
_bg_last_wake = 0.0
_bg_last_ai_activity = 0.0   # 上次 AI 工具调用（状态条构建）时间——异步唤醒不打断连续操作

# 🧺 智能存储配置：storage.default.<地点名> = {x, y}（该场景默认箱）
_storage_cfg = {"default": {}}

# 🛋️ 计划模式（2026-08-14 全自动一天）—— 🚫 已退役（2026-08-17 恒：计划模式暂不实现，AI 连续跑脚本取消）。
#    mode 字段保留结构但恒为 "autonomous"：settings mode=plan 已失效，_mode_cfg["mode"] 永不等于 "plan"。
_MODE_DEFAULTS = {"mode": "autonomous"}
_mode_cfg = dict(_MODE_DEFAULTS)

# 🌙 兜底自动睡觉：凌晨几点触发 go_sleep（覆盖所有行为，最高优先）
_SLEEP_DEFAULTS = {"enabled": True, "time": 2500}
_sleep_cfg = dict(_SLEEP_DEFAULTS)

# 🌿 苔藓暴露门控（2026-08-21 恒）：expose_all_days=off(默认) 只在绿雨当天报苔藓杂草/树；
#   on=平时也暴露（settings moss on/off）——绿雨天变体(clump/GreenRainWeeds/moss树)识别后供搜刮。
_MOSS_DEFAULTS = {"expose_all_days": False}
_moss_cfg = dict(_MOSS_DEFAULTS)


def _settings_load():
    global _retired_tools, _bg_cfg, _storage_cfg, _mode_cfg, _sleep_cfg, _moss_cfg
    try:
        if os.path.exists(_SETTINGS_FILE):
            d = json.load(open(_SETTINGS_FILE, encoding="utf-8"))
            _retired_tools = set(d.get("retired", []))
            if isinstance(d.get("async"), dict):
                for k, v in d["async"].items():
                    if k in _bg_cfg:
                        _bg_cfg[k] = v
            if isinstance(d.get("storage"), dict):
                _storage_cfg = d["storage"]
                if not isinstance(_storage_cfg.get("default"), dict):
                    _storage_cfg["default"] = {}
            # 🚫 计划模式已退役（2026-08-17 恒）：settings.json 里的 "mode" 忽略，恒为 autonomous。
            #    保留解析结构以防将来恢复，但强制覆盖为 autonomous。
            _mode_cfg["mode"] = "autonomous"
            if isinstance(d.get("auto_sleep"), dict):
                for k, v in d["auto_sleep"].items():
                    if k in _sleep_cfg:
                        _sleep_cfg[k] = v
            if isinstance(d.get("moss"), dict):
                for k, v in d["moss"].items():
                    if k in _moss_cfg:
                        _moss_cfg[k] = v
    except Exception:
        pass


def _settings_save():
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "retired": sorted(_retired_tools),
                "async": dict(_bg_cfg),
                "storage": _storage_cfg,
                "mode": "autonomous",   # 🚫 计划模式已退役，恒写 autonomous（2026-08-17）
                "auto_sleep": dict(_sleep_cfg),
                "moss": dict(_moss_cfg),
            }, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


_settings_load()


@mcp.tool()
def settings_status() -> str:
    """⚙️ 查看所有设置 + 退役工具（#6 设置类）
    心跳间隔 / 会话上下文轮次 / 退役的一次性工具（捏脸等）。
    """
    lines = ["⚙️ 设置:"]
    lines.append("  🎮 自主模式（🚫 计划模式已退役 2026-08-17：settings mode=plan 不再生效）")
    _s = _sleep_cfg.get("enabled", True)
    _t = int(_sleep_cfg.get("time", 2500))
    _st = f"{_t//100}:{_t%100:02d}"
    if _t >= 2400:
        _st += f"（凌晨{_t//100 % 24}点）"
    lines.append(f"  🌙 兜底睡觉: {'开' if _s else '关'}（{_st} 自动 go_sleep）")
    lines.append(f"  🌿 苔藓暴露: {'平时也暴露' if _moss_cfg.get('expose_all_days', False) else '只绿雨当天'}（settings moss on/off）")
    lines.append(f"  ⏱️ 心跳间隔: {player_activity.get_interval()} 分钟（0=每次工具返回都显示）")
    lines.append(f"  ⏰ 异步唤醒: {'开' if _bg_cfg.get('enabled', True) else '关'} / 间隔 {_bg_cfg.get('wake_interval', 30)}s"
                 f" / 长脚本自动异步 {'开' if _bg_cfg.get('auto_async', True) else '关'}(settings async_tools)")
    lines.append(f"  🧠 会话 max_turns: {SESSION_CFG['max_turns']}")
    lines.append(f"  📤 会话导出: {SESSION_CFG['export_format']} / auto={SESSION_CFG['auto_export']} / npc={SESSION_CFG['include_npc']}")
    lines.append(f"  🖱️ 失焦暂停: 关（后台完整运行）")
    lines.append("  🔧 退役工具: " + (", ".join(sorted(_retired_tools)) if _retired_tools else "无"))
    lines.append("💡 一次性工具（捏脸等）用完 settings_retire 退役；settings_reactivate 召回")
    return _with_state("\n".join(lines))


@mcp.tool()
def settings_retire(tool_name: str) -> str:
    """🔧 退役一个工具（隐藏，AI 不再用）
    用于一次性工具（捏脸 set_appearance、捏脸参考 list_hair_ref 等）：
    捏完满意就没用了 → 退役。需要时 settings_reactivate 召回。

    Args:
        tool_name: 工具名（如 set_appearance）
    """
    global _retired_tools
    _retired_tools.add(tool_name)
    _settings_save()
    return _with_state(f"🔧 已退役「{tool_name}」（AI 不再使用；settings_reactivate 可召回）")


@mcp.tool()
def settings_reactivate(tool_name: str) -> str:
    """🔧 召回一个退役工具（重新可用）
    比如想重新捏脸 / 看外观参考编号。

    Args:
        tool_name: 工具名（如 set_appearance）
    """
    global _retired_tools
    if tool_name in _retired_tools:
        _retired_tools.discard(tool_name)
        _settings_save()
        return _with_state(f"🔧 已召回「{tool_name}」（重新可用）")
    return _with_state(f"🔧 「{tool_name}」没在退役列表")


@mcp.tool()
def settings(setting: str = "", value: str = "", ops: str = "", kw: dict | None = None) -> Any:
    """⚙️ 系统/设置域（含捏脸）。全 ops + 关键坑（捏脸ok后不可逆）→ help(settings)。

    Args:
        setting: 配置项名(旧路径)
        value: 配置项新值(旧路径)
        ops: 设置域操作序列(新路径)
        **kw: 子工具参数(tool_name/外观参数)
    """
    if ops:
        return _with_state(_ops_run(ops, _SETTINGS_DISPATCH, kw))
    if not setting:
        return settings_status()
    global SESSION_CFG
    if setting == "heartbeat":
        try:
            v = int(value)
        except ValueError:
            return _with_state(f"❌ heartbeat 要数字，收到「{value}」")
        return _with_state(player_activity.set_interval(v))
    elif setting == "context_turns":
        try:
            v = int(value)
        except ValueError:
            return _with_state(f"❌ context_turns 要数字，收到「{value}」")
        SESSION_CFG["max_turns"] = v
        return _with_state(f"🧠 上下文轮次 = {v}")
    elif setting == "async":
        v = value.strip().lower()
        if v in ("on", "1", "true", "yes", "开"):
            _bg_cfg["enabled"] = True
            _settings_save()
            return _with_state("🚀 异步脚本已开启（script start 后台跑，AI 可并行聊天/整理背包）")
        if v in ("off", "0", "false", "no", "关"):
            _bg_cfg["enabled"] = False
            _settings_save()
            return _with_state("🛑 异步脚本已关闭（script start 退回同步等脚本跑完）")
        return _with_state(f"❌ async 要 on/off，收到「{value}」")
    elif setting in ("async_tools", "auto_async", "自动异步"):
        v = value.strip().lower()
        if v in ("on", "1", "true", "yes", "开"):
            _bg_cfg["auto_async"] = True
            _settings_save()
            return _with_state("🚀 长脚本自动异步已开启（钓鱼/挖矿/炸矿等便利工具自动后台跑，AI 不用手动 script start）")
        if v in ("off", "0", "false", "no", "关"):
            _bg_cfg["auto_async"] = False
            _settings_save()
            return _with_state("🛑 长脚本自动异步已关闭（便利工具退回同步等结果）")
        return _with_state(f"❌ async_tools 要 on/off，收到「{value}」")
    elif setting == "state_interval":
        try:
            v = int(value)
        except ValueError:
            return _with_state(f"❌ state_interval 要数字（秒），收到「{value}」")
        if v < 0:
            return _with_state("❌ 间隔不能为负")
        _bg_cfg["wake_interval"] = v
        _settings_save()
        return _with_state(f"⏲️ AI 醒来间隔 = {v}s（异步脚本运行期间状态条提醒频率；0=每次工具返回都提醒）")
    elif setting == "mode":
        # 🚫 计划模式已退役（2026-08-17 恒：计划模式暂不实现，AI 连续跑脚本取消）。
        #    mode=plan 拒绝并提示；autonomous 保持（本来就在）。
        v = value.strip().lower()
        if v in ("autonomous", "自主", "自主模式"):
            _mode_cfg["mode"] = "autonomous"
            _settings_save()
            return _with_state("🎮 自主模式（脚本由 AI 手动调，不自动连跑）")
        return _with_state("🚫 计划模式已退役（2026-08-17 恒）——AI 连续跑脚本的自动化暂不实现；当前只有自主模式。"
                           "需要连跑时请手动 script(ops=\"start\") / 逐任务调脚本。")
    elif setting == "auto_sleep":
        v = value.strip().lower()
        if v in ("on", "1", "true", "yes", "开"):
            _sleep_cfg["enabled"] = True
        elif v in ("off", "0", "false", "no", "关"):
            _sleep_cfg["enabled"] = False
        else:
            return _with_state(f"❌ auto_sleep 要 on/off，收到「{value}」")
        _settings_save()
        return _with_state(f"🌙 兜底自动睡觉已{'开启' if _sleep_cfg['enabled'] else '关闭'}")
    elif setting == "auto_sleep_time":
        try:
            hhmm = int(value)
            if not (0 <= hhmm < 2600):
                raise ValueError
        except ValueError:
            return _with_state(f"❌ auto_sleep_time 要游戏时间整数（如 2400 / 2500），收到「{value}」")
        _sleep_cfg["time"] = hhmm
        _settings_save()
        return _with_state(f"🌙 兜底睡觉时间 = {hhmm//100}:{hhmm%100:02d}（游戏时间，到点自动 go_sleep）")
    elif setting == "pin":
        return _with_state(f"📌 pin location 待做（#6 未来项：标记自己农场建筑位置）")
    elif setting in ("moss", "moss_all_days", "暴露苔藓"):
        # 🌿 苔藓暴露门控（2026-08-21 恒）：默认只在绿雨当天报苔藓，on=平时也暴露。
        v = value.strip().lower()
        if v in ("on", "1", "true", "yes", "开"):
            _moss_cfg["expose_all_days"] = True
            _settings_save()
            return _with_state("🌿 苔藓平时也暴露（非绿雨也报苔藓杂草/树）")
        if v in ("off", "0", "false", "no", "关"):
            _moss_cfg["expose_all_days"] = False
            _settings_save()
            return _with_state("🌿 苔藓只在绿雨当天暴露")
        return _with_state(f"❌ moss 要 on/off，收到「{value}」")
    return _with_state(f"❌ 未知设置「{setting}」（heartbeat/context_turns/async/state_interval/mode/auto_sleep/auto_sleep_time/pin/moss）")


# ═══════════════════════════════════════════
#  🔍 check 超级工具（A2 查询域入口，2026-08-13 #10）
#  ⚠️ 边界：check_status=概览（状态条同款）；check_backpack=逐格详细。查啥用 check。
# ═══════════════════════════════════════════
def _quest_menu_hint() -> str:
    """📜 check quest 指引：任务/进度一律走菜单（2026-09-01 恒拍板：菜单为唯一权威，退役 list_quests/quest_progress）。"""
    return _with_state(
        "📜 任务看 **menu journal**(开日志) + **menu read**(读QuestLog卡，卡上含每子目标 current/max 进度)；"
        "接单去展板 **menu read** + click(button=accept…)；查某单详情用 **menu know <名>**")


@mcp.tool()
def check(what: str) -> str:
    """🔍 查询域（what=...，非 ops）。status(完整状态) backpack(逐格价值/星级) worn(穿戴) machines(机器)
    mine(下矿进度) silo(干草) mastery(精通) buildings(木匠) quest(任务) chests(当前图箱) storage(箱子网络)
    look(环视,radius)。⚠️查概览用 status，查逐格用 backpack。细节→help(check)。
    Args:
        what: 查什么（status/backpack/worn/machines/mine/silo/mastery/buildings/quest/chests/look）
    """
    w = (what or "").strip().lower()
    dispatcher = {
        "status": check_status,
        "backpack": check_backpack,
        "worn": check_worn,
        "machines": machine_report, "machine": machine_report, "machine_report": machine_report,
        "mine": check_mine_progress, "mining": check_mine_progress,
        "silo": silo_status, "hay": silo_status,
        "mastery": mastery_status, "精通": mastery_status,
        "buildings": building_list, "building": building_list, "building_list": building_list,
        "quest": _quest_menu_hint, "quests": _quest_menu_hint, "任务": _quest_menu_hint,
        "chests": scan_chests, "箱子": scan_chests,
        "storage": storage_layout, "存储": storage_layout,
        "look": look_around, "周围": look_around, "环视": look_around,
    }
    fn = dispatcher.get(w)
    if fn is None:
        return _with_state(f"❌ 未知查询「{what}」（status/backpack/worn/machines/mine/silo/mastery/buildings/quest/chests）")
    return fn()


# ═══════════════════════════════════════════
#  🌾⛏️🐄 域工具（A2 十域合并，组合式 ops，2026-08-14 #2）
#  十域（08-12 定稿）：farm / care / mine / social / interact / menu / shop /
#  daily / inspect(=check) / map；surroundings/screenshot/run_script 独立。
#  组合式 ops：域工具一次调用传多个 op（空格分隔），顺序执行多个原语，
#  AI 自己决定粒度（如 farm(ops="till plant water")）。
# ═══════════════════════════════════════════
_STATE_SEP = "\n\n╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"


def _ops_run(ops_str: str, dispatch: dict, kw: dict) -> str:
    """组合式 ops 执行器：空格/逗号拆多 op 逐个执行，kw 按签名自动过滤。
    dispatch: {op: callable}。结果去内嵌状态条，由调用方最后统一 _with_state 附一次。"""
    kw = kw or {}  # 2026-09-02 kw 改为可选后，空参调用会是 None，归一成 {}
    import inspect
    # 🐛 FastMCP 对 **kw 函数生成的 schema 是 {ops, kw}，实际调用 map(ops=, kw={...}) 后 **kw
    #   收成 {"kw": {...}} 嵌套——解包回 {...} 再按签名过滤（2026-08-19 实测：带参域工具一直收不到参）
    if isinstance(kw, dict) and set(kw) == {"kw"} and isinstance(kw.get("kw"), dict):
        kw = kw["kw"]
    ops = [o for o in re.split(r"[\s,，]+", (ops_str or "").strip()) if o]
    if not ops:
        return "❌ ops 为空（如 farm(ops=\"till plant water\")）"
    results = []
    for op in ops:
        fn = dispatch.get(op)
        if fn is None:
            # 2026-09-02 零成本改：未知 op 直接列可用 ops，别只报错让 AI 再猜
            _op_keys = [k for k in dispatch if isinstance(k, str) and not any("一" <= c <= "鿿" for c in k)]
            # 2026-09-03 恒：中文别名同义（喂水/浇/收…）照常可用，报错点明，别让 AI 以为只能英文
            results.append(f"❌ 未知操作「{op}」。此域可用 ops: {' '.join(sorted(_op_keys))}"
                           f"（中文别名同义可用，如 喂水=宠物碗水）；详查 help(域)）")
            continue
        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            sig = None
        try:
            if sig and any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                call_kw = dict(kw)
            else:
                call_kw = {k: v for k, v in (kw or {}).items() if sig and k in sig.parameters}
            out = fn(**call_kw)
            if isinstance(out, str):
                if _STATE_SEP in out:
                    out = out.split(_STATE_SEP)[0]   # 去内嵌状态条，最后统一加一次
                results.append(out)
            else:
                return out   # 非文本结果（如图片）直接返回，不能 join
        except TypeError as e:
            # 2026-09-03 恒：参数名猜错（缺参）→ 直接列出可用参数名，别再让 AI 靠报错猜
            avail = ", ".join(p.name for p in sig.parameters.values()) if sig else "?"
            results.append(f"❌ op「{op}」参数错: {e}（此 op 可用参数: {avail}）")
        except Exception as e:
            results.append(f"❌ op「{op}」: {e}")
    return "\n\n".join(r for r in results if r)


# ═══════════════════════════════════════════
#  🗺️ 动态工具检测（2026-08-14 #13，map 联动·修正版）
#  不拦 AI，只建议：状态条报"本图可用域" + 域工具在错地方时返回建议行（照跑）。
#  未解锁地点由 map_go 单独拦截（见 LOCKED_MAPS + /unlocks）。
# ═══════════════════════════════════════════

# 域 → 适用地图（能干活的功能区；未列出的域任意区可用）
DOMAIN_HOME = {
    # ⚠️ 2026-08-15 恒：温室/姜岛农场也是 farm 适用区（之前只认 Farm，温室给错建议）
    "farm": ["Farm", "Greenhouse", "IslandWest", "IslandNorth", "IslandEast"],
    "mine": ["Mine", "SkullCave"],
    # 🏠 2026-08-16 恒：小屋域=屋里（FarmHouse/Cabin/岛屋）enum 引导
    "cabin": ["FarmHouse", "Cabin", "IslandFarmHouse"],
}
# 前缀匹配（矿洞/火山各层是独立 location）
DOMAIN_PREFIX = {
    "mine": ["UndergroundMine", "VolcanoDungeon"],
}
# 免建议的 op：导航类（自己会导航）/ API 直操作
DOMAIN_EXEMPT = {
    "mine": {"go", "去"},
    "farm": {"buy", "买", "买动物"},   # 买动物去玛妮牧场不在农场，豁免建议（care 域 2026-09-02 并入 farm）
    "fish": {"go", "去", "钓", "fish"},
    "cabin": {"sleep", "睡", "睡觉"},   # go_sleep 自己会回家
}


def _is_domain_applicable(domain: str, cur: str) -> bool:
    """纯地点判断：当前地图是否适用 domain 域（不含 farm 的 HoeDirt 特例）。"""
    if cur in DOMAIN_HOME.get(domain, []):
        return True
    return any(cur.startswith(p) for p in DOMAIN_PREFIX.get(domain, []))


def _domain_advice(domain: str, ops_str: str) -> str:
    """按域建议（不拦）：当前区域不适合该域 → 返回建议行；适合/豁免/读不到 → 空串。"""
    if domain not in DOMAIN_HOME:
        return ""
    try:
        s = api.state()
        cur = (s.get("location") or {}).get("name", "")
    except Exception:
        return ""
    if not cur or _is_domain_applicable(domain, cur):
        return ""
    # 豁免 op：导航/直操作 → 不啰嗦
    ops = [o for o in re.split(r"[\s,，]+", (ops_str or "").strip()) if o]
    exempt = DOMAIN_EXEMPT.get(domain, set())
    if ops and all(o in exempt for o in ops):
        return ""
    # farm 特例：附近有 HoeDirt（温室/姜岛可种植点）也认
    if domain == "farm":
        try:
            surr = api.surroundings(6)
            if any((t.get("terrain") or "") == "HoeDirt" for t in surr.get("tiles", [])):
                return ""
        except Exception:
            pass
    home = DOMAIN_HOME[domain][0]
    return f"💡 当前在{cur}，{domain}通常在{home}做；可先 map go {home}"


def _domains_here(cur: str) -> list:
    """当前地图适用的地点绑定域列表（farm/care/mine；供状态条"🛠️ 可用域"）。"""
    return [d for d in DOMAIN_HOME if _is_domain_applicable(d, cur)]


def _farm_default_xy(x: int, y: int):
    """x/y 传 -1（或默认哨兵）→ 解析成玩家面向格当田块起点（2026-09-03 恒）。
    旧默认 (60,10) 是祖传硬编码：AI 锄完田再 plant 不给坐标，会播到八竿子打不着的 (60,10)，
    冒烟实测"一直移到奇怪起点/种子没消耗"。改成玩家当前位置当起点，犁种都挨着人选。
    面向 0上/1右/2下/3左：默认取面向格（面前 1 格）当田左上角，走位方向 horizontal 右延。
    玩家在田上方站着往下锄的惯例 → 面向下时起点=(px, py+1)。其它朝向按面前 1 格。"""
    if x >= 0 and y >= 0:
        return x, y
    st = api.state()
    px, py = st["player"]["x"], st["player"]["y"]
    fd = st.get("player", {}).get("facingDirection", 2)
    front = {0: (px, py - 1), 1: (px + 1, py), 2: (px, py + 1), 3: (px - 1, py)}
    return front.get(fd, (px, py + 1))


def _farm_rect(x: int, y: int, rows: int, length: int, direction: str):
    """由 x,y,rows,length,direction 算矩形角点 (x1,y1,x2,y2)。"""
    dx, dy = (1, 0) if direction == "horizontal" else (0, 1)
    rdx, rdy = (0, 1) if direction == "horizontal" else (1, 0)
    x2 = x + dx * (length - 1) + rdx * (rows - 1)
    y2 = y + dy * (length - 1) + rdy * (rows - 1)
    return min(x, x2), min(y, y2), max(x, x2), max(y, y2)


def _farm_kw_norm(x, y, rows, length, direction, extra, extra_ok=()):
    """农活域 kw 归一化：cols/col/width/len → length；未知 kw 报错，别静默吞（2026-09-03 恒）。
    返回 (length, None) 正常，或 (None, err)。extra_ok=额外允许键（plant 的 seed_name）。"""
    for k in ("cols", "col", "width", "len"):
        if k in extra:
            length = extra.pop(k)
    bad = set(extra) - set(extra_ok)
    if bad:
        avail = "x y rows length direction" + (f" {' '.join(sorted(extra_ok))}" if extra_ok else "")
        return (None, f"❌ 未知参数 {sorted(bad)}。此 op 可用: {avail}（每行几格写 length，或 cols/width 别名，别同时写）")
    return (length, None)


def _farm_till(x: int = -1, y: int = -1, rows: int = 1, length: int = 1,
               direction: str = "horizontal", **extra) -> str:
    """纯锄地（不开播种）。⚠️ 2026-09-03 恒：**尺寸默认 1×1，不擅自扩**——AI 说种多少就锄多少，
    想锄多宽自己传 rows/length（旧默认 5×5 会把"就锄一下"扩成 25 格大田=意外耗体力）。
    缺坐标→玩家面向格；缺尺寸→只锄面前 1 格。"""
    length, err = _farm_kw_norm(x, y, rows, length, direction, extra)
    if err: return err
    x, y = _farm_default_xy(x, y)   # 2026-09-03 恒：缺坐标→玩家面向格（旧常量 (60,10) 会锄飞到别处）
    x1, y1, x2, y2 = _farm_rect(x, y, rows, length, direction)
    return _till_rect(x1, y1, x2, y2)


def _farm_plant_only(seed_name: str, x: int = -1, y: int = -1, rows: int = 1,
                     length: int = 1, direction: str = "horizontal", **extra) -> str:
    """🌱 独立播种（在已锄好地上种，不锄不浇）——2026-08-15 恒：单独播种工具。
    走 farm_row --plant-only（每格走位+select种子+种）。缺坐标→玩家面向格（2026-09-03 恒）。
    ⚠️ 尺寸默认 1×1 不擅自扩——AI 报多少格就种多少格。"""
    length, err = _farm_kw_norm(x, y, rows, length, direction, extra)
    if err: return err
    x, y = _farm_default_xy(x, y)
    try:
        dir_map = {"horizontal": "right", "vertical": "down"}
        farm_dir = dir_map.get(direction, "right")
        args_list = ["--port", str(_ai_port()), "--plant-only",
                     "--seed", seed_name, str(x), str(y), str(length),
                     "--rows", str(rows), "--dir", farm_dir]
        out = _run_script("farm_row", args_list, timeout=180)
        return f"🌱 播种「{seed_name}」（{rows}行×{length}格，不锄不浇）:\n{out[:600]}"
    except Exception as e:
        return f"❌ 播种失败: {e}"


def _farm_clear(x: int = -1, y: int = -1, rows: int = 1, length: int = 1,
                direction: str = "horizontal", **extra) -> str:
    """清杂草/石头（按 x,y,rows,length 或 x1,y1,x2,y2 都行）。缺坐标→玩家面向格；尺寸默认 1×1 不擅扩。"""
    length, err = _farm_kw_norm(x, y, rows, length, direction, extra)
    if err: return err
    x, y = _farm_default_xy(x, y)
    x1, y1, x2, y2 = _farm_rect(x, y, rows, length, direction)
    return clear_area(x1, y1, x2, y2)


@mcp.tool()
def bundle_status(area: str = "") -> str:
    """🎁 社区中心献祭板状态：逐块【开的板】读 bundle（完成状态+需要物品）
    自动导航到社区中心，对每块开的板 position 到板前 → interact_at(板瓦片) → read_menu，
    返回各房间 bundle 需求清单（✅ 已完成 / ⬜ 未完成 + 物品×数量）。AI 据此规划做哪个献祭、缺什么。
    参数 area: 只查某房间（工艺室/茶水间/鱼缸/锅炉房/布告栏/金库），空=全部开的板。
    捐物品流程（后续）：走到板 → interact_at → read_menu 看缺口 → menu_click 选物品捐赠。
    """
    try:
        cur = (api.state().get("location") or {}).get("name", "")
        if cur != "CommunityCenter":
            walk_to("社区中心(献祭大厅)")
        boards = locations.COMMUNITY_CENTER_BOARDS
        if area:
            boards = {k: v for k, v in boards.items() if area in k}
        if not boards:
            return _with_state("🎁 没有匹配的献祭板（试试 工艺室/茶水间/鱼缸/锅炉房/布告栏/金库）")
        lines = ["🎁 社区中心献祭板：" + "、".join(boards)]
        # 🎒 背包可捐赠标记（2026-08-16）：/state 背包无 id 字段 → 用名字匹配（displayName 中文，
        # 和 /menu 的 ingredient name(GetDisplayName) 一致）。归一化 name → {name, count}
        _have = {}
        try:
            for _it in (api.state().get("inventory") or []):
                _nm = (_it.get("displayName") or _it.get("name") or "").strip().lower()
                if _nm:
                    _have.setdefault(_nm, {"name": _it.get("displayName") or _it.get("name"), "count": 0})
                    _have[_nm]["count"] += int(_it.get("stack", 1) or 1)
        except Exception:
            pass
        for name, cfg in boards.items():
            if not cfg.get("open"):
                lines.append(f"  ⚪ {name}：未开启")
                continue
            tx, ty = cfg["tile"]
            try:
                # 站板下一格朝上（恒 2026-08-16：全部从板往下一格朝上交互）；下一格是墙则探左右
                stand = (tx, ty + 1)
                if not api._post('/passable', {'x': stand[0], 'y': stand[1]}).get('passable'):
                    for sx, sy in ((tx - 1, ty + 1), (tx + 1, ty + 1), (tx, ty + 2)):
                        if api._post('/passable', {'x': sx, 'y': sy}).get('passable'):
                            stand = (sx, sy)
                            break
                api.position(stand[0], stand[1])
                api.face(0)
                time.sleep(0.5)
                interact_at(tx, ty)
                m = api.menu()
                cc = (m or {}).get("characterCust") if isinstance(m, dict) else None
                if not cc:
                    lines.append(f"  ⚠️ {name}：没打开菜单（可能没激活）")
                else:
                    _reward = calendar_data.COMMUNITY_CENTER_REWARDS.get(name, "")
                    _rew = f"  🏆 完成奖励: {_reward}" if _reward else ""
                    lines.append(f"  📋 {name}（{cc.get('areaName')}）:{_rew}")
                    for b in cc.get("bundles") or []:
                        done = "✅" if b.get("complete") else "⬜"
                        ing_parts = []
                        for i in (b.get('ingredients') or []):
                            if i.get('completed'):
                                ing_parts.append(f"⭕{i.get('name')}")   # 已捐
                            else:
                                ing_parts.append(f"{i.get('name')}×{i.get('count')}")
                        ings = ", ".join(ing_parts)
                        don = [f"{_have[k]['name']}×{_have[k]['count']}"
                               for i in (b.get('ingredients') or [])
                               if not i.get('completed')
                               for k in [str(i.get('name') or '').strip().lower()]
                               if k in _have and _have[k]['count'] > 0]
                        mark = f"  🎒你有: {', '.join(don)}" if don else ""
                        lines.append(f"    {done} {ings}{mark}")
            finally:
                try:
                    m = api.menu()
                    if isinstance(m, dict) and m.get("open"):
                        api._post("/menu_close")
                        time.sleep(0.3)
                except Exception:
                    pass
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 献祭板状态失败: {e}")


@mcp.tool()
def bundle_kb(query: str = "") -> str:
    """📖 献祭(社区中心收集包)知识库——**不用跑到社区中心**，从 wiki 静态表查
    某收集包要什么/去哪弄/奖励是啥，方便规划去皮埃尔买献祭相关作物、提前备货。
    与 bundle_status(实地读板看缺口)互补：bundle_kb 查「原来要这些」，bundle_status 查「我现在缺哪些」。
    query:
      - 空 → 全房间概览
      - 房间名: 工艺室/茶水间/鱼缸/锅炉房/布告栏/地下室/遗失 → 那间所有收集包
      - 收集包名 or 物品名: 春季作物/防风草/蟹笼/土豆... → 匹配到的收集包
    ⚠️ 名称以 wiki/官方中文为准(绿豆/甜瓜/西红柿)；项目 crops.py 命名略异（青豆=绿豆/番茄=西红柿/西瓜=甜瓜）。
    """
    return _with_state(bundles.search_bundles(query))


@mcp.tool()
def farm(ops: str = "", kw: dict | None = None) -> str:
    """🌾 农活域（必走，禁手动 use_tool/tool_area）。高频：water 浇 / harvest 收 / plant 种 / till 锄 / fertilize 化肥 / clear 清杂 / collect 收机器。全 ops+参数 → help(farm)。
    ⚠️ till/plant/till_plant/clear/fertilize **尺寸不设默认**（缺=只做 1 格，绝不默认 5×5）——要多少自己传 rows + length（如 rows=1 length=5 锄一行 5 格）。

    """
    op_list = [o for o in re.split(r"[\s,，]+", (ops or "").strip()) if o]
    if not op_list:
        return _with_state("❌ ops 为空（如 farm(ops=\"till plant water\")）")
    # 🗺️ 动态工具检测：不在可种植区 → 建议行（不拦，照跑）
    _adv = _domain_advice("farm", ops)
    # 组合语义：till+plant 同时出现 → 合并成一次 till_and_plant（锄+种一体）
    has_till = "till" in op_list
    has_plant = any(o in ("plant", "sow") for o in op_list)
    if has_till and has_plant:
        op_list = [o for o in op_list if o not in ("till", "plant", "sow")]

    dispatch = {
        "till": _farm_till,
        "plant": _farm_plant_only, "sow": _farm_plant_only, "till_plant": till_and_plant,
        "water": water_crops, "浇": water_crops,
        "harvest": harvest_crops, "收": harvest_crops,
        "scythe": scythe_crops,
        "fertilize": apply_fertilizer, "化肥": apply_fertilizer,
        "clear": _farm_clear, "清": _farm_clear,
        "plot": plot_plan, "规划": plot_plan,
        "tillfield": till_field, "蓄力锄": till_field,
        "hoe": hoe_layout, "布局锄": hoe_layout,
        "plantlayout": plant_layout, "播种规划": plant_layout,
        "plan": plan_farm_layout_tool, "方形规划": plan_farm_layout_tool,
        "chop": chop_trees, "砍树": chop_trees,
        "clearground": clear_ground, "清格": clear_ground,
        "collect": collect_machines, "机器": collect_machines,
        "load": load_machines, "上料": load_machines,
        "building": work_building, "收放": work_building,
        "break": break_tile, "拆": break_tile, "敲": break_tile,
        "place": place_item, "放": place_item, "放置": place_item,
        # 🐟 鱼塘（养殖业，2026-08-16 恒拍板归 farm 域；需新 DLL）
        "pond": _pond_list, "鱼塘": _pond_list, "塘": _pond_list,
        "pond_add": _pond_add, "放鱼": _pond_add,
        "pond_feed": _pond_feed, "喂塘": _pond_feed,
        "pond_collect": _pond_collect, "领籽": _pond_collect,
        "pond_fish": _pond_fish, "塘钓": _pond_fish,
        # 🐄 动物照料（2026-09-02 care 域退役并入 farm；"water"=浇地/"building"=机器收放已占，
        #    动物水/畜舍用 喂水/畜舍 不冲突：farm water 浇地，farm 喂水 宠物碗，farm building 一屋收放，farm 畜舍 这间屋动物）
        "animals": care_animals, "摸动物": care_animals,
        "畜舍": care_building, "这间": care_building,
        "pet": pet_pet, "摸摸": pet_pet, "摸猫狗": pet_pet,
        "喂水": pet_water, "碗": pet_water, "宠物碗": pet_water,
        "milk": milk_shear, "挤奶": milk_shear, "剪毛": milk_shear, "shear": milk_shear,
        "buy": buy_animal, "买动物": buy_animal, "买": buy_animal,
        "doors": close_doors, "关门": close_doors,
        "petwalk": pet_walk, "遛": pet_walk, "放牧": pet_walk,
        "hay": feed_hay, "干草": feed_hay, "加草": feed_hay,
        "statue": blessing_statue, "祈福": blessing_statue,
    }
    results = []
    # till+plant 合并后先锄+种，再接剩余 ops
    if has_till and has_plant:
        results.append(_ops_run("till_plant", dispatch, kw))
    results.append(_ops_run(" ".join(op_list), dispatch, kw))
    _body = "\n\n".join(r for r in results if r)
    return _with_state((_adv + "\n\n" if _adv else "") + _body)


@mcp.tool()
def mine(ops: str = "", kw: dict | None = None) -> str:
    """⛏️ 下矿域。ops: go(去挖矿 mode=rush冲层/farm刷矿, start, target, ore, cycles) progress(进度)
    bomb_status/plan/place/collect/ladder/retreat(单步炸) bomb_mine(自动) bomb_volcano(火山)
    organize(整理背包)。⚠️无镐/血低硬拦；bomb_volcano 需 host 陪同。（协同=bomb_mine 没炸弹自动转内部,不对外）。
    电梯可达层检测不对外暴露工具——内置在 mine_run/bomb_mine 脚本启动时自动读。细节→help(mine)。
    """
    # 🗺️ 动态工具检测：progress/bomb_* 建议在矿里做（go 豁免，不拦）
    _adv = _domain_advice("mine", ops)
    dispatch = {
        "go": go_mining, "rush": go_mining, "farm": go_mining, "去": go_mining,
        "progress": check_mine_progress, "进度": check_mine_progress,
        "bomb_status": bomb_status, "bomb_plan": bomb_plan, "bomb_place": bomb_place,
        "bomb_collect": bomb_collect, "bomb_ladder": bomb_ladder, "bomb_retreat": bomb_retreat,
        "bomb_mine": bomb_mine, "bomb_volcano": bomb_volcano,   # 🚫 2026-08-22 恒：bomb_escort 不对外暴露(协同内建进 bomb_mine 自动转)，AI 不再能主动启用
        "organize": bomb_organize, "整理背包": bomb_organize,
    }
    _body = _ops_run(ops, dispatch, kw)
    return _with_state((_adv + "\n\n" if _adv else "") + _body)


# ═══════════════════════════════════════════
#  🏠 小屋/家 域（2026-08-16 恒：enum 引导 + 屋内操作）
# ═══════════════════════════════════════════
HOME_MAPS = {"FarmHouse", "Cabin", "IslandFarmHouse"}


def _cabin_collect() -> str:
    """收当前屋的待收机器（限定当前地点，不全农场乱跑）。"""
    try:
        cur = api.state().get("location", {}).get("name", "")
    except Exception:
        cur = ""
    return collect_machines(location=cur)


def _cabin_enum() -> str:
    """扫当前屋：待收机器/雕像/家具清单 + 引导。只在屋内（FarmHouse/Cabin/岛屋）有意义。"""
    try:
        cur = api.state().get("location", {}).get("name", "")
        lines = [f"🏠 当前: {cur}"]
        if cur not in HOME_MAPS:
            lines.append(f"⚠️ 小屋域只在屋内用（FarmHouse/Cabin/岛屋）——现在在{cur}，可先 map go 回家")
            return "\n".join(lines)
        # 1) 待收机器（farm_report 按当前屋过滤）
        try:
            fr = _fetch_farm_report()
            ml = (fr.get("machines") or {}).get("machines") or []
            ready = [m for m in ml if m.get("status") == "ready" and m.get("location") == cur]
            if ready:
                names = ", ".join(dict.fromkeys(MACHINE_CN.get(m.get("type", "?"), m.get("type", "?")) for m in ready))
                lines.append(f"⚙️ 待收机器 {len(ready)} 台：{names}（cabin collect 收）")
            else:
                lines.append("⚙️ 屋里没有待收机器")
        except Exception:
            pass
        # 2) 雕像（家具里找 Statue/雕像）
        try:
            fu = api._get("/furniture")
            fl = fu.get("furniture") or []
            stats = [f for f in fl if "Statue" in (f.get("name") or "") or "雕像" in (f.get("name") or "")]
            if stats:
                lines.append(f"🗿 雕像 {len(stats)} 座：{', '.join(f['name'] for f in stats)}（cabin statue 摸）")
            else:
                lines.append("🗿 屋里没有雕像")
        except Exception:
            pass
        # 3) 家具清单（TV/日历标 emoji）
        try:
            fu = api._get("/furniture")
            fl = fu.get("furniture") or []
            tags = []
            for f in fl:
                n = f.get("name") or "?"
                tag = "📺" if f.get("isTV") else ("📅" if "Calendar" in n else "")
                tags.append(f"{n}({f.get('x')},{f.get('y')}){tag}")
            if tags:
                lines.append(f"🪑 家具 {len(tags)} 件：{', '.join(tags[:8])}{'…' if len(tags) > 8 else ''}")
                lines.append("   交互→scene at(x,y)（📺电视/📅日历/壁炉）；摆弄→cabin pickup(x,y) 拿起")
            else:
                lines.append("🪑 屋里没有家具")
        except Exception:
            pass
        # 4) 睡觉引导
        lines.append("🛏️ 睡觉→daily sleep（不传=睡房主床一起睡；传自己名=睡自家）")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


@mcp.tool()
def cabin(ops: str = "", kw: dict | None = None) -> str:
    """🏠 小屋/家域（屋内 FarmHouse/Cabin/岛屋；不传=扫屋）。ops: enum(扫屋查待收) collect(收本屋机器)
    statue(雕像) furniture(扫家具) interact(x,y点家具) pickup(x,y拿起家具) sleep(睡觉 who)。细节→help(cabin)。
    """
    # 🗺️ 动态工具检测：小屋域建议在屋里做（sleep 豁免，自己会回家）
    _adv = _domain_advice("cabin", ops)
    dispatch = {
        "enum": _cabin_enum, "看": _cabin_enum, "引导": _cabin_enum,
        "collect": _cabin_collect, "收": _cabin_collect, "机器": _cabin_collect,
        "statue": blessing_statue, "雕像": blessing_statue, "祈福": blessing_statue,
        "furniture": scan_furniture, "家具": scan_furniture,
        "interact": interact_at, "点": interact_at,
        "place": place_item, "放": place_item, "放置": place_item,
        "break": break_tile, "拆": break_tile, "敲": break_tile,
        "pickup": furniture_pickup, "拿": furniture_pickup, "摆": furniture_pickup,
        "sleep": go_sleep, "睡": go_sleep, "睡觉": go_sleep,
    }
    if not (ops or "").strip():
        return _with_state((_adv + "\n\n" if _adv else "") + _cabin_enum())
    _body = _ops_run(ops, dispatch, kw)
    return _with_state((_adv + "\n\n" if _adv else "") + _body)


# ═══════════════════════════════════════════
#  🐟 鱼塘域（2026-08-16 恒：需新 DLL——/fish_pond 状态端点 + /interact 鱼塘 bypass）
# ═══════════════════════════════════════════
# 状态走 /fish_pond（SDV1.6 FishPond：fishType=NetString合格ID/FishCount属性/容量=maxOccupants/
#   任务=neededItem(NetRef<Item>)+neededItemCount/完成=hasCompletedRequest/产出=output(NetRef<Item>)/天数=daysSinceSpawn）
# 放鱼/喂食/领产出走 /interact 鱼塘 bypass（游戏原生 doAction 处理，不手动复制消耗逻辑）


def _pond_scan() -> list:
    """查所有鱼塘状态（/fish_pond list；新 DLL 端点）。失败返回 []。"""
    try:
        r = api._post("/fish_pond", {"action": "list"})
        return (r.get("ponds") or []) if r.get("ok") else []
    except Exception:
        return []


def _pond_display(p: dict) -> str:
    """单座鱼塘状态行。"""
    fish = p.get("fish") or "空"
    cnt = p.get("count", 0)
    cap = p.get("capacity", 0)
    parts = [f"🐟 {fish} {cnt}/{cap}"]
    if p.get("questItem") and not p.get("questDone"):
        qc = p.get("questCount", 0) or 1
        parts.append(f"📋 任务: 喂 {p['questItem']}x{qc}（pond feed）")
    if p.get("output"):
        parts.append(f"🎁 产出: {p['output']}（pond collect 领）")
    if p.get("daysSinceSpawn") is not None:
        parts.append(f"⏳ {p['daysSinceSpawn']}天")
    return f"({p.get('x')},{p.get('y')}) " + " · ".join(parts)


def _pond_list() -> str:
    ponds = _pond_scan()
    if not ponds:
        return "🐟 农场没有鱼塘（找罗宾建 Fish Pond）"
    lines = [f"🐟 鱼塘 {len(ponds)} 座:"]
    for p in ponds:
        lines.append("  " + _pond_display(p))
    lines.append("操作: add(放鱼) / feed(喂任务) / collect(领产出) / fish(钓) / status(x,y)")
    return "\n".join(lines)


def _pond_find(px: int = -1, py: int = -1) -> dict:
    """找鱼塘：指定坐标优先，否则第一座。"""
    ponds = _pond_scan()
    if not ponds:
        return None
    if px >= 0:
        for p in ponds:
            if int(p.get("x", -1)) == px and int(p.get("y", -1)) == py:
                return p
    return ponds[0]


def _pond_status(x: int = -1, y: int = -1) -> str:
    """指定鱼塘状态。"""
    if x < 0:
        return _pond_list()
    p = _pond_find(x, y)
    if not p:
        return "❌ 该坐标没有鱼塘"
    return "🐟 " + _pond_display(p)


def _pond_do_interact(p: dict) -> str:
    """走到鱼塘旁 → /interact 鱼塘瓦片（新 DLL bypass；放鱼/喂食/领产出都走它）。
    鱼塘 5x5，站东侧中间 (px+5,py+2) 朝左，交互右缘 (px+4,py+2)。"""
    px, py = int(p["x"]), int(p["y"])
    tx, ty = px + 4, py + 2
    try:
        api._post("/walk_to", {"location": "Farm", "x": tx + 1, "y": ty})
        _wait_arrival("Farm", tx + 1, ty, timeout=15)
        api._post("/face", {"direction": 3})
        time.sleep(0.3)
    except Exception:
        pass
    r = api._post("/interact", {"x": tx, "y": ty})
    acted = r.get("actionTriggered")
    fp = r.get("fishPond") or ""
    return f"🐟 交互鱼塘({px},{py}): triggered={acted} fishPond={fp}"


def _pond_add(item: str, x: int = -1, y: int = -1) -> str:
    """手持鱼放进去：选鱼 → 交互鱼塘 → 验证数量增加。"""
    p = _pond_find(x, y)
    if not p:
        return "❌ 农场没有鱼塘"
    before = p.get("count", 0)
    try:
        api.select(item)
        time.sleep(0.5)
    except Exception as e:
        return f"❌ 选中鱼失败: {e}"
    msg = _pond_do_interact(p)
    after = _pond_find(p["x"], p["y"]) or {}
    if after.get("count", 0) > before:
        return f"{msg}\n✅ 鱼已放进去：{after.get('fish')} {after.get('count')}/{after.get('capacity')}"
    return f"{msg}\n⚠️ 鱼没放进去——可能鱼种不符/满了/没选中（先 pond list 看塘里啥鱼，鱼塘放鱼只能放同种）"


def _pond_feed(x: int = -1, y: int = -1) -> str:
    """喂任务物品：从状态拿任务物品 → 选中 → 交互鱼塘 → 验证 questDone。"""
    p = _pond_find(x, y)
    if not p:
        return "❌ 农场没有鱼塘"
    if not p.get("questItem") or p.get("questDone"):
        return "🐟 这座鱼塘没有待喂任务（或已完成）"
    item = p["questItem"]
    try:
        api.select(item)
        time.sleep(0.5)
    except Exception as e:
        return f"❌ 选中任务物品失败: {e}"
    msg = _pond_do_interact(p)
    after = _pond_find(p["x"], p["y"]) or {}
    if after.get("questDone"):
        return f"{msg}\n✅ 已喂 {item}，鱼塘任务完成！会扩容/升级"
    return f"{msg}\n⚠️ 任务物品没喂进去（可能背包没有 {item}）"


def _pond_collect(x: int = -1, y: int = -1) -> str:
    """领鱼籽产出：空手/非鱼 交互鱼塘（游戏 doAction 检测产出→进背包）。"""
    p = _pond_find(x, y)
    if not p:
        return "❌ 农场没有鱼塘"
    if not p.get("output"):
        return "🐟 这座鱼塘还没有产出"
    msg = _pond_do_interact(p)
    after = _pond_find(p["x"], p["y"]) or {}
    if not after.get("output"):
        return f"{msg}\n✅ 已领产出（背包收下）"
    return f"{msg}\n⚠️ 产出没领到（可能背包满了）"


def _pond_fish(x: int = -1, y: int = -1) -> str:
    """🎣 服务端直钓（鱼塘不用钓鱼小游戏/竿）：走到塘边 → /fish_pond action=fish → CatchFish 直接给鱼。
    ⚠️ 鱼塘拿鱼=服务端 CatchFish（免钓鱼），不碰竿/浮漂——钓鱼小游戏是野外/节日的事（见 festival）。"""
    p = _pond_find(x, y)
    if not p:
        return "❌ 农场没有鱼塘"
    px, py = int(p["x"]), int(p["y"])
    tx, ty = px + 4, py + 2
    try:
        # 走到塘边（东侧，面向塘水）
        api._post("/walk_to", {"location": "Farm", "x": tx + 1, "y": ty})
        _wait_arrival("Farm", tx + 1, ty, timeout=15)
        api._post("/face", {"direction": 3})
        time.sleep(0.3)
    except Exception:
        pass
    try:
        r = api._post("/fish_pond", {"x": x, "y": y, "action": "fish"})
        if not r.get("ok"):
            return f"❌ {r.get('error', '钓失败')}"
        pond_after = r.get("pond") or {}
        return f"🎣 已钓出 {r.get('fish')} → 背包（塘剩 {pond_after.get('count')} 条 {pond_after.get('fish')}）"
    except Exception as e:
        return f"❌ {e}"


# ═══════════════════════════════════════════
#  🦀 蟹笼（2026-08-16 恒给了蟹笼×22+鱼饵×215；需新 DLL /water 找水）
# ═══════════════════════════════════════════
# 放置机制（实测）：选蟹笼 → 站水边陆地块 → 面朝水 → /use（placementAction）→ 放进水里。
# 水检测走 /water 端点（isWaterTile，鱼塘不算——那是建筑盖在陆地上）；水边陆地=邻居非水+可通行。
_CRAB_FACE = [(0, -1, 2), (0, 1, 0), (1, 0, 3), (-1, 0, 1)]  # 上/下/右/左 → 站在水邻居时的朝向


def _crab_water_at(cx: int, cy: int, radius: int = 15) -> list:
    """以指定瓦片为中心找水（/water 端点，需新 DLL；淘金/蟹笼共用）。"""
    try:
        r = api._get("/water", {"x": cx, "y": cy, "radius": radius})
        return (r.get("water") or []) if r.get("ok") else []
    except Exception:
        return []


def _crab_water(radius: int = 15) -> list:
    """找当前地点半径内的水瓦片（= _crab_water_at 以玩家为中心，2026-08-29 泛化）。"""
    try:
        st = api.state()
        px = (st.get("player") or {}).get("x") or 0
        py = (st.get("player") or {}).get("y") or 0
        return _crab_water_at(px, py, radius)
    except Exception:
        return []


def _crab_find_edges(radius: int = 15) -> list:
    """水边可站边：返回 [(站x, 站y, 朝水face, 水x, 水y), ...]。
    ⚠️ 2026-08-30 恒铁律(同淘金)：站格=**纯陆地岸上格**(不 allowWater，排掉水格)，先走到岸上。
    只在 **canCrabPot=true** 的水格旁放(/water 已预筛)——放进宽水域一次成功零试错；
    /use 传 x,y 精准远程放到水格(AI 站岸上即可)。放完就在这岸格，不去爬水。"""
    water = _crab_water(radius)
    if not water:
        return []
    # ✅ 只挑能放蟹笼的水格(宽水域：左右都是水或上下都是水，无建筑挡)——零试错关键
    water = [w for w in water if w.get("canCrabPot")]
    water_set = {(w["x"], w["y"]) for w in water}
    edges = []
    for w in water:
        wx, wy = w["x"], w["y"]
        for dx, dy, face in _CRAB_FACE:
            sx, sy = wx + dx, wy + dy
            if (sx, sy) in water_set:
                continue  # 纯水格(无陆地可站)——只要岸上格
            try:
                # ✅ 不 allowWater：只认**纯陆地可走格**(排掉水格)，AI 站在岸上
                if not api._post("/passable", {"x": sx, "y": sy}).get("passable"):
                    continue
            except Exception:
                continue
            edges.append((sx, sy, face, wx, wy))
    return edges


def _pan_stands(gx: int, gy: int, radius: int = 3) -> list:
    """🪙 闪光点邻岸可站格：返回 [(站x, 站y, 朝水face, 闪x, 闪y, dist), ...] 按距闪点最近优先。
    2026-08-30 恒铁律：会【下水】到贴近闪点的格子淘(不需要全程站水上，是操作格)。所以这里 **allowWater=true**
    放行近水格——选到闪点射程内的最优操作格(曼哈顿≤2)。真正"人站岸上"的岸格由 _pan_shore 单独找。dist=到闪点曼哈顿。"""
    stands = []
    for r in range(1, radius + 1):
        ring = []
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:  # 只扫最外圈这层
                    continue
                sx, sy = gx + dx, gy + dy
                try:
                    # ✅ allowWater=true：放行近水/浅水格——操作格能贴近闪点(下水淘合法)
                    if not api._post("/passable", {"x": sx, "y": sy, "allowWater": True}).get("passable"):
                        continue
                except Exception:
                    continue
                # 面朝闪点的最近正方向(0上/1右/2下/3左)——淘金靠包围盒相交不依赖朝向，只作拟人走位用
                face = 2 if dy > 0 else 0 if dy < 0 else (1 if dx > 0 else 3)
                if dy < 0 or (dy == 0 and dx < 0):
                    face = 0 if dy < 0 else 3
                elif dy > 0:
                    face = 2
                else:
                    face = 1
                ring.append((sx, sy, face, gx, gy, abs(dx) + abs(dy)))
        ring.sort(key=lambda t: t[5])
        stands.extend(ring)
    return stands


def _crab_water_report(radius: int = 15) -> str:
    """找附近水瓦片并汇报（河/湖位置）。"""
    water = _crab_water(radius)
    if not water:
        return "❌ 附近没找到水（/water 需新 DLL；鱼塘不算蟹笼水）"
    lines = [f"🌊 附近水瓦片 {len(water)} 个（半径 {radius}）:"]
    for w in water[:12]:
        lines.append(f"  ({w['x']},{w['y']})")
    if len(water) > 12:
        lines.append(f"  … 共 {len(water)} 个")
    lines.append("放蟹笼→crab_pot place")
    return "\n".join(lines)


def _crab_cur_loc() -> str:
    try:
        return api.state().get("location", {}).get("name", "Farm")
    except Exception:
        return "Farm"


def _crab_place(count: int = 5, radius: int = 15, bait: str = "Bait") -> str:
    """沿水边放蟹笼：找水边陆地 → 逐格选蟹笼走位朝水 /use 放置，放完换手持饵，回近水点岸边站定。
    ⚠️ 2026-08-29 恒改：**不要从老远 walk_to 再来、放完又飞回老远处**——只走到**最近的近水点**，
    放蟹笼→放饵→**回近水点旁的岸格站定**。缺蟹笼/鱼饵直接报错。
    bait= 选鱼饵(默认普通鱼饵"Bait")；蟹笼能用:鱼饵/磁铁❌(那是钓具非饵)/野钓饵(774)/豪华鱼饵。"""
    loc = _crab_cur_loc()
    if not api.has_item("Crab Pot"):
        return "❌ 没有蟹笼(背包没有 Crab Pot)！先去买/造蟹笼再来"
    edges = _crab_find_edges(radius)
    if not edges:
        return "❌ 附近没找到水边可站的地方（/water 报 0 水格？鱼塘不算蟹笼水）"
    # 📍 选"最近"近水点：按距玩家最近的站格优先(不是明明很多水却选大老远)——2026-08-29 恒
    try:
        st = api.state()
        px = (st.get("player") or {}).get("x") or 0
        py = (st.get("player") or {}).get("y") or 0
        edges.sort(key=lambda e: (e[0] - px) ** 2 + (e[1] - py) ** 2)
    except Exception:
        pass
    placed = 0
    log = [f"🦀 找到 {len(edges)} 个水边点，放 {min(count, len(edges))} 个蟹笼:"]
    planted = []   # 实放成功的 (原站格sx,sy,face,水x,水y) — 挂饵回原站格用
    for sx, sy, face, wx, wy in edges:
        if placed >= count:
            break
        try:
            api.select("Crab Pot")
            time.sleep(0.3)
            # ✅ 2026-08-30 恒铁律：站格=纯陆地岸上格(不 allowWater)，走到岸上
            api._post("/walk_to", {"location": loc, "x": sx, "y": sy})
            arrived = _wait_arrival(loc, sx, sy, timeout=15)
            # ⛔ 2026-08-30 恒防搁浅：**必须真站上这格才放**——走位失败(被挡/到不了)就跳过，
            #    绝不强放一个"旁边站不上人"的水潭笼，否则又变没人够得着的垃圾。
            if not arrived:
                log.append(f"  ⚠️ 岸格({sx},{sy})走不到,跳过 ({wx},{wy}) 不放(防搁浅)")
                continue
            api._post("/face", {"direction": face})
            time.sleep(0.3)
            # ✅ 2026-08-30 恒铁律：/use 传 x,y 精准远程放指定水格，一次成功零试错(操控无视水格)
            r = api._post("/use", {"force": True, "x": wx, "y": wy})
            if r.get("ok"):
                placed += 1
                planted.append((sx, sy, face, wx, wy))
                log.append(f"  ✓ 放 ({wx},{wy})")
            else:
                log.append(f"  ✗ ({wx},{wy}) {r.get('error') or ''}")
        except Exception as e:
            log.append(f"  ✗ ({wx},{wy}) {e}")
        time.sleep(0.4)
    # 📍 放完**换手持放饵**——只针对实放成功的笼。挂饵站**笼旁原岸格 (sx,sy)**(不 allowWater)，面朝笼 interact。
    if placed:
        if api.has_item(bait):
            try:
                api.select(bait)
                time.sleep(0.4)
                for sx, sy, face, wx, wy in planted:
                    try:
                        # 回到放笼时的纯陆地岸格(就在笼近旁)，面朝笼挂饵
                        api._post("/walk_to", {"location": loc, "x": sx, "y": sy})
                        _wait_arrival(loc, sx, sy, timeout=15)
                        api._post("/face", {"direction": face})
                        time.sleep(0.2)
                        r = api._post("/interact", {"x": wx, "y": wy})
                        if r.get("ok"):
                            log.append(f"  🎣 放饵 ({wx},{wy})")
                        else:
                            log.append(f"  🎣 放饵失败 ({wx},{wy}) {r.get('error') or ''}")
                    except Exception as e:
                        log.append(f"  🎣 放饵异常 ({wx},{wy}) {e}")
            except Exception:
                log.append("⚠️ 放饵出错")
        else:
            log.append("⚠️ 没带鱼饵，只放了笼没放饵（放饵→crab_pot bait）")
    # 📍 2026-08-30 恒铁律：做完行为**回到岸上格**——站格本就是纯陆地岸格，挂饵时也已回原站格，原地即它，不再额外走位。
    if planted:
        ax, ay = planted[-1][0], planted[-1][1]
        log.append(f"↩️ 已回近水点岸格 ({ax},{ay}) 站定")
    log.append(f"📦 放好 {placed} 个(含放饵)；过夜出货，早上 crab_pot collect 收")
    return "\n".join(log)


# ═══════════════════════════════════════════
#  🪙 淘金/淘盘(铜锅)（2026-08-29 恒：找到水下闪光点；反编译 StardewValley.Tools.Pan 破译）
# ═══════════════════════════════════════════
def _pan_run(dry_run: bool = False, radius: int = 3, timeout: int = 20) -> str:
    """🪙 淘金(铜锅/淘盘)：找当前图水下闪光点 → 走近岸上格→下水淘→回岸上格。scene 域。
    ⭐ 恒的铁律(2026-08-30，反复确认)：人假设在(1,11)，中间水域，闪点在(0,5)。
       流程 = ①walk_to 走到离闪点近的**岸上格**(1,5) ②为了够到闪点会**下水**到(0,5) ③淘金 ④**回到岸上格(1,5)**。
       核心：最后一定回【出发的那个岸上格】，不是对岸、不是最近可走格。"""
    try:
        st = api.state()
    except Exception as e:
        return f"❌ 淘金: 读状态失败 {e}"
    ore = (st.get("player") or {}).get("orePan") or {}
    if not ore.get("hasGlint"):
        return "❌ 本图没有水下闪光点(需完成社区中心鱼缸+靠近水边才刷新)"
    if not ore.get("hasPan"):
        return "❌ 没有铜锅(淘金盘)——背包/手上都没有 Pan"
    loc = (st.get("location") or {}).get("name", "Farm")
    gx, gy = ore.get("x"), ore.get("y")
    if gx is None or gy is None:
        return "❌ 闪光点坐标缺失，重查 /state.orePan"
    if dry_run:
        return f"🪙 本图水下有闪光点 ({gx},{gy})，你带了铜锅(升级{ore.get('panUpgrade')})。淘→ scene ops=pan"

    # ── ① 找离闪点最近的【可站格】(允许下水 allowWater 才能贴近闪点；含 岸上格 + 近水操作格)──
    stands = _pan_stands(gx, gy, radius=radius)
    if not stands:
        return f"⚠️ 闪光点 ({gx},{gy}) 对岸没找到可走格，走近点重试(radius={radius})"
    in_range = [t for t in stands if t[5] <= 2]
    if not in_range:
        return f"⚠️ 闪光点 ({gx},{gy}) 藏水中、距最近可走格 {stands[0][5]} 超铜锅射程(±2)——等换个闪点"
    stands = in_range
    # ⭐ 玩家淘前位置 stx,sty（回岸格要回这一岸，别跳对岸）——在任何可能异常前先兜底取好
    try:
        _ps = (st.get("player") or {})
        stx, sty = _ps.get("x") or 0, _ps.get("y") or 0
    except Exception:
        stx, sty = 0, 0
    # 排序：先近闪点(dist 小)，同距再近玩家
    try:
        stands.sort(key=lambda t: (t[5], (t[0] - stx) ** 2 + (t[1] - sty) ** 2))
    except Exception:
        pass
    opx, opy, face, wx, wy, dist = stands[0]   # 下水操作格(贴近闪点)

    # ── ② 找【纯陆地岸上格】(人下水前站、淘完也要回的那格)——⭐优先回"玩家淘前所在的那一岸" ═─
    shore = _pan_shore(opx, opy, gx, gy, stx, sty, radius=radius)
    if not shore:
        return f"⚠️ 闪点 ({gx},{gy}) 附近找不到岸上格里，无法靠近"
    mx, my = shore   # 岸上格(M)，这是"回"，不是站水上跳对岸

    # ── ③ 先走位到岸上格 (M) ──
    try:
        api._post("/walk_to", {"location": loc, "x": mx, "y": my})
        if not _wait_arrival(loc, mx, my, timeout=timeout):
            return f"⚠️ 走位超时(到 {loc} {mx},{my})——可能对岸被挡，换个可走格"
    except Exception as e:
        return f"❌ 走位出错 {e}"

    # ── ④ 下水到操作格 (贴着闪点)——⭐水下用 /position 瞬移(水里走位会卡)，不是 walk_to ──
    try:
        api._post("/position", {"x": opx, "y": opy})
        time.sleep(0.4)
    except Exception as e:
        return f"❌ 下水出错 {e}"

    api._post("/face", {"direction": face})
    try:
        api.select("Copper Pan")
    except Exception:
        pass
    time.sleep(0.3)

    r = api._post("/pan", {}, timeout=60)
    if not r.get("ok"):
        # ⚠️ 淘失败了也要 position 回岸，别留在水里
        try:
            api._post("/position", {"x": mx, "y": my})
        except Exception:
            pass
        return f"❌ 淘金失败: {r.get('error', '')}"

    # ── ⑤ 淘完【position 瞬移回岸上格 (M)】——恒铁律核心：从起始位走位到近岸格，淘金/回程用 position ──
    msg = f"🪙 淘金成功！position 下水点 ({opx},{opy}) 淘(闪点 {gx},{gy})，TimesPanned={r.get('timesPanned')}"
    if r.get("bagFull"):
        msg += "\n📦 背包满了！掉落进了取出菜单 → menu read / menu click 领一下，别丢"
    if r.get("glintCleared"):
        msg += "；闪光点已淘清"
    try:
        api._post("/position", {"x": mx, "y": my})
        time.sleep(0.4)
        msg += f"；已 position 回近岸格 ({mx},{my}) 站定"
    except Exception:
        msg += f"；⚠️ 回岸({mx},{my})失败"
    return msg


def _pan_shore(opx: int, opy: int, gx: int, gy: int, px: int, py: int, radius: int = 3):
    """🪙 从下水操作格 (opx,opy) 找**纯陆地岸上格**(M)——淘完要回这格。
    2026-08-30 恒铁律：人先到这岸格、下水淘、淘完回【这个岸格】。只认纯陆地(/passable 不 allowWater)。
    排序优先级(恒怕"跳对岸")：①离玩家淘前位置近(回自己出发那一岸) ②离闪点近(够得着)。
    环形扩散半径内挑最优，回 None=没岸格。"""
    best = None
    best_key = None
    for r in range(1, radius + 3):   # radius+3 放宽，闪点藏水深处岸格可能远一些
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                tx, ty = opx + dx, opy + dy
                try:
                    # 纯陆地可走格(不 allowWater，排掉水格)——岸上格
                    if not api._post("/passable", {"x": tx, "y": ty}).get("passable"):
                        continue
                except Exception:
                    continue
                # ⭐ 关键：优先回【玩家淘前所在的那一岸】(离 px,py 近)，其次离闪点近——绝不跳对岸
                key = (abs(tx - px) + abs(ty - py), abs(tx - gx) + abs(ty - gy))
                if best_key is None or key < best_key:
                    best_key = key
                    best = (tx, ty)
        if best:
            return best
    return None


def _crab_scan_placed() -> list:
    """扫当前地点已放置的蟹笼（/surroundings 报 Crab Pot object）。"""
    try:
        st = api.state()
        px = (st.get("player") or {}).get("x") or 0
        py = (st.get("player") or {}).get("y") or 0
        sur = api._get("/surroundings", {"x": px, "y": py, "radius": 20})
        return [(t["x"], t["y"]) for t in (sur.get("tiles") or [])
                if (t.get("object") or "") == "Crab Pot"]
    except Exception:
        return []


def _crab_stand_for(wx: int, wy: int):
    """🐟 找蟹笼 (wx,wy) 旁**纯陆地可站格**——任意朝向的岸都能站，不再写死"笼南边+朝北"。
    _CRAB_FACE=上/下/右/左，站格非水(/passable 不 allowWater 排掉水格)、面朝笼。找不到返回 None。"""
    for dx, dy, face in _CRAB_FACE:
        sx, sy = wx + dx, wy + dy
        try:
            if api._post("/passable", {"x": sx, "y": sy}).get("passable"):
                return (sx, sy, face)
        except Exception:
            continue
    return None


def _crab_bait(bait: str = "Bait") -> str:
    """给已放置的蟹笼放饵（选 bait → 逐个交互）。
    ⚠️ 2026-08-16 实测：走位可能重置选中 → 每笼前重新 select bait。
    ⚠️ 2026-08-30 修：不再写死 (wx,wy+1)+朝北，用 _crab_stand_for 找真实岸格(任意朝向)。
    走位超时也继续发 interact——/interact 是坐标定位(距离无关)，不必完美站格。
    bait= 选鱼饵(默认普通鱼饵"Bait")；蟹笼只能用 Category -21 的饵(鱼饵/野钓饵/豪华鱼饵)。"""
    loc = _crab_cur_loc()
    pots = _crab_scan_placed()
    if not pots:
        return "❌ 附近没找到已放的蟹笼"
    baited = 0
    log = [f"🦀 给 {len(pots)} 个蟹笼放饵:"]
    for wx, wy in pots:
        st = _crab_stand_for(wx, wy)
        if not st:
            log.append(f"  ✗ ({wx},{wy}) 找不到旁侧可站岸格")
            continue
        sx, sy, face = st
        try:
            api.select(bait)
            time.sleep(0.4)
            # 走位尽力而为：超时/异常不中断，interact 按坐标仍能触发
            try:
                api._post("/walk_to", {"location": loc, "x": sx, "y": sy})
                _wait_arrival(loc, sx, sy, timeout=15)
                api._post("/face", {"direction": face})
                time.sleep(0.3)
            except Exception:
                pass  # 站不好继续，interact 是坐标定位
            r = api._post("/interact", {"x": wx, "y": wy})
            ok = r.get("ok") and (r.get("actionTriggered") or r.get("crabPot"))
            baited += 1 if ok else 0
            log.append(f"  {('✓' if ok else '✗')} ({wx},{wy})" + ("" if ok else f" {r.get('error') or ''}"))
        except Exception as e:
            log.append(f"  ✗ ({wx},{wy}) {e}")
        time.sleep(0.4)
    log.append(f"🎣 成功挂饵 {baited}/{len(pots)}；过夜就出货(蟹/贝壳/垃圾)，早上 crab_pot collect 收")
    return "\n".join(log)


def _crab_collect() -> str:
    """收蟹笼产出（空手逐个交互；收了会出空笼，需要再放饵）。
    ⚠️ 2026-08-30 修：不再写死 (wx,wy+1)+朝北，用 _crab_stand_for 找真实岸格。"""
    loc = _crab_cur_loc()
    pots = _crab_scan_placed()
    if not pots:
        return "❌ 附近没找到已放的蟹笼"
    got = 0
    log = [f"🦀 收 {len(pots)} 个蟹笼:"]
    for wx, wy in pots:
        st = _crab_stand_for(wx, wy)
        if not st:
            log.append(f"  ✗ ({wx},{wy}) 找不到旁侧可站岸格")
            continue
        sx, sy, face = st
        try:
            # 走位尽力而为；interact 坐标定位，不依赖完美站格
            try:
                api._post("/walk_to", {"location": loc, "x": sx, "y": sy})
                _wait_arrival(loc, sx, sy, timeout=15)
                api._post("/face", {"direction": face})
                time.sleep(0.3)
            except Exception:
                pass
            api._post("/interact", {"x": wx, "y": wy})
            got += 1
        except Exception as e:
            log.append(f"  ✗ ({wx},{wy}) {e}")
        time.sleep(0.4)
    log.append(f"📦 交互了 {got} 个（产出进背包；空笼要重新放饵）")
    return "\n".join(log)


@mcp.tool()
def _crab_status() -> str:
    """🦀 蟹笼概览：已放笼子 + 背包蟹笼/饵数量。"""
    try:
        pots = _crab_scan_placed()
        st = api.state()
        inv = st.get("inventory") or []
        cp = sum(i["stack"] for i in inv if "Crab Pot" in (i.get("name") or ""))
        bt = sum(i["stack"] for i in inv if "Bait" in (i.get("name") or ""))
        lines = [f"🦀 已放蟹笼 {len(pots)} 个: {pots[:8]}{'…' if len(pots) > 8 else ''}" if pots else "🦀 还没有放蟹笼"]
        lines.append(f"🎒 背包: 蟹笼×{cp} 饵×{bt}")
        lines.append("放→fish(放笼) / 挂饵→fish(放饵) / 收→fish(收笼)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


def _crab_pots_scan(location: str = None) -> list:
    """🦀 拉当前/指定图所有蟹笼的真实状态（/crab_pots 端点，需新 DLL）。"""
    try:
        params = {"location": location} if location else {}
        r = api._get("/crab_pots", params)
        return (r.get("pots") or []) if r.get("ok") else []
    except Exception:
        return []


def _crab_diag(location: str = None) -> str:
    """🦀 蟹笼诊断（2026-08-30 恒：定位"放得进却挂不了饵"）——逐笼报真实类型+位置合法性+挂饵/产出状态。
    看 isCrabPotInstance(真 CrabPot 实例?) / tileIsWater+flankedWater(在不在合法鱼水) /
    bait(空/已挂)+readyForHarvest(出没出货)。wouldAcceptNewPot 仅参考：笼占据该格时恒 false。"""
    pots = _crab_pots_scan(location)
    if not pots:
        return "❌ 当前图没扫到蟹笼（location 可指定其它图；需新 DLL /crab_pots）"
    loc_name = location or _crab_cur_loc()
    lines = [f"🦀 {loc_name} 蟹笼 {len(pots)} 只:"]
    bad = 0
    for p in pots:
        flag = []
        if not p.get("isCrabPotInstance"):
            flag.append("非CrabPot实例(普通Object710?)")
        if not p.get("tileIsWater"):
            flag.append("不在水格!")
        if not p.get("flankedWater"):
            flag.append("非宽水域!")
        bait = p.get("bait")
        ready = p.get("readyForHarvest")
        if bait:
            state = f"已挂饵[{bait}]"
            if not ready:
                state += "未出货"
                flag.append("挂饵未出货")
        elif ready:
            state = "已出货"
        else:
            state = "空笼未挂饵"
        mark = " ⚠️" + "/".join(flag) if flag else ""
        lines.append(f"  ({p['x']},{p['y']}) 真笼={p.get('isCrabPotInstance')} 水={p.get('tileIsWater')} 宽水={p.get('flankedWater')} {state}{mark}")
        if flag:
            bad += 1
    if bad:
        lines.append(f"⚠️ {bad}/{len(pots)} 只有异常（上面带 ⚠️ 的）。修法见诊断结论")
    else:
        lines.append("✅ 类型/位置都正常——问题大概率在『挂饵状态』(bait 非空未 ready)或 AI 站格，配 crab_bait 修复")
    return "\n".join(lines)


def _crab_retract(x=None, y=None, location=None) -> str:
    """🦀 回收蟹笼（2026-08-30 恒：搁浅笼出路，无论继续用/退役都要）——把已放蟹笼确定性收回背包。
    x,y 给了=收这1只；不给=扫当前图全部（location 可跨图指定想收的图）。收产+笼本体，满包走菜单不丢。"""
    pots = []
    if x is not None and y is not None:
        pots = [{"x": int(x), "y": int(y)}]
    else:
        pots = _crab_pots_scan(location)
    if not pots:
        return "❌ 没给坐标，当前图也没扫到蟹笼"
    params_base = {"location": location} if location else {}
    ok = 0
    log = [f"🦀 回收 {len(pots)} 只蟹笼:"]
    for p in pots:
        try:
            body = {"x": p["x"], "y": p["y"]}
            body.update(params_base)
            r = api._post("/crab_retract", body)
            if r.get("ok"):
                ok += 1
                note = ""
                if r.get("outputCollected"): note += " 收产出"
                if not r.get("returnedToInventory"): note += " 满包走菜单"
                log.append(f"  ✓ ({p['x']},{p['y']}) 已收回{note}")
            else:
                log.append(f"  ✗ ({p['x']},{p['y']}) {r.get('error') or ''}")
        except Exception as e:
            log.append(f"  ✗ ({p['x']},{p['y']}) {e}")
        time.sleep(0.4)
    log.append(f"📦 成功 {ok}/{len(pots)}；背包多了 {ok} 只蟹笼（若满包走菜单领）")
    return "\n".join(log)


def _crab_normalize() -> str:
    """🦀 归一背包所有蟹笼为"商店/合成同款"普通蟹笼，一键修"回收蟹笼不堆叠"(2026-09-01)。
    根因：旧版 new CrabPot() 留 CrabPot 子类 vs 玩家原有普通 Object(710) → Item.canStackWith 首行比 GetType() 不堆。
    纯整理不回收任何笼；调用 /crab_retract {normalize:true}（需新 DLL）。"""
    r = api._post("/crab_retract", {"normalize": True})
    if r.get("ok"):
        total, re = r.get("total", 0), r.get("reAdded", 0)
        if total <= 0:
            return "✅ 背包里没有蟹笼，无需归一"
        return f"✅ 背包蟹笼已归一：共 {total} 只 → 重加 {re} 只（{'合并成一组' if r.get('merged') else '异常，未合并'}）"
    return f"❌ 归一失败: {r.get('error') or '?'}"


@mcp.tool()
def _fish_info(location: str) -> str:
    """🐟 查某地能钓什么鱼（含季节/天气条件）。知识源 locations.FISH_KNOWLEDGE。"""
    try:
        k = locations.FISH_KNOWLEDGE.get(location)
        if not k:
            return f"🐟 知识库没有「{location}」的鱼（试试 Beach/Mountain/Forest/Town/Sewer/Woods/Desert）"
        lines = [f"🐟 {location}（{k['水']}）可钓:"]
        for f in k["fish"]:
            cond = f"{f['season']}" + (f" · {f['weather']}" if f["weather"] else "")
            lines.append(f"  · {f['name']}（{cond}）")
        lines.append("💡 去钓用 fish go location=... ")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ 查鱼失败: {e}"


def _fish_all_spots() -> str:
    """列出所有钓点知识（locations.FISH_KNOWLEDGE）。"""
    try:
        lines = ["🐟 钓点知识:"]
        for loc, k in locations.FISH_KNOWLEDGE.items():
            lines.append(f"  · {loc}（{k['水']}）: {', '.join(f['name'] for f in k['fish'])}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


@mcp.tool()
def fish(ops: str = "", kw: dict | None = None) -> str:
    """🎣 钓鱼域（蟹笼并入）：go 去钓(location=) / info 查鱼 / spots 钓点 / bobber 浮漂 / rod 竿(上饵钓具) / crab 蟹笼(place/bait/collect)。⚠️鱼塘在 farm 域。全 → help(fish)。

    """
    dispatch = {
        "go": go_fishing, "fish": go_fishing, "钓": go_fishing,
        "info": _fish_info, "能钓": _fish_info, "鱼": _fish_info, "查": _fish_info,
        "spots": _fish_all_spots, "钓点": _fish_all_spots,
        "bobber": bobber_style, "浮漂": bobber_style, "样式": bobber_style,
        "rod": _rod_cmd, "鱼竿": _rod_cmd,
        # 🦀 蟹笼（2026-08-16 并入：能钓鱼的地方就能放）
        "crab": _crab_status, "蟹笼": _crab_status,
        "crab_water": _crab_water_report, "找水": _crab_water_report,
        "crab_place": _crab_place, "放笼": _crab_place,
        "crab_bait": _crab_bait, "放饵": _crab_bait,
        "crab_collect": _crab_collect, "收笼": _crab_collect,
        "crab_diag": _crab_diag, "诊断笼": _crab_diag,
        "crab_retract": _crab_retract, "回收笼": _crab_retract,
        # 🚫 2026-09-01 恒：crab_normalize/归一笼 退役不暴露(仅清自己存档旧乱堆笼的一次性便利件，端用户遇不到；
        #     修复本身靠回收时归一，`_crab_normalize` 函数留作内部兜底不注册)。
    }
    return _with_state(_ops_run(ops, dispatch, kw))


@mcp.tool()
def social(ops: str = "", kw: dict | None = None) -> str:
    """💬 社交域。ops: chat(跟NPC搭话) gift(送礼 npc_name,item_name) give(给玩家 player_name,item_name)
    send(发消息) emote(表情) friendship(查好感) movie(影院) snack(零食)。细节→help(social)。
    """
    dispatch = {
        "chat": chat_npc, "搭话": chat_npc,
        "gift": gift_npc, "送礼": gift_npc,
        "give": give_item, "给": give_item,
        "send": send_chat, "发": send_chat,
        "emote": emote,
        "friendship": check_friendship, "好感": check_friendship,
        "movie": movie, "电影": movie,
        "snack": snack, "零食": snack,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


def _maze_view(radius: int = 14, gx=None, gy=None) -> str:
    """🗺️ 迷宫视图——把当前地图渲染成 ASCII 棋盘，让 AI 自己推算路线 → walk_to。
    #=墙(不可走) .=可走 P=自己 G=目标(传 gx,gy) O=物体 ¥=资源 T=地形。
    读 /surroundings(capped 30)；墙=passable False，只报墙/物，空地块默认可走。"""
    radius = min(max(int(radius), 1), 30)
    if gx is not None: gx = int(gx)
    if gy is not None: gy = int(gy)
    sur = api.surroundings(radius)
    loc = sur.get("location", "?")
    c = sur.get("center") or {}
    cx, cy = c.get("x"), c.get("y")
    if cx is None or cy is None:
        return "⚠️ 拿不到地图中心，非正常场景？"
    grid = {}
    for t in (sur.get("tiles") or []):
        x, y = t["x"], t["y"]
        if t.get("passable") is False:
            grid[(x, y)] = "#"
        elif t.get("object"):
            grid[(x, y)] = "O"
        elif t.get("resource"):
            grid[(x, y)] = "¥"
        elif t.get("terrain") or t.get("largeTerrain"):
            grid[(x, y)] = "T"
    rows = []
    for ty in range(cy - radius, cy + radius + 1):
        row = []
        for tx in range(cx - radius, cx + radius + 1):
            if (tx, ty) == (cx, cy):
                ch = "P"
            elif gx is not None and gy is not None and (tx, ty) == (gx, gy):
                ch = "G"
            else:
                ch = grid.get((tx, ty), ".")
            row.append(ch)
        rows.append("".join(row))
    head = f"🗺️ 迷宫视图 {loc} 你=({cx},{cy}) r={radius}"
    if gx is not None and gy is not None:
        head += f" 目标=({gx},{gy})"
    return head + "\n" + "\n".join(rows)


def _maze_seg_view(gx=None, gy=None, radius=15) -> str:
    """🧩 迷宫"走法链"——把可走格拆成直走廊列表，并拼 你→目标 的多段直线链（AI 自己按段 walk_to）。
    优先 /passable_rect（整迷宫一次取全，需新 DLL），退回 /surroundings。共用 scripts/maze_seg.py 的 compile_chain。
    参数：gx,gy=目标格；radius=退回用视野半径。"""
    try:
        from maze_seg import compile_chain
    except Exception as e:
        return f"⚠️ maze_seg 导入失败: {e}"
    if gx is None: gx = 63
    if gy is None: gy = 16
    try:
        p = (api.state() or {}).get("player", {})
        cx, cy = p.get("x"), p.get("y")
        PAD = 20   # 同 maze_seg：放宽防止走法链用到 bbox 外走廊被裁断
        minX = min(cx, gx) - PAD; maxX = max(cx, gx) + PAD
        minY = min(cy, gy) - PAD; maxY = max(cy, gy) + PAD
        walk, src = None, ""
        try:
            r = api._get(f"/passable_rect?x1={minX}&y1={minY}&x2={maxX}&y2={maxY}")
            if r.get("ok"):
                src = "passable_rect"
                walk = {(t["x"], t["y"]) for t in r.get("tiles", []) if t.get("passable") is True}
        except Exception:
            pass
        if walk is None:
            # ⚠️ 退回 surroundings：它只报墙/物体格，可走格=方形减去墙/物体（同 _maze_view 的 . 推断）
            src = "surroundings(退回)"
            sur = api.surroundings(radius)
            c = sur.get("center") or {}; cx, cy = c.get("x"), c.get("y")
            rw = {(t["x"], t["y"]) for t in sur.get("tiles", []) if t.get("passable") is False}
            objs = {(t["x"], t["y"]) for t in sur.get("tiles", [])
                    if t.get("object") or t.get("terrain") or t.get("resource") or t.get("largeTerrain")}
            walk = set()
            for tx in range(cx - radius, cx + radius + 1):
                for ty in range(cy - radius, cy + radius + 1):
                    if (tx, ty) not in rw and (tx, ty) not in objs:
                        walk.add((tx, ty))
        return (f"[maze_seg] 你=({cx},{cy}) 目标=({gx},{gy}) 数据源={src} 可走格={len(walk)}\n"
                + compile_chain(walk, cx, cy, gx, gy))
    except Exception as e:
        return f"⚠️ maze_seg 出错: {e}"


@mcp.tool()
def scene(ops: str = "", kw: dict | None = None) -> str:
    """🖱️ 场景交互域：at(点格) / interact(点面前) / use(挥工具) / pickup_scene(捡采集物) / berry(摇浆果) / spot(挖蚯蚓) / moss(苔藓) / place(放置播种) / break(拆敲) / maze。全 ops+坑 → help(scene)。

    """
    dispatch = {
        "at": interact_at, "点": interact_at,
        "front": interact, "面前": interact, "interact": interact,
        "use": use_tool, "挥": use_tool,
        "face": face, "转身": face,
        "select": select_item, "拿": select_item,
        "pickup": furniture_pickup, "拾起": furniture_pickup,
        "pickup_scene": pickup_scene, "捡采集": pickup_scene, "捡物": pickup_scene,
        "berry": berry_run, "摇树莓": berry_run, "浆果": berry_run,
        "spot": spot_run, "挖斑点": spot_run, "挖蚯蚓": spot_run,
        "moss": moss_run, "搜刮苔藓": moss_run, "绿雨": moss_run,
        "rock": rock_dig, "挖石": rock_dig, "敲石": rock_dig, "挖矿点": rock_dig, "采矿点": rock_dig,
        "garbage": trash_run, "翻垃圾桶": trash_run, "翻桶": trash_run, "rummage": trash_run,
        "pan": _pan_run, "淘金": _pan_run, "淘盘": _pan_run, "铜锅": _pan_run, "淘": _pan_run,
        "forge_help": lambda: _with_state(FORGE_GUIDE), "锻造帮助": lambda: _with_state(FORGE_GUIDE),
        "drop": drop_item, "丢": drop_item,
        "furniture": scan_furniture, "家具": scan_furniture,
        "place": place_item, "放": place_item, "放置": place_item,
        "break": break_tile, "拆": break_tile, "敲": break_tile, "敲击": break_tile,
        "maze": _maze_view, "迷宫": _maze_view,
        "maze_seg": _maze_seg_view, "迷宫链": _maze_seg_view, "分段": _maze_seg_view,
        "maze_walk": _maze_walk, "走迷宫": _maze_walk, "迷宫走": _maze_walk,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


def open_questlog() -> str:
    """📜 程序化打开任务日志(QuestLog)：AI 自主看任务/领钱（绕开按键/焦点；2026-08-29 恒）。"""
    try:
        r = api._post("/open_questlog", {})
        if r.get("ok"):
            return _with_state("📜 已打开任务日志，menu read 看任务卡/领奖")
        return _with_state(f"❌ 开日志失败: {r.get('error', '')}")
    except Exception as e:
        return _with_state(f"❌ 开日志出错: {e}")


@mcp.tool()
def menu(ops: str = "", kw: dict | None = None) -> str:
    """📋 界面/菜单域（菜单开着时用）。全 ops + 关键坑 → help(menu)。

    """
    dispatch = {
        "read": read_menu, "看": read_menu, "journal": open_questlog, "日志": open_questlog, "开日志": open_questlog,
        "number": number_select, "数量": number_select, "数量框": number_select,
        "display_fill": _menu_display_fill, "放满": _menu_display_fill, "填槽": _menu_display_fill,
        "display_takeback": _menu_display_takeback, "收好": _menu_display_takeback, "收": _menu_display_takeback,
        "advance": advance_story, "推进": advance_story, "剧情": advance_story,
        "click": menu_click, "点": menu_click,
        "key": press_key, "按键": press_key,
        "cancel": cancel, "取消": cancel, "关": cancel,
        "shop": shop_visit, "逛店": shop_visit,
        "sell": sell_to_shop, "卖": sell_to_shop,
        "bin": sell_to_bin, "出货": sell_to_bin,
        "cook": cook, "做饭": cook,
        "craft": craft, "合成": craft,
        "recipes": list_recipes, "菜谱": list_recipes,
        "craftables": list_craftables, "配方": list_craftables,
        "forge": forge, "锻造": forge,
        "geode": process_geode, "晶球": process_geode,
        "geodes": process_geodes,
        "customize": character_customize, "捏人": character_customize, "起名": character_customize,
        "bundle": bundle_status, "献祭": bundle_status,
        "bundle_kb": bundle_kb, "献祭知识": bundle_kb, "知识库": bundle_kb,
        "donate": museum_donate, "捐": museum_donate, "捐赠": museum_donate,
        "read_book": read_book, "读书": read_book, "读物品": read_book, "读纸条": read_book, "读技能书": read_book,
        # 📖 2026-09-02 恒：quest 域退役并入 menu——特别订单知识库查询（原 quest know）
        "know": calendar_data.special_orders_available, "任务知": calendar_data.special_orders_available,
        "订单知": calendar_data.special_orders_available, "知": calendar_data.special_orders_available,
        # 🧬 2026-08-30 恒：技能升级职业选择(5/10级)——LevelUpMenu.receiveLeftClick 空，/menu click 点不动，
        #    只能走专用 op（镜像 vanilla 公共API）。不带参读选项，side/profession 定分支。
        "levelup_choose": _menu_levelup_choose, "选职业": _menu_levelup_choose, "分支": _menu_levelup_choose, "职业选": _menu_levelup_choose,
        # 🎁🎰 2026-08-26 恒：这三个原本没有任何域 op 可达（domain_selftest 报"功能断档"），
        #    而注入的引导文案却在指挥 AI 直接调 menu_claim_swap/minigame_click——
        #    域模式下这些顶层工具已被隐藏 → AI 照提示调一个不存在的工具，当场卡死。
        #    claim_swap 尤其要命：它出现的时机正是背包已满、必须立刻决策的时候。
        #    归 menu 域的理由：claim_swap 本就是 ItemGrabMenu 操作；赌场小游戏虽是
        #    Game1.currentMinigame 不是 IClickableMenu，但 read_menu 已有回落（无菜单时报小游戏状态），
        #    AI 用统一的 `menu read` 探状态不会踩空，放这里心智负担最小。
        # 🚫 2026-08-28 恒：claim_swap(替换领取)已退役——不稳。改用 click action=discard 丢桶腾格 + action=claim(或 slot) 领；或 button=ok 直接关。
        # "claim_swap": menu_claim_swap, "换领": menu_claim_swap, "替换领取": menu_claim_swap,
        "minigame": minigame_click, "小游戏": minigame_click, "赌场": minigame_click,
        "minigame_state": minigame_state, "小游戏状态": minigame_state,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


@mcp.tool()
def storage(ops: str = "", kw: dict | None = None) -> str:
    """🎒 箱子域。ops: view(看箱,box=N看单箱全清单) store(存:what/items限定存哪些,留空=归位只存已有同类堆,target指定箱/all=True全存腾空间) take(取:x,y+name单箱 或 items批量)
    find(模糊查哪箱有某物) default(设/清默认箱,clear=清) tag(改名,可带color顺带改色)。
    🤖 存取统一走位：store/take 都会先走到相关箱子旁（批量只走到第一个相关箱），不用区分拟人/原子。
    ⭐ 每个箱子前自动带【类目标签】(内容过半自动归类:矿/作物/鱼/种子…)+颜色名,AI 看标签定位,别靠编号逐箱翻。
    细节→help(storage)。
    """
    dispatch = {
        "view": storage_view, "看": storage_view, "看箱": storage_view, "扫": storage_view,
        "store": storage_store, "存": storage_store, "存智能": storage_store, "堆": storage_store,
        "take": storage_take, "取": storage_take, "多取": storage_take, "批量取": storage_take,
        "find": storage_find, "找": storage_find, "搜": storage_find, "查": storage_find,
        "default": storage_default, "默认": storage_default,
        "tag": storage_tag, "标记": storage_tag,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


@mcp.tool()
def daily(ops: str = "", kw: dict | None = None) -> str:
    """🗿 日常域。ops: sleep(睡觉 who) settle(过夜结算) eat(吃食物) wear(穿/脱衣物 name/slot/hand) lie_bed(躺床不过夜)
    heartbeat(心跳间隔) pause(后台不暂停) peek(看恒) appearance(捏脸) whiteboard/wb_read/wb_pin/wb_clear(白板记忆)。细节→help(daily)。
    """
    dispatch = {
        "sleep": go_sleep, "睡": go_sleep,
        "settle": confirm_settlement, "结算": confirm_settlement,
        "eat": eat_item, "吃": eat_item,
        "wear": wear, "穿": wear, "穿戴": wear, "脱": wear,
        "lie_bed": lie_bed, "躺": lie_bed, "躺床": lie_bed,
        "heartbeat": set_heartbeat_interval, "心跳": set_heartbeat_interval,
        "pause": set_pause, "暂停": set_pause,
        "peek": peek_player, "看恒": peek_player,
        "whiteboard": whiteboard_write, "白板": whiteboard_write, "写白板": whiteboard_write,
        "wb_read": whiteboard_read, "看白板": whiteboard_read,
        "wb_pin": whiteboard_pin, "钉白板": whiteboard_pin,
        "wb_clear": whiteboard_clear, "清白板": whiteboard_clear,
        "appearance": set_appearance, "捏脸": set_appearance,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


@mcp.tool()
def map(ops: str = "", kw: dict | None = None) -> str:
    """🗺️ 地图导航域（跨图唯一入口）。ops: lookup(查地点) query(功能反查) go(走到目标,跨图唯一入口)
    walk(走到POI,同图) movetile(同图走瓦片) npc(找NPC) warp_safe(紧急逃脱)。⚠️跨图一律 go。
    ⚠️参数都放 kw 对象（别拼进 ops 串）：go kw={"destination":"地点名或POI"} / walk kw={"poi_name":"POI"} /
    movetile kw={"x":int,"y":int}。细节→help(map)。
    """
    dispatch = {
        "lookup": map_lookup, "查": map_lookup,
        "query": map_query, "反查": map_query,
        "go": map_go, "走": map_go,
        "walk": walk_to, "走到": walk_to,
        "movetile": move_to_tile, "走格": move_to_tile,
        "npc": find_npc, "找人": find_npc,
        "warp_safe": warp_safe, "逃脱": warp_safe,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


# ═══════════════════════════════════════════
#  🎪 节日域（A2 第13域，2026-08-14 roadmap #2）
# ═══════════════════════════════════════════

# 已提醒"明天有节日"的日期（只提醒一次；用 dict 可变，省 global）
_FEST_REMIND_KEY = {"last": None}
_SETTLE_REMIND_KEY = {"last": None}   # 过夜结算广播去重（2026-08-15 恒：走游戏广播提醒复盘+白板）
_TU_REMIND = {"last": None}           # 铁匠铺"待取"去重（2026-08-22 恒：升级没领前只提一次，别每条返回都提）
# 🧾 结算期 30s 轮询/超时兜底已并入 _chat_phase_line（2026-08-17 恒），原 _SETTLE_CHECK_IN 已移除
_GREENRAIN_KEY = {"last": None}       # 绿雨天提醒去重（2026-08-17 恒：weather=7，一天一次）
_HW_REMIND_KEY = {"last": None}       # 每日求助栏提醒去重（2026-08-29 恒：每天第一次进Town一次）
_CAL_TOWN_KEY = {"last": None}        # 📅 日历(生日/休店)去重（2026-09-04 恒：只每天第一次(TV) + 顶多进Town再弹一次，别每条都弹）
_HW_READY_KEY = {"last": None}        # 每日求助"已集齐"提醒去重（2026-08-29 恒 Part A：集齐未交付提示去交付，一天一次）
_SPECIAL_RW_KEY = {"last": None}      # 特别订单奖励链提醒去重（2026-08-29 恒：接单/完成领奖/兑奖券可拿，到Town每日一次）

# 🌿 绿雨搜刮引导（2026-08-21 恒：像节日引导一样给 AI 推荐路线）：绿雨天逐图 moss_run，转一圈清完。
GREENRAIN_GUIDE = (
    "绿雨搜刮苔藓(Moss)：每站 scene ops=moss 自动清当前图苔藓——苔雨树斧头砍、长苔藓树镰刀刮、"
    "苔藓杂草块(大块挥3/小块挥2)挥到面前格没，砍完树就停。推荐顺序：森林(Forest)→小镇(Town)→山岭(Mountain)→"
    "林间小径(Backwoods，农场上方)——恒 2026-08-21 确认地图名，转一圈逐站 scene ops=moss；也可按方便自定顺序(哪近先去/草密先清)。"
)


def _greenrain_guide_brief() -> str:
    """绿雨搜刮路线简述（注入状态条用，省 token；细节见 GREENRAIN_GUIDE）"""
    return "森林→小镇→山岭→林间小径(农场上方)转一圈，每站 scene ops=moss(顺序可自定)"
# ⚠️ 花舞节 zh 数据 bug 提醒（2026-08-19）已于 2026-08-21 删除——festival bot 全删后实测中文 AI 进场/邀请全正常，
#    广播注入不再需要。跳舞邀请分场景引导已并入 FESTIVAL_GUIDE（邀NPC=裸/interact 开第二次对话，邀玩家=festival dance）。



def _festival_time(detail: str) -> str:
    """从节日 detail 提取时间范围（"小镇，10:00-22:00（迷宫）" → "10:00-22:00"）。"""
    after = detail.split("—")[-1]
    m = re.search(r"\d{1,2}:\d{2}.*?\d{1,2}:\d{2}", after)
    if m:
        return m.group(0)
    m = re.search(r"\d{1,2}:\d{2}", after)
    if m:
        return m.group(0)
    return after.strip()


_FEST_NOW_CACHE = {"t": 0.0, "d": None}   # 2s TTL 缓存（2026-08-19：状态条每轮构建会调多次，别重复打 API）


def _festival_now_data() -> dict:
    """取今天日期（节日数据查询用）。优先 AI 进程(7843)；solo 只开 host(7842) 时回退 host——
    日期/蛋坐标/引导等只读数据不依赖 AI 角色（2026-08-17 恒 solo 测节日）。
    带 2s TTL 缓存——一天内日期只变一次，重复调用直接复用（2026-08-19）。"""
    _c = _FEST_NOW_CACHE
    if _c["d"] and time.time() - _c["t"] < 2.0:
        return _c["d"]
    try:
        s = api._ai_get("/state")
    except Exception:
        s = None
    if not s or not s.get("time"):
        try:
            s = api.host_state()
        except Exception:
            s = {}
    try:
        t = s.get("time", {})
        season = (t.get("season") or "").lower()
        day = int(t.get("dayOfMonth", 1) or 1)
        year = int(t.get("year", 1) or 1)
        _c["d"] = {"ok": True, "season": season, "day": day, "year": year}
    except Exception as e:
        _c["d"] = {"ok": False, "season": "", "day": 1, "year": 1, "error": str(e)}
    _c["t"] = time.time()
    return _c["d"]


# 🎇 节日限定内部图 → 生效日期（2026-08-19 恒：非节日期间在 map/go_to 隐藏）
# POI 的 map 是这些图=节日限定（夜市/潜艇/美人鱼船只在冬15-17，沙漠节场地只在春15-17）
_FEST_SEASON_CN = {"spring": "春", "summer": "夏", "fall": "秋", "winter": "冬"}
_FESTIVAL_ONLY_MAPS = {
    "BeachNightMarket": {("winter", 15), ("winter", 16), ("winter", 17)},
    "Submarine": {("winter", 15), ("winter", 16), ("winter", 17)},
    "MermaidHouse": {("winter", 15), ("winter", 16), ("winter", 17)},
    "DesertFestival": {("spring", 15), ("spring", 16), ("spring", 17)},
}

# 🎪 节日临时图(2026-08-29 恒：玩家被游戏自动传送到这,不在 MAP_LINKS,map_go 会误报"没路径")
# FESTIVAL_LOCATIONS 里的是逻辑场地(Town/Forest/Beach),实际在这几张图上。
_FESTIVAL_TEMP_MAPS = {"Temp", "Forest-IceFestival"}

# 📅 季节→季度序（春0夏1秋2冬3），配合 _date_ordinal 把游戏日期压成单调序数比较
_SEASON_ORD = {"spring": 0, "summer": 1, "fall": 2, "winter": 3}


def _date_ordinal(d: dict) -> int:
    """游戏日期 → 单调序数：(年*4+季序)*28+天-1。跨季/跨年比大小用（unlock 门禁）。"""
    try:
        y = int(d.get("year", 1) or 1)
        s = _SEASON_ORD.get((d.get("season") or "").lower(), 0)
        day = int(d.get("day", 1) or 1)
        return (y * 4 + s) * 28 + (day - 1)
    except Exception:
        return 0


# 🧾 特别任务交付点匹配（2026-08-22 恒：接了对应特别订单才显示；宽泛/内容匹配——questKey 不稳时用任务内容认，如"24个蛋"）
_ORDER_MATCH_CACHE = {"t": 0.0, "d": []}


def _accepted_order_texts() -> list:
    """当前已接取(进行中)特殊订单 → [(requester, 可检索文本)]。带 2s TTL 缓存。
    文本 = requester + description + 各目标 desc（/quest_progress 已解析，含"24个蛋"这类内容）。"""
    c = _ORDER_MATCH_CACHE
    if c["d"] and time.time() - c["t"] < 2.0:
        return c["d"]
    out = []
    try:
        r = api._get("/quest_progress")
        for q in (r.get("quests") or []):
            if q.get("source") != "specialOrders":
                continue
            if (q.get("state") or "") == "Complete":
                continue  # 已完成的不再显示交付点（进行中/其它才显示）
            blob = (q.get("requester") or "") + " " + (q.get("description") or "")
            for o in (q.get("objectives") or []):
                blob += " " + (o.get("description") or "")
            out.append((q.get("requester") or "", blob))
        c["d"] = out
        c["t"] = time.time()
    except Exception:
        c["d"] = []
        c["t"] = time.time()
    return out


def _order_match_active(spec) -> bool:
    """require_order spec 是否命中当前任一已接(进行中)特殊订单。
    spec 形态：
      - str：当 requester 相等 或 文本子串出现
      - list：任一子项命中即 True（或语义）
      - dict：requester=委托人须相等；keywords=全含；any_keywords=任一含
    """
    try:
        orders = _accepted_order_texts()
        if not orders:
            return False
        if isinstance(spec, list):
            return any(_order_match_active(s) for s in spec)
        if isinstance(spec, dict):
            req = spec.get("requester")
            kws_all = spec.get("keywords") or []
            kws_any = spec.get("any_keywords") or []
            for requester, blob in orders:
                if req and requester != req:
                    continue
                low = (requester + " " + blob).lower()
                if kws_all and not all(str(k).lower() in low for k in kws_all):
                    continue
                if kws_any and not any(str(k).lower() in low for k in kws_any):
                    continue
                return True
            return False
        s = str(spec).lower()
        for requester, blob in orders:
            if requester.lower() == s or s in (requester + " " + blob).lower():
                return True
        return False
    except Exception:
        return False


# 📍 特别订单卡名 → 交付点提示（2026-08-29 恒：AI 在任务日志里打开某单，就插这句"交付坐标+操作"）
# 卡名 = /menu read 的 QuestLog 卡 source=specialOrders 的 name(= SpecialOrder.GetName() 订单标题)。
# 提示含：交付点图/坐标 + 站位 + 放物/交互 + button=ok 结算（容/手持两套都说清）。
_DELIVERY_HINT_BY_NAME = {
    "给乔治的礼物":     "📍 交付：带12韭葱**进乔治家(进门)**触发『韭葱惊喜礼物』过场=自动交付(非放箱)；领奖=日志 rewardBox+兑奖券",
    "烈酒":            "📍 交付：潘姆拖车厨房柜 Trailer(10,6)，站(10,7)朝上交互，放12土豆果汁→button=ok 结算",
    "罗宾的项目":      "📍 交付：木匠商店木头堆 ScienceHouse(10,19)，站(10,20)朝上交互，放80硬木→button=ok 结算",
    "社区清理":        "📍 交付：火车站垃圾箱 Railroad(28,36)，站(28,37)朝上交互，放20垃圾(非Joja可乐)→button=ok 结算",
    "四颗宝石":        "📍 交付：齐先生收集箱 QiNutRoom(1,4)，站(1,5)朝上交互，放4五彩碎片→button=ok 结算",
    "齐先生的五彩农场": "📍 交付：齐先生收集箱 QiNutRoom(1,4)，站(1,5)朝上交互，放红橙黄绿蓝紫各100→button=ok 结算",
    "格斯的著名煎蛋卷": "📍 交付：酒吧冰箱 Saloon(18,16)，站(18,17)朝上交互，放蛋→button=ok 结算",
    "需要多汁的虫子":   "📍 交付：鱼店旁虫桶 Beach(37,33)，站(37,34)朝上交互，放虫肉→button=ok 结算",
}


def _delivery_hint(cname: str) -> str:
    """按特别订单卡名找交付点提示；精确/子串匹配（卡名可能带变体）。"""
    if not cname:
        return ""
    cm = str(cname).strip()
    for k, h in _DELIVERY_HINT_BY_NAME.items():
        if cm == k or k in cm or cm in k:
            return h
    return ""


# 🕵️ 齐先生「神秘的齐」纸条链交付提示（2026-08-29 恒：递进链，按 TH_* 进度给"当前步"）
# 卡名 = /menu read 的 QuestLog 卡 source=questLog 的 name(= questTitle)，该链在日志里叫「奇怪纸条」。
# 进度源 = /mail 的 received 里的 TH_Tunnel→TH_Railroad→TH_MayorFridge→TH_SandDragon→TH_LumberPile（重编译 GameLocation 定论）。
_QI_CARD_KEYS = ("奇怪纸条", "神秘的齐", "神奇的齐", "纸条")


def _qi_chain_hint() -> str:
    """按 /mail 的 TH_* 标记算纸条链当前步→返回"下一步去哪"提示；没进度即第一步。"""
    try:
        m = api._get("/mail")
        recv = set()
        for r in (m.get("received") or []):
            if isinstance(r, dict):
                recv.add(r.get("id") or "")
            elif isinstance(r, str):
                recv.add(r)
    except Exception:
        return ""
    if "TH_SandDragon" in recv:
        return "🧾 神秘的齐④(最后步)：去家门口木材堆检查领会员卡（恒手动；木材堆到④才激活+随房型变，别硬记坐标）"
    if "TH_MayorFridge" in recv:
        return "🧾 神秘的齐③：手持日光精华(768)→沙漠沙之巨龙嘴(9,36)，站(9,37)朝上"
    if "TH_Railroad" in recv:
        return "🧾 神秘的齐②：手持10甜菜→镇长家冰箱(9,4)，站(9,5)朝上"
    if "TH_Tunnel" in recv:
        return "🧾 神秘的齐①b：手持彩虹贝壳(394)→火车站箱(45,40)，站(45,41)朝上"
    return "🧾 神秘的齐①a(任务开头)：手持电池组(787)→隧道锁盒(17,6)，站(17,7)朝上"


def _qi_chain_card_hint(cname: str) -> str:
    """卡片名是否为「神秘的齐」纸条链→是则返回动态交付提示，否则空串。"""
    if not cname:
        return ""
    cm = str(cname).strip()
    if any(k in cm for k in _QI_CARD_KEYS):
        return _qi_chain_hint()
    return ""


def _festival_poi_active(pname: str, p: dict) -> bool:
    """🎇 节日限定 POI 今天是否可见（2026-08-19 恒：非节日在 map/go_to 隐藏）。
    POI 的 map 是节日限定图且今天不在其生效日期 → False（隐藏）。普通 POI 恒 True；
    POI 带 season 字段 → 只在该季节暴露（2026-08-22 恒：冰淇淋摊只在夏季，营业细节在 note）；
    POI 带 unlock 字段 → 今天≥解锁日才暴露（2026-08-22 恒：社区布告栏年1秋2后出现）；
    POI 带 require_order 字段 → 已接对应(进行中)特殊订单才暴露（2026-08-22 恒：交付点，宽泛/内容匹配）。
    POI 带 wallet 字段 → 钱包里没有该 flag 才暴露（2026-08-23 恒：矮人商店=学会矮人语教程）。
    查不到游戏日期 → 保守隐藏（拿不准就当没开）。"""
    try:
        # 🔒 地图门禁（2026-08-23 恒：赌场 POI 要会员卡放行后才显示/可去——POI 所在图若未解锁则隐藏）。
        #    统一规则：POI 的 map 在 _locked_maps()（/unlocks 含 casino/desert/mine…）→ False。
        #    覆盖 map_query/walk_to/map_go 三入口（都调本函数），赌场 POI map=Club 一并生效。
        if (p or {}).get("map") in _locked_maps():
            return False
        # 🍦 季节限定 POI（2026-08-22 恒：冰淇淋摊只夏季，其它季节隐藏）
        s = (p or {}).get("season")
        if s:
            d = _festival_now_data()
            if not d.get("ok"):
                return False
            if (d.get("season") or "").lower() != (s or "").lower():
                return False
        # 📋 解锁日门禁（2026-08-22 恒：社区布告栏年1秋2后）
        u = (p or {}).get("unlock")
        if u:
            d = _festival_now_data()
            if not d.get("ok"):
                return False
            if _date_ordinal(d) < _date_ordinal(u):
                return False
        # 🧾 特别任务交付点门禁（2026-08-22 恒：接了对应特别订单才显示，宽泛/内容匹配）
        ro = (p or {}).get("require_order")
        if ro:
            if not _order_match_active(ro):
                return False
        # 🧱 钱包物品门禁（2026-08-23 恒：矮人商店需"学会矮人语教程"=HasDwarvishTranslationGuide）
        w = (p or {}).get("wallet")
        if w:
            if not _wallet_flag_present(w):
                return False
        # 🪨 堵路石门禁（2026-08-23 恒：矮人商店需先炸开 Mine(27,8) 的 (BC)78 石头才能走到）
        rk = (p or {}).get("rock")
        if rk:
            if _dwarf_rock_blocked():
                return False
        m = (p or {}).get("map", "")
        dates = _FESTIVAL_ONLY_MAPS.get(m)
        if not dates:
            return True
        d = _festival_now_data()
        if not d.get("ok"):
            return False
        return (d["season"], d["day"]) in dates
    except Exception:
        return True


def _festival_pois_here(location: str) -> list:
    """🎪 今天节日的限定 POI/商店（该地点的）。非节日/不在该地点 → []（map 隐藏）。
    返回 [(名称, 详情)]——map_lookup 注入 + 状态条 enum 用（2026-08-19）。"""
    d = _festival_now_data()
    if not d.get("ok"):
        return []
    key = (d["season"], d["day"])
    out = []
    shop = calendar_data.FESTIVAL_SHOPS.get(key)
    if shop and shop.get("location") == location:
        out.append((str(shop.get("note", "节日商店")).split("：")[0],
                    f"🐷 节日商店：站{shop['counter']}朝{shop['face']} → {shop.get('note','')}"))
    for p in calendar_data.FESTIVAL_POI.get(key, []):
        if p.get("location") != location:
            continue
        t = p.get("tile") or (0, 0)
        if t == (0, 0):
            loc, st, fc = "📍坐标待实测", "", ""
        else:
            loc = f"({t[0]},{t[1]})"
            st = f"站{p['stand']}" if p.get("stand") else ""
            fc = f"朝{p['face']}" if p.get("face") is not None else ""
        out.append((p["poi"], f"🎪 {p['poi']} {loc} {st}{fc} — {p.get('desc','')}"))
    return out


# 🎪 节日 POI 交互历史（2026-08-24 恒：`🎪 可:` 动态——近的先 + 交互过沉底）。
# 跨天按 (season, day) 清空；AI 导航到达(walk_to/map_go)或按瓦片交互(interact_at)命中某 POI 即标记。
_POI_DONE_TAG = None
_POI_DONE: set = set()


def _poi_detail(p: dict) -> str:
    """🎪 单条节日 POI 的展示文案（坐标/站位/朝向 + 描述）。"""
    t = p.get("tile") or (0, 0)
    if t == (0, 0):
        loc, st, fc = "📍坐标待实测", "", ""
    else:
        loc = f"({t[0]},{t[1]})"
        st = f"站{p['stand']}" if p.get("stand") else ""
        fc = f"朝{p['face']}" if p.get("face") is not None else ""
    return f"🎪 {p['poi']} {loc} {st}{fc} — {p.get('desc','')}"


def _festival_poi_names_now() -> dict:
    """当前节日 POI 名/节日商店名 -> {tile, stand}，供按名字/瓦片标记交互历史。非节日 → {}。"""
    d = _festival_now_data()
    if not d.get("ok"):
        return {}
    key = (d["season"], d["day"])
    names = {}
    for p in calendar_data.FESTIVAL_POI.get(key, []):
        if p.get("tile") == (0, 0) and not p.get("stand"):
            continue
        names[p["poi"]] = {"tile": p.get("tile"), "stand": p.get("stand")}
    shop = calendar_data.FESTIVAL_SHOPS.get(key)
    if shop:
        _n = str(shop.get("note", "节日商店")).split("：")[0]
        names[_n] = {"tile": shop.get("counter"), "stand": shop.get("counter")}
    return names


def _mark_festival_poi_name(name: str) -> None:
    """导航到达某 POI（walk_to/map_go/go_to 的 _apply_poi_stand_face 处调）→ 记入交互历史。"""
    if name and name in _festival_poi_names_now():
        _POI_DONE.add(name)


def _mark_festival_poi_tile(x, y) -> None:
    """按瓦片交互（interact_at）→ 若命中某节日 POI 的 tile/stand，记入交互历史。"""
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        return
    for name, co in _festival_poi_names_now().items():
        for c in (co.get("tile"), co.get("stand")):
            if c and (int(c[0]), int(c[1])) == (int(x), int(y)):
                _POI_DONE.add(name)
                return


def _festival_pois_sorted(location: str, x=0, y=0, maxn: int = 5) -> list:
    """🎪 节日 POI 动态排序：未交互的在先、交互过的沉底；组内按离 AI（曼哈顿）近的先。
    返回前 maxn 项 [(名称, 详情)]——状态条 `🎪 可:` 注入用（2026-08-24）。"""
    d = _festival_now_data()
    if not d.get("ok"):
        return []
    key = (d["season"], d["day"])
    global _POI_DONE_TAG
    if _POI_DONE_TAG != key:                      # 跨天清空
        _POI_DONE.clear()
        _POI_DONE_TAG = key
    try:
        xi, yi = int(x), int(y)                   # AI 坐标可能 "?"，容错回 0
    except Exception:
        xi = yi = 0

    items = []
    shop = calendar_data.FESTIVAL_SHOPS.get(key)
    if shop and shop.get("location") == location:
        nm = str(shop.get("note", "节日商店")).split("：")[0]
        t = shop.get("counter") or (0, 0)
        dist = abs(t[0] - xi) + abs(t[1] - yi) if t != (0, 0) else 10 ** 6
        items.append((nm, nm in _POI_DONE, dist,
                      f"🐷 节日商店：站{shop['counter']}朝{shop['face']} → {shop.get('note','')}"))
    for p in calendar_data.FESTIVAL_POI.get(key, []):
        if p.get("location") != location:
            continue
        nm = p["poi"]
        t = p.get("tile") or (0, 0)
        dist = abs(t[0] - xi) + abs(t[1] - yi) if t != (0, 0) else 10 ** 6
        items.append((nm, nm in _POI_DONE, dist, _poi_detail(p)))

    items.sort(key=lambda i: (i[1], i[2]))        # 未交互先 -> 组内距离近先
    return [(nm, dt) for nm, hd, ds, dt in items[:maxn]]


def _festival_pois_all() -> list:
    """今天节日的全部限定 POI/商店（跨地点，map_query 反查用）。非节日 → []。
    返回 [(名称, 地点, 详情)]。"""
    d = _festival_now_data()
    if not d.get("ok"):
        return []
    key = (d["season"], d["day"])
    out = []
    shop = calendar_data.FESTIVAL_SHOPS.get(key)
    if shop:
        out.append((str(shop.get("note", "节日商店")).split("：")[0], shop.get("location", ""),
                    f"🐷 节日商店：站{shop['counter']}朝{shop['face']} → {shop.get('note','')}"))
    for p in calendar_data.FESTIVAL_POI.get(key, []):
        t = p.get("tile") or (0, 0)
        loc = "📍坐标待实测" if t == (0, 0) else f"({t[0]},{t[1]})"
        out.append((p["poi"], p.get("location", ""),
                    f"🎪 {p['poi']} @{p.get('location')}{loc} — {p.get('desc','')}"))
    return out


def _festival_today() -> str:
    d = _festival_now_data()
    if not d.get("ok"):
        return "⚠️ 游戏未连接，无法确认今天日期。"
    f = calendar_data.get_festival_today(d["season"], d["day"])
    try:
        live = api.festival_status()
    except Exception as e:
        live = {"ok": False, "error": f"游戏未连接（{e}）"}
    if not f:
        return "今天没有节日。" + (f"（实况: {live.get('error', '')}）" if not live.get("ok") else "")
    parts = [f"🎪 今天是{f['detail']}"]
    if live.get("ok") and live.get("isFestival"):
        parts.append(f"✅ 已在举办（{live.get('location')}，{live.get('actorCount')} 个NPC）")
        actors = "、".join((a.get("displayName") or a.get("name")) for a in live.get("actors", [])[:8])
        if actors:
            parts.append(f"👥 {actors}")
    else:
        parts.append("⏳ 还没开始（时间到了才开）")
    return "。".join(parts)


def _festival_next() -> str:
    d = _festival_now_data()
    if not d.get("ok"):
        return "⚠️ 游戏未连接，无法确认今天日期。"
    f = calendar_data.get_next_festival(d["season"], d["day"])
    if not f:
        return "没有更近的节日了。"
    return (f"🎪 下一个节日：{f['name']}（{f['season']}{f['day']}日，{_festival_time(f['detail'])}），"
            f"剩 {f['days_left']} 天，在 {f['map'] or '?'}")


def _festival_go() -> str:
    d = _festival_now_data()
    if not d.get("ok"):
        return "⚠️ 游戏未连接，无法确认今天日期。"
    f = calendar_data.get_festival_today(d["season"], d["day"])
    if not f or not f.get("map"):
        return "今天不是节日，没有节日地点可去。用 festival next 看下一个。"
    # 先看是否已在节日独立图（Temp）：已在场地就不用再导航（Temp 不在 MAP_LINKS，map_go 会报"没路径"）
    st_loc = ""
    try:
        st_loc = api.state().get("location", {}).get("name", "")
    except Exception:
        pass
    if st_loc in _FESTIVAL_TEMP_MAPS:
        return (f"🎪 你已在 {f['name']} 场地（{st_loc}）——直接玩（festival info/interact 互动；"
                "退出/卡住→联系 user 帮忙，MCP 端 warp 已禁用）")
    try:
        r = map_go(f["map"])
    except Exception as e:
        return f"❌ 导航失败: {e}"
    # ⚠️ 2026-08-16 冬星节实测：节日独立图(Temp)的入口是"到入口图后事件拉进去"——
    #   map_go 常误报"到X失败"(到达验证没等到 Temp)，但人其实已在节日场地。
    #   map_go 后重读位置：若进了 Temp 就给提示，让 AI 别被"失败"误导。
    try:
        st_loc = api.state().get("location", {}).get("name", "")
    except Exception:
        pass
    if st_loc in _FESTIVAL_TEMP_MAPS and f.get("map") not in _FESTIVAL_TEMP_MAPS:
        return (r + f"\n（map_go 可能误报失败——你已在节日场地 {st_loc}，看状态条 🎪 节日进行中即可；"
                "退出/卡住→联系 user 帮忙，MCP 端 warp 已禁用）")
    return r


def _festival_info() -> str:
    try:
        live = api.festival_status()
    except Exception as e:
        return f"⚠️ 游戏未连接: {e}"
    if not live.get("ok"):
        return f"❌ 当前没有活动事件（{live.get('error', '')}）。用 festival today/next 看节日安排。"
    actors = "、".join(f"{a.get('displayName') or a.get('name')}({a.get('x')},{a.get('y')})" for a in live.get("actors", []))
    return f"🎪 {live.get('festivalName', '节日')} 在 {live.get('location', '?')}，{live.get('actorCount', 0)} 个NPC：{actors or '无'}"


def _collect_dialogue(max_steps: int = 15) -> tuple:
    """自动推进当前 DialogueBox：纯文本 key confirm 点掉收台词，到选项/事件/结束停下。
    返回 (collected台词列表, options列表)。复用 chat_npc 的推进逻辑（2026-08-18 抽出共享）。"""
    collected = []
    for _ in range(max_steps):
        st = api.state(light=True)
        m = st.get("activeMenu") or {}
        ev = st.get("activeEvent") or {}
        if m.get("type") == "DialogueBox":
            d = (m.get("dialogue") or "").strip()
            if d and (not collected or collected[-1] != d):
                collected.append(d)
            if m.get("responses"):
                break                      # 出现选项 → 停，让 AI 选
            api.key("confirm")
            time.sleep(0.2)
            continue
        if ev.get("id"):
            _advance_story(m, ev)          # 事件 → 走剧情推进
            continue
        break                              # 没对话了
    opts = []
    try:
        mm = api.menu()
        if mm.get("type") == "DialogueBox":
            opts = mm.get("responses") or []
    except Exception:
        pass
    return collected, opts


def _festival_social_chat(a) -> str:
    """🎪 自然走到节日 NPC 前搭话 + 自动推进对话（纯文本点掉收台词，遇选项停下）。
    2026-08-18 恒：改自然走路（不用 /festival/interact 瞬移端点），拟人地走动找人聊。"""
    tname = a.get("displayName") or a.get("name") or "?"
    tx, ty = int(a.get("x", 0)), int(a.get("y", 0))
    # 走路统一 /walk_to（2026-08-13 恒拍板）先走近（拟人），再 position 精确对位（落点偏一格已知坑）
    cur = (api.state().get("location") or {}).get("name", "")
    try:
        api.walk_to_coord(cur, tx, ty + 1)
        deadline = time.time() + 8
        while time.time() < deadline:
            ax, ay = api.player_tile()
            if abs(ax - tx) + abs(ay - ty) <= 1:
                break
            time.sleep(0.2)
    except Exception:
        pass
    # 对位：position 精确站 NPC 下方（2026-08-18 恒：walk_to 落点偏一格，靠 position 对正）
    try:
        api.position(tx, ty + 1)
        time.sleep(0.25)
    except Exception:
        pass
    api.face(0)
    ax, ay = api.player_tile()
    if abs(ax - tx) + abs(ay - ty) > 4:    # 复查距离：够不着优雅跳过（节日特殊位/墙后）
        return f"  💤 {tname} 够不着（{abs(ax-tx)+abs(ay-ty)}格，可能墙后/特殊位），跳过"
    r = api._post("/interact", {"x": tx, "y": ty})   # 直接打 NPC tile，不依赖面前格
    if not r.get("ok"):
        return f"  ⚠️ {tname}: 搭话失败（{r.get('error', '')}）"
    collected, opts = _collect_dialogue()
    if not collected and not opts:
        # 兜底：节日事件 actor 用 /interact 可能不触发（NPC走动/事件模式，2026-08-20 花舞节实测）
        # → 换 /festival/interact 按名重试（它内部 checkAction + npc.checkAction 双路径）
        try:
            api._post("/festival/interact", {"name": a.get("name")})
            time.sleep(0.3)
            collected, opts = _collect_dialogue()
        except Exception:
            pass
    lines = []
    if collected:
        for d in collected:
            lines.append(f"  💬 {tname}: 「{d}」")
    else:
        lines.append(f"  💬 {tname}: （没台词）")
    if opts:
        opt_str = " | ".join(f"[{i}]{o.get('text')}" for i, o in enumerate(opts))
        lines.append(f"  🗳️ {tname} 选项: {opt_str}")
        lines.append("  → menu_click(option=N) 选完再 festival interact 继续")
    return "\n".join(lines)


# 节日社交巡礼断点（2026-08-18：空参循环挨个聊，遇选项停下，选完再调继续）
_fest_social_state = {"key": None, "targets": [], "idx": 0}


def _festival_interact(name: str = "") -> str:
    """🎪 节日互动（2026-08-18 恒：自然走路，不瞬移）
    - 传 name：自然走到指定节日 NPC 前搭话，自动推进对话收台词（遇选项停下让 AI 选）
    - 空参：自动循环和节日现场【除刘易斯外】所有 NPC 挨个聊（纯对话自动点掉收台词；
      遇选项停下——处理完选项再调 festival interact 继续下一个；断点自动续传）
    ⚠️ 排除刘易斯：他是节日主持，很多节日跟他对话会开启节日小游戏/活动。
    """
    try:
        live = api.festival_status()
    except Exception as e:
        return f"⚠️ 游戏未连接: {e}"
    if not live.get("ok"):
        return f"❌ 当前没有活动事件（{live.get('error', '')}）。用 festival today/next 看节日安排。"
    actors = live.get("actors") or []
    if not actors:
        return "🎪 节日现场没有可互动的 NPC"
    me = (api.state().get("player") or {}).get("name", "")
    # 传名字 → 单点自然聊
    if name:
        target = next((a for a in actors
                       if name in (a.get("name") or "") or name in (a.get("displayName") or "")), None)
        if not target:
            return f"❌ 节日现场没有「{name}」"
        return _festival_social_chat(target)
    # 空参 → 循环巡礼（排除刘易斯+自己，断点续传）
    key = (live.get("season"), live.get("day"))
    st = _fest_social_state
    if st["key"] != key:
        st["key"] = key
        st["targets"] = [a for a in actors
                         if a.get("name") != "Lewis" and a.get("displayName") != "刘易斯"
                         and a.get("name") != me and a.get("displayName") != me]
        st["idx"] = 0
    if not st["targets"]:
        return "🎪 节日现场除刘易斯外没有其他 NPC 可聊（刘易斯是节日主持，对话会开小游戏）"
    if st["idx"] >= len(st["targets"]):
        st["key"] = None
        return "🎪 节日全场（除刘易斯）都聊完了"
    results = []
    while st["idx"] < len(st["targets"]):
        out = _festival_social_chat(st["targets"][st["idx"]])
        results.append(out)
        st["idx"] += 1
        if "🗳️" in out:
            break
    head = f"🎪 节日社交巡礼 {st['idx']}/{len(st['targets'])}：\n"
    if st["idx"] < len(st["targets"]):
        return head + "\n".join(results) + "\n🚦 遇到选项停下——menu_click(option=N) 选完，再调 festival interact 继续"
    st["key"] = None
    return head + "\n".join(results) + "\n✅ 节日全场（除刘易斯）都聊完了"


def _festival_answer(answer: int = 0) -> str:
    try:
        r = api.festival_answer(int(answer))
    except Exception as e:
        return f"⚠️ 游戏未连接: {e}"
    if not r.get("ok"):
        return f"❌ 应答失败: {r.get('error', r)}"
    return f"🎪 应答 {r.get('answer')}（方法 {r.get('method')}）"


def _festival_shop() -> str:
    """🐷 节日商店：今天节日有商店（FESTIVAL_SHOPS）→ 去柜台开 ShopMenu 读商品。
    ⚠️ 节日商店平时不显示、/surroundings 扫不到，只能按记录的柜台位置交互（2026-08-16 恒实测）。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return "⚠️ 游戏未连接"
        key = (d["season"], d["day"])
        shop = calendar_data.FESTIVAL_SHOPS.get(key)
        if not shop:
            return "🎪 今天这个节日没有商店（FESTIVAL_SHOPS 没登记）"
        loc = shop.get("location") or "Temp"
        cx, cy = shop["counter"]
        face = shop.get("face", 0)
        # 到柜台
        api._post("/position", {"x": cx, "y": cy})
        time.sleep(1)
        api._post("/face", {"direction": face})
        time.sleep(0.4)
        dx, dy = [(0, -1), (1, 0), (0, 1), (-1, 0)][face]
        api._post("/interact", {"x": cx + dx, "y": cy + dy})
        time.sleep(1)
        m = api._get("/menu")
        if m.get("open") and m.get("type") == "ShopMenu":
            items = m.get("shopItems") or []
            lines = [f"🐷 {shop.get('note') or '节日商店'} 开了，{len(items)}件:"]
            for i in items:
                lines.append(f"  {i.get('name')} {i.get('price', 0)}g x{i.get('stock')}")
            lines.append("买→menu_click(item=名)；关→menu_click(button=close)")
            return "\n".join(lines)
        return "⚠️ 商店没开（柜台位置/交互问题）"
    except Exception as e:
        return f"❌ {e}"


def _egg_win_route(eggs, start, target: int = 9) -> list:
    """赢家路线：从开局位出发，近邻贪心扩展，捡满 target 颗就停（密集最短）。
    返回前 target 个点（坐标列表）。"""
    unvisited = list(eggs)
    route = []
    cur = start
    while unvisited and len(route) < target:
        nxt = min(unvisited, key=lambda p: abs(p[0] - cur[0]) + abs(p[1] - cur[1]))
        route.append(nxt)
        unvisited.remove(nxt)
        cur = nxt
    return route


def _egg_route_str(route) -> str:
    """坐标列表 → "[(16,66),(15,76),...]" 字符串（festival egg_run 的 route 参数格式）。"""
    return "[" + ",".join(f"({x},{y})" for x, y in route) + "]"


def _egg_festival_guard() -> str:
    """🥚 festival_game 门禁：蛋蛋节找蛋工具只在 spring13 蛋蛋节可用（恒 2026-08-17：其他时候不暴露）。
    返回空串=放行；否则=拒绝原因。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return "⚠️ 游戏未连接"
        if (d["season"], d["day"]) != ("spring", 13):
            return "🎪 今天不是蛋蛋节（spring 13）——找蛋工具只在蛋蛋节当天可用"
        return ""
    except Exception:
        return "⚠️ 门禁检查失败"


def _festival_eggs() -> str:
    """🥚 蛋蛋节找蛋：spring13 返回当年（奇/偶年）全部蛋蛋坐标（不给推荐顺序，AI 自己规划）。
    规划好 → egg_note 偷偷记下 → 开赛 egg_run 照着跑。只在蛋蛋节可用（festival_game 门禁）。"""
    try:
        g = _egg_festival_guard()
        if g:
            return g
        d = _festival_now_data()
        key = (d["season"], d["day"])
        all_eggs = calendar_data.FESTIVAL_EGGS.get(key)
        if not all_eggs:
            return "🎪 今天不是蛋蛋节（spring 13）——没有找蛋"
        parity = "even" if d.get("year", 1) % 2 == 0 else "odd"
        eggs = all_eggs.get(parity) or []
        start = (calendar_data.FESTIVAL_EGG_START.get(key) or {}).get(parity, (27, 69))
        lines = [f"🥚 蛋蛋节找蛋（{parity}年，{len(eggs)} 颗）："]
        lines.append("  🏁 开局 ~" + str(start))
        lines.append("  📋 蛋坐标:")
        for i in range(0, len(eggs), 6):
            lines.append("  " + "  ".join(f"({x},{y})" for x, y in eggs[i:i + 6]))
        lines.append("  → egg_note route=\"[...]\" 记 → 开赛 egg_run 照着跑")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


def _parse_route(route: str) -> list:
    """解析坐标路线字符串 → [(x,y),...]。兼容 "[(16,66),(15,76)]" 或 "16,66 15,76"。"""
    if not route:
        return []
    import re
    pts = re.findall(r"\(?\s*(\d+)\s*,\s*(\d+)\s*\)?", route)
    return [(int(a), int(b)) for a, b in pts]


def _egg_score() -> int:
    """AI 当前 festivalScore（蛋蛋节捡蛋数）；读不到返回 -1。"""
    try:
        return int((api._ai_get("/state").get("player") or {}).get("festivalScore") or 0)
    except Exception:
        return -1


def _wait_walk(x, y, timeout: float = 7.0) -> bool:
    """轮询等 AI 走到 (x,y)（walk_to 异步，需等待）。到达/停稳返回 True。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.4)
        try:
            pl = api._ai_get("/state").get("player") or {}
            px, py = pl.get("x", 999), pl.get("y", 999)
            if abs(px - x) <= 1 and abs(py - y) <= 1:
                return True
            if not pl.get("isMoving"):   # 停稳再确认一次
                time.sleep(0.4)
                pl = api._ai_get("/state").get("player") or {}
                if abs(pl.get("x", 999) - x) <= 1 and abs(pl.get("y", 999) - y) <= 1:
                    return True
        except Exception:
            pass
    return False


def _halt_move():
    """停住角色当前走动（egg_run 每步前/收手时调，防残留走动）。"""
    try:
        api._ai_post("/stop")
    except Exception:
        pass


def _walk_to_egg(x, y) -> bool:
    """走到蛋格：先自然走（walk_to，**放宽等待让走完**），真走不到（栅栏/装饰挡）才 **/position 兜底**。
    ⚠️ 2026-08-17 两次实测修：
      ① _wait_walk 6s 太短→打断慢自然走→position 抢走→交互漏蛋 → 放宽 12s
      ② position 直接站到蛋格上→checkAction 从"站蛋上"出发触发不了（第一轮自然走=邻格才成）→
         position 兜底**站到蛋的邻格**（保持相邻，交互仍打蛋格）"""
    _halt_move()
    try:
        api.walk_to_coord("Temp", x, y)
        if _wait_walk(x, y, timeout=12):
            return True
    except Exception:
        pass
    # 真走不到（栅栏挡）→ position 到蛋的**邻格**（恒拍板：不钻栅栏，补位到旁边再交互蛋格）
    _halt_move()
    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
        try:
            api._ai_post("/position", {"x": nx, "y": ny})
            time.sleep(0.6)
            return True
        except Exception:
            continue
    return False


def _dialogue_now() -> bool:
    """AI 是否出现对话（蛋蛋节捡蛋时=刘易斯宣布结果/比赛结束，egg_run 停检用）。"""
    try:
        am = (api._ai_get("/state").get("activeMenu") or {})
        if am.get("type") == "DialogueBox":
            return True
    except Exception:
        pass
    return False


# 📝 蛋蛋节小纸条：AI 预先规划好路线偷偷记这（防开赛忘记），egg_run 不传 route 时按它执行（2026-08-17 恒）
_EGG_NOTE = {"route": []}
# 🥚 蛋蛋节捡蛋自动 hook 标记（2026-09-05）：本局寻宝是否已"处理过"（自动跑了/提醒过了/手动跑过了）。
#   每局只触发一次：开场有纸条→阻塞自动捡；没纸条→只提醒让 AI 自己跑。防重复启动/互相干扰。
_EGG_RUN_AUTO = {"fired": False}


def _festival_egg_note(route: str = "") -> str:
    """📝 蛋蛋节小纸条：AI 把预先规划好的捡蛋路线偷偷记下来（开赛前想好、防止忘记）。
    规划顺序 AI 自己定（festival eggs 只给坐标不给顺序）；开赛 egg_run 不传 route 就按这个执行。
    route 格式同 egg_run："[(16,66),(15,76),...]" 只在蛋蛋节可用（festival_game 门禁）。"""
    g = _egg_festival_guard()
    if g:
        return g
    pts = _parse_route(route) if route else []
    if not pts:
        return "❌ 要写路线：festival egg_note route=\"[(16,66),(15,76),...]\""
    _EGG_NOTE["route"] = pts
    return (f"📝 已偷偷记下 {len(pts)} 点捡蛋路线（开赛 egg_run 不传 route 就按它执行）：\n"
            + _egg_route_str(pts)[:200])


def _festival_egg_run(route: str = "") -> str:
    """🥚 蛋蛋节循环捡蛋（第一个节日特殊小游戏工具，2026-08-17 恒）：
    沿 route 坐标逐个 walk_to（自然走）→ interact（捡），每步查 festivalScore。
    - route 省略 = 用 egg_note 偷偷记下的路线（AI 开赛前规划好、egg_run 直接执行）
    - **被恒捡走的点自动跳过**（交互后分数没涨=没蛋，跳下一个）
    - **尽量多捡**：不设上限（联机比对手多才赢；单机 9 颗赢阿比盖尔 8 颗）
    - **游戏本身限时**：不做超时/连续没捡到逻辑；出现对话（刘易斯宣布结果）自动停，
      AI 靠事件检测看到宣布结果就停，不再调下一次
    - 可多次调用补捡剩下的（festival eggs 给全部坐标）
    ⚠️ 蛋是事件画的（/surroundings 扫不到但能交互），一次调用沿路线连续捡，避免逐格思考延迟。
    只在蛋蛋节可用（festival_game 门禁）。"""
    g = _egg_festival_guard()
    if g:
        return g
    try:
        if route:
            points = _parse_route(route)
        else:
            points = list(_EGG_NOTE.get("route") or [])
        if not points:
            return "❌ 没路线：festival eggs 看坐标→自己规划→egg_note route=... 记下；或直接 egg_run route=\"[(16,66),...]\""
        # 🥚 2026-09-05：手动/自动跑到这都记一笔——防 _maybe_egg_run_auto 在同一工具调用里又自动启动一次(重复捡)
        _EGG_RUN_AUTO["fired"] = True
        lines = [f"🥚 捡蛋开始（路线 {len(points)} 点，尽量多捡）："]
        collected = 0
        # 开跑先查：寻宝已结束（festivalTimer 归零）→ 立刻收手不白跑（恒：时机很重要；-1=读取失败不误停）
        if _festival_timer() == 0:
            _halt_move()
            return "⚠️ 寻宝已结束/未开始（festivalTimer=0）——没在捡蛋，收手"
        try:
            for i, (x, y) in enumerate(points, 1):
                # 寻宝倒计时归零/出现对话（宣布结果）→ 收手（先停住残留走动）
                if _festival_timer() == 0 or _dialogue_now():
                    _halt_move()
                    lines.append("💬 寻宝结束/宣布结果，收手")
                    break
                before = _egg_score()
                # 自然走（栅栏挡着走不到就直接跳过，不钻栅栏）
                if not _walk_to_egg(x, y):
                    lines.append(f"  ⚠️ ({x},{y}) 走不到/没到，跳过")
                    continue
                time.sleep(0.3)
                # 🧭 面向蛋格再交互（恒观察 2026-08-17：朝向不对会捡不到）
                try:
                    pl = api._ai_get("/state").get("player") or {}
                    px, py = pl.get("x", x), pl.get("y", y)
                    dx, dy = x - px, y - py
                    face = (1 if dx > 0 else 3) if abs(dx) >= abs(dy) else (0 if dy < 0 else 2)
                    api._ai_post("/face", {"direction": face})
                    time.sleep(0.2)
                except Exception:
                    pass
                try:
                    api._ai_post("/interact", {"x": x, "y": y})
                except Exception as e:
                    lines.append(f"  ❌ ({x},{y}) 交互失败: {e}")
                    continue
                time.sleep(0.4)
                after = _egg_score()
                if after > before:
                    collected += 1
                    lines.append(f"  ✅ ({x},{y}) 捡到（累计 {after}）")
                else:
                    lines.append(f"  ⏭️ ({x},{y}) 没蛋（被捡走/已过），跳过")
        finally:
            _halt_move()   # 无论收手原因，都停住角色
        lines.append(f"📊 本次捡到 {collected} 颗（festivalScore={_egg_score()}）；"
                     f"{'可再调 egg_run 补剩下的' if points else ''}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


def _festival_timer() -> int:
    """当前节日限时小游戏倒计时(ms)；无节日/读不到返回 -1。蛋蛋节找蛋/冰钓等用。"""
    try:
        return int((api.festival_status() or {}).get("festivalTimer") or -1)
    except Exception:
        return -1


_TRAVEL_CART_KEY = {"last": None}  # 🐷 旅行猪车提醒去重（2026-08-20 恒：周五/周日一天一条）


def _traveling_cart_hint(season: str, day, tod) -> str:
    """🐷 旅行猪车提醒（2026-08-20 恒）：周五/周日 6:00-20:00 森林开档。
    非出现日不显示；开店当天注入一次，让 AI 去猪车逛逛看稀有商品（walk_to 猪车(旅行货车) → interact 开商店）。"""
    try:
        if not isinstance(day, int):
            return ""
        dow = (day - 1) % 7          # 周几：0=一 … 4=五 6=日
        if dow not in (4, 6):
            return ""
        if isinstance(tod, int) and not (600 <= tod <= 2000):
            return ""                 # 营业 6:00-20:00（维基），收摊了不推
        key = (season, day)
        if _TRAVEL_CART_KEY["last"] == key:
            return ""
        _TRAVEL_CART_KEY["last"] = key
        wd = "五" if dow == 4 else "日"
        return (f"🐷 今天是周{wd}，旅行猪车在森林开档（6:00-20:00）！去逛逛看稀有商品——"
                "map walk 猪车(旅行货车) 到点朝上，scene interact 开商店")
    except Exception:
        return ""


def _egg_festival_hint(loc_name: str) -> str:
    """🥚 蛋蛋节提示（节日场景 Temp 内一直注入，简短省 token——恒 2026-08-17 拍板）：
    进场地→festival eggs 看当年蛋坐标→赛前 egg_note 偷偷记→逛；开赛 egg_run 照着跑。"""
    try:
        d = _festival_now_data()
        if not d.get("ok") or (d["season"], d["day"]) != ("spring", 13):
            return ""
        if loc_name != "Temp":
            return ""
        if _festival_timer() > 0:
            return "🥚 寻宝中！egg_run 按 egg_note 记的路线捡"
        return "🥚 蛋蛋节！festival eggs 看当年蛋坐标，赛前 egg_note 偷偷记路线（写了开赛自动捡）；开赛 egg_run 照着跑"
    except Exception:
        return ""


def _maze_parity(year=None) -> str:
    """迷宫奇偶性："even" if 年偶数 else "odd"（同蛋蛋节；Town-Halloween=偶/Town-Halloween2=奇）。"""
    if year is None:
        year = (_festival_now_data() or {}).get("year", 1)
    return "even" if (year or 1) % 2 == 0 else "odd"


def _festival_maze_coords() -> dict:
    """今天(若是迷宫节)按奇偶年返回 {chest,minecart,goal}；非迷宫节 → {}。2026-08-28 恒实测。"""
    d = _festival_now_data()
    if not d.get("ok"):
        return {}
    mz = calendar_data.FESTIVAL_MAZE.get((d["season"], d["day"]), {})
    if not mz:
        return {}
    return mz.get(_maze_parity(d["year"]), {}) or {}


def _fest_guide(guide: str) -> str:
    """🎪 玩法文本 {host} 占位 → 替换房主实时名字；{chest}/{minecart}/{goal} → 按奇偶年填迷宫坐标（2026-08-28）。"""
    try:
        guide = guide.replace("{host}", _host_name())
        mz = _festival_maze_coords()
        if mz:
            guide = (guide.replace("{chest}", str(mz.get("chest", "")))
                          .replace("{minecart}", str(mz.get("minecart", "")))
                          .replace("{goal}", str(mz.get("goal", ""))))
        return guide
    except Exception:
        return guide


def _festival_maze() -> str:
    """🎃 迷宫标点（奇偶年感知）：返回当年宝箱/矿车/奖励坐标 + 玩法指引。AI 复现用。"""
    mz = _festival_maze_coords()
    if not mz:
        return "⚠️ 今天不是迷宫节（万灵节=秋27），无迷宫数据"
    parity = _maze_parity()
    return (f"🎃 万灵节迷宫（{parity}年）——奇偶年布局不同但**坐标固定**：\n"
            f"  🎁 宝箱 {mz.get('chest')}（奖励 {mz.get('goal')}）\n"
            f"  🚂 矿车 {mz.get('minecart')}（交互回出口）\n"
            f"  🧭 走法：`walk_to 宝箱坐标`（BFS 读实时迷宫墙自适配，含暗道/传送），想自己玩看棋盘用 scene maze/maze_seg，按段走 festival maze_walk。\n"
            f"  ⚠️ AI 踩不好传送瓦片，结束节日请人类帮忙。")


def _minigame_guide_hint() -> str:
    """🎰 小游戏按钮引导（2026-08-23 恒）：AI 打开老虎机/21点，状态条直接告诉它该点啥。
    读 /state player.minigame（Slots/CalicoJack），非小游戏则空串不拦截。"""
    try:
        mg = (api.state().get("player") or {}).get("minigame")
        if not mg:
            return ""
        # ⚠️ 2026-08-26 恒：文案一律写**域形式**（menu minigame …）。
        #    以前写裸工具名 minigame_click(...)，而域模式下该工具已被隐藏 → AI 照提示调不存在的工具。
        if mg == "Slots":
            return "🎰 老虎机：menu minigame action=bet10/bet100 下注 · action=done 退出 · menu read 看转盘"
        if mg == "CalicoJack":
            return "🃏 21点：menu minigame action=hit 加牌 · stand 停牌 · double 加倍 · quit 退出 · menu read 看牌面"
        return f"🎰 小游戏 {mg}：用 menu minigame action=... 操作（menu read 读现状）"
    except Exception:
        return ""


def _festival_activity_hint(menu_type: str = "") -> str:
    """🎪 节日限时小游戏提示（2026-08-16）：ReadyCheckDialog/BobberBar → 注入今天节日引导；
    农展台(StorageContainer)/数量框(NumberSelectionMenu) → 专用操作引导（2026-08-24 恒）。
    让 AI 在节日小游戏/专用菜单打开时知道该干嘛（放展位/输数量/就绪/冰钓限时）。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return ""
        f = calendar_data.get_festival_today(d["season"], d["day"])
        if not f:
            return ""
        if menu_type == "StorageContainer":
            return "🏆 农展台(放9件评分)：festival display_fill items='名,名…' 一次放满 · display_takeback 全部收好(评完必收)"
        if menu_type == "NumberSelectionMenu":
            return "🔢 数量框(金币换星币/转盘押注)：menu number value=N 设数量 · confirm=True 点确定 · button=cancel 取消"
        if menu_type not in ("ReadyCheckDialog", "BobberBar"):
            return ""
        guide = _fest_guide(calendar_data.FESTIVAL_GUIDE.get((d["season"], d["day"]), ""))
        if not guide:
            return ""
        if menu_type == "ReadyCheckDialog":
            return f"🫂 {f['name']} 就绪确认——全员确认后开始！{guide}"
        return f"🎣 {f['name']} 钓鱼小游戏中（限时）：{guide}"
    except Exception:
        return ""


# ── 🎮 小游戏开始/结束检测（2026-08-24 恒）────
# 玩法引导已由 _festival_activity_hint/_minigame_guide_hint 注入；本检测只补"开始/结束"触发信号，
# 让 AI 知道现在进入/退出小游戏。照 _buff_reminder 对比上次活动键，只在状态变化时弹一行。
_ACT_TRACK = {"key": ""}


def _activity_display_name(key: str) -> str:
    """小游戏活动键→人类可读名。key 形如 casino:Slots / event:festival_spring13 / menu:BobberBar。"""
    try:
        if key.startswith("casino:"):
            return {"casino:Slots": "老虎机", "casino:CalicoJack": "21点"}.get(key, key.split(":", 1)[1])
        if key.startswith("mg:"):
            # 通用 Minigame 映射（未知回退原始名，不让 AI 看到空白键）。反编译确认这些子类名叫啥（2026-08-27）。
            return {"mg:FishingGame": "钓鱼小游戏", "mg:MineCart": "矿车游戏",
                    "mg:AbigailGame": "Prairie King街机", "mg:CraneGame": "抓娃娃机",
                    "mg:Darts": "飞镖", "mg:TargetGame": "弹弓游戏",
                    "mg:BoatJourney": "小船历险", "mg:RobotBlastoff": "机器人升空"}.get(key, key.split(":", 1)[1])
        if key.startswith("menu:"):
            return {"menu:BobberBar": "钓鱼小游戏", "menu:ReadyCheckDialog": "节日活动就绪",
                    "menu:StrengthGame": "力量测试"}.get(key, "小游戏菜单")
        if key.startswith("event:"):
            try:
                d = _festival_now_data()
                if d.get("ok"):
                    f = calendar_data.get_festival_today(d["season"], d["day"])
                    if f:
                        return f["name"]
            except Exception:
                pass
            return "节日活动"
        return key
    except Exception:
        return key


def _current_activity_key(st: dict) -> str:
    """从 /state 数据算当前小游戏活动键；无→""。只认小游戏类信号，背包/商店/剧情等普通弹窗不触发。
    ⚠️ ReadyCheckDialog 也用于睡觉确认——只有【节日当天且不在床】才算节日小游戏就绪。"""
    try:
        p = st.get("player") or {}
        mg = p.get("minigame")
        if mg in ("Slots", "CalicoJack"):
            return f"casino:{mg}"
        if mg:
            # 通用：任意 Minigame 对象（秋收钓鱼FishingGame/街机矿车/飞镖/弹弓……）非空即"小游戏在线"。
            # 反编译确认这些全是 StardewValley.Minigames 子类，一条规则覆盖，无需逐个节日写白名单（2026-08-27）。
            return f"mg:{mg}"
        am = st.get("activeMenu") or {}
        amt = am.get("type")
        if amt == "BobberBar":
            return "menu:BobberBar"
        if amt == "StrengthGame":
            return "menu:StrengthGame"
        if amt == "ReadyCheckDialog":
            in_bed = bool(p.get("isInBed") or p.get("isSleeping"))
            d = _festival_now_data()
            return ("menu:ReadyCheckDialog" if (d.get("ok") and not in_bed) else "")
        ev = st.get("activeEvent") or {}
        evid = ev.get("id")
        if evid and evid != "-1" and str(evid).startswith("festival_"):
            return f"event:{evid}"
        return ""
    except Exception:
        return ""


def _activity_change_notice(st: dict) -> str:
    """🎮 小游戏开始/结束检测（只报开始/结束，不给玩法）：对比上次活动键，状态变化时弹一行。
    ""→key 开始·key→"" 结束·key→key2 切换。st=已读 /state 数据（复用，免二次调用）。"""
    try:
        cur = _current_activity_key(st)
        prev = _ACT_TRACK.get("key", "")
        _ACT_TRACK["key"] = cur
        if prev == cur:
            return ""
        if not prev and cur:
            return f"🎮 小游戏开始：{_activity_display_name(cur)}"
        if prev and not cur:
            return f"🎮 小游戏结束：{_activity_display_name(prev)}"
        return f"🎮 小游戏切换：{_activity_display_name(prev)}→{_activity_display_name(cur)}"
    except Exception:
        return ""


_FEST_ARRIVE_KEY = {"last": None}
_FEST_RECOMMEND_KEY = {"last": None}


def _festival_recommend_hint(loc_name: str = "") -> str:
    """🎪 沙漠节玩法推荐（2026-08-18 恒：在节日场地每天注入一次，AI 知道玩什么逛什么）。
    下矿流程 + 逛店推荐；数据来自恒实测（buff表/魔法糖冰棍/免费项目）。"""
    try:
        if loc_name != "DesertFestival":
            return ""
        d = _festival_now_data()
        if not d.get("ok"):
            return ""
        key = (d["season"], d["day"])
        if _FEST_RECOMMEND_KEY["last"] == key:
            return ""
        _FEST_RECOMMEND_KEY["last"] = key
        return ("🎪 沙漠节玩法推荐：\n"
                "  ⛏️ 下矿：先吃厨师buff料理(稀有水果+番茄罗勒酱保生存) → 接马龙任务(二选一) → 骷髅洞炸矿(挖蛋矿/刷积分) → 吉尔领分奖 + 马龙领任务奖\n"
                "  🛍️ 逛店：蛋店魔法糖冰棍(17号250蛋强烈推荐) / 免费换装(艾米丽,联系人类帮换) / 免费下注(赛跑) / 免费仙人掌 / 钓鱼挑战 / 村民商店\n"
                "  📖 festival help 玩法详情 / festival poi 各点位置")
    except Exception:
        return ""
_BUFF_TRACK = {"buffs": None}


def _buff_reminder(loc_name: str = "") -> str:
    """🧪 buff 生效/结束提醒（2026-08-16 恒）：对比上次 buff 列表，新增→生效、消失→结束。
    进小新闻。⚠️ 矿井内不推（挖矿脚本自动吃 buff）。需新 DLL /state 的 buffs 字段。"""
    try:
        if _BUFF_TRACK["buffs"] is None:
            _BUFF_TRACK["buffs"] = {}
        # 矿井内不推
        if loc_name.startswith("UndergroundMine") or loc_name.startswith("Volcano") \
                or loc_name in ("Mine", "SkullCave"):
            _BUFF_TRACK["buffs"] = {}
            return ""
        st = api.state()
        buffs = (st.get("player") or {}).get("buffs") or []
        cur = {}
        for b in buffs:
            n = b.get("name") or ""
            if n:
                cur[n] = (b.get("remainingMs") or 0) // 1000
        prev = _BUFF_TRACK["buffs"] or {}
        lines = []
        for name, secs in cur.items():
            if name not in prev:
                lines.append(f"⏳ buff生效: {name}（剩约{secs}秒）")
        for name in prev:
            if name not in cur:
                lines.append(f"buff结束: {name}")
        _BUFF_TRACK["buffs"] = cur
        return "  ".join(lines) if lines else ""
    except Exception:
        return ""


_ROD_TRACK = {"sig": None, "day": None}


def _fishing_rod_hint(data: dict) -> str:
    """🎣 鱼竿在手（=有钓鱼意图）时，报当前竿上饵/钓具 + 背包饵量，AI 据此决定补不补/去哪补。
    2026-08-29 恒三改：
      ① 新手竿不注入——upgrade<=1 就是竹鱼竿(0)/训练竿(1)，装不了饵，注入=噪音（反编译 upgrade→ItemId 而定，不信名字）。
      ② **当前鱼饵/鱼钩随时报**（按装备签名变化去重，不一天一次）——拿竿/装备变就报，AI 能随时看到装了什么。
      ③ **补饵/补钩提醒一天只一次**（day_key 去重）——"可上饵/没饵了去补货/可上钓具"这种动作提示别刷屏；
         ⚠️多数情况背包根本没饵（要拿虫肉合成/买），0 更要报并提示去哪补。竿不在手不注入。"""
    try:
        p = data.get("player") or {}
        rod = p.get("rod")
        if not rod or not rod.get("inHand"):
            return ""
        # ① 新手竿（竹=upgrade0/训练=upgrade1）不注入
        if (rod.get("upgrade") or 0) <= 1:
            return ""
        name = rod.get("name") or "鱼竿"
        bait = rod.get("bait")
        bait_stack = rod.get("baitStack") or 0
        bag_bait = rod.get("baitInBag") or 0
        tackles = rod.get("tackle") or []
        can_tackle = rod.get("canTackle")

        # ② 当前装备：签名（竿名/饵/饵量/背包饵量/钓具(id,耐久)）变化才报
        sig = (name, bait, bait_stack, bag_bait, tuple((t.get("name"), t.get("uses")) for t in tackles))
        eq_changed = _ROD_TRACK["sig"] != sig
        _ROD_TRACK["sig"] = sig

        parts = [f"饵={bait}×{bait_stack}" if bait else "饵=无"]
        if tackles:
            tk = "、".join(f"{t.get('name')}({t.get('uses', 0)}/{t.get('max', 20)})" for t in tackles)
            parts.append(f"钓具={tk}")
        line = f"🎣 {name}：{'，'.join(parts)}；背包还有饵{bag_bait}"

        # ③ 补饵/补钩提醒：动作提示才一天一次（装备没变时不刷）
        reminder = ""
        if (not bait and bag_bait > 0) or (not bait and bag_bait <= 0) or (can_tackle and not tackles):
            day = api.day_key()
            if _ROD_TRACK["day"] != day:
                _ROD_TRACK["day"] = day
                if not bait and bag_bait > 0:
                    reminder = "（可上鱼饵：fish rod bait）"
                elif not bait and bag_bait <= 0:
                    reminder = " — 没饵了！去箱子取虫肉合成／买鱼饵／开箱拿，否则裸竿钓"
                elif can_tackle and not tackles:
                    reminder = "（可上钓具：fish rod tackle）"

        if not eq_changed and not reminder:
            return ""
        if reminder:
            line += reminder
        return line
    except Exception:
        return ""


_SECRET_NOTE_TRACK = {"sig": None}
_SECRET_NOTE_ZH = {"Secret Note": "秘密纸条", "Journal Scrap": "日记残页"}
_SECRET_NOTE_NAMES = tuple(_SECRET_NOTE_ZH)
# 技能书（read_book 消耗领技能/配方）——同样读到即消耗腾占位
_BOOK_HINT_KEYS = ("Quarterly", "Treatise", "Cookbook", "Monster", "Seasonal", "Almanac", "书", "秘籍", "Way", "草中窜", "年历")


# 🪙 淘金提示(2026-08-29)：本图水下有闪光点时才报，不用每 tick 刷屏
_PAN_TRACK = {"sig": None}


def _pan_hint(data: dict) -> str:
    """🪙 本图水下有闪光点时提一句(once per 坐标+有无Pan)，引导 AI 用 scene ops=pan 淘金。
    2026-08-29：只在 orePanPoint != Zero 时注入；淘完(原点归零)/换图自动再判定。
    有锅 → "scene ops=pan 淘金"；没锅 → 提醒但还缺淘金盘。"""
    try:
        ore = (data.get("player") or {}).get("orePan") or {}
        if not ore.get("hasGlint"):
            return ""
        gx, gy = ore.get("x"), ore.get("y")
        sig = (gx, gy, ore.get("hasPan"))
        if _PAN_TRACK["sig"] == sig:
            return ""
        _PAN_TRACK["sig"] = sig
        if ore.get("hasPan"):
            return f"🪙 本图水下有闪光点 ({gx},{gy})——scene ops=pan 淘金"
        return f"🪙 本图水下有闪光点 ({gx},{gy}) 但你没带铜锅(淘金盘)"
    except Exception:
        return ""


def _read_to_free_hint(data: dict) -> str:
    """🎫 背包里有"读到即消耗、腾占位"的道具时提醒一次。2026-08-29 恒拍板：**只提醒一句**——
    检测到背包有【秘密纸条/日记残页/书】→ 说"读掉腾背包占位"即可，不做复杂判断、不加工具。
    ⚠️ 正常玩收集齐了不会再爆纸条→不会误触发；连书也带上（读了就腾格子）。按清单签名去重。"""
    try:
        inv = data.get("inventory") or []
        names = [i.get("name") or "" for i in inv]
        notes = sorted({n for n in names if n in _SECRET_NOTE_NAMES})
        books = sorted({n for n in names if any(k in n for k in _BOOK_HINT_KEYS)})
        if not notes and not books:
            return ""
        sig = (tuple(notes), tuple(books))
        if _SECRET_NOTE_TRACK["sig"] == sig:
            return ""
        _SECRET_NOTE_TRACK["sig"] = sig
        parts = []
        if notes:
            parts.append("、".join(_SECRET_NOTE_ZH.get(n, n) for n in notes))
        if books:
            parts.append("、".join(books))
        return (f"🎫 背包有【{'；'.join(parts)}】读到就消耗、腾背包占位——用 menu read_book 读（书+纸条/残页统一走右键读）")
    except Exception:
        return ""


def _festival_arrive_hint(location: str = "") -> str:
    """🎪 到达节日地点时弹一次引导（当天一次，2026-08-16 恒：鱿鱼节到海滩弹）。
    匹配外层图（FESTIVAL_LOCATIONS: Desert/Beach…）+ 节日内部图（FESTIVAL_POI/SHOPS 的 location:
    DesertFestival/Temp——festival go 后人在内部图，2026-08-19 修）。
    配合 ReadyCheckDialog/BobberBar 的 _festival_activity_hint。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return ""
        key = (d["season"], d["day"])
        f = calendar_data.get_festival_today(d["season"], d["day"])
        if not f:
            return ""
        # 节日涉及的所有图：外层 + 内部（节日场地/商店所在图）
        fest_maps = {calendar_data.FESTIVAL_LOCATIONS[key]} if calendar_data.FESTIVAL_LOCATIONS.get(key) else set()
        for _p in calendar_data.FESTIVAL_POI.get(key, []):
            fest_maps.add(_p.get("location"))
        _sh = calendar_data.FESTIVAL_SHOPS.get(key)
        if _sh:
            fest_maps.add(_sh.get("location"))
        if location not in fest_maps:
            return ""
        if _FEST_ARRIVE_KEY["last"] == key:
            return ""
        _FEST_ARRIVE_KEY["last"] = key
        guide = _fest_guide(calendar_data.FESTIVAL_GUIDE.get(key, ""))
        return f"🎪 已到{f['name']}场地：{guide}" if guide else f"🎪 已到{f['name']}场地"
    except Exception:
        return ""


_STATUE_REMIND_KEY = {"day": None, "found": None, "shown": False}


def _statue_reminder() -> str:
    """🗿 每日雕像提醒（2026-08-16 恒）：农场/屋里放了 祝福雕像(耕种精通奖励)/矮人国王雕像(采矿精通奖励)
    → 每天提醒去摸一次（拿每日 buff）。雕像在场=精通已领。
    2026-08-23 恒：加精通门禁——只有"已领耕种精通"才提示祝福雕像、"已领采矿精通"才提示矮人国王，
    两者都未领 → 不提醒（改由精通状态决定，而非死靠扫雕像在场）。
    ⚠️ /surroundings 锁定当前地点——只在 Farm 扫农场；未找到且不在 Farm 不缓存(下次再试)。
    ⚠️ 2026-08-16 修bug：函数内给 _STATUE_REMIND_KEY 赋值→Python 当局部→顶部缓存检查 UnboundLocalError→必须 global。"""
    global _STATUE_REMIND_KEY
    try:
        d = api.day_key()
        # 🗿 2026-09-05 恒：报一次就好——当天已报就不再注入（此前每条状态条都塞雕像提醒，太吵）
        if _STATUE_REMIND_KEY.get("day") == d and _STATUE_REMIND_KEY.get("shown"):
            return ""
        # 当天扫过、找到雕像但还没报 → 报这一次并标记已报
        if _STATUE_REMIND_KEY.get("day") == d and _STATUE_REMIND_KEY.get("found"):
            _STATUE_REMIND_KEY["shown"] = True
            return _STATUE_REMIND_KEY["found"]
        # 🧱 精通门禁（2026-08-23 恒）：耕种精通→祝福雕像、采矿精通→矮人国王；都未领→不注入
        _farm = _mastery_claimed("farming")
        _mine = _mastery_claimed("mining")
        if not _farm and not _mine:
            return ""
        loc = ""
        try:
            loc = api.state().get("location", {}).get("name", "")
        except Exception:
            pass
        found = []
        # 按已领精通匹配雕像种类（2026-08-23 恒：耕种→祝福雕像、采矿→矮人国王）
        _obj_kws = []
        if _farm: _obj_kws += ["Blessings", "祝福"]
        if _mine: _obj_kws += ["Dwarf King", "矮人国王"]
        # 扫农场几个中心点（雕像通常在农场上/屋附近）
        if loc == "Farm":
            for cx, cy in [(40, 32), (78, 16), (40, 60), (20, 20), (60, 60), (80, 60), (20, 60), (60, 20)]:
                sur = api._get("/surroundings", {"x": cx, "y": cy, "radius": 26})
                for t in (sur.get("tiles") or []):
                    o = t.get("object") or ""
                    if any(k in o for k in _obj_kws):
                        pt = (t.get("x"), t.get("y"))
                        if (o, pt) not in found:
                            found.append((o, pt))
        # 当前屋家具（也可能放屋里）
        try:
            fu = api._get("/furniture")
            for f in (fu.get("furniture") or []):
                n = (f.get("name") or "")
                if any(k in n for k in _obj_kws):
                    found.append((n, (f.get("x"), f.get("y"))))
        except Exception:
            pass
        txt = ""
        if found:
            names = "、".join(dict.fromkeys(n for n, _ in found))
            first = found[0][1]
            # 精通说明按实际扫到的雕像给（2026-08-23 恒：只提示已领精通的对应项，不硬编码并列）
            why = []
            for n, _ in found:
                if "Blessings" in n or "祝福" in n: why.append("祝福=耕种精通")
                if "Dwarf King" in n or "矮人国王" in n: why.append("矮人国王=采矿精通")
            why_txt = f"（{'，'.join(dict.fromkeys(why))}奖励）" if why else ""
            txt = f"🗿 摸{names}拿每日buff{why_txt}——interact 点 ({first[0]},{first[1]})"
            _STATUE_REMIND_KEY = {"day": d, "found": txt, "shown": True}   # 报这一次，今天不再（2026-09-05）
        # 没找到且不在农场 → 不缓存（下次状态读到农场再试）
        return txt
    except Exception:
        return ""


# 🛠️ 本图设备实时就绪(2026-08-31 恒)：只扫当前图(Farm含室内)，把"今天还没报过"的完成设备
#    按产物(料)种类+数量报一次即止。短时设备(鱼饵机/熏鱼机)当天中途完成也能被看到。
#    日报(晨报)仍每日重置再报，二者独立。全图扫描60s节流+切图必扫，避免每次调用都拉全图。
_MACHINE_READY_SEEN = {}            # {(day_key, loc, x, y): True} 鱼塘专用"一天一次"去重
_MACHINE_LAST_STATUS = {}           # {(day_key, loc, x, y): 上次状态} 机器就绪=边沿(非ready→ready)才报(恒 08-31)
_MACHINE_READY_DAY = {"key": None, "loc": None}   # 日翻转/切图检测
_MACHINE_READY_TS = {"ts": 0.0}
_MACHINE_READY_COOLDOWN = 60        # 秒：全图扫描节流(只对非钓鱼点)
# 恒 08-31 拍板：**只在这批钓鱼点做快速检测(不节流)**——鱼饵机 14s 一轮(游戏10min≈现实14s),节流会漏。
#   这些图机器少,每次工具调用都扫、没机器直接跳。其余(农场/建筑室内/BusStop/Town/Woods等)照旧 60s 节流。
#   ⚠️ 采石场= Mountain 图一部分(/warps 无 Quarry),鹈鹕镇矿井内= UndergroundMine(矿层名带层号→用前缀)。
_FAST_SCAN_LOCS = {"desert", "forest", "mountain", "beach", "town",
                   "islandsouth", "islandwest", "islandeast", "islandsoutheast"}  # 小写,配合 _is_fast_scan 里 loc.lower()


def _is_fast_scan(loc: str) -> bool:
    """是否钓鱼点(快速检测、不节流)。姜岛排除北部(无水的雷欧/宝石谜题丛林)。"""
    if not loc:
        return False
    l = str(loc).lower()
    return l in _FAST_SCAN_LOCS or l.startswith("undergroundmine")


# 农场的**短时**设备做实时(恒 08-31 加回,排除长时小桶/木桶/罐头瓶/避雷针=那批上千台扫+报都重)。
#   种子生产器=Seed Maker、鱼饵制造机=Bait Maker(恒 08-31 补"打得不太标准"更正)。Bone Mill/Loom 目前农场0台也入集。
_FARM_SHORT_TYPES = {"Cheese Press", "Mayonnaise Machine", "Bait Maker", "Recycling Machine",
                     "Fish Smoker", "Furnace", "Heavy Furnace", "Seed Maker", "Bone Mill", "Loom"}

# 农场建筑室内(和 Farm 一起算"农产短时"实时场景);Farm 用 farm_report 扫全农场+室内,建筑内用 /machines。
_FARM_BUILDING_LOCS = {"big shed", "cabin", "cellar", "greenhouse", "shed",
                       "coop", "coop2", "coop3", "deluxe coop", "barn", "barn2", "barn3", "deluxe barn"}


def _is_farm_short_scan(loc: str) -> bool:
    """是否"农场短时设备"实时场景 = 农场室外(含建筑室内)。"""
    if not loc:
        return False
    l = str(loc).lower()
    return l == "farm" or l in _FARM_BUILDING_LOCS or l.startswith("cellar")


def _machine_ready_hint(loc_name: str = "") -> str:
    """🔔 设备实时就绪：机器就绪=**边沿+产物**(非ready→ready 或 换产物)才报(恒 08-31)。
    - 钓鱼点 `_is_fast_scan`(沙漠/森林/Mountain/Town/沙滩/姜岛除北部/矿井内):不节流,报当前图**全部**机器(鱼饵机14s一轮不漏)。
    - 农场(含建筑室内):节流60s,只报 `_FARM_SHORT_TYPES`(压酪机/蛋黄酱机/鱼饵机/回收机/熏鱼机/熔炉/重型熔炉/种子机/碎骨机/织布机),
      排除长时小桶/木桶/罐头瓶/避雷针(那批上千台,日报一次就够)。
    - 其它(BusStop/秘密森林):不实时,靠日报。按产物种类+数量聚合。"""
    global _MACHINE_READY_SEEN, _MACHINE_READY_DAY, _MACHINE_READY_TS
    try:
        if not loc_name or str(loc_name).lower() in ("?", "未知"):
            return ""
        dk = api.day_key()
        if not dk:
            return ""
        # 日翻转 → 清空已报集合（跟着晨报第二天重新报）
        if _MACHINE_READY_DAY["key"] != dk:
            _MACHINE_READY_DAY["key"] = dk
            _MACHINE_READY_SEEN = {}
            _MACHINE_LAST_STATUS = {}
        fast = _is_fast_scan(loc_name)
        farm_short = (not fast) and _is_farm_short_scan(loc_name)
        if not fast and not farm_short:
            # 非钓鱼点、非农场(如 BusStop/秘密森林/Woods):不实时,靠日报。
            return ""
        # 节流:农场短时 60s(扫 farm_report/建筑机器较多);钓鱼点不节流(机器少)。
        loc_changed = _MACHINE_READY_DAY["loc"] != loc_name
        _MACHINE_READY_DAY["loc"] = loc_name
        now = time.time()
        if farm_short:
            if not loc_changed and (now - _MACHINE_READY_TS["ts"]) < _MACHINE_READY_COOLDOWN:
                return ""
            _MACHINE_READY_TS["ts"] = now
        # 机器源:农场 → farm_report(含建筑室内)+当前室外;建筑内/钓鱼点 → 当前图 /machines。
        if str(loc_name).lower() == "farm":
            fr = _fetch_farm_report()
            if not fr.get("ok"):
                return ""
            ml = (fr.get("machines") or {}).get("machines") or []
            try:
                ml = ml + (list((api._ai_get("/machines") or {}).get("machines") or []))
            except Exception:
                pass
            scope = [m for m in ml if (m.get("location") or "") == "Farm" or m.get("building")]
        else:
            try:
                ml = list((api._ai_get("/machines") or {}).get("machines") or [])
            except Exception:
                ml = []
            if not ml:
                return ""
            scope = [m for m in ml if str(m.get("location") or "").lower() == loc_name.lower()]
        # 农场短时:只报短时机型(排除小桶/木桶/罐头瓶/避雷针那批);钓鱼点报全部(机器少)。
        if farm_short:
            scope = [m for m in scope if (m.get("type") or "") in _FARM_SHORT_TYPES]

        # 机器就绪=边沿+产物双重判(恒 2026-08-31):**状态非ready→ready** 或 **同台换产物(河豚→大头鱼)** 都算新完成、报出来。
        #   ⚠️ 只比 status 会漏:上一台记成 ready(河豚),再放大头鱼完成仍 ready → ready==ready 跳过(恒实测漏报 Bullhead)。
        #   记 (status, heldItem):就绪 且(上次非ready 或 产物变了)→报;同产物持续 ready 不重刷。
        #   _MACHINE_LAST_STATUS[key] = (status, heldItem)
        new_ready = []
        for m in scope:
            key = (dk, m.get("location"), m.get("x"), m.get("y"))
            st = m.get("status")
            item = m.get("heldItem") or ""
            prev = _MACHINE_LAST_STATUS.get(key)      # (prev_status, prev_item) or None
            if st == "ready":
                if not prev or prev[0] != "ready" or prev[1] != item:
                    new_ready.append(m)
            _MACHINE_LAST_STATUS[key] = (st, item)
        if not new_ready:
            return ""

        # 按产物(料)种类+数量聚合（日报按机型，这里按产出的东西）
        agg = {}
        for m in new_ready:
            prod = m.get("heldItem") or "?"
            agg[prod] = agg.get(prod, 0) + 1

        # 🐟 鱼塘产出:现只在日报(morning_report)里报(农场实时已按恒拍板移除,不做本图实时)。
        parts = [f"{k}×{v}" for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]
        return f"🔔 本图新就绪: {' '.join(parts)} —— work_building({loc_name}) 收"
    except Exception:
        return ""


def _festival_poi() -> str:
    """🍳 节日限定 POI：今天节日的特殊交互点（如沙漠节厨师 DesertFood，对话做 buff 料理）。
    FESTIVAL_POI 数据（位置+流程+选项）。非节日或无登记 → 提示。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return "⚠️ 当前不在节日（festival today/next 看安排）"
        key = (d["season"], d["day"])
        pois = calendar_data.FESTIVAL_POI.get(key, [])
        if not pois:
            return f"🎪 今天（{d['season']} {d['day']}日）没有登记的限定 POI"
        lines = [f"🎪 节日限定 POI（{key[0]} {key[1]}日）:"]
        for p in pois:
            lines.append(f"  {p['poi']} @{p['location']}({p['tile'][0]},{p['tile'][1]})，站{p.get('stand')}朝{p.get('face')}")
            lines.append(f"    {p.get('desc', '')}")
            lines.append(f"    {p.get('opts', '')}")
            lines.append(f"    💡 {p.get('note', '')}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


def _festival_strength(delay: int = 400) -> str:
    """💪 力量测试（星露谷展览会/秋16，反编译 StrengthGame.cs）：AI 调 delay(毫秒) 摸索逼近99。
    【delay 区间】单轮时长有上限：进度条**完整一轮 0→99→0 约 1秒**(恒 2026-08-23 观感，半程~0.5s到顶)，
    峰值每 ~1s 出现一次；推荐试 delay=200~1300ms 抓峰值（硬上限 2000ms 逾限直接 clamp——AI 别无限等）。
    一次调用=完整一轮：开机器 → 等 delay → 挥锤 → **读结果对话 + 点掉那多余一下(清场)** → 返还「力量等级：X」。
    机制：power 0↔100 震荡(changeSpeed=3或4 每局随机)→ 敲击后 ~640ms 冻结=等级；≥99 大成功 / <2 极弱都 festivalScore+1；
    每局速度随机→ 不能锁 99，= 在盲猜和找规律间摸拍子的乐趣。"""
    try:
        # 清残留（上一个结果对话/StrengthGame）
        for _ in range(4):
            m = api._get("/menu")
            if not m.get("open"):
                break
            if m.get("type") in ("DialogueBox", "StrengthGame"):
                api._post("/click", {"no_move": True, "no_mouse": True})
                time.sleep(0.4)
            else:
                break
        # 开/复用力量测试机（站 29,56 朝右 1，机器 30,56）
        m = api._get("/menu")
        if not (m.get("open") and m.get("type") == "StrengthGame"):
            api._post("/position", {"x": 29, "y": 56})
            time.sleep(0.6)
            api._post("/face", {"direction": 1})
            time.sleep(0.3)
            api._post("/interact", {"x": 30, "y": 56})
            time.sleep(1.0)
            m = api._get("/menu")
            if not (m.get("open") and m.get("type") == "StrengthGame"):
                return f"⚠️ 力量测试没开（当前: {m.get('type')}）"
        # 等 delay（clamp 硬上限 2000ms）
        delay = min(max(int(delay), 0), 2000)
        time.sleep(delay / 1000.0)
        # 挥锤
        api._post("/click", {"no_move": True, "no_mouse": True})
        # 轮询结果对话（约1.6~2.6s 后弹）
        result = ""
        for _ in range(22):
            time.sleep(0.2)
            m = api._get("/menu")
            if m.get("open") and m.get("type") == "DialogueBox":
                dlg = (m.get("dialogue") or "").strip()
                if dlg and ("力量等级" in dlg):
                    result = dlg
                    break
        # 点掉多余那一两下（结果对话→退 StrengthGame），清场
        for _ in range(3):
            m = api._get("/menu")
            if not m.get("open"):
                break
            api._post("/click", {"no_move": True, "no_mouse": True})
            time.sleep(0.4)
        if not result:
            return f"🔨 已挥锤 delay={delay}ms，但结果对话没读到（可能时序/已点掉）"
        return (f"🔨 {result}（delay={delay}ms）"
                f"\n💡 结果每局有随机(速度3/4)；进度条**循环震荡**，高点和低谷约每1s交替——可多试几个delay先看出波形，"
                f"再奔着高点去，别只往单峰收缩（2026-08-23 恒：保留乐趣，只点机制不教策略）")
    except Exception as e:
        return f"❌ {e}"


def _flower_dance_guard() -> str:
    """💃 花舞节门禁：dance 只在花舞节当天(spring24)暴露（恒 2026-08-20：非花舞节不显示）。
    返回空串=放行；否则=拒绝原因。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return "⚠️ 游戏未连接"
        if (d["season"], d["day"]) != ("spring", 24):
            return "🎪 今天不是花舞节（spring 24）——跳舞邀请只在花舞节当天可用"
        return ""
    except Exception:
        return "⚠️ 门禁检查失败"


def _festival_dance(target: str = "") -> str:
    """💃 花舞节跳舞邀请（2026-08-21：正常端口已通，direct=true 退役）
    - 邀玩家（target 留空=房主）：走提案系统，对方弹"XX想和你跳舞"Yes/No 接受框（最自然）
    - 邀 NPC：**不走本工具**——用裸 /interact 站紧邻格弹「什么事？」→选「邀请XX作舞伴」（需4心+）
    ⚠️ 必须在舞会开始前（跟刘易斯对话/主环节前）调用；双方需已在花舞节地图。"""
    g = _flower_dance_guard()
    if g:
        return g
    r = api.dance_invite(target=target)
    if r.get("ok"):
        return f"💃 {r.get('note', r.get('action'))}"
    return f"⚠️ {r.get('error', '邀请失败')}"


def _festival_help() -> str:
    """🎪 节日玩法引导：今天节日该干嘛；今天不是节日→显示下一个节日引导（前一天做准备）。
    FESTIVAL_GUIDE 数据（wiki 详细版：限时/稀有物/商店）。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return "⚠️ 游戏未连接"
        f = calendar_data.get_festival_today(d["season"], d["day"])
        if f:
            guide = _fest_guide(calendar_data.FESTIVAL_GUIDE.get((d["season"], d["day"])))
            if not guide:
                return f"🎪 {f['name']}（今天）暂无玩法引导（FESTIVAL_GUIDE 未登记）"
            return f"🎪 {f['name']}（今天）玩法：{guide}"
        # 今天不是节日 → 下一个节日引导（做准备）
        nxt = calendar_data.get_next_festival(d["season"], d["day"])
        if not nxt:
            return "没有更近的节日了。"
        guide = _fest_guide(calendar_data.FESTIVAL_GUIDE.get((nxt["season"], nxt["day"])))
        if not guide:
            return f"🎪 下一个节日 {nxt['name']}（{nxt['season']}{nxt['day']}，剩 {nxt['days_left']} 天）：暂无玩法引导"
        return f"🎪 下一个节日 {nxt['name']}（{nxt['season']}{nxt['day']}，剩 {nxt['days_left']} 天）：{guide}"
    except Exception as e:
        return f"❌ {e}"


def _festival_prep() -> str:
    """🎯 节日备战明细：今天节日的评分/商店/要点；今天不是节日→下一个节日（提前准备）。
    FESTIVAL_PREP 数据（wiki 详细版：百乐汤评分/农展得分/策略）。GUIDE 只给精要，备战看这里。"""
    try:
        d = _festival_now_data()
        if not d.get("ok"):
            return "⚠️ 游戏未连接"
        # 今天节日优先
        f = calendar_data.get_festival_today(d["season"], d["day"])
        if f:
            prep = calendar_data.FESTIVAL_PREP.get((d["season"], d["day"]))
            if prep:
                return f"🎯 {f['name']}（今天）备战详情：\n{prep}"
            return f"🎯 {f['name']}（今天）暂无专项备战数据（FESTIVAL_PREP 未登记），可用 festival help 看玩法"
        nxt = calendar_data.get_next_festival(d["season"], d["day"])
        if not nxt:
            return "没有更近的节日了。"
        prep = calendar_data.FESTIVAL_PREP.get((nxt["season"], nxt["day"]))
        if not prep:
            return f"🎯 下一个节日 {nxt['name']}（{nxt['season']}{nxt['day']}，剩 {nxt['days_left']} 天）：暂无专项备战数据"
        return f"🎯 下一个节日 {nxt['name']}（{nxt['season']}{nxt['day']}，剩 {nxt['days_left']} 天）备战详情：\n{prep}"
    except Exception as e:
        return f"❌ {e}"


def _maze_walk(waypoints: str = "", location: str = None, max_wait: int = 18, max_seg: int = 200) -> str:
    """🚶 走迷宫——依次 walk_to 多个中间点（"x,y x,y …"空格/分号分隔），每段等到达再走下一段。
    万灵节迷宫（偶数/奇数年布局都变）通用：换了布局喂不同点位即可。参数：waypoints / location / max_wait。
    段1: 右walk_to(21,54) 这种走法链可直接喂进来；也支持手挑中点。"""
    pts = []
    for tok in str(waypoints or "").replace(";", " ").split():
        if "," in tok:
            try:
                x, y = tok.split(",", 1)
                pts.append((int(x), int(y)))
            except ValueError:
                pass
    if not pts:
        return "⚠️ 没解析到中间点，用空格/分号分隔的 'x,y x,y …' 格式"
    pts = pts[: max_seg]
    try:
        st = api.state()
        loc = location or (st.get("location") or {}).get("name", "")
        sp = st.get("player") or {}
        start_pos = (sp.get("x"), sp.get("y"))
    except Exception as ex:
        return f"⚠️ 拿不到当前场景: {ex}"
    # ⚠️ 只回摘要不逐段刷屏（省token；AI 用 walk_to 每段基本都能到，失败才值得提）
    got = 0; fails = []; total = len(pts)
    t0 = time.time()
    for i, (x, y) in enumerate(pts, 1):
        try:
            api._post("/walk_to", {"location": loc, "x": x, "y": y})
        except Exception as ex:
            fails.append(f"段{i}({x},{y})发送失败:{ex}")
            continue
        ok = False
        for _ in range(int(max_wait)):
            time.sleep(0.7)
            try:
                pp = (api._get("/state") or {}).get("player", {})
                if not pp.get("isMoving"):
                    ok = True
                    break
            except Exception:
                pass
        if ok:
            got += 1
        else:
            fails.append(f"段{i}({x},{y})超时")
    end = (api._get("/state") or {}).get("player", {})
    secs = int(time.time() - t0)
    head = (f"🚶 迷宫走法 {total} 段, {got} 段到达✅, "
            f"起({start_pos[0]},{start_pos[1]})→末({end.get('x')},{end.get('y')}), ∫{secs}s")
    if fails:
        return head + "\n⚠️ 未到段: " + "; ".join(fails)
    return head + "\n✅ 全程走通，无需逐段日志"


@mcp.tool()
def _festival_ice_fish() -> str:
    """🎣 冰雪节冰钓比赛自动化（2026-08-28 恒：冬8，阻塞跑 ice_fishing 等比赛自然结束发奖）。
    前置：AI 在冰雪节场地 + 比赛已开始（festivalTimer>0——AI/玩家先对话刘易斯开赛）。
    走位→钓满2分钟→结算。返回"钓 N 条（赢线/不足5条）"。"""
    st = api.state()
    t = st.get("time") or {}
    if str(t.get("season") or "").lower() != "winter" or int(t.get("dayOfMonth") or 0) != 8:
        return "❌ 今天不是冰雪节(冬8)，冰钓只在冬8能跑"
    loc = (st.get("location") or {}).get("name", "") or ""
    if loc not in ("Temp", "Forest-IceFestival"):
        return f"❌ 不在冰雪节场地(loc={loc})——先 festival go 到冰雪节（比赛会在节日场地进行）"
    ft = int((api.festival_status() or {}).get("festivalTimer") or -1)
    if ft <= 0:
        return "⏳ 冰钓比赛还没开始——先 festival interact 找刘易斯/请玩家开始比赛（比赛开始后本工具才钓，别白等）"
    return _ice_fishing_blocking()


@mcp.tool()
def festival(ops: str = "", kw: dict | None = None) -> str:
    """🎪 节日域。ops: today(今天节日) next(下一个) go(去) info(实况) interact(互动,空参=社交巡礼)
    answer(应答 N) shop(节日商店) eggs(找蛋规划) egg_note(纸条) egg_run(捡蛋) poi(限定点)
    dance(跳舞邀请 target) help(玩法) prep(备战明细) maze(迷宫坐标奇偶年) maze_walk(走迷宫 waypoints="x,y x,y …" 依次walk_to)。细节→help(festival)。
    """
    dispatch = {
        "today": _festival_today, "今天": _festival_today,
        "next": _festival_next, "下一个": _festival_next,
        "go": _festival_go, "去": _festival_go,
        "info": _festival_info, "实况": _festival_info,
        "interact": _festival_interact, "互动": _festival_interact,
        "answer": _festival_answer, "应答": _festival_answer,
        "shop": _festival_shop, "商店": _festival_shop,
        "eggs": _festival_eggs, "找蛋": _festival_eggs, "蛋": _festival_eggs,
        "egg_note": _festival_egg_note, "纸条": _festival_egg_note, "记": _festival_egg_note,
        "egg_run": _festival_egg_run, "捡蛋": _festival_egg_run, "捡": _festival_egg_run,
        "dance": _festival_dance, "跳舞": _festival_dance, "邀请": _festival_dance, "舞": _festival_dance,
        "help": _festival_help, "引导": _festival_help, "玩法": _festival_help,
        "prep": _festival_prep, "准备": _festival_prep, "备战": _festival_prep,
        "maze_walk": _maze_walk, "走迷宫": _maze_walk, "迷宫走": _maze_walk,
        "maze": _festival_maze, "迷宫": _festival_maze,
        "poi": _festival_poi, "限定": _festival_poi, "厨师": _festival_poi,
        "strength": _festival_strength, "力量": _festival_strength, "测力": _festival_strength,
        "ice_fish": _festival_ice_fish, "冰钓": _festival_ice_fish, "冰": _festival_ice_fish,
        "display_fill": _menu_display_fill, "放满": _menu_display_fill, "展位放": _menu_display_fill,
        "display_takeback": _menu_display_takeback, "收好": _menu_display_takeback,
        "收": _menu_display_takeback, "取回": _menu_display_takeback, "展位收": _menu_display_takeback,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


def _movie_npc_plan(npc: str, m: dict) -> str:
    """指定NPC：看当前电影多少友好 + 买哪个零食。"""
    fav = npc in (m.get("favorites") or [])
    like = npc in (m.get("likes") or [])
    watch = "+200(最爱)" if fav else ("+100(喜爱)" if like else "+0(不爱,不减)")
    best = None
    for name, s in movie_data.SNACKS.items():
        if "所有村民" in (s.get("favorites") or []) or npc in (s.get("favorites") or []):
            best = (name, s["price"], "+50")
            break
    if not best:
        for name, s in movie_data.SNACKS.items():
            if npc in (s.get("likes") or []):
                best = (name, s["price"], "+25")
                break
    snack_txt = f"买「{best[0]}」{best[2]} ({best[1]}g)" if best else "没找到他爱的零食"
    return f"  {npc}：看《{m.get('name')}》{watch}；{snack_txt}"


@mcp.tool()
def movie(npc: str = "") -> str:
    """🎬 电影院观影攻略（2026-08-16 wiki整理）：当前季/年放什么电影+谁最爱(+200)、买啥零食(+50)。
    npc=指定村民 → 给该村民的方案。每周1次1人；矮人/桑迪爱所有电影。
    看最爱+200/喜爱+100/不爱+0；零食最爱+50/喜爱+25。"""
    try:
        st = api.state()
        t = st["time"]
        season = (t.get("season") or "").lower()
        year = t.get("year") or 1
        m = movie_data.movie_today(season, year)
        if not m:
            return "🎬 今天没有电影（查不到季节/年）"
        cycle = "一" if year % 2 == 1 else "二"
        lines = [f"🎬 当前放映（{season} 年{year}·第{cycle}轮）: 《{m.get('name')}》"]
        if npc:
            lines.append(_movie_npc_plan(npc, m))
        else:
            lines.append(f"❤️ 最爱(+200): {'、'.join(m.get('favorites') or []) or '无'}")
            likes = m.get("likes") or []
            lines.append(f"👍 喜爱(+100): {'、'.join(likes[:10])}{'…' if len(likes) > 10 else ''}" if likes else "👍 喜爱: 无")
            lines.append("💡 矮人/桑迪爱所有电影；邀请1人/周1次；零食→snack 工具")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


@mcp.tool()
def snack() -> str:
    """🍿 电影院零食清单（买给NPC：最爱+50/喜爱+25/不爱+0）。票价外的小卖部消费。"""
    try:
        lines = ["🍿 零食（价格 | 最爱+50 | 喜爱+25）:"]
        for name, s in sorted(movie_data.SNACKS.items(), key=lambda x: x[1]["price"]):
            fav = "、".join(s.get("favorites") or []) or "—"
            likes = s.get("likes") or []
            like = ("、".join(likes[:4]) + ("…" if len(likes) > 4 else "")) if likes else "—"
            lines.append(f"  {name} {s['price']}g | ❤️{fav} | 👍{like}")
        lines.append("💡 星之果实雪糕(1250g)全村民最爱(除科罗布斯)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ {e}"


@mcp.tool()
def read_book(name: str) -> str:
    """📚 读书/读纸条（统一走游戏真读法：Object.performUseAction = 右键读）。
    ⚠️ 2026-08-29 恒反编译：旧 select+confirm 走 Game1.pressActionButton，只认 ActiveObject 不碰 CurrentItem → 读不了纸条。
    真读= performUseAction（书领技能/纸条残页记收藏+弹内容，读到即消耗=腾背包占位）。本工具改为 select 设手持 → /use mode=read。
    书：Combat Quarterly(战斗季刊) / Queen Of Sauce Cookbook / Horse Treatise 等；纸条：Secret Note / Journal Scrap。
    先买书：book_stall 买/回收。"""
    try:
        r = api.select(name)
        if not r.get("ok"):
            return _with_state(f"❌ 没找到「{name}」: {r.get('error', '')}")
        time.sleep(0.4)
        rr = api._post("/use", {"mode": "read"})
        if rr.get("ok") and rr.get("consumed"):
            menu = rr.get("menu")
            note = f"，弹出{menu}" if menu else ""
            return _with_state(f"📚 已读「{name}」（读到消耗、腾背包占位{note}）")
        return _with_state(f"📚 读「{name}」没生效: {rr.get('error', '可能已读过/该物品不能读')}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def confirm_settlement() -> str:
    """🧾 确认过夜结算，进下一天：新一天开始时有收益结算界面（ShippingMenu，游戏时间暂停），
    这是和 user 难得的聊天窗口——先复盘今天、商量明天的安排，**聊完了再调本工具确认**，
    两人一起进入新一天。不确认的话 10 分钟兜底自动关（AI 挂了才用得上）；幂等。
    """
    try:
        r = api.confirm_settlement()
        if not r.get("ok"):
            return _with_state(f"❌ 确认结算失败: {r.get('error', r)}")
        # 过夜结算触发白板复盘（A2 看昨天计划，规划今天）
        wb_extra = ""
        try:
            wb = whiteboard_read()
            wb_extra = "\n" + "\n".join(wb.split("╌")[0].splitlines()[1:])
        except Exception:
            pass
        # 🚫 计划模式已退役（2026-08-17 恒）：不再有"明日计划"段——规划只走白板（whiteboard_write）。
        #    原 plan_extra 段已移除（存档见 _plan_* 代码）。
        return _with_state(f"🧾 已确认过夜结算，和{_host_name()}一起进入新的一天！{wb_extra}\n💡 复盘白板→结合晨报→白板记今天计划（whiteboard_write）")
    except Exception as e:
        return _with_state(f"❌ {e}")


# ⚠️ 2026-08-14：crawl_bed 工具已删除——统一进 go_sleep(who)（睡自家/别人家都走它，爬床彩蛋内置）。


# ═══════════════════════════════════════════
#  精细操控工具
# ═══════════════════════════════════════════

@mcp.tool()
def use_tool(name: Optional[str] = None) -> str:
    """🛠️ 使用工具
    挥动当前选中的工具（或指定名称的工具）。
    ⚠️ **锄地/播种/浇水别用这个**——耕种走 farm 域（farm ops=till/plant/water），
    use_tool 只适合单次挥动（砍树/敲矿/割草/挖采集等）。手动锄/浇会漏格失败（2026-08-15 防回退）。

    Args:
        name: 工具名（Axe / Pickaxe / Hoe / Watering Can / Scythe 等），
              不指定则用当前选中的工具
    """
    try:
        api.use_tool(name or "current")
        return _with_state(f"🛠️ {'使用: ' + name if name else '挥动当前工具'}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def interact() -> str:
    """🤝 与面前的 NPC / 物体 / 机器互动
    相当于按「确认键」，可用于：
    - 与 NPC 对话
    - 收集机器产物（小桶、熔炉等）
    - 开箱子
    - 触发机关
    - 📚 **读书**：手持技能书(select_item选中)时点 confirm 就读（消耗书领技能；别用 use_tool//use——那会把书放地上收不回）
    """
    try:
        api.interact()
        return _with_state("🤝 已互动")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def furniture_pickup(tile_x: int, tile_y: int) -> str:
    """🪑 拿起家具（把摆放的家具收回背包）
    站到家具旁边后指定它的瓦片坐标，等同游戏左键点击拿走（SDV 1.6 右键拿不起家具）。
    限制：只能拿自己摆的家具（农场初始自带的拿不起）、需站旁边（约1.5格内）、
    背包/快捷栏需有空位（满了静默失败）、不能开菜单。
    摆放是另一条路：select_item(家具名) → face(方向) → use_item() 放下。

    Args:
        tile_x: 家具所在的瓦片 X 坐标
        tile_y: 家具所在的瓦片 Y 坐标
    """
    try:
        r = api.furniture_pickup(tile_x, tile_y)
        if r.get("ok") and r.get("picked"):
            name = r.get("furniture") or "家具"
            return _with_state(f"🪑 拿起了 {name}，已收回背包")
        return _with_state(f"⚠️ 拿起失败（picked={r.get('picked')}）：站近点/确认是自己摆的/背包有空位")
    except Exception as e:
        return _with_state(f"❌ 拿起家具失败: {e}")


@mcp.tool()
def scan_furniture() -> str:
    """🪑 扫描当前地点的所有家具（位置 + 类型）
    返回每件家具的名字/瓦片坐标/尺寸/类型/TV标记。先扫再 interact_at / furniture_pickup。
    能认出：📺 TV、📅 日历（开季节日历）、🔥 壁炉（点火）、家具目录（开商店）等。
    """
    try:
        r = api.furniture_scan()
        if not r.get("ok"):
            return _with_state(f"❌ 扫描失败: {r.get('error')}")
        fs = r.get("furniture", [])
        if not fs:
            return _with_state(f"🏠 {r.get('location')} 没有家具")
        lines = [f"🏠 {r.get('location')} 共 {len(fs)} 件家具："]
        for f in fs:
            tag = "📺" if f.get("isTV") else "🪑"
            lines.append(f"{tag} {f['name']} @({f['x']},{f['y']}) {f['width']}x{f['height']} 类型{f['furnitureType']}")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 扫描家具失败: {e}")


def _is_chest_tile(t: dict) -> bool:
    """该格是否为箱子/容器（别砸：满箱会挪位、空箱变掉落——处理麻烦，AI 直接跳过）。
    同时认 **objId(QualifiedItemId) 和 中英文名**——/surroundings 给英文名(Chest)、/dump_tile 给中文名(宝箱)，
    靠 objId(含 chest/bigchest/box/storage) 才稳定；只匹配英文名会漏掉中文"宝箱"。"""
    o = t.get("object")
    name = (o.get("name") if isinstance(o, dict) else (o or "")) or ""
    oid = (o.get("objId") if isinstance(o, dict) and o.get("objId") else None) or t.get("objId") or ""
    s = str(name).lower(); oid = str(oid).lower()
    if any(k in s for k in ("chest", "box", "宝箱", "石箱", "储物", "收纳", "贮藏", "junimo", "shipping bin")):
        return True
    if any(k in oid for k in ("chest", "box", "storage", "shipping")):
        return True
    return False


def _break_worth(t: dict) -> bool:
    """目标格是否值得挥镐：有设备/石头（object 存在，**排除箱子/容器**） 或 已耕且无作物（翻地重耕）。
    其余(空地/作物/树/箱子)跳过——箱子别砸，白敲。"""
    if _is_chest_tile(t):
        return False
    if t.get("object"):
        return True
    if (t.get("terrain") or "") == "HoeDirt" and not t.get("crop"):
        return True
    return False


def _stand_near(tx: int, ty: int):
    """目标格旁的可站格（4 正邻优先，/passable 判），找不到返回 None。"""
    for dx, dy in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
        nx, ny = tx + dx, ty + dy
        try:
            if api._post("/passable", {"x": nx, "y": ny}).get("passable"):
                return (nx, ny)
        except Exception:
            continue
    return None


@mcp.tool()
def place_item(name: Optional[str] = None, x: Optional[int] = None, y: Optional[int] = None) -> str:
    """🪧 放置物品/播种（把背包物品放到指定格：落地/种树；scene 域）
    流程：select(name) → /use{x,y} → placementAction 放地上（箱子/机器/蟹笼）或种下（树种/作物种子）。
    ⚠️ **只能放可放置/可种物**（箱子、机器、蟹笼、树种、作物种子等）；书/纸条等不可放置物会失败且**不消耗**（安全，不会丢地上收不回）。

    Args:
        name: 物品英文名（Chest / Keg / Maple Seed / Crab Pot …），不传则用当前手上物
        x, y: 目标瓦片坐标（留空=放玩家面前格）
    """
    try:
        if name:
            api.select(name)
            time.sleep(0.2)
        if x is not None and y is not None:
            r = api._post("/use", {"x": int(x), "y": int(y), "force": True})
        else:
            r = api.use_item(force=True)
        if r.get("ok"):
            _it = name or r.get("item") or "物品"
            _tile = f"({x},{y})" if x is not None else "面前格"
            return _with_state(f"🪧 已放置「{_it}」@{_tile}")
        return _with_state(f"❌ 放置失败: {r.get('error', '未知')}（物品未消耗、未丢地）")
    except Exception as e:
        return _with_state(f"❌ 放置出错: {e}")


@mcp.tool()
def break_tile(x: int, y: int, steps: int = 1, radius: int = 0) -> str:
    """⛏️ 拆/敲指定格或范围（敲石头 / 翻已耕 / 拆可回收小物件；farm 或家具附近）
    换手持**镐子**（恒拍板：只敲镐子——锄头/斧头有蓄力/范围更难搞，翻地走 farm till、砍树走 farm chop）→
    站到目标旁（够不着自动 /position 到相邻可站格）→ 面朝 → 挥步骤次数。
    radius=N 扫周围 N 格方形范围，**自动跳过空地格**（无设备/石头且未耕——敲了白敲）。
    能敲：石头/矿点（格上有物件，**排除箱子/容器**）、已耕地翻新（terrain=HoeDirt 且无作物）。
    🧰 **箱子/容器格一律跳过不砸**（SDV：满箱会挪位、空箱变掉落——处理麻烦，AI 不碰，单独报"箱子格跳过"）。

    Args:
        x, y: 中心格（radius=0 时就是目标格）
        steps: 每格挥击次数（石头可能要多下）
        radius: 范围半径（0=只敲单格；>0 方圆并自动跳过空地格）
    """
    try:
        tool = "Pickaxe"
        api.select(tool)
        time.sleep(0.2)
        tgts, chest_skip = [], []
        if radius and radius > 0:
            try:
                tiles = (api._get("/surroundings", {"radius": radius}).get("tiles") or [])
            except Exception:
                tiles = []
            bypos = {(t.get("x"), t.get("y")): t for t in tiles if "x" in t and "y" in t}
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    t = bypos.get((x + dx, y + dy))
                    if t is None:
                        continue
                    if _is_chest_tile(t):
                        chest_skip.append((x + dx, y + dy))   # 🧰 箱子/容器格：别砸
                    elif _break_worth(t):
                        tgts.append((x + dx, y + dy))
        else:
            # 单格也判箱子（避免 AI 把箱子砸出来/挪走），/dump_tile 拿 object 名
            try:
                dt = (api._get("/dump_tile", {"x": x, "y": y}).get("tile") or {})
                if _is_chest_tile(dt):
                    chest_skip.append((x, y))
                else:
                    tgts.append((x, y))
            except Exception:
                tgts.append((x, y))
        hit, stood_fail = [], []
        for tx, ty in tgts:
            st = api.state().get("player") or {}
            sx, sy = st.get("x"), st.get("y")
            if sx is None or sy is None:
                stood_fail.append((tx, ty)); continue
            if abs(sx - tx) > 1 or abs(sy - ty) > 1:
                stand = _stand_near(tx, ty)
                if not stand:
                    stood_fail.append((tx, ty)); continue
                api.position(stand[0], stand[1]); time.sleep(0.2)
            api.face(api.face_toward(tx, ty)); time.sleep(0.12)
            for _ in range(max(1, steps)):
                api.use_tool(tool)
                time.sleep(0.15)
            hit.append((tx, ty))
        parts = [f"⛏️ 敲完（{tool} ×{steps}）"]
        parts.append("命中: " + (" ".join(f"{a},{b}" for a, b in hit) if hit else "无"))
        if chest_skip:
            parts.append("🚫 箱子/容器格跳过(不砸): " + " ".join(f"{a},{b}" for a, b in chest_skip))
        if stood_fail:
            parts.append("⚠️ 找不到可站格: " + " ".join(f"{a},{b}" for a, b in stood_fail))
        return _with_state("\n".join(parts))
    except Exception as e:
        return _with_state(f"❌ 敲击出错: {e}")


@mcp.tool()
def interact_at(tile_x: int, tile_y: int) -> str:
    """🎯 与指定瓦片交互（对角也行，不用贴脸）
    与 NPC 对话 / 开机器 / 开箱子 / 触发机关 / 点家具（TV·日历·壁炉·目录都行）。
    SDV 1.6 家具交互已绕过右键标志直接 checkForAction，远程就能点 TV/日历。
    拿家具要用 furniture_pickup，不是这个。

    Args:
        tile_x: 目标瓦片 X 坐标
        tile_y: 目标瓦片 Y 坐标
    """
    try:
        _ensure_background()  # 开商店/锻造台等菜单前先确保不冻结
        r = api.interact_at(tile_x, tile_y)
        if r.get("ok") and r.get("actionTriggered"):
            _mark_festival_poi_tile(tile_x, tile_y)   # 命中节日 POI 瓦片 → 记入交互历史
            what = r.get("furniture") or r.get("object") or "目标"
            return _with_state(f"🎯 与 {what} 交互成功")
        return _with_state(f"⚠️ 该位置没有可交互的东西（actionTriggered=false）")
    except Exception as e:
        return _with_state(f"❌ 交互失败: {e}")


# ── 配饰描述索引 ──
# 配饰索引（已通过实测校正）
ACC_NAMES = {
    0: "无配饰", 2: "络腮胡", 3: "一字胡（鲁迅）", 4: "八字胡",
    5: "短胡子", 6: "短络腮胡", 7: "短络腮胡",
    8: "金耳环", 9: "银耳环", 10: "老花镜（圆）",
    11: "小红鼻子", 12: "AR眼镜", 13: "粗灰眉毛",
    14: "红色领巾", 15: "蓝色墨镜（上圆下方）",
    16: "蓝色领巾", 17: "墨镜", 18: "鸭子嘴",
    19: "灰色反光墨镜", 20: "短胡子",
    21: "胡子", 22: "胡子", 23: "胡子",
    24: "阴沉脸", 25: "小丑鼻子", 26: "扁眼镜",
    27: "黑色粗眉毛", 28: "小腮红",
    29: "张着嘴", 30: "黑眼圈",
}


# ── 发型名称索引 ──
# ⚠️ 2026-08-16 恒实测：这些是【显示编号】（捏人页/内部0=显示1号）。
#   set_appearance(hair=N) 会自动转内部 ID（N-1）传给 changeHairStyle。
HAIR_NAMES = {
    1: "Side-Swept Bangs", 2: "Messy Center Part", 3: "Side-Parted Swoop Bangs",
    4: "Full Afro", 5: "Short Spiky Fluff", 6: "Center-Stripe Mohawk",
    7: "Long Straight Hair", 8: "Short Swept-Back Cut", 9: "Wild Spikes (Leo)",
    10: "Puffy Mushroom Bob", 11: "Swept-Up Spikes", 12: "Short Rounded Cut",
    13: "Low Ponytail", 14: "Shaggy Spikes", 15: "Fluffy Flip",
    16: "Middle-Part Bowl Cut", 17: "High Ponytail", 18: "Twin Side Braids",
    19: "Asymmetrical Fluffy Sweep", 20: "Triple High Buns",
    21: "Flat Heavy Fringe", 24: "Short Jagged Spikes",
    43: "Upright Spiky Crew", 44: "Fluffy Side-Swept Fringe",
    45: "Tidy Boyish Fringe", 46: "Elegant Side-Swept Bangs",
    47: "Puffy Ice Cream Scoop", 48: "Long Middle-Part Fringe",
    49: "Relaxed Flowy Long Cut", 50: "Classic True Mohawk",
    51: "Balding Crown Long Sides", 52: "Receding Hairline",
    53: "Clean Shaven Bald", 54: "Buzz Widow's Peak",
    55: "Messy Tousled Buzz", 56: "Standard Buzz Cut",
    65: "Short Layered Crop", 68: "Tousled Bird's Nest",
    69: "Extreme Fluffy Spikes", 70: "Shaggy Mid-Length Sweep",
    73: "Heavy Fringe Shag", 74: "Fluffy Parted Shag",
}


@mcp.tool()
def list_hair_ref() -> str:
    """💇 发型编号参考 (ID 0~73)
    列出部分已知名称的发型。
    0 ~ 73 共 74 种，未知名称的只有编号。
    女头居多没有标注，建议直接试编号看效果。
    """
    lines = ["💇 发型编号参考:\n"]
    for hid in range(0, 74):
        name = HAIR_NAMES.get(hid, "")
        if name:
            lines.append(f"  {hid:2d}: {name}")
        else:
            lines.append(f"  {hid:2d}: (未命名)")
    return _with_state("\n".join(lines))
COLOR_PRESETS = {
    "金色": "FFE6A0",
    "棕色": "825028",
    "深棕": "4A2810",
    "黑色": "1E1E1E",
    "灰色": "808080",
    "银色": "C0C0C0",
    "白色": "FFFFFF",
    "红色": "FF0000",
    "酒红": "8B0040",
    "橙色": "FF6600",
    "黄色": "FFD700",
    "绿色": "00AA00",
    "翠绿": "008844",
    "青色": "00AAAA",
    "蓝色": "0044FF",
    "深蓝": "002288",
    "天蓝": "66BBFF",
    "紫色": "8800CC",
    "淡紫": "CC88FF",
    "粉色": "FF88AA",
    "品红": "FF00AA",
}


def _portrait_screenshot_image(delay=0.5):
    """🪞 取捏人弹窗的小人展示区截图，返回 mcp Image；无弹窗/失败→None（调用方回退成纯文本，不阻断）。
    set_appearance 改的是 Game1.player 字段，doll 下一帧才画出来 → 先 sleep <delay> 再截。
    区域由游戏自己按 uiViewport 居中算 → 分辨率自适应。"""
    time.sleep(delay)
    try:
        r = api.screenshot_portrait_ai()
    except Exception:
        return None
    if not r.get("ok"):
        return None
    try:
        data = base64.b64decode(r["image"])
        return Image(data=data, format="png")
    except Exception:
        return None


@mcp.tool()
def set_appearance(
    hair: Optional[int] = None,
    hair_color: Optional[str] = None,
    skin: Optional[int] = None,
    shirt: Optional[int] = None,
    pants: Optional[int] = None,
    hat: Optional[int] = None,
    acc: Optional[int] = None,
    eye_color: Optional[str] = None,
    pants_color: Optional[str] = None,
) -> Any:
    """💇 捏脸 — 修改角色外观
    运行时热改角色外观，无需退出游戏。
    所有参数都是可选的，只改你提供的字段。

    颜色参数支持 hex ("FF6600") 或预设名称 ("橙色")。
    用 list_color_presets() 查看所有预设。

    ⚠️ 只能在"捏脸菜单开着"时用（创建角色 or 幻觉神龛解锁后都开 CharacterCustomization）。确认(ok)后基相外观定型，想再改 → 解锁幻觉神龛再开捏脸页。**菜单外调用会报错**（防直调写覆盖值致之后换衣被覆盖，游戏错乱）。改前先 confirm_look 核对+让host参谋+screenshot 满意。
    🪞 每次修改成功后本工具会额外附一张小人展示区截图（捏脸菜单开着才有；菜单外调用会被拦，只回错误）。

    上衣编号: 1000~1999（共301件，用 list_shirt_ref() 查中文名+描述；q=关键词 或 start/end 筛选）
    裤子编号: 0~999（共18条，用 list_pants_ref() 查中文名+描述）
    帽子编号: 0~93（用 list_hats_ref() 查看列表）
    发型: 1~74 显示编号（内部 0~73，内部 = 显示-1；2026-08-16 恒实测：内部0=显示1号）

    Args:
        hair: 发型显示编号 1~74（HAIR_NAMES/list_hair_ref 同编号）
        hair_color: 发色 hex 或预设名
        skin: 肤色编号 0~23
        shirt: 上衣编号 1000~1999（list_shirt_ref 查名/描述）
        pants: 裤子编号 0~999（list_pants_ref 查名/描述）
        hat: 帽子编号 (H)0~93
        acc: 配饰编号 0~30
        eye_color: 瞳色 hex 或预设名
        pants_color: 裤子颜色 hex 或预设名
    """
    # 🔒 2026-08-31 恒：不退役 set_appearance（幻觉神龛解锁后仍可改）；硬门禁在 C# /appearance——只在捏脸菜单开着时允许，
    #   菜单外调用会被 C# 拦（防直调写覆盖值→之后换衣被覆盖）。这里不留退役锁，直接调 /appearance，菜单未开会收到明确报错。
    try:
        kwargs = {}

        # Resolve color presets to hex
        def resolve_color(c):
            if c and c.lower() in {k.lower() for k in COLOR_PRESETS}:
                for k, v in COLOR_PRESETS.items():
                    if k.lower() == c.lower():
                        return v
            return c

        # Resolve accessory by name (e.g. "络腮胡" → 2)
        def resolve_acc(a):
            if isinstance(a, int) or (isinstance(a, str) and a.isdigit()):
                return int(a)
            if isinstance(a, str):
                for kid, kname in ACC_NAMES.items():
                    if kname and a in kname:
                        return kid
            return a

        if hair is not None:
            # ⚠️ 2026-08-16 恒实测：HAIR_NAMES/捏人页显示编号 = 内部 farmer.hair + 1
            #   （内部0=显示1号，内部12=低马尾[显示13]，内部13=男头[显示14]）
            #   set_appearance 收到的 hair 是显示编号 → 减1 转内部 ID 再传给 /appearance（changeHairStyle）
            kwargs["hair"] = int(hair) - 1
        if hair_color is not None: kwargs["hairColor"] = resolve_color(hair_color)
        if skin is not None: kwargs["skin"] = skin
        if shirt is not None: kwargs["shirt"] = shirt
        if pants is not None: kwargs["pants"] = pants
        if hat is not None: kwargs["hat"] = hat
        if acc is not None: kwargs["acc"] = resolve_acc(acc)
        if eye_color is not None: kwargs["eyeColor"] = resolve_color(eye_color)
        if pants_color is not None: kwargs["pantsColor"] = resolve_color(pants_color)

        if not kwargs:
            presets = ", ".join(COLOR_PRESETS.keys())
            return _with_state(
                "⚠️ 没提供任何参数。可用颜色预设:\n" +
                presets + "\n\n"
                "例: set_appearance(hair=16, hair_color=\"紫色\", shirt=1000, pants=0, hat=1)"
            )

        r = api.set_appearance(**kwargs)
        if r.get("ok"):
            changed = r.get("changed", [])
            # 每次修改后附一张小人展示区截图，让 AI 看自己改成了什么样（2026-08-31 恒）。
            msg = _with_state(f"✅ 已修改: {', '.join(changed)}")
            img = _portrait_screenshot_image()
            if img is not None:
                return [msg + "\n🪞 下方是改完后的样子（小人展示区截图）：", img]
            return msg + "\n（捏人弹窗未开，无法截小人展示区；可先 /menu 确认弹窗状态）"
        else:
            return _with_state(f"❌ 修改失败: {r.get('error', '未知')}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def character_customize(name: Optional[str] = None, farmname: Optional[str] = None,
                        favorite: Optional[str] = None) -> str:
    """🎭 处理捏人弹窗（CharacterCustomization）
    新 farmhand 加入时游戏弹"起名/喜欢的东西/确认形象"窗，AI 用这个填名字和喜欢的东西，
    配合 set_appearance 设形象，然后 menu_click(button='ok') 确认创建角色。
    ⚠️ 不传参数=只读当前弹窗状态（各字段值 + canLeaveMenu + 还缺什么）。

    Args:
        name: 角色名（不传=不改）
        farmname: 🏡 不传——AI 后加入的是已有农场，自动继承房主农场名（传了也改不掉房主那份）
        favorite: 喜欢的东西（canLeaveMenu 强制要求非空）。**传真实中文文本**（如 "葡萄"），API 已 UTF-8 处理；
                即使传了 \\uXXXX 转义字面量也会被正确解码。确认用 appearance_info 的 favoriteThing。
    """
    # 🔒 硬锁（2026-08-22 恒：起名/喜好=创建时填一次，ok 后退役=不可再改）
    if "character_customize" in _retired_tools:
        return _with_state("🔒 捏人(起名/喜好)已退役：角色已确认。如需改 → settings reactivate(character_customize) 召回。")
    body = {}
    if name is not None: body["name"] = name
    if farmname is not None: body["farmname"] = farmname
    if favorite is not None: body["favorite"] = favorite
    try:
        r = api._post("/character_customize", body)
        if r.get("ok"):
            return _with_state(
                f"🎭 捏人弹窗: 名字={r.get('name')!r} 农场={r.get('farmname')!r} 喜好={r.get('favorite')!r}\n"
                f"   {r.get('hint', '')}"
            )
        return _with_state(f"❌ {r.get('error', r)}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def confirm_look() -> str:
    """🔍 确认捏人形象（ok 前必做，保险；2026-08-22 恒）
    读当前捏人窗完整形象，逐项核对（尤其发型/瞳色/配饰），满意再提示提交。
    ⚠️ menu_click(button='ok') 确认后 → 基相外观定型（想再改要解锁幻觉神龛）；捏脸工具不退役，但只在捏脸菜单开着时可用。
    流程：①请恒帮忙参谋这个形象 → ②screenshot 截图自己看满意没 → ③满意后调本工具核对 → ok。
    """
    global _look_verified
    try:
        d = api._ai_get("/appearance_info")
    except Exception:
        return _with_state("❌ 读不到当前外观（确认游戏/世界已就绪）")
    hair = (d.get("hair") or {}).get("index", "?")
    eye = (d.get("newEyeColor") or {}).get("hex", "?")
    skin = (d.get("skin") or {}).get("index", "?")
    acc = (d.get("accessory") or {}).get("index", "?")
    shirt = (d.get("shirt") or {}).get("name", "?")
    pants = (d.get("pants") or {}).get("name", "?")
    _look_verified = True
    return _with_state(
        "🔍 捏人形象核对（满意再 ok）：\n"
        f"  🧑 名字: {d.get('name')!r} | 喜爱: {d.get('favoriteThing')!r}\n"
        f"  💇 发型: {hair} | 瞳色: {eye} | 肤色: {skin} | 配饰: {acc}\n"
        f"  👕 上衣: {shirt} | 裤子: {pants}\n"
        "⚠️ 核对重点：发型/瞳色/配饰最容易出错。\n"
        f"流程：①请{_host_name()}帮忙参谋这个形象 → ②screenshot 截图自己确认满意 → ③满意后再 menu_click(button='ok')。\n"
        "✅ 已标记核对。ok 后基相外观定型（想再改要解锁幻觉神龛）；捏脸工具不退役，只在捏脸菜单开着时可用。"
    )


@mcp.tool()
def color_pick(hue: Optional[float] = None, sat: Optional[float] = None, val: Optional[float] = None,
               hex: Optional[str] = None) -> str:
    """🎨 颜色换算（HSV↔RGB，跟游戏颜色条 ColorPicker 完全一致）
    AI 精确调色/确认颜色用。游戏捏脸颜色条是 HSV 三滑块（色相/饱和/明度），
    **三根都是 0-100 整数**（点击切成 ~100 份），选完转成 RGB 存进角色字段。
    确认当前颜色用 appearance_info（返回真 hex）。

    Args:
        hue: 色相滑块 0-100（0=红，每档 3.6°）
        sat: 饱和度滑块 0-100
        val: 明度滑块 0-100
        hex: 已有颜色 "RRGGBB"（给 hex 时返回它的 HSV + 最近滑块档，方便口头说"明亮点/暗点"）
    """
    body = {}
    if hue is not None: body["hue"] = hue
    if sat is not None: body["sat"] = sat
    if val is not None: body["val"] = val
    if hex is not None: body["hex"] = hex
    try:
        r = api._post("/color_pick", body)
        if r.get("ok"):
            if r.get("mode") == "hsv":
                rgb = r.get("rgb")
                hexv = r.get("hex")
                sl = r.get("slider") or {}
                hd = r.get("hueDeg")
                return _with_state(
                    f"🎨 滑块(色相{sl.get('hue')}, 饱和{sl.get('sat')}, 明度{sl.get('val')}) → 色相角{hd}°\n"
                    f"   RGB{rgb} = #{hexv} → set_appearance(..., *_color=\"#{hexv}\")"
                )
            rgb = r.get("rgb")
            hsv = r.get("hsv") or {}
            sl = hsv.get("slider") or {}
            return _with_state(
                f"🎨 #{hex} = RGB{rgb} = HSV(色相{hsv.get('hueDeg')}°, 饱和{hsv.get('sat')}%, 明度{hsv.get('val')}%)\n"
                f"   最近滑块档: 色相{sl.get('hue')} / 饱和{sl.get('sat')} / 明度{sl.get('val')}"
            )
        return _with_state(f"❌ {r.get('error', r)}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def list_color_presets() -> str:
    """🎨 查看所有颜色预设
    返回可用颜色名称与对应 hex 值。
    在 set_appearance 的颜色参数中直接传名称即可。
    """
    lines = ["🎨 颜色预设表:\n"]
    for name, hex_val in COLOR_PRESETS.items():
        lines.append(f"  ■ {name}: #{hex_val}")
    lines.append("\n也可以直接传自定义 hex 值如 \"FF6600\"")
    return _with_state("\n".join(lines))


def _load_appearance_overrides() -> dict:
    """加载 scripts/appearance_overrides.json 的"自定义描写"：{str(id): {name?, desc?}}。缺文件/坏JSON→{}。"""
    path = os.path.join(SCRIPT_DIR, "appearance_overrides.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f) or {}
        return {k: v for k, v in data.items() if k != "_doc"}
    except (OSError, ValueError):
        return {}


def _merged_shirt_rows() -> list:
    """ref_data.SHIRT_REF 运行时叠加自定义描写：返回 (id, 名, 描述, 是否你补录的描写) 四元组。"""
    ovr = _load_appearance_overrides()
    out = []
    for _id, nm, desc in ref_data.SHIRT_REF:
        o = ovr.get(str(_id))
        if o:
            if o.get("name"):
                nm = o["name"]
            if o.get("desc"):
                desc = o["desc"]
        custom = bool(o and (o.get("name") or o.get("desc")))
        out.append((_id, nm, desc, custom))
    return out


@mcp.tool()
def list_shirt_ref(q: str = "", start: int = 0, end: int = 0) -> str:
    """👕 上衣编号参考 (ID 1000~1999，共301件，中文名)
    捏脸选上衣用 set_appearance(shirt=N)。默认出紧凑索引(编号+名)；想看某件长什么样 →
    q=关键词 或 start/end=数字区间 → 出该批「编号+中文名+游戏描述」。
    ※ = 你补录的自定义描写(默认上衣那批原本都叫「上衣/可以穿的上衣」。)
    """
    rows = _merged_shirt_rows()
    if not rows:
        return _with_state("❌ 无上衣数据")
    lo, hi = rows[0][0], rows[-1][0]
    any_custom = any(r[3] for r in rows)

    if q:
        ql = q.strip().lower()
        filtered = [r for r in rows if ql in r[1].lower() or ql in r[2].lower() or ql in str(r[0])]
        mode = "keyword"
    elif start or end:
        s, e = start or lo, end or hi
        filtered = [r for r in rows if s <= r[0] <= e]
        mode = "range"
    else:
        filtered = None
        mode = "index"

    if filtered is not None and not filtered:
        return _with_state(f"❌ 没找到「{q or f'{start or lo}~{end or hi}'}」的上衣（{lo}~{hi} 共 {len(rows)} 件）")

    if mode == "index":
        lines = [f"👕 上衣编号索引 (ID {lo}~{hi}，共 {len(rows)} 件):\n"]
        for sid, sname, sdesc, custom in rows:
            mark = "※" if custom else ""
            if custom:
                # 自定义款：名多为"上衣"，用描写首小节当可识别标签
                label = sdesc.split("。")[0][:14] or sname
                lines.append(f"  {mark}{sid}: {label}")
            else:
                lines.append(f"  {mark}{sid}: {sname}")
        lines.append("\n※ = 你补录的自定义描写（想看齐全 → q=关键词 或 start/end）。")
        return _with_state("\n".join(lines))

    lines = [f"👕 上衣详查 {len(filtered)} 件:\n"]
    for sid, sname, sdesc, custom in filtered:
        mark = "※" if custom else ""
        lines.append(f"  {mark}{sid}: {sname} — {sdesc}")
    lines.append("\n看上哪件 → set_appearance(shirt=<id>)；再搜 → 上衣 q=关键词。")
    return _with_state("\n".join(lines))


def list_pants_ref(q: str = "", start: int = 0, end: int = 0) -> str:
    """👖 裤子编号参考 (共18条，中文名)
    捏脸选裤子用 set_appearance(pants=N)。默认全量(少，直接带描述)；q/start/end 可再筛。
    """
    rows = ref_data.PANTS_REF
    filtered = [r for r in rows
                if (q.strip().lower() in r[1].lower() or q.strip().lower() in str(r[0]))] if q else None
    if filtered is None and (start or end):
        s, e = start or 0, end or 999
        filtered = [r for r in rows if s <= r[0] <= e]
    if filtered is None:
        filtered = rows
    if not filtered:
        return _with_state(f"❌ 没找到「{q}」的裤子")
    lines = [f"👖 裤子参考 {len(filtered)} 条:\n"]
    for sid, sname, sdesc in filtered:
        lines.append(f"  {sid}: {sname} — {sdesc}")
    lines.append("\n选中 → set_appearance(pants=<id>)。")
    return _with_state("\n".join(lines))


HATS_REF = [
    (0, "Cowboy Hat"), (1, "Bowler Hat"), (2, "Top Hat"),
    (3, "Sombrero"), (4, "Straw Hat"), (5, "Official Cap"),
    (6, "Blue Bonnet"), (7, "Plum Chapeau"), (8, "Skeleton Mask"),
    (9, "Goblin Mask"), (10, "Chicken Mask"), (11, "Earmuffs"),
    (12, "Delicate Bow"), (13, "Tropiclip"), (14, "Butterfly Bow"),
    (15, "Hunter's Cap"), (16, "Trucker Hat"), (17, "Sailor's Cap"),
    (18, "Good Ol' Cap"), (19, "Fedora"), (20, "Cool Cap"),
    (21, "Lucky Bow"), (22, "Polka Bow"), (23, "Gnome's Cap"),
    (24, "Eye Patch"), (25, "Santa Hat"), (26, "Tiara"),
    (27, "Hard Hat"), (28, "Sou'wester"), (29, "Daisy"),
    (30, "Watermelon Band"), (31, "Mouse Ears"), (32, "Cat Ears"),
    (33, "Cowgal Hat"), (34, "Cowpoke Hat"), (35, "Archer's Cap"),
    (36, "Panda Hat"), (37, "Blue Cowboy Hat"), (38, "Red Cowboy Hat"),
    (39, "Cone Hat"), (40, "Living Hat"), (41, "Emily's Magic Hat"),
    (42, "Mushroom Cap"), (43, "Dinosaur Hat"), (44, "Totem Mask"),
    (45, "Logo Cap"), (46, "Wearable Dwarf Helm"), (47, "Fashion Hat"),
    (48, "Pumpkin Mask"), (49, "Hair Bone"), (50, "Knight's Helmet"),
    (51, "Squire's Helmet"), (52, "Spotted Headscarf"), (53, "Beanie"),
    (54, "Floppy Beanie"), (55, "Fishing Hat"), (56, "Blobfish Mask"),
    (57, "Party Hat (red)"), (58, "Party Hat (blue)"), (59, "Party Hat (green)"),
    (60, "Arcane Hat"), (61, "Chef Hat"), (62, "Pirate Hat"),
    (63, "Flat Topped Hat"), (64, "Elegant Turban"), (65, "White Turban"),
    (66, "Garbage Hat"), (67, "Golden Mask"), (68, "Propeller Hat"),
    (69, "Bridal Veil"), (70, "Witch Hat"), (71, "Copper Pan"),
    (72, "Green Turban"), (73, "Magic Cowboy Hat"), (74, "Magic Turban"),
    (75, "Golden Helmet"), (76, "Deluxe Pirate Hat"), (77, "Pink Bow"),
    (78, "Frog Hat"), (79, "Small Cap"), (80, "Bluebird Mask"),
    (81, "Deluxe Cowboy Hat"), (82, "Mr. Qi's Hat"), (83, "Dark Cowboy Hat"),
    (84, "Radioactive Goggles"), (85, "Swashbuckler Hat"), (86, "Qi Mask"),
    (87, "Star Helmet"), (88, "Sunglasses"), (89, "Goggles"),
    (90, "Forager's Hat"), (91, "Tiger Hat"), (92, "???"),
    (93, "Warrior Helmet"),
]


@mcp.tool()
def list_hats_ref() -> str:
    """🎩 帽子编号参考 (ID 0~93)
    列出所有帽子的名称与对应编号，方便在 set_appearance 选用。
    """
    lines = ["🎩 帽子编号参考:\n"]
    for hid, hname in HATS_REF:
        lines.append(f"  {hid}: {hname}")
    return _with_state("\n".join(lines))


# ⚙️ 系统/设置域 ops 分发（2026-08-22 恒：把"捏脸设置域"并入"常规设置域"成一个设置域，不再拆分）。
#   放在外观函数之后（引用 set_appearance/character_customize/color_pick/list_*_ref）；
#   settings 工具在函数体内的 _SETTINGS_DISPATCH 引用是运行期前向引用（模块已加载完），安全。
_SETTINGS_DISPATCH = {
    "status": settings_status, "看": settings_status, "状态": settings_status, "设置状态": settings_status,
    "retire": settings_retire, "退役": settings_retire,
    "reactivate": settings_reactivate, "召回": settings_reactivate,
    "appearance": set_appearance, "外观": set_appearance, "捏脸": set_appearance, "改外观": set_appearance,
    "customize": character_customize, "起名": character_customize, "捏人": character_customize,
    "color": color_pick, "颜色": color_pick,
    "hair": list_hair_ref, "发型": list_hair_ref,
    "shirt": list_shirt_ref, "上衣": list_shirt_ref,
    "pants": list_pants_ref, "裤子": list_pants_ref,
    "hat": list_hats_ref, "帽子": list_hats_ref,
    "colorpreset": list_color_presets, "调色": list_color_presets,
    "confirm_look": confirm_look, "核对": confirm_look, "确认捏脸": confirm_look, "确认形象": confirm_look,
}


# 📖 详细域指引（2026-08-22：docstring 精简后，深度/坑靠 help 查，不丢细节）
_DOMAIN_GUIDES = {
"check": "查询域，what=...：status(完整状态) backpack(逐格价值/星级) worn(穿戴) machines(全场机器清单) mine(下矿进度) silo(干草) mastery(精通) buildings(木匠建筑) quest(任务) chests(当前图箱) storage(箱子网络) look(环视周围)。⚠️查概览用 status，查逐格用 backpack，别都调浪费 token。",
"farm": "农活域(🌱必走，禁手动 use_tool/tool_area 组合)：till(蓄力锄) plant(种,跳过已种) water(浇,自动跳雨+水壶没水先装满) harvest(收) scythe(镰刀收蒜/花/茶) fertilize(化肥) clear(清杂草石树桩) plot(连通域规划) till_plant(锄+种一条龙) tillfield(蓄力锄矩) hoe(布局锄) plantlayout(按布局种) chop(砍树) clearground(清单格) collect(收机器) load(放原料) building(一屋收放) break(拆/敲同scene,镐子敲可破物/翻已耕地) place(放置/播种同scene) pond/pond_add/pond_feed/pond_collect/pond_fish(鱼塘)。⚠️漏格DLL自动补；高级工具蓄力用 tool_area(别用/tool)。只在 Farm/温室/姜岛。带参 op(plant 的 seed_name、till 的 x/y/rows、place 的 name)→ kw={'参数名':值}。🐄动物(2026-09-02 care域并入farm): animals(摸+收) 喂水/碗(宠物水) milk(挤奶剪毛) buy(买动物,豁免建议) doors(关门) hay(干草) pet(猫狗) petwalk(拟人摸) 畜舍/这间(这间屋动物) statue(祈福)——⚠️farm water=浇地,动物水用 喂水; farm building=机器收放,这屋动物用 畜舍。💡大田洒水器布局(可选,纯自动化建议,可用可不用)：要按洒水器留格/留走道(种2留1,AI能进田浇收)就 plan(方形规划算格)→hoe(布局锄)→plantlayout(按布局种)三件套；只管种直接 till+plant 也成。",
"mine": "下矿域(⚒️ 矿井/头骨/火山)：go(冲层/刷矿) progress(进度) bomb_status/plan/place/collect/ladder/retreat(单步炸) bomb_mine(自动) bomb_volcano(火山) organize(整理背包)。⚠️无镐/血低硬拦；梯子 /ladder+confirm。⚠️bomb_mine 没炸弹+host在同矿井→自动转【内部】协同(跟随host+帮忙敲矿/打怪)不撤退出矿(bomb_escort 不对外暴露、AI 不主动启用)；bomb_retreat 结束协同+停脚本+脱离矿井回门口。⚠️接「深处的危险」重置电梯→起始层动态从1起(内置脚本自动读，不暴露工具)；刷矿目标层不可直达会上报，需先冲层带回或改浅层。💡出发前占位物(恒2026-08-23)：提前放1个可堆叠物(铱矿/铱锭/五彩碎片)在包，满包时同种战利品自动堆叠吸附、少触发满包停；别拿银河之魂这类带死亡会丢的稀有物当占位。",
"cabin": "小屋引导域(🏠 FarmHouse/Cabin/岛屋；不传=扫屋)：enum(扫**本屋**查待收) collect(收机器) statue(雕像) furniture(扫家具) interact(点家具) pickup(拿起家具) sleep(睡觉)。",
"social": "社交域：chat(跟NPC搭话) gift(送礼提好感) give(送玩家物品) send(发消息) emote(表情) friendship(查好感) movie(影院知识) snack(零食)。",
"scene": "场景交互域(点东西/工具/转身/捡)：at(x,y)(点指定格/柜台) interact(点面前) use(挥工具) face(转向0上1右2下3左) select(拿手上) pickup(拿起家具) pickup_scene(捡当前场景物) berry(摇浆果) spot(挖蚯蚓点) moss(绿雨搜苔藓) rock(室外镐击:敲当前图可破物,采石场/挖掘场/蚌矿场跳普通石,dig/dry,battle-free) garbage(翻垃圾桶) forge_help(锻造攻略) drop(丢物) furniture(扫家具) place(放置/播种:name=物品名,x/y=目标格→箱子/树种/蟹笼落地或种下,只放可放置物) break(拆/敲:x,y=目标格,steps=挥击次,radius=方圆→镐子敲石头/翻已耕地,跳过箱子/容器格) maze(迷宫视图r半径,gx/gy目标格→ASCII棋盘#墙.可走P自己G目标) maze_seg(走法链gx,gy目标→拆直走廊列表+拼「左/右上/下走到(x,y)」多段链,AI按段walk_to) maze_walk(走迷宫 waypoints=「x,y x,y…」依次walk_to) pan(淘金/淘盘:本图水下闪光点→岸边走位面水→铜锅淘金收掉落) front/rummage(分别是interact/garbage的别名)。带参 op(at/break 的 x,y、place 的 name、maze_seg 的 gx/gy)→ kw={'参数名':值}。",
"menu": "菜单/界面域(开→看→点)：read(看菜单) advance(推进剧情/对话) click(option/item/button/xy 点;action=claim领/action=discard丢桶腾格;slot=序号领指定格) key(ok/esc/数字按键) cancel(关弹窗/撤就绪) shop(逛店) sell(卖商店) bin(投出货箱) cook(做饭) craft(合成) recipes(菜谱) craftables(配方) forge(锻造) geode/geodes(砸晶球) customize(捏人) bundle(献祭板) bundle_kb(献祭知识库) donate(捐赠博物馆) read_book(读消耗品:书/秘密纸条/日记残页,统一走右键读 name=物品名) levelup_choose(技能升级职业选择 5/10级:不带参读左右选项,side=left/right 或 profession=职业id 定分支;普通升级自会确认OK) number(数量输入:展览会兑换台/转盘押注 NumberSelectionMenu) minigame(赌场小游戏点按钮 action=hit/stand/bet10/…) minigame_state(读牌面/转盘) display_fill(农展台放满 items='钻石,山羊奶酪') display_takeback(收好) journal(开任务日志→menu read 读卡,翻页=click(button=forward/back),领奖励=click(button=rewardBox)) know(查特别订单详情/知识库SPECIAL_ORDERS,如menu know 岛屿食材;2026-09-02 task域退役并入menu)。🚫满包接鱼/领箱:原 claim_swap(替换领取)已退役→**click action=discard 丢桶腾格(回收返金)+action=claim 领取(或用 slot 领指定格;不想要直接 button=ok 关掉)**。🧾关闭菜单一律 click(button=upperRightCloseButton)（ItemGrabMenu/交付容器用 button=ok 确认才关）；订单交付容器(QuestContainerMenu)=点背包对应物品格(见slots的坐标)→放进→点 button=ok 结算；任务日志领钱=点击已完成的有钱任务卡后 click(button=rewardBox)；兑奖机兑换=click(button=mainButton)；特别订单领奖链=日志领钱(上面)→社区板旁领奖箱(60,93)拿兑奖券→刘易斯家兑奖机(mainButton)兑换。",
"storage": "箱子域：view(看箱,box=N看单箱全清单) store(存:what/items限定存哪些,名可带xN数量只存那N份,留空=归位只存已有同类堆,target指定箱/all=True全存腾空间) take(取:x,y+name单箱 或 items批量) find(模糊查哪箱有某物) default(设/清默认箱 clear=清) tag(改名,可带color改色)。🤖存取统一走位：store/take都会先走到相关箱旁(批量只走到第一个),不区分拟人/原子,别靠编号逐箱翻。⭐每个箱子前自动带【类目标签】(内容过半归类):矿/古物/鱼/种子/作物/农产/建材/料理/装备——AI按标签定位箱,找东西用find。⚠️改色别染纯#000000(=默认木纹,识别成未染色);要黑箱用暗灰#303030。",
"daily": "过日子域：sleep(睡觉) settle(确认过夜结算) eat(吃食物回血体力) wear(穿/脱衣物) lie_bed(躺床不过夜) heartbeat(心跳间隔) pause(后台不暂停) peek(看恒干嘛) whiteboard/wb_read/wb_pin/wb_clear(白板记忆) appearance(捏脸)。",
"map": "导航域(🗺️跨图唯一入口)：lookup(查地点功能+出口) query(功能反查) go(走到目标/多段寻路+交通) walk(走到POI) movetile(同图精确走位) npc(找NPC) warp_safe(紧急逃脱)。⚠️出口走出口前一格；交通图腾柱>矿车>走路。⚠️参数全放kw对象(别拼进ops串)：go kw={destination:地点名/POI} walk kw={poi_name:POI} movetile kw={x:int,y:int}。",
"festival": "节日域(🎪)：today(今天节日) next(下一个) go(去) info(实况) interact(互动) answer(应答) shop(节日商店) eggs(找蛋) egg_note(纸条) egg_run(捡蛋) dance(跳舞邀请) strength(力量测试 delay=毫秒) ice_fish(冰雪节冰钓自动化) help(玩法) prep(备战) poi(限定点) maze(迷宫坐标奇偶年) maze_walk(走迷宫 waypoints=「x,y x,y…」依次walk_to)。",
"fish": "钓鱼域(🎣 2026-08-22修复)：go(去钓 location=) info(查某地鱼) spots(钓点) bobber(浮漂样式) rod(鱼竿:看/上饵钓具 item=名) crab(蟹笼总览) crab_water(找水) crab_place(放笼) crab_bait(放饵) crab_collect(收笼) crab_diag(诊断笼/定位挂饵) crab_retract(回收笼/清搁浅 location=可选)。⚠️鱼塘在 farm 域不在 fish。带参 op(go 的 location、rod 的 item、crab 的 count)→ kw={'参数名':值}。",
"settings": "系统/设置域(⚙️ 合并捏脸进来)：status(看所有设置+退役工具) retire(退役工具) reactivate(召回) appearance(捏脸) customize(捏人) **confirm_look(核对捏人形象,ok前必做)** color(颜色条) hair/shirt/pants/hat/colorpreset(外观参考)。⚠️捏脸=创建定型:ok后set_appearance/捏人自动退役(不可逆);旧配置 settings(setting='async', value='on') 仍可。",
"session": "会话域(🧠 上下文缓冲，多数情况不用)：status(看缓冲条数/设置) set(改设置 setting,value) export(手动导出记忆)。",
"scripts": "脚本/异步域(🚀被动异步优先)：status(查进度,job_id空=看全部+最近) stop(停任务,job_id空=停最近在跑) async(自动异步白名单 show/add/remove/enable=on|off) run(短任务同步 name=脚本名,args=参数) start(主动后台兜底 name,args→job_id)。常用脚本: farm_row(耕) water_crops(浇) harvest(收) chop_trees(砍) clear_area(清杂) pet_animals(摸动物) shop_buy(购物)。⚠️长任务(bomb_mine/炸矿/钓鱼)便利工具**自动后台**，别手动start(白名单async enable=on即可)；跑脚本时别用走动/挥工具同步工具，但聊天/看状态/开背包/整理背包没问题；一次只跑一个脚本。⚠️参数放kw别拼ops(如 script(ops=\"start\", kw={name,args})；script(ops=\"status\", kw={job_id}))。",
}


# 🧭 help 主题别名：中文/口语词 → 域名（精确匹配前的快捷映射，2026-09-02 消"首命中波动"）
_HELP_ALIAS = {
    "钓鱼": "fish", "钓": "fish", "鱼": "fish", "蟹笼": "fish", "鱼竿": "fish",
    "睡觉": "daily", "睡": "daily", "躺床": "daily", "吃": "daily", "吃食物": "daily", "穿着": "daily", "穿戴": "daily", "过夜": "daily",
    "菜单": "menu", "界面": "menu", "商店": "menu", "背包": "menu", "开日志": "menu", "菜单操作": "menu",
    "设置": "settings", "配置": "settings", "捏脸": "settings", "外观": "settings", "起名": "settings",
    "任务": "menu", "订单": "menu", "特别订单": "menu",
    "箱子": "storage", "存储": "storage", "仓库": "storage",
    "导航": "map", "地图": "map", "走路": "map", "寻路": "map", "走": "map", "到哪": "map",
    "节日": "festival", "节": "festival",
    "农场": "farm", "农活": "farm", "种地": "farm", "浇水": "farm", "耕地": "farm", "秧": "farm",
    "矿": "mine", "挖矿": "mine", "下矿": "mine", "矿井": "mine", "炸矿": "mine",
    "动物": "farm", "猫": "farm", "狗": "farm", "宠物": "farm", "挤奶": "farm", "喂": "farm", "摸动物": "farm",
    "社交": "social", "送礼": "social", "好感": "social", "聊天": "social",
    "场景": "scene", "交互": "scene", "采集": "scene", "捡": "scene", "点": "scene",
    "查询": "check", "状态": "check", "看情况": "check",
    "家": "cabin", "小屋": "cabin", "农舍": "cabin",
    "脚本": "scripts", "脚本域": "scripts", "会话": "session", "缓冲": "session",
}


@mcp.tool()
def help(topic: str = "") -> str:
    """📖 详细指引（docstring 精简不够用时查这不丢细节）
    例: help(farm) / help(钓鱼) / help(睡觉) → 该域所有 ops + 关键坑。
    不传 topic → 列出可查话题。

    Args:
        topic: 话题/域名（如 farm / 小贴士 / 睡觉）
    """
    if not topic:
        return "📖 可查话题: " + "、".join(_DOMAIN_GUIDES.keys())
    t = topic.strip()
    tl = t.lower()
    # 1) 精确：域名（help farm / help task 等）
    if tl in _DOMAIN_GUIDES:
        return _DOMAIN_GUIDES[tl]
    # 2) 别名：中文/口语词 → 域名（help 钓鱼 / help 睡觉 等）
    if tl in _HELP_ALIAS:
        return _DOMAIN_GUIDES[_HELP_ALIAS[tl]]
    # 3) 域名作为子串（英文多字，如 "farm stuff"）→ 取最长命中的域
    best = None; bl = 0
    for k in _DOMAIN_GUIDES:
        if k in tl and len(k) > bl:
            best, bl = k, len(k)
    if best:
        return _DOMAIN_GUIDES[best]
    # 4) 内容兜底（中文词 → 在指南文本里找），取"域名最长"命中，替代原首命中波动
    hits = [(k, len(k)) for k, v in _DOMAIN_GUIDES.items() if t in v]
    if hits:
        hits.sort(key=lambda x: x[1], reverse=True)
        return _DOMAIN_GUIDES[hits[0][0]]
    return f"❌ 没有「{t}」的指引。可查: " + "、".join(_DOMAIN_GUIDES.keys())


@mcp.tool()
def select_item(name: str) -> str:
    """🎯 选择背包中的物品或工具
    按名称从背包中选中物品，放在当前工具栏。

    Args:
        name: 物品名称（如 Pickaxe、Axe、Blueberry Seeds、Copper Bar 等）
    """
    try:
        api.select(name)
        return _with_state(f"✅ 已选择: {name}")
    except Exception as e:
        return _with_state(f"❌ 找不到「{name}」或选择失败: {e}")


@mcp.tool()
def eat_item(name: str = "", item_name: str = "") -> str:
    """🍽️ 自己吃背包里的食物（真实吃法：eatObject 动画 + 回血/回体力）
    不指定 name 就吃当前手上选中的食物；指定则先把食物选到手上再吃。
    会先确保后台不冻结（失焦暂停关），动画完整播完后回血真实生效。

    Args:
        name: 食物名称（如 Salad、Pineapple、Farmer's Lunch），留空吃当前选中
        item_name: name 的中文别名（AI 常传 item_name，2026-09-03 恒）
    """
    try:
        _ensure_background()  # 后台吃动画要完整播完(doneEating 结算回血)，先确保不冻结
        if not name:
            name = item_name
        if name:
            api.select(name)
            time.sleep(0.2)
        r = api._post("/eat")
        if r.get("ok"):
            return _with_state(f"🍽️ 吃了 {r.get('ate', name or '当前选中')}")
        # 2026-09-03 恒：AI 常先不选中食物就被拒——直接给路径，别只甩"先/select"
        return _with_state(f"❌ {r.get('error', '吃失败')}——先选中食物再吃: scene ops=select name={name or '<食物名>'} → daily ops=eat")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def face(direction: int) -> str:
    """👤 设置角色朝向

    Args:
        direction: 0=上, 1=右, 2=下, 3=左
    """
    direction = max(0, min(3, direction))
    dir_names = ["↑ 上", "→ 右", "↓ 下", "← 左"]
    try:
        api.face(direction)
        return _with_state(f"👤 朝向: {dir_names[direction]}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def press_key(key: str, count: int = 1, hold: int = 0) -> str:
    """⌨️ 模拟键盘按键

    常用键:
    - confirm — 确认/对话/推进
    - cancel — 取消/后退
    - w/a/s/d — WASD 移动（走到出口/传送瓦片时用 hold=400 长按触发）
    - F5 — 切换 Fishbot
    - F6 — 切换 AutoCombat
    - F7 — 切换 AutoWater
    - F8 — 切换 AutoPet

    Args:
        key: 键名
        count: 按几次（默认 1）
        hold: 长按毫秒（>0 时按住再松开，用于走到边缘/传送瓦片）
    """
    try:
        api.key(key, count, hold=hold)
        msg = f"⌨️ 按键: {key}×{count}" + (f" (按住{hold}ms)" if hold else "")
        if key.upper().startswith("F"):
            toggle_names = {
                "F5": "🎣 Fishbot", "F6": "⚔️ AutoCombat",
                "F7": "💧 AutoWater", "F8": "🐄 AutoPet",
            }
            msg += f" ({toggle_names.get(key.upper(), '')})"
        return _with_state(msg)
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def send_chat(message: str) -> str:
    """💬 发送消息到游戏聊天栏（恒窗口可见）
    ⚠️ 2026-08-16 恒实测：AI 进程(7843)广播同步不可靠，恒看不到——改走 host_chat（host 窗口必见）。

    Args:
        message: 要显示的消息文本
    """
    try:
        api.host_chat(message)
        # 🧠 会话缓冲：AI 发给恒的话归档（speaker=A2）
        _session_append("A2", message)
        return _with_state(f"💬 已发送: {message[:50]}{'…' if len(message) > 50 else ''}")
    except Exception as e:
        return _with_state(f"❌ 发送失败: {e}")


@mcp.tool()
def set_heartbeat_interval(minutes: int = 5) -> str:
    """⏱️ 设置玩家动态检测的心跳间隔
    每隔 N 分钟注入一行 👤 玩家动态。
    AI 可以通过这个知道你在干什么，决定要不要参与或调整自己的行动。

    Args:
        minutes: 间隔分钟数（0=每次工具返回都显示，默认 5 分钟）
    """
    result = player_activity.set_interval(minutes)
    return _with_state(result)


@mcp.tool()
def set_pause(out_of_focus: bool = False) -> str:
    """🖱️ 设置"失焦暂停"（后台运行模式）
    out_of_focus=False（默认）→ AI 窗口在后台也完整运行（走位/菜单/锻造/吃东西都行，
    不抢 user 的前台焦点）。True → 还原默认（后台窗口暂停）。
    菜单/锻造/吃东西工具会自动开后台模式，一般不用手动调；
    想让 AI 长期保持后台在线（自主行动）时调用一次 False 即可。
    """
    try:
        r = api.set_pause(out_of_focus)
        if r.get("ok"):
            return _with_state(f"✅ 失焦暂停 = {'开' if out_of_focus else '关'}"
                               + ("（后台将完整运行）" if not out_of_focus else ""))
        return _with_state(f"⚠️ {r.get('error', '设置失败')}")
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def peek_player() -> str:
    """👤 看看玩家此刻在做什么
    手动触发一次玩家动态检测，不受心跳间隔限制。
    想知道"TA现在在干嘛"时调用这个。
    """
    data = _gather_state()
    activity = player_activity.detect_activity(data)
    player_activity.reset_heartbeat()  # 重置心跳计时，避免重复
    strip = _build_state_strip(data, full=False)
    return f"{activity}\n\n{strip}"


# ── 表情（Y键，网络同步 user 能看到；user 发你的表情会进 recent_events 小新闻） ──
EMOTE_STRINGS = {
    # 中文别名 → SDV emote 字符串（SDV 1.6 Farmer.EMOTES）
    "开心": "happy", "高兴": "happy", "happy": "happy",
    "难过": "sad", "伤心": "sad", "sad": "sad",
    "爱心": "heart", "心": "heart", "爱你": "heart", "heart": "heart",
    "惊讶": "exclamation", "哇": "exclamation", "exclamation": "exclamation",
    "音符": "note", "音乐": "note", "note": "note",
    "睡觉": "sleep", "困": "sleep", "sleep": "sleep",
    "游戏": "game", "game": "game",
    "疑问": "question", "问号": "question", "question": "question",
    "摇头": "x", "x": "x",
    "思考": "pause", "pause": "pause",
    "脸红": "blush", "blush": "blush",
    "生气": "angry", "怒": "angry", "angry": "angry",
    "点头": "yes", "同意": "yes", "yes": "yes",
    "不行": "no", "no": "no",
}
EMOTE_ICON = {
    "happy": "😊开心", "sad": "😢难过", "heart": "❤️爱心", "exclamation": "❗惊讶",
    "note": "🎵音符", "sleep": "😴睡觉", "game": "🎮游戏", "question": "❓疑问",
    "x": "❌摇头", "pause": "⏸思考", "blush": "😳脸红", "angry": "😠生气",
    "yes": "✅点头", "no": "🙅摇头",
}


@mcp.tool()
def emote(name: str = "爱心") -> str:
    """💬 发表情（Y键表情，网络同步 user 能看到）
    user 发你（AI）的表情会出现在小新闻（recent_events）里，可以回应。

    Args:
        name: 表情名（中英文都行）：开心/爱心/难过/惊讶/睡觉/生气/脸红/疑问/点头/摇头/思考/音符/游戏
    """
    key = (name or "").strip().lower()
    emote_str = EMOTE_STRINGS.get(name.strip()) or EMOTE_STRINGS.get(key) or key
    try:
        r = api._post("/emote", {"emote": emote_str})
        if r.get("ok"):
            icon = EMOTE_ICON.get(emote_str, emote_str)
            return _with_state(f"💬 发了{icon}")
        return _with_state(f"❌ 发表情失败: {r.get('error', '')}")
    except Exception as e:
        return _with_state(f"❌ {e}")


# ═══════════════════════════════════════════
#  箱子 & 背包
# ═══════════════════════════════════════════

@mcp.tool()
def scan_chests(chest: int = -1) -> str:
    """📦 扫描当前地图上所有箱子
    chest=-1: 所有箱子摘要（位置/容量/物品数，一箱一行省 token）
    chest=N:   第 N 个箱子的完整物品清单（一箱一页，防截断——2026-08-15 恒：装箱决策要看到全部物品）
    在做存放决策前先调这个看全局。
    """
    try:
        data = api._get("/scan_chests")
        if not data.get("ok"):
            return f"扫描失败: {data.get('error', '?')}"
        chests = data.get("chests", [])
        if not chests:
            return f"当前地图 {data['location']} 没有箱子"
        # 单箱完整页（一箱一页）
        if chest >= 0 and chest < len(chests):
            c = chests[chest]
            tag = f"【{c['autoTag']}】" if c.get("autoTag") else ""
            lines = [f"📦 箱子#{chest} {tag} @ ({c['x']},{c['y']}) [{c['used']}/{c['capacity']}]"]
            items = c.get("items", [])
            if not items:
                lines.append("  (空)")
            for i in items:
                lines.append(f"  · {i['name']}x{i['count']}")
            lines.append(f"💡 共 {len(items)} 种；其他箱子用 scan_chests(chest=N) 看")
            return "\n".join(lines)
        # 摘要模式
        lines = [f"当前地图: {data['location']} | 共 {len(chests)} 个箱子（scan_chests(chest=N) 看单箱全清单；【类目标签】=内容过半自动归类）"]
        for idx, c in enumerate(chests):
            pos = f"({c['x']},{c['y']})"
            emo, czh = _color_display(c.get("color", ""))
            ctag = (emo + czh) if czh else "⬜"
            # ⚠️ 2026-09-04 恒：有 AI 人工标注名字就只显示名字，自动类目标签被覆盖，别双标
            tag = "" if c.get("name") else (f"【{c['autoTag']}】" if c.get("autoTag") else "")
            nm = (c.get("name") or "")
            nmtxt = f" {nm}" if nm else ""
            items = c.get("items", [])
            if items:
                item_str = ", ".join(f"{i['name']}x{i['count']}" for i in items[:5])
                if len(items) > 5:
                    item_str += f"... 共{len(items)}种"
            else:
                item_str = "(空)"
            lines.append(f"  📦#{idx}{tag}{ctag} {pos}{nmtxt} [{c['used']}/{c['capacity']}] {item_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"扫描箱子失败: {e}"


@mcp.tool()
def _walk_to_chest(x, y):
    """走到箱子旁边（站箱子上方朝下），自然走路+position兜底
    ⚠️ 2026-09-03 恒：旧代码硬编码 location="Farm"——玩家在小屋/棚内存箱子会被拉到
    Farm 地图 BFS 兜底瞬移到河边（冒烟实测"第一步就瞬移进河里"）。改成用当前实际地图。"""
    try:
        s = api.state()
        loc = s.get("location", {}).get("name", "Farm")
        api.walk_to_coord(loc, x, y - 1)
        time.sleep(0.3)
    except:
        api.position(x, y - 1)
        time.sleep(0.1)


# ═══ 2026-09-04 恒：storage 域压缩 11→6（scan+layout→view / store+smart→store / take+takeall→take /
#      default+cleardefault→default / tag+color→tag）。view=scan+layout 互补：layout 的⭐默认箱+剩余格 + scan 的单箱全页。 ═══
def storage_view(box: int = -1) -> str:
    """📦 看箱：box=-1 网络概览（⭐默认箱+剩余格+色名+【类目标签】，每箱一行）；
    box=N 看第 N 箱完整清单（不截断，AI 装箱决策前看全）。
    = 原 storage_layout(-1) + scan_chests(N) 互补合一。"""
    if box >= 0:
        return scan_chests(box)
    return storage_layout()


def _primary_chest_for_smart():
    """智能 store 要走到的主箱：默认箱(若设) else 空位最多的箱。返回 {"x","y"} or None。"""
    dflt = _storage_default_for_loc()
    if dflt:
        return {"x": dflt.get("x"), "y": dflt.get("y")}
    try:
        data = api._get("/scan_chests")
        best = None
        for c in data.get("chests") or []:
            if best is None or c.get("freeSlots", 0) > best.get("freeSlots", 0):
                best = c
        return {"x": best["x"], "y": best["y"]} if best else None
    except Exception:
        return None


def _first_chest_for_item(name):
    """批量取的第一件物品所在的箱（中英名/ID 子串匹配，仿 storage_find）。返回 {"x","y"} or None。"""
    q = (name or "").strip().lower()
    if not q:
        return None
    try:
        for c in (api._get("/scan_chests").get("chests") or []):
            for i in c.get("items") or []:
                if (q in (i.get("name", "") or "").lower() or q in (i.get("displayName", "") or "").lower()
                        or q in (i.get("qualifiedId", "") or "").lower()):
                    return {"x": c["x"], "y": c["y"]}
    except Exception:
        pass
    return None


@mcp.tool()
def chest_store(x: int, y: int, name: str = "") -> str:
    """📥 把背包物品存到指定箱子
    自动走到箱子旁边，然后存放。

    Args:
        x: 箱子 X 坐标（用 scan_chests 查）
        y: 箱子 Y 坐标
        name: 物品名，不指定则存所有非工具物品
    """
    try:
        _walk_to_chest(x, y)
        d = {"x": x, "y": y}
        if name:
            d["name"] = name
        r = api._post("/store", d)
        stored = r.get("stored", [])
        if stored:
            detail = ", ".join(f"{s['item']}x{s['count']}" for s in stored)
            return f"存了 {len(stored)} 种物品: {detail}"
        return "没有存任何东西（可能背包没有匹配的物品）"
    except Exception as e:
        return f"存放失败: {e}"


@mcp.tool()
def chest_take(x: int, y: int, name: str, count: int = 999) -> str:
    """📥 从箱子取出物品到背包
    自动走到箱子旁边，然后取物品。

    Args:
        x: 箱子 X 坐标
        y: 箱子 Y 坐标
        name: 物品名
        count: 数量（默认全部取出）
    """
    try:
        _walk_to_chest(x, y)
        r = api._post("/chest_take", {"x": x, "y": y, "name": name, "count": count})
        taken = r.get("taken", 0)
        if taken > 0:
            return f"从 ({x},{y}) 箱子取了 {name} x{taken}"
        return f"箱子 ({x},{y}) 里没有 {name}"
    except Exception as e:
        return f"取物品失败: {e}"


def storage_find(name: str = "") -> str:
    """🔍 模糊查找哪个箱子放着该物品（中英文名/ID 子串都认，2026-09-03 恒）
    一次扫当前场景所有箱，报「物品 在 【类目标签】箱@(x,y) ×count」。
    想真正取出→storage take（精确）；想存→storage store/smart。
    """
    try:
        q = (name or "").strip().lower()
        if not q:
            return "❌ 要查什么（storage find name=物品名/中文/子串）"
        data = api._get("/scan_chests")
        if not data.get("ok"):
            return f"扫描失败: {data.get('error', '?')}"
        chests = data.get("chests", [])
        if not chests:
            return f"当前地图 {data.get('location')} 没有箱子"
        lines = [f"🔍 找「{name}」（当前地图 {data.get('location')}）:"]
        for c in chests:
            matched = [(i, i.get("count", 0)) for i in c.get("items", [])
                       if q in (i.get("name", "") or "").lower()
                       or q in (i.get("displayName", "") or "").lower()
                       or q in (i.get("qualifiedId", "") or "").lower()]
            if not matched:
                continue
            tag = f"【{c['autoTag']}】" if c.get("autoTag") else ""
            nm = (c.get("name") or "")
            nmtxt = f"「{nm}」" if nm else ""
            cnt = sum(n for _, n in matched)
            dname = matched[0][0].get("displayName") or matched[0][0].get("name")
            lines.append(f"  · {tag}{nmtxt}@({c['x']},{c['y']}) {dname} x{cnt}")
        if len(lines) == 1:
            return f"❌ 当前场景没有箱子里有「{name}」——storage view 看看有哪些"
        lines.append("  💡 取用：storage take items=\"……\")")
        return _with_state("\n".join(lines))
    except Exception as e:
        return f"查找失败: {e}"


def storage_take(items: str = "", x: int = -1, y: int = -1, name: str = "", count: int = 999) -> str:
    """📤 取物（统一走位：先走到箱子旁）。两类取法：
    x,y>0 且 name 给了 = 取指定箱的某物（拟人走到那箱）；否则按 items 智能找箱批量取，
    只走到**第一个**配到的箱旁（跨箱凑数仍全收，2026-09-04 恒：智能存取也走位，不区分拟人/原子）。
    items: 逗号/空格分隔，每项可带数量如 "西瓜,铜矿石×30,木材"（不带数=取该类全量）。
    精确匹配（中英文名/ID）；模糊查哪个箱先用 storage find。
    """
    try:
        # 单箱精确取（拟人走到那箱再取）
        if name and x >= 0 and y >= 0:
            _walk_to_chest(x, y)
            return chest_take(x, y, name, count)
        reqs = []
        for part in re.split(r"[,，;；]+", (items or "").strip()):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(.*?)\s*[xX×]\s*(\d+)$", part)
            if m:
                reqs.append({"name": m.group(1).strip(), "count": int(m.group(2))})
            else:
                reqs.append({"name": part, "count": -1})
        if not reqs:
            return "❌ 要取什么（storage take items=\"西瓜,铜矿石×30\" 或 x,y+name）"
        # 🤖 2026-09-04：批量只走到第一个配到的箱旁（拟人），跨箱凑数仍全收
        _first = _first_chest_for_item(reqs[0]["name"])
        if _first:
            _walk_to_chest(_first["x"], _first["y"])
        r = api._post("/chest_take_list", {"items": reqs})
        if not r.get("ok"):
            return f"取物失败: {r.get('error', r)}"
        lines = ["📤 从当前场景箱子取物" + (f" ({r.get('location')})" if r.get("location") else "")]
        any_taken = False
        for it in r.get("items", []):
            itn = it.get("item")
            taken = it.get("taken", 0)
            if taken <= 0:
                lines.append(f"  ⚠️ 「{itn}」没取到（箱子没有；storage find 搜搜）")
                continue
            any_taken = True
            srcs = it.get("from", [])
            src_txt = ", ".join(
                f"({'【' + s['autoTag'] + '】' if s.get('autoTag') else ''}{s.get('name','')}@({s['x']},{s['y']}) ×{s.get('got',0)})"
                for s in srcs) if srcs else "?"
            w = it.get("wanted")
            amt = f"x{taken}" if (w is None or w == -1) else f"{taken}/{w}"
            lines.append(f"  ✅ {itn} {amt} ← {src_txt}")
        if not any_taken:
            lines.append("  什么都没取到（可能背包满了——先清背包/再找）")
        return _with_state("\n".join(lines))
    except Exception as e:
        return f"取物失败: {e}"


def storage_color(target: str = "", color: str = "") -> str:
    """🎨 给箱子改色（写在箱子真实染色，storage view 立即可见）。改名用 storage tag。2026-09-04
    target: 同其它(中文色名/#hex/名字/坐标/类目标签)定位箱。
    color: #RRGGBB 或 色名(红/粉/紫/黑…)；留空/默认/clear/复位 = 复位默认木纹。
    ⚠️ 别染纯 #000000（=默认木纹哨兵，被识别成未染色）；要"黑箱"用暗灰 #303030。
    """
    try:
        if not target.strip():
            return "❌ 要指定哪个箱子（storage color target=... color=...）"
        targ = _resolve_storage_target(target.strip())
        if isinstance(targ, str):
            return _with_state(targ)
        hexc = _color_to_hex(color)
        if hexc and not (len(hexc) == 7 and hexc.startswith("#")):
            return _with_state(f"❌ 颜色格式错: {color}（要 #RRGGBB 或 色名/默认 复位）")
        r = api._post("/chest_color", {"x": targ.get("x"), "y": targ.get("y"), "color": hexc})
        if not r.get("ok"):
            return _with_state(f"改色失败: {r.get('error', r)}")
        newc = r.get("color") or ""
        act = "复位默认(木纹)" if r.get("reset") else f"染成 {newc}"
        return _with_state(f"🎨 ({targ.get('x')},{targ.get('y')}) {act}")
    except Exception as e:
        return _with_state(f"改色失败: {e}")


# ═══════════════════════════════════════════
#  🧺 智能存储（/store_all，2026-08-14）
#  只处理当前场景箱子（恒拍板：不跨地图）。
#  AI 默认"堆高高"（进已有同类堆）；用户可说"放红色箱子"指定箱。
# ═══════════════════════════════════════════

# (hue 下限, hue 上限, 颜色名, emoji)
_HEX_COLOR_BANDS = [
    # ⚠️ 2026-09-03 恒：粉色区间扩到 315-360——浅粉难分（#FF75C3=326 之前算粉，但 #FFC0CB≈349
    #    被原 (330,360) 抓成红）。粉=315-360，紫=260-315，避免粉/紫/红混淆（真草莓红在 0-20）。
    (0, 20, "red", "🟥"), (20, 45, "orange", "🟧"), (45, 70, "yellow", "🟨"),
    (70, 160, "green", "🟩"), (160, 200, "cyan", "🟦"), (200, 260, "blue", "🟦"),
    (260, 315, "purple", "🟪"), (315, 360, "pink", "🟪"),
]
_COLOR_ZH = {"red": "红", "orange": "橙", "yellow": "黄", "green": "绿", "cyan": "青",
             "blue": "蓝", "purple": "紫", "pink": "粉", "gray": "灰", "black": "黑"}


def _color_display(hexstr):
    """→ (emoji, 中文色名)；未染色/默认/非法 → ('⬜', '')。AI 靠名字区分粉/紫/蓝（emoji 粉紫共用 🟪）。"""
    emo, name = _hex_to_color_name(hexstr)
    if not name:
        return ("⬜", "")
    return (emo, _COLOR_ZH.get(name, name))
_COLOR_NAME_SYNONYMS = {
    "红色": "red", "红": "red", "橙色": "orange", "橙": "orange", "黄色": "yellow", "黄": "yellow",
    "绿色": "green", "绿": "green", "青色": "cyan", "蓝色": "blue", "蓝": "blue",
    "紫色": "purple", "紫": "purple", "粉色": "pink", "粉": "pink",
    "黑色": "black", "黑": "black", "灰色": "gray", "灰": "gray",
}

# 🎨 2026-09-04 恒：改色工具的 色名→hex（storage color）。⚠️黑用暗灰 #303030——纯 #000000=默认木纹哨兵(被识别成未染色)。
_COLOR_KEY_HEX = {
    "red": "#C9423B", "orange": "#E98C2B", "yellow": "#E8C93B", "green": "#4FA33B",
    "cyan": "#3BA7C9", "blue": "#3B6FC9", "purple": "#8A4FD0", "pink": "#F07BA9",
    "gray": "#888888", "black": "#303030",
}


def _color_to_hex(name):
    """storage color 的 color 参数 → ('#RRGGBB' 或 ''=复位默认)。支持 #hex/RRGGBB/英文key/中文色名/默认。"""
    s = (name or "").strip()
    low = s.lower()
    if not s or low in ("clear", "默认", "木", "复位", "default", "reset", "none"):
        return ""
    if s.startswith("#"):
        return s if len(s) == 7 else ("#" + s if len(s) == 6 else s)
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return "#" + s
    if low in _COLOR_KEY_HEX:
        return _COLOR_KEY_HEX[low]
    for cn, key in _COLOR_NAME_SYNONYMS.items():
        if s in cn or s == key:
            return _COLOR_KEY_HEX.get(key, "")
    return s  # 未知，交给 C# 报格式错

# 🆕 2026-09-03 恒：自动类目标签词表（_resolve_storage_target 认「矿石箱」这类词→定位 autoTag 箱）
_AUTO_LABEL_WORDS = {
    "矿": {"矿", "矿石", "宝石", "矿箱", "宝石箱", "石英"},
    "古物": {"古物", "古物箱", "骨骼"},
    "鱼": {"鱼", "鱼箱", "水产", "鱼子", "鲑"},
    "种子": {"种子", "种子箱"},
    "作物": {"作物", "作物箱", "蔬果", "菜箱", "水果箱", "蔬菜箱", "果实", "花果"},
    "农产": {"农产", "农产品", "蛋奶", "加工品", "奶酪", "蛋黄酱", "蜂蜜"},
    "建材": {"建材", "建材箱", "木材", "石头", "建筑", "材料", "木头", "石块"},
    "料理": {"料理", "食物", "熟食", "菜肴", "烹饪"},
    "装备": {"装备", "装备箱", "工具箱", "武器", "钓具"},
}


def _hex_to_color_name(hexstr):
    """#RRGGBB → (emoji, 颜色名)。纯白/纯黑(哨兵)/透明/非法 → ('', '')（未染色）。"""
    if not hexstr or not isinstance(hexstr, str):
        return ("", "")
    h = hexstr.lstrip("#")
    if len(h) != 6:
        return ("", "")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return ("", "")
    mx, mn = max(r, g, b), min(r, g, b)
    # ⚠️ 2026-09-04 恒：SDV 未染色的默认箱 playerChoiceColor 读出来是纯 #000000(哨兵/木纹)，不是真黑。
    #    真·黑(玩家染的)是暗灰如 #404040。把纯黑当未染色，别把原木默认箱认成黑。
    if mx == 0:
        return ("", "")
    if mn >= 250:
        return ("", "")  # 纯白 = 未染色
    if mx - mn < 25:
        return ("⬜", "gray") if mx > 120 else ("⬛", "black")
    d = mx - mn
    if mx == r:
        hue = ((g - b) / d) % 6
    elif mx == g:
        hue = (b - r) / d + 2
    else:
        hue = (r - g) / d + 4
    hue = (hue * 60) % 360
    for lo, hi, name, emo in _HEX_COLOR_BANDS:
        if lo <= hue < hi:
            return (emo, name)
    return ("🟥", "red")


def _current_location_name():
    try:
        return (api.state().get("location") or {}).get("name", "?")
    except Exception:
        return "?"


def _storage_default_for_loc():
    """当前场景配置的默认箱 {x,y}，没设返回 None。"""
    try:
        return (_storage_cfg.get("default") or {}).get(_current_location_name())
    except Exception:
        return None


def _resolve_storage_target(t):
    """把用户说的 target（"红色箱子"/#hex/名字/"x,y"）解析成具体箱子坐标 {"x","y"}。
    统一查 scan 定位（避免 C# 颜色/名字匹配的歧义）；解析失败返回错误提示字符串。
    注意别过度剥"箱"："内置冰箱"→"内置冰"就匹配不上了，色名检测只剥一次、名字匹配用原始串。"""
    t = t.strip()
    if not t:
        return None
    # 坐标 "x,y"
    m = re.match(r"^\s*(\d+)\s*[,，]\s*(\d+)\s*$", t)
    if m:
        return {"x": int(m.group(1)), "y": int(m.group(2))}
    try:
        data = api._get("/scan_chests")
        chests = data.get("chests") or []
        # 十六进制
        if t.startswith("#") or re.match(r"^[0-9a-fA-F]{6}$", t):
            hexc = t if t.startswith("#") else "#" + t
            for c in chests:
                if (c.get("color") or "").lower() == hexc.lower():
                    return {"x": c["x"], "y": c["y"]}
            return f"❌ 当前场景没有 {hexc} 颜色的箱子"
        # 中文颜色名 / 名字子串 / 自动类目标签词
        raw = t.lower()
        core = raw.replace("箱子", "").replace("箱", "").strip()
        want = None
        for cn, key in _COLOR_NAME_SYNONYMS.items():
            if core == cn or core == key:
                want = key
                break
        # 🆕 2026-09-03 恒：认自动类目标签词（"矿箱"/"矿石"/"作物箱"→ 对应 autoTag 箱），AI 看标签定位箱
        want_bucket = None
        for bk, words in _AUTO_LABEL_WORDS.items():
            if any(w in raw or w in core for w in words):
                want_bucket = bk
                break
        picked = None
        for c in chests:
            dname = (c.get("name") or "").lower()
            if want is not None:
                _, cname = _hex_to_color_name(c.get("color", ""))
                if cname == want:
                    if picked is None:
                        picked = c
                    if c.get("freeSlots", 0) > 0:
                        picked = c  # 优先挑有空位的同色箱子
                        break
            elif want_bucket is not None:
                if want_bucket in {"矿", "古物", "鱼", "种子", "作物", "农产", "建材", "料理", "装备"}:
                    if (c.get("autoTag") or "") == want_bucket:
                        picked = c
                        if c.get("freeSlots", 0) > 0:
                            picked = c  # 优先挑有空位的同类目箱
                            break
            else:
                # 名字子串：原始串 或 剥"箱"后的串 命中都算（矿石箱/矿石/内置冰箱）
                if raw in dname or core in dname:
                    picked = c
                    break
        if picked is not None:
            return {"x": picked["x"], "y": picked["y"]}
        what = f"叫「{t}」" if want is None and want_bucket is None else (f"{core}颜色的" if want else f"{want_bucket}类的")
        return f"❌ 当前场景没有{what}的箱子（storage view 看看有哪些）"
    except Exception as e:
        return f"❌ 解析目标失败: {e}"


@mcp.tool()
def storage_layout() -> str:
    """📦 当前场景箱子网络视图（检测箱子的口）
    列出每箱：位置/染色/名字/占用/物品。AI 存东西前先看这个，知道哪箱是什么。
    ⭐ 标记的是本场景默认箱（storage_default 设的）。
    """
    try:
        data = api._get("/scan_chests")
        if not data.get("ok"):
            return f"扫描失败: {data.get('error', '?')}"
        chests = data.get("chests", [])
        if not chests:
            return f"当前地图 {data.get('location')} 没有箱子"
        total_free = sum(c.get("freeSlots", 0) for c in chests)
        lines = [f"📦 当前场景箱子：{len(chests)} 箱 | 剩余 {total_free} 格"]
        dflt = _storage_default_for_loc()
        for c in chests:
            pos = f"({c['x']},{c['y']})"
            emo, czh = _color_display(c.get("color", ""))
            tag = (emo + czh) if czh else "⬜"   # 🆕 2026-09-03 恒：带中文色名（🟪粉 vs 🟪紫），AI 分得清粉/紫/蓝
            if c.get("name"):
                tag += f"「{c['name']}」"   # ⚠️ 2026-09-04 恒：AI 人工标注的名字优先——自动类目标签被覆盖，别双标
            elif c.get("autoTag"):
                tag += f"【{c['autoTag']}】"
            star = " ⭐" if dflt and (c["x"], c["y"]) == (dflt.get("x"), dflt.get("y")) else ""
            items = c.get("items", [])
            if items:
                item_str = ", ".join(f"{i['name']}x{i['count']}" for i in items[:6])
                if len(items) > 6:
                    item_str += f"... 共{len(items)}种"
            else:
                item_str = "(空)"
            lines.append(f"  {tag} {pos}{star} [{c['used']}/{c['capacity']}] {item_str}")
        return _with_state("\n".join(lines))
    except Exception as e:
        return f"扫描箱子失败: {e}"


def _parse_store_spec(spec):
    """解析 storage store 的 what/items 参数 → (名字列表, counts dict)。
    spec: 逗号分隔字符串（项可带 xN/×N/*N 数量）/ 字符串列表 / [{name,count}] dict 列表。
    名字只留物品名（剥计数后缀）；counts[name]=N → 只存那 N 份、余量留背包（C# 拆堆）。"""
    if spec is None:
        return None, None
    if isinstance(spec, str):
        raw = [w for w in re.split(r"[,，;；]+", spec) if w.strip()]
    elif isinstance(spec, (list, tuple)):
        raw = spec
    else:
        return None, None
    names, counts = [], {}
    for w in raw:
        if isinstance(w, dict):
            nm = str((w.get("name") or w.get("item") or "")).strip()
            cnt = w.get("count", -1)
            if nm:
                names.append(nm)
                if isinstance(cnt, (int, float)) and cnt >= 0:
                    counts[nm] = int(cnt)
            continue
        w = str(w).strip()
        if not w:
            continue
        m = re.match(r"^(?P<n>.*?)[\s]*(?:x|×|\*)\s*(?P<c>\d+)$", w)
        if m:
            nm = m.group("n").strip()
            names.append(nm)
            counts[nm] = int(m.group("c"))
        else:
            names.append(w)
    return (names or None), (counts or None)


@mcp.tool()
def storage_store(what: str = "", items: str = "", target: str = "", keepTools: bool = True, all: bool = False) -> str:
    """🧺 场景内智能存储（AI 堆高高），或用户指定箱直放
    target 留空 = 智能模式：每个物品进已有同类堆的箱子（空位最多优先），新物品进默认箱。
    target 填了 = 指定箱模式：what/items 指定的（或 all）放进那个箱子。
      target 支持：中文颜色名("红色箱子"/"红箱")、#RRGGBB、箱子名字子串、坐标 "x,y"
    what / items: 逗号/空格分隔的物品名，**只存这些**；名可带数量（树液x10 / ×10 / *10）→ 只存那 N 份、余量留背包（拆堆）。两个都留空=只归位（存"某箱已有同类堆"的，不清背包）。
    all: True=显式存全部非工具腾空间（清背包剩工具）；默认 False **只存指定/归位**，不会全清。
    ⚠️ 只存指定就用 what 或 items；想清背包腾空间才用 all=True。空参默认只归位、不搬背包。
    想存别的场景的箱子：先走过去再调用（只处理当前场景）。
    """
    try:
        targ = None
        if target and target.strip():
            targ = _resolve_storage_target(target.strip())
            if isinstance(targ, str):
                return _with_state(targ)
        # 🐛 2026-09-05 恒：AI 照 take 的 items 参数给 store 传清单，但 store 本是 what —— items 被 _ops_run
        #   按签名过滤静默丢掉 → what 空 → 全存（"只存指定却一键全清"根因）。两参数都接（items 当 what 别名）；
        #   名后可带数量（树液x10/*10/×10）→ 只存那 N 份、余量留背包（counts 拆堆）。
        what_list, counts = _parse_store_spec(what or items)
        dflt = _storage_default_for_loc()
        # 🤖 2026-09-04 恒：走位统一——smart/指定都先走到主箱旁（拟人），批量只走到第一个相关箱
        if targ:
            _walk_to_chest(targ["x"], targ["y"])
        else:
            _primary = _primary_chest_for_smart()
            if _primary:
                _walk_to_chest(_primary["x"], _primary["y"])
        r = api.store_all(keepTools=keepTools, what=what_list, target=targ, default=dflt, clear_all=all, counts=counts)
        if not r.get("ok"):
            return _with_state(f"存储失败: {r.get('error', r)}")

        mode = r.get("mode", "smart")
        scope = r.get("scope", "tidy")
        base = "指定箱" if mode == "target" else "智能堆叠"
        scope_txt = {"specified": "只存指定", "all": "全存腾空间", "tidy": "归位整理"}.get(scope, "")
        hint = "（要清背包腾空间传 all=True，只存指定用 items/what）" if scope == "tidy" else ""
        lines = [f"🧺 {base}·{scope_txt}{hint}" + (f" | {r.get('location')}" if r.get("location") else "")]
        if r.get("noHome"):
            lines.append(f"  ⚠️ {r['noHome']} 个没有归属的物品留下背包（箱子里没同类堆；要存它们用 items/what 指定）")
        stored = r.get("stored", [])
        if stored:
            by_chest = {}
            for s in stored:
                key = (s["to"]["x"], s["to"]["y"])
                by_chest.setdefault(key, []).append(f"{s['item']}x{s['count']}")
            for key, items in by_chest.items():
                lines.append(f"  ✅ 进 ({key[0]},{key[1]}) {len(items)}种: {', '.join(items)}")
        leftovers = r.get("leftovers", [])
        if leftovers:
            reason_txt = {"target_not_found": "指定箱没找到", "target_full": "指定箱满了",
                          "all_chests_full": "箱子全满", "chest_rejected": "放不进箱子"}
            lines.append("  ⚠️ 没存下: " + ", ".join(
                f"{lo['item']}x{lo['count']}" + ("(" + reason_txt.get(lo.get('reason'), lo.get('reason', '')) + ")" if lo.get('reason') else "")
                for lo in leftovers))
        elif not stored:
            # 什么都没动：背包里没有 what 指定的物品，或都已堆在箱子里
            if what_list:
                lines.append("  ⚠️ 没匹配到可存的物品（背包里没有指定的？storage view 看看背包）")
            else:
                lines.append("  ✅ 没有要存的（背包没有非工具物品）")
        else:
            lines.append("  ✅ 全部存下，没剩")
        lines.append(f"  📦 剩余总格: {r.get('totalFree', '?')}")
        if dflt:
            lines.append(f"  ⭐ 默认箱 {dflt.get('x')},{dflt.get('y')}（storage_default 设的）")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"存储失败: {e}")


@mcp.tool()
def storage_default(x: int = -1, y: int = -1, clear: bool = False) -> str:
    """⭐ 设/清 当前场景的默认箱（新物品没处堆时进这个箱子）
    x,y 给坐标 = 设默认（storage view 看坐标）；clear=True 或 x<0 = 清除该场景默认（回退自动选空位最多箱）。
    某场景没设默认箱 → 智能模式自动选空位最多箱。
    """
    try:
        loc = _current_location_name()
        if clear or (x < 0 or y < 0):
            (_storage_cfg.get("default") or {}).pop(loc, None)
            _settings_save()
            return _with_state(f"🧹 已清除 {loc} 的默认箱，回退自动选空位最多箱")
        (_storage_cfg.setdefault("default", {}))[loc] = {"x": x, "y": y}
        _settings_save()
        return _with_state(f"⭐ 默认箱已设: {loc}({x},{y})——新物品没处堆就进这里")
    except Exception as e:
        return f"设置失败: {e}"


@mcp.tool()
def storage_default_clear() -> str:
    """🧹 清除当前场景的默认箱设置（回退自动选空位最多箱）"""
    try:
        loc = _current_location_name()
        (_storage_cfg.get("default") or {}).pop(loc, None)
        _settings_save()
        return _with_state(f"🧹 已清除 {loc} 的默认箱，回退自动选空位最多箱")
    except Exception as e:
        return f"清除失败: {e}"


@mcp.tool()
def storage_tag(tag: str = "", target: str = "", color: str = "") -> str:
    """🏷️ 给当前场景的箱子加括号标记（AI 自己给箱子分类），可选同时改色。🌈一条命改名+改色
    名字变成「本名(标记)」，如 宝箱(矿石) / 迷你冰箱(食物)；tag 留空 = 清除标记只留本名。
    color 填入：#RRGGBB / 色名(红/粉/紫…) → 顺带改色；留空 = 不改色（保持原样）。
    target 支持：中文色名("红色箱子")、#hex、箱子名字（含已标记的）、"x,y"。
    直写 chest.Name（SDV 1.6 存档持久化）+ /chest_color，storage view 立即可见。
    标记好之后 storage store(what, target=标记名) 也能按标记定位箱子。
    """
    try:
        if not target.strip():
            return _with_state("❌ 要指定哪个箱子（target 支持颜色/名字/坐标，如 \"红色箱子\"）")
        targ = _resolve_storage_target(target.strip())
        if isinstance(targ, str):
            return _with_state(targ)
        x, y = targ.get("x"), targ.get("y")
        r = api._post("/name_chest", {"x": x, "y": y, "tag": tag.strip()})
        if not r.get("ok"):
            return _with_state(f"改名失败: {r.get('error', r)}")
        name_txt = "清除标记" if not tag.strip() else f"标记为「{r.get('name')}」"
        msgs = [f"🏷️ ({x},{y}) {name_txt}"]
        # 🌈 color 填了才改色；留空=保持不改
        if color.strip():
            hexc = _color_to_hex(color)
            if hexc and not (len(hexc) == 7 and hexc.startswith("#")):
                return _with_state(f"❌ 颜色格式错: {color}（要 #RRGGBB 或 色名）")
            cr = api._post("/chest_color", {"x": x, "y": y, "color": hexc})
            if not cr.get("ok"):
                return _with_state(f"改名成功但改色失败: {cr.get('error', cr)}")
            msgs.append("改色→" + ("复位默认" if cr.get("reset") else f"#{cr.get('color','')}"))
        return _with_state("；".join(msgs))
    except Exception as e:
        return _with_state(f"改名/改色失败: {e}")


def drop_item(name: str, count: int = 1) -> str:
    """🗑️ 从背包丢弃指定物品（直接消失，不落地面）
    用于清理背包、腾空位。

    Args:
        name: 物品名（如 "Wood"）
        count: 丢弃数量（默认 1）
    """
    try:
        r = api._post("/drop", {"name": name, "count": count})
        if not r.get("ok"):
            return f"丢弃失败: {r.get('error', r)}"
        return f"已丢弃 {name} x{r.get('removed', 0)}，背包剩 {r.get('inventoryLeft')} 格"
    except Exception as e:
        return f"丢弃失败: {e}"


@mcp.tool()
def menu_claim_swap(replace: str = "") -> str:
    """🎁 满包接鱼/箱子领取：原子一步领取并替换（背包满时，把领取物换进背包格、旧物即弃）。
    专治"背包满又钓上鱼/领箱子 → ItemGrabMenu 待领槽"。不走坐标、不靠 heldItem 读，稳定。

    Args:
        replace: 要丢弃的背包物品名（如 "Copper Bar"）。空=自动找首个非工具/武器格替换。
    """
    try:
        r = api._post("/menu/claim_swap", {"replace": replace})
        if r.get("ok"):
            return _with_state(f"🎁 已领取 {r.get('claimed')} → 背包 slot{r.get('slot')}（丢弃 {r.get('replaced')} 腾位）")
        return _with_state(f"⚠️ 领取失败: {r.get('error', '未知')}")
    except Exception as e:
        return _with_state(f"❌ 领取出错: {e}")


@mcp.tool()
def gift_npc(npc_name: str, item_name: str) -> str:
    """🎁 送礼物给 NPC 村民（真实好感度系统）
    自然走到该 NPC 面前再送（不"飞过去"），好感度按喜好度变化（最爱/喜欢/一般/不喜欢/讨厌）。
    每周限 2 次、每天限 1 次（SDV 机制）。NPC 在别的图时先 go_to 过去再送。

    Args:
        npc_name: NPC 名字（如 "Leah"）
        item_name: 背包里的物品名（如 "Grape"）
    """
    try:
        # 0. 自然走到 NPC 面前（当前图内走 /move 自然走路，走不到 position 兜底）
        try:
            fr = api._get("/find_npc", {"name": npc_name})
            if fr.get("ok") and fr.get("npcs"):
                n = fr["npcs"][0]
                if n.get("location") == api.current_location():
                    api.walk_natural(int(n["x"]), int(n["y"]) + 1)
        except Exception:
            pass  # 找不到/跨图就跳过走位，交给 /gift 处理
        r = api._post("/gift", {"target": npc_name, "item": item_name})
        if not r.get("ok"):
            return f"送礼失败: {r.get('error', r)}"
        d = r.get("delta", 0)
        label = r.get("tasteLabel", "")
        heart = "❤" if d > 0 else ("💔" if d < 0 else "➖")
        sign = "+" if d > 0 else ""
        return (f"送 {r.get('item')} 给 {r.get('target')}（{label}）"
                f" 好感 {r.get('friendshipBefore')} → {r.get('friendshipAfter')} ({sign}{d}) {heart}")
    except Exception as e:
        return f"送礼失败: {e}"


@mcp.tool()
def give_item(player_name: str, item_name: str) -> str:
    """🎁 送物品给另一位玩家（物品转移）
    SDV 里玩家之间没有好感度条，本质是把物品从自己的背包转到对方背包。

    Args:
        player_name: 目标玩家的角色名（用 /state 的 otherPlayers 或名单）
        item_name: 背包里的物品名
    """
    try:
        r = api._post("/gift", {"target": player_name, "item": item_name})
        if not r.get("ok"):
            return f"赠送失败: {r.get('error', r)}"
        return f"已送 {r.get('item')} 给 {r.get('target')} 🎁"
    except Exception as e:
        return f"赠送失败: {e}"


@mcp.tool()
def check_friendship(npc_name: str) -> str:
    """❤️ 查询与某 NPC 的好感度
    送礼前先查，挑好感低/喜欢的东西送。

    Args:
        npc_name: NPC 名字（如 "Leah"）
    """
    try:
        r = api._get("/friendship", {"npc": npc_name})
        if not r.get("ok"):
            return f"查询失败: {r.get('error', r)}"
        if not r.get("known"):
            return f"还没认识 {npc_name}（好感 0）"
        return (f"与 {npc_name} 好感 {r.get('points')} 分（{r.get('hearts')}❤️），"
                f"本周已送 {r.get('giftsThisWeek')}/2 次，今天已送 {r.get('giftsToday')} 次")
    except Exception as e:
        return f"查询失败: {e}"


def _require_counter(poi_name: str, cur_ok_locs: tuple, reject_hint: str) -> str:
    """柜台服务前置检查（2026-08-18 恒：早期内部端点加地点限制+拟人走位）。
    不在允许地点 → 返回拒绝提示；已在 → walk_to 柜台站好朝上，返回走位日志正文（无状态条）。
    失败/拒绝返回的字符串以 ❌ 开头，调用方判断。
    """
    try:
        cur = _current_location_name()
    except Exception:
        cur = "?"
    if cur not in cur_ok_locs:
        return f"❌ 不在{cur_ok_locs[0]}，不能操作。{reject_hint}"
    try:
        nav = walk_to(poi_name)  # 同图走到柜台 + 自动站位+朝向（POI_FACE）
    except Exception as e:
        return f"❌ 到柜台失败: {e}"
    nav_body = nav.split(_STATE_SEP)[0] if _STATE_SEP in nav else nav
    if nav_body.startswith("❌") or nav_body.startswith("⚠️"):
        return nav  # 走位失败，原样返回（含错误/超时提示）
    return nav_body


@mcp.tool()
def process_geode() -> str:
    """⛏️ 砸开晶球
    需先到铁匠铺柜台（Blacksmith），AI 会走到克林特柜台前站好再砸。
    自动扣 25g，返回开出的矿物/古物。
    """
    step = _require_counter("铁匠铺(柜台)", ("Blacksmith",), "先 walk_to「铁匠铺(柜台)」到克林特柜台再试。")
    if step.startswith("❌") or step.startswith("⚠️"):
        return step
    try:
        r = api._post("/process_geode")
        if r.get("ok"):
            return f"{step}\n⛏️ 砸开 {r['geode']}！\n  获得: {r['result']}\n  {r.get('description', '')}"
        else:
            return f"❌ {r.get('error', '没有晶球')}"
    except Exception as e:
        return f"❌ {e}"


@mcp.tool()
def process_geodes(count: int = 1) -> str:
    """⛏️ 批量砸开晶球
    需先到铁匠铺柜台（Blacksmith），AI 会走到克林特柜台前站好再砸。
    一次砸开指定数量的晶球（顺序：Geode->Frozen->Magma->Omni）。
    自动扣费 25g/个，返回所有开出物清单。

    Args:
        count: 要砸的晶球数量（默认 1，最大 999）
    """
    step = _require_counter("铁匠铺(柜台)", ("Blacksmith",), "先 walk_to「铁匠铺(柜台)」到克林特柜台再试。")
    if step.startswith("❌") or step.startswith("⚠️"):
        return step
    try:
        r = api._get(f"/process_geode_batch?count={count}")
        if r.get("ok"):
            processed = r['processed']
            lines = [f"{step}\n⛏️ 砸开 {processed} 颗晶球 (花费 {r['cost']}g，剩余 {r['remainingGold']}g)"]
            for item in r.get('results', []):
                lines.append(f"  . {item['itemName']}")
            return "\n".join(lines)
        else:
            return f"❌ {r.get('error', ' 操作失败')}"
    except Exception as e:
        return f"❌ {e}"


@mcp.tool()
def museum_donate() -> str:
    """🏛️ 一键捐赠背包里所有可捐矿物/古物
    需先到博物馆柜台（ArchaeologyHouse），AI 会走到柜台站好再捐。
    自动找空展位摆放，同步 MuseumPieces，返回捐赠明细。
    """
    step = _require_counter("博物馆(柜台)", ("ArchaeologyHouse",), "先 walk_to「博物馆(柜台)」到柜台再试。")
    if step.startswith("❌") or step.startswith("⚠️"):
        return step
    try:
        r = api._post("/museum_donate")
        if r.get("ok"):
            items = r.get("donated") or []
            lines = [f"{step}\n🏛️ 捐赠 {len(items)} 件（剩 {r.get('remainingSlots')} 个展位）"]
            for it in items:
                lines.append(f"  . {it.get('item', it)} → 展位({it.get('tileX')},{it.get('tileY')})")
            return "\n".join(lines)
        else:
            return f"❌ {r.get('error', '没有可捐的新物品或展位已满')}"
    except Exception as e:
        return f"❌ {e}"


def clear_ground(x: int, y: int) -> str:
    """🧹 清理指定坐标的地面掉落物或卡死的物品
    用这个移除捡不起来的石头/树枝等地面杂物。

    Args:
        x: 目标 X 坐标
        y: 目标 Y 坐标
    """
    try:
        r = api._get(f"/clear_ground?x={x}&y={y}")
        if r.get("ok"):
            return f"🧹 已清理 ({x},{y})"
        else:
            return f"❌ {r.get('error', '清理失败')}"
    except Exception as e:
        return f"❌ {e}"


# ═══════════════════════════════════════════
#  任务系统
# ═══════════════════════════════════════════

# 🚫 2026-09-01 恒拍板：list_quests / quest_progress 已退役——原靠 DumpObj 原始字段 + /quest_progress 抽象字段
#    （曾给"绿豆"不明数据）。任务改成"菜单为唯一权威"：menu journal 开日志 + menu read 读 QuestLog 卡(含子目标进度)。
#    这两个函数留作内部兜底(不再 @mcp.tool、不再域 dispatch；仅 /quest_list 端点仍被 _quest_know_hint 用)。
def list_quests() -> str:
    """📋 🚫 已退役（不再暴露给 AI）。任务看 menu journal + menu read。"""
    try:
        r = api._get("/quest_list")
        if r.get("ok"):
            lines = [f"📋 共 {r['count']} 个任务/订单:\n"]
            for i, q in enumerate(r.get("quests", []), 1):
                src = q.get("source", "?")
                if src == "questLog":
                    lines.append(f"{i}. [📜 常规] {q['dump']}")
                elif src == "specialOrders":
                    status = "已接" if q.get("accepted") else "可接"
                    lines.append(f"{i}. [📋 特殊订单 {status}] {q['dump']}")
                elif src == "availableSpecialOrders":
                    lines.append(f"{i}. [📋 可接订单] {q['dump']}")
            _kh = _quest_know_hint()
            if _kh:
                lines.append(_kh)
            return "\n".join(lines)
        else:
            return f"❌ {r.get('error', '获取失败')}"
    except Exception as e:
        return f"❌ {e}"


# ⛔ 2026-08-22 accept_quest 已退役：接取特殊订单改走板上 menu click(button=accept…)（可靠 UI 路径，子目标会初始化）。
#    内部端点 /quest_accept 仍留在 C#（未删，需重编译 DLL 才清），此处不再暴露/推荐。


def quest_progress() -> str:
    """📋 🚫 已退役（不再 @mcp.tool、不再域 dispatch）。任务进度一律走 menu read(QuestLog 卡含子目标）。"""  # kept internal for fallback
    try:
        r = api.quest_progress()
        if r.get("ok"):
            lines = [f"📋 任务进度 ({r['count']} 个):\n"]
            for q in r.get("quests", []):
                src = q.get("source", "?")
                if src == "questOfTheDay":
                    status = "✅" if q.get("completed") else "⏳"
                    lines.append(f"  {status} [📰 今日求助] {q.get('title', '?')}")
                    lines.append(f"      {q.get('description', '')[:80]}")
                    lines.append(f"      ⏱ 剩余 {q.get('daysLeft', '?')}天  💰 {q.get('moneyReward', '?')}g")
                elif src == "questLog":
                    status = "✅" if q.get("completed") else "⏳"
                    t = q.get("type", "?")
                    desc = q.get("description", "")[:60]
                    lines.append(f"  {status} [📜 {t}] {q.get('title', '?')}")
                    if desc: lines.append(f"      {desc}")
                    # 进度信息
                    prog_parts = []
                    k = q.get("killed", "0")
                    if k and k != "0": prog_parts.append(f"💀 {k}/{q.get('required', '?')}")
                    c = q.get("caught", "0")
                    if c and c != "0": prog_parts.append(f"🐟 {c}/{q.get('required', '?')}")
                    col = q.get("collected", "0")
                    if col and col != "0": prog_parts.append(f"📦 {col}/{q.get('required', '?')}")
                    if prog_parts: lines.append(f"      进度: {' | '.join(prog_parts)}")
                    lines.append(f"      ⏱ 剩余 {q.get('daysLeft', '?')}天  💰 {q.get('moneyReward', '?')}g")
                elif src == "specialOrders":
                    days_left = q.get('days_left', '?')
                    expiry = f"⏰ 剩 {days_left} 天" if isinstance(days_left, int) and days_left >= 0 else f"⏰ 已逾期 {-days_left if isinstance(days_left, int) else '?'} 天"
                    lines.append(f"  ⏳ [📋 特殊订单] {q.get('requester', '?')} 订单 ({expiry})")
                    for obj in q.get("objectives", []):
                        icon = "✅" if obj.get("complete") else "⏳"
                        lines.append(f"      {icon} {obj.get('description', '')[:60]}")
                        lines.append(f"         ({obj.get('currentCount', 0)}/{obj.get('maxCount', 0)})")
                elif src == "availableSpecialOrders":
                    d = q.get('duration_days', '?')
                    lines.append(f"  📋 [可接订单] {q.get('requester', '?')} (限时 {d} 天)")
                    if q.get('description'): lines.append(f"      {q['description'][:120]}")
                    if q.get('moneyReward'): lines.append(f"      💰 奖励: {q['moneyReward']}g")
            _kh = _quest_know_hint()
            if _kh:
                lines.append(_kh + "；接单走板上 menu click(button=accept…)")
            return "\n".join(lines)
        else:
            return f"❌ {r.get('error', '获取失败')}"
    except Exception as e:
        return f"❌ {e}"


# ═══════════════════════════════════════════
#  齐钻商店
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
#  商店 & 买卖
# ═══════════════════════════════════════════

def _wait_warp(location: str, timeout: float = 8.0) -> bool:
    """等 warp 生效（warp 是异步的，玩家实际到达才继续），避免 interact 打在旧地点。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = api.state()
            if s.get("location", {}).get("name") == location:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _select_option(opt: int, target: str = "ShopMenu", tries: int = 4) -> bool:
    """选对话框选项，带重试（对话按钮异步构建，常要试几次才生效）。"""
    for _ in range(tries):
        api.menu_click(option=opt)
        time.sleep(0.8)
        try:
            m = api._get("/menu")
            if m.get("type") == target:
                return True
        except Exception:
            pass
    return False


def _parse_want(want: str):
    """'木剑×2, 木锤' → [('木剑', 2), ('木锤', 1)]；支持 ×N 指定数量，逗号分隔多样。"""
    out = []
    if not want:
        return out
    for part in want.split(','):
        part = part.strip()
        if not part:
            continue
        qty = 1
        if '×' in part:
            name, q = part.rsplit('×', 1)
            try:
                qty = max(1, int(q.strip()))
            except Exception:
                qty = 1
            part = name.strip()
        if part:
            out.append((part, qty))
    return out


def _marlon_shop_flow(which: str, want: str) -> str:
    """马龙武器店 / 物品恢复服务（真实菜单交互，不用 /buy 作弊）。"""
    api.warp("AdventureGuild", 5, 13)
    _wait_warp("AdventureGuild")
    api._post("/interact", {"x": 5, "y": 12})  # 柜台交互
    time.sleep(1.2)
    if not _select_option(1 if which == "recovery" else 0):  # "想要什么？" → 商店/恢复
        m = api._get("/menu")
        _menu_close()  # 关菜单（先清光标再关，防 readyToClose 卡住）
        return f"❌ 没打开商店（{m.get('type')}）"
    m = api._get("/menu")
    label = "物品恢复服务" if which == "recovery" else "武器商店"
    si = m.get("shopItems") or []
    if not want:
        _menu_close()  # 关菜单（先清光标再关，防 readyToClose 卡住）
        lines = [f"🗡️ 马龙 {label}（{len(si)} 件）："]
        for s in si[:20]:
            lines.append(f"  {s.get('name')} {s.get('price')}g")
        return "\n".join(lines)
    # 真实购买：一次调用买多样（'木剑×2, 木锤'），每样内部循环买 N 个再关店
    want_items = _parse_want(want)
    bought = []
    for name, qty in want_items:
        r = api.menu_click(item=name, quantity=qty)
        time.sleep(0.3)
        if r.get("ok"):
            bought.append(f"{name}x{qty}")
        else:
            bought.append(f"{name}(失败)")
    _menu_close()  # 关菜单（先清光标再关，防 readyToClose 卡住）
    return f"✅ 已从{label}购买/找回: {', '.join(bought)}"


def _guild_reward_flow() -> str:
    """吉尔讨伐奖励领取（真实菜单：对话 + ItemGrabMenu 槽位点击）。"""
    api.warp("AdventureGuild", 12, 13)
    _wait_warp("AdventureGuild")
    api.face(0)
    time.sleep(0.3)
    api.interact()
    time.sleep(1.2)
    s = api.state()
    menu = s.get("activeMenu") or {}
    if menu.get("type") == "DialogueBox":
        api.key("confirm")
        time.sleep(1.0)
        s = api.state()
        menu = s.get("activeMenu") or {}
    if menu.get("type") != "ItemGrabMenu":
        return f"🗡️ 没弹出领取菜单（{menu.get('type')}）——可能没有可领奖励"
    m = api._get("/menu")
    slots = m.get("slots") or []
    if not slots:
        return "🗡️ 菜单开了但没读到槽位"
    min_y = min(slot.get("y", 9999) for slot in slots)
    grab = [s for s in slots if s.get("y", 9999) <= min_y + 100]
    claimed = 0
    for slot in grab:
        api.menu_click(x=slot.get("x"), y=slot.get("y"))
        time.sleep(0.4)
        claimed += 1
    api.menu_click(button="ok")
    time.sleep(0.5)
    return f"🗡️ 讨伐奖励领取完成（点了 {claimed} 个领取槽位）"


def _qi_shop_flow(want: str) -> str:
    """齐钻核桃房商店（真实注册机交互，不用数据层作弊）。"""
    api.warp("QiNutRoom", 7, 7)
    _wait_warp("QiNutRoom")
    api._post("/interact", {"x": 11, "y": 3})  # 注册机 (11,3)
    time.sleep(1.2)
    m = api._get("/menu")
    if m.get("type") != "ShopMenu":
        _menu_close()  # 关菜单（先清光标再关，防 readyToClose 卡住）
        return f"❌ 没打开齐商店（{m.get('type')}）"
    si = m.get("shopItems") or []
    if not want:
        _menu_close()  # 关菜单（先清光标再关，防 readyToClose 卡住）
        lines = [f"💎 齐钻商店（{len(si)} 件，价格单位=齐钻）："]
        for s in si:
            lines.append(f"  {s.get('name')} — {s.get('price')}")
        return "\n".join(lines)
    # 一次调用买多样（'烟花×2, 马笛'）
    want_items = _parse_want(want)
    bought = []
    for name, qty in want_items:
        r = api.menu_click(item=name, quantity=qty)
        time.sleep(0.3)
        if r.get("ok"):
            bought.append(f"{name}x{qty}")
        else:
            bought.append(f"{name}(失败)")
    _menu_close()  # 关菜单（先清光标再关，防 readyToClose 卡住）
    return f"✅ 已用齐钻购买: {', '.join(bought)}"


@mcp.tool()
def shop_visit(place: str, want: str = "") -> str:
    """🏪 逛店/购买/领奖励（真实菜单交互，不做弊）
    替代旧的 guild_reward / marlon_shop / qi_shop / qi_buy。全部走真实 UI：
    导航到店 → 开菜单 → 读商品 → 指定 want 则 menu_click(item=) 真实购买 → 关闭。
    ⚠️ buy_item 已退役（直购作弊），买一律走这里（真实商店 + 营业时间）。

    Args:
        place: 店铺名——guild(马龙武器店) / guild_recovery(马龙物品恢复) /
               guild_reward(吉尔讨伐奖励) / qi(齐钻核桃房商店)
        want: 要买/领的物品名，支持多样：'木剑×2, 木锤×1'（逗号分隔、×N 数量，空=只浏览不买）
    """
    _ensure_background()  # 真实菜单交互前先确保不冻结
    key = place.lower().replace(" ", "").replace("_", "")
    if key in ("guildreward", "reward", "gil", "吉尔", "讨伐"):
        return _guild_reward_flow()
    if key in ("qi", "qishop", "qigem", "齐钻", "核桃房"):
        return _qi_shop_flow(want)
    if key in ("guild", "guildshop", "marlon", "马龙", "武器"):
        return _marlon_shop_flow("shop", want)
    if key in ("guildrecovery", "recovery", "恢复"):
        return _marlon_shop_flow("recovery", want)
    return _with_state(f"❌ 未知店铺「{place}」。支持: guild / guild_reward / guild_recovery / qi")


# ⚠️ buy_item 已退役（2026-08-16 恒：直购作弊——绕柜台、不查营业时间，AI 应走真实商店 shop_visit/menu click）
def buy_item(item_id: str, quantity: int = 1, price: int = -1) -> str:
    """🛒 购买物品（直购，无需开商店菜单）——已退役，仅供内部参考
    直接从游戏系统购买指定ID的物品，扣钱、入背包。
    自动检测金钱和背包空间。

    Args:
        item_id: 物品 ID（如 "472" 或 "(O)472" 代表蔓越莓种子）
        quantity: 数量（默认 1）
        price: 单价覆盖（默认自动按商店定价*2）
    """
    try:
        data = {"id": item_id, "quantity": quantity}
        if price > 0:
            data["price"] = price
        r = api._post("/buy", data)
        if r.get("ok"):
            return _with_state(
                f"✅ 已购买 {r['bought']} x{r['quantity']}\n"
                f"   💰 花费 {r['totalCost']}g ({r['unitPrice']}g/个)\n"
                f"   💳 剩余 {r['remainingGold']}g"
            )
        else:
            return _with_state(f"❌ 购买失败: {r.get('error', '未知错误')}")
    except Exception as e:
        return _with_state(f"❌ 购买出错: {e}")


@mcp.tool()
def read_menu() -> str:
    """📋 读取当前打开的菜单（商店/对话/衣柜/信箱）
    返回菜单类型 + 内容：商店商品清单（含 bounds 点击坐标 + 分页 shopPage）、
    对话选项、按钮（含 up/down 箭头）、背包槽位、信件正文。
    浏览商店流程：read_menu 看当前页 → menu_click(button=downArrow/upArrow) 翻页 →
    menu_click(x,y) 点商品（bounds）→ menu_click(button=close) 关闭。
    衣柜取件点 bounds，放件点 slots。
    """
    try:
        _ensure_background()
        m = api.menu()
        if not m.get("open"):
            # 🎰 2026-08-23 恒：赌场小游戏是原生 Minigame 非 activeClickableMenu → /menu 报 open=false；
            #    这时提示在玩什么小游戏 + 读牌面 + 给点按钮指引。
            _mg = (api.state().get("player") or {}).get("minigame")
            if _mg:
                body = _mg
                try:
                    _st = api.minigame_state()
                    if _st.get("minigame") == "CalicoJack":
                        body = f"玩家{_st.get('playerCards')} 庄家明牌{_st.get('dealerUp')} 下注{_st.get('currentBet')}🟣"
                    elif _st.get("minigame") == "Slots":
                        body = f"转盘{_st.get('slots')} 余额{_st.get('clubCoins')}🟣"
                except Exception:
                    pass
                return _with_state(f"🎰 小游戏 {_mg}（{body}）：menu minigame action=hit/stand/quit 或 bet10/bet100/done")
            return _with_state("📋 当前没有菜单打开")
        t = m.get("type")
        lines = [f"📋 菜单: {t}"]
        si = m.get("shopItems")
        if si:
            pg = m.get("shopPage") or {}
            idx = pg.get("index", 0)
            tot = pg.get("total", 0)
            lines.append(f"  商店页 {idx + 1}~{min(idx + pg.get('pageSize', 4), tot)} / 共 {tot} 件")
            for i in si:
                mark = "🔴" if i.get("visible") else "⚪"
                b = i.get("bounds")
                # 🆕 2026-08-18 升级工具材料需求（DLL 序列化 ItemStockInformation.TradeItem）
                tname = i.get("tradeName") or i.get("trade")
                tline = f"（需 {tname}×{i.get('tradeCount')}）" if (i.get("tradeCount") and tname) else ""
                lines.append(f"  {mark} {i['name']} [{i['id']}] {i.get('price', 0)}g x{i.get('stock')}{tline}"
                             + (f" @({b['x']},{b['y']})" if b else ""))
        # 🆕 特别任务板（SpecialOrdersBoard：社区布告栏/齐先生核桃房/沙漠节马龙 同机制 2026-08-22 恒）
        #    订单卡序列化在 items（不进 shopItems）——read_menu 必须读 items 才显示任务卡。
        #    ✅ 领奖链（2026-08-29 恒实测正确，之前"板上点accept领奖"是错的）：板上accept按钮只接新单；
        #    完成单只在板上画✔不在此领。真正的领奖=①钱在任务日志点rewardBox领 ②板旁领奖箱(60,93)拿兑奖券
        #    ③刘易斯家兑奖机点mainButton兑换。
        if t == "SpecialOrdersBoard":
            cards = m.get("items") or []
            if not cards:
                lines.append("  （板上的任务卡为空——都接了/还没刷新）")
            for c in cards:
                st = "✅已接" if c.get("accepted") else ("🔓可接" if c.get("canAccept") else "🔒待解锁")
                dl = c.get("daysLeft", "?")
                lines.append(f"  · [{st}] {c.get('name')}（⏱{dl}天）")
                if c.get("description"):
                    lines.append(f"      {c.get('description')}")
                for o in (c.get("objectives") or []):
                    lines.append(f"      ▸ {o}")
                rw = c.get("rewards") or []
                if rw:
                    lines.append(f"      🎁 {'、'.join(rw)}")
                if not c.get("accepted"):
                    lines.append(f"      🖱️ 接取: menu click(button=acceptLeftQuestButton)（左卡）/ acceptRightQuestButton（右卡）")
            lines.append("  💡 特殊订单同时只能接一个；板只接单/看进度，**不在此领奖**（完成单只画✔）。")
            lines.append("  💡 收起=button=upperRightCloseButton（这是接单板，接完/看完就关，别点 accept 误接）")
            lines.append("  🏆 领奖链：①任务日志点 menu click(button=rewardBox) 领钱 ②社区布告栏左2格**领奖箱**站(60,94)朝上交互领**兑奖券**(背包要空位) "
                         "③**刘易斯家兑奖机**站(1,6)朝上交互 → menu click(button=mainButton) 兑换")
            return _with_state("\n".join(lines))
        # 📋 每日求助栏（Billboard：皮埃尔店西侧"需要帮助"栏，2026-08-29 恒）：/menu 对 Billboard 不嵌内容，
        #    由 MCP 从 quest_progress 补 questOfTheDay，让 AI 打开展板时能"看"今日求助。
        if t == "Billboard":
            try:
                qpr = api.quest_progress()
                q = next((x for x in (qpr.get("quests") or []) if x.get("source") == "questOfTheDay"), None)
                if q:
                    status = "✅ 已完成" if q.get("completed") else "⏳ 进行中"
                    rw = q.get("moneyReward") or "?"
                    lines.append(f"  {status} 今日求助：{q.get('title', '?')}")
                    lines.append(f"    {q.get('description', '')}")
                    lines.append(f"    ⏱ 剩 {q.get('daysLeft', '?')}天  💰 {rw}g")
                    lines.append("  💡 交付：收集够任务物品带到对应NPC交给它；进度开 menu journal + menu read 看(卡上含每子目标 current/max)")
                else:
                    lines.append("  （今日没有求助任务）")
            except Exception:
                lines.append("  （读不到今日求助）")
            return _with_state("\n".join(lines))
        # 📜 任务日志（QuestLog）：点 rewardBox 领已完成+有钱任务的钱（2026-08-29 恒，DLL 已暴露按钮）
        if t == "QuestLog":
            # 📜 任务日志（游戏内为准，2026-08-29 恒：含特别订单）：列任务卡，点完成+有钱卡选中→rewardBox 领钱
            cards = m.get("items") or []
            rb = next((b for b in (m.get("buttons") or []) if b.get("name") == "rewardBox"), None)
            if cards:
                lines.append("  📜 任务日志（含特别订单，游戏内为准）:")
                for c in cards:
                    st = "✅" if c.get("completed") else "⏳"
                    src = "📋特" if c.get("source") == "specialOrders" else "📜常"
                    ln = f"    {st} [{src}] {c.get('name')}"
                    if (c.get("daysLeft") or 0) > 0:
                        ln += f" ⏱{c.get('daysLeft')}天"
                    if c.get("completed") and c.get("money"):
                        ln += f" 💰{c.get('money')}g → menu click(x={c['x']}, y={c['y']}) 选中卡 → click(button=rewardBox) 领"
                    else:
                        ln += f" @(x={c['x']}, y={c['y']}) 点卡看详情"
                    lines.append(ln)
                    # 🎯 2026-09-01 恒拍板：菜单为唯一权威 → 卡上直接读子目标/常规进度（读菜单正在用的游戏对象，不另调内部抽象）
                    objs = c.get("objectives") or []
                    if objs:
                        for obj in objs:
                            o_icon = "✅" if obj.get("complete") else "⏳"
                            t = f"      {o_icon} {obj.get('description') or ''}"
                            cur, mx = obj.get("currentCount"), obj.get("maxCount")
                            if cur is not None and mx:
                                t += f" ({cur}/{mx})"
                            lines.append(t)
                    prog = c.get("progress") or {}
                    pparts = []
                    for icon, key in (("💀", "killed"), ("🐟", "caught"), ("📦", "collected")):
                        v = str(prog.get(key) or "0")
                        if v and v != "0":
                            pparts.append(f"{icon} {v}/{prog.get('required') or '?'}")
                    if pparts:
                        lines.append(f"      进度: {' | '.join(pparts)}")
                    # 📍 打开某已接特别订单→插交付点提示（2026-08-29 恒）；「神秘的齐」纸条链按步动态给
                    if c.get("source") == "specialOrders":
                        dh = _delivery_hint(c.get("name"))
                    else:
                        dh = _qi_chain_card_hint(c.get("name"))
                    if dh:
                        lines.append(f"      {dh}")
                if rb:
                    lines.append("  💰 rewardBox 在 → 选中已完成+有钱的卡后 menu click(button=rewardBox) 领钱")
                # 🧭 2026-09-01 恒拍板 enum引导：菜单交互四件套，让 AI 不猜（详情/领奖/翻页/关闭）。
                #    卡上已直接给子目标进度+⏱时限+📍交付点；点卡看完整描述，>6张才翻页。
                lines.append("  🧭 操作：①看详情=menu click(x,y) 选卡（完整描述在详情页；卡面已含子目标进度/⏱时限/📍交付） "
                             "②领已完成+有钱=menu click(button=rewardBox) ③翻页(>6张)=menu click(button=forward/back) ④收起=menu click(button=close)")
                _kh = _quest_know_hint()
                if _kh:
                    lines.append(_kh + "；接单走板上 menu click(button=accept…)")
            else:
                if rb:
                    lines.append("  💰 本日志有可领奖励：选中完成+有钱的任务卡后 menu click(button=rewardBox) 领钱")
                lines.append("  💡 点任务条目看详情；已完成任务的奖励点 rewardBox 领")
            return _with_state("\n".join(lines))
        # 🎰 特别订单兑奖机（PrizeTicketMenu）：点 mainButton 消费1张兑奖券换奖（2026-08-29 恒，DLL 已暴露按钮）
        if t == "PrizeTicketMenu":
            mb = next((b for b in (m.get("buttons") or []) if b.get("name") == "mainButton"), None)
            prizes = m.get("items") or []
            if prizes:
                lines.append("  🎰 兑奖机奖品带(固定顺序，当前/下几个): " + "、".join(f"{p.get('name')}×{p.get('stack')}" for p in prizes))
            if mb:
                lines.append("  🎰 兑奖机有 mainButton：menu click(button=mainButton) 消费1张兑奖券换 `currentPrizeTrack[0]`（手头要有兑奖券+背包空位）")
            else:
                lines.append("  🎰 兑奖机（没读到 mainButton，可能没兑奖券/已兑完？）")
            return _with_state("\n".join(lines))
        # 🍳 订单交付容器（QuestContainerMenu：格斯煎蛋卷/放物进箱）：点背包里对应物品的槽放进容器（2026-08-29 恒）
        if t == "QuestContainerMenu":
            slots = m.get("slots") or []
            lines.append("  🍳 订单交付容器：点**背包里对应物品的槽**放进去（点槽=TryToPlace）。")
            item_slots = [s for s in slots if s.get("item")]
            if item_slots:
                seen = set()
                for s in item_slots:
                    nm = s.get("item")
                    if nm in seen: continue
                    seen.add(nm)
                    lines.append(f"     {nm} ×{s.get('stack')} → menu click(x={s['x']}, y={s['y']}) 放【{nm}】进容器")
            else:
                lines.append("     背包槽: " + "、".join(f"slot{s['index']}({s.get('item') or '空'})@{s['x']},{s['y']}" for s in slots[:12]))
            given = m.get("items") or []
            if given:
                lines.append("  已放容器: " + ", ".join(f"{g.get('name')}×{g.get('stack')}" for g in given))
            lines.append("  💡 放够后点 button=ok 结算（不点不完成）；收起=button=ok；左上X=upperRightCloseButton")
            lines.append("  💡 交付完成后去任务日志 rewardBox 领钱，板旁领奖箱(60,93)领兑奖券")
            return _with_state("\n".join(lines))
        if m.get("responses"):
            lines.append("  选项:")
            for r in m["responses"]:
                lines.append(f"    [{r['index']}] {r['text']}")
        elif m.get("dialogue"):
            lines.append(f"  💬 {m['dialogue']}")
        if t == "ForgeMenu":
            # 锻造台（附魔/幻化/合成戒指）：报槽位坐标 + 操作引导（2026-08-10 实测）
            fb = {b.get("field"): b for b in (m.get("buttons") or [])}
            left = fb.get("leftIngredientSpot")
            right = fb.get("rightIngredientSpot")
            result = fb.get("craftResultDisplay")
            fbtn = fb.get("startTailoringButton")
            lines.append("  🏭 锻造台:")
            if left:
                lines.append(f"    左槽(武器/戒指1) @({left['x']},{left['y']})")
            if right:
                lines.append(f"    右槽(材料/戒指2) @({right['x']},{right['y']})")
            if result:
                lines.append(f"    结果槽 @({result['x']},{result['y']})")
            if fbtn:
                lines.append(f"    锻造按钮 @({fbtn['x']},{fbtn['y']})")
            lines.append("  操作: 点背包槽拿起→点左/右槽放入→点锻造按钮→点背包空槽放回合成结果")
            lines.append("  附魔需龙牙燃料(10/20/30递进)；合成戒指=两枚戒指直接合成")
            # 锻造槽内容（C# 反射扫的 Item 字段）
            for it in (m.get("shopItems") or []):
                lines.append(f"    槽[{it.get('field')}]: {it.get('name')} x{it.get('stack')}")
        # 光标手持物品（合成后/商店买后 heldItem，AI 要知道先放回背包）
        if m.get("heldItem"):
            hi = m["heldItem"]
            lines.append(f"  ✋ 光标拿着: {hi.get('name')} x{hi.get('stack')}（先点背包空槽放下）")
        bt = m.get("buttons")
        if bt and t != "ForgeMenu":
            lines.append(f"  按钮: {', '.join(b['name'] for b in bt)}")
        sl = m.get("slots")
        if sl and t != "ForgeMenu":
            lines.append(f"  槽位: {len(sl)} 个（点 slots 坐标放物品）")
        # 🏆 农展台 StorageContainer（2026-08-24 恒：放9件评分；展示格+背包槽已由 DLL 暴露栏位坐标）
        if t == "StorageContainer":
            dsp = m.get("items") or []
            _occ = [f"格{it['index']+1}:{it.get('name')}" for it in dsp
                    if it.get('name') and it.get('name') != '（空）']
            lines.append("  🏆 农展台展示格: " + ("、".join(_occ) if _occ else "空"))
            _cds = [f"({it['bounds']['x']},{it['bounds']['y']})" for it in dsp if it.get('bounds')]
            if _cds:
                lines.append("  摆放坐标: " + " ".join(_cds) + "（点 slots 背包格拿起→点这些展示格放进）")
            lines.append("  🎒 放: menu_click(x=背包格,y=背包格) → menu_click(x=展示格,y=展示格)；取回=点展示格拿起→点背包格放下")
        # 🎁 送礼菜单（冬星节神秘礼物）：点物品=送出，不是拿起！走 menu_click(item=名)
        if m.get("gift"):
            lines.append("  🎁 送礼菜单：点物品直接送出（menu click item=物品名），别点 okButton/收起——点了物品就被送走")
        # 🐟 满包接鱼/箱子领取（恒 2026-08-23 治本）：ItemGrabMenu 点领取物=拿起；背包满可手动替换或直接退出
        if t == "ItemGrabMenu":
            # ⚠️ 2026-08-26 恒：文案统一域形式——裸工具名在域模式下都被隐藏，AI 照着调会扑空
            lines.append("  🎁 ItemGrabMenu：点领取侧物品=拿起（一般领取用 menu click item=物品名 action=claim）")
            grab = m.get("items") or []
            if grab:
                cnt = {}
                for g in grab:
                    nm = g.get("name")
                    if not nm: continue
                    c = g.get("count") or g.get("stack") or 1
                    try: c = int(c) or 1
                    except Exception: c = 1
                    cnt[nm] = cnt.get(nm, 0) + c
                lines.append("  可领取: " + "、".join(f"{nm}×{c}" for nm, c in cnt.items())
                             + "（menu click item=物品名 action=claim / slot=序号）")
            lines.append("  🐟 背包满接鱼/箱子满（三选一，非必须替换；🚫claim_swap 替换领取已退役）：")
            lines.append("    ① 丢桶腾格: menu click action=discard item=低价值物（垃圾桶，升级有回收返金）→ 腾格后 action=claim 领取")
            lines.append("    ② 领指定格/多领: menu click action=claim slot=序号(领指定格,不想要1要4就 slot=4) · quantity=N 一次领N件(有空位多领;999=全领)")
            lines.append("    ③ 不想要直接 ok 关掉(放弃这条): menu click button=ok")
        if m.get("letterTitle"):
            lines.append(f"  📧 {m['letterTitle']}: {m.get('letterBody')}")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 读取菜单失败: {e}")


def _menu_display_fill(items: str = "") -> str:
    """🏆 农展台(StorageContainer)一次放满空槽：指定物品名列表，一次工具调用
    循环『点背包槽拿起 → 点展示格放进』N 件，不用一格格点。
    items 逗号分隔，支持 名×N 要 N 个（如 '钻石×2,山羊奶酪'）；从展示格第一个空槽依次放。
    例：menu ops=display_fill items='钻石,山羊奶酪,蛋黄酱'
    Args:
        items: 要放的物品名（逗号分隔；名×N=数量）
    """
    try:
        _ensure_background()
        m = api.menu()
        if m.get("type") != "StorageContainer":
            return _with_state(f"⚠️ 当前不是农展台(StorageContainer)，是 {m.get('type') or '无'}——先 scene interact 开农展台")
        want = []
        for part in (items or "").split(","):
            part = part.strip()
            if not part:
                continue
            if "×" in part:
                nm, ct = part.split("×", 1)
                want += [nm.strip()] * max(0, int(ct or 1))
            else:
                want.append(part)
        if not want:
            return _with_state("⚠️ 没给 items，如 ops=display_fill items='钻石,山羊奶酪' 或 '珍珠×2'")
        d = api._get("/state")
        inv = d.get("inventory") or []
        slots = {s["index"]: (s["x"], s["y"]) for s in (m.get("slots") or [])}
        empty = [it for it in (m.get("items") or [])
                 if (it.get("name") or "") in ("", "（空）") and it.get("bounds")]
        if not empty:
            return _with_state("🏆 展示格已满(9/9)")
        used = set()          # 已用的背包槽(slotIndex)，防重复取同一格
        placed = []
        for nm in want:
            if not empty:
                placed.append(f"{nm}×未放(槽满)")
                break
            # /state inventory 的 name=英文、displayName=中文（AI 用中文名），两个都匹配
            src = next((it for it in inv
                        if it.get("slotIndex") not in used
                        and (it.get("displayName") == nm or it.get("name") == nm
                             or (it.get("name") or "").lower() == nm.lower())), None)
            if src is None:
                placed.append(f"{nm}×(背包无)")
                continue
            si = src.get("slotIndex")
            sc = slots.get(si)
            if not sc:
                placed.append(f"{nm}×(无坐标)")
                continue
            used.add(si)
            api.menu_click(x=sc[0], y=sc[1]); time.sleep(0.3)   # 背包槽拿起
            ds = empty.pop(0)
            b = ds["bounds"]
            api.menu_click(x=b["x"], y=b["y"]); time.sleep(0.3)  # 展示格放进
            placed.append(f"{nm}@格{ds['index'] + 1}")
        note = f"（余{len(empty)}空槽）" if empty else "（满9格）"
        return _with_state("🏆 放满: " + "、".join(placed) + note)
    except Exception as e:
        return _with_state(f"❌ 放满失败: {e}")


def _menu_display_takeback() -> str:
    """🏆 农展台(StorageContainer)一次性收好：把展示格所有物品撤回背包（评完'必须取回'）。
    循环『点展示格(拿起)→点最前一个空背包槽(放回)』到格清空。例：menu ops=display_takeback
    """
    try:
        _ensure_background()
        m = api.menu()
        if m.get("type") != "StorageContainer":
            return _with_state(f"⚠️ 当前不是农展台(StorageContainer)，是 {m.get('type') or '无'}")
        occupied = [it for it in (m.get("items") or [])
                    if it.get("name") and it.get("name") != "（空）" and it.get("bounds")]
        if not occupied:
            return _with_state("🏆 展示格已全空")
        d = api._get("/state")
        taken = {it.get("slotIndex") for it in (d.get("inventory") or [])}
        slots = {s["index"]: (s["x"], s["y"]) for s in (m.get("slots") or [])}
        back = 0
        for it in occupied:
            b = it["bounds"]
            api.menu_click(x=b["x"], y=b["y"]); time.sleep(0.3)            # 点展示格拿起
            free = next((i for i in range(36) if i not in taken), None)     # 最前一个空背包槽
            if free is None:
                break
            fc = slots.get(free)
            if not fc:
                break
            api.menu_click(x=fc[0], y=fc[1]); time.sleep(0.3)               # 放回背包
            taken.add(free)
            back += 1
        note = "（展示格已清空）" if back == len(occupied) else f"（收回{back}/{len(occupied)}，余仍在展示格）"
        return _with_state(f"🏆 收好: {back} 件物品已取回背包{note}")
    except Exception as e:
        return _with_state(f"❌ 收好失败: {e}")


def _menu_levelup_choose(side: str = "", profession: int = -1) -> str:
    """🧬 技能升级职业选择(5/10级)：确定选哪个分支。
    不带参调用 → 只读当前 LevelUpMenu 给的左右选项（供 AI 配 /profile 分析后决定）。
    例：menu ops=levelup_choose side=left / side=right / profession=8
    """
    try:
        _ensure_background()
        am = api.state().get("activeMenu") or {}
        if am.get("type") != "LevelUpMenu":
            return _with_state("⚠️ 当前没有技能升级菜单(LevelUpMenu)在开着")
        lu = am.get("levelUp") or {}
        off = lu.get("offered") or []
        if side == "" and profession < 0:
            # 只读：把左右选项亮出来让 AI 决策
            if not lu.get("isProfessionChooser"):
                return _with_state(f"🎉 升级到 {lu.get('skillName') or '?'} Lv{lu.get('level')}——普通升级自会确认OK，不用选分支")
            if len(off) < 2:
                return _with_state("🔀 职业选项还没就绪，稍等再试")
            return _with_state(
                f"🔀 选职业(Skill {lu.get('skillName')} Lv{lu.get('level')}): 左={off[0].get('name')}({off[0].get('id')}) 右={off[1].get('name')}({off[1].get('id')})。"
                f"想清楚后 → menu ops=levelup_choose side=left/right（或 profession={off[0].get('id')}/{off[1].get('id')}）")
        body = {}
        if profession >= 0:
            body["profession"] = int(profession)
        elif side in ("left", "right"):
            body["side"] = side
        else:
            return _with_state("⚠️ 用 side=left/right 或 profession=职业id（先看 menu ops=levelup_choose 不带参读选项）")
        r = api._post("/levelup_choose", body)
        if r.get("ok"):
            return _with_state(f"✅ 已选职业分支: {r.get('name')} (id {r.get('chosen')})")
        return _with_state(f"⚠️ {r.get('error') or '选职业失败'}")
    except Exception as e:
        return _with_state(f"❌ 选职业失败: {e}")


@mcp.tool()
def number_select(value: int = -1, confirm: bool = False) -> str:
    """🔢 数量输入菜单（NumberSelectionMenu）：写数量框 + 可确定。
    ⚠️ 星露谷展览会 50g换1星星币兑换台、转盘押注都弹它（对话完弹）。
    · 只读：number_select() → 返回框当前文本 + min/max/单价（AI 判断能押/换几个）
    · 设数量：number_select(value=N) → 写框（框每帧被游戏重读为 currentValue）
    · 填完点确定：number_select(value=N, confirm=True) → 写框 + 直接点 okButton
    · 取消：menu click(button=cancel)。
    Args:
        value: 要输入的数量（-1=只读不改）
        confirm: True=填完直接点确定（否则只写框，确定/取消由 AI 分步调）
    """
    try:
        _ensure_background()
        data = {}
        if value >= 0: data["value"] = value
        if confirm: data["confirm"] = True
        r = api.menu_number(value if value >= 0 else None, confirm)
        if not r.get("ok"):
            return _with_state(f"⚠️ {r.get('error', '数量输入失败')}")
        cur = r.get("currentValue", 0)
        price = r.get("price", -1)
        pstr = f" 单价{price}g" if price >= 0 else ""
        header = ""
        if r.get("changed"):
            header = f"✍️ 已设数量 {cur}" + (" ✅ 确定" if confirm else "")
        return _with_state(f"{header}🔢 数量框={r.get('text')} (min{r.get('min')}~max{r.get('max')}{pstr})")
    except Exception as e:
        return _with_state(f"❌ 数量输入失败: {e}")


def _fair_fishing_blocking() -> str:
    """🎣 阻塞跑 fair_fishing（等秋收钓鱼小游戏结束），返回 Star币结果行。
    2026-08-28 恒：异步后台时 AI 空转会调别的工具添乱（钓鱼最怕走位/开菜单）→ 改成菜单点击后**同步干等**，
    AI 处于"等待工具返回"状态、不能添乱；游戏结束当场拿回"钓 N 条 +X 星币"。"""
    import subprocess as _sp
    script = os.path.join(SCRIPT_DIR, "fair_fishing.py")
    args = [sys.executable, script, "--port", str(_ai_port())]
    try:
        proc = _sp.Popen(args, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True,
                         encoding="utf-8", errors="replace", cwd=SCRIPT_DIR,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        out, _ = proc.communicate(timeout=140)   # 游戏 ~100s + 结算，140s 上限
        for ln in out.splitlines():
            if "秋收钓鱼完成" in ln:
                return ln.strip()
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return lines[-1].strip() if lines else "（fair_fishing 无输出）"
    except Exception as e:
        return f"❌ fair_fishing 同步执行失败: {e}"


def _ice_fishing_blocking() -> str:
    """🎣 阻塞跑 ice_fishing（等冰雪节冰钓比赛自然结束），返回钓 N 条结果行。
    2026-08-28 恒：与秋收 fair_fishing 同理——异步后台时 AI 空转会调别的工具添乱（钓鱼最怕走位/ESC）→
    改成**同步干等**，AI 处于"等待工具返回"状态不能添乱；比赛结束当场拿"钓 N 条（赢线/不足5条）"。
    ⚠️ 比赛限时 2 分钟 + 走位/等开赛/结算 → 上限给足 210s（秋收 140s 装不下 2min 比赛）。"""
    import subprocess as _sp
    script = os.path.join(SCRIPT_DIR, "ice_fishing.py")
    args = [sys.executable, script, "--port", str(_ai_port())]
    try:
        proc = _sp.Popen(args, stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True,
                         encoding="utf-8", errors="replace", cwd=SCRIPT_DIR,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        out, _ = proc.communicate(timeout=210)   # 比赛~120s + 走位 + 结算，210s 上限
        for ln in out.splitlines():
            if "冰雪冰钓完成" in ln:
                return ln.strip()
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return lines[-1].strip() if lines else "（ice_fishing 无输出）"
    except Exception as e:
        return f"❌ ice_fishing 同步执行失败: {e}"


# 🎣 🚀 冰雪节冰钓自动 hook（2026-08-28 恒）：检测到比赛正式开钓(festivalTimer>0)且本局未跑过 → 自动阻塞跑 ice_fishing。
#    恒：手动点/AI调都有延迟（我开始晚/被误点），只有"检测到就立刻开钓"才能钓满2分钟 → 才赢。
#    只发一次（fired），比赛结束(festivalTimer<=0)复位；非冬8/非场地不触发。
_ICE_FISH_AUTO = {"fired": False}


def _maybe_ice_fishing_auto(data) -> str:
    """每个工具调用都跑一次：冰钓比赛刚开始(festivalTimer>0)且本局没发过 → 阻塞跑 ice_fishing 拿结果。
    ⚠️ 复用 _with_state 已抓好的 data（season/day/loc）判前置——**非冰雪节零额外 HTTP**（不添负担）；
       只在 冬8+冰雪节场地 才补读一次 festival_status（一年就这几天，可忽略）。只在真要开钓时阻塞2min。"""
    try:
        t = (data or {}).get("time") or {}
        if str(t.get("season") or "").lower() != "winter" or int(t.get("dayOfMonth") or 0) != 8:
            _ICE_FISH_AUTO["fired"] = False
            return ""
        loc = (data.get("location") or {}).get("name", "") or ""
        if loc not in ("Temp", "Forest-IceFestival"):
            return ""
        ft = int((api.festival_status() or {}).get("festivalTimer") or -1)
        if ft <= 0:
            _ICE_FISH_AUTO["fired"] = False     # 比赛结束/未开始 → 复位（下次再开赛可再触发）
            return ""
        if not _ICE_FISH_AUTO["fired"]:
            _ICE_FISH_AUTO["fired"] = True      # 只开一次，别每个工具调用都重跑
            return "🎣 " + _ice_fishing_blocking()
    except Exception:
        return ""
    return ""


def _maybe_egg_run_auto(data) -> str:
    """每个工具调用都跑一次：蛋蛋节寻宝刚开赛(festivalTimer>0)且本局没跑过 → 自动捡蛋/提醒。
    ⚠️ 复用 _with_state 已抓好的 data（season/day/loc）判前置——**非蛋蛋节零额外 HTTP**（不添负担）；
       只在 spring13+节日地图 才补读一次 festivalTimer（一年就这一天，可忽略）。只在真要捡蛋时阻塞。
    - 有 egg_note 小纸条 → 阻塞跑 _festival_egg_run()（AI 干等、不能调别的工具干扰；_festival_egg_run 循环捡到底）。
    - 没写纸条 → 只提醒游戏开始（fired 记一次，不再重复；让 AI 自己 festival eggs→egg_note→egg_run）。
    ⚠️ 阻塞用现有 _festival_egg_run（同步就地捡，不新起子进程）——一场寻宝只发生一次，可接受。"""
    try:
        t = (data or {}).get("time") or {}
        if str(t.get("season") or "").lower() != "spring" or int(t.get("dayOfMonth") or 0) != 13:
            _EGG_RUN_AUTO["fired"] = False
            return ""
        loc = (data.get("location") or {}).get("name", "") or ""
        if loc not in ("Temp", "Town-EggFestival", "Town-EggFestival2"):
            return ""
        ft = _festival_timer()
        if ft <= 0:
            _EGG_RUN_AUTO["fired"] = False     # 寻宝结束/未开始 → 复位（下次再开赛可再触发）
            return ""
        if _EGG_RUN_AUTO["fired"]:
            return ""
        _EGG_RUN_AUTO["fired"] = True          # 只开一次，别每个工具调用都重跑
        if _EGG_NOTE.get("route"):
            return "🥚 自动捡蛋：\n" + _festival_egg_run()
        return ("🥚 蛋蛋节寻宝开始了（倒计时跑起来了）！没写 egg_note 小纸条——AI 自己上："
                "festival eggs 看当年蛋坐标 → egg_note route=... 记路线 → egg_run 捡")
    except Exception:
        return ""


def menu_click(option: int = -1, button: str = "", x: int = -1, y: int = -1, item: str = "", right: bool = False, quantity: int = 1, action: str = "", real: bool = False, slot: int = -1) -> str:
    """🖱️ 自适应点击当前菜单（商店/背包/奖励）
    按菜单类型自动适配：
    - 商店菜单：item 买 N 个（quantity）· 免翻页
    - 背包菜单：action=split 拆 N 个 / action=discard 丢垃圾桶（自动找物品槽）
    - 奖励/箱子/接鱼菜单：item 领取 / slot 领指定格（read 看 items 序号）
    也支持：对话选 option、点按钮（close/ok/upArrow/downArrow/trashCan）、精确坐标 (x,y)、右键。

    Args:
        option: 对话选项序号（DialogueBox 用）
        button: 按钮名（close/ok/upArrow/downArrow/trashCan 等）
        x: 屏幕坐标 X（read_menu 返回的商品 bounds / 背包槽位坐标）
        y: 屏幕坐标 Y
        item: 物品名或 ID（商店=买/领取；背包配合 action）
        right: True=右键（商店买5个/背包拆1个）；False=左键（默认）
        real: True=对话选项走真实 receiveLeftClick 响应（createQuestionDialogue 用，如**跳舞邀请**——
              事件激活时默认走 event.answerDialogueQuestion 对跳舞无效；real=true 才点中真回调设舞伴）
        quantity: 批量数量（默认1；商店买 N 个 / 背包拆 N 个）
        action: 背包专用——split=拆堆叠取 N / discard=拿起后丢垃圾桶；奖励菜单=claim 领取
        slot: 领/点指定槽位序号（ItemGrabMenu 的 read items 下标，不想要1想要4就 slot=4；比坐标稳、不挪OS光标）
    """
    global _look_verified   # 🔒 捏人确认门禁：本函数会读+重置它（2026-08-22 恒）
    try:
        _ensure_background()
        # 🔒 捏人确认门禁（2026-08-22 恒）：CharacterCustomization 的 ok 必须先 confirm_look 核对，否则拒绝
        _cust_gate = False
        if (button or "").lower() == "ok":
            _mt = ((api.state().get("activeMenu") or {}).get("type") or "").lower()
            if _mt == "charactercustomization":
                _cust_gate = True
                if not _look_verified:
                    return _with_state(f"🚫 捏人未核对：先 settings ops=confirm_look（请{_host_name()}参谋 + screenshot 截图确认满意）→ 再点 ok。ok 后不可逆！")
        data = {}
        if option >= 0: data["option"] = option
        if button: data["button"] = button
        if x >= 0 and y >= 0: data["x"] = x; data["y"] = y
        if item: data["item"] = item
        if right: data["right"] = True
        if quantity != 1: data["quantity"] = quantity
        if action: data["action"] = action
        if real: data["real"] = True
        if slot >= 0: data["slot"] = slot
        r = api.menu_click(**data)
        if r.get("ok"):
            # 捏人窗 ok 提交成功（窗口消失=角色已确认）→ 自动退役捏脸/捏人工具（真拦截）
            if _cust_gate:
                _mt2 = ((api.state().get("activeMenu") or {}).get("type") or "").lower()
                if _mt2 != "charactercustomization":
                    # 🔒 2026-08-31 恒：不退役 set_appearance（幻觉神龛解锁后仍可改）；只退役 character_customize（起名/喜好一次性）。
                    #   set_appearance 由 C# /appearance 硬门禁拦——只在捏脸菜单(创建/幻神龛)开着时可用。
                    _retired_tools.add("character_customize")
                    _settings_save()
                    _look_verified = False
                    return _with_state("🖱️ 已点击（ok）→ 角色已确认。基相外观定型（想再改以后解锁幻觉神龛）；起名/喜好已定型。捏脸工具保留、只在捏脸菜单开时可用。")
            extra = f" x{r.get('quantity')}" if r.get("quantity") else ""
            # 🎣 秋收节钓鱼小游戏兜底（2026-08-28 恒）：点选"游戏（50金）"若起了 FishingGame → 自动后台兜底。
            # 触发点=点下去的结果（起了小游戏），不是匹配选项文字——游戏里只有秋收钓鱼会启动 FishingGame。
            # 最多等 ~3s 让小游戏注册（只确认"确实开了"，不用于判断时机）。fair_fishing.py 开头还会自查 minigame。
            # ⚠️ 2026-08-28 实测小游戏 ~1.5s 才注册，1.2s 窗会漏 → 提到 3s（纯确认触发，非轮询时机）。
            try:
                for _ in range(10):
                    time.sleep(0.3)
                    if (api.state().get("player") or {}).get("minigame") == "FishingGame":
                        # ⚠️ 2026-08-28 恒：防跑错场景——`FishingGame` 是**秋收节专属** minigame(图 fishingGame)，
                        #    冬钓大赛(森林,跟刘易斯对话推进)是 BobberBar 不是此 minigame，正常不会到这；
                        #    再保险一层：只认「正处 fishingGame 图」或「秋16」才兜，防误触发别的小游戏。
                        _t = api.state().get("time") or {}
                        _loc = (api.state().get("location") or {}).get("name", "") or ""
                        _is_fair = (str(_t.get("season") or "").lower() == "fall" and int(_t.get("dayOfMonth") or 0) == 16) \
                                   or _loc == "fishingGame"
                        if _is_fair:
                            # 🎣 阻塞——同步跑 fair_fishing 等小游戏钓完，拿结果返回。
                            #    异步会让 AI 空转乱调工具；阻塞则 AI 干等、不能添乱，结束当场拿"钓N条+X星币"。
                            _res = _fair_fishing_blocking()
                            return _with_state(f"🎣 钓鱼小游戏结束 → {_res}")
                        break
            except Exception:
                pass
            return _with_state(f"🖱️ 已点击（{r.get('clicked')}{extra}）")
        return _with_state(f"⚠️ {r.get('error', '点击失败')}")
    except Exception as e:
        return _with_state(f"❌ 点击失败: {e}")


@mcp.tool()
def minigame_click(action: str = "", x: int = -1, y: int = -1) -> str:
    """🎰 赌场小游戏（老虎机 Slots / 21点 CalicoJack）点按钮
    ⚠️ 游戏原生 Minigame（Game1.currentMinigame），**不是 activeClickableMenu**——/menu/click 对它无效，必须用这个。
    进入小游戏：bet 用 `scene ops=interact 赌场...` 开对话 → /menu click(option=0 开始) 进牌局 → 本工具点按钮。

    Args:
        action: 语义点名（推荐）——老虎机: bet10/bet100/done(退出)；21点: hit(加牌)/stand(停牌)/double/play_again/quit(退出)
        x: 不传 action 时的屏幕坐标 X（由游戏判是否落按钮内，bounds 是运行时缩放坐标）
        y: 屏幕坐标 Y
    """
    try:
        _ensure_background()
        if not action and (x < 0 or y < 0):
            return _with_state("🎰 小游戏点按钮：传 action（bet10/bet100/done/bet/hit/stand/quit）或坐标 x,y")
        data = {}
        if action: data["action"] = action
        if x >= 0 and y >= 0: data["x"] = x; data["y"] = y
        r = api.minigame_click(**data)
        if r.get("ok"):
            # 🆕 2026-08-23 恒：点完自动读牌局状态摘要，省得每次截图
            state = ""
            try:
                st = api.minigame_state()
                if st.get("minigame") == "CalicoJack":
                    # 🃏 21点：若点的 stand 且庄家还在补牌(showingResults=False) → 等结案再返回，省一次调用
                    if action == "stand" and not st.get("showingResultsScreen"):
                        for _ in range(15):
                            time.sleep(1.0)
                            st = api.minigame_state()
                            if st.get("showingResultsScreen"):
                                break
                    won = "· ✅你赢了" if st.get("playerWon") else ("· ❌庄家赢" if st.get("showingResultsScreen") else "")
                    state = f" 玩家{st.get('playerCards')} 庄家{st.get('dealerCards')} 下注{st.get('currentBet')}{won}"
                elif st.get("minigame") == "Slots":
                    state = f" 组合{st.get('slots')} 余额{st.get('clubCoins')}" + (" 🔄滚动" if st.get('spinning') else "")
            except Exception:
                pass
            return _with_state(f"🎰 已点 {r.get('clicked', '')}（{action or f'{x},{y}'}）· {r.get('minigame', '')}{state}")
        return _with_state(f"⚠️ {r.get('error', '小游戏点击失败')}")
    except Exception as e:
        return _with_state(f"❌ 小游戏点击失败: {e}")


@mcp.tool()
def minigame_state() -> str:
    """🎰 读当前赌场小游戏状态（不截图看牌面）
    ⚠️ 游戏原生 Minigame 需用这个（/menu 读不到）。点按钮用 minigame_click，本工具读现状。
    CalicoJack(21点)：playerCards=玩家点数 / dealerUp=庄家明牌 / currentBet=下注 / showingResultsScreen=结果屏 / playerWon=是否赢；
    Slots(老虎机)：slots=3转盘组合 / clubCoins=余额 / spinning=滚动中 / payoutModifier=赔率。
    """
    try:
        _ensure_background()
        st = api.minigame_state()
        if not st.get("ok"):
            return _with_state(f"⚠️ {st.get('error', '读小游戏失败')}")
        if not st.get("minigame"):
            return _with_state("🎰 当前没有小游戏")
        mg = st.get("minigame")
        if mg == "CalicoJack":
            pc = st.get("playerCards") or []; du = st.get("dealerUp"); bet = st.get("currentBet")
            res = "结果屏" if st.get("showingResultsScreen") else "牌局中"
            return _with_state(f"🎰 21点（{res}）· 玩家牌{pc} · 庄家明牌{du} · 下注{bet}🟣" + (" ・ 你赢了!" if st.get('playerWon') else ""))
        if mg == "Slots":
            return _with_state(f"🎰 老虎机 · 转盘{st.get('slots')} · 余额{st.get('clubCoins')}🟣 · 赔率{st.get('payoutModifier')}" + (" 🔄滚动中" if st.get('spinning') else ""))
        return _with_state(f"🎰 小游戏 {mg}")
    except Exception as e:
        return _with_state(f"❌ 读小游戏失败: {e}")


@mcp.tool()
def dance_invite(target: str = "") -> str:
    """💃 花舞节邀请跳舞（2026-08-21：正常端口已通，direct=true 退役）
    🧑 邀玩家（默认=房主恒）：走提案系统 team.SendProposal(Dance)，对方弹 "XX想和你跳舞" Yes/No 正常接受，
       接受后双方 dancePartner 自动配对（真机验证通过）。
    🧙 邀 NPC：**不走本工具**——站 NPC 紧邻格裸 /interact 弹「什么事？」→选「邀请XX作舞伴」（需4心+，
       对方正常接受/拒绝）。本工具只处理玩家提案。

    Args:
        target: 目标玩家名（默认=另一个在线玩家/房主）

    ⚠️ 时机：必须在舞会开始前（跟刘易斯对话/主环节 setUpFestivalMainEvent 烤死配对）调用；双方需已在花舞节地图。
    ⚠️ 发送后本方弹"等待对方"框，等对方应答自动消失（接受/拒绝都会自动关）。
    """
    try:
        _ensure_background()
        r = api.dance_invite(target=target)
        if r.get("ok"):
            note = r.get("note") or r.get("action")
            return _with_state(f"💃 {note}")
        return _with_state(f"⚠️ {r.get('error', '发送失败')}")
    except Exception as e:
        return _with_state(f"❌ 跳舞邀请失败: {e}")


FORGE_GUIDE = """🔨 火山锻造台（Caldera 顶层，menu forge 一键操作）
🪙 消耗：强化=宝石+火山晶石10/15/20（钻石只10，换随机多属性）；附魔=五彩碎片+20晶石；龙牙附魔=龙牙+10；无限武器=银河之魂×3（20晶石/次）；组合戒指/合并武器=20/10晶石
⚔️ 武器强化（宝石，最多3次）：紫水晶+1重量 / 海蓝宝石+4.6%暴击率 / 绿宝石+速度 / 翡翠+10%暴击力量 / 红宝石+10%攻 / 黄水晶+1防
✨ 附魔（五彩+20晶石，每件1个，覆盖旧的，记录最近2次不重复）：
   武器：巧妙(特殊动作cd-50%) / 虫子杀手(虫伤害×2) / 十字军(邪恶+50%) / 吸血(9%吸血) / 干草(杂草出纤维/干草)
   工具：自动上钩 / 考古学家 / 无底洞 / 省力 / 丰富 / 精通 / 强力 / 耐用 / 范围 / 木屑 / 迅捷 / 渔夫
🐉 龙牙附魔（低级非银河武器，与附魔叠加，宝石强化≤14级）：固有=史莱姆专杀/暴击威力/暴击率/攻击/速度；可能=防御/重量/史莱姆刮泥
∞ 无限武器：银河剑/锤/匕 用银河之魂锻造3次 → 无限之刃/锤/匕（继承强化）
💍 组合戒指：两个不同戒指→组合（同效果可叠，吸附/照明/防御不叠）；组合后不能再组合
⚠️ 取消锻造：左槽放装备→右下红x（武器清强化返晶石保留附魔；戒指还原两枚；工具不能取消）"""


@mcp.tool()
def forge(item1: str, item2: str = "", mode: str = "combine", target: int = 0) -> str:
    """🔨 锻造台一键操作（合成戒指 / 属性附魔 / 龙牙附魔 / 拆解）——2026-08-11 防呆重写
    自动开台（没开就 warp 到 Caldera 锻造台 22,21）→ 放料用 /forge_set 直接设槽（不碰鼠标，
    后台菜单点击放料不可靠：/state 背包陈旧会打错格）→ 锻造 → 领结果(放回背包) → 关菜单。
    默认操作 AI 自己的游戏（NAGI_URL）；target 指定端口可操作其他角色（如 7842 user/房主）。

    📖 附魔机制速览（2026-08-17 恒：怕 AI 不懂附魔）：
    - 强化(attribute)：宝石+火山晶石10/15/20，最多3次。紫水晶+1重量/海蓝宝石+4.6%暴击率/绿宝石+速度/
      翡翠+10%暴击力量/红宝石+10%攻/黄水晶+1防；钻石只10晶石换随机多属性
    - 附魔(random)：五彩碎片+20晶石，武器或工具，每件1个（覆盖旧的，记录最近2次不重复）。
      武器:巧妙/虫子杀手/十字军/吸血/干草；工具:自动上钩/考古学家/无底洞/省力/丰富/精通/强力/耐用/范围/木屑/迅捷/渔夫
    - 龙牙附魔(dragon)：龙牙+10晶石，低级非银河武器，与附魔叠加；宝石强化超15级则不可龙牙
    - 无限武器：银河之魂锻造3次（20晶石+1银河之魂/次）→ 银剑/银锤/银匕 → 无限版（继承强化）
    - 组合戒指(combine)：两个不同戒指→组合（同效果可叠，吸附/照明/防御不叠）；不能3合1
    - 合并武器：两把同类型武器（剑+剑等），继承左边属性+右边外观，10晶石
    - 取消锻造：左槽放装备→右下红x（武器清强化返晶石保留附魔；戒指还原；工具不能取消）

    Args:
        item1: 左槽物品（合成戒指=戒指1；附魔=武器；unforge=组合戒指）
        item2: 右槽物品（合成戒指=戒指2；属性附魔=宝石；龙牙附魔=龙牙；随机附魔=五彩碎片）
        mode: combine=合成戒指 / attribute=属性附魔(宝石) / random=随机附魔(五彩) /
              dragon=龙牙附魔(龙牙,低级非银河武器) / unforge=拆解组合戒指
        target: 目标端口（0=用 NAGI_URL 默认；7842=user/房主；7843=AI）
    """
    import requests as _req
    import os as _os
    if target:
        base = f"http://localhost:{target}"
    else:
        base = _os.environ.get("NAGI_URL", "http://localhost:7843")
    _ensure_background(base)

    def _click(x, y):
        return _req.post(f"{base}/menu/click", json={"x": x, "y": y}, timeout=8).json()

    def _state():
        return _req.get(f"{base}/state", timeout=8).json()

    def _inv():
        return [it for it in _state().get("inventory", []) if it.get("slotIndex") is not None]

    def _menu():
        m = _req.post(f"{base}/menu", json={}, timeout=8).json()
        return m, {b.get("field"): b for b in (m.get("buttons") or [])}

    def _free_slot():
        occ = set(it.get("slotIndex") for it in _inv())
        return next((i for i in range(36) if i not in occ), 36)

    try:
        # ── 开台：没有 ForgeMenu 就清残留 → warp 到锻造台 → 打开 ──
        m, fb = _menu()
        if not m.get("open") or m.get("type") != "ForgeMenu":
            _menu_close(base)
            time.sleep(0.5)
            _req.post(f"{base}/warp", json={"location": "Caldera", "x": 22, "y": 23}, timeout=8)
            for _ in range(20):
                if _state().get("location", {}).get("name") == "Caldera":
                    break
                time.sleep(0.3)
            _req.post(f"{base}/interact", json={"x": 22, "y": 21}, timeout=8)
            time.sleep(1.0)
        m, fb = _menu()
        if not m.get("open") or m.get("type") != "ForgeMenu":
            return _with_state("❌ 锻造台没开（Caldera(22,21) interact_at 失败）")
        left = fb.get("leftIngredientSpot")
        right = fb.get("rightIngredientSpot")
        fbtn = fb.get("startTailoringButton")
        result_btn = fb.get("craftResultDisplay")
        ubtn = fb.get("unforgeButton")
        if not (left and right and fbtn):
            return _with_state("❌ 读不到锻造槽位，关掉重开菜单试试")

        # ── 放料：/forge_set 直接设槽（2026-08-11 后台菜单点击放料不可靠——
        #    /state 背包读取陈旧会打错格；改主线程直接设槽，不碰鼠标） ──
        set_data = {"left": item1}
        if item2:
            set_data["right"] = item2
        fs = _req.post(f"{base}/forge_set", json=set_data, timeout=8).json()
        if not fs.get("ok"):
            return _with_state(f"❌ 放料失败: {fs.get('error')}")
        time.sleep(0.8)

        # ── 拆解 ──
        if mode == "unforge":
            if not ubtn:
                return _with_state("❌ 读不到 unforge 按钮（拆解）")
            _click(ubtn["x"], ubtn["y"])
            time.sleep(2.0)
            _menu_close(base)
            return _with_state("💍 解除合成完成！组件已放回背包")

        # ── 锻造 ──
        _click(fbtn["x"], fbtn["y"])
        time.sleep(2.5)

        # ── 领取结果：点 result 槽拿起 → menu_close 自动放回背包（2026-08-11 实测：
        #    关菜单会把光标物品 CollectOrDrop 回背包，不用再点空槽） ──
        if result_btn:
            _click(result_btn["x"], result_btn["y"])
            time.sleep(0.6)
        mc = _menu_close(base)  # 关菜单=自动放回背包
        time.sleep(0.6)

        # 验证：结果进背包了（附魔=武器回来；合成=Combined Ring）
        names = [it.get("name", "") for it in _inv()]
        back = any(item1.lower() in n.lower() for n in names)
        if not back and mode != "combine":
            return _with_state("⚠️ 结果可能没进背包（光标被关菜单处理了？检查背包）")
        if mode == "combine" and not any("Combined" in n for n in names):
            return _with_state("⚠️ 合成结果没进背包（检查背包）")
        tag = {"combine": "💍 合成戒指", "attribute": "⚔️ 属性附魔",
               "random": "🔮 随机附魔", "dragon": "🐉 龙牙附魔"}.get(mode, mode)
        return _with_state(f"{tag}完成！结果已放回背包")
    except Exception as e:
        try: _menu_close(base)
        except Exception: pass
        return _with_state(f"❌ 锻造失败: {e}")


@mcp.tool()
def sell_to_shop(name: str, count: int = -1) -> str:
    """💰 卖出物品到当前打开的商店菜单
    需要先走到商店柜台前打开商店菜单。
    ⚠️ SDV 商店卖=单击卖整个堆叠（count 参数不生效，永远整组卖）。
    想只卖一部分：先 chest_take(N) 把 N 个取出来，再整组卖。

    Args:
        name: 物品名称（如 Blueberry、Ancient Fruit）
        count: 保留参数（游戏层面整组卖，传多少都卖整组）
    """
    try:
        _ensure_background()  # 商店卖=真实菜单交互，先确保不冻结
        r = api._post("/sell_to_shop", {"name": name, "count": count})
        if r.get("ok"):
            sold = r.get("sold", [])
            detail = ", ".join(f"{s['item']}x{s['sold']} ({s['totalPrice']}g)" for s in sold)
            return _with_state(
                f"💰 已卖出: {detail}\n"
                f"   💳 剩余 {r.get('remainingGold', '?')}g"
            )
        else:
            return _with_state(f"❌ 卖出失败: {r.get('error', '未知错误')}")
    except Exception as e:
        return _with_state(f"❌ 卖出出错: {e}")


@mcp.tool()
def sell_to_bin(name: str = "", sell_all: bool = False) -> str:
    """📦 投放到出货箱
    自动走到出货箱旁边，然后投放物品（明天拿钱）。
    不指定名称则卖所有非工具/种子类物品。
    必须站在农场地图。

    Args:
        name: 物品名（不指定则卖所有可卖物品）
        sell_all: 是否全部卖出（默认 False，只卖指定物品）
    """
    try:
        # 动态找出货箱位置
        s = api.state()
        loc = s.get("location", {}).get("name", "")
        if loc != "Farm":
            api.walk_to_coord("Farm", 64, 15)
            time.sleep(1)

        map_info = api.map_data()
        bins = [b for b in map_info.get("buildings", []) if b.get("type") == "Shipping Bin"]
        if bins:
            bin_door_x = bins[0].get("doorX", bins[0]["x"])
            bin_door_y = bins[0].get("doorY", bins[0]["y"])
            api.walk_natural(bin_door_x, bin_door_y)
            time.sleep(0.3)

        r = api._post("/sell", {"name": name, "all": sell_all})
        if r.get("ok"):
            sold = r.get("sold", [])
            if not sold:
                return _with_state("📦 出货箱: 没有可卖的东西")
            detail = ", ".join(f"{s['item']}x{s['count']} ({s['price']}g)" for s in sold[:8])
            if len(sold) > 8:
                detail += f" 等共 {r['totalItems']} 种"
            return _with_state(f"📦 已投放 {r['totalItems']} 种物品到出货箱\n   {detail}")
        else:
            return _with_state(f"❌ 出货失败: {r.get('error', '未知错误')}")
    except Exception as e:
        return _with_state(f"❌ 出货出错: {e}")


# ═══════════════════════════════════════════
#  烹饪系统
# ═══════════════════════════════════════════

@mcp.tool()
def cook(recipe_name: str, count: int = 1) -> str:
    """🍳 烹饪 — 制作料理
    检查是否已学习该食谱、背包是否有所需食材，然后制作。
    使用游戏自带的制作系统，支持批量制作。

    用 list_recipes() 查看所有已学食谱和当前可用食材。

    常见食谱:
    - Fried Egg, Omelet, Salad, Bread, Tortilla
    - Pizza, Pasta, Hashbrowns, Pancakes
    - Fish Taco, Maki Roll, Sashimi
    - Pumpkin Soup, Eggplant Parmesan, Cranberry Candy
    - 等等（取决于你学了多少食谱）

    Args:
        recipe_name: 食谱名称（英文原名）
        count: 制作数量（默认 1）
    """
    try:
        r = api.cook(recipe_name, count)
        if r.get("ok"):
            crafted = r["crafted"]
            msg = f"🍳 已制作「{recipe_name}」x{crafted}"
            if r.get("warning"):
                msg += f"\n⚠️ {r['warning']}"
            if r.get("missing"):
                details = ", ".join(f"{k}还需{v}" for k, v in r["missing"].items())
                msg += f"\n📝 缺材料: {details}"
            return _with_state(msg)
        else:
            msg = f"❌ 烹饪失败: {r.get('error', '未知')}"
            if r.get("missing"):
                details = ", ".join(f"{k}缺{v}" for k, v in r["missing"].items())
                msg += f"\n📝 缺少材料: {details}"
            if r.get("knownRecipes"):
                msg += f"\n📋 已学食谱: {', '.join(r['knownRecipes'][:10])}{'…' if len(r['knownRecipes']) > 10 else ''}"
            return _with_state(msg)
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def craft(item_name: str, count: int = 1) -> str:
    """🔨 制作物品（通用合成，非烹饪）——直接 C# 合成，不碰背包菜单
    用游戏自带 CraftingRecipe 系统，**遵守技能等级规则**（只造已学配方，
    farmer.craftingRecipes 里没有的配方会拒绝）。适合做梯子/熏鱼机/洒水器等。

    Args:
        item_name: 合成物品名（英文原名，如 Staircase / Bee House / Scarecrow）
        count: 数量（默认 1）

    提示: 不知道能造啥先调 list_craftables() 看已学合成配方 + 材料齐不齐。
    """
    try:
        r = api.craft(item_name, count)
        if r.get("ok"):
            crafted = r["crafted"]
            msg = f"🔨 已制作「{item_name}」x{crafted}"
            if r.get("warning"):
                msg += f"\n⚠️ {r['warning']}"
            if r.get("missing"):
                details = ", ".join(f"{k}还需{v}" for k, v in r["missing"].items())
                msg += f"\n📝 缺材料: {details}"
            return _with_state(msg)
        msg = f"❌ 合成失败: {r.get('error', '未知')}"
        if r.get("missing"):
            details = ", ".join(f"{k}缺{v}" for k, v in r["missing"].items())
            msg += f"\n📝 缺少材料: {details}"
        if r.get("knownRecipes"):
            msg += f"\n📋 已学合成配方: {', '.join(r['knownRecipes'][:10])}{'…' if len(r['knownRecipes']) > 10 else ''}"
        return _with_state(msg)
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def list_craftables() -> str:
    """📋 列出已学合成配方（🔨 通用合成，非烹饪；遵守技能等级规则）
    返回所有已学合成配方，标注材料需求 + 背包现有量 + canMake（材料齐不齐）。
    想知道能造啥/造什么先调这个，然后 craft(name) 造。

    Args:
        无
    """
    try:
        r = api._get("/craft_recipes")
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', '获取失败')}")
        recipes = r.get("recipes", [])
        if not recipes:
            return _with_state("📋 还没学任何合成配方（技能等级不够）——升级技能/学配方后再看")
        can_make = [re for re in recipes if re.get("canMake")]
        cannot = [re for re in recipes if not re.get("canMake")]
        lines = [f"📋 已学合成配方 {r['count']} 个（技能解锁）:"]
        if can_make:
            lines.append(f"\n✅ 材料齐备（{len(can_make)}）:")
            for re in can_make[:15]:
                lines.append(f"  🔨 {re['product']}（{re['name']}）")
        if cannot:
            lines.append(f"\n❌ 缺材料（{len(cannot)}）:")
            for re in cannot[:15]:
                lines.append(f"  🔨 {re['product']}")
        if len(can_make) > 15 or len(cannot) > 15:
            lines.append("…（清单较长，需要时按名查）")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


@mcp.tool()
def list_recipes() -> str:
    """📋 列出已学的烹饪食谱
    返回所有已学食谱，标注每种食谱的食材需求和背包里的现有量。
    canMake=True 表示当前食材够做的。
    """
    try:
        r = api.list_recipes()
        if not r.get("ok"):
            return f"❌ {r.get('error', '获取失败')}"

        recipes = r.get("recipes", [])
        if not recipes:
            return _with_state("📋 还没有学任何烹饪食谱 🍳\n去跟村民搞好关系或者看电视学吧！")

        can_make = [re for re in recipes if re.get("canMake")]
        cannot = [re for re in recipes if not re.get("canMake")]

        lines = [f"📋 共 {r['count']} 个食谱:"]

        if can_make:
            lines.append(f"\n✅ 材料齐备（{len(can_make)} 个）:")
            for re in can_make:
                lines.append(f"  🍳 {re['product']} ({re['name']})")
                for ing in re.get("ingredients", []):
                    status = "✅" if ing["have"] >= ing["needed"] else "❌"
                    lines.append(f"    {status} {ing['name']} x{ing['needed']} (有{ing['have']})")

        if cannot:
            lines.append(f"\n❌ 缺材料（{len(cannot)} 个）:")
            for re in cannot[:10]:
                lines.append(f"  · {re['product']} ({re['name']})")
            if len(cannot) > 10:
                lines.append(f"  …还有 {len(cannot) - 10} 个")

        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ {e}")


# ═══════════════════════════════════════════
#  脚本运行器
# ═══════════════════════════════════════════

def run_script(name: str, args: str = "") -> str:
    """🤖 运行 scripts/ 下的自动化脚本（短任务同步；长任务→script start 后台，别同步等）。常用：farm_row(耕种) / water_crops(浇) / harvest(收) / chop_trees(砍树) / clear_area(清杂) / pet_animals(摸动物) / shop_buy(购物)。全清单→help(scripts)。

    Args:
        name: 脚本名（不含 .py）
        args: 命令行参数字符串（如 "--x 60 --y 14 --rows 5"）
    """
    script_path = os.path.join(SCRIPT_DIR, f"{name}.py")
    if not os.path.exists(script_path):
        return _with_state(f"❌ 脚本不存在: {name}.py\n可用: {', '.join(_list_scripts())}")

    # 解析 args 字符串成列表
    import shlex
    try:
        arg_list = shlex.split(args) if args else []
    except Exception:
        arg_list = args.split()

    out = _run_script(name, arg_list, timeout=120)
    return _with_state(f"🤖 脚本「{name}」{' '.join(arg_list)}:\n{out[:800]}")


def _list_scripts() -> list:
    """列出 scripts/ 目录下可用的 .py 脚本（不包括本文件和工具类）。"""
    exclude = {"nagi_mcp_server", "stardew_api", "tool_agent", "locations", "channel_server",
               "chat_overlay", "chat_watcher", "bomb_common", "_bomb_selftest"}
    scripts = []
    for f in os.listdir(SCRIPT_DIR):
        if f.endswith(".py") and f[:-3] not in exclude:
            scripts.append(f[:-3])
    return sorted(scripts)


# ═══════════════════════════════════════════
#  🚀 后台脚本任务（B1 异步，2026-08-14）
#  核心目的（恒 08-14 澄清）：脚本跑的时候 AI 还能聊天/看状态/整理背包，
#  不打断脚本——script_start 立即返回 job_id，AI 随时 script_status 查进度。
#  值得异步的长任务：bomb_mine / bomb_escort / go_fishing / 拟人浇水。
#  短任务继续用同步 run_script。
# ═══════════════════════════════════════════
import threading as _threading

_BG_OUTPUT_MAX = 400          # 每个任务保留最近 400 行输出
_BG_MAX_FINISHED = 5          # 最多留几个已完成任务（防内存膨胀）
_bg_jobs = {}                 # job_id -> _BgJob
_bg_seq = 0
_bg_lock = _threading.Lock()


class _BgJob:
    """一个后台脚本任务：Popen 子进程 + 读线程 + 滚动输出缓冲。"""

    def __init__(self, name: str, args_list: list):
        global _bg_seq
        _bg_seq += 1
        self.job_id = f"job{_bg_seq}"
        self.name = name
        self.args = args_list
        self.proc = None
        self.start_ts = time.time()
        self.end_ts = None
        self.returncode = None
        self.running = False      # 读线程 EOF 后置 False
        self.output = []
        self.reader = None

    def _tail(self, n: int = 200) -> str:
        return "\n".join(self.output[-n:])


def _bg_reader(job: "_BgJob"):
    """读线程：逐行追加到 job.output（bound），进程退出后置 running=False。"""
    try:
        for line in iter(job.proc.stdout.readline, ""):
            job.output.append(line.rstrip("\n"))
            if len(job.output) > _BG_OUTPUT_MAX:
                del job.output[: len(job.output) - _BG_OUTPUT_MAX]
    except Exception:
        pass
    finally:
        try:
            job.proc.stdout.close()
        except Exception:
            pass
        # Windows 上管道 EOF 可能比进程完全退市早一拍，poll() 会返回 None
        # → 用 wait() 收尸（进程已退出，基本立即返回），收不到再 poll()
        try:
            job.proc.wait(timeout=5)
        except Exception:
            pass
        job.returncode = job.proc.poll()
        job.end_ts = time.time()
        job.running = False


def _bg_start(name: str, args_list: list):
    """后台启动一个脚本，返回 (job, None) 或 (None, 错误提示)。
    ⚠️ 2026-08-16：和 _run_script 同步路径一致，_PORT_SCRIPTS 自动注入 --port AI端口——
      否则异步跑长脚本会默认打 host 7842（挪恒角色）。便利工具已显式传的可跳过。"""
    global _bg_jobs
    # ⚠️ _PORT_SCRIPTS 注入 --port（复用 _run_script 同步路径逻辑，防异步漏端口）
    try:
        if name in _PORT_SCRIPTS and not any(a == "--port" or a.startswith("--port=") for a in args_list):
            args_list = list(args_list) + ["--port", str(_ai_port())]
    except Exception:
        pass
    with _bg_lock:
        # 单槽：一次只跑一个脚本（两个脚本同时打游戏会互相打架）
        for j in _bg_jobs.values():
            if j.running:
                return None, (f"⛔ 已有脚本「{j.name}」在跑（job {j.job_id}，"
                              f"已跑{int(time.time()-j.start_ts)}s）。先 script(ops=\"stop\") 再启动新的。")
        script_path = os.path.join(SCRIPT_DIR, f"{name}.py")
        if not os.path.exists(script_path):
            return None, f"❌ 脚本不存在: {name}.py"
        try:
            proc = subprocess.Popen(
                [sys.executable, script_path] + args_list,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=SCRIPT_DIR,
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
            )
        except Exception as e:
            return None, f"❌ 启动失败: {e}"
        job = _BgJob(name, args_list)
        job.proc = proc
        job.running = True
        job.reader = _threading.Thread(target=_bg_reader, args=(job,), daemon=True)
        job.reader.start()
        _bg_jobs[job.job_id] = job
        # 清理已完成任务（超过上限，丢最旧的）
        finished = sorted((j for j in _bg_jobs.values() if not j.running),
                          key=lambda j: (j.end_ts or 0))
        for old in finished[:-_BG_MAX_FINISHED]:
            _bg_jobs.pop(old.job_id, None)
        return job, None


def _bg_job_display(job: "_BgJob") -> str:
    if job.running:
        elapsed = int(time.time() - job.start_ts)
        return f"🟢 {job.job_id} 「{job.name} {' '.join(job.args)}」运行中 {elapsed}s"
    rc = job.returncode
    dur = int((job.end_ts or time.time()) - job.start_ts)
    mark = "✅" if rc == 0 else ("💀" if rc is None else "⚠️")
    return f"{mark} {job.job_id} 「{job.name} {' '.join(job.args)}」结束({dur}s) 返回码 {rc}"


def _bg_activity_line() -> str:
    """异步脚本运行期间的状态条提醒（按 wake_interval 限频，仿心跳系统）。
    返回空串=不提醒。interval=0 表示每次都提醒。
    ⚠️ 2026-08-16 恒：AI 正在连续操作（上次工具调用 < interval，如整理背包/连续查状态）时不提醒——
      异步唤醒不打断 AI 正在做的事，等 AI 空闲超过 interval 才重新计时。"""
    global _bg_last_wake
    if not _bg_cfg.get("enabled", True):
        return ""
    with _bg_lock:
        active = [j for j in _bg_jobs.values() if j.running]
        if not active:
            return ""
        job = active[0]
    now = time.time()
    interval = int(_bg_cfg.get("wake_interval", 60))
    # ⚠️ AI 活跃时重置计时：上次 AI 操作距今 < interval → 正在连续操作，不提醒（不打断）
    if interval > 0 and _bg_last_ai_activity and now - _bg_last_ai_activity < interval:
        _bg_last_wake = now   # 重置限频：AI 活跃期间不累计"该提醒了"
        return ""
    if interval > 0 and now - _bg_last_wake < interval:
        return ""
    _bg_last_wake = now
    elapsed = int(now - job.start_ts)
    # 🚫 2026-08-17：原 plan_hint（计划任务标注）已随计划模式退役移除。
    try:
        _host = _host_name()   # 动态读 host 玩家名，不写死"恒"
    except Exception:
        _host = "host"
    return (f"⏰ 异步唤醒时间——脚本「{job.name}」后台运行中（{elapsed}s，job {job.job_id}）\n"
            f"   ✅ 可做（不打断脚本）: 整理背包 / 查状态看事项 / 跟{_host}聊天 / 发表情 / 截图观察\n"
            f"   ⛔ 别做（会和脚本打架）: 走位 / 挥工具 / 开商店等强菜单（查邮箱要走去信箱=走位，也算）\n"
            f"   → 要控制权: script(ops=\"stop\")")


def script_start(name: str, args: str = "") -> str:
    """🚀 后台启动脚本（异步不阻塞，返回 job_id；长任务用，AI 可继续聊天/看状态）。查 script status(job_id)、停 script stop(job_id)。⚠️一次只跑一个；跑时别用走动/挥工具同步工具，轻操作(聊天/看状态/开背包)没问题。用法→help(scripts)。

    Args:
        name: 脚本名（不含 .py）
        args: 命令行参数字符串（如 "--target 80"）
    """
    import shlex
    try:
        arg_list = shlex.split(args) if args else []
    except Exception:
        arg_list = args.split()
    # 🚫 2026-08-17：原"计划执行中不能开脚本"检查已随计划模式退役移除。
    if not _bg_cfg.get("enabled", True):
        # 异步开关关闭 → 退回同步执行（等价 run_script，AI 会等脚本跑完）
        out = _run_script(name, arg_list, timeout=120)
        return _with_state(f"⚠️ 异步已关闭（settings async on 可开），本次同步执行:\n{out[:800]}")
    job, err = _bg_start(name, arg_list)
    if err:
        return _with_state(err)
    return _with_state(
        f"🚀 后台启动脚本「{name} {' '.join(arg_list)}」→ job {job.job_id}\n"
        f"  查进度: script(ops=\"status\", kw={{\"job_id\":\"{job.job_id}\"}})   停止: script(ops=\"stop\", kw={{\"job_id\":\"{job.job_id}\"}})")


def script_status(job_id: str = "") -> str:
    """📊 查询后台脚本任务状态
    传空 job_id：列出所有任务 + 最近一个（在跑的优先）的输出尾巴。
    返回：是否运行中/耗时/返回码/滚动输出（最近 N 行）。

    Args:
        job_id: script start 返回的任务ID；空 = 看全部+最近的
    """
    with _bg_lock:
        if not _bg_jobs:
            return _with_state("📭 没有后台脚本任务。用 script(ops=\"start\", kw={name,args}) 启动一个。")
        if job_id:
            job = _bg_jobs.get(job_id)
            if not job:
                return _with_state(f"❌ 找不到任务 {job_id}。现有: {', '.join(_bg_jobs)}")
        else:
            active = [j for j in _bg_jobs.values() if j.running]
            job = active[0] if active else sorted(
                _bg_jobs.values(), key=lambda j: (j.end_ts or j.start_ts))[-1]
        info = _bg_job_display(job)
        tail = job._tail(120)
    body = info
    if tail:
        body += f"\n\n── 输出（最近 {job.name}）──\n{tail[-2000:]}"
    return _with_state(body)


def script_stop(job_id: str = "") -> str:
    """🛑 停止后台脚本任务
    终止进程（terminate → 等 3s → 不行就 kill）。不传 job_id 停最近一个在跑的。

    Args:
        job_id: script start 返回的任务ID
    """
    with _bg_lock:
        if not _bg_jobs:
            return _with_state("📭 没有后台脚本任务。")
        if job_id:
            job = _bg_jobs.get(job_id)
            if not job:
                return _with_state(f"❌ 找不到任务 {job_id}。")
        else:
            running = [j for j in _bg_jobs.values() if j.running]
            if not running:
                return _with_state("没有在跑的脚本（现有任务都结束了）。")
            job = running[-1]
    if not job.running:
        return _with_state(f"任务 {job.job_id} 已结束（返回码 {job.returncode}），无需停止。")
    _bg_kill(job)
    last = job._tail(60)
    body = f"🛑 已停止任务 {job.job_id} 「{job.name}」。"
    # 🚫 2026-08-17：原"计划当前任务→暂停"逻辑已随计划模式退役移除。
    if last:
        body += f"\n── 最后输出 ──\n{last[-1000:]}"
    return _with_state(body)


# ═══════════════════════════════════════════
#  📜 脚本/异步域（2026-09-02 恒：run_script/script_start/status/stop/async_config 五合一并入此域）
#   核心=【被动异步】：便利工具白名单自动后台 + async 开关；AI 主要用 status/stop 管理、async 调白名单。
#   start(主动后台)是兜底——长脚本优先交给便利工具(白名单自动后台)，AI 别主动手动后台。
#   坑：ops 按空格拆成多个 op，脚本名/任务id/参数须放 kw（如 script(ops="start", kw={name,args})）。
# ═══════════════════════════════════════════
def _script_run(name: str = "", args: str = ""):
    return run_script(name, args)


def _script_start(name: str = "", args: str = ""):
    return script_start(name, args)


def _script_status(job_id: str = ""):
    return script_status(job_id)


def _script_stop(job_id: str = ""):
    return script_stop(job_id)


def _script_async(show: bool = False, add: str = "", remove: str = "", enable: str = ""):
    return async_config(show, add, remove, enable)


@mcp.tool()
def script(ops: str = "", kw: dict | None = None) -> str:
    """🚀 脚本/异步域。ops:
    status(查进度,job_id空=看全部+最近) stop(停任务,job_id空=停最近在跑) async(自动异步白名单 show/add/remove/enable)
    run(短任务同步 name=脚本名 args=参数) start(主动后台兜底 name,args→job_id)。
    ⚠️长任务(bomb_mine/炸矿/钓鱼/挖矿)便利工具**自动后台**，别手动 start(白名单自动异步即可)；跑脚本时别用走位/挥工具,轻操作(聊天/看状态/开背包)没关系。①一次只跑一个脚本；②短任务用 run 别 start。
    Args:
        ops: 动作（run/start/status/stop/async，空格可连跑多个）
        kw: 参数——run/start 的 {name,args}；async 的 {show/add/remove/enable}；status/stop 的 {job_id}。⚠️参数必须放 kw，别拼进 ops。
    """
    dispatch = {
        "run": _script_run, "跑": _script_run,
        "start": _script_start, "后台": _script_start, "开": _script_start,
        "status": _script_status, "查": _script_status, "进度": _script_status,
        "stop": _script_stop, "停": _script_stop,
        "async": _script_async, "异步": _script_async, "自动": _script_async, "白名单": _script_async,
    }
    return _with_state(_ops_run(ops, dispatch, kw))


# ═══════════════════════════════════════════
#  🛋️ 计划模式（2026-08-14 全自动一天）—— 🚫 已退役（2026-08-17 恒：计划模式暂不实现）
#
#  ⚠️⚠️ 以下是【存档代码】，整体保留仅供 git 回溯/将来恢复，不再启用： ⚠️⚠️
#   1. `plan` 工具已取消注册（去掉 @mcp.tool()，AI 看不到）
#   2. settings mode=plan 已失效（_mode_cfg 恒 autonomous）
#   3. _autopilot_loop 只跑 🌙 兜底睡觉，计划状态机分支不触发（state 恒 idle）
#   4. 状态条/结算/脚本拦截里的计划逻辑已全部移除
#   若将来恢复：重新注册 plan 工具 + settings mode 放行 + 启动时 _plan_load_state() 即可。
#   🌙 兜底自动睡觉（凌晨1点 go_sleep）是独立功能，不在退役范围，继续工作。
#
#  旧版设计说明（存档）：
#  状态机：mode ∈ {plan, autonomous}（settings.json）；state ∈
#  {idle, waiting_day, running, paused, done, aborted}（plan.json）
#  调度器 = 后台 daemon 线程 _autopilot_loop（~10s tick）：
#    waiting_day → 日翻转+清晨就绪 → running → 逐任务 _bg_start
#    任务结局：success/time→下一个；recoverable/unknown→paused(可恢复，AI plan resume)；
#              fatal→aborted+回自主；节日开始→停脚本+warp安全位+paused；
#  通知全部走 _plan_notices 缓冲 → _with_state 注入到下次工具返回
# ═══════════════════════════════════════════

_PLAN_TICK = 10              # 调度器轮询间隔（秒）（存档：仅供兜底 loop 用）
_PLAN_TIME_MARGIN = 30       # 任务 until 前多少游戏分钟还够跑？（存档）
_plan_data = plan_engine.new_plan()   # 存档：计划数据（state 恒 idle，不参与执行）
_plan_lock = _threading.Lock()
_plan_notices = []           # 计划/兜底事件 → 注入给 AI 的缓冲（兜底睡觉通知仍走这）
_fallback_busy = False       # 兜底 go_sleep 进行中防重入
_fallback_last_fail_ts = 0.0 # 兜底爬床失败时间（冷却用，防反复杀脚本+乱跑）
_FALLBACK_FAIL_COOLDOWN = 300  # 爬床失败后冷却秒数（5 分钟）——失败不重试，避免反复打断脚本


def _bg_kill(job):
    """终止后台脚本进程（terminate → 等3s → 不行 kill）。"""
    try:
        job.proc.terminate()
        job.proc.wait(timeout=3)
    except Exception:
        try:
            job.proc.kill()
        except Exception:
            pass


def _plan_save_state():
    plan_engine.save_plan(_plan_data)


def _plan_load_state():
    """服务启动时恢复 plan.json。上次 running（服务中途挂了）→ 置 aborted 避免半途重跑。"""
    global _plan_data
    with _plan_lock:
        _plan_data = plan_engine.load_plan()
        if _plan_data.get("state") == "running":
            _plan_data["state"] = "aborted"
            _plan_data["abort_reason"] = "服务器重启（上次计划执行中断）"
            _plan_save_state()
            _plan_notices.append("🔄 上次计划执行因服务器重启中断，已中止——plan show 看现状，plan write 重排")


def _plan_notify(msg: str):
    with _plan_lock:
        _plan_notices.append(msg)
        if len(_plan_notices) > 20:
            del _plan_notices[:len(_plan_notices) - 20]


def _plan_drain_notices() -> str:
    with _plan_lock:
        if not _plan_notices:
            return ""
        out = "\n".join(_plan_notices)
        _plan_notices.clear()
    return out


def _plan_describe(tasks) -> str:
    """任务清单展示。"""
    icons = {"pending": "⏳", "running": "🟢", "done": "✅", "failed": "❌", "skipped": "⏭️"}
    lines = []
    for i, t in enumerate(tasks, 1):
        arg = " ".join(t.get("args", []))
        lim = f" until {t['until']}" if t.get("until") else ""
        st = icons.get(t.get("status", "pending"), "⏳")
        note = f"（{t['note']}）" if t.get("note") else ""
        lines.append(f"  {st} {i}. {t['script']} {arg}{lim}{note}")
    return "\n".join(lines) if lines else "  （空）"


def _plan_status_line() -> str:
    """状态条一行：🚫 计划模式已退役（2026-08-17 恒）——不再显示计划状态。
    只保留 🌙 兜底睡觉进行中的提示（独立功能，仍工作）。"""
    try:
        if _fallback_busy:
            return "  🌙 兜底睡觉进行中…"
    except Exception:
        pass
    return ""


def _plan_pack_reminder(tasks) -> str:
    """按任务脚本生成"带够背包物品"提醒（2026-08-14 恒补需求：写计划时提醒带够工具/食物/炸弹）。"""
    need = set()
    for t in tasks or []:
        s = t.get("script", "")
        if s in ("bomb_mine", "bomb_escort", "bomb_volcano"):
            need.add("💣 炸弹 + 🍱 回血食物")
        elif s == "fish_run":
            need.add("🎣 鱼竿 + 鱼饵")
        elif s == "mine_run":
            need.add("⛏️ 镐子 + 🍱 食物")
        elif s == "water_crops":
            need.add("💧 满水壶")
        elif s == "chop_trees":
            need.add("🪓 斧头")
        elif s in ("farm_row", "harvest"):
            need.add("🌱 种子（种田）或 🛠️ 工具（收获）")
        elif s == "clear_area":
            need.add("🛠️ 工具")
        elif s == "scythe_crops":
            need.add("🔪 镰刀")
    if not need:
        return ""
    return "🧳 提醒：明天计划要带 " + "、".join(sorted(need)) + "——今晚就装进背包（或 plan start 前确认），计划开跑不会等你整理"


# ── plan 域子操作 ──

def _plan_write(spec: str = "") -> str:
    tasks, errors = plan_engine.parse_spec(spec)
    ok, msgs = plan_engine.validate_tasks(tasks)
    lines = []
    for e in errors:
        lines.append(e)
    if not ok:
        lines.append("❌ 计划有拒收项，没保存——改好再 plan write：")
        lines.extend(f"  {m}" for m in msgs)
        return "\n".join(lines)
    with _plan_lock:
        _plan_data["tasks"] = tasks
        _plan_data["planned_for"] = api.day_key()
        _plan_data["abort_reason"] = None
        if _mode_cfg.get("mode") == "plan":
            _plan_data["state"] = "waiting_day"
        else:
            _plan_data["state"] = "idle"
        _plan_save_state()
    lines.append(f"📋 已保存 {len(tasks)} 个任务" +
                 ("（模式=计划，等明日自动开跑）" if _mode_cfg.get("mode") == "plan"
                  else "（模式=自主，plan start 可立即开跑）"))
    lines.append(_plan_describe(tasks))
    lines.extend(m for m in msgs if m.startswith(("⚠️", "ℹ️")))
    rem = _plan_pack_reminder(tasks)
    if rem:
        lines.append(rem)
    return "\n".join(lines)


def _plan_show() -> str:
    with _plan_lock:
        p = _plan_data
        mode = _mode_cfg.get("mode")
        st = p.get("state")
        tasks = p.get("tasks", [])
        planned = p.get("planned_for")
        reason = p.get("abort_reason")
    st_names = {"idle": "待命", "waiting_day": "等明日", "running": "执行中",
                "paused": "已暂停", "done": "已完成", "aborted": "已中止"}
    lines = [f"🛋️ 模式: {mode} | 计划状态: {st_names.get(st, st)}"]
    if planned:
        lines.append(f"  📅 计划日: {planned}")
    if reason:
        lines.append(f"  📌 备注: {reason}")
    if not tasks:
        lines.append("  📋 没有任务——plan write \"...\" 敲定（模式=plan 时明日自动跑）")
    else:
        lines.append("  📋 任务:")
        lines.append(_plan_describe(tasks))
    with _bg_lock:
        active = [j for j in _bg_jobs.values() if j.running]
    if active:
        lines.append(f"  🚀 正在跑: {_bg_job_display(active[0])}")
    return "\n".join(lines)


def _plan_clear() -> str:
    with _bg_lock:
        running = [j for j in _bg_jobs.values() if j.running]
    notes = []
    for j in running:
        _bg_kill(j)
        notes.append(f"🛑 已停脚本 {j.job_id}")
    with _plan_lock:
        _plan_data["tasks"] = []
        _plan_data["state"] = "idle"
        _plan_data["abort_reason"] = "清空"
        _plan_save_state()
    return "\n".join(notes + ["🧹 计划已清空"])


def _plan_start() -> str:
    with _plan_lock:
        if not _plan_data.get("tasks"):
            return "❌ 计划是空的——先 plan write 敲定任务"
        if _plan_data.get("state") == "running":
            return "🚀 计划已在执行中（plan show 看进度）"
        _plan_data["state"] = "running"
        _plan_data["abort_reason"] = None
        _plan_save_state()
    return "🚀 计划开始执行（后台逐任务跑，plan show 看进度）"


def _plan_resume() -> str:
    with _plan_lock:
        if _plan_data.get("state") != "paused":
            return f"📋 计划不在暂停状态（当前: {_plan_data.get('state')}）——只有暂停了能 resume"
        _plan_data["state"] = "running"
        _plan_data["abort_reason"] = None
        _plan_save_state()
    return "▶️ 计划继续执行（从剩余任务开始）"


def _plan_abort() -> str:
    with _bg_lock:
        running = [j for j in _bg_jobs.values() if j.running]
    notes = []
    for j in running:
        _bg_kill(j)
        notes.append(f"🛑 已停脚本 {j.job_id}")
    with _plan_lock:
        _plan_data["state"] = "aborted"
        _plan_data["abort_reason"] = "AI 中止"
        _mode_cfg["mode"] = "autonomous"
        _settings_save()
        _plan_save_state()
    _plan_notify("🛑 计划已中止，回到自主模式")
    return "\n".join(notes + ["🛑 计划已中止，回到自主模式"])


def _plan_default() -> str:
    """建议默认计划（按当前时间粗排，AI 按需删改后 plan write）。"""
    try:
        s = api.state(light=True)
        tod = (s.get("time", {}) or {}).get("timeOfDay", 600)
        lines = ["💡 建议计划（先看 farm 域现状再改，plan write 写入）："]
        if isinstance(tod, int) and tod < 900:
            lines.append("  water_crops | clear_area 捡杂物")
        else:
            lines.append("  water_crops（上午没浇的话）")
        lines.append("  chop_trees --count 5")
        lines.append("  fish_run --location Mountain --max-casts 20")
        lines.append("  bomb_mine --target 40 until 17:00（要下矿才加）")
        return "\n".join(lines)
    except Exception:
        return "💡 游戏没连上，先连上再 plan default"


# 🚫 2026-08-17 恒：plan 工具退役——去掉 @mcp.tool() 不再注册，AI 看不到也用不了。
#    函数体保留作存档（恢复计划模式时重新加回 @mcp.tool() 装饰器即可，dispatch 等内部逻辑原样可用）。
def plan(ops: str = "", spec: str = "") -> str:
    """🛋️ 计划域（第14域 · 全自动一天）—— 🚫 已退役（2026-08-17 恒：计划模式暂不实现，此工具未注册）
    存档说明：恢复计划模式时去掉本行注释、重新加回 @mcp.tool() 即可。
    ops:
    - write <spec>: 敲定明日自动脚本（行式，| 分任务，首词=脚本名，直到时间写 until HH:MM/到HH:MM）
    - show / status: 看当前计划+执行状态
    - clear: 清空计划
    - start: 立即开跑（测试/当天用）
    - resume: 暂停后继续（剩余任务）
    - abort: 中止计划，回自主模式
    - default: 给个默认计划参考
    例: plan(ops="write", spec="nav 姜岛小屋(门口) | scythe_crops --radius 20 | bomb_mine --target 40 until 14:00")
    """
    dispatch = {
        "write": _plan_write, "写": _plan_write, "set": _plan_write,
        "show": _plan_show, "看": _plan_show, "status": _plan_show, "状态": _plan_show,
        "clear": _plan_clear, "清": _plan_clear,
        "start": _plan_start, "开跑": _plan_start,
        "resume": _plan_resume, "继续": _plan_resume,
        "abort": _plan_abort, "停": _plan_abort,
        "default": _plan_default, "建议": _plan_default,
    }
    return _with_state(_ops_run(ops, dispatch, {"spec": spec}))


# ── 调度器 ──

def _now_tod():
    try:
        return (api.state(light=True).get("time", {}) or {}).get("timeOfDay")
    except Exception:
        return None


def _plan_festival_now():
    """今天节日且当前游戏时间已到开始时间 → 返回节日名；否则 None。"""
    try:
        s = api._ai_get("/state")
        t = s.get("time", {}) or {}
        season = (t.get("season") or "").lower()
        day = int(t.get("dayOfMonth", 0) or 0)
        tod = t.get("timeOfDay")
        if not season or not day or not isinstance(tod, int):
            return None
        start = plan_engine.festival_start_hhmm(season, day)
        if start is None or tod < start:
            return None
        f = calendar_data.get_festival_today(season, day)
        return f["name"] if f else "节日"
    except Exception:
        return None


def _plan_start_task(task):
    """启动一个计划任务。nav/map_go/go_to → 进程内 map_go 导航（带解锁拦截，2026-08-14 桥接）；
    其余脚本 → 统一注入 --port AI端口 spawn（防脚本默认打 host 7842 挪恒的角色）。"""
    if task.get("script") in ("nav", "map_go", "go_to"):
        _plan_run_nav(task)
        return
    api.ensure_roles()
    port = api.ai_port()
    args = list(task.get("args", []) or [])
    if not any(a == "--port" or a.startswith("--port=") for a in args):
        args = ["--port", str(port)] + args
    job, err = _bg_start(task["script"], args)
    if err:
        task["status"] = "failed"
        task["result"] = str(err)[:150]
        _plan_notify(f"❌ 任务「{task['script']}」启动失败: {err[:120]}")
        _plan_abort_state("任务启动失败")
        return
    task["status"] = "running"
    task["job_id"] = job.job_id
    _plan_notify(f"🚀 计划任务「{task['script']}」开跑（job {job.job_id}，--port {port}）")
    _plan_save_state()


def _plan_run_nav(task):
    """nav 任务：进程内直接跑 map_go 导航（BFS 最短路径 + 解锁拦截 + POI 落点）。
    导航阻塞调度器线程几十秒，但 nav 是当步唯一任务，可接受。"""
    dest = " ".join(task.get("args", []) or []).strip()
    if not dest:
        task["status"] = "failed"
        task["result"] = "nav 要目的地（如 nav 姜岛小屋(门口)）"
        _plan_notify("❌ nav 任务没写目的地")
        _plan_abort_state("nav 缺目的地")
        return
    task["status"] = "running"
    _plan_save_state()
    try:
        result = map_go(dest)  # 进程内导航（_with_state 附加状态条，classify 只认导航段）
    except Exception as e:
        result = f"❌ {e}"
    nav_part = result.split(_STATE_SEP)[0] if _STATE_SEP in result else result
    verdict = plan_engine.classify_end("nav", 0, nav_part)
    if verdict == "success":
        task["status"] = "done"
        task["result"] = f"已到达 {dest}"
        _plan_notify(f"🗺️ 导航到「{dest}」完成")
    else:
        task["status"] = "failed"
        task["result"] = f"导航失败: {nav_part[:120]}"
        _plan_notify(f"❌ 导航到「{dest}」失败: {nav_part[:100]}")
        _plan_abort_state(f"导航到 {dest} 失败")
    _plan_save_state()


def _plan_finish_task(task, job):
    """任务自发结束 → 按结局分类推进状态机。"""
    out = job._tail(400)
    rc = job.returncode
    verdict = plan_engine.classify_end(task["script"], rc, out)
    if verdict == "success":
        task["status"] = "done"
        task["result"] = f"成功(rc={rc})"
        _plan_notify(f"✅ 任务「{task['script']}」完成")
        _plan_save_state()
        return
    if verdict == "recoverable":
        task["status"] = "failed"
        task["result"] = "提前结束（可恢复）"
        with _plan_lock:
            _plan_data["state"] = "paused"
            _plan_data["abort_reason"] = f"任务「{task['script']}」提前结束"
            _plan_save_state()
        _plan_notify(f"📦 任务「{task['script']}」提前结束（可恢复，可能体力/背包/没可干），计划已暂停——处理完 plan resume 继续，或 plan abort")
        return
    # fatal
    task["status"] = "failed"
    task["result"] = f"高风险结束（{verdict}）"
    _plan_abort_state(f"任务「{task['script']}」异常（{verdict}）")
    _plan_notify(f"💀 任务「{task['script']}」高风险结束，计划中止，已回自主模式（plan show 看详情）")


def _plan_abort_state(reason: str):
    """中止计划并回自主模式（fatal/任务启动失败等内部路径）。"""
    with _plan_lock:
        if _plan_data.get("state") not in ("running",):
            _plan_data["state"] = "aborted"
            _plan_data["abort_reason"] = reason
            _plan_save_state()
            return
        _plan_data["state"] = "aborted"
        _plan_data["abort_reason"] = reason
        _mode_cfg["mode"] = "autonomous"
        _settings_save()
        _plan_save_state()


def _plan_finish_all():
    with _plan_lock:
        if _plan_data.get("state") != "running":
            return
        _plan_data["state"] = "done"
        _plan_data["abort_reason"] = None
        _mode_cfg["mode"] = "autonomous"
        _settings_save()
        _plan_save_state()
    _plan_notify("🚀 计划全部执行完毕，已回自主模式")


def _running_tick():
    """执行中：启动下个 pending / 监控当前任务（时间限制/节日/结束）。"""
    with _plan_lock:
        if _plan_data.get("state") != "running":
            return
        tasks = _plan_data.get("tasks", [])
    cur = next((t for t in tasks if t.get("status") == "running"), None)
    if cur is None:
        # ⏰ 时间检测（2026-08-14 恒补）：开跑前跳过 until 已到/快到（剩 < _PLAN_TIME_MARGIN 游戏分钟）的 pending。
        #    防"AI 整理背包花掉游戏时间后 plan start，任务却已超时"的冲突。
        tod = _now_tod()
        if isinstance(tod, int):
            timed = [t for t in tasks if t.get("status") == "pending"
                     and t.get("until") and tod >= t["until"] - _PLAN_TIME_MARGIN]
            if timed:
                for t in timed:
                    t["status"] = "skipped"
                    t["note"] = f"until {t['until']} 已到/快到了（当前 {tod}）"
                with _plan_lock:
                    _plan_save_state()
                _plan_notify(f"⏭️ {len(timed)} 个任务已过/快到 until 时间（当前游戏 {tod}），跳过")
        nxt = next((t for t in tasks if t.get("status") == "pending"), None)
        if nxt is None:
            _plan_finish_all()
            return
        # 开跑前再查计划截止/节日（task 间隙也可能过点/开始）
        end_time = _plan_data.get("end_time")
        if isinstance(tod, int) and end_time and tod >= end_time:
            for t in tasks:
                if t.get("status") == "pending":
                    t["status"] = "skipped"
            with _plan_lock:
                _plan_data["state"] = "done"
                _plan_data["abort_reason"] = f"计划截止 {end_time} 到"
                _mode_cfg["mode"] = "autonomous"
                _settings_save()
                _plan_save_state()
            _plan_notify(f"⏰ 计划截止时间到，剩余任务已跳过，回自主模式")
            return
        fest = _plan_festival_now()
        if fest:
            with _plan_lock:
                _plan_data["state"] = "paused"
                _plan_data["abort_reason"] = f"节日:{fest}"
                _mode_cfg["mode"] = "autonomous"
                _settings_save()
                _plan_save_state()
            try:
                safe = api.warp_safe()
            except Exception:
                safe = ""
            _plan_notify(f"🎪 {fest} 开始了，未开跑的任务已暂停并回安全位{safe}——去 festival 参加或做下一步决策（plan resume 可继续）")
            return
        _plan_start_task(nxt)
        return
    job = _bg_jobs.get(cur.get("job_id"))
    if job is None:
        _plan_abort_state("任务 job 丢失")
        return
    if not job.running:
        _plan_finish_task(cur, job)
        return
    # 时间限制（until / 计划 end_time）
    tod = _now_tod()
    if isinstance(tod, int):
        if cur.get("until") and tod >= cur["until"]:
            _bg_kill(job)
            cur["status"] = "done"
            cur["result"] = f"到点 {cur['until']} 强制收工"
            _plan_notify(f"⏰ 任务「{cur['script']}」到 {cur['until']//100}:{cur['until']%100:02d} 点了，强制收工")
            _plan_save_state()
            return
        end_time = _plan_data.get("end_time")
        if end_time and tod >= end_time:
            _bg_kill(job)
            for t in tasks:
                if t.get("status") == "pending":
                    t["status"] = "skipped"
            with _plan_lock:
                _plan_data["state"] = "done"
                _plan_data["abort_reason"] = f"计划截止 {end_time} 到"
                _mode_cfg["mode"] = "autonomous"
                _settings_save()
                _plan_save_state()
            _plan_notify(f"⏰ 计划截止时间 {end_time} 到，剩余任务已跳过，回自主模式")
            return
    # 节日开始 → 暂停 + 安全位
    fest = _plan_festival_now()
    if fest:
        _bg_kill(job)
        cur["status"] = "pending"
        cur["note"] = f"被{fest}打断"
        with _plan_lock:
            _plan_data["state"] = "paused"
            _plan_data["abort_reason"] = f"节日:{fest}"
            _mode_cfg["mode"] = "autonomous"
            _settings_save()
            _plan_save_state()
        try:
            safe = api.warp_safe()
        except Exception:
            safe = ""
        _plan_notify(f"🎪 {fest} 开始了，脚本已暂停并回安全位{safe}——去 festival 参加或做下一步决策（plan resume 可继续剩余任务）")


def _waiting_day_tick():
    """计划等明日：日翻转 + 清晨就绪 → running。"""
    with _plan_lock:
        if _plan_data.get("state") != "waiting_day":
            return
        planned = _plan_data.get("planned_for")
    try:
        s = api.state(light=True)
        cur = api.day_key(s)
        if not cur:
            return
        t = s.get("time", {}) or {}
        tod = t.get("timeOfDay", 0)
        am = s.get("activeMenu")
        flipped = (planned != cur) if planned else True  # planned 丢了 → 见有效日期就算翻转
        if flipped and isinstance(tod, int) and tod >= 600 and not am:
            with _plan_lock:
                if _plan_data.get("state") != "waiting_day":
                    return
                _plan_data["state"] = "running"
                _plan_save_state()
            _plan_notify(f"☀️ 新的一天（{cur}），今日计划开始执行")
    except Exception:
        pass


def _fallback_tick():
    """🌙 兜底：凌晨到点自动 go_sleep（覆盖所有行为，最高优先）。
    ⚠️ 2026-08-16 修：爬床失败后加冷却（_FALLBACK_FAIL_COOLDOWN 秒内不重试）——
       否则每 10s 又杀脚本又爬床（先杀采集脚本再失败），轮回被反复打断往外跑。
       且杀脚本前先通知（用户能看到"睡觉拦下了采集脚本"而不是被默默打断）。"""
    global _fallback_busy, _fallback_last_fail_ts
    if _fallback_busy:
        return
    try:
        s = api._ai_get("/state")
    except Exception:
        return
    tod = (s.get("time", {}) or {}).get("timeOfDay")
    if not isinstance(tod, int):
        return
    limit = int(_sleep_cfg.get("time", 2500))
    if not (limit <= tod < 2600):
        return
    # ⚠️ 爬床失败冷却：上次失败还没过 _FALLBACK_FAIL_COOLDOWN 秒 → 不重试
    #   （失败说明位置/条件不对，反复试只会杀脚本+乱跑）
    now = time.time()
    if _fallback_last_fail_ts and now - _fallback_last_fail_ts < _FALLBACK_FAIL_COOLDOWN:
        return
    # 已在床/有菜单/剧情/对话框 → 不打扰（避免重爬破坏 ready 同步）
    am = s.get("activeMenu")
    if am or s.get("activeEvent") or s.get("in_dialogue"):
        return
    _fallback_busy = True
    try:
        with _bg_lock:
            running = [j for j in _bg_jobs.values() if j.running]
        # ⚠️ 杀脚本前先通知——让 AI/用户知道睡觉拦下了什么（而不是被默默打断）
        if running:
            names = ", ".join(j.name for j in running)
            _plan_notify(f"🌙 兜底：{limit//100}:{limit%100:02d} 自动睡觉，拦下运行中的脚本: {names}")
        for j in running:
            _bg_kill(j)
        with _plan_lock:
            if _plan_data.get("state") == "running":
                _plan_data["state"] = "aborted"
                _plan_data["abort_reason"] = "凌晨1点兜底"
                _mode_cfg["mode"] = "autonomous"
                _settings_save()
                _plan_save_state()
        _plan_notify(f"🌙 兜底：{limit//100}:{limit%100:02d} 自动睡觉（运行脚本已停）")
        api.ensure_roles()
        who = api.ai_name() or ""
        # ⚠️ 2026-08-22 恒：内部兜底（凌晨自动睡）沿用旧版 warp 流程（humanize=False）——兜底要稳；
        #    AI 主动 go_sleep 工具才走拟人版（humanize=True，本地扫床去 warp）。
        r = api.go_sleep_flow(who, log=None, humanize=False)
        ok = bool(r.get("ok"))
        _plan_notify(f"🌙 兜底睡觉结果: {str(r.get('summary', '?'))[:200]}")
        if not ok:
            # ⚠️ 爬床失败：记录失败时间，冷却期不重试（防止反复杀脚本+乱跑）
            _fallback_last_fail_ts = time.time()
    finally:
        _fallback_busy = False


def _autopilot_tick():
    # 🚫 2026-08-17 计划模式已退役：计划状态机分支（waiting_day/running）不再触发，
    #    _plan_data.state 恒 idle。此 loop 仅保留 🌙 兜底自动睡觉（独立功能）。
    try:
        if _sleep_cfg.get("enabled", True):
            _fallback_tick()
    except Exception:
        pass
    # 存档：以下为退役的计划调度（_waiting_day_tick / _running_tick 已不可达，保留不动）
    # try:
    #     with _plan_lock:
    #         st = _plan_data.get("state")
    #     if st == "waiting_day":
    #         _waiting_day_tick()
    #     elif st == "running":
    #         _running_tick()
    # except Exception:
    #     pass


def _autopilot_loop():
    while True:
        try:
            _autopilot_tick()
        except Exception:
            pass
        time.sleep(_PLAN_TICK)


# ═══════════════════════════════════════════
#  🧬 自我介绍（技能等级 + 职业分支）2026-08-30 恒：让 AI 看自己
# ═══════════════════════════════════════════
# 职业名不硬编(版本易变)；只在关键 ID 上标注，其余诚实回退"分支#id"。
# 11=Luremaster(名不虚传·蟹笼免饵) 已由游戏 CrabPot.NeedsBait 的 Contains(11)+轮回实测确认；
# 10=Mariner(无垃圾·蟹笼不出垃圾) 由 CrabPot.DayUpdate 的 Contains(10) 判 junk_ratio=0 推断。
_PROF_NAMES = {
    10: "Mariner(捕鱼达人·蟹笼不出垃圾)",
    11: "Luremaster(名不虚传·蟹笼免饵)",
}


@mcp.tool()
def profile() -> str:
    """🧬 查看自己(当前角色)的技能等级 + 职业分支(professions)。
    用来看:是不是 Luremaster(蟹笼免饵→放/挂饵是空操作)、各技能等级、学了哪些分支。
    ops: profile(查看)。"""
    try:
        r = api._get("/profile")
        if not r.get("ok"):
            return f"❌ {r.get('error', '读取失败')}"
        name = r.get("name") or "?"
        sk = r.get("skills") or {}
        lines = [
            f"🧬 {name} 技能等级:",
            f"  农{sk.get('farming')} | 渔{sk.get('fishing')} | 采集{sk.get('foraging')} | 矿{sk.get('mining')} | 战{sk.get('combat')}",
        ]
        profs = r.get("professions") or []
        if profs:
            parts = [_PROF_NAMES.get(i, f"分支#{i}") for i in profs]
            lines.append(f"  🎓 职业分支: {', '.join(parts)}")
            if 11 in profs:
                lines.append("⚠️ 你是 Luremaster —— 蟹笼免饵出货,`crab_bait/crab_place` 挂饵是空操作,不用补饵。")
            if 10 in profs:
                lines.append("💡 你是 Mariner —— 蟹笼不出垃圾(全是鱼)。")
        else:
            lines.append("  🎓 职业分支: 无(还在升级/Mastery?)")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ profile: {e}"


# ═══════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════

# 🔒 域工具模式 keep-set（2026-08-22 恒：默认开启，省 token + 测域工具；`--full`/NAGI_FULL_TOOLS=1 回退全量）
#   只暴露 15 域入口 + 无域等价物的必需独立工具；其余独立工具隐藏（域内部仍调它们，只是不给 AI 直调）。
#   2026-08-22：设置域=常规设置+捏脸(一次性)合并——settings_status/retire/reactivate/color_pick/list_*_ref
#     并入 settings 域 ops，不再单独注册；go_sleep/walk_to/pet_* 等已有域 op 的便捷项一并隐藏（走域 op）。
#   模块常量：domain_selftest.py 直接 import 校验 keep-set 完整性。
_KEEP_TOOLS = {
    # 13 域 dispatcher（2026-09-02 合并：quest→menu, care→farm）
    "check", "farm", "mine", "cabin", "social", "scene",
    "menu", "storage", "daily", "map", "festival", "fish", "settings",
    # 🧭 2026-09-02 合并：script(五合一)/session(三合一)——run_script/script_start/status/stop/async_config→script；
    #   session_status/set/export→session。⚠️旧工具名已隐藏，AI 别直调。
    "script", "session",
    # 无任何域 op 等价物的必需独立工具（系统/控制/感知/单点）
    #   buy_item 已退役（2026-08-16 直购作弊，买走真实商店 shop_visit/menu click）；sprinklers 本无此工具
    #   2026-08-22 收编: wear/lie_bed→daily ops, bundle_kb/donate/read_book→menu ops（域内可调，不再占顶层槽位）
    # 2026-08-28：advance_story 加入——`menu advance` 对事件对话只报"调 advance_story"，不真推进；
    #  而 advance_story 是推进剧情/事件对话(含节日 monologue)的必要独立入口，隐藏=AI 推不动 + 触不了 hook。故暴露。
    "advance_story", "which_role",
    "screenshot", "help",
    "profile",  # 🧬 2026-08-30 恒：看自己技能等级+职业分支(尤其蟹笼 Luremaster)——独立感知工具，一直可见
}


if __name__ == "__main__":
    import sys

    use_stdio = "--stdio" in sys.argv

    # 🔒 域工具模式（2026-08-22 恒：默认开启；`--full`/NAGI_FULL_TOOLS=1 回退全量工具）
    #   只暴露 _KEEP_TOOLS 白名单，隐藏其余独立工具（域内部仍调它们，只是不给 AI 直调）。
    _full_requested = "--full" in sys.argv or os.environ.get("NAGI_FULL_TOOLS") == "1"
    if not _full_requested:
        try:
            _before = len(mcp._tool_manager.list_tools())
            _hidden = 0
            for _t in list(mcp._tool_manager.list_tools()):
                if _t.name not in _KEEP_TOOLS:
                    try:
                        mcp._tool_manager.remove_tool(_t.name)
                        _hidden += 1
                    except Exception:
                        pass
            print(f"  🔒 域工具模式（默认）：隐藏 {_hidden} 个，只留 {len(mcp._tool_manager.list_tools())} 个（{len(_KEEP_TOOLS)} 白名单）；--full 回退全量", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️ 域工具过滤失败（继续全量）: {e}", file=sys.stderr)

    # 🚫 2026-08-17 计划模式已退役：不再 _plan_load_state()（不恢复 plan.json）。
    #    后台 daemon 只跑 🌙 兜底自动睡觉（_fallback_tick），计划状态机已注释（见 _autopilot_tick）。
    try:
        _threading.Thread(target=_autopilot_loop, args=(), daemon=True).start()
    except Exception:
        pass

    if use_stdio:
        # Stdio mode for Claude Code .mcp.json
        print("NagiBridge MCP (stdio) starting...", file=sys.stderr, flush=True)
        mcp.run(transport="stdio")
    else:
        # Streamable HTTP mode (modern MCP protocol, compatible with Claude Desktop/Code)
        print()
        print(f"  NagiBridge MCP Server | HTTP: http://{MCP_HOST}:{MCP_PORT}")
        try:
            import socket
            _ip = socket.gethostbyname(socket.gethostname())
            if _ip.startswith("127."):
                _ip = "本机IP(查 ipconfig)"   # 无局域网 IP 时提示
        except Exception:
            _ip = "本机IP"
        print(f"  📱 手机/Claude Code 连（同一网络）: http://{_ip}:{MCP_PORT}/mcp")
        try:
            _tool_count = len(mcp._tool_manager.list_tools())
        except Exception:
            _tool_count = 0
        print(f"  {_tool_count} tools registered | schema 估算 ~{_schema_estimate()} 字符")
        print(f"  会话日志: {_SESSION_LOG_PATH}")
        # ⚠️ 2026-08-14：端口↔角色按"谁先开游戏谁占7842"分配，启动即探测打印映射。
        #    游戏未开会注明，运行时会随 go_sleep/which_role 自动检测对齐。
        try:
            _roles = api.ensure_roles()
            if _roles.get("ok"):
                _a, _h = _roles["ai"], _roles["host"]
                print(f"  ✅ 角色映射: AI({_a.get('name','?')})={_a['port']} | host({_h.get('name','?')})={_h['port']}")
            else:
                print(f"  ⏳ 游戏进程未就绪（{_roles.get('error','?')}），运行时会自动检测")
        except Exception:
            print("  ⏳ 角色自动检测暂不可用，运行时会自动检测")
        print()
        mcp.run(transport="streamable-http")
