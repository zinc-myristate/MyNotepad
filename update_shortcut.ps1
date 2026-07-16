$WS = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath('Desktop')
$SC = $WS.CreateShortcut("$Desktop\MyNotepad.lnk")
$SC.TargetPath = 'D:\Claude code\记事本\dist\MyNotepad\MyNotepad.exe'
$SC.WorkingDirectory = 'D:\Claude code\记事本\dist\MyNotepad'
$SC.IconLocation = 'D:\Claude code\记事本\dist\MyNotepad\MyNotepad.exe,0'
$SC.Save()
Write-Host "Shortcut updated: $Desktop\MyNotepad.lnk"
