# -*- coding: utf-8 -*-
"""NagiBridge 保姆级启动器——组件检测 + 全自动补齐（MCP 启动前的清单检查）。
launcher（启动NagiBridge.bat）先跑本脚本：查 Python/依赖库/SMAPI/mod 部署/Fishbot/局域网IP/防火墙，
缺的尽量自动装（pip 装库 + 复制 DLL 到 C+F），只有必须人工的（SMAPI 本体/第三方 Fishbot/管理员防火墙）才给指引。
用法: python scripts/launcher_check.py   （退出码：全OK=0，有必须人工的=1）
输出末尾给「手机/Claude Code 连: http://<IP>:8000/mcp」+ 防火墙状态。
"""
import os
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
# NagiBridge.csproj 里硬编码的游戏启动路径（两盘都要）：
GAME_DIRS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Stardew Valley",
    r"F:\Stardew Valley 2nd",
]
REQUIRED_PKGS = ["mcp", "requests"]          # mcp=服务器必需, requests=stardew_api
OPTIONAL_PKGS = ["PIL"]                       # 截图降采样用, 缺也能跑
FIREWALL_RULE = f"NagiBridge MCP {MCP_PORT}"


def _ok(msg): return (True, msg)
def _no(msg):  return (False, msg)


def _wrap(*args):
    """subprocess 跑命令, 返回 (returncode, stdout, stderr)。"""
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
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
    code, out, err = _wrap(sys.executable, "-m", "pip", "install", "-U", *missing)
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
    hint = (
        "✗ 找不到 SMAPI（StardewModdingAPI.exe）。SMAPI 是独立安装器, 需手动装:\n"
        "  1. 官网 https://smapi.io 下载 SMAPI-installer\n"
        "  2. 解压运行 'install on Windows.bat', 选你的游戏目录:\n"
        + "\n".join(f"     - {gd}" for gd in GAME_DIRS)
    )
    return _no(hint)


# ── 4. mod 部署（缺/旧→自动复制 bin 产物到 C+F, 旧备份成 .bak-日期）──
def check_mod():
    src = os.path.join(ROOT_DIR, "bin", "Release", "net6.0", "NagiBridge.dll")
    if not os.path.exists(src):
        return _no("✗ 本地无编译产物 bin/Release/net6.0/NagiBridge.dll——先 dotnet build -c Release（不自动 build, 避免卡）")
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
            print(f"  {os.path.basename(gd)}: 部署失败({e})")
    if deployed:
        msg = "mod 部署 ✓ " + ", ".join(deployed)
        if changed:
            msg += "（本轮已同步）"
        return _ok(msg)
    # mod 没部署成功 → 人工
    return _no(f"✗ mod 未部署到两盘（{', '.join(os.path.basename(g) for g in GAME_DIRS)}/Mods/NagiBridge）——请确认 dll 手动复制")


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
    code, out, _ = _wrap("netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}")
    if code == 0 and f"name={FIREWALL_RULE}" in out:
        return _ok(f"防火墙 {FIREWALL_RULE} 已放行 ✓")
    # 没有 → 自动添加（需管理员）
    print(f"  端口 {MCP_PORT} 防火墙未放行 → 尝试自动添加规则…")
    add = _wrap("netsh", "advfirewall", "firewall", "add", "rule",
                f"name={FIREWALL_RULE}", "dir=in", "action=allow", "protocol=TCP", f"localport={MCP_PORT}")
    if add[0] == 0:
        return _ok(f"防火墙已添加 {FIREWALL_RULE} ✓")
    return _no(f"✗ 防火墙自动添加失败（需管理员权限）。请以管理员运行 CMD 粘贴:\n"
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
    checks = [
        ("Python 版本", check_python()),
        ("Python 依赖", check_pip_pkgs()),
        ("SMAPI 本体", check_smapi()),
        ("mod 部署", check_mod()),
        ("Fishbot", check_fishbot()),
    ]
    flags = []
    for label, (ok, msg) in checks:
        mark = "✓" if ok else "✗"
        print(f"[{mark}] {label}: {msg}")
        if not ok:
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
