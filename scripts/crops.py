"""
🌱 crops.py — 作物知识库（crop ID → 名字/收获方式/再生/阶段说明）

/surroundings 只给 crop 的 indexOfHarvest（数字 ID），脚本看不懂。
这个表让 AI 知道：这格是什么作物、是不是镰刀收、会不会再生、长到哪阶段了。

用法:
  from crops import crop_info, is_scythe_crop, crop_phase_text
  info = crop_info(262)          # {'id':262, 'name':'Wheat', 'scythe':True, 'regrow':0}
  is_scythe_crop(262)            # True
  crop_phase_text(262, 4, True)  # '小麦 已成熟（可收）'
"""

# crop harvest ID → 作物信息
# scythe=True 表示要用镰刀收；regrow 是再生长天数（0=一次性）；water 表示水边/水里种植
# trellis=True 表示爬架作物（不可通过格：啤酒花/青豆/葡萄）——种植要留走道（种2留1）
CROPS = {
    # ── 镰刀作物 ──
    262:  {"name": "Wheat",         "zh": "小麦",     "scythe": True,  "regrow": 0, "water": False},
    271:  {"name": "Rice",          "zh": "水稻",     "scythe": True,  "regrow": 0, "water": True},
    300:  {"name": "Amaranth",      "zh": "苋菜",     "scythe": True,  "regrow": 0, "water": False},
    771:  {"name": "Fiber",         "zh": "纤维",     "scythe": True,  "regrow": 0, "water": False},
    830:  {"name": "Taro Root",     "zh": "芋头",     "scythe": True,  "regrow": 0, "water": True},
    # ── 常见手摘作物（日常会遇到） ──
    16:   {"name": "Wild Horseradish", "zh": "野山葵", "scythe": False, "regrow": 0, "water": False},
    20:   {"name": "Kale",          "zh": "甘蓝",     "scythe": False, "regrow": 0, "water": False},
    24:   {"name": "Parsnip",       "zh": "防风草",   "scythe": False, "regrow": 0, "water": False},
    188:  {"name": "Green Bean",    "zh": "青豆",     "scythe": False, "regrow": 3, "water": False, "trellis": True},
    190:  {"name": "Cauliflower",   "zh": "花椰菜",   "scythe": False, "regrow": 0, "water": False},
    192:  {"name": "Potato",        "zh": "土豆",     "scythe": False, "regrow": 0, "water": False},
    248:  {"name": "Garlic",        "zh": "大蒜",     "scythe": False, "regrow": 0, "water": False},
    254:  {"name": "Melon",         "zh": "西瓜",     "scythe": False, "regrow": 0, "water": False},
    270:  {"name": "Corn",          "zh": "玉米",     "scythe": False, "regrow": 0, "water": False},
    272:  {"name": "Eggplant",      "zh": "茄子",     "scythe": False, "regrow": 5, "water": False},
    276:  {"name": "Pumpkin",       "zh": "南瓜",     "scythe": False, "regrow": 0, "water": False},
    282:  {"name": "Strawberry",    "zh": "草莓",     "scythe": False, "regrow": 4, "water": False},
    284:  {"name": "Tomato",        "zh": "番茄",     "scythe": False, "regrow": 4, "water": False},
    296:  {"name": "Hot Pepper",    "zh": "辣椒",     "scythe": False, "regrow": 3, "water": False},
    304:  {"name": "Hops",          "zh": "啤酒花",   "scythe": False, "regrow": 1, "water": False, "trellis": True},
    306:  {"name": "Rhubarb",       "zh": "大黄",     "scythe": False, "regrow": 0, "water": False},
    308:  {"name": "Grape",         "zh": "葡萄",     "scythe": False, "regrow": 3, "water": False, "trellis": True},
    376:  {"name": "Poppy",         "zh": "虞美人",   "scythe": False, "regrow": 0, "water": False},
    378:  {"name": "Spangle",       "zh": "玫瑰仙子", "scythe": False, "regrow": 0, "water": False},
    398:  {"name": "Sunflower",     "zh": "向日葵",   "scythe": False, "regrow": 0, "water": False},
    400:  {"name": "Blue Jazz",     "zh": "蓝爵",     "scythe": False, "regrow": 0, "water": False},
    402:  {"name": "Amaranth",      "zh": "苋菜",     "scythe": True,  "regrow": 0, "water": False},  # 防御
    418:  {"name": "Cranberries",   "zh": "蔓越莓",   "scythe": False, "regrow": 5, "water": False},
    420:  {"name": "Salmonberry",   "zh": "美洲大树莓", "scythe": False, "regrow": 0, "water": False},
    421:  {"name": "Blackberry",    "zh": "黑莓",     "scythe": False, "regrow": 0, "water": False},
    454:  {"name": "Starfruit",     "zh": "杨桃",     "scythe": False, "regrow": 0, "water": False},
    591:  {"name": "Tulip",         "zh": "郁金香",   "scythe": False, "regrow": 0, "water": False},
    593:  {"name": "Fairy Rose",    "zh": "玫瑰仙子", "scythe": False, "regrow": 0, "water": False},
    597:  {"name": "Blue Jazz",     "zh": "蓝爵",     "scythe": False, "regrow": 0, "water": False},
    600:  {"name": "Oak Resin",     "zh": "橡树树脂", "scythe": False, "regrow": 0, "water": False},
    830:  {"name": "Taro Root",     "zh": "芋头",     "scythe": True,  "regrow": 0, "water": True},
    832:  {"name": "Pineapple",     "zh": "菠萝",     "scythe": False, "regrow": 7, "water": False},
    833:  {"name": "Ginger",        "zh": "姜",       "scythe": False, "regrow": 0, "water": False},
    885:  {"name": "Qi Fruit",      "zh": "齐先生水果", "scythe": False, "regrow": 0, "water": False},
    889:  {"name": "Sweet Gem Berry","zh": "甜瓜",    "scythe": False, "regrow": 0, "water": False},
}


def crop_info(crop_id):
    """crop 收获 ID → {id, name, zh, scythe, regrow, water, trellis}；未知返回带 ID 的占位。"""
    base = CROPS.get(int(crop_id)) if crop_id is not None else None
    if base:
        return {"id": crop_id, **base}
    return {"id": crop_id, "name": f"crop#{crop_id}", "zh": f"作物#{crop_id}",
            "scythe": False, "regrow": 0, "water": False, "trellis": False}


def is_scythe_crop(crop_id):
    """是不是镰刀作物（小麦/水稻/芋头/苋菜/纤维等）"""
    base = CROPS.get(int(crop_id)) if crop_id is not None else None
    return bool(base and base.get("scythe"))


def is_trellis_crop(crop_id):
    """是不是爬架作物（啤酒花/青豆/葡萄——不可通过格，种植要留走道）"""
    base = CROPS.get(int(crop_id)) if crop_id is not None else None
    return bool(base and base.get("trellis"))


def crop_phase_text(crop_id, phase, harvestable):
    """把 crop 阶段变成人话：'小麦 已成熟（镰刀收）' / '水稻 生长期 3/5'"""
    info = crop_info(crop_id)
    if harvestable:
        how = "镰刀收" if info["scythe"] else "直接收"
        return f"{info['zh']} 已成熟（{how}）"
    if info["regrow"] and phase > 0 and info["name"] != "Starfruit":
        return f"{info['zh']} 再生中"
    return f"{info['zh']} 生长期 {phase}"
