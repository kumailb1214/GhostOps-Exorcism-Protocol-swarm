---
name: Tick latency lessons
description: Where decision time went in the GhostOps pipeline and the rules that keep it ~4.5 s
---
- First CrewAI Crew.kickoff in a process costs ~15–20 s (client init). Always warm up before starting any timed clock.
- GLM flash answers a terse prompt in ~1.6 s; latency scales with output length, not input. Cap list lengths and words in the schema hint, max_tokens ≈ 1024.
- Only spend a challenge round where it can change the scored outcome (sector that sets the tick action; not when a deterministic floor already decides).
**Why:** rival team posted 3.5–4 s decision times; ours were 18–28 s almost entirely from cold start + verbose outputs, not from the model.
**How to apply:** any new stage must justify its ~2 s; measure per-role call time before touching the architecture.
