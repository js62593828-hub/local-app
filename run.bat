@echo off
cd /d "%~dp0"
echo Installing required packages...
python -m pip install -r requirements.txt
echo Starting dashboard. Your browser will open shortly...
python app.py
pause
