"""Per-sector swarm memory with provenance. Observed facts, retrieved references and agent hypotheses
are stored separately so a vaguely similar past incident can never masquerade as a current observation.
Similarity recall uses Parasail embeddings (or the mock's hashed vectors)."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from partners.base import PartnerClient
from schemas import SectorDecision


def _cos(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0
    return num / den


class SwarmMemory:
    def __init__(self, partners: PartnerClient, max_per_sector: int = 20) -> None:
        self.p = partners
        self.max = max_per_sector
        self._items: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._lore: list[tuple[str, list[float]]] = []

    def remember(self, sector: str, tick_id: int, decision: SectorDecision, judgment: dict | None) -> None:
        entry = {
            "tick_id": tick_id,
            "action": decision.action,
            "observed": decision.supporting_observations,
            "hypothesized": [decision.rationale],
            "judgment": {k: judgment.get(k) for k in ("expected_action", "score", "false_alarm")} if judgment else None,
        }
        bucket = self._items[sector]
        bucket.append(entry)
        del bucket[: max(0, len(bucket) - self.max)]

    def recall(self, sector: str, n: int = 3) -> list[dict[str, Any]]:
        return self._items[sector][-n:]

    # --- lore index (optional curated signatures) ----------------------
    def index_lore(self, docs: list[str]) -> None:
        vecs = self.p.embed(docs)
        self._lore.extend(zip(docs, vecs))

    def nearest_lore(self, query: str, k: int = 2) -> list[dict[str, Any]]:
        if not self._lore:
            return []
        q = self.p.embed([query])[0]
        scored = sorted(((_cos(q, v), d) for d, v in self._lore), reverse=True)[:k]
        return [{"text": d, "similarity": round(s, 3), "provenance": "retrieved"} for s, d in scored]
