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
    def __init__(self, state=None, fish_areas=None, water=None, passable=None):
        self._state = state if state is not None else {}
        self.fish_areas = fish_areas          # None = 端点挂了/老 DLL
        self.water = water or {}              # (锚点x, 锚点y) -> [水格 dict]
        self.passable = passable or {}        # (x, y) -> True/False
        self.gets, self.posts = [], []

    def state(self, **kw):
        return self._state

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
    # 水格扫描那档（Farm：有鱼区数据但一个区都没有 ⇒ 退回 `/water`）
    FISH_FARM = {"ok": True, "location": "Farm", "hasFishAreaData": True, "count": 0, "areas": []}
    FISH_CABIN = {"ok": True, "location": "Cabin", "hasFishAreaData": False, "count": 0, "areas": []}

    def _state(loc="Forest", x=80, y=80, in_hand=True, stamina=300, wh=(100, 100)):
        return {"location": {"name": loc, "mapWidth": wh[0], "mapHeight": wh[1]},
                "player": {"x": x, "y": y, "stamina": stamina, "maxStamina": 300,
                           "rod": {"name": "铱金鱼竿", "upgrade": 4, "inHand": bool(in_hand)}},
                "activeMenu": None}

    def _acct(api, caps=None):
        """走服务器那条真路（`_im_fish`）；⚠️ 顺手清缓存（同一张图的账会缓存 60s）。"""
        M._FISH_CACHE.update({"key": None, "ts": 0.0, "raw": None})
        M.api = api
        return M._im_fish(api._state, caps if caps is not None else {"fish_areas": True})

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
    ck("竿在背包/不在手（rod.inHand=false）⇒ **整行不出现**", "垂钓" not in menu2, menu2)
    ck("…也没白问游戏（竿闸在 `/fish_areas` **之前**）",
       not any(ep == "/fish_areas" for ep, _ in api.gets), str(api.gets))
    ck("…`_im_fish` 直接回 `{}`（那行算不出来）", acct2 == {}, str(acct2))

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

    # 0 个有钓点 + 图上有水 ⇒ 一层（水格扫描）
    water = {(30, 30): [{"x": 31, "y": 31, "canCrabPot": False}]}
    passable = {(32, 31): True}
    api = FakeApi(_state("Farm", x=40, y=32, in_hand=True, wh=(80, 65)),
                  fish_areas=FISH_FARM, water=water, passable=passable)
    acct_w = _acct(api)
    picks_w = acct_w.get("picks") or []
    ctx_w, menu_w = _sheet(acct_w, loc="Farm", px=40, py=32)
    ck("0 个有钓点但图上有水 ⇒ **一层**且给得出钓点（走 `/water` 扫描）",
       len(picks_w) == 1 and "垂钓…" not in menu_w, str(acct_w) + " || " + menu_w)
    ck("…扫描出来的岸位就是 `/passable` 认的那一格 (32,31)、面朝水（dir=3 左）",
       bool(picks_w) and picks_w[0].get("standX") == 32 and picks_w[0].get("standY") == 31
       and picks_w[0].get("dir") == 3 and picks_w[0].get("area") == "", str(picks_w))
    ck("…是多锚点拼的（`/water` 带上了半径上限 `_FISH_WATER_RADIUS`）",
       any(ep == "/water" and a.get("radius") == M._FISH_WATER_RADIUS for ep, a in api.gets),
       str(api.gets[:3]))
    ck("…⛔ 没自己编一种水域名（`area` 是空串，不是「池塘」这种我们发明的词）",
       not any(isinstance(p.get("area"), str) and p.get("area") for p in picks_w), str(picks_w))

    # 水格扫描那档**永远只给一层**（那些钓点没有名字 ⇒ 第二层会变成几行同名）
    water2 = {(30, 30): [{"x": 31, "y": 31}, {"x": 28, "y": 29}]}
    passable2 = {(32, 31): True, (29, 29): True}
    api = FakeApi(_state("Farm", x=40, y=32, in_hand=True, wh=(80, 65)),
                  fish_areas=FISH_FARM, water=water2, passable=passable2)
    acct_w2 = _acct(api)
    ctx_w2, menu_w2 = _sheet(acct_w2, loc="Farm", px=40, py=32)
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
    ck("执行前发现**竿已不在手** ⇒ 什么都不做（走位/脚本都没有）",
       scripts == [] and nav.calls == [] and "竿" in out, out)

    st = _state("Forest", x=80, y=80, in_hand=True)
    a, nav, scripts, out = _go(st, {"wx": 34, "wy": 26, "area": "Lake"}, land=(34, 25))
    ck("行上没带坐标（老单子/账丢了）⇒ 当场报错、**不动手**",
       scripts == [] and nav.calls == [] and "show" in out, out)
finally:
    for k, v in _real.items():
        setattr(M, k, v)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
