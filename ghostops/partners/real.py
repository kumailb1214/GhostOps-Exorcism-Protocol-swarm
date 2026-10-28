"""Real partner clients. Every call is counted so the decision record can prove partner usage.

Env (see .env.example): YDC_API_KEY, PARASAIL_API_KEY, ONE_API_KEY, ONE_MCP_URL, SCENARIO_MCP,
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
from mcp import ClientSession
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


class RealPartners(PartnerClient):
    def __init__(self) -> None:
        super().__init__()
        self.scenario_url = _require("SCENARIO_MCP")
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

    # --- scenario feed -------------------------------------------------
    def get_tick(self) -> Tick:
        self.calls["scenario.get_tick"] += 1
        data = mcp_call(self.scenario_url, "hack26-get-tick", {})
        return Tick.model_validate(data.get("tick", data))

    def submit(self, team: str, tick_id: int, action: str) -> Judgment:
        self.calls["scenario.submit"] += 1
        data = mcp_call(self.scenario_url, "hack26-submit-containment",
                        {"team": team, "tick_id": tick_id, "action": action})
        return Judgment(expected_action=data.get("expected_action") or data.get("expected"),
                        score=data.get("score"), false_alarm=data.get("false_alarm"), raw=data)

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
    def dispatch(self, content: str) -> dict[str, Any]:
        if content == self._last_dispatch:
            return {"ok": True, "method": "dedup", "detail": "identical escalation already posted"}
        self.calls["one.dispatch"] += 1
        one_url, one_key = os.environ.get("ONE_MCP_URL"), os.environ.get("ONE_API_KEY")
        if one_url and one_key:
            headers = {"Authorization": f"Bearer {one_key}"}
            try:
                found = mcp_call(one_url, "find_one_actions", {
                    "task": "post a containment alert to the venue channel",
                    "requests": [{"platform": "discord", "intent": "send a message to a channel"}]}, headers)
                cand = (found.get("actions") or found.get("results") or [found])[0]
                res = mcp_call(one_url, "execute_one_action", {
                    "action_id": cand["action_id"], "connection_key": cand["connection_key"],
                    "path_variables": {"channelId": self.channel_id}, "data": {"content": content}}, headers)
                self._last_dispatch = content
                return {"ok": True, "method": "one", "detail": res}
            except Exception as e:  # one careful retry is inside mcp transport; now fall back loudly
                err = f"One failed: {e}"
        else:
            err = "ONE_MCP_URL/ONE_API_KEY not set"
        hook = os.environ.get("DISCORD_WEBHOOK_URL")
        if hook:
            r = self._http.post(hook, json={"content": content})
            ok = r.status_code < 300
            if ok:
                self._last_dispatch = content
            return {"ok": ok, "method": "webhook-fallback", "detail": f"{err}; webhook status {r.status_code}"}
        return {"ok": False, "method": "manual",
                "detail": f"{err}. Post manually to #loft-incident ({self.channel_id}) and tell the organizers' table."}

