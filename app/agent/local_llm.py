"""本地/远程微调模型 · 仅替代 generate_sql 节点"""
import httpx
from app.core.log import logger


class RemoteSQLModel:
    """远程 LoRA 推理服务"""

    def __init__(self, url: str = "http://192.168.1.100:8100/generate"):
        self.url = url

    async def ainvoke(self, prompt: str, max_tokens: int = 1024) -> str:
        try:
            async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                resp = await client.post(
                    self.url,
                    json={"prompt": prompt, "max_tokens": max_tokens},
                    headers={"Connection": "close"}
                )
                if resp.status_code == 200:
                    return resp.json()["text"]
                logger.warning(f"远程模型返回 {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"远程模型调用失败 ({self.url}): {e}")
        raise RuntimeError("Remote model unavailable")


# 全局实例
local_sql_model = None
use_local_model = False  # 是否启用本地模型


def init_remote_model(url: str, enabled: bool = False):
    global local_sql_model, use_local_model
    local_sql_model = RemoteSQLModel(url)
    use_local_model = enabled
