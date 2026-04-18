@echo off
chcp 65001 >nul
title Бэкап базы данных

echo.
echo ═══════════════════════════════════════════
echo    СОЗДАНИЕ РЕЗЕРВНОЙ КОПИИ БАЗЫ ДАННЫХ
echo ═══════════════════════════════════════════
echo.

:: Создаём папку для бэкапов если нет
set "BACKUP_DIR=%~dp0backups"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

:: Формируем имя файла с датой и временем
for /f "tokens=1-3 delims=." %%a in ("%date%") do set DAY=%%c
for /f "tokens=1-3 delims=." %%a in ("%date%") do set MONTH=%%b
for /f "tokens=1-3 delims=." %%a in ("%date%") do set YEAR=%%a
for /f "tokens=1-4 delims=:" %%a in ("%time%") do set HOUR=%%a
for /f "tokens=1-4 delims=:" %%a in ("%time%") do set MINUTE=%%b

set "BACKUP_FILE=%BACKUP_DIR%\app_data_backup_%YEAR%%MONTH%%DAY%_%HOUR%%MINUTE%.db"

:: Копируем базу данных
echo Копирование базы данных...
copy "%~dp0app_data.db" "%BACKUP_FILE%" >nul

if %errorlevel%==0 (
    echo.
    echo ✓ Бэкап успешно создан!
    echo.
    echo Файл: %BACKUP_FILE%
    echo.
) else (
    echo.
    echo ✗ ОШИБКА при создании бэкапа!
    echo.
    echo Убедитесь что сервер остановлен: taskkill /F /IM python.exe
    echo.
)

echo Нажмите любую клавишу...
pause >nul
