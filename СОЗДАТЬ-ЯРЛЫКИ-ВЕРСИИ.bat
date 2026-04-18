@echo off
chcp 65001 >nul
title Создание ярлыков

echo ========================================
echo   СОЗДАНИЕ ЯРЛЫКОВ НА РАБОЧЕМ СТОЛЕ
echo ========================================
echo.

:: Путь к рабочему столу
set "DESKTOP=%USERPROFILE%\Рабочий стол"
if not exist "%DESKTOP%" set "DESKTOP=%USERPROFILE%\Desktop"

:: Путь к скрипту создания ярлыка
set "SCRIPT=%~dp0create_shortcut.py"

echo  Создание ярлыка: МОЯ ВЕРСИЯ
python "%SCRIPT%" "МОЯ ВЕРСИЯ" "%~dp0МОЯ ВЕРСИЯ.bat" "%DESKTOP%"
if %errorlevel%==0 (echo   [OK] МОЯ ВЕРСИЯ) else (echo   [!!] Ошибка создания МОЯ ВЕРСИЯ)

echo.
echo  Создание ярлыка: ПУБЛИЧНАЯ ВЕРСИЯ
python "%SCRIPT%" "ПУБЛИЧНАЯ ВЕРСИЯ" "%~dp0ПУБЛИЧНАЯ ВЕРСИЯ.bat" "%DESKTOP%"
if %errorlevel%==0 (echo   [OK] ПУБЛИЧНАЯ ВЕРСИЯ) else (echo   [!!] Ошибка создания ПУБЛИЧНАЯ ВЕРСИЯ)

echo.
echo  Готово! Ярлыки созданы на рабочем столе.
echo.
pause
