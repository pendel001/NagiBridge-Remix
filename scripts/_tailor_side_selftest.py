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
_pw = _block("Func<string, ClickableComponent?, (bool ok, string via, string err)> placeWorn", "var wornVia = new List<string>()")
ck("…`placeWorn` 里**没有** `spot.item =`（只读它判成没成）",
   not re.search(r"spot\.item\s*=(?!=)", _pw),
   "又在直接写字段了 —— 那就是绕开游戏手势")
ck("…它只**读** `spot.item == null` 判断进没进", "spot.item == null" in _pw)

print("⑤b 写侧：点击没生效时的**退路**（恒真机：点图标没把裙子抓起来）")
ck("…先判游戏自己的 `HighlightItems(worn)` 才敢抓", "tm.HighlightItems(worn)" in _pw)
ck("…用游戏自己的抓取 helper（`PerformSpecialItemGrabReplacement`）",
   "Utility.PerformSpecialItemGrabReplacement(worn)" in _pw)
ck("…调游戏自己的槽方法（`_leftIngredientSpotClicked`/`_rightIngredientSpotClicked`，反射）",
   "_leftIngredientSpotClicked" in _pw and "_rightIngredientSpotClicked" in _pw)
ck("…沿 `:404-421` 那条「原本穿在身上 ⇒ 放进去就脱下来」",
   "Game1.player.IsEquippedItem(spot.item)" in _pw and "Game1.player.Equip(null" in _pw)
ck("…`via` 如实报**是哪一档成的**（click / method）",
   '"click"' in _pw and '"method"' in _pw and "wornVia.Add(" in _set)

print("⑤c 读侧：把**图标坐标 + 菜单位置**吐出来（诊断「点击打没打到」）")
ck("…`worn` 每条带 `iconX`/`iconY`", "iconX = icx" in _read and "iconY = icy" in _read)
ck("…`tailor` 里带 `menuAt`（菜单自己的位置/尺寸）", "menuAt = new {" in _read)

print("⑥ 写侧：失败都如实报（三段文案在）")
ck("…「点了侧边图标没反应」那段（含 HighlightItems 的原话）在",
   "点了侧边图标没反应" in _set and "HighlightItems" in _set)
ck("…「抓起来了，但没进料槽」那段在", "抓起来了，但没进料槽" in _set)
ck("…「身上没穿」那段在", "身上没穿" in _set)

print("⑦ 🚨 `heldItem` 是**属性**不是字段（MenuWithInventory.cs:14/44）—— 反射读字段恒 null")
_gh = _block("private static Item? GetMenuHeldItem(IClickableMenu menu)", "private static void SetMenuHeldItem")
_sh = _block("private static void SetMenuHeldItem(IClickableMenu menu, Item? item)", "\n    /// <summary>\n    /// POST /menu_close")
ck("…读侧走 `GetProperty(\"heldItem\"`", 'GetProperty("heldItem"' in _gh)
ck("…读侧还兜 `_heldItem`（私有 backing field，`GetField` 不穿基类 ⇒ 自己走 BaseType）",
   '_heldItem", F' in _gh and "BaseType" in _gh)
ck("…写侧**同顺序**（字段 → 属性 → `_heldItem`），否则会「读得到、写不回」",
   'GetProperty("heldItem"' in _sh and '_heldItem", F' in _sh and "BaseType" in _sh)

print("⑧ 🎽 `clear` 不许把**身上那件**再塞进背包（恒真机当场被复制出一条长裙）")
_clr = _block('if (action == "clear")', "// 📥 放料：先校验")
ck("…先判 `Game1.player.IsEquippedItem(it)`", "IsEquippedItem(it)" in _clr)
ck("…装备着的那件：只清槽、**不进背包**（文案点明「留在身上」）", "留在身上" in _clr)
ck("…非装备的照旧进背包／掉脚边（老路没被砍）", "addItemToInventoryBool(it)" in _clr
   and "createItemDebris(it" in _clr)

print()
if FAIL:
    print(f"❌ 失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("✅ 全过（0 失败）")
