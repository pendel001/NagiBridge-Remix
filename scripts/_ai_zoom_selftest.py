# -*- coding: utf-8 -*-
"""🖥️ AI 窗口缩放钉在 50% —— 纯 Python 自验（不起服务、不碰游戏）。2026-11-07

恒：「能不能给 ai 也**固定缩放 50**，我这里看 ai 的窗口视角缩放应该是 75，蛮大的，都看到像素了。
而我自己的好像固定了 50，算舒适」

判据（四条，**别放宽**）：
  ① **只对 7843 生效**：房主（7842=恒）的缩放一个字节都不许动（他的 50 是他自己调的）；
  ② 已经是目标值 ⇒ **不发**设置请求（省一发，也别把游戏设置写脏）；
  ③ 有节流（120s），别每个工具调用都打一发；
  ④ 读/写失败**不许**抛、不许拦任何工具（这条是"顺手做的小事"的底线）。

⚠️ 真机那一半（改了之后 AI 窗口真的变 50%）见 CHANGELOG；这里只验我们的判据与分派。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """记下每一次 `/zoom` 调用；`cur` 是读回包，`setting` 是设置回包。"""
    BASE_URL = "http://localhost:7843"

    def __init__(self):
        self.calls = []
        self.cur = {"ok": True, "percent": 75}
        self.setting = {"ok": True, "percent": 50}
        self.raise_on_get = False

    def _get(self, ep, params=None):
        self.calls.append((ep, dict(params or {})))
        if self.raise_on_get:
            raise RuntimeError("游戏没开")
        if not params:
            return dict(self.cur)
        self.setting = dict(self.setting)
        self.setting["percent"] = params.get("level", 50)
        return self.setting


_REAL_API = M.api
try:
    print("\n⓪ 没在跑服务（armed=False）⇒ 一个请求都不发（钉子/被 import 时不许有噪音）")
    a0 = FakeApi()
    M.api = a0
    M._AI_ZOOM["last"] = 0.0
    M._AI_ZOOM["armed"] = False
    ck("⓪未武装 ⇒ 零请求 + 空回执",
       M._ensure_ai_zoom() == "" and a0.calls == [], str(a0.calls))
    M._AI_ZOOM["armed"] = True

    print("\n① 75% → 50%（恒那条）：发一发设置，回执说清发生了什么")
    a = FakeApi()
    M.api = a
    M._AI_ZOOM["last"] = 0.0
    os.environ["NAGI_AI_ZOOM"] = "50"
    note = M._ensure_ai_zoom()
    ck("①读了当前值", any(ep == "/zoom" and not prm for ep, prm in a.calls), str(a.calls))
    ck("①发了 level=50", ("/zoom", {"level": 50}) in a.calls, str(a.calls))
    ck("①回执写「75% → 50%」", "75% → 50%" in note, note)

    print("\n② 节流：紧接着再来一次 ⇒ 一条请求都不发")
    before = len(a.calls)
    _ = M._ensure_ai_zoom()
    ck("②重复调用被节流拦住", len(a.calls) == before, f"{before} → {len(a.calls)}")

    print("\n③ 已经是 50% ⇒ 只读不写（别把游戏设置写脏）")
    a2 = FakeApi()
    a2.cur = {"ok": True, "percent": 50}
    M.api = a2
    M._AI_ZOOM["last"] = 0.0
    note3 = M._ensure_ai_zoom()
    ck("③没有设置请求", all(not prm for _ep, prm in a2.calls), str(a2.calls))
    ck("③也不啰嗦（返回空）", note3 == "", note3)

    print("\n④ **只对 7843**：房主那条口一发都不许打")
    a3 = FakeApi()
    a3.BASE_URL = "http://localhost:7842"
    M.api = a3
    M._AI_ZOOM["last"] = 0.0
    _ = M._ensure_ai_zoom()
    ck("④7842 ⇒ 零请求（恒自己的缩放不归我们管）", a3.calls == [], str(a3.calls))

    print("\n⑤ 旋钮：NAGI_AI_ZOOM=0 关掉 / =65 按新值")
    a4 = FakeApi()
    M.api = a4
    M._AI_ZOOM["last"] = 0.0
    os.environ["NAGI_AI_ZOOM"] = "0"
    _ = M._ensure_ai_zoom()
    ck("⑤设 0 ⇒ 零请求（完全不碰游戏）", a4.calls == [], str(a4.calls))
    a5 = FakeApi()
    a5.setting = {"ok": True, "percent": 65}
    M.api = a5
    M._AI_ZOOM["last"] = 0.0
    os.environ["NAGI_AI_ZOOM"] = "65"
    _ = M._ensure_ai_zoom()
    ck("⑤设 65 ⇒ 发 level=65", ("/zoom", {"level": 65}) in a5.calls, str(a5.calls))

    print("\n⑥ 读失败 ⇒ 静静放过，**不许抛**（别因为缩放把工具拦了）")
    a6 = FakeApi()
    a6.raise_on_get = True
    M.api = a6
    M._AI_ZOOM["last"] = 0.0
    os.environ["NAGI_AI_ZOOM"] = "50"
    try:
        note6 = M._ensure_ai_zoom()
        _no_raise = True
    except Exception as e:  # noqa: BLE001
        note6, _no_raise = str(e), False
    ck("⑥读失败不抛异常", _no_raise, note6)
    ck("⑥也不返回垃圾", note6 == "", note6)

    print("\n⑦ 写失败 ⇒ 如实说「没成」，别假装改好了")
    a7 = FakeApi()
    a7.setting = {"ok": False, "error": "World not ready"}
    M.api = a7
    M._AI_ZOOM["last"] = 0.0
    note7 = M._ensure_ai_zoom()
    ck("⑦回执明说没成", "没成" in note7, note7)

    print("\n⑨ 真 api 那条分支：走同一个 no-proxy 会话，但**超时必须短**（≤3s）")
    class _Resp:
        def __init__(self, payload):
            self._p = payload

        def json(self):
            return self._p

    class _Sess:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append((url, dict(params or {}), timeout))
            if not params:
                return _Resp({"ok": True, "percent": 75})
            return _Resp({"ok": True, "percent": params.get("level", 50)})

    class ApiWithSess(FakeApi):
        def __init__(self):
            super().__init__()
            self.requests = _Sess()

    a9 = ApiWithSess()
    M.api = a9
    M._AI_ZOOM["last"] = 0.0
    os.environ["NAGI_AI_ZOOM"] = "50"
    _n9 = M._ensure_ai_zoom()
    ck("⑨走 `api.requests` 那条（不是 `_get`）", len(a9.requests.calls) == 2, str(a9.requests.calls))
    ck("⑨URL 打到 /zoom", all(c[0].endswith("/zoom") for c in a9.requests.calls), str(a9.requests.calls))
    ck("⑨超时都 ≤3s（别把工具调用拖住）",
       all((c[2] or 99) <= 3 for c in a9.requests.calls), str(a9.requests.calls))
    ck("⑨回执照旧", "75% → 50%" in _n9, _n9)

    print("\n⑧ 接线：`_with_state` 真的会调它（否则代码写了也不跑）")
    import inspect
    _src = inspect.getsource(M._with_state)
    ck("⑧`_with_state` 的返回值里带 `_ensure_ai_zoom()`", "_ensure_ai_zoom()" in _src, _src[-300:])
finally:
    M.api = _REAL_API
    M._AI_ZOOM["last"] = 0.0
    M._AI_ZOOM["armed"] = False
    os.environ["NAGI_AI_ZOOM"] = "50"

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ AI 窗口缩放：只对 7843、对得上就不动、有节流、失败不抛。")
