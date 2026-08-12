"""LangGraph 节点：每个节点是对 InterviewState 的一次局部更新。

messages / assessments 使用 Annotated[list, operator.add] reducer，
节点只需返回“新增元素”，LangGraph 会自动追加。
"""

from langgraph.types import Command, interrupt

from . import llm
from . import retrieval
from .config import settings
from .state import InterviewState


def _history_chars(messages: list) -> int:
    """messages 总字符数（粗粒度 token 代理，用于判断上下文是否溢出）。"""
    return sum(len(m.get("content", "")) for m in messages)


def _history_over_budget(messages: list) -> bool:
    """上下文空间溢出判定：总字符数超过 history_max_chars 预算。"""
    return _history_chars(messages) > settings.history_max_chars


def init(state: InterviewState) -> dict:
    """初始化面试状态：补齐列表与计数默认值。"""
    return {
        "messages": [],
        "assessments": [],
        "conversation_summary": "",
        "topic_start": 0,
        "follow_up_count": 0,
        "main_question_count": 0,
        "reference_docs": [],
    }


def summarize(state: InterviewState) -> dict:
    """N1 简历摘要：由 LLM 提炼简历。"""
    return {"resume_summary": llm.summarize(state["resume"], state["job_description"])}


def plan_focus(state: InterviewState) -> dict:
    """N2 考察重点：由 LLM 规划待考知识点。"""
    return {"focus_points": llm.plan_focus(state["resume_summary"], state["job_description"])}


def generate_question(state: InterviewState) -> dict:
    """N3 出主问题：按知识点检索知识库 → 参考问答对生成问题。"""
    idx = state["main_question_count"]
    focus = state["focus_points"][idx]  # next_question 保证 idx 一定在范围内
    refs = retrieval.search(focus["skill"], top_k=3)  # 按知识点检索知识库
    plan = llm.generate_question(focus, state["resume_summary"], state["job_description"], refs)
    return {
        "current_question": plan.question_text,
        "current_question_id": idx + 1,
        "knowledge_points": plan.knowledge_points,
        "main_question_count": idx + 1,
        "reference_docs": refs,
        "topic_start": len(state["messages"]),  # 当前主题在 messages 中的起始下标
        "messages": [{"role": "assistant", "content": plan.question_text}],
    }


def wait_answer(state: InterviewState) -> dict:
    """N4 接收回答：interrupt() 挂起等待用户输入（Java 侧表现为轮询）。"""
    answer = (interrupt("wait_for_answer") or "").strip()
    return {"messages": [{"role": "user", "content": answer}]}


def evaluate(state: InterviewState) -> Command:
    """N5 评估回答：生成 AnswerAssessment，并按回答质量路由到追问或换题。"""
    answer = state["messages"][-1]["content"]
    question_id = state["current_question_id"]
    assessment = llm.evaluate(
        state["current_question"],
        answer,
        state["resume_summary"],
        question_id,
        state.get("reference_docs", []),
    )
    update = {"assessments": [assessment.model_dump()]}

    if assessment.quality in ("unknown", "off_topic"):
        return Command(update=update, goto="next_question")
    if assessment.quality == "insufficient" and state["follow_up_count"] < state["max_follow_ups"]:
        return Command(update=update, goto="follow_up")
    return Command(update=update, goto="next_question")


def follow_up(state: InterviewState) -> dict:
    """N7 追问：基于薄弱点生成追问问题。"""
    answer = state["messages"][-1]["content"]
    question = llm.generate_followup(state["current_question"], answer, state["knowledge_points"])
    return {
        "current_question": question,
        "follow_up_count": state["follow_up_count"] + 1,
        "messages": [{"role": "assistant", "content": question}],
    }


def next_question(state: InterviewState) -> Command:
    """N8 换题：重置追问计数；上下文溢出则先压缩，主问题数达到知识点数时收尾。

    主问题轮数 = plan_focus 返回的知识点数量（由提示词控制，3~5 个）。
    路由约定（M6）：上下文空间溢出 → compress_history（压缩旧主题后继续）；
    未溢出且已问完 → final_report；未溢出且未问完 → generate_question。
    """
    update = {"follow_up_count": 0}
    if _history_over_budget(state["messages"]):
        return Command(update=update, goto="compress_history")
    if state["main_question_count"] >= len(state["focus_points"]):
        return Command(update=update, goto="final_report")
    return Command(update=update, goto="generate_question")


def compress_history(state: InterviewState) -> Command:
    """M6 历史摘要压缩：上下文溢出时，把旧主题压缩进 running summary 并裁剪 messages。

    - 依据 topic_start 划分：messages[:topic_start] 为已完成旧主题，压缩进 conversation_summary；
      messages[topic_start:] 为最近主题，原样保留（供 final_report 引用准确原文）。
    - 兜底：若 topic_start == 0（单个主题即超预算），整体压缩并清空 messages。
    - 压缩后按是否问完路由：已问完 → final_report；未问完 → generate_question。
    """
    start = state.get("topic_start", 0)
    if start > 0:
        old = state["messages"][:start]
        keep = state["messages"][start:]
    else:
        old = state["messages"]
        keep = []
    summary = llm.compress_history(state.get("conversation_summary", ""), old)
    update = {
        "conversation_summary": summary,
        "messages": {"__replace__": keep},
        "topic_start": 0,
    }
    if state["main_question_count"] >= len(state["focus_points"]):
        return Command(update=update, goto="final_report")
    return Command(update=update, goto="generate_question")


def final_report(state: InterviewState) -> dict:
    """N10 终局诊断报告：汇总全场生成 FinalReport。"""
    report = llm.final_report(
        state["messages"],
        state["assessments"],
        state["session_id"],
        state.get("conversation_summary", ""),
    )
    return {"final_report": report.model_dump()}



