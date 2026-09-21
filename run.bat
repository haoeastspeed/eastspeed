@echo off
rem 东方神速 DongFang Speed 启动脚本（双击运行，源码方式）
cd /d "%~dp0"
python main.py %*
if errorlevel 1 (
    echo.
    echo 启动失败，请确认已安装 Python 3.10+ 并执行过: pip install -r requirements.txt
    echo 提示：BT/磁力需 Python 3.11/3.12；普通用户可直接使用 dist 目录下的 exe。
    pause
)
