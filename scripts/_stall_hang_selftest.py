# -*- coding: utf-8 -*-
"""🩺 打怪愣住 / 探索打转：2026-10-04 恒真机两连报，这一颗钉子钉住两条根因。

## 现场（`_live/_v203w_stall_trace.log` + 游戏侧 `Mods/NagiBridge/requests.log` 对读）
恒：「这轮也打怪愣住两次，而且刚刚 9:44 是跟史莱姆」「探索重复尝试同坐标」。

**病① 打怪愣住 = `POST /tool` 永久挂起（53 秒站着挨打）**
  · `09:50:01 → 09:50:54` 五条 `POST /tool`，间隔 **11/10/11/10/11 秒**（= Python `timeout=10` + 重试）
  · 同期 `/state`：`canMove=True freezePauseMs=0 stationarySeconds=53`（**游戏没锁人**，是脚本在等回包）
  · 血 116 → 73（Iridium Bat / Bug / Big Slime 围着啃）
  · 实机 A/B（同一时刻同状态，手上 Mega Bomb）：
      `POST /tool {}`                → **14 秒收不到任何回包**（HTTP 000）
      `POST /tool {"name":"Pickaxe"}` → **0.008 秒** `{"ok":true,"tool":"Iridium Pickaxe"}`
  · 根因 C#：`Farmer.CurrentTool => CurrentItem as Tool`（`Farmer.cs:1745`）——手上是炸弹/食物时
    就是 null，而 `HandleTool` 只有 `if (WateringCan) … else if (CurrentTool != null) …`，
    **没有 else** ⇒ 委托掉出去 = **一个回包都不给**（HTTP 永久挂起）。
  · 触发它的手：C# guard 借走手持槽砍怪后**无条件**把槽写回 `prev`（=脚本开打前的炸弹）
    ⇒ 脚本 `/select` 好的镐子被抢回去 ⇒ 接着 `/tool {}` 时手上是炸弹。

**病② 探索打转 = 同一个坐标反复走**
  · `09:51:20 → 09:51:41` 四条 `POST /walk_to` 各 5 秒空档，人一直站在 (27,23)；
    `_v203w_stall_trace` 同一段报 `[动] 停住共 24.0s（站桩）`；随后 `/craft`（造楼梯）
  · 根因 Python：`natural_walk(walk_only=True)` **走不到也返回 True**（"走路没到位就不传，下轮再走"）
    ⇒ 人没挪窝 ⇒ 下一轮按"当前坐标 + 周边岩体"算出**一模一样的目标** ⇒ 空转到 `explore_count>=6`。

这条钉子钉住：C# 永不挂 + guard 不抢槽 + Python 能点名就点名 + 超时不再静默 + 探索记账换目标。
"""
import contextlib
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


def src(fn):
    return io.open(os.path.normpath(os.path.join(HERE, fn)), encoding="utf-8").read()


def block(text, start_pat, span=90):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.search(start_pat, ln):
            return "\n".join(lines[i:i + span])
    return ""


CS = src(os.path.join("..", "ModEntry.cs"))
BC = src("bomb_common.py")
MR = src("mine_run.py")
BM = src("bomb_mine.py")

# ══════════════════════════════════════════════════════════════════════════
print("① C# `/tool`：**任何一条分支都必须回包**（病①的地基）")
_tool = block(CS, r"private object HandleTool\(HttpListenerContext", span=150)
ck("…有 `CurrentTool == null` 的明确回包分支", "farmer.CurrentTool == null" in _tool)
ck("…那条回包带 `ok = false` + 把「手上现在是什么」报出去",
   "ok = false" in _tool and "handItem" in _tool)
_i_name = _tool.find('if (name != "current")')
_i_null = _tool.find("farmer.CurrentTool == null")
ck("…判据在「点名」**之后**（点名那条能自救，别把它一起挡了）",
   0 <= _i_name < _i_null, f"name@{_i_name} null@{_i_null}")
ck("…整段包了 try/catch/finally 兜底（排空循环只 log，不 SetResult —— 见 /interact 那条教训）",
   "try { ToolAction(); }" in _tool and "finally" in _tool and "TrySetResult" in _tool)
ck("…注释里留了现场证据（53 秒 / A/B 两个数）",
   "53" in _tool and "0.008" in _tool)

