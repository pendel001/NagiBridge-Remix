# NagiBridge MCP 工具速查手册（16 个：13 域入口 + `intent` / `screenshot` / `help`）

> AI 眼里只有 **16 个工具**：13 个域入口 + 3 个独立工具。其余旧工具**全部收进域里**（函数还在，只是 AI 不再直调）。
> 两个入口：**🎯 `intent` 意图单子**（首选：`show` 看单子 → `do` 敲编号，**不用记工具名和参数**）
> ｜**域工具**（带参/批量/脚本那条路：`域名(ops="…", kw={…})`）。两套背后是同一批实现，走哪条都行。
> 🔍 某域要细节 → **`help(域名)`**（docstring 精简，深度在这个兜底）；单子玩法 → `intent(ops="help")`。
> ⚠️ 子参数收在 `kw` 里（如 `farm(ops="till", kw={"x":40,"y":20})`）；**键名必须 = 函数签名参数名**，
> 写错会被 `_ops_run` **静默丢掉、不报错**（本项目最容易踩、最难发现的坑）。
> 原始端点（`/state` `/interact` `/click` `/position` `/menu`）**不是** AI 能直调的 MCP 工具，只是文案里的坐标/动作提示。

---

## 🧭 一、总纲（先读这段）

- **每个"域"就是一把瑞士军刀**：`farm(ops="till plant water")` 一次做多件事，ops 空格/逗号分隔，**一份 kw 共用**。
- **首选走单子**：`intent(ops="show")` 看这一刻能做的事（一行一件，`←` 后面是理由）→ `intent(ops="do", kw={"code":"1"})` 敲编号
  （`"1,4"` 多选 · `"1=2,4=7"` 各多少）。⚠️ **「单子上没有」≠「做不到」**，也可能只是这一刻不适用。
- **域工具是底层那条路**：规划类 / 参数爆炸 / 跨图 / 长脚本 —— 单子给不了的维度都在这儿。
- **参数一律放 `kw`**，别拼进 ops 串；键名写错 = 静默丢掉（没反应、不报错）。
- **长任务自动转后台 + 自动注入 AI 端口**（别手动后台，别自己拼 `--port`）；收工自动播报，不用轮询。

---

## ⚙️ 二、13 个域入口（ops 列表 + 参数速查）

### `check(what)` — 查询（状态/背包/机器/收藏）　*注意：这个域的参数叫 **`what`**，不是 `ops`*
`status` `backpack` `worn` `look` `machines` `mine` `silo` `mastery` `buildings` `chests` `storage` `quest` `ready` `profile` `role`

| what | 干嘛的 |
|---|---|
| `status` | 完整状态（位置/时间/血/体力/钱/背包格数）|
| `backpack` | 背包逐格详细（价值/星级/属性）|
| `worn` | 穿戴物（衣服/戒指/饰品）|
| `look` | 环视周围（NPC/怪物/物品/地形）|
| `machines` | 全农场机器清单 |
| `mine` | 下矿进度 |
| `silo` / `mastery` / `buildings` | 筒仓干草 / 精通 / 木匠建筑 |
| `chests` / `storage` | 当前图箱子 / 箱子网络视图 |
| `quest` | **打开任务日志**（回执让你 `menu read` 读卡；**不直接返回列表**）|
| `ready` | 就绪握手实况（卡在"等待其他玩家"框时查，两侧都读才看得出死锁在哪头）|
| `profile` | 我的技能等级 + 职业分支（如是否 Luremaster 蟹笼免饵）|
| `role` | 确认端口↔角色（AI=谁 / host=谁）——**睡觉/协作前先查** |

**📐 `check` 参数速查**

| 带参的 what | 参数（括号内=默认） | 说明 |
|---|---|---|
| `chests` | `chest`(-1) | `chest=N` 看第 N 个箱子的全清单 |
| `look` | `radius`(10) | 环视半径 |

> 其余 `what` 全部**无参**。💡 查概览用 `status`、查逐格用 `backpack`，别都调一遍浪费 token。

### `farm(ops)` — 农活（锄/种/水/收/机器）*AI 必走*　**只能在农场/温室/姜岛**
`farm(ops="till plant water harvest scythe fertilize clear plot chop load plan pond pond_add pond_feed pond_collect pond_fish animals milk pet petwalk buy doors hay statue place break")`

