# -*- coding: utf-8 -*-
"""⏱ 长活的"60s 客户端超时"闸 + 🎪 日常↔节日 对话自动改道 —— 纯 Python 自验。2026-11-07

恒：「浇水，播种，锄地，摸动物等脚本要不要也加入默认异步？前端的 mcp 60s 限制实在是太烦了，狂超时」
    「还有节日的 interact。对了，节日的 npc 对话和日常的可以路由一下嘛，如果 ai 用错成日常的去搭讪
      节日的，自动执行节日的这样子。」

真机数据（`session_log.jsonl` 5097 条调用的实测）：≥60s 的占 **2.3%**，尾巴是
    `farm animals` 255s / `fish go` 234s / `farm till` 119s / `farm plant` 91s / `farm water` 85s /
    `festival interact` 122s —— 前端 60s 一到就掐，AI 只看到"无响应"，活其实还在服务端跑（白烧一遍）。

判据：
  ① `_CALL_BUDGET_S` 压在 60s 之下（留状态条那轮读数的余量），`_budget_left` 语义清楚；
  ② 锄地/播种这类 **in-process 逐格循环** 到点**主动停手**，把"还剩 N 格 + 同一条命令再调一次"说清；
  ③ 节日巡礼的默认预算也收到这条线上（它原来 120s ⇒ 必然被掐）；
  ④ 浇水是**子进程脚本** ⇒ 走异步白名单（异步会挂到收工或 45s，收工当场把脚本原文交回来）；
  ⑤ 人在节日场地时，`social ops=chat` **自动改走** `festival interact`（不导航、不报假失败）；
  ⑥ 反过来：没节日时 `festival interact` 把"该敲哪条"指准（不硬跳）。
"""
import inspect
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


ROWS = {}
SEED = "Mixed Seeds"
CALLS = {"use_tool": 0, "use_item": 0, "surroundings": 0}
_BUDGET_BAK = None


def _scan(pts, margin=1):
    """按 pts 回放假地图：默认都是**生土可耕**；ROWS 里改过的按 ROWS 来。"""
    p = list(pts)
    out = {}
    for (x, y) in p:
        out[(x, y)] = {"terrain": None, "diggable": True}
    for k, v in ROWS.items():
        out[k] = dict(v)
    return out, ""


class FakeApi:
    def state(self):
        return {"player": {"currentToolUpgrade": 0, "stamina": 200, "maxStamina": 270},
                "inventory": [{"name": SEED, "displayName": SEED, "stack": 9}]}

    def walk_ok_tiles(self, *_a):
        return {(x, y) for x in range(0, 20) for y in range(0, 20)}

    def stand_tile(self, tx, ty, _ok):
        return (tx, ty - 1, 2)

    def walk_natural(self, *_a):
        return True

    def aim_at(self, *_a):
        return True, {}

    def park_at(self, *_a):
        return True

    def face(self, *_a):
        return {"ok": True}

    def select(self, *_a):
        return {"ok": True}

    def use_tool(self, *_a):
        CALLS["use_tool"] += 1
        return {"ok": True}

    def use_item(self, *_a):
        CALLS["use_item"] += 1
        return {"ok": True}

    def player_stamina(self):
        return (200, 270)


