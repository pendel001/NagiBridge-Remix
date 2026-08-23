#!/bin/bash
# NagiBridge Channel 一键启动 — 在CC里粘贴: ! bash ~/source/NagiBridge/scripts/start_channel.sh
# 启动channel server + 输出提示

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INBOX="$HOME/nagi/overlay_inbox.jsonl"
mkdir -p "$(dirname "$INBOX")"
touch "$INBOX"

# 杀掉所有旧的channel server
pkill -f "channel_server.py" 2>/dev/null
sleep 0.5

# 启动（不用nohup，跟随调用者生命周期）
cd "$SCRIPT_DIR"
python channel_server.py &
CHANNEL_PID=$!
sleep 1

if curl -s http://localhost:9000/ | grep -q "listening"; then
    echo "Channel server OK (:9000)"
else
    echo "Channel server FAILED"
    exit 1
fi
