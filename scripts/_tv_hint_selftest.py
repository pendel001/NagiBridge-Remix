# -*- coding: utf-8 -*-
"""🏠 「屋里有什么」查得到 —— 家具**动态问游戏**，不靠 POI 表一张死名单。2026-11-08

恒两句话（第二句是纠我第一版）：
  ① 「你确定家具的 poi 给了吗……ai 查什么都查不到怎么看电视」
  ② 「**不对不对不对，每个人的小屋里都有的。那就是漏做了。**他的屋子里有基本的家具，
     壁炉电视地毯床和一套桌椅和盆栽。但**无论它怎么找，屋里都只给床的坐标**」

⇒ 真相：POI 表里 `FarmHouse` 有三件家具、`Cabin` **只有「小屋(床)」**。
  而**每间小屋都有一整套家具**（游戏按地图摆的）⇒ 在自己屋里找电视，`map here` 永远只有床。

判据（都不猜）：
  ① `map here` 多一段**家具**，来源是 `/furniture`（游戏报的），**与 POI 表解耦**；
  ② `Cabin` 的 POI 里**没有**电视，但 `map here` 在 Cabin 里**照样**列得出来（证明是动态那半边在管）；
  ③ 读不到 / 空 ⇒ **不印**（别拿"这屋没家具"撒谎）；
  ④ 状态条那行**不许**再写"电视只在农舍"（每个家都有），也不许写 `scene at 中文`；
  ⑤ `help("怎么看电视")` 指到 `scene furniture`（`scene` 有 dispatch dict，指得进去）。
"""
import os
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


_FURN = {"furniture": [
    {"name": "电视", "x": 3, "y": 7, "isTV": True, "width": 3, "height": 2},
    {"name": "壁炉", "x": 8, "y": 4, "width": 2, "height": 2},
    {"name": "盆栽", "x": 2, "y": 5, "width": 1, "height": 2},
    {"name": "床", "x": 5, "y": 8, "width": 3, "height": 3},
    {"name": "桌子", "x": 6, "y": 2, "width": 2, "height": 2},
]}


class FakeApi:
    """假 7843：`map here` 只碰这几样。"""

    def __init__(self, loc="Cabin", furn=None, boom=False):
        self.loc, self.furn, self.boom = loc, furn, boom
        self.gets = []

    def current_location(self):
        return self.loc

    def state(self):
        return {"time": {"season": "spring", "dayOfMonth": 15, "timeOfDay": 800},
                "location": {"name": self.loc}}

    def _ai_get(self, ep, params=None):
        self.gets.append(ep)
        if ep == "/furniture":
            if self.boom:
                raise RuntimeError("老 DLL 没 /furniture")
            return self.furn if self.furn is not None else {"furniture": []}
        return {}


_BAK = (M.api, M._joja_form, M._place_lines)
M._joja_form = lambda: None
M._place_lines = lambda q, n=3: []

