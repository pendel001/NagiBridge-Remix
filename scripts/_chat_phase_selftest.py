"""🔔 等睡/结算期的「恒刚发消息了」催回话 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-27

恒：「我发现有时候我发送给ai的信息会被静默吞掉……不知为何没有推送」——查链路时逮到这条：
C# 记账写的键是 **`content`**（`AddRecentEvent`，ModEntry.cs:1479），而 `_chat_phase_line`
读的是 **`text`/`message`**（两个键**在整份 recent_events 里从来不存在**）⇒ `_t` 恒为空串。

后果两条（都不是"消息本身丢了"，而是**该催的不催 / 该闭嘴的乱催**）：
  ① 水位线 `msg_sig` 恒为 `"chat|"`，第一次之后再也不会变 ⇒ 等睡/结算期**只有第一条消息**
     能触发「🔔 恒 刚发消息了——先回一句」，之后他再说什么都得干等到 30s 轮询。
  ② 「排掉自己回声」那道防御（代码注释里明写着怕它命中）**从来没生效过** —— 自然同步偶尔把
     自家 `💬 轮回: …` 漏进来时，会对着自己的话催"快回他"。

这是本仓库的老病形状：**给消费方写了分支 ≠ 消费方拿得到数据**（键名对不上 = 静默空转）。
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def data(events, me="轮回"):
    return {"player": {"name": me, "isInBed": False}, "raw": {"recent_events": events}}


def ev(t, content):
    """C# 真发的形状（键名必须是 content —— 这正是被测的那一点）。"""
    return {"type": t, "content": content, "tick": 1}


def prep():
    """只把**时钟**归零，免得 poll(30s)/timeout 分支插进来；`msg_sig` **保留** —— 那正是被测的水位线。"""
    M._CHAT_PHASE["poll_ts"] = time.time()
    M._CHAT_PHASE["last_msg_ts"] = time.time()
    M._CHAT_PHASE["timeout_n"] = 0
    M._CHAT_PHASE["just_entered"] = False


def call(events, me="轮回"):
    return M._chat_phase_line(data(events, me), "ShippingMenu", 1200, "Farm")


_real = {n: getattr(M, n) for n in ("_host_name",)}
try:
    M._host_name = lambda: "恒"

    print("\n① 水位线真的会动：第二条（不同的）消息也要催")
    M._CHAT_PHASE = {"phase": "settlement", "poll_ts": 0.0, "last_msg_ts": time.time(),
                     "timeout_n": 0, "entered": time.time(), "just_entered": False, "msg_sig": None}
    prep()
    out1 = call([ev("chat", "💬 恒: 在吗")])
    ck("第一条消息 → 催回话", "刚发消息了" in out1, out1)

    prep()
    out2 = call([ev("chat", "💬 恒: 在吗")])          # 同一条，重读
    ck("同一条重复读到 → **不**再催（去重还在）", out2 == "", out2)

    prep()
    out3 = call([ev("chat", "💬 恒: 帮我浇个水")])     # ← 修之前这里恒为 ""
    ck("**换了一条新的 → 照样催**（reader 读得到 content 才可能发生）", "刚发消息了" in out3, out3)

    print("\n② 「排掉自己回声」那道防御现在真生效")
    # ⚠️ 水位线**先设成别的值**，否则旧代码也能"蒙对"（`chat|` == 上一次的 `chat|` ⇒ 本来就不催）——
    #    这样设了之后，只有"真的把自家那条跳过去了"才能过。
    M._CHAT_PHASE["msg_sig"] = "chat|💬 恒: 上一句"
    prep()
    out4 = call([ev("chat", "💬 轮回: 我马上来")])     # 自家的话漏进来
    ck("只有自己那条 → **一个字都不催**（别对着自己的话喊'快回他'）", out4 == "", out4)

    print("\n③ 自己 + 恒混在一条账本里 → 取**最新**那条（reversed）")
    M._CHAT_PHASE["msg_sig"] = None
    prep()
    out5 = call([ev("chat", "💬 轮回: 我先去钓鱼"), ev("chat", "💬 恒: 等等我")])
    ck("最新的是恒的 → 催", "刚发消息了" in out5, out5)

    print("\n④ 键名口径钉住：C# 发的就是 `content`")
    M._CHAT_PHASE["msg_sig"] = None
    prep()
    out6 = call([{"type": "chat", "content": "💬 恒: 睡了吗", "tick": 1}])
    ck("`content` 键能读出内容（读不到就说明又被改了名）",
       M._CHAT_PHASE.get("msg_sig") == "chat|💬 恒: 睡了吗", str(M._CHAT_PHASE.get("msg_sig")))
    ck("…且确实催了", "刚发消息了" in out6, out6)

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
