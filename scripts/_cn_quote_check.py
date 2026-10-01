# -*- coding: utf-8 -*-
"""🔎 中文文案里的**嵌套直引号**扫描器 —— 把"提醒自己别写错"换成"机械地查出来"。

## 为什么要有它

`chinese-strings-use-corner-quotes-2026-09-29.md` 这条笔记记了 **4 次**（合计 11 处）。
每次的症状一模一样：想在中文句子里**强调一个词**，手就打出直的 `"`，
**外层 Python 字符串当场被截断** ⇒ `SyntaxError`，而报错指向的行**看着完全正常**。

⚠️ 关键：**它只有在那一行被解析到的时候才炸** —— 所以"跑一遍自验"能发现，
但**代价是十几秒到几分钟**，而且长输出里容易被淹掉。**第 11 次之后我决定不再靠记。**

## 它怎么查（比"记得用「」"可靠）

不看语法（语法错的文件压根 import 不了），而是**静态扫字符**：
在一行里找 `"` 或 `'` 出现在**中文字符旁边**的形状 —— 也就是
`中文字 + 引号` / `引号 + 中文字`（含全角标点相邻）。

⚠️ **只报可疑，不自动改**：这一层给的是"去看一眼这个位置"，改不改由人定
（有些是合法的，比如 f-string 里嵌 `{"x":1}`、或者引号正好贴着中文标点收尾）。

## 用法

    python scripts/_cn_quote_check.py                 # 扫全仓 .py
    python scripts/_cn_quote_check.py 文件1 文件2      # 只扫指定文件

退出码：0 = 没发现 / 1 = 有可疑处（**红的**，去人看一眼）。
"""
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 中文字符
_CN = r"[\u4e00-\u9fff]"
# ⚠️⚠️ **真正的病有精确签名：出错的引号两边都是中文。**
#    11 次实测的样本全符合：`印"还` · `拟"看` · `有"敲` · `清"为` · `报"身` …
#    （而正常的"字符串收尾"引号后面跟的是 `,`/`)`/`：`/行尾 —— **不是中文**。）
#
# ⚠️ **只盯双引号 `"`，不盯单引号**（第五版教训）：这个仓库的中文串一律写成 `"..."`，
#    而 `'...'` 是**里面那层**（`选'是'花500g` —— 单引号套在双引号串里**完全合法**）。
#    把 `'` 也算进来 ⇒ 25 处全是这种噪声。11 次真样本**没有一次**是单引号。
#    （代价：万一哪天在 `'...'` 里嵌了单个 `'`，这个工具抓不到 —— 但 `ast.parse` 抓得到。）
_SUSPECT = re.compile(rf'{_CN}"{_CN}')

# 明确合法的豁免：纯注释行（语法上无害）
_SKIP_LINE = re.compile(r"^\s*#")


def _scan_seg(seg: str, ln: int, line: str, out: list):
    for m in _SUSPECT.finditer(seg):
        out.append((ln, line.strip(), m.group(0)))


def _strip_comment(line: str) -> str:
    """去掉**行尾注释** —— 但要跳过字符串里的 `#`。

    ⚠️ 第三版教训：收紧到"两边都是中文"之后还剩 128 处，**全是行尾注释**
       （`# 明确"回家"→推门进屋就停`）—— 注释里写引号完全合法。
       只删"行首是 `#`"的行不够，得连**行尾**那段一起切掉，而切之前必须知道
       自己是不是在某对引号里面（否则 `"颜色 #FF0000"` 那种会被腰斩）。
    """
    q, i = None, 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "#":
            return line[:i]
        i += 1
    return line


