"""MOCK_PARTNERS=1 — deterministic stand-ins so the Flow runs with zero network and zero keys.

The mock LLM reads the <<DATA>>...<<END>> block that agents.py embeds in every task description
and answers with rule-based JSON shaped exactly like the real model's schema-locked output.
It is deliberately dumb: it exists to exercise routing, budget, challenge, and dispatch paths.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from crewai.llms.base_llm import BaseLLM

from partners.base import PartnerClient
from schemas import Judgment, Tick

FIXTURES = Path(__file__).resolve().parent.parent / "eval" / "fixtures"
DATA_RE = re.compile(r"<<DATA>>(.*?)<<END>>", re.S)


def _extract(messages: Any) -> dict[str, Any]:
    text = messages if isinstance(messages, str) else "\n".join(m.get("content", "") for m in messages)
    m = DATA_RE.search(text)
    return json.loads(m.group(1)) if m else {}


def _sensor_view(d: dict[str, Any]) -> dict[str, Any]:
    s = d.get("sensors", {})
    emf, temp, motion = s.get("emf_milligauss", 0), s.get("temp_c", 20), s.get("motion_events", 0)
    anomalies = []
    if emf >= 60:
        anomalies.append(f"EMF {emf} mG above 60 threshold")
    if temp <= 5:
        anomalies.append(f"cold spot {temp}C")
    if motion >= 10:
        anomalies.append(f"{motion} motion events")
    if emf >= 60 and temp <= 5:
        rec = "evacuate" if motion >= 10 else "contain"
    elif anomalies:
        rec = "contain"
    else:
        rec = "observe"
    conflict = emf >= 60 and temp > 15
    return {
        "finding": ("Conflicting signature: high EMF without thermal drop" if conflict else
                    ("Consistent paranormal signature" if len(anomalies) >= 2 else
                     ("Single weak anomaly" if anomalies else "Sensors nominal"))),
        "evidence": [{"claim": a, "provenance": "observed", "source": "sensors"} for a in anomalies],
        "missing_evidence": ["thermal corroboration"] if conflict else [],
        "recommended_action": "contain" if conflict else rec,
    }


def _researcher_view(d: dict[str, Any]) -> dict[str, Any]:
    sg = d.get("sighting", {})
    conf, wit, cls = sg.get("confidence", 0), sg.get("witness_count", 0), sg.get("entity_class", "none")
    grounded = d.get("grounding", [])
    ev = [{"claim": g.get("snippet", "")[:120], "provenance": "retrieved", "source": g.get("url", "")} for g in grounded[:2]]
    if cls == "none" or conf < 0.3:
        return {"finding": "No credible sighting", "evidence": ev, "missing_evidence": [], "recommended_action": "observe"}
    if wit <= 1 and conf < 0.7:
        return {"finding": f"Single low-confidence report of {cls}", "evidence": ev,
                "missing_evidence": ["independent witness", "sensor corroboration"], "recommended_action": "observe"}
    lvl = int(re.sub(r"\D", "", cls) or 0)
    return {"finding": f"{wit} witnesses report {cls} at {conf:.2f}", "evidence": ev,
            "missing_evidence": [] if ev else ["external reference for entity class"],
            "recommended_action": "evacuate" if lvl >= 4 else "contain"}


def _civilian_view(d: dict[str, Any]) -> dict[str, Any]:
    status, state = d.get("civilian_status", "clear"), d.get("containment_state", "stable")
    pinned = int(re.sub(r"\D", "", status) or 0) if "pinned" in status or "trapped" in status else 0
    if pinned > 0:
        return {"finding": f"{pinned} civilians pinned, containment {state}",
                "evidence": [{"claim": status, "provenance": "observed", "source": "civilian_status"}],
                "missing_evidence": [], "recommended_action": "evacuate"}
    if state == "breached":
        return {"finding": "Breach with no civilians at risk", "evidence": [], "missing_evidence": [], "recommended_action": "contain"}
    return {"finding": "No civilians at risk", "evidence": [], "missing_evidence": [], "recommended_action": "observe"}


def _commander_view(d: dict[str, Any]) -> dict[str, Any]:
    reports = d.get("reports", [])
    recs = [r["recommended_action"] for r in reports]
    order = {"observe": 0, "contain": 1, "evacuate": 2}
    civ = next((r for r in reports if r["role"] == "civilian_safety"), None)
    if civ and civ["recommended_action"] == "evacuate":
        action = "evacuate"
    else:
        votes = sorted(recs, key=lambda a: order[a])
        action = votes[len(votes) // 2] if votes else "observe"  # median severity
    return {
        "action": action,
        "rationale": f"Median of specialist recommendations {recs}, civilian safety "
                     f"{'overrides' if civ and civ['recommended_action']=='evacuate' else 'does not override'}.",
        "supporting_observations": [e["claim"] for r in reports for e in r.get("evidence", [])][:5],
        "disagreement": "yes: " + ", ".join(sorted(set(recs))) if len(set(recs)) > 1 else "",
        "missing_evidence": sorted({m for r in reports for m in r.get("missing_evidence", [])}),
        "rejected_alternatives": ", ".join(a for a in order if a != action),
    }


class MockLLM(BaseLLM):
    """Rule-based CrewAI LLM. Returns a JSON string the Task's output_pydantic can parse."""

    model: str = "mock/ghostops"

    def __init__(self, **data: Any) -> None:
        super().__init__(model="mock/ghostops", **data)

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None, from_agent=None, response_model=None):  # type: ignore[override]
        d = _extract(messages)
        role = d.get("role")
        if role == "sensor_analyst":
            out = _sensor_view(d)
        elif role == "grounding_researcher":
            out = _researcher_view(d)
        elif role == "civilian_safety":
            out = _civilian_view(d)
        elif role == "commander":
            out = _commander_view(d)
        else:
            out = {"finding": "unknown role", "evidence": [], "missing_evidence": [], "recommended_action": "observe"}
        if d.get("challenge"):
            # A challenge re-assessment: a dissenting specialist must justify or soften.
            if out.get("missing_evidence"):
                out["recommended_action"] = "observe" if out["recommended_action"] == "contain" else out["recommended_action"]
                out["finding"] += " — on challenge, downgraded for lack of corroboration"
        return json.dumps(out)

    def supports_function_calling(self) -> bool:
        return False

    def supports_stop_words(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 32000


class MockPartners(PartnerClient):
    def __init__(self, fixture: str | Path | None = None) -> None:
        super().__init__()
        self._fixture = Path(fixture) if fixture else FIXTURES / "outbreak-level-5.json"
        self._llm = MockLLM()
        self.dispatched: list[str] = []

    def get_tick(self) -> Tick:
        self.calls["scenario.get_tick"] += 1
        return Tick.model_validate(json.loads(self._fixture.read_text()))

    def submit(self, team: str, tick_id: int, action: str) -> Judgment:
        self.calls["scenario.submit"] += 1
        tick = Tick.model_validate(json.loads(self._fixture.read_text()))
        from eval.harness import expected_action_for  # local import avoids cycle

        expected = expected_action_for(tick)
        score = 1.0 if action == expected else (0.5 if {action, expected} == {"contain", "evacuate"} else 0.0)
        return Judgment(expected_action=expected, score=score, false_alarm=(expected == "observe" and action != "observe"),
                        raw={"team": team, "tick_id": tick_id, "expectation_source": "mock"})

    def ground(self, query: str, count: int = 3) -> list[dict[str, str]]:
        self.calls["youcom.search"] += 1
        h = hashlib.sha1(query.encode()).hexdigest()[:6]
        return [{"title": f"Field note {h}", "url": f"https://example.invalid/lore/{h}",
                 "snippet": f"Reference material matching '{query[:40]}' (mock)."}][:count]

    def llm(self) -> Any:
        return self._llm

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls["parasail.embed"] += 1
        out = []
        for t in texts:
            v = [0.0] * 16
            for tok in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 16] += 1.0
            n = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / n for x in v])
        return out

    def dispatch(self, content: str) -> dict[str, Any]:
        self.calls["one.dispatch"] += 1
        self.dispatched.append(content)
        return {"ok": True, "method": "mock", "detail": "printed, not sent"}
