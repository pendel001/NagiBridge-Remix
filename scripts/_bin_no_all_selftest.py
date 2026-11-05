# -*- coding: utf-8 -*-
"""📦 出货箱「不许全投」钉子 —— 出货箱不是仓库，投进去就取不回来了。2026-11-05

来历（恒）：
  「还有最后一个出货箱问题，**笨 ai 一下选 all 全卖了**。由于出货箱物品**不能取回**的特性，
    我觉得**不能给 ai 直接 all 了**。上单让它 intent 选 123456 吧。像商店卖东西时那样。」

🩸 病根（这条钉子就是防它回来）：
  ① `menu(ops="bin", sell_all=True)` 是**一条指令、不可逆**：C# `HandleSell` 把背包里能卖的全塞进
     `ShippingBin`，而农场那只箱子开的是"**只收不给**"的界面（`ShippingBin.cs:267`
     `ItemGrabMenu(null, …, showReceivingMenu: false)`）⇒ 投进去就只剩"撤回刚放进去那件"，
     当晚 `ShippingMenu` 结算就卖掉。
  ② 更阴的是**空名字 = 全投**：`HandleSell` 的名字过滤只在 `name` 非空时才生效 ⇒
     `menu(ops="bin")`（一个参数都不给）也照样整包全投。**不可逆的批量不许有默认值**。
  ③ 而"全投"这四个字是**我们自己的状态条教的**：农场那行原来写着
     `menu(ops="bin", sell_all=True)` 全投 / … ⇒ AI 照着念，很"听话"地全投了。

钉六条：
  ① `sell_to_bin(sell_all=True)` ⇒ **拒**，且 `/sell` **一次都没打**（不许"先投了再说"）
  ② `sell_to_bin()`（空名字）⇒ **拒**，且 `/sell` 一次都没打
  ③ 点名一件 ⇒ 才打 `/sell`，body **只有 `name`**（不含 `all`），回执明说**取不回来**
  ④ 单子那层跟「卖」同形：`pick` + `exec_on_pick`（`1,3` 多选、敲了当场投），
     标题/提示都写清「取不回来」；顶层**没有**"全部投放"这种行
  ⑤ 多选执行 = **逐条点名**（两次 `bin`，各带自己的名字），回执逐条 + 尾巴 🔒
  ⑥ 端点自己也守（C# `HandleSell` 空名字在 `bin.Add` **之前**就 return），
     文案面（农场状态条 / `menu` 文档 / 帮助速查表）**不再教"全投"**

跑法：`python scripts/_bin_no_all_selftest.py`（退出码 0 = 全绿）
"""
import ast
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import nagi_mcp_server as M       # noqa: E402
import intent_menu as IM          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ═══════════════════════════════════════════════════════════════════
#  ①②③ 端点这一层：能不能"一把全投"
# ═══════════════════════════════════════════════════════════════════
CALLS = []


def _stub_api():
    """把会真的动游戏的那几个 api 调用全换成记账的桩子。"""
    M.api.state = lambda *a, **k: {"location": {"name": "Farm"}}
    M.api.map_data = lambda *a, **k: {"buildings": [
        {"type": "Shipping Bin", "x": 71, "y": 14, "doorX": 71, "doorY": 15}]}
    M.api.walk_natural = lambda *a, **k: {"ok": True}
    M.api.walk_to_coord = lambda *a, **k: {"ok": True}

    def _post(ep, body=None, *a, **k):
        CALLS.append((ep, body))
        return {"ok": True, "sold": [{"item": "古书", "count": 1, "price": 50}],
                "totalItems": 1}

    M.api._post = _post


def sec_endpoint():
    _stub_api()
    CALLS[:] = []

    print("\n① `sell_all=True`（真机那把「全投」）⇒ 直接拒，端点一次都不打")
    out = M.sell_to_bin(sell_all=True)
    ck("回执是 ⛔/⚠️ 那种「不许」", ("⛔" in out) or ("⚠️" in out), out)
    ck("说明「拿不回来 / 取不回来」", ("拿不回来" in out) or ("取不回来" in out), out)
    ck("指向单子（`intent`）", "intent" in out, out)
    ck("**`/sell` 一次都没打**（没先投了再说）", CALLS == [], str(CALLS))

    print("\n② 空名字（老实现里 = 全投）⇒ 也拒，且不打端点")
    CALLS[:] = []
    out = M.sell_to_bin()
    ck("回执是「得点名」", "得点名" in out or "点名" in out, out)
    ck("**`/sell` 一次都没打**", CALLS == [], str(CALLS))
    CALLS[:] = []
    out = M.sell_to_bin(name="   ")
    ck("空白名字同样拒（别让空格绕过）", CALLS == [], str(CALLS))

    print("\n③ 点名一件 ⇒ 才打 `/sell`，且 **body 里没有 `all`**")
    CALLS[:] = []
    out = M.sell_to_bin(name="古书")
    ck("打了 `/sell`", [c[0] for c in CALLS] == ["/sell"], str(CALLS))
    ck("body 只有 name", CALLS and CALLS[0][1] == {"name": "古书"}, str(CALLS))
    ck("回执说「取不回来」", "取不回来" in out, out)


