# -*- coding: utf-8 -*-
"""⚡ 快检 —— **只跑跟这次改动有关的钉子**（恒 2026-11-06：「没时间每次跑个十年」）。

为什么有它：全套 `scripts/_*_selftest.py` 是**回归网**，改一行就跑全套是浪费——
真正需要每次跑的是"**这次碰过的东西**"的钉子。全套留给"要发版/大改"那一档。

用法：
    python scripts/_quick.py              # 核心一档（最常改的几条链路，默认，几十秒）
    python scripts/_quick.py --changed    # **只跑改过的文件**涉及的那些钉子（最快）
    python scripts/_quick.py -k till      # 名字或内容里含关键词的钉子
    python scripts/_quick.py --list       # 只列表不跑
    python scripts/_quick.py --all        # 全套（发版前跑这一档）

判定口径（`--changed`）：
    `git status --porcelain` 里改过/新增的 `scripts/xxx.py` → 拿 `xxx` 去**每个钉子的源码里搜**，
    搜到就跑。宁可多跑几个，也不漏——但绝不因为"改了 A"就去跑跟 A 无关的 80 个。
"""
import argparse
import io
import os
import re
import subprocess
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# 核心一档：**跨模块的几条主干**（状态条 / 单子 / 导航 / 农活 / 箱子 / 商店）。
# ⚠️ 这不是"重要度排名"，是"改这几个模块时必须顺手看一眼"的那几处。
CORE = [
    "_till_skip_tilled_selftest.py",   # 锄地跳过逻辑
    "_plant_and_layout_selftest.py",   # 播种/洒水器布局
    "_bin_no_all_selftest.py",         # 出货箱
    "_shipping_bin_selftest.py",       # 出货箱提示/坐标
    "_state_ab_selftest.py",           # 状态条 A/B（改了提示就会碰）
    "_map_go_resolve_selftest.py",     # 跨图导航
    "_npc_where_selftest.py",          # 找人
    "_shop_closed_selftest.py",        # 休息日
    "_ai_zoom_selftest.py",            # AI 窗口缩放（只对 7843）
    "_egg_fallback_selftest.py",       # 蛋蛋节：纸条没写也照捡（兜底路线 + 闸门放行）
    "_call_budget_selftest.py",        # 60s 客户端超时闸 + 日常↔节日对话改道
    "_harvest_radius_selftest.py",     # 收获矩形扫描 / 挂住扣时 / 异步判据
    "_water_stamina_selftest.py",      # 浇水低体力闸（大簇切小）
    "_pet_flavor_selftest.py",         # 摸宠物彩蛋文案（猫/狗/龟）
    "_load_machine_filter_selftest.py",  # 上料：箱子/稻草人别串进路线（canPlace 判据）
    "_sleep_overnight_guard_selftest.py",  # 过夜结算/存盘时禁止 sleep（防把房主结算卡死）
    "_tv_hint_selftest.py",            # 电视那行是真路牌（POI 名字 + help 查得到）
    "_helpwanted_selftest.py",         # 求助栏接取按钮（acceptQuestButton）报得出、指得准
    "_farmhouse_door_selftest.py",     # 农舍/农场建筑进门：门格形状 (图,(x,y)) 别再 TypeError
]

# 慢钉子（单个 15~60s，合计占全套一半）：默认**不跑**，`--slow` 或 `--all` 才带上。
# ⚠️ 它们不是不重要，是"每次改一行都跑一遍"没性价比 —— 发版前 / 动到对应功能时再跑。
SLOW = {
    "_intent_wiring_selftest.py",         # ~55s（637 条断言）
    "_toolarea_geode_gift_selftest.py",   # ~18s
    "_walnut_selftest.py",                # ~13s
    "_sit_selftest.py",                   # ~7s
    "_warp_guard_selftest.py",            # ~5s
    "_place_token_selftest.py",           # ~2s（留着是因为它常假红，别默认塞）
}


# 中枢模块：几乎每个钉子都会 `import` 它们 ⇒ "改它就跑所有提到它的钉子"等于跑大半套。
# 碰到它们就走**核心一档**（并明确告诉你"要全量请 --all"），不做假提速。
HUBS = {"nagi_mcp_server", "stardew_api", "intent_menu", "locations",
        "navigation", "calendar_data", "tree_types", "stamina_common"}


def all_nails():
    return sorted(f for f in os.listdir(HERE)
                  if f.startswith("_") and f.endswith("_selftest.py"))


def _read(path):
    try:
        return io.open(path, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def changed_modules():
    """`git status --porcelain` → 改过的脚本模块名（`scripts/clear_area.py` → `clear_area`）。"""
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                             capture_output=True, timeout=30)
        txt = (out.stdout or b"").decode("utf-8", "replace")
    except Exception:
        txt = ""
    mods = set()
    for ln in txt.splitlines():
        m = re.search(r"(?:^|\s)(\S*scripts[/\\]([A-Za-z0-9_]+)\.py)$", ln.strip())
        if m:
            mods.add(m.group(2))
    # 顶层 C# 改动 → 所有钉子都可能相关（钉子里有源码扫描），给个提示不跑全套
    if re.search(r"\bModEntry\.cs\b", txt):
        mods.add("ModEntry")
    return mods


