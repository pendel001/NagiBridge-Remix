# 🧪 全工具测试清单（2026-09-11 生成）

> **505 行 = 190 个后端函数**（抄自各域 dispatch，非文档）｜✅ 299 行 / **113 函数** ｜❌ 0 ｜⏭ 没条件测 30 行 / 8 函数｜**待测 69 函数**｜T1 自动 100 ｜T2 摆场 203 ｜T3 副作用 161 ｜⏭ 当期不可用 41

> 用法：跑测试（任何渠道，只要走 :8000 的 MCP）→ `python gen_tool_checklist.py --from-log` 自动打勾。
> 判定规则见脚本头部；**⚠️ op「」= 参数被静默丢掉，算 ❌ 不算 ✅**。
> ⚠️ 本表**测试前冻结**：`--from-log` 只写后两列，永不动 op 列表。
> 🔁 **`⇄` = 跨域同函数**（同一个后端函数挂在别的域下，各占一行）：判定按函数传播，任一行绿了其余行跟着绿 —— 所以**别重复测**，看 `⇄` 挑一行测即可。


## `check`（33）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `backpack` | T1 自动 |  | ✅ | `session_log:541` |
| `building` | T1 自动 |  | ✅ | `session_log:455` |
| `building_list` ≡`building` | T1 自动 |  | ✅ | ← 同 `check:building`〔building_list〕 |
| `buildings` ≡`building` | T1 自动 |  | ✅ | `session_log:15` |
| `chests` | T1 自动 | chest | ✅ | `session_log:456` |
| `hay` | T1 自动 |  | ✅ | `session_log:457` |
| `look` | T1 自动 | radius | ✅ | `session_log:632` |
| `machine` | T1 自动 |  | ✅ | `session_log:459` |
| `machine_report` ≡`machine` | T1 自动 |  | ✅ | ← 同 `check:machine`〔machine_report〕 |
| `machines` ≡`machine` | T1 自动 |  | ✅ | `session_log:10` |
| `mastery` | T1 自动 |  | ✅ | `session_log:626` |
| `mine` ⇄`mine:progress` | T1 自动 |  | ✅ | `session_log:461` |
| `mining` ≡`mine` ⇄`mine:progress` | T1 自动 |  | ✅ | ← 同 `mine:progress`〔check_mine_progress〕 |
| `profile` | T1 自动 |  | ✅ | `session_log:625` |
| `quest` ⇄`menu:journal` | T2 摆场 |  | ✅ | `session_log:250` |
| `quests` ≡`quest` ⇄`menu:journal` | T2 摆场 |  | ✅ | ← 同 `menu:journal`〔open_questlog〕 |
| `role` | T1 自动 |  | ✅ | `session_log:721` |
| `silo` ≡`hay` | T1 自动 |  | ✅ | `session_log:13` |
| `status` | T1 自动 |  | ✅ | `session_log:705` |
| `storage` | T1 自动 |  | ✅ | `session_log:623` |
| `worn` | T1 自动 |  | ✅ | `session_log:466` |
| `任务` ≡`quest` ⇄`menu:journal` | T2 摆场 |  | ✅ | ← 同 `menu:journal`〔open_questlog〕 |
| `周围` ≡`look` | T1 自动 |  | ✅ | ← 同 `check:look`〔look_around〕 |
| `存储` ≡`storage` | T1 自动 |  | ✅ | ← 同 `check:storage`〔storage_layout〕 |
| `我是谁` ≡`role` | T1 自动 |  | ✅ | ← 同 `check:role`〔which_role〕 |
| `技能` ≡`profile` | T1 自动 |  | ✅ | ← 同 `check:profile`〔profile〕 |
| `环视` ≡`look` | T1 自动 |  | ✅ | ← 同 `check:look`〔look_around〕 |
| `端口` ≡`role` | T1 自动 |  | ✅ | ← 同 `check:role`〔which_role〕 |
| `箱子` ≡`chests` | T1 自动 |  | ✅ | ← 同 `check:chests`〔scan_chests〕 |
| `精通` ≡`mastery` | T1 自动 |  | ✅ | ← 同 `check:mastery`〔mastery_status〕 |
| `职业` ≡`profile` | T1 自动 |  | ✅ | ← 同 `check:profile`〔profile〕 |
| `职业分支` ≡`profile` | T1 自动 |  | ✅ | ← 同 `check:profile`〔profile〕 |
| `角色` ≡`role` | T1 自动 |  | ✅ | ← 同 `check:role`〔which_role〕 |

