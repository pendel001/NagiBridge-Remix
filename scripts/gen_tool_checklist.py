# -*- coding: utf-8 -*-
"""📋 全工具测试清单生成器（2026-09-11）

**从哪来**：op 清单抄自**各域 dispatch 表**（AI 真能调到的东西），**不是**抄文档。
文档只提供"参数"一列（复用 `_kw_doc_check.py` 的解析器）——所以清单本身不会因为文档写错而漏项。

**怎么用**（三步）：
    python gen_tool_checklist.py                 # ① 生成/刷新清单（判定列不动，可反复跑）
    （……跑测试，任何渠道都行，只要经过 :8000 的 MCP 就会落进 session_log.jsonl……）
    python gen_tool_checklist.py --from-log      # ② 按日志自动打勾 + 填证据（日志行号）
    （手动补 ⏭ / ❌ 的判定；再跑 --from-log 不会覆盖你写的 ⏭）

**判定规则**（`--from-log` 用，故意从严——**假绿比漏测危险**）：
  一次调用算"过"要同时满足：`err=0`、返回不以 `❌` 开头、正文里没有 `❌ op「`、
  **没有 `⚠️ op「`**（= 参数名被静默丢掉，看着成功其实没生效）、没有 `Traceback`。
  不满足就记成 `❌`（并保留当时的返回片段当证据，方便回头看）。

⚠️ **清单在测试开始前就该冻结**：`--from-log` 只写"判定/证据"两列，**永远不动 op 列表**。
   如果它报"日志里出现了清单外的 op"，说明有人改过 dispatch —— 那是要单独查的事，不是勾一下。
"""
import io
import json
import os
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M          # noqa: E402
import _kw_doc_check as K            # noqa: E402  复用它的文档解析器（同源，别另写一份）

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(SCRIPT_DIR, "TOOL_TEST_CHECKLIST.md")
LOG_PATH = os.path.join(SCRIPT_DIR, "session_log.jsonl")

# ── 分层（**建议层**，恒可随时改；改这里比改表格靠谱，重跑就刷新）──
# T1 = 只读/幂等：不改游戏状态，可以无脑自动扫
T1_TARGETS = {
    "check_status", "check_backpack", "check_worn", "machine_report", "check_mine_progress",
    "silo_status", "mastery_status", "building_list", "scan_chests", "storage_layout",
    "look_around", "profile", "which_role", "check_friendship",
    "map_lookup", "map_query", "help", "screenshot",
    "theatre_knowledge", "bundle_kb", "fish_info_kb",
    # ⚠️ `bundle_status` **不能**放这儿（2026-09-11 真机踩到）：名字像查询，实现里却
    #    `walk_to("社区中心(献祭大厅)")` —— 一次"只读扫"把 AI 跨 3 张图搬到 CommunityCenter、
    #    丢在锅炉房板前 55 秒。它归 T2（要摆场/会搬人），别按名字判只读，见 audit_tier()。
    "settings_status", "session_status", "bomb_status", "crab_status",
    # ⚠️ `plot_plan` 同样**不能**放这儿：docstring 自己写着「离玩家远会**自动瞬移过去**再扫」，
    #    `api.position(x, y)` 是瞬移。我批量扫时没传 x/y（默认用当前位置）所以没动 —— 那只证明
    #    "不带参数不动人"，**不是"只读"**。带 x/y 调就会传送 AI，见 audit_tier()。
    #    （同理：批量扫的"✅"只覆盖了**我传的那组参数**，别读成"这个 op 安全"。）
    "bomb_plan", "forge_guide", "seats", "scan_furniture_cabin",
    "storage_view", "storage_find", "storage_layout",   # 看箱/找物都是纯读，不动东西
}
# T1 的名字特征（纯查询类，加新查询 op 时不用回来登记）
T1_PAT = re.compile(r"^(list_|scan_|check_)|(_status|_info|_ref|_plan|_lookup|_query)$")
# T3 = 有副作用 / 不可逆 / 花资源，得人在场点头
T3_PAT = re.compile(
    r"(drop|sell|_bin|buy_|give_|gift|hand_|send_chat|craft|forge|geode|donate|_store|_take"
    r"|storage_default|storage_tag|_retire|_reactivate|set_|appearance|customize|levelup"
    r"|display_fill|read_book|cook|eat_item|wear|lie_bed|set_pause|whiteboard|wb_pin|wb_clear"
    r"|_stop|_continue|_export|_start|appearance|place_item|break_tile|bomb_place|bomb_volcano"
    r"|claim|discard|sleep|settle|emote)")
