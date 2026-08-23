# -*- coding: utf-8 -*-
"""火山探针：warp 到 VolcanoDungeon1 看布局/梯子/怪物，验证可行后回农场"""
import sys, time, json
sys.stdout.reconfigure(encoding='utf-8')
from bomb_common import BombMiner

bot = BombMiner(port=7843, host_port=7842)

def st():
    s = bot.state()
    return s.get("location", {}).get("name"), s["player"].get("x"), s["player"].get("y")

loc, x, y = st()
print(f"[出发] {loc} ({x},{y})")

# 1. warp 到火山 1 层
r = bot.warp("VolcanoDungeon1", 5, 5)
print(f"[warp VolcanoDungeon1(5,5)] ok={r}")
time.sleep(1.5)
loc, x, y = st()
print(f"[到达] {loc} ({x},{y})")
if not loc or "VolcanoDungeon" not in loc:
    print("[!] 没进火山，退出")
    sys.exit(0)

# 2. 看 surroundings：岩体/怪物/梯子
d = bot.surroundings(20)
tiles = d.get("tiles", [])
rocks = [t for t in tiles if t.get("object")]
monsters = d.get("monsters", [])
print(f"[布局] 岩体/物件 {len(rocks)} 个，怪物 {len(monsters)} 只")
for t in tiles[:15]:
    print(f"   tile ({t['x']},{t['y']}) obj={t.get('object')} passable={t.get('passable')}")
print("[怪物]", [m.get("name") for m in monsters][:8])

# 3. /ladder
try:
    rl = bot._get("/ladder")
    print(f"[ladder] {json.dumps(rl, ensure_ascii=False)[:300]}")
except Exception as e:
    print("[ladder] err", e)

# 4. 位置安全
print(f"[position_safe(5,5)] {bot.position_safe(5,5, exact=True)}")
print(f"[position_safe(10,10)] {bot.position_safe(10,10, exact=True)}")

# 5. 回农场
bot.warp("FarmHouse", 20, 10)
time.sleep(1.0)
print(f"[回程] {st()}")
