"""PartnerClient — the five functions the swarm depends on. B implements real; everyone else mocks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from typing import Any

from schemas import Judgment, Tick


class PartnerClient(ABC):
    """One object bundles every external dependency so the Flow stays a single pipeline."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    # --- scenario feed -------------------------------------------------
    @abstractmethod
    def get_tick(self) -> Tick: ...

    @abstractmethod
    def submit(self, team: str, tick_id: int, action: str) -> Judgment: ...

    # --- You.com grounding --------------------------------------------
    @abstractmethod
    def ground(self, query: str, count: int = 3) -> list[dict[str, str]]:
        """Return [{title, url, snippet}] for a natural-language query."""

    # --- Parasail -------------------------------------------------------
    @abstractmethod
    def llm(self) -> Any:
        """A CrewAI-compatible LLM object agents can think with."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    # --- One actuator ---------------------------------------------------
    @abstractmethod
    def dispatch(self, content: str) -> dict[str, Any]:
        """Post to #loft-incident. Returns {ok, method, detail}. Never raises silently."""

    def reset_counts(self) -> None:
        self.calls.clear()
