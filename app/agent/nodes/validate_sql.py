from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger

async def validate_sql(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "校验sql语句"})

    try:
        sql = state["sql"]
        # 用 sqlglot 做语法校验(纯内存解析,省一次 MySQL EXPLAIN 往返~0.1s)
        import sqlglot
        sqlglot.parse(sql, dialect="mysql")
        logger.info("校验sql正确")
        return {"error":None}
    except Exception as e:
        logger.error(f"校验sql异常{str(e)}")
        return {"error":str(e)}

