"""🏛️ 献祭板「先走到跟前再点」闸 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-25

恒真机：「**推完任务甚至提交完第一个工艺包，ai 什么板子都互动不了**」——
他以为坏档了。真身是游戏自己抛越界（`ArgumentOutOfRangeException` @ `checkBundle`），
三条一模一样的栈，时间点对上 AI 那句 `scene at 14 23`；而当时 AI 站在 **CommunityCenter (32,23)**。

根因（反编译实锤）：板子那一支 `checkAction` **混用了两个坐标** ——
用**目标格**找板子、用**站格**算"在哪个区"（`getAreaNumberFromLocation(who.Tile)`）。
整间屋**大量格子不属于任何区**（工艺室 x0-20/y12-28、公告板 x22-49/y13-21…，走廊/门口都不在内）
⇒ 站区外 → -1 → `bundleMutexes[-1]` → 游戏抛异常，我们这边只看到 **HTTP 超时**。

测六件：
  ① 不在社区中心 → **不管**（别的图照旧）
  ② 已经贴着板子（含对角）→ **不管**，照旧直接点
  ③ 隔着走廊、`/progress` 说那就是板子 → **先走位**；走到了才放行，并如实报"已走到"
  ④ 走位也没到跟前 → **拦住不点**（且**真的没发出 interact**），并给下一步
  ⑤ 远是远，但那格**不是板子**（电视/日历那类）→ **不许插手**（远程交互是既有能力）
  ⑥ `/progress` 读不回来 → **不拦**（不拿"我读不到"当"它不是板子"）
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []
NOTE = (14, 23)          # 工艺室那块（真机 `/progress` 报的就是这个坐标）


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class FakeApi:
    """只需 state / _get / interact_at 三个口。"""
    def __init__(self, loc="CommunityCenter", me=(32, 23), progress=True, after=(14, 24), here=True):
        self.loc, self.me = loc, me
        self._progress, self.after, self.here = progress, after, here
        self.state_calls = 0
        self.interacted = []

    def state(self):
        self.state_calls += 1
        # ⚠️ 第一次=走位前、第二次=走位后（走位会改变站位，这是这个闸的全部意义）
        x, y = self.me if self.state_calls == 1 else self.after
        return {"location": {"name": self.loc}, "player": {"x": x, "y": y}}

    def _get(self, path):
        if not self._progress:
            raise RuntimeError("progress unreachable")
        return {"ok": True, "areas": [
            {"n": 1, "name": "工艺室", "noteShould": True, "noteHere": self.here,
             "notePos": {"X": NOTE[0], "Y": NOTE[1]}},
            # ⚠️ 另一块：**该有、也没有，但坐标不同** —— 用来证明这道闸是**按坐标**认的，
            #    不会因为"厂里有块没摆上的板"就把别的格子也一起拦了。
            {"n": 0, "name": "茶水间", "noteShould": True, "noteHere": False,
             "notePos": {"X": 14, "Y": 5}},
            {"n": 2, "name": "鱼缸", "noteShould": False, "noteHere": False,
             "notePos": {"X": 40, "Y": 10}},
        ]}

    def interact_at(self, x, y):
        self.interacted.append((x, y))
        return {"ok": True, "actionTriggered": True}


_real = {n: getattr(M, n) for n in ("api", "_walk_and_wait")}
WALKS = []
try:
    M._walk_and_wait = lambda loc, x, y, timeout=None: (WALKS.append((loc, x, y)), (True, ""))[1]

    print("\n① 不在社区中心 → 不插手")
    M.api = FakeApi(loc="Farm")
    ck("空手而归（既不拦也不走）", M._cc_note_guard(*NOTE) == ("", ""), str(M._cc_note_guard(*NOTE)))

    print("\n② 已经贴着板子 → 不插手（照旧直接点）")
    M.api = FakeApi(me=(14, 24))
    WALKS.clear()
    ck("贴着（正下方）→ 不拦不走", M._cc_note_guard(*NOTE) == ("", "") and not WALKS, str(WALKS))
    M.api = FakeApi(me=(15, 24))
    ck("贴着（对角）→ 同样不拦", M._cc_note_guard(*NOTE) == ("", ""), "")

    print("\n③ 隔着走廊点板子 → 先走过去，再放行")
    M.api = FakeApi(me=(32, 23), after=(14, 24))
    WALKS.clear()
    blk, pre = M._cc_note_guard(*NOTE)
    ck("**不拦**（走得到就照做，别推给 AI）", blk == "", blk)
    ck("…真的去走位了，且落在板子正下方 (14,24)",
       WALKS and WALKS[0][1:] == (14, 24), str(WALKS))
    ck("…并如实报「已走到」（别让人以为还在原地）", "已走到" in pre, pre)

    print("\n④ 走也走不到 → 拦住**不点**，并给下一步")
    M.api = FakeApi(me=(32, 23), after=(32, 23))     # 走完还在原地 = 没走到
    M._walk_and_wait = lambda loc, x, y, timeout=None: (False, "四邻站不住")
    blk, pre = M._cc_note_guard(*NOTE)
    ck("拦住（非空拦截语）", bool(blk), blk)
    ck("…说清为什么不点（站格算不出区 → 游戏自己抛异常）",
       "哪个区" in blk and "超时" in blk, blk)
    ck("…给下一步（map go / walk_to 再 scene at）",
       "map go" in blk and "scene at" in blk, blk)
    ck("…⚠️ **真的没发出 interact**", M.api.interacted == [], str(M.api.interacted))
    M._walk_and_wait = lambda loc, x, y, timeout=None: (WALKS.append((loc, x, y)), (True, ""))[1]

    print("\n⑤ 远是远，但那格不是板子 → 不许插手")
    M.api = FakeApi(me=(32, 23))
    WALKS.clear()
    ck("点电视/日历那类照旧远程点", M._cc_note_guard(40, 30) == ("", "") and not WALKS, str(WALKS))

    print("\n⑥ /progress 读不回来 → 不拦（不误伤）")
    M.api = FakeApi(me=(32, 23), progress=False)
    WALKS.clear()
    ck("读不到就不管", M._cc_note_guard(*NOTE) == ("", "") and not WALKS, str(WALKS))

    print("\n⑦ 板子**该在、此刻不在**（真的不在场上）→ 拦住 + 说清「只有重开游戏才回得来」")
    # ⚠️ 2026-09-25 深夜真机：`/progress` 两端都是 `should=True / here=False` ⇒ 那格上**什么都没有**
    #    （板是**运行时摆上去的**，地图一重载就没了）。症状=**光标不变、点了没反应、还不报错**。
    M.api = FakeApi(me=(32, 23), here=False)
    WALKS.clear()
    blk, pre = M._cc_note_guard(*NOTE)
    ck("拦住（不再让 AI 对着空气点）", bool(blk), blk)
    ck("…说清「不在场上」（该有 · 那格是空的）", "不在场上" in blk and "空的" in blk, blk)
    ck("…⚠️ **不许**教 AI「出去再进来」（恒实测那条没用，教了等于让它白跑）",
       "出去再进来" in blk and "实测救不回来" in blk, blk)
    ck("…⚠️ **真的没发出 interact**", M.api.interacted == [], str(M.api.interacted))
    ck("…也**没白走位**（走过去也没用，别浪费一趟）", WALKS == [], str(WALKS))

    print("\n⑧ 这道闸是**按坐标**认的，不是「厂里有块没摆的就全场拦」")
    M.api = FakeApi(me=(32, 23), here=False)
    ck("别的格子照旧放行（茶水间那块没摆 ≠ 电视不能点）",
       M._cc_note_guard(14, 5)[0] and M._cc_note_guard(40, 30) == ("", ""), "")

    print("\n⑨ 第一块板**连续两次**没出内容 → 第二次补上恒那句话（他的原话，一个字不改）")
    M.api = FakeApi(me=(32, 23), here=False)
    M._CC_BOARD_FAILS.clear()
    o1 = M._cc_board_after(*NOTE, "⚠️ 拦住了")
    o2 = M._cc_board_after(*NOTE, "⚠️ 拦住了")
    ck("第一次**不加**（一次可能是手滑/偶发）", "剧情异常中止" not in o1, o1)
    ck("第二次**加上恒的原话**",
       "如果剧情异常中止导致第一块板不可交互，请联系host重开游戏"
       "（可以先继续游玩保存今天），再双人进入社区中心尝试交互。" in o2, o2)
    ck("…第三次继续提醒（不是只响一次）", "剧情异常中止" in M._cc_board_after(*NOTE, "⚠️"), "")
    ck("…**出了内容就清零**（下次失败重新从 1 数）",
       "剧情异常中止" not in M._cc_board_after(*NOTE, "🎯 与 目标 交互成功")
       and "剧情异常中止" not in M._cc_board_after(*NOTE, "⚠️"), "")
    ck("…⚠️ 只管**第一块板**那一格（别处失败再多也不贴）",
       "剧情异常中止" not in M._cc_board_after(14, 5, "⚠️"), "")

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
