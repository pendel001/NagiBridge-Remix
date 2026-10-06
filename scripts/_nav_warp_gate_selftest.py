# -*- coding: utf-8 -*-
"""🚪 走位**门/传送格闸门** ＋ **半路换图停手**的回执 —— 纯离线钉子（不起服务、不碰游戏）。2026-10-06

来历（恒真机）：`map ops=walk x=55 y=12` 那格**正好是小屋门格** ⇒ 踩上去被游戏 warp 进屋，
而"走到 (55,12)"那串**没停**，继续在**新图的同坐标**上收尾 ⇒ 落在小屋室内 (55,12)，
那是房间外的空白画布（屋子只占 x 20~41）＝ 恒那句「怎么又飞墙外」。

两侧的分工（**这条钉子就是钉这个分工**）：
  · mod 侧（`ModEntry.cs`）：① 目标格是门/传送格 + 没传 `allowWarp` ⇒ 当场拒 `{warp_tile:true}`；
    ② 同图坐标走位半路被换图 ⇒ **停手**，并把结构化回执挂进 `walk_changed_map` 警报
    （`/walk_to` 是**发射后不管**的，回包只能走警报这条道）。
  · Python 侧：**跨图/导航内部**的走位（`_walk_and_wait` 的每一个调用方）带 `allowWarp=true` 放行；
    **AI 直调的坐标走位**（`_walk_to_coord`）**不传** ＝ 闸门就设在这一层。

钉五条：
  ① 导航内部走位（`_walk_and_wait`）发 `/walk_to` 时**带 `allowWarp: true`**（放行门格，行为不倒退）
  ② AI 直调坐标走位（`_walk_to_coord`）**不带**（源码级 + 运行时两把尺子）
  ③ `warp_tile` 拒绝 ⇒ 原话照转，并提示该用 `interact` / `map ops=go`
  ④ 半路换图 ⇒ 优先转述**游戏自己**的 `walk_changed_map`（含 from/to），没收到才退回按状态说
  ⑤ `_walk_changed_map_alert` 与 `_walk_failed_alert` 同规矩：`since=None` 关旁路、`peek=True`、比时间戳
"""
import inspect
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import navigation as N          # import 安全：这文件只定义函数/常量

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """假 `/walk_to` `/state` `/alerts`：只按剧本答，并**记下每次发车带的 payload**。"""

    def __init__(self, loc_before="Farm", loc_after="Farm", walk_reply=None, alerts=None):
        self.loc_before = loc_before
        self.loc_after = loc_after
        self.walk_reply = walk_reply if walk_reply is not None else {
            "ok": True, "destination": {"x": 55, "y": 12}}
        # ⚠️ 数据字段**必须**叫 `_alerts`（别叫 `alerts`）：叫 `alerts` 会把下面那个同名**方法**
        #    整个盖掉 ⇒ `api.alerts(peek=True)` 变成"调用一个 list" → 被 `except Exception` 吞成
        #    None ⇒ 钉子红得莫名其妙（我第一版就是这么写的）。
        self._alerts = list(alerts or [])
        self.warped = False          # `walk_to_coord` 一发车就翻成 True ⇒ 之后 `state()` 报新图
        self.walk_calls = []         # `walk_to_coord(...)` 的关键字（AI 直调那条路）
        self.posts = []              # `_post("/walk_to", …)` 的 payload（导航内部那条路）
        self.peek_seen = None

    def state(self, **kw):
        name = self.loc_after if self.warped else self.loc_before
        return {"location": {"name": name}, "player": {"x": 55, "y": 12}, "time": {}, "inventory": []}

    def walk_to_coord(self, location, x, y, allow_warp=False):
        self.walk_calls.append({"location": location, "x": x, "y": y, "allow_warp": allow_warp})
        self.warped = True           # 游戏 warp 发生在走位途中/之后
        return self.walk_reply

    def _post(self, ep, data=None):
        self.posts.append((ep, dict(data or {})))
        self.warped = True
        return self.walk_reply

    def alerts(self, peek=False):
        self.peek_seen = peek
        return {"alerts": self._alerts}

    def _get(self, ep, params=None):
        return {"ok": True}


