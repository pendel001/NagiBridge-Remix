# -*- coding: utf-8 -*-
"""🎂 生日表 / 💎 齐钻显示 —— 纯 Python 自验（不起服务、不碰游戏）。2026-11-01

## 恒原话（两条，都是今天一玩就撞见的）
> 「我发现付了好多东西呀，比如**第二年才回来的肯特怎么第一年也显示生日**。」
> 「然后，**齐钻不在核桃房的话不用显示哦**，只有核桃房买东西需要，或者在矿井爆齐钻就数量变化才报。」

## 这次真查出来的（不是只有肯特那一条）
旧 `BIRTHDAYS` 表**大面积错位**（对照 wiki 四季生日表）：
  · `spring 4` 写成 Jas（真：**Kent**）· `spring 7` 写成 Vincent（真：**Lewis**）
  · `spring 10` 写成 Kent（真：**Vincent**）· `spring 19` 写成 Pierre（真：**26**）
  · `spring 26` 写成 Caroline（真：**Pierre**）· `spring 27` 写成 Elliott（真：**Emily**）
  · `summer 4` 写成 Lewis（真：**Jas**）· `summer 8` 写成 Clint（真：**Gus**）
  · `summer 26` 写成 Penny（真：**Leo**）· `fall 2` 写成 Pierre（真：**Penny**）
  · `winter 26` 写成 Shane（真：**Clint**）· `winter 27` 写成 Emily（真：**没有 27**）
  · **Alex 整个不在表里**（真：summer 13）
⇒ 表已按 wiki 四季页重建（34 位可送礼村民，四季 8/9/8/9）。

## 钉死四条
  ① 34 位一个不多一个不少（不重号）；② 抽查 12 个易错点；
  ③ 肯特**第一年不报**（他秋天/春天才回镇 —— wiki：「away serving in the army throughout
     the first year and returns ... during spring of year two」），第二年照报；
  ④ 齐钻只在「核桃房+商店开着」或「数量变了」时出现，**每天那发完整版不再报**。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calendar_data as C
import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


print("① 四季生日表：34 位、不重号、不多不少")
_n = len(C.BIRTHDAYS)
ck(f"…共 {_n} 条 = 34 位可送礼村民", _n == 34, str(_n))
_names = [str(v).split(" ")[0].strip() for v in C.BIRTHDAYS.values()]
_dup = sorted({x for x in _names if _names.count(x) > 1})
ck("…**没有同一个人占两天**", not _dup, f"重复：{_dup}")
_per_season = {}
for (_s, _d) in C.BIRTHDAYS:
    _per_season[_s] = _per_season.get(_s, 0) + 1
ck("…四季条数 8/9/8/9（夏冬各 9，四季页原话 'tied with Summer for most birthdays'）",
   _per_season == {"spring": 8, "summer": 9, "fall": 8, "winter": 9}, str(_per_season))

print("\n② 抽查（这些正是旧表错的地方 / wiki 四季页逐条对过）")
for (_s, _d), _want in (
    (("spring", 4), "Kent"), (("spring", 7), "Lewis"), (("spring", 10), "Vincent"),
    (("spring", 26), "Pierre"), (("spring", 27), "Emily"),
    (("summer", 4), "Jas"), (("summer", 8), "Gus"), (("summer", 13), "Alex"),
    (("summer", 26), "Leo"), (("fall", 2), "Penny"), (("fall", 5), "Elliott"),
    (("winter", 26), "Clint"),
):
    _got = str(C.BIRTHDAYS.get((_s, _d), "")).split(" ")[0].strip()
    ck(f"…{_s} {_d} ⇒ {_want}", _got == _want, _got)
ck("…**没有** winter 27（旧表多出来的那行）", ("winter", 27) not in C.BIRTHDAYS)

print("\n③ 肯特第一年不报（恒 2026-11-01 抓到的）")
ck("…year=1 ⇒ 不出现", C._birthday_visible("Kent ⚔️（第二年才到镇上）", 1) is False)
ck("…year=2 ⇒ 出现", C._birthday_visible("Kent ⚔️（第二年才到镇上）", 2) is True)
ck("…别的人不受年份影响（Vincent year=1 ⇒ 出现）",
   C._birthday_visible("Vincent 🧒", 1) is True)
ck("…空值不报（不编）", C._birthday_visible("", 3) is False)
_spring4_y1 = C.build_calendar_line("spring", 4, 0, 1)
_spring4_y2 = C.build_calendar_line("spring", 4, 0, 2)
ck("…日历行：第一年 4 号**不写 Kent**", "Kent" not in _spring4_y1, _spring4_y1)
ck("…日历行：第二年 4 号**写 Kent**", "Kent" in _spring4_y2, _spring4_y2)
ck("…`get_today_info` 也走同一道闸（第一年不报）",
   not any("Kent" in (x or "") for x in C.get_today_info("spring", 4, 1)),
   str(C.get_today_info("spring", 4, 1)))

print("\n④ 齐钻：不在核桃房就不报（只有「核桃房+商店开着」或「数量变了」）")
ck("…街上 + 没变化 ⇒ **不报**（每天那发完整版也不报）", M._qi_gem_show("Town", "", False) is False)
ck("…矿里刚爆出一颗（数量变了）⇒ 报", M._qi_gem_show("UndergroundMine", "", True) is True)
ck("…核桃房但商店没开 ⇒ 不报", M._qi_gem_show("QiNutRoom", "GameMenu", False) is False)
ck("…核桃房 + 商店开着 ⇒ 报（那一刻齐钻是钱）",
   M._qi_gem_show("QiNutRoom", "ShopMenu", False) is True)
ck("…核桃房 + 商店开着、数量也没变 ⇒ 照样报",
   M._qi_gem_show("QiNutRoom", "ShopMenu", True) is True)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
