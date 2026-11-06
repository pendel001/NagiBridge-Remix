"""🧭 `map walk` 只认**落点**，不认图名；出入口站格不再是候选 —— 纯 Python 自验（不起服务、不碰游戏）。2026-11-06

真机（`scripts/sessions/session_log.jsonl` 12:36:41 / 12:37:16 / 12:40:38 / 12:40:57）：
    AI 在**农舍**里，照状态条 `🗺️ 可: 农场` 敲 `map walk 农场`
    ⇒ 我们回「❓ 「农场」不是落点名，但**有 12 个相近的**」，
      **头两个就是 农场上口(→深山) / 农场下口(→森林)**
    ⇒ AI 挑了「农场下口(→森林)」⇒ 从 (64,15) 一路走到 (40,64)（六十多格）
    ⇒ 恒 12:40 当场喊「不要去下口！下口太远了」。

两根（**互不依赖**，各自钉）：
  ① **图名不是落点**：`map walk 农场` / `farm` / `FARM` 这种**图名**应该直接转 `map go`
     （跨图真实路线，落点按你从哪个口进来）；`map walk` 只认**图里的落点**。
  ② **出入口/中转站（`nav_only`）不该进候选清单**：2026-11-05 已经从 `map here` 摘掉了
     （恒「这些出入口瓦片不列入」），`map walk` 的"相近的"兜底漏了这一刀 —— 真机正是从这儿走歪的。
     ⚠️ 只是**不推荐**：把全名写全（`map walk 农场下口(→森林)`）照旧能走（明确意图）。

🌱 判据都走**公开入口** `navigation.walk_to()`，不测内部函数形状（`_exact_map_name` 只测行为）。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 🖨 stdout 被管道接走时（`_quick.py` / 全量跑）默认走 gbk，emoji 会 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nagi_mcp_server as M          # 先 import：navigation 的宿主注入在它末尾
import navigation as N
import locations

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """只喂 `walk_to` 开头读的东西；`loc` 可切（农舍里 / 农场里）。"""
    BASE_URL = "http://localhost:7843"

    def __init__(self):
        self.loc = "FarmHouse"

    def state(self, **kw):
        return {"location": {"name": self.loc}, "player": {"x": 5, "y": 9},
                "time": {"season": "spring", "dayOfMonth": 13, "timeOfDay": 600},
                "inventory": []}

    def current_location(self):
        return self.loc

    def _get(self, ep, params=None):
        return {"ok": True}

    def _post(self, ep, data=None):
        return {"ok": True}


_API = FakeApi()
_PATCH = ("api", "_with_state", "map_go", "_apply_poi_stand_face",
          "_festival_poi_active", "_grandpa_shrine_gate")
_real = {n: getattr(N, n) for n in _PATCH}
_NAV_LAST_BAK = dict(N._NAV_LAST)
_FIXTURES = []
try:
    N.api = _API
    N._with_state = lambda s: s
    N.map_go = lambda d, **k: f"【GO {d}】"
    N._apply_poi_stand_face = lambda n: ""
    N._festival_poi_active = lambda *a, **k: True
    N._grandpa_shrine_gate = lambda d: ""

    print("\n① 图名 → 转 `map go`（真机那条：农舍里 `map walk 农场`）")
    _o1 = N.walk_to("农场")
    ck("①`农场` 不再摊候选（没有「不是落点名」那句）", "不是落点名" not in _o1, _o1[:300])
    ck("①`农场` 转成 `map go Farm`", "【GO Farm】" in _o1, _o1[:300])
    ck("①回执说清「图名不是落点」", "是**图名**" in _o1 and "`map walk` 只认" in _o1, _o1[:300])
    ck("①回执交代「哪个口不用点名」", "不用点名" in _o1, _o1[:300])

    print("\n② 大小写 / 空格变体（AI 常照状态条小写敲）")
    for _w in ("farm", "FARM", " 农场 ", "Farm"):
        _o = N.walk_to(_w)
        ck(f"②`{_w}` ⇒ Farm", "【GO Farm】" in _o, _o[:120])

    print("\n③ `map walk Farm`（英文键）走**老路**：不该多出图名提示那段")
    _o3 = N.walk_to("Farm")
    ck("③`Farm` ⇒ Farm（老路照旧）", "【GO Farm】" in _o3, _o3[:200])
    ck("③`Farm` 不贴图名提示（老行为一字不动）", "是**图名**" not in _o3, _o3[:200])

    print("\n④ 只认**精确**：半截名/长名不许被这条改道（宁报错别兜底）")
    for _w, _exp in (("姜岛小屋(门内六人房)", ""), ("农场洞", ""), ("asdf", ""), ("", "")):
        _got = N._exact_map_name(_w)
        ck(f"④`{_w}` ⇒ 不认作图名（''）", _got == _exp, _got)
    ck("④`农场` ⇒ Farm（正向对照）", N._exact_map_name("农场") == "Farm", N._exact_map_name("农场"))

    print("\n⑤ 出入口/中转站（nav_only）不进候选清单")
    # 夹具：两个"正常落点" + 一个"换图站格"，同名子串
    for _n, _opts in (("测试口甲(正常一)", {}), ("测试口甲(正常二)", {}),
                      ("测试口甲(站格)", {"nav_only": True})):
        locations.POI[_n] = {"map": "Farm", "pos": (10, 10), "note": "钉子夹具", **_opts}
        _FIXTURES.append(_n)
    _o5 = N.walk_to("测试口甲")
    ck("⑤两个正常落点都列出来", "测试口甲(正常一)" in _o5 and "测试口甲(正常二)" in _o5, _o5[:400])
    _bullets = [l for l in _o5.splitlines() if l.strip().startswith("·")]
    ck("⑤换图站格**不在**候选里", not any("站格" in l for l in _bullets), str(_bullets)[:300])
    ck("⑤但如实说一句「有几个站格同名、所以没列」", "换图站格" in _o5, _o5[:400])

    print("\n⑥ 只对上一个换图站格 ⇒ 明确说清，**绝不**当落点走")
    _o6 = N.walk_to("中转")
    ck("⑥回执点明「只对上了换图站格」", "只对上了**换图站格**" in _o6, _o6[:400])
    ck("⑥**没有**转给 map_go（不是落点就别走）", "【GO" not in _o6, _o6[:400])
    ck("⑥给下一步：`map go <图名>` + `map here`",
       "map go <图名>" in _o6 and "map here" in _o6, _o6[:400])

    print("\n⑦ 把全名写全 ⇒ 照旧能走（明确意图不拦）")
    _o7 = N.walk_to("农场下口(→森林)")
    ck("⑦`农场下口(→森林)` ⇒ 原样转 map_go", "【GO 农场下口(→森林)】" in _o7, _o7[:200])

    print("\n⑧ 认不出时的兜底清单**也不许**列出入口站格")
    _API.loc = "Farm"
    _o8 = N.walk_to("zzzz")
    ck("⑧兜底清单出得来", "认不出「zzzz」" in _o8, _o8[:300])
    ck("⑧清单里没有 农场下口/农场上口",
       "农场下口" not in _o8 and "农场上口" not in _o8, _o8[:400])
    ck("⑧清单里仍有正常落点（出货箱）", "出货箱" in _o8, _o8[:400])

    print("\n⑨ 回到农场后再问图名：仍然转 `map go`（同图/跨图交给它自己判）")
    _API.loc = "Farm"
    _o9 = N.walk_to("农场")
    ck("⑨`农场` @Farm ⇒ 仍是 map_go（它自己会说「已经在」）", "【GO Farm】" in _o9, _o9[:300])

finally:
    for _n in _FIXTURES:
        locations.POI.pop(_n, None)
    for _n, _v in _real.items():
        setattr(N, _n, _v)
    N._NAV_LAST.clear()
    N._NAV_LAST.update(_NAV_LAST_BAK)

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 图名转 map go / 出入口站格不进候选：全部通过。")
