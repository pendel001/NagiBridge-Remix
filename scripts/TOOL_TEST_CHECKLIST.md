# 🧪 全工具测试清单（2026-09-11 生成）

> **517 行 = 189 个后端函数**（抄自各域 dispatch，非文档）｜✅ 419 行 / **165 函数** ｜❌ 0 ｜⏭ 没条件测 34 行 / 9 函数｜**待测 15 函数**｜T1 自动 103 ｜T2 摆场 212 ｜T3 副作用 161 ｜⏭ 当期不可用 41

> 用法：跑测试（任何渠道，只要走 :8000 的 MCP）→ `python gen_tool_checklist.py --from-log` 自动打勾。
> 判定规则见脚本头部；**⚠️ op「」= 参数被静默丢掉 ⇒ 不算 ✅**（也不算 ❌ —— 那一下压根没执行，当"没有证据"跳过，本行沿用上一轮结论）；真报错才 ❌。
> ⚠️ 本表**测试前冻结**：`--from-log` 只写后两列，永不动 op 列表。
> 🖐️ **手写的 `⏭ 没条件测` 压得过日志的 ✅**（"参数对了/没报错"≠"真验过"）——想转绿要先人工清掉 ⏭；但它**压不过日志里的 ❌**（真失败必须红着露出来）。
> 🔁 **`⇄` = 跨域同函数**（同一个后端函数挂在别的域下，各占一行）：判定按函数传播，任一行绿了其余行跟着绿 —— 所以**别重复测**，看 `⇄` 挑一行测即可。


## `check`（36）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `backpack` | T1 自动 |  | ✅ | `session_log:1438` |
| `building` | T1 自动 |  | ✅ | `session_log:455` |
| `building_list` ≡`building` | T1 自动 |  | ✅ | ← 同 `check:building`〔building_list〕 |
| `buildings` ≡`building` | T1 自动 |  | ✅ | `session_log:15` |
| `chests` | T1 自动 | chest | ✅ | `session_log:1075` |
| `hay` | T1 自动 |  | ✅ | `session_log:457` |
| `look` | T1 自动 | radius | ✅ | `session_log:1247` |
| `machine` | T1 自动 |  | ✅ | `session_log:1074` |
| `machine_report` ≡`machine` | T1 自动 |  | ✅ | ← 同 `check:machine`〔machine_report〕 |
| `machines` ≡`machine` | T1 自动 |  | ✅ | `session_log:10` |
| `mastery` | T1 自动 |  | ✅ | `session_log:1167` |
| `mine` ⇄`mine:progress` | T1 自动 |  | ✅ | `session_log:461` |
| `mining` ≡`mine` ⇄`mine:progress` | T1 自动 |  | ✅ | ← 同 `mine:progress`〔check_mine_progress〕 |
| `profile` | T1 自动 |  | ✅ | `session_log:1125` |
| `quest` ⇄`menu:journal` | T2 摆场 |  | ✅ | `session_log:250` |
| `quests` ≡`quest` ⇄`menu:journal` | T2 摆场 |  | ✅ | ← 同 `menu:journal`〔open_questlog〕 |
| `ready` | T1 自动 |  | ✅ | `session_log:912` |
| `ready_state` ≡`ready` | T1 自动 |  | ✅ | ← 同 `check:ready`〔check_ready_state〕 |
| `role` | T1 自动 |  | ✅ | `session_log:1443` |
| `silo` ≡`hay` | T1 自动 |  | ✅ | `session_log:1364` |
| `status` | T1 自动 |  | ✅ | `session_log:1242` |
| `storage` | T1 自动 |  | ✅ | `session_log:623` |
| `worn` | T1 自动 |  | ✅ | `session_log:466` |
| `任务` ≡`quest` ⇄`menu:journal` | T2 摆场 |  | ✅ | ← 同 `menu:journal`〔open_questlog〕 |
| `周围` ≡`look` | T1 自动 |  | ✅ | ← 同 `check:look`〔look_around〕 |
| `存储` ≡`storage` | T1 自动 |  | ✅ | ← 同 `check:storage`〔storage_layout〕 |
| `就绪` ≡`ready` | T1 自动 |  | ✅ | ← 同 `check:ready`〔check_ready_state〕 |
| `我是谁` ≡`role` | T1 自动 |  | ✅ | ← 同 `check:role`〔which_role〕 |
| `技能` ≡`profile` | T1 自动 |  | ✅ | ← 同 `check:profile`〔profile〕 |
| `环视` ≡`look` | T1 自动 |  | ✅ | ← 同 `check:look`〔look_around〕 |
| `端口` ≡`role` | T1 自动 |  | ✅ | ← 同 `check:role`〔which_role〕 |
| `箱子` ≡`chests` | T1 自动 |  | ✅ | ← 同 `check:chests`〔scan_chests〕 |
| `精通` ≡`mastery` | T1 自动 |  | ✅ | ← 同 `check:mastery`〔mastery_status〕 |
| `职业` ≡`profile` | T1 自动 |  | ✅ | ← 同 `check:profile`〔profile〕 |
| `职业分支` ≡`profile` | T1 自动 |  | ✅ | ← 同 `check:profile`〔profile〕 |
| `角色` ≡`role` | T1 自动 |  | ✅ | ← 同 `check:role`〔which_role〕 |

