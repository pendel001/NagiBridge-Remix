# -*- coding: utf-8 -*-
"""🚫 **自验不许出网** —— 一道「默认拒绝」的机械闸（2026-10-01 立）。

## 为什么立这道闸（案情；两次都是同一个形状，所以不再靠"记得"）

1. **2026-10-01 晚**：`_intent_wiring_selftest.py` 自称"不吃游戏"，实际**打到真机**了 ——
   链路是 `intent do` → `_im_chest_op` → **`_walk_to_chest`**：
     · 它先 `api.state()`（走 `_get` → **真游戏**，测试只桩了 `_ai_get`/`_ai_post`）；
     · 再 `navigation._walk_and_wait(...)`（**真走位**）；
     · 走位炸了就兜底 **`api.position(x, y)` = `POST /position`（瞬移）**。
   ⇒ 那一发把 AI 角色（轮回）从 `Farm (48,42)` 搬到了 `Farm (11,12)`（夹具里箱子的坐标 + 真实地图）。
   ⚠️ **直连 :7842/:7843 不经 MCP** ⇒ 症状是"**MCP 工具日志里一次调用都没有，人却换了地方**"，
      最容易被当成"游戏自己动的"。定位只好靠拦在 `socket.connect` 上打栈。
2. 更早：`_sit_selftest` 也被记过一条「自称不打游戏、其实偷偷依赖游戏在场」。

⇒ 结论：**出口本身要上锁**，而不是让人记得别调。

## 它怎么上闸（默认开）

`stardew_api` 在 **import 期**调 `maybe_arm()`：只有 `sys.argv[0]` 的文件名匹配 `*selftest*.py`
才上闸 —— MCP 服务器（`nagi_mcp_server.py`）、AI 日常脚本、`mcp_cli.py` 的 argv[0] **不匹配**
⇒ 他们**一个字都不受影响**。想临时关掉：`NAGI_NET_GUARD=0`。

## 白名单 = **默认拒绝**

不在 `ALLOW` 里的目标端口连接**一律拒绝**，并且**记一笔（带仓库内调用栈）**。
表里每一项都写清"为什么它必须联网"（人核过、可反驳）。新增一项＝一次人工决定，
不是"加一行让它过"。账在 `LEDGER`（人读的版本），`_net_guard_selftest.py` 会核两张表对齐。
"""
import os
import socket
import sys
import traceback

# 游戏端口：**操作打 7843（AI）/ 检测广播打 7842（host）**——本项目的端口铁律。
GAME_PORTS = (7842, 7843)

# ── 白名单（**默认拒绝**）────────────────────────────────────────────────
# ⚠️ 键 = 自验文件名；值 = **为什么它必须联网**（一句话，人能核）。
#    加进来之前先问：这一发到底是"读"还是"写"？写的一律不许进（要改成打桩）。
ALLOW = {
    "_async_block_selftest.py":
        "只读：拼状态条要 `_host_name()` → `stardew_api.host_state`（GET 7842 问房主叫什么）",
    "_chest_walk_selftest.py":
        "只读：`_build_state_strip` → `navigation.map_feature_hidden` → `unlock_status`（GET /unlocks）",
    "_peer_econ_selftest.py":
        "只读：同上（`unlock_status` + `day_key` 的 `/state`）",
    "_railroad_gate_selftest.py":
        "只读：同上（`unlock_status` + `day_key`）",
    "_screenshot_gate_selftest.py":
        "只读：`_map_go_unlock_check` / `_locked_maps` 要读解锁与当日 key",
    "_state_inner_selftest.py":
        "只读：`_with_state` → `ensure_roles` → `detect_roles` → `_probe_role`（只 connect_ex 探"
        "「哪个端口是谁」，不写世界）",
}

