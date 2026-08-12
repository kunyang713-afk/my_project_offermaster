"""在线检索层：千问 Embedding + LangChain Milvus VectorStore。

职责（在线检索，供面试运行时使用）：
- QianwenEmbeddings：千问 text-embedding-v3 的 LangChain Embeddings 实现。
  DashScope compatible-mode 不兼容 langchain_openai 的请求格式（实测报 input.contents 错误），
  故用 openai SDK 直连封装成标准 Embeddings 接口，与 langchain_milvus 无缝配合。
- search：query → embedding → Milvus top-K → 返回片段列表。

离线构建（建库 / 批量写入）在 kb/build_kb.py，本模块不负责。
"""

from typing import List

from langchain_core.embeddings import Embeddings
from langchain_milvus import Milvus

from .config import settings


# ---------------------------------------------------------------- embedding

class QianwenEmbeddings(Embeddings):
    """千问 text-embedding-v3 的 Embeddings 实现（openai SDK 直连 DashScope）。"""

    # DashScope compatible-mode 单次 embedding 请求的文本数上限
    BATCH = 10

    def __init__(self) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            api_key=settings.qianwen_api_key,
            base_url=settings.qianwen_base_url,
        )
        self._model = settings.qianwen_embedding_model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), self.BATCH):
            resp = self._client.embeddings.create(
                model=self._model, input=texts[i:i + self.BATCH]
            )
            out.extend(d.embedding for d in resp.data)
        return out

    def embed_query(self, text: str) -> List[float]:
        resp = self._client.embeddings.create(model=self._model, input=[text])
        return resp.data[0].embedding


_embeddings: QianwenEmbeddings | None = None
_store: Milvus | None = None


def _get_embeddings() -> QianwenEmbeddings:
    global _embeddings
    if _embeddings is None:
        _embeddings = QianwenEmbeddings()
    return _embeddings


def _get_store() -> Milvus:
    global _store
    if _store is None:
        _store = Milvus(
            embedding_function=_get_embeddings(),
            collection_name=settings.milvus_collection,
            connection_args={"uri": settings.milvus_uri},
        )
    return _store


# ---------------------------------------------------------------- milvus search

def search(query: str, top_k: int = 3) -> List[dict]:
    """向量检索：query → embedding → Milvus top-K → 返回片段。

    返回片段结构：{answer, question, source, topic, qid, score}。
    """
    hits = _get_store().similarity_search_with_score(query, k=top_k)
    return [
        {
            "answer": doc.metadata.get("answer", ""),
            "question": doc.metadata.get("question", ""),
            "source": doc.metadata.get("source", ""),
            "topic": doc.metadata.get("topic", ""),
            "qid": doc.metadata.get("qid", ""),
            "score": round(score, 4),
        }
        for doc, score in hits
    ]