| ops | 干嘛的 |
|---|---|
| `till` / `plant` | 锄地 / 播种（**各只有一个实现**；`ops="till plant"` 一次锄+种，一份 kw 共用）|
| `water` | 浇水（自动跳过下雨；水壶没水先装满）|
| `harvest` / `scythe` | 收成熟作物 / 镰刀收（蒜/花/茶）|
| `fertilize` / `clear` / `plot` | 撒化肥 / 清杂草石头树桩 / 地皮连通域规划 |
| `plan` | **方形规划（只算格，不动手）** —— 洒水器布局三件套的第一步 |
| `tillfield` / `hoe` / `plantlayout` / `sow` | `till` / `plant` 的别名（蓄力锄 / 布局播种）|
| `chop` / `clearground` | 砍树 / 清指定格 |
| `load` | 收机器 + 往机器放料**一条过**（`item` 留空 = 只收不放；`here=True` = 只弄脚下这一间屋）|
| `pond` / `pond_add` / `pond_feed` / `pond_collect` / `pond_fish` | 鱼塘：状态/放鱼/喂/领鱼籽/直钓（⚠️ 简写 `feed`/`collect`/`fish` **不是** op，写全名）|
| `animals` / `milk` / `shear` / `pet` / `petwalk` | 摸动物+收产物 / 挤奶 / 剪毛 / 摸猫狗 / 拟人遛 |
| `buy` / `doors` / `hay` / `statue` / `喂水` | 买动物 / 开关畜棚门 / 加干草 / 摸祈福雕像 / 宠物水碗 |

**📐 `farm` 参数速查**

| op | 参数（括号内=默认值） | 说明 |
|---|---|---|
| `till` / `clear` | **`x`,`y`（必填，不传直接报错）** `rows`(1) `length`(1) `direction`("horizontal")；另有矩形两角 `x1,y1,x2,y2`；till 另有 `layout`(0)，clear 另有 `radius`(0=圆形) `margin`(2) | 锄地 / 清杂草石头树桩（clear **默认往外多清 2 格**，防田边杂草长进田里）|
| `plant` | `seed_name`(必填) **`x`,`y`(必填)** `rows` `length` `direction` **或** `x1,y1,x2,y2`；`layout`(0) `direct`(False) `trellis`(False) | 播种（跳过已种/设施格；带 `layout` 就按洒水器布局种）|
| `fertilize` | `fertilizer_name`(必填) `x`,`y` `rows` `length` `direction` | 撒化肥 |
| `water` / `harvest` / `scythe` | `radius`(15) | 浇水无参 / 收 / 镰刀收 |
| `plot` | `x`(-1) `y`(-1) `radius`(15) `all_plots`(False) | 连通域规划（不传 x/y = 以自己为心）|
| `chop` | `area`("") —— 值写「几个数」：**4 个数=矩形两角 / 3 个数=圆心+半径** | 砍树（限定区域时会先走过去再找）|
| `load` | `item`("") `machine_type`("") `location`("") `count`(0) `here`(False) | 收/放一条过；`item` 留空 = 只收不放 |
| `place` / `break` | `name`,`x`,`y` ／ `x`,`y`(必填) `steps`(1) `radius`(0) | 同 `scene` 同名 op |
| `pond_add` / `pond_feed` / `pond_collect` / `pond_fish` | `x`(-1) `y`(-1)；`pond_add` 另有 `item`(必填) | 鱼塘四件事（不传坐标 = 唯一/最近那口）|
| `buy` | `animal_type` `name` `building`("") | 买动物 |
| `petwalk` | `include_petted`(False) | 拟人遛（默认跳过已摸过的）|
| `hay` / `doors` | `dry_run`(False) / `doorX` `doorY` | 加干草（True=只看不加）/ 只翻**同图 4 格内**够得着的门，够不着的进 `skipped` |

> `direction` 只认两个值：`"horizontal"`(默认) / `"vertical"`（**其它字符串一律当 vertical**，别写"横"/"竖"）。

**💧 大田洒水器布局（可选三件套 `plan` → `till` → `plant`，`layout` 四档含义相同）**

| `layout` | 布局 | 洒水器 | 覆盖 | 留格规则 |
|---|---|---|---|---|
| `0` | 标准整块 | 不预留 | — | 整块全种；锄法=蛇形逐格走位（任何锄头等级都行）|
| `1` | 初级（Sprinkler）| 稀疏十字 | 4 格 | 对角网格（行隔2列隔3每行斜移1）；锄法=**按洒水器逐台锄它上下左右 4 格** ⇒ **不吃蓄力、与锄头等级无关** |
| `2` | 高级（优质，3 的倍数）| 每 3×3 中心 | 8 格 | 田宽高**先裁成 3 的倍数**；整块蓄力锄（吃满当前锄头等级）|
| `3` | 铱（5 的倍数）| 每 5×5 中心 | 24 格 | 同上裁成 5 的倍数；**爬架作物不适用**（`plant_tiles` 返回空）|

- `plan(x1,y1,x2,y2, layout=0, hoe_level=-1, trellis=False)` —— **纯计算只报格**，先拿它看要锄/种哪些格。
  `hoe_level` 0→1格 / 1→3线 / 2→5线 / 3→3×3 / 4→6×3，`-1`=自动读当前手持锄头。
- `plant` 不传 `layout` 就是 `0`（整块），**只管种、不摆洒水器**时直接用 `plant`，不用这三件套。
- `trellis=True` = 爬架作物（啤酒花/青豆/葡萄，**不可通过格**）⇒ 自动**种2留1**留出走道。
- `direct=True` 用瞬移（格多时快）/ 默认走位拟人。
- ⚠️ **已知限制**：`layout=0/2/3` + 金/铱锄时，3×3 / 6×3 的蓄力落点**没实测校准过** ⇒ `plan` 报 **0 处锄地站位**并自己说明。
  **这不是出错，是刻意不猜**；`layout=1` 不吃蓄力站位，不受影响。

