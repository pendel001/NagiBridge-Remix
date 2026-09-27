"""🛒 「恒买卖东西，AI 也该看得见」 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-25

恒：「可不可以让我卖了东西也让 ai 知道呢？就是 ai 的两次调用之间现在是检测钱的变化，
顺手抓别的玩家卖了什么」→ 补：「对，我想花钱也需要。同一个逻辑，看背包多了什么+钱少了」。

做法 = 每次读到房主的 `/state` 时**顺手记一笔他的钱包 + 背包**，下次再读时比对：
**进账**就看他背包少了什么、**花钱**就看他背包多了什么。

这里钉的是**判据的边界**（比功能本身重要）：
  ① 头一次读到 → 只打基准，不报（否则服务一起来就喊"进账/支出"）
  ② 判据挂在**钱包变了**上、不是"背包变了"：
     他为了腾地方把工具存进箱子也会让背包变少，但那不**动钱** ⇒ 不会误报成卖东西
  ③ **看不到就不出声**（恒拍板）——钱包动过、但背包对不上（从箱子拿来卖的 / 出货箱结算 / 任务奖励 /
     背包升级这种没有实物的），一律闭嘴：一条说不出所以然的"钱动了"对 AI 没用、还占状态条
  ④ 两个方向**对称**：进账→背包少了什么 / 花钱→背包多了什么
  ⑤ **柜台 + 钱动 + 背包**（恒 2026-09-27：「上次我们约定好柜台+钱动+背包 算买卖播报」）
     ⇒ **三个缺一不出声**；而且**柜台是硬门槛**（不在柜台 ⇒ 闭嘴，不是"少写一截文案"）。
     ⚠️ 09-25 那版只把柜台当装饰，于是"背包同时有增有减"被当成成交报了出去 ——
        当天真机恒在钓鱼，钓上鱼（多了）＋吃掉沙拉（少了）连报两趟假成交、钱包一分没动。
  ⑥ **柜台名单要够全**：商店柜台 · 节日商店柜台 · 猪车 · 帽子老鼠 · 沙漠商人 · 科罗布斯 …
     但**门口/交付箱/布告栏不算**（站那儿不是在交易）
  ⑦ 少了的东西里**工具/武器要滤掉**（light 模式没有 `sellable`，只能按名字认）
  ⑧ **换天不做**：出货箱是**过夜结算**的，那一跳不是"他刚卖了东西"（恒：「过夜结算不用播报」）
  ⑨ **AI 自己买卖期间暂停**（恒：「会串吗？可能会的。AI 做相关买卖操作时跳过对我的检测吧」）
  ⑩ 取走是一次性的（别每拼一次状态条就重播一遍）
  ⑪ **方向要自洽**：钱涨得配上"少了"的（卖）、钱跌得配上"多了"的（买）；对不上就闭嘴
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def hs(money, inv, day=4, season="spring", loc="SeedShop", x=4, y=18):
    """造一份 host /state 的样子（只带这条观测会读的字段）。

    ⚠️ 默认**站在皮埃尔柜台前**（`loc/x/y`）—— 2026-09-27 起柜台是**硬门槛**，
       不在柜台一个字都不出；下面的正例测的都是"钱/背包"这一维，柜台那一维
       由 ⑧c/⑧d 与新增的 ⑪ 专测（要测"不在柜台"就显式传别的坐标）。
    """
    return {
        "player": {"money": money, "name": "恒", "x": x, "y": y},
        "time": {"season": season, "dayOfMonth": day, "year": 1},
        "location": {"name": loc},
        "inventory": [{"name": n, "stack": s, "slotIndex": i} for i, (n, s) in enumerate(inv)],
    }


def reset():
    M._PEER_ECON.update(day=None, money=None, inv={}, line="", mute_until=0.0, counter=None)


def seq(*states):
    """依次喂几份快照，返回最后一次攒出来的那句话（没有就是空串）。"""
    for st in states:
        M._peer_econ_observe(st)
    return M._peer_econ_take()


print("\n① 头一次读到 → 只打基准，不报")
reset()
ck("第一眼不说话", seq(hs(1000, [("Smallmouth Bass", 2)])) == "")

print("\n② 判据挂在「钱包动了」上：只搬东西不动钱 → 一个字都不说")
reset()
ck("他只是把工具存箱子（钱没涨）", seq(hs(1000, [("Axe", 1), ("Hoe", 1), ("Smallmouth Bass", 3)]),
                              hs(1000, [("Smallmouth Bass", 3)])) == "")
reset()
ck("他只是把东西从箱子拿出来（钱没动）", seq(hs(1000, []),
                                hs(1000, [("Sunfish", 2), ("Coal", 5)])) == "")

print("\n③ 看不到就不出声（恒拍板）")
reset()
ck("钱涨了但背包没对上（从箱子拿来卖的）", seq(hs(1000, [("Smallmouth Bass", 1)]),
                                  hs(1400, [("Smallmouth Bass", 1)])) == "")
reset()
ck("钱少了但背包没多（背包升级这种没实物的）", seq(hs(3000, []), hs(2500, [])) == "")
reset()
ck("钱涨了、少的全是工具（滤完名单是空的）", seq(hs(1000, [("Axe", 1), ("Hoe", 1)]),
                                     hs(1400, [])) == "")

print("\n④ 进账 → 看他背包少了什么")
reset()
line = seq(hs(1000, [("Smallmouth Bass", 3), ("River Jelly", 1), ("Coal", 2)]),
           hs(2260, [("Coal", 2)]))
ck("报了进账数额", "+1,260g" in line, line)
ck("…点了少了的东西", "Smallmouth Bass×3" in line and "River Jelly" in line, line)
ck("…留在背包里的不动它", "Coal" not in line, line)

print("\n⑤ 花钱 → 看他背包多了什么（恒：「对，我想花钱也需要」）")
reset()
line = seq(hs(3000, [("Coal", 2)]), hs(2400, [("Coal", 2), ("Copper Ore", 5)]))
ck("报了支出数额", "-600g" in line, line)
ck("…点了多了的东西", "Copper Ore×5" in line, line)
ck("…旧东西不动它", "Coal" not in line, line)

print("\n⑥ 工具/武器要从「少了」的名单里滤掉")
reset()
line = seq(hs(1000, [("Axe", 1), ("Iridium Rod", 1), ("Rusty Sword", 1), ("Smallmouth Bass", 1)]),
           hs(1150, []))
ck("留的是鱼", "Smallmouth Bass" in line, line)
ck("斧头没进名单", "Axe" not in line, line)
ck("鱼竿没进名单", "Rod" not in line, line)
ck("剑没进名单", "Sword" not in line, line)

print("\n⑦ 换天不做（出货箱过夜结算那一跳；恒：「过夜结算不用播报」）")
reset()
ck("跨天那一跳不报", seq(hs(1000, [("Smallmouth Bass", 9)], day=4),
                   hs(9800, [], day=5)) == "")
ck("…但新的一天里真进账照报",
   "+320g" in seq(hs(9800, [("Sunfish", 2)]), hs(10120, [("Sunfish", 1)])))

print("\n⑧ ⏸️ AI 自己买卖期间暂停观察（恒：「会串吗？可能会的」）")
reset()
M._peer_econ_observe(hs(1000, [("Smallmouth Bass", 3), ("Bomb", 5)]))
M._peer_econ_mute(60)
M._peer_econ_observe(hs(2200, [("Bomb", 5)]))     # 我卖鱼钱涨 + 他那边少了鱼 —— 真机上就是这一对会串
ck("静默期间不出声（哪怕钱和背包都对得上）", M._peer_econ_take() == "")
ck("…但**基准已更新**（是静默不是冻结）", M._PEER_ECON["money"] == 2200)
M._peer_econ_observe(hs(2500, [("Bomb", 5), ("Iron Ore", 3)]))
ck("…静默里继续变 → 依然不出声（积压不会攒着炸一次）", M._peer_econ_take() == "")
M._PEER_ECON["mute_until"] = 0                     # 解禁
ck("…解禁后恢复观察（钱没动仍然不说）", seq(hs(2500, [("Bomb", 5)])) == "")
ck("…解禁后真进账照报（钱涨 + 背包少了）", "+500g" in seq(hs(3000, [("Bomb", 3)])))
import inspect
_srcs = (inspect.getsource(M.sell_to_shop) + inspect.getsource(M.shop_visit)
         + inspect.getsource(M.menu_click))
ck("…卖家那几个 op 真挂了 mute（卖 / 逛店 / 商店里点物品买）",
   _srcs.count("_peer_econ_mute(") >= 3, str(_srcs.count("_peer_econ_mute(")))

print("\n⑧b 🧑 他正站在哪个柜台前（恒：「站在那里…这个应该是包准的了」）")
_keep = dict(M._PEER_POS)
try:
    print("   ── 恒点名的那些地方，一个都不能漏 ──")
    for label, loc, x, y in [("商店柜台", "SeedShop", 4, 19),
                             ("猪车(旅行货车)", "Forest", 27, 12),
                             ("帽子老鼠", "Forest", 34, 96),
                             ("沙漠商人", "Desert", 42, 24),
                             ("科罗布斯", "Sewer", 31, 18),
                             ("姜岛商人", "IslandNorth", 35, 75),
                             ("夜市猪车", "BeachNightMarket", 39, 31),
                             ("夜市装饰商船", "BeachNightMarket", 19, 34),
                             ("书摊", "Town", 110, 27),
                             ("冰淇淋摊位", "Town", 88, 93),
                             ("矮人商店", "Mine", 43, 7),
                             ("火山矮人商店", "VolcanoDungeon5", 36, 30)]:
        ck(f"{label} → 认出来", bool(M._peer_at_counter(loc, x, y)),
           repr(M._peer_at_counter(loc, x, y)))

    print("   ── 门口/交付箱/公告栏**不算**柜台（站那儿不是在交易）──")
    for label, loc, x, y in [("皮埃尔店门口", "Town", 43, 57),
                             ("木匠店门外", "Mountain", 12, 26),
                             ("皮埃尔优选交付箱", "SeedShop", 19, 29),
                             ("木匠店木头堆", "ScienceHouse", 10, 20),
                             ("每日求助栏", "Town", 42, 57),
                             ("农场出货箱", "Farm", 71, 14)]:
        ck(f"{label} → 不算", M._peer_at_counter(loc, x, y) == "",
           repr(M._peer_at_counter(loc, x, y)))

    print("   ── 半径只给 2（小店整间才五六格宽）──")
    ck("离柜台 2 格还算", bool(M._peer_at_counter("SeedShop", 4, 21)))
    ck("离柜台 5 格不算", M._peer_at_counter("SeedShop", 4, 24) == "")

    print("   ── 🎪 节日商店柜台（点位每天变，走 calendar_data.FESTIVAL_SHOPS）──")
    ck("蛋蛋节当天(spring13) Temp(20,54) → 认出来",
       bool(M._peer_at_counter("Temp", 20, 54, season="spring", day=13)))
    ck("…换个日子就不算（坐标一样）",
       M._peer_at_counter("Temp", 20, 54, season="spring", day=14) == "")
    ck("…冬天冰雪节(8) 的猪车柜台也认",
       bool(M._peer_at_counter("Temp", 62, 27, season="winter", day=8)))

    print("   ── 状态条那行：换了柜台才报、离开不报（`_peer_counter_line` 自身仍是对的）──")
    M._STATE_DELTA.pop("peer_counter", None)
    M._PEER_POS.update(ts=0, loc="SeedShop", x=4, y=18, name="恒", season="spring", day=13)
    _l = M._peer_counter_line()
    ck("站在柜台前 → 报", "皮埃尔商店(柜台)" in _l and "恒" in _l, _l)
    ck("…不重播（他还站在那儿）", M._peer_counter_line() == "")
    M._PEER_POS.update(loc="Town", x=50, y=60)
    M._STATE_DELTA.pop("peer_counter", None)
    ck("…离开柜台不报", M._peer_counter_line() == "")
    M._PEER_POS.update(loc="", x=None, y=None)
    M._STATE_DELTA.pop("peer_counter", None)
    ck("…读不到位置 → 不报、不炸", M._peer_counter_line() == "")

    # ⛔ **退役断言**（恒 2026-09-25：「三种表述…留"成交了"就可以了」）：
    #    上面那几条测的是**函数自身**还对不对 —— 但**状态条已经不再调它**了。
    #    这里把函数换成一个哨兵，真拼一次状态条：哨兵**不许出现**。
    #    ⚠️ 不钉这条的话，"函数还绿着"会让人以为那条行还在链路上（红绿两码事）。
    print("   ── ⛔ 退役：状态条**不许**再出现这条 🧑 行 ──")
    _real_dot = M.api
    _real_pc = M._peer_counter_line

    class _NoNet:
        def _get(self, *a, **k):
            return {}

        def _post(self, *a, **k):
            return {}

        def state(self, **k):
            return {}

    try:
        M.api = _NoNet()
        M._peer_counter_line = lambda: "🧑 哨兵：这行不该再出现在状态条里"
        M._PEER_POS.update(ts=0, loc="", x=None, y=None, name="")
        _d = {"raw": {}, "activeMenu": None, "activeEvent": None, "alerts": [],
              "player": {"name": "Claude", "money": 100, "maxItems": 24, "health": 100,
                         "maxHealth": 100, "stamina": 200, "maxStamina": 270,
                         "x": 5, "y": 5, "currentTool": "Axe"},
              "location": {"name": "Town"}, "inventory": [], "otherPlayers": [],
              "time": {"season": "spring", "dayOfMonth": 5, "year": 1, "timeOfDay": 900}}
        _s = M._build_state_strip(_d, full=False)
        ck("状态条**不再**调 `_peer_counter_line`", "哨兵" not in _s, _s[:160])
    finally:
        M._peer_counter_line = _real_pc
        M.api = _real_dot
    ck("柜台索引是真从 locations.POI 建的（不是空表）", len(M._counter_pois()) >= 15,
       str(len(M._counter_pois())))
finally:
    M._PEER_POS.clear()
    M._PEER_POS.update(_keep)

print("\n⑧c 🛒 三个一起（站在柜台 + 钱变 + 背包变）⇒ 成交")
reset()
line = seq(hs(1000, [("Smallmouth Bass", 3)], loc="SeedShop", x=4, y=18),
           hs(2260, [], loc="SeedShop", x=4, y=18))
ck("在柜台成交 → 直接说在哪成交的", "成交了" in line and "皮埃尔商店(柜台)" in line, line)
ck("…不再带「多半」", "多半" not in line, line)
ck("…钱和名单照样给全", "+1,260g" in line and "Smallmouth Bass×3" in line, line)
reset()
line = seq(hs(3000, [], loc="SeedShop", x=4, y=18),
           hs(2400, [("Copper Ore", 5)], loc="SeedShop", x=4, y=18))
ck("在柜台买 → 也直接断言", "成交了" in line and "支出 -600g" in line, line)
reset()
line = seq(hs(1000, [("Smallmouth Bass", 3)], loc="Town", x=60, y=60),
           hs(2260, [], loc="Town", x=60, y=60))
# ⚠️ 2026-09-27 恒拍板改判据（「柜台+钱动+背包」缺一不出声）⇒ 这条**从"照报"翻成"静默"**：
#    09-25 那版只在文案里体现柜台（不在柜台照样说「成交了」、只是不提柜台），
#    结果"背包同时有增有减"被当成成交报了出去（当天真机恒在钓鱼连报两趟假成交）。
ck("**不在**柜台（镇上随便哪）→ 一个字都不说",
   line == "" and "成交了" not in line, line)
reset()
ck("在柜台附近但钱没动 → 照样不说（不是光看位置）",
   seq(hs(1000, [("Sunfish", 2)], loc="SeedShop", x=4, y=18),
       hs(1000, [("Sunfish", 1)], loc="SeedShop", x=4, y=18)) == "")

print("\n⑧d ⏱️ 「五分钟之内路过那个点也算」（恒：买卖的窗口比较短）")
reset()
# 先看到他站在柜台，再看时人已经走开、钱和背包都变了 —— 真机上他多半就是在两次采样之间成交完走了
line = seq(hs(1000, [("Smallmouth Bass", 3)], loc="SeedShop", x=4, y=18),
           hs(2260, [], loc="Town", x=60, y=60))
ck("成交时人已离开柜台，但 5 分钟内刚在柜台露过面 → 仍算柜台成交",
   "成交了" in line and "皮埃尔商店(柜台)" in line, line)
reset()
M._peer_econ_observe(hs(1000, [("Smallmouth Bass", 3)], loc="Town", x=60, y=60))  # 从没在柜台出现过
line = seq(hs(2260, [], loc="Town", x=60, y=60))
ck("…但从没在柜台露过面 → 闭嘴（钱动了也不报）", line == "", line)
reset()
M._peer_econ_observe(hs(1000, [("Smallmouth Bass", 3)], loc="SeedShop", x=4, y=18))
M._PEER_ECON["counter"] = (M._PEER_ECON["counter"][0], time.time() - 9999)   # 装作很久很久以前路过的
M._peer_econ_observe(hs(1000, [("Smallmouth Bass", 3)], loc="Town", x=60, y=60))
line = seq(hs(2260, [], loc="Town", x=60, y=60))
ck("…超过 5 分钟就过期了 → 同样闭嘴", line == "", line)

print("\n⑧e 🛒 边买边卖要**一起报**（恒：「柜台+钱+背包少了什么、多了什么，可以一起报『买了xxx，卖了xxx』」）")
# 当天真机撞到的就是这一形态：他买了三样、又卖了一样（+60g），净额 -580g，
# 原来按 delta 符号二选一 ⇒ 只报"买"，卖的那件连名字都没出现。
reset()
line = seq(hs(1484, [("Green Algae", 1), ("Smallmouth Bass", 1)], loc="SeedShop", x=4, y=18),
           hs(904, [("Green Algae", 1), ("Grass Starter", 1), ("Parsnip Seeds", 1)]))
ck("在柜台：买和卖**都点名**", "买了" in line and "卖了" in line, line)
ck("…买了哪三样/卖了哪样都在",
   "Grass Starter" in line and "Parsnip Seeds" in line and "Smallmouth Bass" in line, line)
ck("…净额照给", "-580g" in line, line)
ck("…柜台断言仍在", "成交了" in line and "皮埃尔商店(柜台)" in line, line)

print("   ── 净额 0 ⇒ **不再算**（恒 2026-09-27 自己撤回：「没必要 0 也算」）──")
# 09-25 曾为"反复买卖看不出来"给净额 0 开了个 OR 分支；恒 09-27 复核后收回：
# 「我那时说的是，因为包包没有东西所以只能买卖同样东西，这反而不符合游戏大部分情况。」
reset()
line = seq(hs(1000, [("Smallmouth Bass", 1)], loc="SeedShop", x=4, y=18),
           hs(1000, [("Parsnip Seeds", 1)]))
ck("在柜台、有买有卖但**钱一分没动** → 静默", line == "", line)
reset()
ck("钱没变、**只有一边**变（多半是整理背包/丢东西）→ 静默",
   seq(hs(1000, [("Coal", 2)], loc="SeedShop", x=4, y=18),
       hs(1000, [("Coal", 1)], loc="SeedShop", x=4, y=18)) == "")
ck("…「钱包没变」这套措辞一并撤（门控已保证钱非动不可）",
   "钱包没变" not in (seq(hs(1000, [("Coal", 2)], loc="SeedShop", x=4, y=18),
                          hs(1000, [("Coal", 1)], loc="SeedShop", x=4, y=18)) or ""))

print("\n⑪ 🚧 柜台是**硬门槛**（恒 2026-09-27 真机误报的根因）")
# 当天实况：恒就在 AI 旁边钓鱼（Town (7,93) / AI Town (3,93)），
# 钓上鱼/垃圾（背包多了）＋ 吃掉沙拉/鲈鱼（背包少了）⇒ 被当成"边买边卖"报了两趟假成交，
# 钱包一分没动、人也压根不在柜台（BobberBar 小游戏开着）。判据原样重放：
reset()
line = seq(hs(1000, [("Salad", 1)], loc="Town", x=7, y=93),
           hs(1000, [("Green Algae", 1), ("Catfish", 2), ("Shad", 1)], loc="Town", x=7, y=93))
ck("🎣 钓鱼形态（吃 + 钓、钱没动、不在柜台）→ 静默", line == "", line)
reset()
line = seq(hs(2000, [("Rice Shoot", 2)], loc="Farm", x=70, y=20),
           hs(2000, [("Fiber", 3)], loc="Farm", x=70, y=20))
ck("📦 存箱子 + 采集（钱没动·不在柜台）→ 静默", line == "", line)
reset()
line = seq(hs(2000, [("Salad", 1)], loc="Farm", x=70, y=20),
           hs(2500, [("Green Algae", 1)], loc="Farm", x=70, y=20))
ck("…哪怕钱**真动了**、不在柜台 → 也静默（柜台不是装饰，是闸门）", line == "", line)
reset()
line = seq(hs(2000, [("Salad", 1)], loc="SeedShop", x=4, y=18),
           hs(2500, [("Green Algae", 1)]))
ck("…同一次变化，人**在柜台**⇒ 照报（闸门只认位置，不认别的）",
   "成交了" in line and "皮埃尔商店(柜台)" in line, line)

print("   ── 方向要自洽（钱涨得有「少了」的、钱跌得有「多了」的）──")
reset()
line = seq(hs(2000, [("Coal", 1)], loc="SeedShop", x=4, y=18),
           hs(2500, [("Coal", 1), ("Copper Ore", 5)]))
ck("钱**涨了**却只看到「多了」（多半是信件给的 500g）→ 静默", line == "", line)
reset()
line = seq(hs(2000, [("Coal", 1), ("Copper Ore", 5)], loc="SeedShop", x=4, y=18),
           hs(1500, [("Coal", 1)]))
ck("钱**少了**却只看到「少了」（对不上账）→ 静默", line == "", line)

print("\n⑨ 取走是一次性的")
reset()
ck("第一次取到", "+400g" in seq(hs(500, [("Coal", 1)]), hs(900, [])))
ck("第二次取是空的（不会每拼一次状态条就重播）", M._peer_econ_take() == "")

print("\n⑩ 读不到 / 脏数据不炸")
reset()
M._peer_econ_observe({})
M._peer_econ_observe({"player": {"money": None}, "time": {}})
M._peer_econ_observe(None)
ck("空/None/没有 money → 不抛异常、也不报", M._peer_econ_take() == "")

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
