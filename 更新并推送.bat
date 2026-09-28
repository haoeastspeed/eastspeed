@echo off
cd /d "%~dp0"
"C:\Program Files\Git\cmd\git.exe" add -A
"C:\Program Files\Git\cmd\git.exe" commit -m "update site"
"C:\Program Files\Git\cmd\git.exe" push origin main
echo PUSH_EXIT=%ERRORLEVEL%
pause