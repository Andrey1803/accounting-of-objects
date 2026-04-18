import os
import sys
import winshell
from win32com.client import Dispatch

# Проверяем, переданы ли параметры командной строки
if len(sys.argv) >= 3:
    # Режим с параметрами: create_shortcut.py "Имя" "Путь к BAT" [Папка назначения]
    shortcut_name = sys.argv[1]
    target_path = sys.argv[2]
    target_dir = os.path.dirname(target_path)
    desktop = sys.argv[3] if len(sys.argv) >= 4 else winshell.desktop()
else:
    # Режим по умолчанию (старое поведение)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    launcher_path = os.path.join(script_dir, "ОТКРЫТЬ-ПРИЛОЖЕНИЕ.bat")
    desktop = winshell.desktop()
    shortcut_name = "Учёт объектов"
    target_path = launcher_path
    target_dir = script_dir

shortcut_path = os.path.join(desktop, f"{shortcut_name}.lnk")

# Создаем ярлык
shell = Dispatch('WScript.Shell')
shortcut = shell.CreateShortcut(shortcut_path)
shortcut.TargetPath = target_path
shortcut.WorkingDirectory = target_dir
shortcut.Description = shortcut_name
shortcut.IconLocation = "shell32.dll,13"
shortcut.save()

print(f"✓ Ярлык '{shortcut_name}' создан на рабочем столе!")
