"""
星露谷地图连接 + POI 知识库
AI 用这个规划路线："先去钓鱼，再挖矿，最后种地"
"""

# ── 地图连接表 ──
# (起点, 出口方向, 终点, 终点入口坐标)
ROUTES = [
    # 农场 ↔ 外部
    ("Farm",         "right", "BusStop",     (10, 23)),   # 口= (79,17)
    ("Farm",         "down",  "Forest",      (68, 1)),    # ✅ 出口Farm(40,64)→Forest(68,1)
    ("Farm",         "up",    "Backwoods",    (14, 39)),  # ✅ 出口Farm(41,0)→Backwoods(14,39) 口=(40,0)
    # 农场内部
    ("Farm",         "门",     "FarmHouse",    (10, 6)),   # 主屋
    ("Farm",         "door",  "Cabin",        (3, 12)),   # ✅ 联机小屋 Farm(75,14)→Cabin(3,12)
    ("Farm",         "door",  "FarmCave",     (8, 11)),   # 🕳️ 农场洞穴 (34,7)→FarmCave(8,11)
    # 巴士站 ↔ 外部
    ("BusStop",      "left",  "Farm",         (79, 17)),  # ✅ Warp(9,22-25)→Farm(79,17)
    ("BusStop",      "right", "Town",         (0, 54)),   # ✅ Warp(44,22-25)→Town(0,54)
    ("BusStop",      "up",    "Backwoods",    (49, 30)),  # ✅ Warp(11,6-9)→Backwoods(49,30)
    ("BusStop",      "door",  "Desert",       (18, 27)),  # ✅ 巴士warp(22,8)→Desert(18,27) 需车票
    # 深山 ↔ 外部
    ("Backwoods",    "right", "BusStop",      (14, 8)),   # ✅ Warp(50,28-32)→BusStop(14,8)
    ("Backwoods",    "right", "Mountain",     (0, 13)),   # ✅ Warp(50,10-17)→Mountain(0,13)
    ("Backwoods",    "down",  "Farm",         (40, 0)),   # ✅ Warp(13~15,40)→Farm(40,0)
    ("Backwoods",    "door",  "Tunnel",       (34, 9)),   # 🚇 隧道入口(22,31)→Tunnel 需特殊条件
    # 鹈鹕镇 ↔ 外部
    ("Town",         "left",  "BusStop",      (10, 23)),   # 口= (44,22) 从巴士站来
    ("Town",         "left",  "Forest",       (118, 25)),  # ✅ Warp(-1,89~93)→Forest(118,25) 森林侧
    ("Town",         "down",  "Beach",        (38, 0)),    # ✅ 隧道warp(53~55,110)→Beach(38,0)
    ("Town",         "up",    "Mountain",     (15, 40)),   # ✅ 已验证: Town→Mountain 入口 (15,40)
    ("Town",         "door",  "CommunityCenter", (32, 23)),  # 口= (53,20)
    ("Town",         "door",  "SeedShop",      (6, 29)),    # 口= (43,57)
    ("Town",         "door",  "Hospital",      (6, 17)),    # 口= (36,56)
    ("Town",         "door",  "Saloon",        (14, 24)),  # ✅ 门口Town(45,72) → 餐吧内(14,24)
    ("Town",         "door",  "Blacksmith",    (5, 19)),   # ✅ 门口Town(94,82)→铁匠铺内(5,19)
    ("Town",         "door",  "JoshHouse",     (9, 24)),    # 🏠 Alex+爷爷奶奶家 (57,64)→JoshHouse(9,24)
    ("Town",         "door",  "HaleyHouse",    (2, 24)),    # 🌊 海莉&艾米丽家 (20,89)→HaleyHouse(2,24)（2026-09-10 恒校准）
    ("Town",         "door",  "SamHouse",      (4, 23)),    # 🏠 乔迪/山姆/文森特/肯特家 (10,86)→SamHouse(4,23)（2026-09-10 恒校准）
    ("Town",         "door",  "ManorHouse",    (5, 11)),    # 🏛️ 镇长家 (59,86)→ManorHouse(5,11)
    ("Town",         "door",  "ArchaeologyHouse", (3, 14)), # 🏫 博物馆/图书馆 (101,90)→ArchaeologyHouse(3,14)
    ("Mountain",     "door",  "ScienceHouse",  (6, 24)),   # ✅ 入口(6,24) | 前门warp (6,25)→(12,26) | 后门 (3,9)→(8,21)
    ("ScienceHouse", "door",  "SebastianRoom", (1, 1)),    # Sebastian地下室入口 (12,23)→SebastianRoom
    # 森林 ↔ 外部
    ("Forest",       "up",    "Town",         (28, 54)),
    ("Forest",       "left",  "Farm",         (64, 16)),
    ("Forest",       "door",  "Woods",        (58, 15)),  # 🌳 秘密森林入口Forest(0,7)→Woods(58,15) 需钢斧
    ("Forest",       "door",  "WizardHouse",   (8, 24)),  # ✅ 法师塔门口Forest(5,27) → WizardHouse(8,24)
    ("Forest",       "door",  "AnimalShop",   (13, 19)),  # ✅ 玛妮牧场门口Forest(90,16)→AnimalShop(13,19)
    # 下水道
    ("Town",         "door",  "Sewer",        (16, 11)),   # 🚇 下水道入口(35,97)→Sewer(16,11) 需钥匙
    ("Sewer",        "door",  "BugLand",      (15, 53)),   # 🐟 变异鲤鱼巢穴 (3,18)→BugLand(15,53)
    # 海滩
    ("Beach",        "left",  "Town",         (28, 54)),   # 隧道回镇
    ("Beach",        "door",  "FishShop",     (5, 9)),     # ✅ 鱼店门口Beach(30,34)→FishShop(5,9)
    ("FishShop",     "door",  "BoatTunnel",   (6, 12)),    # 🚢 鱼店后门→BoatTunnel→姜岛船（落点 (6,12) = 反编译 `FishShop.cs:74` warpFarmer("BoatTunnel",6,12)；2026-10-05 补28c 真机读门格 (4,3)=WarpBoatTunnel）
    # 铁路区域
    # 沙漠区域
    ("Desert",       "door",  "SkullCave",    (7, 8)),     # 💀 头骨矿洞入口Desert(8,5)→SkullCave(7,8)
    ("Desert",       "door",  "SandyHouse",   (4, 9)),     # ✅ 桑迪商店 Desert(6,52)→SandyHouse(4,9)
    ("SandyHouse",   "door",  "Club",         (8, 13)),    # 🎰 赌场入口 SandyHouse(17,1)→Club(8,13)
    ("Railroad",     "down",  "Mountain",     (9, 0)),    # ✅ Railroad→Mountain 下山
    ("Railroad",     "up",    "Summit",       (10, 29)),  # 山顶（需完美达成）
    ("Railroad",     "door",  "WitchWarpCave",(4, 9)),    # 魔女沼泽洞穴入口 (54,33)→WitchWarpCave
    ("Railroad",     "door",  "BathHouse_Entry",(5, 9)),   # ♨️ 浴场入口 (10,57)→BathHouse_Entry(5,9)
    # 矿洞
    ("Mountain",     "left",  "Backwoods",    (49, 14)),  # ✅ Warp(-1,12) → Backwoods(49,14)
    ("Mountain",     "down",  "Town",         (81, 0)),   # ✅ Warp(14-16,41) → Town(81,0) 温泉下山
    ("Mountain",     "up",    "Railroad",     (29, 59)),  # ✅ Warp(9,-1) → Railroad(29,59)
    ("Mountain",     "door",  "Mine",         (18, 13)),  # ✅ 入口warp (54,4)→Mine(18,13) 门口在(54,5)
    ("Mountain",     "door",  "AdventureGuild", (6, 12)),  # ✅ 门口在Mountain(76,9) 需验证内部坐标
    ("Mountain",     "warp",  "Tent",          (2, 5)),    # ⛺ 莱纳斯帐篷 warp(29,6)→Tent(2,5)（2026-09-06 恒：/warps实测非门）
    ("Mountain",     "door",  "LeoTreeHouse",  (3, 8)),    # 🌳 雷欧树屋：站(16,8)面0 interact(16,7) 进（2026-09-06 树屋门口实测）

    # ── 姜岛（2026-08-15 按实时 /warps 校正：IslandSouth 为枢纽）──
    ("IslandSouth",  "west",  "IslandWest",    (104, 41)),  # 🏝️ 西桥→姜岛农场(105,41)
    ("IslandSouth",  "east",  "IslandEast",    (0, 41)),    # 🏝️ 东桥→丛林(0,46)
    ("IslandSouth",  "up",    "IslandNorth",   (40, 24)),   # 🏝️ 北边→火山入口区(36,89)
    ("IslandSouth",  "door",  "FishShop",      (4, 4)),     # 🚢 码头坐船返航→鱼店(4,4)
    ("FishShop",     "door",  "BoatTunnel",    (6, 12)),    # 🚢 鱼店后门→船坞（落点同 `FishShop.cs:74`）
    ("BoatTunnel",   "door",  "IslandSouth",   (21, 43)),   # 🚢 上船→姜岛码头(21,43)
    ("IslandWest",   "door",  "IslandFarmHouse", (14, 15)), # 🏠 姜岛小屋
    ("IslandWest",   "door",  "QiNutRoom",     (7, 8)),     # 🥥 齐钻核桃房（落点 (7,8) = 反编译 `IslandWest.cs:336` warpFarmer("QiNutRoom",7,8,0)；2026-10-05 补28c 改正，原写 (7,7) 差一格）
    ("IslandWest",   "door",  "IslandFarmCave", (4, 10)),   # 🕳️ 农场洞穴
    ("IslandNorth",  "door",  "VolcanoEntrance", (1, 1)),   # 🌋 火山入口
    ("IslandNorth",  "door",  "IslandFieldOffice", (4, 10)),# 🏛️ 办事处
    ("IslandNorth",  "door",  "IslandNorthCave1", (6, 11)), # 🍄 蘑菇洞
    ("VolcanoEntrance","door","VolcanoDungeon0", (37, 4)),  # 🌋 火山矿井
    ("IslandEast",   "door",  "IslandHut",     (7, 13)),    # 🏠 雷欧小屋
    ("IslandEast",   "door",  "IslandShrine",  (13, 28)),   # 🗿 神殿
    ("MasteryCave",  "door",  "Forest",        (101, 73)),  # 🧙 精通山洞→森林
    ("Summit",       "down",  "Railroad",      (29, 59)),   # ⛰️ 山顶下山→铁路
    ("WitchWarpCave","door",  "Railroad",      (54, 33)),   # 🧙 魔女沼泽洞穴→铁路
]

