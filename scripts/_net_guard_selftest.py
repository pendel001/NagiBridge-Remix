# -*- coding: utf-8 -*-
"""🚫 自验出口闸的自验 —— **闸必须自己先咬过人才算数**（2026-10-01 立）。

## 案情（这道闸是为什么立的，别删）

1. **2026-10-01 晚·`_intent_wiring_selftest.py` 把 AI 角色搬了家**：
   它自称"不吃游戏"，实际 `intent do` 会走到**真的** `_walk_to_chest` ——
   链路：`_intent_wiring_selftest.py:343/900` → `intent do` → `_im_chest_op` → `_walk_to_chest`
   → `api.state()`（**读真游戏**：测试只桩了 `_ai_get`/`_ai_post`，没桩 `_get`）
   → `navigation._walk_and_wait`（**真走位**）→ 走位炸了兜底 `api.position()`（`POST /position` **瞬移**）。
   结果：轮回从 `Farm (48,42)` 被搬到 `Farm (11,12)`（夹具里箱子坐标 + 真实地图）。
   ⚠️ **直连 :7842/:7843 不经 MCP** ⇒ 症状是「**MCP 工具日志里一次调用都没有，人却换了地方**」。
   已封：`_stub()` 里把 `_get`/`_post`/`_walk_to_chest`/`_walk_and_wait`/`api.position` 全接上桩。
2. **更早**：`_sit_selftest` 也记过一条「自称不打游戏、其实偷偷依赖游戏在场」。

⇒ 光"记得"没用：出口上锁 + 闸自己会报红（本文件第 ① 组就是证明它会咬人）。

## 本文件查三件事（都不吃游戏）

① **闸真的会咬人**：桩一个"连游戏端口"的尝试 ⇒ 必须**抛**且**记一笔**（带仓库内调用栈）。
② **白名单 ↔ 账本对齐**：`_net_guard.ALLOW` 的每个文件都要在 `LEDGER` 里有"为什么/读还是写"，
   反过来 `LEDGER` 里也不许有白名单外的文件（两张表漂了 = 有人偷偷放行）。
③ **今天这一轮零违规**：本进程（= 一个自验）跑下来 `violations()` 必须为空 ——
   也就是说本文件的 import 期就已经上了闸（`stardew_api` → `maybe_arm`）。

用法: PYTHONIOENCODING=utf-8 python scripts/_net_guard_selftest.py     （退出码 全过=0）
"""
import io
import os
import socket
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _net_guard as G          # noqa: E402
# ⚠️ **必须像别的自验一样 import `stardew_api`**：闸是它 import 期自动上的（`maybe_arm()`）——
#    这里要是自己 `G.arm()`，测的就是"手动上闸"，**等于绕开了被测的那条路**。
import stardew_api as _api      # noqa: E402,F401

FAILS = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)
    return bool(cond)


def main():
    # ① 闸真的会咬人（**先证明它会咬，再谈它放行了什么**）
    ck("在这个自验里闸是**自动上**的（`stardew_api` 的 `maybe_arm()`）", G.ARMED is True)
    _n0 = len(G.HITS)
    _raised = False
    try:
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", G.GAME_PORTS[1]))     # 7843：操作端口
        finally:
            s.close()
    except ConnectionRefusedError as e:
        _raised = "不许出网" in str(e)
    except Exception as e:                                # 真连上了/别的错都不算"咬到了"
        _raised = False
        print(f"     （非预期异常：{type(e).__name__}: {e}）")
    ck("连游戏端口 ⇒ **当场拒绝**（抛 ConnectionRefusedError，带下一步）", _raised)
    _hit = G.HITS[-1] if len(G.HITS) > _n0 else {}
    ck("而且**记了一笔**，带仓库内调用栈（好定位是哪一行）",
       bool(_hit) and any("_net_guard_selftest.py" in f for f in _hit.get("stack") or []),
       _hit.get("stack"))
    ck("这一笔算**违规**（本文件不在白名单里 —— 默认拒绝）",
       bool(G.violations()))

    # 非游戏端口**不许**被误伤（闸只锁游戏那两个口；MCP :8000 / 普通网络照旧）
    _ok_other = True
    try:
        _s = socket.socket()
        _s.settimeout(0.2)
        try:
            _s.connect(("127.0.0.1", 9))          # discard 端口：多半被拒，但**不该是我们的闸拒的**
        except ConnectionRefusedError as e:
            _ok_other = "不许出网" not in str(e)
        except Exception:
            pass
        finally:
            _s.close()
    except Exception:
        pass
    ck("闸**只锁游戏端口**（7842/7843），别的一律不碰", _ok_other)

    # ② 白名单 ↔ 账本对齐（漂了就报红）
    _allow = set(G.ALLOW)
    _ledger = {row[0] for row in G.LEDGER}
    ck("白名单里每个文件，账本里都有『为什么 + 读还是写』",
       _allow <= _ledger, sorted(_allow - _ledger))
    ck("账本里没有白名单外的文件（不许偷偷放行）", _ledger <= _allow,
       sorted(_ledger - _allow))
    _bad_kind = [r[0] for r in G.LEDGER if r[2] != "读"]
    ck("账本里**只有『读』**（写世界的一律不许进白名单）", not _bad_kind, _bad_kind)
    ck("白名单每一项都写了非空理由", all(str(v).strip() for v in G.ALLOW.values()))
    # ⛔ 已封的那条**不许**回到白名单里（它是这道闸的"案发现场"，只能留在账本的注释里）
    ck("`_intent_wiring_selftest.py` **不在**白名单里（它已封出口，不许再放行）",
       "_intent_wiring_selftest.py" not in _allow)

    # ③ 本进程这一轮：白名单外零连接（②③ 之外的其它自验由 runner 各自的进程自己报红）
    _files = sorted({h["file"] for h in G.HITS})
    print(f"  · 本轮进程里碰过游戏端口的文件: {_files or '（无）'}")
    print(f"  · 白名单放行 {len(G.allowed_hits())} 发 · 拒绝 {len(G.violations())} 发"
          f"（含上面那发故意的桩）")

    print(f"\n{'✅ 全过' if not FAILS else '❌ 红的: ' + '、'.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
