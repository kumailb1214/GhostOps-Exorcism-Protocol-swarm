"""The single pipeline: one CrewAI Flow per tick.

get_tick → validate → triage (inspection order, full panel vs single analyst) → specialists in budget-bounded
waves → risk gate → [refine: challenge round + escalation of quiet sectors that flagged] → commander → submit →
dispatch → remember.
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
from swarm.challenge import challenge_prompt, civilians_at_risk, needs_challenge, pick_dissenter
from swarm.triage import classify, effective_cap, inspection_order, pack, severity

SPECIALIST_ROLES = (Role.SENSOR, Role.RESEARCHER, Role.CIVILIAN)


class FlowState(BaseModel):
    tick: Tick | None = None
    reports: dict[str, list[SpecialistReport]] = Field(default_factory=dict)
    challenge_reasons: dict[str, str] = Field(default_factory=dict)
    panel: dict[str, str] = Field(default_factory=dict)  # sector → full | quiet | escalated
    order: list[str] = Field(default_factory=list)  # inspection order (most severe first)
    escalations: dict[str, str] = Field(default_factory=dict)  # quiet sector → why it got the full panel
    decisions: list[SectorDecision] = Field(default_factory=list)
    agents_used: int = 0  # PEAK concurrent agents in any stage
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
        cap = effective_cap(tick.swarm_budget.max_active_agents)
        ordered = inspection_order(tick.sector_reports)
        self.state.order = [r.sector for r in ordered]
        by_sector = {r.sector: r for r in ordered}
        jobs: list[tuple[str, int]] = []  # (sector:role, cost)
        for rep in ordered:
            panel = classify(rep)
            self.state.panel[rep.sector] = panel
            roles = SPECIALIST_ROLES if panel == "full" else (Role.SENSOR,)
            # a full panel is one job of cost 3 so its specialists always run together
            jobs.append((f"{rep.sector}|{','.join(r.value for r in roles)}", len(roles)))
        for wave in pack(jobs, cap):
            tasks = [(by_sector[j.split('|')[0]], Role(role)) for j in wave for role in j.split('|')[1].split(',')]
            for r in self._parallel(lambda t: self.swarm.specialist(t[1], t[0]), tasks, cap):
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
            if self.state.panel[rep.sector] == "quiet":
                sensor = reports[rep.sector][0]
                if sensor.recommended_action != "observe":  # the single analyst saw something: convene the panel
                    self.state.escalations[rep.sector] = f"sensor analyst recommends {sensor.recommended_action}"
                continue
            flag, why = needs_challenge(reports[rep.sector], rep.civilian_status)
            if flag:
                self.state.challenge_reasons[rep.sector] = why
        return "challenge" if (self.state.challenge_reasons or self.state.escalations) else "fast"

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

        def escalate(job):
            rep, role = job
            return rep.sector, self.swarm.specialist(role, rep)

        cap = effective_cap(tick.swarm_budget.max_active_agents)
        by_sector = {r.sector: r for r in tick.sector_reports}
        # Refine = one challenged agent per flagged sector + the two missing specialists for each escalated quiet
        # sector. All in one parallel stage, never more than the cap at once.
        tasks: list[tuple[Any, Any]] = []
        for sector in self.state.challenge_reasons:
            tasks.append((rechallenge, by_sector[sector]))
        for sector in self.state.escalations:
            self.state.panel[sector] = "escalated"
            for role in (Role.RESEARCHER, Role.CIVILIAN):
                tasks.append((escalate, (by_sector[sector], role)))
        for sector, report in self._parallel(lambda t: t[0](t[1]), tasks, cap):
            self.state.reports[sector].append(report)  # originals are kept: the commander sees the change of mind
        return "decide"

    def _parallel(self, fn, items, max_active: int):
        if not items:
            return []
        self.state.agents_used = max(self.state.agents_used, min(len(items), max_active))
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
            decision = SectorDecision(sector=rep.sector, challenged=rep.sector in self.state.challenge_reasons,
                                      panel=self.state.panel[rep.sector], severity_score=severity(rep), **out.model_dump())
            self._apply_floors(decision, latest, rep.civilian_status)
            return decision

        def quiet_decision(rep) -> SectorDecision:
            # A quiet sector whose analyst found nothing: observe, on the record, without convening a commander.
            sensor = self.state.reports[rep.sector][0]
            return SectorDecision(
                sector=rep.sector, action="observe", panel="quiet", severity_score=severity(rep),
                rationale=f"Triage scored this sector {severity(rep)} (below the full-panel threshold); the sensor analyst "
                          f"inspected it and recommends observe: {sensor.finding}",
                supporting_observations=[e.claim for e in sensor.evidence],
                missing_evidence=sensor.missing_evidence,
                rejected_alternatives="No panel convened: single-analyst inspection found no coherent signature, "
                                      "and triage saw no civilians at risk or breach.")

        ordered = [r for s in self.state.order for r in tick.sector_reports if r.sector == s]
        need_commander = [r for r in ordered if self.state.panel[r.sector] != "quiet"]
        decided = {d.sector: d for d in self._parallel(command, need_commander, effective_cap(tick.swarm_budget.max_active_agents))}
        for rep in ordered:
            self.state.decisions.append(decided.get(rep.sector) or quiet_decision(rep))
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
            route="challenge" if (self.state.challenge_reasons or self.state.escalations) else "fast",
            agents_used=self.state.agents_used,
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
        if worst.sector in self.state.challenge_reasons:
            route = f"risk gate: {self.state.challenge_reasons[worst.sector]}"
        elif worst.sector in self.state.escalations:
            route = f"risk gate: escalated ({self.state.escalations[worst.sector]})"
        else:
            route = "risk gate: not triggered"
        lines.append(f"Peak agents: {record.agents_used}; decision time: {record.timing_ms / 1000:.1f}s; {route}.")
        if worst.floor_applied:
            lines.append(f"Safety floor: {worst.floor_applied}.")
        label = {"full": "full panel", "quiet": "single analyst", "escalated": "single analyst → escalated to full panel"}
        lines.append("Inspection order: " + ", ".join(
            f"{d.sector} ({label[d.panel]}, sev {d.severity_score}) → {d.action}" for d in record.sectors) + ".")
        return "\n".join(lines)


def run_tick(partners: PartnerClient, memory: SwarmMemory, **kw: Any) -> DecisionRecord:
    flow = ContainmentFlow(partners, memory, **kw)
    flow.kickoff()
    assert flow.record is not None
    return flow.record
