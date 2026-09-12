"""🎪 就绪屏文案自测 —— `_menu_advice` 的 readycheckdialog 分支。

为什么要钉住（2026-09-13）：
  恒卡在节日入场就绪框时，**屏上写的是「🛏️ 睡觉就绪屏」** —— 因为 `ReadyCheckDialog`
  是**一个类管两件事**（`checkName` = `"sleep"` / `"festivalStart"`），而 Python 这边原先
  只看菜单 type，**一律按睡觉喊**。恒那句「我以为是睡觉呢」就是这个 bug。
  ⇒ 现在按 `activeMenu.readyCheck.name` 分派；旧 DLL 不报 `readyCheck` 时**如实说分不出**，
    不硬猜（宁报错别兜底）。

⚠️ 另一半同样重要：卡在 **2/2（齐了却没放行）** 时，文案必须明确喊「**别 cancel**」——
   撤了就绪 = 房主更等不到人 = 自己把门关上。这条钉死了。

跑法：`PYTHONIOENCODING=utf-8 python _readycheck_hint_selftest.py`
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("_srv", os.path.join(HERE, "nagi_mcp_server.py"))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def rc(name=None, nr=None, nq=None, ok=None):
    """构造 activeMenu 里的 readyCheck 子对象（字段齐 = 新 DLL；全 None = 旧 DLL）。"""
    if name is None and nr is None:
        return {}
    d = {}
    if name is not None:
        d["name"] = name
    if nr is not None:
        d["numberReady"] = nr
    if nq is not None:
        d["numberRequired"] = nq
    if ok is not None:
        d["isReady"] = ok
    return {"readyCheck": d}


# (activeMenu 的 readyCheck 部分, 必须出现, 必须不出现, 说明)
CASES = [
    (rc("festivalStart", 2, 2, False), ["🎪", "别 cancel", "房主"],
     ["睡觉"], "**元凶场景**：节日 + 2/2 卡住 → 必须喊节日 + 别 cancel，绝不能再说睡觉"),
    (rc("festivalStart", 1, 2, False), ["🎪", "别 cancel"],
     ["睡觉"], "节日 + 人没齐 → 节日文案，别 cancel"),
    (rc("festivalStart", 1, 2, True), ["🎪"],
     ["睡觉"], "节日 + 已放行（转瞬即逝的过渡帧）不误报"),
    (rc("sleep", 1, 2, False), ["🛏️", "睡觉"],
     ["🎪"], "睡觉就绪 → 睡觉文案（menu cancel 那条路仍然给）"),
    # 旧 DLL：server 没升级。⚠️ 这里禁的是 **🛏️ 图标**（那等于"断言这是睡觉框"），
    #   不是"睡觉"二字 —— 文案本身合法地写着"分不出睡觉/节日"（第一版判据就是这么误报的）。
    (rc(), ["就绪", "分不出"],
     ["🛏️"], "旧 DLL 不报 readyCheck → **如实说分不出**，别硬猜睡觉"),
    (rc("someNewCheck", 1, 1, False), ["someNewCheck"],
     ["睡觉"], "未知 checkName → 原样报出来，别硬套已知类型"),
]


def main() -> int:
    print("🎪 _menu_advice(readycheckdialog) —— 就绪屏文案判据\n")
    bad = 0
    for am, must, mustnot, note in CASES:
        got = srv._menu_advice("ReadyCheckDialog", am, None) or ""
        miss = [s for s in must if s not in got]
        leak = [s for s in mustnot if s in got]
        ok = not miss and not leak
        bad += not ok
        print(f"  {'✅' if ok else '❌'} {note}")
        if not ok:
            if miss:
                print(f"       缺: {miss}")
            if leak:
                print(f"       多了不该有: {leak}")
            print(f"       实际: {got}")
    print()
    if bad:
        print(f"❌ {bad}/{len(CASES)} 条不过 —— 多半是又把 ReadyCheckDialog 一律当睡觉了。"
              f"\n   `checkName` 才是判据：festivalStart=节日入场 / sleep=就寝；缺字段就如实说分不出。")
        return 1
    print(f"✅ 全部通过（{len(CASES)}/{len(CASES)}）—— 离线自测；真机文案仍待下次卡框时肉眼看一眼")
    return 0


if __name__ == "__main__":
    sys.exit(main())
