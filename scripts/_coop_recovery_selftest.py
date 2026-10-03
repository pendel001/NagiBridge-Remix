# -*- coding: utf-8 -*-
"""👥💣 `bomb_mine` 内部协同（沙漠/头骨那档）的两条行为钉子（恒 2026-10-03 晚）。

恒的原话：「那就是说，沙漠协同不能主动起，只能炸弹耗竭被动跟随？…**确认一下这个协同可以在
异步时随时结束，以及随时得到炸弹能"复活"成炸矿模式**，应该就比较理想了。**火山好像就是这样的，
会自己恢复。**」

⇒ 钉三件事（读源码，不靠跑起来才发现）：
  ① **只能被动起**：`bomb_escort` 那个独立脚本**已删**（恒 2026-10-03 拍板）—— 真跑的是
     `bomb_mine._run_cooperate()`（内联）；而且只在"炸弹耗竭 + host 同矿井"时进。
  ② **随时可结束**：`bomb_retreat`（MCP 单步工具）= 先 `/guard off` 再 kill 本进程 + 把人传出矿井；
     循环里也有"我不在矿井了"的退出判据。
  ③ **能"复活"回炸矿**（2026-10-03 新增，照火山"同一循环里重估炸弹"的形状）：
     协同每拍 `choose_bomb_type()` ⇒ 有弹就 `return "bombs_back"`；**两个调用点都接着炸**（不是结束整趟）。
  ⚠️ 这条钉子防的是"下次有人把 `_run_cooperate()` 的返回值丢掉" —— 那样就又变成"一交棒永远回不去"。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


def src(fn):
    """`scripts/` 下的文件；给 `../ModEntry.cs` 这种相对路径也行（C# 在仓库根）。"""
    return io.open(os.path.normpath(os.path.join(HERE, fn)), encoding="utf-8").read()


def block(text, start_pat, span=70):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.search(start_pat, ln):
            return "\n".join(lines[i:i + span])
    return ""


def code_only(text):
    out, in_doc = [], False
    for ln in text.splitlines():
        if ln.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        s = ln.lstrip()
        if s.startswith("#") or s.startswith("//"):
            continue
        m = re.search(r"\s#", ln)
        if m:
            ln = ln[:m.start()]
        out.append(ln)
    return "\n".join(out)


bm = src("bomb_mine.py")
bc = src("bomb_common.py")
srv = src("nagi_mcp_server.py")
mr = src("mine_run.py")      # ⑨ 头骨 start=121 那条钉子要读它

print("① 沙漠协同**只能被动起**：真跑的是内联的 `_run_cooperate()`；`bomb_escort.py` 已删（恒 2026-10-03 拍板）")
ck("…进协同的判据 = 炸弹耗竭 + host 同矿井（`count_bombs() <= 0` + `is_mine_location(host)`）",
   "if self.count_bombs() <= 0:" in bm and "is_mine_location(self.host_location())" in bm)
ck("…`bomb_escort.py` **文件真的没了**（删干净，不是只退役）",
   not os.path.exists(os.path.join(HERE, "bomb_escort.py")))
ck("…MCP 里那个 `bomb_escort` 工具也删了（不留一个指向已删脚本的入口）",
   "def bomb_escort(" not in srv and '_run_script("bomb_escort"' not in srv)
ck("…各处名单不再引用它（端口注入/异步白名单/矿类脚本/撤退名单/计划提醒）",
   srv.count("bomb_escort") <= 3,   # 只允许出现在"已删"的注释里
   f"srv 里还有 {srv.count('bomb_escort')} 处")
ck("…`bomb_escort` 不在 mine 域的 dispatch 里（AI 调不到它）",
   '"bomb_mine": bomb_mine, "bomb_volcano": bomb_volcano,' in srv
   and '"bomb_escort": bomb_escort' not in srv)

print("② 随时可结束：`bomb_retreat` 先关 guard 再杀 + 传出矿井；循环自己也有『不在矿井』的退出")
ck("…`_bg_kill` 对矿类脚本**先 `/guard off` 再杀**（terminate 不跑 finally，不补这刀 guard 会一直砍）",
   'api._post("/guard"' in block(srv, r"def _bg_kill", span=80))
