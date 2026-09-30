# NagiBridge — AI 排查速查（人遇到问题就把这份丢给你）

> 你是一个接手 NagiBridge 项目问题的 AI。目标：**快速理解这套东西，帮人定位/解决**。
> 下面是**当前事实**（2026-08-23 同步），可直接信；深挖细节去"知识地图"找对应文档。
> 一句话：这套东西让人把 AI 像真玩家一样放进星露谷，靠的是"游戏内 mod"+"外部 MCP 服务器"两层配合。

## 这套东西是什么（三层）

1. **游戏层**：Stardew Valley 1.6 + NagiBridge C# mod（SMAPI）
2. **桥接层**：`ModEntry.cs` — HTTP API（~120 端点）+ Harmony 补丁（IsActive/表情/聊天/节日提示/10048端口修复/拾取检测）
3. **智能层**：`scripts/nagi_mcp_server.py` — MCP 服务器（streamable-http :8000，17 工具/15 域入口）+ 状态注入（`_with_state`）+ 心跳 + 节日/导航/脚本编排

外加 `scripts/` 下大量**自动化脚本**（矿洞/炸矿/钓鱼/农夫），Python 直接调 HTTP API。

## 端口约定（最容易搞错）

| 端口 | 是谁 | 用途 |
|---|---|---|
| **7842** | 人（host） | 检测 / 广播走这个（`/chat/push` → host_chat） |
| **7843** | AI（farmhand） | ***所有操作***打这个（工具/走位/交互） |
| **8000** | MCP 服务器 | 外部 AI 连 `http://<IP>:8000/mcp`，传输 **Streamable HTTP**（不是 SSE） |

- 规律：**操作打 7843，检测/广播打 7842**。角色由"谁先开游戏"动态分配，不写死。
- ⚠️ **打错端口会改到房主身上**——`wear` 尤其注意：`/equip` 走 `_ai_post`（打 7843），不是 `_post`（打 7842）。

## 你要用的现成工具（MCP 已开箱即用，不用重学玩法）

AI **不需要**重新学这套端点怎么用——直接调 MCP 域工具：

- 域模式恒开，只露 **17 个**（15 域入口 + 2 独立）；`--full` / `NAGI_FULL_TOOLS` 已退役
- 15 域入口：`check/ farm/ mine/ cabin/ social/ scene/ menu/ storage/ daily/ map/ festival/ fish/ settings + script/ session`（care→farm、quest→menu 已并 09-02）
- 2 独立：`screenshot/ help`（09-11 收编：`advance_story`→`menu advance`、`profile`/`which_role`→`check(what="profile"/"role")`）
- 域调用 = `域名(ops, kw={...})`，子参数进 `kw`。例：`daily(ops="wear", name="铁头靴")`
- ⚠️ **原始端点 `/state /interact /click /position /menu` 不是 AI 能直调的 MCP 工具**，只是坐标/动作提示——AI 一律走域工具。

## 知识地图（遇到问题去哪查）

| 想查什么 | 去哪 |
|---|---|
| 某机制怎么实现 / 有哪些坑和坐标 | `CHANGELOG.md`（完整知识库，改前必读） |
| 某 MCP 工具/域的 ops | `scripts/TOOL_INVENTORY.md` |
| 全端点清单 / 逐文件职责 / 核心范式 | `PROJECT_PANORAMA.py`（`python PROJECT_PANORAMA.py` 打印） |
| 怎么装怎么连 / 连不上 | `README.md` 第 4、5 节 |
| 当前工具 & 域结构 | 本文件 + `scripts/nagi_mcp_server.py` 的 `_KEEP_TOOLS` |
| 工具调用日志（性能/回归） | `scripts/sessions/session_log.jsonl` |
| 聊天记录（AI 记忆） | `scripts/sessions/session_<时间戳>.jsonl`（实时）/ `.md`（导出） |
| **跨会话记忆（不在 repo）** | 仓库外记忆库 —— 见下节「跨会话记忆库」 |

