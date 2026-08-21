# 金融问数系统（NL2SQL）

江苏省 13 家农商行经营指标自然语言查询系统。用户用自然语言提问，系统自动生成 SQL 查询行内数据库，支持计算指标、指标组、外部宏观数据（iFinD）、多轮对话、知识图谱可视化。

> 本文档以 **Windows** 环境为准。

## 前置环境要求

| 软件 | 版本 | 安装方式 |
|---|---|---|
| **Docker Desktop** | 最新 | [下载](https://www.docker.com/products/docker-desktop/)，安装后启动 |
| **uv**（Python 包管理）| 最新 | 打开 PowerShell 执行：`powershell -c "irm https://astral.sh/install.ps1 \| iex"` |
| **Python** | ≥3.12 | 不用手动装，uv 会自动安装 |
| **DeepSeek API Key** | — | [申请](https://platform.deepseek.com/)，用于 LLM 生成 SQL |

## 快速开始（一键安装）

### 1. 配置环境变量

复制 `.env.example` 为 `.env`：

```cmd
copy .env.example .env
```

用记事本或 VS Code 打开 `.env`，**必须填写以下两项**：

| 变量 | 填什么 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | `sk-你的真实密钥` | **必填**，没这个系统无法生成 SQL。在 [DeepSeek 平台](https://platform.deepseek.com/) 申请 |
| `DB_PASSWORD` | `123321` | Docker 启动的 MySQL 容器的 root 密码。你自己定一个就行，Docker 会用这个密码创建容器，安装脚本也用这个密码连——两边读同一个 `.env` 自动对齐 |

其余变量（QDRANT_HOST/PORT、ES_HOST/PORT、TEI_HOST/PORT 等）在本地安装时**不用改**，默认都是 127.0.0.1 + 标准端口。

`IFIND_MCP_TOKEN` 和 `EXTERNAL_ENABLED` 是可选的——不填外部数据功能关闭，不影响行内指标查询。

### 2. 运行一键安装脚本

双击 `install.bat`，或在命令行执行：

```cmd
install.bat
```

脚本会自动完成：
1. 启动 4 个 Docker 服务（MySQL + Qdrant + Elasticsearch + TEI 嵌入服务）
2. 等待所有服务就绪
3. 安装 Python 依赖（`uv sync`）
4. 初始化数据库（`init_db.py`）
5. 导入预置数据（59 个用户 + 105 条权限 + 30 个公式 + 13 万行指标数据）
6. 构建知识索引（Qdrant 向量 + ES 全文）

首次构建 ES 和 TEI 镜像约需 5-10 分钟（下载 torch + IK 插件），请耐心等待。

### 3. 启动应用

```cmd
uv run -m app.main
```

打开浏览器访问 **http://localhost:8000/static/query.html**

管理员账号：`admin_total`，密码：`1`（**首次登录后请修改密码**）

## 手动安装（逐步）

如果一键脚本不适用，按以下步骤手动安装：

### 步骤 1：启动 Docker 服务

```cmd
docker compose -f docker\docker-compose.yaml up -d --build
```

等待服务启动（首次构建约 5-10 分钟）。

### 步骤 2：安装 Python 依赖

```cmd
uv sync
```

### 步骤 3：初始化数据库

```cmd
uv run init_db.py
```

此脚本会创建 `meta` 和 `finance` 两个数据库（从 `sql/` 目录的 SQL 文件）。

### 步骤 4：导入预置数据

```cmd
docker exec -i finance-mysql mysql -uroot -p123321 meta < two_database_update\meta_data.sql
docker exec -i finance-mysql mysql -uroot -p123321 finance < two_database_update\finance_data.sql
```

导入预置的 59 个用户、105 条岗位权限、30 个计算公式、13 万行业务指标数据。
（密码 `123321` 替换为你的 `DB_PASSWORD`）

### 步骤 5：构建知识索引

```cmd
uv run -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

此脚本读取数据库 schema，通过 TEI 嵌入服务向量化字段/指标，写入 Qdrant 和 Elasticsearch。

### 步骤 6：导入权限和用户

```cmd
uv run -m app.scripts.seed_permissions
```

从 `conf/` 下的 CSV 文件导入岗位-指标权限和用户账号。

### 步骤 7：启动

```cmd
uv run -m app.main
```

## 服务清单

| 服务 | 端口 | 用途 | Docker 容器 |
|---|---|---|---|
| MySQL 8.0 | 3306 | 元数据 + 业务数据 | finance-mysql |
| Qdrant | 6333 | 向量检索（字段/指标召回） | finance-qdrant |
| Elasticsearch 8.11 | 9200 | 全文检索（字段值匹配） | finance-es |
| TEI 嵌入服务 | 8081 | bge-large-zh-v1.5 向量化 | finance-tei |
| 应用 | 8000 | FastAPI 问数服务 | （本地运行） |

## 配置说明（.env）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DB_HOST` | 127.0.0.1 | MySQL 地址 |
| `DB_PORT` | 3306 | MySQL 端口 |
| `DB_USER` | root | MySQL 用户 |
| `DB_PASSWORD` | 123321 | MySQL 密码 |
| `DB_NAME` | finance | 业务数据库名 |
| `APP_PORT` | 8000 | 应用端口 |
| `DEEPSEEK_API_KEY` | — | DeepSeek LLM 密钥（必填）|
| `QDRANT_HOST` | 127.0.0.1 | Qdrant 地址 |
| `QDRANT_PORT` | 6333 | Qdrant 端口 |
| `ES_HOST` | 127.0.0.1 | Elasticsearch 地址 |
| `ES_PORT` | 9200 | Elasticsearch 端口 |
| `TEI_HOST` | 127.0.0.1 | 嵌入服务地址 |
| `TEI_PORT` | 8081 | 嵌入服务端口 |
| `IFIND_MCP_TOKEN` | — | iFinD MCP 令牌（外部数据，可选）|
| `EXTERNAL_ENABLED` | 0 | 外部数据开关：1=启用，0=关闭（未填 token 时保持 0）|

## 目录结构

```
finance-data-main/
├── app/                    # 主应用代码
│   ├── agent/              # Agent 编排 + 内部 NL2SQL 图
│   ├── api/routers/        # FastAPI 路由（query/auth/audit/review）
│   ├── clients/            # MySQL/Qdrant/ES/TEI 客户端
│   ├── core/               # 生命周期管理、日志
│   ├── nodes/              # 11 节点 NL2SQL 管道
│   └── scripts/            # 知识构建 + 权限导入脚本
├── conf/                   # 配置文件
│   ├── app_config.yaml     # 服务配置
│   ├── meta_config.yaml    # 表/字段/指标定义
│   ├── tools.yaml          # iFinD MCP 配置
│   └── finetune_data.json  # Few-shot 示例（启动时自动重建）
├── docker/                 # Docker 构建
│   ├── docker-compose.yaml # 4 服务编排
│   ├── Dockerfile.tei      # 嵌入服务镜像
│   ├── Dockerfile.es       # ES + IK 分词镜像
│   └── tei-model/          # 预打包模型(bge-large-zh-v1.5)
├── knowledge/              # 知识文件（指标组定义）
├── sql/                    # 数据库初始化 SQL
├── static/                 # 前端页面 + vendor 库
│   ├── query.html          # 问数界面
│   ├── knowledge.html      # 知识管理界面
│   └── vendor/             # 本地化 JS 库（lucide/vis-network/chart.js）
├── prompts/                # 提示词模板
├── .env.example            # 环境变量模板
├── install.bat             # 一键安装（Windows）
├── install.sh              # 一键安装（Linux/Mac，备用）
├── init_db.py              # 数据库初始化脚本
└── pyproject.toml          # Python 依赖
```

## 常见问题

### install.bat 闪退
检查命令行是否以管理员身份运行，以及 Docker Desktop 是否已启动。

### ES 启动报 IK 插件错误
ES 镜像（`Dockerfile.es`）已内置 IK 中文分词插件。如果手动拉 ES 镜像，需自行安装：
```cmd
docker exec finance-es elasticsearch-plugin install https://release.infinilabs.com/analysis-ik/stable/elasticsearch-analysis-ik-8.11.0.zip
docker restart finance-es
```

### TEI 服务启动慢
首次启动时 TEI 需要加载 bge-large-zh-v1.5 模型（~1.2GB），约 30-60 秒。模型文件已预打包在 `docker/tei-model/`，无需联网下载。

### 端口冲突
如果本地已占用 3306/6333/9200/8081/8000，修改 `docker/docker-compose.yaml` 的端口映射和 `.env` 中的端口配置。

### Docker 构建失败
ES 镜像构建需要下载 IK 插件（~5MB），TEI 镜像需要安装 CPU 版 torch（~800MB）。首次构建较慢，之后有缓存。如果网络不好，可以配置 Docker 镜像加速器。

### DeepSeek API 连接失败
检查 `.env` 中的 `DEEPSEEK_API_KEY` 是否正确。DeepSeek API 地址：`https://api.deepseek.com/v1`。

### 数据更新后重新导出
如果修改了数据库中的数据，重新导出 dump 文件：
```cmd
uv run python dump_data.py
```
然后提交到 Git，其他人 `git pull` 后重新安装即可同步。
