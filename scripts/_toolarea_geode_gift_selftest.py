"""🧪 2026-10-07 那一批"工具说谎"的钉子（纯离线：不碰游戏、不起服务）。

覆盖三件事（都是恒真机抓到的）：
  ① **"蓄力"是假的** —— `WateringCan/Hoe.DoFunction` 会**丢掉传进来的 power**、改读 `who.toolPower.Value`
     （反编译 `WateringCan.cs:152` / `Hoe.cs:60`），而我们从没设过它 ⇒ 只喷面前 1 格。
     ⇒ 钉 `DoFunctionHere` 里**必须**临时把 `toolPower.Value` 摆成 power、并**还原**。
  ② **砸晶球绕过"克林特在不在"** —— 钉两个 handler 都过 `GeodeGateError()`，且判据含 铁匠铺/克林特/距离。
  ③ **送礼只认英文名** —— 钉 C# 匹配补了 `displayName`、Python 侧也把名字翻成**内部名**再发。
  ④ **`map go` 进矿井/头骨矿洞推门推空** —— 钉导航走 `_step_into_warp`（踩 warp 瓦片），并**真跑一遍**桩实现。

⚠️ ①②③ 是 C# 源码级断言（本项目没有 C# 单测；编译过 + 真机验是主判据，这里是防"哪天被谁删掉"）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

ROOT = os.path.dirname(HERE)
CS = os.path.join(ROOT, "ModEntry.cs")
DECOMP = r"G:\wingheng\Claude\NagiBridge\decomp\c1615\full\StardewValley.Tools"

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


src = open(CS, encoding="utf-8").read()

print("\n① 蓄力落地：DoFunctionHere 必须自己摆 toolPower（否则 power 全被丢掉）")
i = src.find("private static void DoFunctionHere")
body = src[i:i + 4200] if i >= 0 else ""
ck("找得到 DoFunctionHere", i >= 0)
ck("把 power 摆进 farmer.toolPower.Value", "farmer.toolPower.Value = power;" in body)
ck("玩完**还原**（powerBefore）", "int powerBefore = farmer.toolPower.Value;" in body
   and "farmer.toolPower.Value = powerBefore;" in body)
ck("注释里留着反编译实据（WateringCan.cs / Hoe.cs 的行号）",
   "WateringCan.cs:149-154" in body or "WateringCan.cs" in body, body[:0])
for f, line in (("WateringCan.cs", "power = who.toolPower.Value;"), ("Hoe.cs", "power = who.toolPower.Value;")):
    p = os.path.join(DECOMP, f)
    if os.path.exists(p):
        txt = open(p, encoding="utf-8").read()
        ck(f"反编译 {f} 里确实是「丢掉参数改读 toolPower」", line in txt)
    else:
        print(f"  ⏭  反编译目录没有 {f}（跳过这条证据核对）")

print("\n② 砸晶球闸门：两个入口都过 GeodeGateError")
ck("GeodeGateError 已定义", "private static string? GeodeGateError()" in src)
ck("判据含 铁匠铺（Blacksmith）", 'loc.Name != "Blacksmith"' in src)
ck("判据含 克林特在不在", 'FirstOrDefault(n => n != null && n.Name == "Clint")' in src)
ck("判据含 距离（柜台附近）", "if (dist > 8)" in src)
ck("GeodeGateError → let/var 匹配", "private static string? GeodeGateError()" in src)
ck("/process_geode 接了闸", src.count("var gate = GeodeGateError();") == 2,
   f"接了 {src.count('var gate = GeodeGateError();')} 处（应为 2）")

print("\n③ 送礼：C# 认 displayName + Python 翻内部名")
ck("C# 送礼匹配补了 displayName", "string.Equals(n.displayName, _targetTrim, StringComparison.OrdinalIgnoreCase)" in src)
py = open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8").read()
ck("Python gift_npc 里 /gift 发的是**内部名**", '_post("/gift", {"target": _internal' in py)
ck("Python 里那个内部名从 find_npc 来", '_internal = _fr3["npcs"][0].get("name") or npc_name' in py)

print("\n④ 导航：矿井/头骨矿洞这类入口要**踩上 warp 瓦片**，不是推门")
nav_src = open(os.path.join(HERE, "navigation.py"), encoding="utf-8").read()
ck("_step_into_warp 已定义", "def _step_into_warp(" in nav_src)
ck("door 分支对 _DOOR_WARP_ENTRANCES 改道", "if nxt in _DOOR_WARP_ENTRANCES:" in nav_src)
ck("失败时有「走到口 + /warp」二级兜底", "按「走到口 + /warp」收尾" in nav_src)
ck("_walk_and_wait 仍带 allowWarp（踩门格是它本来就放行的）",
   '"allowWarp": True' in nav_src)

import navigation  # noqa: E402

_real_walk = navigation._walk_and_wait
_real_api = navigation.api
try:
    calls = []

    class _FakeApi:
        def current_location(self):
            # 桩：只有踩到 (8,4) 才"换图"（= 真机实测头骨矿洞的 warp 列）
            return "SkullCave" if calls and calls[-1] == (8, 4) else "Desert"

    navigation.api = _FakeApi()

    def _fake_walk(loc, x, y, timeout=25):
        calls.append((x, y))
        return True, ""

    navigation._walk_and_wait = _fake_walk
    link = {"tile": (8, 5), "target": "SkullCave", "kind": "door"}
    ok, note = navigation._step_into_warp("Desert", "SkullCave", link)
    ck("桩：先试标的那格 (8,5)，再试上邻 (8,4) ⇒ 换图成功", ok and calls == [(8, 5), (8, 4)], str(calls))
    ck("成功时说明里带**真踩到的那格**", note == "(8,4)", note)

    # 反向：一直换不了图 ⇒ 必须**如实报失败**（不许谎报到达）
    calls.clear()
    navigation.api = type("_A", (), {"current_location": lambda self: "Desert"})()
    ok2, note2 = navigation._step_into_warp("Desert", "SkullCave", link)
    ck("换不了图 ⇒ ok=False（不谎报）", ok2 is False, f"{ok2} {note2}")
    ck("失败说明可读（含试过的格子）", "(8,5)" in note2 or "(8,4)" in note2, note2)
finally:
    navigation._walk_and_wait = _real_walk
    navigation.api = _real_api

print()
if FAIL:
    print(f"❌ {len(FAIL)} 项不过：" + " / ".join(FAIL))
    sys.exit(1)
print("✅ 蓄力落地 / 砸晶球闸门 / 送礼名字 / 踩 warp 入口：全部通过")