print("② C# guard：**只还自己借的那次槽**（别把脚本刚点好的镐子抢回炸弹）")
ck("…新字段 `_guardPendingRestoreSlot` 存在", "_guardPendingRestoreSlot" in CS)
_restore = block(CS, r"// ② 先还债：挥完那一刀后", span=20)
ck("…还债前核对「槽还是不是我们借的那个」",
   "farmer.CurrentToolIndex == _guardPendingRestoreSlot" in _restore)
ck("…核对不过就放弃还债（两个字段一起清）",
   "_guardPendingRestore = -1;" in _restore and "_guardPendingRestoreSlot = -1;" in _restore)
ck("…借出时记下槽号（⑩ 那段）", "_guardPendingRestoreSlot = slot;" in CS)
ck("…guard 清零那段也带上它", re.search(r"_guardPendingRestore = -1;\s*\n\s*_guardPendingRestoreSlot = -1;", CS) is not None)

print("③ Python：**能点名就点名**（不点名 = `/tool {}` = 靠手上正好是工具）")
ck("…`weapon_special(name=None)` 收名字", "def weapon_special(self, name=None)" in BC)
ck("…特殊攻击把 name 一起发出去", 'd["name"] = name' in BC and '{"special": True}' in BC)
ck("…`swing()` 重砸点名武器", "self.weapon_special(self.weapon_name)" in BC)
ck("…`swing()` 平砍点名武器（两处）", BC.count("self.use_tool(self.weapon_name)") >= 2)
ck("…`smash_rock()` 点名那把真镐子（不是 `current`）", "self.use_tool(pick_name)" in BC)
ck("…`mine_run` 挥武器也点名", "self.use_tool(self.weapon_name)" in MR)

print("④ 超时**不再静默**（现场那 53 秒日志里一个字都没有，这条是「下次一眼看见」）")
_post = block(BC, r"def _post\(self, ep", span=20)
ck("…`_post` 单独接 `requests.exceptions.Timeout`", "requests.exceptions.Timeout" in _post)
ck("…且打了一行带 ⏱️ 的日志", "⏱️" in _post)
_get = block(BC, r"def _get\(self, ep", span=14)
ck("…`_get` 同样接住超时", "requests.exceptions.Timeout" in _get and "⏱️" in _get)

print("⑤ Python 探索：**走过的坑记账 + 回读验真**（病②）")
_ex = block(BM, r"🆕 2026-10-04 恒（现场日志连报", span=60)
ck("…有本层「已试目标」账本", "_explore_tried" in _ex and "tried = self._explore_tried.setdefault(level, set())" in _ex)
ck("…候选里剔掉试过的", "fresh = [r for r in far_rocks if r not in tried]" in _ex)
ck("…走完**回读坐标**验真（它自己不会报错）",
   'if (p2.get("x"), p2.get("y")) == (px, py):' in _ex and "tried.add((target_x, target_y))" in _ex)
ck("…全走不到时走「造楼梯」保底路（不再空转）", "探索目标全走不到 → 造楼梯跳关" in _ex)
ck("…记账每趟进本层清一次（别让上次的账卡住这次）", "if explore_count == 1:" in _ex and "tried.clear()" in _ex)

# ══════════════════════════════════════════════════════════════════════════
print("⑥ 行为钉：超时确实会吼出来（真跑一次 `_post`，用假的 requests 让它超时）")


class _FakeExceptions:
    class Timeout(Exception):
        pass


class _FakeSession:
    def get(self, *a, **k):
        raise _FakeExceptions.Timeout()

    def post(self, *a, **k):
        raise _FakeExceptions.Timeout()


class _FakeRequests:
    exceptions = _FakeExceptions
    Session = _FakeSession

    @staticmethod
    def post(*a, **k):
        raise _FakeExceptions.Timeout()

    @staticmethod
    def get(*a, **k):
        raise _FakeExceptions.Timeout()


try:
    sys.path.insert(0, HERE)
    import bomb_common as _bc                                    # noqa: E402

    _bc.requests = _FakeRequests
    _bot = _bc.BombMiner(port=7843, host_port=7842)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _r = _bot._post("/tool", {})
        _r2 = _bot._get("/state")
    out = buf.getvalue()
    ck("…`_post` 超时返回空 dict（调用方语义不变）", _r == {})
    ck("…但日志里出现 ⏱️ POST /tool 超时", "⏱️ POST /tool 超时" in out)
    ck("…`_get` 超时也吼（GET /state）", "⏱️ GET /state 超时" in out)
except Exception as e:                                           # noqa: BLE001
    ck("…导入/构造 BombMiner 跑得起来", False, repr(e))

print("")
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("✅ 全部通过（打怪愣住 + 探索打转 的根因都被钉住）")
