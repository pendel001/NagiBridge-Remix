"""Machine Loader — 往空机器批量放原料（拟人走路版）。

每台机器：warp 到地点门口（进建筑走门）→ walk_to 沿机器间过道走到机器旁 → 选中原料 → interact。
机器横排竖排留过道，只走得到的地方（墙/被围自然跳过），不瞬移不穿墙。
走的是游戏自己的 checkForAction，配方时间/品质自动对（Keg 酿酒、Cask 陈化…）。
建筑内部 Game1.warpFarmer 按名进不去、多栋同名建筑（Cabin）靠建筑坐标 /warp_building 定位。

用法:
    python machine_loader.py Starfruit --type Keg
    python machine_loader.py "Starfruit Wine" --type Cask --location Cellar --count 10
"""

import argparse
import time
import stardew_api as api


def _status_map():
    """一次拉 `/machines` 建 `{(x,y): status}` 索引。

    ⚠️ 别每台机器拉一次——屋里 100 台，一圈下来 100 发 HTTP（老代码就是这么干的）。
    拉的是**当前所在地点**的机器，所以只在我们已经站在那栋屋里时才有意义。
    """
    try:
        ms = api.machines().get("machines") or []
    except Exception:
        return {}
    out = {}
    for m in ms:
        try:
            out[(int(m.get("x")), int(m.get("y")))] = m.get("status")
        except (TypeError, ValueError):
            continue
    return out


def get_serviceable_machines(machine_type="", location="", with_items=None, here=False):
    """这一趟该伺候哪些机器 = **好了的（收）+ 空着的（放）**。

    ⚠️ 2026-09-27 恒：「**收放一条过**」——好了的机器要收、空着的要放，
        **两者混在同一趟路里**。所以目标不再是"空机器"，是"**能伺候的机器**"。
    ⚠️ `with_items` 空（没带原料）⇒ **只挑 ready**，别把空机器也串进路线白走一趟。
    ⚠️ 机器类型**默认不限**：机器是混着摆的（小桶+脱水机+水冷塔），限了会"路过却不收"——
        人也是走到哪儿收哪儿。（恒 2026-09-27 同意：收不传设备，放只传物品。）

    ⚠️⚠️ `here=True` = **只伺候脚下这间屋**，数据源换成 `/machines`。为什么非要这样——
        2026-09-27 真机踩的坑，**同一间屋子三个名字对不上**：
            `/state.location.name` = `FarmHouse`、`uniqueName` = `FarmHouse`（**都不带 guid**）
            `/machines.location`   = `FarmHouse`
            `/farm_report` 的机器条目：`location` = `Cabin`、`location_unique` = `FarmHouse<guid>`
        ⇒ 拿前两个去过滤第三个，**恒得 0**（第一趟就这么空跑了两千多次）。
        更狠的是：`/farm_report` 默认范围是「农场+建筑室内+地窖」，**压根不含房主的 FarmHouse**
        （它在 `Game1.locations` 里，既不是农场建筑也不是地窖）⇒ 站在家里跑"全农场"，
        **家里那 100 台一台都扫不到**。
        ⇒ **要伺候脚下这间，就用 `/machines`——它报的就是当前地点，不用名字匹配。**
           （`/farm_report` 漏掉 FarmHouse 这件事本身是 C# 的账，已记批次。）
    """
    if here:
        try:
            ml = api.machines().get("machines") or []
        except Exception as e:
            return [], f"/machines 失败: {e}"
    else:
        fr = api.farm_report()
        if not fr.get("ok"):
            return [], fr.get("error", "")
        ml = (fr.get("machines") or {}).get("machines") or []
    only_collect = not with_items
    out = []
    for m in ml:
        st = m.get("status")
        if st == "ready":
            pass                                  # 好了 → 收
        elif st == "empty" and not only_collect:
            pass                                  # 空着且有料 → 放
        else:
            continue
        if machine_type and str(m.get("type", "")).lower() != machine_type.lower():
            continue
        if not here and location and str(m.get("location", "")).lower() != location.lower():
            continue
        out.append(m)
    return out, ""


