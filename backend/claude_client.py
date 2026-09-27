import asyncio
import time

import anthropic
from tokenetics import Tokenetics
from tokenetics.core.cache_hash import stable_prefix_hash

from llm_client import LLMClient


class ClaudeClient(LLMClient):
    def __init__(self):
        # Async SDK client for the actual completion call: the sync client
        # would block the event loop and serialise the asyncio.gather calls
        # in branch_generator.
        self.client = anthropic.AsyncAnthropic()
        self.model = "claude-sonnet-4-6"

        # Caching fix (2026-09-27): Tokenetics' cache_breakpoint_optimizer
        # stage (one of the 11 below) won't place a cache_control marker
        # without a caller-supplied history of prior calls' stable-prefix
        # hashes — it refuses to guess without >=2 matching prior calls
        # (conservative by design; see the stage's own docstring). Before
        # this, no history was ever passed, so it always bailed on
        # "insufficient_usage_history" — this is what actually turns
        # caching on. Held as a plain list, mutated in place (appended to,
        # never replaced) so Tokenetics' stage_config — read fresh off this
        # same dict on every prepare() call — sees it grow across the
        # client's whole lifetime, not reset per call.
        self._cache_usage_history: list[dict] = []
        self._tk = Tokenetics(
            stage_config={
                "cache_breakpoint_optimizer": {"cache_usage_history": self._cache_usage_history},
            }
        )

        # Tokenetics drives context compaction: dedup, near-dup removal,
        # task-aware trimming, knapsack-based context scheduling, cache
        # optimisation and post-hoc reply cleanup — all 11 stages, via its
        # frozen Tier 0 API (prepare()/finalize()). This is what keeps
        # context_summary from growing the token cost of every branch call
        # without bound as the tree gets deep. Tokenetics manages its own
        # internal *sync* anthropic.Anthropic client for token-counting
        # stages (type-hinted `anthropic.Anthropic`, not `AsyncAnthropic`),
        # kept separate from `self.client` above.

    async def complete(self, messages, system=""):
        # prepare()/finalize() are synchronous and make their own blocking
        # HTTP calls (token counting) via Tokenetics' internal client.
        # Running them via asyncio.to_thread keeps them off the event loop
        # so they don't serialise the concurrent branch generation calls
        # this client is used from.
        request = await asyncio.to_thread(
            self._tk.prepare,
            messages=messages,
            model=self.model,
            max_tokens=4096,
            system=system,
        )
        response = await self.client.messages.create(**request)

        # Record *after* the request is built so this reflects exactly what
        # was sent — feeds the *next* call's cache_breakpoint_optimizer
        # decision, not this one's. No tools are ever passed, so the tools
        # side of the hash is always []; hashed on the raw `system` string
        # (pre-pipeline), matching what the stage itself hashes internally
        # (none of the 11 stages rewrite `system`, only `messages`/`tools`).
        self._cache_usage_history.append({
            "timestamp": time.time(),
            "content_hash": stable_prefix_hash(system if system else None, []),
        })

        return await asyncio.to_thread(self._tk.finalize, response)
