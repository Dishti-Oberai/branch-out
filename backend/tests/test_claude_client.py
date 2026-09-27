"""Caching fix (2026-09-27): ClaudeClient now records a `cache_usage_history`
entry after every call, feeding Tokenetics' cache_breakpoint_optimizer stage
(which otherwise always bails on "insufficient_usage_history" -- see
ROADMAP.md's Phase 1 caching note). These tests mock out both the real
Anthropic SDK and Tokenetics' prepare()/finalize() (which itself makes real,
blocking network calls for token counting) so this exercises *only* the new
recording logic, no API key or network required -- consistent with CLAUDE.md's
"the test suite must not require this key" requirement.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tokenetics.core.cache_hash import stable_prefix_hash

with patch("anthropic.AsyncAnthropic"):
    from claude_client import ClaudeClient


def _make_client():
    with patch("anthropic.AsyncAnthropic"):
        client = ClaudeClient()
    client.client.messages.create = AsyncMock(return_value=MagicMock())
    client._tk.prepare = MagicMock(return_value={"messages": [], "model": "x", "max_tokens": 1})
    client._tk.finalize = MagicMock(return_value="ok")
    return client


def test_history_starts_empty():
    client = _make_client()
    assert client._cache_usage_history == []


def test_a_call_appends_one_history_entry_with_the_right_hash():
    client = _make_client()
    asyncio.run(client.complete(messages=[{"role": "user", "content": "hi"}], system="SOME_CODEBASE_SNAPSHOT"))
    assert len(client._cache_usage_history) == 1
    entry = client._cache_usage_history[0]
    assert "timestamp" in entry
    assert entry["content_hash"] == stable_prefix_hash("SOME_CODEBASE_SNAPSHOT", [])


def test_repeated_identical_system_produces_matching_hashes():
    # This is exactly what cache_breakpoint_optimizer needs to see >=2 of
    # before it will place a breakpoint -- two entries with the SAME hash.
    client = _make_client()
    asyncio.run(client.complete(messages=[{"role": "user", "content": "a"}], system="SAME_CONTEXT"))
    asyncio.run(client.complete(messages=[{"role": "user", "content": "b"}], system="SAME_CONTEXT"))
    hashes = {e["content_hash"] for e in client._cache_usage_history}
    assert len(client._cache_usage_history) == 2
    assert len(hashes) == 1


def test_empty_system_hashes_as_none_not_empty_string():
    # complete()'s default system="" and an explicit None must hash
    # identically -- both mean "no codebase context this call" and should
    # match each other in cache_usage_history, not be treated as two
    # different stable prefixes.
    client = _make_client()
    asyncio.run(client.complete(messages=[{"role": "user", "content": "a"}], system=""))
    asyncio.run(client.complete(messages=[{"role": "user", "content": "b"}], system=None))
    hashes = {e["content_hash"] for e in client._cache_usage_history}
    assert hashes == {stable_prefix_hash(None, [])}


def test_stage_config_holds_the_same_list_object_so_it_grows_live():
    # Tokenetics reads stage_config fresh off the dict on every prepare()
    # call -- this only works if we handed it the SAME list object to
    # mutate, not a copy. Regression guard for exactly that.
    client = _make_client()
    configured_history = client._tk._stage_config["cache_breakpoint_optimizer"]["cache_usage_history"]
    assert configured_history is client._cache_usage_history
    asyncio.run(client.complete(messages=[{"role": "user", "content": "a"}], system="X"))
    assert len(configured_history) == 1
