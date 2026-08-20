import asyncio

from langgraph.constants import START, END
from langgraph.graph import StateGraph

from app.agent.context import DataAgentContext
from app.agent.nodes.add_extra_context import add_extra_context
from app.agent.nodes.correct_sql import correct_sql
from app.agent.nodes.execute_sql import execute_sql
from app.agent.nodes.expand_keywords import expand_keywords
from app.agent.nodes.extract_keywords import extract_keywords
from app.agent.nodes.filter_metric import filter_metric
from app.agent.nodes.filter_table import filter_table
from app.agent.nodes.formula_match import formula_match
from app.agent.nodes.generate_sql import generate_sql
from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.agent.nodes.recall_column import recall_column
from app.agent.nodes.recall_metric import recall_metric
from app.agent.nodes.recall_value import recall_value
from app.agent.nodes.validate_sql import validate_sql

from app.agent.state import DataAgentState
from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import meta_mysql_client_manager, dw_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.values_es_repository import ValueEsRepository
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository
from app.repositories.qdrant.column_qdrant_respository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository

# 内部图(2026-08-18 瘦身:纯执行层,11 节点)
# 决策职责(路由/组词/消歧/澄清)全部归外层 Agent(orchestrator);
# 内部只做确定性转换:分词→公式匹配→召回→合并→过滤→上下文→SQL→校验→执行。
# 已删节点: schema_link(组词段,公式匹配独立成 formula_match)、
#          assess_clarify(外层 Agent 按行为总则反问)、
#          route_intent/schema_all(路由已废,恒走召回链)、
#          call_external_tool(外层 Agent 直接调外部工具)。
# 权限链路(execute_sql 白名单校验/generate_sql 权限收敛/审计/经验日志)全部原样保留。
graph_builder = StateGraph(state_schema=DataAgentState, context_schema=DataAgentContext)

graph_builder.add_node("extract_keywords", extract_keywords)
graph_builder.add_node("formula_match", formula_match)
graph_builder.add_node("expand_keywords", expand_keywords)
graph_builder.add_node("recall_column", recall_column)
graph_builder.add_node("recall_metric", recall_metric)
graph_builder.add_node("recall_value", recall_value)
graph_builder.add_node("merge_retrieved_info", merge_retrieved_info)
graph_builder.add_node("filter_metric", filter_metric)
graph_builder.add_node("filter_table", filter_table)
graph_builder.add_node("add_extra_context", add_extra_context)
graph_builder.add_node("generate_sql", generate_sql)
graph_builder.add_node("validate_sql", validate_sql)
graph_builder.add_node("correct_sql", correct_sql)
graph_builder.add_node("execute_sql", execute_sql)

graph_builder.add_edge(START, "extract_keywords")
graph_builder.add_edge("extract_keywords", "formula_match")
graph_builder.add_edge("formula_match", "expand_keywords")
graph_builder.add_edge("expand_keywords", "recall_column")
graph_builder.add_edge("expand_keywords", "recall_metric")
graph_builder.add_edge("expand_keywords", "recall_value")
graph_builder.add_edge("recall_column", "merge_retrieved_info")
graph_builder.add_edge("recall_value", "merge_retrieved_info")
graph_builder.add_edge("recall_metric", "merge_retrieved_info")
graph_builder.add_edge("merge_retrieved_info", "filter_metric")
graph_builder.add_edge("merge_retrieved_info", "filter_table")
graph_builder.add_edge("filter_metric", "add_extra_context")
graph_builder.add_edge("filter_table", "add_extra_context")
graph_builder.add_edge("add_extra_context", "generate_sql")
graph_builder.add_conditional_edges("generate_sql",
    lambda state: END if (state.get("perm_rejected") or state.get("missing_info")) else "validate_sql",
    {END: END, "validate_sql": "validate_sql"})

graph_builder.add_conditional_edges("validate_sql",
                                    lambda state: "execute_sql" if state.get("error") is None else "correct_sql",
                                    {"execute_sql": "execute_sql", "correct_sql": "correct_sql"})

graph_builder.add_edge("correct_sql", "execute_sql")
# execute 后:权限拦截 → END;有错误且纠错次数<2 → correct;否则(成功或纠错耗尽) → END
graph_builder.add_conditional_edges("execute_sql",
    lambda state: END if (state.get("perm_rejected") or not state.get("error") or state.get("retry_count", 0) >= 2) else "correct_sql",
    {"correct_sql": "correct_sql", END: END})


graph = graph_builder.compile()

# 打印图的流程显示
# print(graph.get_graph().draw_mermaid())

if __name__ == '__main__':
    async def test():
        # 创建依赖对象
        # 初始化客户端对象
        embedding_client_manager.init()
        qdrant_client_manager.init()
        es_client_manager.init()
        meta_mysql_client_manager.init()
        dw_mysql_client_manager.init()
        # 创建状态信息
        state: DataAgentState = {"query": "统计各个分行的新增客户数"}
        # 获取session，构建对象
        async with meta_mysql_client_manager.session_factory() as meta_session,dw_mysql_client_manager.session_factory() as dw_session:

            # 创建repository
            embeddings = embedding_client_manager.embeddings
            column_qdrant_repository= ColumnQdrantRepository(qdrant_client_manager.client)
            metric_qdrant_repository= MetricQdrantRepository(qdrant_client_manager.client)
            value_es_repository=ValueEsRepository(es_client_manager.client)
            meta_mysql_repository=MetaMysqlRepository(meta_session)
            dw_mysql_repository=DwMysqlRepository(dw_session)
            # 创建上下文信息
            context: DataAgentContext = {
                "embeddings": embeddings,
                "column_qdrant_repository": column_qdrant_repository,
                "metric_qdrant_repository": metric_qdrant_repository,
                "value_es_repository": value_es_repository,
                "meta_mysql_repository": meta_mysql_repository,
                "dw_mysql_repository": dw_mysql_repository,
            }
            async  for chunk in  graph.astream(input=state,context=context,stream_mode="custom"):
                print(chunk)


        # 释放资源
        await qdrant_client_manager.close()
        await es_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
    asyncio.run(test())
