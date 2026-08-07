"""Few-shot RAG：预计算训练数据向量，查询时快速检索"""
import json, pickle
import numpy as np
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent.parent / "conf" / "finetune_data.json"
CACHE_PATH = Path(__file__).parent.parent.parent / "conf" / "fewshot_embeddings.pkl"

_questions = []
_embeddings = None


def _load_data(force: bool = False):
    """加载训练数据与向量缓存。幂等:已加载则直接返回,避免每查询重读文件/重反序列化。"""
    global _questions, _embeddings

    if _questions and not force:
        return

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = [json.loads(l) for l in f]

    for d in data:
        q = d["input"]
        if "用户问题:" in q:
            q = q.split("用户问题:")[-1].strip()
        _questions.append({"question": q, "sql": d["output"]})

    # 从缓存加载预计算的向量
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "rb") as f:
            _embeddings = pickle.load(f)
    else:
        _embeddings = None


async def precompute_embeddings():
    """启动时调用，预计算所有训练问题的向量并缓存"""
    global _embeddings
    _load_data()

    if _embeddings is not None and len(_embeddings) == len(_questions):
        print(f"[Few-shot RAG] 向量缓存已存在，跳过预计算")
        return

    import httpx
    print(f"[Few-shot RAG] 预计算 {len(_questions)} 条训练样本的向量...")
    texts = [q["question"] for q in _questions]

    # 分批发送，每批20条
    all_embs = []
    batch_size = 20
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i+batch_size]
            resp = await client.post("http://127.0.0.1:8081/embed", json={"inputs": batch})
            embs = [item["embeddings"] for item in resp.json()]
            all_embs.extend(embs)

    _embeddings = np.array(all_embs)
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(_embeddings, f)
    print(f"[Few-shot RAG] 预计算完成，缓存已保存 ({len(_embeddings)} 条)")


async def retrieve_examples(query: str, top_k: int = 3) -> list[dict]:
    """检索与当前问题最相似的训练示例"""
    _load_data()

    if _embeddings is None:
        return []

    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post("http://127.0.0.1:8081/embed", json={"inputs": [query]})
        query_emb = np.array(resp.json()[0]["embeddings"])

    # 余弦相似度
    sims = np.dot(_embeddings, query_emb) / (np.linalg.norm(_embeddings, axis=1) * np.linalg.norm(query_emb))
    top_idx = np.argsort(sims)[-top_k:][::-1]

    examples = []
    for idx in top_idx:
        if sims[idx] > 0.5:
            examples.append(_questions[idx])
    return examples
