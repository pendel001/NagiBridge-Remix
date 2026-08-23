# NagiBridge — 星露谷 AI 远程控制 + 导航 + MCP 文游化接口

## 架构
- **ModEntry.cs** — SMAPI mod，HTTP API（端口7842）+ 游戏控制/聊天/导航
- **scripts/nagi_mcp_server.py** — MCP 服务器（SSE/Stdio双模式），51个工具
- **scripts/player_activity.py** — 玩家行为检测 + 心跳系统
- **scripts/calendar_data.py** — 节日/生日/商店日历知识库
- **scripts/stardew_api.py** — HTTP API 封装层
- **scripts/locations.py** — 地图路线表 + POI 数据库
- **scripts/go_to.py** — 一键导航脚本（warp+walk）
- **scripts/tool_agent.py** — 旧版 LLM 工具调用代理（非 MCP）
- **scripts/mine_run.py** — ⛏️ 自动下矿 v2（逐下检测，已删除SWING_TABLE）
- **scripts/fish_run.py / farm_row.py / chop_trees.py / clear_area.py / water_crops.py** — 自动化脚本
- **scripts/sleep_check.py** — 🛏️ 睡觉/一起睡回归检查（`--scenario own|host`，逐步骤✅/❌+醒来位置核对）

## MCP 服务器 (nagi_mcp_server.py)

### 传输方式
- **默认**: streamable-http（`/mcp`，端口 8000），手机/Claude Code 直连 `http://本机IP:8000/mcp`（2026-08-02 起 `/sse` 已废弃，前端不支持）
- **`--stdio`**: `python scripts/nagi_mcp_server.py --stdio` 给 MCP Inspector 等用
- `.mcp.json` 已配置在项目根目录（url=`http://localhost:8000/mcp`）

### 🎯 目标进程
- **服务器裸启动默认打 AI 进程 7843**（AI 角色）——`nagi_mcp_server.py` 启动时 `setdefault("NAGI_URL", "http://localhost:7843")`
- **⚠️ NAGI_URL 在服务器启动时定死**：`.mcp.json` 的 env 只对 stdio 拉起子进程生效；URL 直连已运行的服务器时看的是**服务器自己的 NAGI_URL**，改目标要重启服务器并设 `NAGI_URL` env
- **Claude Code 桌面端**走 `.mcp.json`（url=`/mcp`，NAGI_URL=7843）→ **操作 7843（AI 角色轮回），心跳检测 7842（用户/房主恒）**
- **`screenshot()` 工具显式打 7843**（`_ai_get`），不看服务器 NAGI_URL——AI 截图永远看自己角色
- **广播/聊天必须走 host 7842**（`api.host_chat()`），否则 7843 本地消息房主看不到
- **⚠️ 给 user 的推送统一走聊天框（2026-08-15 恒拍板）**：`_host_notify`/`_host_converse` 走 `host_chat()`（不用 HUD——过夜复盘/结算看不到）。host `/chat` 只 `addMessage` 本地显示、不 setText+submit（会顶掉恒输入=反复清空根因）；`setText+RecieveCommandInput('\r')` 广播只在 farmhand(AI)进程用。`send_chat()` 走 AI 进程广播成 AI 消息。
- **⚠️ 心跳视角（2026-08-02）**：心跳是给 AI 看用户(房主)在干嘛的——`detect_activity` 拆成 `detect_player_nearby`（AI 进程 7843 视角，用户是否在附近→"👥恒正与你同在"）+ `describe_activity`（描述用户活动，从 host 进程 7842 取状态）。**AI 操作 7843、检测 7842**。
- **⚠️ 角色名不写死（2026-08-02）**：AI 角色是哪个 farmhand 由**端口**决定（ModEntry 自动分配：先开占 7842=房主，后开占 7843=AI 角色），与名字无关。换角色/换号直接可用；`Game1.player.Name` 等全是动态。传名字的地方（如 `go_sleep(who=自己名)`）传当前角色的实际名字即可。

### 51 MCP 工具
**🔄 工具合并（2026-08-15）**：实际 **130 个 @mcp.tool**。A2 十域合并后大部分并入域入口（`farm/mine/care/check/fish/social/scene/menu/quest/storage/daily/map/festival/plan/settings`），AI 记域入口即可。退役重复工具（去装饰保留函数）：`till_field→farm tillfield`、`plan_farm_layout→farm plan`、`move_to_tile→map movetile`、`drop_item→scene drop`、`clear_ground→farm clearground`；删 `sling`。未入域独立：`session_*`、`settings_*`、`script_*`、`list_*_ref`。

