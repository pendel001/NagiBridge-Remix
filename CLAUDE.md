# NagiBridge — 星露谷 AI 远程控制 + MCP 文游化接口

> 📖 **入口只有一份：[`AGENTS.md`](AGENTS.md)。改任何东西之前先读它。**
> 本文件**故意不复制**正文 —— 复制 = 两份会漂移（真漂过一次：`AGENTS.md` 被 `.claudeignore` 标成"旧文档"藏起来，Claude 就只看到半份）。
>
> ⚠️ "入口只有一份"**不等于**"所有文档并成一份"。这四份各司其职，谁也别取代谁：
>
> | 什么时候看 | 看哪 |
> |---|---|
> | 改代码前：三层分工 / 端口 / 不变量 8 条 / 已退役 / 排障 | [`AGENTS.md`](AGENTS.md) |
> | **调工具时**：16 个工具、168 个 op 的参数速查（最常翻） | [`scripts/TOOL_INVENTORY.md`](scripts/TOOL_INVENTORY.md) |
> | 摸全景：逐端点 / 逐文件职责 / 核心范式 | `python PROJECT_PANORAMA.py` |
> | 玩的时候：玩家局怎么走、边界三条 | [`skills/stardew/SKILL.md`](skills/stardew/SKILL.md) |

## 🔴 边界（第一眼必须看到）

<!-- NAGI-BOUNDARY -->
- **玩家局：只用 MCP 的 16 个工具**（13 域入口 + `intent` / `screenshot` / `help`）。
- **内部脚本 + HTTP 直连（`curl :7843/...`）：仅限开发排障。**
- ⚠️ 守卫（`stardew_api.warp()` 落点校验、`machine_loader` 同图不瞬移、菜单闸门）**都在 Python 层**；
  `curl` 打端点 = 整层跳过。跑 `scripts/xxx_run.py` 走 `stardew_api`，守卫还在，但绕过状态注入 / 单子 / 自动 `--port`。
<!-- /NAGI-BOUNDARY -->

## 三句话说完的结构

`ModEntry.cs`（SMAPI mod，HTTP API + Harmony 补丁）← HTTP 7843 → `scripts/nagi_mcp_server.py`（MCP 服务器 :8000，16 工具）← MCP → 外部 AI。
详细分工、规范、坑 → **AGENTS.md**。逐 op 速查 → `scripts/TOOL_INVENTORY.md`。
