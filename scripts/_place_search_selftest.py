# -*- coding: utf-8 -*-
"""钉子：`help` 的「关键词 → 地点」搜索（2026-10-06 恒拍板）。

恒的原话：
  · 「**确保 ai 能搜到就行了**」（＝搜不到就是坏的，别管印不印状态条）
  · 「**列这么三条相关搜索结果给它就好了，不用帮它跑**」（＝只提示，绝不自动执行）

改动前的实测（本钉子的"红"）：
  · `_place_line("洞穴")`      → **空**（表里叫「农场洞穴(外)」，前缀匹配搜不到中缀词）
  · `_place_line("农场洞穴")`  → `📍 Farm（农场洞穴(蘑菇/果蝠)）→ map go Farm`
      ⚠️ 这条**看着像答案、其实不是**：`map go Farm` 只保证"人在农场图上"，
      **不会**把人带到洞口 —— 属于"假门"。（根因：`MAP_FEATURES` 先扫、
      和 `POI` 同命中长度时排序键相同 ⇒ 先入的粗条目把精确 POI 静默挤掉。）

改完要成立（本钉子的"绿"）：
  1. 「洞穴」这类**中缀词**能命中「农场洞穴(外)」，且**目的地是 POI 名**；
  2. 同命中长度时 **POI 优先于 MAP_FEATURES**（别再回 `map go Farm`）；
  3. 结果**最多 3 条**、只提示不执行（行里只有 `📍 …→ map go …` 的指路）；
  4. 谁也不命中时**返回空**（由 `help` 明说"没找到"，不许静默给个错的）；
  5. 老规矩不许破：英文 2~3 字不许当地点前缀（`cook` 不该撞 `CommunityCenter`）。

跑：`python scripts/_place_search_selftest.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M   # noqa: E402

_fail = []


def ck(name, cond, detail=""):
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f"   ← {detail}"))
    if not cond:
        _fail.append(name)


def _lines(q, n=3):
    """拿候选行；`_place_lines` 还没落地时返回 None（＝红）。"""
    f = getattr(M, "_place_lines", None)
    if f is None:
        return None
    return f(q, n=n)


print("🧭 关键词→地点 搜索钉子")

# ── 0. 函数在不在（老 API `_place_line` 必须还在：调用点/其它钉子不许炸） ──
ck("`_place_lines` 存在（新：多条候选）", callable(getattr(M, "_place_lines", None)),
   "还没有 _place_lines —— 功能没做")
ck("`_place_line` 仍存在（老单条 API 向后兼容）", callable(getattr(M, "_place_line", None)))

# ── 1. 中缀词必须能搜到（恒：「洞穴」） ──
_ls = _lines("洞穴")
ck("「洞穴」能搜到东西", bool(_ls), f"返回 {_ls!r}")
if _ls:
    ck("「洞穴」首条落在**农场洞穴**上", "农场洞穴" in _ls[0], _ls[0])
    ck("「洞穴」目的地是 **POI 名**、不是粗粒度的 `Farm`",
       '"destination":"农场洞穴(外)"' in _ls[0] or 'destination="农场洞穴(外)"' in _ls[0],
       _ls[0])

# ── 2. 精确词不许被 MAP_FEATURES 挤掉（同长度 POI 优先） ──
_ls2 = _lines("农场洞穴")
ck("「农场洞穴」首条仍是 POI（不是 `map go Farm`）",
   bool(_ls2) and "农场洞穴(外)" in _ls2[0], str(_ls2[:1]))

# ── 3. 最多 3 条 & 只提示不执行 ──
for q in ("洞穴", "火山", "农场", "沙漠", "马龙"):
    _l = _lines(q) or []
    ck(f"「{q}」候选 ≤3 条", len(_l) <= 3, f"{len(_l)} 条")
    ck(f"「{q}」全是指路行（只提示，不代跑）",
       all("map(ops=\"go\"" in x for x in _l), str(_l))

# ── 4. 谁都不命中 → 空（交给 help 说"没找到"，不许编一个） ──
ck("乱码 → 没有任何候选（不猜）", not (_lines("qqzzxxjj") or []), str(_lines("qqzzxxjj")))

# ── 5. 近似名要能兜住（typo：动/洞） ──
_ls3 = _lines("农场动穴")
ck("错字「农场动穴」给出**相近**候选", bool(_ls3) and "农场洞穴" in str(_ls3), str(_ls3))

# ── 6. 老规矩：英文 2~3 字不算地点前缀（`cook` 里的 `co`） ──
ck("`_place_match('cook','CommunityCenter')` 仍为空（英文短词不截胡）",
   M._place_match("cook", "CommunityCenter") == "",
   repr(M._place_match("cook", "CommunityCenter")))

# ── 7. 主入口 `_intent_search` 总行数 ≤3，且地点那条在里面 ──
_s = M._intent_search("洞穴")
_n = len([x for x in (s for s in _s.split("\n")) if x.strip().startswith(("📍", "🛠"))])
ck("`_intent_search('洞穴')` 结果 ≤3 条", 0 < _n <= 3, f"{_n} 条：\n{_s}")
ck("`_intent_search('洞穴')` 里含地点那条", "📍" in _s, _s[:120])

print()
if _fail:
    print(f"❌ 红 {len(_fail)} 项：{_fail}")
    sys.exit(1)
print("✅ 全绿")