## 跨会话记忆库（在仓库外 · 开局必读）

记忆库**不在 repo 里**（过程记录 / 当前进行中 / 用户偏好），路径：

```
C:\Users\Administrator\.claude\projects\G--wingheng-Claude-NagiBridge-NagiBridge-main\memory\
```

- **开局先读 `MEMORY.md`**（索引）。技术细节一律以仓库 `CHANGELOG.md` 为准，索引只放 repo 记不住的。
- 要细节再 `grep` 那个目录或 read 单个文件 —— 文件名＝「主题-日期.md」，如 `pending-restart-0924-and-ghost-chests.md`（验收总账）。
- **新记忆写回同一目录**，沿用同一命名；**别在 repo 里另起一份**（两份会漂移）。索引里对应加一行。
- ⚠️ 这份记忆是**平台无关**的：Claude Code 与 DSH 共用，谁改都写这里。

## 关键不变量（坑 · 改了几处会"没生效"）

1. 导航：地面 `walk_to` / 矿洞 `position` / 跨图 `map_go`；别用 `/move+BFS`
2. **改 `ModEntry.cs`（C#）必须 `rm -rf bin obj` 重编 + DLL 复制到 C+F 双盘**，否则"改了没生效"
3. 对话推进用 `/click(no_mouse)` 或 `press_key(ok)`，别用 `key confirm`
4. 敲一下 → 检查 → 碎了停，不硬编码次数
5. 长脚本便利工具自动注入 `--port` AI 端口（防挪到房主角色）
6. **Python 侧改动重启 MCP 才生效**（`calendar_data.py` 等启动时 import）；改 C# 要重启游戏
7. Windows 终端跑 Python 加 `PYTHONIOENCODING=utf-8`（emoji 会炸 GBK）
8. `_ops_run` 对 `**kw` 域工具生成 `{ops, kw}`，脚本直调时子参数要放 `kw`，否则报 `kw Field required`

## 已退役 / 忽略（别去找）

- `plan_engine.py`（计划模式 2026-08-17 退役）；`server.ts` / `ChatHud.cs` / `LlmClient.cs`（原作者遗留，已 `.claudeignore`）
- MCP 侧：`accept_quest`、`buy_item`（直购作弊）已退役；`bomb_escort` 不再对外暴露（内建进 `bomb_mine`，没炸弹自动转内部）
- `festival bot`（`mods/NagiFestivalBots`）体系已全删；`set_appearance` ok 后自动退役（捏脸不可逆，走 `settings ops=confirm_look` 先核对）

## 排障入口

| 症状 | 一手检查 |
|---|---|
| **连不上** | `README.md` 第 5 节：curl 自测 / Streamable HTTP（不是 SSE）/ 防火墙放行 8000 / 同局域网 |
| **改的 tools/引导不生效** | **重启 MCP 服务器**（Python 启动时 import） |
| **改了 mod 没效果** | 确认 DLL **C+F 两处**都复制了：`C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley\Mods\NagiBridge\` + **`F:\Stardew Valley 2nd\Mods\NagiBridge\`**（⚠️**本机活的那份在 F 盘**；两边 `Get-FileHash` 比一下最快） |
| **手机 java 报错 / 404** | URL 少了 `/mcp` 或客户端用了 SSE → 补 `/mcp` + 传输改 Streamable HTTP |
| **AI 状态条慢 / token 大** | 看 `scripts/sessions/session_log.jsonl`（工具返回字节）；`settings ops=status` 看配置/退役 |
| **双开 10048 端口冲突** | 确认两个窗口用不同端口（Harmony Lidgren 补丁已修常规竞态） |

> 一句话：**先定位是"游戏 mod"还是"MCP 侧"的问题**——游戏 mod 问题要重编 DLL + 重启游戏；MCP 问题改完重启 MCP 即可。**改任何东西前先查 `CHANGELOG.md`。**
