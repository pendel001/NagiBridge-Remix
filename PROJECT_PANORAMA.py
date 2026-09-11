#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════════════╗
║  NagiBridge 项目全景图 · 给外部 AI 的项目速览                           ║
║  星露谷 AI 远程控制 + MCP 文游化接口                                     ║
╚══════════════════════════════════════════════════════════════════════════╝

一句话：SMAPI C# mod（HTTP API，端口 7842/7843）+ Python MCP 服务器
（17 个工具，streamable-http 8000）让 AI 像真人一样操控星露谷——拟人走位、
受限动作、自主决策。域工具模式恒开，只露这一套 17 个（15 域入口 + 2 独立工具），
`--full` 已退役（2026-09-06）；`advance_story`/`profile`/`which_role` 已收编进 menu/check 域（2026-09-11）。

角色映射（由端口决定，不写死）：
  · 7842 = 人（host）   —— 检测/广播走这个端口
  · 7843 = AI（farmhand）—— 操作走这个端口（AI 控制的角色）
  惯例：操作打 7843，检测/广播打 7842(host_chat)。

运行 `python PROJECT_PANORAMA.py` 可打印全文；或 import 本文件读数据。
"""

PROJECT_OVERVIEW = """
🎮 NagiBridge = 给"AI 玩家"插上一双能动手的眼睛
──────────────────────────────────────────────────────
玩家(host)开一个星露谷联机房 → AI(farmhand) 加入同一张图 → MCP 服务器暴露
工具让 AI 做任何真人能做的事：走位/对话/商店/钓鱼/下矿/种地/节日。
（20 个注册工具，域模式恒开，只露这一套；`--full` 已退役。

分三层：
  [游戏层]  Stardew Valley 1.6.15（SMAPI 4.5.2）+ NagiBridge C# mod
  [桥接层]  ModEntry.cs —— HTTP API（每个玩家一个端口 7842/7843）+ Harmony 补丁
  [智能层]  scripts/nagi_mcp_server.py —— MCP 服务器（17 工具/15 域入口）+ 状态注入 + 心跳）

