"""SHARED CONTRACT — every module imports from here. Change only with a group ping."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Action = Literal["contain", "evacuate", "observe"]
SEVERITY = {"observe": 0, "contain": 1, "evacuate": 2}


class Sensors(BaseModel):
    emf_milligauss: float = 0
    temp_c: float = 20
    motion_events: int = 0


class Sighting(BaseModel):
    entity_class: str = "none"
    confidence: float = 0.0
    witness_count: int = 0


class SectorReport(BaseModel):
    sector: str
    sensors: Sensors = Field(default_factory=Sensors)
    sighting: Sighting = Field(default_factory=Sighting)
    civilian_status: str = "clear"
    containment_state: str = "stable"


class SwarmBudget(BaseModel):
    max_active_agents: int = 8
    api_credits_remaining: int = 0


class EscalationChannel(BaseModel):
    app: str = "discord"
    channel: str = "#loft-incident"
    via: str = "one-mcp"


class Tick(BaseModel):
    """Exact input contract from the brief. Field names must not change."""

    tick_id: int
    facility_id: str
    sector_reports: list[SectorReport]
    swarm_budget: SwarmBudget = Field(default_factory=SwarmBudget)
    escalation_channel: EscalationChannel = Field(default_factory=EscalationChannel)


class Role(str, Enum):
    SENSOR = "sensor_analyst"
    RESEARCHER = "grounding_researcher"
    CIVILIAN = "civilian_safety"
    COMMANDER = "commander"


Provenance = Literal["observed", "retrieved", "hypothesized"]


class Evidence(BaseModel):
    claim: str
    provenance: Provenance
    source: str = ""


class SpecialistReport(BaseModel):
    role: Role
    sector: str
    finding: str
    evidence: list[Evidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_action: Action
    is_challenge_response: bool = False


class SectorDecision(BaseModel):
    sector: str
    action: Action
    rationale: str
    supporting_observations: list[str] = Field(default_factory=list)
    disagreement: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    rejected_alternatives: str = ""
    challenged: bool = False
    floor_applied: str = ""  # deterministic safety floor that overrode the commander, if any
    panel: Literal["full", "quiet", "escalated"] = "full"  # how much swarm triage gave this sector
    severity_score: int = 0
    settled_by: str = ""  # non-empty when the panel agreed (or a floor decided) and no commander round was needed


class DecisionRecord(BaseModel):
    tick_id: int
    facility_id: str
    action: Action
    sectors: list[SectorDecision]
    reports: list[SpecialistReport] = Field(default_factory=list)
    route: Literal["fast", "challenge"] = "fast"
    timing_ms: int = 0
    partner_calls: dict[str, int] = Field(default_factory=dict)
    agents_used: int = 0
    submission: dict[str, Any] | None = None
    dispatch: dict[str, Any] | None = None


class Judgment(BaseModel):
    """What the scenario feed returns after a containment submission."""

    expected_action: str | None = None
    score: float | None = None
    false_alarm: bool | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
