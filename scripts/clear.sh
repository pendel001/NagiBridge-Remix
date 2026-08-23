#!/bin/bash
# 开垦脚本 - 清除指定区域内的障碍物
# 用法: bash clear.sh <起始x> <起始y> <结束x> <结束y>
# 例如: bash clear.sh 58 20 67 25

PORT=${NAGI_PORT:-7843}
BASE="http://localhost:$PORT"
X1=${1:-58}
Y1=${2:-20}
X2=${3:-67}
Y2=${4:-25}
RADIUS=${5:-15}

echo "=== 扫描区域 ($X1,$Y1) - ($X2,$Y2) ==="

# 先走到区域中间
MID_X=$(( (X1 + X2) / 2 ))
MID_Y=$(( (Y1 + Y2) / 2 ))
curl -s -X POST "$BASE/move" -H "Content-Type: application/json" -d "{\"x\":$MID_X,\"y\":$MID_Y}"
echo ""
sleep 2

# 扫描周围
SCAN=$(curl -s "$BASE/surroundings?radius=$RADIUS")

# 解析障碍物并清除
# Weeds/Grass → Scythe, Stone → Pickaxe, Twig → Axe, Tree → Axe
echo "$SCAN" | python3 -c "
import sys, json

data = json.load(sys.stdin)
tiles = data.get('tiles', [])

targets = []
for t in tiles:
    x, y = t['x'], t['y']
    if x < $X1 or x > $X2 or y < $Y1 or y > $Y2:
        continue

    obj = t.get('object', '')
    terrain = t.get('terrain', '')
    resource = t.get('resource', '')

    if obj in ('Weeds', 'Grass'):
        targets.append((x, y, 'Scythe', obj))
    elif obj == 'Stone':
        targets.append((x, y, 'Pickaxe', obj))
    elif obj in ('Twig', 'twig'):
        targets.append((x, y, 'Axe', obj))
    elif terrain == 'Tree':
        targets.append((x, y, 'Axe', 'Tree'))
    elif terrain == 'Grass':
        targets.append((x, y, 'Scythe', 'Grass'))
    elif resource:
        targets.append((x, y, 'Axe', resource))

# 按工具分组输出，减少切换
from collections import defaultdict
by_tool = defaultdict(list)
for x, y, tool, name in targets:
    by_tool[tool].append((x, y, name))

for tool, items in by_tool.items():
    for x, y, name in items:
        print(f'{x},{y},{tool},{name}')
" 2>/dev/null | while IFS=',' read -r tx ty tool name; do
  echo "--- 清除 ($tx,$ty) $name 用 $tool ---"

  # 选工具
  curl -s -X POST "$BASE/select" -H "Content-Type: application/json" -d "{\"name\":\"$tool\"}" > /dev/null
  sleep 0.2

  # 站到障碍物上方，面朝下敲
  STAND_Y=$((ty - 1))
  curl -s -X POST "$BASE/move" -H "Content-Type: application/json" -d "{\"x\":$tx,\"y\":$STAND_Y}" > /dev/null
  sleep 1.5
  curl -s -X POST "$BASE/face" -H "Content-Type: application/json" -d '{"direction":2}' > /dev/null
  sleep 0.2
  curl -s -X POST "$BASE/use" -H "Content-Type: application/json" -d '{}' > /dev/null
  sleep 0.8

  # 石头可能要敲两下
  if [ "$tool" = "Pickaxe" ] || [ "$tool" = "Axe" ]; then
    curl -s -X POST "$BASE/use" -H "Content-Type: application/json" -d '{}' > /dev/null
    sleep 0.8
  fi
done

echo ""
echo "=== 开垦完成！==="
