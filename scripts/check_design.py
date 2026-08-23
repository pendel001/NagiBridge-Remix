"""🔒 农活防回归检查（2026-08-15 恒：防止"做对了又改回作弊"）
扫代码，违反 CLAUDE.md 农活设计原则的模式直接报：
- 浇水不许直接改地块（/water_area 已删；只能 tool_area 蓄力 + 补漏 position+DoFunction 真浇）
- 锄地不许 /till_area 做主（只能补漏）
- farm_row 播种每步要 select 种子
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
    if "has_till and has_plant" not in nms:
        ISSUES.append("  farm 域没有 till+plant 合并（一条龙逻辑丢失）")
    if "_farm_plant_only" not in nms:
        ISSUES.append("  farm 域没有独立播种（plant 被砍成一条龙）")
    if "_farm_till" not in nms:
        ISSUES.append("  farm 域没有独立锄地（till 丢失）")

    # 2. 锄地 _till_rect 的补漏必须是 tool_area（蓄力），不能 /till_area
    #    （hoe_layout 高级锄初级布局的 /till_area 是文档化兜底，不算回归）
    till_rect = open(os.path.join(SCRIPT_DIR, "nagi_mcp_server.py"), encoding="utf-8").read()
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
