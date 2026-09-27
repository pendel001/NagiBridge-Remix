"""📬 邮箱那条线的纯 Python 自验（不起服务、不碰游戏）——2026-09-24。

起因：AI 被告知"去邮箱交互读信"，却**不知道邮箱在哪**（邮箱坐标从来没人交给它），
在小屋里瞎点了一格 (12,8)，而那天手机↔PC 的网又断了 ⇒ 报出来的是 ConnectException，
把"找不到邮箱"这层真问题盖住了。

测四件（全是判据，不是"跑一遍看看"）：
  ① `_mailbox_line`：跨图 → 给 map go 邮箱；同图远 → 给 scene at；同图 ≤2 格 → 不念；缺字段 → 不炸
  ② `_cross_map_guard`：界外+是邮箱坐标 → 点名邮箱并给下一步；界外+普通坐标 → 给 map go；界内 → 放行
  ③ `navigation._resolve_place("邮箱")`：走 /state.mailbox，落点是**旁边的可站格**（邮箱格站不住）
  ④ `open_questlog`：开着信(LetterViewerMenu) → 拒开且**不打 /open_questlog**（信会永久丢）
"""
import os
import sys
import time

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import nagi_mcp_server as M          # import 安全：末尾才 if __name__ == "__main__"
import navigation as N

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


# ── 假 api：只回答这次要问的 ─────────────────────────────────────────────
class FakeApi:
    def __init__(self, state=None, passable=None, posts=None, unread=None, mail=None):
        self._state = state or {}
        self._passable = passable or {}
        self.posts = posts if posts is not None else []
        self._unread = unread if unread is not None else [{"id": "spring_2_1"}]
        self._mail = mail          # 给了就原样当 /mail 回包（用来造"读不到"）

    def state(self, **kw):
        return self._state

    def _post(self, endpoint, data=None, **kw):
        if endpoint == "/passable":
            return {"passable": bool(self._passable.get((data["x"], data["y"])))}
        self.posts.append((endpoint, data))
        return {"ok": True}

    def _get(self, endpoint, params=None):
        if endpoint == "/mail":
            return self._mail if self._mail is not None else {"unread": list(self._unread)}
        return {}


def _fresh_mail_cache():
    """📬 未读邮件封数是 20s 缓存的 —— 每换一次场景必须作废，否则测的是上一轮的缓存。"""
    M._MAIL_CACHE.update(ts=0.0, n=-1)


print("\n① _mailbox_line —— AI 到底看不看得见邮箱在哪（**且只在真有未读信时念**）")
CABIN = {"location": {"name": "Cabin"}, "player": {"x": 9, "y": 9}}
MB = {"x": 56, "y": 16, "location": "Farm"}
_real_api = M.api
try:
    M.api = FakeApi()

    _fresh_mail_cache()
    l = M._mailbox_line({**CABIN, "mailbox": MB})
    ck("有未读信 + 人在小屋 → 说清是 Farm(56,16) 且给 map go 邮箱",
       "Farm(56,16)" in l and "map go 邮箱" in l, l)
    ck("…而且**带上 kw 键名**（别让 AI 去猜 poi/destination）", "destination" in l, l)

    _fresh_mail_cache()
    l = M._mailbox_line({"location": {"name": "Farm"}, "player": {"x": 40, "y": 40}, "mailbox": MB})
    ck("同图但离得远 → 给 scene at 56 16", "scene at 56 16" in l, l)

    _fresh_mail_cache()
    M._STATE_DELTA.pop("mailbox_near", None)
    l = M._mailbox_line({"location": {"name": "Farm"}, "player": {"x": 55, "y": 16}, "mailbox": MB})
    # ⚠️ 2026-09-25 改判据（恒：「跟新邮件一起，不用多做一条」）：小新闻里那两条"未读"行
    #    现在**被跳过**了，所以站在邮箱边**不能再闭嘴** —— 否则 AI 站在邮箱前一个字都收不到。
    #    改成"说一次读它"（`_delta_show` 去重，不每次刷）。
    ck("已经站在邮箱旁边 → 说一次「读它」（别让人干站着没提示）",
       "scene at 56 16" in l and "读" in l, l)
    ck("…而且不重复刷（第二次闭嘴）", M._mailbox_line(
        {"location": {"name": "Farm"}, "player": {"x": 55, "y": 16}, "mailbox": MB}) == "")

    # ⚠️ 2026-09-25 真机逮到：`/mail` 的 unread 是**对象**（{id,title,body}，body 是整封信）——
    #    直接 str(x) 会把**信的正文**塞进状态条，那一行炸成几百字。只准取标题。
    class FakeApiObj:
        def _get(self, ep, *a, **k):
            if ep == "/mail":
                return {"unread": [
                    {"id": "robinKitchenLetter", "title": "robinKitchenLetter",
                     "body": "亲爱的农夫：^^我们第一次见面的时候我嘲笑了你爷爷的老木屋……" * 5},
                    {"id": "fishing2", "title": "fishing2", "body": "我进了一些新货品！" * 5}]}
            return {}
    M.api = FakeApiObj()
    _fresh_mail_cache()
    l = M._mailbox_line({"location": {"name": "Beach"}, "player": {"x": 1, "y": 1}, "mailbox": MB})
    ck("邮件只取**标题**，正文一个字都不许进状态条",
       "robinKitchenLetter" in l and "亲爱的农夫" not in l and len(l) < 200, l[:120])
    M.api = FakeApi(unread=[])
    _fresh_mail_cache()
    l = M._mailbox_line({**CABIN, "mailbox": MB})
    ck("**邮箱没未读信 → 一行都不念**（恒：没信不用兴趣点）", l == "", l)

    M.api = FakeApi(mail={"error": "boom"})     # /mail 读不到（缺 unread 键）
    _fresh_mail_cache()
    l = M._mailbox_line({**CABIN, "mailbox": MB})
    ck("读不到未读队列 → 不念（没证据不占版面，别把「不知道」当「有」）", l == "", l)

    M.api = FakeApi()
    _fresh_mail_cache()
    ck("邮箱字段缺失 → 空串不炸", M._mailbox_line({**CABIN, "mailbox": None}) == "")
    ck("邮箱字段残缺 → 空串不炸", M._mailbox_line({**CABIN, "mailbox": {"x": "?", "location": "Farm"}}) == "")
