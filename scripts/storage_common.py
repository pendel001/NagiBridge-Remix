"""🧺 storage 纯逻辑层（智能分拣/箱子目标解析/颜色/自动类目标签）。

只依赖标准库 + `stardew_api`（`api`），**不碰 `nagi_mcp_server` 的 `_with_state`/状态条**。
MCP 工具壳留守 `nagi_mcp_server`（storage_store/view/take/find/default/tag/layout/color 等），
这里只提供被壳调用的下层算法/数据。
"""
import re
import stardew_api as api


# (hue 下限, hue 上限, 颜色名, emoji)
_HEX_COLOR_BANDS = [
    # ⚠️ 2026-09-03 恒：粉色区间扩到 315-360——浅粉难分（#FF75C3=326 之前算粉，但 #FFC0CB≈349
    #    被原 (330,360) 抓成红）。粉=315-360，紫=260-315，避免粉/紫/红混淆（真草莓红在 0-20）。
    (0, 20, "red", "🟥"), (20, 45, "orange", "🟧"), (45, 70, "yellow", "🟨"),
    (70, 160, "green", "🟩"), (160, 200, "cyan", "🟦"), (200, 260, "blue", "🟦"),
    (260, 315, "purple", "🟪"), (315, 360, "pink", "🟪"),
]
_COLOR_ZH = {"red": "红", "orange": "橙", "yellow": "黄", "green": "绿", "cyan": "青",
             "blue": "蓝", "purple": "紫", "pink": "粉", "gray": "灰", "black": "黑"}
_COLOR_NAME_SYNONYMS = {
    "红色": "red", "红": "red", "橙色": "orange", "橙": "orange", "黄色": "yellow", "黄": "yellow",
    "绿色": "green", "绿": "green", "青色": "cyan", "蓝色": "blue", "蓝": "blue",
    "紫色": "purple", "紫": "purple", "粉色": "pink", "粉": "pink",
    "黑色": "black", "黑": "black", "灰色": "gray", "灰": "gray",
}

# 🎨 2026-09-04 恒：改色工具的 色名→hex（storage color）。⚠️黑用暗灰 #303030——纯 #000000=默认木纹哨兵(被识别成未染色)。
_COLOR_KEY_HEX = {
    "red": "#C9423B", "orange": "#E98C2B", "yellow": "#E8C93B", "green": "#4FA33B",
    "cyan": "#3BA7C9", "blue": "#3B6FC9", "purple": "#8A4FD0", "pink": "#F07BA9",
    "gray": "#888888", "black": "#303030",
}

# 🆕 2026-09-03 恒：自动类目标签词表（_resolve_storage_target 认「矿石箱」这类词→定位 autoTag 箱）
_AUTO_LABEL_WORDS = {
    "矿": {"矿", "矿石", "宝石", "矿箱", "宝石箱", "石英"},
    "古物": {"古物", "古物箱", "骨骼"},
    "鱼": {"鱼", "鱼箱", "水产", "鱼子", "鲑"},
    "种子": {"种子", "种子箱"},
    "作物": {"作物", "作物箱", "蔬果", "菜箱", "水果箱", "蔬菜箱", "果实", "花果"},
    "农产": {"农产", "农产品", "蛋奶", "加工品", "奶酪", "蛋黄酱", "蜂蜜"},
    "建材": {"建材", "建材箱", "木材", "石头", "建筑", "材料", "木头", "石块"},
    "料理": {"料理", "食物", "熟食", "菜肴", "烹饪"},
    "装备": {"装备", "装备箱", "工具箱", "武器", "钓具"},
}


def _color_display(hexstr):
    """→ (emoji, 中文色名)；未染色/默认/非法 → ('⬜', '')。AI 靠名字区分粉/紫/蓝（emoji 粉紫共用 🟪）。"""
    emo, name = _hex_to_color_name(hexstr)
    if not name:
        return ("⬜", "")
    return (emo, _COLOR_ZH.get(name, name))


def _color_to_hex(name):
    """storage color 的 color 参数 → ('#RRGGBB' 或 ''=复位默认)。支持 #hex/RRGGBB/英文key/中文色名/默认。"""
    s = (name or "").strip()
    low = s.lower()
    if not s or low in ("clear", "默认", "木", "复位", "default", "reset", "none"):
        return ""
    if s.startswith("#"):
        return s if len(s) == 7 else ("#" + s if len(s) == 6 else s)
    if len(s) == 6 and all(c in "0123456789abcdefABCDEF" for c in s):
        return "#" + s
    if low in _COLOR_KEY_HEX:
        return _COLOR_KEY_HEX[low]
    for cn, key in _COLOR_NAME_SYNONYMS.items():
        if s in cn or s == key:
            return _COLOR_KEY_HEX.get(key, "")
    return s  # 未知，交给 C# 报格式错