**感知**: `check_status`, `look_around(radius?)`（check look）, `screenshot()`（📸 AI角色DeepSeek视角，7843）
**导航**: `walk_to(poi_name)`, `move_to_tile(x, y)`
**农活**: `clear_area(x1,y1,x2,y2)`, `till_and_plant(seed, x, y, rows, length, dir)`, `water_crops()`, `harvest_crops()`
**动物**: `pet_pets()`（猫狗+水碗）, `care_building()`（当前建筑内摸+收）, `care_animals()`（只摸+收，不动门）, `close_doors()`（关畜棚门）
**产业**: `collect_machines(type?)`, `chop_trees(area?)`
**探险**: `go_mining(mode, target, ...)`, `go_fishing(location?)`
**鱼塘(2026-08-16, 需新DLL, 归 farm 域=养殖业)**: `farm(ops="pond…")`（pond 状态/pond_add 放鱼/pond_feed 喂任务/pond_collect 领鱼籽/pond_fish 服务端直钓；状态走 `/fish_pond`，放鱼/喂食/领产出走 `/interact` 鱼塘 bypass，钓鱼走 CatchFish 服务端——SDV1.6 FishPond 用 `doAction` 且被 didPlayerJustRightClick 卡着；交互都走到塘边）
**蟹笼(2026-08-16, 需新DLL, 归 fish 域)**: `fish(ops="crab…")`（crab 概览/crab_water 找水/crab_place 放笼/crab_bait 放饵/crab_collect 收；`/water` 扫 isWaterTile 找河/湖，放置=选蟹笼站水边朝水 /use，挂饵=选 Bait 交互笼子。坑：挂饵被 didPlayerJustRightClick 卡着→TryCrabPotInteract bypass 根治；`/state` 背包计数缓存未刷新会误判；isWaterTile 含非有效蟹笼水(鱼塘边/浅滩需试放)；落点有偏移）
**日常**: `go_sleep(who?)`（💤统一睡觉入口：不传/房主名→睡房主床一起睡【爬床彩蛋+🌹一起睡成功检测】；传自己名→睡自家；流程=warp进小屋→walk→精确对位→爬床广播→就地ready→等过夜→核验醒来位置）, `set_heartbeat_interval(minutes)`, `peek_player()`, `check_mine_progress()`, `set_appearance(...)`（💇捏脸，daily appearance 域）
- **⚠️ set_appearance 必须用 SDV 官方 change\* 方法（2026-08-15 恒）**：`/appearance` 走 `farmer.changeShirt/changePantStyle/...`（同时改 Farmer+FarmerRenderer 联机同步 Net 字段）。**禁止直接写 `shirtItem/skin/hair.Value` 单字段**：①1.6 `shirt/pants` 是 NetString 覆盖值，GetDisplayShirt 盖过→host 画旧衣服；②renderer 不同步→host 贴图错乱。
- **🎭 捏人弹窗 CharacterCustomization（2026-08-15）**：新 farmhand 起名/喜好/形象窗（`activeMenu.type=CharacterCustomization`）。流程：`set_appearance` 设形象 → `character_customize(name?, favorite?)`（反射写 nameBox/favThingBox+同步 player 字段）→ `/menu/click button=ok`。`canLeaveMenu()` 要求 Name/farmName/favoriteThing 非空（favoriteThing 常卡点）；🏡 farmname 自动继承房主农场名；不用 `/key`/抢前台（反射直写+receiveLeftClick）。`/menu.characterCust` 报状态。
- **🎨 颜色条=HSV 三滑块（0-100 整数，2026-08-15 反编译）**：`ColorPicker`=hueBar/saturationBar/valueBar，`getSelectedColor()`=HsvToRgb(hue/100*360, sat/100, val/100)；存进角色字段的是 RGB（NetColor），set_appearance 给 hex=直存 RGB 零偏差。渲染：眼睛 changeBrightness(-75) 偏暗、衣裤染色叠贴图，**头发用存储色最准**。`/color_pick` 直调 ColorPicker.HsvToRgb 和滑块一致；`appearance_info` 返回真 hex。
**节日**: `festival(ops, ...)`（🎪第13域：today/next/go/info/interact/answer/**shop**/**help**；go=map_go 去节日地点；shop=节日商店（FESTIVAL_SHOPS 登记柜台才开，平时不显示，如冰雪节猪车 Temp(55,32)）；help=节日玩法引导（FESTIVAL_GUIDE wiki 版：限时/稀有物/商店）；数据 calendar_data.FESTIVALS；interact 支持中文/英文名）
**商店**: `buy_item(item_id, quantity, price?)`, `sell_to_shop(name, count?)`, `sell_to_bin(name?, sell_all?)`
**服务**: `process_geode()`（砸晶球扣25g，**需铁匠铺柜台**）、`process_geodes(count)`（批量）、`museum_donate()`（一键捐赠，**需博物馆柜台**）——2026-08-18 恒：早期内部端点加地点限制+拟人走位（`_require_counter` 不在对应地点直接拒绝，到了先 walk_to 柜台站好再操作）
**任务**: `list_quests()`, `quest_progress()`, `accept_quest(quest_id)`
**动物**: `buy_animal(animal_type, name, building?)`（买+命名+入住）
**洒水器**: `sprinklers()`（检测位置+覆盖范围）
**工具范围**: `/tool_area`（走位+蓄力释放，拟人节奏耕地/浇水）
**精细**: `use_tool(name?, power?)`, `interact()`, `select_item(name)`, `face(dir)`, `press_key(key)`, `clear_ground(x, y)`（清杂物+terrain features）
**家具**: `scan_furniture()`（扫描位置/类型）, `furniture_pickup(tile_x, tile_y)`（拿起；摆放=select_item+face+use_item）, `interact_at(tile_x, tile_y)`（点 TV/日历/壁炉等）
**箱子**: `scan_chests()`, `chest_store(x, y, name, count?)`, `chest_take(x, y, name, count?)`
**计划(第14域)**: 🚫 **已退役（2026-08-17 恒：计划模式不实现）**——`plan` 工具未注册、`settings mode=plan` 失效；代码存档于 `plan_engine.py`+`_plan_*`（git 有备份），恢复需重新注册
**设置**: `settings(async, state_interval, auto_sleep, auto_sleep_time, heartbeat, context_turns)`, `settings_status`（mode 参数已退役只认 autonomous）
**小屋(2026-08-16)**: `cabin(ops)`（🏠 enum引导域：无ops=扫当前屋待收机器/雕像/家具+引导；collect收屋/statue摸雕像/furniture家具/interact点家具/pickup拿家具/sleep睡觉；只在 FarmHouse/Cabin/岛屋 有意义，错区给回家建议）
**兜底**: `run_script(name, args)`

### 状态注入（省 token 分层策略）
每次工具返回自动附带状态速报：
- **每天第一次** → 完整版（含日历节日/生日/商店）
- **之后** → 精简版（仅位置/时间/HP/体力/背包/警报）
- **待办仅显示未完成项**：动物未摸、机器待收
- **待办仅在农场显示**，离开农场不叨叨
- **📺 电视不是待办（2026-08-16 恒）**：每天早晨只提醒一句"想学食谱/小贴士可点电视（interact_at；不看也行）"，不计入待办
- 完成待办后自动消失

### 玩家动态心跳系统
- `set_heartbeat_interval(分钟)` — 控制注入频率（0=每次都显示）
- 到时间自动注入一行 `👤 玩家正在钓鱼/挖矿/发呆……`
- `peek_player()` — 手动查看，重置心跳计时
- **⚠️ 心跳视角（2026-08-02）**：心跳给 AI 看用户(房主/恒)在干嘛——AI 操作 7843、检测 7842。`_heartbeat_line`：附近+移动→"👥 恒正与你同在"；附近+窗口/静止→活动优先；不在附近→直接描述活动

