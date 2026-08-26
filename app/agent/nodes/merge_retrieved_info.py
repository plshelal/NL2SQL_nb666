from dataclasses import asdict
from langgraph.runtime import Runtime
import  asyncio
from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState, TableInfoState, ColumnInfoState, MetricInfoState
from app.core.log import logger
from app.models.es.value_info_es import ValueInfoEs
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL
from app.models.qdrant.column_info_qdrant import ColumnInfoQdrant
from app.models.qdrant.metric_info_qdrant import MetricInfoQdrant




async def merge_retrieved_info(state:DataAgentState,runtime:Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "合并召回信息"})

    try:
        # 获取召回的字段列表
        retrieved_columns:list[ColumnInfoQdrant]=state["retrieved_columns"]
        # 获取召回的字段取值
        retrieved_values:list[ValueInfoEs]=state["retrieved_values"]
        # 获取召回的指标信息
        retrieved_metrics:list[MetricInfoQdrant]=state["retrieved_metrics"]

        # 获取持久层操作对象
        meta_mysql_repository =runtime.context["meta_mysql_repository"]


        # 定义收集表信息的列表
        table_infos:list[TableInfoState]=[]
        # 定义收集指标信息列表
        metric_infos:list[MetricInfoState]=[]

        # 去重：转换召回的字段列表结构为字典结构
        retrieved_columns_map:dict[str,ColumnInfoQdrant]={retrieved_column["id"]: retrieved_column for retrieved_column in retrieved_columns}

        # 1.收集所有缺失的字段ID(指标关联 + 取值对应),一次性批量查
        missing_col_ids = set()
        for retrieved_metric in retrieved_metrics:
            for rc in retrieved_metric["relevant_columns"]:
                if rc not in retrieved_columns_map:
                    missing_col_ids.add(rc)
        for retrieved_value in retrieved_values:
            cid = retrieved_value["column_id"]
            if cid not in retrieved_columns_map:
                missing_col_ids.add(cid)

        # 批量查询缺失字段(1次SQL替代N次串行)
        if missing_col_ids:
            from sqlalchemy import text as _t2
            placeholders = ",".join([f":i{n}" for n in range(len(missing_col_ids))])
            params = {f"i{n}": cid for n, cid in enumerate(missing_col_ids)}
            rows = (await meta_mysql_repository.session.execute(_t2(
                f"SELECT id, name, type, role, examples, description, alias, table_id "
                f"FROM column_info WHERE id IN ({placeholders})"
            ), params)).fetchall()
            for row in rows:
                ex = _parse_json(row.examples)
                al = _parse_json(row.alias)
                col = ColumnInfoMySQL(id=row.id, name=row.name, type=row.type,
                                       role=row.role, examples=ex,
                                       description=row.description, alias=al,
                                       table_id=row.table_id)
                retrieved_columns_map[row.id] = _conver_column_info_form_mysql_to_qdrant(col)

        # 取值的 examples 补充
        for retrieved_value in retrieved_values:
            column_id = retrieved_value["column_id"]
            column_value = retrieved_value["value"]
            if column_id in retrieved_columns_map:
                if column_value not in retrieved_columns_map[column_id]["examples"]:
                    retrieved_columns_map[column_id]["examples"].append(column_value)

        # 3.根据所有的字段，以表分组整合
        #表1----字段1，字段2，字段3
        #表2----字段1，字段2，字段3
        # key--table_id    value--字段列表
        table_to_column_map:dict[str,list[ColumnInfoQdrant]]={}

        # 遍历召回的字段列表,构建表和字段的关联
        for column in retrieved_columns_map.values():

            # 获取当前字段对应的表信息
            table_id = column["table_id"]
            # 判断
            if table_id not in table_to_column_map:

                table_to_column_map[table_id]=[]

            # 添加字段到表的关联中
            table_to_column_map[table_id].append(column)



        # 批量查询所有表的主外键(1次SQL替代N次串行往返)
        all_table_ids = list(table_to_column_map.keys())
        if all_table_ids:
            from sqlalchemy import text as _t3
            tk_places = ",".join([f":t{n}" for n in range(len(all_table_ids))])
            tk_params = {f"t{n}": tid for n, tid in enumerate(all_table_ids)}
            key_rows = (await meta_mysql_repository.session.execute(_t3(
                "SELECT id, name, type, role, examples, description, alias, table_id "
                "FROM column_info WHERE table_id IN (" + tk_places + ") "
                "AND role IN ('primary_key', 'foreign_key')"
            ), tk_params)).fetchall()
            for row in key_rows:
                tid = row.table_id
                if tid not in table_to_column_map:
                    continue
                col_ids = [c["id"] for c in table_to_column_map[tid]]
                if row.id not in col_ids:
                    ex = _parse_json(row.examples)
                    al = _parse_json(row.alias)
                    col = ColumnInfoMySQL(id=row.id, name=row.name, type=row.type,
                                           role=row.role, examples=ex,
                                           description=row.description, alias=al,
                                           table_id=row.table_id)
                    table_to_column_map[tid].append(_conver_column_info_form_mysql_to_qdrant(col))

        # 批量查询所有表信息(1次SQL替代N次串行往返)
        if all_table_ids:
            from sqlalchemy import text as _t4
            tb_rows = (await meta_mysql_repository.session.execute(_t4(
                "SELECT id, name, role, description FROM table_info WHERE id IN (" + tk_places + ")"
            ), tk_params)).fetchall()
            for row in tb_rows:
                if row.id not in table_to_column_map:
                    continue
                columns_state = [ColumnInfoState(
                    name=c['name'], type=c['type'], role=c['role'],
                    examples=c['examples'], description=c["description"], alias=c["alias"]
                ) for c in table_to_column_map[row.id]]
                table_infos.append(TableInfoState(
                    name=row.name, role=row.role, description=row.description,
                    columns=columns_state
                ))



        logger.info(f"合并表信息完成，表信息{[table_info['name'] for table_info in table_infos]}")


        # 处理指标信息，构建指标数据结构

        for retrieved_metric in retrieved_metrics:

            # 构建实体
            metric_info_state=MetricInfoState(**retrieved_metric)
            # 收集指标数据
            metric_infos.append(metric_info_state)

        logger.info(f"合并指标信息完成，表信息{[metric_info['name'] for metric_info in metric_infos]}")

        return {"table_infos":table_infos,"metric_infos":metric_infos}
    except Exception as e:
        logger.error(f"合并召回信息异常{str(e)}")
        raise















def _conver_column_info_form_mysql_to_qdrant(column_info_mysql:ColumnInfoMySQL)->ColumnInfoQdrant:
    return ColumnInfoQdrant(
        id=column_info_mysql.id,
        name=column_info_mysql.name,
        type=column_info_mysql.type,
        role=column_info_mysql.role,
        examples=column_info_mysql.examples,
        description=column_info_mysql.description,
        alias=column_info_mysql.alias,
        table_id=column_info_mysql.table_id
    )


def _parse_json(v):
    """MySQL 原始 SQL 查回的 JSON 字段是字符串,需手动反序列化成 list"""
    if v is None:
        return []
    if isinstance(v, (list, dict)):
        return v
    try:
        import json
        return json.loads(v)
    except Exception:
        return []

