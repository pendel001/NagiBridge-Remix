#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键导出游戏里「捏人菜单能选」的发型全集（顺序 = 菜单左右循环顺序）。

数据源是运行中游戏的 /hair_ref（= Farmer.GetAllHairstyleIndices()）：
返回的是「内部 farmer.hair / changeHairStyle 存的值」的有序列表（display = 位置+1）。
1.6 里通常 = [0,1,...,55] + [100,101,...,117]，共 74 款（56~99 是空号，不出现）。

⚠️ 前提：游戏已用含 /hair_ref 端点的新 DLL 重启，且世界已加载（Context.IsWorldReady）。
   先按 CHANGELOG 把 DLL 复制到 C+F 双盘并重启游戏。
用法：
    python scripts/dump_hair_ref.py            # AI 端口 7843
    python scripts/dump_hair_ref.py --port 7842
输出：stdout 对照表 + scripts/hair_ref.json（可用来回填 nagi_mcp_server.py 的 HAIR_REF）
"""
import argparse
import json
import os
import sys

import requests

_AI = os.environ.get("NAGI_AI_URL", "http://localhost:7843")
_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hair_ref.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=None, help="AI 进程端口（默认取 NAGI_AI_URL 或 7843）")
    args = ap.parse_args()
    url = os.environ.get("NAGI_AI_URL", f"http://localhost:{args.port}" if args.port else "http://localhost:7843")

    try:
        r = requests.get(f"{url}/hair_ref", timeout=30)
        data = r.json()
    except Exception as e:  # noqa: BLE001
        print(f"❌ 连不上 {url}/hair_ref：{e}")
        print("  检查：游戏是否已用含 /hair_ref 端点的 DLL 重启？世界是否已加载？端口对不对？")
        return 1

    if not data.get("ok"):
        print(f"❌ {url}/hair_ref 返回错误：{data.get('error')}")
        return 1

    hair = data.get("hair") or []
    hair_indices = data.get("hairIndices") or []
    print(f"✅ 捏人菜单可发型 {len(hair_indices)} 款（内部 farmer.hair 值）→ 相对位置 = 显示编号：")
    for i, hid in enumerate(hair_indices, 1):
        info = hair[i - 1] if i - 1 < len(hair) else {}
        bald = "🦲" if info.get("isBald") else "  "
        print(f"  {i:>3}. 显示{i:>2}号 → 内部{hid:>3} {bald} {info.get('texture') or ''}")

    with open(_OUT, "w", encoding="utf-8") as f:
        json.dump(
            {"count": len(hair_indices), "hairIndices": hair_indices, "hair": hair},
            f, ensure_ascii=False, indent=2,
        )
    print(f"\n  已存 → {_OUT}")
    print("  若与 nagi_mcp_server.py 的 HAIR_REF 不一致，按这份 JSON 的 hairIndices 回填（display=位置+1）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
