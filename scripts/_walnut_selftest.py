"""🌰 姜岛金核桃（附近提示 + `scene walnut` 拿核桃）—— 纯 Python 自验（不起服务、不碰游戏）。2026-09-25

恒：「你打算做姜岛金核桃收集吗？…挖核桃就是人类受罪然后 AI 也毫无参与感。让 AI 也参与进去
帮忙找些。AI 应该参与不了弹弓的金核桃，但是**摇树的、挖掘的应该都可以弄到**。
**附近半径 9 格内有金核桃没获取就可以显示给 AI 去找**。」

两半各验法不同：
  ① `_walnut_hint`（状态条那条提示）—— **单测**，打桩 `api` 只喂一个 `/nuts` 返回值。
  ② `walnut_run.py`（`scene ops=walnut` 真去拿）—— **起一个假 NagiBridge HTTP 服务，真跑一遍
     `main()`**。这个脚本全是"走位→动手→回读确认→如实报"的分支，光测纯函数测不到它；
     假服务能让"挖了但没成"和"成了"两条路都真的走一遍。

判据钉死这几条（都是这个仓栽过的）：
  · `/nuts` 必须打 **AI 那端**（`_ai_get`），不能 `_get` —— 否则报的是**恒脚边**的核桃。
  · 一次只报**最近的一个**；提过还没拿的**不重复提**；换天清账。
  · 内层(_OPS_INNER)闭嘴、脚本跑着不提、**且都不消费**这次机会。
  · 非姜岛不提。
  · 脚本**只认回读**：`/nuts` 那点没翻成 taken 就**如实报失败**，不许因为端点回了 ok 就说拿到。
  · 背包没锄头 → 埋点那条路**明确拒绝 + 说下一步**，且**真的没去动手**。
"""
import os
import sys
import json
import threading

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ══════════════════════════════════════════════════════════════════
# ① 状态条那条提示
# ══════════════════════════════════════════════════════════════════
print("\n① 🌰 附近提示：只在姜岛 / 只报最近一个 / 提过不再提 / 换天清账")


class NutsApi:
    def __init__(self, nuts, ok=True):
        self.nuts, self.ok, self.calls = nuts, ok, []

    def _ai_get(self, ep, *a, **k):
        self.calls.append(("_ai_get", ep))
        if ep != "/nuts":
            return {}
        return {"ok": self.ok, "location": "IslandWest", "isIsland": True,
                "walnutsFound": 7, "nuts": self.nuts}

    def _get(self, ep, *a, **k):
        self.calls.append(("_get", ep))     # ⚠️ 这个被调到就是 bug
        return {}


def fresh(api):
    M.api = api
    M._WALNUT_HINT_KEY["day"] = None
    M._WALNUT_HINT_KEY["told"] = None
    M._OPS_INNER["n"] = 0
    M._bg_any_running = lambda: False


_real_api = M.api

# 两个没拿的（一个脚边、一个远）+ 一个已经拿了的
SEED = [{"x": 21, "y": 81, "kind": "buried", "taken": False},
        {"x": 54, "y": 18, "kind": "bush", "taken": False},
        {"x": 62, "y": 76, "kind": "buried", "taken": True}]

fresh(NutsApi(SEED))
out = M._walnut_hint("IslandWest", 20, 80, daykey="spring|5|1")
ck("脚边有没拿的 → 出提示", "金核桃" in out, out)
ck("…报的是**最近那个**（埋点 (21,81)，不是 30 格外的树丛）", "(21,81)" in out, out)
ck("…带「这张图还剩几个」（已拿的不算进去 ⇒ 2）", "还剩 2 个" in out, out)
ck("…给**可直接敲的 op**", 'scene(ops="walnut")' in out, out)
ck("…埋点那条提醒「别蓄力」（蓄力会打空，血泪记录同 spot_run）", "别蓄力" in out, out)
ck("…**打的是 AI 那端**（_ai_get），不是 _get",
   ("_ai_get", "/nuts") in M.api.calls and not any(c[0] == "_get" for c in M.api.calls),
   str(M.api.calls))

_second = M._walnut_hint("IslandWest", 20, 80, daykey="spring|5|1")
ck("同一个点**不重复提**（它没动手是它的事，喊第二遍只是刷屏）", _second == "", _second)
ck("…但换个没提过的点（树丛 (54,18)）在半径内时还提",
   "金核桃" in M._walnut_hint("IslandWest", 54, 20, daykey="spring|5|1"))
ck("…换一天 → 账清了，又提",
   "金核桃" in M._walnut_hint("IslandWest", 20, 80, daykey="spring|6|1"))

