# 🧪 全工具测试清单（2026-09-11 生成）

> **502 个 op**（抄自各域 dispatch，非文档）｜✅ 46 ｜❌ 0 ｜T1 自动 100 ｜T2 摆场 200 ｜T3 副作用 161 ｜⏭ 当期不可用 41

> 用法：跑测试（任何渠道，只要走 :8000 的 MCP）→ `python gen_tool_checklist.py --from-log` 自动打勾。
> 判定规则见脚本头部；**⚠️ op「」= 参数被静默丢掉，算 ❌ 不算 ✅**。
> ⚠️ 本表**测试前冻结**：`--from-log` 只写后两列，永不动 op 列表。


## `check`（33）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `backpack` | T1 自动 |  | ✅ | `session_log:116` |
| `building` | T1 自动 |  | ✅ | `session_log:117` |
| `building_list` ≡`building` | T1 自动 |  |  |  |
| `buildings` ≡`building` | T1 自动 |  | ✅ | `session_log:15` |
| `chests` | T1 自动 | chest | ✅ | `session_log:118` |
| `hay` | T1 自动 |  | ✅ | `session_log:119` |
| `look` | T1 自动 | radius | ✅ | `session_log:120` |
| `machine` | T1 自动 |  | ✅ | `session_log:121` |
| `machine_report` ≡`machine` | T1 自动 |  |  |  |
| `machines` ≡`machine` | T1 自动 |  | ✅ | `session_log:10` |
| `mastery` | T1 自动 |  | ✅ | `session_log:122` |
| `mine` | T1 自动 |  | ✅ | `session_log:123` |
| `mining` ≡`mine` | T1 自动 |  |  |  |
| `profile` | T1 自动 |  | ✅ | `session_log:124` |
| `quest` | T2 摆场 |  |  |  |
| `quests` ≡`quest` | T2 摆场 |  |  |  |
| `role` | T1 自动 |  | ✅ | `session_log:125` |
| `silo` ≡`hay` | T1 自动 |  | ✅ | `session_log:13` |
| `status` | T1 自动 |  | ✅ | `session_log:126` |
| `storage` | T1 自动 |  | ✅ | `session_log:127` |
| `worn` | T1 自动 |  | ✅ | `session_log:128` |
| `任务` ≡`quest` | T2 摆场 |  |  |  |
| `周围` ≡`look` | T1 自动 |  |  |  |
| `存储` ≡`storage` | T1 自动 |  |  |  |
| `我是谁` ≡`role` | T1 自动 |  |  |  |
| `技能` ≡`profile` | T1 自动 |  |  |  |
| `环视` ≡`look` | T1 自动 |  |  |  |
| `端口` ≡`role` | T1 自动 |  |  |  |
| `箱子` ≡`chests` | T1 自动 |  |  |  |
| `精通` ≡`mastery` | T1 自动 |  |  |  |
| `职业` ≡`profile` | T1 自动 |  |  |  |
| `职业分支` ≡`profile` | T1 自动 |  |  |  |
| `角色` ≡`role` | T1 自动 |  |  |  |

