---
name: One (withone.ai) MCP auth from code
description: Why GhostOps talks to One via the local stdio MCP server instead of the hosted URL
---
Rule: from Python/CrewAI, reach One through `npx -y @withone/mcp` (stdio) with the API key passed as `ONE_SECRET`. Do not use `https://mcp.withone.ai/mcp` from code.

**Why:** the hosted server is browser-OAuth only; Bearer / X-API-Key with an `sk_live_` key returns 401 (verified 2026-10-28). One's own event skill doc says the same. Local server uses camelCase params (`platform`, `actionId`, `connectionKey`, `pathVariables`, `data`) and requires `platform` on execute; remote uses snake_case.

**How to apply:** any new One action = list → find → execute in one child process. Connections must exist on the account first (`one add <platform>` / app.withone.ai); the account used here had only Gmail connected at first.
