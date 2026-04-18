@echo off
chcp 65001 >nul
title Учёт объектов - Лаунчер
color 0A

echo.
echo ╔══════════════════════════════════════════╗
echo ║     УЧЁТ ОБЪЕКТОВ И СМЕТЫ - Лаунчер     ║
echo ╚══════════════════════════════════════════╝
echo.

:: Проверяем, работает ли уже сервер
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/login' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; Write-Host '✓ Сервер уже работает!'; exit 0 } catch { exit 1 }" 2>nul
if %errorlevel%==0 (
    goto open_browser
)

:: Запускаем сервер
cd /d "%~dp0"
echo ⚙ Запуск сервера...
start "ObjectAccounting_Server" /min python app_objects.py

:: Ждём пока сервер станет доступным (макс 15 секунд)
set /a attempts=0
:wait_loop
timeout /t 1 /nobreak >nul
set /a attempts+=1
set /a remaining=15-attempts
echo   Ожидание... осталось %remaining% сек

powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5000/login' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop; exit 0 } catch { exit 1 }" 2>nul
if %errorlevel%==0 goto server_ready

if %attempts% geq 15 goto server_timeout
goto wait_loop

:server_ready
echo.
echo ✓ Сервер успешно запущен!
echo.
goto open_browser

:open_browser
echo 🌐 Открываю браузер...
timeout /t 1 /nobreak >nul
start http://127.0.0.1:5000/
echo.
echo ╔══════════════════════════════════════════╗
echo ║     Приложение успешно запущено!        ║
echo ╚══════════════════════════════════════════╝
echo.
timeout /t 3 /nobreak >nul
exit

:server_timeout
echo.
echo ✗ ОШИБКА: Сервер не запустился за 15 секунд!
echo.
echo Возможные решения:
echo   1. Проверьте установку Python: python --version
echo   2. Установите зависимости: pip install -r requirements.txt
echo   3. Запустите вручную: python app_objects.py
echo.
pause
exit