# 再补几个正则抓不到、但确实会动状态的（宁可往上挪一档，别让人以为能随便跑）
T3_TARGETS = {"use_tool", "menu_click", "crab_place", "crab_bait", "sit_in", "face_dir",
              "select_item", "pet_pet", "pet_walk", "pet_water", "moss_run", "berry_run",
              "spot_run", "pan_gold", "rock_dig", "garbage_can", "pickup_scene",
              "maze_walk", "take_off", "wear"}
# ⏭ 当期不可用（节庆日专属 —— **默认不勾，标出来让人确认**）
#   ⚠️ 别整个 festival 域都算 ⏭：`today`/`next`/`info`/`help` 是**天天都能查**的知识/日历查询，
#   把它们划进"测不了"会白丢一截覆盖（第一版就是这么划的，55 个里只有约 40 个真需要节庆日）。
SKIP_FESTIVAL = {
    "go", "interact", "answer", "shop", "eggs", "egg_run", "egg_note", "dance",
    "strength", "ice_fish", "maze", "maze_walk", "display_fill", "display_takeback",
    "poi", "prep",
    # 中文别名
    "去", "互动", "应答", "商店", "找蛋", "捡蛋", "记", "纸条", "舞", "跳舞", "邀请",
    "测力", "力量", "冰", "冰钓", "迷宫", "迷宫走", "展位放", "展位收", "放满", "收好",
    "限定", "备战", "厨师", "实况",
}


# ⛔ 名字像查询、**实现却会动角色**的。光把它们从 T1_TARGETS 里删掉没用 ——
#    `T1_PAT` 会把它们原样抓回来（`bundle_status` 命中 `_status$`、`plot_plan` 命中 `_plan$`，
#    2026-09-11 第一次改就踩了这个：改完重跑，两个还是 T1）。所以要有个**优先于正则**的排除集。
#    ⚠️ 进这个集合的唯一理由 = 目标函数**源码里真动了角色**（`audit_tier()` 查得出来），不是"我觉得"。
T1_EXCLUDE = {
    # （`bundle_status` 2026-09-11 晚已改读 C# `/bundles` 端点，**真的**只读了，不再需要排除 ——
    #   它一度在这张表里，因为当时实现是 walk_to 跨图去社区中心 + api.position 站板前、实测 55 秒。）
    "plot_plan",       # api.position 瞬移过去再扫（docstring 自己写着"离玩家远会自动瞬移"）
}


def _tier(domain: str, op: str, target: str) -> str:
    t = target or ""
    if domain == "festival":
        return "⏭" if op in SKIP_FESTIVAL else "T1"   # 剩下的都是天天能查的日历/知识
    if t in T1_EXCLUDE:                              # ⛔ 排除集优先于一切"名字像查询"的规则
        return "T3" if (t in T3_TARGETS or T3_PAT.search(t)) else "T2"
    if t in T1_TARGETS or T1_PAT.search(t):
        return "T1"
    if t in T3_TARGETS or T3_PAT.search(t):
        return "T3"
    return "T2"


# ── 分层体检：「名字像只读」≠「实现只读」 ──
# ⚠️ 2026-09-11 真机踩到：`bundle_status` 名字像查询、还被手写进了 T1_TARGETS，
#    实现里却 `walk_to("社区中心(献祭大厅)")` + `api.position(...)` —— 一次"只读扫"
#    把 AI 从 Farm 跨 3 张图搬到 CommunityCenter、对 4 块板逐个 interact、最后丢在锅炉房板前。
#    **T1 是给"可以无脑自动扫"用的档**，判错的代价是悄悄改游戏状态，比漏测更糟。
#    所以别信名字，去目标函数的**源码**里找"动角色"的调用。
_MOVE_CALLS = (
    "walk_to(", "map_go(", "move_to_tile(", "warp_safe(", "_go_home(", "_try_transport(",
    "_obelisk_go(", "_minecart_go(", "_minecart_route_go(", "_step_into_building(",
    "_walk_trigger_warp(", "api.position(", "api.face(", "api.walk_to(", "_go_to_bed(",
)


