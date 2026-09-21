@echo off
rem 东方神速 一键打包（便携版 + 单文件版）
rem 建议在 Python 3.11/3.12 环境运行，以便把 BT/磁力(libtorrent)、HTTP/2(h2) 内置进 exe
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller pillow
python tools\build_exe.py both %*
echo.
echo 打包完成：
echo   便携版  dist\DongFangSpeed\DongFangSpeed.exe
echo   单文件  dist\DongFangSpeed.exe
echo   安装包  请用 Inno Setup 6 编译 installer\dongfangspeed.iss
pause
