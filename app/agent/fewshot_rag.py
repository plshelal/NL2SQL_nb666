"""Few-shot RAG：预计算训练数据向量，查询时快速检索"""
import json, os, pickle
import numpy as np
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent.parent / "conf" / "finetune_data.json"
CACHE_PATH = Path(__file__).parent.parent.parent / "conf" / "fewshot_embeddings.pkl"

# TEI 嵌入服务地址(从 .env 读,默认 127.0.0.1:8081)
_TEI_URL = f"http://{os.getenv('TEI_HOST', '127.0.0.1')}:{os.getenv('TEI_PORT', '8081')}/embed"

_questions = []
_embeddings = None


def _load_data(force: bool = False):
    """加载训练数据与向量缓存。幂等:已加载则直接返回,避免每查询重读文件/重反序列化。

    文件不存在时自动创建空文件(首次部署兜底,不崩)。
    """
    global _questions, _embeddings

    if _questions and not force:
        return

    # 文件不存在 → 创建空文件(兜底,防首次部署 FileNotFoundError)
    if not DATA_PATH.exists():
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text("", encoding="utf-8")

    with open(DATA_PATH, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    for l in lines:
        try:
            d = json.loads(l)
            q = d.get("input", "")
            if "用户问题:" in q:
                q = q.split("用户问题:")[-1].strip()
            _questions.append({"question": q, "sql": d.get("output", "")})
        except (json.JSONDecodeError, KeyError):
            continue

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

    # 空文件/无数据 → 跳过(不崩,不调 TEI,retrieve_examples 返回空)
    if not _questions:
        print("[Few-shot RAG] 无训练数据(finetune_data.json 为空),跳过预计算")
        _embeddings = None
        return

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
            resp = await client.post(_TEI_URL, json={"inputs": batch})
            embs = [item["embeddings"] for item in resp.json()]
            all_embs.extend(embs)

    _embeddings = np.array(all_embs)
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(_embeddings, f)
    print(f"[Few-shot RAG] 预计算完成，缓存已保存 ({len(_embeddings)} 条)")


async def retrieve_examples(query: str, top_k: int = 3) -> list[dict]:
    """检索与当前问题最相似的训练示例"""
    _load_data()

    # 无数据或无向量 → 返回空(不崩)
    if not _questions or _embeddings is None or len(_embeddings) == 0:
        return []

    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(_TEI_URL, json={"inputs": [query]})
        query_emb = np.array(resp.json()[0]["embeddings"])

    # 维度不匹配 → 返回空(兜底)
    if _embeddings.shape[-1] != query_emb.shape[-1]:
        return []

    # 余弦相似度
    sims = np.dot(_embeddings, query_emb) / (np.linalg.norm(_embeddings, axis=1) * np.linalg.norm(query_emb))
    top_idx = np.argsort(sims)[-top_k:][::-1]

    examples = []
    for idx in top_idx:
        if sims[idx] > 0.5:
            examples.append(_questions[idx])
    return examples


async def add_example(question: str, sql: str) -> dict:
    """追加一条 few-shot 示例到检索池(审核通过的 fewshot 规则回灌入口)。

    幂等:question 已在 _questions 中则跳过。
    文件落盘必成(JSONL 追加);向量增量更新尽力——embedding 服务不可用则降级,
    下次启动 precompute_embeddings 会把新条目一并补算,不丢数据。
    """
    global _embeddings
    _load_data()
    question = (question or "").strip()
    sql = (sql or "").strip()
    if not question or not sql:
        return {"ok": False, "reason": "empty"}

    # 查重(跨重启也幂等:_questions 从文件重载,含历史追加项)
    if any(q["question"] == question for q in _questions):
        return {"ok": False, "reason": "duplicate"}

    # 1. 追加到 JSONL 文件(retrieve 只用 "用户问题:" 后的部分,最小 input 即可)
    import json as _j
    rec = {"instruction": "根据用户问题和数据库结构生成MySQL SQL。只输出SQL,不解释。",
           "input": f"用户问题: {question}", "output": sql}
    with open(DATA_PATH, "a", encoding="utf-8") as f:
        f.write(_j.dumps(rec, ensure_ascii=False) + "\n")

    # 2. 内存索引追加
    _questions.append({"question": question, "sql": sql})

    # 3. 增量向量(尽力)
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(_TEI_URL, json={"inputs": [question]})
            vec = np.array(resp.json()[0]["embeddings"])
        if _embeddings is not None:
            _embeddings = np.vstack([_embeddings, vec.reshape(1, -1)])
        else:
            _embeddings = vec.reshape(1, -1)
        with open(CACHE_PATH, "wb") as f:
            pickle.dump(_embeddings, f)
    except Exception as e:
        print(f"[Few-shot RAG] 增量向量失败(文件已落盘,下次启动补算): {e}")
        return {"ok": True, "reason": "file_only", "note": "向量待启动补算"}
    return {"ok": True}
