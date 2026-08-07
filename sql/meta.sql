SET NAMES utf8mb4;

DROP TABLE IF EXISTS column_metric;
DROP TABLE IF EXISTS metric_info;
DROP TABLE IF EXISTS column_info;
DROP TABLE IF EXISTS table_info;

CREATE TABLE table_info (
    id VARCHAR(64) PRIMARY KEY COMMENT '表编号',
    name VARCHAR(128) COMMENT '表名称',
    role VARCHAR(32) COMMENT '表类型(fact/dim)',
    description TEXT COMMENT '表描述'
);

CREATE TABLE column_info (
    id VARCHAR(64) PRIMARY KEY COMMENT '列编号',
    name VARCHAR(128) COMMENT '列名称',
    type VARCHAR(64) COMMENT '数据类型',
    role VARCHAR(32) COMMENT '列类型(primary_key,foreign_key,measure,dimension)',
    examples JSON COMMENT '数据示例',
    description TEXT COMMENT '列描述',
    alias JSON COMMENT '列别名',
    table_id VARCHAR(64) COMMENT '所属表编号'
);

CREATE TABLE metric_info (
    id VARCHAR(64) PRIMARY KEY COMMENT '指标编码',
    name VARCHAR(128) COMMENT '指标名称',
    description TEXT COMMENT '指标描述',
    relevant_columns JSON COMMENT '关联的列',
    alias JSON COMMENT '指标别名'
);

CREATE TABLE column_metric (
    column_id VARCHAR(64) COMMENT '列编号',
    metric_id VARCHAR(64) COMMENT '指标编号',
    PRIMARY KEY (column_id, metric_id)
);

CREATE TABLE IF NOT EXISTS term_cache (
    term VARCHAR(100) NOT NULL COMMENT '用户术语/关键词',
    column_id VARCHAR(200) NOT NULL COMMENT '命中字段ID',
    table_id VARCHAR(64) COMMENT '所属表ID',
    hit_count INT DEFAULT 1 COMMENT '命中次数',
    last_hit DATETIME DEFAULT NOW() COMMENT '最后命中时间',
    PRIMARY KEY (term, column_id)
);

CREATE TABLE IF NOT EXISTS indicator_formula (
    term VARCHAR(64) PRIMARY KEY COMMENT '用户术语/问题中的指标名',
    aliases JSON COMMENT '同义词列表，如存贷比的别名["存贷款比例","贷存比","存贷款比率"]',
    formula_type VARCHAR(32) NOT NULL DEFAULT 'computed' COMMENT 'computed计算 / direct直查',
    index_names JSON NOT NULL COMMENT '涉及的指标名称列表',
    sql_template VARCHAR(500) NOT NULL COMMENT 'SQL计算模板，用{0}{1}等占位',
    description VARCHAR(500) COMMENT '计算口径说明'
);

-- 岗位指标权限：哪个岗位能查哪些指标
CREATE TABLE IF NOT EXISTS query_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(64) COMMENT '用户名',
    query_text TEXT COMMENT '用户问题',
    generated_sql TEXT COMMENT '生成的SQL',
    result_status VARCHAR(32) COMMENT '成功/失败/权限拦截',
    execute_time_ms INT COMMENT '执行耗时(毫秒)',
    created_at DATETIME DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS role_permission (
    role_name VARCHAR(64) NOT NULL COMMENT '岗位名',
    indicator_group VARCHAR(64) COMMENT '指标组名',
    indicator_name VARCHAR(128) NOT NULL COMMENT '指标名(对应index_list.index_name)',
    PRIMARY KEY (role_name, indicator_name)
);

-- 已扩充org和allowed_orgs字段的用户表
CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(64) UNIQUE NOT NULL COMMENT '用户名',
    password_hash VARCHAR(128) NOT NULL COMMENT '密码哈希',
    level VARCHAR(32) NOT NULL DEFAULT '普通员工' COMMENT '职级',
    position VARCHAR(64) NOT NULL DEFAULT '综合管理' COMMENT '岗位',
    org_name VARCHAR(128) COMMENT '所属机构',
    allowed_orgs JSON COMMENT '可查机构范围',
    created_at DATETIME DEFAULT NOW() COMMENT '创建时间'
);
