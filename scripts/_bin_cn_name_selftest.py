# -*- coding: utf-8 -*-
"""📦 出货箱收不收**中文名字** —— 纯 Python 自验（离线，不碰游戏、不发 HTTP）。2026-11-08

## 恒原话
> 「同一个问题：**出货箱不收中文名字**。」

## 根因（两处，一上一下）
  ① C# `HandleSell` 的名字过滤**只比 `item.Name`**（英文内部名 `Salad`）：
        `if (!sellAll && !string.IsNullOrEmpty(name) && !item.Name.Equals(name, …)) continue;`
     `item.DisplayName`（中文显示名 `沙拉`）**根本没参与比较** ⇒ `menu(ops="bin", name="沙拉")`
     一件都匹配不上，可回执反过来跟 AI 说「背包里没有「沙拉」能投」（东西就在包里）。
     同族（`/drop`、`/chest_take`、`/select`）当年各自补过一遍 DisplayName，
     **补一遍漏一处**正是这个 bug 反复报的来源 ⇒ 现在收成 `ItemNameMatches` 一个判据。
  ② 但 C# 改了**得等游戏关掉才能部署**（DLL 锁着），所以 Python 这侧先兜一层：
     `sell_to_bin()` 先用**全仓既有的** `_internal_item_name()` 把名字换成内部名再发 `/sell`
     （`/drop`/`/gift`/`wear` 早就是走它，它自己 doc 里写着"判据只有这一处、别再各写一份"；
     真机已确认 `/state.inventory` 带 `displayName`：`{"name": "Salad", "displayName": "沙拉"}`）。
     ⚠️ **不硬编中英对照表**：名字只从游戏现报的背包来；游戏说没有就原样发、如实说没有。

## 钉死这些条
  ① C#：`HandleSell` 的名字判据**包含显示名**（直接写 DisplayName，或走 `ItemNameMatches` 而后者含它）
  ② C#：`ItemNameMatches` 里**内部名 + 显示名都在比**（删掉 DisplayName 这一句就红）
  ③ C#：`HandleSell` 里**不再有**「只比 Name」的裸过滤行（有人改回去就红）
  ④ C#：`HandleSellToShop` 同一口径（同族不许只修一处）
  ⑤ C#：全仓 `ItemNameMatches(...)` 调用点 ≥ 6 处（select/饰品/戒指/穿戴/两处卖）
  ⑥ Python：给中文名 ⇒ 发出去的是**英文内部名**（打桩 `api._post`，抓它收到的 name）
  ⑦ Python：给英文内部名 ⇒ 照样投得出去（`Salad` 原样；`salad` 归一成内部名也不算错）
  ⑧ Python：包里没有这个名字 ⇒ **原样发**（不猜、不拿对照表硬翻）
  ⑨ Python：名字换到了内部名、游戏仍没投 ⇒ 回执提内部名，**不拿名字背锅**
  ⑩ Python：`/state` 连 `displayName` 都没有 ⇒ 回执说真话「要重开游戏上新 DLL（已修）」
  ⑪ Python：名字判据**复用** `_internal_item_name`（没有另起一份），且它代码里没有一个中文串
  ⑫ Python：`sell_to_bin` 真的调了它，而且**在 `_post` 之前**就换好名字

跑法：`python -X utf8 scripts/_bin_cn_name_selftest.py`（退出码 0 = 全绿）
"""
import ast
import inspect
import io
import os
import re
import sys
import textwrap

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


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _cs_section(src, start, end):
    """切出 `start` 到 `end` 之间那一段源码（找不到就返回空串 —— 那就该红）。"""
    a = src.find(start)
    if a < 0:
        return ""
    b = src.find(end, a + 1)
    return src[a:b] if b > a else ""


def _py_code(src):
    """剥掉 Python 的 `#` 注释 —— **注释里提到函数名不算数**。

    ⚠️ 同 `_cs_code`：这条也是负向刀逼出来的。`sell_to_bin` 的说明里写着
       「`_internal_item_name()` —— `/drop`…」，把兜底那行删掉后，钉子只看
       `'_internal_item_name(' in 源码` ⇒ 又被自己那句注释蒙绿（假绿）。
    """
    return "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())


