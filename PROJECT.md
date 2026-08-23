# NagiBridge — 星露谷 AI 伙伴项目

> ⭐ 一句话：**让 AI 像真人一样住进你的星露谷农场**——陪你聊天、帮你玩、记得你们的故事。

一套让"AI 能在星露谷里动手"的工具集：游戏内装一个 SMAPI mod，外部 AI 通过 HTTP / MCP 控制角色，像真玩家一样走位、聊天、种地、挖矿、钓鱼、逛节日。

## 架构概览

```
[ 游戏层 ]  Stardew Valley 1.6 + NagiBridge C# mod（SMAPI）
                │  HTTP API :7842（房主恒）/ :7843（AI 轮回）
                ▼
[ 桥接层 ]  ModEntry.cs —— 所有 /xxx 端点 + Harmony 补丁
                │
          ┌─────┴─────┐
          ▼           ▼
[ 智能层 ]  scripts/nagi_mcp_server.py（MCP :8000，153 工具/15 域，默认只露 26）
              + 状态注入 + 心跳 + 节日/导航/脚本编排
                │
                ▼
         外部 AI（如 Claude Code）经 MCP 工具"看 + 动手"
```

聊天走游戏内面板：**API Mode**（直接连大模型）或 **Channel Mode**（连 Claude Code 控制游戏）。

## 端口约定（很重要）

| 端口 | 是谁 | 用途 |
|---|---|---|
| **7842** | 房主（恒） | 检测 / 广播用（host_chat） |
| **7843** | AI 角色（轮回/farmhand） | 所有"操作"打这个 |
| **8000** | MCP 服务器 | 外部 AI 连：`http://<IP>:8000/mcp` |

规律：**操作打 7843，检测/广播打 7842**。角色由"谁先开游戏"动态分配，不写死。

## 它能干什么

- 🎮 **会玩**：拟人走位、对话、商店（真实 UI 菜单）、钓鱼、下矿、种地、养动物、节日
- 🗣️ **会聊**：状态注入、心跳、事件推送、聊天框广播
- 🤖 **能跑**：自动化脚本（矿洞冲层、炸矿三模式、钓鱼、农夫……都可后台跑）

## 知识地图（遇到问题先查哪）

| 想看什么 | 去哪 |
|---|---|
| 完整机制 / 坐标 / 坑 / 变更史 | `CHANGELOG.md`（改代码前必读） |
| MCP 工具清单 / ops（给 AI 的大白话） | `scripts/TOOL_INVENTORY.md` |
| 全端点清单 + 逐文件职责 + 核心范式 | `PROJECT_PANORAMA.py`（`python PROJECT_PANORAMA.py` 打印） |
| 安装 / 游戏内聊天 / MCP 连接 / 连不上速查 | `README.md` |
| 遇到问题甩给 AI 就知道怎么处理 | `AGENTS.md` |

## 5 条坑（与 CLAUDE.md 同源）

1. 导航：地面 / `walk_to`、矿洞 / `position`、跨图 / `map_go`；别用 `/move+BFS`
2. 改 `ModEntry.cs` 必须 `rm -rf bin obj` 重编 + DLL **C+F 双盘复制**，否则"改了没生效"
3. 对话推进用 `/click(no_mouse)` 或 `press_key(ok)`，别用 `key confirm`
4. 敲一下 → 检查 → 碎了停，不硬编码次数
5. 长脚本自动注入 `--port` AI 端口（防挪到房主身上）

---

> 详细内容都在 `CHANGELOG.md`（知识库）、`PROJECT_PANORAMA.py`（全端点/文件/范式）与 `AGENTS.md`（AI 排查速查）。本文件是给你快速建立认知的**薄概览**。
> 最后更新：2026-08-23
