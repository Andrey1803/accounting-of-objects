@echo off
title ObjectAccounting Server

echo ========================================
echo   ObjectAccounting Server
echo ========================================
echo.

:: Stop old server if running
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

cd /d "%~dp0"

:: Start server
echo  Starting server...
start "ObjectAccounting" /min python app_objects.py

:: Wait for startup
echo  Waiting 10 seconds...
timeout /t 10 /nobreak >nul

:: Open browser
start http://127.0.0.1:5000/
echo.
echo  Server running at http://127.0.0.1:5000/
echo  Close the "ObjectAccounting" window to stop server
echo.
pause
exit
