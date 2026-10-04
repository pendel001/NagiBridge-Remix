# -*- coding: utf-8 -*-
"""🚪 菜单门禁复查（恒 2026-10-04 提醒：「**域变换了要检查一下菜单门禁**，别把新做的 intent
选项给拦了，或者把需要的操作给拦了」）。

## 这道闸门是干嘛的
`settings retire`/`_KEEP_TOOLS` 那套是"AI 能看见哪些工具"；**菜单门禁**是"**此刻**能不能动"：
`mods/.../nagi_mcp_server.py` 顶部猴补 `mcp.tool`，菜单（ShopMenu/ItemGrabMenu/GameMenu/Fishing
小游戏…）开着时**默认拒掉几乎所有工具**，只放行三张名单：
  · `_MENU_GATE_TOOLS_OK`        —— 整个**工具**放行（`menu`/`intent`/`check`/…）
  · `_MENU_GATE_DOMAINS_FULL_OK` —— 整个**域**放行（`settings`：捏脸/起名/核对全在那儿）
  · `_MENU_GATE_OPS_OK`          —— 指定域的**个别 op** 放行（`daily sleep`/`script continue`/`map warp_safe`…）
另有安全阀：同一工具+同一菜单**拒满 3 次第 4 次放行并明说**（防动画演出/检测异常卡死）。

## 为什么域一动就要复查（这次踩过的形态）
域收编 / op 改名 / 合并之后，这三张名单会**悄悄长歪**：
  · **死配置**：键还在，可那个域的 dispatch 里已经没有这条 op 了（rename/合并的产物）
  · **假门**：新做的 intent 选项**看得见按不动**（2026-09-30 真机：ShopMenu 开着时单子列着买/卖，
    而闸门把 `intent do` 整个挡掉 —— 所以 `intent` 现在整条放行，**前提**是 `_candidates` 菜单态
    只列 `menu_ok` 的动词 + `do_row` 兜旧号）
  · **拦错方向**：把"此刻真需要的那条操作"拦了（例：`script continue` 被拦 → 钓鱼小游戏每轮白烧一次）

⚠️ 这里钉的是**结构**（名单与 dispatch 一致、该放行的还在），不是真机行为 ——
   真机那条要看 `menu` 开着时敲 `intent show` / 敲目标 op（回归清单里那两条）。
"""
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nagi_mcp_server as M          # noqa: E402

fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


print("① 闸门的两条「整条/整域放行」必须还在（它们各自背着一条真机教训）")
ck("…`intent` 整条放行（否则菜单开着时单子**看得见按不动** = 假门）",
   "intent" in M._MENU_GATE_TOOLS_OK)
ck("…`settings` 整域放行（捏脸/起名/核对全在那儿，创建角色时菜单**必然开着**）",
   "settings" in M._MENU_GATE_DOMAINS_FULL_OK)
ck("…`menu`/`check`/`help` 放行（退出界面/查状态/看说明是菜单态的自救三件）",
   {"menu", "check", "help"} <= set(M._MENU_GATE_TOOLS_OK))

print("② `_MENU_GATE_OPS_OK` 里每条 (域, op) 必须真的在那个域的 dispatch 里（防改名/合并留下死配置）")
_raw = M._dispatch_keys()
_allkeys = {(d, k) for k, bucket in _raw.items() for (d, _fn) in bucket}
# ⚠️ `_dispatch_keys()` **故意只收 ≥2 字的键**（单字中文键会让 `help(随便说点啥)` 撞出域来，见它 docstring）
#    ⇒ 单字键（`睡`/`躺`/`停`/`看`）**没法用这条路校验**，跳过它们；剩下的一个都不许悬空。
_skip = {op for ops in M._MENU_GATE_OPS_OK.values() for op in ops if len(str(op)) < 2}
_stale = sorted(f"{d}:{op}" for d, ops in M._MENU_GATE_OPS_OK.items()
                for op in ops if len(str(op)) >= 2 and (d, op) not in _allkeys)
ck("…没有死配置（单字键跳过：`_dispatch_keys` 按设计不收）", not _stale,
   f"死键：{_stale}（单字键已跳过：{sorted(_skip)}）")

print("③ 域变换后**点名的两条**（这次动过的域/op 不许被闸门拦错）")
ck("…`settings` 整域放行 ⇒ 搬过去的 `heartbeat`（心跳间隔）菜单开着时也能调",
   "settings" in M._MENU_GATE_DOMAINS_FULL_OK)
ck("…`intent` 的**执行侧兜底**还在（`do_row` 挡旧号：号不跨屏，但 `_LAST_ROWS` 可能隔了一屏）",
   "do_row" in io.open(os.path.join(HERE, "intent_menu.py"), encoding="utf-8").read())
# ⚠️ `farm` 域**不在**任何放行名单里 = 菜单开着时 `farm harvest`/`farm doors` 一律被拦 ——
#    这是**对的**（握着一把 GUI 不能收菜/翻门）；写在这儿是为了"别哪天顺手把 farm 加进整域放行"。
ck("…`farm` **不该**整域放行（菜单开着不能收菜/翻门）",
   "farm" not in M._MENU_GATE_DOMAINS_FULL_OK
   and not (M._MENU_GATE_OPS_OK.get("farm") or set()))

print("")
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("✅ 菜单门禁复查通过（放行项在、无死配置、动过的域没被拦错）")
