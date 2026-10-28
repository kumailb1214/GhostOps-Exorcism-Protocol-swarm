"""The single pipeline: one CrewAI Flow per tick.

get_tick → validate → specialists per sector (budget-bounded waves) → risk gate →
[challenge round] → commander → submit → dispatch → remember.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from crewai.flow.flow import Flow, listen, router, start
from pydantic import BaseModel, Field

from eval.memory import SwarmMemory
from partners.base import PartnerClient
from schemas import SEVERITY, DecisionRecord, Role, SectorDecision, SpecialistReport, Tick
from swarm.agents import Swarm
from swarm.budget import peak_active, plan_waves
from swarm.challenge import challenge_prompt, civilians_at_risk, needs_challenge, pick_dissenter

SPECIALIST_ROLES = (Role.SENSOR, Role.RESEARCHER, Role.CIVILIAN)


class FlowState(BaseModel):
    tick: Tick | None = None
    reports: dict[str, list[SpecialistReport]] = Field(default_factory=dict)
    challenge_reasons: dict[str, str] = Field(default_factory=dict)
    decisions: list[SectorDecision] = Field(default_factory=list)
    agents_used: int = 0
    started: float = 0.0


class ContainmentFlow(Flow[FlowState]):
    def __init__(self, partners: PartnerClient, memory: SwarmMemory, *, team: str,
                 challenge_enabled: bool = True, submit: bool = True, dispatch: bool = True,
                 tick: Tick | None = None) -> None:
        super().__init__()
        self.p, self.memory, self.team = partners, memory, team
        self.challenge_enabled, self.do_submit, self.do_dispatch = challenge_enabled, submit, dispatch
        self._preloaded = tick
        self.swarm = Swarm(partners, memory)
        self.record: DecisionRecord | None = None

    # 1 ------------------------------------------------------------------
    @start()
    def pull_tick(self) -> Tick:
        self.state.started = time.perf_counter()
        tick = self._preloaded or self.p.get_tick()
        if not tick.sector_reports:
            raise ValueError("tick has no sector_reports")
        self.state.tick = tick
        return tick

    # 2 ------------------------------------------------------------------
    @listen(pull_tick)
    def assess(self, tick: Tick) -> dict[str, list[SpecialistReport]]:
        waves = plan_waves(len(tick.sector_reports), tick.swarm_budget.max_active_agents)
        self.state.agents_used = peak_active(waves, tick.swarm_budget.max_active_agents)
        serial = tick.swarm_budget.max_active_agents < len(SPECIALIST_ROLES)
        for wave in waves:
            jobs = [(tick.sector_reports[i], role) for i in wave for role in SPECIALIST_ROLES]
            if serial:
                results = [self.swarm.specialist(role, rep) for rep, role in jobs]
            else:
                with ThreadPoolExecutor(max_workers=len(jobs)) as ex:
                    results = list(ex.map(lambda j: self.swarm.specialist(j[1], j[0]), jobs))
            for r in results:
                self.state.reports.setdefault(r.sector, []).append(r)
        return self.state.reports

    # 3 ------------------------------------------------------------------
    @router(assess)
    def risk_gate(self, reports: dict[str, list[SpecialistReport]]) -> str:
        if not self.challenge_enabled:
            return "fast"
        tick = self.state.tick
        assert tick
        for rep in tick.sector_reports:
            flag, why = needs_challenge(reports[rep.sector], rep.civilian_status)
            if flag:
                self.state.challenge_reasons[rep.sector] = why
        return "challenge" if self.state.challenge_reasons else "fast"

    # 3b -----------------------------------------------------------------
    @listen("challenge")
    def challenge_round(self) -> str:
        tick = self.state.tick
        assert tick

        def rechallenge(rep):
            reports = self.state.reports[rep.sector]
            dissenter = pick_dissenter(reports)
            prompt = challenge_prompt(dissenter, [r for r in reports if r is not dissenter])
            return rep.sector, self.swarm.specialist(dissenter.role, rep, challenge=prompt)

        flagged = [rep for rep in tick.sector_reports if rep.sector in self.state.challenge_reasons]
        # One challenged agent per flagged sector, in parallel, never more than the budget allows.
        for sector, revised in self._parallel(rechallenge, flagged, tick.swarm_budget.max_active_agents):
            self.state.reports[sector].append(revised)  # keep the original: the commander sees the change of mind
        return "decide"

    def _parallel(self, fn, items, max_active: int):
        if not items:
            return []
        if max_active < 2 or len(items) == 1:
            return [fn(i) for i in items]
        with ThreadPoolExecutor(max_workers=min(len(items), max_active)) as ex:
            return list(ex.map(fn, items))

    # 4 ------------------------------------------------------------------
    @listen("fast")
    def decide_fast(self) -> list[SectorDecision]:
        return self._decide()

    @listen(challenge_round)
    def decide_after_challenge(self) -> list[SectorDecision]:
        return self._decide()

    def _decide(self) -> list[SectorDecision]:
        tick = self.state.tick
        assert tick

        def command(rep):
            # Commander reasons over the latest word from each role.
            latest: dict[Role, SpecialistReport] = {}
            for r in self.state.reports[rep.sector]:
                latest[r.role] = r
            out = self.swarm.command(rep.sector, list(latest.values()))
            decision = SectorDecision(sector=rep.sector, challenged=rep.sector in self.state.challenge_reasons, **out.model_dump())
            self._apply_floors(decision, latest, rep.civilian_status)
            return decision

        # One commander per sector, in parallel within the budget; order of decisions follows the tick.
        self.state.decisions.extend(self._parallel(command, list(tick.sector_reports), tick.swarm_budget.max_active_agents))
        return self.state.decisions

    @staticmethod
    def _apply_floors(d: SectorDecision, latest: dict[Role, SpecialistReport], civilian_status: str) -> None:
        """Two auditable, rule-based floors under the commander. They exist because a false alarm on a
        control tick and an un-evacuated pinned civilian are the two most expensive scored errors."""
        recs = {r.recommended_action for r in latest.values()}
        civ = latest.get(Role.CIVILIAN)
        if recs == {"observe"} and d.action != "observe":
            d.floor_applied = f"commander said {d.action} against unanimous observe → observe"
            d.action = "observe"
        elif civ and civ.recommended_action == "evacuate" and civilians_at_risk(civilian_status) and d.action != "evacuate":
            d.floor_applied = f"civilians at risk ({civilian_status}) and safety officer says evacuate → evacuate"
            d.action = "evacuate"

    # 5 ------------------------------------------------------------------
    @listen(decide_fast)
    def act_fast(self, decisions: list[SectorDecision]) -> DecisionRecord:
        return self._act(decisions)

    @listen(decide_after_challenge)
    def act_after_challenge(self, decisions: list[SectorDecision]) -> DecisionRecord:
        return self._act(decisions)

    def _act(self, decisions: list[SectorDecision]) -> DecisionRecord:
        tick = self.state.tick
        assert tick
        # One action per tick: the most severe sector decision wins.
        action = max((d.action for d in decisions), key=lambda a: SEVERITY[a])
        record = DecisionRecord(
            tick_id=tick.tick_id, facility_id=tick.facility_id, action=action, sectors=decisions,
            reports=[r for rs in self.state.reports.values() for r in rs],
            route="challenge" if self.state.challenge_reasons else "fast",
            agents_used=self.state.agents_used + 1,
        )
        if self.do_submit:
            j = self.p.submit(self.team, tick.tick_id, action)
            record.submission = j.model_dump()
        # decision time = tick pulled → containment submitted; the dispatch itself is not counted against us
        record.timing_ms = int((time.perf_counter() - self.state.started) * 1000)
        record.partner_calls = dict(self.p.calls)
        if self.do_dispatch and action != "observe":
            record.dispatch = self.p.dispatch(self.escalation_text(record))
        for d in decisions:
            self.memory.remember(d.sector, tick.tick_id, d, record.submission)
        self.record = record
        return record

    # --------------------------------------------------------------------
    def escalation_text(self, record: DecisionRecord) -> str:
        """Judge-readable Discord post: headline, one-line why, then the numbers the track scores on."""
        worst = max(record.sectors, key=lambda d: SEVERITY[d.action])
        why = worst.rationale.strip().split(". ")[0].rstrip(".")
        lines = [
            f"**{self.team}** | {record.facility_id} | tick {record.tick_id} | **{record.action.upper()}**",
            f"{worst.sector}: {why}.",
        ]
        if record.submission:
            lines.append(f"Score: {record.submission.get('score')}; false alarm: {record.submission.get('false_alarm')}.")
        route = f"risk gate: {self.state.challenge_reasons[worst.sector]}" if worst.sector in self.state.challenge_reasons else "risk gate: not triggered"
        lines.append(f"Peak agents: {record.agents_used}; decision time: {record.timing_ms / 1000:.1f}s; {route}.")
        if worst.floor_applied:
            lines.append(f"Safety floor: {worst.floor_applied}.")
        others = [d for d in record.sectors if d.sector != worst.sector]
        if others:
            lines.append("Other sectors: " + ", ".join(f"{d.sector} → {d.action}" for d in others) + ".")
        return "\n".join(lines)


def run_tick(partners: PartnerClient, memory: SwarmMemory, **kw: Any) -> DecisionRecord:
    flow = ContainmentFlow(partners, memory, **kw)
    flow.kickoff()
    assert flow.record is not None
    return flow.record
