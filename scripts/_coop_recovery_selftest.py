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
   "self.select(bt)" in block(bc, r"    def place_bomb_at", span=40))
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

print()
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("🎉 全部通过（协同：被动起/随时结束/复活/开宝箱/主循环每层扫箱/送出矿认层号）"
      " + give 不假成功 + 头骨 start=121 不被钳")
