from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import meta_mysql_client_manager, dw_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager


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

    # 远程微调模型 + Few-shot RAG 预计算
    import os
    remote_url = os.getenv("LOCAL_MODEL_URL", "http://192.168.3.41:8100/generate")
    use_local = os.getenv("SQL_MODEL", "deepseek") == "local"
    from app.agent.local_llm import init_remote_model
    init_remote_model(remote_url, enabled=use_local)
    print(f"[SQL模型] {'本地Qwen' if use_local else 'DeepSeek'}（设SQL_MODEL=local切换）")

    from app.agent.fewshot_rag import precompute_embeddings
    await precompute_embeddings()

    yield
    # 释放资源
    await qdrant_client_manager.close()
    await es_client_manager.close()
    await meta_mysql_client_manager.close()
    await dw_mysql_client_manager.close()
