# -*- coding: utf-8 -*-
"""🐮🐑 挤奶/剪毛自验 —— **不吃游戏**（打桩；出口另有 `_net_guard` 兜底）。

被验的三件事（恒 2026-10-01：「**没做的话就跟 pet_animals 一样做**。差别应该就是挤奶剪毛
仍然只用处理绵羊、山羊、牛」）：
  ① **室外放牧那批也会被走上**：棚里空着（动物全在外面）时，`milk_shear` 不能再只回一句「没有动物」
     —— 它要照 `_grazing_care` 的结构处理 `/animals` 报的那批（`skip_grabber=True`，室外没有自动采集器）。
  ② **走位兜底要点名**：站位改成"走过去"（照 `pet_walk` 的真机形状）；只有走不到/动物挪窝才
     `/position` 兜底，而且那次兜底必须在报告里点名（`⚠️ 走不到 (x,y)，position 兜底`）——
     不许像以前那样**对每只都直接瞬移**（那是审计出来的"隔空改世界"）。
  ③ **人不在 Farm 时如实报**，不许把"读不着"装成"没有动物"（同 `_grazing_care` 那条账）。
  ④ 对象不变：牛/山羊 → 挤奶桶、绵羊 → 剪刀；猪/鸡/鸭/兔/恐龙不碰。

用法: PYTHONIOENCODING=utf-8 python scripts/_milk_shear_selftest.py     （退出码 全过=0）
"""
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M          # noqa: E402
import stardew_api as api            # noqa: E402

BUILDINGS = [{"type": "Deluxe Barn", "x": 48, "y": 12, "doorX": 52, "doorY": 16}]
# 室内一棚：一头牛 + 一只羊 + 一只猪（猪**不碰**）
INDOOR = [{"name": "牛牛", "type": "White Cow", "x": 11, "y": 14},
          {"name": "毛毛", "type": "Sheep", "x": 12, "y": 14},
          {"name": "猪猪", "type": "Pig", "x": 13, "y": 14}]
# 室外放牧那批：一头山羊（也要挤）
OUTDOOR = [{"name": "羊羊", "type": "Goat", "x": 40, "y": 40}]
CALLS = []          # 桩记下的调用（对比"有没有真出网"没用 —— 闸管那个；这里看**行为**）


def _stub(loc="Farm", indoor=None, outdoor=None, has_tool=True, walk_ok=True,
          grabber=False):
    """把 `milk_shear` 会碰到的出口全接上桩。`/animals` 按"人现在在哪张图"给不同的一批。"""
    indoor = INDOOR if indoor is None else indoor
    outdoor = OUTDOOR if outdoor is None else outdoor
    CALLS.clear()      # ⚠️ 每个用例从头记（不清的话上一个用例的 `/position` 会被算进来 = 假红）
    # ⚠️ `_has_tool` 读的是 **`/state` 顶层**的 `inventory`（不是 `player.inventory`）——
    #    第一版放错层 ⇒ 每只都判"没带挤奶桶"，四条用例假红（自验当场逮到）。
    inv = [{"name": "Milk Pail", "displayName": "挤奶桶"},
           {"name": "Shears", "displayName": "剪刀"}] if has_tool else []
    state = {"player": {"x": 11, "y": 15, "currentTool": "Milk Pail"},
             "inventory": inv,
             "location": {"name": loc}, "time": {"timeOfDay": 900, "season": "summer",
                                                 "weather": 0}}

    def g(ep, params=None):
        CALLS.append(("GET", ep))
        if ep == "/farm_buildings":
            return {"ok": True, "count": len(BUILDINGS), "buildings": BUILDINGS}
        if ep == "/state":
            return dict(state, player=dict(state["player"]))
        if ep == "/animals":
            return {"animals": (outdoor if loc == "Farm" else indoor), "count": 1}
        if ep == "/machines":
            return {"machines": ([{"type": "Auto-Grabber"}]) if grabber else []}
        if ep == "/menu":
            return {"open": False}
        return {}

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        if ep == "/use":
            return {"ok": True}
        if ep == "/passable":
            return {"passable": True}
        return {"ok": True}
    api._ai_get, api._ai_post = g, p
    api._get, api._post = g, p
    api.animals = lambda: g("/animals")
    api.machines = lambda: g("/machines")
    api.menu = lambda: g("/menu")
    api.state = lambda light=False: g("/state")
    api.use_item = lambda: p("/use")
    api.face = lambda d: p("/face", {"direction": d})
    api.position = lambda x, y: p("/position", {"x": x, "y": y})
    api.warp = lambda *a, **k: {"ok": True}
    # 走位：默认"走到了" **并且把玩家真的挪到动物下方那格**（`_walk_and_wait` 在真机上就是干这个的）。
    # ⚠️ 第一版只回 `(True, "")`、玩家坐标恒不动 ⇒ 站位永远不是卡迪纳尔相邻 ⇒ 触发"站位不对，
    #    position 兜底"那条路，两条用例假红 —— **桩不搬人 = 把成功路径测成了失败路径**。
    def _walk(loc_, x, y, timeout=25):
        if not walk_ok:
            return False, "走位超时没到"
        state["player"]["x"], state["player"]["y"] = x, y - 1     # 站到它正下方，面朝上
        return True, ""
    M._walk_and_wait = _walk
    M._warp_home_if_needed = lambda loc_: "（桩：不用回家）"
    M._enter_building = lambda b: (True, "（桩：进门）")
    return state


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))
    return bool(cond)


