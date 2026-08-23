@echo off
rem ============================================================
rem  NagiBridge 保姆级启动器 —— 双击一键：自检组件 → 启动 MCP
rem  (c) 恒 2026-08-23
rem  用法: 双击本文件。先自动检测 Python/依赖库/SMAPI/mod/Fishbot/
rem        防火墙 + 局域网IP, 缺则尽量自动装; 检测通过后前台启动 MCP。
rem  停止: 在窗口按 Ctrl+C。
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo.
echo  [1/2] NagiBridge 组件自检 ...
echo.
python scripts\launcher_check.py
if errorlevel 1 (
    echo.
    echo  ⚠️ 上面有标 ✗ 的项需先处理（本窗口已暂停, 按任意键关闭）。
    pause >nul
    exit /b 1
)

echo.
echo  [2/2] 启动 MCP 服务器（地址见上方, Ctrl+C 停止）...
echo.
python scripts\nagi_mcp_server.py

echo.
echo  MCP 服务器已停止。
pause
