# -*- coding: utf-8 -*-
"""📋 全工具测试清单生成器（2026-09-11）

**从哪来**：op 清单抄自**各域 dispatch 表**（AI 真能调到的东西），**不是**抄文档。
文档只提供"参数"一列（复用 `_kw_doc_check.py` 的解析器）——所以清单本身不会因为文档写错而漏项。

**怎么用**（三步）：
    python gen_tool_checklist.py                 # ① 生成/刷新清单（判定列不动，可反复跑）
    （……跑测试，任何渠道都行，只要经过 :8000 的 MCP 就会落进 session_log.jsonl……）
    python gen_tool_checklist.py --from-log      # ② 按日志自动打勾 + 填证据（日志行号）
    （手动补 ⏭ / ❌ 的判定。覆盖规则**不对称**，2026-09-17 恒拍板：
      · 你手写的 `⏭ 没条件测` **压得过**日志的 ✅ —— "参数对了/没报错" ≠ "真验过"，
        想转绿必须人工先把 ⏭ 清掉，不许静默漂绿；
      · 但压不过日志里的 ❌ —— 真失败了必须红着露出来，不能被人手捂住。）

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
# 📁 2026-09-12：`session_log.jsonl` 挪进 `scripts/sessions/`（scripts/ 根目录被 238 个
#    `session_*.jsonl` 堆脏了）。⚠️ 存档里旧报告写的 `session_log:NNN` 行号**依然有效**——
#    文件只是换了目录，内容没动。
LOG_PATH = os.path.join(SCRIPT_DIR, "sessions", "session_log.jsonl")

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
        # ⚠️ 跨域传播来的判定（证据以 `← 同 ` 开头）**不是人工结论**，别当成"人工写的"读回来——
        #    否则它会粘住：源那行下轮翻成 ❌ 了，这行还挂着上一轮的 ✅（假绿，正是本表最怕的）。
        #    当作"没判定"，每轮由 propagate_by_fn 重新算。
        if ev.startswith("← 同 "):
            verdict, ev = "", ""
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
    "⚠️ op「",        # 参数名被静默丢掉 —— 看着成功其实没生效，**不算 ✅**
    "Traceback",
)

# 🧪 工具**正确拒绝**的返回 —— 这不是在测那个函数，整条当"没有证据"跳过（2026-09-17 恒拍板）。
#    与 `_BAD_PAT` 的区别：`❌ op「` 是"这个 op 泛泛地报错了"，而这里点名的是**按设计就该发生的拒绝**，
#    所以不能算红；但**也绝不能算绿**（那一下真的没执行）。⇒ 跳过，让上一轮的结论原样留着。
#    ⚠️ 别往上加"看着像拒绝"的宽泛词：吞掉真报错 = 把红藏起来，比假红更坏。
_NOT_EVIDENCE_PAT = (
    "**拒绝执行**",              # 传了 dry_run/preview 这类"只预览"参数而该 op 不支持 ⇒ 按设计拒跑
    "忽略了无法识别的参数",       # 参数名写错被丢掉（多 op 共用一份 kw 时兄弟 op 常中招）
)


# ⚠️ 2026-09-12 恒拍板：**"工具正确拒绝了坏输入" ≠ "测过了"**。
#    测不了就如实记「⏭ 没条件测」，到时候摆场补测 —— **不许**拿"正确拒绝/按设计"之类的话把红的说成绿的。
#    （捏脸是唯一一次例外：它此前反复真机测过多次，恒才同意先放过；**后续同类情况一律如实处理**。）
#    所以这里只做一件事：把"因为**当期条件不具备**才失败"的那几次调用从 ❌ 挪到「没条件测」，
#    并在证据里写明**缺的是什么条件**。⚠️ 它不是绿 —— header 单列一档，一眼看得见还没测过。
#    ⚠️ 加一条的门槛很高：必须**换个场景就能测、只是现在测不了**，且失败文案里写明了原因。
#       凡是"可能就是坏的"，一律留 ❌。
_NOCOND_PAT = (
    ("❌ 修改失败: 捏脸菜单未开", "要捏脸菜单（创建角色 / 幻觉神龛）"),
    ("❌ 放置失败: Cannot place", "要一块能放的地皮 + 可放置物"),
    # 2026-09-12：`crab_bait` 新加的前置检查 —— 背包里没鱼饵时直接拦（原来会逐个空跑 22 遍）。
    #   这是"缺前置条件"不是工具坏：买上饵就能测。
    ("挂饵前先得有饵", "要背包里有鱼饵（先去威利鱼店买 Bait）"),
    # 2026-09-12：批次C 头 5 发跑出来的三个"正确拒绝" —— 全是**当期场景不在**，不是工具坏。
    #   ⚠️ 恒当天拍的规矩：正确拒绝坏输入 **不算测过**。这三个必须留 ⏭，不许被刷成绿。
    #   （`advance`/`minigame_state`/`number` 三个 op 本身没问题 ⇒ 换个场景就能测。）
    ("当前没有剧情/对话", "要一段正在播的剧情/对话（跟 NPC 说话 / 进事件）"),
    ("当前没有小游戏", "要赌场小游戏（CalicoJack/Slots，得先进沙漠赌场）"),
    ("当前不是数量输入菜单", "要弹着数量框（节庆兑换台 / 转盘押注）"),
    # 2026-09-12：`daily settle` —— 这次日志里记的是"**没有结算菜单**时的正确拒绝"。
    #   ⚠️ 但别就此当它绿了：**"菜单开着时能不能真的关掉"这一步当晚没验成** ——
    #   我在**类目明细页**连点 6 次，端点每次都回 `ok:true` 菜单却纹丝不动；最后是 `back`
    #   把菜单关掉的。⇒ 归 ⏭，等下次结算期在**汇总页**干净跑一遍才算数。
    ("当前不在过夜结算界面", "要正在弹着的过夜结算菜单（且停在汇总页）"),
    # 2026-09-12：`donate` 在**已 95/95 全齐**的博物馆上正确拒绝（一件不捐）。
    #   ⚠️ 这是**测不了真路径**（捐一件进去），不是工具坏 —— 按恒的规矩记 ⏭，不记绿也不记红。
    #   但"拒绝"本身是真验收过的：满馆调用前后**展品 95→95、(26,5) 那件还在、背包羊奶酪还在**，
    #   即两个 bug（吃物品 / 删展品）都确认修好，见 CHANGELOG 09-12⑦。
    # ⚠️ 同一件事写两条：`nagi_mcp_server.py` 2026-09-12 把 C# 的英文原文翻成了中文，
    #   重启前日志里是英文、重启后是中文 —— 只认一条，另一边的判定就会掉回 ❌。
    ("No new items to donate", "要一座还真缺东西的博物馆（当前 95/95 全齐，没东西可捐）"
                               "；✅ 但「满馆调用无副作用」已真机验收（不删展品/不吃背包物）"),
    ("没有可捐的了", "要一座还真缺东西的博物馆（当前 95/95 全齐，没东西可捐）"
                     "；✅ 但「满馆调用无副作用」已真机验收（不删展品/不吃背包物）"),
    # 2026-09-12：`forge` 缺火山晶石时**正确拒绝**（点「开始锻造」游戏毫无反应 ⇒ 现在工具明说缺几个）。
    #   ⚠️ 这是"换个场景（弄到晶石）就能测"，不是工具坏 ⇒ 记 ⏭，不许被刷成绿、也不该留红。
    #   ✅ 但**有晶石时的完整路径早已真机跑通**（session_log:571 `💍 合成戒指完成！` 产出 Combined Ring）；
    #      本次也把拒绝路径验了：报的 20 与屏幕一致、两枚戒指原样退回背包（CHANGELOG 09-12⑧）。
    ("火山晶石不够", "要背包里有火山晶石（Cinder Shard）才能走完合成"
                     "；✅ 但「有晶石时完整跑通」已真机验收（产出 Combined Ring），"
                     "「缺料拒绝」本次也验了（报 20 ✓、戒指原样退回）"),
)


# ⚠️ 人工判定（**压过日志**，2026-09-12 恒）：日志只能看出"**报没报错**"，
#    看不出"**做没做错事**"。真机发现某次调用"看着成功、实际干错"时写在这里 ——
#    否则 `--from-log` 会给它一个假绿（这正是本表最怕的东西）。
#    ⚠️ 修好并**重跑复验过**之后，把这条删掉，让日志重新说话。
_MANUAL_VERDICT = {
    # ✅ 2026-09-12：`donate` 的这条**已结案删除**（守规矩——修好 + 重跑复验过，就让日志重新说话）。
    #    它当时记的是"看着成功、实际干错"：满馆调用吃了背包里的羊奶酪 + 造 2 条重复展品。
    #    两处根因（category 兜底 / 哨兵坐标 (26,5)）都已修 + 真机对账复验，见 CHANGELOG 09-12⑦。
    #    现在它由 `_NOCOND_PAT` 判成「⏭ 没条件测」（满馆没东西可捐 ⇒ 测不到真路径）。
}


def judge_from_log() -> dict:
    """扫 session_log.jsonl → {(工具, op): (判定, 证据, 行号)}。
    键带**工具名**（= 域名）而不是裸 op：`place` 这种名字 farm/scene/cabin 三个域都有。
    **最后一次调用说了算**（证据也就是最后那次的行号）。别做成"❌ 一旦出现就粘住"——
    实测中被粘住的正是"先踩到 bug、修完再跑就过了"的那些 op，表上会留下一堆已经修好的红。
    真要看反复，翻 session_log 原文件比看这张表准。

    行号单独返回（2026-09-12）：跨域同函数的判定要传播，**离现在最近的那次调用说了算**，
    得能比大小；塞在证据字符串里再正则抠出来太脏，直接带出来。"""
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
        # 🧪 2026-09-17 恒拍板「负测试不该算 ❌」——工具**正确拒绝**的调用当成"**没有证据**"整条跳过：
        #    · 记 ❌ 是**假红**：工具行为正确，却被当成失败（真机：我为验 09-17(63) 的拒绝逻辑
        #      故意传 `dry_run`，结果 `look`/`seats` **上一轮真验过的 ✅ 被这抹红盖掉**）；
        #    · 记 ✅ 是**假绿**：那个 op 压根没执行（老规矩"参数被丢掉不算 ✅"仍然成立）。
        #    ⇒ 跳过 = 本行不产生判定，`write()` 会回落到上一轮人工/真验的结论。
        #    放在 `bad`/`nocond` 之前判，才能先于 `_BAD_PAT` 里那两条泛匹配生效。
        if any(p in ret for p in _NOT_EVIDENCE_PAT):
            continue
        bad = (r.get("err") or ret.startswith("❌") or any(p in ret for p in _BAD_PAT))
        # 当期条件不具备（不是坏）→ 单列「没条件测」，别混进 ❌、更别混进 ✅
        nocond = next((why for pat, why in _NOCOND_PAT if pat in ret), None)
        for op in re.split(r"[\s,，]+", raw_ops):
            if not op:
                continue
            if nocond:
                res[(tool, op)] = ("⏭ 没条件测", f"`session_log:{i}` {nocond}", i)
            elif bad:
                res[(tool, op)] = ("❌", f"`session_log:{i}` {ret[:60].replace(chr(10), ' ')}", i)
            else:
                res[(tool, op)] = ("✅", f"`session_log:{i}`", i)
    return res


# ── 跨域重复：同一个后端函数挂在多个域下 ──
# ⚠️ 2026-09-12 恒："我怎么记得验过任务工具了？" —— 就是踩到这个。
#    清单是按 **dispatch 表逐条抄**的，而**同一个后端函数**会同时挂在几个域下：
#        check:"quest"  → open_questlog
#        menu :"journal"→ open_questlog      ← 同一个函数，同一个 /open_questlog 端点
#    于是各占一行，看起来是两件事。实测 **500 行 = 188 个函数**，其中 **90 行**是这种跨域重复。
#    同域别名早就标了 `≡`（见 `_rows()`），**跨域的一直没标** —— 这里补上，并把判定按函数传播。
#
#    判据：目标函数名相同 ⇒ 同一个 C# 端点 ⇒ **一行绿了，其余行功能上必然也通**。
#    （域内门牌号有没有拼错，由 `domain_selftest.py` 的检查#3 静态兜住：dispatch 引用的名字
#      必须是模块里真实存在的 callable。所以跨域行能多测到的只有"门牌号拼写"，而那已经静态验过了。）
#
#    ⚠️ 两条不能省：
#      1. **传播要挑最近的那次调用**（行号最大），否则 stale ❌ 会盖掉刚测出来的 ✅。
#      2. 传播只写"自己没测过"的行；**❌ 照样逐行列出来**——别让人以为只有一处坏。
def fn_index(rows: list) -> dict:
    """{后端函数名: [该函数的所有行]}（只收有函数名的；`?`/空的不进）。"""
    idx = {}
    for r in rows:
        if r.get("target"):
            idx.setdefault(r["target"], []).append(r)
    return idx


def propagate_by_fn(rows: list) -> int:
    """把一个函数的判定传播给它所有"自己没测过"的门牌行。返回被传播的行数。"""
    n = 0
    for fn, sib in fn_index(rows).items():
        if len(sib) < 2:
            continue
        dated = [r for r in sib if r["verdict"] and r.get("_line")]
        if not dated:
            continue
        # 离现在最近的那次调用说了算（同一函数在不同域下各调过一次时，别让旧的盖新的）
        src = max(dated, key=lambda r: r["_line"])
        for r in sib:
            if r["verdict"] or r["tier"] == "⏭":
                continue   # 自己测过的不覆盖；层的「当期不可用」本来就标着测不了，别刷
            r["verdict"] = src["verdict"]
            r["evidence"] = f"← 同 `{src['domain']}:{src['op']}`〔{fn}〕"
            n += 1
    return n


def write(rows: list, from_log: bool):
    old = read_existing()
    logres = judge_from_log() if from_log else {}
    for r in rows:
        key = (r["domain"], r["op"])
        v, ev, ln = logres.get(key, ("", "", 0))
        if v.startswith("❌"):
            # 日志里的**真失败**压过一切人工判定——红必须说出来，不能被人手捂住
            r["verdict"], r["evidence"], r["_line"] = v, ev, ln
        elif old.get(key, {}).get("verdict") == "⏭ 没条件测":
            # 🖐️ 2026-09-17 恒拍板：「⏭ 没条件测」是**人判过的事实**，压得过日志里的 ✅。
            #    起因：`farm harvest` 传对了 `radius`、机械判据给绿，可当天农场**没有镰刀可收的作物**
            #    （脚本 0s 收工、没报收割数）⇒ **参数对了 ≠ 真验过**。同族还有 milk（自动采集器）、
            #    berry（非浆果季）、spot（没斑点）——全都会被这一次"看起来成功"的调用刷成假绿。
            #    想转绿**必须人工先把 ⏭ 清掉**：宁多花一步，不许静默漂绿（本表最怕的东西）。
            #    `_line` 给极大值：传播时也优先（同函数=同病），且不会被别的行盖掉。
            r["verdict"], r["evidence"], r["_line"] = old[key]["verdict"], old[key]["evidence"], 10 ** 9
        elif v:
            r["verdict"], r["evidence"], r["_line"] = v, ev, ln
        elif old.get(key, {}).get("verdict"):
            # 人工写的（⏭ / 手测结论）优先于"没测到"
            r["verdict"] = old[key]["verdict"]
            r["evidence"] = old[key]["evidence"]
        # 人工判定压过日志（"没报错"≠"做对了"，见 _MANUAL_VERDICT 上的注释）
        mv = _MANUAL_VERDICT.get(key)
        if mv:
            # `_line` 给个极大值：人工判定要**压过**任何日志行，传播时也优先（同函数=同病）
            r["verdict"], r["evidence"], r["_line"] = mv[0], mv[1], 10 ** 9
    # 跨域同函数：一行绿了，其余门牌行跟着绿（见 fn_index 上的长注释）
    _prop = propagate_by_fn(rows)

    tier_cn = {"T1": "T1 自动", "T2": "T2 摆场", "T3": "T3 副作用", "⏭": "⏭ 当期不可用"}
    stat = {}
    for r in rows:
        stat[r["tier"]] = stat.get(r["tier"], 0) + 1
    done = sum(1 for r in rows if r["verdict"] == "✅")
    fail = sum(1 for r in rows if r["verdict"] == "❌")
    # 「没条件测」单列 —— 它不是绿。恒 2026-09-12：测不了就如实记，别充数。
    nc = sum(1 for r in rows if r["verdict"] == "⏭ 没条件测")
    # 函数口径（2026-09-12）：行数看着吓人、其实同一个后端函数会占好几行。剩多少活儿要按函数看。
    idx = fn_index(rows)
    # ⚠️ 没有函数名的行（解析不出目标的）算作**各自独立的函数** —— 宁可多报几个"待测"，
    #    也别把它们并进别人名下（并了就是"看着测过、其实没测"）。`--stats` 用同一个式子，别各写各的。
    f_total = len(idx) + sum(1 for r in rows if not r.get("target"))
    f_done = sum(1 for f, rs in idx.items() if any(r["verdict"] == "✅" for r in rs))
    f_nc = sum(1 for f, rs in idx.items()
               if not any(r["verdict"] == "✅" for r in rs)
               and any(r["verdict"] == "⏭ 没条件测" for r in rs))
    f_todo = f_total - f_done - f_nc

    L = []
    L.append("# 🧪 全工具测试清单（2026-09-11 生成）\n")
    L.append(f"> **{len(rows)} 行 = {f_total} 个后端函数**（抄自各域 dispatch，非文档）"
             f"｜✅ {done} 行 / **{f_done} 函数** ｜❌ {fail} ｜⏭ 没条件测 {nc} 行 / {f_nc} 函数"
             f"｜**待测 {f_todo} 函数**｜"
             f"{' ｜'.join(f'{tier_cn.get(k, k)} {v}' for k, v in sorted(stat.items()))}\n")
    L.append("> 用法：跑测试（任何渠道，只要走 :8000 的 MCP）→ `python gen_tool_checklist.py --from-log` 自动打勾。")
    L.append("> 判定规则见脚本头部；**⚠️ op「」= 参数被静默丢掉 ⇒ 不算 ✅**（也不算 ❌ —— 那一下压根"
             "没执行，当\"没有证据\"跳过，本行沿用上一轮结论）；真报错才 ❌。")
    L.append("> ⚠️ 本表**测试前冻结**：`--from-log` 只写后两列，永不动 op 列表。")
    L.append("> 🖐️ **手写的 `⏭ 没条件测` 压得过日志的 ✅**（\"参数对了/没报错\"≠\"真验过\"）——"
             "想转绿要先人工清掉 ⏭；但它**压不过日志里的 ❌**（真失败必须红着露出来）。")
    L.append("> 🔁 **`⇄` = 跨域同函数**（同一个后端函数挂在别的域下，各占一行）：判定按函数传播，"
             "任一行绿了其余行跟着绿 —— 所以**别重复测**，看 `⇄` 挑一行测即可。\n")

    for d in K.DOMAINS:
        sub = [r for r in rows if r["domain"] == d]
        if not sub:
            continue
        L.append(f"\n## `{d}`（{len(sub)}）\n")
        L.append("| op | 层 | 参数 | 判定 | 证据 |")
        L.append("|---|---|---|---|---|")
        for r in sub:
            opname = f"`{r['op']}`"
            if r.get("alias_of"):
                opname += f" ≡`{r['alias_of']}`"
            peers = [x for x in idx.get(r.get("target") or "", []) if x["domain"] != d]
            if peers:
                opname += f" ⇄`{peers[0]['domain']}:{peers[0]['op']}`"
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
    L.append("- **⏭ 当期不可用**：节庆日专属/需解锁 —— **不算没测，是测不了**，别打勾")
    L.append("- **⇄跨域同函数**：同一个后端函数在别的域下也挂了门牌，**判定共享、别重复测**")
    L.append("- **← 同 `域:op`**：这行没单独测过，判定是从同函数的另一行走 `⇄` 传播来的\n")

    open(OUT_PATH, "w", encoding="utf-8").write("\n".join(L) + "\n")
    return len(rows), done, fail, stat, (f_total, f_done, f_nc, f_todo, _prop)


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
        idx = fn_index(rows)
        no_t = sum(1 for r in rows if not r.get("target"))
        uniq = sum(1 for r in rows if not r["alias_of"])
        xdom = sum(1 for r in rows
                   if any(x["domain"] != r["domain"] for x in idx.get(r.get("target") or "", [])))
        print(f"共 {len(rows)} 行 ｜ **{len(idx) + no_t} 个后端函数**"
              f"（同域别名去重后 {uniq} 行；跨域重复 {xdom} 行；解析不出目标 {no_t} 行）：", stat)
        for t in ("T1", "T2", "T3", "⏭"):
            sub = [f"{r['domain']}.{r['op']}" for r in rows if r["tier"] == t]
            print(f"\n--- {t}（{len(sub)}）---")
            print(" ".join(sub))
        return 0

    from_log = "--from-log" in sys.argv
    n, done, fail, stat, (ft, fd, fnc, ftodo, prop) = write(rows, from_log)
    # 「没条件测」在这里也要报 —— 否则跑脚本的人只看得到 ✅/❌，那两行会像"没测过"一样糊过去
    nc = sum(1 for r in rows if r["verdict"] == "⏭ 没条件测")
    print(f"✅ 写出 {OUT_PATH}")
    print(f"   {n} 行 = {ft} 函数 ｜ ✅ {done} 行 / {fd} 函数 ｜ ❌ {fail} ｜ "
          f"⏭ 没条件测 {nc} 行 / {fnc} 函数 ｜ **待测 {ftodo} 函数**")
    if prop:
        print(f"   🔁 跨域同函数传播：{prop} 行的判定是从同函数的另一行来的（表里标了 ← 同 …）")
    print("   " + " ｜ ".join(f"{k} {v}" for k, v in sorted(stat.items())))

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
