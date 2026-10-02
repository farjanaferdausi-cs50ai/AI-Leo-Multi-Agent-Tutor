import pytest
from pydantic import ValidationError

from leo.memory import StudentMemory
from leo.orchestrator import score_answers
from leo.schemas import Quiz, QuizQuestion


def _q(ans: int) -> QuizQuestion:
    return QuizQuestion(question="q", options=list("abcd"), answer_index=ans, explanation="e")


def test_scoring():
    quiz = Quiz(topic="t", questions=[_q(0), _q(1)])
    assert score_answers(quiz, [0, 2]) == 50.0


def test_option_validation():
    with pytest.raises(ValidationError):
        QuizQuestion(question="q", options=["a"], answer_index=0, explanation="e")


def test_memory_roundtrip(tmp_path):
    m = StudentMemory(name="Farjana")
    m.remember("RAG", 80)
    p = tmp_path / "m.json"
    m.save(p)
    assert StudentMemory.load(p).scores[0]["score"] == 80


def test_empty_knowledge_base():
    from leo.knowledge import KnowledgeBase
    assert KnowledgeBase().retrieve("anything") == ""


def test_parse_model_handles_fences():
    from leo.orchestrator import parse_model
    raw = '```json\n{"topic":"t","questions":[{"question":"q","options":["a","b","c","d"],"answer_index":1,"explanation":"e"}]}\n```'
    assert parse_model(Quiz, raw).questions[0].answer_index == 1