AI 每次调工具，返回都自动附带"状态速报"（眼睛）：位置/时间/天气/背包/体力/
待办/节日/新闻。状态条分层：每天第一次全量，之后精简，省 token。
"""

# ══════════════════════════════════════════════════════════════════════════
# 1️⃣ 项目文件结构 —— 哪些文件干什么
# ══════════════════════════════════════════════════════════════════════════

FILE_STRUCTURE = {
    # ── C# mod（编译进游戏，改完必须 rm -rf bin obj 重编 + DLL 双盘复制）──
    "ModEntry.cs": "SMAPI mod 主文件：HTTP API 服务器（~120 端点）+ Harmony 补丁（IsActive/表情/聊天/节日提示/Lidgren 端口）+ 拾取检测 + 结算/升级菜单自动确认",
    "ModConfig.cs": "mod 配置（mode/端口/LLM 等）",
    "NagiBridge.csproj": "C# 工程文件",
    "ChatHud.cs / LlmClient.cs / server.ts / index.ts": "⚠️ 原作者遗留，已 .claudeignore 忽略，勿改",

    # ── Python MCP 服务器（核心智能层）──
    "scripts/nagi_mcp_server.py": "MCP 服务器（streamable-http:8000 或 --stdio）：17 个工具 + 状态注入(_with_state) + 心跳 + 节日/导航/脚本编排。15 域入口(check/farm/mine/cabin/social/scene/menu/storage/daily/map/festival/fish/settings + script/session；care→farm、quest→menu 已于 09-02 合并)，域模式恒开只露这套。wear→daily ops，bundle_kb/donate/read_book→menu ops(2026-08-22 收编)；advance_story→menu ops、profile/which_role→check ops(2026-09-11 收编，20→17)",
    "scripts/stardew_api.py": "HTTP API 封装层：Python 侧调 7842/7843 的 /xxx 端点，port↔角色自动检测",
    "scripts/player_activity.py": "行为检测 + 心跳：发呆检测/同场景玩家检测/窗口判定，描述房主活动给 AI 看",
    "scripts/locations.py": "地图知识库：MAP_LINKS(门/出口瓦片) + MAP_FEATURES(地点功能) + POI(兴趣点) + POI_FACE(结构化站位朝向) + SHOP_HOURS",
    "scripts/calendar_data.py": "日历知识库：FESTIVALS/FESTIVAL_SHOPS/FESTIVAL_POI/FESTIVAL_GUIDE/FESTIVAL_PREP/生日/商店/解锁表",
    "scripts/crops.py": "作物知识库（ID→名字/镰刀/再生）",
    "scripts/movie_data.py": "电影院知识库（8 电影 2 年循环 + 24 零食）",
    "scripts/go_to.py": "一键导航（homeDoor 等）",
    "scripts/plan_engine.py": "⚠️ 已退役（计划模式 2026-08-17 下线），勿用",

    # ── 自动化脚本（可后台异步跑，白名单内自动转后台 + 自动注入 --port）──
    "scripts/bomb_mine.py / bomb_escort.py / bomb_common.py / bomb_volcano.py": "炸矿三模式：自动/协同/火山（炸弹=select+placementAction 自动引爆，躲半径+4 防自伤）",
    "scripts/mine_run.py": "矿洞/头骨矿冲层脚本（逐层+整理背包）——08-29 _rock_name 三级(object名>ORE_NODE_IDS>dump_tile真名)认隐藏名宝石/放射矿,ore_score 关键词(放射>宝石),mine_rock 校验改目标格有object",
    "scripts/rock_run.py / rock_scan.py": "室外镐击：采石场/挖掘场/蚌矿场 敲可破物(骨/黏土/蚌/矿点/宝石/煤/放射矿)，只跳普通石。默认只扫不敲(--dig 才敲)，目标按 objId+dump_tile 真名认(1.6 节点 Name 全报 'Stone' 只 objId 可信)；MCP scene ops=rock",
    "scripts/fish_run.py": "钓鱼自动化（walk_to 到钓点→拿竿→抛竿，--max-casts 收手）+ 拿竿后自动补饵/钓具",
    "scripts/farm_row.py / fruit_round.py / building_round.py / harvest.py / scythe_crops.py / keg_manager.py / furnace_manager.py": "农活：行田/果树圈收/建筑一轮/收获/镰刀收割/酒桶管理/熔炉",
    "scripts/pet_animals.py / feed_hay.py / pet_walk.py": "养动物：摸宠/喂干草/遛宠",
    "scripts/pickup_scene.py / scan_entries.py": "场景拾取/扫描",
    "scripts/berry_run.py / blessing_statue.py / chop_trees.py / clear_area.py / machine_loader.py / check_design.py": "其他自动化：浆果/祝福像/砍树/清地/装机器(装载失败列机器需求给AI自查,机器type→输入种类表_MACHINE_NEED)/设计检查",
    "scripts/chat_watcher.py / chat_overlay.py / channel_server.py": "聊天监听/悬浮/通道服务（广播/聊天联动）",

    # ── 文档（新知识写这里，别写回 CLAUDE.md）──
    "CHANGELOG.md": "完整知识库（35k 字符）：所有机制/坐标/坑/变更记录，改代码前先查",
    "CLAUDE.md": "精简指引（≤500 字四段式）：概述/结构/规范/5 条坑",
    "TOOL_INVENTORY.md": "MCP 工具手册（域工具大白话说明书）",
    "PROJECT.md": "项目总览（薄的人间向概览，末尾指到本文件/AGENTS/CHANGELOG）",
    "README.md": "玩家向：安装 + 游戏内聊天 + MCP 连接（含连不上速查）",
    "PROJECT_PANORAMA.py": "本全景图数据源，`python PROJECT_PANORAMA.py` 打印全文，import 本文件读数据",
    "festival/": "中文 wiki 节日原文（数据源，浓缩进 calendar_data）",
}

# ══════════════════════════════════════════════════════════════════════════
# 2️⃣ 已有端点清单 —— 每个 endpoint 的功能和参数
# ══════════════════════════════════════════════════════════════════════════
# 端口：AI 操作走 7843，host 广播走 7842。方法：GET=读，POST=写。
# 参数以 JSON body 传入。下面按域分组，标注核心参数。

ENDPOINTS = {
    "📊 状态/检测（读）：": {
        "/state":        ("完整游戏状态", "位置/时间/天气/背包/体力/菜单/事件/NPC/最近事件/recent_events"),
        "/status":       ("精简状态", "位置/时间/背包格数等轻量字段"),
        "/alerts":       ("系统警报队列", "读未消费的警报（捡起即清空）"),
        "/surroundings": ("周围视野", "中心(10格内)地块 passable/NPC/怪物/farmers，主线程化安全"),
        "/warps":        ("本图出口/门瓦片", "MAP_LINKS 的动态探测"),
        "/dump_tile":    ("单瓦片详情", "x,y → 该格对象/地形/可交互物"),
        "/farm_report":  ("全农场总览", "作物浇水/动物待摸/机器完成，仿农场电脑"),
        "/machines":     ("机器列表", "type/status/位置，筛完成"),
        "/animals":      ("动物列表", "building/是否摸过/产物就绪"),
        "/buffs":        ("当前 buff", "生效中的状态效果"),
        "/worn":         ("已装备", "戒指/鞋/帽等"),
        "/debris":       ("周围可拾取物", "地上物品"),
        "/scan /scan_chests": ("扫描箱子/物品", "场景内箱子内容（颜色/名字/坐标）"),
        "/farm_buildings": ("农场建筑", "位置/类型，动态检测设施"),
        "/festival":     ("节日实况", "festivalName/location/actors(NPC坐标)"),
        "/festival_data":("节日数据", "今天节日原始数据"),
        "/egg_tiles":    ("蛋蛋节蛋坐标", "扫 Paths 图层 fest 图块"),
        "/find_npc":     ("找 NPC", "name → 当前位置"),
        "/unlocks":      ("解锁状态", "真实 flag（地图/设施）"),
        "/mail":         ("邮箱未读", "信件列表/可领附件"),
        "/quest_list /quest_progress": ("任务列表/进度", "📋新任务/✅完成检测"),
        "/screenshot":   ("截图", "AI 视角 PNG（看场景用）"),
    },
    "🚶 移动/导航（写）：": {
        "/walk_to":      ("同图走到坐标", "location,x,y；同图 POI/落点用，不用 /move+BFS"),
        "/move":         ("移动", "方向/距离"),
        "/position":     ("瞬移设置位置", "x,y（同图精确站位用，跨图别用）"),
        "/warp":         ("瞬移到地图", "location,x,y"),
        "/warp_building":("进建筑", "建筑名"),
        "/warp_into":    ("瞬移进某地图", "兜底用"),
        "/face":         ("转身", "direction 0上/1右/2下/3左"),
        "/ladder":       ("矿井下楼/找梯", "矿洞专用"),
        "/crawl_bed":    ("找床", "locate 返回床位置"),
    },
    "🖱️ 交互/菜单（写）：": {
        "/interact":     ("交互场景物", "x,y（柜台/NPC/机子），可加对角"),
        "/click":        ("点击", "no_move/no_mouse(不碰OS鼠标)/real(走真实receiveLeftClick)"),
        "/click_tile":   ("点击瓦片", "x,y"),
        "/menu":         ("读当前菜单", "type/items/shopItems/heldItem/responses"),
        "/menu/click":   ("点菜单", "option=N选项 / button=关闭 / x,y / item=物品名 / quantity=数量 / real"),
        "/menu_close":   ("强关菜单", "ShopMenu/对话/ItemGrabMenu 通用"),
        "/key":          ("按键", "confirm/esc/数字/no_焦点；对话推进用 confirm 或 press_key(ok)"),
        "/queue":        ("动作队列", "排队执行"),
        "/drag /drop":   ("拖拽/放下", "菜单内物品移动"),
        "/focus /zoom /resolution /pause /set_pause": ("窗口控制", "失焦暂停/缩放/分辨率"),
    },
    "🎒 物品/背包/工具（写）：": {
        "/select":       ("选背包物品", "name（拿在手上）"),
        "/use":          ("使用手持物", "工具挥动/物品放置（炸弹 placementAction 自动引爆）"),
        "/tool":         ("工具操作", "指定工具动作"),
        "/tool_area":    ("工具区域操作", "浇水/锄地蓄力补漏的唯一可靠路径"),
        "/give":         ("给物品", "name,count,quality（⚠️不保存，重启丢）"),
        "/eat":          ("吃食物", "name"),
        "/heal":         ("回血", "直接写 HP（farmhand 吃食物回血不生效，用这个）"),
        "/store_all":    ("全背包存入", "就近箱子/指定箱"),
        "/store /chest /chest_take /placechest /name_chest": ("箱子管理", "读箱/存/取/放/命名"),
        "/refill":       ("灌水壶", "水池"),
        "/buy /buy_animal /sell_to_shop /sell": ("商店交易", "买物品/买动物/卖物品"),
        "/craft /cook /recipes /craft_recipes": ("合成/烹饪", "配方列表/制作"),
        "/process_geode":("砸晶球", "晶球 ID"),
        "/furniture /furniture_pickup": ("家具", "摆放/拿起（1.6 左键 LowPriorityLeftClick）"),
        "/ring /rings /trinket /trinkets": ("戒指/饰品", "穿戴/卸下"),
        "/equip":         ("通用穿脱(2026-08-22)", "name=穿(背包找同名,自动判槽位)/slot=脱/加hand=戒指指定手;一个工具替代分槽位穿法;走游戏原生 Farmer.Equip 保染色,MCP 侧挂 daily ops=wear"),
        "/weapon_diag":  ("武器诊断", "wtype 0剑/1匕/2锤/3格挡剑镰 + 挥击速度"),
        "/till_area /water /clear_ground /harvest /ripen /sprinklers": ("农活", "锄地区域/浇水/清地/收获/催熟/洒水器"),
        "/toggle_doors": ("开关畜棚门", "门"),
        "/silo /mastery /mastery_claim /carpenter": ("设施", "干草塔/精通/木匠升级"),
        "/petbowl /petall /waterbowl": ("宠物", "宠物水碗/全摸/水碗"),
    },
    "💬 社交/聊天/送礼（写）：": {
        "/chat":         ("发聊天", "message（AI 进程内用）"),
        "/chat/history": ("聊天历史", "读会话"),
        "/chat/push":    ("推送聊天", "message（给房主看）"),
        "/emote":        ("发表情", "name，netDoEmote 网络同步"),
        "/gift":         ("送礼", "NPC+物品"),
        "/friendship":   ("好感度", "读 NPC 好感"),
        "/dance_invite": ("跳舞邀请", "target=玩家提案/NPC direct=true 直设 dancePartner，花舞节"),
        "/appearance /appearance_info /character_customize /color_pick": ("捏脸", "必须用 SDV 官方 change* 方法同步双 Net 字段"),
        "/appearance_ref": ("外观参考全量烤真(2026-08-22)", "遍历 Game1.shirtData/pantsData 用 ItemRegistry.Create 拿本地化 DisplayName+getDescription;数据源给 list_shirt_ref/list_pants_ref(301上衣+18裤),纯数据非反编译"),
    },
    "😴 睡觉/过夜（写）：": {
        "/sleep":        ("睡觉", "就地睡/stay 参数"),
        "/wakeup":       ("起床", ""),
        "/cancel_sleep": ("取消睡觉", ""),
        "/settlement_confirm": ("过夜结算确认", "farmhand 结算界面由 AI 确认"),
        "/ready_state":  ("就绪状态", "联机过夜同步"),
        "/money":        ("金钱", "读/改"),
    },
    "🎪 节日（写）：": {
        "/festival/interact": ("节日 NPC 互动", "name，自然走位+自动推进对话"),
        "/festival/answer":   ("节日事件应答", "N 选项"),
    },
    "🛠️ 调试/杂项（写）：": {
        "/mine_debug /museum_diag /museum_donate /museum_remove /museum_tiles": ("矿洞/博物馆调试", "捐赠/移除/瓦片"),
        "/mine/elevator": ("读矿井电梯可达层(2026-08-22)", "⚠️内部自动读,不给AI直调:接「深处的危险」会重置电梯,脚本 mine_run/bomb_mine 启动时自动读 maxFloor 判可达,重置态=maxFloor=1"),
        "/qi_shop /qi_buy /process_geode_batch": ("齐先生商店/批量晶球", ""),
        "/unlock_debug /mastery_claim": ("调试解锁", ""),
        "/hud":          ("HUD 控制", "⚠️ 推送统一走聊天框不走 HUD"),
        "/fishbot":      ("钓鱼自动化", "Fishbot mod 控制"),
        "/farm_buildings": ("农场建筑枚举", "动态设施定位（图腾柱等）"),
    },
}

# ══════════════════════════════════════════════════════════════════════════
# 3️⃣ 核心模式 —— 反应式检测、脚本模式、事件推送等已建立的范式
# ══════════════════════════════════════════════════════════════════════════

CORE_PATTERNS = {
    "双端口↔角色映射": """
