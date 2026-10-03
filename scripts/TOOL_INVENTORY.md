# NagiBridge MCP 域工具速查手册（2026-08-22 重写 · keep-set **16** 个 · 09-02 合并 script 域 · 09-06 `--full` 退役 · 09-11 收编 advance_story/profile/which_role · **10-01 收编 session→settings、cabin→scene/farm/daily/check** · **10-03 `bomb_escort` 真删 / `farm collect`+`building` 删除（收放合一走 `load`）/ 协同会开箱且**有炸弹自动回炸矿** / 开箱记账（换层清空）** · **10-03 深夜 **重构**：按"**两套机制**"分开写 + 单列「候选退役」**）

> ## 🧭 怎么读这份（2026-10-03 重构 · 恒：「**分开一套机制来写**」）
> 项目里现在有**两套交互机制**，混着看就是一团 —— 这份手册因此按机制切：
>
> | 篇 | 是什么 | 谁该看 |
> |---|---|---|
> | **第一篇 · 新通用交互机制** | **`intent` 意图单子（主入口）+ `scene` 动作原语**。这套是后来引入的，目的是**替代**一批零散老 op —— **新活一律走这套** | 所有人；AI 日常首选 |
> | **第二篇 · 🗑️ 候选退役清单** | 功能**已经被单子覆盖**的域 op（"上了单子的"）。逐条写「域 op ↔ 单子哪一项 ↔ 覆盖程度 ↔ **差在哪**」 | 「以后能退役哪些、干不干净、会不会漏」就看这篇 |
> | **第三篇 · 域 op 明细**（原「二、13 个域入口」起） | 还没被替代的域 op 完整参数速查（老路，仍在用） | 要查某个域某个 op 的参数 |
>
> 三条**总闸门**（决定"什么能上单子"，`intent_menu._candidates`）：① 没有 `exec` 也没有 `subs` 的动词**不上单子**（所见即所得）
> ② 菜单开着时只留 `menu_ok=True` 的 ③ 只有 `can() is True` 才上榜（算不出＝不上）
> ⇒ **「单子上没有」≠「做不到」**，也可能只是"这一刻不适用"。


> 域工具速查手册：AI 现在**默认只看到 16 个工具**（13 域入口 + 3 独立工具 `intent`/`screenshot`/`help`），
> 其余旧独立工具**全部收进域入口**（函数还在，只是 AI 不再直调）。
> 记住一句话：**"想做 X → 调对应域的 ops"**。旧工具名大多能在域里找到等价物（见文末对照表）。
> 🔍 **想查某域详细 ops/坑/参数 → `help(域名)`**（如 help(farm)；docstring 已精简，深度靠这个查）。
> 📐 **2026-09-11 起：每个域都补了「参数速查」**（每条 op 的 `kw` 键名 + 默认值 + 取值），
> 本手册与 `help(域)` **两处同源**——改一处记得改另一处。⚠️ 参数键名**必须 = 函数签名参数名**，
> 写错会被 `_ops_run` **静默丢掉、不报错**（这是本项目最容易踩且最难发现的坑）。

---

## 🧭 一、总纲（先读这段）

- **域模式恒开**（`--full` / `NAGI_FULL_TOOLS` 已退役 2026-09-06，没有"全量回退"了；AI 就只见下面这套 16 个）。
- **每个"域"就是一把瑞士军刀**：`farm(ops="till plant water")` 一次做多件事，ops 空格/逗号分隔。
- **AI 调用 = 域名 + ops**，不是工具名。例：想点一个格子 → `scene(ops="at", tile_x=8, tile_y=24)`；想推进剧情 → `menu(ops="advance")`。
- **✦ 域工具的子参数会收进 `kw`**：FastMCP 对带 `**kw` 的域工具生成 `{ops, kw}` 结构。Claude Code 自动处理（实际是 `farm(ops="till", kw={…})`），你**只需理解、不用手动拼**；但用脚本直调时子参数要放进 `kw`（如 `fish(ops="go", kw={"location":"Beach"})`），否则报 `kw Field required`。
- **原始端点（/state /interact /click /position /menu）不是 AI 能直调的 MCP 工具**，只是坐标/动作提示；AI 一律用下面的域工具。

---

# 🆕 第一篇 · 新通用交互机制（`intent` 单子 + `scene` 原语）

> 这一篇讲的是**我们引入的那套通用交互**：`intent` 是**主入口**（"看单子 → 敲编号"），
> `scene` 是它下面的**动作原语**（单子每一行最终都落到这些原语 + 少数脚本上）。
> **新活一律走这套**；第三篇那些域 op 里，凡是**已经被这套覆盖**的，都收进第二篇当退役候选。

