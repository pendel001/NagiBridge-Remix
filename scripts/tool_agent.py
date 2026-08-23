"""
NagiBridge Tool Agent — LLM + tool calling to control Stardew Valley

Supports: Claude API, DeepSeek, OpenAI-compatible APIs
Uses NagiBridge HTTP API as tools, so the AI can play the game.

Usage:
    python tool_agent.py --provider deepseek --key sk-xxx --port 7842
    python tool_agent.py --provider claude --key sk-ant-xxx
    python tool_agent.py --provider openai --key sk-xxx --model gpt-4o

Environment variables (alternative to args):
    NAGI_API_KEY, NAGI_API_PROVIDER, NAGI_API_MODEL
"""

import argparse
import json
import os
import sys
import subprocess
import requests

parser = argparse.ArgumentParser(description="NagiBridge Tool Agent")
parser.add_argument("--provider", default=os.environ.get("NAGI_API_PROVIDER", "deepseek"),
                    choices=["claude", "deepseek", "openai"])
parser.add_argument("--key", default=os.environ.get("NAGI_API_KEY", ""))
parser.add_argument("--model", default=os.environ.get("NAGI_API_MODEL", ""))
parser.add_argument("--port", type=int, default=7842)
parser.add_argument("--max-turns", type=int, default=10)
args = parser.parse_args()

if not args.key:
    print("Error: API key required (--key or NAGI_API_KEY)")
    sys.exit(1)

GAME_URL = f"http://localhost:{args.port}"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_MODELS = {
    "claude": "claude-sonnet-4-6-20250514",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o",
}
MODEL = args.model or DEFAULT_MODELS.get(args.provider, "deepseek-chat")

SYSTEM_PROMPT = """You are an AI companion playing Stardew Valley through the NagiBridge mod.
You can see the game state and control the farmer using tools.
When the player asks you to do something, use tools to accomplish it.
Always check the current state first before acting.
Keep chat responses short (1-2 sentences). Report what you did after completing a task.
The player's language is Chinese — respond in Chinese."""

# ── Tool Definitions ──

