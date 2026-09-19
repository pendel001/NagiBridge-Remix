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
import inspect
import functools
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

# ── 献祭(社区中心收集包)静态知识库 2026-08-22（据中文维基整理；与 bundle_status 查存档现状互补）──
import bundles

# ── 🛋️ 计划引擎（2026-08-14 全自动一天）── 🚫 已退役（2026-08-17 恒：计划模式暂不实现）
#    保留 import：存档的 _plan_* 代码仍引用它，若恢复计划模式可直接重新启用（git 有备份）。
import plan_engine
import tree_types as tt   # 🌳 树种判定 + 砍树放行名单（恒 2026-09-12）
from storage_common import (_parse_store_spec, _resolve_storage_target, _hex_to_color_name, _color_display, _color_to_hex)

# ── 🧭 导航（2026-09-11 task#7：从本文件拆出 navigation.py）──
#    ⚠️ 这行**必须**在 `import stardew_api` 之后（也就是这里，和 storage_common 同处）：
#       stardew_api 在 import 期就把 NAGI_URL / NAGI_AI_URL 固化成 BASE_URL / AI_BASE_URL，
#       而 navigation 内部也 `import stardew_api`。放早了它会先导入 stardew_api，
#       **整个进程**都拿到 7842 默认值 → 所有操作打到房主身上（不是 AI 角色）。
#    下面这 9 个是 server 侧仍在直接调用的导航 helper（sit / stand / bomb_* / map_lookup / map_query /
#    _festival_poi_active / _shop_hours_line / _aim_sleep_home / _crab_* / _pond_* … 都在用），
#    按名字 import 回来，几十处调用点零改动。
#    ⚠️ 方向不能反（让 navigation 反向 import server 会成环）：_sit_selftest 就是 monkeypatch 的
#       M._wait_arrival / M._ai_pos —— 名字得留在 server 命名空间里。
import navigation
from navigation import (_wait_arrival, _ai_pos, _buildings, _locked_maps, _wallet_flag_present,
                        _dwarf_rock_blocked, _go_home, _mine_entry_reminder, _volcano_gate)

# ═══════════════════════════════════════════
#  🧠 会话上下文缓冲（2026-08-13 #7：A2 长期记忆层）
# ═══════════════════════════════════════════
_session_context = []
_session_file = None
_session_ts = time.strftime("%Y%m%d_%H%M%S")

# 📁 2026-09-12 恒：会话相关文件全部挪进 `scripts/sessions/` —— 原来 scripts/ 根目录被
#    238 个 `session_*.jsonl` 堆脏了。⚠️ `session_log.jsonl`（工具调用流水）与
#    `session_<时间戳>.jsonl`（聊天/剧情档案）**不是一回事**，见 README 4.6 的辨析。
SESSION_DIR = os.path.join(SCRIPT_DIR, "sessions")
try:
    os.makedirs(SESSION_DIR, exist_ok=True)   # 三个写入点（档案 jsonl/md + 调用流水）共用
except Exception:
    pass

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
            _session_file = os.path.join(SESSION_DIR, f"session_{_session_ts}.jsonl")
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
            md = os.path.join(SESSION_DIR, f"session_{_session_ts}.md")
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
#  🚧 菜单闸门（2026-09-12 恒拍板）—— 菜单开着时禁掉"会乱动"的操作
# ═══════════════════════════════════════════
# 起因：恒「**你看不到当前菜单吗？我很担心这个问题影响实际游戏流程。要不发现开着菜单就
#       禁用所有菜单和发送信息之外的操作？**」
#       —— 当晚测结算菜单时菜单明明开着、操作照发、返回码还全是 ok，菜单却纹丝不动
#       （真实原因是我钻类目后没按 back；但"菜单开着还照发操作"这个隐患是真的）。
# 恒拍板的边界（**宁报错别兜底** —— 明确拒绝，别让它瞎跑）：
#   ✅ 放行：menu 域全部 / social（沟通）/ check·help·screenshot（只读）/
#          收尾与逃生（daily sleep·settle·lie_bed·cancel、script stop、map warp_safe）
#   🚫 拒绝：其余一切，并告诉它**两条正路**（用 menu 处理 / 先关掉）
# ⚠️ 实现=**猴补 `mcp.tool`**，位置必须在**所有 `@mcp.tool()` 之前** ——
#    这样每个工具注册时自动包一层，不用去动上百处装饰器。
_MENU_GATE_TOOLS_OK = {"menu", "social", "check", "help", "screenshot",
                       "which_role", "role", "profile", "cancel"}
_MENU_GATE_OPS_OK = {
    "daily": {"sleep", "睡", "睡觉", "settle", "结算", "lie_bed", "躺", "躺床",
              "cancel", "取消", "peek", "看恒",
              "whiteboard", "写白板", "wb_read", "看白板", "wb_pin", "钉白板",
              "wb_clear", "清白板"},
    "script": {"stop", "停"},
    "map": {"warp_safe", "逃脱"},
    "session": {"status", "看"},
}
_MENU_GATE_CACHE = {"ts": 0.0, "menu": None}
_MENU_GATE_TTL = 1.5     # 秒：短缓存，别让每个工具都多打一次 /state
# 🔓 安全阀（2026-09-12 恒：「闸门能不能拒绝三次之后就放行，我怕出意外 —— 正常 AI 被提醒
#    一次就知道去处理菜单了吧，除非真的是菜单和事件检测一直异常持续。不过是动画演出也说不定？」）
#    ⇒ 同一工具 + 同一菜单最多拒 3 次，第 4 次**放行并明说**。防止"动画演出/检测异常"把 AI 卡死。
_MENU_GATE_DENY_MAX = 3
_MENU_GATE_DENY = {}     # {(工具名, 菜单类型): 已拒次数}


def _menu_gate_now():
    """当前开着的菜单类型（短 TTL 缓存）。读不到 → None（**不误拦**）。"""
    now = time.time()
    if now - _MENU_GATE_CACHE["ts"] < _MENU_GATE_TTL:
        return _MENU_GATE_CACHE["menu"]
    m = None
    try:
        m = ((api.state() or {}).get("activeMenu") or {}).get("type") or None
    except Exception:
        m = None
    _MENU_GATE_CACHE.update(ts=now, menu=m)
    return m


def _close_stray_gamemenu(tries: int = 3) -> bool:
    """用**端点**关掉挡路的 GameMenu（fishbot 补饵弹的那种）。返回是否真关掉了。

    ⚠️ **别用 `/key esc`**：那是**合成按键**，AI 窗口在后台时**根本不生效**
       （同 `ReadyCheckDialog` 那族——不响应 Escape，只有端点关得掉）。
       端点直接改游戏状态、**不挑窗口焦点**。
    ⚠️ 判据**绕过 `_menu_gate_now` 的 TTL 缓存**直接重读 —— 否则刚关掉还会读到缓存里的旧值，
       误判"没关掉"。
    """
    for _ in range(max(1, tries)):
        try:
            api._ai_post("/menu_close", {})
        except Exception:
            pass
        time.sleep(0.35)
        _MENU_GATE_CACHE.update(ts=0.0, menu=None)     # 作废缓存：下一步读的是真值
        try:
            if not _menu_gate_now():
                return True
        except Exception:
            pass
    _MENU_GATE_CACHE.update(ts=0.0, menu=None)
    return False


def _close_hint(menu: str) -> str:
    """「这个菜单该怎么处理掉」——**按类型给**，别一刀切。

    ⚠️ 2026-09-12 真机踩的坑：闸门原先一律让 AI `menu click(button=upperRightCloseButton)`，
       可那是 `IClickableMenu` 的成员，**DialogueBox 上根本没这个按钮** ⇒ 照抄只会得到
       `⚠️ Button 'upperRightCloseButton' not found`（恒提的第三个问题）。
    """
    m = (menu or "").lower()
    if "dialoguebox" in m:
        return ("menu read 看内容 → 有选项走 menu click(option=N) 选；"
                "纯对话用 menu advance 推掉（DialogueBox 没有右上角关闭键）")
    if "itemgrabmenu" in m or "questcontainer" in m or "shipping" in m:
        return "menu read 看内容 → menu click(button=ok) 确认关掉（交付/结算类要点 ok 才算完）"
    if "shopmenu" in m:
        return "menu read 看商品 → menu click(button=upperRightCloseButton) 关掉"
    if "letterviewer" in m or "dialogue" in m:
        return "menu read 看内容 → menu advance 或 menu click(button=ok) 收掉"
    return "menu read 看内容 → menu click(button=upperRightCloseButton) 关掉（认不出的菜单照这个试）"


def _menu_gate(name, kwargs, args=(), fn=None):
    """菜单开着时挡掉"会乱动"的工具。

    返回 `(是否拦截, 文案)`：
      · (True, 拒绝文案)     → 拦下
      · (False, "")          → 放行
      · (False, 放行说明)    → **放行，但把这句话带回给 AI**（安全阀触发时用）
    """
    try:
        menu = _menu_gate_now()
        if not menu:
            return False, ""
        # 🐟 2026-09-17 恒：「fishbot 开菜单叫你补充鱼饵，这个优化真是太蠢了 —— 加一个自动关掉吧。」
        #    fishbot 补饵弹的 GameMenu 会被它**反复弹回来**，菜单挡着鱼就抛不出去（真机：脚本 5s 收手）。
        #    ⇒ **只对 `fish` + `GameMenu` 这一个组合**先端点关掉、关了再放行。
        #    ⚠️ 刻意**不扩大**到别的工具/别的菜单：AI 自己开背包整理是正经营生，
        #       闸门顺手把它关了 = 另一种"替你瞎做主"（同闸门那族教训）。
        if name == "fish" and "gamemenu" in (menu or "").lower():
            if _close_stray_gamemenu():
                return False, ""
            menu = _menu_gate_now() or menu     # 没关掉 → 用刷新后的真值继续走原拦截逻辑
        if name in _MENU_GATE_TOOLS_OK:
            return False, ""
        _reason = ""
        _ok_ops = _MENU_GATE_OPS_OK.get(name)
        if _ok_ops is not None:
            ops = kwargs.get("ops")
            if ops is None:
                ops = kwargs.get("what")
            if ops is None and args:
                # 位置参兜底：签名里第一个形参若叫 ops/what，就拿第一个位置参当 ops
                try:
                    _p = list(inspect.signature(fn).parameters) if fn else []
                    if _p and _p[0] in ("ops", "what"):
                        ops = args[0]
                except Exception:
                    pass
            ops = str(ops or "").strip()
            if ops in _ok_ops:
                return False, ""
            _reason = (f"🚧 现在开着「{menu}」菜单，`{name} {ops}` 这种先别做 —— "
                       f"菜单态下操作不会按预期生效。\n"
                       f"   本域此刻能用的：{'、'.join(sorted(_ok_ops))}\n"
                       f"   处理完菜单再继续：{_close_hint(menu)}")
        else:
            _reason = (f"🚧 现在开着「{menu}」菜单，`{name}` 先别做 —— "
                       f"菜单态下走位/干活/跑脚本都不会按预期生效。\n"
                       f"   能用：menu(全部) / social 发消息 / check·help / "
                       f"daily sleep·settle·cancel / script stop / map warp_safe\n"
                       f"   先把菜单处理掉：{_close_hint(menu)}")
        # 🔓 安全阀：同一工具 + 同一菜单拒够 3 次 → 第 4 次放行（防"动画演出/检测异常"把 AI 卡死）
        _key = (name, menu)
        _n = _MENU_GATE_DENY.get(_key, 0)
        if _n >= _MENU_GATE_DENY_MAX:
            _MENU_GATE_DENY[_key] = 0        # 放行后清零，避免从此一路放行
            return False, (f"⚠️ 这条已在「{menu}」下被拒 {_MENU_GATE_DENY_MAX} 次，**这次放行** —— "
                           f"但菜单还开着，做之前请确认你真知道在干嘛。")
        _MENU_GATE_DENY[_key] = _n + 1
        return True, _reason
    except Exception:
        return False, ""     # 闸门自己出错 → 放行（绝不因为闸门把正常工具卡死）


_orig_mcp_tool = mcp.tool
# ⚠️ 重入计数：**域工具内部是直接调那些隐藏工具的**（`menu(ops=read)` → `read_menu()`），
#    而隐藏工具也被本猴补包了一层 ⇒ 不挡的话会**双层判定**：外层放行、内层照样拦
#    （实测：`menu ops=read` 被 `read_menu` 那一层拦下）。**只有最外层调用才判闸门。**
_MENU_GATE_DEPTH = {"n": 0}


def _gated_tool(*dargs, **dkwargs):
    _deco = _orig_mcp_tool(*dargs, **dkwargs)

    def _wrap(fn):
        @functools.wraps(fn)          # ← 保住 __name__/__doc__/__wrapped__（FastMCP 靠它读签名）
        def _inner(*a, **kw):
            if _MENU_GATE_DEPTH["n"] > 0:      # 内层：已经在外层工具的执行里，不再判
                return fn(*a, **kw)
            _blocked, _note = _menu_gate(getattr(fn, "__name__", ""), kw, a, fn)
            if _blocked:
                return _with_state(_note)
            _MENU_GATE_DEPTH["n"] += 1
            try:
                _out = fn(*a, **kw)
            finally:
                _MENU_GATE_DEPTH["n"] -= 1
            return (_note + "\n" + _out) if _note else _out

        return _deco(_inner)

    return _wrap


mcp.tool = _gated_tool


# ═══════════════════════════════════════════
#  会话日志（D1，2026-08-12；2026-09-11 起记全文）——测试参考指标
# ═══════════════════════════════════════════
# 每次工具调用写一行到 scripts/session_log.jsonl：
#   {"ts":…, "tool":"…", "ops":"…", "args":{…}, "ret":"<返回全文>", "bytes":N, "ms":N, "err":0/1}
# 用途：① A2 工具合并前后 token 对比 ② "跑一年"验证：看哪天哪工具断了
#       ③ **全工具测试的唯一 transcript**——2026-09-11 恒：原来只记 bytes，"调了哪个 op /
#          传了什么参 / 回的是什么"全都看不见，测试没法打勾、文案好坏也没法复查。
#          现在一次调用落一行全文，报告里每个 ✅ 都要能指到这里的某一行。
# 每次工具调用写一行到 scripts/sessions/session_log.jsonl（2026-09-12 起挪进子目录）：
_SESSION_LOG_PATH = os.path.join(SESSION_DIR, "session_log.jsonl")


def _result_text(result) -> str:
    """把工具返回统一取成文本。形态是**实测**出来的，别想当然：
    · `ToolManager.call_tool(convert_result=True)` → **tuple** `(list[ContentBlock], structured|None)`
    · 低层 handler → `CallToolResult`（block 在 `.content` 里）
    · 工具函数本身 → 直接是 `str`
    漏认一种就会记成空字符串 —— 日志"有行没内容"比没日志还坑（2026-09-11 实测踩到）。"""
    if isinstance(result, str):
        return result
    # ① 剥 tuple：要的是内容那半（第 0 个）；structured 那半是结构化输出用的，这里不要
    if isinstance(result, tuple) and result:
        result = result[0]
    # ② 取 block 列表：CallToolResult 在 .content，裸列表它就是它自己
    blocks = getattr(result, "content", None)
    if blocks is None:
        blocks = result if isinstance(result, (list, tuple)) else []
    if not isinstance(blocks, (list, tuple)):
        blocks = [blocks]
    out = []
    for block in blocks:
        if isinstance(block, str):
            out.append(block)
        elif getattr(block, "type", "") == "text":
            out.append(getattr(block, "text", "") or "")
    return "\n".join(out)


def _log_tool_call(name: str, result, error: bool = False, args=None, ms=None) -> None:
    """记录一次工具调用全文。bytes=文本返回字节数（含状态条，即每次响应的 token 参考）。

    ops 单独提出来，是为了全工具测试能**按 op 统计覆盖**（域工具的 op 藏在 args["ops"] 里，
    直接翻 args 得先判断是哪个域；提成顶层字段后 `grep '"ops":"water"'` 就能核对打勾）。
    ⚠️ 写日志本身**绝不能**让工具调用失败 —— 整段包死 try/except（它只是旁路观测）。
    """
    try:
        text = _result_text(result)
        size = len(text.encode("utf-8"))
        ops = ""
        if isinstance(args, dict):
            ops = args.get("ops") or args.get("what") or ""
        rec = {"ts": round(time.time(), 1), "tool": name, "ops": ops,
               "args": args, "ret": text, "bytes": size, "err": 1 if error else 0}
        if ms is not None:
            rec["ms"] = int(ms * 1000)
        with open(_SESSION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
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


# ⚠️ 挂钩点必须是 `mcp._tool_manager.call_tool`，**不能**是 `mcp.call_tool`：
#   FastMCP 在 `__init__` 里就 `self._mcp_server.call_tool(validate_input=False)(self.call_tool)`
#   —— 低层 server 当场抓住了 bound method 的引用。之后再给 `mcp.call_tool` 赋值，注册进去的
#   handler 毫不知情 ⇒ 日志一行不写、全工具测试全瞎。
#   🐛 2026-09-11 实测：本文件原来就是挂在 `mcp.call_tool` 上的，**挂了 30 天一次都没被调到**
#   （`session_log.jsonl` 压根不存在）。两处都错：挂错点 + 当时那个包装是同步的，
#   而真身 `FastMCP.call_tool` 是 **async**，就算挂对了也只会记到 size=0。
#   而 `_tool_manager.call_tool` 是 `FastMCP.call_tool` **运行时现查**的属性，改它必生效。
_orig_tm_call_tool = mcp._tool_manager.call_tool


async def _logged_tm_call_tool(name, arguments=None, *args, **kwargs):
    """记录每次工具调用（名字/参数/返回全文/耗时/是否抛错），再原样转发。"""
    _t0 = time.time()
    try:
        result = await _orig_tm_call_tool(name, arguments, *args, **kwargs)
    except Exception as e:
        _log_tool_call(name, str(e), error=True, args=arguments, ms=time.time() - _t0)
        raise
    _log_tool_call(name, result, error=bool(getattr(result, "isError", False)),
                   args=arguments, ms=time.time() - _t0)
    return result


mcp._tool_manager.call_tool = _logged_tm_call_tool


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


WELCOME_BANNER = "🌿 NagiBridge MCP 已就绪 · 基于原作者 里奈（小红书@里奈 · GitHub @anqinou-art）的MCP适配+全面二改版本。 · 二改作者：恒（小红书@高冷 腿长 偷感重 · GitHub @pendel001）"

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

    # 警报队列（⚠️ 2026-09-05 恒：改为消费一次即清——之前 peek=True 只读不消费，
    # 某条 walk_teleport 等警告永远留在队列(FIFO 上限100,安静农场无新警报挤) → 每条工具输出重播同一句。
    # 改成默认消费：每条警报渲染一次即消失；真实新警报照常显示一次。
    try:
        alerts_resp = api.alerts()          # peek=False 默认消费/drain
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
                    # 2026-09-11：按**实际叠数**计——一叠 53 个是一个 debris（C# 端新出 stack 字段），
                    # 原来 +1 会把它显示成"1 个"。缺字段的老 DLL 兜底 1。
                    items[n] = items.get(n, 0) + (d.get("stack") or 1)
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
    # 🪑♨️ 心跳彩蛋的两个状态（恒 2026-09-11）：坐着 / 泡澡。
    #    `/state` 里没有这两项，各自打一次 host 端口的小端点——**心跳才注入（默认 5 分钟一次）**，
    #    开销可忽略。拿不到就当 False（老 DLL 没 /sittable 也不炸，只是没彩蛋）。
    try:
        _me_sit = api.host_sittable(7).get("me") or {}
        data["sitting"] = bool(_me_sit.get("sitting"))
        # 🪑 坐的是哪件（2026-09-11 新字段）——**只有家具的名字能进中文句子**：
        #    家具 = 本地化 DisplayName（"红色餐椅"）；地图座椅 = 内部英文 token（"bench"），没本地化名。
        #    ⇒ 打包成 {kind,name} 给 player_activity 判，别在那重打一次 HTTP。
        data["seat"] = {"kind": _me_sit.get("seatKind"), "name": _me_sit.get("seatName")}
    except Exception:
        data["sitting"] = False
        data["seat"] = None
    try:
        data["swimming"] = bool((api.host_pool().get("me") or {}).get("swimming"))
    except Exception:
        data["swimming"] = False
    # 🙋 附近 NPC（**带中文显示名**）：对话框 `speaker` 为空时的"面朝格兜底"要用（`player_activity.facing_npc`）。
    #    ⚠️ 富化**放这儿、不在 player_activity 里现打 HTTP**——跟上面 `seat` 同一个道理
    #    （"打包好给消费方判，别在那重打一次 HTTP"）。**只富化 2 格内的**：那是 `facing_npc` 唯一会用的范围，
    #    顺手把 `/find_npc` 的调用数压到 0~2 次（村中心站着十几个 NPC 时不会一次打十几枪）。
    #    ⚠️ 键名故意叫 **`nearby_npcs`** 而不是 `npcs`：`data["raw"]["npcs"]` 是**全量**，
    #    这份是**截过的**（≤2 格）。同名不同义＝以后读的人踩坑（本项目最不缺这种坑）。
    try:
        data["nearby_npcs"] = _nearby_npcs_with_display(s.get("npcs") or [], data.get("player") or {})
    except Exception:
        data["nearby_npcs"] = []
    return data


_NPC_DISPLAY_CACHE: dict = {}


def _npc_display_name(english: str) -> str:
    """英文内部名 → 中文显示名（`/find_npc` 一次 ~31ms，**只在真拿到名字时**才缓存）。

    ⚠️ 新 DLL 的 `/state.npcs[]` 会**直接带 `displayName`**（C# 一行，见 CHANGELOG 09-19(79)）——
    那时本函数一次都不会被调到，它只是**老 DLL 兼容路**。
    ⚠️ **查不到不写缓存**：NPC 可能不在已加载的图里，一次抖动就把英文名永久钉住是"用兜底把问题搬走"。
    """
    if not english:
        return ""
    if english in _NPC_DISPLAY_CACHE:
        return _NPC_DISPLAY_CACHE[english]
    try:
        r = api.host_get("/find_npc", {"name": english}, timeout=5)
    except Exception:
        return english
    for n in (r.get("npcs") or []):
        if n.get("displayName"):
            _NPC_DISPLAY_CACHE[english] = n["displayName"]
            return n["displayName"]
    return english


def _nearby_npcs_with_display(npcs, player, radius: int = 2) -> list:
    """挑出玩家 `radius` 格内的 NPC 并补上中文显示名（`/state.npcs` 只给内部英文名）。"""
    px, py = player.get("x"), player.get("y")
    out = []
    for n in npcs:
        if not isinstance(n, dict):
            continue
        if px is not None and py is not None and n.get("x") is not None and n.get("y") is not None:
            if abs(n["x"] - px) + abs(n["y"] - py) > radius:
                continue
        e = dict(n)
        if not e.get("displayName"):
            e["displayName"] = _npc_display_name(e.get("name") or "")
        out.append(e)
    return out


def _heartbeat_line(ai_data: dict) -> str:
    """心跳一行：给 AI 看"用户在干嘛 / 是否在附近"（折中方案）。

    - 附近 + 移动中 → "👥 user 正与你同在，⛏️ 正在沙漠中探索"（"和你在一起做某事"前缀）
    - 附近 + 窗口/静止 → 活动本身优先（"🎒 user 正在整理背包" / "💭 user 似乎在发呆"）
      ⚠️ **例外：🪑♨️⛪ 彩蛋句（坐着/泡澡/祷告）不受"静止"门控**——那几件事本来就是
         安静待着，静止是常态、恰恰最该说"同在"（恒 2026-09-11 坐教堂被挡掉后发现的）。
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
        # ⚠️ `describe_activity` **只调一次**：它有副作用（`_stamina_dropped`/`_item_just_increased`
        #    会写历史快照），调两次会把一次性检测（体力掉了/刚捡到东西）白白吃掉。
        activity = player_activity.describe_activity(user_data)
        # 🪑♨️⛪ 彩蛋句**不受 paused 门控**（恒 2026-09-11 当场发现：两人都在教堂、
        #    恒坐着 stationary=280s ≥ 20s ⇒ 被"静止→活动本身优先"挡掉，只出光秃秃的
        #    "正在由巴教堂聆听神谕与火苗声"，没有"同在"）。而坐着/泡澡/祷告本来就是
        #    **安静待着**的事——静止正是常态、恰恰最该说"同在"。所以彩蛋句直接走合并。
        is_egg = activity.lstrip().startswith(("🪑", "♨️", "🛁", "🙏", "🕯️"))
        if paused and not is_egg:
            return activity          # 窗口/静止 → 活动本身优先（整理背包/发呆等）

        # 移动中 → "和你在一起做某事"：三种同在句式随机，各自合并通顺
        pname = p.get("name", "")
        detail = activity.replace(f"**{pname}**", "") if pname else activity

        # 🪑♨️⛪ 彩蛋句是**完整句**（"正在由巴教堂聆听神谕与火苗声。"），跟下面的"地点短语"
        #    （"沙漠中探索"）不是一种东西，硬接才串味，所以单独一套模板。
        #    ⚠️ **只剥图标、保留"正在/在"**（恒 2026-09-11："现在读起来怪怪的"——之前把"在"
        #    也一起剥了，拼出「跟你形影不离，由巴教堂聆听神谕与火苗声」，少了"在"像条标题）。
        #    ⚠️ 同时**别把措辞压成一句**（恒："你这么一列，原来更好些"）——保措辞变化、
        #    只换接法（逗号/破折号接整句）。
        if is_egg:
            d_egg = re.sub(r"^[^一-鿿]+", "", detail).strip()
            if not d_egg:
                return player_activity.detect_player_nearby(ai_data)  # 兜底
            # ⚠️ 接缝别撞车（恒 2026-09-11："是不是拆的位置不太好"）：蛋句以"正"开头时
            #    （"正在由巴教堂聆听…"/"正在温泉静养"），前缀"正与你一同，"会拼出
            #    **两个"正"**（"正与你一同，正在由巴教堂…"）读着打嗝 ⇒ 这种时候只用
            #    不含"正"的那两款前缀。
            _lead = [f"👥 **{oname}** 跟你形影不离，{d_egg}",
                     f"👥 **{oname}** 就在你身边——{d_egg}"]
            if not d_egg.startswith("正"):
                _lead.append(f"👥 **{oname}** 正与你一同，{d_egg}")
            return random.choice(_lead)

        # 地点短语（"沙漠中探索"）：剥掉图标 + "正在/在"，再拼进"…在{detail}"才不会重"在"
        detail = re.sub(r"^[^一-鿿]+(?:正在|在)?", "", detail).strip()
        if not detail:
            return player_activity.detect_player_nearby(ai_data)  # 兜底
        return random.choice([
            f"👥 **{oname}** 正与你一同在{detail}",            # 一同在沙漠中探索（恒："同在，→一同"）
            f"👥 **{oname}** 跟你形影不离，在{detail}",        # 形影不离，在沙漠中探索
            f"👥 **{oname}** 和你一起在{detail}",              # 和你一起在沙漠中探索
        ])
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
    覆盖常见卡点（AI 看见菜单但不知调哪个工具）；ShippingMenu 的聊天横幅走状态注入专门处理，这里给**操作枚举引导**。
    active_event=当前事件 → createQuestionDialogue 场景问句（事件激活时普通 option 点不中真回调）区分用（2026-08-23 恒）。"""
    m = (menu_type or "").lower()
    if m == "shippingmenu":
        # 🧾 2026-09-07 恒：过夜结算 enum 操作引导——summary(五大项+第一名物品+总价)靠 menu read；
        #    钻某类明细用 category=N（按类目序号、跟分辨率无关，比坐标稳）；确认走 ok/daily settle。
        return ("🧾 过夜结算：汇总 menu read · 明细 menu click(category=N)(0农作/1采集/2钓鱼/3矿山/4其它) · 确认 button=ok 进下一天")
    if m == "itemlistmenu":
        # 📋 2026-09-07 恒：丢失物品(ItemListMenu) enum 引导——ok 只确认失去并关闭，**不回收物品**，别让 AI 误以为能领回。
        return "📋 物品清单(丢失的物品)：menu read 看物品+总价 → menu click(button=ok) 确认关闭（ok 只确认失去，**不会领回**）"
    if m == "charactercustomization":
        return "🎭 捏人弹窗：menu customize(状态/名字) + settings appearance(捏脸) + settings confirm_look 核对 → 谨慎决定(可问问host) menu click button=ok 确认（ok后定型不可逆，后期只能靠幻觉神龛解锁）"
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
            return "🎁 带动作菜单(送礼/加料/领取)：menu click(item=物品名) 点物品即触发(送出/加汤/领走)——没ok/确认按钮，别点收起"
        return "📦 领取/箱子：menu click 取件/领奖励"
    if m == "dialoguebox":
        if active_menu.get("responses"):
            # 🎪 场景问句（createQuestionDialogue：星币商店换奖品/跳舞邀请等）——事件激活时普通 option=N 走
            #   event.answerDialogueQuestion 点不中真回调，须 real=true 走真实 receiveLeftClick（2026-08-23 恒实测）
            # 🗳️ 2026-09-13：**判据由 C# 给**（`activeMenu.questionKind` ←
            #   `GameLocation.afterQuestion != null` / `Game1.eventUp`），Python 照读，**不再猜**——
            #   猜错就是点空（2026-08-23 恒实测跳舞邀请）。恒："怕 AI 实际不知道怎么选"。
            qk = active_menu.get("questionKind")
            if qk == "ask":
                # "问句框"（不在 NPC 的 Dialogue 上）：**真实点击才走对回调**——
                # 地点级（afterQuestion：跳舞邀请/克林特菜单）与事件脚本级（lastQuestionKey：
                # 转盘/星星币店）**两支都要 real=true**（恒 08-23 实测星星币店）。
                return "🗳️ 问句框（跳舞邀请/摊位/转盘…）：menu click(option=N, **real=true**) 选择"
            if qk == "npc":
                # 选项挂在 `Dialogue` 上（节日里「什么事？」等）。
                # ✅ 2026-09-13 深夜**定案：也要 `real=true`**（反编译 + 可观测副作用双重实证）：
                #   · `Dialogue.cs:1613` 真实点击 → `Dialogue.chooseResponse` → 调的就是
                #     `event.answerDialogueQuestion(speaker, responseKey)`（`case "danceAsk"` 就是邀请）
                #     ⇒ **real 这条路才是对的**；
                #   · mod 不给 real 时自己调同一方法，但 NPC 靠 `isCharacterAtTile(player.GetGrabTile())`
                #     找（`ModEntry.cs:11517`）—— **没面朝对方就是 null ⇒ 静默点空**；
                #   · 真机：不带 real 连点两次框不关；`real=true` 一次 ⇒ **海莉报出接受台词**
                #     （`Event.cs:12123-12133`，只有成功分支才设那句）⇒ 邀请**真的生效**。
                return "🗳️ NPC 对话选项：menu click(option=N, **real=true**) 选择"
            # 旧 DLL 不报 questionKind ⇒ **如实说分不出**，给"点不动再加"（宁报错别兜底）
            if active_event:
                return "🗳️ 场景问句（事件）：menu click(option=N)；**框不关就加 real=true 再点一次**（此 DLL 不报框种类）"
            return "🗳️ 对话选项：menu click(option=N) 选择"
        return "💬 对话推进：menu advance(推进剧情/对话)；**有选项用 menu click(option=N) 选**（confirm 选不了选项）"
    if m == "readycheckdialog":
        # 🎪 2026-09-13：`ReadyCheckDialog` 是**一个类管两件事**，靠 `checkName` 区分
        #   （`"sleep"`=睡觉就绪 / `"festivalStart"`=节日入场就绪）。原先这里一律喊"睡觉就绪屏"，
        #   恒 09-13 看到的就是这句**误导文案**（AI 明明卡在节日入场，屏上却写着睡觉）。
        rc = active_menu.get("readyCheck") or {}
        name = rc.get("name")
        nr, nq, ok = rc.get("numberReady"), rc.get("numberRequired"), rc.get("isReady")
        cnt = f"{nr}/{nq}" if isinstance(nr, int) and isinstance(nq, int) else "?"
        if name == "festivalStart":
            if isinstance(nr, int) and isinstance(nq, int) and nr >= nq and not ok:
                # 客户端视角"全员齐了"却未放行 = 卡在房主侧（CHANGELOG ㊵）。**绝不能 cancel**：
                # 撤了就绪会让房主更等不到人，等于自己把门关上。
                return (f"🎪 节日入场就绪（{cnt} **全员已就绪**，只等房主放行）：**别 cancel、别动**，"
                        f"mod 会自己踹一脚；久等不动用 check(what=\"ready\") 看现场")
            return f"🎪 节日入场就绪（{cnt}，等人齐）：**别 cancel**（撤了就绪会让全员更等不到）"
        if name == "sleep":
            return "🛏️ 睡觉就绪屏（等全员 ready）：想撤就绪/关屏 → menu cancel；确认就寝过夜 → daily sleep"
        if name is None:
            # 旧 DLL 不报 readyCheck —— **如实说分不出**，别硬猜睡觉（宁报错别兜底）
            return "❓ 就绪屏（等全员 ready；此 DLL 不报 checkName，分不出睡觉/节日）：**先别 cancel**，看 check(what=\"status\") 是否在节日"
        return f"❓ 就绪屏（未知 checkName={name}，等全员 ready）：**先别 cancel**"
    if m == "forgemenu":
        return "🔨 锻造台：menu forge 附魔/幻化/组合戒指"
    if m == "junimonotemenu":
        return ("🎁 献祭缺口：menu bundle(只读存档,不走路) 看还缺什么；捐物品仍要走过去开板 → "
                "menu click 点bundle进页 → menu click item=物品捐 / areaNextButton切房间")
    if m == "choosefromiconsmenu":
        return "🎨 选效果菜单：menu click 选图标"
    if m == "specialordersboard":
        return "📋 任务板：menu read 看任务卡(名称/目标/奖励/期限/可接) → 接取 menu click(button=acceptLeftQuestButton/acceptRightQuestButton)（左右二选一）"
    if m == "levelupmenu":
        # 🧬 2026-08-30 恒：LevelUpMenu（升级/职业选择）。普通升级 auto-confirm 自会点OK；职业选择须 AI 决策。
        lu = active_menu.get("levelUp") or {}
        if lu.get("isProfessionChooser"):
            off = lu.get("offered") or []
            nm = " / ".join(f"{o.get('name')}({o.get('id')})" for o in off)
            return (f"🔀 升级选职业(Skill {lu.get('skillName')} Lv{lu.get('level')})：{nm}。"
                    f"→ **menu ops=levelup_choose side=left/right**(或 profession=职业id) 定夺")
        return f"🎉 升级到 {lu.get('skillName') or '?'} Lv{lu.get('level')}——普通升级已自动点OK"
    if m == "masterytrackermenu":
        # 🎓 2026-09-16：精通山洞的碑/基座菜单（此前这里没分支 ⇒ AI 开出来只看到两个按钮坐标，读不懂）。
        #    ⚠️ 这条分支**常年拿不到数据**：本函数喂进来的是 `/state` 的 activeMenu，而 C#
        #    `HandleState` 那份序列化**没有 `mastery` 键**（`/menu` 才有，见 HandleMenu 的
        #    MasteryTrackerMenu 支）。所以这里**绝不能凭空的 mt 下结论**——早先那版会把
        #    "读不到"误判成"现在领不了"，等于对着能领的碑喊"领不了"，比不提示更坏（恒 09-16 当场撞见）。
        #    ✅ 已根治：C# 把 `mastery` 也塞进了 HandleState 的 activeMenu，且与 `/menu` **共用
        #    BuildMasteryInfo**（两处不可能再漂移）。下面这条"拿不到"分支留着只为**兼容旧 DLL**——
        #    真走到这儿说明 DLL 是 09-16 之前的，如实指路 `menu read`（它读 `/menu`，是权威）。
        mt = active_menu.get("mastery") or {}
        if not mt:
            return ("🎓 精通碑/基座菜单 → `menu read` 看这块碑给什么、现在能不能领"
                    "（有没花掉的精通等级才点得动 `menu click(button=mainButton)`）")
        rw = mt.get("rewards") or []
        rw_txt = " / ".join(
            f"{x.get('name') or x.get('id')}"
            + ("(配方)" if x.get("isRecipe") else "(物品)")
            for x in rw) or "（读不到奖励列表）"
        if mt.get("isOverview"):
            return ("🎓 精通**总览**（中央基座）：这页只画精通等级进度条 + 五颗星，看不到具体奖励。"
                    "想看某块碑给什么，得走到那块碑前 `interact` 单独开。")
        if mt.get("claimed"):
            return (f"🎓 精通**{mt.get('title') or mt.get('skill')}**碑 — **这块已经领过了**"
                    f"（游戏不再给领取按钮）。奖励原本是：{rw_txt}。`menu click(button=upperRightCloseButton)` 收掉")
        if mt.get("canClaim"):
            return (f"🎓 精通**{mt.get('title') or mt.get('skill')}**碑，**可以领**：{rw_txt}。"
                    f"→ `menu click(button=mainButton)` 领取（领完这块碑就点亮了）")
        return (f"🎓 精通**{mt.get('title') or mt.get('skill')}**碑，奖励是 {rw_txt}，"
                f"但**现在领不了**（没有没花掉的精通等级）—— 先攒精通经验再来。"
                f"`menu click(button=upperRightCloseButton)` 收掉")
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
    "msg_sig": None,        # 上次已提醒过的"用户最新一条消息"签名（用于"有新消息立刻催"）
}
_CHAT_POLL_INTERVAL = 30      # 轮询间隔：30s 一次（恒拍板 2026-08-17）

# ── 🏝️ 姜岛"互相挤床"彩蛋的一次性闸门（2026-09-19 恒：「如果房主是后睡，触发的时机也要动（互相爬床）」）──
# 大陆那套是**我方主动爬**（进床那一刻播报）；姜岛反过来：我方先躺进大通铺某张床等过夜，
# **房主后来**也钻进**同一张**床 → 那一刻才该播彩蛋。所以要"看着他来没来"，见 `_island_mutual_crawl`。
# 进环节时先记一笔"他当时在不在我床上"——他要是一开始在，那是**我爬他**那条路（进床时已播过），
# 这条就不该再响一次（否则一次同床两条播报）。
_ISLAND_SQUEEZE = {
    "done": False,                 # 今晚这张床的"互相挤床"是否已播过 / 是否已被判定"不该播"
    "check_ts": 0.0,               # 上次问房主位置的时间（节流，别每次状态条都打 HTTP）
    # 🕐 上次看到"我在姜岛小屋"——**省 HTTP 的闸门**：只有当它为真时，2s 那条快线才去拉状态。
    #    平时在大陆就完全空转（否则要常年每 2s 打一次 /state）。
    "maybe": False,
    # 🔒 **本回合"就绪"是否已经上过闩**（每个"就绪回合"只上闩一次，见 `_island_squeeze_tick`）。
    #    ⚠️ 上闩以前挂在**状态条**的环节切换上（旧函数 `_island_mutual_arm`，**已删**）——而阻塞睡觉时状态条
    #    **根本不构建** ⇒ "我爬他"那一晚会**两条都播**（C# 播"我钻进他被窝"，这边又播"他钻进我被窝"）。
    #    现在改成由**就绪信号自己**驱动：ready 由 False→True 那一刻上闩，状态条不再参与。
    "armed": False,
}


def _host_on_my_island_bed(data) -> bool:
    """房主此刻是不是**真的跟我躺同一张姜岛床**（同图 + `isInBed` + 相距 ≤1 格 + **他自己登记了就绪**）。

    ⚠️ 原 docstring 写"岛上那格本来就是床、不是走道 ⇒ `isInBed` 咬不到清早刚醒"——**2026-09-19 真机
    当场推翻**：早 07:50 轮回 `isInBed=True`，只是刚起床杵在床格里。所以这里补第三问：房主**自己的**
    `/state.player.sleepReady.ready`（`h` 本来就是 host 端 `/state`，不用再打一次 HTTP）。
    老 DLL 没这字段 ⇒ 退回只看 `isInBed`（版本兼容，不是改判）。
    """
    me = data.get("player") or {}
    mx, my = me.get("x"), me.get("y")
    if mx is None or my is None:
        return False
    h = api.host_state()
    hp = h.get("player") or {}
    if (h.get("location") or {}).get("name") != api.ISLAND_HOUSE:
        return False
    if not hp.get("isInBed"):
        return False
    if abs(hp.get("x", 999) - mx) > 1 or abs(hp.get("y", 999) - my) > 1:
        return False
    sr = hp.get("sleepReady") or {}
    if "state" in sr and not sr.get("ready"):
        return False          # 量过了：他只是脚踩床格（刚起床/发呆），没登记"要睡"
    return True




def _island_mutual_crawl(data, loc_name) -> str:
    """🏝️ 姜岛**互相挤床**（房主后睡）：返回要播的一句广播，或 ""。

    只在"等睡"环节调（AI 已登记就绪、正躺在**姜岛小屋**的床上等过夜）。
    与大陆那套的关系：大陆是**我方主动爬**（进床那一刻播报）；这里是我方先躺下、**房主后来**
    钻进**同一张**床 ⇒ 那一刻才播——正是恒说的「如果房主是后睡，触发的时机也要动」。
    播报走 AI 进程的 `/chat`（= C# `Broadcast` 在 farmhand 侧的同款：本机粉色 + 显式推给房主）。
    """
    if loc_name != api.ISLAND_HOUSE or _ISLAND_SQUEEZE["done"]:
        return ""
    now = time.time()
    # 节流：最多每 1.5s 问一次房主在哪（状态条会频繁构建）。
    # ⚠️ 原来是 5s —— 那会**饿死** 2s 快线：节流计时是"问过就记"，而窗口本身只有几秒，
    #    一次空问就能把整段窗口挡掉（2026-09-19 真机第一晚的另一种可能死法）。
    if now - _ISLAND_SQUEEZE["check_ts"] < 1.5:
        return ""
    _ISLAND_SQUEEZE["check_ts"] = now
    try:
        if not _host_on_my_island_bed(data):
            return ""
    except Exception:
        return ""
    _ISLAND_SQUEEZE["done"] = True
    me = data.get("player") or {}
    try:
        host_name = ((api.host_state().get("player") or {}).get("name")) or _host_name()
    except Exception:
        host_name = _host_name()
    me_name = me.get("name") or "我"
    # 🏝️ 文案三选一（2026-09-19 恒：「这个好可爱啊」）。⚠️ 主语恒定（永远是"他钻进我"）——
    #    这条与 `island_sleep_plan` 那条是**反方向**的同一张床，方向糊了就分不清谁爬谁。
    #    恒当时把第二条破折号后的调侃（"通铺嘛，挤挤热闹"）划掉了，别再补回去。
    msg = random.choice([
        f"<{host_name}>掀开被子挤了进来，姜岛的小床立刻不够睡了 🏝️",
        f"<{host_name}>一声不吭钻进<{me_name}>的被窝 🌴",
        f"被窝一沉——<{host_name}>也躺下了，两个人挤一张床 🌙",
    ])
    try:
        api._post("/chat", {"message": msg, "color": "hotpink"})     # 轮回自己那侧窗口
    except Exception:
        pass
    # ⚠️ **必须再显式推给恒**（2026-09-19 真机逮到，恒原话「也没广播」）：上面那句打的是 **AI 自己的进程**，
    #    它的 `/chat` 只在本机 `addMessage` + 走**原生发送**，而原生聊天在这套双开下**不通**
    #    （浴场同泡彩蛋踩过同一条，见 `host_chat` 的 docstring）⇒ 彩蛋只在轮回窗口闪过。
    #    C# 侧的 `Broadcast` 有"显式推对面"那段，Python 这边原来漏了。
    #    payload 对齐 `Broadcast` 的 AI→host 分支：hotpink、**不带 notice**（notice 是 host→AI 防回声用的）。
    try:
        api.host_chat(msg, color="hotpink")
    except Exception:
        pass
    return f"🏝️ 姜岛挤床彩蛋已播：{msg}"


def _island_squeeze_tick(s, loc_name=None) -> str:
    """🏝️ 转一次"房主后睡、钻进我被窝"的检查。返回要展示的回执（没播 = ""）。

    ⚠️ **为什么非得自己转、不能只靠状态条**：这套检查原先只挂在**状态条**上，而状态条
    **只在 AI 调工具时**才构建。可这个彩蛋要等的恰恰是"**我躺好等过夜、房主后来才钻进来**"那一刻
    ——那时 AI 正躺着、**根本不会去调工具** ⇒ 检查永远不转、彩蛋永远不响（"触发时机要动"等于没动）。

    🕐 **采样频率是命门**（2026-09-19 真机第一晚就栽在这）：房主一钻进被窝，两人都就绪
    ⇒ 游戏**几秒内**就翻页，而"他已在床上"这个状态只存在那几秒。10s 的后台 tick 整段漏掉
    ⇒ 现在 `_autopilot_loop` 里开了 **2s 一条快线**（`_island_squeeze_fast`），状态条也改调本函数。

    🔒 **上闩（本回合只上一次）**：`armed` 由**就绪信号自己**驱动 —— `ready` 由 False→True 的那一刻，
    问一次"他是不是已经在我床上"：在 ⇒ 那是"**我爬他**"（进床时 C# 已播过），本回合就**不再播**
    （否则一次同床两条播报）。`ready` 落回 False（起床/翻页）⇒ 闩与 done 一起清掉，下一觉重来。
    ⚠️ 以前这闩挂在状态条的环境切换上，而阻塞睡觉时状态条不构建 ⇒ 那个洞是真的（见 `_ISLAND_SQUEEZE` 注释）。
    """
    p = (s or {}).get("player") or {}
    # `loc_name` 可由调用方直接给（状态条那边本来就有），省得依赖 `s["location"]` 的形状
    loc = loc_name if loc_name is not None else ((s.get("location") or {}).get("name") or "")
    _ISLAND_SQUEEZE["maybe"] = (loc == api.ISLAND_HOUSE)   # 喂给 2s 快线的闸门
    if not ((p.get("sleepReady") or {}).get("ready")):
        # 没就绪（清早刚起 / 白天 / 还没上床）⇒ 本回合作废，下一觉从头来
        _ISLAND_SQUEEZE["armed"] = False
        _ISLAND_SQUEEZE["done"] = False
        return ""
    if loc != api.ISLAND_HOUSE:
        return ""
    if not _ISLAND_SQUEEZE["armed"]:
        _ISLAND_SQUEEZE["armed"] = True
        try:
            _ISLAND_SQUEEZE["done"] = _host_on_my_island_bed({"player": p})
        except Exception:
            _ISLAND_SQUEEZE["done"] = False
        return ""                       # 上闩这一拍只判方向，不播
    if _ISLAND_SQUEEZE["done"]:
        return ""
    return _island_mutual_crawl({"player": p}, api.ISLAND_HOUSE)


def _island_squeeze_fast() -> None:
    """🕐 **2s 快线**：只在"上次看到我在姜岛小屋"时才去拉状态（`maybe` 闸门，平时在大陆零 HTTP）。

    为什么值得单开一条 2s 的线：`_island_mutual_crawl` 要抓的窗口只有**几秒**
    （房主钻进被窝 → 双方就绪 → 游戏翻页），10s 的常规 tick 很容易整段错过。
    """
    if not _ISLAND_SQUEEZE.get("maybe"):
        return
    try:
        _island_squeeze_tick(api._ai_get("/state"))
    except Exception:
        pass


_CHAT_TIMEOUT_STEP = 180      # 超时兜底步进（等睡期）：每 3 分钟没新消息发一次
# 🧾 结算期**单独缩短**（2026-09-12 恒：「过夜菜单也180s吗，太久了。过夜菜单应该算聊天环节，
#    专门缩短一下比较好。一分钟比较合适」）—— 结算本身就是聊天环节，而且是**卡着新一天开始**的
#    （游戏时间暂停），干等 3 分钟纯浪费。
_CHAT_TIMEOUT_STEP_BY_PHASE = {"settlement": 60, "wait_sleep": _CHAT_TIMEOUT_STEP}


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


def _chat_phase_newmsg(phase) -> str:
    """🔔 用户刚发来新消息 → **立刻**催一句回话（恒 2026-09-12：「或者 hook 轮询看有新信息就即时回，
    超时只考虑确认结算用」）。

    以前新消息只做"重置超时计时"，AI 要**等到下一个 30s 轮询**才被提醒 —— 恒说句话后最多愣 30 秒。
    现在消息一到就注入这一行（**消息内容本身由小新闻块显示**，这里只给"该回话了"这个动作，不重复抄一遍）。
    """
    h = _host_name()
    if phase == "settlement":
        return f"🔔 {h} 刚发消息了——**先回一句**再谈别的；聊完 daily settle 进下一天"
    return f"🔔 {h} 刚发消息了——先回一句，再接着等一起睡"


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
                           "timeout_n": 0, "entered": time.time(), "just_entered": True,
                           "msg_sig": None}
            # 🏝️ 姜岛"互相挤床"随环节复位**只清节流**，**不动闸门**：
            #    ⚠️ 原先这里连 `done`/上闩一起重置，而**状态条在阻塞睡觉时不构建** ⇒ 那个"上闩"压根不会发生，
            #       "我爬他"那一晚会两条都播。现在上闩归 `_island_squeeze_tick` 按就绪信号自己管（单一真相源）。
            _ISLAND_SQUEEZE["check_ts"] = 0.0
        if not phase:
            return ""
        # 🏝️ 姜岛：房主**后睡**时钻进我这张床 → 播一次"挤一挤"（恒 2026-09-19）
        #    状态条只是**顺路**再转一次（判据/上闩都在 `_island_squeeze_tick` 里，与 2s 快线同一份真相）
        _isl_line = ""
        if phase == "wait_sleep":
            try:
                _isl_line = _island_squeeze_tick(data, loc_name)
            except Exception:
                _isl_line = ""
        now = time.time()
        # 🔔 取用户**最新一条**消息（chat/emote）：既重置超时计时，也用来判"这条还没提醒过"。
        #    ⚠️ 唯一要排掉的是**自然聊天同步回流的自己那条**（格式 `💬 轮回: …` / `💬 轮回 发了…`）
        #    —— 排它是**防御性**的：实测 2026-09-12 `social send` 走 `host_chat`（打 7842 恒的
        #    进程），轮回自己进程的 recent_events **压根收不到**自己的话（0 条），所以正常情况下
        #    这条分支不会命中。
        #    ✅ **裸文本（无前缀）要算新消息**：那是 host→AI 的 HTTP 推送（`/chat` 带 notice，
        #    见 ModEntry.cs:3540），是**真·别人发给我的**，别误杀。
        _msg_sig = None
        try:
            _me = p.get("name") or ""
            for ev in reversed((data.get("raw") or {}).get("recent_events") or []):
                if (ev or {}).get("type") not in ("chat", "emote"):
                    continue
                _t = ev.get("text") or ev.get("message") or ""
                if _me and (_t.startswith(f"💬 {_me}:") or _t.startswith(f"💬 {_me} ")):
                    continue                     # 自然同步回流的自己那条 → 跳过
                _msg_sig = f"{ev.get('type')}|{_t}"
                break
        except Exception:
            _msg_sig = None
        _new_msg = _msg_sig is not None and _msg_sig != _CHAT_PHASE.get("msg_sig")
        if _new_msg:
            _CHAT_PHASE["msg_sig"] = _msg_sig
            _CHAT_PHASE["last_msg_ts"] = now
            _CHAT_PHASE["timeout_n"] = 0
        # ① 刚进入环节 → 引导横幅（一次性；结算的横幅由菜单段提供）
        if _CHAT_PHASE.get("just_entered"):
            _CHAT_PHASE["just_entered"] = False
            return _chat_phase_banner(phase)
        # ② 🏝️ 姜岛挤床彩蛋**刚播出去** → 立刻告诉 AI（它自己"感觉到被窝一沉"，
        #    比 30s 轮询/超时都紧急；播报本身已在 `_island_mutual_crawl` 里发过了，这里只是回执）
        if _isl_line:
            _CHAT_PHASE["poll_ts"] = now
            return _isl_line
        # ③ 🔔 有新消息 → **立刻**催回话，不等 30s 轮询（恒 2026-09-12）
        if _new_msg:
            _CHAT_PHASE["poll_ts"] = now
            return _chat_phase_newmsg(phase)
        # ④ 超时兜底（结算 60s / 等睡 180s 步进加码，不刷屏）——比轮询更紧急，先判
        _step = _CHAT_TIMEOUT_STEP_BY_PHASE.get(phase, _CHAT_TIMEOUT_STEP)
        if now - _CHAT_PHASE["last_msg_ts"] >= _step * (_CHAT_PHASE["timeout_n"] + 1):
            _CHAT_PHASE["timeout_n"] += 1
            return _chat_phase_timeout(phase, int(now - _CHAT_PHASE["last_msg_ts"]))
        # ⑤ 30s 轮询
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

# ⚠️ 2026-09-11：「这条状态条最后会被丢掉吗」——域 op 内部 `_with_state` 产生的状态条会被
#    `_ops_run` 整条砍掉（只留文字，`scene` 等域工具在外层**统一再附一次**，见 _ops_run 末尾）。
#    ⇒ 凡是**"变化才报"**的注入（消费一次就没了的那种），在被丢掉的那一层必须**闭嘴且不消费**，
#    否则：内层先把变化吃掉 → 外层重建时判定"没变化" → **整行对 AI 永久失踪**。
#    2026-09-11 踩坑实录：`🪑 可交互：sit(x,y)` 在 `scene seats` 下死活不出现，单进程调
#    `_sit_hint()` 却正常——就是这个。测试须**走域 op**（`scene seats`），别只单测函数。
#    ✅ 2026-09-12 恒拍板**根治**：不逐处加闸，改成 `_with_state` **开头就 `if _OPS_INNER["n"] > 0: return result`**
#      —— 内层压根不建状态条 ⇒ 上面那族注入（`_is_new_day`/`_statue_reminder`/📰小新闻…）再没有"被丢掉的那一层"。
#      逐处加闸那条路已**两次踩漏**（09-11 🪑、09-12 🌿），靠人守不如靠结构。细节见 `_with_state` 开头的长注释。
_OPS_INNER = {"n": 0}   # >0 = 正在执行域 op；`_with_state` 据此**整层不建状态条**（`_ops_run` 另有兜底再切一次）
_MONSTER_WARN = {"ts": 0.0}   # ⚔️ 农场掉血=有怪提醒，30s 节流防刷屏

# ♨️ 浴场场景引导（2026-09-10 恒："注入场景引导（每日进入一次性提醒）：上下水口坐标、
#    可以在泳池里 walk_to 游泳、静止下来泡温泉以恢复体力"）——每天首次进浴场图报一次，之后不自刷省 token。
_BATH_GUIDE_KEY = {"day": None, "shown": False}


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

    # 🪑 坐着时收窄引导（恒 2026-09-11）：玩家坐着时游戏**把世界动作全锁了**——
    #    `Farmer.MovePosition` 判 `IsSitting()` 直接 return（人走不动）、`Game1` 里 `IsSitting()` 时
    #    也不给用工具、`checkAction` 一进去就 `StopSitting()`。⇒ 这时再枚举"本图能干啥/哪能去"
    #    是纯噪音，**只留一行"起身"**（`scene stand`）。站起来后下一帧自动恢复完整引导。
    #    ⚠️ 数据走 `_sittable_cached`（2s TTL，与 _sit_hint 共用缓存，不多打 HTTP）；
    #    旧 DLL 没 /sittable 时静默 False（退回老行为，不炸）。
    try:
        _sitting_now = bool((_sittable_cached(7).get("me") or {}).get("sitting"))
    except Exception:
        _sitting_now = False

    lines = []
    luck = p.get("dailyLuck")
    # 🎲 运势数值 + (年X)：只在每天第一次(full=True)显示；精简版(后续调用)不重复，省 token（2026-08-22）
    luck_str = f" | 🎲 {luck:+.3f}" if (full and luck is not None) else ""
    year_str = f" (年{year})" if full else ""
    # 🔴 单进程折叠（AI 与 host 是同一个进程 ⇒ 所有"AI 操作"都打在这一个角色身上）——
    #    挂在 📍 行尾当警标（不另起一行省 token）。详细解释在启动横幅和 stardew_api.detect_roles。
    _solo_tag = ""
    try:
        if api.roles_solo():
            _solo_tag = " | 🔴单进程折叠(AI=host,操作都打它)"
    except Exception:
        pass
    lines.append(f"📍 {loc_name} ({x},{y}) | ⏰ {time_str} | {weather_text} · {s_icon}{season.title()} | {day_label}{year_str}{luck_str}{_solo_tag}")

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
        if _map_enum and not _sitting_now and loc_name not in ("Farm", "FarmHouse", "Cabin", "Backwoods", "Tunnel", "Mine", "SkullCave"):
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
        if _fps and not _sitting_now:
            _names = "、".join(_n.split("(")[0] for _n, _d in _fps)
            _total = len(_festival_pois_here(loc_name))
            _suffix = f" 等{_total}项（细节→festival poi）" if _total > len(_fps) else ""
            lines.append(f"  🎪 可: {_names}{_suffix}")
    except Exception:
        pass

    # ♨️ 浴场场景引导（2026-09-10 恒：每日进入一次性——上下水口 / 可 walk_to 游泳 / 静止回体力）
    #    文案与坐标都来自 locations.bath_guide_lines()（坐标是反编译 + /tile_props 扫图实证的）。
    try:
        if isinstance(loc_name, str) and loc_name.startswith("BathHouse_"):
            _bdk = api.day_key()
            if _BATH_GUIDE_KEY["day"] != _bdk:
                _BATH_GUIDE_KEY["day"] = _bdk
                _BATH_GUIDE_KEY["shown"] = False
            if not _BATH_GUIDE_KEY["shown"]:
                _BATH_GUIDE_KEY["shown"] = True
                lines.extend(locations.bath_guide_lines(loc_name))
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
        if _sitting_now:
            pass                                   # 🪑 坐着：世界动作全锁，可用域枚举是噪音 → 收掉
        elif _dom:
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

    # ⚔️ 2026-09-02 恒：农场掉血提醒。**2026-09-12 恒拍板改判据**：
    #    "农场有怪这个，我们应该可以检测荒野农场了。女巫雕像不知道能不能检测，也能的话那触发条件会更加
    #     明确了（荒野农场或打开黑暗屏障），免得玩家养史莱姆吓哭 AI" ⇒ **两个都能，而且就是同一个开关**。
    #    原来只是"在农场 + 掉血 ⇒ **猜**有怪"，现在读 `farmMonsters`（C# `/state` 抄的
    #    `Game1.spawnMonstersAtNight` —— 背后是 `FarmerTeam.spawnMonstersAtNight` 这个**同步 NetBool**，
    #    荒野农场 / 女巫小屋黑暗神龛 都落在这一个 flag 上；游戏真正刷怪还要过 `timeOfDay>=1900` + 幸运随机，
    #    见 Farm.cs:722）：**为真才喊"有怪"，为假就说实话**（别冤枉史莱姆、别让 AI 白躲）。
    #    ⚠️ 旧 DLL 没这字段 ⇒ `None`，此时**退回老措辞**（照旧猜），不假装知道。
    #    前一次+当前都在农场 且 血量下降 → 提醒。30s 节流防连续挨打刷屏；基线首次/换天为 None 不误报。
    try:
        _hp_n = p.get("health")
        _fm = (data.get("raw") or {}).get("farmMonsters")   # None=旧 DLL（认不出），True/False=实锤
        if (loc_name == "Farm" and _STATE_DELTA.get("loc") == "Farm"
                and _hp_n is not None and _STATE_DELTA.get("health_n") is not None
                and _hp_n < _STATE_DELTA["health_n"]):
            if time.time() - _MONSTER_WARN["ts"] > 30:
                _MONSTER_WARN["ts"] = time.time()
                if _fm is True:
                    lines.append("⚔️ 农场掉血！**本档农场会刷怪**（荒野农场 / 女巫小屋黑暗神龛已开，"
                                 "`farmMonsters=true`）——快回农舍躲屋里，别在场上硬扛")
                elif _fm is False:
                    lines.append(f"⚔️ 农场掉血（{_hp_n}）——但本档 `farmMonsters=false`（农场**不刷怪**），"
                                 f"是别的伤害（炸弹/摔落之类），不是怪；血少就进屋歇")
                else:
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
                         "吃食物(eat_item) / 泡温泉(map_go 温泉→大厅(2,4)或(7,4)面0推更衣室门→更衣室走到底行往下蹭进泳池，站水里泡回体力) / 躺床上(go_sleep 不确认，in_Bed 恢复) 直到健康"
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
    #    🪑 坐着时不报（要走去捡的东西，坐着够不着；且 _forage_summary 有"切图才扫"的副作用，
    #    坐着跳过正好不动它的缓存状态）
    forage = "" if _sitting_now else _forage_summary(is_green_rain=(w == 7))
    if forage:
        lines.append(forage)

    # ── 📦 本图迷你出货箱（恒 2026-09-12："也可以报一下这个出货箱让 AI 知道能用"）──
    #    跟 _forage_summary 同款"切图才扫一次"；背包满时它是"就地清包继续干活"的路子。坐着一并跳过。
    try:
        _sb = "" if _sitting_now else _shipbin_hint(loc_name)
        if _sb:
            lines.append(_sb)
    except Exception:
        pass

    # ── 🪑 附近可坐物（恒 2026-09-10；7 格内有椅子才出，变化才报；坐=scene sit / 起身=scene stand） ──
    try:
        _sh = _sit_hint()
        if _sh:
            lines.append(_sh)
    except Exception:
        pass

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
            lines.append(f"💬 {_attributed(dialog, _speaker_of(active_menu))}")
        # 对话选项主动呈现给 AI，让它自己选（不自动跳过剧情/选项）
        responses = active_menu.get("responses")
        if responses:
            # ⚠️ 游戏给的是**折过行**的 responseText（中文逐字断行）⇒ 必须摊平，
            #    否则状态条里一行塞一个字（2026-09-12 真机）
            opts = [f"[{i}]{_opt_text(t)}"
                    for i, t in enumerate(responses)]
            lines.append(f"🗳️ 选项: {' | '.join(opts)}")
            # 🎪 2026-09-13 恒："怕 AI 实际不知道怎么选"。老文案在节日里会**点空**：
            #   C# `Game1.CurrentEvent != null && !real` 会把不带 real 的 option 走成
            #   `event.answerDialogueQuestion`，对 `createQuestionDialogue`（跳舞邀请/节日摊位问句）
            #   **点不中真回调**（2026-08-23 恒实测）。
            # ⚠️ **但不硬改成一律 real=true**：反编译 `DialogueBox.receiveLeftClick:371-398` 显示
            #   节日里**两种问句框并存**、真实点击走哪条取决于 `afterQuestion`：
            #     `createQuestionDialogue`（afterQuestion≠null）⇒ 必须 real=true
            #     event 问句（「什么事？」等）        ⇒ 走 answerDialogueQuestion（real=false）
            #   而 `/state` **看不出是哪种** ⇒ 只能"先普通点、框不关再加 real=true"，
            #   **不断言**（真修法=C# 把 `afterQuestion` 是否为空报出来，别让消费侧猜）。
            # 判据看 C# 报的 `questionKind`（见 `BuildQuestionKind`），**别在消费侧猜**。
            _qk = active_menu.get("questionKind")
            if _qk == "ask":
                lines.append("→ 用 menu click(option=N, **real=true**) 选择")
            elif _qk == "npc":
                # ✅ 同 `_menu_advice`：**也要 real=true**（见那里的反编译 + 真机实证）
                lines.append("→ 用 menu click(option=N, **real=true**) 选择")
            elif active_event:
                # 旧 DLL 不报 questionKind：如实说明 + 给补救动作（两种问句框在节日里并存）
                lines.append("→ 用 menu click(option=N) 选择；**框不关就加 real=true 再点一次**（此 DLL 不报框种类）")
            else:
                lines.append("→ 用 menu click(option=N) 选择")

    if active_event:
        ev_msg = active_event.get("message")
        ev_id = active_event.get("id")
        # 🎬 2026-09-16 恒：「可跳过」光说不给动作=白说——AI 只会一句句 advance。
        #    现在把**怎么跳**写出来（menu skip），并让"无台词的事件"也一样能跳。
        skip_hint = "（可跳过 → `menu skip` 整段跳；想一句句看就接着 `menu advance`）" \
            if active_event.get("skippable") else ""
        if ev_msg:
            lines.append(f"🎬 演出中: {_attributed(ev_msg, _speaker_of(active_menu))}{skip_hint}")
        elif ev_id not in (None, "-1"):
            lines.append(f"🎬 事件中: id={ev_id}{skip_hint}")

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

    # 🎓 精通山洞洞内引导（2026-09-16）——只在 MasteryCave 出，其它图返回空
    try:
        _mch = _mastery_cave_hint(loc_name)
        if _mch:
            lines.append(_mch)
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
        # ⚠️ 台词进缓冲时已由 `_attributed` 署好名**且自带「」** ⇒ 这里别再包一层，
        #    否则出 `「罗宾「…」」`（2026-09-11 真机当场看到的）
        joined = " ".join(_story_buffer[-6:])
        # 2026-09-11：advance_story 收进 menu 域后，状态条得自己把入口点出来（否则 AI 只看到台词不知道调啥）
        # ⚠️ 2026-09-12 改口径：原先尾巴恒写「台词未完 → menu advance 继续」——**只要缓冲非空它就恒这么说**，
        #    而节日期间事件整场在播、指针常年停在自由活动段（真机 21/24）⇒ 那是句**恒真的假消息**，
        #    会把 AI 骗去反复空推。改成**只报"要新台词该调什么"，不再断言剧情没完**；上次推进卡住就在尾巴点名。
        # 🎬 2026-09-16 恒：这里也把"整段跳过"的入口一并给出来（光给 advance = 只给一种选择）。
        _tail = "（要看新台词 → menu advance；不想看这段 → menu skip 整段跳）"
        if _ADV_NOTE.get("stuck"):
            _tail = f"（上次推不动：command {_ADV_NOTE['cmd']}/{_ADV_NOTE['cmd_count']} 停住，别再空推）"
        lines.append(f"🎬 剧情: {joined}{'…' if len(_story_buffer) > 6 else ''}{_tail}")
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
# 🌿 2026-09-12（恒拍板）：「拾取白名单可以考虑加所有可拾取物品了。贝壳啊水果啊我们也都没有加」
#    ⇒ **删掉`_NONPASS_FORAGE`名单，改问游戏** —— `/surroundings` 现在每格带 `forage`
#    （C# 侧读 `Object.isForage()`，反编译 `Object.cs:2806`：`Category ∈ {-79,-81,-80,-75,-23}`
#     或带 tag `forage_item`，外加硬编码 `(O)430`=松露）。一份判据盖住 野菜/浆果/水果/贝壳/海胆/
#    珊瑚/松露 全类，再不用玩"漏一个补一个"的打地鼠（松露/海胆/珊瑚当年各补过一次）。
#    ⚠️ 旧 DLL 没这字段 ⇒ 回到老行为（只有松露那条硬编码特例会报）；`pickup_scene.py` 那边
#      用 `/status.build` 显式查版本并**明说**，不让"能力少一截"静默发生。
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


# 📦 迷你出货箱提示（2026-09-12 恒："也可以报一下这个出货箱让 AI 知道能用"）
_SHIPBIN_SEEN = {"loc": None, "line": ""}


# 🎓 精通山洞洞内引导（2026-09-16 恒：「在精通山洞内时，做好精通领取流程的引导」）。
#   为什么值得单独写一个：`MasteryCave` 原先在 `MAP_FEATURES` 里**连键都没有**，洞内状态条一行不提示；
#   而洞里的正事（摸哪块碑、还剩几个可领、领完干嘛）**看地图看不出来 —— 五块石碑长得一模一样**。
#   ⚠️ 挂在状态条**尾块**（跟 `_machine_ready_hint` / `_shipbin_hint` 同一层）⇒ 自动吃到 `_OPS_INNER`
#      守卫，只在最外层出现一次，不会在域 op 内层被重复注入。
#   ⚠️ 节流：`/mastery` 是要过 HTTP 的，站着不动时没必要每个工具调用都问一遍。
#      这里用「同图 + 15 秒内不重扫」。**要最新值就 `check mastery`** —— 那条路径不缓存，永远实时。
_MASTERY_CAVE_SEEN = {"loc": "", "ts": 0.0, "line": ""}
_MASTERY_CAVE_TTL = 15.0


def _mastery_cave_hint(loc_name: str = "") -> str:
    """🎓 站在精通山洞里时的领取引导：五碑各自状态 + 下一步。其它地图返回 ""。"""
    if (loc_name or "") != "MasteryCave":
        return ""
    now = time.time()
    if _MASTERY_CAVE_SEEN["loc"] == loc_name and now - _MASTERY_CAVE_SEEN["ts"] < _MASTERY_CAVE_TTL:
        return _MASTERY_CAVE_SEEN["line"]
    _MASTERY_CAVE_SEEN["loc"] = loc_name
    _MASTERY_CAVE_SEEN["ts"] = now

    line = ""
    try:
        r = api.mastery()
        if r.get("ok"):
            can_claim = bool(r.get("canClaim"))
            unspent = r.get("unspent") or 0
            plaques = {(p.get("skill") or "").lower(): p for p in (r.get("plaques") or [])}
            marks = []
            for sk in _MASTERY_CAVE_ORDER:
                p = plaques.get(sk) or {}
                mark = "✅" if p.get("claimed") else ("🟢" if can_claim else "⚪")
                marks.append(f"{mark}{p.get('cn') or sk}")
            head = f"🎓 精通山洞 · 五碑(左→右)：{' '.join(marks)}"
            if can_claim:
                tail = (f"有 {unspent} 个可以领 → 走到 🟢 那块碑前 `interact` 开菜单 → "
                        f"`menu read` 看给什么 → `menu click(button=mainButton)` 领取")
            elif plaques and all(p.get("claimed") for p in plaques.values()):
                tail = "五块全领完了。出洞：走到 (7,12) 自动回森林"
            else:
                tail = "暂时没有可领名额（先攒精通经验）。中央基座 (7,9) 看总进度；出洞走 (7,12)"
            line = f"{head}\n  {tail}"
        else:
            line = "🎓 在精通山洞：`check mastery` 查五碑状态（端点报错，模组可能需重编译）"
    except Exception:
        line = "🎓 在精通山洞：`check mastery` 查五碑状态（读不到 /mastery）"
    _MASTERY_CAVE_SEEN["line"] = line
    return line


def _shipbin_hint(loc_name: str = "") -> str:
    """📦 本图的迷你出货箱 —— **切图才扫一次**（同 `_forage_summary` 那套节流，别每调都刷 /surroundings）。

    为什么值得专门报一句：它容量只有 **9 格**（`Chest.GetActualCapacity()`，Chest.cs:895），
    但**当夜由房主进程遍历所有地图结算**（`Game1.cs:7981`）⇒ **背包满了不用把人赶回农场的出货箱**，
    就地清包继续干活。恒海滩上放的那个就是为了这个。

    ⚠️ 三个坑写在这里免得后人重踩：
      · 它**被 `IsStorageChest` 排除**在"存储箱"之外（`ModEntry.cs`）——那是**故意的**：`storage store`
        的"智能归位"要是把东西归进出货箱，等于**偷偷把它卖了**。所以只有**显式点坐标**才认
        （C# 侧 `FindMiniShippingBinAt`），提示里把 target 写法写全，别让 AI 白试。
      · 想"走过去 interact 开它"**不行**：`Chest.cs:773` 的 `playerChest` 分支要
        `Game1.didPlayerJustRightClick()`（真鼠标右键），action 键直接 `return false`。
        本项目家具/鱼塘/蟹笼都栽过同一句，各有 bypass —— 但这里不用绕，直接写进箱子即可。
    ⚠️⚠️ **有一条事实故意不写进提示**（2026-09-12 恒，两句话要连起来读）：
      · 事实（他先纠正我）："**mini 出货箱不像出货箱只能撤回最后一个，它放进去的 9 格都可以在过夜前
        重新拿出来**"——反编译对得上：迷你出货箱开 `ItemGrabMenu(GetItemsForPlayer(), …,
        showReceivingMenu: **true**, …)`（Chest.cs:925）⇒ **箱内 9 格作为可抓取容器列出来、整袋可取回**；
        农场出货箱开 `ItemGrabMenu(**null**, …, showReceivingMenu: **false**, …)`（`ShippingBin.cs:267`，
        源箱子传 null）⇒ 看不到箱内容、只有"撤回刚放进去那件"。
      · 但**别拿它当卖点**（他紧跟着纠正我第二轮）："**感觉你这样说会引导 AI 当溢出背包用呢。最好还是不要，
        怕忘在里面了。出货箱就是用来出货的，用 50 个木头搞个箱子放这儿又不麻烦。**"
        ⇒ 这个能力**是真的，但用错方向**：靠"过夜前能取回"来清包，等于把"记得回来拿"押在 AI 的记忆上，
        忘了就真出货了。**提示里只讲"出货"，要存货就指普通箱子**（50 木一个，本 mod 能建）。
    """
    if not loc_name:
        return ""
    if _SHIPBIN_SEEN["loc"] == loc_name:
        return _SHIPBIN_SEEN["line"]        # 同图不重扫
    _SHIPBIN_SEEN["loc"] = loc_name
    line = ""
    try:
        r = api.surroundings(30)
        bins = [t for t in (r.get("tiles") or []) if (t.get("object") or "") == "Mini-Shipping Bin"]
        if bins:
            c = r.get("center") or {}
            cx, cy = c.get("x", 0), c.get("y", 0)
            bins.sort(key=lambda t: abs(t.get("x", 0) - cx) + abs(t.get("y", 0) - cy))
            b = bins[0]
            line = (f"📦 本图迷你出货箱 ({b['x']},{b['y']})｜9 格·**当夜出货卖掉**，别当仓库"
                    f"（要存货用普通箱子）→ storage(ops=\"store\", target=\"{b['x']},{b['y']}\", all=True)")
    except Exception:
        line = ""                           # 读不到就不报（不瞎猜坐标）
    _SHIPBIN_SEEN["line"] = line
    return line


def _forage_summary(is_green_rain: bool = None) -> str:
    """🌿 当前地图可采集物汇总（有什么、几颗）。**切图时扫一次** + 室内跳过。
    只报告数量不报位置——决定采集后走 pickup_scene 自动走过去捡。
    is_green_rain: 是否绿雨天（None=自动查 weather==7）。苔藓类只在绿雨当天暴露，
    除非设置 moss expose_all_days=on（2026-08-21 恒）。"""
    global _last_forage_loc
    # ⚠️ 2026-09-12 真机复现（同 _sit_hint 那个坑，这是漏网的一条）：域 op 的内嵌状态条会被
    #    `_ops_run` 整条砍掉（见 `_OPS_INNER`），可这层**照样把缓存消费掉了**——
    #    `map go` 一落到新图就先把这里扫完、`_last_forage_loc` 置成新图名，外层重建时判定
    #    "同图，不重扫" → 「🌿 可采集」整行对 AI **失踪**。
    #    实证：从 Forest 走回 Farm 落 (40,64)，隔壁 (31,43) 有猪拱的松露；直接调本函数返回
    #    `🌿 可采集: 🍄松露×1`，可 map_go 的回包里一个字都没有。
    #    照 _sit_hint 的成例：这层闭嘴且**不消费**，把扫留给外层（外层 `_OPS_INNER` 已归零）。
    if _OPS_INNER["n"] > 0:
        return ""
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
        spot_count = 0      # 🪱 斑点 (O)590 Artifact Spot + (O)SeedSpot Seed Spot（锄；俩中文名都叫「远古斑点」）
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
            # 🌿 2026-09-12：不可站、但**游戏说可手捡**的（水果/贝壳/海胆/珊瑚…）照报 —— 判据是
            #    C# 抄来的 `Object.isForage()`，不再是本地名单（名单的由来与坑见上面 `_NONPASS_FORAGE` 注释处）。
            if not t.get("passable", True) and t.get("forage"):
                obj = t.get("object")
                if obj and not any(blk in obj for blk in _FORAGE_BLACKLIST):
                    counts[obj] = counts.get(obj, 0) + 1
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


def _speaker_of(active_menu) -> str:
    """说话人显示名（"罗宾"），取不到返空串——**不猜、不兜底**。
    2026-09-11 恒："现在的对话没有把名字给 AI，不知道是谁在说话。"
    源头是 C# 那侧读错了字段（`DialogueBox.character` 在 1.6 不存在），已修成
    `characterDialogue.speaker.getName()`；本函数只是把它取出来。
    ⚠️ 旁白 / 信件 / 纯提示框（string 构造器的 DialogueBox）**本来就没有说话人** ⇒ 返空串，
    渲染时不署名——宁可不署名，也不要认错人。"""
    try:
        return ((active_menu or {}).get("speaker") or "").strip()
    except Exception:
        return ""


def _attributed(text: str, speaker: str) -> str:
    """把台词署上说话人：有名 → `罗宾「台词」`，无名 → `「台词」`。
    ⚠️ 游戏的 `getCurrentString()` 在**关闭头像显示**时会自己加 `名字: ` 前缀
    （DialogueBox.cs:719 `if (!Game1.options.showPortraits)`）⇒ 先判重，别拼成 `罗宾：罗宾：…`。"""
    if not speaker:
        return f"「{text}」"
    if text.startswith(speaker) or text.startswith(f"{speaker}:"):
        return f"「{text}」"
    return f"{speaker}「{text}」"


# 🎬 事件推进预算（2026-09-12 恒）——修的是真机事故：旧版 15 步 × 0.7s，而**节日期间
#    `activeEvent` 整场恒在播** ⇒ `menu advance` 真机磨了 **139 秒**；同步工具跑在事件循环上
#    ⇒ 这 139 秒整个 :8000 不响应（连 GET / 都超时），还把冰钓比赛那 120 秒**整个吞在调用里**，
#    等到返回时 festivalTimer 已归零、自动钓鱼 hook 压根没看见开赛（当年那场比赛 0 条）。
EVENT_BUDGET_S = 20.0     # 单次调用墙钟上限：到点带已收台词返回，AI 想继续就再调一次（边界 = hook 的机会）
EVENT_STALL_S = 8.0       # 进度指针 + 台词都不动这么久才算"卡住"（`pause` 命令期间指针本来就不动）
# 最近一次推进的结局：给 `menu advance` 的返回文案用（卡住要**出声**，别让 AI 不明不白地反复空推）
# ⚠️ 2026-09-12 真机补：只报「推不动」读起来像**什么都没干**——实际它可能刚推完 5 句台词才停下。
#    ⇒ 多记 `pushed`（推了几次）/`lines`（收到几句新台词），文案**先说干了多少、再说停**。
_ADV_NOTE = {"stuck": False, "why": "", "cmd": -1, "cmd_count": -1, "pushed": 0, "lines": 0}


def _advance_story(active_menu, active_event) -> bool:
    """剧情自动走：事件/对话自动推进并缓存台词，到选项/结束/卡住停下。

    🔑 判据（2026-09-12 恒拍板，反编译 Event.cs:3781/3788/3810/3914/3949）——**看进度指针，不掐动画时长**：
      · 有对话（responseCount=0）              → key confirm 推进（进程内按键，不碰 OS 鼠标）
      · 有选项（responseCount>0）              → **立刻停**，交给 AI 选
      · 无对话 + 指针在动                      → **静默动画在播**（走路/转场/演出）：**什么都别按**，等着
      · 无对话 + 指针不动 ≥ EVENT_STALL_S + skippable → 有跳过键 ⇒ 按 skip 跳过这段演出
      · 无对话 + 指针不动 ≥ EVENT_STALL_S + 无跳过键  → 轻推 confirm；推两次仍不动 ⇒ **收工报"卡住"**
      · `festivalTimer>0`（冰钓/找蛋等限时小游戏）    → 立刻退，玩家已接管
    ⚠️ 旧版"20 秒没进展就**盲按 Escape**"已删——节日里那等于把菜单按开/按乱（Escape 在没有菜单时
       是切菜单的键）；现在**只有确实有跳过键**才按 skip。
    返回 True = 本轮推过；推到哪 / 卡没卡看 `_ADV_NOTE`。无事件 → 退回 `_dismiss_dialogue`。
    """
    if active_event and active_event.get("id"):
        # ⚠️ 每次进事件分支先**清账**：不清的话上次那次的 `stuck=True` 会一直粘着，
        #    状态条尾巴就永远挂着"别再空推"（2026-09-12 自查抓到的陈旧状态 bug）
        _ADV_NOTE.update({"stuck": False, "why": "", "cmd": -1, "cmd_count": -1, "pushed": 0, "lines": 0})
        try:
            if _festival_timer() > 0:
                return True                      # 限时小游戏进行中 → 玩家已接管
        except Exception:
            pass
        try:
            deadline = time.time() + EVENT_BUDGET_S
            advanced = 0
            last_line = ""
            last_cmd = None
            stall_since = time.time()
            nudges = 0
            while time.time() < deadline:
                try:
                    es = api.event_state()       # 便宜：不枚举 NPC/背包，可以按 0.4s 轮询
                except Exception:
                    es = {}
                if not es.get("eventId"):
                    break                        # 事件结束
                _ADV_NOTE["cmd"] = es.get("currentCommand", -1)
                _ADV_NOTE["cmd_count"] = es.get("commandCount", -1)
                if int(es.get("festivalTimer") or -1) > 0:
                    return advanced > 0          # 限时小游戏开赛 → 让位给玩家/脚本
                # 💬 收台词（⚠️ 先去重再署名：缓冲里存的是署好名的，拿原始 line 比永远不等 ⇒ 会重复两遍）
                line = (es.get("message") or "").strip()
                if line and line != last_line:
                    last_line = line
                    try:
                        m_now = api.state(light=True).get("activeMenu") or {}
                    except Exception:
                        m_now = active_menu
                    entry = _attributed(line, _speaker_of(m_now))
                    if entry and (not _story_buffer or _story_buffer[-1] != entry):
                        _story_buffer.append(entry)
                        _ADV_NOTE["lines"] += 1
                if int(es.get("responseCount") or 0) > 0:
                    break                        # 选项 → 停，让 AI 选（confirm 选不了选项）
                if es.get("hasDialogue"):
                    # 💬 **对话在等按键**：立刻 confirm。
                    # ⚠️ 这条不能省——对话期间 `currentCommand` 本来就不动（对话不是事件命令），
                    #    若按"指针停住"处理会白白等满 EVENT_STALL_S 才推一句（5 句台词 = 40 秒）。
                    api.key("confirm")
                    advanced += 1
                    _ADV_NOTE["pushed"] = advanced
                    stall_since = time.time()
                    nudges = 0
                    time.sleep(0.5)
                    continue
                # 📍 进度判据：**指针动** 或 台词换了 = 在推进
                cmd = es.get("currentCommand")
                if cmd != last_cmd:
                    last_cmd = cmd
                    stall_since = time.time()
                    nudges = 0
                if (time.time() - stall_since) < EVENT_STALL_S:
                    time.sleep(0.4)              # 静默动画在播（走路/演出）→ **什么都别按**
                    continue
                # 指针真停住了：有跳过键才敢 skip（演出段），否则轻推 confirm，推两次不动就认输
                if es.get("skippable"):
                    api.key("skip")
                    advanced += 1
                    _ADV_NOTE["pushed"] = advanced
                    time.sleep(0.8)
                    stall_since = time.time()
                    continue
                if nudges < 2:
                    api.key("confirm")
                    nudges += 1
                    advanced += 1
                    _ADV_NOTE["pushed"] = advanced
                    time.sleep(0.6)
                    continue
                _ADV_NOTE["stuck"] = True
                _ADV_NOTE["why"] = (f"事件 command {_ADV_NOTE['cmd']}/{_ADV_NOTE['cmd_count']} 停住、"
                                    "没跳过键、轻推 2 次也不动")
                break
            return advanced > 0
        except Exception:
            return False
    return _dismiss_dialogue(active_menu)


@mcp.tool()
def advance_story() -> str:
    """🎬 推进剧情/对话（menu ops="advance" 的子函数，2026-09-11 起不再占顶层工具槽）。
    卡剧情/不知道按啥时调 `menu advance`：检测事件还在 → 自动走完当前段 → 返回台词+状态。
    事件对话自动 /click，选项出现停下让 AI 选。"""
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
        # ⚠️ 节日期间 activeEvent 恒在播 —— 旧文案「事件仍在播，再调继续」在这时候是**恒真的假消息**，
        #    会骗着 AI 反复空推。现在带上进度指针 + 卡住状态，让"到底还有没有可推的"看得见。
        if _ADV_NOTE.get("stuck"):
            # 💬 **先说干了多少、再说停**（恒 2026-09-12 真机反馈：只报"推不动"读起来像什么都没干，
            #    实际它刚把五句开赛台词全收完了）。pushed=推了几次，lines=收到几句新台词。
            _did = (f"（本次推了 {_ADV_NOTE['pushed']} 次、收到 {_ADV_NOTE['lines']} 句台词）"
                    if (_ADV_NOTE["pushed"] or _ADV_NOTE["lines"]) else "（这次一步都没推）")
            lines.append(f"  🛑 推不动了{_did}：{_ADV_NOTE['why']}。**别反复空推**——可能这段是演出"
                         "（等一会儿再调）、或需要人点屏幕；卡住就找 user。")
        elif ev2.get("id"):
            lines.append(f"  ⏳ 事件还在播（command {_ADV_NOTE['cmd']}/{_ADV_NOTE['cmd_count']}，"
                         f"本次推 {_ADV_NOTE['pushed']} 次/收 {_ADV_NOTE['lines']} 句），"
                         "要接着推就再调 menu advance"
                         + ("；不想看这段就 `menu skip` 整段跳" if ev2.get("skippable") else ""))
        elif m2.get("type") == "DialogueBox" and m2.get("responses"):
            # 别把原始 dict 甩给 AI（以前是一串 {"index":0,"key":...}），摊平成 [N]文本
            _o = " | ".join(f"[{i}]{_opt_text(t)}"
                            for i, t in enumerate(m2["responses"]))
            lines.append(f"  💬 出现选项: {_o} → menu click(option=N) 选择")
        elif m2.get("type") == "DialogueBox":
            lines.append("  💬 对话继续，再调 menu advance")
        else:
            lines.append("  ✅ 剧情结束")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 推进剧情失败: {e}")


@mcp.tool()
def skip_event() -> str:
    """⏭️ 整段跳过当前剧情/事件（menu ops="skip" 的子函数，2026-09-16 恒）。
    和 `menu advance`（一句句看完）互补：不想看这段就走这个，直接结束、控制权交回。
    底层=游戏原生跳过（C# `/key key=skip` → `currentEvent.skipEvent()`），只对
    **事件**生效；没事件时**明确报错不做旁的事**（不假装跳过、也不顺手关掉菜单）。"""
    try:
        st = api.state(light=True)
        ev = st.get("activeEvent") or {}
        m = st.get("activeMenu") or {}
        if not ev.get("id"):
            # 宁报错别兜底：没事件就直说，别让 "skip" 静默变成"关菜单"这种别的事。
            if m.get("type") == "DialogueBox":
                return _with_state("⏭️ 没跳过：现在只有普通对话框、没有事件。"
                                   "想一句句看完用 `menu advance`；要关掉它用 `menu cancel`")
            return _with_state("⏭️ 没跳过：当前没有剧情/事件在播（无事可跳）")
        if ev.get("skippable") is False:
            return _with_state(f"⏭️ 没跳过：这段剧情**不可跳过**（skippable=false，id={ev.get('id')}），"
                               "只能 `menu advance` 一句句推")
        api.key("skip")
        time.sleep(0.4)
        ev2 = (api.state(light=True).get("activeEvent") or {})
        if ev2.get("id"):
            # 别只报"按键已发送"——回读确认，没跳掉就说没跳掉（恒：工具说成功但事没发生最坑）。
            return _with_state(f"⏭️ 发了跳过键但事件还在播（id={ev2.get('id')}）——这段可能跳不动。"
                               "再调一次 `menu skip`，或 `menu advance` 一句句推")
        return _with_state("⏭️ 已跳过当前剧情/事件，控制权交回（该干嘛干嘛去）")
    except Exception as e:
        return _with_state(f"❌ 跳过剧情失败: {e}")


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
            # ⚠️ 去重比 entry（同 _advance_story）：缓冲存的是署名后的，别拿原始 line 比
            entry = _attributed(line, _speaker_of(active_menu))
            if line and (not _story_buffer or _story_buffer[-1] != entry):
                _story_buffer.append(entry)
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

    # 🔒 2026-09-12 恒拍板（"内层压根不建状态条"，一处根治全族）：**域 op 内部调用本函数时直接返回正文**。
    #    域 op 全是 `return _with_state(正文)`，可这层产物会被 `_ops_run` **整条砍掉**（只留正文，见 `_OPS_INNER`
    #    注释）——以前是"照建、照丢"，于是把**带副作用的消费**白白喂了进去，一次踩了一族：
    #      · `_gather_state(consume_events=True)` → 📰 小新闻 / 警报 被内层 drain，AI 永远看不到
    #      · `_is_new_day()` 吃掉 `_last_full_date` → 那天 🌅晨报/🎲运势 **再也不出现**（只有 `check status` 能补）
    #      · `_statue_reminder` 置 `shown=True` → 当天不再提醒摸雕像（**不可逆，少一天 buff**）
    #      · `_machine_ready_hint` 写 `_MACHINE_READY_SEEN` → 那批"就绪"不报（可逆，等产物再变才重报）
    #      · `_plan_drain_notices()` 被 drain、`_chat_phase_line` 等睡开场引导被吃、
    #        `_maybe_ice_fishing_auto` / `_maybe_egg_run_auto` 每次调用**跑两遍**
    #    另一笔账：多 op 调用（`farm(ops="till plant water")`）原本**拉 3 次全量 `_gather_state`** 且 3 次全丢，
    #    现在只在外层拉 1 次 —— 少 2 次 HTTP，最坏少 2×8s 的超时干等。
    #    ⚠️ **为什么只改这一处就够，且安全**：
    #      ① 15 个域工具（farm/mine/cabin/scene/menu/…）**无一例外**都是 `return _with_state(_ops_run(...))`
    #         ⇒ 外层**一定**会再调一次把状态条补上；`check` 不走 `_ops_run`（`n` 恒 0），本就不受影响。
    #      ② 内层原本那条**本来就被丢掉**，AI 从来没见过 ⇒ 可见性**只会变好、不会变差**（最坏 = 维持原样）。
    #      ③ 所有 `_STATE_SEP` 消费点都写成 `x.split(_STATE_SEP)[0] if _STATE_SEP in x else x`
    #         （navigation.py 500/1771、本文件 6636/12524/15023）⇒ 拿不到条也走对分支。
    #      ④ `_sit_hint` / `_forage_summary` 里那两道 `if _OPS_INNER["n"] > 0` 闸门**从此到不了**
    #         （留着当第二道保险：万一将来有人撤了这里，它们还能顶上）。
    if _OPS_INNER["n"] > 0:
        return result

    # 🔌 **热路径重探端口↔角色**（`ensure_roles` 有 30s TTL，且 `_probe_role` 先做 TCP 0.3s 快检，
    #    所以代价很小）。放这里是为了让错误映射能**自愈**：2026-09-11 之前只有 go_sleep /
    #    which_role / 计划任务会重探 ⇒ 启动时若撞上"房主已进世界、farmhand 还没加入"的窗口，
    #    折叠成单进程的错误映射会**挂一整个会话**（所有 AI 操作静默打房主，`cabin statue`
    #    当天就是这么把恒的角色走掉的）。放在 `_gather_state` 之前，纠正后本次就用新绑定。
    try:
        api.ensure_roles()
    except Exception:
        pass

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

# 🚀 自动异步白名单（2026-08-16 恒拍板）：便利工具跑这些长脚本 → 自动后台异步，AI 不用手动后台。
# 长任务（钓鱼/挖矿/炸矿/收放机器/浇水可能很久）被动异步；短任务（清地/砍树/摸动物/捡采集等）保持同步。
_ASYNC_SCRIPTS = {"mine_run", "fish_run", "bomb_mine", "bomb_escort", "bomb_volcano",
                  "fruit_round", "machine_loader", "water_crops",
                  # 🌾 2026-09-16 恒：收获改回**拟人**（逐个走位+动手）后一株要 2~4 秒，
                  #    一片地几十株就是好几分钟 ⇒ 必须进白名单转后台（同 harvest 的老问题）。
                  "scythe_crops"}


def async_config(show: bool = False, add: str = "", remove: str = "", enable: str = "") -> str:
    """🚀 异步配置（长脚本自动后台=被动异步，AI 不用手动后台）。show 看白名单+开关 / add·remove 改白名单(name,不带.py) / enable on|off(同 settings async_tools)。细节→help(scripts)。

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
    lines.append("  白名单脚本便利工具（go_fishing/mine_run/bomb_mine 等）自动后台跑，AI 不用手动后台")
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
    # 🚀 长脚本自动异步（2026-08-16 恒拍板：被动异步，AI 不用手动后台）
    if async_ok and name in _ASYNC_SCRIPTS and _bg_cfg.get("enabled", True):
        try:
            job, err = _bg_start(name, list(args_list) if args_list else [])
        except Exception as e:
            job, err = None, f"❌ 后台启动失败: {e}"
        if err:
            return err
        return (f"🚀 已后台启动「{name} {' '.join(args_list) if args_list else ''}」→ job {job.job_id}\n"
                f"  收工会自动播报（含总时长）   停止: script(ops=\"stop\", kw={{\"job_id\":\"{job.job_id}\"}})")

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
        # ⚠️ 2026-09-16：`api.warp` 的回包**早于 warp 生效**（实测回包 `actual` 还写着旧图），
        #    固定 sleep 之后调用方若立刻读**位置相关**的端点（/animals、/map 之类）会读到旧图 ⇒
        #    判据当场失真。改成轮询确认，拿不准也别撒谎说"已到"。
        if not _wait_on_map(loc_name, timeout=6):
            return f"⚠️ warp 到 {loc_name} 后没能确认已经站上去（后续读数可能还是旧图）→ "
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


def _split_surr_chars(surr: dict) -> tuple:
    """把 `/surroundings` 的 `npcs` 按 `kind` 拆开 → (真人 NPC, 宠物猫狗, 马)。

    ⚠️ 2026-09-11 恒：`npcs` 里**混着 Pet / Horse**（C# 的 `kind` 字段标着，见 ModEntry 的
    `kind = n is Pet ? "pet" : n is Horse ? "horse" : "npc"`）。它们**不是 NPC** ——
    把自家的猫印成「👤 NPC」，AI 会当成一个能搭话的人；`chat_npc` 更会真的走过去"跟猫说话"。
    旧 DLL 没有 `kind` 字段 → 默认当 `npc`（宁可多报真人，也不把真人藏起来）。
    """
    npcs = surr.get("npcs") or []
    real = [n for n in npcs if (n.get("kind") or "npc") == "npc"]
    pets = [n for n in npcs if n.get("kind") == "pet"]
    horses = [n for n in npcs if n.get("kind") == "horse"]
    return real, pets, horses


@mcp.tool()
def look_around(radius: int = 10) -> str:
    """👀 观察周围环境
    扫描指定半径内的 NPC、宠物/马、牲畜、怪物、物品、地形、作物。

    Args:
        radius: 扫描半径（格数，默认 10，最大 20）
    """
    radius = min(radius, 20)
    try:
        surr = api.surroundings(radius)
        tiles = surr.get("tiles", [])
        npcs, pets, horses = _split_surr_chars(surr)
        farm_animals = surr.get("animals") or []      # 🐄 牲畜（牛/羊/鸡/鸭），和 NPC 完全两回事
        monsters = surr.get("monsters", [])

        # 统计感兴趣的东西
        obj_tiles = [t for t in tiles if t.get("object")]
        crop_tiles = [t for t in tiles if t.get("crop")]
        terrain_trees = [t for t in tiles if t.get("terrain") and "Tree" in str(t.get("terrain", ""))]

        lines = [f"👀 半径 {radius} 格内："]

        if npcs:
            npc_list = [n.get("name", "?") for n in npcs]
            lines.append(f"  👤 NPC: {', '.join(npc_list)}")
        if pets:
            lines.append(f"  🐾 宠物: {', '.join(n.get('name', '?') for n in pets)}（自家猫狗，不能搭话）")
        if horses:
            lines.append(f"  🐴 马: {', '.join(n.get('name', '?') for n in horses)}")
        if farm_animals:
            lines.append(f"  🐄 牲畜: {', '.join(str(a.get('name', '?')) for a in farm_animals)}")

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


@mcp.tool()
def walk_to(poi_name: str = "", x: int = None, y: int = None) -> str:
    """🚶 导航到指定地点（POI 落点）—— **也可直接给坐标 x/y**（同图精确走位）
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
        x, y:     同图目标坐标（与 poi_name 二选一）——原 `movetile` 的能力，2026-09-11 并入
    """
    return navigation.walk_to(poi_name, x, y)


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


@mcp.tool()
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
    return navigation.go_to(place)


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


# ℹ️ INTERIOR_EXIT_APPROACH 固定表已于 2026-08-30 删除——_exit_farm_building 改读 /map 原生 warp 瓦片，
#    任何室内建筑/任意档位通用，不再按尺寸查表。


# 🎓 精通领取门禁（2026-09-16 改判据：**问游戏**）
#
# 历史：2026-08-23 想读 /special_items 的 has.mastery_<skill>，实测**不在里面**（specialItems 不含 mastery_*）；
#   于是退而求其次，拿 /craft_recipes 里"已学配方含某个精通奖励配方名"**倒推**——_MASTERY_KEYS 那张
#   四行手抄表就是这么来的。它的毛病：
#     · 手抄（跟被删掉的 crops.py 同一个病）——游戏改奖励名/换语言就静默失效；
#     · fishing 的奖励是**物品**（Advanced Iridium Rod）不是配方，所以只能**写死 return False**，
#       任何钓鱼精通的判断永远是错的；
#     · 奖励变体（如采集精通给两个东西）盖不全。
#
# 现在直读权威源：`/mastery` 报的 `plaques[].claimed` —— 它就是
#   `Game1.player.stats.Get($"mastery_{i}") != 0`（游戏自己的领没领标志，反编译 StatKeys.cs:186）。
#   ⇒ 手抄表连同"猜"的逻辑一起删掉。
#
# TTL 30s 缓存；**读不到 → False**（藏而不拦：旧 DLL 没新端点时不误伤功能，只是门禁暂时不生效）。
_MASTERY_CACHE = {"ts": 0.0, "claimed": None}


def _mastery_claimed(skill: str) -> bool:
    """是否已领取某精通（如 combat=战斗精通→解锁饰品槽/铁砧）。
    skill ∈ farming/fishing/foraging/mining/combat —— **五个都支持**（旧版把 fishing 写死成 False）。
    判据 = AI 进程(7843) `/mastery` 的 plaques[].claimed，即游戏自己的 `mastery_<i>` 统计。"""
    global _MASTERY_CACHE
    key = (skill or "").lower()
    if key not in ("farming", "fishing", "foraging", "mining", "combat"):
        return False
    if _MASTERY_CACHE["claimed"] is not None and time.time() - _MASTERY_CACHE["ts"] < 30:
        return key in _MASTERY_CACHE["claimed"]
    claimed = set()
    try:
        r = api.mastery()          # 已走 _ai_get（7843，AI 自己那份）
        for p in (r.get("plaques") or []):
            if p.get("claimed"):
                claimed.add((p.get("skill") or "").lower())
    except Exception:
        claimed = set()            # 读不到 → 不误伤
    _MASTERY_CACHE = {"ts": time.time(), "claimed": claimed}
    return key in claimed


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


@mcp.tool()
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
    """
    return navigation.map_go(destination, npc)

@mcp.tool()
def warp_safe() -> str:
    """🏠 紧急逃脱：优先 warp 回上次 walk_to/map_go **失败的目标点**（2026-08-29 恒：落在失败点方便续走/救回），
    没失败目标则回安全位（家 homeLocation；没有则 Farm）。
    ⚠️ **仅紧急逃脱/中断兜底**（血低被围、脚本卡死、火山被卡、地图转换失败）——
    日常移动请用 map_go 走真实路径，别拿它当导航。
    """
    return navigation.warp_safe()


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
            # 🦶 2026-09-19：要闪过去扫的是"地块中心"，但那格**可能正摆着洒水器/箱子**
            #    （恒真机："踩到洒水器了"）⇒ 就近挑一个能站的格，差一两格不影响半径 15 的扫描。
            _r = min(max(radius, 1), 30)
            _wk = api.walk_ok_tiles(x - _r, y - _r, x + _r, y + _r)
            _pk = api.stand_near([(px, py) for px in range(x - _r, x + _r + 1)
                                  for py in range(y - _r, y + _r + 1)], _wk, x, y)
            if _pk:
                try:
                    api.position(_pk[0], _pk[1])
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


# ⚠️ C# 送来的 `effect` 其实是**整个物品对象**，里面绝大多数键是"物品元数据"（贴图编号、
#    能否重铸…），不是"这件饰品干什么用的"。元数据对 AI 是纯噪声，白名单式地剔掉。
#    ⏳ 正解在 C#：直接送一句人话效果描述（要重编 DLL，等游戏关了做）。
_TRINKET_META_KEYS = {
    "DisplayName", "Description", "Texture", "SheetIndex", "ParentSheetIndex",
    "TrinketEffectClass", "DropsNaturally", "CanBeReforged", "CustomFields", "ModData",
    "Name", "name", "Type", "Category", "Quality", "Stack", "Price",
}


def _readable_effect(v) -> bool:
    """饰品/装备的 effect 取值是不是"人话"：够短，且不含 .NET 反射/资源路径/本地化串。
    判据来自 2026-09-11 的实测噪声样本（`[LocalizedText Strings\\…`、`TileSheets\\…`、
    `StardewValley.Objects.…`）——这类东西对 AI 是纯干扰。"""
    s = str(v)
    if len(s) > 24:
        return False
    return not any(x in s for x in ("LocalizedText", "\\", "[", "StardewValley.", "Strings"))


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
            # 🐛 2026-09-11（空上下文文案评审挖出）：C# 的 `effect` 是**整个物品对象序列化**，
            #    原样 join 会把 .NET 反射噪声整坨甩给 AI，实测 400+ 字符全是废话：
            #      `DisplayName=[LocalizedText Strings\1_6_Strings:FairyBox_Name], Texture=TileSheets\Objects_2,
            #       TrinketEffectClass=StardewValley.Objects.Trinkets.FairyBoxTrinketEffect, …`
            #    只留"人话"取值（短、且不含路径/本地化/类型名）；全被滤掉就只报名字——
            #    那也比一坨噪声强（其余穿戴行本来就只报名字，这样才一致）。
            #    ⏳ 正解是 C# 侧直接给一句效果描述（要重编 DLL，等游戏关了一起做）。
            eff_str = ", ".join(
                f"{k}={v}" for k, v in eff.items()
                if isinstance(eff, dict) and not str(k).startswith("<")
                and k not in _TRINKET_META_KEYS and _readable_effect(v)
            )
            lines.append(f"🔮 饰品: {t['name']}" + (f" ({eff_str})" if eff_str else ""))
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
    （位置**以房主那端为准**；万一房主这端和轮回自己记的不一样，会另起一行把差异摊开给你看。）

    Args:
        name: NPC 名字（中文名如"莉亚"，或英文名"Leah"，支持子串）
    """
    try:
        # ⚠️ 走 `api.find_npc`（**以 host 为准**），不是 `api._get`：后者打的是轮回**自己**那个进程，
        #    而 farmhand 那端的 NPC 会滞留"各人的家"（2026-09-19 真机：Alex 明明站在沙滩上，
        #    自己这端报 JoshHouse）。
        r = api.find_npc(name)
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', r)}")
        nps = r.get("npcs", [])
        stale = r.get("stale", [])
        if not nps:
            return _with_state(f"🔍 没找到「{name}」——可能出门在外或名字不对")
        lines = []
        for n in nps[:8]:
            d = n.get("dialogue")
            extra = f" 💬「{d}」" if d else ""
            sleep = " 💤在睡" if n.get("isSleeping") else ""
            lines.append(f"  {n.get('displayName') or n.get('name')} @ {n.get('location')} ({n['x']},{n['y']}){sleep}{extra}")
        # ⚠️ 两端不一致**摊开说**（宁报错别兜底）：只列 host 没有的那几条，不静默丢
        tail = ""
        if r.get("src") == "self":
            tail = "\n⚠️ 这只有**轮回自己这端**的记录（房主那端没答上来）——远处的人可能是滞留的旧位置"
        elif stale:
            _s = "、".join(f"{n.get('displayName') or n.get('name')}@{n.get('location')}({n['x']},{n['y']})"
                           for n in stale[:3])
            tail = f"\n⚠️ 轮回自己这端另记着 {_s} —— **已忽略**（它那边远处的地图会滞留旧位置）"
        return _with_state(f"🔍 「{name}」找到 {len(nps)} 处：\n" + "\n".join(lines) + tail)
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
        # ⚠️ 2026-09-11 恒：这里原来裸用 `npcs`，里面混着自家猫狗/马（kind=pet/horse）——
        #    AI 点名要跟"人"说话时，可能挑中猫狗、走过去对着猫说话。只留真人 NPC。
        npcs, _pets, _horses = _split_surr_chars(s)
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
            # ⚠️ 用 `api.find_npc`（host 为准），别用 `api._get`（自己那端会报幽灵位置）
            f = api.find_npc(name)
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
        # 对话推进：**统一走 `_collect_dialogue`**（2026-09-12：原先这里有一份复制粘贴的同款循环，
        # 两边各自长歪；现在只保留一份实现——它只管短对话、不推长剧情，长剧情交 `menu advance`）
        collected, opts = _collect_dialogue()
        # 汇总：一次性返回累计台词 + 选项
        lines = [f"💬 和 {tname} 搭话:{nav_log}"]
        if collected:
            for d in collected:
                lines.append(f"  「{d}」")
        else:
            lines.append("  （没有台词）")
        if opts:
            lines.append(f"  🗳️ 选项: {' | '.join(f'[{i}]{_opt_text(t)}' for i, t in enumerate(opts))}")
            lines.append("  → menu_click(option=N) 选择")
        elif _DIALOGUE_NOTE.get("handoff"):
            lines.append(f"  🎬 {_DIALOGUE_NOTE['handoff']}")
        if not opts:
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

    🛡️ **只清橡/枫/松**：特殊树种（蘑菇树/桃花心木/苔雨树/神秘树/棕榈）**默认不动**，
    收尾会报"受保护跳过：X×N"。要清得先在 settings 域放行（`settings chop 蘑菇树`）。

    Args:
        x1, y1: 区域左上角坐标
        x2, y2: 区域右下角坐标
    """
    return _clear_area_run([str(x1), str(y1), str(x2), str(y2)],
                           f"区域 ({x1},{y1})-({x2},{y2})")


def _clear_area_run(args_list, desc: str) -> str:
    """跑 `clear_area.py` 并包一层（区域描述由调用方给——矩形/圆形的说法不一样）。"""
    warp_log = _warp_home_if_needed("Farm")
    out = _run_script("clear_area", args_list + ["--allow", _chop_allow_arg()], timeout=120)
    return _with_state(f"{warp_log}🧹 清理 {desc}:\n{out[:600]}")


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
                     rows: int = 1, length: int = 1, direction: str = "horizontal",
                     **extra) -> str:
    """🌱 撒化肥（照播种逻辑抄的：逐格走→撒→检测兜底）

    撒前扫描目标区：**已有同种化肥的格跳过**（不浪费），不同种才覆盖，
    没锄的格跳过并提醒。撒完 position 检测兜底，数落格报结果。
    ⚠️ 尺寸默认 1×1 不擅自扩；**x/y 必填**（2026-09-10 恒：缺坐标不再兜底成"玩家面向格"）。

    Args:
        fertilizer_name: 化肥名（Basic Fertilizer / Quality Fertilizer / Speed-Gro /
            Deluxe Speed-Gro / Deluxe Fertilizer / Hyper Speed-Gro / 保留土壤类）
        x: 起始 X 坐标（必填）
        y: 起始 Y 坐标（必填）
        rows: 撒几行（默认 1）
        length: 每行多长（默认 1 格）
        direction: horizontal=横着 / vertical=竖着（默认 horizontal）
    """
    # ⚠️ 2026-09-19：本 op 原来**只吃固定签名**，于是 `farm ops="till fertilize"` 共用一份 kw 时，
    #    它收不下的 `seed_name` 会走组合器那层"参数名写错"的通用提示（误导：那不是写错，是别的 op 的）。
    #    改成和 till/plant 同款：吃 `**extra` + 走 `_farm_kw_norm` 的兄弟参数点名。
    length, err, ignored = _farm_kw_norm(x, y, rows, length, direction, extra, _FARM_SIBLING_KW)
    if err: return err
    xy, err = _farm_require_xy(x, y)   # ⚠️ 坐标必填——先拦，别为一次报错白跑回农场
    if err: return err
    x, y = xy

    warp_log = _warp_home_if_needed("Farm")

    # 1. 化肥 ID 解析（用于同种/异种检测；未知 ID 退化成"有化肥就跳过"）
    fert_id = FERTILIZER_ID_BY_NAME.get(fertilizer_name)

    # 2. 检查化肥在背包
    st = api.state()
    inv = st.get("inventory", [])
    if not any(fertilizer_name in (i.get("name") or "") for i in inv):
        return _with_state(f"{warp_log}❌ 背包里没有「{fertilizer_name}」")

    # 3. 算目标格
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
    _blocked_f = {}    # 🌿 没锄的格里，还把"被什么挡着"分出来（恒：地格上有杂草）
    # 🦶 2026-09-19：站田心扫描**要挑能站的格**——原来写死田心，田心正好是洒水器/箱子格时
    #    人就被闪上去了（恒真机："踩到洒水器了"）。挑不出能站的格就**原地扫**。
    _fcx = x + dx * length // 2 + rdx * rows // 2
    _fcy = y + dy * length // 2 + rdy * rows // 2
    _wk0 = api.walk_ok_tiles(min(t[0] for t in target_tiles) - 1, min(t[1] for t in target_tiles) - 1,
                             max(t[0] for t in target_tiles) + 1, max(t[1] for t in target_tiles) + 1)
    _pk0 = api.stand_near(target_tiles, _wk0, _fcx, _fcy)
    if _pk0:
        try:
            api.position(_pk0[0], _pk0[1])
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
                _lab, _cl = _tile_obstacle(t)
                if _lab:
                    _blocked_f.setdefault("clear" if _cl else "facility",
                                          []).append((tx, ty, _lab))
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

    # 5. 逐格撒（同锄地/播种的走位）
    # 🦶 2026-09-19：原来写死 `(tx, ty-1)` 面朝下 —— 站位格被箱子/机器占住时会**静默瞬移上去**
    #    （和锄地/播种同一个病根，恒："只管耕种"就一并改）。改用共用件挑站位；四邻全占就报缺失、不硬落。
    _xs = [p[0] for p in do]
    _ys = [p[1] for p in do]
    _walk_ok = api.walk_ok_tiles(min(_xs) - 1, min(_ys) - 1, max(_xs) + 1, max(_ys) + 1)
    if _walk_ok is None:
        return _with_state("❌ 拿不到田块的可走信息（/passable_rect 失败）——本次没撒化肥（不盲走）")
    api.select(fertilizer_name)
    time.sleep(0.2)
    no_stand_f = []
    for tx, ty in do:
        stand = api.stand_tile(tx, ty, _walk_ok)
        if stand is None:
            no_stand_f.append((tx, ty))     # 四邻都被占 → 不瞬移，留给报告说清楚
            continue
        api.select(fertilizer_name)         # 走位/瞬移会重置选中（同播种那条纪律）
        try:
            api.walk_natural(stand[0], stand[1])
        except Exception:
            api.position(stand[0], stand[1])
        api.face(stand[2])
        time.sleep(0.1)
        api.use_item()
        time.sleep(0.35)

    # 6. 检测兜底：站田中央重扫，精确比对目标化肥 ID（不能只看"有化肥"——旧化肥会误报）
    time.sleep(0.4)
    cx = x + dx * length // 2 + rdx * rows // 2
    cy = y + dy * length // 2 + rdy * rows // 2
    # 🦶 站田心扫描要挑能站的格（同锄地/播种）——田心正好是洒水器/箱子格时不能硬闪上去
    _park = api.stand_near(do, _walk_ok, cx, cy)
    if _park:
        try:
            api.position(_park[0], _park[1])
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
    _ig = _ignored_note(ignored)
    if _ig:
        lines.append(_ig)
    if got is not None:
        lines.append(f"  ✅ 已撒 {got}/{len(do)} 格")
    else:
        lines.append(f"  ✅ 已执行 {len(do)} 格（检测失败）")
    if no_stand_f:
        lines.append(f"  ⚠️ 没撒 {len(no_stand_f)} 格（**四邻没处站**）: "
                     + " ".join(f"({a},{b})" for a, b in no_stand_f[:8]))
    if occupied:
        lines.append(f"  🔒 异种化肥占用 {len(occupied)} 格（一块地只能撒一种，可镐掉重锄再撒）: {occupied[:6]}{'…' if len(occupied)>6 else ''}")
    if skip:
        lines.append(f"  ⏭️ 已有同种跳过 {len(skip)} 格")
    if not_tilled:
        lines.append(f"  ⚠️ 没锄地跳过 {len(not_tilled)} 格: {not_tilled[:8]}{'…' if len(not_tilled)>8 else ''}"
                     f"（先 `farm ops=\"till\"` 锄好再撒；想一次说完就 `farm ops=\"till fertilize\"`，一份 kw 共用）")
        # 🌿 恒 2026-09-19：这些格"没锄"背后往往**有草/杂物挡着** —— 那个不先说清，
        #    AI 会一直 till、一直失败（真机：报告只说"缺失 13 格"，没人告诉它先割草）。
        lines.extend(_obstacle_lines(_blocked_f, verb="撒"))
    return _with_state(f"{warp_log}\n" + "\n".join(lines))


# ⛔ 2026-09-19 恒拍板：**退役两个 op**（函数已删，别再往回加）——
#    · `till_plant`（锄+种一条龙）：它**绕过 `_farm_till`** 直接调 `_till_rect` ⇒ 没有 layout、不走逐格路由
#      （09-17 收敛锄地时漏掉的一处）。要锄+种就写 **`farm ops="till plant"`** —— 一次调用、**一份 kw 共用**，
#      锄地会自动**点名忽略** seed_name（见 `_FARM_SIBLING_KW`）。
#    · `plantlayout`（按布局播种）：并进 `_farm_plant`，`layout` 变成**传参**（0 整块 / 1 初级 / 2 高级 / 3 铱）。

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


# ⛔ 2026-09-19：`plantlayout` 已并入 `_farm_plant`（`layout` 改传参）——见上面那条退役说明。

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
    逐个站碗建筑位 + 朝右 + /tool 浇碗格。返回报告行列表（没水壶 → ['⚠️ 没带水壶']）。

    🌧️ 2026-09-12 恒真机："下雨水碗会满，可以做个跳过" ⇒ **雨天直接不浇**。
    证据：秋9 雨天 `/petbowl` 读到 `bowlWatered=true`（谁都没浇，是雨填的），而工具照样让 AI 走了
    4 个碗挥了 4 次壶——纯白跑一趟（真机实测）。
    ⚠️ 判据取**天气**（`/state.time.isRaining`）**而不是逐个碗的状态**：1.6 的碗是 PetBowl
    **建筑**、`watered` 挂在建筑上，而 `/petbowl` 只报**第一个**碗（本档有 4 个碗，另 3 个读不到）
    ⇒ 想做到"哪碗满跳哪碗"得先给 C# `/petbowl` 加个全部碗列表（下次重编 DLL 顺手做）。
    在那之前：雨天全跳（雨对每个碗一视同仁），非雨天照旧全浇。
    """
    report_parts = []
    # 🌧️ 雨天跳过（恒 2026-09-12）——放**最前**：雨天连"没带水壶"都没必要报（今天轮不到浇）
    try:
        if bool((api.state().get("time") or {}).get("isRaining")):
            _pb = api._get("/petbowl") or {}
            _full = "（`/petbowl` 报 bowlWatered=true，是雨填的）" if _pb.get("bowlWatered") else ""
            return [f"🌧️ 今天下雨：宠物碗会被雨自动填满{_full}，跳过喂水（不用走位、不用挥壶）"]
    except Exception:
        pass   # 读不到天气就**不跳**（保守：宁可白浇一趟，也别漏掉真没满的碗）
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
    检查水壶，没带/没水会提示。不摸宠物（摸用 pet_pet / pet_walk）。
    🌧️ 雨天直接跳过（碗会被雨填满，不用白跑）——见 `_water_pet_bowls`。"""
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


def _wait_on_map(loc_name: str, timeout: float = 6.0) -> bool:
    """轮询等玩家**真的**站到 loc_name 上。

    ⚠️ 2026-09-16 恒真机抓到：`api.warp()` 的 **HTTP 回包早于 warp 生效**——实测从畜棚
    `/warp Farm` 的回包里 `actual` 还写着 `{"location":"Deluxe Barn","x":11,"y":14}`，
    `sleep(0.5)` 之后人仍在棚内。**所以 warp 之后不能靠固定 sleep 就读下一个读数**，
    要拿结果当判据的地方（尤其 `/animals` 这种"读当前地图"的端点）必须先确认真到了。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if api.state().get("location", {}).get("name") == loc_name:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _grazing_care() -> str:
    """🌾 室外放牧牲畜照料（2026-08-24 恒：忘关门牛羊鸡跑 Farm 上）。AI 站在 Farm 上调用——
    读 api.animals()(=farm.animals 放牧动物)，拟人自然走路摸（pet_walk 已改读 /animals 物理位置，
    petall 摸不到室外）+ 挤奶剪毛(_milk_shear_animals skip_grabber=True，室外无自动采集器)。无放牧动物→空串。

    ⚠️ 2026-09-16 恒真机抓到**静默跳过**：`/animals` 读的是**玩家当前所在图**（实测棚内恒 0 只、
    Farm 上 24 只），而调用方 `care_animals` 从畜棚 `api.warp("Farm")` 后只 `sleep(0.5)`
    —— warp 那时**还没生效** ⇒ 读到棚内 0 只 ⇒ 下面那条 `not count` 守卫当场判空 ⇒
    **整趟室外放牧一声不响地跳过**（24 只动物一只没摸，`farm animals` 11 秒就"收工"）。
    ⇒ 现在：**函数自己先确认人真的在 Farm 上再读**（不再指望调用方摆好位），
      不在 Farm 就**明说**——绝不再把"读不着"伪装成"没有放牧动物"。
    """
    try:
        cur = api.state().get("location", {}).get("name", "")
        if cur != "Farm":
            return (f"⚠️ 室外放牧这趟没做：人还在「{cur}」不在 Farm —— "
                    "`/animals` 只报**玩家当前所在图**的动物（棚内恒 0 只），在这儿读会把"
                    "「跑 Farm 放牧的牲畜」误判成「没有」")
        data = api.animals()
        if not data.get("animals") or not data.get("count"):
            return ""   # 人就在 Farm 上、读到的就是 0 → 确实没放牧动物，安静收工
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
            # ⚠️ 2026-09-16：`api.warp` 的回包**早于 warp 生效**（实测回包 `actual` 还写着 Deluxe Barn），
            #    固定 sleep 等着靠不住 —— 下一趟是"读当前图"的 /animals，慢半拍就会读到棚内 0 只。
            _wait_on_map("Farm", timeout=6)
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


def _door_snapshot(r: dict) -> list:
    """把 /toggle_doors 回包解析成 [(建筑名, 翻转后门是否开着)]；读不到状态记 None。

    ⚠️ 2026-09-16：C# 回包字段是 `{ok, toggled, details}`，**不是** `{closed, doors}`。
       历史上这里读 `closed`/`doors` ⇒ 恒为 0 ⇒ 无论找没找到建筑都报「没有动物建筑」，
       而 C# 那边其实真把门翻了（实测回包 `toggled: 2`，建筑明明找到了）。
       `details` 每项形如 `{building, door_open}` / `{building, toggled_via}`
       / `{building, error, runtime_type}` —— **没有** `door` 坐标对，旧格式化 `d['door'][0]`
       即使键名修对也会 KeyError。
    """
    out = []
    for d in (r.get("details") or []):
        name = d.get("building") or "?"
        if "door_open" in d:
            out.append((name, bool(d["door_open"])))
        else:
            out.append((name, None))   # toggled_via / error 都算未确认
    return out


@mcp.tool()
def close_doors() -> str:
    """🚪 关闭所有动物建筑的门
    晚上调用，防止野生动物袭击牲畜。
    开门已包含在 care_animals 中。
    """
    try:
        r = api.close_doors()
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', '关门失败')}")
        state = _door_snapshot(r)
        if not state:
            return _with_state("⚠️ 没有动物建筑")
        # ⚠️ C# `/toggle_doors` **忽略 action 参数**，实现是 `netBool.Value = !netBool.Value`
        #    纯翻转 ⇒ 「关门」不是关门。这里翻完自检：还有开着的就再翻一次，收敛到全关。
        #    （代价：一次调用最多两次 /toggle_doors；正常情况一次就收敛。）
        if any(open_ is True for _, open_ in state):
            state = _door_snapshot(api.close_doors())
        closed = [n for n, o in state if o is False]
        unknown = [n for n, o in state if o is None]
        detail = "、".join(closed + unknown)
        msg = f"🚪 已关闭 {len(closed)} 扇门" + (f"：{detail}" if detail else "")
        if unknown:
            msg += f"（{len(unknown)} 个未确认）"
        return _with_state(msg)
    except Exception as e:
        return _with_state(f"❌ {e}")


# ═══════════════════════════════════════════
#  产业工具
# ═══════════════════════════════════════════

@mcp.tool()
def harvest_crops(radius: int = 15) -> str:
    """🌾 收割当前场景所有已成熟作物（**拟人**：逐个走到作物旁 → 面朝 → 动手）

    恒定两条路：**已领耕种精通 + 背包有铱镰刀 → 挥镰刀；否则 → 手摘**。
    （精通门禁 = `_mastery_claimed("farming")`，读的是游戏自己的 `mastery_0` 统计，
      经 `/mastery` 的 plaques[].claimed —— 2026-09-16 前是拿"已学配方含 Statue Of Blessings"倒推的。）

    ⚠️ 2026-09-16 恒："我记得有一个个摘的工具的，理应没有做过程序化收获……是不是把原作者的
       作弊方案弄了进来"。属实：原实现走 C# `GET /harvest`——一次遍历 `terrainFeatures`
       直接 `crop.harvest()`，**不走路、不挥工具、产物直进背包**（恒："没有挥镰刀也没有一个个摘，
       直接全作弊进包了"）。2026-09-06 那次"工具收敛"把拟人的 `harvest.py` 退役、把收获改指向了
       这条程序化路径。现改回拟人（站位待恒校准）。
    ⚠️ 拟人慢（每株：走位+等到达+面朝+按键，约 2~4 秒）⇒ 已进 `_ASYNC_SCRIPTS` 转后台跑。

    Args:
        radius: 扫描半径（默认15，覆盖周围作物）
    """
    args_list = ["--radius", str(radius)]
    if _mastery_claimed("farming"):
        args_list.append("--scythe")     # 已领耕种精通 → 优先挥镰刀（脚本仍会确认铱镰刀在背包）
    result = _run_script("scythe_crops", args_list, timeout=1800, async_ok=True)
    if result.startswith("🚀"):
        return _with_state(result)       # 长脚本自动异步：立即返回 job_id
    return _with_state(f"🌾 收菜完成\n{result[:600]}")


@mcp.tool()
def collect_machines(machine_type: str = "", location: str = "") -> str:
    """⚙️ 一键收机器产物（=只收不放，全农场一遍瞬收，不走路）
    遍历所有机器，把已完成的产品直接收进背包（返回带当前品质，Cask 用）。
    只收 readyForHarvest 的机器，陈化中的 Cask 不取。
    ⚠️ vs building：这是原子瞬收（不走路/不拟人）；要进屋逐台拟人收放(收+放料)走 building。

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
def load_machines(item: str, machine_type: str = "", location: str = "", count: int = 0) -> str:
    """⚙️ 批量往空机器放原料（游戏原生路径：warp过去→选中→interact）
    把背包里的原料装进匹配的空机器，加工时间由游戏自己算（Keg酿酒/Cask陈化）。
    每台机器都真实走过去操作（warp 快速移动），100% 走游戏交互逻辑。
    ⚠️ 原料**彻底用尽**就整轮提前收工（不再拿空手把剩余空机器逐台试一遍）。
    ⚠️ 品质不影响：`/select` 每次都重新选，普通品质那栈用完了自然选到金/银星那栈。

    Args:
        item: 原料英文名（如 Starfruit；Cask 用成品如 Starfruit Wine）。
              **可逗号给多个**按优先级依次用完，如 "Ancient Fruit,Starfruit"（恒 2026-09-16）
        machine_type: 目标机器类型（Keg / Cask / Preserves Jar…，留空试所有空机器）
        location: 限定地点（Cellar / Big Shed…；**留空=当前场景/建筑**，不跑全农场——恒 2026-08-13）
        count: **最多装几台**（0=不限）。想"就放 50 台"就传 50——恒 2026-09-16
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
    if count:
        args_list += ["--count", str(int(count))]
    out = _run_script("machine_loader", [item] + args_list, timeout=600, async_ok=True)
    if out.startswith("🚀"):
        return _with_state(out)   # 长脚本自动异步：立即返回 job_id
    return _with_state(f"⚙️ 放置原料 {item}：\n{out[:600]}")


@mcp.tool()
def work_building(location: str, item: str = "", machine_type: str = "") -> str:
    """🏠 拟人收放一轮（进屋⇒收⇒放，逐台严格交互，真走位）
    走到建筑门口开门进去 → 收完该屋所有机器产物 → 把背包原料放进该屋空机器。
    走的是 4 邻+斜对角 8 方向真 checkAction（不是直加作弊）。站过道格一趟处理一圈。
    一屋一轮（单地点），AI 决定去哪些屋子、按什么顺序。
    ⚠️ vs collect：这是拟人走位(逐台真交互)；要全农场一遍瞬收(只收不放)用 collect。
    ⚠️ 2026-09-16 恒：**留空 item = 只收不放**，那就跟 collect 重复了（collect 还不用走路）
       ⇒ 只收请直接用 collect；本 op 的价值在"收完顺手放"，**收放请务必传 item**。
    ⚠️ 同批：脚本现在会在**放不下去**时提前收工返回，不再把整间屋走完——
       ①待放物品用完（背包里没这个 item 了）②机器不收这东西（游戏 `actionTriggered=false`）。
       两种情况都会在日志里写明 `⏹ 提前收工` + 报"已走 N/M 格"。

    Args:
        location: 屋子/地点名（Big Shed / Cabin / Cellar / Farm…）
        item: 要放的原料英文名（如 Starfruit；留空=只收不放，见上面的⚠️）
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

    🛡️ **只砍橡/枫/松（Tree:1~3）**：特殊树种（蘑菇树/桃花心木/苔雨树/神秘树/棕榈）**默认不动**，
    收尾会报"受保护跳过：蘑菇树×13…"。要砍得先在 settings 域放行一种或多种
    （`settings chop 蘑菇树,桃花心木`；收回 `settings chop none`；全放行 `settings chop all` 慎用）。

    Args:
        area: 限定区域 —— **4 个数 = 矩形 `x1,y1,x2,y2`；3 个数 = 圆 `圆心x,y,半径`**
              （如 `"40,11,45,25"` / `"42,17,4"`；逗号空格都认）。留空 = 老行为（找身边半径 20 内的树）。
    """
    # ⚠️ 2026-09-19 修：原来把 `area` **原样当命令行参数**塞给脚本，可脚本**根本没有位置参数**
    #    ⇒ 传任何值都是 `unrecognized arguments` + 退出码 2（"域 op 断档"：文档写着支持、实际必崩）。
    #    现在走 `--area`，脚本那侧认 4 个数=矩形 / 3 个数=圆（见 area_spec.py），并且会**先走过去**再找。
    args_list = ["--allow", _chop_allow_arg()]
    if area:
        args_list = ["--area", str(area)] + args_list
    out = _run_script("chop_trees", args_list, timeout=120)
    return _with_state(f"🪓 砍树" + (f"（限定区域 {area}）" if area else "") + f"：\n{out[:600]}")


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
    hp_threshold: int = 30,
    food_sta: Optional[str] = None,
    food_hp: Optional[str] = None,
    resume: bool = True,
) -> str:
    """⛏️ 去矿井挖矿（双模式最全）
    🏃 冲层 = 下矿(rush)：从指定/当前层往**更深**敲石头找梯子下到 target 层（打通往下冲）；
    🔁 刷矿 = farm：在电梯直达层反复刷指定 ore（Copper铜21/Iron铁41/Gold金71），适合定点囤矿；
       💡 想刷煤：farm Iron 铁层(41) 时会顺手清尘埃精灵/蝙蝠——它们掉煤（不是 ore 选项，内部自动刷）。
    两者都是「mine go」，用 **mode** 切换：mode=rush 下矿、mode=farm 刷矿。已打通的层别担心没得玩——设 start 挑层。

    自动检测镐子级别算好敲击次数，不浪费体力。
    附近有怪物自动切剑砍（贴脸/近身主动反击，不是站桩被磨死）。
    背包有食物会自动吃（按需求：血低优先吃回血的，别再拿纯体力咖啡保命）。

    调用前请用 check(what="status") / daily(ops="peek") 确认带了镐子和剑、有食物、背包留 ≥10 格（满先用 storage(ops="store") 存箱子）。
    （2026-09-11：原来这里写的是 check_status / peek_player / chest_store 三个**隐藏工具名**——op docstring 不进 AI 上下文所以没造成卡死，但会带偏后来改代码的人，已改写成域形式。）
    （进矿时的跑前叮咛——工具/雕像/清包/占位物——统一在第一次到矿井入口层弹出的那 4 句话里，不重复。）

    Args:
        mode: 模式（rush=下矿/冲层, farm=刷矿，默认 rush）
        start: 起始层数，仅 rush 模式。⚠️ 2026-09-06 可让 AI 显式挑层——**≤电梯可达上限且 5 的倍数**（如 40/60/90），
               打通 120 后照样能 start=90 从 90 下到 120，不会"送我到120就没得玩"；start=1+resume=True=从当前所在层继续（不再被进度覆盖）。
        target: 目标层数，仅 rush 模式（默认 120）
        ore: 目标矿石，仅 farm 模式（Copper/Iron/Gold，默认 Iron）
        cycles: 刷矿循环次数，仅 farm 模式（默认 5）
        hp_threshold: 血量低于此 % 吃食物（默认 30%）。⚠️ 这是**吃/兜底**线不是撤退线——
            **撤退看 HP<20 绝对值**（恒 2026-09-19："不到快死都可以跟着房主继续下"）
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

    # ⚠️ 2026-09-06 恒：门禁——只能在矿井里触发下矿，别让 mine_run 从远图 warp 飞进矿(音乐乱)。
    #    AI 先自己 map_go('Mine') 自然到矿井口，再下矿。
    _cur = api.state().get("location", {}).get("name", "")
    if not (_cur == "Mine" or _cur.startswith("UndergroundMine")):
        return _with_state(f"❌ 现在不在矿井里（{_cur}）——先 map_go('Mine') 到矿井口，再触发下矿（防瞬移/音乐乱）")

    # 先看进度
    _rem = _mine_entry_reminder(_cur)   # 若这是今天第一次到该矿井入口层，叮咛（进矿随手带）
    progress_out = _run_script("mine_run", ["--check-progress"], timeout=10)
    out = _run_script("mine_run", args_list, timeout=600, async_ok=True)
    if out.startswith("🚀"):
        return _with_state(_rem + "\n" + out)   # 长脚本自动异步：立即返回 job_id
    return _with_state(_rem + f"⛏️ 挖矿报告：\n{progress_out}\n{out[:700]}")


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
    开局一次性用 isFishing(等待咬钩) 判定能否抛：5s 内建立就开钓；没建立(没水/死点)就收手。
    指定 location → 先用 map_go 走真实路径到该校准钓点(跨图多段，和 walk_to/map_go 分工一致，不是 warp)，再就地钓。
    开 Fishbot 自动钓鱼 → 抛够竿数/体力不足收杆。

    Args:
        location: None=就地钓（当前站位，须 AI 自己站到水边）；指定（Beach / Mountain / Forest / Town）=map_go 去钓点再钓
        max_casts: 抛 N 竿就收手（0=不限，钓到体力<20/背包满/太晚/抛不出去收杆）
        no_sleep: True=钓完不睡觉（留在原地）；False=钓完回家睡。
                  ⚠️ 2026-08-15 改默认 True：睡觉由 AI 用 go_sleep 统一控制（白天钓鱼别早睡）。
    """
    nav = ""
    args_list = ["--port", str(_ai_port()), "--max-casts", str(max_casts)]
    if location:
        # 🎣 2026-09-05 恒：钓点是 POI，跨图该走 map_go（和 walk_to 的跨图委派一致）——不再是 warp 回家再跳。
        try:
            from fish_run import FISHING_TARGETS as _FT   # 懒导入，单一来源
        except Exception:
            _FT = {}
        if location in _FT:
            poi_name, face = _FT[location]
            nav = map_go(poi_name)                        # map_go 真实路径到钓点（可跨图多段）
            try:
                api._post("/face", {"direction": face})   # 抛竿朝向（面下等）
            except Exception:
                pass
            # 已到钓点 → 就地钓（不带 --location，fish_run 不再 warp）；nav 日志拼到结果前
            pass
        else:
            args_list.extend(["--location", location])    # 未识别钓点 → 交 fish_run 自带 /walk_to 兜底
    if no_sleep:
        args_list.append("--no-sleep")

    out = _run_script("fish_run", args_list, timeout=180, async_ok=True)
    head = (nav + "\n\n") if nav else ""
    if out.startswith("🚀"):
        return _with_state(head + out)   # 长脚本自动异步：立即返回 job_id，进度/收工自动播报
    return _with_state(head + f"🎣 钓鱼报告：\n{out[:800]}")


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
    ⚠️ **必须本人站到样式机前**（FishShop(10,4)，站 (10,5) 面朝上）才调得动 ——
       本工具**不替你走位、也不瞬移**；不在跟前会直接报错并告诉你怎么 map_go 过去。
    """
    try:
        # 0. 🔴 先验"人在不在机器前"（2026-09-12 恒拍板：**走过去，工具不替你走、更不瞬移**）——
        #    以前这里调 api.warp_into("FishShop",10,5) **瞬移**过去。它最终落 C# WarpDirect：
        #    `farmer.currentLocation = targetLoc; farmer.Position = ...` —— **没调 Game1.warpFarmer、
        #    没切 Game1.currentLocation**（决定渲染哪张图的字段），于是 farmhand 自己的客户端还在渲染旧图、
        #    人却按新坐标画 ⇒ 真机看到"人在家里、墙外"，房主那边却看它在鱼店。
        #    ⚠️ 更坑的是**整条工具链看不见这个错位**：/state 读的也是 farmer.currentLocation，
        #    所以所有工具都高高兴兴报"FishShop"，没人发现得了。现在改成不在跟前就**明确报错叫人
        #    map_go**（宁报错别兜底）——别拿瞬移把"人没真的过去"糊过去。
        st = api.state()
        loc = (st.get("location") or {}).get("name", "")
        px = (st.get("player") or {}).get("x") or 0
        py = (st.get("player") or {}).get("y") or 0
        if loc != "FishShop" or max(abs(px - 10), abs(py - 5)) > 2:
            return _with_state(
                f"❌ 你不在威利的浮漂样式机前（现在 {loc or '?'} ({px},{py})）。\n"
                f"   样式机在 FishShop(10,4)，要**站在 (10,5) 面朝上**才交互得到。\n"
                f"   自己过去：先 map ops=go 到鱼店（沙滩上的威利鱼店），"
                f"再用 map ops=walk kw={{'x':10,'y':5}} 走到机器正下方 → 再来调本工具。")
        # 1. 关掉可能开着的菜单
        api._post("/menu_close")
        time.sleep(0.3)
        # 2. 装备鱼竿（样式存当前鱼竿）
        rods = [it for it in (st.get("inventory") or []) if "rod" in (it.get("name") or "").lower()]
        rod_name = rods[0]["name"] if rods else None
        if rod_name:
            api._post("/tool", {"name": rod_name})
            time.sleep(0.3)
        # 3. 面朝机器交互（人已在跟前 —— 见上面第 0 步校验，这里不再瞬移）
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
        bomb_radius = bot.blast_reach()
        cands, nrocks = bot.plan_anchors(radius, min_covered, top)
        if nrocks == 0:
            return _with_state("🪨 本层没岩体了，找梯子下楼吧")
        lines = [f"🪨 本层 {nrocks} 块岩体，炸弹半径{bomb_radius}(按{bot.bomb_type}形状)，候选锚点:"]
        if cands:
            for count, negd, ax, ay in cands[:top]:
                lines.append(f"  · ({ax},{ay}) 覆盖 {count} 块")
        else:
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
        # ⚠️ 2026-09-17 真机抓到：这里原写 `ok, msg = ...` —— 而 `bomb_and_collect` 按自己的
        #    docstring 返回 **3 个值** `(ok, message, broken_estimate)`（另外三个调用方
        #    bomb_escort/bomb_mine/bomb_volcano 也都解 3 个）⇒ 每次调用必抛
        #    `ValueError: too many values to unpack (expected 2)`，被下面 except 兜成
        #    "❌ 放炸弹失败" ⇒ **bomb_place 从来没成功过**。
        #    更糟的是**炸弹真放出去炸了**（那一下就放完即炸，AI 掉了 4 血），
        #    工具却报"失败" —— 又一条"说失败但事已发生"。
        ok, msg, _broken = bot.bomb_and_collect(x, y, collect=True)
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
    （进矿跑前叮咛——工具/雕像/清包/占位物——统一在第一次到矿井入口层的 4 句话里，不重复。）

    Args:
        target: 目标层（0=按当前层自适应：在头骨≥121→500、城镇→120；头骨矿洞也算 UndergroundMine121+）
        bomb: 炸弹类型 Bomb/Mega Bomb/Cherry Bomb（默认 Bomb；**背包没有黑会按 黑>超级>樱桃 自动换有的用**）
        min_covered: 至少覆盖N块岩体才炸（默认3）
        follow_host: user 在矿里就一起冲层/增援（默认 True）
        lead: 和 user 保持的层差（默认2）
        autodrop: 自动丢物（已退役），0=只规划不丢交AI手动整理（默认0）
        one_floor: 逐层模式，跑一层返回摘要不撤退（默认 False）
    """
    _cur = api.state().get("location", {}).get("name", "")
    if not (_cur in ("Mine", "SkullCave") or _cur.startswith("UndergroundMine")):
        return _with_state(f"❌ 现在不在矿井/头骨矿洞里（{_cur}）——先 map_go 到矿井(✓)或头骨矿洞(121+)再炸（防瞬移/音乐乱）")
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
    _rem = _mine_entry_reminder(_cur)   # 今天第一次到头骨/矿井入口层→叮咛
    progress_out = _run_script("bomb_mine", ["--check-progress"], timeout=10)
    # 逐层模式(one_floor)是快速单层摘要，保持同步看结果；冲层是长任务→自动异步
    out = _run_script("bomb_mine", args_list, timeout=600, tail=3000, async_ok=not one_floor)
    if out.startswith("🚀"):
        return _with_state(_rem + "\n" + out)   # 长脚本自动异步：立即返回 job_id
    if one_floor:
        import re as _re
        m = _re.search(r'===BOMB_SUMMARY===(.*?)===END===', out, _re.S)
        if m:
            return _with_state(_rem + f"💣 炸矿摘要（目标{target}层，本层完成）：\n{m.group(1).strip()}")
        return _with_state(_rem + f"💣 炸矿逐层（未拿到摘要）：\n{out[:700]}")
    return _with_state(_rem + f"💣 炸矿报告：\n{progress_out}\n{out[:700]}")


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
                hp_threshold: int = 30) -> str:
    """👥 协同模式：跟着 user 下矿炸矿（贴身保镖）
    滞后跟随 user（站身后不贴脸），只在途径处看到高价值矿（铱/宝石/金）才放炸弹，
    帮打怪（user 附近出现怪物就砍）。user 离开矿井就撤，没炸弹就转纯保镖跟随。

    Args:
        ore_radius: 高价值矿离 user 多近才炸（默认7格）
        cooldown: 两次炸弹最小间隔秒数（默认20）
        max_minutes: 最多跟随分钟数（默认不限）
        hp_threshold: 血量低于此%吃食物（默认30）。⚠️ 吃/兜底线，不是撤退线（撤退看 HP<20 绝对值）
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
    🏁 **到第5层 / 火山顶会自动收工**（2026-09-12 恒）：脚本停下后**人留在原地、不撤退**，
       走或留自己判 —— 第5层可续可撤（矮人商店 (36,30) / 撤退出口 (29,34) 交互答「是」），
       山顶没得选只能撤（走山顶出口 (11,36)）。想接着冲层**直接重调本工具**即可（骑行模式跟回 user
       身边，不会从第1层重来）。
    （进矿跑前叮咛——工具/雕像/清包/占位物——统一在第一次到矿井入口层的 4 句话里，不重复。）
    Args:
        bomb: Bomb/Mega Bomb/Cherry Bomb（默认 Bomb；背包没有黑会按 黑>超级>樱桃 自动换有的用）
        min_covered: 至少覆盖N块岩体才炸（火山簇小，默认3）
        hp_threshold: 血量低于此%吃食物（默认30）。⚠️ 吃/兜底线，不是撤退线
            （火山的撤退线是 HP<20 绝对值，恒 2026-09-19）
        max_minutes: 最多跟随分钟数（默认不限）
        poll: user位置轮询间隔秒（默认2.5）
    """
    try:
        vg = _volcano_gate()
        if vg:
            return _with_state(vg)
        _cur = api.state().get("location", {}).get("name", "")
        if not (_cur == "Caldera" or _cur.startswith("Volcano")):
            return _with_state(f"❌ 现在不在火山里（{_cur}）——先 map_go('火山入口') 到火山再炸（防瞬移/音乐乱）")
        args_list = [f"--bomb", bomb, f"--min-covered", str(min_covered),
                     f"--hp-threshold", str(hp_threshold)]
        _rem = _mine_entry_reminder(_cur)   # 今天第一次到火山入口层(VolcanoDungeon0)→叮咛
        if max_minutes:
            args_list.extend(["--max-minutes", str(max_minutes)])
        if poll != 2.5:
            args_list.extend(["--poll", str(poll)])
        out = _run_script("bomb_volcano", args_list, timeout=1200, async_ok=True)
        if out.startswith("🚀"):
            return _with_state(_rem + "\n" + out)   # 长脚本自动异步：立即返回 job_id
        return _with_state(_rem + f"🌋 火山炸矿报告：\n{out[:800]}")
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
    自动：扫 surroundings 找 (O)590 Artifact Spot + (O)SeedSpot Seed Spot + forageCrop="2" 姜点
    （⚠️ `(O)590` 和 `(O)SeedSpot` 的**中文显示名都叫「远古斑点」**，别按中文名分家）→
    检查锄头 → 逐格复用耕地 tool_area till（脚本自己算坐标，不靠 AI 报）→ 循环到挖完。
    蚯蚓点出古物/矿物/种子；远古斑点出季节作物种子；姜点出姜。掉落吸附进包。

    注意：没带锄头不挖（状态注入也不报）。用法：scene ops=spot / 挖斑点 / 挖蚯蚓
    """
    # ⚠️ 2026-09-19：原来 `out[:600]` 只截**头**，16 个斑点时正好把最后的
    #    「✅ 挖成 N 个 / 为什么没挖动」汇总切掉 —— 而那行才是 AI 下一步要读的东西。
    #    改成"留头也留尾"，中间省略。
    out = _run_script("spot_run", timeout=300)
    txt = out if len(out) <= 1200 else out[:500] + "\n…（中间略）…\n" + out[-700:]
    return _with_state(f"🪱 挖斑点/姜报告：\n{txt}")


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


# 🎓 精通山洞里五块石碑**从左到右**的物理顺序（恒 2026-09-16 现场指认）。
#   注意这**不是**游戏的技能 index 顺序（游戏是 0耕种/1钓鱼/2采集/3采矿/4战斗）——
#   按洞内实际走位顺序排，AI 一看就知道该往哪边走。坐标见 locations.POI 的"精通山洞(XX碑)"。
_MASTERY_CAVE_ORDER = ["combat", "foraging", "farming", "fishing", "mining"]

# 🎓 精通"开启条件" = 五项技能**全部到 10 级**（游戏的 `MasteryHint` 就是那时弹的）。
#    在那之前 `MasteryExp` 不涨 ⇒ **不显示精通进度**（恒 2026-09-16 拍板）。
_MASTERY_SKILLS = ("farming", "fishing", "foraging", "mining", "combat")


def _mastery_bar(r: dict) -> tuple:
    """🎓 精通经验条（**与游戏 `MasteryTrackerMenu.drawBar` 同算法**）。

    反编译原文（MasteryTrackerMenu.cs:509）：
        text = (exp − getMasteryExpNeededForLevel(level))
             + "/" + (getMasteryExpNeededForLevel(level+1) − getMasteryExpNeededForLevel(level))
    ⇒ 条和数字都是**本级内**进度，不是总量。⚠️ 别拿 `exp/expForNext` 去 ratio ——
    那会显示成 `74365/100000`，跟游戏里的 `4365/30000` 对不上（2026-09-16 恒当场指出）。
    满级(level>=5)时游戏**不画数字**、条拉满 ⇒ 这里给满格 + "已满级"。
    """
    lv = r.get("level") or 0
    cur = r.get("expThisLevel")
    tot = r.get("expThisLevelNeed")
    if lv >= 5 or not tot or tot <= 0:
        return "█" * 10, "已满级"
    ratio = max(0.0, min(1.0, (cur or 0) / tot))
    filled = int(round(ratio * 10))
    return "█" * filled + "░" * (10 - filled), f"{cur}/{tot}"


def _mastery_brief() -> str:
    """🎓 profile 里那一行精通进度（含游戏同款经验条）。
    读不到就**如实说**（旧 DLL/端点挂了），不静默吞掉。"""
    try:
        r = api.mastery()
    except Exception as e:
        return f"  ⚠️ 精通进度读不到: {e}"
    if not r.get("ok"):
        return f"  ⚠️ 精通进度读不到（{r.get('error', '端点不可用')}；模组需重编译）"
    bar, txt = _mastery_bar(r)
    unspent = r.get("unspent") or 0
    tail = f" · 🟢 有 {unspent} 点可领（去精通山洞摸碑）" if unspent > 0 else ""
    return (f"  🏆 精通 Lv{r.get('level') or 0}/5 · {bar} {txt}"
            f" · 已花 {r.get('levelsSpent') or 0} 点{tail}")


@mcp.tool()
def mastery_status() -> str:
    """🎓 精通状态（SDV 1.6）：精通等级/经验/未花点数 + **五块石碑各自领没领**。

    五项技能**全部到 10 级**之后开始攒精通经验；每升一级给 1 个"可领"名额，
    去**精通山洞**（森林右下角）摸石碑领——五块碑每块只能领一次，领什么由碑决定。

    （需 NagiBridge 模组重编译到最新版）
    """
    try:
        r = api.mastery()
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', 'mastery 端点不可用')}（模组需重编译）")
        who = r.get("who") or ""
        level = r.get("level") or 0
        exp = r.get("exp") or 0
        need = r.get("expForNext")
        spent = r.get("levelsSpent") or 0
        unspent = r.get("unspent") or 0
        can_claim = bool(r.get("canClaim"))

        # 进度条**照抄游戏那根**（本级内 xxx/xxx），不是总量——见 _mastery_bar 的注释。
        bar, txt = _mastery_bar(r)
        lines = [f"🏆 精通状态{'（' + who + '）' if who else ''}：Lv{level}/5 · {bar} {txt} · 已花 {spent} 点"]
        if need and need > 0:
            lines.append(f"  （条=本级进度，与游戏经验条一致；总量 {exp}，攒到 {need} 升 Lv{level + 1}）")
        if can_claim:
            lines.append(f"  🟢 **有 {unspent} 个没花掉的精通可以领**")
        else:
            lines.append(f"  ⚪ 暂时没得领（等级 {level}、已花 {spent}）—— 先攒精通经验")

        plaques = r.get("plaques") or []
        # 按洞内从左到右排（认不出的 skill 排最后，不丢）
        plaques = sorted(plaques, key=lambda p: (
            _MASTERY_CAVE_ORDER.index((p.get("skill") or "").lower())
            if (p.get("skill") or "").lower() in _MASTERY_CAVE_ORDER else 99))
        lines.append("  五块石碑（洞内从左到右）：")
        for p in plaques:
            cn = p.get("cn") or p.get("skill") or "?"
            if p.get("claimed"):
                mark, tail = "✅", "已领"
            elif can_claim:
                mark, tail = "🟢", "**可领**"
            else:
                mark, tail = "⚪", "没名额"
            lines.append(f"    {mark} {cn} — {tail}")

        lines.append("  💡 在洞里：走到碑前 `interact` 开菜单 → `menu read` 看这块碑给什么 → "
                     "`menu click(button=mainButton)` 领；中央基座(7,9)看总进度")
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
    """🔌 确认当前端口↔角色映射（AI=farmhand / host=房主恒）。端口按启动顺序分配，重启可能翻转——睡觉/协作前先调确认。"""
    r = api.which_role()
    if not r.get("ok"):
        return _with_state(f"⚠️ 角色检测未完成: {r.get('error')}（{r.get('note','')}）")
    a, h = r["ai"], r["host"]
    return _with_state(f"🔌 角色映射: AI({a['name']})={a['port']} | host({h['name']})={h['port']}")


def _sleep_roster() -> str:
    """在场玩家名单（**从游戏里读，绝不写死**——名字随存档/多人加入而变，恒 2026-09-19）。
    给"who 该怎么传"的报错用；读不到就如实说读不到。"""
    try:
        fs = (api._post("/crawl_bed", {"action": "locate"}) or {}).get("farmers") or []
    except Exception as e:
        return f"（读不到玩家名单: {e}）"
    try:
        me = api.ai_name()
    except Exception:
        me = ""
    out = []
    for f in fs:
        n = f.get("name") or "?"
        tag = "（你）" if n == me else ("（房主）" if f.get("isMain") else "")
        if n != me and not f.get("online", True):
            tag += "（离线）"
        out.append(n + tag)
    return " / ".join(out) if out else "（游戏没回玩家名单）"


def _who_required(op: str) -> str | None:
    """sleep/lie_bed 没传 who 时的统一报错：说清怎么传 + 列出**这个存档里真实的**玩家名。
    ⚠️ 名字一律现读（`_sleep_roster`），不许写死——存档换人/多人加入都会变。"""
    return (f"❓ {op} 要指名**睡谁的床**（who 必填）：传**自己的名字**=回自家床"
            f"（会自动走过去：跨图→门口→推门→床边）；传**别人的名字**=睡那个人的床"
            f"（爬床彩蛋，同样会自动走过去）。\n"
            f"当前存档的玩家：{_sleep_roster()}")


def _aim_sleep_home(who: str) -> str:
    """「<who> 家的床不在当前场景」→ 先**走过去**（map_go 跨图 → 门口 → 推门 → 床边）。
    返回 `""` = 已在床边/无需动；非空 = **没走到**（一句带 ❌ 的话），调用方必须原样带回去。

    ⚠️ 2026-09-19 恒：原来只处理 `who==自己名`，别人一概不导航 —— 当时的理由是
    「到对方家难自动」（原文就写在旧 docstring 里）。**这个理由现在已经过期**：
    床的位置（`crawl_bed locate` 的 `bed.location`）和对方家的门（同一次 locate 新增的
    `door`，C# 用 `FindHomeDoor(目标玩家)` 动态找：自己=按室内唯一名配小屋、房主=按
    Farmhouse 建筑类型配）都查得到了。⇒ 限制取消：传谁的名字就走谁家，**全程走、不瞬移**。
    走不到就如实报（宁报错别兜底），别让 AI 收到一句"当前场景没有床"干瞪眼。"""
    try:
        c = api._post("/crawl_bed", {"action": "locate", "player": who})
        if not c.get("ok"):
            # 名字打错时 C# 会直接把可选的玩家名列在 error 里（别再往下猜）
            return f"❌ 找不到{who}的床: {c.get('error')}"
        bed_loc = (c.get("bed") or {}).get("location") or ""
        cur = c.get("curLoc") or ""
        if not bed_loc:
            return f"❌ 不知道{who}的床在哪（crawl_bed 没回 bed.location）"
        if bed_loc == cur:
            return ""                                   # 已经在屋里了 → 后面流程自己走到床边
        ok, msg = _go_home(who)                         # 走过去（含推门进屋 → 到床边）
        if ok:
            return ""
        # 🏝️ 姜岛：目的地是**共用的姜岛小屋**（不是"谁的屋"）——报错文案别照搬大陆那句，
        #    否则 AI 会看到"没能走到恒屋里的床边"却其实人在岛上，看不出该往哪走。
        _where = "姜岛小屋（大通铺）" if api.on_island() else f"{who}屋里的床边"
        return f"❌ 没能走到{_where}——{msg}"
    except Exception as e:
        return f"❌ 回屋失败: {e}"


@mcp.tool()
def go_sleep(who: str = "") -> str:
    """💤 上床睡觉（统一入口，已含爬床彩蛋）。**who 必填**，指名睡谁的床：
    - 传**自己的名字** → 睡自己小屋的床（正常回家睡，无彩蛋）
    - 传**别人的名字**（房主/其他玩家）→ 睡那个人的床
    睡别人的床 = 爬床彩蛋：广播"<自己>爬上了<对方>的床！" + 醒来成功检测（在对方床醒来→🌹一起睡彩蛋）。
    ⚠️ 名字要跟游戏里一致（打错会**直接报错并列出可选名字**，不会默默睡成别人的床）。
    **传对名字就不用先回家**：不在那栋屋会自动走过去（map_go 跨图→门口→推门→床边，全程走不瞬移）。
    流程（2026-08-14 四场景验证锁定）：到床边→精确对位→爬床广播→就地 ready→等过夜；
    夜不过自动"走刷新"重爬。对方没配合(卡ReadyCheckDialog)超时则取消起床，绝不卡死。
    🏝️ **在姜岛时是另一套（共用姜岛小屋的大通铺，没有"谁的床"）**：who 传**正躺在那儿的别人**
    → 挤他这张床（姜岛版爬床彩蛋）；否则（who 是自己 / 那人还没躺）→ 大通铺随便挑一张空床
    （安静睡，不播报）。不在小屋里会自动走过去（`map go 姜岛小屋`）。
    """
    api.ensure_roles()  # 端口↔角色可能翻转，先对齐
    if not (who or "").strip():
        return _with_state(_who_required("sleep/睡觉"))
    err = _aim_sleep_home(who)   # 不在那栋屋 → 自动走过去（2026-09-19 起对任何人都生效）
    if err:
        return _with_state(err)  # 没走到就如实说，别丢给下层报"当前场景没有床"
    r = api.go_sleep_flow(who)
    msg = r.get("summary", "❌ 睡觉失败")
    # 睡别人家 + 醒来位置核实 = 一起睡彩蛋成功
    if r.get("co_sleep") and r.get("woke_in_expected_bed") is True:
        msg = "🌹 一起睡彩蛋成功！" + msg
    return _with_state(msg)


@mcp.tool()
def lie_bed(who: str = "") -> str:
    """🛏️ 上床躺着（**只躺不睡，不过夜**）：完整走上床——走到床边→/position 对位→crawl_bed 设 isInBed，
    但**不调 /sleep 确认** → 日不结束、不结束一天。刻意不传送进床格（会 redirect 弹回门口）。
    用于休息/等待/躺一下。**who 必填**：传自己的名字=躺自家；传别人的名字=躺那个人的床（爬床彩蛋）。
    传对名字就不用先到那栋屋：不在会自动走过去（和 go_sleep 同一条路）。
    🏝️ 在姜岛同 go_sleep：走大通铺（挤到别人床上才播报，睡空床安静）。
    就寝真过夜→go_sleep；**不想躺了就 walk_to 走离床格**（isInBed 自动变 false，无需特别起身）；
    就绪屏弹出想撤就绪/关屏→cancel。
    """
    api.ensure_roles()  # 端口↔角色可能翻转，先对齐
    if not (who or "").strip():
        return _with_state(_who_required("lie_bed/躺床"))
    err = _aim_sleep_home(who)   # 不在那栋屋 → 自动走过去（2026-09-19 起对任何人都生效）
    if err:
        return _with_state(err)
    r = api.approach_bed(who)
    if not r.get("ok"):
        return _with_state(f"❌ 躺床失败: {r.get('error')}")
    return _with_state(f"🛏️ 已躺上{r.get('player')}的床（只躺不睡，日没结束）。想过夜→go_sleep；撤就绪/关屏→cancel；想离开→walk_to 走离床格即可")


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
    lines.append(f"  📁 文件: sessions/{os.path.basename(_session_file) if _session_file else f'session_{_session_ts}.jsonl'}")
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
    """🧠 会话域（多数不用）。status 看缓冲 / set 改设置 / export 导出记忆。→ help(session)。

    Args:
        ops: status/set/export
        kw: set 的 {setting,value}
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

# 🪓 砍树放行名单（恒 2026-09-12 拍板）：默认 `allow=[]` ⇒ **只砍橡/枫/松(Tree:1~3)**，
#    特殊树种（蘑菇树7/桃花心木8/苔雨树10~12/神秘树13/棕榈6·9）**默认保护**。
#    由来=真机实测：`chop_trees.py` 的 docstring 写"1~3"、代码却 `startswith("Tree:")` 全收，
#    站在恒的蘑菇树堆旁，最近的可砍目标就是 Tree:7 —— `farm 砍树` 一跑第一斧就砍掉蘑菇树（Farm 上 15 棵）。
#    `clear_area`（清地块）同一个口子，一并受这份名单管。
#    `settings chop 蘑菇树,桃花心木` 放行 / `settings chop none` 收回 / `settings chop all` 全放行。
_CHOP_DEFAULTS = {"allow": []}
_chop_cfg = dict(_CHOP_DEFAULTS)


def _chop_allow_arg() -> str:
    """把放行名单拼成脚本参数（`chop_trees`/`clear_area` 的 `--allow`）。
    ⚠️ `allow` 里存的就是 tree_types 认得的 id / `all`，这里直接逗号拼。"""
    allow = _chop_cfg.get("allow") or []
    return ",".join(allow)


def _settings_load():
    global _retired_tools, _bg_cfg, _storage_cfg, _mode_cfg, _sleep_cfg, _moss_cfg, _chop_cfg
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
            if isinstance(d.get("chop"), dict) and isinstance(d["chop"].get("allow"), list):
                # 🪓 放行名单：老存档没有这项 ⇒ 保持默认 []（只砍橡/枫/松）。
                #    ⚠️ 这里**不做名字解析**（存的就是 id），坏数据(a)当空处理即可，报错留给设置入口。
                _chop_cfg["allow"] = [str(x) for x in d["chop"]["allow"]]
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
                "chop": dict(_chop_cfg),
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
    lines.append(f"  🪓 砍树放行: {tt.allow_label(_chop_cfg.get('allow'))}"
                 f"（特殊树种默认保护；settings chop 蘑菇树,桃花心木 / none / all）")
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
            return _with_state("🚀 异步脚本已开启（长脚本便利工具自动后台跑，AI 可并行聊天/整理背包）")
        if v in ("off", "0", "false", "no", "关"):
            _bg_cfg["enabled"] = False
            _settings_save()
            return _with_state("🛑 异步脚本已关闭（便利工具退回同步等脚本跑完）")
        return _with_state(f"❌ async 要 on/off，收到「{value}」")
    elif setting in ("async_tools", "auto_async", "自动异步"):
        v = value.strip().lower()
        if v in ("on", "1", "true", "yes", "开"):
            _bg_cfg["auto_async"] = True
            _settings_save()
            return _with_state("🚀 长脚本自动异步已开启（钓鱼/挖矿/炸矿等便利工具自动后台跑，AI 不用手动后台）")
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
                           "需要连跑时用对应便利工具(白名单自动后台)/逐任务调脚本；短任务走对应域 op。")
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
    elif setting in ("chop", "砍树", "砍树放行"):
        # 🪓 砍树放行名单（恒 2026-09-12 拍板）：默认只砍橡/枫/松；特殊树种默认保护，
        #    这里放行一种或多种。值写法见 tree_types.parse_allow（名字/树号/none/all）。
        if not value.strip():
            return _with_state("🪓 砍树放行 = " + tt.allow_label(_chop_cfg.get("allow"))
                               + "\n  改：settings chop 蘑菇树,桃花心木（或树号 7,8）"
                               + "｜收回：settings chop none｜全放行：settings chop all（慎用）")
        _allow, _err = tt.parse_allow(value)
        if _err:
            # 宁报错别兜底：认不出就明确拒绝，别静默当成"没放行"或"全放行"
            return _with_state(f"❌ {_err}\n  （当前仍是：{tt.allow_label(_chop_cfg.get('allow'))}）")
        _chop_cfg["allow"] = _allow
        _settings_save()
        return _with_state(f"🪓 砍树放行 = {tt.allow_label(_allow)}"
                           + ("" if _allow else "（特殊树种全部受保护）"))
    return _with_state(f"❌ 未知设置「{setting}」（heartbeat/context_turns/async/state_interval/mode/auto_sleep/auto_sleep_time/pin/moss/chop）")


# ═══════════════════════════════════════════
#  🔍 check 超级工具（A2 查询域入口，2026-08-13 #10）
#  ⚠️ 边界：check_status=概览（状态条同款）；check_backpack=逐格详细。查啥用 check。
# ═══════════════════════════════════════════
@mcp.tool()
def check_ready_state() -> str:
    """🎪 就绪握手实况（`check what=ready` 的实现）——"卡在就绪框"的现场取证。

    ⚠️ **两侧都读**才看得出死锁在哪头（每个进程各有一份 `Game1.netReady`）：
      · 客户端 `numberReady==numberRequired` 却 `isReady=false` ⇒ **房主没放行**，客户端只能干等；
      · 房主 `locking=true` 但有人停在 `Ready`（没到 `Locked`）⇒ 握手卡在锁定阶段。
    详见 CHANGELOG ㊵；端点实现见 ModEntry.handleReadyState。
    """
    lines = ["🎪 就绪握手实况（客户端只认房主的 Finish，所以**两侧都要看**）"]
    seen = {}
    for label, is_host in (("AI", False), ("房主", True)):
        try:
            d = api.ready_state(host=is_host)
        except Exception as e:
            lines.append(f"  ⚠️ {label} 读不到：{type(e).__name__} {e}")
            continue
        if not d.get("ok"):
            lines.append(f"  ⚠️ {label}：{d.get('error')}")
            continue
        checks = d.get("checks") or []
        seen[label] = checks
        who = d.get("player") or "?"
        head = f"  · {label}({who}{'，房主' if d.get('isMaster') else ''})"
        if not checks:
            lines.append(head + "：没有进行中的就绪检查")
            continue
        lines.append(head)
        for c in checks:
            bits = [f"{c.get('id')} [{c.get('kind')}]",
                    f"{c.get('numberReady')}/{c.get('numberRequired')}",
                    f"state={c.get('state')}",
                    "已放行✅" if c.get("isReady") else "未放行❌"]
            if c.get("activeLockId"):
                bits.append(f"lock#{c.get('activeLockId')}")
            if "locking" in c:
                bits.append("正在锁人✅" if c.get("locking") else "没在锁人")
            lines.append("      " + " · ".join(bits))
            rs = c.get("readyStates")
            if rs:
                lines.append("        每人： " + "、 ".join(f"{k}={v}" for k, v in rs.items()))
    # 判词：把"卡在哪一头"直接说出来，省得每次现推
    for c in (seen.get("AI") or []):
        nr, nq = c.get("numberReady"), c.get("numberRequired")
        if isinstance(nr, int) and isinstance(nq, int) and nr >= nq and not c.get("isReady"):
            lines.append("  🔴 **客户端视角全员已就绪、却没放行 ⇒ 卡在房主侧**。"
                          "别 cancel（撤了就绪只会让房主更等不到人）；mod 会在卡满 "
                          "2.5s 后自动踹一脚，踹过会写 SMAPI 日志 [festival-ready]。")
            break
    return _with_state("\n".join(lines))


@mcp.tool()
def check(what: str, kw: dict | None = None) -> str:
    """🔍 查询域（what=...）——"查我自己 + 查我的世界"。status 全状态 / backpack 背包明细(逐格价值/星级) / worn 穿戴 / machines 机器 / look 环视周围 / quest 开任务日志 / profile 我的技能+职业分支(如是否 Luremaster 蟹笼免饵) / role 端口↔角色确认(AI=谁/host=谁) / ready 就绪握手实况(卡在就绪框时查)。完整 what 清单 → help(check)。

    带参的只有两个，参数放进 kw（同域工具的写法）：check(what="look", kw={"radius":30}) / check(what="chests", kw={"chest":2})。其余 what 全无参。

    Args:
        what: 查什么（status/backpack/worn/profile/role/…见 help(check)）
        kw: 仅 look(radius=10) / chests(chest=-1) 用得上；其余留空
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
        "quest": open_questlog, "quests": open_questlog, "任务": open_questlog,
        "chests": scan_chests, "箱子": scan_chests,
        "storage": storage_layout, "存储": storage_layout,
        "look": look_around, "周围": look_around, "环视": look_around,
        # 🧬🔌 2026-09-11 恒：profile/which_role 从顶层工具收进 check 域（都是"查我自己"，
        #   顶层 20→17）。两者定义在本函数之后，调用时才查全局名，故可用。
        "profile": profile, "技能": profile, "职业": profile, "职业分支": profile,
        "role": which_role, "角色": which_role, "端口": which_role, "我是谁": which_role,
        # 🎪 2026-09-13：就绪握手现场（卡在 ReadyCheckDialog 时查；两侧都读才看得出死锁在哪头）
        "ready": check_ready_state, "就绪": check_ready_state, "ready_state": check_ready_state,
    }
    fn = dispatcher.get(w)
    if fn is None:
        return _with_state(f"❌ 未知查询「{what}」（status/backpack/worn/machines/mine/silo/mastery/buildings/quest/chests/look/profile/role/ready）")
    # 🐛 2026-09-11 恒：本函数**原来把子函数 `fn()` 裸调**，一个参数都传不进去——可 `what` 里
    #    `chests`/`look` 是有参的（`scan_chests(chest=-1)` / `look_around(radius=10)`），
    #    文档（help(check) + TOOL_INVENTORY）却写着 `chest=N` / `radius=10`：**照着写必然无效**，
    #    而且因为 MCP schema 里只有 `what`，AI 连"传了没生效"都看不出来。
    #    现在接上 `kw`，走与域工具**同一套** `_filter_kw`（别名归一 + 收不下的键点名，不静默吞）。
    #    `_kw_doc_check.py` 就是靠这条"域转发不转发 kw"的判据把这处挖出来的。
    import inspect
    try:
        sig = inspect.signature(fn)
    except (ValueError, TypeError):
        sig = None
    call_kw, _dropped = _filter_kw(sig, _unpack_kw(kw))
    # 🧪 同上：传了"只预览"的参数而本查询不认 ⇒ 拦住，别静默当成真查询/真动作
    _refuse = _dry_intent_refusal(w, _dropped, sig)
    if _refuse:
        return _with_state(_refuse)
    out = fn(**call_kw)
    if _dropped and isinstance(out, str):
        note = _dropped_kw_note(w, _dropped, sig)
        if _STATE_SEP in out:                    # 点在状态条**前面**（状态条永远压尾）
            body, _, strip = out.partition(_STATE_SEP)
            out = f"{body}\n\n{note}{_STATE_SEP}{strip}"
        else:
            out = f"{out}\n\n{note}"
    return out


# ═══════════════════════════════════════════
#  🌾⛏️🐄 域工具（A2 十域合并，组合式 ops，2026-08-14 #2）
#  十域（08-12 定稿）：farm / care / mine / social / interact / menu / shop /
#  daily / inspect(=check) / map；surroundings/screenshot/run_script 独立。
#  组合式 ops：域工具一次调用传多个 op（空格分隔），顺序执行多个原语，
#  AI 自己决定粒度（如 farm(ops="till plant water")）。
# ═══════════════════════════════════════════
_STATE_SEP = "\n\n╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"

# ➤ 跨域参数别名归一（2026-09-05 恒：map npc=name / social friendship=npc_name / storage find=name
#   **不一致**，AI 传 npc/item/name 常报"参数错"）。候选链按语义优先级，取第一个出现在目标签名里的。
_TARGET_ALIAS = {
    "npc": ("npc_name", "name"),
    "npc_name": ("name",),
    "item": ("item_name", "name"),
    "item_name": ("name",),
    "name": ("npc_name", "item_name"),
}


def _normalize_kw_key(k, sig) -> str:
    """把 AI 常用别名键改写成目标函数签名里的正式参数名；改不到就保留原键（报错逻辑不变）。"""
    if sig is None or k in sig.parameters:
        return k
    for cand in _TARGET_ALIAS.get(k, ()):
        if cand in sig.parameters:
            return cand
    return k


def _unpack_kw(kw) -> dict:
    """🐛 FastMCP 对 **kw 函数生成的 schema 是 {ops, kw}，实际调用后 **kw 收成 {"kw": {...}} 嵌套
    ——解包回 {...}（2026-08-19 实测：带参域工具一直收不到参）。None 归一成 {}。"""
    if isinstance(kw, dict) and set(kw) == {"kw"} and isinstance(kw.get("kw"), dict):
        return kw["kw"]
    return kw or {}


def _filter_kw(sig, kw) -> tuple:
    """按签名过滤 kw → (call_kw, dropped)。
    · 目标吃 `**kw`（如 farm 各 op）→ **原样全给**，由目标自己过滤（`_farm_kw_norm` 会报错）；
    · 否则只留签名里真有的键，别名先经 `_normalize_kw_key` 归一；
      收不下的键**返回给调用方去点名**——绝不静默吞（那是本项目最难发现的坑，见 _ops_run 注释）。"""
    if sig and any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(kw or {}), []
    call_kw, dropped = {}, []
    for _k, _v in (kw or {}).items():
        _nk = _normalize_kw_key(_k, sig)
        if sig is not None and _nk in sig.parameters:
            call_kw[_nk] = _v
        else:
            dropped.append(_k)
    return call_kw, dropped


# 🧪 调用方传了"只预览、别真做"的参数，而该 op 不认识 ⇒ **拒绝这一 op**（2026-09-17 恒拍板）
#    **真机教训**（session_log:1092）：`farm(ops="harvest", kw={"dry_run": True})` —— 当时只是
#    **警告**参数被丢掉，然后**照样真开机割了菜**。调用方以为在看预览、现实却发生了不可逆的事，
#    与恒「宁报错别兜底 / 缺参数就明确报错别长歪」是同族病。
#    ⚠️ **为什么按 op 拒、不拒整次调用**：一次调用可以带多个 op 共用一份 kw
#       （`farm(ops="till plant", kw={...})`），整次报错会让本来跑得动的 op 一起陪葬
#       ——同 `_dropped_kw_note` 上面那条注释的理由。`_dropped` 本来就是**按 op 算**的，
#       所以按 op 拒既拦住"以为在干跑"、又不误伤兄弟 op。
#    ⚠️ **为什么只认这几个名字**：它们是无歧义的"预览"语义。`test`/`check_only` 这类
#       可能撞上真参数名，不列进来（宁可漏拦，不可误拦）。
_DRY_INTENT_KW = {"dry_run", "dryrun", "dry", "preview", "simulate", "simulation"}


def _dry_intent_refusal(op: str, dropped: list, sig) -> str | None:
    """传了"只预览"的参数而本 op 不认 ⇒ 返回拒绝文案；否则 None（照常执行）。"""
    hit = sorted({str(k) for k in dropped if str(k).strip().lower() in _DRY_INTENT_KW})
    if not hit:
        return None
    avail = ", ".join(p.name for p in sig.parameters.values()) if sig else "?"
    return (f"❌ op「{op}」**拒绝执行**：你传了 {hit}（=只预览、不真做），"
            f"但本 op 不认识它（可用参数: {avail}）。照原样跑下去就变成**真做了**、跟你想要的正相反，"
            f"所以这里**一下都不动** —— 确实要真做请去掉该参数重发；本 op 若有干跑模式，请用它的官方参数名。")


def _dropped_kw_note(op: str, dropped: list, sig) -> str:
    """参数名写错时的点名文案（AI 一眼能改）。"""
    avail = ", ".join(p.name for p in sig.parameters.values()) if sig else "?"
    return (f"⚠️ op「{op}」忽略了无法识别的参数 {sorted(dropped)}"
            f"（此 op 可用参数: {avail}）——参数名写错不会报错，别以为它生效了")


def _humanize_call_error(op: str, sig, e: TypeError) -> str:
    """把 `fn(**kw)` 抛的 Python 原生 TypeError 翻成人话（2026-09-12 全工具测试⑨）。

    起因：`settings ops=retire`（没给 tool_name）→ 报的是
    `❌ op「retire」参数错: settings_retire() missing 1 required positional argument: 'tool_name'`
    —— 中文壳里裹着 Python 原生文案，和当天修的 `box`（`'>=' not supported between ...`）同一类。
    只翻译**已知的几种**，认不出的**原样返回**（宁可难看也别把信息吞掉，见 09-12 ①"宁报错别兜底"）。
    """
    msg = str(e)
    avail = ", ".join(p.name for p in sig.parameters.values()) if sig else "?"
    m = re.search(r"missing \d+ required positional arguments?: (.+)", msg)
    if m:
        names = m.group(1).replace("'", "").replace(" and ", ", ")
        return f"❌ op「{op}」缺必需参数: {names}（此 op 可用参数: {avail}）"
    m = re.search(r"unexpected keyword argument '([^']+)'", msg)
    if m:
        return f"❌ op「{op}」参数名不认识: {m.group(1)}（此 op 可用参数: {avail}）"
    m = re.search(r"got multiple values for argument '([^']+)'", msg)
    if m:
        return f"❌ op「{op}」参数重复给了: {m.group(1)}（此 op 可用参数: {avail}）"
    return f"❌ op「{op}」参数错: {msg}（此 op 可用参数: {avail}）"


def _ops_run(ops_str: str, dispatch: dict, kw: dict) -> str:
    """组合式 ops 执行器：空格/逗号拆多 op 逐个执行，kw 按签名自动过滤。
    dispatch: {op: callable}。结果去内嵌状态条，由调用方最后统一 _with_state 附一次。"""
    import inspect
    kw = _unpack_kw(kw)
    ops = [o for o in re.split(r"[\s,，]+", (ops_str or "").strip()) if o]
    if not ops:
        return "❌ ops 为空（如 farm(ops=\"till plant water\")）"
    # 🌾 2026-09-19：「一次调用、一份 kw 共用」是**官方用法**（如 `farm ops="till plant water"`），
    #    所以"某个 op 收不下的键"未必是写错字——**可能只是同次调用里另一个 op 的参数**。
    #    先把"本次调用里所有 op 认得的参数名"并起来，下面据此把两种情形分开说
    #    （否则 `water`/`harvest` 这种零参 op 会打一句误导的"参数名写错不会报错"，把 AI 带沟里）。
    _sib_params = set()
    for _o in ops:
        _f = dispatch.get(_o)
        if _f is None:
            continue
        try:
            _s = inspect.signature(_f)
        except (ValueError, TypeError):
            continue
        _sib_params |= {p.name for p in _s.parameters.values()
                        if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)}
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
            # kw 过滤 + 别名归一（别名：AI 传 npc/item/npc_name/item_name/name 都能落到正式参数名）
            call_kw, _dropped = _filter_kw(sig, kw)
            # 🧪 传了"只预览"的参数而本 op 不认 ⇒ 拦住这一 op，别把它当真做跑了（见 _dry_intent_refusal）
            _refuse = _dry_intent_refusal(op, _dropped, sig)
            if _refuse:
                results.append(_refuse)
                continue
            # ⚠️ 2026-09-11：标记"本层产生的状态条会被丢掉"——里面所有 op 都自带 _with_state，
            #    下面会把它们的内嵌状态条整条砍掉、由域工具在外层统一再附一次。
            #    期间"变化才报"的注入（_sit_hint 等）据此闭嘴且**不消费**，否则变化被内层吃掉、
            #    外层重建判定"没变化" → 整行对 AI 失踪（2026-09-11 🪑 踩坑，见 _OPS_INNER 注释）。
            _OPS_INNER["n"] += 1
            try:
                out = fn(**call_kw)
            finally:
                _OPS_INNER["n"] -= 1
            if isinstance(out, str):
                if _STATE_SEP in out:
                    out = out.split(_STATE_SEP)[0]   # 去内嵌状态条，最后统一加一次
                results.append(out)
            else:
                return out   # 非文本结果（如图片）直接返回，不能 join
            if _dropped:
                # ⚠️ 2026-09-11 恒：这里原来是**静默丢掉**的。AI 把参数名写错（`radius` 写成 `r`、
                #    该用 `tile_x` 写成 `x`）→ 调用照常成功、参数压根没进去，AI 永远发现不了
                #    （TOOL_INVENTORY 自己标注过这是"本项目最容易踩且最难发现"的坑）。现在当场点名。
                #    为什么是 ⚠️ 不是 ❌：一次调用可以带多个 op（`farm(ops="till plant")`）共用一份
                #    kw，报错会让本来跑得动的那个 op 一起陪葬；点名 + 列出该 op 的真参数名，一眼能改。
                #    （farm 域另有更强的 `_farm_kw_norm`：那边 op 吃 **extra、自己报错，走不到这层。）
                # 🆕 2026-09-19：先分清"写错字"和"这是**同次调用里别的 op** 的参数"——
                #    后者不是错，别打误导的"参数名写错"（`farm ops="till plant water"` 里
                #    `water` 零参，x/y/seed_name 全会被丢掉 ⇒ 老文案会连打三行"你写错了"）。
                #    ⚠️ 措辞**别用 `⚠️ op「` 开头**：`gen_tool_checklist.py --from-log` 把那个前缀
                #    当成工具报错，会把一次正常调用记成 ❌。
                _other = sorted(k for k in _dropped if k in _sib_params)
                _typo = sorted(k for k in _dropped if k not in _sib_params)
                if _typo:
                    results.append(_dropped_kw_note(op, _typo, sig))
                if _other:
                    results.append(f"  ⚠️ 已忽略参数 {_other}——那是同一次调用里**别的 op** 的参数"
                                   f"（本 op 用不到，不是写错字、也不用改）")
        except TypeError as e:
            # 2026-09-03 恒：参数名猜错（缺参）→ 直接列出可用参数名，别再让 AI 靠报错猜
            # 2026-09-12：再往前一步，把 Python 原生文案翻成人话（_humanize_call_error）
            results.append(_humanize_call_error(op, sig, e))
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
    # 🐄 2026-09-16 恒：畜棚/鸡舍内部**本来就是 farm 的工作场所** —— hay 加干草（饲料槽是畜棚
    #    自带的，棚外没有）、喂水 宠物碗、畜舍 摸动物，全都只能在棚内做。以前不在表里 ⇒ 棚内调
    #    farm 会吃到「💡 当前在Deluxe Barn，farm通常在Farm做；可先 map go Farm」的**反建议**
    #    （饲料槽那事就是这么被拱出来的），状态条也只显示「🛠️ 可用域: cabin」。
    #    ⚠️ 必须列**全名**：`_is_domain_applicable` 的前缀匹配是 `cur.startswith(p)`，而
    #       "Deluxe Barn" 的限定词在**前面** ⇒ 往 DOMAIN_PREFIX 塞 "Barn" 压根匹配不上
    #       （2026-09-16 第一版就栽在这：doors 测全绿、这条建议却纹丝不动）。矿洞能用前缀
    #       是因为 `UndergroundMine50` 的数字在后头。
    #    ⚠️ 只能**追加在尾部**：`DOMAIN_HOME[domain][0]` 被当建议文案里的「家」用，挪了会改口径。
    #    ⚠️ 2026-09-16 同批补：**棚屋(Big Shed)/地窖(Cellar)** 也是 farm 的工作场所（小桶/罐头瓶/木桶
    #       全在里面）——刚补完畜棚鸡舍就当场又栽在 Big Shed 上（在小桶屋里上料，头顶还挂着
    #       「💡 可先 map go Farm」）。名单与导航共用 `locations.*`（同一件事，别写两份）。
    "farm": ["Farm", "Greenhouse", "IslandWest", "IslandNorth", "IslandEast",
             *locations.FARM_INTERIOR_BUILDINGS],
    "mine": ["Mine", "SkullCave"],
    # 🏠 2026-08-16 恒：小屋域=屋里（FarmHouse/Cabin/岛屋）enum 引导
    "cabin": ["FarmHouse", "Cabin", "IslandFarmHouse"],
}
# 前缀匹配（矿洞/火山各层是独立 location）—— ⚠️ 只适用"限定词在后"的名字
#    （UndergroundMine50 / VolcanoDungeon3）。"Deluxe Barn" 这种限定词在前的**不能**用前缀，
#    得去 DOMAIN_HOME 列全名，见那里 2026-09-16 的备注。
DOMAIN_PREFIX = {
    "mine": ["UndergroundMine", "VolcanoDungeon"],
    # 🏠 地窖 Cellar / Cellar2 … Cellar8：限定词在后，"Cellar" 前缀正好能一次盖住 8 个。
    #    ⚠️ 只进"域适用区"不进导航（出门是两跳，见 locations.FARM_MACHINE_PREFIXES 备注）。
    "farm": list(locations.FARM_MACHINE_PREFIXES),
}
# 免建议的 op：导航类（自己会导航）/ API 直操作
DOMAIN_EXEMPT = {
    "mine": {"go", "去"},
    "farm": {"buy", "买", "买动物"},   # 买动物去玛妮牧场不在农场，豁免建议（care 域 2026-09-02 并入 farm）
    "fish": {"go", "去", "钓", "fish"},
    "cabin": {"sleep", "睡", "睡觉"},   # sleep 自己会走过去（2026-09-19 起传谁的名字就走谁家）
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


def _farm_require_xy(x: int, y: int):
    """x/y **必填**（2026-09-10 恒拍板：删掉「缺坐标→玩家面向格」的兜底）。
    返回 ((x, y), None)；缺坐标返回 (None, 错误提示串)。

    「为什么删」——旧兜底（2026-09-03~09-10）的来历与病根：
      来历：AI 锄完田再 plant 不给坐标 → 落到祖传硬编码 (60,10)（离田八竿子远、种子不消耗）。
            当时的修法是「缺坐标就取玩家面向格当田块起点」。
      病根：`_farm_rect` 的横纵生长方向是**固定**的（一律向右 + 向下长），
            于是朝向一旦是**上(0) 或 左(3)**，起点=面向格、矩形就**长回玩家自己脚下**：
              朝上 → 田=(px,py-1)..(px+L-1, py+R-2)，含 (px,py)
              朝左 → 田=(px-1,py)..(px+L-2, py+R-1)，含 (px,py)
            AI 站着不动锄一块朝上的田，蓄力覆盖正好把自己站的那格犁了（恒：「耕脚下格」）。
            朝下/朝右没事 ⇒ 这毛病**只在某些朝向**冒出来，从行为反推特别难查。
      附带好处：坐标必填 ⇒ 绝不会把 -1 传进 /tool_area ⇒ C# 的 ±25 自动检测分支
            （曾致补漏狂挥 DoFunction、5×1 锄地烧 366 体力）永远碰不到。
    """
    if x >= 0 and y >= 0:
        return (x, y), None
    here = ""
    try:
        p = api.state().get("player") or {}
        here = f"你(轮回)现在在 ({p.get('x')},{p.get('y')})，"
    except Exception:
        pass
    return None, (
        f"❌ 必须传 x/y（缺坐标不再兜底）。{here}"
        "旧的「缺坐标→玩家面向格」兜底已删：矩形一律向右下长，朝上/朝左时会**长回你自己脚下**、"
        "连站位那格一起犁（耕脚下格）。请明确说要动哪几格——先传 x/y（想先看地块就先调 farm plot 拿矩形），"
        "再用 rows/length/direction 说尺寸。"
    )


def _farm_rect(x: int, y: int, rows: int, length: int, direction: str):
    """由 x,y,rows,length,direction 算矩形角点 (x1,y1,x2,y2)。"""
    dx, dy = (1, 0) if direction == "horizontal" else (0, 1)
    rdx, rdy = (0, 1) if direction == "horizontal" else (1, 0)
    x2 = x + dx * (length - 1) + rdx * (rows - 1)
    y2 = y + dy * (length - 1) + rdy * (rows - 1)
    return min(x, x2), min(y, y2), max(x, x2), max(y, y2)


# 🌾 farm 域「兄弟参数」集合（2026-09-19）：`farm(ops="till plant fertilize", kw={...})` 是**一份 kw 多个 op 共用**，
#   每个 op 只吃自己认识的那几个键。farm 各 op 都带 `**extra`（`_filter_kw` 因此"原样全给、由目标自己报错"），
#   所以**容错只能在这里做**：命中本集合的键 = "这次调用里给**别的** op 的参数" ⇒ 从 extra 摘掉并**在输出里点名**；
#   不在集合里的照旧 ❌ 未知参数（`lenght` 这类真拼错还是照样报错，别想糊过去）。
#   ⚠️ `dry_run`/`preview` 这类"**只预览**"键**故意不在集合里**：它们必须走拒绝路径（见 `_farm_kw_norm`），
#      否则一次组合调用会把"以为在干跑"变成"真干了"（2026-09-17 真机教训：dry_run 被丢掉后照样开机割菜）。
#   ⚠️ **加新 farm op 时记得把它的参数补进来** —— `check_design.py` 有一条静态检查盯着
#      （本集合必须 ⊇ farm dispatch 各 op 的参数并集），忘了补会被自检拦下。
_FARM_SIBLING_KW = frozenset({
    # 播种（_farm_plant）
    "seed_name", "seed", "trellis", "direct",
    # 锄地/规划（_farm_till、plan_farm_layout_tool）
    "layout", "hoe_level", "x1", "y1", "x2", "y2",
    # 化肥 / 机器 / 建筑
    "fertilizer_name", "machine_type", "location", "item", "count",
    # 动物 / 宠物 / 放置 / 砍收 / 清场
    "animal_type", "building", "name", "include_petted", "area", "steps", "radius", "all_plots",
    "margin",     # clear 的"外扩几格"（恒 2026-09-19：默认帮 AI 往外多清 2 格）
})


def _farm_kw_norm(x, y, rows, length, direction, extra, extra_ok=()):
    """农活域 kw 归一化：cols/col/width/len → length；未知 kw 报错，别静默吞（2026-09-03 恒）。
    返回 **(length, err, ignored)**：
      · 正常 → `(length, None, [被忽略的兄弟参数名])`；出错 → `(None, 错误串, [])`。
      · `ignored` 由调用方**拼进正文**点名（"已忽略 seed_name —— 那是 plant 的参数"），别静默吞。
    extra_ok = 「兄弟参数」集合（传 `_FARM_SIBLING_KW`）——见上面那段注释。"""
    for k in ("cols", "col", "width", "len"):
        if k in extra:
            length = extra.pop(k)
    # 🧪 "只预览"键**一律拒绝本 op**，绝不走下面的忽略路径（比 `_dry_intent_refusal` 更早拦：
    #    farm 各 op 吃 `**extra`，那边"按签名过滤"对这族从来不触发）
    _dry = sorted(str(k) for k in extra if str(k).strip().lower() in _DRY_INTENT_KW)
    if _dry:
        return (None,
                f"❌ 本 op **拒绝执行**：你传了 {_dry}（=只预览、不真做），但农活 op 没有干跑模式——"
                f"照原样跑下去就变成**真做了**、跟你想要的正相反，所以这里**一下都不动**。"
                f"确实要真做请去掉该参数重发。", [])
    ignored = sorted(set(extra) & set(extra_ok))
    for k in ignored:
        extra.pop(k)
    if extra:
        avail = "x y rows length direction" + (f" {' '.join(sorted(extra_ok))}" if extra_ok else "")
        return (None,
                f"❌ 未知参数 {sorted(extra)}。此 op 可用: {avail}"
                f"（每行几格写 length，或 cols/width 别名，别同时写；"
                f"若那是**别的 op** 的参数，请检查 op 名有没有写错）", [])
    return (length, None, ignored)


def _ignored_note(ignored) -> str:
    """兄弟参数被忽略时的正文点名行（`_ops_run` 只砍状态条，正文里的这行会留到 AI 眼前）。
    ⚠️ 措辞**别用 `⚠️ op「` 开头** —— `gen_tool_checklist.py --from-log` 把那个前缀判成工具报错。"""
    if not ignored:
        return ""
    return (f"  ⚠️ 已忽略参数 {sorted(ignored)}——那是同一次调用里**别的 op** 的参数"
            f"（本 op 用不到，没静默吞、也不会影响别的 op）")


def _snake_tiles(tiles):
    """把格子按"行分组、隔行反向"排成蛇形，减少拟人逐格锄的来回走位。"""
    by_row = {}
    for tx, ty in tiles:
        by_row.setdefault(ty, []).append(tx)
    out = []
    for i, ty in enumerate(sorted(by_row)):
        xs = sorted(by_row[ty])
        if i % 2:
            xs.reverse()
        out.extend((tx, ty) for tx in xs)
    return out


# ═══════════════════════════════════════════
#  🌿 「这格为什么动不了」—— till / plant / fertilize 三个 op 共用的一份判据
# ═══════════════════════════════════════════
# 恒 2026-09-19：「**地格上有杂草！**记得做相关检测和报告让 ai 除草再耕种所选区域啦」
# 起因：把一块长草的地丢给 till，报告只有「缺失 13 格（被杂物/水挡？）」+ `(69,21) Grass·地图没标可耕`
#   ① `Grass` 是**内部类型名**，AI 看不出"那是草、该拿镰刀"；
#   ② 「地图没标可耕」更是**误报**：真机对照 —— `(71,18)` 锄成了、(69,21) 没锄成，两格地图属性
#      **逐字节相同**（`Diggable:T / Type:Dirt`）。`diggable` 字段的真身是
#      `Diggable 属性 && !IsTileBlockedBy(...)`（ModEntry `BuildSurroundings`），**草/物件挡着也会 false**
#      ⇒ 拿它当"可耕"就会对着长草的地喊"不可耕"，AI 只能干瞪眼；
#   ③ 真因果：**草占着 terrainFeature 那个槽** ⇒ 先割掉才锄得出 HoeDirt。
#      真机链路验过：`clear(69,21)` 割草 → `till(69,21)` **1/1 成功**。
#
# 判据**全部来自 `/surroundings` 的字段**（terrain/object/resource/largeTerrain），不另立名单：
#   · "清得掉"那几种 = **与 `clear_area.py` 的 `TOOL_MAP` 对齐**（那脚本才是"能清什么"的真相源：
#     杂草/草→镰刀、石头→镐、树枝/树桩→斧）。**改那边这里要跟着改**。
#   · 其余带 object 的（洒水器/箱子/机器/稻草人/火把）= **设施，别清**。`clear` 的 TOOL_MAP 里
#     没有它们（不会被误铲），但 AI 看见"有东西挡着"很容易自己去敲 ⇒ 报告必须**分开说**。
_CLEARABLE_OBJ = {"Weeds": "🌿 杂草", "Grass": "🌿 草", "Stone": "🪨 石头",
                  "Twig": "🪵 树枝", "Weed": "🌿 杂草"}
_CLEARABLE_RES = {"LargeStump": "🪵 大木桩", "LargeLog": "🪵 大圆木",
                  "LargeBoulder": "🪨 大石头", "MeteoriteOre": "☄️ 陨石"}


def _tile_obstacle(info):
    """一格 `/surroundings` 数据 → `(中文标签, 能不能交给 farm ops="clear" 清掉)`。

    已锄好的地/已长作物的格 → `(None, False)`（那两种不是"障碍"，各有各的处理路径）。
    看不出是什么但确实挡路 → 返回**如实说"不知道"**的标签（别硬编个名字骗 AI）。
    """
    if not isinstance(info, dict):
        return (None, False)
    ter = str(info.get("terrain") or "")
    obj = str(info.get("object") or "")
    res = str(info.get("resource") or "")
    large = str(info.get("largeTerrain") or "")
    if ter == "HoeDirt" or info.get("crop"):
        return (None, False)                       # 已锄/已种 = 不是障碍
    if ter.startswith("Tree:"):
        return ("🌳 树（斧头）", True)
    if ter == "Grass":
        return ("🌿 草", True)
    if obj in _CLEARABLE_OBJ:
        return (_CLEARABLE_OBJ[obj], True)
    if res in _CLEARABLE_RES:
        return (_CLEARABLE_RES[res], True)
    if obj:
        return (f"🏗️ 设施·{obj}", False)            # 洒水器/箱子/机器…**别清**
    if res:
        return (f"🏗️ {res}", False)
    if large:
        return (f"🌳 {large}", False)
    if info.get("isWater") or info.get("water") is True:
        return ("💧 水", False)
    if info.get("passable") is False:
        return ("🚧 挡路（看不出是什么，拿 /dump_tile 细查）", False)
    return (None, False)


def _obstacle_lines(blocked, verb="锄") -> list:
    """`{类别: [(x,y,标签)]}` → 给 AI 看的几行（till/plant/fertilize 共用措辞）。

    ⚠️ 措辞要点：**说清下一步该敲哪个 op**，别只报"缺失 N 格"（恒：那样 AI 只能干瞪眼）。
    """
    lines = []
    _coords = lambda lst, n=8: " ".join(f"({x},{y})" for x, y, _ in lst[:n]) + ("…" if len(lst) > n else "")
    clear_t = blocked.get("clear") or []
    if clear_t:
        _names = "、".join(sorted({lab for _, _, lab in clear_t}))
        lines.append(f"  🌿 {len(clear_t)} 格被杂物挡着（{_names}）: {_coords(clear_t)}")
        lines.append("     → **先清一遍**：`farm ops=\"clear\"`（同一套 x,y,rows,length 坐标；"
                     "草/杂草走镰刀、石头走镐、树枝树桩走斧；**会自动往外多清 2 格**，不用自己放大）。"
                     "清完再" + verb + "就成（真机验过这条链：割草 → 锄地 1/1）")
    fac_t = blocked.get("facility") or []
    if fac_t:
        _names = "、".join(sorted({lab.split("·", 1)[-1] for _, _, lab in fac_t}))
        lines.append(f"  🏗️ {len(fac_t)} 格是**设施**（{_names}）: {_coords(fac_t)}")
        lines.append("     → ⛔ **别清**（洒水器/箱子/机器会跟着被收走）；那是规划该绕开的格")
    unt_t = blocked.get("untilled") or []
    if unt_t:
        lines.append(f"  🌱 {len(unt_t)} 格还没锄地: {_coords(unt_t)}"
                     " → 先 `farm ops=\"till\"`（一次说完也行：`farm ops=\"till plant\"`，**一份 kw 共用**）")
    return lines


def _farm_till(x: int = -1, y: int = -1, rows: int = 1, length: int = 1,
               direction: str = "horizontal",
               x1: int = -1, y1: int = -1, x2: int = -1, y2: int = -1,
               layout: int = 0, **extra) -> str:
    """🌾 锄地 —— **唯一入口**（2026-09-17 恒拍板收敛：旧的 `hoe`/布局锄、`tillfield`/蓄力锄 都并进来）。

    **两种给坐标的方式，二选一**：
      · `x,y` + `rows,length,direction` —— 起点 + 尺寸（老 `till` 的用法）
      · `x1,y1,x2,y2`                  —— 直接给矩形两角（老 `tillfield`/`hoe` 的用法）

    **两个维度分开管**（这正是恒记忆里那个"一个工具传坐标和布局参数就解决"的形态）：
      · `layout` 决定**锄哪些格** —— 0 标准 / 2 高级 / 3 铱 → **整块**；
                                  1 初级 → 只锄每个洒水器上下左右 4 格（十字，不锄整块）
      · **锄头等级 + 地块大小**决定**怎么锄** ——
          `layout=1` 初级布局      → **恒拟人逐格**（恒 2026-09-19：十字格天生离散，
                                      跟锄头等级无关，别因为"升级锄锄得动"就切一键）
          基础锄(0 级)             → **拟人逐格挥锄**（走过去→抬手→落下，看得见动作）
          升级锄 + **田块 ≤ 一次蓄力的覆盖格数** → **也逐格**（恒 2026-09-17：小块地蓄力是大炮打蚊子）
          升级锄 + 田块更大        → **蓄力**（`_till_rect`→`tool_area`，无动画但快得多）
        覆盖格数按等级：0级1格 / 铜3 / 钢5 / 金9(3×3) / 铱18(6×3)

    ⚠️ 尺寸默认 1×1，**不擅自扩**（恒 2026-09-03：旧默认 5×5 会把"就锄一下"扩成 25 格大田=意外耗体力）。
    ⚠️ 用 `x,y` 形式时 **x/y 必填**（恒 2026-09-10：缺坐标的兜底会朝上/朝左时长回自己脚下、连站位格一起犁）。
    🦶 逐格锄的**站位格**：优先目标正上方，被占则试 下/左/右（配对应朝向）；**四边都站不进去
        ⇒ 该格如实报缺失（"四邻没处站"），绝不瞬移过去站**（2026-09-19 恒）。
    """
    # ── 1. 解析矩形（给了 x1..y2 就用它，否则由 x,y + 尺寸算）──
    # ⚠️ 2026-09-19：**两个分支都要过 `_farm_kw_norm`**。原来只有 x,y 分支过，
    #    x1..y2 分支**一次都不查 extra** ⇒ `hoe(x1..y2, seed_nme="x")` 拼错参数被**静默吞掉**，
    #    与 "未知 kw 报错，别静默吞" 直接矛盾（顺手补上）。
    length, err, ignored = _farm_kw_norm(x, y, rows, length, direction, extra, _FARM_SIBLING_KW)
    if err:
        return err
    if min(x1, y1, x2, y2) >= 0:
        rx1, ry1 = min(x1, x2), min(y1, y2)
        rx2, ry2 = max(x1, x2), max(y1, y2)
    else:
        xy, err = _farm_require_xy(x, y)
        if err:
            return err
        x, y = xy
        rx1, ry1, rx2, ry2 = _farm_rect(x, y, rows, length, direction)
    _ig_note = _ignored_note(ignored)

    # ── 2. layout 决定"锄哪些格" ──
    try:
        layout = int(layout)
    except (TypeError, ValueError):
        return f"❌ layout 只能是 0/1/2/3，收到 {layout!r}（0标准 1初级 2高级 3铱）"
    if layout not in (0, 1, 2, 3):
        return f"❌ layout 只能是 0/1/2/3，收到 {layout}（0标准 1初级 2高级 3铱）"

    w, h = rx2 - rx1 + 1, ry2 - ry1 + 1
    if layout == 1:
        p = plan_farm_layout(rx1, ry1, rx2, ry2, layout)
        tile_list = [(tx, ty) for tx, ty in p["plant_tiles"]]
        head = f"🌾 初级洒水器布局锄地 ({rx1},{ry1})-({rx2},{ry2}) {w}x{h}"
        note = f"  🚿 {len(p['sprinklers'])} 个洒水器 | 十字 {len(tile_list)} 格"
    else:
        tile_list = [(cx, cy) for cy in range(ry1, ry2 + 1) for cx in range(rx1, rx2 + 1)]
        _lay_cn = {0: "标准", 2: "高级", 3: "铱"}[layout]
        head = f"🌾 {_lay_cn}布局锄地 ({rx1},{ry1})-({rx2},{ry2}) {w}x{h}"
        note = None

    # ── 3. 锄头等级决定"怎么锄" ──
    _select_best_hoe()
    try:
        hoe_level = (api.state().get("player") or {}).get("currentToolUpgrade", 0)
    except Exception:
        hoe_level = 0

    # 2026-09-17 恒：「既然高级工具也做了非蓄力逐个格子锄，**不妨在田块小于范围时切换成逐格**
    #   （如 3×6 的铱锄锄 3×3 的地）」⇒ 除基础锄恒逐格外，**升级锄在小块地也走逐格** ——
    #   蓄力一次就覆盖 N 格，地里只要 ≤N 格就没必要蓄（大炮打蚊子），逐格挥更像人、也看得见动作。
    #   容量表取自 till_field docstring 的蓄力范围（0:1格 1:3线 2:5线 3:3×3 4:6×3）。
    # 🆕 2026-09-19 恒：「**初级洒水器布局时，无论什么等级的锄头都用逐格**」——
    #   十字格是**离散**的（不是一块矩形），蓄力/一键在这里省不了多少、却把挥锄观感丢了；
    #   域指引早就写着 layout=1「锄法与锄头等级无关」，是实现里那条 `_need <= _cap` 把它带偏了。
    _HOE_CHARGE_TILES = {0: 1, 1: 3, 2: 5, 3: 9, 4: 18}
    _need = len(tile_list)
    _cap = _HOE_CHARGE_TILES.get(hoe_level, 1)
    if layout == 1 or hoe_level == 0 or _need <= _cap:
        # 逐格挥锄（走位 → 面向目标 → 挥锄落下）
        # ⚠️ **必须 `use_tool()`**（→ `/tool`，`BeginUsingTool()`+`EndUsingTool()` 两个都调）；
        #    原来调的 `use_item()`（→ `/use`）**只抬手不落锄** —— 2026-09-17 真机：报 "0/9 锄出"、
        #    恒当场看见"举着锄头没落下"。别改回去。
        #
        # 🦶 站位格：优先目标**正上方**（面向下）；被占就退回 下/左/右。
        #    ⚠️ 2026-09-19 恒真机逮到：原来写死 `walk_natural(tx, ty-1)`，站位格被箱子占住时
        #    `walk_natural` **静默走 `position` 兜底** ⇒ 人**落在箱子格上**挥锄（观感=站进箱子里）。
        #    实测复现：`walk_natural(59,17)` 返回 False、落点就是 (59,17)（那格是 Chest）；
        #    测试田 y=17 一排箱子，24 格里 5 格如此。
        #    判据 = `api.stand_tile`（stardew_api 里的**共用件**，锄/种/撒化肥都用它；
        #    底层 `/passable_rect` 的 passable = 寻路同款 IsTilePassable，含物件/家具/牲畜）。
        #    **四边都站不进去就如实报缺失、不瞬移**；拿不到可走信息则**直接报错不干活**
        #    （宁报错别兜底：盲走正是上面那个 bug 的来源）。
        _walk_ok = api.walk_ok_tiles(rx1 - 1, ry1 - 1, rx2 + 1, ry2 + 1)
        if _walk_ok is None:
            return "❌ 拿不到田块的可走信息（/passable_rect 失败）——本次没锄任何格（不盲走）"
        no_stand, teleported = [], 0
        for tx, ty in _snake_tiles(tile_list):
            stand = api.stand_tile(tx, ty, _walk_ok)
            if stand is None:
                no_stand.append((tx, ty))   # 四邻都被占 → 不瞬移，留给报告说清楚
                continue
            # walk_natural 返回 False = 走不过去、它自己用了 position 兜底（站位格本身可站，
            # 只是路被挡）——数一数报出来，别让"瞬移过去站的"混在"拟人逐格"里没人知道。
            if not api.walk_natural(stand[0], stand[1]):
                teleported += 1
            api.face(stand[2])
            time.sleep(0.1)
            api.use_tool()
            time.sleep(0.4)   # 挥锄动画
        if layout == 1:
            method = f"拟人逐格(初级布局恒逐格·与锄头等级无关，锄头{hoe_level}级，{_need}格)"
        elif hoe_level == 0:
            method = "拟人逐格(基础锄)"
        else:
            method = f"拟人逐格(锄头{hoe_level}级，{_need}格 ≤ 蓄力{_cap}格)"
    else:
        # 整块 → 复用 _till_rect（tool_area 蓄力 + DLL 自检补漏），它自带报告
        return _till_rect(rx1, ry1, rx2, ry2) + ("\n" + _ig_note if _ig_note else "")

    # ── 4. 逐下检测（判据 `terrain == "HoeDirt"`；2026-09-17 真机 A/B 校过：手锄一格立刻读到）──
    # 🦶 站田心扫描**要挑能站的格**：原来写死 `position(田心)`，田心是洒水器/箱子格时人就被闪上去
    #    （恒 2026-09-19 真机："踩到洒水器了"）。挑不出能站的格就**原地扫**（不硬瞬移）。
    time.sleep(0.4)
    _park = api.stand_near(tile_list, _walk_ok, (rx1 + rx2) // 2, (ry1 + ry2) // 2)
    if _park:
        try:
            api.position(_park[0], _park[1])
            time.sleep(0.3)
        except Exception:
            pass
    surr = api.surroundings(max(w, h) // 2 + 6)
    tiles = {(t["x"], t["y"]): t for t in surr.get("tiles", [])}
    tilled = [pt for pt in tile_list if tiles.get(pt, {}).get("terrain") == "HoeDirt"]
    missing = [pt for pt in tile_list if tiles.get(pt, {}).get("terrain") != "HoeDirt"]
    lines = [f"{head} | {method}"]
    if note:
        lines.append(note)
    if _ig_note:
        lines.append(_ig_note)
    lines.append(f"  ✅ {len(tilled)}/{len(tile_list)} 锄出")
    if missing:
        _no_stand = set(no_stand)
        _blocked, _other = {}, []
        for mx, my in missing:
            label, cleanable = _tile_obstacle(tiles.get((mx, my), {}))
            if label and cleanable:
                _blocked.setdefault("clear", []).append((mx, my, label))
            elif label:
                _blocked.setdefault("facility", []).append((mx, my, label))
            else:
                _other.append((mx, my))
        lines.append(f"  ⚠️ 缺失 {len(missing)} 格:")
        for mx, my in missing[:10]:
            info = tiles.get((mx, my), {})
            label, _cl = _tile_obstacle(info)
            what = label or (info.get('object') or info.get('resource')
                             or info.get('terrain') or '裸地')
            # 🆕 2026-09-17 恒：「报错加报目标格障碍物」——把"这格压根不可耕"和
            #    "可耕但被挡"分开说，否则一片草地只会得到一句没头没脑的"缺失 N 格"。
            #    ⚠️ HoeDirt 还要报不可耕就是误报（已翻的地不带 diggable 字段，真机实测）。
            # 🆕 2026-09-19：再加一条"**四邻没处站**"——那是站位问题不是地的问题，
            #    不写清楚 AI 只会盯着这格发呆（原本这里只会说"裸地"，看着像工具失灵）。
            # 🆕 2026-09-19（恒「**地格上有杂草**」）：这句 "地图没标可耕" 原来**对草格是误报** ——
            #    `diggable` 那个字段 = `Diggable 属性 && !IsTileBlockedBy(...)`，**草/物件挡着也会 false**；
            #    真机对照：地图属性逐字节相同的两格，一格锄成了一格没锄成（差别就是那格长着草）。
            #    ⇒ 只有"**看不出任何障碍**、地图又没标可耕"才说这句，有障碍的交给下面分类说。
            marks = []
            if (mx, my) in _no_stand:
                marks.append("四邻没处站")
            if not label and not info.get('diggable') and info.get('terrain') != 'HoeDirt':
                marks.append("地图没标可耕")
            if marks:
                what = f"{what}·{'、'.join(marks)}"
            lines.append(f"    ({mx},{my}) {what}")
        lines.extend(_obstacle_lines(_blocked, verb="锄"))
        if _other:
            lines.append(f"  ❔ 另 {len(_other)} 格看不出障碍也没锄成（地图没标可耕地/被水挡？）: "
                         + " ".join(f"({mx},{my})" for mx, my in _other[:8])
                         + ("…" if len(_other) > 8 else ""))
    return "\n".join(lines)


def _farm_plant(seed_name: str = "", seed: str = "", x: int = -1, y: int = -1, rows: int = 1,
                length: int = 1, direction: str = "horizontal",
                x1: int = -1, y1: int = -1, x2: int = -1, y2: int = -1,
                layout: int = 0, direct: bool = False, trellis: bool = False, **extra) -> str:
    """🌱 播种 —— **唯一入口**（2026-09-19 恒拍板收敛：`plant`/`sow`/`plantlayout`/`播种规划`
    四个名字都指这里；旧的 `till_plant` 一条龙**已退役**，要锄+种就 `farm ops="till plant"`，
    一次调用共用一份 kw，锄地会自动忽略 `seed_name`）。

    **两种给坐标的方式，二选一**（学 `_farm_till`）：
      · `x,y` + `rows,length,direction` —— 起点 + 尺寸
      · `x1,y1,x2,y2`                  —— 直接给矩形两角

    **`layout` 决定种哪些格**：0 标准（= 整个矩形，缺省）/ 1 初级（洒水器十字）/
    2 高级（3 的倍数，扣掉洒水器位）/ 3 铱（5 的倍数）—— 算格全交给 `plan_farm_layout`。
    `trellis=True` = 爬架作物（啤酒花/青豆/葡萄，不可通过格）⇒ **种2留1** 留走道；
    种子名以 `Starter` 结尾时**自动**按爬架算（沿用老一条龙的行为）。
    `direct=True` 用瞬移（格多的密排布局快），默认**走位拟人**。

    ⚠️ 2026-09-10 恒：用 `x,y` 形式时 **x/y 必填**（缺坐标不再兜底成"玩家面向格"）。
    ⚠️ 尺寸默认 1×1 不擅自扩——AI 报多少格就种多少格。
    🦶 站位格走 `api.stand_tile`（和锄地同一个共用件）：优先目标正上方，被占则 下/左/右，
       **四邻全占就如实报缺失、绝不瞬移过去站**。
    """
    # ── 1. 归一化 + 解析矩形（两个分支**都**过 `_farm_kw_norm`，别让未知参数静默漏过）──
    if seed != "" and seed_name == "":
        # 🕳️ `seed` 是**兼容暗桩**：真机 session_log:992/1079 用的就是 `seed`（不是 seed_name）。
        #    文档里一律写 `seed_name`（`_kw_doc_check.py` 会盯着文档与签名的一致性）。
        seed_name = seed
        seed = ""
    if seed != "":
        extra["seed"] = seed
    length, err, ignored = _farm_kw_norm(x, y, rows, length, direction, extra, _FARM_SIBLING_KW)
    if err:
        return err
    if min(x1, y1, x2, y2) >= 0:
        rx1, ry1 = min(x1, x2), min(y1, y2)
        rx2, ry2 = max(x1, x2), max(y1, y2)
    else:
        xy, err = _farm_require_xy(x, y)
        if err:
            return err
        x, y = xy
        rx1, ry1, rx2, ry2 = _farm_rect(x, y, rows, length, direction)
    _ig_note = _ignored_note(ignored)
    if not seed_name:
        return "❌ 要传 seed_name（种子名，如 Parsnip Seeds）——不传就没法种"

    # ── 2. layout 决定"种哪些格" ──
    try:
        layout = int(layout)
    except (TypeError, ValueError):
        return f"❌ layout 只能是 0/1/2/3，收到 {layout!r}（0标准 1初级 2高级 3铱）"
    if layout not in (0, 1, 2, 3):
        return f"❌ layout 只能是 0/1/2/3，收到 {layout}（0标准 1初级 2高级 3铱）"
    if not trellis and seed_name.lower().endswith("starter"):
        trellis = True    # 🌱 爬架种子自动留走道（老一条龙的行为，2026-09-19 并进来）
    p = plan_farm_layout(rx1, ry1, rx2, ry2, layout, trellis=trellis)
    plant_tiles = [(tx, ty) for tx, ty in p["plant_tiles"]]
    if not plant_tiles:
        return _with_state("⚠️ 这块地没有可播种的格子"
                           + ("（铱洒水器不做爬架，这块交给房主）" if trellis and layout == 3 else ""))
    w, h = rx2 - rx1 + 1, ry2 - ry1 + 1
    _lay_cn = {0: "整块", 1: "初级", 2: "高级", 3: "铱"}[layout]
    head = (f"🌱 按{_lay_cn}布局播种「{seed_name}」({rx1},{ry1})-({rx2},{ry2}) {w}x{h}"
            + ("（🧗爬架走道）" if trellis else "")
            + (" | 🚀瞬移" if direct else " | 🚶拟人走位"))

    # ── 3. 种子在背包？ ──
    try:
        inv = api.state().get("inventory", [])
    except Exception:
        inv = []
    if not any(seed_name in (i.get("name") or "") or seed_name in (i.get("displayName") or "")
               for i in inv):
        return _with_state(f"❌ 背包里没有「{seed_name}」（要**英文内部名**，如 Parsnip Seeds / Parsnip Seeds 这类；"
                           f"先 check what=backpack 看有什么）")

    # ── 4. 回到能种的地方（温室/姜岛/附近有 HoeDirt 则不挪，见该函数注释）──
    warp_log = _warp_home_if_needed("Farm")

    # ── 5. 扫地形：① 目标地块在不在当前图 ② 哪些格要跳过（已有作物 / 设施占格）──
    #    ⚠️ 判据用"**田心格在不在扫描结果里**"，不能用"tiles 为空"——半径不够时也会空。
    def _scan(radius):
        try:
            return {(t["x"], t["y"]): t for t in api.surroundings(radius).get("tiles", [])}
        except Exception:
            return {}
    radius = max(w, h) // 2 + 8
    tiles = _scan(radius)
    if not any(pt in tiles for pt in plant_tiles):
        # 当前位置扫不到田块（站得远，或**压根不在同一张图**）→ 站到田里再扫一次。
        # 🦶 落脚点**要挑能站的**：田心是洒水器/箱子格时不能硬闪上去（恒 2026-09-19："踩到洒水器了"）。
        _walk_ok0 = api.walk_ok_tiles(rx1 - 1, ry1 - 1, rx2 + 1, ry2 + 1)
        _park0 = api.stand_near(plant_tiles, _walk_ok0, (rx1 + rx2) // 2, (ry1 + ry2) // 2)
        if _park0:
            try:
                api.position(_park0[0], _park0[1])
                time.sleep(0.4)
                tiles = _scan(radius)
            except Exception:
                pass
    if not any(pt in tiles for pt in plant_tiles):
        cur = (api.state().get("location") or {}).get("name", "?")
        return _with_state(f"❌ 目标田块 ({rx1},{ry1})-({rx2},{ry2}) 不在当前地图（现在在 {cur}）"
                           f"——一个动作都没做。请先用 map go 到那块地所在的图。")
    planted_crop = {pt for pt in plant_tiles if tiles.get(pt, {}).get("crop")}
    # 🆕 2026-09-19（恒「**地格上有杂草**」）：跳过的不再只有"已种/被设施占"两种 —— 还要认出
    #    **杂草挡着**（→ 先 clear）、**压根没锄**（→ 先 till）。原来看见 object 才算"被占"，
    #    长草的格（没有 object，只有 terrain=Grass）就一路走到"逐格种 → 被游戏拒绝"，
    #    最后拿季节当解释；真机实测那 10 格的真因**全是没锄/有草**，跟季节无关。
    _blocked = {}
    do_tiles = []
    for pt in plant_tiles:
        if pt in planted_crop:
            continue
        info = tiles.get(pt, {})
        label, cleanable = _tile_obstacle(info)
        if label:
            _blocked.setdefault("clear" if cleanable else "facility",
                                []).append((pt[0], pt[1], label))
        elif info.get("terrain") != "HoeDirt":
            _blocked.setdefault("untilled", []).append((pt[0], pt[1], "🌱 还没锄"))
        else:
            do_tiles.append(pt)
    if not do_tiles:
        _msg = [f"{warp_log}🌱 {len(plant_tiles)} 格**没一格种得了**（已有作物 {len(planted_crop)}）"]
        _msg.extend(_obstacle_lines(_blocked, verb="种"))
        return _with_state("\n".join(_msg))

    # ── 6. 站位格（共用件；四邻全占则如实报缺失、不瞬移）──
    _walk_ok = api.walk_ok_tiles(rx1 - 1, ry1 - 1, rx2 + 1, ry2 + 1)
    if _walk_ok is None:
        return _with_state("❌ 拿不到田块的可走信息（/passable_rect 失败）——本次没播种（不盲走）")

    # ── 7. 逐格种：走位 → 重选种子 → 面向目标 → 种 ──
    #    ⚠️ **每格都要 `api.select(seed_name)`**：走位/瞬移会把选中重置掉
    #    （farm_row.py:122-124 + check_design.py 的农活原则："种到第三排才拿种子=没重选"）。
    failed, no_stand, teleported, stopped, refused = [], [], 0, "", []
    for tx, ty in _snake_tiles(do_tiles):
        try:
            cur, _mx = api.player_stamina()
            if cur is not None and cur < 20:
                stopped = f"⚠️ 体力 {cur} < 20，停在 ({tx},{ty})，剩 {len(do_tiles) - len(failed) - len(no_stand)} 格没种"
                break
        except Exception:
            pass
        stand = api.stand_tile(tx, ty, _walk_ok)
        if stand is None:
            no_stand.append((tx, ty))
            continue
        sx, sy, facing = stand
        if direct:
            api.position(sx, sy)
            time.sleep(0.1)
        else:
            try:
                if not api.walk_natural(sx, sy):
                    teleported += 1
            except Exception:
                api.position(sx, sy)
        api.select(seed_name)
        time.sleep(0.1)
        api.face(facing)
        time.sleep(0.1)
        # 🌱 游戏拒了就**把它的原话记下来**——这是唯一可靠的"为什么种不上"判据。
        #    ⚠️ 别自己算"是不是当季"：那要么维护一张种子→季节的表（本项目早拍掉"手抄表改成问游戏"），
        #    要么漏掉"温室/姜岛全年可种"这条（露天农场才有季节限制）。**游戏是按当前地点判的**，
        #    温室里种夏季种子它压根不拒 ⇒ 我们照抄它的判断，天然分地点，不用特判。
        _r = api.use_item()
        if isinstance(_r, dict) and _r.get("ok") is False:
            refused.append((tx, ty, str(_r.get("error") or "?")))
        time.sleep(0.35)

    # ── 8. 验证：**只认 `crop`**（别拿 HoeDirt 兜底——已翻的地一堆 HoeDirt，
    #    那样"一格没种上"也会报"✅ 播种完成"，是假绿；恒："别把红的记成绿的"）──
    time.sleep(0.4)
    tiles2 = _scan(max(w, h) // 2 + 8)
    real = [pt for pt in do_tiles if tiles2.get(pt, {}).get("crop")]
    lines = [head]
    if warp_log:
        lines.append(f"  {warp_log}")
    if _ig_note:
        lines.append(_ig_note)
    lines.append(f"  🚿 洒水器 {len(p['sprinklers'])} 个 | 计划 {len(plant_tiles)} 格"
                 + (f" | 🚶 留 {p['walkway_rows']} 行走道（种2留1）" if trellis and p.get("walkway_rows") else ""))
    lines.append(f"  ✅ {len(real)}/{len(do_tiles)} 确认种上（判据=这格真长出 crop）")
    if stopped:
        lines.append(f"  {stopped}")
    if refused:
        # 🌱 游戏明确拒绝了 → 把**它的原话**端上来（恒 2026-09-19：啤酒花春季种不上就是这一条）
        lines.append(f"  ⛔ {len(refused)} 格**被游戏拒绝种下**（原话「{refused[0][2]}」）"
                     + " ".join(f"({x},{y})" for x, y, _ in refused[:8]))
        lines.append("     💡 最常见是**不是当季作物**（露天农场才看季节；**温室/姜岛全年可种**，"
                     "那两个地方种得下去），也可能是那格不能种（水/未锄/被占）")
    if len(real) < len(do_tiles) - len(no_stand) - len(refused):
        lines.append(f"  ❓ {len(do_tiles) - len(no_stand) - len(refused) - len(real)} 格没验到作物"
                     f"（游戏没说拒，但也没长出 crop——用 dump_tile 看那一格）")
    if planted_crop:
        lines.append(f"  ⏭ 跳过 {len(planted_crop)} 格（已有作物）")
    lines.extend(_obstacle_lines(_blocked, verb="种"))
    if no_stand:
        lines.append(f"  ⚠️ 没种 {len(no_stand)} 格（**四邻没处站**）: "
                     + " ".join(f"({x},{y})" for x, y in no_stand[:10]))
    if teleported:
        lines.append(f"  🦶 {teleported} 格是瞬移落位（走路被挡，站位格本身可站）——不是拟人走过去的")
    return _with_state("\n".join(lines))


def _farm_clear(x: int = -1, y: int = -1, rows: int = 1, length: int = 1,
                direction: str = "horizontal", x1: int = -1, y1: int = -1,
                x2: int = -1, y2: int = -1, radius: int = 0, margin: int = 2,
                **extra) -> str:
    """🧹 清杂草/石头/树枝/树桩（`clear_area` 那个 skill 的域 op 入口）。

    坐标**和 till/plant 同一套**（三选一）：
      · `x,y,rows,length,direction` —— 起点 + 尺寸
      · `x1,y1,x2,y2`               —— 直接给矩形两角（2026-09-19 补上：docstring 早就这么写，
                                       实现里一直没有 ⇒ 传了会被当"兄弟参数"静默忽略）
      · `radius=N`（配 `x,y`）      —— **圆形**：以 (x,y) 为圆心、半径 N

    🍥 **默认自动外扩 `margin=2` 格**（恒 2026-09-19：「**你直接帮忙清理 ai 选的范围大两格半径
       就好了，成功再跟它说**」）—— 只清方正一块的话四角还是草窝，**田边的杂草很快就会长进
       田里、把作物顶掉**。所以按你给的坐标算、实际清的是**外扩 2 格那一圈**，收工时会写明
       实际范围。不想外扩就 `margin=0`。

    ⚠️ `x/y` 必填（或给 `x1..y2`）：缺坐标不再兜底成"玩家面向格"（恒 2026-09-10）；
       尺寸默认 1×1 不擅自扩。
    """
    length, err, ignored = _farm_kw_norm(x, y, rows, length, direction, extra, _FARM_SIBLING_KW)
    if err:
        return err
    _m = max(0, int(margin or 0))
    try:
        _r = int(radius or 0)
    except (TypeError, ValueError):
        return f"❌ radius 要是个数（半径几格），收到 {radius!r}"
    if _r < 0:
        return f"❌ radius 不能是负数，收到 {_r}"
    if _r > 0:
        xy, err = _farm_require_xy(x, y)
        if err:
            return err
        x, y = xy
        _rr = _r + _m
        desc = (f"圆形 圆心({x},{y}) 半径{_r} → **实际半径 {_rr}**（自动外扩 {_m} 格）"
                if _m else f"圆形 圆心({x},{y}) 半径{_r}")
        out = _clear_area_run([str(x), str(y), str(_rr)], desc)
    else:
        if min(x1, y1, x2, y2) >= 0:
            rx1, ry1 = min(x1, x2), min(y1, y2)
            rx2, ry2 = max(x1, x2), max(y1, y2)
        else:
            xy, err = _farm_require_xy(x, y)
            if err:
                return err
            x, y = xy
            rx1, ry1, rx2, ry2 = _farm_rect(x, y, rows, length, direction)
        ax1, ay1, ax2, ay2 = rx1 - _m, ry1 - _m, rx2 + _m, ry2 + _m
        desc = (f"矩形 ({rx1},{ry1})-({rx2},{ry2}) → **实际 ({ax1},{ay1})-({ax2},{ay2})**"
                f"（自动外扩 {_m} 格）" if _m else f"矩形 ({rx1},{ry1})-({rx2},{ry2})")
        out = _clear_area_run([str(ax1), str(ay1), str(ax2), str(ay2)], desc)
    _ig = _ignored_note(ignored)
    return f"{out}\n{_ig}" if _ig else out


@mcp.tool()
def bundle_status(area: str = "") -> str:
    """🎁 社区中心献祭**存档状态**（只读——不走路、不开菜单、不看板）。
    逐间报「做完没」+ 未完成收集包还缺哪些材料（⭕=已捐，缺的标 🎒你有）。
    参数 area: 只看某间（茶水间/工艺室/鱼缸/锅炉房/金库/布告栏），空=全部。
    ⚠️ 查「原本要什么 / 去哪弄」用 bundle_kb（wiki 静态表）；本工具只报**这个存档的现状**。
    """
    # 🕰️ 2026-09-11 大改（恒拍板重编 DLL）：**原来它要走到社区中心去开菜单读板**。
    #    恒真机踩到：本存档献祭早已全做完，函数照样把 AI 从 Farm 跨 3 张图走到 CommunityCenter
    #    （55 秒），对 4 块板挨个 interact 全部打不开菜单，最后**把 AI 撂在锅炉房板前**就返回了。
    #    根因=`locations.COMMUNITY_CENTER_BOARDS` 的 `open` 只是 2026-08-16 的一次性实测快照，
    #    存档一变就烂，代码却一直信它（恒："这个存档献祭我们已经做完了"）。
    #    ⇒ 改读 C# 新端点 `/bundles`：netWorldState 的 Bundles **是共享世界状态**，站着不动就能读全。
    #
    # ⚠️⚠️ **同一天真机又打脸一次：别信 `/bundles` 的 `area_complete_flag`（= `areasComplete`）**。
    #    同一进程里房主端口读它 6/6、AI 端口读 0/6 —— 它是**地图上的 net 字段**，而
    #    `markAreaAsComplete` 写着 `if (Game1.currentLocation == this)`（CommunityCenter.cs:846）
    #    ⇒ farmhand **没进过这张图就同步不到**，拿到的是构造默认值全 false。
    #    C# 侧已改成**由收集包反推本间做完没**（游戏自己的定义，JunimoNoteMenu.cs:386-395），
    #    并把原值降级成 `area_complete_flag` 只作诊断 ⇒ **本函数只认 `complete`，别回头去读那个 flag**。
    #    同理别用 `stars`（`numberOfStarsOnPlaque` 是进图时本地重算的，站着读恒 0）——C# 已自己数。
    #    ⛔ 别想"先探一下做完了没"的兜底：`ccMovieTheater` 那个判据**是错的**（影院=废弃 Joja 超市里
    #    的"遗失的收集包"，是**第 7 间**、不属于社区中心；走 Joja 路线不做献祭照样有影院
    #    ⇒ "有影院"推不出"献祭做完了"）。恒 2026-09-11 当面纠正过 —— **没有可靠判据就别装兜底。**
    try:
        d = api._get("/bundles")
    except Exception:
        # 两种情况：① 游戏没开（连接被拒）② 游戏在跑但 Mod DLL 是旧的、压根没这个端点（404 解不出 JSON）。
        # 都只给一句话 —— requests 的整段异常甩给 AI 它既读不懂也没法处理（恒：文案要让人看得懂）。
        return _with_state("❌ 读不到献祭状态：游戏进程没连上（没开？），"
                           "或 Mod DLL 太旧没有 `/bundles` 端点（要更新 Mod）。")
    if not isinstance(d, dict) or not d.get("ok"):
        return _with_state(f"❌ 读不到献祭状态: {(d or {}).get('error', '端点无回应')}"
                           "（⚠️ 旧 DLL 没这个端点，要更新 Mod）")
    areas = d.get("areas") or []
    if area:
        areas = [a for a in areas
                 if area in str(a.get("name") or "") or area in str(a.get("name_en") or "")]
        if not areas:
            return _with_state("🎁 没有匹配的房间（茶水间/工艺室/鱼缸/锅炉房/金库/布告栏）")
    lines = [f"🎁 社区中心献祭（⭐ {d.get('stars', 0)} 星 · "
             f"{d.get('areas_complete', 0)}/{d.get('areas_total', 0)} 间已完成）"]
    # 🎒 背包标记：按材料**名**匹配（材料名走 ItemRegistry.DisplayName，与 /state 的 displayName 同一套）
    _have = {}
    try:
        for _it in (api.state().get("inventory") or []):
            _nm = (_it.get("displayName") or _it.get("name") or "").strip().lower()
            if _nm:
                _have.setdefault(_nm, {"name": _it.get("displayName") or _it.get("name"), "count": 0})
                _have[_nm]["count"] += int(_it.get("stack", 1) or 1)
    except Exception:
        pass
    for a in areas:
        # 🏆 "完成后解锁什么" —— 按**游戏 area 号**去 bundles.py 反查（唯一来源，见 bundles.py 头注释）。
        #    ⚠️ 别按房名查：bundles.py 把 area 4 叫"地下室"，游戏本地化叫"金库"，按名查会撞空。
        _room = bundles.room_by_area(a.get("area"))
        _tag = f"  🏆 {_room['reward']}" if _room else ""
        if a.get("complete"):
            lines.append(f"  ✅ {a.get('name')}{_tag}")
            continue
        bl = a.get("bundles") or []
        done_n = sum(1 for b in bl if b.get("complete"))
        lines.append(f"  ⬜ {a.get('name')}（{done_n}/{len(bl)} 包完成）{_tag}")
        for b in bl:
            if b.get("complete"):
                lines.append(f"    ✅ {b.get('name')}")
                continue
            parts, don = [], []
            for i in (b.get("ingredients") or []):
                nm = i.get("name") or i.get("id") or "?"
                if i.get("completed"):
                    parts.append(f"⭕{nm}")          # 已捐
                    continue
                parts.append(f"{nm}×{i.get('count')}")
                k = str(nm).strip().lower()
                if k in _have and _have[k]["count"] > 0:
                    don.append(f"{_have[k]['name']}×{_have[k]['count']}")
            mark = f"  🎒你有: {', '.join(don)}" if don else ""
            lines.append(f"    ⬜ {b.get('name')}: {', '.join(parts)}{mark}")
    if areas and all(a.get("complete") for a in areas):
        lines.append("🎉 都做完了，没有缺口。")
    return _with_state("\n".join(lines))


@mcp.tool()
def bundle_kb(query: str = "") -> str:
    """📖 献祭(社区中心收集包)知识库——**不用跑到社区中心**，从 wiki 静态表查
    某收集包要什么/去哪弄/奖励是啥，方便规划去皮埃尔买献祭相关作物、提前备货。
    与 bundle_status(读存档看缺口,不用走路)互补：bundle_kb 查「原来要这些」，bundle_status 查「我现在缺哪些」。
    query:
      - 空 → 全房间概览
      - 房间名: 工艺室/茶水间/鱼缸/锅炉房/布告栏/地下室/遗失 → 那间所有收集包
      - 收集包名 or 物品名: 春季作物/防风草/蟹笼/土豆... → 匹配到的收集包
    ⚠️ 名称以 wiki/官方中文为准(绿豆/甜瓜/西红柿)；`/surroundings` 的 cropName 也是**官方中文**
    （ItemRegistry DisplayName 本地化，真机 ID 454 报「上古水果」）⇒ 两边通常对得上。
    但 `/select` 认 **英文内部名**（Starfruit/Ancient Fruit），要操作物品时用英文。
    """
    return _with_state(bundles.search_bundles(query))


@mcp.tool()
def farm(ops: str = "", kw: dict | None = None) -> str:
    """🌾 农活域（农场/温室/姜岛）。till 锄地 / plant 种(可带 layout 按洒水器布局) / water 浇地 / harvest 收 / fertilize 施化肥 / clear 清杂草石头树桩 / collect 收机器(一键只收,不走路) / building 一屋收放(拟人走) / chop 砍树。动物：animals 摸+收 / 喂水 宠物碗 / milk 挤奶剪毛。全 ops → help(farm)。⚠️带尺寸 op(till/plant/clear/fertilize)：**x/y 必填**（不传直接报错，不再兜底成"玩家面向格"）；rows×length 缺省只做 1 格，要多大自己传。animal water 用 喂水，water=浇地。💡**多 op 一次调用共用一份 kw**（如 ops="till plant"），各自只吃自己认识的参数、属于别人的会**点名忽略**。"""
    op_list = [o for o in re.split(r"[\s,，]+", (ops or "").strip()) if o]
    if not op_list:
        return _with_state("❌ ops 为空（如 farm(ops=\"till plant water\")）")
    # 🗺️ 动态工具检测：不在可种植区 → 建议行（不拦，照跑）
    _adv = _domain_advice("farm", ops)
    # 🌾 2026-09-19：一条龙那个 op 退役了，**但"一次调用、一份 kw 共用"的组合要保住** ⇒
    #    这里只做**稳定重排**：锄地类 op 一律排在播种类之前（老组合器是"摘掉再合并"，效果等价：
    #    `plant till` 也跑成先锄后种）。其余 op 保持书写顺序不动。
    #    ⚠️ 别改成"合并成一次调用"——那样又会长出一条龙的替身（`_farm_till` 与 `_farm_plant` 共用
    #    坐标参数，但 `layout` 语义不同：锄地按等级/地块大小路由，播种只按洒布格）。
    _TILL_OPS = ("till", "hoe", "布局锄", "tillfield", "蓄力锄")
    _PLANT_OPS = ("plant", "sow", "plantlayout", "播种规划")
    if any(o in _TILL_OPS for o in op_list) and any(o in _PLANT_OPS for o in op_list):
        _fp = next(i for i, o in enumerate(op_list) if o in _PLANT_OPS)
        _moved = [o for o in op_list[_fp:] if o in _TILL_OPS]
        if _moved:    # 只把"写在播种后面的锄地"挪到播种前，**其余 op 原位不动**
            op_list = op_list[:_fp] + _moved + [o for o in op_list[_fp:] if o not in _TILL_OPS]

    dispatch = {
        # 🌾 2026-09-17 恒拍板「收敛成一个」：锄地只有 `_farm_till` 一个实现 ——
        #    `hoe`/`布局锄`（旧的、唯一带 layout 的）与 `tillfield`/`蓄力锄`（旧的一键）**都并进来了**。
        #    旧名**全部保留为别名**（调用方/AI 老文案照用不误），只是不再各走各的实现。
        "till": _farm_till,
        "hoe": _farm_till, "布局锄": _farm_till,
        "tillfield": _farm_till, "蓄力锄": _farm_till,
        # 🌱 2026-09-19 恒拍板「播种也收敛成一个」：`plant`/`sow`/`plantlayout`/`播种规划`
        #    **四个名字都指 `_farm_plant`**（`layout` 变成传参：0 整块 / 1 初级 / 2 高级 / 3 铱）。
        #    ⛔ `till_plant`（一条龙）**已退役** —— 要锄+种写 `farm ops="till plant"`（一次调用、一份 kw 共用）。
        "plant": _farm_plant, "sow": _farm_plant,
        "plantlayout": _farm_plant, "播种规划": _farm_plant,
        "water": water_crops, "浇": water_crops,
        "harvest": harvest_crops, "收": harvest_crops,
        "scythe": scythe_crops,
        "fertilize": apply_fertilizer, "化肥": apply_fertilizer,
        "clear": _farm_clear, "清": _farm_clear,
        "plot": plot_plan, "规划": plot_plan,
        # （tillfield/hoe 的映射已并到上面 `_farm_till` 那一组 —— 别在这再加回来：
        #   同一个 dict 里**重复键是后者赢**，加回来 = 悄悄退回旧实现。）
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
    # 🌾 2026-09-19：原来这里有条"till+plant 先合并成一条龙再跑"的特殊分支，**已随该 op 退役删掉**：
    #    现在逐个 op 各跑一次（顺序由上面的"稳定重排"保证锄在种前），**一份 kw 共用**靠
    #    `_FARM_SIBLING_KW` 点名忽略对方参数来兜住。
    return _with_state((_adv + "\n\n" if _adv else "") + _ops_run(" ".join(op_list), dispatch, kw))


@mcp.tool()
def mine(ops: str = "", kw: dict | None = None) -> str:
    """⛏️ 下矿域。go 自动下楼挖矿(mode: rush冲层/farm刷矿；farm 定点刷 ore=Copper铜21/Iron铁41/Gold金71，煤靠铁层41清怪掉) / progress 进度 / bomb_mine 普通炸矿(自动) / bomb_volcano 火山专用炸矿。全 ops+单步炸/协同坑(无镐血低硬拦、火山需 host 同行) → help(mine)。"""
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


def _cur_loc_unique() -> str:
    """当前地点的**唯一名**（`/state.location.uniqueName`，如小屋的 `FarmHouse<guid>`）。
    ⚠️ **别再退回 `.name`（显示名）**：农场上多间小屋的 `.Name` 全是 "Cabin"，按名过滤会指错
    ——2026-09-11 真机踩到，`cabin collect` 收到别间小屋去了。读不到就返回 ""（调用方明说读不到，不猜）。"""
    try:
        return (api.state().get("location") or {}).get("uniqueName") or ""
    except Exception:
        return ""


def _cabin_collect() -> str:
    """收**当前屋**的待收机器（限定当前地点，不全农场乱跑）。
    ⚠️ 传**唯一名**而不是显示名——理由见 `_cur_loc_unique`。C# `FindLocationByName` 也相应改成
    「先认玩家当前所在 → 再认唯一名 → 最后才退回按名字扫」（对齐游戏自己的 getLocationFromName）。"""
    cur = _cur_loc_unique()
    if not cur:
        return "❌ 读不到当前屋的唯一名（Mod DLL 太旧，要更新 Mod）——按名字过滤会收到别间小屋去，所以不猜。"
    return collect_machines(location=cur)


def _cabin_enum() -> str:
    """扫当前屋：待收机器/雕像/家具清单 + 引导。只在屋内（FarmHouse/Cabin/岛屋）有意义。"""
    try:
        _loc = api.state().get("location") or {}
        cur, cur_u = _loc.get("name", ""), _loc.get("uniqueName") or ""
        lines = [f"🏠 当前: {cur}"]
        if cur not in HOME_MAPS:
            lines.append(f"⚠️ 小屋域只在屋内用（FarmHouse/Cabin/岛屋）——现在在{cur}，可先 map go 回家")
            return "\n".join(lines)
        if not cur_u:
            # 旧 DLL 没有 `/state.location.uniqueName`。**别退回按显示名过滤**——同名小屋会把几间
            # 的机器加一起报个假数（2026-09-11 的「396 台」= 377+18+1 就是这么来的）。宁可明说。
            lines.append("❌ 读不到当前屋的唯一名（Mod DLL 太旧，要更新 Mod）——同名小屋没法区分，不猜。")
            return "\n".join(lines)
        # 1) 待收机器（farm_report 按当前屋过滤）
        try:
            fr = _fetch_farm_report()
            ml = (fr.get("machines") or {}).get("machines") or []
            # ⚠️ 按 **location_unique** 过滤，不是 `location`（显示名）：农场上多间小屋 `.Name`
            #    都是 "Cabin"，按名过滤会把四间的机器**加在一起**（2026-09-11 真机 396 台）。
            ready = [m for m in ml
                     if m.get("status") == "ready" and m.get("location_unique") == cur_u]
            if ready:
                names = ", ".join(dict.fromkeys(MACHINE_CN.get(m.get("type", "?"), m.get("type", "?")) for m in ready))
                lines.append(f"⚙️ 待收机器 {len(ready)} 台：{names}（cabin collect 收）")
            else:
                lines.append("⚙️ 屋里没有待收机器")
        except Exception:
            pass
        # 2) 雕像 —— ⚠️ **必须扫 object 层**（和 blessing_statue.py 同源；那只认能给增益的那种）。
        #    原来扫 /furniture，会把**装饰雕像**（家具层 (F)xxxx，如「莉亚做的雕像」）也算进来，
        #    然后引导 AI 去 `cabin statue` —— 那边扫的是 object 层，必然报"没找到"。
        #    2026-09-11 真机：enum 报「雕像 1 座」/ statue 报「没找到雕像」，两边打架。
        try:
            su = api._get("/surroundings", {"radius": 30})   # 端点 radius 上限就是 30，够盖住小屋
            stats = [t for t in (su.get("tiles") or []) if "Statue" in (t.get("object") or "")]
            if stats:
                names = ", ".join(dict.fromkeys(t.get("object") for t in stats))
                lines.append(f"🗿 雕像 {len(stats)} 座：{names}（cabin statue 摸）")
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
    """🏠 小屋/家域（屋内）。sleep 睡觉 / cook 做饭 / 布置家居 / 收放设备 / 摸雕像 等 → help(cabin)。"""
    # 🗺️ 动态工具检测：小屋域建议在屋里做（sleep 豁免，自己会回家）
    _adv = _domain_advice("cabin", ops)
    dispatch = {
        "enum": _cabin_enum, "看": _cabin_enum, "引导": _cabin_enum,
        "collect": _cabin_collect, "收": _cabin_collect, "机器": _cabin_collect,
        "statue": blessing_statue, "雕像": blessing_statue, "祈福": blessing_statue,
        "furniture": scan_furniture, "家具": scan_furniture,
        "interact": interact_at, "点": interact_at,
        "place": place_item, "放": place_item, "放置": place_item,
        # 🪵 装修真值表：**装修是在屋里做的**，所以这个域才是 AI 真正会找它的地方
        #    （`scene` 域也有一份；两处指同一个 `decor_report`）
        "decor": decor_report, "装修": decor_report, "可铺": decor_report,
        "break": break_tile, "拆": break_tile, "敲": break_tile,
        "pickup": furniture_pickup, "拿": furniture_pickup, "摆": furniture_pickup,
        "sleep": go_sleep, "睡": go_sleep, "睡觉": go_sleep,
        "cook": cook, "做饭": cook,
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


def _crab_water_at(cx: int, cy: int, radius: int = 15):
    """以指定瓦片为中心找水（/water 端点；淘金/蟹笼共用）。

    ⚠️ 2026-09-12 恒："**别甩锅**" —— 现在**端点挂了返回 None、这儿真没水返回 []**。
    原来两种都吞成 `[]`，于是 `_crab_water_report` 把"图里没水"报成"**需新 DLL**"，
    害人去追一个根本不存在的 DLL 问题（真机：AI 在鱼店**屋里**调 crab_water，端点好得很，
    只是屋里没水 —— 直打 `/water` 明明 200 返回 `{"count":0}`）。
    """
    try:
        r = api._get("/water", {"x": cx, "y": cy, "radius": radius})
    except Exception:
        return None
    if not r.get("ok"):
        return None
    return r.get("water") or []


def _crab_water(radius: int = 15):
    """找当前地点半径内的水瓦片（= _crab_water_at 以玩家为中心，2026-08-29 泛化）。
    **None=端点拿不到数据；[]=本图真没水**（两者含义不同，别混，见 _crab_water_at）。"""
    try:
        st = api.state()
        px = (st.get("player") or {}).get("x") or 0
        py = (st.get("player") or {}).get("y") or 0
    except Exception:
        return None
    return _crab_water_at(px, py, radius)


def _crab_find_edges(radius: int = 15) -> list:
    """水边可站边：返回 [(站x, 站y, 朝水face, 水x, 水y), ...]。
    ⚠️ 2026-08-30 恒铁律(同淘金)：站格=**纯陆地岸上格**(不 allowWater，排掉水格)，先走到岸上。
    只在 **canCrabPot=true** 的水格旁放(/water 已预筛)——放进宽水域一次成功零试错；
    /use 传 x,y 精准远程放到水格(AI 站岸上即可)。放完就在这岸格，不去爬水。"""
    water = _crab_water(radius)
    if water is None:
        return None      # ⚠️ 端点拿不到数据 ≠ 没有水边（2026-09-12：别把两者混成一个空列表）
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
    if water is None:
        return ("❌ 拿不到水格数据（/water 没响应）—— 先确认游戏进程在跑；"
                "**这不是「这张图没水」**，别去怀疑 DLL。")
    if not water:
        return (f"❌ 当前图（{_crab_cur_loc()}）半径 {radius} 内没有水格。"
                f"室内/城镇这类图本来就没水（鱼塘也不算蟹笼水）——**先走到有水的图**"
                f"（海/河/湖/矿洞水池）再调本工具。")
    lines = [f"🌊 附近水瓦片 {len(water)} 个（半径 {radius}）:"]
    for w in water[:12]:
        lines.append(f"  ({w['x']},{w['y']})")
    if len(water) > 12:
        lines.append(f"  … 共 {len(water)} 个")
    lines.append("放蟹笼→fish ops=crab_place")
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
    if edges is None:
        return ("❌ 拿不到水格数据（/water 没响应）—— 先确认游戏进程在跑；"
                "**这不是「这图没水」**，别去怀疑 DLL。")
    if not edges:
        return (f"❌ 当前图（{loc}）半径 {radius} 内没有**可放蟹笼**的水边："
                f"要宽水域（左右都是水或上下都是水）才放得下，零星水格/鱼塘都不算。"
                f"**先走到海/河/湖再试**。")
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


# ⏱ 蟹笼批量 op 的**单次总耗时预算**（秒）——2026-09-12 真机踩到的：
#   `crab_bait` 一次要处理**扫描到的每一个笼**（那天扫出 **22 个**），而每个笼的
#   `_wait_arrival(timeout=15)` 最多啃 15s ⇒ **22 × 15s ≈ 5 分钟**，这 5 分钟里
#   **整个 :8000 服务是不响应的**（恒当时看到的就是"又卡死了"；实测 CPU 不涨=阻塞、
#   游戏端口 7842/7843 却 0.01s 正常 ⇒ 僵的是 MCP 侧）。⚠️ 这**不是死锁**（跟同一天
#   `script.stop` 的 `_bg_lock` 自锁死是两回事）——服务在循环跑完后**自己会恢复**。
#   ⇒ 现在给个预算：够了就停、并**如实报告还剩几个没轮到**（可再调一次接着做，
#     已处理的不会白费）。**别做成静默截断**——那会让人以为"22 个都挂上了"。
#   45s 是**刻意压在 MCP 客户端 60s 超时之下**的：到点要能**把话说完再返回**，
#   否则客户端先超时，AI 收到的是"卡住"而不是"还剩 N 个没轮到"，等于白做这个预算。
_CRAB_OP_BUDGET = 45


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
    # ⚠️ 2026-09-12 真机踩到：**没有饵时本 op 会逐个空跑** —— `api.select(bait)` 静默失败，
    #    后面 `/interact` 自然什么都不发生，于是 22 个笼全报 ✗、**连原因都是空的**，
    #    还白占掉一次总预算（那天 47s 全在服务里堵着）。`_crab_place` 早有同类前置检查
    #    （`has_item("Crab Pot")`），这里**漏了** ⇒ 照它的样子补上（宁报错别兜底）。
    if not api.has_item(bait):
        return (f"❌ 背包里没有「{bait}」—— 挂饵前先得有饵（威利鱼店买 **鱼饵/Bait**；"
                f"也可用野钓饵/豪华鱼饵）。⚠️ 没饵时本 op 会逐个空跑 22 遍白占时间，所以这里直接拦。"
                f"（免饵职业 Luremaster 名不虚传不用挂饵；先 check(what=\"profile\") 看职业分支）")
    baited = 0
    done = 0
    deadline = time.time() + _CRAB_OP_BUDGET
    log = [f"🦀 给 {len(pots)} 个蟹笼放饵（单次上限 {_CRAB_OP_BUDGET}s，到点会停并告诉你剩几个）:"]
    for wx, wy in pots:
        if time.time() > deadline:
            log.append(f"⏱ 到 {_CRAB_OP_BUDGET}s 上限，还有 {len(pots) - done} 个没轮到 —— "
                       f"**再调一次本 op 接着挂**（已挂上的不会白费）")
            break
        done += 1
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
                # ⚠️ 2026-09-12：15s → 5s。这一段本来就是"尽力而为"（interact 是坐标定位、
                #   距离无关），等满 15s 只是白占耗尽预算 —— 22 个笼时那 15s 就是压死服务的元凶。
                _wait_arrival(loc, sx, sy, timeout=5)
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
    log.append(f"🎣 本轮处理 {done}/{len(pots)} 个，成功挂饵 {baited}；"
               f"过夜就出货(蟹/贝壳/垃圾)，早上 crab_collect 收")
    return "\n".join(log)


def _crab_collect() -> str:
    """收蟹笼产出（空手逐个交互；收了会出空笼，需要再放饵）。
    ⚠️ 2026-08-30 修：不再写死 (wx,wy+1)+朝北，用 _crab_stand_for 找真实岸格。"""
    loc = _crab_cur_loc()
    pots = _crab_scan_placed()
    if not pots:
        return "❌ 附近没找到已放的蟹笼"
    got = 0
    done = 0
    deadline = time.time() + _CRAB_OP_BUDGET
    log = [f"🦀 收 {len(pots)} 个蟹笼（单次上限 {_CRAB_OP_BUDGET}s，到点会停并告诉你剩几个）:"]
    for wx, wy in pots:
        if time.time() > deadline:
            log.append(f"⏱ 到 {_CRAB_OP_BUDGET}s 上限，还有 {len(pots) - done} 个没轮到 —— "
                       f"**再调一次接着收**（已收进背包的不会白费）")
            break
        done += 1
        st = _crab_stand_for(wx, wy)
        if not st:
            log.append(f"  ✗ ({wx},{wy}) 找不到旁侧可站岸格")
            continue
        sx, sy, face = st
        try:
            # 走位尽力而为；interact 坐标定位，不依赖完美站格
            try:
                api._post("/walk_to", {"location": loc, "x": sx, "y": sy})
                _wait_arrival(loc, sx, sy, timeout=5)   # ⚠️ 15→5，理由见 _CRAB_OP_BUDGET
                api._post("/face", {"direction": face})
                time.sleep(0.3)
            except Exception:
                pass
            api._post("/interact", {"x": wx, "y": wy})
            got += 1
        except Exception as e:
            log.append(f"  ✗ ({wx},{wy}) {e}")
        time.sleep(0.4)
    log.append(f"📦 本轮交互 {got}/{len(pots)} 个（产出进背包；空笼要重新放饵）")
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


def _crab_pots_scan(location: str = None):
    """🦀 拉当前/指定图所有蟹笼的真实状态（/crab_pots 端点）。
    **None=端点拿不到数据；[]=这张图真没笼**（2026-09-12 恒"别甩锅"：原来两种都是 `[]`，
    于是"这图没笼"被报成"需新 DLL /crab_pots"，害人去追不存在的 DLL —— 端点明明 200 好着）。"""
    try:
        params = {"location": location} if location else {}
        r = api._get("/crab_pots", params)
    except Exception:
        return None
    if not r.get("ok"):
        return None
    return r.get("pots") or []


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
    """💬 社交域。chat 搭话 / gift 送礼 / give 送玩家 / hand 递玩家 / friendship 查好感 / send 发消息 / emote 表情。全 ops+参数 → help(social)。"""
    dispatch = {
        "chat": chat_npc, "搭话": chat_npc,
        "gift": gift_npc, "送礼": gift_npc,
        "give": give_item, "给": give_item,
        "hand": hand_item, "递给": hand_item,
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
    """🖱️ 场景交互域：at(点格) / interact(点面前) / sit(坐椅子,可选face=朝向) / stand(起身) / seats(扫可坐物) / use(挥工具) / pickup_scene(捡采集物) / berry(摇浆果) / spot(挖蚯蚓) / moss(苔藓) / place(放置播种) / break(拆敲) / maze。全 ops+坑 → help(scene)。

    """
    dispatch = {
        "at": interact_at, "点": interact_at,
        "front": interact, "面前": interact, "interact": interact,
        "sit": sit, "坐": sit, "坐下": sit, "seats": seats, "座位": seats, "可坐": seats,
        "stand": stand, "起身": stand, "站起": stand, "站起来": stand,
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
        "decor": decor_report, "装修": decor_report, "可铺": decor_report,   # 🪵 地板/墙纸真值表
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
    """📋 界面/菜单域（菜单开着时用）：read 看菜单 / advance **推进剧情·对话**(卡剧情就调这个) / skip **整段跳过剧情** / click 点选项 / key 按键 / cancel 关弹窗 / shop 逛店。全 ops + 关键坑 → help(menu)。

    """
    dispatch = {
        "read": read_menu, "看": read_menu, "journal": open_questlog, "日志": open_questlog, "开日志": open_questlog,
        "number": number_select, "数量": number_select, "数量框": number_select,
        "display_fill": _menu_display_fill, "放满": _menu_display_fill, "填槽": _menu_display_fill,
        "display_takeback": _menu_display_takeback, "收好": _menu_display_takeback, "收": _menu_display_takeback,
        "advance": advance_story, "推进": advance_story, "剧情": advance_story,
        "skip": skip_event, "跳过": skip_event, "跳剧情": skip_event, "跳": skip_event,
        "click": menu_click, "点": menu_click,
        "key": press_key, "按键": press_key,
        "cancel": cancel, "取消": cancel, "关": cancel,
        "shop": shop_visit, "逛店": shop_visit,
        "sell": sell_to_shop, "卖": sell_to_shop,
        "bin": sell_to_bin, "出货": sell_to_bin,
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
    """🎒 箱子域。view 看箱 / store 存进去 / take 拿出来 / find 找东西在哪箱 / default 归位存进默认箱 / tag 标记+改色。全 ops → help(storage)。"""
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
    """🗿 日常域。sleep 睡觉 / eat 吃 / wear 穿脱衣物 / lie_bed 躺床不过夜 / heartbeat 心跳间隔 / peek 看 host 在干嘛。全 ops → help(daily)。"""
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


def map_unlocks() -> str:
    """🔓 查存档解锁状态（矿洞/巴士/下水道/姜岛/精通/火山近路…）。

    2026-09-12 恒问「**这里的门禁可以看到吗**」—— 火山那道路径的**闸门/机关本身看不到**
    （是运行时 `DwarfGate` 对象改的**动态地图层**，`/dump_tile` 读那几格全是空），
    但它的**结果标志**能看：mail flag `volcanoShortcutUnlocked`。这个 op 就是把 `/unlocks` 摊开。

    ⚠️ 用途：**用一条捷径之前先查它开没开**。目前唯一会挡路的就是火山近路
    （没解锁时 入口层 VolcanoDungeon0 ⇄ 山顶 Caldera 那条捷径是关的）。
    """
    try:
        d = api._get("/unlocks")
        if not d.get("ok"):
            return _with_state(f"❌ 读解锁状态失败: {d.get('error')}")
        u = d.get("unlocks") or {}
        keep = ("mine", "bus", "sewer", "secretWoods", "skullCavern", "island",
                "railroad", "casino", "summit", "mastery", "greenhouse",
                "communityCenter", "cinema", "witchSwamp", "townKey", "forestMagic",
                "parrotExpress", "volcanoShortcut", "reachedCaldera", "volcanoShortcutOut")
        lines = []
        for k in keep:
            v = u.get(k)
            if not isinstance(v, dict):
                continue
            mark = "✅" if v.get("unlocked") else "🔒"
            lines.append(f"  {mark} {k}: {v.get('how', '')}")
        tail = ""
        if isinstance(u.get("volcanoShortcut"), dict) and not u["volcanoShortcut"].get("unlocked"):
            tail = "\n  ⚠️ 火山近路**没解锁** ⇒ 入口层⇄山顶那条捷径不通，只能一层层爬。"
        return _with_state("🔓 存档解锁状态：\n" + "\n".join(lines) + tail)
    except Exception as e:
        return _with_state(f"❌ 查解锁失败: {e}")


@mcp.tool()
def map(ops: str = "", kw: dict | None = None) -> str:
    """🗺️ 导航域（跨图唯一入口）。go 走到目标 / walk 走到地点(POI)**或坐标(x,y)** / lookup 查地点功能 / query 功能反查("哪能买X") / npc 找NPC / warp_safe 紧急逃脱 / unlocks 查存档解锁(**走捷径前先查**)。⚠️参数放 kw 对象。全 ops → help(map)。"""
    dispatch = {
        "lookup": map_lookup, "查": map_lookup,
        "query": map_query, "反查": map_query,
        "go": map_go, "走": map_go,
        # ⚠️ 2026-09-11 恒拍板：`movetile` **退役** —— 坐标走位并入 `walk`（walk=poi_name 或 x/y）。
        #    理由：老 movetile 走 `/move`+BFS，而 CLAUDE.md 关键坑#1 就是「别用 /move+BFS」，
        #    它是仅存的一个绕过口子；且实测两个缺陷（C# 回 No path 也印 ✅；踩到门格静默 warp 换图）。
        #    地面走位现在**只剩一条路**（/walk_to），与 CLAUDE.md「地面/walk_to」一致。
        #    ⚠️ `move_to_tile` 函数本体**留着**：navigation 内部与老脚本(clear_area/harvest)还在调，
        #       只是不再给 AI 直调（同 advance_story/profile 的收编先例）。
        "walk": walk_to, "走到": walk_to,
        # 🚶 2026-09-12 恒拍板：`_maze_walk` 原本只在 scene/festival 两面门牌下（各带"走迷宫"），
        #    但它干的是**通用多段走位**（无节庆门禁），名字把用途埋了 —— 想"走这几个点"的 AI
        #    不会去"走迷宫"底下找。⇒ **主门牌挪进 map**（走位是导航本职），起中性名 `walk_multi`/`闲逛`。
        #    ⚠️ **一份实现，不做副本**；scene/festival 的 `maze_walk`/`走迷宫`/`迷宫走` **全部保留**
        #       （万灵节引导与老习惯都还能用，不断档）。万灵节引导已改指新名。
        "walk_multi": _maze_walk, "闲逛": _maze_walk, "多段走": _maze_walk,
        "npc": find_npc, "找人": find_npc,
        "warp_safe": warp_safe, "逃脱": warp_safe,
        # 2026-09-12（恒问「这里的门禁可以看到吗」）：闸门本身读不到，但它的 mail flag 能读。
        "unlocks": map_unlocks, "解锁": map_unlocks,
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


def _festival_event_running() -> bool:
    """🎪 现在是否**真的在播**节日事件（`/state` 的 `activeEvent.id` 以 `festival_` 开头）。

    用途：`_festival_go` 判"人到了场地，节日事件到底起没起来" —— 起不来就是㊴/㊷ 那个
    "空场 + 时间冻结"的二手现场。读不到就返回 False（当"没起来"处理，触发恢复动作）。
    """
    try:
        ev = api.state().get("activeEvent") or {}
        return str(ev.get("id") or "").startswith("festival_")
    except Exception:
        return False


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

    # 🎪 2026-09-13 恒拍板：**把"已知有效的恢复动作"自动化**（⚠️ 治标，不是治本 —— 恒要的就是这句明说）。
    #
    #   第二层病（㊴/㊷ 真机抓到）：客户端**只在进图时**跑 `checkForEvents()`，而"首次进节日场地"那条路由
    #   没把 `festival_spring13` 带起来 ⇒ 人被丢进**空荡荡、时间完全冻结的普通 Town**
    #   （`activeEvent=null`：人能动，但节日不在）。
    #   ✅ **已验证有效的恢复动作**（恒的经验，09-13 真机原样复现）：**走出地图边界再走回来一次** ——
    #      回来那一脚走的是游戏自己的进图流程，`checkForEvents()` 就把它拉起来了。
    #
    #   ⚠️ 三条边界（恒：「给我说清楚这是治标」）：
    #     · **有界**：只做**一次**。失败就如实报，**绝不循环重试** —— 循环会把"没治本"藏起来。
    #     · **不静默**：输出里明写"用了恢复动作"，让 AI / 恒都看得见这不是正常路径。
    #     · **未坐实的根因**：怀疑 `Game1.whereIsTodaysFest` **只在时钟跳动的 tick 里赋值**，
    #       而节日期间时钟恰好是冻的 —— 两件事撞一起。坐实它要给 `/event_state` 加字段再重编重启
    #       （本轮已加 `whereIsTodaysFest` 探针）。
    if (st_loc == f.get("map") and st_loc not in _FESTIVAL_TEMP_MAPS
            and not _festival_event_running()):
        out = ""
        for lk in (locations.MAP_LINKS.get(f["map"]) or []):
            tgt = lk.get("target")
            if lk.get("kind") == "warp" and tgt and tgt != f["map"]:
                out = tgt
                break
        if not out:
            return (r + f"\n⚠️ 人在 {st_loc}，但**节日事件没起来**（空场、时间冻），"
                        f"而 {f['map']} 没有可用的邻图出口 ⇒ 恢复动作做不了，请把现场交给 user。")
        try:
            map_go(out)
        except Exception:
            pass
        time.sleep(0.6)
        try:
            r2 = map_go(f["map"])
        except Exception as e:
            r2 = f"❌ 回 {f['map']} 时出错: {e}"
        try:
            st2 = api.state().get("location", {}).get("name", "")
        except Exception:
            st2 = ""
        if st2 in _FESTIVAL_TEMP_MAPS or _festival_event_running():
            return (f"🎪 首次进场地时**节日没被加载**（空场 + 时间冻结）—— 已按"
                    f"「{f['map']} → {out} → {f['map']}」出图再进图**一次**把它拉起来了。\n"
                    f"（⚠️ 这是**恢复动作、不是正常路径**；根因未坐实）\n{r2}")
        return (r + f"\n⚠️ 进场地后节日事件**没起来**（空场、时间冻结），出图再进图一次"
                    f"**也没救回来** —— 如实报，不重试。请把现场交给 user。")
    return r


def _festival_info() -> str:
    try:
        live = api.festival_status()
    except Exception as e:
        return f"⚠️ 游戏未连接: {e}"
    if not live.get("ok"):
        # ⚠️ 别用 ❌ 报"没节日"：❌ 读作"查询失败"，AI 会当工具坏了去重试 —— 但这是**确实没有**，正常结果。
        #    同一件事 festival today 早写成了"今天没有节日。"，两处说法得一致（2026-09-11 清单扫出来）。
        #    C# 的原生 error 串（"No active event"）也别往回漏，那是英文的开发措辞。
        err = (live.get("error") or "").strip()
        if err.lower().startswith("no active event"):
            return "⚪ 现在没有正在进行中的节日活动。用 festival today 看今天、festival next 看下一个。"
        return f"⚠️ 查不到节日实况（{err}）。用 festival today/next 看节日安排。"
    actors = "、".join(f"{a.get('displayName') or a.get('name')}({a.get('x')},{a.get('y')})" for a in live.get("actors", []))
    return f"🎪 {live.get('festivalName', '节日')} 在 {live.get('location', '?')}，{live.get('actorCount', 0)} 个NPC：{actors or '无'}"


# 中日韩 + 全角 + CJK 标点（写成转义，别塞字面量——本文件在 GBK 终端里也跑过）
_CJK_RE = re.compile("[㐀-鿿豈-﫿＀-￯　-〿]")


def _flat_text(t) -> str:
    """把**游戏自己折过行**的文本摊平成一行（2026-09-12 恒）。

    `Response.responseText` 是游戏用 `parseText` 折行后的结果，中文没空格 ⇒ **一个字一行**
    （真机看到「我\\n觉\\n得\\n在\\n哪\\n里…」）。C# 读的字段没错，是我们显示时该摊平：
    含中日韩/全角字符 → 直接去换行（中文行间不加空格）；纯拉丁 → 换行变空格（英文是按词折的，删了会粘成一坨）。
    """
    s = "" if t is None else str(t)
    if "\n" not in s and "\r" not in s:
        return s
    if _CJK_RE.search(s):
        return s.replace("\r", "").replace("\n", "")
    return " ".join(s.replace("\r", "").split("\n")).strip()


def _opt_text(t) -> str:
    """选项文本取值：`/menu` 给 dict（{'text':…}）、`/state` 给纯串 —— 统一取出并摊平折行（2026-09-12）。"""
    return _flat_text(t.get("text") if isinstance(t, dict) else t)


# 🗣️ 搭话的"话头该交给谁"提示（2026-09-12 恒：搭话/advance 分工）——消费侧读它决定要不要提示 AI 转 advance
_DIALOGUE_NOTE = {"handoff": ""}


def _collect_dialogue(max_steps: int = 4, budget_s: float = 12.0) -> tuple:
    """**搭话专用**：只点短对话（NPC 闲聊/节日搭话，一般 1~2 句），到选项停下。返回 (台词, 选项)。

    ⚠️ 2026-09-12 恒定案：**搭话与 `menu advance` 分工**——本函数**绝不推进长剧情**。
       · 只处理 DialogueBox，最多 `max_steps` 句 / `budget_s` 秒，**遇选项立刻停**（交给 AI 选）；
       · 撞上**非节日剧情事件**（`eventId` 不以 `festival_` 开头）⇒ 立刻收手，`_DIALOGUE_NOTE["handoff"]`
         写明"这段是长剧情 → 用 menu advance"。因为演出型剧情里有大量**静默动画段**（走路/表演，
         既没对话也没跳过键），搭话工具硬推只会误操作 —— 长剧情交给 advance 看着**进度指针**走。
       · 节日事件（`festival_*`）整场恒在播 = **背景**，不代表"有话要推"，同样不推。
    ⚠️ 旧版这里调 `_advance_story`，节日期间 15 轮 × 15 步把 `festival interact` 磨到 **122 秒**，
       且同步工具跑在事件循环上 ⇒ 那 122 秒整个 :8000 不响应（见 CHANGELOG 09-12）。
    """
    _DIALOGUE_NOTE["handoff"] = ""
    collected = []
    t0 = time.time()
    for _ in range(max_steps):
        if time.time() - t0 >= budget_s:
            break                          # 预算到，带已收到的台词返回
        st = api.state(light=True)
        m = st.get("activeMenu") or {}
        ev = st.get("activeEvent") or {}
        if m.get("type") == "DialogueBox":
            d = (m.get("dialogue") or "").strip()
            # ⚠️ 去重比 entry（同上）：collected 存的是署名后的
            entry = _attributed(d, _speaker_of(m))
            if d and (not collected or collected[-1] != entry):
                collected.append(entry)
            if m.get("responses"):
                break                      # 出现选项 → 停，让 AI 选
            api.key("confirm")
            time.sleep(0.2)
            continue
        if ev.get("id"):
            if not str(ev.get("id") or "").startswith("festival_"):
                _DIALOGUE_NOTE["handoff"] = ("这段是**长剧情/演出**（不是 NPC 闲聊）——"
                                             "交给 `menu advance` 推：它会看着进度指针走、遇选项停下")
            break                          # 节日事件=背景 / 长剧情=交出去，都不在这儿推
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
    # 🚶 站哪：**先问游戏**（`_stand_near` = 4 正邻里第一个 `/passable` 的），**只决策一次、只走一趟**。
    #   ⚠️ 2026-09-13 恒抓到：原版死磕 `(tx, ty+1)`（NPC 正下方）—— NPC 站岸边/水边时那格是**水**，
    #   走不过去 ⇒ BFS 失败 ⇒ mod 兜底瞬移 ⇒ **人飘水里**（战报尾巴那条 `teleporting to (29,36)`）。
    #   ⚠️ 但**不许"不行就换一格再试"**：那会**围着 NPC 转圈**（恒明确担心这个）⇒ 这里**只挑一次**；
    #   挑不到就**原地不动**，让下面那句"够不着"如实报出去 —— **绝不试错式走位**。
    stand = _stand_near(tx, ty)
    cur = (api.state().get("location") or {}).get("name", "")
    if stand:
        sx, sy = stand
        # 走路统一 /walk_to（2026-08-13 恒拍板）先走近（拟人），再 position 精确对位（落点偏一格已知坑）
        try:
            api.walk_to_coord(cur, sx, sy)
            deadline = time.time() + 8
            while time.time() < deadline:
                ax, ay = api.player_tile()
                if abs(ax - tx) + abs(ay - ty) <= 1:
                    break
                time.sleep(0.2)
        except Exception:
            pass
        # 对位：position 精确站到**挑好的那格**（2026-08-18 恒：walk_to 落点偏一格，靠 position 对正）
        try:
            api.position(sx, sy)
            time.sleep(0.25)
        except Exception:
            pass
        # 朝向：站哪边就朝哪边看 NPC（原版恒 `face(0)`，只有"站在正下方"时才碰巧对）
        api.face({(0, 1): 0, (0, -1): 2, (-1, 0): 1, (1, 0): 3}.get((sx - tx, sy - ty), 0))
    ax, ay = api.player_tile()
    if abs(ax - tx) + abs(ay - ty) > 4:    # 复查距离：够不着优雅跳过（节日特殊位/墙后/挑不到站格）
        return f"  💤 {tname} 够不着（{abs(ax-tx)+abs(ay-ty)}格，可能墙后/特殊位），跳过"
    r = api._post("/interact", {"x": tx, "y": ty})   # 直接打 NPC tile，不依赖面前格
    if not r.get("ok"):
        return f"  ⚠️ {tname}: 搭话失败（{r.get('error', '')}）"
    collected, opts = _collect_dialogue()
    stand_bad = False
    if not collected and not opts:
        # 兜底：节日事件 actor 用 /interact 可能不触发（NPC走动/事件模式，2026-08-20 花舞节实测）
        # → 换 /festival/interact 按名重试（它内部 checkAction + npc.checkAction 双路径）
        # ⚠️ 2026-09-13：**这个端点会挪人**（把玩家摆到 NPC 旁边）。原版硬编码 `y+1` 且零检查 ⇒
        #   NPC 站岸边/水边时**人直接落水里**（恒截图"先站对、后下水"的真凶就是它）。
        #   C# 已改成挑可站邻格；这里**读回 `standPicked`** —— False = 四邻全站不住、退回默认站位
        #   ⇒ 如实报出去，**不静默**（宁报错别兜底）。
        try:
            fb = api._post("/festival/interact", {"name": a.get("name")})
            if fb.get("ok") and fb.get("standPicked") is False:
                stand_bad = True
            time.sleep(0.3)
            collected, opts = _collect_dialogue()
        except Exception:
            pass
    lines = []
    if stand_bad:
        lines.append(f"  ⚠️ {tname}: 四邻都站不住，退回默认站位（可能落水里）")
    if collected:
        for d in collected:
            lines.append(f"  💬 {tname}: 「{d}」")
    else:
        lines.append(f"  💬 {tname}: （没台词）")
    if opts:
        opt_str = " | ".join(f"[{i}]{_opt_text(o)}" for i, o in enumerate(opts))
        lines.append(f"  🗳️ {tname} 选项: {opt_str}")
        lines.append("  → menu_click(option=N) 选完再 festival interact 继续")
    if _DIALOGUE_NOTE.get("handoff"):
        lines.append(f"  🎬 {_DIALOGUE_NOTE['handoff']}")
    return "\n".join(lines)


# 节日社交巡礼断点（2026-08-18：空参循环挨个聊，遇选项停下，选完再调继续）
# ⚠️ 2026-09-13 改结构：`idx` → **`targets` 只存"还没聊的"**（聊完就 pop）+ `done` 计数。
#    因为改成**就近搭话**（每轮挑离当前位置最近的）后，顺序是动态的，用下标没法表达。
_fest_social_state = {"key": None, "targets": [], "done": 0}


def _festival_interact(name: str = "", budget_s: float = 120.0) -> str:
    """🎪 节日互动（2026-08-18 恒：自然走路，不瞬移）
    - 传 name：自然走到指定节日 NPC 前搭话，自动推进对话收台词（遇选项停下让 AI 选）
    - 空参：自动循环和节日现场【除刘易斯外】所有 NPC 挨个聊（纯对话自动点掉收台词；
      遇选项停下——处理完选项再调 festival interact 继续下一个；断点自动续传）
      ⏱️ **本批限时 `budget_s` 秒（默认 120，恒 2026-09-13 拍板）**：花舞节全场 33 人实测
         ≈108 秒 ⇒ 一次装得下；万一超时就**停手报"还剩 N 个"**，再调一次接着聊。
         **绝不硬撑到客户端读超时**（前一版就是这么把汇总丢掉的：`mcp_cli` 读超时 60s < 108s，
         工具在服务端明明跑完了，结果没人收得到）。
    ⚠️ 排除刘易斯：他是节日主持，很多节日跟他对话会开启节日小游戏/活动。
    """
    try:
        live = api.festival_status()
    except Exception as e:
        return f"⚠️ 游戏未连接: {e}"
    if not live.get("ok"):
        # ⚠️ 别用 ❌ 报"没节日"：❌ 读作"查询失败"，AI 会当工具坏了去重试 —— 但这是**确实没有**，正常结果。
        #    同一件事 festival today 早写成了"今天没有节日。"，两处说法得一致（2026-09-11 清单扫出来）。
        #    C# 的原生 error 串（"No active event"）也别往回漏，那是英文的开发措辞。
        err = (live.get("error") or "").strip()
        if err.lower().startswith("no active event"):
            return "⚪ 现在没有正在进行中的节日活动。用 festival today 看今天、festival next 看下一个。"
        return f"⚠️ 查不到节日实况（{err}）。用 festival today/next 看节日安排。"
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
        st["done"] = 0
    if not st["targets"]:
        # 空列表有两种：①本场压根没有可聊的人 ②前面几批已经全聊完 —— 分开报，别混。
        if st["done"]:
            st["key"] = None
            st["done"] = 0
            return "🎪 节日全场（除刘易斯）都聊完了"
        return "🎪 节日现场除刘易斯外没有其他 NPC 可聊（刘易斯是节日主持，对话会开小游戏）"
    results = []
    t0 = time.time()
    hit_option = False
    total = st["done"] + len(st["targets"])
    while st["targets"]:
        # ⏱️ 限时到点收工（恒 2026-09-13：设 120s，一场 33 人 ≈108s 装得下）。
        #    判据放**循环头**、且**至少聊一个**（results 非空才判），免得预算一进来就为 0 个。
        if results and (time.time() - t0) >= budget_s:
            break
        # 🧭 **就近搭话**（恒 2026-09-13："每次这头跑那头又跑回来的"）——每轮挑**离当前位置最近**的
        #   那个，而不是按 actors 数组顺序挨个跑 ⇒ 一趟扫过去、不再来回横穿地图。
        #   ⚠️ 这**只是排序**，不是"这个走不到就换一个试" —— 顺序变了但每人只走一趟，**不会围着谁转圈**。
        try:
            ax, ay = api.player_tile()
            st["targets"].sort(key=lambda a: abs(int(a.get("x", 0)) - ax) + abs(int(a.get("y", 0)) - ay))
        except Exception:
            pass          # 读不到位置就按现有顺序走，别为这个中断巡礼
        out = _festival_social_chat(st["targets"][0])
        results.append(out)
        st["targets"].pop(0)
        st["done"] += 1
        if "🗳️" in out:
            hit_option = True
            break
    spent = time.time() - t0
    head = f"🎪 节日社交巡礼 {st['done']}/{total}（本批 {spent:.0f}s）：\n"
    if st["targets"]:
        tail = ("\n🚦 遇到选项停下——menu_click(option=N) 选完，再调 festival interact 继续"
                if hit_option else
                f"\n⏱️ 本批限时 {budget_s:.0f}s 到点收工，**还剩 {len(st['targets'])} 个**"
                f"——再调一次 festival interact 接着聊（断点续传）")
        return head + "\n".join(results) + tail
    st["key"] = None
    st["done"] = 0
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


def _wait_walk(x, y, timeout: float = 7.0, abort=None) -> bool:
    """轮询等 AI 走到 (x,y)（walk_to 异步，需等待）。到达/停稳返回 True。

    🥚 `abort`：可选的"立刻别走了"判据（返回 True 就中断这次等待并回 False）。
    **2026-09-13 恒**："**吹哨时脚本停止不动应该就好了**" —— 原来只在**每颗蛋的间隙**查一次，
    而走路最长要等 12s，**哨响在那 12s 里没人听** ⇒ 人还在走、7843 还在渲染，
    刘易斯已经开始讲话了。把查哨塞进这个等待循环，**哨响即断**。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if abort is not None and abort():
            return False
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


def _walk_to_egg(x, y, abort=None) -> bool:
    """走到蛋格：先自然走（walk_to，**放宽等待让走完**），真走不到（栅栏/装饰挡）才 **/position 兜底**。
    ⚠️ 2026-08-17 两次实测修：
      ① _wait_walk 6s 太短→打断慢自然走→position 抢走→交互漏蛋 → 放宽 12s
      ② position 直接站到蛋格上→checkAction 从"站蛋上"出发触发不了（第一轮自然走=邻格才成）→
         position 兜底**站到蛋的邻格**（保持相邻，交互仍打蛋格）"""
    _halt_move()
    try:
        api.walk_to_coord("Temp", x, y)
        if _wait_walk(x, y, timeout=12, abort=abort):
            return True
    except Exception:
        pass
    # 🥚 哨响 → 别再用 /position 补位（那等于"哨响后还瞬移去捡"，比走路更难看）
    if abort is not None and abort():
        return False
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
                # 🔔 哨响（festivalTimer 归零）/出现对话（宣布结果）→ 收手（先停住残留走动）
                if _egg_hunt_over() or _dialogue_now():
                    _halt_move()
                    lines.append("🔔 听到结束哨（或已宣布结果），收手" if _egg_hunt_over()
                                 else "💬 寻宝结束/宣布结果，收手")
                    break
                before = _egg_score()
                # 自然走（栅栏挡着走不到就直接跳过，不钻栅栏）
                # 🔔 abort=_egg_hunt_over：**走路那 12 秒里哨响就立刻断**，不走到蛋跟前
                if not _walk_to_egg(x, y, abort=_egg_hunt_over):
                    if _egg_hunt_over():
                        _halt_move()
                        lines.append("🔔 走到一半听到结束哨，收手（没交互）")
                        break
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
                # 🔔 转朝向那 0.5s 里哨响了 → 别交互了（哨响后还伸手去拿，最难看的就这一下）
                if _egg_hunt_over():
                    _halt_move()
                    lines.append("🔔 举手前听到结束哨，收手（没交互）")
                    break
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


def _egg_hunt_over() -> bool:
    """🔔 寻宝**结束哨**响了没 —— `festivalTimer <= 0` 就是那一刻。

    2026-09-13 恒："**能不能检测到结束的吹哨呢？吹哨时脚本停止不动应该就好了。**"
    **判据本来就有，缺的是粒度**（原来只在每颗蛋的间隙查一次，走路那最长 12s 没人听）。
    游戏侧的实锤（`Event.cs:11171`）：`festivalTimer <= 0` 时游戏自己
    `Game1.player.Halt()` + `EndPlayerControlSequence()` + 接 `afterEggHunt`（刘易斯讲话）
    ⇒ **零就是权威信号，不用另找音效**。

    ⚠️ 两条别搞错：
      · **用便宜的 `/event_state`**（docstring 原话"便宜、可低频轮询"）；`/festival` 会遍历全部
        actor（蛋蛋节 33 个 NPC），**别拿它进轮询**——原来 `_festival_timer()` 就是那个贵的。
      · **`-1` 一律当"没结束"（绝不误停）** —— `ModEntry.cs:17777` 写死 `int festivalTimer = -1;`
        当**哨兵值**（没有事件 / 反射读不到都给 -1）。而真实倒计时**归零后是小负数**
        （`Event.cs:11106` 只在 `>0` 时递减，最后一帧会减过头，然后一直保持那个负值）
        ⇒ **`-1` 和 `-11` 语义相反**，不能一刀切 `<=0`。
        ⚠️ 已知窄缝：真倒计时**恰好**落在 -1 时会被当"没结束"（那时靠 `_dialogue_now()` 兜底收手）。
        概率极低、且后果只是"多等一次检查"，接受。
      · **开跑前提**本来就是"timer>0"（`_festival_egg_run` 开头那道闸）⇒ 跑动中读到 `<=0` 才结算。
    """
    try:
        t = (api.event_state() or {}).get("festivalTimer")
        if t is None or int(t) == -1:      # -1 = C# 哨兵：没事件/读不到 → 绝不误停
            return False
        return int(t) <= 0
    except Exception:
        return False


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
            f"  🧭 走法：`walk_to 宝箱坐标`（BFS 读实时迷宫墙自适配，含暗道/传送），想自己玩看棋盘用 scene maze/maze_seg，按段走 `map walk_multi`（=闲逛；旧名 festival maze_walk 仍可用）。\n"
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
            # ⚠️ 2026-09-19 恒：「`isInBed` 就只是字面的'在床上'，**每天都是从床上开始的**」
            #    —— 它按"脚踩的那格有没有 Bed 属性"算（`Farmer.cs:7553`），脚踩床格就成立，
            #    所以站在床边随便开个节日框都会被误判成"在睡"⇒ 节日进场被判掉。
            #    ⇒ 判据换成**游戏自己写在 `checkName` 上的名字**（`readyCheck.name`）：
            #      `"sleep"`=睡觉就绪 / `"festivalStart"`=节日入场就绪 —— 这是权威源
            #      （同 2026-09-13 状态条那处的做法）。
            rc_name = (am.get("readyCheck") or {}).get("name")
            if rc_name == "sleep":
                return ""                                # 明确是睡觉 → 不是节日
            if rc_name == "festivalStart":
                return "menu:ReadyCheckDialog"           # 明确是节日入场 → 就是它
            # 旧 DLL 不报 readyCheck → 退回老判据（方向安全：宁可当节日也别漏报）
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
    - 邀 NPC：**不走本工具**——用裸 /interact 站紧邻格弹「什么事？」→选「邀请XX作舞伴」（需4心+）。
      ⚠️ **点法必须带 `real=true`**（`menu click(option=N, real=true)`，N 通常 0）：不带 real 时 mod 走
      `answerDialogueQuestion` 且 NPC 靠"面朝格"找（`isCharacterAtTile(player.GetGrabTile())`）⇒ 对不上就
      **静默点空**。2026-09-13 真机：不带 real 连点两次框不关；带 real 一次 ⇒ 海莉报出**接受台词**（真生效）
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
    """🚶 多段走位 / 闲逛 —— 喂一串坐标，依次走过去（"x,y x,y …"空格/分号分隔），每段等到了再走下一段。

    🎪 **正事**：万灵节迷宫（奇数/偶数年布局都变）——换个布局喂不同点位即可；`scene maze_seg` 出的
       走法链可以直接喂进来；也支持手挑中点。
    🫧 **活人感**（2026-09-12 恒：这是"纯活人感娱乐工具"，AI 被允许**没目的**地动一动）：
      · **闲逛** —— 在广场/院子里随手挑几个点遛一圈
      · **示好** —— 绕着某人转圈（挑一圈围着他/她的点）
      · **玩水** —— 在浴场泳池里绕圈游（泳池格不是"水格"，walk_to 照常寻路，实测可游）

    参数：waypoints / location / max_wait（每段最多等几轮 × 0.7s）/ max_seg（最多几段）。"""
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
    got = 0; fails = []; near = []; total = len(pts)
    t0 = time.time()
    for i, (x, y) in enumerate(pts, 1):
        try:
            api._post("/walk_to", {"location": loc, "x": x, "y": y})
        except Exception as ex:
            fails.append(f"段{i}({x},{y})发送失败:{ex}")
            continue
        # 🔴 2026-09-12 修：以前这里**只要 `isMoving == False` 就判"到达"** —— 而 walk_to 规划失败时
        #    人**压根不动**，第一次轮询（0.7s）就 False ⇒ **当场谎报"到达✅"**。娱乐工具更要诚实，
        #    不然"绕圈"看着成功、其实人没动。改成"真停下来了 **且** 坐标真的对上"。
        #    容差 ±1 格：/walk_to 落 y*64-32、/position 落 y*64（差 16px），同一格读出来偶尔差一格
        #    （CHANGELOG 2026-09-10「走位落点精度」）——卡死 ±0 会冤判。
        arrived = False; seen_move = False; idle = 0
        ax = ay = None
        for _ in range(int(max_wait)):
            time.sleep(0.7)
            try:
                pp = (api._get("/state") or {}).get("player", {}) or {}
            except Exception:
                continue
            if pp.get("isMoving"):
                seen_move = True; idle = 0
                continue
            ax, ay = pp.get("x"), pp.get("y")
            # ⚠️ 容差 ±1 格是给两件事留的：①落点精度（/walk_to 与 /position 差 16px）②walk_to 的
            #    "目标不可走时退到最近可走邻居"兜底。但**兜底 ≠ 到达**，所以**得说出来**：
            #    精确同格 = 到达；落在相邻格 = 🟡 单独点名（真机实测：喂墙格 (15,11) → 人停 (15,10)）。
            if ax is not None and abs(ax - x) <= 1 and abs(ay - y) <= 1:
                arrived = True
                if ax != x or ay != y:
                    near.append(f"段{i}({x},{y})→({ax},{ay})")
                break
            if seen_move:
                break        # 动过又停了、人却不在目标格 ⇒ 卡住，别干耗满 max_wait
            idle += 1
            if idle >= 3:
                break        # 一次都没起步（walk_to 没派活）⇒ 同样别干耗
        if arrived:
            got += 1
        else:
            where = f"停在({ax},{ay})" if ax is not None else "读不到坐标"
            fails.append(f"段{i}({x},{y})未到（{where}）")
    end = (api._get("/state") or {}).get("player", {})
    secs = int(time.time() - t0)
    head = (f"🚶 多段走位 {total} 段, {got} 段到达✅, "
            f"起({start_pos[0]},{start_pos[1]})→末({end.get('x')},{end.get('y')}), ∫{secs}s")
    tail = ""
    if near:
        tail += (f"\n🟡 {len(near)} 段落在**相邻格**（目标格可能本身不可站，walk_to 退到了最近可走格；"
                 f"也可能只是落点精度）：" + "; ".join(near))
    if fails:
        return head + tail + "\n⚠️ 未到段: " + "; ".join(fails)
    return head + tail + ("\n✅ 全程走通，无需逐段日志" if not near else "\n🟡 全程走完（有相邻格落点，见上）")


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
    """🎪 节日域。today 今天节日 / go 去 / info 实况 / interact 互动(**空参=全场巡礼搭话，可能跑~2分钟，属正常，别当卡住**) / answer 应答 / shop 节日商店 / prep 备战。节日专属 op（复活节捡蛋、花舞节跳舞、迷宫等）→ help(festival)。"""
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


def _furniture_miss_msg(x: int, y: int) -> str:
    """🪑 拿家具时那格**没命中家具** —— 把就近家具坐标摊出来（2026-09-19 恒）。

    原先这里回的是「站近点/确认是自己摆的/背包有空位」**三选一瞎猜**，真因（这格压根没家具）
    反而一个字没说。而 C# 那边**本来就知道答案**：`HandleFurniturePickup` 扫 `loc.furniture`
    找包围盒命中，命中不了 `furniture` 就是 null——这个判据一路传到了 Python 手里，被扔了。
    按恒的规矩（报错必须给下一步、**能替 AI 做的别推给 AI**）在这里外扩成"能照抄的坐标"。
    """
    try:
        fs = (api.furniture_scan() or {}).get("furniture") or []
    except Exception:
        fs = []
    head = (f"⚠️ ({x},{y}) 这格**没有家具** —— 多半坐标点偏了，或者它已经被拿走了。"
            f"**物品没动。**")
    if not fs:
        return head + "\n🪑 当前地点一件家具都没有（换间屋子，或先 `ops=place` 摆一件）"
    fs = sorted(fs, key=lambda f: abs((f.get("x") or 0) - x) + abs((f.get("y") or 0) - y))
    near = "；".join(f"{f.get('name', '家具')}@({f.get('x')},{f.get('y')})" for f in fs[:5])
    return (head + f"\n🪑 就近的家具：{near}\n"
                   f"👉 换个坐标再调一次：ops=pickup kw={{tile_x:…, tile_y:…}}"
                   f"（**大件只报左上角那格，但点它覆盖的任意一格都行**）")


# ── 🪑 家具「读回验证」的两个小工具（2026-09-19，坐实「报的是地毯、动的是椅子」）──────────
# 为什么需要：`furniture_pickup` 原来直接把 C# 给的 `furniture` 名字回给 AI，而那个名字是
#   「**第一个包围盒命中**」—— 游戏真删的却是**从后往前**扫到的那件
#   （`GameLocation.cs:7999` `for (num = furniture.Count-1; num >= 0; num--)`），
#   同一格压着两件家具时**两边逻辑上必然报不同的那件**，不是偶尔猜错。
#   ⇒ 改成**前后 diff**：不猜、也不照抄一份判据（照抄将来游戏一改又会漂），直接看谁不见了。
#   ⚠️ **key 里绝不能带名字**：同款两件（两把橡木椅子）名字一模一样，一对比就分不出来；
#     用 (itemId, x, y, 宽, 高) —— 世界里家具的"型号+位置"是唯一的。
def _furn_key(f) -> tuple:
    return (f.get("itemId"), f.get("x"), f.get("y"), f.get("width"), f.get("height"))


def _furn_list():
    """当前地点的家具全表；**读不到返回 None**（调用方要分得清"真的空了"和"我没读到"）。"""
    try:
        return api.furniture_scan().get("furniture") or []
    except Exception:
        return None


def _inv_slots():
    """背包**占格数**（不含空格）；读不到返回 None。

    ⚠️ 用"占格数"而**不是"物品数量之和"**：家具的 `Stack` 可能是 0（恒 2026-09-19：
    家具在包里不堆叠、占一格），按数量求和会算出 `-1` 这种鬼数，判断直接失真。
    """
    try:
        return len(api.state().get("inventory") or [])
    except Exception:
        return None


def _wait_furniture_gone(before, tries: int = 12, dt: float = 0.25):
    """等 `before` 里**最先消失**的那件，返回它；一直没少 / 读不到，返回 None。

    ⚠️ **必须轮询**：删除走 `furnitureToRemove`（`NetMutexQueue`，Processor=`removeQueuedFurniture`）
    ⇒ **下一个 update 才真删**，`LowPriorityLeftClick` 返回时还没删。读完就走会误判成"没拿动"。
    """
    if not before:
        return None
    want = {_furn_key(f): f for f in before}
    for _ in range(tries):
        time.sleep(dt)
        cur = _furn_list()
        if cur is None:
            return None
        left = {_furn_key(f) for f in cur}
        for k, f in want.items():
            if k not in left:
                return f
    return None


@mcp.tool()
def furniture_pickup(tile_x: int, tile_y: int) -> str:
    """🪑 拿起家具（把摆放的家具收回背包）
    指定家具所在瓦片，等同游戏左键点击拿走（SDV 1.6 右键拿不起家具）。
    ⚠️ **距离**：能装修的屋子（自家小屋/棚屋/岛屋）里**隔着整间屋也拿得动** ——
       游戏 `CanFreePlaceFurniture()` 恒真，把"站旁边"那道 96px 检查短路了；
       只有**户外农场**这类非装修图才要站 1~2 格内。（09-19 真机：(10,10) 拿起 (25,14)，隔 15 格成功）
    ⚠️ **初始家具也拿得起**（`AllowLocalRemoval` 默认 true）；真拿不起的是**别人家的床**。
    ✅ **报的名字是"读回来"的**（2026-09-19 起）：拿完会**前后 diff 家具表**，报**真正少掉**的那件。
       以前回的是 C# 猜的名字（"第一个包围盒命中"），而同格压着两件家具时**游戏是从后往前扫**的
       ⇒ 必然报错人（真机实证：回「拿起了 Burlap Rug」，实际拿走的是椅子）。
    ✅ **背包满现在会被抓出来**（以前是静默的）：游戏 `removeQueuedFurniture` 一旦
       `couldInventoryAcceptThisItem` 为假就**整个 return（连家具都不删）**，而 `picked` 照样回 true。
       本工具会盯着看家具少没少 —— 一件没少就如实报"没拿动"并让你先腾格子。
    ⚠️ 开菜单时拿不了。
    摆放走同一域：ops=place kw={name:…, x:…, y:…}

    Args:
        tile_x: 家具所在的瓦片 X 坐标（大件可点它覆盖的任意一格）
        tile_y: 家具所在的瓦片 Y 坐标
    """
    try:
        # 🔍 2026-09-19 **读回验证**（恒真机抓到「报的是地毯、动的是椅子」）：
        #    病根**不在名字取错，在扫描方向相反** —— 游戏 `GameLocation.cs:7999` 是
        #    `for (num = furniture.Count-1; num >= 0; num--)`（**从后往前**，拿最上面那件），
        #    而 C# 那边 `foreach (var f in loc.furniture)` **从前往后**取"第一个包围盒命中"
        #    ⇒ 同一格压着两件家具时，**两边逻辑上必然报不同的那件**，不是偶尔猜错。
        #    ⚠️ 光把方向改对也只是"照抄一份判据"，将来游戏一改就又漂 —— 所以这里**不猜**：
        #       拿 `/furniture` **前后 diff**，报**真正少掉**的那件。C# 回的 `furniture` 一个字不用。
        #    ⚠️ 游戏的删除走 `furnitureToRemove`（NetMutexQueue，`removeQueuedFurniture` 是 Processor）
        #       ⇒ **下一个 update 才真删**，所以要点轮询等它落地，不能读完就走。
        _before = _furn_list()
        _n0 = _inv_slots()
        r = api.furniture_pickup(tile_x, tile_y)
        if not r.get("ok"):
            return _with_state(f"❌ 拿起家具失败: {r.get('error', '未知')}")
        if not r.get("picked"):
            # picked=False：**"这格有没有家具"看 `furnitureHere`**（C# 里"第一个包围盒命中"那个，
            # 语义就是「你点的那格上有没有东西」）。⚠️ **不能再用 `furniture`** —— 它现在是
            # C# 预测的"游戏会挑中哪件"，遇到 `canBeRemoved=false`（别人家的床/坐着人/手上拿着东西）
            # 时会预测不出来、回 null，那时**反推成"这格没家具"就是错的**（2026-09-19 拆成两个字段的原因）。
            _here = r.get("furnitureHere")
            if not _here:
                return _with_state(_furniture_miss_msg(int(tile_x), int(tile_y)))
            _tgt = r.get("furniture")
            _who = f"「{_tgt}」" if _tgt else f"这格上的「{_here}」"
            return _with_state(
                f"⚠️ {_who}没拿起来：可能是**站太远**（只在户外这类非装修图才有这限制）、"
                f"**开着菜单**、或这是**别人家的床**。**物品没动。**")
        _gone = _wait_furniture_gone(_before)
        if _gone:
            # 再补一环：**地上少了 ≠ 包里多了**。家具不堆叠（恒 2026-09-19），
            # `removeQueuedFurniture` 是"先塞工具栏 12 格，塞不下再 addItemToInventory"——
            # 两头都可能出岔子，所以**数一下背包占格有没有 +1**。
            # （用占格数而不是数量：家具的 `Stack` 可能是 0，按数量算会得出 -1 这种鬼数。）
            _n1 = _inv_slots()
            if _n0 is not None and _n1 is not None and _n1 <= _n0:
                return _with_state(
                    f"⚠️ 「{_gone.get('name') or '家具'}」**从地上没了，可背包占格没多**（{_n0}→{_n1}）。\n"
                    f"两种可能，我不敢替它下结论：\n"
                    f"  ① **并进了已有的同类格** —— 家具本该不堆叠，但被写成 `Stack=0` 的那种格子\n"
                    f"     可能被 `addItemToInventory` 当成「没满」（这条我**没验过**，只是有可能）\n"
                    f"  ② **真没进包**。\n"
                    f"👉 立刻 `check ops=backpack` 数一遍：**数目对不上就告诉我**，别当它拿到了")
            return _with_state(f"🪑 拿起了 {_gone.get('name') or '家具'}，已收回背包")
        if _before is None:
            # 读不到家具表 ⇒ **没复核到**，如实说，别拿 C# 那个猜的名字糊过去
            return _with_state(
                "🪑 已点击（游戏回了 picked=true）——⚠️ **但我没能复核**（读不到家具列表），"
                "到底拿走没有**请以背包为准**")
        # picked=true **却一件家具都没少**：游戏那边**根本没删**。
        # 反编译 `removeQueuedFurniture`：`if (!furniture.TryGetValue(...) || !player.couldInventoryAcceptThisItem(value)) return;`
        # ⇒ **背包接不下就整个 return**（连删都不删）—— 这正是 #27「背包满静默失败」的真身，
        #   而 **picked 照样回 true**。以前我们照抄成「已收回背包」，AI 以为拿到了、其实地上还在。
        return _with_state(
            "⚠️ 游戏回了 picked=true，可**家具一件没少**（我盯了 3 秒）——\n"
            "最常见是**背包接不下**：反编译 `removeQueuedFurniture` 是"
            "`if (!couldInventoryAcceptThisItem(value)) return;` ⇒ **连删都不删**。\n"
            "**物品没动、地上还在。**\n"
            "👉 先腾格子：`check ops=backpack` 看剩下什么，或 `scene ops=drop` 丢几件再来")
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


def _decor_rooms(d: dict, kind: str) -> dict:
    """`/decor` 回包里的 floors/walls（按房号）。kind: 'floor'|'wall'。"""
    return d.get("floors" if kind == "floor" else "walls") or {}


def _decor_examples(rooms: dict, n: int = 3) -> str:
    """屋里能铺的格，按房间举例（认房间不认格，所以同一间挑哪格都行）。"""
    out = []
    for rid in sorted(rooms.keys(), key=lambda s: str(s)):
        r = rooms[rid] or {}
        tiles = (r.get("tiles") or [])[:n]
        pts = " ".join(f"({t[0]},{t[1]})" for t in tiles)
        out.append(f"room={rid} 共 {r.get('count')} 格，例：{pts}")
    return "；".join(out)


def _decor_place_check(name: Optional[str], x: int, y: int) -> str:
    """🪵 要放的是地板/墙纸吗？是的话先对 `/decor` 表 —— 格不对就**明说该点哪**。

    （恒 2026-09-19：「AI 很可能分不清哪里是地板哪里是墙」。）
    反编译：`Wallpaper.placementAction`（`Wallpaper.cs:159-204`）在
    `GetFloorID/GetWallpaperID` 取不到房间号时**静默 return false** ——
    游戏什么都不会发生、物品也不消耗，但回包只会给一句 "Cannot place"，
    AI 拿着它只能瞎试下一格。这里替它把"该点哪"摆出来。

    返回 ""（不是地板墙纸 / 查不到 / 这格本来就对）或一段可直接照做的拒绝文案。
    """
    if x is None or y is None:
        return ""
    try:
        st = api.state()
    except Exception:
        return ""
    look = name
    if not look:
        look = (st.get("player") or {}).get("currentItem") or ""
    if not look:
        return ""
    ent = _inv_entries(st, look)
    qid = next((str(e.get("itemId") or "") for e in ent), "")
    if not (qid.startswith("(FL)") or qid.startswith("(WP)")):
        return ""                                    # 不是地板/墙纸，走原路
    want = "floor" if qid.startswith("(FL)") else "wall"
    label = "地板" if want == "floor" else "墙纸"
    disp = next((e.get("displayName") or e.get("name") or look for e in ent), look)
    try:
        d = api._ai_get("/decor")
    except Exception:
        return ""
    if not d.get("ok") or not d.get("decoratable"):
        return (f"❌ 「{disp}」铺不了：{d.get('location', '这里')} 不是可装修场景"
                f"（地板/墙纸只能铺在农舍/小屋/棚屋这类室内）。**物品没消耗、没动过。**")
    rooms = _decor_rooms(d, want)
    if any([x, y] in (r.get("tiles") or []) for r in rooms.values()):
        return ""                                    # 这格本来就对
    other_kind = "wall" if want == "floor" else "floor"
    other_label = "墙格" if want == "floor" else "地板格"
    other_rooms = _decor_rooms(d, other_kind)
    in_other = any([x, y] in (r.get("tiles") or []) for r in other_rooms.values())
    # 🎯 恒担心的那句就在这儿：告诉他"这格其实是墙不是地板"
    why = (f"——这格是**{other_label}**（它铺的是{'墙纸' if want == 'floor' else '地板'}），"
           if in_other else "——这格既不是地板格也不是墙格。")
    if not rooms:
        return (f"❌ 「{disp}」铺不了 @({x},{y})：这间屋子的清单里**一格都没有**可铺的位置。"
                f"**物品没消耗。**")
    return (f"❌ 「{disp}」铺不了 @({x},{y})：这格不是{label}格{why}**物品没消耗、没动过。**\n"
            f"🧱 能铺的{label}格（**认房间不认格**，同一间随便挑一格都行）：{_decor_examples(rooms)}\n"
            # ⚠️ 文案**不写死域名**：`place`/`decor` 在 scene/farm/cabin 三个域都有，
            #    而这个函数是被哪一路调进来的只有调用方知道 —— 写死 "scene ops=…" 会让
            #    站在屋里（cabin 域）的 AI 以为自己要换个域。
            # ⚠️ 回显**限定 id**（`(FL)1`），不是显示名「地板」——
            #    同名多款只有编号分得开，照「地板」做会**又踩回同名坑**（#13）。
            f"👉 改成：ops=place kw={{name:\"{qid or disp}\", x:…, y:…}}（本域就有）"
            f"（或先看全表：ops=decor）")


def decor_report() -> str:
    """🪵 这间屋子能往哪儿铺地板/墙纸（scene 域，2026-09-19 恒）

    地板/墙纸**只认装饰房间的格子**：地板点地板格、墙纸点靠墙那圈墙格，点错了游戏**静默不理**。
    这里把游戏自己的真值表（`DecoratableLocation.floorTiles/wallpaperTiles`）摊开给你挑。
    **认房间不认格** —— 同一间房随便挑一格，效果一样。
    """
    try:
        d = api._ai_get("/decor")
    except Exception as e:
        return _with_state(f"❌ 查不到装修信息: {e}")
    if not d.get("ok"):
        return _with_state(f"❌ 查不到装修信息: {d.get('error', '未知')}")
    loc = d.get("location", "?")
    if not d.get("decoratable"):
        return _with_state(f"🪵 {loc} 不能铺地板/墙纸——{d.get('hint', '')}")
    lines = [f"🪵 {loc} 可铺："]
    for kind, icon, label in (("floor", "🧱", "地板"), ("wall", "🖼️", "墙纸")):
        rooms = _decor_rooms(d, kind)
        if not rooms:
            lines.append(f"  {icon} {label}：这间屋子没有可铺的{label}格")
            continue
        lines.append(f"  {icon} {label}（认房间不认格）：")
        for rid in sorted(rooms.keys(), key=lambda s: str(s)):
            r = rooms[rid] or {}
            tiles = (r.get("tiles") or [])[:3]
            pts = " ".join(f"({t[0]},{t[1]})" for t in tiles)
            cur = r.get("applied")
            lines.append(f"    room={rid} 现在={cur if cur is not None else '?'} "
                         f"共 {r.get('count')} 格，例：{pts}")
    # ⚠️ 同上：**不写死域名**（这个 op scene/farm/cabin 都有）
    lines.append("👉 铺：ops=place kw={name:\"地板\", x:…, y:…}（背包里显示名是「地板」/「壁纸」）")
    return _with_state("\n".join(lines))


# ── 🧍 放置的两道新门（2026-09-19 恒「叠放吃物品」，真机四情形全走完）────────────────────
# **背景**：`/use {x,y}` 是**直接调 `Object.placementAction`**、**跳过 `Utility.playerCanPlaceItemHere`**
#   ⇒ 隔半张图也能凭空放，于是看得见游戏内部那两处粗糙行为（真人玩家永远撞不到）：
#     ① 目标格已有**同款** ⇒ 兜底那段 `if (id != id)` 不成立 ⇒ **什么都不做**，但函数末尾照样
#        `return true` ⇒ 我们先前照抄成「已放置」并 `reduceActiveItemByOne()` ⇒
#        **物品凭空消失、地上零变化**（铁证：`/use` 回 `{"ok":true,"action":"placed"}`、背包 1→0、地上没变）。
#     ② 目标格已有**异款** ⇒ `Game1.createItemDebris(旧的)` **把原来那台打落成掉落物**，新的顶上去。
#     ③ 箱子反而**安全** —— 它走的是另一条分支（反编译 `IL_1d66`），自带 `objects.ContainsKey` 检查、
#        游戏自己会拒（真机对照：`Cannot place 'Chest' here`、物品没消耗）。
#   ⇒ 差别只在**兜底那段没查**。C# 侧已补「读回验证」兜住所有调用方；这里两道门是**给 AI 当场说清**、
#      并且**在动手之前**就把事拦住（C# 那道是事后如实报，那时物品已经出过手了）。
#
# ⚠️ **两道门必须按顺序**：先拟人闸门（保证目标格落进 `/surroundings` 的半径），再占位守门（才看得见那格）。
#
# 📐 闸门取 **2 格**，**照抄游戏、不是我拍脑袋**（`Utility.playerCanPlaceItemHere` 的两条判据）：
#     · 鼠标放置   → `withinRadiusOfPlayer(x, y, 1, f)`            → `Utility.cs:5719` Chebyshev ≤ 1
#     · 非鼠标放置 → `_HasNonMousePlacementLeeway`                → `Utility.cs:5446` Chebyshev ≤ 2
#     取宽的 2（AI 对应"非鼠标放置"那一档），**且判据形式也照抄**：`withinRadiusOfPlayer` 比的是
#     **Chebyshev（切比雪夫/棋盘距离 max(|dx|,|dy|)）**，不是欧氏、不是曼哈顿 —— 别自己换。
_PLACE_REACH = 2


def _place_reach_check(name: Optional[str], x: int, y: int) -> str:
    """🧍 够得着吗？够不着返回拒绝文案，够得着返回 ""。

    ⚠️ **地板/墙纸必须豁免**：`Utility.cs:5736` 那句
        `... || (item is Wallpaper && location is DecoratableLocation) || ...`
    用 `||` 短路 ⇒ 在装修图里**压根不判距离**（`Flooring` 也是 `Wallpaper`）。
    不豁免的话，**靠远墙那圈墙格一次都铺不了**。
    """
    if x is None or y is None:
        return ""
    try:
        st = api.state()
    except Exception:
        return ""
    look = name or (st.get("player") or {}).get("currentItem") or ""
    qid = next((str(e.get("itemId") or "") for e in _inv_entries(st, look)), "")
    if qid.startswith("(FL)") or qid.startswith("(WP)"):
        return ""
    pl = st.get("player") or {}
    px, py = pl.get("x"), pl.get("y")
    if px is None or py is None:
        return ""
    # 照抄 withinRadiusOfPlayer：Chebyshev，不是欧氏距离
    d = max(abs(int(px) - int(x)), abs(int(py) - int(y)))
    if d <= _PLACE_REACH:
        return ""
    return (f"❌ 够不着 @({x},{y})：你现在在 ({px},{py})，**隔了 {d} 格**。\n"
            f"🧍 真人放置最多够到 **{_PLACE_REACH} 格**（游戏 `Utility.playerCanPlaceItemHere` 的判据），"
            f"再远就不是人做得出来的动作了。**物品没消耗、地上没动。**\n"
            f"👉 先走过去：map ops=walk kw={{x:{x}, y:{y}}}，到了再调一次 place")


def _place_empty_hint(tiles, x: int, y: int, n: int = 3) -> str:
    """从 `/surroundings` 的扫描结果里挑几个**没物件**的格给 AI 照抄。

    ⚠️ 只说"**没物件**"，**不说是"可站"** —— 放置本来就不要求那格能站（洒水器/栅栏正是用来占路的），
    而且 `/surroundings` 的 `passable` 字段我们还没验过（2026-09-19 撞见过它和 `/passable` 报不同答案，
    复测又一致 ⇒ 未定性）。宁可说小一点，也别给一个我没验过的判据。
    """
    cand = [(t["x"], t["y"]) for t in tiles
            if t.get("x") is not None and t.get("y") is not None and not t.get("object")]
    cand.sort(key=lambda p: abs(p[0] - x) + abs(p[1] - y))
    pick = cand[:n]
    return " ".join(f"({a},{b})" for a, b in pick) if pick else "（附近没扫到没物件的格）"


def _place_occupied_check(name: Optional[str], x: int, y: int) -> str:
    """🚧 要放的那格已经有东西了吗？有就返回拒绝文案。

    **必须在 `_place_reach_check` 之后调**：目标格只有在 ≤2 格时才落进 `/surroundings` 的半径，
    否则这里读不到那格、会静默放行（那就是兜底了，恒不要）。

    ⚠️ **读不到那格时放行**（返回 ""），**这不是兜底、是有意的分层**：
      `BuildSurroundings` 会跳过越界格（`tx<0 || ty<0 || tx>=mapW || ty>=mapH`），玩家贴地图边时
      目标格可能不在结果里。那时**不判** ≠ "假装它空着" —— 因为 **C# 侧的读回验证才是正确性地板**
      （它直接看 `loc.objects`，不依赖任何扫描半径）；这道 Python 门只是**在动手之前**把话说清楚，
      漏判的那一下由 C# 事后如实报。两层的分工就是"能提前说的提前说，说不了的由底下兜住"。
    """
    if x is None or y is None:
        return ""
    try:
        st = api.state()
        tiles = (api._get("/surroundings", {"radius": _PLACE_REACH}).get("tiles") or [])
    except Exception:
        return ""
    t = next((t for t in tiles if t.get("x") == x and t.get("y") == y), None)
    if t is None or not t.get("object"):
        return ""
    existing = t.get("object")
    eid = t.get("objId") or ""
    look = name or (st.get("player") or {}).get("currentItem") or ""
    qid = next((str(e.get("itemId") or "") for e in _inv_entries(st, look)), "")
    if qid and eid and qid == eid:
        why = (f"⚠️ 游戏对**同款叠放静默不做事** —— 它照样回报成功，谁照抄它就以为放上了，"
               f"**你手上这台凭空消失**（真机实证：`/use` 回 `ok:true`、地上没变、背包 −1）。")
    else:
        why = (f"⚠️ 真人放不了（那格已被占，游戏会拦）。强行放**会把它打落成掉落物**、新东西顶上去 ——"
               f"满包或滚远了就是**真丢**。")
    return (f"❌ ({x},{y}) 上有「{existing}」，放不下这台。\n{why}\n"
            f"**物品没消耗、地上没动。**\n"
            f"👉 想放这儿就先拆：ops=break kw={{x:{x}, y:{y}}}；"
            f"或换一格（你身边**没物件**的格：{_place_empty_hint(tiles, x, y)}）")


@mcp.tool()
def place_item(name: Optional[str] = None, x: Optional[int] = None, y: Optional[int] = None) -> str:
    """🪧 放置物品/播种（把背包物品放到指定格：落地/种树；scene 域）
    流程：select(name) → /use{x,y} → placementAction 放地上（箱子/机器/蟹笼）或种下（树种/作物种子）。
    ⚠️ **只能放可放置/可种物**（箱子、机器、蟹笼、树种、作物种子等）；书/纸条等不可放置物会失败且**不消耗**（安全，不会丢地上收不回）。
    🪵 **地板/墙纸是特例**：只能点在**装饰房间的格**上——地板要点**地板格**、墙纸要点**靠墙那圈墙格**，
       点错了游戏**静默不理**（连错在哪都不说）。拿不准先 `ops=decor` 看这间屋子能铺哪
       （`decor` 在本 op 所在的每个域都有：scene/farm/cabin）；
       点错时本工具会直接告诉你"这格其实是墙不是地板"并给出能铺的格。
    🧍 **够得着才放**：目标格要在你**身边 2 格内**（照抄游戏 `Utility.playerCanPlaceItemHere` 的判据，
       地板/墙纸豁免——游戏自己对它不判距离）。够不着会给你"先走过去"的那一步。
    🚧 **那格得是空的**：目标格上已有东西就**拒绝**——同款叠同款游戏会**静默不做事却回报成功**
       （你手上那台凭空消失，真机实证过），异款叠上去会**把原来那台打落成掉落物**。

    Args:
        name: 物品英文名（Chest / Keg / Maple Seed / Crab Pot …），不传则用当前手上物
        x, y: 目标瓦片坐标（留空=放玩家面前格）
    """
    try:
        if name:
            # ⚠️ 必须看 select 的返回（2026-09-11 抓到的谎报 + 真危害）：
            #    select 失败不抛异常，以前这里直接往下走 `/use force` ⇒ 挥的是**手上原来那把**，
            #    却回报「已放置」。在农场等于对目标格白挥一镐——写错名字可能毁掉那格作物/机器。
            sr = api.select(name)
            if not sr.get("ok"):
                return _with_state(
                    f"❌ 背包里没有「{name}」: {sr.get('error', '未找到')}（没放置、没挥工具、没消耗）")
            time.sleep(0.2)
        if x is not None and y is not None:
            # 🪵 地板/墙纸先对表：点错格游戏是**静默不理**，别让 AI 拿着"Cannot place"瞎试
            _decor_msg = _decor_place_check(name, int(x), int(y))
            if _decor_msg:
                return _with_state(_decor_msg)
            # 🧍 先拟人闸门 → 🚧 再占位守门。**顺序不能反**（见两道门上面的那段注释）
            _reach = _place_reach_check(name, int(x), int(y))
            if _reach:
                return _with_state(_reach)
            _occ = _place_occupied_check(name, int(x), int(y))
            if _occ:
                return _with_state(_occ)
            r = api._post("/use", {"x": int(x), "y": int(y), "force": True})
        else:
            r = api.use_item(force=True)
        if r.get("ok"):
            _it = name or r.get("item") or "物品"
            _tile = f"({x},{y})" if x is not None else "面前格"
            # ⚠️ C# 读回验证发现"原来那台被顶掉了"时会给 note —— **如实转达**，别吞掉。
            _note = r.get("note") or ""
            return _with_state(f"🪧 已放置「{_it}」@{_tile}" + (f"\n{_note}" if _note else ""))
        # 🔍 C# 读回验证抓到"游戏静默没做事"（正常被 B 挡住，这里是第二道保险）——给下一步
        if r.get("action") == "placed_noop":
            _ex = r.get("existing") or ""
            return _with_state(
                f"❌ 放置失败: {r.get('error', '未知')}\n"
                f"👉 那格已经有「{_ex}」了 —— 换一格；真想换掉它就先 ops=break kw={{x:{x}, y:{y}}}")
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
    与 NPC 对话 / 开机器 / 触发机关 / 点家具（TV·日历·壁炉·目录都行）。
    SDV 1.6 家具交互已绕过右键标志直接 checkForAction，远程就能点 TV/日历。
    ⚠️ **开不了宝箱**（2026-09-12 实测）：`Chest.checkForAction` 开头卡 `didPlayerJustRightClick`
       ⇒ 宝箱请走 **`storage` 域**（view 看 / find 找 / take 取 / store 存），别用这个。
    拿家具要用 furniture_pickup，不是这个。

    Args:
        tile_x: 目标瓦片 X 坐标
        tile_y: 目标瓦片 Y 坐标
    """
    try:
        _ensure_background()  # 开商店/锻造台等菜单前先确保不冻结
        # 🪑 交互能翻转 sitting（坐着时任意交互即起身）。**起身是异步的**：`StopSitting(animate:true)`
        #    要先 LerpPosition 0.15s、下一帧才清 `isSitting` ⇒ 交互完立刻读会读到旧的 True，
        #    状态条就骗 AI"还坐着"（恒 2026-09-11 就是这么以为"一排座位起不来"的）。
        #    所以：本来坐着 → 轮询等它真站起来再清缓存（腾不出就最多等 ~0.7s，不卡死）。
        _was_sitting = bool(((_SIT_CACHE.get("data") or {}).get("me") or {}).get("sitting"))
        r = api.interact_at(tile_x, tile_y)
        _sit_cache_clear()
        if _was_sitting:
            for _ in range(8):
                time.sleep(0.08)
                _sit_cache_clear()
                if not ((_sittable_cached(7).get("me") or {}).get("sitting")):
                    break
        if r.get("ok") and r.get("actionTriggered"):
            _mark_festival_poi_tile(tile_x, tile_y)   # 命中节日 POI 瓦片 → 记入交互历史
            what = r.get("furniture") or r.get("object") or "目标"
            return _with_state(f"🎯 与 {what} 交互成功")
        # ⚠️ `actionTriggered=false` **不等于"那儿空着"**（2026-09-12 实测）。
        #    C# 会把该格上的物体名回在 `object` 里 —— **有名字就说明有东西，只是这条路开不了**。
        #    典型=**宝箱**：`Chest.checkForAction` 开头就卡 `Game1.didPlayerJustRightClick()`
        #    （反编译 `Chest.cs:758`），程序化调用永远 false ⇒ 必挂。家具/鱼塘/蟹笼 mod 都写了
        #    专用兜底（TryFurnitureInteract/TryFishPondInteract/TryCrabPotInteract）绕开它，
        #    **唯独宝箱没写**。⚠️ C# 侧待补：加一个 `TryChestInteract` 即可（照抄同款模式）。
        _obj = r.get("object")
        if _obj:
            if "chest" in str(_obj).lower() or "宝箱" in str(_obj):
                return _with_state(
                    f"⚠️ ({tile_x},{tile_y}) 上是个**宝箱**，interact 开不了它 —— "
                    f"`Chest.checkForAction` 卡在 didPlayerJustRightClick（程序化调用必 false）。"
                    f"**改用 `storage` 域**：view 看有哪些 / find 找东西 / take 取 / store 存。"
                )
            return _with_state(
                f"⚠️ ({tile_x},{tile_y}) 上有「{_obj}」，但 interact 没触发它（**不是那里空着**）。"
                f"换条路：机器→cabin/farm 的 collect；家具→看类型走对应交互；NPC→social 域。"
            )
        return _with_state(f"⚠️ 该位置没有可交互的东西（actionTriggered=false）")
    except Exception as e:
        return _with_state(f"❌ 交互失败: {e}")


# ═══════════════════════════════════════════
#  🪑 坐椅子（2026-09-10 恒：让 AI 能坐）
# ═══════════════════════════════════════════
#  反编译定论：能坐的只有两类，都经 GameLocation.checkAction → who.BeginSitting(this)：
#    ① 家具 Furniture.GetSeatCapacity() > 0 —— furniture_type 0=chair/1=bench/2=couch/3=armchair
#       + 直立/黑钢琴（Furniture.cs:656）。
#    ② loc.mapSeats 里的 MapSeat —— Buildings 层瓦片经 Data/ChairTiles 匹配生成
#       （GameLocation.cs:1852 UpdateMapSeats）。**萨隆的凳子/桌椅走这条**，
#       不在 loc.furniture 里 ⇒ 没有 /sittable 端点就完全发现不了。
#  ⚠️ 两条硬约束（决定了 sit 必须"先就位"）：
#    · AddSittingFarmer 里 `float num = 96f` —— 玩家须距座位位置 ≤96px(1.5 格)，
#      否则返回 null、BeginSitting **静默不落座**（不报错、不弹窗）。
#    · 坐着时 GameLocation.checkAction 开头即 `if (who.IsSitting()) { StopSitting(); return true; }`
#      ⇒ **任意 interact 都是起身**（副产物）。🆕 2026-09-11 起有了正门 `scene stand`（POST /stand，
#      调的同一句 StopSitting）——**别再让 AI 猜"点哪一格够得着"**，那是个会静静失败的路子。

_SIT_HINT_KEY = {"sig": None}
_SIT_CACHE = {"t": 0.0, "radius": None, "data": None}


def _sittable(radius: int = 7) -> dict:
    """读 /sittable（每次真打 HTTP）。半径是距离玩家几格。"""
    return api.sittable(radius)


def _sit_cache_clear():
    """清 `/sittable` 的 TTL 缓存。

    ⚠️ **交互/落座/起身后必须清**（2026-09-11 实测踩到）：`_sittable_cached` 有 2s TTL，
    而 `/interact` 恰恰是能**翻转 `sitting`** 的操作（坐着→任意交互=起身；旁边→落座）。
    不清的话：刚站起来那一次调用，状态条还会写"坐着"、还把 enum 引导收着（滞后 ≤2s）。
    🆕 同理 `scene stand`（POST /stand）也翻转 sitting，且它**靠轮询**确认起身 ⇒ 每轮都要清，
    否则 2s 内每轮读到的都是同一个旧值、轮询直接失效。
    """
    _SIT_CACHE.update({"t": 0.0, "radius": None, "data": None})


def _sittable_cached(radius: int = 7) -> dict:
    """状态条专用：2s TTL 缓存——状态条每次工具调用/心跳都会重建，别每次打 HTTP。"""
    now = time.time()
    c = _SIT_CACHE
    if c["data"] is not None and c["radius"] == radius and now - c["t"] < 2.0:
        return c["data"]
    d = api.sittable(radius)
    c.update({"t": now, "radius": radius, "data": d})
    return d


def _seat_dist(px: float, py: float, seat_x: float, seat_y: float) -> float:
    """玩家格到座位位置的距离（格）。**和游戏同式**：游戏比的是
    `(seatPos+0.5)*64` 到 `who.getStandingPosition()`(=TilePoint 中心)，两边各加 0.5 抵消，
    所以这里直接用格坐标欧氏距离；阈值 1.5 格 == 游戏里的 96px。"""
    return ((px - seat_x) ** 2 + (py - seat_y) ** 2) ** 0.5


def _is_passable(x: int, y: int) -> bool:
    try:
        return bool(api._post("/passable", {"x": x, "y": y}).get("passable"))
    except Exception:
        return False


def _best_stand_tile(seat_x: float, seat_y: float, near):
    """离**座位位置**最近的可站格（4 正邻 + 4 对角，`/passable` 判，且须在 1.5 格内）。

    ⚠️ 为什么不用 `_stand_near` 那种"固定顺序取第一个"（2026-09-11 实测教训）：
    落座门槛是**距座位位置 ≤96px**，而带偏移的座位余量极小——`tall` 类座位点比格子中心高
    0.3 格，站着就只剩 ~83px/96px。更要命的是两种落位方式的像素不一样：
      · `/walk_to`（游戏寻路收尾）→ `Position=(x*64, y*64-32)` → 站立像素 `y*64-16`
      · `/position`（瞬移）        → `Position=(x*64, y*64)`    → 站立像素 `y*64+16`
    **差 16px**。于是同一个"站 (54,28)"，走过去够得着、贴过去就超 96px，而游戏**静默不落座**
    （不报错不弹窗）。⇒ 挑**离座位最近**的那格，把余量拉到最大。"""
    tx, ty = int(near[0]), int(near[1])
    best = None
    for cx, cy in ((tx, ty + 1), (tx, ty - 1), (tx + 1, ty), (tx - 1, ty),
                   (tx + 1, ty + 1), (tx - 1, ty - 1), (tx + 1, ty - 1), (tx - 1, ty + 1)):
        if not _is_passable(cx, cy):
            continue
        d = _seat_dist(cx, cy, seat_x, seat_y)
        if d <= 1.5 and (best is None or d < best[0]):
            best = (d, cx, cy)
    return None if best is None else (best[1], best[2])


def _sit_hint() -> str:
    """🪑 状态条：附近可坐物提示（恒 2026-09-10）。

    · 未坐 + 7 格（超级炸弹半径，体感）内有座位 → 「🪑 可交互：sit(x,y)」（最多 3 处）
    · 坐着 → 「🪑 坐着「名字」(x,y)｜起身 = scene stand」
    · **一排椅子只报一个坐标**：对座位格做 8 邻接聚类，每组只取离玩家最近的那一格
      （同一条长凳/沙发/一排吧台凳自然并成一个坐标）。
    · **变化才报**：签名（位置+聚类后坐标+sitting）不变就返回空串，不重复刷屏省 token。
    """
    try:
        if _OPS_INNER["n"] > 0:
            return ""          # 这层 strip 会被 _ops_run 丢掉 → 别消费签名（见 _OPS_INNER 注释）
        d = _sittable_cached(7)
        if not d.get("ok"):
            return ""
        me = d.get("me") or {}
        loc = d.get("location")
        if me.get("sitting"):
            # ⚠️ 坐着这行**不走"变化才报"**（恒 2026-09-11）：别的 enum 引导坐着时全被收掉了，
            #    这行就是**唯一指引**——按变化才报会让"坐久了只剩个光标题"，AI 不知道该干嘛。
            #    它很短，每次都报。仍更新签名：站起来时 key 变化 → 座位枚举会重新出现。
            _SIT_HINT_KEY["sig"] = ("sit", loc, me.get("seatX"), me.get("seatY"))
            # 名字直接带上（2026-09-11）：家具是"红色餐椅"，地图座椅是 seatType 原文（"bench"）。
            # 后者是**内部英文 token**——这里给 AI 看不碍事（跟 scene seats 报的名字一致），
            # 但**不能进心跳的中文句子**（那边只认 furniture，见 player_activity 坐着文案）。
            _sn = (me.get("seatName") or "").strip()
            _bit = f"「{_sn}」" if _sn else ""
            return (f"🪑 坐着{_bit}({me.get('seatX')},{me.get('seatY')})"
                    f"｜起身 = scene stand")
        tiles = {}
        for s in (d.get("seats") or []):
            if s.get("blocked") or int(s.get("free", 0)) <= 0:
                continue                                   # 被 NPC/玩家占了的座位不推荐
            tiles[(int(s["x"]), int(s["y"]))] = s
        if not tiles:
            _SIT_HINT_KEY["sig"] = None
            return ""
        # 8 邻接聚类（一排/一件多座位的只留一个，取离玩家最近的）
        seen, groups = set(), []
        for t in tiles:
            if t in seen:
                continue
            stack, grp = [t], []
            seen.add(t)
            while stack:
                cx, cy = stack.pop()
                grp.append(tiles[(cx, cy)])
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        n = (cx + dx, cy + dy)
                        if n in tiles and n not in seen:
                            seen.add(n)
                            stack.append(n)
            grp.sort(key=lambda s: s.get("dist", 0))
            groups.append(grp[0])
        groups.sort(key=lambda s: s.get("dist", 0))
        groups = groups[:3]
        key = ("stand", loc, tuple((g["x"], g["y"]) for g in groups))
        if key == _SIT_HINT_KEY["sig"]:
            return ""
        _SIT_HINT_KEY["sig"] = key
        coords = " ".join(f"sit({g['x']},{g['y']})" for g in groups)
        return f"🪑 可交互：{coords}"
    except Exception:
        return ""


@mcp.tool()
def sit(x: int, y: int, face: Optional[int] = None) -> str:
    """🪑 坐到 (x,y) 的椅子/长凳/沙发/钢琴（scene 域；别名 坐）

    **自动就位**：游戏要求玩家站座位 1.5 格内才落座（否则静默失败），所以本 op 会
    自己先走到座位旁 → 面朝 → interact → 回读确认，坐不上就明确报错、不谎报成功。
    坐标从状态条的「🪑 可交互：sit(x,y)」或 scene seats 拿。
    想起来：**scene stand**（2026-09-11 新增的主动起身）。

    Args:
        x, y: 座位格（就是状态条里 sit(x,y) 的坐标）
        face: 坐下后的**朝向**（0上/1右/2下/3左）；不传=面朝座位（默认）。
            ⚠️ **不是所有座位都吃 face**——只有朝向"来自坐下那一刻面朝方向"的才吃：
            反编译 `MapSeat.AddSittingFarmer`(MapSeat.cs:317-334) 的 `stool` 类 / `direction==-2`
            （数据里的 "opposite"，如长椅）；家具里 `Name` 含 "Stool" 的也是（Furniture.cs:712）。
            其它座位朝向写死，传了不生效。
            ✅ **吃不吃由 /sittable 的 `seat["face"]` 直接告诉我们**（判据在 C# 里照抄游戏），
            本 op 不再自己猜名字——不生效时会在回报里点名说明。
    """
    _face = None
    if face is not None:
        try:
            _face = int(face)
        except (TypeError, ValueError):
            return _with_state(f"❌ face 要 0上/1右/2下/3左，收到「{face}」")
        if _face not in (0, 1, 2, 3):
            return _with_state(f"❌ face 要 0上/1右/2下/3左，收到「{face}」")
    try:
        d = _sittable(30)                    # 半径给足：先判目标是不是座位，再谈走位
        if not d.get("ok"):
            return _with_state(f"❌ 查可坐物失败: {d.get('error', '')}")
        me = d.get("me") or {}
        if me.get("sitting"):
            return _with_state(f"⚠️ 已经坐在 ({me.get('seatX')},{me.get('seatY')}) 上了"
                               f"——要走动先 scene stand 起身")
        tgt = next((s for s in (d.get("seats") or [])
                    if int(s["x"]) == int(x) and int(s["y"]) == int(y)), None)
        if tgt is None:
            near = " ".join(f"({s['x']},{s['y']})" for s in
                            sorted(d.get("seats") or [], key=lambda s: s.get("dist", 0))[:6])
            return _with_state(f"❌ ({x},{y}) 不是可坐物"
                               f"（30 格内可坐格: {near or '无'}）——用 scene seats 扫一圈")
        if tgt.get("blocked") or int(tgt.get("free", 0)) <= 0:
            return _with_state(f"❌ ({x},{y})「{tgt.get('name')}」被占了（空{tgt.get('free')}/{tgt.get('capacity')}），坐不了")
        loc = d.get("location") or ""
        seat_x, seat_y = float(tgt.get("seatX", x)), float(tgt.get("seatY", y))
        name = tgt.get("name") or "座位"
        walked = ""

        # 🪑 坐下前的朝向：传了 face 就用它（吃 face 的座位会照抄），否则面朝座位。
        # ⚠️ 必须**紧挨着 interact** 设，游戏是在 `AddSittingFarmer` 里读"此刻的 FacingDirection"。
        # ⚠️ 2026-09-11：吃不吃 face 由 **C# 端点算好回给我们**（`seat["face"]`，判据照抄游戏：
        #    Furniture.cs:712 的 `Name.Contains("Stool")` / MapSeat.cs:317-334 的 stool 与 opposite）。
        #    **别再自己拿名字猜**——原先判 `name.lower().startswith("stool")`，可家具的 `name` 是
        #    **本地化 DisplayName**（中文环境=「凳子」）⇒ 永远不匹配、必误报；而且 Contains≠StartsWith。
        #    老 DLL 没这个字段 → None → 照样警告（不假装生效，方向是安全的）。
        _face_note = ""
        if _face is not None and not tgt.get("face"):
            _k = "地图座椅" if tgt.get("kind") == "map" else "家具"
            _face_note = f"（⚠️「{name}」是{_k}，朝向写死，face 不生效）"

        def _face_before_sit():
            if _face is not None:
                api.face(_face)
            else:
                api.face(api.face_toward(int(x), int(y)))

        if _seat_dist(me.get("x"), me.get("y"), seat_x, seat_y) > 1.5:
            # 走到**离座位最近**的可站格（余量最大；见 _best_stand_tile 的 16px 教训）。
            # 都不行就看座位格自己站不站得住。
            cand = (_best_stand_tile(seat_x, seat_y, (int(x), int(y)))
                    or _stand_near(int(x), int(y))
                    or ((int(x), int(y)) if _is_passable(int(x), int(y)) else None))
            if cand is None:
                return _with_state(f"❌ ({x},{y}) 四周没有能站进去的格，到不了椅子旁")
            api._post("/walk_to", {"location": loc, "x": cand[0], "y": cand[1]})
            _wait_arrival(loc, cand[0], cand[1], timeout=20)
            p = _ai_pos()
            if _seat_dist(p[0], p[1], seat_x, seat_y) > 1.5:
                # 落偏了 → 精确贴到邻格（break_tile 同款：够不着就 /position 补一下）
                api.position(cand[0], cand[1])
                time.sleep(0.25)
                p = _ai_pos()
                if _seat_dist(p[0], p[1], seat_x, seat_y) > 1.5:
                    return _with_state(f"❌ 走不到 ({x},{y}) 旁边（现在 {p}），离座位太远坐不上")
            walked = f"（已走到 {p} 旁）"
        _face_before_sit()
        time.sleep(0.12)
        r = api.interact_at(int(x), int(y))
        _sit_cache_clear()   # 🪑 落座/没落座都可能改了 sitting → 清缓存，别让状态条滞后
        time.sleep(0.4)
        if (_sittable(30).get("me") or {}).get("sitting"):
            return _with_state(f"🪑 坐上「{name}」({x},{y}){walked}{_face_note}"
                               f"——要走动 scene stand 起身")
        # 🔁 没坐上一击 → 重贴到余量最大的站格再试一次。
        #    门槛 96px 对带偏移的座位余量极小，落位方式差 16px 就够不着（见 _best_stand_tile），
        #    而游戏是**静默不落座**——不重试的话 AI 只会看到一句"没坐上"却不知所以。
        best = _best_stand_tile(seat_x, seat_y, (int(x), int(y)))
        p2 = _ai_pos()
        if best and best != p2:
            api.position(best[0], best[1])
            time.sleep(0.3)
            _face_before_sit()
            time.sleep(0.12)
            r = api.interact_at(int(x), int(y))
            _sit_cache_clear()   # 🪑 同上：重试这一击也可能改了 sitting
            time.sleep(0.4)
            if (_sittable(30).get("me") or {}).get("sitting"):
                return _with_state(f"🪑 坐上「{name}」({x},{y})（重贴到 {best} 才够着）{_face_note}"
                                   f"——要走动 scene stand 起身")
        return _with_state(f"❌ 没坐上（actionTriggered={r.get('actionTriggered')}）"
                           f"——({x},{y}) 可能不是座位/被挡住/离太远")
    except Exception as e:
        return _with_state(f"❌ 坐椅子出错: {e}")


@mcp.tool()
def seats(radius: int = 12) -> str:
    """🪑 扫附近能坐的东西（椅子/长凳/沙发/钢琴/地图座椅；scene 域；别名 座位）

    状态条的「🪑 可交互：sit(x,y)」只在 7 格内出现一次（变化才报），想看全一点就用这个。
    返回每处可坐点的名字/坐格/空位数/距离。坐下用 scene sit(x,y)。

    Args:
        radius: 扫几格内（默认 12）
    """
    try:
        r = int(radius)
        d = _sittable(r)
        if not d.get("ok"):
            return _with_state(f"❌ 扫可坐物失败: {d.get('error', '')}")
        me = d.get("me") or {}
        ss = d.get("seats") or []
        head = f"🪑 {d.get('location')} 附近 {len(ss)} 个可坐点（{r} 格内"
        if me.get("sitting"):
            head += f"；现在正坐着({me.get('seatX')},{me.get('seatY')})"
        head += "）："
        if not ss:
            return _with_state(head + "无——若屋里摆了椅子，用 scene furniture 看家具表")
        ss.sort(key=lambda s: s.get("dist", 0))
        lines = [head]
        for s in ss[:12]:
            who = "🪑" if s.get("kind") == "furniture" else "🪑🗺️"
            occ = "【占】" if (s.get("blocked") or int(s.get("free", 0)) <= 0) else ""
            # ✋ 吃 sit(face=…) 的座位标出来（C# 算好的 seat["face"]，别在这重算）
            fx = " ✋可改朝向" if s.get("face") else ""
            lines.append(f"{who} {s.get('name')} 坐({s.get('x')},{s.get('y')}) "
                         f"空{s.get('free')}/{s.get('capacity')}{occ} 距{s.get('dist')}{fx}")
        if len(ss) > 12:
            lines.append(f"… 另 {len(ss) - 12} 个（缩 radius 或就近坐）")
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ 扫可坐物出错: {e}")


@mcp.tool()
def stand() -> str:
    """🪑 从椅子上站起来（scene 域；别名 起身）

    坐着的时候才能用；**没坐着会明确报错**，不假装成功。
    调的是游戏自家那条起身路径（`StopSitting()`，带动画+音效，跟玩家自己点起身一样）。

    ⚠️ 起身**不是瞬时的**：游戏要等起身动画的 lerp 收尾才真的解除坐姿（约 0.3~0.5s），
    所以本 op 会轮询确认后再回话，确认不了就**如实说**（别当失败硬重试）。
    """
    try:
        if not ((_sittable(7).get("me") or {}).get("sitting")):
            return _with_state("⚠️ 没在坐着，无需起身")
        r = api.stand()
        if not r.get("ok"):
            return _with_state(f"❌ 起身失败: {r.get('error', '')}")
        _sit_cache_clear()
        # 🔁 轮询确认（StopSitting(animate:true) 只置 isStopSitting，下一帧 lerp 收尾才清 isSitting）。
        #    ⚠️ 用前先 `_sit_cache_clear()`：`_sittable_cached` 有 2s TTL，不清就会 2 秒里读同一个旧值。
        for _ in range(8):
            time.sleep(0.1)
            _sit_cache_clear()
            if not ((_sittable_cached(7).get("me") or {}).get("sitting")):
                p = _ai_pos()
                return _with_state(f"🪑 站起来了（现在 ({p[0]},{p[1]})）")
        return _with_state("⚠️ 已发起身指令但 0.8s 内仍读到坐着——动画没放完？再调一次 scene stand")
    except Exception as e:
        return _with_state(f"❌ 起身出错: {e}")


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


# ── 发型编号索引（2026-09-08 反编译定论，替代旧的"男发型描述"）──
# 真相（Farmer.GetAllHairstyleIndices() + hairstyles.xnb 128×672 + Data/HairData）：
#   捏人页能选的全集 = [0,1,...,55] + [100,101,...,117] = 56 个基础发型 + 18 个 1.6 新增，共 74 款。
#   菜单左/右箭头循环的就是这 74 个；**显示编号 = 索引位置+1**，内部 farmer.hair / changeHairStyle 存的是列表值。
#   ⚠️ 不能按"显示-1"算内部——显示 57~74 对应内部 100~117（中间 56~99 是空号，实际不存在）。
#   set_appearance(hair=N) 的 N 是显示编号 → 内部 = HAIR_REF[N-1]。
HAIR_REF = list(range(0, 56)) + list(range(100, 118))


@mcp.tool()
def list_hair_ref() -> str:
    """💇 发型编号参考（捏人页显示编号 → 内部样式）
    捏人页能选的 74 款发型，按菜单循环顺序列给你（显示第几号 ↔ 内部 farmer.hair 存的值）。
    ‼️ 显示编号 57~74 对应内部 100~117——**内部 ≠ 显示-1**（56~99 是空号，实际不存在）。
    set_appearance(hair=N) 会自动换算（给显示编号即可），这里只是让你脑内有个数。
    看中哪号 → set_appearance(hair=<显示编号>)（每次改完附小人截图，挑到满意为止）。
    """
    lines = ["💇 发型编号参考（显示编号 → 内部样式）:\n"]
    for i, hid in enumerate(HAIR_REF, 1):
        lines.append(f"  {i:2d}. 内部{hid:3d}")
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
    发型: 1~74 显示编号（捏人页菜单顺序；内部 farmer.hair 存的是 HAIR_REF[显示-1]，显示 57~74 对应内部 100~117 ≠ 显示-1；2026-09-08 反编译定论）

    Args:
        hair: 发型显示编号 1~74（list_hair_ref 同编号；57~74 对应内部 100~117）
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
            # ⚠️ 2026-09-08 反编译定论：hair 是"捏人页显示编号"(1~74) → 用 HAIR_REF 转内部 farmer.hair。
            #   不能再"显示-1"当内部——显示 57~74 对应内部 100~117（56~99 是空号）。
            hidx = int(hair)
            if 1 <= hidx <= len(HAIR_REF):
                kwargs["hair"] = HAIR_REF[hidx - 1]
            else:
                return _with_state(f"❌ 发型显示编号必须 1~{len(HAIR_REF)}（共{len(HAIR_REF)}款），收到 {hidx}。用 list_hair_ref() 看编号。")
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
    internal_hair = (d.get("hair") or {}).get("index", "?")
    display_hair = (HAIR_REF.index(internal_hair) + 1) if isinstance(internal_hair, int) and internal_hair in HAIR_REF else "?"
    eye = (d.get("newEyeColor") or {}).get("hex", "?")
    skin = (d.get("skin") or {}).get("index", "?")
    acc = (d.get("accessory") or {}).get("index", "?")
    shirt = (d.get("shirt") or {}).get("name", "?")
    pants = (d.get("pants") or {}).get("name", "?")
    _look_verified = True
    return _with_state(
        "🔍 捏人形象核对（满意再 ok）：\n"
        f"  🧑 名字: {d.get('name')!r} | 喜爱: {d.get('favoriteThing')!r}\n"
        f"  💇 发型: 内部{internal_hair}(=显示第{display_hair}号) | 瞳色: {eye} | 肤色: {skin} | 配饰: {acc}\n"
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
"check": "查询域，what=...：status(完整状态) backpack(逐格价值/星级) worn(穿戴) machines(全场机器清单) mine(下矿进度) silo(干草) mastery(精通) buildings(木匠建筑) quest(开任务日志) chests(当前图箱) storage(箱子网络) look(环视周围) profile(我的技能等级+职业分支,如是否 Luremaster 蟹笼免饵) role(端口↔角色确认:我是谁/恒是谁)。⚠️查概览用 status，查逐格用 backpack，别都调浪费 token。📌profile/role 2026-09-11 从顶层工具收编进来（原来直接叫 profile()/which_role()，现在一律走 check）。📐这个域的参数叫 **what**，**不是 ops**。带参的只有两个: chests(chest=N 看第N个箱) / look(radius=10 环视半径)；其余(status/backpack/worn/machines/mine/silo/mastery/buildings/quest/storage/profile/role)**全无参**。⚠️查概览用 status、查逐格用 backpack，别都调一遍浪费 token。",
"farm": "农活域(🌱必走，别手动挥工具组合，用域 ops)：till(锄地) plant(种,跳过已种;带 layout 就按洒水器布局种) water(浇,自动跳雨+水壶没水先装满) harvest(收) scythe(镰刀收蒜/花/茶) fertilize(化肥) clear(清杂草石树桩;坐标同 till，也可 `radius=N` 走**圆形**；**会自动往外多清 2 格**——田边的杂草会长进田里把作物顶掉，不用自己放大) plot(连通域规划) plan(方形规划,纯算格) chop(砍树)。⚠️**锄地/播种各只有一个实现**：锄地=`till`（`hoe`/`布局锄`/`tillfield`/`蓄力锄` 都是它的别名）、播种=`plant`（`sow`/`plantlayout`/`播种规划` 都是它的别名）；⛔ `till_plant` 已退役——要锄+种写 `ops=\"till plant\"`。 clearground(清单格) collect(一键收机器:只收不放,全农场瞬收不走路) load(放原料) building(一屋收放:拟人走进去收+放料,item留空=只收不放) break(拆/敲同scene,镐子敲可破物/翻已耕地) place(放置/播种同scene) pond/pond_add/pond_feed/pond_collect/pond_fish(鱼塘)。⚠️漏格DLL自动补；**缺的格会被点名「被什么挡着」**（🌿草/杂草/🪨石头/🪵树枝→**先 `clear` 清一遍再 till/plant**；🏗️洒水器/箱子等设施→⛔别清、那是规划该绕开的格）——草占着地格时锄头是锄不出 HoeDirt 的，别对着「缺失N格」发呆；高级工具蓄力用 tool_area(别用/tool)。只在 Farm/温室/姜岛。带参 op(plant 的 seed_name、till/clear 的 x/y/rows、fertilize 的 fertilizer_name、place 的 name、building 的 location、collect 的 machine_type、pond_add 的 item)→ kw={'参数名':值}。🐄动物(2026-09-02 care域并入farm): animals(摸+收) 喂水/碗(宠物水,🌧️雨天自动跳过——雨会把碗填满) milk(挤奶剪毛) buy(买动物,豁免建议) doors(关门) hay(干草) pet(猫狗) petwalk(拟人摸) 畜舍/这间(这间屋动物) statue(祈福)——⚠️farm water=浇地,动物水用 喂水; farm building=机器收放,这屋动物用 畜舍。📐参数键名: till/clear/plant/fertilize 都是 x,y(**必填**),rows,length,direction（till/plant 也可用 x1,y1,x2,y2 直接给矩形两角）；plant 另有 seed_name,layout,direct,trellis；harvest/scythe=radius；plot=x,y,radius,all_plots；chop=area(**值写几个数**：4 个数=矩形两角 / 3 个数=圆心+半径，逗号空格都认)；collect=machine_type,location；load=item,machine_type,location；building=location,item,machine_type；place=name,x,y；break=x,y,steps,radius；pond_add=item+x,y（pond_feed/collect/fish 只要 x,y）；buy=animal_type,name,building；petwalk=include_petted；hay=dry_run。⚠️direction 只认 horizontal(默认)/vertical 两个值,别写'横'/'竖'。💡大田洒水器布局(可选,纯自动化建议)：要按洒水器留格/留走道就 plan→till→plant 三件套——plan(x1,y1,x2,y2,layout=0,hoe_level=-1,trellis=False) **纯算格不动机器**先看要锄/种哪些; till(x1,y1,x2,y2,layout=0) 按布局锄; plant(x1,y1,x2,y2,seed_name,**layout**) 按布局种（layout=0 整块/1 初级十字/2 高级/3 铱；direct=True 瞬移快、默认走位拟人）。想一次说完就 `ops=\"till plant\"`（**一份 kw 共用**，锄地会自动点名忽略 seed_name）。**layout 四档**: 0=标准整块(不预留洒水器,锄法蛇形逐格走位,任何锄头等级都行) 1=初级(十字稀疏,每台覆盖上下左右4格;锄法=精确锄每台4格,**与锄头等级无关**) 2=高级(优质,田宽高先裁成**3的倍数**,每3×3中心1台覆盖8格,整块蓄力锄) 3=铱(裁成**5的倍数**,每5×5中心1台覆盖24格;⚠️爬架作物不适用)。hoe_level: 0→1格 1→3线 2→5线 3→3×3 4→6×3,-1=自动读手持。trellis=True=爬架作物(啤酒花/青豆/葡萄,不可通过格)⇒自动**种2留1**留走道让AI能进田浇收。只管种不摆洒水器就直接 plant,不用 plan 那套。⚠️已知限制: layout 0/2/3 碰上金/铱锄(hoe_level>=3)会报**0处锄地站位**并自打一行'落点未实测校准,暂不规划蓄力站位'——**那是刻意不猜不是出错**; layout 1 不吃蓄力站位不受影响。",
"mine": "下矿域(⚒️ 矿井/头骨/火山)：go(去挖矿:mode=rush冲层/farm刷矿,start起始层,target目标层,ore,cycles圈数) progress(进度) bomb_status/bomb_plan/bomb_place/bomb_collect/bomb_ladder/bomb_retreat(单步炸,**都要 bomb_ 前缀**) bomb_mine(自动) bomb_volcano(火山) organize(整理背包)。🔁**刷矿=mode=go(mode=farm)**：定点刷指定矿→ore=Copper铜(21层)/Iron铁(41层)/Gold金(71层)；**煤靠 farm 铁层(41)顺手清尘埃精灵/蝙蝠掉**（不是 ore 选项，跑 auto 内部刷）。🏃下矿=mode=go(mode=rush,start可选≤电梯上限+5倍数,target默认120)。⚠️无镐/血低硬拦；梯子 /ladder+confirm。⚠️bomb_mine 没炸弹+host在同矿井→自动转【内部】协同(跟随host+帮忙敲矿/打怪)不撤退出矿(bomb_escort 不对外暴露、AI 不主动启用)；bomb_retreat 结束协同+停脚本+脱离矿井回门口。⚠️接「深处的危险」重置电梯→起始层动态从1起(内置脚本自动读，不暴露工具)；刷矿目标层不可直达会上报，需先冲层带回或改浅层。📐带参速查: go(mode=rush冲层/farm刷矿, start起始层, target目标层, ore=Copper铜/Iron铁/Gold金, cycles圈数, hp_threshold, food_sta, food_hp, resume) bomb_plan(radius,min_covered,top) bomb_place(x,y **必填**) bomb_collect(max_items) bomb_mine(target,bomb,min_covered,follow_host,lead,autodrop,one_floor) bomb_volcano(bomb,min_covered,hp_threshold,max_minutes,poll) organize(disable,reset)。💣bomb 三个取值 'Cherry Bomb'樱桃/'Bomb'黑/'Mega Bomb'超级——**点名的包里没有就按 黑>超级>樱桃 自动换成有的**(不会误报没炸弹)；范围 樱桃=边长7十字 / 黑=11x11方块 / 超级=15x15方块，⚠️黑和超级**会炸伤自己**(实测黑掉3血)。⚠️bomb_volcano **要求 host 已在矿/火山里**才放行(火山瓦片没法程序化换层)。⚠️bomb_mine one_floor=True=逐层模式(同步,只跑一层出摘要,不撤退)；**默认冲层模式=异步后台跑,推荐**。💡出发前占位物(恒2026-08-23)：提前放1个可堆叠物(铱矿/铱锭/五彩碎片)在包，满包时同种战利品自动堆叠吸附、少触发满包停；别拿银河之魂这类带死亡会丢的稀有物当占位。",
"cabin": "小屋引导域(🏠 FarmHouse/Cabin/岛屋；不传=扫屋)：enum(扫**本屋**查待收) collect(收**本屋**机器;要全农场→farm collect) statue(雕像) furniture(扫家具) interact(点家具,tile_x/tile_y) pickup(拿起家具,tile_x/tile_y) cook(做饭,recipe_name) sleep(睡觉,**who=谁床必填**：传自己名=睡自己床,传别人名=睡那个人的床/一起睡；不在那栋屋会自动走过去；🏝️姜岛例外=共用小屋大通铺) cook(做饭,recipe_name,count) place/break(同scene) decor(🪵**地板/墙纸真值表**——这屋哪些格能铺+现在铺的什么,**铺之前先查这**;铺地板点**地板格**、铺墙纸点**靠墙那圈墙格**,点错游戏**静默不理**)。📐参数键名: interact/pickup=**tile_x,tile_y(不是x,y)** cook=recipe_name,count sleep=who place=name,x,y break=x,y,steps,radius；enum/collect/statue/furniture/decor 无参。kw={'参数名':值}。",
"social": "社交域：chat(搭话,name=NPC名) gift(送礼,npc_name/item_name) give(送玩家物品,手持右键正式赠予,一次一个要等同意) hand(递给玩家,走过去丢他脚边,磁吸自动收,可整叠) send(发消息,message) emote(表情,name) friendship(查好感,npc_name) movie(影院,npc)。📐参数键名: chat=name / gift=npc_name+item_name / give=player_name+item_name / hand=player_name+item_name+count(0=整叠) / send=message / emote=name(默认爱心) / friendship=name / movie=npc。⚠️**give vs hand**：give=面对面正式赠予(手持右键,一次一个)——**它发的是「赠送提议」,对方点同意东西才过去**(没点会退回;回报会明说「等他点同意」,看到这句别当成已经送到)；hand=走过去丢他脚边(磁吸自动收,**可整叠**,不用对方操作)——想整叠给/对方不在手边就用 hand。kw={'参数名':值}。",
"scene": "场景交互域(点东西/工具/转身/捡/坐)：at(tile_x,tile_y)(点指定格/柜台) interact(点面前) use(挥工具) face(转向0上1右2下3左) select(拿手上) sit(x,y[,face])(**坐椅子**:自动走到座位旁再坐,上不了会明确报错;状态条「🪑 可交互：sit(x,y)」给坐标;可选 face=坐下朝向0上1右2下3左,**只对「朝向来自坐下那刻面朝方向」的座位生效**(反编译:stool 类/opposite 长椅/名字带Stool的家具),其它写死——吃不吃由端点回的 face 字段说了算,不生效会在回报里点名) stand(**起身**:坐着时用,没坐着明确报错,带动画+轮询确认) seats(radius=12)(扫附近能坐的椅子/长凳/沙发,✋=可改朝向) pickup(拿起家具) pickup_scene(捡当前场景物) berry(摇浆果) spot(挖蚯蚓点) moss(绿雨搜苔藓) rock(室外镐击:敲当前图可破物,采石场/挖掘场/蚌矿场跳普通石,dig/dry,battle-free) garbage(翻垃圾桶) forge_help(锻造攻略) drop(丢物:一种 name+count / 多种 items=逗号分隔) decor(🪵地板/墙纸真值表:这间屋哪些格能铺+现在铺的什么,**铺前先查这**) furniture(扫家具) place(放置/播种:name=物品名,x/y=目标格→箱子/树种/蟹笼落地或种下,只放可放置物;🪵**地板/墙纸是特例**——只能点在**地板格**(地板)/**靠墙那圈墙格**(墙纸)上,点错游戏**静默不理**;点错时回报会直接告诉你「这格其实是墙不是地板」并给出能铺的格) break(拆/敲:x,y=目标格,steps=挥击次,radius=方圆→镐子敲石头/翻已耕地,跳过箱子/容器格) maze(迷宫视图r半径,gx/gy目标格→ASCII棋盘#墙.可走P自己G目标) maze_seg(走法链gx,gy目标→拆直走廊列表+拼「左/右上/下走到(x,y)」多段链,AI按段walk_to) maze_walk(走迷宫 waypoints=「x,y x,y…」依次walk_to;⚠️**它其实是通用多段走位,主门牌已挪到 `map walk_multi/闲逛`**(闲逛遛弯/绕人转圈/泳池绕圈游),此处保留旧名为兼容) pan(淘金/淘盘:本图水下闪光点→岸边走位面水→铜锅淘金收掉落) front/rummage(分别是interact/garbage的别名)。📌**坐着想起来：scene stand**（2026-09-11 起有正门，别再拿 at 猜一个够得着的格子——那条路会静静失败）。📐带参速查(键名必须=下面这些,**写错会被静默丢掉、不报错**): at(tile_x,tile_y) **⚠️是 tile_x/tile_y 不是 x/y** / pickup(tile_x,tile_y **同 at 用 tile_**) / use(name) / face(direction 0上1右2下3左) / select(name) / sit(x,y,face) / seats(radius=12) / pickup_scene(max_items=30) / moss(radius,target_max,rounds,dry_run) / rock(dig,radius,max_break,break_stone) / garbage(loc,pos,wait,dry_run) / pan(dry_run,radius,timeout) / drop(name,count,items=多种一起丢) / place(name,x,y) / decor(无参) / break(x,y,steps,radius) / maze(radius,gx,gy) / maze_seg(gx,gy,radius) / maze_walk(waypoints,location,max_wait,max_seg)。kw={'参数名':值}。",
"menu": "菜单/界面域(开→看→点)：read(看菜单) advance(推进剧情/对话,一句句) **skip(整段跳过剧情/事件,事件 skippable=true 才跳得动)** click(option/item/button/xy 点;action=claim领/action=discard丢桶腾格;slot=序号领指定格) key(ok/esc/数字按键) cancel(关弹窗/撤就绪) shop(逛店) sell(卖商店) bin(投出货箱) craft(合成) recipes(菜谱) craftables(配方) forge(锻造) geode/geodes(砸晶球) customize(捏人) bundle(献祭缺口·**只读存档不走路**) bundle_kb(献祭知识库) donate(捐赠博物馆) read_book(读消耗品:书/秘密纸条/日记残页,统一走右键读 name=物品名) levelup_choose(技能升级职业选择 5/10级:不带参读左右选项,side=left/right 或 profession=职业id 定分支;普通升级自会确认OK) number(数量输入:展览会兑换台/转盘押注 NumberSelectionMenu) minigame(赌场小游戏点按钮 action=hit/stand/bet10/…) minigame_state(读牌面/转盘) display_fill(农展台放满 items='钻石,山羊奶酪') display_takeback(收好) journal(开任务日志→menu read 读卡,翻页=click(button=forward/back),领奖励=click(button=rewardBox)) know(查特别订单详情/知识库SPECIAL_ORDERS,如menu know 岛屿食材;2026-09-02 task域退役并入menu)。📐参数键名: click=option,button,x,y,item,right,quantity,action,real,slot,category(**action=claim领 / discard丢桶腾格**;button 用按钮名 ok/upperRightCloseButton/forward/back/rewardBox/mainButton) / key=key,count,hold / number=value,confirm / shop=place,want / sell=name,count(-1=全卖) / bin=name,sell_all / craft=item_name,count / forge=item1,item2,mode,target / geodes=count / customize=name,farmname,favorite / bundle=area / bundle_kb=query / read_book=name / levelup_choose=side,profession(**不带参=只读当前左右选项**,供配 check(what=profile) 分析后再决定) / minigame=action,x,y / display_fill=items。⚠️cook(做饭)**不在 menu 在 cabin**。🚫满包接鱼/领箱:原 claim_swap(替换领取)已退役→**click action=discard 丢桶腾格(回收返金)+action=claim 领取(或用 slot 领指定格;不想要直接 button=ok 关掉)**。🧾关闭菜单一律 click(button=upperRightCloseButton)（ItemGrabMenu/交付容器用 button=ok 确认才关）；订单交付容器(QuestContainerMenu)=点背包对应物品格(见slots的坐标)→放进→点 button=ok 结算；任务日志领钱=点击已完成的有钱任务卡后 click(button=rewardBox)；兑奖机兑换=click(button=mainButton)；特别订单领奖链=日志领钱(上面)→社区板旁领奖箱(60,93)拿兑奖券→刘易斯家兑奖机(mainButton)兑换。",
"storage": "箱子域：view(看箱,box=N看单箱全清单) store(存:what/items限定存哪些,名可带xN数量只存那N份,留空=归位只存已有同类堆,target指定箱/all=True全存腾空间) take(取:x,y+name单箱 或 items批量) find(模糊查哪箱有某物) default(设/清默认箱 clear=清) tag(改名,可带color改色)。📐参数键名(view=box / store=what,items,target,keepTools默认True,all / take=items 或 x+y+name+count默认999 / find=name / default=x,y,clear / tag=tag,target**必填**,color)。🤖存取统一走位：store/take都会先走到相关箱旁(批量只走到第一个),不区分拟人/原子,别靠编号逐箱翻。⭐每个箱子前自动带【类目标签】(内容过半归类):矿/古物/鱼/种子/作物/农产/建材/料理/装备——AI按标签定位箱,找东西用find。⚠️改色别染纯#000000(=默认木纹,识别成未染色);要黑箱用暗灰#303030。",
"daily": "过日子域：sleep(睡觉) eat(吃食物回血体力,name/item_name) wear(穿/脱衣物,name/slot/hand) lie_bed(躺床不过夜) settle(确认过夜结算) heartbeat(心跳间隔,minutes) pause(后台不暂停,out_of_focus) peek(看恒干嘛) whiteboard(写白板,content) wb_read/wb_pin/wb_clear。📐参数键名: sleep/lie_bed=who eat=name,item_name wear=name,slot,hand(**hand 仅戒指**:1/left 或 2/right,或传「要换掉的那枚戒指名」自动找手) heartbeat=minutes pause=out_of_focus whiteboard/wb_pin=content appearance=hair,hair_color,skin,shirt,pants,hat,acc,eye_color,pants_color；settle/peek/wb_read/wb_clear 无参。kw={'参数名':值}。📌sleep/lie_bed 的 who **必填**（名字随存档变，现读现传）：传自己名字=睡自己床；传别人名字=睡那个人的床(一起睡+🌹彩蛋)。⚠️名字写错会报错并列出可选名(不会默默睡成别人的床)。**传对名字就不用先回家**——不在那栋屋会自动走过去(map_go跨图→门口→推门→床边，全程走)。lie_bed 只躺不睡，想离开随时 walk_to 走离床格即可。🏝️**在姜岛是另一套**：岛上共用一间小屋(大通铺)，没有「谁的床」——who 传**正躺在床上的别人**=挤他那张(姜岛版爬床彩蛋)；否则(传自己/那人还没躺)=随便挑一张空床安静睡。⚠️睡别人床/协作前先 check(what=\"role\") 确认端口↔角色（端口按启动顺序分配，重启可能翻转，认错角色=挪了恒的人）。",
"map": "导航域(🗺️跨图唯一入口)：lookup(查地点功能+出口) query(功能反查) go(走到目标/多段寻路+交通) walk(走到POI **或给x,y走同图坐标**) walk_multi(多段走位:喂一串坐标依次走) npc(找NPC) warp_safe(紧急逃脱)。⚠️出口走出口前一格；交通图腾柱>矿车>走路。📐参数全放kw对象(**别拼进ops串**,键名: go=destination地点名/POI 或 npc=NPC名(二选一)、walk=poi_name 或 x+y(二选一,坐标=只走同图;跨图用go)、walk_multi=waypoints(\"x,y x,y …\"空格/分号分隔),location,max_wait,max_seg、npc=name、lookup=location、query=function、warp_safe 无参)。⚠️walk 到 POI 会**自动应用结构化站位+朝向**(水池朝右/柜台朝上),但交互仍要 AI 自己 scene at/interact 触发。🫧walk_multi 别名 **闲逛/多段走**（旧名 festival/scene 的 maze_walk/走迷宫 仍可用）：正事=万灵节迷宫按段走；**活人感**=闲逛遛弯·绕着人转圈示好·浴场泳池绕圈游。",
"festival": "节日域(🎪)：today(今天节日) next(下一个) go(去) info(实况) interact(互动) answer(应答) shop(节日商店) eggs(找蛋) egg_note(纸条) egg_run(捡蛋) dance(跳舞邀请) strength(力量测试 delay=毫秒) ice_fish(冰雪节冰钓自动化) help(玩法) prep(备战) poi(限定点) maze(迷宫坐标奇偶年) maze_walk(走迷宫 waypoints=「x,y x,y…」依次walk_to;⚠️**通用多段走位已搬到 `map walk_multi/闲逛`**,此处保留旧名兼容) strength(力量测试,delay=毫秒) display_fill/display_takeback(农展台放满/收好)。📐参数键名: interact=name answer=answer egg_run/egg_note=route dance=target strength=delay maze_walk=waypoints,location,max_wait,max_seg display_fill=items；today/next/go/info/shop/eggs/help/prep/poi/maze/ice_fish/display_takeback 无参。",
"fish": "钓鱼域(🎣 2026-08-22修复)：go(去钓 location=) info(查某地鱼) spots(钓点) bobber(浮漂样式) rod(鱼竿:看/上饵钓具 item=名) crab(蟹笼总览) crab_water(找水) crab_place(放笼) crab_bait(放饵) crab_collect(收笼) crab_diag(诊断笼/定位挂饵) crab_retract(回收笼/清搁浅 location=可选)。⚠️**crab_bait/crab_collect/crab_retract 不带坐标 = 处理「当前图**全部**」的笼**(不是附近几个;一天真机在海滩 32 只被一次收光)——只想动一只就传 x+y。📐参数键名: go=location(None=**就地钓**,须自己已站到水边;指定 Beach/Mountain/Forest/Town=先 map_go 走真实路径到校准钓点再钓,不是warp),max_casts(0=不限),no_sleep(True) / info=location / bobber=style(默认dice) / rod=action+item / crab_place=count+radius+bait / crab_bait=bait / crab_water=radius / crab_diag=location / crab_retract=x+y+location。⚠️鱼塘在 farm 域不在 fish。带参 op(go 的 location、rod 的 item、crab 的 count)→ kw={'参数名':值}。🧬**挂饵前先 check(what=\"profile\")**：若是 Luremaster(职业11) 蟹笼免饵，crab_bait/crab_place 挂饵是空操作，别浪费。",
"settings": "系统/设置域(⚙️ 合并捏脸进来)：status(看所有设置+退役工具) retire(退役工具) reactivate(召回) appearance(捏脸) customize(捏人) **confirm_look(核对捏人形象,ok前必做)** color(颜色条) hair/shirt/pants/hat/colorpreset(外观参考)。⚠️捏脸=创建定型:ok后set_appearance/捏人自动退役(不可逆);旧配置 settings(setting='async', value='on') 仍可。🪓**砍树放行**：settings(setting='chop', value='蘑菇树,桃花心木') 放行特殊树种（默认只砍橡/枫/松；none 收回 / all 全放行慎用；不传 value 看当前）——管 farm 砍树 + clear_area。📐参数键名: appearance/customize=同 daily 那套(appearance 是 hair,hair_color,skin,shirt,pants,hat,acc,eye_color,pants_color；customize 是 name,farmname,favorite)；color=hue/sat/val(0-100 滑块)或 hex；retire/reactivate=tool_name；hair/shirt/pants/hat/colorpreset/confirm_look 无参。",
"session": "会话域(🧠 上下文缓冲，多数情况不用)：status(看缓冲条数/设置) set(改设置 setting,value) export(手动导出记忆)。📐参数键名: set=setting+value(**都是字符串**)，status/export 无参。",
"script": "脚本/异步域(🚀被动异步优先)：continue(继续阻塞:确认脚本在跑/续跑,不新建不碰层数) stop(停任务,job_id空=停最近在跑) async(自动异步白名单 show/add/remove/enable=on|off)。进度自动播报(运行中+收工含总时长)，无需查。⚠️跑脚本用对应便利工具域 op——短任务(耕/浇/收/砍/清/摸动物)走 farm/scene 域 op(同步)、长任务(钓鱼/挖矿/炸矿/机器收放)走 mine/fish/farm 域便利工具(白名单自动后台)；start/run 已砍(改 continue 确认继续阻塞)；跑脚本时别用走动/挥工具同步工具，但聊天/看状态/开背包/整理背包没问题；📐参数键名: continue/stop=job_id(空=停最近在跑的那个) async=show,add,remove,enable。一次只跑一个脚本。⚠️参数放kw别拼ops(如 script(ops=\"continue\", kw={job_id})。",
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
        r = api.select(name)
        # ⚠️ 必须看返回：`/select` 找不到物品是回 `{ok:false}` 而**不抛异常**，
        #    以前这里无条件报「✅ 已选择」⇒ 名字写错也谎报成功（2026-09-11 抓到）。
        if not r.get("ok"):
            return _with_state(f"❌ 背包里没有「{name}」: {r.get('error', '未找到')}")
        return _with_state(f"✅ 已选择: {r.get('selected') or name}")
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
    - skip — **整段跳过当前剧情/事件**（C# 走 `currentEvent.skipEvent()`；无事件时会退化成"按 ESC 关菜单"，
      所以想跳剧情优先用 `menu skip`，它会先确认真有事件再动手）
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
    # ⚠️ 2026-09-11 修：原来喂 `_gather_state()`（= **AI 自己的 7843**）
    #    ⇒ 输出「💭 **轮回** 似乎在发呆」（说 AI 自己），而引导写的是 `peek(看恒干吗)`。
    #    `player_activity` 模块头也写明**主语是用户(房主)**、且要“从 host 进程取状态喂进来”。
    #    改走 `_heartbeat_line(data)` —— 它内部 `_gather_user_state()`（host 7842）+ 重用同一套
    #    “同在/附近”合并逻辑；拿不到 host 状态时它自带“退化为描述 AI 自己”的既有兜底。
    activity = _heartbeat_line(data)
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
def storage_view(box=-1) -> str:
    """📦 看箱：box=-1 网络概览（⭐默认箱+剩余格+色名+【类目标签】，每箱一行）；
    box=N 看第 N 箱完整清单（不截断，AI 装箱决策前看全）；
    box="内置冰箱"/"红箱"/"#303030"/"30,23" 也行（和 store 的 target 同一套匹配）。
    = 原 storage_layout(-1) + scan_chests(N) 互补合一。"""
    # ⚠️ 2026-09-12：box 以前**只吃序号** —— 传名字（如 "内置冰箱"）会漏出 Python 原生异常文案
    #    `'>=' not supported between instances of 'str' and 'int'`（恒真机撞到）。
    #    现在非数字一律走 `_resolve_storage_target`（= store 的 target 那套：色名/#hex/名字/标签/"x,y"），
    #    认不出就**明确报错**，不再漏原生异常。⚠️ 判据是**坐标回查序号**，别按名字重排列表。
    if isinstance(box, str):
        s = box.strip()
        if s.lstrip("-").isdigit():
            box = int(s)
        else:
            tgt = _resolve_storage_target(s)
            if isinstance(tgt, str):
                return _with_state(tgt)
            try:
                chests = api._get("/scan_chests").get("chests") or []
            except Exception as e:
                return _with_state(f"❌ 查箱失败: {e}")
            hit = next((i for i, c in enumerate(chests)
                        if (c.get("x"), c.get("y")) == (tgt["x"], tgt["y"])), None)
            if hit is None:
                return _with_state(f"❌ 没找到「{s}」对应的箱子（当前场景 {len(chests)} 个）")
            box = hit
    if isinstance(box, int) and box >= 0:
        return scan_chests(box)
    if isinstance(box, int):
        return storage_layout()
    return _with_state(f"❌ box 要序号或箱子名/色名/坐标，「{box}」看不懂")


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
    items: 逗号/空格分隔，每项可带数量如 "西瓜,铜矿石×30,木材"（不带数=用顶层 count，count 也没给=全量）。
    精确匹配（中英文名/ID）；模糊查哪个箱先用 storage find。
    """
    try:
        # 单箱精确取（拟人走到那箱再取）
        if name and x >= 0 and y >= 0:
            _walk_to_chest(x, y)
            return chest_take(x, y, name, count)
        # ⚠️ 2026-09-12：`count` 以前**批量这条路完全不认**（只有上面单箱那条认）⇒
        #    `take items="Large Egg" count=1` 把**整摞 x7 全取走**（恒真机撞到）。
        #    现在：每项自带的 `×N` 优先，没带的用顶层 `count`。`count=999`(默认) 视作"不限" ——
        #    SDV 单摞上限就是 999，且这样显示才是 `x7` 而不是 `7/999`。
        _lim = count if (isinstance(count, int) and 0 < count < 999) else -1
        reqs = []
        for part in re.split(r"[,，;；]+", (items or "").strip()):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(.*?)\s*[xX×]\s*(\d+)$", part)
            if m:
                reqs.append({"name": m.group(1).strip(), "count": int(m.group(2))})
            else:
                reqs.append({"name": part, "count": _lim})
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


def _drop_one(name: str, count: int = 1):
    """丢**一种**物品，返回 (成功?, 回话, 剩余格数)。"""
    _n0 = _inv_slots()
    r = api._post("/drop", {"name": name, "count": count})
    if not r.get("ok"):
        return False, f"❌ {name}: 丢弃失败（{r.get('error', r)}）", r.get("inventoryLeft")
    # ⚠️ 2026-09-11：`removed=0` 以前也照报「已丢弃 … x0」⇒ 名字对不上时 AI 以为丢掉了。
    #    名字口径 = `check backpack` 里显示的（C# 侧已对齐 Name|DisplayName）。
    n = r.get("removed", 0)
    if n:
        return True, f"🗑️ 已丢弃 {name} x{n}", r.get("inventoryLeft")
    # 🔍 2026-09-19 反向补一刀：**`removed=0` 不等于没丢**。
    #    C# `HandleDrop` 的循环是 `toRemove = Math.Min(remaining, item.Stack)` ——
    #    `Stack` 为 **0** 的物品（真机见过：**我们放下去的家具**，见下）算出来 `toRemove=0`、
    #    `removed += 0`，**可紧接着 `if (item.Stack <= 0) Items[i] = null` 照样把槽位清了**
    #    ⇒ **东西真没了，回包却说"一个都没丢"**（方向最危险的那种谎报：AI 以为还在）。
    #    ⚠️ 这种 `Stack=0` 是**我们 `/use` 放家具**造成的：游戏 `location.furniture.Add(this as Furniture)`
    #      加的是**背包那个对象本身**（不是副本），我们放完又 `reduceActiveItemByOne()` ⇒ 它被减成 0；
    #      捡回来时 `removeQueuedFurniture` 把这个 0 原样放回包里。**根治要动 C#（记账中）。**
    #    ⇒ 这里**不信 `removed`，读回占格数**：真少了就算成功，别把已丢的说成没丢。
    _n1 = _inv_slots()
    if _n0 is not None and _n1 is not None and _n1 < _n0:
        return True, f"🗑️ 已丢弃 {name}（游戏回的 `removed=0`，但**背包占格 {_n0}→{_n1}**，确实丢了）", r.get("inventoryLeft")
    return False, f"❌ 背包里没有「{name}」，一个都没丢（名字用 check backpack 里显示的）", r.get("inventoryLeft")


def drop_item(name: str = "", count: int = 1, items: str = "") -> str:
    """🗑️ 丢背包物品（**直接消失、不落地面**）——清背包/腾空位用。

    两种用法（二选一）：
      · `name="Wood" count=3`       丢**一种**物品 N 个（count 省略=1）
      · `items="木头,石头,萝卜:2"`  一次丢**多种**：逗号分隔，每项可跟 `:数量`（不跟=1）

    ⚠️ 名字口径 = `check backpack` 里显示的那个（C# 侧已把 Name|DisplayName 对齐）。
    ⚠️ **菜单开着时别丢**（闸门会拦，别绕）：满包领取(`ItemGrabMenu`)这类菜单持**背包快照**，
       关菜单那一刻会把快照写回 ⇒ 丢掉的会**原样复活**（2026-09-12 真机实测，见 CHANGELOG ㉕）。
    """
    if items:
        specs = [s.strip() for s in items.split(",") if s.strip()]
        if not specs:
            return '❌ items 是空的：写成 items="木头,石头" 这种（逗号分隔；每项可跟 :数量）'
        done, miss, left = [], [], None
        for spec in specs:
            nm, sep, cnt = spec.rpartition(":")
            if not sep:                      # 没写 `:数量` ⇒ 整串就是名字
                nm, cnt = spec, "1"
            try:
                c = int(cnt)
            except ValueError:
                return f"❌ 「{spec}」的数量「{cnt}」不是数字（写法：萝卜:2）"
            ok, msg, left = _drop_one(nm.strip(), c)
            (done if ok else miss).append(msg)
        out = []
        if done:
            out.append("、".join(done))
        if miss:
            out.append("\n".join(miss))
        if left is not None:
            out.append(f"背包剩 {left} 格")
        return "\n".join(out)
    if not name:
        return '❌ 要丢什么？给 name=物品名（一种）或 items="A,B,C"（多种）'
    ok, msg, left = _drop_one(name, count)
    return f"{msg}，背包剩 {left} 格" if ok else msg


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
    每周限 2 次、每天限 1 次（SDV 机制）。NPC 在别的图时自动 map_go 过去再送（全图可做）。

    Args:
        npc_name: NPC 名字（如 "Leah"）
        item_name: 背包里的物品名（如 "Grape"）
    """
    try:
        # 0. 找到 NPC → 同图走位；跨图先 map_go 过去再走（镜像 chat_npc，全图可做）
        fr = api.find_npc(npc_name)
        if fr.get("npcs"):
            n = fr["npcs"][0]
            nloc = n.get("location")
            nname = n.get("displayName") or n.get("name") or npc_name
            if nloc and nloc != api.current_location():
                go = map_go(nloc)
                if "✅ 到达" not in go and "已经在" not in go:
                    return f"❌ 到不了「{nname}」所在的 {nloc}：\n{go[:300]}"
                fr2 = api.find_npc(npc_name)   # 人可能移动了，重查一次走位
                if fr2.get("npcs"):
                    n = fr2["npcs"][0]
            api.walk_natural(int(n.get("x", 0)), int(n.get("y", 0)) + 1)
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
    """🎁 送物品给另一位玩家（正式赠予，一次一个）
    SDV 里玩家之间没有好感度条，本质是把物品从自己的背包转到对方背包。
    ⚠️ 走的是**赠送提议**：对方点同意东西才真过去。**这一下要等几秒，是正常的、不是卡住**
       （拟人节拍 5 秒 + 等对方点头）。回包会明说「他收下了」还是「还在等他点同意」——
       看到后者**别当成已经送到**，也别重复发（重复发只是再发一次提议）。
    💡 想**整叠一次给、不用对方操作**：用 hand（走过去丢他脚边，磁吸自动收）。

    Args:
        player_name: 目标玩家的角色名（用 /state 的 otherPlayers 或名单）
        item_name: 背包里的物品名
    """
    try:
        _t0 = time.time()
        try:
            _had = _count_in_inventory(api.state(), item_name)
        except Exception:
            _had = 0
        # ⏱️ 拟人节拍（恒 2026-09-19：「**第一次询和送礼请求发送成功之间隔五秒**（等接受礼物要时间）」）
        _gift_pace(_t0)
        r = api._post("/gift", {"target": player_name, "item": item_name})
        if not r.get("ok"):
            return _with_state(f"赠送失败: {r.get('error', r)}")
        # ⚠️ 2026-09-19 恒真机测出：玩家之间送礼走的是 **SendProposal**。
        #    回包 `action=gift_proposal_sent` =「提议已发出，**等对方点同意**」——东西这时候
        #    还**没到他包里**。旧代码不看这个字段，一律印「已送 X 给 Y 🎁」⇒ 对方不点同意
        #    也照样报成功（教科书级的"工具说成功但事没发生"）。见 CHANGELOG 2026-09-19。
        if r.get("action") == "gift_proposal_sent":
            # ⏱️ 发出去 ≠ 送到：**接受礼物是要时间的**（恒同日）。等一会儿再回读自己背包——
            #    东西真走了才算送到；没走就如实说"还在等他点同意"。
            if _had > 0:
                for _ in range(_GIFT_ACCEPT_WAIT):
                    time.sleep(1.0)
                    try:
                        if _count_in_inventory(api.state(), item_name) < _had:
                            return _with_state(
                                f"🎁 {r.get('target')} 收下了「{r.get('item')}」，东西已经过去了 ✅")
                    except Exception:
                        break
            return _with_state(
                f"🎁 赠送提议已发给 {r.get('target')}——**还在等他点同意**（东西暂时还在我包里，"
                f"他不点会自动退回）。别当成已经送到。")
        return _with_state(f"已送 {r.get('item')} 给 {r.get('target')} 🎁")
    except Exception as e:
        return _with_state(f"赠送失败: {e}")


def _inv_entries(st: dict, item_name: str) -> list:
    """背包里匹配某物品的条目（中文显示名 / 英文内部名 / **限定 id** 都认）。

    🪵 2026-09-19（#13 收口时一并发现）：**必须也认 `itemId`**。
    地板/墙纸这类「**同名多款**」只有编号能区分 —— `(FL)0` 与 `(FL)1` 的 `Name` 都是 `Flooring`、
    显示名都是「地板」（`Wallpaper.cs:58`）。不认 id 的话 `place name="(FL)1"` 在这里
    **一条都匹配不到** ⇒ `_decor_place_check` 拿不到 qid ⇒ **直接放行走原路** ⇒
    「点错格会点名告诉你」这道守卫**静默失效**（而 C# 的 `/select` 已经认 id 了，
    **两边口径必须一致**，否则守卫只在按名字调时才在）。
    """
    low = (item_name or "").lower()
    return [i for i in (st.get("inventory") or [])
            if i.get("name") and low in ((i.get("name") or "").lower(),
                                         (i.get("displayName") or "").lower(),
                                         str(i.get("itemId") or "").lower())]


def _count_in_inventory(st: dict, item_name: str) -> int:
    """背包里某物品的总数。"""
    return sum(int(i.get("stack") or 0) for i in _inv_entries(st, item_name))


# ⏱️ 玩家之间送礼的节拍（恒 2026-09-19）：
#    「**第一次询和送礼请求发送成功之间隔五秒**（等接受礼物要时间）」——
#    人不会一照面就把东西塞过去。`hand` 贴脸时走位循环会瞬间 `break`、`give` 更是直接闪现，
#    所以这里立一个**下限**而不是凭空加睡眠。
_GIFT_PACE = 5.0

#    发出去之后等对方点头的轮数（每秒 1 轮）——「接受礼物要时间」，别发完立刻宣告成功。
_GIFT_ACCEPT_WAIT = 6


def _gift_pace(t0: float) -> None:
    """补足「从 `t0`（本次送礼开始）到现在」到 `_GIFT_PACE` 秒的**差额**。
    走位/找东西已经花掉 5 秒以上就一秒都不多睡（不为了凑数空转）。"""
    left = _GIFT_PACE - (time.time() - t0)
    if left > 0:
        time.sleep(left)


def _stand_tile_near(tx: int, ty: int, mx: int, my: int):
    """在 (tx,ty) 的 8 邻域里挑一个**离我最近**的可站格（跳过他自己站的那格）。
    挑不到返回 None（那就原地丢，让调用方如实报距离）。"""
    cand = sorted(
        ((tx + dx, ty + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if (dx or dy)),
        key=lambda c: abs(c[0] - mx) + abs(c[1] - my))
    for (x, y) in cand:
        try:
            # ⚠️ 必须是 _post：`/passable` 读的是 **body**（`ReadJson`），用 `_get` 传查询串的话
            #    x/y 全是默认 0 ⇒ 永远 false ⇒ 一个可站格都挑不出来（2026-09-11 真机踩过）。
            if api._post("/passable", {"x": x, "y": y}).get("passable"):
                return (x, y)
        except Exception:
            continue
    return None


@mcp.tool()
def hand_item(player_name: str, item_name: str, count: int = 0) -> str:
    """🤲 走到对方身边，把物品**丢在对方脚边**——磁吸会自动进对方背包，可以整叠，不用等对方点同意。
    和 give 的分工：give 是正式赠予（手持右键，一次一个、要等对方点同意）；hand 是"递过去"，
    适合一次给一大批。会先走近再丢，丢完停一下确认对方真收下了（没接住会如实说明，不谎报）。
    ⏱️ **全过程 ≥5 秒是设计好的**（拟人节拍：第一次问询到递出手之间要有个来回，恒 2026-09-19）
    ——别以为卡住了；对方在动的话还会更久（每轮都重读他的位置）。

    Args:
        player_name: 目标玩家名（用 /state 的 otherPlayers）
        item_name: 背包里的物品名（中文显示名或英文内部名都认）
        count: 丢几个（默认 0 = 整叠）
    """
    try:
        _t0 = time.time()   # ⏱️ 拟人节拍起点（见 _gift_pace）
        st = api.state()
        me = st.get("player") or {}
        my_name = me.get("name") or ""
        loc = st.get("location") or {}
        my_loc = loc.get("name") if isinstance(loc, dict) else str(loc)
        others = [o for o in (st.get("otherPlayers") or []) if (o.get("name") or "") != my_name]
        tgt = next((o for o in others if (o.get("name") or "") == player_name), None)
        # 🙋 代词按性别（恒 2026-09-11："你的'他'要不要按性别匹配一下"）。
        #    ⚠️ 对方的 isMale 只能从 **host 的 /state** 读（`otherPlayers` 没带性别字段）；
        #    对不上名（多玩家/拿不到）就退回中性"他"。要更通用得在 C# 的 otherPlayers 里补 isMale。
        he = "他"
        try:
            hp = (api.host_state() or {}).get("player") or {}
            if hp.get("name") == player_name and hp.get("isMale") is False:
                he = "她"
        except Exception:
            pass
        if tgt is None:
            who = "、".join((o.get("name") or "?") for o in others) or "没别人"
            return f"❌ 没看到 {player_name}（在场的是：{who}）"
        if (tgt.get("location") or "") != my_loc:
            return (f"❌ {player_name} 不在同一张图（{he}在 {tgt.get('location')}，我在 {my_loc}）"
                    f"——先 map_go 过去再递")
        before = _count_in_inventory(st, item_name)
        if before <= 0:
            return f"❌ 背包里没有 {item_name}"
        # 🚫 工具不能丢（恒 2026-09-11："工具是禁止扔出背包的"）。C# 端才是权威（canBeDropped/canBeTrashed），
        #    这里先用 catNum=-99 快速挡一下，省得白走一趟再报错。
        if any(i.get("catNum") == -99 for i in _inv_entries(st, item_name)):
            return f"❌ {item_name} 是工具，不能丢出背包"
    except Exception as e:
        return f"递给失败: {e}"

    # 磁吸要够近才吸得上（基准 ~2 格）。⚠️ 位置**每轮都重读**（恒 2026-09-11："我会一直移动"）——
    # 只读一次的话，走过去那几秒他动了，东西就丢在**出发时的旧位置**上。
    # 3 轮自然走位（每轮等它真到）→ 第 4 轮才闪现兜底（恒拍板"重走失败才 position"）。
    def _live_target():
        s = api.state()
        return (s.get("player") or {},
                next((o for o in (s.get("otherPlayers") or []) if (o.get("name") or "") == player_name), None))

    note, tx, ty = "", 0, 0
    for rnd in range(4):
        mine, t2 = _live_target()
        if t2 is None:
            return f"❌ 递的过程中跟丢了 {player_name}"
        if (t2.get("location") or "") != my_loc:
            return f"❌ {player_name} 跑到 {t2.get('location')} 去了——跨图不递，先 map_go 过去"
        tx, ty = int(t2.get("x", 0)), int(t2.get("y", 0))
        mx, my = int(mine.get("x", 0)), int(mine.get("y", 0))
        gap = max(abs(tx - mx), abs(ty - my))
        if gap <= 2:
            break
        stand = _stand_tile_near(tx, ty, mx, my)
        if not stand:
            break
        try:
            if rnd < 3:
                api._post("/walk_to", {"location": my_loc, "x": stand[0], "y": stand[1]})
                note = f"（先走到 ({stand[0]},{stand[1]}) {he}身边）"
                # ⚠️ 必须**等它真走到**（`/walk_to` 只是下发路径就返回）；⚠️ 超时还得**按距离缩放**——
                #    写死 10s 对 24 格的长走位根本不够，会白跌进闪现兜底（2026-09-11 真机踩过）。
                _wait_arrival(my_loc, stand[0], stand[1], timeout=min(45, 8 + gap * 2))
            else:
                api._post("/position", {"x": stand[0], "y": stand[1]})
                note = f"（{he}一直在走/路挡着，闪现到 ({stand[0]},{stand[1]})）"
                time.sleep(0.5)
        except Exception:
            break

    # ⏱️ 拟人节拍：第一轮询问 → 递出手里这叠，中间至少隔 5 秒（恒 2026-09-19）。
    #    贴脸时上面那圈走位 `gap <= 2` 会**立刻 break**，一照面就丢显得很机械。
    _gift_pace(_t0)

    # 丢之前**最后确认一次**距离（他会动；闪现也可能没落对）：太远就**不递**，
    # 绝不把东西丢在够不着的地方（恒 2026-09-11 拍板）。
    mine, t3 = _live_target()
    if t3 is None:
        return f"⚠️ 最后关头跟丢了 {player_name}——**没递**"
    far = max(abs(int(t3.get("x", 0)) - int(mine.get("x", 0))),
              abs(int(t3.get("y", 0)) - int(mine.get("y", 0))))
    if far > 3:
        return f"⚠️ 没追上 {player_name}（差 {far} 格）——**没递**，让{he}停下来说一声我再来"

    r = api._post("/drop_item", {"item": item_name, "count": count})
    if not r.get("ok"):
        return f"❌ 递给失败：{r.get('error', r)}"
    n, disp = int(r.get("count") or 0), r.get("item")

    # ⚠️ 丢完必须**停一下**：对方没接住时，1.2s 排除期（timeBeforeReturnToDroppingPlayer）一过我
    #    自己的磁吸就会把它吸回来——等这一下正好让"滑回我包里"发生完，再据实回报（不谎报成功）。
    time.sleep(2.5)
    try:
        left = [d for d in (api._get("/debris").get("debris") or [])
                if d.get("droppedByName") == my_name and d.get("itemName") == r.get("name")]
        back = _count_in_inventory(api.state(), item_name)
    except Exception:
        left, back = [], before - n
    if not left and back <= before - n:
        return f"🤲 {disp}×{n} 递给了 {player_name}{note}，{he}收下了 🎁"
    if not left and back >= before:
        return (f"⚠️ {player_name} 没接住，{disp}×{n} 又滑回我包里了（{he}可能刚走开/背包满了）"
                f"——原封不动，没有损失")
    return f"⚠️ 放在 {player_name} 脚边了（({tx},{ty}) 附近），但{he}还没吸走——可能背包满了"


@mcp.tool()
def check_friendship(name: str) -> str:
    """❤️ 查询与某 NPC 的好感度
    送礼前先查，挑好感低/喜欢的东西送。

    Args:
        name: NPC 名字（如 "Leah"）
    """
    try:
        r = api._get("/friendship", {"npc": name})
        if not r.get("ok"):
            return f"查询失败: {r.get('error', r)}"
        if not r.get("known"):
            return f"还没认识 {name}（好感 0）"
        return (f"与 {name} 好感 {r.get('points')} 分（{r.get('hearts')}❤️），"
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
            # ⚠️ C# 的 error 是**英文原文**（如 "No new items to donate or no empty slots"）——
            # 直接透给 AI 就是"中文壳裹着英文话"，跟 09-12③ 修的 `box`/`retire` 同类。
            # 已知的翻人话，认不出的**原样返回**（宁可难看也别吞信息，见 09-12①"宁报错别兜底"）。
            _err = r.get("error", "") or "捐赠失败（C# 没给原因）"
            _known = {
                "No new items to donate or no empty slots":
                    "没有可捐的了 —— 背包里没有「游戏判为可捐、且博物馆还没收」的东西"
                    "（博物馆已收齐时这是正常回答，不是出错）",
            }
            _msg = _known.get(_err.strip(), _err)
            return f"❌ {_msg}"
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
        # 🎓 精通山洞的碑/基座（MasteryTrackerMenu, 2026-09-16 恒）
        #    ⚠️ 数据只在 `/menu` 的 `mastery` 键里；`/state` 的 activeMenu **不带**它
        #    （那边只有 type/dialogue/levelUp/readyCheck/questionKind…）⇒ 状态条 `_menu_advice`
        #    拿不到，所以**以 `menu read` 为权威**。which=-1=中央基座总览（只画进度条，不列奖励）。
        if t == "MasteryTrackerMenu":
            mt = m.get("mastery") or {}
            if not mt:
                lines.append("  ⚠️ 读不到 mastery 数据（DLL 太旧？本菜单需要 2026-09-16 之后的 NagiBridge.dll）")
                return _with_state("\n".join(lines))
            if mt.get("isOverview"):
                lines.append("  🎓 **中央基座（总览）**：这一页只画精通等级进度条 + 五颗星，看不到具体奖励。")
                lines.append("  💡 想看某块碑给什么 → 走到那块碑前 `interact` 单独开（五碑位置 `map lookup MasteryCave`）")
                return _with_state("\n".join(lines))
            lines.append(f"  🎓 **{mt.get('title') or mt.get('skill') or '?'}**碑"
                         f"（which={mt.get('which')} / skill={mt.get('skill')}）")
            for r in (mt.get("rewards") or []):
                tag = "配方" if r.get("isRecipe") else "物品"
                lines.append(f"    · {r.get('name')} [{r.get('id')}]（{tag}）—— {r.get('label') or ''}")
            if mt.get("claimed"):
                lines.append("  ✅ 这块**已经领过了**（游戏不再生成领取按钮）；上面列的是它当年给的东西。")
                lines.append("  🧭 收起: menu click(button=upperRightCloseButton)")
            elif mt.get("canClaim"):
                lines.append("  🟢 **有没花掉的精通等级 ⇒ 现在就能领！**")
                lines.append("  🧭 领: menu click(button=mainButton)（领完这块碑点亮、精通等级 -1）")
                lines.append("  🧭 不领/再看看: menu click(button=upperRightCloseButton) 收起")
            else:
                lines.append("  ⚪ 领不了：**当前没有未花掉的精通等级**（领碑消耗 1 点）。")
                lines.append("  🧭 查还差多少经验升级: check(what=mastery) ｜ 收起: menu click(button=upperRightCloseButton)")
            return _with_state("\n".join(lines))
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
        # 📋 2026-09-07 恒：ItemListMenu（"丢失的物品"等）——读真物品+总价值+ok 确认引导。
        #    ⚠️ 丢失物品的 ok 只是 exitThisMenu（确认失去并关闭，**不回收物品**），别让 AI 误以为能领回。
        if t == "ItemListMenu":
            _QM = {0: "", 1: "[银]", 2: "[金]", 3: "[铱]"}
            items = m.get("items") or []
            lines.append(f"  📋 {m.get('menuTitle') or '物品列表'}")
            if items:
                for it in items:
                    name = it.get("name") or "?"
                    stk = int(it.get("stack") or 0)
                    q = _QM.get(it.get("quality", 0), "")
                    lines.append(f"    · {q}{name}" + (f" x{stk}" if stk > 1 else ""))
            else:
                lines.append("    （无物品）")
            if m.get("listTotal"):
                lines.append(f"    💰 总价值 {m['listTotal']} 金")
            if len(items) > (m.get("listPageSize") or 8):
                lines.append("  🧭 翻页: menu click(button=forward/back)")
            lines.append("  🧭 操作: 确认并关闭（这些是丢失的物品，ok 只确认失去）= menu click(button=ok)")
        # 🧾 2026-09-07 恒：ShippingMenu（过夜结算复盘窗口）——五大项小计+第一名物品+总价；点类目 tab 钻进去看该类明细。
        if t == "ShippingMenu":
            _CAT = {0: "🌾农作物", 1: "🍄采集", 2: "🐟钓鱼", 3: "⛏️矿山", 4: "📦其他", 5: "🧾总计"}
            _QM = {0: "", 1: "[银]", 2: "[金]", 3: "[铱]"}
            sh = m.get("shipping") or []
            cur = m.get("shippingCurrentPage", -1)
            lines.append("  🧾 过夜结算:")
            # 五大项小计 + 类别第一个物品名（类目 tab 就是显示第一个物品的图标）
            for c in sh:
                if c.get("index") == 5:
                    continue
                ci = c.get("index")
                nm = _CAT.get(ci, f"类{ci}")
                its = c.get("items") or []
                fn = ""
                if its:
                    f0 = its[0]
                    fn = f" {_QM.get(f0.get('quality', 0), '')}{f0.get('name', '')}"
                lines.append(f"    {nm} {c.get('subtotal', 0)}g{fn}")
            if m.get("shippingTotal") is not None:
                lines.append(f"    🧾 总计: {m['shippingTotal']}g")
            # 已钻到某类 → 列该类明细；未钻到 → 给类目 tab 让 AI 点进去
            if cur in (0, 1, 2, 3, 4):
                cat = next((c for c in sh if c.get("index") == cur), None)
                if cat:
                    lines.append(f"  📄【{_CAT.get(cur, cur)}】明细:")
                    for it in (cat.get("items") or []):
                        q = _QM.get(it.get("quality", 0), "")
                        lines.append(f"      · {q}{it.get('name')} x{it.get('count', 0)} = {it.get('value', 0)}g")
                lines.append("  🧭 返回五大项: menu click(button=back)；确认: menu click(button=ok)")
            else:
                cats = m.get("shippingCategories") or []
                if cats:
                    lines.append("  📑 想看某类明细 → 点该类目(按序号，稳、跟分辨率无关):")
                    for cc in cats:
                        lines.append(f"      · {cc.get('name')} = menu click(category={cc.get('index')})")
                lines.append("  🧭 点某类目看该类明细 → menu click(category=N)；确认: menu click(button=ok)")
        if m.get("responses"):
            lines.append("  选项:")
            for r in m["responses"]:
                lines.append(f"    [{r['index']}] {_flat_text(r['text'])}")
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


_SENTINEL_PERIOD = 2.0
_sentinel_state = {"ts": 0.0}


def _festival_sentinel_tick():
    """🎪 节日哨兵（2026-09-12 恒）——**不靠工具调用**也能在开赛瞬间接管。

    背景：自动 hook（冰钓/找蛋）挂在 `_with_state` 上 ⇒ 只在**工具调用边界**才被检查。而 2026-09-12
    冬钓节真机：`menu advance` 阻塞 139 秒，比赛那 120 秒**整个发生在这次调用内部** ⇒ 等调用返回时
    `festivalTimer` 已归零，hook **压根没看见开赛**（那场 0 条鱼，见 CHANGELOG 09-12）。

    ⇒ 本哨兵独立于工具调用，在后台按秒级轮询 `/event_state`（**便宜**：只读 Game1 + currentEvent 简单
    字段，**不枚举 NPC/背包** —— 对比 `/festival` 会遍历全部 actor，绝不能拿来轮询），看到"限时小游戏
    开赛"就把**现成的 hook** 叫醒：复用它们的 `fired` 闸门 ⇒ 和"工具边界"那条路不会重复触发，
    而且一年就那几天会真的走到这里（平时 `festivalTimer<=0` 直接返回，零动作）。
    """
    now = time.time()
    if now - _sentinel_state["ts"] < _SENTINEL_PERIOD:
        return
    _sentinel_state["ts"] = now
    try:
        es = api.event_state()
    except Exception:
        return
    if not es.get("ok"):
        return
    if int(es.get("festivalTimer") or -1) <= 0:
        return                                  # 没在限时小游戏 → 哨兵不做事
    data = {"time": {"season": es.get("season"), "dayOfMonth": es.get("day")},
            "location": {"name": es.get("location")}}
    note = ""
    try:
        note = _maybe_ice_fishing_auto(data) or _maybe_egg_run_auto(data)
    except Exception:
        return
    if note:
        try:
            _plan_notices.append(note)          # 结果走既有通知缓冲，注入到 AI 的下次工具返回
            if len(_plan_notices) > 20:
                del _plan_notices[:len(_plan_notices) - 20]
        except Exception:
            pass


def _festival_sentinel_loop():
    while True:
        try:
            _festival_sentinel_tick()
        except Exception:
            pass
        time.sleep(0.5)                         # 真正的节流在 tick 里（2s）；这里只保证醒来够快


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


def menu_click(option: int = -1, button: str = "", x: int = -1, y: int = -1, item: str = "", right: bool = False, quantity: int = 1, action: str = "", real: bool = False, slot: int = -1, category: int = -1) -> str:
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
        category: 🗂️ ShippingMenu 按类目序号钻入（0农作/1采集/2钓鱼/3矿/4其他；比坐标稳、跟分辨率无关）
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
        if category >= 0: data["category"] = category
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
       ⚠️ **点法必须带 `real=true`**（`menu click(option=N, real=true)`，N 通常 0）：不带 real 时 mod 走
       `answerDialogueQuestion` 且 NPC 靠"面朝格"找（`isCharacterAtTile(player.GetGrabTile())`）⇒ 对不上就
       **静默点空**。2026-09-13 真机：不带 real 连点两次框不关；带 real 一次 ⇒ 海莉报出**接受台词**（真生效）

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


FORGE_GUIDE = """🔨 锻造台（优先用本图的迷你锻造台 Mini-Forge；本图没有才去火山顶层 Caldera。menu forge 一键操作）
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
    自动开台（**先找本图 Mini-Forge**，没有才 warp 到 Caldera 锻造台 22,21）→ 放料用 /forge_set 直接设槽（不碰鼠标，
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

    # 📌 材料够不够**不在开台前查**（2026-09-12 恒）：我第一版写死了一张
    #    `{"combine":20,"random":20,"dragon":10,"attribute":10}` 的表，恒指出
    #    「**两个料放进去的时候会显示需要消耗的火山晶石**」—— 游戏自己就有真数，
    #    硬编码那张表必然抄歪（强化按等级是 10/15/20，各模式还不同）。
    #    所以改成：放完料读 `/forge_set` 回的 `shardCost`/`shardsHave`（C# 侧取
    #    `ForgeMenu.GetForgeCost(左,右)` + `Items.CountId("(O)848")`，都是游戏原值）。
    #    检查点见下面 `fs` 之后。

    # 🔙 回程（2026-09-12 恒：「我们没有录火山出口的路」）——
    #    Caldera 那条路把 AI 送到火山顶层，而**火山出口的走法从没录进 locations.py**；
    #    更硬的是 `/warp` **根本回不来**：它只认 `Game1.locations`，而小屋内景的 key 是**唯一名**
    #    （`FarmHouse<guid>`），按 "Cabin" 查 → `Location 'Cabin' not found`（真机实证）。
    #    `/warp_into` 走 `ResolveAnyLocation`（唯一名 / farm.buildings.indoors 都认），实测能精确送回。
    #    所以：**挪过窝就把出发地记下来，收工原样送回**（出错路径也送）。
    _origin = {"loc": None, "x": 0, "y": 0}

    def _back():
        """送回出发地（没挪过窝 = 空操作）。
        ⚠️ 必须赶在拼状态条**之前**调 —— 否则条子还报着火山，AI 明明到家了却读到 Caldera。"""
        if not _origin["loc"]:
            return
        try:
            _req.post(f"{base}/warp_into",
                      json={"location": _origin["loc"], "x": _origin["x"], "y": _origin["y"]},
                      timeout=8)
            time.sleep(0.8)
        except Exception:
            pass
        _origin["loc"] = None

    def _done(msg: str) -> str:
        """收工：先送回出发地，再拼状态条（顺序不能反）。"""
        _back()
        return _with_state(msg)

    try:
        # ── 开台：没有 ForgeMenu 就找一台开 ──
        #   ① **本图有 Mini-Forge 就地用**（2026-09-12 恒：「轮回家好像有迷你锻造台，跟火山的是一样的」）——
        #      真机验过：Mini-Forge 点开的就是 `ForgeMenu`，按钮跟火山顶层那台一字不差
        #      （leftIngredientSpot/rightIngredientSpot/startTailoringButton/unforgeButton 全在）。
        #      没必要每次都往火山跑（还要求开了火山口+有传送手段）。
        #   ② 本图没有 → 老路：warp 到 Caldera(22,23) 点 (22,21)。
        m, fb = _menu()
        _where = None
        if not m.get("open") or m.get("type") != "ForgeMenu":
            _menu_close(base)
            time.sleep(0.5)
            # ① 本图找 Mini-Forge（`/machines` 只列**当前图**的机器）
            _mf = None
            try:
                _mk = _req.get(f"{base}/machines", timeout=8).json()
                _here = ((_state().get("location") or {}).get("uniqueName") or "")
                # ⚠️ 按 `location_unique` 比，不按 `location` 名：同名小屋(Cabin)光看名字会串
                for _x in (_mk.get("machines") or []):
                    if _x.get("type") != "Mini-Forge":
                        continue
                    if _here and _x.get("location_unique") and _x["location_unique"] != _here:
                        continue
                    _mf = _x
                    break
            except Exception:
                _mf = None
            if _mf is not None:
                _where = f"Mini-Forge({_mf['x']},{_mf['y']})"
                # 机器格站不了人 —— walk_to 会自己落到旁边那格（并在回执里说明实际站位）
                navigation.walk_to(x=int(_mf["x"]), y=int(_mf["y"]))
                _req.post(f"{base}/interact", json={"x": _mf["x"], "y": _mf["y"]}, timeout=8)
                time.sleep(1.0)
            else:
                _where = "Caldera(22,21)"
                # 出发地记下来（收工 `_done` 原样送回）—— 用**唯一名**，精确到"哪一间小屋"
                _st0 = _state()
                _l0 = (_st0.get("location") or {})
                _origin["loc"] = _l0.get("uniqueName") or _l0.get("name")
                _origin["x"] = int((_st0.get("player") or {}).get("x") or 0)
                _origin["y"] = int((_st0.get("player") or {}).get("y") or 0)
                _req.post(f"{base}/warp", json={"location": "Caldera", "x": 22, "y": 23}, timeout=8)
                for _ in range(20):
                    if _state().get("location", {}).get("name") == "Caldera":
                        break
                    time.sleep(0.3)
                _req.post(f"{base}/interact", json={"x": 22, "y": 21}, timeout=8)
                time.sleep(1.0)
        m, fb = _menu()
        if not m.get("open") or m.get("type") != "ForgeMenu":
            return _done(f"❌ 锻造台没开（{_where or '已有菜单'} 交互失败）")
        left = fb.get("leftIngredientSpot")
        right = fb.get("rightIngredientSpot")
        fbtn = fb.get("startTailoringButton")
        result_btn = fb.get("craftResultDisplay")
        ubtn = fb.get("unforgeButton")
        if not (left and right and fbtn):
            return _done("❌ 读不到锻造槽位，关掉重开菜单试试")

        # ── 放料：/forge_set 直接设槽（2026-08-11 后台菜单点击放料不可靠——
        #    /state 背包读取陈旧会打错格；改主线程直接设槽，不碰鼠标） ──
        set_data = {"left": item1}
        if item2:
            set_data["right"] = item2
        fs = _req.post(f"{base}/forge_set", json=set_data, timeout=8).json()
        if not fs.get("ok"):
            return _done(f"❌ 放料失败: {fs.get('error')}")
        time.sleep(0.8)

        # ── 晶石够不够：用**游戏自己算的数**（2026-09-12 恒）──
        # 起因：缺料时点「开始锻造」**什么都不会发生**，关菜单把料原样退回 ⇒ 工具只看到"没结果"，
        # 老代码于是报一句「合成结果没进背包（检查背包）」—— **不说是缺晶石**，AI 无从下手。
        # （真机现场：恒一眼看出「火山晶石不够，要20」，工具却把原因咽了。）
        # 这正是《宁报错别兜底》与 09-12④「别甩锅」的同一个病：**报错必须指向真原因**。
        # ⚠️ 只查晶石这一项（它是唯一"点了没反应"的硬门槛）；宝石/五彩碎片/龙牙/戒指配对
        #    这些没查 —— 缺它们仍会走到老那句。别以为这条覆盖了全部前置条件。
        _cost = int(fs.get("shardCost") or 0)
        _have = int(fs.get("shardsHave") or 0)
        if _cost > 0 and _have < _cost and mode != "unforge":
            _menu_close(base)   # 关菜单会把槽里的料放回背包（别让料卡在台上）
            return _done(
                f"❌ 火山晶石不够：这个操作要 {_cost} 个，背包里只有 {_have} 个（还差 {_cost - _have} 个）。\n"
                f"   去弄「火山晶石 Cinder Shard」：火山口(Caldera)敲矿 / 打怪掉落。\n"
                f"   ℹ️ 缺料时点「开始锻造」**不会有任何反应**（料会退回来）——"
                f"以前只报「合成结果没进背包」，就是这个原因。")

        # ── 拆解 ──
        if mode == "unforge":
            if not ubtn:
                return _done("❌ 读不到 unforge 按钮（拆解）")
            _click(ubtn["x"], ubtn["y"])
            time.sleep(2.0)
            _menu_close(base)
            return _done("💍 解除合成完成！组件已放回背包")

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
            return _done("⚠️ 结果可能没进背包（光标被关菜单处理了？检查背包）")
        if mode == "combine" and not any("Combined" in n for n in names):
            return _done("⚠️ 合成结果没进背包（检查背包）")
        tag = {"combine": "💍 合成戒指", "attribute": "⚔️ 属性附魔",
               "random": "🔮 随机附魔", "dragon": "🐉 龙牙附魔"}.get(mode, mode)
        return _done(f"{tag}完成！结果已放回背包")
    except Exception as e:
        try: _menu_close(base)
        except Exception: pass
        return _done(f"❌ 锻造失败: {e}")


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

def _find_kitchen_tiles(location: str = None):
    """🍳 读某张图的**厨房灶台格**（瓦片属性 `Action: Kitchen`，任意图层）。

    照抄游戏判据（`GameLocation.checkAction`: `case "kitchen": case "Kitchen": ActivateKitchen()`），
    ⚠️ **必须扫地图不能硬编码**：农舍/小屋/岛屋厨房布局不同。`location=None` = 当前图。

    ⚠️ 2026-09-11 真机纠正（**差点上线就炸**）：**不在 Back 层，在 `Buildings` 层**——
       恒家/小屋实测 `Buildings 19-23,23`（同图的 `Action: Yoba` 也在 Buildings）。
       原来写死 `layer="Back"` ⇒ **任何图都判"没厨房"**，连站在有厨房的小屋里都报
       「这张图没有厨房」（真机复现过）。→ 干脆不传 layer，全图层扫；C# 侧同一个坑同批修。
    返回 [(x, y), …]；空列表 = 这张图没厨房。
    """
    kw = {"scan": "Action"}
    if location:
        kw["location"] = location
    try:
        r = api._get("/tile_props", kw)
    except Exception:
        return []
    return [(h["x"], h["y"]) for h in (r.get("hits") or [])
            if str(h.get("value") or "").split(" ")[0].lower() == "kitchen"]


_NL = chr(10)   # 拼多行提示用（写 f-string 里的 "\n" 老被工具链吃掉，见 _go_to_kitchen）


def _kitchens_elsewhere() -> str:
    """报错时的**提醒**：探一下别处哪几间真有厨房（只报探到的，不猜、也**不自动过去**）。

    用的是**和"本场景有没有厨房"同一个判据**（扫 `Action: Kitchen` 瓦片）—— 不是猜、不是硬编码名单。
    ⚠️ 小屋得按 `uniqueName` 探（`FarmHouse<guid>`，农场上 `.Name` 全是 "Cabin"）——已把 `/tile_props`
       的定位改走 `FindLocationByName`，所以现在探得到。
    冷路径（本场景没厨房才会调），慢一点无妨。
    """
    cands = [("恒的农舍", "FarmHouse"), ("岛屋", "IslandFarmHouse")]
    try:
        _home = (api.state().get("player") or {}).get("homeLocation") or ""
    except Exception:
        _home = ""
    if _home:
        cands.insert(0, ("自家小屋", _home))
    found = []
    for label, ln in cands:
        if _find_kitchen_tiles(ln):
            found.append(label)
    if found:
        return "、".join(found) + "（已探到有厨房）"
    return "自家小屋 / 恒的农舍 / 岛屋（未逐一确认）"


def _go_to_kitchen():
    """先就位到灶台旁（够得着即可）。**已在厨房则什么都不做**。
    返回 None = 可以继续做菜；返回字符串 = 走不过去的**明确报错**（含灶台坐标，AI 可自己再试）。

    📌 恒 2026-09-11 拍板：**只管本场景** —— 本场景有厨房才 walk_to；
       没厨房就停下报错 + 提醒哪能借，**借厨房的腿由 AI 自己迈**（我们不包办、也不擅自进别人的屋子）。
    """
    try:
        st = api.state()
    except Exception:
        st = {}
    try:
        me = (st.get("player") or {})
        px, py = int(me.get("x", -999)), int(me.get("y", -999))
    except Exception:
        px, py = -999, -999
    tiles = _find_kitchen_tiles()
    if not tiles:
        loc = ((st.get("location") or {}).get("name")) or "?"
        return _with_state(
            f"❌ 这张图（{loc}）没有厨房，做不了菜 —— 升级房屋、或借他人小屋的厨房再试试吧。"
            + _NL
            + f"   有厨房的：{_kitchens_elsewhere()} —— map_go 过去、站到灶台旁再 cook。")
    # 已就位：**任一**灶台格够得着就行 —— 灶台常是一整排（恒家 `19-23,23` 共 5 格），
    # 只认第一格会让站在另一头的人被判"没走到"。
    if any(abs(px - tx) <= 1 and abs(py - ty) <= 1 for tx, ty in tiles):
        return None
    # 灶台本身不可走 → 遍历灶台格，挑一个**离自己最近**的可站 8 邻格（`/passable` 读 body，必须 _post）
    best = None
    for kx, ky in tiles:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                cx, cy = kx + dx, ky + dy
                try:
                    if api._post("/passable", {"x": cx, "y": cy}).get("passable"):
                        d = abs(px - cx) + abs(py - cy)
                        if best is None or d < best[0]:
                            best = (d, cx, cy)
                except Exception:
                    continue
    if best is None:
        kx, ky = tiles[0]
        return _with_state(f"❌ 灶台 ({kx},{ky}) 周围没有可站的格——过不去")
    _, tx, ty = best
    # 底座用 `/walk_to`（= map walk 的坐标版）；老的 move_to_tile 走 /move+BFS，已退役
    out = navigation.walk_to(x=tx, y=ty)
    # ⚠️ 判据落在"人真的到那儿了吗"，别靠字符串找 "✅"（状态条里到处都是 ✅ / ⚠️）
    try:
        _me2 = (api.state().get("player") or {})
        if (int(_me2.get("x", -9)), int(_me2.get("y", -9))) != (tx, ty):
            return _with_state(f"❌ 没走到灶台旁 ({tx},{ty})：{out.splitlines()[0] if out else '无响应'}")
    except Exception:
        pass
    return None


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
        # 🍳 先走到厨房（2026-09-11 恒："我希望角色走到厨房才能 cook"）。
        #    游戏本来就只允许站在灶台旁开烹饪菜单；C# `/cook` 侧已补权威位置门，
        #    这里负责**走过去**（判据在 C#、走位在 Python —— 同门感知那套分层）。
        walk = _go_to_kitchen()
        if walk is not None:
            return walk          # 走不过去 = 明确报错，别硬做
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
    """🤖 运行 scripts/ 下的脚本 —— ⚠️ 已退役（2026-09-06 恒：不再给 AI 直达，保留作内部兜底）。跑脚本用对应便利工具域 op：长任务(钓鱼/挖矿/炸矿/机器收放)走白名单自动后台、短任务(耕/浇/收/砍/清/摸动物)走 farm/scene 域 op。全清单→help(scripts)。

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
#  不打断脚本——script_start 立即返回 job_id；进度/收工(含总时长)自动播报，AI 不用查。
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
        self.finish_announced = False   # 收工信号只播报一次（_bg_activity_line 守卫，防循环）
        self.killed = False       # 🛑 被 script stop / _bg_kill 主动停的（≠自然跑完，播报要分开）
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
    """异步脚本状态条提醒：运行中按 wake_interval 限频；刚跑完一次性播报收工(带总时长)。
    返回空串=不提醒。interval=0 表示每次都提醒。
    ⚠️ 2026-08-16 恒：AI 正在连续操作（上次工具调用 < interval）时不提醒，不打断 AI。
    收工信号不受此限频——它是单次事件，finish_announced 守卫只播一次，之后彻底闭嘴。"""
    global _bg_last_wake
    if not _bg_cfg.get("enabled", True):
        return ""
    with _bg_lock:
        active = [j for j in _bg_jobs.values() if j.running]
        finished = [j for j in _bg_jobs.values()
                    if not j.running and not j.finish_announced]
        # ✅ 收工一次性信号（优先，不受 AI 活跃限频；守卫打一次即止）
        # ⚠️ 2026-09-19 修：原来这里**无条件播 `✅ 收工`**，于是被 script stop 掐掉的脚本
        #    跟自然跑完的长得一模一样（AI 不知道是自己人停的、更不知道脚本是不是崩了）。
        #    现在按"被停 / 有返回码 / 正常"分开说，别再一律报喜。
        if finished:
            j = finished[0]
            j.finish_announced = True
            dur = int((j.end_ts or time.time()) - j.start_ts)
            rc = j.returncode
            tail = "🎣 已停钓（鱼机已关、未再抛竿）" if j.name in _FISHING_SCRIPTS else ""
            if getattr(j, "killed", False):
                head = f"🛑 脚本「{j.name}」是**被停的**（不是自然跑完；跑了 {dur}s"
                head += f"，返回码 {rc}）" if rc is not None else "）"
            elif rc == 0:
                head = f"✅ 脚本「{j.name}」收工（跑了 {dur}s）"
            elif rc is None:
                head = f"💀 脚本「{j.name}」异常终止（拿不到返回码，跑了 {dur}s）"
            else:
                head = f"⚠️ 脚本「{j.name}」非正常退出（跑了 {dur}s，返回码 {rc}）"
            return f"{head}{'，' + tail if tail else ''}"
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
            f"   ✅ 可做（不打断脚本）: 整理背包(⚠️摸完立刻关，别留菜单挡脚本吃东西/动作) / 查状态看事项 / 跟{_host}聊天 / 发表情 / 截图观察\n"
            f"   ⛔ 别做（会和脚本打架）: 走位 / 挥工具 / 开商店等强菜单（查邮箱要走去信箱=走位，也算）\n"
            f"   → 做完事(没事了)就 script(ops=\"continue\") 继续睡，等下次唤醒或脚本收工\n"
            f"   → 要控制权: script(ops=\"stop\")")


def script_start(name: str, args: str = "") -> str:
    """🚀 后台启动脚本（异步不阻塞，返回 job_id；长任务用，AI 可继续聊天/看状态）。进度/收工自动播报(含总时长)、停 script stop(job_id)。⚠️一次只跑一个；跑时别用走动/挥工具同步工具，轻操作(聊天/看状态/开背包)没问题。用法→help(scripts)。

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
        f"  收工会自动播报（含总时长）   停止: script(ops=\"stop\", kw={{\"job_id\":\"{job.job_id}\"}})")


_MINE_SCRIPTS = {"mine_run", "bomb_mine", "bomb_escort", "bomb_volcano"}


def _send_home_from_mine(tries: int = 4):
    """矿类脚本被杀后，把 farmhand 送出矿井。返回送成了的落点 (loc,x,y)，或 None。
    ⚠️ 2026-09-19：**一律回读 /state 确认**——`/warp` 回包是"发了就信"，实测回包里那个
    `actual` 是**旧值**（回包写 Mine(23,8)，人其实已经在 Farm 了），照着回包播报就是谎报。
    返回 None 的两种情况：本来就不在矿井 / 试完还在矿井里（都不该说"已送回"）。"""
    dest = None
    for _ in range(tries):
        try:
            cur = api.state().get("location", {}).get("name", "")
        except Exception:
            return None
        here = _mine_exit_from_loc(cur)
        if here is None:
            return dest          # 本来就不在矿井(首次)→None；送成了(后续)→dest
        dest = here
        try:
            api._post("/warp", {"location": dest[0], "x": dest[1], "y": dest[2]})
        except Exception:
            return None
        time.sleep(1.5)
    return None                  # 试完还在矿井 → 没送成，不谎报


def _mine_exit_from_loc(loc_name: str):
    """判断 farmhand 当前是否站在矿井，是则回对应出口（复用 bomb_common.retreat_to_entrance 约定）。
    返回 (location, x, y) 或 None（不在矿井→不动）。让"主动停矿"不把 farmhand 留在矿井里。"""
    ln = loc_name or ""
    if "VolcanoDungeon" in ln:
        return ("IslandNorth", 40, 24)    # 火山矿洞出口（姜岛火山入口）
    if "SkullCave" in ln:
        return ("Desert", 8, 6)           # 头骨矿洞出口（沙漠）
    if "UndergroundMine" in ln:
        return ("Mountain", 54, 5)        # 普通矿井出口（鹈鹕镇矿井口）
    return None


def script_stop(job_id: str = "") -> str:
    """🛑 停止后台脚本任务
    终止进程（terminate → 等 3s → 不行就 kill）。不传 job_id 停最近一个在跑的。
    矿类脚本被杀后立刻把 farmhand 送回矿井口（不留在矿井——恒 2026-09-06）。

    Args:
        job_id: 便利工具/后台任务返回的任务ID
    """
    # 🔴 2026-09-12：**绝不能在持 `_bg_lock` 时调 `_with_state`** ——
    #    状态条会走 `_bg_activity_line()`，而那里也要 `with _bg_lock:`；
    #    `_threading.Lock()` **不可重入** ⇒ 同一线程自己把自己锁死。
    #    ⚠️ 死的**不是这一次调用**：锁再也放不掉，之后**每个**要拼状态条的工具都排队等它
    #    ⇒ 整个 :8000 的事件循环僵住（连 `GET /` 都不回、CPU 2.6s 不涨 = 阻塞不是死循环）。
    #    真机踩到：全工具测试批次A 打 `script ops=stop`（当时没有后台任务，走的就是这三个提前返回）。
    #    所以：锁里只**准备话**，`_with_state` 一律挪到**出锁之后**。
    early = ""
    with _bg_lock:
        if not _bg_jobs:
            early = "📭 没有后台脚本任务。"
        elif job_id:
            job = _bg_jobs.get(job_id)
            if not job:
                early = f"❌ 找不到任务 {job_id}。"
        else:
            running = [j for j in _bg_jobs.values() if j.running]
            if not running:
                early = "没有在跑的脚本（现有任务都结束了）。"
            else:
                job = running[-1]
    if early:
        return _with_state(early)
    if not job.running:
        # ⚠️ 2026-09-19 修：原来这里**提前 return、不给 tail** ⇒ 异步跑完的脚本，AI 永远只能
        #    看到"跑了 Ns"，看不到脚本自己打的"🏁 结束/撤退原因/到了几层"。现在把它带出来。
        #    （这正是"报成功但事没发生"的另一面：不知道到底干了什么。）
        how = "被停的" if getattr(job, "killed", False) else "已结束"
        body = f"任务 {job.job_id} {how}（返回码 {job.returncode}），无需停止。"
        tail = job._tail(60)
        if tail:
            body += f"\n── 最后输出 ──\n{tail[-1000:]}"
        return _with_state(body)
    is_fish = job.name in _FISHING_SCRIPTS
    is_mine = job.name in _MINE_SCRIPTS
    _bg_kill(job)
    # ⛏️ 2026-09-06 恒：停的是矿类脚本→杀完立刻看 farmhand 在哪，还在矿井就送回对应出口。
    # ⚠️ 2026-09-19 修两处：①原来这行 `api.state()` 露在 try 外面——脚本刚被硬杀时 /state 一旦
    #    超时抛异常，整个 script_stop 就炸了，而**进程已经死了**（"报错但事已发生"，连"已停止"
    #    都看不到）；②落点改成**回读确认**（见 _send_home_from_mine），不信 /warp 回包。
    mine_exit = _send_home_from_mine() if is_mine else None
    last = job._tail(60)
    body = f"🛑 已停止任务 {job.job_id} 「{job.name}」。"
    if is_fish:
        # 🎣 2026-09-05 恒：停钓鱼可小游戏中即时收杆（鱼机已关+收线），不用等一杆钓完——别再说"等收线/别操作"。
        body += "\n🎣 钓鱼已即时停止（鱼机已关、竿已收，不再抛竿）。"
    if mine_exit:
        body += f"\n⛏️ 已把 farmhand 送回矿井口（{mine_exit[0]} {mine_exit[1]},{mine_exit[2]}）——主动停矿不再留矿。"
    # 🚫 2026-08-17：原"计划当前任务→暂停"逻辑已随计划模式退役移除。
    if last:
        body += f"\n── 最后输出 ──\n{last[-1000:]}"
    return _with_state(body)


# ═══════════════════════════════════════════
#  📜 脚本/异步域（2026-09-02 恒：run_script/script_start/status/stop/async_config 五合一并入此域；09-05 删 status——收工自动播报带总时长）
#   核心=【被动异步】：便利工具白名单自动后台 + async 开关；AI 用 stop 管理、async 调白名单，不看 status。
#   start(主动后台)是兜底——长脚本优先交给便利工具(白名单自动后台)，AI 别主动手动后台。
#   坑：ops 按空格拆成多个 op，脚本名/任务id/参数须放 kw（如 script(ops="continue", kw={job_id})）。
# ═══════════════════════════════════════════
def _script_run(name: str = "", args: str = ""):
    return run_script(name, args)


def _script_start(name: str = "", args: str = ""):
    return script_start(name, args)


def _script_stop(job_id: str = ""):
    return script_stop(job_id)


def _script_async(show: bool = False, add: str = "", remove: str = "", enable: str = ""):
    return async_config(show, add, remove, enable)


def _script_continue(job_id: str = ""):
    """▶️ 确认脚本继续阻塞跑下去（不新建、不碰层数——替代旧 start 主动后台，恒 2026-09-06）。
    脚本在跑：返回确认+阻塞状态；无脚本在跑：提示走便利工具(白名单自动后台)/短任务用对应域 op。"""
    with _bg_lock:
        running = [j for j in _bg_jobs.values() if j.running]
        if not running:
            msg = "📭 没有在跑的脚本可继续。想跑脚本用对应便利工具（白名单自动后台）；短任务用对应域 op。"
        elif job_id:
            job = _bg_jobs.get(job_id)
            if not (job and job.running):
                msg = f"❌ 任务 {job_id} 没在跑。"
            else:
                elapsed = int(time.time() - job.start_ts)
                msg = f"▶️ 已确认继续：脚本「{job.name}」阻塞中（{elapsed}s，job {job.job_id}）。"
        else:
            job = running[0]
            elapsed = int(time.time() - job.start_ts)
            msg = f"▶️ 已确认继续：脚本「{job.name}」阻塞中（{elapsed}s，job {job.job_id}）。"
    return _with_state(msg)   # ⚠️ 退出 _bg_lock 后再 _with_state：_with_state→_bg_activity_line 也拿 _bg_lock，不可重入会死锁


@mcp.tool()
def script(ops: str = "", kw: dict | None = None) -> str:
    """🚀 脚本/异步域（被动异步优先）。continue 继续阻塞 / stop 停 / async 白名单。进度自动播报(收工带总时长)，无需查。全 ops+参数 → help(scripts)。⚠️跑脚本用对应便利工具域 op（farm/scene/mine/fish）——长任务白名单自动后台、短任务同步；别手动后台；参数放 kw 别拼 ops。"""
    dispatch = {
        "continue": _script_continue, "继续": _script_continue,
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
_AUTOPILOT_FAST = 2          # 🕐 姜岛挤床快线周期（秒）——窗口只有几秒，必须比 _PLAN_TICK 密（见 _autopilot_loop）
_PLAN_TIME_MARGIN = 30       # 任务 until 前多少游戏分钟还够跑？（存档）
_plan_data = plan_engine.new_plan()   # 存档：计划数据（state 恒 idle，不参与执行）
_plan_lock = _threading.Lock()
_plan_notices = []           # 计划/兜底事件 → 注入给 AI 的缓冲（兜底睡觉通知仍走这）
_fallback_busy = False       # 兜底 go_sleep 进行中防重入
_fallback_last_fail_ts = 0.0 # 兜底爬床失败时间（冷却用，防反复杀脚本+乱跑）
_FALLBACK_FAIL_COOLDOWN = 300  # 爬床失败后冷却秒数（5 分钟）——失败不重试，避免反复打断脚本


_FISHING_SCRIPTS = {"fish_run", "fair_fishing", "ice_fishing"}


def _bg_kill(job):
    """终止后台脚本进程（terminate → 等3s → 不行 kill）。
    ⚠️ 2026-09-05 恒：钓鱼脚本先 `/fishbot off` 再杀——否则直接 terminate 子进程、C# fishbot 的
    AutomationEnabled 还开着 → 停不掉、一直自动抛竿。先关鱼机自动抛、给当前竿一个收完的机会。"""
    try:
        if getattr(job, "name", "") in _FISHING_SCRIPTS:
            try:
                api._post("/fishbot", {"action": "off"})
            except Exception:
                pass
            # 🎣 停钓（2026-09-05 恒：**别用 state 轮询等收线**——鱼机连环抛竿时 isReeling 恒真、
            #   轮询空转把 stop 拖超时；且 api.state() 走 C# 主线程在鱼机狂抛时会卡）。
            #   改为快速三步：fishbot off(立刻停自动抛=不再抛下一竿，关键) → 短等给 cancel/当前竿一点时间
            #   → 补几次 cancel(竿还悬着就收一下) → 立即 kill。全程 ~3s，不阻塞。
            #   ⚠️ 小游戏中也可即时停（收线/退出），不承诺"钓完这条"——恒 2026-09-05 确认可行。
            try:
                api._post("/fishbot", {"action": "off"})   # 停自动抛竿，快且关键
            except Exception:
                pass
            time.sleep(2)   # 给当前竿收完的时间（钓完这一竿）
            try:
                for _ in range(3):
                    try:
                        api._post("/key", {"key": "cancel"})   # 竿还悬着/还在收 → 收线
                    except Exception:
                        pass
                    time.sleep(0.3)
            except Exception:
                pass
    except Exception:
        pass
    # 🛑 打上"被停"的标记——收工播报要能区分"被停"和"自然跑完"（恒 2026-09-19）。
    #    原来不打标记，被杀的脚本下一次工具调用会播报"✅ 脚本收工（跑了 Ns）"，
    #    跟正常完成一模一样，AI/恒看不出这是被掐掉的。
    job.killed = True
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


def _already_sleeping(s: dict) -> bool:
    """😴 我方**真的已经在睡**了吗（凌晨兜底据此决定"别再折腾人"）。判据=`/state.player.sleepReady.ready`。

    ⚠️ 2026-09-19 恒：「兜底反复触发……能不能检测到**入睡成功**就不用兜底重睡了？」
       —— 这段原来写着"**已在床**→不打扰"，实现却是判 `isInBed`（"脚踩床格"，`Farmer.cs:7553`）。
       于是人明明已经躺好（就绪屏没弹 or 被撤掉时），到点照样把整条流程再跑一遍：
       warp 进小屋 → 上床 → 20s → 起身出屋刷新 → 再上床……把人从床上反复折腾下来。

    ✅ **2026-09-19 根治（本批）**：改用 C# `ReadSleepReady()` =
       `ReadySynchronizer` 里 "sleep" 那条 check 的**本地 `State`**——
       "这个人**真的按过床、就绪已上报**"，正好是旧 `isInBed` 想表达却表达不了的那层：
         · 躺好但 ready 被撤/没弹 → `ready=false` ⇒ **兜底照跑**（夜本来就过不了，人就该被捞起来）；
         · 站在床边发呆（isInBed 可能为真）→ `ready=false` ⇒ 兜底照跑 ✅（旧判据会**误跳过**）；
         · 真躺好 + 已就绪（在等恒）→ `ready=true` ⇒ 跳过 ✅。
       ⇒ 旧代码那个"真躺好(B) / 站着发呆(C) 分不清"的问题**自然消失**（不再需要拿"人在床格"当证据）。

    ⚠️ 兼容窗口：老 DLL 没有 `sleepReady` 字段（`state` 缺失）→ 退回旧 `isInBed` 判据。
       这是**临时**的（DLL 一部署就走上面那条）；别把它当长期分支留着。
    """
    p = s.get("player") or {}
    sr = p.get("sleepReady") or {}
    if "state" in sr:          # 新 DLL：字段在 → 只认它（别再掺 isInBed，那会退回旧病）
        return bool(sr.get("ready"))
    return bool(p.get("isInBed"))   # 老 DLL：维持原行为


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
    # 🏝️ 姜岛"互相挤床"：**必须排在下面那个凌晨时间窗早退之前**——它跟几点无关，
    #    要等的是"房主后睡钻进来"，那一晚可能很早就躺好了。复用这里已取到的 `s`，不多打 HTTP。
    try:
        _island_squeeze_tick(s)
    except Exception:
        pass
    limit = int(_sleep_cfg.get("time", 2500))
    if not (limit <= tod < 2600):
        return
    # ⚠️ 爬床失败冷却：上次失败还没过 _FALLBACK_FAIL_COOLDOWN 秒 → 不重试
    #   （失败说明位置/条件不对，反复试只会杀脚本+乱跑）
    now = time.time()
    if _fallback_last_fail_ts and now - _fallback_last_fail_ts < _FALLBACK_FAIL_COOLDOWN:
        return
    # 已登记睡觉就绪 → 不打扰（避免重爬破坏 ready 同步）。判据见 `_already_sleeping()`。
    if _already_sleeping(s):
        return
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
    # ⚠️ **不是**每 `_PLAN_TICK` 秒才醒一次：姜岛"房主后睡"那个彩蛋的窗口只有**几秒**
    #    （房主钻进被窝 → 双方就绪 → 游戏翻页），10s 采样会整段漏掉
    #    —— 2026-09-19 真机第一晚就是这么漏的（聊天里只有游戏自己那两句，彩蛋一声没响）。
    #    拆两层：**每 2s** 一条轻量快线（只做姜岛挤床，`maybe` 闸门挡住平时空转），
    #    **每 `_PLAN_TICK`** 秒才跑一次完整的 🌙 兜底睡觉。
    last_full = 0.0
    while True:
        _island_squeeze_fast()
        now = time.time()
        if now - last_full >= _PLAN_TICK:
            last_full = now
            try:
                _autopilot_tick()
            except Exception:
                pass
        time.sleep(_AUTOPILOT_FAST)


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
    """🧬 看当前角色技能等级 + 职业分支(professions)——比如是不是 Luremaster(蟹笼免饵)。
    2026-09-11 起收进 check 域：`check(what="profile")`（不再占顶层工具槽）。"""
    try:
        # ⚠️ 2026-09-16 修「读错人」：原来走 `_get`（BASE_URL=**7842 恒**）⇒ 查的是**房主**的技能，
        #    可 C# 路由注释白纸黑字写着「(2026-08-30 恒:AI 看自己)」，下文还拿它判"你是不是 Luremaster、
        #    蟹笼免不免饵"——那全是 **AI 自己的**农活。跟 `/mastery` 是同一类 bug（读全局 ≠ 读自己）。
        #    一直没被发现是因为**两边技能都 10 级**、数字长得一样；职业分支/精通数据才露馅。
        r = api._ai_get("/profile")
        if not r.get("ok"):
            return _with_state(f"❌ {r.get('error', '读取失败')}")
        name = r.get("name") or "?"
        sk = r.get("skills") or {}
        lines = [
            f"🧬 {name} 技能等级:",
            f"  农{sk.get('farming')} | 渔{sk.get('fishing')} | 采集{sk.get('foraging')} | 矿{sk.get('mining')} | 战{sk.get('combat')}",
        ]
        # 🎓 精通进度（2026-09-16 恒：并进"看技能等级/分支"的地方）。
        #    **没开精通就不显示** —— 开启条件 = 五项技能全部 10 级（游戏 `MasteryHint` 的触发条件）；
        #    在那之前 `MasteryExp` 根本不涨，画个空条只会误导。
        if all(int(sk.get(_k) or 0) >= 10 for _k in _MASTERY_SKILLS):
            lines.append(_mastery_brief())
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
        return _with_state("\n".join(lines))
    except Exception as e:
        return _with_state(f"❌ profile: {e}")


# ═══════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════

# 🔒 域工具模式 keep-set（2026-08-22 恒：默认开启，省 token + 测域工具；`--full` 已退役 2026-09-06）
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
    "screenshot", "help",
    # 🗜️ 2026-09-11 恒拍板 20→17：三个"能用域路到达"的顶层工具收编（省 schema + 去掉重复路）——
    #   advance_story → menu ops="advance"（menu 的 dispatch 本就直指同一函数，留着=两条路做同一件事）；
    #   profile      → check(what="profile")、"which_role" → check(what="role")（都是"查我自己"，归查询域）。
    #   ⚠️ 三者函数照旧注册、只是不给 AI 直调；**收编时必须同步改引导文案**，否则 AI 照旧文案调隐藏名=当场卡死：
    #     · advance_story 自己返回的"再调 advance_story"→"menu advance"（含 menu_click→menu click）
    #     · 状态条 🎬 剧情行尾补"→ menu advance 继续"、menu 域 docstring 点名 advance
    #     · check 域 docstring/help 点名 profile/role；fish 域 guide 点名 crab 前先 check profile
    #   domain_selftest 的"无断档"检查会兜住（三个都能被域 op 到达，无需进 _KNOWN_SUBSUMED）。
}


# ── 🧭 导航注入（navigation.py 需要宿主的状态条 / 计划通知 / 节日域）──
#    ⚠️ 位置**只能**在这（文件最末尾）：注入项里 `_plan_notify` 定义得最晚（本文件 15000 行往后），
#       而 `_STATE_SEP` 定义在 8000 行左右 —— 别"紧跟依赖"地塞在 `_STATE_SEP` 后面，
#       那样 `_plan_notify` / 两个节日常量会被注入成未绑定，且**只在你卡墙 / 走节日路径时才炸**。
#    ⚠️ 全按**引用**传（不是复制）：`_with_state` 内部读本模块的 `_OPS_INNER["n"]` 决定
#       "变化才报"的注入闭不闭嘴，复制一份就分叉（= 2026-09-11「域 op 内嵌状态条」那个坑）。
navigation.bind(
    with_state=_with_state,
    plan_notify=_plan_notify,
    state_sep=_STATE_SEP,
    festival_poi_active=_festival_poi_active,
    festival_only_maps=_FESTIVAL_ONLY_MAPS,
    festival_temp_maps=_FESTIVAL_TEMP_MAPS,
    fest_season_cn=_FEST_SEASON_CN,
    mark_festival_poi_name=_mark_festival_poi_name,
)


if __name__ == "__main__":
    import sys

    use_stdio = "--stdio" in sys.argv

    # 🔒 域工具模式（2026-08-22 恒：domain_only 恒开；`--full`/NAGI_FULL_TOOLS 已退役 2026-09-06）
    #   只暴露 _KEEP_TOOLS 白名单，隐藏其余独立工具（域内部仍调它们，只是不给 AI 直调）。
    if True:
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
            print(f"  🔒 域工具模式（恒开）：隐藏 {_hidden} 个，只留 {len(mcp._tool_manager.list_tools())} 个（{len(_KEEP_TOOLS)} 白名单）", file=sys.stderr)
        except Exception as e:
            print(f"  ⚠️ 域工具过滤失败（继续全量）: {e}", file=sys.stderr)

    # 🚫 2026-08-17 计划模式已退役：不再 _plan_load_state()（不恢复 plan.json）。
    #    后台 daemon 只跑 🌙 兜底自动睡觉（_fallback_tick），计划状态机已注释（见 _autopilot_tick）。
    try:
        _threading.Thread(target=_autopilot_loop, args=(), daemon=True).start()
    except Exception:
        pass

    # 🎪 节日哨兵（2026-09-12）：独立线程按 2s 轮询 /event_state，限时小游戏一开赛就接管
    #    （自动 hook 只在工具调用边界跑，长调用在飞时它看不见开赛——冬钓节那场就是这么错过的）
    try:
        _threading.Thread(target=_festival_sentinel_loop, args=(), daemon=True).start()
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
        #    游戏未开会注明，运行时会自动检测对齐（见 _build_state_strip 的热路径重探）。
        # 🔴 2026-09-11：**solo（折叠成单进程）必须大声报**，不能混在正常行里 ——
        #    那天就是在"房主已进世界、farmhand 还没加入"的窗口重启了服务，两个端口都落 7842，
        #    `cabin statue` 于是把恒的角色走掉了、还摸了他雕像。单单一行 `AI(恒)=7842 | host(恒)=7842`
        #    我没看出来（名字重复就是征兆）。
        try:
            _roles = api.ensure_roles()
            if _roles.get("ok"):
                _a, _h = _roles["ai"], _roles["host"]
                print(f"  ✅ 角色映射: AI({_a.get('name','?')})={_a['port']} | host({_h.get('name','?')})={_h['port']}")
                if _roles.get("solo"):
                    # 🔴 solo 的两种解读要分开讲（2026-09-12 恒：对着单人游玩的人喊"危险"是虚惊）——
                    #    单人游玩时折叠本属正常；多人世界时才是"打错人"。提醒里必须把分叉讲清楚。
                    print()
                    print("  " + "!" * 64)
                    print("  ⚠️⚠️  特别提醒：单进程折叠（AI 与 host 是同一个进程）")
                    print(f"       目前 AI 端口和 host 端口都指向 {_a['port']}（房主）。")
                    print("       · 若本意就是 AI 单人游玩：世界上只有一个角色，这是正常状态，")
                    print("         忽略本提醒即可（该角色同时充当房主与农场工）。")
                    print("       · 若是多人世界（房主 + farmhand 两个进程）：这是危险状态 ⇒")
                    print("         所有『AI 操作』都会静默打在房主身上（走位/用工具/丢东西全算）。")
                    print("         多半是 farmhand 还没进世界就重启了本服务 ⇒")
                    print("         **等它进世界后重启本服务**即可（热路径每 30s 重探，")
                    print("         两进程都在时会自动纠正，但纠正前那 30s 仍可能打错人）。")
                    print("  " + "!" * 64)
                    print()
            else:
                print(f"  ⏳ 游戏进程未就绪（{_roles.get('error','?')}），运行时会自动检测")
        except Exception:
            print("  ⏳ 角色自动检测暂不可用，运行时会自动检测")
        print()
        mcp.run(transport="streamable-http")