### 玩家行为检测逻辑
通过 `/state` 的（位置+工具+血量+移动状态+附近NPC+窗口+静止计时）判断：
| 条件 | 输出 |
|------|------|
| 附近+移动中 | "👥 恒 正与你同在，⛏️ 正在沙漠中探索"（前缀合体） |
| 附近+窗口/静止 | "🎒 恒 正在整理背包"/"💭 恒 似乎在发呆"（活动优先） |
| 低血量（<25%） | "情况危急！" |
| 窗口打开 | "整理背包/整理箱子/在和xx搭话"（视窗口内容） |
| 鹈鹕镇矿井中 | "正在鹈鹕镇矿井中探索/战斗" |
| 头骨矿洞/火山 | "正在头骨矿洞/火山中探索" |
| 沙漠持武器挥砍 | "正在沙漠中战斗" |
| 钓鱼竿 | "正在钓鱼" |
| 斧头/镐子/锄头/水壶 | "砍树/敲石头/耕地/浇水" |
| 静止≥20秒+没开窗口 | "似乎在发呆" |
| 商店室内 | "正在XX里精挑细选" |
| 畜棚/鸡舍 | "正在照顾动物" |
| 家+凌晨 | "还在赖床" |
| 浴场 | "正在泡澡" |
| 附近有NPC | "和XX在一起" |
| 移动中 | "正在赶路" |
| 兜底 | "似乎在发呆" |

### /state 扩展字段
- **`in_dialogue`**: bool — 是否有对话框弹窗（DialogueBox）
- **`activeMenu.type`**: 当前菜单类型名（"DialogueBox" / "AnimalQueryMenu" / "ShopMenu" 等）
- **`activeMenu.speaker`**: DialogueBox 当前说话人 NPC 名（"在和xx搭话"用）
- **`activeMenu.submenu`**: GameMenu 当前页类名（InventoryPage / CraftingPage…，"整理背包/合成台"用）
- **`player.stationarySeconds`**: 玩家 TilePoint 未变的真实秒数（发呆检测用，2026-08-02 新增）
- **`player.isInBed`**: AI 是否在床上（2026-08-17 恒：纯聊天环节"等睡"检测用，需重启 DLL）
- **`weather`=7**: 🌿 绿雨（2026-08-17 恒：SDV 1.6 `Game1.isGreenRain`，DLL 已部署；旧 DLL 会误报成 1雨/0晴）。绿雨天状态条当天注入一次"放下农活打草收苔藓（Moss）"提示——草/苔藓树/变异树识别待真机测
- **`recent_events`**: 事件队列（新邮件📬 + HUD拾取物品📦 + **任务变化📋**（2026-08-15：新任务/任务完成可领奖励，`CaptureRecentEvents` 快照对比 questLog+questOfTheDay）），最多20条

### 状态条附加信息
- **🎲 运势**: 基于 dailyLuck 的占卜语（精灵非常开心/很开心/没什么倾向/很烦恼/非常不满）
- **📦 地上**: 自动扫描 `/debris`，显示附近掉落物（Woodx55, Sapx23...）
- **🏛️ 可捐赠**: 背包有矿物/古物时提示

### 状态条示例
```
📍 Farm (64,15) | ⏰ 06:30 | 🌸 春13日(周六) (年1)
📅 🥚 蛋蛋节 | 🏪休: 鱼店
❤️ 270/270 | 💪 250/270 | 💰 15,000g | 🎒 1/36格✓ | 🔧 Watering Can
🎲 运势: ▲ 精灵很开心 (+0.025)
💧 Watering can is empty
🐄 2只还没摸
⚙️ 待收: Keg
📦 地上: Woodx55, Sapx23
🏛️ 可捐赠: Orpiment×1
```

