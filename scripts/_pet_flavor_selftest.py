# -*- coding: utf-8 -*-
"""🐾 摸宠物的彩蛋文案（猫/狗/龟各 3 句随机）—— 纯 Python 自验。2026-11-07

恒：「我们的 pet 现在能够区分猫狗龟吗？可以的话我想加点小彩蛋文案，让摸宠物更有反馈：
返回结果"（宠物的名字）+看起来非常开心（通用）/叫了你一声（猫限定）/被摸成了一滩猫饼，直打呼噜（猫限定）/
扭了扭身子和尾巴（乌龟限定）/正在陆游（乌龟限定）/围着你跑来跑去（狗限定）/尾巴兴奋地拍打地板（狗限定），
也就是每个动物随机反馈三种文案中的一种。」

判据：
  ① 游戏得把种类报上来：`Pet.petType`（= `Data/Pets` 的键）⇒ C# `/surroundings` 的 `petType` 字段；
  ② `_pet_kind` 归一（Cat/Dog/Turtle/中文/空/乱码）；
  ③ 每种动物**恰好 3 句**（1 通用 + 2 本种），抽样能出全三句；狗那句带「的尾巴」才读得通；
  ④ 端到端：摸完**一只一行**，句子必须是**那一种**的；
  ⑤ 老 DLL 没报种类 ⇒ 只有通用句 + 一句"重开游戏上新 DLL"的提醒（不瞎猜是猫还是狗）。
"""
import os
import random
import sys

os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import nagi_mcp_server as M

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


try:
    print("\n① 种类归一：游戏报 `Cat`/`Dog`/`Turtle`（也认小写/中文）")
    for _raw, _exp in (("Cat", "cat"), ("cat", "cat"), ("猫", "cat"),
                       ("Dog", "dog"), ("dog", "dog"), ("狗", "dog"),
                       ("Turtle", "turtle"), ("turtle", "turtle"), ("乌龟", "turtle"),
                       ("", ""), (None, ""), ("Lizard", "")):
        ck(f"①{_raw!r} ⇒ {_exp!r}", M._pet_kind(_raw) == _exp, M._pet_kind(_raw))

    print("\n② 每种动物恰好 3 句（1 通用 + 2 本种），抽样三句都出得来")
    random.seed(7)
    for _kind in ("cat", "dog", "turtle"):
        _own = M._PET_FLAVOR.get(_kind, ())
        ck(f"②{_kind}：本种文案 2 句", len(_own) == 2, str(_own))
        _seen = {M._pet_flavor("海盐", _kind) for _ in range(120)}
        ck(f"②{_kind}：抽样能出全 3 句", len(_seen) == 3, str(sorted(_seen)))
        ck(f"②{_kind}：每句都带名字在前",
           all(s.startswith("海盐") for s in _seen), str(sorted(_seen))[:160])
    ck("②狗那句带「的尾巴」（名字+句子连起来读得通）",
       any(l.startswith("的尾巴") for l in M._PET_FLAVOR["dog"]), str(M._PET_FLAVOR["dog"]))
    ck("②通用句用的是恒给的原话", M._PET_GENERIC == "看起来非常开心", M._PET_GENERIC)

    print("\n③ 端到端：两只（猫 + 龟）⇒ 一只一行、句子必须是**那一种**的")
    class FakeApi:
        def __init__(self, pets):
            self.pets = pets
            self.interacts = 0

        def surroundings(self, *_a):
            return {"npcs": self.pets}

        def state(self):
            return {"location": {"name": "Farm"}}

        def walk_to_coord(self, *_a):
            return True

        def player_tile(self):
            return (5, 5)

        def position(self, *_a):
            return None

        def face(self, *_a):
            return {"ok": True}

        def _post(self, *_a, **_k):
            self.interacts += 1
            return {"ok": True}

    _bak = M.api
    M.api = FakeApi([{"name": "海盐", "x": 10, "y": 10, "kind": "pet", "petType": "Cat"},
                     {"name": "壳壳", "x": 12, "y": 10, "kind": "pet", "petType": "Turtle"}])
    _out = M._pet_pets_natural()
    ck("③摸了 2/2", "摸了 2/2" in _out, _out)
    ck("③两行（一只一行）", _out.count("· ") == 2, _out)
    _cat_lines = {f"海盐{s}" for s in (M._PET_GENERIC,) + M._PET_FLAVOR["cat"]}
    _tur_lines = {f"壳壳{s}" for s in (M._PET_GENERIC,) + M._PET_FLAVOR["turtle"]}
    _a = [ln.strip()[2:] for ln in _out.splitlines() if ln.strip().startswith("· ")]
    ck("③海盐那行必须是猫的 3 句之一", _a and _a[0] in _cat_lines, str(_a))
    ck("③壳壳那行必须是龟的 3 句之一", len(_a) > 1 and _a[1] in _tur_lines, str(_a))
    ck("③没有「没报种类」的提醒（种类报上来了）", "没报宠物种类" not in _out, _out)

    print("\n④ 老 DLL（没 petType）⇒ 只有通用句 + 明说「重开游戏上新 DLL」")
    M.api = FakeApi([{"name": "海盐", "x": 10, "y": 10, "kind": "pet"}])
    _out2 = M._pet_pets_natural()
    ck("④那行就是通用句", "海盐看起来非常开心" in _out2, _out2)
    ck("④并提醒「这版 DLL 没报宠物种类」", "没报宠物种类" in _out2, _out2)
    M.api = _bak

    print("\n⑤ 源码钉：种类是**游戏报的**（C# `Pet.petType`），不是我们猜的")
    _cs = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "ModEntry.cs"), encoding="utf-8").read()
    ck("⑤`/surroundings` 的 NPC 里**真的读** `Pet.petType`",
       "_pp.petType?.Value" in _cs and "petType = n is Pet" in _cs, "没找到 petType 字段")
    ck("⑤`/petbowl` 也带（摸碗那条路）", "type = (pet as Pet)?.petType?.Value" in _cs)
finally:
    pass

print()
if FAIL:
    print(f"❌ {len(FAIL)} 条没过：")
    for f in FAIL:
        print("   ·", f)
    sys.exit(1)
print("✅ 摸宠物：猫/狗/龟分得出来，每种 3 句随机，老 DLL 只给通用句并说明原因。")
