#!/usr/bin/env bash
# 金融问数系统 一键安装
# 流程:检查前置 → 配.env → 启Docker → 等就绪 → 装依赖 → 建表 → 导数据 → 建索引 → 导权限
set -e
cd "$(dirname "$0")"

echo "=========================================="
echo "  金融问数系统 - 一键安装"
echo "=========================================="

# 从 .env 文件读单个变量(不靠 export,跨平台可靠)
read_env() { grep "^$1=" .env 2>/dev/null | cut -d= -f2- || echo ""; }

# ---- 1. 前置检查 ----
echo "[1/9] 检查前置环境..."
if ! command -v docker &>/dev/null; then
  echo "✗ 未安装 Docker,请先安装 Docker Desktop"
  echo "  下载: https://www.docker.com/products/docker-desktop/"
  exit 1
fi
if ! command -v uv &>/dev/null; then
  echo "✗ 未安装 uv,请先执行:"
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi
echo "✓ Docker 和 uv 已就绪"

# ---- 2. 配置 .env ----
echo "[2/9] 配置 .env..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  已从 .env.example 复制 .env"
fi

# 检查必填项:DEEPSEEK_API_KEY
API_KEY=$(read_env "DEEPSEEK_API_KEY")
if [ -z "$API_KEY" ] || [ "$API_KEY" = "your-deepseek-api-key" ]; then
  echo ""
  echo "  ⚠ DEEPSEEK_API_KEY 未填写!"
  echo "  请打开 .env 填入你的 DeepSeek API Key"
  echo "  申请地址: https://platform.deepseek.com/"
  echo "  填完后按回车继续(或 Ctrl+C 退出)..."
  read -r
  API_KEY=$(read_env "DEEPSEEK_API_KEY")
  if [ -z "$API_KEY" ] || [ "$API_KEY" = "your-deepseek-api-key" ]; then
    echo "✗ DEEPSEEK_API_KEY 仍为空,无法继续"
    exit 1
  fi
fi

# 检查 DB_PASSWORD(如果还是占位符,设为默认 123321)
DBPWD=$(read_env "DB_PASSWORD")
if [ -z "$DBPWD" ] || [ "$DBPWD" = "your-password" ]; then
  echo "  DB_PASSWORD 未设置,使用默认值 123321"
  DBPWD="123321"
  sed -i.bak 's/DB_PASSWORD=your-password/DB_PASSWORD=123321/' .env 2>/dev/null || \
  sed -i '' 's/DB_PASSWORD=your-password/DB_PASSWORD=123321/' .env 2>/dev/null || true
fi
echo "✓ .env 已配置(DB_PASSWORD=$DBPWD)"

# ---- 3. 启动 Docker 服务 ----
echo "[3/9] 启动 Docker 服务(MySQL + Qdrant + ES + TEI)..."
echo "  首次构建 ES 和 TEI 镜像约需 5-10 分钟,请耐心等待..."
docker compose -f docker/docker-compose.yaml up -d --build
echo "✓ Docker 服务已启动"

# ---- 4. 等待服务就绪 ----
echo "[4/9] 等待服务就绪(最多等待 2 分钟)..."
wait_for() {
  local name=$1 cmd=$2
  for i in $(seq 1 60); do
    if eval "$cmd" &>/dev/null; then echo "  ✓ $name 就绪"; return 0; fi
    sleep 2
  done
  echo "  ✗ $name 超时(120s)"; return 1
}
wait_for "MySQL"  "docker exec finance-mysql mysqladmin ping -uroot -p${DBPWD} 2>/dev/null"
wait_for "Qdrant"  "curl -sf http://localhost:6333/health"
wait_for "ES"      "curl -sf http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=60s"
wait_for "TEI"     "curl -sf http://localhost:8081/health"

# ---- 5. 安装 Python 依赖 ----
echo "[5/9] 安装 Python 依赖..."
uv sync
echo "✓ Python 依赖已安装"

# ---- 6. 初始化数据库(建表结构) ----
echo "[6/9] 初始化数据库表结构..."
uv run init_db.py
echo "✓ 数据库表结构已创建"

# ---- 7. 导入预置数据 ----
echo "[7/9] 导入预置数据(用户+权限+公式+13万行业务数据)..."
docker exec -i finance-mysql mysql -uroot -p${DBPWD} meta < two_database_update/meta_data.sql
docker exec -i finance-mysql mysql -uroot -p${DBPWD} finance < two_database_update/finance_data.sql
echo "✓ 预置数据已导入(59用户 + 105权限 + 30公式 + 13万行指标数据)"

# ---- 8. 建知识索引 ----
echo "[8/9] 构建知识索引(Qdrant 向量 + ES 全文)..."
uv run -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
echo "✓ 知识索引已构建"

# ---- 9. 导入权限和用户(CSV) ----
echo "[9/9] 导入岗位权限和用户(CSV,补充)..."
uv run -m app.scripts.seed_permissions 2>/dev/null || echo "  (CSV 权限导入跳过,已用 dump 预置)"
echo "✓ 完成"

echo ""
echo "=========================================="
echo "  安装完成!"
echo "=========================================="
echo "启动应用:  uv run -m app.main"
echo "访问地址:  http://localhost:8000/static/query.html"
echo "管理员:    admin_total / 密码: 1 (首次登录后请修改)"
echo ""
