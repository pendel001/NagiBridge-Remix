# -*- coding: utf-8 -*-
"""🐍 探针：飞蛇打不到，到底是**3×3 够不着**还是**两端看到的不一样**？（2026-09-20 恒提问）

起因（真机）：沙漠跑了 15 层，guard 挥 43 刀 —— Green Slime 31 / Bug 8 / Blue Squid 4 /
  **Serpent 0**。恒现场："没打飞蛇"。我第一反应是"farmhand 的位置是插值出来的，飞蛇太快所以滞后"
  —— **恒当场推翻**："按理来说延迟是延迟，我看到的是**根本没挥**。"（滞后 ⇒ 晚挥；不挥 ⇒ 不是滞后）

零成本先手（不需要新端点）：`/find_npc` 的 C# 实现**不过滤怪物**（只带了个 `isVillager` 标记），
  所以 `name=Serpent` 能直接读到飞蛇的 `TilePoint`。而且**两端各读一次**就能回答那个老问题
  ——"farmhand 那份 `loc.characters` 到底可不可信"（⚠️ 09-17 是 farmhand 有幽灵 NPC、
  **不是"房主就一定对"**）。

本探针同时记三件事，每 0.5s 一行：
  ① AI 自己那格（判据：Chebyshev 半径 1 = guard 的命中范围）
  ② 本层的飞蛇：**两端各读一次**（7843/7842）+ 每条离 AI 的切比雪夫距离
  ③ guard 的 `swings` / `lastTarget`（挥没挥、朝谁挥）

读出来能直接判：
  · 飞蛇**总在 ≥2 格** ⇒ 恒那个「只有身边格」的猜想成立，3×3 结构性够不着
  · 飞蛇进到 ≤1 格但 `swings` 不涨 ⇒ guard 看得见却不挥（另一类病）
  · 7843 有 / 7842 没有（或反过来） ⇒ 是"两端不一致"那族，得改从哪端读

只读，不动角色。配合一趟短冲层跑（`bomb_mine --target 126`）抓样本。
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

AI = "http://localhost:7843"
HOST = "http://localhost:7842"


def get(base, ep, timeout=6):
    try:
        return json.loads(urllib.request.urlopen(base + ep, timeout=timeout).read().decode("utf-8"))
    except Exception as e:
        return {"_err": str(e)}


def serpents(base):
    """返回 [(location, x, y)] —— 只取 AI 附近那层的，跨层的不关心。"""
    d = get(base, "/find_npc?name=Serpent")
    return [(n.get("location"), n.get("x"), n.get("y")) for n in d.get("npcs", [])]


def main(duration=180):
    t0 = time.time()
    hits = []          # 飞蛇进了 3×3 的时刻
    near = []          # 飞蛇最近到过几格
    seen = 0           # 见过飞蛇的采样数
    while time.time() - t0 < duration:
        st = get(AI, "/state")
        if "_err" in st:
            time.sleep(0.5)
            continue
        p = st["player"]
        # ⚠️ `/state` 的 player.x/y **就是格**（真机核对：`/state`=8,5 与 `/surroundings` 的
        #    `center`=8,5 完全一致；`find_blocker` 也是拿它直接和岩体格比）。**别再除以 64**
        #    —— 我自己第一版就除了，那会让所有距离恒为 0（"全都在身边"的假绿）。
        loc, px, py = st["location"]["name"], p["x"], p["y"]
        g = get(AI, "/guard")
        sw, lt = g.get("swings"), g.get("lastTarget")

        s_ai = [s for s in serpents(AI) if s[0] == loc]
        s_ho = [s for s in serpents(HOST) if s[0] == loc]
        if s_ai or s_ho:
            seen += 1
        for (sloc, sx, sy) in s_ai:
            d = max(abs(sx - px), abs(sy - py))     # 切比雪夫 = guard 的判据
            near.append(d)
            if d <= 1:
                hits.append((round(time.time() - t0, 1), loc, px, py, sx, sy, sw, lt))
        if s_ai or s_ho:
            print("[%6.1fs] %-18s AI(%2d,%2d) hp=%-3s | 本层飞蛇: 7843=%d %s  7842=%d %s | swings=%s last=%s"
                  % (time.time() - t0, loc, px, py, p["health"],
                     len(s_ai), [(x, y) for _, x, y in s_ai],
                     len(s_ho), [(x, y) for _, x, y in s_ho], sw, lt), flush=True)
        time.sleep(0.5)

    print("\n===== 结论素材 =====")
    print("见过飞蛇的采样数 =", seen)
    if near:
        print("飞蛇到 AI 的切比雪夫距离分布 =", {d: near.count(d) for d in sorted(set(near))})
    else:
        print("整趟没在 AI 那层读到飞蛇（没样本）")
    print("飞蛇进到 ≤1 格（guard 该挥）的时刻 =", len(hits))
    for h in hits:
        print("   ", h)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 180)
