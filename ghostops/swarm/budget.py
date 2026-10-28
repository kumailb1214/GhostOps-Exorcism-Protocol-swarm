"""Enforce swarm_budget.max_active_agents. Exceeding it is a scored failure, so we plan waves."""
from __future__ import annotations

SPECIALISTS_PER_SECTOR = 3  # sensor, researcher, civilian
COMMANDER = 1


class BudgetError(RuntimeError):
    pass


def plan_waves(n_sectors: int, max_active: int) -> list[list[int]]:
    """Group sector indices into waves whose concurrent specialist count fits the budget.

    The commander runs alone after all waves, so it never competes with specialists.
    """
    if max_active < 1:
        raise BudgetError("max_active_agents < 1 — cannot run any agent")
    if max_active < SPECIALISTS_PER_SECTOR:
        # Run specialists one at a time per sector (still three distinct agents, just serialized).
        return [[i] for i in range(n_sectors)]
    per_wave = max(1, max_active // SPECIALISTS_PER_SECTOR)
    return [list(range(i, min(i + per_wave, n_sectors))) for i in range(0, n_sectors, per_wave)]


def peak_active(waves: list[list[int]], max_active: int) -> int:
    widest = max((len(w) for w in waves), default=0)
    return min(widest * SPECIALISTS_PER_SECTOR, max_active) if widest else 0
