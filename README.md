# 金融问数系统 安装教程（请先通读一遍此教程，预先了解可能遇到的报错）

> **所有命令都在项目根目录 `finance-data-main` 下执行**（就是能看到 `install.bat` 的那个文件夹）。
> 打开命令行（Win+R → 输入 cmd → 回车），用 `cd` 切到项目目录，例如：
> ```
> cd C:\Users\你的用户名\Desktop\finance-data-main
> ```

## 你需要准备什么

1. **一台 Windows 电脑**（Win10/11 都行）
2. **Docker Desktop** — 下载安装：https://www.docker.com/products/docker-desktop/ ，装完打开它，等右下角图标变成绿色
3. **uv**（Python 包管理器）— 打开 PowerShell，粘贴这行回车：
   ```
   powershell -c "irm https://astral.sh/install.ps1 | iex"
   ```
   装完后关掉 PowerShell 重新打开，输入 `uv --version` 确认能看到版本号
4. **DeepSeek API Key** — 去 https://platform.deepseek.com/ 注册，拿到 `sk-` 开头的密钥(若你部署不提供相关资费，用我的即可sk-dc53fd20e19d48b5a562ea9b76f1d803)


### 重要：配置 Docker 镜像加速（不配会很慢）

打开 Docker Desktop → 点右上角齿轮图标(Settings) → 左侧选 Docker Engine → 把内容**全部替换**成：

```json
{
  "experimental": false,
  "features": {
    "buildkit": true
  },
  "registry-mirrors": [
    "https://docker.1ms.run",
    "https://docker.xuanyuan.me",
    "https://docker.m.daocloud.io"
  ]
}
```

点 **Apply & Restart**，等 Docker 重启完（右下角图标变绿）。

### 重要：停掉本地 MySQL（如果有）

如果你电脑上装过 MySQL，必须先停掉它，否则端口 3306 会冲突：
- Win+R → 输入 `services.msc` → 回车
- 在列表里找到 **MySQL** → 右键 → 停止

> 如果你电脑上没装过 MySQL，这步跳过。

---

## 安装步骤（跟着做就行）

### 第 1 步：配置密钥

在项目根目录下操作：

1. 找到 `.env.example` 文件，复制一份，改名成 `.env`（把 `.example` 去掉）
2. 用记事本打开 `.env`，改两行：
   - `DEEPSEEK_API_KEY=你的sk密钥`（把 `your-deepseek-api-key` 换成真实的）
   - `DB_PASSWORD=123321`（不用改，保持 123321 就行）
3. 保存关闭

### 第 2 步：运行安装脚本

在项目根目录下，双击 `install.bat`，或者在命令行里输入：
```
install.bat
```

**脚本会自动完成所有事情**（首次约 10 分钟，请耐心等）：
1. 启动 4 个 Docker 服务（MySQL + Qdrant + Elasticsearch + TEI 嵌入服务）
2. 等待所有服务就绪
3. 安装 Python 依赖（151 个包）
4. 创建数据库表结构
5. 导入预置数据（59 个用户 + 105 条权限 + 30 个公式 + 13 万行指标数据）
6. 构建知识索引（Qdrant 向量 + ES 全文）

看到"安装完成!"就成功了。

### 第 3 步：启动应用

在项目根目录下，命令行输入：
```
uv run -m app.main
```

看到 `Uvicorn running on http://0.0.0.0:8000` 就说明启动成功了。

打开浏览器访问：**http://localhost:8000/static/query.html**

登录：`admin_total`，密码：`1`

> **输入框左下角两个小图标**：
> - 🧠 **深度思考**：点亮后 SQL 生成启用推理模式，更准但慢约 8 秒；默认关（约 1 秒，日常够用），复杂对比/排名题再开。
> - 🔌 **外部数据源**：查**行外数据**（LPR / CPI / PPI / 汇率 / 地产政策 / 财经资讯等同花顺 iFinD 数据）时，**先点亮它再提问**；只查行内指标（存贷款、利润、不良率等）不用开，默认直接走内部查询、最快。
>
> 没配 iFinD 令牌的部署，🔌 点开会显示"暂无已配置数据源"——属正常，见下方「外部数据」说明。

---

## 如果安装失败

### install.bat 闪退
1. 右键 `install.bat` → 以管理员身份运行
2. 确认 Docker Desktop 已启动（右下角绿色图标）
3. 确认在项目根目录下运行（命令行里输入 `dir install.bat` 能看到文件）
4. 如果还是闪退，打开 cmd，手动输入 `install.bat`，看具体报什么错

