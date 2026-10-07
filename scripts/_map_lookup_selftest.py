# -*- coding: utf-8 -*-
"""🧪 `map lookup` 的**名字归一**（离线自验，不碰游戏）。

跑法: PYTHONIOENCODING=utf-8 python scripts/_map_lookup_selftest.py

为什么有它（恒 2026-10-07 让零背景 AI 试跑任务书）：
  AI 探路先敲 `map lookup 林间小径 / 幻觉神龛 / 木匠店 / 矿井` —— **四个全回"知识库没有"**，
  它当场把这几处当成"不存在"。病根：`map_lookup` 原来**直接把入参当键**（表里是英文图名），
  而 `map go` 那边认中文别名和 POI 名 ⇒ 两条路口径不一致。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import nagi_mcp_server as M          # noqa: E402  —— 安全：末尾才有 __main__ 守卫

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"   {extra}" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def main():
    print("① 中文场景别名 → 图名（AI 试跑时全栽在这一步）")
    for cn, want in (("林间小径", "Backwoods"), ("木匠店", "ScienceHouse"), ("矿井", "Mine"),
                     ("农场", "Farm"), ("酒吧", "Saloon"), ("沙漠", "Desert")):
        k, _x = M._lookup_canon(cn)
        ck(f"「{cn}」→ `{want}`", k == want, str(k))

    print("② POI 名 → 它所在那张图 + 怎么去")
    k2, x2 = M._lookup_canon("幻觉神龛")
    ck("「幻觉神龛」认到 `WizardHouseBasement`", k2 == "WizardHouseBasement", str(k2))
    _j = " ".join(x2)
    ck("……并告诉它**用 `map go` 过去**", "map go 幻觉神龛" in _j, _j[:120])
    ck("……还带上了那条 note（站位/交互）", "12,5" in _j, _j[:160])

    print("③ 英文图名原样通过（不许多加噪声）")
    k3, x3 = M._lookup_canon("Farm")
    ck("`Farm` → `Farm` 且**没有多余说明行**", k3 == "Farm" and x3 == [], str((k3, x3)))
    k4, x4 = M._lookup_canon("SeedShop")
    ck("`SeedShop` 同理", k4 == "SeedShop" and x4 == [], str((k4, x4)))

    print("④ 认不出 ⇒ 给**相近的名字**（不许一句「没有」就完事）")
    k5, x5 = M._lookup_canon("精")
    ck("半截名 `精` ⇒ 键 None 但**列出相近的**", k5 is None and any("精通" in s for s in x5), str(x5)[:120])
    k6, x6 = M._lookup_canon("完全不存在的词")
    ck("彻底认不出 ⇒ 也不炸（键 None）", k6 is None, str(k6))

    print("⑤ 端到端（桩掉游戏侧）：`map_lookup` 认中文名，且认不出时给「下一步」")
    _saved = {}
    for nm, val in (("_shop_hours_line", lambda *a, **k: ""),
                    ("_festival_now_data", lambda: {"ok": False}),
                    ("map_feature_hidden", lambda *a, **k: set()),
                    ("_festival_pois_here", lambda *a, **k: []),
                    ("_mini_obelisk_pair", lambda *a, **k: []),
                    ("_backpack_upgrade_poi", lambda *a, **k: ""),
                    ("_with_state", lambda s: s)):
        _saved[nm] = getattr(M, nm)
        setattr(M, nm, val)
    try:
        out = M.map_lookup("矿井")
        ck("`map_lookup 矿井` 出的是 **Mine** 的资料（不是「没有」）",
           out.startswith("🗺️ Mine") and "知识库没有" not in out, out[:120])
        out2 = M.map_lookup("查无此地")
        ck("认不出时**带下一步**（`map go` / `map query`）",
           "知识库没有" in out2 and "map go" in out2 and "map query" in out2, out2[:200])
        out3 = M.map_lookup("幻觉神龛")
        ck("`map_lookup 幻觉神龛` 也带得回来（POI 那一路）",
           "幻觉神龛" in out3, out3[:120])
    finally:
        for nm, val in _saved.items():
            setattr(M, nm, val)

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 项没过: {FAIL}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