## `farm`（77）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `animals` | T2 摆场 |  |  |  |
| `break` ⇄`cabin:break` | T3 副作用 | radius, steps | ✅ | `session_log:289` |
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
| `place` ⇄`cabin:place` | T3 副作用 | radius, steps | ✅ | `session_log:291` |
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
| `statue` ⇄`cabin:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |
| `till` | T2 摆场 | direction, length, rows | ✅ | `session_log:708` |
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
| `拆` ≡`break` ⇄`cabin:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `挤奶` ≡`milk` | T2 摆场 |  |  |  |
| `摸动物` ≡`animals` | T2 摆场 |  |  |  |
| `摸摸` ≡`pet` | T3 副作用 |  |  |  |
| `摸猫狗` ≡`pet` | T3 副作用 |  |  |  |
| `播种规划` ≡`plantlayout` | T2 摆场 |  |  |  |
| `收` ≡`harvest` | T2 摆场 |  |  |  |
| `收放` ≡`building` | T2 摆场 |  |  |  |
| `放` ≡`place` ⇄`cabin:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `放牧` ≡`petwalk` | T3 副作用 |  |  |  |
| `放置` ≡`place` ⇄`cabin:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `放鱼` ≡`pond_add` | T2 摆场 |  |  |  |
| `敲` ≡`break` ⇄`cabin:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `方形规划` ≡`plan` | T2 摆场 |  |  |  |
| `机器` ≡`collect` | T2 摆场 |  |  |  |
| `浇` ≡`water` | T2 摆场 |  |  |  |
| `清` ≡`clear` | T2 摆场 |  |  |  |
| `清格` ≡`clearground` | T2 摆场 |  |  |  |
| `畜舍` | T2 摆场 |  |  |  |
| `砍树` ≡`chop` | T2 摆场 |  |  |  |
| `碗` ≡`喂水` | T3 副作用 |  |  |  |
| `祈福` ≡`statue` ⇄`cabin:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |
| `蓄力锄` ≡`tillfield` | T2 摆场 |  |  |  |
| `规划` ≡`plot` | T2 摆场 |  | ✅ | ← 同 `farm:plot`〔plot_plan〕 |
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
| `bomb_plan` | T1 自动 | min_covered, radius, top | ✅ | `session_log:467` |
| `bomb_retreat` | T2 摆场 |  |  |  |
| `bomb_status` | T1 自动 |  | ✅ | `session_log:468` |
| `bomb_volcano` | T3 副作用 | bomb, hp_threshold, max_minutes, min_covered, poll | ✅ | `session_log:590` |
| `farm` | T2 摆场 |  |  |  |
| `go` ≡`farm` | T2 摆场 | cycles, food_hp, food_sta, hp_threshold, mode, ore, resume, start, target |  |  |
| `organize` | T2 摆场 | disable, reset |  |  |
| `progress` ⇄`check:mine` | T1 自动 |  | ✅ | `session_log:469` |
| `rush` ≡`farm` | T2 摆场 |  |  |  |
| `去` ≡`farm` | T2 摆场 |  |  |  |
| `整理背包` ≡`organize` | T2 摆场 |  |  |  |
| `进度` ≡`progress` ⇄`check:mine` | T1 自动 |  | ✅ | ← 同 `mine:progress`〔check_mine_progress〕 |

## `cabin`（27）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `break` ⇄`farm:break` | T3 副作用 | radius, steps | ✅ | `session_log:287` |
| `collect` | T2 摆场 |  | ✅ | `session_log:628` |
| `cook` | T3 副作用 | recipe_name | ✅ | `session_log:445` |
| `enum` | T2 摆场 |  | ✅ | `session_log:627` |
| `furniture` ⇄`scene:furniture` | T1 自动 |  | ✅ | `session_log:630` |
| `interact` ⇄`scene:at` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:631` |
| `pickup` ⇄`scene:pickup` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:281` |
| `place` ⇄`farm:place` | T3 副作用 |  | ✅ | `session_log:290` |
| `sleep` ⇄`daily:sleep` | T3 副作用 | who | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | `session_log:629` |
| `做饭` ≡`cook` | T3 副作用 |  | ✅ | ← 同 `cabin:cook`〔cook〕 |
| `家具` ≡`furniture` ⇄`scene:furniture` | T1 自动 |  | ✅ | ← 同 `cabin:furniture`〔scan_furniture〕 |
| `引导` ≡`enum` | T2 摆场 |  | ✅ | ← 同 `cabin:enum`〔_cabin_enum〕 |
| `拆` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `拿` ≡`pickup` ⇄`scene:pickup` | T2 摆场 |  | ✅ | ← 同 `cabin:pickup`〔furniture_pickup〕 |
| `摆` ≡`pickup` ⇄`scene:pickup` | T2 摆场 |  | ✅ | ← 同 `cabin:pickup`〔furniture_pickup〕 |
| `收` ≡`collect` | T2 摆场 |  | ✅ | ← 同 `cabin:collect`〔_cabin_collect〕 |
| `放` ≡`place` ⇄`farm:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `放置` ≡`place` ⇄`farm:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `敲` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `机器` ≡`collect` | T2 摆场 |  | ✅ | ← 同 `cabin:collect`〔_cabin_collect〕 |
| `点` ≡`interact` ⇄`scene:at` | T2 摆场 |  | ✅ | ← 同 `cabin:interact`〔interact_at〕 |
| `看` ≡`enum` | T2 摆场 |  | ✅ | ← 同 `cabin:enum`〔_cabin_enum〕 |
| `睡` ≡`sleep` ⇄`daily:sleep` | T3 副作用 |  | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `睡觉` ≡`sleep` ⇄`daily:sleep` | T3 副作用 |  | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `祈福` ≡`statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |
| `雕像` ≡`statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |

## `social`（17）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `chat` | T2 摆场 |  |  |  |
| `emote` | T3 副作用 |  |  |  |
| `friendship` | T1 自动 |  | ✅ | `session_log:471` |
| `gift` | T3 副作用 | item_name, npc_name |  |  |
| `give` | T3 副作用 | item_name, player_name |  |  |
| `hand` | T3 副作用 | item_name, player_name |  |  |
| `movie` | T2 摆场 | npc |  |  |
| `send` | T3 副作用 | message | ✅ | `session_log:698` |
| `snack` | T2 摆场 |  |  |  |
| `发` ≡`send` | T3 副作用 |  | ✅ | ← 同 `social:send`〔send_chat〕 |
| `好感` ≡`friendship` | T1 自动 |  | ✅ | ← 同 `social:friendship`〔check_friendship〕 |
| `搭话` ≡`chat` | T2 摆场 |  |  |  |
| `电影` ≡`movie` | T2 摆场 |  |  |  |
| `给` ≡`give` | T3 副作用 |  |  |  |
| `送礼` ≡`gift` | T3 副作用 |  |  |  |
| `递给` ≡`hand` | T3 副作用 |  |  |  |
| `零食` ≡`snack` | T2 摆场 |  |  |  |