## 🌾 农活设计原则（2026-08-15 恒，改农活代码前必读）
- **AI 必走 farm 域**：锄地/播种/浇水/收获/清杂一律 `farm(ops=till/plant/water/harvest/clear)`，禁止手动 use_tool/tool_area 组合（漏格/蓄力错位失败）
- **拟人优先**：走位+工具动画；直接改地块只做文档化兜底。工具每次操作前重新 select（走位/瞬移重置选中）
- **基础工具**：水壶/锄头 tool_area `use`=BeginUsingTool(动画)+DoFunction(落地) 双调；高级工具走 charge_release（真蓄力）。漏格 DLL 自动补（position+DoFunction，不直接改地块，返回 patches/still_missing）
- **`/tool power` 对高级工具无效**（range 锁 UpgradeLevel）→ 蓄力用 tool_area；`/water_area` 已删（直改 dirt.state 作弊）
- **plot_plan(地皮规划)**：连通域找连续可耕地（设施/杂草/树纳入），输出边界+🏁最大可耕方形+需清杂，接 `farm ops=clear→till→plant→water`
- **温室/姜岛**：按名字认种植点（不能靠 HoeDirt，清地后误判）；种植用 `_farm_plant_only`/`_farm_till`
## ⛏️ 自动下矿 v2 (mine_run.py)
双模式：**rush** 冲层（从进度恢复/指定层→敲石找梯→下楼循环）/ **farm** 刷矿（电梯层21/41/71刷指定矿石）。
要点：逐下检测（敲→检查→碎就停，无敲击次数表）；梯子=`/ladder` 扫 Buildings 层 173/174 tile + `key confirm` 下楼（暴力搜兜底）；自动吃食物/血低睡/矿内2-4格走>4格闪现/贴脸切剑砍；`mine_progress.json` 记进度，下次 `floor//5*5` 恢复；结束回矿井口 Mountain(54,5)。
用法：
```bash
python mine_run.py --mode rush --target 80          # 冲层
python mine_run.py --no-resume --start 1 --target 40
python mine_run.py --mode farm --ore Iron --cycles 5  # 刷矿
python mine_run.py --check-progress / --reset-progress
```
## 💣 炸矿三模式（bomb_mine/escort/common，纯 Python 不用重启）
- `bomb_common.py`：放置/爆炸等待/贪心锚点/拾取/战斗/梯子/撤退
- `bomb_escort.py` 协同：跟恒（滞后站身后1-2格）途径高价值矿才放，帮打怪；恒离开就撤
- `bomb_mine.py` 自动：每层贪心放覆盖最多岩体的炸弹→躲≥半径+4→拾取→下楼；血低吃/撤、背包满丢低价值腾格
- 默认打 7843（`--port` 改），恒从 7842 读位置
用法：
```bash
python bomb_escort.py --bomb Bomb --ore-radius 7 --cooldown 20 --max-minutes 30
python bomb_mine.py --target 80   # 或 --no-resume --start 1 / --bomb 'Mega Bomb' --min-covered 5
```
机制：`select(Bomb)`+`/use`→placementAction 自动引爆（~3s）；半径 樱桃1/黑3/大红4；爆炸后 `/debris` 捡、`/ladder`+confirm 下楼。MCP：`bomb_status/plan/place/collect/ladder/retreat/mine/escort`
## 📦 箱子系统
端点：`/scan_chests`(扫当前图箱子) `/chest_take {x,y,name,count?}` `/store {x,y,name?,count?,keepTools?}` `/placechest`；MCP `scan_chests/chest_store/chest_take`
🧺 **智能存储**（2026-08-14）：`/store_all` 只处理当前场景箱（堆高高=进同类堆空位最多箱→默认箱→空位最多箱，用户可指定颜色/名/坐标）；`/name_chest {x,y,tag}` 标记箱子(本名(标记))；MCP `storage_layout/store/default/tag`，收进 `storage` 域。坑：大箱子=BigChest 不是 None、迷你出货箱=MiniShippingBin/248、祝尼魔箱=JunimoChest、内置冰箱=FarmHouse.fridge、`what` 按逗号分割
🎣 **浮漂样式机**：FishShop(10,4) 交互→ChooseFromIconsMenu(40图标+**-2=骰子随机**)，样式存装备鱼竿；MCP `bobber_style("dice")`。双盘 mod 已统一 Fishbot（`/fishbot` 只认 AdroSlice.Fishbot）
## ⚠️ 导航经验（2026-07-26 踩坑记录）
**不要用 `/move` + BFS** → 森林农场路径规划有病，会无视碰撞走到障碍物上
**✅ 矿井内用 `/position`** → 没有池塘，安全可靠
**✅ 地面用 `/walk_to`** → 游戏自带导航，跨地图自动找有效着地点
**✅ 矿洞内自然走** → 2-4格用 `/walk_to`，>4 格闪现
`go_to.py` 一直是对的，MCP 的 `walk_to` 和 `move_to_tile` 是另一套 `/move` 系统

## 💡 逐下检测原则（2026-07-26 统一策略）
**不再硬编码敲击次数表**，所有敲击操作统一为：敲一下→检查→碎了就停。
- 砍树：`chop_trees.py` 每下检查 `tree_still_there()`
- 挖矿：`mine_run.py` `mine_rock()` 每下扫 `surroundings`
- 固定类型（twig/stump/big_stump）仍用硬编码值

## 🏛️ 博物馆捐赠（2026-07-26）
- `/museum_donate` 直接标记 item 为已捐赠，无需进博物馆对话
- 展位坐标：下层大桌 y=11 (x=26~35)、中层 y=8、上层 y=6/5/4
- 同步更新 `archaeologyFound` + `MuseumPieces`
- 兼容多种 ID 格式（ItemId / QualifiedItemId / 裸ID）

## 🔧 编译陷阱（2026-07-26 血的教训）
- **DLL复制路径**：游戏跑在 F:\Stardew Valley 2nd\，不是 C:\Program Files\！
- **编译缓存**：`dotnet build` 太快（<2s）说明用了缓存，必须 `rm -rf bin obj` 再编
- **`ArchaeologyHouse` 类被移除**：SDV 1.6 没有 `StardewValley.Locations.ArchaeologyHouse`，改用 `Game1.netWorldState.Value.MuseumPieces`
- **MuseumPieces 键类型**：`Add(Vector2, string)` 第二个参数是字符串 itemId

## ✅ 已验证坐标
- warp("Farm") 落点：**(40, 32)** ← 池塘！别从这里出发
- warp 矿洞用 **(5,5)** 安全
- 水碗 POI: **(52, 7)**
- 小屋门口: **(64, 15)**
- 矿井口: Mountain **(54, 5)**
- 入口梯子位置: Level 1(10,4), Level 2(4,5), Level 5(12,6)

## pet_pets 最终实现
```
walk_to_coord("Farm", 52, 7)   → 用游戏自带导航
轮询等待到达                      → polling 30次×0.5s
face(1)                          → 朝右
select+refill+use_tool("Watering Can") → 浇水
petall()                         → 作弊摸所有动物
```

## 新增 API 函数（stardew_api.py）
- `walk_to_coord(location, x, y)` — 游戏自带 `/walk_to` 接口
- `walk_natural(target_x, target_y)` — 用 `/move` 寻路自然走路，走不到则 position 兜底
- `position(x, y)` — 直接传送（矿井内安全）
- `petbowl()` / `petall()` / `waterbowl()` — 宠物系统

## ModEntry.cs 已编译

### 通用端点
- `/interact` — `checkAction` 传像素坐标（修了坐标bug）
- `/state` — 新增 `dailyLuck`、`weather` 字段
- `/key` — 新增数字键 0-9 支持（用于对话框选项选择）

### 功能端点
- `/ladder` — 扫 Buildings 图层找梯子 tile index 173/174
- `/scan_chests` / `/chest_take` / `/store` — 箱子系统
- `/petbowl` / `/petall` / `/waterbowl` — 宠物系统

### 服务端点（2026-07-26 新增）
| 端点 | 功能 | 说明 |
|------|------|------|
| `POST /process_geode` | 直接砸晶球 | 扣25g/个，绕开克林特UI |
| `POST /museum_donate` | 捐矿物/古物到博物馆 | 自动找空展位，同步MuseumPieces |
| `GET /museum_remove?id=X` | 移除捐赠记录 | 用于调试 |
| `GET /museum_debug` | 调试博物馆数据 | 看背包物品ID/类型/捐赠状态 |
| `GET /clear_ground?x=&y=` | 清除地面杂物+terrain features | 2026-07-30升级：同时移除debris+objects+terrainFeatures+largeTerrainFeatures |
| `GET /debris` | 扫描地上掉落物 | 返回坐标+物品名 |