## 1.1 `intent` —— 意图单子（**首选入口**）
- `intent(ops="show", n=20)` 看这一刻能做的事（一行一件；`←` 后面是**理由**）
- `intent(ops="do", rows="1")` 敲编号（`1,4` 多选；`1=1,4=4` 各做多少）
- `intent(ops="at", x=…, y=…)` **指哪打哪**：把该格**所有** `target="tile"` 的动词列出来（**绕过权重排序、无损**）
- `intent(ops="help")` 玩法细则
- 三条总闸门 + 「`0 做点别的…（at x,y 指哪打哪）」这条逃生口，见本文件顶部说明。
- 代码锚点：单子本体 `scripts/intent_menu.py`（`VERBS` **3035–3182** / `PENDING` **3192–3234** /
  `_candidates()` **3300** / `_render_level()` **3539** / `render_at()` **4012** / `do_row()` **3896**）；
  执行桥 `scripts/nagi_mcp_server.py`（`intent()` **24026** / `_im_run()` **23649** / `_im_ctx()` **23071**）。

## 1.2 `scene` 原语（单子下面的**最小动作单位**——留在这套机制里，不退役）
| op | 干嘛的 | 为什么算"原语" |
|---|---|---|
| `at(tile_x,tile_y)` | 点指定格 | `at x,y` 逃生口 & 几乎每个 `_exec_*` 内部都打它 |
| `front` / `interact` | 点面前的东西 | 同上（多数脚本"走到 → 交互"里的那一发） |
| `use(name)` | 挥工具 | 工具类动作的底座 |
| `face(dir)` / `select(name)` | 转身 / 选中物品拿手上 | 上面几个的前置动作 |
| `seats` / `furniture` / `decor` / `rock` / `maze`·`maze_seg`·`maze_walk` / `forge_help` | 扫可坐物 / 扫家具 / 地板墙纸真值表 / 室外镐击 / 迷宫视图·走法 / 锻造攻略 | **视图·判据·攻略**类，不是"被替代的对象" |
| `place` / `break` / `drop` | 放（地板/墙纸）/ 拆敲 / 丢背包物 | 动作，但**单子没有对应行**（放地板墙纸是恒拍板后置）⇒ 留 |

## 1.3 单子项 → 底层（照代码抄，方便将来改一处时知道牵动谁）
| 单子文案 | 底层落到哪 |
|---|---|
| `捡 地上的东西` | `pickup_scene` 脚本（=`scene ops=pickup_scene`）|
| `收 成熟作物` | `harvest_crops()`（=`farm ops=harvest`）|
| `收 已好的机器` | `load_machines(item="", here=True)`（=`farm ops=load`，**只收不放**）|
| `摸 猫狗` / `摸 还没摸的动物` | `_pet_pets_natural()`（=`farm pet`）/ `pet_walk` 脚本（=`farm petwalk`）|
| `放牧（开棚门）` / `关棚门` | `_doors_flip_all` → `/toggle_doors`（=`farm ops=doors`；**单子带方向、会收敛**）|
| `确认结算` / `领取` | `confirm_settlement()`（=`daily settle`）/ `/menu/click{button:mainButton}` |
| `推进对话` / `跳过整段` / `选 「…」` | `advance_story()` / `skip_event()`（=`menu advance`/`skip`）/ `/menu/click{option}` |
| `卖…` / `买…` / `砸晶球` / `投出货箱…` | `/sell_to_shop`（=`menu sell`）/ `/menu/click{item,quantity}` / `process_geodes`（=`menu geodes`）/ `sell_to_bin`（=`menu bin`）|
| `收 蟹笼` | `_crab_collect()`（=`fish ops=crab_collect`）|
| `摇 浆果丛`/`摘 茶叶`/`摇 金核桃`/`摇 果树` | `berry_run` 脚本（=`scene ops=berry`）⚠️ 见 2.4 的"金核桃"灰区 |
| `挖 远古斑点` / `刮 苔藓` / `淘 金` / `翻垃圾桶` | `spot_run` / `moss_run` / `_pan_run` / `trash_run`（=`scene ops=spot`/`moss`/`pan`/`garbage`）|
| `挤奶 / 剪毛` / `铺 干草` | `milk_shear()`（=`farm milk`）/ `feed_hay()` 脚本（=`farm hay`）|
| `躺一下` | `lie_rest` 脚本（⚠️ **与 `daily ops=lie_bed` 不是同一实现**，见 2.4）|
| `吃` / `看` / `坐` / `起身` / `关掉界面` | `/select`+`/eat`（=`daily eat`）/ 右键读书（=`menu read_book`）/ `sit()` / `stand()` / `cancel()` |
| `重铸饰品` / `买 Joja 可乐 (75g)` | `/select`+`/interact` —— **没有对应域 op**（单子独有）|
| `箱子…` / `箱子里…` / `存…` | `/chest_open`+`/chest_take`+`/store` / `/menu/click{action:claim,slot}` / `/store` |

---

# 🗑️ 第二篇 · 候选退役清单（"已经上了单子的" op）

> **判据 = 单子那一行与域 op 落到同一个函数**（不是"看起来像"）。
> 「✅ 完全等价」= 退役后**功能不减**；「🟡 部分」= 先看「差在哪」那一列（详细的都在 2.4）；
> 「🚫」= **单子把行撤了/故意不给**，域 op 是**唯一入口**，**千万别退役**。

## 2.1 ✅ 完全等价（12 条，退役后功能不减）
| 域 op | 单子里的项 |
|---|---|
| `scene ops=stand` | `起身` |
| `scene ops=berry` | `摇 浆果丛`＋`摘 茶叶`＋`摇 金核桃(bush)`＋`摇 果树(摘果子)` |
| `scene ops=spot` | `挖 远古斑点` |
| `fish ops=crab_collect` | `收 蟹笼` |
| `farm ops=milk` | `挤奶 / 剪毛` |
| `farm ops=pet` | `摸 猫狗` |
| `farm ops=petwalk` | `摸 还没摸的动物` |
| `farm ops=harvest` | `收 成熟作物`（`radius` 单子固定 25）|
| `daily ops=settle` | `确认结算` |
| `menu ops=advance` | `推进对话` |
| `menu ops=skip` | `跳过整段` |
| `menu ops=geode` / `geodes` | `砸晶球`（带数量层）|

## 2.2 🟡 部分覆盖（15 条，"差在哪"见 2.3）
| 域 op | 单子里的项 | 差在哪（一句）|
|---|---|---|
| `scene ops=sit` | `坐` | 单子不给 `face`（坐下朝向）|
| `scene ops=pickup_scene` | `捡 地上的东西` | 单子 `max_items` 固定 30；不能"只捡某一件" |
| `scene ops=moss` | `刮 苔藓` | 不给 `radius`/`target_max`/`rounds`/`dry_run` |
| `scene ops=pan` | `淘 金` | 不给 `dry_run`/`radius`/`timeout` |
| `scene ops=garbage` | `翻垃圾桶` | 不给 `loc`/`pos`/`wait`/`dry_run` |
| `farm ops=hay` | `铺 干草` | 不给 `dry_run` |
| `farm ops=animals` | `摸 还没摸的动物` | 单子只到"摸"；**收产物**那条不在单子上 |
| `farm ops=doors` | `放牧（开棚门）`＋`关棚门` | ⚠️ **单子更强**（带方向＋收敛到目标态；域 op 是无方向翻转）|
| `farm ops=load` | `收 已好的机器` | 🔴 **单子恒 `item=""`+`here=True`** ⇒ **"给机器上料 / 跨屋收放"单子做不到** |
| `daily ops=eat` | `吃` | 点名的 `name`/`item_name` 只在"低值全列"那一刻等价 |
| `daily ops=lie_bed` | `躺一下` | ⚠️ **两条不同实现**（单子走 `lie_rest` 且自动挑床主；域 op 走 `approach_bed` 且 `who` 必填）|
| `menu ops=cancel` | `关掉界面` | `cancel()` 还管"撤睡觉就绪"那条特殊分支 |
| `menu ops=click(option)` | `选 「…」` | 单子只覆盖 `option` 与 `button=mainButton`；`item`/`slot`/`x,y`/`right`/`real`/`action=discard` 都不在单子上 |
| `menu ops=sell` / `bin` / `read_book` | `卖…` / `投出货箱…` / `看` | 单子一次一摞（不能多选）/ 没有 `sell_all` / 只能读**手持**那本 |
| `storage ops=view` / `take` / `store` | `看（走过去开箱）` / `取` / `存` | ⚠️ **`view` 甚至不是一回事**（单子那行是**画面通道** `/chest_open`，`storage view` 是**数据清单** `/scan_chests`）；`take/store` 的 `items`/`target`/`count`/`all` 单子都给不了 |

## 2.3 🚫 反向缺口 —— **千万别退役**（域 op 在，单子**没有行**）
| 域 op | 为什么单子没有 |
|---|---|
| `daily ops=wear` | 单子那行 **2026-10-02 撤掉**（`intent_menu.py:3133–3136`，恒拍板：权重 38 太低会常驻）|
| `daily ops=sleep` | **故意不上**（`nagi_mcp_server.py:23696`：`who` 是"去哪儿"不是"点哪格"）|
| `scene ops=pickup`（搬家具） | 单子「搬走」**2026-10-02 撤掉**（`intent_menu.py:3092–3097`）|

## 2.4 🔍 遗漏检查（"部分覆盖"里**会真丢能力**的）
| 域 op | 单子做不到的 | 影响 / 建议 |
|---|---|---|
| 🔴 `farm ops=load` | `item`（放什么料）/`machine_type`/`location`（不传 `here` ⇒ 全图·跨屋） | **"给机器上料"整件事单子做不到**（放料＝规划，`intent_menu.py:3051–3058` 恒拍板不上单）⇒ **要么留着，要么在单子上补一条"上料"目录行**（`PENDING:3232` 的 P2 设计稿正是这么写的）|
| 🔴 `storage ops=store/take` | `what`/`items` 只存指定几样、`target` 指定哪只箱、`keepTools=False`、`all=True`；`count`、按坐标取 | 单子只能"眼前这只箱、挑列表里的整摞" ⇒ 批量/定点这两族留着 |
| 🔴 `storage ops=view` | 看**第 N 只箱**、按名字/色/坐标点名、**不开菜单**读清单 | 单子那行会**真的把箱子打开**（画面通道）|
| 🟡 `scene ops=walnut` | **整条可能没被覆盖**：单子「摇 金核桃」走的是 `berry_run`（bush 那类），`walnut_run()` 是**另一个脚本**（`radius`/`max_count`/`dry_run`） | **不确定** ⇒ 建议姜岛真机各跑一次比对后再决定能不能退役 |
| 🟡 其余"部分" | 都是**参数维度**丢失（`face`/`dry_run`/`radius`/`count`/多选/点名） | 影响小；要退役就在单子上补参数，或接受"够用" |

> **一句话结论**：单子已覆盖 **30 条**域 op（12 完全 + 15 部分 + 3 反向缺口不能退役）。
> **最值得先收敛的一撮**＝`scene` 那 8 条 + `farm` 那 6 条（与单子行**同一实现**，退役后功能不减）；
> **唯一会真的削减能力**的是 `farm load` 的"上料/跨屋"与 `storage store/take/view` 的批量·定点参数。

---

## ⚙️ 三、13 个域入口（ops 列表）　*（= 第三篇：还没被替代的域 op 明细）*


> 🗜️ **2026-10-01 撤下顶层两个域**（**函数没删**，只是 AI 不再直调；逐 op 的替代路见 `domain_selftest._SUBSUMED_DOMAINS`）：
> **`cabin`** → `cook`→`daily cook` · `sleep`→`daily sleep` · `statue`→`farm statue` ·
> `interact/place/break/furniture/decor`→`scene` 同名 op · `pickup`→`scene pickup`/单子「搬走…」 ·
> `collect`→单子「收 已好的机器」/`farm load`（**只收不放**） · `enum`→`check(what="machines")`（+ 单子）。
> **`session`** → 三条 op 全进 **`settings`**（`session_status`/`session_set`/`session_export`）——
> 这不是简化而是**消除重复**：`settings status` 早在印会话设置，
> 而 `settings(setting="context_turns")` 与 `session set max_turns` 改的是同一个变量。

### `check(what)` — 查询（状态/背包/机器/收藏）　*注意：这是 `what` 不是 `ops`*
| what | 干嘛的 |
|---|---|
| `status` | 完整状态（位置/时间/血/体力/钱/背包格数）|
| `backpack` | 背包逐格详细（价值/星级/属性）|
| `worn` | 穿戴物（衣服/戒指/饰品）|
| `machines` | 全农场机器清单 |
| `mine` | 下矿进度 |
| `silo` / `mastery` / `buildings` | 筒仓干草 / 精通 / 木匠建筑 |
| `quest` | **打开任务日志**（回执让你再 `menu read` 读卡；**不直接返回列表**）|
| `ready` / `就绪` | 就绪握手实况（卡在"等待其他玩家"框时查；两侧都读才看得出死锁在哪头）｜🆕09-11 原顶层就绪工具 |
| `chests` / `storage` | 当前图箱子 / 箱子网络视图 |
| `look` | 环视周围（NPC/怪物/物品/地形）|
| `profile` / `技能` | 我的技能等级 + 职业分支（如是否 Luremaster 蟹笼免饵）｜🆕09-11 原顶层 `profile()` |
| `role` / `角色` / `端口` | 确认端口↔角色（AI=谁 / host=恒）——睡觉/协作前先查｜🆕09-11 原顶层 `which_role()` |

**📐 `check` 参数速查**（⚠️ 这个域的参数叫 **`what`**，不是 `ops`）

| 带参的 what | 参数（括号内=默认） | 说明 |
|---|---|---|
| `chests` | `chest`(-1) | 看当前图的箱子；`chest=N` 看第 N 个的全清单 |
| `look` | `radius`(10) | 环视半径（NPC/怪物/物品/地形）|

> 其余 `what`（`status`/`backpack`/`worn`/`machines`/`mine`/`silo`/`mastery`/`buildings`/`quest`/`ready`/`storage`/`profile`/`role`）**全部无参**。
> 💡 查概览用 `status`、查逐格用 `backpack`，别都调一遍浪费 token。

### `farm(ops)` — 农活（锄/种/水/收/机器）*AI 必走* **只能在 Farm/温室/姜岛**
| ops | 干嘛的 |
|---|---|
| `till` / `plant` | 锄地 / 播种（**各只有一个实现**；想一次锄+种就 `ops="till plant"`，一份 kw 共用）|
| `water` | 浇水（自动跳过下雨、水壶没水先装满）|
| `harvest` / `scythe` | 收成熟作物 / 镰刀收（蒜/花/茶）|
| `fertilize` / `clear` / `plot` | 撒化肥 / 清杂草石头树桩 / 地皮规划 |
| `tillfield` / `hoe` / `plan` | 全是 `till` 的别名（蓄力锄/布局锄）/ **方形规划(算格)**——`plantlayout`/`播种规划` 也是 `plant` 的别名 |
| `chop` / `clearground` | 砍树 / 清单格 |
| `load` | 收机器 / 往机器放料**一条过**（`item` 留空 = **只收不放**）｜⚠️ 老 op `collect`/`building` **2026-10-03 已删**（连函数/端点一起）|
| `pond` / `pond_add` / `pond_feed` / `pond_collect` / `pond_fish` | 鱼塘：状态/放鱼/喂/领鱼籽/直钓 |
| `animals` / `milk` / `pet` / `petwalk` | 摸动物+收 / 挤奶剪毛 / 摸猫狗 / 拟人摸（care 域 09-02 并入 farm）|
| `喂水`/`碗` / `畜舍`/`这间` / `buy` / `doors` / `hay` / `statue` | 宠物水 / 这间屋动物 / 买动物 / 关门 / 加干草 / 祈福 |

**📐 `farm` 参数速查**（`kw` 的键名**必须 = 函数签名参数名**，写错会被 `_ops_run` **静默丢掉**、不报错）

| op | 参数（括号内=默认值） | 说明 |
|---|---|---|
| `till` / `clear` | **`x`,`y`（必填，不传直接报错）** `rows`(1) `length`(1) `direction`("horizontal")；till 另有 `x1,y1,x2,y2` / `layout`(0)；clear 另有 `x1,y1,x2,y2` / `radius`(0=圆形) / `margin`(2) | 锄地 / 清杂草石树桩（clear **默认往外多清 2 格**，防田边杂草长进田里）|
| `plant` | `seed_name`(必填) `x`,`y`(必填) `rows` `length` `direction` **或** `x1,y1,x2,y2`；`layout`(0) `direct`(False) `trellis`(False) | 播种（跳过已种/设施格；带 `layout` 就按洒水器布局种）|
| `fertilize` | `fertilizer_name`(必填) `x`,`y` `rows` `length` `direction` | 撒化肥 |
| `water` / `harvest`(15) / `scythe`(15) | `radius` | 浇水无参 / 收 / 镰刀收 |
| `plot` | `x`(-1) `y`(-1) `radius`(15) `all_plots`(False) | 连通域规划（不传 x/y=以自己为心）|
| `chop` | `area`("") —— 值写「几个数」：**4 个数=矩形两角 / 3 个数=圆心+半径** | 砍树（限定区域时会先走过去再找）|
| `load` | `item`(必填) `machine_type`("") `location`("") `count`(0) `here`(False) | 收 / 放一条过；`here=True` = 只弄脚下这一间屋（原 `building` 的活法）；`item` 留空 = 只收不放 |
| `place` / `break` | `name`,`x`,`y` ／ `x`,`y`(必填) `steps`(1) `radius`(0) | 同 `scene` 同名 op |
| `pond_add` / `pond_feed` / `pond_collect` / `pond_fish` | `x`(-1) `y`(-1)；`pond_add` 另有 `item`(必填) | 鱼塘四件事（不传坐标=唯一/最近那口）。⚠️ **简写 `feed`/`collect`/`fish` 不是 op**，必须写全名 |
| `buy` | `animal_type` `name` `building`("") | 买动物 |
| `petwalk` | `include_petted`(False) | 拟人遛（默认跳过已摸过的）|
| `hay` | `dry_run`(False) | 加干草（True=只看不加）|

> `direction` 只认两个值：`"horizontal"`(默认) / `"vertical"`（**其它字符串一律当 vertical**，别写"横"/"竖"）。

**💧 大田洒水器布局（可选，纯自动化建议——可用可不用）**

三件套 `plan` → `till` → `plant`，`layout` 四档含义相同（`plant` 不传=0 整块）：

| `layout` | 布局 | 洒水器 | 覆盖 | 留格规则 |
|---|---|---|---|---|
| `0` | 标准整块 | 不预留 | — | 整块全种；**锄法**蛇形逐格走位（任何锄头等级都行）|
| `1` | 初级（Sprinkler）| 稀疏十字 | 4 格 | 对角网格（行隔2列隔3每行斜移1）；锄法=**按洒水器逐台锄它上下左右 4 格**（`layout=1` 不用蓄力站位 ⇒ **与锄头等级无关**：**一律拟人逐格走位**，升级锄也不切蓄力/一键）|
| `2` | 高级（优质，3 的倍数）| 每 3×3 中心 | 8 格 | 田宽高**先裁成 3 的倍数**；整块蓄力锄（吃满当前锄头等级）|
| `3` | 铱（5 的倍数）| 每 5×5 中心 | 24 格 | 同上裁成 5 的倍数；**爬架作物不适用（`plant_tiles` 返回空）** |

- `plan(x1,y1,x2,y2, layout=0, hoe_level=-1, trellis=False)` —— **纯计算只报格**（不动机器），先拿它看要锄/种哪些格。
  `hoe_level` 0→1格 / 1→3线 / 2→5线 / 3→3×3 / 4→6×3，`-1`=自动读当前手持锄头。
- `till(x1,y1,x2,y2, layout=0)` —— 按布局锄地（锄法随 layout 自动切，见上表"锄法"列；`till` 还有同义别名 `hoe`/`布局锄`/`tillfield`/`蓄力锄`）。
- `plant(x1,y1,x2,y2, seed_name, layout=0, direct=False, trellis=False)` —— 按布局播种（`layout` 不传=0 整块，就是老 `plant` 的语义；老名字 `plantlayout`/`播种规划` 仍可用）。
  `direct=True` 用瞬移（格多时快）/ 默认走位拟人。
- `trellis=True` = 爬架作物（啤酒花/青豆/葡萄，**不可通过格**）⇒ 自动**种2留1**留出 AI 能走进去浇/收的走道；`layout=0/2` 会过滤走道格并重排顺序，`layout=1` 十字天然有走道不用调。
- **只管种**（不摆洒水器）就直接 `plant`（不传 layout 就是整块），不用这三件套。

> ⚠️ **已知限制（`layout=0/2/3` + 金/铱锄）**：3×3 / 6×3 的蓄力落点**没实测校准过**，所以 `hoe_level>=3` 时 `plan` 会报 **0 处锄地站位**、并自己打印一行"金/铱锄 3×3/6×3 落点未实测校准，暂不规划蓄力站位"——**这不是出错，是刻意不猜**。`layout=1` 不吃蓄力站位，**不受影响**。


### `mine(ops)` — 下矿（冲层/刷矿/炸矿）**只在 矿井/头骨/火山**
`go`(去挖矿) `progress`(进度) `bomb_status` `bomb_plan` `bomb_place` `bomb_collect` `bomb_ladder` `bomb_retreat`(单步炸矿) `bomb_mine`(自动) `bomb_volcano`(火山) `organize`(整理背包)

> 🚫 协同（跟随 host 敲矿打怪）**不是独立 op**：`bomb_mine` 发现**没炸弹且 host 同矿井**时自动转内部协同；`bomb_retreat` 结束协同+脱矿回门口；**协同期间又拿到炸弹会自动回炸矿模式**（2026-10-03）。🗑️ `bomb_escort` 那个独立脚本 + 它的 MCP 工具 **2026-10-03 恒拍板已真删**（协同本来就是 `bomb_mine` 内联的，那个脚本全仓没有启动点）。

**📐 `mine` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `go` | `mode`("rush") `start`(1) `target`(None) `ore`(None) `cycles`(5) `hp_threshold`(**30**) `food_sta` `food_hp` `food_buff`(2026-10-03 新增) `resume`(True) | `mode=rush` 冲层（往更深敲到 `target`）/ `mode=farm` 刷矿（在电梯直达层反复刷指定 `ore`）。⚠️ `hp_threshold` **只是"血量低于它就不肯下矿"的出门线**，**不是吃食物的线**（吃的线是 `bomb_common.EAT_HP_PCT=60`）——**别拿它当吃的线调**。`food_buff` 点名要补的 buff（**不传也会自动补**：自动挑满包里带 buff 的那份）|
| ↳ `ore` 取值 | `Copper`(铜,21层) / `Iron`(铁,41层) / `Gold`(金,71层) | ⚠️ **煤没有 ore 选项**——`farm` 铁层(41) 会顺手清尘埃精灵/蝙蝠，它们掉煤 |
| `bomb_plan` | `radius`(14) `min_covered`(3) `top`(3) | 找"炸一下覆盖 ≥`min_covered` 块岩体"的锚点，给前 `top` 个 |
| `bomb_place` | `x`,`y`（**必填**） | 在指定格放炸弹 |
| `bomb_collect` | `max_items`(12) | 炸完收掉落 |
| `bomb_mine` | `target`(0) `bomb`("Bomb") `min_covered`(3) `follow_host`(True) `lead`(2) `autodrop`(0) `one_floor`(False) `food_hp` `food_sta` `food_buff` | 全自动炸矿。`target=0` = **按当前层自适应**（在头骨矿洞另有语义）；`one_floor=True` = 逐层模式（同步、只跑一层返回摘要，不撤退）——**默认冲层模式是异步后台跑，推荐**。吃食三件：`food_hp`/`food_sta` 点名要吃的，`food_buff` 点名补哪个 buff（**不传也会自动补**）|
| ↳ `follow_host`/`lead` | True / 2 | host 在矿里就一起冲层，目标层 = host 层数 ± `lead` |
| `bomb_volcano` | `bomb`("Bomb") `min_covered`(3) `hp_threshold`(30) `max_minutes`(None) `poll`(2.5) `food_hp` `food_sta` | 火山专用。⚠️ **要求 host 已在矿/火山里**才放行（火山特殊瓦片无法程序化换层）。⚠️ 火山**没有** `food_buff`（buff 那条线按恒 2026-10-03 拍板维持现状）|
| `organize` | `disable`(False) `reset`(False) | 整理背包；`reset=True` 恢复默认 |

**💣 `bomb` 三个取值**：`"Cherry Bomb"` 樱桃 / `"Bomb"` 黑 / `"Mega Bomb"` 超级。

- 点名的那种**背包里没有**（或不传）→ 自动按 **黑 > 超级 > 樱桃** 挑背包里**实际有的**（不会因为"只认黑"就误报没炸弹）。
- 爆炸范围：樱桃 = 边长 7 的**十字**（缺 4 角）/ 黑 = **11×11 方块** / 超级 = **15×15 方块**。
  ⚠️ 黑和超级**会炸伤自己**：实测黑弹掉 3 血（可接受），超级更大更痛。

### `social(ops)` — 社交
`chat`(跟NPC搭话) `gift`(送礼物) `give`(给物品玩家·手持右键正式赠予) `hand`(递给玩家·走过去丢他脚边·磁吸自动收·可整叠) `send`(发聊天消息) `emote`(表情) `friendship`(查好感) `movie`/`snack`(电影院知识)

**📐 `social` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `chat` | `name`("") | 跟 NPC 搭话（不传=面前那个）|
| `gift` | `npc_name` `item_name`（**都必填**） | 送礼给 NPC |
| `give` | `player_name` `item_name`（**都必填**） | 给**玩家**物品（手持右键正式赠予，**一次一个、要等对方同意**）|
| `hand` | `player_name` `item_name`（必填）`count`(0=整叠) | 递给玩家：**走过去丢在他脚边**、磁吸自动收，**可整叠** |
| `send` | `message`（必填） | 发聊天消息 |
| `emote` | `name`("爱心") | 表情（对方能看到）|
| `friendship` | `name`（必填） | 查好感 |
| `movie` | `npc`("") | 电影院知识 |
| `snack` | 无参 | 零食知识 |

> ⚠️ **`give` vs `hand`**：`give` 是**面对面正式赠予**（要等同意、一次一个）；`hand` 是**走过去丢他脚边**（磁吸自动收、可整叠）——想整叠给/对方不在手边时用 `hand`。

### `scene(ops)` — 场景交互（点东西/工具/转身/捡）
| ops | 干嘛的 |
|---|---|
| `at(x,y)` | 点指定格（柜台/电视/机器，对角也行）|
| `interact` | 点面前的东西（=确认键）|
| `use`(工具名) | 挥工具 |
| `face`(方向) | 转身（0上1右2下3左）|
| `select`(物品名) | 选中背包物品拿手上 |
| `sit(x,y[,face])` / `stand` / `seats`(radius) | 坐椅子（自动就位；`face=`=坐下朝向，只对部分座位生效）/ **起身**（坐着时用）/ 扫可坐物（✋=可改朝向）|
| `pickup` / `pickup_scene` | 拿起家具 / 捡当前场景可拾取物 |
| `berry` / `spot` / `moss` / `walnut` | 摇/摘 灌木与果树（浆果·茶叶·果子；⚠️果树摇完果子**掉地上**要再走上去捡） / 挖斑点蚯蚓 / 绿雨搜刮苔藓 / **敲金核桃**（`walnut`：`radius`(0=默认范围) `max_count`(1) `dry_run`(False)）|
| `garbage` | 翻垃圾桶（`loc`("") `pos`("") `wait`(1.0) `dry_run`(False)；`dry_run=True` 只报位置不翻）|
| `rock` | 室外镐击（采石场/挖掘场/蚌矿场敲可破物：骨/黏土/蚌/矿点/宝石/煤/放射矿，只跳普通石；dig=false 只扫） |
| `forge_help` | 锻造台附魔攻略（台子：本图 Mini-Forge 优先，没有才去火山 Caldera） |
| `drop` / `furniture` / `decor` | 丢背包物品 / 扫家具 / **🪵 地板墙纸真值表**（这间屋哪些格能铺 + 现在铺的什么；地板点**地板格**、墙纸点**靠墙那圈墙格**，点错游戏静默不理）|
| `pan`(:dry_run,radius=3,timeout=20) / `maze` / `maze_seg` / `maze_walk` | 淘金 / 迷宫视图 / 走法链 / 走迷宫（⚠️通用多段走位,主门牌= `map walk_multi`/`闲逛`）|

**📐 `scene` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `at` | `tile_x`,`tile_y`（**必填**） | ⚠️ 是 `tile_x`/`tile_y` **不是** `x`/`y`（写错会被静默丢掉）|
| `use` | `name`(None) | 不传=用当前手持工具 |
| `face` | `direction`（**必填**）0上/1右/2下/3左 | |
| `select` | `name`（必填） | 选中背包物品拿手上 |
| `sit` | `x`,`y`（必填）`face`(None) | 见上表 |
| `seats` | `radius`(12) | |
| `pickup` | `tile_x`,`tile_y`（必填） | 拿起家具（⚠️ 同 `at`，是 `tile_x`）|
| `pickup_scene` | `max_items`(30) | 捡当前场景可拾取物 |
| `moss` | `radius`(25) `target_max`(80) `rounds`(5) `dry_run`(False) | 绿雨搜苔藓；`dry_run=True` 只探不采 |
| `rock` | `dig`(True) `radius`(14) `max_break`(0，不限) `break_stone`(False) | 室外镐击；`dig=False` 只扫不敲 |
| `garbage` | `loc`("") `pos`("") `wait`(1.0) `dry_run`(False) | 翻垃圾桶；`dry_run=True` 只报位置 |
| `pan` | `dry_run`(False) `radius`(3) `timeout`(20) | 淘金 |
| `drop` | `name` `count`(1) `items` | 丢背包物品（直接消失不落地）：一种用 name+count；**多种一次丢**用 items=逗号分隔（每项可跟 :数量，如 `items=木头,石头:3`）。⚠️ 名字口径=check backpack 里显示的；菜单开着时别丢（满包菜单会持背包快照，关时写回） |
| `place` | `name`,`x`,`y` | 🪵 放**地板/墙纸**时只能点**地板格/墙格**（点错游戏静默不理）→ 点错会直接告诉你「这格其实是墙不是地板」并给出能铺的格；拿不准先 `decor` |
| `decor` | 无参 | 🪵 当前图的地板格/墙格清单 + 每间房现在铺的什么（**认房间不认格**）|
| `break` | `x`,`y`（必填）`steps`(1) `radius`(0) | `radius>0`=方圆若干格 |
| `maze` / `maze_seg` | `radius`(14/15) `gx` `gy` | 迷宫视图 / 走法链 |
| `maze_walk` | `waypoints`（"x,y x,y …"）`location`(None) `max_wait`(18) `max_seg`(200) | ⚠️ **它其实是通用多段走位**，主门牌 2026-09-12 已挪到 **`map walk_multi`/`闲逛`**；此处保留旧名为兼容 |

> ⚠️ **`at` 和 `pickup` 的参数名是 `tile_x`/`tile_y`**，其余走位类多是 `x`/`y` —— 这是本项目最容易写错、且**错了不报错只是没反应**的地方。

### `menu(ops)` — 菜单/界面（开→看→点）
| ops | 干嘛的 |
|---|---|
| `read` | 看当前菜单（商店/对话/选项/信件）|
| `click`(option/item/button/x/y) | 点菜单项（自适应）|
| `key`(ok/esc/数字) | 按键盘 |
| `advance` | **推进剧情/对话**（自动走剧情）|
| `skip` | **整段跳过剧情/事件**（`skippable=true` 的事件才跳得动；无参。想一步一步走用 `advance`）|
| `cancel` | 关当前弹窗/撤睡觉就绪 |
| `shop` / `sell` / `bin` | 逛店 / 卖商店 / 投出货箱 |
| `craft` / `recipes` / `craftables` | 合成 / 菜谱 / 配方 |
| `forge` | 锻造台 |
| `geode` / `geodes` | 砸晶球（×1 / 批量）|
| `customize` | 捏人弹窗（起名/喜好）|
| `bundle` | 社区中心献祭板（实地读板看缺口）|
| `bundle_kb`(query=…) | 献祭知识库（不用跑社区中心，查"原来要这些"）|
| `donate` | 捐赠博物馆（走到柜台一键捐可捐矿物/古物）|
| `read_book`(name=…) | 读书（消耗技能书领配方）|
| `journal` | 开任务日志→`menu read` 读 QuestLog 卡（含每子目标 current/max）|
| `know`(名) | 查特别订单详情（知识库；`menu know 岛屿食材`。原 quest 域 09-02 并入 menu）|
| `number` | 数量输入框（展览会兑换台/转盘押注 `NumberSelectionMenu`）|
| `display_fill` / `display_takeback` | 农展台放满 / 收好 |
| `minigame` / `minigame_state` | 赌场小游戏点按钮 / 读牌面·转盘 |
| `levelup_choose` | 技能升级选职业分支（5/10 级）|

> ⚠️ **`cook`（做饭）不在 `menu` 域，在 `daily` 域**（2026-10-01 从已撤顶层的 `cabin` 收编进来）。旧版 `claim_swap`（替换领取）也已退役。

**📐 `menu` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `click` | `option`(-1) `button`("") `x`(-1) `y`(-1) `item`("") `right`(False) `quantity`(1) `action`("") `real`(False) `slot`(-1) `category`(-1) | 万能点击器。`action` 取值见下；`button` 用**按钮名**（如 `ok`/`upperRightCloseButton`/`forward`/`back`/`rewardBox`/`mainButton`）；`slot`=领指定格；`real=True`=真鼠标 |
| ↳ `action` 取值 | `"claim"` 领取 / `"discard"` 丢桶腾格（回收返金） | 🚫 **满包接鱼/领箱**：旧的 `claim_swap`（替换领取）**已退役** ⇒ 用 `action=discard` 先丢桶腾格，再 `action=claim` 领取（或不想要直接 `button=ok` 关掉）|
| `key` | `key`（必填）`count`(1) `hold`(0) | `key` 可填 `ok`/`esc`/数字 |
| `number` | `value`(-1) `confirm`(False) | 数量输入框；`value=-1` 只读不动 |
| `shop` | `place`（必填）`want`("") | 逛店；`want`=想买的东西（会帮你找）|
| `sell` | `name`（必填）`count`(-1) | 卖商店；`count=-1`=全卖 |
| `bin` | `name`("") `sell_all`(False) | 投出货箱 |
| `craft` | `item_name`（必填）`count`(1) | 合成 |
| `forge` | `item1`（必填）`item2`("") `mode`("combine") `target`(0) | 锻造台附魔/合成 |
| `geodes` | `count`(1) | 批量砸晶球 |
| `customize` | `name` `farmname` `favorite`（全 None） | 捏人弹窗（起名/农场名/喜好）|
| `bundle` | `area`("") | 献祭板；不传=当前 |
| `bundle_kb` | `query`("") | 献祭知识库查询 |
| `read_book` | `name`（必填） | 读书/读纸条 |
| `levelup_choose` | `side`("") `profession`(-1) | **不带参 = 只读**当前给的左右选项（供配 `check(what="profile")` 分析）；定分支传 `side=left/right` 或 `profession=职业id` |
| `minigame` | `action`("") `x`(-1) `y`(-1) | 如 `action=hit/stand/bet10` |
| `display_fill` | `items`("") | 如 `items="钻石,山羊奶酪"` |

> 🧾 **关闭菜单一律 `click(button="upperRightCloseButton")`** —— 但 `ItemGrabMenu`/交付容器要用 `button=ok` 确认才关。

### `script(ops)` — 脚本/异步（09-02 五合一：run_script/start/status/stop/async_config；09-05 删 status——收工自动播报）
`continue`(确认脚本继续阻塞/AI 做完事回待命) `stop`(停) `async`(自动异步白名单)

**📐 `script` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `continue` / `stop` | `job_id`("") | 不传 = 当前那个任务 |
| `async` | `show`(False) `add` `remove` `enable` | 自动异步白名单管理 |

> ⚠️ 长任务便利工具**自动后台**（别手动后台）；进度**自动播报**（收工带总时长），**不需要轮询 `status`**（`status` 已于 09-05 删除）。
> ⚠️ 参数放 `kw`，别拼进 ops 串。

> 🗜️ **2026-10-01：`session` 域已撤下顶层**，三条 op 全进 **`settings`** ——
> `settings(ops="session_status"/"session_set"/"session_export")`。
> 理由不是"简化"，是**本来就在做同一件事**：`settings status` 早在印会话设置，
> 而 `settings(setting="context_turns")` 跟 `session set max_turns` 改的是同一个变量。

### `storage(ops)` — 箱子
`view`(看箱) `store`(存) `take`(取) `find`(模糊查哪箱有) `default`(设/清默认箱) `tag`(改名+改色)

> 🗑️ 旧版这里写的 `scan` / `smart` / `layout` / `cleardefault` **都不存在**（历史残留，已删）——对应现在是 `view` / `store` / `check(what="storage")` / `default(clear=True)`。

**📐 `storage` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `view` | `box`(-1) | 看箱子；`box=N` 看第 N 个箱子的全清单；`box` 也吃箱子名/色名/`#hex`/`"x,y"`（同 `store` 的 `target`，如 `"内置冰箱"`）|
| `store` | `what`("") `items`("") `target`("") `keepTools`(True) `all`(False) | `what`/`items` = 限定只存哪些（名字可带 `xN` 只存那 N 份）；**留空 = 归位**（只把已有同类堆叠回去）；`target` = 指定箱（中文色名/`#hex`/箱子名/标记名/`"x,y"`）；`all=True` 全存腾空间 |
| `take` | `items`("") `x`(-1) `y`(-1) `name`("") `count`(999) | 单箱取 = `x`,`y` + `name`；批量取 = `items`（`count` **两条路都认**；项内自带 `×N` 优先）。`count=999`(默认) = 不限 |
| `find` | `name`("") | 模糊查哪个箱里有某物 |
| `default` | `x`(-1) `y`(-1) `clear`(False) | 设默认箱；`clear=True` 清掉 |
| `tag` | `tag`("") **`target`（必填）** `color`("") | 名字变「本名(标记)」；`tag` 留空 = 只清标记留本名；`color` = `#RRGGBB` 或色名 |

