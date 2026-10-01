# -*- coding: utf-8 -*-
"""🚪🐄 门的两句**农场待办**自验 —— **不吃游戏**（打桩；出口另有 `_net_guard` 兜底）。

被验的是状态条里那两句（恒：「在非雨天的早晨且有畜牧建筑时，跟其他农场待办注入一样**每天第一次**
播报，后续隐藏+放在待办单。**19 点之后第一次进入农场或者正位于农场**，注入提醒给动物关门」）：
  · 早晨：`_doors_morning_hint()` —— 挂在 `_build_state_strip` 的 `if morning:`（= 每天第一次）；
  · 晚上：`_doors_evening_hint(loc)` —— 照 `_TRAVEL_CART_KEY` 那套**一天一条**去重。

⚠️ 两句都**不许声称门现在是开还是关**（门态没有只读口：`/toggle_doors` 是翻转端点，读=翻）
   ⇒ 只能写"该放牧了/该关门了 + 敲哪个 op"，并说明**回执会报执行后的门态**。这条在这里被钉住。

用法: PYTHONIOENCODING=utf-8 python scripts/_doors_todo_selftest.py     （退出码 全过=0）
"""
import io
import os
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M          # noqa: E402
import stardew_api as api            # noqa: E402

BUILDINGS = [{"type": "Deluxe Coop", "x": 40, "y": 12, "doorX": 44, "doorY": 16},
             {"type": "Deluxe Barn", "x": 48, "y": 12, "doorX": 52, "doorY": 16},
             {"type": "Greenhouse", "x": 28, "y": 20}]      # ⚠️ 非动物建筑：不许被数进去
DAY = {"v": "summer|3|1"}


def _stub(tod=800, season="summer", weather=0, buildings=BUILDINGS, buildings_raise=False):
    """把两个只读口接上桩（`/state` 的时钟 + `/farm_buildings`）；**一个字节都不出网**。"""
    t = {"timeOfDay": tod, "season": season, "dayOfMonth": 3, "year": 1, "weather": weather}

    def g(ep, params=None):
        if ep == "/farm_buildings":
            if buildings_raise:
                raise RuntimeError("模拟：/farm_buildings 读不到")
            return {"ok": True, "count": len(buildings), "buildings": buildings}
        if ep == "/state":
            return {"player": {"x": 12, "y": 12}, "location": {"name": "Farm"}, "time": t}
        return {}
    api._ai_get = g
    api.day_key = lambda: DAY["v"]
    M._DOOR_CLOSE_KEY["last"] = None          # 每个用例从"今天还没报过"开始


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))
    return bool(cond)


