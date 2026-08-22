@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ==========================================
echo   金融问数系统 - 一键启动
echo ==========================================

REM ---- 1. 检查 Docker 引擎 ----
echo [1/4] 检查 Docker...
docker info >nul 2>&1
if !errorlevel! neq 0 (
  echo   Docker 未运行,正在启动 Docker Desktop...
  start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
  echo   等待 Docker Desktop 启动(最多 90 秒)...
  set /a cnt=0
  :wait_docker
  timeout /t 3 /nobreak >nul
  docker info >nul 2>&1
  if !errorlevel! equ 0 goto docker_ok
  set /a cnt+=1
  if !cnt! lss 30 goto wait_docker
  echo   ✗ Docker 启动超时,请手动打开 Docker Desktop
  pause & exit /b 1
  :docker_ok
)
echo   ✓ Docker 引擎就绪

REM ---- 2. 启动 4 个 Docker 服务(restart 策略会自动恢复) ----
echo [2/4] 启动 Docker 服务(MySQL + Qdrant + ES + TEI)...
docker compose -f docker\docker-compose.yaml up -d
if !errorlevel! neq 0 (
  echo   ✗ 容器启动失败,请检查 docker compose
  pause & exit /b 1
)
echo   ✓ 容器已启动

REM ---- 3. 等待服务就绪(ES 最慢) ----
echo [3/4] 等待服务就绪...
set /a cnt=0
:wait_es
curl -sf "http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=5s" >nul 2>&1
if !errorlevel! equ 0 goto es_ok
timeout /t 2 /nobreak >nul
set /a cnt+=1
if !cnt! lss 60 goto wait_es
echo   ⚠ ES 未就绪(可稍后手动检查 docker logs finance-es)
:es_ok
curl -sf http://localhost:8081/health >nul 2>&1
if !errorlevel! equ 0 (
  echo   ✓ MySQL + Qdrant + ES + TEI 全部就绪
) else (
  echo   ⚠ TEI 仍在加载模型,稍后可访问
)

REM ---- 4. 启动应用(前台,关闭窗口即停止) ----
echo [4/4] 启动应用...
echo   访问地址: http://localhost:8000/static/query.html
echo   管理员:   admin_total / 密码 1
echo   按 Ctrl+C 停止应用
echo ==========================================
uv run -m app.main
pause
