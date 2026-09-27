"""🗑️🌾🎒 三条顺手提示 + 💬 发言带名字 + 🍽️ 吃完还原手持 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-25

一次验五件（都是恒当天从真机提的）：

  ① 🗑️ **垃圾桶**：「AI 看起来并不知道有垃圾桶工具」→ 每天第一次走到桶 5 格内提一句
  ② 🌾 **稻苗**：「AI 把稻苗种在地上了」→ 包里带着 + 手上正拿农具时提"种水边不用浇水"
  ③ 🎒 **满包**：剩 1~2 格时教一次怎么清（弃垃圾 / 存箱子）
  ④ 💬 **发言带名字**：恒发的是「小恒：…」，AI 发的却是裸文本 → 补上 AI 角色名前缀
  ⑤ 🍽️ **吃完还原手持**：`/eat` 吃的是手持那一格，吃完那格空了 → 钓鱼脚本再也抛不出去

三条提示共同的规矩都要验到：**每天最多一次**、**内层(_OPS_INNER)闭嘴且不消费**、
**脚本在跑时不提**（那时候叫 AI 走位是自相矛盾的引导）。
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def fresh_hint(key):
    """把"每天一次"的闸门复位（每个用例都从'今天还没提过'开始）。"""
    key.update(day=None, shown=False)


_real = {n: getattr(M, n) for n in
         ("api", "_with_state", "_ensure_background", "_OPS_INNER", "_bg_any_running",
          "_session_append")}

try:
    M._with_state = lambda s: s
    M._ensure_background = lambda: None

    # ── ① 🗑️ 垃圾桶 ───────────────────────────────────────────────
    print("\n① 🗑️ 垃圾桶：走到桶 5 格内提一句（每天一次）")

    class TrashApi:
        def __init__(self, cans=((52, 63),)):
            self.cans = cans

        def _get(self, ep, *a, **k):
            if ep == "/scan":
                return {"ok": True, "location": "Town",
                        "actions": [{"x": x, "y": y, "action": "Garbage 0"} for x, y in self.cans]
                        + [{"x": 1, "y": 1, "action": "Warp 5 5"}]}
            return {}

    M.api = TrashApi()
    M._bg_any_running = lambda: False
    M._OPS_INNER["n"] = 0

    def trash(px, py, loc="Town", day="spring|4|1"):
        M._TRASH_CANS.update(key=None, cans=[])
        return M._trash_hint(loc, px, py, daykey=day)

    fresh_hint(M._TRASH_HINT_KEY)
    out = trash(50, 63)                                   # 曼哈顿 2 格
    ck("桶在 5 格内 → 出提示", "垃圾桶" in out, out)
    ck("…报了桶的坐标", "(52,63)" in out, out)
    ck("…给**能直接调用的下一步**（op + 参数名都在）",
       'scene(ops="garbage"' in out and '"loc"' in out, out)
    ck("…第二次经过同一个桶 → 不再提",
       trash(50, 63) == "", trash(50, 63))
    ck("…换一天 → 又可以提一次", "垃圾桶" in trash(50, 63, day="spring|5|1"))
    fresh_hint(M._TRASH_HINT_KEY)
    ck("…离得远（>5 格）→ 不提", trash(20, 90) == "")
    fresh_hint(M._TRASH_HINT_KEY)
    M.api = TrashApi(cans=())
    ck("…本图没有桶 → 不提", trash(50, 63) == "", trash(50, 63))
    M.api = TrashApi()

    print("\n①b 三条提示共同的规矩：内层闭嘴 / 脚本跑着不提（且**不消费**这次机会）")
    fresh_hint(M._TRASH_HINT_KEY)
    M._OPS_INNER["n"] = 1
    ck("域 op 内层 → 闭嘴", trash(50, 63) == "")
    ck("…而且没消费掉（外层还有机会提）", M._TRASH_HINT_KEY["shown"] is False)
    M._OPS_INNER["n"] = 0
    M._bg_any_running = lambda: True
    ck("后台脚本在跑 → 不提（叫 AI 走位是自相矛盾的引导）", trash(50, 63) == "")
    ck("…同样没消费掉", M._TRASH_HINT_KEY["shown"] is False)
    M._bg_any_running = lambda: False
    ck("…脚本停了 → 照常提", "垃圾桶" in trash(50, 63))

    # ── ② 🌾 稻苗 ────────────────────────────────────────────────
    print("\n② 🌾 稻苗：包里带着 + 手上正拿农具 → 提「种水边不用浇水」（每天一次）")
    fresh_hint(M._WATER_PLANT_KEY)
    INV = [{"name": "Mixed Seeds", "count": 3}, {"name": "Rice Shoot", "count": 12}]
    out = M._water_plant_hint(INV, "Hoe", daykey="spring|4|1")
    ck("拿锄 + 有稻苗 → 出提示", "稻苗" in out, out)
    ck("…说清**种水边 3 格内不用浇水**", "水" in out and "不用" in out, out)
    ck("…带数量", "12" in out, out)
    # ⚠️ 真机逮到的：light 模式的背包叠数键叫 `stack`（不是 `count`）——
    #    原来只读 `count` ⇒ 永远 None ⇒ 兜底成 1，包里 6 个稻苗写成「×1」。
    #    这两条**独立开一段**（各给一个临时 daykey，跑完把闸门还回去），
    #    别把上面那串"每天一次"的时序搅乱。
    _keep_wp = dict(M._WATER_PLANT_KEY)
    _l7 = M._water_plant_hint([{"name": "Rice Shoot", "stack": 7}], "Hoe", daykey="__t1")
    ck("…light 模式（键名是 stack）也要报对数", "×7" in _l7, _l7)
    _l3 = M._water_plant_hint([{"name": "Rice Shoot", "count": 3}], "Hoe", daykey="__t2")
    ck("…非 light（键名是 count）照旧认", "×3" in _l3, _l3)
    M._WATER_PLANT_KEY.clear()
    M._WATER_PLANT_KEY.update(_keep_wp)
    ck("…给能直接调的 op + seed_name", 'farm(ops="plant"' in out and "Rice Shoot" in out, out)
    ck("…第二次要求 → 不再提", M._water_plant_hint(INV, "Hoe", daykey="spring|4|1") == "")
    ck("…换一天 → 又可以提", "稻苗" in M._water_plant_hint(INV, "Hoe", daykey="spring|5|1"))
    fresh_hint(M._WATER_PLANT_KEY)
    ck("…手上没拿农具（拿着斧头）→ 不提", M._water_plant_hint(INV, "Axe", daykey="spring|4|1") == "")
    ck("…空手 → 不提", M._water_plant_hint(INV, "", daykey="spring|4|1") == "")
    ck("…包里没有稻苗 → 不提",
       M._water_plant_hint([{"name": "Mixed Seeds", "count": 3}], "Hoe", daykey="spring|4|1") == "")
    ck("…中文名「稻苗」也认",
       "稻苗" in M._water_plant_hint([{"name": "稻苗", "count": 2}], "Watering Can", daykey="spring|4|1"))
    fresh_hint(M._WATER_PLANT_KEY)
    M._OPS_INNER["n"] = 1
    ck("…域 op 内层闭嘴", M._water_plant_hint(INV, "Hoe", daykey="spring|4|1") == "")
    M._OPS_INNER["n"] = 0

    # ── ③ 🎒 满包 ────────────────────────────────────────────────
    print("\n③ 🎒 满包：剩 1~2 格时教一次怎么清（每天一次）")
    fresh_hint(M._BAGFULL_KEY)
    out = M._bagfull_hint(2, daykey="spring|4|1")
    ck("剩 2 格 → 出提示", "满" in out, out)
    ck("…给丢弃的路子（scene drop，含参数名）", 'scene(ops="drop"' in out, out)
    ck("…给存箱子的路子（storage store all）",
       'storage(ops="store"' in out and '"all"' in out, out)
    ck("…第二次 → 不再提", M._bagfull_hint(2, daykey="spring|4|1") == "")
    ck("…换一天 → 又可以提", "满" in M._bagfull_hint(1, daykey="spring|5|1"))
    fresh_hint(M._BAGFULL_KEY)
    ck("…剩 3 格（还没那么急）→ 不提", M._bagfull_hint(3, daykey="spring|4|1") == "")
    ck("…读不到格数（None）→ 不提", M._bagfull_hint(None, daykey="spring|4|1") == "")
    fresh_hint(M._BAGFULL_KEY)
    M._OPS_INNER["n"] = 1
    ck("…域 op 内层闭嘴", M._bagfull_hint(1, daykey="spring|4|1") == "")
    M._OPS_INNER["n"] = 0

    # ── ④ 💬 发言带名字 ──────────────────────────────────────────
    print("\n④ 💬 AI 发言要带自己的角色名（恒那条：「我发是小恒：…，他发是裸的」）")

    class ChatApi:
        def __init__(self, name="Claude", boom=False):
            self.name, self.boom, self.sent = name, boom, []

        def state(self):
            if self.boom:
                raise RuntimeError("读不到")
            return {"player": {"name": self.name}}

        def host_chat(self, msg, color=""):
            self.sent.append(msg)

    M._session_append = lambda *a, **k: None
    M.api = ChatApi()
    out = M.send_chat(message="我钓到鱼了")
    ck("发出去的那行**带名字前缀**", M.api.sent == ["Claude：我钓到鱼了"], str(M.api.sent))
    ck("…回执里也看得见（AI 知道自己发出去长什么样）", "Claude：我钓到鱼了" in out, out)
    M.api = ChatApi()
    M.send_chat(message="Claude：我自己写了名字")
    ck("…自己已经写了名字 → 不加第二遍", M.api.sent == ["Claude：我自己写了名字"], str(M.api.sent))
    M.api = ChatApi(boom=True)
    M.send_chat(message="你好")
    ck("…读不到名字 → 照旧裸发（不编一个名字出来）", M.api.sent == ["你好"], str(M.api.sent))

    # ── ⑤ 🍽️ 吃完还原手持 ───────────────────────────────────────
    print("\n⑤ 🍽️ 吃东西会清空手持那一格 → 吃完要还原（不然钓鱼脚本再也抛不出去）")

    class EatApi:
        def __init__(self, held="Bamboo Pole", boom=False):
            self.held, self.sel, self.boom = held, [], boom

        def state(self):
            if self.boom:
                raise RuntimeError("读不到")
            return {"player": {"currentItem": self.held, "currentTool": self.held}}

        # ⚠️ 2026-09-27 修腐烂：真代码 2026-09-25 起带 `quality`（恒：「先吃最高星级的」，
        #    `api.select(name, _q)`）⇒ 这个假 api 只收 1 个参数 ⇒ 每次调用都 TypeError、
        #    被 `eat_item` 的兜底吞成一句错误话术，**整段 ④ 全红但看起来像产品坏了**。
        #    （和 `_reply_hint_selftest` 同一种病：产品按恒的要求改了，测试没跟上。）
        def select(self, name, quality=None):
            self.sel.append(name)
            return {"ok": True}

        def _post(self, ep, data=None):
            return {"ok": True, "ate": "Field Snack"}

    M.api = EatApi()
    out = M.eat_item(name="工作小食")
    ck("先选中食物、吃完**再指回原来的竿**",
       M.api.sel == ["工作小食", "Bamboo Pole"], str(M.api.sel))
    ck("…回执里点名还原了（AI 要知道手持被动过）", "还原" in out and "Bamboo Pole" in out, out)
    M.api = EatApi(held="Field Snack")
    M.eat_item(name="Field Snack")
    ck("…本来拿的就是这个食物（没被清空）→ 不做多余的还原",
       M.api.sel == ["Field Snack"], str(M.api.sel))
    M.api = EatApi(boom=True)
    M.eat_item(name="工作小食")
    ck("…读不到原来拿的 → 只选食物、不瞎还原", M.api.sel == ["工作小食"], str(M.api.sel))

    print("\n" + ("=" * 46))
    print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
    # ── ⑦ 🏛️ 进社区中心报"现有哪几块献祭板"（问游戏自己，见 _cc_notes_line）──────────
    print(chr(10) + "⑦ 🏛️ 进 CC 报现有的献祭板（板子不是瓦片/家具，只能问游戏）")

    class CCApi:
        def __init__(self, areas):
            self.areas = areas

        def _get(self, ep, *a, **k):
            return {"ok": True, "areas": self.areas} if ep == "/progress" else {}

    _keep_cc = M.api
    M._OPS_INNER["n"] = 0
    M.api = CCApi([{"n": 0, "name": "茶水间", "noteHere": False, "notePos": {}},
                   {"n": 1, "name": "工艺室", "noteHere": True,
                    "notePos": {"type": "Location", "X": "14", "Y": "23"}},
                   {"n": 2, "name": "鱼缸", "noteHere": False, "notePos": {}}])
    M._STATE_DELTA.pop("ccnotes", None)
    _l = M._cc_notes_line("CommunityCenter")
    ck("只报**真有**的那块（不报其余五个）", "工艺室" in _l and "茶水间" not in _l, _l)
    ck("…带 POI 坐标", "(14,23)" in _l, _l)
    # ⚠️ 2026-09-27 修腐烂：原来还要求提示里有 `menu(ops="cancel")`（怎么关界面）——
    #    那句**2026-09-25 恒自己让砍的**（「你的提示也好长」⇒ `_cc_notes_line` 只留
    #    「叫什么 + 在哪 + 敲哪条打开」，"为什么/怎么关"全搬进该函数的注释里）。
    #    改成锁**当前**契约：给出可照抄的调用 + 说清它干什么 + **就一行**（别再涨回去）。
    ck("…给可直接敲的摸法（只留「敲哪条打开」，恒 2026-09-25）",
       'scene(ops="at"' in _l and "交互打开" in _l and "\n" not in _l, _l)
    ck("…同一批不重播", M._cc_notes_line("CommunityCenter") == "")
    ck("…不在 CC 这张图就不问", M._cc_notes_line("Town") == "")
    M._STATE_DELTA.pop("ccnotes", None)
    M.api = CCApi([{"n": 0, "name": "茶水间", "noteHere": False, "notePos": {}}])
    ck("…一块都没有 → 不报", M._cc_notes_line("CommunityCenter") == "")
    M._STATE_DELTA.pop("ccnotes", None)
    M.api = CCApi([{"n": 1, "name": "工艺室", "noteHere": True, "notePos": {}}])
    _l2 = M._cc_notes_line("CommunityCenter")
    ck("…老 DLL 没坐标时降级：仍报区名 + 给通用摸法",
       "工艺室" in _l2 and 'scene(ops="interact")' in _l2, _l2)
    M._OPS_INNER["n"] = 1
    M._STATE_DELTA.pop("ccnotes", None)
    ck("…域 op 内层闭嘴", M._cc_notes_line("CommunityCenter") == "")
    M._OPS_INNER["n"] = 0
    M.api = _keep_cc

finally:
    for k, v in _real.items():
        setattr(M, k, v)

sys.exit(1 if FAIL else 0)
