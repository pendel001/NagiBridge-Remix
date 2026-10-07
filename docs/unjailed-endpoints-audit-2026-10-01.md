# NagiBridge mod 侧「改世界却不校验距离」审计报告（2026-10-01）

> ## 🔄 2026-10-03 更新（这份报告是**快照**；三处结论已落地，读时连着看）
> - **`/machine_collect` 已整个删除**（C# 路由 + handler + `stardew_api.machine_collect` + `collect_machines()` + 旧脚本 `building_round.py`）。
>   恒当天把口径收成二选一：「**要么删掉收放兼容之外的所有口，要么你想留就留一个一键快捷收在单子上**」⇒ 收官选**前者**。
>   ⇒ 下面 146 行那条 5 号建议（"要么删端点，要么加距离门"）**已按"删端点"执行**；
>   凡提到 "C# 端点仍在 L2988" 的句子**都已过期**（`grep` 出来的行号也是旧的）。
> - ✅ **212-213 行那族「一键全图」已经处理完**（恒 2026-10-03 拍板「删两个孤儿 + 给门/箱子加够得着门」）：
>   · `/petall` `/waterbowl` **整个删除**（零调用点；一个反射直写 `wasPet` 假签收、一个反射猜字段名从来没成功过）；
>   · `/store_all` `/chest_take_list` `/toggle_doors` 加了 **`ReachTiles = 4`** 的「够得着」闸：
>     只对**玩家同图 ≤ 4 格**的目标生效，够不着的在回包里点名（`tooFar` / `skipped`，带坐标，**不静默丢**）；
>   · `/toggle_doors` 还支持按门坐标点名（`doorX/doorY`）—— 闸是"玩家周围 N 格"，站 B 栋门口叫"全翻"
>     会把刚翻好的 A 栋翻回去，所以消费侧改成**逐栋走位 + 点名翻**。
>   ⇒ 本报告"没加距离门/一键全图"那句**作废**；详细账见 `CHANGELOG.md` 的 **203k** 条。
> - ⚠️ **审计口径仍在**：本报告只回答"有没有距离判定"，**不回答"该不该有"** —— 上面那两条改动都是**恒拍的板**，
>   不是审计自己推出来的结论。

> ## 📖 这份文件是什么
>
> - **性质：只读审计。审计过程中没有修改任何代码**（`ModEntry.cs` 一行未动，只在最后把本报告写进这个文件）。
> - **审计对象**：`ModEntry.cs`（**22,867 行**，**活路由 171 条**）。
> - **目的**：找出「**改了游戏状态，但不校验调用者离目标够近 / 朝向对不对**」的端点——即所谓「隔空」作弊面。
> - **背景**（本次审计的起因）：
>   - `/store`、`/chest_take` 原本就是**原子直操、不校验距离**，2026-10-01（190c）才在 **Python 侧**包了一层「先走过去」（`_im_chest_op` → `/walk_to_chest`）；
>   - `/machine_collect`（一键瞬收全农场机器）**已被退役**（Python 不再暴露）——但**C# 端点仍在**（见下）；
>   - 已知 `/machine_reqs` 是 **probe 型**（`Object.PlaceInMachine(probe:true)`）。
> - **项目原则「拟人」**：动作应该「走到目标旁边再做」，不能「人站在几十格外，一个 HTTP 请求就把箱子里的东西拿走」。
> - **口径声明**：本报告的每条结论都给了**行号 + 代码片段**。凡我没读全的，一律进第 4 节「不确定/需人复核」，**不猜、不把「我没看到校验」写成「没有校验」**。

---

## 1. 路由机制 + 全量路由

`HandleRequest`（L2853-3053）用**一张按 `path` 的 switch** 分发，注册点在 **L2860**：

```csharp
2860:  object? result = path switch
2862:  { "/status" => HandleStatus(), "/move" => HandleMove(ctx), ... }
3044:      _ => throw new InvalidOperationException($"Unknown endpoint: {path}")
```

- `method`（L2856）读了但**几乎不用**——作者自己在 L12233 注明：「路由是**按 path 分发**的（HandleRequest 读了 method 却从不用）」。全文件只有 `/pan`（L7802）和 `/guard`（L12246）真按 method/body 分流。
- **活路由 171 条**。`/quest_accept` 已注释退役（L2976-2979），`/mastery_claim` 已删除（L3002-3005）。`/machine_collect` **仍在路由表里**（L2988）。
- ⚠️ 推论：**除 /pan、/guard 外，所有端点 GET 也能改世界**。

<details>
<summary><b>全部 171 条路由（点开）</b></summary>

**只读/诊断（约 80）**：`/status /weapon_diag /decor /sittable /passable /passable_rect /state /surroundings /warps /farm_buildings /doors /fish_pond /trinkets /rings /worn /alerts /appearance_info /appearance_ref /appearance_creation /hair_ref /map /minigame_state /focus /buffs /friendship /chest /scan_chests /menu /dump_tile /tile_props /mine_rock /crab_pots /profile /recipes /craft_recipes /museum_debug /museum_tiles /museum_diag /quest_list /quest_progress /qi_shop /debris /machines /farm_report /animals /scan /bombs /silo /mastery /special_items /achievements /carpenter /mine_debug /mine/elevator /festival /event_state /festival_data /egg_tiles /unlocks /gsq /minecarts /unlock_debug /chat/history /mail /bundles /progress /nuts /sprinklers /petbowl /ready_state /screenshot /screenshot_portrait /pool /ladder`

