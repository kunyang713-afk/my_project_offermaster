from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class FocusPoint(BaseModel):
    skill: str = Field(description="知识点/技能名")
    weight: float = Field(description="重要度 0~1")


class FocusPlan(BaseModel):
    points: List[FocusPoint]


class QuestionPlan(BaseModel):
    question_text: str = Field(description="主问题文本")
    knowledge_points: List[str] = Field(description="本题应覆盖的知识点")


class AnswerAssessment(BaseModel):
    question_id: int
    quality: Literal["sufficient", "insufficient", "unknown", "off_topic"]
    coverage: Optional[float] = None  # MVP 无 RAG 时为 None
    missing_keys: List[str] = []
    wrong_points: List[str] = []
    note: str = ""


class WrongQuestion(BaseModel):
    question_id: int
    question_text: str
    user_answer: str
    kind: Literal["wrong", "unknown", "off_topic"]
    missing_keys: List[str] = []
    wrong_points: List[str] = []
    suggestion: str = ""


class FinalReport(BaseModel):
    session_id: str
    summary: str
    wrong_questions: List[WrongQuestion]


class StartRequest(BaseModel):
    resume: str
    job_description: str
    max_follow_ups: int = 2


class AnswerRequest(BaseModel):
    turn_id: str
    answer: str