## `farm`（76）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `animals` | T2 摆场 |  | ✅ | `session_log:1048` |
| `break` ⇄`cabin:break` | T3 副作用 | radius, steps | ✅ | `session_log:289` |
| `building` | T2 摆场 | location, machine_type | ✅ | `session_log:1063` |
| `buy` | T3 副作用 | animal_type, building |  |  |
| `chop` | T2 摆场 | area | ✅ | `session_log:1352` |
| `clear` | T2 摆场 | direction, layout, length, margin, radius, rows, x1,y1,x2,y2 | ✅ | `session_log:1353` |
| `clearground` | T2 摆场 |  | ✅ | `session_log:1182` |
| `collect` | T2 摆场 | location, machine_type | ✅ | `session_log:1354` |
| `doors` | T2 摆场 |  | ✅ | `session_log:1003` |
| `fertilize` | T2 摆场 | direction, fertilizer_name, length, rows | ✅ | `session_log:1344` |
| `harvest` | T2 摆场 | radius | ✅ | `session_log:1177` |
| `hay` | T2 摆场 | dry_run | ✅ | `session_log:1367` |
| `hoe` | T2 摆场 |  | ✅ | `session_log:1324` |
| `load` | T2 摆场 | location, machine_type | ✅ | `session_log:1097` |
| `milk` | T2 摆场 |  | ⏭ 没条件测 | `session_log:1186` 鸡舍/畜棚都装了**自动采集器**（工具如实报"产物已自动收集、不用挤奶"）⇒ 挤奶动作没被执行；需手动收产物的档 |
| `pet` | T3 副作用 |  | ✅ | `session_log:791` |
| `petwalk` | T3 副作用 | include_petted | ✅ | `session_log:1047` |
| `place` ⇄`cabin:place` | T3 副作用 | radius, steps | ✅ | `session_log:291` |
| `plan` | T2 摆场 | hoe_level, layout, trellis, x1, x2, y1, y2 | ✅ | `session_log:1346` |
| `plant` | T2 摆场 | direct, direction, layout, length, rows, seed_name, trellis, x1, x1,y1,x2,y2, x2, y1, y2 | ✅ | `session_log:1347` |
| `plantlayout` ≡`plant` | T2 摆场 |  | ✅ | `session_log:1327` |
| `plot` | T2 摆场 | all_plots, radius | ✅ | `session_log:1080` |
| `pond` | T2 摆场 |  | ✅ | `session_log:1049` |
| `pond_add` | T2 摆场 |  | ✅ | `session_log:1054` |
| `pond_collect` | T2 摆场 |  | ✅ | `session_log:1051` |
| `pond_feed` | T2 摆场 |  | ✅ | `session_log:1052` |
| `pond_fish` | T2 摆场 |  | ✅ | `session_log:1053` |
| `scythe` | T2 摆场 | radius | ✅ | `session_log:1035` |
| `shear` ≡`milk` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `farm:milk`〔milk_shear〕 |
| `sow` ≡`plant` | T2 摆场 |  | ✅ | ← 同 `farm:plant`〔_farm_plant〕 |
| `statue` ⇄`cabin:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |
| `till` ≡`hoe` | T2 摆场 | direction, layout, length, margin, radius, rows, x1, x1,y1,x2,y2, x2, y1, y2 | ✅ | `session_log:1350` |
| `tillfield` ≡`hoe` | T2 摆场 |  | ✅ | `session_log:1263` |
| `water` | T2 摆场 | radius | ✅ | `session_log:786` |
| `上料` ≡`load` | T2 摆场 |  | ✅ | ← 同 `farm:load`〔load_machines〕 |
| `买` ≡`buy` | T3 副作用 |  |  |  |
| `买动物` ≡`buy` | T3 副作用 |  |  |  |
| `关门` ≡`doors` | T2 摆场 |  | ✅ | ← 同 `farm:doors`〔close_doors〕 |
| `剪毛` ≡`milk` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `farm:milk`〔milk_shear〕 |
| `加草` ≡`hay` | T2 摆场 |  | ✅ | ← 同 `farm:hay`〔feed_hay〕 |
| `化肥` ≡`fertilize` | T2 摆场 |  | ✅ | ← 同 `farm:fertilize`〔apply_fertilizer〕 |
| `喂塘` ≡`pond_feed` | T2 摆场 |  | ✅ | ← 同 `farm:pond_feed`〔_pond_feed〕 |
| `喂水` | T3 副作用 |  | ✅ | `session_log:796` |
| `塘` ≡`pond` | T2 摆场 |  | ✅ | ← 同 `farm:pond`〔_pond_list〕 |
| `塘钓` ≡`pond_fish` | T2 摆场 |  | ✅ | ← 同 `farm:pond_fish`〔_pond_fish〕 |
| `宠物碗` ≡`喂水` | T3 副作用 |  | ✅ | ← 同 `farm:喂水`〔pet_water〕 |
| `布局锄` ≡`hoe` | T2 摆场 |  | ✅ | `session_log:1266` |
| `干草` ≡`hay` | T2 摆场 |  | ✅ | ← 同 `farm:hay`〔feed_hay〕 |
| `拆` ≡`break` ⇄`cabin:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `挤奶` ≡`milk` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `farm:milk`〔milk_shear〕 |
| `摸动物` ≡`animals` | T2 摆场 |  | ✅ | ← 同 `farm:animals`〔care_animals〕 |
| `摸摸` ≡`pet` | T3 副作用 |  | ✅ | ← 同 `farm:pet`〔pet_pet〕 |
| `摸猫狗` ≡`pet` | T3 副作用 |  | ✅ | ← 同 `farm:pet`〔pet_pet〕 |
| `播种规划` ≡`plant` | T2 摆场 |  | ✅ | ← 同 `farm:plant`〔_farm_plant〕 |
| `收` ≡`harvest` | T2 摆场 |  | ✅ | ← 同 `farm:harvest`〔harvest_crops〕 |
| `收放` ≡`building` | T2 摆场 |  | ✅ | ← 同 `farm:building`〔work_building〕 |
| `放` ≡`place` ⇄`cabin:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `放牧` ≡`petwalk` | T3 副作用 |  | ✅ | ← 同 `farm:petwalk`〔pet_walk〕 |
| `放置` ≡`place` ⇄`cabin:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `放鱼` ≡`pond_add` | T2 摆场 |  | ✅ | ← 同 `farm:pond_add`〔_pond_add〕 |
| `敲` ≡`break` ⇄`cabin:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `方形规划` ≡`plan` | T2 摆场 |  | ✅ | ← 同 `farm:plan`〔plan_farm_layout_tool〕 |
| `机器` ≡`collect` | T2 摆场 |  | ✅ | ← 同 `farm:collect`〔collect_machines〕 |
| `浇` ≡`water` | T2 摆场 |  | ✅ | ← 同 `farm:water`〔water_crops〕 |
| `清` ≡`clear` | T2 摆场 |  | ✅ | ← 同 `farm:clear`〔_farm_clear〕 |
| `清格` ≡`clearground` | T2 摆场 |  | ✅ | ← 同 `farm:clearground`〔clear_ground〕 |
| `畜舍` | T2 摆场 |  | ✅ | `session_log:993` |
| `砍树` ≡`chop` | T2 摆场 |  | ✅ | ← 同 `farm:chop`〔chop_trees〕 |
| `碗` ≡`喂水` | T3 副作用 |  | ✅ | ← 同 `farm:喂水`〔pet_water〕 |
| `祈福` ≡`statue` ⇄`cabin:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |
| `蓄力锄` ≡`hoe` | T2 摆场 |  | ✅ | `session_log:1264` |
| `规划` ≡`plot` | T2 摆场 |  | ✅ | ← 同 `farm:plot`〔plot_plan〕 |
| `这间` ≡`畜舍` | T2 摆场 |  | ✅ | ← 同 `farm:畜舍`〔care_building〕 |
| `遛` ≡`petwalk` | T3 副作用 |  | ✅ | ← 同 `farm:petwalk`〔pet_walk〕 |
| `领籽` ≡`pond_collect` | T2 摆场 |  | ✅ | ← 同 `farm:pond_collect`〔_pond_collect〕 |
| `鱼塘` ≡`pond` | T2 摆场 |  | ✅ | ← 同 `farm:pond`〔_pond_list〕 |