try:
    print("\n① 家具那段：来源是 `/furniture`，跟 POI 表解耦")
    _a = FakeApi("Cabin", furn=_FURN)
    M.api = _a
    _out = M.map_here()
    ck("①真的问了 `/furniture`", "/furniture" in _a.gets, str(_a.gets))
    ck("①印了家具段", "🛋" in _out, _out[:400])
    ck("①电视带坐标", "电视(3,7)" in _out, _out[:400])
    ck("①电视有 📺 标记", "📺电视(3,7)" in _out or "📺 电视" in _out, _out[:400])
    ck("①壁炉/盆栽/桌椅/床都在（恒点名的那些）",
       all(n in _out for n in ("壁炉(8,4)", "盆栽(2,5)", "床(5,8)", "桌子(6,2)")), _out[:500])
    ck("①告诉它怎么点（`scene at x,y`，不用走过去）",
       "scene at" in _out and "不用走过去" in _out, _out[:400])

    print("\n② Cabin 的 POI 表里确实**没有**电视（恒看到的「只给床」就是这个）——但 map here 照样列得出来")
    _cab_poi = [n for (n, _x, _y) in locations.legal_tokens_here("Cabin")["poi"]]
    ck("②POI 表里 Cabin 只有床（不骗人：这是事实）",
       all("电视" not in n for n in _cab_poi), str(_cab_poi))
    ck("②可 `map here` 在 Cabin 里**列得出电视**（动态那半边接管）", "电视(3,7)" in _out, _out[:300])
    ck("②农舍的静态 POI 还在（没被这层改坏）",
       any("电视" in n for (n, _x, _y) in locations.legal_tokens_here("FarmHouse")["poi"]),
       str(locations.legal_tokens_here("FarmHouse")["poi"]))

    print("\n③ 空 / 读不到 ⇒ **不印**（别拿「这屋没家具」撒谎）")
    M.api = FakeApi("Cabin", furn={"furniture": []})
    _o2 = M.map_here()
    ck("③家具空 ⇒ 没有 🛋 段", "🛋" not in _o2, _o2[:300])
    ck("③但仍然正常吐本图信息", "🗺️" in _o2, _o2[:200])
    M.api = FakeApi("Cabin", boom=True)
    _o3 = M.map_here()
    ck("③`/furniture` 炸了 ⇒ 不印、不崩", "🛋" not in _o3 and "🗺️" in _o3, _o3[:300])

    print("\n④ 状态条那行：不许再把 AI 往农舍赶，也不许 `scene at 中文`")
    for _show, _lb in (("离地而居", "离地而居"), ("酱料女王", "酱料女王"), (None, "无新节目")):
        _ln = M._tv_hint_line(_show)
        ck(f"④[{_lb}] 非空", bool(_ln.strip()), repr(_ln))
        ck(f"④[{_lb}] 给了单子那行", "intent show" in _ln, _ln)
        ck(f"④[{_lb}] 给了拿坐标那步（scene furniture → scene at）",
           "scene furniture" in _ln and "scene at" in _ln, _ln)
        ck(f"④[{_lb}] **不说「只在农舍」**（每个家都有电视）", "农舍" not in _ln, _ln)
        ck(f"④[{_lb}] 没有 `scene at 中文` 这种假参数", "at 点电视" not in _ln, _ln)

    print("\n⑤ `help` 查得到，且指到真 op")
    for _q in ("怎么看电视", "离地而居", "今天天气"):
        _h = M.help(_q)
        ck(f"⑤help({_q}) 没掉进「没有指引」", "没有「" not in _h, _h[:150])
        ck(f"⑤help({_q}) 指到 scene furniture", 'scene(ops="furniture")' in _h, _h[:200])
        ck(f"⑤help({_q}) 说了电视在每个家", "每个家" in _h, _h[:220])
        ck(f"⑤help({_q}) 说到 `scene at x,y`", "scene at" in _h, _h[:220])
    _entry = [e for e in M._INTENT_INDEX if any("看电视" in k for k in e[0].split(","))]
    ck("⑤意图索引那条域=scene op=furniture", bool(_entry) and _entry[0][1] == "scene"
       and _entry[0][2] == "furniture", str(_entry[0][1:3]) if _entry else "缺")

    print("\n⑥ 源码钉")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "nagi_mcp_server.py"), encoding="utf-8").read()
    _here = _src.split("def map_here")[1].split("def map(")[0]
    ck("⑥`map_here` 里真的读 `/furniture`", '"/furniture"' in _here, "没读 => 又变成死名单")
    ck("⑥家具段在 `legal_tokens_here` **之后**（不掺进 POI 表）",
       _here.find('"/furniture"') > _here.find("legal_tokens_here"), "顺序不对")
    ck("⑥`_TV_HOW` 只定义一次", _src.count("_TV_HOW = ") == 1, str(_src.count("_TV_HOW = ")))
    _bad = [ln.strip() for ln in _src.splitlines()
            if "scene at 点电视" in ln and not ln.lstrip().startswith("#")]
    ck("⑥代码里没有旧假路牌", not _bad, str(_bad[:2]))
finally:
    M.api, M._joja_form, M._place_lines = _BAK

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 屋里有什么：家具按游戏报的列（每间小屋都算），状态条/help 都给得出去。")
