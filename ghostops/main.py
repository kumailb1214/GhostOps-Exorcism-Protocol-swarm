"""GhostOps — one command to run the Exorcism Protocol swarm.

  python main.py --mock                       # one fixture tick, no keys, no network
  python main.py --mock --fixture eval/fixtures/control-tick.json
  python main.py --mock --eval                # baseline vs enhanced over all fixtures
  python main.py                              # live: pull a tick from the scenario feed, submit, dispatch
  python main.py --loop 5                     # live: process five ticks back to back
  python main.py --no-challenge               # ablate the risk gate (baseline behaviour)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

import logging

logging.getLogger("crewai").setLevel(logging.ERROR)
logging.getLogger("root").setLevel(logging.ERROR)

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from crewai.events.event_listener import EventListener  # noqa: E402

_fmt = EventListener().formatter  # silence CrewAI's flow/crew banners; our timeline is the output
_fmt.verbose = False
_fmt.print = lambda *a, **k: None  # type: ignore[method-assign]  # flow panels bypass the verbose flag

from eval.harness import run_suite  # noqa: E402
from eval.memory import SwarmMemory  # noqa: E402
from eval.timeline import console, show_comparison, show_record  # noqa: E402
from partners import make_partners  # noqa: E402
from schemas import Tick  # noqa: E402
from swarm.flow import run_tick  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mock", action="store_true", help="use deterministic mock partners (MOCK_PARTNERS=1)")
    ap.add_argument("--fixture", help="run this tick JSON instead of pulling from the feed")
    ap.add_argument("--eval", action="store_true", help="run baseline vs enhanced over eval/fixtures")
    ap.add_argument("--loop", type=int, default=1, help="number of live ticks to process")
    ap.add_argument("--no-challenge", action="store_true", help="disable the risk gate / challenge round")
    ap.add_argument("--no-submit", action="store_true")
    ap.add_argument("--no-dispatch", action="store_true")
    ap.add_argument("--json", metavar="PATH", help="also write the DecisionRecord(s) as JSON lines to PATH")
    a = ap.parse_args()

    team = os.environ.get("TEAM_HANDLE", "agent-doom")
    mock = a.mock or os.environ.get("MOCK_PARTNERS") == "1"
    partners = make_partners(mock=mock, fixture=a.fixture)
    memory = SwarmMemory(partners)

    if a.eval:
        def make(challenge: bool):
            def _run(tick: Tick):
                partners.reset_counts()
                return run_tick(partners, memory, team=team, challenge_enabled=challenge, submit=False, dispatch=False, tick=tick)
            return _run
        baseline = run_suite(make(False), "baseline")
        enhanced = run_suite(make(True), "enhanced")
        show_comparison(baseline, enhanced)
        return 0

    for i in range(a.loop):
        tick = Tick.model_validate(json.loads(Path(a.fixture).read_text())) if a.fixture else None
        rec = run_tick(partners, memory, team=team, challenge_enabled=not a.no_challenge,
                       submit=not a.no_submit, dispatch=not a.no_dispatch, tick=tick)
        show_record(rec)
        if a.json:
            with open(a.json, "a") as f:
                f.write(rec.model_dump_json() + "\n")
        if rec.dispatch and not rec.dispatch.get("ok"):
            console.print(f"[red]Dispatch failed:[/red] {rec.dispatch.get('detail')}")
        partners.reset_counts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