## `farm`（77）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `animals` | T2 摆场 |  |  |  |
| `break` | T3 副作用 | radius, steps |  |  |
| `building` | T2 摆场 | location, machine_type |  |  |
| `buy` | T3 副作用 | animal_type, building |  |  |
| `chop` | T2 摆场 | area |  |  |
| `clear` | T2 摆场 | direction, length, rows |  |  |
| `clearground` | T2 摆场 |  |  |  |
| `collect` | T2 摆场 | location, machine_type |  |  |
| `doors` | T2 摆场 |  |  |  |
| `fertilize` | T2 摆场 | direction, fertilizer_name, length, rows |  |  |
| `harvest` | T2 摆场 | radius |  |  |
| `hay` | T2 摆场 | dry_run |  |  |
| `hoe` | T2 摆场 | layout, x1, x2, y1, y2 |  |  |
| `load` | T2 摆场 | location, machine_type |  |  |
| `milk` | T2 摆场 |  |  |  |
| `pet` | T3 副作用 |  |  |  |
| `petwalk` | T3 副作用 | include_petted |  |  |
| `place` | T3 副作用 | radius, steps |  |  |
| `plan` | T2 摆场 | hoe_level, layout, trellis, x1, x2, y1, y2 |  |  |
| `plant` | T2 摆场 | direction, length, rows, seed_name |  |  |
| `plantlayout` | T2 摆场 | direct, layout, seed, trellis, x1, x2, y1, y2 |  |  |
| `plot` | T2 摆场 | all_plots, radius | ✅ | `session_log:43` |
| `pond` | T2 摆场 |  |  |  |
| `pond_add` | T2 摆场 |  |  |  |
| `pond_collect` | T2 摆场 |  |  |  |
| `pond_feed` | T2 摆场 |  |  |  |
| `pond_fish` | T2 摆场 |  |  |  |
| `scythe` | T2 摆场 | radius |  |  |
| `shear` ≡`milk` | T2 摆场 |  |  |  |
| `sow` ≡`plant` | T2 摆场 |  |  |  |
| `statue` | T2 摆场 |  |  |  |
| `till` | T2 摆场 | direction, length, rows |  |  |
| `till_plant` | T2 摆场 | direction, length, rows, seed_name, trellis |  |  |
| `tillfield` | T2 摆场 |  |  |  |
| `water` | T2 摆场 | radius |  |  |
| `上料` ≡`load` | T2 摆场 |  |  |  |
| `买` ≡`buy` | T3 副作用 |  |  |  |
| `买动物` ≡`buy` | T3 副作用 |  |  |  |
| `关门` ≡`doors` | T2 摆场 |  |  |  |
| `剪毛` ≡`milk` | T2 摆场 |  |  |  |
| `加草` ≡`hay` | T2 摆场 |  |  |  |
| `化肥` ≡`fertilize` | T2 摆场 |  |  |  |
| `喂塘` ≡`pond_feed` | T2 摆场 |  |  |  |
| `喂水` | T3 副作用 |  |  |  |
| `塘` ≡`pond` | T2 摆场 |  |  |  |
| `塘钓` ≡`pond_fish` | T2 摆场 |  |  |  |
| `宠物碗` ≡`喂水` | T3 副作用 |  |  |  |
| `布局锄` ≡`hoe` | T2 摆场 |  |  |  |
| `干草` ≡`hay` | T2 摆场 |  |  |  |
| `拆` ≡`break` | T3 副作用 |  |  |  |
| `挤奶` ≡`milk` | T2 摆场 |  |  |  |
| `摸动物` ≡`animals` | T2 摆场 |  |  |  |
| `摸摸` ≡`pet` | T3 副作用 |  |  |  |
| `摸猫狗` ≡`pet` | T3 副作用 |  |  |  |
| `播种规划` ≡`plantlayout` | T2 摆场 |  |  |  |
| `收` ≡`harvest` | T2 摆场 |  |  |  |
| `收放` ≡`building` | T2 摆场 |  |  |  |
| `放` ≡`place` | T3 副作用 |  |  |  |
| `放牧` ≡`petwalk` | T3 副作用 |  |  |  |
| `放置` ≡`place` | T3 副作用 |  |  |  |
| `放鱼` ≡`pond_add` | T2 摆场 |  |  |  |
| `敲` ≡`break` | T3 副作用 |  |  |  |
| `方形规划` ≡`plan` | T2 摆场 |  |  |  |
| `机器` ≡`collect` | T2 摆场 |  |  |  |
| `浇` ≡`water` | T2 摆场 |  |  |  |
| `清` ≡`clear` | T2 摆场 |  |  |  |
| `清格` ≡`clearground` | T2 摆场 |  |  |  |
| `畜舍` | T2 摆场 |  |  |  |
| `砍树` ≡`chop` | T2 摆场 |  |  |  |
| `碗` ≡`喂水` | T3 副作用 |  |  |  |
| `祈福` ≡`statue` | T2 摆场 |  |  |  |
| `蓄力锄` ≡`tillfield` | T2 摆场 |  |  |  |
| `规划` ≡`plot` | T2 摆场 |  |  |  |
| `这间` ≡`畜舍` | T2 摆场 |  |  |  |
| `遛` ≡`petwalk` | T3 副作用 |  |  |  |
| `领籽` ≡`pond_collect` | T2 摆场 |  |  |  |
| `鱼塘` ≡`pond` | T2 摆场 |  |  |  |

## `mine`（16）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `bomb_collect` | T2 摆场 | max_items |  |  |
| `bomb_ladder` | T2 摆场 |  |  |  |
| `bomb_mine` | T2 摆场 | autodrop, bomb, follow_host, lead, min_covered, one_floor, target |  |  |
| `bomb_place` | T3 副作用 |  |  |  |
| `bomb_plan` | T1 自动 | min_covered, radius, top | ✅ | `session_log:129` |
| `bomb_retreat` | T2 摆场 |  |  |  |
| `bomb_status` | T1 自动 |  | ✅ | `session_log:130` |
| `bomb_volcano` | T3 副作用 | bomb, hp_threshold, max_minutes, min_covered, poll |  |  |
| `farm` | T2 摆场 |  |  |  |
| `go` ≡`farm` | T2 摆场 | cycles, food_hp, food_sta, hp_threshold, mode, ore, resume, start, target |  |  |
| `organize` | T2 摆场 | disable, reset |  |  |
| `progress` | T1 自动 |  | ✅ | `session_log:131` |
| `rush` ≡`farm` | T2 摆场 |  |  |  |
| `去` ≡`farm` | T2 摆场 |  |  |  |
| `整理背包` ≡`organize` | T2 摆场 |  |  |  |
| `进度` ≡`progress` | T1 自动 |  |  |  |