## `scene`（70）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `at` ⇄`cabin:interact` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:429` |
| `berry` | T3 副作用 |  |  |  |
| `break` ⇄`farm:break` | T3 副作用 | radius, steps | ✅ | `session_log:288` |
| `drop` | T3 副作用 |  | ✅ | `session_log:501` |
| `face` | T2 摆场 | direction | ✅ | `session_log:310` |
| `forge_help` | T2 摆场 |  | ✅ | `session_log:312` |
| `front` | T2 摆场 |  | ✅ | `session_log:636` |
| `furniture` ⇄`cabin:furniture` | T1 自动 |  | ✅ | `session_log:601` |
| `garbage` | T2 摆场 | dry_run, loc, pos, wait | ✅ | `session_log:321` |
| `interact` ≡`front` | T2 摆场 |  | ✅ | `session_log:392` |
| `maze` | T2 摆场 | gx, gy, radius | ✅ | `session_log:711` |
| `maze_seg` | T2 摆场 | gx, gy, radius | ✅ | `session_log:313` |
| `maze_walk` ⇄`map:walk_multi` | T2 摆场 | location, max_seg, max_wait, waypoints | ✅ | `session_log:715` |
| `moss` | T3 副作用 | dry_run, radius, rounds, target_max |  |  |
| `pan` | T2 摆场 | dry_run, radius, timeout |  |  |
| `pickup` ⇄`cabin:pickup` | T2 摆场 | tile_x, tile_y | ✅ | ← 同 `cabin:pickup`〔furniture_pickup〕 |
| `pickup_scene` | T3 副作用 | max_items | ✅ | `session_log:502` |
| `place` ⇄`farm:place` | T3 副作用 |  | ⏭ 没条件测 | `session_log:307` 要一块能放的地皮 + 可放置物 |
| `rock` | T3 副作用 | break_stone, dig, max_break, radius |  |  |
| `rummage` ≡`garbage` | T2 摆场 |  | ✅ | ← 同 `scene:garbage`〔trash_run〕 |
| `seats` | T1 自动 | radius | ✅ | `session_log:600` |
| `select` | T3 副作用 |  | ✅ | `session_log:295` |
| `sit` | T2 摆场 | face | ✅ | `session_log:646` |
| `spot` | T3 副作用 |  |  |  |
| `stand` | T2 摆场 |  | ✅ | `session_log:647` |
| `use` | T3 副作用 |  | ✅ | `session_log:322` |
| `丢` ≡`drop` | T3 副作用 |  | ✅ | ← 同 `scene:drop`〔drop_item〕 |
| `分段` ≡`maze_seg` | T2 摆场 |  | ✅ | ← 同 `scene:maze_seg`〔_maze_seg_view〕 |
| `可坐` ≡`seats` | T1 自动 |  | ✅ | ← 同 `scene:seats`〔seats〕 |
| `坐` ≡`sit` | T2 摆场 |  | ✅ | ← 同 `scene:sit`〔sit〕 |
| `坐下` ≡`sit` | T2 摆场 |  | ✅ | ← 同 `scene:sit`〔sit〕 |
| `家具` ≡`furniture` ⇄`cabin:furniture` | T1 自动 |  | ✅ | ← 同 `cabin:furniture`〔scan_furniture〕 |
| `座位` ≡`seats` | T1 自动 |  | ✅ | ← 同 `scene:seats`〔seats〕 |
| `拆` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `拾起` ≡`pickup` ⇄`cabin:pickup` | T2 摆场 |  | ✅ | ← 同 `cabin:pickup`〔furniture_pickup〕 |
| `拿` ≡`select` | T3 副作用 |  | ✅ | ← 同 `scene:select`〔select_item〕 |
| `挖斑点` ≡`spot` | T3 副作用 |  |  |  |
| `挖石` ≡`rock` | T3 副作用 |  |  |  |
| `挖矿点` ≡`rock` | T3 副作用 |  |  |  |
| `挖蚯蚓` ≡`spot` | T3 副作用 |  |  |  |
| `挥` ≡`use` | T3 副作用 |  | ✅ | ← 同 `scene:use`〔use_tool〕 |
| `捡物` ≡`pickup_scene` | T3 副作用 |  | ✅ | ← 同 `scene:pickup_scene`〔pickup_scene〕 |
| `捡采集` ≡`pickup_scene` | T3 副作用 |  | ✅ | ← 同 `scene:pickup_scene`〔pickup_scene〕 |
| `搜刮苔藓` ≡`moss` | T3 副作用 |  |  |  |
| `摇树莓` ≡`berry` | T3 副作用 |  |  |  |
| `放` ≡`place` ⇄`farm:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `放置` ≡`place` ⇄`farm:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `敲` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `敲击` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `敲石` ≡`rock` | T3 副作用 |  |  |  |
| `浆果` ≡`berry` | T3 副作用 |  |  |  |
| `淘` ≡`pan` | T2 摆场 |  |  |  |
| `淘盘` ≡`pan` | T2 摆场 |  |  |  |
| `淘金` ≡`pan` | T2 摆场 |  |  |  |
| `点` ≡`at` ⇄`cabin:interact` | T2 摆场 |  | ✅ | ← 同 `cabin:interact`〔interact_at〕 |
| `站起` ≡`stand` | T2 摆场 |  | ✅ | ← 同 `scene:stand`〔stand〕 |
| `站起来` ≡`stand` | T2 摆场 |  | ✅ | ← 同 `scene:stand`〔stand〕 |
| `绿雨` ≡`moss` | T3 副作用 |  |  |  |
| `翻垃圾桶` ≡`garbage` | T2 摆场 |  | ✅ | ← 同 `scene:garbage`〔trash_run〕 |
| `翻桶` ≡`garbage` | T2 摆场 |  | ✅ | ← 同 `scene:garbage`〔trash_run〕 |
| `走迷宫` ≡`maze_walk` ⇄`map:walk_multi` | T2 摆场 |  | ✅ | ← 同 `map:walk_multi`〔_maze_walk〕 |
| `起身` ≡`stand` | T2 摆场 |  | ✅ | ← 同 `scene:stand`〔stand〕 |
| `转身` ≡`face` | T2 摆场 |  | ✅ | ← 同 `scene:face`〔face〕 |
| `迷宫` ≡`maze` | T2 摆场 |  | ✅ | ← 同 `scene:maze`〔_maze_view〕 |
| `迷宫走` ≡`maze_walk` ⇄`map:walk_multi` | T2 摆场 |  | ✅ | ← 同 `map:walk_multi`〔_maze_walk〕 |
| `迷宫链` ≡`maze_seg` | T2 摆场 |  | ✅ | ← 同 `scene:maze_seg`〔_maze_seg_view〕 |
| `采矿点` ≡`rock` | T3 副作用 |  |  |  |
| `铜锅` ≡`pan` | T2 摆场 |  |  |  |
| `锻造帮助` ≡`forge_help` | T2 摆场 |  | ✅ | ← 同 `scene:forge_help`〔_lambda_scene_932ae5f5〕 |
| `面前` ≡`front` | T2 摆场 |  | ✅ | ← 同 `scene:front`〔interact〕 |

