@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ========================================
echo  打包 校园网自动登录 (UPX 压缩版, 约15MB)
echo ========================================
echo.

echo [1/3] 安装/更新依赖(PyInstaller + 托盘库) ...
python -m pip install --upgrade pyinstaller pystray pillow
if errorlevel 1 ( echo pip 安装失败 & pause & exit /b 1 )
echo.

echo [2/3] 获取 UPX 压缩工具(可选, 镜像下载, 失败则跳过) ...
python fetch_upx.py
echo.

echo [3/3] 打包中(单文件, 无控制台, 排除无用重模块 + UPX 压缩) ...
set UPXARG=
for /d %%D in (upx_tool\upx-*-win64) do set UPXARG=--upx-dir %%D
python -m PyInstaller --noconfirm --onefile --windowed --clean ^
    --hidden-import=pystray._win32 ^
    --collect-submodules pystray ^
    %UPXARG% ^
    --exclude-module numpy ^
    --exclude-module scipy ^
    --exclude-module pandas ^
    --exclude-module matplotlib ^
    --exclude-module test ^
    --exclude-module tkinter.test ^
    --exclude-module unittest ^
    --exclude-module pydoc ^
    --exclude-module doctest ^
    --name SrunAutoLogin srun_gui.py
echo.

if exist "dist\SrunAutoLogin.exe" (
    for %%I in ("dist\SrunAutoLogin.exe") do echo 完成: dist\SrunAutoLogin.exe  ^(%%~zI 字节^)
    echo 首次运行请填账号密码并点[保存设置]
) else (
    echo !!! 打包失败, 请查看上方日志
)
pause