## `cabin`（27）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `break` | T3 副作用 | radius, steps |  |  |
| `collect` | T2 摆场 |  |  |  |
| `cook` | T3 副作用 | recipe_name |  |  |
| `enum` | T2 摆场 |  |  |  |
| `furniture` | T1 自动 |  | ✅ | `session_log:132` |
| `interact` | T2 摆场 | tile_x, tile_y |  |  |
| `pickup` | T2 摆场 | tile_x, tile_y |  |  |
| `place` | T3 副作用 |  |  |  |
| `sleep` | T3 副作用 | who |  |  |
| `statue` | T2 摆场 |  |  |  |
| `做饭` ≡`cook` | T3 副作用 |  |  |  |
| `家具` ≡`furniture` | T1 自动 |  |  |  |
| `引导` ≡`enum` | T2 摆场 |  |  |  |
| `拆` ≡`break` | T3 副作用 |  |  |  |
| `拿` ≡`pickup` | T2 摆场 |  |  |  |
| `摆` ≡`pickup` | T2 摆场 |  |  |  |
| `收` ≡`collect` | T2 摆场 |  |  |  |
| `放` ≡`place` | T3 副作用 |  |  |  |
| `放置` ≡`place` | T3 副作用 |  |  |  |
| `敲` ≡`break` | T3 副作用 |  |  |  |
| `机器` ≡`collect` | T2 摆场 |  |  |  |
| `点` ≡`interact` | T2 摆场 |  |  |  |
| `看` ≡`enum` | T2 摆场 |  |  |  |
| `睡` ≡`sleep` | T3 副作用 |  |  |  |
| `睡觉` ≡`sleep` | T3 副作用 |  |  |  |
| `祈福` ≡`statue` | T2 摆场 |  |  |  |
| `雕像` ≡`statue` | T2 摆场 |  |  |  |

## `social`（17）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `chat` | T2 摆场 |  |  |  |
| `emote` | T3 副作用 |  |  |  |
| `friendship` | T1 自动 |  | ✅ | `session_log:133` |
| `gift` | T3 副作用 | item_name, npc_name |  |  |
| `give` | T3 副作用 | item_name, player_name |  |  |
| `hand` | T3 副作用 | item_name, player_name |  |  |
| `movie` | T2 摆场 | npc |  |  |
| `send` | T3 副作用 | message |  |  |
| `snack` | T2 摆场 |  |  |  |
| `发` ≡`send` | T3 副作用 |  |  |  |
| `好感` ≡`friendship` | T1 自动 |  |  |  |
| `搭话` ≡`chat` | T2 摆场 |  |  |  |
| `电影` ≡`movie` | T2 摆场 |  |  |  |
| `给` ≡`give` | T3 副作用 |  |  |  |
| `送礼` ≡`gift` | T3 副作用 |  |  |  |
| `递给` ≡`hand` | T3 副作用 |  |  |  |
| `零食` ≡`snack` | T2 摆场 |  |  |  |

