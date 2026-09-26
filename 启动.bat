@echo off
rem ============================================================
rem  我的记事本 — 快捷启动
rem  优先用打包版（数据在 dist\MyNotepad\data\）；
rem  没有打包产物时才退回源码运行（数据在仓库 data\，是另一本记事本）。
rem
rem  编码约定：本文件是 **UTF-8（无 BOM）+ chcp 65001**，中文提示才不会乱码，
rem  在 GitHub 上也能正常显示（GBK 的 .bat 在网页上是一片乱码）。
rem  注意：诊断.bat 刻意保持 GBK —— 它要调用 systeminfo / wmic 这类按系统代码页
rem  输出的老工具，切成 UTF-8 反而会把它们的输出变成乱码。
rem ============================================================
chcp 65001 >nul
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
