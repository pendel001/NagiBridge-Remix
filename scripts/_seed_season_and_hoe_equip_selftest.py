"""🌱⚙️ 恒 2026-10-07 真机演示后那两条的钉子（纯离线：不碰游戏、不起服务）。

恒的原话（演示收工）：
  「**唯一说得上问题的应该是**……锄头没跳设备，一把把熔炉全部拍下来了。
    还有优化一下箱子里**当季的种子**给标一下当季可种或者春夏秋冬吧」

① **锄头的蓄力范围会连设备一起收走**（真机凭据：`farm("clear till plant")` 6×4 那次收工后
   背包里多了 2 台熔炉；反编译 `Hoe.cs:66-85` + `Object.cs:1350`）。
   ⇒ 钉 C# 侧：`HoeWouldPickUp` 判据逐条对齐游戏分支 + `BuildToolAreaCommands` 里
   "满级脏 ⇒ 逐级降 power / 都脏 ⇒ 整个锚点不发" + 回包把让开的格如实带出去。
② **箱子里的种子打季节标**（读游戏 `Game1.cropData[...].Seasons`，不手抄表）
   ⇒ 钉 C# `/crop_seasons` 端点 + Python `_seed_season_tag` 的四种输出 + 老 DLL 不打标也不报错。

⚠️ ① 是 C# 源码级断言（本项目没有 C# 单测；编译过 + 真机验是主判据，这里防"哪天被谁删掉"）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("NAGI_URL", "http://localhost:7843")

ROOT = os.path.dirname(HERE)
CS = os.path.join(ROOT, "ModEntry.cs")
DECOMP = r"G:\wingheng\Claude\NagiBridge\decomp\c1615\full"

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


src = open(CS, encoding="utf-8").read()

print("\n① 锄头蓄力：**波及格**也要筛（只筛目标格 = 熔炉被一起收走）")
i = src.find("private static bool HoeWouldPickUp")
body = src[i:src.find("private static string HoePickUpLabel", i)] if i >= 0 else ""
ck("找得到 HoeWouldPickUp", i >= 0)
ck("杂物（Category == -999：杂草/树枝/石头）⇒ 不拦", "o.Category == -999" in body)
ck("斑点（锄头正是去挖它）⇒ 不拦", "IsDiggableSpot(o)" in body)
ck("洒水器 ⇒ 不拦（游戏专门豁免锄头）", "o.IsSprinkler()" in body)
ck("fragility == 2 ⇒ 不拦（游戏自己也不收）", "o.Fragility == 2" in body)
ck("剩下 `Type == \"Crafting\"` / bigCraftable ⇒ 拦", 'return o.Type == "Crafting" || o.bigCraftable.Value;' in body)

j = src.find("private static List<Dictionary<string, object?>> BuildToolAreaCommands")
b2 = src[j:src.find("/// <summary>主线程自检漏格", j)] if j >= 0 else ""
ck("锚点生成收到了 loc/operation（不筛就没法筛）", "GameLocation? loc, string operation" in b2)
ck("满级脏 ⇒ 先算脏格", "HoeWouldPickUp(loc, t.Item1, t.Item2)" in b2)
ck("🚫 **改成「改站位重挥」**（恒真机「第一拍一列，第二拍绝对不止三列」：降档 = 动画和效果对不上）",
   "anchorsMovedByEquip++" in b2 and "GetToolAffectedTiles(c.x, c.y, 2, upgradeLevel)" in b2)
ck("⛔ **不许**把「逐级降 power」重新加回来", "for (int pw = upgradeLevel - 1; pw >= 0; pw--)" not in b2)
ck("候选顺序：原位 → 左右 → 上下 → 左上/右上（先横后竖）",
   "(ax, ay), (ax - 1, ay), (ax + 1, ay), (ax, ay - 1), (ax, ay + 1)," in b2)
ck("候选的满级范围必须**干净**才在那儿挥", "if (aoe.Exists(t => HoeWouldPickUp(loc, t.Item1, t.Item2))) continue;" in b2)
ck("候选还要**盖得到至少一格真目标**", "if (!hit.Any(t => aoe.Contains(t))) continue;" in b2)
ck("charge 的 power **永远满级**（动画=效果）", "int usePower = upgradeLevel;      // ⚠️ 永远满级" in b2)
ck("**站位格自己**压着设备 ⇒ 换候选（不硬站、不靠瞬移保底）",
   "if (HoeWouldPickUp(loc, c.x, c.y)) continue;" in b2)
ck("四个方向都躲不开 ⇒ 整条不发（如实记账）", "anchorsSkippedByEquip++;" in b2 and "if (!placed)" in b2)
ck("挪站位/跳过**都要记账**（回执要说出让开了哪些设备）",
   "foreach (var d in dirtyFull)" in b2 and "equipAvoided.Add((d.Item1, d.Item2, lbl));" in b2
   and b2.find("foreach (var d in dirtyFull)") < b2.find("if (!placed)"))
ck("🚨 空队列不许挂在 `Wait(10 分钟)`（真机：客户端 180s 超时、地里一格没动）",
   "if (commands.Count == 0)" in src and "CompleteCommandQueue();" in src
   and src.find("if (commands.Count == 0)") > src.find("_toolAreaTotalSwings = commands.Count / 3;"))
ck("都脏 ⇒ 整个锚点不发（continue，不发 move/face/charge）", "anchorsSkippedByEquip++;" in b2 and "continue;" in b2)
ck("回包带 equipment_avoided", '["equipment_avoided"] = _toolAreaEquipAvoided' in src)
ck("回包带两个计数（跳过 / 挪站位）",
   '["anchors_skipped_by_equipment"] = _toolAreaAnchorsSkipped' in src
   and '["anchors_moved_by_equipment"] = _toolAreaAnchorsMoved' in src
   and "anchors_downgraded_by_equipment" not in src)
ck("每轮开工先清账（别把上一轮让开的格报到这一轮）",
   "_toolAreaEquipAvoided = new List<(int x, int y, string what)>();" in src)

# 反编译实据：这三条是判据的出处，哪天游戏/我们的判断变了要能立刻对回来
hoe = os.path.join(DECOMP, "StardewValley.Tools", "Hoe.cs")
obj = os.path.join(DECOMP, "StardewValley", "Object.cs")
if os.path.exists(hoe) and os.path.exists(obj):
    htxt = open(hoe, encoding="utf-8").read()
    otxt = open(obj, encoding="utf-8").read()
    ck("反编译 Hoe.cs：波及格逐格 `performToolAction`（不是只打目标格）",
       "foreach (Vector2 item in list)" in htxt and "value2.performToolAction(this)" in htxt)
    ck("反编译 Object.cs：`Type == \"Crafting\"` + heavy hitter ⇒ 被收走",
       'if (Type == "Crafting" && !(t is MeleeWeapon) && t.isHeavyHitter())' in otxt)
    ck("反编译 Object.cs：洒水器对锄头豁免（所以我们也不拦它）",
       "if (t is Hoe && IsSprinkler())" in otxt)
else:
    print("  ⏭  反编译目录不在（跳过证据核对那三条）")

print("\n② 箱子里的种子标季节：**问游戏**（`Game1.cropData[...].Seasons`），不手抄表")
ck("端点 `/crop_seasons` 已注册", '"/crop_seasons" => HandleCropSeasons()' in src)
ck("读的是游戏数据 Game1.cropData", "foreach (var kv in Game1.cropData)" in src)
ck("读的是 CropData.Seasons", "kv.Value?.Seasons" in src)
ck("季节名小写化（和 Python 侧同一套 key）", 's.ToString().ToLowerInvariant()' in src)
ck("caps 里声明了 crop_seasons（老 DLL 消费侧据此不打标）", '["crop_seasons"] = true,' in src)

import nagi_mcp_server as M  # noqa: E402  （import 安全：末尾才有 __main__ 守卫）

print("\n③ `_seed_season_tag`：当季 ✅ / 非当季 / 跨季 / 非种子")
FIXTURE = {"(O)472": ["spring"],            # 防风草种子
           "(O)481": ["summer"],            # 蓝莓种子
           "(O)490": ["spring", "summer"],  # 南瓜种子（造的跨季夹具，只为验格式）
           "(O)499": ["spring", "summer", "fall"]}


class FakeApi:
    def __init__(self):
        self.season = "summer"
        self.seasons_ok = True
        self.calls = 0

    def _get(self, path, params=None):
        if path == "/crop_seasons":
            self.calls += 1
            if not self.seasons_ok:
                return {"ok": False, "error": "404"}
            return {"ok": True, "count": len(FIXTURE), "seasons": FIXTURE}
        return {}

    def state(self, consume_events=False, light=False):
        return {"time": {"season": self.season}, "player": {}, "location": {"name": "Farm"}}


_api = FakeApi()
_real_api = M.api
M.api = _api
try:
    M._SEED_SEASON_CACHE.update({"ts": 0.0, "map": {}, "ok": False})
    ck("当季种子 ⇒ ☀️夏 + ✅当季可种",
       M._seed_season_tag("(O)481") == " ☀️夏 ✅当季可种", repr(M._seed_season_tag("(O)481")))
    ck("非当季 ⇒ 只报季节、**不打** ✅",
       M._seed_season_tag("(O)472") == " 🌸春", repr(M._seed_season_tag("(O)472")))
    ck("跨季 ⇒ 两个都报，当季那个也在 ⇒ 带 ✅",
       M._seed_season_tag("(O)490") == " 🌸春·☀️夏 ✅当季可种", repr(M._seed_season_tag("(O)490")))
    ck("三季种子 ⇒ 春·夏·秋（当季在后也认）",
       M._seed_season_tag("(O)499") == " 🌸春·☀️夏·🍂秋 ✅当季可种", repr(M._seed_season_tag("(O)499")))
    ck("不是种子（表里没有）⇒ 空串", M._seed_season_tag("(O)390") == "", repr(M._seed_season_tag("(O)390")))
    ck("空/None ⇒ 空串不炸", M._seed_season_tag("") == "" and M._seed_season_tag(None) == "")

    _api.season = "winter"
    ck("冬天：夏季种子 ⇒ ☀️夏（无 ✅）", M._seed_season_tag("(O)481") == " ☀️夏",
       repr(M._seed_season_tag("(O)481")))

    _api.season = ""
    ck("读不到季节 ⇒ **一个 ✅ 都不打**（宁缺勿编）",
       "✅" not in M._seed_season_tag("(O)481"), repr(M._seed_season_tag("(O)481")))

    _api.season = "summer"
    _api.seasons_ok = False
    M._SEED_SEASON_CACHE.update({"ts": 0.0, "map": {}, "ok": False})
    ck("老 DLL / 端点挂了 ⇒ 空串、不抛", M._seed_season_tag("(O)481") == "")
    ck("…而且**没有**把空表缓存成'问过了'（下次还会再问）", M._SEED_SEASON_CACHE["ok"] is False)
finally:
    M.api = _real_api
    M._SEED_SEASON_CACHE.update({"ts": 0.0, "map": {}, "ok": False})

print("\n④ `scan_chests` 单箱页：种子后面挂季节标")
_cur = open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8").read()
ck("单箱页用了 `_seed_season_tag`", "_seed_season_tag(i.get('qualifiedId'))" in _cur)
ck("一览前 5 条也标", "_seed_season_tag(i.get('qualifiedId'))" in _cur and "items[:5]" in _cur)
ck("带一句'季节标 = 露天能不能种'（温室/姜岛全年）", "露天" in _cur)
ck("⛔ 没在 Python 里手抄种子→季节表（只有 ICON 那张表情表）",
   "SEED_SEASON_TABLE" not in _cur and "_SEASON_ICON_CN" in _cur)


class ChestApi(FakeApi):
    """/scan_chests 夹具：一格蓝莓种子（当季）+ 一格石头（非种子）。"""

    def _get(self, path, params=None):
        if path == "/scan_chests":
            return {"ok": True, "location": "Farm", "count": 1, "chests": [
                {"x": 74, "y": 15, "used": 2, "capacity": 36, "name": "", "autoTag": "",
                 "typeName": "Chest", "color": "", "items": [
                     {"name": "蓝莓种子", "count": 5, "qualifiedId": "(O)481", "slot": 0, "quality": 0},
                     {"name": "石头", "count": 45, "qualifiedId": "(O)390", "slot": 1, "quality": 0}]}]}
        return super()._get(path, params)


_bak2 = M.api
_ch = ChestApi()
M.api = _ch
try:
    _ch.season = "summer"
    M._SEED_SEASON_CACHE.update({"ts": 0.0, "map": {}, "ok": False})
    page = M.scan_chests(0)
    ck("单箱页：蓝莓种子后面挂「☀️夏 ✅当季可种」",
       "蓝莓种子x5 ☀️夏 ✅当季可种" in page, page[:400])
    ck("单箱页：石头**不**挂标", "石头x45 " not in page, page[:400])
    ck("单箱页：带「露天」那句口径提示", "露天" in page, page[:400])
    _ch.season = "winter"
    M._SEED_SEASON_CACHE.update({"ts": 0.0, "map": {}, "ok": False})
    page_w = M.scan_chests(0)
    ck("冬天看同一口箱子：只报「☀️夏」、没有 ✅", "蓝莓种子x5 ☀️夏" in page_w and "✅" not in page_w, page_w[:400])
    summ = M.scan_chests(-1)
    ck("一览模式也给标（前 5 条内）", "蓝莓种子x5 ☀️夏" in summ, summ[:400])
finally:
    M.api = _bak2
    M._SEED_SEASON_CACHE.update({"ts": 0.0, "map": {}, "ok": False})

print("\n⑤ `_till_rect`：让开设备要**如实报**（老 DLL 没这键 ⇒ 少一行，不骗一行）")


class TillApi(FakeApi):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload
        self.posts = []

    def state(self, consume_events=False, light=False):
        return {"player": {"currentTool": "Iridium Hoe", "currentToolUpgrade": 4},
                "inventory": [{"name": "Iridium Hoe"}],
                "time": {"season": "summer"}, "location": {"name": "Farm"}}

    def select(self, *_a, **_k):
        return {"ok": True}

    def _post(self, path, body=None, timeout=None):
        self.posts.append((path, body))
        return self.payload


_old = M._stamina_now
M._stamina_now = lambda: (270, 270)
try:
    payload_new = {"ok": True, "operation": "till", "swings": 8, "patches": 3,
                   "still_missing": [], "out_of_water": False,
                   "equipment_avoided": [{"x": 60, "y": 24, "what": "熔炉"},
                                         {"x": 63, "y": 24, "what": "重型熔炉"}],
                   "anchors_skipped_by_equipment": 2, "anchors_moved_by_equipment": 1,
                   "result": {"executed": 8, "results": []}}
    _bak = M.api
    M.api = TillApi(payload_new)
    _out = M._till_rect(58, 22, 63, 25)
    ck("报出「让开设备 2 处」", "让开设备 2 处" in _out, _out[:400])
    ck("报出设备坐标+名字", "(60,24)" in _out and "熔炉" in _out, _out[:400])
    ck("报出跳过锚点 2 / **挪站位** 1", "跳过锚点 2" in _out and "挪站位 1" in _out, _out[:400])
    ck("明说「那些东西一根没动」", "一根没动" in _out, _out[:400])
    ck("明说改站位那几挥**照旧满级**（动画不变）", "照旧满级" in _out, _out[:400])

    payload_old = {"ok": True, "operation": "till", "swings": 8, "patches": 3,
                   "still_missing": [], "out_of_water": False, "result": {"executed": 8, "results": []}}
    M.api = TillApi(payload_old)
    _out2 = M._till_rect(58, 22, 63, 25)
    ck("老 DLL（没这键）⇒ **不出现**那行（少一行而不是编一行）",
       "让开设备" not in _out2, _out2[:300])
    ck("老 DLL 的常规回执照旧", "tool_area 完成" in _out2, _out2[:300])
finally:
    M.api = _bak if '_bak' in dir() else _real_api
    M._stamina_now = _old

print("\n⑥ 蓄力落地/验证的时序（恒 2026-10-07：「斜右下格在抬手就被改」「没在范围的格子也被锄到」）")
# 两条旧账：① 先 DoFunction（土立刻变）再 EndUsingTool（才起手挥）② `charge` 是异步命令，
# 而逐锚点验证在 **dispatch 那一拍**就跑了 ⇒ 还没落地就把 targets 全补成土（比抬手还早）。
ck("`charge` 命令**不再**在 dispatch 时验证（改成存起来）",
   'else if (action == "charge") _pendingChargeCmd = cmd;' in src
   and 'if (action == "use" || action == "charge")' not in src)
ck("待验证的蓄力命令有地方存（字段 `_pendingChargeCmd`）",
   "private Dictionary<string, object?>? _pendingChargeCmd;" in src)
ck("落地挪进 `DelayedAction`（挥下去那一帧 ≈200ms）",
   "DelayedAction.functionAfterDelay(() =>" in src and "}, 200);" in src)
ck("旧的「释放即落地」写法已删（先 DoFunction 再 EndUsingTool）",
   "if (_chargeOp == \"till\" && tool is Hoe hoe2)" not in src)
ck("按掉标记在 `EndUsingTool()` **之前**挂（动画自己 frame68 那发照旧按掉）",
   src.find("_suppressAnimToolUse = true;\n                            _suppressAnimTicks = 90;\n                            farmer.EndUsingTool();") > 0)
ck("队列结账搬进回调（否则最后一锚点会在落地前结账、补漏把整片土直接写上）",
   "if (_commandQueue == null || _commandQueue.Count == 0) CompleteCommandQueue();\n                                }\n                            }, 200);" in src)
ck("动画超时兜底也能结账（DelayedAction 万一没跑到，别让 HTTP 悬 10 分钟）",
   "if (_commandQueue != null && _commandQueue.Count == 0) CompleteCommandQueue();" in src)
ck("降档时 charge 的 targets **只报这一挥真盖得到的**（盖不到的留给收工补漏）",
   "var chargeTargets = (loc != null && operation == \"till\"" in src and '["targets"] = chargeTargets' in src)

print(f"\n{0 if FAIL else 1} 组结论：{'全部通过' if not FAIL else '有失败'}  （{len(FAIL)} 条未过）")
if FAIL:
    print("  未过：" + " / ".join(FAIL))
sys.exit(0 if not FAIL else 1)
