# NagiBridge MCP 域工具速查手册（2026-08-22 重写 · keep-set 26 个）

> 给恒的速查手册：AI 现在**默认只看到 26 个工具**（15 个"域入口" + 11 个独立工具），
> 其余旧独立工具**全部收进域入口**（函数还在，只是 AI 不再直调）。
> 记住一句话：**"想做 X → 调对应域的 ops"**。旧工具名大多能在域里找到等价物（见文末对照表）。
> 🔍 **想查某域详细 ops/坑 → `help(域名)`**（如 help(farm)；docstring 已精简，深度靠这个查）。

---

## 🧭 一、总纲（先读这段）

- **域模式默认开启**（`--full` / `NAGI_FULL_TOOLS=1` 可回退全量 151 个工具，调试/手机前端用）。
- **每个"域"就是一把瑞士军刀**：`farm(ops="till plant water")` 一次做多件事，ops 空格/逗号分隔。
- **AI 调用 = 域名 + ops**，不是工具名。例：想点一个格子 → `scene(ops="at", tile_x=8, tile_y=24)`；想推进剧情 → `menu(ops="advance")`。
- **✦ 域工具的子参数会收进 `kw`**：FastMCP 对带 `**kw` 的域工具生成 `{ops, kw}` 结构。Claude Code 自动处理（实际是 `farm(ops="till", kw={…})`），你**只需理解、不用手动拼**；但用脚本直调时子参数要放进 `kw`（如 `fish(ops="go", kw={"location":"Beach"})`），否则报 `kw Field required`。
- **原始端点（/state /interact /click /position /menu）不是 AI 能直调的 MCP 工具**，只是坐标/动作提示；AI 一律用下面的域工具。

---

## ⚙️ 二、15 个域入口（ops 列表）

### `check(what)` — 查询（状态/背包/机器/收藏）　*注意：这是 `what` 不是 `ops`*
| what | 干嘛的 |
|---|---|
| `status` | 完整状态（位置/时间/血/体力/钱/背包格数）|
| `backpack` | 背包逐格详细（价值/星级/属性）|
| `worn` | 穿戴物（衣服/戒指/饰品）|
| `machines` | 全农场机器清单 |
| `mine` | 下矿进度 |
| `silo` / `mastery` / `buildings` | 筒仓干草 / 精通 / 木匠建筑 |
| `quest` | 任务列表 |
| `chests` / `storage` | 当前图箱子 / 箱子网络视图 |
| `look` | 环视周围（NPC/怪物/物品/地形）|

### `farm(ops)` — 农活（锄/种/水/收/机器）*AI 必走* **只能在 Farm/温室/姜岛**
| ops | 干嘛的 |
|---|---|
| `till` / `plant` / `till plant`(合并) | 锄地 / 播种 / 锄+种一条龙 |
| `water` | 浇水（自动跳过下雨、水壶没水先装满）|
| `harvest` / `scythe` | 收成熟作物 / 镰刀收（蒜/花/茶）|
| `fertilize` / `clear` / `plot` | 撒化肥 / 清杂草石头树桩 / 地皮规划 |
| `tillfield` / `hoe` / `plantlayout` | 蓄力锄整块地 / 按洒水器布局锄 / 播种规划 |
| `chop` / `clearground` | 砍树 / 清单格 |
| `collect` / `load` / `building` | 收机器产物 / 往机器放原料 / 一屋收放一轮 |
| `pond` / `pond_add` / `pond_feed` / `pond_collect` / `pond_fish` | 鱼塘：状态/放鱼/喂/领鱼籽/直钓 |

### `mine(ops)` — 下矿（冲层/刷矿/炸矿）**只在 矿井/头骨/火山**
`go`(去挖矿) `progress`(进度) `bomb_status/plan/place/collect/ladder/retreat`(单步炸矿) `bomb_mine`(自动) `bomb_escort`(协同) `bomb_volcano`(火山) `organize`(整理背包)

### `care(ops)` — 动物 **只在 Farm**
`animals`(摸+收) `building`(这间屋) `pet`(摸猫狗) `water`(宠物碗喂水) `milk`(挤奶剪毛) `buy`(买动物) `doors`(关门) `petwalk`(拟人摸) `hay`(加干草) `statue`(祈福)

### `cabin(ops)` — 小屋引导 **只在 小屋/农场屋里**　不传=扫屋
`enum`(扫屋待收) `collect`(收机器) `statue`(雕像) `furniture`(扫家具) `interact`(点家具) `pickup`(拿起家具) `sleep`(睡觉)

### `social(ops)` — 社交
`chat`(跟NPC搭话) `gift`(送礼物) `give`(给物品玩家) `send`(发聊天消息) `emote`(表情) `friendship`(查好感) `movie`/`snack`(电影院知识)

