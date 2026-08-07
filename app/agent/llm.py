from langchain_openai import ChatOpenAI

from app.conf.app_config import app_config

llm = ChatOpenAI(
    model=app_config.llm.model_name,
    api_key=app_config.llm.api_key,
    base_url="https://api.deepseek.com/v1",
    temperature=0,
    extra_body={"thinking": {"type": "disabled"}},
)


if __name__ == '__main__':
    for chunk in llm.stream("who are you ?"):
        print(chunk.text, end="", flush=True)
