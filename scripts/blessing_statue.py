"""
🗿 blessing_statue.py — 摸当前图的雕像（祝福雕像 / 矮人国王雕像），选效果

用途（下矿前）：找到雕像 → 走过去 → 交互（有选项就选）。
祝福雕像每天给随机祝福（摸一次）；如果选项里有"免疫炸弹伤害/防御"类就优先选。

⚠️ 2026-10-04 恒：「**摸雕像改成当前图有就报，有就摸**」⇒ 找雕像这一层换源：
   原先扫 `/surroundings radius 30`（真机站 (53,58)、雕像在 (74,16) 就"没找到"），
   现在走 **`/machines`** —— 它扫**整图**且每条带 `location`（真机离 45 格照样报）。
   ⚠️ 判据只此一处：`type` 含 `Statue` + `location == 本图`；**不扫家具层**
      （`(F)` 装饰雕像摸不出东西 —— 当年 `cabin enum` 与这里打架就是因为扫错了层）。

用法:
  python blessing_statue.py --port 7843        # 摸当前图第一座雕像
  python blessing_statue.py --port 7843 --list # 只列出找到的雕像
"""

import os
import sys
import time
import argparse

parser = argparse.ArgumentParser(description="[statue] 摸雕像")
parser.add_argument("--port", type=int, default=None, help="NagiBridge 端口（默认 7843）")
parser.add_argument("--list", action="store_true", help="只列雕像不摸")
args = parser.parse_args()

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ.setdefault("NAGI_URL", f"http://localhost:{args.port or 7843}")
import requests

NAGI = os.environ["NAGI_URL"]


def log(msg):
    try:
        print(f"[statue] {msg}", flush=True)
    except UnicodeEncodeError:
        try:
            enc = sys.stdout.encoding or 'utf-8'
            print(f"[statue] {msg.encode(enc, errors='replace').decode(enc, errors='replace')}", flush=True)
        except Exception:
            pass


def get(ep, params=None):
    return requests.get(f"{NAGI}{ep}", params=params, timeout=10).json()


def post(ep, data=None):
    return requests.post(f"{NAGI}{ep}", json=data or {}, timeout=10).json()


def find_statues():
    """扫**当前图**的雕像 → (图名, [(x, y, 名字), …])。

    ⚠️ 源是 `/machines`（整图 + 带 `location`），**不是** `/surroundings` 的 30 格窗口 ——
       恒 2026-10-04 的口径就是"当前图有就报"，站远了也算有。
    """
    here = ""
    try:
        here = ((get("/state").get("location") or {}).get("name")) or ""
    except Exception:
        pass
    try:
        m = get("/machines")
    except Exception as e:
        log(f"⚠️ /machines 读不到，没法找雕像：{e}")
        return here, []
    out = []
    for it in (m.get("machines") or []):
        ty = str(it.get("type") or "")
        if "Statue" not in ty:
            continue
        loc = it.get("location")
        if here and loc and loc != here:
            continue          # 别的图/别的屋：这趟不去（跨图走位是另一件事）
        x, y = it.get("x"), it.get("y")
        if isinstance(x, int) and isinstance(y, int):
            out.append((x, y, ty))
    return here, out


def my_xy():
    try:
        p = get("/state").get("player") or {}
        return int(p.get("x")), int(p.get("y"))
    except Exception:
        return None, None