### `scene(ops)` — 场景交互（点东西/工具/转身/捡）
| ops | 干嘛的 |
|---|---|
| `at(x,y)` | 点指定格（柜台/电视/机器，对角也行）|
| `interact` | 点面前的东西（=确认键）|
| `use`(工具名) | 挥工具 |
| `face`(方向) | 转身（0上1右2下3左）|
| `select`(物品名) | 选中背包物品拿手上 |
| `pickup` / `pickup_scene` | 拿起家具 / 捡当前场景可拾取物 |
| `berry` / `spot` / `moss` | 摇浆果 / 挖斑点蚯蚓 / 绿雨搜刮苔藓 |
| `forge_help` | 火山锻造台附魔攻略 |
| `drop` / `furniture` | 丢背包物品 / 扫家具 |

### `menu(ops)` — 菜单/界面（开→看→点）
| ops | 干嘛的 |
|---|---|
| `read` | 看当前菜单（商店/对话/选项/信件）|
| `click`(option/item/button/x/y) | 点菜单项（自适应）|
| `key`(ok/esc/数字) | 按键盘 |
| `advance` | **推进剧情/对话**（自动走剧情）|
| `cancel` | 关当前弹窗/撤睡觉就绪 |
| `shop` / `sell` / `bin` | 逛店 / 卖商店 / 投出货箱 |
| `cook` / `craft` / `recipes` / `craftables` | 做饭 / 合成 / 菜谱 / 配方 |
| `forge` | 锻造台 |
| `geode` / `geodes` | 砸晶球（×1 / 批量）|
| `customize` | 捏人弹窗（起名/喜好）|
| `bundle` | 社区中心献祭板（实地读板看缺口）|
| `bundle_kb`(query=…) | 献祭知识库（不用跑社区中心，查"原来要这些"）|
| `donate` | 捐赠博物馆（走到柜台一键捐可捐矿物/古物）|
| `read_book`(name=…) | 读书（消耗技能书领配方）|

### `quest(ops)` — 任务
`list` `progress` `accept`

### `storage(ops)` — 箱子
`scan`(扫箱) `store`(存进箱) `take`(取) `smart`(智能堆叠) `layout`(箱子网络) `default`/`cleardefault`/`tag`(设默认箱/清/标记)

### `daily(ops)` — 过日子
`sleep`(睡觉) `settle`(确认过夜结算) `eat`(吃食物) `wear`(穿/脱衣物 name/slot/hand) `lie_bed`(躺床不过夜) `heartbeat`(心跳间隔) `pause`(后台不暂停) `peek`(看恒在干嘛) `whiteboard`/`wb_read`/`wb_pin`/`wb_clear`(白板笔记) `appearance`(捏脸)

### `map(ops)` — 导航 **跨图唯一走这个**
`lookup`(查地点功能+出口) `query`(功能反查) `go`(走到目标,自动多段寻路/交通) `walk`(走到指定POI) `movetile`(同图精确走位) `npc`(找NPC) `warp_safe`(紧急逃脱)

### `festival(ops)` — 节日
`today` `next` `go` `info` `interact` `answer` `shop` `eggs`(找蛋) `egg_note`(纸条) `egg_run`(捡蛋) `dance`(跳舞邀请) `help`(玩法) `prep`(备战) `poi`(限定点)

### `settings(ops)` — 系统/设置（合并"捏脸设置"进来，不再拆）
`status`(看所有设置+退役工具) `retire`(退役工具) `reactivate`(召回) `appearance`(捏脸) `customize`(起名) `color`(颜色条) `hair`/`shirt`/`hat`/`colorpreset`(外观参考)
> 旧配置路径仍可用：`settings(setting="async", value="on")`（heartbeat/async/auto_sleep/moss…）

### `fish(ops)` — 钓鱼（2026-08-22 修复注册，现已可达）
`go`(去钓) `info`(查某地鱼) `spots`(钓点知识) `bobber`(浮漂样式) `crab`(蟹笼概览) `crab_water`(找水) `crab_place`(放笼) `crab_bait`(放饵) `crab_collect`(收笼)

---

## 🛠️ 三、11 个独立工具（无域等价物，直接调）

| 工具 | 干嘛的 |
|---|---|
| `which_role` | 确认端口↔角色（AI/房主）|
| `screenshot` | 截图看画面（AI 的"眼睛"）|
| `async_config` | 长脚本自动异步配置 |
| `script_start` / `script_status` / `script_stop` | B1 异步脚本控制 |
| `session_status` / `session_set` / `session_export` | 会话上下文缓冲 |
| `run_script` | 跑任意脚本（兜底）|
| `help` | 查某域详细 ops/坑（docstring 精简后的细节兜底；不传=列话题）|

---

## 🎯 四、常用场景速查（"我想… → 调…"）

