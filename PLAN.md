# GhostOps — Track 1 (The Exorcism Protocol) Build Plan

Hack with YOU: Halloween Edition · 8 hours · 3 people · single pipeline

## 0. One-line pitch

Most swarms deliberate the same way every tick. GhostOps spends extra reasoning only where it can change the decision — and measures whether that helps.

## 1. Required stack (all four must be load-bearing)

| Partner | Role in pipeline | Irreplaceable because |
|---|---|---|
| CrewAI | Flow orchestration + 4 agents | The swarm itself (`@start`, `@listen`, `@router`) |
| Parasail | Inference (`parasail-glm-53-flash`), schema-locked JSON, embeddings (`parasail-ai/GritLM-7B-vllm`) | Every specialist report and the commander decision |
| You.com | Grounding tool for the researcher (`/v1/search` with `extraction: "highlights"`, `/v1/contents`) | Evidence for/against a sighting |
| One | `find_one_actions` → `execute_one_action` → Discord `#loft-incident` (`1564842098740695100`) | The real escalation the room sees |

Scenario feed: MCP at `SCENARIO_MCP` via `MCPServerHTTP(url=..., streamable=True)`. Tools: `hack26-get-tick`, `hack26-submit-containment`, `hack26-get-scoreboard`.

Python 3.12 (CrewAI needs 3.10–3.13). Keys in `.env`, never committed.

## 2. Single pipeline (one Flow, per tick)

```
get_tick
  └─ validate Tick (fields unchanged, multi-sector aware)
  └─ for each sector (bounded by swarm_budget.max_active_agents):
       ├─ SensorAnalyst      (sensors + prior readings + signature recall)   ─┐
       ├─ GroundingResearcher(sighting + You.com evidence)                   ─┤ parallel
       └─ CivilianSafety     (civilian_status + containment_state)           ─┘
  └─ @router  risk_gate:
       ├─ "clear"      → Commander decides immediately   (fast path)
       └─ "ambiguous"  → ONE bounded challenge round → Commander decides
  └─ Commander → DecisionRecord {action, rationale, evidence, disagreement, timing}
  └─ submit_containment(team, tick_id, action)
  └─ if action != observe → dispatch to Discord via One (dedup, 1 retry, manual fallback)
  └─ memory.remember(sector, observations, decision, judgment)
```

Risk gate triggers "ambiguous" when: specialists disagree, researcher reports missing evidence, or proposed action is `evacuate` on a sector with no civilians pinned. Never on model self-reported confidence alone.

## 3. Repo layout

```
ghostops/
├─ main.py                 # one command: python main.py [--mock] [--fixture path]
├─ schemas.py              # SHARED CONTRACT — change only with group ping
├─ swarm/                  # Person A
│   ├─ flow.py             # CrewAI Flow + router
│   ├─ agents.py           # 4 agents, scoped tools, scoped Memory
│   ├─ challenge.py        # bounded second-assessment round
│   └─ triage.py           # max_active_agents enforcement
├─ partners/               # Person B
│   ├─ base.py             # PartnerClient interface
│   ├─ mock.py             # MOCK_PARTNERS=1 implementation
│   ├─ scenario.py         # MCP get_tick / submit / scoreboard
│   ├─ parasail.py         # chat (json_schema), embeddings, store=False
│   ├─ youcom.py           # search + contents, X-API-Key, 429 backoff
│   └─ one.py              # find_one_actions / execute_one_action + fallback
├─ eval/                   # Person C
│   ├─ memory.py           # per-sector store with provenance tags
│   ├─ harness.py          # baseline vs enhanced over fixtures
│   ├─ fixtures/           # outbreak-level-5.json, control-tick.json, synthetic/
│   └─ timeline.py         # judge-facing decision timeline (CLI/HTML)
├─ .env.example
└─ README.md
```

## 4. Shared contracts (`schemas.py`)

- `Tick` — exact brief fields: `tick_id, facility_id, sector_reports[], swarm_budget, escalation_channel`
- `SpecialistReport` — `role, sector, finding, evidence[], missing_evidence[], recommended_action, provenance`
- `DecisionRecord` — `tick_id, sector, action, rationale, supporting_observations[], disagreement, missing_evidence[], rejected_alternatives, timing_ms, partner_calls{}`
- `PartnerClient` — `get_tick(), submit(team, tick_id, action), ground(query), chat(messages, schema), embed(texts), dispatch(content)`

Provenance tags on every memory item: `observed | retrieved | hypothesized`.

## 5. Ownership

| Person | Module | Done by hour 3 |
|---|---|---|
| A | `swarm/` | Flow runs a tick dict → DecisionRecord against `mock.py` |
| B | `partners/` | All real clients tested with one curl-equivalent each; retry/backoff |
| C | `eval/` + README + submission | Harness prints correctness / false alarms / latency / call count for any decision fn |

Rules: only B touches `.env`. Everyone runs `MOCK_PARTNERS=1` until hour 6. Integration at hour 6 regardless — unfinished features get cut, not extended.

## 6. Hour-by-hour

| Hour | A | B | C |
|---|---|---|---|
| 1 | schemas + Flow skeleton | creds verified, MCP tools listed | fixtures loaded, harness skeleton |
| 2–3 | 4 agents, baseline routing, budget | real clients, JSON-locked Parasail | memory store, synthetic variants |
| 4–5 | selective challenge round | dispatch dedup + Discord fallback | baseline vs enhanced numbers |
| 6 | **Integration — one real tick end to end** | | |
| 7 | tune risk-gate thresholds from C's numbers | hardening, error messages | timeline, README, demo script |
| 8 | freeze | freeze | Google Form submission, final run |

## 7. Scoring map (what each feature buys)

| Criterion (pts) | Feature |
|---|---|
| Containment speed (30) | fast path for clear ticks; parallel specialists |
| False-alarm rate (20) | risk gate + bounded challenge; control-tick in harness |
| Civilians / escalation correctness (20) | CivilianSafety agent; dispatch only when action ≠ observe |
| All four partners (20) | each is a hard dependency in the Flow |
| Code quality / reproducible (10) | `python main.py`, seeded fixtures, `--mock` mode |

Disqualifiers to avoid: single-agent swarm, hardcoded scenario responses, passthrough partner use.

## 8. Demo script (3 min)

1. Control tick → swarm observes, shows why it refused to escalate.
2. Level-5 breach → fast path, evacuate, Discord post appears in `#loft-incident`.
3. Ambiguous synthetic tick → challenge round, show disagreement resolved with evidence.
4. Harness table: baseline vs enhanced.

## 9. Cut list (in order)

Timeline HTML → synthetic variants → challenge round. Never cut: end-to-end submission, dispatch, mock mode.
