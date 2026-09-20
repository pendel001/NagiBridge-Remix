# -*- coding: utf-8 -*-
"""🚶 真机走位探针：人**真的会绕开**大石头吗？（2026-09-20）

**上一支探针（`_clump_passable_probe.py`）只证明了"尺子"**——`/passable_rect` 说大石头那几格
`passable=False`。但那只证明**尺子读数**；恒要的是**看起来对不对**，也就是"人真的是绕着走的吗"。

**本探针测的**：把角色摆在**大石头正前方**，`walk_to` 到**正后方**——
直线必须穿过石头（BFS 的最短路就是穿过去），旁边 `y=57` 有一整行全空可绕。
⇒ 采样整趟的落点：
   · **从没踩过石头那 4 格** + **轨迹里出现 y=57 那一行** = 真的绕了 ✅
   · 踩到石头格                              = 尺子没生效 ❌

**选这条走廊的理由**（`(57,58)` 那块大石，`x=56` 起步 → `x=62` 收）：
   `y=58` 那一行 `x=56` 空、`57/58` 是石头、`59~63` 空；`y=57` **整行 51~63 全空**
   ⇒ **绕行路干净**（不会"因为旁边全是杂草所以本来就绕"这种说不清的结论）。
   这是关键：走一条**本来就堵得走不动**的走廊，绕开了也不能证明是石头拦的。

⚠️ 会**真挪动 AI 角色**（`walk_to`，拟人那条路，不是 `/warp`）。不碰房主。
"""
import sys
import time
import threading
import requests

BASE = "http://localhost:7843"          # AI(轮回) 端口
START = (64, 30)                        # 大石正前一格
GOAL = (71, 30)                         # 大石正后（直线必穿 (67,30)/(68,30)）
BOULDER = {(67, 30), (68, 30), (67, 31), (68, 31)}
DETOUR_ROW = 29                         # 干净的那条绕行行
# ⚠️ 2026-09-20 第一版选错了走廊（(57,58) 那块）：挑的时候我拿"有没有物件/地形"当尺子，
#    把那片画成空白 ⇒ 以为 y=57 是干净绕行路。**真相是那片是农场的池塘**（`isWater=True`）。
#    ⇒ **要判断"能不能走"就问 `passable`／`/dump_tile` 的 `mapPassable`+`isWater`**，
#      别拿"看起来空"当尺子（同族：别拿错尺子）。本走廊是**按 `passable` 选出来的**：
#      直线 x=64~71 除石头外全通、绕行行 y=29 从 x=65 到 x=69 全通。


def g(path, **kw):
    return requests.get(f"{BASE}{path}", params=kw or None, timeout=20).json()


def pos():
    s = g("/state")
    p = s.get("player") or {}
    return p.get("x"), p.get("y")


def all_clumps():
    """整片农场所有资源堆格（不只是那块大石）——**顺带验证整趟没踩任何一件**。"""
    r = g("/passable_rect", x1=0, y1=0, x2=80, y2=65, loc="Farm")
    return {(t["x"], t["y"]): t["resource"] for t in (r.get("tiles") or []) if t.get("resource")}


def walk(x, y, timeout=180):
    requests.post(f"{BASE}/walk_to", json={"location": "Farm", "x": x, "y": y}, timeout=20)
    t0 = time.time()
    last = pos()
    still = 0
    while time.time() - t0 < timeout:
        time.sleep(0.3)
        p = pos()
        if p == (x, y):
            return True, time.time() - t0
        if p == last:
            still += 1
            if still >= 12:                 # 4 秒没动 = 卡住/到了别处
                break
        else:
            still = 0
        last = p
    return False, time.time() - t0


def main():
    st = g("/state")
    if not st.get("worldReady"):
        print("❌ 世界没就绪")
        return 2
    print(f"🌍 起点 {pos()}   目标 {GOAL}   大石 {sorted(BOULDER)}")

    clumps = all_clumps()
    print(f"🪨 全农场资源堆 {len(clumps)} 格（整趟都不该踩到任何一件）\n")

    print(f"① 先走到石头正前方 {START} …")
    ok, sec = walk(*START)
    print(f"   {'✅ 到了' if ok else '⚠️ 没到（在 ' + str(pos()) + '）'}  {sec:.1f}s\n")

    print(f"② 关键一步：{START} → {GOAL}（直线必穿石头，y={DETOUR_ROW} 有干净绕行路）")
    samples = []
    stop = {"v": False}

    def sampler():
        while not stop["v"]:
            samples.append(pos())
            time.sleep(0.1)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    ok2, sec2 = walk(*GOAL)
    stop["v"] = True
    th.join(timeout=2)

    seq, seen = [], set()
    for p in samples:                       # 去重保序，读轨迹
        if p not in seen:
            seen.add(p)
            seq.append(p)
    print(f"   {'✅ 到了' if ok2 else '❌ 没到（停在 ' + str(pos()) + '）'}  {sec2:.1f}s，"
          f"采样 {len(samples)} 点 / 去重 {len(seq)} 格")

    hit = [p for p in seq if p in BOULDER]
    hit_any = [p for p in seq if p in clumps]
    detour = [p for p in seq if p[1] == DETOUR_ROW]

    print(f"\n═══ 轨迹（按时间顺序，只列前 60 个落点）═══")
    print("   " + " → ".join(f"({x},{y})" for x, y in seq[:60]))

    print("\n═══ 判定 ═══")
    if hit:
        print(f"❌ **踩到那块大石了**：{hit}")
        print("   ⇒ 尺子没生效。先查两边 DLL md5 是否一致、游戏是否真的重启过。")
        return 1
    if hit_any:
        print(f"❌ **踩到别的资源堆了**：{[(p, clumps[p]) for p in hit_any]}")
        return 1
    print(f"✅ 整趟 **{len(seq)} 个落点、一件资源堆都没踩过**（石头/树桩/原木都没踩）。")

    if detour:
        print(f"✅ 轨迹里出现 y={DETOUR_ROW} 那一行 {len(detour)} 格（{detour[:6]}）"
              f"—— **就是那条干净的绕行路** ⇒ 确实是「绕开了石头」，不是「路本来就通」。")
    else:
        print(f"⚠️ 没走 y={DETOUR_ROW} 那条绕行路（可能挑了另一侧）——轨迹见上，你眼睛过一下。")

    if ok2:
        print(f"✅ 而且**真的走到了** {GOAL} —— 没卡在石头上。")
    else:
        print(f"⚠️ 没走到 {GOAL}（停在 {pos()}）—— 得看是石头拦的还是别的杂物拦的。")
        return 1
    print("\n⚠️ 最后一句还是那句：这是**客观轨迹**，'看起来拟人不拟人'仍要你眼睛过一下。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
