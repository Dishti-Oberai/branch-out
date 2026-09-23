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
        self.response_fn = response_fn or (
            lambda i: json.dumps({"code": f"print({i})", "reasoning": f"approach {i}"})
        )

    async def complete(self, messages, system=""):
        i = self.calls
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if i in self.fail_on:
            raise RuntimeError(f"simulated failure on call {i}")
        return self.response_fn(i)


@pytest.fixture
def fake_llm():
    return FakeLLM()
