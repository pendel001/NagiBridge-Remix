# -*- coding: utf-8 -*-
"""NagiBridge 保姆级启动器——组件检测 + 全自动补齐（MCP 启动前的清单检查）。
launcher（启动NagiBridge.bat）先跑本脚本：查 Python/依赖库/SMAPI/mod 部署/Fishbot/局域网IP/防火墙，
缺的尽量自动装（pip 装库 + 复制 DLL 到 C+F），只有必须人工的（SMAPI 本体/第三方 Fishbot/管理员防火墙）才给指引。
用法: python scripts/launcher_check.py   （退出码：全OK=0，有必须人工的=1）
输出末尾给「手机/Claude Code 连: http://<IP>:8000/mcp」+ 防火墙状态。
"""
import os
import re
import sys
import io
import socket
import shutil
import subprocess
from datetime import datetime

# ⚠️ Windows GBK 控制台 + 中文/emoji 会崩 → 强制 stdout/stderr 走 utf-8（照 test_mapgo.py:18 模式）
if sys.stdout.encoding and sys.stdout.encoding.lower().startswith("gbk"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)

# ── 配置（可从环境变量覆盖）──
MCP_PORT = int(os.environ.get("NAGI_MCP_PORT", "8000"))
MCP_HOST = os.environ.get("NAGI_MCP_HOST", "0.0.0.0")
REQUIRED_PKGS = ["mcp", "requests"]          # mcp=服务器必需, requests=stardew_api
OPTIONAL_PKGS = ["PIL"]                       # 截图降采样用, 缺也能跑
FIREWALL_RULE = f"NagiBridge MCP {MCP_PORT}"


# ── 游戏目录探测（2026-09-22 开源普适性）──
# ⚠️ 以前这里写死 `C:\Program Files (x86)\Steam\...` + `F:\Stardew Valley 2nd` 两条（恒本机的两盘）。
#    后果：**别人把游戏装在别的盘/别的 Steam 库，三项检查全 ✗ → 退出码 1 → .bat 直接不给启动服务器**，
#    还指引他去 C 盘那个并不存在的目录装 SMAPI（指错路）。而 csproj 那边的 ModBuildConfig
#    早就会自己找游戏了（find-game-folder.targets：注册表 + Steam 库 + 各盘常见路径）——只有这层漏了。
# 顺序：① NAGI_GAME_DIRS 环境变量（分号/逗号分隔，最高优先，给"探测不到"当逃生口）
#       ② 注册表 + Steam 库自动探测  ③ 已知候选兜底
# ⚠️ 探测结果**必须打印出来**（见 main）：宁可让人一眼看出"找错了"，也别静默挑一个用。
_GAME_DIR_FALLBACK = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley",   # Steam 默认位置
    r"F:\Stardew Valley 2nd",                                          # 恒本机的手动副本（别人机器上没有，自动跳过）
]


def _reg_value(root, subkey, name):
    """读一条注册表值；非 Windows / 没有该项 → None（不抛）。"""
    try:
        import winreg
        with winreg.OpenKey(root, subkey) as k:
            return winreg.QueryValueEx(k, name)[0]
    except Exception:
        return None