**改状态（约 91）**：`/move /tool /interact /furniture_pickup /furniture /chat /emote /trinket /ring /equip /appearance /character_customize /color_pick /stop /stand /buy /face /select /use /pan /sleep /wakeup /queue /key /minigame_click /eat /set_pause /guard /warp /warp_into /warp_building /walk_to /position /pause /resume /give /drop /gift /money /refill /heal /ripen /sell /sell_to_shop /harvest /store /store_all /name_chest /chest_color /chest_open /chest_take /chest_take_list /placechest /fishbot /menu/click /menu/number /menu/claim_swap /menu_close /open_questlog /forge_set /water /crab_retract /levelup_choose /click /click_tile /drag /craft /cook /process_geode /process_geode_batch /museum_donate /qi_buy /museum_remove /clear_ground /drop_item /machine_collect /machine_load /machine_reqs /petall /waterbowl /buy_animal /till_area /tool_area /dig_spot /toggle_doors /crawl_bed /cancel_sleep /settlement_confirm /zoom /resolution /chat/push /hud`
</details>

### 全局基线：全文件**只有 5 处**真正的距离/邻接判据

我 grep 了 `Distance / TileDistance / within / adjacent / IsWithinPlayerThreshold / Intersects / IsCloseEnoughToFarmer / GetBoundingBox / Math.Abs` 并逐处读，得到的**完整清单**是：

| # | 位置 | 判据 | 强度 |
|---|---|---|---|
| 1 | **L7846** `/pan` | `if (!farmer.GetBoundingBox().Intersects(rect))` → 报「离闪光点太远」 | ✅ 真校验 |
| 2 | **L8776-8778** `/crawl_bed` | `Math.Abs(farmer.TilePoint.X-bedX)<=3 && …Y<=3`，不满足明确报错 | ✅ 真校验 |
| 3 | **L16088-16094** `/cook` | `FindKitchenTile` + `Math.Abs(_pt.X-kx)>1 \|\| …>1` 拒绝 | ✅ 真校验 |
| 4 | **L11897-11899** `/harvest` | `Math.Abs(pos.X-farmer.TilePoint.X) > radius` 过滤，**radius 默认 15、上限 50** | ⚠️ 过弱 |
| 5 | **L12141-12144** `/ripen` | 同款，**radius 默认 30**（≈整张农场） | ❌ 等于没校验 |

> （另 L3609 / L3630 家具预测里的 `IsCloseEnoughToFarmer(who)` 被 `loc.CanFreePlaceFurniture() ||` **短路**，L3587-3588 注释自承「装修图里距离检查一次都不求值」。）
> **没有任何共享的 `EnsureNear()` / `RequireNear()` 类助手**（grep 零命中）。

作者的设计立场有明文，`/chest_open` 注释 L11612-11616：

```
L11612: ⚠️ **拟人那条在调用方（Python）**：先 /walk_to 走到箱子边、手够得着，再敲这个端点。
L11613:    这里**只管开** —— 端点里不做走位（走位是导航的活，两处各写一份必然漂）。
L11616:    —— **没有**距离前置条件、也**不吃** `GetMutex()`（那套是"玩家右键碰箱子"走的路）。
```

**即：截至审计时点，「拟人」是 Python 侧约定，C# 侧普遍没有门。**

---

## 2. 主表 —— 改世界 + 无距离/朝向校验（「隔空」）

