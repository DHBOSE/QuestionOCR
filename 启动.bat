@echo off
rem ============================================================
rem  截图转题目 Word —— 一键启动脚本（无窗口后台运行版）
rem  双击后服务在后台静默运行（无黑色窗口），自动打开浏览器。
rem  停止服务：双击"停止服务.bat"
rem ============================================================
setlocal
set "ROOT=%~dp0"

if /i "%~1"=="hidden" goto run_hidden

rem ---- 检查后端虚拟环境 ----
if not exist "%ROOT%backend\.venv\Scripts\pythonw.exe" (
    echo [错误] 未找到后端虚拟环境 backend\.venv
    echo 请先按 README 安装后端依赖。
    pause
    exit /b 1
)

rem ---- 检查前端构建产物 ----
if not exist "%ROOT%frontend\dist\index.html" (
    echo [错误] 未找到前端构建产物 frontend\dist
    echo 请先执行：cd frontend ^&^& npm install ^&^& npm run build
    pause
    exit /b 1
)

rem ---- 已有实例在运行则直接打开页面 ----
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if not errorlevel 1 (
    echo 服务已在运行，直接打开浏览器……
    start "" http://localhost:8000
    exit /b 0
)

rem ---- 通过 VBS 以隐藏方式重新启动自身（无黑色窗口）----
wscript.exe "%ROOT%启动.vbs"

rem ---- 等待服务就绪后自动打开浏览器 ----
ping 127.0.0.1 -n 6 >nul
start "" http://localhost:8000
echo 服务已在后台启动（无窗口）。如需停止，请双击"停止服务.bat"。
exit /b 0

:run_hidden
cd /d "%ROOT%backend"
"%ROOT%backend\.venv\Scripts\pythonw.exe" api.py > "%ROOT%backend\console.log" 2>&1
exit /b 0
