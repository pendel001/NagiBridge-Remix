# -*- coding: utf-8 -*-
"""🚫 上料（`farm ops=load`）不该把箱子/稻草人串进路线 —— 纯 Python + C# 源码自验。2026-11-07

恒真机：「**刚刚的 load 收明明只需要三台熔炉，但遍历了箱子、稻草人等奇怪的点**」。
（现场账：`sessions/session_log.jsonl` 13:15:13 那趟 `farm(ops=load, kw={item:"Copper Bar", here:true})`
 ——「📍 Farm: **8 台** → 36 个过道格」，然后在 **Scarecrow (32,23)** 边上转了三格、整整 32 秒；
 同一天 13:08:02 也是「8 台」。而那会儿场上只有 **3 台熔炉**。）

根因两层，**两层都得堵**：
  ① C#：`/machines` / `/farm_report` 只问 `bigCraftable` ⇒ 箱子/稻草人也被报成 `status=empty` 的机器；
  ② Python：`machine_loader` 老门只看 `status == "empty"` ⇒ 谁空着就去摸 ——
     而**箱子的 interact 是开箱 UI**，菜单一起吃进来，走位当场卡死。

判据（都不猜，问游戏）：
  ③ `/machine_reqs` 的 `canPlace`：`True` 放得进去 / `False` 是机器但不吃这件料 / `None` **压根不是机器**
     （真机验过：Furnace=有值，Chest/Scarecrow=`null`）；
  ④ 问不到（老 DLL / 料不在背包）⇒ 只按兜底名单剔，**绝不清空整组**；
  ⑤ "还差什么"出自 `requirements`（`MachineData.AdditionalConsumedItems`），不编。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import machine_loader as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _world():
    """真机那一刻的农场：3 台熔炉 + 3 只箱子 + 2 个稻草人（坐标都是现场坐标）。"""
    return [
        {"type": "Furnace", "x": 53, "y": 22, "status": "empty", "location": "Farm"},
        {"type": "Furnace", "x": 53, "y": 23, "status": "empty", "location": "Farm"},
        {"type": "Furnace", "x": 53, "y": 24, "status": "ready", "location": "Farm"},
        {"type": "Chest", "x": 58, "y": 14, "status": "empty", "location": "Farm"},
        {"type": "Chest", "x": 58, "y": 15, "status": "empty", "location": "Farm"},
        {"type": "Chest", "x": 58, "y": 16, "status": "empty", "location": "Farm"},
        {"type": "Scarecrow", "x": 64, "y": 23, "status": "empty", "location": "Farm"},
        {"type": "Scarecrow", "x": 32, "y": 23, "status": "empty", "location": "Farm"},
    ]


class FakeApi:
    """假的 7843。够 `run()` 走完一圈：passable / walk_to / interact / select / machines / machine_reqs。"""

    def __init__(self, machines, canplace=None, probe="ok"):
        self.ms = [dict(m) for m in machines]
        self.canplace = dict(canplace or {})
        self.probe = probe              # "ok" = /machine_reqs 可用；"missing" = 老 DLL
        self.calls = []
        self.player = {"x": 54, "y": 23}
        self.cur = ""
        self.inv = [{"name": "Iron Ore", "displayName": "铁矿石"}]
        self.loc = "Farm"

    # ── 读 ─────────────────────────────────────────────────────────────
    def log(self, m):
        self.calls.append(("log", m))

    def machines(self):
        return {"location": self.loc, "machines": [dict(m) for m in self.ms]}

    def farm_report(self):
        return {"ok": True, "machines": {"machines": [dict(m) for m in self.ms]}}

    def current_location(self):
        return self.loc

    def state(self):
        return {"location": {"name": self.loc},
                "player": {**self.player, "currentItem": self.cur, "isMoving": False},
                "inventory": [dict(i) for i in self.inv]}

    def select(self, nm):
        if any(i["name"].lower() == str(nm).lower() for i in self.inv):
            self.cur = nm
            return {"ok": True}
        return {"ok": False}

    # ── 写 ─────────────────────────────────────────────────────────────
    def _ai_post(self, ep, data=None):
        self.calls.append((ep, data))
        if self.probe == "missing" or ep != "/machine_reqs":
            return {"ok": False, "error": "404"}
        return {"ok": True, "count": len(self.ms),
                "machines": [
                    {"x": m["x"], "y": m["y"], "type": m["type"],
                     "canPlace": self.canplace.get((m["x"], m["y"])),
                     "requirements": m.get("requirements") or []}
                    for m in self.ms]}

    def _post(self, ep, data=None, **_k):
        self.calls.append((ep, data))
        d = data or {}
        if ep == "/passable":
            return {"passable": True}
        if ep == "/walk_to":
            self.player["x"] = d.get("x", self.player["x"])
            self.player["y"] = d.get("y", self.player["y"])
            return {"ok": True}
        if ep == "/interact":
            for m in self.ms:
                if (m["x"], m["y"]) == (d.get("x"), d.get("y")):
                    if m.get("status") == "empty":
                        m["status"] = "processing"
                    elif m.get("status") == "ready":
                        m["status"] = "empty"
                    return {"ok": True, "actionTriggered": True}
            return {"ok": True, "actionTriggered": False}
        return {"ok": True}

    # ── 断言用 ─────────────────────────────────────────────────────────
    def eps(self, ep):
        return [d for (e, d) in self.calls if e == ep]

    def logs(self):
        return " ｜ ".join(str(d) for (e, d) in self.calls if e == "log")

    def touched(self):
        return [(d.get("x"), d.get("y")) for d in self.eps("/interact")]


_SLEEP_BAK = M.time.sleep


def _reset(api):
    M.api = api
    M._PASS_CACHE.clear()
    M.time.sleep = lambda *_a, **_k: None     # 别真睡（一趟十几秒）


try:
    print("\n① 目标集：箱子/稻草人**根本不进**（老 DLL 也得拦得住）")
    _a = FakeApi(_world(), probe="missing")
    _reset(_a)
    _out, _err = M.get_serviceable_machines("", "", with_items=["Iron Ore"], here=True)
    ck("①只剩 3 台熔炉", len(_out) == 3, str([(m["type"], m["x"], m["y"]) for m in _out]))
    ck("①没有 Chest", not any(m["type"] == "Chest" for m in _out))
    ck("①没有 Scarecrow", not any(m["type"] == "Scarecrow" for m in _out))
    ck("①ready 那台还在（收的活儿不能漏）",
       any(m["status"] == "ready" for m in _out))

    print("\n①b 兜底名单：容器 / 稻草人 / 纯装饰 ⇒ 不是机器；真机器一个不误伤")
    for _t, _exp in (("Chest", True), ("Stone Chest", True), ("Mini-Fridge", True),
                     ("Scarecrow", True), ("Rarecrow", True), ("Deluxe Scarecrow", True),
                     ("Campfire", True), ("Torch", True),
                     ("Furnace", False), ("Keg", False), ("Cask", False),
                     ("Bee House", False), ("Auto-Grabber", False), ("", False), (None, False)):
        ck(f"①b{_t!r} ⇒ {_exp}", M._not_machine_name(_t) is _exp, M._not_machine_name(_t))

    print("\n② 放料前问游戏：只留 `canPlace is True`，其余说清为什么")
    _a = FakeApi(_world(), canplace={(53, 22): True, (53, 23): False})
    _a.ms[1]["requirements"] = [{"name": "煤炭", "need": 1, "have": 0, "enough": False}]
    _reset(_a)
    _emp = [m for m in _a.ms if m["status"] == "empty"]
    _keep, _note = M._filter_loadable(_emp, ["Iron Ore"])
    ck("②只留放得进去的那台", [(m["x"], m["y"]) for m in _keep] == [(53, 22)],
       str([(m["x"], m["y"]) for m in _keep]))
    ck("②剔掉的报出「不是机器」", "不是机器的点" in _note, _note)
    ck("②缺料说的是**煤炭**（游戏给的 requirements，不是编的）",
       "煤炭×1" in _note and "手上 0" in _note, _note)
    ck("②说得清「它是机器但不吃这件料」", "不吃这件料" in _note, _note)

    print("\n③ 全都不吃 ⇒ **一步都不走**（老账：13:17 那趟白转 13 秒）")
    _only_empty = [m for m in _world() if not (m["type"] == "Furnace" and m["status"] == "ready")]
    _a = FakeApi(_only_empty, canplace={(53, 22): False, (53, 23): False})
    _reset(_a)
    M.run(["Iron Ore"], "", "", 0, no_enter=True, here=True)
    ck("③没走过一步", _a.eps("/walk_to") == [], str(_a.eps("/walk_to")[:5]))
    ck("③没摸过一下", _a.eps("/interact") == [], str(_a.touched()[:5]))
    ck("③明说「这组没有能放料的机器」", "没有能放料的机器" in _a.logs(), _a.logs()[:300])

    print("\n④ 正常那趟：只碰熔炉，**箱子/稻草人格子一格都不去**")
    _a = FakeApi(_only_empty, canplace={(53, 22): True, (53, 23): True})
    _reset(_a)
    M.run(["Iron Ore"], "", "", 0, no_enter=True, here=True)
    _t = _a.touched()
    ck("④摸过熔炉", (53, 22) in _t and (53, 23) in _t, str(_t))
    ck("④**没摸过箱子**（x=58 那三只）", not any(x == 58 for (x, _y) in _t), str(_t))
    ck("④**没摸过稻草人**（64,23 / 32,23）",
       not any((x, y) in ((64, 23), (32, 23)) for (x, y) in _t), str(_t))
    ck("④走过道格也只围着熔炉", all(abs(x - 53) <= 1 and 21 <= y <= 25
                                     for (x, y) in _t), str(_t))
    ck("④账对得上（放入 2 台）", "放入 2 台" in _a.logs(), _a.logs()[-260:])

    print("\n⑤ 老 DLL（没 `/machine_reqs`）⇒ 不清空、照旧干活，只是不剔不认识的")
    _a = FakeApi(_world(), probe="missing")
    _reset(_a)
    M.run(["Iron Ore"], "", "", 0, no_enter=True, here=True)
    _t = _a.touched()
    ck("⑤熔炉照样上了料", any(x == 53 for (x, _y) in _t), str(_t))
    ck("⑤箱子照样没碰（名单兜底）", not any(x == 58 for (x, _y) in _t), str(_t))

    print("\n⑥ 不认识的容器（没进名单，但游戏说 `canPlace=None`）也剔得掉")
    _a = FakeApi([{"type": "Aquarium", "x": 20, "y": 20, "status": "empty", "location": "Farm"},
                  {"type": "Furnace", "x": 53, "y": 22, "status": "empty", "location": "Farm"}],
                 canplace={(53, 22): True})
    _reset(_a)
    _keep, _note = M._filter_loadable([m for m in _a.ms if m["status"] == "empty"], ["Iron Ore"])
    ck("⑥Aquarium 被剔掉", [m["type"] for m in _keep] == ["Furnace"], str([m["type"] for m in _keep]))
    ck("⑥说明里点了它的名", "Aquarium×1" in _note, _note)

    print("\n⑦ 收工复核那句话别把箱子算成「空着的机器」")
    _a = FakeApi(_world(), canplace={(53, 22): True, (53, 23): True})
    _reset(_a)
    M.run(["Iron Ore"], "", "", 0, no_enter=True, here=True)
    ck("⑦没有「还有 5 台空着」（那是箱子+稻草人）", "还有 5 台空着" not in _a.logs(), _a.logs()[-200:])

    print("\n⑧ C# 源码钉：机器清单里剔掉箱子/稻草人（这版 DLL 上游戏后才生效）")
    _cs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "ModEntry.cs"), encoding="utf-8").read()
    ck("⑧有 `IsNotAMachine`（判据只此一处）", "private static bool IsNotAMachine" in _cs)
    ck("⑧它认 `Chest`", "if (obj is Chest) return true;" in _cs)
    ck("⑧它认 `IsScarecrow()`（反编译实有）", "obj.IsScarecrow()" in _cs)
    ck("⑧`/machines`+`/farm_report` 那条扫描用了它",
       "ScanLocationMachines" in _cs and _cs.count("if (IsNotAMachine(obj)) continue;") == 2,
       f"出现 {_cs.count('if (IsNotAMachine(obj)) continue;')} 次")
    ck("⑧`/machine_reqs` 那条枚举也用了它（探针别把箱子列进来）",
       "EnumerateMachines" in _cs and "private static bool IsNotAMachine" in _cs)

    print("\n⑨ 源码钉：探针和主连接**打同一个人**（`--port` 一换，两个地址一起换）")
    _ml = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "machine_loader.py"), encoding="utf-8").read()
    ck("⑨`--port` 会同时钉住 `NAGI_URL` 和 `NAGI_AI_URL`",
       'os.environ["NAGI_URL"] = f"http://localhost:{args.port}"' in _ml
       and 'os.environ["NAGI_AI_URL"] = os.environ["NAGI_URL"]' in _ml,
       "少了 NAGI_AI_URL 那行 ⇒ 探针会去问另一个人")
finally:
    M.time.sleep = _SLEEP_BAK

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 上料：只走**游戏说收得下**的机器；箱子/稻草人不再被当成空机器，问不到游戏时也不清空。")
