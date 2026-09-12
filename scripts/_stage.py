# -*- coding: utf-8 -*-
"""🎬 摆场小工具（2026-09-12）—— 只给**测试摆场**用，不进游戏逻辑。

**为什么写**：批次 C 我用 `python -c "import stardew_api as api; api._post('/give',…)"` 发测试物品，
结果 60 个木料进了**恒**的背包。原因：`_post` 打的是 `BASE_URL`（默认 7842 = host），
不是 AI —— MCP 工具内部都先调 `ensure_roles()` 把端口摆正，我裸调绕过了那一步。
`ensure_roles()` 会把 `BASE_URL` 和 `AI_BASE_URL` **一起**指到 AI 端口，所以正确姿势就一条：

    ⚠️ **先 ensure_roles()，再发请求。** 别裸调 `_post`。

本脚本把这条固化下来，让摆场命令一眼看得出打给谁。

用法:
    python _stage.py who                      # 打印当前角色映射（先自查）
    python _stage.py give '(O)388' 60         # 发物品给 AI
    python _stage.py inv Wood                 # 看 AI 背包里某物
    python _stage.py ground                   # 看 AI 当前图地面掉落
    python _stage.py drop Wood 10             # 丢给 AI（⚠️ 会立刻被自己捡回，仅测试用）
"""
import io
import json
import os
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stardew_api as api          # noqa: E402


def _boot():
    """🔒 唯一入口：先摆正端口，再把映射打出来（看不见映射就别动手）。"""
    r = api.ensure_roles()
    if not r.get("ok"):
        print(f"❌ 角色探测不完整：{r.get('error')} —— 两个角色都进世界了吗？")
        sys.exit(1)
    if r.get("solo"):
        print("🔴 solo：AI 和 host 是同一个进程 —— 现在发什么都打在房主身上，停手。")
        sys.exit(1)
    print(f"🎯 AI={r['ai']['port']}({r['ai'].get('name')})  "
          f"host={r['host']['port']}({r['host'].get('name')})  "
          f"⇒ BASE_URL={api.BASE_URL}")
    return r


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "who":
        _boot()
        return 0
    _boot()                                   # 其余命令一律先摆正端口
    if cmd == "give":
        iid = sys.argv[2]
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        print(json.dumps(api._ai_post("/give", {"id": iid, "count": n}), ensure_ascii=False))
    elif cmd == "inv":
        want = sys.argv[2] if len(sys.argv) > 2 else ""
        st = api._ai_get("/state")
        hits = [(i, it) for i, it in enumerate(st.get("inventory") or [])
                if it and (not want or it.get("name") == want)]
        for i, it in hits:
            print(f"  第{i}格  {it.get('name')} x{it.get('stack')}")
        if not hits:
            print(f"  （背包里没有「{want}」）")
    elif cmd == "ground":
        print(json.dumps(api._ai_get("/debris"), ensure_ascii=False))
    elif cmd == "drop":
        name = sys.argv[2]
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        print(json.dumps(api._ai_post("/drop_item", {"item": name, "count": n}), ensure_ascii=False))
    else:
        print(f"❌ 不认识 {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
