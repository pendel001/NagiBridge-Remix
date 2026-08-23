@echo off
setlocal
rem ================================================================
rem  NagiBridge one-click launcher  (c) ? 2026-08-23
rem  DOUBLE-CLICK to: auto-check deps -> start the MCP server.
rem  Port-busy / dep / firewall checks are done by
rem  scripts\launcher_check.py (prints Chinese; a busy port makes it
rem  stop harmlessly, so double-clicking again is safe).
rem  STOP this window: press Ctrl+C (the server stops with it).
rem  This .bat is pure-ASCII (CMD parses it as GBK); Chinese text
rem  is printed by scripts\launcher_check.py (UTF-8).
rem ================================================================
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 >nul

echo.
echo  [1/2] NagiBridge pre-flight checks ...
echo.
python scripts\launcher_check.py
if errorlevel 1 (
    echo.
    echo  Check items marked X, then re-run. Press any key to close.
    pause >nul
    exit /b 1
)

echo.
echo  [2/2] Starting MCP server (URL printed above).
echo        To STOP: press Ctrl+C in this window.
echo.
python scripts\nagi_mcp_server.py

echo.
echo  MCP server stopped. Press any key to close.
pause
endlocal