def main():
    res = []

    # ① 早晨那句：非雨天 + 非冬天 + 有畜牧建筑 + 12:00 前
    _stub(tod=800)
    _m = M._doors_morning_hint()
    res.append(ck("🌅 08:00 · 晴 · 夏 · 有畜棚 ⇒ 给早晨那句", bool(_m), _m))
    res.append(ck("🌅 那句带**能直接照抄的 op**（`farm(ops=\"doors\")`）",
                  'farm(ops="doors")' in _m, _m))
    res.append(ck("🌅 那句说清**回执会报执行后的门态**", "回执" in _m and "门态" in _m, _m))
    res.append(ck("🌅 **不许声称门现在是开还是关**（没有只读口）",
                  "门现在是" not in _m and "开着" not in _m and "关着" not in _m, _m))
    # 钟点边界：12:00 起就不是"早晨"了
    _stub(tod=1159)
    res.append(ck("🌅 11:59 ⇒ 还给", bool(M._doors_morning_hint())))
    _stub(tod=1200)
    res.append(ck("🌅 12:00 ⇒ **不给**（边界：< 12:00 才算早晨）", not M._doors_morning_hint()))
    _stub(tod=1400)
    res.append(ck("🌅 14:00 ⇒ 不给（下午了）", not M._doors_morning_hint()))
    # 天气/季节
    for _w, _why in ((1, "雨"), (2, "雷暴"), (7, "绿雨")):
        _stub(tod=800, weather=_w)
        res.append(ck(f"🌅 {_why}天 ⇒ **不给**（动物不出去吃草）", not M._doors_morning_hint()))
    for _w, _why in ((0, "晴"), (3, "风"), (5, "雪")):
        _stub(tod=800, weather=_w)
        res.append(ck(f"🌅 {_why}天 ⇒ 给（雪不是雨；冬天另判）", bool(M._doors_morning_hint())))
    _stub(tod=800, season="winter")
    res.append(ck("🌅 冬天 ⇒ **不给**", not M._doors_morning_hint()))
    # 建筑：没有 / 读不到 —— 两种都**不出现**（宁可不给，不编）
    _stub(tod=800, buildings=[])
    res.append(ck("🌅 本档没有动物建筑 ⇒ 不给", not M._doors_morning_hint()))
    _stub(tod=800, buildings=[{"type": "Greenhouse"}, {"type": "Shed"}])
    res.append(ck("🌅 只有非动物建筑（温室/棚屋）⇒ 不给（`type` 里没有 Coop/Barn）",
                  not M._doors_morning_hint()))
    _stub(tod=800, buildings_raise=True)
    res.append(ck("🌅 `/farm_buildings` 读不到 ⇒ **不给**（不许拿默认值兜底）",
                  not M._doors_morning_hint()))
    _stub(tod=800, season="")
    res.append(ck("🌅 季节读不到 ⇒ 不给（「算不出」就不出现）", not M._doors_morning_hint()))

    # ② 晚上那句：≥19:00 且**人在 Farm**（一天一条）
    _stub(tod=1900)
    _e = M._doors_evening_hint("Farm")
    res.append(ck("🌙 19:00 · 在 Farm ⇒ 给晚上那句", bool(_e), _e))
    res.append(ck("🌙 那句带 op（`farm(ops=\"doors\")`）+ 说清回执报门态",
                  'farm(ops="doors")' in _e and "回执" in _e, _e))
    res.append(ck("🌙 **不许声称门现在是开还是关**",
                  "门现在是" not in _e and "开着" not in _e and "关着" not in _e, _e))
    _stub(tod=1859)
    res.append(ck("🌙 18:59 ⇒ 不给（边界：≥19:00 才提醒）", not M._doors_evening_hint("Farm")))
    _stub(tod=1900)
    res.append(ck("🌙 19:00 但**人不在 Farm** ⇒ 不给（别在矿里喊他回去关门）",
                  not M._doors_evening_hint("Mine")))
    res.append(ck("🌙 而且**不消费**今天那个名额（等回农场还要提醒）",
                  M._DOOR_CLOSE_KEY["last"] is None, M._DOOR_CLOSE_KEY))
    res.append(ck("🌙 …回到 Farm（19:30）⇒ 仍然给", bool(M._doors_evening_hint("Farm"))))
    res.append(ck("🌙 **一天一条**：当天第二次在 Farm ⇒ 不再出现",
                  not M._doors_evening_hint("Farm")))
    DAY["v"] = "summer|4|1"                     # 换天
    _stub(tod=1900)                             # ⚠️ `_stub` 会清 key —— 这行之后会重置
    M._DOOR_CLOSE_KEY["last"] = "summer|3|1"    # 手工摆回"昨天已经报过"
    res.append(ck("🌙 换天 ⇒ 重新给一条", bool(M._doors_evening_hint("Farm"))))
    DAY["v"] = "summer|3|1"
    # 雨夜照报（不开门 ≠ 不关门）；冬天照报
    for _w in (1, 7):
        _stub(tod=1900, weather=_w)
        res.append(ck(f"🌙 雨/绿雨夜（weather={_w}）⇒ **照报**关门", bool(M._doors_evening_hint("Farm"))))
    _stub(tod=1900, season="winter")
    res.append(ck("🌙 冬天晚上 ⇒ 照报", bool(M._doors_evening_hint("Farm"))))
    _stub(tod=1900, buildings=[])
    res.append(ck("🌙 没有动物建筑 ⇒ 不给", not M._doors_evening_hint("Farm")))
    _stub(tod=1900, buildings_raise=True)
    res.append(ck("🌙 `/farm_buildings` 读不到 ⇒ **不给**", not M._doors_evening_hint("Farm")))

    # ③ 文案规矩（AI 看得到的字）：不许出现改动史/日期/人名
    _stub(tod=800)
    _m2 = M._doors_morning_hint()
    _stub(tod=1900)
    _e2 = M._doors_evening_hint("Farm")
    for _txt, _who in ((_m2, "早晨"), (_e2, "晚上")):
        res.append(ck(f"📝 {_who}那句**没有改动史/日期**（不写 2026-xx-xx / 今天改的）",
                      "2026-" not in _txt and "2026年" not in _txt and "改" not in _txt, _txt))
        # ⚠️ 判据照 `_cn_quote_check.py`：**中文旁边**不许出现半角直引号（`farm(ops="doors")`
        #    那种**代码里的引号**不算 —— 它两边不是中文）。踩过 11 次的那个坑是 `中"中`。
        _badq = re.search(r'[\u4e00-\u9fff]"|"[\u4e00-\u9fff]', _txt)
        res.append(ck(f"📝 {_who}那句中文旁边没有半角直引号（中文一律「」）",
                      _badq is None, _badq.group(0) if _badq else _txt))

    # ④ 接线：早晨那句**只在"每天第一次"那条路上**（`if morning:`）；晚上那句每次都在
    _stub(tod=800)
    _d = {"player": {"x": 12, "y": 12, "health": 100, "maxHealth": 100, "stamina": 268,
                     "maxStamina": 270, "money": 1000, "maxItems": 36},
          "location": {"name": "Farm"},
          "time": {"timeOfDay": 800, "season": "summer", "dayOfMonth": 3, "year": 1,
                   "weather": 0},
          "inventory": [], "alerts": [], "activeMenu": None, "activeEvent": None}
    _s_full = M._build_state_strip(dict(_d), full=True, morning="🌅 农场晨报:（桩）")
    res.append(ck("🧩 接线上：`full=True`（每天第一次）⇒ 状态条里有早晨那句",
                  "今天能放牧" in _s_full, _s_full[-160:]))
    _s_lite = M._build_state_strip(dict(_d), full=False, morning="")
    res.append(ck("🧩 接线上：`full=False`（当天后续）⇒ 早晨那句**不出现**（不重复播报）",
                  "今天能放牧" not in _s_lite)) 
    _stub(tod=1900)
    _d2 = dict(_d, time=dict(_d["time"], timeOfDay=1900))
    _s_eve = M._build_state_strip(dict(_d2), full=False, morning="")
    res.append(ck("🧩 接线上：19:00 在 Farm ⇒ 状态条里有晚上那句",
                  "去把棚门关了" in _s_eve, _s_eve[-160:]))
    _d3 = dict(_d2, location={"name": "Mine"})
    res.append(ck("🧩 接线上：19:00 在矿里 ⇒ **没有**那句（别在矿里喊他回去关门）",
                  "去把棚门关了" not in M._build_state_strip(dict(_d3), full=False, morning="")))

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
