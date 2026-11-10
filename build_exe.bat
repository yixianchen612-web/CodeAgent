@echo off
REM 一键重新打包 CodeAgent.exe（首次运行会自动创建 .venv 并安装 PyInstaller）
cd /d "%~dp0"

if not exist .venv (
    echo [1/3] 创建虚拟环境...
    py -m venv .venv || goto :err
)

echo [2/3] 安装 PyInstaller...
.venv\Scripts\python.exe -m pip install --quiet pyinstaller || goto :err

echo [3/3] 打包 exe...
.venv\Scripts\python.exe -m PyInstaller --onefile --windowed --name CodeAgent --clean --noconfirm main.py || goto :err

echo.
echo 打包完成: dist\CodeAgent.exe
exit /b 0

:err
echo 打包失败，请检查上方错误信息。
exit /b 1