def audit_tier() -> list:
    """[(域, op, 命中的调用…)]：被判 T1 却会动角色的。

    只看**去注释后的源码**：`bundle_status` 的 docstring 里本来就在讲"导航到社区中心"，
    连注释一起搜会把"只是提了一嘴"的也报进来。"""
    import inspect
    out = []
    for r in _rows():
        if r["tier"] != "T1" or r["alias_of"]:
            continue
        fn = getattr(M, r["target"] or "", None)
        if not callable(fn):
            continue
        try:
            src = inspect.getsource(fn)
        except Exception:
            continue
        body = "\n".join(ln for ln in src.split("\n") if not ln.lstrip().startswith("#"))
        hits = sorted({c for c in _MOVE_CALLS if c in body})
        if hits:
            out.append((r["domain"], r["op"], hits))
    return out


def _params_of() -> dict:
    """(域, op) → 文档里写的参数名（可能空）。直接借 _kw_doc_check 的解析结果。"""
    out = {}
    for d in K.DOMAINS:
        for ops, params in K.doc_groups(d):
            real = sorted(p for p in params if p not in K._ALLOW)
            for o in ops:
                out.setdefault((d, o), set()).update(real)
    return out


def _rows() -> list:
    """清单行：(域, op, 目标函数, 参数, 层)。ops 取 dispatch 的**字符串 key**（含中文别名）。

    🔖 **别名标记**：同域里两个 op 指向同一个目标函数（`chests` 和 `箱子`）→ 后者标 `≡chests`。
    中文别名占了全表约四成，全测一遍纯属重复劳动；标出来，实测时挑一个即可。"""
    params = _params_of()
    rows = []
    for d in K.DOMAINS:
        tmap = K._targets_of(d)
        by_target = {}
        for op in sorted(K._ops_of(d)):
            by_target.setdefault(tmap.get(op, ""), []).append(op)
        for op in sorted(K._ops_of(d)):
            tgt = tmap.get(op, "")
            peers = by_target.get(tgt, [op])
            alias_of = peers[0] if (len(peers) > 1 and op != peers[0]) else ""
            rows.append({
                "domain": d, "op": op, "target": tgt, "alias_of": alias_of,
                "params": ", ".join(sorted(params.get((d, op), []))),
                "tier": _tier(d, op, tgt),
                "verdict": "", "evidence": "",
            })
    return rows


# ── 从既有清单里回读"判定/证据"（保住人工写的 ⏭ 和注释）──
# ⚠️ 键必须是 (域, op) 而不是 op：`place` / `break` / `find` 这些名字**在多个域里都存在**，
#    只按 op 存会互相覆盖（第一版就会把 scene 的判定写到 farm 头上）。
_ROW_RE = re.compile(r"^\|\s*`([^`]+)`[^|]*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$")
_H2_RE = re.compile(r"^##\s*`([a-z_]+)`")


def read_existing() -> dict:
    if not os.path.exists(OUT_PATH):
        return {}
    out, dom = {}, None
    for ln in open(OUT_PATH, encoding="utf-8"):
        h = _H2_RE.match(ln)
        if h:
            dom = h.group(1)
            continue
        m = _ROW_RE.match(ln.rstrip())
        if not (m and dom):
            continue
        op, _tier, _params, verdict, ev = (x.strip() for x in m.groups())
        out[(dom, op)] = {"verdict": verdict, "evidence": ev}
    return out


