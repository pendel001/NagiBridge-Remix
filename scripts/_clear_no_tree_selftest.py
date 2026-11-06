# -*- coding: utf-8 -*-
"""🌳 「清地不清树」钉子 —— 幼苗不许被当成"18 下的树"去砍。2026-11-06

来历（恒）：
  「你来看看现在为什么出现了**逐个格子挥斧**的情况……？**明明是干净空地了**」

🩸 病根（查证过的）：
  `/surroundings` 对树只报 `Tree:<树种>`（`ModEntry.cs:7722` = `tree.treeType.Value`），
  **从来不报生长阶段**（全仓没有一处读 `growthStage`）。而农场被砍过一轮之后遍地是**幼苗**
  （`Tree:1/2/3` 的 0~1 阶段）—— 画面上就两片小叶，肉眼就是"干净空地"。
  旧 `clear_area` 认 `Tree:` 就当树，按 `TOOL_MAP["Tree"]=18 下` 排进待办：
  一格一格走过去挥斧、每格白烧 2 点体力，**还顺手把恒要留的小树砍了**。
  真机证据（12:45 那次 `farm clear`）：`Tree at (18,21) cleared after 2/18 hits`、
  `(17,27) cleared after 1/18` —— 成树要十来下，1~2 下就倒的只可能是幼苗。

钉四条：
  ① `tile_target_name`：**任何树种**（含放行的）都不返回 `Tree`，只记账
  ② `scan_area`：一块"只有树"的地 ⇒ 待清名单**空**（= 这个 bug 回来就会红）
  ③ `run()`：**一斧都不许挥**（`api.use_tool` 零调用），且收工要报「还有 N 棵树」「farm chop」
  ④ 源码：`TOOL_MAP` 里**没有** `"Tree"` 这一条、`tile_target_name` 里**没有** `return "Tree"`
     （结构钉子 —— 防"哪天顺手加回去"）

跑法：`python scripts/_clear_no_tree_selftest.py`（退出码 0 = 全绿）
"""
import ast
import importlib
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _load(region):
    """带区域参数重新导入 `clear_area`（它在模块级就把 `_AREA` 解析好了）。"""
    sys.argv = ["clear_area.py", "--port", "0"] + [str(v) for v in region]
    mod = importlib.import_module("clear_area")
    return importlib.reload(mod)


def _stub(mod, tiles, location="Farm"):
    """把会动游戏的几样全换成桩子；`use_tool` 记账（**这就是"挥了几下斧"的计数器**）。"""
    hits = []
    logs = []
    mod.api.surroundings = lambda radius=10: {"center": {"x": 30, "y": 30},
                                             "location": location, "tiles": list(tiles)}
    mod.api._post = lambda *a, **k: {"ok": True}
    mod.api.state = lambda *a, **k: {"inventory": [], "player": {"currentTool": "Axe"}}
    mod.api.player_stamina = lambda *a, **k: (200.0, 270.0)
    mod.api._ai_get = lambda *a, **k: {"debris": []}
    mod.api.select = lambda *a, **k: None
    mod.api.face = lambda *a, **k: None
    mod.api.face_toward = lambda *a, **k: 2
    mod.api.move_to = lambda *a, **k: True
    mod.api.position = lambda *a, **k: None
    mod.api.use_tool = lambda *a, **k: hits.append(a)
    mod.api.log = lambda msg: logs.append(str(msg))
    return hits, logs


