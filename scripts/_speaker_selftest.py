# -*- coding: utf-8 -*-
"""💬 对话说话人署名 离线自检（2026-09-11）。
用法: PYTHONIOENCODING=utf-8 python scripts/_speaker_selftest.py"""
import inspect, io, os, sys
if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nagi_mcp_server as M

FAIL = []
def case(label, got, want):
    ok = got == want
    print(("  ✅ " if ok else "  ❌ ") + f"{label}: {got!r}" + ("" if ok else f"  期望 {want!r}"))
    if not ok: FAIL.append(label)

# 1) 取说话人：有/无/异常都不炸
case("NPC 台词", M._speaker_of({"speaker": "罗宾", "dialogue": "瞧！"}), "罗宾")
case("旁白(无 speaker)", M._speaker_of({"dialogue": "……"}), "")
case("speaker 为 None", M._speaker_of({"speaker": None}), "")
case("activeMenu 为空", M._speaker_of(None), "")
case("activeMenu 是垃圾", M._speaker_of({"speaker": "  "}), "")

# 2) 署名渲染
case("有名", M._attributed("瞧！我的最新作品。", "罗宾"), "罗宾「瞧！我的最新作品。」")
case("无名(旁白)", M._attributed("呃……罗宾？", ""), "「呃……罗宾？」")

# 3) ⚠️ 关键：游戏关头像时 getCurrentString 自带 "名字: " 前缀，别拼成两遍
case("已带 '罗宾: ' 前缀", M._attributed("罗宾: 瞧！", "罗宾"), "「罗宾: 瞧！」")
case("已带 '罗宾' 开头",   M._attributed("罗宾说你好", "罗宾"), "「罗宾说你好」")
case("前缀是别人不算",      M._attributed("艾米丽: 嗨", "罗宾"), "罗宾「艾米丽: 嗨」")

# 4) 🐛 去重回归（2026-09-11 真机当场抓的）：同一句被读多次只该进缓冲一条。
#    病根 = 去重拿**原始 line** 比，而缓冲存的是**署名后的** entry ⇒ 永不相等 → 重复追加。
#    直接跑真函数 `_dismiss_dialogue`（stub 掉网络/按键/sleep），别只测我自己抄的逻辑。
_menu = {"type": "DialogueBox", "dialogue": "瞧！我的最新作品。", "speaker": "罗宾"}
_orig = (M.api.state, M.api.key, M.time)
M.api.state = lambda light=False: {"activeMenu": dict(_menu)}
M.api.key = lambda *a, **kw: {"ok": True}
class _T:
    def sleep(self, *_): pass
M.time = _T()
try:
    M._story_buffer.clear()
    M._dismiss_dialogue(dict(_menu))
    case("同一句读 10 次只进 1 条", len(M._story_buffer), 1)
    case("   且署了名", M._story_buffer[0] if M._story_buffer else "", "罗宾「瞧！我的最新作品。」")
finally:
    M.api.state, M.api.key, M.time = _orig
    M._story_buffer.clear()

# 5) 🐛 双重引号回归：缓冲已自带「」⇒ 状态条别再包一层（否则出「罗宾「…」」）
_src = inspect.getsource(M._build_state_strip)
case("状态条不再重复包「」", 'f"「{l}」"' in _src and "STILL" or "ok", "ok")

print("\n" + ("❌ 失败: " + ", ".join(FAIL) if FAIL else "✅ 全过"))
sys.exit(1 if FAIL else 0)
