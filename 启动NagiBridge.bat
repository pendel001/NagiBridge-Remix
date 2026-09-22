@echo off
setlocal
rem ================================================================
rem  NagiBridge one-click launcher  (c) ? 2026-08-23
rem  DOUBLE-CLICK to: auto-check deps -> start the MCP server.
rem  Port-busy / dep / firewall checks are done by
rem  scripts\launcher_check.py (a busy port makes it
rem  stop harmlessly, so double-clicking again is safe).
rem  STOP this window: press Ctrl+C (the server stops with it).
rem  KEEP THIS FILE NEXT TO the scripts\ folder -- it locates the repo
rem  via %~dp0 (its own location), so a COPY moved to the Desktop (or
rem  anywhere else) cannot find scripts\.  For a desktop icon use a
rem  SHORTCUT: right-click this .bat -> Send to -> Desktop (shortcut).
rem  This .bat is pure-ASCII ON PURPOSE.  CMD mis-parses a batch file
rem  that mixes `chcp 65001` with multi-byte text: the byte offset it
rem  seeks by drifts on every multi-byte char, and once it lands inside
rem  one the REST OF THE FILE gets split into bogus commands (reproduced
rem  2026-09-22 -- it kicked in at the 8th Chinese line, and moving the
rem  text around just moved the breakage elsewhere).  All Chinese output
rem  therefore lives in scripts\launcher_check.py (UTF-8).  Keep this
rem  file ASCII-only; do not "improve" it with Chinese echoes.
rem ================================================================
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
chcp 65001 >nul

if not exist "scripts\launcher_check.py" goto :no_root
where python >nul 2>nul || goto :no_python

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
exit /b 0

:no_root
echo.
echo  [X] scripts\launcher_check.py NOT FOUND.
echo.
echo      This .bat must stay inside the NagiBridge folder, right next to
echo      the scripts\ folder.  It finds the repo via its own location:
echo          %~dp0
echo      so a COPY moved somewhere else (the Desktop, say) has nothing
echo      to look at.
echo.
echo      How to fix:
echo        1) Move this .bat back into the NagiBridge folder, then
echo           double-click it there.
echo        2) Want it on the Desktop? Right-click the .bat -^> Send to -^>
echo           Desktop (create shortcut).  A shortcut may sit anywhere.
echo.
pause
exit /b 1

:no_python
echo.
echo  [X] 'python' was not found in PATH.
echo.
echo      Install Python 3.10 or newer: https://www.python.org/downloads/
echo      On the FIRST setup screen, TICK "Add python.exe to PATH".
echo      Already installed but missed the tick? Re-run the installer and
echo      pick Modify, then re-open this window and double-click again.
echo      Verify in a NEW Command Prompt with:   python -V
echo.
pause
exit /b 1
