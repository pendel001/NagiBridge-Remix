"""🌾 「翻好的地不用再锄一遍」 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-25

恒真机：「耕地在**已经耕过的格子**上，没有智能跳过」。
上一轮（09-23）只挡住了「已有作物」，**已翻好但还没种的地**会一路走到 C# 的 `Diggable` 检查 ——
那是地图 Back 层的**静态属性**、翻地不会把它抹掉 ⇒ 整块地每轮都被重复锄一遍
（蓄力那条路更狠：`/tool_area` 拿着整块矩形重扫）。

这里只验 **Python 这一半**（哪几格进 `tile_list`、报告怎么写）；
C# 那一半（`IsTillTarget`，蓄力路整块矩形走它）只能真机验，⏭ 见 CHANGELOG。

⚠️ 判据只有一份：`terrain == "HoeDirt"`（没有作物）——和**验收**那个 `tilled` 用的是同一个键，
   所以跳过的格在报告里天然算"已完成"，不会自相矛盾。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []
ROWS = {}          # 假地图：{(x,y): tile}


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _fake_scan(pts, margin=1):
    """按 pts 回放假地图。第一次（开工前扫）= ROWS；之后（验收扫）= 全 HoeDirt。"""
    p = list(pts)
    if not p:
        return {}, ""
    x1, y1 = min(t[0] for t in p) - margin, min(t[1] for t in p) - margin
    x2, y2 = max(t[0] for t in p) + margin, max(t[1] for t in p) + margin
    out = {}
    for x in range(x1, x2 + 1):
        for y in range(y1, y2 + 1):
            out[(x, y)] = {"terrain": None}
    for k, v in ROWS.items():
        if k in out:
            out[k] = dict(v)
    if _fake_scan.n:                       # 验收那次：挥过的格都翻好了（假地图记不住谁被挥，一律算成功）
        for k in list(out):
            out[k] = {"terrain": "HoeDirt"}
    _fake_scan.n += 1
    return out, ""


_fake_scan.n = 0
_fake_scan.tilled = []


class FakeApi:
    def state(self):
        return {"player": {"currentToolUpgrade": 0, "stamina": 200, "maxStamina": 270}}

    def walk_ok_tiles(self, *_a):
        return {(x, y) for x in range(40, 50) for y in range(40, 50)}

    def stand_tile(self, tx, ty, _ok):
        return (tx, ty - 1, 2)

    def walk_natural(self, *_a):
        return True

    def face(self, *_a):
        return {"ok": True}

    def use_tool(self, *_a):
        return {"ok": True}

    def player_stamina(self):
        return (200, 270)


_real = {n: getattr(M, n) for n in ("api", "_scan_tiles", "_select_best_hoe", "_with_state")}

# 记下"被要求锄的格"：**第 2 次**扫（验收）问到的就是本次真正下锄的名单
def _spy_scan(pts, margin=1):
    p = list(pts)
    r = _fake_scan(p, margin)
    if _fake_scan.n == 2:
        _fake_scan.tilled = p
    return r


def _reset_case():
    _fake_scan.n = 0
    _fake_scan.tilled = []
    ROWS.clear()


try:
    M.api = FakeApi()
    M._scan_tiles = _spy_scan
    M._select_best_hoe = lambda: None
    M._with_state = lambda s: s

    print("\n① 全是「已有作物」——照旧跳过（这条是 09-23 补的，别被这次改动碰坏）")
    _reset_case()
    ROWS.update({(40, 40): {"crop": "防风草"}, (41, 40): {"forageCrop": "1"}})
    out = M._farm_till(x1=40, y1=40, x2=41, y2=40)
    ck("报「一格都不用锄」", "一格都不用锄" in out, out)
    ck("…说明是已有的作物", "已有作物" in out, out)
    ck("…给下一步（先收掉）", "收掉" in out, out)

    print("\n② 全是「早就翻好了的地」—— 一格都不锄（本次新加）")
    _reset_case()
    ROWS.update({(40, 40): {"terrain": "HoeDirt"}, (41, 40): {"terrain": "HoeDirt"}})
    out = M._farm_till(x1=40, y1=40, x2=41, y2=40)
    ck("报「一格都不用锄」", "一格都不用锄" in out, out)
    ck("…点明是**早就翻好了**", "早就翻好了" in out, out)
    ck("…下一步是**直接播种**（不是让人再锄）", "直接在这块地上播种" in out, out)
    ck("…没让人去收割", "收掉" not in out, out)

    print("\n③ 混着来：1 格翻好了 + 1 格生土 → 只锄生土那格")
    _reset_case()
    ROWS.update({(40, 40): {"terrain": "HoeDirt"}, (41, 40): {"terrain": None}})
    out = M._farm_till(x1=40, y1=40, x2=41, y2=40)
    ck("验收扫只问生土那格（说明翻好的那格真没进名单）",
       _fake_scan.tilled == [(41, 40)], str(_fake_scan.tilled))
    ck("…报告里点名跳过 1 格**本来就翻好了**", "本来就翻好了" in out, out)
    ck("…只锄了 1 格", "1/1" in out, out)

    print("\n④ 两样都跳过时的下一步措辞（全作物 → 收；否则 → 播种/收）")
    _reset_case()
    ROWS.update({(40, 40): {"terrain": "HoeDirt"}, (41, 40): {"crop": "防风草"}})
    out = M._farm_till(x1=40, y1=40, x2=41, y2=40)
    ck("两种原因都点了名", "已有作物" in out and "早就翻好了" in out, out)
    ck("…下一步同时给「收」和「种」两条", "收掉" in out and "播种" in out, out)

    print("\n⑤ 真正的生土不受影响（别把跳过头了）")
    _reset_case()
    ROWS.update({(40, 40): {"terrain": None}})
    out = M._farm_till(x1=40, y1=40, x2=40, y2=40)
    ck("生土照样锄（1/1）", "1/1" in out, out)
    ck("…且没有跳过的行", "⏭ 跳过" not in out, out)
finally:
    for k, v in _real.items():
        setattr(M, k, v)

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