## `scene`（70）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `at` | T2 摆场 | tile_x, tile_y |  |  |
| `berry` | T3 副作用 |  |  |  |
| `break` | T3 副作用 | radius, steps |  |  |
| `drop` | T3 副作用 |  |  |  |
| `face` | T2 摆场 | direction |  |  |
| `forge_help` | T2 摆场 |  |  |  |
| `front` | T2 摆场 |  |  |  |
| `furniture` | T1 自动 |  | ✅ | `session_log:134` |
| `garbage` | T2 摆场 | dry_run, loc, pos, wait |  |  |
| `interact` ≡`front` | T2 摆场 |  |  |  |
| `maze` | T2 摆场 | gx, gy, radius |  |  |
| `maze_seg` | T2 摆场 | gx, gy, radius |  |  |
| `maze_walk` | T2 摆场 | location, max_seg, max_wait, waypoints |  |  |
| `moss` | T3 副作用 | dry_run, radius, rounds, target_max |  |  |
| `pan` | T2 摆场 | dry_run, radius, timeout |  |  |
| `pickup` | T2 摆场 | tile_x, tile_y |  |  |
| `pickup_scene` | T3 副作用 | max_items |  |  |
| `place` | T3 副作用 |  |  |  |
| `rock` | T3 副作用 | break_stone, dig, max_break, radius |  |  |
| `rummage` ≡`garbage` | T2 摆场 |  |  |  |
| `seats` | T1 自动 | radius | ✅ | `session_log:135` |
| `select` | T3 副作用 |  |  |  |
| `sit` | T2 摆场 | face |  |  |
| `spot` | T3 副作用 |  |  |  |
| `stand` | T2 摆场 |  |  |  |
| `use` | T3 副作用 |  |  |  |
| `丢` ≡`drop` | T3 副作用 |  |  |  |
| `分段` ≡`maze_seg` | T2 摆场 |  |  |  |
| `可坐` ≡`seats` | T1 自动 |  |  |  |
| `坐` ≡`sit` | T2 摆场 |  |  |  |
| `坐下` ≡`sit` | T2 摆场 |  |  |  |
| `家具` ≡`furniture` | T1 自动 |  |  |  |
| `座位` ≡`seats` | T1 自动 |  |  |  |
| `拆` ≡`break` | T3 副作用 |  |  |  |
| `拾起` ≡`pickup` | T2 摆场 |  |  |  |
| `拿` ≡`select` | T3 副作用 |  |  |  |
| `挖斑点` ≡`spot` | T3 副作用 |  |  |  |
| `挖石` ≡`rock` | T3 副作用 |  |  |  |
| `挖矿点` ≡`rock` | T3 副作用 |  |  |  |
| `挖蚯蚓` ≡`spot` | T3 副作用 |  |  |  |
| `挥` ≡`use` | T3 副作用 |  |  |  |
| `捡物` ≡`pickup_scene` | T3 副作用 |  |  |  |
| `捡采集` ≡`pickup_scene` | T3 副作用 |  |  |  |
| `搜刮苔藓` ≡`moss` | T3 副作用 |  |  |  |
| `摇树莓` ≡`berry` | T3 副作用 |  |  |  |
| `放` ≡`place` | T3 副作用 |  |  |  |
| `放置` ≡`place` | T3 副作用 |  |  |  |
| `敲` ≡`break` | T3 副作用 |  |  |  |
| `敲击` ≡`break` | T3 副作用 |  |  |  |
| `敲石` ≡`rock` | T3 副作用 |  |  |  |
| `浆果` ≡`berry` | T3 副作用 |  |  |  |
| `淘` ≡`pan` | T2 摆场 |  |  |  |
| `淘盘` ≡`pan` | T2 摆场 |  |  |  |
| `淘金` ≡`pan` | T2 摆场 |  |  |  |
| `点` ≡`at` | T2 摆场 |  |  |  |
| `站起` ≡`stand` | T2 摆场 |  |  |  |
| `站起来` ≡`stand` | T2 摆场 |  |  |  |
| `绿雨` ≡`moss` | T3 副作用 |  |  |  |
| `翻垃圾桶` ≡`garbage` | T2 摆场 |  |  |  |
| `翻桶` ≡`garbage` | T2 摆场 |  |  |  |
| `走迷宫` ≡`maze_walk` | T2 摆场 |  |  |  |
| `起身` ≡`stand` | T2 摆场 |  |  |  |
| `转身` ≡`face` | T2 摆场 |  |  |  |
| `迷宫` ≡`maze` | T2 摆场 |  |  |  |
| `迷宫走` ≡`maze_walk` | T2 摆场 |  |  |  |
| `迷宫链` ≡`maze_seg` | T2 摆场 |  |  |  |
| `采矿点` ≡`rock` | T3 副作用 |  |  |  |
| `铜锅` ≡`pan` | T2 摆场 |  |  |  |
| `锻造帮助` ≡`forge_help` | T2 摆场 |  |  |  |
| `面前` ≡`front` | T2 摆场 |  |  |  |