## `mine`（16）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `bomb_collect` | T2 摆场 | max_items | ✅ | `session_log:1232` |
| `bomb_ladder` | T2 摆场 |  | ✅ | `session_log:1233` |
| `bomb_mine` | T2 摆场 | autodrop, bomb, follow_host, lead, min_covered, one_floor, target | ✅ | `session_log:1239` |
| `bomb_place` | T3 副作用 |  | ✅ | `session_log:1231` |
| `bomb_plan` | T1 自动 | min_covered, radius, top | ✅ | `session_log:1230` |
| `bomb_retreat` | T2 摆场 |  | ✅ | `session_log:1240` |
| `bomb_status` | T1 自动 |  | ✅ | `session_log:1226` |
| `bomb_volcano` | T3 副作用 | bomb, hp_threshold, max_minutes, min_covered, poll | ✅ | `session_log:590` |
| `farm` | T2 摆场 |  |  |  |
| `go` ≡`farm` | T2 摆场 | cycles, food_hp, food_sta, hp_threshold, mode, ore, resume, start, target |  |  |
| `organize` | T2 摆场 | disable, reset | ✅ | `session_log:1174` |
| `progress` ⇄`check:mine` | T1 自动 |  | ✅ | `session_log:1215` |
| `rush` ≡`farm` | T2 摆场 |  |  |  |
| `去` ≡`farm` | T2 摆场 |  |  |  |
| `整理背包` ≡`organize` | T2 摆场 |  | ✅ | ← 同 `mine:organize`〔bomb_organize〕 |
| `进度` ≡`progress` ⇄`check:mine` | T1 自动 |  | ✅ | ← 同 `mine:progress`〔check_mine_progress〕 |

## `cabin`（30）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `break` ⇄`farm:break` | T3 副作用 | radius, steps | ✅ | `session_log:287` |
| `collect` | T2 摆场 |  | ✅ | `session_log:628` |
| `cook` | T3 副作用 | recipe_name | ✅ | `session_log:445` |
| `decor` ⇄`scene:decor` | T2 摆场 |  | ✅ | `session_log:1428` |
| `enum` | T2 摆场 |  | ✅ | `session_log:627` |
| `furniture` ⇄`scene:furniture` | T1 自动 |  | ✅ | `session_log:1445` |
| `interact` ⇄`scene:at` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:631` |
| `pickup` ⇄`scene:pickup` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:1444` |
| `place` ⇄`farm:place` | T3 副作用 |  | ✅ | `session_log:1439` |
| `sleep` ⇄`daily:sleep` | T3 副作用 | who | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | `session_log:629` |
| `做饭` ≡`cook` | T3 副作用 |  | ✅ | ← 同 `cabin:cook`〔cook〕 |
| `可铺` ≡`decor` ⇄`scene:decor` | T2 摆场 |  | ✅ | ← 同 `cabin:decor`〔decor_report〕 |
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
| `点` ≡`interact` ⇄`scene:at` | T2 摆场 |  | ✅ | ← 同 `scene:at`〔interact_at〕 |
| `看` ≡`enum` | T2 摆场 |  | ✅ | ← 同 `cabin:enum`〔_cabin_enum〕 |
| `睡` ≡`sleep` ⇄`daily:sleep` | T3 副作用 |  | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `睡觉` ≡`sleep` ⇄`daily:sleep` | T3 副作用 |  | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `祈福` ≡`statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |
| `装修` ≡`decor` ⇄`scene:decor` | T2 摆场 |  | ✅ | ← 同 `cabin:decor`〔decor_report〕 |
| `雕像` ≡`statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |

