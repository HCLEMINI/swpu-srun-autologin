@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo  打包 校园网自动登录 -> SrunAutoLogin.exe
echo ========================================
echo.

echo [1/2] 安装/更新依赖(PyInstaller + 托盘库) ...
python -m pip install --upgrade pyinstaller pystray pillow
if errorlevel 1 ( echo pip 安装失败 & pause & exit /b 1 )
echo.

echo [2/2] 打包中(单文件, 无控制台) ...
python -m PyInstaller --noconfirm --onefile --windowed --clean ^
    --hidden-import=pystray._win32 ^
    --collect-submodules pystray ^
    --name SrunAutoLogin srun_gui.py
echo.

if exist "dist\SrunAutoLogin.exe" (
    echo ========================================
    echo  完成! 输出: %CD%\dist\SrunAutoLogin.exe
    echo  首次运行请填账号密码并点[保存设置]
    echo ========================================
) else (
    echo !!! 打包失败, 请查看上方日志
)
pause
