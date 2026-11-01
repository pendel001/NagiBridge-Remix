# -*- coding: utf-8 -*-
"""🧥 「戴不上帽子（不认中文），换英文就好了」 —— 纯 Python 自验（不碰游戏）。2026-11-01

## 恒原话
> 「新问题：**戴不上帽子（不认中文）**，后来换英文试好了」

## 病根（跟 2026-09-27 卖东西那次**同一个形状**）
C# 的 `/equip` 是 `item.Name.Equals(name)` —— **只认英文内部名**（`Straw Hat`），
而 AI 在状态条/背包里看到的是 **中文显示名**（`草帽`）⇒ 它照抄中文，永远 `背包没有 '草帽'`。
`wear()` 当时**没享受到** 09-27 给 `sell_to_shop()` 打的那次补丁。

## 钉死
  ① `草帽` ⇒ `Straw Hat`；② 英文小写/带空格也认；③ 带品质前缀（`[金]草帽`）也认；
  ④ 认不出**原样返回**（交给 C# 如实说"背包没有"，不许在这编名字）；
  ⑤ 端到端：`wear(name="草帽")` 发出去的 body 里必须是**内部名**；
  ⑥ 失败话术要点明"中文/英文都行"，别让 AI 自己瞎猜英文。
"""
import io
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []
POSTED = []
INV = [
    {"slotIndex": 3, "name": "Straw Hat", "displayName": "草帽", "stack": 1},
    {"slotIndex": 4, "name": "Smallmouth Bass", "displayName": "[金]小嘴鲈鱼", "stack": 2},
    {"slotIndex": 5, "name": "Boots", "displayName": "靴子", "stack": 1},
]


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_real = {"state": M.api.state, "_ai_post": M.api._ai_post, "with_state": M._with_state}
try:
    M.api.state = lambda **kw: {"inventory": INV}
    M._with_state = lambda s, force_full=False: s

    print("① 中文显示名 ⇒ 内部英文名")
    ck("…`草帽` ⇒ `Straw Hat`", M._internal_item_name("草帽") == "Straw Hat",
       M._internal_item_name("草帽"))
    ck("…`靴子` ⇒ `Boots`", M._internal_item_name("靴子") == "Boots", M._internal_item_name("靴子"))

    print("\n② 英文/大小写/空格也认")
    ck("…`straw hat` ⇒ `Straw Hat`", M._internal_item_name("straw hat") == "Straw Hat",
       M._internal_item_name("straw hat"))
    ck("…`  Straw   Hat ` ⇒ `Straw Hat`", M._internal_item_name("  Straw   Hat ") == "Straw Hat",
       M._internal_item_name("  Straw   Hat "))

    print("\n③ 带品质前缀也认（`/state` 的展示名就长这样）")
    ck("…`[金]小嘴鲈鱼` ⇒ `Smallmouth Bass`",
       M._internal_item_name("[金]小嘴鲈鱼") == "Smallmouth Bass", M._internal_item_name("[金]小嘴鲈鱼"))

    print("\n④ 认不出 ⇒ **原样返回**（不编名字）")
    ck("…`不存在的帽子` 原样回去", M._internal_item_name("不存在的帽子") == "不存在的帽子",
       M._internal_item_name("不存在的帽子"))
    ck("…空串原样回去", M._internal_item_name("") == "")

    print("\n⑤ 端到端：`wear(name=中文)` 发出去的 body 是**内部名**")
    def _fake_post(path, body=None, **kw):
        POSTED.append((path, body))
        return {"ok": True, "action": "on", "slot": "hat", "equipped": body.get("name"), "replaced": None}
    M.api._ai_post = _fake_post
    _out = M.wear(name="草帽")
    ck("…只打了一发 `/equip`", len(POSTED) == 1, str(POSTED))
    ck("…body.name == `Straw Hat`", (POSTED[0][1] or {}).get("name") == "Straw Hat", str(POSTED))
    ck("…回执说穿上了", "已穿上" in _out, _out)

    print("\n⑥ 失败话术：要点明「中文/英文都行」（别让 AI 猜英文）")
    M.api._ai_post = lambda path, body=None, **kw: {"ok": False, "error": "背包没有 '牛头帽'"}
    _bad = M.wear(name="牛头帽")
    ck("…如实报失败", "背包没有" in _bad, _bad)
    ck("…并给出「中文显示名/英文内部名都行」", "英文内部名" in _bad, _bad)

finally:
    M.api.state = _real["state"]
    M.api._ai_post = _real["_ai_post"]
    M._with_state = _real["with_state"]

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