## `menu`（70）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `advance` | T2 摆场 |  | ⏭ 没条件测 | `session_log:544` 要一段正在播的剧情/对话（跟 NPC 说话 / 进事件） |
| `bin` | T3 副作用 | sell_all | ✅ | `session_log:657` |
| `bundle` | T1 自动 | area | ✅ | `session_log:602` |
| `bundle_kb` | T1 自动 | query | ✅ | `session_log:603` |
| `cancel` | T2 摆场 |  | ✅ | `session_log:707` |
| `click` | T3 副作用 | action, button, category, option, quantity, real, right, slot | ✅ | `session_log:685` |
| `craft` | T3 副作用 | item_name | ✅ | `session_log:548` |
| `craftables` | T1 自动 |  | ✅ | `session_log:606` |
| `customize` ⇄`settings:customize` | T3 副作用 | farmname, favorite | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `display_fill` ⇄`festival:display_fill` | T3 副作用 | items |  |  |
| `display_takeback` ⇄`festival:display_takeback` | T3 副作用 |  |  |  |
| `donate` | T3 副作用 |  | ⏭ 没条件测 | `session_log:567` 要一座还真缺东西的博物馆（当前 95/95 全齐，没东西可捐）；✅ 但「满馆调用无副作用」已真机验收（不删展品/不吃背包物） |
| `forge` | T3 副作用 | item1, item2, mode, target | ⏭ 没条件测 | `session_log:577` 要背包里有火山晶石（Cinder Shard）才能走完合成；✅ 但「有晶石时完整跑通」已真机验收（产出 Combined Ring），「缺料拒绝」本次也验了（报 20 ✓、戒指原样退回） |
| `geode` | T3 副作用 |  | ✅ | `session_log:555` |
| `geodes` | T3 副作用 |  | ✅ | `session_log:556` |
| `journal` ⇄`check:quest` | T2 摆场 |  | ✅ | `session_log:690` |
| `key` | T2 摆场 | hold, key | ✅ | `session_log:278` |
| `know` | T2 摆场 |  | ✅ | `session_log:510` |
| `levelup_choose` | T3 副作用 | profession, side |  |  |
| `minigame` | T2 摆场 | action |  |  |
| `minigame_state` | T2 摆场 |  | ⏭ 没条件测 | `session_log:545` 要赌场小游戏（CalicoJack/Slots，得先进沙漠赌场） |
| `number` | T2 摆场 | confirm | ⏭ 没条件测 | `session_log:546` 要弹着数量框（节庆兑换台 / 转盘押注） |
| `read` | T2 摆场 |  | ✅ | `session_log:697` |
| `read_book` | T3 副作用 |  | ✅ | `session_log:549` |
| `recipes` | T1 自动 |  | ✅ | `session_log:605` |
| `sell` | T3 副作用 |  |  |  |
| `shop` | T2 摆场 | place, want |  |  |
| `任务知` ≡`know` | T2 摆场 |  | ✅ | ← 同 `menu:know`〔calendar_data.special_orders_available〕 |
| `关` ≡`cancel` | T2 摆场 |  | ✅ | ← 同 `menu:cancel`〔cancel〕 |
| `出货` ≡`bin` | T3 副作用 |  | ✅ | ← 同 `menu:bin`〔sell_to_bin〕 |
| `分支` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `剧情` ≡`advance` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `menu:advance`〔advance_story〕 |
| `卖` ≡`sell` | T3 副作用 |  |  |  |
| `取消` ≡`cancel` | T2 摆场 |  | ✅ | ← 同 `menu:cancel`〔cancel〕 |
| `合成` ≡`craft` | T3 副作用 |  | ✅ | ← 同 `menu:craft`〔craft〕 |
| `填槽` ≡`display_fill` ⇄`festival:display_fill` | T3 副作用 |  |  |  |
| `小游戏` ≡`minigame` | T2 摆场 |  |  |  |
| `小游戏状态` ≡`minigame_state` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `menu:minigame_state`〔minigame_state〕 |
| `开日志` ≡`journal` ⇄`check:quest` | T2 摆场 |  | ✅ | ← 同 `menu:journal`〔open_questlog〕 |
| `按键` ≡`key` | T2 摆场 |  | ✅ | ← 同 `menu:key`〔press_key〕 |
| `捏人` ≡`customize` ⇄`settings:customize` | T3 副作用 |  | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `捐` ≡`donate` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `menu:donate`〔museum_donate〕 |
| `捐赠` ≡`donate` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `menu:donate`〔museum_donate〕 |
| `推进` ≡`advance` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `menu:advance`〔advance_story〕 |
| `收` ≡`display_takeback` ⇄`festival:display_takeback` | T3 副作用 |  |  |  |
| `收好` ≡`display_takeback` ⇄`festival:display_takeback` | T3 副作用 |  |  |  |
| `放满` ≡`display_fill` ⇄`festival:display_fill` | T3 副作用 |  |  |  |
| `数量` ≡`number` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `menu:number`〔number_select〕 |
| `数量框` ≡`number` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `menu:number`〔number_select〕 |
| `日志` ≡`journal` ⇄`check:quest` | T2 摆场 |  | ✅ | ← 同 `menu:journal`〔open_questlog〕 |
| `晶球` ≡`geode` | T3 副作用 |  | ✅ | ← 同 `menu:geode`〔process_geode〕 |
| `点` ≡`click` | T3 副作用 |  | ✅ | ← 同 `menu:click`〔menu_click〕 |
| `献祭` ≡`bundle` | T1 自动 |  | ✅ | ← 同 `menu:bundle`〔bundle_status〕 |
| `献祭知识` ≡`bundle_kb` | T1 自动 |  | ✅ | ← 同 `menu:bundle_kb`〔bundle_kb〕 |
| `看` ≡`read` | T2 摆场 |  | ✅ | ← 同 `menu:read`〔read_menu〕 |
| `知` ≡`know` | T2 摆场 |  | ✅ | ← 同 `menu:know`〔calendar_data.special_orders_available〕 |
| `知识库` ≡`bundle_kb` | T1 自动 |  | ✅ | ← 同 `menu:bundle_kb`〔bundle_kb〕 |
| `职业选` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `菜谱` ≡`recipes` | T1 自动 |  | ✅ | ← 同 `menu:recipes`〔list_recipes〕 |
| `订单知` ≡`know` | T2 摆场 |  | ✅ | ← 同 `menu:know`〔calendar_data.special_orders_available〕 |
| `读书` ≡`read_book` | T3 副作用 |  | ✅ | ← 同 `menu:read_book`〔read_book〕 |
| `读技能书` ≡`read_book` | T3 副作用 |  | ✅ | ← 同 `menu:read_book`〔read_book〕 |
| `读物品` ≡`read_book` | T3 副作用 |  | ✅ | ← 同 `menu:read_book`〔read_book〕 |
| `读纸条` ≡`read_book` | T3 副作用 |  | ✅ | ← 同 `menu:read_book`〔read_book〕 |
| `赌场` ≡`minigame` | T2 摆场 |  |  |  |
| `起名` ≡`customize` ⇄`settings:customize` | T3 副作用 |  | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `选职业` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `逛店` ≡`shop` | T2 摆场 |  |  |  |
| `配方` ≡`craftables` | T1 自动 |  | ✅ | ← 同 `menu:craftables`〔list_craftables〕 |
| `锻造` ≡`forge` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `menu:forge`〔forge〕 |

