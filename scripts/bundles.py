"""
🌱 bundles.py — 社区中心「收集包 / 献祭」静态知识库（2026-08-22，据中文维基整理）

与 bundle_status（**只读存档看本档缺口，不走路**，2026-09-11 起）互补：
本表是**静态知识**（这个包要什么、去哪弄、完成后解锁啥），存档状态去问 bundle_status。

用法:
  from bundles import search_bundles, BUNDLE_ROOMS
  search_bundles("献祭")          # 全房间概览
  search_bundles("工艺室")        # 那一间所有收集包
  search_bundles("春季作物")      # 按收集包/物品名模糊搜
  search_bundles("防风草")        # 哪些收集包要它

命名说明：本表用维基/官方中文名（绿豆/甜瓜/西红柿…）；项目 crops.py 里部分作物名
略有出入（青豆=绿豆/番茄=西红柿/西瓜=甜瓜 等），AI 看到时可自行对应。
"""

# 每 room: room(中文名) / area(游戏 area 号) / unlock(完成后的奖励简述) / reward(房间总奖励)
#          / bundles[] 每个含 name/items/reward
# item: {"n": 数量文本(1省略), "note": 来源（采集/季节/获取方式）}
#
# ⚠️ **本表是本项目里"收集包房间"的唯一来源**（2026-09-11 定）。`area` 号来自游戏权威
#    `CommunityCenter.getAreaNumberFromName`(CommunityCenter.cs:205)：
#      0=茶水间 Pantry / 1=工艺室 Crafts Room / 2=鱼缸 / 3=锅炉房 / 4=Vault(**本表叫"地下室"**) /
#      5=布告栏 / 6=废弃Joja超市(遗失的收集包)
#    ⚠️ 注意 **0 是茶水间不是工艺室**（曾一度写反）；`area` 用于 `room_by_area()` 反查，
#    `bundle_status` 拿存档的 area 号来显示「完成后解锁什么」。别在别的文件里再抄一份房名表。
BUNDLE_ROOMS = [
    {
        "room": "工艺室",
        "area": 1,
        "aliases": ["工艺室", "Craft", "觅食", "春季觅食", "夏季觅食", "秋季觅食", "冬季觅食"],
        "unlock": "修复矿井东侧木桥 → 进采石场（此后矿车才能到采石场）",
        "reward": "桥梁维修",
        "bundles": [
            {"name": "春季觅食收集包", "items": [
                {"n": 1, "name": "野山葵", "note": "春季 觅食"},
                {"n": 1, "name": "水仙花", "note": "春季 觅食 / 花舞节皮埃尔处"},
                {"n": 1, "name": "韭葱", "note": "春季 觅食"},
                {"n": 1, "name": "蒲公英", "note": "春季 觅食 / 花舞节皮埃尔处"},
            ], "reward": "春季种子(30)"},
            {"name": "夏季觅食收集包", "items": [
                {"n": 1, "name": "葡萄", "note": "夏季 觅食 / 秋季 耕种"},
                {"n": 1, "name": "香料果", "note": "夏季 觅食 / 农场山洞(果蝠)"},
                {"n": 1, "name": "甜豌豆", "note": "夏季 觅食"},
            ], "reward": "夏季种子(30)"},
            {"name": "秋季觅食收集包", "items": [
                {"n": 1, "name": "普通蘑菇", "note": "秋季 觅食 / 秘密森林 / 蘑菇洞 / 大蘑菇树液"},
                {"n": 1, "name": "野梅", "note": "秋季 觅食"},
                {"n": 1, "name": "榛子", "note": "秋季 觅食"},
                {"n": 1, "name": "黑莓", "note": "秋季 觅食 / 农场山洞(果蝠)"},
            ], "reward": "秋季种子(30)"},
            {"name": "冬季觅食收集包", "items": [
                {"n": 1, "name": "冬根", "note": "冬季 锄远古斑点 / 矿洞41-79蓝史莱姆"},
                {"n": 1, "name": "水晶野果", "note": "冬季 觅食 / 矿洞41-79灰尘精灵"},
                {"n": 1, "name": "雪山药", "note": "冬季 锄远古斑点"},
                {"n": 1, "name": "番红花", "note": "冬季 觅食"},
            ], "reward": "冬季种子(30)"},
            {"name": "建筑收集包", "items": [
                {"n": 99, "name": "木材", "note": "斧头砍树 ×2"},
                {"n": 99, "name": "木材", "note": "斧头砍树"},
                {"n": 99, "name": "石头", "note": "十字镐敲碎"},
                {"n": 10, "name": "硬木", "note": "升级斧砍大树桩/圆木, 矿井木箱"},
            ], "reward": "煤炭窑(1)"},
            # 备选多选：12 件 6 槽
            {"name": "异域情调觅食收集包", "items": [
                {"n": 1, "name": "椰子", "note": "沙漠 觅食"},
                {"n": 1, "name": "仙人掌果子", "note": "沙漠 觅食"},
                {"n": 1, "name": "山洞萝卜", "note": "矿洞 木箱/锄地"},
                {"n": 1, "name": "红蘑菇", "note": "矿洞 觅食 / 秘密森林 / 蘑菇洞"},
                {"n": 1, "name": "紫蘑菇", "note": "矿洞 / 蘑菇洞 / 森林农场秋"},
                {"n": 1, "name": "枫糖浆", "note": "树液采集器 放枫树"},
                {"n": 1, "name": "橡树树脂", "note": "树液采集器 放橡树"},
                {"n": 1, "name": "松焦油", "note": "树液采集器 放松树"},
                {"n": 1, "name": "羊肚菌", "note": "春季 秘密森林 觅食 / 蘑菇洞"},
            ], "reward": "秋日恩赐(5)"},
        ],
    },
    {
        "room": "茶水间",
        "area": 0,
        "aliases": ["茶水间", "Pantry", "作物", "农作物", "品质作物", "工匠", "动物制品"],
        "unlock": "修复温室（一年四季可种、不随季节枯死）→ 艾芙琳赠花盆配方",
        "reward": "温室",
        "bundles": [
            {"name": "春季作物收集包", "items": [
                {"n": 1, "name": "防风草", "note": "春季 农作物"},
                {"n": 1, "name": "绿豆", "note": "春季 农作物"},
                {"n": 1, "name": "花椰菜", "note": "春季 农作物"},
                {"n": 1, "name": "土豆", "note": "春季 农作物"},
            ], "reward": "生长激素(20)"},
            {"name": "夏季作物收集包", "items": [
                {"n": 1, "name": "西红柿", "note": "夏季 农作物"},
                {"n": 1, "name": "辣椒", "note": "夏季 农作物"},
                {"n": 1, "name": "蓝莓", "note": "夏季 农作物"},
                {"n": 1, "name": "甜瓜", "note": "夏季 农作物"},
            ], "reward": "优质洒水器(1)"},
            {"name": "秋季作物收集包", "items": [
                {"n": 1, "name": "玉米", "note": "夏/秋季 农作物"},
                {"n": 1, "name": "茄子", "note": "秋季 农作物"},
                {"n": 1, "name": "南瓜", "note": "秋季 农作物"},
                {"n": 1, "name": "山药", "note": "秋季 农作物 / 掘地虫掉落"},
            ], "reward": "蜂房(1)"},
            {"name": "品质作物收集包", "items": [
                {"n": 5, "name": "防风草", "note": "春季 金星品质作物"},
                {"n": 5, "name": "甜瓜", "note": "夏季 金星品质作物"},
                {"n": 5, "name": "南瓜", "note": "秋季 金星品质作物"},
                {"n": 5, "name": "玉米", "note": "夏/秋季 金星品质作物"},
            ], "reward": "罐头瓶(1)"},
            {"name": "动物制品收集包", "items": [
                {"n": 1, "name": "大壶牛奶", "note": "奶牛"},
                {"n": 1, "name": "棕色大鸡蛋", "note": "鸡 (Brown)"},
                {"n": 1, "name": "大鸡蛋", "note": "鸡"},
                {"n": 1, "name": "大瓶羊奶", "note": "山羊"},
                {"n": 1, "name": "动物毛", "note": "绵羊 / 兔子"},
                {"n": 1, "name": "鸭蛋", "note": "鸭"},
            ], "reward": "压酪机(1)"},
            # 备选多选：12 件 6 槽
            {"name": "工匠物品收集包", "items": [
                {"n": 1, "name": "松露油", "note": "产油机 做 松露"},
                {"n": 1, "name": "布料", "note": "织布机 / 回收机回收湿报纸"},
                {"n": 1, "name": "山羊奶酪", "note": "压酪机"},
                {"n": 1, "name": "奶酪", "note": "压酪机"},
                {"n": 1, "name": "蜂蜜", "note": "蜂房"},
                {"n": 1, "name": "果冻", "note": "罐头瓶"},
                {"n": 1, "name": "苹果", "note": "秋季 苹果树 / 农场山洞(果蝠)"},
                {"n": 1, "name": "杏子", "note": "春季 杏树 / 农场山洞(果蝠)"},
                {"n": 1, "name": "橙子", "note": "夏季 橘树 / 农场山洞(果蝠)"},
                {"n": 1, "name": "桃子", "note": "夏季 桃树 / 农场山洞(果蝠)"},
                {"n": 1, "name": "石榴", "note": "秋季 石榴树 / 农场山洞(果蝠)"},
                {"n": 1, "name": "樱桃", "note": "春季 樱桃树 / 农场山洞(果蝠)"},
            ], "reward": "小桶(1)"},
        ],
    },
    {
        "room": "鱼缸",
        "area": 2,
        "aliases": ["鱼缸", "Fish", "钓鱼", "鱼", "河鱼", "湖鱼", "海鱼", "蟹笼", "夜间垂钓"],
        "unlock": "移除矿井入口巨型卵石 + 威利送淘盘",
        "reward": "移除巨型卵石",
        "bundles": [
            {"name": "河鱼收集包", "items": [
                {"n": 1, "name": "太阳鱼", "note": "河流 6AM-7PM, 春/夏"},
                {"n": 1, "name": "鲶鱼", "note": "河流 全天, 春/秋; 秘密森林池塘, 夏; 仅雨天"},
                {"n": 1, "name": "西鲱", "note": "河流 9AM-2AM, 春/夏/秋; 仅雨天"},
                {"n": 1, "name": "虎纹鳟鱼", "note": "河流 6AM-7PM, 秋/冬"},
            ], "reward": "高级鱼饵(30)"},
            {"name": "湖鱼收集包", "items": [
                {"n": 1, "name": "大嘴鲈鱼", "note": "湖泊 6AM-7PM, 全季节"},
                {"n": 1, "name": "鲤鱼", "note": "湖泊 全天, 春/夏/秋"},
                {"n": 1, "name": "大头鱼", "note": "湖泊 全天, 全季节"},
                {"n": 1, "name": "鲟鱼", "note": "湖泊 6AM-7PM, 夏/冬; 难度较高"},
            ], "reward": "精装旋式鱼饵(1)"},
            {"name": "海鱼收集包", "items": [
                {"n": 1, "name": "沙丁鱼", "note": "海洋 6AM-7PM, 春/秋/冬"},
                {"n": 1, "name": "金枪鱼", "note": "海洋 6AM-7PM, 夏/冬"},
                {"n": 1, "name": "红鲷鱼", "note": "海洋 6AM-7PM, 夏/秋; 仅雨天"},
                {"n": 1, "name": "罗非鱼", "note": "海洋 6AM-2PM, 夏/秋"},
            ], "reward": "传送图腾：海滩(5)"},
            {"name": "夜间垂钓收集包", "items": [
                {"n": 1, "name": "大眼鱼", "note": "河/湖/森林池塘 12PM-2AM, 秋; 仅雨天"},
                {"n": 1, "name": "鲷鱼", "note": "河流 6PM-2AM, 全季节"},
                {"n": 1, "name": "鳗鱼", "note": "海洋 4PM-2AM, 春/秋; 仅雨天"},
            ], "reward": "小型光辉戒指(1)"},
            # 备选多选：10 件
            {"name": "蟹笼收集包", "items": [
                {"n": 1, "name": "龙虾", "note": "蟹笼"},
                {"n": 1, "name": "小龙虾", "note": "蟹笼"},
                {"n": 1, "name": "螃蟹", "note": "蟹笼 / 矿井岩石蟹"},
                {"n": 1, "name": "鸟蛤", "note": "蟹笼 / 海滩觅食"},
                {"n": 1, "name": "蚌", "note": "蟹笼 / 海滩觅食"},
                {"n": 1, "name": "虾", "note": "蟹笼"},
                {"n": 1, "name": "蜗牛", "note": "蟹笼"},
                {"n": 1, "name": "玉黍螺", "note": "蟹笼"},
                {"n": 1, "name": "牡蛎", "note": "蟹笼 / 海滩觅食"},
                {"n": 1, "name": "蛤", "note": "蟹笼 / 海滩觅食"},
            ], "reward": "蟹笼(3)"},
            {"name": "特色鱼类收集包", "items": [
                {"n": 1, "name": "河豚", "note": "海洋 12PM-4PM, 夏晴; 难度较高"},
                {"n": 1, "name": "鬼鱼", "note": "矿洞 全天 全季节 / 幽灵掉"},
                {"n": 1, "name": "沙鱼", "note": "沙漠池塘 6AM-8PM 全季节"},
                {"n": 1, "name": "木跃鱼", "note": "秘密森林 全天 全季节"},
            ], "reward": "海之菜肴(5)"},
        ],
    },
    {
        "room": "锅炉房",
        "area": 3,
        "aliases": ["锅炉房", "Boiler", "矿车", "铁匠", "地质", "冒险者", "矿石", "锭"],
        "unlock": "修复矿车（可快速抵达鹈鹕镇几个地点；未完成工艺室前矿车无法到采石场）",
        "reward": "维修矿车",
        "bundles": [
            {"name": "铁匠收集包", "items": [
                {"n": 1, "name": "铜锭", "note": "铜矿石 放熔炉"},
                {"n": 1, "name": "铁锭", "note": "铁矿石 放熔炉"},
                {"n": 1, "name": "金锭", "note": "黄金矿石 放熔炉"},
            ], "reward": "熔炉(1)"},
            {"name": "地质学家收集包", "items": [
                {"n": 1, "name": "石英", "note": "矿洞 觅食(全层)"},
                {"n": 1, "name": "地晶", "note": "矿洞1-39 觅食/晶洞/掘地虫"},
                {"n": 1, "name": "泪晶", "note": "矿洞40-79 觅食/冰封晶洞/沙尘恶灵"},
                {"n": 1, "name": "火水晶", "note": "矿洞80-120 觅食/巨大晶洞/万象晶石"},
            ], "reward": "万象晶球(5)"},
            {"name": "冒险者收集包", "items": [
                {"n": 99, "name": "史莱姆泥", "note": "杀史莱姆"},
                {"n": 10, "name": "蝙蝠翅膀", "note": "矿井杀蝙蝠"},
                {"n": 1, "name": "太阳精华", "note": "矿井杀幽灵/乌贼娃/金属大头, 头骨山洞杀木乃伊"},
                {"n": 1, "name": "虚空精华", "note": "矿井杀影子狂徒, 头骨山洞杀巨蛇"},
            ], "reward": "小型磁铁戒指(1)"},
        ],
    },
    {
        "room": "布告栏",
        "area": 5,
        "aliases": ["布告栏", "Bulletin", "友谊", "厨师", "染料", "地质研究", "饲料", "魔法师"],
        "unlock": "镇上每个非单身村民好感 +2 心",
        "reward": "友谊",
        "bundles": [
            {"name": "大厨收集包", "items": [
                {"n": 1, "name": "枫糖浆", "note": "树液采集器 放枫树"},
                {"n": 1, "name": "蕨菜", "note": "夏季 秘密森林 觅食"},
                {"n": 1, "name": "松露", "note": "猪"},
                {"n": 1, "name": "虞美人", "note": "夏季 农作物"},
                {"n": 1, "name": "生鱼寿司", "note": "烹饪(酱料女皇/星之果实酒吧)"},
                {"n": 1, "name": "煎鸡蛋", "note": "烹饪"},
            ], "reward": "粉红蛋糕(3)"},
            {"name": "染料收集包", "items": [
                {"n": 1, "name": "红蘑菇", "note": "矿洞 觅食 / 秘密森林 / 蘑菇洞"},
                {"n": 1, "name": "海胆", "note": "海滩 觅食(修复东断桥后)"},
                {"n": 1, "name": "向日葵", "note": "夏/秋季 农作物"},
                {"n": 1, "name": "鸭毛", "note": "鸭"},
                {"n": 1, "name": "海蓝宝石", "note": "冰封晶洞 / 矿洞箱子"},
                {"n": 1, "name": "红叶卷心菜", "note": "夏季作物(第二年起皮埃尔才卖种子, 旅行货车可买)"},
            ], "reward": "种子生产器(1)"},
            {"name": "地质研究收集包", "items": [
                {"n": 1, "name": "紫蘑菇", "note": "矿洞 / 蘑菇洞 / 森林农场秋"},
                {"n": 1, "name": "鹦鹉螺", "note": "冬季 沙滩 觅食(非化石)"},
                {"n": 1, "name": "鲢鱼", "note": "山湖和河流 全季节"},
                {"n": 1, "name": "冰封晶球", "note": "矿洞40-79层"},
            ], "reward": "回收机(1)"},
            {"name": "饲料收集包", "items": [
                {"n": 10, "name": "小麦", "note": "夏/秋季 农作物"},
                {"n": 10, "name": "干草", "note": "玛妮牧场买 / 筒仓+镰刀割草"},
                {"n": 3, "name": "苹果", "note": "秋季 苹果树"},
            ], "reward": "取暖器(1)"},
            {"name": "魔法师收集包", "items": [
                {"n": 1, "name": "橡树树脂", "note": "树液采集器 放橡树"},
                {"n": 1, "name": "果酒", "note": "小桶"},
                {"n": 1, "name": "兔子的脚", "note": "兔子 / 头骨山洞巨蛇(0.8%)"},
                {"n": 1, "name": "石榴", "note": "秋季 石榴树 / 农场山洞(果蝠)"},
            ], "reward": "金锭(5)"},
        ],
    },
    {
        "room": "地下室",
        "area": 4,   # ⚠️ 本表沿用了"地下室"这个叫法，游戏本地化名字是**金库 Vault**——是同一间
        "aliases": ["地下室", "金库", "Vault", "钱", "巴士", "沙漠", "公交", "车票"],
        "unlock": "修路 → 公交可到卡利科沙漠（共需 42,500g）",
        "reward": "修理汽车",
        "bundles": [
            {"name": "2500收集包", "items": [{"n": "2500g", "name": "钱", "note": "交2500g"}], "reward": "巧克力蛋糕(3)"},
            {"name": "5000收集包", "items": [{"n": "5000g", "name": "钱", "note": "交5000g"}], "reward": "高级肥料(30)"},
            {"name": "10000收集包", "items": [{"n": "10000g", "name": "钱", "note": "交10000g"}], "reward": "避雷针(1)"},
            {"name": "25000收集包", "items": [{"n": "25000g", "name": "钱", "note": "交25000g"}], "reward": "宝石复制机(1)"},
        ],
    },
    {
        "room": "遗失的收集包",
        "area": 6,   # ⚠️ 第 7 间，**不属于社区中心六间**——废弃Joja超市里的独立收集包（恒 2026-09-11 纠正）
        "aliases": ["遗失", "Missing", "Joja超市", "废弃超市", "电影院", "joja", "超市"],
        "unlock": "完成社区中心+剧情后，废弃的Joja超市→改造成电影院",
        "reward": "电影院",
        "bundles": [
            {"name": "遗失的收集包", "items": [
                {"n": 1, "name": "果酒", "note": "银星或更高品质, 木桶陈酿14天"},
                {"n": 1, "name": "恐龙蛋黄酱", "note": "蛋黄酱机 恐龙蛋"},
                {"n": 1, "name": "五彩碎片", "note": "挖矿"},
                {"n": 5, "name": "上古水果", "note": "金星品质"},
                {"n": 1, "name": "虚空鲑鱼", "note": "金星/铱星, 女巫沼泽钓鱼"},
                {"n": 1, "name": "鱼籽酱", "note": "罐头瓶"},
            ], "reward": "电影院"},
        ],
    },
]