### Docker 下载很慢或卡住
确认你已完成上面的"配置 Docker 镜像加速"。如果配了还是慢，重启 Docker Desktop 再试。

### 端口 3306 被占用
说明你电脑上已有 MySQL 在跑。
- Win+R → `services.msc` → 找到 MySQL → 右键停止 → 重新跑 `install.bat`

### 端口 8000 被占用
说明已有程序占着 8000 端口。打开 `.env` 改 `APP_PORT=8001`，然后用 `http://localhost:8001/static/query.html` 访问。

### uv sync 报错
关掉所有命令行窗口，重新打开一个，`cd` 到项目根目录，再跑：
```
uv sync
```
如果还报错，试试把缓存目录设到项目内：
```
set UV_CACHE_DIR=.\.uv-cache
uv sync
```

### TEI 镜像构建失败
Dockerfile 里的 pip 源可能不通。如果报 `No matching distribution found`，手动构建：
```
docker compose -f docker\docker-compose.yaml build tei
```
如果还失败，尝试修改 `docker\Dockerfile.tei` 里的 pip 源地址。

### 其他问题
把命令行里的报错截图发出来。

---

## 手动安装（脚本不行时用）

> 以下所有命令都在**项目根目录**下执行（就是能看到 `install.bat` 的那个文件夹）。
> 打开 cmd，`cd` 到项目目录，然后逐条执行。

**步骤 1：启动 Docker 服务**（在项目根目录）
```
docker compose -f docker\docker-compose.yaml up -d --build
```
等待 4 个容器全部启动（首次构建约 10 分钟）。用 `docker ps` 确认 4 个容器都是 Up 状态。

**步骤 2：安装 Python 依赖**（在项目根目录）
```
uv sync
```

**步骤 3：初始化数据库**（在项目根目录）
```
uv run init_db.py
```

**步骤 4：导入预置数据**（在项目根目录）
```
docker exec -i finance-mysql mysql -uroot -p123321 meta < two_database_update\meta_data.sql
docker exec -i finance-mysql mysql -uroot -p123321 finance < two_database_update\finance_data.sql
```
（密码 `123321` 替换为你在 `.env` 里设的 `DB_PASSWORD`）

**步骤 5：构建知识索引**（在项目根目录）
```
uv run -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

**步骤 6：（可选）导入 CSV 权限**（在项目根目录）
```
uv run -m app.scripts.seed_permissions
```
> 这步如果报错可以忽略——数据已经由步骤 4 预置了，CSV 只是补充。

**步骤 7：启动**（在项目根目录）
```
uv run -m app.main
```

---

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
| `DB_PASSWORD` | 123321 | Docker MySQL 的 root 密码 |
| `DEEPSEEK_API_KEY` | — | DeepSeek LLM 密钥（**必填**）|
| `APP_PORT` | 8000 | 应用端口 |
| `IFIND_MCP_TOKEN` | — | iFinD 外部数据令牌（可选）|
| `EXTERNAL_ENABLED` | 0 | 外部数据开关：1=启用，0=关闭 |

其余变量（DB_HOST/PORT、QDRANT/ES/TEI_HOST/PORT）默认都是 127.0.0.1 + 标准端口，本地安装不用改。

### 外部数据（同花顺 iFinD，可选）

行外数据（LPR/CPI/PPI/汇率/地产政策/财经资讯）通过同花顺 iFinD MCP 接入。**这部分需要你自己向同花顺申请的 MCP 令牌**，不是系统自带的：

1. 拿到令牌后，在 `.env` 填：`IFIND_MCP_TOKEN=你的令牌`
2. 把 `EXTERNAL_ENABLED` 改成 `1`
3. 重启应用（`uv run -m app.main`）

**没有令牌就两项都别动**（令牌留空、`EXTERNAL_ENABLED=0`）。此时系统只查行内指标，完全正常；输入框的 🔌 图标点开会提示"暂无已配置数据源"。

> iFinD 是同花顺的商业数据服务，令牌一般需付费/申请。比赛演示以行内指标查询为主，不配外部数据不影响主功能。

## 数据更新

在项目根目录下执行：
```
uv run python dump_data.py
```
刷新 dump 文件，提交到 Git 后其他人 git pull 即可同步。

## 常见问题

### TEI 服务启动慢
首次启动时 TEI 需要加载 bge-large-zh-v1.5 模型（约 1.2GB），约 30-60 秒。模型已预打包在 `docker/tei-model/`，无需联网下载。

### DeepSeek API 连接失败
检查 `.env` 中的 `DEEPSEEK_API_KEY` 是否正确（sk- 开头）。API 地址：`https://api.deepseek.com/v1`。
