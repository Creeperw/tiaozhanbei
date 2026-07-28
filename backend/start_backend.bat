@echo off
cd /d e:\15971\1_比赛项目\项目\tiaozhanbei\backend
for /f "tokens=5" %%i in ('netstat -ano ^| findstr ":7860" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%i >nul 2>&1
)
echo Starting backend...
set COMPETITION_APP_MODE=stub
python -m competition_app.cli.app serve
pause