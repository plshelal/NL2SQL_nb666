from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def add_extra_context(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "添加额外上下文"})
    try:
        #  获取repository
        dw_mysql_repository=runtime.context["dw_mysql_repository"]

        # 只注入数据库方言/版本(对生成合法SQL有用,与日期无关)
        # 2026-08-18:取消"当前时间"注入——墙钟日期与数据真实范围(截止2026-04-30)不符,
        # 会让模型把"最新"误署为近期日期;日期语义交给模型自己的时间知识,数据边界由查询结果自然呈现
        db_info:dict=await dw_mysql_repository.get_db_info()
        logger.info(f"额外上下文信息添加，数据库信息{db_info}")
        return {"db_info":db_info}

    except Exception as e:
        logger.error(f"添加额外上下文信息异常，{str(e)}")
        raise

