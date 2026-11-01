# -*- coding: utf-8 -*-
"""🎉 「恒在森林里参加节日」——平时不该播成节日 —— 纯 Python 自验（不碰游戏）。2026-11-01

## 恒原话
> 「还有这个"**恒在森林里参加节日**"hhh ai 吓了一跳"**今天哪儿有节日**"」

## 病根
`player_activity.describe_activity` 的"节日"那一支原来**只拿地图名**去撞
`FESTIVAL_LOCATIONS = {"town": 节日广场, "beach": 海滩, "forest": 森林}`：

```python
for keyword, display in FESTIVAL_LOCATIONS.items():
    if keyword in loc_lower:                       # ← 平时站森林也命中
        return f"🎉 **{name}** 正在{display}参加节日活动"
```

⇒ **任何一天**只要恒站在镇上/海滩/森林，AI 就被"事实陈述"告知他在过节。
（不是"猜测"句式，AI 会当真 —— 于是去查一个不存在的节日。）

## 修法（两条同时成立才报）
  ① 今天**真有**节日：问 `calendar_data.FESTIVAL_LOCATIONS[(季, 日)]`；
  ② 人就在**这场节日的场地**（表里那张图），或者已在节日临时图（`Temp`/名字带 festival）。

## 钉死
  ① 无节日日 + 森林 ⇒ **不报节日**；② 无节日日 + 海滩/镇上 ⇒ 不报；
  ③ 节日当天 + 场地对 ⇒ 报；④ 节日当天 + 场地**不对** ⇒ 不报；
  ⑤ 节日临时图（`Temp`）⇒ 报；⑥ 非节日日 + `Temp` ⇒ 不报（不编）。
"""
import io
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import player_activity as P

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _st(season, day, loc, moving=False):
    """一份最小的 `/state` 形状（够走到"节日"那一支）。"""
    return {
        "player": {"name": "恒", "x": 10, "y": 10, "health": 100, "maxHealth": 100,
                   "stamina": 270, "maxStamina": 270, "isMoving": moving,
                   "stationarySeconds": 1, "fishing": {}, "currentTool": ""},
        "location": {"name": loc},
        "time": {"season": season, "dayOfMonth": day, "timeOfDay": 1200},
        "inventory": [], "alerts": [], "activeMenu": None,
        "raw": {"time": {"season": season, "dayOfMonth": day}, "npcs": []},
    }


print("① 平常日子（恒今天撞到的就是这个）")
for _loc in ("Forest", "Beach", "Town"):
    _out = P.describe_activity(_st("spring", 10, _loc))
    ck(f"…spring 10（**没有节日**）+ {_loc} ⇒ 不许说「节日」", "节日" not in _out, _out)

print("\n② 节日当天 + 场地对 ⇒ 照报")
_out = P.describe_activity(_st("spring", 13, "Town"))      # 蛋蛋节在镇上
ck("…spring 13 + Town ⇒ 报节日", "节日" in _out, _out)
_out = P.describe_activity(_st("spring", 24, "Forest"))    # 花舞节在森林
ck("…spring 24 + Forest ⇒ 报节日", "节日" in _out, _out)

print("\n③ 节日当天 + 场地**不对** ⇒ 不报（宁缺勿编）")
_out = P.describe_activity(_st("spring", 24, "Beach"))     # 花舞节不在海滩
ck("…spring 24 + Beach ⇒ 不许说节日", "节日" not in _out, _out)
_out = P.describe_activity(_st("spring", 13, "Forest"))    # 蛋蛋节不在森林
ck("…spring 13 + Forest ⇒ 不许说节日", "节日" not in _out, _out)

print("\n④ 节日临时图")
_out = P.describe_activity(_st("spring", 13, "Temp"))
ck("…节日当天 + `Temp` ⇒ 报节日", "节日" in _out, _out)
_out = P.describe_activity(_st("spring", 10, "Temp"))
ck("…**非**节日日 + `Temp` ⇒ 不许说节日（不编）", "节日" not in _out, _out)

print("\n⑤ 读不到日历也不许瞎报")
_bad = _st("spring", 10, "Forest")
_bad["raw"] = {"time": {}, "npcs": []}
_bad["time"] = {}
ck("…季/日读不到 ⇒ 不报节日", "节日" not in P.describe_activity(_bad), P.describe_activity(_bad))

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
