---
name: CrewAI quirks
description: Non-obvious CrewAI 1.15 behaviours hit while building the GhostOps swarm.
---
- Flow method banners ("Flow Method Running") are printed regardless of `ConsoleFormatter.verbose`; silence by replacing `EventListener().formatter.print` with a no-op before kickoff.
  **Why:** they pollute stdout and break any JSON-on-stdout plan. **How to apply:** do it once at process start; write machine output to a file instead.
- A rule-based `crewai.llms.base_llm.BaseLLM` subclass works as a drop-in agent LLM for mock mode; `Task(output_pydantic=...)` parses its JSON string fine. Embed scoped data in the task description so the mock can read it.
- CrewAI import/startup costs ~12 s; the Flow itself runs sub-second in mock mode. Don't optimise the flow for mock latency.