# ── POI（兴趣点） ──
# AI 根据"想干什么"查这个表，找到目的地的坐标
POI = {
    # ── 农场 ──
    # ⚠️ 2026-08-30 恒拍板：农场里能动的建筑/物品**不留固定 POI**——全用权威动态读取：
    #   · 床(自己/AI 小屋)= /crawl_bed locate（动态，别再写死模板坐标）
    #   · 门口/家门= /state.homeDoor（带 GUID，权威）
    #   · 建筑(温室/出货箱/图腾柱/宠物水碗/畜棚鸡舍…) = /farm_buildings（抗搬家）
    #   · 箱子/小桶区 = 玩家自己放的位置，本就无固定
    #   只保留**地图固有几何**（出入口 warp 瓦片、洞穴门），这些才是静态的。
    # ⛩️ 爷爷神龛：**坐标随农场类型变**（每种农场一整张独立地图 Farm.xnb / Farm_Fishing.xnb / …），
    #    所以这里存的 (8,8) **只是占位**，真实坐标由 navigation._grandpa_shrine_gate() 在导航前
    #    动态扫 Farm 图的 `Action: Message "…"` 瓦片刷新（跨类型通用；定位不到会明确报错，不会用这个占位值）。
    #    (8,8) = 7 种农场的站位（标准/河畔/森林/山地/荒野/四角/海滩）；**草地是 (14,10)**（差 6 格）。
    #    ⚠️ 旧注释写"农场西南角"是错的——(8,8) 是**西北角**（2026-09-11 实测纠正）。
    "爷爷的神龛":        {"map": "Farm",      "pos": (8, 8),  "note": "爷爷神龛（农场西北角；放钻石评估/拿铱猫）。坐标随农场类型变，导航时动态定位"},
    "农场上口(→深山)":  {"map": "Farm",      "pos": (41, 0), "note": "Farm上口(warp瓦片固定)，warp到Backwoods"},
    "农场下口(→森林)":  {"map": "Farm",      "pos": (40, 64),"note": "Farm下口(warp瓦片固定)，warp到Forest(68,1)"},
    "农场洞穴(外)":      {"map": "Farm",      "pos": (34, 7), "note": "农场洞穴门口(地图固定)，蘑菇/果蝠洞"},
    "农场洞穴(内)":      {"map": "FarmCave",  "pos": (8, 11), "note": "蘑菇/果蝠洞内部"},
    "出货箱":            {"map": "Farm",      "pos": (71, 14),"note": "出货箱(**固定不可移**，2026-08-30 恒确认)——田外金属箱，隔夜到账；menu bin 投放。宠物水碗(可移/multiple)不走这"},

    # ── 巴士站 ──
    "巴士站(售票处)":    {"map": "BusStop",    "pos": (17, 12),"note": "巴士售票处(机子在17,11站位17,12)：交互选'是'花500g去沙漠；等动画~7s（2026-08-15实测）"},
    "巴士站(矿车)":      {"map": "BusStop",    "pos": (14, 4), "note": "🚂 矿车（献祭解锁，2026-08-15实测）：站(14,4)朝上交互(14,3)→菜单[0]矿井[1]城镇[2]采石场[3]取消"},
    "巴士站(巴士)":      {"map": "BusStop",    "pos": (22, 13),"note": "巴士上车点，warp到Desert(18,27)"},
    "巴士站(农场口)":    {"map": "BusStop",    "pos": (9, 23), "note": "从农场出来到BusStop"},
    "巴士站(镇方向)":    {"map": "BusStop",    "pos": (44, 22),"note": "去鹈鹕镇"},

    # ── 鹈鹕镇广场 ──
    "皮埃尔商店(门口)":   {"map": "Town",       "pos": (43, 57),"note": "商店门口"},
    "皮埃尔商店(入口)":   {"map": "SeedShop",   "pos": (6, 29), "note": "门口刚进来"},
    "皮埃尔商店(柜台)":   {"map": "SeedShop",   "pos": (4, 19), "note": "买种子、肥料"},
    "皮埃尔商店(背包升级)": {"map": "SeedShop",  "pos": (7, 19), "note": "🎒 背包升级（站(7,19)朝上交互(7,18) BuyBackpack）：12→24格 2000g、24→36格 10000g；⚠️不是柜台，在旁边一点"},
    "皮埃尔商店(优选交付箱)": {"map": "SeedShop", "pos": (19, 29), "require_order": {"requester": "Pierre"}, "note": "🧺 皮埃尔优选交货箱=**「皮埃尔优选」交付点**(收获并把25个金星品质蔬菜放进箱)：人站(19,29)朝0交互(19,28)把金星菜放箱；require_order=已接Pierre订单(进行中)才显示；⚠️箱子没接单不交互,(19,28)为AI面前solid推测,**坐标待恒确认**；内容可加 keywords:[\"金星\",\"蔬菜\"]（2026-08-22 恒带路）"},
    # ⚠️ 2026-10-05：本键曾**写过两遍**（后写的那条 note 只写"入口"，把"祝尼魔献祭入口"这句盖掉了）。
    #    dict 同名后写的赢 ⇒ 删掉重复那条，**留信息多的**。（同批共清 5 处重复键，见 CHANGELOG 203z补34）
    "社区中心(门口)":     {"map": "Town",       "pos": (53, 20),"note": "祝尼魔献祭入口"},
    "社区中心(献祭大厅)": {"map": "CommunityCenter", "pos": (14, 23),"note": "🟨 献祭面板(祝尼魔卷轴)：站它**旁边**朝它 scene interact 开 JunimoNoteMenu，看完 menu cancel 关掉。⚠️ 坐标是 2026-09-25 真机实测改的（**原写 (32,23)，实测那是块空地、interact 打空**——那个数多半来自反编译/另一存档状态，别信）。⚠️ **这块板子不是游戏对象**（`/surroundings`、`/tile_props?scan=Action` 在这间屋里全是 0），是地图贴图 + 一个孤立阻挡格 ⇒ **「它出现了没有」没法从数据判**（恒 2026-09-25：「知不知道哪些板子出现了好像不清楚」）：走到这儿 `interact`，没反应就是还没有。"},
    "社区布告栏(特别任务板)": {"map": "Town", "pos": (62, 94), "unlock": {"year": 1, "season": "fall", "day": 2}, "note": "📋 鹈鹕镇社区布告栏/**特别任务板**(1.5)：⚠️不是社区中心献祭板！**年1秋2后出现**；人站(62,94)朝上交互(62,93)开 SpecialOrdersBoard；`menu read` 看任务卡(名称/目标/奖励/期限)→`menu click(button=acceptLeftQuestButton/acceptRightQuestButton)`接；订单如「起风的日子」「给谁送餐」等（2026-08-22 AI现场检测+read_menu修复，accept_quest已退役）"},
    "社区布告栏(特别任务领奖箱)": {"map": "Town", "pos": (60, 94), "note": "📬 特别订单**领奖小邮箱**（社区布告栏左2格，⚠️不是接单板！板在(62,93)）：站(60,94)朝上交互(60,93)领已完成订单的**兑奖券(Prize Ticket)×1**（物品奖励需背包有空位，满格领不到——先丢低价值物腾格）；兑奖券再去**刘易斯家兑奖机**兑换实战奖励（2026-08-29 AI现场实测：completed的绿豆单在此领到兑奖券）"},
    "皮埃尔商店(求助布告栏)": {"map": "Town", "pos": (42, 57), "note": "📋 皮埃尔店西墙**每日求助栏**(Help Wanted/Billboard)：⚠️**不是社区布告栏(特别任务板)**！站(42,57)朝上交互(42,56)开 Billboard 每日求助菜单；`menu read` 看今日求助(如帮罗宾收35木材)；求助内容=questOfTheDay(2026-08-29 AI现场检测，玩家站(42,57)开菜单)"},
    "博物馆(门口)":       {"map": "Town",       "pos": (101, 90),"note": "博物馆/图书馆门口"},
    "博物馆(门内)":       {"map": "ArchaeologyHouse","pos": (3, 14),"note": "博物馆入口处"},
    "博物馆(柜台)":       {"map": "ArchaeologyHouse","pos": (3, 10),"note": "博物馆柜台，捐矿物/古物"},
    "博物馆(历史碎片投递箱)": {"map": "ArchaeologyHouse", "pos": (6, 10), "require_order": {"requester": "Gunther"}, "note": "🦴 **「历史的碎片」交付点**(收集骨类文物放进箱；⚠️物品必须是任务期间收集的)：骨类=两栖动物化石/骨笛/骨头碎片/腿骨化石/肋骨化石/颅骨化石/脊柱化石/尾巴化石/蝙蝠木乃伊/青蛙木乃伊/鹦鹉螺化石/棕榈化石/史前肋骨/史前肩胛骨/史前头骨/史前胫骨/史前脊骨/手部骨骼/尾部骨骼/蛇头骨/蛇脊椎骨/三叶虫；人站(6,10)朝0交互(6,9)放箱；require_order=已接Gunther订单(进行中)才显示；⚠️(6,9)为AI面前solid推测,**坐标待恒确认**；内容可加 keywords:[\"骨头\",\"化石\"]（2026-08-22 恒带路）"},
    "镇长家(门外)":       {"map": "Town",       "pos": (59, 86),"note": "刘易斯镇长家门口"},
    "镇长家(门内)":       {"map": "ManorHouse", "pos": (5, 11), "note": "镇长家内，warp回Town(58-59,86)"},
    "刘易斯家(特别订单兑奖机)": {"map": "ManorHouse", "pos": (1, 6), "note": "🎰 特别订单**兑奖机**(PrizeTicketMenu)：在刘易斯镇长家内左上；站(1,6)朝0交互(1,5)开兑奖菜单，把手里的**兑奖券(PrizeTicket)**换成实战奖励（物品奖励先腾背包空格）；⚠️/菜单对 PrizeTicketMenu 仅读到关闭钮(items空)，点名奖品格需补序列化（2026-08-29 AI现场实测：交互(1,5)弹出兑奖菜单，玩家站(1,6)）"},
    "哈维医院(门口)":     {"map": "Town",       "pos": (36, 56),"note": "医院入口"},
    "哈维医院(柜台)":     {"map": "Hospital",   "pos": (6, 17), "note": "买药、看病"},
    "哈维医院(出口)":     {"map": "Hospital",   "pos": (10, 19),"note": "回Town"},
    "哈维房间(外门)":     {"map": "Hospital",   "pos": (10, 14),"note": "🩺 哈维房间链**最外一扇门**（单扇，**无门禁纯推**）：医院内站(10,14)面0 interact门格(10,13)→门开变可走。顺序=外门(10,13)→楼梯间大门(10,5)→上楼warp(10,1)→HarveyRoom（2026-09-10 恒带路校准）"},
    "哈维房间(门右)":     {"map": "Hospital",   "pos": (10, 6), "note": "🩺 哈维房间**楼梯间大门·右门**：同图隔间门（非warp），医院内站(10,6)面0 interact门格(10,5)→门开变可走；再往里(10,1)warp进 HarveyRoom(6,12)。两扇门左右都行（2026-09-10 恒带路校准）"},
    "哈维房间(门左)":     {"map": "Hospital",   "pos": (9, 6),  "note": "🩺 哈维房间**楼梯间大门·左门**（与右门(10,5)是一对**双开门**的两扇，同图隔间门非warp）：站(9,6)面0 interact门格(9,5)→门开变可走，左右任推一扇（2026-09-10 恒带路校准）"},
    "哈维房间(内)":       {"map": "HarveyRoom", "pos": (6, 12), "note": "🩺 哈维的房间（医院楼上）：进来落点(6,12)；出来=(6,13)warp→医院(10,2)"},
    "哈维房间(楼梯口)":   {"map": "Hospital",   "pos": (10, 3), "note": "⬆️ 哈维房间上楼口（医院内走廊）：走到最顶行 ⚠️**必须用 /move 走上去**（walk_to/position/interact 都不触发 warp！），/move(10,1) → HarveyRoom(6,12)。与塞巴地下室(下楼)对称（2026-09-10 恒带路+/move实测）"},
    "星之果实餐吧(门口)": {"map": "Town",       "pos": (45, 72),"note": "格斯餐吧门口"},
    "铁匠铺(门口)":       {"map": "Town",       "pos": (94, 82),"note": "升级工具/买矿石煤/开晶球"},
    "电影院(门口)":       {"map": "Town",       "pos": (95, 51), "joja_form": "theater","note": "🔒解锁前隐藏(需献祭完成+雷暴开门)前Joja超市大门(95,50)→电影院，看电影约会；献祭完成+雷暴开门后进(2026-08-16恒校准)"},
    "电影院售票处":       {"map": "Town",       "pos": (98, 52), "joja_form": "theater","note": "🔒解锁前隐藏(需献祭完成+雷暴开门)🎬 电影票1000g(社区中心献祭后解锁)；朝上交互(98,51)开ShopMenu买票，票可送人看电影(2026-08-16恒实测)"},
    "电影院小卖部":       {"map": "MovieTheater","pos": (7, 7), "joja_form": "theater", "note": "🔒解锁前隐藏(需献祭完成+雷暴开门)🍿 电影院前台/零食柜台：**带NPC客人一起来才能买零食请他们吃**，独自来只提示(2026-08-16恒实测)；交互(7,6)"},
    "电影院放映厅":       {"map": "MovieTheater","pos": (14, 4), "joja_form": "theater","note": "🔒解锁前隐藏(需献祭完成+雷暴开门)🎬 放映厅入口(14,3)——进去看当前播放的电影；和邀请的NPC一起看涨好感(2026-08-16恒实测)"},
    # 🏬 那栋楼的三形态（恒 2026-10-05：「形态多次变化，路由到什么样的 poi，其它的就隐藏起来」）
    #    ⚠️ 三形态**互斥**，`joja_form` 就是那道闸（判据 = `_joja_form()` **当场读** `Town(95,50)` 门那格）：
    #       读到哪种 ⇒ 只放行那种的 POI，另两种一并隐藏；**认不出 ⇒ 三种全不给**（绝不猜）。
    "Joja超市(门口)":     {"map": "Town",      "pos": (95, 51), "joja_form": "jojamart", "note": "🏬 Joja 超市门口（形态=Joja超市）。门格 (95,50)/(96,50) = 原生 `LockedDoorWarp 13 29 JojaMart 900 2300` ⇒ 落 **JojaMart(13,29)/(14,29)**，**营业 9:00-23:00**（门原生时段 900~2300；2026-10-05 真机读）。⚠️ 会员/**社区发展申请表是房主专属**（`JojaMart.cs:87 if (Game1.IsMasterGame)`，farmhand 只会听到 `_SecondPlayer` 那句）⇒ 我们不做那条线"},
    "Joja超市(店内)":     {"map": "JojaMart",  "pos": (13, 29), "joja_form": "jojamart", "note": "🏬 Joja 超市店内：进来落点 (13,29)/(14,29)，出口瓦片 (13,30)/(14,30)→Town(95,51)/(96,51)（2026-10-05 真机读 warps）。柜台 Action 格 = **(10,24)/(10,25)** `JojaShop`、入会/申请表标牌 = **(21,25)** `JoinJoja`（真机扫 Action 得）——⚠️**柜台站立格没真机验过**（新档那支我们只在旧档跑），所以本 POI 只给「店内落点」，柜台坐标先记在 note 里"},
    "废弃超市(门口)":     {"map": "Town",      "pos": (95, 51), "joja_form": "abandoned", "note": "🏚 废弃 Joja 超市门口（形态=废弃超市）：这两格由 `Town.cs:290-302` 的**瓦片 case 2000/2001/2032/2033** 接管 ⇒ `warpFarmer(\"AbandonedJojaMart\", 9, 13)`（**判据是瓦片不是 Action 文本**）"},
    "废弃超市(收集包板子)":{"map": "AbandonedJojaMart", "pos": (8, 8), "joja_form": "abandoned", "note": "🏚 第 6 区**遗失的收集包**板子（跟社区中心同一套 `JunimoNoteMenu(6, bundles)`，`AbandonedJojaMart.cs:34-41`）⇒ 我们能捧、能献。⚠️坐标 (8,8) 是**推理**（`AbandonedJojaMart.cs:64` 过场拆的就是 (8,8) 的 Buildings 瓦片）**没有真机样本**；进来落点 (9,13)、出口 (9,14)→Town(96,51)（地图属性 `Warp: 9 14 Town 96 51`，真机实读）"},
    "书摊(马尔赛罗)":    {"map": "Town",       "pos": (110, 27),"note": "📚 马尔赛罗书摊(**⏳每季随机开张2天**,joja超市后小山坡,皮埃尔店右边悬崖有路线提示)：对话→[0]购买书籍/[1]回收书籍；卖技能书(星露谷年历8000/战斗季刊5000/怪物图鉴20000/风之道1·2/马术秘籍25000/草中窜/酱料女皇烹饪秘籍50000)。**可能开张日**见 calendar_data.BOOK_STALL_DATES；当天日历有热气球标志+左下角提示'书摊老板今天在镇上'(2026-08-16恒)"},
    # 🎇 以下夜市点位=节日限定(冬15-17)：BeachNightMarket 只在夜市加载，非夜市去不了/不在
    "夜市咖啡商人":      {"map": "BeachNightMarket","pos": (14, 38),"note": "☕ 夜市咖啡商人(🎇节日限定冬15-17)：对话→[是]=免费咖啡150g（每晚一次，2026-08-16恒实测）；夜市地图叫 BeachNightMarket 不是 Beach"},
    "夜市装饰商船":      {"map": "BeachNightMarket","pos": (19, 34),"note": "🎇节日限定(冬15-17)🎪 夜市装饰商船：卖**拐杖糖**(大绿杖/绿杖/混色杖/红杖/大红杖 200g,仅此出售)、火炬800、云彩帘子1000、季节性装饰/植物500(2026-08-16恒实测)"},
    "夜市猪车(旅行货车)": {"map": "BeachNightMarket","pos": (39, 31),"note": "🐷 夜市旅行货车：随机商品(麻哈脂鲤/防风草汤/矿工特供/山羊奶酪/粉红蛋糕…)；联机卖结婚戒指配方(2026-08-16恒实测)"},
    "夜市美人鱼船":      {"map": "BeachNightMarket","pos": (58, 32),"note": "🎇节日限定(冬15-17)🧜 美人鱼船门(58,31)→MermaidHouse：看美人鱼秀,**等表演结束(约2-3分钟真实时间,可截图等待)再点贝壳 1-5-4-2-3 拿珍珠**(每存档1颗,2.5k金,换白桦双人床/新娘头纱)。**贝壳排 (2,6)-(6,6) 位置1-5,正确点序=(2,6),(6,6),(5,6),(3,6),(4,6)**；⚠️表演没结束按=白按；午夜12:30关(2026-08-16恒实测)"},
    "夜市魔法商船":      {"map": "BeachNightMarket","pos": (48, 35),"note": "🎇节日限定(冬15-17)🪄 魔法商船(黑乎乎那个)：卖装饰(墓石200比万灵节便宜/石蛙500)+**锥帽5000(第二晚最便宜)**+**季节种子**(15/16/17号卖春夏秋各季,第一年不卖大蒜/红叶卷心菜/洋蓟)(2026-08-16恒实测)"},
    "夜市画家Lupini":    {"map": "BeachNightMarket","pos": (43, 35),"note": "🎇节日限定(冬15-17)🎨 著名画家卢皮尼：对话→[是]=买画1200g（9幅3年轮,唯一出处;2026-08-16恒实测）"},
    "夜市裹布人":        {"map": "BeachNightMarket","pos": (32, 35),"note": "🎇节日限定(冬15-17)👤 裹布人：对话→**传送回农场250g**（不受夜市营业时间限制;2026-08-16恒实测）"},
    "夜市钓鱼潜艇":      {"map": "BeachNightMarket","pos": (5, 35),"note": "🎇节日限定(冬15-17)🛸 钓鱼潜艇门(5,35)：营业**17-23时**(23时后上锁)。⚠️**进门后还要和艇长交互**(Submarine(2,10)对话[是]1000g)才下潜钓深海鱼(午夜鱿鱼/幽灵鱼/水滴鱼,唯一出处)+极稀有珍珠(珍稀诱钩提升)；多人同时只能1人(2026-08-16恒实测)"},
    "潜艇艇长":          {"map": "Submarine",  "pos": (2, 10), "note": "🎇节日限定(冬15-17)🛸 潜艇艇长：对话→[是]=**1000g下潜**。⚠️**下潜到开门约30分钟游戏时间**——等开门后站附近朝艇长方向抛竿钓(午夜鱿鱼/幽灵鱼/水滴鱼/珍珠,范围大不会钓空)。**安全内部入口=(14,15)**(门卡住时用position到(14,15))；⚠️24时关门会弹到门外(2026-08-16恒实测)"},
    "城镇矿车":           {"map": "Town",       "pos": (105, 80),"note": "矿车交通：可到BusStop/Mountain/采石场"},
    "下水道入口":         {"map": "Town",       "pos": (35, 97),"note": "需要钥匙才能进→科罗布斯商店/变异鲤鱼钓点"},
    "下水道(出口)":       {"map": "Forest",     "pos": (94, 100),"note": "从下水道出来在森林侧"},
    "科罗布斯商店":       {"map": "Sewer",      "pos": (31, 18),"note": "科罗布斯摊位：买铱环/电池/星之果/虚空蛋"},
    "改变职业点(雕像)":   {"map": "Sewer",      "pos": (8, 21), "note": "🗿 **不确定之雕像(换职业点)**(2026-08-30 恒摆位+AI 记)：站(8,21)面朝0(上) interact(面前=雕像(8,20))→**花金重选职业分支**(换一个职业)。⚠️ 花钱改分支；交互会开职业重选菜单(LevelUpMenu职业选择)——**正好用来验 menu ops=levelup_choose**。需下水道钥匙(已解锁)"},
    "变异鲤鱼钓点":       {"map": "Sewer",      "pos": (16, 28),"note": "下水道钓变异鲤鱼（传说鱼之一）✅"},
    "变异虫穴(入口)":     {"map": "BugLand",    "pos": (15, 53),"note": "变异虫穴入口，有放射性矿石/蛆/蚊子"},
    "变异虫穴(钓鱼点)":   {"map": "BugLand",    "pos": (20, 40),"note": "变异鲤鱼也可以在这钓（待校准）"},
    "公交站(镇内方向)":   {"map": "Town",       "pos": (34, 44),"note": "公交站广场区域"},
    "墓地":               {"map": "Town",       "pos": (18, 32),"note": "捡棒子"},
    "镇小桥钓点":         {"map": "Town",       "pos": (74, 68),"note": "镇中心桥下河边钓点（河鱼）"},
    "镇传送(左下)":       {"map": "Town",       "pos": (1, 55), "note": "Town左下入口附近，warp落点+走几步"},
    "镇传送(右下)":       {"map": "Town",       "pos": (55, 85),"note": "Town右下铁匠铺/博物馆附近"},
    "镇传送(广场)":       {"map": "Town",       "pos": (45, 60),"note": "Town中心广场，皮埃尔/社区中心附近"},
    "镇鲶鱼钓点":         {"map": "Town",       "pos": (3, 93), "note": "雨天鲶鱼钓点（Town左河边）✅"},
    "森林河边钓点":      {"map": "Forest",     "pos": (20, 76),"note": "森林河边钓鱼（河鱼/鲶鱼）✅"},
    "镇下水道钓点":       {"map": "Town",       "pos": (33, 97),"note": "镇最下方河边钓点（河鱼/鲶鱼）"},
    "森林小池塘钓点":     {"map": "Forest",     "pos": (34, 25),"note": "森林猪车旁小池塘钓点（河鱼）✅"},
     # 🏝️ 姜岛两处钓鱼点（**恒 2026-10-06 亲站给坐标**）。⚠️ 只记"这儿能钓"、**不记鱼种** ——
     #    恒的规矩：鱼种不上代码/单子（AI 要查走 `fish ops=info`）。
     "姜岛农场湖钓点":     {"map": "IslandWest",  "pos": (50, 52),"note": "姜岛农场旁那个湖（姜岛专属鱼）✅ 2026-10-06 恒亲站，**面左**"},
     "姜岛南岸海钓点":     {"map": "IslandSouth", "pos": (26, 34),"note": "姜岛南岸海洋（姜岛专属鱼）✅ 2026-10-06 轮回亲站，**面下**"},
     # 🏴‍☠️ 恒 2026-10-06 亲站：**东南岛→海盗湾的 warp 瓦片**（走上去触发，不在 `/warps` 表里）。
     #    ⚠️ `IslandSouthEast (0,29)` 那块以**度假村修复**为门禁（恒当天指出）。
     "海盗湾(入口)":       {"map": "IslandSouthEast", "pos": (31, 18),"note": "🏴‍☠️ 东南岛→海盗湾的 **warp 瓦片**（**总表口径 (31,18)**；恒亲站的 (29,18) 是边走格）（Action 触发，走上去就进湾；`/warps` 查不到）✅ 2026-10-06 恒亲站"},
     "海盗湾内钓点":     {"map": "IslandSouthEastCave", "pos": (6, 8),"note": "海盗湾内的钓点（姜岛专属鱼）✅ 2026-10-06 真机：单子按下去走到这格**朝右**，真钓上一条"},
    "书摊":               {"map": "Town",       "pos": (110,27),"note": "书商摊位(非每日开)"},
    "冰淇淋摊位":        {"map": "Town",       "pos": (88, 93), "season": "summer", "note": "🍦 冰淇淋摊(夏季限定)：博物馆桥东；**只在夏季营业**，周三/雨天休，13:00-17:00；亚历克斯站柜台(88,91)→人站(88,93)朝上交互(88,92)买冰淇淋；海莉常在这附近(夏季找她好地方)（2026-08-22 AI现场检测）"},
    "艾芙琳家(门内)":     {"map": "JoshHouse",  "pos": (9, 24),"note": "Alex+爷爷奶奶家，warp回Town(57,64)"},
    "艾芙琳家(门外)":     {"map": "Town",       "pos": (57, 64),"note": "JoshHouse门口在Town"},
    "亚历克斯卧室(门口)":  {"map": "JoshHouse",  "pos": (10, 10),"friend_gate": {"npc": "Alex"}, "note": "🏋️ 亚历克斯卧室门（艾芙琳/乔治/亚历克斯家内）：同图隔间门（非warp），**好感门禁**——和Alex不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(10,9)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "乔治&艾芙琳卧室(门口)": {"map": "JoshHouse", "pos": (5, 10), "friend_gate": {"npc": "Evelyn"}, "note": "👵 乔治&艾芙琳夫妻卧室门（JoshHouse内）：同图隔间门（非warp），**好感门禁**——和Evelyn(或George)不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(5,9)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "海莉&艾米丽家(门外)":  {"map": "Town",       "pos": (20, 89),"note": "海莉&艾米丽家门口（2026-09-10 恒校准）"},
    "海莉&艾米丽家(门内)":  {"map": "HaleyHouse", "pos": (2, 24),"note": "海莉&艾米丽家内"},
    "海莉卧室(门口)":      {"map": "HaleyHouse", "pos": (5, 14),"friend_gate": {"npc": "Haley"}, "note": "🌊 海莉卧室门：同图隔间门（非warp），**好感门禁**——和Haley不是朋友→interact弹『和Haley还不是朋友，不能进入他房间』；够了好感→静默开门（门格(5,13)朝上，开门后门格 passable 变 true，可用 dump_tile 探门关没关）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "艾米丽卧室(门口)":    {"map": "HaleyHouse", "pos": (16, 13),"friend_gate": {"npc": "Emily"}, "note": "🌊 艾米丽卧室门：同图隔间门（非warp），**好感门禁**——和Emily不是朋友→interact弹『和Emily还不是朋友，不能进入他房间』；够了好感→静默开门（门格(16,12)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "乔迪家(门外)":        {"map": "Town",       "pos": (10, 86),"note": "乔迪/山姆/文森特/肯特家门口（2026-09-10 恒校准）"},
    "乔迪家(门内)":        {"map": "SamHouse",   "pos": (4, 23),"note": "乔迪/山姆/文森特/肯特家内"},
    "文森特卧室(门口)":    {"map": "SamHouse",   "pos": (11, 17),"friend_gate": {"npc": "Vincent"}, "note": "🏠 文森特卧室门：同图隔间门（非warp），**好感门禁**——和Vincent不是朋友→interact弹『和Vincent还不是朋友，不能进入他房间』；够了好感→静默开门（门格(11,18)朝**下**(face=2)，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "山姆卧室(门口)":      {"map": "SamHouse",   "pos": (12, 15),"friend_gate": {"npc": "Sam"}, "note": "🏠 山姆卧室门：同图隔间门（非warp），**好感门禁**——和Sam不是朋友→interact弹『和Sam还不是朋友，不能进入他房间』；够了好感→静默开门（门格(12,14)朝上，开门后门格 passable 变 true，**可直接 walk_to 走上去**）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "乔迪&肯特卧室(门口)": {"map": "SamHouse",   "pos": (17, 7), "friend_gate": {"npc": "Jodi"}, "note": "🏠 乔迪&肯特夫妻卧室门：同图隔间门（非warp），**好感门禁**——和Jodi(或Kent)不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(17,6)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "玛妮卧室(门口)":      {"map": "AnimalShop", "pos": (15, 13),"friend_gate": {"npc": "Marnie"}, "note": "🐄 玛妮卧室门(牧场AnimalShop内)：同图隔间门（非warp），**好感门禁**——和Marnie不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(15,12)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "谢恩卧室(门口)":      {"map": "AnimalShop", "pos": (21, 14),"friend_gate": {"npc": "Shane"}, "note": "🐔 谢恩卧室门(牧场AnimalShop内)：同图隔间门（非warp），**好感门禁**——和Shane不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(21,13)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "贾斯卧室(门口)":      {"map": "AnimalShop", "pos": (6, 13), "friend_gate": {"npc": "Jas"}, "note": "🐔 贾斯卧室门(牧场AnimalShop内)：同图隔间门（非warp），**好感门禁**——和Jas不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(6,12)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "罗宾&德米特里厄斯卧室(门口)": {"map": "ScienceHouse", "pos": (13, 11),"friend_gate": {"npc": "Robin"}, "note": "🌲 罗宾&德米特里厄斯夫妻卧室门(木匠店ScienceHouse内)：同图隔间门（非warp），**好感门禁**——和Robin(或Demetrius)不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(13,10)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "玛鲁卧室(门口)":      {"map": "ScienceHouse", "pos": (7, 11), "friend_gate": {"npc": "Maru"}, "note": "🔧 玛鲁卧室门(木匠店ScienceHouse内)：同图隔间门（非warp），**好感门禁**——和Maru不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(7,10)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "塞巴斯蒂安地下室(入口)": {"map": "ScienceHouse", "pos": (13, 22),"note": "⬇️ 地下室楼梯口(木匠店内)：站(13,22)面下 踩(13,23)warp→SebastianRoom(1,1)（2026-09-10 恒带路校准）"},
    "塞巴斯蒂安房间(门内)": {"map": "SebastianRoom", "pos": (1, 1), "note": "⬆️ 下地下室落点=SebastianRoom(1,1)面下；出来踩(1,0)warp→ScienceHouse(12,21)（2026-09-10 恒带路校准）"},
    "塞巴斯蒂安卧室(门口)": {"map": "SebastianRoom", "pos": (1, 2), "friend_gate": {"npc": "Sebastian"}, "note": "🖤 塞巴斯蒂安卧室门(地下室SebastianRoom内)：同图隔间门（非warp），**好感门禁**——和Sebastian不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(1,3)朝**下**(face=2)，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "乔治家(给乔治的礼物)": {"map": "JoshHouse","pos": (9, 24),"require_order": {"any_keywords": ["韭葱"]}, "note": "🧾 **「给乔治的礼物」交付点**(艾芙琳订单,春季28天,收12韭葱)：交付=**带12韭葱进乔治家(进门)**→触发『韭葱惊喜礼物』过场即**自动交付**(进门就完成，不是放箱子)；reward=2000g+咖啡机+兑奖券；require_order=已接韭葱订单(进行中)才显示（2026-08-29 恒提供攻略）"},

    # ── 鹈鹕镇 ──
    "皮埃尔商店":        {"map": "SeedShop",   "pos": (4, 19), "note": "买种子、肥料"},
    "皮埃尔商店(后房第一道门)": {"map": "SeedShop", "pos": (14, 17), "note": "🚪 皮埃尔商店**后房第一道门（无门禁纯推）**：同图隔间门（非warp），店内站(14,17)面0 interact门格(14,16)→门开变可走（实测 passable false→true）。门后=阿比盖尔卧室、皮埃尔&卡洛琳夫妻卧室（好感门禁门，见下）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "阿比盖尔卧室(门口)": {"map": "SeedShop", "pos": (13, 12), "friend_gate": {"npc": "Abigail"}, "note": "🎮 阿比盖尔卧室门（皮埃尔商店SeedShop内，过第一道后房门(14,16)）：同图隔间门（非warp），**好感门禁**——和Abigail不是朋友→interact弹『和Abigail还不是朋友，不能进入他房间』；够了好感→静默开门（门格(13,11)朝上，开门后门格 passable 变 true，实测雪落14心静默开）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "皮埃尔&卡洛琳卧室(门口)": {"map": "SeedShop", "pos": (20, 12), "friend_gate": {"npc": "Caroline"}, "note": "🏪 皮埃尔&卡洛琳夫妻卧室门（皮埃尔商店SeedShop内，过第一道后房门(14,16)）：同图隔间门（非warp），**好感门禁**——和Caroline(或Pierre)不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(20,11)朝上，开门后门格 passable 变 true，实测雪落Caroline8心/Pierre7心静默开）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "星之果实餐吧(入口)":{"map": "Saloon",     "pos": (14, 24),"note": "餐吧入口处，warp回Town(45,71)"},
    "格斯房间(门口)":     {"map": "Saloon",     "pos": (20, 10),"friend_gate": {"npc": "Gus"}, "note": "🍳 格斯卧室门（星之果实餐吧Saloon内，楼上）：同图隔间门（非warp），**好感门禁**——和Gus不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(20,9)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "格斯房间外门(双开)":  {"map": "Saloon",     "pos": (3, 17), "note": "🍳 格斯房间**外面还有一重双开门**（进格斯房间前要先过；**无好感门禁，纯推门**）：两扇并排=**(3,16)左扇+(4,16)右扇**，站(3,17)面0 interact(3,16)推左扇。⚠️右扇(4,16)格斯进出时会自己掩门，左扇要自己推（2026-09-10 恒带路校准）"},
    "星之果实餐吧(柜台)":{"map": "Saloon",     "pos": (10, 20),"note": "格斯柜台，买沙拉/啤酒 ✅"},
    "星之果实餐吧(可乐机)":{"map": "Saloon",   "pos": (38, 18),"note": "🥤 Joja可乐机(2026-08-22 恒 7842 检测)：买 Joja 可乐 75g；人站(38,18)朝上交互(38,17)→对话「是/否 花费75金买」选「是」买(菜单_click option=0,非 ShopMenu)；可乐=谢恩最爱/雷欧喜欢"},
    "酒吧冰箱(格斯煎蛋卷)": {"map": "Saloon", "pos": (18, 17), "require_order": {"requester": "Gus"}, "note": "🧾 酒吧冰箱=**「格斯的著名煎蛋卷」交付点**(疑似24个蛋放冰箱)：人站(18,17)朝0交互(18,16)；require_order=已接Gus订单(进行中)才显示；⚠️冰箱没接单不交互,(18,16)为AI面前solid推测,**坐标待恒确认**；内容匹配可再加 keywords:[\"煎蛋卷\"]（2026-08-22 恒带路）"},
    "潘姆拖车(厨房柜)":   {"map": "Trailer",  "pos": (10, 7), "require_order": {"requester": "Pam"}, "note": "🧾 **「烈酒」交付点**(潘姆订单,春季14天,收12土豆果汁)：DropBox=Trailer(10,6) box_id=PamKitchen；**人站(10,7)朝0交互(10,6)**开 QuestContainerMenu→放12果汁→ok 结算 Complete；reward=3000g+友情+兑奖券；require_order=已接Pam订单(进行中)才显示；未接单时 DropBox 被 ignore(无问号)但瓦片一直在（2026-08-29 /scan 实测,坐标已定）"},
    "佩妮卧室(门口)":     {"map": "Trailer",  "pos": (6, 8),  "friend_gate": {"npc": "Penny"}, "note": "📚 佩妮卧室门（潘姆拖车Trailer内，即潘姆/佩妮母女家）：同图隔间门（非warp），**好感门禁**——和Penny不是朋友→interact弹『还不是朋友，不能进入他房间』；够了好感→静默开门（门格(6,7)朝上，开门后门格 passable 变 true）。⚠️开了没法自己掩门，重新进屋才刷新为关（2026-09-10 恒带路校准）"},
    "木匠商店(木头堆)":   {"map": "ScienceHouse","pos": (10, 20),"require_order": {"requester": "Robin"}, "note": "🧾 **「罗宾的项目」交付点**(罗宾订单,7天,收80硬木)：DropBox=ScienceHouse(10,19)/(11,19) box_id=RobinWood(木头堆,相邻两格)；**人站(10,20)朝0交互(10,19)**开 QuestContainerMenu→放80硬木→ok 结算 Complete；reward=2000g+友情+兑奖券+解锁木匠店豪华红双人床购买；require_order=已接罗宾订单(进行中)才显示；未接单无问号但瓦片一直在（2026-08-29 /scan 实测,坐标已定）"},
    "火车站(垃圾箱)":     {"map": "Railroad",  "pos": (28, 37),"require_order": {"requester": "Linus"}, "note": "🧾 **「社区清理」交付点**(莱纳斯订单,7天,收20垃圾([Joja可乐除外]))：DropBox=Railroad(28,36)/(29,36) box_id=Dumpster(垃圾箱,相邻两格)；**人站(28,37)朝0交互(28,36)**开 QuestContainerMenu→放20垃圾→ok 结算 Complete；reward=500g+友情+纤维种子配方(次日邮箱)+万能鱼饵配方(过场后)；⚠️**别跟齐先生 RailroadBox(45,40) 混淆**；require_order=已接莱纳斯订单(进行中)才显示（2026-08-29 /scan 实测,坐标已定）"},
    "哈维的医院":        {"map": "Hospital",   "pos": (4, 16), "note": "看病、买补给"},
    "木匠商店(柜台)":    {"map": "ScienceHouse","pos": (7, 20),"note": "罗宾柜台，买建筑/家具"},
    "木匠商店(门口内)":  {"map": "ScienceHouse","pos": (6, 24),"note": "店里入口处"},
    "木匠商店(门外)":    {"map": "Mountain",    "pos": (12, 26),"note": "罗宾木匠店门口（Mountain侧）"},
    "木匠商店(后门)":    {"map": "Mountain",    "pos": (8, 21),"note": "ScienceHouse后门出来在Mountain"},
    "铁匠铺(入口内)":    {"map": "Blacksmith",  "pos": (5, 19),"note": "铁匠铺入口处"},
    "铁匠铺(柜台)":      {"map": "Blacksmith",  "pos": (3, 15),"note": "克林特柜台：升级工具/买矿石煤铜铁金铱锭/开晶球 ✅"},
    "电影院(门内)":      {"map": "MovieTheater","pos": (12, 12), "joja_form": "theater","note": "电影院内部（待校准）"},

    # ── 传送中转点 ──
    "中转(Town左下)":     {"map": "Town",       "pos": (1, 55), "note": "Town左下入口，到BusStop/森林/瀑布"},
    "中转(Town右下)":     {"map": "Town",       "pos": (55, 87),"note": "Town右下铁匠铺/博物馆/海滩隧道"},
    "中转(Town广场)":     {"map": "Town",       "pos": (43, 57),"note": "Town中心皮埃尔/社区中心/餐吧"},
    "中转(Forest入口)":   {"map": "Forest",     "pos": (118, 25),"note": "Forest左上入口到Town/玛妮/秘密森林"},
    "中转(Forest下)":     {"map": "Forest",     "pos": (5, 27), "note": "Forest下方巫师塔/下水道出口"},
    "中转(Mountain上)":   {"map": "Mountain",   "pos": (15, 40),"note": "Mountain底部入口到Town"},
    "中转(Mountain中)":   {"map": "Mountain",   "pos": (54, 10),"note": "Mountain中部矿洞/罗宾/湖"},

    # ── 海滩 ──
    "海滩(入口)":        {"map": "Beach",      "pos": (38, 1), "note": "从Town进海滩的入口"},
    "海滩钓鱼点(码头)":  {"map": "Beach",      "pos": (52, 25),"note": "码头钓鱼，有海鱼/章鱼/红鲷鱼 ✅"},
    "海滩断桥":          {"map": "Beach",      "pos": (58, 13),"note": "300木头修复→右侧沙滩/潮池，可拾珊瑚/海胆/贝壳"},
    "海滩潮池钓点":      {"map": "Beach",      "pos": (85, 10),"note": "右侧潮池钓点（海鱼/蟹）"},
    # 🔴 2026-09-12 恒：原 pos (35,38) 是**凭空猜的坐标** —— 那一格是**开阔海面**
    #    （Back 层 Water、Buildings 层空的，没有任何桥面），走上去就是泡在海里；而且鱼店旁边
    #    x28~41 / y≥37 **一整套全是水、根本没有码头**（恒截图："跑进水里了"）。
    #    恒指定改用 (35,35)：陆地、可走，站岸上往南抛向 (35,37) 水面。
    "海滩鱼店码头钓点":  {"map": "Beach",      "pos": (35, 35),"note": "鱼店旁岸上钓点（面朝下抛向海面）⚠️09-12 从 (35,38) 水格改正"},
    "海滩左侧钓点":      {"map": "Beach",      "pos": (11, 26),"note": "海滩左侧礁石区钓点（海鱼）"},
    "海滩中部钓点":      {"map": "Beach",      "pos": (44, 35),"note": "海滩中部码头旁钓点（海鱼）"},
    # 🔴 2026-09-12：原 pos (82,28) 是**开阔水面**（Back=Water、Buildings 空）。扫图发现
    #    右侧确实有码头：y=25 的 x=78~85 全是桥面瓦片、y=26 的 x=85 也是，y≥27 才是水。
    #    ⇒ 把钓点落在**码头面上** (82,25)（同 x 往北 3 格），面朝下抛向 y≥27 的水。
    "海滩钓鱼点(右边)":  {"map": "Beach",      "pos": (82, 25),"note": "右侧码头钓点（面朝下）✅09-12 从水格 (82,28) 校正到码头面"},
    "艾利欧特家(门口)":  {"map": "Beach",      "pos": (49, 11),"note": "艾利欧特小屋门口"},
    "鱼店(门口)":        {"map": "Beach",      "pos": (30, 34),"note": "威利鱼店门口"},
    "鱼店(门内)":        {"map": "FishShop",   "pos": (5, 9),  "note": "鱼店入口处"},
    "鱼店(柜台)":        {"map": "FishShop",   "pos": (4, 6),  "note": "威利柜台：买鱼竿/鱼饵/蟹笼/鱼 ✅"},
    "鱼店(多汁的虫子桶)": {"map": "Beach", "pos": (37, 34), "require_order": {"any_keywords": ["虫肉"]}, "note": "🪱 **「需要多汁的虫子」交付点**(收集100虫肉倒进鱼店旁桶)：人站(37,34)朝0交互(37,33)倒虫肉进桶；require_order=已接虫肉订单(进行中)才显示；⚠️(37,33)为AI面前solid推测,**坐标待恒确认**(鱼店门口30,34;旁边(40-41,33-34)也有一小块，桶可能是那);可加 requester(\"Willy\")更准（2026-08-22 恒带路,点位不太确定）"},
    "鱼店(姜岛船门)":    {"map": "FishShop",   "pos": (4, 4),  "note": "鱼店后门→BoatTunnel→姜岛"},
    "姜岛船坞(入口)":    {"map": "BoatTunnel", "pos": (6, 12), "note": "🚢 船坞隧道**真实落点**(6,12)（反编译 FishShop.cs:74；2026-10-05 补28c 由 (4,10) 改正），买票上船去姜岛"},
    "姜岛船坞(售票)":    {"map": "BoatTunnel", "pos": (4, 9), "note": "售票机(触发4,9站位4,10)：交互选'是'花1000g去姜岛码头；等动画~10s（2026-08-15实测）。⚠️码头返程=传送岛(17,44)→鱼店(4,4)"},

    # ── 矿洞 ──
    "矿洞入口(外)":      {"map": "Mountain",    "pos": (54, 5), "note": "Mountain侧矿洞门口"},
    "矿洞入口(内)":      {"map": "Mine",        "pos": (18, 13),"note": "矿洞内部入口"},
    "矮人商店":          {"map": "Mine",        "pos": (43, 7), "wallet": "HasDwarvishTranslationGuide", "rock": True, "note": "🧱 矮人商店：炸开堵路石头+学会矮人语教程才开放。人站(43,7)朝上(0)正对矮人(43,6)，interact 买炸弹/矿石批发；wallet=钱包里 HasDwarvishTranslationGuide（学会矮人语教程，同 HasRustyKey 等钥匙检测源）；rock=先炸开 Mine(27,8) 的 (BC)78 堵路石才能走到（未炸→隐藏/拦，炸掉不再生）（2026-08-23 恒带路+AI现场检测）"},
    "探险家公会(外)":    {"map": "Mountain",     "pos": (76, 9), "note": "Mountain侧公会门口"},
    "探险家公会(内)":    {"map": "AdventureGuild","pos": (6, 12),"note": "买武器、接怪物任务"},
    # 🗿 2026-10-04 恒：「**马龙这里要暴露的坐标有三个**：一个是吉尔、一个是马龙柜台、
    #     一个是墙上的讨伐清单」——三个都真机验过（站 `pos` 朝上 interact 就成）：
    "探险家公会(马龙柜台)": {"map": "AdventureGuild", "pos": (5, 13), "note": "🧔 马龙柜台：站(5,13)朝上交互(5,12)→ShopMenu 38 样（武器/靴子）。瓦片 Action=`AdventureShop`"},
    "探险家公会(吉尔)":     {"map": "AdventureGuild", "pos": (11, 13),"note": "🛏️ 吉尔（躺床上）：站(11,13)朝上交互(11,12)→他说的话；**讨伐奖励没领完时会弹 ItemGrabMenu**（`AdventureGuild.gil()`；没得领就说一句'等你有了能让我刮目相看的东西'）"},
    "探险家公会(讨伐清单)": {"map": "AdventureGuild", "pos": (8, 11), "note": "📜 墙上讨伐清单：站(8,11)朝上交互(8,10)→LetterViewerMenu，**正文我们包办读**（单子上直接印全表：各怪 x/目标 + `*`=已达标）；瓦片索引 1306"},

    # ── 深山 ──
    "温泉(门口)":        {"map": "Railroad",    "pos": (10, 57),"note": "♨️ 浴场入口在Railroad(10,57)，推门进 BathHouse_Entry(5,9)（一键开门）"},
    #   ♨️ 大厅两扇性别门真身（2026-09-10 反编译 + `/tile_props` 实证）：Buildings 层
    #   `Action: WarpWomensLocker 13 27 BathHouse_WomensLocker` / `WarpMensLocker 3 27 BathHouse_MensLocker`
    #   —— **不是推门、不在 interiorDoors**。`if (who.IsMale) { 弹「WomensLocker_WrongGender」; return; }`，
    #   否则立即 warpFarmer 传送进去。轮回=Female（存档 `<Gender>Female</Gender>` 实证）→ 走女门畅通。
    #   ⚠️ 泳池图 (5,2)(6,2)(7,2)=TouchAction WomensLocker / (20,2)(21,2)(22,2)=MensLocker：
    #      性别不符**踩上去就被推回+弹窗**（那条"门禁拒绝"路径以前一直没样本）。
    "温泉(大厅)":        {"map": "BathHouse_Entry","pos": (5, 9), "note": "♨️ 进浴场后的门口大厅格（⚠️不是更衣室！2026-09-10 恒修正：旧记录误标'更衣室'）。大厅=y4~8×x1~8 横条。往**更衣室**（性别门禁：男进男/女进女）——女=大厅(2,4)面0 interact门格(2,3)→BathHouse_WomensLocker(13,27)；男=大厅(7,4)面0→门格(7,3)→BathHouse_MensLocker(3,27)。（更衣室→泳池见「温泉(更衣室女/男)」POI；门格机制见上方注释）"},
    "温泉(更衣室女)":    {"map": "BathHouse_WomensLocker","pos": (13, 27),"note": "♨️ **女更衣室**（图18×28，y只到27）——只有女角色能进，男角色撞门会弹 DialogueBox『这里是女更衣室，你不能进』。**进门落点(13,27)**。出门两条路都走 map_go 关系网（已修 2026-09-10）：回大厅=站 (13,27) → warp BathHouse_Entry(2,4)；去泳池=站 (2,27) → warp Pool(6,0)。⚠️ 真出口 (13,28)/(2,28) 在**图外**，别再物理蹭边缘（恒：会把游戏搞不稳）"},
    "温泉(更衣室男)":    {"map": "BathHouse_MensLocker","pos": (3, 27), "note": "♨️ **男更衣室**（图同18×28）——只有男角色能进，女角色撞门会弹 DialogueBox『这里是男更衣室，你不能进』。**进门落点(3,27)**。出门同女更衣室：回大厅=站 (3,27) → warp BathHouse_Entry(7,4)；去泳池=站 (15,27) → warp Pool(21,0)。真出口 (3,28)/(15,28) 在图外，别蹭边缘"},
    # ── 🩳 换装格（2026-09-10 反编译 + 真机验证；Back 层 TouchAction）──
    #   恒原话"走过淋浴廊会变慢、出来是泳衣"的真身：**不是地形减速，是换泳装后引擎强制 canOnlyWalk（只能走不能跑）**。
    #   触发：踩到该格的下一个 tick 自动触发（唯一闸门 Game1.eventUp；不用按键）。
    #   两格紧挨着，靠行走方向天然分先后 → 下去先"脱"(y小)再"穿"(y大)，回来先"穿"(空操作)再"脱" ⇒ 恒说的"原路返回才换回"。
    "温泉(女更衣室·穿泳装)": {"map": "BathHouse_WomensLocker","pos": (2, 17),"note": "🩳 `TouchAction: ChangeIntoSwimsuit` → bathingClothes=true + **canOnlyWalk=true**（变慢的真身）。✅2026-09-10 真机验证：走到这格 → /pool 报 bathingClothes:True canOnlyWalk:True"},
    "温泉(女更衣室·脱泳装)": {"map": "BathHouse_WomensLocker","pos": (2, 16),"note": "🩳 `TouchAction: ChangeOutOfSwimsuit` → bathingClothes=false + canOnlyWalk=false。✅真机验证过。⚠️ 在 (2,17) 正上方一格，**只是路过也会触发**（回来时正好靠它换回便装）"},
    "温泉(男更衣室·穿泳装)": {"map": "BathHouse_MensLocker","pos": (15, 19),"note": "🩳 `TouchAction: ChangeIntoSwimsuit`（男侧，同女侧镜像）"},
    "温泉(男更衣室·脱泳装)": {"map": "BathHouse_MensLocker","pos": (15, 18),"note": "🩳 `TouchAction: ChangeOutOfSwimsuit`（男侧）"},
    # ── ♨️ 泳池 BathHouse_Pool（28×34）（2026-09-10 恒带路校准）──
    #   地形：甲板 y3~7 × x1~26（干区）→ 两个「凹型凸起」水槽 x5~7 / x20~22（y8~11）
    #   → 水区 y≈9~18 × x5~22 → 池底通道 (13,19)/(14,19) 往南。
    #   ⚠️ `isWater` 在浴场泳池**恒 false**（/dump_tile 认不出水）→ 判水只能按坐标区间，别信 isWater。
    #   🎯 2026-09-10 反编译定论：泳池那汪水**引擎压根不认**——`isWaterTile` 查 Back 层 "Water" 属性（泳池格全无），
    #      且 `GameLocation.waterTiles` 只在 (isOutdoors ‖ 地图属性 indoorWater ‖ Sewer ‖ Submarine) 且非 Desert 才建 ⇒ 室内泳池恒 null。
    #      游泳的唯一权威 = `Character.swimming` 这个 NetBool，**跟水格无关**。下水/上岸全靠踩 `TouchAction: PoolEntrance` 格：
    #        在岸踩 → swimming=true（落水动画+yVelocity）；在水里踩 → swimming=false（jump 跳上岸）。
    #      ✅ 真机验证：站 (6,0)→/move (6,9) 踩上 → swimming:True；再 /move (6,9) → swimming:False。
    "温泉(泳池·甲板)":   {"map": "BathHouse_Pool","pos": (6, 7), "note": "♨️ **泳池甲板**（干区）y3~7 横条，x1~26。从左右水口下水"},
    "温泉(上水口左)":    {"map": "BathHouse_Pool","pos": (6, 9), "note": "♨️ **左上下水口** ★恒 2026-09-10 目测校准 (6,9)，随后 `/tile_props` 扫图**实证分毫不差**：Back 层 `TouchAction: PoolEntrance`。下水/上岸同一格——在岸踩=下水、在水里踩=跳上岸。从甲板 (6,7) 沿 x=6 往下走即触发"},
    "温泉(上水口右)":    {"map": "BathHouse_Pool","pos": (21, 9), "note": "♨️ **右上下水口**——`TouchAction: PoolEntrance`（男侧镜像，同样实证）。沿 x=21 往下下水"},
    "温泉(池底水口左)":  {"map": "BathHouse_Pool","pos": (7, 27), "note": "♨️ 池底那两处 `TouchAction: PoolEntrance`（y=27，x=7/x=20）——2026-09-10 扫图发现，恒带路时未知。同样是在岸下水/水里上岸。⚠️ 尚未真机走过这一段"},
    "温泉(池底水口右)":  {"map": "BathHouse_Pool","pos": (20, 27),"note": "♨️ 池底 `PoolEntrance`（右）"},
    "温泉(泳池水面)":    {"map": "BathHouse_Pool","pos": (13, 14),"note": "🏊 **泳池水区** ≈ y9~18 × x5~22（凹型 + 下面那片连通区）。泡澡/游泳站位就落这片；上岸走左右水口 (6,9)/(21,9)。⚠️ 水区里 `isWater` 恒 false、走位照常可寻路（就是普通可走地面），别按 isWater 判"},
    "莱纳斯帐篷(外)":    {"map": "Mountain",    "pos": (29, 7), "note": "莱纳斯帐篷外，篝火旁，warp(29,6)→Tent"},
    "莱纳斯帐篷(内)":    {"map": "Tent",        "pos": (2, 5),  "note": "帐篷内部"},
    "山湖钓鱼点(左)":    {"map": "Mountain",    "pos": (68, 24),"note": "山湖左岸，春季鱼王点位 ✅"},
    "山湖钓鱼点(右)":    {"map": "Mountain",    "pos": (79, 30),"note": "山湖右岸钓点 ✅"},
    "Mountain上口(去铁路)": {"map": "Mountain", "pos": (9, 0),  "note": "Mountain顶部，warp到Railroad"},
    "深山小路(→Mountain)": {"map": "Backwoods", "pos": (49, 14),"note": "Backwoods右出口warp到Mountain(0,13)"},
    "深山隧道(入口)":    {"map": "Backwoods", "pos": (22, 31),"note": "Backwoods隧道口→Tunnel"},
    "深山隧道(内部)":    {"map": "Tunnel",    "pos": (34, 9), "note": "隧道入口处"},
    "齐先生电池箱":      {"map": "Tunnel",    "pos": (17, 7), "note": "🧾 **齐先生「神秘的齐」任务开头**(TunnelSafe 隧道锁盒)：**手持电池组(787)朝箱交互**→消耗放电池入箱→开「神秘的齐」纸条；TunnelSafe=Tunnel(17,5)/(17,6)，**人站(17,7)朝0交互(17,6)**；机制=手持物(ActiveObject)+mail标记(TH_Tunnel)，**不是**DropBox/容器菜单（2026-08-29 /scan 实测坐标已定）"},
    "齐先生火车站台箱": {"map": "Railroad",  "pos": (45, 41),"note": "🧾 **齐先生「神秘的齐」步骤1b**(RailroadBox)：**手持彩虹贝壳(394)朝箱交互**→消耗+TH_Railroad+推「神秘的齐3」。机制=手持物(ActiveObject)+mail标记，**不是**DropBox容器；⚠️ 跟莱纳斯社区清理的 DropBox=Dumpster(28,36) 是俩箱,别混（2026-08-29 /scan 实测,站(45,41)朝0交互(45,40)）"},
    "齐先生镇长冰箱":   {"map": "ManorHouse","pos": (9, 5), "note": "🧾 **齐先生「神秘的齐」步骤2**(MayorFridge 刘易斯家冰箱)：**手持10甜菜朝冰箱交互**→消耗+TH_MayorFridge（恒确认纯手持，非容器菜单）；人站(9,5)朝0交互(9,4)；机制=手持物+mail标记（2026-08-29 /scan 实测坐标已定）"},
    "沙之巨龙嘴":       {"map": "Desert",    "pos": (9, 37),"note": "🧾 **齐先生「神秘的齐」步骤3**(SandDragon 沙漠沙之巨龙嘴)：**手持日光精华(768)朝龙嘴交互**→消耗+TH_SandDragon+推「神秘的齐4」(需先 TH_MayorFridge 即②完成)；SandDragon=Desert(9,36)/(10,36)；人站(9,37)朝0交互(9,36)；机制=手持物+mail标记（2026-08-29 /scan 实测坐标已定）"},
    "齐先生收集箱":     {"map": "QiNutRoom",  "pos": (1, 5), "require_order": {"any_keywords": ["五彩碎片", "收集箱"]}, "note": "🧾 **齐先生特别订单交付点**(「四颗宝石」放4五彩碎片 / 「齐先生的五彩农场」放红橙黄绿蓝紫各100)：DropBox=QiNutRoom(1,4) box_id=QiChallengeBox；**人站(1,5)朝0交互(1,4)**开 QuestContainerMenu→放物品→ok 结算 Complete；奖励=Qi宝石(五彩农场≈35)+兑奖券；⚠️ 这是**特别订单容器机制**(同其它交付箱)，**非**「神秘的齐」纸条链手持机制；require_order=已接齐先生订单(进行中)才显示；旁有 QiChallengeBoard(2,3)=接单板、QiGemShop(11,3)=宝石商店、QiCat(13,4)（2026-08-29 /scan 实测坐标已定）"},
    "采石场矿车":        {"map": "Mountain",    "pos": (124,12),"note": "采石场桥头，需修桥才能从公会过来"},
    "采石场矿井(外)":    {"map": "Mountain",    "pos": (103, 18),"note": "采石场骷髅矿井入口，Mountain侧"},
    "采石场矿井(入口)":  {"map": "UndergroundMine","pos": (28, 96),"note": "一层骷髅矿井梯子下来处（地图名动态生成）"},
    "铁路(入口)":        {"map": "Railroad",    "pos": (29, 59),"note": "从Mountain上来到Railroad的入口"},
    "铁路(站台)":        {"map": "Railroad",    "pos": (35, 40),"note": "等火车的地方，可以捡掉落"},
    "魔女沼泽洞口":    {"map": "Railroad",    "pos": (54, 33),"note": "魔女沼泽洞穴入口，warp到WitchWarpCave(4,9)"},
    "魔女沼泽洞穴(内部)": {"map": "WitchWarpCave","pos": (4, 9), "note": "🧙 黑暗护身符洞穴内部（从铁路(54,33)进来落(4,9)=入口落点）；**传送阵在(4,5)**，站(4,6)面0 interact→女巫沼泽(20,42)，map_go 直达不需交互。需黑暗护身符(HasDarkTalisman)（2026-08-30 /warps+恒领跑实测，入口落点(4,9)/传送阵(4,5)别混）"},
    "法师地下室(落点)":  {"map": "WizardHouseBasement","pos": (2, 5), "note": "🧙 法师塔地下室(WizardHouseBasement)——**新地点未在 /warps**(查无出入瓦片，纯魔法地点)。AI 从这里进来落 (2,5)/(3,5)；含**法师传送阵**(进女巫区)与**幻觉神龛**；从法师塔进地下室的**入口待探测**。传送阵连通女巫小屋(三大神龛)。需先有相关进度（2026-08-30 恒指引）"},
    "幻觉神龛":        {"map": "WizardHouseBasement","pos": (12, 5),"note": "🎭 **幻觉神龛**(2026-08-30 恒+AI 实测)：站(12,5)面0 interact→DialogueBox「是否花500金使用幻觉神龛改变你的外表？」是/否。**500金重新捏脸**(可改发型/衣服/肤色等外观)。⚠️ 属花钱改外观，非破坏性。需黑暗护身符(进女巫区/法师地下室)。选否安全退出"},
    "女巫沼泽":        {"map": "WitchSwamp",  "pos": (20, 42),"note": "🧹 女巫沼泽：传送阵落点(20,42)，**→女巫小屋走 warp 瓦片(20,21)**；→铁路(20,50)也可回。需黑暗护身符（2026-08-30 实测：传送阵落点是(20,42)非(20,21)，(20,21)是小屋门侧）"},
    "女巫小屋(门口)":  {"map": "WitchSwamp",  "pos": (20, 21),"note": "🧙 女巫小屋门口（沼泽侧 warp 瓦片，站(20,21)→WitchHut(7,16)）。从传送阵落点(20,42)走过来到这→warp 进小屋。需黑暗护身符（2026-08-30 /warps实测）"},
    "女巫小屋(内)":    {"map": "WitchHut",    "pos": (7, 15), "note": "🧙 女巫小屋内部：**从外面站(20,21)→面0 interact 进来后落(7,15)**；出口门/回去warp瓦片在(7,16)→女巫沼泽(20,21)。需黑暗护身符（2026-08-30 AI实测：进来落(7,15)、(7,16)是出口）"},
    "私欲之黑暗神殿":  {"map": "WitchHut",    "pos": (2, 7), "note": "😈 **三大黑暗神殿·私欲**(2026-08-30 AI实测，**危险：不可逆**！站(2,7)面0 interact→DialogueBox:「你的孩子会变成鸽子飞走……是否确定献祭一块五彩碎片?」是/否确认。⚠️**真的把孩子变鸽子飞走**——AI 别碰,除非恒明确要求。选否安全退出。需黑暗护身符"},
    "夜惊之黑暗神殿":  {"map": "WitchHut",    "pos": (12, 7),"note": "😈 **三大黑暗神殿·夜惊**(2026-08-30 AI实测，**危险：永久**！站(12,7)面0 interact→DialogueBox:「古老的魔法保护屏障会被撤走，怪物会在夜间出没你的农场。是否确定献祭一个奇怪小包子?」是/否确认。⚠️**撤掉农场夜间怪物保护、怪物夜里刷**——利弊大,AI 别碰除非恒明确要求。选否安全退出。需黑暗护身符"},
    "记忆之黑暗神殿":  {"map": "WitchHut",    "pos": (7, 6), "note": "😈 **三大黑暗神殿·记忆**(2026-08-30 恒+AI 实测)：站(7,6)面0 interact→DialogueBox「雕像的锐利眼神透视你的身体……」纯叙述**无选项**。功能=**清除离婚/分手伴侣的记忆**(让前任不再恨你/恢复好感)。**因为本档还没离婚，所以只叙述勾选不了**——有了离婚经历才弹选项。⚠️ 会删除伴侣记忆,副作用需谨慎。需黑暗护身符"},

    # ── 沙漠 ──
    "沙漠(巴士站)":      {"map": "Desert",      "pos": (18, 27),"note": "巴士下车/上车点，warp回BusStop(22,10)"},
    "头骨矿洞(外)":      {"map": "Desert",      "pos": (8, 6),  "note": "头骨矿洞门口"},
    "头骨矿洞(内)":      {"map": "SkullCave",   "pos": (3, 4),  "note": "头骨矿洞内，梯子下100层"},
    "桑迪商店(门口)":    {"map": "Desert",      "pos": (6, 52), "note": "桑迪绿洲商店门口"},
    "桑迪商店(门内)":    {"map": "SandyHouse",  "pos": (4, 9),  "note": "桑迪店内入口处"},
    "桑迪商店(柜台)":    {"map": "SandyHouse",  "pos": (2, 7),  "note": "桑迪柜台：买杨桃种子/向日葵/饰品 ✅"},
    "桑迪商店(赌场)":    {"map": "SandyHouse",  "pos": (17, 1), "note": "赌场入口（需俱乐部会员卡）→Club(8,13)"},
    "赌场(门口)":        {"map": "Club",       "pos": (8, 12), "note": "🎰 赌场入口（2026-08-23 AI实测）：桑迪店门进→Club落地(8,13)，站(8,12)朝上(0)。需会员卡（读AI自己的clubCard，非MasterPlayer）"},
    "赌场里程熊(记录机)": {"map": "Club",       "pos": (3, 5),  "note": "🐻 赌场里程/杀怪记录机（熊，2026-08-23 恒确认+AI交互实测）：站(3,5)朝上(0)交互(3,4)→DialogueBox 显示里程/杀怪等统计（labels 因 mod GBK 输出乱码，已见数值簇）"},
    "赌场卖币机":         {"map": "Club",       "pos": (12, 5), "note": "🪙 赌场卖币机（2026-08-23 AI实测）：站(12,5)朝上(0)交互(12,4)。纯对话框金币→游戏币，无需服务员"},
    "赌场无尽雕像贩子":   {"map": "Club",       "pos": (25, 4), "note": "🛒 赌场无尽财富雕像贩子（2026-08-23 AI实测）：站(25,4)朝上(0)交互(25,3)→对话框『1,000,000』（无尽财富雕像价）。赌场商店/齐先生贩子"},
    "赌场老虎机":         {"map": "Club",       "pos": (11, 11), "note": "🎰 出神老虎机（2026-08-23 AI实测+截图识别）：站(11,11)朝上(0)交互→游戏原生画面(非activeMenu,/menu读不到,靠截图/鼠标操作)。画面=🪙余额+3格转盘+按钮[赌注10/赌注100/完成]+右侧赔率表(图案组合×倍数)。操作=点按钮(赌注10/100下注,完成退出)。⚠️ 原生UI不响应esc/menu_click,鼠标点按钮才关；AI站位/交互格待真机复核"},
    "赌场21点":           {"map": "Club",       "pos": (3, 11), "note": "🃏 普通21点桌（2026-08-23 AI实测为100硬币赌注）：站(3,11)**朝上(0)**交互→**DialogueBox**(/menu可读:0开始/1离开/2规则)→选开始进牌局**原生画面**(加牌/停止按钮,鼠标点)。牌局面=庄家:?/赌注:100🟣/玩家手牌/轮次。⚠️ 取消/esc/menu_click(离开)都关不掉对话，只有 **menu_close 强关**成功；**小游戏进了只能打完一局不能中途退**。详情记忆"},
    "赌场大赌注21点":     {"map": "Club",       "pos": (24, 9), "note": "🃏 大赌注21点桌（2026-08-23 AI实测为**1000硬币起步**）：站(24,9)**朝下(2)**交互（⚠️ 跟普通21点朝上相反！）→**DialogueBox**(/menu可读:0开始/1离开，无规则选项)→选开始进牌局原生画面(加牌/停止)。注意方向：赌桌在 AI 下方，必须朝下(2)才交互得到。关闭同上(menu_close/打完一局)"},
    "沙漠商人":          {"map": "Desert",      "pos": (42, 24),"note": "沙漠贸易商：万象晶球/换物品"},
    "沙漠钓鱼点":        {"map": "Desert",      "pos": (9, 10), "note": "沙鱼/蝎子鲤钓点；站(9,10)朝下钓(9,11)水面（沙漠节 DesertFestival 同坐标）"},

    # ── 火山顶（Caldera） ──
    # ⚠️ Caldera 几乎全是岩浆，只有边缘一圈可走 + 锻造台平台 + 出口。
    # 落点必须用已验证的走格：user实测站 (22,22)（锻造台正南）、AI 交互从 (22,23)。
    "火山锻造台":        {"map": "Caldera",     "pos": (22, 21),"note": "🔥 锻造台（2026-08-11 实测）：武器附魔/合成戒指/龙牙附魔。人站(22,22)朝北交互，AI从(22,23)对角scene at(22,21)"},
    "火山顶出口":        {"map": "Caldera",     "pos": (11, 36), "note": "🌋 Caldera→入口层捷径的**站格**(11,36)（2026-09-12 按 /warps 落盘；旧值 (5,5) 是占位、已废弃）。⚠️ 真传送瓦片在 (11,37)，**站不上人** —— 走到 (11,36) 后换图靠 /warp（见 MAP_LINKS['Caldera']）"},

    # ── 火山第5层（VolcanoDungeon5）──
    # 反编译依据：`VolcanoDungeon.cs` 的 `GenerateContents` 尾部有 `if (level.Value != 5) return;`
    # ⇒ 下面这几样是**第5层专属**，别的层没有。✅ 2026-09-12 两样都真机验通了。
    # 第5层"村口"地形（`/passable_rect` 实测 x24~41/y26~38）：
    #   · 一条南北竖井走廊在 **x=32**（y26 直到 y37）—— 上下层就靠它
    #   · 井(水格) `(27~30,29~32)` ／ 矮人商店柜台 `(36,30)`（**不可走**，站旁边 interact）
    #   · 撤退出口 `(29,34)`（**不可走**，恒：站不上人）；站格 = 正西的 `(28,34)`
    "火山矮人商店":      {"map": "VolcanoDungeon5", "pos": (36, 30), "note": "🧔 火山矮人商店（`checkAction` case 77 @(36,30) → `OpenShopMenu(VolcanoShop)`）。✅ **2026-09-12 真机验过**：站 (36,31) 面朝北 `/interact {x:36,y:30}` → 开出 `ShopMenu`。顺带证实**轮回懂矮人语** —— 反编译里 `canUnderstandDwarves` 为假只会 `doEmote(8)` 冒个表情、**不开菜单**，它开了。(36,30) 本身**不可走**（柜台格），站旁边那排 interact 即可"},
    "火山第五层出口":    {"map": "VolcanoDungeon5", "pos": (29, 34), "note": "🦜 第5层撤退出口 ✅ **2026-09-12 真机全通**：**站 (28,34)、面朝东(facingDirection=1)、`/interact {x:29,y:34}`** → 弹「**要走捷径离开火山吗？**」是/否 → `menu click(option=0)` 答「是」→ **落 IslandNorth (56,17)**（与反编译 `warpFarmer(IslandNorth,56,17,1)` 一字不差）。⚠️ **不是踩上去就传的 warp，是一格要 interact 再答对话的瓦片**（现有 warp/door/portal 三种 kind 都盖不住，所以**没进 MAP_LINKS**；恒拍板「像竖井一样自己撤」）。(29,34) 本身**不可走**。前置=mail flag `Island_VolcanoShortcutOut`（恒档已有，用 `map ops=unlocks` 查）"},

    # ── 森林 ──
    "玛妮牧场(门外)":    {"map": "Forest",      "pos": (90, 16),"note": "玛妮牧场门口（森林侧）"},
    "玛妮牧场(门内)":    {"map": "AnimalShop",  "pos": (13, 19),"note": "玛妮牧场入口处"},
    "玛妮牧场(柜台)":    {"map": "AnimalShop",  "pos": (12, 16),"note": "玛妮柜台：买鸡/鸭/牛/羊/饲料/加热器/挤奶器 ✅"},
    "巫师塔(门外)":      {"map": "Forest",      "pos": (5, 27), "note": "法师塔门口（森林侧）"},
    "巫师塔(门内)":      {"map": "WizardHouse", "pos": (8, 24), "note": "法师塔入口处"},
    "巫师塔(法师)":      {"map": "WizardHouse", "pos": (3, 18), "note": "法师位置：祝尼魔任务/开建筑/改宠物"},
    "帽子老鼠":          {"map": "Forest",     "pos": (34, 96),"note": "🎩 **帽子老鼠**(2026-08-30 AI 实测)：站(34,96)面? interact→**ShopMenu 帽子店**(25件头饰:老伙计帽/幸运蝴蝶结/凉帽/圆顶礼帽/墨西哥帽/牛仔帽/蝴蝶结/老鼠耳朵…)。⚠️**解锁条件=获得首个成就的第二天**才出现；**非好感NPC**(不能送礼/不会因好感变，就一个商人)。买帽=menu click 点商品+确定。关闭=menu close（2026-08-30 恒：帽子老鼠不属于好感npc）"},
    "秘密森林(入口外)":  {"map": "Forest",      "pos": (0, 7),  "note": "秘密森林入口在森林右侧，需钢斧"},
    "秘密森林(入口内)":  {"map": "Woods",       "pos": (58, 15),"note": "秘密森林入口处"},
    "秘密森林(钓点)":    {"map": "Woods",       "pos": (12, 18),"note": "木跃鱼钓点"},
    "秘密森林(香炸奶酪卷)":{"map": "Woods",     "pos": (9, 8),  "note": "Old Master Cannoli：放甜宝石莓→星之果实"},
    "秘密森林(硬木1)":   {"map": "Woods",       "pos": (24, 6), "note": "硬木桩×2（2×2），左上"},
    "秘密森林(硬木2)":   {"map": "Woods",       "pos": (29, 7), "note": "硬木桩×2（2×2），上中"},
    "秘密森林(硬木3)":   {"map": "Woods",       "pos": (46, 6), "note": "硬木桩×2（2×2），右上"},
    "秘密森林(硬木4)":   {"map": "Woods",       "pos": (26, 10),"note": "硬木桩×2（2×2），中"},
    "秘密森林(硬木5)":   {"map": "Woods",       "pos": (34, 26),"note": "硬木桩×2（2×2），左下"},
    "秘密森林(硬木6)":   {"map": "Woods",       "pos": (41, 26),"note": "硬木桩×2（2×2），右下"},
    "浣熊窝":            {"map": "Forest",      "pos": (57, 9),  "note": "1.6浣熊一家，修树桩后触发，可换物品"},
    "猪车(旅行货车)":    {"map": "Forest",      "pos": (27, 12), "note": "周五/周日来森林，卖随机稀有物品"},
    # ⚠️ 2026-10-05：这一行原来还有**第二条**同名 key（`森林河边钓点` → (70, 95)，note 写「❌待校准」）——
    #    dict **同名 key 后写的赢** ⇒ 它把上面 :191 那条 **恒 2026-10-05 亲站验过的 (20, 76)** 静默盖掉了
    #    （实测 `POI` 里这个名字回的就是 (70, 95)，`map go 森林河边钓点` 一直走去的是那儿）。
    #    已删；那个未校准坐标**没有丢**，就记在这一行里。
    #    📌 通式：往 POI 里加同名点时**先搜一遍**（`ast` 扫一遍 dict 字面量即可）—— 否则「改了没生效」。
    #   （旧行原文留档：pos (70, 95) · note 「森林河边钓鱼（河鱼/鲶鱼）❌待校准」）
    # ── 🎓 精通山洞（1.6）：五项技能**全部**到 10 级才进得去 ──
    #   布局（恒 2026-09-16 现场指认 + `/tile_props?scan=Action&location=MasteryCave` 实测）：
    #   五块碑嵌在三面墙上、中央一座基座看总进度。从左到右 = 战斗/采集/耕种/钓鱼/采矿。
    #   所有站位都经 `/passable` 全图扫描实测**可走**；进门落点 (7,11)，出洞踩 (7,12)。
    #   ⚠️ **奖励内容一律不写在这**——游戏 `MasteryTrackerMenu` 里就那份，写第二遍必然长歪。
    #      统一走 `menu read`（读的是菜单自己那个奖励列表，本地化后的官方文案）。
    "精通山洞(门口)":     {"map": "Forest",      "pos": (101, 73),"note": "🎓 1.6 精通山洞入口（森林右下角）。**站 (101,73) 面北 `interact (101,72)`** → 五技能全 10 级才放行（不够会弹提示说还差几项），放行后传进 MasteryCave(7,11)。旁边 (101,71) 也是同款格子（游戏 `MasteryRoom` Action，反编译 GameLocation.cs:8780）"},
    "精通山洞(中央基座)": {"map": "MasteryCave", "pos": (7, 9),  "note": "🎓 **中央基座**：站 (7,9) 面北 `interact (7,8)` → 开总览菜单（`MasteryTrackerMenu` which=-1）。**只显示精通等级进度条 + 五颗星**，看不到具体奖励；要看奖励得去摸对应的碑。`menu read` 读得到"},
    "精通山洞(耕种碑)":   {"map": "MasteryCave", "pos": (7, 6),  "note": "🎓 耕种精通碑（正中那块）：站 (7,6) 面北 `interact (7,5)` → `menu read` 看奖励 → 有未花掉的精通等级才点得动 `menu click(button=mainButton)`"},
    "精通山洞(钓鱼碑)":   {"map": "MasteryCave", "pos": (9, 6),  "note": "🎓 钓鱼精通碑：站 (9,6) 面北 `interact (9,5)` → `menu read` → `menu click(button=mainButton)` 领取"},
    "精通山洞(采集碑)":   {"map": "MasteryCave", "pos": (5, 6),  "note": "🎓 采集精通碑：站 (5,6) 面北 `interact (5,5)` → `menu read` → `menu click(button=mainButton)` 领取"},
    "精通山洞(战斗碑)":   {"map": "MasteryCave", "pos": (3, 7),  "note": "🎓 战斗精通碑（最左）：站 (3,7) 面北 `interact (3,6)` → `menu read` → `menu click(button=mainButton)` 领取。领了才解锁饰品槽"},
    "精通山洞(采矿碑)":   {"map": "MasteryCave", "pos": (11, 7), "note": "🎓 采矿精通碑（最右）：站 (11,7) 面北 `interact (11,6)` → `menu read` → `menu click(button=mainButton)` 领取"},
    "精通山洞(爷爷的信)": {"map": "MasteryCave", "pos": (10, 10),"note": "💌 地上那张信纸（Action `GrandpaMasteryNote`）：站 (10,10) 面北 `interact (10,9)` → 开 `LetterViewerMenu` 读爷爷的信；`menu read` 看内容、`menu click(button=close)` 收掉"},

    # ── 🏝️ 姜岛 ──
    "姜岛(码头)":         {"map": "IslandSouth", "pos": (21, 43),"note": "姜岛码头，从Willy鱼店坐船到姜岛的落点"},
    "姜岛(西桥头)":       {"map": "IslandSouth", "pos": (0, 11), "note": "IslandSouth西侧桥头，向西→IslandWest姜岛农场(104,41)"},
    "姜岛(东桥头)":       {"map": "IslandSouth", "pos": (34, 12), "note": "IslandSouth东侧桥头，向东→IslandEast丛林/度假村(0,41)"},
    "丛林(西入口)":       {"map": "IslandEast",  "pos": (0, 41), "note": "IslandEast丛林/度假村区西入口，从IslandSouth(34,12)桥过来"},
    "姜岛农场(东入口)":   {"map": "IslandWest",  "pos": (104, 41),"note": "姜岛农场(IslandWest)东侧入口，从IslandSouth(0,11)过来"},
    "姜岛小屋(门口)":     {"map": "IslandWest",  "pos": (77, 40), "note": "姜岛农场小屋门口，进门到IslandFarmHouse"},
    "姜岛图腾柱(→农场)": {"map": "IslandWest",  "pos": (72, 36), "note": "🔥 姜岛→农场图腾柱（**固定不可移**，2026-08-15恒实测）：站(72,37)朝上→key confirm→传回 Farm(48,7)。⚠️右键/interact不触发，必须 confirm；传送后等~2秒"},
    "农场图腾柱落点(姜岛回)": {"map": "Farm", "pos": (48, 7), "note": "🔥 **姜岛**图腾柱传回农场的落点(48,7)——姜岛图腾柱固定，故落点也固定（2026-08-30 恒：姜岛的回家图腾柱不可移动，农场的才可移）。农场自家的大图腾柱(岛/沙漠/水/土)是可挪建筑，固定坐标误导→已删，动态走 /farm_buildings 的 *_Obelisk 定位+confirm（见 _obelisk_plan）。"},
    "姜岛小屋(门内六人房)": {"map": "IslandFarmHouse","pos": (14, 15),"note": "姜岛小屋内部，六张床的大通铺，map 30x18"},
    "姜岛农场(南沙滩蚌矿)": {"map": "IslandWest",  "pos": (70, 73), "note": "农场南侧沙滩，有蚌矿石(Clam rocks)可挖，捡拾翻找得蚌"},
    "姜岛农场(南桥拾贝)":  {"map": "IslandWest",  "pos": (42, 77), "note": "农场西南过桥的拾贝区，可捡珊瑚/海胆/贝壳等海滩采集品"},
    "齐钻核桃房(门口)":   {"map": "IslandWest",  "pos": (20, 23), "note": "齐钻核桃房(Walnut Room/QiNutRoom)门口，在IslandWest西北"},
    "齐钻核桃房(内)":     {"map": "QiNutRoom",   "pos": (7, 8),  "note": "齐先生核桃房内部(15x10)，**真实落点 (7,8)**（反编译 IslandWest.cs:336；2026-10-05 补28c 由 (7,7) 改正），接齐钻任务/兑换物品"},
    "齐先生任务板":      {"map": "QiNutRoom",   "pos": (3, 4),  "note": "📜 齐先生任务板(QiNutRoom)：接齐钻任务/挑战；人站(3,4)朝上交互(3,3)开 SpecialOrdersBoard；menu read 看任务卡→menu click(button=acceptLeftQuestButton/acceptRightQuestButton)接；⚠️与社区布告栏同型(2026-08-22 AI现场检测+实测接单，accept_quest已退役)"},
    "姜岛农场(鹦鹉特快)": {"map": "IslandWest",  "pos": (74, 9),  "note": "农场上方鹦鹉特快站，给金核桃解锁后快速传送"},
    "火山区域(鹦鹉特快)": {"map": "IslandNorth", "pos": (60, 17), "note": "IslandNorth火山入口区鹦鹉特快站"},
    "火山(入口)":         {"map": "IslandNorth", "pos": (41, 24), "note": "🌋 火山入口(IslandNorth)(恒2026-09-06牵定)：火山墙间隙(41,24)，别往里钻；旁边 Statue Of The Dwarf King(42,26)可摸；进门warp(39-42,20)→VolcanoEntrance→VolcanoDungeon0"},
    "火山矿井(入口)":      {"map": "VolcanoDungeon0", "pos": (31, 50), "note": "🌋 火山矿井入口层落点(2026-09-06恒牵定，避免顶部不稳定warp)：入口窄，落地(31,50)别硬钻墙——往下是VolcanoDungeon1"},
    "办事处(门口)":       {"map": "IslandNorth", "pos": (46, 47), "note": "姜岛办事处/Field Office门口（IslandNorth右下方），可捐赠化石"},
    "办事处(室内)":       {"map": "IslandFieldOffice", "pos": (4, 9), "note": "姜岛办事处内部入口，捐赠化石/领奖励"},
    "蜗牛教授":           {"map": "IslandFieldOffice", "pos": (8, 8), "note": "蜗牛教授柜台(IslandFieldOffice)，站(8,8)面向互动；2026-08-02实测，用户校准；帐篷靠走进去不是右键"},
    "挖掘场(蘑菇洞门口)": {"map": "IslandNorth", "pos": (22, 48), "note": "IslandNorth挖掘场/蘑菇洞门口，可挖化石，进洞采蘑菇"},
    "蘑菇洞(内)":         {"map": "IslandNorthCave1", "pos": (6, 10), "note": "姜岛蘑菇洞内部(12x12)，可采集蘑菇"},
    "挖掘场(鹦鹉特快)":   {"map": "IslandNorth", "pos": (5, 48), "note": "IslandNorth挖掘场区鹦鹉特快站"},
    "姜岛商人":           {"map": "IslandNorth", "pos": (35, 75), "note": "姜岛商人(IslandParrot)去火山路上拐角，面向右互动弹ShopMenu，2026-08-02实测"},
    "丛林(鹦鹉特快)":     {"map": "IslandEast",  "pos": (28, 29), "note": "IslandEast丛林/度假村区鹦鹉特快站"},
    "雷欧小屋(门口)":     {"map": "IslandEast",  "pos": (22, 11), "note": "雷欧(Leo)的小屋门口，在姜岛东部丛林"},
    "雷欧小屋(内)":       {"map": "IslandHut",   "pos": (7, 12), "note": "雷欧小屋内部(16x16)"},
    "雷欧树屋(门口)":     {"map": "Mountain",    "pos": (16, 8), "note": "🌳 雷欧(Leo)树屋门口(雪山)，面0 interact(16,7) 进屋"},
    "雷欧树屋(内)":       {"map": "LeoTreeHouse","pos": (3, 8),  "note": "雷欧树屋内部（雷欧6心搬来住/常在树屋）"},

    # ── 🐄 畜棚/鸡舍 ──
    "高级鸡舍(门内)":     {"map": "Deluxe Coop", "pos": (2, 9),  "note": "高级鸡舍内部入口(23x10)，进门位置"},
    "高级畜棚(门内)":     {"map": "Deluxe Barn", "pos": (11, 14),"note": "高级畜棚内部入口(25x15)，进门位置"},
}

# ═══════════════════════════════════════════════════════════════
#  ♨️ 浴场专用常量（2026-09-10 浴室专题②：反编译 + `/tile_props` 扫图实证）
#
#  换装/游泳**全是 Back 层瓦片属性 `TouchAction`**（踩到新格的下一个 tick 自动触发，不用按键；
#  唯一闸门 Game1.eventUp）——**不是地形、不是脚本区、也不是 interact 推门**。
#  ⚠️ 泳池那汪水**引擎压根不认**：`isWaterTile` 查 Back 层 "Water" 属性（泳池格全无），
#     且 `waterTiles` 数组只在 (isOutdoors‖地图属性 indoorWater‖Sewer‖Submarine) 且非 Desert 才建
#     ⇒ 室内泳池恒 null。**"在水里"的唯一权威 = Character.swimming 这个 NetBool。**
# ═══════════════════════════════════════════════════════════════

# 🩳 换泳装/换回便装：更衣室里**紧挨着的两格**，靠行走方向天然分先后
#    （下去先 "out"(y小) 再 "into"(y大)；回来先 "into"(空操作) 再 "out"）⇒ 恒说的"原路返回才换回"。
#    效果 = bathingClothes + **canOnlyWalk**（"变慢"的真身：换完只能走不能跑）。
BATH_CHANGE_TILES = {
    "BathHouse_WomensLocker": {"into": (2, 17), "out": (2, 16)},    # 女（轮回是 Female，走这条）
    "BathHouse_MensLocker":   {"into": (15, 19), "out": (15, 18)},  # 男
}

# 🏊 `TouchAction: PoolEntrance`：**在岸踩=下水(swimming=true)，在水里踩=上岸(swimming=false)**，同一格两用。
#    ⚠️ swimming **不限制走位**（全代码只有切换/禁交互/晕倒/视觉四处引用）⇒ 逛上甲板不会自动退水，
#       **必须再踩一次 PoolEntrance 才算上岸**，否则一直挂着"在水里"的状态且不能交互。
BATH_POOL_ENTRANCES = [(6, 9), (21, 9), (7, 27), (20, 27)]

# 🏊 泳池地形（BathHouse_Pool 28×34，恒 2026-09-10 带路校准；水区靠坐标，别信 isWater）
BATH_POOL_DECK_Y = (3, 7)                       # 甲板/干区：y3~7 × x1~26
BATH_POOL_WATER_X = (5, 22)                     # 水区 x5~22
BATH_POOL_WATER_Y = (9, 18)                     # 水区 y9~18（凹型水槽 x5~7 / x20~22 自 y8 起）
BATH_POOL_SOAK_SPOT = (13, 14)                  # 🛁 推荐的"发呆泡澡"落点（水区正中，对应 POI 温泉(泳池水面)）


def bath_guide_lines(loc_name: str) -> list:
    """♨️ 浴场场景引导（每日进入一次性）——供 nagi_mcp_server 的状态注入调用。

    2026-09-10 恒：「注入场景引导（每日进入一次性提醒）：上下水口坐标、可以在泳池里 walk_to 游泳、
    静止下来泡温泉以恢复体力。」三条都由**游戏代码实证**：
      · 上下水口 = BATH_POOL_ENTRANCES（`/tile_props` 扫图，扫出来的和恒目测分毫不差）
      · 水里可 walk_to = 水格就是普通可走地面（swimming 不限走位）
      · 回体力 = `Farmer.Update` 的 swimming 分支：**静止不动**时每 100ms `Stamina++` 且 `health++`
    """
    if not loc_name or not loc_name.startswith("BathHouse_"):
        return []
    ent = "、".join(f"({x},{y})" for x, y in BATH_POOL_ENTRANCES)
    wx0, wx1 = BATH_POOL_WATER_X
    wy0, wy1 = BATH_POOL_WATER_Y
    sx, sy = BATH_POOL_SOAK_SPOT
    chg = BATH_CHANGE_TILES.get(loc_name, BATH_CHANGE_TILES["BathHouse_WomensLocker"])
    def _p(t):
        return f"({t[0]},{t[1]})"
    return [
        "♨️ **温泉池**（今日首次提醒）：",
        f"  🩳 换泳装=更衣室踩 **{_p(chg['into'])}**（换完『只能走不能跑』是正常的，别当卡了）；"
        f"换回便装=踩 {_p(chg['out'])}",
        f"  🏊 **水口 {ent}**——在岸踩=下水、在水里踩=上岸（同一格两用）；水区 ≈ y{wy0}~{wy1} × x{wx0}~{wx1}，"
        f"**可以直接 walk_to 在水里游**（水格=普通可走地面）",
        f"  🛁 **原地静止**才回体力/血（每 0.1 秒 +1/+1）——泡澡就 walk_to 到 **({sx},{sy})** 发呆，别乱走",
        "  ⚠️ 引擎不认这汪水（isWater 恒 false），『在水里』只看 swimming 标记；它**不限制走位**，"
        "逛上甲板不会自动退水，**必须再踩一次水口才算上岸**",
    ]

# ═══════════════════════════════════════════════════════════════
#  🎯 POI 结构化站位+朝向（2026-08-16 恒：map walk 到 POI 后自动朝向，交互交给 AI）
#  face: 0上 1右 2下 3左；stand: 玩家站位（默认=pos；柜台类 pos 即站位）。
#  ⚠️ **只收固定可交互兴趣点**——农场设施（建筑/可移动物如畜棚/温室/图腾柱/出货箱）不在此表，
#     走 go_to/_resolve_place 动态检测（/farm_buildings），硬编码坐标会随建筑搬家失效。
#  ⚠️ 柜台 face=0 按"pos=站位、柜台在面前一格"惯例推断（皮埃尔实测 pos(4,19)=站、柜台(4,18)）；钓点/导航地标是纯位置不需要朝向。
#  ⚠️ 2026-08-16 恒：宠物水碗不在此表——它是 pet 工具专属（浇水交互，pet_pets 处理），map walk 不扛浇水。
# ═══════════════════════════════════════════════════════════════
POI_FACE = {
    # 矿车（站格朝上，交互目标在面前）
    "巴士站(矿车)":      {"face": 0},                      # 站(14,4)朝上交互(14,3)
    "城镇矿车":          {"face": 0},                      # 站(105,80)朝上
    # 售票机（站位=机子下方一格）
    "巴士站(售票处)":    {"face": 0, "stand": (17, 12)},  # 机子(17,11)，站位(17,12)朝上
    "姜岛船坞(售票)":    {"face": 0, "stand": (4, 10)},   # 机子(4,9)，站位(4,10)朝上
    "电影院售票处":      {"face": 0, "stand": (98, 52)},  # 机子(98,51)，站位(98,52)朝上买电影票(2026-08-16恒实测)
    "电影院(门口)":      {"face": 0, "stand": (95, 51)},  # 门(95,50)在面前，站位(95,51)朝上
    "Joja超市(门口)":    {"face": 0, "stand": (95, 51)},  # 🏬 同一扇门；形态=Joja超市（补25 门瓦片判据）
    "废弃超市(门口)":    {"face": 0, "stand": (95, 51)},  # 🏚 同一扇门；形态=废弃超市（补25）
    "书摊(马尔赛罗)":    {"face": 0, "stand": (110, 27)}, # 书摊在面前(对话买/回收书)
    "社区布告栏(特别任务板)": {"face": 0, "stand": (62, 94)}, # 📋 站(62,94)朝上交互(62,93)开特别任务板(年1秋2后)（2026-08-22 AI现场检测）
    "社区布告栏(特别任务领奖箱)": {"face": 0, "stand": (60, 94)}, # 📬 站(60,94)朝上交互(60,93)领特别订单兑奖券(板左2格)（2026-08-29 AI现场实测）
    "刘易斯家(特别订单兑奖机)": {"face": 0, "stand": (1, 6)}, # 🎰 站(1,6)朝0交互(1,5)开兑奖菜单换兑奖券（2026-08-29 AI现场实测）
    "皮埃尔商店(求助布告栏)": {"face": 0, "stand": (42, 57)}, # 📋 每日求助栏(Help Wanted)：站(42,57)朝上交互(42,56)开 Billboard（2026-08-29 AI现场检测）
    "齐先生任务板":      {"face": 0, "stand": (3, 4)},   # 📜 站(3,4)朝上交互(3,3)开齐钻任务板（2026-08-22 AI现场检测）
    # 🎇 夜市点位（2026-08-16 恒：柜台/商人在上方,全朝上交互）
    "夜市咖啡商人":      {"face": 0, "stand": (14, 38)},
    "夜市装饰商船":      {"face": 0, "stand": (19, 34)},
    "夜市猪车(旅行货车)": {"face": 0, "stand": (39, 31)},
    "猪车(旅行货车)":   {"face": 0, "stand": (27, 12)},  # 🐷 周五/周日森林猪车，站(27,12)朝上(2026-08-20恒实测)
    "夜市美人鱼船":      {"face": 0, "stand": (58, 32)},
    "夜市魔法商船":      {"face": 0, "stand": (48, 35)},
    "夜市画家Lupini":    {"face": 0, "stand": (43, 35)},
    "夜市裹布人":        {"face": 0, "stand": (32, 35)},
    "夜市钓鱼潜艇":      {"face": 0, "stand": (5, 35)},
    "潜艇艇长":          {"face": 0, "stand": (2, 10)},
    # 锻造台/石碑/教授（朝北交互）
    "火山锻造台":        {"face": 0, "stand": (22, 22)},  # 人站(22,22)朝北交互
    # 🎓 精通山洞（2026-09-16）：五块碑 + 基座 + 信纸，全部站下面那格**朝北**交互。
    #    stand 不写 ⇒ 用 POI 自己的 pos（七条 pos 都是实测可走的站位）。
    "精通山洞(门口)":     {"face": 0, "stand": (101, 73)},
    "精通山洞(中央基座)": {"face": 0},
    "精通山洞(耕种碑)":   {"face": 0},
    "精通山洞(钓鱼碑)":   {"face": 0},
    "精通山洞(采集碑)":   {"face": 0},
    "精通山洞(战斗碑)":   {"face": 0},
    "精通山洞(采矿碑)":   {"face": 0},
    "精通山洞(爷爷的信)": {"face": 0},
    "蜗牛教授":          {"face": 0},                      # 站(8,8)面向互动
    # 商店柜台（pos=站位，柜台在面前一格，朝上）
    "皮埃尔商店(柜台)":  {"face": 0, "stand": (4, 19)},    # 站(4,19)朝上，柜台(4,18)
    # 🗿 2026-10-04 真机验过的公会三个点位（都朝上、stand 就是 POI 的 pos）
    "探险家公会(马龙柜台)": {"face": 0, "stand": (5, 13)},
    "探险家公会(吉尔)":     {"face": 0, "stand": (11, 13)},
    "探险家公会(讨伐清单)": {"face": 0, "stand": (8, 11)},
    "皮埃尔商店(背包升级)": {"face": 0, "stand": (7, 19)}, # 🎒 站(7,19)朝上交互(7,18) BuyBackpack（2026-08-18 /scan 实测）
    "皮埃尔商店(优选交付箱)": {"face": 0, "stand": (19, 29)}, # 🧺 站(19,29)朝上交互(19,28)放金星菜进箱(接Pierre订单才显示)（2026-08-22 恒带路,坐标待确认）
    "沙漠钓鱼点":      {"face": 2, "stand": (9, 10)},   # 🎣 站(9,10)朝下钓(9,11)水面（沙漠节 DesertFestival 同坐标，2026-08-18 实测）
    "赌场(门口)":      {"face": 0, "stand": (8, 12)},   # 🎰 站(8,12)朝上(0)（2026-08-23 AI实测入口站位；进门落地(8,13)再上一格）
    "赌场里程熊(记录机)": {"face": 0, "stand": (3, 5)},   # 🐻 站(3,5)朝上(0)交互(3,4)（2026-08-23 恒确认=里程/杀怪记录机+AI交互实测）
    "赌场卖币机":     {"face": 0, "stand": (12, 5)},   # 🪙 站(12,5)朝上(0)交互(12,4)（2026-08-23 AI实测卖币机）
    "赌场无尽雕像贩子": {"face": 0, "stand": (25, 4)},  # 🛒 站(25,4)朝上(0)交互(25,3)（2026-08-23 AI实测无尽财富雕像贩子/药店）
    "赌场老虎机":     {"face": 0, "stand": (11, 11)},  # 🎰 站(11,11)朝上(0)交互(11,10)（2026-08-23 AI实测出神老虎机）
    "赌场21点":       {"face": 0, "stand": (3, 11)},  # 🃏 普通21点 站(3,11)朝上(0)交互(3,10)（2026-08-23 AI实测）
    "赌场大赌注21点": {"face": 2, "stand": (24, 9)},  # 🃏 大赌注21点 站(24,9)**朝下(2)**交互(24,10)（2026-08-23 AI实测；赌桌在下方）
    "皮埃尔商店":        {"face": 0},
    "星之果实餐吧(柜台)": {"face": 0, "stand": (10, 20)},
    "星之果实餐吧(可乐机)": {"face": 0, "stand": (38, 18)}, # 🥤 站(38,18)朝上交互(38,17)买Joja可乐75g（2026-08-22 恒 7842 检测）
    "酒吧冰箱(格斯煎蛋卷)": {"face": 0, "stand": (18, 17)}, # 🧾 站(18,17)朝上交互(18,16)放蛋进冰箱(接Gus订单才显示)（2026-08-22 恒带路,坐标待确认）
    "潘姆拖车(厨房柜)":   {"face": 0, "stand": (10, 7)},   # 🗄️ 站(10,7)朝上交互(10,6)DropBox=PamKitchen 放12果汁进柜(接Pam订单才显示)（2026-08-29 /scan 实测）
    "木匠商店(木头堆)":   {"face": 0, "stand": (10, 20)},  # 🪵 站(10,20)朝上交互(10,19)DropBox=RobinWood 放80硬木进堆(接Robin订单才显示)（2026-08-29 /scan 实测）
    "火车站(垃圾箱)":     {"face": 0, "stand": (28, 37)},  # 🗑️ 站(28,37)朝上交互(28,36)DropBox=Dumpster 放20垃圾进箱(接Linus订单才显示)（2026-08-29 /scan 实测；齐先生箱=45,40）
    "齐先生电池箱":       {"face": 0, "stand": (17, 7)},    # 🔋 站(17,7)朝上交互(17,6)TunnelSafe 放电池组(787)开神秘齐(任务开头)（2026-08-29 /scan 实测）
    "齐先生火车站台箱":   {"face": 0, "stand": (45, 41)},  # 🐚 站(45,41)朝上交互(45,40)手持彩虹贝壳(394)(齐先生神秘的齐步骤1b)（2026-08-29 /scan 实测）
    "齐先生镇长冰箱":     {"face": 0, "stand": (9, 5)},    # 🥬 站(9,5)朝上交互(9,4)放10甜菜进冰箱(齐先生神秘的齐步骤2)（2026-08-29 /scan 实测）
    "沙之巨龙嘴":         {"face": 0, "stand": (9, 37)},   # 🐉 站(9,37)朝上交互(9,36)手持日光精华(768)(齐先生神秘的齐步骤3)（2026-08-29 /scan 实测）
    "齐先生收集箱":       {"face": 0, "stand": (1, 5)},    # 📦 站(1,5)朝上交互(1,4)DropBox=QiChallengeBox 放单改物品(齐先生特别订单交付)（2026-08-29 /scan 实测）
    "冰淇淋摊位":        {"face": 0, "stand": (88, 93)},   # 🍦 站(88,93)朝上，柜体(88,92)，Alex(88,91)（2026-08-22 AI现场检测）
    "铁匠铺(柜台)":      {"face": 0, "stand": (3, 15)},   # ✅ 2026-08-17 恒验证：站(3,15)朝上交互克林特(3,13)
    "矮人商店":          {"face": 0, "stand": (43, 7)},  # 🧱 站(43,7)朝上(0)正对矮人(43,6)（2026-08-23 恒带路+AI现场检测）
    "木匠商店(柜台)":    {"face": 0, "stand": (7, 20)},
    "哈维医院(柜台)":    {"face": 0, "stand": (6, 17)},
    "玛妮牧场(柜台)":    {"face": 0, "stand": (12, 16)},
    "鱼店(柜台)":        {"face": 0, "stand": (4, 6)},    # 🎣 威利柜台（FishShop(4,6)），待恒真机验证站位
    "鱼店(多汁的虫子桶)": {"face": 0, "stand": (37, 34)},   # 🪱 站(37,34)朝上交互(37,33)倒虫肉进桶(接虫肉订单才显示)（2026-08-22 恒带路,坐标待确认）
    "博物馆(柜台)":      {"face": 0},
    "博物馆(历史碎片投递箱)": {"face": 0, "stand": (6, 10)},   # 🦴 站(6,10)朝上交互(6,9)放骨类文物进箱(接Gunther订单才显示)（2026-08-22 恒带路,坐标待确认）
    "桑迪商店(柜台)":    {"face": 0},
}

# ═══════════════════════════════════════════════════════════════
#  🎪 社区中心献祭板（板瓦片 = 反编译 `CommunityCenter.getNotePosition`，CommunityCenter.cs:233）
#
#  ⚠️ **本表只为「捐物品」留着**：要往板上放东西时得走到板瓦片前 → scene at(板瓦片) → menu。
#     **查"做完没"别用这张表**，用 `bundle_status`（读 C# `/bundles`，站着不动）。
#     （本表目前**没有 Python 代码消费者**——旧 bundle_status 是唯一那个，已改读 `/bundles`；
#      留着的理由是捐物品时 AI 要照它走位。别再往表里加"实测快照"型字段，见下。）
#
#  ✅ **房名 ↔ area 号 2026-09-11 已用 `/bundles` 实测定死**（该端点返回游戏本地化的
#     `name` + `name_en`，拿它俩一对照即可——不用去解**压缩**的 xnb（⚠️ 2026-10-05 实测头字节
#     `Flags=0x81` ⇒ **LZX**，本行原写"LZ4"是**错的**，别再照它写；解压这条路没必要走））：
#       0=茶水间 Pantry   1=工艺室 Crafts Room   2=鱼缸 Fish Tank
#       3=锅炉房 Boiler Room   4=金库 Vault   5=布告栏 Bulletin Board
#     ⚠️ **0 是茶水间、1 才是工艺室；4 是金库、5 才是布告栏**。本表原先这两对**写反了**
#        （0/1 互换、4/5 互换），2026-09-11 一并修正 —— 别照原样改回去。
#        反编译侧的英文权威 = `getAreaNameFromNumber`(:1238) 与 `getAreaNumberFromName`(:205)，
#        两边逐条对得上；`tile` 照 `getNotePosition`(:233) 抄，本来就按 area 号索引，一直是对的。
#     📌 6=Abandoned Joja Mart（废弃Joja超市的「遗失的收集包」）是**第 7 间、不属社区中心**，
#        恒 2026-09-11 亲口纠正过（影院那套是独立的），本表不收它。
#  🗑️ **`open` 字段已删**（2026-09-11）：它是 2026-08-16 本档一次性实测的快照，本存档献祭全做完后
#     仍是老样子 ⇒ 早烂了；而旧 `bundle_status` 正是信了它，每次调用都把 AI 跨 3 张图走过去把那
#     四块板挨个 interact（55 秒，最后撂在锅炉房板前）。**别再往这张表加"实测快照"型字段**（存档一变就骗人）。
#  交互：走到板附近 → scene at(板瓦片) → menu 读/点（areaNextButton/areaBackButton 切房间，
#        purchaseButton 购买）。
# ═══════════════════════════════════════════════════════════════
COMMUNITY_CENTER_BOARDS = {
    "茶水间": {"area": 0, "tile": (14, 5)},
    "工艺室": {"area": 1, "tile": (14, 23)},
    "鱼缸":   {"area": 2, "tile": (40, 10)},
    "锅炉房": {"area": 3, "tile": (63, 14)},
    "金库":   {"area": 4, "tile": (55, 6)},
    "布告栏": {"area": 5, "tile": (46, 11)},
}

# ═══════════════════════════════════════════════════════════════
#  🗺️ 地图链接详细标注（2026-08-13 任务#4：门 vs 出口瓦片 + 交互功能）
# ═══════════════════════════════════════════════════════════════
# kind:
#   "warp" = 出口瓦片：AI 走/传到该瓦片，站上去游戏自动 warp 到下张图（不用交互）
#   "door" = 建筑门：走门瓦片 + key confirm 进建筑（SDV 门是 Warp 属性，走上去自动传；
#             但 scene at(场景交互) 对门无效，进门用"走门 tile + confirm"——跟门交互行为）
# tile 是源地图上的瓦片坐标（None=建筑门坐标按农场类型/建筑位置动态，用门检测或 /warp_building 兜底）
MAP_LINKS = {
    "Trailer_Big": [{"tile": [13, 25], "target": "Town", "kind": "warp", "arrive": [72, 69], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}],
    "Sunroom": [{"tile": [5, 14], "target": "SeedShop", "kind": "warp", "arrive": [32, 4], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}],
    "IslandWestCave1": [{"tile": [6, 12], "target": "IslandWest", "kind": "warp", "arrive": [61, 5], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}],
    "IslandSouthEastCave": [{"tile": [0, 7], "target": "IslandSouthEast", "kind": "warp", "arrive": [30, 19], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}],
    "IslandSouthEast": [{"tile": [31, 18], "target": "IslandSouthEastCave", "kind": "warp", "arrive": [1, 8], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}, {"tile": [0, 28], "target": "IslandSouth", "kind": "warp", "arrive": [42, 29], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）；⚠️ 出口瓦片 x<0（边界外）⇒ 取边界内可达格"}],
    "DesertFestival": [{"tile": [8, 5], "target": "SkullCave", "kind": "warp", "arrive": [7, 8], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}, {"tile": [18, 26], "target": "BusStop", "kind": "warp", "arrive": [22, 10], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}],
    "Cellar": [{"tile": [3, 1], "target": "FarmHouse", "kind": "warp", "arrive": [19, 34], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}],
    "CaptainRoom": [{"tile": [0, 5], "target": "IslandWest", "kind": "warp", "arrive": [59, 92], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）；⚠️ 出口瓦片 x<0（边界外）⇒ 取边界内可达格"}],
    # ── 农场 ──
    "Farm": [
        {"tile": (79, 17), "target": "BusStop", "kind": "warp", "arrive": (11, 23), "note": "农场右侧口→巴士站。原出口瓦片标(80,15-18)，x=80 在宽度80边界外(x0..79)，walk_to 到不了=到不了巴士站根因；改边界内达格(79,17)(同 Farm→Backwoods 修正)，BusStop→Farm 落点正是(79,17)，站这里再/warp 巴士站(11,23)"},
        {"tile": (41, 64), "target": "Forest", "kind": "warp", "arrive": (68, 0), "note": "农场下口站格(41,64)→/warp 森林(68,0)。原出口(41,65) y=65 在高度65边界外,walk_to 到不了(同 Farm→BusStop 修正)"},
        # ⚠️ 2026-08-30 恒：出口瓦片标地图内可达格(40,1)——原(41,-1)地图外 walk_to 到不了；
        #   AI 不用原生 warp 触发(不稳定)，走"walk_to 到 (40,1) 站定 → /warp 到深山(14,39)"。
        {"tile": (40, 1), "target": "Backwoods", "kind": "warp", "arrive": (14, 39), "note": "农场上口站格(40,1)→/warp 深山(14,39)。原出口瓦片(41,-1)在地图外，已改边界内达格"},
        {"tile": (34, 5), "target": "FarmCave", "kind": "warp", "note": "农场洞穴口(34,5)→FarmCave(8,11)（/warps 实测）"},
        {"tile": None, "target": "FarmHouse", "kind": "door", "note": "主屋门（走门+confirm，建筑 warp 不在 /warps）"},
        {"tile": None, "target": "Cabin", "kind": "door", "note": "联机小屋门（多栋同名按建筑坐标）"},
        {"tile": None, "target": "Greenhouse", "kind": "door", "note": "温室门（需献祭解锁；温室四季可种）"},
    ],
    # ── 巴士站 ──
    "BusStop": [
        {"tile": (9, 22), "target": "Farm", "kind": "warp", "note": "巴士站左侧→农场(79,17)"},
        {"tile": (44, 22), "target": "Town", "kind": "warp", "note": "巴士站右侧→鹈鹕镇(0,54)"},
        {"tile": (11, 6), "target": "Backwoods", "kind": "warp", "note": "巴士站上口→深山(49,30)"},
        {"tile": (22, 8), "target": "Desert", "kind": "door", "note": "巴士上车→沙漠(18,27)，需车票（潘姆开车）"},
    ],
    # ── 深山 ──
    "Backwoods": [
        {"tile": (49, 28), "target": "BusStop", "kind": "warp", "arrive": (14, 8), "note": "深山右侧站格(49,28)→/warp 巴士站(14,8)。原出口(50,28) x=50 在宽度50边界外"},
        {"tile": (49, 10), "target": "Mountain", "kind": "warp", "arrive": (0, 13), "note": "深山右侧站格(49,10)→/warp 山(0,13)。原出口(50,10) x=50 越界"},
        {"tile": (14, 39), "target": "Farm", "kind": "warp", "arrive": (40, 0), "note": "深山下方站格(14,39)→/warp 农场(40,0)。原出口瓦片(13,40) y=40 在地图外(Backwoods 行0-39)，已改边界内达格；对侧 Farm→Backwoods 亦同(见 Farm(40,1))"},
        {"tile": (22, 31), "target": "Tunnel", "kind": "warp", "note": "隧道口→Tunnel(39,9)（warp 瓦片站上自动传送，/warps 实测 22,29-32→39,9）；齐先生电池箱在里头"},
    ],
    # ── 鹈鹕镇 ──
    "Town": [
        # ⚠️ 2026-09-13 恒：「这两个不是互通的吗」—— 对，四条全修。**病根：站格填的是对面那张图的坐标。**
        #   规律（用 Farm↔BusStop 反证过）：**A→B 的站格 = B→A 的落点** —— 游戏就是从那儿把你放下来的。
        #   全量依据 = 游戏自己的 `/warps`（反向 warp 的 targetX/targetY）：
        #     BusStop(44,22..25)→Town 落点(0,54) | Mountain(14..16,41)→Town 落点(81,0)
        #     Forest(120,24..27)→Town 落点(0,90) | Beach(38,-1)→Town 落点(54,108)
        #   前两条**真机实测过**（小星从巴士站 / 从山进镇，游戏就把它落在那两格上）。
        #   ⚠️ 这四个错值从 08-13 写字典那天就在（08-17 快照一字不差），一直没暴露是因为
        #     **末尾那句显式 `/warp` 会把结果掰正** —— 代价只是白走：真机量到站错格让
        #     Town→BusStop 白走 **76 格 / 22 秒**（逐秒采样，2026-09-13）。
        #   ⚠️ 08-30 `def36cb` 把 `link['tile']` 升成权威落点（`use_exact=True`）之前，这字段根本没人读。
        #   ⚠️ 站错格不只是费腿：站到**有特殊意义的格子**上会触发副作用（09-13 那个 4928 次
        #     `Warping to Town` 死循环就是站格落在 warp 格上引的）。
        #   ⚠️ 2026-09-13 恒「是不是站得太边了」—— 对，四条里有三条**正好压在边界行/列**（x=0 或 y=0）。
        #     游戏落点本身是能站的，但**别站在边界上**（今天刚被边界格坑过一整天）⇒ 统一**往图内挪一格**：
        #       (0,54)→(1,54) / (0,90)→(1,90) / (81,0)→(81,1) / (54,108)→(54,107)
        #     四格**全部 `/dump_tile` 验过** `passable=true mapPassable=true isWater=false`（真机，2026-09-13）。
        #     ⚠️ 挪这一格不影响正确性：末尾是显式 `/warp <目的地>`（`HandleWarp` 自己挑当前图里 TargetName
        #     匹配的那条），**站在图内哪一格都跳得对**，站格只决定"从哪儿迈出这一步"。
        {"tile": (1, 54), "target": "BusStop", "kind": "warp", "note": "镇左侧→巴士站(42,23)。站格=BusStop→Town 落点(0,54)往图内一格；原错标(44,22)填的是**巴士站那格**的坐标"},
        {"tile": (1, 90), "target": "Forest", "kind": "warp", "note": "镇左上→森林(118,25)。站格=Forest→Town 落点(0,90)往图内一格；原错标(1,55)"},
        {"tile": (54, 107), "target": "Beach", "kind": "warp", "note": "镇下方隧道→海滩(38,0)。站格=Beach→Town 落点(54,108)往图内一格；原错标(53,96)"},
        {"tile": (81, 1), "target": "Mountain", "kind": "warp", "note": "镇上口→山(15,40)。站格=Mountain→Town 落点(81,0)往图内一格；原错标(15,40)填的是**山那格**的坐标"},
        {"tile": (53, 19), "target": "CommunityCenter", "kind": "door", "note": "社区中心门→(32,23)，祝尼魔献祭"},
        {"tile": (43, 56), "target": "SeedShop", "kind": "door", "note": "皮埃尔店门→SeedShop(6,29)，买种子/肥料"},
        {"tile": (36, 55), "target": "Hospital", "kind": "door", "note": "哈维医院门→(6,17)，看病/买药"},
        {"tile": (45, 70), "target": "Saloon", "kind": "door", "note": "星之果实餐吧门→(14,24)，格斯柜台"},
        {"tile": (94, 81), "target": "Blacksmith", "kind": "door", "note": "铁匠铺门→(5,19)，升级工具/买矿/开晶球"},
        {"tile": (57, 63), "target": "JoshHouse", "kind": "door", "note": "艾芙琳家(亚历克斯)门→(9,24)"},
        {"tile": (59, 85), "target": "ManorHouse", "kind": "door", "note": "镇长家门→(5,11)"},
        # 🆕 2026-09-17 补三户（此前**压根不在表里** ⇒ `map_go` 直接报"知识库没有地点链接"，
        #    连带 9 个 POI 全不可达，含 Pam 订单「烈酒」的交付点）。tile 必须与 BUILDING_DOORS 同值
        #    （`_REVERSE_DOORS` 的键就是 BUILDING_DOORS 的值，两边不一致就推不开门）。
        {"tile": (20, 88), "target": "HaleyHouse", "kind": "door", "note": "🌊 海莉&艾米丽家门→(2,24)（2026-09-17 真机验）"},
        {"tile": (10, 85), "target": "SamHouse", "kind": "door", "note": "🏠 乔迪/山姆/文森特/肯特家门→(4,23)（2026-09-17 真机验）"},
        {"tile": (72, 68), "target": "Trailer", "kind": "door", "note": "🚚 潘姆/佩妮拖车门→(12,9)（2026-09-17 真机验）"},
        {"tile": (101, 89), "target": "ArchaeologyHouse", "kind": "door", "note": "博物馆/图书馆门→(3,14)，捐矿物/古物"},
        {"tile": (35, 97), "target": "Sewer", "kind": "door", "note": "下水道口（需钥匙）→(16,11)，科罗布斯商店"},
        {"tile": (96, 50), "target": "MovieTheater", "kind": "door", "note": "电影院（前Joja超市）→(12,12)"},
        # 🏬 同一栋楼的另两种形态（**互斥**；闸在 POI 层 `joja_form`，导航只在 POI 被放行时才会走到这儿）：
        {"tile": (95, 50), "target": "JojaMart", "kind": "door", "note": "🏬 Joja超市门→(13,29)/(14,29)（原生 `LockedDoorWarp 13 29 JojaMart 900 2300`，2026-10-05 真机读）"},
        {"tile": (96, 50), "target": "AbandonedJojaMart", "kind": "door", "note": "🏚 废弃超市门（同两格由 `Town.cs:290-302` 瓦片 case 接管 → `warpFarmer(9,13)`）；⚠️与 MovieTheater 共用 (96,50)——两形态互斥，`_REVERSE_DOORS` 只会中一个（同 Trailer/Trailer_Big 那条老账）"},
    ],
    # ── 山 ──
    "Mountain": [
        {"tile": (1, 12), "target": "Backwoods", "kind": "warp", "note": "山左侧→深山(49,14)"},
        {"tile": (15, 40), "target": "Town", "kind": "warp", "arrive": (81, 0), "note": "山下口站格(15,40)→/warp 镇(81,0)，温泉旁。原出口(15,41) y=41 在高度41边界外"},
        {"tile": (9, 1), "target": "Railroad", "kind": "warp", "note": "山上口→铁路(29,59)"},
        {"tile": (54, 4), "target": "Mine", "kind": "door", "note": "矿井口→Mine(18,13)，下矿"},
        {"tile": (76, 8), "target": "AdventureGuild", "kind": "door", "note": "探险家公会门→(6,12)，买武器/怪物任务"},
        {"tile": (29, 6), "target": "Tent", "kind": "warp", "arrive": (2, 5), "note": "⛺ 莱纳斯帐篷 warp(29,6)→Tent(2,5)（2026-09-06 恒：/warps实测非门，站上自动传）"},
        {"tile": (12, 25), "target": "ScienceHouse", "kind": "door", "note": "罗宾木匠店门→(6,24)，买建筑/家具"},
        {"tile": (16, 8), "target": "LeoTreeHouse", "kind": "door", "arrive": (3, 8), "note": "🌳 雷欧树屋(雷欧6心搬来住/常在)：站(16,8)面0 interact(16,7)开树屋门→LeoTreeHouse(3,8)（2026-09-06 树屋门口实测）"},
    ],
    # ── 森林 ──
    "Forest": [
        {"tile": (119, 25), "target": "Town", "kind": "warp", "arrive": (0, 90), "note": "森林东口站格(119,25)→/warp 镇(0,90)。原出口(120,25) x=120 在宽度120边界外"},
        {"tile": (67, 0), "target": "Farm", "kind": "warp", "arrive": (41, 64), "note": "森林上口站格(67,0)→/warp 农场(41,64)。原出口(67,-1) y=-1 边界外(原靠push-in兜底,已显式标站格)"},
        {"tile": (0, 6), "target": "Woods", "kind": "door", "note": "秘密森林口（需钢斧）→Woods(59,15)，硬木/钓木跃鱼。door 走砖门动态路径,此 tile 仅标注用,原(-1,6) 边界外已回0"},
        {"tile": (5, 26), "target": "WizardHouse", "kind": "door", "note": "法师塔门→(8,24)，祝尼魔任务/改宠物"},
        {"tile": (90, 15), "target": "AnimalShop", "kind": "door", "note": "玛妮牧场门→(13,19)，买动物/饲料"},
        {"tile": (27, 12), "target": "Forest", "kind": "door", "note": "猪车（周五/周日旅行货车）"},
        {"tile": (104, 32), "target": "LeahHouse", "kind": "door", "note": "🏠 莉亚小屋→(7,9)（2026-09-17 补）。⚠️门是 `LockedDoorWarp … 1000 1800 Leah 500`：10:00–18:00 之外锁 + **Leah 好感≥500**"},
        # ⚠️ 2026-09-17：tile 由 (101,73) 改成 (101,72)，与 `BUILDING_DOORS["MasteryCave"]` 对齐
        #    （那张表的注释本就写着"给**目标 Action 格 (101,72)** 而不是站位 (101,73)"）。
        #    两边不一致时 `_REVERSE_DOORS` 查不到 ⇒ 推门那步**静默失效**、退回硬瞬移进屋。
        {"tile": (101, 72), "target": "MasteryCave", "kind": "door", "note": "精通山洞→落点(7,11)，五技能全10级才放行（2026-09-16 校准：旧注写 (7,9) 是基座站位，非落点）"},
    ],
    # ── 海滩 ──
    "Beach": [
        {"tile": (38, 1), "target": "Town", "kind": "warp", "note": "海滩→镇（隧道）"},
        {"tile": (30, 33), "target": "FishShop", "kind": "door", "note": "威利鱼店门→(5,9)，买鱼竿/鱼饵/蟹笼"},
        {"tile": (49, 10), "target": "ElliottHouse", "kind": "door", "note": "🏠 艾利欧特小屋→(3,9)（2026-09-17 补）。⚠️门是 `LockedDoorWarp … 1000 1800 Elliott 500`：10:00–18:00 之外锁 + **Elliott 好感≥500**，不够就弹「上锁了……」"},
    ],
    # ── 铁路 ──
    "Railroad": [
        {"tile": (29, 61), "target": "Mountain", "kind": "warp", "arrive": (9, 0), "note": "铁路下口站格(29,61)→/warp 山(9,0)。原出口(29,62) y=62 在高度62边界外"},
        {"tile": (33, 0), "target": "Summit", "kind": "warp", "arrive": (10, 29), "note": "铁路上口站格(33,0)→/warp 山顶(10,29)（需完美达成）。原出口(33,-1) y=-1 边界外"},
        {"tile": (10, 56), "target": "BathHouse_Entry", "kind": "door", "note": "浴场门→(5,9)，泡澡回体力"},
        {"tile": (54, 33), "target": "WitchWarpCave", "kind": "warp", "note": "魔女沼泽洞穴口(54,33)→WitchWarpCave(4,9)（/warps实测，2026-08-30）"},
    ],
    # ── 沙漠 ──
    "Desert": [
        # 🚌 返程（2026-09-21 恒）：**先走原生**（`navigation.BUS_RETURN`：走到站台 (18,27) → 接住弹出的
        #    原生对话框选"是" → 等动画）；这条 link 现在只当**没弹菜单时的 warp 兜底**用。
        {"tile": (18, 26), "target": "BusStop", "kind": "warp", "note": "巴士站→回鹈鹕镇巴士站(22,10)（返程，/warps实测）"},
        {"tile": (8, 5), "target": "SkullCave", "kind": "door", "note": "头骨矿洞口→(7,8)，下100层"},
        {"tile": (6, 51), "target": "SandyHouse", "kind": "door", "note": "桑迪绿洲店门→(4,9)，买杨桃种子/饰品"},
        {"tile": (42, 24), "target": "Desert", "kind": "door", "note": "沙漠商人（换万象晶球等）"},
    ],
    # ── 姜岛 ──
    # ⚠️ 2026-08-15 用实时 /warps 校准：岛的结构是 IslandSouth 为枢纽——西桥→IslandWest、东桥→IslandEast、北边→IslandNorth(火山区)。
    #    ❌ 不存在 IslandWest↔IslandNorth / IslandEast↔IslandNorth 直连（之前误加已删）；岛内快捷=金核桃解锁的鹦鹉特快（见 LOCKED_MAPS.parrotExpress）
    "IslandSouth": [
        {"tile": [42, 28], "target": "IslandSouthEast", "kind": "warp", "arrive": [0, 29], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}, {"tile": (0, 11), "target": "IslandWest", "kind": "warp", "note": "西桥头→姜岛农场(105,41)"},
        {"tile": (36, 12), "target": "IslandEast", "kind": "warp", "note": "东桥头→丛林/度假村(0,46)"},
        {"tile": (18, 0), "target": "IslandNorth", "kind": "warp", "arrive": (36, 89), "note": "北边小路站格(18,0)→/warp 火山入口区(36,89)。原出口(18,-1) y=-1 边界外"},
        # ⚠️ `tile` 必须是**站得住的格**（本表契约：`_map_go_walk` 拿它当"走到这再 /warp"的出口站格）。
        #    原标 `(17,44)` —— 那是 `/warps` 报的 **warp 触发格本身，站不住**：`/walk_to` 会把它
        #    就近改到 `(20,44)`，而旧代码仍在等 `(17,44)`（差 3 格 > `_wait_arrival` 的 ±2 容差）
        #    ⇒ **每次返航白站 25 秒**才 warp（恒 2026-09-19：「谜之停顿了至少20s才warp」）。
        #    `(20,44)` 是游戏自己算出的"最近可走格"，实测站得住、返航正常（CHANGELOG 09-19(85)）。
        {"tile": (20, 44), "target": "FishShop", "kind": "warp", "note": "码头→坐船返航直达鱼店(4,4)（站格 20,44；warp 触发格 17,44 站不住）"},
    ],
    "IslandWest": [
        {"tile": [61, 3], "target": "IslandWestCave1", "kind": "warp", "arrive": [6, 11], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}, {"tile": [60, 92], "target": "CaptainRoom", "kind": "warp", "arrive": [0, 5], "note": "📋 2026-10-06 按 `/warps` 总表补（原表缺这条 ⇒ `map go` 到不了）"}, {"tile": (106, 41), "target": "IslandSouth", "kind": "warp", "note": "东桥→IslandSouth(0,11)（/warps实测）"},
        {"tile": (77, 40), "target": "IslandFarmHouse", "kind": "door", "note": "姜岛小屋门"},
        {"tile": (20, 23), "target": "QiNutRoom", "kind": "door", "note": "🥥 齐钻核桃房：**站格=(20,23)**（人要站这儿）；**门格=(20,22)** 记在 `BUILDING_DOORS[\"QiNutRoom\"]`（Buildings **瓦片索引 1470**，⚠️**没有 Action**）。反编译 `IslandWest.cs:327-338`：1470 ⇒ 未解锁弹核桃计数、解锁则 `warpFarmer(\"QiNutRoom\",7,8,0)`；2026-10-05 补28c 真机读瓦片确认（原表把**站格**写进了门格那一位 ⇒ `map go` 到门口如实停）"},
        {"tile": None, "target": "IslandFarmCave", "kind": "door", "note": "农场洞穴(96,32)→IslandFarmCave(4,10)（2026-08-15补）"},
    ],
    "IslandNorth": [
        {"tile": (36, 89), "target": "IslandSouth", "kind": "warp", "arrive": (18, 0), "note": "南边站格(36,89)→/warp 岛南(18,0)。原出口(36,90) y=90 在高度90边界外"},
        {"tile": (40, 20), "target": "VolcanoEntrance", "kind": "warp", "arrive": (1, 1), "note": "火山口→火山入口(1,1)：/warps实测 39-42,20 站(40,20)warp进（非门，2026-09-06恒）"},
        {"tile": (46, 45), "target": "IslandFieldOffice", "kind": "door", "note": "办事处→(4,10)，捐化石（/warps实测）"},
        {"tile": (21, 45), "target": "IslandNorthCave1", "kind": "door", "note": "蘑菇洞→(6,11)（/warps实测）"},
    ],
    "IslandEast": [
        {"tile": (0, 46), "target": "IslandSouth", "kind": "warp", "arrive": (35, 12), "note": "西桥站格(0,46)→/warp 岛南(35,12)。原出口(-1,46) x=-1 边界外"},
        {"tile": (22, 9), "target": "IslandHut", "kind": "door", "note": "雷欧小屋→(7,13)（/warps实测）"},
        {"tile": (34, 30), "target": "IslandShrine", "kind": "door", "note": "神殿→(13,28)（/warps实测）"},
    ],
    "VolcanoEntrance": [
        {"tile": None, "target": "IslandNorth", "kind": "warp", "note": "火山入口出来→火山入口区(39,20)"},
        {"tile": None, "target": "VolcanoDungeon0", "kind": "door", "note": "火山地牢入口→火山矿井(37,4)（下矿/炸矿用）"},
    ],
    # ── 🌋 火山顶 Caldera（2026-09-12 恒：**先落盘，不实地走**）──
    # 来源：`/warps`（读游戏自己的 warp 表）+ 2026-09-12 恒实地走通一次。
    #   Caldera (11,37) → VolcanoDungeon0   ← **山顶直通入口层的捷径**（不用爬 9 层）
    #     ⚠️ **有前置**：必须在 `volcanoShortcutUnlocked` 这个 mail flag 下才通
    #        （首次到过 Caldera 次日自动补发 / 或在入口层矮人门机关踩下）。没解锁时这条是关的。
    #     ⚠️ 真实落点 = (44,51)（恒 2026-09-12 走出来实测），**不是** `/warps` 报的 targetX/Y (20,30)
    #        —— **warp 定义里的落点 ≠ 真实落地**，本表以实地为准。这是个新坑，见 CHANGELOG 09-12⑨。
    #   Caldera (21,40) → VolcanoDungeon9 (-1,-1)   ← 逐层往下（落点由游戏自选）
    # ⚠️ 关键：`(11,37)` 是**传送瓦片，站不上人** —— `/passable` 两轮独立探皆为 false，
    #    迷宫视图看是"只朝北开口的单格凹槽"。硬把 Position 放上去也**不触发** warp
    #    （真机实测：静置无反应；隔壁 (21,40) 是在位置发生变化那一帧才触发的）。
    #    按 2026-08-30 恒定的惯例 —— **"AI 不用原生 warp 触发(不稳定)"** —— 所以 `tile` 标
    #    **边界内可达格 (11,36)**（真机 walk_to 到过），站定后由 navigation 用 `/warp` 换图。
    # ⚠️ 回程走这条路时 `map_go` 会撞 `_volcano_gate()`（navigation.py:2097 判 `dest.startswith("Volcano")`）
    #    ⇒ **仍需 host(恒) 在矿井/火山里陪同才放行**。这是恒定的安全规矩，不是 bug，别顺手拆。
    "Caldera": [
        {"tile": (11, 36), "target": "VolcanoDungeon0", "kind": "warp", "arrive": (44, 50),
         "note": "🌋 山顶→入口层**捷径**（不用爬 9 层）。真出口瓦片=(11,37)（站不上人），站格取(11,36)。⚠️ **前置=mail flag `volcanoShortcutUnlocked`**，没解锁时这条不通（用 `map ops=unlocks` 查）。✅ **落点 2026-09-12 真机走通** = VolcanoDungeon0 **(44,50)**（`map ops=go` 实测；恒步行走出来时报 (44,51) —— 差 1 格，游戏 `warpFarmer` 落点取整所致，两格都 `passable:true` 无物体，不影响导航）。⚠️ `arrive` 就按实测 **44,50** 写；`/warps` 里这条报的是 targetX/Y=(20,30)，**与实际不符 —— warp 定义值 ≠ 真实落地，别信那个**。出洞后走 (37,4)→VolcanoEntrance→IslandNorth"},
        {"tile": (21, 40), "target": "VolcanoDungeon9", "kind": "warp",
         "note": "🌋 山顶→第9层（逐层下山用）。真出口瓦片=(21,40)，落点=(-1,-1) 游戏自选。⏳ tile 未校准（放上去过、静置不触发），用前先探站不站得住"},
    ],
    "IslandFarmCave": [{"tile": None, "target": "IslandWest", "kind": "warp", "note": "农场洞穴口→姜岛农场(96,33)"}],
    "IslandShrine": [{"tile": None, "target": "IslandEast", "kind": "warp", "note": "神殿门口→丛林(33,30)"}],
    # ── 室内 → 室外（恒 2026-08-13：室内对室外没有"门"，统一"站瓦片上 warp"→ kind=warp）──
    "FarmHouse": [{"tile": None, "target": "Farm", "kind": "warp", "note": "主屋门口站上→农场"}],
    "Cabin": [{"tile": None, "target": "Farm", "kind": "warp", "note": "小屋门口站上→农场"}],
    "FarmCave": [{"tile": None, "target": "Farm", "kind": "warp", "note": "洞穴口站上→农场"}],
    "Greenhouse": [{"tile": None, "target": "Farm", "kind": "warp", "note": "温室门口站上→农场"}],
    "SeedShop": [{"tile": None, "target": "Town", "kind": "warp", "note": "皮埃尔店门口→镇"}],
    "Hospital": [{"tile": (10, 20), "target": "Town", "kind": "warp", "note": "医院门口→镇(36,56)"},
                 {"tile": (10, 1), "target": "HarveyRoom", "kind": "warp", "note": "⬆️ 哈维房间上楼：医院内(9/10,1)是**上楼梯 warp**→HarveyRoom(6,12)；进房前要**先推两扇同图隔间门**——右门站(10,6)面0开门格(10,5)、左门站(9,6)面0开门格(9,5)。⚠️触发上楼 warp **必须用 /move 走上去**（walk_to/position/interact 都不触发！2026-09-10 恒带路+/move实测）"}],
    "HarveyRoom": [{"tile": (6, 13), "target": "Hospital", "kind": "warp", "note": "哈维房间出来→医院(10,2)"}],
    "Saloon": [{"tile": None, "target": "Town", "kind": "warp", "note": "餐吧门口→镇"}],
    "Blacksmith": [{"tile": None, "target": "Town", "kind": "warp", "note": "铁匠铺门口→镇"}],
    "CommunityCenter": [{"tile": None, "target": "Town", "kind": "warp", "note": "社区中心门口→镇"}],
    "JoshHouse": [{"tile": None, "target": "Town", "kind": "warp", "note": "艾芙琳家门口→镇"}],
    "ManorHouse": [{"tile": None, "target": "Town", "kind": "warp", "note": "镇长家门口→镇"}],
    # 🆕 2026-09-17 补五户（此前**都不在 MAP_LINKS** ⇒ `map_go` 报"知识库没有地点链接"）。
    #    `tile: None` 与邻居同款 = 出口瓦片运行时从 `/warps` 取；出口落点在 note 里备查。
    "HaleyHouse": [{"tile": None, "target": "Town", "kind": "warp", "note": "🌊 海莉&艾米丽家门口→镇（出口瓦片 (2,25)，落 Town(20,89)）"}],
    "SamHouse": [{"tile": None, "target": "Town", "kind": "warp", "note": "🏠 乔迪/山姆家→镇（出口瓦片 (4,24)，落 Town(10,86)）"}],
    "Trailer": [{"tile": None, "target": "Town", "kind": "warp", "note": "🚚 潘姆/佩妮拖车→镇（出口瓦片 (12,10)，落 Town(72,69)；2026-09-17 真机走验：站上去即回镇）"}],
    # ⚠️ `Trailer_Big`（升级版拖车）**故意不列**：它和 `Trailer` **共用同一扇门** (Town 72,68)，
    #    而 `_REVERSE_DOORS` 是「一格 → 一个建筑」的字典，两个都填会互相覆盖 ⇒ 反而把 Trailer 的进门弄坏。
    #    一个存档只会是其中一种；本档是 Trailer。(出口瓦片 (13,25) → Town(72,69))
    "ElliottHouse": [{"tile": None, "target": "Beach", "kind": "warp", "note": "🏠 艾利欧特小屋→海滩（出口瓦片 (3,10)，落 Beach(49,11)）"}],
    "LeahHouse": [{"tile": None, "target": "Forest", "kind": "warp", "note": "🏠 莉亚小屋→森林（出口瓦片 (7,10)，落 Forest(104,33)）"}],
    "ArchaeologyHouse": [{"tile": None, "target": "Town", "kind": "warp", "note": "博物馆门口→镇"}],
    "MovieTheater": [{"tile": None, "target": "Town", "kind": "warp", "note": "电影院门口→镇"}],
    "JojaMart": [{"tile": None, "target": "Town", "kind": "warp", "note": "🏬 Joja超市→镇（出口瓦片 (13,30)/(14,30)，落 Town(95,51)/(96,51)；2026-10-05 真机读 warps）"}],
    "AbandonedJojaMart": [{"tile": None, "target": "Town", "kind": "warp", "note": "🏚 废弃超市→镇（地图属性 `Warp: 9 14 Town 96 51` ⇒ 站 (9,14)，落 Town(96,51)；2026-10-05 真机读地图属性）"}],
    "ScienceHouse": [{"tile": None, "target": "Mountain", "kind": "warp", "note": "木匠店门口→山"},
                     {"tile": (13, 23), "target": "SebastianRoom", "kind": "warp", "note": "⬇️ 地下室楼梯：站(13,22)面下 踩(13,23)warp→SebastianRoom(1,1)（另一格(12,23)同效）。⚠️是**走上去的 warp**不是门（2026-09-10 恒带路校准）"}],
    "SebastianRoom": [{"tile": (1, 0), "target": "ScienceHouse", "kind": "warp", "note": "⬆️ 地下室出来楼梯：站(1,1)面下 踩(1,0)warp→ScienceHouse(12,21)（2026-09-10 恒带路校准）"}],
    "FishShop": [{"tile": None, "target": "Beach", "kind": "warp", "note": "鱼店门口→海滩"},
                 {"tile": None, "target": "BoatTunnel", "kind": "door", "note": "🚢 鱼店后门→船坞：**门格 (4,3)** 记在 `BUILDING_DOORS[\"BoatTunnel\"]`（Buildings `Action: WarpBoatTunnel`）；站格=(4,4)（POI「鱼店(姜岛船门)」）。反编译 `FishShop.cs:70-76`：需**威利后屋邀请** `willyBackRoomInvitation`，否则弹「上锁了……」；进了落 BoatTunnel **(6,12)**"}],
    "BoatTunnel": [{"tile": None, "target": "FishShop", "kind": "warp", "note": "船坞→鱼店"},
                   {"tile": None, "target": "IslandSouth", "kind": "door", "note": "上船→姜岛码头(21,43)，1000g（2026-08-15补）"}],
    "AnimalShop": [{"tile": None, "target": "Forest", "kind": "warp", "note": "玛妮牧场门口→森林"}],
    "WizardHouse": [{"tile": None, "target": "Forest", "kind": "warp", "note": "法师塔门口→森林"},
                    {"tile": (4, 5), "target": "WizardHouseBasement", "kind": "door", "note": "法师塔**盖板/暗地板**=**面前格(4,4)**，站(4,5)面0 interact(面前(4,4))→下地下室WizardHouseBasement；含幻觉神龛/法师传送阵。door如门（2026-08-30 恒+AI 实测）"}],
    "WizardHouseBasement": [{"tile": (4, 4), "target": "WizardHouse", "kind": "door", "note": "地下室**爬梯**=(4,4) 站此面0 interact→上法师塔WizardHouse(4,5)，再面0 interact面前(4,4)=盖板可回。door如门（2026-08-30 AI 实测）"}],
    "Woods": [{"tile": None, "target": "Forest", "kind": "warp", "note": "秘密森林→森林"}],
    "SandyHouse": [{"tile": None, "target": "Desert", "kind": "warp", "note": "桑迪店门口→沙漠"},
                   {"tile": (17, 1), "target": "Club", "kind": "warp", "note": "🎰 赌场入口（2026-08-23 恒拍板：**是出口瓦片 not 门**，走 map_go 的 warp 链）桑迪店(17,1)→warp→Club(8,13)；需会员卡（读AI自己的clubCard）。⚠️ 建筑室内普通/warp进不去，_walk_trigger_warp 已加 /warp_into 兜底"}],
    "Club": [{"tile": None, "target": "SandyHouse", "kind": "warp", "note": "赌场→桑迪店"}],
    "AdventureGuild": [{"tile": None, "target": "Mountain", "kind": "warp", "note": "公会门口→山"}],
    "Mine": [{"tile": None, "target": "Mountain", "kind": "warp", "note": "矿井口→山(54,5)"}],
    "SkullCave": [{"tile": None, "target": "Desert", "kind": "warp", "note": "头骨矿洞口→沙漠"}],
    "Sewer": [{"tile": (3, 49), "target": "Forest", "kind": "warp", "note": "下水道出口→森林(94,100)（/warps实测）"},
              {"tile": None, "target": "Town", "kind": "door", "note": "下水道镇内井盖(35,97)（恒2026-08-15：镇内口也在；交互/兜底warp回镇）"},
              {"tile": (3, 18), "target": "BugLand", "kind": "door", "note": "下水道→变异虫穴(15,53)，变异鲤鱼钓点（/warps实测）"}],
    "BugLand": [{"tile": None, "target": "Sewer", "kind": "warp", "note": "变异虫穴→下水道"}],
    "BathHouse_Entry": [{"tile": (5, 9), "target": "Railroad", "kind": "warp", "arrive": (10, 57), "note": "浴场→铁路(10,57)：⚠️真出口 (5,10) 在**图外**（大厅图只有 10×10, y0~9）→ 站图内最后一格 (5,9) 再 /warp 模拟（2026-09-10 实测：旧写法整段导航失败）"},
                        {"tile": (2, 3), "target": "BathHouse_WomensLocker", "kind": "door", "gender": "female", "note": "♀ 女更衣室门：大厅(2,4)面0 interact(2,3)→WomensLocker(13,27)（2026-09-10 实测换图）"},
                        {"tile": (7, 3), "target": "BathHouse_MensLocker", "kind": "door", "gender": "male", "note": "♂ 男更衣室门：大厅(7,4)面0 interact(7,3)→MensLocker(15,27)（2026-09-10 恒带路）"}],
    # ♨️ 更衣室出口全是**图外格**（图18×28, y只到27，真出口在 y=28）——
    #    walk_to 的入口校验遇越界直接 ok:false ⇒ 旧写法根本走不出去（2026-09-10 抓到的病根）。
    #    改 LeoTreeHouse/Tunnel 同款：**tile 标图内最后一格**（站得住的实格）+ arrive 标落地格 → /warp 模拟。
    #    ⚠️ 恒 2026-09-10 拍板：**别物理蹭地图边缘**（会把游戏搞不稳），一律记坐标走关系网模拟 warp。
    # ⚠️ **浴场两条链都挂了 `via` 途经点**（恒 2026-09-10「只要能保证换衣服」）：
    #    进门落点 (13,27)/(3,27) 和 warp 格 (2,27)/(15,27) **同在 y=27 一条线上**，
    #    顺线走根本不经过换装格 ⇒ AI 会穿着便装直接跳进泳池、泡完又穿着泳装走回村里。
    #    `via` 会被 nagi_mcp_server 的 warp 分支**先精确踩一遍**（`/move` 兜底，见那里的注释）。
    #    穿/脱是相邻两格：去泳池踩「穿」那格，回大厅踩「脱」那格，方向天然分先后（路过会连着触发、净效果对）。
    "BathHouse_WomensLocker": [{"tile": (13, 27), "target": "BathHouse_Entry", "kind": "warp", "arrive": (2, 4), "via": [(2, 16)], "note": "女更衣室→大厅(2,4)：真出口(13,28)图外 → 站图内最后一格(13,27) 再 /warp；途经(2,16)脱泳装"},
                        {"tile": (2, 27), "target": "BathHouse_Pool", "kind": "warp", "arrive": (6, 0), "via": [(2, 17)], "note": "♀ 女更衣室→泳池(6,0)：真出口(2,28)图外 → 站(2,27) 再 /warp；途经(2,17)穿泳装"}],
    "BathHouse_MensLocker": [{"tile": (3, 27), "target": "BathHouse_Entry", "kind": "warp", "arrive": (7, 4), "via": [(15, 18)], "note": "男更衣室→大厅(7,4)：真出口(3,28)图外 → 站(3,27) 再 /warp；途经(15,18)脱泳装"},
                        {"tile": (15, 27), "target": "BathHouse_Pool", "kind": "warp", "arrive": (21, 0), "via": [(15, 19)], "note": "♂ 男更衣室→泳池(21,0)：真出口(15,28)图外 → 站(15,27) 再 /warp；途经(15,19)穿泳装"}],
    # ⚠️ 回程这两条**也要标 gender**（2026-09-10 恒抓的"勇闯女更衣室"）：只标了前门 (Entry→更衣室) 没标后门，
    #    BFS 按列表顺序挑了女门 ⇒ 男角色从泳池出来被塞进女更衣室。出口/入口**两边都要标**。
    "BathHouse_Pool": [{"tile": (6, -1), "target": "BathHouse_WomensLocker", "kind": "warp", "gender": "female", "note": "泳池→女更衣室(2,27)"},
                        {"tile": (21, -1), "target": "BathHouse_MensLocker", "kind": "warp", "gender": "male", "note": "泳池→男更衣室(15,27)"}],
    "Tunnel": [{"tile": (39, 9), "target": "Backwoods", "kind": "warp", "arrive": (23, 30), "note": "隧道出口站格(39,9)→/warp 深山(23,30)；原出口(40,9) x=40 在宽度40边界外；齐先生电池箱 TunnelSafe(17,6) 在里头"},{"tile": None, "target": "Backwoods", "kind": "warp", "note": "隧道→深山(兜底)"}],
    "Tent": [{"tile": None, "target": "Mountain", "kind": "warp", "note": "帐篷→山"}],
    "LeoTreeHouse": [{"tile": (3, 8), "target": "Mountain", "kind": "warp", "arrive": (16, 8), "note": "🌳 雷欧树屋出口→山(16,8)：树屋仅7x9(0..6,0..8)，/warps报(3,9)越界，站(3,8)边格warp（2026-09-06实测；门单向，进=interact<16,7> 出=warp站边格）"}],
    # 🏝️ 出口格 (14,18) —— 2026-09-19 由 `/warps` 补（原来 tile=None ⇒ 走出去只能靠兜底 warp 硬瞬移）：
    #    `IslandFarmHouse(14,18) → IslandWest(77,40)`，与入口 `IslandWest(77,39) → IslandFarmHouse(14,17)`
    #    互为反演（出去落在门口站格 (77,40)、进来落在屋内 (14,17)），两边互相印证。
    "IslandFarmHouse": [{"tile": (14, 18), "target": "IslandWest", "kind": "warp", "note": "姜岛小屋门口→姜岛农场(77,40)（/warps实测）"}],
    "QiNutRoom": [{"tile": None, "target": "IslandWest", "kind": "warp", "note": "核桃房门口→姜岛农场"}],
    "IslandFieldOffice": [{"tile": None, "target": "IslandNorth", "kind": "warp", "note": "办事处门口→火山入口区（2026-08-15补）"}],
    "IslandHut": [{"tile": None, "target": "IslandEast", "kind": "warp", "note": "雷欧小屋门口→丛林（2026-08-15补）"}],
    "IslandNorthCave1": [{"tile": None, "target": "IslandNorth", "kind": "warp", "note": "蘑菇洞口→火山入口区（2026-08-15补）"}],
    "VolcanoDungeon0": [
        {"tile": None, "target": "IslandNorth", "kind": "warp", "note": "火山矿井口→火山入口区（2026-08-15补）"},
        # 🌋 入口层→山顶（捷径，2026-09-12 恒实地走通一次 + 反编译定位）
        #    反编译 `VolcanoDungeon.cs:833`：`warps.Add(new Warp(44, 48, "Caldera", 11, 36))` ⇒ **站格 (44,48)**。
        #    ⚠️ 别被紧邻的第 832 行 `CreateExit(new Point(44, 50))` 骗了 —— `CreateExit`（实现见同文件 971 行）
        #       **只画楼梯贴图、不加 warp**（通篇只有 removeTile/SetTile）。楼梯可视范围 x43~45 × y46~50，
        #       warp 落在楼梯中段 (44,48)。旧值写 (44,50) = 把"楼梯起点"当成了传送瓦片。
        #    ⚠️ 也别说"没 flag 出口就不存在" —— 832/833 两行都在 817~831 那个 if **外面**，**无条件生成**。
        #       那个 if 只管**把矮人门推开**。flag 锁的是**能不能走到**，不是出口有没有：
        #       门 = `DwarfGate` 建在 (40,48)（第 810 行 CreateDwarfGate），机关 = (40,51)（第 809 行
        #       AddPossibleSwitchLocation）⇒ 恒实测「站机关上、前面是门」= 机关 (40,51) + 门 (40,48) 正好对上。
        #    恒实测从山顶出来落在 (44,51) = 楼梯最下一格，往北 3 格即 (44,48) 传送。
        #    ✅ 2026-09-12 真机走通（`map ops=go VolcanoDungeon0→Caldera`）：一条 1 段导航，落 Caldera (11,36)。
        #    入口层地形（/passable_rect 实测，一张图说明白"门"和"楼梯"是两处）：
        #      x=40 一条竖走廊 y44~50 ← **矮人门 (40,48) 就卡在这条走廊上**（现显示可走=门已开）
        #      x=44 一条竖梯 y48~50 ← **(44,48) 是传送格**；上下两头都在底部房间 (x39~45,y50~53) 会合
        #      ⇒ 没解锁时走不到楼梯：门把 x=40 那条走廊掐断，绕不过去（x=41~43 全是墙）。
        #    ⚠️ **进没进过的地牢层：`/warp` 行、`/warp_into` 不行** —— 见 CHANGELOG 09-12⑩⑦。
        {"tile": (44, 48), "target": "Caldera", "kind": "warp", "arrive": (11, 36),
         "note": "🌋 入口层→山顶（**捷径**）。真 warp 瓦片=(44,48)（反编译 833 行）；arrive=(11,36) ✅ **2026-09-12 真机实测**（不是定义值猜的了）。⚠️ 前置=mail flag `volcanoShortcutUnlocked`（用 `map ops=unlocks` 查）：首次到过 Caldera 次日自动补发（Caldera.cs:106），或在入口层矮人门机关 (40,51) 踩下当场发（DwarfGate.cs:121）。**它锁的是 (40,48) 那道矮人门，不是出口本身** —— 没解锁时人会被门挡在楼梯外"},
    ],
    # 🎓 精通山洞出口（2026-09-16 写实）：出口是**地图级 `Warp` 属性** `7 12 Forest 101 73`
    #    ——不是瓦片属性，所以 `/tile_props?scan=Warp` 扫不到（扫出来 count=0），
    #    但 `/warps` 的 `EnsureWarpGraph` 读得到（已实测 `{x:7,y:12}→Forest(101,73)`）。
    #    原来写 `tile:None` 靠 live /warps 兜，现按实测钉死；arrive 也钉上游戏真实落点。
    "MasteryCave": [{"tile": (7, 12), "target": "Forest", "kind": "warp", "arrive": (101, 73),
                     "note": "精通山洞→森林(101,73)：踩 (7,12) 自动出（地图级 Warp 属性，2026-09-16 实测）"}],
    "Summit": [{"tile": None, "target": "Railroad", "kind": "warp", "note": "山顶下山→铁路（2026-08-15补）"}],
    "WitchWarpCave": [{"tile": (4, 9), "target": "Railroad", "kind": "warp", "arrive": (54, 34), "note": "魔女沼泽洞穴→铁路(54,34)；原出口(4,10) y=10 在高度10边界外（/warps实测，2026-08-30）"},
                      {"tile": (4, 5), "target": "WitchSwamp", "kind": "portal", "stand": [4, 6], "note": "🔮 传送阵(准确坐标(4,5)，2026-08-30 恒领跑实测)：站(4,6)面0 interact(面前=(4,5))→女巫沼泽(20,42)。map_go 先walk到(4,6)站位再warp跨。需黑暗护身符(HasDarkTalisman)"}],
    # 🧙 女巫沼泽/女巫小屋（2026-08-30 恒：黑暗护身符洞穴内传送阵→女巫区；LIVE /warps 实测）
    "WitchSwamp": [{"tile": (20, 49), "target": "Railroad", "kind": "warp", "arrive": (54, 34), "note": "女巫沼泽→铁路(54,34)；原出口(20,50) y=50 在高度50边界外（/warps实测，2026-08-30）"},
                   {"tile": (20, 20), "target": "WitchHut", "kind": "door", "note": "女巫沼泽站(20,21)面0 interact→WitchHut(7,16)，**交互开门非warp**（2026-08-30 AI实测）"},
                   {"tile": (20, 42), "target": "WitchWarpCave", "kind": "portal", "stand": [20, 42], "arrive": [4, 5], "note": "🔮 可逆传送阵(2026-08-30 恒领跑)：站(20,42)即沼泽入口/洞穴传送阵落点→回魔女洞穴(**walk到(20,42)站位再warp落(4,5)**)；再铁路。**回铁路=踩这→洞穴→(4,10)warp→铁路**"}],
    "WitchHut": [{"tile": (7, 16), "target": "WitchSwamp", "kind": "portal", "stand": [7, 15], "arrive": [20, 21], "note": "🧙 女巫小屋→女巫沼泽**模拟出口warp**(2026-08-30 恒)：离开小屋→walk到(7,15)站位再warp落地沼泽门口(20,21)。⚠️真瓦片(7,16)/interact触发不了，靠模拟warp"},
                 {"tile": (11, 11), "target": "WizardHouseBasement", "kind": "portal", "stand": [10, 11], "note": "🔮 传送阵(单向，面前格(11,11) 2026-08-30 恒确认)：女巫小屋(三大神龛)→法师塔地下室WizardHouseBasement(2,5)。**站(10,11)面右(1)面前(11,11) = 传送阵**；女巫→法师塔单向，不可回小屋；map_go 先walk到(10,11)再warp落(2,5)。需黑暗护身符"}],
    # 🎇 夜市内部（2026-08-16 恒：节日限定冬15-17，双向出入；只在夜市加载）
    "BeachNightMarket": [
        {"tile": (58, 32), "target": "MermaidHouse", "kind": "door", "note": "美人鱼船门→MermaidHouse（看秀点贝壳1-5-4-2-3拿珍珠）"},
        {"tile": (5, 35), "target": "Submarine", "kind": "door", "note": "钓鱼潜艇门→Submarine（艇长1000g深海钓）"},
        {"tile": (38, 0), "target": "Town", "kind": "warp", "arrive": (54, 108), "note": "夜市上口站格(38,0)→/warp 镇(54,108)；原出口(38,-1) y=-1 边界外"},
    ],
    "MermaidHouse": [
        {"tile": (4, 11), "target": "BeachNightMarket", "kind": "warp", "note": "美人鱼船出口→夜市(58,32)"},
    ],
    "Submarine": [
        {"tile": (14, 16), "target": "BeachNightMarket", "kind": "warp", "note": "潜艇出口→夜市(5,35)"},
    ],
}


# ── 🌰 姜岛金核桃升级表（2026-08-15 恒提供，鹦鹉特快/图腾/桥等全解锁条件）──
# 喂金核桃给岛上鹦鹉解锁；建个传送塔=姜岛→农场图腾柱（需先修睡觉小屋+邮箱）。
# map_query("金核桃"/"图腾"/"鹦鹉") 能搜到。⚠️ 具体 parrotUpgradesDone 索引待实测。
ISLAND_UPGRADES = [
    {"name": "姜岛北部",      "desc": "解锁通往姜岛北部的通道（火山区）",      "where": "雷欧的房子",      "cost": 1},
    {"name": "叫醒乌龟",      "desc": "解锁通往姜岛西部（农场）的通道",        "where": "姜岛南部",        "cost": 10},
    {"name": "修好睡觉小屋",  "desc": "解锁姜岛农场房屋，可在岛上睡觉（第二个家）", "where": "姜岛农场小屋", "cost": 20},
    {"name": "传递信件",      "desc": "可以在姜岛查看信件",                    "where": "姜岛农场",        "cost": 5},
    {"name": "建个传送塔",    "desc": "建农场图腾柱传送回农场（⚠️需先修好睡觉小屋+邮箱）", "where": "姜岛农场", "cost": 20},
    {"name": "修好桥",        "desc": "修复去挖掘场的桥，间接解锁岛屿办事处",   "where": "姜岛北部",        "cost": 10},
    {"name": "建造贸易小屋",  "desc": "解锁姜岛商人的商店",                    "where": "姜岛北部",        "cost": 10},
    {"name": "建座桥",        "desc": "解锁通往火山内部的永久桥（不用浇水）",  "where": "火山地牢入口",    "cost": 5},
    {"name": "开条捷径",      "desc": "火山地牢第5层挖出口到姜岛北部（单向）", "where": "火山地牢第5层",   "cost": 5},
    {"name": "建个度假村",    "desc": "港口附近建度假村，村民可能过来",         "where": "姜岛南部",        "cost": 20},
    {"name": "鹦鹉特快",      "desc": "开启岛上传送系统（类似矿车）",          "where": "姜岛",            "cost": "10 + 2齐钻"},
    {"name": "齐钻兑换",      "desc": "建设后剩余金核桃换齐钻（仅其他全买后）", "where": "齐先生的核桃房",  "cost": 1},
]


# ── 🐟 钓鱼知识（2026-08-15 恒：钓鱼域加"这里能钓什么鱼"，纯远程 AI 也要知道）──
# 地点 → {水: 水域类型, fish: [{name, season(季节), weather(特殊天气)}]}
# season 取值: 春/夏/秋/冬/全季；weather 为空=任意天气，否则注明（雨天/夜晚等）
FISH_KNOWLEDGE = {
    "Beach": {"水": "🌊海洋", "fish": [
        {"name": "沙丁鱼/凤尾鱼/鲱鱼", "season": "春/夏/秋", "weather": ""},
        {"name": "金枪鱼/红鲷鱼", "season": "夏", "weather": ""},
        {"name": "章鱼", "season": "夏", "weather": "夜晚(6pm后)"},
        {"name": "比目鱼/海参", "season": "春/夏", "weather": ""},
        {"name": "大比目鱼", "season": "春/夏/冬", "weather": ""},
    ]},
    "Mountain": {"水": "🏞️湖泊", "fish": [
        {"name": "大口黑鲈/鲤鱼/大头鱼", "season": "全季", "weather": ""},
        {"name": "鲟鱼", "season": "夏/冬", "weather": ""},
        {"name": "虹鳟鱼", "season": "夏", "weather": "山间湖泊"},
        {"name": "传奇鱼(传说鱼)", "season": "春", "weather": "雨天"},
    ]},
    "Forest": {"水": "🏞️河流", "fish": [
        {"name": "鲦鱼/鲤鱼/鲈鱼", "season": "全季", "weather": ""},
        {"name": "鲶鱼", "season": "全季", "weather": "雨天"},
        {"name": "大嘴鲈鱼", "season": "全季", "weather": ""},
        {"name": "鲑鱼", "season": "秋", "weather": ""},
    ]},
    "Town": {"水": "🏞️河流", "fish": [
        {"name": "鲦鱼/鲤鱼/鲈鱼", "season": "全季", "weather": ""},
        {"name": "鲶鱼", "season": "全季", "weather": "雨天"},
        {"name": "太阳鱼", "season": "春/夏", "weather": "晴朗白天"},
    ]},
    "Sewer": {"水": "🏚️下水道", "fish": [
        {"name": "变异鲤鱼(传说鱼)", "season": "全季", "weather": ""},
    ]},
    "Woods": {"水": "🌳秘密森林", "fish": [
        {"name": "木跃鱼", "season": "全季", "weather": ""},
    ]},
    "Desert": {"水": "🏜️沙漠", "fish": [
        {"name": "沙鱼", "season": "全季", "weather": ""},
        {"name": "蝎子鲤", "season": "全季", "weather": ""},
    ]},
    "BugLand": {"水": "🦠变异虫穴", "fish": [
        {"name": "变异鲤鱼(传说鱼)", "season": "全季", "weather": ""},
    ]},
}


# ── 🕐 商店营业时间（2026-08-15 恒：map 提示各店上班时间；新档买种子/升级要知道几点开门）──
SHOP_HOURS = {
    "SeedShop": "9:00-21:00（周三休）",
    "Hospital": "9:00-15:00",
    "Saloon": "12:00-24:00",
    "Blacksmith": "9:00-16:00",
    "ArchaeologyHouse": "9:00-18:00（周一休）",   # ⚠️待核：门那格原生 `LockedDoorWarp 3 14 ArchaeologyHouse 800 1800` ⇒ 游戏里是 **8:00-18:00**（恒 2026-10-05 确认）；这张表还没改（补24 起记为待核）
    "JojaMart": "9:00-23:00",                    # 🏬 门那格原生 `LockedDoorWarp 13 29 JojaMart 900 2300`（2026-10-05 真机读）
    "FishShop": "9:00-17:00",
    "AnimalShop": "9:00-16:00",
    "SandyHouse": "9:00-23:00",
    "ScienceHouse": "9:00-17:00",
    "AdventureGuild": "14:00-24:00（需先杀怪解锁）",
}


# 🏠 室内图清单（2026-10-04 恒：「**所有室内的 poi（柜台等）都列出来给 ai 的，室外就算了**」）
#    用途：`🗺️ 可:` 那一行——**在室内图**改成"把这张图 `POI` 表里的条目全列出来（带坐标 + 以交互）"，
#    室外图照旧用 `MAP_FEATURES`（街上兴趣点太多太碎，全列等于刷屏）。
#    ⚠️ **显式清单**（宁缺勿滥）：不在表里的图 = 行为一字不变；发现哪张室内图漏了就往这儿加。
INDOOR_MAPS = {
    # 商店/公共建筑
    "SeedShop", "Hospital", "Saloon", "Blacksmith", "ArchaeologyHouse", "ScienceHouse",
    "AnimalShop", "FishShop", "SandyHouse", "AdventureGuild", "MovieTheater",
    "JojaMart", "AbandonedJojaMart",
    "CommunityCenter", "ManorHouse", "WizardHouse", "WizardHouseBasement", "Club",
    "Trailer", "Tent", "IslandFieldOffice", "IslandHut", "IslandFarmHouse",
    "VolcanoDungeon5", "FarmCave", "Cellar", "Greenhouse", "Shed",
    "BathHouse_Entry", "BathHouse_MensLocker", "BathHouse_WomensLocker", "BathHouse_Pool",
    # 农场动物建筑室内（名字 = `/state` 报的室内名）
    "Barn", "Big Barn", "Deluxe Barn", "Coop", "Big Coop", "Deluxe Coop",
}


# ── 每地点交互功能（map_lookup 用：AI 想知道"这能干嘛"） ──
MAP_FEATURES = {
    "Farm": ["出货箱(卖东西隔夜到账)", "农场电脑(作物/机器总览)", "爷爷神龛(放钻石评估)", "宠物水碗", "温室(四季可种)", "农场洞穴(蘑菇/果蝠)", "信箱(收邮件)"],
    "FarmHouse": ["床(睡觉/重生点)", "电视(天气/运势/食谱)", "厨房(做饭)", "壁炉(取暖)"],
    "Cabin": ["床(睡觉)", "小屋木箱", "壁炉"],
    "BusStop": ["巴士售票处(买票去沙漠)", "矿车(交通)"],
    "Backwoods": ["深山(连接巴士站/山/农场)", "隧道口(齐先生电池箱任务)"],
    "Town": ["皮埃尔商店(种子/肥料)", "哈维医院(看病/买药)", "星之果实餐吧(买沙拉/啤酒/接任务)", "铁匠铺(升级工具/买矿/开晶球)", "博物馆(捐矿物/古物/借书)", "社区中心(献祭)", "镇长家", "电影院", "墓地", "下水道(需钥匙)", "河流钓点"],
    "SeedShop": ["柜台买种子/肥料/墙纸/树苗/花束", "收银台卖东西"],
    "Hospital": ["哈维柜台(看病/买药/体检)", "诊所"],
    "Saloon": ["格斯柜台(买食物/啤酒)", "点唱机", "台球", "接「给谁送餐」任务"],
    "Blacksmith": ["克林特柜台(升级工具/买矿石煤锭/开晶球)", "炉子"],
    "ArchaeologyHouse": ["柜台捐矿物/古物", "书摊(买书)"],
    "CommunityCenter": ["祝尼魔献祭面板"],
    "ManorHouse": ["镇长刘易斯"],
    "JoshHouse": ["艾芙琳/乔治/亚历克斯"],
    "MovieTheater": ["看电影(约会/涨好感)", "影院小吃(爆米花)"],
    "Sewer": ["科罗布斯商店(买铱环/电池/虚空蛋)", "变异鲤鱼钓点"],
    "Mountain": ["矿井(下矿)", "探险家公会(买武器/怪物任务)", "罗宾木匠店(买建筑/家具/升级房子)", "山湖钓点", "温泉", "莱纳斯帐篷", "采石场(修桥后)"],
    "Mine": ["矿洞(逐层下/挖矿)", "电梯层", "采集矿石"],
    "AdventureGuild": ["马龙(买武器/接怪物任务)", "吉尔(讨伐奖励)"],
    "ScienceHouse": ["罗宾柜台(买建筑/家具/升级)", "地下室塞巴斯蒂安"],
    "Forest": ["玛妮牧场(买动物/饲料)", "巫师塔(祝尼魔/改宠物)", "秘密森林(硬木/木跃鱼)", "猪车(周五周日)", "精通山洞", "河边钓点"],
    "MasteryCave": ["🎓 五块精通碑(战斗/采集/耕种/钓鱼/采矿，各领一次)", "中央基座(看精通等级总进度)", "爷爷的信(地上信纸)"],
    "AnimalShop": ["玛妮柜台(买鸡鸭牛羊/饲料/加热器/挤奶器)"],
    "WizardHouse": ["法师(祝尼魔任务/改宠物/买魔力项链)", "魔法书"],
    "Woods": ["硬木桩×6", "木跃鱼钓点", "老大师香炸奶酪卷(放甜宝石莓换星之果实)"],
    "Beach": ["威利鱼店(买鱼竿/鱼饵/蟹笼)", "码头/潮池钓点", "断桥(300木修复→右侧沙滩)", "艾利欧特小屋", "姜岛船(鱼店后门)"],
    "FishShop": ["威利柜台(买鱼竿/鱼饵/蟹笼/鱼)", "姜岛船票(1000g→姜岛)"],
    "Railroad": ["浴场(泡澡)", "魔女沼泽口", "山顶(完美达成后)", "铁轨(等火车捡掉落)"],
    "BathHouse_Entry": ["更衣室→温泉池(泡澡回体力)"],
    "Desert": ["头骨矿洞(下100层)", "桑迪绿洲(买杨桃种子/饰品)", "沙漠商人(换物)", "沙鱼钓点"],
    "SkullCave": ["头骨矿洞(逐层下/挖铱)", "宝箱层"],
    "SandyHouse": ["桑迪柜台(买杨桃种子/向日葵/饰品)", "赌场入口(需会员卡)"],
    "IslandWest": ["姜岛农场(四季可种)", "齐钻核桃房(接齐钻任务/兑换)", "鹦鹉特快(金核桃解锁)", "姜岛商人", "火山口"],
    "IslandSouth": ["码头(回姜岛船)", "海滩钓点", "西→姜岛农场", "东→丛林/度假村"],
    "IslandEast": ["雷欧小屋", "丛林钓点", "度假村", "鹦鹉特快"],
    "MermaidHouse": ["🧜 美人鱼秀：**等表演结束(约2-3分钟真实时间)再点贝壳** 1-5-4-2-3 = (2,6)(6,6)(5,6)(3,6)(4,6) 拿**珍珠**(每存档1颗,2.5k金)；表演中可截图等待,⚠️没演完按=白按"],
    "Submarine": ["🛸 深海钓**无时限**(随便钓午夜鱿鱼/幽灵鱼/水滴鱼/珍珠)；**返回水面要和艇长再对话**(等~30分钟游戏时间上浮)；⚠️**留好回家时间**(建议用裹布人传回农场,别钓到昏倒)；⚠️**别在潜艇下潜/上浮中途进出**(恒实测会卡脚!),等门开/停稳再进出"],
    "Temp": ["🎪 节日进行中：⏸️**时间静止**(游戏时钟不动、不自动送回家)；碰地图边缘不触发结束。⚠️ **导航认准 festival go**——map_go 会误报\"到X失败\"(节日事件拉进Temp独立图)，实际人已在场地，看 📍 Temp+🎪 即可；**退出/卡住/要结束→联系 user 帮忙**(MCP 端 warp 已禁用，AI 自己出不去)"],
    "IslandNorth": ["火山(挖矿/锻造台)", "办事处(捐化石)", "挖掘场", "姜岛商人"],
    "Caldera": ["锻造台(附魔/合成戒指/龙牙附魔)"],
    "QiNutRoom": ["齐先生任务板/兑换店"],
}


# 🚧 `MAP_FEATURES` 条目的**可用性门槛**（2026-09-23 恒：春2日 AI 跑到 Mountain，
#    状态条「🗺️ 可:」就告诉它能去木匠商店、探险家公会和矿井 —— 可矿井第 5 天才开、
#    公会的门禁也没做过）。**只收录"有权威判据"的条目，拿不准的一律不写**（宁可不藏，不误藏）。
#
# 键 = 地点（同 MAP_FEATURES），值 = {条目去掉括号后的前缀: 依赖}
#   条目前缀必须**逐字等于** `MAP_FEATURES` 里那一项 `split("(")[0]`（渲染处就是这么切的）。
# 依赖两种写法：
#   `map:<地图名>`  → 该地图未解锁就藏。判据走 `navigation._locked_maps()`（= /unlocks，
#                     游戏自己的 mail flag/条件；地图名必须与 `navigation.LOCKED_MAPS` 的键一致）。
#   `door:<建筑名>` → "推门被游戏挡回来"的记忆（`navigation.door_blocked()`，当天有效、换天清）。
#                     用在哪：**游戏侧没有可读 flag 的门**（探险家公会的锁是 `guildMember`
#                     或 quest 16，Python 这边读不到当前角色的那两项）。
# 🛤️ **铁路石堆**：`Mountain.railroadAreaBlocked` / `railroadBlockRect` —— **夏3日地震后清**
#    （出处：`ModEntry.cs` 的 `RuntimeBlockers` 表，反射读游戏自己的私有字段，不抄坐标）。
#    ⚠️ 石堆堵的是 **Mountain 上去铁路那条路** ⇒ 在它清掉之前 **温泉（BathHouse）根本去不了**。
#    恒 2026-09-25：「railroad 我记得有门禁，特定天数之后才解锁……**在那个日期之前都不要误导 AI 去泡温泉**」
#    —— 他记得对，具体日期是**年1 夏3日**（地震事件当天清）。
RAILROAD_OPEN_SEASON, RAILROAD_OPEN_DAY = "summer", 3


def railroad_open(season="", day=0, year=1) -> bool:
    """🛤️ 铁路通了吗（= **温泉能不能去**）。判据＝**年1 夏3日之后**。

    ⚠️ 读不到日期 → 返回 False（**当没通**）：这一侧的失败只是"少提一条恢复体力的路"，
       而反过来（错说"能去"）会让 AI 白跑半个地图 —— 两害相权，宁少提。
    """
    try:
        if int(year) > 1:
            return True                      # 1 年过后早清了（地震是一次性事件）
        se = str(season or "").lower()
        if se in ("fall", "winter"):
            return True
        if se == "summer":
            return int(day) >= RAILROAD_OPEN_DAY
        return False                         # spring（和夏3日之前）
    except Exception:
        return False


MAP_FEATURE_GATES = {
    "Farm":       {"温室": "map:Greenhouse"},
    "BusStop":    {"巴士售票处": "map:Desert"},      # 巴士没修好 = Desert 未解锁
    "Town":       {"电影院": "map:MovieTheater", "下水道": "map:Sewer"},
    "Forest":     {"秘密森林": "map:Woods", "精通山洞": "map:MasteryCave"},
    # ⚠️ 探险家公会：**两条**门禁（值是 list ⇒ 任一条不满足就藏）。
    #    `map:Mine` 挡的是**山体塌方**（与矿井同一天解除：`landslide = DaysPlayed<5`，
    #    见 navigation `_MOUNTAIN_BEHIND_LANDSLIDE` 里的连通域实测）——塌方在的时候公会**物理上到不了**；
    #    `door:` 挡的是"到了门口但门锁着"（杀 10 只绿史莱姆 / guildMember）。
    "Mountain":   {"矿井": "map:Mine",
                   "探险家公会": ["map:Mine", "door:AdventureGuild"]},
    "Beach":      {"姜岛船": "map:IslandSouth"},
    "FishShop":   {"姜岛船票": "map:IslandSouth"},
    "Desert":     {"头骨矿洞": "map:SkullCave"},
    "Railroad":   {"山顶": "map:Summit", "魔女沼泽口": "map:WitchSwamp"},
    "SandyHouse": {"赌场入口": "map:Club"},
}


# ── 🚂 矿车关系网（2026-08-15 恒实测：社区中心献祭解锁）──
# 每站：map=矿车所在图, drop=下车点, interact=(站位,朝向), menu={选项:目的地}(不能选自己站)
# 城镇/采石场 到达=交互点(朝上)；矿井 下车点与交互点差1格(交互站13,10朝左)；巴士站 站(14,4)朝上
MINE_CART_STATIONS = {
    "巴士站": {"map": "BusStop", "drop": (14, 4), "interact": ((14, 4), 0), "menu": {0: "矿井", 1: "城镇", 2: "采石场"}},
    "矿井":   {"map": "Mine",    "drop": (13, 9), "interact": ((13, 10), 3), "menu": {0: "城镇", 1: "巴士站", 2: "采石场"}},
    "城镇":   {"map": "Town",    "drop": (105, 80), "interact": ((105, 80), 0), "menu": {0: "矿井", 1: "巴士站", 2: "采石场"}},
    "采石场": {"map": "Mountain","drop": (124, 12), "interact": ((124, 12), 0), "menu": {0: "矿井", 1: "城镇", 2: "巴士站"}},
}


# ── 建筑门口坐标（map_go 进门用：建筑地点 → (室外地图, 门口瓦片)）──
# 农场建筑（FarmHouse/Cabin/畜棚/鸡舍/温室等）动态用 /farm_buildings，不在这张表
BUILDING_DOORS = {
    # ⚠️⚠️ 2026-09-17 大修正：下面这批原本填的是**出口落点**（= 门格 + 1 行），系统性差 1。
    #    权威源 = 游戏自己的 Action 瓦片：`/tile_props?scan=Action&location=<图>`
    #    （门 = `LockedDoorWarp <内x> <内y> <建筑> <开门h> <关门h> [好感门槛]` 那一格）。
    #    改前它也能跑，但**靠兜底**：`_enter_building_door` walk 到落点 → `interact_at` 打空地（没反应）
    #    → 才走最后那条 `face(0)` + `/interact`（打**面前格**）蒙中真门。主路径每次空转一轮。
    "SeedShop":        ("Town",     (43, 56)),   # 也在 (44,56)（双子门）
    "Hospital":        ("Town",     (36, 55)),
    "Saloon":          ("Town",     (45, 70)),   # 门格 (45,70)；原注写的"交互45,70"才是对的，存的值(45,71)是站位
    "Blacksmith":      ("Town",     (94, 81)),
    "CommunityCenter": ("Town",     (53, 19)),   # 也在 (52,19)
    "JoshHouse":       ("Town",     (57, 63)),
    # ⚠️⚠️ 2026-09-17 发现：**本表绝大多数条目填的是「出口落点」而不是「门格」，系统性差 1 行**
    #    （落点 = 门格 + 1）。它一直能跑，是因为 `_enter_building_door` 末尾有条
    #    `face(0)` + `/interact`（打**面前格**）的兜底正好打中真门 —— 属于"靠兜底蒙对、主路径没走通"。
    #    权威来源 = 游戏自己的 Action 瓦片：`/tile_props?scan=Action&location=<图>`（`LockedDoorWarp` 那条）。
    #    下面这三条是 2026-09-17 **逐扇真机走验**过的（走过去→interact→真进屋），已改成**真门格**。
    "HaleyHouse":      ("Town",     (20, 88)),   # 🌊 海莉&艾米丽家，门格=出口落点(20,89)上一格（2026-09-17 真机验）
    "SamHouse":        ("Town",     (10, 85)),   # 🏠 乔迪/山姆/文森特/肯特家，门格=落点(10,86)上一格（2026-09-17 真机验）
    "Trailer":         ("Town",     (72, 68)),   # 🚚 潘姆/佩妮拖车，门格=落点(72,69)上一格（2026-09-17 真机验：站(72,69)面0推→Trailer(12,9)）
    "ManorHouse":      ("Town",     (59, 85)),   # 也在 (58,85)，双子门
    "ArchaeologyHouse":("Town",     (101, 89)),
    "MovieTheater":    ("Town",     (96, 50)),   # 前 Joja 超市；JojaMart 门是 (95,50)/(96,50) 同一扇
    "JojaMart":        ("Town",     (95, 50)),   # 🏬 Joja超市（形态=Joja超市；门原生 LockedDoorWarp 13 29 JojaMart 900 2300）
    "AbandonedJojaMart":("Town",    (96, 50)),   # 🏚 废弃超市（同一栋楼的废墟形态；⚠️与 MovieTheater 共用 (96,50)，两形态互斥）
    "Mine":            ("Mountain", (54, 5)),
    "AdventureGuild":  ("Mountain", (76, 8)),
    "ScienceHouse":    ("Mountain", (12, 25)),   # 也在 (8,20)（Maru 侧门）
    "Tent":            ("Mountain", (29, 6)),
    "LeoTreeHouse":    ("Mountain", (16, 8)),
    "FishShop":        ("Beach",    (30, 33)),
    # 🏠 Elliott 家（2026-09-17 新增）：门格来源=游戏 Action 扫描
    #    `Beach(49,10) = LockedDoorWarp 3 9 ElliottHouse 1000 1800 Elliott 500`
    #    ⇒ **10:00–18:00 之外锁、且要 Elliott 好感≥500**。AI 好感不够 ⇒ 推门弹「上锁了……」（真机见）。
    #    ⚠️ 这是**门禁**不是坐标错——`_enter_building_door` 的 `_locked_door_dialogue()` 会把它和"走不到"分开。
    "ElliottHouse":    ("Beach",    (49, 10)),
    "AnimalShop":      ("Forest",   (90, 15)),
    # 🏠 Leah 家（2026-09-17 新增）：`Forest(104,32) = LockedDoorWarp 7 9 LeahHouse 1000 1800 Leah 500`
    #    ——同 Elliott 家，10:00–18:00 + Leah 好感≥500 才开。
    "LeahHouse":       ("Forest",   (104, 32)),
    "WizardHouse":     ("Forest",   (5, 26)),
    "Woods":           ("Forest",   (0, 7)),
    "SandyHouse":      ("Desert",   (6, 51)),
    "Club":            ("SandyHouse", (17, 1)),   # 🎰 进赌场的门口在桑迪店内(17,1)（2026-08-23 恒：AI 实测 /map warp 出口=17,1→Club(8,13)；BUILDING_DOORS 新补，此前缺致 _enter_building_door 拿不到门口坐标进不去）
    "WitchHut":        ("WitchSwamp", (20, 20)),  # 🧙 女巫小屋（2026-08-30 恒+AI 实测：站(20,21)面朝0交互→进 WitchHut(7,16)；交互开门非 warp）
    "WizardHouseBasement": ("WizardHouse", (4, 5)),  # 🪜 法师塔地下室（2026-08-30 恒+AI 实测：站塔内(4,5)面0 interact 爬梯→下地下室；含幻觉神龛/法师传送阵）
    "SkullCave":       ("Desert",   (8, 6)),
    "FarmCave":        ("Farm",     (34, 7)),
    # 🌱 温室（2026-09-16 补）：**此前三处全缺** ⇒ `map go Greenhouse` 推门拿不到门格 →
    #    走兜底 warp 硬进 ⇒ 落到 ARRIVE 的 (1,1) 墙角，恒真机看到"**进温室飞到墙外**"。
    #    门格来源（问游戏，不猜）：`/farm_buildings` 报 Greenhouse doorX=28,doorY=15；
    #    且温室自己的出口 warp 是 `Greenhouse(10,24) → Farm(28,16)` —— 出口落点就是门口，
    #    两边**互相印证**，所以门格取 (28,15)、站门下方 (28,16) 面朝上推。
    "Greenhouse":      ("Farm",     (28, 15)),
    # 🎓 精通山洞（2026-09-16 补）：入口是 Forest (101,71)/(101,72) 的 `Action:MasteryRoom` 格
    #    （反编译 GameLocation.cs:8780：五技能全 10 级 → `warpFarmer("MasteryCave",7,11,0)`，不够就弹提示）。
    #    **不是推门、也不是踩上去就传** —— 要 `checkAction` 才触发。_enter_building_door 的语义正好是
    #    「walk 到 (dx,dy) → `interact_at(dx,dy)`」，而 `/interact {x,y}` 是直接对目标格 checkAction、
    #    **不依赖面朝**，所以这里给**目标 Action 格 (101,72)** 而不是站位 (101,73)。
    #    此前这张表没有 MasteryCave ⇒ 推门拿不到门格 ⇒ 退回 ARRIVE 硬瞬移进屋（日志「⚠️推门没成」）。
    "MasteryCave":     ("Forest",   (101, 72)),
    "Sewer":           ("Town",     (35, 97)),
    "BathHouse_Entry": ("Railroad", (10, 56)),
    # ♨️ 更衣室两扇**性别门禁门**（2026-09-10 补）：同图隔间门，非 warp。不进这张表的话
    #    _enter_building_door 拿不到门格 → 直接放弃 → 走兜底 warp 瞬移进屋（真机日志「⚠️推门没成」抓到的）。
    #    门格：女 (2,3) / 男 (7,3)，都是站大厅 y=4 面朝上(0) 推。性别不符→checkAction 弹 DialogueBox → 报"门锁着"不硬闯。
    "BathHouse_WomensLocker": ("BathHouse_Entry", (2, 3)),
    "BathHouse_MensLocker":   ("BathHouse_Entry", (7, 3)),
    # 🏝️ 姜岛小屋（2026-09-19 补，恒真机逮到"没推门就 warp 硬进"）：**此前这张表没有它**
    #    ⇒ `_enter_building_door` 拿不到门格 → 直接 False → 走兜底 warp 硬闯进屋
    #    （真机日志「⚠️推门没成 → 兜底warp 硬进」）。
    #    门格来源=**问游戏**（`/tile_props?scan=Action&location=IslandWest`）：
    #      `IslandWest(77,39) = Warp 14 17 IslandFarmHouse`
    #    ⇒ 它**不是"门"、是个 warp 瓦片**（踩上去就传，不用 interact）——
    #      `_enter_building_door` 走位到门格那一步就已经触发传送，函数里"若走位已触发进门就提前返回"正好接住。
    #    ✅ 第二重印证（恒 2026-09-19 真机配合）：他站在 `IslandWest(77,40)` **面朝上(0)** 正对门
    #      ⇒ (77,40)=**站位格**、(77,39)=**触发格**，与上面扫描一致。
    #    ⚠️ `MAP_LINKS["IslandWest"]` 里记的 (77,40) 是**站位/出口落点**，**不是**触发格——
    #      两张表用途不同（那边=先走到门口一带，这边=最后踩哪一格），别互相抄（09-17 差 1 行就是这么来的）。
    "IslandFarmHouse": ("IslandWest", (77, 39)),
    "Tunnel":          ("Backwoods",(22, 31)),
    "MermaidHouse":    ("BeachNightMarket", (58, 32)),   # 🎇 美人鱼船门（节日限定冬15-17）
    "Submarine":       ("BeachNightMarket", (5, 35)),    # 🎇 钓鱼潜艇门（节日限定冬15-17）
    # 🚢🥥 2026-10-05（补28c）新增两条：**门格此前根本没进表** ⇒ `map go` 走到门口就如实停（"表里查不到 X 的门格"）。
    #    判据都是**只读真机**（`/tile_props`）+ 反编译：
    "BoatTunnel":      ("FishShop",  (4, 3)),    # 🚢 鱼店后屋门：FishShop(4,3) Buildings `Action: WarpBoatTunnel`（真机读）；
                                                 #    反编译 `FishShop.cs:70-76`：需威利后屋邀请 `willyBackRoomInvitation`，进了落 BoatTunnel **(6,12)**
    "QiNutRoom":       ("IslandWest",(20, 22)),  # 🥥 核桃房门：IslandWest(20,22) Buildings **瓦片 1470**（⚠️**没有 Action**，别只按 Action 找门）；
                                                 #    反编译 `IslandWest.cs:327-338`：解锁则 `warpFarmer("QiNutRoom",7,8,0)`；站格=(20,23)（MAP_LINKS/POI 那边）
}


# 🐄 农场动物建筑（畜棚/鸡舍全变体）—— 名字 = `/state` 报的室内名 = `/farm_buildings` 的 indoorsName。
#    2026-09-16 恒：以前有两处都不知道畜棚鸡舍的存在，各栽了一次——
#      ①**导航**：`_interior_to_farm` 只查 MAP_LINKS，而农场建筑室内**压根不是 MAP_LINKS 的节点**
#        （`/warps` 表里连 "Deluxe Barn" 这个图都没有）⇒ 认不出"人在棚里" ⇒ `map go Farm` 一路走到
#        BFS，拿 "Deluxe Barn" 当节点，报「🗺️ 知识库没找到从 Deluxe Barn 到 Farm 的路径（缺地图链接）」。
#        实际上 `_exit_farm_building` 本来就是通用的（自己读 /map 的原生出口 warp），只是没被叫到——
#        等于出棚只剩内部 `_warp_home_if_needed` 的裸 warp，AI 看得见的那条路是断的。
#      ②**farm 域适用区**：棚内调 farm 会吃到「💡 可先 map go Farm」的反建议，而棚内**正是**干农活的
#        地方（饲料槽是畜棚自带的、宠物碗/摸动物都只能在棚里做）。
#    ⚠️ 单一来源：navigation.py 与 nagi_mcp_server.py 都从这里取，别各写一份（列表漂移最难查）。
FARM_ANIMAL_BUILDINGS = ("Coop", "Big Coop", "Deluxe Coop", "Barn", "Big Barn", "Deluxe Barn")

# 🏠 农场工作建筑（机器屋）—— 同一类的第三、第四个受害者（2026-09-16 当场又栽在 Big Shed 上：
#    在屋里给小桶上料，头顶却挂着「💡 当前在Big Shed，farm通常在Farm做；可先 map go Farm」）。
#    小桶/罐头瓶/复制机全在棚屋里，**这里就是 farm 的工作场所**。
FARM_MACHINE_BUILDINGS = ("Big Shed",)
# 限定词在**后面**的（Cellar / Cellar2 … Cellar8，恒 8 个地窖）⇒ 可走 startswith 前缀。
# ⚠️ 地窖只放进"域适用区"（不要再劝它出去），**暂不放进 `_interior_to_farm`**：
#    地窖的原生出口是通到**它上面那栋房子**（FarmHouse/Cabin/Big Shed），不是 Farm
#    ⇒ `_exit_farm_building(cellar, "Farm")` 找不到通往 Farm 的 warp 会直接失败。
#    要修得出"地窖→楼→Farm"两跳，单独立项，别顺手塞进来把出门路搅断。
FARM_MACHINE_PREFIXES = ("Cellar",)

# 导航用：人在这些屋里时，先走出到 Farm（`_exit_farm_building` 通用的，读 /map 原生出口 warp）
FARM_INTERIOR_BUILDINGS = FARM_ANIMAL_BUILDINGS + FARM_MACHINE_BUILDINGS


# ── 每地点到达入口（map_go 传送到这继续走，2026-08-13 恒：只传标注过的点）──
# 校准安全落点（POI/ROUTES 提取）。⚠️ Farm 是河流农场，入口待实测（普通农场坐标会传进河）。
ARRIVE = {
    "Farm": (40, 32),           # ⚠️ 河流农场待实测校准
    "FarmHouse": (10, 6),
    "Cabin": (3, 12),
    "FarmCave": (8, 11),
    # 🌱 温室入口 **(10,23)**（2026-10-05 补30 真机落点读数改正）。
    #    ⚠️ 旧值 (10,24) **其实是出口格**：温室自己的出口 warp 就是 `Greenhouse(10,24) → Farm(28,16)`
    #    （`/warps` 对得上）—— 旧注释把"出口"当成了"落点"。真机（恒的验收子代理）`map go Greenhouse`
    #    走进去、连读 6 帧稳定落在 **(10,23)**（(10,24) 是踩上去就出去的出口，人停不住）。
    "Greenhouse": (10, 23),
    "BusStop": (9, 23),         # 从农场来
    "Town": (0, 54),            # 从巴士站来（主入口）
    "Mountain": (54, 5),        # 矿洞门口（安全）
    "Forest": (68, 1),          # 从农场下口来
    "Beach": (38, 1),
    "Backwoods": (14, 39),      # 从农场上口来
    "Railroad": (29, 59),
    "Desert": (18, 27),
    "SeedShop": (6, 29),        # 皮埃尔商店(入口)
    "Hospital": (6, 17),
    "Saloon": (14, 24),
    "Blacksmith": (5, 19),
    "CommunityCenter": (32, 23),
    "JoshHouse": (9, 24),
    "HaleyHouse": (2, 24),
    "SamHouse": (4, 23),
    "ManorHouse": (5, 11),
    "ArchaeologyHouse": (3, 14),
    "MovieTheater": (12, 12),
    "JojaMart": (13, 29),          # 🏬 Joja超市进来落点（真机读：门 LockedDoorWarp 13 29/14 29）
    "AbandonedJojaMart": (9, 13),  # 🏚 废弃超市落点（Town.cs:299 warpFarmer(9,13)；⚠️无真机样本）
    "ScienceHouse": (6, 24),
    "SebastianRoom": (1, 1),
    "FishShop": (5, 9),
    "BoatTunnel": (6, 12),         # 🚢 从鱼店后门进来的**真实落点**（反编译 `FishShop.cs:74` warpFarmer("BoatTunnel",6,12)）；2026-10-05 补28c 改正（原写 (4,10)=售票机站格，不是落点）
    "AnimalShop": (13, 19),
    "WizardHouse": (8, 24),
    "Woods": (58, 15),
    "SandyHouse": (4, 9),
    "Club": (8, 13),
    "AdventureGuild": (6, 12),
    "Mine": (18, 13),
    "SkullCave": (7, 8),
    "Sewer": (16, 11),
    "BugLand": (15, 53),
    "BathHouse_Entry": (5, 9),
    # ♨️ 更衣室/泳池的「入口落点」= 图内最后一格（真出口在图外 y=28，见 MAP_LINKS 注释）
    "BathHouse_WomensLocker": (13, 27),
    "BathHouse_MensLocker": (3, 27),
    "BathHouse_Pool": (6, 0),       # 🏊 左上下水口正上方（女侧）；男侧落地是 (21,0)
    "Tunnel": (34, 9),
    "Tent": (2, 5),
    "IslandSouth": (21, 43),
    "IslandWest": (104, 41),
    "IslandEast": (0, 41),
    "IslandNorth": (40, 24),        # 火山入口区（2026-08-15补，待实测）
    "IslandFieldOffice": (4, 10),
    "IslandHut": (7, 13),
    "LeoTreeHouse": (3, 8),
    "IslandNorthCave1": (6, 11),
    "IslandFarmCave": (4, 10),
    "IslandShrine": (13, 28),
    "VolcanoEntrance": (1, 1),
    "VolcanoDungeon0": (31, 50),
    # 🏝️ 2026-10-05（补30，真机落点读数）：(14,15) → **(14,17)**。
    #    旧值 (14,15) **表内自相矛盾**：同文件 `:902` 的 MAP_LINKS 注释早写着入口是
    #    `IslandWest(77,39) → IslandFarmHouse(14,17)`。真机（恒的验收子代理）`map go` 走进去、
    #    连读 6 帧稳定落在 **(14,17)**；本文件 `:904` 的出口瓦片 (14,18) → IslandWest(77,40) 也自洽。
    "IslandFarmHouse": (14, 17),
    "QiNutRoom": (7, 8),           # 🥥 真实落点（反编译 `IslandWest.cs:336` warpFarmer("QiNutRoom",7,8,0)）；
                                   #    2026-10-05 补28c 改正（原写 (7,7) 差一格）。判据同 MasteryCave 那条先例：
                                   #    以游戏自己的 warpFarmer 落点为准（(7,8) 有 Back 瓦片 105，可站）
    # 🎓 精通山洞（2026-09-16 改）：原来是 (7,9)，但游戏的**真实落点**是 `warpFarmer("MasteryCave",7,11,0)`
    #    （反编译 GameLocation.cs:8784）—— (7,11) 就在出洞口 (7,12) 正北一格，不会被立刻弹出去。
    "MasteryCave": (7, 11),
    "Caldera": (22, 22),
    "WitchWarpCave": (4, 9),
    "WitchSwamp": (20, 42),
    "WitchHut": (7, 15),
    "WizardHouseBasement": (2, 5),
    "Summit": (10, 29),
}


# ── 任务建议 ──
# AI 可以根据时间/季节/天气推荐做什么
TASK_SUGGESTIONS = {
    "spring": {
        "sunny": [
            "去皮埃尔买防风草种子 → 种地",
            "去海滩钓鱼（春季鱼多）",
            "去矿洞挖铜矿和铁矿",
        ],
        "rainy": [
            "去矿洞挖矿（省了浇水时间）",
            "去鱼店买鱼饵 → 下雨钓鱼有特殊鱼",
        ],
    },
    "summer": {
        "sunny": [
            "收蓝莓 → 酿酒桶酿酒",
            "去海滩/森林钓鱼",
        ],
    },
}

# ── 查路线工具 ──
def plan_route(from_map, to_poi):
    """返回: [ (地图名, 目的坐标, 说明), ... ]"""
    if to_poi in POI:
        target = POI[to_poi]
        to_map = target["map"]
        to_pos = target["pos"]
    else:
        to_map = to_poi
        to_pos = None

    if from_map == to_map:
        return [(to_map, to_pos, "已在地图上")]

    # BFS 搜地图连接
    graph = {}
    for src, _, dst, _ in ROUTES:
        graph.setdefault(src, set()).add(dst)
        graph.setdefault(dst, set()).add(src)

    visited = {from_map}
    queue = [(from_map, [from_map])]
    while queue:
        cur, path = queue.pop(0)
        for nxt in graph.get(cur, set()):
            if nxt == to_map:
                route = []
                for m in path[1:] + [to_map]:
                    entry = POI.get(m, {}).get("pos", None)
                    route.append((m, entry, f"到 {m}"))
                route.append((to_map, to_pos, f"到达 {to_poi}"))
                return route
            if nxt not in visited:
                visited.add(nxt)
                queue.append((nxt, path + [nxt]))
    return None

if __name__ == "__main__":
    print("=== 地图路线表 ===")
    for src, direction, dst, entry in sorted(ROUTES):
        print(f"  {src} [{direction}] → {dst} @ {entry}")

    print("\n=== 兴趣点列表 ===")
    for name, info in sorted(POI.items()):
        print(f"  {name}: {info['map']} {info['pos']} — {info['note']}")

    print("\n=== 路线测试 ===")
    r = plan_route("Farm", "海滩钓鱼点(码头)")
    if r:
        for m, pos, note in r:
            print(f"  {m} {pos} {note}")
