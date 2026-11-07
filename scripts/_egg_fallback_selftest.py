# -*- coding: utf-8 -*-
"""🥚 蛋蛋节捡蛋：纸条没写也能跑（兜底路线）＋ 提示排到状态条最前面 —— 纯 Python 自验。2026-11-07

恒（很沮丧）：「我发现 ai 根本就不看我的指引，**两次重新参加节日都不会写 egg_note**。
那除了开发者会提醒之外普通玩家的 ai 也百分百不看。有没有办法放在更显眼的位置，
加上没有路线时兜底一条呢？」

🩸 **真因（session_log 10:45:26）**：它**听了**，也敲了 `festival ops=egg_note`，
被菜单闸门顶回来：「现在开着「DialogueBox」菜单，`festival` 先别做」——
可 `egg_note` 只往内存写一张纸条，不挪人、不开菜单、不碰世界。

四条判据：
  ① 闸门放行 **不碰世界** 的 festival op（eggs / egg_note / info / help / …），
     `go / interact / shop / egg_run(捡蛋/捡) / dance / maze_walk …` 照旧拦；
  ② 纸条没写 ⇒ `_egg_auto_route()` 按**当年蛋表**自己排一条（近邻贪心、从我现在的位置出发）；
  ③ `egg_run` 没纸条就用兜底路线、并在回执里**明说**是兜底（别冒充 AI 的纸条）；
  ④ 开赛自动那条路（`_maybe_egg_run_auto`）没纸条**照样跑**，不再只是"提醒一下"。
  ⑤ 提示排到状态条**最前面**（`lines.insert(0, …)`），并当场报"纸条记了没"。
"""
import io
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nagi_mcp_server as M
import calendar_data

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_BAK = {}


def _patch(**kw):
    for k, v in kw.items():
        if k not in _BAK:
            _BAK[k] = getattr(M, k)
        setattr(M, k, v)