## `storage`（20）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `default` | T3 副作用 |  | ✅ | `session_log:263` |
| `find` | T1 自动 |  | ✅ | `session_log:604` |
| `store` | T3 副作用 | all, items, keepTools, target | ✅ | `session_log:644` |
| `tag` | T3 副作用 | target | ✅ | `session_log:258` |
| `take` | T3 副作用 | items | ✅ | `session_log:643` |
| `view` | T1 自动 | box | ✅ | `session_log:598` |
| `取` ≡`take` | T3 副作用 |  | ✅ | ← 同 `storage:take`〔storage_take〕 |
| `堆` ≡`store` | T3 副作用 |  | ✅ | ← 同 `storage:store`〔storage_store〕 |
| `多取` ≡`take` | T3 副作用 |  | ✅ | ← 同 `storage:take`〔storage_take〕 |
| `存` ≡`store` | T3 副作用 |  | ✅ | ← 同 `storage:store`〔storage_store〕 |
| `存智能` ≡`store` | T3 副作用 |  | ✅ | ← 同 `storage:store`〔storage_store〕 |
| `扫` ≡`view` | T1 自动 |  | ✅ | ← 同 `storage:view`〔storage_view〕 |
| `批量取` ≡`take` | T3 副作用 |  | ✅ | ← 同 `storage:take`〔storage_take〕 |
| `找` ≡`find` | T1 自动 |  | ✅ | ← 同 `storage:find`〔storage_find〕 |
| `搜` ≡`find` | T1 自动 |  | ✅ | ← 同 `storage:find`〔storage_find〕 |
| `查` ≡`find` | T1 自动 |  | ✅ | ← 同 `storage:find`〔storage_find〕 |
| `标记` ≡`tag` | T3 副作用 |  | ✅ | ← 同 `storage:tag`〔storage_tag〕 |
| `看` ≡`view` | T1 自动 |  | ✅ | ← 同 `storage:view`〔storage_view〕 |
| `看箱` ≡`view` | T1 自动 |  | ✅ | ← 同 `storage:view`〔storage_view〕 |
| `默认` ≡`default` | T3 副作用 |  | ✅ | ← 同 `storage:default`〔storage_default〕 |