| 我想干嘛 | 调用 |
|---|---|
| 起床看今天/天气/运势 | `check(what="status")` |
| 环视四周有啥 | `check(what="look")` |
| 锄地种一片 | `farm(ops="till plant")` |
| 浇水 / 收菜 | `farm(ops="water")` / `farm(ops="harvest")` |
| 砍树 / 清地 | `farm(ops="chop")` / `farm(ops="clear")` |
| 摸动物 / 挤奶 | `care(ops="animals milk")` |
| 穿/脱穿戴物（衣/裤/帽/鞋/戒指/饰品）| `daily(ops="wear", name="铁头靴")` / `daily(ops="wear", slot="boots")` |
| 去挖矿 / 看进度 | `mine(ops="go")` / `mine(ops="progress")` |
| 自动炸矿 | `mine(ops="bomb_mine")` |
| 去钓鱼 | `fish(ops="go", location="Beach")` |
| 找某 NPC / 跟他聊天 | `map(ops="npc", name="艾米丽")` → `social(ops="chat")` |
| 送礼提好感 | `social(ops="gift", npc_name=…, item_name=…)` |
| 点面前的东西 | `scene(ops="interact")` |
| 点指定格（柜台/炉子）| `scene(ops="at", tile_x=…, tile_y=…)` |
| 开商店买东西 | `menu(ops="shop", place="皮埃尔商店")` |
| 点菜单选项 | `menu(ops="click", option=1)` |
| 推进剧情/对话 | `menu(ops="advance")` |
| 翻箱找东西 | `storage(ops="scan")` → `storage(ops="take", …)` |
| 去某大广场/跨图 | `map(ops="go", destination="Town")` |
| 睡觉 / 蹭床 | `daily(ops="sleep")` |
| 过夜结算进新一天 | `daily(ops="settle")` |
| 今天/明天节日 | `festival(ops="today")` / `festival(ops="next")` |
| 去参加节日 | `festival(ops="go")` |
| 查献祭还缺啥 | `menu(ops="bundle_kb", query="工艺室")`（知识库）/ `menu(ops="bundle")` 实地看 |
| 捏脸 / 起名 | `settings(ops="appearance", …)` / `menu(ops="customize")` |
| 关掉卡住的弹窗 | `menu(ops="cancel")` |
| 后台跑长脚本 | `script_start(name="fish_run", args="…")` |
| 截图看自己 | `screenshot()` |

---

## 🔄 五、旧工具 → 域形式 对照（AI 不用记旧名了）

| 旧独立工具 | 现在这样调 |
|---|---|
| `check_status` / `check_backpack` / `look_around` / `silo_status` / `mastery_status` / `building_list` / `machine_report` | `check(what="status"/"backpack"/"look"/…)` |
| `walk_to` / `go_to` / `move_to_tile` / `find_npc` | `map(ops="walk")` / `map(ops="go")` / `map(ops="movetile")` / `map(ops="npc")` |
| `interact_at` / `interact` / `use_tool` / `face` / `select_item` | `scene(ops="at")` / `scene(ops="interact")` / `scene(ops="use")` / `scene(ops="face")` / `scene(ops="select")` |
| `read_menu` / `menu_click` / `press_key` / `advance_story` / `cancel` / `shop_visit` / `sell_to_shop` / `forge` / `process_geode` | `menu(ops="read"/"click"/"key"/"advance"/"cancel"/"shop"/"sell"/"forge"/"geode")` |
| `go_sleep` / `confirm_settlement` / `eat_item` / `set_appearance` / `wear` / `lie_bed` | `daily(ops="sleep"/"settle"/"eat"/"appearance"/"wear"/"lie_bed")` |
| `scan_chests` / `chest_store` / `chest_take` | `storage(ops="scan"/"store"/"take")` |
| `list_quests` / `quest_progress` | `quest(ops="list"/"progress")`；接单走板上 `menu click(button=accept…)`（accept_quest 已退役） |
| `chat_npc` / `gift_npc` / `give_item` / `send_chat` / `emote` / `check_friendship` | `social(ops="chat"/"gift"/"give"/"send"/"emote"/"friendship")` |
| `moss_run` / `berry_run` / `spot_run` / `pickup_scene` | `scene(ops="moss"/"berry"/"spot"/"pickup_scene")` |
| `go_mining` / `bomb_*` | `mine(ops="go"/"bomb_mine"…)` |
| `go_fishing` / `bobber_style` | `fish(ops="go"/"bobber")` |
| `set_appearance` / `character_customize` / `color_pick` / `list_hair_ref` 等 | `settings(ops="appearance"/"customize"/"color"/"hair"…)` |
| `bundle_status` / `bundle_kb` | `menu(ops="bundle")` / `menu(ops="bundle_kb")` |
| `museum_donate` / `read_book` | `menu(ops="donate")` / `menu(ops="read_book")` |

---

## 💡 六、一句提醒
- **AI 全程走域工具**，原始端点（/state /interact /click /position）AI 不会直调，只是文案里的坐标/动作提示。
- 手机前端若**直调被隐藏的旧工具名**（`walk_to`/`go_sleep`/`menu_click`…）会报不存在 → 改走域形式，或服务端 `NAGI_FULL_TOOLS=1` 启动回全量。
