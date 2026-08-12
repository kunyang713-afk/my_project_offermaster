# 阶段二：RAG 知识库（ai-brain RAG 接入）编写思路

> 目标读者：想真正看懂「M5 怎么给 AI 大脑装上知识库」的人。
> 阅读配套：`docker-compose.yml`、`ai-brain/app/` 下的 retrieval/config/schemas/state/nodes/graph/llm、`ai-brain/kb/build_kb.py`、`RAG-database/Java基础面试题.jsonl`。推荐先读这份思路，再按文末顺序逐个打开文件对照。
> 版本说明：本文描述 **LangChain Milvus VectorStore 封装 + 移除 mock** 后的实现（原手写 Milvus client + mock 兜底版本已重写）。

---

## 0. 先想清楚：这个阶段到底要做什么

写代码前先回答三个问题：

1. **要补什么短板**：MVP 阶段出题、评估、追问全靠 LLM 现编，评估没有参考答案对照，`coverage` 一直是 `None`。M5 的目标就是给考官装知识库，让评估有据可依。
2. **边界在哪**：`coverage` 只反映回答对参考要点的覆盖程度，**不改变收尾逻辑**——主问题数仍由 `plan_focus` 返回的知识点数决定，`next_question` 原样不动。
3. **怎么才算完成**：知识库能离线构建、在线检索；`evaluate` 产出的 `coverage` 非 `None`，`missing_keys`/`wrong_points` 以检索片段为对照依据。**无 mock 兜底**：Milvus / API Key 不可用时直接报错，依赖必须配齐。

一句话：**不推翻阶段一的图结构，在 `generate_question` 节点内嵌一道检索**——按当轮知识点检索 top-3 问答对，写入 `state.reference_docs`，既作为出题蓝本、又作为 `evaluate` 的对照依据，一份检索两处复用，无需新增节点。

---

## 1. 整体思考顺序（自底向上）

延续阶段一的思路，先数据、后动作、再组装：

```
基础设施 → 配置 → 数据契约 → 状态 → 在线检索 → 离线构建 → 节点 → 图 → LLM 增强
```

每一层只依赖它前面写好的东西。当前实现涉及的文件，按这个顺序读：

```
01 docker-compose.yml         基础设施（Milvus 从哪来）
02 config.py                  配置（千问/Milvus 从哪来）
03 schemas.py                 数据契约（评估/报告模型）
04 state.py                   状态（reference_docs 检索参考）
05 app/retrieval.py           在线检索（QianwenEmbeddings + Milvus VectorStore）
06 kb/build_kb.py             离线构建（jsonl → VectorStore 建库写入）
07 nodes.py                   节点（generate_question 内嵌检索）
08 graph.py                   图（接线，无新节点）
09 llm.py                     LLM 增强（出题参考 + 评估对照）
```

`RAG-database/Java基础面试题.jsonl` 是输入数据，读 06 之前先扫一眼它的格式即可。

> 模块划分按「生命周期」：面试运行时的向量生成 + 检索收在 `app/retrieval.py`；知识库离线的建库 / 批量写入由 `kb/build_kb.py` 调用 Milvus VectorStore 完成。原 `kb/vectorstore.py`（手写 Milvus 建库/写入）已删除，职责并入 langchain_milvus 与 build_kb。

---

## 2. 逐个文件：它解决什么问题、怎么写

### 01 docker-compose.yml —— 基础设施从哪来
**问题**：Milvus 是独立服务，得有人起它。
**思路**：`etcd`（元数据）+ `minio`（对象存储）+ `milvus`（主服务，暴露 19530）三个服务；`python-app` 增加 `QIANWEN_*`、`MILVUS_URI` 环境变量，并挂载 `./RAG-database:/app/RAG-database:ro` 供构建脚本读取。
**关键点**：敏感 Key 只放 `.env`（已 gitignore），compose 用 `${VAR:-default}` 引用；`python-app` 的 healthcheck 用端口探测（镜像里未必有 curl）。