## `social`（17）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `chat` | T2 摆场 |  | ✅ | `session_log:1295` |
| `emote` | T3 副作用 |  | ✅ | `session_log:1173` |
| `friendship` | T1 自动 |  | ✅ | `session_log:1302` |
| `gift` | T3 副作用 | item_name, npc_name |  |  |
| `give` | T3 副作用 | item_name, player_name | ✅ | `session_log:1357` |
| `hand` | T3 副作用 | item_name, player_name | ✅ | `session_log:1355` |
| `movie` | T2 摆场 | npc |  |  |
| `send` | T3 副作用 | message | ✅ | `session_log:698` |
| `snack` | T2 摆场 |  |  |  |
| `发` ≡`send` | T3 副作用 |  | ✅ | ← 同 `social:send`〔send_chat〕 |
| `好感` ≡`friendship` | T1 自动 |  | ✅ | ← 同 `social:friendship`〔check_friendship〕 |
| `搭话` ≡`chat` | T2 摆场 |  | ✅ | ← 同 `social:chat`〔chat_npc〕 |
| `电影` ≡`movie` | T2 摆场 |  |  |  |
| `给` ≡`give` | T3 副作用 |  | ✅ | ← 同 `social:give`〔give_item〕 |
| `送礼` ≡`gift` | T3 副作用 |  |  |  |
| `递给` ≡`hand` | T3 副作用 |  | ✅ | ← 同 `social:hand`〔hand_item〕 |
| `零食` ≡`snack` | T2 摆场 |  |  |  |