## `menu`（70）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `advance` | T2 摆场 |  |  |  |
| `bin` | T3 副作用 | sell_all |  |  |
| `bundle` | T1 自动 | area | ✅ | `session_log:51` |
| `bundle_kb` | T1 自动 | query | ✅ | `session_log:136` |
| `cancel` | T2 摆场 |  |  |  |
| `click` | T3 副作用 | action, button, category, option, quantity, real, right, slot |  |  |
| `craft` | T3 副作用 | item_name |  |  |
| `craftables` | T1 自动 |  | ✅ | `session_log:137` |
| `customize` | T3 副作用 | farmname, favorite |  |  |
| `display_fill` | T3 副作用 | items |  |  |
| `display_takeback` | T3 副作用 |  |  |  |
| `donate` | T3 副作用 |  |  |  |
| `forge` | T3 副作用 | item1, item2, mode, target |  |  |
| `geode` | T3 副作用 |  |  |  |
| `geodes` | T3 副作用 |  |  |  |
| `journal` | T2 摆场 |  |  |  |
| `key` | T2 摆场 | hold, key |  |  |
| `know` | T2 摆场 |  |  |  |
| `levelup_choose` | T3 副作用 | profession, side |  |  |
| `minigame` | T2 摆场 | action |  |  |
| `minigame_state` | T2 摆场 |  |  |  |
| `number` | T2 摆场 | confirm |  |  |
| `read` | T2 摆场 |  |  |  |
| `read_book` | T3 副作用 |  |  |  |
| `recipes` | T1 自动 |  | ✅ | `session_log:138` |
| `sell` | T3 副作用 |  |  |  |
| `shop` | T2 摆场 | place, want |  |  |
| `任务知` ≡`know` | T2 摆场 |  |  |  |
| `关` ≡`cancel` | T2 摆场 |  |  |  |
| `出货` ≡`bin` | T3 副作用 |  |  |  |
| `分支` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `剧情` ≡`advance` | T2 摆场 |  |  |  |
| `卖` ≡`sell` | T3 副作用 |  |  |  |
| `取消` ≡`cancel` | T2 摆场 |  |  |  |
| `合成` ≡`craft` | T3 副作用 |  |  |  |
| `填槽` ≡`display_fill` | T3 副作用 |  |  |  |
| `小游戏` ≡`minigame` | T2 摆场 |  |  |  |
| `小游戏状态` ≡`minigame_state` | T2 摆场 |  |  |  |
| `开日志` ≡`journal` | T2 摆场 |  |  |  |
| `按键` ≡`key` | T2 摆场 |  |  |  |
| `捏人` ≡`customize` | T3 副作用 |  |  |  |
| `捐` ≡`donate` | T3 副作用 |  |  |  |
| `捐赠` ≡`donate` | T3 副作用 |  |  |  |
| `推进` ≡`advance` | T2 摆场 |  |  |  |
| `收` ≡`display_takeback` | T3 副作用 |  |  |  |
| `收好` ≡`display_takeback` | T3 副作用 |  |  |  |
| `放满` ≡`display_fill` | T3 副作用 |  |  |  |
| `数量` ≡`number` | T2 摆场 |  |  |  |
| `数量框` ≡`number` | T2 摆场 |  |  |  |
| `日志` ≡`journal` | T2 摆场 |  |  |  |
| `晶球` ≡`geode` | T3 副作用 |  |  |  |
| `点` ≡`click` | T3 副作用 |  |  |  |
| `献祭` ≡`bundle` | T1 自动 |  |  |  |
| `献祭知识` ≡`bundle_kb` | T1 自动 |  |  |  |
| `看` ≡`read` | T2 摆场 |  |  |  |
| `知` ≡`know` | T2 摆场 |  |  |  |
| `知识库` ≡`bundle_kb` | T1 自动 |  |  |  |
| `职业选` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `菜谱` ≡`recipes` | T1 自动 |  |  |  |
| `订单知` ≡`know` | T2 摆场 |  |  |  |
| `读书` ≡`read_book` | T3 副作用 |  |  |  |
| `读技能书` ≡`read_book` | T3 副作用 |  |  |  |
| `读物品` ≡`read_book` | T3 副作用 |  |  |  |
| `读纸条` ≡`read_book` | T3 副作用 |  |  |  |
| `赌场` ≡`minigame` | T2 摆场 |  |  |  |
| `起名` ≡`customize` | T3 副作用 |  |  |  |
| `选职业` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `逛店` ≡`shop` | T2 摆场 |  |  |  |
| `配方` ≡`craftables` | T1 自动 |  |  |  |
| `锻造` ≡`forge` | T3 副作用 |  |  |  |

## `storage`（20）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `default` | T3 副作用 |  |  |  |
| `find` | T1 自动 |  | ✅ | `session_log:139` |
| `store` | T3 副作用 | all, items, keepTools, target |  |  |
| `tag` | T3 副作用 | target |  |  |
| `take` | T3 副作用 | items |  |  |
| `view` | T1 自动 | box | ✅ | `session_log:140` |
| `取` ≡`take` | T3 副作用 |  |  |  |
| `堆` ≡`store` | T3 副作用 |  |  |  |
| `多取` ≡`take` | T3 副作用 |  |  |  |
| `存` ≡`store` | T3 副作用 |  |  |  |
| `存智能` ≡`store` | T3 副作用 |  |  |  |
| `扫` ≡`view` | T1 自动 |  |  |  |
| `批量取` ≡`take` | T3 副作用 |  |  |  |
| `找` ≡`find` | T1 自动 |  |  |  |
| `搜` ≡`find` | T1 自动 |  |  |  |
| `查` ≡`find` | T1 自动 |  |  |  |
| `标记` ≡`tag` | T3 副作用 |  |  |  |
| `看` ≡`view` | T1 自动 |  |  |  |
| `看箱` ≡`view` | T1 自动 |  |  |  |
| `默认` ≡`default` | T3 副作用 |  |  |  |

