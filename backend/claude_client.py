import asyncio

import anthropic
from tokenetics import Tokenetics

from llm_client import LLMClient


class ClaudeClient(LLMClient):
    def __init__(self):
        # Async SDK client for the actual completion call: the sync client
        # would block the event loop and serialise the asyncio.gather calls
        # in branch_generator.
        self.client = anthropic.AsyncAnthropic()
        self.model = "claude-sonnet-4-6"

        # Tokenetics drives context compaction: dedup, near-dup removal,
        # task-aware trimming, knapsack-based context scheduling, cache
        # optimisation and post-hoc reply cleanup — all 11 stages, via its
        # frozen Tier 0 API (prepare()/finalize()). This is what keeps
        # context_summary from growing the token cost of every branch call
        # without bound as the tree gets deep. Tokenetics manages its own
        # internal *sync* anthropic.Anthropic client for token-counting
        # stages (type-hinted `anthropic.Anthropic`, not `AsyncAnthropic`),
        # kept separate from `self.client` above.
        self._tk = Tokenetics()

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
        return await asyncio.to_thread(self._tk.finalize, response)
