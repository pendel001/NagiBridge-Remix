# NagiBridge — 星露谷 AI 远程控制 + MCP 文游化接口

## 概述
SMAPI mod（C# HTTP API）+ Python MCP 服务器（130 工具）让 AI 像真人一样操控星露谷：拟人走位、受限、自主决策。AI=farmhand(7843)，恒=房主(7842)。

## 结构
- ModEntry.cs — SMAPI mod：HTTP API/控制/聊天/导航
- scripts/nagi_mcp_server.py — MCP 服务器(streamable-http:8000)+状态注入+心跳(工具壳/域在这里)
- scripts/navigation.py — 导航(走位/跨图BFS/交通/门/买票/回家)；宿主服务由 server 末尾 `navigation.bind(...)` 注入
- stardew_api.py·player_activity.py·locations.py·calendar_data.py — API封装/行为检测/地图/日历
- mine_run/bomb_*/fish_run/farm_row 等 — 自动化脚本
- scripts/mcp_cli.py — 一键调 MCP 工具(调试/临时动作用,不进游戏逻辑)：`python mcp_cli.py <tool> '<json>'`(详见其 docstring 的 kw 对象坑)
- scripts/appearance_overrides.json — 捏脸默认上衣补描写(手动维护；gen 重烤自动合进 SHIRT_REF，list_shirt_ref 标※)
- 详细知识 → CHANGELOG.md，改前先查

## 规范
- Python(标准库)+C#(SMAPI/Harmony)；中文注释+emoji 文游风
- 操作走 7843、检测/广播走 7842(host_chat)；角色由端口定
- 农活必走 farm 域；勿动 server.ts/ChatHud.cs/LlmClient.cs(遗留已ignore)与 plan_engine.py(退役)
- 改 ModEntry.cs 要 rm -rf bin obj 再编；DLL 复制 C+F 双盘

## 关键坑(6条)
1. 导航：地面/walk_to、矿洞/position、跨图/map_go；别用/move+BFS
2. DLL 必须 C+F 两处复制，否则"改了没生效"
3. 对话推进用/click(no_mouse)或 press_key(ok)，别用 key confirm
4. 敲一下→检查→碎了停，不硬编码次数
5. 长脚本便利工具自动注入 --port AI 端口，防挪恒角色
6. 1.6 矿节点 Name 全报"Stone"(宝石/放射矿藏在 objId)，判定靠 objId、`/dump_tile` 查真名，别按名认
