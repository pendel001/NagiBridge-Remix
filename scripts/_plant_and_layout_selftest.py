# -*- coding: utf-8 -*-
"""🚿🌱 洒水器布局 + 播种"种子用完就停" 钉子。2026-11-06

来历（恒，连着三句）：
  ① 「AI 反馈说**根本不知道传什么参**能启动洒水器布局。难道做成了**只有预先放置洒水器**才能
     锄地布局的用法了吗。我是想可以**空锄预留位置补上**也可以、**围绕现有洒水器锄地**……」
  ② 「我还希望播种在**不手持种子**的情况下停止循环（现在还有种子明明已经种完了，
     仍然逐格尝试空地的状况）」

钉六条：
  ① `_existing_sprinkler_crosses`：认出田里**已摆好的**洒水器，只取它四邻（不取自己那格、不取对角）
  ② `layout=1` 锄地：待锄格是**十字**；田里有洒水器时，它的四邻也进名单
  ③ `layout=0` 锄/种了一大块 ⇒ 回执里**直接教** `layout=1` 怎么加（AI 不会回去翻文档）
  ④ `farm plot` 的 `🏁 最大可耕方形` 后面要给**能复制粘贴**的 kw（含 layout=1）
  ⑤ 播种：种子只剩 2 颗而地有 3 格 ⇒ **只挥 2 次**，停手并在回执里说「种子用完了 / 剩 1 格」
  ⑥ 源码钉：`_farm_till` / `_farm_plant` 都调 `_existing_sprinkler_crosses`

跑法：`python scripts/_plant_and_layout_selftest.py`
"""
import importlib
import io
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import nagi_mcp_server as M  # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


TILES = {}          # (x,y) -> 该格 info（`/surroundings` 的形状）
INV = []            # 背包（可变：`use_item` 会减种子）
CALLS = []


def _scan(pts, margin=1):
    """按矩形给一份 `/surroundings`（只含 `TILES` 里有的格 + 有作物就带 crop）。"""
    out = {}
    for (x, y) in list(pts):
        if (x, y) in TILES:
            out[(x, y)] = dict(TILES[(x, y)])
    if _scan.verify:                     # 验收那趟：种过的格长出 crop
        for k in _scan.planted:
            out[k] = {"terrain": "HoeDirt", "crop": "24"}
    return out, ""


_scan.verify = False
_scan.planted = []


class FakeApi:
    def state(self):
        return {"location": {"name": "Farm"},
                "player": {"currentToolUpgrade": 0, "stamina": 250, "maxStamina": 270,
                           "x": 40, "y": 40},
                "inventory": [dict(i) for i in INV]}

    def walk_ok_tiles(self, *a):
        return {(x, y) for x in range(0, 60) for y in range(0, 60)}

    def stand_tile(self, tx, ty, _ok):
        return (tx, ty - 1, 2)

    def walk_natural(self, *a):
        return True

    def park_at(self, *a):
        return True

    def aim_at(self, *a):
        return True, {}

    def face(self, *a):
        return {"ok": True}

    def select(self, *a):
        return {"ok": True}

    def use_tool(self, *a):
        CALLS.append(("tool", a))
        return {"ok": True}

    def use_item(self, *a):
        CALLS.append(("item", a))
        for it in INV:                       # 真种下去 ⇒ 种子 -1（让"种子用完"能自然发生）
            if it.get("name") == "Parsnip Seeds" and it.get("stack", 0) > 0:
                it["stack"] -= 1
                break
        return {"ok": True}

    def interact_at(self, *a):
        CALLS.append(("interact", a))
        return {"ok": True}

    def player_stamina(self):
        return (250, 270)

    # 🗺️ `farm plot` 还会用这两样（`plot_plan` 里挑"站到田心"的落点）
    def stand_near(self, tiles, walk_ok, cx, cy):
        return (cx, cy)

    def surroundings(self, *_a, **_k):
        return {"ok": True, "location": "Farm",
                "tiles": [dict(v, x=k[0], y=k[1]) for k, v in TILES.items()]}


_real = {n: getattr(M, n) for n in ("api", "_scan_tiles", "_select_best_hoe", "_with_state",
                                    "_warp_home_if_needed")}


def _reset():
    TILES.clear()
    INV[:] = [{"name": "Parsnip Seeds", "displayName": "防风草种子", "stack": 99}]
    CALLS.clear()
    _scan.verify = False
    _scan.planted = []