ck("…`bomb_retreat` = 停后台脚本 + `retreat_to_entrance`",
   "_bg_kill(j)" in block(srv, r"def bomb_retreat", span=30)
   and "retreat_to_entrance" in block(srv, r"def bomb_retreat", span=30))
_coop = block(bm, r"    def _run_cooperate", span=120)   # ⚠️ 窗口要盖住整个函数（加了开宝箱那段后变长了）
ck("…协同循环里有『我不在矿井/你不在矿井 ⇒ 退出』的判据",
   "not is_mine_location(ml) or not is_mine_location(hl)" in code_only(_coop))

print("③ 能『复活』回炸矿（照火山那套：同一循环里重估炸弹）")
ck("…协同每拍先问 `choose_bomb_type()`（会顺手换成包里真有的那种）",
   "choose_bomb_type()" in _coop and "bombs_back" in _coop)
ck("…有弹就 `return \"bombs_back\"`（不是 break 走收工那条）",
   'return "bombs_back"' in _coop)
ck("…`clear_floor` 那个调用点：拿到 bombs_back 就 `continue` **接着炸这一层**",
   # ⚠️ 走 `code_only`（原来直接搜原文，被中间那行 `# 💣 …` 注释挡住 ⇒ 假红）
   re.search(r'if self\._run_cooperate\(\) == "bombs_back":\s*\n\s*self\.coop_handoff = False\s*\n\s*continue',
             code_only(bm)) is not None)
ck("…`_run_rush_inner` 那个调用点：同样 `continue` 接着冲（**不结束整趟**）",
   bm.count('== "bombs_back"') >= 2)
ck("…复活时把 `coop_handoff` 复位（否则结尾会打『协同模式结束』误导人）",
   bm.count("self.coop_handoff = False") >= 2)
ck("…**交棒前先试换类型**（`count_bombs()` 只数当前那种 ⇒ 手里有樱桃也会被判『没炸弹』）",
   "先试**换类型**再判" in bm or "_sw = self.choose_bomb_type()" in bm)
ck("…放炸弹那条路自己会 `select`（换类型/复活后不用担心手上不是炸弹）",
   "self.select(bt)" in block(bc, r"    def place_bomb_at", span=95))
ck("…火山那套本来就是这么干的（对照事实：同一循环里 `choose_bomb_type` 重估）",
   "choose_bomb_type()" in src("bomb_volcano.py"))

print("④ `quiet` 那个计数器接上了（恒「3我有点不懂……交给你来修」）")
ck("…`quiet` 被**读过**（有心跳日志用它），不再只写不读",
   len(re.findall(r"quiet", code_only(_coop))) >= 3)
ck("…心跳**不**自动撤退（恒只是站着不动，悄悄走人才是坑）——它那一段只有 log",
   # ⚠️ 原来只在 `quiet == 15` 之后 200 字里搜 `retreat` —— 后来加了开宝箱那段（同函数里确实有
   #    `retreat_to_entrance("宝箱满包")`）就把这条钉成假红了 ⇒ 改成**只看 if 的下一行**。
   re.search(r'if quiet == 15[^\n]*\n\s+log\(', code_only(_coop)) is not None)

print("⑤ 🎁 协同也要开宝箱（恒 2026-10-03 真机：「协同不会开箱子！让协同也加开箱吧」）")
ck("…协同循环里调了 `open_treasure_chests()`（带 `skip` 记账）",
   "open_treasure_chests(skip=coop_opened)" in _coop)
ck("…🎁 记账只在当次下矿有效（换层清空；恒：空箱再点会爆掉消失、百层宝箱房重进会刷新 ⇒ 重开无害）",
   "coop_opened.clear()" in _coop and "coop_opened = set()" in _coop)
ck("…`mine_run` 那边把新返回值当**个数**用（`len()`，别印成 `×[(9, 9)]`）",
   "len(n)" in src("mine_run.py"))
ck("…是**节流**扫的（`COOP_CHEST_EVERY`）——`open_treasure_chests` 内部是 `surroundings(30)` 大扫描，每拍都扫会打满 API",
   "COOP_CHEST_EVERY" in bm and "coop_tick % COOP_CHEST_EVERY" in _coop)
ck("…🎁 记账**开过的箱子**（`skip=` + 换层清空）——不然每 9s 把同一个空箱再开一遍（真机 `🎁 开宝箱 (9,9)` 连出两行）",
   "skip=coop_opened" in _coop and "coop_opened.clear()" in _coop)
