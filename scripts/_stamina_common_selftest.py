"""⚡ 低体力线（锄/浇/砍/敲/钓 全族）—— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒：①「耕种相关（**锄/浇**）也要记得加低体力保护，**也是低过 20 都停**」
    ②「做成模块的话应该挺方便的了，**探到使用武器/镰刀之外的工具都可以检测**」

测四件：
  ① 这条线**全局只有一份**（fish_run / chop_trees / server / water_crops 都指到 stamina_common，
     谁也别再写死一个 20 —— "四份不一样"正是这模块要收掉的东西）
  ② **武器/镰刀不拦**（挥剑打架时停手 = 挨打）；认得出来的费体力工具才拦
  ③ 读不到体力 **不拦**（读不到 ≠ 没体力；别把"我瞎了"变成"路不通"）
  ④ 停手文案**必须带下一步**（恒的规矩：报错必须给下一步，别只报"停了"）
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stamina_common as S

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


print("\n① 全局只有一份（各处 import 到的是**同一个 int 对象**）")
import fish_run as F
import chop_trees as C
import nagi_mcp_server as M
ck("fish_run.MIN_STAMINA is stamina_common.MIN_STAMINA", F.MIN_STAMINA is S.MIN_STAMINA)
ck("chop_trees.STAMINA_RESERVE is stamina_common.MIN_STAMINA", C.STAMINA_RESERVE is S.MIN_STAMINA)
ck("值是 20（恒的口径）", S.MIN_STAMINA == 20, str(S.MIN_STAMINA))

print("\n② 武器/镰刀**不拦**；费体力的工具才拦（恒点的那条判据）")
for w in ("Rusty Sword", "Lava Katana", "Galaxy Sword", "Scythe", "Golden Scythe",
          "Iridium Scythe", "Wood Club", "Dwarf Dagger", "Slingshot"):
    ck(f"「{w}」（武器/镰刀）→ 不拦", S.tool_costs_stamina(w) is False, str(S.tool_costs_stamina(w)))
for t in ("Hoe", "Iridium Hoe", "Pickaxe", "Axe", "Golden Axe", "Watering Can",
          "Iridium Watering Can", "Pan", "Milk Pail", "Shears", "锄头", "喷壶"):
    ck(f"「{t}」（费体力）→ 拦", S.tool_costs_stamina(t) is True, str(S.tool_costs_stamina(t)))
ck("认不出的东西 → **不拦**（宁可漏拦，也不能把挥剑挡成没体力）",
   S.tool_costs_stamina("Mystery Thing") is False and S.tool_costs_stamina("") is False)

print("\n③ 读不到体力不拦（读不到 ≠ 没体力）")
ck("cur=None → 不算低", S.is_low(None) is False)
ck("cur=19 → 低", S.is_low(19) is True)
ck("cur=20 → 不低（**低过** 20 才停，正好 20 还能干）", S.is_low(20) is False)
ck("cur='x'（脏数据）→ 不拦", S.is_low("x") is False)

print("\n④ 停手文案带下一步 + 报清剩多少")
n = S.stop_note(12, 270, "剩 7 格没锄")
ck("说了当前/上限", "12/270" in n, n)
ck("说了低于多少停", "低于 20" in n, n)
ck("说了剩多少", "剩 7 格没锄" in n, n)
ck("**给了下一步**（吃/温泉/睡）", "eat" in n and "温泉" in n and "sleep" in n, n)
nb = S.tool_block_note(9, 270, "Axe", "砍这棵树")
ck("工具版还把工具名/干嘛写清楚", "Axe" in nb and "砍这棵树" in nb, nb)
ck("…并点明武器/镰刀不在此列（AI 别以为被锁死）", "武器" in nb and "镰刀" in nb, nb)
ck("读不到上限也不崩", "?" in S.stop_note(12, None), S.stop_note(12, None))

print("\n" + ("=" * 46))
print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