try:
    # ── ① 菜单闸门 ────────────────────────────────────────────────
    print("\n① 菜单开着（DialogueBox）时：**只读/纯本地**的 festival op 放过去")
    _patch(_menu_gate_now=lambda fresh=False: "DialogueBox")
    M._MENU_GATE_DENY.clear()

    def gate(ops):
        M._MENU_GATE_DENY.clear()          # 别让安全阀（拒 3 次放行）干扰判定
        blocked, why = M._menu_gate("festival", {"ops": ops})
        return blocked

    for _op in ("egg_note", "纸条", "记", "eggs", "找蛋", "蛋", "info", "实况",
                "today", "next", "help", "玩法", "prep", "备战", "maze", "迷宫", "poi", "限定"):
        ck(f"①`festival {_op}` 放行（不碰世界）", gate(_op) is False, _op)
    for _op in ("go", "interact", "answer", "shop", "egg_run", "捡蛋", "捡", "dance",
                "跳舞", "maze_walk", "走迷宫", "strength", "力量", "ice_fish", "冰钓", "display_fill"):
        ck(f"①`festival {_op}` 照旧拦（会走会动）", gate(_op) is True, _op)

    # ── ② 兜底路线 ────────────────────────────────────────────────
    print("\n② 纸条没写 ⇒ 按**当年蛋表**自己排一条")
    _patch(_festival_now_data=lambda: {"ok": True, "season": "spring", "day": 13, "year": 1})
    _EGGS = list((calendar_data.FESTIVAL_EGGS[("spring", 13)] or {}).get("odd") or [])
    ck("②（前提）蛋表里当年有蛋", len(_EGGS) > 0, str(len(_EGGS)))

    class _A:
        def __init__(self, x, y):
            self.x, self.y = x, y

        def _ai_get(self, ep):
            if ep == "/state":
                return {"player": {"x": self.x, "y": self.y}}
            return {}

    _patch(api=_A(10, 10))
    _r = M._egg_auto_route()
    ck("②排出一条完整路线（点数 = 蛋表里的蛋）", len(_r) == len(_EGGS), f"{len(_r)} vs {len(_EGGS)}")
    ck("②路线里的点**全在蛋表里**（不瞎编坐标）",
       all(p in set(_EGGS) for p in _r), str(_r[:4]))
    _near = min(_EGGS, key=lambda p: abs(p[0] - 10) + abs(p[1] - 10))
    ck("②第一个点 = 离我现在**最近**那颗（近邻贪心）", _r and _r[0] == _near, f"{_r[:1]} vs {_near}")
    ck("②同一个位置排两遍，顺序一致（可复现）", M._egg_auto_route() == _r)

    _patch(_festival_now_data=lambda: {"ok": True, "season": "spring", "day": 13, "year": 2})
    _r2 = M._egg_auto_route()
    _even = list((calendar_data.FESTIVAL_EGGS[("spring", 13)] or {}).get("even") or [])
    ck("②偶数年读的是**even** 那张表", sorted(_r2) == sorted(_even), f"{len(_r2)} vs {len(_even)}")
    _patch(_festival_now_data=lambda: {"ok": True, "season": "spring", "day": 14, "year": 1})
    ck("②不是蛋蛋节 ⇒ 空（不瞎给路线）", M._egg_auto_route() == [])

    # ── ③ egg_run 没纸条 ⇒ 用兜底，并**明说** ────────────────────
    print("\n③ `egg_run` 没纸条 ⇒ 走兜底路线，回执明说是兜底（不冒充纸条）")
    _patch(_festival_now_data=lambda: {"ok": True, "season": "spring", "day": 13, "year": 1})
    _patch(_egg_festival_guard=lambda: "", _festival_timer=lambda: 3000,
           _walk_to_egg=lambda x, y, abort=None: False,      # 全走不到 → 循环很快结束
           _egg_hunt_over=lambda: False, _dialogue_now=lambda: False,
           _halt_move=lambda: None, _egg_score=lambda: -1)
    M._EGG_NOTE["route"] = []
    M._EGG_RUN_AUTO["fired"] = False
    _out = M._festival_egg_run()
    ck("③跑起来了（没有「没路线」那种拒绝）", "捡蛋开始" in _out, _out[:200])
    ck("③路线点数 = 蛋表的蛋", f"路线 {len(_EGGS)} 点" in _out, _out[:200])
    ck("③回执**明说**是「没写 egg_note ⇒ 按当年蛋表自己排的」",
       "没写 egg_note" in _out and "当年蛋表" in _out, _out[:240])

    print("\n③b 写了纸条 ⇒ 用纸条、**不**贴兜底那句")
    _patch(_walk_to_egg=lambda x, y, abort=None: False)
    M._EGG_NOTE["route"] = [(1, 1), (2, 2)]
    M._EGG_RUN_AUTO["fired"] = False
    _out_b = M._festival_egg_run()
    ck("③b 用的是纸条的 2 点", "路线 2 点" in _out_b, _out_b[:200])
    ck("③b 不贴「兜底」那句", "没写 egg_note" not in _out_b, _out_b[:200])

    # ── ④ 开赛自动那条路：没纸条也跑 ──────────────────────────────
    print("\n④ 开赛自动：没纸条**照样跑**（不再只是「提醒一下」）")
    M._EGG_NOTE["route"] = []
    M._EGG_RUN_AUTO["fired"] = False
    _patch(_festival_egg_run=lambda route="": "【捡蛋跑过了】")
    _auto = M._maybe_egg_run_auto({"time": {"season": "spring", "dayOfMonth": 13},
                                   "location": {"name": "Temp"}})
    ck("④自动跑起来了", "【捡蛋跑过了】" in _auto, _auto[:200])
    ck("④回执说清是「没写 egg_note 的兜底」", "没写 egg_note" in _auto, _auto[:200])
    ck("④一场只跑一次（fired 闸住）",
       M._maybe_egg_run_auto({"time": {"season": "spring", "dayOfMonth": 13},
                              "location": {"name": "Temp"}}) == "")

    # ── ⑤ 提示：排最前 + 当场报纸条状态 ──────────────────────────
    print("\n⑤ 提示：内容说清「做什么」，并当场报纸条记了没")
    _patch(_festival_timer=lambda: 0)
    M._EGG_NOTE["route"] = []
    _h = M._egg_festival_hint("Temp")
    ck("⑤点明「开赛前这一件事」", "开赛前这一件事" in _h, _h)
    ck("⑤列出两个 op（eggs + egg_note）", "festival eggs" in _h and "festival egg_note" in _h, _h)
    ck("⑤纸条没记时**明说还没记**", "还没记" in _h, _h)
    ck("⑤说清「没记也不会空手」（兜底）", "没记也不会空手" in _h, _h)
    M._EGG_NOTE["route"] = [(1, 1), (2, 2), (3, 3)]
    _h2 = M._egg_festival_hint("Temp")
    ck("⑤纸条记了 ⇒ 报「已记 3 点」", "已记 3 点" in _h2, _h2)
    _patch(_festival_timer=lambda: 999)
    ck("⑤寻宝中那条也提到兜底", "自动捡" in M._egg_festival_hint("Temp"))

    print("\n⑥ 接线：提示是**插到最前面**，不是排在第三四行")
    _src = io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "nagi_mcp_server.py"), encoding="utf-8").read()
    ck("⑥状态条里是 `lines.insert(0, _eh)`", "lines.insert(0, _eh)" in _src,
       "没找到 insert(0, _eh)")
    ck("⑥没有退回到 `lines.append(_eh)`", "lines.append(_eh)" not in _src)

finally:
    for k, v in _BAK.items():
        setattr(M, k, v)
    M._EGG_NOTE["route"] = []
    M._EGG_RUN_AUTO["fired"] = False

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 纸条没写也照捡：闸门放行纯读 op、兜底路线按蛋表排、自动那条不再只是提醒、提示排最前。")