### 🆕 2026-07-30 新增

| 端点 | 功能 | 说明 |
|------|------|------|
| `POST /buy_animal` | 买动物+命名+入住 | 直调 `new FarmAnimal()`，跳过PurchaseAnimalsMenu |
| `GET /quest_list` | 所有任务原始数据 | 含常规+特殊订单+可接订单 |
| `GET /quest_progress` | 任务进度（结构化） | 含子目标 currentCount/maxCount |
| `POST /quest_accept` | 接取特殊订单 | 布告板订单直接接 |
| `GET /sprinklers` | 洒水器检测 | 位置+类型+覆盖格坐标 |
| `POST /toggle_doors` | 开关畜棚/鸡舍门 | 反射调 `animalDoorOpen` 字段，100%生效 |
| `POST /tool_area` | 工具范围操作 | 走位+蓄力释放，拟人节奏耕地/浇水 |

**`/state` 新增字段：** `in_dialogue`(bool), `recent_events`(数组), `activeMenu.type`(菜单类型名)

**`/tool` 新增参数：** `power=N` — 蓄力等级（0-4），直调 `DoFunction` + 地块修改

**`/clear_ground` 升级：** 现在同时移除 debris + objects + terrainFeatures（树/草/种子） + largeTerrainFeatures

### 🛏️ 睡觉体系（已并入 go_sleep，历史修复见 git）
- **`go_sleep(who)` 统一入口**：不传/房主名=睡房主床一起睡（爬床彩蛋+🌹一起睡检测），传自己名=睡自家。流程=warp进小屋→walk到床边→`_snap_onto_bed`精确对位→crawl广播→`/sleep stay`→等夜→核验醒来位置
- **端口↔角色自动检测**：`detect_roles(7842,7843)`（player2==player=host）；`ensure_roles(ttl=30)`；spawn 脚本读 env
- **关键坑**：2×3床只有中间行 isInBed 能站住；`/position` 传床格不 redirect；`_sleeping_now`=isInBed或ReadyCheckDialog；对话框在→绝不重爬；多人在线要全员就绪才过夜
- 回归：`scripts/sleep_check.py --scenario own|host`（逐步骤✅/❌+醒来位置）
### 🆕 2026-08-06 干草/精通/木匠（需重编译 ModEntry）
| 端点 | 功能 | 说明 |
|------|------|------|
| `GET /silo` | 筒仓干草检测 | `{silos, hay, capacity, room, full}`（每筒仓 240） |
| `GET /mastery` | 精通状态（只读） | 反射列 Farmer 所有含 Mastery 的字段/属性值（MasteryExp/MasteryLevelsSpent） |
| `POST /mastery_claim` | 领取精通（探测版） | 1.6 无统一 claimMastery 方法，先反射列候选方法供确认；`{type}` |
| `GET /carpenter` | 木匠建筑清单（只读） | 从 `Game1.buildingData` 列价格+材料+尺寸+是否买得起；**建造写操作暂不做**（1.6 无 BluePrint 类，数据驱动，防误扣资源） |
- MCP: `silo_status()` / `mastery_status()` / `building_list()`
- **部署注意**：这些端点在**编译后的 DLL** 里，需重启游戏生效（炸弹三模式是纯 Python，不用重启）
- SDV 1.6 API 踩坑：`farm.piecesOfHay` 是 **NetInt**（`.Value`），不是 int；无 `MasteryType`/`GetMasteryExp`/`claimMastery`/`BluePrint` 类

### 🛋️ 家具/商店/菜单/对话（2026-08-09 实测要点）
- **家具拿起=左键**：`/furniture_pickup {x,y}`（LowPriorityLeftClick）；摆放=`select`+`face`+`use`→placementAction。家具交互 `/interact` 兜底直调 checkForAction（绕过 didPlayerJustRightClick，点 TV/日历/壁炉）
- **商店菜单通用**：`/menu` 报 `shopItems[].bounds`+`slots`（衣柜/鱼缸/马龙/皮埃尔通用）；分页=右侧 up/down 箭头；**免翻页直点** `/menu/click {item: 名或ID}`；批量买 `buy_item(id, quantity)` 直购
- **🆕 升级工具材料需求（2026-08-18，需重启）**：`/menu` shopItems 带 `trade/tradeCount/tradeName`（SDV1.6 `ItemStockInformation.TradeItem`），read_menu 显示"（需 铜锭×5）"——克林特升级菜单不再只看到钱。BuildStamp=`2026-08-18-shop-tradeitem`
- **背包**：`/key menu` 开；`menu_click(x,y)` 移动、`right=true` 拆1、`action=discard` 丢垃圾桶、`action=split` 拆N
- **对话自适应**：`read_menu()` 一眼看出信件/背包/商店/对话/奖励；`menu_click(option=N)` 选对话选项、`menu_click(item/button)` 自适应。**剧情推进用 `/click` 或 `press_key(ok)`，别用 key confirm/interact**
- **坑**：普通商店买物先进光标(heldItem)要再点包；`/warp` 异步要 `_wait_warp`；对话选项异步要 `_select_option` 重试
### 🔍 缩放/截图（2026-08-01）
- `/zoom?level=50` 设缩放（1-200%），持久字段=baseZoomLevel+singlePlayerBaseZoomLevel+localCoopBaseZoomLevel 全设
- **ZoomPerMap mod 会按图覆盖缩放**——要统一直接改它 config.json（default*ZoomLevel 全 0.5，重启生效）
- `screenshot_ai()` 显式打 AI 进程 7843；`GetBackBufferData` 渲染永远新鲜（截图旧场景=进程/时间线问题，连拍两张比 MD5 验证）
- `/sleep stay`：DS 已在床上则就地睡不 warp 回家；farmhand 自动确认结算/ReadyCheckDialog（只对 farmhand，host 留给真人）
## 已校准地图（2026-07-25）
✅ Mountain / Railroad / BusStop / Desert / Beach / Forest / Town / Backwoods
✅ 室内：ScienceHouse/Saloon/ManorHouse/ArchaeologyHouse/Blacksmith/AnimalShop
  SandyHouse/FishShop/BathHouse/Tent/MasteryCave/WizardHouse/SkullCave/FarmCave/Cabin
