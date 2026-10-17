import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from llm_client import LLMClient  # noqa: E402


class FakeLLM(LLMClient):
    """Deterministic stand-in for ClaudeClient. No network, no API key."""

    def __init__(self, response_fn=None, delay: float = 0.0, fail_on=()):
        self.calls = 0
        self.delay = delay
        self.fail_on = set(fail_on)
        # is_code_change=True by default (2026-10-17 amendment): most
        # existing tests are exercising code-generation flows, not
        # specifically testing the auto/question/code_change classification
        # itself (see test_branch_generator.py and the orchestrator tests
        # for those) -- defaulting True here keeps target_file threading
        # through under the default "auto" intent without every pre-
        # existing test needing to pass intent="code_change" explicitly.
        self.response_fn = response_fn or (
            lambda i: json.dumps({"code": f"print({i})", "reasoning": f"approach {i}", "is_code_change": True})
        )
        # Records of every call, for tests that need to assert on what was
        # actually sent (e.g. whether codebase_context made it into the
        # prompt) rather than just how many times complete() was called.
        self.received: list = []

    async def complete(self, messages, system=""):
        i = self.calls
        self.calls += 1
        self.received.append({"messages": messages, "system": system})
        if self.delay:
            await asyncio.sleep(self.delay)
        if i in self.fail_on:
            raise RuntimeError(f"simulated failure on call {i}")
        return self.response_fn(i)


@pytest.fixture
def fake_llm():
    return FakeLLM()
