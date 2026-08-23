#!/bin/bash
# 种田脚本 - 批量模式：翻地一排 → 播种一排 → 浇水一排
# 用法: bash farm.sh <起始x> <y> <长度> <种子名>
# 例如: bash farm.sh 60 18 8 "Potato Seeds"

PORT=${NAGI_PORT:-7843}
BASE="http://localhost:$PORT"
START_X=${1:-60}
Y=${2:-18}
LEN=${3:-8}
SEED=${4:-"Potato Seeds"}

END_X=$((START_X + LEN - 1))

api() {
  curl -s -X POST "$BASE/$1" -H "Content-Type: application/json" -d "$2"
  echo ""
}

# 工具使用：走到目标旁边(上方)，面朝下使用
use_on_tile() {
  local x=$1 y=$2
  local stand_y=$((y - 1))
  api move "{\"x\":$x,\"y\":$stand_y}"
  sleep 1.5
  api face '{"direction":2}'
  sleep 0.2
  api use '{}'
  sleep 0.8
}

echo "=== 第一遍：翻地 (Hoe) ==="
api select '{"name":"Hoe"}'
sleep 0.3
for ((x=START_X; x<=END_X; x++)); do
  echo "翻地 ($x,$Y)"
  use_on_tile $x $Y
done

echo ""
echo "=== 第二遍：播种 ($SEED) ==="
api select "{\"name\":\"$SEED\"}"
sleep 0.3
for ((x=START_X; x<=END_X; x++)); do
  echo "播种 ($x,$Y)"
  use_on_tile $x $Y
done

echo ""
echo "=== 第三遍：浇水 (Watering Can) ==="
api select '{"name":"Watering Can"}'
sleep 0.3
for ((x=START_X; x<=END_X; x++)); do
  echo "浇水 ($x,$Y)"
  use_on_tile $x $Y
done

echo ""
echo "=== 种田完成！==="
