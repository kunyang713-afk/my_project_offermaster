# OfferMaster AI 面试官

双栈架构：Java（Spring Boot，业务底座）+ Python（FastAPI + LangChain + LangGraph，AI 大脑）。

## 已实现能力（M1-M6）
- Java：JWT 认证、简历脱敏、会话/消息/报告同步落库、报告 IDOR 校验、Java→Python 探活。
- Python：LangGraph 全节点 + PostgresSaver（断线可恢复）+ `/start` `/answer` 轮询协议。
- Python：Milvus standalone 知识库 + 千问 Embedding（text-embedding-v3）RAG 检索（LangChain Milvus VectorStore 封装），出题/评估有知识库依据。
- Python：M6 历史摘要压缩——上下文空间溢出（messages 总字符超 `history_max_chars` 预算，默认 8000）时才触发：把旧主题压缩进 `conversation_summary` 并裁剪 `messages`（保留最近主题原文），终局报告以「摘要 + 最近一题 + 评估」生成（提示词自适应：未压缩时按完整对话、压缩后按摘要+最近主题），防上下文膨胀；未溢出不压缩。
- 面试流：出主问题（数量 = plan_focus 知识点数）→ 评估 → （追问/换题）→ 按知识点数收尾 → 诊断报告（无数值评分）。
- 前端：`http://localhost:8080/` 静态页（注册/登录 → 贴简历 → 聊天 → 报告）。
- 需配置真实 API Key（`DEEPSEEK_API_KEY` / `QIANWEN_API_KEY`）与 Milvus，不再提供 mock 兜底。

## 目录
```
backend-java/   Spring Boot 3.4 (JDK17)
ai-brain/       FastAPI + LangGraph
frontend/       静态前端（构建时复制进 Java static）
RAG-database/   知识库源数据（Java基础面试题.jsonl）
docker-compose.yml
```

### Python 侧分层（ai-brain/app/）
```
config.py        # 配置（环境变量 / .env）
schemas.py       # Pydantic 结构化模型
state.py         # InterviewState（状态 schema，含追加式 reducer）
llm.py           # LLM 调用（DeepSeek，真实路径）
nodes.py         # 节点函数（局部更新 + Command(goto) 路由）
checkpointer.py  # PostgresSaver
retrieval.py     # 在线检索：QianwenEmbeddings + LangChain Milvus VectorStore
graph.py         # 图构建 + start/answer 会话入口（无条件边）
api.py           # FastAPI 端点
draw_mermaid.py  # 打印编译图 Mermaid（辅助）
kb/build_kb.py   # 离线构建知识库脚本（VectorStore 建库/写入）
```

## 运行
前置：Docker Desktop 已启动；Java 用本机 `JAVA_HOME`（JDK17）。

```powershell
# 1) 启动基础设施 + AI 大脑
docker compose up -d --build

# 1.5) 构建知识库（首次执行；文档更新后加 --recreate 重建）
docker exec offermaster-python python -m kb.build_kb

# 2) 构建并运行 Java 后端（监听 8080）
cd backend-java
$env:JAVA_HOME='D:\Program Files\JDK.17'
$env:Path="$env:JAVA_HOME\bin;$env:Path"
mvn -DskipTests package
java -jar target/backend-java-0.1.0.jar
```

访问 `http://localhost:8080/`。

## 关键接口
- `POST /api/auth/register`、`POST /api/auth/login` → JWT
- `POST /api/interviews` → 开始面试（返回首个问题）
- `POST /api/interviews/{id}/answer` `{turnId, answer}` → 推进一轮
- `GET  /api/interviews/{id}/report` → 诊断报告（归属校验）
- `GET  /api/diag/python-health` → Java 探活 Python

## 环境变量（Python，docker-compose 传入）
- `DEEPSEEK_API_KEY`：DeepSeek API Key（必填，缺失将无法调用 LLM）。
- `DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`：默认 `https://api.deepseek.com` / `deepseek-chat`。
- `QIANWEN_API_KEY`：千问 Embedding Key（DashScope，必填，用于 RAG 向量化与检索）。
- `QIANWEN_BASE_URL`、`QIANWEN_EMBEDDING_MODEL`：默认 DashScope compatible-mode / `text-embedding-v3`。
- `MILVUS_URI`：Milvus 地址，compose 内为 `http://milvus:19530`。

## 验收记录
- 完整面试流程（start→多轮 answer→final_report）✓
- Python 重启后会话续跑（PostgresSaver）✓
- Milvus 知识库构建（67 块）与真实向量检索 ✓
- RAG 评估 coverage 非 null ✓
- 报告 JSON 结构正确、IDOR 拦截 ✓
- Java→Python `/health` 探活 ✓
- M6 历史摘要压缩：messages 超预算触发压缩（旧主题入 summary、保留最近主题原文），未超预算不压缩，终局报告正常 ✓



