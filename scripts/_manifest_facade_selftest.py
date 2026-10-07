"""🏷️ 门面钉子：manifest 的"身份证"别被改回原作（纯离线）。

起因（恒 2026-10-07）：「**我们的模组真的改名了吗**？我这里看游戏文件夹里还是 nagibridge，
会不会跟原作者撞啊。」—— 查证结论：SMAPI 的身份是 manifest 的 `UniqueID`（**不是文件夹名**），
早在 2026-09-08 就从原作的 `Nagi.NagiBridge` 换成了 `pendel001.NagiBridge` ⇒ 不撞；
但 manifest 里**漏了原作署名**，这次补上（Author/Description）。

这条钉子就是防"哪天被谁改回原作的 ID 或把署名抹掉"。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = os.path.join(ROOT, "manifest.json")
GAME = [r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley\Mods\NagiBridge\manifest.json",
        r"F:\Stardew Valley 2nd\Mods\NagiBridge\manifest.json"]
ORIG_ID = "Nagi.NagiBridge"

FAIL = []


def ck(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAIL.append(name)


try:
    d = json.load(open(REPO, encoding="utf-8"))
except Exception as e:  # noqa: BLE001
    print(f"❌ manifest.json 读不了/不是合法 JSON: {e}")
    sys.exit(1)

print(f"\n① 仓库 manifest：{REPO}")
ck("UniqueID 已**不是**原作 ID", d.get("UniqueID") != ORIG_ID, d.get("UniqueID"))
ck("UniqueID = pendel001.NagiBridge", d.get("UniqueID") == "pendel001.NagiBridge", d.get("UniqueID"))
ck("UpdateKeys 是空的（不会被更新器拉成原版覆盖）", d.get("UpdateKeys") == [], str(d.get("UpdateKeys")))
ck("Author 里有本人（恒）", "恒" in (d.get("Author") or ""), d.get("Author"))
ck("Author 里点名了原作", "原作" in (d.get("Author") or "") and "Nagi" in (d.get("Author") or ""), d.get("Author"))
ck("Description 点名原作 + 写明两个 ID 的关系",
   "原作" in (d.get("Description") or "") and ORIG_ID in (d.get("Description") or ""),
   (d.get("Description") or "")[:80])
ck("EntryDll 还是 NagiBridge.dll（文件夹/DLL 名本来就不用改）",
   d.get("EntryDll") == "NagiBridge.dll", d.get("EntryDll"))

print("\n② 游戏里那两份（有就比一比，必须逐字一致）")
for p in GAME:
    if not os.path.exists(p):
        print(f"  ⏭  没有 {p}（跳过）")
        continue
    same = open(p, "rb").read() == open(REPO, "rb").read()
    ck(f"{os.path.dirname(p).split(chr(92))[0]} 盘那份与仓库一致", same, p)

print()
if FAIL:
    print(f"❌ {len(FAIL)} 项不过：" + " / ".join(FAIL))
    sys.exit(1)
print("✅ 门面（名字/署名/ID）：全部通过")