def scan(path: str) -> list:
    """→ [(行号, 那一行, 可疑片段)]

    ⚠️ **必须跳过三引号区**（第二版教训）：三引号 docstring 里写「与"某个分支"共用」
       是**完全合法**的（三引号里单个直引号不截断任何东西）。不跳的话 1386 处噪声里 99% 是这种。
       📌 **写这一行的时候我自己把坑又踩了一遍** —— 原话里嵌了三个连续直引号，
       把外层 docstring 当场闭合、`SyntaxError: invalid character '…'`。
       **这条笔记本身就是这个 bug 的又一个样本。**
    ⚠️ 这里的三引号跟踪是**近似**的（按行找 `\"\"\"` / `'''`，不处理"三引号出现在单引号串里"
       这种罕见嵌套）。**它是"提前提醒"，不是判据的正主** ——
       正主永远是 `ast.parse`（这个 bug 一定是 SyntaxError，那个 100% 准且零误报）。
    """
    out = []
    try:
        src = open(path, encoding="utf-8").read().splitlines()
    except Exception as e:
        return [(0, f"（读不了：{e}）", "")]
    in_triple = None
    for i, line in enumerate(src, 1):
        if _SKIP_LINE.match(line):
            continue                      # 纯注释行：语法上无害，不报（噪声太大）
        seg = line
        while True:
            if in_triple:
                k = seg.find(in_triple)
                if k < 0:
                    break                 # 整行都在三引号里
                seg = seg[k + 3:]
                in_triple = None
                continue
            # ⚠️ **切注释只能在不处于三引号里的时候做**（第四版教训）：
            #    docstring 正文里写个 `#` 是很常见的，先切注释会把 `"""` 一起切掉，
            #    状态机当场失准 ⇒ 剩下 83 处全是 docstring 正文的误报。
            seg = _strip_comment(seg)
            if not seg:
                break
            # 不在三引号里：本行可能"开"一个三引号 ⇒ 只扫开口**之前**那一段
            cut, opened = len(seg), None
            for q in ('"""', "'''"):
                j = seg.find(q)
                if 0 <= j < cut:
                    cut, opened = j, q
            _scan_seg(seg[:cut], i, line, out)
            if opened is None:
                break
            rest = seg[cut + 3:]
            k = rest.find(opened)
            if k >= 0:                    # 同一行开又关（一行 docstring）⇒ 接着扫后面的
                seg = rest[k + 3:]
                continue
            in_triple = opened
            break
    return out


# ── 自证会咬人（恒的规矩：**没咬过人的护栏等于没有**）────────────────────
# 每一条都是真样本或真豁免；跑一次全对才说明判据没跑偏。
# ⚠️ 这里的片段一律用**单引号**包（`'...'`），别在 checker 自己身上再踩一次那个坑。
_CASES = [
    ("嵌套直引号（**真病**，第 11 次的原样）",
     'ok.append(("🛏 直说「已经满了」（不印"还差 N 分钟"）",\n', True),
    ("嵌套直引号（**真病**，`_can` 那次）",
     '_can = "关掉界面" if x else "（这个界面没有"敲一下就好"的出口）"\n', True),
    ("三引号 docstring 里写引号（**合法**）",
     '"""与"没传 rect 的分支"共用同一份 IsWaterTarget。"""\n', False),
    ("行尾注释里写引号（**合法**）",
     'x = 1  # 明确"回家"→推门进屋就停\n', False),
    ("单引号套在双引号串里（**合法**）",
     "n = \"交互选'是'花500g\"\n", False),
    ("双引号收尾、后面是中文标点（**合法**）",
     'a = "农活域（farm 域）"\n', False),
]


def _prove() -> int:
    """把 `_CASES` 逐条写进临时文件再扫 —— 应报的必须报、合法的一个都不许报。"""
    import tempfile
    bad = 0
    for name, snippet, want in _CASES:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as f:
            f.write(snippet)
            p = f.name
        try:
            got = bool(scan(p))
        finally:
            os.unlink(p)
        mark = "✅" if got is want else "❌"
        if got is not want:
            bad += 1
        print(f"  {mark} {name} —— 应{'报' if want else '不报'}，实{'报' if got else '不报'}")
    if bad:
        print(f"\n❌ 自证 {bad}/{len(_CASES)} 条不对 —— **判据本身有问题**，先修它")
    else:
        print(f"\n✅ 自证 {len(_CASES)}/{len(_CASES)}：真病会报、合法的不误报")
    return bad


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    print("── 自证（先证明它会咬人）──")
    if _prove():
        return 1
    print("\n── 扫全仓 ──")
    targets = argv[1:]
    if not targets:
        targets = []
        for d in (here, root):
            for f in sorted(os.listdir(d)):
                if f.endswith(".py"):
                    _p = os.path.join(d, f)
                    # ⚠️ **跳过它自己**：`_CASES` 里故意躺着两段"真病"样本，
                    #    扫自己必然报 —— 那不是代码有病，是夹具（同 linter 忽略自己的 fixtures）。
                    if os.path.abspath(_p) == os.path.abspath(__file__):
                        continue
                    targets.append(_p)
        # 仓库根目录只看自己那几个（不递归 decomp/ 之类的第三方）
    bad = 0
    for p in targets:
        hits = scan(p)
        if not hits:
            continue
        bad += len(hits)
        rel = os.path.relpath(p, root)
        print(f"\n⚠️ {rel}")
        for ln, text, frag in hits:
            print(f"   L{ln}: …{frag}…")
            print(f"        {text[:110]}")
    if bad:
        print(f"\n❌ 可疑 {bad} 处 —— 去人看一眼（**多数是嵌套直引号，要改成「」**）；"
              f"确认合法的就忽略。")
        return 1
    print("✅ 没发现中文旁边的直引号")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