## `daily`（30）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `appearance` ⇄`settings:appearance` | T3 副作用 | acc, eye_color, hair, hair_color, hat, pants, pants_color, shirt, skin | ⏭ 没条件测 | ← 同 `settings:appearance`〔set_appearance〕 |
| `eat` | T3 副作用 | item_name | ✅ | `session_log:340` |
| `heartbeat` | T3 副作用 | minutes | ✅ | `session_log:692` |
| `lie_bed` | T3 副作用 | who |  |  |
| `pause` | T3 副作用 | out_of_focus | ✅ | `session_log:342` |
| `peek` | T2 摆场 |  | ✅ | `session_log:706` |
| `settle` | T3 副作用 |  | ⏭ 没条件测 | `session_log:687` 要正在弹着的过夜结算菜单（且停在汇总页） |
| `sleep` ⇄`cabin:sleep` | T3 副作用 | who | ✅ | `session_log:659` |
| `wb_clear` | T3 副作用 |  | ✅ | `session_log:641` |
| `wb_pin` | T3 副作用 | content | ✅ | `session_log:331` |
| `wb_read` | T3 副作用 |  | ✅ | `session_log:642` |
| `wear` | T3 副作用 | hand, slot | ✅ | `session_log:351` |
| `whiteboard` | T3 副作用 | content | ✅ | `session_log:639` |
| `写白板` ≡`whiteboard` | T3 副作用 |  | ✅ | ← 同 `daily:whiteboard`〔whiteboard_write〕 |
| `吃` ≡`eat` | T3 副作用 |  | ✅ | ← 同 `daily:eat`〔eat_item〕 |
| `心跳` ≡`heartbeat` | T3 副作用 |  | ✅ | ← 同 `daily:heartbeat`〔set_heartbeat_interval〕 |
| `捏脸` ≡`appearance` ⇄`settings:appearance` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `settings:appearance`〔set_appearance〕 |
| `暂停` ≡`pause` | T3 副作用 |  | ✅ | ← 同 `daily:pause`〔set_pause〕 |
| `清白板` ≡`wb_clear` | T3 副作用 |  | ✅ | ← 同 `daily:wb_clear`〔whiteboard_clear〕 |
| `白板` ≡`whiteboard` | T3 副作用 |  | ✅ | ← 同 `daily:whiteboard`〔whiteboard_write〕 |
| `看恒` ≡`peek` | T2 摆场 |  | ✅ | ← 同 `daily:peek`〔peek_player〕 |
| `看白板` ≡`wb_read` | T3 副作用 |  | ✅ | ← 同 `daily:wb_read`〔whiteboard_read〕 |
| `睡` ≡`sleep` ⇄`cabin:sleep` | T3 副作用 |  | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `穿` ≡`wear` | T3 副作用 |  | ✅ | ← 同 `daily:wear`〔wear〕 |
| `穿戴` ≡`wear` | T3 副作用 |  | ✅ | ← 同 `daily:wear`〔wear〕 |
| `结算` ≡`settle` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `daily:settle`〔confirm_settlement〕 |
| `脱` ≡`wear` | T3 副作用 |  | ✅ | ← 同 `daily:wear`〔wear〕 |
| `躺` ≡`lie_bed` | T3 副作用 |  |  |  |
| `躺床` ≡`lie_bed` | T3 副作用 |  |  |  |
| `钉白板` ≡`wb_pin` | T3 副作用 |  | ✅ | ← 同 `daily:wb_pin`〔whiteboard_pin〕 |

## `map`（17）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `go` | T2 摆场 | destination | ✅ | `session_log:710` |
| `lookup` | T1 自动 | location | ✅ | `session_log:610` |
| `npc` | T2 摆场 |  | ✅ | `session_log:650` |
| `query` | T1 自动 | function | ✅ | `session_log:611` |
| `unlocks` | T2 摆场 |  | ✅ | `session_log:582` |
| `walk` | T2 摆场 | poi_name | ✅ | `session_log:651` |
| `walk_multi` ⇄`scene:maze_walk` | T2 摆场 | location, max_seg, max_wait, waypoints | ✅ | `session_log:720` |
| `warp_safe` | T2 摆场 |  | ✅ | `session_log:656` |
| `反查` ≡`query` | T1 自动 |  | ✅ | ← 同 `map:query`〔map_query〕 |
| `多段走` ≡`walk_multi` ⇄`scene:maze_walk` | T2 摆场 |  | ✅ | ← 同 `map:walk_multi`〔_maze_walk〕 |
| `找人` ≡`npc` | T2 摆场 |  | ✅ | ← 同 `map:npc`〔find_npc〕 |
| `查` ≡`lookup` | T1 自动 |  | ✅ | ← 同 `map:lookup`〔map_lookup〕 |
| `解锁` ≡`unlocks` | T2 摆场 |  | ✅ | ← 同 `map:unlocks`〔map_unlocks〕 |
| `走` ≡`go` | T2 摆场 |  | ✅ | ← 同 `map:go`〔map_go〕 |
| `走到` ≡`walk` | T2 摆场 |  | ✅ | ← 同 `map:walk`〔walk_to〕 |
| `逃脱` ≡`warp_safe` | T2 摆场 |  | ✅ | ← 同 `map:warp_safe`〔warp_safe〕 |
| `闲逛` ≡`walk_multi` ⇄`scene:maze_walk` | T2 摆场 |  | ✅ | `session_log:717` |