ck("…⭐ `ManualChestFull` **单独接住**（协同那层 `except Exception` 会把它当普通异常吞掉 ⇒ 满包就静默不吭声）",
   "except ManualChestFull" in _coop)
ck("…满包那条走**和主循环一样的规矩**：停脚本交 AI 手动（不自动丢物）",
   "宝箱满包领不走" in _coop and "retreat_to_entrance(\"宝箱满包\")" in _coop)
ck("…文档行也提到开宝箱（别让 help 文案落后于行为）",
   "开宝箱" in _coop)

print("⑥ `/give` 不许假成功（恒那趟现场：包满 36/36 时回 `ok:true, given:Bomb` 而**一颗没进包**）")
_gv = block(src(os.path.join("..", "ModEntry.cs")), r"private object HandleGive", span=95)
# ⚠️ span 从 45 抬到 95：2026-10-03 晚又往这个方法头上加了"验 ID"那段（Error Item 假门），
#    45 行的窗口够不到下面的 `addItemToInventory` ⇒ 假红。
ck("…看 `addItemToInventory` 的返回值", "addItemToInventory(item)" in _gv)
ck("…1.6 的签名是返回**余量 Item**（不是 bool）——拿 `leftover?.Stack` 算真进去了几个",
   "Item? leftover" in _gv and "leftover?.Stack" in _gv)
ck("…塞不进 ⇒ `ok:false` + 报空格 + 给下一步", "ok = false" in _gv and "freeSlots" in _gv)

print("⑦ 炸矿主循环也**每层都扫**宝箱（恒 2026-10-03：「跳了。是不是因为不是整百层也不认？」——正是）")
ck("…主循环里 `open_treasure_chests()` **不再**被 `% 10` / `(level-120)%100` 的条件包住",
   not re.search(r"if \(level - 120\) % 100 == 0", code_only(bm))
   and "self.open_treasure_chests()" in code_only(bm))

print("⑧ `script stop` 把人送出矿时，**头骨矿洞要送回沙漠**（恒：「应该到沙漠洞口而不是 mountain」）")
_ex = block(srv, r"def _mine_exit_from_loc", span=26)
ck("…按**层号**分（`UndergroundMine121+` = 头骨 ⇒ Desert）",
   "UndergroundMine(\\d+)" in _ex or "UndergroundMine(\\\\d+)" in _ex or ">= 121" in _ex)
ck("…不再只判 `\"SkullCave\" in ln`（头骨矿洞层名**不含** SkullCave 字样 ⇒ 原来会判成普通矿井）",
   "int(m.group(1)) >= 121" in _ex)
ck("…镇矿井入口那层（location 就叫 `Mine`）也送回 Mountain",
   'if ln == "Mine"' in _ex)

print("⑨ 🕳️ 头骨矿洞（≥121）**不能续层**：`start=121` 不许被钳成 120（恒：「沙漠下矿不能续。出去进来就得121开始。」）")
ck("…`mine_run` 的钳位只在**目标 ≤120（镇矿井）**时生效",
   "if args.mode == \"rush\" and args.target <= 120:" in mr)
ck("…头骨模式会把 start 兜到 121（进沙漠矿洞只能从 121 起）",
   "args.start = 121" in mr)
ck("…`run_rush` 里 `start_level >= 121` **跳过电梯那套**（否则 121 会被『往下取 5 的倍数』折成 120）",
   "if start_level >= 121:" in mr and "往下取 5 的倍数" in mr)
ck("…显式 start>121 会**如实说明**那是直接 warp 跳层（非原版行为，别装成正常路径）",
   "直接 warp 跳层" in mr)
# ✅ 真机验过（2026-10-03 晚）：`mine_run --mode rush --start 121 --target 124` 在头骨矿洞跑出
#    `起点 121 → 终点 124，通过 3 层`（日志 `_live/_v203r_skull121.log`）。

