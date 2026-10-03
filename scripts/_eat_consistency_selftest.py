# -*- coding: utf-8 -*-
"""🍽️ 自动吃食机制 · **4 套脚本一致性**钉子（恒 2026-10-03：「检查一下自动吃食机制4套脚本
（炸矿两套，下矿刷矿共两套）都差不多应该就ok了」）。

四条路（吃食入口各不相同，判据必须是同一套）：
  ① `bomb_mine.py`  炸矿·普通/头骨（`BombMiner`，吃食实现在 `bomb_common.py`）
  ② `bomb_volcano.py` 炸矿·火山（`VolcanoBot(BombMineBot)`）
  ③ `mine_run.py --mode rush`  下矿·冲层
  ④ `mine_run.py --mode farm`  下矿·刷矿
  （🗑️ 2026-10-03 恒拍板：`bomb_escort.py` **已真删** —— 协同是 `bomb_mine._run_cooperate()` 内联的，
   那个独立脚本全仓没有启动点。见 `_coop_recovery_selftest.py`。）

钉的是**『吃法/判据只有一套』**这类跨文件不变量 —— 本项目的病历来是「两个类各抄一遍，
改了一处、另一处还是死的」（2026-09-20「点名在炸矿脚本里是死的」、2026-10-03「`_eat_one`
发的是 `/use`」都是这么来的）。所以这里**读源码**钉，不靠跑起来才发现。
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def ck(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        fails.append(name)
        print(f"  ❌ {name}  {extra}")


def src(fn):
    return io.open(os.path.join(HERE, fn), encoding="utf-8").read()


def block(text, start_pat, span=60):
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if re.search(start_pat, ln):
            return "\n".join(lines[i:i + span])
    return ""


def code_only(text):
    """去掉注释**和 docstring** —— 注释/文档里**必须**能写旧形状（否则下一个读的人不知道坑在哪）。
    ⚠️ 只去 `#` 行是不够的：`/use` 那个坑正是写在 docstring 里的（"这里原来发的是 `/use`"），
       我第一版就因为把 docstring 当代码，误报了一条红（钉子比代码糙 = 假红灯）。"""
    out = []
    in_doc = False
    for ln in text.splitlines():
        if ln.count('"""') % 2 == 1:
            in_doc = not in_doc
            continue
        if in_doc:
            continue
        s = ln.lstrip()
        if s.startswith("#") or s.startswith("//"):
            continue
        # ⚠️ **行尾注释也要去掉**：`r = self._post("/eat")   # ⬅️ 必须 /eat；/use 是"用/放"`
        #    这行的注释里就写着 `/use` ⇒ 只去整行注释会把这条钉成假红（我第一版就栽在这）。
        m = re.search(r"\s#", ln)
        if m:
            ln = ln[:m.start()]
        out.append(ln)
    return "\n".join(out)


bc = src("bomb_common.py")
mr = src("mine_run.py")
bm = src("bomb_mine.py")
bv = src("bomb_volcano.py")

print("① 吃法只有一条：`/eat`（**不是** `/use` —— 后者是『用/放』，吃不动）")
eat_m = block(bc, r"    def eat\(self, name=None\)", span=14)
ck("…`BombMiner.eat()` 发 `/eat`", '"/eat"' in eat_m, eat_m[:200])
ck("…`BombMiner.eat()` **不发** `/use`", "/use" not in code_only(eat_m))
_e1 = block(mr, r"    def _eat_one\(self, name", span=45)
ck("…`MineBot._eat_one()` 发 `/eat`（2026-10-03 真机：它原来发 `/use` ⇒ 点名吃食从来没吃上）",
   '"/eat"' in _e1)
ck("…`MineBot._eat_one()` **不发** `/use`", "/use" not in code_only(_e1))
_ae = block(mr, r"    def auto_eat\(self", span=60)
ck("…`MineBot.auto_eat()` 发 `/eat`", '"/eat"' in _ae)

print("② 『效果食物除外』的判据只有一处：`food_buff_entries` / `food_buffs_of`（跨语言形状适配）")
ck("…有 `food_buff_entries()` 这个唯一的形状适配入口", "def food_buff_entries(" in bc)
ck("…`food_buffs_of()` 走它（不再自己拆一遍形状）", "return food_buff_entries(f[3])" in bc)
_er = block(bc, r"    def eat_recovery\(self", span=190)
ck("…`eat_recovery()` 的『效果食物除外』走 `food_buff_entries`",
   'food_buff_entries(it.get("foodBuffs"))' in _er)
ck("…`eat_recovery()` 代码里不再有裸的 `it.get(foodBuffs) or []` 当真假判据",
   not re.search(r'it\.get\("foodBuffs"\)\s*or\s*\[\]', code_only(_er)))
_ein = block(bc, r"    def eat_if_needed\(self", span=90)
ck("…炸矿那套自动挑走『离补满最接近』（共享 `pick_food_closest_to_full`）",
   "pick_food_closest_to_full" in _ein)
