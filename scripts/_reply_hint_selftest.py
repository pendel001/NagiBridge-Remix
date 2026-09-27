"""🗣️ 「恒发消息 → 跟着给一次怎么回」 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒：「AI 看到信息**习惯在前端回我**。你可以在 user 发送的信息注入后面附一次（可用 message 回复信息）。」

⚠️ 恒记的那个名字要**当场纠正**：`message` 是**参数名**，op 是 `social send`
   （→ `send_chat` → `host_chat` 打 7842，恒窗口必见）。名字给错 = AI 照着调不通，
   所以这里既验"附了这行"，也验"这行里的调用**真能通**"。

测五件：
  ① 恒发的 chat 后面**紧跟一行**回复指引（含完整调用）
  ② 一批里多条 chat → **只附一次**（别刷屏）
  ③ 自己发的那条（`💬 我: …`）→ **不附**（防御：自然同步偶尔把自家话漏进来）
  ④ 不是 chat 的事件（拾取/邮件/表情/穿脱）→ **不附**（对着一颗萝卜说"回他"很蠢）
  ⑤ 这行里的调用**真能走通**到 send_chat（工具名别写错）
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


def mkdata(events, me="轮回", loc="Town"):
    return {
        "raw": {"recent_events": events},
        "inventory": [{"name": "Axe"}],
        "player": {"name": me, "maxItems": 12},
        "location": {"name": loc},
    }


_real = {n: getattr(M, n) for n in ("api", "_session_append", "_with_state")}

# ⚠️ 2026-09-27 修腐烂：这里原来把标记**写死成 `↳ 回他`**，而 2026-09-25 恒两条决定把它改掉了
#    （①「你的提示也好长」⇒ 砍掉"为什么"那半句、理由搬进代码注释；②「host 不一定是男的」
#     ⇒ 改成中性词『回复』）。**改完测试没跟上** ⇒ 那几条断言变成**恒真**（串压根不存在了，
#    `not in out` 永远成立）= 假绿，测不出任何东西。
#    ⇒ 标记**改成读模块自己那行**：措辞再改测试自动跟上（"附了一次"才是契约，措辞不是）。
MARK = M._REPLY_HINT.strip()
try:
    ck("⚠️ _REPLY_HINT 非空（防「标记为空 ⇒ 全绿」）", bool(MARK), repr(MARK))
    M._session_append = lambda *a, **k: None          # 别碰会话文件
    M._with_state = lambda s: s

    print("\n① 恒发的消息后面跟着「怎么回」")
    out = M._news_block(mkdata([{"type": "chat", "content": "💬 恒: 睡了吗？"}]))
    ck("chat 行还在（原样显示）", "💬 恒: 睡了吗？" in out, out)
    ck("…后面紧跟回复指引", M._REPLY_HINT.strip() in out, out)
    ck("…指引里是**完整调用**（不是光写个 'message'）",
       'social(ops="send"' in out and '"message"' in out, out)
    # ⚠️ 原来这里断言「说清为什么（回前端他要切窗口）」—— 那句**2026-09-25 恒自己让砍的**
    #    （「你的提示也好长」⇒ 理由搬进 `_REPLY_HINT` 上面的代码注释）。改成锁**同批的另一条决定**：
    #    用中性词『回复』而不是『回他』（「host 不一定是男的」）—— 这条是**当前**要求，值得钉住。
    ck("…用中性词『回复』（host 不一定是男的，恒 2026-09-25）",
       "↳ 回复" in out and "回他" not in out, out)

    print("\n② 一批里多条 chat → 只附一次")
    out = M._news_block(mkdata([{"type": "chat", "content": "💬 恒: 在吗"},
                                {"type": "chat", "content": "💬 恒: 帮我浇水"},
                                {"type": "chat", "content": "💬 恒: 谢啦"}]))
    ck("三条消息只出现**一行**指引（不刷屏）", out.count(MARK) == 1, out)

    print("\n③ 自己那条不附（C# 已滤，这里再兜一层）")
    out = M._news_block(mkdata([{"type": "chat", "content": "💬 轮回: 我马上来"}]))
    ck("`💬 轮回: …`（我的名字）→ 不附", MARK not in out, out)

    print("\n③b 系统提示**不是**玩家发言（真机第一批踩的：对着「玩家上线」喊回他）")
    # C# 里"以下玩家在线 / 谁进来了"这类系统提示，事件类型**也是 `chat`** ⇒ 只看 type 会误报
    out = M._news_block(mkdata([{"type": "chat", "content": "📢 以下玩家在线：\n📢  - 恒 (127.0.0.1)"}]))
    ck("「以下玩家在线」→ **不附**（那是系统提示，不是他说的话）", MARK not in out, out)

    print("\n④ 不是聊天的事件不附")
    out = M._news_block(mkdata([{"type": "pickup", "content": "🥕 防风草 x2"},
                                {"type": "emote", "content": "💬 恒 发了爱心"},
                                {"type": "mail", "content": "📬 收到信"}]))
    ck("拾取/表情/邮件 → 一个字都不加", MARK not in out, out)

    print("\n⑤ 指引里那行调用**真能通**（工具名别写错：message 是参数不是工具）")
    class FakeApi:
        def __init__(self): self.sent = []
        def host_chat(self, msg, color=""): self.sent.append(msg)

    M.api = FakeApi()
    out = M.social.__wrapped__(ops="send", kw={"message": "在的，马上过去"})
    ck("social(ops=\"send\", kw={\"message\": …}) → 真发出去（host_chat 打 7842）",
       M.api.sent == ["在的，马上过去"], out)
    ck("…回执确认已发送", "已发送" in out, out)

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
