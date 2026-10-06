"""🎣 「快捷钓鱼上单」的判据 + 执行链 —— 纯 Python 自验（不起服务、不碰游戏）。2026-10-05

测的是**这一批新接的 Python 侧**（C# 的 `/fish_areas` 端点本批**不动**、也不在这里测）：
  ① **竿闸**：`rod.inHand` 真 ⇒ 出现「垂钓」行；假 ⇒ **整行不出现**
     （⛔ 不许退回「背包里有竿」—— 那是 `FindFishingRod`（`ModEntry.cs:6787`）的兜底，不是「手上有竿」）
  ② **层数**（恒拍板）：2 个有钓点水域 ⇒ **两级** · 1 个 ⇒ **只有一层** ·
     0 个但图上有水 ⇒ **一层**（水格扫描） · 0 个且没水 ⇒ **整行不出现**
  ③ **哨兵「水域≠钓点」**：`spots:[]`（`spotsFound:0`）的水域**不许出现在第二层**（恒的硬规矩）
  ④ **名字原样透传**：印出来的就是 `areas[].id` **原文**（夹具里放奇怪 id），
     且单子上**一个中文水域名都没有**、**一个鱼种都没有**（防有人偷偷加对照表/鱼种预报）
  ⑤ **执行链按被点那一区走**：敲哪一行，传下去的就是**那一行**的坐标
     （不是「最近/默认」那个 —— 本项目有过「按 A 缸的判断动 B 缸」的事故）
  ⑥ **服务器执行侧**：体力闸拦在走位之前 · 竿不在手不动作 · 没走到钓点**不开钓** ·
     朝向按**实际站位**重算 · `fish_run` 走**就地钓**（不带 `--location`）

⚠️ **全离线**：只有假 api（`FakeApi`）+ 假 `navigation.walk_to` + 假 `_run_script`，
   **一发 HTTP 都不打**（跑的时候带 `NAGI_NET_GUARD_FORCE=1` 更保险）。
⚠️ **这里绿 ≠ 真机成**：`/fish_areas` 的字段、以及「水格扫描的锚点覆盖够不够」都还没在真机跑过
   —— 真机验收清单见 `CHANGELOG.md` 的 `203z补32`。
"""
import json
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # import 安全：末尾才 if __name__ == "__main__"

IM = M.intent_menu                   # 同一个模块实例（`_im_ctx` 用的就是它）

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 假 api：只回答这次要问的（`/fish_areas` / `/water` / `/passable` / `/face` / `/state`）──────
class FakeApi:
    def __init__(self, state=None, fish_areas=None, water=None, passable=None, host=None,
                 profile=None):
        self._state = state if state is not None else {}
        self.fish_areas = fish_areas          # None = 端点挂了/老 DLL
        self.water = water or {}              # (锚点x, 锚点y) -> [水格 dict]
        self.passable = passable or {}        # (x, y) -> True/False
        self.host = host or {}                # 7842 的 `/state` 回包（矿井那档用；`{}` = 读不到）
        self.profile = profile                # `GET /profile`（钓鱼等级兜底那档；None = 没有这一位）
        self.gets, self.posts = [], []

    def state(self, **kw):
        return self._state

    def host_get(self, ep, params=None, timeout=10):
        self.gets.append(("host" + str(ep), dict(params or {})))
        if ep == "/state":
            return self.host
        raise RuntimeError("unexpected host GET " + ep)

    def _ai_get(self, ep, params=None):
        self.gets.append((ep, dict(params or {})))
        if ep == "/state":
            return self._state
        if ep == "/fish_areas":
            if self.fish_areas is None:
                raise RuntimeError("no such endpoint")
            return self.fish_areas
        if ep == "/water":
            p = params or {}
            return {"ok": True, "count": 0,
                    "water": list(self.water.get((p.get("x"), p.get("y"))) or [])}
        if ep == "/profile":
            # 🎣 钓鱼等级的兜底来源（`ModEntry.cs:18266`）：`/state.player.fishing` 只在**竿是当前工具**
            #    时才有 ⇒ 竿在包里时走这一发。⚠️ 老 DLL / 读不到 ⇒ 抛（消费侧如实不给行）。
            if self.profile is None:
                raise RuntimeError("no such endpoint")
            return self.profile
        raise RuntimeError("unexpected GET " + ep)

    def _ai_post(self, ep, data=None):
        self.posts.append((ep, dict(data or {})))
        if ep == "/face":
            return {"ok": True}
        if ep == "/passable":
            d = data or {}
            return {"ok": True, "passable": bool(self.passable.get((d.get("x"), d.get("y"))))}
        if ep == "/interact":
            return {"ok": True}
        raise RuntimeError("unexpected POST " + ep)

    def _post(self, ep, data=None, **kw):     # `_crab_find_edges` 那族走的是 `_post`
        return self._ai_post(ep, data)


class FakeNav:
    """假 `navigation.walk_to`：记下往哪走，并按 `land` 把玩家挪过去（模拟到点/没到点）。"""

    def __init__(self, api, land=None):
        self.api, self.land, self.calls = api, land, []

    def walk_to(self, poi_name="", x=None, y=None):
        self.calls.append((poi_name, x, y))
        if self.land is not None:
            p = self.api._state.setdefault("player", {})
            p["x"], p["y"] = self.land
        return f"🚶 已到 ({x},{y})"


