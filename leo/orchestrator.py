"""Sequential orchestration of the four Leo agents with explicit handoffs."""
import json
import logging
import os
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


class LeoError(Exception):
    """Raised after the Coordinator has exhausted its retries."""


@dataclass
class Lesson:
    plan: Plan
    text: str
    quiz: Quiz


def _llm(key: str = "") -> LLM:
    """I give the light Coordinator task a faster model and the heavy tasks the main model."""
    fast = os.getenv("LEO_FAST_MODEL", "gemini/gemini-3.1-flash-lite")
    main = os.getenv("LEO_MODEL", "gemini/gemini-3.5-flash")
    return LLM(
        model=fast if key == "coordinator" else main,
        api_key=os.getenv("GEMINI_API_KEY"),
        temperature=0.4,
    )


def _run(key: str, inputs: dict, model=None):
    """I build one agent and one task, run them, and return the typed result."""
    spec = ROLES[key]
    agent = Agent(role=spec["role"], goal=spec["goal"], backstory=spec["backstory"],
                  llm=_llm(key), allow_delegation=False, verbose=False,
                  max_execution_time=120, max_retry_limit=0)
    description = TASKS[key]
    if model:
        # I ask for pure JSON and validate it with Pydantic myself, which is more reliable.
        inputs = {**inputs, "schema": json.dumps(model.model_json_schema())}
        description += " Reply with ONLY one valid JSON object that matches this JSON schema, no markdown fences: {schema}"
    task = Task(description=description, expected_output="Follow the instructions exactly.", agent=agent)
    out = Crew(agents=[agent], tasks=[task], process=Process.sequential,
               verbose=False).kickoff(inputs=inputs)
    return parse_model(model, out.raw) if model else out.raw


def parse_model(model, raw: str):
    """I extract the JSON object from the raw reply and validate it against the schema."""
    text = raw.strip().replace("```json", "").replace("```", "")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in agent reply")
    return model.model_validate_json(text[start:end + 1])


def _guarded(key: str, inputs: dict, model=None, attempts: int = 3):
    """The Coordinator retries stalled agents with backoff before giving up."""
    last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            result = _run(key, inputs, model)
            if result is None:
                raise ValueError("empty structured output")
            return result
        except Exception as exc:  # noqa: BLE001 - intentional broad guard
            last = exc
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                raise LeoError("Gemini quota exceeded (429). Wait a minute, check the quota in Google AI Studio, or set LEO_MODEL in .env to a model that still has quota.") from exc
            log.warning("%s failed (attempt %d/%d): %s", key, i, attempts, exc, exc_info=True)
            time.sleep(4 * i)
    raise LeoError(f"The {ROLES[key]['role']} could not finish. Last error: {str(last)[:300]}")


def score_answers(quiz: Quiz, answers: list[int]) -> float:
    """Deterministic scoring tool used before the Evaluator writes feedback."""
    right = sum(a == q.answer_index for q, a in zip(quiz.questions, answers))
    return 100.0 * right / max(len(quiz.questions), 1)


def teach(name: str, topic: str, level: str, history: str = "none",
          weak: str = "", n: int = 4, kb=None, on_step: Step | None = None) -> Lesson | str:
    """Phase 1: Coordinator -> Explainer -> Quiz Master. Returns a clarification str if unclear."""
    step = on_step or (lambda *_: None)
    step("Coordinator", "Validating request and planning")
    plan: Plan = _guarded("coordinator", dict(name=name, topic=topic, level=level, history=history), Plan)
    if not plan.is_clear:
        return plan.clarification or "Could you tell me a little more about the topic?"
    context = (kb.retrieve(plan.topic) if kb else "") or "none"
    step("Explainer", "Writing the lesson")
    text = _guarded("explainer", dict(name=name, level=plan.level, plan=plan.model_dump_json(),
                                      weak=weak or "none", context=context))
    step("Quiz Master", "Building the quiz")
    quiz = _guarded("quiz_master", dict(name=name, level=plan.level, lesson=text,
                                        topic=plan.topic, n=n), Quiz)
    return Lesson(plan, text, quiz)


def evaluate(name: str, quiz: Quiz, answers: list[int],
             on_step: Step | None = None) -> tuple[float, Evaluation]:
    """Phase 2: Evaluator grades the answers that the Quiz Master handed over."""
    (on_step or (lambda *_: None))("Evaluator", "Checking answers")
    score = score_answers(quiz, answers)
    ev = _guarded("evaluator", dict(name=name, quiz=quiz.model_dump_json(),
                                    answers=str(answers), score=round(score)), Evaluation)
    return score, ev
