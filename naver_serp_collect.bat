@echo off
chcp 65001 >nul
cd /d "%~dp0"
python tools\naver_serp_collect.py %*
echo.
pause
