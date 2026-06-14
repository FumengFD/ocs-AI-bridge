@echo off
cd /d "%~dp0"

echo ============================================
echo   ocs-AI-bridge
echo ============================================
echo.

REM -- 环境检测 --
echo === 环境检测 ===

REM .env
if exist ".env" (
    python -c "import os; from dotenv import load_dotenv; load_dotenv(); k=os.getenv('DEEPSEEK_API_KEY',''); exit(0 if k and len(k)>10 else 1)" >nul 2>&1
    if errorlevel 1 (
        echo [WARN] .env 存在但 API key 无效
        goto :ask_key
    ) else (
        echo [ OK ] .env + API key
    )
) else (
    echo [WARN] 未找到 .env
    goto :ask_key
)
goto :after_key

:ask_key
echo.
echo 请粘贴你的 API Key（Ctrl+V 然后回车）：
echo 示例：sk-your-key-here
set /p USER_KEY="> "
if not "%USER_KEY%"=="" (
    echo DEEPSEEK_API_KEY=%USER_KEY%> .env
    echo [ OK ] API key 已保存
) else (
    echo [WARN] 未输入 key，服务器可启动但无法答题
)
echo.
:after_key

REM 证书
if exist "cert.pem" if exist "key.pem" (
    echo [ OK ] HTTPS 证书
) else (
    echo [WARN] 缺少 HTTPS 证书，将使用 HTTP 模式。按 README 生成证书可启用 HTTPS
)

REM Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] 找不到 Python，请先安装 Python
    pause & exit /b 1
) else (
    echo [ OK ] Python
)

REM pyOpenSSL
python -c "import OpenSSL" >nul 2>&1
if errorlevel 1 (
    echo [WARN] pyOpenSSL 未安装 - 运行: pip install pyOpenSSL
) else (
    echo [ OK ] pyOpenSSL
)

REM 核心依赖
python -c "import starlette, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [FAIL] 依赖缺失 - 请运行: pip install -r requirements.txt
    pause & exit /b 1
) else (
    echo [ OK ] 核心依赖
)

REM MinerU
python -c "import mineru" >nul 2>&1
if errorlevel 1 (
    echo [INFO] MinerU 未安装 - 图片OCR不可用。可选: pip install mineru[core]
) else (
    echo [ OK ] MinerU OCR
)

echo.
echo === 启动服务器 ===
echo.

REM 关闭旧进程
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8865.*LISTENING" 2^>nul') do (
    echo 关闭端口 8865 旧进程 (PID: %%a)
    taskkill /PID %%a /F 2>nul
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8888.*LISTENING" 2^>nul') do (
    taskkill /PID %%a /F 2>nul
)
timeout /t 2 /nobreak >nul

python ocs_server.py
pause