fresh(NutsApi(SEED))
ck("离得远（> 9 格，切比雪夫）→ 不提",
   M._walnut_hint("IslandWest", 20, 60, daykey="spring|5|1") == "")
fresh(NutsApi(SEED))
ck("…正好 9 格 → 提（边界含在内，跟 walnut_run --radius 同口径）",
   "金核桃" in M._walnut_hint("IslandWest", 30, 81, daykey="spring|5|1"))
fresh(NutsApi(SEED))
ck("边上 10 格 → 不提", M._walnut_hint("IslandWest", 31, 81, daykey="spring|5|1") == "")

fresh(NutsApi(SEED))
ck("**非姜岛不提**（金核桃只长在姜岛）",
   M._walnut_hint("Farm", 20, 80, daykey="spring|5|1") == "")
ck("…IslandHut/IslandShrine 这种也认（前缀门禁）",
   "金核桃" in M._walnut_hint("IslandShrine", 20, 80, daykey="spring|5|1"))

fresh(NutsApi([{"x": 21, "y": 81, "kind": "buried", "taken": True}]))
ck("没拿的一个都没有（只剩已拿的）→ 不提",
   M._walnut_hint("IslandWest", 20, 80, daykey="spring|5|1") == "")
fresh(NutsApi(SEED, ok=False))
ck("端点报错（老 DLL / 抖动）→ 不提（**不猜**）",
   M._walnut_hint("IslandWest", 20, 80, daykey="spring|5|1") == "")

print("\n①b 共同的规矩：内层闭嘴 / 脚本跑着不提 —— **且都不消费这次机会**")
fresh(NutsApi(SEED))
M._OPS_INNER["n"] = 1
ck("域 op 内层 → 闭嘴", M._walnut_hint("IslandWest", 20, 80, daykey="spring|5|1") == "")
ck("…没消费掉（外层还有机会提）", not (M._WALNUT_HINT_KEY["told"] or set()))
M._OPS_INNER["n"] = 0
M._bg_any_running = lambda: True
ck("后台脚本在跑 → 不提", M._walnut_hint("IslandWest", 20, 80, daykey="spring|5|1") == "")
ck("…同样没消费掉", not (M._WALNUT_HINT_KEY["told"] or set()))
M._bg_any_running = lambda: False
ck("…脚本停了 → 照常提", "金核桃" in M._walnut_hint("IslandWest", 20, 80, daykey="spring|5|1"))


# ══════════════════════════════════════════════════════════════════
# ② walnut_run.py —— 起个假服务，真跑一遍 main()
# ══════════════════════════════════════════════════════════════════
print("\n② 🌰 walnut_run.py：真跑一遍（假 NagiBridge 服务）")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeGame:
    """一台假游戏：记着玩家在哪、背包有什么、还剩几个核桃。"""

    def __init__(self, nuts, hoe=True, tool_works=True, is_island=True, fail_nuts=False):
        self.nuts = [dict(n) for n in nuts]
        self.hoe, self.tool_works = hoe, tool_works
        self.is_island, self.fail_nuts = is_island, fail_nuts
        self.player = {"x": 20, "y": 80}
        self.tool_calls = self.interact_calls = self.walk_calls = 0
        self.shared = False          # 房主那端：**不该有人来问它**

    def body(self, path):
        if path == "/status":
            return {"worldReady": True}
        if path == "/state":
            inv = [{"name": "Hoe"}] if self.hoe else [{"name": "Axe"}]
            return {"location": {"name": "IslandWest"}, "player": dict(self.player), "inventory": inv}
        if path == "/nuts":
            if self.fail_nuts:
                return {"ok": False, "error": "模拟端点报错"}
            return {"ok": True, "location": "IslandWest", "isIsland": self.is_island,
                    "walnutsFound": 4, "nuts": self.nuts}
        if path == "/passable":
            return {"passable": True}
        return {"ok": True}


