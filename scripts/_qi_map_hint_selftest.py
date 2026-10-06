# -*- coding: utf-8 -*-
"""钉子：「神秘的齐」纸条链的**按图注入**（恒 2026-10-06 点的）。

恒原话：
  「**神秘的齐那四个阶段也是**：在**隧道**时**没有完成过**神秘的齐任务就报那个坐标和物品提示
   （我记得做过了，但不记得**注入条件**了，请核实）」

核实到的老形状：坐标全有（`locations.py:340-343`）、进度全有（`_qi_chain_hint()` 按 `/mail` 的 `TH_*` 阶梯），
但**注入点只在"打开那张任务卡"那条路**上；而 `Tunnel` 这张图连 `🗺️ 可:` 整行都被跳过
⇒ **人在隧道里什么都不报**。本钉子钉住新加的那一路（按图注入）。

钉六条：
  ① `_QI_STEPS` 五步、图名顺序对（Tunnel→Railroad→ManorHouse→Desert→Farm）
  ② 当前步那张图上 ⇒ 报（且带**该手持的物品 id ＋ 目标坐标**）
  ③ ⛔ 不是当前步的图 ⇒ 不报（别在镇上也念叨隧道的事）
  ④ **已完成**（`TH_LumberPile`）⇒ **哪张图都不报**
  ⑤ 读不到 `/mail` ⇒ **不报**（宁缺勿编，别凭猜让 AI 去放电池）
  ⑥ 与 `_qi_chain_hint()` **两边文字防漂移**：同一步下两处提到的数字（物品 id／坐标）必须一致

跑：`python scripts/_qi_map_hint_selftest.py`
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, HERE)
import nagi_mcp_server as M   # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_real = (M.api._get,)

print("① 台阶表")
ck("…`_QI_STEPS` 五步、图名顺序 = Tunnel→Railroad→ManorHouse→Desert→Farm",
   [t[0] for t in M._QI_STEPS] == ["Tunnel", "Railroad", "ManorHouse", "Desert", "Farm"],
   str([t[0] for t in M._QI_STEPS]))
ck("…`_qi_map_hint` 存在", callable(getattr(M, "_qi_map_hint", None)))

try:
    def _stub_mail(recv):
        M.api._get = lambda path, params=None: ({"received": [{"id": r} for r in recv]}
                                               if path == "/mail" else {})

    print("② 当前步那张图 ⇒ 报（含物品 id ＋ 坐标）")
    _cases = [
        ([],                                   "Tunnel",     "787",  "(17,6)"),
        (["TH_Tunnel"],                        "Railroad",   "394",  "(45,40)"),
        (["TH_Tunnel", "TH_Railroad"],         "ManorHouse", "甜菜", "(9,4)"),
        (["TH_Tunnel", "TH_Railroad", "TH_MayorFridge"], "Desert", "768", "(9,36)"),
        (["TH_Tunnel", "TH_Railroad", "TH_MayorFridge", "TH_SandDragon"], "Farm", "木材堆", ""),
    ]
    for recv, loc, item, coord in _cases:
        _stub_mail(recv)
        h = M._qi_map_hint(loc)
        ck(f"…{loc or '?'}（进度 {len(recv)} 步）报出 {item}", bool(h) and item in h, repr(h))
        if coord:
            ck(f"…{loc} 那句里带坐标 {coord}", coord in h, repr(h))

    print("③ ⛔ 不是当前步的图 ⇒ 不报")
    _stub_mail([])
    ck("…没进度、人在 Town ⇒ 不报", M._qi_map_hint("Town") == "", repr(M._qi_map_hint("Town")))
    _stub_mail(["TH_Tunnel"])
    ck("…①b 阶段、人在 Tunnel ⇒ 不报（当前步是 Railroad）",
       M._qi_map_hint("Tunnel") == "", repr(M._qi_map_hint("Tunnel")))

    print("④ 已完成（领过会员卡）⇒ 哪张图都不报")
    _done = ["TH_Tunnel", "TH_Railroad", "TH_MayorFridge", "TH_SandDragon", "TH_LumberPile"]
    _stub_mail(_done)
    _bad = [m for m, _t in M._QI_STEPS if M._qi_map_hint(m)]
    ck("…`TH_LumberPile` 在手 ⇒ 五张图全不报", _bad == [], str(_bad))

    print("⑤ 读不到 /mail ⇒ 不报（宁缺勿编）")
    def _boom(path, params=None):
        raise RuntimeError("net down")
    M.api._get = _boom
    ck("…抛错 ⇒ 空串", M._qi_map_hint("Tunnel") == "", repr(M._qi_map_hint("Tunnel")))

    print("⑥ 与 `_qi_chain_hint()` 两边防漂移（同一步的数字必须一致）")
    import re
    for i, recv in enumerate([[], ["TH_Tunnel"], ["TH_Tunnel", "TH_Railroad"],
                              ["TH_Tunnel", "TH_Railroad", "TH_MayorFridge"],
                              ["TH_Tunnel", "TH_Railroad", "TH_MayorFridge", "TH_SandDragon"]]):
        _stub_mail(recv)
        _mine = set(re.findall(r"\d+", M._QI_STEPS[i][1]))
        _theirs = set(re.findall(r"\d+", M._qi_chain_hint()))
        ck(f"…第 {i + 1} 步两处数字一致（物品 id／坐标没写岔）",
           _mine == _theirs or (_mine and _mine <= _theirs), f"我的={sorted(_mine)} 卡提示={sorted(_theirs)}")
finally:
    (M.api._get,) = _real

print()
if FAIL:
    print(f"❌ 失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("✅ 全过（0 失败）")
