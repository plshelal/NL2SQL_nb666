import asyncio

import jieba.analyse
from langgraph.runtime import Runtime

from app.agent.context import DataAgentContext
from app.agent.state import DataAgentState
from app.core.log import logger


async def extract_keywords(state: DataAgentState, runtime: Runtime[DataAgentContext]):
    # 第一种方式：流输出器
    # writer=get_stream_writer()
    # 第二种方式：runtime
    writer = runtime.stream_writer
    # 输出信息
    writer({"stage": "提取关键字"})
    try:
        # 获取问题
        query = state["query"]

        # 定义词性
        allowPOS = (
            "n",  # 名词: 数据、服务器、表格
            "nr",  # 人名: 张三、李四
            "ns",  # 地名: 北京、上海
            "nt",  # 机构团体名: 政府、学校、某公司
            "nz",  # 其他专有名词: Unicode、哈希算法、诺贝尔奖
            "v",  # 动词: 运行、开发
            "vn",  # 名动词: 工作、研究
            "a",  # 形容词: 美丽、快速
            "an",  # 名形词: 难度、合法性、复杂度
            "eng",  # 英文
            "i",  # 成语
            "l",  # 常用固定短语
        )

        # 提取关键字
        keywords=jieba.analyse.extract_tags(query, allowPOS=allowPOS)

        # 分词结果去重即可,不把整句塞进关键词——
        # 整句 embedding 开销大(长文本)且无语义增益(分词已覆盖实体),还污染召回
        keywords = list(set(keywords))

        logger.info(f"提取关键成功{keywords}")

        return {"keywords":keywords}
    except Exception as e:
        logger.error(f"提取关键字异常{str(e)}")

        raise
