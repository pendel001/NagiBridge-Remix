"""📚 「包里有书 → 提示读」的判据 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-27(164)

**恒真机逮到的**：他自己说「你的海之宝石拿出来读呀，怎么放在箱子里」→ AI 取出来了，
**状态条一个字没提读**。查下去：
    `Jewels Of The Sea = (O)Book_Roe / catNum=-102 / category='书'`
游戏自己标得清清楚楚，而我们**攥着一份手写名字名单在猜**（`_BOOK_HINT_KEYS`）——
名单里 `Quarterly/Treatise/Cookbook/Monster/Seasonal/…` 一个关键字都不含 ⇒ 零命中 ⇒ 哑巴。
AI 只好去 `scene select` + `scene interact` 想"读"（那条路根本不对）。

修 = **判据改成问游戏**（`itemId`/`catNum`，C# light 背包新补的两个字段），
     名字名单**降级成旧 DLL 兜底**。这个文件把两件事都钉死：

  ① **正解**：游戏标了的（`catNum=-102` / `id` 前缀 `(O)Book_`）⇒ 认出，**哪怕名字完全陌生**
  ② **兜底**：旧 DLL（没这两个字段）⇒ 退回名单，**不比改动前差**
  ③ **新字段在场时，id 说了算** —— 别再让名单里那些松判据（单字「书」「Way」）把非书捡回来
  ④ 端到端：`_read_to_free_hint` 拿恒真机那件也能出提示；**同签名第二次仍不重播**（原有节流不能被破坏）
  ⑤ 阴性：普通物品（胡萝卜之类）一个字都不出
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_real = {n: getattr(M, n) for n in ("_with_state",)}

try:
    print("\n① 正解：游戏标了的就认（**哪怕名字完全陌生**）")
    # 恒真机那一件，字段照 /state 实报
    ck("Jewels Of The Sea（(O)Book_Roe / catNum=-102）→ 认出",
       M._is_readable_item("Jewels Of The Sea", "(O)Book_Roe", -102))
    ck("…光凭 catNum=-102 就认（名字是个瞎编的）",
       M._is_readable_item("随便什么怪名字", "", -102))
    ck("…光凭 id 前缀 (O)Book_ 就认（catNum 没给）",
       M._is_readable_item("Another Unknown Book", "(O)Book_Fishing", None))
    ck("…旧字段是字符串 id 也认", M._is_readable_item("X", "(O)Book_Roe", ""))

    print("\n② 兜底：旧 DLL 没这两个字段 ⇒ 退回名单，不比改动前差")
    ck("Combat Quarterly（老名单里的）→ 仍认出", M._is_readable_item("Combat Quarterly", "", None))
    ck("Secret Note（英文内部名）→ 仍认出", M._is_readable_item("Secret Note", "", None))
    ck("Journal Scrap → 仍认出", M._is_readable_item("Journal Scrap", "", None))
    ck("⚠️ 兜底时**认不出**的也没变坏：Jewels Of The Sea 无字段 → 认不出（这就是修之前的样子）",
       not M._is_readable_item("Jewels Of The Sea", "", None))

    print("\n③ 新字段在场时，**id 说了算** —— 别让名单的松判据把非书捡回来")
    ck("斧头（名字不含书，id 是工具）→ 不认", not M._is_readable_item("Axe", "(T)Axe", -99))
    ck("名字里带「书」但 id 是工具 → **不认**（名单会被 id 否掉）",
       not M._is_readable_item("奇怪的书", "(T)Hoe", -99))
    ck("名字里带 Way 但 id 是工具 → 不认", not M._is_readable_item("Always Path", "(T)Axe", -99))
    ck("…但 id 没给时，名单照旧能认（兜底权还在）",
       M._is_readable_item("Combat Quarterly", "", -99))

    print("\n④ 端到端：拿 AI 真机背包（含海之宝石）打 _read_to_free_hint")
    # 照 session_log 里 /state 实报的口径
    BAG = [
        {"name": "Axe", "stack": 1, "itemId": "(T)Axe", "catNum": -99},
        {"name": "Carrot", "stack": 3, "itemId": "(O)Carrot", "catNum": -75},
        {"name": "Smallmouth Bass", "stack": 4, "itemId": "(O)137", "catNum": -4},
        {"name": "Jewels Of The Sea", "stack": 2, "itemId": "(O)Book_Roe", "catNum": -102},
    ]
    M._SECRET_NOTE_TRACK["sig"] = None
    out = M._read_to_free_hint({"inventory": BAG})
    ck("恒真机那一件 → **出提示了**（修之前这里是空串）", "海之宝石" in out or "Jewels Of The Sea" in out, out)
    ck("…提示里给的是**能直接照抄**的 op（menu read_book）", "menu read_book" in out, out)
    ck("…工具（斧头）没被算进去", "Axe" not in out and "斧头" not in out, out)
    ck("…同签名第二次 → 不重播（原有节流没被破坏）", M._read_to_free_hint({"inventory": BAG}) == "")

    M._SECRET_NOTE_TRACK["sig"] = None
    ck("⑤ 阴性：包里只有普通物品 → 一个字都不出",
       M._read_to_free_hint({"inventory": BAG[:3]}) == "",
       M._read_to_free_hint({"inventory": BAG[:3]}))

    print("\n⑤b 中文名纸条 + 英文内部名（游戏 `i.Name` 是**内部英文名**，实测 `Smallmouth Bass`）")
    M._SECRET_NOTE_TRACK["sig"] = None
    out = M._read_to_free_hint({"inventory": [{"name": "Secret Note", "stack": 1}]})
    ck("Secret Note（无字段，旧 DLL 情形）→ 照旧出提示", "秘密纸条" in out, out)

finally:
    for k, v in _real.items():
        setattr(M, k, v)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