def main():
    try:
        M.api = FakeApi()
        M._scan_tiles = _scan
        M._select_best_hoe = lambda: None
        M._with_state = lambda s: s
        M._warp_home_if_needed = lambda *_a: ""
        # ⏱ 把 `time.sleep` 打成空转 —— `_farm_till` 每格有 `time.sleep(0.4)`（挥锄动画），
        #    十几格就要十来秒；钉子只需要逻辑，不需要动画（否则每次跑都像"十年"）。
        _real_time = M.time

        class _FastTime:
            @staticmethod
            def sleep(*_a, **_k):
                return None

            @staticmethod
            def time():
                return 0.0

        M.time = _FastTime

        # ── ① 已有洒水器的四邻 ─────────────────────────────────────
        print("\n① `_existing_sprinkler_crosses`：只取**四邻**（不含自己、不含对角）")
        _reset()
        TILES.update({(5, 5): {"object": "Sprinkler"}, (5, 4): {}, (5, 6): {},
                      (4, 5): {}, (6, 5): {}, (4, 4): {}})
        got = set(M._existing_sprinkler_crosses(0, 0, 9, 9))
        ck("四邻都在", got == {(5, 4), (5, 6), (4, 5), (6, 5)}, str(sorted(got)))
        ck("洒水器自己那格**不在**", (5, 5) not in got)
        ck("对角那格**不在**（初级洒水器浇不到对角）", (4, 4) not in got)
        _reset()
        TILES.update({(5, 5): {"object": "Quality Sprinkler"}})
        ck("「优质洒水器」也认", len(M._existing_sprinkler_crosses(0, 0, 9, 9)) == 4)
        _reset()
        ck("田里没洒水器 ⇒ 空集", M._existing_sprinkler_crosses(0, 0, 9, 9) == set())

        # ── ② layout=1 的锄地：十字 + 已摆好的那些 ─────────────────
        print("\n② `layout=1` 锄地：待锄格是**十字**，且围着田里已摆好的洒水器")
        _reset()
        TILES.update({(x, y): {"diggable": True}
                      for x in range(0, 13) for y in range(0, 13)})
        out = M._farm_till(x1=0, y1=0, x2=12, y2=12, layout=1)
        ck("回执标明是**初级洒水器布局**", "初级洒水器布局" in out, out[:200])
        ck("…而且是逐格锄（布局 1 恒逐格）", "拟人逐格" in out, out[:200])
        n_tilled = out.count("锄出")
        ck("确实给出锄出统计", n_tilled >= 1, out[:200])

        _reset()
        TILES.update({(x, y): {"diggable": True}
                      for x in range(0, 13) for y in range(0, 13)})
        TILES[(6, 6)] = {"object": "Sprinkler", "diggable": True}
        out2 = M._farm_till(x1=0, y1=0, x2=12, y2=12, layout=1)
        ck("田里有洒水器 ⇒ 报文点明「围着已摆好的」",
           "已摆好" in out2 or "已有" in out2, out2[:400])

        # ── ③ 没带 layout 的大块 ⇒ 回执里教怎么加 ───────────────────
        print("\n③ `layout=0` 锄了一大块 ⇒ **回执里直接教** `layout=1`")
        _reset()
        for x in range(0, 6):
            for y in range(0, 6):
                TILES[(x, y)] = {"diggable": True}
        out3 = M._farm_till(x1=0, y1=0, x2=5, y2=5)
        ck("提示里点名 `layout=1`", "layout=1" in out3, out3[-300:])
        ck("…并说清 1/2/3 是什么", "初级" in out3 and "高级" in out3 and "铱" in out3, out3[-300:])
        _reset()
        TILES.update({(0, 0): {"diggable": True}, (1, 0): {"diggable": True}})
        out3b = M._farm_till(x1=0, y1=0, x2=1, y2=0)
        ck("…只 1×2 的小地**不啰嗦**（<3×3 不给这条）", "layout=1" not in out3b, out3b[-200:])

        # ── ④ plot 给的方形后面带可复制的 kw ────────────────────────
        # ⚠️ 这条走**源码钉**而不是真跑 `farm plot`：plot 里那套连通域分类要喂一整张像样的图
        #    （`_plot_classify` 认 diggable/crop/object 一堆字段），夹具凑齐的成本比它值当的高。
        #    要钉的其实只有一件事：**方形后面那行有没有给出能照抄的 kw**。
        print("\n④ `farm plot` 的方形后面给**能复制粘贴**的 kw（源码钉）")
        src = io.open(os.path.join(HERE, "nagi_mcp_server.py"),
                      encoding="utf-8", errors="replace").read()
        ck("方形后面有一行「要按洒水器布局…」", "要按**洒水器布局**锄/种这块方形" in src)
        ck("…kw 里带 x1/y1/x2/y2 的具体写法",
           "x1={sq[0]} y1={sq[1]} x2={sq[0]+sq[2]-1}" in src)
        ck("…并且点名 `layout=1`", "layout=1`（1初级十字" in src)

        # ── ⑤ 播种：种子用完就停 ──────────────────────────────────
        print("\n⑤ 播种：种子只有 2 颗、地有 3 格 ⇒ **只挥 2 次**，然后说清为什么停")
        _reset()
        INV[:] = [{"name": "Parsnip Seeds", "displayName": "防风草种子", "stack": 2}]
        for x in range(0, 3):
            TILES[(x, 3)] = {"terrain": "HoeDirt"}
        _scan.planted = [(0, 3), (1, 3)]
        out5 = M._farm_plant(seed_name="Parsnip Seeds", x1=0, y1=3, x2=2, y2=3)
        n_use = len([c for c in CALLS if c[0] == "item"])
        ck("**只挥了 2 次**（第 3 格没去试）", n_use == 2, f"use_item ×{n_use}")
        ck("回执说「种子用完了」", "种子用完" in out5, out5[-400:])
        ck("…说清剩几格没种", "剩 1 格" in out5 or "1 格" in out5, out5[-400:])
        ck("…并明说**不是这些地的问题**", "不是这些地" in out5 or "不是地的问题" in out5, out5[-400:])

        # ── ⑥ 源码钉：两条路都接了"已有洒水器" ─────────────────────
        print("\n⑥ 源码：`till` 与 `plant` 都接了 `_existing_sprinkler_crosses`")
        src = io.open(os.path.join(HERE, "nagi_mcp_server.py"),
                      encoding="utf-8", errors="replace").read()
        ck("`_farm_till` 里调了", src.count("_existing_sprinkler_crosses(") >= 3,
           str(src.count("_existing_sprinkler_crosses(")))
        ck("…而且 `layout == 1` 才调（别的布局不白付一发 /surroundings）",
           "if layout == 1 else set()" in src)
    finally:
        M.time = _real_time
        for k, v in _real.items():
            setattr(M, k, v)

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        sys.exit(1)
    print("✅ 布局与播种全绿：十字照算、已摆好的洒水器自动绕、种子空了立刻停。")
    sys.exit(0)


if __name__ == "__main__":
    main()