✅ 秘密森林6硬木桩坐标
✅ 钓点×15（河流3/湖泊3/海洋3/特殊6）

## 🗺️ 地图机制（2026-08-13 完成）
- **locations.py**：`MAP_LINKS`（门 vs 出口瓦片 kind）、`MAP_FEATURES`（每地点交互功能）、`BUILDING_DOORS`（建筑门口）、`ARRIVE`（参考）
- **MCP 工具**：`map_lookup`（查地点功能+出口）、`map_query`（功能反查，同义词模糊）、`map_go`（多段导航：walk_to 出口前一格→/warp 下一图入口）
- **map_go 血泪经验**：走路统一用 `/walk_to`（`/move` 效果相同但别在 warp 瓦片上走）；出口走"边缘法线前一格"（往中心偏移会算出不可达→瞬移）；开门用 `/interact`（key confirm 开不了）；落点用 /warps targetX/Y 按方向对
- **🚦 交通优先级（2026-08-16 恒：图腾柱 > 矿车 > 走路）**：map_go 到目标先试交通——玩家在农场且有对应柱（山岭→山/海滩→海滩/沙漠→沙漠/姜岛→全岛，含落点近处建筑 Mine/SkullCave/SandyHouse 等）→ 动态 /farm_buildings 定位柱 → 站柱下1格朝上 `key confirm`（⚠️必须 confirm，interact/右键不触发）；玩家在矿车站图且 dest 在矿车网络（矿井/城镇/巴士站/采石场）→ 站矿车格朝站面 `/interact` 开菜单 → `menu_click(option=站名)`。落点≠dest（岛柱落岛南等）由 map_go 从落点续走 BFS。两者都不可用才走路。交通节点前先下马（riding 时 confirm 是下马）。纯 Python 不用重编译。
- ⚠️ **节日/内部临时场景没 map_go 入口**（2026-08-16 恒发现）：Submarine/MermaidHouse/BeachNightMarket 不在 MAP_LINKS，map_go 进不去。**处理**：map_go 到外场景（如夜市 BeachNightMarket），再交互门进入（如潜艇门(5,35)交互→Submarine、美人鱼船门(58,32)→MermaidHouse）。**⚠️潜艇/美人鱼船别在下潜/演出中途进出（会卡脚）**。
- **状态条 map enum**：商店/设施地点注入"🗺️ 可: …"，农场/家不报

## 🎪 节日工具（2026-08-14，roadmap #2 收官；纯 Python + 1 个 DLL 补丁）

**MCP `festival` 域**（第 13 域，工具 128→129）：
- `festival today`（今天节日+时间+是否已开始）、`next`（下一个+剩几天）、`go`（**map_go 导航去节日地点**）、`info`（实况/actors）、`interact(name)`、`answer(N)`、**`eggs`（蛋蛋节找蛋：当年奇/偶年全坐标+近邻路线）**、`shop`、`help`
- 数据：`calendar_data.FESTIVALS`（**完整列表含 1.6 新增：沙漠节春15-17 Desert / 鳟鱼大赛夏20-21 Forest / 鱿鱼节冬12-13 Beach**；冬8 是 Cindersap 森林不是海滩）+ `FESTIVAL_LOCATIONS`（节日→地图名，多日节日逐日登记）+ `FESTIVAL_EGGS`（蛋蛋节全坐标，odd/even 两套）
- 封装：`stardew_api.festival_status()/festival_interact()/festival_answer()`（打 AI 进程 7843；端点 ModEntry 已有）。`_festival_now_data` **AI 不通回退 host**（solo 也能查日期/蛋坐标）
- **🥚 蛋蛋机制（2026-08-17 反编译）**：蛋是 `Event.cs` 寻宝开始扫地图 **Paths 图层 fest\* 图块**（52秒捡越多越好，非固定9颗）。奇偶年地图不同：奇 `Town-EggFestival`(32蛋)/偶 `Town-EggFestival2`(35蛋)。ModEntry 新端点 **`/egg_tiles?map=`**（扫 fest 图块返回坐标）；`/festival_data`（读节日数据脚本）。开局位：奇~(27,69)、偶~(29,70)
- **状态条两次提醒**：节日前一天晚 ≥18:00 `🌙 明天{节日}（时间）`（只提醒一次）；节日当天早上 `🎪 今天{节日}（时间）！用 festival go 去参加`
- **ModEntry 原生提示抓取（DLL 补丁，需重启）**：Harmony patch `Game1.showGlobalMessage`，消息含"举办/开始了"+节日名/书摊 → `AddRecentEvent("news", "📢 {msg}")`（"花舞节已经在森林里开始举办了。" 直接进 AI 小新闻）
- **chat 系统消息修复（同 DLL）**：`src=0` 系统消息（xx加入/躺下/早早结束）→ 📢 推送（过滤关于轮回自己的），不再打"找不到发送者"告警
- BuildStamp = `2026-08-14-festival-msg`

## 🗺️ 动态工具检测 + map_go 未解锁拦截（2026-08-14 #13，map 联动·修正版）
- **不拦 AI，只建议**：`DOMAIN_HOME`（farm/care→Farm，mine→矿/火山）+ `DOMAIN_EXEMPT`（导航/直操作豁免）。
- **`_domain_advice(domain, ops)`**：不在适用区 → 返回 `💡 当前在{X}，{domain}通常在{Farm}做；可先 map go Farm`，**照跑不拦**。接入 farm/care/mine 域工具（结果前置建议行）。
- **状态条动态提示**：`_build_state_strip` 加 `🛠️ 可用域: farm/care`（在 Farm/矿等显示适用域；在 Town 显示"本图 farm/mine/care 不适用"）。
- **🔒 map_go 未解锁拦截**：`LOCKED_MAPS`（Desert/Sewer/Woods/SkullCave/Island*/Railroad/Club/Summit/MasteryCave/Greenhouse）+ ModEntry `GET /unlocks`（读 mailReceived flag/工具等级/技能）；未解锁 → map_go 返回 `❌ {地点} 未解锁（{条件}）`。旧 DLL 无 /unlocks → 兜底放行不误伤。
- menu 是"菜单状态"非地点绑定、shop_visit 自己导航，不按地点拦。

