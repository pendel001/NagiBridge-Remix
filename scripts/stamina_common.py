"""⚡ 低体力线 —— **全局唯一一处**。

恒 2026-09-24：「耕种相关（**锄/浇**）也要记得加低体力保护，**也是低过 20 都停**。」
（鱼脚本早就有这条线：`fish_run` 的监控循环 `stamina < 20` 收手。）

**为什么要单开一个模块**：这条线同时被下面几处用
  · `nagi_mcp_server`（锄地逐格 / 播种逐格 / 开钓闸门 / map_go 之后的各种农活）
  · `water_crops.py`（浇水，逐簇 tool_area）
  · `fish_run.py`（钓鱼，监控循环）
分散在各处各写一个 `20` ⇒ 迟早四份不一样（本项目最不缺这种坑：
"判据只有一份"是 CLAUDE.md 级的规矩）。

⚠️ **别把它塞进 `stardew_api`**（曾经想过）：那会让 `fish_run` 这种
「自带 urllib、压根不碰 stardew_api」的脚本，为了一个数字去 import 它 ——
而它 **import 期就把 `BASE_URL` 固化成默认 `7842`（恒）**。
哪天有人在那种脚本里顺手用一下 `api.xxx`，就直接打在**恒的角色**上了
（2026-09-23 因为同一类事把恒传走四次）。这个模块**零依赖、零副作用**，谁都能安全 import。
"""

MIN_STAMINA = 20

# 低于这条线就停手时，给 AI 的**下一步**（别只报"停了"——报错必须给下一步）
STOP_HINT = ("先补体力：`daily(ops=\"eat\", kw={\"name\": \"<背包里的食物>\"})` / "
             "泡温泉（`map go 温泉`）/ `daily(ops=\"sleep\")` 过夜")


# 🛠️ 用起来**费体力**的工具（按名字认的**白名单**）。
#    ⚠️ 为什么是白名单不是黑名单：**认不出的工具一律不拦** —— 宁可漏拦，也不能把
#       "挥剑打架"挡成"你没体力了"（战斗中停手 = 挨打）。武器/镰刀因此天然落在名单外，
#       恒 2026-09-24 点的那条（「武器/镰刀之外的工具都检测」）就是这个方向。
#    ⚠️ 局限：SDV 里**武器里也有一把叫 "Axe" 的**（棍类），名字会撞车 ⇒ 极端情况下可能误拦一次挥击。
#       要根治得让 C# `/state` 多报一个 `currentToolKind`（`MeleeWeapon`/`Hoe`/…，**按类型不按名字**）——
#       已记进 C# 批次（要关一次游戏），在那之前用这份名字表。
_STAMINA_TOOLS = ("Hoe", "Pickaxe", "Axe", "Watering Can", "WateringCan", "Pan",
                  "Milk Pail", "Shears", "锄", "镐", "斧", "喷壶", "水壶", "铜锅", "淘盘", "挤奶桶", "剪刀")


def tool_costs_stamina(tool_name) -> bool:
    """这个工具用起来费体力吗？（认不出 → **False**，即不拦，见 `_STAMINA_TOOLS` 的注释）"""
    n = str(tool_name or "")
    if not n:
        return False
    return any(k.lower() in n.lower() for k in _STAMINA_TOOLS)


def tool_block_note(cur, mx, tool_name: str, what: str = "") -> str:
    """用工具前的低体力拦截文案（`what` = 这次要干嘛，如"砍这棵树"）。

    ⚠️ 别在开头再加一个 ⚡：`stop_note()` 自己就以 ⚠️ 开头，叠出来是「⚡ ⚠️ 体力…」
    （真机验的那次当场看到，两个图标打架）。
    """
    return (stop_note(cur, mx, f"这次要用「{tool_name}」{('去' + what) if what else ''}，**没动手**")
            + f"\n  要用武器/镰刀不在此列（那些不费体力，随时可以挥）。")


def is_low(cur) -> bool:
    """当前体力低于停手线吗？`cur` 读不到（None）→ **False**（读不到 ≠ 没体力，不许拦路）。"""
    try:
        return cur is not None and float(cur) < MIN_STAMINA
    except Exception:
        return False


def stop_note(cur, mx, left_note: str = "") -> str:
    """停手那行文案（四处共用一份措辞，别各自造句）。`left_note` = 还剩多少没做完。"""
    _c = int(cur) if isinstance(cur, (int, float)) else "?"
    _m = int(mx) if isinstance(mx, (int, float)) else "?"
    return (f"⚠️ 体力 {_c}/{_m} 低于 {MIN_STAMINA} → **停手**，剩下的没干"
            + (f"（{left_note}）" if left_note else "") + f"。{STOP_HINT}")