print("⑩ 🪜 下楼偶发不生效 ⇒ **同一个位置重按 confirm**，别一次不成就上非原版 warp（恒 2026-10-03 当场质疑）")
# 现场（`_live/_v203s_ladder_town27.log` / `_v203s_skull_scan.log` / `_v203s_skull_ladder2.log`
#      / `_v203s_skull_rerun.log`，四份都在）：
#   ① 镇矿井 27 层：传送到梯子格 ✅ → 不按键不下（3s）→ confirm **0.65s 下楼** ✅
#   ② 头骨 121 层（同一段代码、同一步）：第一次 confirm **12s 纹丝不动** ❌；紧接着**再按一次 0.83s 就成了** ✅
#   ③ 真脚本连跑三层：**121 ✅ / 122 ✅ / 123 ❌** ⇒ 1/3 失败
#   ⇒ 老代码一次不成就 `/warp UndergroundMine{n}` 兜底 = **非原版**、还会**跳过整层内容**
#     （203r 那趟"敲碎 0 块矿石"就是这么来的）。**恒看到的"每次都是踩梯子 confirm 下去的"才是常态。**
_tl = block(mr, r"    def take_ladder\(", span=55)
ck("…有重试（`tries=3` + `for attempt in range(1, tries + 1)`）",
   "tries=3" in _tl and "for attempt in range(1, tries + 1)" in _tl)
ck("…每一轮都**重新摆位置**再按 confirm（位置别只摆一次）",
   "self.mine_teleport(lx, ly)" in _tl and '"/key", {"key": "confirm"' in _tl)
ck("…轮询抽成 `_wait_descend`（两段重复的轮询合一）", "_wait_descend" in _tl and "def _wait_descend" in mr)
ck("…洞（MineShaft）单独走一条：**先真走一步**再 confirm（confirm 对洞没用）",
   "is_shaft" in _tl and "walk_to_coord" in _tl)
ck("…兜底 warp 的日志**如实**写「非原版：会跳过本层内容」", "非原版" in _tl and "跳过本层内容" in _tl)
ck("…下游那句也如实（走梯子失败 ⇒ 跳过本层）", "跳过本层" in mr)

print("⑪ 🚧 落脚点选点**必须查「能不能站」**（恒第二次报：入侵层放梯第一次传进墙）")
# 恒 2026-10-03 原话：「我发现**入侵层的放梯，第一次总是传送到穿墙位置，第二次才合法**。
#   这好像不是第一次发生了，麻烦你检查」（2026-09-20 他就报过同族，那天只给 warp 加了事后体检）。
_fs = block(bc, r"    def find_safe_spot\(", span=95)
ck("…`find_safe_spot` 会问 `/passable`（**只读、不挪人**）", '"/passable"' in _fs)
ck("…候选按「优先 → 兜底」顺序**逐个**验", "for c in cands" in _fs and "cands.append(c)" in _fs)
ck("…一个都不行 ⇒ `None`（宁报错别兜底）", "return None" in _fs)
ck("…注释把恒**两次**报的现场都记着（2026-09-20 / 这次）", "2026-09-20" in _fs and "穿墙" in _fs)
_ps = block(bc, r"    def position_safe\(", span=30)
ck("…`position_safe` 的**默认**已改成 `check_passable=True`（堵住剩下所有入口）",
   "check_passable=True, check_connectivity=False" in _ps)
ck("…注释记着「人进墙 ⇒ guard 贴不到脸 ⇒ 撤退路上被打死」这条后果",
   "guard" in _ps and "打死" in _ps)
ck("…开箱站位 / 通用走过去 两处裸 `position` 也改了（`position_safe` + 不可站就报）",
   'self.position(sx, sy)' not in bc and "开箱站位" in bc and "目标格" in bc)

print("⑫ 🪜 梯子格/楼梯格**必须显式关掉可站校验**（它们本来就「不可走」，站上去才传层）")
# 真机现场（2026-10-03 深夜，我把 position_safe 默认改成校验之后**立刻**撞到）：
#   `position (8,31) 被拒（不可走/孤岛）` ×3 ⇒ 站不上梯子 ⇒ 造楼梯 ⇒ 楼梯格同样被拒 ⇒
#   `撤退原因: 没梯子也没楼梯材料`（恒看到的：「两个竖井，一把梯，又插一把，然后自己撤退了」）。
ck("…梯子站位显式 `check_passable=False`",
   "position_safe(lx, ly, exact=True, check_passable=False)" in bc)
ck("…楼梯站位同样显式关掉",
   "position_safe(nx, ny, exact=True, check_passable=False)" in bc)
