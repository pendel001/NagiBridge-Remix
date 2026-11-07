# -*- coding: utf-8 -*-
"""📺 「怎么看电视」查得到、也敲得出去 —— 纯 Python 自验。2026-11-08

恒：「**你确定家具的 poi 给了吗……ai 查什么都查不到怎么看电视**」。

查证两件事（都离线跑，不碰游戏）：
  ① **POI 是给了**：`locations.legal_tokens_here("FarmHouse")` ⇒
     `[('农舍(电视)',3,7), ('农舍(床)',5,8), ('农舍(壁炉)',8,4)]`，`map here` 会连坐标一起印；
  ② **但 AI 问不到**：状态条那句尾巴写死 `（scene at 点电视）` —— `scene at` 只吃
     `tile_x/tile_y`（数字），**「点电视」既不是坐标也不是地名** ⇒ 一块**假路牌**；
     而 `help("怎么看电视")` 当时也直接回「没有这个指引」（`_INTENT_INDEX` 没电视这条）。

判据：
  ① 状态条那行里的 token 必须**真是** POI 里的名字（拿 `legal_tokens_here("FarmHouse")` 对）；
  ② 不许再出现 `scene at 中文`（点的是格子，不是名字）；
  ③ `help("怎么看电视")` / `help("离地而居")` 都要命中，且给的域/op 真存在；
  ④ 三个播出日（离地而居 / 酱料女王 / 无节目）都得给同一套真路牌，不空心；
  ⑤ 点破「电视在**农舍**」——AI 在自己 Cabin 里是找不到电视的。
"""
import os
import re
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import locations
import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# 🧪 只读：`_intent_search` 会顺手去找"地点候选"（那要打游戏）⇒ 这里把它摘掉，只验工具那一路。
M._place_lines = lambda q, n=3: []

_TOKENS = {n for (n, _x, _y) in locations.legal_tokens_here("FarmHouse")["poi"]}

try:
    print("\n① 状态条那行是真路牌（token 对得上 POI 表）")
    for _show, _label in (("离地而居", "离地而居"), ("酱料女王", "酱料女王"), (None, "无新节目")):
        _ln = M._tv_hint_line(_show)
        ck(f"①[{_label}] 非空", bool(_ln.strip()), repr(_ln))
        _m = re.search(r"poi_name=([^\s`]+)", _ln)
        ck(f"①[{_label}] 给了 `poi_name=`", bool(_m), _ln)
        ck(f"①[{_label}] 那个名字**真在 POI 表里**", bool(_m) and _m.group(1) in _TOKENS,
           f"{_m.group(1) if _m else '?'} ∉ {sorted(_TOKENS)}")
        ck(f"①[{_label}] 名字是**农舍的电视**（别写成小屋）",
           bool(_m) and _m.group(1).startswith("农舍(") and "电视" in _m.group(1), _ln)
        ck(f"①[{_label}] 给了第二步（单子那行动作）", "intent show" in _ln, _ln)

    print("\n② 不许再出现假路牌 `scene at 中文`（点的是格子）")
    for _show in ("离地而居", "酱料女王", None):
        _ln = M._tv_hint_line(_show)
        _bad = re.findall(r"scene at\s*([^\s（(,，。]*)", _ln)
        ck(f"②[{_show or '无'}] 没有 `scene at <非坐标>`", not _bad or all(b.isdigit() for b in _bad if b),
           str(_bad))
        ck(f"②[{_show or '无'}] 没有旧文案「点电视」当参数", "at 点电视" not in _ln, _ln)

    print("\n③ `help` 查得到（从「我想看电视」跳到「敲哪条」）")
    for _q in ("怎么看电视", "看电视", "离地而居", "今天天气", "看运势"):
        _out = M.help(_q)
        ck(f"③help({_q}) 没掉进「没有指引」", "没有「" not in _out, _out[:160])
        ck(f"③help({_q}) 指到 map walk（第一步就是走过去）", 'map(ops="walk")' in _out, _out[:200])
        ck(f"③help({_q}) 点破了农舍(电视)这个真 token", "农舍(电视)" in _out, _out[:200])

    print("\n④ 域/op 真的存在（别指到空气）")
    _entry = [e for e in M._INTENT_INDEX if any("看电视" in k for k in e[0].split(","))]
    ck("④意图索引里有「看电视」那条", bool(_entry), str(len(_entry)))
    if _entry:
        _kws, _dom, _op, _hint = _entry[0]
        ck("④域 = map（`intent` 没有 dispatch dict，指过去 domain_selftest 会红）", _dom == "map", _dom)
        ck("④op = walk", _op == "walk", _op)
        ck("④说明里写清了要先去农舍", "农舍" in _hint, _hint)
        ck("④说明里给了 POI 真 token", "农舍(电视)" in _hint, _hint)
        ck("④说明里第二步给了单子那行", "intent show" in _hint, _hint)

    print("\n⑤ 源码钉：状态条不再自己拼字符串（只有一处真路牌）")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "nagi_mcp_server.py"), encoding="utf-8").read()
    ck("⑤状态条调的是 `_tv_hint_line(`", "lines.append(_tv_hint_line(_tv_show_today(day)))" in _src)
    # ⚠️ 注释里**允许**提旧文案（那是案情记录）；只不许它出现在**代码**里。
    _bad_lines = [ln.strip() for ln in _src.splitlines()
                  if "scene at 点电视" in ln and not ln.lstrip().startswith("#")]
    ck("⑤旧假路牌只留在注释里（代码里一处都没有）", not _bad_lines, str(_bad_lines[:3]))
    ck("⑤`_TV_HOW` 只定义一次（真路牌单一来源）", _src.count("_TV_HOW = ") == 1,
       f"出现 {_src.count('_TV_HOW = ')} 次")
finally:
    pass

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 电视：POI 名字是真的、状态条给的是能敲的两步、`help` 问得到。")
