from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import meta_mysql_client_manager, dw_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.core.log import logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化客户端对象
    embedding_client_manager.init()
    qdrant_client_manager.init()
    es_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()

    # 确保术语缓存表和用户表存在
    from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository
    from sqlalchemy import text
    async with meta_mysql_client_manager.session_factory() as session:
        meta_repo = MetaMysqlRepository(session)
        await meta_repo.ensure_term_cache_table()
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS indicator_formula (
                term VARCHAR(64) PRIMARY KEY,
                aliases JSON,
                formula_type VARCHAR(32) NOT NULL DEFAULT 'computed',
                index_names JSON NOT NULL,
                sql_template VARCHAR(500) NOT NULL,
                description VARCHAR(500)
            )
        """))
        await meta_repo.seed_indicator_formulas()
        # 建权限表
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS query_log (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(64),
                query_text TEXT,
                generated_sql TEXT,
                result_status VARCHAR(32),
                execute_time_ms INT,
                created_at DATETIME DEFAULT NOW()
            )
        """))
        # 幂等补列:反馈+审核闭环所需(老库无则加)
        for col_ddl in (
            "ADD COLUMN result_summary MEDIUMTEXT COMMENT 'AI最终回答摘要(反馈/审核依据)'",
            "ADD COLUMN feedback TEXT COMMENT '用户对本次回答的自然语言反馈'",
            "ADD COLUMN review_status VARCHAR(16) DEFAULT 'none' COMMENT 'none/pending/correct/problem'",
            "ADD COLUMN review_note TEXT COMMENT '审核员补充描述'",
        ):
            col_name = col_ddl.split("ADD COLUMN ")[1].split(" ")[0]
            r = await session.execute(text(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME='query_log' AND COLUMN_NAME=:c"
            ), {"c": col_name})
            if r.scalar() == 0:
                await session.execute(text(f"ALTER TABLE query_log {col_ddl}"))
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS role_permission (
                role_name VARCHAR(64) NOT NULL,
                indicator_group VARCHAR(64),
                indicator_name VARCHAR(128) NOT NULL,
                PRIMARY KEY (role_name, indicator_name)
            )
        """))
        await session.commit()

        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                username VARCHAR(64) UNIQUE NOT NULL,
                password_hash VARCHAR(128) NOT NULL,
                level VARCHAR(32) NOT NULL DEFAULT '普通员工',
                position VARCHAR(64) NOT NULL DEFAULT '综合管理',
                created_at DATETIME DEFAULT NOW()
            )
        """))
        await session.commit()

        # 外部数据缓存 + 经验日志 + 蒸馏规则(自迭代 P0/B2)
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS external_data_cache (
                query_hash VARCHAR(64) PRIMARY KEY,
                tool_name VARCHAR(64),
                params JSON,
                result_json JSON,
                source VARCHAR(256),
                fetched_at DATETIME,
                expires_at DATETIME,
                INDEX idx_expires (expires_at)
            )
        """))
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS experience_log (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                query_text TEXT,
                final_sql TEXT,
                outcome VARCHAR(32),
                error_message TEXT,
                correction_path VARCHAR(32),
                latency_ms INT,
                user_position VARCHAR(64),
                created_at DATETIME DEFAULT NOW(),
                INDEX idx_outcome (outcome),
                INDEX idx_created (created_at)
            )
        """))
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS distilled_rules (
                id BIGINT PRIMARY KEY AUTO_INCREMENT,
                rule_type VARCHAR(32),
                trigger_pattern VARCHAR(256),
                action TEXT,
                source VARCHAR(32) DEFAULT 'auto_distilled',
                confidence FLOAT DEFAULT 0.5,
                status VARCHAR(16) DEFAULT 'pending',
                evidence_count INT DEFAULT 0,
                created_at DATETIME DEFAULT NOW()
            )
        """))
        await session.commit()

    # Few-shot RAG 预计算(本地 LoRA 模型已废弃,统一走 DeepSeek)
    from app.agent.fewshot_rag import precompute_embeddings
    await precompute_embeddings()

    # query_log 老表自动补列(result_data/is_rejected 被 audit_router 和 execute_sql 使用
    # 但原 sql/meta.sql 建表时没有这些列,需自动迁移)
    async with meta_mysql_client_manager.session_factory() as s:
        for col, ddl in [
            ("result_data", "ADD COLUMN result_data TEXT"),
            ("is_rejected", "ADD COLUMN is_rejected BOOLEAN DEFAULT 0"),
            ("analyzed_at", "ADD COLUMN analyzed_at DATETIME DEFAULT NULL"),
            ("tool_trace", "ADD COLUMN tool_trace TEXT"),
        ]:
            r = await s.execute(text(
                "SELECT COUNT(*) n FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='query_log' AND COLUMN_NAME=:c"), {"c": col})
            if r.scalar() == 0:
                await s.execute(text(f"ALTER TABLE query_log {ddl}"))
        await s.commit()

    # 凌晨3点定时归纳任务(后台 asyncio,服务存活期间每天触发一次)
    import asyncio as _aio
    import datetime as _dt
    async def _semantic_analyze_loop():
        while True:
            try:
                now = _dt.datetime.now()
                # 下次3:00
                next3 = now.replace(hour=3, minute=0, second=0, microsecond=0)
                if next3 <= now:
                    next3 = next3 + _dt.timedelta(days=1)
                sleep_s = (next3 - now).total_seconds()
                await _aio.sleep(sleep_s)
                from app.agent.knowledge_feedback import analyze_problem_queries
                n = await analyze_problem_queries()
                logger.info(f"[定时归纳] 凌晨3点归纳完成,抽出 {n} 条 semantic_hint 规则")
            except Exception as e:
                logger.warning(f"[定时归纳] 异常(继续循环): {e}")
                await _aio.sleep(3600)
    _aio.create_task(_semantic_analyze_loop())

    yield
    # 释放资源
    from app.agent.tools.ifind_mcp import ifind_mcp_manager
    await ifind_mcp_manager.close()
    await qdrant_client_manager.close()
    await es_client_manager.close()
    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()