| 端点 | 改什么 | 距离/朝向校验 | 证据（行号 + 代码） | 结论 |
|---|---|---|---|---|
| `/money` | 玩家金币 | **无**（只读 `amount`） | `L12189: Game1.player.Money += amount;` | **隔空** |
| `/give` | 凭空造任意物品；`upgrade` 可直接给铱级工具 | **无** | `L12169: var item = ItemRegistry.Create(itemId, count);` `L12171: tool.UpgradeLevel = upgrade;` `L12172: farmer.addItemToInventory(item);` | **隔空** |
| `/buy` | 扣钱 + 给物；**价格可由请求方指定** | **无**（不开 ShopMenu、不见商人） | `L8132-8134: unitPrice = priceOverride>=0 ? priceOverride : obj.salePrice()*2` `L8162: farmer.Money -= totalCost;` | **隔空** |
| `/buy_animal` | 扣钱 + 凭空建 FarmAnimal 塞棚 | **无**（不经 Marnie/购买菜单） | `L8366: new FarmAnimal(...)` `L8371: indoors.animals.Add(...)` `L8374: farmer.Money -= price;` | **隔空** |
| `/qi_buy` | 扣 QiGems + 给物 | **无**（Qi 商店要人在场） | `L17530-17532: farmer.QiGems -= totalCost; …addItemByMenuIfNecessary(item);` | **隔空** |
| `/chest_take` | 任意 `(x,y)` 箱子取物 | **无** | `L11699: FindStorageChestAt(loc, cx, cy)` → `L11739-11741: item.Stack -= want; chest.Items[i] = null;` | **隔空**（已知先例，C# 侧确实无门） |
| `/chest_take_list` | **扫全图所有箱**一次取齐 | **无** | `L11803: CollectStorageChests(loc)` `L11820: foreach (var (chest,_,_) in chests)` `L11852: chest.Items[i] = null;` | **隔空（全图）** |
| `/chest_open` | 远程开箱（挂 `ItemGrabMenu`） | **无**，作者注明在 Python | `L11636-11643: FindStorageChestAt(...) → chest.ShowMenu();` + L11612-11616 注释 | **隔空** |
| `/store` | 把背包物塞进任意 `(x,y)` 箱 | **无** | `L10532: FindStorageChestAt(loc, cx, cy)` `L10579: var leftover = chest.addItem(item);` `L10584: farmer.Items[i] = null;` | **隔空**（已知先例） |
| `/store_all` | 全场景箱智能归位 | **无** | `L10668: var chests = CollectStorageChests(loc);` `L10861: target.addItem(moveItem);` | **隔空（全图）** |
| `/placechest` | 凭空在 `(x,y)` 造一口玩家箱 | **无**（只查该格有无物件） | `L10499: ItemRegistry.Create<Chest>("(BC)130")` `L10502: loc.objects.Add(tileVec, chest);` | **隔空**（L10495 注释称 Python 无调用方） |
| `/machine_collect` | 一键收全农场机器产物 | **无** | `L19022: foreach (… in EnumerateMachines(locs, type))`（location 空 → `ResolveLocations` 全农场，L19284-19299）`L19044-19045: heldObject.Value=null; readyForHarvest.Value=false;` | **隔空（全农场）** |
| `/machine_load` | 逐台机器放料 | **无**，但**自己瞬移**过去 | `L19137: RelocateActor(loc, tile);` → `L19444: farmer.currentLocation = loc;` `L19452: farmer.Position = new Vector2(...)*Game1.tileSize;` | **自己瞬移**（L19135-19137 自述） |
| `/petall` | 全农场**所有**动物 `wasPetToday=true`（含棚内） | **无**（人不在场，反射写字段） | `L19815: foreach (var building in farm.buildings)` `L19832-19839: f.SetValue(animal, true);` | **隔空（全农场）** |
| `/waterbowl` | 直接写 `petBowlWatered = true` | **无** | `L19910-19918: f.SetValue(farm, true);`（Method1 反射直写；Method3 才用浇水壶） | **隔空** |
| `/toggle_doors` | 切换**所有**畜舍门 | **无** | `L17784: foreach (var building in farm.buildings)` `L17799: netBool.Value = !netBool.Value;` | **隔空（全农场）** |
| `/clear_ground` | 删掉 `(x,y)` 的 debris/objects/terrainFeatures/大型地形 | **无** | `L17567-17581: loc.debris.Remove(d) / loc.objects.Remove(tileVec) / loc.terrainFeatures.Remove(tileVec) / loc.largeTerrainFeatures.Remove(ltf)` | **隔空** |
| `/dig_spot` | 任意格挖蚯蚓/姜 | **无**（注释自承「玩家在旁自动进包」） | `L15273: loc.digUpArtifactSpot(x, y, farmer);` | **隔空** |
| `/crab_retract` | 删任意图上的蟹笼 + 收产出 | **无**（还能 `location` 指定别的地图） | `L15429-15430: getLocationFromName(locName)` `L15459: loc.objects.Remove(tile);` | **隔空（跨图）** |
| `/till_area` | 任意格表/矩形**直接造 HoeDirt** | **无**（只查 Diggable/占用） | `L17975-17977: var dirt = new HoeDirt(); … loc.terrainFeatures[vec] = dirt;` | **隔空** |
| `/ripen` | 半径内作物 `growCompletely()` | 有，但**默认 30 格** | `L12125-12127: int radius = 30;` `L12141-12144: if (Math.Abs(...) <= radius …) dirt.crop.growCompletely();` | **隔空**（30 格≈全图） |
| `/harvest` | 半径内批量收作物 | 有，但**默认 15 格** | `L11881-11883: int radius = 15; …r <= 50` `L11897-11899` | **隔空（近拟人）** |
| `/process_geode` + `/process_geode_batch` | 任意地点砸晶球，扣 25g 给产出 | **无**（不需 GeodeMenu/铁匠） | `L16350-16359: farmer.Money -= COST; Utility.getTreasureFromGeode(obj);` `L16420-16465` | **隔空** |
| `/museum_donate` | 直写 `netWorldState.MuseumPieces` 捐赠 | **无**（不需 MuseumMenu） | `L18604: Game1.netWorldState.Value.MuseumPieces[vKey] = "_diag_";`（+后续真捐） | **隔空** |
| `/museum_remove` | 直接从博物馆撤展 | **无** | `L16728-16732: archaeologyFound.Remove(id); … np.Remove(vec);` ⚠️ 且 `EnqueueMainThread` **之外**就 `return {ok=true}`（L16735）——无论里面成不成都说成功 | **隔空** |
| `/sell` | 背包物直接塞出货箱 | **无**（只要求人在 Farm 且箱存在） | `L11929-11931: farm.getShippingBin(farmer)` `L11959-11960: bin.Add(item); farmer.Items[i] = null;` | **隔空** |
| `/refill` | 水壶直接灌满（不查水边） | **无** | `L12053: wc.WaterLeft = wc.waterCanMax;` | **隔空** |
| `/heal` | health/Stamina 拉满 | **无** | `L12068-12069: f.health = f.maxHealth; f.Stamina = f.MaxStamina;` | **隔空** |
| `/sleep` `{stay:true}` | 在**任何地点**强制入睡 | **无** | `L8431-8433: farmer.isInBed.Value = true; … answerDialogueAction("Sleep_Yes", …)` | **隔空** |
| **`/interact` `{x,y}`** | 远程 `checkAction` 任意格（+家具/鱼塘/蟹笼兜底） | **无**（传 x,y 时完全不看人在哪） | `L3405-3408: loc.checkAction(new Location(targetX,targetY), …)`；兜底 `L3716: f.checkForAction(farmer)`、`L3752: fp.doAction(...)`、`L3435 TryCrabPotInteract` | **隔空**（不传 x,y 才用面前格） |
| **`/use` `{x,y}`（放物）** | 任意格 `placementAction`（放机器/蟹笼/种子/地板） | **无**，注释自承「站格/面朝无关」 | `L7704 注释` `L7719: if (ppx>=0 && ppy>=0) { placeX=ppx; placeY=ppy; }` `L7746: obj.placementAction(loc, px, py, farmer)` | **隔空** |
| `/fishbot` | 开关第三方 Fishbot 全自动钓鱼 | **无**（无距离概念） | `L12343: Helper.ModRegistry.Get("AdroSlice.Fishbot")` `L12375-12381: 写 AutomationEnabled` | **隔空**（性质=开外挂开关） |

