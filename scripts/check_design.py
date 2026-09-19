"""🔒 农活防回归检查（2026-08-15 恒：防止"做对了又改回作弊"）
扫代码，违反 CLAUDE.md 农活设计原则的模式直接报：
- 浇水不许直接改地块（/water_area 已删；只能 tool_area 蓄力 + 补漏 position+DoFunction 真浇）
- 锄地不许 /till_area 做主（只能补漏）
- **播种每步要 select 种子**（原挂 farm_row；2026-09-19 播种收敛成 `_farm_plant` 后改挂在它头上）
- farm 域：锄地/播种各有**唯一实现**，且「多 op 一次调用共用一份 kw」不被砍掉
- 无水壶不许浇水
用法: python scripts/check_design.py
"""
import io
import re
import sys
import os

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ISSUES = []


def check(filename, patterns, label):
    """patterns: [(regex, 为什么违反), ...]"""
    path = os.path.join(SCRIPT_DIR, filename)
    if not os.path.exists(path):
        return
    src = open(path, encoding="utf-8").read()
    for pat, why in patterns:
        if re.search(pat, src, re.MULTILINE):
            # 找行号
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(pat, line):
                    ISSUES.append(f"  {filename}:{i}  {label} — {why}")
                    break


def main():
    # 1. water_crops：脚本头写 batch（回归标志）
    check("water_crops.py", [
        (r"batch mode|batch\b.*/water_area", "脚本头/注释还在写 batch（应一格一格拟人）"),
        (r"api\._post\(\"/tool\"", "水壶浇水用 /tool（无效！应 use_item 或 tool_area）"),
    ], "浇水")
    # 1.5 farm 域自由组合 ops（恒：耕/浇/种/耕+浇...任意组合，别又砍成只有一条龙）
    nms = open(os.path.join(SCRIPT_DIR, "nagi_mcp_server.py"), encoding="utf-8").read()
    # 2026-09-19 改：原来判的是 `"has_till and has_plant"`（那条"合并成一条龙"的分支）——
    #   恒拍板**播种也收敛成一个 op、一条龙退役**，所以判据换成：
    #   ① 锄地/播种各只有一个实现；② 「多 op 一次调用共用一份 kw」还在（`_ops_run` 只调一次）；
    #   ③ 兄弟参数集盖得住 dispatch 各 op 的参数（否则 `farm ops="till plant fertilize"` 会报"未知参数"）。
    if "_farm_plant" not in nms:
        ISSUES.append("  farm 域没有 `_farm_plant`（播种的唯一实现丢了）")
    if "_farm_till" not in nms:
        ISSUES.append("  farm 域没有独立锄地（till 丢失）")
    try:
        import ast as _ast
        _tree = _ast.parse(nms)
        _defs = {n.name: [a.arg for a in n.args.args + n.args.kwonlyargs]
                 for n in _ast.walk(_tree) if isinstance(n, _ast.FunctionDef)}
        _farm = next(n for n in _ast.walk(_tree)
                     if isinstance(n, _ast.FunctionDef) and n.name == "farm")
        _disp = None
        for _n in _ast.walk(_farm):
            if isinstance(_n, _ast.Assign) and any(getattr(t, "id", "") == "dispatch" for t in _n.targets):
                _disp = _n.value
        _union = set()
        for _v in _disp.values:
            if isinstance(_v, _ast.Name):
                _union |= set(_defs.get(_v.id, []))
        _core = {"x", "y", "rows", "length", "direction"}
        # 🧪 "只预览"键（dry_run/preview…）**故意不进兄弟集**：它们必须走 `_farm_kw_norm` 的
        #    拒绝路径，否则"以为在干跑"会变成"真干了"（2026-09-17 真机教训）。判据里同样豁免它们。
        _dryintent = set()
        for _n in _ast.walk(_tree):
            if isinstance(_n, _ast.Assign) and any(getattr(t, "id", "") == "_DRY_INTENT_KW" for t in _n.targets):
                _set_node = _n.value.args[0] if isinstance(_n.value, _ast.Call) else _n.value
                _dryintent = {e.value for e in getattr(_set_node, "elts", [])}
        _sib = set()
        for _n in _ast.walk(_tree):
            if isinstance(_n, _ast.Assign) and any(getattr(t, "id", "") == "_FARM_SIBLING_KW" for t in _n.targets):
                _val = _n.value
                _set_node = _val.args[0] if isinstance(_val, _ast.Call) else _val
                _sib = {e.value for e in getattr(_set_node, "elts", [])}
        _missing = (_union - _core - _dryintent) - _sib
        if _missing:
            ISSUES.append(f"  `_FARM_SIBLING_KW` 没盖住 farm 这些参数 {sorted(_missing)}"
                          f"（新 op 的参数要补进去，否则 `farm ops=\"till plant X\"` 一份 kw 共用会报'未知参数'）")
        _calls = [c for c in _ast.walk(_farm)
                  if isinstance(c, _ast.Call) and getattr(c.func, "id", "") == "_ops_run"]
        if len(_calls) != 1:
            ISSUES.append(f"  farm() 里 `_ops_run` 调了 {len(_calls)} 次（应恰好 1 次 —— "
                          f"多个 op 一次调用共用一份 kw 的语义靠这个形态）")
    except Exception as _e:
        ISSUES.append(f"  farm 域结构自检失败（AST）: {_e}")

    # 2. 锄地 _till_rect 的补漏必须是 tool_area（蓄力），不能 /till_area
    #    （2026-09-19 起 _farm_till 里那条"高级锄锄初级布局→一键 /till_area"的路已删：
    #     初级布局**恒拟人逐格**、与锄头等级无关 ⇒ 全仓不该再有 api.till_area）
    till_rect = open(os.path.join(SCRIPT_DIR, "nagi_mcp_server.py"), encoding="utf-8").read()
    if "api.till_area(" in till_rect:
        ISSUES.append("  nagi_mcp_server.py 出现 api.till_area()（锄地主方法必须拟人逐格/蓄力，直接改地块只做补漏）")
    # 找到 _till_rect 函数体（2833 行附近），检查补漏段
    m = re.search(r"def _till_rect.*?查缺补漏(.*?)# 5\. 报告", till_rect, re.S)
    if m:
        body = m.group(1)
        if "till_area" in body and "tool_area" not in body.split("查缺补漏")[-1][:200]:
            ISSUES.append("  nagi_mcp_server.py _till_rect 补漏用 /till_area（应 tool_area 蓄力补）")
        if "/water_area" in body:
            ISSUES.append("  /water_area 出现（2026-08-15 已删除，禁止改地块浇水——只能用 tool_area 蓄力+补漏真浇）")

    if ISSUES:
        print("❌ 检测到农活回归模式：")
        print("\n".join(ISSUES))
        print("\n💡 看 CLAUDE.md 农活设计原则，主方法拟人，直接改地块只做兜底")
        sys.exit(1)
    print("✅ 农活代码符合设计原则（拟人主方法 + 兜底补漏）")


if __name__ == "__main__":
    main()