# 宽泛别名表（用户指定），匹配 query → 直达某 room
ROOM_ALIASES = {
    "献祭": None, "社区": None, "社区中心": None, "收集包": None, "收集": None, "大厅": None,
    "工艺室": 0, "茶水间": 1, "鱼缸": 2, "锅炉房": 3, "布告栏": 4, "地下室": 5,
    "金库": 5, "遗失": 6, "joja": 6, "Joja": 6, "奥斯卡": 6, "超市": 6, "电影院": 6,
    "觅食": 0, "作物": 1, "农作物": 1, "钓鱼": 2, "鱼": 2, "矿车": 3, "矿石": 3,
    "钱": 5, "巴士": 5, "沙漠": 5,
}


def _fmt_item(n, name, note):
    cnt = f"×{n}" if str(n) != "1" else ""
    return f"{name}{cnt}（{note}）"


def _fmt_bundle(b):
    items = "、".join(_fmt_item(i["n"], i["name"], i["note"]) for i in b["items"])
    return f"  ▫ {b['name']}\n     要: {items}\n     给: {b['reward']}"


def search_bundles(query: str = "", room_index=None) -> str:
    """按关键词模糊查收集包。query 空 → 全房间概览；命中房间别名 → 那一间全列；
    命中收集包/物品名 → 精确到该项。返回供 AI 读的文本。"""
    q = (query or "").strip()
    if room_index is not None:
        r = BUNDLE_ROOMS[room_index]
        lines = [f"🎁 {r['room']}（{r['reward']}）", f"  💡 完成后: {r['unlock']}"]
        lines += [_fmt_bundle(b) for b in r["bundles"]]
        return "\n".join(lines)

    if not q:
        lines = ["🎁 社区中心收集包全观（共%d间）:" % len(BUNDLE_ROOMS)]
        for r in BUNDLE_ROOMS:
            lines.append(f"  ▸ {r['room']}：{len(r['bundles'])}包 · 完成后→{r['reward']}")
        lines.append("  ⭐ 想看某间明细，搜房间名（工艺室/茶水间/鱼缸/锅炉房/布告栏/地下室/遗失）。")
        return "\n".join(lines)

    # 1) 房间别名命中
    alias_hit = ROOM_ALIASES.get(q)
    if isinstance(alias_hit, int):
        return search_bundles(room_index=alias_hit)
    if q in ("献祭", "社区", "社区中心", "收集包", "收集", "大厅"):
        # 命中"全部"类的词 → 概览 + 提示
        return search_bundles() + "\n（输入具体房间名可看该间明细）"

    # 2) 收集包/物品名 子串匹配
    hits = []
    for r in BUNDLE_ROOMS:
        for b in r["bundles"]:
            if q in b["name"] or any(q in it["name"] for it in b["items"]):
                hits.append(f"【{r['room']}】\n" + _fmt_bundle(b))
    if hits:
        return "🎁 匹配到：\n" + "\n".join(hits)

    return f"❓ 没查到「{q}」。可试房间名（工艺室/茶水间/鱼缸/锅炉房/布告栏/地下室/遗失）或物品名。"


# area 号 → room 条目。给 bundle_status 用：它拿存档里游戏自己的 area 号，反查这间的奖励。
# ⚠️ 别按 `room` 中文名去查——本表把 area 4 叫"地下室"，而游戏本地化叫"金库"，按名查会撞空。
ROOMS_BY_AREA = {r["area"]: r for r in BUNDLE_ROOMS}


def room_by_area(area) -> dict:
    """按游戏 area 号取房间条目（含 reward/unlock）；查不到返回 None，**不兜底**。"""
    try:
        return ROOMS_BY_AREA.get(int(area))
    except (TypeError, ValueError):
        return None