# ═══════════════════════════════════════════════════════════════════
#  ④⑤ 单子这一层：形状照「卖」，多选、逐条点名
# ═══════════════════════════════════════════════════════════════════
def _farm_ctx():
    inv = IM.scan_backpack({"inventory": [
        {"slotIndex": 2, "displayName": "古书", "stack": 1, "catNum": -102,
         "sellable": True, "shippable": True, "value": 50},
        {"slotIndex": 3, "displayName": "草莓", "stack": 5, "catNum": -79,
         "sellable": True, "shippable": True, "value": 120},
        # ⚠️ 可卖但**投不了**（大型可制造物）：真机上正是这批让"投"列表变成假承诺
        {"slotIndex": 4, "displayName": "小桶", "stack": 1, "catNum": -9,
         "sellable": True, "shippable": False, "value": 0}]})
    return IM.Ctx(px=12, py=12, loc="Farm", inv=inv, held=None, tiles={},
                  stamina=268, max_items=36, pets=[],
                  caps={"state_shippable": True})


def sec_intent():
    ctx = _farm_ctx()
    print("\n④ 单子那层 = 跟「卖」同一套（pick + exec_on_pick，`1,3` 多选）")
    IM.reset_menu()
    top = IM.render_menu(ctx, n=40)
    ck("顶层给「投出货箱…」目录行", "投出货箱…" in top, top[:200])
    ck("顶层**没有**「全部投放」这类行（不可逆的批量不许出现）",
       not any("全部" in (r.label or "") for r in IM._LAST_ROWS),
       str([r.label for r in IM._LAST_ROWS]))
    _no = next(r.no for r in IM._LAST_ROWS if (r.label or "") == "投出货箱")
    sub = IM.do_row(_no, lambda *a: {"ok": True}, ctx)
    lv = IM._STACK[-1]
    ck("点开是 pick 层", lv.mode == "pick", lv.mode)
    ck("…而且 `exec_on_pick`（敲了当场投，不进「各多少」那个假界面）",
       lv.exec_on_pick is True, str(lv.exec_on_pick))
    ck("标题写明**拿不回来**", "拿不回来" in (lv.title or ""), lv.title)
    ck("提示行说清怎么敲（多选）", "多选" in (lv.hint or ""), lv.hint)
    ck("一件一行：古书 + 草莓 都在", "投 古书" in sub and "投 草莓" in sub, sub[:300])
    ck("**投不了的**（小桶）不列", "投 小桶" not in sub, sub[:300])

    print("\n⑤ 多选 `1,3` ⇒ 逐条点名（一个名字一发），回执逐条 + 🔒")
    calls = []

    def _run(ep, payload):
        calls.append((ep, dict(payload)))
        nm = payload.get("name")
        return {"ok": True, "st": "yes",
                "text": f"📦 已投进出货箱 1 种：{nm}×1 (50g)\n   🔒 取不回来"}

    IM.reset_menu()
    IM.render_menu(ctx, n=40)
    IM.do_row(next(r.no for r in IM._LAST_ROWS if (r.label or "") == "投出货箱"),
              lambda *a: {"ok": True}, ctx)
    _labels = [r.label for r in IM._STACK[-1].rows
               if (r.label or "").startswith("投 ")]
    _pick = ",".join(str(i) for i, r in enumerate(IM._STACK[-1].rows, 1)
                     if (r.label or "").startswith("投 "))
    out = IM.do_row(_pick, _run, ctx)
    ck("两件 ⇒ 两次 `bin`（各带自己的名字）",
       [c[0] for c in calls] == ["bin", "bin"]
       and [c[1].get("name") for c in calls] == ["古书", "草莓"], str(calls))
    ck("回执逐条报（两行 `· `）", out.count("  · ") == 2, out)
    ck("回执尾巴写 🔒 取不回来", "取不回来" in out, out)
    ck("回执是 ✅ 起的（真投成了）", out.lstrip().startswith("✅"), out[:60])


