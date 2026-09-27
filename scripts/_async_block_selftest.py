"""⏰ 异步**阻塞到唤醒** —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒原话：「异步好像没做好阻塞，然后 AI 看到『脚本已经在后台跑』**就结束这次对话了**。
这不是我本意……我是希望**阻塞到唤醒**，然后 AI 整理背包，`continue` 继续。」

死的部分：老版 `_run_script` 一转后台**立刻 return** ⇒ AI 这一回合当场结束；
而"唤醒"那套（状态条里的 ⏰ 提示）是**塞在下一次工具调用里的** —— AI 已经没有下一次调用了
⇒ **整套被动异步永远等不到那次唤醒**。

测四件：
  ① 脚本还在跑 → 这一挂**必须挂满唤醒间隔**才返回，且返回的是唤醒文案（不是"已后台启动"）
  ② 挂到一半脚本收工 → 立刻返回**收工播报**（含脚本原话），且标记"已播过"（状态条不许再播一遍）
  ③ `script continue` 的语义就是"**接着挂**"（没脚本在跑时不许挂，要立刻如实说）
  ④ 静默后台的 `CREATE_NO_WINDOW` 别被人手滑删掉（恒：「启动脚本会弹窗」）
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def mkjob(running=True, name="fish_run"):
    """手搓一个 _BgJob（不真起进程），字段照 _bg_reader 收工时的样子摆。"""
    j = M._BgJob(name, [])
    j.proc = None
    j.running = running
    j.start_ts = time.time() - 5
    j.end_ts = None if running else time.time()
    j.returncode = None if running else 0
    j.output = ["[fish] 达到 2 竿", "[fish] no-sleep: 留在钓点不睡觉"]
    return j


_real = {n: getattr(M, n) for n in ("_with_state", "_bg_jobs", "_bg_cfg")}
try:
    M._with_state = lambda s: s
    M._bg_cfg = dict(M._bg_cfg)
    M._bg_cfg["wake_interval"] = 2          # 尺子上挂 2 秒就够，别真等 60
    M._bg_cfg["enabled"] = True

    print("\n① 脚本还在跑 → 挂满唤醒间隔，回的是唤醒文案")
    j = mkjob(running=True)
    M._bg_jobs = {j.job_id: j}
    t0 = time.time()
    out = M._bg_block_until_wake(j)
    dt = time.time() - t0
    ck("**真的挂住了**（≥ 唤醒间隔）", dt >= 1.9, f"{dt:.1f}s")
    ck("回的是「唤醒」而不是「已后台启动」（后者=把 AI 放走了）",
       "唤醒时间到了" in out and "已后台启动" not in out, out)
    ck("…并说清现在能做什么（整理背包）", "整理背包" in out, out)
    ck("…并给下一步：`script continue` 接着挂", "continue" in out, out)

    print("\n② 挂着挂着脚本收工 → 立刻回收工播报（不白等）")
    j2 = mkjob(running=True)
    M._bg_jobs = {j2.job_id: j2}
    j2.running = False
    j2.returncode = 0
    j2.end_ts = time.time()
    t0 = time.time()
    out2 = M._bg_block_until_wake(j2)
    dt2 = time.time() - t0
    ck("收工了就不再挂满（立刻返回）", dt2 < 1.5, f"{dt2:.1f}s")
    ck("回的是**收工播报**（✅ + 跑了Xs + 脚本原话）",
       "收工" in out2 and "跑了" in out2 and "达到 2 竿" in out2, out2)
    ck("标记已播过 → 状态条不会再播第二遍", j2.finish_announced is True)

    print("\n③ script continue = 接着挂")
    M._bg_jobs = {}
    t0 = time.time()
    out3 = M._script_continue()
    ck("没脚本在跑 → **立刻**如实说（不许空挂）",
       time.time() - t0 < 1.0 and "没有在跑的脚本" in out3, out3)

    j3 = mkjob(running=True)
    M._bg_jobs = {j3.job_id: j3}
    t0 = time.time()
    out4 = M._script_continue()
    dt4 = time.time() - t0
    ck("有脚本在跑 → **continue 也真的挂住**（老版只回一句「阻塞中」就交还控制权）",
       dt4 >= 1.9 and "唤醒时间到了" in out4, f"{dt4:.1f}s / {out4[:60]}")

    print("\n③b ⚠️ 持 _bg_lock 时调 _with_state = **自锁死**（`Lock()` 不可重入，整个服务僵住）")
    # ⚠️ 为什么非要有这条：上面 ③ 把 `_with_state` 换成了恒等函数，**恰好把自锁死遮住了**
    #    （真机/静态检查抓到的是 `_script_continue` 在锁里 `return _with_state(...)`）。
    #    这里让 `_with_state` 走**真链**的一环（状态条 → `_bg_activity_line` → `with _bg_lock`），
    #    用看门狗线程跑一次「没有脚本在跑」那条最常走的路。
    #    （`domain_selftest.py` 另有静态版；两条都留着——静态的便宜，动态的才是出事时先红的。）
    import threading as _th
    _real_ws = M._with_state
    M._with_state = lambda s: s + M._bg_activity_line()   # 真链：状态条会去拿 _bg_lock
    M._bg_jobs = {}
    _out = {}
    _t = _th.Thread(target=lambda: _out.update(r=M._script_continue()), daemon=True)
    _t.start()
    _t.join(3.0)
    ck("没有脚本在跑时 `script continue` **不僵住**（锁里只准备话，_with_state 在出锁之后）",
       not _t.is_alive() and "没有在跑的脚本" in _out.get("r", ""), str(_out))
    M._with_state = _real_ws

    print("\n③c ⏰ 同一次调用**别吐两遍**（恒 2026-09-25 截图：唤醒文案 + 状态条那条几乎一字不差）")
    # ⚠️ 根因：状态条那条按"距上次 AI 操作 ≥ wake_interval"限频，而这次调用**被自己阻塞了一整轮**
    #    ⇒ 在它眼里 AI"刚好闲了一轮"（挂住 ≠ 操作）。修 = 挂完把限频时钟拨到当下。
    # ⚠️ 先验**尺子**：不拨钟时它确实会啰嗦（否则这条测试是空的 —— 同 ③b 那条教训）。
    j5 = mkjob(running=True)
    M._bg_jobs = {j5.job_id: j5}
    M._bg_last_ai_activity = time.time() - 10     # 模拟"AI 刚挂完一轮，距上次操作早就超时了"
    M._bg_last_wake = 0
    base_line = M._bg_activity_line()
    ck("（尺子）不拨钟时，状态条那条**确实**会再来一遍", bool(base_line), repr(base_line[:40]))
    M._bg_last_wake = 0
    M._bg_last_ai_activity = time.time() - 10
    M._bg_block_until_wake(j5)                    # 挂满一个间隔，返回唤醒文案（= 已经说过了）
    ck("…挂完立刻再取状态条 → **闭嘴**（同一件事不重复）",
       M._bg_activity_line() == "", repr(M._bg_activity_line()[:40]))

    print("\n④ 静默后台：别把 CREATE_NO_WINDOW 弄丢（恒：启动脚本会弹窗）")
    if os.name == "nt":
        ck("Windows 上 _SUBPROC_FLAGS = CREATE_NO_WINDOW",
           M._SUBPROC_FLAGS == 0x08000000, hex(M._SUBPROC_FLAGS))
    else:
        ck("非 Windows → 0（不传无效标志）", M._SUBPROC_FLAGS == 0, str(M._SUBPROC_FLAGS))

    print("\n⑤ wake=N 可调（它同时也是单次工具调用的时长）")
    for bad, why in ((3, "太小"), (999, "太大")):
        o = M.async_config(wake=bad)
        ck(f"wake={bad}（{why}）→ 拒绝并说明范围", "5~600" in o, o)
    M._settings_save = lambda: None
    o = M.async_config(wake=45)
    ck("wake=45 → 接受并回执", "45" in o, o)
    ck("…且真的写进了配置", M._bg_cfg["wake_interval"] == 45, str(M._bg_cfg["wake_interval"]))

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
