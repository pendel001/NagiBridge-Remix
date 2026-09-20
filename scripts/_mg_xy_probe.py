# -*- coding: utf-8 -*-
"""🎰 探针：minigame_click 的**裸坐标**路径（x,y）能不能替掉 action（2026-09-20）

`HandleMinigameClick` 两条路：
  ① action  → 反射读按钮字段 bounds.Center → receiveLeftClick(cx,cy)
  ② x,y     → 直接 receiveLeftClick(x,y)
两条最终都调 receiveLeftClick，但 ② 从没真机走过。
做法：第一局用 action="hit" 拿回包里的 (x,y)（就是 hit 按钮中心）→ 第二局拿这对坐标当 x,y 传，
      看是不是等效地补了一张牌（回包 clicked 应为 "minigame_pos"）。
"""
import sys, json, time
sys.path.insert(0, ".")
import requests

AI = "http://localhost:7843"


def st():
    return requests.get(f"{AI}/minigame_state", timeout=10).json()


def setcoins():
    return (requests.get(f"{AI}/state", timeout=10).json().get("player") or {}).get("clubCoins")


def click(**kw):
    return requests.post(f"{AI}/minigame_click", json=kw, timeout=20).json()


def cards(s):
    return s.get("playerCards") or []


def total(s):
    return sum(c for c in cards(s) if isinstance(c, int))


def wait_result(timeout=12):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = st()
        if s.get("showingResultsScreen") or not s.get("minigame"):
            return s
        time.sleep(0.5)
    return st()


def finish_hand():
    """把这局收掉（<17 要牌），回到结果屏。"""
    for _ in range(6):
        s = st()
        if s.get("showingResultsScreen") or not s.get("minigame"):
            return s
        if total(s) >= 17:
            break
        click(action="hit"); time.sleep(1.0)
    click(action="stand")
    return wait_result()


def new_hand():
    s = st()
    if s.get("showingResultsScreen"):
        click(action="play_again"); time.sleep(1.2)
    return st()


print("起点:", st().get("playerCards"), st().get("currentBet"), "coins=", setcoins())

# ── ① 用 action 拿 hit 按钮的真实中心坐标 ────────────────────────────────
s = new_hand()
print("A 局开局 玩家", cards(s))
r = click(action="hit")
time.sleep(1.0)
print("A 局 action=hit 回包:", json.dumps(r, ensure_ascii=False))
print("A 局 hit 后 玩家", cards(st()))
hx, hy = r.get("x"), r.get("y")
finish_hand()

# ── ② 同一对坐标，改用裸 x,y 传 ─────────────────────────────────────────
if hx is None or hy is None:
    print("❌ 没拿到 hit 按钮坐标，无法测裸坐标")
    raise SystemExit(1)

s = new_hand()
n_before = len(cards(s))
print(f"B 局开局 玩家 {cards(s)}（{n_before} 张）")
r2 = click(x=hx, y=hy)
time.sleep(1.0)
s2 = st()
n_after = len(cards(s2))
print(f"B 局 裸坐标({hx},{hy}) 回包:", json.dumps(r2, ensure_ascii=False))
print(f"B 局 之后 玩家 {cards(s2)}（{n_after} 张）")
print(f"→ 牌数 {n_before} → {n_after}；clicked={r2.get('clicked')}")
print("✅ 裸坐标等效补牌" if n_after > n_before and r2.get("clicked") == "minigame_pos"
      else "⚠️ 裸坐标没补牌（回包说点了，牌没动）")
finish_hand()
print("收尾:", st().get("showingResultsScreen"), "coins=", setcoins())