# ═══════════════════════════════════════════════════════════════════
#  ⑥ 文案面 + C# 端点
# ═══════════════════════════════════════════════════════════════════
def sec_texts():
    print("\n⑥ 文案面：不再有一条路教「全投」")
    M._SHIPBIN_SEEN.clear()
    M._SHIPBIN_SEEN["loc"] = ""
    M.api.map_data = lambda *a, **k: {"buildings": [
        {"type": "Shipping Bin", "x": 71, "y": 14, "doorX": 71, "doorY": 15}]}
    M.api.surroundings = lambda *a, **k: {"tiles": [], "center": {"x": 71, "y": 15}}
    line = M._shipbin_hint("Farm")
    ck("农场状态条那行还在（AI 得知道箱子在哪）", "出货箱" in line and "(71,15)" in line, line)
    ck("…**不再写** `sell_all`", "sell_all" not in line, line)
    ck("…改成指单子", "intent" in line, line)
    ck("…并且说了「取不回来」", "取不回来" in line, line)

    _src = io.open(os.path.join(HERE, "nagi_mcp_server.py"),
                   encoding="utf-8", errors="replace").read()
    _tree = ast.parse(_src)
    _strs = [n.value for n in ast.walk(_tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    ck("源码的**字符串常量**里没有 `bin=name,sell_all` 这种诱导",
       not any("bin=name,sell_all" in s for s in _strs))
    # 帮助速查表里那条必须改成"只认名字"
    _row = None
    for n in ast.walk(_tree):
        if isinstance(n, ast.Tuple) and len(n.elts) >= 4 and \
                isinstance(n.elts[0], ast.Constant) and isinstance(n.elts[0].value, str) \
                and "投出货箱" in n.elts[0].value:
            _row = n
            break
    _hint = (_row.elts[3].value if _row is not None
             and isinstance(_row.elts[3], ast.Constant) else "")
    ck("帮助速查表那条：不再教 `sell_all`", "sell_all" not in _hint, _hint)
    ck("…并写明「一次一件」", "一次一件" in _hint, _hint)
    ck("`sell_to_bin` 文档不再说「不指定名称则卖所有」",
       "不指定名称" not in (M.sell_to_bin.__doc__ or ""))
    ck("…并写明「一次一件」", "一次一件" in (M.sell_to_bin.__doc__ or ""))

    # C# 端点：空名字必须在 `bin.Add` **之前**就拒（不可逆的批量不许有默认值）
    _cs = io.open(os.path.join(HERE, os.pardir, "ModEntry.cs"),
                  encoding="utf-8", errors="replace").read()
    ck("`bin.Add(item)` 全仓只有一处（HandleSell）", _cs.count("bin.Add(item)") == 1,
       str(_cs.count("bin.Add(item)")))
    # ⚠️ 判据**切在 HandleSell 这一段里**：全仓搜 `string.IsNullOrWhiteSpace(name)`
    #    会命中别的函数（第一版就这么写的，把闸拆了钉子还亮着 —— 假绿）。
    _hs0 = _cs.find("private object HandleSell(")
    _hs1 = _cs.find("private object HandleSellToShop(", _hs0)
    _hs = _cs[_hs0:_hs1] if 0 <= _hs0 < _hs1 else ""
    ck("切到了 `HandleSell` 那段", bool(_hs) and "getShippingBin" in _hs, str(_hs0))
    _i_guard = _hs.find("string.IsNullOrWhiteSpace(name)")
    _i_add = _hs.find("bin.Add(item)")
    ck("HandleSell 挡了空名字", _i_guard > 0, f"guard={_i_guard}")
    ck("…而且**挡在 `bin.Add` 之前**（不是投完再说）",
       0 <= _i_guard < _i_add, f"guard={_i_guard} add={_i_add}")
    ck("…拦下来的那句说清了「空名字 = 全投，已封」",
       "空名字 = 全投，已封" in _hs)


def main():
    sec_endpoint()
    sec_intent()
    sec_texts()

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 出货箱**投进去取不回来**：一把「全投」= 当场把该留的也卖了，别改这条钉子去凑。")
        sys.exit(1)
    print("✅ 出货箱全绿：没有「全投」，要投哪件就在单子上敲 1,2,3（可多选），端点也只认点名。")
    sys.exit(0)


if __name__ == "__main__":
    main()
