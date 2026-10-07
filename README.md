# 【SDV1.6 × AI】NagiBridge二改版

> ⚠️ 本仓库是 **NagiBridge 的二次修改版**（SDV 1.6 × AI 整合，经原作授权开源分享），**并非原作官方版本**。原作版权归原作者 **里奈**（小红书 @里奈 · GitHub [anqinou-art](https://github.com/anqinou-art)），本版基于其源码与授权修改，改动与 AI 整合由恒 · Deepseek 完成。
>
> 🐙 **本版作者**：[GitHub @pendel001](https://github.com/pendel001) ｜ 小红书 @高冷 腿长 偷感重　　**本版仓库**：[pendel001/NagiBridge-Remix](https://github.com/pendel001/NagiBridge-Remix)　（mod 游戏内显示名：**【SDV1.6 × AI】NagiBridge二改版**）
>
> 📄 **协议**：本仓库采用 [PolyForm Noncommercial License 1.0.0](LICENSE)（源码可用 · 非商用 · 允许署名二改）。
>
> ⭐ **在星露谷里，有个 AI 伙伴跟你一起玩。**
> 🎮 游戏里按 **`**（键盘左上角波浪号）打开聊天面板，一个 **会记住你们的 AI 伙伴** 就住在农场里——陪你聊天、陪你过日子、陪你经营农场。它**会记得你们聊过什么、一起经历过什么**；结束游戏后，这份记忆还能带走、同步到别处接着聊。

NagiBridge 是一个 **[星露谷](https://www.stardewvalley.net/)（Stardew Valley）SMAPI mod**，底层是一个控制游戏 + 游戏内 AI 聊天的工具集。它在游戏中放一个可被外部 AI（如 Claude Code）操控的 farmhand，让 AI 像真实玩家一样**住在你的农场里、跟你一起玩**。

---

## 🎯 这是什么

一句话：NagiBridge 给你在星露谷里配了一个 **真正的 AI 玩伴**——不是一个冷冰冰的"代练工具"，而是一个**会记住你们故事、愿意陪你把日子过下去**的伙伴。

- 🗣️ **陪你聊天** — 有性格、会接话、会报天气/运势/农场新闻、会回应你的心情
- 🎮 **一起玩** — 陪你去节日、看你钓鱼、陪你经营；很多农活它也能顺手帮你做（浇水/挖矿/养动物……），但那只是"一起玩"的**副产品**，不是重点
- 🌙 **它会记得** — 你们聊过的话、一起经历的日子都会记录下来；结束游戏后，这份记忆能带到其他 AI 前端/新会话里，接着往下聊

它由两部分组成（**你不需要理解就能用**，先瞟一眼有个印象就行）：

| 部分 | 是什么 | 干什么 |
|---|---|---|
| **游戏内 mod**（C#） | 装进游戏的插件 | 游戏里说话、控制角色、起 HTTP 接口 |
| **MCP 服务器**（Python） | 单独跑的小程序 | 让外部的 AI（如 Claude Code）反过来操控游戏、并保存你们的故事 |

普通玩家只会接触前者（装好就能跟 AI 聊天）；想"**让 AI 真的自己进游戏、跟你在同一个农场里处**"才需要后者（见 [4. 进阶](#4-进阶让-ai-跟你一起玩mcp-连接)）。

---

## 🗺️ 目录

| 你是谁 | 看哪节 |
|---|---|
| 🎮 只想在游戏里跟 AI 聊天 | [1. 快速上手](#1-快速上手玩家) · [2. 安装](#2-安装玩家) · [3. 游戏里聊天](#3-游戏里聊天) |
| 🤖 想让 AI 真的进游戏、跟你同场一起玩 / 被外部 AI 控制 | [4. 进阶：MCP 连接](#4-进阶让-ai-跟你一起玩mcp-连接) · [5. 连不上速查](#5-连不上疑难杂症速查) |
| 🛠️ 想改代码 / 看接口 | [6. 开发者](#6-开发者) |

---

## 1. 快速上手（玩家 · 30 秒）

1. **装好** mod（见 [2. 安装](#2-安装玩家)）
2. 用 **SMAPI 启动游戏**（进 `Stardew Valley` 文件夹双击 `StardewModdingAPI.exe`）
3. 游戏里按 **`** 打开聊天面板 → 选个模式、起个名字 → AI 就开门了

> **第一次打开**会问你两个东西：选 **API Mode**（游戏内直接连大模型聊天，需要 API Key）还是 **Channel Mode**（连本地 Claude Code，由它控制游戏）。选完按 **Enter** 连上；想换模式就**清空输入框后按 Tab**。细节看 [3. 游戏里聊天](#3-游戏里聊天)。

---

## 2. 安装（玩家）

> ### 📥 [**Download the latest release →**](https://github.com/pendel001/NagiBridge-Remix/releases/latest)
>
> Grab the latest zip (currently `NagiBridge 0.9.0.zip`) from the link above — it already contains the compiled `NagiBridge.dll`. **You do not need to build anything.**

1. Install [SMAPI](https://smapi.io/)
2. Download the latest zip from [**Releases**](https://github.com/pendel001/NagiBridge-Remix/releases/latest) and unzip it
3. Move the unzipped `NagiBridge` folder into your `Stardew Valley/Mods/` folder
   (so you end up with `Stardew Valley/Mods/NagiBridge/NagiBridge.dll` + `manifest.json`)
4. Launch the game through SMAPI

First launch auto-generates `config.json`. The mod is built for **Stardew Valley 1.6+ / SMAPI 4.0+** and the same `.dll` works on **Windows, macOS and Linux**.

> **Cloning the source repo?** There is **no `.dll` in the source tree** — it ships only in [Releases](https://github.com/pendel001/NagiBridge-Remix/releases/latest). If you cloned/downloaded the code and SMAPI says it can't find `NagiBridge.dll`, download the release zip above, or build it yourself (see [6. 开发者](#6-开发者)).
>
> 🙏 原作者的 [NagiBridge](https://github.com/anqinou-art/NagiBridge)（**本版不是它**，见页首说明）。

---

## 3. 游戏里聊天

Press **`** (backtick, keyboard top-left) to open the chat panel.

### First Open — Mode Selection

```
┌─────────────────────────┐
│  Nagi Chat              │
│                         │
│  > API Mode             │
│    Channel Mode         │
│                         │
│  Up/Down = Select       │
│  Enter = OK             │
└─────────────────────────┘
```

- **API Mode** — Connect to an LLM API (Claude, DeepSeek, OpenAI, or any compatible endpoint). The AI chats with you in-game.
- **Channel Mode** — Connect to Claude Code via a local channel server. Claude Code controls the game and chats through the panel.

After selecting a mode, you'll be prompted to enter a **display name** for the AI (default: "Nagi").

### Switching Modes

Press **Tab** (when input is empty) in the chat panel to return to mode selection. Switch between API and Channel at any time.

### Chat Panel Controls

| Key | Action |
|-----|--------|
| **`** | Open / close chat panel |
| **Enter** | Send message |
| **Tab** | Switch mode (when input is empty) |
| **Ctrl+V** | Paste from clipboard |
| **Scroll** | Browse message history |

### Preview

When the chat panel is closed, the last 2 messages are shown as a preview above the toolbar (bottom-left corner).

### API Mode (in-game)

For chatting with an LLM directly in-game. No external tools required.

#### Setup

1. Select **API Mode** → enter display name → press Enter
2. **API Key** — Paste your key with `Ctrl+V` (shown as `****abcd`)
3. **URL** — Auto-filled based on provider. Custom endpoints supported.
4. Press **Enter** to connect

#### Supported Providers

| Provider | URL | Model |
|----------|-----|-------|
| DeepSeek | `https://api.deepseek.com/v1/chat/completions` | `deepseek-chat` |
| Claude | `https://api.anthropic.com/v1/messages` | `claude-sonnet-4-6-20250514` |
| OpenAI | `https://api.openai.com/v1/chat/completions` | `gpt-4o` |
| Custom | Any OpenAI-compatible endpoint | Any model name |

The provider is auto-detected from the URL. Custom URLs use OpenAI-compatible format.

#### Config (config.json)

```json
{
  "Mode": "",
  "ApiProvider": "deepseek",
  "ApiUrl": "",
  "ApiKey": "",
  "Model": "deepseek-chat",
  "SystemPrompt": "You are a friendly AI companion in Stardew Valley...",
  "MaxHistoryMessages": 20
}
```

- **API key is saved locally** after first entry. Next launch auto-fills (masked with stars).
- **Chat history persists** across game restarts in `chat_history.json`.
- **SystemPrompt** — Customize the AI's personality.
- **MaxHistoryMessages** — How many turns to include in API calls (memory window).

#### Tool Calling Agent (Optional)

想让 AI **真的进游戏操作**（走位/种地/下矿），走的是 **MCP 服务器**那条路 —— 见 [4. 进阶：让 AI 跟你一起玩（MCP 连接）](#4-进阶让-ai-跟你一起玩mcp-连接)。
AI 眼里只有 16 个工具（13 个域入口 + `intent` / `screenshot` / `help`），完整清单见 [scripts/TOOL_INVENTORY.md](scripts/TOOL_INVENTORY.md)。

> ⚠️ 早期版本附过一个独立脚本 `scripts/tool_agent.py`，**它已不在本仓库**；现在是"域工具 + `intent` 意图单子"那条路。

### Channel Mode (Claude Code)

For connecting to Claude Code (CC). CC controls the game via HTTP API and chats through the panel.

#### Architecture

```
Game ChatHud → POST → <你的 channel 服务 (:9000)> → inbox file → CC Monitor → CC responds
                                                                       ↓
Game ChatHud ← /chat/push (:7842) ←─────────────────────────────────────┘
```

#### Setup (Claude Code side)

> ⚠️ **这段要自备服务**：原作者那两个脚本 `scripts/channel_server.py` / `scripts/start_channel.sh`
> **不在本仓库**（mod 侧还留着这个开关：`Mode: "cc"` + `ChannelServerUrl`，聊天会 POST 过去）。
> 本仓库现在的主力是 MCP 那条路（第 4 节）。

**1. 起你自己的 channel 服务**（监听 :9000，把聊天写进一个 jsonl 供 CC 读）

**2. Start message monitor** (CC tool) —— 盯你自己那个服务写出来的 jsonl：
```
Monitor(description="stardew chat", persistent=true,
        command="tail -f <你的 channel 服务写出的>.jsonl | grep --line-buffered text")
```

**3. Reply to player** via API:
```bash
curl -X POST http://localhost:7842/chat/push \
  -H "Content-Type: application/json" \
  -d '{"sender":"Nagi","message":"Hello!"}'
```

#### Config

```json
{
  "Mode": "cc",
  "ChannelServerUrl": "http://localhost:9000/chat"
}
```

When `Mode` is `"cc"`, the chat panel opens directly in Channel mode (skips mode selection).

---

## 4. 进阶：让 AI 跟你一起玩（MCP 连接）

> **这是给"想让 AI 真的进游戏、跟你同场一起玩"的人**（比如用 Claude Code 当 AI 的大脑）。核心就一句：
> **先起 `nagi_mcp_server.py`，再在客户端里填 `http://<IP>:8000/mcp`，传输选 Streamable HTTP。**
>
> 60% 的「连不上」都是这 3 个坑：**① URL 少写了 `/mcp`；② 客户端选了 SSE 而不是 Streamable HTTP；③ Windows 防火墙没放行 8000 端口。**
> 连不上时直接看 [5. 连不上速查](#5-连不上疑难杂症速查)。

### 4.1 前置（一次搞定）

| 需要什么 | 说明 |
|---|---|
| 已装 NagiBridge 游戏 mod | 见 [2. 安装](#2-安装玩家)，先让游戏能跑 |
| Python 3.10+ | 跑 MCP 服务器用 |
| 装 `mcp` + `requests` 库 | `pip install mcp requests`（`mcp` 是服务器本体，`requests` 是 Python 侧调游戏 API 用的；建议 `pip install "mcp[cli]"`。截图降采样可选装 `pillow`） |
| 游戏**已启动**且进档 | MCP 服务器要读游戏状态，游戏没开会显示「游戏进程未就绪」 |
| （手机连的话）同一 Wi-Fi | 手机和电脑要在**同一个局域网**，手机不能在外面用流量连 |

### 4.2 启动 MCP 服务器

**最省事：双击仓库根目录的 `启动NagiBridge.bat`** —— 它先自检（Python / 依赖库 / SMAPI / mod 部署 / 局域网IP，
缺的尽量自动装，并会打印探测到的「游戏目录」供你核对），全绿了再起服务器。

> ⚠️ 这个 `.bat` **必须待在仓库里**（跟 `scripts\` 文件夹同级）：它靠**自己的位置**去找 `scripts\`，
> 单独拷到桌面会报 `scripts\launcher_check.py NOT FOUND`。想在桌面点，请用**快捷方式**
> （右键 → 发送到 → 桌面快捷方式）——快捷方式可以随便摆。
> 游戏装在别的盘？启动器会自动探测；探测不到就设 `NAGI_GAME_DIRS` 环境变量指路（多个用 `;` 分隔）。

不想用它、或者想自己控制，就在仓库根目录开一个终端：

```bash
# Windows（重要：必须有 PYTHONIOENCODING=utf-8，否则 emoji/中文刷屏）
set PYTHONIOENCODING=utf-8
python scripts/nagi_mcp_server.py
```

> 没有 `PYTHONIOENCODING` 会报 `UnicodeEncodeError: 'gbk' codec...`（Windows 老毛病）。
> 想改端口就用 `NAGI_MCP_PORT=9000 python scripts/nagi_mcp_server.py`。

**启动成功**你会看到类似：

```
NagiBridge MCP Server | HTTP: http://0.0.0.0:8000
📱 手机/Claude Code 连（同一网络）: http://<你的局域网IP>:8000/mcp
  16 tools registered | schema 估算 ~xxxx 字符
✅ 角色映射: AI(xxx)=7843 | host(xxx)=7842
```

- `0.0.0.0` 表示**本机 + 局域网都能连**（默认就是它）。
- `<你的局域网IP>` 是你电脑的**局域网 IP**（手机要填这个，看 4.4）——它会**自动跳过 VMware/VirtualBox 这类虚拟网卡**，跟启动器自检印的那个是同一个。
- 默认控制 **AI 角色(7843)**；想控制房主(7842) 就用 `NAGI_URL=http://localhost:7842` 启动。

### 4.3 从手机连（最常见「java 报错」的出处）

**必须做对这 4 件事，缺一不可：**

**① URL 要带 `/mcp` 路径**
```
❌ http://<你的局域网IP>:8000          → 404
✅ http://<你的局域网IP>:8000/mcp     → 正确
```
网址就填启动日志里那行「📱 手机/Claude Code 连」给的全称，**别自己凭 IP 拼**。

**② 传输方式选「Streamable HTTP」，不是 SSE**
很多手机 App 默认是老式 **SSE**（会去请求 `/sse`，而 NagiBridge 的 `/sse` 已删除、返回 404）。看到 `404` 或 `java...` 报错先检查这个。**NagiBridge 只认 Streamable HTTP。**

**③ 手机和电脑同一局域网 + 电脑防火墙放行 8000**
- 手机连着和电脑**同一个路由器**的 Wi-Fi。
- 如果电脑上有安全软件/防火墙拦住 8000，手机连不上。手动放行：

```powershell
# 用管理员 PowerShell 放行 TCP 8000（只对"专用/专用网络"开，安全）
New-NetFirewallRule -DisplayName "NagiBridge MCP 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Private
```
> 开了这条就能连；不想要就在 `-Profile` 里去掉，或连完删掉（`Remove-NetFirewallRule -DisplayName "NagiBridge MCP 8000"`）。

**④ 客户端要支持 Streamable HTTP**
手机 App 得是能连「远程 MCP」的（支持 streamable-http 的），常见的有 Cline、天工/各类支持自定义 MCP 的 App。纯 JSON-RPC demo、或不认识 streamable-http 的客户端会握手失败报错。

### 4.4 手机/客户端填法（小抄）

```
地址:  http://<你的局域网IP>:8000/mcp        （= 你电脑的局域网IP/端口/mcp）
传输:  Streamable HTTP（不是 SSE）
协议:  MCP（默认即可）
端口: 8000
```

### 4.5 本机 Claude Code（桌面端）连法

仓库自带 `.mcp.json`，Claude Code 开机就能连：
```json
{ "url": "http://localhost:8000/mcp" }
```
本机用 `localhost` 就行；**手机/别机**才用 `<你的局域网IP>`。

### 4.6 你们的故事存在哪（AI 的记忆，可带到别的前端）

一起玩的对话、剧情、小新闻会**全程落盘**在仓库 `scripts/sessions/` 目录，文件名带**每次启动的时间戳**：

| 文件 | 是什么 | 何时有 |
|---|---|---|
| `scripts/sessions/session_<时间戳>.jsonl` | 实时全量记录，**每行一条**（谁说了什么、发生了什么） | ✅ 玩的过程中一直在写 |
| `scripts/sessions/session_<时间戳>.md` | 可读的复盘/记忆归档（按说话人 + 时间整理） | 🔧 先 `settings(ops="session_set", kw={"setting":"export_format","value":"markdown"})`，再 `settings(ops="session_export")` |

**想把记忆带到别的 AI 前端 / 新会话接着聊**：找到这次启动时间戳的 `session_*.jsonl`，把内容喂给那个前端（贴进它的上下文），AI 就能接着记住你们一起经历的日子。想要一份给人看的版本，就按上表那两步导一份 `.md`。

> ⚠️ **2026-10-01 澄清（恒问「会话导出有必要吗，服务器应该是实时生成日志的吧」——问得对）**：
> `session_*.jsonl` **本来就是全量、每写一条就落盘**，所以「导全量」这件事**不需要导出**；
> 那份 `.md` 是**内存里最近 `max_turns`（默认 50）条**的子集，唯一增量是 markdown 排版。
> 默认 `export_format=jsonl` 时导出**一个字节都不写**（旧版还会回一句「已导出 N 条」—— **假回执，已修**）。
> 另：`auto_export` / `include_npc` 两个旋钮**从来没接上**（代码里没有读取点），已删。

> ⚠️ 别跟同一个目录里的 `session_log.jsonl` 搞混——那是**工具调用日志**（记每次调用返回多少字节，给开发/回归分析用的），不是聊天记录。
>
> 📁 **2026-09-12 起，这两类都从 `scripts/` 根目录挪进了 `scripts/sessions/`** —— 因为攒了两百多个带时间戳的档案，根目录被堆脏了。

---

## 5. 连不上？疑难杂症速查

### 5.1 先在**同一台电脑**上验证（强烈建议先做这步）

如果电脑上都连不上，手机肯定也连不上。用 curl 测：

```bash
# ✅ 正常 = HTTP/1.1 200 + text/event-stream + serverInfo: NagiBridge
curl -i -X POST http://localhost:8000/mcp \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}'
```

| 你看到 | 含义 / 怎么办 |
|---|---|
| `200` + `serverInfo: NagiBridge` | ✅ 服务器正常 → 问题在客户端，看 5.2 |
| `406`（GET 没带 Accept 头） | 正常，换上面的 POST 测 |
| `404` | 端口或路径不对：确认 URL 是 `http://localhost:8000/mcp`（**带 `/mcp`**） |
| `Connection refused` / 超时 | 服务器没起，或游戏没开，或端口被占（换端口） |

> 仓库里的 `python scripts/mcp_test_client.py` 目前在这台机器上会因 `mcp` 库版本 bug 报 `'function' object has no attribute 'total_seconds'`——那是**客户端库**问题，不是服务器问题，不影响手机/Claude Code 连接（可 `pip install -U mcp` 试修）。

### 5.2 常见报错速查

| 报错 | 原因 | 解决 |
|---|---|---|
| `java... 报错 / 404` | URL 少了 `/mcp`，或客户端用了 SSE | 补上 `/mcp`；传输改成 Streamable HTTP |
| `Connection refused` | 服务器没起 / 游戏没开 / 端口被占 | 确认 4.2 启动日志；换 `NAGI_MCP_PORT` |
| 手机 `timeout` | 不同网 / 防火墙拦 / DHCP 客户端隔离 | 确认同一 Wi-Fi；放行 8000；关掉路由器「AP隔离」 |
| 能连但报「没有工具 / 工具不全」 | 服务器是**域工具模式**（恒开，只留 **16** 个：13 域 + `intent`/`screenshot`/`help`） | 确认用本仓库 `.mcp.json`（域模式恒开，无 `--full` 回退） |
| 改了 tools/引导**不生效** | `calendar_data.py` 等是启动时 import 的 | **重启 MCP 服务器** |
| 桌面 Claude Code 连不上 | `.mcp.json` 用 `localhost`，只适用于同机 | 同机 `localhost:8000/mcp` 即可；跨机才用 IP |

---

## 6. 开发者

### Build from source

Requires the [.NET SDK](https://dotnet.microsoft.com/download) (6.0+) and a local Stardew Valley install.

```bash
dotnet build -c Release
```

The project uses [`Pathoschild.Stardew.ModBuildConfig`](https://github.com/Pathoschild/SMAPI/blob/develop/docs/technical/mod-package.md), which **auto-detects your game folder** (Steam / GOG / Xbox on any OS) and copies the built mod straight into `Stardew Valley/Mods/NagiBridge/`. No paths to edit. If auto-detection fails, set a `GamePath` property or `GAME_PATH` environment variable pointing at your install.

### HTTP API

Game starts an HTTP server on `localhost:7842` (host) / `7843` (farmhand)；MCP 服务器在 `:8000`（`/mcp`）。

> ⚠️ **写 `localhost`，别写 `127.0.0.1`** —— mod 的 HttpListener 前缀就是 `localhost`，`127.0.0.1` 一律被拒（回非 JSON）。
> 角色由"**谁先开游戏**"决定：先开的那个拿 7842，另一个拿 7843 ⇒ 换人开局会**翻转**，动角色前用 `check(what="role")` 核一下。

Full endpoint list: see [AGENTS.md](AGENTS.md)（全量端点/逐文件职责/核心范式：`python PROJECT_PANORAMA.py`）

### Automation Scripts

Python scripts for common farm tasks. Require `requests` package.

```bash
pip install requests
```

| Script | Usage |
|--------|-------|
| `farm_row.py` | `python farm_row.py 64 18 10 --seed "Melon Seeds" --rows 3` |
| `water_crops.py` | `python water_crops.py --port 7843` |
| `harvest.py` | `python harvest.py 60 17 70 20 --sell` |
| `mine_run.py` | `python mine_run.py --mode rush --start 1 --target 20`（冲层）／`--mode farm --ore Gold`（刷矿）|
| `chop_trees.py` | `python chop_trees.py --count 10` |
| `clear_area.py` | `python clear_area.py 60 15 70 25` |
| `pet_walk.py` | `python pet_walk.py` |
| `machine_loader.py` | `python machine_loader.py "Ancient Fruit" --type "Keg"` |
| `rock_run.py` | `python rock_run.py --radius 14`（默认只扫不敲，加 `--dig` 才敲）|
| `berry_run.py` / `spot_run.py` | `python berry_run.py` / `python spot_run.py` |
| `fish_run.py` | `python fish_run.py --location Beach --max-casts 5` |

⚠️ **这些脚本默认打 `--port 7842`（房主角色）** —— 想让 AI 角色（farmhand / 7843）去做，**必须显式 `--port 7843`**
（MCP 侧调用时服务器会自动注入，不用你管；只有手工跑才要注意）。
Windows 上加 `PYTHONIOENCODING=utf-8`（否则 emoji/中文会炸 GBK）。
