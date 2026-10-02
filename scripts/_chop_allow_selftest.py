# -*- coding: utf-8 -*-
"""🧪 「砍树放行名单」离线自测（2026-09-12 恒拍板）——不碰游戏。

治的病：`chop_trees.py` docstring 写 `Tree:1~3`、代码却 `startswith("Tree:")` 全收 ⇒
真机站在恒的蘑菇树堆旁，最近的可砍目标就是 `Tree:7`。现在改成**特殊树种默认保护**，
`settings chop ...` 显式放行，且被挡下的**必须报出来**（不静默）。

这个自测用假 `/surroundings` 数据喂**真函数**，验四件事：
  A `parse_allow`：名字/树号/none/all 都认；**认不出的报错**（不静默当空）
  B `is_choppable`：默认 7/8/10/11/12/13 全挡、1/2/3 放行；放行后只开点名的
  C `chop_trees.find_trees`：默认**不返回**蘑菇树/桃花心木，但 skipped 里数得出来；放行后返回
  D `clear_area.tile_target_name`：同款过滤

跑法： `PYTHONIOENCODING=utf-8 python _chop_allow_selftest.py`
"""
import importlib
import sys

import tree_types as tt

FAILED = []


def _check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        FAILED.append(name)


# ── 假 /surroundings：两棵普通树 + 蘑菇树 + 桃花心木 + 苔雨树 + 一根树枝 ──
FAKE_TILES = {
    "center": {"x": 40, "y": 40},
    "location": "Farm",
    "tiles": [
        {"x": 41, "y": 40, "terrain": "Tree:1"},                  # 橡树
        {"x": 42, "y": 40, "terrain": "Tree:3"},                  # 松树
        {"x": 43, "y": 40, "terrain": "Tree:7"},                  # 🍄 蘑菇树
        {"x": 44, "y": 40, "terrain": "Tree:8"},                  # 桃花心木
        {"x": 45, "y": 40, "terrain": "Tree:10"},                 # 苔雨树
        {"x": 46, "y": 40, "terrain": "Tree:13"},                 # 神秘树
        {"x": 47, "y": 40, "terrain": "Tree:99"},                 # 🤷 未知树种（该按"保护"处理）
        {"x": 48, "y": 40, "object": "Twig"},                     # 树枝（不属树，照砍）
    ],
}


def _load(mod_name, allow_arg):
    """带上 `--allow` 重新导入脚本模块（脚本在模块级就把名单解析好了）。

    ⚠️ `clear_area.py` 的 x1,y1,x2,y2 是**位置参数**，不给会 argparse 直接退出（第一版就踩了）。
    """
    argv = [mod_name + ".py", "--port", "0"]
    if mod_name == "clear_area":
        argv += ["0", "0", "1", "1"]          # 占位区域，本自测不真扫
    if allow_arg:
        argv += ["--allow", allow_arg]
    sys.argv = argv
    mod = importlib.import_module(mod_name)
    return importlib.reload(mod)


def case_a():
    print("A) parse_allow：认名字/树号/none/all，认不出**报错**")
    _check("空值 → 空名单（只砍普通树）", tt.parse_allow("") == ([], None))
    _check("'蘑菇树' → ['7']", tt.parse_allow("蘑菇树") == (["7"], None))
    _check("'蘑菇树,桃花心木' → ['7','8']", tt.parse_allow("蘑菇树,桃花心木") == (["7", "8"], None))
    _check("'苔雨树' 一名对多号 → 10/11/12",
           tt.parse_allow("苔雨树") == (["10", "11", "12"], None))
    _check("树号直写 '7,13' → ['7','13']", tt.parse_allow("7,13") == (["7", "13"], None))
    _check("'none' → 收回", tt.parse_allow("none") == ([], None))
    _check("'all' → 全放行", tt.parse_allow("all") == (["all"], None))
    a, err = tt.parse_allow("蘑菇树,椰子树")
    _check("认不出的**报错**且不返回半截名单", a == [] and err and "椰子树" in err, f"err={err!r}")


def case_b():
    print("B) is_choppable：默认特殊树全挡，放行才开")
    for i in ("7", "8", "10", "11", "12", "13", "6", "9", "99"):
        _check(f"默认挡 Tree:{i}", tt.is_choppable(i, []) is False)
    for i in ("1", "2", "3"):
        _check(f"默认放 Tree:{i}", tt.is_choppable(i, []) is True)
    _check("放行蘑菇树后开 7、仍挡 8", tt.is_choppable("7", ["7"]) is True
           and tt.is_choppable("8", ["7"]) is False)
    _check("'all' 全开（含未知 99）", tt.is_choppable("99", ["all"]) is True)


