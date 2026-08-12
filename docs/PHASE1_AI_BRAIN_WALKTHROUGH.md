# 阶段一：AI 大脑（ai-brain/app）编写思路

> 目标读者：想真正看懂「为什么这么写」的人。
> 阅读配套：`ai-brain/app/` 下的 9 个文件。推荐先读这份思路，再按 `config → schemas → state → llm → nodes → checkpointer → graph → api` 逐个打开文件对照；`draw_mermaid.py` 是辅助工具，看完 `graph.py` 后可用它打印整张图。

---

## 0. 先想清楚：这个服务到底要做什么

写代码前先回答三个问题：

1. **职责边界**：Java 负责登录、存业务数据、编排；Python 只负责「面试的决策大脑」——出题、评估、追问、何时结束、生成诊断报告。
2. **对外接口**（就两个）：
   - `POST /ai/interviews/{session_id}/start` → 返回第一道题
   - `POST /ai/interviews/{session_id}/answer` → 返回下一道题，或最终报告
3. **硬约束**：状态要能持久化（断线恢复）；LLM 调用全放 Python；MVP 阶段可以没有 RAG。

只要接口固定成这两个，内部怎么拆都行。剩下就是「怎么把内部组织得清晰」。

---

## 1. 整体思考顺序（自底向上）

写的时候我按「先数据、后动作、再组装、最后入口」的顺序想：

```
配置 → 数据契约 → 状态 → LLM → 节点 → 持久化 → 组装成图 → 对外接口
```

每一层都「只依赖它前面已经写好的东西」，不回头改，链路就顺。`draw_mermaid.py` 只是最后拿来「看整图」的辅助脚本。

---

## 2. 逐个文件：它解决什么问题、怎么写

### 01 config.py —— 配置从哪来
**问题**：`DEEPSEEK_API_KEY`、数据库地址、模型名这些散在哪？
**思路**：用 `pydantic-settings` 把「环境变量 / .env」读成带类型、带默认值的 `settings` 对象。
```python
class Settings(BaseSettings):
    deepseek_api_key: str = ""          # 必填；缺失则 LLM 调用失败（无 mock 兜底）
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
settings = Settings()                   # 模块级实例，全项目共用
```
**关键点**：先在 `config.py` 定义「有哪些外部输入」，后面所有文件只管 `settings.xxx`，不用到处 `os.getenv`。

### 02 schemas.py —— 数据长什么样（契约）
**问题**：LLM 返回、请求体这些结构化数据，怎么定义？
**思路**：用 Pydantic 定义「双方都认的 JSON 形状」。
```python
class QuestionPlan(BaseModel):
    question_text: str
    knowledge_points: List[str]   # 本题应覆盖的知识点（供 evaluate / follow_up 使用）

class AnswerAssessment(BaseModel):
    question_id: int
    quality: Literal["sufficient", "insufficient", "unknown", "off_topic"]
    coverage: Optional[float] = None   # MVP 无 RAG 时为 None
    missing_keys: List[str] = []
    wrong_points: List[str] = []
    note: str = ""
```
**关键点**：先把「出题计划、评估、报告」这些数据模型定死，后面 LLM 结构化输出、节点、图都围绕它们转。注意 `StartRequest` 只带 `resume / job_description / max_follow_ups`——**没有 min/max_questions**，主问题数量由 `plan_focus` 返回的知识点数量决定。

