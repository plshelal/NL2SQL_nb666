import json

import yaml
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.llm import llm
from app.agent.nodes.execute_sql import ALL_INDS
from app.agent.state import DataAgentState, TableInfoState, MetricInfoState
from app.core.log import logger
from app.prompt.prompt_loader import loader_prompt


async def generate_sql(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    writer = runtime.stream_writer
    writer({"stage": "生成sql语句"})

    try:
        query: str = state["query"]
        table_infos: list[TableInfoState] = state["table_infos"]
        metric_infos: list[MetricInfoState] = state["metric_infos"]
        date_info = state["date_info"]
        db_info = state["db_info"]

        # ---- few-shot 示例 ----
        from app.agent.fewshot_rag import retrieve_examples
        examples = await retrieve_examples(query, top_k=3)

        # ---- system 角色（业务规则 + 权限 + 对话上下文）----
        system_lines = [
            "你是一个金融数据库专家，根据用户问题和表结构生成MySQL SQL。",
            "",
            "【核心规则】",
            "1. index_name存储具体指标名（各项存款余额、不良贷款率等）。比率/比值（存贷比=贷款÷存款）必须计算，严禁直接WHERE匹配",
            "2. 排名必须用子查询：先RANK() OVER全量排序，外层再WHERE筛选目标机构",
            "3. 指标方向：不良贷款率/逾期贷款率/成本收入比/不良贷款余额/拨贷率→越低越好(ASC)；其余→越高越好(DESC)。最好/靠前/前三→ORDER BY好的方向；最差/靠后/垫底/后三→ORDER BY坏的方向（反方向）",
            "4. 所有查询结果必须包含一列'单位'(JOIN index_list ON index_name 取 index_unit)。数值列不加后缀只出原始值，单位单独一列。禁止把单位和数值拼在一起",
            "5. 只输出一条纯文本SQL，不用markdown",
            "6. 同比/环比/变化/增幅/降幅类问题必须计算百分比变化，不能只返回原始值或差值",
            "6. 用户没提供的条件不准脑补。缺日期不要自动填，缺机构不要自动补，缺指标不要自动猜。自然返回空，由系统提示用户补充",
            "",
            "【参考示例】",
        ]
        for i, ex in enumerate(examples):
            system_lines.append(f"示例{i+1}：用户问\"{ex['question'][:80]}\" → {ex['sql'][:200]}")
        system_lines.append("")

        # 对话上下文
        chat_context = state.get("chat_context", {})
        if chat_context and chat_context.get("history"):
            system_lines.append("")
            system_lines.append("【对话历史】")
            for turn in chat_context["history"]:
                system_lines.append(f"用户: {turn['question']}")
                if turn.get('answer'):
                    system_lines.append(f"系统: {turn['answer'][:200]}")
            if chat_context.get("last_turn"):
                last = chat_context["last_turn"]
                system_lines.append(f"上一轮: {last['question']}")
                system_lines.append("本轮是上一轮的延续，继承指标和时间范围。")

        # 计算公式
        formula_context = state.get("formula_context", "")
        if formula_context:
            system_lines.append("")
            system_lines.append(formula_context)


        system_prompt = "\n".join(system_lines)

        # ---- user 角色（表结构+问题，跟训练格式一致）----
        tables_text = yaml.dump(table_infos, allow_unicode=True, sort_keys=False)
        user_prompt = (
            f"可用数据表:\n{tables_text}\n"
            f"可选指标参考:\n{yaml.dump(metric_infos, allow_unicode=True, sort_keys=False)}\n"
            f"当前时间: {date_info['date']}, MySQL {db_info['version']}\n\n"
            f"用户问题: {query}"
        )

        # ---- 调用模型 ----
        from app.agent.local_llm import local_sql_model, use_local_model
        if use_local_model and local_sql_model is not None:
            try:
                formatted = (
                    f"<|im_start|>system\n{system_prompt}<|im_end|>\n"
                    f"<|im_start|>user\n{user_prompt}<|im_end|>\n"
                    "<|im_start|>assistant\n"
                )
                sql = await local_sql_model.ainvoke(formatted)
                sql = sql.strip()
                logger.info(f"[本地模型] 生成的sql\n：{sql}")
            except Exception as e:
                logger.warning(f"本地模型失败，回退DeepSeek: {e}")
                sql = None
        else:
            sql = None

        if sql is None:
            # === 两步CoT：先列字段 → 判权 → 再生成SQL ===
            perms = state.get("user_permissions", {})
            allowed_inds = perms.get("allowed_indicators", [])
            allowed_orgs = perms.get("allowed_orgs", [])

            perm_note = ""
            is_admin = perms.get("is_admin", False)
            if not is_admin and (allowed_inds or allowed_orgs):
                # Step 0: 取公式表的组件指标（硬匹配，不让LLM猜）
                formula_indicators = set(state.get("formula_indicators", []))

                # Step 1: 让LLM列出计划使用的机构和指标
                plan_prompt = (
                    f"{system_prompt}\n\n"
                    f"用户问题: {query}\n\n"
                    f"请先列出回答这个问题需要用到的机构名称和指标名称（每行一个），不要输出SQL。格式:\n"
                    f"机构: xxx, xxx\n"
                    f"指标: xxx, xxx"
                )
                chain = PromptTemplate.from_template("{input}") | llm | StrOutputParser()
                plan_text = await chain.ainvoke({"input": plan_prompt})
                plan_text = plan_text.strip()
                logger.info(f"[两步CoT] 计划字段: {plan_text[:200]}")

                # 从plan_text提取机构和指标
                import re as _cot_re
                plan_orgs = set(_cot_re.findall(r"江苏省\S+农商行", plan_text))
                plan_inds = set()
                for m in _cot_re.finditer(r"指标[:：]\s*(.+)", plan_text):
                    plan_inds.update(i.strip() for i in m.group(1).split(","))

                # Step 2: 笛卡尔积判权
                allowed_inds_set = set(allowed_inds)
                allowed_orgs_set = set(f"江苏省{o}农商行" for o in allowed_orgs) if allowed_orgs else plan_orgs

                if plan_inds:
                    all_forbidden = True
                    for ind in plan_inds:
                        if ind in allowed_inds_set:
                            all_forbidden = False
                            break
                    if all_forbidden and plan_inds:
                        logger.warning(f"[两步CoT] 全部越权，拦截")
                        writer({"result": [], "perm_rejected": True,
                                "hint": "您无权查询相关指标,已拦截。"})
                        return {"sql": "", "perm_rejected": True}

                    # 差集：哪些指标没匹配到任何已知来源
                    unknown_inds = plan_inds - formula_indicators - ALL_INDS
                    if unknown_inds:
                        writer({"compute_note": f"指标 {', '.join(unknown_inds)} 未在直接指标库和计算公式库中匹配，系统将基于语义推断计算方式"})
                        logger.info(f"[两步CoT] 未匹配指标: {unknown_inds}")

                    # 合并公式表的组件指标
                    plan_inds = (plan_inds | formula_indicators) & allowed_inds_set if allowed_inds else (plan_inds | formula_indicators)
                    logger.info(f"[两步CoT] 判权后合法指标(含公式): {plan_inds}")

                # Step 2.5: schema 收敛——把越权指标从注入 LLM 的 metric_infos 中物理删除
                # LLM 看不到越权指标的描述/列信息就无法凭空生成,从源头防越权(第一层)
                if allowed_inds:
                    allowed_set = set(allowed_inds)
                    _before = len(metric_infos)
                    metric_infos = [m for m in metric_infos if m["name"] in allowed_set]
                    logger.info(f"[权限] metric_infos 收敛: {_before} -> {len(metric_infos)} 可见={[m['name'] for m in metric_infos]}")

                # Step 3: 带合法指标生成SQL + 权限硬约束声明
                perm_note = ""
                if plan_inds:
                    perm_note = (
                        "\n\n【权限硬约束】当前用户仅可查询以下指标, "
                        "禁止使用其余任何指标,违反将被直接拦截: "
                        f"{', '.join(sorted(plan_inds))}。"
                    )

            # 生成最终SQL
            tml = await loader_prompt("generate_sql")
            tml += "\n\n【重要】如果问题涉及多表JOIN、子查询嵌套、窗口函数或聚合计算，请先用一两句话简述执行步骤，再输出SQL。简单查询直接输出SQL。"
            chain = PromptTemplate.from_template(tml) | llm | StrOutputParser()
            sql = await chain.ainvoke({
                "query": query,
                "table_infos": tables_text,
                "metric_infos": yaml.dump(metric_infos, allow_unicode=True, sort_keys=False),
                "date_info": yaml.dump(date_info, allow_unicode=True, sort_keys=False),
                "db_info": yaml.dump(db_info, allow_unicode=True, sort_keys=False),
                "chat_context": system_prompt + perm_note,
                "user_perms": "",
            })
            sql = sql.strip()
            # 去掉 markdown 代码块标记
            import re as _re2
            sql = _re2.sub(r'```(?:sql)?\s*', '', sql)
            sql = _re2.sub(r'\s*```', '', sql)
            # CoT可能输出推理过程，提取最后的SQL语句
            if "SELECT" in sql.upper() or "WITH" in sql.upper():
                m = _re2.search(r"(?:SELECT|WITH)\s+.+", sql, _re2.DOTALL | _re2.IGNORECASE)
                if m:
                    sql = m.group(0).strip()

        # 修复简写机构名: 'A市农商行' → '江苏省A市农商行'
        import re as _re_org
        for abbr in ["A市","B市","C市","D市","E市","F市","G市","H市","I市","J市","K市","L市","M市"]:
            full = f"江苏省{abbr}"
            sql = _re_org.sub(rf"'{abbr}农商行'", f"'{full}农商行'", sql)

        # 缺关键信息检测:对比「问题提及的实体」vs「SQL 的 WHERE 条件」
        # 仅对 index_data 事实表查询生效(查 index_list/org_info 等维表不检测)
        # 三类必要信息:日期 / 机构 / 指标,缺哪报哪,不再一刀切只报日期
        import re as _re_chk
        _involves_index_data = bool(_re_chk.search(r"\bindex_data\b", sql, _re_chk.IGNORECASE))
        if _involves_index_data:
            # SQL 现有过滤条件(DESC/ASC 也算,覆盖"取最新一期"的 ORDER BY data_date DESC)
            _sql_has_date = bool(_re_chk.search(
                r"data_date\s*(?:=|IN|BETWEEN|>|<|>=|<=|DESC|ASC)", sql, _re_chk.IGNORECASE))
            _sql_has_org = bool(_re_chk.search(r"org_name\s*=", sql, _re_chk.IGNORECASE))
            _sql_has_ind = bool(_re_chk.search(r"index_name\s*=", sql, _re_chk.IGNORECASE))

            # 问题是否提及特定机构(A市农商行 / 江苏省X市农商行);"各机构/所有机构"不算
            _q_orgs = _re_chk.findall(r"(?:江苏省)?[A-M]市农商行", query)
            # 问题是否提及指标(直接指标 + 计算指标关键词,子串匹配覆盖口语简称)
            _IND_KW = ["存款","贷款","不良","拨备","资本充足","逾期","净利润","营业收入",
                       "营业支出","中间业务","净利息","成本收入","员工人数","网点","客户数",
                       "存贷比","人均","点均","户均","利润率","占比","监管边际","安全边际"]
            _q_has_ind = any(k in query for k in _IND_KW)

            _missing = []
            if not _sql_has_date:
                _missing.append("日期(如:2025年6月15日)")
            if _q_orgs and not _sql_has_org:
                _missing.append("机构")
            if _q_has_ind and not _sql_has_ind:
                _missing.append("指标")

            if _missing:
                _hint = f"查询缺少关键信息:{'、'.join(_missing)},请补充后再试。"
                logger.warning(f"缺关键信息: {_missing}, 跳过执行")
                writer({"result": [], "missing_info": True, "hint": _hint})
                return {"sql": "", "missing_info": True}

        logger.info(f"生成的sql语句\n：{sql}")
        return {"sql": sql}
    except Exception as e:
        logger.error(f"生成sql异常：{str(e)}")
        raise
