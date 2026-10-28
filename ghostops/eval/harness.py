"""Baseline vs enhanced over fixtures. Prints correctness, false alarms, latency and partner calls.

The official scoreboard judges live submissions; this harness exists so we can tune the risk gate on
numbers instead of vibes. `expected_action_for` is the deterministic referee rule from the brief's
scoring curve (correct 1, contain-vs-evacuate 0.5, observing a breach 0, acting on a control tick =
false alarm). Fixture files may override it with an `_expected` key.
"""
from __future__ import annotations

import json
import re
import statistics
from pathlib import Path
from typing import Any

from schemas import SEVERITY, Tick

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def expected_action_for(tick: Tick) -> str:
    worst = "observe"
    for s in tick.sector_reports:
        pinned = bool(re.search(r"[1-9]", s.civilian_status)) and any(
            k in s.civilian_status for k in ("pinned", "trapped", "injured", "missing"))
        signature = (s.sensors.emf_milligauss >= 60 and s.sensors.temp_c <= 5) or (
            s.sighting.confidence >= 0.6 and s.sighting.witness_count >= 2)
        if pinned and signature:
            a = "evacuate"
        elif signature or s.containment_state == "breached":
            a = "contain"
        else:
            a = "observe"
        if SEVERITY[a] > SEVERITY[worst]:
            worst = a
    return worst


def load_fixtures() -> list[tuple[str, Tick, str]]:
    out = []
    for path in sorted(FIXTURES.rglob("*.json")):
        raw = json.loads(path.read_text())
        expected = raw.pop("_expected", None)
        raw.pop("_note", None)
        tick = Tick.model_validate(raw)
        out.append((path.stem, tick, expected or expected_action_for(tick)))
    return out


def score(action: str, expected: str) -> float:
    if action == expected:
        return 1.0
    if {action, expected} == {"contain", "evacuate"}:
        return 0.5
    return 0.0


def run_suite(make_flow, label: str) -> dict[str, Any]:
    """make_flow(tick) -> DecisionRecord. Returns aggregate metrics."""
    rows = []
    for name, tick, expected in load_fixtures():
        rec = make_flow(tick)
        rows.append({
            "fixture": name, "expected": expected, "action": rec.action, "score": score(rec.action, expected),
            "false_alarm": expected == "observe" and rec.action != "observe",
            "route": rec.route, "ms": rec.timing_ms, "calls": sum(rec.partner_calls.values()),
            "agents": rec.agents_used, "budget": tick.swarm_budget.max_active_agents,
        })
    return {
        "label": label,
        "rows": rows,
        "mean_score": round(statistics.mean(r["score"] for r in rows), 3),
        "false_alarms": sum(r["false_alarm"] for r in rows),
        "budget_violations": sum(r["agents"] > r["budget"] for r in rows),
        "p50_ms": int(statistics.median(r["ms"] for r in rows)),
        "total_calls": sum(r["calls"] for r in rows),
    }
