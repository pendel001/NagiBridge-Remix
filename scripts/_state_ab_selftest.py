"""🧪 A/B：`_with_state` 抽函数前后**逐字一致**（离线，不碰游戏）。

`_with_state` 是全工具的热路径，2026-09-23 为了给截图补状态条把它的尾段抽成了
`_state_suffix()`。这个脚本就是那次重构的**证据**：把 git HEAD 的旧版和当前版并在同一进程里，
喂同一份假状态，逐字比对返回串。

跑法:
    PYTHONIOENCODING=utf-8 python scripts/_state_ab_selftest.py
（旧版副本由脚本自己从 git 取到临时目录，不留进仓库。）
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

FAKE_DATA = {
    "time": {"season": "spring", "dayOfMonth": 2, "year": 1,
             "timeOfDay": 1210, "weather": 0},
    "player": {"name": "轮回", "x": 15, "y": 40, "health": 100, "maxHealth": 100,
               "stamina": 200, "maxStamina": 270, "money": 500, "maxItems": 12,
               "currentTool": "", "dailyLuck": 0.0},
    "location": {"name": "Mountain"},
    "inventory": [],
    "raw": {"recent_events": []},
    "alerts": [],
}


class _NoGame:
    def __getattr__(self, name):
        def _boom(*a, **kw):
            raise RuntimeError(f"自验不该碰游戏（api.{name}）")
        return _boom


def _stub(M):
    """把新旧两版都打到同一套桩上——**桩必须一模一样**，否则比的是桩不是代码。"""
    M.api = _NoGame()
    M._gather_state = lambda: dict(FAKE_DATA)
    M._fetch_farm_report = lambda *a, **kw: {}
    M._build_morning_report = lambda *a, **kw: ""
    M._maybe_ice_fishing_auto = lambda *a, **kw: ""
    M._maybe_egg_run_auto = lambda *a, **kw: ""
    M._bg_activity_line = lambda *a, **kw: ""
    M._plan_drain_notices = lambda *a, **kw: ""
    M._delta_show = lambda *a, **kw: ""
    M._statue_reminder = lambda *a, **kw: ""
    M._machine_ready_hint = lambda *a, **kw: ""
    M._sit_hint = lambda *a, **kw: ""
    M._worn_summary = lambda *a, **kw: ""
    M._forage_summary = lambda *a, **kw: ""
    M._shipbin_hint = lambda *a, **kw: ""
    M._backpack_upgrade_poi = lambda *a, **kw: ""
    M._special_orders_inject = lambda *a, **kw: ""
    M._sittable_cached = lambda *a, **kw: {}
    M.player_activity.should_inject = lambda *a, **kw: False
    M._first_call_since_start = False
    M._last_full_date = None
    M.map_feature_hidden = lambda loc: set()      # 门禁是新增功能，A/B 时把它关掉


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    tmp = tempfile.mkdtemp(prefix="nagi_ab_")
    old_path = os.path.join(tmp, "_old_server_ab.py")
    src = subprocess.run(["git", "show", "HEAD:scripts/nagi_mcp_server.py"],
                         cwd=REPO, capture_output=True, check=True)
    with open(old_path, "wb") as f:
        f.write(src.stdout)

    import nagi_mcp_server as NEW
    OLD = _load(old_path, "_old_server_ab")

    fails = []
    for label, body, force in [
        ("普通调用", "🧹 清完了", False),
        ("强制全量", "📊 状态速报", True),
        ("多行正文", "行1\n行2\n\n行3", False),
        ("空正文（截图新路）", "", False),
    ]:
        _stub(OLD)
        _stub(NEW)
        OLD._last_full_date = ("spring", 1, 1)     # 让两边的 full 判定都从同一起点出发
        NEW._last_full_date = ("spring", 1, 1)
        a = OLD._with_state(body, force) if force else OLD._with_state(body)
        b = NEW._with_state(body, force) if force else NEW._with_state(body)
        if not body:
            # 这一条**故意不同**：空正文时新版不加分隔线（旧版没有调用方会这么用）。
            # 判据 = 旧版恰好比新版多一个"只由换行和 ╌ 组成"的前缀，其余逐字相同。
            prefix = a[:len(a) - len(b)] if len(a) > len(b) else ""
            ok = (b and len(prefix) > 0 and set(prefix) <= {"\n", "╌"}
                  and a[len(prefix):] == b)
        else:
            ok = a == b
        print(("  ✅ " if ok else "  ❌ ") + label + (f"  ({len(a)} vs {len(b)} 字节)" if ok else ""))
        if not ok:
            fails.append(label)
            print("     OLD:", repr(a[:220]))
            print("     NEW:", repr(b[:220]))

    print()
    if fails:
        print(f"❌ {len(fails)} 条不一致: {fails}")
        return 1
    print("✅ 抽函数前后逐字一致（空正文那条按设计少一条分隔线）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