### 02 config.py —— 配置从哪来
**问题**：千问 Key、模型名、Milvus 地址这些新外部输入散在哪？
**思路**：继续用 `pydantic-settings`，给 `Settings` 加字段：
```python
qianwen_api_key: str = ""
qianwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
qianwen_embedding_model: str = "text-embedding-v3"
milvus_uri: str = "http://localhost:19530"
milvus_collection: str = "java_kb"
kb_docs_dir: str = str(KB_DOCS_DIR)   # KB_DOCS_DIR = Path(__file__).resolve().parents[2] / "RAG-database"
```
**关键点**：
- 无 mock 兜底，`qianwen_api_key` / `milvus_uri` 必须配好，否则在线检索直接抛错。
- `kb_docs_dir` 基于本文件位置解析为绝对路径（`parents[2]` 跳到仓库根），避免运行时 cwd 不同导致找不到 `RAG-database/` 目录。

### 03 schemas.py —— 数据长什么样（契约）
**思路**：沿用阶段一的模型。`AnswerAssessment.coverage: Optional[float] = None` 在 MVP 时恒为 `None`，RAG 接入后由 `evaluate` 填充为 0~1 的数值；`missing_keys` / `wrong_points` 继续是评估产物。`WrongQuestion` 保持原有字段。

### 04 state.py —— 状态改了什么
**问题**：检索结果在 State 里怎么放？
**思路**：新增 `reference_docs` 字段，**覆盖式**——每个主问题由 `generate_question` 节点按 `focus.skill` 检索 top-3 问答对填入，**出题和评估共用同一份**：
```python
reference_docs: List[dict]  # 当前主问题的知识库参考问答对，由 generate_question 填充
# 元素结构：{answer, question, source, topic, qid, score}
```
**关键点**：
- 与 `messages` / `assessments` 的追加式不同，`reference_docs` 每出新一题就覆盖上一题的，不累积，避免 State 无限膨胀。
- 字段名从早期设计的 `retrieved_context` 改为 `reference_docs`，更准确地表达「参考文档」语义；旧文档/代码若仍用 `retrieved_context`，请按此更名。
- 出题（`generate_question`）和评估（`evaluate`）共用同一份检索结果，避免重复检索。

### 05 app/retrieval.py —— 在线检索（向量生成 + 搜索）
**问题**：面试运行时，文本怎么变成向量、怎么检索？
**思路**：一个模块管「在线检索」的完整链路，全部走 LangChain 抽象：
```python
QianwenEmbeddings   # 千问 text-embedding-v3 的 Embeddings 实现（openai SDK 直连 DashScope）
search(query, top_k)  # query → embed_query → Milvus VectorStore 检索 → 片段列表
```
**关键点**：
- `QianwenEmbeddings` 继承 `langchain_core.embeddings.Embeddings`，实现 `embed_documents` / `embed_query`。**不用 `langchain_openai.OpenAIEmbeddings`**——实测 DashScope compatible-mode 不认它的请求格式（报 `input.contents` 错误），所以用 openai SDK 直连封装成标准接口，与 langchain_milvus 无缝配合。
- DashScope 单次 embedding 请求文本数上限 10，`embed_documents` 内部按 `BATCH=10` 分批。
- `_get_store()` 懒加载 `langchain_milvus.Milvus`（`connection_args={"uri": ...}`），`search()` 调用 `similarity_search_with_score`，返回 `(Document, score)`，再映射回 `{answer, question, source, topic, qid, score}`（`score` 保留 4 位小数）。
- 不再有 mock 关键词检索；collection 缺失 / Milvus 不可用直接抛错。

