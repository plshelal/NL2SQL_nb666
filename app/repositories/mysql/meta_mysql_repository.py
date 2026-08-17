from sqlalchemy import Select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log import logger
from app.models.mysql.column_info_mysql import ColumnInfoMySQL
from app.models.mysql.column_metric_mysql import ColumnMetricMySQL
from app.models.mysql.metric_info_mysql import MetricInfoMySQL
from app.models.mysql.table_info_mysql import TableInfoMySQL


class MetaMysqlRepository:
    def __init__(self,session:AsyncSession):
        self.session = session

    async def ensure_term_cache_table(self):
        """确保术语缓存表存在"""
        await self.session.execute(text("""
            CREATE TABLE IF NOT EXISTS term_cache (
                term VARCHAR(100) NOT NULL,
                column_id VARCHAR(200) NOT NULL,
                table_id VARCHAR(64),
                hit_count INT DEFAULT 1,
                last_hit DATETIME DEFAULT NOW(),
                PRIMARY KEY (term, column_id)
            )
        """))

    async def get_cached_columns(self, terms: list[str]) -> dict[str, list[dict]]:
        """查询术语缓存，仅返回命中>=3次的可靠映射"""
        if not terms:
            return {}
        mapping: dict[str, list[dict]] = {}
        for term in terms:
            result = await self.session.execute(
                text("SELECT column_id, table_id, hit_count FROM term_cache WHERE term = :term"),
                {"term": term}
            )
            rows = result.fetchall()
            if rows:
                # 每次查询都计数，但只有≥3次命中才返回（避免一次错误永久污染）
                await self.session.execute(
                    text("UPDATE term_cache SET hit_count = hit_count + 1, last_hit = NOW() WHERE term = :term"),
                    {"term": term}
                )
                trusted = [r for r in rows if r.hit_count >= 2]  # 当前计数>=2，加上本次=3
                if trusted:
                    mapping[term] = [{"column_id": r.column_id, "table_id": r.table_id} for r in trusted]
        if mapping:
            logger.info(f"术语缓存命中(≥3次): {list(mapping.keys())}")
        return mapping

    async def save_term_mappings(self, terms: list[str], column_id: str, table_id: str):
        """保存术语→字段映射"""
        for term in terms:
            if not term or len(term) < 2:
                continue
            await self.session.execute(
                text("""
                    INSERT INTO term_cache (term, column_id, table_id)
                    VALUES (:term, :column_id, :table_id)
                    ON DUPLICATE KEY UPDATE hit_count = hit_count + 1, last_hit = NOW()
                """),
                {"term": term, "column_id": column_id, "table_id": table_id}
            )

    async def save_table_infos(self, table_infos:list[TableInfoMySQL]):
        """
        保存表信息到meta数据库
        :param table_infos:
        :return:
        """
        self.session.add_all(table_infos)

    async def save_column_infos(self, column_infos:list[ColumnInfoMySQL]):
        """
        保存字段信息到meta数据库
        :param column_infos:
        :return:
        """
        self.session.add_all(column_infos)

    async def save_metrics(self, metric_infos:list[MetricInfoMySQL]):
        """
        保存指标信息到meta数据库
        :param metric_infos:
        :return:
        """
        self.session.add_all(metric_infos)

    async def save_column_metrics(self, column_metrics:list[ColumnMetricMySQL]):
        """
        保存字段指标关联信息到meta数据
        :param column_metrics:
        :return:
        """
        self.session.add_all(column_metrics)

    async def get_column_info_by_id(self, column_id:str):
        """
        根据字段id查询字段信息对象
        :param relevant_column:
        :return:
        """
        return await self.session.get(ColumnInfoMySQL,column_id)

    async def get_key_columns_by_table_id(self, table_id:str):
        """
        查询指定表的主外键字段
        select*
        from column_info
        where role in ('primary_key', 'foreign_key')
          and table_id = 'fact_order'
        :param table_id:
        :return:
        """
        # 定义sql
        sql ="""
            select*
            from column_info
            where role in ('primary_key', 'foreign_key')
              and table_id = :table_id
        """
        # 设置封装结构
        query=Select(ColumnInfoMySQL).from_statement(text(sql))
        # 执行sql
        result = await self.session.execute(query,{"table_id":table_id})
        # 结果ScalarResult-->[(ColumnInfoMysql对象),(ColumnInfoMysql对象),(ColumnInfoMysql对象)]
        return result.scalars().fetchall()

    async def get_table_by_id(self, table_id:str):
        """
        根据表ID查询表信息对象
        :param table_id:
        :return:
        """
        return await self.session.get(TableInfoMySQL,table_id)

    async def get_indicator_formulas(self, terms: list[str]) -> dict[str, dict]:
        """根据术语列表查询计算公式，返回 {term: {index_names, sql_template, description}}
        同时匹配别名：存贷款比例 → 命中 存贷比 的别名字段"""
        if not terms:
            return {}
        result = {}
        for term in terms:
            row = await self.session.execute(
                text("SELECT term, formula_type, index_names, sql_template, description FROM indicator_formula WHERE term = :t OR JSON_CONTAINS(aliases, JSON_QUOTE(:t))"),
                {"t": term}
            )
            r = row.fetchone()
            if r:
                result[r.term] = {
                    "formula_type": r.formula_type,
                    "index_names": r.index_names,
                    "sql_template": r.sql_template,
                    "description": r.description,
                }
        return result

    async def seed_indicator_formulas(self):
        """初始化计算指标公式"""
        formulas = [
            ("存贷比", '["存贷款比例","贷存比","存贷款比率"]', '["各项贷款余额","各项存款余额"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "各项贷款余额÷各项存款余额×100%"),
            ("净利润率", '["净利率","净利润占比"]', '["净利润","营业收入"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "净利润÷营业收入×100%"),
            ("对公贷款占比", '["对公贷款比例","对公贷款比重"]', '["对公贷款余额","各项贷款余额"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "对公贷款余额÷各项贷款余额×100%"),
            ("个人贷款占比", '["个人贷款比例","个人贷款比重"]', '["个人贷款余额","各项贷款余额"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "个人贷款余额÷各项贷款余额×100%"),
            ("对公存款占比", '["对公存款比例","对公存款比重"]', '["对公存款余额","各项存款余额"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "对公存款余额÷各项存款余额×100%"),
            ("个人存款占比", '["个人存款比例","个人存款比重"]', '["个人存款余额","各项存款余额"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "个人存款余额÷各项存款余额×100%"),
            ("中间业务收入占比", '["中收占比","中间业务收入比重"]', '["中间业务收入","营业收入"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "中间业务收入÷营业收入×100%"),
            ("净利息收入占比", '["净息差占比","利息收入占比"]', '["净利息收入","营业收入"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "净利息收入÷营业收入×100%"),
            ("不良逾期合计占贷款比", '[]', '["不良贷款率","逾期贷款率"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)+SUM(CASE WHEN index_name='{1}' THEN index_value END)",
             "不良贷款率+逾期贷款率"),
            ("人均利润", '["人均创利","人均盈利","人均净利润"]', '["净利润","员工人数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "净利润÷员工人数"),
            ("网点平均存款", '["网均存款","单点存款"]', '["各项存款余额","网点数量"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "各项存款余额÷网点数量"),
        ]
        # 新增指标（23+4）
        new_formulas = [
            ("人均存款", '["人均吸存","人均储蓄"]', '["各项存款余额","员工人数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "各项存款余额÷员工人数，万元/人"),
            ("人均贷款", '["人均放贷","人均信贷"]', '["各项贷款余额","员工人数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "各项贷款余额÷员工人数，万元/人"),
            ("人均营收", '["人均营业收入","全员劳动生产率"]', '["营业收入","员工人数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "营业收入÷员工人数，万元/人"),
            ("点均存款", '["网均存款","单点存款"]', '["各项存款余额","网点数量"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "各项存款余额÷网点数量"),
            ("点均贷款", '["网均贷款","单点贷款"]', '["各项贷款余额","网点数量"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "各项贷款余额÷网点数量"),
            ("点均营收", '["网点营收","单点营业收入"]', '["营业收入","网点数量"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "营业收入÷网点数量"),
            ("点均净利润", '["网点净利润"]', '["净利润","网点数量"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "净利润÷网点数量"),
            ("人均客户数", '["人均服务客户","人均客户量"]', '["个人客户数","员工人数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "个人客户数÷员工人数"),
            ("营业利润率", '["利润率","净利润率"]', '["净利润","营业收入"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)*100",
             "净利润÷营业收入×100%"),
            ("拨贷率", '["拨贷比","拨备贷款比"]', '["拨备覆盖率","不良贷款率"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)*SUM(CASE WHEN index_name='{1}' THEN index_value END)/100",
             "拨备覆盖率×不良贷款率÷100"),
            ("非不良贷款占比", '["正常贷款占比","资产健康度"]', '["不良贷款率"]',
             "(1 - SUM(CASE WHEN index_name='{0}' THEN index_value END)/100)*100",
             "(1-不良贷款率/100)×100，即100%-不良率"),
            ("零售户均存款", '["个人户均存款","零售客户平均存款"]', '["个人存款余额","个人客户数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "个人存款余额÷个人客户数"),
            ("零售户均贷款", '["个人户均贷款","零售客户平均贷款"]', '["个人贷款余额","个人客户数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "个人贷款余额÷个人客户数"),
            ("对公户均存款", '["企业户均存款","对公客户平均存款"]', '["对公存款余额","对公客户数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "对公存款余额÷对公客户数"),
            ("对公户均贷款", '["企业户均贷款","对公客户平均贷款"]', '["对公贷款余额","对公客户数"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END)/NULLIF(SUM(CASE WHEN index_name='{1}' THEN index_value END),0)",
             "对公贷款余额÷对公客户数"),
            ("不良率监管边际", '["不良率安全边际"]', '["不良贷款率"]',
             "5 - SUM(CASE WHEN index_name='{0}' THEN index_value END)",
             "5% - 不良贷款率，正值=达标"),
            ("拨备覆盖率监管边际", '["拨备安全边际"]', '["拨备覆盖率"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END) - 150",
             "拨备覆盖率 - 150%，正值=达标"),
            ("资本充足率监管边际", '["资本安全边际"]', '["资本充足率"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END) - 10.5",
             "资本充足率 - 10.5%，正值=达标"),
            ("逾期与不良偏差度", '["逾期不良剪刀差","逾期不良偏离"]', '["逾期贷款率","不良贷款率"]',
             "SUM(CASE WHEN index_name='{0}' THEN index_value END) - SUM(CASE WHEN index_name='{1}' THEN index_value END)",
             "逾期贷款率 - 不良贷款率"),
        ]
        for term, aliases, names, tmpl, desc in new_formulas:
            await self.session.execute(
                text("INSERT INTO indicator_formula (term, aliases, formula_type, index_names, sql_template, description) VALUES (:t,:a,'computed',:n,:s,:d) ON DUPLICATE KEY UPDATE aliases=VALUES(aliases), sql_template=VALUES(sql_template), description=VALUES(description)"),
                {"t": term, "a": aliases, "n": names, "s": tmpl, "d": desc}
            )
        logger.info(f"指标公式已初始化: {len(formulas) + len(new_formulas)} 条")

        # 补充新指标的岗位权限
        new_perms = [
            ("人均存款", "财务人员"), ("人均存款", "综合管理"),
            ("人均贷款", "客户经理"), ("人均贷款", "综合管理"),
            ("人均营收", "财务人员"), ("人均营收", "综合管理"),
            ("人均利润", "财务人员"), ("人均利润", "综合管理"),
            ("点均存款", "财务人员"), ("点均存款", "综合管理"),
            ("点均贷款", "客户经理"), ("点均贷款", "综合管理"),
            ("点均营收", "财务人员"), ("点均营收", "综合管理"),
            ("点均净利润", "财务人员"), ("点均净利润", "综合管理"),
            ("人均客户数", "客户经理"), ("人均客户数", "综合管理"),
            ("营业利润率", "财务人员"), ("营业利润率", "综合管理"),
            ("拨贷率", "风控专员"), ("拨贷率", "综合管理"),
            ("非不良贷款占比", "风控专员"), ("非不良贷款占比", "综合管理"),
            ("零售户均存款", "客户经理"), ("零售户均存款", "综合管理"),
            ("零售户均贷款", "客户经理"), ("零售户均贷款", "综合管理"),
            ("对公户均存款", "客户经理"), ("对公户均存款", "综合管理"),
            ("对公户均贷款", "客户经理"), ("对公户均贷款", "综合管理"),
            ("不良率监管边际", "风控专员"), ("不良率监管边际", "综合管理"),
            ("拨备覆盖率监管边际", "风控专员"), ("拨备覆盖率监管边际", "综合管理"),
            ("资本充足率监管边际", "风控专员"), ("资本充足率监管边际", "综合管理"),
            ("逾期与不良偏差度", "风控专员"), ("逾期与不良偏差度", "综合管理"),
        ]
        # 原有11个计算指标的权限
        old_perms = [
            ("存贷比", "综合管理"), ("存贷比", "财务人员"), ("存贷比", "风控专员"),
            ("净利润率", "综合管理"), ("净利润率", "财务人员"),
            ("对公贷款占比", "综合管理"), ("对公贷款占比", "客户经理"),
            ("个人贷款占比", "综合管理"), ("个人贷款占比", "客户经理"),
            ("对公存款占比", "综合管理"), ("对公存款占比", "财务人员"),
            ("个人存款占比", "综合管理"), ("个人存款占比", "财务人员"),
            ("中间业务收入占比", "综合管理"), ("中间业务收入占比", "财务人员"),
            ("净利息收入占比", "综合管理"), ("净利息收入占比", "财务人员"),
            ("不良逾期合计占贷款比", "风控专员"), ("不良逾期合计占贷款比", "综合管理"),
            ("人均利润", "综合管理"), ("人均利润", "财务人员"),
            ("网点平均存款", "综合管理"), ("网点平均存款", "财务人员"),
        ]
        for ind, role in old_perms + new_perms:
            await self.session.execute(
                text("INSERT IGNORE INTO role_permission (role_name, indicator_name) VALUES (:r, :i)"),
                {"r": role, "i": ind}
            )
        logger.info(f"权限数据已补充: {len(old_perms) + len(new_perms)} 条")