ck("…`MineBot.auto_eat()` 同上（**共享**同一个函数，不是各写一份）",
   "pick_food_closest_to_full" in _ae)
ck("…挑不出来要留痕（`effect_food_note`）——『没吃』和『没跑』不能长得一样",
   "effect_food_note" in _ein and "effect_food_note" in _ae)

print("③ 『点名 = 白名单，没货不吃别的』两套实现必须都在")
ck("…炸矿那套：`eat_if_needed()` 点名没吃上 ⇒ 到此为止，不回落自动挑",
   "点了名而没吃上 ⇒ 到此为止，不换别的" in _ein)
ck("…炸矿自保那套：`eat_recovery()` 点名没货 ⇒ 显式不吃别的",
   "按「有点名只吃点名」**不吃别的**" in _er)
_mrein = block(mr, r"    def eat_if_needed\(self", span=60)
ck("…下矿那套：`eat_if_needed()` 同一条规矩", "不吃别的" in _mrein)

print("④ 吃完必须知道『成没成』（回包 `ok` 才算吃上，别拿『我发过请求』当成功）")
ck("…`BombMiner.eat()` 读回包的 `ok`", 'r.get("ok", False)' in eat_m)
ck("…`MineBot._eat_one()` 读回包的 `ok`", 'r.get("ok")' in _e1)
_erc = block(bc, r"    def eat_recovery\(self", span=230)
ck("…`eat_recovery()` 只在 `ate_ok` 时记 `_recover_streak`（否则假撤退）",
   "if not ate_ok:" in _erc and "_recover_streak += 1" in _erc)
ck("…`eat_recovery()` 的『自保吃 X』那行**只在真吃上时才打**（原来无条件打 ⇒ 先说吃了再说没吃上）",
   "if ate_ok:" in _erc and "自保想吃" in _erc)

print("⑤ 吃食线只有一个口径：`EAT_HP_PCT`（`hp_threshold` 形参已废弃，别再传它装样子）")
ck("…常量是 60", "EAT_HP_PCT = 60" in bc)
ck("…`bomb_mine.py` 不再传那个废弃形参", "eat_if_needed(self.hp_threshold)" not in code_only(bm))
ck("…`bomb_volcano.py` 也不传", "eat_if_needed(self.hp_threshold)" not in code_only(bv))
ck("…注释里写明『参数保留但不再影响吃』（别让下一个人以为 `--hp-threshold` 能调吃食线）",
   "不再影响吃" in bc)

print("⑥ 四套的『补 buff』入口都在（`maintain_buffs` → 共享的 `maintain_buffs_for`）")
ck("…`BombMiner.maintain_buffs()` 委托给模块级 `maintain_buffs_for`（不各抄一遍）",
   "return maintain_buffs_for(self, threshold=threshold, want=want)" in bc)
ck("…`MineBot.maintain_buffs()` 同上（同一份实现）",
   "return maintain_buffs_for(self, threshold=threshold, want=want)" in mr)
ck("…`bomb_mine.py` 每层/每次尝试都维护 buff", code_only(bm).count("maintain_buffs(") >= 2)
ck("…`mine_run.py` 冲层每层 + 刷矿每轮都维护 buff", code_only(mr).count("maintain_buffs(") >= 2)

print("⑦ 『不点名也自动补』（恒 2026-10-03 晚拍板：4 套统一到炸矿那套的口径）")
_mrc = code_only(mr)
ck("…`mine_run` 两处都是 `want=food_buff or None`（点名才限、不点名自动挑）",
   _mrc.count("want=food_buff or None") >= 2)
ck("…**没有** `if food_buff:` 把补 buff 关在门外（原来不点名时压根不补）",
   not re.search(r"if food_buff:\s*\n\s*try:\s*\n\s*self\.maintain_buffs", _mrc))
ck("…`bomb_mine` 那两处也是 `want=... or None`（本来就是自动补）",
   code_only(bm).count('want=getattr(self, "food_buff", "") or None') >= 2)
ck("…banner 要把『不点名=自动挑』说出来（别让日志看着像『没在补』）",
   "不点名（自动挑带 buff 的那份）" in mr)
# ⚠️ 火山**没有**这条线 —— 恒 2026-10-03 晚拍板「都不接，维持现状」⇒ 这里只**记录事实**。
print(f"  ℹ️ 恒拍板维持现状：bomb_volcano 里 `maintain_buffs` {code_only(bv).count('maintain_buffs(')} 次"
      f"（火山不补 buff，MCP 指南已写明）；协同那段同口径（bomb_mine._run_cooperate 里也 0 次）")

print()
if fails:
    print(f"❌ {len(fails)} 条没过：")
    for f in fails:
        print(f"   · {f}")
    sys.exit(1)
print("🎉 全部通过（4 套吃食：吃法 /eat、效果食物除外、点名白名单、补 buff、吃食线口径 —— 都对齐了）")