### 主表 —— 自己瞬移（不是隔空，但同样非拟人）

| 端点 | 证据 | 结论 |
|---|---|---|
| `/warp` | `L9638: farmer.Position = new Vector2(x,y)*tileSize;` `L9643: Game1.warpFarmer(location,x,y,false);` | 自己瞬移 |
| `/warp_into` | `L9771: farmer.currentLocation = targetLoc;` `L9772: farmer.Position = new Vector2(x,y)*tileSize;` | 自己瞬移 |
| `/warp_building` | `L9667` 注释：走不进就只能用 `farm.buildings.indoors` 引用直切 | 自己瞬移 |
| `/position` | `L9999: farmer.Position = new Vector2(x,y)*tileSize;` | 自己瞬移 |
| `/sleep`（非 stay） | `L8488: Game1.warpFarmer(bedLocName, bedX, bedY, false);` + `L8543: f.Position = …` 贴床 | 自己瞬移 |
| `/gift` | `L11307-11311: // 站到目标下方一格 … farmer.Position = new Vector2(tt.X, tt.Y+1)*Game1.tileSize; farmer.FacingDirection = 0;` → `L11315-11319: loc.checkAction(facing…)` | 自己瞬移 |
| `/festival/interact` | `L21147-21161: 挑可站邻格 → farmer.Position = new Vector2(standX, standY)*tileSize;` | 自己瞬移 |
| `/machine_load` | `L19137 → L19439-19452 RelocateActor`（自述「不走 warpFarmer，直接设 currentLocation」） | 自己瞬移 |
| `/tool_area` | 主体走 BFS 接近锚点，**但兜底瞬移**：`L2582-2591 if (path==null\|\|Count==0) { farmer.Position = …; _commandResults.Add(new{… teleported = true }); }`；`L2536` 到位后又直接对齐 Position | **拟人+瞬移兜底** |

### 主表 —— 拟人 / 间接（不用改）

| 端点 | 依据 | 结论 |
|---|---|---|
| `/tool` | 打的是玩家**面前格**：`L3238 / L3268: var facingTile = GetFacingTile(farmer);` → `DoFunctionHere(...)` | 拟人（朝向即距离） |
| `/use`（Tool 分支） | `L7670-7673: GetFacingTile(farmer)` → `L7695-7696: BeginUsingTool/EndUsingTool` | 拟人 |
| `/interact`（不传 x,y） | 面前格 + 脚下格，`L3454-3468` | 拟人 |
| `/pan` | `L7846: if (!farmer.GetBoundingBox().Intersects(rect))` → `L7848: 离闪光点太远，走近岸边再淘` | **拟人（真校验）** |
| `/cook` | `L16088-16094: FindKitchenTile(…) + Math.Abs(_pt.X-kx)>1 \|\| …>1` → 拒绝 | **拟人（真校验）** |
| `/crawl_bed` | `L8776-8778` ≤3 格，否则 `L8783-8784: 请先用 walk_to 把 … 带到床附近` | **拟人（真校验）** |
| `/craft` | 只需自身材料（原版合成随处可做） | 拟人 |
| `/drop_item` | `L17739: Game1.createItemDebris(thrown, farmer.getStandingPosition(), farmer.FacingDirection)` | 拟人 |
| `/key`、`/click`、`/click_tile`、`/drag` | 真实 OS 输入：`L13582-13590: setMousePosition / SetForegroundWindow / SetCursorPos / mouse_event(...)`；`/key` 走 `keybd_event` | 拟人（输入通道，天然受原版邻接约束） |
| `/walk_to` | 越界/站不住校验 `L21739-21768`（越界 `ok:false`、站不住退最近可走格），之后 BFS 走位 | 拟人（走位原语） |
| `/guard` | 自述 `L12265: guard = 每 tick 找 3×3 内的怪，转身+挥近战武器，不移动` | 自动化开关（范围自限 3×3） |
| `/menu/click`、`/menu/number`、`/menu/claim_swap`、`/menu_close`、`/open_questlog` | `L13752-13757: menu == null → "No menu open"` | 间接（靠调用方先开菜单） |
| `/sell_to_shop` | `L11982-11987: if (menu is not ShopMenu) → "No shop menu open"` | 间接 |
| `/forge_set` | `L14814-14818: if (Game1.activeClickableMenu is not ForgeMenu forge) → "锻造台没开"` | 间接 |
| `/levelup_choose` | `L15885-15893: 需 LevelUpMenu 且 isProfessionChooser` | 间接 |
| `/select` | 只改 `farmer.CurrentToolIndex`（自身手持格，L7324-7345 搜索） | 无关/自身 |

