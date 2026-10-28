"""Risk gate + one bounded challenge round.

The swarm does NOT debate every tick. It asks for one targeted second assessment only when:
  1. specialists disagree on the action,
  2. the researcher recommends acting (contain/evacuate) while admitting missing evidence, or
  3. someone recommends evacuate for a sector with no civilians at risk.
Model self-reported confidence is never a trigger on its own.
"""
from __future__ import annotations

import re

from schemas import SEVERITY, Role, SpecialistReport


def civilians_at_risk(civilian_status: str) -> bool:
    s = civilian_status.lower()
    return any(k in s for k in ("pinned", "trapped", "injured", "missing")) and bool(re.search(r"[1-9]", s))


def needs_challenge(reports: list[SpecialistReport], civilian_status: str) -> tuple[bool, str]:
    actions = {r.recommended_action for r in reports}
    researcher = next((r for r in reports if r.role == Role.RESEARCHER), None)
    if actions == {"observe"}:
        return False, ""  # unanimous observe: nothing to challenge, and challenging it only invites false alarms
    if len(actions) > 1:
        return True, f"specialists disagree ({', '.join(sorted(actions))})"
    if researcher and researcher.missing_evidence and researcher.recommended_action != "observe":
        return True, f"researcher wants {researcher.recommended_action} despite missing: {', '.join(researcher.missing_evidence[:2])}"
    if "evacuate" in actions and not civilians_at_risk(civilian_status):
        return True, "evacuate proposed with no civilians at risk"
    return False, ""


def pick_dissenter(reports: list[SpecialistReport]) -> SpecialistReport:
    """The specialist whose recommendation is furthest from the group's median severity."""
    sev = sorted(SEVERITY[r.recommended_action] for r in reports)
    median = sev[len(sev) // 2]
    return max(reports, key=lambda r: abs(SEVERITY[r.recommended_action] - median))


def challenge_prompt(dissenter: SpecialistReport, others: list[SpecialistReport]) -> str:
    peers = "; ".join(f"{o.role.value} recommends {o.recommended_action} because {o.finding}" for o in others)
    return (f"You recommended {dissenter.recommended_action}. Your peers: {peers}. "
            f"What concrete evidence supports your recommendation over theirs? If you cannot name any, revise it.")