7842=人(host)，7843=AI(farmhand)，由"谁先开游戏"动态分配，不写死。
· 操作(工具/走位/交互) → 打 7843
· 检测(心跳/状态) / 广播(提醒/消息) → 打 7842 的 host_chat
· detect_roles/ensure_roles 自动对齐，纯 Python 不用重编 DLL
""",

    "状态条注入（AI 的眼睛，最重要）": """
每次工具调用结果末尾 `_with_state` 自动附加状态速报（省 token 分层）：
· 每天第一次调用 → full=True（日历/节日/农场晨报全量）
· 之后 → full=False（必选项 + 待办提醒：位置/时间/天气/HP/体力/钱/背包/地图枚举/商店营业/节日POI/工具待取/低血警告）
· 心跳检测的是【房主】活动给 AI 看，不是 AI 自己
· 状态获取放后台线程 + 8s 超时兜底（游戏卡住不阻塞工具返回）
· 2026-08-19 起【不再自动推进剧情】——只报「🎬 剧情」，由 AI 自己调 advance_story
""",

    "反应式检测（Harmony 补丁 + Python 心跳）": """
C# 补丁（ModEntry.cs）：
· Game.IsActive → 后台失焦也能操作（根治 10048/失焦卡死）
· Farmer.performPlayerEmote → 房主表情检测 → recent_events
· ChatBox.receiveChatMessage → 房主聊天检测 → recent_events
· Game1.showGlobalMessage → 节日原生提示抓取（"夏威夷宴会已开始"等）
· LidgrenServer.initialize → 10048 双开端口冲突修复
Python 侧：
· player_activity 心跳：发呆检测（静止+窗口判定）/同场景玩家检测/房主活动描述
· InventoryChanged 拾取检测：数量快照 diff 净增 → 📦 拾取新闻
· 任务变化/邮件/绿雨/节日 各按 (season,day) 去重注入一次
""",

    "事件推送（recent_events + 聊天框广播）": """