def main():
    # ── ① 判据：任何树种都不进清理名单 ──────────────────────────────────
    print("\n① `tile_target_name`：**任何树种都不动**（幼苗=成树，分不出来就不猜）")
    mod = _load((0, 0, 1, 1))
    for tt_id, why in [("1", "橡"), ("2", "枫"), ("3", "松"), ("7", "蘑菇树"),
                       ("8", "桃花心木"), ("10", "苔雨树"), ("13", "神秘树")]:
        mod._TREES_PASS.clear()
        got = mod.tile_target_name({"terrain": f"Tree:{tt_id}"})
        ck(f"Tree:{tt_id}（{why}）⇒ None", got is None, str(got))
        ck(f"…Tree:{tt_id} 记了一笔（收工要报）", mod._TREES_PASS.get(tt_id) == 1,
           str(mod._TREES_PASS))
    print("   非树的照旧：")
    for tile, want in [({"object": "Weeds"}, "Weeds"), ({"object": "Stone"}, "Stone"),
                       ({"object": "Twig"}, "Twig"), ({"terrain": "Grass"}, "Grass"),
                       ({"resource": "LargeStump"}, "LargeStump"),
                       ({"resource": "LargeLog"}, "LargeLog"),
                       ({"resource": "LargeBoulder"}, "LargeBoulder"),
                       ({"resource": "MeteoriteOre"}, "MeteoriteOre")]:
        got = mod.tile_target_name(dict(tile))
        ck(f"{tile} ⇒ {want}", got == want, str(got))

    # ── ② 一块"只有树"的地：待清名单必须是空的 ──────────────────────────
    print("\n② `scan_area`：只有树 ⇒ 待清名单**空**（这就是「逐个格子挥斧」的判据）")
    mod = _load((20, 20, 30, 30))
    tree_tiles = [{"x": 22 + i, "y": 24, "terrain": "Tree:1"} for i in range(6)]
    _stub(mod, tree_tiles)
    mod._TREES_SEEN.clear()
    got = mod.scan_area()
    ck("扫到 0 个待清目标", got == [], str(got))
    ck("但树数得出来（6 棵）", sum(mod._TREES_SEEN.values()) == 6, str(mod._TREES_SEEN))

    print("\n   混着来的地：树不算、别的照算")
    mod = _load((20, 20, 30, 30))
    mixed = tree_tiles[:2] + [
        {"x": 25, "y": 25, "object": "Weeds"},
        {"x": 26, "y": 25, "object": "Stone"},
        {"x": 27, "y": 25, "object": "Twig"},
        {"x": 28, "y": 25, "resource": "LargeStump"},
    ]
    _stub(mod, mixed)
    mod._TREES_SEEN.clear()
    got = mod.scan_area()
    names = sorted(g[3] for g in got)
    ck("三个普通目标照旧：Weeds/Stone/Twig", names == ["Stone", "Twig", "Weeds"], str(names))
    ck("两棵树仍然没进名单", "Tree" not in names, str(names))
    # 🪨 树桩在名单里**被拦在工具等级**那道（桩要铜斧，夹具里是基础斧）—— 不是"没识别"
    ck("树桩照旧被识别（只因斧头等级不够被拦）", "LargeStump" in mod._TOO_WEAK,
       str(mod._TOO_WEAK))

    # ── ③ 整跑一遍：**一斧都不许挥** + 收工要报 ────────────────────────
    print("\n③ `run()` 全跑：**零次 `use_tool`**，收工报「还有 N 棵树」+ 指路 `farm chop`")
    mod = _load((20, 20, 30, 30))
    hits, logs = _stub(mod, tree_tiles)
    mod.run()
    ck("**一斧都没挥**（use_tool 零调用）", hits == [], str(hits))
    joined = "\n".join(logs)
    ck("报了「还有 6 棵树」", "6 棵树" in joined, joined[-300:])
    ck("说清「清地不清树」", "清地不清树" in joined, joined[-300:])
    ck("指路 `farm chop`", "farm chop" in joined, joined[-300:])
    ck("没谎报 All clear（有树就不算干净）", "All clear" not in joined, joined[-300:])

    # ── ④ 结构钉子 ─────────────────────────────────────────────────
    print("\n④ 源码结构：不许把树偷偷加回清理名单")
    mod = _load((0, 0, 1, 1))
    ck("`TOOL_MAP` 里没有 `Tree` 这一条", "Tree" not in mod.TOOL_MAP, str(mod.TOOL_MAP.keys()))
    src = io.open(os.path.join(HERE, "clear_area.py"), encoding="utf-8", errors="replace").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "tile_target_name")
    rets = [n.value.value for n in ast.walk(fn)
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)]
    ck("`tile_target_name` 里没有 `return \"Tree\"`", "Tree" not in rets, str(rets))

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ 树（尤其幼苗）被当成「18 下的目标」去砍 = 一格一格挥斧、白烧体力、砍掉要留的小树。")
        sys.exit(1)
    print("✅ 清地不清树：树一棵不动、账照记、要砍走 chop —— 幼苗不会再骗出一地斧印。")
    sys.exit(0)


if __name__ == "__main__":
    main()