_real = {n: getattr(M, n) for n in ("api", "navigation", "_run_script", "_ai_port")}
try:
    M._ai_port = lambda: 7843

    print("\n① 竿闸：`rod.inHand` 决定「垂钓」整行在不在（⛔ 不是「背包里有竿」）")
    # 夹具：Forest 两个有钓点的水域（都是游戏 id，故意一个怪名字）+ 一个没钓点的
    FISH_FOREST = {
        "ok": True, "location": "Forest", "hasFishAreaData": True, "count": 3,
        "areas": [
            # ⚠️ 名字**故意怪**（带空格/大小写/下划线）—— 防有人「顺手美化」成中文
            {"id": "Lake", "displayName": None,
             "position": {"x": 30, "y": 20, "w": 10, "h": 10},
             "waterTiles": 59, "spotsFound": 1, "spotsTruncated": False,
             "spots": [{"waterX": 34, "waterY": 26, "standX": 34, "standY": 25,
                        "dir": 2, "dirName": "down"}]},
            {"id": "Lower_River 2", "displayName": None, "position": None,
             "waterTiles": 200, "spotsFound": 1, "spotsTruncated": False,
             "spots": [{"waterX": 20, "waterY": 77, "standX": 20, "standY": 76,
                        "dir": 2, "dirName": "down"}],
             # ⛔ 鱼种**不该进单子**：夹具里故意塞一份（真端点不回，这里防「以后有人接进来」）
             "fish": ["鲤鱼", "鲶鱼"]},
            {"id": "干水域", "displayName": None, "position": {"x": 0, "y": 0, "w": 5, "h": 5},
             "waterTiles": 0, "spotsFound": 0, "spotsTruncated": False, "spots": [],
             "noSpotReason": "本区范围内没扫到水格（isWaterTile 恒 false）"},
        ],
    }
    # 水格扫描那档（**有鱼区数据但一个区都没有** ⇒ 退回 `/water`）。
    # 🔴 2026-10-06 恒拍板「**撤掉农场的钓鱼选项单**」之后，这里**不能再用 Farm** 当夹具
    #    （农场现在整行不给，见下面那根"农场不给行"的钉子）⇒ 换**同类形状**的 `Desert`。
    # ⚠️ 2026-10-06 又改：原来用的是 `Mountain` —— 可 `Mountain` **在按图校准表里有条目**
    #    （`(68,24) 山湖钓鱼点(左)`）⇒ 修好「优先走校准点」之后它**根本不进水格扫描**了。
    #    这一档要的是"**没有校准点**的图"，所以夹具图名换成 `Desert`（按图/按区两张表都轮不到它：
    #    `get_spot("Desert")` = None）。
    FISH_NOAREA = {"ok": True, "location": "Desert", "hasFishAreaData": True, "count": 0, "areas": []}
    # 农场载荷（**只用来验"整行不出现"**）：形状跟上面一样 —— 单看数据它是"能扫出落点"的。
    FISH_FARM = {"ok": True, "location": "Farm", "hasFishAreaData": True, "count": 0, "areas": []}
    FISH_CABIN = {"ok": True, "location": "Cabin", "hasFishAreaData": False, "count": 0, "areas": []}

    def _state(loc="Forest", x=80, y=80, in_hand=True, stamina=300, wh=(100, 100), level=10):
        return {"location": {"name": loc, "mapWidth": wh[0], "mapHeight": wh[1]},
                "player": {"x": x, "y": y, "stamina": stamina, "maxStamina": 300,
                           "rod": {"name": "铱金鱼竿", "upgrade": 4, "inHand": bool(in_hand)},
                           # ⚠️ `/state.player.fishing` **只在竿是当前工具时**才有
                           #    （`ModEntry.cs:6215-6236`）⇒ `in_hand=False` 这一支正是"竿在包里"，
                           #    等级得靠 `GET /profile` 兜底（见 `_fish_level`）。
                           "fishing": ({"isFishing": False, "fishingLevel": level}
                                       if in_hand else None)},
                "activeMenu": None}

    def _acct(api, caps=None):
        """走服务器那条真路（`_im_fish`）；⚠️ 顺手清缓存（同一张图的账会缓存 60s）。

        ⚠️ 默认 caps **必须带 `water_fishable`**（2026-10-05 新加的假门闸①）：不带它，
           水格扫描那档整趟不扫（老 DLL 的行为，另有专测）。
        """
        M._FISH_CACHE.update({"key": None, "ts": 0.0, "raw": None})
        M._FISH_LEVEL_CACHE.update({"ts": 0.0, "lv": None})   # 等级缓存也要清（跨用例会串）
        M.api = api
        return M._im_fish(api._state,
                          caps if caps is not None
                          else {"fish_areas": True, "water_fishable": True})

    def _sheet(acct, loc="Forest", px=80, py=80):
        """用真 `ctx_from` 拼 Ctx（也顺手钉住「新字段 `fish=` 真的接上了」）再渲染一屏。"""
        st = _state(loc, px, py)
        ctx = IM.ctx_from(st, {}, caps={"fish_areas": True}, fish=acct)
        IM.reset_menu()
        return ctx, IM.render_menu(ctx, n=40)

    def _no_of(needle):
        no = next((r.no for r in IM._LAST_ROWS if needle in (r.label or "")), None)
        if no is None:
            raise AssertionError("这一屏没有含「%s」的行：%s"
                                 % (needle, " / ".join((r.label or "?") for r in IM._LAST_ROWS)))
        return no

    api = FakeApi(_state("Forest", in_hand=True), fish_areas=FISH_FOREST)
    acct = _acct(api)
    ctx, menu = _sheet(acct)
    ck("手上有竿（rod.inHand=true）⇒ 单子上有「垂钓」", "垂钓" in menu, menu)
    ck("…两个有钓点的水域 ⇒ **目录行**（句尾带省略号）", "垂钓…" in menu, menu)
    ck("…计数栏印的是**水域名**，不是默认那句「N 件」（水域不是「件」——同屏两把尺子是老病）",
       "件 ·" not in menu and "Lake" in menu, menu)

    api = FakeApi(_state("Forest", in_hand=False), fish_areas=FISH_FOREST)
    acct2 = _acct(api)
    ctx2, menu2 = _sheet(acct2)
    # 🔴 2026-10-06 恒纠正：「**我记得我说的是包里有竿子就行，不用在手**」——
    #    原来这里钉的是"不在手 ⇒ 整行不出现"（我把它做严了 ✗）。现在：**有竿就给**
    #    （鱼区/校准点那两档不依赖落点距离 D）；只有**水格扫描**那档因为要钓鱼等级算 D 才给不出来。
    ck("竿在背包/不在手（`rod.inHand=false`）⇒ **照样出「垂钓」**（恒：有竿就行）",
       "垂钓" in menu2, menu2)
    ck("…而且**真去问了游戏**（竿闸只要求「有竿」）",
       any(ep == "/fish_areas" for ep, _ in api.gets), str(api.gets))
    ck("…账里有那一档（不再直接回 `{}`）", bool(acct2), str(acct2))

    api = FakeApi(_state("Forest", in_hand=True), fish_areas=FISH_FOREST)
    acct3 = _acct(api, caps={})          # 老 DLL：`/status.caps` 没有 fish_areas
    ck("老 DLL（caps 没 fish_areas）⇒ 不给行（**绝不退回手抄表**）", acct3 == {}, str(acct3))
    ck("…那一发 `/fish_areas` 压根没打", not any(ep == "/fish_areas" for ep, _ in api.gets),
       str(api.gets))

    print("\n② 层数：2 个 ⇒ 两级 · 1 个 ⇒ 只有一层 · 0 个但图上有水 ⇒ 一层（扫描）")
    api = FakeApi(_state("Forest", in_hand=True), fish_areas=FISH_FOREST)
    acct = _acct(api)
    ck("2 个有钓点的水域 ⇒ `picks` 只有那 2 个（没钓点那个**不在**）",
       len(acct.get("picks") or []) == 2, str(acct))
    ctx, menu = _sheet(acct)
    lv2 = IM.do_row(_no_of("垂钓"), lambda op, a: {"ok": True, "text": "x"}, ctx)
    ck("敲「垂钓」⇒ 第二层列出两个水域（标题点明去哪一处）",
       "去哪一处" in lv2 and "Lake" in lv2 and "Lower_River 2" in lv2, lv2)
    ck("…**两行**（第三行那个没钓点的不许上）",
       sum(1 for l in lv2.splitlines() if "←" in l) == 2 and len(IM._LAST_ROWS) == 2, lv2)

    # 1 个有钓点 ⇒ 只有一层（`subs` 返回 None ⇒ 这一行就是动作行）
    one = {"ok": True, "location": "Forest", "hasFishAreaData": True, "count": 2,
           "areas": [FISH_FOREST["areas"][0], FISH_FOREST["areas"][2]]}
    api = FakeApi(_state("Forest", in_hand=True), fish_areas=one)
    acct1 = _acct(api)
    ctx1, menu1 = _sheet(acct1)
    ck("1 个有钓点的水域 ⇒ **只有一层**（行尾没有省略号）",
       "垂钓" in menu1 and "垂钓…" not in menu1, menu1)
    ck("…理由栏直接给出那个钓点坐标（不让 AI 再点一层才看得见）",
       "(34,25)" in menu1, menu1)
    ck("…`_fish_subs` 确实返回 None（判据就是「能去的水域 ≤1」）",
       IM._fish_subs(ctx1, [None]) is None, str(acct1))

    # 0 个有钓点 + 图上有水 ⇒ 一层（水格扫描，**按落点挑位**）
    # ⚠️ 2026-10-06（恒：「这个落点也得自动算哦，因为前期会有变化」）：那一档现在按
    #    「站格 + 朝向 × D = 落点必须是 `fishable` 的水」挑位，D 由 `_fish_cast_d` 自动来
    #    （实测优先／游戏公式＋实测蓄力）。下面把实测账**打桩**成 10 级 h=7/v=6、蓄力 1.0
    #    —— 就是今天真机四方向量出来的那组数（不是编的）。
    M._FISH_WATER_SWEEP_ENABLED = True
    M._fish_cast_dist_raw = lambda: {"power": 1.0, "obs": {"10": {"h": 7, "v": 6}}}
    # ⚠️ `/water` 的每格**必须**带 `fishable`（游戏自己的抛竿判据 `isTileFishable`，`GL:2330`）
    #    ——这一档只认 `fishable is True` 的格（假门闸②，见 `_fish_water_scan` 的注释）。
    # 水格 (31,31)（fishable）⇒ 只有"站 (38,31) 面左"这一对能把鱼漂丢到它上面（D_h=7）
    water = {(30, 30): [{"x": 31, "y": 31, "canCrabPot": False, "fishable": True}]}
    passable = {(38, 31): True}
    api = FakeApi(_state("Desert", x=40, y=32, in_hand=True, wh=(80, 65)),
                  fish_areas=FISH_NOAREA, water=water, passable=passable)
    acct_w = _acct(api)
    picks_w = acct_w.get("picks") or []
    ctx_w, menu_w = _sheet(acct_w, loc="Desert", px=40, py=32)
    ck("0 个有钓点但图上有水 ⇒ **一层**且给得出钓点（走 `/water` 扫描）",
       len(picks_w) == 1 and "垂钓…" not in menu_w, str(acct_w) + " || " + menu_w)
    ck("…挑出来的是**落点那一对**：站 (38,31) 面左 3 ⇒ 鱼漂飞 7 格正好落在水 (31,31) 上",
       bool(picks_w) and picks_w[0].get("standX") == 38 and picks_w[0].get("standY") == 31
       and picks_w[0].get("dir") == 3 and picks_w[0].get("waterX") == 31
       and picks_w[0].get("waterY") == 31 and picks_w[0].get("area") == "", str(picks_w))
    ck("…⛔ 不是「四邻那一格」（老错模型会给 (32,31) ⇒ 鱼漂会飞过头）",
       bool(picks_w) and picks_w[0].get("standX") != 32, str(picks_w))
    ck("…是多锚点拼的（`/water` 带上了半径上限 `_FISH_WATER_RADIUS`）",
       any(ep == "/water" and a.get("radius") == M._FISH_WATER_RADIUS for ep, a in api.gets),
       str(api.gets[:3]))
    ck("…⛔ 没自己编一种水域名（`area` 是空串，不是「池塘」这种我们发明的词）",
       not any(isinstance(p.get("area"), str) and p.get("area") for p in picks_w), str(picks_w))

    # 🔴 假门闸①：老 DLL 的 `/water` **不吐 `fishable`** ⇒ 这一档**整趟不扫**（宁可不给行）
    api_old = FakeApi(_state("Desert", x=40, y=32, in_hand=True, wh=(80, 65)),
                      fish_areas=FISH_NOAREA, water=water, passable=passable)
    acct_old = _acct(api_old, caps={"fish_areas": True})           # 只给了老的两位
    ck("老 DLL（caps 没有 `water_fishable`）⇒ 水格扫描那档**一行都不给**",
       acct_old == {} and not any(ep == "/water" for ep, _ in api_old.gets),
       str(acct_old) + " || " + str(api_old.gets[:2]))

    # 🔴 假门闸②：`/water` 说这格 `fishable:false`（看着是水、抛不出去）⇒ 不算钓点
    for _bad, _why in (({"x": 31, "y": 31, "canCrabPot": True, "fishable": False},
                        "`fishable:false`（真机：农场池塘北沿那排就是这个）"),
                       ({"x": 31, "y": 31, "canCrabPot": True},
                        "**缺 `fishable` 键**（老 DLL 的回包）")):
        _a = FakeApi(_state("Desert", x=40, y=32, in_hand=True, wh=(80, 65)),
                     fish_areas=FISH_NOAREA, water={(30, 30): [_bad]},
                     passable={(38, 31): True})
        _ac = _acct(_a)
        ck(f"…{_why} ⇒ 那一格水**不算钓点**（整行不出现，⛔ 连 `canCrabPot` 都不拿来当理由）",
           _ac == {}, str(_ac))

    # 📏 落点距离 **D 算不出来 ⇒ 整档不给行**（⛔ 宁可空着，也不拿"旁边那格"糊一个）
    M._fish_cast_dist_raw = lambda: {}                 # 从没量过
    _a = FakeApi(_state("Desert", x=40, y=32, in_hand=True, wh=(80, 65)),
                 fish_areas=FISH_NOAREA, water=water, passable=passable)
    _ac = _acct(_a)
    ck("📏 从没量过抛竿距离（`_fish_cast_dist.json` 空）⇒ 那一档**一行都不给**",
       _ac == {}, str(_ac))
    ck("…而且**连水都不扫**（算不出 D 就早退，不白打 `/water`）",
       not any(ep == "/water" for ep, _ in _a.gets), str(_a.gets[:2]))
    M._fish_cast_dist_raw = lambda: {"power": 1.0, "obs": {"10": {"h": 7, "v": 6}}}

    # 水格扫描那档**永远只给一层**（那些钓点没有名字 ⇒ 第二层会变成几行同名）
    water2 = {(30, 30): [{"x": 31, "y": 31, "fishable": True},
                         {"x": 28, "y": 29, "fishable": True}]}
    passable2 = {(38, 31): True, (35, 29): True}       # (28,29) 的落点对：站 (35,29) 面左 7 格
    api = FakeApi(_state("Desert", x=40, y=32, in_hand=True, wh=(80, 65)),
                  fish_areas=FISH_NOAREA, water=water2, passable=passable2)
    acct_w2 = _acct(api)
    ctx_w2, menu_w2 = _sheet(acct_w2, loc="Desert", px=40, py=32)
    ck("…扫出**两处**岸位也只给一层（没名字 ⇒ 第二层就是几行同名，恒拍板这档只有一层）",
       len(acct_w2.get("picks") or []) == 2 and "垂钓…" not in menu_w2
       and IM._fish_subs(ctx_w2, [None]) is None, str(acct_w2) + " || " + menu_w2)

    # 0 个有钓点 + 一点水都没有 ⇒ 整行不出现
    api = FakeApi(_state("Cabin", x=6, y=6, in_hand=True, wh=(12, 12)),
                  fish_areas=FISH_CABIN, water={}, passable={})
    acct_n = _acct(api)
    ctx_n, menu_n = _sheet(acct_n, loc="Cabin", px=6, py=6)
    ck("0 个有钓点 + 图上一格水都没有 ⇒ **整行不出现**",
       acct_n == {} and "垂钓" not in menu_n, str(acct_n) + " || " + menu_n)
    ck("…也真问过了（不是「没读就算没有」）：`/water` 打了、回包 ok 但是空",
       any(ep == "/water" for ep, _ in api.gets), str(api.gets[:2]))

    # 🔴 恒 2026-10-05：「河流农场有很多小河，而轮回的钓鱼等级是 10，抛到对岸也会算没水」
    #    ⇒ 那一档改成**按落点挑位**（见上面那组检查）；`_FISH_WATER_SWEEP_ENABLED` 现在只当**紧急开关**。
    #    这条钉子钉的是"开关关掉就真不给行"（真机又验出它在骗人时，先关再查）。
    M._FISH_WATER_SWEEP_ENABLED = False
    api_off = FakeApi(_state("Desert", x=40, y=32, in_hand=True, wh=(80, 65)),
                      fish_areas=FISH_NOAREA, water=water, passable=passable)
    acct_off = _acct(api_off)
    ck("⛔ 紧急开关 `_FISH_WATER_SWEEP_ENABLED=False` ⇒ 没有鱼区/也没校准点的图（Desert）**一行都不给**",
       acct_off == {} and not any(ep == "/water" for ep, _ in api_off.gets),
       str(acct_off) + " || " + str(api_off.gets[:2]))
    M._FISH_WATER_SWEEP_ENABLED = True   # 后面的检查继续钉"逻辑本身"（见上面那段说明）

    # 🎣🔴 2026-10-06 修：**「优先走校准点」原来是一次都没生效的死码** ——
    #    那一档问的是 `_fish_calibrated_poi({"id": ""}, loc)`，而那个函数第二路要"鱼区矩形"证据
    #    （`area["position"]`），我们传的假区**没有 `position`** ⇒ `not isinstance(pos, dict)` ⇒
    #    **恒返回 None**（实测 Forest/Beach/Mountain/IslandSouth/IslandSouthEastCave 五张全 None）。
    #    ⇒ 没有鱼区的图（`/fish_areas count=0`：姜岛南岸、海盗湾、山湖、镇子…）**从来没用过校准点**，
    #      全去跑水格扫描；扫描挑不出落点时就"一行都没有"（恒的 (26,34)/(6,8)/(68,24) 白标了）。
    #    现在改走 `_fish_calibrated_map`（按图那张表**自己就声明了"这是本图的校准点"**，不做矩形验证）。
    for _loc, _sx, _sy, _dir, _poi in (
            ("Mountain", 68, 24, 2, "山湖钓鱼点(左)"),
            ("Beach", 52, 25, 2, "海滩钓鱼点(码头)"),
            ("Town", 3, 93, 2, "镇鲶鱼钓点"),
            ("IslandSouth", 26, 34, 2, "姜岛南岸海钓点"),
            ("IslandSouthEastCave", 6, 8, 1, "海盗湾内钓点")):
        _a = FakeApi(_state(_loc, x=40, y=32, in_hand=True, wh=(80, 65)),
                     fish_areas=FISH_NOAREA, water=water, passable=passable)
        _ac = _acct(_a)
        _pk = _ac.get("picks") or []
        ck(f"🎣 没有鱼区的图 `{_loc}` ⇒ **直接用按图校准点** "
           f"({_sx},{_sy}) 朝{_dir}「{_poi}」（⛔ 不再靠水格扫描挑）",
           len(_pk) == 1 and (_pk[0].get("standX"), _pk[0].get("standY")) == (_sx, _sy)
           and _pk[0].get("dir") == _dir and _pk[0].get("calibrated") == _poi, str(_ac))
        ck(f"…`{_loc}` **连 `/water` 都不打**（省一发大图逐格扫描）",
           not any(ep == "/water" for ep, _ in _a.gets), str(_a.gets[:2]))
    # ⛔ 反面：**没有校准点**的图不许凭空"变"一个出来（⛔ 尤其别把弃用的 `FISHING_SPOTS` 当校准点）
    _a = FakeApi(_state("Desert", x=40, y=32, in_hand=True, wh=(80, 65)),
                 fish_areas=FISH_NOAREA, water=water, passable=passable)
    ck("⛔ 没有校准点的图（Desert）⇒ `_fish_calibrated_map` 给 `None`（照旧走扫描，不编点）",
       M._fish_calibrated_map("Desert") is None and M._fish_calibrated_map("") is None,
       str(M._fish_calibrated_map("Desert")))

    # 🎣🔴 2026-10-06 修：**钓鱼等级不能"读不到就当 0 级"** ——
    #    `/state.player.fishing` 只在**竿是当前工具**时才有（`ModEntry.cs:6215-6236`），
    #    而「垂钓」那行**竿在包里就出现**（恒：「包里有竿子就行，不用在手」）⇒
    #    竿在包里时老代码 `int(None)` 抛异常被吞 ⇒ 按 **0 级**算 D（`_fish_added_distance(None)=0`）
    #    ⇒ 10 级的人算出 4 格（真值 7）⇒ 挑出来的"站格＋朝向"落点是错的（按下去白跑 = 假门）。
    #    ⇒ 现在兜底问 `GET /profile`（`skills.fishing`，与手上拿什么无关）；两处都没有 ⇒ 如实不给行。
    M._fish_cast_dist_raw = lambda: {"power": 1.0, "obs": {}}      # 没实测 ⇒ 只能靠"等级"现算
    _bag = _state("Desert", x=40, y=32, in_hand=False, wh=(80, 65))  # 竿在包里 ⇒ 没有 fishing 段
    _a = FakeApi(_bag, fish_areas=FISH_NOAREA, water=water, passable=passable,
                 profile={"ok": True, "skills": {"fishing": 10}})
    _ac = _acct(_a)
    _pk = _ac.get("picks") or []
    ck("🎣 竿在包里（`/state` 没有 `player.fishing`）⇒ 等级兜底走 `GET /profile`："
       "10 级算出 D_h=7 ⇒ 站 (38,31) 面左落 (31,31) 仍能出这一行",
       any(ep == "/profile" for ep, _ in _a.gets)
       and _pk and (_pk[0].get("standX"), _pk[0].get("standY")) == (38, 31), str(_ac) + str(_a.gets[:3]))
    ck("…而**不是**按 0 级算（0 级 D_h=4 ⇒ 会给站 (35,31) 那种错位；⛔ 这正是原来那个假门）",
       bool(_pk) and _pk[0].get("standX") != 35, str(_pk[:1]))
    # ⛔ 两条路都读不到等级 ⇒ **整档不给行**（⛔ 绝不默默当 0 级）
    M._FISH_LEVEL_CACHE.update({"ts": 0.0, "lv": None})
    _a = FakeApi(_bag, fish_areas=FISH_NOAREA, water=water, passable=passable)   # profile=None ⇒ 端点不存在
    _ac = _acct(_a)
    ck("⛔ `GET /profile` 也读不到（老 DLL）⇒ 水格扫描那档**一行都不给**（如实，不按 0 级猜）",
       _ac == {}, str(_ac))
    ck("📏 `_fish_cast_d(level=None, …)` ⇒ **None**（⛔ 不许默默降级成 0 级：10 级真值 7 格）",
       M._fish_cast_d(None, "h") is None and M._fish_cast_d(None, "v") is None,
       str((M._fish_cast_d(None, "h"), M._fish_cast_d(None, "v"))))
    # 等级那发**要缓存**（`intent show` 一屏一发，不能每屏都打 `/profile`）
    M._FISH_LEVEL_CACHE.update({"ts": 0.0, "lv": None})
    _a = FakeApi(_bag, fish_areas=FISH_NOAREA, water=water, passable=passable,
                 profile={"ok": True, "skills": {"fishing": 10}})
    M.api = _a
    M._fish_level(_bag)
    _a.gets[:] = []
    M._fish_level(_bag)
    ck("…等级那一发**有 60s 缓存**（同一屏重复渲染不再打 `/profile`）",
       not any(ep == "/profile" for ep, _ in _a.gets), str(_a.gets[:2]))
    M._FISH_LEVEL_CACHE.update({"ts": 0.0, "lv": None})
    M._fish_cast_dist_raw = lambda: {"power": 1.0, "obs": {"10": {"h": 7, "v": 6}}}

    # 🚫🏡 恒 2026-10-06：「**除了河流农场之外应该就一两个水潭子，而且只有森林农场钓得木跃鱼，
    #    其他农场钓上来都是垃圾** ⇒ 我建议**撤掉农场的钓鱼选项单**，实在需要的时候让 ai 自己调用 fish 抛。」
    _api_farm = FakeApi(_state("Farm", x=40, y=32, in_hand=True, wh=(80, 65)),
                        fish_areas=FISH_FARM, water=water, passable=passable)
    _acct_farm = _acct(_api_farm)
    _ctx_farm, _menu_farm = _sheet(_acct_farm, loc="Farm", px=40, py=32)
    ck("🚫 农场（Farm）⇒ **整行不出现**（恒拍板撤掉农场钓鱼档）",
       _acct_farm == {} and "垂钓" not in _menu_farm, str(_acct_farm) + " || " + _menu_farm)
    ck("…而且**连水格扫描都不打**（那档在农场大图上最贵，白烧）",
       not any(ep == "/water" for ep, _ in _api_farm.gets), str(_api_farm.gets[:3]))
    # ⚠️ 只是"不上单子"，**不是**"农场不能钓"：同图换到 `FarmHouse`（另外的图）判据不该被误伤
    ck("…判据只认农场那张图本体：`FarmHouse`/`FarmCave` 不算（别用 startswith('Farm') 一刀切）",
       M._is_farm_map("Farm") and M._is_farm_map("Farm_Island")
       and not M._is_farm_map("FarmHouse") and not M._is_farm_map("FarmCave"),
       str([M._is_farm_map(x) for x in ("Farm", "Farm_Island", "FarmHouse", "FarmCave")]))

    # 📏 落点距离 D 的算法（**自动**：实测优先 → 公式＋实测蓄力 → 算不出给 None）
    M._fish_cast_dist_raw = lambda: {"power": 1.0, "obs": {"10": {"h": 7, "v": 6}}}
    ck("📏 10 级有实测 ⇒ 直接用**实测**（h=7 / v=6，不是拿公式现算）",
       M._fish_cast_d(10, "h") == 7 and M._fish_cast_d(10, "v") == 6,
       str((M._fish_cast_d(10, "h"), M._fish_cast_d(10, "v"))))
    M._fish_cast_dist_raw = lambda: {"power": 1.0, "obs": {}}
    ck("📏 本等级没实测、但有实测蓄力 ⇒ 按游戏公式现算：10 级 h=7 / v=6（加成 +3）",
       M._fish_cast_d(10, "h") == 7 and M._fish_cast_d(10, "v") == 6,
       str((M._fish_cast_d(10, "h"), M._fish_cast_d(10, "v"))))
    ck("📏 **等级跨档要跟着变**（前期就在变）：1 级加成+1 ⇒ h=5/v=4 · 4 级 ⇒ h=6/v=5 · 15 级 ⇒ h=8/v=7",
       (M._fish_cast_d(1, "h"), M._fish_cast_d(1, "v")) == (5, 4)
       and (M._fish_cast_d(4, "h"), M._fish_cast_d(4, "v")) == (6, 5)
       and (M._fish_cast_d(15, "h"), M._fish_cast_d(15, "v")) == (8, 7),
       str([(lv, M._fish_cast_d(lv, "h"), M._fish_cast_d(lv, "v")) for lv in (0, 1, 4, 8, 15)]))
    ck("📏 蓄力小 ⇒ 落到**下限 2 格**（`Math.Max(128f, …)`，128px=2 格）",
       M._fish_cast_d(0, "h") == 4 and M._fish_cast_d(0, "v") == 3, "0 级加成=0 ⇒ 4/3")
    M._fish_cast_dist_raw = lambda: {"power": 0.2}
    ck("📏 蓄力 0.2 ⇒ 撞下限：h=v=2（不是 0/1）",
       M._fish_cast_d(0, "h") == 2 and M._fish_cast_d(0, "v") == 2,
       str((M._fish_cast_d(0, "h"), M._fish_cast_d(0, "v"))))
    M._fish_cast_dist_raw = lambda: {}
    ck("📏 蓄力也没量过 ⇒ **None**（消费侧据此整档不给行，⛔ 不许拿 7 顶替）",
       M._fish_cast_d(10, "h") is None and M._fish_cast_d(10, "v") is None,
       str((M._fish_cast_d(10, "h"), M._fish_cast_d(10, "v"))))
    ck("📏 等级加成逐字照抄游戏（`FishingRod.cs:357`）：≥15⇒4 / ≥8⇒3 / ≥4⇒2 / ≥1⇒1 / 0⇒0",
       [M._fish_added_distance(v) for v in (0, 1, 3, 4, 7, 8, 14, 15, 20)] == [0, 1, 1, 2, 2, 3, 3, 4, 4],
       str([M._fish_added_distance(v) for v in (0, 1, 3, 4, 7, 8, 14, 15, 20)]))
    M._fish_cast_dist_raw = lambda: {"power": 1.0, "obs": {"10": {"h": 7, "v": 6}}}

    # 🎣 鱼区那档的 `fishable` 口径（2026-10-05）：**只丢游戏明说钓不了的**，缺键的照旧留着
    _f = {"ok": True, "location": "Forest", "hasFishAreaData": True, "count": 1,
          "areas": [{"id": "River", "displayName": None, "position": None,
                     "waterTiles": 9, "fishableTiles": 1, "spotsFound": 1, "spotsTruncated": False,
                     "spots": [{"waterX": 21, "waterY": 76, "standX": 20, "standY": 76,
                                "dir": 1, "fishable": False}]}]}
    M._FISH_CACHE.update({"key": None, "ts": 0.0, "raw": None})
    _a = FakeApi(_state("Forest", in_hand=True), fish_areas=_f)
    M.api = _a
    # 🔴 2026-10-06 恒：「**那就用我们标的那两个森林钓点？一个河流一个湖泊的**」——
    #    ⚠️ 这一格**正是实况**：`/fish_areas` 自己挑给 Forest `River` 的是 `(21,76)`/站 `(20,76)`，
    #    而它 `fishable:false`（游戏说那格抛不出去）……可**恒的校准点就是同一处**、真机 `isFishing=True` 还进了小游戏 ✓
    #    （根因：鱼区那档按矩形挑水格挑错了，不是水不对）。
    #    ⇒ 规矩改成一对：**该水域有校准点 ⇒ 一律用校准点（`fishable` 不参与）；没有才按 `fishable` 筛**。
    _pick_cal = (M._im_fish(_a._state, {"fish_areas": True, "water_fishable": True}) or {}).get("picks") or []
    ck("🎣 有校准点的水域：`spots[].fishable:false` **照样出一行**（用校准点，`fishable` 不参与）",
       bool(_pick_cal) and (_pick_cal[0] or {}).get("standX") == 20 and (_pick_cal[0] or {}).get("standY") == 76
       and (_pick_cal[0] or {}).get("dir") == 1,
       str(_pick_cal[:1]))
    # 反面：**没有校准点**的水域 ⇒ `fishable:false` 仍然"整区不上单子"（宁缺勿编，那条硬规矩没被放松）
    _f_nocal = {"ok": True, "location": "Desert", "hasFishAreaData": True, "count": 1,
                "areas": [{"id": "BottomPond", "displayName": None, "position": None,
                           "waterTiles": 9, "spotsFound": 1, "spotsTruncated": False,
                           "spots": [{"waterX": 21, "waterY": 76, "standX": 20, "standY": 76,
                                      "dir": 1, "fishable": False}]}]}
    M._FISH_CACHE.update({"key": None, "ts": 0.0, "raw": None})
    _a2 = FakeApi(_state("Desert", in_hand=True), fish_areas=_f_nocal)
    M.api = _a2
    ck("⛔ 没有校准点的水域：`spots[].fishable:false` ⇒ 那一区**不上单子**（硬规矩没放松）",
       M._im_fish(_a2._state, {"fish_areas": True, "water_fishable": True}) == {}, "应当为 {}")
    _f2 = {"ok": True, "location": "Forest", "hasFishAreaData": True, "count": 1,
           "areas": [{"id": "River", "displayName": None, "position": None,
                      "waterTiles": 9, "spotsFound": 1, "spotsTruncated": False,
                      "spots": [{"waterX": 21, "waterY": 76, "standX": 20, "standY": 76,
                                 "dir": 1}]}]}          # ⚠️ **没有** fishable 这一位 = 老 DLL 的回包
    M._FISH_CACHE.update({"key": None, "ts": 0.0, "raw": None})
    _a2 = FakeApi(_state("Forest", in_hand=True), fish_areas=_f2)
    M.api = _a2
    _ac2 = M._im_fish(_a2._state, {"fish_areas": True})
    ck("…**缺 `fishable` 键**（老 DLL）⇒ 照旧留着（Forest/Town/Beach/Desert 今天能用，不许因\"问不到\"全砍）",
       len(_ac2.get("picks") or []) == 1 and _ac2["picks"][0].get("dir") == 1, str(_ac2))

    print("\n③ 哨兵：「水域 ≠ 钓点」—— `spots:[]` 的水域不许上第二层")
    ck("没钓点那个 id（干水域）在整个单子/第二层里**一个字都不出现**",
       "干水域" not in menu and "干水域" not in lv2, lv2)
    ck("…它的 `noSpotReason` 也没被抄进理由栏（那是端点给审计看的，不是给 AI 的门）",
       "isWaterTile 恒 false" not in lv2, lv2)

    print("\n④ 名字**原样透传**：印的就是 `areas[].id`（没有中文小表、没有鱼种）")
    ck("怪 id 原样印出（`Lower_River 2` 一格没改）", "Lower_River 2" in lv2, lv2)
    ck("⛔ 单子上没有任何中文水域名（对照表一律不许进 Python）",
       not any(w in lv2 for w in ("湖泊", "河流", "池塘", "海洋", "喷泉", "水池")), lv2)
    ck("⛔ 没有鱼种预报（夹具里塞了鲤鱼/鲶鱼，单子上一个字都不许出现）",
       "鲤鱼" not in lv2 and "鲶鱼" not in lv2 and "鱼种" not in lv2, lv2)
    ck("…`displayName` 也不参与显示（夹具里全 null ⇒ 恒拍板印 id）",
       "displayName" not in lv2, lv2)

    print("\n⑤ 执行链按**被点那一区**走（不是「最近/默认」那个）")
    # Forest：Lake 用**校准钓点** (34,25)（`fish_run.FISHING_TARGETS`，恒真机验过）；
    #        另一个水域用它自己的 `spots` (20,76)。人站在 (80,80) —— **离后者近得多**。
    runs = []

    def _fake_run(op, a):
        runs.append((op, dict(a or {})))
        return {"ok": True, "st": "yes", "text": "🚀 已后台启动 fish_run"}

    api = FakeApi(_state("Forest", x=80, y=80, in_hand=True), fish_areas=FISH_FOREST)
    acct = _acct(api)
    ctx, menu = _sheet(acct)
    picked = list(acct.get("picks") or [])
    lake = next(p for p in picked if p.get("area") == "Lake")
    ck("…`Lake` 那一行用的是**校准钓点 (34,25) / face 2**（真机验过的那一份）",
       (lake.get("standX"), lake.get("standY"), lake.get("dir")) == (34, 25, 2)
       and lake.get("calibrated") == "森林小池塘钓点", str(lake))
    lv2 = IM.do_row(_no_of("垂钓"), _fake_run, ctx)                # 点开第二层（重发号）
    ck("…校准点的理由栏如实标出来（审计面：这一行凭什么用这个点）",
       "校准钓点" in lv2, lv2)
    IM.do_row(_no_of("Lower_River 2"), _fake_run, ctx)             # 敲**后面那一行**
    ck("敲第二个水域 ⇒ 传下去的是**它自己**的坐标 (20,76)/(20,77) dir=2",
       bool(runs) and runs[-1][0] == "fish"
       and (runs[-1][1].get("x"), runs[-1][1].get("y")) == (20, 76)
       and (runs[-1][1].get("wx"), runs[-1][1].get("wy")) == (20, 77)
       and runs[-1][1].get("dir") == 2
       and runs[-1][1].get("area") == "Lower_River 2", str(runs))

    runs.clear()
    ctx, menu = _sheet(acct)
    lv2 = IM.do_row(_no_of("垂钓"), _fake_run, ctx)
    IM.do_row(_no_of("Lake"), _fake_run, ctx)                      # 敲**前面那一行**
    ck("敲第一个水域（Lake）⇒ 传下去的是 (34,25)/(34,26) dir=2 + 那一区的 id",
       bool(runs) and (runs[-1][1].get("x"), runs[-1][1].get("y")) == (34, 25)
       and (runs[-1][1].get("wx"), runs[-1][1].get("wy")) == (34, 26)
       and runs[-1][1].get("area") == "Lake", str(runs))

    # 单子可能是**上一次**看的：人换了图/账变了，旧号不许拿去跨图乱走
    runs.clear()
    ctx_chg = IM.ctx_from(_state("Forest", x=80, y=80), {}, caps={"fish_areas": True},
                          fish={"mode": "areas", "picks": [
                              {"area": "Lower_River 2", "standX": 20, "standY": 76,
                               "waterX": 20, "waterY": 77, "dir": 2},
                              {"area": "Lake", "standX": 55, "standY": 66,
                               "waterX": 55, "waterY": 67, "dir": 2}]})
    IM.reset_menu()
    IM.render_menu(ctx, n=40)
    lv2 = IM.do_row(_no_of("垂钓"), _fake_run, ctx)
    IM.do_row(_no_of("Lake"), _fake_run, ctx_chg)                  # 用**新账**去敲旧号
    ck("账变了（人换图了）⇒ 仍走**同一处水域**的新坐标 (55,66)，**不跨图、也不换水域**",
       bool(runs) and (runs[-1][1].get("x"), runs[-1][1].get("y")) == (55, 66)
       and runs[-1][1].get("area") == "Lake", str(runs))

    runs.clear()
    ctx_gone = IM.ctx_from(_state("Town", x=80, y=80), {}, caps={"fish_areas": True},
                           fish={"mode": "areas", "picks": [
                               {"area": "River", "standX": 3, "standY": 93,
                                "waterX": 3, "waterY": 94, "dir": 2}]})
    IM.reset_menu()
    IM.render_menu(ctx, n=40)
    lv2 = IM.do_row(_no_of("垂钓"), _fake_run, ctx)
    out_gone = IM.do_row(_no_of("Lake"), _fake_run, ctx_gone)      # 新图根本没有 Lake
    ck("那一处水域**不在了** ⇒ 如实拒（不硬走、也不偷偷换成别的河）",
       runs == [] and "不在单子上" in out_gone, out_gone)

    print("\n⑥ 服务器执行侧：`_im_fish_go` 的六道次序")
    def _go(st, args, land=None):
        a = FakeApi(st)
        nav = FakeNav(a, land)
        scripts = []
        M.api = a
        M.navigation = nav
        M._run_script = lambda name, argv, **kw: (scripts.append((name, list(argv))),
                                                  "🚀 已后台启动")[1]
        return a, nav, scripts, M._im_fish_go(args)

    ARGS = {"x": 34, "y": 25, "wx": 34, "wy": 26, "dir": 2, "area": "Lake"}
    st = _state("Forest", x=80, y=80, in_hand=True)
    a, nav, scripts, out = _go(st, ARGS, land=(34, 25))
    ck("走到**被点那一区**的岸格（`navigation.walk_to(x=34,y=25)`）",
       bool(nav.calls) and nav.calls[0][1:] == (34, 25), str(nav.calls) + " || " + out)
    ck("…到点后按**实际站位**朝水 `/face 2`（水在正下方）",
       ("/face", {"direction": 2}) in a.posts, str(a.posts))
    ck("…`fish_run` 是**就地钓**（不带 `--location`）+ 带 `--port`（防挪恒角色）",
       bool(scripts) and scripts[0][0] == "fish_run"
       and "--location" not in scripts[0][1] and "--port" in scripts[0][1], str(scripts))
    ck("…回执说清去了哪一处（原样印 id + 坐标）", "Lake" in out and "(34,25)" in out, out)

    st = _state("Forest", x=36, y=25, in_hand=True)      # 人已经在岸格旁 2 格（同排）
    a, nav, scripts, out = _go(st, ARGS)
    ck("落点偏了就**按实际站位重算朝向**（(36,25)→水(34,26) 是朝左 `/face 3`，不是死抄 dir=2）",
       ("/face", {"direction": 3}) in a.posts and ("/face", {"direction": 2}) not in a.posts,
       str(a.posts))

    st = _state("Forest", x=80, y=80, in_hand=True)
    a, nav, scripts, out = _go(st, ARGS, land=None)      # 走位回执正常、但人没动（还在半路）
    ck("走位没把人送到 ⇒ **不开钓**（不许发一竿朝着走路方向）",
       scripts == [] and "还没站到" in out, out)
    ck("…并给出下一步（`map walk` / 再看一眼单子）", "map walk" in out and "show" in out, out)

    st = _state("Forest", x=80, y=80, in_hand=True, stamina=15)
    a, nav, scripts, out = _go(st, ARGS, land=(34, 25))
    ck("体力 15 < 钓鱼线 ⇒ 当场拦，**走位/脚本都没发生**（拦在走位之前）",
       scripts == [] and nav.calls == [] and "不开" in out, out)
    _blk = M._fish_stamina_block()
    ck("…判据与脚本**同一份**（`_fish_stamina_block` = `fish_run.MIN_STAMINA`，不是另写一个数）",
       "不开" in _blk and "20" in _blk, _blk)

    st = _state("Forest", x=80, y=80, in_hand=False)
    a, nav, scripts, out = _go(st, ARGS, land=(34, 25))
    # 🔴 2026-10-06 恒：「**包里有竿子就行，不用在手**」⇒ 执行侧**也不该因为"不在手"就不动**
    #    （`fish_run` 自己会 select 竿）。这里钉成：**照走照钓**（有竿 = 能干）。
    ck("执行前**竿在包里**（不在手）⇒ **照样走位开钓**（恒：有竿就行；`fish_run` 自己选竿）",
       bool(scripts) and bool(nav.calls) and "竿" not in (out or ""), out)

    st = _state("Forest", x=80, y=80, in_hand=True)
    a, nav, scripts, out = _go(st, {"wx": 34, "wy": 26, "area": "Lake"}, land=(34, 25))
    ck("行上没带坐标（老单子/账丢了）⇒ 当场报错、**不动手**",
       scripts == [] and nav.calls == [] and "show" in out, out)

    # ②b 🎣 那一格水**现在**钓不钓得着（游戏自己的 `isTileFishable`；只查一格）
    def _go_w(st, args, water, land=None):
        a = FakeApi(st, water=water)
        nav = FakeNav(a, land)
        scripts = []
        M.api = a
        M.navigation = nav
        M._run_script = lambda name, argv, **kw: (scripts.append((name, list(argv))),
                                                  "🚀 已后台启动")[1]
        return a, nav, scripts, M._im_fish_go(args)

    st = _state("Forest", x=34, y=25, in_hand=True)     # 人已经站在岸格上
    a, nav, scripts, out = _go_w(st, ARGS, {(34, 26): [{"x": 34, "y": 26, "fishable": False}]})
    ck("按下去时游戏说那格水 `isTileFishable=false` ⇒ **当场拒**（不走位、不抛竿、不设朝向）",
       scripts == [] and nav.calls == [] and "/face" not in [e for e, _ in a.posts]
       and "不许下竿" in out, out)
    a, nav, scripts, out = _go_w(st, ARGS, {(34, 26): [{"x": 34, "y": 26, "fishable": True}]})
    ck("…`fishable:true` ⇒ 照常走完（这一道闸不误伤能钓的点）",
       bool(scripts) and scripts[0][0] == "fish_run", out)
    a, nav, scripts, out = _go_w(st, ARGS, {})          # 老 DLL：回包里压根没有 fishable 这一位
    ck("…回包**没有** `fishable` 这一位（老 DLL）⇒ 鱼区那档**不因此变红**（Forest/Town/Beach/Desert 今天能用）",
       bool(scripts) and scripts[0][0] == "fish_run", out)

    # 📏 水格扫描那一档的"到点复核"必须按**落点**判（2026-10-06 真机踩到：旧尺子"≤4 格"把
    #    正确的行 (站 29,21 → 落点 29,27，D=6) 判成"够不着"，白走一趟）
    _SW = {"x": 29, "y": 21, "wx": 29, "wy": 27, "dir": 2, "area": ""}   # 落点在正下 6 格（D_v=6）
    st = _state("Mountain", x=29, y=21, in_hand=True, wh=(80, 65))
    a, nav, scripts, out = _go_w(st, _SW, {(29, 27): [{"x": 29, "y": 27, "fishable": True}]})
    ck("📏 水格扫描档：站 (29,21) 落点 (29,27) = 正下 6 格 ⇒ **照常开钓**（旧的「≤4 格」闸会误拦）",
       bool(scripts) and scripts[0][0] == "fish_run" and ("/face", {"direction": 2}) in a.posts, out)
    _BAD = dict(_SW, wy=30)                                              # 落点对不上（9 格，远超 D=6）
    st = _state("Mountain", x=29, y=21, in_hand=True, wh=(80, 65))
    a, nav, scripts, out = _go_w(st, _BAD, {(29, 30): [{"x": 29, "y": 30, "fishable": True}]})
    ck("…落点距离对不上 D（9 格 vs 该 6 格）⇒ **当场拒**、不发这一竿",
       scripts == [] and "对不上" in out, out)
    # ⚠️ 反向：**紧邻（≤4 格）**的 `wx/wy` 是"该朝哪一格"（校准点的 face 目标）⇒ **不走落点窗口**这条闸
    _NEAR = {"x": 34, "y": 25, "wx": 34, "wy": 26, "dir": 2, "area": "Lake"}
    st = _state("Forest", x=34, y=25, in_hand=True)
    a, nav, scripts, out = _go_w(st, _NEAR, {(34, 26): [{"x": 34, "y": 26, "fishable": True}]})
    ck("…紧邻那档（校准点 face 目标，1 格）⇒ **照旧放行**（别把鱼区那档一起收紧）",
       bool(scripts) and scripts[0][0] == "fish_run", out)

    print("\n⑦b 🎣 矿井钓点（20/60/100 层 · 站位这几层同一格：恒亲站的 (26,13) 朝右）")
    # 恒 2026-10-06：「位于这些层时也给对应的钓鱼选项，抛竿位置在现在 7842 的站位和朝向」
    #              + 「站位应该这几层都是一样的，**包括困难模式的矿井**站位也是一样的」
    # 真机那一刻：7842 在 UndergroundMine100 (26,13) facing=1；朝右第 7 格 (33,13) 实测 `fishable:true`；
    # 随后他在 **UndergroundMine60 也是 (26,13) facing=1** ⇒ 同格被两层坐实。
    FISH_MINE = {"ok": True, "location": "UndergroundMine100", "hasFishAreaData": False,
                 "count": 0, "areas": []}
    HOST_M = {"location": {"name": "UndergroundMine100"},
              "player": {"x": 26, "y": 13, "facingDirection": 1}}
    _mw = {(33, 13): [{"x": 33, "y": 13, "canCrabPot": False, "fishable": True}]}
    api = FakeApi(_state("UndergroundMine100", x=20, y=13, in_hand=True, wh=(50, 22)),
                  fish_areas=FISH_MINE, water=_mw, passable={(26, 13): True}, host=HOST_M)
    acct_m = _acct(api)
    pk_m = acct_m.get("picks") or []
    ck("矿井那三层：他**也在同一层**时用他的**当下**站位 (26,13) 朝向 1 ⇒ 落点 = 朝右 D=7 ⇒ (33,13)",
       acct_m.get("mode") == "host" and len(pk_m) == 1
       and (pk_m[0].get("standX"), pk_m[0].get("standY")) == (26, 13)
       and pk_m[0].get("dir") == 1
       and (pk_m[0].get("waterX"), pk_m[0].get("waterY")) == (33, 13), str(acct_m))
    ck("…名字用**游戏自己的图名**（`UndergroundMine100` 原样，层号就在里面）",
       pk_m and pk_m[0].get("area") == "UndergroundMine100", str(pk_m))
    ck("…⛔ 一个鱼种、一个中文水域名都没有（恒 2026-10-05 的老规矩）",
       not any(w in json.dumps(acct_m, ensure_ascii=False)
               for w in ("鬼鱼", "石鱼", "冰柱鱼", "岩浆鳗鱼", "洞穴凝胶", "水池", "岩浆")), str(acct_m))
    ck("…`_fish_subs` 返回 None（这一档只有一条 ⇒ 它自己就是动作行）",
       IM._fish_subs(IM.ctx_from(_state("UndergroundMine100", x=20, y=13, in_hand=True),
                                 {}, caps={"fish_areas": True}, fish=acct_m), [None]) is None, "")

    # 🔑 恒："站位这几层都一样" ⇒ **他不在场也能给行**（用常量 `_FISH_MINE_SPOT`）
    for _lv in (20, 60, 100):
        M._FISH_CACHE.update({"key": None, "ts": 0.0, "raw": None})
        _a = FakeApi(_state("UndergroundMine%d" % _lv, x=6, y=10, in_hand=True, wh=(50, 22)),
                     fish_areas=FISH_MINE, water=_mw, passable={(26, 13): True},
                     host={"location": {"name": "FarmHouse"},
                           "player": {"x": 29, "y": 27, "facingDirection": 0}})   # 他不在矿井
        _ac = _acct(_a)
        _p = (_ac.get("picks") or [{}])[0]
        ck("…他**不在场**也照样给行（第 %d 层）：用常量 (26,13)/朝右 ⇒ 落点 (33,13)" % _lv,
           _ac.get("mode") == "host"
           and (_p.get("standX"), _p.get("standY"), _p.get("dir")) == (26, 13, 1)
           and (_p.get("waterX"), _p.get("waterY")) == (33, 13), str(_ac))

    # 三道闸：任何一道过不去 ⇒ **如实不给行**
    for _h, _p, _w, _why in (
            (HOST_M, {(26, 13): False}, _mw, "站格**走不过去**"),
            (HOST_M, {(26, 13): True}, {(33, 13): [{"x": 33, "y": 13, "fishable": False}]},
             "落点**不是能钓的水**")):
        M._FISH_CACHE.update({"key": None, "ts": 0.0, "raw": None})
        _a = FakeApi(_state("UndergroundMine100", x=20, y=13, in_hand=True, wh=(50, 22)),
                     fish_areas=FISH_MINE, water=_w, passable=_p, host=_h)
        _ac = _acct(_a)
        ck(f"…{_why} ⇒ 矿井那一行**不给**（宁可不给，也不给假门）", _ac == {}, str(_ac))
    # 层号解析（照抄游戏 `MineShaft.cs:4864 GetLevelName`：`…<层>` 或 `…<层>:<强制布局>`）
    ck("…层号解析认 `:布局` 那种写法（困难模式/强制布局）⇒ 100 / 20 都认得出",
       M._fish_mine_level("UndergroundMine100") == 100
       and M._fish_mine_level("UndergroundMine100:1") == 100
       and M._fish_mine_level("undergroundmine20:3") == 20, str(M._fish_mine_level("UndergroundMine100:1")))
    ck("…⛔ 不是那三层就不走这条路（21 层 / 空名 / 别的图 ⇒ None）",
       [M._fish_mine_level(x) for x in ("UndergroundMine21", "UndergroundMine", "Mine", "Farm")] == [None] * 4,
       str([M._fish_mine_level(x) for x in ("UndergroundMine21", "UndergroundMine", "Mine", "Farm")]))

    print("\n⑦ 按鱼区校准表（`FISHING_AREA_TARGETS`）：三条映射 + 老路一字不变 + 无校准就退回游戏 spots")
    import fish_run as FR
    from locations import POI as _POI

    # ① POI 本身（同名 key 后写的赢 —— 2026-10-05 真踩过：森林河边钓点 被 (70,95) 那条静默盖掉）
    ck("`POI[森林河边钓点]` 唯一且 = (20,76)（曾有一条同名 (70,95)待校准 盖掉它）",
       (_POI.get("森林河边钓点") or {}).get("pos") == (20, 76), str(_POI.get("森林河边钓点")))
    ck("…`沙漠钓鱼点` = (9,10) · `森林小池塘钓点` = (34,25)（后两者没被同名盖）",
       (_POI.get("沙漠钓鱼点") or {}).get("pos") == (9, 10)
       and (_POI.get("森林小池塘钓点") or {}).get("pos") == (34, 25),
       str([_POI.get("沙漠钓鱼点"), _POI.get("森林小池塘钓点")]))

    # ② 老调用点（不传 area_id）**一字不变**
    _leg = {k: FR.get_spot(k) for k in ("Beach", "Mountain", "Forest", "Town")}
    ck("老路 `get_spot(图)` 4 张图一字不变（Forest 仍回 (34,25) 小池塘 face2）",
       [(k, _leg[k].get("x"), _leg[k].get("y"), _leg[k].get("face")) for k in
        ("Beach", "Mountain", "Forest", "Town")]
       == [("Beach", 52, 25, 2), ("Mountain", 68, 24, 2), ("Forest", 34, 25, 2), ("Town", 3, 93, 2)],
       str(_leg))

    # ③ 新路三条 + 两条兜底
    _r = FR.get_spot("Forest", "River")
    ck("`get_spot('Forest','River')` ⇒ 恒亲站的 (20,76) face=1（冬季鱼王点，面东抛向河面）",
       (_r or {}).get("x") == 20 and _r.get("y") == 76 and _r.get("face") == 1, str(_r))
    _l = FR.get_spot("Forest", "Lake")
    ck("`get_spot('Forest','Lake')` ⇒ (34,25) face=2（= 原按图那份，保持等价）",
       (_l or {}).get("x") == 34 and _l.get("y") == 25 and _l.get("face") == 2, str(_l))
    _d = FR.get_spot("Desert", "TopPond")
    ck("`get_spot('Desert','TopPond')` ⇒ (9,10) face=2（POI note 自证：站(9,10)朝下钓(9,11)）",
       (_d or {}).get("x") == 9 and _d.get("y") == 10 and _d.get("face") == 2, str(_d))
    _n = FR.get_spot("Forest", "NoSuchArea")
    ck("没进按区表的 id ⇒ 退回**按图**那张（Forest 仍回 (34,25)，不是「没找到就放弃」）",
       (_n or {}).get("x") == 34 and _n.get("y") == 25, str(_n))
    _b = FR.get_spot("Beach", "Default")
    ck("…Beach 没有按区条目 ⇒ 退回按图 (52,25)（只认表里那几条，不许顺手扩图）",
       (_b or {}).get("x") == 52 and _b.get("y") == 25, str(_b))

    # ④ 端到端：Forest 的 River / Lake 各走对了点（River 的 position 是 null ⇒ 只有按区表救得了它）
    FISH_FOREST_REAL = {"ok": True, "location": "Forest", "hasFishAreaData": True, "count": 2, "areas": [
        {"id": "River", "displayName": None, "position": None, "waterTiles": 300, "spotsFound": 2,
         "spots": [{"waterX": 91, "waterY": 5, "standX": 91, "standY": 6, "dir": 0},
                   {"waterX": 21, "waterY": 76, "standX": 21, "standY": 77, "dir": 0}]},
        {"id": "Lake", "displayName": None, "position": {"x": 30, "y": 20, "w": 10, "h": 10},
         "waterTiles": 59, "spotsFound": 1, "spotsTruncated": False,
         "spots": [{"waterX": 34, "waterY": 26, "standX": 34, "standY": 25, "dir": 2}]}]}
    api = FakeApi(_state("Forest", x=80, y=80, in_hand=True), fish_areas=FISH_FOREST_REAL)
    acct_f = _acct(api)
    _picks_f = {p.get("area"): p for p in (acct_f.get("picks") or [])}
    ck("端到端 Forest/River ⇒ (20,76) face1「校准钓点」（它的 position=null，按图那条路够不着）",
       (_picks_f.get("River", {}).get("standX"), _picks_f.get("River", {}).get("standY"),
        _picks_f.get("River", {}).get("dir")) == (20, 76, 1)
       and _picks_f["River"].get("calibrated") == "森林河边钓点", str(_picks_f.get("River")))
    ck("端到端 Forest/Lake ⇒ (34,25) face2（两个区各走各的，不串）",
       (_picks_f.get("Lake", {}).get("standX"), _picks_f.get("Lake", {}).get("standY"),
        _picks_f.get("Lake", {}).get("dir")) == (34, 25, 2), str(_picks_f.get("Lake")))
    _ctx_f, _m_f = _sheet(acct_f, loc="Forest", px=80, py=80)
    _lv2_f = IM.do_row(_no_of("垂钓"), _fake_run, _ctx_f)
    _rv_line = next((l for l in _lv2_f.splitlines() if "River" in l), "")
    ck("…第二层 `River` 那行印 (20,76) 且标「校准钓点」；`Lake` 行是 (34,25)（不是同一行复制两遍）",
       "(20,76)" in _rv_line and "校准钓点" in _rv_line and "(34,25)" in _lv2_f, _lv2_f)

    # ⑤ 端到端 Desert：唯一有水的 TopPond ⇒ 一层 + 校准点 (9,10)
    FISH_DESERT = {"ok": True, "location": "Desert", "hasFishAreaData": True, "count": 2, "areas": [
        {"id": "TopPond", "displayName": None, "position": None, "waterTiles": 59, "spotsFound": 2,
         "spots": [{"waterX": 10, "waterY": 13, "standX": 10, "standY": 12, "dir": 2},
                   {"waterX": 5, "waterY": 12, "standX": 5, "standY": 13, "dir": 0}]},
        {"id": "BottomPond", "displayName": None, "position": {"x": 0, "y": 56, "w": 255, "h": 255},
         "waterTiles": 0, "spotsFound": 0, "spots": [], "noSpotReason": "没扫到水格"}]}
    api = FakeApi(_state("Desert", x=40, y=40, in_hand=True), fish_areas=FISH_DESERT)
    acct_d = _acct(api)
    _pd = (acct_d.get("picks") or [])
    _m_d = _sheet(acct_d, loc="Desert", px=40, py=40)[1]
    ck("端到端 Desert/TopPond ⇒ (9,10) face2、账里 `calibrated`=沙漠钓鱼点、**只有一层**"
       "（BottomPond 无水不上；`position=null` ⇒ 只有按区表救得了它）",
       len(_pd) == 1 and (_pd[0].get("standX"), _pd[0].get("standY"), _pd[0].get("dir")) == (9, 10, 2)
       and _pd[0].get("calibrated") == "沙漠钓鱼点" and "垂钓…" not in _m_d
       and "(9,10)" in _m_d, str(_pd) + " || " + _m_d)

    # ⑥ 老「按图」那条路：同图两区**只有一个**能拿到校准点（矩形包含判据）
    FISH_TOWN = {"ok": True, "location": "Town", "hasFishAreaData": True, "count": 2, "areas": [
        {"id": "River", "displayName": None, "position": {"x": 0, "y": 0, "w": 120, "h": 120},
         "waterTiles": 300, "spotsFound": 2, "spots": [
             {"waterX": 91, "waterY": 5, "standX": 91, "standY": 6, "dir": 0},
             {"waterX": 3, "waterY": 94, "standX": 3, "standY": 93, "dir": 2}]},
        {"id": "EastPond", "displayName": None, "position": {"x": 80, "y": 0, "w": 30, "h": 30},
         "waterTiles": 20, "spotsFound": 1, "spots": [
             {"waterX": 85, "waterY": 10, "standX": 85, "standY": 11, "dir": 0}]}]}
    api = FakeApi(_state("Town", x=50, y=50, in_hand=True), fish_areas=FISH_TOWN)
    acct_t = _acct(api)
    _pt = {p.get("area"): p for p in (acct_t.get("picks") or [])}
    ck("按图那条路：`Town/River` 的矩形含 (3,93) ⇒ 用校准点 `镇鲶鱼钓点`（(3,93) face2）",
       (_pt.get("River", {}).get("standX"), _pt.get("River", {}).get("standY")) == (3, 93)
       and _pt["River"].get("calibrated") == "镇鲶鱼钓点", str(_pt.get("River")))
    ck("…`Town/EastPond` 的矩形**不含** (3,93) ⇒ 退回它自己的 spots (85,11)，`calibrated` 缺席",
       (_pt.get("EastPond", {}).get("standX"), _pt.get("EastPond", {}).get("standY")) == (85, 11)
       and "calibrated" not in _pt["EastPond"], str(_pt.get("EastPond")))
    IM.reset_menu()
    _ctx_t, _m_t = _sheet(acct_t, loc="Town", px=50, py=50)
    _lv2_t = IM.do_row(_no_of("垂钓"), _fake_run, _ctx_t)
    _ep_line = next((l for l in _lv2_t.splitlines() if "EastPond" in l), "")
    ck("…**无校准的鱼区那行不许报「校准钓点」**（按图那个点是 River 的，不是它的）",
       "校准" not in _ep_line and "校准钓点" in _lv2_t, _ep_line or _lv2_t)

    # ⑦ 恒亲站那条的**执行端**：站在 (20,76) ⇒ 面东朝 (21,76) 河面（`/face 1`）
    st = _state("Forest", x=20, y=76, in_hand=True)
    a, nav, scripts, out = _go(st, {"x": 20, "y": 76, "wx": 21, "wy": 76, "dir": 1, "area": "River"},
                               land=(20, 76))
    ck("执行端 Forest/River：站在 (20,76) ⇒ `/face 1`（面东朝河）+ 开了 `fish_run`",
       ("/face", {"direction": 1}) in a.posts and bool(scripts) and "River" in out,
       str(a.posts) + " || " + out)
    ck("…`(20,76)` 已经在岸格上 ⇒ **不用再走位**（map walk 不发）", nav.calls == [], str(nav.calls))
finally:
    for k, v in _real.items():
        setattr(M, k, v)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
