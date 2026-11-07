# -*- coding: utf-8 -*-
"""📋 「需要帮助」求助栏：**有接取按钮**，得让 AI 看得见、点得着 —— 纯 Python + C# 源码自验。2026-11-08

恒：「**"需要帮助" billboard 好像压根没跟 ai 说要接取任务，没有任何按钮引导**」。

反编译实据（`dotnet` 反射 `StardewValley.Menus.Billboard`，不是猜的）：
  · 字段 `ClickableComponent acceptQuestButton` —— **这块板有接取按钮**；
  · 方法 `UpdateDailyQuestButton()` —— 按钮状态跟着"接没接"变；
  · 字段 `Boolean dailyQuestBoard` —— 同一张 `Billboard` 菜单也用于"日历/书商"那类板。

⇒ 三处漏了：
  ① C# `/menu` 的按钮表里**没有 `acceptQuestButton`**（那张表是手写字段名清单）⇒ AI 眼里"没有任何按钮"；
  ② 我们文档还写着「Billboard **无 accept 按钮**（自动在任务簿里）」——**错的**，点了才算接；
  ③ `menu read` 的 Billboard 分支用 `quest_progress`（读的是**任务簿**）判断"今天有没有求助"，
     可**没接的求助根本不在簿里** ⇒ 它印「（今日没有求助任务）」，越看越懵。

判据：
  ① C# 按钮表里有 `acceptQuestButton`（且两处 `Billboard` 判定用的是 `dailyQuestBoard` 之外的东西，别乱动）；
  ② `menu read`：有按钮 ⇒ 给 `menu click(button=acceptQuestButton)`；没按钮（老 DLL）⇒ 如实说"这版 DLL 还没报"；
  ③ 没接时**不许**说"今天没有求助"，要说"还没接、不在任务簿里"；
  ④ 每日求助栏 vs 社区中心 (62,94) 特别任务板：`help` 得能分开，别把人带到错的那块；
  ⑤ 每日注入那句也得点名按钮（老话把 AI 教成"没按钮"）。
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
    """假 7843：`menu read` 的 Billboard 分支只碰 `/menu` 与 `quest_progress`。"""

    def __init__(self, buttons, quest=None):
        self.buttons, self.quest = buttons, quest
        self.calls = []

    def _menujson(self):
        return {"open": True, "type": "Billboard", "title": "",
                "customerName": "", "items": [], "buttons": self.buttons}

    def menu(self):
        self.calls.append("/menu")
        return self._menujson()

    def _ai_get(self, ep, *_a, **_k):
        self.calls.append(ep)
        if ep == "/menu":
            return self._menujson()
        return {}

    def state(self):
        return {"location": {"name": "Town", "uniqueName": "Town"}, "player": {"name": "Claude"},
                "time": {"season": "spring", "dayOfMonth": 15, "timeOfDay": 900}}

    def game_state(self):
        return self.state()

    def quest_progress(self):
        return {"quests": ([self.quest] if self.quest else [])}

    def day_key(self):
        return "spring15"

    def _ai_post(self, *_a, **_k):
        return {"ok": True}


_BAK = (M.api, M._with_state)
M._with_state = lambda s: s
M._HW_REMIND_KEY["last"] = ""

try:
    print("\n① 展板开着 + DLL 报了 acceptQuestButton ⇒ 必须给「点它接取」这句")
    _a = FakeApi([{"name": "acceptQuestButton", "x": 640, "y": 520},
                  {"name": "upperRightCloseButton", "x": 900, "y": 100}])
    M.api = _a
    _out = M.menu(ops="read")
    ck("①读到了菜单（问了一次 `/menu`）", "/menu" in _a.calls, str(_a.calls))
    ck("①给出接取按钮名", "acceptQuestButton" in _out, _out[:400])
    ck("①给的是能直接敲的调用式", "menu click(button=acceptQuestButton)" in _out, _out[:400])
    ck("①说清「点了才算接」", "才算接" in _out or "没点这个按钮" in _out, _out[:400])

    print("\n② 老 DLL（/menu 没报这个按钮）⇒ 如实说，别让人以为没得点")
    _a = FakeApi([{"name": "upperRightCloseButton", "x": 900, "y": 100}])
    M.api = _a
    _out2 = M.menu(ops="read")
    ck("②明确说「这版 DLL 还没报」", "这版 DLL 还没报" in _out2, _out2[:400])
    ck("②不算它造出按钮名来", "menu click(button=acceptQuestButton)" not in _out2, _out2[:400])

    print("\n③ 没接（任务簿里没有）⇒ 不许说「今天没有求助」")
    _a = FakeApi([{"name": "acceptQuestButton", "x": 640, "y": 520}], quest=None)
    M.api = _a
    _out3 = M.menu(ops="read")
    ck("③不说「今日没有求助任务」", "今日没有求助任务" not in _out3, _out3[:400])
    ck("③说清「还没接、所以不在任务簿里」", "还没接" in _out3, _out3[:400])
    ck("③并给出接取按钮", "acceptQuestButton" in _out3, _out3[:400])

    print("\n④ 接过的（任务簿里有）⇒ 照常报任务内容")
    _a = FakeApi([{"name": "acceptQuestButton", "x": 640, "y": 520}],
                 quest={"source": "questOfTheDay", "title": "罗宾的木材", "description": "我是罗宾",
                        "daysLeft": 1, "moneyReward": 250, "completed": False})
    M.api = _a
    _out4 = M.menu(ops="read")
    ck("④印了求助标题", "罗宾的木材" in _out4, _out4[:300])
    ck("④仍给接取按钮（不该接的话不点就行）", "acceptQuestButton" in _out4, _out4[:300])

    print("\n⑤ 每日注入那句：点名按钮，且不再写「无 accept 按钮」")
    M._HW_REMIND_KEY["last"] = ""
    _a = FakeApi([], quest={"source": "questOfTheDay", "title": "罗宾的木材",
                            "description": "我是罗宾，帮我弄35个木材", "daysLeft": 1,
                            "moneyReward": 250, "completed": False})
    M.api = _a
    _inj = M._helpwanted_inject("Town")
    ck("⑤注入了", bool(_inj), repr(_inj))
    ck("⑤点名 acceptQuestButton", "acceptQuestButton" in _inj, _inj)
    ck("⑤不再说「无 accept 按钮」", "无 accept 按钮" not in _inj, _inj)

    print("\n⑥ `help` 能分开两块板（每日求助栏 ≠ 社区中心特别任务板）")
    M._place_lines = lambda q, n=3: []
    _h = M.help("怎么接求助")
    ck("⑥指到求助栏（皮埃尔店西墙）", "皮埃尔商店(求助布告栏)" in _h, _h[:300])
    ck("⑥点名 acceptQuestButton", "acceptQuestButton" in _h, _h[:300])
    _h2 = M.help("接特别订单")
    ck("⑥特别订单那条还在（没被顶掉）", "(62,94)" in _h2, _h2[:300])

    print("\n⑦ C# 源码钉：按钮表里有 acceptQuestButton（反射实据的字段名）")
    _cs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "ModEntry.cs"), encoding="utf-8").read()
    ck("⑦`/menu` 的按钮表里有它", '"acceptQuestButton",' in _cs, "没加 => /menu 永远不报这个按钮")
    ck("⑦它在 `buttons` 那张字段名清单里（挨着别的 accept*）",
       '"acceptLeftQuestButton", "acceptRightQuestButton"' in _cs
       and _cs.index('"acceptQuestButton",') > _cs.index('"acceptLeftQuestButton"'), "位置不对")
finally:
    M.api, M._with_state = _BAK

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 求助栏：按钮报得出来、`menu read` 给得出、老 DLL 不撒谎、两块板分得开。")
