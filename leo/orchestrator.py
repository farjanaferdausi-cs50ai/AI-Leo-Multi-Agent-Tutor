"""Sequential orchestration of the four Leo agents with explicit, typed handoffs."""
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Callable

from crewai import Agent, Crew, LLM, Process, Task
from dotenv import load_dotenv

from leo.prompts import ROLES, TASKS
from leo.schemas import Evaluation, Plan, Quiz

load_dotenv()
log = logging.getLogger("leo")
PASS_MARK = 60.0
Step = Callable[[str, str], None]
JSON_RULE = " Reply with ONLY one valid JSON object that matches this JSON schema, no markdown fences: {schema}"


VAGUE = re.compile(
    r"^(please )?(teach|tell|show|help|explain)( me)?( something| anything| stuff| a topic)?$"
    r"|^(something|anything|stuff|random|surprise me|i want to learn( something)?|learn something|hi|hello|hey)$")
CLARIFY = ("Which subject would you like to learn? For example: 'What is machine learning?', "
           "'How does a decision tree work?' or 'How does backpropagation work?'")


def is_vague(text: str) -> bool:
    """The Coordinator's first check: a request with no subject is never guessed."""
    t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", "", text.lower())).strip()
    return not t or bool(VAGUE.match(t))


class LeoError(Exception):
    """Raised after the Coordinator has exhausted its retries."""


@dataclass
class Lesson:
    plan: Plan
    text: str
    quiz: Quiz


def _llm(key: str = "", fallback: bool = False) -> LLM:
    """I give the light Coordinator task a faster model and the heavy tasks the main model.

    When the main model is overloaded, the Coordinator retries on the fallback model.
    """
    fast = os.getenv("LEO_FAST_MODEL", "gemini/gemini-3.1-flash-lite")
    main = os.getenv("LEO_MODEL", "gemini/gemini-3.5-flash")
    backup = os.getenv("LEO_FALLBACK_MODEL", "gemini/gemini-3.1-flash-lite")
    return LLM(
        model=backup if fallback else (fast if key == "coordinator" else main),
        api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.4,
    )


def _agent(key: str, fallback: bool = False) -> Agent:
    spec = ROLES[key]
    return Agent(role=spec["role"], goal=spec["goal"], backstory=spec["backstory"],
                 llm=_llm(key, fallback), allow_delegation=False, verbose=False,
                 max_execution_time=120, max_retry_limit=0)


def parse_model(model, raw: str):
    """I extract the JSON object from the raw reply and validate it against the schema."""
    text = raw.strip().replace("```json", "").replace("```", "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in agent reply")
    return model.model_validate_json(text[start:end + 1])


def _run(key: str, inputs: dict, model=None, fallback: bool = False):
    """I run one agent as one task in its own sequential Crew and return the typed result."""
    agent = _agent(key, fallback)
    description = TASKS[key]
    if model:
        # I ask for pure JSON and validate it with Pydantic myself, which is more reliable.
        inputs = {**inputs, "schema": json.dumps(model.model_json_schema())}
        description += JSON_RULE
    task = Task(description=description, expected_output="Follow the instructions exactly.", agent=agent)
    out = Crew(agents=[agent], tasks=[task], process=Process.sequential,
               verbose=False).kickoff(inputs=inputs)
    return parse_model(model, out.raw) if model else out.raw


class _Notify:
    """I wrap the UI callback so CrewAI can call it when the Explainer task finishes."""

    def __init__(self, fn: Callable[[], None]) -> None:
        self.fn = fn

    def __call__(self, _output) -> None:
        self.fn()


def _run_lesson(inputs: dict, on_quiz_start: Callable[[], None], fallback: bool = False) -> tuple[str, Quiz]:
    """Explainer and Quiz Master run in ONE sequential Crew.

    The lesson reaches the quiz task through CrewAI task context, a real handoff.
    """
    explainer, quiz_master = _agent("explainer", fallback), _agent("quiz_master", fallback)
    explain = Task(description=TASKS["explainer"], expected_output="A Markdown lesson.",
                   agent=explainer, callback=_Notify(on_quiz_start))
    quiz = Task(description=TASKS["quiz_master"] + JSON_RULE, expected_output="A JSON quiz.",
                agent=quiz_master, context=[explain])
    out = Crew(agents=[explainer, quiz_master], tasks=[explain, quiz], process=Process.sequential,
               verbose=False).kickoff(inputs={**inputs, "schema": json.dumps(Quiz.model_json_schema())})
    return out.tasks_output[0].raw, parse_model(Quiz, out.tasks_output[1].raw)


