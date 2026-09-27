"""💰 商店卖东西：名字口径 + 「这店收哪些」 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-27

恒：「还有 ai 在害怕什么呢，还是会一件一件卖东西！它可能害怕全卖是把所有东西都卖出去，
包括那些种子。——而其实，威利的鱼店只收鱼和浮漂！所以我们可能还是得做游戏里的当前可卖给它看看。」

查下来是三件事叠在一起（都被自验逮到 / 钉死）：
  ① **`sell_all` 有个真 bug**：C# 是 `item.Name.Equals(name)`（**英文内部名** "Smallmouth Bass"），
     而 Python 传的是 `displayName`（中文 "小嘴鲈鱼"）⇒ **永远匹配不上**，只会报一串"没卖动的"
     （`/state` 实测：`name='Driftwood'` / `displayName='浮木'`）；
  ② **名字不能只做精确比对**：`check backpack` 显示的是 `[金]小嘴鲈鱼`（**带品质前缀**），
     AI 天然会写 `小嘴鲈鱼` ⇒ 要能剥前缀/唯一子串对上；
  ③ **C# 一次只卖第一组同名堆**（`break`），而**不同品质是不同槽** ⇒ 同一类要反复卖到钱不再涨；
  ④ **C# 回的 `sold`/`totalPrice` 是"点之前记的堆叠"** —— 商店不收的它**照样报"卖了"**
     ⇒ 只有**钱包**是真话（钱从每次回包自带的 `remainingGold` 拿，不用多拉 /state）；
  ⑤ **文案没把 `sell_all` 的边界说清** ⇒ AI 以为"全卖 = 连种子一起卖"，宁可一类一类点。
     现在 `menu read` 会列出「这店收」哪些（C# 走游戏自己的 `ShopMenu.highlightItemToSell`），
     本函数也**一次调用就顺手量出"这家收不收"**（钱没动 = 不收），回包如实报。

顺带：`name` 支持**逗号分隔多选**（恒：「它可以传名字多选却仍然一个一个点，这会花掉很多次调用」）。
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


# 威利的鱼店：收 鱼(-4)/鱼饵(-21)/渔具(-22)。**不收** 垃圾(-20)/种子(-74)，工具不可卖(-99)。
# ⚠️ 两条鱼是**同内部名、不同品质**（现实里就是两个背包槽，C# 一次只卖得掉其中一组）。
BAG = [
    {"name": "Smallmouth Bass", "displayName": "[金]小嘴鲈鱼", "catNum": -4, "sellable": True, "stack": 9},
    {"name": "Smallmouth Bass", "displayName": "[银]小嘴鲈鱼", "catNum": -4, "sellable": True, "stack": 4},
    {"name": "Catfish", "displayName": "[银]鲶鱼", "catNum": -4, "sellable": True, "stack": 7},
    {"name": "Driftwood", "displayName": "浮木", "catNum": -20, "sellable": True, "stack": 1},
    {"name": "AncientSeeds", "displayName": "上古种子", "catNum": -74, "sellable": True, "stack": 1},
    {"name": "Pickaxe", "displayName": "十字镐", "catNum": -99, "sellable": False, "stack": 1},
]
SHOP_CATS = {-4, -21, -22}
PRICE = {"Smallmouth Bass": 93, "Catfish": 312, "Driftwood": 0, "AncientSeeds": 30}


class FakeApi:
    def __init__(self, give_gold=True):
        self.money = 1000
        self.calls = []
        self.give_gold = give_gold
        self.inv = [dict(x) for x in BAG]

    def state(self, **kw):
        return {"inventory": self.inv, "player": {"money": self.money}}

    def _post(self, ep, data=None, **kw):
        assert ep == "/sell_to_shop", ep
        nm = (data or {}).get("name")
        self.calls.append(nm)
        it = next((i for i in self.inv if i["name"] == nm), None)
        if it is None:
            return {"ok": False, "error": f"Item '{nm}' not found in inventory"}
        bought = it["catNum"] in SHOP_CATS
        if bought:
            self.money += PRICE[nm] * it["stack"]
            self.inv.remove(it)          # ← 一次只卖掉**一组**（C# 里那句 `break`），再调一次才卖下一组
        # ⚠️ 商店**不收**时：点了没反应、钱一分不动，但 C# **照样报 sold** —— 这正是它骗人的地方
        r = {"ok": True,
             "sold": [{"item": nm, "sold": it["stack"],
                       "unitPrice": PRICE[nm] if bought else 0,
                       "totalPrice": (PRICE[nm] * it["stack"]) if bought else 0}]}
        if self.give_gold:
            r["remainingGold"] = self.money
        return r


_real = {n: getattr(M, n) for n in ("api", "_with_state", "_ensure_background", "_peer_econ_mute")}
try:
    M._with_state = lambda s: s
    M._ensure_background = lambda: None
    M._peer_econ_mute = lambda: None

    print("\n① sell_all：名字口径必须是**英文内部名**（旧代码传中文 ⇒ 一件都卖不动）")
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(sell_all=True)
    ck("真机判据：调 C# 时传的是 'Driftwood' 而不是 '浮木'",
       "Driftwood" in M.api.calls and "浮木" not in M.api.calls, str(M.api.calls))
    ck("**同一类的两组品质都卖掉了**（+1209g：金×9 + 银×4）", "小嘴鲈鱼 +1209g" in out, out)
    ck("…鲶鱼也卖了（+2184g）", "鲶鱼 +2184g" in out, out)
    ck("…**如实报「这店不收」**（浮木/上古种子一根没动）",
       "这店不收" in out and "浮木" in out and "上古种子" in out, out)
    ck("…工具压根不进候选（不可卖）", "十字镐" not in out, out)
    ck("…钱包实收对得上（1209+2184）", "+3393g" in out, out)

    print("\n② 名字要认「去掉品质前缀」的写法（AI 看的是 [金]小嘴鲈鱼，写的是 小嘴鲈鱼）")
    ck("…剥前缀能对上（上面 ① 整段都靠它）", True)
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(name="小嘴鲈鱼")
    ck("单一类 → 回「卖出『…』 +1209g」", "卖出「" in out and "+1209g" in out, out)
    ck("…没被当成「背包里没有」", "对不上" not in out, out)

    print("\n③ 逗号分隔多选 → **一次调用**卖完几类（别一类一次）")
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(name="小嘴鲈鱼,鲶鱼")
    ck("两类都卖了", "小嘴鲈鱼" in out and "鲶鱼" in out, out)
    ck("…且每类都卖**干净**（两组品质都走了）",
       M.api.calls.count("Smallmouth Bass") >= 2 and M.api.calls.count("Catfish") >= 1,
       str(M.api.calls))

    print("\n③b 分隔符：半角/全角逗号、分号都认；**空格不算分隔**（英文内部名里就有空格）")
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(name="小嘴鲈鱼，鲶鱼")
    ck("全角逗号 `，` 能拆", "小嘴鲈鱼" in out and "鲶鱼" in out and "对不上" not in out, out)
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(name="小嘴鲈鱼;鲶鱼")
    ck("分号 `;` 能拆", "小嘴鲈鱼" in out and "鲶鱼" in out and "对不上" not in out, out)
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(name="Smallmouth Bass")
    ck("**带空格的英文内部名整体当一个名字**（没被空格拆坏）",
       "对不上" not in out and "卖出「" in out, out)

    print("\n④ 对不上的名字 → 当场说清并给照抄口径（别丢给 C# 回一句 not found）")
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(name="翻车鱼")
    ck("点名对不上", "没在背包里对上" in out and "翻车鱼" in out, out)
    ck("…给出现在能卖的名字", "小嘴鲈鱼" in out, out)

    print("\n⑤ 没给名字 → 报错并给**下一步**（含 sell_all 的边界说明）")
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__()
    ck("列出能卖的", "小嘴鲈鱼" in out and "浮木" in out, out)
    ck("**说清 sell_all 只卖这店收的**（这才是它敢用的关键）", "只卖这店收的" in out, out)
    ck("…并提示逗号分隔多选", "逗号分隔" in out, out)

    print("\n⑥ 读不到钱包 → **不编金额**（「读不到」≠「没卖动」）")
    M.api = FakeApi(give_gold=False)
    out = M.sell_to_shop.__wrapped__(name="小嘴鲈鱼")
    ck("不伪报 +Ng", "+" not in out, out)

    print("\n⑦ 恒真机那条：拿**商店不收**的东西去卖，**绝不能回「卖成了」**")
    # 「现在我又看到破碎的眼镜它试图出售，而且返回结果还告诉它它卖成了（实则是不收）」
    M.api = FakeApi()
    out = M.sell_to_shop.__wrapped__(name="浮木")       # 垃圾类，威利不收
    ck("抬头明说**一件都没卖出去**", "一件都没卖出去" in out, out)
    ck("…不出现「卖出「浮木」」这种像成功的话", "💰 卖出" not in out, out)
    ck("…并点名是**这店不收**", "这店不收" in out and "浮木" in out, out)

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
