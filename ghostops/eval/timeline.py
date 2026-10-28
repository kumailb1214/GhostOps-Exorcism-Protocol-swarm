"""Judge-facing decision timeline: evidence and rationale, not hidden chain-of-thought."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from schemas import DecisionRecord

console = Console()


def show_record(rec: DecisionRecord) -> None:
    t = Table(title=f"Tick {rec.tick_id} · {rec.facility_id} · route={rec.route} · {rec.timing_ms} ms", expand=True)
    t.add_column("Sector")
    t.add_column("Role")
    t.add_column("Recommends")
    t.add_column("Finding")
    t.add_column("Missing evidence")
    for r in rec.reports:
        t.add_row(r.sector, r.role.value + (" ↺" if r.is_challenge_response else ""), r.recommended_action,
                  r.finding, ", ".join(r.missing_evidence) or "—")
    console.print(t)
    for d in rec.sectors:
        body = (f"[bold]{d.action.upper()}[/bold] — {d.rationale}\n"
                f"supporting: {', '.join(d.supporting_observations) or '—'}\n"
                f"disagreement: {d.disagreement or 'none'} · challenged: {d.challenged}\n"
                f"missing: {', '.join(d.missing_evidence) or '—'} · rejected: {d.rejected_alternatives or '—'}"
                + (f"\n[red]FLOOR: {d.floor_applied}[/red]" if d.floor_applied else ""))
        console.print(Panel(body, title=f"{'Settled · ' + d.settled_by if d.settled_by else 'Commander'} · {d.sector}"))
    foot = (f"TICK ACTION: [bold]{rec.action}[/bold] · agents used {rec.agents_used} · partner calls {rec.partner_calls}\n"
            f"submission: {rec.submission}\ndispatch: {rec.dispatch}")
    console.print(Panel(foot, title="Outcome"))


def show_comparison(baseline: dict, enhanced: dict) -> None:
    t = Table(title="Baseline vs enhanced (local fixtures — not the official scoreboard)")
    for c in ("fixture", "expected", "baseline", "enhanced", "route", "b_ms", "e_ms", "b_calls", "e_calls"):
        t.add_column(c)
    for b, e in zip(baseline["rows"], enhanced["rows"]):
        mark = lambda r: r["action"] + (" ✓" if r["score"] == 1 else (" ½" if r["score"] == 0.5 else " ✗"))
        t.add_row(b["fixture"], b["expected"], mark(b), mark(e), e["route"], str(b["ms"]), str(e["ms"]), str(b["calls"]), str(e["calls"]))
    console.print(t)
    s = Table(title="Summary")
    for c in ("metric", "baseline", "enhanced"):
        s.add_column(c)
    for k in ("mean_score", "false_alarms", "budget_violations", "p50_ms", "total_calls"):
        s.add_row(k, str(baseline[k]), str(enhanced[k]))
    console.print(s)
