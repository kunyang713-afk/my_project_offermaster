"""离线构建知识库：RAG-database/*.jsonl → 分块 → 向量化 → 写入 Milvus。

基于 LangChain Milvus VectorStore（langchain_milvus）封装：
- 数据源为 jsonl：每行一个"问题-答案"对象（含 id/topic/source/category/question/answer/tags）。
  天然是一问一答的检索单元，无需像 md 那样解析 front-matter 和按标题切块。
- 建库 / 建索引 / 写入 / 落盘由 Milvus VectorStore 内部完成，不再手写 Milvus client 胶水代码。
- Embedding 使用 app.retrieval.QianwenEmbeddings（千问 text-embedding-v3）。

用法（需在能连到 Milvus 的环境运行）：
    docker compose up -d
    docker exec offermaster-python python -m kb.build_kb
    # 强制重建（清空旧 collection）：
    docker exec offermaster-python python -m kb.build_kb --recreate
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.documents import Document
from langchain_milvus import Milvus

from app.config import settings  # noqa: E402
from app.retrieval import QianwenEmbeddings  # noqa: E402


EMBED_BATCH = 32  # 每批向量化并写入的块数（避免单次 embedding 请求过大）


def _clean_answer(text: str) -> str:
    """清洗答案文本：去掉从 md/word 提取时残留的 '答：' / '：' 前缀。"""
    return text.strip().lstrip("：:").strip()


def load_jsonl(path: Path) -> list[dict]:
    """加载单个 jsonl：每行一个"问题-答案"对象。"""
    chunks = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            text = _clean_answer(item.get("answer", ""))
            if not text:
                continue
            chunks.append(
                {
                    "question": item.get("question", "").strip(),
                    "answer": text,
                    "topic": item.get("topic", path.stem),
                    "source": item.get("source", path.name),
                    "category": item.get("category", ""),
                    "qid": item.get("id", ""),
                }
            )
    return chunks


def load_all_docs() -> list[dict]:
    """加载 RAG-database 下所有 jsonl 文档。"""
    base = Path(settings.kb_docs_dir)
    if not base.is_dir():
        raise SystemExit(f"知识库目录不存在: {base.resolve()}")
    docs = []
    for path in sorted(base.glob("*.jsonl")):
        chunks = load_jsonl(path)
        print(f"  {path.name}: {len(chunks)} 条")
        docs.extend(chunks)
    return docs


def split_long_chunks(docs: list[dict], max_chars: int = 1500) -> list[dict]:
    """超长块二次切分，metadata 随块继承。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chars, chunk_overlap=100, separators=["\n\n", "\n", "。", "；", " "]
    )
    out = []
    for d in docs:
        if len(d["answer"]) <= max_chars:
            out.append(d)
            continue
        pieces = splitter.split_text(d["answer"])
        for i, piece in enumerate(pieces):
            out.append(
                {
                    **d,
                    "answer": piece,
                    "question": d["question"] if i == 0 else f"{d['question']}（续{i}）",
                }
            )
    return out


def _collection_exists() -> bool:
    """Milvus 中是否已存在目标 collection。"""
    from pymilvus import MilvusClient

    return MilvusClient(uri=settings.milvus_uri).has_collection(settings.milvus_collection)


def build(force_recreate: bool) -> None:
    """解析文档 → 切分 → 建库并写入 Milvus。"""
    print("加载文档...")
    docs = load_all_docs()
    print(f"共 {len(docs)} 条，开始二次切分...")
    docs = split_long_chunks(docs)
    print(f"切分后 {len(docs)} 块")
    if not docs:
        raise SystemExit("没有可写入的知识块")

    documents = [
        Document(
            # 题目+答案拼接后作为向量主体，让相似度同时覆盖题目与答案；
            # metadata["answer"] 保留纯答案，供检索层直接返回。
            page_content=f"题目：{d['question']}\n答案：{d['answer']}",
            metadata={
                "question": d["question"],
                "answer": d["answer"],
                "source": d["source"],
                "topic": d["topic"],
                "category": d.get("category", ""),
                "qid": d["qid"],
            },
        )
        for d in docs
    ]

    if _collection_exists() and not force_recreate:
        raise SystemExit(
            f"collection {settings.milvus_collection} 已存在；如需重建请加 --recreate"
        )

    print(f"创建/复用 collection（COSINE / AUTOINDEX），批量写入...")
    store = Milvus(
        embedding_function=QianwenEmbeddings(),
        collection_name=settings.milvus_collection,
        connection_args={"uri": settings.milvus_uri},
        drop_old=force_recreate,
        auto_id=True,
        index_params={"metric_type": "COSINE", "index_type": "AUTOINDEX", "params": {}},
    )
    for i in range(0, len(documents), EMBED_BATCH):
        batch = documents[i:i + EMBED_BATCH]
        store.add_texts(
            texts=[d.page_content for d in batch],
            metadatas=[d.metadata for d in batch],
        )
        print(f"  已写入 {min(i + EMBED_BATCH, len(documents))}/{len(documents)}")
    print(f"完成：共写入 {len(documents)} 条到 {settings.milvus_collection}")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建面试知识库")
    parser.add_argument("--recreate", action="store_true", help="清空并重建 collection")
    args = parser.parse_args()
    build(force_recreate=args.recreate)


if __name__ == "__main__":
    main()
