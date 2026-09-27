"""🎁 领取菜单的「读」与「回执」—— 纯 Python 自验（不起服务、不碰游戏）。2026-09-27

恒真机看着 AI 在博物馆领东西：「他看起来很困难，我看着 result 也返回了太多的字」。四条：
  ① **ok 不该只在"满包"分支里** —— 任何领取都能 ok 关掉、下次再来；
  ② **写简单点** —— 原来是一段「🐟 背包满接鱼/箱子满（三选一…）」+ ①②③ 三条，其实操作是通用的；
  ③ **退役的工具被当成日志写上去了** —— 文案里赫然写着「🚫claim_swap 替换领取已退役」
     （违反「给 AI 的文案不许写改动史」）；
  ④ 回执只回内部代号 `🖱️ 已点击（claim）` ⇒ AI 不知道自己领到没、领了啥，**原样重发同一条命令**，
     第二次因为东西不在了而回「领取菜单里没有…」，反倒让它以为第一次也失败了
     （实录 session_log 1790485850 那两发，第一发其实**成功**了）。

另外钉一条反编译来的事实：**裸 `menu click item=名`（不带 action）不会领取** ——
C# 的 `wantClaim` 要求 `action=="claim"` 或 `slot>=0`（ModEntry.cs:13918），
所以文案里不能再写「点领取侧物品=拿起」这种暗示「有两种拿法」的话。
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


# 真机那一刻的博物馆领取菜单（照 session_log 的 menu read 抄）
GRAB_MENU = {
    "open": True, "type": "ItemGrabMenu",
    "buttons": [{"name": "okButton"}, {"name": "trashCan"}],
    "slots": [],
    "items": [{"name": "上古种子（配方）", "count": 1},
              {"name": "直立的晶洞", "count": 1},
              {"name": "花椰菜种子", "count": 9}],
}
EMPTY_MENU = {"open": True, "type": "ItemGrabMenu", "buttons": [{"name": "okButton"}],
              "slots": [], "items": []}


class FakeApi:
    def __init__(self, menu): self._menu = menu
    def menu(self, **kw): return self._menu
    def state(self, **kw): return {"player": {}}


_real = {n: getattr(M, n) for n in ("api", "_with_state", "_ensure_background")}
try:
    M._with_state = lambda s: s
    M._ensure_background = lambda: None

    print("\n① 领取说明：短、通用、ok 升成通用项（不在「满包」分支里）")
    M.api = FakeApi(GRAB_MENU)
    out = M.read_menu.__wrapped__()
    ck("还看得见「可领取」清单", "上古种子" in out, out)
    ck("有领取那条（action=claim）", "action=claim" in out, out)
    ck("**ok 是并列的通用项**（提示语写「先不领」而不是「放弃这条」）",
       "先不领" in out and "放弃这条" not in out, out)
    ck("**不再有「三选一 / ①②③」那种分支框架**", "三选一" not in out and "①" not in out, out)
    ck("**不再把退役工具名写进 AI 眼前**（claim_swap）", "claim_swap" not in out, out)
    ck("**不再暗示「点物品=拿起」还有第二种拿法**", "点领取侧物品" not in out, out)
    ck("别啰嗦：整块 ≤ 8 行", out.count("\n") <= 9, str(out.count("\n")))

    print("\n①b 送礼/加料菜单 ≠ 领取菜单（两种都是 ItemGrabMenu，别把领取说明打到送礼菜单上）")
    # 恒 2026-09-27：「送礼/百乐汤的引导被拿到哪里去了，还在吗？」—— 在，但原来 **`elif` 之前**
    # 会把下面那块「领取」说明**一起打出来**，对着送礼菜单喊「可领取 / action=claim」。
    M.api = FakeApi(dict(GRAB_MENU, gift=True))
    out = M.read_menu.__wrapped__()
    ck("老 DLL（没 grabBehavior）→ 说**通用但为真**的话", "触发它自己的行为" in out, out)
    ck("…**不出现**「可领取」", "可领取" not in out, out)
    ck("…**不出现** action=claim（那是领取那条路）", "action=claim" not in out, out)

    print("\n①c 行为函数名 → 给**具体**指引（别把开箱子说成「送礼」）")
    # 恒 2026-09-27：「要不只在这两个节日当天有这个分支？就是不知道有没有漏情况」——
    # 反编译数过 32 处 `new ItemGrabMenu(`：设 behaviorFunction 的**不止那两个节日**，
    # 开箱子/冰箱/出货箱/祝尼魔屋**天天在用** ⇒ 按日期 gate 一定漏。判据是**行为函数叫什么**。
    M.api = FakeApi(dict(GRAB_MENU, gift=True, grabBehavior="chooseSecretSantaGift"))
    out = M.read_menu.__wrapped__()
    ck("冬星节 → 点名「送出冬星节礼物」", "送出冬星节礼物" in out, out)

    M.api = FakeApi(dict(GRAB_MENU, gift=True, grabBehavior="clickToAddItemToLuauSoup"))
    out = M.read_menu.__wrapped__()
    ck("百乐汤 → 点名「加进百乐汤」", "加进百乐汤" in out, out)

    M.api = FakeApi(dict(GRAB_MENU, gift=True, grabBehavior="grabItemFromInventory"))
    out = M.read_menu.__wrapped__()
    ck("**开箱子 → 说取出，不说送出**", "取出" in out and "送出" not in out, out)

    M.api = FakeApi(dict(GRAB_MENU, gift=True, grabBehavior="shipItem"))
    out = M.read_menu.__wrapped__()
    ck("出货箱 → 提醒「投了就卖掉」", "投了就卖掉" in out, out)

    print("\n② 领空了 → 只剩「关掉」这一步（别带着空菜单四处撞墙）")
    M.api = FakeApi(EMPTY_MENU)
    out = M.read_menu.__wrapped__()
    ck("点破「已经拿空」", "拿空" in out, out)
    ck("…并给出关法 button=ok", "button=ok" in out, out)

    print("\n③ 回执要说清**领到了什么**（别再只回内部代号）")
    class ClickApi:
        def __init__(self, r): self._r = r
        def menu_click(self, **kw): return self._r
        # ⚠️ `minigame` 报 "FishingGame" 只是为了让秋收节那个兜底循环**第一轮就 break**
        #    （它不 FishingGame 就会 10×0.3s 空转 3 秒；`_is_fair` 为假 ⇒ 不会真跑钓鱼）
        def state(self, **kw):
            return {"player": {"minigame": "FishingGame"}, "activeMenu": {"type": "ItemGrabMenu"}}
    def click_with(r):
        M.api = ClickApi(r)
        return M.menu_click(action="claim", item="上古种子（配方）")

    out = click_with({"ok": True, "clicked": "claim", "item": "上古种子（配方）", "slot": 0})
    ck("按名领取 → 回「已领取『上古种子（配方）』」", "已领取" in out and "上古种子" in out, out)
    ck("…不再只回代号", "已点击（claim）" not in out, out)

    out = click_with({"ok": True, "clicked": "claim_multi", "count": 4})
    ck("多领 → 回「已领取 4 件」", "4 件" in out and "已领取" in out, out)

    out = click_with({"ok": True, "clicked": "claim_slot", "item": "上古种子（配方）",
                      "slot": 2, "claimed": False})
    ck("**空槽 no-op 要如实说「没领到」**（不能伪报成功）",
       "空的" in out and "没领到" in out, out)

    out = click_with({"ok": True, "clicked": "claim_slot", "item": "直立的晶洞",
                      "slot": 1, "claimed": True})
    ck("真领到 → 照样报名字", "直立的晶洞" in out and "没领到" not in out, out)

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