TOOLS = [
    {
        "name": "get_state",
        "description": "Get current game state: player position, health, stamina, time, location, inventory, active menu/event",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_surroundings",
        "description": "Scan surrounding tiles for objects, NPCs, monsters, crops. Returns tiles with passability, objects, terrain, crops.",
        "parameters": {
            "type": "object",
            "properties": {"radius": {"type": "integer", "description": "Scan radius (default 10)"}},
            "required": [],
        },
    },
    {
        "name": "warp",
        "description": "Teleport to a location. Common: Farm, Town, Beach, Mountain, Forest, Mine, BusStop, Desert",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {"type": "string"},
                "x": {"type": "integer", "description": "Optional tile X"},
                "y": {"type": "integer", "description": "Optional tile Y"},
            },
            "required": ["location"],
        },
    },
    {
        "name": "move_to",
        "description": "Pathfind and walk to a tile position",
        "parameters": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "use_tool",
        "description": "Swing a tool (Axe, Pickaxe, Hoe, Watering Can, etc.) or 'current'",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "default": "current"}},
            "required": [],
        },
    },
    {
        "name": "select_item",
        "description": "Select an inventory item by name (tool, seed, crop, etc.)",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "interact",
        "description": "Interact with the object/NPC in front of the player",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "face",
        "description": "Set facing direction: 0=up, 1=right, 2=down, 3=left",
        "parameters": {
            "type": "object",
            "properties": {"direction": {"type": "integer", "enum": [0, 1, 2, 3]}},
            "required": ["direction"],
        },
    },
    {
        "name": "use_item",
        "description": "Use/place the currently held item (seeds, objects, etc.)",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "press_key",
        "description": "Simulate a key press. Keys: confirm, cancel, skip, ok, F1-F12",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "count": {"type": "integer", "default": 1},
            },
            "required": ["key"],
        },
    },
    {
        "name": "run_script",
        "description": "Run a farming automation script. Available: farm_row, water_crops, harvest, mine_run, chop_trees, clear_area, pet_animals, keg_manager, furnace_manager",
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Script name without .py"},
                "args": {"type": "string", "description": "Command line arguments as a string"},
            },
            "required": ["script"],
        },
    },
    {
        "name": "craft",
        "description": "Craft an item if you have the recipe and materials",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer", "default": 1},
            },
            "required": ["name"],
        },
    },
    {
        "name": "sell",
        "description": "Sell items via the shipping bin. Must be on the Farm.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Item name, or omit to sell all non-tool items"},
            },
            "required": [],
        },
    },
    {
        "name": "sleep",
        "description": "Go to bed and end the day",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_machines",
        "description": "Scan all machines in current location (kegs, furnaces, etc.) with their status",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_animals",
        "description": "Get all farm animals: pet status, friendship, happiness, product readiness",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "give_item",
        "description": "Cheat: spawn an item into inventory. Use item ID or (W)ID for weapons.",
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "count": {"type": "integer", "default": 1},
            },
            "required": ["id"],
        },
    },
    {
        "name": "heal",
        "description": "Cheat: fully restore health and stamina",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


# ── Tool Execution ──

def game_api(method, endpoint, data=None, params=None):
    try:
        if method == "GET":
            r = requests.get(f"{GAME_URL}{endpoint}", params=params, timeout=10)
        else:
            r = requests.post(f"{GAME_URL}{endpoint}", json=data or {}, timeout=30)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def execute_tool(name, arguments):
    match name:
        case "get_state":
            return game_api("GET", "/state")
        case "get_surroundings":
            return game_api("GET", "/surroundings", params={"radius": arguments.get("radius", 10)})
        case "warp":
            return game_api("POST", "/warp", arguments)
        case "move_to":
            result = game_api("POST", "/move", arguments)
            import time; time.sleep(2)
            return game_api("GET", "/state")
        case "use_tool":
            return game_api("POST", "/tool", {"name": arguments.get("name", "current")})
        case "select_item":
            return game_api("POST", "/select", {"name": arguments["name"]})
        case "interact":
            return game_api("POST", "/interact")
        case "face":
            return game_api("POST", "/face", {"direction": arguments["direction"]})
        case "use_item":
            return game_api("POST", "/use")
        case "press_key":
            return game_api("POST", "/key", {"key": arguments["key"], "count": arguments.get("count", 1)})
        case "craft":
            return game_api("POST", "/craft", {"name": arguments["name"], "count": arguments.get("count", 1)})
        case "sell":
            data = {}
            if "name" in arguments:
                data["name"] = arguments["name"]
            else:
                data["all"] = True
            return game_api("POST", "/sell", data)
        case "sleep":
            return game_api("POST", "/sleep")
        case "get_machines":
            return game_api("GET", "/machines")
        case "get_animals":
            return game_api("GET", "/animals")
        case "give_item":
            return game_api("POST", "/give", {"id": arguments["id"], "count": arguments.get("count", 1)})
        case "heal":
            return game_api("POST", "/heal")
        case "run_script":
            script = arguments["script"]
            script_path = os.path.join(SCRIPT_DIR, f"{script}.py")
            if not os.path.exists(script_path):
                return {"error": f"Script not found: {script}.py"}
            cmd = f"python \"{script_path}\" {arguments.get('args', '')} --port {args.port}"
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                        timeout=300, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                return {"stdout": result.stdout[-500:] if result.stdout else "", "returncode": result.returncode}
            except subprocess.TimeoutExpired:
                return {"error": "Script timed out (5min)"}
        case _:
            return {"error": f"Unknown tool: {name}"}


# ── API Callers ──

def convert_tools_openai():
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in TOOLS]


def convert_tools_claude():
    return [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in TOOLS]