### `mine(ops)` — 下矿（冲层/刷矿/炸矿）　**只在矿井/头骨/火山**
`mine(ops="go progress bomb_status bomb_plan bomb_place bomb_collect bomb_ladder bomb_retreat bomb_mine bomb_volcano organize")`
`go`(去挖矿) `progress`(进度) `bomb_*`(单步炸矿) `bomb_mine`(自动) `bomb_volcano`(火山) `organize`(整理背包)

**📐 `mine` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `go` | `mode`("rush") `start`(1) `target`(None) `ore`(None) `cycles`(5) `hp_threshold`(30) `food_sta` `food_hp` `food_buff` `resume`(True) | `mode=rush` 冲层（往更深敲到 `target`）/ `mode=farm` 刷矿（电梯直达层反复刷指定 `ore`）。⚠️ `hp_threshold` **只是"血量低于它就不肯下矿"的出门线**，**不是吃食物的线**（吃食线在 `bomb_common.EAT_HP_PCT=60`）——别拿它当吃的线调 |
| ↳ `ore` 取值 | `Copper`(铜,21层) / `Iron`(铁,41层) / `Gold`(金,71层) | ⚠️ **煤没有 ore 选项**——`farm` 铁层(41) 会顺手清尘埃精灵/蝙蝠，它们掉煤 |
| `bomb_plan` | `radius`(14) `min_covered`(3) `top`(3) | 找"炸一下覆盖 ≥`min_covered` 块岩体"的锚点，给前 `top` 个 |
| `bomb_place` | `x`,`y`（**必填**） | 在指定格放炸弹 |
| `bomb_collect` | `max_items`(12) | 炸完收掉落 |
| `bomb_mine` | `target`(0) `bomb`("Bomb") `min_covered`(3) `follow_host`(True) `lead`(2) `autodrop`(0) `one_floor`(False) `food_hp` `food_sta` `food_buff` | 全自动炸矿。`target=0` = 按当前层自适应；`one_floor=True` = 逐层模式（同步、只跑一层返回摘要）——**默认冲层模式是异步后台跑，推荐** |
| ↳ `follow_host`/`lead` | True / 2 | host 在矿里就一起冲层，目标层 = host 层数 ± `lead` |
| `bomb_volcano` | `bomb`("Bomb") `min_covered`(3) `hp_threshold`(30) `max_minutes`(None) `poll`(2.5) `food_hp` `food_sta` | 火山专用。⚠️ **要求 host 已在矿/火山里**才放行（火山特殊瓦片无法程序化换层）|
| `organize` | `disable`(False) `reset`(False) | 整理背包；`reset=True` 恢复默认 |

**💣 `bomb` 三个取值**：`"Cherry Bomb"` 樱桃 / `"Bomb"` 黑 / `"Mega Bomb"` 超级。

- 点名的那种**背包里没有**（或不传）→ 自动按 **黑 > 超级 > 樱桃** 挑背包里**实际有的**。
- 爆炸范围：樱桃 = 边长 7 的**十字**（缺 4 角）/ 黑 = **11×11 方块** / 超级 = **15×15 方块**。
  ⚠️ 黑和超级**会炸伤自己**（实测黑弹掉 3 血）。
- 🚫 **协同不是独立 op**：`bomb_mine` 发现**没炸弹且 host 同矿井**时自动转内部协同；协同期间又拿到炸弹会自动回炸矿。

### `social(ops)` — 社交
`social(ops="chat gift give hand send emote friendship movie snack")`

**📐 `social` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `chat` | `name`("") | 跟 NPC 搭话（不传 = 面前那个）|
| `gift` | `npc_name` `item_name`（**都必填**） | 送礼给 NPC |
| `give` | `player_name` `item_name`（**都必填**） | 给**玩家**物品（手持右键正式赠予，**一次一个、要等对方同意**）|
| `hand` | `player_name` `item_name`（必填）`count`(0=整叠) | 递给玩家：**走过去丢他脚边**、磁吸自动收，**可整叠** |
| `send` | `message`（必填） | 发聊天消息 |
| `emote` | `name`("爱心") | 表情（对方能看到）|
| `friendship` | `name`（必填） | 查好感 |
| `movie` / `snack` | `npc`("") / 无参 | 电影院知识 / 零食知识 |

> ⚠️ **`give` vs `hand`**：`give` 是**面对面正式赠予**（要等同意、一次一个）；`hand` 是**走过去丢他脚边**（磁吸自动收、可整叠）
> —— 想整叠给、或对方不在手边，用 `hand`。

### `scene(ops)` — 场景交互（点东西/工具/转身/捡）
`scene(ops="at front interact use face select sit stand seats pickup pickup_scene berry spot moss walnut garbage rock forge_help drop furniture decor place break pan maze maze_seg maze_walk")`

