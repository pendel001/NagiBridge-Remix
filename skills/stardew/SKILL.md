---
name: stardew
description: 在星露谷里替玩家玩 NagiBridge（MCP 端）。当用户说"帮我浇水/下矿/钓鱼/去镇上/看看背包"这类游戏内请求，或要你接管一个存档时用。只在玩家局用 MCP 的 16 个工具，别直连端点。
---

# 🌾 NagiBridge 星露谷 · agent 端玩法

> 你手上有一个**已经在跑**的 NagiBridge：游戏开着、MCP 服务器在 `:8000`、AI 是联机 farmhand。
> 你的活是**替玩家玩这个存档**，不是改这个项目的代码。

## 🔴 三条边界（先读）

1. **玩家局只用 MCP 的 16 个工具**：`check` `farm` `mine` `social` `scene` `menu` `storage` `daily` `map` `festival` `fish` `settings` `script` + `intent` `screenshot` `help`。
2. **不要 `curl` 端点**（`:7843` / `:7842`）。守卫（`stardew_api.warp()` 的落点校验、"同图不瞬移"、菜单闸门）**全在 Python 层**，curl 直接打端点等于把守卫整层跳过 —— 会把角色瞬移到水面上、把该走的路直接挪过去。
3. **不要直接跑 `scripts/` 下的内部脚本**。那些是开发排障工具：跑起来虽然还走 `stardew_api`，但会绕过状态注入、单子（动作空间约束）和自动 `--port` 注入（**手跑不带 `--port` 会操作到房主身上**）。

## 开工前自检（一次就够）

```bash
python scripts/launcher_check.py
```

必须 **全绿**（Python / 依赖 / SMAPI / mod 部署 / 端口）。红了就别玩，先把那一条修掉 —— 红着玩只会得到一个错的存档状态和一堆看不懂的现象。

## 玩的主循环

```
intent(ops="show")           →  看这一瞬能做的事（一行一件，箭头后面是理由）
intent(ops="do", kw={"code": "1"})        →  敲编号
intent(ops="do", kw={"code": "1,4"})      →  多件
intent(ops="do", kw={"code": "1=2,4=7"})  →  各做几次
```

**先看单子再动手。** 单子上没有的事，不代表做不到，只代表**这一瞬不适用**——这时才退到域工具：

```
farm(ops="till plant water", kw={"x": 40, "y": 20})     # 同一个 kw 供这串 op 共用
check(what="status")                                     # 全状态
check(what="backpack")                                   # 背包逐格
map(ops="go", kw={"place": "Town"})                      # 跨图
```

- 参数一律放 **`kw`**，别拼进 `ops` 字符串；键名尽量 = 函数签名里的参数名。
- 不确定某个域有哪些 op → `help("farm")`；子参数速查 → `scripts/TOOL_INVENTORY.md`。
- 长任务自动转后台 + 自动注入 `--port`，**不要手动 `--port`、不要自己 `&` 后台**。

## 三个最容易忘的坑

| 现象 | 真因 / 怎么办 |
|---|---|
| 改了东西"没生效" | 改 Python 要**重启 MCP 服务器**（启动时才 import）；改 C# 要重编 DLL + **重启游戏** |
| 卡在某个菜单里，工具全在报"有菜单" | 先 `menu(ops="read")` 看是什么菜单，再按类型关（`menu(ops="close")`）；别硬点 |
| 打字/对话推不动 | 用 `menu(ops="click")` 或 `press_key(ok)`，**别**用 `key confirm` 那条旧路 |

## 收工

- 长任务在飞时，`script(ops="stop")` 是随时递得进来的（工具体跑在工作线程里）。
- 走之前报告**实际做成了什么**，别只说"已完成"——`check(what="status")` 对一遍。
- 存档安全：不确定的操作先问玩家，尤其是卖东西、扔掉、睡觉（睡觉会推进日期，不可逆）。

## 想更深

- 完整说明书 → **`AGENTS.md`**（三层分工、端口约定、关键不变量、排障入口）
- 逐 op 速查 → `scripts/TOOL_INVENTORY.md`
- 逐端点/逐文件职责 → `python PROJECT_PANORAMA.py`
