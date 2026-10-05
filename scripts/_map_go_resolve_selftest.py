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
    # ⚠️ 2026-10-05（真机 B 精修）：`map_go` 现在是**薄壳**（只贴"按短名理解"那句声明），
    #    大身板搬到了 `_map_go_body` ⇒ 这几条钉子必须钉**身板**，钉壳子会全假过。
    _ia = _src.index("def _map_go_body(")
    _ib = _src.index("\ndef ", _ia + 10)
    _body = _src[_ia:_ib]
    # ⚠️ 只看**代码行**（我这批的解释注释里故意引用了旧判据原样，不能把它自己当病）
    _code = "\n".join(l for l in _body.splitlines() if not l.strip().startswith("#"))
    ck("⑦ `map_go` 身板走的是整串判据 `_is_home_word(...)`", "_is_home_word(destination)" in _code, "")
    ck('⑦ …且**没有**裸子串 `"小屋" in`（真机 A 的病根）', '"小屋" in' not in _code, "")
    ck("⑦ …且歧义闸在（`_poi_ambiguous`）", "_poi_ambiguous(destination)" in _code, "")
    ck("⑦ …且**变体名闸**在（`_variant_name_error`）", "_variant_name_error(destination)" in _code, "")
    # 薄壳本身的钉子：贴声明 + 歧义闸那种不贴（`_poi_ambiguous` 守卫）
    _wa = _src.index("def map_go(")
    _wb = _src.index("\ndef ", _wa + 10)
    _wcode = "\n".join(l for l in _src[_wa:_wb].splitlines() if not l.strip().startswith("#"))
    ck("⑦b 薄壳 `map_go` 调身板 `_map_go_body(...)` 并贴 `_variant_shortname_note(...)`",
       "_map_go_body(destination, npc)" in _wcode and "_variant_shortname_note(destination)" in _wcode, "")
    ck("⑦b …且歧义闸那种**不贴**（`not _poi_ambiguous(destination)`）",
       "not _poi_ambiguous(destination)" in _wcode, "")

    # ══════════════════════════════════════════════════════════════════════════
    # ⑧⑨⑩⑪ 2026-10-05 真机 B：**不许静默改写目的地**（"别名 ⊂ 输入"那条）
    # ══════════════════════════════════════════════════════════════════════════
    _CALLS = []
    N.go_to = lambda s: (_CALLS.append(s), f"【GO_TO {s}】")[1]     # 记录"角色**动没动**"

    print("\n⑧ 病样本（真机 B）：`姜岛小屋(门内六房)`（真名带\"人\"字：`locations.py:432`）")
    _CALLS.clear(); N._NAV_LAST.clear()
    _out8 = N.map_go("姜岛小屋(门内六房)")
    ck("⑧ 回执含「认不出「姜岛小屋(门内六房)」这个地点」",
       "认不出「姜岛小屋(门内六房)」这个地点" in _out8, _out8)
    ck("⑧ 候选里有**真名** `姜岛小屋(门内六人房)`", "姜岛小屋(门内六人房)" in _out8, _out8)
    ck("⑧ 候选里有那个短别名原本指向的目标（短名「姜岛」→ IslandSouth）",
       "短名「姜岛」" in _out8 and "IslandSouth" in _out8, _out8)
    ck("⑧ 给了下一步（`map lookup`）", "map lookup" in _out8, _out8)
    ck("⑧ **没有**任何「到达/已到」字样", "到达" not in _out8 and "已到" not in _out8, _out8)
    ck("⑧ 没被放行到下游（不是【GATE】/【HOME】）", _out8 not in (GATE_MARK, HOME_MARK), _out8)
    ck("⑧ **没动角色**（`go_to` 一次都没调）", not _CALLS, str(_CALLS))
    ck("⑧ 也没挑目标（`_NAV_LAST` 空）", not N._NAV_LAST, str(N._NAV_LAST))
    ck("⑧ 判据本身：`_alias_variant` 分类 = longer",
       N._alias_variant("姜岛小屋(门内六房)")[0] == "longer",
       str(N._alias_variant("姜岛小屋(门内六房)")))
    ck("⑧ 判据本身：`_resolve_scene_name` **不再**把它当成 IslandSouth",
       N._resolve_scene_name("姜岛小屋(门内六房)") != "IslandSouth",
       N._resolve_scene_name("姜岛小屋(门内六房)"))
    ck("⑧ 判据本身：相似度 0.952 ≥ 阈值 0.95（所以走**报错**，不是「按短名」）",
       N._variant_sim_best("姜岛小屋(门内六房)")[0] >= N._VARIANT_SIM_THRESHOLD,
       str(N._variant_sim_best("姜岛小屋(门内六房)")))
    ck("⑧ **不贴**「按短名」声明（报错回执里不许自相矛盾）",
       "按短名" not in _out8 and N._variant_shortname_note("姜岛小屋(门内六房)") == "", _out8)

    print("\n⑨ 回归：短名/精确全名/口语前缀——**一条都不许改坏**")
    for _q in ("姜岛", "岛", "鱼店", "农场"):
        _CALLS.clear()
        _o = N.map_go(_q)
        ck(f"⑨ 短名 `{_q}` 照旧放行（精确别名 ⇒ 下一道闸）", _o == GATE_MARK, _o)
    ck("⑨ 短名 `小屋` 照旧 = **自家小屋门口**（恒 2026-09-05 语义）",
       N.map_go("小屋") == HOME_MARK, N.map_go("小屋"))
    for _q in ("去姜岛", "回姜岛", "去铁路"):
        _CALLS.clear()
        _o = N.map_go(_q)
        ck(f"⑨ 口语前缀 `{_q}` 照旧认得出（剥前缀后精确命中）", _o == GATE_MARK, _o)
    _CALLS.clear()
    _o9 = N.map_go("姜岛小屋(门内六人房)")
    ck("⑨ 精确全名 `姜岛小屋(门内六人房)` 照旧直达（不被变体闸拦）", _o9 == GATE_MARK, _o9)
    ck("⑨ …单候选半截名照旧认（`姜岛农` ⇒ IslandWest）",
       N._resolve_scene_name("姜岛农") == "IslandWest", N._resolve_scene_name("姜岛农"))
    ck("⑨ …单字精确别名照旧（`镇` ⇒ Town；单字输入的旧口径本批不动）",
       N._resolve_scene_name("镇") == "Town", N._resolve_scene_name("镇"))
    # 判据①的**穷举回归**：表里所有名字（POI/MAP_LINKS/别名）一个都不许被变体闸拦（精确名优先）
    _allnames = list(M.locations.POI) + list(M.locations.MAP_LINKS) + list(N.SCENE_NAME_ALIAS)
    _bad = [k for k in _allnames if N._variant_name_error(k) != ""]
    ck(f"⑨b 表里**全部** {len(_allnames)} 个名字都不被变体闸拦（精确名优先）", not _bad, str(_bad[:8]))
    _badn = [k for k in _allnames if N._variant_shortname_note(k) != ""]
    ck("⑨b …也**都不**贴「按短名」声明（精确名 = 直达，不用解释）", not _badn, str(_badn[:8]))
    # 真机 selftest（需窗口，本文件不跑）里那些**写死的目的地**也得照样放行
    for _q in ("镇鲶鱼钓点", "ArchaeologyHouse", "畜棚", "火山入口", "Mine"):
        ck(f"⑨b `{_q}`（需窗口的 `_map_go_arrive_selftest` 写死的输入）⇒ 放行",
           N._variant_name_error(_q) == "", N._variant_name_error(_q))

    print("\n⑩ 歧义（判据②：输入更短、多候选且目标不在一张图）⇒ 报错 + 列候选，绝不静默选一个")
    _CALLS.clear(); N._NAV_LAST.clear()
    _out10 = N.map_go("姜岛小屋")
    ck("⑩a 真名夹具（两条 POI：IslandWest / IslandFarmHouse）⇒ 报「2 个候选」并列两条全名",
       "2 个候选" in _out10 and "姜岛小屋(门口)" in _out10 and "姜岛小屋(门内六人房)" in _out10, _out10)
    ck("⑩a …没动角色（`go_to` 一次都没调）", not _CALLS, str(_CALLS))
    ck("⑩a …也没挑目标（`_NAV_LAST` 空）", not N._NAV_LAST, str(N._NAV_LAST))
    ck("⑩a …**不贴**「按短名」声明（歧义闸不替谁挑，贴了就自相矛盾）",
       "按短名" not in _out10 and N._variant_shortname_note("姜岛小屋") != "", _out10)
    _CALLS.clear()
    _out10b = N.map_go("山入口")
    ck("⑩b 别名方向多候选（火山入口 VolcanoEntrance / 火山入口区 IslandNorth）⇒ 报错并列两个候选",
       _out10b not in (GATE_MARK, HOME_MARK)
       and "火山入口" in _out10b and "火山入口区" in _out10b
       and "VolcanoEntrance" in _out10b and "IslandNorth" in _out10b, _out10b)
    ck("⑩b …给下一步（`map lookup`）", "map lookup" in _out10b, _out10b)
    ck("⑩b …没动角色（`go_to` 一次都没调）", not _CALLS, str(_CALLS))
    ck("⑩b 判据本身：`_resolve_scene_name('山入口')` **不猜**（返回原值）",
       N._resolve_scene_name("山入口") == "山入口", N._resolve_scene_name("山入口"))
    _CALLS.clear()
    _out10b2 = N.map_go("巴士")     # 这条**先**被 补30 的 POI 歧义闸拦（POI 里 6 条含"巴士"）—— 同样不静默选
    ck("⑩b' `巴士` 先被 补30 的 POI 歧义闸拦（列 6 条候选）⇒ 也**不静默选一个**",
       _out10b2 not in (GATE_MARK, HOME_MARK) and "个候选" in _out10b2 and "沙漠(巴士站)" in _out10b2, _out10b2)
    ck("⑩b' …没动角色（`go_to` 一次都没调）", not _CALLS, str(_CALLS))

    print("\n⑩c 合成夹具：往 POI 表里临时塞两条（不同图）⇒ 子串命中仍然报歧义")
    _FAKE = {"测试点甲(内)": {"map": "Farm", "pos": (1, 1), "note": "自验夹具"},
             "测试点乙(内)": {"map": "Town", "pos": (2, 2), "note": "自验夹具"}}
    _CALLS.clear(); N._NAV_LAST.clear()
    try:
        M.locations.POI.update(_FAKE)
        ck("⑩c `_poi_ambiguous('测试点')` = 两个候选",
           set(N._poi_ambiguous("测试点")) == set(_FAKE), str(N._poi_ambiguous("测试点")))
        _out10c = N.map_go("测试点")
        ck("⑩c 回执报「2 个候选」并把两条全名列出来",
           "2 个候选" in _out10c and "测试点甲(内)" in _out10c and "测试点乙(内)" in _out10c, _out10c)
        ck("⑩c …没动角色（`go_to` 一次都没调）", not _CALLS, str(_CALLS))
    finally:
        for _k in _FAKE:
            M.locations.POI.pop(_k, None)

    print("\n⑪ 源码钉：`_resolve_scene_name` 里「别名 ⊂ 输入 ⇒ 悄悄选中」那半条**必须消失**")
    _ja = _src.index("def _resolve_scene_name(")
    _jb = _src.index("\ndef ", _ja + 10)
    _jraw = _src[_ja:_jb]
    # ⚠️ docstring 里**故意引用了旧判据原样**（"alias in s"）来说明这次删了什么 ⇒ 先摘掉 docstring 再看代码
    _q1 = _jraw.index('"""')
    _q2 = _jraw.index('"""', _q1 + 3)
    _jraw = _jraw[:_q1] + _jraw[_q2 + 3:]
    _jcode = "\n".join(l for l in _jraw.splitlines() if not l.strip().startswith("#"))
    ck("⑪ …「输入 ⊂ 别名」（`s in a`）那半条照旧保留", "s in a" in _jcode, "")
    ck("⑪ …且多候选分叉时**返回原值不猜**（`len({k for _, k in _cands}) >= 2`）",
       "len({k for _, k in _cands}) >= 2" in _jcode, "")
    # ⚠️ 2026-10-05 精修后语义变了：方向 B 不是"删掉"，而是"**上了相似度闸**"——
    #    像真名错字（≥ 阈值）⇒ 原样返回交门口报错；不像 ⇒ 按最长短名解析（回执由薄壳声明）。
    #    ⇒ 钉子照实改成"方向 B 必须带闸"，不许再钉"`a in s` 必须消失"（那是上一版的判据）。
    ck("⑪ …方向 B（`a in s`）现在**必须**带相似度闸",
       "a in s" in _jcode and ">= _VARIANT_SIM_THRESHOLD" in _jcode and "return s" in _jcode, "")
    ck("⑪ …且没有旧的无条件形态 `if alias in s or s in alias`",
       "if alias in s or s in alias" not in _jcode, "")
    ck("⑪ …阈值常量 = 0.95 且两处（闸/声明）都用它（同一把尺子）",
       N._VARIANT_SIM_THRESHOLD == 0.95
       and "_sim < _VARIANT_SIM_THRESHOLD" in _src
       and "_variant_sim_best(q)[0] >= _VARIANT_SIM_THRESHOLD" in _src,
       str(N._VARIANT_SIM_THRESHOLD))

    print("\n⑫ 真机**惯用名回归**（恒钦定：这四条不许报错，但回执必须**说**按短名）")
    for _q, _sc, _sn in (("罗宾木匠店", "ScienceHouse", "木匠店"),
                         ("威利鱼店", "FishShop", "鱼店"),
                         ("皮埃尔店", "SeedShop", "皮埃尔"),
                         ("皮埃尔店(柜台)", "SeedShop", "皮埃尔")):
        _CALLS.clear()
        _o12 = N.map_go(_q)
        ck(f"⑫ `{_q}` ⇒ **不报错**（变体闸放行）", N._variant_name_error(_q) == "", N._variant_name_error(_q))
        ck(f"⑫ …回执**必须出现「按短名」**且指出「{_sn}」→ {_sc}",
           "按短名" in _o12 and f"「{_sn}」" in _o12 and _sc in _o12, _o12)
        ck(f"⑫ …声明的目标 = `_resolve_scene_name` 真解析的目标（{_sc}，同一口径）",
           N._resolve_scene_name(_q) == _sc and GATE_MARK in _o12, f"{N._resolve_scene_name(_q)} / {_o12}")
    _CALLS.clear()
    _o12b = N.map_go("姜岛农")      # 两头都沾（`姜岛` ⊂ 它 ⊂ `姜岛农场`）：老口径取**更长命中** ⇒ IslandWest，且**不用**声明
    ck("⑫b `姜岛农`（两头都沾）⇒ 取更长命中 IslandWest，且**不贴**「按短名」",
       N._resolve_scene_name("姜岛农") == "IslandWest" and "按短名" not in _o12b
       and N._alias_variant("姜岛农")[0] == "shorter", _o12b)

    print("\n⑬ 合成近似名夹具：『比真名少一个字』⇒ 必须走**报错**分支（宁报错别兜底）")
    _FQ = "农场自验房(门内六房)"          # = 下面那条真名删掉一个"人"字
    _FAKE2 = {"农场自验房(门内六人房)": {"map": "Farm", "pos": (1, 1), "note": "自验夹具"}}
    _CALLS.clear(); N._NAV_LAST.clear()
    try:
        M.locations.POI.update(_FAKE2)
        _sim13 = N._variant_sim_best(_FQ)
        ck(f"⑬ 与夹具真名的相似度 {_sim13[0]:.3f} ≥ {N._VARIANT_SIM_THRESHOLD}",
           _sim13[0] >= N._VARIANT_SIM_THRESHOLD, str(_sim13))
        _o13 = N.map_go(_FQ)
        ck("⑬ 回执是**报错**（认不出）且候选里有那条真名",
           "认不出" in _o13 and "农场自验房(门内六人房)" in _o13, _o13)
        ck("⑬ …**不贴**「按短名」", "按短名" not in _o13, _o13)
        ck("⑬ …没被放行到下游（不是【GATE】/【HOME】）", _o13 not in (GATE_MARK, HOME_MARK), _o13)
        ck("⑬ **没动角色**（`go_to` 一次都没调）", not _CALLS, str(_CALLS))
    finally:
        for _k in _FAKE2:
            M.locations.POI.pop(_k, None)

    print("\n⑭ 精确名回归：四条真名照旧**直达**，一个「按短名」都不许冒出来")
    for _q in ("姜岛小屋(门内六人房)", "木匠商店(门外)", "鱼店(门口)", "皮埃尔商店(求助布告栏)"):
        _CALLS.clear()
        _o14 = N.map_go(_q)
        ck(f"⑭ `{_q}` ⇒ 放行到下一道闸（不报错）", _o14 == GATE_MARK, _o14)
        ck("⑭ …**没有**「按短名」字样", "按短名" not in _o14, _o14)
        ck("⑭ …判据本身：精确名 ⇒ 闸/声明都不动它（`_variant_name_error`/`_variant_shortname_note` 皆空）",
           N._variant_name_error(_q) == "" and N._variant_shortname_note(_q) == "", "")

finally:
    for k, v in _real.items():
        setattr(N, k, v)
    N._NAV_LAST.clear()
    N._NAV_LAST.update(_NAV_LAST_BAK)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
