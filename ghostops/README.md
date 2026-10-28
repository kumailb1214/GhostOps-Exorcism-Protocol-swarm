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
python main.py --scenario outbreak-level-5 --loop 2   # both outbreak ticks (stops itself when the feed is exhausted)
python main.py --scenario control-tick                # the control tick (must be observe)
python main.py --scoreboard     # live scoreboard
python main.py --no-challenge   # ablation: disable the risk gate
```

Live status (Oct 28): all three scenario ticks submitted as `agent-doom`, 3/3 correct, 0 false alarms.
The feed is per-team and one-shot: once a scenario is exhausted it re-serves the last tick with `exhausted: true`
and further submissions are flagged `duplicate` (not scored twice). To answer a tick you already pulled, run
`--fixture <tick json>` with submit on; the submission goes by `tick_id`.

Python 3.10–3.13. Dependencies are in the repo root `pyproject.toml` (`uv sync` or `pip install crewai crewai-tools httpx openai pydantic python-dotenv rich`).

## Lean by design: triage before agents

Before any model runs, `swarm/triage.py` scores every sector from the signals the referee sees and sorts them
(the **inspection order**). Hot sectors (civilians at risk, a breach, or a strong multi-signal signature) get the
full 3-specialist panel. Quiet sectors get **one sensor analyst**; if it recommends anything but observe the sector
is escalated to the full panel in the same stage as the challenge round. Concurrency is capped at
`GHOSTOPS_MAX_AGENTS` (default 4) under the feed's budget. Measured live on the real tick 143 shape:

| | before | after |
|---|---|---|
| peak agents | 7 | **4** |
| model calls | 13 | **6** |
| decision time | 25 s | **12–14 s** |
| control tick | 4 agents, 9 s | **1 agent, 6 s** |

Triage allocates agents; it never decides the action. Every sector is read by at least one agent and the record
says which panel it got (`panel`, `severity_score`).

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
swarm/            flow.py (pipeline), agents.py (4 agents), challenge.py (risk gate), triage.py
partners/         base.py (interface), mock.py (MOCK_PARTNERS=1), real.py (You.com, Parasail, One, scenario MCP)
eval/             memory.py (provenance-tagged), harness.py (baseline vs enhanced), timeline.py, fixtures/
```

## Fallbacks (in the order the brief recommends)

1. One, via its **local** MCP server (`npx -y @withone/mcp` with `ONE_API_KEY` as `ONE_SECRET`; the hosted
   `mcp.withone.ai/mcp` is browser-OAuth only). One attempt per run; organizers confirmed One's Discord send is
   401-ing and requires server-management access we do not have. `ONE_DISPATCH=0` skips the attempt.
2. `DISCORD_WEBHOOK_URL` — the venue webhook from hack page §3.4, the organizers' recommended programmatic path
   (method = `webhook-fallback`, status 204 = posted).
3. `ok=False, method=manual` — post `agent-doom · [sector] · ACTION · rationale` yourself from the account that joined the server.

Feed down? `python main.py --fixture eval/fixtures/outbreak-level-5.json` runs the identical contract offline; queue the
decision and replay to `hack26-submit-containment` when it returns.

## Demo (3 min)

1. `--fixture eval/fixtures/control-tick.json` — swarm observes; no dispatch, no false alarm.
2. `--fixture eval/fixtures/outbreak-level-5.json` — fast path, evacuate, Discord post.
3. `--fixture eval/fixtures/synthetic/ambiguous-single-witness.json` — challenge round, sensor analyst downgrades, false alarm avoided.
4. `--eval` — baseline vs enhanced table.
