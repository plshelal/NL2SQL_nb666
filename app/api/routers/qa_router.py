"""PPT协作问答接口 —— 同事问项目技术细节，DeepSeek答"""
from fastapi import APIRouter
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from app.agent.llm import llm
import json

qa_router = APIRouter()

SYSTEM_PROMPT = """你是金融问数系统的技术助手。系统架构如下：

【项目概述】
金融问数是一个面向零售银行的 NL2SQL 智能问数系统。用户用自然语言提问，系统自动生成 SQL 并返回图表和报告。

【核心流程】
jieba分词 → Schema Link语义分流(LLM) → 关键词扩展(LLM) → TEI向量化 → 三路并行召回(Qdrant向量+ES全文) → 合并去重 → LLM过滤 → SQL生成(DeepSeek/Qwen微调双模式) → EXPLAIN校验 → 执行 → SSE推送 → 异步图表+报告

【关键机制】
- Schema Link: 一次LLM调用判断问题类型(指标组/计算指标/普通)，锁定指标直注关键词
- 计算指标公式表: 30个衍生指标的SQL模板硬匹配，不靠LLM猜
- 动态Few-shot RAG: 120条训练数据向量化，语义检索最相似3条作示例
- 思维链CoT: DeepSeek路径复杂查询先推理再写SQL
- 权限管理: SQL执行后扫描index_name/org_name与白名单比对，与操作
- 空结果统一提示: 缺日期自动注1900-01-01返回空，触发"检查是否遗漏日期/机构/指标"
- 缺失信息兜底: SQL无日期过滤→代码注入假日期→自然空返回→提示用户
- 多轮对话: 对话历史以自然格式注入系统提示，LLM自行理解延续
- 双模型: SQL_MODEL=local切换Qwen微调(4080远程推理)/DeepSeek

【技术栈】
FastAPI + LangGraph + DeepSeek + Qdrant + Elasticsearch + MySQL + Chart.js + Qwen2.5-7B QLoRA

【数据】
3张核心表: org_info(机构), index_data(指标值,13万行), index_list(指标定义,21个)
13家江苏省农商行, 时间跨度约2年, 日频数据

请用中文回答，简明专业。"""


class QARequest(BaseModel):
    question: str
    history: list[dict] = []


@qa_router.post("/api/qa")
async def qa(req: QARequest):
    chain = PromptTemplate.from_template("{q}") | llm | StrOutputParser()

    # 构建对话历史
    history_text = ""
    if req.history:
        lines = ["【对话历史】"]
        for turn in req.history[-10:]:  # 最近10轮
            lines.append(f"用户: {turn.get('q','')}")
            lines.append(f"助手: {turn.get('a','')[:300]}")
        history_text = "\n".join(lines) + "\n\n"

    async def gen():
        full_prompt = f"{SYSTEM_PROMPT}\n\n{history_text}用户: {req.question}\n助手:"
        resp = await chain.ainvoke({"q": full_prompt})
        yield f"data:{json.dumps({'answer': resp.strip()}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
