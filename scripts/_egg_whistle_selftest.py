"""🔔 蛋蛋节"结束哨"判据自测 —— `_egg_hunt_over()` 的 -1 语义。

为什么要单独钉住（2026-09-13）：
  恒："**能不能检测到结束的吹哨呢？吹哨时脚本停止不动应该就好了。**"
  判据本来就有（`festivalTimer` 归零 = 游戏自己 `Game1.player.Halt()`，`Event.cs:11171`），
  缺的是**粒度**——原来只在每颗蛋的间隙查一次，走路那最长 12s 没人听 ⇒ 哨响后人还在走。
  ⇒ 把查哨塞进 `_wait_walk` 的等待循环 + 交互前再查一次。

⚠️ **这个测试存在的唯一理由**：`festivalTimer` 里 **`-1` 是哨兵值、小负数是"真结束"**，两者语义**相反**：
  · `ModEntry.cs:17777` `int festivalTimer = -1;` —— 没事件 / 反射读不到，都给 -1
  · `Event.cs:11106` 只在 `>0` 时递减 ⇒ 最后一帧会**减过头**，此后一直保持那个小负值
  ⇒ 看着最自然的写法 `return t <= 0` 是**错的**（会把"读不到"当成"已结束"，当场误停、白丢蛋）。
  后人"顺手简化"回去 = 静默退化，所以这 7 条钉在这里。

跑法：`PYTHONIOENCODING=utf-8 python _egg_whistle_selftest.py`
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("_srv", os.path.join(HERE, "nagi_mcp_server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


class _FakeAPI:
    """只替 `event_state()`：`v` 模拟 C# 回的 festivalTimer（'raise'= 网络/服务异常）。"""

    def __init__(self, v):
        self.v = v

    def event_state(self):
        if self.v == "raise":
            raise RuntimeError("boom")
        return {} if self.v is None else {"festivalTimer": self.v}


# (festivalTimer 取值, 期望 _egg_hunt_over(), 说明)
CASES = [
    (-1,     False, "C# 哨兵：没事件在播 / 反射读不到 → **绝不能误停**"),
    (None,   False, "字段整个缺失（旧 DLL / 端点变了）→ 不误停"),
    ("raise", False, "读失败（掉线/超时）→ 不误停"),
    (52000,  False, "刚开跑（Event.cs:11614 固定 52000ms）"),
    (300,    False, "还剩 300ms，还在跑"),
    (0,      True,  "**归零 = 吹哨**（游戏此刻自己 Halt + 收回控制权）"),
    (-11,    True,  "减过头的小负数 = 也已结束（最后一帧减超了）"),
]


def main() -> int:
    print("🔔 _egg_hunt_over() —— 结束哨判据（-1 哨兵 vs 小负数=已结束）\n")
    bad = 0
    for v, want, note in CASES:
        srv.api = _FakeAPI(v)
        got = srv._egg_hunt_over()
        ok = got == want
        bad += not ok
        print(f"  {'✅' if ok else '❌'} festivalTimer={str(v):>7}  →  {str(got):>5}   {note}")
    print()
    if bad:
        print(f"❌ {bad}/{len(CASES)} 条不过 —— 多半是把 `-1` 和负数混成一句 `t <= 0` 了："
              f"\n   `-1` 是 C# 的哨兵（没事件/读不到），小负数是真结束，**语义相反**。")
        return 1
    print(f"✅ 全部通过（{len(CASES)}/{len(CASES)}）—— 离线自测；真机仍待下次节日听那一声哨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
