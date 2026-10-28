# GhostOps — Track 1: The Exorcism Protocol

A supervised CrewAI swarm that senses, grounds, challenges itself, and contains a paranormal outbreak —
spending extra reasoning only where it can change the decision, and measuring whether that helps.

## Run

```bash
cd ghostops
cp .env.example .env            # fill in keys (B owns this file)
python main.py --mock           # zero keys: one fixture tick end to end
python main.py --mock --eval    # baseline vs enhanced over every fixture
python main.py                  # live: pull tick → decide → submit → dispatch to #loft-incident
python main.py --loop 10        # live: ten ticks
python main.py --no-challenge   # ablation: disable the risk gate
```

Python 3.10–3.13. Dependencies are in the repo root `pyproject.toml` (`uv sync` or `pip install crewai crewai-tools httpx openai pydantic python-dotenv rich`).

## The pipeline (one CrewAI Flow per tick)

```
hack26-get-tick
 └─ validate Tick                        (field names unchanged, multi-sector)
 └─ specialists per sector, in waves bounded by swarm_budget.max_active_agents
      Sensor Analyst        sees sensors + prior memory          tool: recall_signatures
      Grounding Researcher  sees sighting + You.com results      tool: youcom_search
      Civilian Safety       sees civilian_status + containment
 └─ @router risk_gate
      fast      → Commander
      challenge → ONE bounded second assessment from the dissenter → Commander
 └─ Commander (sees reports only, never raw telemetry) → SectorDecision
 └─ hack26-submit-containment (most severe sector action)
 └─ One: find_one_actions → execute_one_action → Discord #loft-incident   (only if action ≠ observe)
 └─ memory.remember(sector, decision, judgment)
```

The risk gate fires only on: specialist disagreement, missing evidence from the researcher, or an
`evacuate` with no civilians at risk. Never on model confidence alone.

## Why each partner is irreplaceable

| Partner | Removed → |
|---|---|
| CrewAI | no Flow, no agents, no router — there is no swarm |
| Parasail | no specialist can think; `_CountingLLM` wraps every call so usage is auditable |
| You.com | the researcher has nothing to ground against; `missing_evidence` is always "everything" |
| One | the room never sees an escalation; dispatch returns `ok=False` loudly, never silently |

Partner calls per tick are printed in the Outcome panel (`partner_calls`).

## Layout

```
schemas.py        shared contract (Tick, SpecialistReport, DecisionRecord, PartnerClient shape)
swarm/            flow.py (pipeline), agents.py (4 agents), challenge.py (risk gate), budget.py
partners/         base.py (interface), mock.py (MOCK_PARTNERS=1), real.py (You.com, Parasail, One, scenario MCP)
eval/             memory.py (provenance-tagged), harness.py (baseline vs enhanced), timeline.py, fixtures/
```

## Fallbacks (in the order the brief recommends)

1. One MCP → one retry inside the transport.
2. `DISCORD_WEBHOOK_URL` if set (method = `webhook-fallback`; tell the organizers' table).
3. `ok=False, method=manual` — post `team-XX · [sector] · ACTION · rationale` yourself from the account that joined the server.

Feed down? `python main.py --fixture eval/fixtures/outbreak-level-5.json` runs the identical contract offline; queue the
decision and replay to `hack26-submit-containment` when it returns.

## Demo (3 min)

1. `--fixture eval/fixtures/control-tick.json` — swarm observes; no dispatch, no false alarm.
2. `--fixture eval/fixtures/outbreak-level-5.json` — fast path, evacuate, Discord post.
3. `--fixture eval/fixtures/synthetic/ambiguous-single-witness.json` — challenge round, sensor analyst downgrades, false alarm avoided.
4. `--eval` — baseline vs enhanced table.