### 03 state.py —— 全流程共享什么（状态）
**问题**：一轮轮对话，Python 要记住什么？
**思路**：定义一个 `TypedDict` 作为 LangGraph 的 State——所有节点读它、改它。
```python
class InterviewState(TypedDict, total=False):
    session_id: str
    resume: str
    resume_summary: str
    job_description: str
    focus_points: List[dict]          # [{skill, weight}]，主问题数量由它决定
    current_question: str
    current_question_id: int
    knowledge_points: List[str]       # 本题应覆盖知识点
    follow_up_count: int
    messages: Annotated[List[dict], operator.add]      # 追加式：节点只返回新增
    assessments: Annotated[List[dict], operator.add]   # 追加式：节点只返回新增
    main_question_count: int          # focus_points 的游标 + 是否问完所有知识点
    max_follow_ups: int               # 每题最多追问次数，Java start 传入，默认 2
    reference_docs: List[dict]        # 阶段二 RAG 用，MVP 为空；M5 起由 generate_question 按知识点检索填充，出题与评估共用
    final_report: Optional[dict]
```
**关键点**：State 就是「面试的存档」。字段想清楚，节点和图的逻辑就顺了。
- **主问题轮数 = `focus_points` 的数量**（`plan_focus` 提示词控制 3~5 个），不再有独立的 min/max_questions。
- `messages` / `assessments` 用 `Annotated[list, operator.add]` **追加式 reducer**：节点只「上交新增元素」，LangGraph 自动合并，不用手动拼整表。

### 04 llm.py —— 节点靠什么干活
**问题**：出题、评估、总结都要调 LLM，怎么封装？
**思路**：把「调 LLM + 解析成结构化对象」收进一个文件（早期曾内建 mock 模式，后续已移除，统一走真实 DeepSeek）。
```python
def evaluate(question, answer, resume_summary, question_id, reference_docs) -> AnswerAssessment:
    llm = _chat().with_structured_output(AnswerAssessment, method="function_calling")
    ...
```
**关键点**：节点只调用 `llm.evaluate(...)`，不关心 LLM 细节——这是「依赖隔离」。
- 真实链路用 `with_structured_output(..., method="function_calling")` 拿结构化结果，并在 `_chat()` 里加 `reasoning_effort="none"` 关闭 thinking 模式，保证 function_calling 可用。
- M5 RAG 接入后，`evaluate` 增加 `reference_docs` 参数（检索片段注入提示词做对照）；`generate_question` 同样接收 `reference_docs` 作为出题蓝本。详见 `docs/PHASE2_RAG_WALKTHROUGH.md`。

### 05 nodes.py —— 单个动作怎么写（节点）
**问题**：每个节点到底做什么？
**思路**：一个函数 = 「读 State → 局部更新 State → 返回 dict 或 Command」。只做一件事。
```python
def evaluate(state) -> Command:
    answer = state["messages"][-1]["content"]
    assessment = llm.evaluate(
        state["current_question"], answer, state["resume_summary"],
        state["current_question_id"], state.get("reference_docs", []),
    )
    update = {"assessments": [assessment.model_dump()]}
    # 用 Command(update=..., goto=...) 直接在节点内完成路由
    if assessment.quality in ("unknown", "off_topic"):
        return Command(update=update, goto="next_question")
    if assessment.quality == "insufficient" and state["follow_up_count"] < state["max_follow_ups"]:
        return Command(update=update, goto="follow_up")
    return Command(update=update, goto="next_question")
```
**关键点**：节点**不互相调用**，只改 State；`messages/assessments` 只返回新增元素（reducer 合并）。需要动态路由的节点（`evaluate` / `next_question`）直接返回 `Command(goto=...)`，由节点自己决定下一步去哪。
- M5 RAG 接入后，`evaluate` 从 `state.reference_docs` 取检索片段传给 LLM 做对照；`reference_docs` 由 `generate_question` 节点在出题时填充，两节点共用同一份。

### 06 checkpointer.py —— 状态存哪（持久化）
**问题**：断线后怎么恢复？
**思路**：用 PostgresSaver 把每一步 State 快照存进 PostgreSQL。
```python
conn = Connection.connect(settings.database_url, autocommit=True)
checkpointer = PostgresSaver(conn)
checkpointer.setup()
```
**关键点**：只要 `compile(checkpointer=...)`，LangGraph 就自动按 `thread_id` 存取状态，实现断线恢复。

