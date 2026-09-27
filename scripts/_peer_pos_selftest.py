"""🧑🤝🧑 同图时报另一位玩家的坐标 —— 纯 Python 自验（不起服务、不碰游戏）。2026-09-24

恒：「我还希望**玩家在同图时报玩家坐标**。」

三件必须成立（否则不如不做）：
  ① **只在同图报** —— 不同图时那两个数字对 AI 没意义（走不过去也没法互动）
  ② **别每个工具调用都去敲对面**（跨进程读 host 的 /state）⇒ TTL 缓存
  ③ **读不到就闭嘴**，而且**不许吐旧坐标**（旧数据比没有更坏：AI 会照一个过时的位置行动）
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


class FakeApi:
    def __init__(self):
        self.calls = 0
        self.host = {"location": {"name": "Town"}, "player": {"name": "恒", "x": 7, "y": 93}}

    def host_state(self, **kw):
        self.calls += 1
        if self.host is None:
            raise RuntimeError("host 没醒")
        return self.host


_real = {n: getattr(M, n) for n in ("api", "_PEER_POS")}
try:
    M._PEER_POS = dict(M._PEER_POS)
    M.api = FakeApi()

    print("\n① 同图 → 报；不同图 → 不报")
    M._PEER_POS.update({"ts": 0.0})
    ck("同图 Town → ` | 🧑 恒 (7,93)`", M._peer_pos_seg("Town") == " | 🧑 恒 (7,93)",
       M._peer_pos_seg("Town"))
    M._PEER_POS.update({"ts": 0.0})          # 让缓存过期，重新读一次
    ck("不同图（我在 Farm）→ 空串", M._peer_pos_seg("Farm") == "", M._peer_pos_seg("Farm"))

    print("\n② TTL 缓存：别每个工具调用都敲对面一次")
    M._PEER_POS.update({"ts": 0.0})
    M.api = FakeApi()
    M._peer_pos_seg("Town")
    n1 = M.api.calls
    for _ in range(5):                        # 连续拼 5 次状态条
        M._peer_pos_seg("Town")
    ck("5 次连拼不会敲 5 次（走缓存）", M.api.calls == n1, f"{M.api.calls} vs {n1}")

    print("\n③ 读不到 → 闭嘴，**且不吐旧坐标**")
    M._PEER_POS.update({"ts": 0.0})
    M._peer_pos_seg("Town")                   # 先缓存到一个真坐标
    ck("先有一次有效缓存", M._PEER_POS["x"] == 7, str(M._PEER_POS))
    M.api = FakeApi()
    M.api.host = None                         # 对面没醒
    M._PEER_POS["ts"] = 0.0                   # 强制重读
    got = M._peer_pos_seg("Town")
    ck("读不到 → 空串（不编、不刷屏）", got == "", got)
    ck("…而且**旧坐标被清掉**（拿过时位置行动比没有更坏）", M._PEER_POS["x"] is None, str(M._PEER_POS))

    print("\n④ 名字读不到 → 用「他」，别显示 None")
    M.api = FakeApi()
    M.api.host = {"location": {"name": "Town"}, "player": {"x": 7, "y": 93}}
    M._PEER_POS.update({"ts": 0.0})
    seg = M._peer_pos_seg("Town")
    ck("无名 → 「他」兜底，且坐标照给", seg == " | 🧑 他 (7,93)", seg)

    print("\n⑤ 心跳/主动心跳的句尾也用同一份判据（恒：心跳和主动心跳也报一个吧）")
    M.api = FakeApi()
    M._PEER_POS.update({"ts": 0.0})
    ck("同图 → `（7,93）`", M._peer_pos_suffix("Town") == "（7,93）", M._peer_pos_suffix("Town"))
    M._PEER_POS.update({"ts": 0.0})
    ck("不同图 → 空串", M._peer_pos_suffix("Farm") == "", M._peer_pos_suffix("Farm"))

    _real_body = M._heartbeat_body
    try:
        M._heartbeat_body = lambda d: "💭 **恒** 似乎在发呆"
        M._PEER_POS.update({"ts": 0.0})
        out = M._heartbeat_line({"location": {"name": "Town"}, "player": {}})
        ck("心跳行 = 原句 + 坐标（措辞没被动过）",
           out == "💭 **恒** 似乎在发呆（7,93）", out)
        M._PEER_POS.update({"ts": 0.0})
        out2 = M._heartbeat_line({"location": {"name": "Farm"}, "player": {}})
        ck("不同图的心跳行**不加**坐标", out2 == "💭 **恒** 似乎在发呆", out2)
    finally:
        M._heartbeat_body = _real_body

    print("\n⑥ `_gather_user_state` 顺手喂缓存 ⇒ 心跳那次**不再多读一遍**对面")
    M.api = FakeApi()
    M._PEER_POS.update({"ts": 0.0})
    M._gather_user_state()
    ck("读完 host 状态后缓存里就有了坐标", M._PEER_POS["x"] == 7 and M._PEER_POS["loc"] == "Town",
       str(M._PEER_POS))
    n = M.api.calls
    M._peer_pos_suffix("Town")
    ck("紧接着取坐标**没有再敲一次** host（同一份数据两处用）", M.api.calls == n, f"{M.api.calls} vs {n}")

    print("\n" + ("=" * 46))
    print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
finally:
    for k, v in _real.items():
        setattr(M, k, v)
sys.exit(1 if FAIL else 0)
