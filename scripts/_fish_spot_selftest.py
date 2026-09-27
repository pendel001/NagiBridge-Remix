"""🎣 钓点名 / 脚本秒退 / 剧情推进不重复 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

起因（恒真机）：AI 想钓鱼，调 `fish(ops="go", kw={"location":"River"})`。
"River" 不是校准钓点 ⇒ `fish_run` 打一行 `unknown fishing spot: River` 就 `return`
⇒ 子进程 **0 秒正常退出(rc=0)** ⇒ 后台包装照样播报「✅ 脚本「fish_run」收工（跑了 0s）」。
AI 眼里就是"脚本闪退了"，而且**连脚本自己说的那句话都看不到**（播报只有一行 ✅）。

测四件（都是判据，不是"跑一遍看看"）：
  ① `go_fishing` 的 location **当场校验**：认不出 → 报错并**列出能用的 + 怎么就地钓**，
     且**绝不把没见过的名字透传给脚本**（这次事故的根：透传 = 把错误变成"静默无事发生"）
  ② 归一：大小写 / POI 全名 / 就地钓别名（here·就地·原地）各落到该走的路
  ③ `_bg_activity_line` 收工播报：**秒退不许报 ✅**，且必须带出脚本原话
  ④ `advance_story` 的 result **不复述台词**（台词只由状态条那条给），但**选项必须留**
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # import 安全：末尾才 if __name__ == "__main__"

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 假 api：只回答这次要问的 ─────────────────────────────────────────────
class FakeApi:
    def __init__(self, state=None):
        self._state = state or {}
        self.posts = []
        self._water = None          # 打桩：`/water` 报的水格（None=端点没回）

    def state(self, **kw):
        return self._state

    def water_tiles(self, x=None, y=None, radius=15):
        return self._water

    def _post(self, endpoint, data=None, **kw):
        self.posts.append((endpoint, data))
        return {"ok": True}

    def key(self, k):
        self.posts.append(("key:" + k, None))
        return {"ok": True}


_real = {n: getattr(M, n) for n in ("api", "_with_state", "map_go", "_run_script", "_ai_port")}
try:
    M._with_state = lambda s: s                 # 去掉状态条拼接，只看 result 本身
    M._ai_port = lambda: 7843

    def _fresh(state=None):
        """每轮重置：假 api + 记录 map_go / _run_script 被怎么调的。"""
        calls = {"map_go": [], "script": []}
        M.api = FakeApi(state)
        M.map_go = lambda poi: (calls["map_go"].append(poi), "🗺️ 已到 " + poi)[1]
        M._run_script = lambda name, args, **kw: (
            calls["script"].append((name, list(args))), "🚀 已后台启动")[1]
        return calls

    print("\n① go_fishing 的 location 当场校验（认不出就报错，绝不透传）")
    calls = _fresh()
    out = M.go_fishing(location="River")
    ck("River → 报「认不出」，且列出 Beach/Mountain/Forest/Town",
       "认不出" in out and all(k in out for k in ("Beach", "Mountain", "Forest", "Town")), out)
    ck("River → 明确给出**下一步**（就地钓怎么说）", "fish go" in out and "不带" in out, out)
    ck("River → **一个脚本都没启动**（错名不许透传进脚本）", calls["script"] == [], str(calls["script"]))
    ck("River → 也没瞎走位（map_go 没被调）", calls["map_go"] == [], str(calls["map_go"]))

    print("\n② 归一：大小写 / POI 全名 / 就地钓别名")
    calls = _fresh()
    out = M.go_fishing(location="beach")
    ck("小写 beach → 认到 Beach 并 map_go 到它的 POI",
       calls["map_go"] == ["海滩钓鱼点(码头)"], str(calls["map_go"]))
    ck("小写 beach → 脚本**不带** --location（已到点，就地钓）",
       "--location" not in calls["script"][0][1], str(calls["script"]))
    ck("小写 beach → 抛竿朝向面下(2)也设了", ("/face", {"direction": 2}) in M.api.posts, str(M.api.posts))

    calls = _fresh()
    M.go_fishing(location="海滩钓鱼点(码头)")
    ck("POI 全名 → 同样认到 Beach", calls["map_go"] == ["海滩钓鱼点(码头)"], str(calls["map_go"]))

    for alias in ("就地", "here", "原地"):
        calls = _fresh()
        M.go_fishing(location=alias)
        ck(f"「{alias}」→ 就地钓（不走位、脚本不带 --location）",
           calls["map_go"] == [] and "--location" not in calls["script"][0][1], str(calls))

    calls = _fresh()
    M.go_fishing()                       # 不传 location
    ck("不传 location → 就地钓", calls["map_go"] == [] and "--location" not in calls["script"][0][1],
       str(calls))
    ck("就地钓也会带 --port（防挪恒角色）", "--port" in calls["script"][0][1], str(calls["script"]))

    print("\n③ 收工播报：秒退不许报 ✅，且要带出脚本原话")
    _real_bg = M._bg_jobs
    try:
        j = M._BgJob("fish_run", ["--port", "7843"])
        j.proc = None
        j.running = False
        j.returncode = 0
        j.start_ts = time.time() - 0.4
        j.end_ts = time.time()
        j.output = ["[fish] === fish run: 当前站位 ===",
                    "[fish] unknown fishing spot: River, known: ['Beach', 'Mountain', 'Forest', 'Town']"]
        M._bg_jobs = {j.job_id: j}
        line = M._bg_activity_line()
        ck("0.4s 退出 + rc=0 → 报「刚起就退了」，不是 ✅",
           "刚起就退" in line and "✅" not in line, line)
        ck("…并把脚本原话带出来（这句才是 AI 能自救的信息）",
           "unknown fishing spot: River" in line, line)

        j2 = M._BgJob("fish_run", [])
        j2.proc = None
        j2.running = False
        j2.returncode = 0
        j2.start_ts = time.time() - 120
        j2.end_ts = time.time()
        j2.output = ["[fish] 钓上 3 条"]
        M._bg_jobs = {j2.job_id: j2}
        line2 = M._bg_activity_line()
        ck("跑了 120s 的正常收工 → 照旧 ✅（别把正常活儿吓成警告）",
           "✅" in line2 and "刚起就退" not in line2, line2)

        jk = M._BgJob("fish_run", [])
        jk.proc = None
        jk.running = False
        jk.returncode = 1
        jk.killed = True                 # script stop 停的：stop 的回包已摊了 60 行输出
        jk.start_ts = time.time() - 30
        jk.end_ts = time.time()
        jk.output = ["[fish] 正在抛竿…"]
        M._bg_jobs = {jk.job_id: jk}
        linek = M._bg_activity_line()
        ck("被停的脚本 → 报「是被停的」但**不再重复**脚本原话（stop 的回包已经给过）",
           "被停" in linek and "脚本原话" not in linek, linek)

        jr = M._BgJob("fish_run", [])
        jr.proc = None
        jr.running = False
        jr.returncode = 0
        jr.start_ts = time.time() - 27
        jr.end_ts = time.time()
        # 真机那次：最后两行是收尾套话，「达到 2 竿」被挤到第三行外 ⇒ 恒只能问"是你停的吗"
        jr.output = ["[fish] 监控循环…", "[fish] 达到 2 竿",
                     "[fish] no-sleep: 留在钓点不睡觉", "[fish] === fish run complete ==="]
        M._bg_jobs = {jr.job_id: jr}
        liner = M._bg_activity_line()
        ck("「为什么收的手」不许被收尾套话挤出窗口（「达到 2 竿」要看得见）",
           "达到 2 竿" in liner, liner)

        j3 = M._BgJob("fish_run", [])
        j3.proc = None
        j3.running = False
        j3.returncode = 0
        j3.start_ts = time.time() - 1
        j3.end_ts = time.time()
        j3.output = ["x" * 900]
        M._bg_jobs = {j3.job_id: j3}
        line3 = M._bg_activity_line()
        ck("脚本吐超长行 → 原话被截断（别把长输出整段甩给 AI）",
           len(line3) < 400 and line3.endswith("…"), str(len(line3)))
    finally:
        M._bg_jobs = _real_bg

    print("\n④ fish info —— 「我想去河流钓」要能一路接到命令上")
    M.api = FakeApi(state={"activeMenu": None})
    out = M._fish_info(location="Town")
    ck("Town 是河流 → **点名校准钓点**并给可复制的命令",
       "镇鲶鱼钓点" in out and "fish go location=Town" in out, out)
    ck("…坐标也给出来（别让 AI 再问一遍）", "(3,93)" in out or "(3, 93)" in out, out)

    out = M._fish_info(location="town")
    ck("小写 town 也认（真机 AI 传过 location=river，直接查无此地）",
       "镇鲶鱼钓点" in out, out)

    out = M._fish_info(location="Sewer")
    ck("没有校准钓点的地方 → **直说**「得自己 map go 走到水边再 fish go」",
       "没有校准钓点" in out and "map go" in out, out)

    out = M._fish_all_spots()
    ck("spots 总览：点明**只有四个能自动导航**", "Beach / Mountain / Forest / Town" in out, out)

    print("\n⑤ advance_story：result 不复述台词（台词只由状态条给），但选项必须留")
    _real_buf, _real_note, _real_adv = M._story_buffer, dict(M._ADV_NOTE), M._advance_story
    try:
        def _fake_adv(m, ev):
            M._story_buffer.append("威利「给，这条旧鱼竿就送给你好了。」")
            M._ADV_NOTE.update({"pushed": 12, "lines": 7, "cmd": 34, "cmd_count": 61, "stuck": False})
            return True

        M._advance_story = _fake_adv
        M._story_buffer = []
        M.api = FakeApi(state={"activeEvent": {"id": "739330"},
                               "activeMenu": {"type": "DialogueBox"}})
        # ⚠️ 必须走 **AI 真正的那条路**（`menu(ops="advance")`）：直接调 `advance_story()` 会先撞
        #    菜单闸门（DialogueBox 开着），测到的是闸门不是本函数。
        out = M.menu(ops="advance")
        ck("事件还在播 → result 只说「已推进 + 进度 + 下一步」",
           "已推进" in out and "34/61" in out and "menu advance" in out, out)
        ck("…**没有**把台词抄第二遍（那句台词只在状态条出现）",
           "旧鱼竿" not in out, out)
        ck("…也报出收了 7 句（沉默=以为它没干活）", "7" in out, out)

        M._story_buffer = []
        M.api = FakeApi(state={"activeEvent": {},
                               "activeMenu": {"type": "DialogueBox",
                                              "responses": [{"index": 0, "key": "a", "text": "好"},
                                                            {"index": 1, "key": "b", "text": "算了"}]}})
        out = M.menu(ops="advance")
        ck("出现选项 → **必须留在 result**（状态条不报选项，AI 要当场做动作）",
           "选项" in out and "[0]" in out and "menu click(option=N)" in out, out)
    finally:
        M._story_buffer, M._advance_story = _real_buf, _real_adv
        M._ADV_NOTE.update(_real_note)

    print("\n⑥ 体力闸：低于钓鱼线就别开钓（恒：「钓到 15 体力了，低于 20 不拦吗」）")
    # 脚本自己会在 <20 时收手 ⇒ 这个体力开钓最多抛一竿就被自己停掉，白跑一趟。
    # 判据与脚本**同一份**（`fish_run.MIN_STAMINA`），别在这儿另写一个数。
    calls = _fresh({"player": {"stamina": 15, "maxStamina": 270}})
    out = M.go_fishing()
    ck("体力 15 < 钓鱼线 → 当场拦，**一个脚本都没启动**",
       "不开" in out and calls["script"] == [], out)
    ck("…给下一步（吃/温泉/睡）", "eat" in out and "温泉" in out, out)
    ck("…且拦在走位**之前**（别先让人跑到钓点再说不行）", calls["map_go"] == [], str(calls["map_go"]))

    calls = _fresh({"player": {"stamina": 300, "maxStamina": 300}})
    out = M.go_fishing()
    ck("体力够 → 照常开钓（闸门不是路障）", calls["script"] != [], out)

    calls = _fresh({})
    out = M.go_fishing()
    ck("读不到体力 → **不拦**（读不到 ≠ 没体力，别把「我瞎了」变成「路不通」）",
       calls["script"] != [], out)

    print("\n⑦ 到点校验：`map_go` 说到了 ≠ 人站在钓点上（恒：「是不是路途太遥远了」）")
    # 真机现场：人在书摊那片山坡（Town 114,17），走到 (3,93) 一百多格、20s 走不完，
    # 而 map_go 从前**谎报"已走到"** ⇒ 就地开钓 ⇒ 鱼机朝**走路方向**抛竿 ⇒「抛竿方向没有水」。
    _STAM = {"stamina": 300, "maxStamina": 300}
    calls = _fresh({"player": dict(_STAM, x=68, y=74)})
    out = M.go_fishing(location="Town")
    ck("人在半路 (68,74) → **不开钓**，且说清为什么",
       "还没站到钓点" in out and calls["script"] == [], out)
    ck("…给下一步（再 fish go / 自己 map go 走过去）",
       "fish go location=Town" in out and "map go" in out, out)

    calls = _fresh({"player": dict(_STAM, x=3, y=93)})
    out = M.go_fishing(location="Town")
    ck("人真在钓点 (3,93) → 照常开钓", calls["script"] != [], out)

    calls = _fresh({"player": dict(_STAM)})       # 读不到坐标
    out = M.go_fishing(location="Town")
    ck("读不到坐标 → **不拦**（读不到 ≠ 没到）", calls["script"] != [], out)

    print("\n⑧ 就地钓要把脸转向水（恒：「见它每次都**朝向错**报面前没水」的另一条根因）")
    # 真机那一刻：人就站在镇鲶鱼钓点 (3,93)、正下方 (3,94) 就是水，可脸朝着**来路**（走路方向）
    # ⇒ 鱼机朝陆地抛 ⇒「🚫 抛竿方向没有水」。站位全对、只有脸错。
    calls = _fresh({"player": dict(_STAM, x=3, y=93)})
    M.api._water = [{"x": 3, "y": 94}, {"x": 3, "y": 95}]
    out = M.go_fishing()                     # 就地钓
    ck("就地钓前**转了脸**（/face 2=下，朝最近的水）",
       ("/face", {"direction": 2}) in M.api.posts, str(M.api.posts))
    ck("…并在回包里说清朝哪了", "已朝下" in out, out)

    calls = _fresh({"player": dict(_STAM, x=3, y=93)})
    M.api._water = []
    out = M.go_fishing()
    ck("附近没水 → **不乱转**（如实交给脚本去报「没有水」）",
       not any(p[0] == "/face" for p in M.api.posts), str(M.api.posts))
finally:
    for k, v in _real.items():
        setattr(M, k, v)

print("\n" + ("=" * 46))
print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