---

## 3. 风险排序（「隔空」按危害降序）

1. **`/money`** —— 一句 `Money += amount`。**不包走位，AI 能给自己刷任意金币**，经济系统（买卖/礼/任务）当场失去意义；而且它没有「目标对象」，**加走位也救不了**。
2. **`/give`** —— 任意 `itemId` + 任意数量，还能 `upgrade` 直接给铱级工具。**AI 一个请求就能拿到全游戏任意物品/满级工具**，跳过所有玩法与进度。
3. **`/buy`（+ `price` 参数）** —— 不用去商店，**还能自己指定价格**（`price: 0`）。AI 可白拿任何商店商品，且「有没有钱」都不再是约束。
4. **`/chest_take_list` + `/store_all`** —— 全图所有存储箱一次搬空/塞满。**人在小屋，全农场箱子被远程清空**（`/store_all` 还能按颜色/名字挑箱，等于远程整理）。
5. **`/machine_collect`（已退役但仍在 L2988）** —— 全农场机器一键收。**任何直连 HTTP 的脚本（非 MCP 路径）立刻复现已废除的作弊**，Python 退役对它没有任何约束力。
6. **`/chest_take` + `/store` + `/chest_open`** —— 单箱原子直操。Python 包了走位，但 **C# 侧仍无门**：新脚本、调试直调、任何绕过 MCP 的路径立刻回到「隔空取物」。
7. **`/qi_buy`** —— 齐币换物不落地。**不用去姜岛、不用见 Qi**。
8. **`/buy_animal`** —— 不用去 Marnie，凭空生成动物+扣钱。历史上还真把牛塞进过鸡舍（`L8294-8306` 注释记录恒真机事故）。
9. **`/petall` + `/waterbowl` + `/toggle_doors`** —— 全农场动物/水碗/门一键处理。**「日常照料」整条玩法被一句话抹平**；门全开还会让动物夜里乱跑、影响次日产出。
10. **`/ripen`** —— 半径 30 格（覆盖整张农场）直接催熟。**跳过所有生长等待**，等于把农业玩法的时间轴删掉。
11. **`/harvest`** —— 半径 15（31×31 格）。**人站在农场一角能收半个农场**的作物（顺手还会 `destroyCrop`）。
12. **`/process_geode` + `_batch`** —— 任意地点砸晶球。铁匠铺与 GeodeMenu 玩法消失，可批量刷矿物/古物。
13. **`/museum_donate` + `/museum_remove`** —— 远程捐/撤展。博物馆进度随意改；且代码里已记录过「满馆再捐会删掉真展品」的事故（`L18593-18599`）；`/museum_remove` 还**无条件回 ok:true**。
14. **`/till_area`** —— 凭空造耕地。**无需锄头、无需体力，整片地瞬间翻好**（注释自承还绕过了游戏的雨天自动浇湿，L17969-17974）。
15. **`/clear_ground`** —— 一格请求删掉该格的物件/地形/大型地形。**AI 能「隔空」删除箱子、树、巨石**，不可逆且无提示。
16. **`/dig_spot`** —— 远程挖蚯蚓/姜。古物/种子直接刷，不用走过去。
17. **`/crab_retract`** —— 远程收笼，**还能 `location` 指定别的地图**：全图蟹笼一次清空。
18. **`/interact {x,y}`** —— 通用远程 `checkAction`。**这是「隔空」的总后门**：开箱菜单、开铁砧菜单、开升级菜单、放鱼塘、献祭板……很多「间接」端点的**前置**都能被它远程满足。
19. **`/use {x,y}`** —— 远程放置。**能隔空把机器/蟹笼/种子摆到任意格**（注释自承「站格/面朝无关」），绕过走位与朝向。
20. **`/sell`** —— 远程出货。**背包一键变钱**，不用走到出货箱。
21. **`/refill` + `/heal` + `/sleep{stay}`** —— 无代价回满水/血/体力、在任意地点过夜。**体力、血量、日夜三大资源机制失效**。
22. **`/gift` + `/festival/interact` + `/machine_load`（自己瞬移）** —— 不是隔空而是瞬移，从「像真人」的验收角度同样是作弊；`/gift` 还会把玩家**硬塞到对方身下格**（同款事故先例见 `L21140-21144`：「被这一行摁进水里」）。
23. **`/fishbot`** —— 打开第三方全自动钓鱼 mod。**直接开外挂开关**。

---

## 4. 不确定 / 需人复核（**我没读全，不下结论**）