## ⏰ 异步唤醒 / 🌙 兜底 / 🧾 纯聊天环节（2026-08-17 更新）

### 🚫 计划模式已退役（2026-08-17 恒：暂不实现）
- **`plan` 工具未注册**、`settings mode=plan` 失效、`_autopilot_loop` 只跑兜底睡觉不跑计划状态机。
- 代码**存档**于 `scripts/plan_engine.py`（已标退役）+ nagi_mcp_server.py 的 `_plan_*` 函数（未注册）。
- 想连跑脚本：手动 `script_start` 逐任务（无自动编排）。
- 恢复计划模式：重新注册 plan 工具 + settings mode 放行 + 启动 `_plan_load_state()`（git 有备份）。

### 异步唤醒（settings async / state_interval）
- 脚本后台运行期间按 `state_interval` 秒注入 `⏰ 异步唤醒`：**可做轻量操作**（整理背包/发消息/表情/截图/看状态），**别用走位/工具/菜单强操作**（会打架）；要控制权用 `script_stop`。

### 🌙 兜底（覆盖所有行为，最高优先）
- **凌晨1点自动 go_sleep**：后台 `_autopilot_loop` 到 `settings(auto_sleep_time)`（默认 2500）→ 先停所有运行脚本 → `api.go_sleep_flow(who=AI名)`。有菜单/剧情/对话框/已上床时不打扰。`settings(auto_sleep=off)` 可关。
- **停止脚本所有模式适用**：`script_stop(job_id)` 随时停。

### 🧾 纯聊天环节（2026-08-17 恒）
- **触发**：AI 在床等恒入睡（isInBed/ReadyCheckDialog，夜未过）或 过夜结算 ShippingMenu。
- 行为：状态条提示"纯聊天环节"（别跑脚本）→ **每 30s 轮询**注入 → 有恒新消息（chat/emote→recent_events）重置超时 → **持续无消息 3 分钟起超时兜底**（每 3 分钟加码一句）→ 过夜后自然恢复。
- 实现：`_chat_phase_line()`（`/state` 新增 `player.isInBed`，需重启 DLL）。

### ⚠️ 端口注入（关键）
长脚本便利工具/异步自动跑前 `api.ensure_roles()` 取 AI 端口，**统一注入 `--port <AI端口>`**（water_crops/chop_trees/clear_area/mine_run 默认打 host 7842 且会覆盖 env——不注入会挪恒的角色/耗恒体力）。已知脚本都支持 `--port`。

## 🆕 2026-08-16 MCP 真实环境反馈五修（纯 Python，不用重编译 DLL）

**① plot_plan 排除建筑 footprint（温室误耕）**
- `_building_footprints()`：只在 Farm 地图查 `/farm_buildings`，把温室/畜棚/鸡舍等建筑 footprint 全当**阻挡格**（截断连通域、不进可耕/最大方形）。温室内部(独立地图)不受影响。

**② 禁飞：walk_to/go_to 跨图改走 map_go（不瞬移）**
- `walk_to`：**保留并纳入 domains-only `_KEEP_TOOLS`**（恒拍板：实测必要工具）；跨图 POI 自动转 `map_go`（走出口瓦片真实路径），同图才 go_to.py。`move_to_tile` doc 改"同图不跨场景"。`go_to` 的 POI 兜底改调 `map_go`（不再 go_to.py 跨图飞）；建筑/回家路径不动。
- `map_go` doc 强化：**跨场景唯一入口**（走出口瓦片/门/买票旅行）。map_lookup 提示改指向 map_go。
- 新增 `warp_safe()` MCP 工具（紧急逃脱，调 `api.warp_safe()`，仅血低被围/卡死兜底）→ `map` 域 op `warp_safe/逃脱` + 独立工具 + `_KEEP_TOOLS`。

**③ chat_npc 自动推进对话 + 找人**
- 附近没有 → `find_npc` 定位：跨图先 `map_go(NPC所在图)` 再走；同图 walk_natural 走（position 兜底）。
- 开口后**自动推进**：纯文本 DialogueBox 用 `key confirm` 收台词到 `_story_buffer`，出现 `responses`（选项）/事件走 `_advance_story`/结束停下；一次性返回累计台词 + `🗳️ 选项`。
- doc 手动引导：推进=`press_key(ok)`/`/click`；选选项=`menu_click option=N`；关=`menu_click button=close`；剧情别用 `key confirm`/`interact`。

**④ 矿井引导：背包预检 + 火山门禁 + bomb_volcano 工具**
- 预检（脚本层，两硬两黄）：`mine_run.py` 无镐子/血低→**硬拦**，无武器/背包满→黄；`bomb_mine.py` 无炸弹/血低→硬拦，无武器→黄；`bomb_volcano.py` 血低→硬拦，无炸弹/武器→黄（骑行可切镐子硬跟）。
- `_volcano_gate()`：host(7842) 不在 Mine/SkullCave/UndergroundMine*/Volcano*/Caldera → **禁入火山**（特殊瓦片无法程序化换层），消息用 host 真实名字（自适应）；host 读不到→默认拦。接入 `map_go`（dest 火山图）+ 新 `bomb_volcano` MCP 工具（mine 域 op）。
- `bomb_volcano` 加入 `_PORT_SCRIPTS`（统一注入 --port）；plan_engine `SCRIPT_META` 加 bomb_volcano（--max-minutes/until）。

**⑤ 后台窗口固定操作问题（Python + DLL 已部署）**
- `_advance_story` 事件对话：**优先进程内 `key confirm`**（不碰 OS 鼠标、不抢前台），连续 2 轮无推进才退 `/click`（事件 receiveLeftClick 兜底）。
- **DLL 根治（2026-08-16 已部署）**：`/click` 加 `no_mouse` 参数——no-menu 分支不再用 OS `SetCursorPos`+`mouse_event` 点屏幕中心（后台可能点到错误窗口=AI 操作到 7842 的根因），改用进程内 `currentEvent.receiveActionPress` / `Game1.pressActionButton`（IsActive 补丁下失焦也能推进）。Python `_advance_story` 退 `/click` 时传 `no_mouse=true`。BuildStamp = `2026-08-16-click-nomouse`；C/F 双盘 DLL 已同步（md5 一致），需重启游戏生效。

