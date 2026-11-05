# -*- coding: utf-8 -*-
"""🔧 升级工具的回执钉子 —— **升级不是"没买到"**。2026-11-05

来历（恒）：
  「刚刚升级工具被误报了『没买成但钱掉了』，**实际上是升级了的**。」

🩸 病根（这条钉子就是防它回来）：
  C# `/menu/click` 判成交靠 `bought` 计数 = "背包里多了几件"。可**升级工具**是
  **扣钱 + 扣铜锭 + 把工具从他手里收走** —— 包里只会少、永远不会多 ⇒ `bought` 恒为 0
  ⇒ 掉进"一件都没成交"那条，回「没买成」并把扣掉的 2000g 摊出来（真机 22:43:57 原文）：
      商店里没买成「铜喷壶」（一件都没成交，但**钱掉了 2000g**：17318 → 15318）
  而**同一屏状态条**上已经印着「🔧 铁匠铺有『铜喷壶』待取」—— 自己打自己的脸。

钉四条：
  ① 失败回包 + 钱掉了 + 这一刻**有工具在升级** ⇒ 必须改口成「升级受理」，不许说"没买成"
  ② 同理不许出现「花了钱但没进包」那种断言
  ③ **没有**工具在升级时，老那句话一个标点都不许变（别为了这条把真失败也放水）
  ④ C# 那边也修了（判据 `player.toolBeingUpgraded`）——源码钉住，防它被改回去

跑法：`python scripts/_tool_upgrade_receipt_selftest.py`（退出码 0 = 全绿）
"""
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import nagi_mcp_server as M  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_MONEY_ERR = ("商店里没买成「铜喷壶」（一件都没成交，但**钱掉了 2000g**：17318 → 15318）"
              "—— 别当没发生，回一句 check 核对钱和背包")


def _stub(tool_upgrading):
    """把 `menu_click` 需要的那一层全打成桩：状态 / /menu/click 回包 / 背景。"""
    M._ensure_background = lambda *a, **k: None
    M.api.state = lambda *a, **k: {
        "location": {"name": "Blacksmith"},
        "player": {"name": "Claude", "x": 3, "y": 15, "money": 15318,
                   "toolUpgrading": tool_upgrading},
        "activeMenu": {}, "inventory": [], "time": {},
    }
    M.api.menu_click = lambda **kw: {"ok": False, "error": _MONEY_ERR, "cost": 2000, "money": 15318}


def main():
    _state_save, _click_save = M.api.state, M.api.menu_click

    print("\n① 钱掉了 + 有工具在升级 ⇒ 说「升级受理」")
    _stub("铜喷壶")
    out = M.menu_click(item="铜喷壶")
    ck("回执说升级受理", "升级受理" in out, out[:200])
    ck("…点名被收走的那个工具", "铜喷壶" in out)
    ck("…明说「不是没买成」", "不是没买成" in out)
    ck("…**不再**出现「没买成」那套断言", "商店里没买成" not in out)
    ck("…也不出现「花了钱但没进包」", "花了钱但没进包" not in out)
    ck("…给了下一步（2 天后回来取）", "铁匠铺(柜台)" in out)

    print("\n② 没有工具在升级 ⇒ 老那句原样透传（别把真失败放水）")
    _stub(None)
    out2 = M.menu_click(item="铜喷壶")
    ck("还是报没买成", "商店里没买成" in out2, out2[:200])
    ck("…钱掉了的实数照旧摊出来", "钱掉了 2000g" in out2)
    ck("…没有冒充升级受理", "升级受理" not in out2)

    print("\n③ C# 那边的判据也修了（防回退）")
    _cs = io.open(os.path.join(os.path.dirname(HERE), "ModEntry.cs"),
                  encoding="utf-8", errors="replace").read()
    _blk = _cs.split("if (bought <= 0)")[1].split("int spentShop")[0]
    ck("判据认 `toolBeingUpgraded`", "toolBeingUpgraded" in _blk, _blk[:200])
    ck("…且先于「一件都没成交」那条判定", "upgraded = true" in _blk, _blk[:200])
    ck("…点之前记了一个基线（`toolUpgBefore`）", "toolUpgBefore" in _cs)

    M.api.state, M.api.menu_click = _state_save, _click_save

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 升级工具的扣钱是**对**的，别让回执把它说成没买到。")
        sys.exit(1)
    print("✅ 升级工具回执全绿：扣了钱 + 工具被收走 = 升级受理，不是没买到。")
    sys.exit(0)


if __name__ == "__main__":
    main()
