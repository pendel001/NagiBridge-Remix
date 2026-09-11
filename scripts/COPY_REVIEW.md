# 🗣️ 返回文案预筛报告（2026-09-11）

> **这次评的是**：`session_log.jsonl` 里的真实往返，配齐「AI 当时看得到的 17 个工具 + help(域) + 这次调用 + 返回原文」，
> 交给**空上下文 subagent** 判——它没读过本仓库源码，是"看不看得懂"唯一有效的评委（写代码的人/读过码的 AI 看什么都懂）。
> **⚠️ 这只是筛子，不是判官**：终判归手机端真机 AI。这里挑出来的都是"值得让真机再多看一眼"的。

出题：`python _copy_review.py --build`　｜　题包：`_copy_review_packets.json`
本轮评了 6 道（check role / check worn / check storage / map lookup / map query / menu recipes）。

---

## 🔴 真 bug（已修）

### 1. `check(what="worn")` 把整个 .NET 对象甩给 AI —— 已修
评委原话：
> `🔮 饰品: FairyBox (DisplayName=[LocalizedText Strings\1_6_Strings:FairyBox_Name], Description=[…], Texture=TileSheets\Objects_2, SheetIndex=74, TrinketEffectClass=StardewValley.Objects.Trinkets.FairyBoxTrinketEffect, DropsNaturally=True, CanBeReforged=True, CustomFields=None, ModData=None)` 这一整行（看不懂）

C# 送来的 `effect` 其实是**整个物品对象**，代码却当"效果"拼进了括号，**400+ 字符全是反射噪声**。

修：加 `_TRINKET_META_KEYS`（物品元数据黑名单）+ `_readable_effect()`（长度/路径/类型名过滤）。
真机复验：`🔮 饰品: FairyBox`。
⏳ **正解在 C#**：直接送一句人话效果描述（要重编 DLL，等游戏关了一起做）。

### 2. `map` 的工具描述漏了 `query` —— 已修（发现性）
评委原话：
> ① 的 map 工具清单只有 go/walk/movetile/lookup/npc/warp_safe、**没列 query**，而我这次正是靠 query 查的

`map` 描述里列了 7 个 op 中的 6 个，**漏掉的那个读起来像不存在**。`help(map)` 里有，但 AI 不会为一个"看着已经列全了"的工具去翻 help。
已补上 `query 功能反查("哪能买X")`。

> 📌 **这类"列举了一部分、读起来像全部"的坑没有自动检查**：`_guide_orphan_check.py` 只查 `help(域)` 正文，
> 管不到工具**描述**。已记进 CHANGELOG 待办。

---

## 🟡 文案待改（都是真机 AI 会踩的）

| # | 出处 | 评委原话（节选） | 判断 |
|---|---|---|---|
| 3 | `check role` 状态条 | `🎲 -0.010`（**没单位、没说是啥**） | 待查：这数字是什么？运气？ |
| 4 | `check role` 状态条 | `❄️雪 · ❄️Winter`（**同一件事说了两遍**） | 可去重 |
| 5 | `check role` 状态条 | `📅 🏪休: 鱼店 (Willy)`（"**休**"是缩写，猜是休息/关门） | 缩写要展开 |
| 6 | `check role` 状态条 | `🛠️ 可用域: farm` 和上面列出的 **17 个域自相矛盾** | 实为"**本图**可用域"，字面确实打架 |
| 7 | `check storage` | `[5/36]`、`剩余 208 格` **都不说单位**；`⬜/🟩绿` 色块**没有图例** | 补单位 + 图例 |
| 8 | `check storage` | help 写 `chests(chest=N 看第N个箱)`，但**返回里只有坐标 `(58,17)` 没有箱子编号** —— 我不知道这个 N 该填几 | **真断裂**：编号没出口，参数就白设计了 |
| 9 | `check storage` | `Iridium Rodx1`（**物品名和数量粘连**，容易读成 Rodx 这个道具） | 分隔符 |
| 10 | `map lookup` | 出口那行：「原出口瓦片标(80,15-18)…**根因**…**同 Farm→Backwoods 修正**」——**代码改动史**，工具使用者不需要也没法懂 | 该删（开发注释漏进 AI 视野） |
| 11 | `map lookup` | `🟢`/`🚪` 和 `(warp)`/`(door)` **两组标记没有任何说明** | 补图例 |
| 12 | `map query` | `SeedShop: 柜台买种子…` 和 `Town: 皮埃尔商店(种子/肥料)` **看着是同一家店却并列为两个候选**，不知道该把哪个传给 go | 去重或点明关系 |
| 13 | `menu recipes` | `❌ 缺材料（78 个）` —— **这个 ❌ 到底是"出错"还是"分组标签"？**；且「共 78 个食谱」和「缺材料（78 个）」两个 78 叠一起 | ⚠️ 同一个 ❌ 还害得**自动打勾误判过**（见下） |

---

## 📎 附带收获：这套流程当场抓到了**打勾工具自己**的一个 bug

`menu recipes` / `menu craftables` 起初被自动判成 ❌，理由是"返回里有 `❌`"。
但那是**工具在告诉 AI 缺哪些材料**，不是它自己失败了 —— **判定规则误报**。

已收紧成结构化判据（`startswith("❌")` / `❌ op「` / `⚠️ op「` / `Traceback`），
17 ✅ 里的 2 个冤枉红已纠正为 ✅。
有意思的是：**空上下文评委独立地也把那个 `❌` 标成了"看不懂"** —— 同一个歧义，两个角度都撞上了，
说明它确实该改（改文案，不是改判据）。
