import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 知识库目录位于项目根目录（ai-brain 的上一级）。
# 基于本文件位置解析为绝对路径，避免运行时 cwd 不同导致找不到目录。
KB_DOCS_DIR = Path(__file__).resolve().parents[2] / "RAG-database"

class Settings(BaseSettings):
    database_url: str = "postgresql://offermaster:offermaster@localhost:5432/offermaster"
    redis_url: str = "redis://localhost:6379/0"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    qianwen_api_key: str = ""
    qianwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qianwen_embedding_model: str = "text-embedding-v3"

    milvus_uri: str = "http://localhost:19530"
    milvus_collection: str = "java_kb"

    kb_docs_dir: str = str(KB_DOCS_DIR)

    # M6 历史摘要压缩：messages 总字符数超过该预算时触发压缩（防上下文膨胀）。
    # 只按需触发（上下文空间溢出），未超出预算则不调用 LLM、不裁剪。
    history_max_chars: int = 8000

    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()

# LangSmith SDK 直接读取 os.environ；这里把 settings 同步进环境变量，
# 确保 uvicorn / 脚本 / Docker 任意启动方式下追踪都生效。
if settings.langsmith_api_key:
    os.environ["LANGSMITH_TRACING"] = "true" if settings.langsmith_tracing else "false"
    os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
