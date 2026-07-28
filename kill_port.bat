@echo off
for /f "tokens=5" %%i in ('netstat -ano ^| findstr ":7860" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%i >nul 2>&1
    echo Killed PID %%i
)
echo Port 7860 freed.