finally:
    M.api = _real_api

print("\n② _cross_map_guard —— 点别张图的格子要当场拦住")
_real = M.api
try:
    # 尺寸是**假值**（真值从 `/state.location.mapWidth/mapHeight` 来，实测 AI 小屋那份报 12×12）；
    # 这里只验"界外拦、界内放"这条判据本身。
    M.api = FakeApi(state={"location": {"name": "Cabin", "mapWidth": 12, "mapHeight": 12},
                           "mailbox": MB})
    g = M._cross_map_guard(56, 16)
    ck("小屋(12×12)里点 (56,16)=邮箱坐标 → 拦 + 点名邮箱 + 给 map go 邮箱",
       "邮箱" in g and "map go 邮箱" in g and "scene at 56 16" in g, g)

    g = M._cross_map_guard(40, 40)
    ck("小屋(12×12)里点界外普通格 (40,40) → 拦 + 给 map go <地点>",
       "map go" in g and "邮箱" not in g, g)

    ck("界内 (9,9) → 放行（Python 无从知道同名坐标属于哪张图，不装懂）",
       M._cross_map_guard(9, 9) == "")

    M.api = FakeApi(state={"location": {"name": "Farm", "mapWidth": 80, "mapHeight": 65},
                           "mailbox": MB})
    ck("在 Farm 上点邮箱那格 (56,16)（界内）→ 放行", M._cross_map_guard(56, 16) == "")

    M.api = FakeApi(state={})   # 旧 DLL / 读不到尺寸
    ck("读不到地图尺寸 → 放行（不误拦）", M._cross_map_guard(999, 999) == "")
finally:
    M.api = _real

print("\n③ navigation._resolve_place('邮箱') —— map go 邮箱 落哪一格")
_real_nav = N.api
try:
    N.api = FakeApi(state={"mailbox": MB},
                    passable={(55, 16): False, (56, 17): True})   # 左格站不住 → 应挑下格
    got = N._resolve_place("邮箱")
    ck("落点 = 旁边**可站**的那格 (56,17)，不是邮箱格本身",
       got == ("Farm", 56, 17), str(got))

    N.api = FakeApi(state={"mailbox": MB}, passable={(55, 16): True})
    ck("左格可站时优先用左格 (55,16)", N._resolve_place("邮箱") == ("Farm", 55, 16))

    N.api = FakeApi(state={"mailbox": MB}, passable={})
    ck("四邻全站不住 → 返回 None（宁走兜底，不硬塞一个站不住的落点）",
       N._resolve_place("邮箱") is None)

    N.api = FakeApi(state={}, passable={})
    ck("老 DLL 没有 mailbox 字段 → None（不炸）", N._resolve_place("邮箱") is None)

    N.api = FakeApi(state={"mailbox": MB}, passable={(55, 16): True})
    ck("英语 mailbox 同义可解", N._resolve_place("mailbox") == ("Farm", 55, 16))