| ops | 干嘛的 |
|---|---|
| `at(tile_x,tile_y)` | 点指定格（柜台/电视/机器，对角也行）|
| `front` / `interact` | 点面前的东西（= 确认键）|
| `use(name)` | 挥工具 |
| `face(direction)` / `select(name)` | 转身（0上1右2下3左）/ 选中背包物品拿手上 |
| `sit(x,y[,face])` / `stand` / `seats(radius)` | 坐椅子（自动就位；`face=` 只对部分座位生效）/ **起身** / 扫可坐物（✋=可改朝向）|
| `pickup(tile_x,tile_y)` / `pickup_scene` | 拿起家具 / 捡当前场景可拾取物 |
| `berry` / `spot` / `moss` / `walnut` | 摇/摘灌木与果树（浆果·茶叶·果子）/ 挖斑点蚯蚓 / 绿雨搜刮苔藓 / 敲金核桃 |
| `garbage` / `pan` / `rock` | 翻垃圾桶（别名 `rummage`）/ 淘金 / 室外镐击（采石场·挖掘场·蚌矿场）|
| `drop` / `furniture` / `decor` | 丢背包物品 / 扫家具 / **🪵 地板墙纸真值表** |
| `forge_help` / `maze` `maze_seg` `maze_walk` | 锻造台附魔攻略 / 迷宫视图·走法链·走迷宫 |

**📐 `scene` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `at` | `tile_x`,`tile_y`（**必填**） | ⚠️ 是 `tile_x`/`tile_y` **不是** `x`/`y`（写错静默丢掉）|
| `use` / `select` / `face` | `name`(None) / `name`(必填) / `direction`(必填) | 不传 name = 用当前手持工具 |
| `sit` / `seats` / `stand` | `x`,`y`(必填) `face`(None) / `radius`(12) / 无参 | 坐 / 扫可坐 / 起身 |
| `pickup` | `tile_x`,`tile_y`（必填） | 拿起家具（⚠️ 同 `at`，是 `tile_x`）|
| `pickup_scene` | `max_items`(30) | 捡当前场景可拾取物 |
| `moss` | `radius`(25) `target_max`(80) `rounds`(5) `dry_run`(False) | 绿雨搜苔藓；`dry_run=True` 只探不采 |
| `rock` | `dig`(True) `radius`(14) `max_break`(0) `break_stone`(False) | 室外镐击；`dig=False` 只扫不敲 |
| `garbage` | `loc`("") `pos`("") `wait`(1.0) `dry_run`(False) | 翻垃圾桶；`dry_run=True` 只报位置 |
| `pan` | `dry_run`(False) `radius`(3) `timeout`(20) | 淘金 |
| `walnut` | `radius`(0) `max_count`(1) `dry_run`(False) | 敲金核桃 |
| `drop` | `name` `count`(1) `items` | 丢背包物品（直接消失不落地）：一种用 `name`+`count`；**多种一次丢**用 `items="木头,石头:3"`。⚠️ 名字口径 = `check backpack` 里显示的；**菜单开着时别丢** |
| `place` / `break` | `name`,`x`,`y` / `x`,`y`(必填) `steps`(1) `radius`(0) | 🪵 放地板/墙纸只能点**地板格/墙格**（点错游戏静默不理）⇒ 拿不准先 `decor` |
| `decor` | 无参 | 当前图地板格/墙格清单 + 每间房现在铺的什么 |
| `maze` / `maze_seg` | `radius`(14/15) `gx` `gy` | 迷宫视图 / 走法链 |
| `maze_walk` | `waypoints`("x,y x,y …") `location`(None) `max_wait`(18) `max_seg`(200) | ⚠️ **它其实是通用多段走位**，主门牌是 `map(ops="walk_multi")`；此处保留旧名兼容 |

> ⚠️ **`at` 和 `pickup` 的参数名是 `tile_x`/`tile_y`**，其余走位类多是 `x`/`y`
> —— 本项目最容易写错、且**错了不报错只是没反应**的地方。

### `menu(ops)` — 菜单/界面（开 → 看 → 点）
`menu(ops="read click key advance skip cancel shop sell bin craft recipes craftables forge tailor geode geodes customize bundle bundle_kb donate read_book journal know number display_fill display_takeback minigame minigame_state levelup_choose")`

| ops | 干嘛的 |
|---|---|
| `read` / `click` / `key` | 看当前菜单 / 点菜单项（自适应）/ 按键盘（`ok`/`esc`/数字）|
| `advance` / `skip` | 推进剧情对话 / **整段跳过**（`skippable=true` 的事件才跳得动）|
| `cancel` | 关当前弹窗 / 撤睡觉就绪 |
| `shop` / `sell` / `bin` | 逛店 / 卖商店 / 投出货箱 |
| `craft` / `recipes` / `craftables` | 合成 / 菜谱 / 配方 |
| `forge` | 锻造台 |
| `tailor` | 🧵 缝纫机（只认**已经开着**的 `TailoringMenu`）：放料 / 换槽 / 开缝 / 取产物 / 退料 |
| `geode` / `geodes` | 砸晶球（×1 / 批量）|
| `bundle` / `bundle_kb` / `donate` | 献祭板实地看 / 献祭知识库查 / 捐赠博物馆 |
| `read_book` | 读书·读纸条（消耗技能书领配方）|
| `journal` / `know` | 开任务日志（再 `read` 读卡，含每子目标 current/max）/ 查特别订单详情 |
| `customize` / `number` / `levelup_choose` | 捏人起名 / 数量输入框 / 技能升级选职业分支 |
| `minigame` / `minigame_state` | 赌场小游戏点按钮 / 读牌面·转盘 |
| `display_fill` / `display_takeback` | 农展台放满 / 收好 |