def case_b2():
    """🌲 2026-10-02 恒：「**不要保护农场之外的绿雨树，免得绿雨天收集不了苔藓了**」。
    ⇒ 苔雨树(10~12)的保护**只在 Farm**；别的图上它们是可砍目标（斧头砍正是收苔藓的手势）。"""
    print("B2) 苔雨树只在「Farm」受保护；**农场外一律可砍**")
    for loc in ("Forest", "Backwoods", "Mountain", "Town", "Railroad", "IslandWest"):
        _check(f"{loc} 上苔雨树**可砍**（不保护）",
               all(tt.is_choppable(i, [], loc) is True for i in ("10", "11", "12")))
    _check("Farm 上照旧**保护**（留着长苔藓）",
           all(tt.is_choppable(i, [], "Farm") is False for i in ("10", "11", "12")))
    _check("⚠️ **不知道自己在哪**（loc 空/没传）⇒ 按老规矩保护（宁少砍，别猜）",
           tt.is_choppable("10", [], "") is False and tt.is_choppable("10", []) is False)
    _check("别的特殊树**不被这条影响**：森林里蘑菇树/桃花心木照样保护",
           tt.is_choppable("7", [], "Forest") is False
           and tt.is_choppable("8", [], "Forest") is False
           and tt.is_choppable("13", [], "Forest") is False)
    _check("显式放行仍然优先（农场里也放得开）", tt.is_choppable("10", ["10"], "Farm") is True)
    _check("`scope_note()` 说得清适用范围", "Farm" in tt.scope_note()
           and "农场外" in tt.scope_note())


def case_c():
    print("C) chop_trees.find_trees：默认不返回特殊树，但 skipped 数得出来")
    ct = _load("chop_trees", "")
    ct.api.surroundings = lambda radius=20: FAKE_TILES
    targets, skipped = ct.find_trees()
    types = sorted(tt.tree_type_of(t[2]) for t in targets if tt.tree_type_of(t[2]))
    _check("只回 1/3 两种普通树", types == ["1", "3"], f"回的是 {types}")
    _check("Twig 照旧是目标", any(t[2] == "twig" for t in targets))
    _check("🌳 被挡的都在 skipped 里（7/8/10/13/99）",
           sorted(skipped) == ["10", "13", "7", "8", "99"], f"skipped={skipped}")
    _check("skipped 汇总说人话", tt.skipped_summary(skipped).count("×") == 5,
           tt.skipped_summary(skipped))

    ct2 = _load("chop_trees", "蘑菇树,桃花心木")
    ct2.api.surroundings = lambda radius=20: FAKE_TILES
    t2, s2 = ct2.find_trees()
    types2 = sorted(tt.tree_type_of(t[2]) for t in t2 if tt.tree_type_of(t[2]))
    _check("放行 7/8 后它们进目标", types2 == ["1", "3", "7", "8"], f"回的是 {types2}")
    _check("苔雨树/神秘树**仍在** skipped", sorted(s2) == ["10", "13", "99"], f"skipped={s2}")


def case_d():
    print("D) clear_area.tile_target_name：同款过滤（清地块不会顺手铲蘑菇树）")
    ca = _load("clear_area", "")
    _check("橡树 → 'Tree'（可清）", ca.tile_target_name({"terrain": "Tree:1"}) == "Tree")
    _check("蘑菇树 → None（挡住）", ca.tile_target_name({"terrain": "Tree:7"}) is None)
    _check("桃花心木 → None（挡住）", ca.tile_target_name({"terrain": "Tree:8"}) is None)
    _check("杂草不受影响", ca.tile_target_name({"object": "Weeds"}) == "Weeds")

    ca2 = _load("clear_area", "蘑菇树")
    _check("放行后蘑菇树 → 'Tree'", ca2.tile_target_name({"terrain": "Tree:7"}) == "Tree")
    _check("桃花心木仍挡住", ca2.tile_target_name({"terrain": "Tree:8"}) is None)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    import os
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.getcwd())
    case_a()
    case_b()
    case_b2()
    case_c()
    case_d()
    print()
    if FAILED:
        print("❌ 失败：", "、".join(FAILED))
        sys.exit(1)
    print("✅ 全部通过（⚠️ 仅逻辑自测；真机砍树仍未验）")
