#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键导出「创建(选人)页可选中上衣」的有序列表 —— 顺序 = 创建页左右循环的"第N件"。

数据源是运行中游戏的 /appearance_creation（= Game1.player.GetValidShirtIds()，
按 Game1.shirtData 装载序、只留 CanChooseDuringCharacterCustomization 为真的那批，通常是112件）。
与 /appearance_ref(按ID排、全量301件)不同：这里就是创建页真正循环的顺序。

⚠️ 前提：游戏已用含 /appearance_creation 端点的新 DLL 重启（内容已加载即可，不用等世界加载完）。
用法：
    python scripts/dump_appearance_creation.py            # AI 端口 7843
    python scripts/dump_appearance_creation.py --port 7842
输出：scripts/appearance_creation.json（有序列表，首件=创建页第1件）
"""
import argparse
import json
import os
import sys

import requests

_AI = os.environ.get("NAGI_AI_URL", "http://localhost:7843")
_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "appearance_creation.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None, help="AI 进程端口（默认取 NAGI_AI_URL 或 7843）")
    args = ap.parse_args()
    url = os.environ.get("NAGI_AI_URL", f"http://localhost:{args.port}" if args.port else "http://localhost:7843")

    try:
        r = requests.get(f"{url}/appearance_creation", timeout=30)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 连不上 {url}/appearance_creation：{e}")
        print("  检查：游戏是否已用新 DLL 重启？/appearance_creation 端点是否已加载？")
        return 1

    if not data.get("ok"):
        print(f"❌ {url}/appearance_creation 返回错误：{data.get('error')}")
        return 1

    shirts = data.get("shirts") or []
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump({"count": len(shirts), "shirts": shirts}, f, ensure_ascii=False, indent=2)

    print(f"✅ 创建页可选中上衣 {len(shirts)} 件 → {_OUT}")
    print("   「第N件 ↔ 编号 ↔ 名」对照：")
    for i, s in enumerate(shirts, 1):
        print(f"    {i:>3}. {s['id']:<6} {s['name']}")
    print("\n   首件示例：", shirts[0] if shirts else "无")
    return 0


if __name__ == "__main__":
    sys.exit(main())
