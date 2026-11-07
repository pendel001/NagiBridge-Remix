# -*- coding: utf-8 -*-
"""🏷️ 投出货箱要认**品质词** —— 纯 Python 自验（离线打桩 `_post`，不碰游戏、不发 HTTP）。

## 恒原话
> 「ai 刚刚想往箱子里放**指定品质的鲶鱼**，但是不认。建议**智能或者选项存取的时候无所谓**，
>  但**指定这么传的时候认**。金/银/铱 字眼 + 物品」

## 根因
  `sell_to_bin()` 只把**名字**换成内部名（`_internal_item_name`），**完全不解析星级**：
  `menu(ops="bin", name="铱星鲶鱼")` ⇒ 原样发 `/sell {name:"铱星鲶鱼"}` ⇒ 背包里没有这个名字
  ⇒ 回执「背包里没有「铱星鲶鱼」能投」；而 C# `/sell` 也**没有** `quality` 参数、
  同名不同星级的几摞根本分不开（它按名字把**每一摞**都投进去）。

## 改法（两边都写；**不传 quality 时一个字都不变**）
  · Python：`_split_quality_prefix()` 把「<品质词>+物品名」拆成 `(quality, 物品名)`
    （0 普通 / 1 银 / 2 金 / 4 铱 —— `Item.cs:308`，3 不存在），
    再交给既有的 `_internal_item_name()` 换内部名；**只有点名了星级**才在 body 里加 `quality`。
  · C#：`HandleSell` 加**可选** `quality`（`GetParamOr(p, "quality", -1)`），
    `wantQuality >= 0` 时才要求 `Object.Quality == wantQuality`；不传 ⇒ 老行为。
  · 出货箱**取不回来** ⇒ 两个安全闸：① 能力位 `sell_quality` 不在（老 DLL 会忽略 quality、
    把同名各星级一起投光）⇒ 这一下**不投**，如实说"要重开游戏上新 DLL"；
    ② 背包里**没有**那一档（只有普通/银星的鲶鱼）⇒ **不投**，回执说人话。

## 钉死这些条
  ① `铱星鲶鱼` ⇒ `/sell` 收到 `{"name": "Catfish", "quality": 4}`（dict 完全相等：不许夹别的键）
  ② `金鲶鱼`⇒2 · `银星鲶鱼`⇒1 · `银色/银鲶鱼`⇒1 · `普通鲶鱼`⇒0
  ③ `鲶鱼`（不带星级）⇒ `{"name": "Catfish"}`，**一个 quality 键都没有**
  ④ 英文 `iridium/gold/silver catfish` 同样认
  ⑤ 物品名里自带「金/铱」的（`金枪鱼` / `铱锭`）⇒ **不拆**，也不带 quality
  ⑥ 没那一档（只有普通/银星的鲶鱼，要铱星）⇒ **不打 `/sell`**，回执说「只有…没有铱星的」
  ⑦ 老 DLL（没有 `sell_quality` 能力位）+ 指定星级 ⇒ **不打 `/sell`**，如实说"重开游戏上新 DLL"
  ⑧ 不带星级时**不受能力位影响**（老行为照旧：中文名换内部名就发）
  ⑨ C# 源码钉：`quality` 是**可选参数**（默认 -1）、只在 `wantQuality >= 0` 时才比较、
     能力位 `sell_quality` 有登记
  ⑩ Python 源码钉：`sell_to_bin` 真的走 `_split_quality_prefix`，且 `quality` 只在
     `_want_q is not None` 时进 body；名字判据**复用** `_internal_item_name`（没另起一份）

跑法：`python -X utf8 scripts/_bin_quality_selftest.py`（退出码 0 = 全绿）
"""
import ast
import inspect
import io
import os
import re
import sys
import types

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import nagi_mcp_server as M       # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 真机形状的背包（`/state.inventory` 每件都带 quality：0 普通/1 银/2 金/4 铱）──
def _cat(q, n):
    return {"name": "Catfish", "displayName": "鲶鱼", "stack": n, "quality": q, "catNum": -4}


INV_ALL = [
    {"name": "Axe", "displayName": "斧头", "stack": 1, "quality": 0, "catNum": -99},
    _cat(0, 3), _cat(1, 2), _cat(2, 4), _cat(4, 5),
    {"name": "Tuna", "displayName": "金枪鱼", "stack": 2, "quality": 0, "catNum": -4},
    {"name": "Iridium Bar", "displayName": "铱锭", "stack": 1, "quality": 0, "catNum": -15},
]
# 只有普通 + 银星两摞（用来钉"没那一档"）
INV_TWO = [
    {"name": "Axe", "displayName": "斧头", "stack": 1, "quality": 0, "catNum": -99},
    _cat(0, 3), _cat(1, 2),
]

