# AI 面试官（Java + Python 双栈）设计决策记录

> 记录日期：2026-08-10
> 架构：Java（Spring Boot，业务底座）+ Python（FastAPI + LangChain + LangGraph，AI 大脑）
> 说明：按“第1步→第7步”逐步确认；P0/P2 审核修订已内嵌到对应章节原文，不以补丁形式追加。

---

## 第 1 步：产品概念 - MVP 边界（已锁定）

| # | 决策点 | 结论（MVP） |
|---|--------|-------------|
| 1 | 交互形式 | 纯文本聊天（语音留作后续独立通道） |
| 2 | 结束机制 | 自适应 + 硬顶：min/max 题数 + 每题追问次数上限，由 LangGraph 条件边判定是否结束 |
| 3 | 简历输入 | 仅支持粘贴文本（PDF/Word 解析后置） |
| 4 | 输出形式 | **问题诊断报告**：最终不产出数值评分，只列出“答错/答非所问/需改进”的问题、原因与建议 |
| 5 | 用户控制权 | 完全由 AI 主导，禁止打断（“跳过”扩展留到后置阶段） |

---

## 第 2 步：业务逻辑 - 决策大脑（已锁定）

### 决策流程（文字流程图）

```
[入口] 简历文本 + 岗位描述
   │
   ▼
N1  简历摘要（LLM）────────────────► resume_summary
   │
   ▼
N2  考察重点提炼（LLM）───────────► focus_points[知识点+权重]
   │
   ▼
N3  出主问题（LLM + RAG 选知识点）──► current_question + knowledge_points
   │
   ▼
N4  提问 / 接收回答（Java 传输层）──► user_answer（轮询推进，见第5步协议）
   │
   ▼
N5  评估回答（LLM + RAG 对照）────► AnswerAssessment(quality+coverage+错误清单)
   │
   ├── quality = unknown(不知道/敷衍) ──► 记入错误清单 ─► N8 换下一主题 ─►N3
   │
   ├── quality = off_topic(答非所问) ────► 记入错误清单 ─► N8 换下一主题 ─►N3
   │
   ├── quality = insufficient 且 追问次数<上限 → N7 追问 ─►回 N4
   │
   ├── quality = insufficient 且 追问次数到顶 → N8 换下一主题 ─►N3
   │
   └── quality = sufficient ────────────────► N8 换下一主题 ─►N3
   │
   ▼
N9  结束判定（条件边：min/max + 正常作答覆盖度）── 不满足 ──► N3 出下一题
   │            │
   │            └─ 满足结束条件
   ▼
N10 终局诊断报告（LLM，汇总全场）───► FinalReport(错误清单+整体结论)
   │
   ▼
N11 报告写入（Java 持久化 + 前端拉取）
```

### 关键业务结论

1. **决策步骤**：简历摘要 → 考察重点提炼 → 出主问题 → 接收回答 → 评估回答 →（追问/换题）→ 结束判定 → 终局诊断报告 → 报告落库。
2. **RAG 使用范围**：出题(N3)、评估(N5)、追问(N7) 需要八股知识库；简历摘要、考察重点、终局报告为纯 LLM。MVP 阶段暂不接 RAG（见第 6 步）。
3. **“回答是否充分”判定**：RAG 知识点覆盖度 + 错误点识别 + 长度/“不知道”关键词兜底，产出 quality ∈ {sufficient, insufficient, unknown, off_topic}（不产出数值分）。
4. **追问内容**：知识库提供“待核验知识点”作骨架，LLM 基于薄弱点现场组织自然语言；追问复用主问题的 knowledge_points。
5. **“不知道”与“答非所问”**：均**记入错误清单**（unknown 标“未作答/敷衍”，off_topic 标“答非所问”）并换下一主题，不提示引导、不追问。
---

## 第 3 步：技术选型 - 双栈技术栈（已锁定）

### Java 侧（业务底座）
- Spring Boot 3.4.x + Java 17（若环境允许可上 21 LTS）
- Starter：web、validation、data-jpa、security+JWT、data-redis、actuator、springdoc-openapi
- 跨语言调用：WebClient（非阻塞）
- 中间件：PostgreSQL（主库）、Redis（缓存/限流/会话）；MQ 后置

