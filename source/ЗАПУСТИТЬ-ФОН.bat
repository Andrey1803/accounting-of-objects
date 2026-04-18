@echo off
chcp 65001 >nul
title Учёт объектов (фоновый режим)

echo Запуск сервера в фоновом режиме...
cd /d "%~dp0"
start /B pythonw app_objects.py > NUL 2>&1

:: Ждём пока сервер станет доступным
echo Ожидание...
set /a attempts=0
:wait_loop
timeout /t 1 /nobreak >nul
set /a attempts+=1

powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/login' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; exit 0 } catch { exit 1 }" 2>nul
if %errorlevel%==0 goto server_ready

if %attempts% geq 15 goto server_timeout
goto wait_loop

:server_ready
echo Сервер запущен!
timeout /t 1 /nobreak >nul
start http://127.0.0.1:5000
echo.
echo Для остановки: taskkill /F /IM pythonw.exe
exit

:server_timeout
echo.
echo ОШИБКА: Сервер не запустился!
echo Попробуйте обычный ЗАПУСТИТЬ.bat
echo.
pause
exit
