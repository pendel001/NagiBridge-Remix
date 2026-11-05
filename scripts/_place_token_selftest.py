# -*- coding: utf-8 -*-
"""🧱 地名 token 钉子 —— 「印出来的名字，必须能直接粘回去」。2026-11-05

来历（恒）：
  「我们要不要允许ai查看当前地图的所有交互点和连接口，方便核对名字。
    不然老是死在对不准上，总该给人家一个入口。」
  「新入口的字眼要求是**直接能穿回 map go 能被认出来的**」「不合法就不要了」
  「直接替换一下现在的『可：』的那些不太合法的名字」

🩸 病根（这条钉子就是防它回来）：
  状态条原来印的是 `MAP_FEATURES` 的描述砍掉括号后的**残名**：
      "罗宾木匠店(买建筑/家具/升级房子)" → 印「罗宾木匠店」
  可那张图的真名是 **`ScienceHouse`** => AI 照着敲 = 当场死在「认不出」上。
  那时 `navigation.py` 里甚至写着一句"⚠️ 别照状态条 `🗺️ 可:` 那行抄名字"
  —— **等于承认那行是张假路牌**。

钉四条：
  ① `locations.legal_tokens_here()` 吐的**每一个** POI 名，都真的在 `locations.POI` 里
  ② 吐的**每一个**出口 target，都真的在 `locations.MAP_LINKS` 里（否则 BFS 走到死胡同）
  ③ 每张有登记的图，`here` 至少能给出 POI 或出口之一（不许空手）
  ④ 源码里**不许**再出现"把状态条描述砍括号当名字"的老写法（`f.split("(")[0] for f in _map_enum`）

跑法：`python scripts/_place_token_selftest.py`（退出码 0 = 全绿）
"""
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import locations  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def main():
    maps = set(locations.MAP_LINKS) | {v.get("map") for v in locations.POI.values() if v.get("map")}
    print(f"\n① 扫 {len(maps)} 张有登记的图：token 必须真在表里")

    bad_poi, bad_link, empty, dead_unlisted = [], [], [], []
    for m in sorted(maps):
        tok = locations.legal_tokens_here(m)
        for n, _x, _y in tok["poi"]:
            if n not in locations.POI:
                bad_poi.append((m, n))
        for tgt, _k, _tl in tok["links"]:
            if tgt not in locations.MAP_LINKS:
                bad_link.append((m, tgt))
        for tgt, _k, _tl in tok.get("dead") or []:
            if tgt not in getattr(locations, "LEAF_TARGETS", {}):
                dead_unlisted.append((m, tgt))
        if not tok["poi"] and not tok["links"]:
            empty.append(m)

    ck("① POI 名全部在 POI 表里", not bad_poi, str(bad_poi[:5]))
    ck("② 能发的出口 target 全部是 MAP_LINKS 的键", not bad_link, str(bad_link[:8]))
    ck("②b 进不去的地名都在 LEAF_TARGETS 里登记过（不登记就是新的假路牌）",
       not dead_unlisted, str(dead_unlisted[:8]))
    ck("③ 每张登记过的图都至少能给 POI 或出口", not empty, str(empty[:8]))

    print("\n④ 源码里不许再有「砍括号当名字」的老写法")
    src = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8", errors="replace").read()
    old = 'f.split("(")[0] for f in _map_enum'
    ck("④ 状态条不再把 MAP_FEATURES 描述砍括号当名字", old not in src)

    print("\n⑤ 新入口在不在")
    import nagi_mcp_server as M  # 安全：末尾有 __main__ 守卫（见记忆 python-only-fix-verify-without-restart）
    ck("⑤ 有 map_here()", hasattr(M, "map_here"))
    srv = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8", errors="replace").read()
    ck('⑤ map 域 dispatch 里有 "here"', '"here": map_here' in srv)
    ck("⑤ 认不出时指路 map here", "map here" in io.open(os.path.join(HERE, "navigation.py"),
                                                    encoding="utf-8", errors="replace").read())

    print("\n⑥ 真跑一次 map_here（stub 掉 current_location，不起游戏、不碰存档）")
    M.api.current_location = lambda: "Mountain"
    txt = M.map_here(full=True)
    print("     样例：")
    for _l in txt.split("\n")[:6]:
        print("       " + _l)
    ck("⑥ 输出里真给出了连通图名", "Mine" in txt or "AdventureGuild" in txt or "矿井" in txt)
    bad_tok = []
    for _line in txt.split("\n"):
        s = _line.strip()
        if not s.startswith("· "):
            continue
        tok = s[2:].split("  ")[0].strip()
        # 出口行的格式是「中文别名（EnglishKey）」——两种写法 `map go` 都认；只校验「名字」那半
        head = tok.split("（")[0].strip()
        if head and head not in locations.POI and head not in locations.MAP_LINKS \
                and head not in getattr(M.navigation, "SCENE_NAME_ALIAS", {}):
            bad_tok.append(head)
    ck("⑥ 印出来的每个词都能回填（POI / MAP_LINKS / 中文别名）", not bad_tok, str(bad_tok[:6]))
    # ⑦ 中文别名必须是最短的那个（恒：「印少的那个」）
    _al = [k for k, v in M.navigation.SCENE_NAME_ALIAS.items() if v == "AdventureGuild"]
    _lb = M._tok_label("AdventureGuild")
    ck("⑦ 挑的是最短中文别名、且比英文短",
       bool(_al) and _lb == sorted(_al, key=lambda s: (len(s), s))[0] and len(_lb) < len("AdventureGuild"),
       f"{_lb} / 候选={_al}")

    # ⑧ 全局地名目录：每个名字都能回填，且不重复
    _names = []
    for _g in ("室内", "室外"):          # ⚠️ 别传 ""（那已经包含两半，会重复计一遍）
        for _ln in M.map_names(_g).split("\n"):
            if ":" in _ln:
                _names += _ln.split(":", 1)[1].split()
    _bad_n = [n for n in _names if n not in locations.POI and n not in locations.MAP_LINKS
              and n not in M.navigation.SCENE_NAME_ALIAS]
    ck("⑧ map names 里每个名字都能回填", not _bad_n, str(_bad_n[:8]))
    ck("⑧b map names 没有重名", len(_names) == len(set(_names)),
       f"{len(_names)} 个 / 去重 {len(set(_names))}")

    # ⑨ 2026-11-05 恒：「还列相邻图嘛？只列入当前图 poi 和建筑算了」
    #    ⇒ `here` 里**不许**再出现出口行（`[warp]` / `[door]`）；想去别的图一律指路 `map names`
    ck("⑨ map here 不再列相邻图（无 [warp]/[door] 行）",
       "[warp]" not in txt and "[door]" not in txt)
    ck("⑨ map here 指路 map names", "map names" in txt)

    # ⑩ 建筑名：`here` 印的必须全部是 `navigation.BUILDING_KEYWORDS` 的键，且**只有一份表**
    M.api.current_location = lambda: "Farm"
    _farm = M.map_here(full=True)
    print("     Farm 样例：")
    for _l in _farm.split("\n"):
        if "🏗️" in _l:
            print("       " + _l)
    _bk = getattr(M.navigation, "BUILDING_KEYWORDS", {})
    ck("⑩ navigation.BUILDING_KEYWORDS 存在且非空", bool(_bk))
    _bline = [l for l in _farm.split("\n") if "🏗️" in l]
    ck("⑩ Farm 上 here 列出了建筑清单", bool(_bline))
    if _bline:
        _toks = _bline[0].split(":", 1)[1].split()
        ck("⑩ 每个建筑名都是 BUILDING_KEYWORDS 的键", all(t in _bk for t in _toks),
           str([t for t in _toks if t not in _bk]))
    # 唯一来源：`_resolve_place` 用的是同一个 dict 对象（不是另抄的副本）
    _nvs = io.open(os.path.join(HERE, "navigation.py"), encoding="utf-8", errors="replace").read()
    ck("⑩ `_resolve_place` 复用同一份表（KEYWORDS = BUILDING_KEYWORDS）",
       "KEYWORDS = BUILDING_KEYWORDS" in _nvs)

    # ⑪ nav_only：出口站格只给导航用，**不许进 AI 清单**（恒：「走过去触发不了任何事」）
    _nav = [n for n, v in locations.POI.items() if (v or {}).get("nav_only")]
    ck("⑪ POI 里有 nav_only 标注", len(_nav) > 0, f"{len(_nav)} 条")
    ck("⑪ nav_only 的条目仍在 POI 里（摘展示不删数据，导航还要用）", bool(_nav))
    _leak = []
    for _m in sorted(maps):
        for _n, _x, _y in locations.legal_tokens_here(_m)["poi"]:
            if _n in _nav:
                _leak.append((_m, _n))
    ck("⑪ nav_only 条目没有漏进 legal_tokens_here", not _leak, str(_leak[:5]))
    _btok = [l for l in _farm.split("\n") if l.strip().startswith("· ")]
    ck("⑪ Farm 的清单里不再有「农场上口/下口」",
       not any(("上口" in l or "下口" in l) for l in _btok),
       str([l.strip()[:40] for l in _btok][:6]))

    # ⑫ 同格去重（恒：「看看有没有重复注入些什么」）
    _dup = []
    for _m in sorted(maps):
        _seen_tile = {}
        for _n, _x, _y in locations.legal_tokens_here(_m)["poi"]:
            if (_x, _y) in _seen_tile:
                _dup.append((_m, _x, _y, _seen_tile[(_x, _y)], _n))
            _seen_tile[(_x, _y)] = _n
    ck("⑫ 同一格不再出现两个名字", not _dup, str(_dup[:5]))

    # ⑬ 同一栋楼三形态（Joja超市/废弃超市/电影院）必须按当前形态只留一条
    _town = {}
    for _f in ("jojamart", "abandoned", "theater"):
        _n = [n for n, _x, _y in locations.legal_tokens_here("Town", joja_form=_f)["poi"]
              if n in ("Joja超市(门口)", "废弃超市(门口)", "电影院(门口)")]
        _town[_f] = _n
    ck("⑬ 形态 jojamart ⇒ 只列 Joja超市(门口)", _town["jojamart"] == ["Joja超市(门口)"], str(_town))
    ck("⑬ 形态 theater ⇒ 只列 电影院(门口)", _town["theater"] == ["电影院(门口)"], str(_town))
    ck("⑬ 形态 abandoned ⇒ 只列 废弃超市(门口)", _town["abandoned"] == ["废弃超市(门口)"], str(_town))

    # ⑭ 日历门槛（2026-11-05 恒：「**候选前也检查一下 poi 的限制**，因为春12不是夜市啦，
    #    猪车就只可能是常规的猪车」）
    #    真机那单：`map go 猪车`（春12=周五）摊出 `夜市猪车` + `猪车` 两个候选逼人挑，
    #    可夜市的图春天根本不存在 ⇒ 只剩森林那辆时**不该再问**，直接走。
    _spring12 = locations.calendar_ctx({"season": "spring", "dayOfMonth": 12, "timeOfDay": 800})
    _spring10 = locations.calendar_ctx({"season": "spring", "dayOfMonth": 10, "timeOfDay": 800})
    _winter16 = locations.calendar_ctx({"season": "winter", "dayOfMonth": 16, "timeOfDay": 1900})
    _winter19_late = locations.calendar_ctx({"season": "winter", "dayOfMonth": 19, "timeOfDay": 2100})
    ck("⑭ weekday 算法对上真机（5日=周五、12日=周五）",
       locations.calendar_ctx({"dayOfMonth": 5}).get("weekday") == 4
       and locations.calendar_ctx({"dayOfMonth": 12}).get("weekday") == 4)
    ck("⑭ 春10 = 周三（夹具自检，别拿错日子验）", _spring10.get("weekday") == 2, str(_spring10))
    ck("⑭ POI_WHEN 的键都真在 POI 里（打错字 = 门槛永远不触发）",
       all(k in locations.POI for k in locations.POI_WHEN),
       str([k for k in locations.POI_WHEN if k not in locations.POI]))
    ck("⑭ 春12(周五)：森林猪车存在", locations.poi_available("猪车(旅行货车)", **_spring12)[0])
    ck("⑭ 春12：夜市猪车**不存在**（春不是冬）",
       not locations.poi_available("夜市猪车(旅行货车)", **_spring12)[0])
    ck("⑭ 春10(周三)：森林猪车不存在",
       not locations.poi_available("猪车(旅行货车)", **_spring10)[0])
    ck("⑭ 冬16：夜市猪车存在", locations.poi_available("夜市猪车(旅行货车)", **_winter16)[0])
    ck("⑭ 冬19(周五) 21:00：森林猪车判收摊",
       not locations.poi_available("猪车(旅行货车)", **_winter19_late)[0])
    ck("⑭ 没登记的 POI 一律放行（不误伤 278 条）",
       locations.poi_available("出货箱", **_spring10)[0])
    _forest12 = [n for n, _x, _y in locations.legal_tokens_here("Forest", when=_spring12)["poi"]]
    _forest10 = [n for n, _x, _y in locations.legal_tokens_here("Forest", when=_spring10)["poi"]]
    ck("⑭ Forest 春12 的清单里有森林猪车", "猪车(旅行货车)" in _forest12, str(_forest12))
    ck("⑭ Forest 春10(周三) 的清单里**没有**森林猪车",
       "猪车(旅行货车)" not in _forest10, str(_forest10))
    _nm12 = [n for n, _x, _y in locations.legal_tokens_here("BeachNightMarket", when=_spring12)["poi"]]
    _nm16 = [n for n, _x, _y in locations.legal_tokens_here("BeachNightMarket", when=_winter16)["poi"]]
    ck("⑭ 夜市(春12) 不列夜市猪车", "夜市猪车(旅行货车)" not in _nm12, str(_nm12))
    ck("⑭ 夜市(冬16) 列夜市猪车", "夜市猪车(旅行货车)" in _nm16, str(_nm16))

    import nagi_mcp_server as M       # noqa: E402  （先它：navigation 的宿主注入在它末尾）
    import navigation as N            # noqa: E402
    ck("⑭ `map go 猪车`（春12）不再报两个候选", N._poi_ambiguous("猪车", when=_spring12) == [],
       str(N._poi_ambiguous("猪车", when=_spring12)))
    ck("⑭ 不传 when 仍按老规矩报歧义（判据没被顺手放宽）",
       len(N._poi_ambiguous("猪车")) == 2, str(N._poi_ambiguous("猪车")))
    _nt_wed = N._not_today("猪车(旅行货车)", _spring10)
    ck("⑭ 周三点全名 ⇒ 拦住并说清哪天有", bool(_nt_wed) and "周五" in _nt_wed, _nt_wed)
    ck("⑭ 春12 点全名 ⇒ 不拦", N._not_today("猪车(旅行货车)", _spring12) == "",
       N._not_today("猪车(旅行货车)", _spring12))
    ck("⑭ 春天点夜市猪车 ⇒ 拦住", "冬" in N._not_today("夜市猪车(旅行货车)", _spring12))
    ck("⑭ 没登记的名字一律不拦", N._not_today("出货箱", _spring10) == "")
    # ⑭b 唯一命中要**认得出来**（否则 `map go 猪车` 会从"列 2 个候选"掉成"认不出"，比原来更差）
    ck("⑭b `map go 猪车`（春12）认成森林那辆",
       N._poi_single("猪车", when=_spring12) == "猪车(旅行货车)",
       str(N._poi_single("猪车", when=_spring12)))
    ck("⑭b 周三：一个都不存在 ⇒ 不认（交给 _not_today 说清哪天有）",
       N._poi_single("猪车", when=_spring10) == "")
    ck("⑭b 全名不走这条（精确名优先）",
       N._poi_single("猪车(旅行货车)", when=_spring12) == "")
    ck("⑭b 地图名/别名**不许**被半截名劫走（农场 ≠ 农场洞穴）",
       N._poi_single("农场") == "" and N._poi_single("镇") == "" and N._poi_single("森林") == "")
    ck("⑭b 唯一命中时照全名继续导航（源码钉：`_one = _poi_single(...)` + `destination = _one`）",
       "_one = _poi_single(destination, when=_today_ctx())" in _nvs
       and "destination = _one" in _nvs)

    _svs = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8", errors="replace").read()
    ck("⑭ 状态条把日历传进了 legal_tokens_here",
       "legal_tokens_here(loc_name, when=_when)" in _svs)
    ck("⑭ `map here` 也传了 when",
       "legal_tokens_here(cur, joja_form=_jf, when=_when)" in _svs)
    ck("⑭ 状态条那行猪车判据已改用 poi_available（不再自己算 (day-1)%7）",
       'poi_available("猪车(旅行货车)", **_when)' in _svs)
    # ⑭c 第 4 处副本：`_traveling_cart_hint`（原来也自己算星期/时段）——必须一起改读同一张表
    ck("⑭c 猪车提醒也改读同一张表（不再留第 2 份星期判据）",
       'poi_available("猪车(旅行货车)",' in _svs.split("def _traveling_cart_hint")[1].split("\ndef ")[0])
    _TRAVEL_CART_KEY = M._TRAVEL_CART_KEY
    _saved = _TRAVEL_CART_KEY["last"]
    _TRAVEL_CART_KEY["last"] = None
    ck("⑭c 周三不给猪车提醒", M._traveling_cart_hint("spring", 10, 800) == "")
    _TRAVEL_CART_KEY["last"] = None
    ck("⑭c 春12 周五 8 点给提醒", "旅行猪车" in M._traveling_cart_hint("spring", 12, 800))
    _TRAVEL_CART_KEY["last"] = None
    ck("⑭c 周五 21 点收摊不给", M._traveling_cart_hint("winter", 19, 2100) == "")
    _TRAVEL_CART_KEY["last"] = _saved

    # ⑮ 输出里**不许写死角色名**（2026-11-05 恒真机：「还显示轮回什么什么的，**那是测试用的角色**，
    #    不知道为什么出现了这一串提示语」）
    #    病根：`find_npc` 的两条尾注写死了「轮回自己这端」——那是早期测试档的角色名，
    #    换个存档就在回执里冒出一句跟当前角色无关的话 ⇒ 一律**现读现用**。
    _legacy = "轮" + "回"          # 拆开写：免得这条钉子自己命中自己
    _dirty = []
    for _pn, _pv in (locations.POI or {}).items():
        for _k, _v in (_pv or {}).items():
            if isinstance(_v, str) and _legacy in _v:
                _dirty.append(f"{_pn}.{_k}")
    ck("⑮ POI 的说明文字里没有写死的角色名", not _dirty, str(_dirty[:5]))
    ck("⑮ find_npc 的尾注不再写死角色名",
       (_legacy + "自己这端") not in _svs and ("你(" + _legacy + ")") not in _svs)
    # 2026-11-05 晚（恒：「什么都不用说……报个位置就完了」）⇒ 那套"两端差异"尾注整段撤了，
    # 这里改成钉**它没回来**：既不许出现写死的角色名，也不许再提"这端/滞留旧位置"那一串。
    _fn = _svs.split("def find_npc(")[1].split("\ndef ")[0]
    ck("⑮ find_npc 里没有写死的角色名", _legacy not in _fn)
    ck("⑮ find_npc 不再印「两端差异」那套（这端/滞留旧位置）",
       "这端" not in _fn and "滞留旧位置" not in _fn)
    ck("⑮ find_npc 不印台词（恒：「不需要把台词数据……翻出来一大串」）",
       "d.get(\"dialogue\")" not in _fn and "🗨️" not in _fn)
    ck("⑮ find_npc 带上了「今天聊没聊/送没送礼」（`_npc_today_marks`）",
       "_npc_today_marks(" in _fn)
    _bms = io.open(os.path.join(HERE, "bomb_mine.py"), encoding="utf-8", errors="replace").read()
    ck("⑮ 炸矿的死亡播报不再写死角色名", (_legacy + "在矿里死了") not in _bms)
    # （"查位置而已，没跟他说话"那条 2026-11-05 晚已退役：恒改口「什么都不用说，报个位置就完了」
    #   ⇒ 那句解释本身也是 token。现在的验收在 `_npc_where_selftest.py`：一行一个人、不许有台词。）

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 名字对不准的根就在这里，**修数据或修输出，别改这条钉子**。")
        sys.exit(1)
    print("✅ 地名 token 全绿：印出来的每个词都能直接回填。")
    sys.exit(0)


if __name__ == "__main__":
    main()