SENT = []
_CAPS = {"sell_quality": True}


def _stub(inv):
    """把会动游戏的调用全换成记账桩子（`/state` 形状照真机抄）。"""
    M.api.state = lambda *a, **k: {"location": {"name": "Farm"}, "inventory": list(inv)}
    M.api.map_data = lambda *a, **k: {"buildings": [
        {"type": "Shipping Bin", "x": 71, "y": 14, "doorX": 71, "doorY": 15}]}
    M.api.walk_natural = lambda *a, **k: {"ok": True}
    M.api.walk_to_coord = lambda *a, **k: {"ok": True}
    M._im_caps = lambda: dict(_CAPS)

    def _post(ep, body=None, *a, **k):
        SENT.append((ep, dict(body or {})))
        _b = dict(body or {})
        _q = _b.get("quality")
        if _q is None:
            _n = sum(it.get("stack") or 0 for it in inv
                     if it.get("displayName") == "鲶鱼")
            _sd = [{"item": "Catfish", "count": _n, "price": 0}]
        else:
            _n = sum(it.get("stack") or 0 for it in inv
                     if it.get("displayName") == "鲶鱼" and it.get("quality") == _q)
            _sd = [{"item": "Catfish", "count": _n, "price": 0}]
        return {"ok": True, "sold": _sd, "totalItems": len(_sd)}

    M.api._post = _post
    M._with_state = lambda s, *a, **k: s          # 状态条不是这条钉子的事（省掉整条 state 组装）
    M.time = types.SimpleNamespace(sleep=lambda *_: None)
    SENT[:] = []


_REAL = {"state": M.api.state, "map_data": M.api.map_data, "walk_natural": M.api.walk_natural,
         "walk_to_coord": M.api.walk_to_coord, "_post": M.api._post, "_with_state": M._with_state,
         "_im_caps": M._im_caps, "time": M.time,
         "split": M._split_quality_prefix}


def sec_parse():
    print("\n①②③④ 品质词 ⇒ `/sell` 收到的**就是那一摞**（dict 完全相等，不许夹别的键）")
    _stub(INV_ALL)
    _cases = [
        ("铱星鲶鱼", 4), ("铱鲶鱼", 4), ("金鲶鱼", 2), ("金星鲶鱼", 2),
        ("银星鲶鱼", 1), ("银色鲶鱼", 1), ("银鲶鱼", 1), ("普通鲶鱼", 0),
        ("iridium catfish", 4), ("gold catfish", 2), ("silver catfish", 1),
    ]
    for _name, _q in _cases:
        SENT[:] = []
        M.sell_to_bin(name=_name)
        _got = SENT[0][1] if SENT else None
        ck(f"①「{_name}」⇒ /sell 收到 name=Catfish + quality={_q}",
           _got == {"name": "Catfish", "quality": _q}, f"实际 {_got}")
        ck(f"①「{_name}」⇒ 只打一发 `/sell`、没夹 `all`",
           [e for (e, _b) in SENT] == ["/sell"] and "all" not in (_got or {}), str(SENT))

    print("\n③ 不带星级 ⇒ **一个 quality 键都没有**（不传的时候 Python 不许瞎猜）")
    for _name in ("鲶鱼", "Catfish", "catfish", "鲶 鱼"):
        SENT[:] = []
        M.sell_to_bin(name=_name)
        _got = SENT[0][1] if SENT else None
        ck(f"③「{_name}」⇒ body 就是 `{{'name': 'Catfish'}}`（无 quality）",
           _got == {"name": "Catfish"}, f"实际 {_got}")

    print("\n⑤ 物品名里自带「金/铱」的 ⇒ **不拆**（`金枪鱼` 不是「金星+枪鱼」）")
    for _name, _internal in (("金枪鱼", "Tuna"), ("铱锭", "Iridium Bar")):
        SENT[:] = []
        M.sell_to_bin(name=_name)
        _got = SENT[0][1] if SENT else None
        ck(f"⑤「{_name}」⇒ 当普通物品投（name={_internal}，无 quality）",
           _got == {"name": _internal}, f"实际 {_got}")

    print("\n⑤' 拆出来的物品名不在包里 ⇒ 不认（宁可不猜）")
    SENT[:] = []
    M.sell_to_bin(name="铱星南瓜")
    _got = SENT[0][1] if SENT else None
    ck("⑤'「铱星南瓜」（南瓜不在包里）⇒ 原样发、不带 quality",
       _got == {"name": "铱星南瓜"}, f"实际 {_got}")

    print("\n⑤'' 只剩一个品质词（没有物品名）⇒ 不当成「品质+物品」")
    SENT[:] = []
    M.sell_to_bin(name="铱星")
    _got = SENT[0][1] if SENT else None
    ck("⑤''「铱星」⇒ 原样发、不带 quality", _got == {"name": "铱星"}, f"实际 {_got}")


