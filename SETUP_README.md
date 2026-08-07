# 金融问数系统 · 部署指南

## 一、环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.12+ | 推荐 3.12 |
| MySQL | 8.0 | 业务数据 + 元数据 |
| Elasticsearch | 8.11.0 | 需安装 IK 分词插件 |
| Qdrant | 1.x | 向量数据库 |
| DeepSeek API Key | - | 注册获得：https://platform.deepseek.com |
| uv | 最新 | Python 包管理器：https://docs.astral.sh/uv |

## 二、安装步骤

### 1. 克隆项目

```bash
git clone <仓库地址>
cd finance-data
```

### 2. 安装 Python 依赖

```bash
uv sync
```

### 3. 启动基础服务

**MySQL**（本地已安装略过，需建两个库）：

```sql
CREATE DATABASE finance CHARACTER SET utf8mb4;
CREATE DATABASE meta CHARACTER SET utf8mb4;
```
```
将/two_database_update内的两个数据库导入
```
**Elasticsearch**（Docker 一键，已含 IK 插件）：

```bash
docker run -d --name elasticsearch -p 9200:9200 -p 9300:9300 \
  -e "discovery.type=single-node" -e "xpack.security.enabled=false" \
  zingimmick/elasticsearch-ik:8.11.0
```

**Qdrant**（Docker）：

```bash
docker run -d --name qdrant -p 6333:6333 qdrant/qdrant
```
```
本项目tei是用python本地启动，不在docker里，若你的装在在docker里就直接启动，忽略第8步启动嵌入服务
```
### 4. 配置环境变量

复制模板并填写真实值：

```bash
cp .env.example .env
```

`.env` 内容：

```
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=你的密码
DB_NAME=finance

DEEPSEEK_API_KEY=sk-xxxxxxxx

QDRANT_HOST=127.0.0.1
QDRANT_PORT=6333

ES_HOST=127.0.0.1
ES_PORT=9200

TEI_HOST=127.0.0.1
TEI_PORT=8081
```

### 5. 初始化数据库

```bash
uv run init_db.py          # 创建 finance 业务库
uv run init_meta.py        # 创建 meta 元数据库
```

### 6. 构建知识库

```bash
uv run -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

### 7. 导入权限数据

```bash
uv run python app/scripts/seed_permissions.py
```

### 8. 启动嵌入服务

```bash
uv run python embedding_server.py
```

### 9. 启动主服务

```bash
uv run -m app.main
```

## 三、访问地址

| 入口 | 地址 |
|------|------|
| 问数界面 | http://127.0.0.1:8000/static/query.html |
| 审计日志 | http://127.0.0.1:8000/static/audit.html |
| 技术问答 | http://127.0.0.1:8000/static/qa.html |
| Swagger | http://127.0.0.1:8000/docs |

## 四、测试账号

| 账号 | 密码 | 岗位 | 权限范围 |
|------|------|------|------|
| admin_total | 1 | 综合管理 | 全部机构+全部指标 |
| khjl_A | 1 | 客户经理 | A市+基础业务指标 |
| fkzy_B | 1 | 风控专员 | B市+风险合规指标 |
| cwry_C | 1 | 财务人员 | C市+经营效益指标 |

## 五、常见问题

**Q: Elasticsearch IK 插件安装失败？**
使用预装了 IK 的镜像 `zingimmick/elasticsearch-ik:8.11.0`，或离线下载 `elasticsearch-analysis-ik-8.11.0.zip` 手动安装。

**Q: TEI 嵌入服务启动报错？**
确保模型已下载到本地缓存 `~/.cache/huggingface/hub/models--BAAI--bge-large-zh-v1.5`。

**Q: 权限数据导入失败？**
确认 `岗位指标权限.csv` 和 `用户权限配置.csv` 放在 `conf/` 目录下，编码为 UTF-8。
