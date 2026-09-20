@echo off
rem ============================================================
rem  我的记事本 — 快捷启动
rem  优先用打包版（数据在 dist\MyNotepad\data\）；
rem  没有打包产物时才退回源码运行（数据在仓库 data\，是另一本记事本）。
rem ============================================================
setlocal
set "EXE=%~dp0dist\MyNotepad\MyNotepad.exe"
if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)
echo.
echo 未找到打包版 %EXE%
echo 改用源码运行：数据目录是仓库的 data\，与打包版不是同一份笔记。
echo 如需打包：python build.py
echo.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0app.pyw"
) else (
  start "" python "%~dp0app.pyw"
)
endlocal