def _wait_arrival(tx, ty, timeout=18):
    """轮询等 walk_to 到达 (tx,ty)（1 格内）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = api.state()
            px, py = s.get("player", {}).get("x"), s.get("player", {}).get("y")
            if px is not None and py is not None:
                if abs(px - tx) <= 1 and abs(py - ty) <= 1 and not s.get("player", {}).get("isMoving"):
                    return True
        except Exception:
            pass
        time.sleep(0.7)
    return False


def _current_location():
    try:
        return api.machines().get("location")
    except Exception:
        return None


def enter_building(loc, b):
    """拟人进门：walk_to 到门口 → 站门下方 → 面朝门 → interact 开门进去。
    不瞬移。walk_to 走不到门口时（不肯出门）才回退 warp 到门外。返回 True/False。

    ⚠️ 2026-09-16 恒："中间还是传送出来重新出发了" —— 和 `fruit_round.enter_building` 同病：
       下面那句 `walk_to {location:"Farm"}` 无条件发，人在**屋里**时会走**跨图分支**
       （先 warp 到 Farm=屋外，再走回门口再进门）⇒ 肉眼看就是"被传到屋外又跑回来"。
       ⚠️ **注意这函数在 fruit_round / machine_loader / building_round 里各有一份**，
       改一处不够（我第一轮就只改了 fruit_round，恒这次看到的正是漏掉的这份）。
       ⇒ 开头先确认是不是已经在里面，是就直接返回。
    """
    if str(_current_location() or "").lower() == str(loc).lower():
        api.log(f"🚪 已经在 {loc} 里了，不重复进出")
        return True
    api.log(f"🚪 走门口进 {loc} Farm({b['doorX']},{b['doorY']})")
    api._post("/walk_to", {"location": "Farm", "x": b["doorX"], "y": b["doorY"]})
    if not _wait_arrival(b["doorX"], b["doorY"], timeout=20):
        api.log("  walk_to 走不到，warp 到门外")
        wr = api.warp_into("Farm", b["doorX"], b["doorY"])
        if not wr.get("ok"):
            return False
        time.sleep(0.5)

    if _current_location() and str(_current_location()).lower() == str(loc).lower():
        return True

    api._post("/position", {"x": b["doorX"], "y": b["doorY"] + 1})
    time.sleep(0.5)
    api._post("/face", {"direction": 0})   # 0=上，门在头顶
    time.sleep(0.3)
    api._post("/interact")
    for _ in range(10):
        time.sleep(0.8)
        if _current_location() and str(_current_location()).lower() == str(loc).lower():
            return True
    return False


# ── 机器需求判定(2026-08-29 细化装载失败报错, 数据源=恒提供的星露谷设备输入映射表) ──
# 键=mod 报的机器 type(英文), 值=正确输入的描述(含煤条件)。2026-08-29 恒拍板:只列种类,给 AI 自查装对没
_MACHINE_NEED = {
    "Furnace": "矿产(铜/铁/金/铱矿石)+1煤炭",
    "Heavy Furnace": "矿产+3煤炭",
    "Fish Smoker": "鱼+1煤炭",
    "Keg": "水果/蔬菜/蜂蜜/咖啡豆/茶叶",
    "Cask": "陈酿用成品(果酒/奶酪/腌菜——木桶收成品再陈化)",
    "Seed Maker": "作物(水果/蔬菜)",
    "Preserves Jar": "水果/蔬菜",
    "Cheese Press": "牛奶/大壶牛奶",
    "Mayonnaise Machine": "鸡蛋/鸭蛋/鸵鸟蛋/虚空蛋",
    "Loom": "兔毛/绵羊毛",
    "Oil Maker": "向日葵种子/玉米/油菜籽/松露",
    "Dehydrator": "5个同星级同名果蔬/蘑菇",
    "Crystalarium": "宝石(钻/翡翠/红宝/黄玉/紫/海蓝/绿宝石)",
    "Charcoal Kiln": "木材×10",
    "Bones Mill": "骨头碎片/古代骨头",
    "Geode Crusher": "晶球",
    "Recycling Machine": "垃圾/破碎CD/废报纸等",
    "Wood Chipper": "硬木",
    "Slime Egg Press": "史莱姆泥×100",
    "Bait Maker": "鱼",
}
# 自动/放置类(无需放料、随时间自动产)——注意 Bait Maker 要放鱼,不算自动
_AUTO_MACHINE = {"Bee House", "Tapper", "Heavy Tapper", "Lightning Rod", "Solar Panel",
                 "Worm Bin", "Deluxe Worm Bin", "Mushroom Log",
                 "Incubator", "Slime Incubator", "Ostrich Incubator",
                 "Statue Of Perfection", "Statue Of Endless Fortune", "Garden Pot"}


def _machine_missing_reason(mtype):
    """机器 type(英文) → 一句话:这台机器要什么(列正确种类, AI 自己对照是否装错品类)。"""
    mt = (mtype or "").strip()
    if mt in _AUTO_MACHINE:
        return f"{mt} 是自动/放置类,无需放料"
    need = _MACHINE_NEED.get(mt)
    if not need:
        return f"{mt} 需求未登记(查 help 或让恒确认)"
    return f"{mt} 需要 {need}"


def _inv_has(name):
    """背包里有没有这个物品（按内部名或显示名比对）。

    ⚠️ **必须精确相等**：第一版写成 `nm in i["name"]`——那是**字符串子串匹配**，于是
       `"ancient fruit" in "ancient fruit wine"` **成立** ⇒ 背包里只剩果酒时也答"有上古水果"，
       照样去 `select` 一下、闪出错物品、再被核对拒掉。**判据松一格，闪就还在。**
       （下面 `want in (a, b)` 是**元组成员判定=精确相等**，不是子串——两者写法像、语义差得远。）
    """
    try:
        want = str(name).strip().lower()
        for i in (api.state().get("inventory") or []):
            if want in ((i.get("name") or "").strip().lower(),
                        (i.get("displayName") or "").strip().lower()):
                return True
    except Exception:
        pass
    return False


def _current_item():
    """当前手持物名（`/state` 的 `player.currentItem`），读不到就空串。"""
    try:
        return str((api.state().get("player") or {}).get("currentItem") or "").strip()
    except Exception:
        return ""


# 交互 8 方向：**正交四邻 + 斜角四邻**。
# ⚠️ 恒 2026-09-16："**八方向的意思是邻近斜格和上下左右有设备就可以交互**"
#    —— SDV 站在 (1,1) 能右键到 (2,2)，"孤岛"布局（机器排满、四正交邻全被占）**只能靠对角收**。
#    这里原来**只有正交四邻** ⇒ 机器一被围就直接报「四邻都走不到（被围死）」跳过。
#    `fruit_round.py` 的 `INTERACT_NEIGHBORS` 早就是 8 向了，是**这一份没跟着改**。
#    ⚠️ 正交排前面：正交朝向精确、`/interact` 更稳；斜角当补充。
INTERACT_NEIGHBORS = [(0, 1), (0, -1), (-1, 0), (1, 0),
                      (1, 1), (1, -1), (-1, 1), (-1, -1)]


def select_any(items, holding=""):
    """按顺序挑一个**背包里真有**的原料选中；返回 (选中的名字 or "", 说明)。

    ⚠️ **必须先查背包再 select**（2026-09-16 恒看见的"防风草→果酒→停顿"就是这个）：
       早先的写法是"先 select 再回读核对"，可 `/select` 精确匹配失败后有 **Contains 兜底**
       （`"Pickaxe"→"Iridium Pickaxe"` 是特意加的），`"Ancient Fruit"` 没货时会**先**匹配到
       `"Ancient Fruit Wine"` —— **手上当场闪一下果酒**，然后核对不过、再去选下一个。
       功能上没错，观感上就是手持物在乱跳。⇒ 先读 `/state` 背包确认有货，再去 select；
       最后仍**回读 `currentItem` 兜底核对**（万一背包读花了）。
    ⚠️ `holding` = 上一台记下的"手上拿的是什么"。**它只是个提示，绝不能拿它当判据**——
       2026-09-16 恒真机抓到「**空手摸桶**」：那一栈**刚刚在上一台用尽**（`currentItem` 已空），
       可 `holding` 还记着"手上是上古水果" ⇒ 这里直接 `return` 不重选 ⇒ 空手去 interact
       ⇒ 游戏没收下 ⇒ 报成「Keg 需要 水果/蔬菜/蜂蜜…」（**把"我没拿东西"误报成"装错品类"**）。
       ⇒ **一律回读 `/state` 的 `currentItem` 当场核对**，"手上真是它"才敢跳过 `select`。
       这一条正是**跨品质档必然踩到**的：普通那栈 17 个放完 → 下一台就是空手。
    ⚠️ 品质不影响核对：`currentItem` 报的是物品名（`'Ancient Fruit'`），**不带品质前缀**（实测）；
       `/select` 重新选时，普通品质那栈用完了自然会选到金/银星那栈（**这就是"自动换档"**）。
    """
    cur_now = _current_item()
    for nm in items:
        nm = (nm or "").strip()
        if not nm:
            continue
        if not _inv_has(nm):
            continue                      # 背包里压根没有 → 直接跳下一个，**别去 select**（避免闪错物品）
        if cur_now and cur_now.lower() == nm.lower():
            return nm, ""                 # 回读确认**真的**还拿在手上，才跳过重选
        s = api.select(nm)
        if not s.get("ok"):
            continue
        time.sleep(0.15)
        if _current_item().lower() == nm.lower():
            return nm, ""
    return "", "背包里没有可用的原料了（" + "、".join(x.strip() for x in items if x and x.strip()) + "）"


def load_one(m, items, skip_enter=False, holding=""):
    """拟人走路版：走门口开门进去（或已在屋内跳过）→ walk_to 走到机器旁过道 → 选原料 → interact。

    `items` = **按优先级排好的原料名列表**（一个用完自动换下一个）。
    `holding` = 手上正拿着的原料名（一样就不重选，免得手持物每台闪一次）。
    返回 (成功?, 说明, 原料是否已彻底用尽, 现在手上拿的是什么)。
    机器横排竖排留过道，walk_to 沿过道走（只走得到的地方，墙/被围就跳过）。

    ⚠️ 2026-09-16 恒全工具测试抓到：**这个 `def` 行曾经整行不见**——函数体被粘在
    `_machine_missing_reason` 的 `return` 后面成了**死代码**，于是 `run()` 一句
    `ok, msg = load_one(...)` 直接 `NameError: name 'load_one' is not defined`
    ⇒ **`farm load`（上料）一次都没真正跑起来过**（恒："可以传指定数量多物品吗"顺出来的）。
    修=把函数头补回来。⚠️ 这类"删函数头"的事故**静态检查看不出来**（语法合法、死代码不报错），
       只能靠真机跑一遍才现形——所以每个 op 都得真调一次，别只看代码像不像对的。
    """
    loc, x, y = m["location"], m["x"], m["y"]
    b = m.get("building")   # 建筑内机器带 Farm 建筑坐标（多栋同名建筑精确定位用）

    # 1. 进地点：建筑走门（开门），非建筑 warp_into 入口。已在同屋则跳过（避免重复进门）
    if not skip_enter:
        if b:
            if not enter_building(loc, b):
                return False, f"进门失败: {loc}", False, holding
        else:
            wr = api.warp_into(loc)
            if not wr.get("ok"):
                return False, f"warp 失败: {wr.get('error', '')}", False, holding
            time.sleep(0.5)

    # 2. 依次尝试机器**八方向邻格**，walk_natural 走过去（/move 游戏 BFS 真走路；走不到才 position 兜底）
    # ⚠️ 2026-08-13：原 walk_to 对鱼饵机"纯粹没走过去"（长距离 interact 装上了）——改 walk_natural 强制走路
    # ⚠️ 2026-09-16：原来是**只有正交四邻**的字面量 —— 恒当场抓出「最初的八方向尝试丢失了」，
    #    机器一被排满围住就报「四邻都走不到（被围死）」白跳过一堆。改用共享的 8 向表。
    for dx, dy in INTERACT_NEIGHBORS:
        ax, ay = x + dx, y + dy
        # ⚠️ 2026-09-16 恒："明明有走道，闪到桶上面也很怪"。
        #    成因：机器的"四邻格"里**可能本身也摆着桶/机器**（一排桶挨着时就是这样），
        #    而下面 `_wait_arrival` 的容差是 **±2 格**（人在两格外也算"到了"），紧接着那句
        #    `/position` 又是**无条件精确瞬移**（项目里 `/position` 默认不校验落点可走）
        #    ⇒ 角色当场被闪到一个桶格上。**先问游戏这格站不站得住**，站不住就换下一个邻格。
        try:
            if not api._post("/passable", {"x": ax, "y": ay}).get("passable"):
                continue
        except Exception:
            pass
        try:
            api.walk_natural(ax, ay)
        except Exception:
            pass
        if not _wait_arrival(ax, ay, timeout=18):
            continue

        # 2b. 精确站到 (ax,ay)：walk_to 落点可能偏 1 格（容忍判定），偏了 face/interact 会点错机器
        #     （能走到这儿就说明 (ax,ay) 刚查过是可站的）
        api._post("/position", {"x": ax, "y": ay})
        time.sleep(0.3)

        # 3. 选中原料（多原料按优先级自动切换）→ 对机器瓦片直接 interact（/interact {x,y}，8 方向都行）
        #    ⚠️ 原料**彻底没得用了**是"整轮收工"信号（第三个返回值 True），不是"跳过这一台"——
        #       否则会拿着空手把剩下的每一台空机器都试一遍，刷一屏无用告警（恒 2026-09-16）。
        nm, why = select_any(items, holding=holding)
        if not nm:
            return False, why, True, holding
        holding = nm        # 记住手上是什么 → 下一台机器不用再选一次（手持物不再反复闪）

        r2 = api._post("/interact", {"x": x, "y": y})
        time.sleep(0.4)

        # 4. 验证：重新扫当前地点，(x,y) 这台是否已进入 processing
        verified = False
        try:
            for mm in (api.machines().get("machines") or []):
                if int(mm.get("x", -1)) == x and int(mm.get("y", -1)) == y:
                    verified = mm.get("status") == "processing"
                    break
        except Exception:
            pass
        if verified:
            return True, "", False, holding
        if r2.get("actionTriggered"):
            return True, "触发未验证", False, holding
        # 细化报错:列这台机器要什么,给 AI 对照是否装错品类(2026-08-29 恒拍板:只说种类,别啰嗦)
        return False, _machine_missing_reason(m["type"]), False, holding
    return False, "四邻都走不到（被围死）", False, holding


_PASS_CACHE = {}


def _passable(x, y):
    """`/passable` 带缓存——过道格要问几百格，不能一格一发 HTTP。"""
    k = (x, y)
    if k not in _PASS_CACHE:
        try:
            _PASS_CACHE[k] = bool(api._post("/passable", {"x": x, "y": y}).get("passable"))
        except Exception:
            _PASS_CACHE[k] = False
    return _PASS_CACHE[k]


def _aisle_map(machines):
    """目标机器 → **过道格**：{可站的格子: [它能八邻到的目标机器…]}。

    🚶 恒 2026-09-16："理论上**走一步就能把邻格的桶全部放上**了，但是现在看上去还是一个一个
       桶去摸，**上面一排桶下面一排桶要走两轮**。" —— 正是这个 op 的老毛病：
       老 `load_one` **一台机器一趟**（走到它邻格 → 选料 → interact → 换下一台），
       而机器是**成排摆的**，站中间一格八邻就有 2~6 台 ⇒ 白白来回扫两趟。
       `fruit_round` 早就是"站一个过道格处理一圈"，是 `machine_loader` 没跟上。

    ⚠️ 排除机器本格：两条理由——① 站到桶上面很怪（恒："闪到桶上面也很怪"）；
       ② 站在机器本格上 `interact` 自己那台，语义也不对。
    """
    occupied = {(m["x"], m["y"]) for m in machines}
    out = {}
    for m in machines:
        for dx, dy in INTERACT_NEIGHBORS:
            t = (m["x"] + dx, m["y"] + dy)
            if t in occupied:
                continue
            out.setdefault(t, []).append(m)
    return {t: ms for t, ms in out.items() if _passable(*t)}


def _order_tiles(tiles, start):
    """最近邻排序：从 start 起反复挑最近的没走过的格子（同 fruit_round.plan_path）。"""
    remaining = list(tiles)
    path = []
    px, py = start
    while remaining:
        t = min(remaining, key=lambda k: abs(k[0] - px) + abs(k[1] - py))
        path.append(t)
        px, py = t
        remaining.remove(t)
    return path


def service_around(sx, sy, ms, items, holding=""):
    """**站在过道格 (sx,sy)，把它八邻的机器挨个伺候**——该收的收、该放的放。
    （"走一步办一圈"，2026-09-16 恒骂过"上面一排下面一排走两轮"之后的老形状。）

    ⚠️ 2026-09-27 恒拍板「**一台摸两下**」：好了的机器**第一下收进包、第二下才把料放进去**。
       所以 ready 的机器最多摸两次；本来就空着的只摸一次。（游戏里人也是这么操作的。）

    **怎么知道干成了什么：看 `status` 的前后变化，不猜**——
        ready → empty       = 收了
        empty → processing  = 放了
        没变                = 什么都没干（放错品类 / 没原料）
    ⚠️ 手持不合法时游戏**不消耗原料**，所以"放错品类"只是白摸一下，不亏东西。

    ⚠️ 原料耗尽**不等于整轮收工**：只是**停止放**，剩下的机器照样收。
       （老 `load_around` 一没料就 return「exhausted」，调用方当场 break 掉整轮——
        那是"只放不收"年代的写法，放到今天会把后面 ready 的机器**整片漏收**。）

    返回 (伺候到的机器名单, 收了几件, 放了几台, 说明, 原料是否耗尽, 现在手上拿的是什么)
    ⚠️ 回**名单**不是回个数：老代码 `todo[:n]` 假设"前 n 台成功"，
       中间一旦有失败就记错（无害，但是假的）。这里如实回名单。
    """
    done, notes = [], []
    n_collect = n_load = 0
    exhausted = False
    smap = _status_map()

    def st_of(m):
        return smap.get((m["x"], m["y"]))

    for m in ms:
        before = st_of(m)

        # ── 空着的机器：**先拿料，再摸** ──────────────────────────────
        # ⚠️⚠️ 2026-09-27 真机血案：第一版只在"收完再放"那条支路里 select，
        #     空机器这条路**直接 interact** ⇒ **空手摸桶**，53 台全报
        #     「Keg 需要 水果/蔬菜/蜂蜜/咖啡豆/茶叶」。恒一眼看出「没有成功手持」。
        #     **select 必须在 interact 之前，两条路都要**（老 load_around 就是每台都先 select 的）。
        if before == "empty":
            if not items or exhausted:
                continue                      # 没料不摸（计划里也该被剔掉，见 run() 的剪枝）
            nm, why = select_any(items, holding=holding)
            if not nm:
                exhausted = True
                continue
            holding = nm
            api._post("/interact", {"x": m["x"], "y": m["y"]})
            time.sleep(0.35)
            smap = _status_map()
            if st_of(m) == "processing":
                n_load += 1
                done.append(m)
            else:
                notes.append(f"({m['x']},{m['y']}) {_machine_missing_reason(m['type'])}")
            continue

        # ── 好了的机器：第一下是"收"，第二下才拿料放 ────────────────────
        api._post("/interact", {"x": m["x"], "y": m["y"]})
        time.sleep(0.35)
        smap = _status_map()
        after = st_of(m)

        if before == "ready" and after == "empty":
            n_collect += 1
            done.append(m)
            if items and not exhausted:
                nm, why = select_any(items, holding=holding)
                if not nm:
                    exhausted = True
                else:
                    holding = nm
                    api._post("/interact", {"x": m["x"], "y": m["y"]})
                    time.sleep(0.35)
                    smap = _status_map()
                    if st_of(m) == "processing":
                        n_load += 1
            continue

        # 既不是 ready 也不是 empty（processing 之类）——本来就不该进这趟
        notes.append(f"({m['x']},{m['y']}) 来的时候是 {before}，摸完是 {after}，没动")

    return done, n_collect, n_load, "; ".join(notes), exhausted, holding


def run(items, machine_type="", location="", count=0, no_enter=False, here=False):
    """items: 原料名列表（按优先级；一个用尽自动换下一个）。**留空 = 只收不放。**
    count: 最多装几台（0=不限）。

    ⚠️ 2026-09-27 恒：「**收放一条过**」——好了的收、空着的放，同一趟路办完。
       原料用尽 ⇒ **只是停止放，继续收**。老代码在这儿 break 掉整轮，
       会把后面 ready 的机器**整片漏掉**（那是"只放不收"年代的写法）。

    ⚠️⚠️ `machine_type` 那一档**别省**（2026-10-01 真机两句话钉的）：
       恒「**怎么把裁缝机也交互了**」（不传类型 ⇒ 空机器的目标集是"本图**所有**空机器"
       ⇒ AI 挨个去点缝纫机/花盆；游戏不收、只白摸一下，**但屏幕上看得见**）+
       恒「**罐头瓶和酒桶控制不了分别放不同水果**」（不传类型 ⇒ "一件料"会撒进**所有**收得下它的机器）。
       ⇒ **机器类型就是 AI 手里的那个旋钮**：单子按类型发号，这里按类型干活
       （类型由调用方从 `/machine_reqs` 的 `canPlace` = 游戏 `PlaceInMachine(probe:true)` 算出来）。

    🚶 走位模型（2026-09-16 改）：**按"过道格"走，不按"机器"走**——
       一站八邻一次办完，机器成排时省掉一大半来回。
    """
    if here:
        no_enter = True          # 只伺候脚下这间 ⇒ 本来就不用进门
    mode = "收放" if items else "只收"
    api.log(f"=== Machine Loader({mode}): items={items or '(无)'} type={machine_type or 'any'} "
            f"loc={'★脚下这间' if here else (location or 'all')} "
            f"count={count or '∞'} no_enter={no_enter} ===")
    targets, err = get_serviceable_machines(machine_type, location,
                                            with_items=items, here=here)
    if err:
        api.log(f"获取机器列表失败: {err}")
        return
    n_ready = sum(1 for m in targets if m.get("status") == "ready")
    api.log(f"可伺候 {len(targets)} 台（好了 {n_ready} · 空着 {len(targets) - n_ready}）")
    if not targets:
        api.log("没有可伺候的机器")
        return

    # 按"地点+建筑"分组：同一栋屋里一口气走完，不用反复进出
    groups = {}
    for m in targets:
        b = m.get("building") or {}
        groups.setdefault((m.get("location"), b.get("x"), b.get("y")), []).append(m)

    n_collect = n_load = skipped = 0
    stop_msg = ""
    holding = ""      # 手上正拿着的原料；跨过道格保持，避免每格都重选、手持物狂闪（恒 2026-09-16）
    feed = list(items)          # 原料用尽后置空 ⇒ 后面只收不放（**不是收工**）
    for key, ms in groups.items():
        if count and n_load >= count:
            break
        loc, bx, by = key
        # 每组（= 一栋屋）只进一次门。`b` 直接取该组任一机器的 building 字典（doorX/doorY 就在里面）。
        if not no_enter:
            b = next((m["building"] for m in ms if m.get("building")), None)
            if b and "doorX" in b:
                if not enter_building(loc, b):
                    api.log(f"⚠️ 进不去 {loc}，跳过这一组 {len(ms)} 台")
                    skipped += len(ms)
                    continue
            else:
                wr = api.warp_into(loc)
                if not wr.get("ok"):
                    api.log(f"⚠️ warp 进 {loc} 失败，跳过这一组 {len(ms)} 台")
                    skipped += len(ms)
                    continue
                time.sleep(0.5)

        aisles = _aisle_map(ms)
        p = api.state().get("player", {})
        path = _order_tiles(list(aisles), (p.get("x", 0), p.get("y", 0)))
        api.log(f"📍 {loc}: {len(ms)} 台 → {len(path)} 个过道格（走一圈，不再一桶一趟）")
        done_machines = set()
        for (sx, sy) in path:
            if count and n_load >= count:
                break
            todo = [m for m in aisles[(sx, sy)] if (m["x"], m["y"]) not in done_machines]
            if not todo:
                continue
            if not _walk_to_tile(loc, sx, sy):
                skipped += len(todo)
                api.log(f"  ⚪ 过道格 ({sx},{sy}) 走不到，跳过其 {len(todo)} 台")
                continue
            done, c, l, note, exhausted, holding = service_around(
                sx, sy, todo, feed, holding=holding)
            for m in done:
                done_machines.add((m["x"], m["y"]))
            n_collect += c
            n_load += l
            skipped += len(todo) - len(done)
            if c or l or note:
                api.log(f"  🟢 格({sx},{sy}) 八邻收 {c} 件 / 放 {l} 台"
                        + (f"  ⚠️ {note}" if note else ""))
            if exhausted and feed:
                feed = []
                stop_msg = "原料用完了"
                # ⚠️ **不 break**（剩下的 ready 机器照样要收），但**必须立刻把空机器剪掉**：
                #    没料了再走过去纯属白走。恒 2026-09-27：「**多少水果放多少个桶，
                #    没有就停了**。8 个水果……别说经过 8 个桶，远远不止八个位了」。
                #    第一版只把 feed 置空、没剪枝 ⇒ 6 个水果在 53 台空桶间走了一整圈。
                ready_xy = {(m["x"], m["y"]) for m in targets if m.get("status") == "ready"}
                aisles = {t: [x for x in v if (x["x"], x["y"]) in ready_xy]
                          for t, v in aisles.items()}
                n_left = sum(len(v) for v in aisles.values())
                api.log(f"  ⏹ {stop_msg} —— 后面只收不放（本图还有 {n_left} 台待收）")
                if not n_left:
                    api.log("  ⏹ 本图没有待收的机器了 —— 收工")
                    break

    # 收工复核：**再扫一次**，如实报还剩几台空着——不靠自己记的账
    left_empty = 0
    try:
        # 复核跟目标用**同一个数据源**（`--here` 时是 /machines），别混着比
        src = (api.machines().get("machines") if here
               else (api.farm_report().get("machines") or {}).get("machines")) or []
        left_empty = sum(
            1 for m in src
            if m.get("status") == "empty"
            and (here or not location
                 or str(m.get("location", "")).lower() == location.lower())
            and (not machine_type
                 or str(m.get("type", "")).lower() == machine_type.lower()))
    except Exception:
        pass

    msg = f"✅ 收了 {n_collect} 件 · 放入 {n_load} 台"
    if not items:
        msg += " · 没带原料，只收"
    if left_empty:
        msg += f" · 还有 {left_empty} 台空着" + (f"（{stop_msg}）" if stop_msg else "")
    msg += f" · 未办成 {skipped} 台"
    api.log(msg)


def _walk_to_tile(loc, tx, ty):
    """走到指定过道格并等到位（走不到返回 False，交给调用方跳过）。"""
    try:
        r = api._post("/walk_to", {"location": loc, "x": tx, "y": ty})
        if not r.get("ok"):
            return False
    except Exception:
        return False
    return _wait_arrival(tx, ty, timeout=18)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load raw materials into empty machines (game-native)")
    parser.add_argument("item", nargs="?", default="",
                        help="原料英文名/ID（**留空 = 只收不放**）；可用逗号给多个并按优先级依次用完，"
                             "如 'Ancient Fruit,Starfruit'（Cask 用成品如 Starfruit Wine）")
    parser.add_argument("--type", default="", help="机器类型，如 Keg / Cask（留空=所有空机器）")
    parser.add_argument("--location", default="", help="限定地点，如 Cellar / Big Shed（留空=全农场）")
    parser.add_argument("--count", type=int, default=0, help="最多装几台（0=不限）")
    parser.add_argument("--no-enter", action="store_true", help="已在目标屋内，跳过进门的 warp")
    parser.add_argument("--here", action="store_true",
                        help="**只伺候脚下这间屋**（数据源 /machines，不做地点名匹配）。"
                             "在家/在棚里干活就用它——`--location` 跟 `/farm_report` 的名字对不上，"
                             "而且 `/farm_report` 默认范围压根不含房主的 FarmHouse。")
    parser.add_argument("--port", type=int, default=7843)   # AI 角色进程（恒批注 2026-08-13：别打到 host 恒的号）
    args = parser.parse_args()

    import os
    os.environ["NAGI_URL"] = f"http://localhost:{args.port}"
    import importlib
    importlib.reload(api)

    # 逗号分隔 → 原料名列表（按给到的顺序即优先级）
    _items = [x.strip() for x in str(args.item).split(",") if x.strip()]
    run(_items, args.type, args.location, args.count, args.no_enter, here=args.here)
