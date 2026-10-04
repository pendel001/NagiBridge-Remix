# 🧪 全工具测试清单（2026-09-11 生成）

> **517 行 = 189 个后端函数**（抄自各域 dispatch，非文档）｜✅ 426 行 / **165 函数** ｜❌ 0 ｜⏭ 没条件测 27 行 / 9 函数｜**待测 15 函数**｜T1 自动 103 ｜T2 摆场 212 ｜T3 副作用 161 ｜⏭ 当期不可用 41
> ⚠️ **2026-09-19(92) 这一批是🖐️手改的行**（`买动物`/`gift`/`送礼`/`shop`/`逛店` 五行的判定+证据），
> **上面那行汇总数还没跟着重算**（手工加应为 ✅ **167 函数** / 待测 **13**）。**没敢跑 `--from-log`**，因为它对今晚这类
> **故意的负样本**会把它们记成 ❌（实测副本跑出来 `❌ 3`），并且会把 7 行手写的 `⏭ 没条件测`（`放`/`放置` 那族）
> 又冲成 ✅ —— 两个都是本表最怕的漂。**下次专门收一次：先修生成器（负样本/⏭ 语义），再重算汇总。**
> ⚠️ **2026-09-20 又手改了一批**（`mine:go`/`mine:farm` + social `movie`/`snack`/`电影`/`零食` + menu
> `minigame`/`minigame_state`/`小游戏`/`小游戏状态`/`赌场` 共 11 行）。**汇总数照旧没动** —— 上面那条"先修生成器
> 再重算"的前提没变，我不想在生成器还漂的时候手搓一个数字进表。**待测函数净减 5**（`go_mining`/`movie`/`snack`/
> `minigame_click`/`minigame_state`），其中 `minigame_state` 是**清掉一个过期的 `⏭ 没条件测`**（本轮真进赌场了）。
> ⚠️ **2026-09-20 深夜再手改 4 行**（`menu:forge`/`锻造` + `fish:crab_bait`/`放饵`，全是 `⏭ → ✅` 真机验掉；
> 见 CHANGELOG (103)）。**汇总数照旧没动**（同上，生成器还漂）。**待测函数净减 2**。
> 📋 同批盘点：**剩下的 `⏭` 基本全卡季节/节日**（`menu:number` 只有秋16 展销会弹、`display_fill/takeback` 秋16 展位、
> `scene:berry` 浆果季、`farm:milk` 装了自动采集器、`menu:donate` 博物馆满），`menu:levelup_choose` 要真升级；
> **恒 09-20 拍板"黄色红色不必测"**。另三行 `farm:buy`/`mine:rush`/`festival:捡` 是**假缺口**（`≡⇄` 别名传播没打勾，函数本身已有结论）。
> ⚠️ **2026-09-20 第二轮"回滚体检"（原语烟囱）—— 判定列 0 行增绿，1 行翻红。** 恒拍板**不重跑 189 全量**，
> 改成挑 6 个**共用 C# 原语**（`/state` `/menu` `/walk_to` `/interact` `/use` `/passable`）各找一个**已验工具**跑通
> ⇒ **6/6 全绿**；两个"改过的共享面"（`navigation.py:1372` 售票「是」/ `:2254` 矿车目的站）也全绿
> ⇒ **原语绿 = 地基没塌，但【不等于】某条 op 绿**（靠原语反推叶子是倒着推，**不记进表**，所以判定列没加绿）。
> 🔴 红是红项自己挣的：`social:give` 真机翻案 → ❌（原 `session_log:1357` 那条 ✅ 是**只看"送没送到"**，没看副作用）。
> 🔧 **待办**：修 `give` 成功分支的手持格写回（方案见 CHANGELOG (104)④），修完**复验这一行**。

