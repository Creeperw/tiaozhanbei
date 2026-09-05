from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class PromptSkill:
    skill_id: str
    version: str
    agent: str
    task_type: str
    instructions: str

    def as_model_input(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "agent": self.agent,
            "task_type": self.task_type,
            "instructions": self.instructions,
        }


class PromptSkillRegistry:
    """Load only approved, task-specific prompt skills from the package directory."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path(__file__).resolve().parents[1] / "prompt_skills").resolve()

    def load(self, agent: str, task_type: str) -> PromptSkill:
        relative = Path(agent) / f"{task_type}.md"
        path = (self.root / relative).resolve()
        if self.root not in path.parents or not path.is_file():
            raise KeyError(f"prompt skill is not registered: {agent}.{task_type}")
        metadata, instructions = self._parse(path.read_text(encoding="utf-8"))
        expected = {"agent": agent, "task_type": task_type}
        for key, value in expected.items():
            if metadata.get(key) != value:
                raise ValueError(f"prompt skill metadata mismatch: {key}")
        skill_id = metadata.get("skill_id", "").strip()
        version = metadata.get("version", "").strip()
        if not skill_id or not version or not instructions.strip():
            raise ValueError(f"prompt skill is incomplete: {relative}")
        return PromptSkill(
            skill_id=skill_id,
            version=version,
            agent=agent,
            task_type=task_type,
            instructions=instructions.strip(),
        )

    def load_many(self, requests: Iterable[tuple[str, str]]) -> list[PromptSkill]:
        return [self.load(agent, task_type) for agent, task_type in requests]

    @staticmethod
    def _parse(value: str) -> tuple[dict[str, str], str]:
        if not value.startswith("---\n"):
            raise ValueError("prompt skill must start with metadata")
        try:
            raw_metadata, body = value[4:].split("\n---\n", 1)
        except ValueError as exc:
            raise ValueError("prompt skill metadata is not closed") from exc
        metadata: dict[str, str] = {}
        for line in raw_metadata.splitlines():
            if not line.strip():
                continue
            key, separator, raw_value = line.partition(":")
            if not separator:
                raise ValueError("prompt skill metadata line must use key: value")
            metadata[key.strip()] = raw_value.strip()
        return metadata, body


prompt_skill_registry = PromptSkillRegistry()