· recent_events 缓冲：拾取/聊天/表情/邮件/任务/节日提示 → AI 每次状态条看到
· 广播统一走【聊天框】不走 HUD（过夜复盘能看到）；host 7842 /chat/push
· _plan_notify：脚本完成/暂停/兜底/节日打断 → 注入 AI 上下文 + 可双端口广播
· 节日 bug 提醒双端口注入（AI 状态条 + host 聊天框都播）
""",

    "脚本模式（B1 异步脚本）": """
· 长脚本白名单(_ASYNC_SCRIPTS 8个) + settings async_tools 默认开 → 自动转后台跑，不打断 AI 聊天
· script stop/continue + _bg_start 自动注入 --port AI 端口（防挪到人/host 角色）
· 调度器(plan)已退役(2026-08-17)；go_sleep_flow 共享（crawl_bed 不挪位 + /sleep 就地睡 + 走出建筑刷新同步）
· 兜底：凌晨自动睡觉拦下运行脚本；脚本卡住→_plan_notify 提醒 AI 处理
""",

    "导航范式（拟人走路，不瞬移）": """
· 同图 POI → walk_to（+POI_FACE 自动站位朝向）
· 跨图唯一入口 → map_go（走出口瓦片/门/买票的真实路径，BFS 逐段 + 每段验证）
· 交通优先级：图腾柱 > 矿车 > 走路（动态检测 farm_buildings）
· 矿洞用 position（不是 walk_to）；室内防穿墙；FindPath 邻居兜底
· 卡墙检测：连续 5 次位置没变 → 提醒 warp_safe 紧急脱离
""",

    "菜单流水线（所有界面交互 = 一套流程）": """
