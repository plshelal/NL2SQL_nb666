from langchain_openai import ChatOpenAI

from app.conf.app_config import app_config

# 推理 LLM(SQL生成/纠错/Agent决策):开启 thinking,质量优先(~8s/次)
llm = ChatOpenAI(
    model=app_config.llm.model_name,
    api_key=app_config.llm.api_key,
    base_url="https://api.deepseek.com/v1",
    temperature=0,
    extra_body={"thinking": {"type": "enabled"}},
)

# 快速 LLM(SQL生成默认):关闭 thinking,速度优先(~1s/次)
llm_fast = ChatOpenAI(
    model=app_config.llm.model_name,
    api_key=app_config.llm.api_key,
    base_url="https://api.deepseek.com/v1",
    temperature=0,
    extra_body={"thinking": {"type": "disabled"}},
)


if __name__ == '__main__':
    for chunk in llm.stream("who are you ?"):
        print(chunk.text, end="", flush=True)
