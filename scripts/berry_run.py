"""
🍓 berry_run.py — 摇/摘当前场景的灌木与果树（拟人：走过去摇，不是隔空）

原理（2026-08-17 恒+克劳德实测，2026-10-02 补全）：
- 灌木 Bush 在 GameLocation.largeTerrainFeatures（不在 terrainFeatures），surroundings 报 terrain="Bush"
- 摇出来什么由**游戏的 `Bush.GetShakeOffItem()`** 定（反编译 `Bush.cs:460`）：
    size 0/1/2 → 春树莓 `(O)296` / 秋黑莓 `(O)410` / **其它季节 null（摇不出）**
    size 3     → **茶叶 `(O)815`**   ← 恒 2026-10-02：「茶树也值得摇」
    size 4     → **金核桃 `(O)73`**（存档计数，不进背包）
- 🎯 "现在摇得出吗" = 游戏 `Bush.shake()` 的原条件 `!townBush && readyForHarvest() && inBloom()`
  ⇒ C# 直接把这个答案报成 `bushShakeable`（老 DLL 才退回 `bushBloom`+`bushInSeason`/日历窗口）
- 🍎 **果树**（`FruitTree`，另一类地形）也是摇，但 `FruitTree.shake()` 把果子变成**地上 Debris**
  （`FruitTree.cs:361-425`）⇒ **摇完得再走上去捡**（本脚本摇完补了那一段）。
  真机 2026-10-02（温室）：`scene at 12 7` → 地上 3×Banana → 走到 (12,8) → 背包 Banana×3
- 摇 = checkAction（interact）；拟人 = 先走到相邻格再面朝它

流程：扫 `surroundings` 挑出该摇的 → 逐个走过去+面朝+interact → 重扫直到没有 → 把掉地上的走上去捡。

用法:
  python berry_run.py                    # 摇当前场景该摇的全部
  python berry_run.py --port 7842        # 指定端口（solo 恒在 7842）
  python berry_run.py --dry-run          # 只报有几处该摇，不摇
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[berry] 摇当前场景结果浆果灌木")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--radius", type=int, default=30, help="扫描半径（默认30，覆盖整图小图）")
parser.add_argument("--rounds", type=int, default=4, help="重扫轮数上限（防漏，默认4）")
parser.add_argument("--dry-run", action="store_true", help="只扫不摇")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests
# 🍓 浆果窗口的判据在 `calendar_data`（**只此一处**：服务器显示侧 + 这儿的执行侧共用）。
#    本脚本**不能** import `nagi_mcp_server`（那会把 MCP 服务器起起来），所以窗口放数据模块里。
import calendar_data

NAGI = os.environ["NAGI_URL"]


def log(msg):
    try:
        print(f"[berry] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[berry] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def main():
    try:
        st = requests.get(f"{NAGI}/status", timeout=5).json()
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)
    if not st.get("worldReady"):
        log("❌ 游戏未就绪")
        sys.exit(1)

    base = NAGI

    def scan_targets():
        """扫当前场景**该摇的**目标 → `([{x,y,kind},…], 图名, 全部瓦片)`。

        ⚠️ 2026-10-02 一天之内这条判据升了三级（**每级都是"别自己猜，问游戏"**）：
          ① 老口径：`bushBloom` + `calendar_data` 的浆果窗口（**猜**：会把茶树丛/夏天残留帧当浆果）；
          ② `bushSize` + `bushInSeason`（C# 报 `Bush.size` / `Bush.inBloom()`）；
          ③ `bushShakeable` = 游戏 `Bush.shake()` 的**原条件**（`!townBush && readyForHarvest() && inBloom()`）
             —— 有它就用它（连"Town 装饰丛不给东西"这种只有游戏知道的事都算进去了）。
        🍎 果树（`FruitTree`）是**另一类地形**、同一件事：`fruitCount>0` ⇒ 摇它果子**掉地上**、再走上去捡
           （真机 2026-10-02 温室：`scene at 12 7` → 地上 3×Banana → 走上去 → 背包 Banana×3）。
        🍵 茶树丛(size3) 摇出来是**茶叶(O)815**（同一份反编译 `GetShakeOffItem()`）⇒ **也摇**（恒：「茶树也值得摇」）。
        ⚠️ 老 DLL 没这些键 ⇒ 退回 ①（那段退化**如实标着**，不是"也能用"）。
        """
        try:
            d = requests.get(f"{base}/surroundings", params={"radius": args.radius}, timeout=10).json()
        except Exception:
            return [], "", []
        loc = d.get("location", "?")
        tiles = d.get("tiles", [])
        out = []
        for t in tiles:
            _terr = t.get("terrain") or ""
            if _terr == "FruitTree":
                if int(t.get("fruitCount") or 0) > 0:          # 🍎 挂果了（新 DLL 才有这个字段）
                    out.append({"x": t["x"], "y": t["y"], "kind": "fruit"})
                continue
            if _terr != "Bush" or not t.get("bushBloom"):
                continue
            _sz = t.get("bushSize")
            if _sz is None:                                    # 老 DLL：帧 + 日历窗口（会误摇，见 docstring）
                if _in_season:
                    out.append({"x": t["x"], "y": t["y"], "kind": "berry"})
                continue
            _kind = {3: "tea", 4: "walnut"}.get(int(_sz), "berry")
            if "bushShakeable" in t:
                _ok = bool(t.get("bushShakeable"))
            else:
                _ok = bool(t.get("bushBloom")) and bool(t.get("bushInSeason", _in_season))
            if not _ok:
                continue
            if _kind == "walnut" and not _walnut_mode:
                continue                                       # 核桃丛只在"本图还有挂着的"时摇
            out.append({"x": t["x"], "y": t["y"], "kind": _kind})
        return out, loc, tiles

    def find_stand(bx, by, tiles):
        """找目标相邻可走格（优先下方），避开其他灌木/树/果树。"""
        cands = [(bx, by + 1), (bx + 1, by), (bx, by - 1), (bx - 1, by)]
        blocked = {(bx, by)}
        for t in tiles:
            if t.get("terrain") in ("Bush", "FruitTree", "Tree:0", "Tree:1", "Tree:2", "Tree:3",
                                    "Tree:5", "Tree:6", "Tree:7", "Tree:8", "Tree:9", "Tree:10"):
                blocked.add((t["x"], t["y"]))
        passable = {(t["x"], t["y"]) for t in tiles if t.get("passable", True)}
        for c in cands:
            if c in passable and c not in blocked:
                return c
        return cands[0]

    def walk_near(x, y, timeout=20):
        """walk_to 目标格并按位置等到达。"""
        try:
            loc = requests.get(f"{base}/state", timeout=10).json().get("location", {})
            loc_name = loc.get("name") if isinstance(loc, dict) else loc
            r = requests.post(f"{base}/walk_to", json={"location": loc_name, "x": x, "y": y}, timeout=10)
            if not r.json().get("ok"):
                return False
            deadline = time.time() + timeout
            while time.time() < deadline:
                p = requests.get(f"{base}/state", timeout=10).json().get("player", {})
                if abs(p.get("x", 0) - x) <= 1 and abs(p.get("y", 0) - y) <= 1:
                    time.sleep(0.3)
                    return True
                time.sleep(0.25)
            return False
        except Exception:
            return False

    def face_and_shake(bx, by):
        """站格朝灌木方向，interact 摇。"""
        s = requests.get(f"{base}/state", timeout=10).json()
        px, py = s.get("player", {}).get("x", 0), s.get("player", {}).get("y", 0)
        if py > by: direction = 0   # 站下方 → 朝上
        elif py < by: direction = 2
        elif px < bx: direction = 1
        else: direction = 3
        requests.post(f"{base}/face", json={"direction": direction}, timeout=10)
        time.sleep(0.2)
        requests.post(f"{base}/interact", {}, timeout=10)   # 无参数=面前格=灌木
        time.sleep(0.6)

    # 🍓🍓 2026-10-02 真机（恒：「**看起来有些不会长树莓的树丛也摇摇了！**」）：
    #    `bushBloom` = 游戏那个 `Bush.tileSheetOffset == 1`，意思是"**贴图切到第 1 帧**"，
    #    **不等于"这丛有果子"**——山地实测：7 棵 `bushBloom=True`，摇了 6 棵，
    #    **背包一件都没多**、摇完那几棵 `bushBloom` 还是 true。
    #    ⚠️ 当晚先收成"只认**浆果窗口**"（春15~18 / 秋8~11，判据在 `calendar_data`）；
    #    ⚠️ 2026-10-02 深夜 **C# 补了 `bushSize`/`bushInSeason`/`bushShakeable`** ⇒ **判据升级成问游戏**
    #       （见 `scan_targets`）：有 `bushShakeable` 就用它（= 游戏 `Bush.shake()` 的原条件），
    #       没有就 `bushBloom && bushInSeason`。日历窗口只在**老 DLL**（连 `bushSize` 都没）时兜底。
    #    🍵 茶树丛(size3) 摇出来是**茶叶(O)815**（反编译 `GetShakeOffItem()`，跟浆果**同一次动作**）
    #       ⇒ **也摇**（恒 2026-10-02：「**茶树也值得摇**，不过确实不是同一件事」）。
    #    🍎 果树（`FruitTree`）也是"摇"，但**果子掉地上**（得走上去捡），见 `scan_targets` 的 docstring。
    #    🌰 例外（恒 2026-10-02 拍板）：「**姜岛地图摇晃树丛可以一直放行，直到当前图的金核桃
    #       都被摇掉了**」——姜岛的"核桃丛"也是 `Bush`（`size==4`），**同一个 `tileSheetOffset` 字段**。
    #       ⚠️ 判据**不靠地图名**（那又是一张会烂的名单），直接问游戏：`/nuts` 里还有没有
    #       `kind=="bush"` 且没拿走的 ⇒ 有就一直放行，摇到没有为止（摇完 offset 变 0、重扫自然没了）。
    #    ⚠️ 顺序：**先把"在不在季 / 有没有核桃"问清楚，再扫**（`scan_targets` 里要用这两个状态）。
    try:
        _st = requests.get(f"{base}/state", timeout=10).json()
        _t = _st.get("time") or {}
    except Exception:
        _t = {}
    _in_season = calendar_data.in_berry_season(_t.get("season"), _t.get("dayOfMonth"))

    def bush_nuts_left():
        """本图**还挂在树丛上**的金核桃：`/nuts` 里 `kind=="bush"` 且 `taken` 为假。读不到返回 None。"""
        try:
            nn = requests.get(f"{base}/nuts", timeout=10).json() or {}
        except Exception:
            return None
        return [n for n in (nn.get("nuts") or [])
                if n.get("kind") == "bush" and not n.get("taken")]

    _nuts0 = bush_nuts_left()
    _walnut_mode = bool(_nuts0)

    # ── 主循环：扫→逐个摇→重扫 ──
    targets, loc, _tiles0 = scan_targets()
    if not targets:
        # ⚠️ 没该摇的也要**说清"为什么没有"**（原来只会说"没有结果的灌木"）：
        #    本图可能有"贴图还亮着"的丛，但游戏说摇不出东西（老 DLL 甚至分不出是哪一丛）。
        _raw = len([t for t in _tiles0
                    if t.get("terrain") == "Bush" and t.get("bushBloom")])
        _frt = len([t for t in _tiles0
                    if t.get("terrain") == "FruitTree" and int(t.get("fruitCount") or 0) > 0])
        if _raw or _frt:
            log(f"🌿 {loc}：贴图是「有货」那帧的灌木 {_raw} 丛、挂着熟果的果树 {_frt} 棵 —— "
                f"但**游戏说现在摇不出东西**（不在浆果季 / 茶叶还没到时候 / 果树已摘过）⇒ **不摇**")
        else:
            log(f"🎉 {loc or '当前场景'}没有该摇的东西")
        return

    if _walnut_mode:
        log(f"🌰 {loc} 有 {len(_nuts0)} 个**挂着的金核桃丛**{'(顺带也在浆果季)' if _in_season else ''}"
            f" —— 姜岛的树丛一直放行，摇到本图摇干净为止。")

    _KIND_CN = {"berry": "浆果丛", "tea": "茶树丛", "walnut": "核桃丛", "fruit": "果树"}
    _kinds = {}
    for _t in targets:
        _kinds[_t["kind"]] = _kinds.get(_t["kind"], 0) + 1
    log(f"🍓 {loc} 该摇 {len(targets)} 处：" + "、".join(f"{_KIND_CN.get(k, k)}×{v}"
                                                     for k, v in sorted(_kinds.items())))

    if args.dry_run:
        log("--dry-run：不摇")
        return

    def _inv_counts():
        """背包按名字计数 —— 用来**回读"到底摇到没有"**（别只说"按键发出去了"）。"""
        try:
            inv = requests.get(f"{base}/state", timeout=10).json().get("inventory") or []
        except Exception:
            return None
        c = {}
        for i in inv:
            if i.get("name"):
                c[i["name"]] = c.get(i["name"], 0) + int(i.get("stack") or 0)
        return c

    _before = _inv_counts()

    total = 0
    shaken = set()
    for _ in range(args.rounds):
        targets, loc, _tl = scan_targets()
        if not targets:
            break
        tiles = _tl
        for _tg in targets:
            bx, by, _kd = _tg["x"], _tg["y"], _tg.get("kind")
            if (bx, by) in shaken:
                continue
            stand = find_stand(bx, by, tiles)
            log(f"  · 摇 {_KIND_CN.get(_kd, _kd)} ({bx},{by}) 站 {stand}")
            if not walk_near(*stand):
                log(f"    ⚠️ 走不到 {stand}，跳过")
                continue
            face_and_shake(bx, by)
            shaken.add((bx, by))
            total += 1
        time.sleep(0.5)

    # 🍎 **果子是掉在地上的**（`FruitTree.shake()` 逐颗 `Location.debris.Add` + `fruit.Clear()`，
    #    反编译 `FruitTree.cs:361-425`）⇒ 摇完必须**走上去捡**（真机 2026-10-02 温室：
    #    摇 (12,7) → 地上 3×Banana → 走到 (12,8) → 背包 Banana×3）。灌木那边是直接进包的。
    picked = 0
    try:
        _db = requests.get(f"{base}/debris", timeout=10).json().get("debris") or []
    except Exception:
        _db = []
    for _d in _db:
        try:
            dx, dy = int(_d.get("x")), int(_d.get("y"))
        except Exception:
            continue
        if walk_near(dx, dy, timeout=12):
            picked += 1
        time.sleep(0.2)
    if _db:
        log(f"🍎 地上有 {len(_db)} 件掉落物（果子那类），走过去捡了 {picked} 处")

    # ⚠️ 回执必须**回读真值**：原来不管有没有摇到都说「✅ …树莓已进背包」（真机上那是假的）。
    _after = _inv_counts()
    _gain = {}
    if _before is not None and _after is not None:
        for k, v in _after.items():
            d = v - _before.get(k, 0)
            if d > 0:
                _gain[k] = d
    if _gain:
        log(f"✅ 摇了 {total} 棵该摇的灌木，**背包 +{sum(_gain.values())}**："
            + "、".join(f"{k}×{v}" for k, v in _gain.items()))
    elif (not _walnut_mode) and _before is not None and _after is not None:
        # ⚠️ 核桃模式不走这句：金核桃**不进背包**（是存档计数），"背包没多"在姜岛是**正常**的，
        #    那句话留给下面的 `/nuts` 回读说（别拿背包账替核桃说话）。
        log(f"⚠️ 摇了 {total} 棵，可**背包一件都没多** —— 这些灌木现在并没有可摘的果子"
            f"（`bushBloom` 只是「贴图那一帧」，不等于有货）。别重复摇，白走路。")
    elif _before is None or _after is None:
        log(f"⚠️ 摇了 {total} 棵，但**没能回读背包**（读不到 /state）—— 到底摇到没有我不知道，"
            f"自己看一眼背包")

    # 🌰 **金核桃不进背包**（它是存档计数）⇒ 背包 diff 说不了它的话，得单独回读 `/nuts`。
    #    恒 2026-10-02：「姜岛地图摇晃树丛可以一直放行，**直到当前图的金核桃都被摇掉了**」
    #    ⇒ 回执就得回答"摇掉几个 / 本图还剩几个"。
    if _walnut_mode:
        _nuts1 = bush_nuts_left()
        if _nuts1 is None:
            log("⚠️ 核桃那边**没能回读** `/nuts` —— 本图还剩几个我不知道，自己看一眼")
        elif len(_nuts1) < len(_nuts0):
            log(f"🌰 摇掉 {len(_nuts0) - len(_nuts1)} 个核桃丛，本图**还剩 {len(_nuts1)} 个**没拿"
                + ("（本图摇干净了）" if not _nuts1 else " —— 再调一次 `scene ops=berry` 接着摇"))
        else:
            log(f"⚠️ 核桃丛**一个都没摇掉**（本图还剩 {len(_nuts1)} 个）—— 多半是走不到 / 没对准，"
                f"自己看一眼再决定")


if __name__ == "__main__":
    main()
