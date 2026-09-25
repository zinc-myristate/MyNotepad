@echo off
rem ============================================================
rem  我的记事本 — 环境诊断（只读，不改任何文件）
rem  界面残缺 / 启动异常 / 提醒不响时运行本脚本，把输出发出来即可定位。
rem ============================================================
setlocal enabledelayedexpansion
set "ROOT=%~dp0"
echo ============ 我的记事本 诊断 ============
echo 时间: %date% %time%
echo.

echo ---- 1. Python ----
where python 2>nul || echo   [x] PATH 里找不到 python（仅影响源码运行，不影响打包版）
for /f "delims=" %%v in ('python -V 2^>^&1') do echo   %%v
echo.

echo ---- 2. 产物 ----
if exist "%ROOT%dist\MyNotepad\MyNotepad.exe" (
  for %%f in ("%ROOT%dist\MyNotepad\MyNotepad.exe") do echo   [v] exe: %%~tf  %%~zf 字节
) else (
  echo   [x] 未找到 dist\MyNotepad\MyNotepad.exe（还没打包）
)
if exist "%ROOT%dist\MyNotepad\_internal\renderer\index.html" (
  echo   [v] 包内前端存在
) else (
  echo   [x] 包内前端缺失（重新打包：python build.py）
)
echo.

echo ---- 3. 数据目录（两份要分清） ----
if exist "%ROOT%data\notes.db" (
  for %%f in ("%ROOT%data\notes.db") do echo   [v] 仓库 data\notes.db  %%~zf 字节  %%~tf
) else (
  echo   [-] 仓库 data\notes.db 不存在
)
if exist "%ROOT%dist\MyNotepad\data\notes.db" (
  for %%f in ("%ROOT%dist\MyNotepad\data\notes.db") do echo   [v] 打包版 data\notes.db  %%~zf 字节  %%~tf
) else (
  echo   [-] 打包版 data\notes.db 不存在
)
echo   注：快捷方式与 启动.vbs 用的是「打包版」那一份。
echo.

echo ---- 4. 依赖（源码运行需要） ----
for %%m in (webview cryptography PIL pystray cv2 docx openpyxl) do (
  python -c "import %%m" >nul 2>nul && echo   [v] %%m || echo   [x] %%m 缺失
)
echo.

echo ---- 5. WebView2 运行时 ----
reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv >nul 2>nul
if %errorlevel%==0 (
  for /f "tokens=3" %%v in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" /v pv ^| find "pv"') do echo   [v] WebView2 版本 %%v
) else (
  echo   [?] 未从注册表查到 WebView2（可能装的是每用户版，或没装——没装则界面起不来）
)
echo.

echo ---- 6. 托盘与自启 ----
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MyNotepad >nul 2>nul
if %errorlevel%==0 (
  for /f "tokens=2*" %%a in ('reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v MyNotepad ^| find "MyNotepad"') do echo   [v] 开机自启已开启: %%b
) else (
  echo   [-] 开机自启未设置
)
echo.

echo ---- 7. 错误日志（最后 15 行） ----
if exist "%ROOT%dist\MyNotepad\data\error.log" (
  echo   [打包版 error.log]
  powershell -NoProfile -Command "Get-Content -Tail 15 -Encoding UTF8 '%ROOT%dist\MyNotepad\data\error.log'"
) else (
  echo   [v] 打包版没有 error.log（零错误）
)
if exist "%ROOT%data\error.log" (
  echo   [仓库 data\error.log]
  powershell -NoProfile -Command "Get-Content -Tail 15 -Encoding UTF8 '%ROOT%data\error.log'"
)
echo.
echo ---- 8. 图片文字识别（OCR） ----
python -c "import winocr" >nul 2>nul && echo   [v] winocr      || echo   [x] winocr 缺失（识别不可用）
python -c "from winrt.windows.media.ocr import OcrEngine" >nul 2>nul && echo   [v] winrt       || echo   [x] winrt 缺失（识别不可用）
python ocr.py 2>nul | findstr /C:"ready" /C:"zh-" /C:"en-"
if errorlevel 1 echo   [?] 没能列出 OCR 语言：可能没装 winocr，或系统没装任何 OCR 语言包
echo   提示：打包版可用 "MyNotepad.exe --ocr-selftest 图片.png" 单独自检（结果写在图片旁边的 .ocr.json）
echo.
echo ============ 诊断结束 ============
pause
