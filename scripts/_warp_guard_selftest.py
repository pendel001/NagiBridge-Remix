# -*- coding: utf-8 -*-
"""🧱 `stardew_api.warp()` 的**落地校验** —— 纯离线钉子（不起服务、不碰游戏）。2026-10-06

来历（恒当天一句揪出来的）：
  「**我勒个在水中央  记得校验站位！！这不是真机能到达的地方吧**」
  —— 我给 `/warp` 喂了一个**自验夹具里的假坐标**（`Farm (40,32)`），那格在真机上是**水面**：
     `/passable=false`、`isMoving` 恒 false ⇒ 小人**卡在水中央**。
  ⚠️ 另一个当场逮到的**假报面**：`/warp` 回包里的 `actual` 印的是**落地前**的位置
     （我 warp 进 FarmHouse 了，回包还写 `Farm(40,32)`）⇒ **认落地只看 `/state`**。

钉四条路：
  ① 落点能站 ⇒ 原样返回、**不画蛇添足**（`landing_note` 不该出现）
  ② 落点站不住 + 有能站的邻格 ⇒ `/position` 挪过去 + 如实说"已挪到"
  ③ 落点站不住 + 四邻八邻都不能站 ⇒ 退回该图**默认落点** + 如实说
  ④ 读不到位置（游戏卡住）⇒ **不猜**：返回原结果 + "没能校验落点"
"""
import os
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stardew_api as A          # import 安全：这文件只定义函数

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


class Fake:
    """假 `/warp` `/position` `/state` `/passable`：只按剧本答。"""

    def __init__(self, land=(40, 32), passable_at=None, loc="Farm", neighbors_ok=(),
                 fail_state=False, default_land=(55, 12)):
        self.land = land                      # 第一次落地读到哪
        self.passable_at = dict(passable_at or {})   # 坐标 → 能不能站
        self.loc = loc
        self.neighbors_ok = tuple(neighbors_ok)       # 哪些邻格能站（按查询顺序取第一个命中的）
        self.fail_state = fail_state
        self.default_land = default_land
        self.calls = []
        self._warps = 0

    def post(self, ep, data=None):
        self.calls.append((ep, dict(data or {})))
        if ep == "/warp":
            self._warps += 1
            if self._warps > 1:               # 第二次 warp = 退回默认落点
                self.land = self.default_land
            return {"ok": True, "action": "warped",
                    "actual": {"location": self.loc, "x": 40, "y": 32}}   # ⚠️ 故意印"落地前"的假值
        if ep == "/position":
            self.land = (data.get("x"), data.get("y"))
            return {"ok": True, "action": "positioned"}
        if ep == "/passable":
            k = (data.get("x"), data.get("y"))
            if k == self.land:
                return {"ok": True, "passable": self.passable_at.get(k, True)}
            return {"ok": True, "passable": k in self.neighbors_ok}
        raise RuntimeError("unexpected POST " + ep)

    def get(self, ep, params=None):
        self.calls.append((ep, params))
        if self.fail_state:
            raise RuntimeError("game busy")
        return {"location": {"name": self.loc},
                "player": {"x": self.land[0], "y": self.land[1]}}


_real_post, _real_get = A._post, A._get
try:
    # ⚠️ 护栏**全程走 `_post`/`_get`**（跟它发的那发 `/warp` 同一套基址，见 `stardew_api.warp` 里那段注释）：
    #    我第一版护栏混用了 `_ai_post`/`_ai_get` —— 而本文件只桩了 `_post` ⇒ 那一发**真打**了出去，
    #    被 `_net_guard`（按**端口** hook `socket.connect`）当场 `ConnectionRefusedError` 拒掉，
    #    护栏又把异常吞成"没能校验" ⇒ 钉子红得莫名其妙（**游戏其实没被碰**，闸门是好的）。
    def _stub_all(f):
        A._post, A._get = f.post, f.get

    print("\n① 落点能站 ⇒ 原样返回，**不画蛇添足**")
    f = Fake(land=(55, 12), passable_at={(55, 12): True})
    _stub_all(f)
    r = A.warp("Farm", 55, 12)
    ck("…返回 ok、**没有 `landing_note`**（正常落地别多话）", r.get("ok") and "landing_note" not in r, str(r))
    ck("…也没多打 `/position`（不该乱挪人）",
       not any(ep == "/position" for ep, _ in f.calls), str(f.calls))

    print("\n② 落点站不住（水/墙）+ 有能站的邻格 ⇒ 挪过去 + 如实说")
    f = Fake(land=(40, 32), passable_at={(40, 32): False}, neighbors_ok={(40, 33)})
    _stub_all(f)
    r = A.warp("Farm", 40, 32)
    _note = r.get("landing_note") or ""
    ck("…明说「落点 (40,32) 站不住」", "站不住" in _note and "(40,32)" in _note, _note)
    ck("…并且真挪到了能站的邻格 (40,33)", "(40,33)" in _note, _note)
    ck("…用的确实是 `/position`（同图挪位）",
       any(ep == "/position" and d.get("x") == 40 and d.get("y") == 33 for ep, d in f.calls), str(f.calls))

    print("\n③ 落点站不住 + 邻格也都不行 ⇒ 退回该图默认落点")
    f = Fake(land=(40, 32), passable_at={(40, 32): False}, neighbors_ok=())
    _stub_all(f)
    r = A.warp("Farm", 40, 32)
    _note = r.get("landing_note") or ""
    ck("…明说站不住 + 已退回默认落点", "站不住" in _note and "默认落点" in _note, _note)
    ck("…真发了第二发 `/warp`（不带坐标）",
       sum(1 for ep, _ in f.calls if ep == "/warp") == 2, str(f.calls))

    print("\n④ 读不到位置（游戏卡住）⇒ **不猜**，如实说没校验")
    f = Fake(fail_state=True)
    _stub_all(f)
    r = A.warp("Farm", 40, 32)
    ck("…说「没能校验落点」，⛔ 不假称站得住", "没能校验落点" in str(r.get("landing") or ""), str(r))

    print("\n⑤ `check=False` ⇒ 老行为（愿意自己负责时用）")
    f = Fake(land=(40, 32), passable_at={(40, 32): False})
    _stub_all(f)
    r = A.warp("Farm", 40, 32, check=False)
    ck("…一发 `/warp` 就返回、不校验不挪人",
       len(f.calls) == 1 and "landing_note" not in r and "landing" not in r, str(f.calls) + str(r))
finally:
    A._post, A._get = _real_post, _real_get

print("\n" + "=" * 46)
print(("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL)) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
