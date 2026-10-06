# -*- coding: utf-8 -*-
"""钉子：单子上那行「缝纫」（**开缝纫机**）—— 纯离线（不起服务、不碰游戏）。

恒 2026-10-06：「**这类还是上一下吧，不做任何打开缝纫机菜单之外的入口就可以了**（一般在小屋里用不到）
然后在**工作台：铁砧（饰品锻造）、锻造（武器附魔）后面加个缝纫**」。

⇒ 分工：**这一行只负责"走到缝纫机旁把它打开"**；放料/开缝/收产物仍归 `menu tailor`（`TAILOR_V`）。

钉六条：
  ① `SEWING_V` 存在（键 `sewing`、标签「缝纫」）
  ② 注册表里**紧挨着 `REFORGE_V`**（恒点名的位置：铁砧/锻造后面）
  ③ 判据三态：`ctx.sewing` 空 ⇒ 不给；有坐标 ⇒ 给；**机器已经开着（`ctx.tailor` 非空）⇒ 收起来**
     （⛔ 不然菜单态里两行同名「缝纫」）
  ④ 执行只 `run("sewing", {"x","y"})`（⛔ 不在 exec 里自己写 HTTP）
  ⑤ 服务端三样都在：`_im_sewing` / `_im_run` 里的 `"sewing"` / `ctx_from(sewing=…)`
  ⑥ `_im_sewing` 的判据是**回读 `activeMenu`**（`actionTriggered` 不算数）

跑：`python scripts/_sewing_row_selftest.py`
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("NAGI_URL", "http://localhost:7843")
sys.path.insert(0, HERE)
import nagi_mcp_server as M       # noqa: E402
import intent_menu as IM          # noqa: E402

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


_im = io.open(os.path.join(HERE, "intent_menu.py"), encoding="utf-8").read()
_sv = io.open(os.path.join(HERE, "nagi_mcp_server.py"), encoding="utf-8").read()

print("① 动词在不在")
ck("…`SEWING_V` 存在", callable(getattr(IM, "SEWING_V", None)) is False and IM.SEWING_V is not None)
ck("…键是 `sewing`、标签「缝纫」", IM.SEWING_V.key == "sewing" and IM.SEWING_V.label == "缝纫",
   f"{IM.SEWING_V.key}/{IM.SEWING_V.label}")

print("② 注册位置（恒：铁砧/锻造后面）")
_i_ref, _i_sew = _im.index("GEODE_V, REFORGE_V,"), _im.index("SEWING_V,", _im.index("GEODE_V, REFORGE_V,"))
ck("…`SEWING_V` 紧跟在 `GEODE_V, REFORGE_V,` 之后那一行",
   0 < _i_sew - _i_ref < 400, f"间距 {_i_sew - _i_ref}")

print("③ 三态判据（含「机器已开就收起来」）")
_st = {"location": {"name": "Cabin"}, "player": {"x": 41, "y": 24}}
_c_none = IM.ctx_from(state=_st, surr={}, sewing={})
_c_has = IM.ctx_from(state=_st, surr={}, sewing={"x": 38, "y": 30, "name": "缝纫机"})
_c_open = IM.ctx_from(state=_st, surr={}, sewing={"x": 38, "y": 30}, tailor={"left": None, "canStart": False})
ck("…`sewing` 空 ⇒ 不给那一行", IM._sewing_can(_c_none, None) is IM.CAN_NO)
ck("…有坐标 ⇒ 给（CAN_YES）", IM._sewing_can(_c_has, None) is IM.CAN_YES)
ck("…⛔ 机器已开着（`ctx.tailor` 非空）⇒ 收起来（不许两行同名「缝纫」）",
   IM._sewing_can(_c_open, None) is IM.CAN_NO)
ck("…理由栏带坐标（一眼知道去哪台）", "38" in IM._sewing_reason(_c_has, None)
   and "30" in IM._sewing_reason(_c_has, None), IM._sewing_reason(_c_has, None))

print("④ 执行只调 op（不自己写 HTTP）")
_i = _im.index("def _exec_sewing(")
_body = _im[_i:_im.index("\ndef ", _i + 10)]
ck("…走 `run(\"sewing\", {…x…y…})`", 'run("sewing"' in _body and '"x"' in _body and '"y"' in _body)
ck("…⛔ exec 里没有裸 HTTP（`_post(`/`_get(`）", "_post(" not in _body and "_get(" not in _body)

print("⑤ 服务端三样")
ck("…`_im_sewing` 在", "def _im_sewing(" in _sv)
ck("…`_im_run` 挂了 `\"sewing\"`", '"sewing": lambda:' in _sv)
ck("…会话里传了 `sewing=`（探针）", "sewing=_sewing_machine_probe(state)" in _sv)
ck("…探针扫的是 `(BC)247`（缝纫机没有 Action，只能扫 objId）",
   '"(BC)247"' in _sv and "_sewing_machine_probe" in _sv)

print("⑥ 判据＝回读菜单态（`actionTriggered` 不算数）")
_sew = _sv[_sv.index("def _im_sewing("):]
_sew = _sew[:_sew.index("\ndef ", 10)]
ck("…认 `activeMenu` 里是不是 `TailoringMenu`", "TailoringMenu" in _sew and "activeMenu" in _sew)
ck("…没开就如实报（别当成开成了）", "菜单没开" in _sew)

print()
if FAIL:
    print(f"❌ 失败 {len(FAIL)} 项: {FAIL}")
    sys.exit(1)
print("✅ 全过（0 失败）")
