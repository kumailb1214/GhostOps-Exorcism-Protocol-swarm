"""Four CrewAI agents with distinct roles, distinct tools, and partial knowledge.

Each specialist sees ONLY its slice of the tick (scoped in the <<DATA>> block). The commander sees
structured reports, never raw sensors. That partial-knowledge split is what the track asks for.
"""
from __future__ import annotations

import atexit
import threading
import json
import os
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.tools import tool
from pydantic import BaseModel, Field

from partners.base import PartnerClient
from schemas import Action, Evidence, Role, SectorReport, SpecialistReport


class SpecialistOut(BaseModel):
    finding: str
    evidence: list[Evidence] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    recommended_action: Action


class CommanderOut(BaseModel):
    action: Action
    rationale: str
    supporting_observations: list[str] = Field(default_factory=list)
    disagreement: str = ""
    missing_evidence: list[str] = Field(default_factory=list)
    rejected_alternatives: str = ""


SCHEMA_HINT = (
    "Respond with ONLY a JSON object matching this schema, no prose: "
    '{"finding": str, "evidence": [{"claim": str, "provenance": "observed"|"retrieved"|"hypothesized", "source": str}], '
    '"missing_evidence": [str], "recommended_action": "contain"|"evacuate"|"observe"}. '
    "Tag every claim with its provenance honestly: sensor/witness data is observed, web results are retrieved, "
    "your inferences are hypothesized. List what evidence you would need but do not have. "
    "Be terse: finding ≤ 30 words, at most 3 evidence items, at most 2 missing_evidence items."
)


HEDGE_MS = int(os.environ.get("GHOSTOPS_HEDGE_MS", "3000"))  # 0 disables tail-latency hedging
_hedge_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="hedge")
_in_flight: set = set()  # futures that may still be running (losing hedges)


def drain_hedges(timeout: float = 20.0) -> None:
    """Wait for losing hedge requests to finish before the process exits, so the interpreter does not tear the
    HTTP client down under them. Called by main after the record is printed: it is outside the tick clock."""
    wait(list(_in_flight), timeout=timeout)
    _in_flight.clear()


atexit.register(drain_hedges)


def _data_block(payload: dict[str, Any]) -> str:
    return "<<DATA>>" + json.dumps(payload, default=str) + "<<END>>"