① interact_at(坐标) 点场景物 → 弹菜单/对话
② read_menu()      看弹出来的是啥（商店/对话/选项/ItemGrabMenu）
③ menu_click(option/button/item/quantity) 点菜单
④ press_key(confirm/esc/数字)
配角的三个：select_item(选背包) / use_tool(挥工具) / face(转身)
关键坑：对话推进用 /click(no_mouse) 或 press_key(ok)；菜单选项用 menu_click(option=N)
""",

    "节日系统（festival 域 + 知识库）": """
· festival(ops, ...)：today/next/go/info/interact/answer/shop/eggs/egg_note/egg_run/dance/prep/help
· go=map_go 去节日地点；shop=节日商店（FESTIVAL_SHOPS 登记柜台才开，非出现日不显示）
· FESTIVAL_POI 限定 POI（沙漠节厨师等）；FESTIVAL_GUIDE 玩法；FESTIVAL_PREP 备战评分表（不注入，AI 主动调 festival prep）
· 花舞节跳舞：festival dance（玩家提案/NPC direct=true 直设 dancePartner），只能 AI 主动邀请
· ⚠️ 节日场地平时不开放，别用 map_go 导航节日图（Temp/DesertFestival 只在节日存在）
""",

    "农活域（farm 域）": """
农活必走 farm 域（farm/check/...）：plot_plan 地皮规划 → clear 清杂 → till 锄 → plant 种 → water 浇 → 收。
设计原则：连通域分析把设施/杂草/树纳入地块；蓄力用 tool_area；浇水失败根因=use_item 无释放，只用 /tool_area。
""",

    "域工具收敛（2026-08-22，15 域入口 + 2 独立；域模式恒开）": """
