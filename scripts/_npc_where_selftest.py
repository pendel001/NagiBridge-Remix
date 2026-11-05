# -*- coding: utf-8 -*-
"""🔍 `map npc` 的输出钉子 —— **一行一个人，报位置 + 今天聊没聊/送没送礼，别的一概不印**。2026-11-05

来历（恒）：
  「也就是说 map npc 是用来找 npc 的对吧，那**什么都不用说，最多说今天聊过天没有，送过礼没有，
    报个位置就完了**，不需要把台词数据、解释全都翻出来一大串给 ai 看，比较占 token。」

🩸 病根（这条钉子就是防它回来）：
  原来一行里塞了：位置 + `💬「他此刻的台词…」` + 睡姿 + 末尾一大段「⚠️ 某某这端另记着 某某@图(21,4)
  —— 已忽略（它那边远处的地图会滞留旧位置）」。AI 要的只是"人在哪、今天说过话没"，
  台词和那套两端差异解释全是 token，而且**曾把"头顶台词"误读成"我刚跟他聊过"**。

钉五条：
  ① 一行一个人：`名 @ 图 (x,y) ｜ 今天聊过/没聊 ｜ 今天送过礼/没送礼`
  ② **不许**出现台词（`💬`/`🗨️`/`dialogue`）
  ③ **不许**出现"两端差异"那套解释，也不许写死角色名
  ④ 今天聊过没有 = 游戏自己的 `TalkedToToday`（C# 那边新加的位）；**老 DLL 没这一位 ⇒ 自动少一句**，不编
  ⑤ 送礼同理走 `giftsToday`；`还没认识` 单独一个短语，别伪装成"没聊过"

跑法：`python scripts/_npc_where_selftest.py`（退出码 0 = 全绿）
"""
import io
import os
import re
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

import nagi_mcp_server as M  # noqa: E402

FAIL = []
LEGACY = "轮" + "回"


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


def _stub(npc_name="Robin", disp="罗宾", talked=True, gifts=0, known=True,
          dialogue="你遇见我的儿子塞巴斯蒂安了吗？他就住在楼下。", src="host"):
    M.api.state = lambda *a, **k: {
        "location": {"name": "Forest"}, "player": {"name": "Claude", "x": 110, "y": 82},
        "time": {}, "inventory": [], "activeMenu": {}}
    M.api.find_npc = lambda q: {"ok": True, "src": src, "npcs": [{
        "name": npc_name, "displayName": disp, "location": "ScienceHouse",
        "x": 8, "y": 18, "dialogue": dialogue, "isSleeping": False}], "stale": []}
    M.api._get = lambda ep, params=None: (
        {"ok": True, "known": known, "points": 120, "hearts": 0,
         "giftsThisWeek": 2, "giftsToday": gifts, "talkedToToday": talked}
        if str(ep).startswith("/friendship") else {"ok": True})


