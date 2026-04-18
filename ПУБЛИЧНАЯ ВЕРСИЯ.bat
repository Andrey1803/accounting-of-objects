@echo off
chcp 65001 >nul
title ПУБЛИЧНАЯ ВЕРСИЯ - Учёт объектов

echo ========================================
echo   ПУБЛИЧНАЯ ВЕРСИЯ
echo   Учёт объектов и Сметы
echo ========================================
echo.
echo  Запуск сервера...
echo.

:: Запускаем сервер в фоне
cd /d "%~dp0"
set PORT=5001
set FLASK_DEBUG=0
set DB_FILE=%~dp0app_data_public.db
start "ObjectAccounting_PublicVersion" /min python app_objects.py

:: Ждём пока сервер станет доступным (макс 15 секунд)
echo  Ожидание сервера...
set /a attempts=0
:wait_loop
timeout /t 1 /nobreak >nul
set /a attempts+=1

:: Пробуем подключиться
curl -s -o nul http://127.0.0.1:%PORT%/login 2>nul
if %errorlevel%==0 goto server_ready

if %attempts% geq 15 goto server_timeout
goto wait_loop

:server_ready
echo  Сервер запущен!
echo.
echo  Открываю браузер...
timeout /t 1 /nobreak >nul
start http://127.0.0.1:%PORT%/
echo.
echo  ^================================^
echo  ПУБЛИЧНАЯ ВЕРСИЯ готова к работе!
echo  ^================================^
echo.
echo  Чтобы остановить сервер — закройте консольное окно "ObjectAccounting_PublicVersion"
echo.
pause
exit

:server_timeout
echo.
echo  ОШИБКА: Сервер не запустился за 15 секунд!
echo  Проверьте что установлен Python и зависимости:
echo    pip install -r requirements.txt
echo.
echo  Попробуйте запустить вручную:
echo    python app_objects.py
echo.
pause
exit