| 端点（行号） | 我卡在哪 |
|---|---|
| `/name_chest`(10924-10973)、`/chest_color`(10973-11059) | 按 `x,y` 改世界对象（箱名/颜色），**未逐行读实体** ⇒ 有无距离门未确认（低危） |
| `/equip`(6240-6453)、`/ring`(6005-6177)、`/trinket`(5950-5993)、`/rod`(6177-6240) | 只从 Items 写入的 grep 判定它们在动 `farmer.Items[*]`（自身背包），**未逐行读**，不能断言无其它世界副作用 |
| `/gift`(11121-11431) | 只读了 11295-11334（含瞬移）；**好感/友谊写入路径未逐行读** |
| `/dance_invite`(21290-21375) | 只读到 21334（NPC 被明确拒绝、要求走自然流程）；**玩家分支未读完** |
| `/appearance`(6882-7006)、`/character_customize`(14520-14606)、`/color_pick`(14606-14696) | 外观/捏脸（AGENTS 标注不可逆），**未读实体**；推测是自身属性、与「站在哪」无关，未验证 |
| `/emote`(4970)、`/chat`(4357)、`/chat/push`(4401)、`/hud`(4461) | 广播类，**未读实体**；性质是聊天/HUD 而非世界状态 |
| `/set_pause`(9890)、`/pause`(12195)、`/resume`(12204)、`/stop`(10402)、`/stand`(10430)、`/cancel_sleep`(9135)、`/ready_state`(9279)、`/zoom`、`/resolution` | 状态开关，**未读实体** |
| **`/settlement_confirm`(9172-9279)** | **会推进「日结算」**，我认为它值得单独优先复核——可能与 `/sleep` 同属「无代价跳日」 |
| `/menu/click` 的商店购买分支(13730-14415) | 只读到 13824；**商店买/卖分支未读完**，其中可能有不依赖菜单的分支 |
| `/menu/number`(14415)、`/menu/claim_swap`(13646) | 未读实体；需确认它们不会自己开菜单或点开放世界格子 |
| `/carpenter`(20361)、`/mine_debug`(20614)、`/bombs`(20565)、`/mine_rock`(15215)、`/unlocks`(21375)、`/progress`(4581)、`/nuts`(4749)、`/bundles`(4836)、`/fish_pond`(5732)、`/sittable`(4021)、`/decor`(3935)、`/tile_props`(15103)、`/pool`(14998)、`/ladder`(20416-20565) | 看起来只读/诊断，但**未逐处确认无隐藏写入**。具体两处疑问：`/ladder` 里的 `spawned` 变量（L20440-20452 从字段读出）**是否会被回写**？`/pool` **是否改 `swimming`/`bathingClothes`**？ |
| `/tool_area` 主体 18170-18563 | 我只读了 18097-18170 + 执行器/命令生成（2478-2600、18303-18359）+ 执行路径；**中间的 `IsTillTarget`/`IsWaterTarget` 与补漏段未逐行读**。我**没有找到**「把目标矩形限制在玩家附近」的判据 ⇒ 倾向「不限制，但会走过去」 |
| `/machine_reqs` 19199-19252 | 我确认了 `L19252 probe:true` 且只构造返回字典（L19256-19273），但**解析段未读完** |
| **全局** | 除 `/pan`、`/guard` 外**所有路由不区分 GET/POST**，`/status` 的 `caps` 也未声明这一事实 |

---

## 5. 真生效 vs probe（只问不做）

**probe / dry-run（只问不做）**

| 端点 | 证据 |
|---|---|
| `/machine_reqs` | `L19252: canPlace = obj.PlaceInMachine(md, probeItem, true, farmer, false, false);`（`probe:true`）——与已知线索一致 ✅ |
| `/pan`（GET） | `L7812-7827: 读 orePanPoint/背包，只报快照，不调 DoFunction` |
| `/guard`（不带 `on`） | `L12246-12267: 只报 _guardOn/_guardSwings/… 直接 return` |
| `/mine/elevator` | `L20714-20715: 用 throwaway MineElevatorMenu 反射读楼层，不点任何按钮` |
| `/achievements`(HandleAchievementProbe)、`/unlocks`、`/bundles`、`/progress`、`/crab_pots`、`/sprinklers`、`/debris`、`/scan_chests`、`/chest`(L11496-11501 只列 items)、`/machines`、`/farm_report`、`/state`、`/surroundings` | 只构造返回 |

**顺带发现的「会报成功但可能没生效」陷阱**：`/museum_remove` 的写入在 `EnqueueMainThread` 里，但 `L16735: return new { ok = true, removed = id };` 在**队列外**——**无论里面成功与否都回 ok:true**，是典型的「工具说成功但事没发生」。

**其余全部真生效**（上面主表里都给出了具体写入行）：/money /give /buy /buy_animal /qi_buy /chest_take /chest_take_list /chest_open /store /store_all /placechest /machine_collect /machine_load /petall /waterbowl /toggle_doors /clear_ground /dig_spot /crab_retract /till_area /tool_area /harvest /ripen /process_geode(_batch) /museum_donate /museum_remove /sell /refill /heal /sleep /interact /use /gift /festival_interact /warp /warp_into /warp_building /position /walk_to /cook /craft /drop_item /key /click /click_tile /drag /fishbot。

---

## 6. 我认为最该先修的三条 + 理由

