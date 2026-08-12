from langchain_openai import ChatOpenAI

from .config import settings
from .schemas import AnswerAssessment, FinalReport, FocusPlan, QuestionPlan, WrongQuestion


def _chat(temperature: float = 0.3, max_tokens: int = 1024) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort="none",  # 关闭 thinking 模式，使 function_calling 可用
    )


# ---------------- public API ----------------

def summarize(resume: str, job_description: str) -> str:
    # tags 标记：astream_events 据此把该调用产出为 token 流式事件（stream: 前缀）。
    llm = _chat(temperature=0.3, max_tokens=600).with_config({"tags": ["stream:summarize"]})
    out = llm.invoke(
        "你是资深技术面试官。请基于以下简历和岗位，提炼候选人的核心技术栈、项目经历与年限，"
        "输出一段简洁的简历摘要（200字以内）。\n\n岗位：" + job_description + "\n\n简历：\n" + resume
    )
    return out.content.strip()


def plan_focus(resume_summary: str, job_description: str) -> list:
    plan: FocusPlan = _chat().with_structured_output(FocusPlan, method="function_calling").invoke(
        "基于候选人的简历摘要与目标岗位，规划面试要考察的核心技术知识点"
        "（主问题数量 = 知识点数量，想多问就多给几个知识点，建议 3~5 个），"
        "按重要度给出 skill 与 weight(0~1)。\n\n岗位：" + job_description + "\n\n简历摘要：\n" + resume_summary
    )
    return [p.model_dump() for p in plan.points]


def generate_question(focus: dict, resume_summary: str, job_description: str, reference_docs: list) -> QuestionPlan:
    refs_text = "\n\n".join(
        f"问题：{d.get('question', '')}\n答案：{d.get('answer', '')}"
        for d in reference_docs
    )
    qplan: QuestionPlan = _chat().with_structured_output(QuestionPlan, method="function_calling").invoke(
        f"基于知识点「{focus['skill']}」出一道面试主问题，并列出本题应覆盖的 knowledge_points（考察点）。\n"
        f"以下是从知识库检索到的相关问答对，作为出题蓝本：\n{refs_text}\n"
        "要求：题目与知识库问题相关，可参考或改编，但不要直接照抄问题文本；结合候选人背景更有针对性。\n"
        f"岗位：{job_description}\n简历摘要：{resume_summary}"
    )
    return qplan


def evaluate(
    question: str,
    answer: str,
    resume_summary: str,
    question_id: int,
    reference_docs: list,
) -> AnswerAssessment:
    snippets_text = "\n\n".join(
        f"【参考{idx + 1}】{s.get('question', '')}\n{s.get('answer', '')}"
        for idx, s in enumerate(reference_docs)
    )
    assessment: AnswerAssessment = _chat().with_structured_output(AnswerAssessment, method="function_calling").invoke(
        f"判断候选人回答是否充分。\n\n问题：{question}\n\n回答：{answer}\n\n简历摘要：{resume_summary}\n\n"
        "quality 取值：sufficient(充分) / insufficient(不充分) / unknown(不知道或敷衍) / off_topic(答非所问)。\n"
        "coverage 为 0~1 的小数，表示回答覆盖参考要点的比例（无参考要点时可给 0.5）。"
        "missing_keys 列出未覆盖的考察点，wrong_points 列出具体错误点，note 一句话说明。\n\n"
        f"检索到的参考要点：\n{snippets_text}"
    )
    assessment.question_id = question_id
    return assessment


def generate_followup(question: str, answer: str, knowledge_points: list) -> str:
    llm = _chat(temperature=0.3, max_tokens=400).with_config({"tags": ["stream:follow_up"]})
    out = llm.invoke(
        f"基于以下回答的薄弱点，追问一个具体问题（围绕考察点：{knowledge_points}）。只输出追问问题本身。\n\n原问题：{question}\n回答：{answer}"
    )
    return out.content.strip()


def compress_history(previous_summary: str, recent_messages: list) -> str:
    """M6 历史摘要压缩：把刚结束主题的对话增量合并进已有摘要。

    输入：上一轮的 running summary + 新压缩的主题对话（可能含一道或多道题目）；
    输出：合并后的新摘要（纯文本，覆盖式写入 state.conversation_summary）。
    """
    recent_text = "\n".join(f"{m['role']}: {m['content']}" for m in recent_messages)
    llm = _chat(temperature=0.3, max_tokens=600).with_config({"tags": ["stream:compress_history"]})
    out = llm.invoke(
        "你是面试记录员。请把「上一轮历史摘要」与「新压缩的主题对话」增量合并，"
        "输出一份新的历史摘要（400字以内）。\n"
        "新压缩的主题对话可能包含一道或多道题目；必须保留：已考察的知识点与对应题目、"
        "候选人回答的核心内容与质量结论（含追问情况）、整体表现趋势。\n"
        "不要遗漏上一轮摘要中仍重要的信息，不要添加未出现的内容。\n\n"
        f"上一轮历史摘要：\n{previous_summary or '（无）'}\n\n"
        f"新压缩的主题对话：\n{recent_text}"
    )
    return out.content.strip()


def final_report(
    messages: list, assessments: list, session_id: str, summary: str = ""
) -> FinalReport:
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    # 自适应描述：全程未压缩 → 完整对话；中途压缩过 → 历史摘要 + 最近主题完整对话。
    if summary.strip():
        sections = (
            "历史对话摘要：\n" + summary + "\n\n"
            "最近主题完整对话：\n" + (transcript or "（无）")
        )
        desc = (
            "面试记录由两部分组成：历史对话摘要（较早题目与回答的压缩记录）与最近主题完整对话（原文）。"
            "wrong_questions 的 question_text / user_answer 优先取自最近主题完整对话，较早题目以摘要与评估记录为准。"
        )
    else:
        sections = "完整对话记录：\n" + (transcript or "（无）")
        desc = (
            "本次面试未触发历史压缩，以下为完整对话记录（覆盖全部题目与回答），"
            "wrong_questions 的 question_text / user_answer 直接从对话原文提取。"
        )
    report: FinalReport = _chat(temperature=0.2, max_tokens=16000).with_structured_output(FinalReport, method="function_calling").invoke(
        f"根据面试记录与每题评估，生成最终诊断报告。"
        f"不要给数值分数，只给出整体结论 summary，并把每题的问题整理进 wrong_questions。"
        f"kind 取值：wrong(答错) / unknown(未作答或敷衍) / off_topic(答非所问)。\n"
        f"说明：{desc}\n\n"
        f"session_id：{session_id}\n\n"
        f"{sections}\n\n"
        f"评估记录：\n{assessments}"
    )
    report.session_id = session_id
    return report