## `scene`（73）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `at` ⇄`cabin:interact` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:1238` |
| `berry` | T3 副作用 |  | ⏭ 没条件测 | `session_log:1189` 农场没有结果的浆果灌木（春25 早过树莓季春15-18）⇒ 摇的动作没发生；需在浆果季再验 |
| `break` ⇄`farm:break` | T3 副作用 | radius, steps | ✅ | `session_log:288` |
| `decor` ⇄`cabin:decor` | T2 摆场 |  | ✅ | 真机 09-19(81) 于 IslandFarmHouse：`cabin`/`scene` 域都调通；**两端(7842/7843)各读一次逐项一致**（4 地板房+5 墙房，applied/格数/bbox/tiles 全同），且与反编译 `IslandFarmHouse` 构造硬编码的 9 句 `SetFloor/SetWallpaper` 一一对上 |
| `drop` | T3 副作用 | items | ✅ | `session_log:1374`；**🔴 09-19(88) 真机抓到「反向谎报」并已修（Python 侧）**：`Stack=0` 的物品（＝**我们放下去的家具**捡回来的那种）⇒ C# `HandleDrop` 里 `toRemove = Math.Min(remaining, item.Stack)` 算出 **0**、`removed` 记 0，**可紧接着 `if (item.Stack <= 0) Items[i] = null` 照样把槽位清了** ⇒ **东西真没了，却回「一个都没丢」**（AI 以为还在）。修法=`_drop_one` 不信 `removed`，**读回占格数**（`_n1 < _n0` 即算成功并注明）。真机闭环验过：给→放→捡→丢 ⇒ 「已丢弃 家居植物（游戏回的 removed=0，但背包占格 15→14，确实丢了）」✅。**✅ 09-19(89) C# 侧也根治了**（`int have = item.Stack > 0 ? item.Stack : 1;`，任务 #40 结）：真机 `place`→`pickup` 造出 `stack=0` 的椅子 ⇒ 打前包格 18／椅子 `slot 11 stack 0`，POST `/drop` 回 **`{"removed":1,"inventoryLeft":17}`**、椅子真从背包消失、包格 17 ⇒ **报的 = 做的**（修前必是 `removed:0` 而格子照清）。`scene ops=drop` 域通路同样回「🗑️ 已丢弃 橡木椅子 x1」。⚠️ **Stack=0 是 vanilla 正常产物**（放家具时 `placementAction` 把**对象本身**入表 + `reduceActiveItemByOne` 把它减成 0；捡回来原样进包），**别去"修"它** |
| `face` | T2 摆场 | direction | ✅ | `session_log:1257` |
| `forge_help` | T2 摆场 |  | ✅ | `session_log:312` |
| `front` | T2 摆场 |  | ✅ | `session_log:636` |
| `furniture` ⇄`cabin:furniture` | T1 自动 |  | ✅ | `session_log:601` |
| `garbage` | T2 摆场 | dry_run, loc, pos, wait | ✅ | `session_log:321` |
| `interact` ≡`front` | T2 摆场 |  | ✅ | `session_log:1221` |
| `maze` | T2 摆场 | gx, gy, radius | ✅ | `session_log:711` |
| `maze_seg` | T2 摆场 | gx, gy, radius | ✅ | `session_log:313` |
| `maze_walk` ⇄`map:walk_multi` | T2 摆场 | location, max_seg, max_wait, waypoints | ✅ | `session_log:715` |
| `moss` | T3 副作用 | dry_run, radius, rounds, target_max | ✅ | `session_log:1191` |
| `pan` | T2 摆场 | dry_run, radius, timeout | ✅ | `session_log:1176` |
| `pickup` ⇄`cabin:pickup` | T2 摆场 | tile_x, tile_y | ✅ | ⚠️ 原判定是 `--from-log` **自动打的**（只证明"调用没报错"，不算验过）；**09-19 真机补**：隔 15 格拿起(`/furniture` 19→18、背包 11→12) ＋ 放回(18→19、原地)，空格子点名「这格没有家具」＋列就近家具（CHANGELOG 82）。⚠️ **09-19(87) 又抓到一个谎报（未修，已记账）**：**多件家具重叠时，报的名字是"猜的"** —— 拿 `(39,24)` 的椅子（上面压着 2x2 地毯），工具回「拿起了 Burlap Rug」，**全量 diff 却是椅子被拿走、地毯没动**。根因 `HandleFurniturePickup` 的 `furnitureName` 取「第一个包围盒命中」，真删的是 `LowPriorityLeftClick` —— 两者不是一回事（同「判据别放消费侧猜」）。修法=`LowPriorityLeftClick` 前后 diff `loc.furniture`。**✅ 09-19(88) 已修并真机验**（Python 侧读回，没关游戏）：`/furniture` 前后 diff 报**真正少掉**的那件（跑轮询，因为删除走 `furnitureToRemove` 队列、下一个 update 才落地）；真机 `(39,24)`（地毯压着）报「家居植物」✅、`(39,26)` 报「橡木椅子」✅。**同日一并验掉 #27「背包满静默失败」**：塞满 36/36 ⇒ 如实报「picked=true 可一件没少…物品没动、地上还在」+ 椅子独立核验仍在原地 ✅（反编译 `removeQueuedFurniture`：`if (!couldInventoryAcceptThisItem) return;` 连删都不删）。再补一环：捡完还数**背包占格有没有 +1**（用占格数不用数量——家具 Stack 可能是 0）。**✅ 09-19(89) C# 侧也照抄了游戏的两趟扫**（`PredictPickTarget`，任务 #40 结）：真机在恒的原案原地（Cabin，(39,24) 椅子 + `(39,23)` 2x2 粗麻布地毯 **type=12 可穿行、且在表里排前面**）⇒ C# 回 `"furniture":"Oak Chair"`（**预测=游戏真拿走的那件**，家具 28→27 只剩地毯+电视）＋ 新开的 `"furnitureHere":"Burlap Rug"`（保留旧的"这格上有没有家具"语义，防"拿不动"被反推成"没家具"）；空 `(40,25)` ⇒ 两个都是 null ⇒ Python 如实报「这格没有家具」；AI 通路回归仍报「拿起了 橡木椅子」。🔧 **已知小瑕疵（未修 · 攒着）**：这两处名字是**英文** `f.Name`，而同 DLL 的 `/furniture` 用 `SafeDisplayName`——一行 ×2 处可对齐，需再关一次游戏；**恒 09-19 拍板攒到下次 C# 批次**（任务 #41，可与 `/placechest` 搭车）。⏭ 仍没条件测：**开菜单时拿不了**（要 AI 窗口前台才能合成按键） |
| `pickup_scene` | T3 副作用 | max_items | ✅ | `session_log:831` |
| `place` ⇄`farm:place` | T3 副作用 |  | ✅ | 🪵 **地板/墙纸全链 09-19 真机验过**（IslandFarmHouse）：正铺地板 `applied 48→0` / 正铺墙纸 `87→0`（**两端 7842/7843 逐项一致**、背包各 −1）＋ 故意拿地板点**墙格** ⇒ 点名「这格是**墙格**」+ 列各房可铺格 + 给可照抄 op，且**不消耗、applied 一点没变** ＋ 直接打 `/use` 的**对照组**证明守门是承重的（游戏自己只回 `Cannot place 'Flooring' here`、不说为什么）＋ 非装修图(IslandSouth) 拒绝且不消耗 ＋ 非地板/墙纸**不误拦**（树液走物品门）＋ 铺完**还原 9 个值全回原样**。⚠️ **仍然没验的**：地皮上放**箱子/机器**那一路（`session_log:307` 原本要的那块地皮）。⚠️ 已知缺口见 `select` 行。**🎯 09-19(87) 又补一轮 —— 放置三件（同款叠放吃物品根治）**：真机坐实 `Object.placementAction` 兜底那段**不查占位** ⇒ 同款叠放**物品凭空消失、`/use` 还回 `ok:true`**（异款则把旧的打落成掉落物；**箱子走另一条分支、游戏自己会拒**）。改了三处并**八条真机全验**：**A** C# `/use` 读回验证（`placed_noop` + 不消耗，判据要 `objects`∩`terrainFeatures`∩`furniture` **三处一起看**，否则误杀木地板/家具）· **C** 拟人闸门（Chebyshev ≤2，照抄 `_HasNonMousePlacementLeeway`；**地板/墙纸豁免**，已验隔 19 格远墙仍可铺）· **B** 占位守门（动手前点名 + 给 `break`/换格两条路）。现场全还原 |
| `rock` | T3 副作用 | break_stone, dig, max_break, radius | ✅ | `session_log:1185` |
| `rummage` ≡`garbage` | T2 摆场 |  | ✅ | ← 同 `scene:garbage`〔trash_run〕 |
| `seats` | T1 自动 | radius | ✅ | `session_log:845` |
| `select` | T3 副作用 |  | ✅ | `session_log:1258`；⚠️ **09-19 真机坐实一个缺口**：它按 **Name/DisplayName** 精确匹配取**第一个** ⇒ 地板/墙纸这类「**同名多款**」（`(FL)0`/`(FL)1` 显示名**都是**「地板」，游戏自己起的，见 `Wallpaper.cs:58`）**永远只会选中 slot 靠前那款**，想铺特定一款办不到（实测：同背包装两张地板，铺掉的必是先出现的那张）。`/state` 已经给了 `itemId`+`slotIndex`、`/drop` 已经认 QualifiedItemId —— **唯独 `/select` 不认**，缺的就这一环 |
| `sit` | T2 摆场 | face | ✅ | `session_log:646` |
| `spot` | T3 副作用 |  | ✅ | `session_log:1362`；🫚 **09-19 姜点支路补齐**（`IsGingerTile` 此前从没样本）：IslandWest 野生姜 **9/9** 挥中（`(O)829` Ginger ×9 进包、9 格 `forageCrop` 全清、**HoeDirt 全留**、当天绿雨⇒走 `hitWithHoe` 的下雨分支 `state=1`）；IslandNorth 又 **2/2**（累计 11/11）。⚠️ **背上"走路"那层还漏**：`_walk_exact` 死等 0.6 秒 ⇒ 每次换目标都 `/position` 瞬移（**已修**，见 CHANGELOG 09-19(91)②）；**真凶在 C#**——走位器 `FindPath` 失败时 `farmer.Position = 目标` 兜底瞬移（CHANGELOG 09-19(91)③，任务 **#43**） |
| `stand` | T2 摆场 |  | ✅ | `session_log:647` |
| `use` | T3 副作用 |  | ✅ | `session_log:1254` |
| `丢` ≡`drop` | T3 副作用 |  | ✅ | ← 同 `scene:drop`〔drop_item〕 |
| `分段` ≡`maze_seg` | T2 摆场 |  | ✅ | ← 同 `scene:maze_seg`〔_maze_seg_view〕 |
| `可坐` ≡`seats` | T1 自动 |  | ✅ | ← 同 `scene:seats`〔seats〕 |
| `可铺` ≡`decor` ⇄`cabin:decor` | T2 摆场 |  | ✅ | ← 同 `cabin:decor`〔decor_report〕 |
| `坐` ≡`sit` | T2 摆场 |  | ✅ | ← 同 `scene:sit`〔sit〕 |
| `坐下` ≡`sit` | T2 摆场 |  | ✅ | ← 同 `scene:sit`〔sit〕 |
| `家具` ≡`furniture` ⇄`cabin:furniture` | T1 自动 |  | ✅ | ← 同 `cabin:furniture`〔scan_furniture〕 |
| `座位` ≡`seats` | T1 自动 |  | ✅ | ← 同 `scene:seats`〔seats〕 |
| `拆` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `拾起` ≡`pickup` ⇄`cabin:pickup` | T2 摆场 |  | ✅ | ← 同 `cabin:pickup`〔furniture_pickup〕 |
| `拿` ≡`select` | T3 副作用 |  | ✅ | ← 同 `scene:select`〔select_item〕 |
| `挖斑点` ≡`spot` | T3 副作用 |  | ✅ | ← 同 `scene:spot`〔spot_run〕 |
| `挖石` ≡`rock` | T3 副作用 |  | ✅ | ← 同 `scene:rock`〔rock_dig〕 |
| `挖矿点` ≡`rock` | T3 副作用 |  | ✅ | ← 同 `scene:rock`〔rock_dig〕 |
| `挖蚯蚓` ≡`spot` | T3 副作用 |  | ✅ | ← 同 `scene:spot`〔spot_run〕 |
| `挥` ≡`use` | T3 副作用 |  | ✅ | ← 同 `scene:use`〔use_tool〕 |
| `捡物` ≡`pickup_scene` | T3 副作用 |  | ✅ | ← 同 `scene:pickup_scene`〔pickup_scene〕 |
| `捡采集` ≡`pickup_scene` | T3 副作用 |  | ✅ | ← 同 `scene:pickup_scene`〔pickup_scene〕 |
| `搜刮苔藓` ≡`moss` | T3 副作用 |  | ✅ | ← 同 `scene:moss`〔moss_run〕 |
| `摇树莓` ≡`berry` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:berry`〔berry_run〕 |
| `放` ≡`place` ⇄`farm:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `放置` ≡`place` ⇄`farm:place` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:place`〔place_item〕 |
| `敲` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `敲击` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `farm:break`〔break_tile〕 |
| `敲石` ≡`rock` | T3 副作用 |  | ✅ | ← 同 `scene:rock`〔rock_dig〕 |
| `浆果` ≡`berry` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `scene:berry`〔berry_run〕 |
| `淘` ≡`pan` | T2 摆场 |  | ✅ | ← 同 `scene:pan`〔_pan_run〕 |
| `淘盘` ≡`pan` | T2 摆场 |  | ✅ | ← 同 `scene:pan`〔_pan_run〕 |
| `淘金` ≡`pan` | T2 摆场 |  | ✅ | ← 同 `scene:pan`〔_pan_run〕 |
| `点` ≡`at` ⇄`cabin:interact` | T2 摆场 |  | ✅ | ← 同 `scene:at`〔interact_at〕 |
| `站起` ≡`stand` | T2 摆场 |  | ✅ | ← 同 `scene:stand`〔stand〕 |
| `站起来` ≡`stand` | T2 摆场 |  | ✅ | ← 同 `scene:stand`〔stand〕 |
| `绿雨` ≡`moss` | T3 副作用 |  | ✅ | ← 同 `scene:moss`〔moss_run〕 |
| `翻垃圾桶` ≡`garbage` | T2 摆场 |  | ✅ | ← 同 `scene:garbage`〔trash_run〕 |
| `翻桶` ≡`garbage` | T2 摆场 |  | ✅ | ← 同 `scene:garbage`〔trash_run〕 |
| `装修` ≡`decor` ⇄`cabin:decor` | T2 摆场 |  | ✅ | ← 同 `cabin:decor`〔decor_report〕 |
| `走迷宫` ≡`maze_walk` ⇄`map:walk_multi` | T2 摆场 |  | ✅ | ← 同 `map:walk_multi`〔_maze_walk〕 |
| `起身` ≡`stand` | T2 摆场 |  | ✅ | ← 同 `scene:stand`〔stand〕 |
| `转身` ≡`face` | T2 摆场 |  | ✅ | ← 同 `scene:face`〔face〕 |
| `迷宫` ≡`maze` | T2 摆场 |  | ✅ | ← 同 `scene:maze`〔_maze_view〕 |
| `迷宫走` ≡`maze_walk` ⇄`map:walk_multi` | T2 摆场 |  | ✅ | ← 同 `map:walk_multi`〔_maze_walk〕 |
| `迷宫链` ≡`maze_seg` | T2 摆场 |  | ✅ | ← 同 `scene:maze_seg`〔_maze_seg_view〕 |
| `采矿点` ≡`rock` | T3 副作用 |  | ✅ | ← 同 `scene:rock`〔rock_dig〕 |
| `铜锅` ≡`pan` | T2 摆场 |  | ✅ | ← 同 `scene:pan`〔_pan_run〕 |
| `锻造帮助` ≡`forge_help` | T2 摆场 |  | ✅ | ← 同 `scene:forge_help`〔_lambda_scene_932ae5f5〕 |
| `面前` ≡`front` | T2 摆场 |  | ✅ | ← 同 `scene:interact`〔interact〕 |

## `menu`（74）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `advance` | T2 摆场 |  | ✅ | `session_log:1133` |
| `bin` | T3 副作用 | sell_all | ✅ | `session_log:657` |
| `bundle` | T1 自动 | area | ✅ | `session_log:602` |
| `bundle_kb` | T1 自动 | query | ✅ | `session_log:603` |
| `cancel` | T2 摆场 |  | ✅ | `session_log:1317` |
| `click` | T3 副作用 | action, button, category, option, quantity, real, right, slot | ✅ | `session_log:1223` |
| `craft` | T3 副作用 | item_name | ✅ | `session_log:548` |
| `craftables` | T1 自动 |  | ✅ | `session_log:1291` |
| `customize` ⇄`settings:customize` | T3 副作用 | farmname, favorite | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `display_fill` ⇄`festival:display_fill` | T3 副作用 | items |  |  |
| `display_takeback` ⇄`festival:display_takeback` | T3 副作用 |  |  |  |
| `donate` | T3 副作用 |  | ⏭ 没条件测 | `session_log:567` 要一座还真缺东西的博物馆（当前 95/95 全齐，没东西可捐）；✅ 但「满馆调用无副作用」已真机验收（不删展品/不吃背包物） |
| `forge` | T3 副作用 | item1, item2, mode, target | ⏭ 没条件测 | `session_log:577` 要背包里有火山晶石（Cinder Shard）才能走完合成；✅ 但「有晶石时完整跑通」已真机验收（产出 Combined Ring），「缺料拒绝」本次也验了（报 20 ✓、戒指原样退回） |
| `geode` | T3 副作用 |  | ✅ | `session_log:555` |
| `geodes` | T3 副作用 |  | ✅ | `session_log:556` |
| `journal` ⇄`check:quest` | T2 摆场 |  | ✅ | `session_log:690` |
| `key` | T2 摆场 | hold, key | ✅ | `session_log:1108` |
| `know` | T2 摆场 |  | ✅ | `session_log:510` |
| `levelup_choose` | T3 副作用 | profession, side |  |  |
| `minigame` | T2 摆场 | action |  |  |
| `minigame_state` | T2 摆场 |  | ⏭ 没条件测 | `session_log:545` 要赌场小游戏（CalicoJack/Slots，得先进沙漠赌场） |
| `number` | T2 摆场 | confirm | ⏭ 没条件测 | `session_log:546` 要弹着数量框（节庆兑换台 / 转盘押注） |
| `read` | T2 摆场 |  | ✅ | `session_log:1290` |
| `read_book` | T3 副作用 |  | ✅ | `session_log:549` |
| `recipes` | T1 自动 |  | ✅ | `session_log:1116` |
| `sell` | T3 副作用 |  | ✅ | `session_log:866` |
| `shop` | T2 摆场 | place, want |  |  |
| `skip` | T2 摆场 |  | ✅ | `session_log:1121` |
| `任务知` ≡`know` | T2 摆场 |  | ✅ | ← 同 `menu:know`〔calendar_data.special_orders_available〕 |
| `关` ≡`cancel` | T2 摆场 |  | ✅ | ← 同 `menu:cancel`〔cancel〕 |
| `出货` ≡`bin` | T3 副作用 |  | ✅ | ← 同 `menu:bin`〔sell_to_bin〕 |
| `分支` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `剧情` ≡`advance` | T2 摆场 |  | ✅ | ← 同 `menu:advance`〔advance_story〕 |
| `卖` ≡`sell` | T3 副作用 |  | ✅ | ← 同 `menu:sell`〔sell_to_shop〕 |
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
| `推进` ≡`advance` | T2 摆场 |  | ✅ | ← 同 `menu:advance`〔advance_story〕 |
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
| `跳` ≡`skip` | T2 摆场 |  | ✅ | ← 同 `menu:跳剧情`〔skip_event〕 |
| `跳剧情` ≡`skip` | T2 摆场 |  | ✅ | `session_log:1122` |
| `跳过` ≡`skip` | T2 摆场 |  | ✅ | ← 同 `menu:跳剧情`〔skip_event〕 |
| `选职业` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `逛店` ≡`shop` | T2 摆场 |  |  |  |
| `配方` ≡`craftables` | T1 自动 |  | ✅ | ← 同 `menu:craftables`〔list_craftables〕 |
| `锻造` ≡`forge` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `menu:forge`〔forge〕 |

## `storage`（20）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `default` | T3 副作用 |  | ✅ | `session_log:263` |
| `find` | T1 自动 |  | ✅ | `session_log:604` |
| `store` | T3 副作用 | all, items, keepTools, target | ✅ | `session_log:846` |
| `tag` | T3 副作用 | target | ✅ | `session_log:258` |
| `take` | T3 副作用 | items | ✅ | `session_log:643` |
| `view` | T1 自动 | box | ✅ | `session_log:1076` |
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
| `lie_bed` | T3 副作用 | who | ✅ | `session_log:1273` |
| `pause` | T3 副作用 | out_of_focus | ✅ | `session_log:342` |
| `peek` | T2 摆场 |  | ✅ | `session_log:1411` |
| `settle` | T3 副作用 |  | ⏭ 没条件测 | `session_log:687` 要正在弹着的过夜结算菜单（且停在汇总页） |
| `sleep` ⇄`cabin:sleep` | T3 副作用 | who | ✅ | `session_log:1402` |
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
| `躺` ≡`lie_bed` | T3 副作用 |  | ✅ | ← 同 `daily:lie_bed`〔lie_bed〕 |
| `躺床` ≡`lie_bed` | T3 副作用 |  | ✅ | ← 同 `daily:lie_bed`〔lie_bed〕 |
| `钉白板` ≡`wb_pin` | T3 副作用 |  | ✅ | ← 同 `daily:wb_pin`〔whiteboard_pin〕 |

## `map`（17）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `go` | T2 摆场 | destination | ✅ | `session_log:1397` |
| `lookup` | T1 自动 | location | ✅ | `session_log:1103` |
| `npc` | T2 摆场 |  | ✅ | `session_log:1415` |
| `query` | T1 自动 | function | ✅ | `session_log:611` |
| `unlocks` | T2 摆场 |  | ✅ | `session_log:582` |
| `walk` | T2 摆场 | poi_name | ✅ | `session_log:1382` |
| `walk_multi` ⇄`scene:maze_walk` | T2 摆场 | location, max_seg, max_wait, waypoints | ✅ | `session_log:720` |
| `warp_safe` | T2 摆场 |  | ✅ | `session_log:927` |
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
| `egg_note` | ⏭ 当期不可用 | route | ✅ | `session_log:885` |
| `egg_run` | ⏭ 当期不可用 | route |  |  |
| `eggs` | ⏭ 当期不可用 |  | ✅ | `session_log:858` |
| `go` | ⏭ 当期不可用 |  | ✅ | `session_log:970` |
| `help` | T1 自动 |  | ✅ | `session_log:736` |
| `ice_fish` | ⏭ 当期不可用 |  | ✅ | 🎣 **真机过(2026-09-12 冬钓节复测)**：哨兵自动接管→钓 6 条**赢了**(赢线≥5)；新满级站位(69,36)一次到位。⚠️ 这次不经 MCP 调用(后台线程跑的)故无 session_log 行；证据=比赛 score=6 + 哨兵播报 + 背包收到首胜四件套(水手帽/精装旋式鱼饵/倒刺钩/磁铁) |
| `info` | T1 自动 |  | ✅ | `session_log:968` |
| `interact` | ⏭ 当期不可用 |  | ✅ | `session_log:983` |
| `maze` | ⏭ 当期不可用 |  |  |  |
| `maze_walk` ⇄`scene:maze_walk` | ⏭ 当期不可用 | location, max_seg, max_wait, waypoints |  |  |
| `next` | T1 自动 |  | ✅ | `session_log:617` |
| `poi` | ⏭ 当期不可用 |  | ✅ | `session_log:731` |
| `prep` | ⏭ 当期不可用 |  | ✅ | `session_log:734` |
| `shop` | ⏭ 当期不可用 |  | ✅ | `session_log:962` |
| `strength` | ⏭ 当期不可用 | delay |  |  |
| `today` | T1 自动 |  | ✅ | `session_log:854` |
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
| `蛋` ≡`eggs` | T1 自动 |  | ✅ | ← 同 `festival:eggs`〔_festival_eggs〕 |
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
| `crab` | T1 自动 |  | ✅ | `session_log:849` |
| `crab_bait` | T2 摆场 | bait | ⏭ 没条件测 | `session_log:537` 要背包里有鱼饵（先去威利鱼店买 Bait） |
| `crab_collect` | T2 摆场 |  | ✅ | `session_log:539` |
| `crab_diag` | T2 摆场 | location | ✅ | `session_log:538` |
| `crab_place` | T2 摆场 | bait, radius | ✅ | `session_log:532` |
| `crab_retract` | T2 摆场 | location | ✅ | `session_log:540` |
| `crab_water` | T2 摆场 | radius | ✅ | `session_log:531` |
| `fish` | T2 摆场 |  | ✅ | ← 同 `fish:go`〔go_fishing〕 |
| `go` ≡`fish` | T2 摆场 | location, max_casts, no_sleep | ✅ | `session_log:1241` |
| `info` | T1 自动 | location | ✅ | `session_log:616` |
| `rod` | T2 摆场 | action | ✅ | `session_log:614` |
| `spots` | T2 摆场 |  | ✅ | `session_log:1202` |
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
| `钓` ≡`fish` | T2 摆场 |  | ✅ | ← 同 `fish:go`〔go_fishing〕 |
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
| `continue` | T3 副作用 | job_id | ✅ | `session_log:1099` |
| `stop` | T3 副作用 | job_id | ✅ | `session_log:1098` |
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

