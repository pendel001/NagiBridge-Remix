# -*- coding: utf-8 -*-
"""🌾 收获"漏一大片"的根 + 🎣 钓鱼仍超时的根 —— 纯 Python 自验。2026-11-07

恒：「我想问在刚刚的调用中，钓鱼怎么还会超时呀，不是异步嘛。然后 ai 自己用 harvest 的时候
怎么会漏这么多，是它自己没圈对地块嘛？后面胡萝卜等等是用 intent 收获的。」

🩸 **harvest 为什么漏**（session_log 12:25:56 / 09:54:41）：
   `scythe_crops.py` 发的是 `get("/surroundings", {"radius": args.radius + 5})`，
   而 C# 的半径解析是 `r > 0 && r <= 30`，**传 >30 不是截到 30、而是静默退回 10**！
   ⇒ `harvest radius=30`（30+5=35）实际只看 **±10 格** → 只找到 1 株；
     `radius=40`（45）→ 看到 0 株 → 回执「成熟 0 株」，AI 只能读成"没有可收的"
     （胡萝卜等后来是被 `intent` 的收获项扫到的）。
   ⇒ 改**矩形扫描**（`stardew_api.scan_rects_for`，不受半径 30 限制），并把覆盖范围写进日志。

🩸 **fish 为什么还超时**（12:34:55 实测 **68.6s**）：
   异步只包住**脚本那段**；`fish go` 前面还要**同步导航**（Farm→Town 走 60+ 格 ≈ 24s），
   于是「导航 24s + 挂 45s」= 68.6s ⇒ 前端 60s 照样掐。
   ⇒ 记下"这次调用几点开始"（`_CALL_T0`），`_bg_block_until_wake` 按**剩余额度**挂。

另外把 9 处 `startswith("🚀")` 全换成 `_async_park_reply`（`_bg_block_until_wake` 只回
"⏰/✅/🛑"，永远不回 "🚀" ⇒ 那 9 处判据从来没命中过 = **假完成**）。
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nagi_mcp_server as M
import stardew_api as A

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


try:
    # ── ① 矩形切块：覆盖范围 / 单块上限 ──────────────────────────
    print("\n① 矩形扫描切块：并集**正好覆盖**方框、每块 ≤4096、含中心")
    for r in (0, 15, 30, 31, 40, 48):
        rects = A.scan_rects_for(100, 80, r, cap=4096)
        area = sum((c - a + 1) * (d - b + 1) for a, b, c, d in rects)
        big = max((c - a + 1) * (d - b + 1) for a, b, c, d in rects)
        inside = all(100 - r <= a <= c <= 100 + r and 80 - r <= b <= d <= 80 + r for a, b, c, d in rects)
        ck(f"①r={r}：并集面积 = 方框面积（{(2*r+1)**2}）", area == (2 * r + 1) ** 2, f"{area}")
        ck(f"①r={r}：单块 ≤4096", big <= 4096, str(big))
        ck(f"①r={r}：块都在方框内", inside)
        ck(f"①r={r}：至少一块含中心 (100,80)",
           any(a <= 100 <= c and b <= 80 <= d for a, b, c, d in rects))
    ck("①r=30 只切 1 块（3721 格，装得下）", len(A.scan_rects_for(100, 80, 30)) == 1,
       str(A.scan_rects_for(100, 80, 30)))
    ck("①r=40 必须切多块（6561 > 4096）", len(A.scan_rects_for(100, 80, 40)) > 1,
       str(len(A.scan_rects_for(100, 80, 40))))

    # ── ② 脚本真的改用矩形扫、不再发"radius+5" ─────────────────
    print("\n② `scythe_crops.py` 主路径走矩形（半径模式只在读不到坐标的兜底里）")
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "scythe_crops.py"), encoding="utf-8").read()
    ck("②调了共用的切块函数", "scan_rects_for" in _src)
    ck("②主路径发矩形（`\"x1\"`）", '"x1": a' in _src, _src[:0])
    _bad = [ln for ln in _src.splitlines()
            if '"radius": args.radius + 5' in ln and not ln.strip().startswith("#")]
    # 只用**代码**判（docstring 里那段"当年是这么写的"不算）：ast 找 get(...) 的 radius 参数
    import ast as _ast
    _rad_calls = []
    for _node in _ast.walk(_ast.parse(_src)):
        if isinstance(_node, _ast.Call) and isinstance(_node.func, _ast.Name) and _node.func.id == "get":
            for _a in _node.args:
                if isinstance(_a, _ast.Dict) and any(
                        isinstance(k, _ast.Constant) and k.value == "radius" for k in _a.keys):
                    _rad_calls.append(_ast.unparse(_a))
    ck("②活代码里**不再**用半径模式扫作物（只允许 `rr + 5` 那条兜底）",
       all("args.radius + 5" not in c for c in _rad_calls), str(_rad_calls))
    ck("②兜底那条压在 ≤25（+5 不越 30）", "min(r, 25)" in _src)
    ck("②日志报出扫描覆盖范围", "扫描以 (" in _src)

    # ── ③ harvest 的异步判据不再判 "🚀" ──────────────────────────
    print("\n③ `harvest_crops` 收到「还在跑」时**不许**包成「收菜完成」")
    _run_bak = M._run_script
    _ws_bak = M._with_state
    M._with_state = lambda s: s
    M._run_script = lambda *a, **k: "⏰ 唤醒时间到了——脚本「scythe_crops」还在后台跑（45s，job job9）"
    _o3 = M.harvest_crops(radius=15)
    ck("③原样交出去（含 ⏰）", "⏰" in _o3, _o3[:160])
    ck("③**没有**「收菜完成」这句假完成", "收菜完成" not in _o3, _o3[:160])
    M._run_script = _run_bak

    # ── ④ 挂住要扣掉"这次调用已经花掉的时间" ────────────────────
    print("\n④ 导航吃掉的时间要从挂住额度里扣（fish go = 导航 + 脚本）")

    class _Job:
        job_id = "jobTEST"
        name = "fish_run"
        running = True
        output = []
        finish_announced = False
        killed = False
        start_ts = time.time()

        def _tail(self, n=200):
            return ""

    _slice_bak = M._BG_PARK_SLICE
    _wake_bak = M._bg_wake_text
    _cfg_bak = dict(M._bg_cfg)
    try:
        M._BG_PARK_SLICE = 10
        M._bg_cfg["wake_interval"] = 180
        M._bg_wake_text = lambda j: "⏰ 唤醒(假)"
        M._CALL_T0.set(0.0)
        t = time.time()
        _r0 = M._bg_block_until_wake(_Job())
        _d0 = time.time() - t
        ck("④没打点时：照常挂满封顶（10s 那档）", _d0 >= 8.5 and _r0 == "⏰ 唤醒(假)", f"{_d0:.1f}s")
        M._CALL_T0.set(time.time() - 9)      # 前面导航已经花了 9s
        t = time.time()
        _r1 = M._bg_block_until_wake(_Job())
        _d1 = time.time() - t
        ck("④打了点（已花 9s）：只挂剩下的 ≈1s（下限 3s）",
           _d1 <= 5.0 and _r1 == "⏰ 唤醒(假)", f"{_d1:.1f}s")
        M._CALL_T0.set(time.time() - 999)    # 早就超了
        t = time.time()
        M._bg_block_until_wake(_Job())
        ck("④已经超了：只留 3s 下限（别零等待弹唤醒）", time.time() - t <= 5.0,
           f"{time.time()-t:.1f}s")
    finally:
        M._BG_PARK_SLICE = _slice_bak
        M._bg_wake_text = _wake_bak
        M._bg_cfg.clear()
        M._bg_cfg.update(_cfg_bak)
        M._CALL_T0.set(0.0)

    # ── ⑤ 九个调用点都不再自己判 "🚀" ───────────────────────────
    print("\n⑤ 源码里 `startswith(\"🚀\")` 的调用点全部改用 `_async_park_reply`")
    _ms = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "nagi_mcp_server.py"), encoding="utf-8").read()
    import ast as _ast2
    _live = []
    for _node in _ast2.walk(_ast2.parse(_ms)):
        # 抓 `X.startswith("🚀")` 这种**真的在跑**的代码（docstring/注释里提到不算）
        if (isinstance(_node, _ast2.Call) and isinstance(_node.func, _ast2.Attribute)
                and _node.func.attr == "startswith"
                and any(isinstance(a, _ast2.Constant) and a.value == "🚀" for a in _node.args)):
            _live.append(_ast2.unparse(_node))
    ck("⑤没有活代码还在判 \"🚀\"（注释/docstring 不算）", not _live, str(_live[:3]))
    ck("⑤`_async_park_reply` 被多处调用", _ms.count("_async_park_reply(") >= 10,
       str(_ms.count("_async_park_reply(")))
finally:
    try:
        M._run_script = _run_bak
        M._with_state = _ws_bak
    except Exception:
        pass

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 收获不再瞎扫（矩形覆盖）、钓鱼不再因导航超时、异步回包不再被当成完成。")
