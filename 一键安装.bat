@echo off
rem ============================================================
rem  QuestionOCR 一键安装脚本（Windows）
rem  自动完成：Python 环境检查 → 创建虚拟环境 → 安装依赖
rem           → 构建前端 → 检查 Pandoc
rem  安装完成后双击 "启动.bat" 即可使用
rem ============================================================
setlocal
set "ROOT=%~dp0"
title QuestionOCR 一键安装

echo ============================================================
echo   QuestionOCR 一键安装
echo ============================================================
echo.

rem ---- 第 1 步：检查 Python ----
echo [1/5] 检查 Python 环境...
where python >/dev/null 2>/dev/null
if errorlevel 1 (
    echo [错误] 未找到 Python。请先安装 Python 3.10 或更高版本：
    echo        https://www.python.org/downloads/
    echo        安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)
python -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >/dev/null 2>/dev/null
if errorlevel 1 (
    echo [错误] Python 版本过低，需要 3.10 或更高版本。当前版本：
    python --version
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version') do echo       找到 %%v

rem ---- 第 2 步：创建虚拟环境 ----
echo [2/5] 准备 Python 虚拟环境 backend\.venv ...
if exist "%ROOT%backend\.venv\Scripts\python.exe" (
    echo       虚拟环境已存在，跳过创建
) else (
    python -m venv "%ROOT%backend\.venv"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo       虚拟环境创建完成
)
set "VENV_PY=%ROOT%backend\.venv\Scripts\python.exe"

rem ---- 第 3 步：安装 Python 依赖（耗时较长，请耐心等待）----
echo [3/5] 安装 Python 依赖（首次约需 5-15 分钟，取决于网速）...
"%VENV_PY%" -m pip install --upgrade pip -q
echo       安装 CPU 版 PyTorch...
"%VENV_PY%" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
if errorlevel 1 (
    echo [错误] PyTorch 安装失败，请检查网络后重新运行本脚本
    pause
    exit /b 1
)
echo       安装其余依赖...
"%VENV_PY%" -m pip install -r "%ROOT%backend\requirements.txt"
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重新运行本脚本
    pause
    exit /b 1
)
echo       安装 UniMERNet 公式引擎（--no-deps，规避依赖冲突）...
"%VENV_PY%" -m pip install unimernet==0.2.3 --no-deps -q
if errorlevel 1 (
    echo [警告] UniMERNet 安装失败，将自动跳过公式二次识别，不影响主流程
) else (
    echo       UniMERNet 安装完成
)
echo       Python 依赖安装完成

rem ---- 第 4 步：构建前端 ----
echo [4/5] 构建前端...
where npm >/dev/null 2>/dev/null
if errorlevel 1 (
    echo [警告] 未找到 Node.js ^(npm^)，跳过前端构建。
    echo        请安装 Node.js 18+ 后手动执行：
    echo        cd frontend ^&^& npm install ^&^& npm run build
    echo        下载地址：https://nodejs.org/
) else (
    if exist "%ROOT%frontend\dist\index.html" (
        echo       前端已构建过 frontend\dist，跳过
        echo       如更新了前端代码，请手动执行 npm run build
    ) else (
        cd /d "%ROOT%frontend"
        call npm install
        if errorlevel 1 (
            echo [错误] npm install 失败，请检查网络
            pause
            exit /b 1
        )
        call npm run build
        if errorlevel 1 (
            echo [错误] 前端构建失败
            pause
            exit /b 1
        )
        cd /d "%ROOT%"
        echo       前端构建完成
    )
)

rem ---- 第 5 步：检查 Pandoc（Word/PDF 输入与导出需要）----
echo [5/5] 检查 Pandoc...
where pandoc >/dev/null 2>/dev/null
if not errorlevel 1 goto pandoc_ok
if exist "%ROOT%backend\pandoc\pandoc.exe" goto pandoc_ok
echo       未检测到 Pandoc。Pandoc 用于 Word/PDF 输入与导出。
echo       现在是否自动安装？（需要 winget，约 220MB）
choice /c YN /n /m "       输入 Y 自动安装，输入 N 跳过（可稍后自行安装）: "
if errorlevel 2 goto pandoc_skip
winget install --id JohnMacFarlane.Pandoc -e --accept-source-agreements --accept-package-agreements
if errorlevel 1 (
    echo [警告] Pandoc 自动安装失败，可稍后将 pandoc.exe 放入 backend\pandoc\ 目录
) else (
    echo       Pandoc 安装完成
)
goto pandoc_done
:pandoc_ok
echo       Pandoc 已就绪
goto pandoc_done
:pandoc_skip
echo       已跳过。稍后可用 winget install pandoc 安装，或将 pandoc.exe 放入 backend\pandoc\
:pandoc_done

rem ---- 完成 ----
echo.
echo ============================================================
echo   安装完成！
echo ============================================================
echo.
echo   首次识别时 Pix2Text 模型会自动下载（约 1-2GB，保持网络畅通）
echo   可选：下载 UniMERNet 权重放入 backend\models\unimernet_small\
echo        可提升公式识别质量（缺省也能正常使用）
echo.
choice /c YN /n /m "是否现在启动程序？(Y/N): "
if errorlevel 2 goto end
call "%ROOT%启动.bat"
:end
endlocal