def pick(args):
    names = all_nails()
    if args.all:
        return names
    if args.changed:
        mods = changed_modules()
        if not mods:
            print("（git 里没有改过 scripts/*.py —— 默认跑核心一档）")
            return [n for n in CORE if n in names]
        _hubs = sorted(m for m in mods if m in HUBS)
        print("改过的模块：" + "、".join(sorted(mods)))
        # 中枢模块被改 ⇒ "提到它的钉子"≈ 大半套：与其假装精准，不如跑**核心一档**并说清楚。
        if _hubs and not args.all:
            print(f"⚠️ 改到了**中枢模块**（{'、'.join(_hubs)}）—— 提它的钉子有 "
                  f"{len([n for n in names if any(m in _read(os.path.join(HERE, n)) for m in _hubs)])} 个，"
                  f"挑等于挑一半 ⇒ 这一档只跑**核心 {len(CORE)} 个**；"
                  f"发版前记得 `--all`。")
            return [n for n in CORE if n in names]
        hit = []
        for n in names:
            src = _read(os.path.join(HERE, n))
            if any(re.search(r"\b%s\b" % re.escape(m), src) for m in mods):
                hit.append(n)
        if "ModEntry" in mods:                 # C# 改了：源码钉（扫 .cs 的）也要跑
            hit = [n for n in names
                   if n in hit or "ModEntry.cs" in _read(os.path.join(HERE, n))]
        # ⚠️ 新加/改过的钉子**自己**也要跑（模块名搜不到自己）
        for n in names:
            if any(m in n for m in mods):
                hit.append(n)
        hit = sorted(set(hit))
        if not args.slow:                      # 慢钉子只在 --slow / --all 时带
            _dropped = [n for n in hit if n in SLOW]
            hit = [n for n in hit if n not in SLOW]
            if _dropped:
                print(f"⏭ 跳过 {len(_dropped)} 个慢钉子（要约 15~55s/个）："
                      + "、".join(_dropped) + "（要跑加 --slow）")
        return hit
    if args.keyword:
        kw = args.keyword
        return [n for n in names
                if kw in n or kw in _read(os.path.join(HERE, n))]
    return [n for n in CORE if n in names]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed", action="store_true", help="只跑改过文件涉及的那些")
    ap.add_argument("--all", action="store_true", help="全套（发版前）")
    ap.add_argument("--slow", action="store_true", help="连慢钉子一起跑（15~55s/个）")
    ap.add_argument("-k", "--keyword", default="", help="按关键词挑钉子")
    ap.add_argument("--list", action="store_true", help="只列表不跑")
    ap.add_argument("--show", action="store_true", help="失败的钉子把尾巴打出来")
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()

    names = pick(args)
    if not names:
        print("❌ 一个钉子都没挑到（用 --all 跑全套）")
        sys.exit(2)
    print(f"⚡ 快检：{len(names)} 个钉子（全套 {len(all_nails())} 个）")
    if args.list:
        for n in names:
            print("   " + n)
        return

    fails, t0 = [], time.time()
    # 🖨 2026-11-06：子进程的 stdout 是**管道** ⇒ Python 默认按 locale 编码（中文 Windows = gbk），
    #    钉子里的 ✅/❌ 直接 UnicodeEncodeError ⇒ **明明全绿也报红**（`_state_ab`/`_seed_season`/
    #    `_tool_inventory_sync` 这些"还没 import 服务器就先 print"的钉子全中）。
    #    以前是控制台里跑（控制台走 UTF-8 的 console API）所以看不出来 —— 一进 `_quick` 就变红。
    #    ⇒ 统一给子进程加 `-X utf8`（+ PYTHONIOENCODING 兜底）：**红只有一种含义 = 真红**。
    _env = dict(os.environ)
    _env["PYTHONIOENCODING"] = "utf-8"
    for n in names:
        t = time.time()
        try:
            r = subprocess.run([sys.executable, "-X", "utf8", os.path.join(HERE, n)],
                               cwd=HERE, capture_output=True, timeout=args.timeout, env=_env)
            code = r.returncode
            tail = (r.stdout or b"").decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            code, tail = 124, f"超时 {args.timeout}s"
        dt = time.time() - t
        print(f"  {'✅' if code == 0 else '❌'} {n:<40} {dt:5.1f}s")
        if code != 0:
            fails.append(n)
            if args.show:
                for ln in tail.splitlines()[-12:]:
                    if "❌" in ln:
                        print("       " + ln.strip())
    dt = time.time() - t0
    print("\n" + (f"✅ {len(names) - len(fails)}/{len(names)} 通过（{dt:.1f}s）" if not fails
                  else f"❌ {len(fails)} 个红：" + "、".join(fails) + f"（{dt:.1f}s）"))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