**① `/money` + `/give` —— 端点层面删掉或加白名单。**
理由：这两个**没有任何「目标对象」**，「先走过去」这个约束在语义上就不适用——加走位也救不了。它们是唯二能「凭空创造世界资源」的口子（钱、任意 `itemId`、甚至 `upgrade` 满级工具），一条 curl 就能让整局游戏失去意义。参照已退役的 `buy_item`/`accept_quest`，正确做法是**从路由表里摘掉**（或只留 host 端口），而不是靠 Python 约定。

**② 「一键全图」家族：`/chest_take_list` + `/store_all` + `/machine_collect` + `/petall` + `/waterbowl` + `/toggle_doors`。**
理由：它们的共性是**把「一个地点、一个目标、一个动作」变成「全地图一次完成」**。即使 Python 老老实实包了走位，也只会变成「走到一个箱子面前，却把全农场的箱子收了」——拟人判据在这里**必然失效**，且 Python 包一层根本管不住。另外 `/machine_collect` 的 **C# 端点仍在 L2988**，「退役」只发生在 Python ⇒ 任何直连脚本（调试、新写的自动化、别的客户端）立刻复现。这六条应该**要么删端点，要么在 C# 里强制加 `location + 单目标 + 距离门`**。

**③ `/interact {x,y}` + `/use {x,y}` —— 给坐标分支加邻接门（切比雪夫 ≤1 或与玩家包围盒相接）。**
理由：这两个**不是「某个玩法」，而是其它端点的前置**：开箱子菜单、开铁砧菜单、开升级菜单、开献祭板、放机器/蟹笼/种子……全走它们。**只要它们能隔空 `checkAction`/`placementAction`，整个「先走过去」的约束就能被整体绕开**——把 ② 里每个业务端点都包上走位，仍然会被这两个捅穿。而且代价极小：不传 x,y 时它们本来就走面前格（已经是拟人的），只需给坐标分支加一道门，不满足就**明确报错**而不是静默执行。

> 附带建议：目前全文件**零共享邻接助手**。加一个 `RequireNear(tile, maxChebyshev)` / `RequireFacing(tile)` 统一入口，并把它写进 `/status` 的 `caps`（L3129 那段已有先例），既能让 Python 端 `_im_caps()` 问得出「这版 DLL 认不认这道门」，也能避免以后再出现「五处校验、七十处没有」的漂移。

---

## 📌 下次接手怎么用

### A. 哪些结论是「grep + 读代码」得出的（可信度高，可直接引用）

这些**都有行号 + 代码片段**，且是我**实际读过**的（下面括号里是我读过的行段）：

| 结论 | 我实际读了 | 手段 |
|---|---|---|
| 路由＝按 path 的 switch，171 条活路由，method 几乎不用 | L2853-3053（整段读） | 通读 + 手数 |
| 全文件**只有 5 处**真距离/邻接判据 | L7846 附近、L8764-8810、L11875-12195、L16050-16094 | **grep 全文件**（`Distance/Intersects/IsCloseEnoughToFarmer/Math.Abs/…`）+ 逐处读 |
| **没有任何共享 `EnsureNear` 类助手** | — | grep 零命中（`EnsureNear/RequireNear/CheckNear/MustBeClose/AtTarget/Proximity`） |
| `/money`、`/give`、`/heal`、`/refill`、`/ripen`、`/harvest`、`/sell`、`/sell_to_shop`、`/eat` | L11875-12195（连续整段读） | 通读 |
| `/store`、`/store_all`、`/name_chest` 头 | L10508-10927 | 通读 |
| `/chest`、`/scan_chests` 头、`/chest_open`、`/chest_take`、`/chest_take_list` + `FindStorageChestAt` | L11474-11874 | 通读 |
| `/machine_collect`、`/machine_load`、`/machine_reqs`（probe 确认） | L18999-19281 | 通读 |
| `/dig_spot`、`/water`、`/crab_pots`、`/crab_retract`、`NormalizeCrabStacks` | L15252-15519 | 通读 |
| `/clear_ground`、`/debris`、`/drop_item`、`/toggle_doors`、`/sprinklers`、`/till_area` | L17546-18089 | 通读 |
| `/petbowl`、`/petall`、`/waterbowl` | L19515-19980 | 通读 |
| `/buy`、`/buy_animal` | L8100-8407 | 通读 |
| `/qi_buy` | L17466-17545 | 通读 |
| `/tool`、`/weapon_diag`、`/interact`（含 `TryFurnitureInteract` 等兜底） | L3202-3638 | 通读 |
| `/use`（含 `read`/`scepter`/放置分支） | L7586-7786 | 通读 |
| `/pan` 的射程门 | L7797-7866 | 通读 |
| `/tool_area` 执行器 + BFS 瞬移兜底 | L2470-2609、L2770-2791、L17931-18170、L18303-18359 | 分段通读 |
| `/walk_to` 入口校验 | L21696-21773 | 通读 |
| `/menu/click` 头部（"No menu open" + 对话选项） | L13730-13824 | 通读 |
| `/craft` 头、`/cook` 位置门、`/levelup_choose` 菜单门 | L15940-16009、L16050-16094、L15871-15915 | 通读 |
| `/forge_set`（需 ForgeMenu）、`/process_geode`、`/museum_donate`、`/mastery`（只读） | L14799-14860、L16325-16384、L18563-18662、L20170-20237 | 通读 |
| `/museum_remove`（隔空 + 无条件 ok:true） | L16712-16736 | 通读 |
| `/placechest`、`/click_tile` | L10461-10506、L13564-13600 | 通读 |
| `/sleep`（stay 分支 + warp 回家）、`/crawl_bed` 的 ≤3 门 | L8407-8501、L8621-8700、L8764-8810 | 分段通读 |
| `/gift` 的瞬移、`/festival/interact` 的瞬移、`RelocateActor` | L11295-11334、L21125-21179、L19432-19453 | 分段通读 |
| `/chest_open` 作者的「拟人靠 Python」自述 | L11576-11619 | 通读 |
| 全局 `farmer.Position =` / `warpFarmer` / `currentLocation =` 分布 | — | grep 全文件（36 命中，逐条归类） |
| `/chest`、`/fishbot`、`/guard`、`/dance_invite` 头部 | L11474-11504、L12327-12381、L12231-12280、L21290-21334 | 通读 |

