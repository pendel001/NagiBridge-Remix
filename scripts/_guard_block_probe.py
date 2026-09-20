# -*- coding: utf-8 -*-
"""🔬 探针：一边跑脚本一边采样 `/guard` 的 `block` —— 回答「guard 为什么不挥」。（2026-09-20）

**为什么需要它**：头骨矿洞里 guard 一刀不挥（恒现场："啥都不打。幽灵也不打，木乃伊也不打"），
  但把同样的代码放进**站桩**场景（站着不动、不跑脚本）测，普通矿井 43 刀全中、
  头骨矿洞打大史莱姆也中。⇒ 至少有两个独立变量纠缠在一起：**「跑脚本(忙)」** 和 **「怪的种类」**。
  光靠"挥了几刀"分不开，所以 `ModEntry.cs` 加了 `block`（这一 tick 卡在哪道门）+ `charsMonsters`。

**怎么读**（这是本探针的全部价值）：
  · `block` 分布里 **12(UsingTool) / 11(蓄力) 占大头** ⇒ "忙"那条线成立（脚本在挥镐/放炸弹，
    guard 被动画门挡住）。这**不是** bug，是设计；但意味着**炸矿时基本没有自卫**。
  · `block=13` 而 `charsMonsters > 0` 且 `nearestMonster <= 1` ⇒ **列表里有怪、就在身边格，
    却被判"没有打得动的怪"** ⇒ 问题出在 `GuardTargetable` 的过滤条件上
    （候选：装壳岩蟹 / 装甲虫 / **`isInvincible()`**（`Monster.cs:371`，挨打后有 450ms 无敌帧，
    `Monster.cs:588`）/ `IsInvisible`）。
  · `charsMonsters == 0` ⇒ 数据源问题（这条已被真机证伪：头骨矿洞里 `charsMonsters` 有 4~7）。

只读，不动角色。用法：`python _guard_block_probe.py [秒数]`（先跑 bomb_mine，再跑它）。
"""
import collections
import json
import sys
import time
import urllib.request

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

B = "http://localhost:7843"
MON = ["Serpent", "Royal Serpent", "Mummy", "Ghost", "Big Slime", "Green Slime",
       "Squid", "Bug", "Bat", "Skeleton"]


def g(ep):
    try:
        return json.loads(urllib.request.urlopen(B + ep, timeout=6).read().decode("utf-8"))
    except Exception as e:
        return {"_err": str(e)}


def main(duration=330):
    t0 = time.time()
    last_sw = None
    blocks = collections.Counter()
    close = []          # 身边格有怪时的 (block, blockName, charsMonsters, nearest, swings, loc)
    for _ in range(int(duration / 0.8)):
        st = g("/state")
        p = st.get("player") or {}
        if not p:
            time.sleep(1)
            continue
        loc = st["location"]["name"]
        gd = g("/guard")
        blk, sw = gd.get("block"), gd.get("swings")
        cm, nm = gd.get("charsMonsters"), gd.get("nearestMonster")
        if gd.get("on"):
            blocks[blk] += 1
            if isinstance(nm, int) and 0 <= nm <= 1:
                close.append((blk, gd.get("blockName"), cm, nm, sw, loc))
            if sw != last_sw:
                print("[%6.1fs] %-18s swings %s→%s | block=%s(%s) charsMonsters=%s near=%s"
                      % (time.time() - t0, loc, last_sw, sw, blk, gd.get("blockName"), cm, nm),
                      flush=True)
                last_sw = sw
        time.sleep(0.8)

    print("\n===== guard 开着期间的 block 分布（采样数） =====")
    for b, n in blocks.most_common():
        print("   block=%-3s %-30s %d 次" % (b, _name(b), n))
    print("\n===== 怪贴到身边格（near<=1）时的采样 =====")
    if not close:
        print("   （整趟没有怪进到身边格 —— 没样本）")
    for r in close[:40]:
        print("   block=%-3s charsMonsters=%-3s near=%-2s swings=%-4s %s" % (r[0], r[2], r[3], r[4], r[5]))


def _name(b):
    return {-1: "未评估", 0: "✅挥了", 1: "世界没就绪", 2: "guard关着", 3: "房主进程",
            4: "血<=0", 5: "菜单", 6: "事件", 7: "游泳/桥", 8: "躺床", 9: "骑马",
            10: "吃东西", 11: "蓄力中", 12: "UsingTool(动画)", 13: "3×3内没有打得动的怪",
            14: "没武器", 15: "currentLocation为空"}.get(b, str(b))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 330)
