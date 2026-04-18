Set oWS = WScript.CreateObject("WScript.Shell")
Set oEnv = oWS.Environment("Process")

' Get desktop path
userProfile = oEnv("USERPROFILE")
desktop = userProfile & "\Desktop"

' Check Russian desktop path
If CreateObject("Scripting.FileSystemObject").FolderExists(userProfile & "\Рабочий стол") Then
    desktop = userProfile & "\Рабочий стол"
End If

' Get script directory
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
launcherPath = scriptDir & "\ОТКРЫТЬ-ПРИЛОЖЕНИЕ.bat"

' Create shortcut
sLinkFile = desktop & "\Учёт объектов.lnk"
Set oLink = oWS.CreateShortcut(sLinkFile)
oLink.TargetPath = launcherPath
oLink.WorkingDirectory = scriptDir
oLink.Description = "Object Accounting Application"
oLink.IconLocation = "shell32.dll,13"
oLink.Save

WScript.Echo "Shortcut created on desktop!"
