SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- 衍生维度说明表
CREATE TABLE dim_derivative (
    dim_name VARCHAR(50)  COMMENT '衍生维度',
    dim_desc VARCHAR(800) COMMENT '衍生口径说明'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='衍生维度说明表';

-- 指标数据表（核心事实表）
CREATE TABLE index_data (
    data_date  DATE           COMMENT '数据日期',
    index_code VARCHAR(32)   COMMENT '指标编号',
    index_name VARCHAR(100)  COMMENT '指标名称',
    org_code   VARCHAR(32)   COMMENT '机构编号',
    index_value DECIMAL(18,4) COMMENT '指标值'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='指标数据表';

-- 指标清单表
CREATE TABLE index_list (
    index_code VARCHAR(32)  COMMENT '指标编号',
    index_name VARCHAR(100) COMMENT '指标名称',
    index_desc VARCHAR(500) COMMENT '指标含义',
    index_unit VARCHAR(32)  COMMENT '指标单位'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='指标清单表';

-- 机构信息表
CREATE TABLE org_info (
    org_code VARCHAR(32)  COMMENT '机构编号',
    org_name VARCHAR(100) COMMENT '机构名称'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='机构信息表';

-- 问答样例表（用于 Few-shot 训练）
CREATE TABLE qa_sample (
    qa_id    VARCHAR(40) COMMENT '问题编号',
    qa_type  VARCHAR(32) COMMENT '问题类型',
    qa_level VARCHAR(20) COMMENT '问题难度',
    question TEXT        COMMENT '问题描述',
    answer   TEXT        COMMENT '问题结果'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='问答样例清单表';

SET FOREIGN_KEY_CHECKS = 1;