### 06 kb/build_kb.py —— 知识库离线构建
**问题**：jsonl 文档怎么变成 Milvus 里的向量？
**思路**：独立脚本，一次跑完，建库 / 建索引 / 写入 / 落盘全部交给 `Milvus VectorStore`：
```
RAG-database/*.jsonl
  → load_jsonl：逐行解析（每行一个 {id/topic/source/category/question/answer/tags}）
  → _clean_answer：去掉从 md/word 提取时残留的 "答：" / "：" 前缀
  → split_long_chunks：>1500 字符的答案二次切分（RecursiveCharacterTextSplitter，overlap=100）
  → 组 Document（page_content="题目：{q}\n答案：{a}"，metadata 含纯 answer/question/source/topic/qid）
  → Milvus VectorStore 建库（COSINE / AUTOINDEX，auto_id=True，drop_old=--recreate）+ 按 32 条一批 add_texts
```
```bash
docker exec offermaster-python python -m kb.build_kb             # 首次
docker exec offermaster-python python -m kb.build_kb --recreate  # 清空重建
```
**关键点**：
- jsonl 是天然的「一问一答」检索单元，无需像 md 那样解析 front-matter 和按 "## " 标题切块——`load_jsonl` 直接逐行 `json.loads` 即可。
- **page_content 拼接策略**：`"题目：{question}\n答案：{answer}"`，让向量相似度同时覆盖题目与答案；纯 `answer` 保留在 metadata 中，供检索层直接返回，避免二次拼装。
- collection 已存在且未加 `--recreate` 时（`_collection_exists()` 通过 `MilvusClient.has_collection` 检查）直接 `SystemExit` 退出，避免重复写入；重建需显式 `--recreate`（底层 `drop_old=True`）。
- `index_params` 显式指定 `{"metric_type": "COSINE", "index_type": "AUTOINDEX", "params": {}}`；`auto_id=True` 让 Milvus 自动生成主键。
- `embed_documents` 的 10 条内部分批在 `QianwenEmbeddings` 内部处理，`add_texts` 的 32 条批（`EMBED_BATCH=32`）是「向量化 + 插入」粒度。

