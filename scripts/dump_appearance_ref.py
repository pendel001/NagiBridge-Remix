#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键导出游戏里的外观参考数据。

把运行中游戏的 /appearance_ref（所有上衣 + 裤子的 ID/中文显示名/游戏描述）dump 成 JSON，
供把 MCP 的 list_shirt_ref / list_pants_ref 烤全成「中文名 + 游戏描述」。

⚠️ 前提：游戏已用「含 /appearance_ref 端点」的新 DLL 重启，且世界已加载（Context.IsWorldReady）。
用法：
    python scripts/dump_appearance_ref.py
    # 打自定义端口：NAGI_AI_URL=http://localhost:7843 python scripts/dump_appearance_ref.py
输出：scripts/appearance_ref.json
"""
import json
import os
import sys

import requests

_AI = os.environ.get("NAGI_AI_URL", "http://localhost:7843")
_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "appearance_ref.json")


def main() -> int:
    try:
        r = requests.get(f"{_AI}/appearance_ref", timeout=30)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 连不上 {_AI}/appearance_ref：{e}")
        print("  检查：游戏是否已用新 DLL 重启？世界是否已加载？端口对不对？")
        return 1

    if not data.get("ok"):
        print(f"❌ {_AI}/appearance_ref 返回错误：{data.get('error')}")
        return 1

    shirts = data.get("shirts") or []
    pants = data.get("pants") or []
    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump({"shirts": shirts, "pants": pants}, f, ensure_ascii=False, indent=2)

    print(f"✅ 导出 {len(shirts)} 件上衣 + {len(pants)} 条裤子 → {_OUT}")
    if shirts:
        print("   首件示例：", shirts[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