# ── 账（人读的版本；`_net_guard_selftest` 会核它与 ALLOW 对齐）─────────────
# (自验文件, 触发那一发的仓库内行/链, 读还是写, 说明)
LEDGER = [
    ("_async_block_selftest.py", "_async_block_selftest.py:57 → _bg_wake_text → _host_name → host_state",
     "读", "GET /state（host 7842）只为拿房主名字"),
    ("_chest_walk_selftest.py", "_chest_walk_selftest.py:105 → storage_store → _state_suffix → _locked_maps → unlock_status",
     "读", "GET /unlocks 7843"),
    ("_peer_econ_selftest.py", "_peer_econ_selftest.py:225 → _build_state_strip → _locked_maps → unlock_status",
     "读", "GET /unlocks 7843 + /state（day_key）"),
    ("_railroad_gate_selftest.py", "_railroad_gate_selftest.py:73 → _build_state_strip → _locked_maps → unlock_status",
     "读", "GET /unlocks 7843 + /state（day_key）"),
    ("_screenshot_gate_selftest.py", "_screenshot_gate_selftest.py:86 → _state_suffix → _locked_maps / _map_go_unlock_check",
     "读", "GET /unlocks 7843 + /state（day_key）"),
    ("_state_inner_selftest.py", "_state_inner_selftest.py:67/79 → _with_state → ensure_roles → detect_roles → _probe_role",
     "读", "connect_ex 7842+7843 探角色（不写世界）"),
    # ⛔ 已封（**不再联网**，所以**不在** ALLOW 里 —— 这条留着记账，别删）：
    #     `_intent_wiring_selftest.py`：原来 `intent do` 会走真 `_walk_to_chest`
    #     （读真 `/state` + 真走位 + 兜底 `POST /position` 瞬移），把角色搬去 Farm(11,12)。
    #     2026-10-01 已在 `_stub()` 里把 `_get`/`_post`/`_walk_to_chest`/`_walk_and_wait`/`api.position`
    #     全部接上桩 ⇒ 现在**零命中**（闸会盯着它）。
    # ⛔⛔ 2026-10-02 **这个闸自己漏了第二种形状**（恒真机又逮到一次「它怎么自己又跑起来了」）：
    #     同一份自验里 `M._im_run("pickup_scene", {})` 是**真执行** ⇒ `_run_script` 起了**真子进程**
    #     `pickup_scene.py` ⇒ 直连 7843 把农场地上 7 个松露全捡了（跑两遍共 18 发 walk/face/interact）。
    #     ⚠️ **为什么闸没拦住**：闸是"谁 import 我、谁上闸"，而 `pickup_scene.py`/`feed_hay.py`
    #     这类脚本**根本不 import `stardew_api`** ⇒ `maybe_arm()` 一次没跑；它们还用**裸 `requests`**
    #     ⇒ `_game_calls.log` 也一条不记；也不是 MCP 工具调用 ⇒ 会话日志只剩"某条断言过了"。
    #     ⇒ 补的堵法在 **spawn 出口**：`nagi_mcp_server._net_guard_refuse_spawn()` ——
    #     本进程已被闸罩住时，`_run_script`/`_bg_start` **一律拒绝起真脚本**（会说清该打哪个桩）。
    #     ⇒ **教训**：闸只罩"直接连接"是不够的，**凡是能把世界交给另一个进程的路，都得单独罩**。
]

HITS = []          # 被拒/被放行的连接（每条带仓库内调用栈）
ARMED = False
_ORIG = {}


def _proj_frames():
    """只留**仓库内**的调用帧（库帧占满整条栈 = 等于没定位到是哪一行，2026-10-01 踩过）。"""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/ 的上一级
    out = []
    for fr in traceback.extract_stack()[:-2]:
        fn = (fr.filename or "").replace("\\", "/")
        if "_net_guard.py" in fn:
            continue
        if fn.lower().startswith(here.replace("\\", "/").lower()):
            out.append("%s:%d in %s" % (fn.split("/")[-1], fr.lineno, fr.name))
    return out[-12:]