## `daily`（30）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `appearance` | T3 副作用 | acc, eye_color, hair, hair_color, hat, pants, pants_color, shirt, skin |  |  |
| `eat` | T3 副作用 | item_name |  |  |
| `heartbeat` | T3 副作用 | minutes |  |  |
| `lie_bed` | T3 副作用 | who |  |  |
| `pause` | T3 副作用 | out_of_focus |  |  |
| `peek` | T2 摆场 |  |  |  |
| `settle` | T3 副作用 |  |  |  |
| `sleep` | T3 副作用 | who |  |  |
| `wb_clear` | T3 副作用 |  |  |  |
| `wb_pin` | T3 副作用 | content |  |  |
| `wb_read` | T3 副作用 |  |  |  |
| `wear` | T3 副作用 | hand, slot |  |  |
| `whiteboard` | T3 副作用 | content |  |  |
| `写白板` ≡`whiteboard` | T3 副作用 |  |  |  |
| `吃` ≡`eat` | T3 副作用 |  |  |  |
| `心跳` ≡`heartbeat` | T3 副作用 |  |  |  |
| `捏脸` ≡`appearance` | T3 副作用 |  |  |  |
| `暂停` ≡`pause` | T3 副作用 |  |  |  |
| `清白板` ≡`wb_clear` | T3 副作用 |  |  |  |
| `白板` ≡`whiteboard` | T3 副作用 |  |  |  |
| `看恒` ≡`peek` | T2 摆场 |  |  |  |
| `看白板` ≡`wb_read` | T3 副作用 |  |  |  |
| `睡` ≡`sleep` | T3 副作用 |  |  |  |
| `穿` ≡`wear` | T3 副作用 |  |  |  |
| `穿戴` ≡`wear` | T3 副作用 |  |  |  |
| `结算` ≡`settle` | T3 副作用 |  |  |  |
| `脱` ≡`wear` | T3 副作用 |  |  |  |
| `躺` ≡`lie_bed` | T3 副作用 |  |  |  |
| `躺床` ≡`lie_bed` | T3 副作用 |  |  |  |
| `钉白板` ≡`wb_pin` | T3 副作用 |  |  |  |

## `map`（14）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `go` | T2 摆场 | destination | ✅ | `session_log:75` |
| `lookup` | T1 自动 | location | ✅ | `session_log:141` |
| `movetile` | T2 摆场 |  |  |  |
| `npc` | T2 摆场 |  |  |  |
| `query` | T1 自动 | function | ✅ | `session_log:142` |
| `walk` | T2 摆场 | poi_name |  |  |
| `warp_safe` | T2 摆场 |  |  |  |
| `反查` ≡`query` | T1 自动 |  |  |  |
| `找人` ≡`npc` | T2 摆场 |  |  |  |
| `查` ≡`lookup` | T1 自动 |  |  |  |
| `走` ≡`go` | T2 摆场 |  |  |  |
| `走到` ≡`walk` | T2 摆场 |  |  |  |
| `走格` ≡`movetile` | T2 摆场 |  |  |  |
| `逃脱` ≡`warp_safe` | T2 摆场 |  |  |  |