class Swarm:
    def __init__(self, partners: PartnerClient, memory) -> None:
        self.p = partners
        self.memory = memory
        self.hedges = 0  # duplicate requests fired against slow calls (reported in the record)
        self._hedge_lock = threading.Lock()
        self._hedge_open = False
        llm = partners.llm()

        @tool("youcom_search")
        def youcom_search(query: str) -> str:
            """Search the live web via You.com for facts about an entity class, a facility, or a protocol."""
            return json.dumps(partners.ground(query, count=3))

        self.sensor = dict(
            role="Sensor Analyst", llm=llm, tools=[], verbose=False, max_iter=2,  # prior signatures arrive in its payload
            goal="Decide whether the sector's sensor readings form a coherent paranormal signature or a false positive.",
            backstory="You read EMF, thermal and motion telemetry. You never see witness reports; you judge physics only. "
                      "A coherent signature is several channels anomalous together (EMF ≥ 60 mG with a cold spot ≤ 5 C, "
                      "motion rising with them): recommend contain. One channel alone, or readings within equipment norms, "
                      "is a probable false positive: recommend observe. Never explain away a coherent signature.")
        self.researcher = dict(
            role="Grounding Researcher", llm=llm, tools=[youcom_search], verbose=False, max_iter=3,
            goal="Verify the reported sighting against external references and say what evidence is missing.",
            backstory="You only see the witness sighting and web results. You distrust single low-confidence witnesses and "
                      "you separate what a source actually says from what an agent assumes.")
        self.civilian = dict(
            role="Civilian Safety Officer", llm=llm, tools=[], verbose=False, max_iter=2,
            goal="Weigh the human cost of acting versus waiting in this sector.",
            backstory="You only see civilian status and containment state. Pinned civilians override caution; "
                      "an empty breached room does not justify evacuating the building.")
        self.commander = dict(
            role="Containment Commander", llm=llm, tools=[], verbose=False, max_iter=2,
            goal="Choose contain, evacuate or observe for the sector from specialist reports and explain the call.",
            backstory="You never see raw telemetry — only structured reports. You resolve disagreement explicitly, "
                      "name what evidence was missing, and say why the alternatives were rejected.")

    def warm_up(self) -> None:
        """Pay the one-off CrewAI/LLM client initialisation (~15-20 s cold) before the tick clock starts."""
        class _Ok(BaseModel):
            ok: bool
        self._run(dict(self.civilian, max_iter=1), 'Reply with ONLY {"ok": true}.', _Ok)

    # ---- specialists ----------------------------------------------------
    def _run(self, spec: dict[str, Any], description: str, out_model: type[BaseModel]) -> BaseModel:
        """Run one agent task, hedged against provider tail latency: the model answers in ~1.5 s alone but under
        concurrent load one call in four comes back in 4 s+ and the whole parallel stage waits for it. If no
        answer arrives within HEDGE_MS the SAME agent sends a duplicate request and the first answer wins.
        It is a retry, not an extra agent, and it is capped at one hedge per task."""
        if HEDGE_MS <= 0:
            return self._run_once(spec, description, out_model)
        futures = [_hedge_pool.submit(self._run_once, spec, description, out_model)]
        _in_flight.update(futures)
        done, _ = wait(futures, timeout=HEDGE_MS / 1000, return_when=FIRST_COMPLETED)
        if not done and self._take_hedge_slot():
            futures.append(_hedge_pool.submit(self._run_once, spec, description, out_model))
            _in_flight.add(futures[-1])
        if not done:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
        winner = next(iter(done))
        if len(futures) > 1:
            self._release_hedge_slot()
        _in_flight.discard(winner)
        for f in futures:
            if f is not winner:
                f.cancel()  # the loser's result is discarded if it is still in flight
        return winner.result()

    def _take_hedge_slot(self) -> bool:
        # One hedge in flight at a time: under a provider rate limit, extra requests make everything slower.
        with self._hedge_lock:
            if self._hedge_open:
                return False
            self._hedge_open = True
            self.hedges += 1
            return True

    def _release_hedge_slot(self) -> None:
        with self._hedge_lock:
            self._hedge_open = False

    def _run_once(self, spec: dict[str, Any], description: str, out_model: type[BaseModel]) -> BaseModel:
        # A fresh Agent per task: CrewAI refuses to run one Agent's executor concurrently, and sectors run in
        # parallel waves. Construction is cheap; the role/goal/tools come from the shared spec.
        agent = Agent(**spec)
        task = Task(description=description, expected_output="A single JSON object.", agent=agent, output_pydantic=out_model)
        res = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False).kickoff()
        if res.pydantic is not None:
            return res.pydantic
        return out_model.model_validate_json(res.raw)

    def specialist(self, role: Role, report: SectorReport, challenge: str | None = None) -> SpecialistReport:
        if role == Role.SENSOR:
            agent, payload = self.sensor, {"sensors": report.sensors.model_dump(), "prior": self.memory.recall(report.sector)}
        elif role == Role.RESEARCHER:
            sg = report.sighting
            grounding = self.p.ground(f"{sg.entity_class} apparition {report.sector} containment protocol", count=3) \
                if sg.entity_class != "none" else []
            agent, payload = self.researcher, {"sighting": sg.model_dump(), "grounding": grounding}
        else:
            agent, payload = self.civilian, {"civilian_status": report.civilian_status, "containment_state": report.containment_state}
        payload |= {"role": role.value, "sector": report.sector}
        if challenge:
            payload["challenge"] = challenge
        desc = (f"Sector '{report.sector}'. Your scoped data:\n{_data_block(payload)}\n"
                + (f"CHALLENGE from the commander — answer it directly: {challenge}\n" if challenge else "")
                + SCHEMA_HINT)
        out: SpecialistOut = self._run(agent, desc, SpecialistOut)  # type: ignore[assignment]
        return SpecialistReport(role=role, sector=report.sector, is_challenge_response=bool(challenge), **out.model_dump())

    # ---- commander -------------------------------------------------------
    def command(self, sector: str, reports: list[SpecialistReport]) -> CommanderOut:
        payload = {"role": Role.COMMANDER.value, "sector": sector,
                   "reports": [r.model_dump(mode="json") for r in reports]}
        desc = (f"Sector '{sector}'. Specialist reports (you have no other data):\n{_data_block(payload)}\n"
                "Respond with ONLY a JSON object: {\"action\": \"contain\"|\"evacuate\"|\"observe\", \"rationale\": str, "
                "\"supporting_observations\": [str], \"disagreement\": str, \"missing_evidence\": [str], "
                "\"rejected_alternatives\": str}. Rules: pinned civilians require evacuate; a control sector with no "
                "corroborated signature must be observe — a false alarm is scored against us; contain when the "
                "signature is real but no one is in danger. Be terse: rationale ≤ 40 words, lists ≤ 3 items.")
        return self._run(self.commander, desc, CommanderOut)  # type: ignore[return-value]