### B. 哪些**没读全**（引用时必须打问号）

**就是第 4 节那张表**，其中我认为最该优先补读的四个：

1. **`/settlement_confirm`(9172-9279)** —— 唯一可能「无代价跳日」的端点，比 `/sleep{stay}` 影响更大（跳日＝作物生长/动物产出/重建）。
2. **`/interact {x,y}` 的兜底族** —— 四个 helper 的**真实起始行**（已 grep 复核）：`TryFurnitureInteract` **L3707**、`TryFishPondInteract` **L3733**、`TryCrabPotInteract` **L3771**、`TryJunimoNoteInteract` **L3834**。
   我读了调用点（L3420-3448、L3481-3528）与 **`TryFurnitureInteract` / `TryFishPondInteract` 的实现**（L3707-3762），**`TryCrabPotInteract`(L3771 起) 与 `TryJunimoNoteInteract`(L3834 起) 的实现段没读** —— 它们同样是无距离兜底，需补读确认。
3. **`/menu/click` 的商店买/卖分支**(13825-14415) —— 若存在「不依赖 ShopMenu 直改钱/物」的分支，会改变第 3 节的排序。
4. **`/pool`(14998-15103)** 与 **`/ladder`(20416-20565)** —— 我按「只读」归类是**推断**（它们的函数名/注释像诊断），但没逐行确认没有写 `swimming` / 没回写 `ladderHasSpawned`。

另外**方法学限制**（请知悉）：
- 我在本会话中**无法开子代理**（`subagent depth 2 exceeds maxDepth 1`），所以全部是**我自己串行读**的——覆盖面上「读全的」都是整段通读，「没读全的」都已在第 4 节列明，**没有任何一条结论是靠「名字像只读」直接下的**（凡这么推断的，都进了第 4 节）。
- `caps`（L3129 起）我只确认它存在、且注释要求「加/改端点时必须同步这里」，**没有逐条核对 caps 与 171 条路由是否一一对应**。

### C. 建议的验证方式（真机 A/B，别靠读代码下最终结论）

对每一条「隔空」结论，**用同一存档、同一角色、同一位置做两组对照**，判据是「游戏屏幕上的画面」而不是回包里的 `ok`：

```
【A 组·不包走位】把角色留在离目标最远的地方（例如小屋/镇口）
   → 直打 7843 的端点（curl，绕开 MCP，别用域工具）
   → 记录：① 回包 ② 屏幕上目标是否真的变了（箱子空了？钱变了？地翻了吗？）
   预期：多数「隔空」端点 A 组就会成功 ⇒ 结论成立

【B 组·包走位】先 /walk_to 走到目标旁边再打同一端点
   → 记录同样两项
   判据：A、B 两组**结果一样** ⇒ 该端点**自带作弊面**（距离门不存在）
        A 失败 / B 成功 ⇒ 该端点**原本就有门**（说明我漏读了，回去补读）
```

**优先做 A 组的三条**（投入最小、信息量最大）：
1. `/money` + `/give`（一条 curl，看钱和背包）—— 预计 A 组直接成功。
2. `/chest_take_list` 站在**另一个地图**打（例如人在镇口，箱在农场）—— 测「跨图是否也生效」，这比同图更有说服力。
3. `/interact {x,y}` 人在镇口，指一个农场里的箱子格 —— 这是验证 ③ 号建议是否必要的最直接 A/B。

**另外两个便宜的对照（不用真机）**：
- `grep -n "GetFacingTile\|TilePoint\|Math.Abs\|Intersects" ModEntry.cs` 自己数一遍那 5 处门（本报告的基线），若数量变了说明 DLL 比这份报告新。
- 比三处 DLL 哈希（`bin\Debug\net6.0` / **本机各份安装的 Mods**，本机活的那份不一定在 Steam 默认目录）——**报告的行号只对得上某个特定编译版本**，换版本后行号会漂，先对哈希再引用行号。

### D. 本文件的使用边界

- 这是**审计快照**，不是规范；**改代码前请先查 `CHANGELOG.md`**（项目规矩），本文件**没有**、也**不应该**写进 CHANGELOG。
- 引用行号时请注明「对应 `ModEntry.cs` 22,867 行、活路由 171 条的那一版」。