## `festival`（55）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `answer` | ⏭ 当期不可用 | answer |  |  |
| `dance` | ⏭ 当期不可用 | target |  |  |
| `display_fill` | ⏭ 当期不可用 | items |  |  |
| `display_takeback` | ⏭ 当期不可用 |  |  |  |
| `egg_note` | ⏭ 当期不可用 | route |  |  |
| `egg_run` | ⏭ 当期不可用 | route |  |  |
| `eggs` | ⏭ 当期不可用 |  |  |  |
| `go` | ⏭ 当期不可用 |  |  |  |
| `help` | T1 自动 |  | ✅ | `session_log:143` |
| `ice_fish` | ⏭ 当期不可用 |  |  |  |
| `info` | T1 自动 |  | ✅ | `session_log:144` |
| `interact` | ⏭ 当期不可用 |  |  |  |
| `maze` | ⏭ 当期不可用 |  |  |  |
| `maze_walk` | ⏭ 当期不可用 | location, max_seg, max_wait, waypoints |  |  |
| `next` | T1 自动 |  | ✅ | `session_log:145` |
| `poi` | ⏭ 当期不可用 |  |  |  |
| `prep` | ⏭ 当期不可用 |  |  |  |
| `shop` | ⏭ 当期不可用 |  |  |  |
| `strength` | ⏭ 当期不可用 | delay |  |  |
| `today` | T1 自动 |  | ✅ | `session_log:146` |
| `下一个` ≡`next` | T1 自动 |  |  |  |
| `互动` ≡`interact` | ⏭ 当期不可用 |  |  |  |
| `今天` ≡`today` | T1 自动 |  |  |  |
| `冰` ≡`ice_fish` | ⏭ 当期不可用 |  |  |  |
| `冰钓` ≡`ice_fish` | ⏭ 当期不可用 |  |  |  |
| `准备` ≡`prep` | T1 自动 |  |  |  |
| `力量` ≡`strength` | ⏭ 当期不可用 |  |  |  |
| `厨师` ≡`poi` | ⏭ 当期不可用 |  |  |  |
| `去` ≡`go` | ⏭ 当期不可用 |  |  |  |
| `取回` ≡`display_takeback` | T1 自动 |  |  |  |
| `商店` ≡`shop` | ⏭ 当期不可用 |  |  |  |
| `备战` ≡`prep` | ⏭ 当期不可用 |  |  |  |
| `实况` ≡`info` | ⏭ 当期不可用 |  |  |  |
| `展位收` ≡`display_takeback` | ⏭ 当期不可用 |  |  |  |
| `展位放` ≡`display_fill` | ⏭ 当期不可用 |  |  |  |
| `应答` ≡`answer` | ⏭ 当期不可用 |  |  |  |
| `引导` ≡`help` | T1 自动 |  |  |  |
| `找蛋` ≡`eggs` | ⏭ 当期不可用 |  |  |  |
| `捡` ≡`egg_run` | T1 自动 |  |  |  |
| `捡蛋` ≡`egg_run` | ⏭ 当期不可用 |  |  |  |
| `收` ≡`display_takeback` | T1 自动 |  |  |  |
| `收好` ≡`display_takeback` | ⏭ 当期不可用 |  |  |  |
| `放满` ≡`display_fill` | ⏭ 当期不可用 |  |  |  |
| `测力` ≡`strength` | ⏭ 当期不可用 |  |  |  |
| `玩法` ≡`help` | T1 自动 |  |  |  |
| `纸条` ≡`egg_note` | ⏭ 当期不可用 |  |  |  |
| `舞` ≡`dance` | ⏭ 当期不可用 |  |  |  |
| `蛋` ≡`eggs` | T1 自动 |  |  |  |
| `记` ≡`egg_note` | ⏭ 当期不可用 |  |  |  |
| `走迷宫` ≡`maze_walk` | T1 自动 |  |  |  |
| `跳舞` ≡`dance` | ⏭ 当期不可用 |  |  |  |
| `迷宫` ≡`maze` | ⏭ 当期不可用 |  |  |  |
| `迷宫走` ≡`maze_walk` | ⏭ 当期不可用 |  |  |  |
| `邀请` ≡`dance` | ⏭ 当期不可用 |  |  |  |
| `限定` ≡`poi` | ⏭ 当期不可用 |  |  |  |

## `fish`（28）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `bobber` | T2 摆场 | style |  |  |
| `crab` | T1 自动 |  | ✅ | `session_log:147` |
| `crab_bait` | T2 摆场 | bait |  |  |
| `crab_collect` | T2 摆场 |  |  |  |
| `crab_diag` | T2 摆场 | location |  |  |
| `crab_place` | T2 摆场 | bait, radius |  |  |
| `crab_retract` | T2 摆场 | location |  |  |
| `crab_water` | T2 摆场 | radius |  |  |
| `fish` | T2 摆场 |  |  |  |
| `go` ≡`fish` | T2 摆场 | location, max_casts, no_sleep |  |  |
| `info` | T1 自动 | location | ✅ | `session_log:148` |
| `rod` | T2 摆场 | action |  |  |
| `spots` | T2 摆场 |  |  |  |
| `回收笼` ≡`crab_retract` | T2 摆场 |  |  |  |
| `找水` ≡`crab_water` | T2 摆场 |  |  |  |
| `收笼` ≡`crab_collect` | T2 摆场 |  |  |  |
| `放笼` ≡`crab_place` | T2 摆场 |  |  |  |
| `放饵` ≡`crab_bait` | T2 摆场 |  |  |  |
| `查` ≡`info` | T1 自动 |  |  |  |
| `样式` ≡`bobber` | T2 摆场 |  |  |  |
| `浮漂` ≡`bobber` | T2 摆场 |  |  |  |
| `能钓` ≡`info` | T1 自动 |  |  |  |
| `蟹笼` ≡`crab` | T1 自动 |  |  |  |
| `诊断笼` ≡`crab_diag` | T2 摆场 |  |  |  |
| `钓` ≡`fish` | T2 摆场 |  |  |  |
| `钓点` ≡`spots` | T2 摆场 |  |  |  |
| `鱼` ≡`info` | T1 自动 |  |  |  |
| `鱼竿` ≡`rod` | T2 摆场 |  |  |  |