def make_server(game, port_out):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            b = json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            self._send(game.body(self.path.split("?")[0]))

        def do_POST(self):
            ep = self.path.split("?")[0]
            n = int(self.headers.get("Content-Length") or 0)
            try:
                kw = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                kw = {}
            if ep == "/passable":                       # ⚠️ 脚本是 **POST** 打它的（读 body 不看 query）
                self._send({"passable": True})
                return
            if ep == "/walk_to":
                game.walk_calls += 1
                game.player = {"x": kw.get("x", 0), "y": kw.get("y", 0)}   # 假服务：一步到位
            elif ep == "/position":
                game.player = {"x": kw.get("x", 0), "y": kw.get("y", 0)}
            elif ep == "/tool":
                game.tool_calls += 1
                if game.tool_works:                     # 真挥中了才翻 taken
                    for nt in game.nuts:
                        if nt["kind"] == "buried":
                            nt["taken"] = True
            elif ep == "/interact":
                game.interact_calls += 1
                if game.tool_works:
                    for nt in game.nuts:
                        if nt["kind"] == "bush":
                            nt["taken"] = True
            self._send({"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    port_out.append(srv.server_address[1])
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_script(game, argv):
    """把 walnut_run.py 当脚本跑一遍，返回它打出来的 [walnut] 日志。"""
    import importlib
    port = []
    srv = make_server(game, port)
    old_env, old_argv = os.environ.get("NAGI_URL"), sys.argv
    os.environ["NAGI_URL"] = f"http://127.0.0.1:{port[0]}"
    sys.argv = ["walnut_run.py"] + argv
    import io
    buf = io.StringIO()
    old_out = sys.stdout
    sys.stdout = buf
    try:
        sys.modules.pop("walnut_run", None)
        wr = importlib.import_module("walnut_run")
        try:
            wr.main()
        except SystemExit:
            pass
    finally:
        sys.stdout = old_out
        sys.argv = old_argv
        if old_env is None:
            os.environ.pop("NAGI_URL", None)
        else:
            os.environ["NAGI_URL"] = old_env
        srv.shutdown()
    return buf.getvalue()


# ── ②a 埋点：走过去 → 挥锄 → 回读确认 → 报拿到 ────────────────────
g = FakeGame([{"x": 21, "y": 81, "kind": "buried", "taken": False}])
out = run_script(g, ["--max", "1"])
ck("埋点：报「拿到 1/1」", "拿到 1/1" in out, out.strip().splitlines()[-1] if out.strip() else "")
ck("…**是挥了锄**（没去 interact 摇）", g.tool_calls >= 1 and g.interact_calls == 0,
   f"tool={g.tool_calls} interact={g.interact_calls}")
ck("…拿到之后清完这张图如实说「清完了」", "清完了" in out, out)

# ── ②b 树丛：走的是动作键摇，不是挥工具 ───────────────────────────
g = FakeGame([{"x": 54, "y": 18, "kind": "bush", "taken": False}])
out = run_script(g, ["--max", "1"])
ck("树丛：报「拿到 1/1」", "拿到 1/1" in out, out)
ck("…**是 interact 摇的**（⚠️ 核桃丛 size==4，拿工具砍它一点反应都没有）",
   g.interact_calls >= 1 and g.tool_calls == 0,
   f"tool={g.tool_calls} interact={g.interact_calls}")

# ── ②c 动手了但没成 → **如实报失败**，不许因为端点回 ok 就说拿到 ──
g = FakeGame([{"x": 21, "y": 81, "kind": "buried", "taken": False}], tool_works=False)
out = run_script(g, ["--max", "1"])
ck("挥了但那格没翻成 → 报「拿到 0/1」", "拿到 0/1" in out, out)
ck("…而且点名了原因（换站位也没拿到）", "没拿到" in out or "没挥中" in out, out)
ck("…**没有谎报成功**", "✓" not in out.split("⚠️")[0].split("· 埋的")[-1], out)

# ── ②d 没锄头 → 埋点明确拒绝 + 说下一步，且**真的没动手** ─────────
g = FakeGame([{"x": 21, "y": 81, "kind": "buried", "taken": False}], hoe=False)
out = run_script(g, ["--max", "1"])
ck("没锄头 → 埋点那条路拒绝", "没有锄头" in out, out)
ck("…**给出了下一步**（别让 AI 干瞪眼）", "storage" in out or "箱子" in out, out)
ck("…而且**真的一次都没挥**（不是先挥了再说没锄头）", g.tool_calls == 0, str(g.tool_calls))

# ── ②e 非姜岛 / 端点报错 → 明确说，不猜 ──────────────────────────
g = FakeGame([], is_island=False)
out = run_script(g, ["--max", "1"])
ck("非姜岛 → 说清「这里不是姜岛」", "不是姜岛" in out, out)

g = FakeGame([], fail_nuts=True)
out = run_script(g, ["--max", "1"])
ck("端点报错 → 如实报错名，不装作「这张图没有」", "报错" in out, out)

# ── ②f --dry-run 不动手 ─────────────────────────────────────────
g = FakeGame([{"x": 21, "y": 81, "kind": "buried", "taken": False}])
out = run_script(g, ["--max", "1", "--dry-run"])
ck("--dry-run：报有但不动手", "dry-run" in out and g.tool_calls == 0, out)

M.api = _real_api

print("\n" + ("=" * 46))
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