### 07 nodes.py —— 节点（检索内嵌进 generate_question）
**问题**：检索这个动作放在图的哪个位置？
**思路**：**不新增 `retrieve` 节点**，而是把检索直接嵌进 `generate_question`——按当轮知识点 `focus.skill` 检索 top-3 问答对，写入 `state.reference_docs`，供本节点出题和后续 `evaluate` 复用：
```python
def generate_question(state) -> dict:
    idx = state["main_question_count"]
    focus = state["focus_points"][idx]
    refs = retrieval.search(focus["skill"], top_k=3)  # 按知识点检索知识库
    plan = llm.generate_question(focus, state["resume_summary"], state["job_description"], refs)
    return {
        "current_question": plan.question_text,
        "current_question_id": idx + 1,
        "knowledge_points": plan.knowledge_points,
        "main_question_count": idx + 1,
        "reference_docs": refs,   # 覆盖式写入，供 evaluate 复用
        "messages": [{"role": "assistant", "content": plan.question_text}],
    }
```
`evaluate` 节点直接读 `state["reference_docs"]` 传给 LLM 做对照，无需重复检索。
**关键点**：
- 检索 query 是 `focus.skill`（如「Java 基础」），而不是「问题 + 回答」——出题在收到回答之前发生，检索的目的就是给这道题找蓝本。
- 一份 `reference_docs` 同时服务出题（`generate_question`）和评估（`evaluate`）两个节点，相比独立 `retrieve` 节点少一次 State 写入和一次检索调用。
- `evaluate` 的调用签名同步增加 `reference_docs` 参数（见 [llm.py#L52-L71](file:///d:/Dev/Projects/my_project_offermaster/ai-brain/app/llm.py#L52-L71)）。

### 08 graph.py —— 图（接线）
**问题**：怎么把检索接进现有图？
**思路**：**图结构不变**——因为检索嵌在 `generate_question` 节点内部，无需新增节点或改边。阶段一的 9 个节点和普通边原样保留：
```
START → init → summarize → plan_focus → generate_question → wait_answer → evaluate
                                                            ↑              ↓  ↑
                                                            │       ┌──────┘  │（Command.goto 路由）
                                                            │       ↓         │
                                                            │   follow_up ─────┘（追问 → 回 wait_answer）
                                                            │       ↓
                                                            │   next_question
                                                            │       ├─ 主问题数 < focus_points 数量 → generate_question（出下一题，重新检索）
                                                            │       └─ 否则 → final_report → END
                                                            └─────────┘
```
图仍然是「无条件边 + Command(goto) 路由」，阶段一的结构完全没动。
- `generate_question` 出新主问题时重新检索一次（每个主问题独立 `reference_docs`）。
- `follow_up` 不重新检索，复用当前主问题的 `reference_docs` 与 `knowledge_points`。

### 09 llm.py —— LLM 增强（出题参考 + 评估对照）
**问题**：出题和评估怎么用上检索片段？
**思路**：两个函数都接收 `reference_docs`，但用法不同：
- `generate_question`：把检索到的问答对拼成 `问题：{q}\n答案：{a}` 文本块注入提示词，作为**出题蓝本**，并要求"题目与知识库问题相关，可参考或改编，但不要直接照抄问题文本"。
- `evaluate`：把参考要点编号成 `【参考1】...` 注入提示词，要求给出 0~1 的 `coverage`，`missing_keys`/`wrong_points` 以片段为对照依据。
- 已移除全部 `[MOCK]` 兜底分支，LLM 调用走真实 DeepSeek（`reasoning_effort="none"` 使 function_calling 可用）。

---

## 3. 数据怎么流动（一张图）

```
【离线构建】
RAG-database/*.jsonl
  └─ build_kb：load_jsonl（逐行解析） → _clean_answer（去 "答：" 前缀）
       → split_long_chunks（>1500 字符二次切分）
       → Document（page_content="题目：{q}\n答案：{a}"，metadata 含纯 answer）
       → Milvus VectorStore（COSINE/AUTOINDEX，auto_id=True，add_texts 32 条/批）

【在线面试】
Java 调 start（resume + job_description）
  └─ init → summarize → plan_focus（产出 focus_points[{skill, weight}]）
       └─ generate_question：
            ├─ retrieval.search(focus.skill, top_k=3)
            │    └─ QianwenEmbeddings.embed_query → Milvus top-3 问答对
            ├─ llm.generate_question(..., refs)  ← 出题蓝本
            └─ state.reference_docs = refs（覆盖式写入）
       └─ wait_answer（interrupt() 挂起，返回首题给 Java）

Java 调 answer（answer 文本）
  └─ wait_answer 接住回答（Command(resume=answer) 续跑）
       └─ evaluate：
            ├─ 读 state.reference_docs（复用 generate_question 写入的检索结果）
            ├─ llm.evaluate(..., reference_docs)  ← 评估对照
            └─ 产出 coverage / missing_keys / wrong_points
                 └─ 按 quality 路由（Command.goto）：
                       unknown / off_topic            → next_question
                       insufficient 且 追问<上限       → follow_up → wait_answer
                       其余（sufficient 或追问到顶）    → next_question
       └─ next_question：主问题数 >= focus_points 数量 → final_report → END
                          否则 → generate_question（重新检索下一题的参考）
```

State 每一步照旧写进 PostgresSaver（thread_id = session_id）。

---

## 4. 最容易踩的坑（我实际踩过）

1. **Milvus 不建索引就无法加载**：`load_collection` 报 `index not found`（code=700）。VectorStore 建库时需显式传 `index_params`（AUTOINDEX + COSINE），建库流程内部会完成「建索引 → load」。
2. **insert 后要落盘才搜得到**：旧版手动实现需 `flush()` + `load_collection()`；改用 `Milvus VectorStore` 后建库流程内部处理，但若手工 `client.insert` 仍要注意。
3. **langchain 的 OpenAIEmbeddings 调 DashScope 报错**：`input.contents is neither str nor list of str`。DashScope compatible-mode 不兼容其请求格式，需自行实现 `Embeddings` 接口（本项目 `QianwenEmbeddings` 用 openai SDK 直连）。
4. **DashScope embedding 单次批量上限 10**：一次传超过 10 条文本报 `batch size is invalid, it should not be larger than 10`。`embed_documents` 内部按 10 条分批。
5. **embedding 模型要选对**：`multimodal-embedding-v1` 在 OpenAI 兼容模式下文本检索相关性差（0.35 且完全无关），换 `text-embedding-v3` 后命中 0.78~0.86，维度同为 1024，schema 不用改。

---

## 5. 一句话总结

> 先起基础设施、把千问/Milvus 配置收进 config（compose + config），按生命周期拆两层——在线检索（app/retrieval.py：QianwenEmbeddings + Milvus VectorStore）与离线构建（kb/build_kb.py：jsonl → Document → VectorStore 建库写入），把检索**内嵌进 `generate_question` 节点**而非新增 `retrieve` 节点——一份 `reference_docs` 同时作为出题蓝本和 `evaluate` 的对照依据（nodes + graph + llm）——评估从此有据可依、出题不再凭空硬编；Milvus / Key 必须配好，无 mock 兜底，依赖缺失时直接报错暴露问题。
