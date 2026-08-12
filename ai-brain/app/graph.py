"""LangGraph 图：组装节点并提供会话入口。

路由约定：本图不再使用条件边；需要动态路由的节点（evaluate / next_question）
直接返回 Command(goto=...) 完成路由，图只用普通边串起固定流程。

分层约定：
- state.py   -> InterviewState（状态）
- nodes.py   -> 节点函数（对状态做局部更新，并可返回 Command 路由）
- graph.py   -> 图构建 + 会话入口（start / answer）
- llm.py     -> 所有 LLM 调用
"""

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .checkpointer import get_async_checkpointer, get_checkpointer
from .nodes import (
    compress_history,
    evaluate,
    final_report,
    follow_up,
    generate_question,
    init,
    next_question,
    plan_focus,
    summarize,
    wait_answer,
)
from .schemas import StartRequest
from .state import InterviewState


# ---------- 图构建 ----------

builder = StateGraph(InterviewState)

builder.add_node("init", init)
builder.add_node("summarize", summarize)
builder.add_node("plan_focus", plan_focus)
builder.add_node("generate_question", generate_question)
builder.add_node("wait_answer", wait_answer)
builder.add_node("evaluate", evaluate)
builder.add_node("follow_up", follow_up)
builder.add_node("next_question", next_question)
builder.add_node("compress_history", compress_history)
builder.add_node("final_report", final_report)

builder.add_edge(START, "init")
builder.add_edge("init", "summarize")
builder.add_edge("summarize", "plan_focus")
builder.add_edge("plan_focus", "generate_question")
builder.add_edge("generate_question", "wait_answer")
builder.add_edge("wait_answer", "evaluate")
builder.add_edge("follow_up", "wait_answer")
builder.add_edge("final_report", END)

app = builder.compile(checkpointer=get_checkpointer())

# SSE 流式专用图：AsyncPostgresSaver（同步 PostgresSaver 无 async 接口）。
# 与 app 共享同一组 Postgres checkpoint 表，thread_id 状态互通。
_async_app = None


async def _get_async_app():
    global _async_app
    if _async_app is None:
        _async_app = builder.compile(checkpointer=await get_async_checkpointer())
    return _async_app


# ---------- 会话入口 ----------

# SSE 流式：节点开始执行时推送的进度文案（仅 LLM 重节点，跳过 init/wait_answer/next_question）。
NODE_LABELS = {
    "summarize": "正在总结简历…",
    "plan_focus": "正在规划考察重点…",
    "generate_question": "正在生成题目…",
    "evaluate": "正在评估回答…",
    "follow_up": "正在生成追问…",
    "compress_history": "正在压缩历史…",
    "final_report": "正在生成诊断报告…",
}


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}


def start_session(session_id: str, req: StartRequest) -> dict:
    """Java 发起面试：跑图到第一个 wait_answer 挂起，返回首个问题。"""
    initial = {
        "session_id": session_id,
        "resume": req.resume,
        "job_description": req.job_description,
        "max_follow_ups": req.max_follow_ups,
    }
    result = app.invoke(initial, config=_config(session_id))
    return _parse_result(result)


def answer_session(session_id: str, answer: str) -> dict:
    """Java 提交回答：从 checkpoint 续跑一轮，返回下一题或终局报告。"""
    result = app.invoke(
        Command(resume=answer),
        config=_config(session_id),
    )
    return _parse_result(result)


def _parse_result(state: dict) -> dict:
    if state.get("final_report"):
        return {"status": "done", "report": state["final_report"]}
    return {
        "status": "need_answer",
        "question": state.get("current_question"),
        "question_id": state.get("current_question_id"),
        "follow_up": state.get("follow_up_count", 0) > 0,
    }


# ---------- SSE 流式入口 ----------

# LLM 调用 tags 前缀：命中即把该调用的 token 增量推给前端（真逐 token 流式）。
STREAM_TAG_PREFIX = "stream:"


async def _aiter_events(input_, config: dict):
    """迭代 LangGraph 事件流。

    - on_chain_start（有 NODE_LABELS 的节点）→ stage 事件（进度文案）
    - on_chat_model_stream（tags 命中 stream: 前缀）→ token 事件（逐 token 增量）
    结构化输出节点（generate_question / evaluate / final_report）无 stream tag，
    整体 result 返回后由前端做打字机渲染。
    """
    g = await _get_async_app()
    async for event in g.astream_events(input_, config=config, version="v2"):
        ev = event.get("event")
        if ev == "on_chain_start":
            name = event.get("name", "")
            if name in NODE_LABELS:
                yield {"type": "stage", "data": {"node": name, "label": NODE_LABELS[name]}}
        elif ev == "on_chat_model_stream":
            for tag in event.get("tags") or []:
                if tag.startswith(STREAM_TAG_PREFIX):
                    chunk = event.get("data", {}).get("chunk")
                    piece = ""
                    if chunk is not None:
                        content = getattr(chunk, "content", None)
                        if isinstance(content, str):
                            piece = content
                    if piece:
                        yield {
                            "type": "token",
                            "data": {"node": tag.split(":", 1)[1], "token": piece},
                        }
                    break


async def stream_start(session_id: str, req: StartRequest):
    """SSE 流式版本：start 全流程，产出 stage / result 事件。

    与 start_session 同图同输入；流在首个 wait_answer 挂起处自然结束，
    挂起后状态已落 checkpoint，用 aget_state 读取最终结果。
    """
    initial = {
        "session_id": session_id,
        "resume": req.resume,
        "job_description": req.job_description,
        "max_follow_ups": req.max_follow_ups,
    }
    config = _config(session_id)
    g = await _get_async_app()
    async for ev in _aiter_events(initial, config):
        yield ev
    state = await g.aget_state(config)
    yield {"type": "result", "data": _parse_result(state.values)}


async def stream_answer(session_id: str, answer: str):
    """SSE 流式版本：answer 一轮，产出 stage / result 事件。"""
    config = _config(session_id)
    g = await _get_async_app()
    async for ev in _aiter_events(Command(resume=answer), config):
        yield ev
    state = await g.aget_state(config)
    yield {"type": "result", "data": _parse_result(state.values)}