def main():
    _st, _fn, _get = M.api.state, M.api.find_npc, M.api._get

    print("\n① 一行一个人 + 位置")
    _stub(talked=True, gifts=0)
    out = M.find_npc("Robin")
    print("     实得:", repr(out.split("╌")[0].strip()[:160]))
    ck("有名字", "罗宾" in out)
    ck("有图 + 坐标", "ScienceHouse" in out and "(8,18)" in out)
    ck("说今天聊过", "今天聊过" in out)
    ck("说今天没送礼", "今天没送礼" in out)

    print("\n② 台词 / 解释 一概不许出现")
    ck("没有台词正文", "塞巴斯蒂安" not in out)
    ck("没有 💬/🗨️", "💬" not in out and "🗨️" not in out)
    ck("没有再提「这端/滞留/已忽略」", "这端" not in out and "滞留" not in out and "已忽略" not in out)
    ck("没有写死的角色名", LEGACY not in out)
    ck("没有「查位置而已」那种解释", "查位置而已" not in out)

    print("\n③ 长度约束（恒：「比较占 token」）")
    _body = out.split("╌")[0]
    # ⚠️ 别把 `_with_state` 那条「🌿 已就绪」首调横幅算进来 —— 那是每次会话第一发才有的，
    #    不属于 `map npc` 的输出长度。
    _lines = [l for l in _body.strip().splitlines()
              if l.strip() and not l.strip().startswith("🌿")]
    ck(f"正文 ≤ 2 行（实际 {len(_lines)} 行）", len(_lines) <= 2, str(_lines))
    _npc_line = next((l for l in _lines if "ScienceHouse" in l), "")
    ck(f"NPC 那一行 ≤ 60 字符（实际 {len(_npc_line)}）", 0 < len(_npc_line) <= 60, _npc_line)

    print("\n④ 已聊过 + 已送礼")
    _stub(talked=False, gifts=1)
    out2 = M.find_npc("Robin")
    ck("说今天没聊", "今天没聊" in out2)
    ck("说今天送过礼", "今天送过礼" in out2)

    print("\n⑤ 老 DLL 没有 talkedToToday ⇒ 少一句，不编")
    M.api._get = lambda ep, params=None: (
        {"ok": True, "known": True, "points": 120, "hearts": 0,
         "giftsThisWeek": 2, "giftsToday": 0}          # 没有 talkedToToday 这一位
        if str(ep).startswith("/friendship") else {"ok": True})
    out3 = M.find_npc("Robin")
    ck("不出现「今天聊过/没聊」", "今天聊" not in out3, out3[:160])
    ck("…但送礼那半照旧说", "今天没送礼" in out3)

    print("\n⑥ 还没认识 / 宿主那端没答上来")
    _stub(known=False)
    ck("说「还没认识」", "还没认识" in M.find_npc("Robin"))
    _stub(src="self")
    ck("位置只有自己那份记录 ⇒ 只加「⚠️ 旧」三个字", "⚠️ 旧" in M.find_npc("Robin"))

    print("\n⑧ 「绕过去」那半：站格顺序 + 开口门槛 + 朝向")
    # 恒 2026-11-05：「之前也能穿过两三个室内门的长距离找人」⇒ **距离从来不是门槛**，
    #   柜台那次是"落脚点本身走不进"。恒原话想要 8 格，但那是按"4 格限制了接近范围"推的；
    #   真相是 `/interact` 只打**面前那一格**（`ModEntry.cs:3791`）⇒ 能不能聊 = **贴没贴到身边**，
    #   跟 4/8/24 都无关。所以门槛按"身边（≤1）"写，距离只进文案。
    _o = M._npc_stand_order(6, 24, 8, 18)          # 玩家 (6,24)，NPC (8,18)（真机那一局）
    _ds = [abs(8 + d[0] - 6) + abs(18 + d[1] - 24) for d in _o]
    ck("⑧ 八个相邻格都在（不重不漏）", len(set(_o)) == 8 and all(abs(d[0]) <= 1 and abs(d[1]) <= 1 for d in _o))
    ck("⑧ 正交的排在前面（对角贴上去聊不了：/interact 只打面前那格）",
       all(d[0] == 0 or d[1] == 0 for d in _o[:4]), str(_o))
    ck("⑧ 同类型里按离玩家近排", _ds[:4] == sorted(_ds[:4]) and _ds[4:] == sorted(_ds[4:]), str(_ds))
    ck("⑧ 第一格是**最近的正交格**（不再死磕 `ty+1`）",
       (_o[0][0] == 0 or _o[0][1] == 0) and _ds[0] == min(_ds[:4]), str(_o[:4]))
    _chat = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8",
                    errors="replace").read().split("def chat_npc(")[1].split("\ndef ")[0]
    ck("⑧「能不能开口」= 贴没贴到身边（≤1），不是 4/8 那种半径", "if _d > 1:" in _chat, "")
    ck("⑧ 贴不到会围着 NPC 再试几格（最多 4 个）", "_npc_stand_order(ax, ay, tx, ty)[:4]" in _chat)
    ck("⑧ 首次走位不再硬编码 `ty + 1`", "walk_natural(tx, ty + 1)" not in _chat)
    ck("⑧ 走不到时如实报「最近 N 格」+ 给下一步", "走不到" in _chat and "map walk x=" in _chat)
    ck("⑧ 朝向按实际相对位置算（不再写死朝上 `api.face(0)`）",
       "_npc_face_dir(ax, ay, tx, ty)" in _chat and "api.face(0)" not in _chat)
    ck("⑧ `_npc_face_dir`：东=1 西=3 下=2 上=0",
       (M._npc_face_dir(0, 0, 1, 0), M._npc_face_dir(0, 0, -1, 0),
        M._npc_face_dir(0, 0, 0, 1), M._npc_face_dir(0, 0, 0, -1)) == (1, 3, 2, 0))

    print("\n⑨ C# 那边真给了 `TalkedToToday`（否则④永远只剩半句）")
    _cs = io.open(os.path.join(os.path.dirname(HERE), "ModEntry.cs"),
                  encoding="utf-8", errors="replace").read()
    _blk = _cs.split("private object HandleFriendship")[1].split("TasteLabel")[0]
    ck("HandleFriendship 回 `talkedToToday`", "talkedToToday" in _blk)
    ck("…取自 `f.TalkedToToday`（游戏自己的记录）", "TalkedToToday" in _blk)

    M.api.state, M.api.find_npc, M.api._get = _st, _fn, _get

    print()
    if FAIL:
        print(f"❌ {len(FAIL)} 条没过：" + " / ".join(FAIL))
        print("   ⇒ `map npc` 是**找人用的**，不是聊天记录本。")
        sys.exit(1)
    print("✅ map npc 输出全绿：一行一个人，位置 + 今天聊没聊/送没送礼，完。")
    sys.exit(0)


if __name__ == "__main__":
    main()