**📐 `menu` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `click` | `option`(-1) `button`("") `x`(-1) `y`(-1) `item`("") `right`(False) `quantity`(1) `action`("") `real`(False) `slot`(-1) `category`(-1) | 万能点击器。`button` 用**按钮名**（`ok`/`upperRightCloseButton`/`forward`/`back`/`rewardBox`/`mainButton`）；`slot`=领指定格；`real=True`=真鼠标 |
| ↳ `action` | `"claim"` 领取 / `"discard"` 丢桶腾格（回收返金） | 🚫 **满包接鱼/领箱**：先 `action=discard` 丢桶腾格，再 `action=claim` 领取（不想要就直接 `button=ok` 关掉）|
| `key` | `key`(必填) `count`(1) `hold`(0) | `key` 可填 `ok`/`esc`/数字 |
| `number` | `value`(-1) `confirm`(False) | 数量输入框；`value=-1` 只读不动 |
| `shop` / `sell` / `bin` | `place`(必填) `want`("") / `name`(必填) `count`(-1) / `name`("") `sell_all`(False) | 逛店（`want`=想买的东西，会帮你找）/ 卖（`count=-1`=全卖）/ 投出货箱 |
| `craft` / `forge` | `item_name`(必填) `count`(1) / `item1`(必填) `item2`("") `mode`("combine") `target`(0) | 合成 / 锻造台附魔合成 |
| `tailor` | `place`("") `slot`("left") `action`("") | 缝纫机：`place`=放进槽那件、`slot`=`left`/`right`、`action`=`start`/`take`/`clear`（留空=只放料）|
| `geodes` / `customize` | `count`(1) / `name` `farmname` `favorite` | 批量砸晶球 / 捏人弹窗 |
| `bundle` / `bundle_kb` / `read_book` | `area`("") / `query`("") / `name`(必填) | 献祭板不传=当前 / 知识库查询 / 读书 |
| `levelup_choose` | `side`("") `profession`(-1) | **不带参 = 只读**当前给的左右选项（配 `check(what="profile")` 分析）；定分支传 `side=left/right` 或 `profession=职业id` |
| `minigame` | `action`("") `x`(-1) `y`(-1) | 如 `action=hit/stand/bet10` |
| `display_fill` | `items`("") | 如 `items="钻石,山羊奶酪"` |

> 🧾 **关闭菜单**：一律 `click(button="upperRightCloseButton")`；但 `ItemGrabMenu`/交付容器要用 `button=ok` 确认才关。
> ⚠️ **`cook`（做饭）在 `daily` 域**，不在 `menu`。

### `script(ops)` — 脚本/异步
`script(ops="continue stop async")`

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `continue` / `stop` | `job_id`("") | 不传 = 当前那个任务 |
| `async` | `show`(False) `add` `remove` `enable` | 自动异步白名单管理 |

> ⚠️ 长任务便利工具**自动后台**（别手动后台）；进度**自动播报**（收工带总时长），**不需要轮询**。
> ⚠️ 参数放 `kw`，别拼进 ops 串。

### `storage(ops)` — 箱子
`storage(ops="view store take find default tag")`

**📐 `storage` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `view` | `box`(-1) | 看箱子；`box=N` 看第 N 个的全清单；`box` 也吃箱子名/色名/`#hex`/`"x,y"`（如 `"内置冰箱"`）|
| `store` | `what`("") `items`("") `target`("") `keepTools`(True) `all`(False) | `what`/`items` = 只存哪些（名字可带 `xN`）；**留空 = 归位**（只把已有同类堆叠回去）；`target` = 指定箱；`all=True` 全存腾空间 |
| `take` | `items`("") `x`(-1) `y`(-1) `name`("") `count`(999) | 单箱取 = `x`,`y` + `name`；批量取 = `items`（项内自带 `×N` 优先）|
| `find` | `name`("") | 模糊查哪个箱里有某物 |
| `default` | `x`(-1) `y`(-1) `clear`(False) | 设默认箱；`clear=True` 清掉 |
| `tag` | `tag`("") **`target`(必填)** `color`("") | 名字变「本名(标记)」；`tag` 留空 = 只清标记留本名 |

> 🤖 存取都**自动走位**（批量只走到第一个）——别靠编号逐箱翻，用 `find` 定位。
> ⭐ 每个箱子前自动带**【类目标签】**（矿/古物/鱼/种子/作物/农产/建材/料理/装备）。
> ⚠️ 改色**别用纯 `#000000`**（= 默认木纹，会被当成"未染色"）；要黑箱用暗灰 `#303030`。