# ── 按日志打勾 ──
# ⚠️ 只认**结构化**的失败标志，别见 `❌` 就判红：工具正文里本来就会出现 `❌`——
#    `menu recipes` 返回 "❌ 缺材料（78 个）" 是在**告诉 AI 缺什么**，不是它自己失败了。
#    （第一版就是这么误判的：17 个 ✅ 里 2 个被冤枉成 ❌。）错误一律打在**首行**，
#    所以 `startswith` 判整体失败、「❌ op「」判单个 op 失败，这两条够了。
_BAD_PAT = (
    "❌ op「",        # 单个 op 抛错 / 参数错（_ops_run 的格式）
    "❌ 未知操作「",   # op 名不存在
    "❌ 未知查询「",   # check 的 what 不存在
    "❌ ops 为空",
    "⚠️ op「",        # 参数名被静默丢掉 —— 看着成功其实没生效，**必须算 ❌**
    "Traceback",
)


def judge_from_log() -> dict:
    """扫 session_log.jsonl → {(工具, op): (判定, 证据)}。
    键带**工具名**（= 域名）而不是裸 op：`place` 这种名字 farm/scene/cabin 三个域都有。
    **最后一次调用说了算**（证据也就是最后那次的行号）。别做成"❌ 一旦出现就粘住"——
    实测中被粘住的正是"先踩到 bug、修完再跑就过了"的那些 op，表上会留下一堆已经修好的红。
    真要看反复，翻 session_log 原文件比看这张表准。"""
    res = {}
    if not os.path.exists(LOG_PATH):
        return res
    for i, ln in enumerate(open(LOG_PATH, encoding="utf-8"), 1):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        raw_ops = (r.get("ops") or "").strip()
        if not raw_ops:
            continue
        tool = r.get("tool") or ""
        ret = r.get("ret") or ""
        bad = (r.get("err") or ret.startswith("❌") or any(p in ret for p in _BAD_PAT))
        for op in re.split(r"[\s,，]+", raw_ops):
            if not op:
                continue
            if bad:
                res[(tool, op)] = ("❌", f"`session_log:{i}` {ret[:60].replace(chr(10), ' ')}")
            else:
                res[(tool, op)] = ("✅", f"`session_log:{i}`")
    return res