### 07 graph.py —— 怎么连成图（组装）
**问题**：这么多节点，流程怎么串起来？
**思路**：模块级 `builder` 定义节点和边，`app` 一次编译好。
```python
builder = StateGraph(InterviewState)
builder.add_node("generate_question", generate_question)
builder.add_edge("START", "init")
builder.add_edge("plan_focus", "generate_question")
builder.add_edge("generate_question", "wait_answer")
builder.add_edge("wait_answer", "evaluate")
builder.add_edge("follow_up", "wait_answer")
builder.add_edge("final_report", END)
app = builder.compile(checkpointer=get_checkpointer())
```
**关键点**：**本图不用条件边**——固定流程用普通边串起来，需要动态路由的节点（`evaluate` / `next_question`）在节点内返回 `Command(goto=...)` 完成路由。图因此非常干净，路由逻辑都收在节点里，好读、好测。

### 08 api.py —— 对外怎么暴露（入口）
**问题**：Java 怎么调？
**思路**：FastAPI 路由只管「收请求 → 调 graph → 返回」，不写业务逻辑。
```python
@app.post("/ai/interviews/{session_id}/answer")
def answer(session_id: str, payload: AnswerRequest):
    return answer_session(session_id, payload.answer)
```

### 09 draw_mermaid.py —— 怎么看整张图（辅助）
**问题**：图长什么样，光看代码不够直观？
**思路**：用 LangGraph 自带的 `app.get_graph().draw_mermaid()` 一键输出当前编译图的 Mermaid 代码，方便贴进文档/画布核对。
```bash
docker exec offermaster-python python -m app.draw_mermaid
```

---

## 3. 数据怎么流动（一张图）

```
Java 调 start
  └─ app.invoke(...) → init → summarize → plan_focus → generate_question
        └─ wait_answer (interrupt 挂起，返回第一题)
Java 调 answer
  └─ app.invoke(Command(resume=answer)) → wait_answer 接住回答
        └─ evaluate 路由（Command.goto）：
             unknown / off_topic            → next_question
             insufficient 且 追问<上限       → follow_up → wait_answer
             其余（sufficient 或追问到顶）    → next_question
        └─ next_question 路由（Command.goto）：
             主问题数 >= focus_points 数量   → final_report → END
             否则                           → generate_question → wait_answer
State 每一步自动写进 PostgresSaver（thread_id = session_id）
```

---

## 4. 最容易踩的坑（我实际踩过）

1. **必要时用 `interrupt()`，而不是 `interrupt_before`**：LangGraph 1.2 里 `interrupt_before` 放 config 不生效，会在某次测试里直接跑穿到 END。用节点内的 `interrupt()` 才能拿到 resume 值。
2. **`Command(resume=...)` 传 dict 不会合并进 State**：resume 值要*从 `interrupt()` 的返回值拿*，不能指望它自动写进某个字段。
3. **条件边函数不要又注册成节点**：早期 `continue_check` 曾经既 `add_node` 又当条件边，结果它返回字符串（路由 key）被当成节点返回值，报 `Expected dict, got generate_question`。现在的写法绕开了这个坑——不再用条件边，动态路由统一走 `Command(goto=...)`。
4. **早期 mock 模式便于无 Key 自测，现已移除**：MVP 阶段没有 API Key 也能端到端自测；生产化后删除 mock 分支，LLM 统一走真实 DeepSeek，Key 缺失直接报错。
5. **`eval_keys` 已改名 `knowledge_points`**：追问和评估都围绕「本题应覆盖的知识点」转，字段统一叫 `knowledge_points`，别再用旧名。

---

## 5. 一句话总结

> 先定配置和数据长什么样（config/schemas），再定面试要记哪些状态、用追加式 reducer 管对话和评估（state），把「调 LLM」封一层（llm，真实 DeepSeek），写一个个只改状态、用 Command(goto) 路由的节点（nodes），用 PostgresSaver 存档（checkpointer），最后用 builder 拼成图、暴露成两个接口（graph + api），需要看整图时用 draw_mermaid 打印。