def sec_safety():
    print("\n⑥ 背包里**没有那一档** ⇒ 不打 `/sell`，回执说人话")
    _stub(INV_TWO)
    _out = M.sell_to_bin(name="铱星鲶鱼")
    ck("⑥没那一档 ⇒ 一个请求都没发（出货箱取不回来，宁可不投）", SENT == [], str(SENT))
    ck("⑥回执说清「只有 普通、银星 的鲶鱼」", "只有" in _out and "银星" in _out, _out[:200])
    ck("⑥…并点明「没有铱星的」", "没有铱星的" in _out, _out[:200])
    ck("⑥…且明说**没投**", "没投" in _out, _out[:200])
    ck("⑥…还给下一步（换个星级 / 去掉星级）",
       "换个星级" in _out and "去掉星级" in _out, _out[:240])

    print("\n⑦ 老 DLL（没有 `sell_quality` 能力位）⇒ 指定星级**不投**，如实说清")
    _stub(INV_ALL)
    _CAPS["sell_quality"] = False
    try:
        _out = M.sell_to_bin(name="铱星鲶鱼")
        ck("⑦能力位不在 ⇒ 一个请求都没发（老 DLL 会忽略 quality 把各星级一起投光）",
           SENT == [], str(SENT))
        ck("⑦回执说「这版 DLL 还认不出星级」", "认不出星级" in _out, _out[:200])
        ck("⑦…并说清解法「重开游戏上新 DLL」", "重开游戏" in _out and "DLL" in _out, _out[:240])
        ck("⑦…还说清为什么（会把各星级一起投光 / 取不回来）",
           "一起投光" in _out or "取不回来" in _out, _out[:260])
    finally:
        _CAPS["sell_quality"] = True

    print("\n⑧ 不带星级 ⇒ **不看能力位**（老行为一个字都不变）")
    _stub(INV_ALL)
    _CAPS["sell_quality"] = False
    try:
        M.sell_to_bin(name="鲶鱼")
        ck("⑧老 DLL + 不带星级 ⇒ 照发 `{'name': 'Catfish'}`（一个 quality 都没有）",
           SENT and SENT[0][1] == {"name": "Catfish"}, str(SENT))
    finally:
        _CAPS["sell_quality"] = True


