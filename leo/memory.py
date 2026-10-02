"""Tiny JSON-backed session memory: student, topics, and score history."""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class StudentMemory:
    name: str = "Student"
    topics: list[str] = field(default_factory=list)
    scores: list[dict] = field(default_factory=list)

    def remember(self, topic: str, score: float) -> None:
        if topic not in self.topics:
            self.topics.append(topic)
        self.scores.append({"topic": topic, "score": round(score, 1)})

    def history_text(self) -> str:
        return ", ".join(self.topics[-5:]) or "none"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> "StudentMemory":
        if path.exists():
            return cls(**json.loads(path.read_text()))
        return cls()