def _steam_game_dirs():
    """Steam 装的星露谷可能在任意盘的任意 Steam 库里，逐个库翻一遍。"""
    out = []
    try:
        import winreg
    except Exception:
        return out
    # ① 卸载表里直接有安装路径（最省事、最准）
    p = _reg_value(winreg.HKEY_LOCAL_MACHINE,
                   r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App 413150",
                   "InstallLocation")
    if p:
        out.append(p)
    # ② Steam 库列表 libraryfolders.vdf（游戏装在 D:\SteamLibrary 这种要靠它）
    steam = _reg_value(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam", "SteamPath")
    if steam:
        out.append(os.path.join(steam, "steamapps", "common", "Stardew Valley"))
        vdf = os.path.join(steam, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                libs = re.findall(r'"path"\s+"([^"]+)"', f.read())
            for lib in libs:
                out.append(os.path.join(lib.replace("\\\\", "\\"), "steamapps", "common", "Stardew Valley"))
        except Exception:
            pass
    return out


def _gog_game_dir():
    try:
        import winreg
        return _reg_value(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GOG.com\Games\1453375253", "PATH")
    except Exception:
        return None


def _detect_game_dirs():
    """本机「可能装着星露谷」的目录列表（去重、按可信度排序、只留真的存在的）。"""
    cands = []
    env = os.environ.get("NAGI_GAME_DIRS", "").strip()
    if env:
        cands += [p.strip().strip('"') for p in re.split(r"[;,]", env) if p.strip()]
    cands += _steam_game_dirs()
    cands.append(_gog_game_dir())
    cands += _GAME_DIR_FALLBACK
    seen, out = set(), []
    for p in cands:
        if not p:
            continue
        p = os.path.normpath(p)
        if p.lower() in seen:
            continue
        seen.add(p.lower())
        if os.path.isdir(p):      # 只留真的存在的目录（不存在=不是这台机器的游戏）
            out.append(p)
    return out


GAME_DIRS = _detect_game_dirs()
_GAME_DIRS_HINT = (
    "没探测到游戏目录。请设环境变量指路（分号分隔），再双击一次：\n"
    '  set NAGI_GAME_DIRS=D:\\Games\\Stardew Valley\n'
    "（或在系统设置里加一个用户环境变量 NAGI_GAME_DIRS）"
)


def _ok(msg): return (True, msg)
def _no(msg):  return (False, msg)


def _wrap(*args, encoding=None):
    """subprocess 跑命令, 返回 (returncode, stdout, stderr)。

    ⚠️ `encoding` **只给 python 子进程传 "utf-8"**，别一刀切（2026-09-12 恒拍板）：
    - python 子进程（`pip`）继承启动器 .bat 的 `PYTHONIOENCODING=utf-8`，**吐 UTF-8**；
      而 `text=True` 默认按**本地编码(GBK)** 解 —— 解不动时 `subprocess` 的**读取线程直接死掉**，
      `r.stdout/stderr` 变成 **None**（**不抛异常、returncode 照样有效**，静默得离谱）。
      给 pip 补上 encoding 后，失败时才能看到 pip 的报错原文。
    - `ipconfig`/`netsh` 是**原生程序**，吐的是 **GBK**，`text=True` 默认解**正好对** ——
      强上 utf-8 反而把它们搞成乱码，所以那几处**不传**。
    注：`r.stdout or ""` 已经兜住了 None，所以调用方不会炸；最坏只是"输出是空的"。
    """
    try:
        extra = {"encoding": encoding, "errors": "replace"} if encoding else {}
        r = subprocess.run(args, capture_output=True, text=True, timeout=60, **extra)
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as e:
        return -1, "", str(e)


# ── 1. Python 版本 ──
def check_python():
    v = sys.version_info
    if (v.major, v.minor) >= (3, 10):
        return _ok(f"Python {v.major}.{v.minor}.{v.micro} ✓")
    return _no(f"Python {v.major}.{v.minor} 太旧（需 3.10+）。请装 3.10+ 并确保 python 在 PATH：python.org/downloads")


# ── 2. Python 依赖库（缺→自动 pip 装）──
def check_pip_pkgs():
    missing = []
    for pkg in REQUIRED_PKGS:
        need = "PIL" if pkg == "PIL" else pkg
        try:
            __import__(need)
        except Exception:
            missing.append(pkg)
    # PIL 可选：缺只提示不装（不阻塞）
    try:
        import PIL  # noqa
        pil_ok = True
    except Exception:
        pil_ok = False
    if not missing:
        msg = "依赖库 ✓"
        if not pil_ok:
            msg += "（PIL 可选,未装,截图不缩放）"
        return _ok(msg)
    to_install = " ".join(missing)
    print(f"  缺依赖库: {missing} → 自动 pip 安装…")
    code, out, err = _wrap(sys.executable, "-m", "pip", "install", "-U", *missing, encoding="utf-8")
    if code == 0:
        return _ok(f"已装 {', '.join(missing)} ✓")
    return _no(f"pip 装 {missing} 失败:\n{err}\n请手动: pip install -U {' '.join(missing)}")


# ── 3. SMAPI（本体, 只能指引不能自动装）──
def check_smapi():
    found = []
    for gd in GAME_DIRS:
        p = os.path.join(gd, "StardewModdingAPI.exe")
        if os.path.exists(p):
            found.append(p)
    if found:
        return _ok("SMAPI ✓ " + ", ".join(os.path.basename(os.path.dirname(p)) for p in found))
    if not GAME_DIRS:
        return _no("✗ " + _GAME_DIRS_HINT)
    hint = (
        "✗ 找到游戏目录但里面没有 SMAPI（StardewModdingAPI.exe）。SMAPI 是独立安装器, 需手动装:\n"
        "  1. 官网 https://smapi.io 下载 SMAPI-installer\n"
        "  2. 解压运行 'install on Windows.bat', 选你的游戏目录:\n"
        + "\n".join(f"     - {gd}" for gd in GAME_DIRS)
    )
    return _no(hint)


# ── 4. mod 部署（缺/旧→自动复制 bin 产物到 C+F, 旧备份成 .bak-日期）──
def check_mod():
    # ⚠️ 2026-08-26 恒：以前写死 bin/Release/net6.0——但 `dotnet build` 不带 -c 出的是 **Debug**，
    #    实际两盘部署的一直都是 Debug 产物。结果这步永远走 "先 dotnet build -c Release" 的死路，
    #    自动部署从来没真正跑过（改了 DLL 以为同步了，其实没有）。
    #    改成 Release/Debug 都找，取**较新**的那个。
    cands = [os.path.join(ROOT_DIR, "bin", c, "net6.0", "NagiBridge.dll") for c in ("Release", "Debug")]
    cands = [p for p in cands if os.path.exists(p)]
    if not cands:
        return _no("✗ 本地无编译产物 bin/{Release,Debug}/net6.0/NagiBridge.dll——先 dotnet build（不自动 build, 避免卡）")
    src = max(cands, key=os.path.getmtime)
    print(f"  产物: bin/{os.path.basename(os.path.dirname(os.path.dirname(src)))}/net6.0/NagiBridge.dll")
    src_m = os.path.getmtime(src)
    deployed = []
    changed = False
    for gd in GAME_DIRS:
        mod = os.path.join(gd, "Mods", "NagiBridge")
        manifest = os.path.join(mod, "manifest.json")
        dll = os.path.join(mod, "NagiBridge.dll")
        if not os.path.exists(manifest):
            print(f"  {os.path.basename(gd)}: Mods/NagiBridge 缺 manifest.json（mod 未部署）")
            changed = True
            continue
        if os.path.exists(dll) and os.path.getmtime(dll) == src_m and os.path.getsize(dll) == os.path.getsize(src):
            deployed.append(os.path.basename(gd))
            continue
        # mod 已存在但旧/缺失 → 备份旧 DLL 再复制
        if os.path.exists(dll):
            bak = dll + f".bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            try:
                shutil.copy2(dll, bak)
                print(f"  {os.path.basename(gd)}: 备份旧 DLL → {os.path.basename(bak)}")
            except Exception as e:
                print(f"  {os.path.basename(gd)}: 备份失败({e})")
        try:
            os.makedirs(mod, exist_ok=True)
            shutil.copy2(src, dll)
            deployed.append(os.path.basename(gd))
            changed = True
            print(f"  {os.path.basename(gd)}: 已部署最新 DLL ✓")
        except Exception as e:
            # 2026-08-26 恒：游戏开着时 DLL 被进程锁住，报 "Device or resource busy"/
            # "另一个程序正在使用此文件"——不是权限问题，关掉游戏再跑就行。
            hint = "（游戏还开着？DLL 被占用，关掉星露谷再跑）" if "busy" in str(e).lower() or "使用" in str(e) or "process" in str(e).lower() else ""
            print(f"  {os.path.basename(gd)}: 部署失败({e}){hint}")
    if deployed:
        msg = "mod 部署 ✓ " + ", ".join(deployed)
        if changed:
            msg += "（本轮已同步）"
        return _ok(msg)
    # mod 没部署成功 → 人工
    if not GAME_DIRS:
        return _no("✗ " + _GAME_DIRS_HINT)
    return _no(f"✗ mod 未部署到（{', '.join(os.path.basename(g) for g in GAME_DIRS)}/Mods/NagiBridge）——请确认 dll 手动复制")


# ── 5. Fishbot（第三方 mod, 只指引不自动装, 不阻塞）──
def check_fishbot():
    found = []
    for gd in GAME_DIRS:
        mods_root = os.path.join(gd, "Mods")
        if not os.path.isdir(mods_root):
            continue
        try:
            for name in os.listdir(mods_root):
                if "fishbot" in name.lower() or "adroslice" in name.lower():
                    found.append(name)
        except Exception:
            pass
    if found:
        return _ok("Fishbot ✓ " + ", ".join(found))
    return _no("Fishbot（AdroSlice.Fishbot, 第三方 SMAPI mod）未装——只在钓鱼自动化(go_fishing/fish_run)需要, "
               "MCP 服务器无它也能启动。要用钓鱼: 去下载放入 <游戏>/Mods/。")


# ── 6. 局域网 IP ──
def get_lan_ip():
    # 1) hostname 解析
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    # 2) ipconfig 解析 IPv4, 跳过虚拟网卡
    try:
        code, out, _ = _wrap("ipconfig")
        if code == 0:
            lines = out.splitlines()
            cur_nic = ""
            for ln in lines:
                s = ln.strip()
                if "adapter" in s.lower() or "适配器" in s:
                    cur_nic = s
                elif s.startswith("IPv4") or "IPv4 地址" in s:
                    if ":" in s:
                        ip = s.split(":")[-1].strip()
                        if ip and not ip.startswith("127.") and \
                           not any(k in cur_nic.lower() for k in ("virtualbox", "vmware", "wsl", "docker", "vethernet", "loopback")):
                            return ip
    except Exception:
        pass
    return "查 ipconfig"


# ── 7. 防火墙（端口放行, 查→没有则自动加; 无管理员则给命令）──
def check_firewall():
    # ⚠️ 2026-09-22 修正判据：以前写的是 `f"name={FIREWALL_RULE}" in out`，但 netsh 打出来的是
    #    「规则名称:   NagiBridge MCP 8000」（英文系统 "Rule Name:"）——**输出里根本不含 `name=` 这串**
    #    ⇒ 判据恒为假 ⇒ 每次启动都报"未放行"并再 add 一次（管理员跑就叠出重复规则）。
    code, out, _ = _wrap("netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}")
    if code == 0 and FIREWALL_RULE in out:
        return _ok(f"防火墙 {FIREWALL_RULE} 已放行 ✓")
    # 没有 → 自动添加（需管理员）
    print(f"  端口 {MCP_PORT} 防火墙未放行 → 尝试自动添加规则…")
    add = _wrap("netsh", "advfirewall", "firewall", "add", "rule",
                f"name={FIREWALL_RULE}", "dir=in", "action=allow", "protocol=TCP", f"localport={MCP_PORT}")
    if add[0] == 0:
        return _ok(f"防火墙已添加 {FIREWALL_RULE} ✓")
    return _no(f"✗ 防火墙未放行（不阻断启动：本机 Claude Code 走 127.0.0.1 不受影响；"
               f"只有手机/别的电脑连才需要）。需管理员运行 CMD 粘贴:\n"
               f"  netsh advfirewall firewall add rule name=\"{FIREWALL_RULE}\" dir=in action=allow protocol=TCP localport={MCP_PORT}")


# ── 8. 端口被占（服务器已在跑 → 别重复双击）──
def check_port_busy():
    """8000 端口是否已被占用（说明 MCP 服务器/别的进程在跑）。
    被占 → 返回 ('busy', msg)：launcher 应停下别再启动第二个服务器（防白痴重复双击）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", MCP_PORT))
            return ("busy", f"端口 {MCP_PORT} 已被占用——MCP 服务器可能已在跑（或别的程序占用）。\n"
                            "别重复双击！关掉这个窗口即可；要重启先停掉旧服务器。")
        except OSError:
            return ("free", f"端口 {MCP_PORT} 空闲 ✓")
        finally:
            s.close()
    except Exception as e:
        return ("free", f"端口检测跳过({e})")


# ── 汇总 ──
def main():
    print("═" * 52)
    print("  NagiBridge 启动前组件自检")
    print("═" * 52)
    # 🔍 游戏装在哪——自动探测的结果**摊开给人看**：宁可一眼看出"找错了"（设 NAGI_GAME_DIRS 覆盖），
    #    也不要静默挑一个用下去。
    if GAME_DIRS:
        print("🔍 游戏目录（自动探测；不对就设 NAGI_GAME_DIRS 覆盖）:")
        for gd in GAME_DIRS:
            print(f"     {gd}")
    else:
        print(f"🔍 游戏目录: {_GAME_DIRS_HINT}")
    print()
    # (标签, (ok, 消息), 是否阻断启动)
    checks = [
        ("Python 版本", check_python(), True),
        ("Python 依赖", check_pip_pkgs(), True),
        ("SMAPI 本体", check_smapi(), True),
        ("mod 部署", check_mod(), True),
        # ⚠️ 2026-09-22：Fishbot 是**可选**的第三方 mod（只有钓鱼自动化用）。以前它 ✗ 会进 flags
        #    ⇒ 退出码 1 ⇒ .bat 拒绝启动服务器 —— 跟它自己那句"MCP 服务器无它也能启动"直接打架，
        #    新用户没装 Fishbot 就双击 = 当场被拦。改成非阻断项。
        ("Fishbot", check_fishbot(), False),
    ]
    flags = []
    for label, (ok, msg), blocking in checks:
        mark = "✓" if ok else ("✗" if blocking else "!")
        note = "（可选项，不阻断启动）" if (not ok and not blocking) else ""
        print(f"[{mark}] {label}: {msg}{note}")
        if not ok and blocking:
            flags.append(label)
    # 🔒 防火墙（单独, 需管理员很常见）
    fw_ok, fw_msg = check_firewall()
    print(f"[{'✓' if fw_ok else '✗'}] 防火墙 8000: {fw_msg}")
    # 🚦 端口被占 → 硬停（防白痴重复双击，别再起第二个服务器）
    pstat, pmsg = check_port_busy()
    if pstat == "busy":
        print()
        print(f"[✗] {pmsg}")
        return 2

    ip = get_lan_ip()
    print()
    print("─" * 52)
    print("  📱 手机 / Claude Code 连（同一网络，Streamable HTTP 不是 SSE）:")
    print(f"      http://{ip}:{MCP_PORT}/mcp")
    print(f"  端口: {MCP_PORT} | 传输: Streamable HTTP | 地址 /mcp 不能漏")
    print("─" * 52)
    if flags:
        print(f"⚠️ 仍有 {len(flags)} 项需人工处理: {', '.join(flags)}（本清单可重跑）")
        return 1
    print("✅ 全部就绪，可以启动 MCP 服务器。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
