# NagiBridge Skills

skill = 规范 + 脚本。规范告诉AI怎么规划，脚本负责执行。
所有脚本统一站在目标格上方(y-1)面朝下操作，蛇形走位。

---

## clear — 开垦

扫描区域，按工具分组清障碍物（杂草→镰刀，石头→镐，树枝/树→斧）

### 规划
1. 截图 + `/surroundings` 看区域障碍物分布
2. 确定清除范围 (x1,y1)-(x2,y2)

### 执行
```bash
PYTHONIOENCODING=utf-8 python3 clear_area.py <x1> <y1> <x2> <y2> --port 7843
```

---

## farm — 种田

翻地 → 播种 → 浇水，蛇形走位，带体力/水量检测

### 规划
1. 截图 + `/surroundings` 筛选 diggable=true 且无障碍的连续空格
2. 确定起点、长度、方向、排数、种子

### 执行
```bash
PYTHONIOENCODING=utf-8 python3 farm_row.py <x> <y> <len> \
  --seed "Parsnip Seeds" --dir right --rows 5 --port 7843
```

### 安全机制
- 体力 < 15% 自动停止
- 浇水壶空了自动去水塘补水（需新DLL）
- 蛇形走位：奇数排反转方向

---

## harvest — 收割+出售

扫描成熟作物 → 收割 → （可选）送shipping bin出售

### 规划
1. `/surroundings` 查 harvestable=true 的格子
2. 有就收，没有就等

### 执行
```bash
# 只收割
PYTHONIOENCODING=utf-8 python3 harvest.py <x1> <y1> <x2> <y2> --port 7843

# 收割+出售
PYTHONIOENCODING=utf-8 python3 harvest.py <x1> <y1> <x2> <y2> --sell --port 7843
```

---

## 完整农业循环

1. `clear_area.py` 开垦
2. `farm_row.py` 种田
3. 等作物成熟（防风草4天，土豆6天）
4. `harvest.py --sell` 收割出售
5. 回到步骤2，用赚的钱买更多种子

---

## mine — 挖矿

warp传送进矿洞 → 逐层扫描敲石头 → 传送下一层 → 低血量/体力不足自动撤退回农场
战斗由 AutoCombat mod 自动处理，脚本不需要管怪物。

### 执行
```bash
PYTHONIOENCODING=utf-8 python3 mine_run.py --start-level 1 --max-levels 5 --hp-threshold 30 --port 7842
```

### 参数
- `--start-level` 从第几层开始（默认1）
- `--max-levels` 最多挖几层（默认5）
- `--hp-threshold` 血量百分比低于此值时warp回农场（默认30）
- `--port` NagiBridge端口

### 流程
1. warp 到 `UndergroundMine{N}` 直接传送到指定层
2. 扫描周围矿石/石头 → 走过去用镐敲（每个3下）
3. 当前层没石头了 → warp 下一层
4. 血量 < 阈值 或 体力 < 15 → warp 回 Farm

### 注意
- 不需要手动走到矿洞入口，全程warp传送
- AutoCombat mod 会自动处理怪物战斗
- 挖矿前确保背包有镐子且有空位装矿石