def _hex_to_color_name(hexstr):
    """#RRGGBB → (emoji, 颜色名)。纯白/纯黑(哨兵)/透明/非法 → ('', '')（未染色）。"""
    if not hexstr or not isinstance(hexstr, str):
        return ("", "")
    h = hexstr.lstrip("#")
    if len(h) != 6:
        return ("", "")
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except Exception:
        return ("", "")
    mx, mn = max(r, g, b), min(r, g, b)
    # ⚠️ 2026-09-04 恒：SDV 未染色的默认箱 playerChoiceColor 读出来是纯 #000000(哨兵/木纹)，不是真黑。
    #    真·黑(玩家染的)是暗灰如 #404040。把纯黑当未染色，别把原木默认箱认成黑。
    if mx == 0:
        return ("", "")
    if mn >= 250:
        return ("", "")  # 纯白 = 未染色
    if mx - mn < 25:
        return ("⬜", "gray") if mx > 120 else ("⬛", "black")
    d = mx - mn
    if mx == r:
        hue = ((g - b) / d) % 6
    elif mx == g:
        hue = (b - r) / d + 2
    else:
        hue = (r - g) / d + 4
    hue = (hue * 60) % 360
    for lo, hi, name, emo in _HEX_COLOR_BANDS:
        if lo <= hue < hi:
            return (emo, name)
    return ("🟥", "red")


def _resolve_storage_target(t):
    """把用户说的 target（"红色箱子"/#hex/名字/"x,y"）解析成具体箱子坐标 {"x","y"}。
    统一查 scan 定位（避免 C# 颜色/名字匹配的歧义）；解析失败返回错误提示字符串。
    注意别过度剥"箱"："内置冰箱"→"内置冰"就匹配不上了，色名检测只剥一次、名字匹配用原始串。"""
    t = t.strip()
    if not t:
        return None
    # 坐标 "x,y"
    m = re.match(r"^\s*(\d+)\s*[,，]\s*(\d+)\s*$", t)
    if m:
        return {"x": int(m.group(1)), "y": int(m.group(2))}
    try:
        data = api._get("/scan_chests")
        chests = data.get("chests") or []
        # 十六进制
        if t.startswith("#") or re.match(r"^[0-9a-fA-F]{6}$", t):
            hexc = t if t.startswith("#") else "#" + t
            for c in chests:
                if (c.get("color") or "").lower() == hexc.lower():
                    return {"x": c["x"], "y": c["y"]}
            return f"❌ 当前场景没有 {hexc} 颜色的箱子"
        # 中文颜色名 / 名字子串 / 自动类目标签词
        raw = t.lower()
        core = raw.replace("箱子", "").replace("箱", "").strip()
        want = None
        for cn, key in _COLOR_NAME_SYNONYMS.items():
            if core == cn or core == key:
                want = key
                break
        # 🆕 2026-09-03 恒：认自动类目标签词（"矿箱"/"矿石"/"作物箱"→ 对应 autoTag 箱），AI 看标签定位箱
        want_bucket = None
        for bk, words in _AUTO_LABEL_WORDS.items():
            if any(w in raw or w in core for w in words):
                want_bucket = bk
                break
        picked = None
        for c in chests:
            dname = (c.get("name") or "").lower()
            if want is not None:
                _, cname = _hex_to_color_name(c.get("color", ""))
                if cname == want:
                    if picked is None:
                        picked = c
                    if c.get("freeSlots", 0) > 0:
                        picked = c  # 优先挑有空位的同色箱子
                        break
            elif want_bucket is not None:
                if want_bucket in {"矿", "古物", "鱼", "种子", "作物", "农产", "建材", "料理", "装备"}:
                    if (c.get("autoTag") or "") == want_bucket:
                        picked = c
                        if c.get("freeSlots", 0) > 0:
                            picked = c  # 优先挑有空位的同类目箱
                            break
            else:
                # 名字子串：原始串 或 剥"箱"后的串 命中都算（矿石箱/矿石/内置冰箱）
                if raw in dname or core in dname:
                    picked = c
                    break
        if picked is not None:
            return {"x": picked["x"], "y": picked["y"]}
        what = f"叫「{t}」" if want is None and want_bucket is None else (f"{core}颜色的" if want else f"{want_bucket}类的")
        return f"❌ 当前场景没有{what}的箱子（storage view 看看有哪些）"
    except Exception as e:
        return f"❌ 解析目标失败: {e}"


def _parse_store_spec(spec):
    """解析 storage store 的 what/items 参数 → (名字列表, counts dict)。
    spec: 逗号分隔字符串（项可带 xN/×N/*N 数量）/ 字符串列表 / [{name,count}] dict 列表。
    名字只留物品名（剥计数后缀）；counts[name]=N → 只存那 N 份、余量留背包（C# 拆堆）。"""
    if spec is None:
        return None, None
    if isinstance(spec, str):
        raw = [w for w in re.split(r"[,，;；]+", spec) if w.strip()]
    elif isinstance(spec, (list, tuple)):
        raw = spec
    else:
        return None, None
    names, counts = [], {}
    for w in raw:
        if isinstance(w, dict):
            nm = str((w.get("name") or w.get("item") or "")).strip()
            cnt = w.get("count", -1)
            if nm:
                names.append(nm)
                if isinstance(cnt, (int, float)) and cnt >= 0:
                    counts[nm] = int(cnt)
            continue
        w = str(w).strip()
        if not w:
            continue
        m = re.match(r"^(?P<n>.*?)[\s]*(?:x|×|\*)\s*(?P<c>\d+)$", w)
        if m:
            nm = m.group("n").strip()
            names.append(nm)
            counts[nm] = int(m.group("c"))
        else:
            names.append(w)
    return (names or None), (counts or None)