finally:
    N.api = _real_nav

print("\n④ open_questlog —— 信没读完不许顶菜单（顶掉=这封信永久读不到）")
try:
    M.api = FakeApi(state={"activeMenu": {"type": "LetterViewerMenu"}})
    out = M.open_questlog()
    ck("开着信 → 拒开，且**没有**真的打 /open_questlog",
       "/open_questlog" not in [p[0] for p in M.api.posts] and "信" in out, out)

    M.api = FakeApi(state={"activeMenu": {"type": "QuestLog"}})
    M.open_questlog()
    ck("没开着信 → 照常开", "/open_questlog" in [p[0] for p in M.api.posts])

    M.api = FakeApi(state={"activeMenu": None})
    M.open_questlog()
    ck("没菜单 → 照常开", [p[0] for p in M.api.posts] == ["/open_questlog"],
       str(M.api.posts))
finally:
    M.api = _real

print("\n⑤ 坐标参数别名 —— AI 传 x/y 也要能落到 tile_x/tile_y")
import inspect


def _sig_of(fn):
    return inspect.signature(fn)


kw, dropped = M._filter_kw(_sig_of(M.interact_at), {"x": 56, "y": 16})
ck("interact_at：x/y → tile_x/tile_y（不是报参数错）",
   kw == {"tile_x": 56, "tile_y": 16} and dropped == [], f"{kw} / {dropped}")

kw, dropped = M._filter_kw(_sig_of(M.interact_at), {"tile_x": 56, "tile_y": 16})
ck("正式名照旧可用", kw == {"tile_x": 56, "tile_y": 16}, str(kw))


def _fake_xy(x: int = 0, y: int = 0):
    return (x, y)


kw, dropped = M._filter_kw(_sig_of(_fake_xy), {"x": 1, "y": 2})
ck("本来就叫 x/y 的 op：原样收下（别名不乱改）", kw == {"x": 1, "y": 2}, str(kw))

# 🗺️ 第二层：引导文案写「map go 邮箱」→ AI 猜了 `kw={"poi":"邮箱"}`（同域 walk 的参数就叫 poi_name）
kw, dropped = M._filter_kw(_sig_of(M.map_go), {"poi": "邮箱"})
ck("map go：poi → destination（别让 AI 白吃一次「忽略了无法识别的参数」）",
   kw == {"destination": "邮箱"} and dropped == [], f"{kw} / {dropped}")

kw, dropped = M._filter_kw(_sig_of(M.map_go), {"poi_name": "邮箱"})
ck("map go：poi_name 也收（同域 walk 就叫这个名，AI 会串）",
   kw == {"destination": "邮箱"}, str(kw))

kw, dropped = M._filter_kw(_sig_of(M.walk_to), {"poi_name": "码头"})
ck("map walk：本来就叫 poi_name，原样收下", kw == {"poi_name": "码头"}, str(kw))

print("\n⑥ 菜单闸门：**要拦就先重读真值**（别拿缓存里的旧菜单拦人）")
_real_cache = dict(M._MENU_GATE_CACHE)
try:
    # 真机现场：AI `menu cancel` 关掉 QuestLog 后 0.3s 就 `map go`，闸门用的是 TTL 内的旧值
    M.api = FakeApi(state={"activeMenu": None})
    M._MENU_GATE_CACHE.update(ts=time.time(), menu="QuestLog")     # 缓存里还留着"菜单开着"
    blocked, note = M._menu_gate("map", {"ops": "go"}, (), M.map_lookup)
    ck("缓存说『QuestLog 开着』但真值已关 → **放行**（不许拿旧值拦人）",
       blocked is False and note == "", f"{blocked} / {note}")

    M.api = FakeApi(state={"activeMenu": {"type": "QuestLog"}})
    M._MENU_GATE_CACHE.update(ts=0.0, menu="QuestLog")   # 缓存作废 → 闸门自己去读真值
    blocked, note = M._menu_gate("map", {"ops": "go"}, (), M.map_lookup)
    ck("真值确实开着 → 照拦（重读不等于放水）", blocked is True and "QuestLog" in note, note)
finally:
    M._MENU_GATE_CACHE.update(_real_cache)

print("\n" + ("=" * 46))
print("❌ 失败 " + str(len(FAIL)) + " 项: " + ", ".join(FAIL) if FAIL else "✅ 全过（0 失败）")
sys.exit(1 if FAIL else 0)