> 用法：跑测试（任何渠道，只要走 :8000 的 MCP）→ `python gen_tool_checklist.py --from-log` 自动打勾。
> 判定规则见脚本头部；**⚠️ op「」= 参数被静默丢掉 ⇒ 不算 ✅**（也不算 ❌ —— 那一下压根没执行，当"没有证据"跳过，本行沿用上一轮结论）；真报错才 ❌。
> ⚠️ 本表**测试前冻结**：`--from-log` 只写后两列，永不动 op 列表。
> 🖐️ **手写的 `⏭ 没条件测` 压得过日志的 ✅**（"参数对了/没报错"≠"真验过"）——想转绿要先人工清掉 ⏭；但它**压不过日志里的 ❌**（真失败必须红着露出来）。
> 🔁 **`⇄` = 跨域同函数**（同一个后端函数挂在别的域下，各占一行）：判定按函数传播，任一行绿了其余行跟着绿 —— 所以**别重复测**，看 `⇄` 挑一行测即可。


## `check`（36）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `backpack` | T1 自动 |  | ✅ | `session_log:1539` |
| `building` | T1 自动 |  | ✅ | `session_log:455` |
| `building_list` ≡`building` | T1 自动 |  | ✅ | ← 同 `check:building`〔building_list〕 |
| `buildings` ≡`building` | T1 自动 |  | ✅ | `session_log:15` |
| `chests` | T1 自动 | chest | ✅ | `session_log:1075` |
| `hay` | T1 自动 |  | ✅ | `session_log:457` |
| `look` | T1 自动 | radius | ✅ | `session_log:1538` |
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
| `role` | T1 自动 |  | ✅ | `session_log:1597` |
| `silo` ≡`hay` | T1 自动 |  | ✅ | `session_log:1364` |
| `status` | T1 自动 |  | ✅ | `session_log:1459` |
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
| `买动物` ≡`buy` | T3 副作用 |  | ✅ | 真机 09-19：正路买牛(age 0 当场同步房主端)/棚满/未知种类/缺参/点名满棚/点名类型不符/点名瞎名 — 见 CHANGELOG 92① |
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
| `拆` ≡`break` ⇄`cabin:break` | T3 副作用 |  | ✅ | ← 同 `cabin:break`〔break_tile〕 |
| `挤奶` ≡`milk` | T2 摆场 |  | ⏭ 没条件测 | ← 同 `farm:milk`〔milk_shear〕 |
| `摸动物` ≡`animals` | T2 摆场 |  | ✅ | ← 同 `farm:animals`〔care_animals〕 |
| `摸摸` ≡`pet` | T3 副作用 |  | ✅ | ← 同 `farm:pet`〔pet_pet〕 |
| `摸猫狗` ≡`pet` | T3 副作用 |  | ✅ | ← 同 `farm:pet`〔pet_pet〕 |
| `播种规划` ≡`plant` | T2 摆场 |  | ✅ | ← 同 `farm:plant`〔_farm_plant〕 |
| `收` ≡`harvest` | T2 摆场 |  | ✅ | ← 同 `farm:harvest`〔harvest_crops〕 |
| `收放` ≡`building` | T2 摆场 |  | ✅ | ← 同 `farm:building`〔work_building〕 |
| `放` ≡`place` ⇄`cabin:place` | T3 副作用 |  | ✅ | ← 同 `scene:place`〔place_item〕 |
| `放牧` ≡`petwalk` | T3 副作用 |  | ✅ | ← 同 `farm:petwalk`〔pet_walk〕 |
| `放置` ≡`place` ⇄`cabin:place` | T3 副作用 |  | ✅ | ← 同 `scene:place`〔place_item〕 |
| `放鱼` ≡`pond_add` | T2 摆场 |  | ✅ | ← 同 `farm:pond_add`〔_pond_add〕 |
| `敲` ≡`break` ⇄`cabin:break` | T3 副作用 |  | ✅ | ← 同 `cabin:break`〔break_tile〕 |
| `方形规划` ≡`plan` | T2 摆场 |  | ✅ | ← 同 `farm:plan`〔plan_farm_layout_tool〕 |
| ~~`机器` ≡`collect`~~ | — |  | ⛔ | **2026-10-03 删除**：`farm:collect`〔`collect_machines`〕与 C# `/machine_collect` 一起删了（收放只留拟人那条：`farm:load` → `machine_loader --here`） |
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
| `farm` | T2 摆场 |  | ✅ | ← 同 `mine:go`〔go_mining〕 |
| `go` ≡`farm` | T2 摆场 | cycles, food_hp, food_sta, hp_threshold, mode, ore, resume, start, target | ✅ | `session_log:1666` farm ore=Copper 真机跑通（找到目标矿→敲碎→背包 Copper Ore x4）。⚠️ **首跑是红的**：困难矿井铜矿不认（(O)849）连刷 3 次「本层无 Copper」敲 0 块，已修 → CHANGELOG 09-20 |
| `organize` | T2 摆场 | disable, reset | ✅ | `session_log:1174` |
| `progress` ⇄`check:mine` | T1 自动 |  | ✅ | `session_log:1215` |
| `rush` ≡`farm` | T2 摆场 |  |  |  |
| `去` ≡`farm` | T2 摆场 |  |  |  |
| `整理背包` ≡`organize` | T2 摆场 |  | ✅ | ← 同 `mine:organize`〔bomb_organize〕 |
| `进度` ≡`progress` ⇄`check:mine` | T1 自动 |  | ✅ | ← 同 `mine:progress`〔check_mine_progress〕 |

