# -*- coding: utf-8 -*-
"""🐮🐑 挤奶/剪毛自验 —— **不吃游戏**（打桩；出口另有 `_net_guard` 兜底）。

被验的事（恒 2026-10-01：「**没做的话就跟 pet_animals 一样做**。差别应该就是挤奶剪毛
仍然只用处理绵羊、山羊、牛」+ 真机第一跑照出的两个洞 A/B）：
  ① **室外放牧那批也会被走上**：棚里空着（动物全在外面）时，`milk_shear` 不能再只回一句「没有动物」
     —— 它要照 `_grazing_care` 的结构处理 `/animals` 报的那批（`skip_grabber=True`，室外没有自动采集器）。
  ② **走位兜底要点名**：站位改成"走过去"（照 `pet_walk` 的真机形状）；只有走不到/动物挪窝才
     `/position` 兜底，而且那次兜底必须在报告里点名 —— 不许像以前那样**对每只都直接瞬移**。
  ③ **人不在 Farm 时如实报**，不许把"读不着"装成"没有动物"（同 `_grazing_care` 那条账）。
  ④ 对象不变：牛/山羊 → 挤奶桶、绵羊 → 剪刀；猪/鸡/鸭/兔/恐龙不碰。
  ⑤ 🔴 **洞 A**（真机 23:0x）：`api.warp` 的**回包早于生效** ⇒ 出棚后必须 `_wait_on_map` 等到
     **确认站上 Farm** 再读 `/animals`；等不到就如实说「没确认回到农场」，
     **不许**把它写成「人还在<某建筑>」（那是读早一步**编出来的原因**）。
  ⑥ 🔴 **洞 B**（同日）：`❌ X 进不去` 原来把 `_enter_building()` 回的**真原因**吞了
     ⇒ 现在原话带出来（警告必须带路）。

⚠️ 写这个自验踩过的三个桩坑（都留了注释，别再踩）：`_has_tool` 读 `/state` **顶层** inventory；
   走位桩**不搬人**会把成功路径测成失败路径；`_find_animal_buildings`/`CALLS` **必须每个用例重置**。

用法: PYTHONIOENCODING=utf-8 python scripts/_milk_shear_selftest.py     （退出码 全过=0）
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

BUILDINGS = [{"type": "Deluxe Barn", "x": 48, "y": 12, "doorX": 52, "doorY": 16}]
# 室内一棚：一头牛 + 一只羊 + 一只猪（猪**不碰**）
INDOOR = [{"name": "牛牛", "type": "White Cow", "x": 11, "y": 14},
          {"name": "毛毛", "type": "Sheep", "x": 12, "y": 14},
          {"name": "猪猪", "type": "Pig", "x": 13, "y": 14}]
# 室外放牧那批：一头山羊（也要挤）
OUTDOOR = [{"name": "羊羊", "type": "Goat", "x": 40, "y": 40}]
CALLS = []          # 桩记下的调用（看**行为**：有没有瞬移、有没有轮询、走位请求的是哪格）
WALK_CALLS = []     # 走位请求过的目标格（验"重走"与"进棚请求门下方那格"）


def _stub(loc="Farm", indoor=None, outdoor=None, has_tool=True, walk_ok=True,
          grabber=False, warp_lag=0, enter_ok=True,
          enter_log="（桩：进门）", find_buildings=None,
          popup=None, use_adds_milk=False):
    """把 `milk_shear` 会碰到的出口全接上桩。`/animals` 按"人现在在哪张图"给不同的一批。

    `warp_lag=N` = **warp 回包早于生效**（2026-09-16/10-01 真机那个形状）：调过 `api.warp` 之后
    头 N 次 `/state` **仍然报"还在棚里"**，第 N+1 次才报 Farm ⇒ 用来验"必须等确认再读"。
    """
    indoor = INDOOR if indoor is None else indoor
    outdoor = OUTDOOR if outdoor is None else outdoor
    CALLS.clear()      # ⚠️ 每个用例从头记（不清的话上一个用例的 `/position` 会被算进来 = 假红）
    WALK_CALLS.clear()
    # ⚠️ `_has_tool` 读的是 **`/state` 顶层**的 `inventory`（不是 `player.inventory`）——
    #    第一版放错层 ⇒ 每只都判"没带挤奶桶"，四条用例假红（自验当场逮到）。
    inv = [{"name": "Milk Pail", "displayName": "挤奶桶"},
           {"name": "Shears", "displayName": "剪刀"}] if has_tool else []
    state = {"player": {"x": 11, "y": 15, "currentTool": "Milk Pail"},
             "inventory": inv,
             "location": {"name": loc}, "time": {"timeOfDay": 900, "season": "summer",
                                                 "weather": 0}}
    # 🚪 warp 状态机：`pend` = 还差几次 `/state` 才"生效"；`cur` = **此刻在哪张图**（`/animals` 用它）
    S = {"pend": 0, "cur": loc}

    def _loc_now():
        """此刻 `/state` 会报的地图（**会推进 warp 状态机**，模拟"回包早于生效"）。"""
        if S["pend"] > 0:
            S["pend"] -= 1
            S["cur"] = "Deluxe Barn"        # warp 还没生效：先报"还在棚里"
            return S["cur"]
        S["cur"] = loc
        return S["cur"]

    def g(ep, params=None):
        CALLS.append(("GET", ep))
        if ep == "/farm_buildings":
            return {"ok": True, "count": len(BUILDINGS), "buildings": BUILDINGS}
        if ep == "/state":
            return dict(state, location={"name": _loc_now()},
                        player=dict(state["player"]))
        if ep == "/animals":
            # ⚠️ `/animals` 只报**玩家当前所在图**那批（棚内 = indoor / Farm = outdoor）——
            #    第一版拿"有没有 landed"当判据 ⇒ 站棚里也回室外那批，四条老用例当场假红。
            return {"animals": (outdoor if S["cur"] == "Farm" else indoor), "count": 1}
        if ep == "/machines":
            return {"machines": ([{"type": "Auto-Grabber"}]) if grabber else []}
        if ep == "/menu":
            return ({"open": True, "type": "DialogueBox", "dialogue": popup} if popup
                    else {"open": False})
        return {}

    def p(ep, data=None):
        CALLS.append(("POST", ep, data))
        if ep == "/use":
            # 就地交互"有产物"那条：往包里塞一个 Milk（`_count_item` 靠它判成没成）
            if use_adds_milk:
                state["inventory"].append({"name": "Milk", "displayName": "牛奶", "stack": 1})
            return {"ok": True}
        if ep == "/passable":
            return {"passable": True}
        return {"ok": True}
    api._ai_get, api._ai_post = g, p
    api._get, api._post = g, p
    # ⚠️ 都带 `**kw`：`_with_state` 拼状态条时会用**关键字**调它们（不带就漏一句
    #    "状态读取失败: unexpected keyword argument"，虽然不影响断言但很吵）。
    api.animals = lambda **kw: g("/animals")
    api.machines = lambda **kw: g("/machines")
    api.menu = lambda **kw: g("/menu")
    api.state = lambda **kw: g("/state")
    api.use_item = lambda: p("/use")
    api.face = lambda d: p("/face", {"direction": d})
    api.position = lambda x, y: p("/position", {"x": x, "y": y})

    def _warp(*a, **k):
        # ⚠️ 回包恒 `ok`（真机也这样）——**它早于 warp 生效**，所以后面必须靠 `/state` 确认
        S["pend"] = int(warp_lag)
        return {"ok": True, "actual": {"location": "Deluxe Barn"}}
    api.warp = _warp
    # 走位：默认"走到了" **并且把玩家真的挪到动物下方那格**（`_walk_and_wait` 在真机上就是干这个的）。
    # ⚠️ 第一版只回 `(True, "")`、玩家坐标恒不动 ⇒ 站位永远不是卡迪纳尔相邻 ⇒ 触发"站位不对，
    #    position 兜底"那条路，两条用例假红 —— **桩不搬人 = 把成功路径测成了失败路径**。
    def _walk(loc_, x, y, timeout=25):
        WALK_CALLS.append((loc_, x, y))
        if not walk_ok:
            return False, "走位超时没到"
        state["player"]["x"], state["player"]["y"] = x, y - 1     # 站到它正下方，面朝上
        return True, ""
    M._walk_and_wait = _walk
    # ⚠️ 就地交互那条**绝不许瞬移** ⇒ 任何 `/position` 调用都记下来（用例会断言"零命中"）
    api.position = lambda x, y: p("/position", {"x": x, "y": y})
    M._warp_home_if_needed = lambda loc_: "（桩：不用回家）"
    M._enter_building = lambda b: (enter_ok, enter_log)
    # ⚠️ **建筑列表也要在这儿桩**（默认 BUILDINGS）：第一版只在个别用例里设，
    #    结果上一个用例留下的 `lambda: []` 被下一个用例读到 ⇒ 三组用例假红（自验当场逮到）。
    M._find_animal_buildings = lambda: (BUILDINGS if find_buildings is None
                                        else find_buildings)
    return state


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))
    return bool(cond)


def main():
    res = []
    _orig_ms = M._milk_shear_animals
    _orig_wait = M._wait_on_map
    # ⚠️ ⑨ 要跑**真的** `_enter_building`（前面每个 `_stub()` 都会把它换成桩）⇒ 先留一份原件
    _orig_enter = M._enter_building
    _orig_pxy = M._player_xy

    # ① 室外那半：棚里没有建筑时**也要**处理室外那批（原来直接 return「没找到动物建筑」）
    _stub(loc="Farm", find_buildings=[])
    _called = []
    M._milk_shear_animals = lambda skip_grabber=False: (
        _called.append(skip_grabber), "🐐 挤奶 1/1 只（羊羊）")[1]
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
    M.milk_shear()
    M._milk_shear_animals = _orig_ms
    res.append(ck("🐄 有建筑 ⇒ 棚内一次 + 室外一次（顺序：先棚内后室外）",
                  _called2 == [False, True], _called2))

    # ③ 人不在 Farm ⇒ **如实说没做**，不许静默跳过（也不许说"没有动物"）
    _stub(loc="Deluxe Barn", find_buildings=[])
    _called3 = []
    M._milk_shear_animals = lambda skip_grabber=False: (
        _called3.append(skip_grabber), "🐮 挤奶 0/0 只")[1]
    _out3 = M.milk_shear()
    M._milk_shear_animals = _orig_ms
    res.append(ck("🚫 人不在 Farm ⇒ **明说「室外那批没做」**（不静默、不装成没有）",
                  "没做" in _out3 and "不在 Farm" in _out3, _out3[:200]))
    res.append(ck("🚫 而且**不许**说成「没有动物」", "没有动物" not in _out3, _out3[:200]))
    res.append(ck("🚫 给了下一步（能直接照抄的 `map go` + `farm milk`）",
                  "map(ops=" in _out3 and 'destination' in _out3, _out3[:240]))
    res.append(ck("🚫 人不在 Farm ⇒ **不**去读 `/animals`（室外那批不跑）",
                  _called3 == [], _called3))

    # ④ 🚫 **不兜底了**（恒 2026-10-01：「走到附近做个样子就 ok」）——
    #    够不着 ⇒ 重走（最多 2 次）⇒ 还够不着就**就地交互**；**全程零 `/position`**。
    def _pos_calls():
        return [c for c in CALLS if c[0] == "POST" and c[1] == "/position"]

    def _ratio(txt):
        m = re.search(r"(\d+)/(\d+) 只", txt)
        return (int(m.group(1)), int(m.group(2))) if m else (None, None)

    _stub(loc="Farm", walk_ok=False)                      # 怎么走都站不到正旁边
    _td = M._milk_shear_animals(skip_grabber=True)
    res.append(ck("🚫 走不到 ⇒ **一次 `/position` 都没有**（瞬移那条路整个撤掉）",
                  not _pos_calls(), _pos_calls()))
    res.append(ck("🚫 走不到 ⇒ 报告里**如实点名**「没站到正旁边…就地交互了」",
                  "没站到正旁边" in _td and "就地交互了" in _td, _td[:220]))
    res.append(ck("🚫 走不到 ⇒ 先**重走**过（走位请求 ≥2 次，不是走一次就放弃）",
                  len(WALK_CALLS) >= 2, WALK_CALLS))
    res.append(ck("🔢 就地交互**没弹窗也没产物** ⇒ **不算进分子**（`0/1 只`）",
                  _ratio(_td) == (0, 1), (_ratio(_td), _td[:200])))
    res.append(ck("🔢 而且这件事也写进回执（「就地交互没成」）",
                  "就地交互没成" in _td, _td[:260]))
    # 就地交互**有产物** ⇒ 该算成（判据看背包里的 Milk 多没多）
    _stub(loc="Farm", walk_ok=False, use_adds_milk=True)
    _td_prod = M._milk_shear_animals(skip_grabber=True)
    res.append(ck("🔢 就地交互**出产物**（背包多了 Milk） ⇒ 算成（`1/1 只`）",
                  _ratio(_td_prod) == (1, 1), _ratio(_td_prod)))
    res.append(ck("🔢 有产物那条同样**零 `/position`**", not _pos_calls(), _pos_calls()))
    # 就地交互**有弹窗** ⇒ 也算成（老判据：弹窗 = 有反应）
    _stub(loc="Farm", walk_ok=False, popup="现在没有奶了。")
    _td_pop = M._milk_shear_animals(skip_grabber=True)
    res.append(ck("🔢 就地交互**有弹窗**（「现在没有奶了。」） ⇒ 也算成（`1/1 只`）",
                  _ratio(_td_pop) == (1, 1), _ratio(_td_pop)))
    # 走得到 ⇒ 零瞬移、零"就地"那句
    _stub(loc="Farm", walk_ok=True)
    _td2 = M._milk_shear_animals(skip_grabber=True)
    res.append(ck("🚶 走得到 ⇒ **一次都不瞬移**（原实现对每只都直接 position）",
                  not _pos_calls(), _pos_calls()))
    res.append(ck("🚶 走得到 ⇒ 报告里**没有**「就地交互」那句", "就地交互" not in _td2, _td2[:160]))
    # ⚠️ 2026-10-01 真机逮到的假数：`挤奶 15/8 只` —— 兜底那些行原来也算进分子（分母比分子还小）。
    _n_bad, _m_bad = _ratio(_td)
    res.append(ck("🔢 有兜底时**分子也不许超过分母**（`15/8 只` 那种假数）",
                  _n_bad is not None and _n_bad <= _m_bad, (_n_bad, _m_bad)))
    res.append(ck("🔢 兜底那几行挂在「走位兜底：」后面（**不占分子**）",
                  "走位兜底：" in _td, _td[:220]))

    # ⑤ 对象不变：牛/山羊 → 挤奶桶；绵羊 → 剪刀；猪**不碰**
    _stub(loc="Farm")
    _td3 = M._milk_shear_animals(skip_grabber=True)     # 室外那批 = 山羊
    _sel = [c[2].get("name") for c in CALLS if c[0] == "POST" and c[1] == "/select"]
    res.append(ck("🐐 山羊 ⇒ 挤奶桶（`/select 挤奶桶`）", "挤奶桶" in _sel, _sel))
    # 室内那批（牛 + 羊 + 猪）：应出现**挤奶桶和剪刀**、且**不碰猪**
    _stub(loc="Deluxe Barn", indoor=INDOOR, outdoor=[])
    _td4 = M._milk_shear_animals()                       # 人在棚里 ⇒ 读的是室内那批
    _sel4 = [c[2].get("name") for c in CALLS if c[0] == "POST" and c[1] == "/select"]
    res.append(ck("🐮🐑 棚内 ⇒ 挤奶桶（牛）+ 剪刀（羊）都出现过",
                  "挤奶桶" in _sel4 and "剪刀" in _sel4, _sel4))
    res.append(ck("🐷 猪**不碰**（回执里不出现猪）", "猪猪" not in _td4, _td4[:200]))
    res.append(ck("🐮🐑 数目照旧报 `n/m 只`", "/" in _td4 and "只" in _td4, _td4[:160]))
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

    # ⑥ 🔴 洞 A（2026-10-01 真机）：**warp 回包早于生效** ⇒ 出棚后必须等确认再读 `/animals`
    #    桩：调过 `api.warp` 之后**头一拍 `/state` 仍报"还在 Deluxe Barn"**，第二拍才报 Farm。
    _stub(loc="Farm", warp_lag=1)
    _called6 = []
    M._milk_shear_animals = lambda skip_grabber=False: (
        _called6.append(skip_grabber), "🐐 挤奶 1/1 只（羊羊）")[1]
    _out6 = M.milk_shear()
    M._milk_shear_animals = _orig_ms
    res.append(ck("🔴 出棚后**等确认站上 Farm**（warp 头一拍还报棚里 ⇒ 不能就此下结论）",
                  "已确认站上 Farm" in _out6, _out6[:260]))
    res.append(ck("🔴 等到了 ⇒ **室外那批照跑**（`skip_grabber=True` 那一发在）",
                  _called6 == [False, True], _called6))
    res.append(ck("🔴 而且要真的**轮询**过（不是靠固定 sleep 蒙的）",
                  any(c[1] == "/state" for c in CALLS)))

    # ⑦ 🔴 洞 A 的"没等到"那一支：**如实说"没确认回到农场"**，不许编"人还在<某建筑>"
    _stub(loc="Farm", warp_lag=99)          # 永远不生效
    M._wait_on_map = lambda *a, **k: False  # 桩掉等待，免得白等 6 秒（等待本身由 ⑥ 验）
    _called7 = []
    M._milk_shear_animals = lambda skip_grabber=False: (
        _called7.append(skip_grabber), "🐐 挤奶 1/1 只")[1]
    _out7 = M.milk_shear()
    M._milk_shear_animals = _orig_ms
    M._wait_on_map = _orig_wait
    res.append(ck("🔴 没等到 ⇒ 如实说「**没确认回到农场**」", "没确认回到农场" in _out7, _out7[:280]))
    res.append(ck("🔴 而且**不许**编原因（不能写成人还在某栋建筑里）",
                  "人还在「Deluxe Barn」不在 Farm" not in _out7, _out7[:280]))
    res.append(ck("🔴 没确认 ⇒ 给下一步（`map go Farm` + `farm milk`）",
                  "map(ops=" in _out7 and 'destination' in _out7, _out7[:300]))
    res.append(ck("🔴 没确认 ⇒ **不乱猜地去读 `/animals`**（室外那一发 `skip_grabber=True` 不出现）",
                  True not in _called7, _called7))

    # ⑧ 🔴 洞 B：进不去时**把 `_enter_building` 的日志原样带出来**（不许吞原因）
    _stub(loc="Farm", enter_ok=False,
          enter_log="⚠️ 没走到门格：目标(52,16) 实际(50,20)")
    _out8 = M.milk_shear()
    res.append(ck("🔴 进不去 ⇒ 回执里**带出真原因**（原话）",
                  "没走到门格" in _out8 and "(52,16)" in _out8, _out8[:260]))
    res.append(ck("🔴 而且「进不去」这句话本身还在（不许把失败说成成功）",
                  "进不去" in _out8, _out8[:200]))

    # ⑨ 🔴 `_enter_building` 自己（2026-10-01 恒：真机偶发"Coop 进不去"的病根）——
    #    **走到门下方那格 → face 朝门 → `/interact 门格`**，**不再 `position` 顶上门格**；
    #    失败**隔帧重试 1 次**并把**真实原因**带回来。这里跑的是**真函数**，只桩传输层。
    _BUILD9 = {"type": "Deluxe Coop", "x": 44, "y": 34, "doorX": 48, "doorY": 38,
               "indoorsName": "Deluxe Coop"}
    M._enter_building = _orig_enter      # 跑真函数（前面那些 `_stub()` 把它换成了桩）

    def _setup_enter(land=True, dlg=None):
        """桩：`/state` 起初在 Farm；`land=True` 时**第一次 interact 之后**就报已进棚。"""
        _S = {"loc": "Farm", "interacts": [], "faces": [], "keys": []}

        def _g(ep, params=None):
            if ep == "/state":
                return {"location": {"name": _S["loc"]},
                        "player": {"x": 48, "y": 39, "currentTool": ""}}
            if ep == "/menu":
                return {"open": bool(dlg), "type": "DialogueBox", "dialogue": dlg} if dlg \
                    else {"open": False}
            return {}
        api._ai_get, api._ai_post = _g, _g
        api._get, api._post = _g, _g
        api.state = lambda **kw: _g("/state")
        api.menu = lambda **kw: _g("/menu")
        api.warp = lambda *a, **k: {"ok": True}
        api.face = lambda d: (_S["faces"].append(d), {"ok": True})[1]

        def _interact(x, y):
            _S["interacts"].append((x, y))
            if land:
                _S["loc"] = "Deluxe Coop"       # 门开了、人进去了（真机会晚一两拍，这里立刻）
            return {"ok": True, "actionTriggered": True}
        api.interact_at = _interact
        api.key = lambda k, *a, **kw: (_S["keys"].append(k), {"ok": True})[1]
        api.position = lambda x, y: (_S.__setitem__("pos", (x, y)), {"ok": True})[1]
        M._walk_and_wait = lambda loc_, x, y, timeout=25: (
            WALK_CALLS.append((loc_, x, y)), (True, ""))[1]
        M._player_xy = lambda: (48, 39)
        return _S

    _S9 = _setup_enter(land=True)
    _ok9, _log9 = M._enter_building(_BUILD9)
    res.append(ck("🚪 进棚：**走位请求的是门下方那格** (48,39)，不是门格 (48,38)",
                  WALK_CALLS and WALK_CALLS[-1][1:] == (48, 39), WALK_CALLS))
    res.append(ck("🚪 进棚：进门靠 **`/interact 门格`**", _S9["interacts"] == [(48, 38)],
                  _S9["interacts"]))
    res.append(ck("🚪 进棚：**没有 `position` 顶格**那一步", "pos" not in _S9, _S9.get("pos")))
    res.append(ck("🚪 进棚：面向门（face 0 = 朝北）", 0 in _S9["faces"], _S9["faces"]))
    res.append(ck("🚪 进棚成功 ⇒ 返回 True + 日志说清了进了哪", _ok9 is True and "进入" in _log9,
                  _log9))
    # 失败那条：永远进不去 ⇒ **重试 1 次**（两次 interact）+ **带出真实原因**
    _S9b = _setup_enter(land=False)
    WALK_CALLS.clear()
    _ok9b, _log9b = M._enter_building(_BUILD9)
    res.append(ck("🚪 进不去 ⇒ **隔帧重试 1 次**（`/interact` 打了两次）",
                  len(_S9b["interacts"]) == 2, _S9b["interacts"]))
    res.append(ck("🚪 进不去 ⇒ 返回 False 且**如实说**（带站位 + 门格坐标）",
                  _ok9b is False and "(48,38)" in _log9b and "进门失败" in _log9b, _log9b[:260]))
    res.append(ck("🚪 进不去 ⇒ **带下一步**（能直接照抄的 `scene at` 手动点门）",
                  "scene(ops=" in _log9b and "tile_x" in _log9b, _log9b[:300]))
    res.append(ck("🚪 进不去那条路同样**零 `position`**", "pos" not in _S9b, _S9b.get("pos")))

    print(f"\n{sum(res)}/{len(res)} 过")
    return all(res)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
