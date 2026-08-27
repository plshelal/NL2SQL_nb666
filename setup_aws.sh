#!/bin/bash
# 金融问数系统 AWS 一键部署脚本(需 m7i-flex.large 8G 实例)
set -e
export DEBIAN_FRONTEND=noninteractive
cd ~

echo "===== [1/12] 建 swap (4G 兜底,防 TEI/ES 峰值 OOM) ====="
sudo fallocate -l 4G /swapfile 2>/dev/null || true
sudo chmod 600 /swapfile
sudo mkswap /swapfile || true
sudo swapon /swapfile 2>/dev/null || true
grep -q swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

echo "===== [2/12] ES 内核参数 ====="
grep -q max_map_count /etc/sysctl.conf || echo 'vm.max_map_count=262144' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p 2>/dev/null || true

echo "===== [3/12] 装 docker ====="
command -v docker &>/dev/null || curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER 2>/dev/null || true

echo "===== [4/12] 装 uv ====="
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv &>/dev/null || curl -LsSf https://astral.sh/install.sh | sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
uv --version

echo "===== [5/12] clone 项目 ====="
if [ ! -d finance-data-main ]; then
  git clone https://github.com/plshelal/NL2SQL_nb666.git finance-data-main
fi
cd ~/finance-data-main
git pull --rebase 2>/dev/null || true

echo "===== 移入 fewshot(若已 scp 到 ~) ====="
[ -f ~/finetune_data.json ] && mv ~/finetune_data.json conf/ 2>/dev/null || true
[ -f ~/fewshot_embeddings.pkl ] && mv ~/fewshot_embeddings.pkl conf/ 2>/dev/null || true

echo "===== [6/12] 配 .env ====="
cp -n .env.example .env
sed -i 's|DEEPSEEK_API_KEY=your-deepseek-api-key|DEEPSEEK_API_KEY=sk-dc53fd20e19d48b5a562ea9b76f1d803|' .env
sed -i 's|EXTERNAL_ENABLED=.*|EXTERNAL_ENABLED=0|' .env
echo "--- .env 关键项 ---"
grep -E 'DEEPSEEK|DB_PASSWORD|EXTERNAL' .env

echo "===== [7/12] 启动 docker 服务 (首次构建 ES/TEI 镜像约 5-10 分钟) ====="
sudo docker compose -f docker/docker-compose.yaml up -d --build

echo "===== 等 MySQL 就绪 ====="
for i in $(seq 1 60); do sudo docker exec finance-mysql mysqladmin ping -uroot -p123321 2>/dev/null && break; sleep 2; done
echo "===== 等 ES 就绪 ====="
for i in $(seq 1 60); do curl -sf 'http://localhost:9200/_cluster/health?wait_for_status=yellow&timeout=60s' >/dev/null 2>&1 && break; sleep 3; done
echo "ES: $(curl -s http://localhost:9200/_cluster/health 2>/dev/null | grep -o '"status":"[^"]*"')"

echo "===== [8/12] 装 Python 依赖 (Python 3.12) ====="
uv python install 3.12
uv sync --python 3.12

echo "===== [9/12] 建库 + 导数据 ====="
uv run init_db.py
sudo docker exec -i finance-mysql mysql -uroot -p123321 meta < two_database_update/meta_data.sql
sudo docker exec -i finance-mysql mysql -uroot -p123321 finance < two_database_update/finance_data.sql

echo "===== [10/12] 下载 TEI 模型 (1.3G, 几分钟) ====="
cd docker
sudo apt install -y git-lfs
git lfs install
if [ ! -f tei-model/pytorch_model.bin ]; then
  rm -rf tei-model
  git clone https://huggingface.co/BAAI/bge-large-zh-v1.5 tei-model
fi
cd ..
sudo docker compose -f docker/docker-compose.yaml restart tei
echo "===== 等 TEI 加载模型就绪 (约 30-60s) ====="
for i in $(seq 1 90); do curl -sf http://localhost:8081/health >/dev/null 2>&1 && break; sleep 3; done
curl -s http://localhost:8081/health && echo
echo "TEI 内存(应 2-3G):"; sudo docker stats --no-stream finance-tei --format '{{.MemUsage}}'

echo "===== [11/12] 建知识索引 (TEI 算 embedding, 慢) ====="
uv run -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml

echo "===== [12/12] 启动应用 ====="
pkill -f 'app.main' 2>/dev/null || true
nohup uv run -m app.main > /tmp/app.log 2>&1 &
echo "等 app 启动..."
for i in $(seq 1 60); do curl -s -o /dev/null http://localhost:8000/static/query.html 2>/dev/null && break; sleep 3; done
echo "===== 访问测试 ====="
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://localhost:8000/static/query.html
tail -15 /tmp/app.log

echo ""
echo "=========================================="
echo "  部署完成!"
echo "=========================================="
echo "公网访问: http://<公网IP>:8000/static/query.html"
echo "登录: admin_total / 密码 1"
echo "⚠ 记得安全组放行 8000 端口 (EC2→实例→安全→安全组→入站→8000/TCP/0.0.0.0/0)"
echo "app 日志: tail -f /tmp/app.log"
echo "重启 app: cd ~/finance-data-main && pkill -f app.main; nohup uv run -m app.main > /tmp/app.log 2>&1 &"