ck("…两处注释都写明「它们本来就不可走」+ 真机现场",
   bc.count("本来就") >= 1 and bc.count("被拒（不可走/孤岛）") >= 1)


print("⑬ 🔨 `item_keep_score` 不许再把**卖价0的装备**当垃圾（银河之锤被丢过三次）")
# 铁证：三趟炸矿日志各一行 `🎒 腾格：丢 Galaxy Hammer`（bomb2/bomb4/bombday）。
# 病因：保护名单有 Sword/Blade 却**没有 Hammer**（SDV 锤子是 Club 类），而武器卖不掉 val=0 ⇒ 判成垃圾。
ck("…武器关键字补全了 `Hammer`", "\"Hammer\" in name" in bc)
ck("…`value <= 0` 一律不丢（卖不掉=装备）", "if value <= 0:" in bc and "绝不丢" in bc)
ck("…注释里记着铁证「腾格：丢 Galaxy Hammer」", "腾格：丢 Galaxy Hammer" in bc)
ck("…腾不出格时**如实报**（不静默失败）", "一个能丢的都没有" in bc)

print("⑭ 🎒 `ensure_free_slot` 必须是**全包最低分**（恒：「说好的腾价值最低项呢」）")
# 原来是 `for …: if keep < 40: 丢它; return True` ⇒ 丢的是「第一个低分项」，背包顺序说了算，
# 跟"价值最低"没有关系（银河之锤就是这么被丢的）。⇒ 先全表算分、再挑最低分那件。
ck("…先全表算分再排序（`cands.sort()`）", "cands.sort()" in bc and "cands.append((item_keep_score" in bc)
ck("…取的就是最低分那件（`cands[0]`）", "cands[0]" in bc)
ck("…最低分 ≥40 时**一件都不丢**并如实报", "一件都不丢" in bc and "score >= 40" in bc)
ck("…日志写明「全包最低分」", "全包最低分" in bc)

print("⑮ 💣 「放不了炸弹」必须**自证**（恒：那个傻愣着是它没往下炸；可能位置被挡放不了）")
# 现场：bomb4 那趟撤退原因就是 `连续放置失败，疑似卡死`，另有 `⚠️ 放炸弹失败: use 失败`——
# 而原来 `place_bomb_at` 只回一句 `use 失败`，把 `/use` 原始回包**丢了**，现场看不出原因。
_pb = block(bc, r"    def place_bomb_at\(", span=95)
ck("…放之前后都数一遍炸弹（`n_before`/`n_after`）", "n_before = self.count_bombs" in _pb and "n_after = self.count_bombs" in _pb)
ck("…炸弹数少了 ⇒ 按「已放出」算（不抠 action 字符串）", "n_after < n_before" in _pb and "placed_by_count" in _pb)
ck("…失败时把 `/use` **原始回包**记进 msg（下次一眼看出为什么）", "/use 回包" in _pb and "str(r)[:160]" in _pb)
ck("…注释点明恒的观察（「没往下炸…位置被挡放不了炸弹」）", "没往下炸" in _pb)

print("⑯ 🔨 guard 抢手持槽 ⇒ `/use` 挥成锤子、炸弹放不出去（真机抓到的真凶）")
# 真机回包：`{'ok':True,'action':'tool','item':'Galaxy Hammer'}` + 炸弹数没变 ⇒ 那颗炸弹根本没放。
# guard（C#）为了砍怪会把 `CurrentToolIndex` 切到武器槽，我们在它之后 `/use` ⇒ 手里已经不是炸弹了。
_pb2 = block(bc, r"    def place_bomb_at\(", span=70)
ck("…放之前先确认手持真是炸弹（读 `currentItem`）", "currentItem" in _pb2 and "bt not in cur" in _pb2)
ck("…回包 `action == \"tool\"` 被视为「被 guard 抢了」并重选重试", '== "tool"' in _pb2 and "guard 抢" in _pb2)
ck("…有重试次数上限（`for attempt in (1, 2, 3)`）", "for attempt in (1, 2, 3)" in _pb2)
ck("…数炸弹兜底仍在（少了=放出去了）", "n_after < n_before" in _pb2)

print()
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("🎉 全部通过（协同：被动起/随时结束/复活/开宝箱/主循环每层扫箱/送出矿认层号）"
      " + give 不假成功 + 头骨 start=121 不被钳")
