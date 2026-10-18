"""Phase 6.2: a real FastAPI server (not a TestClient) for Playwright E2E
tests to talk to, but backed by a deterministic FakeLLM instead of a real
ClaudeClient -- the same swap `tests/test_api.py` does, just exposed over a
real HTTP+WebSocket port so a real browser can hit it. No API key needed,
no real API cost, and actually exercises the WebSocket (branch.ready/
score.ready) path a route-mocked fetch() stub never would.

Run directly: `python3 tests/e2e_server.py` (from backend/), or via
Playwright's own `webServer` config, which is how this is actually used.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402

import main  # noqa: E402
from llm_client import LLMClient  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402


class _E2EFakeLLM(LLMClient):
    """Unlike `tests/conftest.py`'s FakeLLM (shared across the unit suite,
    where each test customizes `response_fn` for the ONE call shape it's
    actually exercising), this one real server sees every call shape the
    app makes in one session -- branch/continue generation AND, when a
    branch has no tests, `score_normaliser.rank_branches`'s ranking call,
    which expects a JSON ARRAY, not the `{"code": ...}` dict shape. A
    single canned response for both produced a real-looking "Score
    normalisation failed: string indices must be integers" error on every
    E2E run (caught here, not a real app bug) -- this switches on the
    system prompt's own content instead of guessing from call order.
    """
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, system=""):
        self.calls += 1
        system = (system or "").lower()  # branch_generator passes system=None, not "", when unset
        if "ranking" in system and "candidate" in system:
            n = len(messages[0]["content"].split("--- Candidate"))  - 1
            return json.dumps([
                {"branch_index": i, "rank": i + 1, "score": round(1.0 - i * 0.1, 2), "reason": "ok"}
                for i in range(n)
            ])
        return json.dumps({
            "code": "def f(): pass",
            "reasoning": f"a synthesized approach {self.calls}",
            "is_code_change": True,
        })


main._orchestrator = Orchestrator(_E2EFakeLLM())

if __name__ == "__main__":
    # Deliberately NOT :8000 -- a manual testing session may have the real
    # backend running there; this must never collide with or disrupt it.
    uvicorn.run(main.app, host="127.0.0.1", port=8010, log_level="warning")