### Python 侧（AI 大脑）
- Python 3.11 + FastAPI + uvicorn
- langchain-core、langchain-openai、langgraph、langgraph-checkpoint + langgraph-checkpoint-postgres（PostgresSaver）
- langchain-text-splitters、pydantic v2、pydantic-settings、httpx
- 结构化输出：with_structured_output 绑定 Pydantic

### 跨语言通信
- Java↔Python：REST JSON（同步）+ SSE（逐 token/逐轮流式，阶段二起用）
- 前端↔Java：WebSocket 或 SSE
- gRPC / MQ：后续阶段

### 数据库与向量库
- 关系库：PostgreSQL（会话、用户、问题、回答、诊断报告）
- 向量库：**Milvus**（独立向量库，八股知识库，阶段二接入）
- 缓存：Redis

### LLM
- 主模型：**DeepSeek**（OpenAI 兼容协议）
- Embedding：**阿里云 DashScope 千问 text-embedding-v3**（OpenAI 兼容接口，1024 维）；注：DeepSeek 无官方 embedding 模型
- 封装位置：所有 LLM/Embedding 调用集中在 Python 侧；Java 不直连 LLM

### 部署
- MVP/中期：Docker Compose（java-app、python-app、postgres、milvus、redis、nginx[可选]）
- 规模化：K8s

### 认证
- **Spring Security + JWT**
---

## 第 4 步：数据流 - 数据从哪来到哪去（已锁定）

1. 简历：前端 POST → Java 存 PostgreSQL `resume` 表（权威库）；Python 只读进 LangGraph State，不落库；入库前做脱敏（手机号/邮箱替换）并告知用户用途。
2. 每轮上下文：当前问题 + 过往对话(messages) + 简历摘要 + 当轮知识库片段；权威数据在 PostgreSQL（session/message 表），运行期在 Python State + PostgresSaver，知识库片段不落业务库。
3. 知识库离线构建：`RAG-database/*.jsonl`（每行一个 Q-A 对）→ `_clean_answer` 清洗 → `langchain-text-splitters` 对超长答案二次切分 → 云端 embedding（千问 text-embedding-v3）→ 写 Milvus collection（metadata: qid/question/source/topic/answer）；由 `kb/build_kb.py` 脚本执行。
4. 在线检索：`generate_question` 节点按当轮 `focus.skill` 构造检索 Query → 云端 embedding → 查 Milvus top-3 → snippets 进 `State.reference_docs`，**出题（generate_question）和评估（evaluate）共用同一份**，不重复检索。
5. 诊断报告：Python N10 生成 FinalReport JSON（错误清单+整体结论）→ 返回 Java → Java 校验后写 PostgreSQL `report` 表 → 前端 GET /api/interviews/{id}/report（JWT 鉴权 + **校验报告归属当前用户，防 IDOR**）。

### 数据载体
- resume 表 / interview_session 表 / message 表 / report 表：PostgreSQL
- LangGraph State + PostgresSaver checkpoint：Python 执行状态
- 八股知识库 collection：Milvus
- 补充确认：每一轮对话 Java 侧**同步落库**到 PostgreSQL（interview_session / message 表），天然支持断线恢复。

---

## 第 5 步：LangGraph 状态图设计 - “考官大脑”（已锁定，含 P0 修订）

### State Schema（Pydantic / TypedDict）

```python
class AnswerAssessment(BaseModel):
    question_id: int
    quality: Literal["sufficient", "insufficient", "unknown", "off_topic"]
    coverage: Optional[float]            # RAG 知识点覆盖度 0~1；MVP 无 RAG 时为 None
    missing_keys: List[str]              # 未覆盖/答错的知识点
    wrong_points: List[str]              # 具体错误点（LLM 判定）
    note: str                            # 一句话说明（含 unknown/off_topic 标记）

class WrongQuestion(BaseModel):
    question_id: int
    question_text: str
    user_answer: str
    kind: Literal["wrong", "unknown", "off_topic"]   # 区分错误/未作答/答非所问
    missing_keys: List[str]
    wrong_points: List[str]
    suggestion: str                      # 改进建议

class FinalReport(BaseModel):
    session_id: str
    summary: str                         # 整体结论（非分数）
    wrong_questions: List[WrongQuestion]

class InterviewState(TypedDict):
    session_id: str
    resume: str
    resume_summary: str
    job_description: str
    focus_points: List[dict]             # [{skill, weight}]，主问题数量 = len(focus_points)
    current_question: str
    current_question_id: int
    knowledge_points: List[str]
    follow_up_count: int
    messages: List[ChatMessage]
    assessments: List[AnswerAssessment]  # 每题评估
    main_question_count: int
    max_follow_ups: int                  # 由 Java 在 start 请求传入
    reference_docs: List[dict]   # M5：当前主问题的知识库参考问答对，由 generate_question 检索填充，出题与评估共用
    final_report: Optional[FinalReport]
```

