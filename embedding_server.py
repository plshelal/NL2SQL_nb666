import os
import asyncio
import threading
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

app = FastAPI()

# 模型路径:优先环境变量(容器挂载用),缺省回落本地开发路径
model_path = os.getenv(
    "MODEL_PATH",
    r"C:\Users\10704\.cache\huggingface\hub\models--BAAI--bge-large-zh-v1.5\snapshots\79e7739b6ab944e86d6171e44d24c997fc1e0116",
)

print("开始加载本地模型...")
model = SentenceTransformer(model_path)
print("模型加载完成，服务准备就绪！")

infer_lock = threading.Lock()

class EmbedRequest(BaseModel):
    inputs: list[str]

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/embed")
async def embedding_api(request: EmbedRequest):
    def encode_task():
        with infer_lock:
            return model.encode(request.inputs)
    embeddings = await asyncio.to_thread(encode_task)
    result_list = []
    for idx, vec in enumerate(embeddings):
        result_list.append({
            "embeddings": vec.tolist(),
            "index": idx,
            "truncated": False
        })
    return result_list

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("__main__:app", host="0.0.0.0", port=8081, log_level="info")
