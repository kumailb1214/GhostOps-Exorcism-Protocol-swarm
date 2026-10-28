"""Deterministic triage: how much swarm a sector gets before any model is called.

Spending three agents on a quiet sector costs time and credits and buys nothing. Triage scores each sector from
the same signals the referee sees, orders sectors by that score (the inspection order), and classifies them:

  full   — the full 3-specialist panel up front (hot: civilians at risk, breach, or a strong multi-signal signature)
  quiet  — one sensor analyst inspects; if it recommends anything but observe, the sector is ESCALATED to the
           full panel in the refine stage. A quiet sector is never decided without an agent reading it.

Triage decides agent allocation only. It never decides the action.
"""
from __future__ import annotations

import os
from typing import Literal

from schemas import SectorReport

Panel = Literal["full", "quiet"]

HOT_THRESHOLD = 6
LEAN_CAP = int(os.environ.get("GHOSTOPS_MAX_AGENTS", "4"))  # self-imposed concurrency cap, under the feed's budget


def severity(rep: SectorReport) -> int:
    s = rep.sensors
    score = 0
    score += 3 if s.emf_milligauss >= 80 else 2 if s.emf_milligauss >= 30 else 1 if s.emf_milligauss >= 10 else 0
    score += 3 if s.temp_c <= 0 else 2 if s.temp_c <= 10 else 1 if s.temp_c <= 15 else 0
    score += 1 if s.motion_events >= 20 and s.emf_milligauss >= 10 else 0
    g = rep.sighting
    if g.entity_class not in ("none", "human-crowd", ""):
        score += 3 if g.confidence >= 0.7 else 2 if g.confidence >= 0.4 else 1
        score += 1 if g.witness_count >= 2 else 0
    civ = rep.civilian_status.lower()
    if civ != "clear":
        score += 6 if any(k in civ for k in ("pinned", "trapped", "injured", "missing")) else 3
    cont = rep.containment_state.lower()
    if cont not in ("stable", "holding"):
        score += 3 if "breach" in cont else 2
    return score


def classify(rep: SectorReport) -> Panel:
    return "full" if severity(rep) >= HOT_THRESHOLD else "quiet"


def inspection_order(reports: list[SectorReport]) -> list[SectorReport]:
    return sorted(reports, key=lambda r: -severity(r))


def effective_cap(budget_max_active: int) -> int:
    return max(1, min(budget_max_active, LEAN_CAP))


def pack(jobs: list[tuple[str, int]], cap: int) -> list[list[str]]:
    """First-fit packing of (job_id, cost) into waves whose total cost ≤ cap, keeping the given priority order."""
    waves: list[list] = []  # [[job_ids], used]
    for job, cost in jobs:
        cost = min(cost, cap)
        for wave in waves:
            if wave[1] + cost <= cap:
                wave[0].append(job)
                wave[1] += cost
                break
        else:
            waves.append([[job], cost])
    return [w[0] for w in waves]