### 节点
init / summarize / plan_focus / generate_question / wait_answer / evaluate / follow_up / next_question / final_report
- `next_question` 节点：**换题时重置 `follow_up_count = 0`**；当 `main_question_count >= len(focus_points)` 时进入终局报告，否则出新主问题。
- `evaluate` 节点：输出 `AnswerAssessment`；`unknown` 与 `off_topic` 都会写入 `assessments`，进终局错误清单。

### wait_answer 唤醒协议（interrupt() 挂起 + 轮询式 HTTP）
- `POST /ai/interviews/{id}/start`：Java 发起，Python 初始化图并返回第一题。
- `POST /ai/interviews/{id}/answer` `{turn_id, answer}`：Java 每轮调用一次；Python 以 `thread_id = session_id` 定位 PostgresSaver checkpoint，推进一轮，返回下一题或 `final_report`。
- 图在 `wait_answer` 节点内调用 **`interrupt()` HITL 函数挂起**，配合 PostgresSaver 支持续跑；Java 侧表现为普通轮询/同步请求，无长连接。
- **演进说明**：早期设计为 config 级 `interrupt_before` 暂停，但 LangGraph 1.2 实测中 `interrupt_before` 放 config 不生效（会直接跑穿到 END），后改用节点内 `interrupt()` 函数（详见 `docs/PHASE1_AI_BRAIN_WALKTHROUGH.md` §4.1）；Java 侧协议不变，仍是 `start` / `answer` 两个端点轮询。

### 节点路由（Command，不用条件边）
- 图不再注册条件边；需要动态路由的节点直接返回 `Command(goto=...)`，图只用普通边串起固定流程。
- `evaluate` 节点评估后路由：unknown / off_topic → next_question（均记入错误清单）；insufficient 且追问<上限 → follow_up；insufficient 且追问到顶 → next_question；sufficient → next_question。
- `next_question` 节点换题后路由（结束判定）：
  - `count >= len(focus_points)` → final_report（主问题轮数 = 考察知识点数量，由 `plan_focus` 提示词控制，建议 3~5 个）；
  - 否则 → generate_question。
- 已移除 `min_questions` / `max_questions` 与“覆盖度≥80% 提前结束”逻辑（MVP 无 RAG 时 `coverage` 恒为 None）；若阶段二接入 Milvus 后需要自适应提前结束，再以知识点数量为基数扩展。

### Checkpointer
- **MVP 即用 PostgresSaver**（同一 PostgreSQL 实例、Python 独立 schema），彻底消除 MemorySaver 重启丢 State 的问题。
- 职责划分：PostgreSQL 业务表是**业务数据权威**（Java 写 messages/报告）；Python checkpoint 表是**执行状态权威**（图跑到哪、State 是什么）。
- 每个节点完成后、wait_answer 前各存一次 checkpoint，支撑断线恢复。

### Human-in-the-Loop（预留扩展点，MVP 关闭）
- `wait_answer` 节点已使用 `interrupt()` 函数实现「等待用户回答」的挂起点（这是 LangGraph HITL 机制的运用，而非人工审核）。
- 未来真正的人工干预点（仍可继续用 `interrupt()` 预留）：发送下一题前（支持主动跳过/中止）、终局报告生成后落库前（人工复核）。MVP 阶段这两个扩展点不启用。
---

## 第 6 步：实施路线图 - 分阶段落地（已锁定）

### 阶段一（MVP）：跑通「多轮面试 → 诊断报告」
- Java：Spring Boot 骨架 + JWT 认证 + 用户/面试会话/消息/报告表 CRUD + 同步落库 + WebClient 调用封装 + 报告归属(IDOR)校验 + 简历脱敏。
- Python：LangGraph 全部节点 + **PostgresSaver** + DeepSeek 调用；**暂不接 Milvus/RAG**，出题/评估用 LLM 硬编码，`coverage=None`，结束判定仅用 min/max 计数。
- 前端：最简页面（贴简历 → 聊天框 → 结果清单）。
- 暂缓：Milvus/RAG、SSE 流式（先同步返回）、Redis、文档解析、语音、多语言。
- 验收：完整流程可跑通；Python 重启后会话可续跑；异常可恢复；报告 JSON 结构正确。预计 2~3 周。