TRANSIENT = ("503", "UNAVAILABLE", "overloaded", "high demand", "timed out", "timeout")


def _retry(label: str, fn: Callable[[bool], object], attempts: int = 3):
    """The Coordinator retries stalled agents, switches to a fallback model when the
    main model is overloaded, and fails fast on quota errors."""
    last: Exception | None = None
    fallback = False
    for i in range(1, attempts + 1):
        try:
            result = fn(fallback)
            if result is None:
                raise ValueError("empty structured output")
            return result
        except Exception as exc:  # noqa: BLE001 - intentional broad guard
            last = exc
            msg = str(exc)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                raise LeoError("Gemini quota exceeded (429). Wait a minute, check the quota in Google AI Studio, "
                               "or set LEO_MODEL in .env to a model that still has quota.") from exc
            transient = any(t.lower() in msg.lower() for t in TRANSIENT)
            if transient:
                fallback = True
            log.warning("%s failed (attempt %d/%d, fallback next=%s): %s", label, i, attempts, fallback, exc)
            time.sleep((6 if transient else 4) * i)
    raise LeoError(f"The {label} could not finish. Google's model is busy, please press the button again in a minute. "
                   f"Last error: {str(last)[:200]}")


def _guarded(key: str, inputs: dict, model=None):
    return _retry(ROLES[key]["role"], lambda fb: _run(key, inputs, model, fb))


def score_answers(quiz: Quiz, answers: list[int]) -> float:
    """Deterministic scoring tool used before the Evaluator writes feedback."""
    right = sum(a == q.answer_index for q, a in zip(quiz.questions, answers))
    return 100.0 * right / max(len(quiz.questions), 1)


def describe_answers(quiz: Quiz, answers: list[int]) -> str:
    """I turn option indexes into readable text so the Evaluator never mentions indexes."""
    lines = []
    for i, (q, a) in enumerate(zip(quiz.questions, answers), 1):
        verdict = "correct" if a == q.answer_index else "wrong"
        lines.append(f"Q{i}: student chose '{q.options[a]}' ({verdict}); correct answer: '{q.options[q.answer_index]}'")
    return " | ".join(lines)


def teach(name: str, topic: str, level: str, history: str = "none",
          weak: str = "", n: int = 4, kb=None, on_step: Step | None = None) -> Lesson | str:
    """Phase 1: Coordinator, then Explainer and Quiz Master. Returns a clarification str if unclear."""
    step = on_step or (lambda *_: None)
    step("Coordinator", "Validating request and planning")
    if is_vague(topic):
        return CLARIFY
    plan: Plan = _guarded("coordinator", dict(name=name, topic=topic, level=level, history=history), Plan)
    if not plan.is_clear:
        return plan.clarification or "Could you tell me a little more about the topic?"
    context = (kb.retrieve(plan.topic) if kb else "") or "none"
    step("Explainer", "Writing the lesson")
    inputs = dict(name=name, level=plan.level, plan=plan.model_dump_json(), weak=weak or "none",
                  context=context, topic=plan.topic, n=n)
    text, quiz = _retry("Explainer and Quiz Master", lambda fb: _run_lesson(
        inputs, lambda: step("Quiz Master", "Building the quiz"), fb))
    return Lesson(plan, text, quiz)


def evaluate(name: str, quiz: Quiz, answers: list[int],
             on_step: Step | None = None) -> tuple[float, Evaluation]:
    """Phase 2: Evaluator grades the answers that the Quiz Master handed over."""
    (on_step or (lambda *_: None))("Evaluator", "Checking answers")
    score = score_answers(quiz, answers)
    ev = _guarded("evaluator", dict(name=name, quiz=quiz.model_dump_json(),
                                    answers=describe_answers(quiz, answers), score=round(score)), Evaluation)
    return score, ev