### `daily(ops)` — 过日子
`daily(ops="sleep settle eat cook wear lie_bed heartbeat peek whiteboard wb_read wb_pin wb_clear appearance")`

**📐 `daily` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `sleep` / `lie_bed` | `who`（**必填**） | `sleep` = 真过夜；`lie_bed` = **只躺不睡**（随时 `map walk` 走离床格即可）。`who`：传自己名 = 自己床；传别人名 = 那人的床（一起睡）。**传对名字就不用先回家**，会自动走过去。名字写错 → 报错并列出可选名 |
| ↳ 🏝️ 姜岛 | 同上 | `IslandFarmHouse` 大通铺，岛上**没有"谁的床"**：传**正躺着的别人** = 挤他那张；传自己 / 那人还没躺 = 挑一张**空床安静睡**；全占满 → 报错点名谁在哪张床 |
| `eat` | `name`("") `item_name`("") | 吃食物回血/体力 |
| `cook` | `recipe_name`（**必填**，食谱用英文原名）`count`(1) | 做饭；**会先走到厨房**，走不过去直接报错；满包会被前置拦下不吃材料 |
| `wear` | `name`(None) `slot`(None) `hand`(None) | `name` = 穿上（**自动判槽位**，替下的回背包）；`slot` = 脱下该槽（`boots`/`leftRing`/`rightRing`/`trinket`/`hat`）；`hand` = **仅戒指**（`1`/`left`、`2`/`right`，或传"要换掉的那枚戒指名"）|
| `heartbeat` | `minutes`(5) | 心跳间隔（0 = 每次工具返回都显示）；真身在 `settings`，这里是别名 |
| `whiteboard` / `wb_pin` | `content`（必填） | 写白板 / 钉白板 |
| `appearance` | `hair` `hair_color` `skin` `shirt` `pants` `hat` `acc` `eye_color` `pants_color` | 捏脸 |
| 无参 | `settle` `peek` `wb_read` `wb_clear` | 过夜结算 / 看 host 在干嘛 / 读白板 / 清白板 |

> ⚠️ **睡别人床 / 协作前先 `check(what="role")`** —— 端口按启动顺序分配、重启可能翻转，**认错角色 = 挪了 host 的人**。
> ⚠️ 饰品需**战斗精通**，未解锁会被权威拦截。

### `map(ops)` — 导航　**跨图唯一走这个**
`map(ops="lookup query go walk walk_multi npc warp_safe unlocks")`

**📐 `map` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `go` | `destination`(地点名/POI) `npc`(NPC名) | **跨场景切换的唯一入口**（走出口瓦片/门/买票的真实路径，**不瞬移**）；两者二选一 |
| `walk` | `poi_name`(POI 名) **或** `x`,`y`(同图坐标) | **二选一，都不给=报错**。给 POI 会**到点自动应用结构化站位+朝向**（水池朝右、柜台朝上），交互仍要自己 `scene at/interact`；给坐标 = 同图精确走位（**只走同图**，跨图用 `go`）|
| `walk_multi` | `waypoints`("x,y x,y …") `location`(None) `max_wait`(18) `max_seg`(200) | **多段走位**（别名 `闲逛`/`多段走`）：一串坐标依次走。⚠️ 每段必须**精确落到目标格**才算到达，落在相邻格标 🟡（**兜底 ≠ 到达**）|
| `npc` / `lookup` / `query` | `name`(必填) / `location`(必填) / `function`(必填) | 找 NPC（跨图）/ 查地点功能+出口 / 功能反查（"哪里能买到 X"）|
| `warp_safe` / `unlocks` | 无参 | 紧急逃脱（卡住时用）/ 查存档解锁（矿洞·巴士·下水道·姜岛·精通·**火山近路**）——**走捷径前先查** |

> 🚦 交通优先级：**图腾柱 > 矿车 > 走路**（在农场且有对应图腾柱时自动用）。
> ⚠️ 参数**必须放 `kw` 对象**，别拼进 ops 串里。

### `festival(ops)` — 节日
`festival(ops="today next go info interact answer shop eggs egg_note egg_run dance prep poi strength ice_fish maze maze_walk display_fill display_takeback help")`

**📐 `festival` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `interact` / `answer` | `name`("") / `answer`(0) | 节日互动 / 应答（答题类活动）|
| `egg_run` / `egg_note` | `route`("") | 复活节捡蛋 / 记纸条路线 |
| `dance` | `target`("") | 花舞节邀请跳舞（不传=默认对象）|
| `strength` | `delay`(400) | 力量测试，`delay` = **毫秒** |
| `maze_walk` | `waypoints`("") `location`(None) `max_wait`(18) `max_seg`(200) | 走迷宫。⚠️ 通用多段走位主门牌是 `map(ops="walk_multi")`，此处旧名兼容 |
| `display_fill` | `items`("") | 农展台放满 |