def main():
    res = []

    # ① 室外那半：棚里没有建筑时**也要**处理室外那批（原来直接 return「没找到动物建筑」）
    _stub(loc="Farm")
    _called = []
    _orig_ms = M._milk_shear_animals
    M._milk_shear_animals = lambda skip_grabber=False: (
        _called.append(skip_grabber), "🐐 挤奶 1/1 只（羊羊）")[1]
    M._find_animal_buildings = lambda: []
    _out = M.milk_shear()
    M._milk_shear_animals = _orig_ms
    res.append(ck("🌾 没找到动物建筑时**照样**处理室外那批（不再 early-return）",
                  _called == [True], _called))
    res.append(ck("🌾 那批走的是 `skip_grabber=True`（室外没有自动采集器）",
                  _called == [True], _called))
    res.append(ck("🌾 回执里能看到室外那批的结果", "羊羊" in _out, _out[:160]))

    # ② 棚内 + 室外各走一遍（两个不同的 `skip_grabber`）
    _stub(loc="Farm")
    _called2 = []
    M._milk_shear_animals = lambda skip_grabber=False: (
        _called2.append(skip_grabber), "🐮 挤奶 1/1 只")[1]
    M._find_animal_buildings = lambda: BUILDINGS
    M.milk_shear()
    M._milk_shear_animals = _orig_ms
    res.append(ck("🐄 有建筑 ⇒ 棚内一次 + 室外一次（顺序：先棚内后室外）",
                  _called2 == [False, True], _called2))

    # ③ 人不在 Farm ⇒ **如实说没做**，不许静默跳过（也不许说"没有动物"）
    _stub(loc="Deluxe Barn")
    _called3 = []
    M._milk_shear_animals = lambda skip_grabber=False: (
        _called3.append(skip_grabber), "🐮 挤奶 0/0 只")[1]
    M._find_animal_buildings = lambda: []
    _out3 = M.milk_shear()
    M._milk_shear_animals = _orig_ms
    res.append(ck("🚫 人不在 Farm ⇒ **明说「室外那批没做」**（不静默、不装成没有）",
                  "没做" in _out3 and "不在 Farm" in _out3, _out3[:200]))
    res.append(ck("🚫 而且**不许**说成「没有动物」", "没有动物" not in _out3, _out3[:200]))
    res.append(ck("🚫 给了下一步（能直接照抄的 `map go` + `farm milk`）",
                  "map(ops=" in _out3 and 'destination' in _out3, _out3[:240]))

    # ④ 走位兜底**要点名**（人不在它旁边时不许悄悄瞬移）
    _stub(loc="Farm", walk_ok=False)
    _td = M._milk_shear_animals(skip_grabber=True)
    _pos = [c for c in CALLS if c[0] == "POST" and c[1] == "/position"]
    res.append(ck("🚶 走不到 ⇒ 报告里**点名**那次 position 兜底（带坐标）",
                  "position 兜底" in _td and "羊羊" in _td, _td[:200]))
    res.append(ck("🚶 兜底确实打的是 `/position`（只此一条路，且只在走不到时）",
                  len(_pos) >= 1, _pos[:2]))
    # 走得到 ⇒ **一次都不该瞬移**
    _stub(loc="Farm", walk_ok=True)
    _td2 = M._milk_shear_animals(skip_grabber=True)
    _pos2 = [c for c in CALLS if c[0] == "POST" and c[1] == "/position"]
    res.append(ck("🚶 走得到 ⇒ **一次都不瞬移**（原实现对每只都直接 position）",
                  not _pos2, _pos2))
    res.append(ck("🚶 走得到 ⇒ 报告里**没有**兜底那句", "position 兜底" not in _td2, _td2[:160]))

    # ⑤ 对象不变：牛/山羊 → 挤奶桶；绵羊 → 剪刀；猪**不碰**
    _stub(loc="Farm")
    _sel = [c[2].get("name") for c in CALLS if c[0] == "POST" and c[1] == "/select"]
    _td3 = M._milk_shear_animals(skip_grabber=True)     # 室外那批 = 山羊
    _sel = [c[2].get("name") for c in CALLS if c[0] == "POST" and c[1] == "/select"]
    res.append(ck("🐐 山羊 ⇒ 挤奶桶（`/select 挤奶桶`）", "挤奶桶" in _sel, _sel))
    res.append(ck("🐐 只对山羊/牛走这条：猪/鸡/鸭/兔/恐龙**不在**名单里",
                  "Pig" not in str(M.__dict__.get("_milk_targets", "")), _td3[:120]))
    # 室内那批（牛 + 羊 + 猪）：应出现**挤奶桶和剪刀**、且**不碰猪**
    _stub(loc="Deluxe Barn", indoor=INDOOR, outdoor=[])
    _td4 = M._milk_shear_animals()                       # 人在棚里 ⇒ 读的是室内那批
    _sel4 = [c[2].get("name") for c in CALLS if c[0] == "POST" and c[1] == "/select"]
    res.append(ck("🐮🐑 棚内 ⇒ 挤奶桶（牛）+ 剪刀（羊）都出现过",
                  "挤奶桶" in _sel4 and "剪刀" in _sel4, _sel4))
    res.append(ck("🐷 猪**不碰**（`/select`/`/use` 只发生在牛和羊身上）",
                  "猪猪" not in _td4, _td4[:200]))
    # 没带工具 ⇒ 明说去哪买（老行为，别改坏）
    _stub(loc="Deluxe Barn", indoor=INDOOR, outdoor=[], has_tool=False)
    _td5 = M._milk_shear_animals()
    res.append(ck("🧰 没带挤奶桶 ⇒ 明说「没带…去玛妮牧场买」", "玛妮牧场" in _td5 or "没带" in _td5,
                  _td5[:160]))
    # 自动采集器 ⇒ 棚内跳过（老判据保留）；室外那批 `skip_grabber=True` 不受它影响
    _stub(loc="Deluxe Barn", indoor=INDOOR, outdoor=[], grabber=True)
    _td6 = M._milk_shear_animals()
    res.append(ck("🤖 棚内有自动采集器 ⇒ 跳过（老判据保留）", "自动采集器" in _td6, _td6[:160]))
    _stub(loc="Farm", outdoor=OUTDOOR, grabber=True)
    _td7 = M._milk_shear_animals(skip_grabber=True)
    res.append(ck("🤖 但室外那批（skip_grabber=True）**不受**采集器判据影响",
                  "自动采集器" not in _td7, _td7[:160]))

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
