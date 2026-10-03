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


def test_describe_answers_uses_text():
    from leo.orchestrator import describe_answers
    quiz = Quiz(topic="t", questions=[_q(1)])
    text = describe_answers(quiz, [0])
    assert "'a'" in text and "wrong" in text and "'b'" in text


def test_task_templates_have_known_placeholders():
    import re
    from leo.prompts import TASKS
    known = {"name", "topic", "level", "history", "plan", "weak", "context", "n", "quiz", "answers", "score", "schema"}
    for text in TASKS.values():
        assert set(re.findall(r"\{(\w+)\}", text)) <= known


def test_retry_switches_to_fallback_on_503(monkeypatch):
    import leo.orchestrator as o
    monkeypatch.setattr(o.time, "sleep", lambda s: None)
    seen = []

    def fn(fallback):
        seen.append(fallback)
        if not fallback:
            raise RuntimeError("503 UNAVAILABLE high demand")
        return "ok"

    assert o._retry("x", fn) == "ok"
    assert seen == [False, True]


def test_retry_fails_fast_on_quota(monkeypatch):
    import leo.orchestrator as o
    monkeypatch.setattr(o.time, "sleep", lambda s: None)
    calls = []

    def fn(fallback):
        calls.append(1)
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    with pytest.raises(o.LeoError):
        o._retry("x", fn)
    assert len(calls) == 1
