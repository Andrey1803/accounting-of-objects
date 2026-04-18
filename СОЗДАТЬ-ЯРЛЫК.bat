@echo off
chcp 65001 >nul
title Создание ярлыка на рабочем столе

echo.
echo Создание ярлыка для запуска приложения...
echo.

:: Определяем путь к рабочему столу
set "DESKTOP=%USERPROFILE%\Рабочий стол"
if not exist "%DESKTOP%" set "DESKTOP=%USERPROFILE%\Desktop"

:: Путь к лаунчеру
set "LAUNCHER=%~dp0ОТКРЫТЬ-ПРИЛОЖЕНИЕ.bat"

:: Создаем VBScript для создания ярлыка
set "VBS=%TEMP%\create_shortcut.vbs"

echo Set oWS = WScript.CreateObject("WScript.Shell") > "%VBS%"
echo sLinkFile = "%DESKTOP%\Учёт объектов.lnk" >> "%VBS%"
echo Set oLink = oWS.CreateShortcut(sLinkFile) >> "%VBS%"
echo oLink.TargetPath = "%LAUNCHER%" >> "%VBS%"
echo oLink.WorkingDirectory = "%~dp0" >> "%VBS%"
echo oLink.Description = "Учёт объектов и сметы" >> "%VBS%"
echo oLink.IconLocation = "shell32.dll,13" >> "%VBS%"
echo oLink.Save >> "%VBS%"

:: Выполняем VBScript
cscript //nologo "%VBS%"
del "%VBS%"

echo.
echo ✓ Ярлык создан на рабочем столе!
echo.
echo Теперь вы можете запускать приложение через ярлык "Учёт объектов"
echo Сервер будет запускаться автоматически.
echo.
pause