> 🤖 存取都**自动走位**（`store`/`take` 先走到相关箱旁，批量只走到第一个）——别靠编号逐箱翻，用 `find` 定位。
> ⭐ 每个箱子前自动带**【类目标签】**（内容过半归类：矿/古物/鱼/种子/作物/农产/建材/料理/装备）。
> ⚠️ 改色**别用纯 `#000000`**（=默认木纹，会被识别成"未染色"）；要黑箱用暗灰 `#303030`。

### `daily(ops)` — 过日子
`sleep`(睡觉) `settle`(确认过夜结算) `eat`(吃食物) `cook`(做饭) `wear`(穿/脱衣物) `lie_bed`(躺床不过夜) `heartbeat`(心跳间隔) `pause`(后台不暂停) `peek`(看host在干嘛) `whiteboard`/`wb_read`/`wb_pin`/`wb_clear`(白板笔记) `appearance`(捏脸)

**📐 `daily` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `sleep` / `lie_bed` | `who`（**必填**） | `sleep` = 真过夜；`lie_bed` = **只躺不睡**（想离开随时 `map walk` 走离床格，`isInBed` 自动变 false）。`who`：传自己名 = 自己床；传别人名 = 那人的床（一起睡 + 🌹彩蛋）。**传对名字就不用先回家**——不在那栋屋会自动走过去（map_go 跨图→门口→推门→床边）。名字写错 → 报错并列出可选名（不会默默睡成别人的床）。🏝️ **在姜岛是另一套**（`IslandFarmHouse` 大通铺，岛上**没有"谁的床"**）：传**正躺在床上的别人** = 挤他那张（姜岛版爬床彩蛋）；传自己 / 那人还没躺 = 挑一张**空床安静睡**（不播报）；全占满 → 报错点名谁在哪张床；导航走 `map_go("IslandFarmHouse")`，**不是**回大陆那个家 |
| `eat` | `name`("") `item_name`("") | 吃食物回血/体力 |
| `cook` | `recipe_name`（**必填**；食谱用英文原名）`count`(1) | 做饭（🆕10-01 从 `cabin` 收编进来）；**会先走到厨房**，走不过去直接报错；满包会被前置拦下不吃材料 |
| `wear` | `name`(None) `slot`(None) `hand`(None) | `name` = 穿上（**自动判槽位**，替下的回背包）；`slot` = 脱下该槽（`boots`/`leftRing`/`rightRing`/`trinket`/`hat`）；`hand` = **仅戒指**，`1`/`left` 或 `2`/`right`，或传"要换掉的那枚戒指名"（自动找它在哪只手）|
| `heartbeat` | `minutes`(5) | 0 = 每次工具返回都显示 |
| `pause` | `out_of_focus`(False) | False = 后台也完整运行（走位/菜单/锻造都行，**不抢前台焦点**）|
| `whiteboard` / `wb_pin` | `content`（必填） | 写白板 / 钉白板 |
| `appearance` | `hair` `hair_color` `skin` `shirt` `pants` `hat` `acc` `eye_color` `pants_color`（全 None）| 捏脸 |
| 无参 | `settle` `peek` `wb_read` `wb_clear` | |

