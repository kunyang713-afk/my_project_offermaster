import operator
from typing import Annotated, List, Optional, TypedDict


def reduce_messages(current, incoming):
    """messages 的合并 reducer：默认追加，支持整体裁剪。

    - 节点返回普通 list → 追加到现有消息（与原 operator.add 行为一致）。
    - 节点返回 {"__replace__": [...]} → 整体替换（compress_history 裁剪历史用）。
    """
    current = current or []
    if isinstance(incoming, dict) and "__replace__" in incoming:
        return incoming["__replace__"]
    return current + list(incoming)


class InterviewState(TypedDict, total=False):
    """LangGraph 面试状态：贯穿整个面试流程的共享数据。

    说明：
    - 节点函数只读取本状态并返回“局部的增量更新”，由 LangGraph 合并回状态。
    - `Annotated[List, operator.add]` 表示该字段是“追加式”的：节点返回的新元素会被合并追加，
      而不是整体覆盖。
    - `total=False` 表示所有字段都是可选的，图中每个节点按需读写。
    """

    # ================= 会话基础 =================
    # 由 Java 在 start 请求时建立，贯穿一次面试的整个生命周期。

    session_id: str
    """会话唯一标识，等同 Java 侧的 session_id。用于 checkpoint 定位（thread_id）和终局报告关联。"""

    resume: str
    """候选人原始简历文本，由 Java 在 start 请求时传入。作为简历摘要（summarize）和出题的事实来源。"""

    resume_summary: str
    """简历摘要，由 N1 summarize 节点调用 LLM 生成。后续出题、评估、追问都以此作为候选人的背景参考。"""

    job_description: str
    """目标岗位 JD 文本，由 Java 在 start 请求时传入。用于规划考察重点（plan_focus）和让问题贴合岗位要求。"""

    focus_points: List[dict]
    """考察重点列表，由 N2 plan_focus 节点调用 LLM 规划，元素形如 {"skill": str, "weight": float}。
    按照重要度从高到低排序；generate_question 以 main_question_count 为游标按序取用。"""

    # ================= 当前题目与流转 =================
    # 描述“这道题问到哪了”，每轮由 generate_question / follow_up 更新。

    current_question: str
    """当前题目文本（主问题或追问），展示给候选人，并作为 evaluate 的评估对象。"""

    current_question_id: int
    """当前主问题的编号（从 1 递增）。每出一个主问题 +1，追问沿用同一个主问题编号，评估时用于把回答关联到具体题目。"""

    knowledge_points: List[str]
    """本题应覆盖的知识点列表，由 LLM 出题（generate_question）时生成。后续追问（follow_up）围绕这些知识点展开。"""

    follow_up_count: int
    """当前主问题已进行的追问次数。达到 max_follow_ups 后不再追问而是换题；换题节点（next_question）将其重置为 0。"""

    # ================= 对话记录与评分（自动追加）=================
    # 这两个列表使用追加式 reducer：节点只返回“新增元素”，LangGraph 自动合并。
    # messages 例外：compress_history 节点可返回 {"__replace__": [...]} 整体裁剪历史。

    messages: Annotated[List[dict], reduce_messages]
    """面试对话记录，元素形如 {"role": "assistant"|"user", "content": str}。
    出题/追问追加 assistant 消息，候选人回答追加 user 消息；终局报告（final_report）基于它生成完整诊断。
    M6：compress_history 节点在 messages 总字符数超出 history_max_chars 预算时触发，
    把 topic_start 之前的旧主题压缩进 conversation_summary 并整体裁剪，只保留最近主题窗口。"""

    topic_start: int
    """当前主主题在 messages 中的起始下标。generate_question 出题前写入（等于当时的 messages 长度），
    compress_history 据此确定「旧主题 = messages[:topic_start]、保留的最近主题 = messages[topic_start:]」。"""

    assessments: Annotated[List[dict], operator.add]
    """每题评估结果列表，元素为 AnswerAssessment 序列化字典（含 quality / missing_keys / wrong_points 等）。
    由 evaluate 节点追加；终局报告据此汇总出错误清单。"""

    conversation_summary: str
    """历史对话摘要（running summary），由 compress_history 节点增量合并生成。
    记录已考察知识点、题目、候选人回答质量要点；供 final_report 替代全量 transcript，
    避免上下文膨胀。与 messages 不同，本字段是覆盖式（最后一次写入生效）。"""

    # ================= 结束控制 =================
    # 主问题轮数由 plan_focus 返回的知识点数量决定（提示词控制 3~5 个）；
    # 追问次数上限由 Java 在 start 请求时传入。

    main_question_count: int
    """已出的主问题数（从 0 开始，每出 1 题 +1）。
    身兼两职：(1) 作为 focus_points 的游标，决定下一个考察点；(2) 供 next_question 判断是否问完所有知识点而收尾。"""

    max_follow_ups: int
    """每个主问题最多允许的追问次数。达到后即使回答仍不充分也换下一题。Java start 传入，默认 2。"""

    # ================= 检索与终局 =================
    # 阶段二（RAG / Milvus）与面试收尾相关的数据。

    reference_docs: List[dict]
    """当前主问题的知识库参考问答对，由 generate_question 按知识点检索填入。
    元素形如 {"answer", "question", "source", "topic", "qid", "score"}；
    供出题参考与 evaluate 对照，不参与最终报告。"""

    final_report: Optional[dict]
    """终局诊断报告，final_report 节点生成，为 FinalReport 的序列化字典。
    非空即代表面试已结束，对外接口据此返回 status=done。"""

