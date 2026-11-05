# -*- coding: utf-8 -*-
"""🏪 商店「今天休息」钉子 —— 别把开着的店喊成休息。2026-11-05

来历（恒）：
  「我发现铁匠铺**天天都显示休息**，你有什么头绪吗。感觉**很可能误判了**，克林特明明在店里。」
  ——他猜对了。

🩸 病根（这条钉子就是防它回来）：
  `calendar_data.get_closed_shops_today_v2()` 里 `4: ["铁匠铺 (Clint)"]  # 周五` 是**硬算**的。
  可中文维基[铁匠铺]写的是：**非营业时间 = 社区中心重建后的每个星期五** ⇒ 周五休息**有前置**。
  ⇒ **年1（社区中心没修）的每个周五都在冤枉它**；`_im_clint_open()` 读的是同一张表，
     于是「🪨 砸晶球（铁匠铺柜台前）」那行也跟着在周五消失 —— 一句话错，两处功能一起哑。

钉五条：
  ① 社区中心**没重建** ⇒ 周五**不报**铁匠铺休息（年1 的常态）
  ② 社区中心**已重建** ⇒ 周五**要报**
  ③ 状态**读不到** ⇒ 也不报（不编"关门"这种话让它白躲）
  ④ 别的店**不受牵连**（周一木匠 / 周三皮埃尔 / 周六鱼店照旧）
  ⑤ 判据来源钉死：`_cc_done()` 认的是 `/progress` 的 `mail` 里的 `ccIsComplete`，
     **不是** `/unlocks.communityCenter`（那个是"开锁"，开局就 True ⇒ 拿它判必然永远误判）

跑法：`python scripts/_shop_closed_selftest.py`（退出码 0 = 全绿）
"""
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import calendar_data as C  # noqa: E402

FAIL = []
FRI, MON, WED, SAT = 4, 0, 2, 5


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def main():
    print("\n① 社区中心没重建（年1 常态）⇒ 周五铁匠铺**不该**报休息")
    _no = C.get_closed_shops_today_v2(FRI, cc_done=False)
    ck("周五 + cc_done=False ⇒ 列表里没有铁匠铺", not any("铁匠铺" in c for c in _no), str(_no))
    ck("…状态条那行也就不会印「🏪休:」",
       "🏪休" not in C.build_calendar_line("spring", 12, FRI, 1, cc_done=False),
       C.build_calendar_line("spring", 12, FRI, 1, cc_done=False))

    print("\n② 社区中心已重建 ⇒ 周五**要**报（维基那条前置）")
    _yes = C.get_closed_shops_today_v2(FRI, cc_done=True)
    ck("周五 + cc_done=True ⇒ 有铁匠铺", any("铁匠铺" in c for c in _yes), str(_yes))
    ck("…状态条印「🏪休: 铁匠铺 (Clint)」",
       "铁匠铺" in C.build_calendar_line("winter", 5, FRI, 1, cc_done=True))

    print("\n③ 读不到（None）⇒ 也不报（不许凭空喊关门）")
    _unk = C.get_closed_shops_today_v2(FRI, cc_done=None)
    ck("周五 + cc_done=None ⇒ 没有铁匠铺", not any("铁匠铺" in c for c in _unk), str(_unk))

    print("\n④ 别的店不受牵连")
    ck("周一 ⇒ 木匠店", any("木匠" in c for c in C.get_closed_shops_today_v2(MON)), "")
    ck("周三 ⇒ 皮埃尔", any("皮埃尔" in c for c in C.get_closed_shops_today_v2(WED)), "")
    ck("周六 ⇒ 鱼店", any("鱼店" in c for c in C.get_closed_shops_today_v2(SAT)), "")
    ck("周三不受 cc_done 影响（两种都报皮埃尔）",
       C.get_closed_shops_today_v2(WED, cc_done=True) == C.get_closed_shops_today_v2(WED, cc_done=False))
    ck("周四/周日本来就没人休息", not C.get_closed_shops_today_v2(3) and not C.get_closed_shops_today_v2(6))

    print("\n⑤ 判据来源（读错 flag 这门课就白修了）")
    # ⚠️ 只扫**执行代码里的字符串常量**（ast：跳过 docstring）——
    #    否则我这段解释性注释/文档里提到那两个词，钉子就会自己咬自己。
    import ast
    _src = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8", errors="replace").read()
    _fn = next(n for n in ast.walk(ast.parse(_src))
               if isinstance(n, ast.FunctionDef) and n.name == "_cc_done")
    _body = list(_fn.body)
    if _body and isinstance(_body[0], ast.Expr) and isinstance(_body[0].value, ast.Constant) \
            and isinstance(_body[0].value.value, str):
        _body = _body[1:]                      # 去掉 docstring
    _strs = [n.value for st in _body for n in ast.walk(st)
             if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    ck("`_cc_done` 读的是 `/progress`", "/progress" in _strs, str(_strs))
    ck("…认的 flag 是 `ccIsComplete`", "ccIsComplete" in _strs, str(_strs))
    ck("…**代码里没有**拿 `/unlocks` 那条 `communityCenter` 当已重建判据",
       not any("/unlocks" in s or "communityCenter" in s for s in _strs), str(_strs))
    _svs = _src
    ck("`_calendar_line` 把 cc_done 传下去了", "cc_done=_cc_done()" in _svs)
    ck("`_im_clint_open` 也传了（砸晶球那行同源）",
       "get_closed_shops_today_v2(day_index, cc_done=_cc_done())" in _svs)
    ck("`build_calendar_line` 的签名带 cc_done", "cc_done=None" in
       io.open(os.path.join(HERE, "calendar_data.py"), encoding="utf-8", errors="replace").read())

    print("\n⑥ 真机取到的那条 flag 真在 /progress 的原表里（key 名别记错）")
    ck("键名是 `mail`（不是 `mailReceived`）—— 2026-11-05 实测 /progress 回的键",
       True)   # 记录性质：实测 `/progress` 回 {ok, location, areas, members, mail, mailCount, events, eventCount}

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 「开着的店被喊成休息」会让 AI 白躲一整天，别改这条钉子去凑。")
        sys.exit(1)
    print("✅ 商店休息日全绿：只有社区中心重建后，周五才许喊铁匠铺休息。")
    sys.exit(0)


if __name__ == "__main__":
    main()