## `settings`（31）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `appearance` | T3 副作用 | acc, eye_color, hair_color, pants_color, skin |  |  |
| `color` | T2 摆场 |  |  |  |
| `colorpreset` | T1 自动 |  | ✅ | `session_log:149` |
| `confirm_look` | T2 摆场 |  |  |  |
| `customize` | T3 副作用 | farmname, favorite |  |  |
| `hair` | T1 自动 |  | ✅ | `session_log:150` |
| `hat` | T1 自动 |  | ✅ | `session_log:151` |
| `pants` | T1 自动 |  | ✅ | `session_log:152` |
| `reactivate` | T3 副作用 |  |  |  |
| `retire` | T3 副作用 |  |  |  |
| `shirt` | T1 自动 |  | ✅ | `session_log:153` |
| `status` | T1 自动 |  | ✅ | `session_log:154` |
| `上衣` ≡`shirt` | T1 自动 |  |  |  |
| `发型` ≡`hair` | T1 自动 |  |  |  |
| `召回` ≡`reactivate` | T3 副作用 |  |  |  |
| `外观` ≡`appearance` | T3 副作用 |  |  |  |
| `帽子` ≡`hat` | T1 自动 |  |  |  |
| `捏人` ≡`customize` | T3 副作用 |  |  |  |
| `捏脸` ≡`appearance` | T3 副作用 |  |  |  |
| `改外观` ≡`appearance` | T3 副作用 |  |  |  |
| `核对` ≡`confirm_look` | T2 摆场 |  |  |  |
| `状态` ≡`status` | T1 自动 |  |  |  |
| `看` ≡`status` | T1 自动 |  |  |  |
| `确认形象` ≡`confirm_look` | T2 摆场 |  |  |  |
| `确认捏脸` ≡`confirm_look` | T2 摆场 |  |  |  |
| `裤子` ≡`pants` | T1 自动 |  |  |  |
| `设置状态` ≡`status` | T1 自动 |  |  |  |
| `调色` ≡`colorpreset` | T1 自动 |  |  |  |
| `起名` ≡`customize` | T3 副作用 |  |  |  |
| `退役` ≡`retire` | T3 副作用 |  |  |  |
| `颜色` ≡`color` | T2 摆场 |  |  |  |

## `script`（8）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `async` | T2 摆场 | add, enable, remove, show |  |  |
| `continue` | T3 副作用 | job_id |  |  |
| `stop` | T3 副作用 | job_id |  |  |
| `停` ≡`stop` | T3 副作用 |  |  |  |
| `异步` ≡`async` | T2 摆场 |  |  |  |
| `白名单` ≡`async` | T2 摆场 |  |  |  |
| `继续` ≡`continue` | T3 副作用 |  |  |  |
| `自动` ≡`async` | T2 摆场 |  |  |  |

## `session`（6）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `export` | T3 副作用 |  |  |  |
| `set` | T2 摆场 | setting |  |  |
| `status` | T1 自动 |  | ✅ | `session_log:155` |
| `导出` ≡`export` | T3 副作用 |  |  |  |
| `改` ≡`set` | T2 摆场 |  |  |  |
| `看` ≡`status` | T1 自动 |  |  |  |

## ⚠️ 分层存疑（判成 T1，但目标函数会动角色）

> **别直接按 T1 无脑扫这些** —— 先人工确认归哪层。判据=目标函数源码里出现下列调用。

✅ 无。（只查了 `_MOVE_CALLS` 列的那几种调用 —— 这是**没发现**，不是**证明没有**。）

---

## 📐 图例

- **T1 自动**：只读/幂等，随时可跑（建议先全扫一遍）
- **T2 摆场**：要 AI 站在对的地方、或当前图/季节合适
- **T3 副作用**：会改状态/花资源/不可逆，**要恒在场点头**
- **⏭ 当期不可用**：节庆日专属/需解锁 —— **不算没测，是测不了**，别打勾

