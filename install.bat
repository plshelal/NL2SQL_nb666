@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   金融问数系统 - 一键安装
echo ==========================================

REM ---- 1. 前置检查 ----
echo [1/9] 检查前置环境...
where docker >nul 2>&1
if %errorlevel% neq 0 (
  echo ✗ 未安装 Docker,请先安装 Docker Desktop
  echo   下载: https://www.docker.com/products/docker-desktop/
  pause & exit /b 1
)
where uv >nul 2>&1
if %errorlevel% neq 0 (
  echo ✗ 未安装 uv,请先执行: powershell -c "irm https://astral.sh/install.ps1 ^| iex"
  pause & exit /b 1
)
echo ✓ Docker 和 uv 已就绪

REM ---- 2. 配置 .env ----
echo [2/9] 配置 .env...
if not exist .env (
  copy .env.example .env >nul
  echo   已从 .env.example 复制 .env
)

REM 检查 DEEPSEEK_API_KEY
findstr /C:"DEEPSEEK_API_KEY=your-deepseek-api-key" .env >nul 2>&1
if %errorlevel% equ 0 (
  echo.
  echo   ⚠ DEEPSEEK_API_KEY 未填写!
  echo   请打开 .env 填入你的 DeepSeek API Key
  echo   申请地址: https://platform.deepseek.com/
  echo   填完后按回车继续(或 Ctrl+C 退出)...
  pause
  findstr /C:"DEEPSEEK_API_KEY=your-deepseek-api-key" .env >nul 2>&1
  if %errorlevel% equ 0 (
    echo ✗ DEEPSEEK_API_KEY 仍为空,无法继续
    pause & exit /b 1
  )
)

REM 检查 DB_PASSWORD(占位符则设为默认)
set DBPWD=123321
for /f "tokens=2 delims==" %%a in ('findstr /B "DB_PASSWORD" .env 2^>nul') do set DBPWD=%%a
if "!DBPWD!"=="" set DBPWD=123321
if "!DBPWD!"=="your-password" (
  echo   DB_PASSWORD 未设置,使用默认值 123321
  set DBPWD=123321
  powershell -c "(Get-Content .env) -replace 'DB_PASSWORD=your-password','DB_PASSWORD=123321' | Set-Content .env" 2>nul
)
echo ✓ .env 已配置 ^(DB_PASSWORD=!DBPWD!^)

REM ---- 3. 启动 Docker 服务 ----
echo [3/9] 启动 Docker 服务(MySQL + Qdrant + ES + TEI)...
echo   首次构建 ES 和 TEI 镜像约需 5-10 分钟,请耐心等待...
docker compose -f docker\docker-compose.yaml up -d --build
echo ✓ Docker 服务已启动

REM ---- 4. 等待服务就绪 ----
echo [4/9] 等待服务就绪(最多等待 2 分钟)...

echo   等待 MySQL...
for /l %%i in (1,1,60) do (
  docker exec finance-mysql mysqladmin ping -uroot -p!DBPWD! >nul 2>&1 && (echo   ✓ MySQL 就绪 & goto mysql_ok)
  timeout /t 2 /nobreak >nul
)
echo   ✗ MySQL 超时 & goto :fail
:mysql_ok

echo   等待 Qdrant...
for /l %%i in (1,1,30) do (
  curl -sf http://localhost:6333/health >nul 2>&1 && (echo   ✓ Qdrant 就绪 & goto qdrant_ok)
  timeout /t 2 /nobreak >nul
)
echo   ✗ Qdrant 超时 & goto :fail
:qdrant_ok

echo   等待 Elasticsearch...
for /l %%i in (1,1,60) do (
  curl -sf "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=60s" >nul 2>&1 && (echo   ✓ ES 就绪 & goto es_ok)
  timeout /t 2 /nobreak >nul
)
echo   ✗ ES 超时 & goto :fail
:es_ok

echo   等待 TEI 嵌入服务...
for /l %%i in (1,1,60) do (
  curl -sf http://localhost:8081/health >nul 2>&1 && (echo   ✓ TEI 就绪 & goto tei_ok)
  timeout /t 2 /nobreak >nul
)
echo   ✗ TEI 超时 & goto :fail
:tei_ok

REM ---- 5. 安装 Python 依赖 ----
echo [5/9] 安装 Python 依赖...
uv sync
echo ✓ Python 依赖已安装

REM ---- 6. 初始化数据库(建表结构) ----
echo [6/9] 初始化数据库表结构...
uv run init_db.py
echo ✓ 数据库表结构已创建

REM ---- 7. 导入预置数据 ----
echo [7/9] 导入预置数据(用户+权限+公式+13万行业务数据)...
docker exec -i finance-mysql mysql -uroot -p!DBPWD! meta < two_database_update\meta_data.sql
if !errorlevel! neq 0 echo   ⚠ meta 数据导入失败,请检查 MySQL 是否就绪
docker exec -i finance-mysql mysql -uroot -p!DBPWD! finance < two_database_update\finance_data.sql
if !errorlevel! neq 0 echo   ⚠ finance 数据导入失败,请检查 MySQL 是否就绪
echo ✓ 预置数据已导入(59用户 + 105权限 + 30公式 + 13万行指标数据)

REM ---- 8. 建知识索引 ----
echo [8/9] 构建知识索引(Qdrant 向量 + ES 全文)...
uv run -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
echo ✓ 知识索引已构建

REM ---- 9. 导入权限和用户(CSV) ----
echo [9/9] 导入岗位权限和用户(CSV,补充)...
uv run -m app.scripts.seed_permissions 2>nul
if !errorlevel! neq 0 echo   (CSV 权限导入跳过,已用 dump 预置)
echo ✓ 完成

echo.
echo ==========================================
echo   安装完成!
echo ==========================================
echo 启动应用:  uv run -m app.main
echo 访问地址:  http://localhost:8000/static/query.html
echo 管理员:    admin_total / 密码: 1 (首次登录后请修改)
echo.
pause
exit /b 0

:fail
echo.
echo 安装失败,请检查上方错误信息
pause
exit /b 1
