# GhostOps

Track 1 (Exorcism Protocol) entry for Hack with YOU: Halloween 2026 — a CrewAI swarm that contains a simulated paranormal outbreak using You.com grounding, Parasail inference and One dispatch. Full plan in `PLAN.md`.

## Run & Operate

- `cd ghostops && python main.py --mock` — one fixture tick with no keys; `--eval` runs baseline vs enhanced; no flags = live feed
- Python deps live in root `pyproject.toml`; keys go in `ghostops/.env` (see `.env.example`)

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `ghostops/schemas.py` — shared contract (Tick, SpecialistReport, DecisionRecord). Change only with a group ping.
- `ghostops/swarm/` — flow.py is the single pipeline; agents.py the 4 agents; challenge.py the risk gate; triage.py severity, inspection order, wave packing (cap GHOSTOPS_MAX_AGENTS=4)
- `ghostops/partners/` — base.py interface, mock.py (MOCK_PARTNERS=1), real.py (You.com, Parasail, One, scenario MCP)
- `ghostops/eval/` — memory, harness, timeline, fixtures
- The pnpm/TypeScript scaffold (`artifacts/`, `lib/`) is unused by the swarm

## Architecture decisions

- One CrewAI Flow per tick; specialists run in budget-bounded waves, commander sees reports only (partial knowledge).
- Risk gate triggers one bounded challenge round only on disagreement / missing evidence / evacuate-without-civilians — never on model confidence.
- Mock mode swaps in a rule-based `BaseLLM` subclass so the identical Flow runs with zero network.
- Grounding is pre-fetched into the researcher's data block (so You.com is load-bearing in both mock and real), with a `youcom_search` tool for follow-ups.
- Dispatch never fails silently: One → webhook fallback → `ok=False, method=manual`.

## Product

- Live status (2026-10-28): all 3 scenario ticks submitted as `agent-doom`, 3/3 correct, 0 false alarms; Discord escalations posted via the venue webhook.
- Team handle `agent-doom` is self-chosen (organizers allow it); `TEAM_HANDLE` env var, no token needed.

## User preferences

- Team handle is `agent-doom`. Use the venue Discord webhook for dispatch — the team cannot connect Discord in One (needs server-management access they lack); organizers endorse the webhook.

## Gotchas

- Live feed schema differs from the brief: `hack26-get-tick` requires `team` + `scenario` (`outbreak-level-5`|`control-tick`); exhausted scenarios re-serve the last tick with `exhausted: true`; verdicts are nested under `judgment`.
- CrewAI refuses to run one Agent instance concurrently — agents.py builds a fresh Agent per task because sectors run in parallel waves.
- Parasail GLM-5.3-flash needs `reasoning_effort="low"` or it burns max_tokens on thinking and returns empty content.

- CrewAI flow banners ignore the verbose flag; main.py monkeypatches `EventListener().formatter.print`.
- Use `MCPServerHTTP`/`streamablehttp_client`, not the URL-string shorthand, inside an event loop (zero tools, no error).
- One dispatch uses the local `npx -y @withone/mcp` server with `ONE_API_KEY` as `ONE_SECRET`; the hosted `mcp.withone.ai/mcp` is browser-OAuth only and rejects API keys. Discord must be connected on the One account.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