def write(rows: list, from_log: bool):
    old = read_existing()
    logres = judge_from_log() if from_log else {}
    for r in rows:
        key = (r["domain"], r["op"])
        v, ev = logres.get(key, ("", ""))
        if v:
            r["verdict"], r["evidence"] = v, ev
        elif old.get(key, {}).get("verdict"):
            # 人工写的（⏭ / 手测结论）优先于"没测到"——但**压不过日志里的 ❌**（上面那个分支）
            r["verdict"] = old[key]["verdict"]
            r["evidence"] = old[key]["evidence"]

    tier_cn = {"T1": "T1 自动", "T2": "T2 摆场", "T3": "T3 副作用", "⏭": "⏭ 当期不可用"}
    stat = {}
    for r in rows:
        stat[r["tier"]] = stat.get(r["tier"], 0) + 1
    done = sum(1 for r in rows if r["verdict"] == "✅")
    fail = sum(1 for r in rows if r["verdict"] == "❌")

    L = []
    L.append("# 🧪 全工具测试清单（2026-09-11 生成）\n")
    L.append(f"> **{len(rows)} 个 op**（抄自各域 dispatch，非文档）｜✅ {done} ｜❌ {fail} ｜"
             f"{' ｜'.join(f'{tier_cn.get(k, k)} {v}' for k, v in sorted(stat.items()))}\n")
    L.append("> 用法：跑测试（任何渠道，只要走 :8000 的 MCP）→ `python gen_tool_checklist.py --from-log` 自动打勾。")
    L.append("> 判定规则见脚本头部；**⚠️ op「」= 参数被静默丢掉，算 ❌ 不算 ✅**。")
    L.append("> ⚠️ 本表**测试前冻结**：`--from-log` 只写后两列，永不动 op 列表。\n")

    for d in K.DOMAINS:
        sub = [r for r in rows if r["domain"] == d]
        if not sub:
            continue
        L.append(f"\n## `{d}`（{len(sub)}）\n")
        L.append("| op | 层 | 参数 | 判定 | 证据 |")
        L.append("|---|---|---|---|---|")
        for r in sub:
            opname = f"`{r['op']}`" + (f" ≡`{r['alias_of']}`" if r.get("alias_of") else "")
            L.append(f"| {opname} | {tier_cn.get(r['tier'], r['tier'])} | {r['params']} | "
                     f"{r['verdict']} | {r['evidence']} |")
    # ⚠️ 分层体检：T1 却会动角色的 —— 必须写在**文档里**让人看见，不能只在脚本里跑一下就完
    #    （"别把红的检查记成既有的误报长期跳过"）。这里报出来的是"名字档 vs 源码行为"打架。
    # ⚠️ 空也要写出来：写"无"才证明**这一轮真查过**。不写的话，读者分不清
    #    "检查通过" 和 "压根没跑这个检查"——那正是"把红的检查记成误报长期跳过"的开头。
    sus = audit_tier()
    L.append("\n## ⚠️ 分层存疑（判成 T1，但目标函数会动角色）\n")
    L.append("> **别直接按 T1 无脑扫这些** —— 先人工确认归哪层。判据=目标函数源码里出现下列调用。\n")
    if sus:
        L.append("| 域.op | 命中的动角色调用 |")
        L.append("|---|---|")
        for _d, _op, _hits in sus:
            L.append(f"| `{_d}.{_op}` | {', '.join(_hits)} |")
    else:
        L.append(f"✅ 无。（只查了 `_MOVE_CALLS` 列的那几种调用 —— 这是**没发现**，不是**证明没有**。）")

    L.append("\n---\n\n## 📐 图例\n")
    L.append("- **T1 自动**：只读/幂等，随时可跑（建议先全扫一遍）")
    L.append("- **T2 摆场**：要 AI 站在对的地方、或当前图/季节合适")
    L.append("- **T3 副作用**：会改状态/花资源/不可逆，**要恒在场点头**")
    L.append("- **⏭ 当期不可用**：节庆日专属/需解锁 —— **不算没测，是测不了**，别打勾\n")

    open(OUT_PATH, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return len(rows), done, fail, stat


def main():
    rows = _rows()
    if "--audit-tier" in sys.argv:
        sus = audit_tier()
        print(f"⚠️ 判成 T1 却会动角色的：{len(sus)} 个")
        for d, op, hits in sus:
            print(f"  {d}.{op:<16} ← {', '.join(hits)}")
        if not sus:
            print("  （没有 —— 但只查了 _MOVE_CALLS 列的那几种调用，不是证明）")
        return 0
    if "--stats" in sys.argv:
        stat = {}
        for r in rows:
            stat[r["tier"]] = stat.get(r["tier"], 0) + 1
        uniq = sum(1 for r in rows if not r["alias_of"])
        print(f"共 {len(rows)} 个 op（去掉中文等别名后**真需要测的 {uniq} 个**）：", stat)
        for t in ("T1", "T2", "T3", "⏭"):
            sub = [f"{r['domain']}.{r['op']}" for r in rows if r["tier"] == t]
            print(f"\n--- {t}（{len(sub)}）---")
            print(" ".join(sub))
        return 0

    from_log = "--from-log" in sys.argv
    n, done, fail, stat = write(rows, from_log)
    print(f"✅ 写出 {OUT_PATH}")
    print(f"   op {n} 个 ｜ ✅ {done} ｜ ❌ {fail} ｜ " +
          " ｜ ".join(f"{k} {v}" for k, v in sorted(stat.items())))

    # 日志里出现、清单里没有的 op → 说明 dispatch 被改过，值得单独查
    # ⚠️ 比的是 (域, op) **成对**：`look` 在 check 域有、在别处没有，只比 op 名会全判成"清单外"。
    if from_log:
        known = {(r["domain"], r["op"]) for r in rows}
        extra = sorted(k for k in judge_from_log() if k not in known)
        if extra:
            print(f"\n⚠️ 日志里出现了清单外的 op（dispatch 改过？）：{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