_real = {n: getattr(M, n) for n in ("api", "_scan_tiles", "_select_best_hoe", "_with_state")}
try:
    M.api = FakeApi()
    M._scan_tiles = _scan
    M._select_best_hoe = lambda: None
    M._with_state = lambda s: s
    _BUDGET_BAK = M._CALL_BUDGET_S

    # ── ① 预算常数与判据 ──────────────────────────────────────────
    print("\n① 单次预算压在客户端 60s 之下")
    ck("①`_CALL_BUDGET_S` 在 (0, 55] 之间（60 减状态条那轮读数）",
       0 < M._CALL_BUDGET_S <= 55, str(M._CALL_BUDGET_S))
    ck("①刚到点 ⇒ 还没用完", M._budget_left(__import__("time").time()) is False)
    ck("①很久以前起的头 ⇒ 用完", M._budget_left(__import__("time").time() - 999) is True)
    ck("①显式 budget_s 优先于默认",
       M._budget_left(__import__("time").time() - 5, budget_s=1) is True
       and M._budget_left(__import__("time").time(), budget_s=99) is False)

    # ── ② 锄地到点停手 ────────────────────────────────────────────
    print("\n② 锄地：预算一到就**停手**，说清还剩几格、让 AI 再调一次")
    ROWS.clear()
    CALLS["use_tool"] = 0
    M._CALL_BUDGET_S = 1e-9          # 等于"第一格之前就到点"
    _out = M._farm_till(x1=0, y1=0, x2=2, y2=2)
    ck("②回执点明「单次限时」", "单次限时" in _out, _out[:400])
    ck("②…说清还剩几格", "剩 9 格" in _out, _out[:400])
    ck("②…并给下一步「同一条命令再调一次」", "再调一次" in _out, _out[:400])
    ck("②**一格都没挥**（到点就停，不是干完再说）", CALLS["use_tool"] == 0, str(CALLS["use_tool"]))
    M._CALL_BUDGET_S = _BUDGET_BAK

    print("\n②b 预算够用时照常锄完（别把正常路径也拦了）")
    CALLS["use_tool"] = 0
    _out2 = M._farm_till(x1=0, y1=0, x2=1, y2=0)
    ck("②b 两格都挥了", CALLS["use_tool"] == 2, str(CALLS["use_tool"]))
    ck("②b 没有「限时」字样", "单次限时" not in _out2, _out2[:200])

    # ── ③ 播种到点停手 ────────────────────────────────────────────
    print("\n③ 播种：同样到点停手（种子/地都没问题，是时间到了）")
    ROWS.clear()
    ROWS.update({(0, 0): {"terrain": "HoeDirt", "diggable": True},
                 (1, 0): {"terrain": "HoeDirt", "diggable": True}})
    CALLS["use_item"] = 0
    M._CALL_BUDGET_S = 1e-9
    _out3 = M._farm_plant(seed_name=SEED, x1=0, y1=0, x2=1, y2=0)
    ck("③回执点明「单次限时」", "单次限时" in _out3, _out3[:400])
    ck("③…说清还剩几格", "剩 2 格" in _out3, _out3[:400])
    ck("③…并给下一步「再调一次」", "再调一次" in _out3, _out3[:400])
    ck("③**一颗都没种**", CALLS["use_item"] == 0, str(CALLS["use_item"]))
    M._CALL_BUDGET_S = _BUDGET_BAK

    # ── ④ 节日巡礼的默认预算 ──────────────────────────────────────
    print("\n④ 节日巡礼的默认预算也收到这条线上（原来 120s ⇒ 必然被掐）")
    _dflt = inspect.signature(M._festival_interact).parameters["budget_s"].default
    ck("④默认预算 == `_CALL_BUDGET_S`", _dflt == M._CALL_BUDGET_S, str(_dflt))
    ck("④…且 < 55（60s 客户端超时之下）", _dflt < 55, str(_dflt))

    # ── ⑤ 浇水走异步白名单 ────────────────────────────────────────
    print("\n⑤ 浇水是子进程脚本 ⇒ 进异步白名单（挂到收工或 45s，收工把原文交回来）")
    ck("⑤`water_crops` 在 `_ASYNC_SCRIPTS` 里", "water_crops" in M._ASYNC_SCRIPTS,
       str(sorted(M._ASYNC_SCRIPTS)))
    print("\n⑤b 异步回包的前缀判据（老账：判 \"🚀\" 永远不命中 ⇒ 假完成）")
    for _s, _exp in (("⏰ 异步唤醒时间——脚本「water_crops」后台运行中（30s，job job3）", True),
                     ("✅ 脚本「water_crops」收工（跑了 92s）", True),
                     ("🛑 脚本「water_crops」被停", True),
                     ("💧 浇了 41 格，剩 0 格没浇上", False),
                     ("", False)):
        ck(f"⑤b `{_s[:16]}` ⇒ {'异步' if _exp else '正文'}", M._async_park_reply(_s) is _exp, _s[:40])
    _src_w = inspect.getsource(M.water_crops)
    ck("⑤b 浇水那个调用点用的是**同一个**判据（别再各写一份）",
       "_async_park_reply(result)" in _src_w, _src_w[:300])

    # ── ⑥ 日常搭话 → 自动走节日互动 ───────────────────────────────
    print("\n⑥ 人在节日场地：`social ops=chat` 自动改走 `festival interact`（不导航）")
    ACT = [{"name": "Abigail", "displayName": "阿比盖尔", "x": 27, "y": 69}]

    class FestApi:
        def __init__(self, actors):
            self.actors = actors

        def festival_status(self):
            return {"ok": True, "actors": self.actors}

        def surroundings(self, *_a):
            CALLS["surroundings"] += 1
            return {"tiles": [], "npcs": []}

    _fi_bak = M._festival_interact
    M._festival_interact = lambda name="", budget_s=None: f"【FESTIVAL-INTERACT {name}】"

    M.api = FestApi(ACT)
    CALLS["surroundings"] = 0
    _o6 = M.chat_npc("阿比盖尔")
    ck("⑥回执说明是**节日现场**改成节日互动", "节日现场" in _o6 and "festival interact" in _o6, _o6[:300])
    ck("⑥真的走了 festival interact（点了名）", "【FESTIVAL-INTERACT 阿比盖尔】" in _o6, _o6[:300])
    ck("⑥**没有**去扫周围/导航（surroundings 一次没调）", CALLS["surroundings"] == 0,
       str(CALLS["surroundings"]))
    _o6b = M.chat_npc("罗宾")           # 不在场
    ck("⑥不在场的人**不改道**（该报找不到就报找不到）", "【FESTIVAL-INTERACT" not in _o6b, _o6b[:200])
    M.api = FestApi([])                 # 没有节日
    CALLS["surroundings"] = 0
    _o6c = M.chat_npc("阿比盖尔")
    ck("⑥没有节日时**不改道**（照常扫周围）", CALLS["surroundings"] >= 1, str(CALLS["surroundings"]))
    M._festival_interact = _fi_bak

    # ── ⑦ 反过来：没节日时节日互动把路指准 ────────────────────────
    print("\n⑦ 没节日时 `festival interact`：不硬跳，但把「该敲 social chat」指到手上")
    class NoFestApi(FestApi):
        def festival_status(self):
            return {"ok": False, "error": "No active event"}

    M.api = NoFestApi([])
    _o7 = M._festival_interact(name="阿比盖尔")
    ck("⑦点明「没有节日活动」", "现在没有正在进行中的节日活动" in _o7, _o7[:300])
    ck("⑦…并给出 social chat 这条正路", 'social(ops="chat"' in _o7, _o7[:300])
    ck("⑦…且带上人名字", "阿比盖尔" in _o7, _o7[:300])
finally:
    M._CALL_BUDGET_S = _BUDGET_BAK if _BUDGET_BAK is not None else 40.0
    for k, v in _real.items():
        setattr(M, k, v)

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 长活不再硬撑到 60s：锄/种到点停手、节日巡礼收预算、浇水走异步；日常↔节日对话自动改道。")
