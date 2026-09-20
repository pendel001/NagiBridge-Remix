# -*- coding: utf-8 -*-
"""🎰 探针：21点结果屏上的 `double` 到底是什么按钮（2026-09-20）

疑点：`minigame_click(action="double")` 映射到 CalicoJack 的 `doubleOrNothing` 字段，
      但 **牌局中途**点它：下注不变(100)、牌不动、金币不动 —— 空点。
      C# 注释写的是 "double=加倍"（像 21 点的 double-down），可疑。
假设：`doubleOrNothing` 是**结果屏上的"再赌一把"**（赢了才出现），不是中途加倍。
做法：连打若干局（<17 要牌），等到**赢局**的结果屏上点一次 double，看下注/金币/牌面怎么变。
"""
import sys, json, time
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"


def st():
    return requests.get(f"{AI}/minigame_state", timeout=10).json()


def coins():
    return (requests.get(f"{AI}/state", timeout=10).json().get("player") or {}).get("clubCoins")


def click(action):
    return requests.post(f"{AI}/minigame_click", json={"action": action}, timeout=20).json()


def brief(s):
    if not s.get("minigame"):
        return "无小游戏"
    r = "结果屏" if s.get("showingResultsScreen") else "牌局中"
    return (f"{r} 玩家{s.get('playerCards')} 庄家明牌{s.get('dealerUp')} "
            f"下注{s.get('currentBet')} won={s.get('playerWon')}")


def total(s):
    return sum(c for c in (s.get("playerCards") or []) if isinstance(c, int))


def wait_result(timeout=12):
    """等结果屏（直连 HTTP 拿不到 Python 工具那层自动等待，得自己等）。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = st()
        if s.get("showingResultsScreen"):
            return s
        time.sleep(0.5)
    return st()


print("起点:", brief(st()), "coins=", coins())

for i in range(1, 21):
    s0 = st()
    if not s0.get("minigame"):
        print(f"!! [{i}] 小游戏没了（可能钱不够自动退了）")
        break
    if not s0.get("showingResultsScreen"):
        print(f"[{i}] 起点不在结果屏，先收局:", brief(s0))
        click("stand"); wait_result()
    click("play_again")
    time.sleep(1.2)
    # 🎯 基本策略：<17 要牌
    for _ in range(6):
        s = st()
        if s.get("showingResultsScreen") or not s.get("minigame"):
            break
        if total(s) >= 17:
            break
        click("hit")
        time.sleep(1.0)
    click("stand")
    s = wait_result()
    print(f"[{i}] 打完 →", brief(s), "coins=", coins())
    if s.get("playerWon"):
        c0 = coins()
        print(f"  ✅ 赢局到手 → 结果屏上点 double（coins 前={c0}）:", brief(s))
        r = click("double")
        print("  double 回包:", json.dumps(r, ensure_ascii=False))
        time.sleep(2)
        print("  double 之后:", brief(st()), "coins=", coins())
        break
else:
    print("20 局没遇到赢局 —— 没拿到 double 的样本")
