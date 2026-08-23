# -*- coding: utf-8 -*-
"""从 scripts/appearance_ref.json 重新生成 scripts/appearance_ref_data.py。

appearance_ref_data.py 标注"自动生成勿手改"——游戏更新 / 加内容包后，用
    python scripts/dump_appearance_ref.py     # 重新 dump（需新 DLL + 世界加载完）
    python scripts/gen_appearance_ref_data.py # 重新烤成数据模块
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "appearance_ref.json")
OUT = os.path.join(HERE, "appearance_ref_data.py")


def clean(desc: str) -> str:
    """清理游戏工具提示里的换行：\r\r\n=段落分隔 → '；'，段落内 \r\n 折行 → 去掉。"""
    paras = re.split(r"\r\r\n", desc or "")
    paras = [re.sub(r"[\r\n]+", "", p).strip() for p in paras]
    return "；".join(p for p in paras if p)


def lit(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def emit(items, name: str) -> str:
    lines = [f"{name} = ["]
    for _, nm, desc in items:
        lines.append(f'    ({_}, "{lit(nm)}", "{lit(clean(desc))}"),')
    lines.append("]")
    return "\n".join(lines)


def main() -> int:
    if not os.path.exists(SRC):
        print(f"❌ 缺 {SRC}，先跑 scripts/dump_appearance_ref.py")
        return 1
    d = json.load(open(SRC, encoding="utf-8"))
    sh = [(s["id"], s["name"], s["description"]) for s in d["shirts"]]
    pa = [(p["id"], p["name"], p["description"]) for p in d["pants"]]
    body = (
        "# -*- coding: utf-8 -*-\n"
        '"""🤖 外观参考数据（自动生成，勿手改）：从运行中游戏 /appearance_ref dump。\n\n'
        "SHIRT_REF 上衣 + PANTS_REF 裤子，每项 = (id, 中文显示名, 游戏描述)。\n"
        '描述已清理换行符(段落用"；"连接)。中文名/描述来自游戏本地化(中文版 DisplayName + getDescription)，非反编译。\n"""\n\n'
        + emit(sh, "SHIRT_REF") + "\n\n" + emit(pa, "PANTS_REF") + "\n"
    )
    io_open = open(OUT, "w", encoding="utf-8")
    io_open.write(body)
    io_open.close()
    print(f"[OK] regenerated {OUT}: SHIRT_REF {len(sh)} shirts, PANTS_REF {len(pa)} pants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