**⑥ POI 结构化站位+朝向（walk_to 到点自动朝向，2026-08-16 恒）**
- `locations.POI_FACE`（18 条固定可交互 POI）：`{poi: {"face": 0上/1右/2下/3左, "stand": 玩家站位(默认=pos)}}`。宠物水碗朝右站位(51,7)、矿车/售票机/锻造台/柜台朝上。
- `walk_to` / `map_go` 到 POI 后自动 `_apply_poi_stand_face()`：先走/挪到 stand（不同则 move_to）→ `face(朝向)` → 返回"站位X，朝Y"提示；**交互交给 AI**（interact / interact_at）。幂等，双调无害。
- ⚠️ **农场设施（建筑/可移动物：畜棚/温室/图腾柱/出货箱）不在此表**——走 `go_to`/`_resolve_place` 动态检测（`/farm_buildings`），硬编码坐标会随建筑搬家失效。钓点/导航地标是纯位置不需要朝向。

**⑦ 🐟 鱼塘交互（2026-08-16，需重编译 DLL，已部署 C/F 双盘，重启生效）**
- **发现**：鱼塘 `checkAction` 被 `didPlayerJustRightClick()` 卡着（API 没点鼠标右键不触发）——实测只有门瓦片 action=True 但不开菜单。**SDV 1.6 FishPond 交互入口是 `doAction(Vector2, Farmer)`（不是 checkForAction）**。
- **ModEntry `/interact` 鱼塘 bypass**：`TryFishPondInteract` 瓦片落在 FishPond footprint → 直接 `fp.doAction(tile, farmer)`（放鱼/喂食/领产出/开 PondQueryMenu 全走游戏原生逻辑，不手动复制消耗逻辑）。响应带 `fishPond` 字段。**实测 collect 成功**：AI 在 Cabin 也能交互，Legend II Roe×2 进背包（金色框=动物饼干双倍）。
- **`POST /fish_pond` 状态端点**：`{x?, y?, action?}`——`list`(全部)/`status`(指定x,y)/`fish`(服务端直钓)。**SDV1.6 FishPond 真实字段**：`fishType`=NetString(合格ID)、`FishCount`=属性(读 currentOccupants)、容量=`maxOccupants`、任务=`neededItem`(NetRef<Item>)+`neededItemCount`、完成=`hasCompletedRequest`、产出=`output`(NetRef<Item>)、天数=`daysSinceSpawn`、**金色框=`goldenAnimalCracker`**（没有 fishCount/GetMaxFishes/hasFinishedRequest/daysSinceProduction！反编译确认）。
- **🎣 `fish` = 服务端直钓（2026-08-16 恒实测拍板）**：`action=fish` → `FishPond.CatchFish()` 把塘里鱼直接给玩家 + `currentOccupants-1`。**不碰竿/鼠标**——鱼塘钓鱼本来就不用小游戏（cast+reel 秒出），且**右键拉竿会误触"吃鱼"（尤其手持鱼时）**，AI 走 API 无法可靠等鱼咬钩再拉竿 → 服务端最安全。
- **MCP `pond` 域**：list/status 查状态；add 放鱼（选鱼→交互）；feed 喂任务（自动从状态拿任务物品）；collect 领产出（空手交互）；fish 服务端直钓。纯 Python 封装。恒实测 collect✓/钓出 Legend II✓。
- **坑**：鱼塘 5x5 建筑 footprint 交互瓦片站东侧中间 (px+4,py+2) 朝左；放鱼只能放同种（空塘定种）；`/surroundings` 不报鱼塘（是建筑不是 object）。BuildStamp=`2026-08-16-fish-pond`。

## ⚠️ 关键坑（2026-08-13）
- **SDV 1.6 化肥编号大改**：Speed-Gro=465、Deluxe Speed-Gro=466、Deluxe Fertilizer=919、保留土壤=370/371/920、Hyper Speed-Gro=918（372/373/374 已不是化肥）
- **失焦根治**：启动+读档自动 `pauseWhenOutOfFocus=false`（后台 warp/开门不卡）
- **`/give` 工具必须用 ID**（`(T)16`=真铱锄，按名字造 ErrorItem）；`/craft_recipes` 端点 + `craft`/`list_craftables` 工具
- **走路统一 `/walk_to`**；`walk_natural`/`move_to` 标弃用（耕种/下矿脚本勿动）
- **📚 读书= `select_item(书名)` + `press_key(confirm)`**（右键/动作键，消耗书领技能/配方）；**别用 `/use` 读书**——/use 对 object 走 placementAction 会把书放地上（放下的 object 收不回！）。书摊(马尔赛罗 Town 110,27)卖技能书

## 待校准
⏳ 社区中心 · 姜岛（哈维医院已校准：locations.py 门口/柜台/出口 POI）
⏳ 玩家自定义箱子/酒桶/小屋位置

## 开发环境
- 配置文件在 F:\Stardew Valley 2nd\ 不在 C 盘
- 端口：谁先开谁占7842，后开的占7843（host=小恒=7842，AI=DeepSeek=7843）
- 编译：`dotnet build -c Release -p:ModDeploy=false --output bin/out`
- DLL复制：需复制到 C:\Program Files...\Mods\NagiBridge\ 和 F:\Stardew Valley 2nd\Mods\NagiBridge\

## 启动步骤
1. 开星露谷（确保 NagiBridge MOD 运行）
2. MCP 服务器：`python scripts/nagi_mcp_server.py`（SSE 模式，手机连；**默认控制 AI 角色 DeepSeek=7843**）
   或通过 `.mcp.json` 由 Claude Code 自动加载（Stdio 模式，显式 `NAGI_URL=7842` 控制房主小恒）
3. 手机客户端连 `http://192.168.1.190:8000/sse`
