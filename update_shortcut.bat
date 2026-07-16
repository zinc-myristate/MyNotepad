@echo off
chcp 65001 >nul
powershell -ExecutionPolicy Bypass -Command "$WS = New-Object -ComObject WScript.Shell; $SC = $WS.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\MyNotepad.lnk'); $SC.TargetPath = 'D:\Claude code\记事本\dist\MyNotepad\MyNotepad.exe'; $SC.WorkingDirectory = 'D:\Claude code\记事本\dist\MyNotepad'; $SC.IconLocation = 'D:\Claude code\记事本\dist\MyNotepad\MyNotepad.exe,0'; $SC.Save(); Write-Host 'Shortcut updated successfully'"
pause
