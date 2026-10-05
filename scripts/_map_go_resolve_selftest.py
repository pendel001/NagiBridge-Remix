"""🎯 `map go` 的**目的地解析**：精确名优先 + 半截名不猜 —— 纯 Python 自验（不起服务、不碰游戏）。2026-10-05 补30

真机现场（恒的验收子代理 2026-10-05）：
    `map go 姜岛小屋(门内六人房)`（`map lookup` 确认这个 POI **存在**）
    ⇒ 它走到了**自家 Cabin 门口 `Farm(55,12)`**，还回「🏠 **已到自家小屋门口**」。
    换 `map go IslandFarmHouse` 才正常。
根因：`map_go` 开头那句自家小屋拦截用的是**子串**判据（`"小屋" in 目的地`）⇒
    `locations.py:432` 里那条**全名** POI 被"小屋"两个字劫持 ⇒ **认错地方还报"到了"**（假成功）。

判据（钉死三条）：
  ① **精确名优先**：全名（POI 键）/别名精确命中 ⇒ 直接走它，**不许**被子串赢走；
  ② **半截名多候选 ⇒ 不许自己挑**：报错 + 把候选列出来（宁报错别兜底）；
  ③ 到达声明必须与被选中的目标**一致**（这里钉 `_NAV_LAST` 的落点图 + 不许出现"自家门口"那句）。

⚠️ 只验 Python 这一半（解析/分派/文案）；真机那半见 CHANGELOG `203z补30` §A。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # 先 import 它：navigation 的宿主注入发生在它末尾
import navigation as N

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """只喂 `map_go` 开头那几步读的东西（这些人都在 IslandWest 农场）。"""
    def state(self, **kw):
        return {"location": {"name": "IslandWest"}, "player": {"x": 77, "y": 40},
                "time": {}, "inventory": []}

    def _get(self, ep, params=None):
        return {"ok": True}

    def _post(self, ep, data=None):
        return {"ok": True}

    def player_tile(self):
        return (77, 40)


HOME_MARK = "【HOME·自家小屋门口】"
GATE_MARK = "【GATE·再往后就该导航了】"
_PATCH = ("api", "_with_state", "_nav_home_door", "_grandpa_shrine_gate",
          "_festival_poi_active", "go_to")
_real = {n: getattr(N, n) for n in _PATCH}
_NAV_LAST_BAK = dict(N._NAV_LAST)
try:
    N.api = FakeApi()
    N._with_state = lambda s: s
    N._nav_home_door = lambda: HOME_MARK
    # 🚏 把"再往后"截在这里：过了头两道闸才会走到它（用它证明"这一发被放行了"）
    N._grandpa_shrine_gate = lambda dest: GATE_MARK
    N._festival_poi_active = lambda *a, **k: True
    N.go_to = lambda s: f"【GO_TO {s}】"

    print("\n① 「自家小屋」只认**整串**（恒 2026-09-05 的语义原样保留）")
    for _w in ("小屋", "去小屋", "进小屋", "回小屋", "我的小屋", "自己小屋", "我家", "我的家",
               "家", "cabin", "小屋(床)"):
        ck(f"`{_w}` ⇒ 算自家小屋", N._is_home_word(_w) is True, str(N._is_home_word(_w)))
    print("\n①b 带修饰的**全名**一律不算（真机 A 那一条就是被这两个字劫持的）")
    for _w in ("姜岛小屋(门内六人房)", "姜岛小屋(门口)", "姜岛小屋", "雷欧小屋(内)", "雷欧小屋(门口)",
               "女巫小屋(门口)", "女巫小屋"):
        ck(f"`{_w}` ⇒ **不算**自家小屋", N._is_home_word(_w) is False, str(N._is_home_word(_w)))

    print("\n② 恒 09-05 的老语义没被碰坏：`map go 小屋` 照旧回家门口")
    _out = N.map_go("小屋")
    ck("`小屋` ⇒ 走回家门口那条（【HOME】）", _out == HOME_MARK, _out)

    print("\n③ 🎯 **精确名优先**：全名 POI 不再被「小屋」子串劫持（真机 A 的哨兵钉）")
    N._NAV_LAST.clear()
    _out2 = N.map_go("姜岛小屋(门内六人房)")
    ck("③ **不**走自家小屋（旧代码这里回「已到自家小屋门口」）", _out2 != HOME_MARK, _out2)
    ck("③ …被**放行**到下一道闸（证明没被头两道闸拦下）", _out2 == GATE_MARK, _out2)
    ck("③ …到达声明与被选中的目标**一致**：`_NAV_LAST` 的落点图 = IslandFarmHouse",
       (N._NAV_LAST.get("loc") == "IslandFarmHouse"), str(N._NAV_LAST))

    print("\n④ 半截名多候选 ⇒ **报错 + 列候选**，绝不自己挑一个")
    N._NAV_LAST.clear()
    _out3 = N.map_go("姜岛小屋")
    _amb = N._poi_ambiguous("姜岛小屋")
    ck("④ 报错（不是【HOME】、也不是被放行）", _out3 not in (HOME_MARK, GATE_MARK), _out3)
    ck("④ …点明**有几个候选**", "2 个候选" in _out3, _out3)
    ck("④ …把两个候选**都列出来**",
       "姜岛小屋(门口)" in _out3 and "姜岛小屋(门内六人房)" in _out3, _out3)
    ck("④ …并给下一步（照抄全名 / 直接写图）",
       f"map ops=go {_amb[0]}" in _out3
       and f"map ops=go {M.locations.POI[_amb[0]].get('map')}" in _out3, _out3)
    ck("④ …而且**没动** `_NAV_LAST`（没挑任何一个当目标）", not N._NAV_LAST, str(N._NAV_LAST))

    print("\n⑤ 精确**别名**/精确 **POI 键** ⇒ 一律不进歧义闸（别把能用的路拦死）")
    for _q, _why in (("鱼店", "精确别名 → FishShop"),
                     ("出货箱", "精确 POI 键（Farm 的出货箱）"),
                     ("女巫小屋", "精确别名 → WitchHut"),
                     ("牧场", "精确别名 → AnimalShop")):
        _o = N.map_go(_q)
        ck(f"⑤ `{_q}`（{_why}）⇒ 放行到下一道闸", _o == GATE_MARK, _o)

    print("\n⑥ `_poi_ambiguous` 判据本身：只在「多候选 **且** 落点不在同一张图」时报")
    _c6 = N._poi_ambiguous("姜岛小屋")
    ck("⑥ `姜岛小屋` ⇒ 2 个候选（IslandWest / IslandFarmHouse）",
       len(_c6) == 2 and set(_c6) == {"姜岛小屋(门口)", "姜岛小屋(门内六人房)"}, str(_c6))
    ck("⑥ 全名 ⇒ 空（精确名优先）", N._poi_ambiguous("姜岛小屋(门内六人房)") == [], "")
    ck("⑥ 别名 ⇒ 空", N._poi_ambiguous("鱼店") == [], "")
    ck("⑥ MAP_LINKS 键（含大小写/空格归一）⇒ 空",
       N._poi_ambiguous("farm") == [] and N._poi_ambiguous("Skull Cave") == [], "")
    ck("⑥ 认不出的怪名字 ⇒ 空（交给下游既有逻辑报「知识库没有」）",
       N._poi_ambiguous("不存在的地方xyz") == [], "")

    print("\n⑦ 源码钉：`map_go` 里不许再出现**子串**判据（防回归）")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "navigation.py"),
                encoding="utf-8").read()
    _ia = _src.index("def map_go(")
    _ib = _src.index("\ndef ", _ia + 10)
    _body = _src[_ia:_ib]
    # ⚠️ 只看**代码行**（我这批的解释注释里故意引用了旧判据原样，不能把它自己当病）
    _code = "\n".join(l for l in _body.splitlines() if not l.strip().startswith("#"))
    ck("⑦ `map_go` 走的是整串判据 `_is_home_word(...)`", "_is_home_word(destination)" in _code, "")
    ck('⑦ …且**没有**裸子串 `"小屋" in`（真机 A 的病根）', '"小屋" in' not in _code, "")
    ck("⑦ …且歧义闸在（`_poi_ambiguous`）", "_poi_ambiguous(destination)" in _code, "")
finally:
    for k, v in _real.items():
        setattr(N, k, v)
    N._NAV_LAST.clear()
    N._NAV_LAST.update(_NAV_LAST_BAK)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