_real = {n: getattr(N, n) for n in ("api", "_with_state", "_wait_arrival")}
try:
    N._with_state = lambda s: s                     # 不拼状态条（离线）
    N._wait_arrival = lambda *a, **k: True          # 只把"等"这一步停掉（成功路）

    # ── ① 导航内部走位：`_walk_and_wait` 必须带 allowWarp（门/出口格放行）──
    print("\n① 导航内部走位（`_walk_and_wait`）⇒ 发 `/walk_to` 带 `allowWarp: true`")
    f = FakeApi()
    N.api = f
    _ok, _note = N._walk_and_wait("Farm", 55, 12, timeout=5)
    _wl = [d for ep, d in f.posts if ep == "/walk_to"]
    ck("…发了一发 `/walk_to`", len(_wl) == 1, str(f.posts))
    ck("…payload 带 `allowWarp: true`（矿井入口/帐篷/门格那类靠它放行）",
       bool(_wl) and _wl[0].get("allowWarp") is True, str(_wl))
    ck("…`location/x/y` 一个字没动（只加了一个键）",
       bool(_wl) and (_wl[0].get("location"), _wl[0].get("x"), _wl[0].get("y")) == ("Farm", 55, 12), str(_wl))

    # ── ② AI 直调坐标走位：`_walk_to_coord` 不带 allowWarp（闸门）──
    print("\n② AI 直调坐标走位（`_walk_to_coord`）⇒ **不带** `allowWarp`（闸门就设在这层）")
    _src = inspect.getsource(N._walk_to_coord)
    ck("…源码里那发车**没有** `allow_warp=True`", "allow_warp" not in _src, "源码里出现了 allow_warp")
    ck("…源码里也没有 `allowWarp`（别从 Python 侧偷偷放行）", "allowWarp" not in _src)
    f = FakeApi()
    N.api = f
    N._walk_to_coord(55, 12)
    ck("…运行时不带 allowWarp（用的是缺省 False）",
       bool(f.walk_calls) and f.walk_calls[0]["allow_warp"] is False, str(f.walk_calls))

    # ── ③ 门/传送格被拒 ⇒ 原话照转 ──
    print("\n③ 目标格是门/传送格 ⇒ 转述 mod 的拒绝原话（该用 interact / map ops=go）")
    f = FakeApi(loc_before="Farm", loc_after="Farm", walk_reply={
        "ok": False, "warp_tile": True,
        "error": "那格是门/传送格（踩上去会换图）—— 要进门请用 interact；跨图请用 map ops=go"})
    N.api = f
    out = N._walk_to_coord(55, 12)
    ck("…回包说「走位被拒」＋ 坐标", "走位被拒" in out and "(55,12)" in out, out)
    ck("…原话里有 `interact` 和 `map ops=go`（给 AI 的下一步）",
       "interact" in out and "map ops=go" in out, out)
    ck("…⛔ 没谎称「已到」", "已到" not in out, out)

    # ── ④ 半路换图 ⇒ 优先转述游戏自己的 walk_changed_map ──
    print("\n④ 同图坐标走位半路被换图 ⇒ 转述**游戏自己**的 `walk_changed_map`（含 from/to）")
    _al = {"timeUtc": "2099-01-01T00:00:00.0000000Z", "type": "walk_changed_map",
           "severity": "warning", "source": "walk", "ok": False, "changed_map": True,
           "from": "Farm", "to": "Cabin2",
           "error": "走到一半换图了（踩到传送格）—— 已停手；跨图请用 map ops=go"}
    f = FakeApi(loc_before="Farm", loc_after="Cabin2", alerts=[_al])
    N.api = f
    out = N._walk_to_coord(55, 12)
    ck("…用的是**游戏原话**（不是我们自己编的那句「那格是**传送点**」）",
       "走到一半换图了" in out, out)
    ck("…并带上 from→to（`Farm` → `Cabin2`）", "Farm" in out and "Cabin2" in out, out)
    ck("…依然指向 `map ops=go`（跨图该走它）", "map ops=go" in out, out)
    ck("…读警报必须 `peek=True`（⛔ 别消费状态条的队列）", f.peek_seen is True, str(f.peek_seen))

    print("\n④-b 没收到那条警报（被 4 秒同文案去重）⇒ 退回按状态如实说，⛔ 不假称已到")
    f = FakeApi(loc_before="Farm", loc_after="Cabin2", alerts=[])
    N.api = f
    out = N._walk_to_coord(55, 12)
    ck("…如实说是传送点/已经离开", "传送点" in out and "Cabin2" in out, out)
    ck("…⛔ 没谎称「已到」", "已到" not in out, out)

    # ── ⑤ `_walk_changed_map_alert` 的旁路规矩（同 `_walk_failed_alert`）──
    print("\n⑤ `_walk_changed_map_alert`：`since=None` 关旁路 / 只认**晚于**发车时刻那条 / peek=True")
    ck("…`since=None` ⇒ 关掉（认不出'这次'就宁可不要）",
       N._walk_changed_map_alert(None) is None)
    f = FakeApi(alerts=[_al])
    N.api = f
    _old = N._alert_epoch_utc(_al)
    ck("…晚于发车时刻的那条 ⇒ 取到", N._walk_changed_map_alert(_old - 1) is not None)
    ck("…**早于/等于**发车时刻的那条 ⇒ 不算这次的（别把旧警报当这次）",
       N._walk_changed_map_alert(_old + 1) is None)
    f = FakeApi(alerts=[{"timeUtc": "2099-01-01T00:00:00.0000000Z", "type": "walk_failed",
                         "message": "别的警报"}])
    N.api = f
    ck("…`walk_failed` 那条**不认**（只认 walk_changed_map）",
       N._walk_changed_map_alert(0) is None)
finally:
    for _k, _v in _real.items():
        setattr(N, _k, _v)

print("\n" + "=" * 46)
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
