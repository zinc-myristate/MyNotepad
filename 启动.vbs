' MyNotepad launcher (packaged build)
'
' NOTE: this file is deliberately ASCII-only. Windows Script Host reads .vbs in the
' ANSI codepage unless the file has a UTF-16 BOM, so non-ASCII text saved as UTF-8
' would show up as mojibake in the MsgBox below.
'
' The path is built from the script's own folder -- never hardcode a drive letter
' (the old version had D:\MyNotepad\... which simply does not exist for anyone else).
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws  = CreateObject("WScript.Shell")
exe = fso.GetParentFolderName(WScript.ScriptFullName) & "\dist\MyNotepad\MyNotepad.exe"
If fso.FileExists(exe) Then
  ws.Run """" & exe & """", 0, False
Else
  MsgBox "Not built yet." & vbCrLf & vbCrLf & _
         "Run this in the repo folder first:" & vbCrLf & "    python build.py" & vbCrLf & vbCrLf & _
         "(or use the .bat launcher, which falls back to running from source)", 48, "MyNotepad"
End If