def sec_source():
    print("\n⑨ C# 源码钉：`quality` 只在**传了**时才比较（不传 ⇒ 老行为一个字都不变）")
    _cs = io.open(os.path.join(HERE, os.pardir, "ModEntry.cs"),
                  encoding="utf-8", errors="replace").read()

    def _cs_code(src):
        """剥掉 C# 注释（注释里写着 quality 不算修好了）。"""
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        return re.sub(r"//[^\n]*", "", src)

    _a = _cs.find("private object HandleSell(HttpListenerContext ctx)")
    _b = _cs.find("private object HandleSellToShop(HttpListenerContext ctx)")
    _hs = _cs_code(_cs[_a:_b] if 0 <= _a < _b else "")
    ck("⑨切到了 `HandleSell` 那段（不是空串）", bool(_hs) and "getShippingBin" in _hs, str(len(_hs)))
    ck("⑨`quality` 是**可选参数**（`GetParamOr(p, \"quality\", -1)`，默认 -1 = 不限）",
       'GetParamOr(p, "quality", -1)' in _hs,
       "没找到可选 quality 参数的取法（默认值必须 -1）")
    _i_guard = _hs.find("wantQuality >= 0")
    _i_cmp = _hs.find("!= wantQuality")
    ck("⑨有 `wantQuality >= 0` 这道闸（传了才比）", _i_guard >= 0, "没找到闸")
    ck("⑨真的比了 `Quality != wantQuality`", _i_cmp >= 0, "没找到比较")
    ck("⑨闸在比较**之前**（顺序反了就是「永远比」）", 0 <= _i_guard < _i_cmp,
       f"guard@{_i_guard} cmp@{_i_cmp}")
    ck("⑨比较用的是 `Object.Quality`（非 Object 取 -1 ⇒ 指定星级时不会误命中工具/帽子）",
       "StardewValley.Object)?.Quality" in _hs, "没看到 Object.Quality")
    ck("⑨名字判据仍然走 `ItemNameMatches`（品质是**附加**条件，不是替掉名字）",
       "ItemNameMatches(item, name)" in _hs, "名字判据被换掉了")
    ck("⑨能力位 `sell_quality` 在 `/status.caps` 里登记了（消费侧靠它挡老 DLL）",
       '["sell_quality"] = true' in _cs, "caps 里没有 sell_quality")

    print("\n⑩ Python 源码钉：解析 → 换内部名 → 只有点名了才带 quality")
    _bin_c = "\n".join(ln.split("#", 1)[0]
                       for ln in inspect.getsource(M.sell_to_bin).splitlines())
    ck("⑩`sell_to_bin` 走了 `_split_quality_prefix(`", "_split_quality_prefix(" in _bin_c,
       "没走品质解析")
    ck("⑩品质解析在 `_internal_item_name(` **之前**（先拆星级，再换内部名）",
       -1 < _bin_c.find("_split_quality_prefix(") < _bin_c.find("_internal_item_name("),
       f"split@{_bin_c.find('_split_quality_prefix(')} name@{_bin_c.find('_internal_item_name(')}")
    ck("⑩不带星级那条 `api._post(\"/sell\", {\"name\": post_name})` **原样还在**（老行为一个字没改）",
       'api._post("/sell", {"name": post_name})' in _bin_c, "老那行被改掉了")
    ck("⑩带星级时才多带 `quality`（`{\"name\": post_name, \"quality\": _want_q}`）",
       'api._post("/sell", {"name": post_name, "quality": _want_q})' in _bin_c,
       "没找到带 quality 的那一发")
    ck("⑩带 quality 的那一发在 `_want_q is None` 分支**之后**（不是无条件发）",
       -1 < _bin_c.find("if _want_q is None:") < _bin_c.find('"quality": _want_q'),
       f"guard@{_bin_c.find('if _want_q is None:')} q@{_bin_c.find(chr(34) + 'quality' + chr(34) + ': _want_q')}")
    ck("⑩名字判据**复用** `_internal_item_name`（没另起一份中英对照）",
       "_internal_item_name(_base" in _bin_c, "没复用")
    _i_caps = _bin_c.find('_im_caps()')
    _i_post = _bin_c.find('api._post("/sell"')
    ck("⑩老 DLL 的闸在 `_post` **之前**（不许「先投了再说」）", -1 < _i_caps < _i_post,
       f"caps@{_i_caps} post@{_i_post}")

    # 解析器本身：只认前缀，不认"中间出现"
    _f = M._split_quality_prefix
    ck("⑩`_split_quality_prefix('鲶鱼铱星')` ⇒ 不拆（品质词必须在**开头**）",
       _f("鲶鱼铱星", INV_ALL) == (None, "鲶鱼铱星"), str(_f("鲶鱼铱星", INV_ALL)))
    ck("⑩`_split_quality_prefix('铱星鲶鱼')` ⇒ (4, '鲶鱼')",
       _f("铱星鲶鱼", INV_ALL) == (4, "鲶鱼"), str(_f("铱星鲶鱼", INV_ALL)))
    ck("⑩背包读不到（空表）时也认得出「铱星鲶鱼」（显式指定就照拆）",
       _f("铱星鲶鱼", []) == (4, "鲶鱼"), str(_f("铱星鲶鱼", [])))
    _src = inspect.getsource(M)          # 品质词表在**模块级**（不是函数局部）⇒ 按模块源码钉
    ck("⑩品质词表按「长词在前」排（`铱星` 必须先于 `铱`）",
       _src.find('("铱星", 4)') < _src.find('("铱", 4)'), "顺序错了")
    ck("⑩铱星映射到 **4**（`Item.cs:308`：3 不存在）",
       ("铱星", 4) in M._QUALITY_WORDS and 3 not in [q for _w, q in M._QUALITY_WORDS],
       str(M._QUALITY_WORDS))


def main():
    try:
        sec_parse()
        sec_safety()
        sec_source()
    finally:
        M.api.state = _REAL["state"]
        M.api.map_data = _REAL["map_data"]
        M.api.walk_natural = _REAL["walk_natural"]
        M.api.walk_to_coord = _REAL["walk_to_coord"]
        M.api._post = _REAL["_post"]
        M._with_state = _REAL["_with_state"]
        M._im_caps = _REAL["_im_caps"]
        M.time = _REAL["time"]
        M._split_quality_prefix = _REAL["split"]

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 品质词又没被认：指定「铱星鲶鱼」投不进那一摞，或者不传星级时被瞎猜了一个。"
              "别改钉子去凑。")
        sys.exit(1)
    print("✅ 全绿：点名星级 ⇒ /sell 带 quality 挑那一摞；不带星级 ⇒ 一个 quality 都没有；"
          "没那一档/老 DLL ⇒ 不瞎投、说人话。")
    sys.exit(0)


if __name__ == "__main__":
    main()