## `festival`（55）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `answer` | ⏭ 当期不可用 | answer |  |  |
| `dance` | ⏭ 当期不可用 | target |  |  |
| `display_fill` ⇄`menu:display_fill` | ⏭ 当期不可用 | items |  |  |
| `display_takeback` ⇄`menu:display_takeback` | ⏭ 当期不可用 |  |  |  |
| `egg_note` | ⏭ 当期不可用 | route |  |  |
| `egg_run` | ⏭ 当期不可用 | route |  |  |
| `eggs` | ⏭ 当期不可用 |  |  |  |
| `go` | ⏭ 当期不可用 |  |  |  |
| `help` | T1 自动 |  | ✅ | `session_log:618` |
| `ice_fish` | ⏭ 当期不可用 |  |  |  |
| `info` | T1 自动 |  | ✅ | `session_log:483` |
| `interact` | ⏭ 当期不可用 |  |  |  |
| `maze` | ⏭ 当期不可用 |  |  |  |
| `maze_walk` ⇄`scene:maze_walk` | ⏭ 当期不可用 | location, max_seg, max_wait, waypoints |  |  |
| `next` | T1 自动 |  | ✅ | `session_log:617` |
| `poi` | ⏭ 当期不可用 |  |  |  |
| `prep` | ⏭ 当期不可用 |  | ✅ | `session_log:619` |
| `shop` | ⏭ 当期不可用 |  |  |  |
| `strength` | ⏭ 当期不可用 | delay |  |  |
| `today` | T1 自动 |  | ✅ | `session_log:615` |
| `下一个` ≡`next` | T1 自动 |  | ✅ | ← 同 `festival:next`〔_festival_next〕 |
| `互动` ≡`interact` | ⏭ 当期不可用 |  |  |  |
| `今天` ≡`today` | T1 自动 |  | ✅ | ← 同 `festival:today`〔_festival_today〕 |
| `冰` ≡`ice_fish` | ⏭ 当期不可用 |  |  |  |
| `冰钓` ≡`ice_fish` | ⏭ 当期不可用 |  |  |  |
| `准备` ≡`prep` | T1 自动 |  | ✅ | ← 同 `festival:prep`〔_festival_prep〕 |
| `力量` ≡`strength` | ⏭ 当期不可用 |  |  |  |
| `厨师` ≡`poi` | ⏭ 当期不可用 |  |  |  |
| `去` ≡`go` | ⏭ 当期不可用 |  |  |  |
| `取回` ≡`display_takeback` ⇄`menu:display_takeback` | T1 自动 |  |  |  |
| `商店` ≡`shop` | ⏭ 当期不可用 |  |  |  |
| `备战` ≡`prep` | ⏭ 当期不可用 |  |  |  |
| `实况` ≡`info` | ⏭ 当期不可用 |  |  |  |
| `展位收` ≡`display_takeback` ⇄`menu:display_takeback` | ⏭ 当期不可用 |  |  |  |
| `展位放` ≡`display_fill` ⇄`menu:display_fill` | ⏭ 当期不可用 |  |  |  |
| `应答` ≡`answer` | ⏭ 当期不可用 |  |  |  |
| `引导` ≡`help` | T1 自动 |  | ✅ | ← 同 `festival:help`〔_festival_help〕 |
| `找蛋` ≡`eggs` | ⏭ 当期不可用 |  |  |  |
| `捡` ≡`egg_run` | T1 自动 |  |  |  |
| `捡蛋` ≡`egg_run` | ⏭ 当期不可用 |  |  |  |
| `收` ≡`display_takeback` ⇄`menu:display_takeback` | T1 自动 |  |  |  |
| `收好` ≡`display_takeback` ⇄`menu:display_takeback` | ⏭ 当期不可用 |  |  |  |
| `放满` ≡`display_fill` ⇄`menu:display_fill` | ⏭ 当期不可用 |  |  |  |
| `测力` ≡`strength` | ⏭ 当期不可用 |  |  |  |
| `玩法` ≡`help` | T1 自动 |  | ✅ | ← 同 `festival:help`〔_festival_help〕 |
| `纸条` ≡`egg_note` | ⏭ 当期不可用 |  |  |  |
| `舞` ≡`dance` | ⏭ 当期不可用 |  |  |  |
| `蛋` ≡`eggs` | T1 自动 |  |  |  |
| `记` ≡`egg_note` | ⏭ 当期不可用 |  |  |  |
| `走迷宫` ≡`maze_walk` ⇄`scene:maze_walk` | T1 自动 |  | ✅ | ← 同 `map:walk_multi`〔_maze_walk〕 |
| `跳舞` ≡`dance` | ⏭ 当期不可用 |  |  |  |
| `迷宫` ≡`maze` | ⏭ 当期不可用 |  |  |  |
| `迷宫走` ≡`maze_walk` ⇄`scene:maze_walk` | ⏭ 当期不可用 |  |  |  |
| `邀请` ≡`dance` | ⏭ 当期不可用 |  |  |  |
| `限定` ≡`poi` | ⏭ 当期不可用 |  |  |  |

## `fish`（28）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `bobber` | T2 摆场 | style | ✅ | `session_log:523` |
| `crab` | T1 自动 |  | ✅ | `session_log:613` |
| `crab_bait` | T2 摆场 | bait | ⏭ 没条件测 | `session_log:537` 要背包里有鱼饵（先去威利鱼店买 Bait） |
| `crab_collect` | T2 摆场 |  | ✅ | `session_log:539` |
| `crab_diag` | T2 摆场 | location | ✅ | `session_log:538` |
| `crab_place` | T2 摆场 | bait, radius | ✅ | `session_log:532` |
| `crab_retract` | T2 摆场 | location | ✅ | `session_log:540` |
| `crab_water` | T2 摆场 | radius | ✅ | `session_log:531` |
| `fish` | T2 摆场 |  |  |  |
| `go` ≡`fish` | T2 摆场 | location, max_casts, no_sleep |  |  |
| `info` | T1 自动 | location | ✅ | `session_log:616` |
| `rod` | T2 摆场 | action | ✅ | `session_log:614` |
| `spots` | T2 摆场 |  | ✅ | `session_log:511` |
| `回收笼` ≡`crab_retract` | T2 摆场 |  | ✅ | ← 同 `fish:crab_retract`〔_crab_retract〕 |
| `找水` ≡`crab_water` | T2 摆场 |  | ✅ | ← 同 `fish:crab_water`〔_crab_water_report〕 |
| `收笼` ≡`crab_collect` | T2 摆场 |  | ✅ | ← 同 `fish:crab_collect`〔_crab_collect〕 |
| `放笼` ≡`crab_place` | T2 摆场 |  | ✅ | ← 同 `fish:crab_place`〔_crab_place〕 |
| `放饵` ≡`crab_bait` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `fish:crab_bait`〔_crab_bait〕 |
| `查` ≡`info` | T1 自动 |  | ✅ | ← 同 `fish:info`〔_fish_info〕 |
| `样式` ≡`bobber` | T2 摆场 |  | ✅ | ← 同 `fish:bobber`〔bobber_style〕 |
| `浮漂` ≡`bobber` | T2 摆场 |  | ✅ | ← 同 `fish:bobber`〔bobber_style〕 |
| `能钓` ≡`info` | T1 自动 |  | ✅ | ← 同 `fish:info`〔_fish_info〕 |
| `蟹笼` ≡`crab` | T1 自动 |  | ✅ | ← 同 `fish:crab`〔_crab_status〕 |
| `诊断笼` ≡`crab_diag` | T2 摆场 |  | ✅ | ← 同 `fish:crab_diag`〔_crab_diag〕 |
| `钓` ≡`fish` | T2 摆场 |  |  |  |
| `钓点` ≡`spots` | T2 摆场 |  | ✅ | ← 同 `fish:spots`〔_fish_all_spots〕 |
| `鱼` ≡`info` | T1 自动 |  | ✅ | ← 同 `fish:info`〔_fish_info〕 |
| `鱼竿` ≡`rod` | T2 摆场 |  | ✅ | ← 同 `fish:rod`〔_rod_cmd〕 |