> 其余（`today`/`next`/`go`/`info`/`shop`/`eggs`/`help`/`prep`/`poi`/`maze`/`ice_fish`）**无参**。
> ⚠️ 节日场地平时不开放，**别用 `map go` 导航节日图**（Temp/DesertFestival 只在节日存在）。

### `settings(ops)` — 系统/设置（含捏脸外观、会话缓冲）
`settings(ops="status retire reactivate appearance customize color hair shirt pants hat colorpreset confirm_look session_status session_set session_export")`

**📐 `settings` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `appearance` / `customize` / `color` | `hair` `hair_color` `skin` `shirt` `pants` `hat` `acc` `eye_color` `pants_color` / `name` `farmname` `favorite` / — | 捏脸 / 起名 / 颜色条 |
| `hair` `shirt` `pants` `hat` `colorpreset` | — | 外观编号参考表 |
| `confirm_look` | — | **核对捏人形象，`ok` 前必做** |
| `retire` / `reactivate` | — | 退役工具 / 召回 |
| `session_set` | `setting`,`value`（**都是字符串**） | 改会话缓冲；`setting` **只有** `max_turns` / `export_format` |
| `session_status` / `session_export` | — | 看缓冲条数·最近3条·文件路径 / 导出**给人看的 md** |
| `status` | — | 看所有设置 + 已退役工具 |

> ⚠️ **捏脸 = 创建定型**：`ok` 之后捏人**自动退役、不可逆**。
> 旧配置路径仍可用：`settings(setting="async", value="on")`（heartbeat/context_turns/async/state_interval/mode/auto_sleep/auto_sleep_time/pin/moss）。

### `fish(ops)` — 钓鱼
`fish(ops="go info spots bobber rod crab crab_water crab_place crab_bait crab_collect crab_diag crab_retract")`

**📐 `fish` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `go` | `location`(None) `max_casts`(0) `no_sleep`(True) | `location=None` = **就地钓**（须自己已站到水边）；指定 `Beach`/`Mountain`/`Forest`/`Town` = 先走真实路径到校准钓点再钓（**不是 warp**）。`max_casts=0` 不限（体力<20/背包满/太晚/抛不出去就收杆）；`no_sleep=False` 钓完回家睡 |
| `info` / `spots` | `location`(必填) / — | 查某地能钓什么 / 看钓点 |
| `bobber` | `style`("dice") | 浮漂样式 |
| `rod` | `action`("show") `item`("") | 看/上饵/装钓具 |
| `crab_place` / `crab_bait` / `crab_water` | `count`(5) `radius`(15) `bait`("Bait") / `bait`("Bait") / `radius`(15) | 放笼 / 放饵 / 找可下笼的水 |
| `crab_diag` / `crab_retract` | `location`(None) / `x` `y` `location`(全 None) | 诊断蟹笼 / 回收笼·清搁浅 |

> ⚠️ **鱼塘在 `farm` 域**，不在 `fish`。
> 🧬 **挂饵前先 `check(what="profile")`**：若是 Luremaster（职业 11）蟹笼免饵，挂饵是**空操作**，别浪费。

---

## 🛠️ 三、3 个独立工具

| 工具 | 干嘛的 |
|---|---|
| `intent` | 🎯 **意图单子**：`show` 看这一刻能做的事（一行一件，`←` 后面是理由）/ `do` 敲编号（`1` · `1,4` 多选 · `1=1,4=4` 各多少）/ `at x y` 指哪打哪（问"这一格能做什么"）/ `help` 玩法 |
| `screenshot` | 截图看画面（AI 的"眼睛"）|
| `help` | 查某域详细 ops/坑（docstring 精简后的细节兜底；不传 = 列话题）|

> 🎯 **单子怎么读**：一行一件事，**行尾 `…` 的是目录行**（点开还有下一层，**号是当场发的、不跨屏**）。
> 单子上**出现的每一条，按了就成**；没出现的不是"不行"，是这一刻算不出来。
> ⚠️ 名字**不能叫 `menu`**（那是"界面/菜单域"）；单子的工程细节看 `INTENT-MENU.md` 与 `CHANGELOG` 165~167。

---

## 🎯 四、常用场景速查（"我想… → 调…"）