def call_openai_compatible(messages, endpoint, auth_header):
    body = {
        "model": MODEL,
        "max_tokens": 1024,
        "messages": messages,
        "tools": convert_tools_openai(),
    }
    headers = {"Content-Type": "application/json", **auth_header}
    r = requests.post(endpoint, json=body, headers=headers, timeout=60)
    if r.status_code != 200:
        print(f"API error {r.status_code}: {r.text[:200]}")
        return None, []
    data = r.json()
    choice = data["choices"][0]
    msg = choice["message"]
    text = msg.get("content", "")
    tool_calls = []
    for tc in msg.get("tool_calls", []):
        tool_calls.append({
            "id": tc["id"],
            "name": tc["function"]["name"],
            "arguments": json.loads(tc["function"]["arguments"]),
        })
    return text, tool_calls


def call_claude(messages):
    system = SYSTEM_PROMPT
    body = {
        "model": MODEL,
        "max_tokens": 1024,
        "system": system,
        "messages": messages,
        "tools": convert_tools_claude(),
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": args.key,
        "anthropic-version": "2023-06-01",
    }
    r = requests.post("https://api.anthropic.com/v1/messages", json=body, headers=headers, timeout=60)
    if r.status_code != 200:
        print(f"API error {r.status_code}: {r.text[:200]}")
        return None, []
    data = r.json()
    text = ""
    tool_calls = []
    for block in data.get("content", []):
        if block["type"] == "text":
            text += block["text"]
        elif block["type"] == "tool_use":
            tool_calls.append({
                "id": block["id"],
                "name": block["name"],
                "arguments": block["input"],
            })
    return text, tool_calls


def call_api(messages):
    if args.provider == "claude":
        return call_claude(messages)
    elif args.provider == "deepseek":
        return call_openai_compatible(
            messages, "https://api.deepseek.com/v1/chat/completions",
            {"Authorization": f"Bearer {args.key}"})
    else:
        return call_openai_compatible(
            messages, "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {args.key}"})


# ── Chat Loop ──

def build_messages_openai(history):
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.extend(history)
    return msgs


def build_messages_claude(history):
    return [m for m in history if m["role"] != "system"]


def run():
    print(f"NagiBridge Tool Agent | provider={args.provider} model={MODEL} port={args.port}")
    print(f"Type your message. The AI will use tools to play the game.")
    print(f"Commands: /quit, /state, /clear\n")

    history = []

    while True:
        try:
            user_input = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input == "/quit":
            break
        if user_input == "/state":
            s = game_api("GET", "/state")
            print(json.dumps(s, indent=2, ensure_ascii=False))
            continue
        if user_input == "/clear":
            history.clear()
            print("History cleared.")
            continue

        history.append({"role": "user", "content": user_input})

        for turn in range(args.max_turns):
            if args.provider == "claude":
                messages = build_messages_claude(history)
            else:
                messages = build_messages_openai(history)

            text, tool_calls = call_api(messages)

            if text:
                print(f"AI > {text}")

            if not tool_calls:
                if text:
                    history.append({"role": "assistant", "content": text})
                break

            # Handle tool calls
            if args.provider == "claude":
                content_blocks = []
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for tc in tool_calls:
                    content_blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["arguments"]})
                history.append({"role": "assistant", "content": content_blocks})

                for tc in tool_calls:
                    print(f"  [tool] {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})")
                    result = execute_tool(tc["name"], tc["arguments"])
                    result_str = json.dumps(result, ensure_ascii=False)
                    if len(result_str) > 2000:
                        result_str = result_str[:2000] + "...(truncated)"
                    print(f"  [result] {result_str[:200]}")
                    history.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tc["id"], "content": result_str}]})
            else:
                assistant_msg = {"role": "assistant", "content": text or ""}
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                        for tc in tool_calls
                    ]
                history.append(assistant_msg)

                for tc in tool_calls:
                    print(f"  [tool] {tc['name']}({json.dumps(tc['arguments'], ensure_ascii=False)})")
                    result = execute_tool(tc["name"], tc["arguments"])
                    result_str = json.dumps(result, ensure_ascii=False)
                    if len(result_str) > 2000:
                        result_str = result_str[:2000] + "...(truncated)"
                    print(f"  [result] {result_str[:200]}")
                    history.append({"role": "tool", "tool_call_id": tc["id"], "content": result_str})

        # Trim history
        if len(history) > 40:
            history = history[-30:]


if __name__ == "__main__":
    run()