def _cs_code(src):
    """剥掉 C# 注释：**注释里提到 DisplayName 不算数**。

    ⚠️ 这条是负向刀逼出来的（第一版没剥注释）：`HandleSell` 的说明里写着
       「`DisplayName`（`沙拉`）根本没参与比较」，于是把判据退回老 bug 后，
       钉子只看 `'DisplayName' in 段` ⇒ 被自己那句注释蒙绿（假绿）。判据必须看**代码**。
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


# ═══════════════════════════════════════════════════════════════════
#  C# 那一侧：判据里得有 DisplayName（源码钉，切片不整仓搜 —— 整仓搜会命中别的函数）
# ═══════════════════════════════════════════════════════════════════
def sec_csharp():
    print("\n① ~ ⑤ C# 侧：名字判据 = 内部名 + 显示名（同一个口径，别只修一处）")
    _cs = io.open(os.path.join(HERE, os.pardir, "ModEntry.cs"),
                  encoding="utf-8", errors="replace").read()
    _hs = _cs_section(_cs, "private object HandleSell(HttpListenerContext ctx)",
                      "private object HandleSellToShop(HttpListenerContext ctx)")
    _shop = _cs_section(_cs, "private object HandleSellToShop(HttpListenerContext ctx)",
                        "private object HandleRefill(")
    ck("切到了 `HandleSell` 那段（不是空串）", bool(_hs) and "getShippingBin" in _hs, str(len(_hs)))
    ck("切到了 `HandleSellToShop` 那段", bool(_shop) and "ShopMenu" in _shop, str(len(_shop)))

    _i = _cs.find("private static bool ItemNameMatches(")
    _helper = _cs[_i:_cs.find(chr(10) + "    }", _i) + 6] if _i >= 0 else ""
    # ⚠️ 判据只看**代码**（剥注释）：注释里写 DisplayName 不算修好了
    _hs_c, _shop_c, _helper_c = _cs_code(_hs), _cs_code(_shop), _cs_code(_helper)
    ck("切到了 `ItemNameMatches` 的定义", bool(_helper_c) and "DisplayName" in _helper_c,
       str(len(_helper_c)))

    # ① HandleSell 的名字判据含显示名：直接写 DisplayName，或走 ItemNameMatches 而后者含它
    ck("① `HandleSell` 的名字匹配**含显示名**（不再只认英文内部名）",
       ("DisplayName" in _hs_c)
       or ("ItemNameMatches" in _hs_c and "DisplayName" in _helper_c),
       "sell 段的**代码**里既没 DisplayName 也没走 ItemNameMatches")
    # ② 判据体里两种名字都在比
    ck("② `ItemNameMatches` 里**内部名 + 显示名都在比**",
       "item.Name.Equals(name" in _helper_c and "DisplayName" in _helper_c, _helper_c[:120])
    # ③ 老的裸判据不许回来（`!item.Name.Equals(name, …)` 后面直接 continue）
    _bare = re.findall(r"!item\.Name\.Equals\(name,\s*StringComparison\.OrdinalIgnoreCase\)\)",
                       _hs_c)
    ck("③ `HandleSell` 里**不再**只有 `Name.Equals` 一道（改回去就红）",
       not _bare, f"命中 {len(_bare)} 处裸判据")

    # 逐行扫：出现 `Name.Equals(name` 的那行必须同时带 DisplayName / ItemNameMatches
    def _bare_lines(sec):
        return [ln.strip() for ln in sec.splitlines()
                if "Name.Equals(name" in ln
                and "ItemNameMatches" not in ln and "DisplayName" not in ln]

    ck("③b `HandleSell` 里没有一行「只比 Name」的过滤", not _bare_lines(_hs_c),
       str(_bare_lines(_hs_c)))
    # ④ 同族 `HandleSellToShop` 一个口径
    ck("④ `HandleSellToShop` 也走同一判据（不是只修出货箱）",
       "ItemNameMatches" in _shop_c and not _bare_lines(_shop_c),
       str(_bare_lines(_shop_c)) or "没走 ItemNameMatches")
    # ⑤ 全仓调用点：1 处定义 + 至少 6 处用（select/饰品/戒指/穿戴/两处卖）
    _defs = _cs.count("private static bool ItemNameMatches(")
    _calls = _cs.count("ItemNameMatches(") - _defs
    ck("⑤ 全仓 `ItemNameMatches` 的调用点 ≥ 6（select/饰品/戒指/穿戴/`/sell`/`/sell_to_shop`）",
       _calls >= 6, f"defs={_defs} calls={_calls}")


# ═══════════════════════════════════════════════════════════════════
#  Python 那一侧：中文名 ⇒ 发出去的必须是内部名
# ═══════════════════════════════════════════════════════════════════
SENT = []

INV_SALAD = [
    {"slotIndex": 0, "name": "Axe", "displayName": "斧头", "stack": 1, "catNum": -99},
    {"slotIndex": 2, "name": "Salad", "displayName": "沙拉", "stack": 4, "catNum": -7,
     "sellable": True, "shippable": True, "value": 110},
]


def _stub(inv, sold=None):
    """把会动游戏的调用全换成记账桩子（`/state` 的返回形状照真机抄）。"""
    M.api.state = lambda *a, **k: {"location": {"name": "Farm"}, "inventory": list(inv)}
    M.api.map_data = lambda *a, **k: {"buildings": [
        {"type": "Shipping Bin", "x": 71, "y": 14, "doorX": 71, "doorY": 15}]}
    M.api.walk_natural = lambda *a, **k: {"ok": True}
    M.api.walk_to_coord = lambda *a, **k: {"ok": True}

    def _post(ep, body=None, *a, **k):
        SENT.append((ep, dict(body or {})))
        _sd = list(sold or [])
        return {"ok": True, "sold": _sd, "totalItems": len(_sd)}

    M.api._post = _post
    SENT[:] = []


def sec_python():
    print("\n⑥ 中文名 ⇒ 发出去的是**英文内部名**（DLL 没上也能投）")
    _stub(INV_SALAD, sold=[{"item": "Salad", "count": 4, "price": 440}])
    out = M.sell_to_bin(name="沙拉")
    ck("⑥ 打了 `/sell`", [c[0] for c in SENT] == ["/sell"], str(SENT))
    ck("⑥ body 里是**内部名** `Salad`（不是「沙拉」）",
       SENT and SENT[0][1].get("name") == "Salad", str(SENT))
    ck("⑥ body 里仍然**没有** `all`（不可逆的批量照旧封着）",
       SENT and "all" not in SENT[0][1], str(SENT))
    ck("⑥ 真投成了 ⇒ 回执照样报「已投进出货箱」", "已投进出货箱" in out)   # noqa: E501

    print("\n⑦ 英文内部名 ⇒ 照样投得出去（老路不动）")
    _stub(INV_SALAD, sold=[{"item": "Salad", "count": 4, "price": 440}])
    M.sell_to_bin(name="Salad")
    ck("⑦ `Salad` 原样发出去", SENT and SENT[0][1].get("name") == "Salad", str(SENT))
    _stub(INV_SALAD, sold=[{"item": "Salad", "count": 4, "price": 440}])
    M.sell_to_bin(name="salad")
    ck("⑦ 英文大小写照旧能投（归一成内部名 `Salad`，判据本来就忽略大小写）",
       SENT and str(SENT[0][1].get("name") or "").lower() == "salad", str(SENT))

    print("\n⑧ 包里没这个名字 ⇒ 原样发（**不许拿对照表硬翻**、不许猜）")
    _stub(INV_SALAD)
    out = M.sell_to_bin(name="南瓜")
    ck("⑧ `南瓜` 原样发出去（没被换成任何别的名字）",
       SENT and SENT[0][1].get("name") == "南瓜", str(SENT))
    ck("⑧ 回执如实说「背包里没有「南瓜」能投」", "背包里没有「南瓜」" in out, out[:160])
    ck("⑧ 没有显示名可查时**不冒充**「已换内部名」", "查到内部名" not in out, out[:200])

    print("\n⑨ 名字换到了内部名、游戏仍没投 ⇒ 回执提内部名，别拿名字背锅")
    _stub(INV_SALAD, sold=[])
    out = M.sell_to_bin(name="沙拉")
    ck("⑨ 回执写清「已按背包里的内部名「Salad」去投」",
       "已按背包里的内部名「Salad」" in out, out[:220])
    ck("⑨ …此时**不许**再说「要重开游戏上新 DLL」（那是另一种情形）",
       "重开游戏" not in out, out[:220])

    print("\n⑩ `/state` 连 `displayName` 都没有（老 DLL）⇒ 说真话，别编对照表")
    _stub([{"slotIndex": 2, "name": "Salad", "stack": 4, "catNum": -7}], sold=[])
    out = M.sell_to_bin(name="沙拉")
    ck("⑩ 回执点明「只认英文内部名」", "只认英文内部名" in out, out[:240])
    ck("⑩ …并说清解法「重开游戏上新 DLL（已修）」",
       "重开游戏" in out and "DLL" in out and "已修" in out, out[:240])

    print("\n⑪ 名字判据**复用** `_internal_item_name`（没有另起一份 + 代码里没有中文串）")
    _bin_c = _py_code(inspect.getsource(M.sell_to_bin))
    ck("⑪ 用的是全仓已有的 `_internal_item_name(`（别再各写一份判据）",
       "_internal_item_name(" in _bin_c, "没走既有判据")
    ck("⑪ 没有另起一个自造的判据函数（`_bin_post_name` 那种）",
       "_bin_post_name" not in _bin_c, "又写了一份")
    _src = textwrap.dedent(inspect.getsource(M._internal_item_name))
    _tree = ast.parse(_src)
    _fn = _tree.body[0]
    _body = _fn.body
    if (_body and isinstance(_body[0], ast.Expr)
            and isinstance(_body[0].value, ast.Constant)
            and isinstance(_body[0].value.value, str)):
        _body = _body[1:]                      # 跳过 docstring（说明文字里当然有中文）
    _strs = [n.value for n in ast.walk(ast.Module(body=_body, type_ignores=[]))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    _cn = [s for s in _strs if CJK.search(s)]
    ck("⑪ 判据函数里没有中文名字面量（名字只能来自 `/state`）", not _cn, str(_cn))
    ck("⑪ 它按 `displayName` 找（看**代码里的字符串常量**，不看注释/说明）",
       "displayName" in _strs, str(_strs))

    print("\n⑫ `sell_to_bin` 真的调了它，而且**在 `_post` 之前**就换好名字")
    ck("⑫ 调了 `_internal_item_name(`", "_internal_item_name(" in _bin_c)
    _i_helper = _bin_c.find("_internal_item_name(")
    _i_post = _bin_c.find("api._post(")
    ck("⑫ 换名字在 `_post` **之前**（发出去的就是换好的那个）",
       -1 < _i_helper < _i_post, f"helper={_i_helper} post={_i_post}")
    ck("⑫ 发出去的就是 `post_name`（不是原始 `name`）",
       'api._post("/sell", {"name": post_name})' in _bin_c,
       "没找到把 post_name 交给 /sell 的那句")


def main():
    try:
        sec_csharp()
        sec_python()
    finally:
        M.api.state = _REAL["state"]
        M.api.map_data = _REAL["map_data"]
        M.api.walk_natural = _REAL["walk_natural"]
        M.api.walk_to_coord = _REAL["walk_to_coord"]
        M.api._post = _REAL["_post"]

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 中文名投不进出货箱 = 名字判据只认英文内部名，"
              "或 Python 兜底没把显示名换成内部名。别改钉子去凑。")
        sys.exit(1)
    print("✅ 全绿：C# 判据认「内部名 + 显示名」，Python 兜底会把中文名换成内部名再投。")
    sys.exit(0)


_REAL = {"state": M.api.state, "map_data": M.api.map_data,
         "walk_natural": M.api.walk_natural, "walk_to_coord": M.api.walk_to_coord,
         "_post": M.api._post}

if __name__ == "__main__":
    main()