def _game_port(addr):
    try:
        return int(addr[1])
    except Exception:
        return None


def _guard(kind, addr):
    if not ARMED:
        return
    port = _game_port(addr)
    if port not in GAME_PORTS:
        return
    who = os.path.basename(sys.argv[0] or "?")
    allowed = who in ALLOW
    HITS.append({"file": who, "port": port, "kind": kind,
                 "allowed": allowed, "stack": _proj_frames()})
    # 📝 想留账就设 `NAGI_NET_GUARD_LOG=<文件>`（每条一行 JSONL）——
    #    "跑一遍全量、把触网的文件+行号列成一张表"用的就是它，别靠人肉抄。
    _logp = os.environ.get("NAGI_NET_GUARD_LOG")
    if _logp:
        try:
            import json
            with open(_logp, "a", encoding="utf-8") as f:
                f.write(json.dumps(HITS[-1], ensure_ascii=False) + "\n")
        except Exception:
            pass
    if allowed:
        return                      # 白名单里的"只读探测"放行（但记一笔）
    raise ConnectionRefusedError(
        "🚫 自验不许出网：%s 想连游戏端口 %s（%s）。"
        "要么打桩，要么把理由写进 scripts/_net_guard.py 的 ALLOW（默认拒绝）。" % (who, port, kind))


def arm():
    """上闸（幂等）。⚠️ 只换**出口**，不动任何业务逻辑。"""
    global ARMED
    if ARMED:
        return True
    _ORIG["connect"] = socket.socket.connect
    _ORIG["connect_ex"] = socket.socket.connect_ex
    _ORIG["create_connection"] = socket.create_connection

    def _connect(self, addr):
        _guard("connect", addr)
        return _ORIG["connect"](self, addr)

    def _connect_ex(self, addr):
        _guard("connect_ex", addr)
        return _ORIG["connect_ex"](self, addr)

    def _create_connection(addr, *a, **k):
        _guard("create_connection", addr)
        return _ORIG["create_connection"](addr, *a, **k)

    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.create_connection = _create_connection
    ARMED = True
    return True


def maybe_arm():
    """`stardew_api` 在 import 期调它：**跑在自验里**才上闸。

    ⚠️ 判据是 `sys.argv[0]` 的文件名（`*selftest*.py`）——**不是**"谁 import 了 stardew_api"：
       MCP 服务器、AI 日常脚本都会 import 它，那些**一次都不许被挡**。
    🔒 2026-10-02 恒拍板：再加一道 **`NAGI_NET_GUARD_FORCE=1`** —— **无论文件名一律上闸**。
       为什么需要它：**安全不变量必须能证伪**。全量 runner 会跑两类"名字里没有 selftest"的
       脚本（`EXTRA`：`intent_menu.py` / `domain_selftest.py` / `_guide_orphan_check.py` /
       `_kw_doc_check.py` / `_cn_quote_check.py`）—— 按文件名判，它们**根本不上闸**，
       哪天它们里有人手滑打了 7842/7843，谁都不会知道。
       ⇒ runner（`_run_all_selftests.py --no-net`）给**每个子进程**都设这个变量。
       （`NAGI_NET_GUARD=0` 仍然是"整条关掉"的总闸，优先于本项 —— 那是给排查用的。）
    """
    if os.environ.get("NAGI_NET_GUARD", "1") == "0":
        return False
    if os.environ.get("NAGI_NET_GUARD_FORCE") == "1":
        return arm()
    who = os.path.basename(sys.argv[0] or "")
    if not who.endswith(".py") or "selftest" not in who.lower():
        return False
    return arm()


def violations():
    """被**拒**的那些（白名单放行的不算）—— 闸红了就看它。"""
    return [h for h in HITS if not h["allowed"]]


def allowed_hits():
    """白名单里放行的那些（= LEDGER 那本账）。"""
    return [h for h in HITS if h["allowed"]]