所有工具收敛成 15 个域入口 + 2 独立工具（域模式恒开，只露这 17 个；--full 已退役）：
· 15 域 = check/farm/mine/cabin/social/scene/menu/storage/daily/map/festival/fish/settings + script/session
· care→farm、quest→menu 已于 09-02 合并（不再单列）
· 改名/合并：settings 域合并「捏脸」(appearance)+外观参考进来(不再拆)；scene 因 interact 占用改名；fish 域曾缺注册不可达(2026-08-22 修复)
· 退役：plan(计划模式)/accept_quest/buy_item 已下线；bomb_escort 不再对外暴露(内建进 bomb_mine 自动转内部)；festival bot 体系全删
· 收编：wear/lie_bed → daily ops；bundle_kb/donate/read_book → menu ops；rock/挖石 → scene ops(2026-08-29 室外镐击)
· 🗜️ 2026-09-11 再收编（20→17）：advance_story → menu ops(menu 的 dispatch 本就直指同一函数，留着=两条路做同一件事)；profile/which_role → check(what="profile"/"role")(都是"查我自己"归查询域)。⚠️改 keep-set 必须同步改引导文案(状态条/menu/check/fish/daily 的 help)，否则 AI 照旧文案调隐藏名=当场卡死
""",

    "长脚本便利（防坑）": """
· 便利工具长脚本自动转后台 + 自动注入 --port AI 端口
· DLL 改动要 rm -rf bin obj 重编 + C/F 双盘复制（否则"改了没生效"）
· Windows GBK 编码坑：终端跑 Python 加 PYTHONIOENCODING=utf-8（emoji 会炸 GBK）
""",

    "已知坑（CLAUDE.md 5 条）": """
1. 导航：地面/walk_to、矿洞/position、跨图/map_go；别用 /move+BFS
2. DLL 必须 C+F 两处复制
3. 对话推进用 /click(no_mouse) 或 press_key(ok)，别用 key confirm
4. 敲一下→检查→碎了停，不硬编码次数
5. 长脚本自动注入 --port AI 端口
""",
}

# ══════════════════════════════════════════════════════════════════════════
# 入口：python PROJECT_PANORAMA.py 打印全文
# ══════════════════════════════════════════════════════════════════════════

def _render():
    out = [PROJECT_OVERVIEW, "\n" + "=" * 70,
           "\n1️⃣ 项目文件结构\n" + "─" * 70]
    for k, v in FILE_STRUCTURE.items():
        out.append(f"  · {k}\n      {v}")
    out.append("\n" + "=" * 70 + "\n2️⃣ 已有端点清单\n" + "─" * 70)
    for group, eps in ENDPOINTS.items():
        out.append(f"\n{group}")
        for ep, (func, params) in eps.items():
            out.append(f"  · {ep:<22} {func}")
            if params:
                out.append(f"      ⚙️  {params}")
    out.append("\n" + "=" * 70 + "\n3️⃣ 核心模式\n" + "─" * 70)
    for k, v in CORE_PATTERNS.items():
        out.append(f"\n▌{k}\n{v}")
    return "\n".join(out)


def main():
    print(_render())


if __name__ == "__main__":
    main()
