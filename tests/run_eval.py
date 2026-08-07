"""验证集+测试集评估：计时、LLM初判、按难度统计耗时"""
import asyncio, csv, json, sys, time
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.clients.embedding_client_manager import embedding_client_manager
from app.clients.es_client_manager import es_client_manager
from app.clients.mysql_client_manager import meta_mysql_client_manager, dw_mysql_client_manager
from app.clients.qdrant_client_manager import qdrant_client_manager
from app.repositories.es.values_es_repository import ValueEsRepository
from app.repositories.mysql.dw_mysql_repository import DwMysqlRepository
from app.repositories.mysql.meta_mysql_repository import MetaMysqlRepository
from app.repositories.qdrant.column_qdrant_respository import ColumnQdrantRepository
from app.repositories.qdrant.metric_qdrant_repository import MetricQdrantRepository
from app.agent.graph import graph
from app.agent.state import DataAgentState
from app.agent.context import DataAgentContext

CSV_PATH = Path(r"C:\Users\10704\Desktop\csv\qa.csv")
OUT_DIR = ROOT / "tests" / "eval_results"


async def run_one(query):
    state = {"query": query}
    async with meta_mysql_client_manager.session_factory() as ms, dw_mysql_client_manager.session_factory() as ds:
        ctx = {
            "embeddings": embedding_client_manager.embeddings,
            "column_qdrant_repository": ColumnQdrantRepository(qdrant_client_manager.client),
            "metric_qdrant_repository": MetricQdrantRepository(qdrant_client_manager.client),
            "value_es_repository": ValueEsRepository(es_client_manager.client),
            "meta_mysql_repository": MetaMysqlRepository(ms),
            "dw_mysql_repository": DwMysqlRepository(ds),
        }
        result, sql = None, None
        async for ch in graph.astream(input=state, context=ctx, stream_mode="custom"):
            if isinstance(ch, dict):
                if "result" in ch: result = ch["result"]
                if "sql" in ch: sql = ch["sql"]
        return sql, result


async def judge(expected, actual):
    from app.agent.llm import llm
    from langchain_core.output_parsers import StrOutputParser
    prompt = (
        f"判断以下金融查询结果是否与期望答案语义一致（数值近似、含义相同即可）。\n"
        f"期望: {expected}\n"
        f"实际: {actual}\n"
        f"只回答 PASS 或 FAIL。"
    )
    resp = await (llm | StrOutputParser()).ainvoke(prompt)
    return resp.strip().upper() == "PASS"


async def main():
    meta_mysql_client_manager.init(); dw_mysql_client_manager.init()
    qdrant_client_manager.init(); embedding_client_manager.init(); es_client_manager.init()

    with open(CSV_PATH, "r", encoding="gbk") as f:
        rows = [r for r in csv.DictReader(f) if r["问题编号"].strip().startswith(("VAL-", "TST-"))]
    print(f"共 {len(rows)} 条 (验证集+测试集)\n")

    passed, failed = [], []
    times = defaultdict(list)

    for i, q in enumerate(rows):
        qid = q["问题编号"].strip()
        query = q["问题描述"].strip()
        expected = q.get("问题结果", "").strip()
        level = qid.split("-")[1]

        t0 = time.time()
        sql, result = await run_one(query)
        elapsed = time.time() - t0
        times[level].append(elapsed)

        actual = str(result[:3] if result else "")[:200]
        ok = await judge(expected, actual)
        item = {"id": qid, "query": query, "expected": expected, "actual": actual, "sql": sql, "time_s": round(elapsed, 1)}
        status = "PASS" if ok else "FAIL"
        print(f"[{i+1}/{len(rows)}] {qid} [{level}] {elapsed:.1f}s {status}")

        if ok: passed.append(item)
        else: failed.append(item)

    for name, data in [("passed", passed), ("failed", failed)]:
        with open(OUT_DIR / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for item in data:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"\n===== 耗时统计 =====")
    print(f"{'难度':<6} {'数量':<6} {'平均耗时':<10} {'最长':<10} {'最短':<10}")
    for level in ["S", "M", "H"]:
        tlist = times[level]
        if tlist:
            print(f"{level:<6} {len(tlist):<6} {sum(tlist)/len(tlist):<8.1f}s  {max(tlist):<8.1f}s  {min(tlist):<8.1f}s")
    all_t = [t for tl in times.values() for t in tl]
    print(f"{'总计':<6} {len(all_t):<6} {sum(all_t)/len(all_t):<8.1f}s")
    print(f"\nPASS={len(passed)} FAIL={len(failed)}")
    print(f"结果保存在: {OUT_DIR}")

    await qdrant_client_manager.close(); await es_client_manager.close()
    await meta_mysql_client_manager.close(); await dw_mysql_client_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