| 我想干嘛 | 调用 |
|---|---|
| 这一刻能做什么 | `intent(ops="show")` → `intent(ops="do", kw={"code":"1"})` |
| 起床看今天/天气/运势 | `check(what="status")` |
| 环视四周有啥 | `check(what="look")` |
| 查我的技能/职业（是否 Luremaster）| `check(what="profile")` |
| 确认我是谁 / host 是谁（睡别人床前）| `check(what="role")` |
| 锄地种一片 | `farm(ops="till plant", x=40, y=20, rows=3, length=5)` ⚠️ **x/y 必填** |
| 浇水 / 收菜 | `farm(ops="water")` / `farm(ops="harvest")` |
| 砍树 / 清地 | `farm(ops="chop")` / `farm(ops="clear", x=40, y=20, rows=2, length=4)` |
| 收机器 / 往机器放料 | `farm(ops="load", item="上古水果")`（`item` 留空=只收不放）|
| 摸动物 / 挤奶 | `farm(ops="animals milk")` |
| 穿/脱穿戴物（衣/裤/帽/鞋/戒指/饰品）| `daily(ops="wear", name="铁头靴")` / `daily(ops="wear", slot="boots")` |
| 去挖矿 / 看进度 | `mine(ops="go")` / `mine(ops="progress")` |
| 自动炸矿 | `mine(ops="bomb_mine")` |
| 去钓鱼 | `fish(ops="go", location="Beach")` |
| 找某 NPC / 跟他聊天 | `map(ops="npc", name="艾米丽")` → `social(ops="chat")` |
| 送礼提好感 | `social(ops="gift", npc_name=…, item_name=…)` |
| 递东西给 host（一次一大把）| `social(ops="hand", player_name="…", item_name=…)` |
| 点面前的东西 / 点指定格 | `scene(ops="interact")` / `scene(ops="at", tile_x=…, tile_y=…)` |
| 开商店买东西 / 点菜单选项 | `menu(ops="shop", place="皮埃尔商店")` / `menu(ops="click", option=1)` |
| 推进剧情 / 整段跳过 | `menu(ops="advance")` / `menu(ops="skip")` |
| 翻箱找东西 | `storage(ops="find", name="…")` → `storage(ops="take", …)` |
| 跨图去某地 | `map(ops="go", destination="Town")` |
| 睡觉 / 蹭床 / 过夜结算 | `daily(ops="sleep", who="…")` / `daily(ops="settle")` |
| 今天/明天节日、去参加 | `festival(ops="today")` / `festival(ops="next")` / `festival(ops="go")` |
| 查献祭还缺啥 | `menu(ops="bundle_kb", query="工艺室")` / `menu(ops="bundle")` 实地看 |
| 捏脸 / 起名 | `settings(ops="appearance", …)` / `menu(ops="customize")` |
| 关掉卡住的弹窗 | `menu(ops="cancel")` |
| 后台跑长脚本 | 长任务自动后台：续跑 `script(ops="continue")`，停 `script(ops="stop")` |
| 截图看自己 | `screenshot()` |

---

## 🔄 五、旧工具 → 域形式 对照（给维护者：这些旧名 AI 已经看不见了）

| 旧独立工具 | 现在这样调 |
|---|---|
| `check_status` / `check_backpack` / `look_around` / `silo_status` / `mastery_status` / `building_list` / `machine_report` | `check(what="status"/"backpack"/"look"/…)` |
| `walk_to` / `go_to` / `find_npc` | `map(ops="walk"/"go"/"npc")` |
| `interact_at` / `interact` / `use_tool` / `face` / `select_item` | `scene(ops="at"/"interact"/"use"/"face"/"select")` |
| `read_menu` / `menu_click` / `press_key` / `advance_story` / `cancel` / `shop_visit` / `sell_to_shop` / `process_geode` | `menu(ops="read"/"click"/"key"/"advance"/"cancel"/"shop"/"sell"/"geode")` |
| `which_role` / `profile` | `check(what="role")` / `check(what="profile")` |
| `go_sleep` / `confirm_settlement` / `eat_item` / `set_appearance` / `wear` / `lie_bed` / `cook` | `daily(ops="sleep"/"settle"/"eat"/"appearance"/"wear"/"lie_bed"/"cook")` |
| `scan_chests` / `chest_store` / `chest_take` | `storage(ops="view"/"store"/"take")` |
| `run_script` / `script_start` / `script_stop` / `async_config` | `script(ops="continue"/"stop"/"async")` |
| `session_status` / `session_set` / `session_export` | `settings(ops="session_status"/"session_set"/"session_export", kw={setting,value})` |
| `chat_npc` / `gift_npc` / `give_item` / `hand_item` / `send_chat` / `emote` / `check_friendship` | `social(ops="chat"/"gift"/"give"/"hand"/"send"/"emote"/"friendship")` |
| `moss_run` / `berry_run` / `spot_run` / `pickup_scene` / `rock_run` | `scene(ops="moss"/"berry"/"spot"/"pickup_scene"/"rock")` |
| `go_mining` / `bomb_*` | `mine(ops="go"/"bomb_mine"…)` |
| `go_fishing` / `bobber_style` | `fish(ops="go"/"bobber")` |
| `set_appearance` / `character_customize` / `color_pick` / `list_hair_ref` 等 | `settings(ops="appearance"/"customize"/"color"/"hair"…)` |
| `bundle_status` / `bundle_kb` / `museum_donate` / `read_book` | `menu(ops="bundle"/"bundle_kb"/"donate"/"read_book")` |
| `list_quests` / `quest_progress` / `accept_quest` | 任务走 `menu(ops="journal")` → `menu(ops="read")`；详情 `menu(ops="know")`；接单在板上 `menu(ops="click", button=…)` |

> ⚠️ 手机/别的前端若**直调这些旧名**会报"工具不存在"⇒ 改走域形式。
> 服务端**没有** `--full` / `NAGI_FULL_TOOLS` 全量回退（已退役），传了也不生效。
