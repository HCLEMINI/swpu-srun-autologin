@echo off
set PYTHONUTF8=1
cd /d "%~dp0"
where python >nul 2>nul && (python netdiag.py & goto done)
where py >nul 2>nul && (py netdiag.py & goto done)
python3 netdiag.py
:done
echo.
echo Report saved to: %~dp0netdiag report (.txt)
pause