def wait_arrive(tx, ty, timeout=20.0, exact=False):
    """真的等到站到 (tx,ty)（`exact=True` = **不许差一格**）—— `/walk_to` 是**发射后不管**的（老坑）。

    ⚠️⚠️ 2026-10-04 真机栽过：第一次实现允许"差一格也算到"（≤1 格），结果停在 (73,17)——
       那是雕像 (74,16) 的**斜角**，任何正方向都指不到它 ⇒ `/interact` 打空
       （`triggered=False`）而脚本照样报"摸了"。**站位必须正交**，这一格不能放。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        x, y = my_xy()
        if x is None:
            return False, "读不到坐标"
        at = ((x, y) == (tx, ty)) if exact else (max(abs(x - tx), abs(y - ty)) <= 1)
        if at:
            return True, f"到 ({x},{y})"
        time.sleep(0.4)
    x, y = my_xy()
    return False, f"{timeout:.0f}s 没到（停在 ({x},{y})，目标 ({tx},{ty}){'，要精确' if exact else ''}）"


def touch(sx, sy, name):
    """走到雕像**正旁那一格** → 面向它 → 交互 → **回读游戏那一位**。

    站点候选：下(朝上0) / 上(朝下2) / 右(朝左3) / 左(朝右1) —— 每个都要求**精确站位**。
    成功判据（照"按了就成"的规矩，**不许拿"我发过请求"当成功**）：
      · `actionTriggered=true` **且** `facingTile` 就是雕像那一格；**或**
      ·（新 DLL）`/state.player.blessedByStatueToday` 变成 `true` —— **游戏自己说的**。
    返回 (ok, teleported, why)。
    """
    here = ""
    b0 = None
    try:
        st0 = get("/state")
        here = ((st0.get("location") or {}).get("name")) or ""
        b0 = (st0.get("player") or {}).get("blessedByStatueToday")
    except Exception:
        pass
    # ⚠️⚠️ 2026-10-04 真机（第二次同族假成功）：**"游戏那位本来就是 true"不许算成功** ——
    #    那说明**今天已经有人摸过**（单子那行的门禁就是 `used_today is not True`，正常轮不到），
    #    这一发其实什么都没做。成功必须是"**我这下把它从 false 变成 true**"或 `hit`。
    if b0 is True:
        return False, False, "今天已经摸过了（`blessedByStatueToday` 本来就是 true）—— 这一下没触发，不报成功"
    cands = [((sx, sy + 1), 0), ((sx, sy - 1), 2), ((sx + 1, sy), 3), ((sx - 1, sy), 1)]
    why = "四个站位都没站到"
    for (px, py), face_dir in cands:
        ok = False
        for attempt in (1, 2):
            try:
                post("/walk_to", {"location": here, "x": px, "y": py})
            except Exception as e:
                why = f"/walk_to 失败：{e}"
                break
            got, w = wait_arrive(px, py, timeout=20.0, exact=True)
            if got:
                ok = True
                break
            why = w
            log(f"  🚶 试站 ({px},{py}) 第{attempt}次：{w}")
        if not ok:
            continue
        post("/face", {"direction": face_dir})
        time.sleep(0.3)
        r = post("/interact", {})
        ft = r.get("facingTile") or {}
        hit = bool(r.get("actionTriggered")) and ft.get("x") == sx and ft.get("y") == sy
        blessed = None
        try:
            blessed = (get("/state").get("player") or {}).get("blessedByStatueToday")
        except Exception:
            pass
        log(f"  🗿 站 ({px},{py}) 朝 {face_dir} → interact：triggered={r.get('actionTriggered')} "
            f"facing=({ft.get('x')},{ft.get('y')}) object={r.get('object')} "
            f"blessedByStatueToday={blessed}")
        if hit or blessed is True:
            return True, False, f"站 ({px},{py}) 朝 {face_dir}，游戏那位={blessed}"
        # ⚠️ 打空了就**换下一个站位**，绝不在这儿报成功（真机：斜角站位 ⇒ triggered=False）
        why = (f"站在 ({px},{py}) 朝 {face_dir} 打空了"
               f"（triggered={r.get('actionTriggered')}，facing=({ft.get('x')},{ft.get('y')})，"
               f"游戏那位还是 {blessed}）")
    # 兜底：真走位都不成 ⇒ 落到雕像正下方（**如实报**，不偷偷干）—— 同 pet_walk 的口径
    try:
        px, py = sx, sy + 1
        post("/position", {"x": px, "y": py})
        time.sleep(0.5)
        post("/face", {"direction": 0})
        time.sleep(0.3)
        r = post("/interact", {})
        ft = r.get("facingTile") or {}
        hit = bool(r.get("actionTriggered")) and ft.get("x") == sx and ft.get("y") == sy
        blessed = None
        try:
            blessed = (get("/state").get("player") or {}).get("blessedByStatueToday")
        except Exception:
            pass
        log(f"  ⚠️ 走位没成功 ⇒ 用 `/position` 落到 ({px},{py}) 再摸（**这一下不是拟人**，如实报）"
            f"：triggered={r.get('actionTriggered')} blessedByStatueToday={blessed}")
        return (hit or blessed is True), True, f"/position 兜底落到 ({px},{py})，游戏那位={blessed}"
    except Exception as e:
        return False, False, f"连兜底落点都失败：{e}（{why}）"


def main():
    try:
        st = get("/status")
        if not st.get("worldReady"):
            log("❌ 游戏未就绪")
            sys.exit(1)
    except Exception as e:
        log(f"❌ 连不上游戏: {e}")
        sys.exit(1)

    loc, statues = find_statues()
    if not statues:
        log(f"📍 {loc} | 本图没有雕像（`/machines` 整图扫过；有的话它会带坐标报出来）")
        return
    me = my_xy()
    log(f"📍 {loc} | 找到雕像（我在 {me}）:")
    for x, y, name in statues:
        d = max(abs(x - me[0]), abs(y - me[1])) if me[0] is not None else -1
        log(f"  · {name} ({x},{y})｜约 {d} 格")
    if args.list:
        return

    # 摸**最近那座**（多座时先摸顺路的）；祝福雕像一般一座就够
    statues.sort(key=lambda s: max(abs(s[0] - me[0]), abs(s[1] - me[1])) if me[0] is not None else 0)
    x, y, name = statues[0]
    ok, teleported, why = touch(x, y, name)
    if not ok:
        # ⚠️⚠️ 判决行**必须以 ⚠️/❌ 开头**：服务器那层（`blessing_statue()` → `_im_run`）是
        #    看**第一个字符**判成没成的 —— 上一版这里印"🗿 摸了…"（哪怕 triggered=False）
        #    ⇒ 单子回执头一行印 ✅（"嘴上说成功"那一族，2026-10-04 真机当场抓到）。
        log(f"⚠️ 没摸到 {name}：{why}")
        log("   🔎 下一步：站到雕像**正旁那一格**（上下左右，别站斜角）再试；"
            "或直接 `scene at {x} {y}` 手动交互".replace("{x}", str(x)).replace("{y}", str(y)))
        return
    log(f"✅ 摸到雕像 {name}（{why}{'｜含一次 /position 兜底' if teleported else '｜全程真走位'}）")

    # 选效果：读菜单图标选项，优先免疫炸弹（"无法对你造成伤害"）
    try:
        m = get("/menu")
        if m and m.get("open") and m.get("type") == "ChooseFromIconsMenu":
            icons = [b for b in (m.get("buttons") or []) if b.get("hoverText")]
            for b in icons:
                log(f"  选项: {b.get('hoverText')} @ ({b.get('x')},{b.get('y')})")
            # 优先免疫炸弹
            pick = None
            for b in icons:
                ht = (b.get("hoverText") or "")
                if "无法对你造成伤害" in ht or "炸弹" in ht:
                    pick = b
                    break
            if pick is None:
                # 其次找梯子/竖井
                for b in icons:
                    if "梯子" in (b.get("hoverText") or ""):
                        pick = b
                        break
            if pick is None and icons:
                pick = icons[0]
            if pick:
                # ⚠️ 2026-09-26：原来没带 `no_move` ⇒ `/click` 会先 `setMousePosition` **拽走恒的光标**
                #   （恒：「献祭那个强切前台+鼠标漂移」—— 拽光标 + 他正好在点 ⇒ 点到游戏窗口 ⇒ 窗口被顶到最前）。
                #   `ChooseFromIconsMenu` 不在"真读 Game1.getMouseX"的名单里（decomp grep 实查）⇒ 不用挪光标。
                post("/click", {"x": pick["x"], "y": pick["y"], "no_move": True})
                time.sleep(1.0)
                log(f"✅ 选了: {pick.get('hoverText')}")
            else:
                log("  ⚠️ 没找到可选项")
    except Exception as e:
        log(f"  ⚠️ 选效果失败: {e}")


if __name__ == "__main__":
    main()
