"""🎣 「我哪里有在钓鱼」 —— 心跳"正在做什么"的钓鱼判据自验（不起服务、不碰游戏）。2026-09-25

恒真机：「**我哪里有在钓鱼**。——啊！怪不得 ai 说我在商店钓鱼。心跳是不是写成手持鱼竿就算了？」

他猜对了一半，实际情况更糟。旧判据是：

    is_fishing = p.get("fishing") is not None or "FishingRod" in tool
    if "FishingRod" in tool or is_fishing: return "🎣 正在钓鱼"

  ① `p.get("fishing") is not None` —— `/state` **永远**带这个字典（全是 false 的布尔）⇒ **恒为真**
     ⇒ 任何人只要没被前面几级（移动/菜单/拾取/背包）认出来，**一律**被播成"正在钓鱼"
     （恒站在商店里 = 上一句"在商店" + 这句 = "**在商店钓鱼**"）。
  ② `"FishingRod" in tool` —— 那是**类型名**；`/state` 的 currentTool 是 `Tool.Name`，
     实值 `Bamboo Pole`/`Training Rod`/…，**永远不含它** ⇒ 这块招牌从来没生效过，
     真正在起作用的只有 ① 那个恒真式。

这里钉住修好之后的行为：
  ① 拿着竿但线没下水 → **不是**钓鱼（恒的原始怀疑）
  ② 在商店里站着（拿不拿竿）→ 不是钓鱼
  ③ 真在钓（线在水里 / 收线中 / 甩竿中）→ 是钓鱼（别矫枉过正）
  ④ 前面几级该先答的还是先答（拾取/移动/菜单不被这条抢走）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import player_activity as PA

FAIL = []
FISH_OFF = {"isCasting": False, "isFishing": False, "isNibbling": False,
            "isReeling": False, "hit": False}


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def state(tool="Bamboo Pole", loc="Farm", fishing=None, moving=False, menu=None,
          in_bed=False, tod=900):
    p = {"name": "恒", "x": 10, "y": 10, "currentTool": tool, "currentItem": tool,
         "isMoving": moving, "health": 100, "maxHealth": 100,
         "stamina": 200, "maxStamina": 270, "isInBed": in_bed,
         "stationarySeconds": 999,
         "fishing": dict(FISH_OFF, **(fishing or {}))}
    st = {"player": p, "location": {"name": loc}, "inventory": [], "alerts": [],
          "time": {"timeOfDay": tod, "season": "spring", "dayOfMonth": 5}, "activeMenu": menu}
    return st


def act(**kw):
    return PA.describe_activity(state(**kw))


print("\n① 拿着竿、线没下水 → **不许**说在钓鱼（恒的原始怀疑）")
ck("农舍里拿着竹竿站着", "钓鱼" not in act(), act())
ck("商店里拿着竿站着（就是恒逮到的那句「在商店钓鱼」）",
   "钓鱼" not in act(loc="SeedShop"), act())

print("\n② 在商店里，拿不拿竿都不该说钓鱼")
ck("商店空手", "钓鱼" not in act(tool="", loc="SeedShop"), act())
ck("商店拿斧头", "钓鱼" not in act(tool="Axe", loc="SeedShop"), act())

print("\n③ 真在钓 → **必须**认出来（别矫枉过正）")
for label, kw in [("线在水里", {"isFishing": True}),
                  ("正在甩竿", {"isCasting": True}),
                  ("正在收线", {"isReeling": True})]:
    ck(label, "正在钓鱼" in act(fishing=kw), act(fishing=kw))
ck("…而且不挑地方（海滩上钓也认）", "正在钓鱼" in act(loc="Beach", fishing={"isFishing": True}))
ck("…就算手上不是竿（收线那一刻可能已换手）也认", "正在钓鱼" in act(tool="", fishing={"isReeling": True}))

print("\n④ 前面几级该先答的仍归它们（这条没有抢戏）")
ck("走路中 → 不算钓鱼（归「闲逛」那类）", "正在钓鱼" not in act(moving=True), act(moving=True))

print("\n⑤ 判据是**读那几个布尔**，不是「这个键在不在」")
st = state()
st["player"]["fishing"] = FISH_OFF                       # 字典在、全 false
ck("fishing 字典存在但全 false → 不是钓鱼", "正在钓鱼" not in PA.describe_activity(st))
st2 = state()
st2["player"].pop("fishing", None)                       # 字典整个不在
ck("…字典整个不在（老 DLL）也不崩", isinstance(PA.describe_activity(st2), str))
ck("…且同样不误报钓鱼", "正在钓鱼" not in PA.describe_activity(st2))

print("\n⑥ 💤 「赖床」的窗口 = **6:30 起、中午 12 点前**（恒 2026-09-25 两次定的）")
# ⚠️ 这段我栽过：修完钓鱼那条后我擅自把窗口收成 6:30~7:00，恒打回「10 点了怎么在发呆，
#    好像说是赖床才对吧」→ 量数据证明他真在床上（isInBed=True、那格 passable=false）。
#    接着他定了终点：「太短了，改成中午 12 点」。这几条钉住窗口的两个端点。
ck("06:10 刚醒那一刻在床上 → **不算**（人正好在床格上，正常）",
   "赖床" not in act(in_bed=True, tod=610), act(in_bed=True, tod=610))
ck("09:00 在床上 → **是赖床**", "赖床" in act(in_bed=True, tod=900), act(in_bed=True, tod=900))
ck("10:20 在床上（恒逮到那次）→ **还是赖床**",
   "赖床" in act(in_bed=True, tod=1020), act(in_bed=True, tod=1020))
ck("11:50 还没到中午 → **仍是赖床**（窗口终点是 12:00）",
   "赖床" in act(in_bed=True, tod=1150), act(in_bed=True, tod=1150))
ck("12:00 过了中午 → **不再叫赖床**（落到发呆）",
   "赖床" not in act(in_bed=True, tod=1200), act(in_bed=True, tod=1200))
ck("…躺在床上也不该被念成「在钓鱼」", "钓鱼" not in act(in_bed=True, tod=1020))
ck("…不在床上就不算（还是发呆）",
   "赖床" not in act(in_bed=False, tod=1020) and "发呆" in act(in_bed=False, tod=1020),
   act(in_bed=False, tod=1020))

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
