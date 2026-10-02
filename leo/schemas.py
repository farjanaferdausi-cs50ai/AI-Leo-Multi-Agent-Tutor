"""Typed contracts that agents use to hand work to each other."""
from pydantic import BaseModel, Field, field_validator


class Plan(BaseModel):
    """Coordinator output: a validated study plan or a clarification request."""
    is_clear: bool
    clarification: str = ""
    topic: str = ""
    level: str = "beginner"
    focus_points: list[str] = Field(default_factory=list)


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    answer_index: int
    explanation: str

    @field_validator("options")
    @classmethod
    def four_options(cls, v: list[str]) -> list[str]:
        if len(v) != 4:
            raise ValueError("Each question needs exactly 4 options")
        return v

    @field_validator("answer_index")
    @classmethod
    def valid_index(cls, v: int) -> int:
        if not 0 <= v <= 3:
            raise ValueError("answer_index must be between 0 and 3")
        return v


class Quiz(BaseModel):
    topic: str
    questions: list[QuizQuestion]


class QuestionFeedback(BaseModel):
    number: int
    correct: bool
    feedback: str


class Evaluation(BaseModel):
    summary: str
    per_question: list[QuestionFeedback]
    weak_topics: list[str] = Field(default_factory=list)
    encouragement: str