> ⚠️ **睡别人床 / 协作前先 `check(what="role")`** —— 端口按启动顺序分配、重启可能翻转，**认错角色 = 挪了恒的人**。
> ⚠️ 饰品需**战斗精通**，未解锁会被权威拦截（报"未解锁战斗精通"）。

### `map(ops)` — 导航 **跨图唯一走这个**
`lookup`(查地点功能+出口) `query`(功能反查) `go`(走到目标,自动多段寻路/交通) `walk`(走到指定POI **或给x,y走同图坐标**) `walk_multi`(多段走位,=**闲逛**) `npc`(找NPC) `warp_safe`(紧急逃脱) `unlocks`(查存档解锁)

**📐 `map` 参数速查**

| op | 参数 | 说明 |
|---|---|---|
| `go` | `destination`(地点名/POI) `npc`(NPC名) | **跨场景切换的唯一入口**（走出口瓦片/门/买票的真实路径，**不瞬移**）。`destination` 与 `npc` 二选一 |
| `walk` | `poi_name`(POI 名) **或** `x`,`y`(同图坐标) | **二选一，两个都不给=报错**。给 POI 会**到点自动应用结构化站位+朝向**（如水池朝右、柜台朝上），交互仍要 AI 自己 `scene at/interact`；给坐标=同图精确走位（**只走同图**，跨图用 `go`）。⚠️ 底座都是 `/walk_to`（2026-09-11 起 `movetile` 退役并入这里的坐标模式） |
| `walk_multi` | `waypoints`("x,y x,y …") `location`(None) `max_wait`(18) `max_seg`(200) | **多段走位**：喂一串坐标依次走，每段等到了再走下一段。别名 **`闲逛`**/`多段走`。🎪 正事=万灵节迷宫按段走；🫧 **活人感**=闲逛遛弯 / 绕着某人转圈示好 / 浴场泳池绕圈游。⚠️每段必须**精确落到目标格**才算到达；落在相邻格会标 🟡（目标格不可站时 walk_to 会退到最近可走格，**兜底 ≠ 到达**）。旧名 `festival`/`scene` 的 `maze_walk`/`走迷宫` 仍可用 |
| `npc` | `name`（必填） | 找 NPC（跨图）|
| `lookup` | `location`（必填） | 查某地点的功能+出口 |
| `query` | `function`（必填） | 功能反查（"哪里能买到 X"）|
| `warp_safe` | 无参 | 紧急逃脱（卡住时用）|
| `unlocks` | 无参 | 查存档解锁：矿洞/巴士/下水道/姜岛/精通/**火山近路**…——**走捷径（尤其火山近路）前先查这个**，别硬走 |

> 🚦 交通优先级：**图腾柱 > 矿车 > 走路**（玩家在农场且有对应图腾柱时自动用）。
> ⚠️ 参数**必须放 `kw` 对象**，别拼进 ops 串里。

### `festival(ops)` — 节日
`today` `next` `go` `info` `interact` `answer` `shop` `eggs`(找蛋) `egg_note`(纸条) `egg_run`(捡蛋) `dance`(跳舞邀请) `help`(玩法) `prep`(备战) `poi`(限定点) `strength`(力量测试) `ice_fish`(冰雪节冰钓) `maze`(迷宫坐标) `maze_walk`(走迷宫;⚠️通用多段走位主门牌=`map walk_multi`/`闲逛`,此处旧名兼容) `display_fill`/`display_takeback`(农展台放满/收好)

**📐 `festival` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `interact` | `name`("") | 节日互动 |
| `answer` | `answer`(0) | 应答（答题类活动）|
| `egg_run` / `egg_note` | `route`("") | 复活节捡蛋 / 记纸条路线 |
| `dance` | `target`("") | 花舞节邀请跳舞（不传=默认对象）|
| `strength` | `delay`(400) | 力量测试，`delay` = **毫秒** |
| `maze_walk` | `waypoints`("") `location`(None) `max_wait`(18) `max_seg`(200) | 走迷宫（waypoints = "x,y x,y …"）。⚠️ **通用多段走位的主门牌 2026-09-12 已挪到 `map walk_multi`/`闲逛`**，此处保留旧名兼容 |
| `display_fill` | `items`("") | 农展台放满 |

> 其余（`today`/`next`/`go`/`info`/`shop`/`eggs`/`help`/`prep`/`poi`/`maze`/`ice_fish`）**无参**。

### `settings(ops)` — 系统/设置（合并"捏脸设置"进来，不再拆）
`status`(看所有设置+退役工具) `retire`(退役工具) `reactivate`(召回) `appearance`(捏脸) `customize`(起名) `color`(颜色条) `hair`/`shirt`/`pants`/`hat`/`colorpreset`(外观参考) `confirm_look`(核对捏人形象) **`session_status`/`session_set`/`session_export`(🧠 会话缓冲，🆕10-01 从 `session` 域收编进来)**

**📐 `settings` 参数速查**

| op | 参数 | 说明 |
|---|---|---|
| `appearance` | `hair` `hair_color` `skin` `shirt` `pants` `hat` `acc` `eye_color` `pants_color` | 捏脸 |
| `customize` | `name` `farmname` `favorite` | 捏人起名 |
| `color` | — | 颜色条 |
| `hair` / `shirt` / `pants` / `hat` / `colorpreset` | — | 外观编号参考表 |
| `confirm_look` | — | **核对捏人形象，`ok` 前必做** |
| `retire` / `reactivate` | — | 退役工具 / 召回 |
| `session_set` | `setting`,`value`（**都是字符串**） | 改会话缓冲设置；`setting` **只有** `max_turns` / `export_format`（`auto_export`/`include_npc` 2026-10-01 删了：它们是**死旋钮**，设了只会拿到假成功）|
| `session_status` / `session_export` | — | 看缓冲条数/最近3条/文件路径 ／ 导出**给人看的 md**（⚠️ 实时 `session_<时间戳>.jsonl` **本来就是全量**；这份是内存 ≤`max_turns` 条的子集，默认 `export_format=jsonl` 时它**不写文件并如实说明**）|

> ⚠️ **捏脸 = 创建定型**：`ok` 之后 `set_appearance` / 捏人**自动退役、不可逆**。
> 旧配置路径仍可用：`settings(setting="async", value="on")`（heartbeat/context_turns/async/state_interval/mode/auto_sleep/auto_sleep_time/pin/moss）。
> 🧠 `session_set max_turns` 与老路 `settings(setting="context_turns")` 改的是**同一个数**（现在只有这一条路，因为 `session` 域已撤出顶层）。

### `fish(ops)` — 钓鱼（2026-08-22 修复注册，现已可达）
`go`(去钓) `info`(查某地鱼) `spots`(钓点) `bobber`(浮漂样式) `rod`(鱼竿:看/上饵钓具) `crab`(蟹笼总览) `crab_water`(找水) `crab_place`(放笼) `crab_bait`(放饵) `crab_collect`(收笼) `crab_diag`(诊断笼/定位挂饵) `crab_retract`(回收笼/清搁浅)

**📐 `fish` 参数速查**

| op | 参数（括号内=默认） | 说明 |
|---|---|---|
| `go` | `location`(None) `max_casts`(0) `no_sleep`(True) | `location=None` = **就地钓**（站在原地，须自己已站到水边）；指定 `Beach`/`Mountain`/`Forest`/`Town` = 先 `map_go` 走真实路径到校准钓点再钓（**不是 warp**）。`max_casts=0` 不限（钓到体力<20/背包满/太晚/抛不出去收杆）；`no_sleep=False` 钓完回家睡 |
| `info` | `location`（必填） | 查某地能钓什么 |
| `bobber` | `style`("dice") | 浮漂样式 |
| `rod` | `action`("show") `item`("") | `action` 看/上饵/装钓具 |
| `crab_place` | `count`(5) `radius`(15) `bait`("Bait") | 放蟹笼 |
| `crab_bait` | `bait`("Bait") | 放饵 |
| `crab_water` | `radius`(15) | 找可下笼的水 |
| `crab_diag` | `location`(None) | 诊断蟹笼 |
| `crab_retract` | `x` `y` `location`(全 None) | 回收蟹笼 / 清搁浅 |

> ⚠️ **鱼塘在 `farm` 域**，不在 `fish`。
> 🧬 **挂饵前先 `check(what="profile")`**：若是 Luremaster（职业 11）蟹笼免饵，`crab_bait`/`crab_place` 挂饵是**空操作**，别浪费。

---

## 🛠️ 四、3 个独立工具（无域等价物，直接调）

| 工具 | 干嘛的 |
|---|---|
| `intent` | 🎯 **意图选项单**（"看单子 → 敲编号"）：见下节 |
| `screenshot` | 截图看画面（AI 的"眼睛"）|
| `help` | 查某域详细 ops/坑（docstring 精简后的细节兜底；不传=列话题）|

> 🎯 **`intent`**：`show` 看这一刻能做的事（一行一件，`←` 后面是理由）/ `do` 敲编号（`1` · `1,4` 多选 · `1=1,4=4` 各多少）/ `at x y` 指哪打哪（问"这一格能做什么"）/ `help`。
> ⚠️ 它的详细玩法看**域内的 `intent` 指南**（`help(intent)`），别在这儿抄第二份。

> 🗜️ **2026-09-11 收编**：原顶层 `which_role` / `advance_story` / `profile` 已全部进域（20→17）——
> **`advance_story` → `menu(ops="advance")`**（menu 的 dispatch 本就直指同一函数，留着=两条路做同一件事）；
> **`profile` → `check(what="profile")`**、**`which_role` → `check(what="role")`**（都是"查我自己"，归查询域）。
> ⚠️ 三个函数照旧注册、只是不再给 AI 直调；引导文案已同步（状态条 🎬 剧情行、menu/check/fish/daily 域 help）。
> 🗜️ **2026-10-01 收编（18→16）**：**`session`→`settings`**、**`cabin`→`scene`/`farm`/`daily`/`check`**（`cook` 搬进 `daily`）。
> 判据是"功能已被别的路替代（能收就收）"，逐 op 的替代路写在 `domain_selftest._SUBSUMED_DOMAINS`
> ——那是**闸门**：漏一条就报错；**不许**把域名塞 `_KNOWN_SUBSUMED`（那会让检查静默通过）。

---

## 🎯 五、常用场景速查（"我想… → 调…"）

| 我想干嘛 | 调用 |
|---|---|
| 起床看今天/天气/运势 | `check(what="status")` |
| 环视四周有啥 | `check(what="look")` |
| 查我的技能/职业（是否 Luremaster）| `check(what="profile")` |
| 确认我是谁 / 恒是谁（睡别人床前）| `check(what="role")` |
| 锄地种一片 | `farm(ops="till plant", x=40, y=20, rows=3, length=5)` ⚠️**x/y 必填**（不传报错，不再兜底） |
| 浇水 / 收菜 | `farm(ops="water")` / `farm(ops="harvest")` |
| 砍树 / 清地 | `farm(ops="chop")` / `farm(ops="clear", x=40, y=20, rows=2, length=4)` |
| 摸动物 / 挤奶 | `farm(ops="animals milk")` |
| 穿/脱穿戴物（衣/裤/帽/鞋/戒指/饰品）| `daily(ops="wear", name="铁头靴")` / `daily(ops="wear", slot="boots")` |
| 去挖矿 / 看进度 | `mine(ops="go")` / `mine(ops="progress")` |
| 自动炸矿 | `mine(ops="bomb_mine")` |
| 去钓鱼 | `fish(ops="go", location="Beach")` |
| 找某 NPC / 跟他聊天 | `map(ops="npc", name="艾米丽")` → `social(ops="chat")` |
| 送礼提好感 | `social(ops="gift", npc_name=…, item_name=…)` |
| 递东西给恒（一次一大把） | `social(ops="hand", player_name="恒", item_name=…)`（走过去丢他脚边，磁吸自动收） |
| 点面前的东西 | `scene(ops="interact")` |
| 点指定格（柜台/炉子）| `scene(ops="at", tile_x=…, tile_y=…)` |
| 开商店买东西 | `menu(ops="shop", place="皮埃尔商店")` |
| 点菜单选项 | `menu(ops="click", option=1)` |
| 推进剧情/对话 | `menu(ops="advance")` |
| 整段跳过剧情/事件 | `menu(ops="skip")`（事件 `skippable=true` 才跳得动）|
| 翻箱找东西 | `storage(ops="find", name="…")` → `storage(ops="take", …)` |
| 去某大广场/跨图 | `map(ops="go", destination="Town")` |
| 睡觉 / 蹭床 | `daily(ops="sleep")` |
| 过夜结算进新一天 | `daily(ops="settle")` |
| 今天/明天节日 | `festival(ops="today")` / `festival(ops="next")` |
| 去参加节日 | `festival(ops="go")` |
| 查献祭还缺啥 | `menu(ops="bundle_kb", query="工艺室")`（知识库）/ `menu(ops="bundle")` 实地看 |
| 捏脸 / 起名 | `settings(ops="appearance", …)` / `menu(ops="customize")` |
| 关掉卡住的弹窗 | `menu(ops="cancel")` |
| 后台跑长脚本 | 长任务便利工具自动后台（AI 想确认/续跑 `script(ops="continue")`；停 `script(ops="stop")`）|
| 截图看自己 | `screenshot()` |

---

## 🔄 六、旧工具 → 域形式 对照（AI 不用记旧名了）

| 旧独立工具 | 现在这样调 |
|---|---|
| `check_status` / `check_backpack` / `look_around` / `silo_status` / `mastery_status` / `building_list` / `machine_report` | `check(what="status"/"backpack"/"look"/…)` |
| `walk_to` / `go_to` / `find_npc` | `map(ops="walk")` / `map(ops="go")` / `map(ops="npc")` |
| `interact_at` / `interact` / `use_tool` / `face` / `select_item` | `scene(ops="at")` / `scene(ops="interact")` / `scene(ops="use")` / `scene(ops="face")` / `scene(ops="select")` |
| `read_menu` / `menu_click` / `press_key` / `advance_story` / `cancel` / `shop_visit` / `sell_to_shop` / `forge` / `process_geode` | `menu(ops="read"/"click"/"key"/"advance"/"cancel"/"shop"/"sell"/"forge"/"geode")` |
| `which_role`（确认端口↔角色）| `check(what="role")`（🆕09-11 从顶层收编）|
| `profile`（技能等级+职业分支）| `check(what="profile")`（🆕09-11 从顶层收编）|
| `go_sleep` / `confirm_settlement` / `eat_item` / `set_appearance` / `wear` / `lie_bed` | `daily(ops="sleep"/"settle"/"eat"/"appearance"/"wear"/"lie_bed")` |
| `scan_chests` / `chest_store` / `chest_take` | `storage(ops="view"/"store"/"take")`（⚠️ 旧文档这里写过 `scan`，**`scan` 从来不是 op**，见上面 storage 那句）|
| ~~`list_quests` / `quest_progress`~~（2026-09-01 已退役：任务/进度改 `menu(ops="journal"/"read")` 读 QuestLog 卡，卡上含每子目标 current/max；`menu(ops="know")` 查详情，原 quest 域 09-02 并入 menu） | 接单走板上 `menu click(button=accept…)`（accept_quest 已退役） |
| `run_script` / `script_start` / `script_status` / `script_stop` / `async_config`（09-02 五合一；09-05 删 status、09-06 continue 取代 run/start） | `script(ops="continue"/"stop"/"async")` |
| `session_status` / `session_set` / `session_export`（09-02 三合一，**10-01 起进 `settings`**） | `settings(ops="session_status" / "session_set" / "session_export", kw={setting,value})` |
| `chat_npc` / `gift_npc` / `give_item` / `hand_item` / `send_chat` / `emote` / `check_friendship` | `social(ops="chat"/"gift"/"give"/"hand"/"send"/"emote"/"friendship")` |
| `moss_run` / `berry_run` / `spot_run` / `pickup_scene` | `scene(ops="moss"/"berry"/"spot"/"pickup_scene")` |
| `rock_run`（室外镐击） | `scene(ops="rock")`（08-29 新增） |
| `go_mining` / `bomb_*` | `mine(ops="go"/"bomb_mine"…)` |
| `go_fishing` / `bobber_style` | `fish(ops="go"/"bobber")` |
| `set_appearance` / `character_customize` / `color_pick` / `list_hair_ref` 等 | `settings(ops="appearance"/"customize"/"color"/"hair"…)` |
| `bundle_status` / `bundle_kb` | `menu(ops="bundle")` / `menu(ops="bundle_kb")` |
| `museum_donate` / `read_book` | `menu(ops="donate")` / `menu(ops="read_book")` |

---

## 💡 七、一句提醒
- **AI 全程走域工具**，原始端点（/state /interact /click /position）AI 不会直调，只是文案里的坐标/动作提示。
- 手机前端若**直调被隐藏的旧工具名**（`walk_to`/`go_sleep`/`menu_click`…）会报不存在 → 改走域形式。⚠️ 服务端**无 `--full`/`NAGI_FULL_TOOLS` 全量回退**（2026-09-06 退役，见本文件顶部），传了也不生效。