## `settings`（31）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `appearance` ⇄`daily:appearance` | T3 副作用 | acc, eye_color, hair_color, pants_color, skin | ⏭ 没条件测 | `session_log:268` 要捏脸菜单（创建角色 / 幻觉神龛） |
| `color` | T2 摆场 | hue | ✅ | `session_log:654` |
| `colorpreset` | T1 自动 |  | ✅ | `session_log:597` |
| `confirm_look` | T2 摆场 |  | ✅ | `session_log:645` |
| `customize` ⇄`menu:customize` | T3 副作用 | farmname, favorite | ✅ | `session_log:270` |
| `hair` | T1 自动 |  | ✅ | `session_log:593` |
| `hat` | T1 自动 |  | ✅ | `session_log:596` |
| `pants` | T1 自动 |  | ✅ | `session_log:595` |
| `reactivate` | T3 副作用 | tool_name | ✅ | `session_log:499` |
| `retire` | T3 副作用 | tool_name | ✅ | `session_log:498` |
| `shirt` | T1 自动 |  | ✅ | `session_log:594` |
| `status` | T1 自动 |  | ✅ | `session_log:592` |
| `上衣` ≡`shirt` | T1 自动 |  | ✅ | ← 同 `settings:shirt`〔list_shirt_ref〕 |
| `发型` ≡`hair` | T1 自动 |  | ✅ | ← 同 `settings:hair`〔list_hair_ref〕 |
| `召回` ≡`reactivate` | T3 副作用 |  | ✅ | ← 同 `settings:reactivate`〔settings_reactivate〕 |
| `外观` ≡`appearance` ⇄`daily:appearance` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `settings:appearance`〔set_appearance〕 |
| `帽子` ≡`hat` | T1 自动 |  | ✅ | ← 同 `settings:hat`〔list_hats_ref〕 |
| `捏人` ≡`customize` ⇄`menu:customize` | T3 副作用 |  | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `捏脸` ≡`appearance` ⇄`daily:appearance` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `settings:appearance`〔set_appearance〕 |
| `改外观` ≡`appearance` ⇄`daily:appearance` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `settings:appearance`〔set_appearance〕 |
| `核对` ≡`confirm_look` | T2 摆场 |  | ✅ | ← 同 `settings:confirm_look`〔confirm_look〕 |
| `状态` ≡`status` | T1 自动 |  | ✅ | ← 同 `settings:status`〔settings_status〕 |
| `看` ≡`status` | T1 自动 |  | ✅ | ← 同 `settings:status`〔settings_status〕 |
| `确认形象` ≡`confirm_look` | T2 摆场 |  | ✅ | ← 同 `settings:confirm_look`〔confirm_look〕 |
| `确认捏脸` ≡`confirm_look` | T2 摆场 |  | ✅ | ← 同 `settings:confirm_look`〔confirm_look〕 |
| `裤子` ≡`pants` | T1 自动 |  | ✅ | ← 同 `settings:pants`〔list_pants_ref〕 |
| `设置状态` ≡`status` | T1 自动 |  | ✅ | ← 同 `settings:status`〔settings_status〕 |
| `调色` ≡`colorpreset` | T1 自动 |  | ✅ | ← 同 `settings:colorpreset`〔list_color_presets〕 |
| `起名` ≡`customize` ⇄`menu:customize` | T3 副作用 |  | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `退役` ≡`retire` | T3 副作用 |  | ✅ | ← 同 `settings:retire`〔settings_retire〕 |
| `颜色` ≡`color` | T2 摆场 |  | ✅ | ← 同 `settings:color`〔color_pick〕 |

## `script`（8）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `async` | T2 摆场 | add, enable, remove, show | ✅ | `session_log:648` |
| `continue` | T3 副作用 | job_id | ✅ | `session_log:508` |
| `stop` | T3 副作用 | job_id | ✅ | `session_log:655` |
| `停` ≡`stop` | T3 副作用 |  | ✅ | ← 同 `script:stop`〔_script_stop〕 |
| `异步` ≡`async` | T2 摆场 |  | ✅ | ← 同 `script:async`〔_script_async〕 |
| `白名单` ≡`async` | T2 摆场 |  | ✅ | ← 同 `script:async`〔_script_async〕 |
| `继续` ≡`continue` | T3 副作用 |  | ✅ | ← 同 `script:continue`〔_script_continue〕 |
| `自动` ≡`async` | T2 摆场 |  | ✅ | ← 同 `script:async`〔_script_async〕 |

## `session`（6）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `export` | T3 副作用 |  | ✅ | `session_log:248` |
| `set` | T2 摆场 | setting | ✅ | `session_log:653` |
| `status` | T1 自动 |  | ✅ | `session_log:609` |
| `导出` ≡`export` | T3 副作用 |  | ✅ | ← 同 `session:export`〔_session_exportop〕 |
| `改` ≡`set` | T2 摆场 |  | ✅ | ← 同 `session:set`〔_session_set〕 |
| `看` ≡`status` | T1 自动 |  | ✅ | ← 同 `session:status`〔_session_status〕 |

## ⚠️ 分层存疑（判成 T1，但目标函数会动角色）

> **别直接按 T1 无脑扫这些** —— 先人工确认归哪层。判据=目标函数源码里出现下列调用。

✅ 无。（只查了 `_MOVE_CALLS` 列的那几种调用 —— 这是**没发现**，不是**证明没有**。）

---

## 📐 图例

- **T1 自动**：只读/幂等，随时可跑（建议先全扫一遍）
- **T2 摆场**：要 AI 站在对的地方、或当前图/季节合适
- **T3 副作用**：会改状态/花资源/不可逆，**要恒在场点头**
- **⏭ 当期不可用**：节庆日专属/需解锁 —— **不算没测，是测不了**，别打勾
- **⇄跨域同函数**：同一个后端函数在别的域下也挂了门牌，**判定共享、别重复测**
- **← 同 `域:op`**：这行没单独测过，判定是从同函数的另一行走 `⇄` 传播来的

