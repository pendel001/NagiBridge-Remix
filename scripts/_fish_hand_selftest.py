"""🎣 「手上没竿就自己装回去」 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-25

恒真机：「钓鱼的 continue 好像没有切换回手持工具导致钓鱼不成功」。

病根**不在 continue**，在整条路上没人再看手上的东西：`fish_run` 只在开局 `select` 一次竿，
之后 AI 在异步窗口里干的事会把它挤掉 —— 最典型的是 `daily eat`（`/eat` 吃的**就是手持那一格**，
吃完那格空了 → `CurrentTool` 变 null → 鱼机抛不出去），而脚本照样一句一句打 `check: …`，
看上去跟正常没两样（恒是从**画面上**看出来的）。

所以这里要钉死三件事：
  ① 判据分得清 **空手 / 别的工具 / 拿着物品** —— 前两个才该抢竿，**"拿着物品"一动不动**
     （那多半是 AI 正要把这东西吃掉，抢了它就吃不成了）
  ② 小游戏(`BobberBar`)开着时不动手；竿真不在包里时**如实报 missing**，别静默空转
  ③ 这个函数**真的挂在监控循环上**（写了个没人调的函数 = 白写）
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fish_run as F

FAIL = []
RODS = ["Iridium Rod", "Fiberglass Rod", "Training Rod", "Bamboo Pole"]

# (说明, player 字典, 期望的 hand_kind, 期望是否该抢竿)
CASES = [
    ("拿着竿", {"currentTool": "Bamboo Pole", "currentItem": "Bamboo Pole"}, "rod", False),
    ("拿着别的工具", {"currentTool": "Axe", "currentItem": "Axe"}, "tool", True),
    ("**空手**（吃完东西那格空了）", {"currentTool": None, "currentItem": None}, "empty", True),
    ("**拿着要吃的**（Field Snack）", {"currentTool": None, "currentItem": "Field Snack"}, "item", False),
    ("拿着钓上来的鱼", {"currentTool": None, "currentItem": "Sunfish"}, "item", False),
    ("升过级的竿（靠 rod 认出来）", {"currentTool": "Iridium Rod", "currentItem": "Iridium Rod"}, "rod", False),
    ("缺字段（老 /state 没有这两个键）", {}, "empty", True),
]


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeBot:
    """假 NAGI 客户端 —— 只记"被要求装什么竿"，一个网络包都不发。"""

    def __init__(self, ok=True):
        self.picked = []
        self.ok = ok

    def select(self, name):
        self.picked.append(name)
        return {"ok": self.ok}


print("\n① 手上是什么 —— 四种状态要分得清")
for label, p, want, _should in CASES:
    got = F.hand_kind(p, RODS)
    ck(f"{label} → {want}", got == want, f"得到 {got}")

print("\n② 该抢的抢、不该抢的一动不动")
for label, p, _want, should in CASES:
    b = FakeBot()
    r = F.ensure_rod(b, p, RODS)
    if should:
        ck(f"{label} → 抢竿（装上第一个能装的）", r == f"ok:{RODS[0]}" and b.picked == [RODS[0]],
           f"{r} {b.picked}")
    else:
        ck(f"{label} → **一动不动**", r == "" and b.picked == [], f"{r} {b.picked}")

print("\n③ 小游戏(BobberBar)开着时不动手")
b = FakeBot()
r = F.ensure_rod(b, {"currentTool": None, "currentItem": None}, RODS, has_menu=True)
ck("has_menu=True → 不 select（那是游戏自己的菜单）", r == "" and b.picked == [], f"{r} {b.picked}")

print("\n④ 竿不在背包里 → 如实报 missing（别静默空转）")
b = FakeBot(ok=False)
r = F.ensure_rod(b, {"currentTool": None, "currentItem": None}, RODS)
ck("select 全失败 → missing", r == "missing", r)
ck("…而且**试过了所有竿**（不是试一个就放弃）", b.picked == RODS, str(b.picked))

print("\n⑤ 它真的挂在监控循环上（不是写了个没人调的函数）")
_src = inspect.getsource(F.run)
ck("监控循环里调了 ensure_rod(...)", "ensure_rod(" in _src)
ck("…'missing' 只喊一次（别每 2 秒刷一行）", "_rod_gone_logged" in _src)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
