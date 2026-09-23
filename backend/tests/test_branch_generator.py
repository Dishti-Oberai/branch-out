import asyncio
import json
import time

from branch_generator import branch_label, generate_branches
from tests.conftest import FakeLLM


def test_generate_branches_returns_one_per_beam_slot():
    llm = FakeLLM()
    branches = asyncio.run(generate_branches("task", "", [], llm, beam_width=3))
    assert len(branches) == 3
    assert all(b.code for b in branches)


def test_one_failed_call_still_returns_the_rest():
    llm = FakeLLM(fail_on={1})
    branches = asyncio.run(generate_branches("task", "", [], llm, beam_width=3))
    assert len(branches) == 2


def test_calls_are_concurrent_not_sequential():
    llm = FakeLLM(delay=0.2)
    start = time.time()
    asyncio.run(generate_branches("task", "", [], llm, beam_width=3))
    elapsed = time.time() - start
    assert elapsed < 0.2 * 3, "branch calls ran sequentially, not concurrently"


def test_malformed_json_keeps_raw_output_instead_of_failing():
    llm = FakeLLM(response_fn=lambda i: "not json")
    branches = asyncio.run(generate_branches("task", "", [], llm, beam_width=1))
    assert branches[0].code == "not json"


def test_branch_label_sequence():
    assert [branch_label(i) for i in range(3)] == ["A", "B", "C"]
    assert branch_label(26) == "AA"