### 阶段二（增强）：RAG + 更真实的考官
- Python：接入 Milvus + 知识库离线构建脚本 + 在线检索 Query 构造 + `coverage` 用于评估质量与报告建议（**不参与收尾判断**）+ **历史摘要压缩节点**（防上下文膨胀，M6 落地）。
- Java：Redis 限流/缓存；SSE 流式返回；WebSocket 前端连接。
- 暂缓：文档解析上传、语音、多种报告导出。
- 验收：出题命中知识点；评估标出 missing_keys/wrong_points；`coverage` 非 null 且用于评估对照；长面试上下文可控。预计 2~3 周。

> M5（RAG 知识库）已落地：Milvus standalone + 千问 text-embedding-v3 + `kb/build_kb.py` 离线构建 + `generate_question` 节点内嵌在线检索（结果存入 `state.reference_docs`，出题与评估共用）；`coverage` 由评估产出、不参与收尾（详见 `docs/PHASE2_RAG_WALKTHROUGH.md`）。
>
> M6（历史摘要压缩）已落地：新增 `compress_history` 节点，**上下文空间溢出触发**（`messages` 总字符数超过 `history_max_chars` 预算，默认 8000，可在 config/.env 调整）——`next_question` 在换题/收尾前判定溢出，溢出才路由压缩：按 `topic_start` 把已完成旧主题压缩进 `state.conversation_summary`（running summary，纯文本）并整体裁剪 `state.messages`（`messages` 改用支持「追加/替换」双语义的自定义 reducer `reduce_messages`），保留最近主题原文；未溢出不调用 LLM、不裁剪。`final_report` 以「历史摘要 + 最近一题完整对话 + 全量 assessments」生成报告，提示词按 `summary` 是否为空**自适应**（未压缩 → 完整对话记录；已压缩 → 历史摘要 + 最近主题完整对话），防上下文膨胀。不新增外部接口，Java 协议不变。

### 阶段三（完善）：生产级
- Python：会话级并发无状态化；可观测（Langfuse/OpenTelemetry）；多副本。
- Java：异步报告生成（MQ）、熔断/限流、Redis 分布式锁（替换 MVP 内存锁）、Docker Compose 一键部署、监控告警。
- 暂缓：K8s、语音、文档解析（若仍非核心）。
- 验收：断线可恢复、故障可观测、Docker Compose 一键起、恢复后状态一致、并发正确。预计 2~3 周。
---

## 第 7 步：异常处理 - 容错与兜底（已锁定，含 P0 修订）

1. LLM JSON 格式乱：绝不崩溃。统一用 with_structured_output 绑定 Pydantic 挡源头；解析失败走 retry_output 重试 1 次（带错误提示）；仍失败进 fallback_finalize 宽松解析/降级输出，保证流程能结束。
2. 回答超长：Java 入口限制（如 4000 字符）超长拒绝并提示精简；Python wait_answer 二次截断防 State 撑爆。
3. Python 服务挂：Java WebClient 配超时 + Actuator /health 探活 + Resilience4j 熔断；返回友好提示；依赖同步落库 + PostgresSaver 续跑。
4. 网络超时：幂等/只读请求做 2~3 次指数退避重试；会推进面试状态的请求不自动重试，采**“重发对齐”**（非真正回滚 checkpoint）：Java 为每个 turn 记录 `turn_id → checkpoint_id`，超时后用户重发时先查该 turn 是否已提交（Python get-state / turn_id 去重）；已提交直接返回结果，未提交则从该 checkpoint 推进一次。`session_id = thread_id`。
5. 刷新/断线：Java 同步落库 + PostgresSaver checkpoint；前端用 session_id 调 GET /api/interviews/{id}/state 恢复，原地续跑不重头。
6. 并发互斥：同一 session 串行化 —— MVP 单实例用 per-session 内存锁（ConcurrentHashMap<session_id, Lock>）+ turn_id 严格递增校验（拒绝乱序）；阶段三横向扩容时换 Redis 分布式锁。
7. 安全与合规：报告接口校验资源归属当前用户（防 IDOR）；简历入库前脱敏（手机号/邮箱替换）+ 告知用户用途，符合个保法要求。