## `cabin`（30）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `break` ⇄`farm:break` | T3 副作用 | radius, steps | ✅ | `session_log:1554` |
| `collect` | T2 摆场 |  | ✅ | `session_log:628` |
| `cook` | T3 副作用 | recipe_name | ✅ | `session_log:445` |
| `decor` ⇄`scene:decor` | T2 摆场 |  | ✅ | `session_log:1428` |
| `enum` | T2 摆场 |  | ✅ | `session_log:627` |
| `furniture` ⇄`scene:furniture` | T1 自动 |  | ✅ | `session_log:1449` |
| `interact` ⇄`scene:at` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:631` |
| `pickup` ⇄`scene:pickup` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:1583` |
| `place` ⇄`farm:place` | T3 副作用 |  | ✅ | `session_log:1582` |
| `sleep` ⇄`daily:sleep` | T3 副作用 | who | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | `session_log:629` |
| `做饭` ≡`cook` | T3 副作用 |  | ✅ | ← 同 `cabin:cook`〔cook〕 |
| `可铺` ≡`decor` ⇄`scene:decor` | T2 摆场 |  | ✅ | ← 同 `scene:decor`〔decor_report〕 |
| `家具` ≡`furniture` ⇄`scene:furniture` | T1 自动 |  | ✅ | ← 同 `cabin:furniture`〔scan_furniture〕 |
| `引导` ≡`enum` | T2 摆场 |  | ✅ | ← 同 `cabin:enum`〔_cabin_enum〕 |
| `拆` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `cabin:break`〔break_tile〕 |
| `拿` ≡`pickup` ⇄`scene:pickup` | T2 摆场 |  | ✅ | ← 同 `scene:pickup`〔furniture_pickup〕 |
| `摆` ≡`pickup` ⇄`scene:pickup` | T2 摆场 |  | ✅ | ← 同 `scene:pickup`〔furniture_pickup〕 |
| `收` ≡`collect` | T2 摆场 |  | ✅ | ← 同 `cabin:collect`〔_cabin_collect〕 |
| `放` ≡`place` ⇄`farm:place` | T3 副作用 |  | ✅ | ← 同 `scene:place`〔place_item〕 |
| `放置` ≡`place` ⇄`farm:place` | T3 副作用 |  | ✅ | ← 同 `scene:place`〔place_item〕 |
| `敲` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `cabin:break`〔break_tile〕 |
| `机器` ≡`collect` | T2 摆场 |  | ✅ | ← 同 `cabin:collect`〔_cabin_collect〕 |
| `点` ≡`interact` ⇄`scene:at` | T2 摆场 |  | ✅ | ← 同 `scene:at`〔interact_at〕 |
| `看` ≡`enum` | T2 摆场 |  | ✅ | ← 同 `cabin:enum`〔_cabin_enum〕 |
| `睡` ≡`sleep` ⇄`daily:sleep` | T3 副作用 |  | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `睡觉` ≡`sleep` ⇄`daily:sleep` | T3 副作用 |  | ✅ | ← 同 `daily:sleep`〔go_sleep〕 |
| `祈福` ≡`statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |
| `装修` ≡`decor` ⇄`scene:decor` | T2 摆场 |  | ✅ | ← 同 `scene:decor`〔decor_report〕 |
| `雕像` ≡`statue` ⇄`farm:statue` | T2 摆场 |  | ✅ | ← 同 `cabin:statue`〔blessing_statue〕 |

## `social`（17）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `chat` | T2 摆场 |  | ✅ | `session_log:1295` |
| `emote` | T3 副作用 |  | ✅ | `session_log:1173` |
| `friendship` | T1 自动 |  | ✅ | `session_log:1302` |
| `gift` | T3 副作用 | item_name, npc_name | ✅ | 真机 09-19：送礼正路(台词+好感+周/日计数+背包)/手持非空不毁物/今天已送过被拦/背包没这件/NPC 不存在 — 见 CHANGELOG 92② |
| `give` | T3 副作用 | item_name, player_name | ❌ | 真机 09-20 第二轮（同图）：**东西送得到，但手持那一格连东西带格一起被顶掉** —— 成功分支 `ModEntry.cs:10468` **从不写回 `heldSaved`**（只在失败分支 `10437`/`10463` 还原）；实测 破损CD x2 蒸发、背包 35→34 格、该格变空。另：**不同图时先把人挪到"对方坐标在本图的位置"再失败**（`give` 假设两人同图）。见 CHANGELOG (104)④。**🔧 09-21 已修**（礼物改放空格 + `CurrentToolIndex` 指过去，提议了结后由 `RestoreHeldWhenSettled` 指回；DLL 已双盘部署）—— **⏳ 但没验，仍挂 ❌**，明天重启游戏后复验 |
| `hand` | T3 副作用 | item_name, player_name | ✅ | `session_log:1355` |
| `movie` | T2 摆场 | npc | ✅ | `session_log:1631,1634` 无参出全表；npc=阿比盖尔 走 `_movie_npc_plan`（喜爱 +100、糖冰棍 +50）两条分支都对 |
| `send` | T3 副作用 | message | ✅ | `session_log:698` |
| `snack` | T2 摆场 |  | ✅ | `session_log:1632` 28 条零食全出（Joja玉米 10g → 星之果实雪糕 1250g） |
| `发` ≡`send` | T3 副作用 |  | ✅ | ← 同 `social:send`〔send_chat〕 |
| `好感` ≡`friendship` | T1 自动 |  | ✅ | ← 同 `social:friendship`〔check_friendship〕 |
| `搭话` ≡`chat` | T2 摆场 |  | ✅ | ← 同 `social:chat`〔chat_npc〕 |
| `电影` ≡`movie` | T2 摆场 |  | ✅ | ← 同 `social:movie`〔movie〕 |
| `给` ≡`give` | T3 副作用 |  | ❌ | ← 同 `social:give`〔give_item〕，见 CHANGELOG (104)④ |
| `送礼` ≡`gift` | T3 副作用 |  | ✅ | ← 同 `social:gift`〔gift_npc〕；站位另验（恒目视「走过去的，拐进柜台里，很自然」）见 CHANGELOG 92③ |
| `递给` ≡`hand` | T3 副作用 |  | ✅ | ← 同 `social:hand`〔hand_item〕 |
| `零食` ≡`snack` | T2 摆场 |  | ✅ | ← 同 `social:snack`〔snack〕 |

## `scene`（73）

| op | 层 | 参数 | 判定 | 证据 |
|---|---|---|---|---|
| `at` ⇄`cabin:interact` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:1450` |
| `berry` | T3 副作用 |  | ⏭ 没条件测 | `session_log:1189` 农场没有结果的浆果灌木（春25 早过树莓季春15-18）⇒ 摇的动作没发生；需在浆果季再验 |
| `break` ⇄`farm:break` | T3 副作用 | radius, steps | ✅ | `session_log:1544` |
| `decor` ⇄`cabin:decor` | T2 摆场 |  | ✅ | `session_log:1492` |
| `drop` | T3 副作用 | items | ✅ | `session_log:1596` |
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
| `pickup` ⇄`cabin:pickup` | T2 摆场 | tile_x, tile_y | ✅ | `session_log:1595` |
| `pickup_scene` | T3 副作用 | max_items | ✅ | `session_log:831` |
| `place` ⇄`farm:place` | T3 副作用 |  | ✅ | `session_log:1594` |
| `rock` | T3 副作用 | break_stone, dig, max_break, radius | ✅ | `session_log:1185` |
| `rummage` ≡`garbage` | T2 摆场 |  | ✅ | ← 同 `scene:garbage`〔trash_run〕 |
| `seats` | T1 自动 | radius | ✅ | `session_log:845` |
| `select` | T3 副作用 |  | ✅ | `session_log:1258` |
| `sit` | T2 摆场 | face | ✅ | `session_log:646` |
| `spot` | T3 副作用 |  | ✅ | `session_log:1362`；🫚 **09-19 姜点支路补齐**（`IsGingerTile` 此前从没样本）：IslandWest 野生姜 **9/9** 挥中（`(O)829` Ginger ×9 进包、9 格 `forageCrop` 全清、**HoeDirt 全留**、当天绿雨⇒走 `hitWithHoe` 的下雨分支 `state=1`）；IslandNorth 又 **2/2**（累计 11/11）。⚠️ **背上"走路"那层还漏**：`_walk_exact` 死等 0.6 秒 ⇒ 每次换目标都 `/position` 瞬移（**已修**，见 CHANGELOG 09-19(91)②）；**真凶在 C#**——走位器 `FindPath` 失败时 `farmer.Position = 目标` 兜底瞬移（CHANGELOG 09-19(91)③，恒拍板「不改，就这样」，已收成设计如此） |
| `stand` | T2 摆场 |  | ✅ | `session_log:647` |
| `use` | T3 副作用 |  | ✅ | `session_log:1254` |
| `丢` ≡`drop` | T3 副作用 |  | ✅ | ← 同 `scene:drop`〔drop_item〕 |
| `分段` ≡`maze_seg` | T2 摆场 |  | ✅ | ← 同 `scene:maze_seg`〔_maze_seg_view〕 |
| `可坐` ≡`seats` | T1 自动 |  | ✅ | ← 同 `scene:seats`〔seats〕 |
| `可铺` ≡`decor` ⇄`cabin:decor` | T2 摆场 |  | ✅ | ← 同 `scene:decor`〔decor_report〕 |
| `坐` ≡`sit` | T2 摆场 |  | ✅ | ← 同 `scene:sit`〔sit〕 |
| `坐下` ≡`sit` | T2 摆场 |  | ✅ | ← 同 `scene:sit`〔sit〕 |
| `家具` ≡`furniture` ⇄`cabin:furniture` | T1 自动 |  | ✅ | ← 同 `cabin:furniture`〔scan_furniture〕 |
| `座位` ≡`seats` | T1 自动 |  | ✅ | ← 同 `scene:seats`〔seats〕 |
| `拆` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `cabin:break`〔break_tile〕 |
| `拾起` ≡`pickup` ⇄`cabin:pickup` | T2 摆场 |  | ✅ | ← 同 `scene:pickup`〔furniture_pickup〕 |
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
| `放` ≡`place` ⇄`farm:place` | T3 副作用 |  | ✅ | ← 同 `scene:place`〔place_item〕 |
| `放置` ≡`place` ⇄`farm:place` | T3 副作用 |  | ✅ | ← 同 `scene:place`〔place_item〕 |
| `敲` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `cabin:break`〔break_tile〕 |
| `敲击` ≡`break` ⇄`farm:break` | T3 副作用 |  | ✅ | ← 同 `cabin:break`〔break_tile〕 |
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
| `装修` ≡`decor` ⇄`cabin:decor` | T2 摆场 |  | ✅ | ← 同 `scene:decor`〔decor_report〕 |
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
| `click` | T3 副作用 | action, button, category, option, quantity, real, right, slot | ✅ | `session_log:1546` |
| `craft` | T3 副作用 | item_name | ✅ | `session_log:548` |
| `craftables` | T1 自动 |  | ✅ | `session_log:1291` |
| `customize` ⇄`settings:customize` | T3 副作用 | farmname, favorite | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `display_fill` ⇄`festival:display_fill` | T3 副作用 | items |  |  |
| `display_takeback` ⇄`festival:display_takeback` | T3 副作用 |  |  |  |
| `donate` | T3 副作用 |  | ⏭ 没条件测 | `session_log:567` 要一座还真缺东西的博物馆（当前 95/95 全齐，没东西可捐）；✅ 但「满馆调用无副作用」已真机验收（不删展品/不吃背包物） |
| `forge` | T3 副作用 | item1, item2, mode, target | ✅ | 真机 09-20：**小屋迷你锻造台**（`Mini-Forge` Cabin(29,23)，**没去火山**）一条龙全通 —— 开台→`/forge_set` 放料→`combine`→领结果→**菜单干净关掉**(`activeMenu:None`)；`Glow Ring`+`Ruby Ring` → **`Combined Ring`「组合:Glow Ring+Ruby Ring」**（发光 + +10%攻击 两条属性都继承）；**晶石 30→10（扣 20，吻合 `GetForgeCost`）**。见 CHANGELOG (103)① |
| `geode` | T3 副作用 |  | ✅ | `session_log:555` |
| `geodes` | T3 副作用 |  | ✅ | `session_log:556` |
| `journal` ⇄`check:quest` | T2 摆场 |  | ✅ | `session_log:690` |
| `key` | T2 摆场 | hold, key | ✅ | `session_log:1108` |
| `know` | T2 摆场 |  | ✅ | `session_log:510` |
| `levelup_choose` | T3 副作用 | profession, side |  |  |
| `minigame` | T2 摆场 | action | ✅ | `session_log:1638-1663` 全验：老虎机 bet10/bet100/done（余额真扣 5300→5290→5190）；21点 hit/stand/play_again/quit/double；裸坐标 x,y 与 action 等效补牌。⚠️ `double`=**结果屏「再赌一把」**(赢局才生效、下注 100→200)，牌局中途点静默无效却回 ok —— docstring 写"加倍"会误导 AI |
| `minigame_state` | T2 摆场 |  | ✅ | `session_log:1646,1648,1650` 牌局中 / 结果屏 / 转盘滚动 三态全读到（**清掉旧 ⏭**：本轮真进沙漠赌场了） |
| `number` | T2 摆场 | confirm | ⏭ 没条件测 | `session_log:546` 要弹着数量框（节庆兑换台 / 转盘押注） |
| `read` | T2 摆场 |  | ✅ | `session_log:1453` |
| `read_book` | T3 副作用 |  | ✅ | `session_log:549` |
| `recipes` | T1 自动 |  | ✅ | `session_log:1116` |
| `sell` | T3 副作用 |  | ✅ | `session_log:866` |
| `shop` | T2 摆场 | place, want | ✅ | 真机 09-19：走过去(不再 warp)/开真菜单读 38 件/买木剑扣 250 进 Wooden Blade/回读复核/中心兜底加闸后 option0 报错 — 见 CHANGELOG 92⑥⑦ |
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
| `小游戏` ≡`minigame` | T2 摆场 |  | ✅ | ← 同 `menu:minigame`〔minigame_click〕 |
| `小游戏状态` ≡`minigame_state` | T2 摆场 |  | ✅ | ← 同 `menu:minigame_state`〔minigame_state〕 |
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
| `赌场` ≡`minigame` | T2 摆场 |  | ✅ | ← 同 `menu:minigame`〔minigame_click〕 |
| `起名` ≡`customize` ⇄`settings:customize` | T3 副作用 |  | ✅ | ← 同 `settings:customize`〔character_customize〕 |
| `跳` ≡`skip` | T2 摆场 |  | ✅ | ← 同 `menu:跳剧情`〔skip_event〕 |
| `跳剧情` ≡`skip` | T2 摆场 |  | ✅ | `session_log:1122` |
| `跳过` ≡`skip` | T2 摆场 |  | ✅ | ← 同 `menu:跳剧情`〔skip_event〕 |
| `选职业` ≡`levelup_choose` | T3 副作用 |  |  |  |
| `逛店` ≡`shop` | T2 摆场 |  | ✅ | ← 同 `menu:shop`〔shop_visit〕 |
| `配方` ≡`craftables` | T1 自动 |  | ✅ | ← 同 `menu:craftables`〔list_craftables〕 |
| `锻造` ≡`forge` | T3 副作用 |  | ✅ | ← 同 `menu:forge`〔forge〕，见 CHANGELOG (103)① |

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
| `lie_bed` | T3 副作用 | who | ✅ | `session_log:1448` |
| `pause` | ~~T3 副作用~~ | out_of_focus | 🗑️ 2026-10-04 退役（恒「失焦暂停可以退役了」）| 原 `session_log:342`；op 已撤 → AI 够不着 |
| `peek` | T2 摆场 |  | ✅ | `session_log:1411` |
| `settle` | T3 副作用 |  | ⏭ 没条件测 | `session_log:687` 要正在弹着的过夜结算菜单（且停在汇总页） |
| `sleep` ⇄`cabin:sleep` | T3 副作用 | who | ✅ | `session_log:1545` |
| `wb_clear` | T3 副作用 |  | ✅ | `session_log:641` |
| `wb_pin` | T3 副作用 | content | ✅ | `session_log:331` |
| `wb_read` | T3 副作用 |  | ✅ | `session_log:642` |
| `wear` | T3 副作用 | hand, slot | ✅ | `session_log:351` |
| `whiteboard` | T3 副作用 | content | ✅ | `session_log:639` |
| `写白板` ≡`whiteboard` | T3 副作用 |  | ✅ | ← 同 `daily:whiteboard`〔whiteboard_write〕 |
| `吃` ≡`eat` | T3 副作用 |  | ✅ | ← 同 `daily:eat`〔eat_item〕 |
| `心跳` ≡`heartbeat` | T3 副作用 |  | ✅ | ← 同 `daily:heartbeat`〔set_heartbeat_interval〕 |
| `捏脸` ≡`appearance` ⇄`settings:appearance` | T3 副作用 |  | ⏭ 没条件测 | ← 同 `settings:appearance`〔set_appearance〕 |
| `暂停` ≡~~`pause`~~ | ~~T3 副作用~~ |  | 🗑️ 2026-10-04 退役 | ← 原 `daily:pause`〔set_pause〕，op 已撤 |
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
| `go` | T2 摆场 | destination | ✅ | `session_log:1602` |
| `lookup` | T1 自动 | location | ✅ | `session_log:1103` |
| `npc` | T2 摆场 |  | ✅ | `session_log:1415` |
| `query` | T1 自动 | function | ✅ | `session_log:611` |
| `unlocks` | T2 摆场 |  | ✅ | `session_log:582` |
| `walk` | T2 摆场 | poi_name | ✅ | `session_log:1601` |
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
| `crab_bait` | T2 摆场 | bait | ✅ | 真机 09-20 **A/B 对照**：A 面先 `crab_collect` 清饵 ⇒ `/crab_pots` 4 只 `bait` 全 `None`（坐实反编译 `CrabPot.cs:312` 收笼清饵）；B 面 `crab_bait` ⇒ `✓(20,20)(20,21)(22,23)` **3/3**、回读 `bait='鱼饵'`、**背包饵 20→17（正好扣 3，一笼一颗）**。🎯 `(42,1)` 在半径 20 外 ⇒ **如实报 3/3 不冒充 4**；挪近再调 `1/1 ✓`、饵 17→16（**扫描跟着人走**）。收工 4/4 全挂回。见 CHANGELOG (103)② |
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
| `放饵` ≡`crab_bait` | T2 摆场 |  | ✅ | ← 同 `fish:crab_bait`〔_crab_bait〕，见 CHANGELOG (103)② |
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

