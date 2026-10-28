"""Real partner clients. Every call is counted so the decision record can prove partner usage.

Env (see .env.example): YDC_API_KEY, PARASAIL_API_KEY, ONE_API_KEY (used as ONE_SECRET), SCENARIO_MCP,
DISCORD_CHANNEL_ID, TEAM_HANDLE, optional DISCORD_WEBHOOK_URL (venue fallback only).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import httpx
from crewai import LLM
from crewai.events import crewai_event_bus
from crewai.events.types.llm_events import LLMCallCompletedEvent
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from openai import OpenAI

from partners.base import PartnerClient
from schemas import Judgment, Tick

PARASAIL_BASE = "https://api.parasail.io/v1"
YDC_SEARCH = "https://ydc-index.io/v1/search"
PARASAIL_CHAT_MODEL = os.environ.get("PARASAIL_MODEL", "parasail-glm-53-flash")
PARASAIL_EMBED_MODEL = os.environ.get("PARASAIL_EMBED_MODEL", "parasail-bge-m3")  # GritLM is not deployed on self-serve keys (verified live)


class PartnerError(RuntimeError):
    pass


class FeedExhausted(PartnerError):
    """The scenario has no new ticks for this team; the feed re-serves the last one with exhausted=true."""


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise PartnerError(f"{name} is not set. Copy .env.example to .env and fill it in, or run with --mock.")
    return v


async def _mcp_call(url: str, tool: str, args: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    async with streamablehttp_client(url, headers=headers or {}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            if getattr(res, "isError", False):
                raise PartnerError(f"{tool} failed: {res.content}")
            if getattr(res, "structuredContent", None):
                return res.structuredContent
            text = "".join(getattr(c, "text", "") for c in res.content)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}


def mcp_call(url: str, tool: str, args: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    return asyncio.run(_mcp_call(url, tool, args, headers))


def _parse_tool_result(res: Any, tool: str) -> Any:
    if getattr(res, "isError", False):
        raise PartnerError(f"{tool} failed: {res.content}")
    if getattr(res, "structuredContent", None):
        return res.structuredContent
    text = "".join(getattr(c, "text", "") for c in res.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


async def _one_calls(secret: str, calls: list[tuple[str, dict[str, Any]]]) -> list[Any]:
    """One's hosted MCP server is browser-OAuth only (its skill doc: a bearer key does NOT work against it).
    Frameworks use the local `npx -y @withone/mcp` server, which reads ONE_SECRET from the environment.
    Several tool calls share one child process so list → find → execute costs one startup."""
    params = StdioServerParameters(command="npx", args=["-y", "@withone/mcp"],
                                   env={**os.environ, "ONE_SECRET": secret})
    out = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for tool, args in calls:
                out.append(_parse_tool_result(await s.call_tool(tool, args), tool))
    return out


class RealPartners(PartnerClient):
    def __init__(self) -> None:
        super().__init__()
        self.scenario_url = os.environ.get("SCENARIO_MCP", "https://api.you.com/mcp/halloween-hack-26")
        self.team = os.environ.get("TEAM_HANDLE", "agent-doom")
        self.scenario = os.environ.get("SCENARIO", "outbreak-level-5")  # feed enum: outbreak-level-5 | control-tick
        self.team_token = os.environ.get("TEAM_TOKEN")  # only if organizers enable tokens
        self.channel_id = os.environ.get("DISCORD_CHANNEL_ID", "1564842098740695100")
        self._openai = OpenAI(base_url=PARASAIL_BASE, api_key=_require("PARASAIL_API_KEY"))
        self._llm = LLM(
            model=f"openai/{PARASAIL_CHAT_MODEL}",
            base_url=PARASAIL_BASE,
            api_key=os.environ["PARASAIL_API_KEY"],
            temperature=0.1,
            max_tokens=4096,
            # GLM-5.3-flash is a reasoning model: without this it burns the whole token budget thinking and
            # returns content=None. reasoning_effort=low keeps ~40 reasoning tokens and ~1 s latency (verified live).
            reasoning_effort="low",
        )
        # LLM(...) is a factory returning a provider class, so count calls via the event bus instead of subclassing.
        @crewai_event_bus.on(LLMCallCompletedEvent)
        def _count_llm(_src: Any, _ev: Any) -> None:
            self.calls["parasail.chat"] += 1
        self._http = httpx.Client(timeout=20)
        self._last_dispatch: str | None = None
        self._one_action: dict[str, Any] | None = None  # resolved once per process via find_one_actions
        self._one_broken: str | None = None  # first One failure this run; afterwards go straight to the webhook

    # --- scenario feed -------------------------------------------------
    def _feed_args(self, **extra: Any) -> dict[str, Any]:
        args: dict[str, Any] = {"team": self.team, **extra}
        if self.team_token:
            args["token"] = self.team_token
        return args

    def get_tick(self) -> Tick:
        """Live schema (verified 2026-10-28): get-tick requires team + scenario; returns {tick, exhausted}."""
        self.calls["scenario.get_tick"] += 1
        data = mcp_call(self.scenario_url, "hack26-get-tick", self._feed_args(scenario=self.scenario))
        if data.get("exhausted"):
            raise FeedExhausted(f"scenario '{self.scenario}' has no new ticks for {self.team} "
                                f"(last tick {(data.get('tick') or {}).get('tick_id')}); submitting again would be a duplicate")
        return Tick.model_validate(data.get("tick", data))

    def submit(self, team: str, tick_id: int, action: str) -> Judgment:
        self.calls["scenario.submit"] += 1
        data = mcp_call(self.scenario_url, "hack26-submit-containment",
                        self._feed_args(tick_id=tick_id, action=action))
        j = data.get("judgment") or data  # live feed nests the verdict under "judgment"
        return Judgment(expected_action=j.get("expected_action") or j.get("expected"),
                        score=j.get("score"), false_alarm=j.get("false_alarm"), raw=data)

    def scoreboard(self) -> Any:
        self.calls["scenario.scoreboard"] += 1
        return mcp_call(self.scenario_url, "hack26-get-scoreboard", {})

    # --- You.com --------------------------------------------------------
    def ground(self, query: str, count: int = 3) -> list[dict[str, str]]:
        self.calls["youcom.search"] += 1
        for attempt in range(2):
            r = self._http.post(YDC_SEARCH, headers={"X-API-Key": _require("YDC_API_KEY"), "Content-Type": "application/json"},
                                json={"query": query, "count": count})  # "extraction" returns 422 on self-serve keys
            if r.status_code == 429 and attempt == 0:
                time.sleep(float(r.headers.get("Retry-After", "1")))
                continue
            r.raise_for_status()
            break
        body = r.json()
        hits = body.get("results", {}).get("web", []) if isinstance(body.get("results"), dict) else body.get("results", body.get("hits", []))
        out = []
        for h in hits[:count]:
            snippet = " ".join(h.get("highlights") or h.get("snippets") or [h.get("description", "")])
            if not snippet.strip():
                continue
            out.append({"title": h.get("title", ""), "url": h.get("url", ""), "snippet": snippet[:400]})
        return out

    # --- Parasail -------------------------------------------------------
    def llm(self) -> Any:
        return self._llm

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls["parasail.embed"] += 1
        res = self._openai.embeddings.create(model=PARASAIL_EMBED_MODEL, input=texts)
        return [d.embedding for d in res.data]

    # --- One ------------------------------------------------------------
    def _dispatch_via_one(self, secret: str, content: str) -> Any:
        """list → find → execute, per One's three-tool loop. Action ids are resolved live, never hardcoded."""
        if self._one_action is None:
            listed, found = asyncio.run(_one_calls(secret, [
                ("list_one_integrations", {}),
                ("find_one_actions", {"task": "post a containment alert to the venue incident channel",
                                      "ai_model": PARASAIL_CHAT_MODEL,
                                      "requests": [{"platform": "discord", "intent": "send a message to a channel"}]}),
            ]))
            conns = [c for c in listed.get("connections", []) if c.get("platform") == "discord"]
            if not conns:
                have = sorted({c.get("platform") for c in listed.get("connections", [])})
                raise PartnerError(f"Discord is not connected on this One account (connected: {have}). "
                                   "Add it at https://app.withone.ai or `one add discord`.")
            results = found.get("results") or found.get("actions") or []
            chosen = (results[0].get("action") or results[0]) if results else {}
            action_id = chosen.get("actionId") or chosen.get("action_id")
            if not action_id:
                raise PartnerError(f"find_one_actions returned no Discord action: {json.dumps(found)[:300]}")
            self._one_action = {"platform": "discord", "actionId": action_id, "connectionKey": conns[0]["key"],
                                "path": chosen.get("path", "")}
        a = self._one_action
        (res,) = asyncio.run(_one_calls(secret, [("execute_one_action", {
            "platform": a["platform"], "actionId": a["actionId"], "connectionKey": a["connectionKey"],
            "pathVariables": {"channelId": self.channel_id}, "data": {"content": content}})]))
        return res

    def dispatch(self, content: str) -> dict[str, Any]:
        if content == self._last_dispatch:
            return {"ok": True, "method": "dedup", "detail": "identical escalation already posted"}
        self.calls["one.dispatch"] += 1
        one_key = os.environ.get("ONE_SECRET") or os.environ.get("ONE_API_KEY")
        if self._one_broken:
            err = f"One skipped (failed earlier this run: {self._one_broken})"
        elif one_key and os.environ.get("ONE_DISPATCH", "1") != "0":
            try:
                res = self._dispatch_via_one(one_key, content)
                self._last_dispatch = content
                return {"ok": True, "method": "one", "detail": res}
            except Exception as e:
                # Organizers (Oct 28): One's Discord send 401s for connected accounts; make ONE attempt, then use
                # the venue webhook for the rest of the run. Never retry a write blindly.
                self._one_broken = str(e)[:200]
                err = f"One failed: {e}"
        else:
            err = "One dispatch disabled or ONE_API_KEY not set"
        hook = os.environ.get("DISCORD_WEBHOOK_URL")
        if hook:
            r = self._http.post(hook, json={"content": content})
            ok = r.status_code < 300
            if ok:
                self._last_dispatch = content
            return {"ok": ok, "method": "webhook-fallback",
                    "detail": f"{err}; venue webhook status {r.status_code}" + ("" if ok else f" {r.text[:200]}")}
        return {"ok": False, "method": "manual",
                "detail": f"{err}. Post manually to #loft-incident ({self.channel_id}) and tell the organizers' table."}

