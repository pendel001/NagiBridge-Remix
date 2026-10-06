# -*- coding: utf-8 -*-
"""钉子：「**背包里的书/纸条要能出现在选项单、而且能包办**」（恒 2026-10-06 点的）。

恒原话：「读书相关：背包有**秘密纸条、日记残页、书本**时，出现在选项单，**可以包办** —— 这个也看看做了没有」。

查到的真缺口：选项单的 `read` 行判据原来**只认 `catNum == -102`**，
而**秘密纸条/日记残页不是 -102**（服务端 `_is_readable_item` 一直靠**名字**兜着它们，就是这个原因）
⇒ 那两类**根本不上单子**（书能上）。修 = `is_book` 补第二、三把尺子（`(O)Book_` 前缀 / 纸条名字，中英都收）。

钉五条：
  ① **不许漏**：服务端 `_is_readable_item` 说"能读"的，`is_book` **不许判 False**（最多 CAN_MAYBE）
     —— 两把尺子同口径，这条是**防漂移**的交叉对照
  ② 秘密纸条 / 日记残页（**没有 catNum** 的真实形态）⇒ `is_book` True ⇒ `_read_can` 放行 ⇒ **有那一行**
  ③ 工具（名字带「书」但 id 是 `(T)Hoe`）⇒ **False**（id 说了算，别让名字松判据捡回来）
  ④ 老 DLL（没 catNum 没 id，名字是个普通物）⇒ **CAN_MAYBE**（不判死，照旧进单子）
  ⑤ **包办**：`read` 那个动词挂着 `exec=`，执行器走 `/use mode=read`（选格→用→如实报"读过了没反应"）

跑：`python scripts/_read_row_selftest.py`
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, HERE)
import nagi_mcp_server as M       # noqa: E402
import intent_menu as IM          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _slot(name, item_id=None, cat=None):
    """照 `scan_backpack` 归一化后的形状（`name` 是 displayName 优先）。"""
    return {"name": name, "item_id": item_id, "cat_num": cat}


print("① 与服务端那把尺子交叉对照（**不许漏**）")
# ⚠️ 两个模块吃的**不是同一个字段**（这条就是钉子自己逮出来的，我第一版喂错了 ⇒ 假红两处）：
#    · 服务端 `_read_to_free_hint` 喂的是 `/state.inventory[].name` = **内部英文名**（实测 `Smallmouth Bass`）
#      ⇒ 它的纸条名单是**英文键**（`_SECRET_NOTE_NAMES = {"Secret Note": "秘密纸条", …}`）
#    · 菜单 `scan_backpack` 的 `name` 是 **displayName 优先** ⇒ 进来的是**中文**
#    ⇒ 交叉对照必须**按各自真实输入**喂（喂错字段 = 假红/假绿）。
_MATRIX = [
    # (内部英文名, displayName, item_id, cat_num)
    ("Jewels Of The Sea", "海之宝石", "(O)Book_Roe", -102),
    ("随便什么怪名字", "随便什么怪名字", "", -102),
    ("Another Unknown Book", "另一本怪书", "(O)Book_Fishing", None),
    ("Secret Note", "秘密纸条", "(O)79", None),
    ("Journal Scrap", "日记残页", "(O)842", None),
    ("Combat Quarterly", "战斗季刊", "", None),      # 服务端靠名字名单认（旧 DLL 兜底）
    ("Carrot", "胡萝卜", "(O)Carrot", -75),          # 阴性
    ("Axe", "斧头", "(T)Axe", -99),                  # 阴性
]
for en, zh, iid, cat in _MATRIX:
    _srv = M._is_readable_item(en, iid or "", cat)
    _mine = IM.is_book(_slot(zh, iid, cat))
    if _srv:
        ck(f"…服务端说能读 ⇒ `is_book` 不判 False（{en}）", _mine is not False,
           f"is_book={_mine!r}（CAN_MAYBE 可以、False 不行）")
    else:
        ck(f"…服务端说不能读 ⇒ `is_book` 也不是 True（{en}）", _mine is not True, f"is_book={_mine!r}")

print("② 秘密纸条/日记残页（真机形态：没有 catNum）⇒ 有那一行")
for nm, iid in (("Secret Note", "(O)79"), ("Journal Scrap", "(O)842"),
                ("秘密纸条", None), ("日记残页", None)):
    _s = _slot(nm, iid, None)
    ck(f"…`is_book({nm})` is True", IM.is_book(_s) is True, repr(IM.is_book(_s)))
    ck(f"…`_read_can` 放行（＝那行会出现）", IM._read_can(None, _s) is True)

print("③ 工具不许被名字松判据捡回来")
ck("…名字带「书」但 id=(T)Hoe ⇒ False",
   IM.is_book(_slot("奇怪的书", "(T)Hoe", -99)) is False)
ck("…名字带 Way 但 id=(T)Axe ⇒ False",
   IM.is_book(_slot("Always Path", "(T)Axe", -99)) is False)

print("④ 老 DLL（没 catNum 没 id）⇒ CAN_MAYBE（不判死）")
ck("…锄头那件 ⇒ CAN_MAYBE", IM.is_book(_slot("锄头", None, None)) is IM.CAN_MAYBE)

print("⑤ 包办：`read` 动词挂 exec、执行器走 `/use mode=read`")
_src = io.open(os.path.join(HERE, "intent_menu.py"), encoding="utf-8").read()
_i = _src.index('Verb("read"')
ck("…`Verb(\"read\", …)` 带 `exec=_exec_read`", "exec=_exec_read" in _src[_i:_i + 300])
_j = _src.index("def _exec_read(")
_body = _src[_j:_src.index("\ndef ", _j + 10)]
ck("…`_exec_read` 走 `\"use\", {\"mode\": \"read\"}`（选格→用，不是自己写字段）",
   '"use", {"mode": "read"}' in _body, _body[:160])
ck("…失败时**如实报**（note_fail 带游戏的 r）", 'note_fail="游戏回：{r}"' in _body)

print()
if FAIL:
    print(f"❌ 失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("✅ 全过（0 失败）")
