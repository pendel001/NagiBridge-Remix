# -*- coding: utf-8 -*-
"""钉子：缝纫机**侧边那一列（身上穿的三件）**的读＋写 —— 纯离线（读 C# 源码，不起服务、不碰游戏）。

恒 2026-10-06（红框那两张截图）：「**检查侧边槽有没有做**」。
查到的老形状：**读**只翻 `bagList`（`ModEntry.cs` 的 `/menu` tailor 分支）⇒ 身上三件**从不上单子**；
**写**（`/tailor_set`）永远是**直接写 `spot.item`** ⇒ 绕开游戏手势（恒真机见过"数据变了、界面没动/背包灰了"）。

游戏侧的真相（反编译 `TailoringMenu.cs`，c1615）：
  · 侧边三个图标名字就叫 `Hat`/`Shirt`/`Pants`（`:203-227`），悬停读的是
    `player.hat / shirtItem / pantsItem`（`:928-941`）；
  · **空光标点侧边图标** ⇒ 把那件抓到光标（`:435-445`/`:468-478`/`:501-511`，且游戏先过 `HighlightItems` 才抓）；
  · 再点料槽 ⇒ 进槽（`_leftIngredientSpotClicked`）；
  · `BuildHighlightCache()`（`:281-286`）建缓存时**就把这三件算进去了** —— 所以"能不能当料"不用另写判据。

钉六条：
  ① 读侧：`/menu` 的 tailor 分支**吐 `worn`**，且三件分别取 `hat`/`shirtItem`/`pantsItem`
  ② 读侧：那三件的"能不能当料"走**同一张** `ItemHighlightCache`（不是另写一把尺子）
  ③ 写侧：`/tailor_set` 认 `hat`/`shirt`/`pants`（含中文别名 帽子/衬衫/上衣/裤/裤子）
  ④ 写侧：**走游戏自己的点击** —— `receiveLeftClick(图标中心)` ⇒ `receiveLeftClick(料槽中心)`
  ⑤ 写侧：⛔ 那条路**不许**写 `spot.item`（只准读它判断成没成）
  ⑥ 写侧：抓不到 / 进不去都**如实报**（三段失败文案在）

跑：`python scripts/_tailor_side_selftest.py`
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_CS = io.open(os.path.join(os.path.dirname(HERE), "ModEntry.cs"), encoding="utf-8").read()

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _block(start_marker, end_marker, frm=0):
    """抠一段源码；end_marker 找不到就退化成"往后 6000 字"（免得钉子在注释改一个字就整根红）。"""
    i = _CS.index(start_marker, frm)
    try:
        j = _CS.index(end_marker, i)
    except ValueError:
        j = min(len(_CS), i + 6000)
    return _CS[i:j]


# ── 读侧：/menu 的 TailoringMenu 分支 ──────────────────────────────────────
_read = _block("else if (menu is StardewValley.Menus.TailoringMenu tm)", "worn = wornSide")
_read = _block("else if (menu is StardewValley.Menus.TailoringMenu tm)", "\n                else if (menu is ")
print("① 读侧：吐 `worn` 且三件取对了字段")
ck("…回包里有 `worn = wornSide`", "worn = wornSide" in _read)
ck("…取 `Game1.player.hat?.Value`", "Game1.player.hat?.Value" in _read)
ck("…取 `Game1.player.shirtItem?.Value`", "Game1.player.shirtItem?.Value" in _read)
ck("…取 `Game1.player.pantsItem?.Value`", "Game1.player.pantsItem?.Value" in _read)
ck("…`slot` 键是 hat/shirt/pants", 'AddWorn("hat"' in _read and 'AddWorn("shirt"' in _read
   and 'AddWorn("pants"' in _read)

print("② 读侧：判据 = **同一张** ItemHighlightCache（不另写尺子）")
_worn = _block("void AddWorn(string key, Item? it)", "AddWorn(\"hat\"")
ck("…`AddWorn` 里查的就是 `cache.Contains(it)`", "cache.Contains(it)" in _worn)
ck("…左右槽两位也从同一张表抠（LeftSlot/RightSlot）",
   "LeftSlot" in _worn and "RightSlot" in _worn)
ck("…⛔ 没在 C# 侧另编「什么能当料」的名单",
   not re.search(r"Data/TailoringRecipes|clothesType\s*==", _worn))

# ── 写侧：/tailor_set ──────────────────────────────────────────────────────
_set = _block("private object HandleTailorSet(HttpListenerContext ctx)", "\n    private object Handle")
print("③ 写侧：认 hat/shirt/pants（含中文别名）")
for _k in ('"hat" or "帽子"', '"shirt" or "衬衫" or "上衣"', '"pants" or "裤" or "裤子"'):
    ck(f"…别名表里有 {_k}", _k in _set)

print("④ 写侧：**走游戏自己的点击**（图标中心 → 料槽中心）")
ck("…点侧边图标：`tm.receiveLeftClick(icon.bounds.Center.X, icon.bounds.Center.Y)`",
   "tm.receiveLeftClick(icon.bounds.Center.X, icon.bounds.Center.Y)" in _set)
ck("…再点料槽：`tm.receiveLeftClick(spot.bounds.Center.X, spot.bounds.Center.Y)`",
   "tm.receiveLeftClick(spot.bounds.Center.X, spot.bounds.Center.Y)" in _set)
ck("…图标按**名字**找（Hat/Shirt/Pants）",
   "string.Equals(ic.name, key, StringComparison.OrdinalIgnoreCase)" in _set)

print("⑤ 写侧：⛔ 那条路不许写 `spot.item`")
_pw = _block("Func<string, ClickableComponent?, (bool ok, string err)> placeWorn", "var wornL = wornKeyOf(left)")
ck("…`placeWorn` 里**没有** `spot.item =`（只读它判成没成）",
   not re.search(r"spot\.item\s*=(?!=)", _pw),
   "又在直接写字段了 —— 那就是绕开游戏手势")
ck("…它只**读** `spot.item == null` 判断进没进", "spot.item == null" in _pw)

print("⑥ 写侧：失败都如实报（三段文案在）")
ck("…「游戏没把身上那件抓起来」那段在", "但游戏没把身上那件抓起来" in _set)
ck("…「抓起来了，但没进料槽」那段在", "抓起来了，但没进料槽" in _set)
ck("…「身上没穿」那段在", "身上没穿" in _set)

print()
if FAIL:
    print(f"❌ 失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("✅ 全过（0 失败）")
